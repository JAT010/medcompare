"""Authoritative 25-field final master hospital intake schema.

This module is the SINGLE authority for the final intake deliverable:

- the exact 25 fields
- their exact order (1-25)
- their exact human-readable display labels
- their data types
- their logical sections

Extraction, projection, provenance and the future CSV/XLSX exporters all
consume this registry; no other module may define a competing field list.
Internal Python names stay snake_case (e.g. ``da_vinci_robotic_surgery``),
but the *output* labels are fixed and human-readable (e.g.
``Da Vinci Robotic Surgery``).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "INTAKE_FIELDS",
    "INTAKE_FIELD_COUNT",
    "IntakeField",
    "SECTIONS",
    "boolean_keys",
    "intake_field",
    "intake_labels",
    "numeric_keys",
]

IDENTITY_SECTION = "Identity & Location"
TAXONOMY_SECTION = "Advanced Taxonomy — Departments & Procedures"

KIND_TEXT = "text"
KIND_URL = "url"
KIND_NUMBER = "number"
KIND_DROPDOWN = "dropdown"
KIND_BOOLEAN = "boolean"

# Logical sections preserved in human-readable output (the machine CSV stays
# flat). Order matters and matches the field order below.
SECTIONS: tuple[str, ...] = (IDENTITY_SECTION, TAXONOMY_SECTION)


@dataclass(frozen=True)
class IntakeField:
    """One final intake field.

    - ``key``: python-friendly attribute name used by the projection model.
    - ``label``: exact display label used by the final CSV/XLSX output.
    - ``kind``: one of ``text`` / ``url`` / ``number`` / ``dropdown`` /
      ``boolean``.
    - ``section``: logical grouping for the human-readable form.
    """

    key: str
    label: str
    kind: str
    section: str

    @property
    def is_boolean(self) -> bool:
        return self.kind == KIND_BOOLEAN

    @property
    def is_numeric(self) -> bool:
        return self.kind == KIND_NUMBER


# The single authoritative field list, in the exact required order.
INTAKE_FIELDS: tuple[IntakeField, ...] = (
    IntakeField("hospital_name", "Hospital Name", KIND_TEXT, IDENTITY_SECTION),
    IntakeField("pin_code", "PIN Code", KIND_TEXT, IDENTITY_SECTION),
    IntakeField("logo_image_url", "Logo Image URL", KIND_URL, IDENTITY_SECTION),
    IntakeField("city", "City", KIND_DROPDOWN, IDENTITY_SECTION),
    IntakeField("area_locality", "Area / Locality", KIND_TEXT, IDENTITY_SECTION),
    IntakeField("established_year", "Established Year", KIND_NUMBER, IDENTITY_SECTION),
    IntakeField("total_beds", "Total Beds", KIND_NUMBER, IDENTITY_SECTION),
    IntakeField("icu_beds", "ICU Beds", KIND_NUMBER, IDENTITY_SECTION),
    IntakeField("doctor_count", "Doctor Count", KIND_NUMBER, IDENTITY_SECTION),
    IntakeField(
        "da_vinci_robotic_surgery",
        "Da Vinci Robotic Surgery",
        KIND_BOOLEAN,
        IDENTITY_SECTION,
    ),
    IntakeField(
        "advanced_laparoscopy_mis",
        "Advanced Laparoscopy / MIS",
        KIND_BOOLEAN,
        IDENTITY_SECTION,
    ),
    IntakeField(
        "mri_3_tesla_or_higher",
        "MRI (3 Tesla or higher)",
        KIND_BOOLEAN,
        IDENTITY_SECTION,
    ),
    IntakeField("nabh_status", "NABH Status", KIND_DROPDOWN, IDENTITY_SECTION),
    IntakeField("jci_accredited", "JCI Accredited", KIND_BOOLEAN, IDENTITY_SECTION),
    IntakeField("icu", "ICU", KIND_BOOLEAN, IDENTITY_SECTION),
    IntakeField(
        "cardiac_sciences_enabled",
        "Cardiac Sciences Enabled",
        KIND_BOOLEAN,
        TAXONOMY_SECTION,
    ),
    IntakeField(
        "bypass_surgery_cabg",
        "Bypass Surgery (CABG)",
        KIND_BOOLEAN,
        TAXONOMY_SECTION,
    ),
    IntakeField(
        "angioplasty_stenting",
        "Angioplasty & Stenting",
        KIND_BOOLEAN,
        TAXONOMY_SECTION,
    ),
    IntakeField(
        "heart_valve_replacement",
        "Heart Valve Replacement",
        KIND_BOOLEAN,
        TAXONOMY_SECTION,
    ),
    IntakeField(
        "heart_valve_repair",
        "Heart Valve Repair",
        KIND_BOOLEAN,
        TAXONOMY_SECTION,
    ),
    IntakeField(
        "pacemaker_icd_implantation",
        "Pacemaker / ICD Implantation",
        KIND_BOOLEAN,
        TAXONOMY_SECTION,
    ),
    IntakeField("heart_transplant", "Heart Transplant", KIND_BOOLEAN, TAXONOMY_SECTION),
    IntakeField(
        "orthopaedics_joint_replacement_enabled",
        "Orthopaedics & Joint Replacement Enabled",
        KIND_BOOLEAN,
        TAXONOMY_SECTION,
    ),
    IntakeField(
        "total_knee_replacement",
        "Total Knee Replacement",
        KIND_BOOLEAN,
        TAXONOMY_SECTION,
    ),
    IntakeField(
        "total_hip_replacement",
        "Total Hip Replacement",
        KIND_BOOLEAN,
        TAXONOMY_SECTION,
    ),
)

INTAKE_FIELD_COUNT = len(INTAKE_FIELDS)

_BY_KEY: dict[str, IntakeField] = {field.key: field for field in INTAKE_FIELDS}
_BY_LABEL: dict[str, IntakeField] = {field.label: field for field in INTAKE_FIELDS}

_BOOLEAN_KEYS = frozenset(field.key for field in INTAKE_FIELDS if field.is_boolean)
_NUMERIC_KEYS = frozenset(field.key for field in INTAKE_FIELDS if field.is_numeric)


def intake_field(key: str) -> IntakeField:
    """Return the :class:`IntakeField` for a projection key."""
    return _BY_KEY[key]


def intake_labels() -> list[str]:
    """Display labels of all 25 fields, in the exact required order."""
    return [field.label for field in INTAKE_FIELDS]


def boolean_keys() -> frozenset[str]:
    """Projection keys of the boolean intake fields."""
    return _BOOLEAN_KEYS


def numeric_keys() -> frozenset[str]:
    """Projection keys of the numeric intake fields."""
    return _NUMERIC_KEYS
