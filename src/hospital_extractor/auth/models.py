"""Typed auth, RBAC, and audit models (P24)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "AuditEvent",
    "AuthMode",
    "AuthSettings",
    "LoginRequest",
    "LoginResponse",
    "Permission",
    "Principal",
    "Role",
    "TokenRecord",
    "User",
    "UserCreate",
    "UserPublic",
    "UserRoleUpdate",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuthMode(StrEnum):
    """Authentication mode. Development auth is never production auth."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


class Role(StrEnum):
    """Default role vocabulary (P24)."""

    ADMIN = "admin"
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    VIEWER = "viewer"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)


class Permission(StrEnum):
    """Granular permissions mapped to real MedCompare operations."""

    VIEW_HOSPITAL = "view_hospital"
    CAPTURE_SOURCE = "capture_source"
    MANAGE_MONITORING = "manage_monitoring"
    MANAGE_SOURCES = "manage_sources"
    VERIFY_CHANGE = "verify_change"
    REVIEW_RECONCILIATION = "review_reconciliation"
    GENERATE_REPORT = "generate_report"
    SUBMIT_REPORT = "submit_report"
    MANAGE_USERS = "manage_users"
    MANAGE_CONFIGURATION = "manage_configuration"
    VIEW_QUALITY = "view_quality"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)


class AuthSettings(BaseModel):
    """Runtime authentication settings (env + overrides)."""

    model_config = ConfigDict(frozen=True)

    require_auth: bool = False
    mode: AuthMode = AuthMode.DEVELOPMENT
    token_ttl_seconds: int = Field(default=12 * 3600, ge=60, le=30 * 24 * 3600)
    allow_dev_header: bool = False
    login_rate_limit: int = Field(default=10, ge=1, le=1000)
    login_rate_window_seconds: int = Field(default=60, ge=1, le=3600)
    trusted_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    cors_origins: tuple[str, ...] = ()


class User(BaseModel):
    """Persistent user row. ``password_hash`` never leaves the store."""

    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: str
    username: str = Field(min_length=1, max_length=64)
    password_hash: str = ""
    role: Role = Role.VIEWER
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    hospital_ids: list[str] = Field(default_factory=list)


class UserPublic(BaseModel):
    """Safe user projection for API/UI (never includes password_hash)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: str
    username: str
    role: Role
    enabled: bool
    created_at: datetime
    updated_at: datetime
    hospital_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_user(cls, user: User) -> UserPublic:
        return cls(
            user_id=user.user_id,
            username=user.username,
            role=user.role,
            enabled=user.enabled,
            created_at=user.created_at,
            updated_at=user.updated_at,
            hospital_ids=list(user.hospital_ids),
        )


class UserCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: Role = Role.VIEWER
    enabled: bool = True
    hospital_ids: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("password")
    @classmethod
    def _password_not_username(cls, value: str, info: Any) -> str:
        username = info.data.get("username")
        if username and value == username:
            raise ValueError("password must not equal username")
        return value


class UserRoleUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    role: Role | None = None
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)
    hospital_ids: list[str] | None = Field(default=None, max_length=500)


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    """Login result. ``token`` is shown once and never stored in plaintext."""

    model_config = ConfigDict(str_strip_whitespace=True)

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserPublic
    mode: AuthMode
    development: bool = False


class TokenRecord(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    token_id: str
    user_id: str
    token_hash: str
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime
    revoked_at: datetime | None = None


class Principal(BaseModel):
    """Authenticated request principal (P24 Layer 1)."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    username: str
    role: Role
    permissions: tuple[Permission, ...] = ()
    auth_status: str = "authenticated"  # authenticated | development_bypass | dev_header
    token_id: str | None = None
    development: bool = False
    hospital_ids: tuple[str, ...] = ()  # empty + admin → all; empty + non-admin → none

    def has_permission(self, permission: Permission) -> bool:
        return permission in self.permissions

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def actor_id(self) -> str:
        return self.user_id

    @property
    def actor_label(self) -> str:
        return self.username


class AuditEvent(BaseModel):
    """Append-oriented security audit row. Never contains secrets."""

    model_config = ConfigDict(str_strip_whitespace=True)

    audit_event_id: str
    timestamp: datetime = Field(default_factory=_utc_now)
    actor_id: str = ""
    actor_role: str = ""
    action: str
    resource_type: str = ""
    resource_id: str = ""
    hospital_id: str = ""
    outcome: str = "success"  # success | failure | denied
    correlation_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
