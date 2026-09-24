"""Field-aware, collection-aware change detection between canonical datasets.

The detector's inputs are consecutive snapshots of the SAME hospital from the
SAME source — it never compares across sources (that is P19-C reconciliation)
and it never compares two different hospitals.

Design rules:

- Comparison operates on canonical datasets, never raw HTML / API JSON.
- Collections (specialities, doctors, procedures, ...) are matched by stable
  identity derived from the existing schema, not by position.  A list that
  merely reorders therefore produces NO changes.
- Named entities match on their normalized ``name``; doctors prefer
  ``profile_url`` when present (a stable per-doctor link) and fall back to
  ``name``.  When no identity exists, the full semantic record is used.
- Within a matched pair, only semantic (non-provenance) fields are compared;
  each differing field becomes one ``MODIFIED`` change.
- Single-record categories (hospital, contacts, infrastructure) are treated
  as at-most-one-element collections with the fixed identity ``primary``.
- The FIRST snapshot a hospital+source ever sees is reported as
  ``initial_snapshot`` with NO changes — an empty database never produces a
  wall of "added" noise (see :func:`build_change_set`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from hospital_extractor.models.base import EntityRecord
from hospital_extractor.models.change import Change, ChangeSet, ChangeSetStatus, ChangeType
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.snapshot import DatasetSnapshot
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES
from hospital_extractor.snapshots.fingerprint import PROVENANCE_FIELDS

__all__ = ["build_change_set", "compute_changes"]

# Deterministic identity for single-record categories (hospital, contacts,
# infrastructure) — there is at most one of each, so the pair always matches.
_PRIMARY_IDENTITY = "primary"

_IDENTITY_FIELDS = ("profile_url", "name")


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _new_change_set_id() -> str:
    return f"c_{uuid4().hex[:12]}"


def _collection_items(dataset: HospitalDataset, key: str) -> list[EntityRecord]:
    """Records for a category as a list (hospital scalar is wrapped)."""
    if key == "hospital":
        hospital = dataset.hospital
        return [hospital] if hospital is not None else []
    items = getattr(dataset, key, [])
    return cast("list[EntityRecord]", list(items) if isinstance(items, list) else [])


def _identity(record: EntityRecord) -> str:
    """Stable identity for a record, schema-aware."""
    for field in _IDENTITY_FIELDS:
        value = getattr(record, field, None)
        if value:
            return str(value).strip().lower()
    return _PRIMARY_IDENTITY


def _semantic_fields(record: EntityRecord) -> dict[str, object]:
    """Non-provenance, non-null fields of a record (semantic content only)."""
    data = record.model_dump(mode="json")
    return {
        key: value
        for key, value in data.items()
        if key not in PROVENANCE_FIELDS and value is not None
    }


def _label(record: EntityRecord) -> str:
    """Human label for a record (its name, or a compact descriptor)."""
    name = getattr(record, "name", None)
    if name:
        return str(name)
    for field in ("city", "total_beds", "address"):
        value = getattr(record, field, None)
        if value is not None:
            return str(value)
    return "record"


def _diff_field_changes(
    category: str,
    identity: str,
    entity_name: str | None,
    previous: EntityRecord,
    current: EntityRecord,
) -> list[Change]:
    """Field-level differences between two matched records."""
    previous_fields = _semantic_fields(previous)
    current_fields = _semantic_fields(current)
    changes: list[Change] = []
    for field in sorted(set(previous_fields) | set(current_fields)):
        old_value = previous_fields.get(field)
        new_value = current_fields.get(field)
        if old_value != new_value:
            changes.append(
                Change(
                    category=category,
                    path=f"{category}.{field}",
                    field=field,
                    change_type=ChangeType.MODIFIED,
                    entity_name=entity_name,
                    old_value=old_value,
                    new_value=new_value,
                    source_url=str(getattr(current, "source_url", "") or ""),
                )
            )
    return changes


def _diff_category(
    category: str,
    previous: list[EntityRecord],
    current: list[EntityRecord],
) -> list[Change]:
    """Diff one collection category, identity-keyed (order-insensitive)."""
    previous_by_key: dict[str, list[EntityRecord]] = {}
    for record in previous:
        previous_by_key.setdefault(_identity(record), []).append(record)
    current_by_key: dict[str, list[EntityRecord]] = {}
    for record in current:
        current_by_key.setdefault(_identity(record), []).append(record)

    changes: list[Change] = []

    for key in sorted(set(current_by_key) - set(previous_by_key)):
        record = current_by_key[key][0]
        changes.append(
            Change(
                category=category,
                path=category,
                field=category,
                change_type=ChangeType.ADDED,
                entity_name=_label(record),
                new_value=_label(record),
                source_url=str(getattr(record, "source_url", "") or ""),
            )
        )

    for key in sorted(set(previous_by_key) - set(current_by_key)):
        record = previous_by_key[key][0]
        changes.append(
            Change(
                category=category,
                path=category,
                field=category,
                change_type=ChangeType.REMOVED,
                entity_name=_label(record),
                old_value=_label(record),
                source_url=str(getattr(record, "source_url", "") or ""),
            )
        )

    for key in sorted(set(current_by_key) & set(previous_by_key)):
        for previous_record, current_record in zip(
            previous_by_key[key], current_by_key[key], strict=False
        ):
            entity_name = _label(current_record)
            changes.extend(
                _diff_field_changes(
                    category, key, entity_name, previous_record, current_record
                )
            )
    return changes


def compute_changes(
    previous_dataset: HospitalDataset,
    current_dataset: HospitalDataset,
) -> list[Change]:
    """Return field-level changes from *previous_dataset* to *current_dataset*.

    Both datasets must describe the same hospital from the same source.
    Collection order is irrelevant: items are matched by stable identity.
    """
    changes: list[Change] = []
    for category in CANONICAL_CATEGORIES:
        key = category.key
        changes.extend(
            _diff_category(
                key,
                _collection_items(previous_dataset, key),
                _collection_items(current_dataset, key),
            )
        )
    changes.sort(key=lambda change: (change.category, change.path, change.entity_name or ""))
    return changes


def build_change_set(
    previous: DatasetSnapshot | None,
    current: DatasetSnapshot,
    *,
    change_set_id: str | None = None,
    detected_at: datetime | None = None,
) -> ChangeSet:
    """Build the :class:`ChangeSet` for *current* relative to *previous*.

    First snapshot semantics: with no previous snapshot the status is
    ``initial_snapshot`` and the change list is EMPTY — the database being
    empty is not a signal that hundreds of things were "added".
    """
    timestamp = detected_at or _now_utc()
    identifier = change_set_id or _new_change_set_id()

    if previous is None:
        return ChangeSet(
            change_set_id=identifier,
            snapshot_id=current.snapshot_id,
            previous_snapshot_id=None,
            hospital_id=current.hospital_id,
            source_type=current.source_type,
            provider=current.provider,
            detected_at=timestamp,
            status=ChangeSetStatus.INITIAL_SNAPSHOT,
            changes=[],
        )

    if previous.content_hash == current.content_hash:
        return ChangeSet(
            change_set_id=identifier,
            snapshot_id=current.snapshot_id,
            previous_snapshot_id=previous.snapshot_id,
            hospital_id=current.hospital_id,
            source_type=current.source_type,
            provider=current.provider,
            detected_at=timestamp,
            status=ChangeSetStatus.NO_CHANGE,
            changes=[],
        )

    changes = compute_changes(previous.dataset, current.dataset)
    for change in changes:
        if not change.source_type:
            change.source_type = current.source_type
        if change.provider is None:
            change.provider = current.provider
        if change.captured_at is None:
            change.captured_at = current.captured_at
    return ChangeSet(
        change_set_id=identifier,
        snapshot_id=current.snapshot_id,
        previous_snapshot_id=previous.snapshot_id,
        hospital_id=current.hospital_id,
        source_type=current.source_type,
        provider=current.provider,
        detected_at=timestamp,
        status=ChangeSetStatus.CHANGES_DETECTED,
        changes=changes,
    )