"""Report generation service.

Generates structured, evidence-backed reports from reconciliation cases,
verification results, and change sets. Reports reference immutable snapshots
instead of duplicating their payloads.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from hospital_extractor.reconciliation.models import ReconciliationCase
from hospital_extractor.reporting.models import (
    Report,
    ReportSnapshotReference,
    ReportStatus,
    ReportType,
)

if TYPE_CHECKING:
    from hospital_extractor.storage import HospitalDatabase


__all__ = [
    "ReportGenerationError",
    "generate_report",
    "generate_reconciliation_report",
    "generate_verification_report",
    "generate_change_report",
]


class ReportGenerationError(RuntimeError):
    """Report generation failed."""


def _now() -> datetime:
    return datetime.now(UTC)


def _snapshot_reference(snapshot: Any) -> ReportSnapshotReference:
    """A payload-free reference to an immutable snapshot."""
    return ReportSnapshotReference(
        snapshot_id=snapshot.snapshot_id,
        hospital_id=snapshot.hospital_id,
        source_type=snapshot.source_type,
        provider=snapshot.provider,
        hospital_ref=snapshot.hospital_ref or "",
        captured_at=snapshot.captured_at,
        status=snapshot.status,
        content_hash=snapshot.content_hash,
    )


def _build_report_content(
    case: ReconciliationCase,
    evidence: dict[str, Any],
    report_type: ReportType,
) -> dict[str, Any]:
    """Build the structured report content from evidence package."""
    change_set = evidence.get("change_set")
    job = evidence.get("job")
    result = evidence.get("result")
    triggering_snap = evidence.get("triggering_snapshot")
    previous_snap = evidence.get("previous_snapshot")
    verification_snap = evidence.get("verification_snapshot")

    content: dict[str, Any] = {
        "report_type": report_type.value,
        "generated_at": _now().isoformat(),
        "case": case.model_dump(mode="json"),
        "items": [
            {
                "item_id": item.item_id,
                "path": item.path,
                "category": item.category,
                "entity_key": item.entity_key,
                "field": item.field,
                "change_type": item.change_type.value,
                "outcome": item.outcome.value,
                "previous_value": item.previous_value,
                "authoritative_value": item.authoritative_value,
                "verification_value": item.verification_value,
            }
            for item in case.items
        ],
        "change_set": change_set,
        "verification_job": job,
        "verification_result": result,
        "snapshots": {
            "triggering": triggering_snap,
            "previous": previous_snap,
            "verification": verification_snap,
        },
    }

    if report_type == ReportType.VERIFICATION_REPORT:
        content["focus"] = "Verification result summary with per-change outcomes"
    elif report_type == ReportType.CHANGE_REPORT:
        content["focus"] = "Change detection audit trail"
    else:
        content["focus"] = "Reconciliation case with human resolution decision"

    return content


def _extract_evidence_references(evidence: dict[str, Any]) -> dict[str, str]:
    """Extract key evidence references for quick lookup."""
    refs = {}
    case = evidence.get("case")
    if case and case.get("reconciliation_case_id"):
        refs["reconciliation_case"] = case["reconciliation_case_id"]
    change_set = evidence.get("change_set")
    if change_set and change_set.get("change_set_id"):
        refs["change_set"] = change_set["change_set_id"]
    job = evidence.get("job")
    if job and job.get("verification_job_id"):
        refs["verification_job"] = job["verification_job_id"]
    result = evidence.get("result")
    if result and result.get("verification_result_id"):
        refs["verification_result"] = result["verification_result_id"]
    return refs


def _extract_snapshot_references(evidence: dict[str, Any]) -> list[ReportSnapshotReference]:
    """Extract snapshot references from evidence package."""
    refs = []
    for key in ("triggering_snapshot", "previous_snapshot", "verification_snapshot"):
        snap = evidence.get(key)
        if snap and isinstance(snap, dict) and snap.get("snapshot_id"):
            refs.append(
                ReportSnapshotReference(
                    snapshot_id=snap["snapshot_id"],
                    hospital_id=snap.get("hospital_id", ""),
                    source_type=snap.get("source_type", ""),
                    provider=snap.get("provider"),
                    hospital_ref=snap.get("hospital_ref", ""),
                    captured_at=datetime.fromisoformat(snap["captured_at"])
                    if isinstance(snap.get("captured_at"), str)
                    else snap.get("captured_at", _now()),
                    status=snap.get("status", ""),
                    content_hash=snap.get("content_hash", ""),
                )
            )
    return refs


def generate_report(
    db: HospitalDatabase,
    case: ReconciliationCase,
    *,
    report_type: ReportType = ReportType.RECONCILIATION_REPORT,
    format: str = "json",
    generated_by: str = "system",
    now: datetime | None = None,
) -> Report:
    """Generate a report from a reconciliation case."""
    from hospital_extractor.reconciliation.service import build_evidence_package

    evidence = build_evidence_package(db, case)
    timestamp = now or _now()

    content = _build_report_content(case, evidence, report_type)
    evidence_refs = _extract_evidence_references(evidence)
    snapshot_refs = _extract_snapshot_references(evidence)

    report = Report(
        report_id=f"rpt_{uuid.uuid4().hex[:12]}",
        hospital_id=case.hospital_id,
        reconciliation_case_id=case.reconciliation_case_id,
        verification_result_id=case.verification_result_id,
        change_set_id=case.change_set_id,
        report_type=report_type,
        status=ReportStatus.GENERATED,
        format=format,
        content=content,
        generated_at=timestamp,
        generated_by=generated_by,
        snapshot_references=snapshot_refs,
        evidence_references=evidence_refs,
        created_at=timestamp,
        updated_at=timestamp,
    )
    return report


def generate_reconciliation_report(
    db: HospitalDatabase,
    case: ReconciliationCase,
    *,
    format: str = "json",
    generated_by: str = "system",
    now: datetime | None = None,
) -> Report:
    """Generate a reconciliation report from a case."""
    return generate_report(
        db,
        case,
        report_type=ReportType.RECONCILIATION_REPORT,
        format=format,
        generated_by=generated_by,
        now=now,
    )


def generate_verification_report(
    db: HospitalDatabase,
    case: ReconciliationCase,
    *,
    format: str = "json",
    generated_by: str = "system",
    now: datetime | None = None,
) -> Report:
    """Generate a verification report from a case."""
    return generate_report(
        db,
        case,
        report_type=ReportType.VERIFICATION_REPORT,
        format=format,
        generated_by=generated_by,
        now=now,
    )


def generate_change_report(
    db: HospitalDatabase,
    case: ReconciliationCase,
    *,
    format: str = "json",
    generated_by: str = "system",
    now: datetime | None = None,
) -> Report:
    """Generate a change report from a case."""
    return generate_report(
        db,
        case,
        report_type=ReportType.CHANGE_REPORT,
        format=format,
        generated_by=generated_by,
        now=now,
    )