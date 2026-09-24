"""Doctor record — hospital-relevant, shallow depth only."""

from __future__ import annotations

from hospital_extractor.models.base import EntityRecord


class Doctor(EntityRecord):
    """Shallow hospital-relevant information about a doctor.

    By design this does not include deep biographical data (publications,
    university history, memberships, ...). Experience is kept as the
    free-text form the website actually publishes (e.g. "45+ Years").

    ``hospital_association`` records how confident we are that this doctor is
    affiliated with the target hospital: ``confirmed``, ``probable``,
    ``uncertain`` or ``not_associated``. A doctor appearing on the site does
    not automatically belong to the target hospital.
    """

    name: str | None = None
    speciality: str | None = None
    department: str | None = None
    designation: str | None = None
    experience: str | None = None
    gender: str | None = None
    opd_hours: str | None = None
    location: str | None = None
    profile_url: str | None = None
    hospital_association: str | None = None
