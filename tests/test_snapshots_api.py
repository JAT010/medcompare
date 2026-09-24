"""API and CLI read-model tests for snapshots & change detection (P19-A).

Covers the new read endpoints (snapshots, changes, registry, single snapshot),
the explicit capture endpoint, and the ``capture`` / ``snapshots`` / ``changes``
/ ``hospitals`` CLI subcommands — all offline.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.cli import main
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.metadata import FetchStatus, SourceMetadata, SourceType
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.storage.database import HospitalDatabase


@pytest.fixture
def client(tmp_path) -> TestClient:
    app = create_app()
    app.state.medcompare_db_path = tmp_path / "medcompare-test.db"
    app.state.medcompare_persist = True
    with TestClient(app) as test_client:
        yield test_client


def _source(ref: str) -> SourceMetadata:
    return SourceMetadata(
        source_type=SourceType("mock_api"),
        source_id=f"mock_api:{ref}",
        hospital_ref=ref,
        base_url=f"https://{ref}",
        retrieved_at="2026-01-01T00:00:00Z",
        status=FetchStatus.SUCCESS,
        hospital_name="",
    )


def _dataset(ref: str = "demo-northstar", speciality: str | None = "Cardiology") -> HospitalDataset:
    return HospitalDataset(
        source=_source(ref),
        hospital=HospitalOverview(
            name="Northstar", bed_count=300, source_url=f"https://{ref}"
        ),
        contacts=[],
        infrastructure=[],
        facilities=[],
        departments=[],
        specialities=(
            [Speciality(name=speciality, source_url=f"https://{ref}/{speciality}")]
            if speciality
            else []
        ),
        doctors=[],
        treatments=[],
        procedures=[],
        technologies=[],
        accreditations=[],
    )


class TestSnapshotsApi:
    def test_capture_then_read_snapshots_and_changes(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/hospitals/ignored/capture",
            json=_dataset().model_dump(mode="json"),
        )
        assert response.status_code == 201
        payload = response.json()
        snapshot_id = payload["snapshot"]["snapshot_id"]
        hospital_id = payload["snapshot"]["hospital_id"]
        assert payload["change_set"]["status"] == "initial_snapshot"
        assert payload["snapshot"]["status"] == "initial"

        history = client.get(f"/api/v1/hospitals/{hospital_id}/snapshots")
        assert history.status_code == 200
        body = history.json()
        assert body["hospital_id"] == hospital_id
        assert len(body["snapshots"]) == 1
        assert body["snapshots"][0]["snapshot_id"] == snapshot_id
        assert len(body["change_sets"]) == 1

        changes = client.get(f"/api/v1/hospitals/{hospital_id}/changes")
        assert changes.status_code == 200
        assert len(changes.json()["change_sets"]) == 1
        assert changes.json()["change_sets"][0]["added"] == 0

        single = client.get(f"/api/v1/snapshots/{snapshot_id}")
        assert single.status_code == 200
        assert single.json()["snapshot"]["snapshot_id"] == snapshot_id
        assert single.json()["snapshot"]["dataset"]["hospital"]["name"] == "Northstar"

    def test_resolution_by_source_ref_and_404(self, client: TestClient) -> None:
        client.post(
            "/api/v1/hospitals/ignored/capture",
            json=_dataset().model_dump(mode="json"),
        )
        by_ref = client.get("/api/v1/hospitals/demo-northstar/snapshots")
        assert by_ref.status_code == 200
        assert by_ref.json()["hospital_id"].startswith("h_")

        missing = client.get("/api/v1/hospitals/nope/snapshots")
        assert missing.status_code == 404
        unknown_snapshot = client.get("/api/v1/snapshots/s_doesnotexist")
        assert unknown_snapshot.status_code == 404

    def test_capture_is_explicit_and_idempotent(self, client: TestClient) -> None:
        payload = _dataset().model_dump(mode="json")
        first = client.post("/api/v1/hospitals/ignored/capture", json=payload)
        second = client.post("/api/v1/hospitals/ignored/capture", json=payload)
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["change_set"]["status"] == "no_change"
        hospital_id = first.json()["snapshot"]["hospital_id"]
        history = client.get(f"/api/v1/hospitals/{hospital_id}/snapshots").json()
        assert len(history["snapshots"]) == 1, "no duplicate snapshot rows"

    def test_changed_capture_creates_change_set(self, client: TestClient) -> None:
        ref = "demo-northstar"
        payload_a = _dataset(ref, "Cardiology").model_dump(mode="json")
        payload_b = _dataset(ref, "Oncology").model_dump(mode="json")
        first = client.post("/api/v1/hospitals/ignored/capture", json=payload_a)
        second = client.post("/api/v1/hospitals/ignored/capture", json=payload_b)
        assert first.status_code == 201
        assert second.status_code == 201
        assert second.json()["change_set"]["status"] == "changes_detected"
        assert second.json()["change_set"]["added"] == 1

    def test_registry_endpoint(self, client: TestClient) -> None:
        assert client.get("/api/v1/hospitals/registry").json()["hospitals"] == []
        client.post("/api/v1/hospitals/ignored/capture", json=_dataset().model_dump(mode="json"))
        hospitals = client.get("/api/v1/hospitals/registry").json()["hospitals"]
        assert len(hospitals) == 1
        assert hospitals[0]["snapshot_count"] == 1

    def test_existing_endpoints_still_work(self, client: TestClient) -> None:
        dataset = _dataset()
        imported = client.post("/api/v1/hospitals/import", json=dataset.model_dump(mode="json"))
        assert imported.status_code == 200
        dataset_id = imported.json()["dataset_id"]
        listing = client.get("/api/v1/hospitals")
        assert listing.status_code == 200
        assert any(d["dataset_id"] == dataset_id for d in listing.json()["datasets"])

        page = client.get(f"/hospitals/{dataset_id}")
        assert page.status_code == 200
        assert "Data history" in page.text


class TestSnapshotsCli:
    def _populate(self, tmp_path) -> tuple[str, str]:
        db_path = tmp_path / "cli.db"
        with HospitalDatabase(db_path) as db:
            db.save_snapshot(_dataset())
        return str(db_path), "demo-northstar"

    def test_hospitals_command(self, tmp_path, capsys) -> None:
        db_path, _ = self._populate(tmp_path)
        assert main(["hospitals", "--db", db_path]) == 0
        out = capsys.readouterr().out
        assert "Hospitals in registry (1)" in out
        assert "snapshots=1" in out

    def test_snapshots_command_by_source_ref(self, tmp_path, capsys) -> None:
        db_path, ref = self._populate(tmp_path)
        assert main(["snapshots", ref, "--db", db_path]) == 0
        out = capsys.readouterr().out
        assert "Snapshots for h_" in out
        assert "initial" in out

    def test_snapshots_command_by_hospital_id(self, tmp_path, capsys) -> None:
        db_path, ref = self._populate(tmp_path)
        with HospitalDatabase(db_path) as db:
            hospital_id = db.find_hospital_id(source_ref=ref)
            assert hospital_id is not None
        assert main(["snapshots", hospital_id, "--db", db_path]) == 0
        assert "Snapshots for h_" in capsys.readouterr().out

    def test_changes_command(self, tmp_path, capsys) -> None:
        db_path, ref = self._populate(tmp_path)
        assert main(["changes", ref, "--db", db_path]) == 0
        out = capsys.readouterr().out
        assert "Change audit for h_" in out
        assert "initial_snapshot" in out

    def test_unknown_hospital_is_an_error(self, tmp_path, capsys) -> None:
        db_path, _ = self._populate(tmp_path)
        assert main(["snapshots", "does-not-exist", "--db", db_path]) != 0
        assert "error: unknown hospital" in capsys.readouterr().err

    def test_capture_command_via_json_feed(self, tmp_path, capsys) -> None:
        from hospital_extractor.storage import write_dataset

        db_path = tmp_path / "cli.db"
        feed = write_dataset(_dataset("demo-solstice"), tmp_path / "feed.json")
        assert main(["capture", str(feed), "--source", "json_feed", "--db", str(db_path)]) == 0
        out = capsys.readouterr().out
        assert "Capturing" in out
        assert "status:        initial_snapshot" in out
        with HospitalDatabase(db_path) as db:
            assert len(db.list_snapshots(db.list_hospitals()[0].hospital_id)) == 1

    def test_capture_repeat_reports_no_change(self, tmp_path, capsys) -> None:
        from hospital_extractor.storage import write_dataset

        db_path = tmp_path / "cli.db"
        feed = write_dataset(_dataset(), tmp_path / "feed.json")
        assert main(["capture", str(feed), "--source", "json_feed", "--db", str(db_path)]) == 0
        capsys.readouterr()
        assert main(["capture", str(feed), "--source", "json_feed", "--db", str(db_path)]) == 0
        out = capsys.readouterr().out
        assert "status:        no_change" in out
        with HospitalDatabase(db_path) as db:
            assert len(db.list_snapshots(db.list_hospitals()[0].hospital_id)) == 1