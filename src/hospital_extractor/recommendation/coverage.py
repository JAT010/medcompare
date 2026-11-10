"""Evidence coverage calculation kept separate from suitability scoring."""

from __future__ import annotations

from collections.abc import Sequence

from hospital_extractor.recommendation.criteria import CriterionKey
from hospital_extractor.recommendation.schemas import (
    CriterionScore,
    EvidenceCoverage,
)


def build_coverage(
    observations: Sequence[CriterionScore],
    coverage_weights: dict[CriterionKey, float],
    unsupported_criteria: Sequence[CriterionKey],
    clarification_criteria: Sequence[CriterionKey],
) -> EvidenceCoverage:
    """Compute coverage against the complete active preference weight.

    Unsupported and clarification-required preferences carry weight but zero
    coverage. Scorable criteria carry their own evidence coverage, which may be
    partial when only some supporting fields were extracted.
    """
    total_weight = sum(coverage_weights.get(item.criterion, 0.0) for item in observations)
    covered_weight = sum(
        coverage_weights.get(item.criterion, 0.0) * item.evidence_coverage
        for item in observations
    )
    overall = covered_weight / total_weight if total_weight > 0.0 else 0.0
    unsupported_weight = sum(
        coverage_weights.get(key, 0.0) for key in unsupported_criteria
    )
    clarification_weight = sum(
        coverage_weights.get(key, 0.0) for key in clarification_criteria
    )
    per_criterion = {item.criterion: item.evidence_coverage for item in observations}
    return EvidenceCoverage(
        overall=round(min(max(overall, 0.0), 1.0), 6),
        covered_weight=round(max(covered_weight, 0.0), 6),
        total_weight=round(max(total_weight, 0.0), 6),
        unsupported_weight=round(max(unsupported_weight, 0.0), 6),
        clarification_weight=round(max(clarification_weight, 0.0), 6),
        per_criterion=per_criterion,
        evaluated_criteria=[item.criterion for item in observations if item.usable],
        unsupported_criteria=list(unsupported_criteria),
        clarification_criteria=list(clarification_criteria),
        missing_criteria=[
            item.criterion
            for item in observations
            if not item.usable
            and item.preference_state not in ("unsupported", "requires_clarification")
        ],
    )
