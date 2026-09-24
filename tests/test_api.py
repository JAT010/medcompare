"""API integration tests.

All tests use mocked extraction — no real HTTP requests are made.
Deterministic and offline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.comparison.models import ComparisonResult
from hospital_extractor.comparison.service import compare_urls
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.treatment import Treatment

_BASE = "https://test-hospital.example"


def _record(source_url: str = _BASE, **kwargs: object) -> dict[str, object]:
    return {"source_url": source_url, **kwargs}


def _make_dataset(name: str = "Test Hospital") -> HospitalDataset:
    return HospitalDataset(
        hospital=HospitalOverview(**_record(name=name, bed_count=300)),
        treatments=[Treatment(**_record(name="Knee Replacement"))],
    )


def _mock_extract_all(urls: list[str]) -> list[tuple[str, HospitalDataset]]:
    return [(url, _make_dataset(f"Hospital from {url}")) for url in urls]


@pytest.fixture
def client(tmp_path) -> TestClient:
    app = create_app()
    app.state.medcompare_db_path = tmp_path / "medcompare-test.db"
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /api/v1/compare
# ---------------------------------------------------------------------------


class TestCompareAPI:
    @patch("hospital_extractor.api.routes._extract_all", new_callable=AsyncMock)
    def test_valid_request(self, mock_extract: AsyncMock, client: TestClient) -> None:
        mock_extract.return_value = _mock_extract_all(
            ["https://a.test", "https://b.test"]
        )
        response = client.post(
            "/api/v1/compare",
            json={
                "urls": ["https://a.test", "https://b.test"],
                "procedure": "Knee Replacement",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "result" in data
        result = ComparisonResult.model_validate(data["result"])
        assert result.target.name == "Total Knee Replacement"
        assert len(result.hospitals) == 2

    @patch("hospital_extractor.api.routes._extract_all", new_callable=AsyncMock)
    def test_three_hospitals(self, mock_extract: AsyncMock, client: TestClient) -> None:
        mock_extract.return_value = _mock_extract_all(
            ["https://a.test", "https://b.test", "https://c.test"]
        )
        response = client.post(
            "/api/v1/compare",
            json={
                "urls": ["https://a.test", "https://b.test", "https://c.test"],
                "procedure": "CABG",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["result"]["hospitals"]) == 3

    def test_too_few_hospitals(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/compare",
            json={
                "urls": ["https://a.test"],
                "procedure": "Knee Replacement",
            },
        )
        assert response.status_code == 422  # Pydantic validation error

    def test_too_many_hospitals(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/compare",
            json={
                "urls": [
                    "https://a.test",
                    "https://b.test",
                    "https://c.test",
                    "https://d.test",
                    "https://e.test",
                ],
                "procedure": "Knee Replacement",
            },
        )
        assert response.status_code == 422

    def test_missing_procedure(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/compare",
            json={
                "urls": ["https://a.test", "https://b.test"],
            },
        )
        assert response.status_code == 422

    def test_empty_body(self, client: TestClient) -> None:
        response = client.post("/api/v1/compare", json={})
        assert response.status_code == 422

    def test_malformed_json(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/compare",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    def test_empty_urls_list(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/compare",
            json={
                "urls": [],
                "procedure": "Knee Replacement",
            },
        )
        assert response.status_code == 422

    @patch("hospital_extractor.api.routes._extract_all", new_callable=AsyncMock)
    def test_response_schema_has_result(
        self, mock_extract: AsyncMock, client: TestClient
    ) -> None:
        mock_extract.return_value = _mock_extract_all(["https://a.test", "https://b.test"])
        response = client.post(
            "/api/v1/compare",
            json={
                "urls": ["https://a.test", "https://b.test"],
                "procedure": "Angioplasty",
            },
        )
        assert response.status_code == 200
        data = response.json()
        result = data["result"]
        assert "target" in result
        assert "hospitals" in result
        assert "compared_at" in result
        assert "name" in result["target"]
        assert "category" in result["target"]
        for hospital in result["hospitals"]:
            assert "hospital_id" in hospital
            assert "dimensions" in hospital
            assert "evidence_coverage" in hospital

    @patch("hospital_extractor.api.routes._extract_all", new_callable=AsyncMock)
    def test_dimensions_have_required_fields(
        self, mock_extract: AsyncMock, client: TestClient
    ) -> None:
        mock_extract.return_value = _mock_extract_all(["https://a.test", "https://b.test"])
        response = client.post(
            "/api/v1/compare",
            json={
                "urls": ["https://a.test", "https://b.test"],
                "procedure": "Knee Replacement",
            },
        )
        data = response.json()
        for hospital in data["result"]["hospitals"]:
            for dim in hospital["dimensions"]:
                assert "dimension_key" in dim
                assert "label" in dim
                assert "decision" in dim
                assert dim["decision"] in ("yes", "no", "unknown")
                assert "evidence" in dim
                assert isinstance(dim["evidence"], list)

    @patch(
        "hospital_extractor.api.routes._extract_all",
        new_callable=AsyncMock,
        side_effect=Exception("Crawl failed"),
    )
    def test_internal_error_returns_500(
        self, mock_extract: AsyncMock, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/compare",
            json={
                "urls": ["https://a.test", "https://b.test"],
                "procedure": "Knee Replacement",
            },
        )
        assert response.status_code == 500
        data = response.json()
        assert "error" in data

    @patch("hospital_extractor.api.routes._extract_all", new_callable=AsyncMock)
    def test_department_category_reaches_engine(
        self, mock_extract: AsyncMock, client: TestClient
    ) -> None:
        mock_extract.return_value = _mock_extract_all(["https://a.test", "https://b.test"])
        response = client.post(
            "/api/v1/compare",
            json={
                "urls": ["https://a.test", "https://b.test"],
                "procedure": "Cardiology",
                "category": "department",
            },
        )
        assert response.status_code == 200
        result = ComparisonResult.model_validate(response.json()["result"])
        assert result.target.category == "department"
        assert result.target.name == "Cardiology"

    def test_invalid_category_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/compare",
            json={
                "urls": ["https://a.test", "https://b.test"],
                "procedure": "Knee Replacement",
                "category": "not-a-category",
            },
        )
        assert response.status_code == 422  # Pydantic Literal validation error


# ---------------------------------------------------------------------------
# GET / (HTML frontend)
# ---------------------------------------------------------------------------


class TestFrontendRoutes:
    def test_index_renders(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_index_contains_form(self, client: TestClient) -> None:
        response = client.get("/")
        assert b"<form" in response.content
        assert b"procedure" in response.content
        assert b"urls" in response.content

    def test_index_contains_htmx(self, client: TestClient) -> None:
        response = client.get("/")
        assert b"htmx" in response.content.lower()

    @patch("hospital_extractor.api.routes._extract_all", new_callable=AsyncMock)
    def test_result_template_renders_with_two_hospitals(
        self, mock_extract: AsyncMock, client: TestClient
    ) -> None:
        mock_extract.return_value = _mock_extract_all(["https://a.test", "https://b.test"])
        response = client.post(
            "/compare",
            data={
                "urls": "https://a.test\nhttps://b.test",
                "procedure": "Knee Replacement",
            },
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        html = response.text
        assert "comparison-container" in html
        assert "decision-yes" in html or "decision-unknown" in html
        assert "YES" in html or "UNKNOWN" in html
        assert "evidence" in html.lower()

    @patch("hospital_extractor.api.routes._extract_all", new_callable=AsyncMock)
    def test_result_template_shows_hospital_names(
        self, mock_extract: AsyncMock, client: TestClient
    ) -> None:
        mock_extract.return_value = _mock_extract_all(["https://a.test", "https://b.test"])
        response = client.post(
            "/compare",
            data={
                "urls": "https://a.test\nhttps://b.test",
                "procedure": "Knee Replacement",
            },
        )
        assert response.status_code == 200
        html = response.text
        assert "Hospital from https://a.test" in html
        assert "Hospital from https://b.test" in html

    def test_web_compare_too_few_urls(self, client: TestClient) -> None:
        response = client.post(
            "/compare",
            data={"urls": "https://a.test", "procedure": "Knee Replacement"},
        )
        assert response.status_code == 200
        assert "at least 2" in response.text.lower()

    def test_web_compare_empty_procedure(self, client: TestClient) -> None:
        response = client.post(
            "/compare",
            data={"urls": "https://a.test\nhttps://b.test", "procedure": ""},
        )
        assert response.status_code == 200
        assert "procedure" in response.text.lower()

    def test_result_template_shows_evidence_coverage(self, client: TestClient) -> None:
        mock_extract_fn = AsyncMock(return_value=_mock_extract_all(["https://a.test", "https://b.test"]))
        with patch("hospital_extractor.api.routes._extract_all", mock_extract_fn):
            response = client.post(
                "/compare",
                data={
                    "urls": "https://a.test\nhttps://b.test",
                    "procedure": "Knee Replacement",
                },
            )
        assert response.status_code == 200
        assert "coverage-card" in response.text
        assert "evidence coverage" in response.text

    def test_index_contains_category_select(self, client: TestClient) -> None:
        response = client.get("/")
        assert b'name="category"' in response.content
        assert b'value="department"' in response.content
        assert b'value="procedure"' in response.content

    def test_web_compare_error_fragment_has_no_hx_retarget(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/compare",
            data={"urls": "https://a.test", "procedure": "Knee Replacement"},
        )
        assert response.status_code == 200
        assert "HX-Retarget" not in response.headers
        assert 'class="error"' in response.text
        assert "at least 2" in response.text.lower()

    @patch(
        "hospital_extractor.api.routes.compare_urls",
        side_effect=Exception("engine boom"),
    )
    @patch("hospital_extractor.api.routes._extract_all", new_callable=AsyncMock)
    def test_web_compare_server_error_renders_fragment(
        self, mock_extract: AsyncMock, mock_compare: object, client: TestClient
    ) -> None:
        mock_extract.return_value = _mock_extract_all(["https://a.test", "https://b.test"])
        response = client.post(
            "/compare",
            data={
                "urls": "https://a.test\nhttps://b.test",
                "procedure": "Knee Replacement",
            },
        )
        assert response.status_code == 200
        assert "HX-Retarget" not in response.headers
        assert 'class="error"' in response.text
        assert "engine boom" in response.text

    @patch("hospital_extractor.api.routes.compare_urls")
    @patch("hospital_extractor.api.routes._extract_all", new_callable=AsyncMock)
    def test_web_compare_passes_category(
        self, mock_extract: AsyncMock, mock_compare: object, client: TestClient
    ) -> None:
        ds = _make_dataset("Hospital")
        result = compare_urls(
            [("https://a.test", ds), ("https://b.test", ds)],
            "Cardiology",
            category="department",
        )
        mock_extract.return_value = [
            ("https://a.test", ds),
            ("https://b.test", ds),
        ]
        mock_compare.return_value = result
        response = client.post(
            "/compare",
            data={
                "urls": "https://a.test\nhttps://b.test",
                "procedure": "Cardiology",
                "category": "department",
            },
        )
        assert response.status_code == 200
        _args, kwargs = mock_compare.call_args
        assert kwargs.get("category") == "department"

    @patch("hospital_extractor.api.routes._extract_all", new_callable=AsyncMock)
    def test_result_template_renders_no_decision(
        self, mock_extract: AsyncMock, client: TestClient
    ) -> None:
        ds = HospitalDataset(
            hospital=HospitalOverview(**_record(name="Hospital N", bed_count=300)),
            treatments=[
                Treatment(
                    **_record(
                        name="Support",
                        description=(
                            "Total knee replacement is not available at our centre."
                        ),
                    )
                )
            ],
        )
        mock_extract.return_value = [
            ("https://a.test", ds),
            ("https://b.test", ds),
        ]
        response = client.post(
            "/compare",
            data={
                "urls": "https://a.test\nhttps://b.test",
                "procedure": "Total Knee Replacement",
            },
        )
        assert response.status_code == 200
        html = response.text
        assert "decision-no" in html
        assert ">NO<" in html
