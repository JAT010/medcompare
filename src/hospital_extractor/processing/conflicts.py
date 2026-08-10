"""Conflict detection for single-record categories.

List categories surface conflicts during deduplication. Single-record
categories (hospital overview, contact, infrastructure, visiting) merge
candidate records from several pages; when the candidates disagree on a field
the disagreement becomes a :class:`Conflict` with the kept value and the
alternative, plus both source URLs.
"""

from __future__ import annotations

from typing import Any

from hospital_extractor.models.conflict import Conflict

__all__ = ["merge_single_record", "record_conflicts"]

_SKIP_FIELDS = frozenset({"extracted_at"})


def merge_single_record(
    category: str, candidates: list[Any], *, priority_fields: tuple[str, ...] = ()
) -> tuple[Any | None, list[Conflict]]:
    """Merge *candidates* into one record, flagging conflicting fields.

    The first candidate is the primary. Later candidates fill only empty
    fields; when both carry different non-null values a conflict is recorded
    (the primary value is kept). Returns ``(merged_record, conflicts)`` where
    ``merged_record`` is ``None`` when there are no candidates.
    """
    if not candidates:
        return None, []
    primary = candidates[0]
    conflicts: list[Conflict] = []
    primary_data = _as_dict(primary)

    for candidate in candidates[1:]:
        for field_name, value in _as_dict(candidate).items():
            if field_name in _SKIP_FIELDS or value is None:
                continue
            current = primary_data.get(field_name)
            if current is None:
                setattr(primary, field_name, value)
                continue
            if current != value:
                conflicts.append(
                    Conflict(
                        category=category,
                        field=field_name,
                        primary=_stringify(current),
                        alternative=_stringify(value),
                        source_urls=[_source_of(primary), _source_of(candidate)],
                    )
                )
    return primary, conflicts


def record_conflicts(category: str, primary: Any, alternative: Any) -> list[Conflict]:
    """List fields where *primary* and *alternative* differ (both non-null)."""
    conflicts: list[Conflict] = []
    for field_name, value in _as_dict(alternative).items():
        if field_name in _SKIP_FIELDS or value is None:
            continue
        current = _as_dict(primary).get(field_name)
        if current is not None and current != value:
            conflicts.append(
                Conflict(
                    category=category,
                    field=field_name,
                    primary=_stringify(current),
                    alternative=_stringify(value),
                    source_urls=[_source_of(primary), _source_of(alternative)],
                )
            )
    return conflicts


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
