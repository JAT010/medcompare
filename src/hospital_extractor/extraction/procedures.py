"""Procedure extraction.

Sources: links to ``/procedure/*`` pages, cards, and the detail-page H1.
Procedures share the Treatments shape (name + optional speciality derived from
the URL path).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from hospital_extractor.extraction.helpers import clean, provenance, text_of
from hospital_extractor.extraction.listing import (
    card_description,
    card_name,
    is_boilerplate,
    link_entries,
)
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.procedure import Procedure

__all__ = ["extract_procedures"]

_PROCEDURE_PATH_RE = re.compile(r"/(?:procedure|surgery|operation)(?:s)?/[a-z0-9-]+")
_SPECIALITY_SLUG_RE = re.compile(r"(?:procedure|surgery|operation)(?:s)?/[a-z0-9-]+/([a-z0-9-]+)")


def extract_procedures(page: AnalyzedPage) -> list[Procedure]:
    records: list[Procedure] = []
    records.extend(_from_links(page))
    records.extend(_from_cards(page))
    detail = _from_h1(page)
    if detail is not None:
        records.append(detail)
    return records


def _from_links(page: AnalyzedPage) -> list[Procedure]:
    records: list[Procedure] = []
    for name, href in link_entries(page, _PROCEDURE_PATH_RE):
        records.append(
            Procedure(
                name=name,
                speciality=_slug_to_label(_url_speciality(href)),
                **provenance(page.url, title=page.title, method="list", strength="medium"),
            )
        )
    return records


def _from_cards(page: AnalyzedPage) -> list[Procedure]:
    records: list[Procedure] = []
    for card in page.cards:
        name = card_name(card)
        if not name:
            continue
        records.append(
            Procedure(
                name=name,
                description=card_description(card),
                **provenance(page.url, title=page.title, method="card", strength="medium",
                              context=text_of(card)[:300]),
            )
        )
    return records


def _from_h1(page: AnalyzedPage) -> Procedure | None:
    for level, value in page.headings:
        if level != "h1" or not value or is_boilerplate(value) or len(value) > 120:
            continue
        return Procedure(
            name=value,
            speciality=_slug_to_label(_url_speciality(page.url)),
            description=_detail_description(page),
            **provenance(page.url, title=page.title, method="heading", strength="medium"),
        )
    return None


def _url_speciality(url: str | None) -> str | None:
    if not url:
        return None
    match = _SPECIALITY_SLUG_RE.search(urlsplit(url).path)
    return match.group(1) if match else None


def _slug_to_label(slug: str | None) -> str | None:
    if not slug:
        return None
    return clean(" ".join(part for part in slug.replace("-", " ").split() if part).title()) or None


def _detail_description(page: AnalyzedPage) -> str | None:
    for section in page.sections:
        if section.heading.lower() in ("overview", "about", "introduction"):
            return section.text
    for paragraph in page.soup.find_all("p"):
        text = clean(text_of(paragraph))
        if text and len(text) > 40:
            return text[:800]
    return None
