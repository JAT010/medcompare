"""Tests for the 25-field extraction engine semantics and responsibilities.

Covers the conservative evidence rules (no inference beyond explicit text),
the field-specific responsibility registry, and evidence provenance.
"""

from __future__ import annotations

import pytest

from hospital_extractor.extraction import analyze_pages, build_dataset
from hospital_extractor.extraction.fields import (
    FIELD_GROUPS,
    group_for_field,
    responsible_fields,
)
from hospital_extractor.extraction.intake import project_intake
from hospital_extractor.schema.boolean_resolver import apply_detectors, detectors, resolve
from hospital_extractor.schema.intake import INTAKE_FIELDS, boolean_keys

BASE = "https://example.test"

_HOME = """<!doctype html><html><head><title>City Hospital</title></head>
<body><h1>City Hospital</h1><p>City Hospital is a multi-speciality hospital.</p></body></html>"""

_TECH_ROBOTIC = """<!doctype html><html><head><title>Technology - City Hospital</title></head>
<body><h1>Technology</h1><p>We offer advanced robotic surgery.</p></body></html>"""

_TECH_DAVINCI = """<!doctype html><html><head><title>Technology - City Hospital</title></head>
<body><h1>Technology</h1><p>Our surgical suites feature the Da Vinci Xi system.</p></body></html>"""


def _decide(text: str, field_key: str) -> str:
    items = [
        item for item in apply_detectors(text, f"{BASE}/page") if item.field_key == field_key
    ]
    intake, _ = resolve({}, {field_key: items})
    return intake.booleans[field_key]


def test_mri_available_without_strength_is_unknown():
    assert _decide("Our hospital provides MRI facilities.", "mri_3_tesla_or_higher") == "unknown"
    assert _decide("3 Tesla MRI available.", "mri_3_tesla_or_higher") == "yes"


def test_generic_cardiac_care_is_unknown_cabg():
    assert _decide("cardiac care and heart health programs", "bypass_surgery_cabg") == "unknown"
    assert _decide("Coronary artery bypass graft surgery", "bypass_surgery_cabg") == "yes"


def test_robotic_surgery_alone_is_unknown_da_vinci():
    assert _decide("We offer robotic surgery.", "da_vinci_robotic_surgery") == "unknown"
    assert _decide("Da Vinci Xi system", "da_vinci_robotic_surgery") == "yes"


def test_orthopaedics_alone_is_unknown_joint_replacement():
    assert _decide("Comprehensive orthopaedics department.", "total_knee_replacement") == "unknown"
    assert _decide("Total Knee Replacement offered.", "total_knee_replacement") == "yes"
    assert _decide("Total Hip Replacement offered.", "total_hip_replacement") == "yes"


def test_page_evidence_carries_full_provenance():
    items = apply_detectors("Total Knee Replacement surgery is available", f"{BASE}/ortho")
    knee = [item for item in items if item.field_key == "total_knee_replacement"]
    assert knee
    assert knee[0].source_url == f"{BASE}/ortho"
    assert knee[0].method == "pattern-match"
    assert knee[0].strength > 0
    assert knee[0].decision == "yes"


def test_every_boolean_field_has_a_detector():
    detected = {detector.name for detector in detectors()}
    assert boolean_keys() <= detected


def test_every_intake_field_owned_by_exactly_one_group():
    owners: dict[str, str] = {}
    for group in FIELD_GROUPS:
        for key in group.field_keys:
            assert key not in owners, f"{key} owned by both {owners[key]} and {group.key}"
            owners[key] = group.key
    assert set(owners) == {field.key for field in INTAKE_FIELDS}


def test_group_lookup_and_responsible_fields():
    assert group_for_field("total_beds").label == "CAPACITY"
    assert group_for_field("bypass_surgery_cabg").label == "CARDIAC"
    assert group_for_field("hospital_name").label == "IDENTITY"
    assert group_for_field("icu").label == "FACILITY"
    assert set(responsible_fields("orthopaedics")) == {
        "orthopaedics_joint_replacement_enabled",
        "total_knee_replacement",
        "total_hip_replacement",
    }
    assert responsible_fields("nonsense") == ()
    with pytest.raises(KeyError):
        group_for_field("nonsense")


def test_cardiac_group_lists_evidence_categories():
    cardiac = group_for_field("bypass_surgery_cabg")
    assert set(cardiac.evidence_categories) == {
        "specialities",
        "departments",
        "procedures",
        "treatments",
    }


def test_intake_robotic_surgery_alone_is_unknown(config):
    pages = [(f"{BASE}/", _HOME), (f"{BASE}/technology", _TECH_ROBOTIC)]
    dataset = build_dataset(pages, target_url=f"{BASE}/", config=config)
    intake = project_intake(dataset, pages=analyze_pages(pages))
    assert intake.booleans["da_vinci_robotic_surgery"] == "unknown"


def test_intake_explicit_da_vinci_is_yes(config):
    pages = [(f"{BASE}/", _HOME), (f"{BASE}/technology", _TECH_DAVINCI)]
    dataset = build_dataset(pages, target_url=f"{BASE}/", config=config)
    intake = project_intake(dataset, pages=analyze_pages(pages))
    assert intake.booleans["da_vinci_robotic_surgery"] == "yes"
    da_vinci_evidence = intake.evidence["da_vinci_robotic_surgery"]
    assert da_vinci_evidence
    assert all(item.source_url for item in da_vinci_evidence)
