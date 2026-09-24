"""Accreditation extraction.

Accreditations (NABH, JCI, ISO, NABL, ...) come from accreditation pages:
cards, list items and short headings. A card or section is accepted only when
it names a known accreditation body (NABH/JCI/ISO/NABL) or uses an explicit
accreditation/certification marker, so unrelated ``quality``-styled cards and
generic "Certificate Courses" headings are not misread as accreditations. The
year is bound only to the sentence that names the body, never to an arbitrary
4-digit number elsewhere in the section text.
"""

from __future__ import annotations

import re

from hospital_extractor.extraction.helpers import clean, provenance, text_of
from hospital_extractor.extraction.listing import card_description, card_name, is_boilerplate
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.accreditation import Accreditation
from hospital_extractor.processing.normalize import parse_year

__all__ = ["extract_accreditations"]

_ACCREDITATION_HINTS = re.compile(
    r"\b(nabh|jci|iso\s*\d+|nabl|joint commission|accreditation|certified|certificate)\b", re.I
)
_CARD_CLASS_RE = re.compile(r"(accredit|certif|quality)", re.I)

# Accreditation/certification markers that must appear in a card's name/text.
_ACCREDITATION_MARKER = re.compile(
    r"\b(?:nabh|jci|joint[ -]?commission|nabl|iso\s*\d*|accred\w*|certif\w*)\b", re.I
)

# Specific accreditation bodies; used to anchor sections and to bound the year.
# An ISO revision suffix (e.g. "9001:2015") is consumed so the standard's
# version number is never mistaken for the issuing year.
_BODY_KW_RE = re.compile(
    r"(?:nabh|jci|joint[ -]?commission|nabl|iso\s*\d+)(?:[\s:：-]*\d{4})?",
    re.I,
)
_YEAR_SENTENCE_END_RE = re.compile(r"\b[.!?](?:\s|$)")


def _accreditation_year(text: str) -> int | None:
    """Bind a year only to the sentence that names the accreditation body."""
    match = _BODY_KW_RE.search(text)
    if not match:
        return None
    segment = text[match.end():]
    boundary = _YEAR_SENTENCE_END_RE.search(segment)
    if boundary:
        segment = segment[: boundary.start()]
    return parse_year(segment)


def extract_accreditations(page: AnalyzedPage) -> list[Accreditation]:
    records: list[Accreditation] = []
    records.extend(_from_cards(page))
    records.extend(_from_sections(page))
    return records


def _from_cards(page: AnalyzedPage) -> list[Accreditation]:
    records: list[Accreditation] = []
    for card in page.cards:
        classes = " ".join(c for c in card.get("class") or [])
        if not _CARD_CLASS_RE.search(classes):
            continue
        name = card_name(card)
        if not name:
            continue
        text = text_of(card)
        if not _ACCREDITATION_MARKER.search(f"{name} {text}"):
            continue
        records.append(
            Accreditation(
                name=name,
                year=_accreditation_year(text),
                description=card_description(card),
                **provenance(page.url, title=page.title, method="card", strength="medium",
                              context=text[:300]),
            )
        )
    return records


def _from_sections(page: AnalyzedPage) -> list[Accreditation]:
    records: list[Accreditation] = []
    for section in page.sections:
        if not _ACCREDITATION_HINTS.search(section.heading):
            continue
        name = clean(section.heading)
        if not name or is_boilerplate(name) or len(name) > 120:
            continue
        text = section.text
        if not _BODY_KW_RE.search(f"{section.heading} {text}"):
            continue
        records.append(
            Accreditation(
                name=name,
                year=_accreditation_year(f"{section.heading} {text}"),
                description=text,
                **provenance(page.url, title=page.title, method="section", strength="medium",
                              context=text[:300]),
            )
        )
    return records
