"""Snapshot model — an immutable, timestamped capture of a canonical dataset.

A snapshot is the state of a hospital's canonical dataset from one source at
one point in time.  It reuses :class:`~hospital_extractor.models.dataset.HospitalDataset`
verbatim — there is no competing schema; the canonical JSON is the truth.
``content_hash`` is the deterministic semantic fingerprint from
:mod:`hospital_extractor.snapshots.fingerprint`; two snapshots of the same
(`hospital_id`, `source_type`) with equal hashes carry identical data.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from hospital_extractor.models.change import ChangeSet
from hospital_extractor.models.dataset import HospitalDataset

__all__ = ["DatasetSnapshot", "SnapshotCapture", "SnapshotSummary"]


class DatasetSnapshot(BaseModel):
    """One immutable capture of a hospital's canonical dataset.

    ``hospital_id`` is the canonical identity (independent of source, dataset,
    snapshot and name).  ``status`` reflects the change-detection outcome that
    produced this snapshot: ``initial`` / ``detected`` / ``no_change``.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    snapshot_id: str
    hospital_id: str
    source_type: str
    source_id: str = ""
    provider: str | None = None
    hospital_key: str = ""
    hospital_ref: str = ""
    hospital_name: str | None = None
    captured_at: datetime
    content_hash: str
    dataset_id: str = ""
    status: str = "initial"
    dataset: HospitalDataset


class SnapshotSummary(BaseModel):
    """Lightweight, payload-free view of one snapshot for listings."""

    model_config = ConfigDict(str_strip_whitespace=True)

    snapshot_id: str
    hospital_id: str
    source_type: str
    provider: str | None = None
    hospital_ref: str = ""
    hospital_name: str | None = None
    captured_at: datetime
    content_hash: str
    status: str
    record_count: int = 0
    quality_records: int = 0


class SnapshotCapture(BaseModel):
    """Result of one explicit capture: the new snapshot plus its change set."""

    model_config = ConfigDict(str_strip_whitespace=True)

    snapshot: DatasetSnapshot
    change_set: ChangeSet