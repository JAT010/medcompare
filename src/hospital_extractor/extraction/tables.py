"""Table extraction: convert published HTML tables into entity records.

Hospital sites publish capacity and department tables. Parsing is deliberately
conservative: a table is only mapped to a category when its header vocabulary
is unambiguous, and only the columns that match the category's fields are
used. Tables without a clear header (or without a matching category) are
ignored — nothing is guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup, Tag

from hospital_extractor.extraction.helpers import clean, in_navigation, provenance, text_of
from hospital_extractor.models.department import Department
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.processing.normalize import parse_leading_int

if TYPE_CHECKING:
    from hospital_extractor.extraction.page_analyzer import AnalyzedPage

__all__ = [
    "Table",
    "classify_table",
    "extract_table_records",
    "parse_tables",
]

_MAX_ROWS = 200

_HEADER_TOKENS = (
    "day", "time", "timing", "timings", "hours", "visiting", "opd",
    "department", "doctor", "consultant", "speciality", "name", "contact",
    "phone", "mobile", "email", "address", "location", "facility", "service",
    "capacity", "bed", "beds", "company", "provider", "status", "availability",
    "details", "description", "type", "parameter", "category", "particular",
    "total",
)
_CAPACITY_RE = re.compile(r"\b(capacity|beds?|bed strength|bed count)\b", re.I)
_DEPARTMENT_RE = re.compile(r"\b(department|doctor|consultant|speciality)\b", re.I)
_DEPARTMENT_CONTACT_RE = re.compile(
    r"\b(contact|phone|telephone|mobile|email|timing|opd|location|room)\b", re.I
)


@dataclass(frozen=True)
class Table:
    """One parsed HTML table: a header row plus data rows."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


def parse_tables(soup: BeautifulSoup) -> list[Table]:
    """Parse every content table on *soup* (navigation tables are skipped)."""
    tables: list[Table] = []
    for node in soup.find_all("table"):
        if in_navigation(node):
            continue
        parsed = _parse_table(node)
        if parsed is not None:
            tables.append(parsed)
    return tables


def classify_table(table: Table) -> str | None:
    """Return the canonical category *table* maps to, or ``None``.

    Categories are conservative: the header vocabulary must be unambiguous
    before a table is trusted. ``capacity`` and ``department`` are recognized;
    anything else is ``None``.
    """
    headers = " ".join(table.headers)
    if _CAPACITY_RE.search(headers):
        return "capacity"
    if _DEPARTMENT_RE.search(headers) and _DEPARTMENT_CONTACT_RE.search(headers):
        return "department"
    return None


def extract_table_records(page: AnalyzedPage) -> list[object]:
    """Entity records extracted from every table on *page*."""
    records: list[object] = []
    for table in page.tables:
        category = classify_table(table)
        if category == "capacity":
            record = _capacity_record(table, page)
            if record is not None:
                records.append(record)
        elif category == "department":
            records.extend(_department_record(table, page))
    return records


def _parse_table(node: Tag) -> Table | None:
    rows: list[tuple[str, ...]] = []
    for row in node.find_all("tr"):
        cells = _row_cells(row)
        if len(cells) >= 2:
            rows.append(tuple(cells))
    if not rows:
        return None

    header_index = _header_row_index(rows)
    if header_index is None:
        return Table(headers=("item", "value"), rows=tuple(rows[:_MAX_ROWS]))
    header_row = rows[header_index]
    data_rows = [row for index, row in enumerate(rows) if index != header_index]
    return Table(headers=tuple(header_row), rows=tuple(data_rows[:_MAX_ROWS]))


def _row_cells(row: Tag) -> list[str]:
    result: list[str] = []
    for cell in row.find_all(["th", "td"]):
        value = clean(text_of(cell))
        if value is not None:
            result.append(value)
    return result


def _header_row_index(rows: list[tuple[str, ...]]) -> int | None:
    """Index of the row that looks like a header (or ``None`` when none does).

    The best-scoring row among the first few wins; a score of zero means no
    explicit header row exists, so the caller falls back to generic headers.
    """
    scores = [_header_score(row) for row in rows[:3]]
    best = max(scores, default=0)
    if best == 0:
        return None
    return scores.index(best)


def _header_score(row: tuple[str, ...]) -> int:
    score = 0
    for cell in row:
        lowered = cell.lower()
        for token in _HEADER_TOKENS:
            if re.search(rf"\b{re.escape(token)}\b", lowered):
                score += 1
    return score


def _capacity_record(table: Table, page: AnalyzedPage) -> Infrastructure | None:
    label_idx = _column_index(table.headers, "capacity", "beds", "facility", "type",
                              "parameter", "category", "particular", "item")
    value_idx = _column_index(table.headers, "count", "number", "capacity", "strength",
                              "value", "total", "qty")
    if label_idx is None or value_idx is None:
        label_idx, value_idx = 0, 1
    values: dict[str, int] = {}
    other: list[str] = []
    for row in table.rows:
        label = clean(_cell(row, label_idx))
        value = clean(_cell(row, value_idx))
        if not label or not value:
            continue
        parsed = parse_leading_int(value)
        if parsed is None:
            continue
        field = _capacity_field(label)
        if field is not None:
            values.setdefault(field, parsed)
        else:
            other.append(f"{label}: {value}")
    if not values and not other:
        return None
    return Infrastructure(
        total_beds=values.get("total_beds"),
        icu_beds=values.get("icu_beds"),
        operation_theatres=values.get("operation_theatres"),
        cath_labs=values.get("cath_labs"),
        critical_care_beds=values.get("critical_care_beds"),
        patient_rooms=values.get("patient_rooms"),
        other_capacity="; ".join(other) if other else None,
        **provenance(page.url, title=page.title, method="table", strength="medium",
                      context="; ".join(other) if other else "capacity table"),
    )


def _capacity_field(label: str) -> str | None:
    lowered = label.lower()
    if "total" in lowered and "bed" in lowered:
        return "total_beds"
    if lowered in ("bed", "beds") or "bed strength" in lowered or "bed count" in lowered:
        return "total_beds"
    if "icu" in lowered or "intensive" in lowered:
        return "icu_beds"
    if "operation" in lowered or "theatre" in lowered or "surgical" in lowered:
        return "operation_theatres"
    if "cath" in lowered:
        return "cath_labs"
    if "critical" in lowered:
        return "critical_care_beds"
    if "room" in lowered and "bed" not in lowered:
        return "patient_rooms"
    return None


def _department_record(table: Table, page: AnalyzedPage) -> list[Department]:
    name_idx = _column_index(table.headers, "department", "doctor", "consultant", "speciality",
                             "name")
    if name_idx is None:
        return []
    contact_idx = _column_index(table.headers, "contact", "phone", "telephone", "mobile", "email")
    records: list[Department] = []
    for row in table.rows:
        name = clean(_cell(row, name_idx))
        if not name:
            continue
        contact = clean(_cell(row, contact_idx)) if contact_idx is not None else None
        description = f"Contact: {contact}" if contact else None
        records.append(Department(
            name=name,
            description=description,
            **provenance(page.url, title=page.title, method="table", strength="medium",
                          context="; ".join(cell for cell in row)[:300]),
        ))
    return records


def _column_index(headers: tuple[str, ...], *tokens: str) -> int | None:
    """Index of the first header that starts with any *tokens* (word prefix).

    Prefix matching lets ``timing`` pick up ``Timings`` and ``bed`` pick up
    ``Beds`` without needing every plural variant in the token list.
    """
    patterns = [re.compile(rf"\b{re.escape(token)}", re.I) for token in tokens]
    for index, header in enumerate(headers):
        if any(pattern.search(header) for pattern in patterns):
            return index
    return None


def _cell(row: tuple[str, ...], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index]
