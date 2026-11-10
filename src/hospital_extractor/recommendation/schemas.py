"""Validated schemas for the preference-based recommendation foundation."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.recommendation.criteria import CriterionKey, CriterionSupport

CandidateMode = Literal["selected", "discover"]
MandatoryStatus = Literal["satisfied", "violated", "unknown"]
MatchCategory = Literal[
    "strong_match",
    "good_match",
    "possible_match",
    "weak_match",
    "insufficient_evidence",
]
PreferenceSupportState = Literal[
    "supported",
    "partially_supported",
    "unsupported",
    "requires_clarification",
]
CriterionEvidenceState = Literal[
    "verified_available",
    "verified_unavailable",
    "partial",
    "unknown",
    "unsupported",
    "requires_clarification",
]
RecommendationConfidence = Literal["high", "medium", "low", "insufficient"]


def _clean_terms(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = " ".join(value.split())
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            cleaned.append(term)
    return cleaned


class LocationRequirement(BaseModel):
    """Text location match and/or radius search around a coordinate."""

    model_config = ConfigDict(extra="forbid")

    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    radius_km: float | None = Field(default=None, gt=0.0, le=500.0)

    @model_validator(mode="after")
    def _validate_coordinates(self) -> LocationRequirement:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        if self.radius_km is not None and self.latitude is None:
            raise ValueError("radius_km requires latitude and longitude")
        return self

    @property
    def has_text_fields(self) -> bool:
        return any(value for value in (self.city, self.state, self.postal_code))


class DepartmentSelection(BaseModel):
    """Requested departments, specialities and treatments."""

    model_config = ConfigDict(extra="forbid")

    departments: list[str] = Field(default_factory=list)
    specialities: list[str] = Field(default_factory=list)
    treatments: list[str] = Field(default_factory=list)

    @field_validator("departments", "specialities", "treatments")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return _clean_terms(value)


class MandatoryConstraints(BaseModel):
    """Hard requirements evaluated before any scoring.

    ``required_insurance_providers`` and ``max_affordability_cost`` are accepted
    for API completeness but are currently unsupported: they always evaluate to
    ``unknown`` and therefore can never be treated as satisfied.
    """

    model_config = ConfigDict(extra="forbid")

    required_city: str | None = None
    required_state: str | None = None
    required_postal_code: str | None = None
    max_distance_km: float | None = Field(default=None, gt=0.0, le=500.0)
    required_departments: list[str] = Field(default_factory=list)
    required_specialities: list[str] = Field(default_factory=list)
    required_treatments: list[str] = Field(default_factory=list)
    minimum_total_beds: int | None = Field(default=None, ge=1)
    minimum_icu_beds: int | None = Field(default=None, ge=1)
    require_emergency_services: bool = False
    required_accreditations: list[str] = Field(default_factory=list)
    required_insurance_providers: list[str] = Field(default_factory=list)
    max_affordability_cost: float | None = Field(default=None, ge=0.0)

    @field_validator(
        "required_departments",
        "required_specialities",
        "required_treatments",
        "required_accreditations",
        "required_insurance_providers",
    )
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return _clean_terms(value)


class WeightedPreferences(BaseModel):
    """Non-negative criterion weights; active weights are normalized to 100."""

    model_config = ConfigDict(extra="forbid")

    weights: dict[CriterionKey, float] = Field(default_factory=dict)

    @field_validator("weights")
    @classmethod
    def _validate_weights(cls, value: dict[CriterionKey, float]) -> dict[CriterionKey, float]:
        for key, weight in value.items():
            if not math.isfinite(weight):
                raise ValueError(f"weight for {key!r} must be finite")
            if weight < 0.0:
                raise ValueError(f"weight for {key!r} must be non-negative")
        if not any(weight > 0.0 for weight in value.values()):
            raise ValueError("at least one weight must be positive")
        return value


class PreferenceAssessment(BaseModel):
    """Request-time support assessment for one active preference.

    ``support`` is the static data support; ``state`` is the effective state for
    this request after checking whether the required inputs were supplied.
    """

    model_config = ConfigDict(extra="forbid")

    criterion: CriterionKey
    label: str
    requested_weight: float = Field(ge=0.0)
    support: CriterionSupport
    state: PreferenceSupportState
    scorable: bool
    suitability_weight: float | None = Field(default=None, ge=0.0, le=100.0)
    coverage_weight: float = Field(ge=0.0, le=100.0)
    explanation: str
    clarification_prompt: str | None = None


class PreferenceRequest(BaseModel):
    """Complete preference request for one recommendation run."""

    model_config = ConfigDict(extra="forbid")

    candidate_mode: CandidateMode
    selected_hospital_ids: list[str] = Field(default_factory=list)
    location: LocationRequirement | None = None
    departments: DepartmentSelection | None = None
    diagnostics: list[str] = Field(default_factory=list)
    mandatory: MandatoryConstraints = Field(default_factory=MandatoryConstraints)
    preferences: WeightedPreferences = Field(default_factory=WeightedPreferences)
    minimum_evidence_coverage: float = Field(default=0.5, ge=0.0, le=1.0)
    max_results: int = Field(default=10, ge=1, le=100)

    @field_validator("selected_hospital_ids", "diagnostics")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return _clean_terms(value)

    @model_validator(mode="after")
    def _validate_mode(self) -> PreferenceRequest:
        if self.candidate_mode == "selected" and not self.selected_hospital_ids:
            raise ValueError("selected mode requires at least one selected_hospital_id")
        if self.candidate_mode == "discover" and self.selected_hospital_ids:
            raise ValueError("discover mode must not specify selected_hospital_ids")
        if self.mandatory.max_distance_km is not None:
            location = self.location
            if location is None or location.latitude is None or location.longitude is None:
                raise ValueError(
                    "mandatory.max_distance_km requires location latitude and longitude"
                )
        return self


class HospitalCandidate(BaseModel):
    """One candidate hospital supplied by the backend registry or selection."""

    model_config = ConfigDict(extra="forbid")

    hospital_id: str
    name: str | None = None
    dataset: HospitalDataset
    registry_metadata: dict[str, str] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    """Provenance reference for one supporting extracted value."""

    model_config = ConfigDict(extra="forbid")

    entity: str
    source_url: str
    field: str | None = None
    value: str | None = None
    extraction_method: str | None = None
    source_strength: str | None = None
    confidence: float | None = None
    context: str | None = None


class CriterionScore(BaseModel):
    """Score, support state and evidence coverage for a single criterion."""

    model_config = ConfigDict(extra="forbid")

    criterion: CriterionKey
    label: str
    support: CriterionSupport
    preference_state: PreferenceSupportState
    evidence_state: CriterionEvidenceState
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    usable: bool
    evidence_coverage: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    explanation: str


class EvidenceCoverage(BaseModel):
    """How much of the complete active preference weight is backed by evidence.

    ``total_weight`` covers every active preference, including unsupported and
    clarification-required preferences. Those contribute weight but no coverage,
    so a high-priority unsupported preference cannot be hidden by normalizing
    only the supported subset to 100.
    """

    model_config = ConfigDict(extra="forbid")

    overall: float = Field(ge=0.0, le=1.0)
    covered_weight: float = Field(ge=0.0, le=100.0)
    total_weight: float = Field(ge=0.0, le=100.0)
    unsupported_weight: float = Field(ge=0.0, le=100.0)
    clarification_weight: float = Field(ge=0.0, le=100.0)
    per_criterion: dict[CriterionKey, float]
    evaluated_criteria: list[CriterionKey]
    unsupported_criteria: list[CriterionKey]
    clarification_criteria: list[CriterionKey]
    missing_criteria: list[CriterionKey]


class MandatoryCheck(BaseModel):
    """One mandatory-constraint evaluation result."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: MandatoryStatus
    detail: str


class RecommendationResult(BaseModel):
    """An eligible candidate with its suitability and evidence trail."""

    model_config = ConfigDict(extra="forbid")

    hospital_id: str
    hospital_name: str | None = None
    rank: int | None = None
    eligible: Literal[True] = True
    suitability_score: float | None = Field(default=None, ge=0.0, le=100.0)
    match_category: MatchCategory
    confidence: RecommendationConfidence
    criterion_scores: list[CriterionScore]
    evidence_coverage: EvidenceCoverage
    explanations: list[str]
    warnings: list[str] = Field(default_factory=list)


class ExcludedCandidate(BaseModel):
    """A candidate removed before scoring, with the authoritative reason."""

    model_config = ConfigDict(extra="forbid")

    hospital_id: str
    hospital_name: str | None = None
    reason: str
    mandatory_checks: list[MandatoryCheck] = Field(default_factory=list)


class RecommendationResponse(BaseModel):
    """Deterministic recommendation response for one request."""

    model_config = ConfigDict(extra="forbid")

    candidate_mode: CandidateMode
    scoring_available: bool
    weights_used: dict[CriterionKey, float]
    coverage_weights: dict[CriterionKey, float]
    unsupported_criteria: list[CriterionKey]
    clarification_required: list[CriterionKey]
    partially_supported_criteria: list[CriterionKey]
    support_states: dict[CriterionKey, PreferenceSupportState]
    preference_assessments: list[PreferenceAssessment]
    warnings: list[str]
    results: list[RecommendationResult]
    excluded: list[ExcludedCandidate]
    candidates_considered: int
    eligible_count: int
    returned_count: int
