"""Patient service record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class PatientService(NamedEntity):
    """A service provided to patients (lab, pharmacy, rehab, ...)."""

    name: str | None = None
    category: str | None = None
    availability: str | None = None
    description: str | None = None
