"""Final master hospital intake XLSX export (submission-ready intake form).

Renders the completed :class:`~hospital_extractor.models.intake.MasterIntake`
as a single professionally formatted workbook:

- ``Master Hospital Intake`` — the exact 25 fields in schema order, grouped
  into the two intake-form sections, with the hospital name prominent at the
  top. Booleans are the SAME submission values as the Master CSV (``TRUE`` /
  ``FALSE`` per the configured ``boolean_unknown_policy``), numbers stay
  numeric and the PIN code stays text.
- ``Sources`` — extraction provenance from the finished dataset.
- ``Data Quality`` — metrics, coverage, missing fields, conflicts, boolean
  evidence/submission state (including UNKNOWN -> FALSE conversions) and
  warnings.

The exporter only consumes the already-projected intake plus the existing
quality/provenance models. Nothing here re-scrapes the website, nothing
downloads images and nothing is invented.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.worksheet import Worksheet

from hospital_extractor.config import UNKNOWN_FALSE, UNKNOWN_POLICY_DEFAULT
from hospital_extractor.export.master_csv import master_row_values, submit_boolean
from hospital_extractor.models.intake import MasterIntake
from hospital_extractor.schema.intake import (
    IDENTITY_SECTION,
    INTAKE_FIELDS,
    TAXONOMY_SECTION,
    IntakeField,
)

if TYPE_CHECKING:
    from hospital_extractor.models.dataset import HospitalDataset

__all__ = ["MASTER_XLSX_NAME", "export_master_excel"]

MASTER_XLSX_NAME = "master.xlsx"

MASTER_SHEET = "Master Hospital Intake"
SOURCES_SHEET = "Sources"
QUALITY_SHEET = "Data Quality"

_FIXED_STAMP = datetime(2020, 1, 1, tzinfo=UTC)

_TITLE_FONT = Font(bold=True, size=16, color="FFFFFF")
_TITLE_FILL = PatternFill("solid", start_color="1F4E79", end_color="1F4E79")
_NAME_FONT = Font(bold=True, size=14, color="1F4E79")
_SECTION_FONT = Font(bold=True, size=12, color="FFFFFF")
_SECTION_FILL = PatternFill("solid", start_color="4472C4", end_color="4472C4")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", start_color="8EAADB", end_color="8EAADB")
_THIN = Side(style="thin", color="B4C6E7")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_CENTER = Alignment(horizontal="center", vertical="center")
_LEFT_TOP = Alignment(horizontal="left", vertical="top", wrap_text=True)
_RIGHT_TOP = Alignment(horizontal="right", vertical="top")


def export_master_excel(
    intake: MasterIntake,
    output_dir: Path | str,
    *,
    boolean_unknown_policy: str = UNKNOWN_POLICY_DEFAULT,
    dataset: HospitalDataset | None = None,
) -> Path:
    """Write the master intake workbook to ``master.xlsx`` under *output_dir*.

    Returns the workbook path. *dataset* is optional and only feeds the
    ``Sources`` / ``Data Quality`` worksheets; the 25-field intake table needs
    only the projection.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    if workbook.active is not None:
        workbook.remove(workbook.active)
    workbook.properties.created = _FIXED_STAMP
    workbook.properties.modified = _FIXED_STAMP

    _write_master_sheet(workbook, intake, boolean_unknown_policy)
    _write_sources(workbook, dataset)
    _write_quality(workbook, intake, dataset, boolean_unknown_policy)

    path = out / MASTER_XLSX_NAME
    workbook.save(path)
    return path


# --- Master Hospital Intake sheet -------------------------------------------


def _write_master_sheet(workbook: Workbook, intake: MasterIntake, policy: str) -> Worksheet:
    ws: Worksheet = workbook.create_sheet(MASTER_SHEET)
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 50
    ws.column_dimensions["C"].width = 70

    values = dict(
        zip(
            (field.key for field in INTAKE_FIELDS),
            master_row_values(intake, policy),
            strict=True,
        )
    )

    ws.merge_cells("A1:C1")
    ws["A1"] = "MASTER HOSPITAL INTAKE FORM"
    ws["A1"].font = _TITLE_FONT
    ws["A1"].fill = _TITLE_FILL
    ws["A1"].alignment = _CENTER
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:C2")
    ws["A2"] = intake.hospital_name or ""
    ws["A2"].font = _NAME_FONT
    ws["A2"].alignment = _CENTER
    ws.row_dimensions[2].height = 22

    identity = [field for field in INTAKE_FIELDS if field.section == IDENTITY_SECTION]
    taxonomy = [field for field in INTAKE_FIELDS if field.section == TAXONOMY_SECTION]

    next_row = _section_and_table(ws, 4, IDENTITY_SECTION, identity, values, start_number=1)
    _section_and_table(
        ws,
        next_row + 1,
        TAXONOMY_SECTION,
        taxonomy,
        values,
        start_number=len(identity) + 1,
    )

    ws.freeze_panes = "A4"
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = "1:2"
    ws.print_area = ws.dimensions
    ws.page_margins = PageMargins(left=0.5, right=0.5, top=0.75, bottom=0.75)
    return ws


def _section_and_table(
    ws: Worksheet,
    start_row: int,
    section: str,
    fields: list[IntakeField],
    values: dict[str, object],
    *,
    start_number: int,
) -> int:
    """Write one section title + ``# | Field Name | Value`` table.

    Returns the row index one past the last data row.
    """
    ws.merge_cells(start_row=start_row, start_column=1, end_row=start_row, end_column=3)
    title = ws.cell(row=start_row, column=1, value=section)
    title.font = _SECTION_FONT
    title.fill = _SECTION_FILL
    title.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[start_row].height = 20

    header_row = start_row + 1
    for column, text in enumerate(("#", "Field Name", "Value"), start=1):
        cell = ws.cell(row=header_row, column=column, value=text)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _CENTER
        cell.border = _BORDER

    for offset, field in enumerate(fields, start=1):
        row = header_row + offset
        ws.cell(row=row, column=1, value=start_number + offset - 1).alignment = _CENTER
        ws.cell(row=row, column=2, value=field.label).alignment = _LEFT_TOP
        _write_value_cell(ws, row, values[field.key])
        for column in (1, 2, 3):
            ws.cell(row=row, column=column).border = _BORDER
    return header_row + len(fields)


def _write_value_cell(ws: Worksheet, row: int, value: object) -> None:
    cell = ws.cell(row=row, column=3)
    if isinstance(value, int):
        cell.value = value
        cell.alignment = _RIGHT_TOP
    elif value in (None, ""):
        cell.value = None
        cell.alignment = _LEFT_TOP
    else:
        cell.value = str(value)
        cell.alignment = _LEFT_TOP


# --- Sources sheet -----------------------------------------------------------


def _write_sources(workbook: Workbook, dataset: HospitalDataset | None) -> Worksheet:
    ws: Worksheet = workbook.create_sheet(SOURCES_SHEET)
    header = ["Entity", "Source URL", "Page Title", "Method", "Strength", "Confidence", "Context"]
    for column, text in enumerate(header, start=1):
        cell = ws.cell(row=1, column=column, value=text)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _CENTER
        cell.border = _BORDER

    sources = dataset.sources if dataset is not None else []
    for index, source in enumerate(sources, start=2):
        row = [
            source.entity or "",
            source.source_url or "",
            source.page_title or "",
            source.extraction_method or "",
            source.source_strength or "",
            source.confidence if source.confidence is not None else "",
            source.context or "",
        ]
        for column, value in enumerate(row, start=1):
            cell = ws.cell(row=index, column=column, value=value)
            cell.alignment = _LEFT_TOP
            cell.border = _BORDER
    if not sources:
        ws.cell(row=2, column=1, value="No sources recorded.").alignment = _LEFT_TOP

    for column, width in zip(range(1, 8), (22, 45, 30, 14, 10, 10, 60), strict=True):
        ws.column_dimensions[get_column_letter(column)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(7)}{len(sources) + 1}"
    return ws


# --- Data Quality sheet ------------------------------------------------------


def _write_quality(
    workbook: Workbook,
    intake: MasterIntake,
    dataset: HospitalDataset | None,
    policy: str,
) -> Worksheet:
    ws: Worksheet = workbook.create_sheet(QUALITY_SHEET)
    for column, width in zip(range(1, 8), (38, 60, 18, 18, 18, 18, 46), strict=True):
        ws.column_dimensions[get_column_letter(column)].width = width

    ws.merge_cells("A1:G1")
    ws["A1"] = "Data Quality"
    ws["A1"].font = _TITLE_FONT
    ws["A1"].fill = _TITLE_FILL
    ws["A1"].alignment = _CENTER
    ws.row_dimensions[1].height = 24

    row = 2
    if dataset is None:
        row = _note(ws, row, "No dataset supplied; only boolean evidence is available.")
    else:
        row = _quality_metrics(ws, row, dataset)
        row = _quality_coverage(ws, row, dataset)
        row = _quality_missing(ws, row, dataset)
        row = _quality_conflicts(ws, row, dataset)

    row = _boolean_state(ws, row, intake, policy)

    if dataset is not None:
        row = _string_list(ws, row, "Warnings", dataset.quality.warnings)
        row = _string_list(ws, row, "Failures", dataset.quality.failures)
    return ws


def _section_title(ws: Worksheet, row: int, text: str) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = _SECTION_FONT
    cell.fill = _SECTION_FILL
    cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[row].height = 18
    return row + 1


def _note(ws: Worksheet, row: int, text: str) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
    ws.cell(row=row, column=1, value=text).alignment = _LEFT_TOP
    return row + 1


def _pair_row(ws: Worksheet, row: int, key: str, value: object) -> None:
    ws.cell(row=row, column=1, value=key).alignment = _LEFT_TOP
    display = "-" if value in ("", None) else str(value)
    cell = ws.cell(row=row, column=2, value=display)
    cell.alignment = _LEFT_TOP


def _table_header(ws: Worksheet, row: int, columns: list[str]) -> None:
    for column, text in enumerate(columns, start=1):
        cell = ws.cell(row=row, column=column, value=text)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _CENTER
        cell.border = _BORDER


def _quality_metrics(ws: Worksheet, row: int, dataset: HospitalDataset) -> int:
    row = _section_title(ws, row, "Run metrics")
    quality = dataset.quality
    metrics = [
        ("Pages considered", quality.pages_considered),
        ("Pages extracted", quality.pages_extracted),
        ("Pages classified", quality.pages_classified),
        ("Records extracted", quality.records_extracted),
        ("Records rejected", quality.records_rejected),
        ("Duplicates removed", quality.duplicates_removed),
        ("Conflicts detected", quality.conflicts_detected),
        ("Low-confidence records", quality.low_confidence_records),
        ("Uncertain hospital associations", quality.uncertain_hospital_associations),
        ("Categories discovered", ", ".join(quality.categories_discovered) or "-"),
        ("Categories empty", ", ".join(quality.categories_empty) or "-"),
    ]
    for key, value in metrics:
        _pair_row(ws, row, key, value)
        row += 1
    return row


def _quality_coverage(ws: Worksheet, row: int, dataset: HospitalDataset) -> int:
    row = _section_title(ws, row, "Category coverage")
    _table_header(ws, row, ["Category", "Status"])
    row += 1
    for label, status in sorted(dataset.quality.category_coverage.items()):
        ws.cell(row=row, column=1, value=label).alignment = _LEFT_TOP
        ws.cell(row=row, column=2, value=status).alignment = _LEFT_TOP
        row += 1
    return row


def _quality_missing(ws: Worksheet, row: int, dataset: HospitalDataset) -> int:
    row = _section_title(ws, row, "Missing fields")
    missing = dataset.quality.missing_fields
    if not missing:
        return _note(ws, row, "None.")
    _table_header(ws, row, ["Category", "Missing fields"])
    row += 1
    for label, fields in sorted(missing.items()):
        ws.cell(row=row, column=1, value=label).alignment = _LEFT_TOP
        ws.cell(row=row, column=2, value=", ".join(fields)).alignment = _LEFT_TOP
        row += 1
    return row


def _quality_conflicts(ws: Worksheet, row: int, dataset: HospitalDataset) -> int:
    row = _section_title(ws, row, "Conflicts")
    conflicts = dataset.quality.conflicts
    if not conflicts:
        return _note(ws, row, "None.")
    _table_header(
        ws, row, ["Category", "Field", "Primary", "Alternative", "Resolution", "Source URLs"]
    )
    row += 1
    for conflict in conflicts:
        cells = [
            conflict.category,
            conflict.field,
            conflict.primary or "",
            conflict.alternative or "",
            conflict.resolution_status,
            "; ".join(conflict.source_urls),
        ]
        for column, value in enumerate(cells, start=1):
            ws.cell(row=row, column=column, value=value).alignment = _LEFT_TOP
        row += 1
    return row


def _boolean_state(ws: Worksheet, row: int, intake: MasterIntake, policy: str) -> int:
    row = _section_title(ws, row, "Boolean evidence & submission state")
    _table_header(
        ws,
        row,
        [
            "Field",
            "Resolved",
            "Submitted",
            "Yes evidence",
            "No evidence",
            "Evidence (first signal)",
            "Notes",
        ],
    )
    row += 1
    for field in INTAKE_FIELDS:
        if not field.is_boolean:
            continue
        decision = intake.boolean_state(field.key)
        submitted = submit_boolean(decision, policy)
        items = intake.evidence.get(field.key, [])
        yes = sum(1 for item in items if item.decision == "yes")
        no = sum(1 for item in items if item.decision == "no")
        first = items[0].text if items else ""
        notes = (
            "UNKNOWN converted to FALSE by submission policy"
            if decision == "unknown" and policy == UNKNOWN_FALSE
            else ""
        )
        cells = [field.label, decision, submitted, str(yes), str(no), first, notes]
        for column, value in enumerate(cells, start=1):
            cell = ws.cell(row=row, column=column, value=value)
            cell.alignment = _LEFT_TOP
        row += 1
    return row


def _string_list(ws: Worksheet, row: int, title: str, values: list[str]) -> int:
    row = _section_title(ws, row, title)
    if not values:
        return _note(ws, row, "None.")
    for value in values:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        ws.cell(row=row, column=1, value=value).alignment = _LEFT_TOP
        row += 1
    return row
