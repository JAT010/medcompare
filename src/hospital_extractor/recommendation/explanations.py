"""Deterministic, template-based explanation generation."""

from __future__ import annotations

from collections.abc import Sequence

from hospital_extractor.recommendation.schemas import (
    CriterionScore,
    MatchCategory,
    RecommendationConfidence,
)


def build_explanations(
    observations: Sequence[CriterionScore],
    suitability_score: float | None,
    evidence_coverage: float,
    match_category: MatchCategory,
    confidence: RecommendationConfidence,
) -> list[str]:
    """Build stable human-readable explanations without any model inference."""
    lines: list[str] = []
    for observation in sorted(observations, key=lambda item: item.criterion):
        if observation.usable and observation.score is not None:
            lines.append(
                f"{observation.label}: score {observation.score:.2f}, "
                f"evidence coverage {observation.evidence_coverage:.0%}, "
                f"preference state {observation.preference_state}, "
                f"evidence state {observation.evidence_state}. "
                f"{observation.explanation}"
            )
        else:
            lines.append(
                f"{observation.label}: not scored "
                f"(preference state {observation.preference_state}, "
                f"evidence state {observation.evidence_state}). "
                f"{observation.explanation}"
            )

    score_text = "not available" if suitability_score is None else f"{suitability_score:.2f}/100"
    lines.append(
        f"Suitability: {score_text}. Evidence coverage: {evidence_coverage:.0%}. "
        f"Match category: {match_category}. Confidence: {confidence}."
    )
    return lines
