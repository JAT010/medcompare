"""Pydantic models for all extracted hospital entities."""

from __future__ import annotations

from hospital_extractor.models.accreditation import Accreditation
from hospital_extractor.models.award import Award
from hospital_extractor.models.base import EntityRecord, NamedEntity
from hospital_extractor.models.contact import Contact
from hospital_extractor.models.dataset import ENTITY_FIELDS, HospitalDataset
from hospital_extractor.models.department import Department
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.emergency import EmergencyService
from hospital_extractor.models.facility import Facility
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.models.insurance import InsuranceProvider
from hospital_extractor.models.other import OtherInformation
from hospital_extractor.models.procedure import Procedure
from hospital_extractor.models.program import Program
from hospital_extractor.models.service import PatientService
from hospital_extractor.models.source import SourceRecord
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.models.technology import Technology
from hospital_extractor.models.treatment import Treatment
from hospital_extractor.models.visiting import VisitingInformation

__all__ = [
    "Accreditation",
    "Award",
    "Contact",
    "Department",
    "Doctor",
    "EmergencyService",
    "EntityRecord",
    "Facility",
    "HospitalDataset",
    "HospitalOverview",
    "Infrastructure",
    "InsuranceProvider",
    "NamedEntity",
    "OtherInformation",
    "PatientService",
    "Procedure",
    "Program",
    "SourceRecord",
    "Speciality",
    "Technology",
    "Treatment",
    "VisitingInformation",
    "ENTITY_FIELDS",
]
