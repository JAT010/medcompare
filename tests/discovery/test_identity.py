from __future__ import annotations

import inspect

from hospital_extractor.discovery import interpret as interpret_module
from hospital_extractor.discovery import provider as provider_module
from hospital_extractor.discovery.identity import (
    FacilityIdentityCategory,
    assess_identity_signals,
)
from hospital_extractor.discovery.schemas import DiscoverySearchRequest


def test_shared_domain_is_not_exact_identity():
    a = assess_identity_signals(same_domain=True)
    assert a.category != FacilityIdentityCategory.EXACT_REGISTRY_MATCH
    assert a.category == FacilityIdentityCategory.AMBIGUOUS_MATCH
    assert a.auto_assign_registry_id_allowed is False
    assert a.registry_mutation_allowed is False


def test_canonical_url_equality_alone_is_not_exact_identity():
    a = assess_identity_signals(same_canonical_url=True)
    assert a.category != FacilityIdentityCategory.EXACT_REGISTRY_MATCH
    assert a.category == FacilityIdentityCategory.AMBIGUOUS_MATCH


def test_similar_name_not_exact_identity():
    a = assess_identity_signals(same_name=True)
    assert a.category in {
        FacilityIdentityCategory.AMBIGUOUS_MATCH,
        FacilityIdentityCategory.NEW_CANDIDATE_FACILITY,
    }
    assert a.category != FacilityIdentityCategory.EXACT_REGISTRY_MATCH


def test_matching_name_domain_and_facility_address_is_strong_possible_not_auto_merge():
    a = assess_identity_signals(same_name=True, same_domain=True, same_address=True)
    assert a.category == FacilityIdentityCategory.STRONG_POSSIBLE_MATCH
    assert a.auto_assign_registry_id_allowed is False
    assert a.registry_mutation_allowed is False
    assert a.registry_facility_id is None


def test_conflicting_address_remains_unresolved():
    a = assess_identity_signals(same_name=True, same_domain=True, address_conflict=True)
    assert a.category == FacilityIdentityCategory.CONFLICTING_IDENTITY
    assert a.registry_facility_id is None
    assert a.auto_assign_registry_id_allowed is False


def test_conflicting_location_remains_unresolved():
    a = assess_identity_signals(same_canonical_url=True, location_conflict=True)
    assert a.category == FacilityIdentityCategory.CONFLICTING_IDENTITY


def test_organization_level_not_physical_facility_identity():
    a = assess_identity_signals(same_name=True, same_domain=True, organization_level_only=True)
    assert a.category != FacilityIdentityCategory.EXACT_REGISTRY_MATCH
    assert any("organization" in r.lower() for r in a.reasons)


def test_no_registry_mutation_performed_by_discovery_modules():
    # P1-A modules expose no create/update/delete/merge registry operations.
    for mod in (provider_module, interpret_module):
        src = inspect.getsource(mod)
        lowered = src.lower()
        assert "registryservice" not in lowered
        assert "create_facility" not in lowered
        assert "merge" not in lowered
        assert "deactivate" not in lowered
    prov = provider_module.NotConfiguredDiscoveryProvider()
    caps = prov.capabilities()
    assert caps["live_network"] is False
    # search produces no candidates and no side-effect API exists
    resp = prov.search(DiscoverySearchRequest(query_text="x"))
    assert resp.candidates == []
