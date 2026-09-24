"""Conflict detection and conservative resolution for single-record categories.

List categories surface conflicts during deduplication. Single-record
categories (hospital overview, contact, infrastructure, visiting) merge
candidate records from several pages; when the candidates disagree on a field
the disagreement becomes a :class:`Conflict` with the kept value, the
alternative, both source URLs and the extraction methods of each source.

Resolution is deliberately conservative: a value is only marked ``resolved``
when one source is clearly more authoritative (structured hospital profile >
generic page). The alternative value and its source are always preserved. When
the sources are of equal authority the conflict stays ``unresolved`` and the
first value is kept — nothing is fabricated.
"""

from __future__ import annotations

from typing import Any

from hospital_extractor.models.conflict import Conflict, ConflictSource

__all__ = [
    "merge_single_record",
    "record_conflicts",
    "authority",
    "build_conflict",
]

# Fields that describe where a value came from rather than the entity itself.
# Two pages describing the same entity will legitimately carry different
# provenance; those differences are not data conflicts.
_SKIP_FIELDS = frozenset(
    {
        "extracted_at",
        "source_url",
        "page_url",
        "page_title",
        "context",
        "extraction_method",
        "source_strength",
        "confidence",
    }
)

# Evidence authority by extraction method (higher is more authoritative).
_METHOD_AUTHORITY = {
    "jsonld": 3,
    "semantic_html": 2,
    "card": 2,
    "table": 2,
    "faq": 2,
    "section": 2,
    "list": 1,
    "heading": 1,
    "text": 0,
}


def merge_single_record(
    category: str, candidates: list[Any], *, priority_fields: tuple[str, ...] = ()
) -> tuple[Any | None, list[Conflict]]:
    """Merge *candidates* into one record, flagging conflicting fields.

    The first candidate is the primary. Later candidates fill only empty
    fields; when both carry different non-null values a conflict is recorded.
    If the later candidate's source is strictly more authoritative the record
    takes its value (``resolved``), otherwise the primary value is kept and the
    conflict stays ``unresolved``. Returns ``(merged_record, conflicts)`` where
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
                primary_data[field_name] = value
                continue
            if current != value:
                conflict = build_conflict(
                    category, field_name, current, value, primary, candidate
                )
                apply_resolution(
                    primary, primary_data, field_name, conflict, alternative_value=value
                )
                conflicts.append(conflict)
    return primary, conflicts


def record_conflicts(category: str, primary: Any, alternative: Any) -> list[Conflict]:
    """List fields where *primary* and *alternative* differ (both non-null)."""
    conflicts: list[Conflict] = []
    for field_name, value in _as_dict(alternative).items():
        if field_name in _SKIP_FIELDS or value is None:
            continue
        current = _as_dict(primary).get(field_name)
        if current is not None and current != value:
            conflict = build_conflict(
                category, field_name, current, value, primary, alternative
            )
            apply_resolution(
                primary, {}, field_name, conflict, alternative_value=value
            )
            conflicts.append(conflict)
    return conflicts


def build_conflict(
    category: str,
    field_name: str,
    current: Any,
    value: Any,
    primary: Any,
    alternative: Any,
) -> Conflict:
    return Conflict(
        category=category,
        field=field_name,
        primary=_stringify(current),
        alternative=_stringify(value),
        values=[_stringify(current), _stringify(value)],
        sources=[_source_info(primary), _source_info(alternative)],
        source_urls=[_source_of(primary), _source_of(alternative)],
    )


def apply_resolution(
    primary: Any,
    primary_data: dict[str, Any],
    field_name: str,
    conflict: Conflict,
    *,
    alternative_value: Any = None,
) -> None:
    """Resolve *conflict* by authority when possible, else leave it open.

    When the two sources have equal authority the conflict is left
    ``unresolved`` and the first (primary) value stays in the record. When the
    alternative source is strictly more authoritative its raw typed value
    (``alternative_value``) is promoted into the record — never the stringified
    form shown on the conflict.
    """
    primary_method = _method_of(conflict.sources[0])
    alternative_method = _method_of(conflict.sources[1])
    primary_authority = authority(primary_method)
    alternative_authority = authority(alternative_method)
    if alternative_authority > primary_authority:
        # Promote the more authoritative value but keep the alternative's
        # provenance in the conflict.
        promoted = conflict.alternative if alternative_value is None else alternative_value
        setattr(primary, field_name, promoted)
        primary_data[field_name] = promoted
        conflict.primary, conflict.alternative = (
            conflict.alternative,
            conflict.primary,
        )
        conflict.preferred_value = conflict.primary
        conflict.resolution_status = "resolved"
        conflict.status = "resolved"
    elif primary_authority > alternative_authority:
        conflict.preferred_value = conflict.primary
        conflict.resolution_status = "resolved"
        conflict.status = "resolved"


def authority(method: str | None) -> int:
    return _METHOD_AUTHORITY.get(method or "", 0)


def _method_of(source: ConflictSource) -> str | None:
    return source.extraction_method


def _as_dict(record: Any) -> dict[str, Any]:
    dump = getattr(record, "model_dump", None)
    if callable(dump):
        return dict(dump(exclude={"extracted_at"}))
    return dict(record)


def _source_of(record: Any) -> str:
    return str(getattr(record, "source_url", ""))


def _source_info(record: Any) -> ConflictSource:
    return ConflictSource(
        url=_source_of(record),
        extraction_method=getattr(record, "extraction_method", None),
        source_strength=getattr(record, "source_strength", None),
    )


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
