"""Extraction: parse structured signals from fetched pages and build a dataset."""

from __future__ import annotations

from hospital_extractor.extraction.engine import build_dataset
from hospital_extractor.extraction.jsonld import (
    JsonLdBlock,
    JsonLdResult,
    extract_jsonld,
)
from hospital_extractor.extraction.page_analyzer import (
    AnalyzedPage,
    FaqItem,
    Section,
    analyze_page,
)

__all__ = [
    "AnalyzedPage",
    "FaqItem",
    "JsonLdBlock",
    "JsonLdResult",
    "Section",
    "analyze_page",
    "build_dataset",
    "extract_jsonld",
]
