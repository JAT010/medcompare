"""Capacity and infrastructure record."""

from __future__ import annotations

from hospital_extractor.models.base import EntityRecord


class Infrastructure(EntityRecord):
    """Published capacity figures for a hospital.

    Numeric fields are ``None`` when the website does not publish them.
    Values that cannot be reliably parsed as integers may be recorded verbatim
    in ``other_capacity`` instead of being guessed.
    """

    total_beds: int | None = None
    icu_beds: int | None = None
    operation_theatres: int | None = None
    cath_labs: int | None = None
    critical_care_beds: int | None = None
    patient_rooms: int | None = None
    other_capacity: str | None = None
