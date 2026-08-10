"""Department record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class Department(NamedEntity):
    """A hospital department (name + optional description)."""

    description: str | None = None
