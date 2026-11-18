"""Persistent verification job queue.

Cross-dialect semantics with PostgreSQL ``FOR UPDATE SKIP LOCKED`` claiming.
``status`` lifecycle: pending -> running -> verified|mismatch|
insufficient_evidence|failed; pending -> cancelled is administrative.

Claiming is done with a conditional ``UPDATE ... WHERE id = ... AND
status = 'pending'`` after lock-selection, so a concurrent worker that claimed
first causes a no-op. On PostgreSQL the lock-selection uses
``SKIP LOCKED``; on other dialects (tests) a short select + conditional update
is used.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hospital_extractor.persistence.models_integration import (
    HospitalVerificationJob,
    VerificationJobStatus,
)

__all__ = [
    "ClaimedJob",
    "EnqueueResult",
    "claim_due_jobs",
    "enqueue_job",
    "fail_job",
    "sweep_stale_leases",
]

DEFAULT_LEASE_SECONDS = 300
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 300


@dataclass(frozen=True)
class EnqueueResult:
    job: HospitalVerificationJob
    created: bool


@dataclass(frozen=True)
class ClaimedJob:
    job: HospitalVerificationJob


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue_job(
    session: Session,
    *,
    facility_id: uuid.UUID,
    target_ref: str,
    trigger_type: str,
    idempotency_key: str,
    change_set_id: uuid.UUID | None = None,
    snapshot_id: uuid.UUID | None = None,
    integration_id: uuid.UUID | None = None,
    scheduled_for: datetime | None = None,
    input_json: dict[str, Any] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> EnqueueResult:
    """Idempotently enqueue a verification job.

    When a job with the same ``idempotency_key`` exists, return it with
    ``created=False`` instead of failing.
    """
    now = _now()
    job = HospitalVerificationJob(
        facility_id=facility_id,
        integration_id=integration_id,
        change_set_id=change_set_id,
        snapshot_id=snapshot_id,
        target_ref=target_ref,
        trigger_type=trigger_type,
        status=VerificationJobStatus.PENDING.value,
        idempotency_key=idempotency_key,
        attempt_count=0,
        max_attempts=max_attempts,
        scheduled_for=scheduled_for or now,
        input_json=input_json or {},
    )
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
        return EnqueueResult(job=job, created=True)
    except IntegrityError:
        existing = session.scalars(
            select(HospitalVerificationJob).where(
                HospitalVerificationJob.idempotency_key == idempotency_key
            )
        ).first()
        if existing is None:  # pragma: no cover - defensive
            raise
        return EnqueueResult(job=existing, created=False)


def claim_due_jobs(
    session: Session,
    *,
    worker_id: str,
    limit: int = 1,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> list[HospitalVerificationJob]:
    """Claim up to *limit* due jobs; returns the claimed jobs."""
    now = now or _now()
    dialect = session.bind.dialect.name if session.bind is not None else ""
    stmt = (
        select(HospitalVerificationJob)
        .where(
            HospitalVerificationJob.status == VerificationJobStatus.PENDING.value,
            HospitalVerificationJob.scheduled_for <= now,
            or_(
                HospitalVerificationJob.lease_expires_at.is_(None),
                HospitalVerificationJob.lease_expires_at <= now,
            ),
            HospitalVerificationJob.attempt_count < HospitalVerificationJob.max_attempts,
        )
        .order_by(HospitalVerificationJob.scheduled_for, HospitalVerificationJob.created_at)
        .limit(limit)
    )
    if dialect == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    candidates = list(session.scalars(stmt).all())

    claimed: list[HospitalVerificationJob] = []
    for job in candidates:
        lease_until = now + timedelta(seconds=lease_seconds)
        result = cast(
            "CursorResult[tuple[object, ...]]",
            session.execute(
                update(HospitalVerificationJob)
                .where(
                    HospitalVerificationJob.id == job.id,
                    HospitalVerificationJob.status == VerificationJobStatus.PENDING.value,
                    or_(
                        HospitalVerificationJob.lease_expires_at.is_(None),
                        HospitalVerificationJob.lease_expires_at <= now,
                    ),
                )
                .values(
                    status=VerificationJobStatus.RUNNING.value,
                    lease_owner=worker_id,
                    lease_expires_at=lease_until,
                    started_at=now,
                    attempt_count=HospitalVerificationJob.attempt_count + 1,
                )
            ),
        )
        if result.rowcount != 1:
            # Another worker claimed it first; skip.
            continue
        session.flush()
        claimed.append(job)
    return claimed


def fail_job(
    session: Session,
    job: HospitalVerificationJob,
    *,
    error_code: str,
    error_message: str,
    retryable: bool,
    backoff_seconds: int = DEFAULT_BACKOFF_SECONDS,
    now: datetime | None = None,
) -> HospitalVerificationJob:
    """Fail *job*; requeue with backoff when attempts remain, else terminate."""
    now = now or _now()
    job.last_error_code = error_code[:64]
    job.last_error_message = error_message[:4000]
    job.lease_owner = None
    job.lease_expires_at = None
    if retryable and job.attempt_count < job.max_attempts:
        job.status = VerificationJobStatus.PENDING.value
        job.scheduled_for = now + timedelta(seconds=backoff_seconds * max(job.attempt_count, 1))
    else:
        job.status = VerificationJobStatus.FAILED.value
        job.finished_at = now
    session.flush()
    return job


def sweep_stale_leases(
    session: Session,
    *,
    now: datetime | None = None,
) -> list[HospitalVerificationJob]:
    """Return running jobs whose lease expired; requeue or terminate them."""
    now = now or _now()
    stmt = select(HospitalVerificationJob).where(
        HospitalVerificationJob.status == VerificationJobStatus.RUNNING.value,
        HospitalVerificationJob.lease_expires_at.is_not(None),
        HospitalVerificationJob.lease_expires_at <= now,
    )
    swept: list[HospitalVerificationJob] = []
    for job in session.scalars(stmt).all():
        job.lease_owner = None
        job.lease_expires_at = None
        if job.attempt_count < job.max_attempts:
            job.status = VerificationJobStatus.PENDING.value
            job.last_error_code = "lease_expired"
            job.last_error_message = "Worker lease expired; job requeued."
        else:
            job.status = VerificationJobStatus.FAILED.value
            job.finished_at = now
            job.last_error_code = "lease_expired"
            job.last_error_message = "Worker lease expired and no attempts remain."
        session.flush()
        swept.append(job)
    return swept
