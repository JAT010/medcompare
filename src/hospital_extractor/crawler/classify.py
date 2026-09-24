"""Deterministic, universal page classification.

Classifies crawled pages into the hospital-relevant categories the pipeline
cares about. Signals are gathered from the URL (the first path segment is a
strong structural "section" signal), the page title, H1/H2 headings, JSON-LD
``@type`` values and a shallow scan of visible text.

The token catalogs are generic hospital-website vocabulary, not Max
Healthcare-specific. Classification never relies on URL keywords alone: a
page is only strongly classified when several independent signals agree.
Within a signal group a category is counted at most once: the ``url``,
``section`` and ``jsonld`` groups each count once, and ``title``/``h1``/``text``
are grouped as one page-level voice (title first, at its strongest weight). A
footer that repeats "24x7 Emergency", "ambulance" and "24/7" therefore
contributes a single weak signal and cannot outrank a URL/title identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from hospital_extractor.extraction.jsonld import (
    extract_jsonld,
)

__all__ = [
    "CATEGORIES",
    "LOW_VALUE_CATEGORIES",
    "PageClassification",
    "PageClassifier",
    "is_low_value_category",
]

CATEGORIES = (
    "hospital",
    "doctor",
    "department",
    "speciality",
    "facility",
    "treatment",
    "procedure",
    "technology",
    "accreditation",
    "contact",
    "faq",
    "blog",
    "career",
    "news",
    "booking",
    "unknown",
)

LOW_VALUE_CATEGORIES = frozenset({"blog", "career", "news", "booking"})

# Tie-break order: when two categories score equally, the earlier one wins.
_TIE_BREAK = (
    "accreditation",
    "hospital",
    "doctor",
    "contact",
    "faq",
    "speciality",
    "department",
    "facility",
    "treatment",
    "procedure",
    "technology",
    "booking",
    "career",
    "news",
    "blog",
    "unknown",
)

_URL_TOKEN_CATEGORIES: dict[str, tuple[str, ...]] = {
    "hospital": ("hospital", "about-us", "about", "overview", "hospital-network"),
    "doctor": ("doctor", "physician", "specialist", "find-a-doctor", "our-team"),
    "department": ("department",),
    "speciality": ("specialit", "specialt", "centre", "center", "institute"),
    "facility": ("facilit", "amenit"),
    "treatment": ("treatment", "condition", "disease"),
    "procedure": ("procedure", "surgery", "operation"),
    "technology": ("technolog", "tech"),
    "accreditation": ("accreditation", "accredited", "nabb", "nahh"),
    "contact": ("contact", "reach-us", "locations", "locate"),
    "faq": ("faq", "help", "query"),
    "blog": ("blog",),
    "career": ("career", "jobs", "vacanc"),
    "news": ("news", "press", "media", "press-release"),
    "booking": ("book", "appointment", "schedule", "book-an-appointment"),
}

_TITLE_TOKENS: dict[str, tuple[str, ...]] = {
    "hospital": ("hospital", "about us", "overview", "our hospital"),
    "doctor": ("doctor", "physician", "find a doctor", "our specialists"),
    "department": ("department",),
    "speciality": ("specialit", "specialt", "specialties", "specialities"),
    "facility": ("facilit", "amenit"),
    "treatment": ("treatment", "condition", "disease"),
    "procedure": ("procedure", "surgery"),
    "technology": ("technology", "technologies"),
    "accreditation": ("accreditation", "accredited"),
    "contact": ("contact", "reach us", "location"),
    "faq": ("faq", "frequently asked"),
    "blog": ("blog",),
    "career": ("career", "jobs"),
    "news": ("news", "press release", "media"),
    "booking": ("book appointment", "book an appointment", "schedule appointment"),
}

_JSONLD_TYPES: dict[str, str] = {
    "Hospital": "hospital",
    "MedicalOrganization": "hospital",
    "LocalBusiness": "hospital",
    "Organization": "hospital",
    "MedicalClinic": "hospital",
    "Physician": "doctor",
    "Person": "doctor",
    "FAQPage": "faq",
    "ContactPage": "contact",
}

_URL_SIGNAL_WEIGHT = 0.55
_SECTION_SIGNAL_WEIGHT = 0.9
_TITLE_SIGNAL_WEIGHT = 0.6
_HEADING_SIGNAL_WEIGHT = 0.5
_JSONLD_SIGNAL_WEIGHT = 0.85
_TEXT_SIGNAL_WEIGHT = 0.35
_BREADCRUMB_SIGNAL_WEIGHT = 0.3
_META_SIGNAL_WEIGHT = 0.25
_CONFIDENCE_CAP = 0.95

# Sources whose signals are grouped as one page-level voice in ``_classify``.
_PAGE_SOURCES = frozenset({"title", "h1", "text"})


@dataclass(frozen=True)
class PageClassification:
    """Result of classifying one page."""

    category: str
    confidence: float
    signals: tuple[str, ...]


class PageClassifier:
    """Deterministic classifier for hospital website pages."""

    def from_url(self, url: str) -> PageClassification:
        """Classify *url* from its path tokens alone (pre-fetch scoping).

        The first path segment is a structural "section" signal (``/blogs/``,
        ``/doctor/``, ``/hospital-network/``...) and dominates: a deep slug
        like ``10-breast-cancer-faqs`` must not flip a blog page to ``faq``.
        """
        signals = _section_signals(url)
        signals.extend(_token_signals(_url_signal_tokens(url), _URL_SIGNAL_WEIGHT, "url"))
        return _classify(signals)

    def from_content(self, html: str, url: str) -> PageClassification:
        """Classify a fetched page using URL, title, headings, JSON-LD, text."""
        soup = BeautifulSoup(html, "lxml")
        title = _text(soup.title.string) if soup.title else ""
        headings = [
            _text(node.get_text(" ", strip=True))
            for node in soup.find_all(["h1", "h2"])
            if _text(node.get_text(" ", strip=True))
        ]
        body = _text(soup.get_text(" ", strip=True))[:8000]
        jsonld_types = extract_jsonld(html).types

        signals: list[tuple[str, float, str]] = []
        signals.extend(_section_signals(url))
        signals.extend(_token_signals(_url_signal_tokens(url), _URL_SIGNAL_WEIGHT, "url"))
        signals.extend(_text_signals(title, _TITLE_TOKENS, _TITLE_SIGNAL_WEIGHT, "title"))
        for heading in headings:
            signals.extend(_text_signals(heading, _TITLE_TOKENS, _HEADING_SIGNAL_WEIGHT, "h1"))
        for type_name in jsonld_types:
            category = _JSONLD_TYPES.get(type_name)
            if category:
                signals.append((category, _JSONLD_SIGNAL_WEIGHT, f"jsonld:{type_name}"))
        for segment in _breadcrumb_segments(soup):
            signals.extend(
                _token_signals([segment], _BREADCRUMB_SIGNAL_WEIGHT, "breadcrumb")
            )
        meta = _meta_description(soup)
        if meta:
            signals.extend(_text_signals(meta, _TITLE_TOKENS, _META_SIGNAL_WEIGHT, "meta"))
        signals.extend(_text_signals(body, _TITLE_TOKENS, _TEXT_SIGNAL_WEIGHT, "text"))
        return _classify(signals)


def is_low_value_category(category: str) -> bool:
    """Return True for categories that should be deprioritized by default."""
    return category in LOW_VALUE_CATEGORIES


def _section_signals(url: str) -> list[tuple[str, float, str]]:
    """Strong signal from the first path segment, the structural section.

    ``/blogs/10-breast-cancer-faqs`` -> ``section:blogs``. A deep slug must
    not override the section the URL lives under.
    """
    segment = _first_path_segment(url)
    if not segment:
        return []
    for category, keywords in _URL_TOKEN_CATEGORIES.items():
        if any(keyword in segment for keyword in keywords):
            return [(category, _SECTION_SIGNAL_WEIGHT, f"section:{segment}")]
    return []


def _first_path_segment(url: str) -> str:
    for segment in urlsplit(url).path.split("/"):
        if segment:
            return segment.lower()
    return ""


def _url_signal_tokens(url: str) -> list[str]:
    parts = urlsplit(url)
    segments = [segment.lower() for segment in parts.path.split("/") if segment]
    if parts.query:
        segments.append(parts.query.lower())
    return segments


def _token_signals(tokens: list[str], weight: float, source: str) -> list[tuple[str, float, str]]:
    signals: list[tuple[str, float, str]] = []
    for token in tokens:
        for category, keywords in _URL_TOKEN_CATEGORIES.items():
            if any(keyword in token for keyword in keywords):
                signals.append((category, weight, f"{source}:{token}"))
    return signals


def _text_signals(
    text: str, catalog: dict[str, tuple[str, ...]], weight: float, source: str
) -> list[tuple[str, float, str]]:
    signals: list[tuple[str, float, str]] = []
    lowered = text.lower()
    for category, keywords in catalog.items():
        for keyword in keywords:
            if keyword in lowered:
                signals.append((category, weight, f"{source}:{keyword}"))
    return signals


def _classify(
    signals: list[tuple[str, float, str]],
) -> PageClassification:
    scores: dict[str, float] = {}
    seen: dict[str, list[str]] = {}
    sources: dict[str, set[str]] = {}
    for category, weight, signal in signals:
        if category == "unknown":
            continue
        source = signal.split(":", 1)[0]
        # Title, headings and body are one page-level voice: a term repeated
        # across all three counts once, at its strongest weight (title first).
        group = "page" if source in _PAGE_SOURCES else source
        if group in sources.setdefault(category, set()):
            continue
        sources[category].add(group)
        scores[category] = scores.get(category, 0.0) + weight
        seen.setdefault(category, []).append(signal)

    if not scores:
        return PageClassification(category="unknown", confidence=0.0, signals=())

    best_category = max(scores, key=lambda c: (scores[c], -_tie_rank(c)))
    return PageClassification(
        category=best_category,
        confidence=_confidence(scores[best_category]),
        signals=tuple(seen[best_category]),
    )


def _confidence(score: float) -> float:
    return min(_CONFIDENCE_CAP, score)


def _tie_rank(category: str) -> int:
    try:
        return _TIE_BREAK.index(category)
    except ValueError:
        return len(_TIE_BREAK)


def _text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def _breadcrumb_segments(soup: BeautifulSoup) -> list[str]:
    for container in soup.find_all(class_=re.compile(r"breadcrumb", re.I)):
        text = _text(container.get_text(" ", strip=True))
        if text:
            return [
                part.strip().lower()
                for part in re.split(r"\s*[>/]\s*", text)
                if part.strip()
            ]
    return []


def _meta_description(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta is None:
        return ""
    content = meta.get("content")
    return _text(content) if isinstance(content, str) else ""
