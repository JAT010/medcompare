"""Extraction coverage report.

Renders how much of each canonical category was recovered and by which
extraction methods, so a run can be judged at a glance. Pure presentation —
all numbers come from the dataset's :class:`DataQuality` metadata.
"""

from __future__ import annotations

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES
from hospital_extractor.schema.discovery import category_records

__all__ = ["format_coverage_report"]


def format_coverage_report(dataset: HospitalDataset) -> str:
    """Render the extraction coverage report for *dataset*."""
    quality = dataset.quality
    lines: list[str] = ["Extraction coverage:", ""]

    lines.append("Pages:")
    lines.append(f"  Considered:       {quality.pages_considered}")
    lines.append(f"  With records:     {quality.pages_with_information}")
    lines.append(f"  Without records:  {quality.pages_without_information}")
    lines.append("")

    lines.append("Category coverage:")
    for category in CANONICAL_CATEGORIES:
        count = len(category_records(dataset, category))
        status = quality.category_coverage.get(category.label, "covered" if count else "not found")
        lines.append(f"  {category.label}: {count}  ({status})")
    lines.append("")

    if quality.missing_fields:
        lines.append("Missing fields:")
        for label, fields in quality.missing_fields.items():
            lines.append(f"  {label}: {', '.join(fields)}")
        lines.append("")

    if quality.extraction_methods:
        lines.append("Extraction methods:")
        for method, count in sorted(
            quality.extraction_methods.items(), key=lambda item: (-item[1], item[0])
        ):
            lines.append(f"  {method}: {count}")
        lines.append("")
    return "\n".join(lines)
