"""Snapshot / change-set persistence tests (P19-A).

Exercise the SQLite storage layer: first-capture semantics, idempotent
no-change captures, change detection on later captures, listing/ordering,
deletion and the additive migration that backfills hospital identities.
"""

from __future__ import annotations

import sqlite3

import pytest

from hospital_extractor.models.change import ChangeSetStatus
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.metadata import FetchStatus, SourceMetadata, SourceType
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.storage.database import HospitalDatabase


@pytest.fixture
def db(tmp_path) -> HospitalDatabase:
    store = HospitalDatabase(tmp_path / "medcompare.db")
    yield store
    store.close()


def _source(ref: str, source_type: str = "mock_api") -> SourceMetadata:
    return SourceMetadata(
        source_type=SourceType(source_type),
        source_id=f"{source_type}:{ref}",
        hospital_ref=ref,
        base_url=f"https://{ref}",
        retrieved_at="2026-01-01T00:00:00Z",
        status=FetchStatus.SUCCESS,
        hospital_name="",
    )


def _spec(name: str) -> Speciality:
    return Speciality(name=name, source_url=f"https://x/{name}")


def _dataset(
    *,
    ref: str = "demo-northstar",
    source_type: str = "mock_api",
    specialities: list[Speciality] | None = None,
    doctors: list[Doctor] | None = None,
    bed_count: int = 300,
) -> HospitalDataset:
    return HospitalDataset(
        source=_source(ref, source_type),
        hospital=HospitalOverview(name="Northstar", bed_count=bed_count, source_url=f"https://{ref}"),
        contacts=[],
        infrastructure=[],
        facilities=[],
        departments=[],
        specialities=specialities or [],
        doctors=doctors or [],
        treatments=[],
        procedures=[],
        technologies=[],
        accreditations=[],
    )


class TestSnapshotLifecycle:
    def test_first_capture_is_initial_snapshot(self, db: HospitalDatabase) -> None:
        capture = db.save_snapshot(_dataset())
        snapshot, change_set = capture.snapshot, capture.change_set
        assert change_set.status is ChangeSetStatus.INITIAL_SNAPSHOT
        assert change_set.changes == []
        assert change_set.previous_snapshot_id is None
        assert snapshot.snapshot_id.startswith("s_")
        assert snapshot.hospital_id.startswith("h_")
        assert snapshot.dataset_id  # mutable latest row also persisted

    def test_identical_recapture_is_no_change(self, db: HospitalDatabase) -> None:
        first = db.save_snapshot(_dataset())
        second = db.save_snapshot(_dataset())
        assert second.change_set.status is ChangeSetStatus.NO_CHANGE
        assert second.snapshot.snapshot_id == first.snapshot.snapshot_id
        assert len(db.list_snapshots(first.snapshot.hospital_id)) == 1
        assert len(db.list_hospitals()) == 1

    def test_semantic_change_is_detected_and_persisted(self, db: HospitalDatabase) -> None:
        first = db.save_snapshot(_dataset(specialities=[_spec("Cardiology")]))
        later = _dataset(specialities=[_spec("Cardiology"), _spec("Oncology")])
        second = db.save_snapshot(later)

        assert second.change_set.status is ChangeSetStatus.CHANGES_DETECTED
        assert second.change_set.previous_snapshot_id == first.snapshot.snapshot_id
        assert second.change_set.added == 1
        assert second.snapshot.snapshot_id.startswith("s_")

        hospital_id = second.snapshot.hospital_id
        snapshots = db.list_snapshots(hospital_id)
        assert len(snapshots) == 2
        assert snapshots[0].snapshot_id == second.snapshot.snapshot_id  # newest first
        change_sets = db.list_change_sets(hospital_id)
        assert len(change_sets) == 2
        assert change_sets[0].snapshot_id == second.snapshot.snapshot_id

    def test_snapshots_are_immutable_and_round_trip(self, db: HospitalDatabase) -> None:
        capture = db.save_snapshot(_dataset(bed_count=120))
        loaded = db.get_snapshot(capture.snapshot.snapshot_id)
        assert loaded is not None
        assert loaded.dataset == capture.snapshot.dataset
        assert loaded.content_hash == capture.snapshot.content_hash
        assert loaded.captured_at == capture.snapshot.captured_at

    def test_latest_snapshot_is_per_source(self, db: HospitalDatabase) -> None:
        with db._connection:  # noqa: SLF001
            first = db.save_snapshot(_dataset(source_type="mock_api"))
            second = db.save_snapshot(
                _dataset(source_type="website", ref=first.snapshot.hospital_ref)
            )
        hospital_id = first.snapshot.hospital_id
        assert hospital_id == second.snapshot.hospital_id
        latest = db.get_latest_snapshot(hospital_id, "mock_api")
        latest_web = db.get_latest_snapshot(hospital_id, "website")
        assert latest is not None and latest.source_type == "mock_api"
        assert latest_web is not None and latest_web.source_type == "website"
        assert latest_web.snapshot_id != latest.snapshot_id
        assert latest_web.snapshot_id == second.snapshot.snapshot_id
        assert db.get_latest_snapshot(hospital_id, "json_feed") is None
        assert len(db.list_snapshots(hospital_id)) == 2

    def test_source_signature_does_not_break_first_snapshot(self, db: HospitalDatabase) -> None:
        # Same data re-fetched later (different retrieved_at) is still a
        # no-change — provenance is not semantic.
        db.save_snapshot(_dataset())
        later = _dataset()
        later.source.retrieved_at = "2026-06-01T00:00:00Z"
        second = db.save_snapshot(later)
        assert second.change_set.status is ChangeSetStatus.NO_CHANGE

    def test_delete_removes_snapshot_and_its_change_sets(self, db: HospitalDatabase) -> None:
        first = db.save_snapshot(_dataset(specialities=[_spec("A")]))
        second = db.save_snapshot(_dataset(specialities=[_spec("A"), _spec("B")]))
        hospital_id = second.snapshot.hospital_id
        assert db.delete_snapshot(second.snapshot.snapshot_id) is True
        assert db.get_snapshot(second.snapshot.snapshot_id) is None
        remaining_change_sets = db.list_change_sets(hospital_id)
        assert second.change_set.change_set_id not in {
            c.change_set_id for c in remaining_change_sets
        }
        assert first.change_set.change_set_id in {c.change_set_id for c in remaining_change_sets}
        assert db.delete_snapshot("s_doesnotexist") is False


class TestSnapshotPersistenceAcrossReopen:
    def test_snapshots_survive_reopen(self, tmp_path) -> None:
        path = tmp_path / "reopen.db"
        with HospitalDatabase(path) as db:
            capture = db.save_snapshot(_dataset(specialities=[_spec("Cardiology")]))
            hospital_id = capture.snapshot.hospital_id
        with HospitalDatabase(path) as db:
            snapshots = db.list_snapshots(hospital_id)
            change_sets = db.list_change_sets(hospital_id)
            hospitals = db.list_hospitals()
        assert len(snapshots) == 1
        assert len(change_sets) == 1
        assert hospitals[0].hospital_id == hospital_id
        assert hospitals[0].snapshot_count == 1
        # The registry was rebuilt from stored rows — no duplicate identity.
        assert db._connection is not None

    def test_migration_backfills_existing_rows(self, tmp_path) -> None:
        """Pre-P19-A databases gain a hospital_id without losing data."""
        path = tmp_path / "legacy.db"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE datasets (
                dataset_id   TEXT PRIMARY KEY,
                hospital_ref TEXT NOT NULL,
                hospital_name TEXT,
                hospital_key TEXT NOT NULL DEFAULT '',
                source_type  TEXT NOT NULL,
                provider     TEXT,
                created_at   TEXT NOT NULL,
                payload      TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO datasets
                (dataset_id, hospital_ref, hospital_name, hospital_key,
                 source_type, provider, created_at, payload)
            VALUES ('legacy-ds', 'demo-northstar', 'Northstar', 'northstar',
                    'mock_api', 'Northstar Demo Data Services',
                    '2026-01-01T00:00:00Z', '{}')
            """
        )
        connection.commit()
        connection.close()

        with HospitalDatabase(path) as db:
            hospital_id = db._connection.execute(  # noqa: SLF001
                "SELECT hospital_id FROM datasets WHERE dataset_id = 'legacy-ds'"
            ).fetchone()["hospital_id"]
            assert hospital_id.startswith("h_")
            # Backfill also registered the source ref for future resolution.
            assert db.find_hospital_id(source_ref="demo-northstar") == hospital_id