"""Preference-based recommendation foundation.

The package is framework-agnostic: the existing backend must supply the
authoritative ``HospitalAuthorizer`` and the candidate hospital data. No
network, database or LLM access happens here.
"""

from __future__ import annotations

from hospital_extractor.recommendation.authorization import (
    AuthorizationScope,
    DenyAllAuthorizer,
    HospitalAuthorizer,
    StaticAuthorizer,
)
from hospital_extractor.recommendation.criteria import (
    CRITERIA,
    CRITERIA_BY_KEY,
    SUPPORTED_CRITERIA,
    CriterionKey,
    CriterionSpec,
    CriterionSupport,
    SupportLevel,
    criterion,
    is_supported,
)
from hospital_extractor.recommendation.preferences import (
    PreferenceValidationError,
    ValidatedPreferences,
    validate_preferences,
)
from hospital_extractor.recommendation.profile import ScoringProfile
from hospital_extractor.recommendation.schemas import (
    CandidateMode,
    CriterionEvidenceState,
    CriterionScore,
    DepartmentSelection,
    EvidenceCoverage,
    EvidenceRef,
    ExcludedCandidate,
    HospitalCandidate,
    LocationRequirement,
    MandatoryCheck,
    MandatoryConstraints,
    MandatoryStatus,
    MatchCategory,
    PreferenceAssessment,
    PreferenceRequest,
    PreferenceSupportState,
    RecommendationConfidence,
    RecommendationResponse,
    RecommendationResult,
    WeightedPreferences,
)
from hospital_extractor.recommendation.service import RecommendationService

__all__ = [
    "CRITERIA",
    "CRITERIA_BY_KEY",
    "SUPPORTED_CRITERIA",
    "AuthorizationScope",
    "CandidateMode",
    "CriterionEvidenceState",
    "CriterionKey",
    "CriterionScore",
    "CriterionSpec",
    "CriterionSupport",
    "DenyAllAuthorizer",
    "DepartmentSelection",
    "EvidenceCoverage",
    "EvidenceRef",
    "ExcludedCandidate",
    "HospitalAuthorizer",
    "HospitalCandidate",
    "LocationRequirement",
    "MandatoryCheck",
    "MandatoryConstraints",
    "MandatoryStatus",
    "MatchCategory",
    "PreferenceAssessment",
    "PreferenceRequest",
    "PreferenceSupportState",
    "PreferenceValidationError",
    "RecommendationConfidence",
    "RecommendationResponse",
    "RecommendationResult",
    "RecommendationService",
    "ScoringProfile",
    "StaticAuthorizer",
    "SupportLevel",
    "ValidatedPreferences",
    "WeightedPreferences",
    "criterion",
    "is_supported",
    "validate_preferences",
]
