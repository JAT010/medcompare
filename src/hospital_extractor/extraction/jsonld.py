"""Reusable JSON-LD block parser.

Extracts every ``<script type="application/ld+json">`` block from a page and
flattens the schema.org graphs it contains into individual blocks carrying
their ``@type`` list and raw data. Nested objects are preserved untouched for
later extraction stages.

The parser is deliberately defensive: malformed JSON-LD never terminates a
crawl and is simply skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

__all__ = ["JsonLdBlock", "JsonLdResult", "extract_jsonld"]

_JSONLD_SCRIPT_TYPE = "application/ld+json"

# schema.org types the classifier treats as a hospital-level page.
HOSPITAL_TYPES = frozenset(
    {
        "Hospital",
        "MedicalOrganization",
        "LocalBusiness",
        "Organization",
        "MedicalClinic",
        "HealthClub",
    }
)
DOCTOR_TYPES = frozenset({"Physician", "Person", "MedicalWebPage"})


@dataclass(frozen=True)
class JsonLdBlock:
    """One schema.org node extracted from a page."""

    types: tuple[str, ...]
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_type(self) -> str | None:
        return self.types[0] if self.types else None


@dataclass(frozen=True)
class JsonLdResult:
    """All JSON-LD blocks found on a page."""

    blocks: tuple[JsonLdBlock, ...] = ()

    @property
    def types(self) -> frozenset[str]:
        return frozenset(t for block in self.blocks for t in block.types)

    @property
    def empty(self) -> bool:
        return not self.blocks


def extract_jsonld(html: str) -> JsonLdResult:
    """Parse all JSON-LD blocks out of *html*.

    Handles single objects, arrays of objects, and ``@graph`` documents
    (each graph node becomes its own block). Nodes without an ``@type`` are
    still returned so downstream code can inspect them. Malformed JSON is
    skipped.
    """
    soup = BeautifulSoup(html, "lxml")
    blocks: list[JsonLdBlock] = []
    for script in soup.find_all("script", type=_JSONLD_SCRIPT_TYPE):
        if not script.string:
            continue
        payload = _parse_json(script.string)
        if payload is None:
            continue
        for node in _iter_nodes(payload):
            data = node if isinstance(node, dict) else {}
            blocks.append(JsonLdBlock(types=_node_types(data), data=data))
    return JsonLdResult(blocks=tuple(blocks))


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _iter_nodes(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return [n for item in payload for n in _iter_nodes(item)]
    if isinstance(payload, dict):
        graph = payload.get("@graph")
        if isinstance(graph, list):
            return list(graph)
        return [payload]
    return []


def _node_types(node: dict[str, Any]) -> tuple[str, ...]:
    raw = node.get("@type")
    if isinstance(raw, list):
        return tuple(str(t) for t in raw if isinstance(t, str))
    if isinstance(raw, str):
        return (raw,)
    return ()
