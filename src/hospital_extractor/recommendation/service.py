"""Deterministic recommendation service orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from hospital_extractor.recommendation.authorization import HospitalAuthorizer
from hospital_extractor.recommendation.eligibility import evaluate_mandatory
from hospital_extractor.recommendation.preferences import validate_preferences
from hospital_extractor.recommendation.profile import ScoringProfile
from hospital_extractor.recommendation.schemas import (
    ExcludedCandidate,
    HospitalCandidate,
    PreferenceRequest,
    RecommendationResponse,
    RecommendationResult,
)
from hospital_extractor.recommendation.scoring import score_candidate


class RecommendationService:
    """Authorization-aware, framework-agnostic recommendation orchestrator."""

    def __init__(
        self,
        authorizer: HospitalAuthorizer,
        profile: ScoringProfile | None = None,
    ) -> None:
        self._authorizer = authorizer
        self._profile = profile or ScoringProfile()

    def recommend(
        self,
        request: PreferenceRequest,
        candidates: Sequence[HospitalCandidate],
        *,
        user_id: str,
    ) -> RecommendationResponse:
        """Validate preferences, authorize candidates, evaluate and rank them."""
        validated = validate_preferences(request)
        indexed = _index_candidates(candidates)
        excluded: list[ExcludedCandidate] = []
        eligible: list[RecommendationResult] = []

        if request.candidate_mode == "selected":
            target_ids = _unique(request.selected_hospital_ids)
        else:
            target_ids = sorted(indexed)

        response_fields: dict[str, Any] = {
            "candidate_mode": request.candidate_mode,
            "scoring_available": bool(validated.scorable_criteria),
            "weights_used": validated.normalized_weights,
            "coverage_weights": validated.coverage_weights,
            "unsupported_criteria": list(validated.unsupported_criteria),
            "clarification_required": list(validated.clarification_criteria),
            "partially_supported_criteria": list(validated.partially_supported_criteria),
            "support_states": validated.support_states,
            "preference_assessments": list(validated.assessments),
            "warnings": list(validated.warnings),
        }

        if not validated.scorable_criteria:
            return RecommendationResponse(
                **response_fields,
                results=[],
                excluded=[],
                candidates_considered=len(target_ids),
                eligible_count=0,
                returned_count=0,
            )

        scope = self._authorizer.scope_for(user_id)
        for hospital_id in target_ids:
            candidate = indexed.get(hospital_id)
            if candidate is None:
                excluded.append(
                    ExcludedCandidate(
                        hospital_id=hospital_id,
                        reason="not found in the supplied candidate set",
                    )
                )
                continue
            if not scope.allows(hospital_id):
                excluded.append(
                    ExcludedCandidate(
                        hospital_id=hospital_id,
                        hospital_name=_candidate_name(candidate),
                        reason="not authorized for this user",
                    )
                )
                continue

            evaluation = evaluate_mandatory(request, candidate)
            if not evaluation.satisfied:
                excluded.append(
                    ExcludedCandidate(
                        hospital_id=hospital_id,
                        hospital_name=_candidate_name(candidate),
                        reason="; ".join(evaluation.reasons),
                        mandatory_checks=list(evaluation.checks),
                    )
                )
                continue

            eligible.append(score_candidate(candidate, request, validated, self._profile))

        eligible.sort(key=_rank_key)
        results = [
            result.model_copy(update={"rank": rank})
            for rank, result in enumerate(eligible[: request.max_results], start=1)
        ]
        return RecommendationResponse(
            **response_fields,
            results=results,
            excluded=excluded,
            candidates_considered=len(target_ids),
            eligible_count=len(eligible),
            returned_count=len(results),
        )


def _index_candidates(
    candidates: Sequence[HospitalCandidate],
) -> dict[str, HospitalCandidate]:
    indexed: dict[str, HospitalCandidate] = {}
    for candidate in candidates:
        if candidate.hospital_id in indexed:
            raise ValueError(f"duplicate candidate hospital_id {candidate.hospital_id!r}")
        indexed[candidate.hospital_id] = candidate
    return indexed


def _unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _candidate_name(candidate: HospitalCandidate) -> str | None:
    if candidate.name is not None:
        return candidate.name
    if candidate.dataset.hospital is not None:
        return candidate.dataset.hospital.name
    return None


def _rank_key(result: RecommendationResult) -> tuple[bool, float, float, str]:
    score = result.suitability_score
    return (
        score is None,
        -(score if score is not None else 0.0),
        -result.evidence_coverage.overall,
        result.hospital_id,
    )
