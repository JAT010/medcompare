"""Contracts for the database-to-website consistency audit.

The audit compares two independently sourced views of the same physical
facility: the stored MedCompare registry record and a persisted website
extraction. Every finding carries provenance for each side that actually has a
value, and a missing value is reported as "not found in that source" rather
than as proof that the hospital lacks it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AuditStatus = Literal[
    "consistent",
    "differs",
    "missing_from_website",
    "missing_from_internal_record",
    "potentially_outdated",
    "unverifiable",
]
AuditSourceKind = Literal["internal_record", "website_crawl"]
CrawlRunStatusValue = Literal[
    "completed", "completed_with_warnings", "failed", "timed_out", "empty"
]

AUDIT_DISCLAIMER = (
    "The consistency audit reports what each source states. Missing information means "
    "it was not found in that source, not that the facility lacks it. No automatic "
    "correction, winner or quality ranking is produced."
)

BRANCH_IDENTITY_NOTE = (
    "Audited at the physical-facility level. The parent organisation is never "
    "substituted for this specific branch."
)

INTERNAL_SOURCE_NOTE = (
    "Value read from the stored MedCompare registry record for this facility. This is "
    "not a confirmed live connection to the hospital's own database."
)


class AuditSourceValue(BaseModel):
    """One side of a finding, with the provenance that supports it."""

    model_config = ConfigDict(extra="forbid")

    source: AuditSourceKind
    source_name: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=4000)
    source_url: str | None = Field(default=None, max_length=2000)
    retrieved_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str | None = Field(default=None, max_length=1000)


class AuditFinding(BaseModel):
    """The comparison outcome for one field of one physical facility."""

    model_config = ConfigDict(extra="forbid")

    field_key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=160)
    status: AuditStatus
    internal: AuditSourceValue | None = None
    website: AuditSourceValue | None = None
    explanation: str = Field(min_length=1, max_length=2000)
    requires_authorized_integration: bool = False


class AuditSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings_total: int
    consistent: int
    differs: int
    missing_from_website: int
    missing_from_internal_record: int
    potentially_outdated: int
    unverifiable: int


class InternalRecordSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    name: str
    organization_name: str | None = None
    canonical_url: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    updated_at: datetime | None = None


class WebsiteSnapshotSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crawl_run_id: uuid.UUID
    status: CrawlRunStatusValue
    target_url: str
    requested_url: str
    hospital_name: str | None = None
    pages_fetched: int
    evidence_count: int
    usable_for_comparison: bool
    requested_at: datetime | None = None
    completed_at: datetime | None = None
    retrieved_at: datetime | None = None


class ConsistencyAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    crawl_run_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Explicit persisted crawl run to audit. When omitted, the latest usable "
            "snapshot for the facility is used."
        ),
    )


class ConsistencyAuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    facility_name: str
    organization_name: str | None = None
    branch_identity_note: str = BRANCH_IDENTITY_NOTE
    internal_record: InternalRecordSummary
    website_snapshot: WebsiteSnapshotSummary | None = None
    findings: list[AuditFinding]
    summary: AuditSummary
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = AUDIT_DISCLAIMER
