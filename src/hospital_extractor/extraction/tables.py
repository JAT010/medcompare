"""Table extraction: convert published HTML tables into entity records.

Hospital sites publish visiting hours, capacity, department contacts and
insurance status as tables. Parsing is deliberately conservative: a table is
only mapped to a category when its header vocabulary is unambiguous, and only
the columns that match the category's fields are used. Tables without a clear
header (or without a matching category) are ignored — nothing is guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup, Tag

from hospital_extractor.extraction.helpers import clean, in_navigation, provenance, text_of
from hospital_extractor.models.department import Department
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.models.insurance import InsuranceProvider
from hospital_extractor.models.service import PatientService
from hospital_extractor.models.visiting import VisitingInformation
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
    "capacity", "bed", "beds", "insurance", "tpa", "cashless", "company",
    "provider", "status", "availability", "details", "description", "type",
    "parameter", "category", "particular", "total",
)
_VISITING_RE = re.compile(r"\b(visiting|opd)\b", re.I)
_CAPACITY_RE = re.compile(r"\b(capacity|beds?|bed strength|bed count)\b", re.I)
_DEPARTMENT_RE = re.compile(r"\b(department|doctor|consultant|speciality)\b", re.I)
_DEPARTMENT_CONTACT_RE = re.compile(
    r"\b(contact|phone|telephone|mobile|email|timing|opd|location|room)\b", re.I
)
_INSURANCE_RE = re.compile(r"\b(insurance|tpa|cashless|provider|company)\b", re.I)
_SERVICE_RE = re.compile(r"\b(service|facility|amenit)\b", re.I)
_SERVICE_DETAIL_RE = re.compile(r"\b(availability|timing|hours|status|24)\b", re.I)
_GOVERNMENT_HINTS = ("cghs", "e-cghs", "ayushman", "pmjay", "eshram", "esic", "govt")


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
    before a table is trusted. ``visiting`` / ``capacity`` / ``department`` /
    ``insurance`` / ``service`` are recognized; anything else is ``None``.
    """
    headers = " ".join(table.headers)
    if _VISITING_RE.search(headers) or (
        _header_has(headers, "day") and _header_has(headers, "time", "timing", "hours")
    ):
        return "visiting"
    if _CAPACITY_RE.search(headers):
        return "capacity"
    if _DEPARTMENT_RE.search(headers) and _DEPARTMENT_CONTACT_RE.search(headers):
        return "department"
    if _INSURANCE_RE.search(headers):
        return "insurance"
    if _SERVICE_RE.search(headers) and _SERVICE_DETAIL_RE.search(headers):
        return "service"
    return None


def extract_table_records(page: AnalyzedPage) -> list[object]:
    """Entity records extracted from every table on *page*."""
    records: list[object] = []
    for table in page.tables:
        category = classify_table(table)
        if category == "visiting":
            record: object | None = _visiting_record(table, page)
            if record is not None:
                records.append(record)
        elif category == "capacity":
            record = _capacity_record(table, page)
            if record is not None:
                records.append(record)
        elif category == "department":
            records.extend(_department_record(table, page))
        elif category == "insurance":
            records.extend(_insurance_record(table, page))
        elif category == "service":
            records.extend(_service_record(table, page))
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


def _visiting_record(table: Table, page: AnalyzedPage) -> VisitingInformation | None:
    day_idx = _column_index(table.headers, "day")
    time_idx = _column_index(table.headers, "time", "timing", "hours")
    if time_idx is None:
        return None
    opd = bool(re.search(r"\bopd\b", " ".join(table.headers), re.I))
    parts: list[str] = []
    for row in table.rows:
        time_value = _cell(row, time_idx)
        if not time_value:
            continue
        day_value = _cell(row, day_idx) if day_idx is not None else ""
        parts.append(f"{day_value}: {time_value}" if day_value else time_value)
    if not parts:
        return None
    value = "; ".join(parts)
    data = {"opd_hours" if opd else "visiting_hours": value}
    return VisitingInformation(
        **data,
        **provenance(page.url, title=page.title, method="table", strength="medium",
                      context=value[:300]),
    )


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


def _insurance_record(table: Table, page: AnalyzedPage) -> list[InsuranceProvider]:
    name_idx = _column_index(table.headers, "insurance", "tpa", "cashless", "provider",
                             "company", "name")
    if name_idx is None:
        return []
    cashless_idx = _column_index(table.headers, "cashless")
    tpa_idx = _column_index(table.headers, "tpa")
    records: list[InsuranceProvider] = []
    for row in table.rows:
        name = clean(_cell(row, name_idx))
        if not name:
            continue
        row_text = " ".join(cell for cell in row)
        cashless = _affirmative(_cell(row, cashless_idx)) if cashless_idx is not None else (
            "Yes" if _has_keyword(row_text, "cashless") else None
        )
        tpa = _affirmative(_cell(row, tpa_idx)) if tpa_idx is not None else (
            "Yes" if _has_keyword(row_text, "tpa") else None
        )
        government = _government_flag(name, row_text)
        if cashless is None and tpa is None and government is None and not _has_keyword(
            name, "insurance"
        ):
            continue
        records.append(InsuranceProvider(
            name=name,
            cashless=cashless,
            tpa=tpa,
            government_scheme=government,
            **provenance(page.url, title=page.title, method="table", strength="medium",
                          context=row_text[:300]),
        ))
    return records


def _service_record(table: Table, page: AnalyzedPage) -> list[PatientService]:
    name_idx = _column_index(table.headers, "service", "facility", "amenit", "name")
    if name_idx is None:
        return []
    avail_idx = _column_index(table.headers, "availability", "timing", "timings", "hours", "status")
    desc_idx = _column_index(table.headers, "description", "details", "remarks")
    records: list[PatientService] = []
    for row in table.rows:
        name = clean(_cell(row, name_idx))
        if not name:
            continue
        availability = None
        if avail_idx is not None:
            availability = _availability(_cell(row, avail_idx))
        description = clean(_cell(row, desc_idx)) if desc_idx is not None else None
        records.append(PatientService(
            name=name,
            availability=availability,
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


def _header_has(headers: str, *tokens: str) -> bool:
    lowered = headers.lower()
    return any(re.search(rf"\b{re.escape(token)}\b", lowered) for token in tokens)


def _has_keyword(text: str, keyword: str) -> bool:
    return bool(re.search(rf"\b{re.escape(keyword)}\b", text, re.I))


def _affirmative(value: str) -> str | None:
    lowered = value.strip().lower()
    if lowered in ("yes", "y", "true") or lowered.startswith("yes "):
        return "Yes"
    return None


def _availability(value: str) -> str | None:
    if re.search(r"24\s*[x/]\s*7|available|open all day", value, re.I):
        return "24/7"
    return None


def _government_flag(*parts: str) -> str | None:
    text = " ".join(part.lower() for part in parts if part)
    return "Yes" if any(hint in text for hint in _GOVERNMENT_HINTS) else None
