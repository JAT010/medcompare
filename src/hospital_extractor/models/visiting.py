"""Visiting / admission information record."""

from __future__ import annotations

from hospital_extractor.models.base import EntityRecord


class VisitingInformation(EntityRecord):
    """Visiting hours, OPD hours and admission/registration guidance.

    Free-text fields: websites publish these as prose, not structured values.
    """

    visiting_hours: str | None = None
    opd_hours: str | None = None
    appointment_information: str | None = None
    registration_information: str | None = None
    admission_information: str | None = None
    discharge_information: str | None = None
