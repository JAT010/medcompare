"""Award record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class Award(NamedEntity):
    """An award won by the hospital."""

    name: str | None = None
    organization: str | None = None
    year: int | None = None
    description: str | None = None
