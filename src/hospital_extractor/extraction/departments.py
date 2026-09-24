"""Department extraction.

Sources: links to ``/department/*`` pages, cards and the detail-page H1.
Departments are kept simple (name + optional description); speciality
groupings belong to the Specialities category, and a department that is really
a speciality is not double-counted here.
"""

from __future__ import annotations

import re

from hospital_extractor.extraction.helpers import provenance, text_of
from hospital_extractor.extraction.listing import (
    card_description,
    card_name,
    is_boilerplate,
    link_entries,
)
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.department import Department

__all__ = ["extract_departments"]

_DEPARTMENT_PATH_RE = re.compile(r"/department(?:s)?/[a-z0-9-]+")


def extract_departments(page: AnalyzedPage) -> list[Department]:
    records: list[Department] = []
    records.extend(_from_links(page))
    records.extend(_from_cards(page))
    detail = _from_h1(page)
    if detail is not None:
        records.append(detail)
    return records


def _from_links(page: AnalyzedPage) -> list[Department]:
    records: list[Department] = []
    for name, _href in link_entries(page, _DEPARTMENT_PATH_RE):
        records.append(
            Department(
                name=name,
                **provenance(page.url, title=page.title, method="list", strength="medium"),
            )
        )
    return records


def _from_cards(page: AnalyzedPage) -> list[Department]:
    records: list[Department] = []
    for card in page.cards:
        name = card_name(card)
        if not name:
            continue
        records.append(
            Department(
                name=name,
                description=card_description(card),
                **provenance(page.url, title=page.title, method="card", strength="medium",
                              context=text_of(card)[:300]),
            )
        )
    return records


def _from_h1(page: AnalyzedPage) -> Department | None:
    for level, value in page.headings:
        if level != "h1" or not value or is_boilerplate(value) or len(value) > 120:
            continue
        description = _detail_description(page)
        return Department(
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
    return None
