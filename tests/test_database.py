"""SQLite hospital-database persistence tests."""

from __future__ import annotations

import anyio
import httpx
import pytest

from hospital_extractor.mockapi.app import create_mock_app
from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource
from hospital_extractor.storage.database import (
    DatasetSummary,
    HospitalDatabase,
)


@pytest.fixture
def demo_dataset():
    transport = httpx.ASGITransport(app=create_mock_app())
    source = MockHospitalAPIDataSource(
        hospital_id="demo-northstar",
        client=httpx.AsyncClient(transport=transport, base_url="http://testserver"),
    )
    return anyio.run(source.fetch)


@pytest.fixture
def db(tmp_path):
    store = HospitalDatabase(tmp_path / "medcompare.db")
    yield store
    store.close()


class TestSaveAndLoad:
    def test_save_returns_id_and_load_round_trips(self, db, demo_dataset):
        dataset_id = db.save_dataset(demo_dataset)
        assert dataset_id
        loaded = db.load_dataset(dataset_id)
        assert loaded is not None
        assert loaded.hospital is not None
        assert loaded.hospital.name == "Northstar Multispeciality Hospital"
        assert loaded.source is not None
        assert loaded.source.hospital_ref == "demo-northstar"
        assert loaded.source.source_type.value == "mock_api"

    def test_record_source_id_matches_scoped_sources_source_id(self, db, demo_dataset) -> None:
        """records.source_id is scoped exactly like sources.source_id (P18.1)."""
        dataset_id = db.save_dataset(demo_dataset)
        scoped = db._connection.execute(  # noqa: SLF001
            "SELECT source_id FROM sources WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()["source_id"]
        record_sources = {
            row["source_id"]
            for row in db._connection.execute(  # noqa: SLF001
                "SELECT source_id FROM records WHERE dataset_id = ?", (dataset_id,)
            ).fetchall()
        }
        assert scoped.startswith(f"{dataset_id}::")
        assert record_sources == {scoped}

    def test_load_missing_returns_none(self, db):
        assert db.load_dataset("does-not-exist") is None

    def test_delete_dataset(self, db, demo_dataset):
        dataset_id = db.save_dataset(demo_dataset)
        assert db.delete_dataset(dataset_id) is True
        assert db.load_dataset(dataset_id) is None
        assert db.delete_dataset(dataset_id) is False


class TestListAndSearch:
    def test_list_datasets(self, db, demo_dataset):
        dataset_id = db.save_dataset(demo_dataset)
        summaries = db.list_datasets()
        assert len(summaries) == 1
        summary = summaries[0]
        assert isinstance(summary, DatasetSummary)
        assert summary.dataset_id == dataset_id
        assert summary.hospital_ref == "demo-northstar"
        assert summary.source_type == "mock_api"
        assert summary.hospital_name == "Northstar Multispeciality Hospital"
        assert summary.record_count > 0

    def test_search_records_by_source(self, db, demo_dataset):
        db.save_dataset(demo_dataset)
        rows = db.search_records(source_type="mock_api")
        assert rows
        assert all(row["source_type"] == "mock_api" for row in rows)

    def test_search_records_by_category_and_name(self, db, demo_dataset):
        db.save_dataset(demo_dataset)
        rows = db.search_records(category="procedures", name_contains="knee")
        assert rows
        assert all(row["category"] == "procedures" for row in rows)


class TestHospitalKeyGrouping:
    def test_same_hospital_from_two_sources_groups_together(self, db, demo_dataset) -> None:
        key = db.hospital_key_for(demo_dataset)
        assert key
        db.save_dataset(demo_dataset)

        variant = demo_dataset.model_copy(deep=True)
        variant.source.hospital_ref = "northstar-variant"
        db.save_dataset(variant)

        group = db.list_hospital_sources(key)
        assert len(group) == 2
        refs = {summary.hospital_ref for summary in group}
        assert refs == {"demo-northstar", "northstar-variant"}
        assert {summary.source_type for summary in group} == {"mock_api"}

    def test_different_hospital_does_not_group(self, db, demo_dataset) -> None:
        key = db.hospital_key_for(demo_dataset)
        db.save_dataset(demo_dataset)
        other = demo_dataset.model_copy(deep=True)
        other.hospital.name = "Solstice Heart & Bone Institute"
        db.save_dataset(other)
        assert len(db.list_hospital_sources(key)) == 1

    def test_summary_exposes_hospital_key(self, db, demo_dataset) -> None:
        db.save_dataset(demo_dataset)
        summary = db.list_datasets()[0]
        assert summary.hospital_key == db.hospital_key_for(demo_dataset)
        assert summary.hospital_key != ""


class TestSchemaMigration:
    def test_legacy_database_gains_hospital_key_column(self, tmp_path) -> None:
        """A DB created before ``hospital_key`` gets the column additively."""
        import sqlite3

        path = tmp_path / "legacy.db"
        connection = sqlite3.connect(str(path))
        connection.executescript(
            """
            CREATE TABLE datasets (
                dataset_id   TEXT PRIMARY KEY,
                hospital_ref TEXT NOT NULL,
                hospital_name TEXT,
                source_type  TEXT NOT NULL,
                provider     TEXT,
                created_at   TEXT NOT NULL,
                payload      TEXT NOT NULL
            );
            CREATE TABLE sources (
                source_id TEXT PRIMARY KEY, dataset_id TEXT, source_type TEXT
            );
            CREATE TABLE records (
                id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id TEXT NOT NULL,
                category TEXT NOT NULL, source_type TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )
        connection.commit()
        connection.close()

        store = HospitalDatabase(path)
        store.close()
        connection = sqlite3.connect(str(path))
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(datasets)").fetchall()
        }
        connection.close()
        assert "hospital_key" in columns
        assert store is not None

    def test_save_after_migration_persists_sources_and_records(
        self, tmp_path, demo_dataset
    ) -> None:
        """A realistic pre-P18 DB upgrades additively and stays fully writable."""
        import sqlite3

        path = tmp_path / "legacy-full.db"
        connection = sqlite3.connect(str(path))
        connection.executescript(
            """
            CREATE TABLE datasets (
                dataset_id   TEXT PRIMARY KEY,
                hospital_ref TEXT NOT NULL,
                hospital_name TEXT,
                source_type  TEXT NOT NULL,
                provider     TEXT,
                created_at   TEXT NOT NULL,
                payload      TEXT NOT NULL
            );
            CREATE TABLE sources (
                source_id     TEXT PRIMARY KEY,
                dataset_id    TEXT,
                source_type   TEXT NOT NULL,
                provider      TEXT,
                base_url      TEXT,
                hospital_ref  TEXT,
                hospital_name TEXT,
                retrieved_at  TEXT,
                status        TEXT
            );
            CREATE TABLE records (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id  TEXT NOT NULL,
                category    TEXT NOT NULL,
                name        TEXT,
                source_url  TEXT,
                source_type TEXT NOT NULL,
                source_id   TEXT,
                payload     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_records_dataset ON records(dataset_id);
            CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_type);
            CREATE INDEX IF NOT EXISTS idx_dataset_source ON datasets(source_type);
            """
        )
        connection.commit()
        connection.close()

        with HospitalDatabase(path) as store:
            dataset_id = store.save_dataset(demo_dataset)
            loaded = store.load_dataset(dataset_id)
            summary = store.list_datasets()[0]
            sources = store._connection.execute(  # noqa: SLF001
                "SELECT COUNT(*) AS n FROM sources WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()["n"]
            records = store._connection.execute(  # noqa: SLF001
                "SELECT COUNT(*) AS n FROM records WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()["n"]

        assert loaded is not None
        assert loaded.source is not None
        assert loaded.source.hospital_ref == "demo-northstar"
        assert summary.hospital_key == HospitalDatabase.hospital_key_for(demo_dataset)
        assert summary.hospital_key != ""
        assert sources == 1
        assert records > 0


class TestIsolationAndCleanup:
    def test_delete_removes_sources_and_records(self, db, demo_dataset):
        dataset_id = db.save_dataset(demo_dataset)
        row = db._connection.execute(  # noqa: SLF001
            "SELECT COUNT(*) AS n FROM records WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()
        assert row["n"] > 0
        db.delete_dataset(dataset_id)
        row = db._connection.execute(  # noqa: SLF001
            "SELECT COUNT(*) AS n FROM records WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()
        assert row["n"] == 0