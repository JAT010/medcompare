"""Deterministic scoring scales and match-category thresholds.

The reference values are saturation points for published capacity and
experience, not clinical quality judgements. They are explicit, configurable
and never inferred from a hospital's identity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringProfile:
    experience_reference_years: float = 30.0
    infrastructure_beds_reference: float = 1000.0
    infrastructure_icu_reference: float = 200.0
    infrastructure_ot_reference: float = 30.0
    infrastructure_cath_reference: float = 10.0
    strong_match_min_score: float = 80.0
    strong_match_min_coverage: float = 0.8
    good_match_min_score: float = 65.0
    good_match_min_coverage: float = 0.6
    possible_match_min_score: float = 50.0

    def __post_init__(self) -> None:
        positive = (
            self.experience_reference_years,
            self.infrastructure_beds_reference,
            self.infrastructure_icu_reference,
            self.infrastructure_ot_reference,
            self.infrastructure_cath_reference,
        )
        if any(not math.isfinite(value) or value <= 0 for value in positive):
            raise ValueError("scoring reference values must be finite and positive")

        thresholds = (
            self.strong_match_min_score,
            self.strong_match_min_coverage,
            self.good_match_min_score,
            self.good_match_min_coverage,
            self.possible_match_min_score,
        )
        if any(not math.isfinite(value) for value in thresholds):
            raise ValueError("match thresholds must be finite")
        if not 0.0 <= self.strong_match_min_score <= 100.0:
            raise ValueError("strong_match_min_score must be between 0 and 100")
        if not 0.0 <= self.good_match_min_score <= 100.0:
            raise ValueError("good_match_min_score must be between 0 and 100")
        if not 0.0 <= self.possible_match_min_score <= 100.0:
            raise ValueError("possible_match_min_score must be between 0 and 100")
        if not 0.0 <= self.strong_match_min_coverage <= 1.0:
            raise ValueError("strong_match_min_coverage must be between 0 and 1")
        if not 0.0 <= self.good_match_min_coverage <= 1.0:
            raise ValueError("good_match_min_coverage must be between 0 and 1")
        if not (
            self.strong_match_min_score
            >= self.good_match_min_score
            >= self.possible_match_min_score
        ):
            raise ValueError("match score thresholds must be descending")
