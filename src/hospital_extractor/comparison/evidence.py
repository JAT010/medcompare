"""Turning stored and extracted hospital data into attributable evidence.

Two producers exist:

- :func:`registry_evidence` reads the MedCompare registry (the only thing the
  server holds for a registered facility today).
- :func:`evidence_from_intake` converts a completed website extraction into
  evidence, keeping the page each finding came from.

Both obey the same rule: only statements that were actually found become
evidence. An absent statement produces no item at all, so a hospital is never
recorded as lacking something merely because the server has no data for it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime

from hospital_extractor.comparison.schemas import EvidenceItem
from hospital_extractor.models.intake import MasterIntake
from hospital_extractor.persistence.models import HospitalFacility
from hospital_extractor.schema.intake import intake_field

__all__ = [
    "REGISTRY_SOURCE_NAME",
    "criterion_evidence",
    "evidence_from_intake",
    "registry_evidence",
]

REGISTRY_SOURCE_NAME = "MedCompare registry"
CRAWL_SOURCE_NAME = "Hospital website (crawled)"

# Boolean intake fields grouped by the standard criterion they can answer.
_BOOLEAN_CRITERIA: dict[str, str] = {
    "icu": "infrastructure",
    "mri_3_tesla_or_higher": "diagnostic_facilities",
    "cardiac_sciences_enabled": "department_expertise",
    "orthopaedics_joint_replacement_enabled": "department_expertise",
    "da_vinci_robotic_surgery": "treatment_facilities",
    "advanced_laparoscopy_mis": "treatment_facilities",
    "bypass_surgery_cabg": "treatment_facilities",
    "angioplasty_stenting": "treatment_facilities",
    "heart_valve_replacement": "treatment_facilities",
    "heart_valve_repair": "treatment_facilities",
    "pacemaker_icd_implantation": "treatment_facilities",
    "heart_transplant": "treatment_facilities",
    "total_knee_replacement": "treatment_facilities",
    "total_hip_replacement": "treatment_facilities",
}

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "their",
        "there",
        "this",
        "that",
        "these",
        "those",
        "its",
        "our",
        "your",
        "about",
        "from",
        "into",
        "have",
        "has",
        "does",
        "avail",
        "availability",
        "available",
        "options",
        "option",
        "services",
        "service",
        "facility",
        "facilities",
        "information",
        "details",
        "provide",
        "offered",
        "offering",
        "presence",
    }
)


def _join_parts(parts: Iterable[str | None]) -> str:
    """Join non-empty address parts, skipping ones already present in the result."""
    result = ""
    for part in parts:
        if not part:
            continue
        text = " ".join(str(part).split())
        if not text:
            continue
        if result and text.casefold() in result.casefold():
            continue
        result = f"{result}, {text}" if result else text
    return result


def registry_evidence(facility: HospitalFacility) -> list[EvidenceItem]:
    """Evidence the MedCompare server actually holds for *facility*.

    Today that is identity and address data only; every other standard
    criterion is intentionally absent so the comparison can report it as
    unavailable rather than inventing a value.
    """
    items: list[EvidenceItem] = []
    address = _join_parts(
        [
            facility.address,
            facility.city,
            _state_postal(facility),
            facility.country,
        ]
    )
    if address:
        items.append(
            EvidenceItem(
                criterion_key="location",
                value=address,
                source="medcompare_server",
                source_name=REGISTRY_SOURCE_NAME,
                source_url=None,
                status="available",
                note=(
                    f"Stored identity record for facility {facility.id} in the MedCompare "
                    "hospital registry."
                ),
            )
        )
    return items


def _state_postal(facility: HospitalFacility) -> str | None:
    bits = [facility.state, facility.postal_code]
    joined = " ".join(str(bit).strip() for bit in bits if bit)
    return joined or None


def evidence_from_intake(
    intake: MasterIntake,
    *,
    target_url: str,
    retrieved_at: datetime | None = None,
) -> list[EvidenceItem]:
    """Convert a completed extraction into attributable evidence items."""
    items: list[EvidenceItem] = []
    crawl_note = f"Extracted from crawled pages of {target_url}."

    locality = _join_parts([intake.area_locality, intake.city, intake.pin_code])
    if locality:
        items.append(
            EvidenceItem(
                criterion_key="location",
                value=locality,
                source="website_crawl",
                source_name=CRAWL_SOURCE_NAME,
                source_url=target_url,
                retrieved_at=retrieved_at,
                status="available",
                note=crawl_note,
            )
        )

    capacity_bits = [
        f"Total beds: {intake.total_beds}" if intake.total_beds else None,
        f"ICU beds: {intake.icu_beds}" if intake.icu_beds else None,
    ]
    icu = _boolean_group(intake, ("icu",))
    if icu is not None:
        capacity_bits.append(icu.value)
    capacity = _join_parts(capacity_bits)
    if capacity:
        items.append(
            EvidenceItem(
                criterion_key="infrastructure",
                value=capacity,
                source="website_crawl",
                source_name=CRAWL_SOURCE_NAME,
                source_url=target_url,
                retrieved_at=retrieved_at,
                status="available",
                confidence=icu.confidence if icu else None,
                note=crawl_note,
            )
        )

    grouped: dict[str, list[str]] = {}
    for boolean_key, criterion_key in _BOOLEAN_CRITERIA.items():
        if boolean_key == "icu":
            continue
        group = _boolean_group(intake, (boolean_key,))
        if group is None:
            continue
        grouped.setdefault(criterion_key, []).append(group.value)

    confidences = _group_confidences(intake)
    for criterion_key, statements in grouped.items():
        items.append(
            EvidenceItem(
                criterion_key=criterion_key,
                value="; ".join(statements),
                source="website_crawl",
                source_name=CRAWL_SOURCE_NAME,
                source_url=_first_source_url(intake, criterion_key) or target_url,
                retrieved_at=retrieved_at,
                status=_group_status(intake, criterion_key),
                confidence=confidences.get(criterion_key),
                note=crawl_note,
            )
        )

    return items


class _BooleanGroup:
    def __init__(self, value: str, confidence: float | None) -> None:
        self.value = value
        self.confidence = confidence


def _boolean_group(intake: MasterIntake, keys: Sequence[str]) -> _BooleanGroup | None:
    statements: list[str] = []
    strengths: list[float] = []
    for key in keys:
        if intake.boolean_state(key) != "yes":
            continue
        entries = intake.evidence.get(key, [])
        label = intake_field(key).label
        yes = [entry for entry in entries if entry.decision == "yes"]
        if not yes:
            continue
        statements.append(label)
        strengths.append(max(entry.strength for entry in yes))
    if not statements:
        return None
    return _BooleanGroup("; ".join(statements), max(strengths) if strengths else None)


def _group_confidences(intake: MasterIntake) -> dict[str, float]:
    confidences: dict[str, float] = {}
    for boolean_key, criterion_key in _BOOLEAN_CRITERIA.items():
        group = _boolean_group(intake, (boolean_key,))
        if group is None or group.confidence is None:
            continue
        current = confidences.get(criterion_key)
        confidences[criterion_key] = (
            group.confidence if current is None else max(current, group.confidence)
        )
    return confidences


def _group_status(intake: MasterIntake, criterion_key: str) -> str:
    """A field that was both affirmed and denied on the site is conflicting."""
    for boolean_key, key in _BOOLEAN_CRITERIA.items():
        if key != criterion_key:
            continue
        entries = intake.evidence.get(boolean_key, [])
        has_yes = any(entry.decision == "yes" for entry in entries)
        has_no = any(entry.decision == "no" for entry in entries)
        if has_yes and has_no:
            return "conflicting"
    return "available"


def _first_source_url(intake: MasterIntake, criterion_key: str) -> str | None:
    for boolean_key, key in _BOOLEAN_CRITERIA.items():
        if key != criterion_key:
            continue
        for entry in intake.evidence.get(boolean_key, []):
            if entry.decision == "yes" and entry.source_url:
                return entry.source_url
    return None


def criterion_evidence(
    criterion_key: str,
    *,
    label: str | None,
    available: Sequence[EvidenceItem],
    overlaps_standard: str | None = None,
) -> list[EvidenceItem]:
    """Evidence items in *available* that speak to the requested criterion.

    A custom criterion first falls back to the standard criterion it overlaps
    (same underlying fact, differently worded), then matches on wording so a
    qualitative criterion such as "room privacy" can still be answered when a
    source actually states it.
    """
    matches = [item for item in available if item.criterion_key == criterion_key]
    if matches:
        return matches
    if overlaps_standard:
        standard_matches = [
            item for item in available if item.criterion_key == overlaps_standard
        ]
        if standard_matches:
            return standard_matches
    if not label:
        return []
    tokens = _content_tokens(label)
    if not tokens:
        return []
    found: list[EvidenceItem] = []
    for item in available:
        haystack = f"{item.value} {item.source_name or ''} {item.note or ''}".casefold()
        if any(re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", haystack) for token in tokens):
            found.append(item)
    return found


def _content_tokens(label: str) -> list[str]:
    raw = re.split(r"[^a-z0-9]+", label.casefold())
    return [
        token
        for token in raw
        if len(token) >= 4 and token not in _STOPWORDS and not token.isdigit()
    ]
