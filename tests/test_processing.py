"""Quality tests: conflict detection/resolution, dedup, confidence, provenance.

Covers the P6 contract at both the processing layer (records in/records out)
and the pipeline layer (``build_dataset`` from synthetic HTML).
"""

from __future__ import annotations

from hospital_extractor.extraction.engine import build_dataset
from hospital_extractor.models.accreditation import Accreditation
from hospital_extractor.models.contact import Contact
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.processing.conflicts import (
    authority,
    build_conflict,
    merge_single_record,
)
from hospital_extractor.processing.deduplicate import deduplicate_category

TARGET = "https://example.com"


def _infrastructure(
    beds: int,
    url: str,
    *,
    method: str = "text",
    strength: str = "medium",
) -> Infrastructure:
    return Infrastructure(
        total_beds=beds,
        source_url=url,
        extraction_method=method,
        source_strength=strength,
    )


def _contact(phone: str, url: str, *, method: str = "text") -> Contact:
    return Contact(
        phone=phone,
        address="Saket, New Delhi",
        source_url=url,
        extraction_method=method,
        source_strength="medium",
    )


class TestConflictDetection:
    def test_bed_count_conflict_unresolved_keeps_primary(self) -> None:
        merged, conflicts = merge_single_record(
            "infrastructure",
            [
                _infrastructure(650, f"{TARGET}/a"),
                _infrastructure(700, f"{TARGET}/b"),
            ],
        )
        assert merged is not None
        assert merged.total_beds == 650
        assert len(conflicts) == 1
        conflict = conflicts[0]
        assert conflict.field == "total_beds"
        assert conflict.conflict_type == "capacity"
        assert conflict.primary == "650"
        assert conflict.alternative == "700"
        assert conflict.values == ["650", "700"]
        assert conflict.resolution_status == "unresolved"
        assert conflict.status == "conflicting"
        assert len(conflict.sources) == 2

    def test_authoritative_source_resolves_conflict(self) -> None:
        merged, conflicts = merge_single_record(
            "infrastructure",
            [
                _infrastructure(650, f"{TARGET}/text", method="text"),
                _infrastructure(700, f"{TARGET}/structured", method="jsonld",
                                strength="high"),
            ],
        )
        assert merged is not None
        assert merged.total_beds == 700
        assert len(conflicts) == 1
        conflict = conflicts[0]
        assert conflict.resolution_status == "resolved"
        assert conflict.status == "resolved"
        assert conflict.preferred_value == "700"
        assert conflict.primary == "700"
        assert conflict.alternative == "650"

    def test_equal_authority_stays_unresolved(self) -> None:
        merged, conflicts = merge_single_record(
            "infrastructure",
            [
                _infrastructure(650, f"{TARGET}/a", method="section"),
                _infrastructure(700, f"{TARGET}/b", method="card"),
            ],
        )
        assert merged is not None
        assert merged.total_beds == 650
        assert conflicts[0].resolution_status == "unresolved"
        assert conflicts[0].preferred_value is None

    def test_identical_values_do_not_conflict(self) -> None:
        merged, conflicts = merge_single_record(
            "infrastructure",
            [
                _infrastructure(650, f"{TARGET}/a"),
                _infrastructure(650, f"{TARGET}/b"),
            ],
        )
        assert merged is not None
        assert conflicts == []

    def test_missing_fields_filled_without_conflict(self) -> None:
        primary = _infrastructure(650, f"{TARGET}/a")
        candidate = _infrastructure(650, f"{TARGET}/b")
        candidate.operation_theatres = 25
        merged, conflicts = merge_single_record(
            "infrastructure", [primary, candidate]
        )
        assert merged is not None
        assert merged.operation_theatres == 25
        assert conflicts == []

    def test_phone_conflict_detected(self) -> None:
        merged, conflicts = merge_single_record(
            "contacts",
            [
                _contact("+91-11-4567-8900", f"{TARGET}/a"),
                _contact("+91-11-4015-4000", f"{TARGET}/b"),
            ],
        )
        assert merged is not None
        assert conflicts[0].conflict_type == "phone"
        assert conflicts[0].values == ["+91-11-4567-8900", "+91-11-4015-4000"]

    def test_name_conflict_detected(self) -> None:
        primary = HospitalOverview(name="Saket Hospital", source_url=f"{TARGET}/a")
        alternative = HospitalOverview(
            name="Max Smart Saket", source_url=f"{TARGET}/b"
        )
        merged, conflicts = merge_single_record("hospital", [primary, alternative])
        assert merged is not None
        assert conflicts[0].conflict_type == "name"

    def test_year_conflict_detected(self) -> None:
        primary = Accreditation(
            name="NABH", year=2012, source_url=f"{TARGET}/a"
        )
        alternative = Accreditation(
            name="NABH", year=2015, source_url=f"{TARGET}/b"
        )
        conflicts = build_conflict(
            "accreditations", "year", 2012, 2015, primary, alternative
        )
        assert conflicts.conflict_type == "year"


class TestConflictModel:
    def test_conflict_type_inferred_for_known_fields(self) -> None:
        assert build_conflict("hospital", "bed_count", 1, 2, None, None).conflict_type == "capacity"
        assert build_conflict("contacts", "phone", "a", "b", None, None).conflict_type == "phone"
        assert (
            build_conflict("contacts", "address", "a", "b", None, None).conflict_type
            == "address"
        )
        assert (
            build_conflict("hospital", "established_year", 1, 2, None, None).conflict_type
            == "year"
        )
        assert build_conflict("hospital", "name", "a", "b", None, None).conflict_type == "name"
        assert (
            build_conflict("hospital", "parent_organization", "a", "b", None, None).conflict_type
            == "organization"
        )
        assert (
            build_conflict("doctors", "experience", "a", "b", None, None).conflict_type
            == "value"
        )

    def test_authority_ordering(self) -> None:
        assert authority("jsonld") > authority("semantic_html")
        assert authority("semantic_html") > authority("list")
        assert authority("list") > authority("text")
        assert authority(None) == 0
        assert authority("unknown") == 0

    def test_values_and_sources_preserved(self) -> None:
        primary = _infrastructure(650, f"{TARGET}/a", method="table")
        alternative = _infrastructure(700, f"{TARGET}/b", method="section")
        conflict = build_conflict(
            "infrastructure", "total_beds", 650, 700, primary, alternative
        )
        assert conflict.values == ["650", "700"]
        assert [source.url for source in conflict.sources] == [
            f"{TARGET}/a",
            f"{TARGET}/b",
        ]
        assert [source.extraction_method for source in conflict.sources] == [
            "table",
            "section",
        ]
        assert conflict.source_urls == [f"{TARGET}/a", f"{TARGET}/b"]


class TestDeduplication:
    def test_duplicate_doctors_merged_with_conflict(self) -> None:
        primary = Doctor(
            name="Dr. A Sharma",
            speciality="Cardiology",
            designation="Consultant",
            source_url=f"{TARGET}/d1",
            extraction_method="card",
            source_strength="medium",
        )
        duplicate = Doctor(
            name="Dr. A Sharma",
            speciality="Cardiology",
            designation="Senior Consultant",
            source_url=f"{TARGET}/d2",
            extraction_method="card",
            source_strength="medium",
        )
        result = deduplicate_category("doctors", [primary, duplicate])
        assert result.removed == 1
        assert len(result.records) == 1
        assert result.records[0] is primary
        assert len(result.conflicts) == 1
        assert result.conflicts[0].field == "designation"
        assert result.conflicts[0].resolution_status == "unresolved"

    def test_distinct_doctors_not_merged(self) -> None:
        first = Doctor(
            name="Dr. A Sharma", speciality="Cardiology", source_url=f"{TARGET}/d1"
        )
        second = Doctor(
            name="Dr. B Gupta", speciality="Cardiology", source_url=f"{TARGET}/d2"
        )
        result = deduplicate_category("doctors", [first, second])
        assert result.removed == 0
        assert len(result.records) == 2

    def test_same_name_doctors_merged_by_profile_url(self) -> None:
        first = Doctor(
            name="Dr. A Sharma",
            profile_url=f"{TARGET}/doctors/a-sharma",
            source_url=f"{TARGET}/d1",
        )
        second = Doctor(
            name="Dr. A Sharma",
            profile_url=f"{TARGET}/doctors/a-sharma",
            source_url=f"{TARGET}/d2",
        )
        result = deduplicate_category("doctors", [first, second])
        assert result.removed == 1
        assert len(result.records) == 1


class TestPipelineQuality:
    HOSPITAL_A = """
    <html><head><title>Max Smart Hospital</title></head>
    <body>
    <h1>Max Smart Hospital</h1>
    <h2>About Us</h2>
    <p>Max Smart Saket is a 650 bedded multi-speciality hospital
    established in 2012 serving the people of Delhi.</p>
    </body></html>
    """
    HOSPITAL_B = """
    <html><head><title>Max Smart Hospital</title></head>
    <body>
    <h1>Max Smart Hospital</h1>
    <h2>About Us</h2>
    <p>Max Smart Saket is a 700 bedded multi-speciality hospital
    established in 2012 serving the people of Delhi.</p>
    </body></html>
    """

    def test_pipeline_bed_count_conflict_unresolved(self) -> None:
        dataset = build_dataset(
            [
                (f"{TARGET}/hospital-network/saket", self.HOSPITAL_A),
                (f"{TARGET}/hospital-network/saket-south", self.HOSPITAL_B),
            ],
            target_url=TARGET,
        )
        quality = dataset.quality
        assert dataset.infrastructure is not None
        assert dataset.infrastructure[0].total_beds == 650
        capacity = [c for c in quality.conflicts if c.conflict_type == "capacity"]
        assert capacity, "expected at least one unresolved capacity conflict"
        assert all(c.resolution_status == "unresolved" for c in capacity)
        assert any(
            c.values == ["650", "700"] and c.field == "total_beds" for c in capacity
        )

    def test_pipeline_phone_conflict_from_contact_pages(self) -> None:
        page_a = """
        <html><head><title>Contact Us</title></head>
        <body>
        <h1>Contact Us</h1>
        <p>Call us at <a href="tel:+911145678900">+91-11-4567-8900</a></p>
        <address>Saket, New Delhi 110017</address>
        </body></html>
        """
        page_b = """
        <html><head><title>Contact Us</title></head>
        <body>
        <h1>Contact Us</h1>
        <p>Call us at <a href="tel:+911140154000">+91-11-4015-4000</a></p>
        <address>Saket, New Delhi 110017</address>
        </body></html>
        """
        dataset = build_dataset(
            [
                (f"{TARGET}/contact-us", page_a),
                (f"{TARGET}/contact-us-2", page_b),
            ],
            target_url=TARGET,
        )
        phones = [c for c in dataset.quality.conflicts if c.conflict_type == "phone"]
        assert phones, "expected a phone conflict"
        assert phones[0].resolution_status == "unresolved"

    def test_unresolved_conflicts_surface_as_warnings(self) -> None:
        dataset = build_dataset(
            [
                (f"{TARGET}/hospital-network/saket", self.HOSPITAL_A),
                (f"{TARGET}/hospital-network/saket-south", self.HOSPITAL_B),
            ],
            target_url=TARGET,
        )
        assert dataset.quality.conflicts_unresolved > 0
        assert any("unresolved conflict" in w for w in dataset.quality.warnings)

    def test_low_confidence_records_counted(self) -> None:
        dataset = build_dataset(
            [(f"{TARGET}/hospital-network/saket", self.HOSPITAL_A)],
            target_url=TARGET,
        )
        assert dataset.quality.low_confidence_records >= 1
        assert dataset.infrastructure[0].confidence == 0.4

    def test_records_carry_full_provenance(self) -> None:
        dataset = build_dataset(
            [(f"{TARGET}/hospital-network/saket", self.HOSPITAL_A)],
            target_url=TARGET,
        )
        record = dataset.infrastructure[0]
        assert record.source_url == f"{TARGET}/hospital-network/saket"
        assert record.page_url == record.source_url
        assert record.extraction_method == "text"
        assert record.confidence == 0.4
        assert record.context is not None
