"""Deterministic entity matching for comparison.

Scans every entity in a :class:`~hospital_extractor.models.dataset.HospitalDataset`
and identifies which ones are relevant to the comparison target.  Each match
carries a relevance score and an ``availability_evidence`` flag that
distinguishes *service-level* evidence (the hospital offers the procedure)
from a mere text mention.

Matching rules:

- **Exact name match** → relevance 1.0, availability evidence = True
- **Synonym match** → relevance 0.9, availability evidence = True
- **All target tokens present** → relevance 0.7, availability evidence = True
- **Partial token overlap** → relevance 0.5, availability evidence = True
- **Boolean intake fields** → checked against the dataset's evidence trail
- **No match** → excluded entirely (never negative evidence)

``availability_evidence`` is ``True`` for entities in service-level categories
(procedures, treatments, departments, specialities, doctors, technologies,
facilities) because their mere presence as extracted entities means they
were found on service-related pages.  It is ``False`` for entities that only
appear in generic text context.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from hospital_extractor.comparison.models import BooleanDecision, EntityMatch
from hospital_extractor.comparison.target import (
    ComparisonTarget,
    availability_categories,
    related_categories_for,
)
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.intake import IntakeEvidence
from hospital_extractor.schema.boolean_resolver import detectors
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES

if TYPE_CHECKING:
    pass

__all__ = ["match_entities", "match_booleans", "resolve_boolean_decision"]

# Thresholds for relevance scoring.
_EXACT_MATCH = 1.0
_SYNONYM_MATCH = 0.9
_FULL_TOKEN_MATCH = 0.7
_PARTIAL_TOKEN_MATCH = 0.5
_MIN_RELEVANCE = 0.3

# Doctor association states that indicate the doctor is genuinely affiliated.
_CONFIRMED_ASSOCIATIONS = frozenset({"confirmed", "probable"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def match_entities(
    dataset: HospitalDataset,
    target: ComparisonTarget,
    *,
    hospital_id: str = "",
) -> list[EntityMatch]:
    """Find all entities in *dataset* relevant to *target*.

    Returns matches sorted by relevance (highest first), with ties broken
    by entity category then entity name.
    """
    categories_to_search = set(related_categories_for(target))
    target_tokens = _tokenise_keywords(target.keywords)
    availability_cats = availability_categories()
    matches: list[EntityMatch] = []

    for category in CANONICAL_CATEGORIES:
        if category.key not in categories_to_search:
            continue
        records = _get_records(dataset, category.key)
        for record in records:
            match = _match_record(
                record,
                category.key,
                target,
                target_tokens,
                hospital_id=hospital_id,
                is_availability_category=category.key in availability_cats,
            )
            if match is not None:
                matches.append(match)

    # Sort by relevance descending, then category, then name.
    matches.sort(key=lambda m: (-m.relevance_score, m.entity_category, m.entity_name or ""))

    return matches


def match_booleans(
    dataset: HospitalDataset,
    target: ComparisonTarget,
    *,
    hospital_id: str = "",
) -> list[EntityMatch]:
    """Check intake boolean fields related to *target*.

    Each relevant boolean key becomes an ``EntityMatch`` with ``entity_category``
    set to ``"intake_boolean"`` and the decision derived from the dataset's
    existing evidence trail.
    """
    matches: list[EntityMatch] = []
    for key in target.intake_boolean_keys:
        decision, evidence_items = _resolve_boolean(dataset, key)
        if evidence_items:
            # Use the best evidence item for the match
            best = max(evidence_items, key=lambda e: e.strength)
            match = EntityMatch(
                hospital_id=hospital_id,
                entity_category="intake_boolean",
                entity_name=key,
                relevance_score=_EXACT_MATCH,
                availability_evidence=decision == "yes",
                boolean_decision=decision,
                source_url=best.source_url,
                extraction_method=best.method,
                source_strength=None,
                confidence=None,
                context=best.text,
                source_type=_evidence_source_type(best.source_url),
            )
        else:
            match = EntityMatch(
                hospital_id=hospital_id,
                entity_category="intake_boolean",
                entity_name=key,
                relevance_score=0.0,
                availability_evidence=False,
                boolean_decision="unknown",
                source_url="",
                extraction_method=None,
                context="No evidence recorded for this boolean field.",
            )
        matches.append(match)
    return matches


# ---------------------------------------------------------------------------
# Record matching
# ---------------------------------------------------------------------------


def _evidence_source_type(source_url: str) -> str:
    """Intake-boolean evidence carries no per-record source_type, so it is
    treated as originating from the dataset's default (website) source.  A
    richer registry is a later enhancement."""
    return "website"


def _match_record(
    record: object,
    category_key: str,
    target: ComparisonTarget,
    target_tokens: set[str],
    *,
    hospital_id: str,
    is_availability_category: bool,
) -> EntityMatch | None:
    """Try to match a single entity record against the target."""
    name = _get_str(record, "name")
    description = _get_str(record, "description")
    speciality = _get_str(record, "speciality")
    department = _get_str(record, "department")
    category_field = _get_str(record, "category")

    # Build the full text for matching
    texts = [part for part in (name, speciality, department, category_field, description) if part]
    full_text = " ".join(texts)
    if not full_text.strip():
        return None

    # Score relevance
    relevance = _score_relevance(name, texts, target)
    if relevance < _MIN_RELEVANCE:
        return None

    # Determine availability evidence
    availability = _is_availability_evidence(
        category_key, is_availability_category, speciality, department
    )

    source_url = _get_str(record, "source_url") or ""
    extraction_method = _get_str(record, "extraction_method")
    source_strength = _get_str(record, "source_strength")
    confidence = getattr(record, "confidence", None)
    context = _get_str(record, "context")

    return EntityMatch(
        hospital_id=hospital_id,
        entity_category=category_key,
        entity_name=name,
        relevance_score=relevance,
        availability_evidence=availability,
        source_url=source_url,
        extraction_method=extraction_method,
        source_strength=source_strength,
        confidence=confidence if isinstance(confidence, (int, float)) else None,
        context=context,
        source_type=_get_str(record, "source_type") or "website",
    )


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------


def _score_relevance(
    name: str | None,
    texts: list[str],
    target: ComparisonTarget,
) -> float:
    """Deterministic relevance score for an entity against the target.

    Returns 0.0 when no match is found.
    """
    target_keywords = [kw.lower() for kw in target.keywords]
    target_tokens = _tokenise_keywords(target.keywords)

    # 1. Exact name match against the canonical target name
    if name:
        name_lower = name.lower().strip()
        if name_lower == target.name.lower().strip():
            return _EXACT_MATCH
        # Exact match against any keyword
        for kw in target_keywords:
            if name_lower == kw:
                return _EXACT_MATCH

    # 2. Synonym / substring match on name
    if name:
        name_lower = name.lower().strip()
        for kw in target_keywords:
            if len(kw) >= 3 and kw in name_lower:
                return _SYNONYM_MATCH
            if len(name_lower) >= 3 and name_lower in kw:
                return _SYNONYM_MATCH

    # 3. Full token match — all target tokens appear in the entity text
    combined = " ".join(texts).lower()
    entity_tokens = _tokenise_text(combined)
    if target_tokens and target_tokens.issubset(entity_tokens):
        return _FULL_TOKEN_MATCH

    # 4. Partial token overlap
    if target_tokens and entity_tokens:
        overlap = target_tokens & entity_tokens
        if overlap:
            # Score proportional to overlap fraction
            fraction = len(overlap) / len(target_tokens)
            if fraction >= 0.5:
                return _PARTIAL_TOKEN_MATCH

    return 0.0


# ---------------------------------------------------------------------------
# Availability evidence
# ---------------------------------------------------------------------------


def _is_availability_evidence(
    category_key: str,
    is_availability_category: bool,
    speciality: str | None,
    department: str | None,
) -> bool:
    """Decide if this entity provides service-level availability evidence.

    Entities in service-level categories (procedures, treatments,
    departments, specialities) are inherently availability evidence.
    Doctors are availability evidence when their speciality or department
    matches.
    """
    if is_availability_category:
        return True

    # Doctors: availability if their speciality/department is relevant
    if category_key == "doctors":
        return speciality is not None or department is not None

    # Technologies and facilities: availability if they exist
    if category_key in ("technologies", "facilities"):
        return True

    return False


# ---------------------------------------------------------------------------
# Boolean resolution
# ---------------------------------------------------------------------------

# Evidence magnitude below this absolute score resolves to UNKNOWN.  Positive
# record evidence carries strength 1.0; explicit negation carries 10.0 (mirroring
# the rescaling in schema/boolean_resolver.py).  A single weak negative that
# falls short of the threshold must therefore stay UNKNOWN, not NO.
_BOOLEAN_THRESHOLD = 1.0


def resolve_boolean_decision(
    evidence_items: list[IntakeEvidence],
    *,
    threshold: float = _BOOLEAN_THRESHOLD,
) -> BooleanDecision:
    """Resolve a weighted evidence list into a decision.

    Positive evidence adds its strength; negative evidence subtracts it.
    The sign of the resulting score decides YES/NO, but only when its
    magnitude reaches *threshold* — otherwise the decision is UNKNOWN.
    """
    if not evidence_items:
        return "unknown"
    score = sum(
        item.strength if item.decision == "yes" else -item.strength
        for item in evidence_items
    )
    if score >= threshold:
        return "yes"
    if score <= -threshold:
        return "no"
    return "unknown"


def _resolve_boolean(
    dataset: HospitalDataset,
    key: str,
) -> tuple[BooleanDecision, list[IntakeEvidence]]:
    """Resolve a boolean intake key from the dataset's evidence.

    Reuses the existing evidence trail from the hospital's intake projection
    when available; falls back to record-level matching.
    """
    # Check record-level evidence (accreditations, procedures, etc.)
    evidence_items: list[IntakeEvidence] = []
    _collect_boolean_record_evidence(dataset, key, evidence_items)
    _collect_boolean_negative_evidence(dataset, key, evidence_items)

    if not evidence_items:
        return "unknown", []

    return resolve_boolean_decision(evidence_items), evidence_items


# Boolean key → categories + name regex that prove it exists.
# Mirrors extraction/intake.py _BOOLEAN_RECORD_RULES but simplified.
_BOOLEAN_PROOF_RULES: dict[str, tuple[tuple[str, ...], re.Pattern[str]]] = {
    "jci_accredited": (("accreditations",), re.compile(r"\bjci\b|joint commission", re.I)),
    "icu": (("facilities",), re.compile(r"\bicu\b|intensive care\s+unit", re.I)),
    "da_vinci_robotic_surgery": (
        ("technologies", "facilities"),
        re.compile(r"da\s+vinci", re.I),
    ),
    "advanced_laparoscopy_mis": (
        ("technologies", "procedures"),
        re.compile(r"laparoscop|minimal[-\s]?invasive|mis\b", re.I),
    ),
    "mri_3_tesla_or_higher": (
        ("technologies", "facilities"),
        re.compile(r"3(?:\.\d+)?[-\s]?tesla|three[-\s]?tesla", re.I),
    ),
    "cardiac_sciences_enabled": (
        ("specialities", "departments", "procedures", "treatments"),
        re.compile(r"cardiac|cardio|heart", re.I),
    ),
    "bypass_surgery_cabg": (
        ("procedures", "treatments"),
        re.compile(r"bypass|\bcabg\b|coronary artery", re.I),
    ),
    "angioplasty_stenting": (
        ("procedures", "treatments"),
        re.compile(r"angioplasty|stent", re.I),
    ),
    "heart_valve_replacement": (
        ("procedures", "treatments"),
        re.compile(r"valve replacement|\bavr\b|\bmvr\b", re.I),
    ),
    "heart_valve_repair": (
        ("procedures", "treatments"),
        re.compile(r"valve repair", re.I),
    ),
    "pacemaker_icd_implantation": (
        ("procedures", "treatments"),
        re.compile(r"pacemaker|\bicd\b|defibrillator", re.I),
    ),
    "heart_transplant": (
        ("procedures", "treatments"),
        re.compile(r"heart transplant", re.I),
    ),
    "orthopaedics_joint_replacement_enabled": (
        ("specialities", "departments"),
        re.compile(r"orthop|joint replacement|arthroplasty", re.I),
    ),
    "total_knee_replacement": (
        ("procedures", "treatments"),
        re.compile(r"total\s+knee|\btkr\b|knee replacement", re.I),
    ),
    "total_hip_replacement": (
        ("procedures", "treatments"),
        re.compile(r"total\s+hip|\bthr\b|hip replacement", re.I),
    ),
}


def _collect_boolean_record_evidence(
    dataset: HospitalDataset,
    key: str,
    evidence_items: list[IntakeEvidence],
) -> None:
    """Collect record-level evidence for a boolean key."""

    rule = _BOOLEAN_PROOF_RULES.get(key)
    if rule is None:
        return

    categories, pattern = rule
    for cat_key in categories:
        records = _get_records(dataset, cat_key)
        for record in records:
            text = " ".join(
                part
                for part in (
                    _get_str(record, "name"),
                    _get_str(record, "category"),
                    _get_str(record, "description"),
                )
                if part
            )
            if pattern.search(text):
                source = _get_str(record, "source_url") or ""
                evidence_items.append(
                    IntakeEvidence.yes(
                        key,
                        text=text[:300],
                        source_url=source,
                        method="record",
                        strength=1.0,
                        label=key,
                    )
                )
                break  # One proof per category is enough

    # Infrastructure bed counts as proof of ICU
    if key == "icu":
        for record in _get_records(dataset, "infrastructure"):
            for field in ("icu_beds", "critical_care_beds"):
                value = getattr(record, field, None)
                if isinstance(value, int) and value > 0:
                    source = _get_str(record, "source_url") or ""
                    evidence_items.append(
                        IntakeEvidence.yes(
                            key,
                            text=f"{field}: {value}",
                            source_url=source,
                            method="record",
                            strength=1.0,
                            label=key,
                        )
                    )
                    break


# Plain-language negation for record-level scans.  Mirrors the 10x rescaling
# used by schema/boolean_resolver.negate() while also covering "not": e.g.
# "total knee replacement is not available" must read as explicit denial.
# "non-*" prefixes are deliberately excluded: in medical text "non-X" is a
# subtype descriptor (non-cardiac, non-invasive), never a service denial.
_RECORD_NEGATION_PREFIX = r"\b(?:no|not|without|never)\b"
_RECORD_NEGATION_VERB = r"\b(?:does|do|did)\s+not\s+(?:offer|provide|perform)\b"
_RECORD_NOT_AVAILABLE = (
    r"\bnot\b[^\w]{0,12}(?:available|offered|provided|performed|done|equipped)"
)
_RECORD_NO_LONGER = r"\b(?:no|not)\s+longer\b"


def _build_negative_record_patterns() -> dict[str, tuple[tuple[str, float], ...]]:
    """Derive per-key negation patterns from the resolver's own term corpus.

    Every term that the extractor recognises positively is paired with the
    local negation wrappers, so the comparison layer reasons about exactly
    the same vocabulary as intake projection.
    """
    result: dict[str, list[tuple[str, float]]] = {}
    for detector in detectors():
        for term, _weight in detector.yes_patterns:
            # Suffix denial: "<term>[gap][is/are/was/were] not available..."
            suffix_not_available = (
                rf"{term}\b[^\w]{{0,40}}\b(?:is|are|was|were)?\b[^\w]{{0,3}}"
                rf"\bnot\b[^\w]{{0,12}}"
                rf"(?:available|offered|provided|performed|done|equipped)"
            )
            patterns = (
                (rf"{_RECORD_NEGATION_PREFIX}[^\w]{{0,4}}{term}", 10.0),
                (rf"{_RECORD_NOT_AVAILABLE}[^\w]{{0,6}}{term}", 10.0),
                (suffix_not_available, 10.0),
                (rf"{term}[^\w]{{1,6}}{_RECORD_NO_LONGER}", 10.0),
                (rf"{_RECORD_NEGATION_VERB}[^\w]{{0,6}}{term}", 10.0),
            )
            result.setdefault(detector.name, []).extend(patterns)
    return {key: tuple(patterns) for key, patterns in result.items()}


_NEGATIVE_RECORD_PATTERNS = _build_negative_record_patterns()


def _collect_boolean_negative_evidence(
    dataset: HospitalDataset,
    key: str,
    evidence_items: list[IntakeEvidence],
) -> None:
    """Collect explicit-denial evidence for a boolean key.

    A record statement such as "total knee replacement is not available"
    becomes a negative evidence item.  Absence of any statement is NOT
    treated as negative — UNKNOWN stays the answer for missing evidence.
    """
    patterns = _NEGATIVE_RECORD_PATTERNS.get(key)
    if not patterns:
        return

    for cat_key in ("procedures", "treatments", "specialities", "departments"):
        for record in _get_records(dataset, cat_key):
            text = " ".join(
                part
                for part in (
                    _get_str(record, "name"),
                    _get_str(record, "category"),
                    _get_str(record, "description"),
                )
                if part
            )
            if not text:
                continue
            window = text[:400].lower()
            if any(re.search(pattern, window, re.IGNORECASE) for pattern, _w in patterns):
                source = _get_str(record, "source_url") or ""
                evidence_items.append(
                    IntakeEvidence.no(
                        key,
                        text=text[:300],
                        source_url=source,
                        method="record",
                        strength=10.0,
                        label=key,
                    )
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_records(dataset: HospitalDataset, category_key: str) -> list[object]:
    """Get entity records for a canonical category."""
    if category_key == "hospital":
        return [dataset.hospital] if dataset.hospital is not None else []
    records = getattr(dataset, category_key, [])
    return list(records) if records else []


def _get_str(record: object, attr: str) -> str | None:
    value = getattr(record, attr, None)
    return value if isinstance(value, str) and value else None


def _tokenise_keywords(keywords: list[str]) -> set[str]:
    """Tokenise a list of keyword phrases into a set of significant tokens."""
    tokens: set[str] = set()
    for kw in keywords:
        tokens.update(_tokenise_text(kw))
    return tokens


def _tokenise_text(text: str) -> set[str]:
    """Tokenise text into a set of lowercase alpha tokens (3+ chars)."""
    return {
        token
        for token in re.findall(r"[a-z]{3,}", text.lower())
        if token not in _STOPWORDS
    }


_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "are", "was",
    "not", "but", "has", "had", "have", "can", "may", "will", "all",
    "any", "our", "its", "who", "how", "what", "when", "where", "why",
    "also", "than", "them", "then", "each", "make", "like", "over",
    "such", "been", "much", "some", "very", "into", "just", "your",
})
