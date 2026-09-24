"""Search, structured query and field-level hospital comparison (P22).

Consumes stored canonical datasets only — never re-crawls.  Results keep
full provenance (source, capture time, confidence) and treat missing
fields as UNKNOWN, never as NO.  Comparison is side-by-side only: no
ranking, scoring, winner, or medical-superiority claim.
"""

from __future__ import annotations

from hospital_extractor.search.filters import (
    DEFAULT_COMPARISON_FIELDS,
    InvalidQueryError,
    compare_field_values,
    evidence_for,
    label_for_field,
    matches_filter,
    resolve_field_values,
    validate_field_path,
)
from hospital_extractor.search.models import (
    UNKNOWN_VALUE,
    ComparisonField,
    ComparisonFieldCell,
    ComparisonRequest,
    ComparisonResult,
    EvidenceReference,
    FieldMatch,
    Filter,
    FilterOperator,
    Query,
    QueryResult,
)
from hospital_extractor.search.service import (
    HospitalNotFoundError,
    compare_stored_hospitals,
    search_datasets,
)

__all__ = [
    "DEFAULT_COMPARISON_FIELDS",
    "HospitalNotFoundError",
    "InvalidQueryError",
    "UNKNOWN_VALUE",
    "ComparisonField",
    "ComparisonFieldCell",
    "ComparisonRequest",
    "ComparisonResult",
    "EvidenceReference",
    "FieldMatch",
    "Filter",
    "FilterOperator",
    "Query",
    "QueryResult",
    "compare_field_values",
    "compare_stored_hospitals",
    "evidence_for",
    "label_for_field",
    "matches_filter",
    "resolve_field_values",
    "search_datasets",
    "validate_field_path",
]
