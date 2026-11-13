"""Tests for comparison endpoints: capabilities, interpretation, matrix."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api import create_app
from hospital_extractor.api.deps import get_db_session
from hospital_extractor.api.routers.crawl import CrawlOutcome, get_crawl_runner

NORTHGATE = "Northgate General Hospital"
SOUTHWIND = "Southwind Medical Centre"


def _registered(facility_id: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {"type": "registered", "facility_id": facility_id}
    payload.update(extra)
    return payload


def test_capabilities_report_names_missing_integrations(client) -> None:
    response = client.get("/api/v1/comparison/capabilities")

    assert response.status_code == 200
    capabilities = {item["key"]: item for item in response.json()["capabilities"]}

    assert capabilities["registered_hospital_retrieval"]["available"] is True
    assert capabilities["standard_comparison"]["available"] is True
    assert capabilities["custom_prompt_interpretation"]["available"] is True
    assert capabilities["website_crawling_on_request"]["available"] is True
    assert capabilities["evidence_provenance"]["available"] is True

    search = capabilities["public_internet_search"]
    assert search["available"] is False
    assert search["required_integration"]
    assert "search provider" in search["required_integration"].lower()

    jobs = capabilities["asynchronous_crawl_jobs"]
    assert jobs["available"] is False
    assert "persistent job store" in (jobs["required_integration"] or "")

    model = capabilities["model_assisted_interpretation"]
    assert model["available"] is False
    assert "language-model client" in (model["required_integration"] or "")


def test_interpret_endpoint_returns_criteria_not_hospital_facts(client) -> None:
    response = client.post(
        "/api/v1/comparison/interpret",
        json={"prompt": "Compare parking, private rooms and visiting hours"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["label"] for item in body["criteria"]] == [
        "Parking",
        "Private Rooms",
        "Visiting Hours",
    ]
    assert body["interpreter"] == "deterministic_clause_parser"
    assert "No language model is configured" in body["interpretation_note"]
    assert all(item["origin"] == "custom" for item in body["criteria"])
    assert all(item["key"] for item in body["criteria"])
    assert not any("rating" in item["evidence_needed"].lower() for item in body["criteria"])


def test_interpret_endpoint_rejects_blank_prompt(client) -> None:
    response = client.post("/api/v1/comparison/interpret", json={"prompt": "   "})

    assert response.status_code == 422


def test_interpret_endpoint_reports_ambiguity(client) -> None:
    response = client.post(
        "/api/v1/comparison/interpret", json={"prompt": "compare the good stuff"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["criteria"] == []
    assert body["clarification_questions"]


def test_compare_registered_facilities_by_physical_identity(client, seeded) -> None:
    northgate = seeded["northgate"]
    southwind = seeded["southwind"]

    response = client.post(
        "/api/v1/comparison",
        json={
            "scope": "standard_12",
            "source": "medcompare_server",
            "hospitals": [
                _registered(str(northgate.id)),
                _registered(str(southwind.id)),
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [column["name"] for column in body["columns"]] == [NORTHGATE, SOUTHWIND]
    assert body["columns"][0]["column_id"] == str(northgate.id)
    assert body["columns"][0]["identity_note"]
    assert len(body["rows"]) == 12
    assert body["scope"] == "standard_12"
    assert "No winner" in body["disclaimer"]

    location = next(row for row in body["rows"] if row["criterion"]["key"] == "location")
    assert location["cells"][0]["status"] == "available"
    assert location["cells"][0]["source"] == "medcompare_server"
    assert location["cells"][1]["status"] == "available"
    assert location["cells"][1]["value"] == "Southmere, Southmere State"

    affordability = next(row for row in body["rows"] if row["criterion"]["key"] == "affordability")
    assert all(cell["status"] == "unavailable" for cell in affordability["cells"])
    assert all(cell["value"] is None for cell in affordability["cells"])


def test_compare_includes_all_three_source_statuses(client, seeded) -> None:
    facility = seeded["northgate"]
    other = seeded["southwind"]

    response = client.post(
        "/api/v1/comparison",
        json={
            "scope": "standard_12",
            "source": "medcompare_server",
            "hospitals": [_registered(str(facility.id)), _registered(str(other.id))],
        },
    )

    statuses = {item["source"]: item for item in response.json()["source_statuses"]}
    assert statuses["medcompare_server"]["status"] == "active"
    assert statuses["public_research"]["status"] == "not_configured"
    assert statuses["website_crawl"]["status"] == "active"


def test_public_research_reports_missing_search_integration(client, seeded) -> None:
    facility = seeded["northgate"]
    other = seeded["southwind"]

    response = client.post(
        "/api/v1/comparison",
        json={
            "scope": "standard_12",
            "source": "public_research",
            "hospitals": [_registered(str(facility.id)), _registered(str(other.id))],
        },
    )

    assert response.status_code == 501
    detail = response.json()["detail"]
    assert detail["error"] == "public_research_not_configured"
    assert "search provider" in detail["required_integration"].lower()
    assert "cannot research hospitals" in detail["message"]


def test_unregistered_hospital_is_rejected_for_server_source(client, seeded) -> None:
    facility = seeded["northgate"]

    response = client.post(
        "/api/v1/comparison",
        json={
            "scope": "standard_12",
            "source": "medcompare_server",
            "hospitals": [
                _registered(str(facility.id)),
                {"type": "unregistered", "name": "Harbour View Hospital", "location": "Testville"},
            ],
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "unregistered_hospital_requires_other_source"
    assert "Harbour View Hospital" in detail["message"]
    assert "website crawling" in detail["message"]


def test_unknown_facility_identity_returns_404(client, seeded) -> None:
    other = seeded["southwind"]

    response = client.post(
        "/api/v1/comparison",
        json={
            "scope": "standard_12",
            "source": "medcompare_server",
            "hospitals": [_registered(str(uuid.uuid4())), _registered(str(other.id))],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "facility_not_found"


def test_duplicate_facility_is_rejected(client, seeded) -> None:
    facility = seeded["northgate"]

    response = client.post(
        "/api/v1/comparison",
        json={
            "scope": "standard_12",
            "source": "medcompare_server",
            "hospitals": [_registered(str(facility.id)), _registered(str(facility.id))],
        },
    )

    assert response.status_code == 422
    messages = " ".join(str(item.get("msg", "")) for item in response.json()["detail"])
    assert "may only appear once" in messages


def test_custom_only_without_interpreted_criteria_returns_422(client, seeded) -> None:
    facility = seeded["northgate"]
    other = seeded["southwind"]

    response = client.post(
        "/api/v1/comparison",
        json={
            "scope": "custom_only",
            "source": "medcompare_server",
            "criteria": [],
            "hospitals": [_registered(str(facility.id)), _registered(str(other.id))],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "invalid_criteria"


def test_standard_plus_custom_adds_interpreted_rows(client, seeded) -> None:
    facility = seeded["northgate"]
    other = seeded["southwind"]
    interpreted = client.post(
        "/api/v1/comparison/interpret",
        json={"prompt": "compare parking and vegetarian meal options"},
    ).json()["criteria"]

    response = client.post(
        "/api/v1/comparison",
        json={
            "scope": "standard_12_plus_custom",
            "source": "medcompare_server",
            "criteria": interpreted,
            "hospitals": [_registered(str(facility.id)), _registered(str(other.id))],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["rows"]) == 14
    custom_rows = [row for row in body["rows"] if row["criterion"]["origin"] == "custom"]
    assert [row["criterion"]["label"] for row in custom_rows] == [
        "Parking",
        "Vegetarian Meal Options",
    ]
    assert all(cell["status"] == "unavailable" for row in custom_rows for cell in row["cells"])


def test_crawl_mode_requires_extraction_before_comparing(client, seeded) -> None:
    facility = seeded["northgate"]

    response = client.post(
        "/api/v1/comparison",
        json={
            "scope": "standard_12",
            "source": "website_crawl",
            "hospitals": [
                _registered(str(facility.id)),
                {
                    "type": "unregistered",
                    "name": "Harbour View Hospital",
                    "location": "Testville",
                    "website_url": "https://harbourview.example/",
                },
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "extraction_required"
    assert "Submit an extraction" in response.json()["detail"]["message"]


def test_crawl_mode_uses_supplied_extraction_evidence_with_provenance(client, seeded) -> None:
    facility = seeded["northgate"]

    response = client.post(
        "/api/v1/comparison",
        json={
            "scope": "standard_12",
            "source": "website_crawl",
            "hospitals": [
                _registered(str(facility.id)),
                {
                    "type": "unregistered",
                    "name": "Harbour View Hospital",
                    "location": "Testville",
                    "website_url": "https://harbourview.example/",
                    "evidence": [
                        {
                            "criterion_key": "location",
                            "value": "4 Harbour Road, Testville",
                            "source": "website_crawl",
                            "source_name": "Hospital website (crawled)",
                            "source_url": "https://harbourview.example/contact",
                            "retrieved_at": "2026-11-13T10:00:00Z",
                            "status": "available",
                            "note": "Extracted from crawled pages.",
                        }
                    ],
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    location = next(row for row in body["rows"] if row["criterion"]["key"] == "location")
    registered_cell, crawled_cell = location["cells"]
    assert registered_cell["source"] == "medcompare_server"
    assert crawled_cell["source"] == "website_crawl"
    assert crawled_cell["source_url"] == "https://harbourview.example/contact"
    assert crawled_cell["retrieved_at"].startswith("2026-11-13")
    assert "Extracted from crawled pages." in crawled_cell["explanation"]
    assert any("legacy client-supplied evidence" in warning for warning in body["warnings"])
    assert "not server-validated crawl-run provenance" in " ".join(body["warnings"])


@pytest.fixture()
def crawl_allergic_client(db_session):
    """App whose crawl runner fails the test if it is ever invoked."""

    async def _runner(_request) -> CrawlOutcome:  # pragma: no cover - must not run
        raise AssertionError("the crawler must not start implicitly")

    application = create_app()
    application.dependency_overrides[get_db_session] = lambda: db_session
    application.dependency_overrides[get_crawl_runner] = lambda: _runner
    return application


def test_comparison_never_starts_a_crawl(crawl_allergic_client, seeded) -> None:
    facility = seeded["northgate"]
    other = seeded["southwind"]
    with TestClient(crawl_allergic_client) as client:
        response = client.post(
            "/api/v1/comparison",
            json={
                "scope": "standard_12",
                "source": "medcompare_server",
                "hospitals": [
                    _registered(str(facility.id)),
                    _registered(str(other.id)),
                ],
            },
        )

    assert response.status_code == 200
