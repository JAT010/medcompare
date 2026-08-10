"""Medical technology extraction.

Sources: links to ``/technology/*`` pages, technology cards, and H3/H2 items on
technology listing pages. Category is set only from an explicit heading label
("Imaging Technology" -> "imaging"); otherwise left ``None``.
"""

from __future__ import annotations

import re

from hospital_extractor.extraction.helpers import clean, provenance, text_of
from hospital_extractor.extraction.listing import (
    card_description,
    card_name,
    is_boilerplate,
    link_entries,
)
from hospital_extractor.extraction.page_analyzer import AnalyzedPage, Section
from hospital_extractor.models.technology import Technology

__all__ = ["extract_technologies"]

_TECH_PATH_RE = re.compile(r"/(?:technology|technolog)(?:ies)?/[a-z0-9-]+")
_CATEGORY_HINTS = (
    ("imaging", ("imaging", "radiology", "mri", "ct", "x-ray")),
    ("robotics", ("robot", "robotic")),
    ("surgical", ("surgical", "operation theatre", "cath lab", "hybrid")),
    ("diagnostics", ("diagnostic", "lab", "pathology")),
    ("radiation", ("radiation", "radiotherapy", "linear accelerator", "gamma knife")),
    ("intensive care", ("icu", "critical care")),
    ("digital", ("digital", "ai", "telemedicine")),
)


def extract_technologies(page: AnalyzedPage) -> list[Technology]:
    records: list[Technology] = []
    records.extend(_from_links(page))
    records.extend(_from_cards(page))
    records.extend(_from_headings(page))
    return records


def _from_links(page: AnalyzedPage) -> list[Technology]:
    records: list[Technology] = []
    for name, _href in link_entries(page, _TECH_PATH_RE):
        records.append(
            Technology(
                name=name,
                **provenance(page.url, title=page.title, method="list", strength="medium"),
            )
        )
    return records


def _from_cards(page: AnalyzedPage) -> list[Technology]:
    records: list[Technology] = []
    for card in page.cards:
        name = card_name(card)
        if not name:
            continue
        records.append(
            Technology(
                name=name,
                description=card_description(card),
                **provenance(page.url, title=page.title, method="card", strength="medium",
                              context=text_of(card)[:300]),
            )
        )
    return records


def _from_headings(page: AnalyzedPage) -> list[Technology]:
    records: list[Technology] = []
    section_index = 0
    for level, value in page.headings:
        if level not in ("h2", "h3") or not value or is_boilerplate(value) or len(value) > 120:
            continue
        if _looks_like_technology(value):
            section = page.sections[section_index] if section_index < len(page.sections) else None
            description = section.text if section and _same_heading(section, value) else None
            records.append(
                Technology(
                    name=value,
                    category=_category_of(value),
                    description=description,
                    **provenance(page.url, title=page.title, method="heading", strength="low"),
                )
            )
        section_index += 1
    return records


def _looks_like_technology(text: str) -> bool:
    lowered = text.lower()
    return bool(
        re.search(
            r"\b(mri|ct\s*scan|x-?ray|ultrasound|da\s*vinci|pet\s*scan|cath|linear\s+accelerator"
            r"|gamma\s+knife|robot|endoscop|3\s*t\s*mri|1\.5\s*t|mammograph|angio)\b",
            lowered,
        )
        or re.search(r"\b(technology|system|scanner|machine|equipment)\b", lowered)
    )


def _category_of(text: str) -> str | None:
    lowered = text.lower()
    for category, hints in _CATEGORY_HINTS:
        if any(hint in lowered for hint in hints):
            return category
    return None


def _same_heading(section: Section, value: str) -> bool:
    return clean(section.heading) == clean(value)
