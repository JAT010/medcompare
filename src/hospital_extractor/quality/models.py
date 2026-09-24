"""Typed quality-report models (P23).

Every metric is derived from stored rows only (datasets, snapshots, change
sets, verification jobs, reconciliation cases, monitoring policies, reports,
orchestration runs).  There is intentionally **no** opaque overall score:
each dimension is reported as an independent, definition-backed metric so
reviewers can see exactly what was counted and what was not.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DatasetQualityReport",
    "HospitalQualityReport",
    "MetricDefinition",
    "MetricValue",
    "PipelineQualityReport",
    "QualityDimension",
    "QualityMetric",
    "ReportScope",
    "SourceQualityReport",
    "no_overall_score_note",
]

no_overall_score_note = (
    "Metrics are independent dimensions. No composite or overall quality "
    "score is computed or implied."
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class QualityDimension(StrEnum):
    """Named dimension a metric belongs to (not a ranking axis)."""

    FIELD_COVERAGE = "field_coverage"
    PROVENANCE = "provenance"
    UNKNOWN_RATE = "unknown_rate"
    CONFLICT = "conflict"
    DUPLICATE = "duplicate"
    EXTRACTION = "extraction"
    CRAWL = "crawl"
    SOURCE_HEALTH = "source_health"
    FRESHNESS = "freshness"
    VERIFICATION = "verification"
    RECONCILIATION = "reconciliation"
    MONITORING = "monitoring"
    CHANGE_FREQUENCY = "change_frequency"
    SNAPSHOT = "snapshot"
    DATASET = "dataset"
    CONNECTOR = "connector"
    PIPELINE = "pipeline"


class ReportScope(StrEnum):
    """What a quality report covers."""

    HOSPITAL = "hospital"
    SOURCE = "source"
    DATASET = "dataset"
    PIPELINE = "pipeline"


class MetricDefinition(BaseModel):
    """Static description of one metric (how it is computed)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    label: str
    dimension: QualityDimension
    unit: str = "count"
    definition: str


class MetricValue(BaseModel):
    """One observed metric value with its definition reference."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    label: str
    dimension: QualityDimension
    unit: str = "count"
    value: int | float | str | bool | None = None
    definition: str = ""
    detail: str = ""


class QualityMetric(BaseModel):
    """Convenience constructor helper re-exported for tests/docs."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    label: str
    dimension: QualityDimension
    unit: str = "count"
    value: int | float | str | bool | None = None
    definition: str = ""
    detail: str = ""

    def as_value(self) -> MetricValue:
        return MetricValue(
            name=self.name,
            label=self.label,
            dimension=self.dimension,
            unit=self.unit,
            value=self.value,
            definition=self.definition,
            detail=self.detail,
        )


class _ReportBase(BaseModel):
    """Shared envelope fields for every quality report."""

    model_config = ConfigDict(str_strip_whitespace=True)

    scope: ReportScope
    generated_at: datetime = Field(default_factory=_utc_now)
    source_of_metrics: str = "stored_rows_only"
    overall_score: None = Field(
        default=None,
        description="Always null. Composite scores are intentionally unsupported.",
    )
    note: str = no_overall_score_note
    metrics: list[MetricValue] = Field(default_factory=list)

    def metric(self, name: str) -> MetricValue | None:
        for item in self.metrics:
            if item.name == name:
                return item
        return None

    def metric_value(
        self,
        name: str,
        default: int | float | str | bool | None = None,
    ) -> int | float | str | bool | None:
        found = self.metric(name)
        return default if found is None else found.value


class DatasetQualityReport(_ReportBase):
    """Quality metrics for one stored dataset."""

    scope: ReportScope = ReportScope.DATASET
    dataset_id: str
    hospital_id: str = ""
    hospital_key: str = ""
    hospital_name: str | None = None
    source_type: str = "website"
    provider: str | None = None
    hospital_ref: str = ""
    captured_at: datetime | None = None
    record_count: int = 0


class SourceQualityReport(_ReportBase):
    """Quality metrics for one source (source_type + provider) of a hospital."""

    scope: ReportScope = ReportScope.SOURCE
    hospital_id: str = ""
    hospital_name: str | None = None
    source_type: str = "website"
    provider: str | None = None
    dataset_count: int = 0
    latest_dataset_id: str = ""
    latest_captured_at: datetime | None = None
    authorization_status: str = ""


class HospitalQualityReport(_ReportBase):
    """Quality metrics for one canonical hospital identity."""

    scope: ReportScope = ReportScope.HOSPITAL
    hospital_id: str
    hospital_name: str | None = None
    hospital_key: str = ""
    dataset_count: int = 0
    source_count: int = 0
    latest_captured_at: datetime | None = None
    dataset_reports: list[DatasetQualityReport] = Field(default_factory=list)
    source_reports: list[SourceQualityReport] = Field(default_factory=list)


class PipelineQualityReport(_ReportBase):
    """Quality metrics for the whole store / pipeline (no ranking)."""

    scope: ReportScope = ReportScope.PIPELINE
    hospital_count: int = 0
    dataset_count: int = 0
    snapshot_count: int = 0
    change_set_count: int = 0
    verification_job_count: int = 0
    reconciliation_case_count: int = 0
    monitoring_policy_count: int = 0
    report_count: int = 0
    orchestration_run_count: int = 0
