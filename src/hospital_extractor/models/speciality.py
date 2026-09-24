"""Speciality record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class Speciality(NamedEntity):
    """A medical speciality offered by the hospital."""

    name: str | None = None
    category: str | None = None
    description: str | None = None
