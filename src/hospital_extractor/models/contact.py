"""Location & contact details record."""

from __future__ import annotations

from hospital_extractor.models.base import EntityRecord


class Contact(EntityRecord):
    """Postal address, phones, emails and geo coordinates for a hospital."""

    address: str | None = None
    locality: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    emergency_phone: str | None = None
    appointment_phone: str | None = None
    email: str | None = None
    emergency_email: str | None = None
    maps_url: str | None = None
    latitude: float | None = None
    longitude: float | None = None
