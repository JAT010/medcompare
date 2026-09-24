"""Hospital profile store API: persistence front-end for the SQLite store.

Provides JSON endpoints to list/read/import/delete canonical datasets and
HTML pages (with source badges) to browse stored hospital profiles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from hospital_extractor.auth.audit import AuditOutcome, record_audit
from hospital_extractor.auth.dependencies import (
    AuthDep,
    PrincipalDep,
    get_request_correlation,
    require_hospital_access,
    require_permission,
)
from hospital_extractor.auth.models import Permission, Principal
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.sources import SourceType, get_default_registry
from hospital_extractor.storage import DatasetSummary, HospitalDatabase
from hospital_extractor.storage.database import default_database_path

__all__ = ["profile_router"]

profile_router = APIRouter()


class ImportResponse(BaseModel):
    dataset_id: str


class DeleteResponse(BaseModel):
    deleted: bool


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@profile_router.get("/api/v1/hospitals")
async def api_list_hospitals(
    request: Request,
    hospital_key: str | None = Query(default=None),
) -> dict[str, list[DatasetSummary]]:
    """List persisted datasets with provenance summaries.

    ``?hospital_key=`` narrows the listing to one hospital across all of its
    sources (website / demo API / imported feed).
    """
    with _open_db(request) as db:
        if hospital_key:
            return {"datasets": db.list_hospital_sources(hospital_key)}
        return {"datasets": db.list_datasets()}


@profile_router.get("/api/v1/hospitals/registry")
async def api_hospital_registry(request: Request) -> dict[str, list[dict[str, Any]]]:
    """List the canonical hospital identities in the registry."""
    from hospital_extractor.storage.database import HospitalSummary

    with _open_db(request) as db:
        hospitals = db.list_hospitals()
    serialized = [HospitalSummary.model_validate(h).model_dump(mode="json") for h in hospitals]
    return {"hospitals": serialized}


@profile_router.get("/api/v1/hospitals/{hospital_id}/snapshots")
async def api_hospital_snapshots(
    hospital_id: str,
    request: Request,
    source_type: str | None = Query(default=None),
) -> JSONResponse:
    """Snapshot history for one hospital (all sources, or filtered)."""
    with _open_db(request) as db:
        resolved = _resolve_hospital_id(db, hospital_id)
        if resolved is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Hospital not found", "detail": hospital_id},
            )
        snapshots = db.list_snapshots(resolved, source_type=source_type)
        changes = db.list_change_sets(resolved, source_type=source_type)
    return JSONResponse(
        content={
            "hospital_id": resolved,
            "snapshots": [s.model_dump(mode="json") for s in snapshots],
            "change_sets": [c.model_dump(mode="json") for c in changes],
        }
    )


@profile_router.get("/api/v1/hospitals/{hospital_id}/changes")
async def api_hospital_changes(
    hospital_id: str,
    request: Request,
    source_type: str | None = Query(default=None),
) -> JSONResponse:
    """Audit trail of detected changes for one hospital."""
    with _open_db(request) as db:
        resolved = _resolve_hospital_id(db, hospital_id)
        if resolved is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Hospital not found", "detail": hospital_id},
            )
        change_sets = db.list_change_sets(resolved, source_type=source_type)
    return JSONResponse(
        content={
            "hospital_id": resolved,
            "change_sets": [
                {
                    **c.model_dump(mode="json"),
                    "added": c.added,
                    "removed": c.removed,
                    "modified": c.modified,
                    "total": c.total,
                }
                for c in change_sets
            ],
        }
    )


@profile_router.get("/api/v1/snapshots/{snapshot_id}")
async def api_get_snapshot(snapshot_id: str, request: Request) -> JSONResponse:
    """Return one stored snapshot at full fidelity."""
    with _open_db(request) as db:
        snapshot = db.get_snapshot(snapshot_id)
    if snapshot is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Snapshot not found", "detail": snapshot_id},
        )
    return JSONResponse(content={"snapshot": snapshot.model_dump(mode="json")})


@profile_router.post("/api/v1/hospitals/{hospital_id}/capture")
async def api_capture_snapshot(
    dataset: HospitalDataset,
    request: Request,
    hospital_id: str,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[Principal, Depends(require_permission(Permission.CAPTURE_SOURCE))],
) -> JSONResponse:
    """Explicitly capture a dataset as a new snapshot, detecting changes.

    P19-A: captures only happen through an explicit call — nothing schedules
    this automatically.  When an authoritative capture detects real changes,
    P19-B schedules a verification job against the target source (the worker —
    never this endpoint — performs the verification capture).

    P24: requires CAPTURE_SOURCE + hospital grant; writes an audit event.
    """
    require_hospital_access(
        principal,
        service,
        hospital_id,
        correlation_id=get_request_correlation(request),
    )
    with _open_db(request) as db:
        capture = db.save_snapshot(dataset)
        if capture.change_set.status.value == "no_change":
            status_code = 200
        else:
            status_code = 201
        verification_job = None
        if capture.change_set.status.value == "changes_detected":
            from hospital_extractor.verification.service import schedule_job_for_capture

            likely = schedule_job_for_capture(db, capture)
            if likely is not None:
                verification_job = likely.model_dump(mode="json")
    with service.open_store() as store:
        record_audit(
            store,
            action="hospital.capture",
            outcome=AuditOutcome.SUCCESS,
            principal=principal,
            resource_type="snapshot",
            resource_id=capture.snapshot.snapshot_id,
            hospital_id=hospital_id,
            correlation_id=get_request_correlation(request),
            metadata={
                "change_status": capture.change_set.status.value,
                "source_type": capture.snapshot.source_type,
            },
        )
    return JSONResponse(
        status_code=status_code,
        content={
            "snapshot": capture.snapshot.model_dump(mode="json"),
            "change_set": {
                **capture.change_set.model_dump(mode="json"),
                "added": capture.change_set.added,
                "removed": capture.change_set.removed,
                "modified": capture.change_set.modified,
                "total": capture.change_set.total,
            },
            "verification_job": verification_job,
        },
    )


@profile_router.get("/api/v1/hospitals/{dataset_id}")
async def api_get_hospital(dataset_id: str, request: Request) -> JSONResponse:
    """Return the canonical dataset for one stored hospital."""
    with _open_db(request) as db:
        dataset = db.load_dataset(dataset_id)
    if dataset is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Hospital not found", "detail": dataset_id},
        )
    return JSONResponse(content={"dataset": dataset.model_dump(mode="json")})


@profile_router.post("/api/v1/hospitals/import")
async def api_import_hospital(
    dataset: HospitalDataset,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[Principal, Depends(require_permission(Permission.CAPTURE_SOURCE))],
) -> ImportResponse:
    """Register a canonical dataset (e.g. produced by a source adapter)."""
    with _open_db(request) as db:
        dataset_id = db.save_dataset(dataset)
    with service.open_store() as store:
        record_audit(
            store,
            action="hospital.import",
            principal=principal,
            resource_type="dataset",
            resource_id=dataset_id,
            correlation_id=get_request_correlation(request),
        )
    return ImportResponse(dataset_id=dataset_id)


@profile_router.delete("/api/v1/hospitals/{dataset_id}")
async def api_delete_hospital(
    dataset_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[Principal, Depends(require_permission(Permission.CAPTURE_SOURCE))],
) -> DeleteResponse:
    """Remove a stored hospital dataset."""
    with _open_db(request) as db:
        deleted = db.delete_dataset(dataset_id)
    with service.open_store() as store:
        record_audit(
            store,
            action="hospital.delete",
            principal=principal,
            resource_type="dataset",
            resource_id=dataset_id,
            outcome=AuditOutcome.SUCCESS if deleted else AuditOutcome.FAILURE,
            correlation_id=get_request_correlation(request),
        )
    return DeleteResponse(deleted=deleted)


# ---------------------------------------------------------------------------
# HTML / browsing frontend
# ---------------------------------------------------------------------------


@profile_router.get("/hospitals", response_class=HTMLResponse)
async def hospitals_page(request: Request) -> HTMLResponse:
    """Render the stored-hospital gallery."""
    with _open_db(request) as db:
        datasets = db.list_datasets()
    templates = request.app.state.templates
    return HTMLResponse(
        content=templates.env.get_template("hospitals.html").render(
            request=request, datasets=datasets
        )
    )


@profile_router.get("/hospitals/{dataset_id}", response_class=HTMLResponse)
async def hospital_profile_page(dataset_id: str, request: Request) -> HTMLResponse:
    """Render a single hospital profile with source provenance."""
    with _open_db(request) as db:
        dataset = db.load_dataset(dataset_id)
        available_sources: list[DatasetSummary] = []
        hospital_key = ""
        hospital_id: str | None = None
        history: dict[str, object] = {}
        verification_view: dict[str, object] = {"present": False, "jobs": []}
        monitoring_view: list[dict[str, object]] = []
        reconciliation_view: list[dict[str, object]] = []
        connector_view: dict[str, object] = {}
        quality_panel: dict[str, object] | None = None
        if dataset is not None:
            hospital_key = db.hospital_key_for(dataset)
            summaries = db.list_hospital_sources(hospital_key)
            available_sources = [
                summary for summary in summaries if summary.dataset_id != dataset_id
            ]
            stored_hospital_id = next(
                (
                    summary.hospital_id
                    for summary in summaries
                    if summary.dataset_id == dataset_id and summary.hospital_id
                ),
                "",
            )
            hospital_id = stored_hospital_id or (
                db.find_hospital_id(
                    preferred=_dataset_ref(dataset),
                    source_ref=_dataset_ref(dataset),
                    hospital_name=(
                        dataset.hospital.name if dataset.hospital is not None else ""
                    ),
                )
                or ""
            )
            snapshots = db.list_snapshots(hospital_id)
            change_sets = db.list_change_sets(hospital_id)
            verification: list[dict[str, object]] = []
            for job in db.list_verification_jobs(hospital_id):
                result = db.get_verification_result(job.verification_job_id)
                verification.append(
                    {
                        "verification_job_id": job.verification_job_id,
                        "status": job.status.value,
                        "target_source_type": job.target_source_type,
                        "target_ref": job.target_ref or "",
                        "scheduled_for": job.scheduled_for.isoformat(),
                        "completed_at": (
                            job.completed_at.isoformat() if job.completed_at else None
                        ),
                        "attempt_count": job.attempt_count,
                        "last_error": job.last_error or "",
                        "change_set_id": job.change_set_id,
                        "result_status": result.status.value if result is not None else None,
                        "checked_at": (
                            result.checked_at.isoformat() if result is not None else None
                        ),
                        "explanation": (
                            result.explanation if result is not None and result.explanation else ""
                        ),
                        "verified": result.verified_count if result is not None else 0,
                        "mismatch": result.mismatch_count if result is not None else 0,
                        "unverified": result.unverified_count if result is not None else 0,
                    }
                )
            latest_snapshots: list[dict[str, object]] = []
            seen_sources: set[str] = set()
            for snapshot in snapshots:
                if snapshot.source_type in seen_sources:
                    continue
                seen_sources.add(snapshot.source_type)
                latest_snapshots.append(
                    {
                        "source_type": snapshot.source_type,
                        "captured_at": snapshot.captured_at.isoformat(),
                        "status": snapshot.status,
                        "snapshot_id": snapshot.snapshot_id,
                    }
                )
            history = {
                "snapshot_count": len(snapshots),
                "change_set_count": len(change_sets),
                "latest_snapshots": latest_snapshots,
                "recent_changes": [
                    {
                        "change_set_id": c.change_set_id,
                        "status": c.status.value,
                        "detected_at": c.detected_at.isoformat(),
                        "added": c.added,
                        "removed": c.removed,
                        "modified": c.modified,
                        "snapshot_id": c.snapshot_id,
                    }
                    for c in change_sets[:5]
                ],
            }
            verification_view = {
                "present": bool(verification),
                "jobs": verification,
            }
            from hospital_extractor.monitoring.service import list_policies

            monitoring_view = [
                {
                    "policy_id": p.policy_id,
                    "status": p.status.value,
                    "frequency": p.frequency.value,
                    "enabled": p.enabled,
                    "source_type": p.source_type,
                    "target_ref": p.target_ref,
                    "next_run_at": p.next_run_at.isoformat() if p.next_run_at else None,
                    "last_success_at": (
                        p.last_success_at.isoformat() if p.last_success_at else None
                    ),
                    "last_failure_at": (
                        p.last_failure_at.isoformat() if p.last_failure_at else None
                    ),
                    "consecutive_failures": p.consecutive_failures,
                    "last_error": p.last_error or "",
                    "last_orchestration_run_id": p.last_orchestration_run_id or "",
                }
                for p in list_policies(db, hospital_id=hospital_id)
            ]
            from hospital_extractor.reconciliation.service import list_cases

            reconciliation_cases = list_cases(db, hospital_id=hospital_id)
            reconciliation_view = [
                {
                    "reconciliation_case_id": case.reconciliation_case_id,
                    "status": case.status.value,
                    "trigger": case.trigger.value,
                    "assigned_to": case.assigned_to or "",
                    "created_at": case.created_at.isoformat(),
                    "updated_at": case.updated_at.isoformat(),
                    "item_count": case.item_count,
                    "resolution": case.resolution.value if case.resolution else "",
                    "resolution_reason": case.resolution_reason or "",
                    "events": len(db.list_reconciliation_events(case.reconciliation_case_id)),
                }
                for case in reconciliation_cases
            ]
            # Connector / authorization view for this dataset's source (P21).
            source_meta = dataset.source
            source_type_value = (
                source_meta.source_type.value if source_meta is not None else ""
            )
            from hospital_extractor.sources.registry import get_default_registry

            registry = get_default_registry()
            simulated = False
            requires_auth = False
            connector_name = ""
            if source_type_value and registry.has(source_type_value):
                definition = registry.definition(source_type_value)
                simulated = definition.simulated
                requires_auth = definition.requires_authorization
                try:
                    connector_name = registry.connector_for(source_type_value).__name__
                except KeyError:
                    connector_name = ""
            if simulated:
                auth_status = "SIMULATED / MOCK"
            elif requires_auth:
                # Dataset was already acquired — report as authorized capture.
                auth_status = "AUTHORIZED"
            else:
                auth_status = "PUBLIC"
            connector_view = {
                "source_type": source_type_value,
                "provider": source_meta.provider if source_meta else "",
                "source_ref": source_meta.hospital_ref if source_meta else "",
                "connector": connector_name,
                "simulated": simulated,
                "authorization_status": auth_status,
                "last_acquisition": (
                    source_meta.retrieved_at.isoformat() if source_meta else ""
                ),
                "endpoint": source_meta.endpoint if source_meta else "",
                "notes": source_meta.notes if source_meta and source_meta.notes else "",
            }
            if hospital_id:
                from hospital_extractor.api.quality import quality_panel_payload

                quality_panel = quality_panel_payload(
                    db, hospital_id, dataset_id=dataset_id
                )
    if dataset is None:
        return HTMLResponse(
            content=f'<div class="error">Hospital dataset "{dataset_id}" not found.</div>'
        )
    profile = _profile_view(dataset)
    profile["hospital_id"] = hospital_id or ""
    profile["history"] = history
    profile["verification"] = verification_view
    profile["reconciliation"] = reconciliation_view
    profile["monitoring"] = monitoring_view
    profile["connector"] = connector_view
    profile["quality_panel"] = quality_panel
    templates = request.app.state.templates
    return HTMLResponse(
        content=templates.env.get_template("hospital_profile.html").render(
            request=request,
            dataset_id=dataset_id,
            hospital_key=hospital_key,
            profile=profile,
            available_sources=available_sources,
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_db(request: Request) -> HospitalDatabase:
    configured: Path | str = getattr(
        request.app.state, "medcompare_db_path", default_database_path()
    )
    return HospitalDatabase(configured)


def _resolve_hospital_id(db: HospitalDatabase, reference: str) -> str | None:
    """Map a user reference to a canonical hospital id (read-only).

    ``reference`` may be an opaque ``h_…`` id, a per-source hospital ref or a
    hospital name.  Never mints — unknown references simply don't resolve.
    """
    return db.find_hospital_id(
        preferred=reference,
        source_ref=reference,
        hospital_name=reference,
    )


def _source_label(value: str) -> str:
    """Human-readable label for a source type value (e.g. ``mock_api`` → Demo API)."""
    try:
        return SourceType(value).label
    except ValueError:
        return value


def _is_simulated(value: str) -> bool:
    """Whether a source serves simulated data — asked of the registry, never hard-coded."""
    try:
        return get_default_registry().definition(SourceType(value)).simulated
    except (KeyError, ValueError):
        return False


def _dataset_ref(dataset: HospitalDataset) -> str:
    """The per-source hospital reference a dataset was captured under."""
    if dataset.source is not None and dataset.source.hospital_ref:
        return dataset.source.hospital_ref
    if dataset.hospital is not None and dataset.hospital.source_url:
        return dataset.hospital.source_url
    return ""


def _profile_view(dataset: HospitalDataset) -> dict[str, object]:
    """Derive a UI-friendly profile from the canonical dataset."""
    hospital = dataset.hospital
    source = dataset.source

    procedures = [getattr(p, "name", "") for p in getattr(dataset, "procedures", [])]
    specialities = [getattr(s, "name", "") for s in getattr(dataset, "specialities", [])]
    infrastructure = {}
    if hospital is not None and isinstance(hospital.bed_count, int) and hospital.bed_count > 0:
        infrastructure["total_beds"] = hospital.bed_count
    accreditations = [getattr(a, "name", "") for a in getattr(dataset, "accreditations", [])]
    quality = None
    if dataset.quality is not None:
        quality = {
            "records_extracted": dataset.quality.records_extracted,
            "records_rejected": dataset.quality.records_rejected,
            "pages_with_information": dataset.quality.pages_with_information,
            "pages_crawled": dataset.quality.pages_crawled,
            "conflicts_total": dataset.quality.conflicts_detected,
            "conflicts_unresolved": dataset.quality.conflicts_unresolved,
            "warning_count": len(dataset.quality.warnings or []),
            "warnings": list(dataset.quality.warnings or []),
            "categories_empty": list(dataset.quality.categories_empty or []),
        }

    return {
        "name": hospital.name if hospital is not None else "",
        "source_type": source.source_type.value if source is not None else "website",
        "source_type_label": _source_label(
            source.source_type.value if source is not None else "website"
        ),
        "is_simulated": _is_simulated(
            source.source_type.value if source is not None else "website"
        ),
        "provider": source.provider if source is not None else None,
        "base_url": source.base_url if source is not None else None,
        "hospital_ref": source.hospital_ref if source is not None else None,
        "retrieved_at": (source.retrieved_at.isoformat() if source is not None else None),
        "last_updated": (
            (source.raw_payload_metadata or {}).get("last_updated") if source is not None else None
        ),
        "overview": {
            "type": getattr(hospital, "hospital_type", "") or "Unknown",
            "description": getattr(hospital, "description", "") or "",
            "parent_organization": getattr(hospital, "parent_organization", "") or "",
            "established_year": getattr(hospital, "established_year", None),
            "status": getattr(hospital, "status", "") or "",
            "bed_count": getattr(hospital, "bed_count", None),
            "staff_count": getattr(hospital, "staff_count", None),
            "doctor_count": getattr(hospital, "doctor_count", None),
            "speciality_count": getattr(hospital, "speciality_count", None),
            "department_count": getattr(hospital, "department_count", None),
            "website": getattr(hospital, "website", "") or "",
        },
        "procedures": procedures,
        "specialities": specialities,
        "infrastructure": infrastructure,
        "accreditations": accreditations,
        "doctors": len(getattr(dataset, "doctors", [])),
        "departments": len(getattr(dataset, "departments", [])),
        "quality": quality,
    }