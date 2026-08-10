"""Excel (.xlsx) export derived from the finished dataset.

One worksheet per non-empty canonical category (dynamic), with the canonical
fields that are actually populated, a frozen styled header, autofilter and
wrapped text. ``Sources`` and ``Data Quality`` worksheets are appended last.
Nothing here re-scrapes the website.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.quality import DataQuality
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES
from hospital_extractor.schema.discovery import category_records, discover_fields

__all__ = ["export_excel"]

_SHEET_NAME_MAX = 31
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = "4472C4"
_CELL_ALIGNMENT = Alignment(wrap_text=True, vertical="top")
_MAX_COLUMN_WIDTH = 60


def export_excel(dataset: HospitalDataset, path: Path | str) -> Path:
    """Write *dataset* to a dynamic workbook at *path*; returns *path*."""
    workbook = Workbook()
    if workbook.active is not None:
        workbook.remove(workbook.active)

    for category in CANONICAL_CATEGORIES:
        records = category_records(dataset, category)
        if not records:
            continue
        worksheet = workbook.create_sheet(category.label[:_SHEET_NAME_MAX])
        fields = discover_fields(category, records)
        _write_records(worksheet, fields, records)

    if dataset.sources:
        _write_sources(workbook, dataset)

    _write_quality(workbook, dataset.quality)

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output


def _write_records(worksheet: Worksheet, fields: list[str], records: list[object]) -> None:
    worksheet.append(fields)
    for cell in worksheet[1]:
        cell.font = _HEADER_FONT
        cell.fill = PatternFill(start_color=_HEADER_FILL, end_color=_HEADER_FILL, fill_type="solid")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for record in records:
        dump = getattr(record, "model_dump", None)
        data = dict(dump()) if callable(dump) else {}
        worksheet.append([_cell_value(data.get(field)) for field in fields])
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    for index, _field in enumerate(fields, start=1):
        width = max(_MAX_COLUMN_WIDTH, 10)
        for row in worksheet.iter_rows(min_col=index, max_col=index):
            for cell in row:
                value = cell.value
                if isinstance(value, str):
                    width = min(_MAX_COLUMN_WIDTH, max(width, len(value) + 2))
        worksheet.column_dimensions[get_column_letter(index)].width = width
    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = _CELL_ALIGNMENT


def _write_sources(workbook: Workbook, dataset: HospitalDataset) -> None:
    worksheet = workbook.create_sheet("Sources")
    header = ["Entity", "Source URL", "Page Title", "Method", "Strength", "Context"]
    worksheet.append(header)
    for cell in worksheet[1]:
        cell.font = _HEADER_FONT
    for source in dataset.sources:
        worksheet.append(
            [
                source.entity,
                source.source_url,
                source.page_title or "",
                source.extraction_method or "",
                source.source_strength or "",
                source.context or "",
            ]
        )
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    for column, width in zip("ABCDEF", (18, 40, 30, 14, 10, 60), strict=False):
        worksheet.column_dimensions[column].width = width


def _write_quality(workbook: Workbook, quality: DataQuality) -> None:
    worksheet = workbook.create_sheet("Data Quality")
    worksheet.append(["Metric", "Value"])
    for cell in worksheet[1]:
        cell.font = _HEADER_FONT
    metrics = [
        ("Pages considered", quality.pages_considered),
        ("Pages extracted", quality.pages_extracted),
        ("Records extracted", quality.records_extracted),
        ("Records rejected", quality.records_rejected),
        ("Duplicates removed", quality.duplicates_removed),
        ("Conflicts detected", quality.conflicts_detected),
        ("Low-confidence records", quality.low_confidence_records),
        ("Uncertain hospital associations", quality.uncertain_hospital_associations),
        ("Categories discovered", ", ".join(quality.categories_discovered) or "-"),
        ("Categories empty", ", ".join(quality.categories_empty) or "-"),
    ]
    for metric, value in metrics:
        worksheet.append([metric, _cell_value(value)])

    if quality.conflicts:
        worksheet.append([])
        worksheet.append(["Conflicts", ""])
        for cell in worksheet[worksheet.max_row]:
            cell.font = Font(bold=True)
        worksheet.append(["Category", "Field", "Primary", "Alternative", "Source URLs"])
        for cell in worksheet[worksheet.max_row]:
            cell.font = Font(bold=True)
        for conflict in quality.conflicts:
            worksheet.append(
                [
                    conflict.category,
                    conflict.field,
                    conflict.primary or "",
                    conflict.alternative or "",
                    "; ".join(conflict.source_urls),
                ]
            )

    if quality.warnings:
        worksheet.append([])
        worksheet.append(["Warnings", ""])
        for cell in worksheet[worksheet.max_row]:
            cell.font = Font(bold=True)
        for warning in quality.warnings:
            worksheet.append([warning, ""])

    if quality.failures:
        worksheet.append([])
        worksheet.append(["Failures", ""])
        for cell in worksheet[worksheet.max_row]:
            cell.font = Font(bold=True)
        for failure in quality.failures:
            worksheet.append([failure, ""])

    worksheet.column_dimensions["A"].width = 32
    worksheet.column_dimensions["B"].width = 70


def _cell_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    return value
