"""Text cleaning helpers.

These functions clean extracted text without destroying legitimate source
content: whitespace collapse, HTML artifact removal, unicode-space
normalization and boilerplate-aware deduplication.
"""

from __future__ import annotations

import html
import re

_WHITESPACE_RE = re.compile(r"\s+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Non-breaking and other unicode spaces treated as regular spaces.
_UNICODE_SPACES = {
    "\u00a0",  # NBSP
    "\u200b",  # zero-width space
    "\u2007",  # figure space
    "\u2009",  # thin space
    "\u200a",  # hair space
    "\u202f",  # narrow NBSP
    "\u205f",  # medium mathematical space
    "\u3000",  # ideographic space
    "\ufeff",  # BOM / zero-width no-break space
}


def is_blank(value: str | None) -> bool:
    """Return True for ``None`` or strings containing only whitespace."""
    return value is None or not value.strip()


def normalize_unicode_spaces(value: str) -> str:
    """Replace unicode spaces (NBSP, thin spaces, ...) with plain spaces."""
    if not value:
        return value
    return "".join(" " if ch in _UNICODE_SPACES else ch for ch in value)


def collapse_whitespace(value: str) -> str:
    """Collapse all whitespace runs into single spaces and strip edges."""
    return _WHITESPACE_RE.sub(" ", normalize_unicode_spaces(value)).strip()


def strip_html(value: str) -> str:
    """Remove HTML tags, unescape entities and collapse whitespace."""
    if not value:
        return ""
    without_tags = _HTML_TAG_RE.sub(" ", value)
    return collapse_whitespace(html.unescape(without_tags))


def truncate(value: str, limit: int, ellipsis: str = "...") -> str:
    """Truncate *value* to at most *limit* characters, appending *ellipsis*."""
    if limit <= 0 or len(value) <= limit:
        return value
    return value[: max(0, limit - len(ellipsis))].rstrip() + ellipsis


def dedupe_sequential(items: list[str]) -> list[str]:
    """Remove immediately repeated items while preserving original order.

    Handles the common boilerplate pattern where a paragraph is rendered
    twice (e.g. a hidden "read more" clone of the visible text). Non-adjacent
    duplicates are preserved.
    """
    result: list[str] = []
    previous: str | None = None
    for item in items:
        key = collapse_whitespace(item)
        if not key or (previous is not None and key == previous):
            continue
        previous = key
        result.append(item)
    return result


def clean_text(value: str) -> str:
    """Full cleaning pipeline: unescape, strip tags, collapse whitespace."""
    return strip_html(value)
