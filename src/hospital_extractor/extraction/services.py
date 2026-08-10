"""Patient service extraction.

Services (international patient care, teleconsult, physiotherapy, billing
assistance, home care, ...) are extracted from FAQ answers and labelled
sections, using the schema mapper's vocabulary so overlapping terms route to
the right category.
"""

from __future__ import annotations

import re

from hospital_extractor.extraction.helpers import clean, provenance
from hospital_extractor.extraction.listing import card_name
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.service import PatientService
from hospital_extractor.schema.mapper import route_faq_question

__all__ = ["extract_services"]

_AVAILABILITY_RE = re.compile(r"24\s*[x/]\s*7|available|open all day", re.I)
_SERVICE_CARD_CLASS_RE = re.compile(
    r"(service|international|teleconsult|rehab|physio|home[- ]care|billing)", re.I
)


def extract_services(page: AnalyzedPage) -> list[PatientService]:
    records: list[PatientService] = []
    records.extend(_from_faq(page))
    records.extend(_from_sections(page))
    records.extend(_from_cards(page))
    return records


def _from_faq(page: AnalyzedPage) -> list[PatientService]:
    records: list[PatientService] = []
    for item in page.faq_items:
        if route_faq_question(item.question) != "patient_services":
            continue
        records.append(
            PatientService(
                name=item.question,
                availability=_availability(item.answer),
                description=item.answer,
                **provenance(page.url, title=page.title, method="faq", strength="medium",
                              context=item.answer[:300]),
            )
        )
    return records


def _from_sections(page: AnalyzedPage) -> list[PatientService]:
    records: list[PatientService] = []
    for section in page.sections:
        if route_faq_question(section.heading) != "patient_services":
            continue
        records.append(
            PatientService(
                name=section.heading,
                availability=_availability(section.text),
                description=section.text,
                **provenance(page.url, title=page.title, method="section", strength="medium",
                              context=section.text[:300]),
            )
        )
    return records


def _from_cards(page: AnalyzedPage) -> list[PatientService]:
    records: list[PatientService] = []
    for card in page.cards:
        classes = " ".join(c for c in card.get("class") or [])
        if not _SERVICE_CARD_CLASS_RE.search(classes):
            continue
        name = card_name(card)
        if not name:
            continue
        records.append(
            PatientService(
                name=name,
                **provenance(page.url, title=page.title, method="card", strength="medium",
                              context=(clean(card.get_text(" ", strip=True)) or "")[:300] or None),
            )
        )
    return records


def _availability(text: str) -> str | None:
    if _AVAILABILITY_RE.search(text):
        return "24/7"
    return None
