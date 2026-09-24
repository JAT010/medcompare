"""FastAPI dependencies for P24 authz (401/403) and hospital isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from hospital_extractor.auth.models import AuthSettings, Permission, Principal
from hospital_extractor.auth.service import (
    AuthService,
    AuthServiceError,
    authenticate,
    get_auth_service,
)
from hospital_extractor.auth.settings import auth_settings

__all__ = [
    "AuthDep",
    "PrincipalDep",
    "current_principal",
    "get_request_correlation",
    "optional_principal",
    "require_any_permission",
    "require_hospital_access",
    "require_permission",
]


def get_auth_service_from_app(request: Request) -> AuthService:
    service = getattr(request.app.state, "medcompare_auth_service", None)
    if service is None:
        settings = getattr(request.app.state, "medcompare_auth_settings", None)
        db_path = getattr(request.app.state, "medcompare_db_path", None)
        if db_path is None:
            from hospital_extractor.storage import default_database_path

            db_path = default_database_path()
            request.app.state.medcompare_db_path = db_path
        service = get_auth_service(db_path, settings=settings)
        request.app.state.medcompare_auth_service = service
    return service


def get_auth_settings_from_app(request: Request) -> AuthSettings:
    settings = getattr(request.app.state, "medcompare_auth_settings", None)
    if settings is None:
        settings = auth_settings()
        request.app.state.medcompare_auth_settings = settings
    return settings


def get_request_correlation(request: Request) -> str:
    """Request/run correlation id set by app middleware (never a secret)."""
    value = getattr(request.state, "request_id", None)
    if isinstance(value, str) and value:
        return value
    return ""


AuthDep = Annotated[AuthService, Depends(get_auth_service_from_app)]


def current_principal(request: Request) -> Principal:
    """Layer 1 authn + authz context. Raises 401 when authentication fails."""
    existing = getattr(request.state, "principal", None)
    if isinstance(existing, Principal):
        return existing
    service = get_auth_service_from_app(request)
    settings = get_auth_settings_from_app(request)
    authorization = request.headers.get("authorization")
    dev_user = request.headers.get("x-medcompare-dev-user")
    try:
        principal = authenticate(
            service,
            authorization=authorization,
            dev_user=dev_user,
            settings=settings,
        )
    except AuthServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    request.state.principal = principal
    return principal


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def optional_principal(request: Request) -> Principal | None:
    try:
        return current_principal(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None
        raise


def require_permission(permission: Permission) -> Callable[[Principal], Principal]:
    """Factory dependency: 403 when the principal lacks *permission*."""

    def _dependency(principal: PrincipalDep) -> Principal:
        if not principal.has_permission(permission):
            raise HTTPException(status_code=403, detail="Permission denied")
        return principal

    return _dependency


def require_any_permission(
    *permissions: Permission,
) -> Callable[[Principal], Principal]:
    """Factory dependency: 403 when the principal has none of *permissions*."""

    def _dependency(principal: PrincipalDep) -> Principal:
        if not any(principal.has_permission(p) for p in permissions):
            raise HTTPException(status_code=403, detail="Permission denied")
        return principal

    return _dependency


def require_hospital_access(
    principal: Principal,
    service: AuthService,
    hospital_id: str,
    *,
    correlation_id: str = "",
) -> None:
    """Resource-level isolation (Layer 1, P24). Fail closed.

    ADMIN may access any hospital; other roles need an explicit grant.
    """
    if not hospital_id:
        raise HTTPException(status_code=400, detail="hospital_id is required")
    if not service.can_access_hospital(principal, hospital_id):
        from hospital_extractor.auth.audit import AuditOutcome, record_audit

        record_audit(
            service.open_store(),
            action="hospital.access_denied",
            outcome=AuditOutcome.DENIED,
            principal=principal,
            resource_type="hospital",
            resource_id=hospital_id,
            hospital_id=hospital_id,
            correlation_id=correlation_id,
        )
        raise HTTPException(status_code=403, detail="Access denied to this hospital")
