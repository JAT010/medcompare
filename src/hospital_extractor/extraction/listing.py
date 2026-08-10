"""Shared helpers for category listing extraction.

Specialities, departments, treatments, procedures, technologies, facilities,
services, awards and accreditations are extracted from the same shapes: cards,
links to detail pages, headings and section text. This module keeps those
extractors short and consistent. It knows nothing about a specific hospital.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import Tag

from hospital_extractor.extraction.helpers import clean, text_of
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.utils.urls import resolve_url

__all__ = ["BOILERPLATE_WORDS", "card_description", "card_name", "is_boilerplate", "link_entries"]

# Words that mark a heading/anchor as a generic listing label rather than an
# item name ("Our Specialities", "View All Doctors", "Read More").
BOILERPLATE_WORDS = frozenset(
    {
        "about", "about us", "read more", "learn more", "know more", "view all", "see all",
        "view details", "book appointment", "our specialities", "our specialties",
        "specialities", "specialties", "departments", "doctors", "find a doctor",
        "our doctors", "treatments", "conditions", "procedures", "technologies",
        "technology", "facilities", "services", "patient services", "programs",
        "programmes", "awards", "accreditations", "recognition", "achievements",
        "contact", "contact us", "reach us", "faq", "frequently asked questions",
        "home", "hospital", "our hospital", "overview", "visit us", "location",
        "emergency", "insurance", "tpa", "cashless", "health check", "wellness",
        "international patient", "our team", "team", "specialist", "specialists",
    }
)

_BOILERPLATE_RE = re.compile(
    r"^(read more|learn more|know more|view all|see all|view details|book) ", re.I
)


def is_boilerplate(text: str) -> bool:
    """Return True when *text* is a generic listing/navigation label."""
    lowered = clean(text) or ""
    lowered = lowered.lower()
    if lowered in BOILERPLATE_WORDS:
        return True
    if _BOILERPLATE_RE.match(lowered):
        return True
    return False


def link_entries(
    page: AnalyzedPage,
    path_re: re.Pattern[str],
    *,
    min_len: int = 3,
    max_len: int = 90,
) -> list[tuple[str, str]]:
    """(anchor_text, resolved_href) for internal links whose path matches *path_re*.

    Empty, boilerplate and duplicate anchor texts are dropped. Anchors whose
    text exceeds *max_len* are treated as nav/card noise.
    """
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for anchor in page.soup.find_all("a", href=True):
        href = anchor["href"]
        if not isinstance(href, str):
            continue
        if not path_re.search(urlsplit(href).path):
            continue
        text = clean(text_of(anchor))
        if not text or not (min_len <= len(text) <= max_len) or is_boilerplate(text):
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        resolved = resolve_url(page.url, href)
        if resolved is not None:
            result.append((text, resolved))
    return result


def card_name(card: Tag) -> str | None:
    """Best item-name candidate inside a card: heading, strong, or named element."""
    for node in card.find_all(["h1", "h2", "h3", "h4"]):
        value = clean(text_of(node))
        if value and not is_boilerplate(value) and len(value) < 120:
            return value
    for node in card.find_all(class_=re.compile(r"(^|\s)(name|title|heading|head)", re.I)):
        value = clean(text_of(node))
        if value and not is_boilerplate(value) and len(value) < 120:
            return value
    for node in card.find_all("strong"):
        value = clean(text_of(node))
        if value and not is_boilerplate(value) and len(value) < 120:
            return value
    return None


def card_description(card: Tag) -> str | None:
    """Short description text inside a card (excludes the name element)."""
    name = card_name(card)
    for node in card.find_all(["p", "div", "span"]):
        value = clean(text_of(node))
        if not value or value == name or is_boilerplate(value):
            continue
        if len(value) > 240:
            continue
        if len(value) < 20 and not re.search(r"\b(24/7|24x7|24 x 7)\b", value, re.I):
            continue
        return value
    return None
