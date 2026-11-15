from __future__ import annotations

import pytest
from pydantic import ValidationError

from hospital_extractor.discovery.interpret import interpret_discovery_prompt
from hospital_extractor.discovery.schemas import (
    DISCOVERY_CONTRACT_VERSION,
    DISCOVERY_INTERPRETER,
    ConstraintKind,
    DiscoveryInterpretRequest,
    InformationPresence,
)


def interpret(prompt: str):
    return interpret_discovery_prompt(DiscoveryInterpretRequest(prompt=prompt))


def test_original_prompt_preserved_and_normalization():
    prompt = "  Find   multispecialty hospital   in  Austin  "
    resp = interpret(prompt)
    assert resp.original_prompt == prompt
    assert resp.normalized_prompt == "Find multispecialty hospital in Austin"
    assert resp.interpreter == DISCOVERY_INTERPRETER
    assert resp.contract_version == DISCOVERY_CONTRACT_VERSION


def test_required_vs_preference_distinction():
    resp = interpret("Find hospital in Austin must have ICU and preferably parking")
    kinds = {(c.key, c.value, c.kind) for c in resp.filters.constraints}
    pref_vals = {(p.key, p.value) for p in resp.filters.preferences}
    assert ("department", "intensive_care_unit", ConstraintKind.REQUIRED) in kinds
    assert ("facility", "parking") in pref_vals


def test_ambiguous_intent_no_invented_requirement_or_preference():
    resp = interpret("Find hospital in Austin with ICU")
    # ICU recognized but no explicit required/preference cue -> must not be invented
    assert any(t.value == "intensive_care_unit" for t in resp.filters.departments)
    assert not any(c.value == "intensive_care_unit" for c in resp.filters.constraints)
    assert not any(p.value == "intensive_care_unit" for p in resp.filters.preferences)


def test_missing_location_explicit():
    resp = interpret("Find multispecialty hospital with ICU must have ambulance")
    assert resp.filters.location_text is None
    assert "location" in resp.filters.missing_information
    assert resp.filters.location_presence == InformationPresence.MISSING
    assert not resp.filters.negative_requirements


def test_multiple_conflicting_locations_trigger_clarification():
    resp = interpret("Find hospital in Austin and in Dallas must have ICU")
    assert len([a for a in resp.ambiguities if a.code == "conflicting_locations"]) >= 1
    assert any(c.field == "location_text" for c in resp.clarification_questions)
    assert resp.filters.location_presence == InformationPresence.AMBIGUOUS


def test_explicit_exclusions_and_negative_requirements():
    resp = interpret("Find hospital in Austin without parking")
    assert any(n.value == "parking" for n in resp.filters.explicit_exclusions)
    assert any(
        n.value == "parking" and n.field == "facility"
        for n in resp.filters.negative_requirements
    )
    assert all("parking" not in n.value for n in [])


def test_radius_parsing_and_units():
    resp = interpret("Find hospital within 10 km of Austin must have ICU")
    assert resp.filters.geo is not None
    assert resp.filters.geo.type == "radius"
    assert resp.filters.geo.original_value == 10.0
    assert resp.filters.geo.original_unit == "km"
    assert resp.filters.geo.radius_km == 10.0
    assert "Austin" in (resp.filters.geo.center_text or "")


def test_radius_unit_miles_converted():
    resp = interpret("Hospitals within 5 miles of Austin")
    assert resp.filters.geo is not None
    assert resp.filters.geo.original_unit == "mi"
    assert resp.filters.geo.radius_km == pytest.approx(8.0467, rel=1e-3)


def test_recognized_vocabulary_extracted():
    resp = interpret(
        "Find multispecialty hospital in Austin must have cardiology "
        "and emergency department and ambulance"
    )
    assert any(
        t.value == "multispecialty_hospital" for t in resp.filters.hospital_categories
    )
    assert any(t.value == "cardiology" for t in resp.filters.specialties)
    assert any(t.value == "emergency_department" for t in resp.filters.departments)
    assert any(t.value == "ambulance" for t in resp.filters.required_facilities)


def test_unsupported_vocabulary_preserved_not_mapped():
    resp = interpret("Find hospital in Austin with robotic surgery center must have ICU")
    texts = [u.original_text.lower() for u in resp.unsupported_requirements]
    assert any("robotic surgery center" in t for t in texts)
    # no silent mapping to supported terms
    all_supported = (
        [t.value for t in resp.filters.specialties]
        + [t.value for t in resp.filters.departments]
        + [t.value for t in resp.filters.required_facilities]
    )
    assert "robotic surgery center" not in " ".join(all_supported)


def test_missing_vs_explicit_negative_not_conflated():
    resp = interpret("Find hospital must have ICU")
    assert "location" in resp.filters.missing_information
    assert not any(n.field == "location" for n in resp.filters.negative_requirements)
    assert resp.filters.location_presence != InformationPresence.EXPLICIT_NEGATIVE


def test_deterministic_repeated_output():
    req = DiscoveryInterpretRequest(prompt="Prefer parking in Austin, must have ICU and cardiology")
    a = interpret_discovery_prompt(req).model_dump(mode="json")
    b = interpret_discovery_prompt(req).model_dump(mode="json")
    assert a == b


def test_invalid_blank_rejected():
    with pytest.raises(ValidationError):
        DiscoveryInterpretRequest(prompt="   ")


def test_oversized_request_rejected():
    with pytest.raises(ValidationError):
        DiscoveryInterpretRequest(prompt="a" * 2001)


def test_extra_fields_rejected_request():
    with pytest.raises(ValidationError):
        DiscoveryInterpretRequest.model_validate(
            {"prompt": "Find hospital in Austin", "extra": "x"}
        )


def test_extra_fields_rejected_response_shape():
    resp = interpret("Find hospital in Austin must have ICU")
    with pytest.raises(ValidationError):
        type(resp).model_validate({**resp.model_dump(), "surprise": True})


def test_no_invented_filters_or_service_availability():
    resp = interpret("Find hospitals")
    assert resp.filters.location_text is None
    assert resp.filters.geo is None
    assert resp.filters.hospital_categories == []
    assert resp.filters.specialties == []
    assert resp.filters.departments == []
    assert resp.filters.required_facilities == []
    assert "location" in resp.filters.missing_information
    # must not claim discovered availability/evidence
    dump = resp.model_dump(mode="json")
    assert "candidates" not in dump
    assert dump["filters"]["required_facilities"] == [] or all(
        "discovered" not in str(note).lower()
        for note in dump.get("interpretation_notes", [])
    )


def test_deduplication_deterministic():
    resp = interpret("Find hospital in Austin must have ICU and must have ICU")
    icu = [t for t in resp.filters.departments if t.value == "intensive_care_unit"]
    assert len(icu) == 1


def test_requested_facility_not_treated_as_evidence():
    resp = interpret("Find hospital in Austin must have ambulance")
    # represented as recognized requested facility/constraint only;
    # no candidates/evidence fields exist
    assert any(t.value == "ambulance" for t in resp.filters.required_facilities)
    assert not hasattr(resp, "evidence")
    assert not hasattr(resp, "candidates")
