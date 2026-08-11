"""Field-specific extraction responsibilities for the 25-field intake.

Every extraction operation answers one question: *does this page provide
evidence for one of the 25 required intake fields?* This registry makes the
answer explicit and testable.

The seven responsibility groups (IDENTITY, CAPACITY, TECHNOLOGY,
ACCREDITATION, FACILITY, CARDIAC, ORTHOPAEDICS) each own a disjoint slice of
the 25 intake fields and declare the canonical categories that may carry
evidence for them. A category being listed here is never proof by itself -
the boolean resolver still decides from explicit text matches, and numeric /
identity fields come only from extracted values with provenance.
"""

from __future__ import annotations

from dataclasses import dataclass

from hospital_extractor.schema.intake import INTAKE_FIELDS

__all__ = [
    "FIELD_GROUPS",
    "EvidenceGroup",
    "group_for_field",
    "responsible_fields",
]

_IDENTITY_CATEGORIES = ("hospital", "contacts")
_CAPACITY_CATEGORIES = ("hospital", "infrastructure", "facilities")
_TECHNOLOGY_CATEGORIES = ("technologies", "facilities")
_ACCREDITATION_CATEGORIES = ("accreditations",)
_FACILITY_CATEGORIES = ("infrastructure", "facilities")
_CARDIAC_CATEGORIES = ("specialities", "departments", "procedures", "treatments")
_ORTHOPAEDICS_CATEGORIES = ("specialities", "departments", "procedures", "treatments")


@dataclass(frozen=True)
class EvidenceGroup:
    """One extraction responsibility group and the intake fields it owns."""

    key: str
    label: str
    field_keys: tuple[str, ...]
    evidence_categories: tuple[str, ...]
    description: str


FIELD_GROUPS: tuple[EvidenceGroup, ...] = (
    EvidenceGroup(
        key="identity",
        label="IDENTITY",
        field_keys=(
            "hospital_name",
            "pin_code",
            "logo_image_url",
            "city",
            "area_locality",
            "established_year",
        ),
        evidence_categories=_IDENTITY_CATEGORIES,
        description="Who and where the hospital is: name, location and logo.",
    ),
    EvidenceGroup(
        key="capacity",
        label="CAPACITY",
        field_keys=("total_beds", "icu_beds", "doctor_count"),
        evidence_categories=_CAPACITY_CATEGORIES,
        description="Published counts of beds, ICU beds and doctors.",
    ),
    EvidenceGroup(
        key="technology",
        label="TECHNOLOGY",
        field_keys=(
            "da_vinci_robotic_surgery",
            "advanced_laparoscopy_mis",
            "mri_3_tesla_or_higher",
        ),
        evidence_categories=_TECHNOLOGY_CATEGORIES,
        description="Explicitly named equipment and surgery technology.",
    ),
    EvidenceGroup(
        key="accreditation",
        label="ACCREDITATION",
        field_keys=("nabh_status", "jci_accredited"),
        evidence_categories=_ACCREDITATION_CATEGORIES,
        description="Explicit accreditation or certification claims.",
    ),
    EvidenceGroup(
        key="facility",
        label="FACILITY",
        field_keys=("icu",),
        evidence_categories=_FACILITY_CATEGORIES,
        description="Standalone facility booleans such as an ICU.",
    ),
    EvidenceGroup(
        key="cardiac",
        label="CARDIAC",
        field_keys=(
            "cardiac_sciences_enabled",
            "bypass_surgery_cabg",
            "angioplasty_stenting",
            "heart_valve_replacement",
            "heart_valve_repair",
            "pacemaker_icd_implantation",
            "heart_transplant",
        ),
        evidence_categories=_CARDIAC_CATEGORIES,
        description="Cardiac sciences and every explicit cardiac procedure.",
    ),
    EvidenceGroup(
        key="orthopaedics",
        label="ORTHOPAEDICS",
        field_keys=(
            "orthopaedics_joint_replacement_enabled",
            "total_knee_replacement",
            "total_hip_replacement",
        ),
        evidence_categories=_ORTHOPAEDICS_CATEGORIES,
        description="Orthopaedics and every explicit joint-replacement procedure.",
    ),
)

_GROUP_BY_FIELD: dict[str, EvidenceGroup] = {
    field_key: group for group in FIELD_GROUPS for field_key in group.field_keys
}

_SCHEMA_KEYS = {field.key for field in INTAKE_FIELDS}
_OWNED_KEYS = {key for group in FIELD_GROUPS for key in group.field_keys}
if _SCHEMA_KEYS != _OWNED_KEYS:
    missing = sorted(_SCHEMA_KEYS - _OWNED_KEYS)
    extra = sorted(_OWNED_KEYS - _SCHEMA_KEYS)
    raise RuntimeError(
        "field responsibility registry out of sync with the 25-field schema: "
        f"missing={missing} extra={extra}"
    )


def group_for_field(field_key: str) -> EvidenceGroup:
    """Return the :class:`EvidenceGroup` that owns *field_key*."""
    try:
        return _GROUP_BY_FIELD[field_key]
    except KeyError:
        raise KeyError(f"no responsibility group owns intake field {field_key!r}") from None


def responsible_fields(group_key: str) -> tuple[str, ...]:
    """Field keys owned by the group with *group_key* (empty when unknown)."""
    for group in FIELD_GROUPS:
        if group.key == group_key:
            return group.field_keys
    return ()
