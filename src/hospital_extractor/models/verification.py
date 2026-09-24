"""Verification models: what changed (ChangeSet) vs what must be checked.

A :class:`VerificationJob` is created when an authoritative capture detects
changes: it records *what MedCompare needs to verify because of that change*.
The job always references the ChangeSet that triggered it and the snapshot the
change was observed in, and names the target source that must be checked to
confirm or refute the change.

A :class:`VerificationResult` is the outcome of that check: for every
triggering change the website (or other target source) value is compared to
the authoritative claim and classified as VERIFIED / MISMATCH / UNVERIFIED.

.. note::
   ``ChangeSet`` and ``VerificationJob`` are deliberately separate concepts.
   A change set never mutates; verification lifecycles live entirely on jobs.

Verification job state transitions::

    PENDING -----> RUNNING -----> VERIFIED
      |   |           |           MISMATCH
      |   |           |           UNVERIFIED
      |   |           |           FAILED
      |   |           +----> PENDING   (retryable failure, backoff, bounded)
      |   +---> CANCELLED (only from PENDING/RUNNING; manual/administrative)
      +--------> CANCELLED

    - PENDING -> RUNNING   : atomic claim by a worker.
    - RUNNING -> PENDING   : temporary failure, attempt < max_attempts.
    - RUNNING -> FAILED    : permanent/configuration error, or retries spent.
    - RUNNING -> terminal  : a VerificationResult decides the job status.
    - Optional CANCELLED  : administrative stop (P19-B+; not worker-driven).

Verification result statuses are a subset of the job statuses:

    AUTHORITATIVE=YES, WEBSITE=YES      -> VERIFIED
    AUTHORITATIVE=YES, WEBSITE=UNKNOWN  -> UNVERIFIED   (UNKNOWN is never NO)
    AUTHORITATIVE=YES, WEBSITE=NO       -> MISMATCH
    anything else / no check performed  -> FAILED
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hospital_extractor.models.change import ChangeType

__all__ = [
    "ChangeVerification",
    "VerificationJob",
    "VerificationJobStatus",
    "VerificationOutcome",
    "VerificationProvenance",
    "VerificationResult",
    "VerificationResultStatus",
]


class VerificationJobStatus(StrEnum):
    """Lifecycle status of one verification job."""

    PENDING = "pending"
    RUNNING = "running"
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            VerificationJobStatus.VERIFIED,
            VerificationJobStatus.MISMATCH,
            VerificationJobStatus.UNVERIFIED,
            VerificationJobStatus.FAILED,
            VerificationJobStatus.CANCELLED,
        }


class VerificationResultStatus(StrEnum):
    """Outcome of a completed verification check."""

    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    FAILED = "failed"


class VerificationOutcome(StrEnum):
    """Per-change verdict produced by comparing the website to the claim."""

    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    NOT_EVALUATED = "not_evaluated"


class ChangeVerification(BaseModel):
    """One triggering change checked against the target source's dataset.

    ``path`` is the ChangeSet ``path`` (e.g. ``procedures`` or
    ``hospital.bed_count``); ``claimed_value`` is what the authoritative source
    now claims and ``website_value`` what the target source actually showed.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    path: str
    category: str = ""
    change_type: ChangeType
    entity_name: str | None = None
    claimed_value: Any = None
    website_value: Any = None
    outcome: VerificationOutcome
    detail: str = ""


class VerificationProvenance(BaseModel):
    """Traceability for one verification check (IDs, never duplicated data)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    triggering_source_type: str = ""
    target_source_type: str = ""
    target_provider: str | None = None
    target_source_id: str = ""
    target_ref: str = ""


class VerificationJob(BaseModel):
    """A persistent request to verify a ChangeSet against a target source.

    One ``(change_set_id, target_source_type)`` pair may have AT MOST one job
    (enforced by a unique index) — reprocessing the same event returns the
    existing job (idempotent scheduling).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    verification_job_id: str
    hospital_id: str
    change_set_id: str
    triggering_snapshot_id: str
    triggering_source_type: str
    target_source_type: str
    target_ref: str = ""
    status: VerificationJobStatus = VerificationJobStatus.PENDING
    scheduled_for: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt_count: int = 0
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class VerificationResult(BaseModel):
    """The persistent outcome of one verification job attempt/job.

    ``status`` aggregates the per-change outcomes: any MISMATCH wins, else all
    VERIFIED is required for VERIFIED, otherwise UNVERIFIED.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    verification_result_id: str
    job_id: str
    hospital_id: str
    change_set_id: str
    status: VerificationResultStatus
    change_results: list[ChangeVerification] = Field(default_factory=list)
    verification_snapshot_id: str | None = None
    checked_at: datetime
    provenance: VerificationProvenance = Field(default_factory=VerificationProvenance)
    explanation: str | None = None
    created_at: datetime

    @property
    def verified_count(self) -> int:
        return sum(
            1
            for change in self.change_results
            if change.outcome is VerificationOutcome.VERIFIED
        )

    @property
    def mismatch_count(self) -> int:
        return sum(
            1
            for change in self.change_results
            if change.outcome is VerificationOutcome.MISMATCH
        )

    @property
    def unverified_count(self) -> int:
        return sum(
            1
            for change in self.change_results
            if change.outcome is VerificationOutcome.UNVERIFIED
        )

    @property
    def not_evaluated_count(self) -> int:
        return sum(
            1
            for change in self.change_results
            if change.outcome is VerificationOutcome.NOT_EVALUATED
        )

    @property
    def change_count(self) -> int:
        return len(self.change_results)