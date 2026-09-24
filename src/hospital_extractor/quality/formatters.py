"""JSON and Markdown formatters for quality reports (P23).

JSON is the structured interchange form; Markdown is the human review form.
Neither introduces an overall score.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from hospital_extractor.quality.models import (
    DatasetQualityReport,
    HospitalQualityReport,
    PipelineQualityReport,
    SourceQualityReport,
    no_overall_score_note,
)

__all__ = [
    "QualityReport",
    "format_quality_json",
    "format_quality_markdown",
    "quality_report_to_dict",
]

QualityReport = (
    HospitalQualityReport
    | SourceQualityReport
    | DatasetQualityReport
    | PipelineQualityReport
)


def quality_report_to_dict(report: QualityReport) -> dict[str, Any]:
    """Stable dict view of any quality report (mode=json)."""
    return report.model_dump(mode="json")


def format_quality_json(report: QualityReport, *, indent: int = 2) -> str:
    """Serialize a quality report as pretty-printed JSON."""
    return json.dumps(quality_report_to_dict(report), indent=indent, sort_keys=False)


def _fmt_dt(value: datetime | None) -> str:
    return value.isoformat() if value is not None else "—"


def _fmt_value(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _metric_rows(report: QualityReport) -> list[str]:
    lines = [
        "| Metric | Dimension | Value | Unit | Definition |",
        "|--------|-----------|-------|------|------------|",
    ]
    for metric in report.metrics:
        lines.append(
            f"| `{metric.name}` | {metric.dimension.value} | "
            f"{_fmt_value(metric.value)} | {metric.unit} | {metric.definition} |"
        )
    if len(report.metrics) == 1:
        # Keep the table header even for a single row (readable diffs).
        pass
    return lines


def _scope_heading(report: QualityReport) -> str:
    if isinstance(report, DatasetQualityReport):
        name = report.hospital_name or report.hospital_ref or report.dataset_id
        return f"Dataset `{report.dataset_id}` — {name}"
    if isinstance(report, SourceQualityReport):
        name = report.hospital_name or report.hospital_id
        provider = report.provider or "—"
        return f"Source `{report.source_type}` ({provider}) — {name}"
    if isinstance(report, HospitalQualityReport):
        name = report.hospital_name or report.hospital_id
        return f"Hospital `{report.hospital_id}` — {name}"
    return "Pipeline (whole store)"


def _scope_context(report: QualityReport) -> list[str]:
    lines: list[str] = []
    if isinstance(report, DatasetQualityReport):
        lines.append(f"- **Source type:** `{report.source_type}`")
        lines.append(f"- **Provider:** {report.provider or '—'}")
        lines.append(f"- **Captured:** {_fmt_dt(report.captured_at)}")
        lines.append(f"- **Record count:** {report.record_count}")
        lines.append(f"- **Hospital id:** `{report.hospital_id or '—'}`")
    elif isinstance(report, SourceQualityReport):
        lines.append(f"- **Hospital id:** `{report.hospital_id or '—'}`")
        lines.append(f"- **Datasets:** {report.dataset_count}")
        lines.append(f"- **Latest captured:** {_fmt_dt(report.latest_captured_at)}")
        if report.authorization_status:
            lines.append(f"- **Authorization:** `{report.authorization_status}`")
    elif isinstance(report, HospitalQualityReport):
        lines.append(f"- **Hospital key:** `{report.hospital_key or '—'}`")
        lines.append(f"- **Datasets:** {report.dataset_count}")
        lines.append(f"- **Distinct sources:** {report.source_count}")
        lines.append(f"- **Latest captured:** {_fmt_dt(report.latest_captured_at)}")
    else:
        lines.append(f"- **Hospitals:** {report.hospital_count}")
        lines.append(f"- **Datasets:** {report.dataset_count}")
        lines.append(f"- **Snapshots:** {report.snapshot_count}")
        lines.append(f"- **Change sets:** {report.change_set_count}")
        lines.append(f"- **Verification jobs:** {report.verification_job_count}")
        lines.append(
            f"- **Reconciliation cases:** {report.reconciliation_case_count}"
        )
        lines.append(f"- **Monitoring policies:** {report.monitoring_policy_count}")
        lines.append(f"- **Reports:** {report.report_count}")
        lines.append(f"- **Orchestration runs:** {report.orchestration_run_count}")
    return lines


def format_quality_markdown(report: QualityReport) -> str:
    """Human-readable Markdown quality report (no overall score)."""
    lines = [
        f"# Quality report — {_scope_heading(report)}",
        "",
        f"**Scope:** `{report.scope.value}`  ",
        f"**Generated:** {_fmt_dt(report.generated_at)}  ",
        f"**Source of metrics:** `{report.source_of_metrics}`  ",
        "**Overall score:** not computed (always null)  ",
        "",
        f"> {no_overall_score_note}",
        "",
        "## Context",
        "",
        *_scope_context(report),
        "",
        "## Metrics",
        "",
        *_metric_rows(report),
        "",
        "---",
        f"*Generated {_fmt_dt(report.generated_at)} from stored rows only.*",
    ]
    return "\n".join(lines)
