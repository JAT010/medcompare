"""Treatment / condition extraction.

Sources: links to ``/treatment/*``, ``/condition/*`` pages, cards, and the
detail-page H1. The owning speciality is derived from the URL path
(``/treatment/x/cardiac/`` -> "cardiac") or the breadcrumb when published;
never guessed from free text.
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
from hospital_extractor.models.treatment import Treatment

__all__ = ["extract_treatments"]

_TREATMENT_PATH_RE = re.compile(r"/(?:treatment|condition|disease)(?:s)?/[a-z0-9-]+")
_SPECIALITY_SLUG_RE = re.compile(r"(?:treatment|condition|disease)(?:s)?/[a-z0-9-]+/([a-z0-9-]+)")


def extract_treatments(page: AnalyzedPage) -> list[Treatment]:
    records: list[Treatment] = []
    records.extend(_from_links(page))
    records.extend(_from_cards(page))
    detail = _from_h1(page)
    if detail is not None:
        records.append(detail)
    return records


def _from_links(page: AnalyzedPage) -> list[Treatment]:
    records: list[Treatment] = []
    for name, href in link_entries(page, _TREATMENT_PATH_RE):
        records.append(
            Treatment(
                name=name,
                speciality=_slug_to_label(_url_speciality(href)),
                **provenance(page.url, title=page.title, method="list", strength="medium"),
            )
        )
    return records


def _from_cards(page: AnalyzedPage) -> list[Treatment]:
    records: list[Treatment] = []
    for card in page.cards:
        name = card_name(card)
        if not name:
            continue
        records.append(
            Treatment(
                name=name,
                description=card_description(card),
                **provenance(page.url, title=page.title, method="card", strength="medium",
                              context=text_of(card)[:300]),
            )
        )
    return records


def _from_h1(page: AnalyzedPage) -> Treatment | None:
    for level, value in page.headings:
        if level != "h1" or not value or is_boilerplate(value) or len(value) > 120:
            continue
        return Treatment(
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
