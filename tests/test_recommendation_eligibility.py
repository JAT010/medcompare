"""Mandatory constraints, candidate modes and authorization tests."""

from __future__ import annotations

from collections.abc import Sequence

from recommendation_fixtures import make_candidate

from hospital_extractor.recommendation.authorization import (
    DenyAllAuthorizer,
    StaticAuthorizer,
)
from hospital_extractor.recommendation.schemas import (
    LocationRequirement,
    MandatoryConstraints,
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


def _mandatory_request(**mandatory: object) -> PreferenceRequest:
    return PreferenceRequest(
        candidate_mode="discover",
        mandatory=MandatoryConstraints(**mandatory),
        preferences=WeightedPreferences(weights={"infrastructure": 100.0}),
    )


def test_mandatory_minimum_beds_excludes_small_hospital() -> None:
    large = make_candidate("alpha", total_beds=600)
    small = make_candidate("beta", total_beds=300)
    request = PreferenceRequest(
        candidate_mode="discover",
        mandatory=MandatoryConstraints(minimum_total_beds=500),
        preferences=WeightedPreferences(weights={"infrastructure": 100.0}),
    )

    response = _service(["alpha", "beta"]).recommend(request, [large, small], user_id=USER)

    assert response.eligible_count == 1
    assert [result.hospital_id for result in response.results] == ["alpha"]
    assert [excluded.hospital_id for excluded in response.excluded] == ["beta"]
    assert "minimum_total_beds" in response.excluded[0].reason


def test_unknown_mandatory_attribute_never_satisfied() -> None:
    candidate = make_candidate("alpha", total_beds=600)
    request = _mandatory_request(minimum_icu_beds=10)

    response = _service(["alpha"]).recommend(request, [candidate], user_id=USER)

    assert response.eligible_count == 0
    assert response.results == []
    check = response.excluded[0].mandatory_checks[0]
    assert check.name == "minimum_icu_beds"
    assert check.status == "unknown"


def test_unsupported_insurance_mandatory_is_unknown_and_excluded() -> None:
    candidate = make_candidate("alpha", city="Fictional City")
    request = _mandatory_request(required_insurance_providers=["Fictional Insurer"])

    response = _service(["alpha"]).recommend(request, [candidate], user_id=USER)

    assert response.eligible_count == 0
    check = response.excluded[0].mandatory_checks[0]
    assert check.name == "required_insurance_providers"
    assert check.status == "unknown"


def test_no_eligible_candidates_returns_empty_results() -> None:
    candidate = make_candidate("alpha", city="Fictional City")
    request = _mandatory_request(required_city="Nowhere")

    response = _service(["alpha"]).recommend(request, [candidate], user_id=USER)

    assert response.eligible_count == 0
    assert response.returned_count == 0
    assert response.results == []
    assert response.excluded[0].mandatory_checks[0].status == "violated"


def test_multiple_mandatory_constraints_report_each_check() -> None:
    candidate = make_candidate("alpha", city="Fictional City", total_beds=300)
    request = _mandatory_request(required_city="Fictional City", minimum_total_beds=500)

    response = _service(["alpha"]).recommend(request, [candidate], user_id=USER)

    assert response.results == []
    checks = {check.name: check.status for check in response.excluded[0].mandatory_checks}
    assert checks["required_city"] == "satisfied"
    assert checks["minimum_total_beds"] == "violated"


def test_selected_mode_scores_only_selected_candidates() -> None:
    alpha = make_candidate("alpha", city="Fictional City", state="Fictional State")
    beta = make_candidate("beta", city="Fictional City", state="Fictional State")
    service = _service(["alpha", "beta"])
    selected = PreferenceRequest(
        candidate_mode="selected",
        selected_hospital_ids=["beta"],
        location=LocationRequirement(city="Fictional City"),
        preferences=WeightedPreferences(weights={"location": 100.0}),
    )

    response = service.recommend(selected, [alpha, beta], user_id=USER)

    assert response.candidates_considered == 1
    assert [result.hospital_id for result in response.results] == ["beta"]


def test_discover_mode_scores_all_authorized_candidates() -> None:
    alpha = make_candidate("alpha", city="Fictional City", state="Fictional State")
    beta = make_candidate("beta", city="Fictional City", state="Fictional State")

    response = _service(["alpha", "beta"]).recommend(
        _location_request(), [alpha, beta], user_id=USER
    )

    assert response.candidates_considered == 2
    assert {result.hospital_id for result in response.results} == {"alpha", "beta"}


def test_unauthorized_candidates_are_excluded() -> None:
    alpha = make_candidate("alpha", city="Fictional City", state="Fictional State")
    beta = make_candidate("beta", city="Fictional City", state="Fictional State")

    response = _service(["alpha"]).recommend(_location_request(), [alpha, beta], user_id=USER)

    assert response.eligible_count == 1
    assert response.results[0].hospital_id == "alpha"
    assert response.excluded[0].hospital_id == "beta"
    assert "not authorized" in response.excluded[0].reason


def test_deny_all_authorizer_excludes_everything() -> None:
    candidate = make_candidate("alpha", city="Fictional City", state="Fictional State")

    response = RecommendationService(DenyAllAuthorizer()).recommend(
        _location_request(), [candidate], user_id=USER
    )

    assert response.eligible_count == 0
    assert response.excluded[0].reason == "not authorized for this user"


def test_radius_mandatory_uses_candidate_coordinates() -> None:
    candidate = make_candidate("alpha", latitude=0.0, longitude=0.0)
    request = PreferenceRequest(
        candidate_mode="discover",
        location=LocationRequirement(latitude=0.0, longitude=0.0, radius_km=10.0),
        mandatory=MandatoryConstraints(max_distance_km=5.0),
        preferences=WeightedPreferences(weights={"location": 100.0}),
    )

    response = _service(["alpha"]).recommend(request, [candidate], user_id=USER)

    assert response.eligible_count == 1
    assert response.excluded == []


def test_radius_mandatory_is_unknown_without_candidate_coordinates() -> None:
    candidate = make_candidate("alpha", city="Fictional City")
    request = PreferenceRequest(
        candidate_mode="discover",
        location=LocationRequirement(latitude=0.0, longitude=0.0, radius_km=10.0),
        mandatory=MandatoryConstraints(max_distance_km=5.0),
        preferences=WeightedPreferences(weights={"location": 100.0}),
    )

    response = _service(["alpha"]).recommend(request, [candidate], user_id=USER)

    assert response.eligible_count == 0
    assert response.excluded[0].mandatory_checks[0].status == "unknown"
