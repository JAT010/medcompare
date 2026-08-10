"""Medical technology record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class Technology(NamedEntity):
    """A medical technology or equipment available at the hospital."""

    name: str | None = None
    category: str | None = None
    description: str | None = None
