"""Canonical schema registry, dynamic discovery and mapping rules."""

from __future__ import annotations

from hospital_extractor.schema.canonical import (
    CANONICAL_CATEGORIES,
    CATEGORY_LABELS,
    CanonicalCategory,
    category_for,
)
from hospital_extractor.schema.discovery import (
    discover_categories,
    empty_categories,
    format_dataset_summary,
)
from hospital_extractor.schema.mapper import FAQ_ROUTES, route_faq_question

__all__ = [
    "CANONICAL_CATEGORIES",
    "CATEGORY_LABELS",
    "CanonicalCategory",
    "FAQ_ROUTES",
    "category_for",
    "discover_categories",
    "empty_categories",
    "format_dataset_summary",
    "route_faq_question",
]
