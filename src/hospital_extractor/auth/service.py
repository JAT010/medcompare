"""Auth service: login, tokens, principals, user management (P24)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hospital_extractor.auth.hashing import hash_token, new_token, verify_password
from hospital_extractor.auth.models import (
    AuthMode,
    AuthSettings,
    LoginResponse,
    Principal,
    Role,
    User,
    UserCreate,
    UserPublic,
    UserRoleUpdate,
)
from hospital_extractor.auth.permissions import PERMISSIONS_BY_ROLE
from hospital_extractor.auth.settings import auth_settings
from hospital_extractor.auth.store import AuthStore

__all__ = [
    "AuthService",
    "AuthServiceError",
    "authenticate",
    "create_user",
    "get_auth_service",
    "list_users",
]


class AuthServiceError(Exception):
    """Safe auth failure (message is client-safe; no secrets)."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _LoginRateLimiter:
    """Simple in-memory sliding-window limiter for login attempts."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            window = self._hits[key]
            while window and now - window[0] > self._window:
                window.popleft()
            if len(window) >= self._limit:
                return False
            window.append(now)
            return True


class AuthService:
    """Coordinates store access, settings, and principal construction."""

    def __init__(
        self,
        db_path: Path | str,
        settings: AuthSettings | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.settings = settings or auth_settings()
        self._rate = _LoginRateLimiter(
            self.settings.login_rate_limit,
            self.settings.login_rate_window_seconds,
        )

    def open_store(self) -> AuthStore:
        return AuthStore(self.db_path)

    # ------------------------------------------------------------------ login

    def login(self, username: str, password: str, *, client_key: str = "") -> LoginResponse:
        rate_key = client_key or username
        if not self._rate.allow(rate_key):
            raise AuthServiceError(
                "Too many login attempts; try again later", status_code=429
            )

        with self.open_store() as store:
            user = store.get_user_by_username(username)
            encoded = user.password_hash if user is not None else ""
            password_ok = verify_password(password, encoded) if encoded else False
            if user is None or not password_ok:
                # Identical failure path for unknown user vs bad password.
                raise AuthServiceError("Invalid username or password", status_code=401)
            if not user.enabled:
                raise AuthServiceError("Account is disabled", status_code=403)

            raw_token = new_token()
            expires = datetime.now(UTC) + timedelta(seconds=self.settings.token_ttl_seconds)
            store.save_token(user.user_id, hash_token(raw_token), expires)
            return LoginResponse(
                access_token=raw_token,
                expires_in=self.settings.token_ttl_seconds,
                user=UserPublic.from_user(user),
                mode=self.settings.mode,
                development=self.settings.mode is AuthMode.DEVELOPMENT,
            )

    def logout(self, raw_token: str) -> bool:
        if not raw_token:
            return False
        with self.open_store() as store:
            return store.revoke_token(hash_token(raw_token))

    def principal_for_token(self, raw_token: str) -> Principal | None:
        if not raw_token:
            return None
        with self.open_store() as store:
            record = store.get_token_by_hash(hash_token(raw_token))
            if record is None or record.revoked_at is not None:
                return None
            if record.expires_at <= datetime.now(UTC):
                return None
            user = store.get_user(record.user_id)
            if user is None or not user.enabled:
                return None
            principal = self.principal_for_user(user)
            return principal.model_copy(
                update={"token_id": record.token_id, "auth_status": "authenticated"}
            )

    def principal_for_dev_header(self, username: str) -> Principal | None:
        """DEVELOPMENT/TEST ONLY: resolve a principal from a username header."""
        if not self.settings.allow_dev_header:
            return None
        if self.settings.mode is AuthMode.PRODUCTION:
            return None
        with self.open_store() as store:
            user = store.get_user_by_username(username)
            if user is None or not user.enabled:
                return None
            principal = self.principal_for_user(user)
            return principal.model_copy(
                update={"auth_status": "dev_header", "development": True}
            )

    def principal_for_user(self, user: User) -> Principal:
        perms = PERMISSIONS_BY_ROLE.get(user.role, frozenset())
        return Principal(
            user_id=user.user_id,
            username=user.username,
            role=user.role,
            permissions=tuple(sorted(perms, key=lambda p: p.value)),
            auth_status="authenticated",
            development=self.settings.mode is AuthMode.DEVELOPMENT,
            hospital_ids=tuple(user.hospital_ids),
        )

    def development_bypass_principal(self) -> Principal:
        """Synthetic ADMIN principal when require_auth is off (local/dev)."""
        perms = PERMISSIONS_BY_ROLE[Role.ADMIN]
        return Principal(
            user_id="u_dev_bypass",
            username="development-bypass",
            role=Role.ADMIN,
            permissions=tuple(sorted(perms, key=lambda p: p.value)),
            auth_status="development_bypass",
            development=True,
        )

    # ------------------------------------------------------------------ users

    def create_user(self, payload: UserCreate) -> UserPublic:
        with self.open_store() as store:
            if store.get_user_by_username(payload.username) is not None:
                raise AuthServiceError("Username already exists", status_code=409)
            user = store.create_user(
                username=payload.username,
                password=payload.password,
                role=payload.role,
                enabled=payload.enabled,
                hospital_ids=list(payload.hospital_ids),
            )
            return UserPublic.from_user(user)

    def list_users(self) -> list[UserPublic]:
        with self.open_store() as store:
            return [UserPublic.from_user(u) for u in store.list_users()]

    def update_user(self, user_id: str, payload: UserRoleUpdate) -> UserPublic | None:
        with self.open_store() as store:
            user = store.update_user(
                user_id,
                role=payload.role,
                enabled=payload.enabled,
                password=payload.password,
                hospital_ids=payload.hospital_ids,
            )
            if user is None:
                return None
            if payload.enabled is False or payload.password is not None:
                store.revoke_user_tokens(user_id)
            return UserPublic.from_user(user)

    def can_access_hospital(self, principal: Principal, hospital_id: str) -> bool:
        """ADMIN: all hospitals. Others: explicit grant only (fail closed)."""
        if not hospital_id:
            return False
        if principal.is_admin:
            return True
        if hospital_id in principal.hospital_ids:
            return True
        with self.open_store() as store:
            return store.has_hospital_grant(principal.user_id, hospital_id)


_service_lock = threading.Lock()
_services: dict[str, AuthService] = {}


def get_auth_service(
    db_path: Path | str,
    settings: AuthSettings | None = None,
) -> AuthService:
    """Process-level service cache keyed by db path + require_auth + mode."""
    path = str(Path(db_path))
    effective = settings or auth_settings()
    key = (
        f"{path}|{effective.require_auth}|{effective.mode.value}"
        f"|{effective.allow_dev_header}"
    )
    with _service_lock:
        service = _services.get(key)
        if service is None:
            service = AuthService(path, settings=effective)
            _services[key] = service
        return service


def authenticate(
    service: AuthService,
    *,
    authorization: str | None,
    dev_user: str | None,
    settings: AuthSettings,
) -> Principal:
    """Resolve a principal from request credentials (raises AuthServiceError)."""
    if settings.require_auth:
        if authorization:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() == "bearer" and token:
                principal = service.principal_for_token(token.strip())
                if principal is not None:
                    return principal
            raise AuthServiceError("Invalid or expired credentials", status_code=401)
        if dev_user and settings.allow_dev_header and settings.mode is AuthMode.DEVELOPMENT:
            principal = service.principal_for_dev_header(dev_user)
            if principal is not None:
                return principal
            raise AuthServiceError("Invalid development user", status_code=401)
        raise AuthServiceError("Authentication required", status_code=401)
    return service.development_bypass_principal()


def create_user(service: AuthService, payload: UserCreate) -> UserPublic:
    return service.create_user(payload)


def list_users(service: AuthService) -> list[UserPublic]:
    return service.list_users()
