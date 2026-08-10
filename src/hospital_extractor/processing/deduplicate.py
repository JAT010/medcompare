"""Conservative entity-level deduplication.

Duplicates are merged only when the identity evidence is strong: same
normalized profile URL for doctors, same normalized name (plus year for
awards/accreditations) for named entities. Records that merely "look similar"
are preserved, never merged. When two duplicates carry different values for
the same field the disagreement is surfaced as a :class:`Conflict`, never
silently resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hospital_extractor.models.conflict import Conflict
from hospital_extractor.processing.conflicts import (
    _SKIP_FIELDS,
    apply_resolution,
    build_conflict,
)
from hospital_extractor.processing.normalize import normalize_text, normalize_url_value

__all__ = ["DedupResult", "deduplicate_category", "identity_key"]


@dataclass
class DedupResult:
    """Outcome of deduplicating one category."""

    records: list[Any]
    removed: int = 0
    conflicts: list[Conflict] = field(default_factory=list)


def identity_key(category: str, record: Any) -> tuple[str, ...] | None:
    """Return a deduplication identity for *record*, or ``None`` to keep it unique."""
    data = _as_dict(record)
    name = normalize_text(data.get("name")) or normalize_text(data.get("label"))
    if category in {"facilities", "departments", "specialities", "emergency_services",
                    "patient_services", "programs", "technologies"}:
        return (name.lower(),) if name else None
    if category in {"treatments", "procedures"}:
        key = name.lower() if name else ""
        speciality = (data.get("speciality") or "").lower()
        return (key, speciality) if name else None
    if category == "insurance":
        return (name.lower(),) if name else None
    if category in {"awards", "accreditations"}:
        year = str(data.get("year") or "")
        return (name.lower(), year) if name else None
    if category == "other_information":
        return (name.lower(),) if name else None
    if category == "doctors":
        profile_url = normalize_url_value(data.get("profile_url"))
        if profile_url:
            return ("url", profile_url)
        if name:
            speciality = (data.get("speciality") or "").lower()
            return ("name", name.lower(), speciality)
        return None
    return None


def deduplicate_category(category: str, records: list[Any]) -> DedupResult:
    """Merge duplicates in *records* by identity, preserving first-seen order."""
    result = DedupResult(records=[])
    buckets: dict[tuple[str, ...], list[Any]] = {}
    for record in records:
        key = identity_key(category, record)
        if key is None:
            result.records.append(record)
            continue
        buckets.setdefault(key, []).append(record)

    for _key, group in buckets.items():
        if len(group) == 1:
            result.records.append(group[0])
            continue
        primary, *others = group
        for other in others:
            _merge_into(primary, other, category, result)
        result.removed += len(others)
        result.records.append(primary)
    return result


def _merge_into(primary: Any, other: Any, category: str, result: DedupResult) -> None:
    """Merge *other* into *primary*; record conflicting fields as conflicts."""
    primary_data = _as_dict(primary)
    for field_name, value in _as_dict(other).items():
        if field_name in _SKIP_FIELDS or value is None:
            continue
        current = primary_data.get(field_name)
        if current is None:
            setattr(primary, field_name, value)
            primary_data[field_name] = value
            continue
        if current != value:
            conflict = build_conflict(
                category, field_name, current, value, primary, other
            )
            apply_resolution(
                primary, primary_data, field_name, conflict, alternative_value=value
            )
            result.conflicts.append(conflict)


def _as_dict(record: Any) -> dict[str, Any]:
    dump = getattr(record, "model_dump", None)
    if callable(dump):
        return dict(dump(exclude={"extracted_at"}))
    return dict(record)


def _source_of(record: Any) -> str:
    return str(getattr(record, "source_url", ""))


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
