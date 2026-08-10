"""Speciality extraction.

Sources: ``medicalSpecialty`` JSON-LD, cards, links to ``/speciality/*`` (and
``centre``/``institute`` detail pages), and the detail-page H1. Display names
from H1/anchor text win over URL slugs. Only the speciality itself is stored;
individual doctor or treatment associations belong to those categories.
"""

from __future__ import annotations

import re

from hospital_extractor.extraction.helpers import clean, first_text, provenance, text_of
from hospital_extractor.extraction.listing import (
    card_description,
    card_name,
    is_boilerplate,
    link_entries,
)
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.speciality import Speciality

__all__ = ["extract_specialities"]

_HOSPITAL_TYPES = ("Hospital", "MedicalOrganization", "MedicalClinic", "LocalBusiness")
_SPECIALITY_PATH_RE = re.compile(
    r"/(?:specialit\w*|specialt\w*|centre|center|institute)/[a-z0-9-]+"
)
_H1_CATEGORY_HINTS = ("speciality", "speciality detail", "specialities")


def extract_specialities(page: AnalyzedPage) -> list[Speciality]:
    records: list[Speciality] = []
    records.extend(_from_jsonld(page))
    records.extend(_from_links(page))
    records.extend(_from_cards(page))
    detail = _from_h1(page)
    if detail is not None:
        records.append(detail)
    return records


def _from_jsonld(page: AnalyzedPage) -> list[Speciality]:
    records: list[Speciality] = []
    for block in page.jsonld.blocks:
        if not set(block.types).intersection(_HOSPITAL_TYPES):
            continue
        specialties = block.data.get("medicalSpecialty")
        if isinstance(specialties, str):
            specialties = [specialties]
        if not isinstance(specialties, list):
            continue
        for item in specialties:
            name = None
            if isinstance(item, str):
                name = clean(item)
            elif isinstance(item, dict):
                name = clean(first_text(item, "name"))
            if not name or is_boilerplate(name):
                continue
            records.append(
                Speciality(
                    name=name,
                    **provenance(page.url, title=page.title, method="jsonld", strength="high"),
                )
            )
    return records


def _from_links(page: AnalyzedPage) -> list[Speciality]:
    records: list[Speciality] = []
    for name, _href in link_entries(page, _SPECIALITY_PATH_RE):
        records.append(
            Speciality(
                name=name,
                **provenance(page.url, title=page.title, method="list", strength="medium"),
            )
        )
    return records


def _from_cards(page: AnalyzedPage) -> list[Speciality]:
    records: list[Speciality] = []
    for card in page.cards:
        name = card_name(card)
        if not name:
            continue
        description = card_description(card)
        records.append(
            Speciality(
                name=name,
                description=description,
                **provenance(page.url, title=page.title, method="card", strength="medium",
                              context=text_of(card)[:300]),
            )
        )
    return records


def _from_h1(page: AnalyzedPage) -> Speciality | None:
    for level, value in page.headings:
        if level != "h1" or not value or is_boilerplate(value):
            continue
        if len(value) > 120:
            continue
        description = _detail_description(page)
        return Speciality(
            name=value,
            description=description,
            **provenance(page.url, title=page.title, method="heading", strength="medium",
                          context=description),
        )
    return None


def _detail_description(page: AnalyzedPage) -> str | None:
    for section in page.sections:
        if section.heading.lower() in ("overview", "about", "introduction"):
            return section.text
    for paragraph in page.soup.find_all("p"):
        text = clean(text_of(paragraph))
        if text and len(text) > 40:
            return text[:800]
    return None
