"""Benchmark formatters (P26): stable JSON and human-readable Markdown."""

from __future__ import annotations

from hospital_extractor.benchmark.models import (
    AVAILABLE,
    BenchmarkRun,
    MetricResult,
)

__all__ = ["format_benchmark_json", "format_benchmark_markdown"]


def _metric_line(metric: MetricResult) -> str:
    if metric.status is not AVAILABLE:
        label = metric.status.value.upper()
        return f"- **{metric.name}** — {label}: {metric.detail or metric.definition}"
    raw = ""
    if metric.numerator is not None and metric.denominator is not None:
        raw = f" raw={metric.numerator}/{metric.denominator}"
    elif metric.numerator is not None:
        raw = f" raw={metric.numerator}"
    status = (
        "PASS"
        if metric.passed is True
        else "FAIL"
        if metric.passed is False
        else "OBS"
    )
    value = metric.value
    expected = f" expected={metric.expected}" if metric.expected is not None else ""
    unit = f" {metric.unit}" if metric.unit and metric.unit != "count" else ""
    detail = f" — {metric.detail}" if metric.detail else ""
    return (
        f"- [{status}] **{metric.name}** = {value}{unit}{raw}{expected}{detail}"
    )


def format_benchmark_json(run: BenchmarkRun) -> str:
    """Canonical JSON document (sorted keys, no secrets, full raw counts)."""
    return run.model_dump_json(indent=2)


def format_benchmark_markdown(run: BenchmarkRun) -> str:
    """Markdown research report with methodology, cases, and observations."""
    lines: list[str] = []
    summary = run.summary
    lines.append("# Hospital Website Extractor — Benchmark Report")
    lines.append("")
    lines.append(f"- **Run ID**: `{run.run_id}`")
    lines.append(f"- **Timestamp**: {run.timestamp.isoformat()}")
    lines.append(
        f"- **Environment**: Python {run.environment.python_version} on "
        f"{run.environment.platform} (package {run.environment.package_version})"
    )
    lines.append(f"- **Methodology**: {run.methodology}")
    lines.append("")
    if summary is not None:
        lines.append("## Summary (no composite score)")
        lines.append("")
        lines.append(f"- Cases: total={summary.cases_total} pass={summary.cases_passed} "
                     f"fail={summary.cases_failed} observation={summary.cases_observation} "
                     f"error={summary.cases_error}")
        lines.append(f"- Metrics: available={summary.metrics_available} "
                     f"not_measured={summary.metrics_not_measured} "
                     f"not_applicable={summary.metrics_not_applicable} "
                     f"passed={summary.metrics_passed} failed={summary.metrics_failed}")
        lines.append(f"- Total case duration: {summary.total_duration_ms} ms")
        lines.append(f"- Note: {summary.note}")
        lines.append("")
    lines.append("## Cases")
    lines.append("")
    for result in run.cases:
        lines.append(f"### {result.case.case_id} — {result.case.label} [{result.status}]")
        lines.append("")
        lines.append(f"- Category: `{result.case.category}`")
        lines.append(f"- Fixtures: {', '.join(result.case.fixture_ids) or '—'}")
        lines.append(f"- Duration: {result.duration_ms} ms")
        if result.error:
            lines.append(f"- Error: `{result.error}`")
        if result.notes:
            lines.append(f"- Notes: {result.notes}")
        lines.append("")
        if result.metrics:
            lines.append("| Metric | Status | Value | Passed |")
            lines.append("|---|---|---|---|")
            for m in result.metrics:
                lines.append(
                    f"| {m.name} | {m.status.value} | {m.value} | {m.passed} |"
                )
            lines.append("")
            lines.append("Details:")
            lines.append("")
            for m in result.metrics:
                lines.append(_metric_line(m))
            lines.append("")
    if run.real_world_observations:
        lines.append("## Real-world observations (documented prior runs)")
        lines.append("")
        lines.append(
            "These are REAL-WORLD OBSERVATIONS from prior documented runs "
            "(not re-executed here, never re-labeled as model accuracy)."
        )
        lines.append("")
        for obs in run.real_world_observations:
            lines.append(f"### {obs.source_id} ({obs.recorded_date})")
            lines.append("")
            lines.append(f"- URL: {obs.url}")
            lines.append(f"- Classification: `{obs.classification}`")
            lines.append(f"- Summary: {obs.summary}")
            lines.append(f"- Evidence: `{obs.evidence_doc}`")
            if obs.raw_counts:
                counts = ", ".join(f"{k}={v}" for k, v in sorted(obs.raw_counts.items()))
                lines.append(f"- Raw counts: {counts}")
            if obs.known_limitations:
                lines.append(f"- Known limitations: {'; '.join(obs.known_limitations)}")
            lines.append("")
    if run.limitations:
        lines.append("## Limitations")
        lines.append("")
        for item in run.limitations:
            lines.append(f"- {item}")
        lines.append("")
    lines.append(
        "---\n\nTEST CORRECTNESS (fixture metrics) and REAL-WORLD ACCURACY "
        "are distinct. No composite overall score is reported."
    )
    return "\n".join(lines) + "\n"
