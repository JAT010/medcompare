"""Conflict representation for values that disagree across sources.

A conflict records one field whose value differs between two (or more) sources.
The disagreement is never silently dropped: the kept value, the alternative,
all values with their sources, and a resolution status are preserved so the
Data Quality export can surface it later.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["Conflict", "ConflictSource", "conflict_type_for"]

_CAPACITY_FIELDS = frozenset(
    {
        "total_beds",
        "icu_beds",
        "operation_theatres",
        "cath_labs",
        "critical_care_beds",
        "patient_rooms",
        "bed_count",
    }
)
_PHONE_FIELDS = frozenset({"phone", "emergency_phone", "appointment_phone", "contact"})
_ADDRESS_FIELDS = frozenset(
    {"address", "city", "state", "country", "postal_code", "maps_url"}
)
_YEAR_FIELDS = frozenset({"year", "established_year"})
_NAME_FIELDS = frozenset({"name", "hospital_name"})


def conflict_type_for(category: str, field: str) -> str:
    """Classify a conflicting field into a coarse conflict type.

    Known types: ``capacity``, ``phone``, ``address``, ``name``, ``year``,
    ``organization``; anything else is ``value``.
    """
    if field in _CAPACITY_FIELDS:
        return "capacity"
    if field in _PHONE_FIELDS:
        return "phone"
    if field in _ADDRESS_FIELDS:
        return "address"
    if field in _YEAR_FIELDS:
        return "year"
    if field in _NAME_FIELDS:
        return "name"
    if field in ("parent_organization", "organization"):
        return "organization"
    return "value"


class ConflictSource(BaseModel):
    """Provenance of one of the values involved in a conflict."""

    model_config = ConfigDict(str_strip_whitespace=True)

    url: str
    extraction_method: str | None = None
    source_strength: str | None = None


class Conflict(BaseModel):
    """One field whose value differs between sources.

    ``primary`` is the value kept in the dataset; ``alternative`` is the
    conflicting value. ``values`` and ``sources`` preserve every participant
    with its provenance. ``resolution_status`` is ``resolved`` only when one
    value is clearly more authoritative (e.g. structured hospital profile over
    raw text); otherwise it stays ``unresolved`` and no value is fabricated.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    category: str
    field: str
    conflict_type: str = ""
    primary: str | None = None
    alternative: str | None = None
    values: list[str] = Field(default_factory=list)
    preferred_value: str | None = None
    sources: list[ConflictSource] = Field(default_factory=list)
    resolution_status: str = "unresolved"
    # Backwards-compatible aliases used by the current export layer.
    status: str = "conflicting"
    source_urls: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fill_conflict_type(self) -> Conflict:
        if not self.conflict_type:
            self.conflict_type = conflict_type_for(self.category, self.field)
        return self
