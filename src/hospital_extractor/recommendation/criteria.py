"""Registry of the twelve preference criteria and their real data support.

Static data support is deliberately conservative:

- ``supported``: the extracted data can produce a meaningful, evidence-backed
  score for the criterion.
- ``partially_supported``: only a narrow or proxy signal exists; a score is
  produced only when that signal is present.
- ``unsupported``: no extracted field exists. The criterion is accepted in the
  request schema but is never scored with a placeholder value.

Request-time preference states add ``requires_clarification`` for criteria that
have data support but lack the request inputs needed to evaluate them. Those
states live in :mod:`hospital_extractor.recommendation.preferences`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CriterionKey = Literal[
    "affordability",
    "location",
    "doctor_experience",
    "department_expertise",
    "infrastructure",
    "emergency_services",
    "doctor_availability",
    "patient_reviews",
    "diagnostic_facilities",
    "insurance_compatibility",
    "accessibility",
    "treatment_facilities",
]

CriterionSupport = Literal["supported", "partially_supported", "unsupported"]

# Backward-compatible alias for callers that imported the previous name.
SupportLevel = CriterionSupport


@dataclass(frozen=True)
class CriterionSpec:
    key: CriterionKey
    label: str
    support: CriterionSupport
    source_fields: tuple[str, ...]
    rationale: str
    required_extraction: tuple[str, ...] = ()


CRITERIA: tuple[CriterionSpec, ...] = (
    CriterionSpec(
        key="affordability",
        label="Affordability",
        support="unsupported",
        source_fields=(),
        rationale="No price, tariff, package or cost field exists in any extracted model.",
        required_extraction=(
            "structured price/tariff/package records with currency and procedure scope",
        ),
    ),
    CriterionSpec(
        key="location",
        label="Location",
        support="supported",
        source_fields=(
            "Contact.address",
            "Contact.locality",
            "Contact.city",
            "Contact.state",
            "Contact.postal_code",
            "Contact.latitude",
            "Contact.longitude",
        ),
        rationale=(
            "City/state/PIN comparison and radius distance are possible from contact records."
        ),
    ),
    CriterionSpec(
        key="doctor_experience",
        label="Doctor experience",
        support="partially_supported",
        source_fields=("Doctor.experience", "Doctor.hospital_association"),
        rationale="Experience is free text; only explicitly published year counts are parsed.",
        required_extraction=("structured years-of-experience per doctor",),
    ),
    CriterionSpec(
        key="department_expertise",
        label="Department expertise",
        support="partially_supported",
        source_fields=(
            "Department.name",
            "Speciality.name",
            "Doctor.department",
            "Doctor.speciality",
        ),
        rationale=(
            "Presence of the requested department/speciality is measurable; "
            "clinical quality is not."
        ),
        required_extraction=("per-department quality, volume or outcome data",),
    ),
    CriterionSpec(
        key="infrastructure",
        label="Infrastructure",
        support="supported",
        source_fields=(
            "Infrastructure.total_beds",
            "Infrastructure.icu_beds",
            "Infrastructure.operation_theatres",
            "Infrastructure.cath_labs",
        ),
        rationale=(
            "Published capacity counts can be normalized against documented saturation scales."
        ),
    ),
    CriterionSpec(
        key="emergency_services",
        label="Emergency services",
        support="partially_supported",
        source_fields=(
            "Contact.emergency_phone",
            "Contact.emergency_email",
            "Facility.name",
        ),
        rationale=(
            "Positive emergency evidence is usable; absence is treated as unknown, "
            "never as a negative."
        ),
        required_extraction=("explicit emergency/casualty service declarations",),
    ),
    CriterionSpec(
        key="doctor_availability",
        label="Doctor availability",
        support="partially_supported",
        source_fields=("Doctor.opd_hours", "Doctor.location"),
        rationale="Only published OPD hours are usable; absence is unknown.",
        required_extraction=("structured appointment/OPD schedules per doctor",),
    ),
    CriterionSpec(
        key="patient_reviews",
        label="Patient reviews",
        support="unsupported",
        source_fields=(),
        rationale="No rating, review, testimonial or patient-feedback field exists.",
        required_extraction=("verified patient review records with source and date",),
    ),
    CriterionSpec(
        key="diagnostic_facilities",
        label="Diagnostic facilities",
        support="partially_supported",
        source_fields=(
            "Facility.name",
            "Technology.name",
            "Procedure.name",
            "DataQuality.category_coverage",
        ),
        rationale=(
            "Requested diagnostics are matched to extracted facilities/technology; "
            "coverage metadata distinguishes not-found from unknown."
        ),
        required_extraction=("normalized diagnostic service catalog",),
    ),
    CriterionSpec(
        key="insurance_compatibility",
        label="Insurance compatibility",
        support="unsupported",
        source_fields=(),
        rationale=(
            "No insurer, TPA or cashless-network field exists; such pages are crawled "
            "but not extracted."
        ),
        required_extraction=("insurer/TPA network records",),
    ),
    CriterionSpec(
        key="accessibility",
        label="Accessibility",
        support="partially_supported",
        source_fields=("Facility.name", "Facility.description", "Technology.name"),
        rationale="Only explicit accessibility facility keywords are usable; absence is unknown.",
        required_extraction=("structured accessibility feature declarations",),
    ),
    CriterionSpec(
        key="treatment_facilities",
        label="Treatment facilities",
        support="supported",
        source_fields=(
            "Treatment.name",
            "Procedure.name",
            "Technology.name",
            "Speciality.name",
            "Department.name",
        ),
        rationale="Requested treatments are matched to extracted treatment/procedure records.",
    ),
)

CRITERIA_BY_KEY: dict[str, CriterionSpec] = {criterion.key: criterion for criterion in CRITERIA}
SUPPORTED_CRITERIA: frozenset[str] = frozenset(
    criterion.key for criterion in CRITERIA if criterion.support != "unsupported"
)


def criterion(key: str) -> CriterionSpec:
    """Return the specification for *key*, raising ``KeyError`` when unknown."""
    return CRITERIA_BY_KEY[key]


def is_supported(key: str) -> bool:
    """True when extracted data can produce usable evidence for *key*."""
    return key in SUPPORTED_CRITERIA
