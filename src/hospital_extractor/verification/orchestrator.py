"""Capture orchestration: snapshot a source and enqueue verification on change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from hospital_extractor.jobs.queue import EnqueueResult, enqueue_job
from hospital_extractor.persistence.models_integration import (
    ChangeSetStatus,
    HospitalSourceIntegration,
    HospitalVerificationJob,
)
from hospital_extractor.snapshots.service import CaptureResult, SnapshotService
from hospital_extractor.sources.base import HospitalSourceConnector
from hospital_extractor.sources.registry import build_connector, source_kind_for

__all__ = ["CaptureAndEnqueueOutcome", "capture_and_enqueue"]


@dataclass(frozen=True)
class CaptureAndEnqueueOutcome:
    capture: CaptureResult
    verification_job: HospitalVerificationJob | None
    job_created: bool
    job_skip_reason: str | None


async def capture_and_enqueue(
    session: Session,
    integration: HospitalSourceIntegration,
    *,
    connector: HospitalSourceConnector | None = None,
    target_ref: str | None,
    trigger_type: str = "change_detected",
    captured_by: str = "system",
    max_attempts: int = 3,
) -> CaptureAndEnqueueOutcome:
    """Capture a snapshot and, on detected changes, enqueue verification.

    Idempotent: re-running against the same change set returns the existing
    verification job (``job_created=False``).
    """
    resolved_connector = connector if connector is not None else build_connector(integration)
    source_kind = source_kind_for(integration)
    capture = await SnapshotService(session).capture(
        integration,
        connector=resolved_connector,
        source_kind=source_kind,
        captured_by=captured_by,
    )

    if capture.change_set.status != ChangeSetStatus.CHANGES_DETECTED.value:
        return CaptureAndEnqueueOutcome(
            capture=capture,
            verification_job=None,
            job_created=False,
            job_skip_reason=(
                "no detected changes"
                if capture.change_set.status == ChangeSetStatus.NO_CHANGE.value
                else "initial_snapshot"
            ),
        )
    if not target_ref:
        return CaptureAndEnqueueOutcome(
            capture=capture,
            verification_job=None,
            job_created=False,
            job_skip_reason="no website target_ref configured for the facility",
        )

    input_json: dict[str, Any] = {
        "change_set_id": str(capture.change_set.id),
        "snapshot_id": str(capture.snapshot.id),
        "content_hash": capture.snapshot.content_hash,
        "change_paths": [change.path for change in capture.changes],
    }
    enqueued: EnqueueResult = enqueue_job(
        session,
        facility_id=integration.facility_id,
        integration_id=integration.id,
        change_set_id=capture.change_set.id,
        snapshot_id=capture.snapshot.id,
        target_ref=target_ref,
        trigger_type=trigger_type,
        idempotency_key=f"{trigger_type}:{capture.change_set.id}",
        input_json=input_json,
        max_attempts=max_attempts,
    )
    return CaptureAndEnqueueOutcome(
        capture=capture,
        verification_job=enqueued.job,
        job_created=enqueued.created,
        job_skip_reason=None,
    )
