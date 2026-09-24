"""Verification orchestration service.

Pieces:

- :func:`schedule_job_for_capture` — the capture hook: an authoritative
  capture that detects changes schedules a verification job against the
  target source; ``initial_snapshot`` and ``no_change`` captures never do.
- :func:`create_manual_job` — the testing/demo hook (API POST, ``verify``).
- :func:`run_worker_cycle` — one pass of the background worker: claims due
  jobs, captures the target source (FULL_DATASET), evaluates ONLY the
  triggering changes, stores a verification snapshot + result, and completes
  or re-queues the job.

The worker is never invoked by request handling; normal user reads (profiles,
search, comparisons, history) touch the database only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from hospital_extractor.models.change import ChangeSetStatus
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.snapshot import SnapshotCapture
from hospital_extractor.models.verification import (
    VerificationJob,
    VerificationProvenance,
    VerificationResult,
)
from hospital_extractor.sources.base import SourceFetchError
from hospital_extractor.sources.models import SourceType
from hospital_extractor.sources.pipeline import HarvestError, harvest
from hospital_extractor.sources.registry import SourceRegistry, get_default_registry
from hospital_extractor.storage.database import HospitalDatabase
from hospital_extractor.verification.evaluate import evaluate_change_set
from hospital_extractor.verification.scheduler import VerificationScheduler
from hospital_extractor.verification.settings import (
    VerificationSettings,
    verification_settings,
)

__all__ = [
    "CaptureFn",
    "VerificationPermanentError",
    "VerificationRetryableError",
    "build_target_ref",
    "create_manual_job",
    "run_worker_cycle",
    "run_worker_forever",
    "schedule_job_for_capture",
]

CaptureFn = Callable[[VerificationJob], Awaitable[HospitalDataset]]


class VerificationPermanentError(RuntimeError):
    """The job can never succeed (configuration/authorization). No retries."""


class VerificationRetryableError(RuntimeError):
    """Temporary failure — retryable up to the bounded attempt limit."""


def _now() -> datetime:
    return datetime.now(UTC)


def build_target_ref(dataset: HospitalDataset) -> str:
    """The reference to verify a hospital against (a website URL when known).

    Prefers the hospital's own ``website`` link, then the page it was captured
    from; falls back to the source's hospital ref when nothing better exists.
    """
    hospital = dataset.hospital
    if hospital is not None:
        if hospital.website:
            return hospital.website
        if hospital.source_url:
            return hospital.source_url
    if dataset.source is not None and dataset.source.hospital_ref:
        return dataset.source.hospital_ref
    return ""


def schedule_job_for_capture(
    db: HospitalDatabase,
    capture: SnapshotCapture,
    *,
    settings: VerificationSettings | None = None,
    now: datetime | None = None,
) -> VerificationJob | None:
    """The authoritative-capture hook: schedule verification on real changes.

    Returns ``None`` for ``initial_snapshot`` / ``no_change`` captures and for
    captures whose source IS the verification target (a website capture never
    schedules a job to verify itself).  Otherwise returns the (possibly
    pre-existing, idempotent) job.
    """
    settings = settings or verification_settings()
    if capture.change_set.status is not ChangeSetStatus.CHANGES_DETECTED:
        return None
    if capture.snapshot.source_type == settings.target_source_type:
        return None
    scheduler = VerificationScheduler(db, settings=settings)
    return scheduler.schedule(
        hospital_id=capture.snapshot.hospital_id,
        change_set_id=capture.change_set.change_set_id,
        triggering_snapshot_id=capture.snapshot.snapshot_id,
        triggering_source_type=capture.snapshot.source_type,
        target_source_type=settings.target_source_type,
        target_ref=build_target_ref(capture.snapshot.dataset),
        now=now,
    )


def create_manual_job(
    db: HospitalDatabase,
    hospital_id: str,
    *,
    target_source_type: str = "website",
    target_ref: str | None = None,
    settings: VerificationSettings | None = None,
    now: datetime | None = None,
) -> tuple[VerificationJob | None, str]:
    """Create a verification job manually (testing/demo), idempotently.

    Targets the most recent ``changes_detected`` change set for the hospital.
    Returns ``(None, reason)`` when there is nothing to verify.
    """
    settings = settings or verification_settings()
    change_sets = db.list_change_sets(hospital_id)
    eligible = [
        change_set
        for change_set in change_sets
        if change_set.status is ChangeSetStatus.CHANGES_DETECTED and change_set.changes
    ]
    if not eligible:
        return None, "no change sets to verify — nothing has changed since the last capture"

    change_set = eligible[0]
    snapshot = db.get_snapshot(change_set.snapshot_id)
    fallback_ref = build_target_ref(snapshot.dataset) if snapshot is not None else ""
    scheduler = VerificationScheduler(db, settings=settings)
    job = scheduler.schedule(
        hospital_id=change_set.hospital_id,
        change_set_id=change_set.change_set_id,
        triggering_snapshot_id=change_set.snapshot_id,
        triggering_source_type=change_set.source_type,
        target_source_type=target_source_type,
        target_ref=(target_ref if target_ref is not None else fallback_ref),
        now=now,
    )
    return job, ""


def _registry_capture(registry: SourceRegistry) -> CaptureFn:
    """Default full-dataset capture through the selected source adapter."""

    async def capture(job: VerificationJob) -> HospitalDataset:
        if not job.target_ref:
            raise VerificationPermanentError(
                "no target reference to verify against; provide one or capture "
                f"a dataset whose hospital has a website/source_url (job {job.verification_job_id})"
            )
        harvested = await harvest(
            [job.target_ref],
            source_type=job.target_source_type,
            registry=registry,
        )
        return harvested[0].dataset

    return capture


async def _execute_job(
    db: HospitalDatabase,
    scheduler: VerificationScheduler,
    job: VerificationJob,
    *,
    registry: SourceRegistry,
    capture: CaptureFn | None,
    now: datetime,
) -> None:
    """One claimed job: capture target, snapshot it, evaluate, store result."""
    source_type = job.target_source_type
    if not registry.has(source_type):
        raise VerificationPermanentError(f"no source adapter registered for {source_type!r}")

    definition = registry.definition(SourceType(source_type))
    if definition.requires_authorization:
        raise VerificationPermanentError(
            f"target source {source_type!r} requires an authorized integration "
            "that is not available yet (P19-D). The job was NOT executed and no "
            "private/authorized endpoint was accessed."
        )

    fetcher = capture or _registry_capture(registry)
    try:
        dataset = await fetcher(job)
    except VerificationPermanentError:
        raise
    except VerificationRetryableError:
        raise
    except (HarvestError, SourceFetchError) as exc:
        raise VerificationRetryableError(
            f"target source capture failed (attempt {job.attempt_count}): {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise VerificationRetryableError(
            f"target source capture raised (attempt {job.attempt_count}): {exc}"
        ) from exc

    if dataset.source is None or not dataset.source.source_type:
        raise VerificationPermanentError(
            "target source returned a dataset without source provenance"
        )

    verification_snapshot = db.save_snapshot(
        dataset,
        hospital_id=job.hospital_id,
        captured_at=now,
    )
    snapshot = verification_snapshot.snapshot

    change_set = db.get_change_set(job.change_set_id)
    if change_set is None:
        raise VerificationPermanentError(
            f"change set {job.change_set_id} no longer exists; cannot verify"
        )

    change_results, status, explanation = evaluate_change_set(change_set, dataset)
    result = VerificationResult(
        verification_result_id=f"r_{uuid4().hex[:12]}",
        job_id=job.verification_job_id,
        hospital_id=job.hospital_id,
        change_set_id=job.change_set_id,
        status=status,
        change_results=change_results,
        verification_snapshot_id=snapshot.snapshot_id,
        checked_at=now,
        provenance=VerificationProvenance(
            triggering_source_type=job.triggering_source_type,
            target_source_type=job.target_source_type,
            target_provider=(
                dataset.source.provider if dataset.source is not None else None
            ),
            target_source_id=(
                dataset.source.source_id if dataset.source is not None else ""
            ),
            target_ref=job.target_ref,
        ),
        explanation=explanation,
        created_at=now,
    )
    db.save_verification_result(result)
    scheduler.complete(
        job.verification_job_id,
        status=status,
        completed_at=now,
    )
    _create_reconciliation_cases(db, result, now=now)


def _create_reconciliation_cases(
    db: HospitalDatabase,
    result: VerificationResult,
    *,
    now: datetime,
) -> None:
    """P19-C hook: a MISMATCH automatically opens a reconciliation case.

    UNVERIFIED only opens a case when the opt-in policy is enabled (evidence
    is insufficient, not a disagreement); VERIFIED/FAILED never do.  The
    operation is idempotent per verification result.
    """
    from hospital_extractor.reconciliation.service import create_cases_for_result
    from hospital_extractor.reconciliation.settings import reconciliation_settings

    create_cases_for_result(
        db,
        result,
        settings=reconciliation_settings(),
        actor="system",
        now=now,
    )


async def run_worker_cycle(
    db: HospitalDatabase,
    *,
    settings: VerificationSettings | None = None,
    registry: SourceRegistry | None = None,
    capture: CaptureFn | None = None,
    now: datetime | None = None,
) -> int:
    """Process every due job once; returns the number of jobs processed.

    - Claim atomically (PENDING -> RUNNING, attempt_count +1).
    - Execute the verification capture and evaluation.
    - On retryable failure: RE-queue with backoff until ``max_attempts``, then
      FAILED.  On permanent/configuration failure: FAILED immediately.
    """
    settings = settings or verification_settings()
    registry = registry or get_default_registry()
    timestamp = now or _now()
    scheduler = VerificationScheduler(db, settings=settings)
    processed = 0
    for job in scheduler.get_due_jobs(timestamp):
        claimed = scheduler.claim(job, timestamp)
        if claimed is None:
            continue  # another worker got it
        try:
            await _execute_job(
                db, scheduler, claimed, registry=registry, capture=capture, now=timestamp
            )
        except VerificationPermanentError as exc:
            scheduler.fail(
                claimed.verification_job_id, error=str(exc), retryable=False, now=timestamp
            )
        except Exception as exc:  # noqa: BLE001
            scheduler.fail(
                claimed.verification_job_id, error=str(exc), retryable=True, now=timestamp
            )
        processed += 1
    return processed


async def run_worker_forever(
    db: HospitalDatabase,
    *,
    settings: VerificationSettings | None = None,
    registry: SourceRegistry | None = None,
    capture: CaptureFn | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Continuous worker loop (the CLI ``worker`` command)."""
    settings = settings or verification_settings()
    interval = settings.worker_interval_seconds
    while True:
        await run_worker_cycle(db, settings=settings, registry=registry, capture=capture)
        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except TimeoutError:
                continue
        await asyncio.sleep(interval)