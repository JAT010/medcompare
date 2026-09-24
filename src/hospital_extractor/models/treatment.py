"""Treatment / condition record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class Treatment(NamedEntity):
    """A condition or treatment offered by the hospital."""

    name: str | None = None
    speciality: str | None = None
    description: str | None = None
