"""Accreditation record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class Accreditation(NamedEntity):
    """An accreditation or certification held by the hospital."""

    name: str | None = None
    organization: str | None = None
    status: str | None = None
    year: int | None = None
    description: str | None = None
