"""Reconciliation models: a human-reviewable case for an inconclusive check.

A :class:`ReconciliationCase` connects the P19-A/P19-B chain — ChangeSet →
VerificationJob → VerificationResult — to two immutable snapshots and answers
"What should MedCompare do with a MISMATCH / UNVERIFIED verdict?"  It
*references* existing data rather than duplicating it.

The state machine is deliberately small::

    OPEN -----> UNDER_REVIEW -----> RESOLVED -----> REOPENED
      |              |     ^-----------^             |
      |              +----> DISMISSED                |
      +-----> RESOLVED / DISMISSED (direct)
      +-----------------------------------------------+

The full transition table lives in :data:`ALLOWED_TRANSITIONS`; the service
layer enforces it and writes one :class:`ReconciliationEvent` per transition,
so a resolved case never changes silently.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hospital_extractor.models.change import ChangeType
from hospital_extractor.models.verification import VerificationOutcome

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ReconciliationCase",
    "ReconciliationCaseStatus",
    "ReconciliationEvent",
    "ReconciliationItem",
    "ReconciliationTrigger",
    "ResolutionType",
]


class ReconciliationCaseStatus(StrEnum):
    """Lifecycle status of one reconciliation case."""

    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    DISMISSED = "dismissed"


ALLOWED_TRANSITIONS: dict[ReconciliationCaseStatus, frozenset[ReconciliationCaseStatus]] = {
    ReconciliationCaseStatus.OPEN: frozenset(
        {
            ReconciliationCaseStatus.UNDER_REVIEW,
            ReconciliationCaseStatus.RESOLVED,
            ReconciliationCaseStatus.DISMISSED,
        }
    ),
    ReconciliationCaseStatus.UNDER_REVIEW: frozenset(
        {
            ReconciliationCaseStatus.RESOLVED,
            ReconciliationCaseStatus.DISMISSED,
        }
    ),
    ReconciliationCaseStatus.RESOLVED: frozenset({ReconciliationCaseStatus.REOPENED}),
    ReconciliationCaseStatus.REOPENED: frozenset(
        {
            ReconciliationCaseStatus.UNDER_REVIEW,
            ReconciliationCaseStatus.RESOLVED,
            ReconciliationCaseStatus.DISMISSED,
        }
    ),
    ReconciliationCaseStatus.DISMISSED: frozenset({ReconciliationCaseStatus.REOPENED}),
}


class ReconciliationTrigger(StrEnum):
    """Why a case exists."""

    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    MANUAL = "manual"


class ResolutionType(StrEnum):
    """The review decision recorded when a case is resolved.

    A resolution is a decision *about the available evidence*; it never
    asserts which source is the "truth".
    """

    AUTHORITATIVE_SOURCE_CONFIRMED = "authoritative_source_confirmed"
    WEBSITE_CONFIRMED = "website_confirmed"
    SOURCE_DISAGREEMENT = "source_disagreement"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FALSE_POSITIVE = "false_positive"


class ReconciliationItem(BaseModel):
    """One conflicting/uncertain dimension identified for review.

    Field-level: ``path`` points at the canonical dimension (e.g.
    ``procedures`` or ``hospital.bed_count``); ``entity_key`` the collection
    item involved (``""`` for single-record fields).  Values are the actual
    canonical values already captured — no dataset is duplicated.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    item_id: str
    path: str
    category: str = ""
    entity_key: str = ""
    field: str = ""
    change_type: ChangeType = ChangeType.MODIFIED
    outcome: VerificationOutcome
    previous_value: Any = None
    authoritative_value: Any = None
    verification_value: Any = None


class ReconciliationCase(BaseModel):
    """A persistent, idempotent case for one verification result.

    At most one case exists per ``verification_result_id`` (enforced by a
    unique index in storage), so reprocessing a result never duplicates a case
    and reopening never mints a new one.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    reconciliation_case_id: str
    hospital_id: str
    change_set_id: str
    verification_job_id: str
    verification_result_id: str
    status: ReconciliationCaseStatus = ReconciliationCaseStatus.OPEN
    trigger: ReconciliationTrigger = ReconciliationTrigger.MANUAL
    created_by: str = "system"
    assigned_to: str | None = None
    resolution: ResolutionType | None = None
    resolution_reason: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    items: list[ReconciliationItem] = Field(default_factory=list)

    @property
    def item_count(self) -> int:
        return len(self.items)


class ReconciliationEvent(BaseModel):
    """One audit entry: who did what to a case, why, and when."""

    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: str
    case_id: str
    actor: str = "system"
    action: str
    reason: str | None = None
    from_status: ReconciliationCaseStatus | None = None
    to_status: ReconciliationCaseStatus | None = None
    created_at: datetime