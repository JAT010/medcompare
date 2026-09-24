"""Reporting models: persistent reports and submissions after reconciliation.

A report is a structured, evidence-backed document generated from a reconciliation
case (or verification result / change set). It never modifies external systems.

REPORT -> NOTIFY -> SUBMIT are three distinct operations. Default behavior
remains: generate the report only. External actions require explicit
authorization/configuration.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ReportType",
    "ReportStatus",
    "SubmissionStatus",
    "NotificationProviderType",
    "Report",
    "ReportSnapshotReference",
    "Submission",
    "NotificationConfig",
    "SubmissionIdempotencyKey",
]


class ReportType(StrEnum):
    """Type of report generated."""

    RECONCILIATION_REPORT = "reconciliation_report"
    VERIFICATION_REPORT = "verification_report"
    CHANGE_REPORT = "change_report"


class ReportStatus(StrEnum):
    """Lifecycle status of a report."""

    GENERATED = "generated"
    READY = "ready"
    SUBMITTED = "submitted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubmissionStatus(StrEnum):
    """Lifecycle status of a submission to an external system."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NotificationProviderType(StrEnum):
    """Available notification/submission providers."""

    LOCAL = "local"
    MOCK = "mock"
    EMAIL = "email"
    GOVERNMENT_PORTAL = "government_portal"


class ReportSnapshotReference(BaseModel):
    """A reference to an immutable snapshot (never duplicated)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    snapshot_id: str
    hospital_id: str
    source_type: str
    provider: str | None = None
    hospital_ref: str = ""
    captured_at: datetime
    status: str
    content_hash: str


class Report(BaseModel):
    """A persistent, evidence-backed report generated from reconciliation data.

    References existing immutable snapshots instead of copying their payloads.
    The field-level agreements/disagreements live in the reconciliation case
    items which are derived from already-captured canonical values.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    report_id: str
    hospital_id: str
    reconciliation_case_id: str | None = None
    verification_result_id: str | None = None
    change_set_id: str | None = None
    report_type: ReportType
    status: ReportStatus = ReportStatus.GENERATED
    format: str = "json"
    content: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime
    generated_by: str = "system"
    snapshot_references: list[ReportSnapshotReference] = Field(default_factory=list)
    evidence_references: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class NotificationConfig(BaseModel):
    """Configuration for a notification provider."""

    model_config = ConfigDict(str_strip_whitespace=True)

    provider_type: NotificationProviderType
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class SubmissionIdempotencyKey(BaseModel):
    """Deterministic idempotency key for external submissions.

    Combines report_id + provider_type to prevent duplicate submissions
    of the same report to the same provider.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    report_id: str
    provider_type: NotificationProviderType
    key: str

    @property
    def idempotency_key(self) -> str:
        return f"sub_{self.report_id}_{self.provider_type.value}"


class Submission(BaseModel):
    """A persistent record of an external submission action.

    Every external action (notify, submit) creates a submission record
    with full audit trail. Idempotency is enforced via a deterministic
    key derived from (report_id, provider_type).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    submission_id: str
    report_id: str
    provider_type: NotificationProviderType
    idempotency_key: str
    status: SubmissionStatus = SubmissionStatus.PENDING
    submitted_at: datetime | None = None
    external_reference: str | None = None
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    dry_run: bool = False
    created_by: str = "system"
    created_at: datetime
    updated_at: datetime