"""Hospital-level overview record."""

from __future__ import annotations

from hospital_extractor.models.base import EntityRecord


class HospitalOverview(EntityRecord):
    """Top-level hospital facts.

    A single record per extraction run (or ``None`` when the hospital page
    cannot be identified).
    """

    name: str | None = None
    hospital_type: str | None = None
    description: str | None = None
    parent_organization: str | None = None
    established_year: int | None = None
    status: str | None = None
    bed_count: int | None = None
    doctor_count: int | None = None
    staff_count: int | None = None
    speciality_count: int | None = None
    department_count: int | None = None
    website: str | None = None
    logo_url: str | None = None
