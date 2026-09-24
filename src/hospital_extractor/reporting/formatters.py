"""Report formatters: JSON, HTML, Markdown."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from hospital_extractor.reporting.models import Report

__all__ = [
    "format_report_json",
    "format_report_html",
    "format_report_markdown",
    "format_report",
]


def _format_datetime(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, (list, dict)):
        return str(value)
    return str(value)


def format_report_json(report: Report) -> str:
    """Format report as JSON."""
    import json

    return json.dumps(report.model_dump(mode="json"), indent=2, default=str)


def format_report_html(report: Report) -> str:
    """Format report as HTML."""
    lines = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "    <meta charset=\"UTF-8\">",
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">",
        f"    <title>Report {report.report_id}</title>",
        "    <style>",
        "        body { font-family: -apple-system, BlinkMacSystemFont, "
        "'Segoe UI', Roboto, sans-serif; max-width: 900px; "
        "margin: 2rem auto; padding: 0 1rem; }",
        "        h1, h2, h3 { color: #1a1a2e; }",
        "        .badge { display: inline-block; padding: 0.25rem 0.5rem; "
        "border-radius: 0.25rem; font-size: 0.75rem; font-weight: 600; }",
        "        .badge-generated { background: #e0e7ff; color: #3730a3; }",
        "        .badge-ready { background: #dcfce7; color: #166534; }",
        "        .badge-submitted { background: #fef3c7; color: #92400e; }",
        "        .badge-failed { background: #fee2e2; color: #991b1b; }",
        "        .card { border: 1px solid #e5e7eb; border-radius: 0.5rem; "
        "padding: 1rem; margin: 1rem 0; }",
        "        .muted { color: #6b7280; font-size: 0.875rem; }",
        "        code { background: #f3f4f6; padding: 0.125rem 0.25rem; "
        "border-radius: 0.25rem; font-size: 0.875rem; }",
        "        table { width: 100%; border-collapse: collapse; margin: 1rem 0; }",
        "        th, td { border: 1px solid #e5e7eb; padding: 0.5rem; "
        "text-align: left; }",
        "        th { background: #f9fafb; font-weight: 600; }",
        "        .outcome-verified { color: #166534; }",
        "        .outcome-mismatch { color: #991b1b; }",
        "        .outcome-unverified { color: #92400e; }",
        "    </style>",
        "</head>",
        "<body>",
        "    <header>",
        f"        <h1>Report <code>{report.report_id}</code></h1>",
        "        <p>",
        f"            <span class=\"badge badge-{report.status.value}\">"
        f"{report.status.value.upper()}</span> ",
        f"            Type: {report.report_type.value} ",
        f"            Format: {report.format}",
        "        </p>",
        "        <p class=\"muted\">",
        f"            Hospital: <code>{report.hospital_id}</code> · ",
        f"            Generated: {_format_datetime(report.generated_at)} · ",
        f"            By: {report.generated_by}",
        "        </p>",
        "    </header>",
        "    <main>",
    ]

    # Case info
    if report.reconciliation_case_id:
        lines.append("    <section class=\"card\">")
        lines.append("        <h2>Reconciliation Case</h2>")
        lines.append(f"        <p><code>{report.reconciliation_case_id}</code></p>")
        lines.append("    </section>")

    # Evidence references
    if report.evidence_references:
        lines.append("    <section class=\"card\">")
        lines.append("        <h2>Evidence References</h2>")
        lines.append("        <ul>")
        for key, value in report.evidence_references.items():
            lines.append(f"            <li><code>{key}</code>: <code>{value}</code></li>")
        lines.append("        </ul>")
        lines.append("    </section>")

    # Snapshot references
    if report.snapshot_references:
        lines.append("    <section class=\"card\">")
        lines.append("        <h2>Snapshot References</h2>")
        lines.append("        <table>")
        lines.append(
            "            <tr><th>Snapshot ID</th><th>Source</th>"
            "<th>Provider</th><th>Captured</th><th>Status</th></tr>"
        )
        for snap in report.snapshot_references:
            lines.append(
                f"            <tr>"
                f"<td><code>{snap.snapshot_id}</code></td>"
                f"<td>{snap.source_type}</td>"
                f"<td>{snap.provider or '—'}</td>"
                f"<td>{_format_datetime(snap.captured_at)}</td>"
                f"<td>{snap.status}</td>"
                f"</tr>"
            )
        lines.append("        </table>")
        lines.append("    </section>")

    # Items (for reconciliation reports)
    items = report.content.get("items", [])
    if items:
        lines.append("    <section class=\"card\">")
        lines.append("        <h2>Field-Level Details</h2>")
        lines.append("        <table>")
        lines.append(
            "            <tr><th>Field</th><th>Category</th><th>Change</th>"
            "<th>Outcome</th><th>Authoritative</th><th>Verification</th></tr>"
        )
        for item in items:
            entity_key_html = (
                f'<br><span class="muted">{item.get("entity_key")}</span>'
                if item.get("entity_key")
                else ""
            )
            outcome_html = (
                f'<span class="outcome outcome-{item.get("outcome", "")}">'
                f'{item.get("outcome", "")}</span>'
            )
            lines.append(
                "            <tr>"
                f"<td><code>{item.get('path', '')}</code>"
                f"{entity_key_html}"
                f"</td>"
                f"<td>{item.get('category', '')}</td>"
                f"<td>{item.get('change_type', '')}</td>"
                f"<td>{outcome_html}</td>"
                f"<td>{_format_value(item.get('authoritative_value'))}</td>"
                f"<td>{_format_value(item.get('verification_value'))}</td>"
                f"</tr>"
            )
        lines.append("        </table>")
        lines.append("    </section>")

    lines.extend([
        "    </main>",
        "    <footer>",
        f"        <p class=\"muted\">Report generated at "
        f"{_format_datetime(report.generated_at)} by {report.generated_by}</p>",
        "    </footer>",
        "</body>",
        "</html>",
    ])

    return "\n".join(lines)


def format_report_markdown(report: Report) -> str:
    """Format report as Markdown."""
    lines = [
        f"# Report `{report.report_id}`",
        "",
        f"**Status:** `{report.status.value.upper()}`  ",
        f"**Type:** `{report.report_type.value}`  ",
        f"**Format:** `{report.format}`  ",
        "",
        f"**Hospital:** `{report.hospital_id}`  ",
        f"**Generated:** {_format_datetime(report.generated_at)}  ",
        f"**By:** {report.generated_by}  ",
        "",
    ]

    if report.reconciliation_case_id:
        lines.append("## Reconciliation Case")
        lines.append(f"`{report.reconciliation_case_id}`")
        lines.append("")

    if report.evidence_references:
        lines.append("## Evidence References")
        for key, value in report.evidence_references.items():
            lines.append(f"- **{key}**: `{value}`")
        lines.append("")

    if report.snapshot_references:
        lines.append("## Snapshot References")
        lines.append("| Snapshot ID | Source | Provider | Captured | Status |")
        lines.append("|-------------|--------|----------|----------|--------|")
        for snap in report.snapshot_references:
            lines.append(
                f"| `{snap.snapshot_id}` | {snap.source_type} | {snap.provider or '—'} | "
                f"{_format_datetime(snap.captured_at)} | {snap.status} |"
            )
        lines.append("")

    items = report.content.get("items", [])
    if items:
        lines.append("## Field-Level Details")
        lines.append("")
        lines.append("| Field | Category | Change | Outcome | Authoritative | Verification |")
        lines.append("|-------|----------|--------|---------|---------------|--------------|")
        for item in items:
            lines.append(
                f"| `{item.get('path', '')}` | {item.get('category', '')} | "
                f"{item.get('change_type', '')} | {item.get('outcome', '')} | "
                f"{_format_value(item.get('authoritative_value'))} | "
                f"{_format_value(item.get('verification_value'))} |"
            )
        lines.append("")

    lines.append("---")
    lines.append(
        f"*Report generated at {_format_datetime(report.generated_at)} "
        f"by {report.generated_by}*"
    )

    return "\n".join(lines)


def format_report(report: Report, format: str | None = None) -> str:
    """Format a report in the specified format (json, html, markdown)."""
    fmt = (format or report.format).lower()
    if fmt == "html":
        return format_report_html(report)
    if fmt == "markdown" or fmt == "md":
        return format_report_markdown(report)
    return format_report_json(report)