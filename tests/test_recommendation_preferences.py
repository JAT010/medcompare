"""Preference validation and weight-normalization tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hospital_extractor.recommendation.preferences import validate_preferences
from hospital_extractor.recommendation.schemas import (
    DepartmentSelection,
    LocationRequirement,
    MandatoryConstraints,
    PreferenceRequest,
    WeightedPreferences,
)


def _request(weights: dict[str, float], **overrides: object) -> PreferenceRequest:
    payload: dict[str, object] = {
        "candidate_mode": "discover",
        "preferences": WeightedPreferences(weights=weights),
    }
    payload.update(overrides)
    return PreferenceRequest(**payload)


def test_unsupported_only_preferences_are_flagged_not_scored() -> None:
    validated = validate_preferences(_request({"affordability": 100.0}))

    assert validated.scorable_criteria == ()
    assert validated.normalized_weights == {}
    assert validated.unsupported_criteria == ("affordability",)
    assert validated.coverage_weights == {"affordability": 100.0}
    assert validated.support_states == {"affordability": "unsupported"}
    assert validated.assessments[0].scorable is False
    assert any("No supported preference" in warning for warning in validated.warnings)


def test_partially_supported_preference_is_scorable() -> None:
    validated = validate_preferences(_request({"doctor_experience": 100.0}))

    assert validated.scorable_criteria == ("doctor_experience",)
    assert validated.partially_supported_criteria == ("doctor_experience",)
    assert validated.support_states["doctor_experience"] == "partially_supported"
    assert validated.normalized_weights == {"doctor_experience": 100.0}
    assert any("partially supported" in warning for warning in validated.warnings)


def test_unsupported_weight_stays_in_coverage_but_not_suitability() -> None:
    request = _request(
        {"location": 2.0, "infrastructure": 3.0, "affordability": 5.0},
        location=LocationRequirement(city="Fictional City"),
    )

    validated = validate_preferences(request)

    assert validated.normalized_weights == {"location": 40.0, "infrastructure": 60.0}
    assert validated.coverage_weights == {
        "affordability": 50.0,
        "infrastructure": 30.0,
        "location": 20.0,
    }
    assert validated.unsupported_criteria == ("affordability",)
    assert sum(validated.normalized_weights.values()) == pytest.approx(100.0)
    assert sum(validated.coverage_weights.values()) == pytest.approx(100.0)
    assert any("Affordability" in warning for warning in validated.warnings)


def test_location_without_requirement_requires_clarification() -> None:
    validated = validate_preferences(_request({"location": 100.0}))

    assert validated.clarification_criteria == ("location",)
    assert validated.support_states["location"] == "requires_clarification"
    assert validated.normalized_weights == {}
    assert validated.assessments[0].clarification_prompt is not None


def test_coordinates_without_radius_require_clarification() -> None:
    request = _request(
        {"location": 100.0},
        location=LocationRequirement(latitude=0.0, longitude=0.0),
    )

    validated = validate_preferences(request)

    assert validated.clarification_criteria == ("location",)
    assert validated.assessments[0].clarification_prompt is not None


def test_department_expertise_without_selection_requires_clarification() -> None:
    validated = validate_preferences(_request({"department_expertise": 100.0}))

    assert validated.clarification_criteria == ("department_expertise",)
    assert validated.normalized_weights == {}


def test_diagnostic_facilities_without_selection_requires_clarification() -> None:
    validated = validate_preferences(_request({"diagnostic_facilities": 100.0}))

    assert validated.clarification_criteria == ("diagnostic_facilities",)
    assert validated.normalized_weights == {}


def test_treatment_facilities_without_selection_requires_clarification() -> None:
    validated = validate_preferences(_request({"treatment_facilities": 100.0}))

    assert validated.clarification_criteria == ("treatment_facilities",)
    assert validated.normalized_weights == {}


def test_supported_preference_with_required_inputs_is_scorable() -> None:
    request = _request(
        {"location": 100.0},
        location=LocationRequirement(city="Fictional City"),
    )

    validated = validate_preferences(request)

    assert validated.scorable_criteria == ("location",)
    assert validated.support_states["location"] == "supported"
    assert validated.clarification_criteria == ()
    assert validated.normalized_weights == {"location": 100.0}


def test_department_expertise_with_selection_is_scorable() -> None:
    request = _request(
        {"department_expertise": 100.0},
        departments=DepartmentSelection(departments=["Cardiology"]),
    )

    validated = validate_preferences(request)

    assert validated.scorable_criteria == ("department_expertise",)
    assert validated.partially_supported_criteria == ("department_expertise",)


def test_zero_weights_are_ignored() -> None:
    request = _request({"location": 0.0, "infrastructure": 10.0})

    validated = validate_preferences(request)

    assert validated.normalized_weights == {"infrastructure": 100.0}
    assert "location" not in validated.active_criteria


def test_negative_and_non_finite_weights_are_rejected() -> None:
    with pytest.raises(ValidationError):
        WeightedPreferences(weights={"location": -1.0})
    with pytest.raises(ValidationError):
        WeightedPreferences(weights={"location": float("nan")})


def test_selected_mode_requires_ids() -> None:
    with pytest.raises(ValidationError):
        _request({"location": 100.0}, candidate_mode="selected")


def test_discover_mode_forbids_ids() -> None:
    with pytest.raises(ValidationError):
        _request(
            {"location": 100.0},
            candidate_mode="discover",
            selected_hospital_ids=["fictional-alpha"],
        )


def test_radius_requires_coordinates() -> None:
    with pytest.raises(ValidationError):
        LocationRequirement(radius_km=10.0)


def test_distance_mandatory_requires_location_coordinates() -> None:
    with pytest.raises(ValidationError):
        _request(
            {"location": 100.0},
            mandatory=MandatoryConstraints(max_distance_km=10.0),
        )
