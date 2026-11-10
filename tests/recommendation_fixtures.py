"""Synthetic recommendation fixtures.

Every hospital, doctor, value and URL in this module is fictional. These
fixtures exist only to exercise deterministic scoring and must never be read as
real hospital data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from hospital_extractor.models.contact import Contact
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.department import Department
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.facility import Facility
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.models.quality import DataQuality
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.models.technology import Technology
from hospital_extractor.models.treatment import Treatment
from hospital_extractor.recommendation.schemas import HospitalCandidate

DoctorSpec = tuple[str | None, str | None, str | None]


def _provenance(label: str) -> dict[str, object]:
    return {
        "source_url": f"https://fictional.example/{label}",
        "page_title": f"Fictional {label}",
        "context": "Synthetic fixture - not real hospital data",
        "extraction_method": "text",
        "source_strength": "medium",
        "confidence": 0.9,
    }


def make_candidate(
    hospital_id: str,
    *,
    name: str | None = None,
    city: str | None = None,
    state: str | None = None,
    postal_code: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    emergency_phone: str | None = None,
    total_beds: int | None = None,
    icu_beds: int | None = None,
    operation_theatres: int | None = None,
    cath_labs: int | None = None,
    doctor_specs: Sequence[DoctorSpec] = (),
    departments: Sequence[str] = (),
    specialities: Sequence[str] = (),
    treatments: Sequence[str] = (),
    facilities: Sequence[str] = (),
    technologies: Sequence[str] = (),
    category_coverage: Mapping[str, str] | None = None,
) -> HospitalCandidate:
    """Build one fictional candidate with explicit, caller-controlled fields."""
    hospital = (
        HospitalOverview(name=name, **_provenance(f"hospital-{hospital_id}"))
        if name is not None
        else None
    )
    contact = Contact(
        city=city,
        state=state,
        postal_code=postal_code,
        latitude=latitude,
        longitude=longitude,
        emergency_phone=emergency_phone,
        **_provenance(f"contact-{hospital_id}"),
    )
    infrastructure: list[Infrastructure] = []
    if any(
        value is not None
        for value in (total_beds, icu_beds, operation_theatres, cath_labs)
    ):
        infrastructure.append(
            Infrastructure(
                total_beds=total_beds,
                icu_beds=icu_beds,
                operation_theatres=operation_theatres,
                cath_labs=cath_labs,
                **_provenance(f"infrastructure-{hospital_id}"),
            )
        )
    doctors = [
        Doctor(
            name=f"Dr Fictional {hospital_id} {index}",
            experience=experience,
            opd_hours=opd_hours,
            hospital_association=association,
            **_provenance(f"doctor-{hospital_id}-{index}"),
        )
        for index, (experience, opd_hours, association) in enumerate(doctor_specs)
    ]
    dataset = HospitalDataset(
        hospital=hospital,
        contacts=[contact],
        infrastructure=infrastructure,
        facilities=[
            Facility(name=value, **_provenance(f"facility-{hospital_id}-{index}"))
            for index, value in enumerate(facilities)
        ],
        departments=[
            Department(name=value, **_provenance(f"department-{hospital_id}-{index}"))
            for index, value in enumerate(departments)
        ],
        specialities=[
            Speciality(name=value, **_provenance(f"speciality-{hospital_id}-{index}"))
            for index, value in enumerate(specialities)
        ],
        doctors=doctors,
        treatments=[
            Treatment(name=value, **_provenance(f"treatment-{hospital_id}-{index}"))
            for index, value in enumerate(treatments)
        ],
        technologies=[
            Technology(name=value, **_provenance(f"technology-{hospital_id}-{index}"))
            for index, value in enumerate(technologies)
        ],
        quality=DataQuality(category_coverage=dict(category_coverage or {})),
    )
    return HospitalCandidate(hospital_id=hospital_id, name=name, dataset=dataset)
