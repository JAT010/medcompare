"""Tests for the 25-field intake projection."""

from __future__ import annotations

from hospital_extractor.extraction import build_dataset
from hospital_extractor.extraction.intake import project_intake
from hospital_extractor.schema.intake import INTAKE_FIELD_COUNT, INTAKE_FIELDS


def test_intake_projects_all_fields(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)
    intake = project_intake(dataset)

    assert len(INTAKE_FIELDS) == INTAKE_FIELD_COUNT == 25
    assert len(intake.to_dict()) == INTAKE_FIELD_COUNT + 1  # 25 fields + booleans composite


def test_intake_text_fields(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)
    intake = project_intake(dataset)

    assert intake.hospital_name == "City Hospital"
    assert intake.pin_code == "411001"
    assert intake.city == "Pune"
    assert intake.area_locality in ("12 MG Road", "Koregaon Park", None)
    assert intake.established_year == 1998
    assert intake.total_beds == 650
    assert intake.icu_beds == dataset.infrastructure[0].icu_beds
    assert intake.logo_image_url == "https://example.test/static/logo.png"


def test_intake_boolean_fields(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)
    intake = project_intake(dataset)

    boolean_keys = {f.key for f in INTAKE_FIELDS if f.is_boolean}
    assert set(intake.booleans) <= boolean_keys
    assert all(value in ("yes", "no", "unknown") for value in intake.booleans.values())


def test_intake_structural_check(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)
    intake = project_intake(dataset)

    assert intake.check() == []
