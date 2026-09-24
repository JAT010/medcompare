"""Canonical category registry.

Defines the 11 canonical categories the extraction pipeline maps into, their
worksheet labels and the Pydantic model that represents them. This registry is
the authority used by dynamic schema discovery, validation and the dataset
builder — extraction modules never invent categories outside it. Only
categories that feed the final 25-field intake are kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hospital_extractor.models.accreditation import Accreditation
from hospital_extractor.models.base import EntityRecord
from hospital_extractor.models.contact import Contact
from hospital_extractor.models.department import Department
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.facility import Facility
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.models.procedure import Procedure
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.models.technology import Technology
from hospital_extractor.models.treatment import Treatment

__all__ = ["CATEGORY_LABELS", "CanonicalCategory", "CANONICAL_CATEGORIES", "category_for"]


@dataclass(frozen=True)
class CanonicalCategory:
    """One canonical category.

    ``key`` matches the :class:`HospitalDataset` field name. ``single_record``
    marks categories that hold at most one merged record (hospital, contact,
    infrastructure, visiting).
    """

    key: str
    label: str
    model: type[EntityRecord]
    single_record: bool = False
    required_fields: tuple[str, ...] = ("name",)


# Order matches docs/canonical-schema.md §1-§18.
CANONICAL_CATEGORIES: tuple[CanonicalCategory, ...] = (
    CanonicalCategory(
        "hospital",
        "Hospital Overview",
        HospitalOverview,
        single_record=True,
        required_fields=("name",),
    ),
    CanonicalCategory("contacts", "Location & Contact", Contact, single_record=True,
                      required_fields=()),
    CanonicalCategory(
        "infrastructure", "Capacity & Infrastructure", Infrastructure, single_record=True,
        required_fields=(),
    ),
    CanonicalCategory("facilities", "Facilities", Facility),
    CanonicalCategory("departments", "Departments", Department),
    CanonicalCategory("specialities", "Specialities", Speciality),
    CanonicalCategory("doctors", "Doctors", Doctor),
    CanonicalCategory("treatments", "Treatments / Conditions", Treatment),
    CanonicalCategory("procedures", "Procedures", Procedure),
    CanonicalCategory("technologies", "Medical Technology", Technology),
    CanonicalCategory("accreditations", "Accreditations", Accreditation),
)

CATEGORY_LABELS: dict[str, str] = {
    category.key: category.label for category in CANONICAL_CATEGORIES
}

_BY_KEY: dict[str, CanonicalCategory] = {
    category.key: category for category in CANONICAL_CATEGORIES
}
_BY_MODEL: dict[type[Any], CanonicalCategory] = {
    category.model: category for category in CANONICAL_CATEGORIES
}


def category_for(key: str) -> CanonicalCategory:
    """Return the canonical category for a dataset key.

    Raises :class:`KeyError` for unknown keys so that typos surface loudly.
    """
    return _BY_KEY[key]


def canonical_key_for_model(model: type[EntityRecord]) -> str:
    """Return the canonical dataset key for a record model."""
    return _BY_MODEL[model].key
