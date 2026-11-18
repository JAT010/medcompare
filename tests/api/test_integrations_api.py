"""Integration API tests: operator gating, facility isolation, simulated flow."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.persistence.registry.service import FacilityCreate, RegistryService

NORTHGATE = "Northgate General Hospital"


def _seed_facility(db_session, *, canonical_url="https://northgate.example/"):
    service = RegistryService(db_session)
    return service.register_facility(
        FacilityCreate(name=NORTHGATE, canonical_url=canonical_url)
    )


@pytest.fixture()
def facility(db_session):
    return _seed_facility(db_session)


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _mock_integration_body() -> dict:
    return {
        "name": "Simulated baseline",
        "connector_type": "mock_hospital_api_simulated",
        "config_json": {"payload_variant": "default"},
        "enabled": True,
    }


class TestOperatorGate:
    def test_create_integration_closed_without_key(
        self, client: TestClient, facility, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MEDCOMPARE_OPERATOR_API_KEY", raising=False)
        response = client.post(
            f"/api/v1/facilities/{facility.id}/integrations", json=_mock_integration_body()
        )
        assert response.status_code == 503
        assert response.json()["detail"]["error"] == "operator_api_key_not_configured"

    def test_create_integration_invalid_token(
        self, client: TestClient, facility, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_OPERATOR_API_KEY", "secret-test-key")
        response = client.post(
            f"/api/v1/facilities/{facility.id}/integrations",
            json=_mock_integration_body(),
            headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 401

    def test_capture_disabled_without_key(
        self, client: TestClient, facility, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MEDCOMPARE_OPERATOR_API_KEY", raising=False)
        response = client.post(
            f"/api/v1/facilities/{facility.id}/integrations/{uuid.uuid4()}/capture"
        )
        assert response.status_code == 503


class TestSimulatedFlow:
    def test_baseline_changed_capture_and_job(
        self, client: TestClient, facility, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_OPERATOR_API_KEY", "test-operator-key")
        headers = _auth("test-operator-key")

        created = client.post(
            f"/api/v1/facilities/{facility.id}/integrations",
            json=_mock_integration_body(),
            headers=headers,
        )
        assert created.status_code == 201, created.text
        integration = created.json()
        assert integration["connector_type"] == "mock_hospital_api_simulated"
        assert integration["simulated"] is True
        integration_id = integration["id"]

        baseline = client.post(
            f"/api/v1/facilities/{facility.id}/integrations/{integration_id}/capture",
            headers=headers,
        )
        assert baseline.status_code == 200, baseline.text
        baseline_body = baseline.json()
        assert baseline_body["change_set"]["status"] == "initial_snapshot"
        assert baseline_body["verification_job"] is None
        assert baseline_body["snapshot"]["version_number"] == 1
        assert baseline_body["snapshot"]["is_simulation"] is True

        # Flip the simulated source to the "changed" payload variant.
        patched = client.patch(
            f"/api/v1/facilities/{facility.id}/integrations/{integration_id}",
            json={"config_json": {"payload_variant": "changed"}},
            headers=headers,
        )
        assert patched.status_code == 200

        changed = client.post(
            f"/api/v1/facilities/{facility.id}/integrations/{integration_id}/capture",
            headers=headers,
        )
        assert changed.status_code == 200, changed.text
        changed_body = changed.json()
        assert changed_body["change_set"]["status"] == "changes_detected"
        assert changed_body["snapshot"]["version_number"] == 2
        job = changed_body["verification_job"]
        assert job is not None
        assert changed_body["job_created"] is True
        assert job["status"] == "pending"
        assert job["trigger_type"] == "change_detected"
        paths = {c["path"] for c in changed_body["change_set"]["changes"]}
        assert "hospital.bed_count" in paths

        jobs = client.get(f"/api/v1/facilities/{facility.id}/verification-jobs")
        assert jobs.status_code == 200
        assert len(jobs.json()["items"]) == 1

        first_manual = client.post(
            f"/api/v1/facilities/{facility.id}/verification-jobs", headers=headers
        )
        assert first_manual.status_code == 201
        assert first_manual.json()["created"] is True
        second_manual = client.post(
            f"/api/v1/facilities/{facility.id}/verification-jobs", headers=headers
        )
        assert second_manual.status_code == 201
        assert second_manual.json()["created"] is False
        assert (
            first_manual.json()["verification_job"]["id"]
            == second_manual.json()["verification_job"]["id"]
        )

        snaps = client.get(f"/api/v1/facilities/{facility.id}/snapshots")
        assert snaps.status_code == 200
        assert [s["version_number"] for s in snaps.json()["items"]] == [2, 1]
        # Summary rows never expose the payload.
        assert all(s["payload_json"] is None for s in snaps.json()["items"])

        detail = client.get(f"/api/v1/facilities/{facility.id}/change-sets/"
                            f"{changed_body['change_set']['id']}")
        assert detail.status_code == 200
        assert any(c["path"] == "infrastructure.total_beds" for c in detail.json()["changes"])

    def test_verification_job_detail_and_404s(
        self, client: TestClient, facility, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_OPERATOR_API_KEY", "test-operator-key")
        headers = _auth("test-operator-key")
        created = client.post(
            f"/api/v1/facilities/{facility.id}/integrations",
            json=_mock_integration_body(),
            headers=headers,
        ).json()
        job = client.post(
            f"/api/v1/facilities/{facility.id}/integrations/{created['id']}/capture",
            headers=headers,
        ).json()["verification_job"]
        assert job is None  # initial snapshot: no job
        missing = client.get(
            f"/api/v1/facilities/{facility.id}/verification-jobs/{uuid.uuid4()}"
        )
        assert missing.status_code == 404
        assert client.get("/api/v1/facilities/not-a-uuid/verification-jobs").status_code == 422


class TestCrossFacilityIsolation:
    def test_integrations_snapshots_jobs_are_facility_scoped(
        self, client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_OPERATOR_API_KEY", "test-operator-key")
        headers = _auth("test-operator-key")
        other = _seed_facility(db_session, canonical_url="https://other.example/")
        mine = _seed_facility(db_session, canonical_url="https://mine.example/")
        # Configure an integration under "other" only.
        created = client.post(
            f"/api/v1/facilities/{other.id}/integrations",
            json=_mock_integration_body(),
            headers=headers,
        ).json()

        mine_list = client.get(f"/api/v1/facilities/{mine.id}/integrations")
        assert mine_list.status_code == 200
        assert mine_list.json()["items"] == []

        stolen_detail = client.get(
            f"/api/v1/facilities/{mine.id}/integrations/{created['id']}"
        )
        assert stolen_detail.status_code == 404

        mine_snapshots = client.get(f"/api/v1/facilities/{mine.id}/snapshots")
        assert mine_snapshots.json()["items"] == []

        mine_jobs = client.get(f"/api/v1/facilities/{mine.id}/verification-jobs")
        assert mine_jobs.json()["items"] == []

        other_integration = created["id"]
        first_capture = client.post(
            f"/api/v1/facilities/{other.id}/integrations/{other_integration}/capture",
            headers=headers,
        )
        assert first_capture.status_code == 200, first_capture.text
        assert first_capture.json()["change_set"]["status"] == "initial_snapshot"
        client.patch(
            f"/api/v1/facilities/{other.id}/integrations/{other_integration}",
            json={"config_json": {"payload_variant": "changed"}},
            headers=headers,
        )
        second_capture = client.post(
            f"/api/v1/facilities/{other.id}/integrations/{other_integration}/capture",
            headers=headers,
        )
        assert second_capture.status_code == 200, second_capture.text
        assert second_capture.json()["change_set"]["status"] == "changes_detected"
        mine_change_sets = client.get(f"/api/v1/facilities/{mine.id}/change-sets")
        assert mine_change_sets.json()["items"] == []

        other_jobs = client.get(f"/api/v1/facilities/{other.id}/verification-jobs").json()["items"]
        assert len(other_jobs) == 1
        mine_cancel = client.post(
            f"/api/v1/facilities/{mine.id}/verification-jobs/{other_jobs[0]['id']}/cancel",
            headers=headers,
        )
        assert mine_cancel.status_code == 404


class TestMonitoringApi:
    def test_schedule_crud_and_executions(
        self, client: TestClient, facility, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_OPERATOR_API_KEY", "test-operator-key")
        headers = _auth("test-operator-key")
        created = client.post(
            f"/api/v1/facilities/{facility.id}/integrations",
            json=_mock_integration_body(),
            headers=headers,
        ).json()

        schedule = client.post(
            f"/api/v1/facilities/{facility.id}/monitoring/schedules",
            json={
                "name": "drift",
                "integration_id": created["id"],
                "interval_seconds": 120,
                "enabled": True,
                "target_ref": "https://northgate.example/",
            },
            headers=headers,
        )
        assert schedule.status_code == 201, schedule.text
        assert schedule.json()["enabled"] is True

        too_fast = client.post(
            f"/api/v1/facilities/{facility.id}/monitoring/schedules",
            json={
                "name": "too fast",
                "integration_id": created["id"],
                "interval_seconds": 30,
                "enabled": True,
            },
            headers=headers,
        )
        assert too_fast.status_code == 422

        listed = client.get(
            f"/api/v1/facilities/{facility.id}/monitoring/schedules"
        )
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1

        patched = client.patch(
            f"/api/v1/facilities/{facility.id}/monitoring/schedules/"
            f"{schedule.json()['id']}",
            json={"enabled": False},
            headers=headers,
        )
        assert patched.status_code == 200
        assert patched.json()["enabled"] is False

        executions = client.get(
            f"/api/v1/facilities/{facility.id}/monitoring/executions"
        )
        assert executions.status_code == 200
        assert executions.json()["items"] == []


class TestReconciliationApi:
    def test_manual_case_and_transitions(
        self, client: TestClient, facility, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_OPERATOR_API_KEY", "test-operator-key")
        headers = _auth("test-operator-key")

        created = client.post(
            f"/api/v1/facilities/{facility.id}/reconciliation-cases",
            json={"summary": "manual review item", "notes": "see bed count"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        case = created.json()
        assert case["status"] == "open"

        transition = client.post(
            f"/api/v1/facilities/{facility.id}/reconciliation-cases/{case['id']}/transitions",
            json={"to_status": "under_review", "actor_label": "nurse", "note": "looking"},
            headers=headers,
        )
        assert transition.status_code == 200
        assert transition.json()["status"] == "under_review"
        assert [e["to_status"] for e in transition.json()["events"]] == [
            "open",
            "under_review",
        ]

        bad = client.post(
            f"/api/v1/facilities/{facility.id}/reconciliation-cases/{case['id']}/transitions",
            json={"to_status": "resolved", "actor_label": "nurse"},
            headers=headers,
        )
        assert bad.status_code == 422  # resolution required

        resolved = client.post(
            f"/api/v1/facilities/{facility.id}/reconciliation-cases/{case['id']}/transitions",
            json={
                "to_status": "resolved",
                "resolution": "website_confirmed",
                "actor_label": "nurse",
                "note": "website wins for now",
            },
            headers=headers,
        )
        assert resolved.status_code == 200
        body = resolved.json()
        assert body["status"] == "resolved"
        assert body["resolution"] == "website_confirmed"
        assert [e["to_status"] for e in body["events"]] == [
            "open",
            "under_review",
            "resolved",
        ]

        listing = client.get(
            f"/api/v1/facilities/{facility.id}/reconciliation-cases"
        )
        assert listing.status_code == 200
        assert len(listing.json()["items"]) == 1

    def test_case_isolation_across_facilities(
        self, client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_OPERATOR_API_KEY", "test-operator-key")
        headers = _auth("test-operator-key")
        other = _seed_facility(db_session, canonical_url="https://other.example/")
        mine = _seed_facility(db_session, canonical_url="https://mine.example/")
        # Seed the facility fixture for this test is "other"; create a case under
        # "other", then read it via "mine" to prove facility isolation.
        created = client.post(
            f"/api/v1/facilities/{other.id}/reconciliation-cases",
            json={"summary": "isolated"},
            headers=headers,
        )
        case_id = created.json()["id"]
        stolen = client.get(
            f"/api/v1/facilities/{mine.id}/reconciliation-cases/{case_id}"
        )
        assert stolen.status_code == 404
