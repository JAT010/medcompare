"""SQLite-backed verification scheduler.

Conceptual operations (all local, no distributed queues):

- :meth:`schedule` — persist a PENDING job (idempotent per change set/target).
- :meth:`get_due_jobs` — PENDING jobs whose ``scheduled_for`` has arrived.
- :meth:`claim` — atomic PENDING -> RUNNING (safe across local workers).
- :meth:`complete` — RUNNING -> terminal verification status.
- :meth:`fail` — RUNNING -> PENDING (bounded retry w/ backoff) or FAILED.

Delays and backoff are computed from deterministic supplied timestamps so
tests and the demo never sleep.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hospital_extractor.models.verification import (
    VerificationJob,
    VerificationJobStatus,
    VerificationResultStatus,
)
from hospital_extractor.storage.database import HospitalDatabase
from hospital_extractor.verification.settings import (
    VerificationSettings,
    verification_settings,
)

__all__ = ["VerificationScheduler"]


def _now() -> datetime:
    return datetime.now(UTC)


def _job_status(
    status: VerificationJobStatus | VerificationResultStatus | str,
) -> VerificationJobStatus:
    if isinstance(status, VerificationJobStatus):
        return status
    if isinstance(status, VerificationResultStatus):
        return VerificationJobStatus(status.value)
    return VerificationJobStatus(str(status))


class VerificationScheduler:
    """Persistence and lifecycle for :class:`VerificationJob` rows."""

    def __init__(
        self,
        db: HospitalDatabase,
        *,
        settings: VerificationSettings | None = None,
    ) -> None:
        self._db = db
        self.settings = settings or verification_settings()

    def schedule(
        self,
        *,
        hospital_id: str,
        change_set_id: str,
        triggering_snapshot_id: str,
        triggering_source_type: str,
        target_source_type: str,
        target_ref: str = "",
        delay_minutes: int | None = None,
        now: datetime | None = None,
    ) -> VerificationJob:
        """Create a PENDING verification job.

        Idempotent: at most one job per ``(change_set_id, target_source_type)``.
        Reprocessing the same event returns the existing job.
        """
        timestamp = now or _now()
        job = VerificationJob(
            verification_job_id=f"v_{uuid4().hex[:12]}",
            hospital_id=hospital_id,
            change_set_id=change_set_id,
            triggering_snapshot_id=triggering_snapshot_id,
            triggering_source_type=triggering_source_type,
            target_source_type=target_source_type,
            target_ref=target_ref,
            status=VerificationJobStatus.PENDING,
            scheduled_for=timestamp
            + timedelta(
                minutes=(
                    delay_minutes
                    if delay_minutes is not None
                    else self.settings.delay_minutes
                )
            ),
            attempt_count=0,
            created_at=timestamp,
            updated_at=timestamp,
        )
        return self._db.create_verification_job(job)

    def get_due_jobs(self, now: datetime | None = None) -> list[VerificationJob]:
        """PENDING jobs schedulable at *now* (oldest first)."""
        return self._db.get_due_verification_jobs(now or _now())

    def claim(self, job: VerificationJob, now: datetime | None = None) -> VerificationJob | None:
        """Atomically move *job* to RUNNING; ``None`` if another worker claimed it."""
        timestamp = now or _now()
        if not self._db.claim_verification_job(job.verification_job_id, timestamp):
            return None
        return self._db.get_verification_job(job.verification_job_id)

    def complete(
        self,
        verification_job_id: str,
        *,
        status: VerificationJobStatus | VerificationResultStatus | str,
        completed_at: datetime | None = None,
    ) -> VerificationJob:
        """Finish a job in a terminal verification status."""
        timestamp = completed_at or _now()
        self._db.complete_verification_job(
            verification_job_id,
            status=_job_status(status),
            completed_at=timestamp,
        )
        job = self._db.get_verification_job(verification_job_id)
        assert job is not None
        return job

    def fail(
        self,
        verification_job_id: str,
        *,
        error: str,
        retryable: bool,
        now: datetime | None = None,
    ) -> VerificationJob:
        """Handle a failed attempt.

        Retryable failures with attempts remaining are re-queued to PENDING
        with exponential backoff; permanent/configuration errors and exhausted
        attempts go straight to FAILED (never retried forever).
        """
        timestamp = now or _now()
        job = self._db.get_verification_job(verification_job_id)
        if job is None:
            raise ValueError(f"unknown verification job {verification_job_id!r}")

        if not retryable or job.attempt_count >= self.settings.max_attempts:
            self._db.fail_verification_job(
                verification_job_id,
                last_error=error,
                completed_at=timestamp,
                updated_at=timestamp,
            )
            return self._db.get_verification_job(verification_job_id) or job

        backoff = self.settings.backoff_minutes * job.attempt_count
        self._db.requeue_verification_job(
            verification_job_id,
            scheduled_for=timestamp + timedelta(minutes=backoff),
            attempt_count=job.attempt_count,
            last_error=error,
            updated_at=timestamp,
        )
        return self._db.get_verification_job(verification_job_id) or job