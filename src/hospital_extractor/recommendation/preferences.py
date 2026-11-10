"""Preference validation, support assessment and weight normalization.

Every positive requested weight is assessed into an explicit preference state:

- ``supported``: the criterion can be scored from extracted data for this request
- ``partially_supported``: only a narrow proxy signal exists
- ``unsupported``: no extracted data exists
- ``requires_clarification``: data support exists but the request lacks inputs

Unsupported and clarification-required preferences stay in the evidence-coverage
denominator. Only scorable preferences are normalized to 100 for suitability, so
a high-priority unsupported preference can never silently disappear.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from hospital_extractor.recommendation.criteria import (
    CRITERIA_BY_KEY,
    CriterionKey,
    CriterionSpec,
    CriterionSupport,
)
from hospital_extractor.recommendation.schemas import (
    PreferenceAssessment,
    PreferenceRequest,
    PreferenceSupportState,
)


class PreferenceValidationError(ValueError):
    """The request cannot produce a supported, evidence-based recommendation."""


@dataclass(frozen=True)
class ValidatedPreferences:
    requested_weights: dict[CriterionKey, float]
    normalized_weights: dict[CriterionKey, float]
    coverage_weights: dict[CriterionKey, float]
    active_criteria: tuple[CriterionKey, ...]
    scorable_criteria: tuple[CriterionKey, ...]
    unsupported_criteria: tuple[CriterionKey, ...]
    clarification_criteria: tuple[CriterionKey, ...]
    partially_supported_criteria: tuple[CriterionKey, ...]
    support_states: dict[CriterionKey, PreferenceSupportState]
    assessments: tuple[PreferenceAssessment, ...]
    warnings: tuple[str, ...]


def validate_preferences(request: PreferenceRequest) -> ValidatedPreferences:
    """Assess every active preference and build both weight bases.

    ``normalized_weights`` is the suitability basis over scorable preferences.
    ``coverage_weights`` is the evidence-coverage basis over *all* active
    preferences, including unsupported and clarification-required ones.
    """
    requested: dict[CriterionKey, float] = {}
    for key, weight in request.preferences.weights.items():
        if not math.isfinite(weight) or weight < 0.0:
            raise PreferenceValidationError(f"weight for {key!r} must be finite and non-negative")
        if weight == 0.0:
            continue
        requested[key] = weight

    if not requested:
        raise PreferenceValidationError("at least one positive preference weight is required")

    coverage_weights = _normalize_to_100(requested)
    support_states: dict[CriterionKey, PreferenceSupportState] = {}
    prompts: dict[CriterionKey, str | None] = {}
    scorable_requested: dict[CriterionKey, float] = {}
    unsupported: list[CriterionKey] = []
    clarification: list[CriterionKey] = []
    partially_supported: list[CriterionKey] = []
    warnings: list[str] = []

    for key in sorted(requested, key=str):
        spec = CRITERIA_BY_KEY[key]
        state, prompt = _assess_state(key, spec.support, request)
        support_states[key] = state
        prompts[key] = prompt
        if _is_scorable(spec.support, state):
            scorable_requested[key] = requested[key]

        if state == "unsupported":
            unsupported.append(key)
        elif state == "requires_clarification":
            clarification.append(key)
        elif state == "partially_supported":
            partially_supported.append(key)

        warnings.extend(
            _state_warnings(
                spec,
                state,
                requested[key],
                coverage_weights[key],
                prompt,
            )
        )

    normalized = _normalize_to_100(scorable_requested) if scorable_requested else {}
    assessments = tuple(
        _assessment(key, request, support_states[key], prompts[key], normalized, coverage_weights)
        for key in sorted(requested, key=str)
    )

    highest = max(requested, key=lambda key: (requested[key], key))
    if support_states[highest] == "unsupported":
        warnings.append(
            f"Highest-priority preference {CRITERIA_BY_KEY[highest].label} is unsupported; "
            "results cannot be considered reliable for that priority."
        )
    elif support_states[highest] == "requires_clarification":
        warnings.append(
            f"Highest-priority preference {CRITERIA_BY_KEY[highest].label} requires "
            "clarification; provide the missing input before relying on results."
        )

    if not scorable_requested:
        warnings.append(
            "No supported preference can be scored from the current request; provide a "
            "supported preference or clarify the highlighted preferences."
        )

    return ValidatedPreferences(
        requested_weights=requested,
        normalized_weights=normalized,
        coverage_weights=coverage_weights,
        active_criteria=tuple(sorted(requested, key=str)),
        scorable_criteria=tuple(sorted(scorable_requested, key=str)),
        unsupported_criteria=tuple(sorted(unsupported, key=str)),
        clarification_criteria=tuple(sorted(clarification, key=str)),
        partially_supported_criteria=tuple(sorted(partially_supported, key=str)),
        support_states=support_states,
        assessments=assessments,
        warnings=tuple(warnings),
    )


def _assess_state(
    key: CriterionKey,
    support: CriterionSupport,
    request: PreferenceRequest,
) -> tuple[PreferenceSupportState, str | None]:
    if support == "unsupported":
        return "unsupported", None
    prompt = _clarification_prompt(key, request)
    if prompt is not None:
        return "requires_clarification", prompt
    if support == "partially_supported":
        return "partially_supported", None
    return "supported", None


def _clarification_prompt(key: CriterionKey, request: PreferenceRequest) -> str | None:
    if key == "location":
        location = request.location
        if location is None:
            return "supply a city/state/PIN or latitude/longitude with radius_km"
        if not location.has_text_fields and location.radius_km is None:
            return "supply radius_km for the coordinates or add city/state/PIN"
    if key == "department_expertise":
        selection = request.departments
        if selection is None or not (selection.departments or selection.specialities):
            return "select at least one department or speciality"
    if key == "diagnostic_facilities" and not request.diagnostics:
        return "select at least one diagnostic service"
    if key == "treatment_facilities":
        selection = request.departments
        if selection is None or not selection.treatments:
            return "select at least one treatment"
    return None


def _is_scorable(support: CriterionSupport, state: PreferenceSupportState) -> bool:
    return support != "unsupported" and state != "requires_clarification"


def _assessment(
    key: CriterionKey,
    request: PreferenceRequest,
    state: PreferenceSupportState,
    prompt: str | None,
    normalized: dict[CriterionKey, float],
    coverage_weights: dict[CriterionKey, float],
) -> PreferenceAssessment:
    spec = CRITERIA_BY_KEY[key]
    return PreferenceAssessment(
        criterion=key,
        label=spec.label,
        requested_weight=request.preferences.weights[key],
        support=spec.support,
        state=state,
        scorable=_is_scorable(spec.support, state),
        suitability_weight=normalized.get(key),
        coverage_weight=coverage_weights[key],
        explanation=_assessment_explanation(spec, state, prompt),
        clarification_prompt=prompt,
    )


def _state_warnings(
    spec: CriterionSpec,
    state: PreferenceSupportState,
    weight: float,
    coverage_weight: float,
    prompt: str | None,
) -> list[str]:
    if state == "unsupported":
        return [
            f"{spec.label} (requested weight {weight:g}, {coverage_weight:g}% of active "
            "weight) is unsupported and cannot be scored. It remains in evidence coverage. "
            f"Required extraction: {_required_extraction(spec)}."
        ]
    if state == "requires_clarification":
        return [
            f"{spec.label} (requested weight {weight:g}) requires clarification: {prompt}. "
            "It remains in evidence coverage until clarified."
        ]
    if state == "partially_supported":
        return [
            f"{spec.label} is only partially supported; scoring uses a narrow proxy signal. "
            f"{spec.rationale}"
        ]
    return []


def _assessment_explanation(
    spec: CriterionSpec,
    state: PreferenceSupportState,
    prompt: str | None,
) -> str:
    if state == "unsupported":
        return (
            f"{spec.label} cannot be scored because {spec.rationale} "
            f"Required extraction: {_required_extraction(spec)}."
        )
    if state == "requires_clarification":
        return f"{spec.label} cannot be evaluated until clarified: {prompt}."
    if state == "partially_supported":
        return (
            f"{spec.label} uses a partial proxy signal: {spec.rationale} "
            f"Additional extraction needed: {_required_extraction(spec)}."
        )
    return f"{spec.label} is supported by extracted data: {spec.rationale}"


def _required_extraction(spec: CriterionSpec) -> str:
    return "; ".join(spec.required_extraction) or "no extracted data source exists"


def _normalize_to_100(weights: dict[CriterionKey, float]) -> dict[CriterionKey, float]:
    total = sum(weights.values())
    rounded = {key: round(value / total * 100.0, 6) for key, value in weights.items()}
    difference = round(100.0 - sum(rounded.values()), 6)
    if difference:
        largest = max(rounded, key=lambda key: (rounded[key], key))
        rounded[largest] = round(rounded[largest] + difference, 6)
    return rounded
