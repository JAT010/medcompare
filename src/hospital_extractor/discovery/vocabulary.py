"""Controlled, versioned vocabulary for deterministic discovery interpretation.

Intentionally small and explicit (P1-A): only unambiguous, documented terms are
recognized. Unknown terms must be preserved as unsupported requirements with
their original wording (never silently mapped). Extend by adding canonical terms
and synonym mappings here so changes remain reviewable and versionable.

No geocoding, no external taxonomy dependencies.
"""

from __future__ import annotations

import re

VOCABULARY_VERSION = "discovery.vocabulary.v1"

# --- Location / parsing cue phrases (deterministic, English patterns) ---
LOCATION_PREPOSITIONS: tuple[str, ...] = ("in", "near", "around")
RADIUS_UNITS: dict[str, str] = {
    "km": "km",
    "kms": "km",
    "kilometer": "km",
    "kilometers": "km",
    "kilometre": "km",
    "kilometres": "km",
    "mi": "mi",
    "mile": "mi",
    "miles": "mi",
    "m": "m",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
}

REQUIRED_CUES: tuple[str, ...] = (
    "must have",
    "must include",
    "required to have",
    "required",
    "need to have",
    "needs to have",
    "with a requirement for",
)
PREFERENCE_CUES: tuple[str, ...] = (
    "prefer",
    "preferably",
    "ideally",
    "if possible",
    "nice to have",
    "would like",
)
EXCLUSION_CUES: tuple[str, ...] = (
    "without",
    "excluding",
    "exclude",
    "except",
    "no ",
    "not ",
    "do not have",
    "does not have",
)

# Canonical category -> accepted surface forms/synonyms (lowercased, normalized).
# Only unambiguous terms; this set is deliberately not comprehensive.
HOSPITAL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "general_hospital": ("general hospital", "general hospitals"),
    "multispecialty_hospital": (
        "multispecialty hospital",
        "multispecialty hospitals",
        "multi-specialty hospital",
        "multi-specialty hospitals",
        "multi specialty hospital",
        "multi specialty hospitals",
    ),
}

# Explicitly named specialties (small initial set).
SPECIALTIES: dict[str, tuple[str, ...]] = {
    "cardiology": ("cardiology", "cardiac"),
    "orthopedics": ("orthopedics", "orthopaedics", "orthopedic", "orthopaedic"),
    "oncology": ("oncology", "cancer care"),
    "neurology": ("neurology", "neurological"),
    "pediatrics": ("pediatrics", "paediatrics", "pediatric", "paediatric"),
}

# Common departments (small initial set).
DEPARTMENTS: dict[str, tuple[str, ...]] = {
    "emergency_department": ("emergency department", "emergency room", "er", "casualty"),
    "intensive_care_unit": ("intensive care unit", "icu"),
    "outpatient_department": ("outpatient department", "opd"),
    "radiology_department": ("radiology department", "radiology"),
}

# Explicitly named facilities (small initial set).
FACILITIES: dict[str, tuple[str, ...]] = {
    "ambulance": ("ambulance", "ambulance service", "ambulance services"),
    "pharmacy": ("pharmacy", "in-house pharmacy"),
    "blood_bank": ("blood bank",),
    "parking": ("parking", "car parking"),
    "wheelchair_access": ("wheelchair access", "wheelchair accessible"),
}

VOCABULARY_KINDS: tuple[str, ...] = (
    "hospital_category",
    "specialty",
    "department",
    "facility",
)


def _normalize_token(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def all_recognized_forms() -> dict[str, tuple[str, dict[str, tuple[str, ...]]]]:
    """Return kind -> (kind_label, mapping) for deterministic scanning."""
    return {
        "hospital_category": ("hospital_category", HOSPITAL_CATEGORIES),
        "specialty": ("specialty", SPECIALTIES),
        "department": ("department", DEPARTMENTS),
        "facility": ("facility", FACILITIES),
    }
