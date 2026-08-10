"""Facility extraction.

Facilities (pharmacy, blood bank, radiology, parking, ICU, OPD, ...) appear in
FAQ answers, labelled sections and facility cards. Extraction reuses the
schema mapper's FAQ vocabulary so a "24x7 Pharmacy" answer lands here while an
"emergency" answer goes to the Emergency category.
"""

from __future__ import annotations

import re

from hospital_extractor.extraction.helpers import provenance, text_of
from hospital_extractor.extraction.listing import card_name
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.facility import Facility
from hospital_extractor.schema.mapper import route_faq_question

__all__ = ["extract_facilities"]

_AVAILABILITY_RE = re.compile(r"24\s*[x/]\s*7|available|open all day", re.I)
_FACILITY_CARD_CLASS_RE = re.compile(
    r"(facility|amenit|pharmacy|lab|blood|parking|cafeteria)", re.I
)


def extract_facilities(page: AnalyzedPage) -> list[Facility]:
    records: list[Facility] = []
    records.extend(_from_faq(page))
    records.extend(_from_sections(page))
    records.extend(_from_cards(page))
    return records


def _from_faq(page: AnalyzedPage) -> list[Facility]:
    records: list[Facility] = []
    for item in page.faq_items:
        if route_faq_question(item.question) != "facilities":
            continue
        records.append(
            Facility(
                name=item.question,
                availability=_availability(item.answer),
                description=item.answer,
                **provenance(page.url, title=page.title, method="faq", strength="medium",
                              context=item.answer[:300]),
            )
        )
    return records


def _from_sections(page: AnalyzedPage) -> list[Facility]:
    records: list[Facility] = []
    for section in page.sections:
        if route_faq_question(section.heading) != "facilities":
            continue
        records.append(
            Facility(
                name=section.heading,
                availability=_availability(section.text),
                description=section.text,
                **provenance(page.url, title=page.title, method="section", strength="medium",
                              context=section.text[:300]),
            )
        )
    return records


def _from_cards(page: AnalyzedPage) -> list[Facility]:
    records: list[Facility] = []
    for card in page.cards:
        classes = " ".join(c for c in card.get("class") or [])
        if not _FACILITY_CARD_CLASS_RE.search(classes):
            continue
        name = card_name(card)
        if not name:
            continue
        records.append(
            Facility(
                name=name,
                **provenance(page.url, title=page.title, method="card", strength="medium",
                              context=text_of(card)[:300]),
            )
        )
    return records


def _availability(text: str) -> str | None:
    if _AVAILABILITY_RE.search(text):
        return "24/7"
    return None
