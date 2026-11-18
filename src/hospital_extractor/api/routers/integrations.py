"""Facility-scoped hospital integration / verification / monitoring API.

Read endpoints stay open like the rest of the API (no secrets exist in these
records). Write operations are gated by the operator bearer token
(:func:`hospital_extractor.api.deps_operator.require_operator`). Hospital
payloads (snapshot ``payload_json``) are operator-only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from hospital_extractor.api.deps import get_db_session
from hospital_extractor.api.deps_operator import require_operator
from hospital_extractor.monitoring.service import MonitoringError, MonitoringService
from hospital_extractor.persistence.exceptions import FacilityNotFoundError
from hospital_extractor.persistence.models import HospitalFacility
from hospital_extractor.persistence.models_integration import (
    ChangeSetStatus,
    HospitalDataSnapshot,
    HospitalDetectedChange,
    HospitalMonitoringExecution,
    HospitalMonitoringSchedule,
    HospitalReconciliationCase,
    HospitalSnapshotChangeSet,
    HospitalSourceIntegration,
    HospitalVerificationJob,
    HospitalVerificationResult,
    IntegrationAuthMode,
    IntegrationStatus,
    ReconciliationCaseStatus,
    VerificationJobStatus,
    VerificationTriggerType,
)
from hospital_extractor.persistence.registry.service import RegistryService
from hospital_extractor.reconciliation.service import (
    ReconciliationError,
    ReconciliationService,
)
from hospital_extractor.snapshots.service import SnapshotService
from hospital_extractor.sources.base import (
    ConnectorErrorCode,
    ConnectorUnauthorizedError,
    HospitalConnectorError,
)
from hospital_extractor.sources.registry import (
    KNOWN_CONNECTOR_TYPES,
    build_connector,
)
from hospital_extractor.verification.orchestrator import capture_and_enqueue

__all__ = ["router"]

router = APIRouter(tags=["integrations"])

MOCK_CONNECTOR_TYPE = "mock_hospital_api_simulated"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class IntegrationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    facility_id: uuid.UUID
    name: str
    connector_type: str
    base_url: str | None
    auth_mode: str
    credential_env_ref: str | None
    config_json: dict[str, Any]
    status: str
    enabled: bool
    simulated: bool
    created_at: datetime
    updated_at: datetime


class IntegrationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    connector_type: str = Field(min_length=1, max_length=64)
    base_url: str | None = Field(default=None, max_length=2000)
    auth_mode: Literal["none", "api_key", "bearer_token"] = "none"
    credential_env_ref: str | None = Field(default=None, max_length=255)
    config_json: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False


class IntegrationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, max_length=2000)
    auth_mode: Literal["none", "api_key", "bearer_token"] | None = None
    credential_env_ref: str | None = Field(default=None, max_length=255)
    config_json: dict[str, Any] | None = None
    enabled: bool | None = None
    status: Literal["not_configured", "configured", "disabled", "error"] | None = None


class IntegrationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    items: list[IntegrationOut]


class IntegrationEventOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    integration_id: uuid.UUID
    event_type: str
    detail_json: dict[str, Any]
    created_at: datetime


class IntegrationDetailOut(IntegrationOut):
    events: list[IntegrationEventOut] = Field(default_factory=list)


class SnapshotOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    facility_id: uuid.UUID
    integration_id: uuid.UUID | None
    source_kind: str
    version_number: int
    content_hash: str
    captured_at: datetime
    captured_by: str
    is_simulation: bool
    created_at: datetime
    provenance_json: dict[str, Any] | None = None
    payload_json: dict[str, Any] | None = None  # operator-only


class SnapshotListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    items: list[SnapshotOut]
    limit: int
    offset: int


class ChangeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    path: str
    change_type: str
    entity_name: str
    field: str
    old_value_json: object | None
    new_value_json: object | None
    source_url: str | None
    detected_at: datetime


class ChangeSetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    facility_id: uuid.UUID
    integration_id: uuid.UUID | None
    previous_snapshot_id: uuid.UUID | None
    snapshot_id: uuid.UUID
    status: str
    added_count: int
    removed_count: int
    modified_count: int
    detected_at: datetime


class ChangeSetDetailOut(ChangeSetOut):
    changes: list[ChangeOut] = Field(default_factory=list)


class ChangeSetListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    items: list[ChangeSetOut]
    limit: int
    offset: int


class ResultOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    job_id: uuid.UUID
    facility_id: uuid.UUID
    hospital_snapshot_id: uuid.UUID | None
    website_crawl_run_id: uuid.UUID | None
    status: str
    summary: str | None
    compared_at: datetime
    evidence_json: dict[str, Any]
    provenance_json: dict[str, Any]


class JobOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    facility_id: uuid.UUID
    integration_id: uuid.UUID | None
    change_set_id: uuid.UUID | None
    snapshot_id: uuid.UUID | None
    target_ref: str
    trigger_type: str
    status: str
    attempt_count: int
    max_attempts: int
    scheduled_for: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    result: ResultOut | None = None


class JobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    items: list[JobOut]
    limit: int
    offset: int


class CaptureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: SnapshotOut
    change_set: ChangeSetDetailOut
    verification_job: JobOut | None
    job_created: bool
    job_skip_reason: str | None


class ManualJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_job: JobOut | None
    created: bool
    reason: str | None = None


class ScheduleOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    facility_id: uuid.UUID
    integration_id: uuid.UUID
    name: str
    interval_seconds: int
    enabled: bool
    next_run_at: datetime | None
    last_enqueued_at: datetime | None
    last_status: str | None


class ScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    integration_id: uuid.UUID
    interval_seconds: int = Field(ge=60)
    enabled: bool = False
    target_ref: str | None = Field(default=None, max_length=2000)


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    interval_seconds: int | None = Field(default=None, ge=60)
    enabled: bool | None = None
    target_ref: str | None = Field(default=None, max_length=2000)


class ScheduleListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    items: list[ScheduleOut]


class ExecutionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    schedule_id: uuid.UUID
    facility_id: uuid.UUID
    job_id: uuid.UUID | None
    status: str
    reason: str | None
    scheduled_for: datetime | None
    executed_at: datetime


class ExecutionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    items: list[ExecutionOut]
    limit: int
    offset: int


class ReconciliationCaseOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    facility_id: uuid.UUID
    verification_result_id: uuid.UUID | None
    detected_change_id: uuid.UUID | None
    status: str
    resolution: str | None
    summary: str | None
    notes: str | None
    opened_at: datetime
    resolved_at: datetime | None
    resolved_by_label: str | None
    provenance_json: dict[str, Any]


class ReconciliationEventOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    case_id: uuid.UUID
    from_status: str | None
    to_status: str
    actor_label: str | None
    note: str | None
    occurred_at: datetime


class ReconciliationCaseDetailOut(ReconciliationCaseOut):
    events: list[ReconciliationEventOut] = Field(default_factory=list)


class CaseListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    items: list[ReconciliationCaseOut]
    limit: int
    offset: int


class TransitionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_status: Literal["open", "under_review", "resolved", "dismissed"]
    resolution: (
        Literal[
            "hospital_source_confirmed",
            "website_confirmed",
            "source_disagreement",
            "insufficient_evidence",
            "false_positive",
        ]
        | None
    ) = None
    note: str | None = Field(default=None, max_length=2000)
    actor_label: str | None = Field(default=None, max_length=128)


class ManualCaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    notes: str | None = Field(default=None, max_length=4000)
    verification_result_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _integration_out(row: HospitalSourceIntegration) -> IntegrationOut:
    simulated = row.connector_type == MOCK_CONNECTOR_TYPE
    return IntegrationOut(
        id=row.id,
        facility_id=row.facility_id,
        name=row.name,
        connector_type=row.connector_type,
        base_url=row.base_url,
        auth_mode=row.auth_mode,
        credential_env_ref=row.credential_env_ref,
        config_json=dict(row.config_json or {}),
        status=row.status,
        enabled=row.enabled,
        simulated=simulated,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _snapshot_out(row: HospitalDataSnapshot, *, include_payload: bool) -> SnapshotOut:
    return SnapshotOut(
        id=row.id,
        facility_id=row.facility_id,
        integration_id=row.integration_id,
        source_kind=row.source_kind,
        version_number=row.version_number,
        content_hash=row.content_hash,
        captured_at=row.captured_at,
        captured_by=row.captured_by,
        is_simulation=row.is_simulation,
        created_at=row.created_at,
        provenance_json=dict(row.provenance_json or {}) if include_payload else None,
        payload_json=dict(row.payload_json or {}) if include_payload else None,
    )


def _change_out(row: HospitalDetectedChange) -> ChangeOut:
    return ChangeOut(
        id=row.id,
        path=row.path,
        change_type=row.change_type,
        entity_name=row.entity_name,
        field=row.field,
        old_value_json=row.old_value_json,
        new_value_json=row.new_value_json,
        source_url=row.source_url,
        detected_at=row.detected_at,
    )


def _change_set_out(row: HospitalSnapshotChangeSet) -> ChangeSetOut:
    return ChangeSetOut(
        id=row.id,
        facility_id=row.facility_id,
        integration_id=row.integration_id,
        previous_snapshot_id=row.previous_snapshot_id,
        snapshot_id=row.snapshot_id,
        status=row.status,
        added_count=row.added_count,
        removed_count=row.removed_count,
        modified_count=row.modified_count,
        detected_at=row.detected_at,
    )


def _result_out(row: HospitalVerificationResult) -> ResultOut:
    return ResultOut(
        id=row.id,
        job_id=row.job_id,
        facility_id=row.facility_id,
        hospital_snapshot_id=row.hospital_snapshot_id,
        website_crawl_run_id=row.website_crawl_run_id,
        status=row.status,
        summary=row.summary,
        compared_at=row.compared_at,
        evidence_json=dict(row.evidence_json or {}),
        provenance_json=dict(row.provenance_json or {}),
    )


def _job_out(row: HospitalVerificationJob) -> JobOut:
    result = row.result
    return JobOut(
        id=row.id,
        facility_id=row.facility_id,
        integration_id=row.integration_id,
        change_set_id=row.change_set_id,
        snapshot_id=row.snapshot_id,
        target_ref=row.target_ref,
        trigger_type=row.trigger_type,
        status=row.status,
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        scheduled_for=row.scheduled_for,
        started_at=row.started_at,
        finished_at=row.finished_at,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        created_at=row.created_at,
        result=_result_out(result) if result is not None else None,
    )


def _schedule_out(row: HospitalMonitoringSchedule) -> ScheduleOut:
    return ScheduleOut(
        id=row.id,
        facility_id=row.facility_id,
        integration_id=row.integration_id,
        name=row.name,
        interval_seconds=row.interval_seconds,
        enabled=row.enabled,
        next_run_at=row.next_run_at,
        last_enqueued_at=row.last_enqueued_at,
        last_status=row.last_status,
    )


def _execution_out(row: HospitalMonitoringExecution) -> ExecutionOut:
    return ExecutionOut(
        id=row.id,
        schedule_id=row.schedule_id,
        facility_id=row.facility_id,
        job_id=row.job_id,
        status=row.status,
        reason=row.reason,
        scheduled_for=row.scheduled_for,
        executed_at=row.executed_at,
    )


def _case_out(row: HospitalReconciliationCase) -> ReconciliationCaseOut:
    return ReconciliationCaseOut(
        id=row.id,
        facility_id=row.facility_id,
        verification_result_id=row.verification_result_id,
        detected_change_id=row.detected_change_id,
        status=row.status,
        resolution=row.resolution,
        summary=row.summary,
        notes=row.notes,
        opened_at=row.opened_at,
        resolved_at=row.resolved_at,
        resolved_by_label=row.resolved_by_label,
        provenance_json=dict(row.provenance_json or {}),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _facility(session: Session, facility_id: uuid.UUID) -> HospitalFacility:
    try:
        return RegistryService(session).get_facility(facility_id)
    except FacilityNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "facility_not_found", "message": str(exc)},
        ) from exc


def _integration(
    session: Session, facility_id: uuid.UUID, integration_id: uuid.UUID
) -> HospitalSourceIntegration:
    _facility(session, facility_id)
    row = session.get(HospitalSourceIntegration, integration_id)
    if row is None or row.facility_id != facility_id:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "integration_not_found",
                "message": f"No integration {integration_id} for facility {facility_id}.",
            },
        )
    return row


def _require_operator_error(exc: HospitalConnectorError) -> HTTPException:
    if isinstance(exc, ConnectorUnauthorizedError):
        return HTTPException(
            status_code=403,
            detail={"error": "unauthorized", "message": str(exc)},
        )
    code = exc.code.value if isinstance(exc.code, ConnectorErrorCode) else str(exc.code)
    return HTTPException(
        status_code=502,
        detail={"error": "connector_failed", "code": code, "message": str(exc)},
    )


def _validate_connector_config(
    *,
    connector_type: str,
    base_url: str | None,
    config_json: dict[str, Any],
    auth_mode: str,
    credential_env_ref: str | None,
    enabled: bool,
) -> None:
    if connector_type not in KNOWN_CONNECTOR_TYPES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_connector_type",
                "message": f"connector_type must be one of {sorted(KNOWN_CONNECTOR_TYPES)}.",
            },
        )
    if auth_mode != IntegrationAuthMode.NONE.value and not (credential_env_ref or "").strip():
        raise HTTPException(
            status_code=422,
            detail={
                "error": "credential_reference_required",
                "message": (
                    "auth_mode other than 'none' requires credential_env_ref naming the "
                    "environment variable that holds the credential. The secret itself is "
                    "never stored."
                ),
            },
        )
    if connector_type == MOCK_CONNECTOR_TYPE:
        variant = config_json.get("payload_variant", "default")
        from hospital_extractor.sources.mock_hospital_api import VARIANTS

        if variant not in VARIANTS:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_payload_variant",
                    "message": f"payload_variant must be one of {sorted(VARIANTS)}.",
                },
            )
        return
    # Live connector validation.
    allowed = config_json.get("allowed_hosts")
    allowed_hosts = [str(h).strip() for h in allowed] if isinstance(allowed, list) else []
    allowed_hosts = [h for h in allowed_hosts if h]
    if not allowed_hosts:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "allowlist_required",
                "message": (
                    "A live hospital_rest_json integration requires a non-empty "
                    "'allowed_hosts' list in config_json. Live captures fail closed "
                    "without it."
                ),
            },
        )
    if not base_url or not base_url.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "error": "base_url_required",
                "message": "A live integration requires a base_url for the hospital API.",
            },
        )


# ---------------------------------------------------------------------------
# Integration endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/facilities/{facility_id}/integrations",
    response_model=IntegrationListResponse,
)
def list_integrations(
    facility_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> IntegrationListResponse:
    _facility(session, facility_id)
    from sqlalchemy import select

    rows = session.scalars(
        select(HospitalSourceIntegration)
        .where(HospitalSourceIntegration.facility_id == facility_id)
        .order_by(HospitalSourceIntegration.name, HospitalSourceIntegration.id)
    ).all()
    return IntegrationListResponse(
        facility_id=facility_id,
        items=[_integration_out(row) for row in rows],
    )


@router.post(
    "/facilities/{facility_id}/integrations",
    response_model=IntegrationOut,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
def create_integration(
    facility_id: uuid.UUID,
    body: IntegrationCreate,
    session: Annotated[Session, Depends(get_db_session)],
) -> IntegrationOut:
    _facility(session, facility_id)
    connector_type = body.connector_type.strip()
    base_url = body.base_url.strip() if body.base_url else None
    credential_env_ref = (body.credential_env_ref or "").strip() or None
    _validate_connector_config(
        connector_type=connector_type,
        base_url=base_url,
        config_json=body.config_json,
        auth_mode=body.auth_mode,
        credential_env_ref=credential_env_ref,
        enabled=body.enabled,
    )
    status = IntegrationStatus.CONFIGURED.value if body.enabled else (
        IntegrationStatus.DISABLED.value
    )
    if body.connector_type == MOCK_CONNECTOR_TYPE and body.enabled:
        status = IntegrationStatus.CONFIGURED.value
    row = HospitalSourceIntegration(
        facility_id=facility_id,
        name=body.name.strip(),
        connector_type=connector_type,
        base_url=base_url,
        auth_mode=body.auth_mode,
        credential_env_ref=credential_env_ref,
        config_json=body.config_json,
        status=status,
        enabled=body.enabled,
    )
    session.add(row)
    try:
        session.flush()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "integration_exists",
                "message": f"An integration named {body.name!r} already exists for this facility.",
            },
        ) from exc
    from hospital_extractor.persistence.models_integration import HospitalIntegrationEvent

    session.add(
        HospitalIntegrationEvent(
            integration_id=row.id,
            facility_id=facility_id,
            event_type="created",
            detail_json={
                "connector_type": connector_type,
                "enabled": body.enabled,
                "simulated": connector_type == MOCK_CONNECTOR_TYPE,
            },
        )
    )
    session.flush()
    return _integration_out(row)


@router.get(
    "/facilities/{facility_id}/integrations/{integration_id}",
    response_model=IntegrationDetailOut,
)
def get_integration(
    facility_id: uuid.UUID,
    integration_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> IntegrationDetailOut:
    row = _integration(session, facility_id, integration_id)
    from sqlalchemy import select

    from hospital_extractor.persistence.models_integration import HospitalIntegrationEvent

    events = session.scalars(
        select(HospitalIntegrationEvent)
        .where(HospitalIntegrationEvent.integration_id == row.id)
        .order_by(HospitalIntegrationEvent.created_at)
    ).all()
    base = _integration_out(row)
    return IntegrationDetailOut(
        **base.model_dump(),
        events=[
            IntegrationEventOut(
                id=event.id,
                integration_id=event.integration_id,
                event_type=event.event_type,
                detail_json=dict(event.detail_json or {}),
                created_at=event.created_at,
            )
            for event in events
        ],
    )


@router.patch(
    "/facilities/{facility_id}/integrations/{integration_id}",
    response_model=IntegrationOut,
    dependencies=[Depends(require_operator)],
)
def update_integration(
    facility_id: uuid.UUID,
    integration_id: uuid.UUID,
    body: IntegrationUpdate,
    session: Annotated[Session, Depends(get_db_session)],
) -> IntegrationOut:
    row = _integration(session, facility_id, integration_id)
    merged_config = dict(row.config_json or {})
    if body.config_json is not None:
        merged_config = body.config_json
    _validate_connector_config(
        connector_type=row.connector_type,
        base_url=(body.base_url if body.base_url is not None else row.base_url),
        config_json=merged_config,
        auth_mode=body.auth_mode or row.auth_mode,
        credential_env_ref=(
            body.credential_env_ref
            if body.credential_env_ref is not None
            else row.credential_env_ref
        ),
        enabled=body.enabled if body.enabled is not None else row.enabled,
    )
    changed = False
    if body.name is not None:
        row.name = body.name.strip()
        changed = True
    if body.base_url is not None:
        row.base_url = body.base_url.strip() or None
        changed = True
    if body.auth_mode is not None:
        row.auth_mode = body.auth_mode
        changed = True
    if body.credential_env_ref is not None:
        row.credential_env_ref = body.credential_env_ref.strip() or None
        changed = True
    if body.config_json is not None:
        row.config_json = merged_config
        changed = True
    if body.enabled is not None:
        row.enabled = body.enabled
        row.status = (
            IntegrationStatus.CONFIGURED.value
            if body.enabled
            else IntegrationStatus.DISABLED.value
        )
        changed = True
    if body.status is not None:
        row.status = body.status
        changed = True
    session.flush()
    from hospital_extractor.persistence.models_integration import HospitalIntegrationEvent

    if changed:
        session.add(
            HospitalIntegrationEvent(
                integration_id=row.id,
                facility_id=facility_id,
                event_type="updated",
                detail_json={"fields": sorted(body.model_fields_set)},
            )
        )
        session.flush()
    return _integration_out(row)


@router.post(
    "/facilities/{facility_id}/integrations/{integration_id}/capture",
    response_model=CaptureResponse,
    dependencies=[Depends(require_operator)],
)
async def capture_integration(
    facility_id: uuid.UUID,
    integration_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> CaptureResponse:
    """Run a synchronous capture against the (simulated or authorized) source."""
    integration = _integration(session, facility_id, integration_id)
    facility = _facility(session, facility_id)
    if not integration.enabled:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "integration_disabled",
                "message": "Enable the integration before capturing.",
            },
        )
    target_ref = None
    if facility.canonical_url:
        target_ref = facility.canonical_url
    try:
        connector = build_connector(integration)
        outcome = await capture_and_enqueue(
            session,
            integration,
            connector=connector,
            target_ref=target_ref,
            trigger_type="change_detected",
            captured_by="operator",
        )
    except HospitalConnectorError as exc:
        raise _require_operator_error(exc) from exc

    capture = outcome.capture
    change_set = capture.change_set
    changes = capture.changes
    return CaptureResponse(
        snapshot=_snapshot_out(capture.snapshot, include_payload=True),
        change_set=ChangeSetDetailOut(
            **_change_set_out(change_set).model_dump(),
            changes=[_change_out(change) for change in changes],
        ),
        verification_job=_job_out(outcome.verification_job)
        if outcome.verification_job is not None
        else None,
        job_created=outcome.job_created,
        job_skip_reason=outcome.job_skip_reason,
    )


# ---------------------------------------------------------------------------
# Snapshot endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/facilities/{facility_id}/snapshots",
    response_model=SnapshotListResponse,
)
def list_snapshots(
    facility_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
    source_kind: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> SnapshotListResponse:
    _facility(session, facility_id)
    rows = SnapshotService(session).list_for_facility(
        facility_id, source_kind=source_kind, limit=limit, offset=offset
    )
    return SnapshotListResponse(
        facility_id=facility_id,
        items=[_snapshot_out(row, include_payload=False) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/facilities/{facility_id}/snapshots/{snapshot_id}",
    response_model=SnapshotOut,
    dependencies=[Depends(require_operator)],
)
def get_snapshot(
    facility_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> SnapshotOut:
    """Full snapshot including payload and provenance (operator-only)."""
    _facility(session, facility_id)
    row = SnapshotService(session).get_snapshot_for_facility(snapshot_id, facility_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "snapshot_not_found",
                "message": f"No snapshot {snapshot_id} for facility {facility_id}.",
            },
        )
    return _snapshot_out(row, include_payload=True)


@router.get(
    "/facilities/{facility_id}/change-sets",
    response_model=ChangeSetListResponse,
)
def list_change_sets(
    facility_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ChangeSetListResponse:
    _facility(session, facility_id)
    rows = SnapshotService(session).list_change_sets(facility_id, limit=limit, offset=offset)
    return ChangeSetListResponse(
        facility_id=facility_id,
        items=[_change_set_out(row) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/facilities/{facility_id}/change-sets/{change_set_id}",
    response_model=ChangeSetDetailOut,
)
def get_change_set(
    facility_id: uuid.UUID,
    change_set_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> ChangeSetDetailOut:
    _facility(session, facility_id)
    service = SnapshotService(session)
    row = service.get_change_set(change_set_id)
    if row is None or row.facility_id != facility_id:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "change_set_not_found",
                "message": f"No change set {change_set_id} for facility {facility_id}.",
            },
        )
    changes = service.list_changes(row.id)
    return ChangeSetDetailOut(
        **_change_set_out(row).model_dump(),
        changes=[_change_out(change) for change in changes],
    )


# ---------------------------------------------------------------------------
# Verification job endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/facilities/{facility_id}/verification-jobs",
    response_model=ManualJobResponse,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
def create_verification_job(
    facility_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> ManualJobResponse:
    """Enqueue a manual verification job against the latest detected changes."""
    _facility(session, facility_id)
    from sqlalchemy import select

    latest = session.scalars(
        select(HospitalSnapshotChangeSet)
        .where(HospitalSnapshotChangeSet.facility_id == facility_id)
        .order_by(
            HospitalSnapshotChangeSet.detected_at.desc(),
            HospitalSnapshotChangeSet.created_at.desc(),
        )
        .limit(1)
    ).first()
    if latest is None or latest.status != ChangeSetStatus.CHANGES_DETECTED.value:
        return ManualJobResponse(
            verification_job=None,
            created=False,
            reason="nothing_to_verify",
        )
    snapshot = SnapshotService(session).get_snapshot(latest.snapshot_id)
    facility = _facility(session, facility_id)
    from hospital_extractor.jobs.queue import enqueue_job

    enqueued = enqueue_job(
        session,
        facility_id=facility_id,
        change_set_id=latest.id,
        snapshot_id=snapshot.id if snapshot else None,
        target_ref=facility.canonical_url or "",
        trigger_type=VerificationTriggerType.MANUAL.value,
        idempotency_key=f"manual:{latest.id}",
        input_json={"change_set_id": str(latest.id)},
    )
    if not enqueued.job.target_ref:
        enqueued.job.target_ref = facility.canonical_url or ""
    session.flush()
    return ManualJobResponse(
        verification_job=_job_out(enqueued.job),
        created=enqueued.created,
    )


@router.get(
    "/facilities/{facility_id}/verification-jobs",
    response_model=JobListResponse,
)
def list_verification_jobs(
    facility_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> JobListResponse:
    _facility(session, facility_id)
    from sqlalchemy import select

    stmt = select(HospitalVerificationJob).where(
        HospitalVerificationJob.facility_id == facility_id
    )
    if status:
        stmt = stmt.where(HospitalVerificationJob.status == status)
    stmt = (
        stmt.order_by(
            HospitalVerificationJob.created_at.desc(),
            HospitalVerificationJob.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    rows = list(session.scalars(stmt).all())
    return JobListResponse(
        facility_id=facility_id,
        items=[_job_out(row) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/facilities/{facility_id}/verification-jobs/{job_id}",
    response_model=JobOut,
)
def get_verification_job(
    facility_id: uuid.UUID,
    job_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> JobOut:
    _facility(session, facility_id)
    row = session.get(HospitalVerificationJob, job_id)
    if row is None or row.facility_id != facility_id:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "verification_job_not_found",
                "message": f"No verification job {job_id} for facility {facility_id}.",
            },
        )
    return _job_out(row)


@router.post(
    "/facilities/{facility_id}/verification-jobs/{job_id}/cancel",
    response_model=JobOut,
    dependencies=[Depends(require_operator)],
)
def cancel_verification_job(
    facility_id: uuid.UUID,
    job_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> JobOut:
    _facility(session, facility_id)
    row = session.get(HospitalVerificationJob, job_id)
    if row is None or row.facility_id != facility_id:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "verification_job_not_found",
                "message": f"No verification job {job_id} for facility {facility_id}.",
            },
        )

    if row.status not in (
        VerificationJobStatus.PENDING.value,
        VerificationJobStatus.RUNNING.value,
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "job_not_cancellable",
                "message": f"Job is in status {row.status!r} and cannot be cancelled.",
            },
        )
    row.status = VerificationJobStatus.CANCELLED.value
    row.lease_owner = None
    row.lease_expires_at = None
    row.finished_at = datetime.now(UTC)
    session.flush()
    return _job_out(row)


# ---------------------------------------------------------------------------
# Monitoring endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/facilities/{facility_id}/monitoring/schedules",
    response_model=ScheduleListResponse,
)
def list_monitoring_schedules(
    facility_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> ScheduleListResponse:
    _facility(session, facility_id)
    rows = MonitoringService(session).list_schedules(facility_id)
    return ScheduleListResponse(
        facility_id=facility_id,
        items=[_schedule_out(row) for row in rows],
    )


@router.post(
    "/facilities/{facility_id}/monitoring/schedules",
    response_model=ScheduleOut,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
def create_monitoring_schedule(
    facility_id: uuid.UUID,
    body: ScheduleCreate,
    session: Annotated[Session, Depends(get_db_session)],
) -> ScheduleOut:
    _facility(session, facility_id)
    _integration(session, facility_id, body.integration_id)
    config: dict[str, Any] = {}
    if body.target_ref:
        config["target_ref"] = body.target_ref.strip()
    try:
        schedule = MonitoringService(session).create_schedule(
            facility_id=facility_id,
            integration_id=body.integration_id,
            name=body.name,
            interval_seconds=body.interval_seconds,
            enabled=body.enabled,
            config_json=config,
        )
    except MonitoringError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "schedule_conflict", "message": str(exc)},
        ) from exc
    return _schedule_out(schedule)


@router.patch(
    "/facilities/{facility_id}/monitoring/schedules/{schedule_id}",
    response_model=ScheduleOut,
    dependencies=[Depends(require_operator)],
)
def update_monitoring_schedule(
    facility_id: uuid.UUID,
    schedule_id: uuid.UUID,
    body: ScheduleUpdate,
    session: Annotated[Session, Depends(get_db_session)],
) -> ScheduleOut:
    _facility(session, facility_id)
    schedule = session.get(HospitalMonitoringSchedule, schedule_id)
    if schedule is None or schedule.facility_id != facility_id:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "schedule_not_found",
                "message": f"No monitoring schedule {schedule_id} for facility {facility_id}.",
            },
        )
    if body.interval_seconds is not None:
        schedule.interval_seconds = body.interval_seconds
    if body.name is not None:
        schedule.name = body.name.strip()
    if body.target_ref is not None:
        config = dict(schedule.config_json or {})
        config["target_ref"] = body.target_ref.strip()
        schedule.config_json = config
    if body.enabled is not None:
        MonitoringService(session).set_enabled(schedule, body.enabled)
    if body.enabled is None and body.interval_seconds is not None:
        # Re-arm scheduling window after interval change.
        if schedule.next_run_at is not None:
            schedule.next_run_at = datetime.now(UTC) + timedelta(
                seconds=schedule.interval_seconds
            )
    session.flush()
    return _schedule_out(schedule)


@router.get(
    "/facilities/{facility_id}/monitoring/executions",
    response_model=ExecutionListResponse,
)
def list_monitoring_executions(
    facility_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
    schedule_id: uuid.UUID | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ExecutionListResponse:
    _facility(session, facility_id)
    rows = MonitoringService(session).list_executions(
        facility_id, schedule_id=schedule_id, limit=limit, offset=offset
    )
    return ExecutionListResponse(
        facility_id=facility_id,
        items=[_execution_out(row) for row in rows],
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Reconciliation endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/facilities/{facility_id}/reconciliation-cases",
    response_model=CaseListResponse,
)
def list_reconciliation_cases(
    facility_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> CaseListResponse:
    _facility(session, facility_id)
    rows = ReconciliationService(session).list_cases(
        facility_id, status=status, limit=limit, offset=offset
    )
    return CaseListResponse(
        facility_id=facility_id,
        items=[_case_out(row) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/facilities/{facility_id}/reconciliation-cases/{case_id}",
    response_model=ReconciliationCaseDetailOut,
)
def get_reconciliation_case(
    facility_id: uuid.UUID,
    case_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> ReconciliationCaseDetailOut:
    _facility(session, facility_id)
    row = ReconciliationService(session).get_case_for_facility(case_id, facility_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "reconciliation_case_not_found",
                "message": f"No reconciliation case {case_id} for facility {facility_id}.",
            },
        )
    from sqlalchemy import select

    from hospital_extractor.persistence.models_integration import HospitalReconciliationCaseEvent

    events = list(
        session.scalars(
            select(HospitalReconciliationCaseEvent)
            .where(HospitalReconciliationCaseEvent.case_id == row.id)
            .order_by(HospitalReconciliationCaseEvent.occurred_at,
                      HospitalReconciliationCaseEvent.created_at)
        ).all()
    )
    return ReconciliationCaseDetailOut(
        **_case_out(row).model_dump(),
        events=[
            ReconciliationEventOut(
                id=event.id,
                case_id=event.case_id,
                from_status=event.from_status,
                to_status=event.to_status,
                actor_label=event.actor_label,
                note=event.note,
                occurred_at=event.occurred_at,
            )
            for event in events
        ],
    )


@router.post(
    "/facilities/{facility_id}/reconciliation-cases",
    response_model=ReconciliationCaseDetailOut,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
def create_reconciliation_case(
    facility_id: uuid.UUID,
    body: ManualCaseIn,
    session: Annotated[Session, Depends(get_db_session)],
) -> ReconciliationCaseDetailOut:
    _facility(session, facility_id)
    from hospital_extractor.persistence.models_integration import (
        HospitalReconciliationCase,
        HospitalReconciliationCaseEvent,
    )

    provenance: dict[str, Any] = {"created_via": "api_manual"}
    if body.verification_result_id is not None:
        result = session.get(HospitalVerificationResult, body.verification_result_id)
        if result is None or result.facility_id != facility_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "verification_result_not_found",
                    "message": "No verification result with that id for this facility.",
                },
            )
        provenance["verification_result_id"] = str(result.id)
    case = HospitalReconciliationCase(
        facility_id=facility_id,
        verification_result_id=body.verification_result_id,
        status=ReconciliationCaseStatus.OPEN.value,
        summary=body.summary.strip(),
        notes=body.notes.strip() if body.notes else None,
        opened_at=datetime.now(UTC),
        provenance_json=provenance,
    )
    session.add(case)
    session.flush()
    session.add(
        HospitalReconciliationCaseEvent(
            case_id=case.id,
            facility_id=facility_id,
            from_status=None,
            to_status=ReconciliationCaseStatus.OPEN.value,
            actor_label="operator",
            note="Case created manually.",
            occurred_at=datetime.now(UTC),
        )
    )
    session.flush()
    return get_reconciliation_case(facility_id, case.id, session)


@router.post(
    "/facilities/{facility_id}/reconciliation-cases/{case_id}/transitions",
    response_model=ReconciliationCaseDetailOut,
    dependencies=[Depends(require_operator)],
)
def transition_reconciliation_case(
    facility_id: uuid.UUID,
    case_id: uuid.UUID,
    body: TransitionIn,
    session: Annotated[Session, Depends(get_db_session)],
) -> ReconciliationCaseDetailOut:
    _facility(session, facility_id)
    case = ReconciliationService(session).get_case_for_facility(case_id, facility_id)
    if case is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "reconciliation_case_not_found",
                "message": f"No reconciliation case {case_id} for facility {facility_id}.",
            },
        )
    try:
        ReconciliationService(session).transition(
            case,
            to_status=body.to_status,
            actor_label=(body.actor_label or "operator").strip() or "operator",
            note=body.note,
            resolution=body.resolution,
        )
    except ReconciliationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_transition", "message": str(exc)},
        ) from exc
    return get_reconciliation_case(facility_id, case.id, session)
