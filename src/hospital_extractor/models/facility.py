"""Facility record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class Facility(NamedEntity):
    """A hospital facility (ICU, OPD, pharmacy, OT, ...)."""

    availability: str | None = None
