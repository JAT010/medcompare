"""Procedure record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class Procedure(NamedEntity):
    """A surgical or medical procedure performed at the hospital."""

    name: str | None = None
    speciality: str | None = None
    description: str | None = None
