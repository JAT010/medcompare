"""Shared helpers for entity extractors.

Keeps extractors short and consistent: text cleaning, JSON-LD field access,
and provenance construction. Nothing here knows about a specific hospital.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import Tag

from hospital_extractor.extraction.jsonld import JsonLdResult
from hospital_extractor.utils.text import collapse_whitespace

__all__ = [
    "blocks_of",
    "clean",
    "confidence_for",
    "first_block_value",
    "first_text",
    "in_navigation",
    "jsonld_value",
    "provenance",
    "text_of",
]

_NAV_REGION_TAGS = frozenset({"nav", "header", "footer", "aside"})
_NAV_CLASS_RE = re.compile(
    r"\b(nav|navbar|nav-menu|menu|sidebar|footer|cookie|banner|overlay)\b", re.I
)


def in_navigation(node: Tag) -> bool:
    """True when *node* sits inside page chrome (nav/footer/header/aside).

    Walks ancestors, treating ``nav``/``header``/``footer``/``aside`` elements
    and class hints (``navbar``, ``menu``, ``sidebar``, ``cookie``, ...) as
    navigation. Content inside these regions is site chrome, not facts, so the
    extraction ladder skips it.
    """
    current: Tag | None = node
    while current is not None:
        name = current.name
        if name in _NAV_REGION_TAGS:
            return True
        classes = " ".join(c for c in current.get("class") or [])
        if _NAV_CLASS_RE.search(classes):
            return True
        parent = current.parent
        if not isinstance(parent, Tag):
            break
        current = parent
    return False


def clean(value: str | None) -> str | None:
    """Collapse whitespace and trim; ``None`` for blank input."""
    if not value:
        return None
    cleaned = collapse_whitespace(value)
    return cleaned or None


def text_of(node: Tag | None) -> str:
    """Visible text of a BeautifulSoup node, collapsed."""
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def first_text(data: dict[str, Any], *keys: str) -> str | None:
    """First non-empty string value found by trying *keys* in order.

    Supports nested access by dotted keys (e.g. ``"address"``,
    ``"address.streetAddress"``) and list-of-strings values.
    """
    for key in keys:
        value = _lookup(data, key)
        if isinstance(value, str):
            cleaned = clean(value)
            if cleaned:
                return cleaned
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    cleaned = clean(item)
                    if cleaned:
                        return cleaned
    return None


def jsonld_value(data: dict[str, Any], key: str) -> Any:
    """Raw JSON-LD value for *key* (supports dotted nested access)."""
    return _lookup(data, key)


def first_block_value(blocks: list[dict[str, Any]], key: str) -> str | None:
    """First non-empty string value of *key* across JSON-LD *blocks*.

    Like :func:`first_text` but for a single key tried over multiple blocks,
    in order. Handles nested dotted keys and list-of-strings values.
    """
    for block in blocks:
        value = _lookup(block, key)
        if isinstance(value, str):
            cleaned = clean(value)
            if cleaned:
                return cleaned
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    cleaned = clean(item)
                    if cleaned:
                        return cleaned
    return None


def blocks_of(result: JsonLdResult, *types: str) -> list[dict[str, Any]]:
    """Raw data dicts of every JSON-LD block whose @type list matches *types*."""
    wanted = set(types)
    return [block.data for block in result.blocks if wanted.intersection(block.types)]


def provenance(
    url: str,
    *,
    title: str | None = None,
    method: str,
    strength: str,
    confidence: float | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """Provenance keyword arguments shared by every extracted record.

    ``confidence`` defaults to a deterministic, evidence-derived value for the
    given method/strength combination (see :func:`confidence_for`), so every
    record carries a confidence without each extractor having to compute one.
    """
    return {
        "source_url": url,
        "page_url": url,
        "page_title": title,
        "extraction_method": method,
        "source_strength": strength,
        "confidence": confidence
        if confidence is not None
        else confidence_for(method, strength),
        "context": context,
    }


_METHOD_CONFIDENCE = {
    "jsonld": 0.9,
    "semantic_html": 0.8,
    "card": 0.7,
    "table": 0.7,
    "faq": 0.7,
    "section": 0.7,
    "list": 0.6,
    "heading": 0.6,
    "text": 0.4,
}
_STRENGTH_CONFIDENCE = {"high": 0.85, "medium": 0.65, "low": 0.4}
_STRENGTH_DELTA = {"high": 0.05, "medium": 0.0, "low": -0.1}


def confidence_for(
    method: str | None = None, strength: str | None = None
) -> float | None:
    """Evidence-based confidence for a (method, strength) combination.

    Structured data ranks above semantic HTML, which ranks above raw text
    heuristics. The result is an ordinal confidence label mapped to a number,
    not a statistical probability. Returns ``None`` when there is no evidence
    to base a confidence on.
    """
    if method is None and strength is None:
        return None
    if method is None:
        return _STRENGTH_CONFIDENCE.get(strength or "medium")
    base = _METHOD_CONFIDENCE.get(method)
    if base is None:
        base = _STRENGTH_CONFIDENCE.get(strength or "medium", 0.5)
    delta = _STRENGTH_DELTA.get(strength or "medium", 0.0)
    return round(min(0.95, max(0.2, base + delta)), 2)


def _lookup(data: dict[str, Any], key: str) -> Any:
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if isinstance(current, dict) and "@type" in current and len(current) == 1:
            current = None
    return current
