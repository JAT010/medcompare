"""Data quality & observability reports (P23).

Read-only metrics computed from stored rows only: field/provenance coverage,
UNKNOWN rate, conflict rate, duplicate rate, extraction/crawl success, source
health, freshness, verification/reconciliation/monitoring/connector outcomes,
change frequency, snapshot health, and pipeline stage counters.

There is intentionally **no** opaque overall score — each metric carries its
own definition so reviewers can audit exactly what was counted.
"""

from __future__ import annotations

from hospital_extractor.quality.formatters import (
    QualityReport,
    format_quality_json,
    format_quality_markdown,
    quality_report_to_dict,
)
from hospital_extractor.quality.models import (
    DatasetQualityReport,
    HospitalQualityReport,
    MetricDefinition,
    MetricValue,
    PipelineQualityReport,
    QualityDimension,
    QualityMetric,
    ReportScope,
    SourceQualityReport,
    no_overall_score_note,
)
from hospital_extractor.quality.service import (
    HospitalQualityNotFoundError,
    build_dataset_quality_report,
    build_hospital_quality_report,
    build_pipeline_quality_report,
    build_source_quality_reports,
)

__all__ = [
    "DatasetQualityReport",
    "HospitalQualityNotFoundError",
    "HospitalQualityReport",
    "MetricDefinition",
    "MetricValue",
    "PipelineQualityReport",
    "QualityDimension",
    "QualityMetric",
    "QualityReport",
    "ReportScope",
    "SourceQualityReport",
    "build_dataset_quality_report",
    "build_hospital_quality_report",
    "build_pipeline_quality_report",
    "build_source_quality_reports",
    "format_quality_json",
    "format_quality_markdown",
    "no_overall_score_note",
    "quality_report_to_dict",
]
