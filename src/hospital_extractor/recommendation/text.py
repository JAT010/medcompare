"""Deterministic text matching shared by normalization and eligibility."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from hospital_extractor.models.base import EntityRecord

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_text(value: str | None) -> str:
    """Case-fold, strip punctuation and collapse whitespace."""
    if not value:
        return ""
    return _NON_ALNUM.sub(" ", value.casefold()).strip()


def term_in_text(term: str, text: str | None) -> bool:
    """True when the normalized *term* appears in the normalized *text*."""
    normalized_term = normalize_text(term)
    if not normalized_term:
        return False
    return normalized_term in normalize_text(text)


def record_matches(record: EntityRecord, term: str, fields: Sequence[str]) -> bool:
    """True when any of *fields* on *record* contains *term*."""
    for field in fields:
        value = getattr(record, field, None)
        if isinstance(value, str) and term_in_text(term, value):
            return True
    return False


def first_matching_record(
    records: Iterable[EntityRecord],
    term: str,
    fields: Sequence[str],
) -> EntityRecord | None:
    """First record matching *term*, preserving the input order."""
    for record in records:
        if record_matches(record, term, fields):
            return record
    return None


def dedupe_terms(terms: Iterable[str]) -> list[str]:
    """Order-preserving, case-insensitive term de-duplication."""
    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = " ".join(term.split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result
