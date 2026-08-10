"""Award extraction.

Awards come from awards/recognition pages: cards and list items. The year is
parsed from an explicit "(YYYY)" or "YYYY" in the item text and stripped from
the name. The awarding organization is kept when it is clearly labelled
("... by National Awards").
"""

from __future__ import annotations

import re

from hospital_extractor.extraction.helpers import clean, provenance, text_of
from hospital_extractor.extraction.listing import card_description, card_name, is_boilerplate
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.award import Award
from hospital_extractor.processing.normalize import parse_year

__all__ = ["extract_awards"]

_ORGANIZATION_HINTS = ("awarded by", "won by", "by ", "presented by", "conferred by")
_CARD_CLASS_RE = re.compile(r"(award|recognit|achievement|honour)", re.I)


def extract_awards(page: AnalyzedPage) -> list[Award]:
    records: list[Award] = []
    records.extend(_from_cards(page))
    records.extend(_from_sections(page))
    return records


def _from_cards(page: AnalyzedPage) -> list[Award]:
    records: list[Award] = []
    for card in page.cards:
        classes = " ".join(c for c in card.get("class") or [])
        if not _CARD_CLASS_RE.search(classes):
            continue
        raw_name = card_name(card)
        if not raw_name:
            continue
        text = text_of(card)
        name, year = _split_year(raw_name)
        if year is None:
            year = parse_year(text)
        records.append(
            Award(
                name=name,
                year=year,
                organization=_organization(text),
                description=card_description(card),
                **provenance(page.url, title=page.title, method="card", strength="medium",
                              context=text[:300]),
            )
        )
    return records


def _from_sections(page: AnalyzedPage) -> list[Award]:
    records: list[Award] = []
    for section in page.sections:
        raw_name = clean(section.heading)
        if not raw_name or is_boilerplate(raw_name) or len(raw_name) > 120:
            continue
        name, year = _split_year(raw_name)
        if year is None:
            year = parse_year(section.text)
        records.append(
            Award(
                name=name,
                year=year,
                organization=_organization(section.text),
                description=section.text,
                **provenance(page.url, title=page.title, method="section", strength="medium",
                              context=section.text[:300]),
            )
        )
    return records


def _split_year(name: str) -> tuple[str, int | None]:
    match = re.search(r"\((\d{4})\)$", name)
    if match:
        return clean(name[: match.start()]) or name, int(match.group(1))
    return name, None


def _organization(text: str) -> str | None:
    for hint in ("awarded by", "won by", "presented by", "conferred by"):
        index = text.lower().find(hint)
        if index >= 0:
            value = clean(text[index + len(hint):].split(".")[0].split(",")[0])
            if value and len(value) < 100:
                return value
    return None
