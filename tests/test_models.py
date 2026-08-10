from datetime import datetime

import pytest
from pydantic import ValidationError

from hospital_extractor.models import (
    Award,
    Contact,
    Doctor,
    Facility,
    HospitalDataset,
    HospitalOverview,
    Infrastructure,
    InsuranceProvider,
    SourceRecord,
)


class TestEntityBase:
    def test_source_url_required(self) -> None:
        with pytest.raises(ValidationError):
            Facility()  # type: ignore[call-arg]

    def test_defaults_are_none(self) -> None:
        f = Facility(source_url="https://example.com/")
        assert f.name is None
        assert f.category is None
        assert f.description is None
        assert f.availability is None

    def test_extracted_at_defaults_to_utc(self) -> None:
        f = Facility(source_url="https://example.com/")
        assert isinstance(f.extracted_at, datetime)
        assert f.extracted_at.tzinfo is not None

    def test_str_strip_whitespace(self) -> None:
        d = Doctor(source_url="https://example.com/", name="  Dr. Smith  ")
        assert d.name == "Dr. Smith"


class TestDoctor:
    def test_shallow_fields(self) -> None:
        d = Doctor(
            source_url="https://example.com/doctor/x",
            name="Dr. Nitin S. Walia",
            speciality="Dermatology",
            department="Dermatology",
            designation="Principal Consultant",
            experience="20 Years",
            gender="Female",
            profile_url="https://example.com/doctor/x",
        )
        assert d.experience == "20 Years"
        assert d.designation == "Principal Consultant"

    def test_empty_doctor_is_valid(self) -> None:
        d = Doctor(source_url="https://example.com/")
        assert d.name is None


class TestHospitalOverview:
    def test_capacity_fields(self) -> None:
        h = HospitalOverview(
            source_url="https://example.com/hospital-network/saket",
            name="Max Smart Super Speciality Hospital, Saket",
            bed_count=650,
        )
        assert h.bed_count == 650
        assert h.parent_organization is None


class TestInfrastructure:
    def test_numeric_capacity(self) -> None:
        i = Infrastructure(
            source_url="https://example.com/",
            total_beds=650,
            operation_theatres=25,
            critical_care_beds=170,
        )
        assert i.total_beds == 650
        assert i.operation_theatres == 25


class TestContact:
    def test_address_fields(self) -> None:
        c = Contact(
            source_url="https://example.com/",
            address="12, Press Enclave Marg, Saket",
            city="New Delhi",
            postal_code="110017",
            country="IN",
            latitude=28.5277928,
        )
        assert c.city == "New Delhi"
        assert c.postal_code == "110017"

    def test_phone_types(self) -> None:
        c = Contact(
            source_url="https://example.com/",
            phone="+91 926 888 0303",
            appointment_phone="8860444888",
            emergency_phone="011-40554055",
        )
        assert c.appointment_phone == "8860444888"


class TestDataset:
    def test_empty_lists_by_default(self) -> None:
        ds = HospitalDataset()
        assert ds.doctors == []
        assert ds.hospital is None
        assert ds.sources == []

    def test_holds_entities(self) -> None:
        ds = HospitalDataset(
            hospital=HospitalOverview(source_url="u", name="H"),
            doctors=[Doctor(source_url="u", name="Dr. A")],
            awards=[Award(source_url="u", name="Best Hospital", year=2025)],
        )
        assert ds.hospital is not None
        assert len(ds.doctors) == 1
        assert ds.awards[0].year == 2025

    def test_json_round_trip(self) -> None:
        ds = HospitalDataset(
            hospital=HospitalOverview(source_url="u", name="H", bed_count=100),
            contacts=[Contact(source_url="u", phone="123")],
            insurance=[InsuranceProvider(source_url="u", name="ICICI", cashless=True)],
        )
        restored = HospitalDataset.model_validate_json(ds.model_dump_json())
        assert restored == ds


class TestSourceRecord:
    def test_provenance_fields(self) -> None:
        s = SourceRecord(
            source_url="https://example.com/doctor/x",
            page_title="Dr. Nitin S. Walia",
            entity="Doctors",
        )
        assert s.entity == "Doctors"
