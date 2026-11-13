"""Assembling the side-by-side comparison from real evidence only.

The service is pure: it receives already-resolved hospital subjects (with the
evidence each one actually has) and returns a matrix. It never fetches, never
scores hospitals against each other and never emits a winner — a row can only
report what a source said, or that nothing was found.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from hospital_extractor.comparison.evidence import criterion_evidence
from hospital_extractor.comparison.interpret import standard_criteria
from hospital_extractor.comparison.schemas import (
    ColumnCoverage,
    ComparisonCell,
    ComparisonColumn,
    ComparisonRequest,
    ComparisonResponse,
    ComparisonRow,
    CriterionDefinition,
    EvidenceItem,
    EvidenceSource,
    EvidenceStatus,
    SourceStatus,
)
from hospital_extractor.recommendation.criteria import CRITERIA_BY_KEY

__all__ = ["ComparisonSubject", "build_comparison", "resolve_criteria"]


@dataclass(frozen=True)
class ComparisonSubject:
    """One physical facility column together with the evidence it has."""

    column: ComparisonColumn
    evidence: tuple[EvidenceItem, ...] = ()


_SOURCE_LABELS: dict[EvidenceSource, str] = {
    "medcompare_server": "the MedCompare server",
    "public_research": "public internet research",
    "website_crawl": "the requested website extraction",
    "user_provided": "information you supplied",
}


def resolve_criteria(
    request: ComparisonRequest,
) -> tuple[list[CriterionDefinition], list[str]]:
    """Return the criteria rows for *request* plus any warnings to surface."""
    warnings: list[str] = []
    custom = [criterion for criterion in (request.criteria or []) if criterion.origin == "custom"]

    if request.scope == "standard_12":
        return standard_criteria(), warnings

    if request.scope == "custom_only":
        if not custom:
            raise ValueError(
                "scope 'custom_only' requires at least one custom criterion; "
                "interpret a prompt first"
            )
        return custom, warnings

    rows = standard_criteria()
    standard_keys = {criterion.key for criterion in rows}
    for criterion in custom:
        if criterion.overlaps_standard and criterion.overlaps_standard in standard_keys:
            warnings.append(
                f"Custom criterion '{criterion.label}' overlaps the standard criterion "
                f"'{CRITERIA_BY_KEY[criterion.overlaps_standard].label}'; both rows are shown "
                "so nothing you asked for is dropped."
            )
        rows.append(criterion)
    return rows, warnings


def build_comparison(
    request: ComparisonRequest,
    subjects: Sequence[ComparisonSubject],
    *,
    source_statuses: Sequence[SourceStatus],
    extra_warnings: Sequence[str] = (),
) -> ComparisonResponse:
    """Build the comparison matrix for *subjects* under *request*."""
    criteria, warnings = resolve_criteria(request)
    warnings = [*extra_warnings, *warnings]

    columns = [subject.column for subject in subjects]
    rows = [
        ComparisonRow(
            criterion=criterion,
            cells=[_cell(criterion, subject, request) for subject in subjects],
        )
        for criterion in criteria
    ]
    coverage = [
        _coverage(index, subject.column.column_id, rows)
        for index, subject in enumerate(subjects)
    ]

    return ComparisonResponse(
        scope=request.scope,
        source=request.source,
        criteria=criteria,
        columns=columns,
        rows=rows,
        source_statuses=list(source_statuses),
        coverage=coverage,
        warnings=warnings,
    )


def _cell(
    criterion: CriterionDefinition,
    subject: ComparisonSubject,
    request: ComparisonRequest,
) -> ComparisonCell:
    evidence = criterion_evidence(
        criterion.key,
        label=criterion.label,
        available=subject.evidence,
        overlaps_standard=criterion.overlaps_standard,
    )
    if not evidence:
        return ComparisonCell(
            status="unavailable",
            value=None,
            explanation=_missing_explanation(criterion, request.source),
        )

    values = list(dict.fromkeys(str(item.value) for item in evidence))
    status = _aggregate_status(item.status for item in evidence)
    notes = [str(item.note) for item in evidence if item.note]
    explanation = " ".join(dict.fromkeys(notes))
    if status == "conflicting":
        conflict = "Conflicting information: " + " | ".join(values)
        explanation = f"{explanation} {conflict}".strip() if explanation else conflict

    source_names = [str(item.source_name) for item in evidence if item.source_name]
    source_urls = [str(item.source_url) for item in evidence if item.source_url]
    retrieved = [item.retrieved_at for item in evidence if item.retrieved_at]
    published = [item.published_at for item in evidence if item.published_at]
    confidences = [item.confidence for item in evidence if item.confidence is not None]
    primary = evidence[0]

    return ComparisonCell(
        status=status,
        value=" | ".join(values),
        source=primary.source,
        source_name=", ".join(dict.fromkeys(source_names)) or None,
        source_url=source_urls[0] if source_urls else None,
        retrieved_at=max(retrieved) if retrieved else None,
        published_at=max(published) if published else None,
        confidence=max(confidences) if confidences else None,
        explanation=explanation,
    )


def _aggregate_status(statuses: Iterable[EvidenceStatus]) -> EvidenceStatus:
    unique = set(statuses)
    if "conflicting" in unique:
        return "conflicting"
    if unique == {"outdated"}:
        return "outdated"
    if "outdated" in unique and "available" in unique:
        return "outdated"
    return "available"


def _missing_explanation(criterion: CriterionDefinition, source: EvidenceSource) -> str:
    origin = _SOURCE_LABELS.get(source, "the selected source")
    if criterion.origin == "standard":
        spec = CRITERIA_BY_KEY.get(criterion.key)
        reason = spec.rationale if spec else "No stored evidence exists for this criterion."
        return (
            f"Information not available from {origin}. {reason} "
            "Nothing was found for this facility, which is not evidence that it lacks it."
        )
    return (
        f'No available source states "{criterion.label}" for this facility, so the '
        f"criterion cannot be evaluated from {origin}. Missing evidence is not a "
        "negative finding."
    )


def _coverage(index: int, column_id: str, rows: Sequence[ComparisonRow]) -> ColumnCoverage:
    total = len(rows)
    with_evidence = sum(
        1
        for row in rows
        if index < len(row.cells) and row.cells[index].status in ("available", "conflicting")
    )
    return ColumnCoverage(
        column_id=column_id,
        criteria_total=total,
        criteria_with_evidence=with_evidence,
        coverage_ratio=round(with_evidence / total, 3) if total else 0.0,
    )
