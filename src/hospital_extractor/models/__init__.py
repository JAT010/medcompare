"""Pydantic models for all extracted hospital entities."""

from __future__ import annotations

from hospital_extractor.models.accreditation import Accreditation
from hospital_extractor.models.base import EntityRecord, NamedEntity
from hospital_extractor.models.conflict import Conflict
from hospital_extractor.models.contact import Contact
from hospital_extractor.models.dataset import ENTITY_FIELDS, HospitalDataset
from hospital_extractor.models.department import Department
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.facility import Facility
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.models.procedure import Procedure
from hospital_extractor.models.quality import DataQuality
from hospital_extractor.models.source import SourceRecord
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.models.technology import Technology
from hospital_extractor.models.treatment import Treatment

__all__ = [
    "Accreditation",
    "Conflict",
    "Contact",
    "DataQuality",
    "Department",
    "Doctor",
    "EntityRecord",
    "Facility",
    "HospitalDataset",
    "HospitalOverview",
    "Infrastructure",
    "NamedEntity",
    "Procedure",
    "SourceRecord",
    "Speciality",
    "Technology",
    "Treatment",
    "ENTITY_FIELDS",
]
