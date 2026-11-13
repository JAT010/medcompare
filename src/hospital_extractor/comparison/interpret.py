"""Deterministic interpretation of a user's natural-language comparison request.

The user describes what they want compared in their own words; this module
turns that description into a list of individually comparable criteria, each
with a statement of what evidence would be needed to evaluate it.

How it works (and what it deliberately does not do):

- The command framing ("please compare ...") is stripped, the remainder is split
  into clauses on commas, semicolons and conjunctions, and each clause is
  normalised into a criterion label while the verbatim wording is preserved.
- Criteria whose wording is genuinely ambiguous ("good", "overall", "everything")
  produce a clarification question instead of a silently invented interpretation.
- Each criterion is matched against the standard criterion vocabulary so a
  custom request that duplicates one of the twelve standards is flagged rather
  than silently double-counted.
- No language model call is made. There is no AI integration in this codebase,
  so interpretation is transparent, deterministic parsing; the response says so
  explicitly. Nothing about hospitals, ratings or facilities is ever generated
  here — only the shape of the question the user asked.
"""

from __future__ import annotations

import re

from hospital_extractor.comparison.schemas import (
    INTERPRETER_DETERMINISTIC,
    CriterionDefinition,
    InterpretResponse,
)
from hospital_extractor.recommendation.criteria import CRITERIA, CriterionSpec

__all__ = ["interpret_prompt"]

_INTERPRETATION_NOTE = (
    "Criteria were derived by deterministic parsing of your wording (command "
    "stripping, clause splitting and normalisation). No language model is "
    "configured in this deployment, so open-ended rephrasing is not performed. "
    "No hospital information, rating or conclusion was generated."
)

# Framing words removed from the front of the prompt, repeatedly, until none match.
_COMMAND_PREFIXES = (
    "please ",
    "kindly ",
    "can you ",
    "could you ",
    "would you ",
    "i want to compare ",
    "i'd like to compare ",
    "i would like to compare ",
    "i want to ",
    "i'd like to ",
    "i would like to ",
    "i need to ",
    "i want ",
    "i need ",
    "i am looking for ",
    "im looking for ",
    "let's ",
    "lets ",
    "also compare ",
    "compare ",
    "check ",
    "evaluate ",
    "assess ",
    "review ",
    "analyse ",
    "analyze ",
    "look at ",
    "consider ",
    "include ",
    "tell me about ",
    "show me ",
    "find out about ",
    "give me ",
    "list ",
)

_CLAUSE_SEPARATOR = re.compile(
    r"\s*(?:,|;|\band\b|\bplus\b|\bas well as\b|\balong with\b|\bbesides\b|"
    r"\bbetween\b|\bvs\.?\b|\bversus\b)\s*",
    re.IGNORECASE,
)

# Leading filler removed from each clause, repeatedly, until none match.
_CLAUSE_PREFIXES = (
    "whether ",
    "if they have ",
    "if there is ",
    "if there are ",
    "if it has ",
    "if ",
    "do they have ",
    "does it have ",
    "are there ",
    "is there ",
    "the availability of ",
    "availability of ",
    "having ",
    "the ",
    "their ",
    "its ",
    "our ",
    "about ",
    "on ",
    "with ",
    "specifically ",
    "things like ",
    "such as ",
    "what about ",
    "how about ",
    "also ",
    "and ",
    "plus ",
    "i.e. ",
    "e.g. ",
)

_TRAILING_PUNCTUATION = ".,;:!?"

# Wording that does not identify anything checkable against a source.
_AMBIGUOUS_TERMS = (
    "good",
    "best",
    "great",
    "nice",
    "excellent",
    "high quality",
    "modern",
    "state of the art",
    "world class",
    "top class",
    "overall",
    "everything",
    "anything",
    "etc",
    "and so on",
)

# Keywords that tie a custom criterion to one of the twelve standard criteria.
_STANDARD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "affordability": (
        "affordab",
        "price",
        "pricing",
        "cost",
        "costs",
        "fee",
        "fees",
        "charge",
        "charges",
        "tariff",
        "budget",
        "expensive",
        "cheap",
    ),
    "location": (
        "location",
        "located",
        "address",
        "distance",
        "nearby",
        "locality",
        "pin code",
        "pincode",
        "directions",
        "how far",
    ),
    "doctor_experience": (
        "doctor experience",
        "years of experience",
        "senior doctor",
        "qualifications",
        "well experienced",
    ),
    "department_expertise": ("department", "speciality", "specialty", "specialist", "expertise"),
    "infrastructure": (
        "infrastructure",
        "total beds",
        "icu beds",
        "operation theatre",
        "operating theatre",
    ),
    "emergency_services": (
        "emergency",
        "casualty",
        "ambulance",
        "trauma",
        "24x7",
        "24 hours",
    ),
    "doctor_availability": (
        "doctor availability",
        "opd hours",
        "appointment",
        "appointments",
        "consultation hours",
        "visiting hours of doctors",
    ),
    "patient_reviews": (
        "review",
        "reviews",
        "rating",
        "ratings",
        "patient feedback",
        "testimonials",
    ),
    "diagnostic_facilities": (
        "diagnostic",
        "diagnostics",
        "laboratory",
        "pathology",
        "radiology",
        "mri",
        "ct scan",
        "x-ray",
        "blood test",
    ),
    "insurance_compatibility": (
        "insurance",
        "insurer",
        "cashless",
        "tpa",
        "network hospital",
        "policy",
    ),
    "accessibility": (
        "accessibility",
        "accessible",
        "wheelchair",
        "ramp",
        "elevator",
        "lift",
        "disabled access",
        "differently abled",
    ),
    "treatment_facilities": (
        "treatment",
        "procedure",
        "surgery",
        "surgeries",
        "therap",
    ),
}

_STANDARD_BY_KEY: dict[str, CriterionSpec] = {
    criterion.key: criterion for criterion in CRITERIA
}


def interpret_prompt(prompt: str) -> InterpretResponse:
    """Turn *prompt* into comparable criteria plus any clarification questions."""
    text = _strip_command(prompt.strip())
    raw_clauses = _split_clauses(text)
    clauses = [_clean_clause(clause) for clause in raw_clauses]
    pairs = [
        (original, cleaned)
        for original, cleaned in zip(raw_clauses, clauses, strict=True)
        if cleaned
    ]

    criteria: list[CriterionDefinition] = []
    questions: list[str] = []
    seen: set[str] = set()
    for original, cleaned in pairs:
        normalised = cleaned.casefold()
        if normalised in seen:
            continue
        seen.add(normalised)
        ambiguous = _ambiguous_terms(cleaned)
        if ambiguous:
            questions.append(_clarification_question(cleaned, ambiguous[0]))
            continue
        criteria.append(_criterion(cleaned, original))

    if not criteria and not questions:
        questions.append(
            "Please describe at least one specific thing to compare, for example: "
            "parking, private rooms, visiting hours or wheelchair assistance."
        )

    return InterpretResponse(
        criteria=criteria,
        clarification_questions=questions,
        interpreter=INTERPRETER_DETERMINISTIC,
        interpretation_note=_INTERPRETATION_NOTE,
    )


def _strip_command(text: str) -> str:
    remaining = text
    for _ in range(8):
        lowered = remaining.casefold()
        for prefix in _COMMAND_PREFIXES:
            if lowered.startswith(prefix):
                remaining = remaining[len(prefix) :]
                break
        else:
            break
    return remaining.strip()


def _split_clauses(text: str) -> list[str]:
    parts = [part.strip() for part in _CLAUSE_SEPARATOR.split(text)]
    return [part for part in parts if part]


def _clean_clause(clause: str) -> str:
    working = " ".join(clause.split())
    for _ in range(6):
        lowered = working.casefold()
        for prefix in _CLAUSE_PREFIXES:
            if lowered.startswith(prefix):
                working = working[len(prefix) :].strip()
                break
        else:
            break
    working = working.strip(_TRAILING_PUNCTUATION + " \"'")
    return " ".join(working.split())


def _ambiguous_terms(clause: str) -> list[str]:
    lowered = clause.casefold()
    found: list[str] = []
    for term in _AMBIGUOUS_TERMS:
        if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", lowered):
            found.append(term)
    return found


def _clarification_question(clause: str, term: str) -> str:
    return (
        f'What specifically do you mean by "{term}" when asking about "{clause}"? '
        "Name an observable detail that a source could confirm or deny."
    )


def _criterion(label: str, original_text: str) -> CriterionDefinition:
    overlap = _standard_overlap(label)
    spec = _STANDARD_BY_KEY.get(overlap) if overlap else None
    return CriterionDefinition(
        key=_slug(label),
        label=_title_case(label),
        origin="custom",
        evidence_needed=_evidence_needed(label),
        matched_source=", ".join(spec.source_fields) if spec and spec.source_fields else None,
        overlaps_standard=overlap,
        original_text=original_text,
    )


def _standard_overlap(label: str) -> str | None:
    lowered = label.casefold()
    for key, keywords in _STANDARD_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return key
    return None


def _evidence_needed(label: str) -> str:
    return (
        f'A citable source stating "{label}" for this exact facility (branch), '
        "with a link and a retrieval or publication date. If no source states it, "
        "the criterion stays unavailable."
    )


def _slug(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
    return (slug[:60].rstrip("_") or "criterion")


def _title_case(label: str) -> str:
    words = label.split()
    if not words:
        return label
    small = {"and", "or", "of", "the", "a", "an", "in", "for", "to", "with", "at", "on"}
    styled: list[str] = []
    for index, word in enumerate(words):
        lowered = word.casefold()
        if index and lowered in small:
            styled.append(lowered)
        elif lowered.isupper() and len(lowered) > 1:
            styled.append(word)
        else:
            styled.append(word[:1].upper() + word[1:])
    return " ".join(styled)


def standard_criterion_definition(key: str) -> CriterionDefinition:
    """Return the :class:`CriterionDefinition` for one of the twelve standards."""
    spec = _STANDARD_BY_KEY[key]
    if spec.required_extraction:
        evidence = "Evidence required: " + "; ".join(spec.required_extraction)
    else:
        evidence = spec.rationale
    return CriterionDefinition(
        key=spec.key,
        label=spec.label,
        origin="standard",
        evidence_needed=evidence,
        matched_source=", ".join(spec.source_fields) if spec.source_fields else None,
        overlaps_standard=None,
        original_text=None,
    )


def standard_criteria(keys: tuple[str, ...] | None = None) -> list[CriterionDefinition]:
    """The twelve standard criteria, in registry order, or a subset of them."""
    selected = keys or tuple(criterion.key for criterion in CRITERIA)
    return [standard_criterion_definition(key) for key in selected]
