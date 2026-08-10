"""Field-aware normalization.

Always: whitespace collapse, HTML artifact removal and unicode-space
normalization (via ``utils/text.py``) and URL normalization (via
``utils/urls.py``). Numeric parsing is conservative: a leading integer of
"650+ bedded" is parsed, the "+" is never fabricated into an exact value.
Phone numbers are trimmed and lightly cleaned, never force-reformatted.
"""

from __future__ import annotations

import re

from hospital_extractor.utils.text import collapse_whitespace, truncate
from hospital_extractor.utils.urls import InvalidURLError, normalize_url

__all__ = [
    "parse_leading_int",
    "parse_year",
    "normalize_email",
    "normalize_phone",
    "normalize_text",
    "normalize_url_value",
    "normalize_field",
    "normalize_record",
]

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_INT_RE = re.compile(r"\b\d[\d,]*\b")
_PHONE_CLEAN_RE = re.compile(r"[^\d+()xX-]")

# Fields that must stay free text and never be integer-parsed.
_TEXT_ONLY_FIELDS = frozenset(
    {"name", "description", "experience", "designation", "speciality", "department", "opd_hours"}
)
_EMAIL_FIELDS = frozenset({"email", "emergency_email"})
_PHONE_FIELDS = frozenset({"phone", "emergency_phone", "appointment_phone", "contact"})
_URL_FIELDS = frozenset({"website", "profile_url", "maps_url", "source_url"})
_YEAR_FIELDS = frozenset({"year"})
_INT_FIELDS = frozenset(
    {
        "established_year",
        "bed_count",
        "doctor_count",
        "staff_count",
        "speciality_count",
        "department_count",
        "total_beds",
        "icu_beds",
        "operation_theatres",
        "cath_labs",
        "critical_care_beds",
        "patient_rooms",
    }
)


def normalize_text(value: str | None) -> str | None:
    """Collapse whitespace and strip; return ``None`` for blank input."""
    if not value:
        return None
    cleaned = collapse_whitespace(value)
    return cleaned or None


def normalize_phone(value: str | None) -> str | None:
    """Trim and collapse whitespace around a phone number.

    Formatting is otherwise preserved (country codes, dashes and spaces stay)
    so a phone number is never silently rewritten.
    """
    return normalize_text(value)


def normalize_email(value: str | None) -> str | None:
    """Lowercase and trim an email address."""
    cleaned = normalize_text(value)
    if not cleaned:
        return None
    return cleaned.lower()


def normalize_url_value(value: str | None) -> str | None:
    """Normalize a URL; return ``None`` when it cannot be normalized."""
    cleaned = normalize_text(value)
    if not cleaned:
        return None
    try:
        return normalize_url(cleaned)
    except InvalidURLError:
        return None


def parse_leading_int(value: str | None) -> int | None:
    """Parse the first integer in *value* ("650+ bedded" -> 650).

    Returns ``None`` when no integer is present. Never rounds or estimates.
    """
    if not value:
        return None
    match = _INT_RE.search(value)
    if not match:
        return None
    try:
        return int(match.group().replace(",", ""))
    except ValueError:
        return None


def parse_year(value: str | None) -> int | None:
    """Extract a 4-digit year in the range 1900-2099."""
    if not value:
        return None
    match = _YEAR_RE.search(value)
    if not match:
        return None
    try:
        return int(match.group())
    except ValueError:
        return None


def normalize_field(category: str, field: str, value: object) -> object:
    """Normalize *value* according to *field* semantics."""
    if value is None:
        return None
    if field in _URL_FIELDS:
        return normalize_url_value(str(value))
    if field in _EMAIL_FIELDS:
        return normalize_email(str(value))
    if field in _PHONE_FIELDS:
        return normalize_phone(str(value))
    if field in _YEAR_FIELDS:
        parsed = parse_year(str(value))
        return parsed if parsed is not None else value
    if field in _INT_FIELDS:
        if isinstance(value, int):
            return value
        parsed = parse_leading_int(str(value))
        return parsed if parsed is not None else value
    if field in _TEXT_ONLY_FIELDS:
        return normalize_text(str(value))
    if isinstance(value, str):
        return normalize_text(value)
    return value


def normalize_record(category: str, record: object) -> object:
    """Normalize every field of *record* in place, returning *record*."""
    model_dump = getattr(record, "model_dump", None)
    if not callable(model_dump):
        return record
    data = model_dump(exclude={"extracted_at"})
    for field_name, value in data.items():
        if value is None:
            continue
        normalized = normalize_field(category, field_name, value)
        try:
            setattr(record, field_name, normalized)
        except (AttributeError, ValueError):  # pragma: no cover - defensive
            continue
    return record


def normalize_description(value: str | None, max_length: int) -> str | None:
    """Clean and truncate a description to *max_length*."""
    cleaned = normalize_text(value)
    if not cleaned:
        return None
    return truncate(cleaned, max_length)
