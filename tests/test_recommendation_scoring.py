"""Deterministic scoring, coverage and tie-breaking tests."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from recommendation_fixtures import make_candidate

from hospital_extractor.recommendation.authorization import StaticAuthorizer
from hospital_extractor.recommendation.schemas import (
    DepartmentSelection,
    LocationRequirement,
    PreferenceRequest,
    WeightedPreferences,
)
from hospital_extractor.recommendation.service import RecommendationService

USER = "fictional-user"


def _service(hospital_ids: Sequence[str]) -> RecommendationService:
    return RecommendationService(StaticAuthorizer({USER: hospital_ids}))


def _location_request() -> PreferenceRequest:
    return PreferenceRequest(
        candidate_mode="discover",
        location=LocationRequirement(city="Fictional City", state="Fictional State"),
        preferences=WeightedPreferences(weights={"location": 100.0}),
    )


def _single_weight_request(criterion: str) -> PreferenceRequest:
    return PreferenceRequest(
        candidate_mode="discover",
        preferences=WeightedPreferences(weights={criterion: 100.0}),
    )


def test_location_first_prefers_matching_hospital() -> None:
    matching = make_candidate(
        "alpha",
        name="Fictional Alpha Hospital",
        city="Fictional City",
        state="Fictional State",
    )
    non_matching = make_candidate(
        "beta",
        name="Fictional Beta Hospital",
        city="Other City",
        state="Other State",
    )

    response = _service(["alpha", "beta"]).recommend(
        _location_request(), [matching, non_matching], user_id=USER
    )

    assert [result.hospital_id for result in response.results] == ["alpha", "beta"]
    assert response.results[0].suitability_score == pytest.approx(100.0)
    assert response.results[1].suitability_score == pytest.approx(0.0)


def test_doctor_experience_first_prefers_more_experience() -> None:
    senior = make_candidate("alpha", doctor_specs=[("25 years", None, "confirmed")])
    junior = make_candidate("beta", doctor_specs=[("5 years", None, "confirmed")])

    response = _service(["alpha", "beta"]).recommend(
        _single_weight_request("doctor_experience"), [junior, senior], user_id=USER
    )

    assert [result.hospital_id for result in response.results] == ["alpha", "beta"]
    assert response.results[0].suitability_score == pytest.approx(25 / 30 * 100, abs=1e-3)
    assert response.results[1].suitability_score == pytest.approx(5 / 30 * 100, abs=1e-3)


def test_missing_infrastructure_evidence_is_insufficient_not_zero() -> None:
    candidate = make_candidate("alpha", city="Fictional City")

    response = _service(["alpha"]).recommend(
        _single_weight_request("infrastructure"), [candidate], user_id=USER
    )

    result = response.results[0]
    assert result.suitability_score is None
    assert result.match_category == "insufficient_evidence"
    assert result.evidence_coverage.overall == 0.0
    assert result.criterion_scores[0].usable is False
    assert result.criterion_scores[0].score is None


def test_partial_infrastructure_evidence_reduces_coverage() -> None:
    candidate = make_candidate("alpha", total_beds=500)

    response = _service(["alpha"]).recommend(
        _single_weight_request("infrastructure"), [candidate], user_id=USER
    )

    result = response.results[0]
    assert result.suitability_score == pytest.approx(50.0)
    assert result.evidence_coverage.overall == pytest.approx(0.25)
    assert result.evidence_coverage.covered_weight == pytest.approx(25.0)
    assert result.evidence_coverage.total_weight == pytest.approx(100.0)
    assert result.match_category == "insufficient_evidence"


def test_equal_scores_are_tied_by_hospital_id_and_stable() -> None:
    beta = make_candidate("beta", city="Fictional City", state="Fictional State")
    alpha = make_candidate("alpha", city="Fictional City", state="Fictional State")
    service = _service(["alpha", "beta"])

    first = service.recommend(_location_request(), [beta, alpha], user_id=USER)
    second = service.recommend(_location_request(), [beta, alpha], user_id=USER)

    assert [result.hospital_id for result in first.results] == ["alpha", "beta"]
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_repeated_runs_are_reproducible() -> None:
    senior = make_candidate("alpha", doctor_specs=[("25 years", None, "confirmed")])
    junior = make_candidate("beta", doctor_specs=[("5 years", None, "confirmed")])
    request = _single_weight_request("doctor_experience")
    service = _service(["alpha", "beta"])

    first = service.recommend(request, [senior, junior], user_id=USER)
    second = service.recommend(request, [senior, junior], user_id=USER)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_absent_emergency_evidence_is_unknown_not_negative() -> None:
    candidate = make_candidate("alpha", city="Fictional City")

    response = _service(["alpha"]).recommend(
        _single_weight_request("emergency_services"), [candidate], user_id=USER
    )

    score = response.results[0].criterion_scores[0]
    assert score.usable is False
    assert score.score is None
    assert score.evidence_coverage == 0.0


def test_supported_absence_yields_zero_department_score() -> None:
    candidate = make_candidate(
        "alpha",
        departments=["Cardiology"],
        category_coverage={"Departments": "found"},
    )
    request = PreferenceRequest(
        candidate_mode="discover",
        departments=DepartmentSelection(specialities=["Oncology"]),
        preferences=WeightedPreferences(weights={"department_expertise": 100.0}),
    )

    response = _service(["alpha"]).recommend(request, [candidate], user_id=USER)

    score = response.results[0].criterion_scores[0]
    assert score.usable is True
    assert score.score == 0.0
    assert score.evidence_coverage == 1.0
    assert score.evidence_state == "verified_unavailable"


def test_partial_infrastructure_evidence_is_explicit() -> None:
    candidate = make_candidate("alpha", total_beds=500)

    response = _service(["alpha"]).recommend(
        _single_weight_request("infrastructure"), [candidate], user_id=USER
    )

    score = response.results[0].criterion_scores[0]
    assert score.preference_state == "supported"
    assert score.evidence_state == "partial"


def test_all_unsupported_preferences_return_empty_results() -> None:
    candidate = make_candidate("alpha")

    response = _service(["alpha"]).recommend(
        _single_weight_request("affordability"), [candidate], user_id=USER
    )

    assert response.scoring_available is False
    assert response.results == []
    assert response.eligible_count == 0
    assert response.unsupported_criteria == ["affordability"]
    assert response.support_states == {"affordability": "unsupported"}
    assert any("No supported preference" in warning for warning in response.warnings)


def test_requires_clarification_returns_empty_results() -> None:
    candidate = make_candidate("alpha", city="Fictional City")

    response = _service(["alpha"]).recommend(
        _single_weight_request("location"), [candidate], user_id=USER
    )

    assert response.scoring_available is False
    assert response.results == []
    assert response.clarification_required == ["location"]
    assert response.preference_assessments[0].clarification_prompt is not None


def test_unsupported_high_weight_reduces_coverage_and_confidence() -> None:
    candidate = make_candidate("alpha", city="Fictional City", state="Fictional State")
    request = PreferenceRequest(
        candidate_mode="discover",
        location=LocationRequirement(city="Fictional City", state="Fictional State"),
        preferences=WeightedPreferences(weights={"location": 60.0, "affordability": 40.0}),
    )

    response = _service(["alpha"]).recommend(request, [candidate], user_id=USER)

    result = response.results[0]
    assert result.suitability_score == pytest.approx(100.0)
    assert result.evidence_coverage.overall == pytest.approx(0.6)
    assert result.evidence_coverage.unsupported_weight == pytest.approx(40.0)
    assert result.match_category == "good_match"
    assert result.confidence == "low"
    unsupported = next(
        item for item in result.criterion_scores if item.criterion == "affordability"
    )
    assert unsupported.preference_state == "unsupported"
    assert unsupported.evidence_state == "unsupported"
    assert unsupported.score is None


def test_partially_supported_preference_caps_confidence_at_medium() -> None:
    candidate = make_candidate("alpha", doctor_specs=[("25 years", None, "confirmed")])

    response = _service(["alpha"]).recommend(
        _single_weight_request("doctor_experience"), [candidate], user_id=USER
    )

    result = response.results[0]
    assert result.match_category == "strong_match"
    assert result.confidence == "medium"
    assert result.criterion_scores[0].preference_state == "partially_supported"
    assert result.criterion_scores[0].evidence_state == "verified_available"


def test_equal_suitability_prefers_higher_evidence_coverage() -> None:
    partial = make_candidate("alpha", total_beds=500)
    complete = make_candidate("beta", total_beds=500, icu_beds=100)

    response = _service(["alpha", "beta"]).recommend(
        _single_weight_request("infrastructure"), [partial, complete], user_id=USER
    )

    assert [result.hospital_id for result in response.results] == ["beta", "alpha"]
    assert response.results[0].suitability_score == pytest.approx(50.0)
    assert response.results[1].suitability_score == pytest.approx(50.0)
    assert response.results[0].evidence_coverage.overall == pytest.approx(0.5)
    assert response.results[1].evidence_coverage.overall == pytest.approx(0.25)


def test_empty_candidate_set_returns_empty_results() -> None:
    response = _service([]).recommend(_location_request(), [], user_id=USER)

    assert response.scoring_available is True
    assert response.candidates_considered == 0
    assert response.eligible_count == 0
    assert response.returned_count == 0
    assert response.results == []


def test_unknown_and_verified_absence_are_distinct() -> None:
    unknown = make_candidate("alpha")
    verified = make_candidate(
        "beta",
        departments=["Cardiology"],
        category_coverage={"Departments": "found"},
    )
    request = PreferenceRequest(
        candidate_mode="discover",
        departments=DepartmentSelection(specialities=["Oncology"]),
        preferences=WeightedPreferences(weights={"department_expertise": 100.0}),
    )

    response = _service(["alpha", "beta"]).recommend(
        request, [unknown, verified], user_id=USER
    )
    by_id = {result.hospital_id: result for result in response.results}

    unknown_score = by_id["alpha"].criterion_scores[0]
    assert unknown_score.score is None
    assert unknown_score.evidence_state == "unknown"
    assert unknown_score.evidence_coverage == 0.0

    verified_score = by_id["beta"].criterion_scores[0]
    assert verified_score.score == 0.0
    assert verified_score.evidence_state == "verified_unavailable"
    assert verified_score.evidence_coverage == 1.0
