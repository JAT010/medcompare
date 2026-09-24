"""P20 monitoring API: list / read / create / enable / disable / run policies.

These endpoints never start the crawler themselves — they only write policy
rows.  The explicit ``monitoring worker`` process performs the orchestration
captures, mirroring the P19-B verification worker boundary.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from hospital_extractor.auth.audit import record_audit
from hospital_extractor.auth.dependencies import (
    AuthDep,
    PrincipalDep,
    get_request_correlation,
    require_hospital_access,
    require_permission,
)
from hospital_extractor.auth.models import Permission, Principal
from hospital_extractor.monitoring.models import MonitoringPolicy, ScheduleFrequency
from hospital_extractor.monitoring.service import (
    create_policy,
    list_policies,
    run_policy_now,
    set_policy_enabled,
)
from hospital_extractor.sources import SourceType
from hospital_extractor.storage import HospitalDatabase
from hospital_extractor.storage.database import default_database_path

__all__ = ["monitoring_router"]

monitoring_router = APIRouter()


class MonitoringPolicyCreateRequest(BaseModel):
    """Body for ``POST /api/v1/hospitals/{hospital_id}/monitoring-policies``."""

    frequency: ScheduleFrequency
    source: str = Field(default="website", description="Target source type")
    target_ref: str = Field(default="", description="Target URL/ref to capture")
    enabled: bool = True

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        if value not in SourceType.values():
            raise ValueError(
                f"unknown source {value!r}; expected one of {', '.join(SourceType.values())}"
            )
        return value


def _policy_json(policy: MonitoringPolicy) -> dict[str, Any]:
    return policy.model_dump(mode="json")


def _open_db(request: Request) -> HospitalDatabase:
    configured = getattr(request.app.state, "medcompare_db_path", default_database_path())
    return HospitalDatabase(configured)


def _resolve_hospital(db: HospitalDatabase, reference: str) -> str | None:
    return db.find_hospital_id(
        preferred=reference,
        source_ref=reference,
        hospital_name=reference,
    )


@monitoring_router.get("/api/v1/hospitals/{hospital_id}/monitoring-policies")
async def api_list_monitoring_policies(
    hospital_id: str,
    request: Request,
    enabled: bool | None = None,
) -> JSONResponse:
    """List monitoring policies for one hospital."""
    with _open_db(request) as db:
        resolved = _resolve_hospital(db, hospital_id)
        if resolved is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Hospital not found", "detail": hospital_id},
            )
        policies = list_policies(db, hospital_id=resolved, enabled=enabled)
    return JSONResponse(
        content={
            "hospital_id": resolved,
            "monitoring_policies": [_policy_json(p) for p in policies],
        }
    )


@monitoring_router.get("/api/v1/monitoring-policies/{policy_id}")
async def api_get_monitoring_policy(policy_id: str, request: Request) -> JSONResponse:
    """Return one monitoring policy."""
    with _open_db(request) as db:
        policies = list_policies(db)
        policy = next((p for p in policies if p.policy_id == policy_id), None)
    if policy is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Monitoring policy not found", "detail": policy_id},
        )
    return JSONResponse(content={"monitoring_policy": _policy_json(policy)})


@monitoring_router.post("/api/v1/hospitals/{hospital_id}/monitoring-policies")
async def api_create_monitoring_policy(
    hospital_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[
        Principal, Depends(require_permission(Permission.MANAGE_MONITORING))
    ],
    body: Annotated[MonitoringPolicyCreateRequest | None, Body()] = None,
) -> JSONResponse:
    """Create (or idempotently update) a monitoring policy for one identity."""
    require_hospital_access(
        principal,
        service,
        hospital_id,
        correlation_id=get_request_correlation(request),
    )
    if body is None:
        return JSONResponse(
            status_code=422,
            content={"error": "Request body required", "detail": "frequency"},
        )
    with _open_db(request) as db:
        resolved = _resolve_hospital(db, hospital_id)
        if resolved is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Hospital not found", "detail": hospital_id},
            )
        policy = create_policy(
            db,
            hospital_id=resolved,
            frequency=body.frequency,
            source_type=body.source,
            target_ref=body.target_ref,
            enabled=body.enabled,
        )
    with service.open_store() as store:
        record_audit(
            store,
            action="monitoring.create",
            principal=principal,
            resource_type="monitoring_policy",
            resource_id=policy.policy_id,
            hospital_id=resolved,
            correlation_id=get_request_correlation(request),
            metadata={"frequency": body.frequency.value, "enabled": body.enabled},
        )
    return JSONResponse(status_code=201, content={"monitoring_policy": _policy_json(policy)})


@monitoring_router.post("/api/v1/monitoring-policies/{policy_id}/enable")
async def api_enable_monitoring_policy(
    policy_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[
        Principal, Depends(require_permission(Permission.MANAGE_MONITORING))
    ],
) -> JSONResponse:
    """Enable a policy (resets a FAILED policy to PENDING)."""
    with _open_db(request) as db:
        policy = set_policy_enabled(db, policy_id, enabled=True)
    if policy is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Monitoring policy not found", "detail": policy_id},
        )
    with service.open_store() as store:
        record_audit(
            store,
            action="monitoring.enable",
            principal=principal,
            resource_type="monitoring_policy",
            resource_id=policy_id,
            hospital_id=policy.hospital_id,
            correlation_id=get_request_correlation(request),
        )
    return JSONResponse(content={"monitoring_policy": _policy_json(policy)})


@monitoring_router.post("/api/v1/monitoring-policies/{policy_id}/disable")
async def api_disable_monitoring_policy(
    policy_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[
        Principal, Depends(require_permission(Permission.MANAGE_MONITORING))
    ],
) -> JSONResponse:
    """Disable a policy (never claimed while disabled)."""
    with _open_db(request) as db:
        policy = set_policy_enabled(db, policy_id, enabled=False)
    if policy is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Monitoring policy not found", "detail": policy_id},
        )
    with service.open_store() as store:
        record_audit(
            store,
            action="monitoring.disable",
            principal=principal,
            resource_type="monitoring_policy",
            resource_id=policy_id,
            hospital_id=policy.hospital_id,
            correlation_id=get_request_correlation(request),
        )
    return JSONResponse(content={"monitoring_policy": _policy_json(policy)})


@monitoring_router.post("/api/v1/monitoring-policies/{policy_id}/run")
async def api_run_monitoring_policy(
    policy_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[
        Principal, Depends(require_permission(Permission.MANAGE_MONITORING))
    ],
) -> JSONResponse:
    """Trigger one policy immediately (still atomic claim + idempotent).

    This endpoint delegates to orchestration under the hood but does **not**
    start a long-running worker; it is an explicit single-shot request.
    """
    with _open_db(request) as db:
        policy = await run_policy_now(db, policy_id)
    if policy is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Monitoring policy not found", "detail": policy_id},
        )
    with service.open_store() as store:
        record_audit(
            store,
            action="monitoring.run",
            principal=principal,
            resource_type="monitoring_policy",
            resource_id=policy_id,
            hospital_id=policy.hospital_id,
            correlation_id=get_request_correlation(request),
        )
    return JSONResponse(content={"monitoring_policy": _policy_json(policy)})
