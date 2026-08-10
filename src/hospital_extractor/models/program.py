"""Hospital program record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class Program(NamedEntity):
    """A hospital program (health check-up plans, international care, ...)."""

    name: str | None = None
    category: str | None = None
    description: str | None = None
