"""Orchestration models: end-to-end run orchestration for the hospital extractor.

This module defines the models for the end-to-end orchestration run that coordinates
the P19-A through P19-D workflow:

    Hospital/source
          ↓
    Capture
          ↓
    Snapshot
          ↓
    Change Detection
          ↓
    Verification
          ↓
    Reconciliation
          ↓
    Evidence Report
          ↓
    Authorized Submission

The orchestrator composes existing services without duplicating them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "OrchestrationStage",
    "OrchestrationStatus",
    "OrchestrationRun",
    "OrchestrationEvent",
    "OrchestrationMetrics",
    "OrchestrationRunId",
]


class OrchestrationStage(StrEnum):
    """Stages in the end-to-end orchestration workflow."""

    # Initial state
    PENDING = "pending"

    # Capture stages
    CAPTURE = "capture"
    SNAPSHOT = "snapshot"
    CHANGE_DETECTION = "change_detection"

    # Verification stages
    VERIFICATION_SCHEDULED = "verification_scheduled"
    VERIFICATION_RUNNING = "verification_running"
    VERIFICATION_COMPLETED = "verification_completed"

    # Reconciliation stages
    RECONCILIATION_PENDING = "reconciliation_pending"
    RECONCILIATION_OPEN = "reconciliation_open"
    RECONCILIATION_IN_REVIEW = "reconciliation_in_review"
    RECONCILIATION_RESOLVED = "reconciliation_resolved"
    RECONCILIATION_DISMISSED = "reconciliation_dismissed"

    # Reporting stages
    REPORT_GENERATION = "report_generation"
    REPORT_READY = "report_ready"

    # Submission stages
    SUBMISSION_PENDING = "submission_pending"
    SUBMISSION_SUBMITTED = "submission_submitted"
    SUBMISSION_ACKNOWLEDGED = "submission_acknowledged"

    # Terminal states
    COMPLETED = "completed"
    NO_CHANGE = "no_change"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Valid transitions between orchestration stages
ALLOWED_STAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"capture", "cancelled"}),
    "capture": frozenset({"snapshot", "failed", "cancelled"}),
    "snapshot": frozenset({"change_detection", "failed", "cancelled"}),
    "change_detection": frozenset(
        {
            "no_change",
            "verification_scheduled",
            "verification_running",
            "failed",
            "cancelled",
        }
    ),
    "verification_scheduled": frozenset(
        {"verification_running", "failed", "cancelled"}
    ),
    "verification_running": frozenset(
        {
            "verification_completed",
            "failed",
            "cancelled",
        }
    ),
    "verification_completed": frozenset(
        {
            "no_change",
            "reconciliation_pending",
            "reconciliation_open",
            "report_generation",
            "completed",
            "failed",
            "cancelled",
        }
    ),
    "no_change": frozenset({"completed", "cancelled"}),
    "reconciliation_pending": frozenset(
        {
            "reconciliation_open",
            "cancelled",
        }
    ),
    "reconciliation_open": frozenset(
        {
            "reconciliation_in_review",
            "reconciliation_dismissed",
            "reconciliation_resolved",
            "cancelled",
        }
    ),
    "reconciliation_in_review": frozenset(
        {
            "reconciliation_resolved",
            "reconciliation_dismissed",
            "cancelled",
        }
    ),
    "reconciliation_resolved": frozenset({"report_generation", "cancelled"}),
    "reconciliation_dismissed": frozenset({"report_generation", "cancelled"}),
    "report_generation": frozenset(
        {"report_ready", "failed", "cancelled"}
    ),
    "report_ready": frozenset(
        {
            "submission_pending",
            "completed",
            "cancelled",
        }
    ),
    "submission_pending": frozenset(
        {
            "submission_submitted",
            "completed",
            "cancelled",
        }
    ),
    "submission_submitted": frozenset(
        {"submission_acknowledged", "failed", "cancelled"}
    ),
    "submission_acknowledged": frozenset({"completed", "cancelled"}),
    "completed": frozenset({}),
    "failed": frozenset({}),
    "cancelled": frozenset({}),
}


class OrchestrationStatus(StrEnum):
    """Overall status of an orchestration run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    NO_CHANGE = "no_change"
    WAITING_VERIFICATION = "waiting_verification"
    WAITING_RECONCILIATION = "waiting_reconciliation"
    WAITING_SUBMISSION = "waiting_submission"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OrchestrationRunId(BaseModel):
    """Deterministic run ID for idempotency.

    Combines hospital_id + source_type + timestamp to create a deterministic
    run ID that prevents duplicate orchestration runs.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    hospital_id: str
    source_type: str
    timestamp: datetime

    @property
    def run_id(self) -> str:
        return f"run_{self.hospital_id}_{self.source_type}_{int(self.timestamp.timestamp())}"


class OrchestrationRun(BaseModel):
    """A persistent, idempotent orchestration run.

    One run coordinates the entire P19-A through P19-D workflow for a single
    hospital/source combination. The run is idempotent per hospital/source/trigger.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    run_id: str
    hospital_id: str
    source_type: str
    trigger_ref: str = ""
    status: OrchestrationStatus = OrchestrationStatus.PENDING
    current_stage: str = OrchestrationStage.PENDING
    error: str | None = None
    error_class: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    created_by: str = "system"

    # References to artifacts created during the run
    snapshot_id: str | None = None
    change_set_id: str | None = None
    change_set_status: str | None = None
    verification_job_id: str | None = None
    verification_result_id: str | None = None
    verification_result_status: str | None = None
    reconciliation_case_id: str | None = None
    reconciliation_case_status: str | None = None
    report_id: str | None = None
    report_idempotency_key: str | None = None
    submission_id: str | None = None
    submission_idempotency_key: str | None = None

    # Metrics
    metrics: dict[str, Any] = Field(default_factory=dict)

    # Idempotency key for deduplication
    idempotency_key: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            OrchestrationStatus.COMPLETED,
            OrchestrationStatus.NO_CHANGE,
            OrchestrationStatus.FAILED,
            OrchestrationStatus.CANCELLED,
        }

    @property
    def is_retryable(self) -> bool:
        return self.status == OrchestrationStatus.FAILED

    def can_transition_to(self, new_stage: str) -> bool:
        """Check if transition to new_stage is allowed."""
        allowed = ALLOWED_STAGE_TRANSITIONS.get(self.current_stage, frozenset())
        return new_stage in allowed


class OrchestrationEvent(BaseModel):
    """One audit event for the orchestration run."""

    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: str
    run_id: str
    stage: str
    event_type: str  # e.g., "stage_started", "stage_completed", "stage_failed", "retry", "resume"
    message: str | None = None
    duration_ms: int | None = None
    artifact_id: str | None = None
    artifact_type: str | None = None  # snapshot, change_set, verification_job, etc.
    error: str | None = None
    error_class: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    created_by: str = "system"


class OrchestrationMetrics(BaseModel):
    """Deterministic metrics collected during an orchestration run."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # Crawl metrics
    pages_attempted: int = 0
    pages_fetched: int = 0
    pages_failed: int = 0
    pages_skipped: int = 0

    # Extraction metrics
    records_extracted: int = 0
    records_rejected: int = 0
    duplicates_removed: int = 0
    conflicts_detected: int = 0

    # Snapshot/change metrics
    snapshot_created: bool = False
    snapshot_reused: bool = False
    change_set_created: bool = False
    changes_added: int = 0
    changes_removed: int = 0
    changes_modified: int = 0
    change_set_status: str = ""

    # Verification metrics
    verification_job_created: bool = False
    verification_job_id: str = ""
    verification_status: str = ""
    verification_attempts: int = 0
    verified_count: int = 0
    mismatch_count: int = 0
    unverified_count: int = 0
    not_evaluated_count: int = 0

    # Reconciliation metrics
    reconciliation_case_created: bool = False
    reconciliation_case_id: str = ""
    reconciliation_status: str = ""
    reconciliation_trigger: str = ""
    reconciliation_events: int = 0

    # Report metrics
    report_generated: bool = False
    report_id: str = ""
    report_type: str = ""
    report_format: str = ""

    # Submission metrics
    submission_created: bool = False
    submission_id: str = ""
    submission_status: str = ""
    submission_provider: str = ""
    submission_dry_run: bool = False
    submission_external_reference: str = ""

    # Timing
    total_duration_ms: int = 0
    stage_durations_ms: dict[str, int] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def validate_stage_transition(current: str, new: str) -> bool:
    """Validate that a stage transition is allowed."""
    allowed = ALLOWED_STAGE_TRANSITIONS.get(current, frozenset())
    return new in allowed