"""Final master intake models: the exact output rows (25 fields) plus the
provenance trail that explains how each value was decided."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from hospital_extractor.schema.intake import (
    INTAKE_FIELD_COUNT,
    INTAKE_FIELDS,
    boolean_keys,
    intake_field,
)

__all__ = ["BooleanDecision", "IntakeEvidence", "MasterIntake"]

BooleanDecision = Literal["yes", "no", "unknown"]


@dataclass(frozen=True)
class IntakeEvidence:
    """One discrete piece of evidence for a single intake field.

    The evidence trail is cumulative: every item appended survives, so a
    field can carry a yes and a no signal at once (the resolver then decides).
    """

    field_key: str
    decision: Literal["yes", "no"]
    text: str
    source_url: str
    method: str
    strength: float
    label: str | None = None

    @classmethod
    def yes(
        cls,
        field_key: str,
        text: str,
        source_url: str,
        method: str,
        strength: float = 1.0,
        label: str | None = None,
    ) -> IntakeEvidence:
        return cls(
            field_key=field_key,
            decision="yes",
            text=text,
            source_url=source_url,
            method=method,
            strength=strength,
            label=label,
        )

    @classmethod
    def no(
        cls,
        field_key: str,
        text: str,
        source_url: str,
        method: str,
        strength: float = 1.0,
        label: str | None = None,
    ) -> IntakeEvidence:
        return cls(
            field_key=field_key,
            decision="no",
            text=text,
            source_url=source_url,
            method=method,
            strength=strength,
            label=label,
        )


@dataclass
class MasterIntake:
    """The complete 25-field hospital intake, one per hospital.

    Every attribute maps 1:1 to a field in :mod:`hospital_extractor.schema.intake`.
    Value types follow the schema (text/url/number/dropdown); boolean fields
    are resolved by :func:`hospital_extractor.schema.boolean_resolver.resolve`
    and therefore live in a dict keyed by their projection key.
    """

    hospital_name: str | None = None
    pin_code: str | None = None
    logo_image_url: str | None = None
    city: str | None = None
    area_locality: str | None = None
    established_year: int | None = None
    total_beds: int | None = None
    icu_beds: int | None = None
    doctor_count: int | None = None
    nabh_status: str | None = None
    booleans: dict[str, BooleanDecision] = field(default_factory=dict)
    evidence: dict[str, list[IntakeEvidence]] = field(default_factory=dict)

    # --- construction helpers -------------------------------------------

    def add_evidence(self, evidence: IntakeEvidence) -> None:
        self.evidence.setdefault(evidence.field_key, []).append(evidence)

    def decide(self, field_key: str) -> BooleanDecision:
        """Resolved yes/no/unknown for *field_key* (using the same weighting
        rule as the resolver, so a single source of truth survives here)."""
        items = self.evidence.get(field_key, [])
        if not items:
            return "unknown"
        score = sum(item.strength if item.decision == "yes" else -item.strength for item in items)
        if score > 0.0:
            return "yes"
        if score < 0.0:
            return "no"
        return "unknown"

    # --- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Flat, header-ordered dict with the exact 25 output keys plus a
        ``booleans`` composite for the machine-readable layer."""
        result: dict[str, object] = {
            field.key: self._value_of(field.key) for field in INTAKE_FIELDS
        }
        result["booleans"] = self.booleans.copy()
        return result

    def _value_of(self, key: str) -> object:
        field = intake_field(key)
        if field.is_boolean:
            return self.booleans.get(key, "unknown")
        return getattr(self, key)

    # --- validation -------------------------------------------------------

    def check(self) -> list[str]:
        """Structural sanity check; returns a list of problems (empty = ok)."""
        problems: list[str] = []
        for key in self.booleans:
            try:
                found = intake_field(key)
            except KeyError:
                problems.append(f"boolean key {key!r} not in schema")
                continue
            if not found.is_boolean:
                problems.append(f"key {key!r} is not a boolean field")
        for key in self.evidence:
            if key not in boolean_keys():
                problems.append(f"evidence for non-boolean key {key!r}")
        if len(self.to_dict()) != INTAKE_FIELD_COUNT + 1:
            problems.append("serialised row does not carry all fields")
        return problems
