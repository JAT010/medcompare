"""End-to-end extraction tests against the standard six-page fixture."""

from __future__ import annotations

from hospital_extractor.extraction import build_dataset


def test_full_pipeline_populates_categories(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)

    assert dataset.hospital is not None
    assert dataset.hospital.name == "City Hospital"
    assert dataset.hospital.website == "https://example.test/"

    assert len(dataset.contacts) == 1
    contact = dataset.contacts[0]
    assert contact.city == "Pune"
    assert contact.postal_code == "411001"

    assert len(dataset.infrastructure) == 1
    infra = dataset.infrastructure[0]
    assert infra.total_beds == 650
    assert infra.critical_care_beds == 120
    assert infra.operation_theatres == 5

    assert len(dataset.departments) == 3
    assert len(dataset.treatments) == 2

    facility_names = {f.name for f in dataset.facilities}
    assert "24x7 Pharmacy" in facility_names
    assert "Blood Bank" in facility_names


def test_faq_routes_to_facilities(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)

    facility_names = {f.name for f in dataset.facilities}
    assert any("diagnostic" in name.lower() or "lab" in name.lower() for name in facility_names)


def test_duplicate_doctor_removed(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)

    assert dataset.quality.duplicates_removed >= 1
    profiles = [d.profile_url for d in dataset.doctors]
    assert len(profiles) == len(set(profiles))


def test_extraction_source_strength(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)

    assert dataset.hospital.source_strength == "high"
    assert dataset.hospital.extraction_method == "jsonld"
