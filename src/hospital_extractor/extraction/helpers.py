"""Shared helpers for entity extractors.

Keeps extractors short and consistent: text cleaning, JSON-LD field access,
and provenance construction. Nothing here knows about a specific hospital.
"""

from __future__ import annotations

from typing import Any

from bs4 import Tag

from hospital_extractor.extraction.jsonld import JsonLdResult
from hospital_extractor.utils.text import collapse_whitespace

__all__ = [
    "blocks_of",
    "clean",
    "first_block_value",
    "first_text",
    "jsonld_value",
    "provenance",
    "text_of",
]


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
    """Provenance keyword arguments shared by every extracted record."""
    return {
        "source_url": url,
        "page_title": title,
        "extraction_method": method,
        "source_strength": strength,
        "confidence": confidence,
        "context": context,
    }


def _lookup(data: dict[str, Any], key: str) -> Any:
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if isinstance(current, dict) and "@type" in current and len(current) == 1:
            current = None
    return current
