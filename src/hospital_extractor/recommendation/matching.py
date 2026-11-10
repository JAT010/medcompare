"""Match categorization from suitability and evidence coverage."""

from __future__ import annotations

from hospital_extractor.recommendation.profile import ScoringProfile
from hospital_extractor.recommendation.schemas import (
    MatchCategory,
    RecommendationConfidence,
)


def categorize(
    suitability_score: float | None,
    evidence_coverage: float,
    minimum_evidence_coverage: float,
    profile: ScoringProfile,
) -> MatchCategory:
    """Return a conservative, coverage-aware match category.

    Poorly supported candidates are never labelled as strong or good matches,
    even when their partial suitability score is high.
    """
    if suitability_score is None or evidence_coverage < minimum_evidence_coverage:
        return "insufficient_evidence"
    if (
        suitability_score >= profile.strong_match_min_score
        and evidence_coverage >= profile.strong_match_min_coverage
    ):
        return "strong_match"
    if (
        suitability_score >= profile.good_match_min_score
        and evidence_coverage >= profile.good_match_min_coverage
    ):
        return "good_match"
    if suitability_score >= profile.possible_match_min_score:
        return "possible_match"
    return "weak_match"


def confidence_for(
    match_category: MatchCategory,
    *,
    has_partially_supported: bool,
    has_unavailable_preferences: bool,
) -> RecommendationConfidence:
    """Derive a conservative confidence label.

    Unsupported or clarification-required preferences cap confidence at ``low``.
    A recommendation resting only on partial proxy signals can reach ``medium``
    but never ``high``.
    """
    if match_category == "insufficient_evidence":
        return "insufficient"
    if match_category == "strong_match":
        confidence: RecommendationConfidence = "high"
    elif match_category == "good_match":
        confidence = "medium"
    else:
        confidence = "low"

    if has_unavailable_preferences:
        return "low"
    if has_partially_supported and confidence == "high":
        return "medium"
    return confidence
