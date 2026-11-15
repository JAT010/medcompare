"""Deterministic discovery prompt interpretation (P1-A, no LLM, no geocoding).

Parsing is intentionally small, explicit, and testable. It recognizes only
clearly supported patterns/vocabulary and never invents locations, radii,
services, facilities, or availability. Unknown/ambiguous/conflicting intent is
surfaced as unsupported requirements, ambiguities, or clarification questions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .schemas import (
    DISCOVERY_CONTRACT_VERSION,
    DISCOVERY_INTERPRETER,
    AmbiguityItem,
    BoundaryTextGeo,
    ClarificationItem,
    ConstraintItem,
    ConstraintKind,
    DiscoveryFilters,
    DiscoveryInterpretRequest,
    DiscoveryInterpretResponse,
    ExtractedTerm,
    InformationPresence,
    NegativeRequirement,
    PreferenceItem,
    RadiusGeo,
    UnsupportedRequirement,
    normalize_prompt_text,
)
from .vocabulary import (
    EXCLUSION_CUES,
    HOSPITAL_CATEGORIES,
    PREFERENCE_CUES,
    RADIUS_UNITS,
    REQUIRED_CUES,
    VOCABULARY_VERSION,
    _normalize_token,
)

_SEGMENT_SPLIT_RE = re.compile(
    r"\s*(?:[;,]|\band\b|\bwith\b|\bmust have\b|\bmust include\b"
    r"|\brequired\b|\bprefer\b|\bpreferably\b|\bideally\b"
    r"|\bwithout\b|\bexcluding\b|\bexclude\b)\s*",
    re.IGNORECASE,
)
_RADIUS_RE = re.compile(
    r"(?P<prep>within|in|around|near)\s+(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>km|kms|kilometer|kilometers|kilometre|kilometres|mi|mile|miles|m|meter|meters"
    r"|metre|metres)\b"
    r"(?:\s+(?:of|from|around|near)\s+(?P<center>[^,.;]+))?",
    re.IGNORECASE,
)
_LOCATION_AFTER_PREP_RE = re.compile(
    r"\b(?:in|near|around)\s+"
    r"(?P<loc>[A-Za-z0-9][A-Za-z0-9 .,'/\-]{1,80}?)"
    r"(?=\s*(?:,|;|\.|\bwith\b|\band\b|\bthat\b|\bwhich\b|\bhaving\b|$))",
    re.IGNORECASE,
)

# Mapping of canonical term -> (kind, source_kind_field)
_TERM_SOURCES: list[tuple[str, str, dict[str, tuple[str, ...]]]] = [
    ("hospital_category", "hospital_categories", HOSPITAL_CATEGORIES),
]

# We import the rest of vocab mappings lazily to keep ordering deterministic.
from .vocabulary import DEPARTMENTS, FACILITIES, SPECIALTIES  # noqa: E402

_TERM_SOURCES.extend(
    [
        ("specialty", "specialties", SPECIALTIES),
        ("department", "departments", DEPARTMENTS),
        ("facility", "required_facilities", FACILITIES),
    ]
)

_RECOGNIZED_FORM_SET = {
    form.lower()
    for _, _, mapping in _TERM_SOURCES
    for canonical, forms in mapping.items()
    for form in (*forms, canonical)
}


def _is_recognized_term(text: str) -> bool:
    normalized = _normalize_token(text)
    lowered = text.lower().strip()
    return normalized in _RECOGNIZED_FORM_SET or lowered in _RECOGNIZED_FORM_SET


@dataclass(frozen=True)
class _Match:
    canonical: str
    kind: str
    field: str
    source_text: str
    span: tuple[int, int]


def interpret_discovery_prompt(request: DiscoveryInterpretRequest) -> DiscoveryInterpretResponse:
    original_prompt = request.prompt
    normalized_prompt = normalize_prompt_text(original_prompt)
    normalized_lc = normalized_prompt.lower()

    segments = _split_segments(normalized_prompt)
    vocab_matches = _extract_vocabulary_matches(normalized_prompt)
    location_texts, geo, location_ambiguities, location_clarifications = _extract_location_and_geo(
        normalized_prompt, normalized_lc
    )

    explicit_exclusions, negative_requirements = _extract_negatives_and_exclusions(
        normalized_prompt, segments, vocab_matches
    )
    constraints, preferences = _extract_constraints_and_preferences(
        normalized_prompt, segments, vocab_matches, explicit_exclusions
    )
    unsupported = _extract_unsupported_requirements(segments, vocab_matches)

    ambiguities: list[AmbiguityItem] = list(location_ambiguities)
    clarifications: list[ClarificationItem] = list(location_clarifications)

    # Location presence semantics (never converted to negative requirement).
    if len(location_texts) > 1:
        location_text = sorted(location_texts, key=str.lower)[0]
        if not any(a.code == "conflicting_locations" for a in ambiguities):
            ambiguities.append(
                AmbiguityItem(
                    code="conflicting_locations",
                    message=(
                        "Multiple conflicting locations were detected; "
                        "clarification is required."
                    ),
                    source_text=normalized_prompt,
                )
            )
        if not any(c.code == "clarify_location" for c in clarifications):
            clarifications.append(
                ClarificationItem(
                    code="clarify_location",
                    message="Please specify a single target location.",
                    field="location_text",
                )
            )
    elif len(location_texts) == 1:
        location_text = location_texts[0]
    else:
        location_text = None

    missing_information: list[str] = []
    if location_text is None:
        missing_information.append("location")
    # Single unambiguous location is not missing; conflicting locations are ambiguous.

    filters = DiscoveryFilters(
        location_text=location_text,
        location_presence=(
            InformationPresence.AMBIGUOUS
            if len(location_texts) > 1
            or (
                location_text
                and any(
                    a.code.startswith("conflicting") or a.code == "ambiguous_location"
                    for a in ambiguities
                )
            )
            else InformationPresence.MISSING
        ),
        geo=geo,
        hospital_categories=_terms_for(vocab_matches, "hospital_category"),
        specialties=_terms_for(vocab_matches, "specialty"),
        departments=_terms_for(vocab_matches, "department"),
        required_facilities=_terms_for(vocab_matches, "facility"),
        explicit_exclusions=_dedupe_negatives(explicit_exclusions),
        constraints=_dedupe_constraints(constraints),
        preferences=_dedupe_preferences(preferences),
        negative_requirements=_dedupe_negatives(negative_requirements),
        missing_information=sorted(set(missing_information)),
    )

    notes = [
        "Deterministic interpretation only (no LLM, no geocoding, no external taxonomy).",
        f"Vocabulary: {VOCABULARY_VERSION}. Only explicitly recognized terms are extracted.",
        (
            "Requested facilities/services are interpreted as user "
            "constraints/preferences, not discovered evidence or availability."
        ),
    ]
    if unsupported:
        notes.append(
            "Unknown terms were preserved as unsupported requirements and not auto-mapped."
        )

    return DiscoveryInterpretResponse(
        original_prompt=original_prompt,
        normalized_prompt=normalized_prompt,
        filters=filters,
        clarification_questions=_dedupe_clarifications(clarifications),
        unsupported_requirements=_dedupe_unsupported(unsupported),
        ambiguities=_dedupe_ambiguities(ambiguities),
        interpreter=DISCOVERY_INTERPRETER,
        contract_version=DISCOVERY_CONTRACT_VERSION,
        interpretation_notes=notes,
    )


def _split_segments(text: str) -> list[str]:
    parts = [p.strip(" .") for p in _SEGMENT_SPLIT_RE.split(text)]
    return [p for p in parts if p]


def _extract_vocabulary_matches(text: str) -> list[_Match]:
    matches: list[_Match] = []
    lower = text.lower()
    for kind, field, mapping in _TERM_SOURCES:
        # Match longer forms first to prefer specific phrases.
        for canonical in sorted(mapping.keys()):
            forms = sorted(set(mapping[canonical]) | {canonical}, key=lambda s: (-len(s), s))
            for form in forms:
                pattern = re.compile(rf"\b{re.escape(form.lower())}\b")
                for found in pattern.finditer(lower):
                    src = text[found.start() : found.end()]
                    matches.append(
                        _Match(
                            canonical,
                            kind,
                            field,
                            src,
                            (found.start(), found.end()),
                        )
                    )
    # Deterministic dedupe: by (field, canonical), keep earliest source_text occurrence.
    by_key: dict[tuple[str, str], _Match] = {}
    for match in sorted(matches, key=lambda x: (x.field, x.canonical, x.span[0])):
        key = (match.field, match.canonical)
        if key not in by_key:
            by_key[key] = match
    return sorted(by_key.values(), key=lambda x: (x.field, x.canonical))


def _terms_for(matches: list[_Match], kind: str) -> list[ExtractedTerm]:
    items = [
        ExtractedTerm(value=m.canonical, kind=kind, source_text=m.source_text)
        for m in matches
        if m.kind == kind
    ]
    return sorted(items, key=lambda i: (i.kind, i.value))


def _extract_location_and_geo(
    text: str, lower: str
) -> tuple[
    list[str],
    RadiusGeo | BoundaryTextGeo | None,
    list[AmbiguityItem],
    list[ClarificationItem],
]:
    ambiguities: list[AmbiguityItem] = []
    clarifications: list[ClarificationItem] = []
    geo: RadiusGeo | BoundaryTextGeo | None = None
    locations: list[str] = []

    radius_matches = list(_RADIUS_RE.finditer(text))
    if radius_matches:
        rm = radius_matches[0]
        value = float(rm.group("value"))
        unit_raw = rm.group("unit").lower()
        unit = RADIUS_UNITS.get(unit_raw, unit_raw)
        radius_km = _to_km(value, unit)
        center = (rm.group("center") or "").strip(" .")
        if not center:
            # Try to infer center from nearby "in/near/around <loc>" not part of radius number.
            center = _first_location_phrase(text) or ""
        if center:
            locations.append(_clean_location(center))
        geo = RadiusGeo(
            type="radius",
            center_text=_clean_location(center) if center else "unspecified",
            radius_km=radius_km,
            original_value=value,
            original_unit=unit,
            unit_explicit=True,
        )
        if not center:
            ambiguities.append(
                AmbiguityItem(
                    code="ambiguous_radius_center",
                    message="A radius was specified but its center/location is ambiguous.",
                    source_text=rm.group(0),
                )
            )
            clarifications.append(
                ClarificationItem(
                    code="clarify_radius_center",
                    message="Please specify the location/center for the stated radius.",
                    field="geo.center_text",
                )
            )
        if len(radius_matches) > 1:
            ambiguities.append(
                AmbiguityItem(
                    code="conflicting_radius",
                    message=(
                        "Multiple radius specifications were detected; "
                        "clarification is required."
                    ),
                    source_text=text,
                )
            )
            clarifications.append(
                ClarificationItem(
                    code="clarify_radius",
                    message="Please specify a single radius constraint.",
                    field="geo",
                )
            )

    # Explicit location phrases (deterministic, conservative).
    for m in _LOCATION_AFTER_PREP_RE.finditer(text):
        loc = _clean_location(m.group("loc"))
        if not loc:
            continue
        # Avoid capturing pure numeric/unit fragments.
        if re.fullmatch(
            r"\d+(?:\.\d+)?\s*(?:km|kms|mi|mile|miles|m|meters|metres)?",
            loc,
            re.IGNORECASE,
        ):
            continue
        # Avoid treating recognized vocabulary terms as locations (e.g. "in ICU").
        if _is_recognized_term(loc):
            continue
        locations.append(loc)

    # Deduplicate while preserving order by normalized value.
    deduped: list[str] = []
    seen: set[str] = set()
    for loc in locations:
        key = _normalize_token(loc)
        if key and key not in seen:
            seen.add(key)
            deduped.append(loc)
    # Conflicting locations: more than one distinct location string.
    if len({_normalize_token(loc_value) for loc_value in deduped}) > 1:
        # keep all for detection; caller marks ambiguous
        pass
    return deduped, geo, ambiguities, clarifications


def _first_location_phrase(text: str) -> str | None:
    m = _LOCATION_AFTER_PREP_RE.search(text)
    if not m:
        return None
    return _clean_location(m.group("loc"))


def _clean_location(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" ,.;:-")
    # Trim trailing generic words
    cleaned = re.sub(r"\b(area|region)$", "", cleaned, flags=re.IGNORECASE).strip(" ,.;:-")
    return cleaned[:120]


def _to_km(value: float, unit: str) -> float:
    if unit == "km":
        return float(value)
    if unit == "mi":
        return float(value) * 1.60934
    if unit == "m":
        return float(value) / 1000.0
    return float(value)


def _extract_negatives_and_exclusions(
    text: str, segments: list[str], matches: list[_Match]
) -> tuple[list[NegativeRequirement], list[NegativeRequirement]]:
    exclusions: list[NegativeRequirement] = []
    negatives: list[NegativeRequirement] = []
    lower = text.lower()
    for cue in EXCLUSION_CUES:
        idx = lower.find(cue)
        while idx != -1:
            # capture a short window after cue as source context
            window = text[idx : idx + 120].strip()
            # Try to associate with a recognized term appearing after cue.
            associated = None
            for m in sorted(matches, key=lambda x: x.span[0]):
                if m.span[0] >= idx:
                    associated = m
                    break
            if associated:
                item = NegativeRequirement(
                    field=associated.kind,
                    value=associated.canonical,
                    source_text=_snippet(window),
                )
                exclusions.append(item)
                negatives.append(item)
            idx = lower.find(cue, idx + len(cue))
    return exclusions, negatives


def _extract_constraints_and_preferences(
    text: str, segments: list[str], matches: list[_Match], exclusions: list[NegativeRequirement]
) -> tuple[list[ConstraintItem], list[PreferenceItem]]:
    constraints: list[ConstraintItem] = []
    preferences: list[PreferenceItem] = []
    excluded_keys = {(e.field, e.value) for e in exclusions}
    for m in matches:
        if (m.kind, m.canonical) in excluded_keys:
            continue
        kind = _intent_for_match(text, m)
        if kind == ConstraintKind.REQUIRED:
            constraints.append(
                ConstraintItem(
                    key=m.kind,
                    value=m.canonical,
                    kind=ConstraintKind.REQUIRED,
                    source_text=m.source_text,
                )
            )
        elif kind == ConstraintKind.PREFERENCE:
            preferences.append(
                PreferenceItem(key=m.kind, value=m.canonical, source_text=m.source_text)
            )
        else:
            # No explicit supported intent cue -> ambiguous intent; do not invent
            # requirement/preference.
            # Represented later as clarification/ambiguity at segment level if needed.
            pass
    return constraints, preferences


def _intent_for_match(text: str, match: _Match) -> ConstraintKind | None:
    # Inspect a local window before the term for explicit cues.
    start = max(0, match.span[0] - 60)
    window = text[start : match.span[0]].lower()
    if any(cue in window for cue in PREFERENCE_CUES):
        return ConstraintKind.PREFERENCE
    if any(cue in window for cue in REQUIRED_CUES):
        return ConstraintKind.REQUIRED
    # Also check for simple "must" / "need" tokens
    if re.search(r"\b(must|need|required)\b", window):
        return ConstraintKind.REQUIRED
    if re.search(r"\b(prefer|ideally|preferably)\b", window):
        return ConstraintKind.PREFERENCE
    return None


def _extract_unsupported_requirements(
    segments: list[str], matches: list[_Match]
) -> list[UnsupportedRequirement]:
    unsupported: list[UnsupportedRequirement] = []
    # If a segment has no recognized vocab match and is not purely a location/radius/control
    # phrase, preserve it as unsupported (conservative). We avoid overfitting by targeting
    # noun-like segments.
    control_re = re.compile(
        r"^(find|show|list|looking for|search|hospitals?|hospital|near|in|around|within"
        r"|please|me|a|an|the)\b",
        re.IGNORECASE,
    )
    for seg in segments:
        if not seg or len(seg) < 3:
            continue
        if control_re.match(seg.strip().lower()) and not any(ch.isdigit() for ch in seg):
            # may still contain meaningful unsupported ask; check for recognized terms inside
            pass
        seg_lower = seg.lower()
        has_match = any(
            _normalize_token(m.source_text) in seg_lower
            or m.canonical.replace("_", " ") in seg_lower
            for m in matches
        )
        has_location = bool(_LOCATION_AFTER_PREP_RE.search(seg) or _RADIUS_RE.search(seg))
        if has_match or has_location:
            continue
        # Heuristic: treat as unsupported requirement text (preserve original wording)
        # unless it's generic.
        if re.fullmatch(r"[a-z\s]{0,20}", seg_lower) and seg_lower.strip() in {
            "hospitals",
            "hospital",
            "find hospitals",
        }:
            continue
        unsupported.append(
            UnsupportedRequirement(
                original_text=seg.strip(),
                reason=(
                    "Term or requirement is not in the supported discovery vocabulary; "
                    "preserved without mapping."
                ),
                kind="unrecognized_term",
            )
        )
    return _dedupe_unsupported(unsupported)


def _snippet(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:300]


def _dedupe_clarifications(items: list[ClarificationItem]) -> list[ClarificationItem]:
    seen: set[tuple[str, str | None]] = set()
    out: list[ClarificationItem] = []
    for i in sorted(items, key=lambda x: (x.code, x.field or "", x.message)):
        key = (i.code, i.field)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def _dedupe_ambiguities(items: list[AmbiguityItem]) -> list[AmbiguityItem]:
    seen: set[tuple[str, str]] = set()
    out: list[AmbiguityItem] = []
    for i in sorted(items, key=lambda x: (x.code, x.message)):
        key = (i.code, i.message)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def _dedupe_unsupported(items: list[UnsupportedRequirement]) -> list[UnsupportedRequirement]:
    seen: set[str] = set()
    out: list[UnsupportedRequirement] = []
    for i in sorted(items, key=lambda x: x.original_text.lower()):
        key = _normalize_token(i.original_text)
        if key and key not in seen:
            seen.add(key)
            out.append(i)
    return out


def _dedupe_negatives(items: list[NegativeRequirement]) -> list[NegativeRequirement]:
    seen: set[tuple[str, str, str]] = set()
    out: list[NegativeRequirement] = []
    for i in sorted(items, key=lambda x: (x.field, x.value, x.source_text.lower())):
        key = (i.field, i.value, _normalize_token(i.source_text))
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def _dedupe_constraints(items: list[ConstraintItem]) -> list[ConstraintItem]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ConstraintItem] = []
    for i in sorted(items, key=lambda x: (x.kind.value, x.key, x.value)):
        key = (i.kind.value, i.key, i.value)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def _dedupe_preferences(items: list[PreferenceItem]) -> list[PreferenceItem]:
    seen: set[tuple[str, str]] = set()
    out: list[PreferenceItem] = []
    for i in sorted(items, key=lambda x: (x.key, x.value)):
        key = (i.key, i.value)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out
