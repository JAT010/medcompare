"""Container holding all extracted hospital entities for a single run."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hospital_extractor.models.accreditation import Accreditation
from hospital_extractor.models.base import EntityRecord
from hospital_extractor.models.contact import Contact
from hospital_extractor.models.department import Department
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.facility import Facility
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.models.metadata import SourceMetadata
from hospital_extractor.models.procedure import Procedure
from hospital_extractor.models.quality import DataQuality
from hospital_extractor.models.source import SourceRecord
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.models.technology import Technology
from hospital_extractor.models.treatment import Treatment

ENTITY_FIELDS: dict[str, type[EntityRecord]] = {
    "Contact": Contact,
    "Infrastructure": Infrastructure,
    "Facilities": Facility,
    "Departments": Department,
    "Specialities": Speciality,
    "Doctors": Doctor,
    "Treatments": Treatment,
    "Procedures": Procedure,
    "Medical Technology": Technology,
    "Accreditations": Accreditation,
}


class HospitalDataset(BaseModel):
    """The complete normalized extraction result for one hospital website.

    This is the intermediate structured representation; Excel/CSV export is
    derived from it, never the other way around.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    hospital: HospitalOverview | None = None
    contacts: list[Contact] = Field(default_factory=list)
    infrastructure: list[Infrastructure] = Field(default_factory=list)
    facilities: list[Facility] = Field(default_factory=list)
    departments: list[Department] = Field(default_factory=list)
    specialities: list[Speciality] = Field(default_factory=list)
    doctors: list[Doctor] = Field(default_factory=list)
    treatments: list[Treatment] = Field(default_factory=list)
    procedures: list[Procedure] = Field(default_factory=list)
    technologies: list[Technology] = Field(default_factory=list)
    accreditations: list[Accreditation] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)
    quality: DataQuality = Field(default_factory=DataQuality)
    source: SourceMetadata | None = None
