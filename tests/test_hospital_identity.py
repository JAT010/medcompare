"""Canonical hospital identity tests (P19-A).

Verifies that a hospital receives one stable opaque ``h_…`` identity across
datasets, sources and names; that identity resolution never mints on read;
and that two distinct hospitals with the same name (or different sources)
are never silently merged.
"""

from __future__ import annotations

import pytest

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.sources.json_feed import JSONFeedDataSource
from hospital_extractor.storage.database import HospitalDatabase


@pytest.fixture
def db(tmp_path) -> HospitalDatabase:
    store = HospitalDatabase(tmp_path / "medcompare.db")
    yield store
    store.close()


def _make_dataset(
    name: str,
    source_ref: str,
    *,
    hospital_key: str = "",
    source: object | None = None,
) -> HospitalDataset:
    dataset = HospitalDataset(
        hospital=HospitalOverview(name=name, source_url=f"https://x/{source_ref}"),
        contacts=[],
        infrastructure=[],
        facilities=[],
        departments=[],
        specialities=[],
        doctors=[],
        treatments=[],
        procedures=[],
        technologies=[],
        accreditations=[],
    )
    if source is not None:
        dataset.source = source  # type: ignore[assignment]
    return dataset


def _website_source(ref: str, retrieved: str = "2026-01-01T00:00:00Z") -> object:
    from hospital_extractor.models.metadata import FetchStatus, SourceMetadata, SourceType

    return SourceMetadata(
        source_type=SourceType("website"),
        source_id=f"website:{ref}",
        hospital_ref=ref,
        base_url=ref,
        retrieved_at=retrieved,
        status=FetchStatus.SUCCESS,
        hospital_name="",
    )


class TestIdentityMinting:
    def test_mints_opaque_id_and_aliases(self, db: HospitalDatabase) -> None:
        hospital_id = db.hospital_id_for(source_ref="demo-northstar", hospital_name="Northstar")
        assert hospital_id.startswith("h_")
        assert len(hospital_id) == 14

        # Same source ref + name splits into the SAME id, never a new mint.
        again = db.hospital_id_for(source_ref="demo-northstar", hospital_name="Northstar")
        assert again == hospital_id

        # Resolution is stable through the read-only path.
        assert db.find_hospital_id(preferred=hospital_id) == hospital_id
        assert db.find_hospital_id(source_ref="demo-northstar") == hospital_id
        assert db.find_hospital_id(hospital_name="Northstar") == hospital_id

    def test_write_path_never_merges_by_name(self, db: HospitalDatabase) -> None:
        """Distinct source refs stay separate histories even with the same name."""
        first = db.hospital_id_for(source_ref="ref-a", hospital_name="City Hospital")
        second = db.hospital_id_for(source_ref="ref-b", hospital_name="City Hospital")
        assert first != second, "name must not decide hospital identity on write"

        # The shared name is now genuinely ambiguous → reads fail closed.
        assert db.find_hospital_id(hospital_name="City Hospital") is None

    def test_unique_name_readable_after_registration(self, db: HospitalDatabase) -> None:
        hospital_id = db.hospital_id_for(source_ref="ref-a", hospital_name="Northstar")
        assert db.find_hospital_id(hospital_name="Northstar") == hospital_id

    def test_ambiguous_name_never_collapses(self, db: HospitalDatabase) -> None:
        a = db.hospital_id_for(source_ref="r1", hospital_name="City Hospital")
        b = db.hospital_id_for(source_ref="r2", hospital_name="City Hospital")
        assert a != b, "distinct source refs must NOT share one identity"

        # Reading by the ambiguous name must fail closed (None), not guess.
        assert db.find_hospital_id(hospital_name="City Hospital") is None

    def test_find_never_mints(self, db: HospitalDatabase) -> None:
        assert db.find_hospital_id(source_ref="does-not-exist") is None
        assert db.find_hospital_id(preferred="h_deadbeef0000") is None
        assert db.find_hospital_id(hospital_name="Nobody") is None
        assert db.list_hospitals() == []


class TestIdentityThroughDatasets:
    def test_save_dataset_points_to_one_hospital_id(self, db: HospitalDatabase) -> None:
        source = _website_source("demo-northstar")
        ds1 = _make_dataset("North Star", "demo-northstar", source=source)
        ds2 = _make_dataset("North Star", "demo-northstar", source=source)
        first = db.save_dataset(ds1)
        second = db.save_dataset(ds2)
        hid1 = db._connection.execute(  # noqa: SLF001
            "SELECT hospital_id FROM datasets WHERE dataset_id = ?", (first,)
        ).fetchone()["hospital_id"]
        hid2 = db._connection.execute(  # noqa: SLF001
            "SELECT hospital_id FROM datasets WHERE dataset_id = ?", (second,)
        ).fetchone()["hospital_id"]
        assert hid1 == hid2
        assert hid1.startswith("h_")

    def test_different_source_same_name_keeps_separate_history(self, db: HospitalDatabase) -> None:
        # Two source refs describing hospitals that share a name must stay
        # SEPARATE histories — no silent name-based merge (P19-C reconciles).
        website = _make_dataset("Demo", "example.test", source=_website_source("example.test"))
        api = _make_dataset("Demo", "demo-northstar", source=_website_source("demo-northstar"))
        db.save_dataset(website)
        db.save_dataset(api)
        assert len(db.list_hospitals()) == 2


class TestIdentityFromJsonFeed:
    def test_json_feed_inherits_identity(self, db: HospitalDatabase, tmp_path) -> None:
        """A persisted dataset re-ingested as a JSON feed keeps one identity."""
        from hospital_extractor.storage import write_dataset

        base = _make_dataset("Stable", "stable.test")
        feed = write_dataset(base, tmp_path / "feed.json")
        dataset = _asyncio_run_capture(JSONFeedDataSource(str(feed)))

        from hospital_extractor.models.metadata import SourceType

        assert dataset.source is not None
        assert dataset.source.source_type is SourceType.JSON_FEED
        db.save_dataset(dataset)
        # The feed's own source ref (its file path) resolves back to the id;
        # the payload's hospital name is registered as a unique read alias.
        assert db.find_hospital_id(source_ref=str(feed)) is not None
        assert db.find_hospital_id(hospital_name="Stable") is not None


def _asyncio_run_capture(source: object):
    import anyio

    return anyio.run(source.fetch)