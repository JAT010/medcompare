"""Weighted suitability scoring using only criteria with usable evidence."""

from __future__ import annotations

from hospital_extractor.recommendation.coverage import build_coverage
from hospital_extractor.recommendation.explanations import build_explanations
from hospital_extractor.recommendation.matching import categorize, confidence_for
from hospital_extractor.recommendation.normalization import observe_criteria
from hospital_extractor.recommendation.preferences import ValidatedPreferences
from hospital_extractor.recommendation.profile import ScoringProfile
from hospital_extractor.recommendation.schemas import (
    CriterionScore,
    HospitalCandidate,
    PreferenceRequest,
    PreferenceSupportState,
    RecommendationResult,
)


def score_candidate(
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    validated: ValidatedPreferences,
    profile: ScoringProfile,
) -> RecommendationResult:
    """Score one eligible candidate deterministically."""
    observations = observe_criteria(
        validated.active_criteria,
        candidate,
        request,
        profile,
    )
    observations = [
        _with_preference_state(
            observation,
            validated.support_states.get(observation.criterion, "supported"),
        )
        for observation in observations
    ]
    coverage = build_coverage(
        observations,
        validated.coverage_weights,
        validated.unsupported_criteria,
        validated.clarification_criteria,
    )

    weighted_sum = 0.0
    denominator = 0.0
    for observation in observations:
        if not observation.usable or observation.score is None:
            continue
        weight = validated.normalized_weights.get(observation.criterion, 0.0)
        weighted_sum += weight * observation.score
        denominator += weight

    suitability = (
        round(min(max(weighted_sum / denominator * 100.0, 0.0), 100.0), 6)
        if denominator > 0.0
        else None
    )
    match_category = categorize(
        suitability,
        coverage.overall,
        request.minimum_evidence_coverage,
        profile,
    )
    confidence = confidence_for(
        match_category,
        has_partially_supported=bool(validated.partially_supported_criteria),
        has_unavailable_preferences=bool(
            validated.unsupported_criteria or validated.clarification_criteria
        ),
    )
    explanations = build_explanations(
        observations,
        suitability,
        coverage.overall,
        match_category,
        confidence,
    )
    hospital_name = candidate.name
    if hospital_name is None and candidate.dataset.hospital is not None:
        hospital_name = candidate.dataset.hospital.name

    return RecommendationResult(
        hospital_id=candidate.hospital_id,
        hospital_name=hospital_name,
        suitability_score=suitability,
        match_category=match_category,
        confidence=confidence,
        criterion_scores=observations,
        evidence_coverage=coverage,
        explanations=explanations,
    )


def _with_preference_state(
    observation: CriterionScore,
    state: PreferenceSupportState,
) -> CriterionScore:
    if state == "requires_clarification":
        return observation.model_copy(
            update={
                "preference_state": state,
                "evidence_state": "requires_clarification",
                "usable": False,
                "score": None,
                "evidence_coverage": 0.0,
                "evidence": [],
            }
        )
    if state == "unsupported":
        return observation.model_copy(
            update={
                "preference_state": state,
                "evidence_state": "unsupported",
                "usable": False,
                "score": None,
                "evidence_coverage": 0.0,
                "evidence": [],
            }
        )
    return observation.model_copy(update={"preference_state": state})
