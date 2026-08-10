"""Processing layer: normalization, deduplication, conflict detection, validation."""

from __future__ import annotations

from hospital_extractor.processing.deduplicate import DedupResult, deduplicate_category
from hospital_extractor.processing.normalize import (
    normalize_field,
    normalize_record,
    parse_leading_int,
    parse_year,
)
from hospital_extractor.processing.validate import (
    ValidationIssue,
    ValidationResult,
    validate_records,
)

__all__ = [
    "DedupResult",
    "ValidationIssue",
    "ValidationResult",
    "deduplicate_category",
    "normalize_field",
    "normalize_record",
    "parse_leading_int",
    "parse_year",
    "validate_records",
]
