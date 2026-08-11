"""Tests for the 25-field intake schema, projection and models."""

from __future__ import annotations

from hospital_extractor.extraction.intake import project_intake
from hospital_extractor.extraction.page_analyzer import analyze_page
from hospital_extractor.models.accreditation import Accreditation
from hospital_extractor.models.contact import Contact
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.models.intake import IntakeEvidence, MasterIntake
from hospital_extractor.models.procedure import Procedure
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.schema.intake import (
    IDENTITY_SECTION,
    INTAKE_FIELD_COUNT,
    INTAKE_FIELDS,
    TAXONOMY_SECTION,
    boolean_keys,
    intake_field,
    intake_labels,
)

URL = "https://example.com/"


def _overview(**overrides: object) -> HospitalOverview:
    values: dict[str, object] = {
        "name": "Saket Super Speciality Hospital",
        "established_year": 2012,
        "bed_count": 650,
        "doctor_count": 200,
        "logo_url": "https://example.com/logo.png",
        "website": URL,
        "source_url": URL,
        "extraction_method": "jsonld",
        "source_strength": "high",
    }
    values.update(overrides)
    return HospitalOverview(**values)


def _contact(**overrides: object) -> Contact:
    values: dict[str, object] = {
        "address": "Press Enclave Road, Saket",
        "locality": "Saket",
        "city": "New Delhi",
        "state": "Delhi",
        "postal_code": "110017",
        "source_url": URL,
        "extraction_method": "jsonld",
        "source_strength": "high",
    }
    values.update(overrides)
    return Contact(**values)


def _infrastructure(**overrides: object) -> Infrastructure:
    values: dict[str, object] = {
        "total_beds": 650,
        "icu_beds": 90,
        "source_url": URL,
        "extraction_method": "text",
        "source_strength": "medium",
    }
    values.update(overrides)
    return Infrastructure(**values)


class TestIntakeSchema:
    def test_field_count_is_25(self) -> None:
        assert INTAKE_FIELD_COUNT == 25
        assert len(INTAKE_FIELDS) == 25
        assert len(intake_labels()) == 25
        assert len(set(intake_labels())) == 25

    def test_exact_labels_and_order(self) -> None:
        assert intake_labels() == [
            "Hospital Name",
            "PIN Code",
            "Logo Image URL",
            "City",
            "Area / Locality",
            "Established Year",
            "Total Beds",
            "ICU Beds",
            "Doctor Count",
            "Da Vinci Robotic Surgery",
            "Advanced Laparoscopy / MIS",
            "MRI (3 Tesla or higher)",
            "NABH Status",
            "JCI Accredited",
            "ICU",
            "Cardiac Sciences Enabled",
            "Bypass Surgery (CABG)",
            "Angioplasty & Stenting",
            "Heart Valve Replacement",
            "Heart Valve Repair",
            "Pacemaker / ICD Implantation",
            "Heart Transplant",
            "Orthopaedics & Joint Replacement Enabled",
            "Total Knee Replacement",
            "Total Hip Replacement",
        ]

    def test_sections_and_keys(self) -> None:
        identity_keys = [field.key for field in INTAKE_FIELDS if field.section == IDENTITY_SECTION]
        taxonomy_keys = [field.key for field in INTAKE_FIELDS if field.section == TAXONOMY_SECTION]
        assert "hospital_name" in identity_keys
        assert "logo_image_url" in identity_keys
        assert "cardiac_sciences_enabled" in taxonomy_keys
        assert len(identity_keys) + len(taxonomy_keys) == 25
        assert boolean_keys() == {field.key for field in INTAKE_FIELDS if field.kind == "boolean"}

    def test_intake_field_lookup(self) -> None:
        assert intake_field("pin_code").label == "PIN Code"
        assert intake_field("total_beds").kind == "number"
        assert intake_field("jci_accredited").is_boolean


class TestMasterIntake:
    def test_to_dict_and_check(self) -> None:
        intake = MasterIntake(
            hospital_name="Saket",
            city="New Delhi",
            booleans={"icu": "yes", "jci_accredited": "unknown"},
            evidence={
                "icu": [
                    IntakeEvidence.yes("icu", "90 ICU beds", URL, method="record"),
                ]
            },
        )
        data = intake.to_dict()
        assert data["hospital_name"] == "Saket"
        assert data["icu"] == "yes"
        assert data["booleans"]["jci_accredited"] == "unknown"
        assert intake.check() == []

    def test_check_flags_unknown_boolean_key(self) -> None:
        intake = MasterIntake(booleans={"not_a_field": "yes"})
        problems = intake.check()
        assert any("not in schema" in problem for problem in problems)

    def test_decide_rule(self) -> None:
        intake = MasterIntake()
        intake.add_evidence(IntakeEvidence.no("icu", "no ICU", URL, method="text", strength=10.0))
        assert intake.decide("icu") == "no"
        intake.add_evidence(IntakeEvidence.yes("icu", "ICU", URL, method="record"))
        assert intake.decide("icu") == "no"
        assert intake.decide("jci_accredited") == "unknown"


class TestProjectIntake:
    def _dataset(self) -> HospitalDataset:
        return HospitalDataset(
            hospital=_overview(),
            contacts=[_contact()],
            infrastructure=[_infrastructure()],
        )

    def test_identity_and_numbers(self) -> None:
        intake = project_intake(self._dataset())
        assert intake.hospital_name == "Saket Super Speciality Hospital"
        assert intake.pin_code == "110017"
        assert intake.logo_image_url == "https://example.com/logo.png"
        assert intake.city == "New Delhi"
        assert intake.area_locality == "Saket"
        assert intake.established_year == 2012
        assert intake.total_beds == 650
        assert intake.icu_beds == 90
        assert intake.doctor_count == 200

    def test_booleans_from_records(self) -> None:
        dataset = self._dataset()
        dataset.specialities = [Speciality(name="Cardiac Sciences", source_url=URL)]
        dataset.procedures = [
            Procedure(name="Bypass Surgery (CABG)", source_url=URL),
            Procedure(name="Angioplasty & Stenting", source_url=URL),
        ]
        dataset.accreditations = [Accreditation(name="JCI Accreditation", source_url=URL)]
        intake = project_intake(dataset)
        assert intake.booleans["cardiac_sciences_enabled"] == "yes"
        assert intake.booleans["bypass_surgery_cabg"] == "yes"
        assert intake.booleans["angioplasty_stenting"] == "yes"
        assert intake.booleans["jci_accredited"] == "yes"
        assert intake.booleans["da_vinci_robotic_surgery"] == "unknown"
        assert intake.nabh_status is None
        assert intake.evidence["bypass_surgery_cabg"]

    def test_nabh_status(self) -> None:
        dataset = self._dataset()
        dataset.accreditations = [Accreditation(name="NABH Accreditation", source_url=URL)]
        assert project_intake(dataset).nabh_status == "accredited"

    def test_unknown_booleans_without_evidence(self) -> None:
        intake = project_intake(self._dataset())
        assert intake.booleans["heart_transplant"] == "unknown"
        assert intake.booleans["jci_accredited"] == "unknown"

    def test_page_text_adds_evidence(self) -> None:
        dataset = self._dataset()
        page = analyze_page(
            "<html><body><p>Equipped with the Da Vinci robotic surgery system.</p></body></html>",
            "https://example.com/technology",
        )
        intake = project_intake(dataset, pages=[page])
        assert intake.booleans["da_vinci_robotic_surgery"] == "yes"

    def test_total_beds_falls_back_to_overview(self) -> None:
        dataset = self._dataset()
        dataset.infrastructure = []
        assert project_intake(dataset).total_beds == 650
