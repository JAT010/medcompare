"""Change-detection models: field-level, auditable changes between snapshots.

A :class:`ChangeSet` is the deterministic output of comparing two consecutive
snapshots of the same hospital from the same source.  Every change is
field-aware (``path`` like ``specialities`` or ``hospital.bed_count``) and
preserves provenance — the new state's source, provider, capture time and the
source page the value came from.

Status lifecycle (P19-A scope only)::

    initial_snapshot  ->  changes_detected  ->  (future) verified
    initial_snapshot  ->  no_change         ->  (future) verified

``VERIFIED`` / ``NOTIFIED`` / ``GOVERNMENT_SUBMITTED`` belong to P19-B/C/D and
are intentionally NOT modelled here; this enum only knows what P19-A can
observe (a first capture, an unchanged capture, or detected changes).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Change", "ChangeSet", "ChangeSetStatus", "ChangeType"]


class ChangeType(StrEnum):
    """Kind of a single detected change."""

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


class ChangeSetStatus(StrEnum):
    """Lifecycle status of one change-detection run.

    P19-A produces exactly one of INITIAL_SNAPSHOT / NO_CHANGE /
    CHANGES_DETECTED.  Verification states are a later phase.
    """

    INITIAL_SNAPSHOT = "initial_snapshot"
    NO_CHANGE = "no_change"
    CHANGES_DETECTED = "changes_detected"


class Change(BaseModel):
    """One auditable change between two snapshots.

    ``path`` is a dotted reference into the canonical dataset, e.g. a
    collection key (``specialities``) for an item add/remove, or a field
    within an entity (``hospital.bed_count```).  ``entity_name`` identifies
    the collection item the change belongs to (``None`` for single-record
    fields).  Old/new values carry the actual canonical values.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    category: str
    path: str
    field: str
    change_type: ChangeType
    entity_name: str | None = None
    old_value: Any = None
    new_value: Any = None
    # Provenance of the NEW state (the change is visible because the new
    # capture introduced it); for REMOVED the value came from the old capture.
    source_type: str = ""
    provider: str | None = None
    captured_at: datetime | None = None
    source_url: str = ""


class ChangeSet(BaseModel):
    """The complete result of comparing two consecutive snapshots."""

    model_config = ConfigDict(str_strip_whitespace=True)

    change_set_id: str
    snapshot_id: str
    previous_snapshot_id: str | None = None
    hospital_id: str
    source_type: str
    provider: str | None = None
    detected_at: datetime
    status: ChangeSetStatus = ChangeSetStatus.CHANGES_DETECTED
    changes: list[Change] = Field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(1 for change in self.changes if change.change_type is ChangeType.ADDED)

    @property
    def removed(self) -> int:
        return sum(1 for change in self.changes if change.change_type is ChangeType.REMOVED)

    @property
    def modified(self) -> int:
        return sum(1 for change in self.changes if change.change_type is ChangeType.MODIFIED)

    @property
    def total(self) -> int:
        return len(self.changes)