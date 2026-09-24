"""Typed search and field-comparison query models (P22).

Pure data containers.  Absence of a value is always UNKNOWN — never
silently rewritten to NO or to an empty string that later reads as a
negative claim.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
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
    "UNKNOWN_VALUE",
]

# Display token for a field that is present in the schema but not published
# for this hospital.  Distinct from an empty string or from NO.
UNKNOWN_VALUE = "UNKNOWN"

MIN_COMPARISON_HOSPITALS = 2
MAX_COMPARISON_HOSPITALS = 4
DEFAULT_SEARCH_LIMIT = 50
MAX_SEARCH_LIMIT = 500


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FilterOperator(StrEnum):
    """Deterministic field-aware filter operators."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    AFTER = "after"
    BEFORE = "before"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)


class Filter(BaseModel):
    """One field predicate applied to every candidate dataset.

    ``field`` is a dotted path into the canonical dataset, e.g.
    ``hospital.name``, ``hospital.bed_count``, ``contacts.email``,
    ``specialities.name``, ``source.source_type``, ``quality.records_extracted``.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    field: str = Field(min_length=1, max_length=200)
    operator: FilterOperator
    value: Any = None

    @model_validator(mode="after")
    def _require_value_where_needed(self) -> Filter:
        needs_value = self.operator not in (FilterOperator.EXISTS, FilterOperator.NOT_EXISTS)
        if needs_value and self.value is None:
            raise ValueError(f"operator {self.operator.value!r} requires a value")
        return self


class Query(BaseModel):
    """Structured search over stored hospital datasets."""

    model_config = ConfigDict(str_strip_whitespace=True)

    filters: list[Filter] = Field(default_factory=list, max_length=50)
    hospital_ids: list[str] = Field(default_factory=list, max_length=50)
    text: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=DEFAULT_SEARCH_LIMIT, ge=1, le=MAX_SEARCH_LIMIT)
    offset: int = Field(default=0, ge=0)

    @field_validator("text")
    @classmethod
    def _blank_text_is_none(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value


class EvidenceReference(BaseModel):
    """Provenance for one matched field value."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source_url: str = ""
    source_type: str = "website"
    page_title: str | None = None
    captured_at: datetime | None = None
    confidence: float | None = None
    extraction_method: str | None = None
    source_strength: str | None = None
    context: str | None = None


class FieldMatch(BaseModel):
    """One field of one hospital that satisfied the query."""

    model_config = ConfigDict(str_strip_whitespace=True)

    field: str
    value: Any = None
    unknown: bool = False
    evidence: list[EvidenceReference] = Field(default_factory=list)


class QueryResult(BaseModel):
    """Search result page with provenance-preserving matches."""

    model_config = ConfigDict(str_strip_whitespace=True)

    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_SEARCH_LIMIT)
    offset: int = Field(ge=0)
    matches: list[dict[str, Any]] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    queried_at: datetime = Field(default_factory=_utc_now)


class ComparisonRequest(BaseModel):
    """Field-by-field comparison of 2–4 stored hospitals.

    ``hospital_ids`` are canonical registry identities; the newest stored
    dataset for each is used.  ``fields`` are dotted dataset paths; when
    empty a small default set of overview fields is compared.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    hospital_ids: list[str] = Field(min_length=MIN_COMPARISON_HOSPITALS,
                                    max_length=MAX_COMPARISON_HOSPITALS)
    fields: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("hospital_ids")
    @classmethod
    def _unique_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("hospital_ids must be unique")
        if len(cleaned) < MIN_COMPARISON_HOSPITALS:
            raise ValueError(
                f"at least {MIN_COMPARISON_HOSPITALS} hospitals are required"
            )
        if len(cleaned) > MAX_COMPARISON_HOSPITALS:
            raise ValueError(
                f"at most {MAX_COMPARISON_HOSPITALS} hospitals can be compared"
            )
        return cleaned


class ComparisonFieldCell(BaseModel):
    """One hospital's cell for one compared field.

    ``unknown`` is True when the field was not published for this hospital.
    That is never rendered or stored as NO.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    hospital_id: str
    hospital_name: str | None = None
    dataset_id: str = ""
    value: Any = None
    unknown: bool = False
    source_type: str = "website"
    provider: str | None = None
    captured_at: datetime | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)


class ComparisonField(BaseModel):
    """One compared field across every requested hospital."""

    model_config = ConfigDict(str_strip_whitespace=True)

    field: str
    label: str
    cells: list[ComparisonFieldCell] = Field(default_factory=list)


class ComparisonResult(BaseModel):
    """Side-by-side field comparison — no ranking, no winner."""

    model_config = ConfigDict(str_strip_whitespace=True)

    hospital_ids: list[str] = Field(default_factory=list)
    hospital_names: dict[str, str | None] = Field(default_factory=dict)
    fields: list[ComparisonField] = Field(default_factory=list)
    compared_at: datetime = Field(default_factory=_utc_now)
    note: Literal[
        "Field comparison only. No ranking, scoring, or medical "
        "superiority claim is made."
    ] = (
        "Field comparison only. No ranking, scoring, or medical "
        "superiority claim is made."
    )
