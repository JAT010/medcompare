"""Conservative physical-facility identity semantics (P1-A, no DB matching).

These rules are domain safeguards: discovery must not guess identity or mutate
the registry. A hospital organization is not a physical facility.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .schemas import DiscoveryBaseModel


class FacilityIdentityCategory(StrEnum):
    EXACT_REGISTRY_MATCH = "exact_registry_match"
    STRONG_POSSIBLE_MATCH = "strong_possible_match"
    AMBIGUOUS_MATCH = "ambiguous_match"
    NEW_CANDIDATE_FACILITY = "new_candidate_facility"
    CONFLICTING_IDENTITY = "conflicting_identity"


class IdentityAssessment(DiscoveryBaseModel):
    """Conservative, explainable identity assessment (no automatic registry binding)."""

    category: FacilityIdentityCategory
    reasons: list[str] = Field(default_factory=list, max_length=20)
    registry_facility_id: str | None = Field(default=None, max_length=64)
    auto_assign_registry_id_allowed: bool = False
    registry_mutation_allowed: bool = False


EXACT_IDENTITY_FORBIDDEN_REASONS = {
    "shared_domain": "A shared domain does not establish physical-facility identity.",
    "canonical_url_only": (
        "A matching canonical URL alone does not establish physical-facility identity."
    ),
    "similar_name": "Similar names do not establish identity.",
    "organization_match": "A hospital organization is not a physical facility.",
}


def assess_identity_signals(
    *,
    same_domain: bool = False,
    same_canonical_url: bool = False,
    same_name: bool = False,
    same_address: bool = False,
    address_conflict: bool = False,
    location_conflict: bool = False,
    organization_level_only: bool = False,
    unique_registry_facility_confirmed: bool = False,
) -> IdentityAssessment:
    """Return a conservative assessment from normalized comparison signals.

    P1-A encodes semantics only (no database lookups, no automatic assignment).
    Even strong corroboration (name+domain+facility-specific address) is at most a
    strong possible match and must not trigger automatic merging or registry ID
    assignment without explicit, verified resolution in a later phase.
    """
    reasons: list[str] = []

    if address_conflict or location_conflict:
        reasons.append("Conflicting address or location information must remain unresolved.")
        return IdentityAssessment(
            category=FacilityIdentityCategory.CONFLICTING_IDENTITY,
            reasons=reasons,
            registry_facility_id=None,
            auto_assign_registry_id_allowed=False,
            registry_mutation_allowed=False,
        )

    if organization_level_only:
        reasons.append(EXACT_IDENTITY_FORBIDDEN_REASONS["organization_match"])
        return IdentityAssessment(
            category=FacilityIdentityCategory.AMBIGUOUS_MATCH,
            reasons=reasons,
            registry_facility_id=None,
            auto_assign_registry_id_allowed=False,
            registry_mutation_allowed=False,
        )

    # Exact match requires a unique, verified facility-level confirmation signal that is
    # stronger than domain/name/URL heuristics. P1-A never performs that confirmation.
    if unique_registry_facility_confirmed:
        reasons.append(
            "Exact identity requires verified facility-level confirmation; "
            "P1-A performs no DB-backed confirmation."
        )
        return IdentityAssessment(
            category=FacilityIdentityCategory.AMBIGUOUS_MATCH,
            reasons=reasons,
            registry_facility_id=None,
            auto_assign_registry_id_allowed=False,
            registry_mutation_allowed=False,
        )

    # Forbidden-to-promote signals.
    if same_domain and not (same_name and same_address and same_canonical_url):
        reasons.append(EXACT_IDENTITY_FORBIDDEN_REASONS["shared_domain"])
    if same_canonical_url and not (same_name and same_address and same_domain):
        reasons.append(EXACT_IDENTITY_FORBIDDEN_REASONS["canonical_url_only"])
    if same_name and not (same_address and (same_domain or same_canonical_url)):
        reasons.append(EXACT_IDENTITY_FORBIDDEN_REASONS["similar_name"])

    if same_name and same_domain and same_address:
        reasons.append(
            "Matching name, domain, and facility-specific address may support a strong "
            "possible match but must not trigger automatic merging."
        )
        return IdentityAssessment(
            category=FacilityIdentityCategory.STRONG_POSSIBLE_MATCH,
            reasons=reasons,
            registry_facility_id=None,
            auto_assign_registry_id_allowed=False,
            registry_mutation_allowed=False,
        )

    if same_domain or same_canonical_url or same_name:
        reasons.append("Available signals are insufficient for exact physical-facility identity.")
        return IdentityAssessment(
            category=FacilityIdentityCategory.AMBIGUOUS_MATCH,
            reasons=reasons,
            registry_facility_id=None,
            auto_assign_registry_id_allowed=False,
            registry_mutation_allowed=False,
        )

    reasons.append(
        "No sufficient identity signals; candidate remains a new/unresolved facility candidate."
    )
    return IdentityAssessment(
        category=FacilityIdentityCategory.NEW_CANDIDATE_FACILITY,
        reasons=reasons,
        registry_facility_id=None,
        auto_assign_registry_id_allowed=False,
        registry_mutation_allowed=False,
    )
