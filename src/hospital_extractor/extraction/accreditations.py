"""Accreditation extraction.

Accreditations (NABH, JCI, ISO, NABL, ...) come from accreditation pages:
cards, list items and short headings. The issuing year is parsed only from an
explicit 4-digit year in the item text.
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
        records.append(
            Accreditation(
                name=name,
                year=parse_year(text),
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
        records.append(
            Accreditation(
                name=name,
                year=parse_year(text),
                description=text,
                **provenance(page.url, title=page.title, method="section", strength="medium",
                              context=text[:300]),
            )
        )
    return records
