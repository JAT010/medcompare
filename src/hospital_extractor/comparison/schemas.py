"""Request/response contracts for the hospital comparison experience.

The models here are deliberately explicit about *provenance*: every piece of
evidence carries the source it came from, an optional source URL, retrieval and
publication dates, and a status that distinguishes available, unavailable,
conflicting and outdated information. Nothing in this module can express a
"winner", a clinical score or a fabricated citation - those concepts have no
field to live in.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ComparisonScope = Literal["standard_12", "standard_12_plus_custom", "custom_only"]
EvidenceSource = Literal["medcompare_server", "public_research", "website_crawl", "user_provided"]
EvidenceStatus = Literal["available", "unavailable", "conflicting", "outdated"]
CriterionOrigin = Literal["standard", "custom"]
CrawlRunStatusValue = Literal[
    "completed", "completed_with_warnings", "failed", "timed_out", "empty"
]

INTERPRETER_DETERMINISTIC = "deterministic_clause_parser"

DISCLAIMER = (
    "Comparison reports evidence only. No winner, clinical quality score or "
    "overall ranking is produced, and missing information is never treated as "
    "a negative finding."
)


class CriterionDefinition(BaseModel):
    """One criterion to be compared, standard or user-defined."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=160)
    origin: CriterionOrigin
    evidence_needed: str = Field(
        description="What information would be required to evaluate this criterion."
    )
    matched_source: str | None = Field(
        default=None,
        description="Registry or extraction field able to answer this criterion, when known.",
    )
    overlaps_standard: str | None = Field(
        default=None,
        description="Standard criterion key this custom criterion duplicates, when any.",
    )
    original_text: str | None = Field(
        default=None,
        description="Verbatim user phrase the criterion was derived from (custom criteria).",
    )


class InterpretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=2000)

    @field_validator("prompt")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("prompt must not be blank")
        return stripped


class InterpretResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: list[CriterionDefinition]
    clarification_questions: list[str]
    interpreter: str
    interpretation_note: str


class EvidenceItem(BaseModel):
    """One attributable finding for one criterion of one hospital."""

    model_config = ConfigDict(extra="forbid")

    criterion_key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=4000)
    source: EvidenceSource
    source_name: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=2000)
    retrieved_at: datetime | None = None
    published_at: date | None = None
    status: EvidenceStatus = "available"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str | None = Field(default=None, max_length=1000)


class RegisteredHospital(BaseModel):
    """A facility that already exists in the MedCompare registry.

    Server-side persisted crawl evidence is authoritative. ``evidence`` is a
    legacy/untrusted compatibility field and must not override server evidence.
    ``crawl_run_id`` selects an explicit persisted run (validated against the
    facility); when omitted, the latest usable snapshot is used.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["registered"] = "registered"
    facility_id: uuid.UUID
    crawl_run_id: uuid.UUID | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)


class UnregisteredHospital(BaseModel):
    """A physical facility that is not in the registry.

    Requires an explicit stable server-generated ``comparison_subject_id`` and,
    for crawl-backed comparison, a validated ``crawl_run_id`` belonging to that
    subject. ``evidence`` is legacy/untrusted compatibility only.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["unregistered"] = "unregistered"
    comparison_subject_id: uuid.UUID | None = None
    crawl_run_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=300)
    website_url: str | None = Field(default=None, max_length=2000)
    evidence: list[EvidenceItem] = Field(default_factory=list)


HospitalInput = Annotated[
    RegisteredHospital | UnregisteredHospital,
    Field(discriminator="type"),
]


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ComparisonScope = "standard_12"
    source: EvidenceSource = "medcompare_server"
    criteria: list[CriterionDefinition] | None = None
    hospitals: list[HospitalInput] = Field(min_length=2, max_length=6)

    @field_validator("hospitals")
    @classmethod
    def _reject_duplicate_identities(cls, value: list[HospitalInput]) -> list[HospitalInput]:
        seen_facilities: set[uuid.UUID] = set()
        seen_subjects: set[uuid.UUID] = set()
        for hospital in value:
            if isinstance(hospital, RegisteredHospital):
                if hospital.facility_id in seen_facilities:
                    raise ValueError("the same facility may only appear once")
                seen_facilities.add(hospital.facility_id)
            else:
                if hospital.comparison_subject_id is not None:
                    if hospital.comparison_subject_id in seen_subjects:
                        raise ValueError("the same unregistered subject may only appear once")
                    seen_subjects.add(hospital.comparison_subject_id)
        return value


class ComparisonColumn(BaseModel):
    """Header information identifying one physical facility."""

    model_config = ConfigDict(extra="forbid")

    column_id: str
    kind: Literal["registered", "unregistered"]
    name: str
    location: str | None = None
    organization_name: str | None = None
    website_url: str | None = None
    identity_note: str | None = None
    crawl_run_id: uuid.UUID | None = None
    comparison_subject_id: uuid.UUID | None = None


class ComparisonCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: EvidenceStatus
    value: str | None = None
    source: EvidenceSource | None = None
    source_name: str | None = None
    source_url: str | None = None
    retrieved_at: datetime | None = None
    published_at: date | None = None
    confidence: float | None = None
    explanation: str = ""


class ComparisonRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: CriterionDefinition
    cells: list[ComparisonCell]


class SourceStatus(BaseModel):
    """Truthful status of one evidence source for this request."""

    model_config = ConfigDict(extra="forbid")

    source: EvidenceSource
    status: Literal["active", "not_configured", "unsupported"]
    detail: str
    required_integration: str | None = None


class ColumnCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_id: str
    criteria_total: int
    criteria_with_evidence: int
    coverage_ratio: float


class ComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ComparisonScope
    source: EvidenceSource
    criteria: list[CriterionDefinition]
    columns: list[ComparisonColumn]
    rows: list[ComparisonRow]
    source_statuses: list[SourceStatus]
    coverage: list[ColumnCoverage]
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


class CapabilityStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    available: bool
    detail: str
    implementation: str | None = None
    required_integration: str | None = None


class CapabilityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capabilities: list[CapabilityStatus]


class CrawlRequest(BaseModel):
    """A single, explicitly requested website extraction."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2000)
    facility_id: uuid.UUID | None = None
    comparison_subject_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=300)
    max_pages: int = Field(default=15, ge=1, le=50)

    @field_validator("url")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("url must not be blank")
        return stripped


class CrawlResponse(BaseModel):
    """Result of a synchronous extraction, persisted server-side."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed"]
    execution: Literal["synchronous"]
    job_store_available: bool
    target_url: str
    pages_fetched: int
    hospital_name: str | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    criteria_without_evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    crawl_run_id: uuid.UUID | None = None
    facility_id: uuid.UUID | None = None
    comparison_subject_id: uuid.UUID | None = None
    crawl_status: CrawlRunStatusValue | None = None
    evidence_count: int | None = None
    completed_at: datetime | None = None
    retrieved_at: datetime | None = None


class CrawlRunDetail(BaseModel):
    """Persisted crawl run metadata and normalized evidence (no raw paths)."""

    model_config = ConfigDict(extra="forbid")

    crawl_run_id: uuid.UUID
    facility_id: uuid.UUID | None = None
    comparison_subject_id: uuid.UUID | None = None
    subject_name: str | None = None
    subject_location: str | None = None
    subject_website_url: str | None = None
    status: CrawlRunStatusValue
    execution_mode: Literal["synchronous"] = "synchronous"
    target_url: str
    requested_url: str
    hospital_name: str | None = None
    pages_fetched: int
    evidence_count: int
    evidence: list[EvidenceItem] = Field(default_factory=list)
    criteria_without_evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usable_for_comparison: bool
    requested_at: datetime | None = None
    completed_at: datetime | None = None
    retrieved_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None
