"""Dynamic schema discovery.

After extraction, only categories with meaningful records and only fields with
non-null values are part of the output. This module decides which categories
actually appear, and renders the human-readable dataset summary the CLI prints.
"""

from __future__ import annotations

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES, CanonicalCategory

__all__ = [
    "category_records",
    "discover_categories",
    "empty_categories",
    "format_dataset_summary",
]


def discover_categories(dataset: HospitalDataset) -> list[str]:
    """Canonical category keys with at least one meaningful record."""
    result: list[str] = []
    for category in CANONICAL_CATEGORIES:
        if _category_records(dataset, category):
            result.append(category.key)
    return result


def empty_categories(dataset: HospitalDataset) -> list[str]:
    """Canonical category keys with no records (used by data quality)."""
    return [
        category.key
        for category in CANONICAL_CATEGORIES
        if not _category_records(dataset, category)
    ]


def category_records(dataset: HospitalDataset, category: CanonicalCategory) -> list[object]:
    """Records held by *dataset* for *category* (empty list when none)."""
    return _category_records(dataset, category)


def _category_records(dataset: HospitalDataset, category: CanonicalCategory) -> list[object]:
    if category.key == "hospital":
        records: list[object] = [dataset.hospital] if dataset.hospital is not None else []
    else:
        records = list(getattr(dataset, category.key))
    if category.single_record:
        return records[:1]
    return records


def format_dataset_summary(dataset: HospitalDataset) -> str:
    """Render the CLI summary block for an extracted dataset."""
    lines: list[str] = ["Structured dataset:", ""]
    hospital = dataset.hospital
    quality = dataset.quality
    if hospital is not None and hospital.name:
        lines.append("Hospital:")
        lines.append(f"  {hospital.name}")
        if hospital.hospital_type:
            lines.append(f"  Type: {hospital.hospital_type}")
        lines.append("")

    categories = discover_categories(dataset)
    lines.append("Categories discovered:")
    for category in CANONICAL_CATEGORIES:
        if category.key in categories:
            lines.append(f"  {category.label}")
    lines.append("")

    coverage = quality.category_coverage
    if coverage:
        lines.append("Category coverage:")
        for category in CANONICAL_CATEGORIES:
            status = coverage.get(category.label)
            if status is not None:
                lines.append(f"  {category.label}: {status}")
        lines.append("")

    missing = quality.missing_fields
    if missing:
        lines.append("Missing fields:")
        for label, fields in missing.items():
            lines.append(f"  {label}: {', '.join(fields)}")
        lines.append("")

    lines.append("Records:")
    for category in CANONICAL_CATEGORIES:
        records = _category_records(dataset, category)
        if records:
            lines.append(f"  {category.label}: {len(records)}")
    lines.append("")

    lines.extend(
        [
            "Processing:",
            f"  Pages considered:   {quality.pages_considered}",
            f"  Pages extracted:    {quality.pages_extracted}",
            f"  Records extracted:  {quality.records_extracted}",
            f"  Records rejected:   {quality.records_rejected}",
            f"  Duplicates removed: {quality.duplicates_removed}",
            f"  Conflicts:          {quality.conflicts_detected}",
            f"  Uncertain hospital associations: {quality.uncertain_hospital_associations}",
        ]
    )
    if quality.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  {warning}" for warning in quality.warnings[:_MAX_WARNINGS])
    if quality.failures:
        lines.append("")
        lines.append("Extraction failures:")
        lines.extend(f"  {failure}" for failure in quality.failures[:_MAX_WARNINGS])
    return "\n".join(lines)


_MAX_WARNINGS = 10
