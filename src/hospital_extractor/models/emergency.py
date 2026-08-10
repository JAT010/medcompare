"""Emergency service record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class EmergencyService(NamedEntity):
    """An emergency-related service (casualty, ambulance, helpline, ...)."""

    name: str | None = None
    availability: str | None = None
    contact: str | None = None
    description: str | None = None
