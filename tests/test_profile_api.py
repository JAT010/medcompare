"""Profile-store API tests (list / read / import / delete / HTML pages)."""

from __future__ import annotations

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.mockapi.app import create_mock_app
from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource


@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.state.medcompare_db_path = tmp_path / "medcompare-test.db"
    app.state.medcompare_persist = True
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def demo_payload() -> dict:
    transport = httpx.ASGITransport(app=create_mock_app())
    source = MockHospitalAPIDataSource(
        hospital_id="demo-northstar",
        client=httpx.AsyncClient(transport=transport, base_url="http://testserver"),
    )
    dataset = anyio.run(source.fetch)
    return dataset.model_dump(mode="json")


class TestJsonEndpoints:
    def test_empty_list(self, client):
        response = client.get("/api/v1/hospitals")
        assert response.status_code == 200
        assert response.json()["datasets"] == []

    def test_import_then_get(self, client, demo_payload):
        response = client.post("/api/v1/hospitals/import", json=demo_payload)
        assert response.status_code == 200
        dataset_id = response.json()["dataset_id"]
        assert dataset_id

        get = client.get(f"/api/v1/hospitals/{dataset_id}")
        assert get.status_code == 200
        body = get.json()["dataset"]
        assert body["hospital"]["name"] == "Northstar Multispeciality Hospital"

        listing = client.get("/api/v1/hospitals").json()["datasets"]
        assert len(listing) == 1
        assert listing[0]["dataset_id"] == dataset_id
        assert listing[0]["source_type"] == "mock_api"

    def test_get_missing_returns_404(self, client):
        response = client.get("/api/v1/hospitals/does-not-exist")
        assert response.status_code == 404
        assert response.json()["error"] == "Hospital not found"

    def test_delete(self, client, demo_payload):
        dataset_id = client.post("/api/v1/hospitals/import", json=demo_payload).json()["dataset_id"]
        deleted = client.delete(f"/api/v1/hospitals/{dataset_id}")
        assert deleted.json() == {"deleted": True}
        gone = client.delete(f"/api/v1/hospitals/{dataset_id}")
        assert gone.json() == {"deleted": False}


class TestHtmlPages:
    def test_hospitals_gallery_empty(self, client):
        response = client.get("/hospitals")
        assert response.status_code == 200
        assert "No hospital datasets stored yet." in response.text

    def test_profile_page(self, client, demo_payload):
        dataset_id = client.post("/api/v1/hospitals/import", json=demo_payload).json()["dataset_id"]
        page = client.get(f"/hospitals/{dataset_id}")
        assert page.status_code == 200
        assert "Northstar Multispeciality Hospital" in page.text
        assert "mock_api" in page.text

    def test_profile_page_missing(self, client):
        response = client.get("/hospitals/nope")
        assert response.status_code == 200
        assert "not found" in response.text