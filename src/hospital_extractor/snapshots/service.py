"""Snapshot capture service: acquire, version, fingerprint, diff, persist."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hospital_extractor.persistence.models_integration import (
    ChangeSetStatus,
    HospitalDataSnapshot,
    HospitalDetectedChange,
    HospitalSnapshotChangeSet,
    HospitalSourceIntegration,
)
from hospital_extractor.snapshots.change_detection import DetectedChange, compute_changes
from hospital_extractor.snapshots.fingerprint import canonical_digest, strip_provenance
from hospital_extractor.sources.base import (
    AcquisitionResult,
    HospitalSourceConnector,
)

__all__ = [
    "CaptureResult",
    "SnapshotService",
    "SnapshotServiceError",
]


class SnapshotServiceError(RuntimeError):
    """Snapshot capture/persistence failure."""


@dataclass(frozen=True)
class CaptureResult:
    """The result of capturing one snapshot."""

    snapshot: HospitalDataSnapshot
    change_set: HospitalSnapshotChangeSet
    changes: tuple[HospitalDetectedChange, ...]
    acquisition: AcquisitionResult


class SnapshotService:
    """Capture hospital-provided snapshots and detect changes."""

    def __init__(self, session: Session) -> None:
        self.session = session

    async def capture(
        self,
        integration: HospitalSourceIntegration,
        *,
        connector: HospitalSourceConnector,
        source_kind: str,
        captured_by: str = "system",
    ) -> CaptureResult:
        """Acquire from *connector*, persist a versioned snapshot and its diff.

        Raises:
            hospital_extractor.sources.base.HospitalConnectorError: on acquisition failure.
            SnapshotServiceError: on persistence failure.
        """
        result = await connector.acquire()
        payload = result.payload
        if not isinstance(payload, dict):
            payload = {"payload": payload}
        semantic_payload = strip_provenance(payload)
        fingerprint = canonical_digest(semantic_payload)

        integration_id = integration.id
        facility_id = integration.facility_id
        now = datetime.now(UTC)

        previous = self._latest_snapshot(facility_id, source_kind)
        version_number = 1 if previous is None else previous.version_number + 1

        snapshot = HospitalDataSnapshot(
            facility_id=facility_id,
            integration_id=integration_id,
            source_kind=source_kind,
            version_number=version_number,
            content_hash=fingerprint,
            payload_json=payload,
            provenance_json={
                "source_kind": source_kind,
                "connector_type": integration.connector_type,
                "provider": result.provider,
                "requested_url": result.requested_url,
                "status_code": result.status_code,
                "content_type": result.content_type,
                "acquisition": result.evidence,
                "simulated": result.simulated,
                "captured_at": result.captured_at.isoformat(),
                "raw_size_bytes": result.raw_size_bytes,
                "content_hash": fingerprint,
            },
            captured_at=result.captured_at,
            captured_by=captured_by,
            is_simulation=result.simulated,
        )

        for attempt in range(2):
            self.session.add(snapshot)
            try:
                # SAVEPOINT so a version-number race never discards caller state.
                with self.session.begin_nested():
                    self.session.flush()
            except IntegrityError:
                if attempt == 0:
                    previous = self._latest_snapshot(facility_id, source_kind)
                    version_number = 1 if previous is None else previous.version_number + 1
                    snapshot.version_number = version_number
                    continue
                raise SnapshotServiceError("could not allocate a snapshot version number") from None
            break

        detected = self._diff(previous, snapshot)
        change_set = HospitalSnapshotChangeSet(
            facility_id=facility_id,
            integration_id=integration_id,
            previous_snapshot_id=previous.id if previous is not None else None,
            snapshot_id=snapshot.id,
            status=detected.status,
            added_count=detected.added,
            removed_count=detected.removed,
            modified_count=detected.modified,
            detected_at=now,
        )
        self.session.add(change_set)
        self.session.flush()

        rows: list[HospitalDetectedChange] = []
        for change in detected.changes:
            rows.append(
                HospitalDetectedChange(
                    change_set_id=change_set.id,
                    facility_id=facility_id,
                    path=change.path,
                    change_type=change.change_type,
                    entity_name=change.entity_name,
                    field=change.field,
                    old_value_json=change.old_value,
                    new_value_json=change.new_value,
                    source_url=result.requested_url,
                    detected_at=now,
                )
            )
        if rows:
            self.session.add_all(rows)
            self.session.flush()
        return CaptureResult(
            snapshot=snapshot,
            change_set=change_set,
            changes=tuple(rows),
            acquisition=result,
        )

    def list_for_facility(
        self,
        facility_id: uuid.UUID,
        *,
        source_kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[HospitalDataSnapshot]:
        stmt = select(HospitalDataSnapshot).where(HospitalDataSnapshot.facility_id == facility_id)
        if source_kind is not None:
            stmt = stmt.where(HospitalDataSnapshot.source_kind == source_kind)
        stmt = (
            stmt.order_by(
                HospitalDataSnapshot.version_number.desc(),
                HospitalDataSnapshot.captured_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def get_snapshot(self, snapshot_id: uuid.UUID) -> HospitalDataSnapshot | None:
        return self.session.get(HospitalDataSnapshot, snapshot_id)

    def get_snapshot_for_facility(
        self, snapshot_id: uuid.UUID, facility_id: uuid.UUID
    ) -> HospitalDataSnapshot | None:
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None or snapshot.facility_id != facility_id:
            return None
        return snapshot

    def latest_for_facility(
        self, facility_id: uuid.UUID, *, source_kind: str | None = None
    ) -> HospitalDataSnapshot | None:
        stmt = select(HospitalDataSnapshot).where(HospitalDataSnapshot.facility_id == facility_id)
        if source_kind is not None:
            stmt = stmt.where(HospitalDataSnapshot.source_kind == source_kind)
        stmt = stmt.order_by(
            HospitalDataSnapshot.version_number.desc(),
            HospitalDataSnapshot.captured_at.desc(),
        ).limit(1)
        return self.session.scalars(stmt).first()

    def get_change_set(self, change_set_id: uuid.UUID) -> HospitalSnapshotChangeSet | None:
        return self.session.get(HospitalSnapshotChangeSet, change_set_id)

    def list_change_sets(
        self, facility_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> list[HospitalSnapshotChangeSet]:
        stmt = (
            select(HospitalSnapshotChangeSet)
            .where(HospitalSnapshotChangeSet.facility_id == facility_id)
            .order_by(
                HospitalSnapshotChangeSet.detected_at.desc(),
                HospitalSnapshotChangeSet.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def list_changes(self, change_set_id: uuid.UUID) -> list[HospitalDetectedChange]:
        stmt = (
            select(HospitalDetectedChange)
            .where(HospitalDetectedChange.change_set_id == change_set_id)
            .order_by(HospitalDetectedChange.path, HospitalDetectedChange.change_type)
        )
        return list(self.session.scalars(stmt).all())

    def _latest_snapshot(
        self, facility_id: uuid.UUID, source_kind: str
    ) -> HospitalDataSnapshot | None:
        return self.latest_for_facility(facility_id, source_kind=source_kind)

    def _diff(
        self,
        previous: HospitalDataSnapshot | None,
        current: HospitalDataSnapshot,
    ) -> _DiffResult:
        if previous is None:
            return _DiffResult(ChangeSetStatus.INITIAL_SNAPSHOT.value, [], 0, 0, 0)
        if previous.content_hash == current.content_hash:
            return _DiffResult(ChangeSetStatus.NO_CHANGE.value, [], 0, 0, 0)
        previous_payload = strip_provenance(previous.payload_json)
        current_payload = strip_provenance(current.payload_json)
        changes = compute_changes(previous_payload, current_payload)
        added = sum(1 for c in changes if c.change_type == "added")
        removed = sum(1 for c in changes if c.change_type == "removed")
        modified = sum(1 for c in changes if c.change_type == "modified")
        return _DiffResult(
            ChangeSetStatus.CHANGES_DETECTED.value, changes, added, removed, modified
        )


@dataclass(frozen=True)
class _DiffResult:
    status: str
    changes: list[DetectedChange]
    added: int
    removed: int
    modified: int
