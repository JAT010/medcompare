"""Unit tests for deduplication, validation and doctor association filtering."""

from __future__ import annotations

from hospital_extractor.config import ExtractionConfig
from hospital_extractor.extraction.doctors import HospitalContext, extract_doctors
from hospital_extractor.extraction.engine import _filter_doctors
from hospital_extractor.extraction.page_analyzer import analyze_page
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.processing.deduplicate import deduplicate_category
from hospital_extractor.processing.validate import validate_records
from hospital_extractor.schema.canonical import category_for


def _doctor(name: str, profile_url: str, association: str | None) -> Doctor:
    return Doctor(
        source_url="https://example.test/",
        page_url="https://example.test/doctors",
        page_title="Our Doctors",
        extracted_at="2026-01-01T00:00:00Z",
        context="hospital page",
        extraction_method="cards",
        name=name,
        speciality="Cardiology",
        profile_url=profile_url,
        hospital_association=association,
    )


def test_deduplicate_doctors_by_profile_url():
    records = [
        _doctor("Dr. Anil Kumar", "https://example.test/doctor/anil-kumar", None),
        _doctor("Dr. Anil Kumar", "https://example.test/doctor/anil-kumar", None),
        _doctor("Dr. Meera Nair", "https://example.test/doctor/meera-nair", None),
    ]

    result = deduplicate_category("doctors", records)

    assert result.removed == 1
    assert len(result.records) == 2


def test_validate_doctors_accepts_named_records():
    records = [_doctor("Dr. Anil Kumar", "https://example.test/doctor/anil-kumar", None)]
    category = category_for("doctors")

    result = validate_records(category, records)

    assert result.rejected == []
    assert len(result.accepted) == 1


def test_extract_doctors_from_cards():
    page = analyze_page(
        '<div class="doctor-card"><h3>Dr. Anil Kumar</h3><p>Cardiologist</p>'
        '<a href="/doctor/anil-kumar">Profile</a></div>',
        url="https://example.test/doctors",
    )
    context = HospitalContext(target_url="https://example.test/")

    records = extract_doctors(page, context)

    assert len(records) == 1
    assert records[0].name == "Dr. Anil Kumar"
    assert records[0].hospital_association == "probable"


def test_filter_doctors_excludes_uncertain_by_default():
    cfg = ExtractionConfig()
    records = [
        _doctor("A", "https://example.test/a", "confirmed"),
        _doctor("B", "https://example.test/b", "uncertain"),
        _doctor("C", "https://example.test/c", "not_associated"),
        _doctor("D", "https://example.test/d", None),
    ]

    kept = _filter_doctors(records, cfg)

    assert {r.name for r in kept} == {"A", "D"}


def test_filter_doctors_keeps_uncertain_when_included():
    cfg = ExtractionConfig(doctor_association_include_min="uncertain")
    records = [
        _doctor("A", "https://example.test/a", "confirmed"),
        _doctor("B", "https://example.test/b", "uncertain"),
        _doctor("C", "https://example.test/c", "not_associated"),
    ]

    kept = _filter_doctors(records, cfg)

    assert {r.name for r in kept} == {"A", "B"}


def test_extract_doctors_deduplicates_profiles():
    page = analyze_page(
        '<div class="doctor-card"><h3>Dr. Anil Kumar</h3><p>Cardiologist</p>'
        '<a href="/doctor/anil-kumar">Profile</a></div>'
        '<div class="doctor-card"><h3>Dr. Anil Kumar</h3><p>Cardiologist</p>'
        '<a href="/doctor/anil-kumar">Profile</a></div>',
        url="https://example.test/doctors",
    )
    context = HospitalContext(target_url="https://example.test/")

    records = extract_doctors(page, context)

    assert len(records) == 1


def test_extract_doctors_same_domain_probable_association():
    page = analyze_page(
        '<script type="application/ld+json">'
        '{"@type":"Physician","name":"Dr. Sharma","url":"https://apollohospitals.com/doctor/sharma"}'
        '</script>',
        url="https://www.apollohospitals.com/find-a-doctor",
    )
    context = HospitalContext(target_url="https://www.apollohospitals.com/")

    records = extract_doctors(page, context)

    assert len(records) == 1
    assert records[0].hospital_association == "probable"


def test_extract_doctors_different_domain_uncertain_association():
    page = analyze_page(
        '<script type="application/ld+json">'
        '{"@type":"Physician","name":"Dr. Patel","url":"https://other-hospital.com/doctor/patel"}'
        '</script>',
        url="https://example.test/doctors",
    )
    context = HospitalContext(target_url="https://example.test/")

    records = extract_doctors(page, context)

    assert len(records) == 1
    assert records[0].hospital_association == "uncertain"


def test_extract_doctors_jsonld_with_affiliations_confirmed():
    page = analyze_page(
        '<script type="application/ld+json">'
        '{"@type":"Physician","name":"Dr. Singh",'
        '"worksFor":{"@type":"Hospital","name":"City Hospital"}}'
        '</script>',
        url="https://example.test/doctors",
    )
    context = HospitalContext(target_url="https://example.test/", name="City Hospital")

    records = extract_doctors(page, context)

    assert len(records) == 1
    assert records[0].hospital_association == "confirmed"


def test_extract_doctors_jsonld_affiliations_not_associated():
    page = analyze_page(
        '<script type="application/ld+json">'
        '{"@type":"Physician","name":"Dr. Gupta",'
        '"worksFor":{"@type":"Hospital","name":"Other Hospital"}}'
        '</script>',
        url="https://example.test/doctors",
    )
    context = HospitalContext(target_url="https://example.test/", name="City Hospital")

    records = extract_doctors(page, context)

    assert len(records) == 1
    assert records[0].hospital_association == "not_associated"
