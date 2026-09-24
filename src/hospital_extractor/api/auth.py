"""Auth, user management, and audit API routes (P24).

- ``POST /api/v1/auth/login`` — username/password → bearer token (once)
- ``POST /api/v1/auth/logout`` — revoke current bearer token
- ``GET  /api/v1/auth/me`` — current principal + permissions
- ``GET/POST /api/v1/users`` — ADMIN list/create users
- ``PATCH /api/v1/users/{user_id}``` — ADMIN role/enable/password/grants
- ``GET  /api/v1/audit`` — ADMIN/security audit list (sanitized)
- ``GET  /api/v1/audit/{audit_event_id}`` — single event

Never returns password hashes, raw tokens, or stack traces.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from hospital_extractor.auth.audit import AuditOutcome, record_audit
from hospital_extractor.auth.dependencies import (
    PrincipalDep,
    get_auth_service_from_app,
    get_request_correlation,
    require_permission,
)
from hospital_extractor.auth.models import (
    LoginRequest,
    LoginResponse,
    Permission,
    Principal,
    UserCreate,
    UserPublic,
    UserRoleUpdate,
)
from hospital_extractor.auth.service import AuthService, AuthServiceError

__all__ = ["auth_router"]

auth_router = APIRouter(prefix="/api/v1", tags=["auth"])


def _client_key(request: Request) -> str:
    if request.client:
        return request.client.host
    return "anonymous"


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer":
        return token.strip()
    return ""


@auth_router.post(
    "/auth/login",
    response_model=LoginResponse,
    summary="Exchange username/password for a bearer token",
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service_from_app)],
) -> LoginResponse:
    correlation = get_request_correlation(request)
    try:
        result = service.login(
            payload.username,
            payload.password,
            client_key=_client_key(request),
        )
    except AuthServiceError as exc:
        with service.open_store() as store:
            record_audit(
                store,
                action="auth.login",
                outcome=AuditOutcome.FAILURE,
                actor_id=payload.username,
                actor_role="",
                resource_type="user",
                resource_id=payload.username,
                correlation_id=correlation,
                metadata={"status_code": exc.status_code},
            )
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None

    with service.open_store() as store:
        record_audit(
            store,
            action="auth.login",
            outcome=AuditOutcome.SUCCESS,
            principal=Principal(
                user_id=result.user.user_id,
                username=result.user.username,
                role=result.user.role,
            ),
            resource_type="user",
            resource_id=result.user.user_id,
            correlation_id=correlation,
            metadata={"mode": result.mode.value},
        )
    # Opaque bearer: client stores it; server keeps only a SHA-256 digest.
    response.headers["Cache-Control"] = "no-store"
    return result


@auth_router.post("/auth/logout", status_code=204, summary="Revoke current token")
def logout(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service_from_app)],
    principal: PrincipalDep,
) -> Response:
    correlation = get_request_correlation(request)
    token = _bearer_token(request)
    revoked = service.logout(token) if token else False
    with service.open_store() as store:
        record_audit(
            store,
            action="auth.logout",
            outcome=AuditOutcome.SUCCESS,
            principal=principal,
            resource_type="user",
            resource_id=principal.user_id,
            correlation_id=correlation,
            metadata={"revoked": revoked},
        )
    return Response(status_code=204)


@auth_router.get("/auth/me", summary="Current principal and permissions")
def me(
    request: Request,
    principal: PrincipalDep,
) -> dict[str, object]:
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role.value,
        "permissions": [p.value for p in principal.permissions],
        "auth_status": principal.auth_status,
        "development": principal.development,
        "hospital_ids": list(principal.hospital_ids),
        "correlation_id": get_request_correlation(request),
    }


@auth_router.get(
    "/users",
    response_model=list[UserPublic],
    summary="List users (ADMIN)",
)
def list_users_route(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service_from_app)],
    _: Annotated[Principal, Depends(require_permission(Permission.MANAGE_USERS))],
) -> list[UserPublic]:
    return service.list_users()


@auth_router.post(
    "/users",
    response_model=UserPublic,
    status_code=201,
    summary="Create user (ADMIN)",
)
def create_user_route(
    payload: UserCreate,
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service_from_app)],
    principal: PrincipalDep,
    _: Annotated[Principal, Depends(require_permission(Permission.MANAGE_USERS))],
) -> UserPublic:
    correlation = get_request_correlation(request)
    try:
        user = service.create_user(payload)
    except AuthServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    with service.open_store() as store:
        record_audit(
            store,
            action="user.create",
            principal=principal,
            resource_type="user",
            resource_id=user.user_id,
            correlation_id=correlation,
            metadata={"username": user.username, "role": user.role.value},
        )
    return user


@auth_router.patch(
    "/users/{user_id}",
    response_model=UserPublic,
    summary="Update user role/status/password/grants (ADMIN)",
)
def update_user_route(
    user_id: str,
    payload: UserRoleUpdate,
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service_from_app)],
    principal: PrincipalDep,
    _: Annotated[Principal, Depends(require_permission(Permission.MANAGE_USERS))],
) -> UserPublic:
    correlation = get_request_correlation(request)
    user = service.update_user(user_id, payload)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    changed = [
        field
        for field in ("role", "enabled", "password", "hospital_ids")
        if getattr(payload, field, None) is not None
    ]
    with service.open_store() as store:
        record_audit(
            store,
            action="user.update",
            principal=principal,
            resource_type="user",
            resource_id=user_id,
            correlation_id=correlation,
            metadata={"changed": changed, "role": user.role.value, "enabled": user.enabled},
        )
    return user


@auth_router.get("/audit", summary="List audit events (ADMIN/security)")
def list_audit_route(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service_from_app)],
    principal: PrincipalDep,
    action: str | None = None,
    hospital_id: str | None = None,
    limit: int = 100,
) -> dict[str, object]:
    # VIEWER/OPERATOR/REVIEWER cannot enumerate the audit log.
    if not principal.has_permission(Permission.MANAGE_USERS) and not principal.is_admin:
        raise HTTPException(status_code=403, detail="Permission denied")
    limit = max(1, min(limit, 500))
    with service.open_store() as store:
        events = store.list_audit(action=action, hospital_id=hospital_id, limit=limit)
    return {
        "events": [e.model_dump(mode="json") for e in events],
        "count": len(events),
        "correlation_id": get_request_correlation(request),
    }


@auth_router.get("/audit/{audit_event_id}", summary="Single audit event")
def get_audit_route(
    audit_event_id: str,
    service: Annotated[AuthService, Depends(get_auth_service_from_app)],
    principal: PrincipalDep,
) -> dict[str, object]:
    if not principal.has_permission(Permission.MANAGE_USERS) and not principal.is_admin:
        raise HTTPException(status_code=403, detail="Permission denied")
    with service.open_store() as store:
        event = store.get_audit(audit_event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Audit event not found")
    return event.model_dump(mode="json")
