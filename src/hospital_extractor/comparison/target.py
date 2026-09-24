"""Target resolution and controlled terminology.

Converts a user-supplied procedure, department or speciality name into a
fully-resolved :class:`ComparisonTarget` with normalised keywords, related
intake boolean keys and the canonical entity categories to search.

Design rules:

- Deterministic — no ML, no LLM, no external services.
- Controlled synonyms for known procedures derived directly from the
  existing 25-field intake schema (``schema/intake.py``).
- Unknown procedures fall back to keyword-based expansion: normalised
  tokens, common abbreviation expansion and plural/singular variants.
- The target's ``category`` field distinguishes the user's intent:
  ``"procedure"`` / ``"treatment"`` / ``"speciality"`` / ``"department"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hospital_extractor.comparison.models import ComparisonTarget
from hospital_extractor.schema.boolean_resolver import _FEATURE_TERMS

__all__ = [
    "ComparisonTarget",
    "DepartmentEntry",
    "ProcedureEntry",
    "KNOWN_DEPARTMENTS",
    "RESOLVED_CATEGORIES",
    "resolve_target",
]

# ---------------------------------------------------------------------------
# Controlled terminology — known procedures from the intake schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcedureEntry:
    """A known procedure with controlled synonyms and related metadata.

    ``intake_boolean_keys`` lists the exact boolean keys from the intake
    schema that this procedure feeds into (e.g. ``total_knee_replacement``).
    ``related_categories`` lists the canonical entity categories that should
    be searched for evidence (e.g. ``procedures``, ``treatments``,
    ``departments``).
    """

    canonical_name: str
    synonyms: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    intake_boolean_keys: tuple[str, ...] = ()
    related_categories: tuple[str, ...] = ("procedures", "treatments")
    parent_specialty: str | None = None
    parent_department: str | None = None


KNOWN_PROCEDURES: tuple[ProcedureEntry, ...] = (
    ProcedureEntry(
        canonical_name="Total Knee Replacement",
        synonyms=("knee replacement", "tkr", "total knee arthroplasty"),
        keywords=("knee", "replacement", "arthroplasty"),
        intake_boolean_keys=("total_knee_replacement",),
        related_categories=("procedures", "treatments", "departments", "specialities"),
        parent_specialty="Orthopaedics",
        parent_department="Orthopaedics & Joint Replacement",
    ),
    ProcedureEntry(
        canonical_name="Total Hip Replacement",
        synonyms=("hip replacement", "thr", "total hip arthroplasty"),
        keywords=("hip", "replacement", "arthroplasty"),
        intake_boolean_keys=("total_hip_replacement",),
        related_categories=("procedures", "treatments", "departments", "specialities"),
        parent_specialty="Orthopaedics",
        parent_department="Orthopaedics & Joint Replacement",
    ),
    ProcedureEntry(
        canonical_name="Bypass Surgery (CABG)",
        synonyms=("cabg", "coronary artery bypass", "bypass surgery", "heart bypass"),
        keywords=("bypass", "cabg", "coronary", "artery"),
        intake_boolean_keys=("bypass_surgery_cabg", "cardiac_sciences_enabled"),
        related_categories=("procedures", "treatments", "departments", "specialities"),
        parent_specialty="Cardiology",
        parent_department="Cardiac Sciences",
    ),
    ProcedureEntry(
        canonical_name="Angioplasty & Stenting",
        synonyms=("angioplasty", "stenting", "ptca", "coronary stenting"),
        keywords=("angioplasty", "stent", "stenting", "ptca", "coronary"),
        intake_boolean_keys=("angioplasty_stenting", "cardiac_sciences_enabled"),
        related_categories=("procedures", "treatments", "departments", "specialities"),
        parent_specialty="Cardiology",
        parent_department="Cardiac Sciences",
    ),
    ProcedureEntry(
        canonical_name="Heart Valve Replacement",
        synonyms=("valve replacement", "avr", "mvr", "aortic valve replacement",
                   "mitral valve replacement"),
        keywords=("valve", "replacement", "avr", "mvr", "aortic", "mitral"),
        intake_boolean_keys=("heart_valve_replacement", "cardiac_sciences_enabled"),
        related_categories=("procedures", "treatments", "departments", "specialities"),
        parent_specialty="Cardiology",
        parent_department="Cardiac Sciences",
    ),
    ProcedureEntry(
        canonical_name="Heart Valve Repair",
        synonyms=("valve repair", "mitral valve repair"),
        keywords=("valve", "repair", "mitral"),
        intake_boolean_keys=("heart_valve_repair", "cardiac_sciences_enabled"),
        related_categories=("procedures", "treatments", "departments", "specialities"),
        parent_specialty="Cardiology",
        parent_department="Cardiac Sciences",
    ),
    ProcedureEntry(
        canonical_name="Pacemaker / ICD Implantation",
        synonyms=("pacemaker", "icd", "defibrillator", "cardiac resynchronisation",
                   "crt", "pacemaker implantation"),
        keywords=("pacemaker", "icd", "defibrillator", "crt"),
        intake_boolean_keys=("pacemaker_icd_implantation", "cardiac_sciences_enabled"),
        related_categories=("procedures", "treatments", "departments", "specialities"),
        parent_specialty="Cardiology",
        parent_department="Cardiac Sciences",
    ),
    ProcedureEntry(
        canonical_name="Heart Transplant",
        synonyms=("heart transplant", "cardiac transplant"),
        keywords=("heart", "transplant", "cardiac"),
        intake_boolean_keys=("heart_transplant", "cardiac_sciences_enabled"),
        related_categories=("procedures", "treatments", "departments", "specialities"),
        parent_specialty="Cardiology",
        parent_department="Cardiac Sciences",
    ),
)

# Quick lookup by normalised name / synonym.
_PROCEDURE_INDEX: dict[str, ProcedureEntry] = {}
for _proc_entry in KNOWN_PROCEDURES:
    _PROCEDURE_INDEX[_proc_entry.canonical_name.lower()] = _proc_entry
    for _syn in _proc_entry.synonyms:
        _PROCEDURE_INDEX[_syn.lower()] = _proc_entry

# ---------------------------------------------------------------------------
# Controlled terminology — known departments
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DepartmentEntry:
    """A known department with controlled synonyms.

    ``intake_boolean_keys`` lists the boolean keys the department feeds
    (e.g. ``cardiac_sciences_enabled``), and ``related_categories`` names
    the canonical entity categories searched for evidence.
    """

    canonical_name: str
    synonyms: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    intake_boolean_keys: tuple[str, ...] = ()
    related_categories: tuple[str, ...] = (
        "departments", "specialities", "doctors", "facilities",
    )


KNOWN_DEPARTMENTS: tuple[DepartmentEntry, ...] = (
    DepartmentEntry(
        canonical_name="Cardiology",
        synonyms=("cardiac sciences", "cardiology department", "cardiac care"),
        keywords=("cardiology", "cardiac", "heart"),
        intake_boolean_keys=("cardiac_sciences_enabled",),
    ),
    DepartmentEntry(
        canonical_name="Orthopaedics",
        synonyms=("orthopedics", "orthopaedics & joint replacement", "bone and joint"),
        keywords=("orthopaedics", "orthopedics", "joint", "replacement", "arthroplasty"),
        intake_boolean_keys=("orthopaedics_joint_replacement_enabled",),
    ),
    DepartmentEntry(
        canonical_name="Neurology",
        synonyms=("neurology department", "neurosciences"),
        keywords=("neurology", "neurosciences", "neuro"),
    ),
    DepartmentEntry(
        canonical_name="Oncology",
        synonyms=("oncology department", "cancer care"),
        keywords=("oncology", "cancer", "onco"),
    ),
    DepartmentEntry(
        canonical_name="Gastroenterology",
        synonyms=("gastroenterology department", "digestive care", "gastro"),
        keywords=("gastroenterology", "gastro", "digestive"),
    ),
)

# Quick lookup by normalised name / synonym.
_DEPARTMENT_INDEX: dict[str, DepartmentEntry] = {}
for _dept_entry in KNOWN_DEPARTMENTS:
    _DEPARTMENT_INDEX[_dept_entry.canonical_name.lower()] = _dept_entry
    for _syn in _dept_entry.synonyms:
        _DEPARTMENT_INDEX[_syn.lower()] = _dept_entry

# ---------------------------------------------------------------------------
# Intake-boolean-key → related keywords (from schema/boolean_resolver.py)
# ---------------------------------------------------------------------------

_BOOLEAN_KEYWORDS: dict[str, tuple[str, ...]] = {}
for _key, _terms in _FEATURE_TERMS.items():
    _BOOLEAN_KEYWORDS[_key] = tuple(
        re.sub(r"[^\w\s]+", " ", t).replace("\\b", "").strip()
        for t in _terms
    )

# ---------------------------------------------------------------------------
# Default entity categories to search for any procedure / department
# ---------------------------------------------------------------------------

_ALL_RELATED_CATEGORIES: tuple[str, ...] = (
    "procedures",
    "treatments",
    "departments",
    "specialities",
    "doctors",
    "technologies",
    "facilities",
)

# The canonical entity categories that carry boolean intake fields.
_BOOLEAN_EVIDENCE_CATEGORIES: tuple[str, ...] = (
    "procedures",
    "treatments",
    "technologies",
    "facilities",
    "departments",
    "specialities",
    "accreditations",
)

# Which categories provide *service-level* availability evidence.
_AVAILABILITY_CATEGORIES: frozenset[str] = frozenset(
    {"procedures", "treatments", "departments", "specialities"}
)

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_ABBREVIATIONS: dict[str, str] = {
    "tkr": "total knee replacement",
    "thr": "total hip replacement",
    "cabg": "coronary artery bypass graft",
    "avr": "aortic valve replacement",
    "mvr": "mitral valve replacement",
    "ptca": "percutaneous transluminal coronary angioplasty",
    "icd": "implantable cardioverter defibrillator",
    "crt": "cardiac resynchronisation therapy",
    "mis": "minimally invasive surgery",
    "nabh": "national accreditation board for hospitals",
    "jci": "joint commission international",
}


def _normalise(text: str) -> str:
    """Lowercase, collapse whitespace, strip."""
    cleaned = " ".join(text.lower().split())
    return cleaned.strip()


def _expand_abbreviations(text: str) -> tuple[str, ...]:
    """Return variants with and without abbreviations expanded."""
    tokens = text.split()
    expanded_tokens: list[str] = []
    for token in tokens:
        if token in _ABBREVIATIONS:
            expanded_tokens.append(_ABBREVIATIONS[token])
        else:
            expanded_tokens.append(token)
    expanded = " ".join(expanded_tokens)
    if expanded != text:
        return (text, expanded)
    return (text,)


def _derive_keywords(name: str) -> list[str]:
    """Derive matching keywords from a procedure/department name.

    Returns a list of normalised keyword strings, ordered from most specific
    to least specific.  Includes the original name, expanded abbreviations
    and individual significant tokens.
    """
    normalised = _normalise(name)
    variants: list[str] = [normalised]

    # Expand abbreviations
    for variant in _expand_abbreviations(normalised):
        if variant not in variants:
            variants.append(variant)

    # Add individual significant tokens (3+ chars, not stopwords)
    stopwords = {
        "the", "and", "for", "with", "surgery", "procedure", "treatment",
        "replacement", "repair", "implantation",
    }
    for token in normalised.split():
        if len(token) >= 3 and token not in stopwords:
            if token not in variants:
                variants.append(token)

    return variants


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_target(
    name: str,
    *,
    category: str | None = None,
) -> ComparisonTarget:
    """Resolve a user-supplied procedure / department name into a target.

    ``category`` may be explicitly provided (``"procedure"``,
    ``"treatment"``, ``"speciality"``, ``"department"``).  When ``None`` the
    category is inferred from the known procedure / department registries
    (falling back to ``"procedure"`` for unknown names).

    Returns a fully-populated :class:`ComparisonTarget`.
    """
    normalised = _normalise(name)

    # 1. Check the controlled terminology — procedures first
    entry = _PROCEDURE_INDEX.get(normalised)

    if entry is not None:
        keywords = list(entry.keywords)
        # Also include synonyms so the matcher can find them
        for syn in entry.synonyms:
            norm_syn = _normalise(syn)
            if norm_syn not in keywords:
                keywords.append(norm_syn)
        if normalised not in keywords:
            keywords.insert(0, normalised)

        resolved_category = category or "procedure"

        # Collect intake boolean keys, also pulling in parent booleans
        intake_keys = list(entry.intake_boolean_keys)

        return ComparisonTarget(
            name=entry.canonical_name,
            category=resolved_category,
            keywords=keywords,
            intake_boolean_keys=intake_keys,
        )

    # 2. Department — explicitly requested, or inferred ONLY when the caller
    #    left the category unspecified (so an explicit procedure/treatment/
    #    speciality request is never silently re-routed).
    if category == "department" or (category is None and normalised in _DEPARTMENT_INDEX):
        return _resolve_department(normalised, category)

    # 3. Unknown procedure — build from keyword expansion
    keywords = _derive_keywords(name)
    resolved_category = category or "procedure"

    # Try to find related intake boolean keys by keyword overlap
    intake_keys = _find_boolean_keys_by_keywords(keywords)

    return ComparisonTarget(
        name=name.title(),
        category=resolved_category,
        keywords=keywords,
        intake_boolean_keys=intake_keys,
    )


def _resolve_department(name: str, category: str | None) -> ComparisonTarget:
    """Resolve a known or unknown department into a comparison target."""
    entry = _DEPARTMENT_INDEX.get(name)

    if entry is not None:
        keywords = list(entry.keywords)
        for syn in entry.synonyms:
            norm_syn = _normalise(syn)
            if norm_syn not in keywords:
                keywords.append(norm_syn)
        if name not in keywords:
            keywords.insert(0, name)
        return ComparisonTarget(
            name=entry.canonical_name,
            category="department",
            keywords=keywords,
            intake_boolean_keys=list(entry.intake_boolean_keys),
        )

    # Unknown department — keep the explicit category (no procedure fallback)
    keywords = _derive_keywords(name)
    intake_keys = _find_boolean_keys_by_keywords(keywords)

    return ComparisonTarget(
        name=name.title(),
        category="department",
        keywords=keywords,
        intake_boolean_keys=intake_keys,
    )


def _find_boolean_keys_by_keywords(keywords: list[str]) -> list[str]:
    """Find intake boolean keys whose feature terms overlap with *keywords*."""
    keyword_set = {kw.lower() for kw in keywords}
    matched: list[str] = []
    for key, feature_terms in _FEATURE_TERMS.items():
        for term in feature_terms:
            # Strip regex escaping for comparison
            plain = re.sub(r"[^\w\s]+", " ", term).replace("\\b", "").strip()
            plain_tokens = set(plain.split())
            if keyword_set & plain_tokens:
                if key not in matched:
                    matched.append(key)
                break
    return matched


# ---------------------------------------------------------------------------
# Category resolution
# ---------------------------------------------------------------------------

# Categories the comparison engine should search for a given target category.
RESOLVED_CATEGORIES: dict[str, tuple[str, ...]] = {
    "procedure": _ALL_RELATED_CATEGORIES,
    "treatment": ("treatments", "procedures", "departments", "specialities",
                  "doctors", "technologies", "facilities"),
    "speciality": ("specialities", "departments", "doctors", "facilities"),
    "department": ("departments", "specialities", "doctors", "facilities"),
}


def related_categories_for(target: ComparisonTarget) -> tuple[str, ...]:
    """Canonical entity categories to search for *target*."""
    return RESOLVED_CATEGORIES.get(target.category, _ALL_RELATED_CATEGORIES)


def availability_categories() -> frozenset[str]:
    """Categories that provide service-level availability evidence."""
    return _AVAILABILITY_CATEGORIES
