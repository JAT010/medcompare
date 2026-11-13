"""Tests for the explicit website-crawl endpoint (evidence source C)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api import create_app
from hospital_extractor.api.deps import get_db_session
from hospital_extractor.api.routers.crawl import CrawlOutcome, CrawlRequest, get_crawl_runner
from hospital_extractor.models.intake import IntakeEvidence, MasterIntake

URL = "https://harbourview.example/"
PUBLIC_TEST_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def fake_public_dns(monkeypatch) -> None:
    """Resolve every host to a public address so tests never hit live DNS.

    Structural (SSRF) checks still run first, so unsafe URLs are rejected
    before this resolver is ever consulted.
    """
    from hospital_extractor.crawler import ssrf

    monkeypatch.setattr(ssrf, "default_resolver", lambda host: [PUBLIC_TEST_IP])


def _intake() -> MasterIntake:
    """Controlled extraction result used instead of any real hospital record."""
    intake = MasterIntake(
        hospital_name="Harbour View Hospital",
        area_locality="Harbour Side",
        city="Testville",
        pin_code="400001",
        total_beds=180,
        icu_beds=24,
    )
    intake.booleans["cardiac_sciences_enabled"] = "yes"
    intake.evidence["cardiac_sciences_enabled"] = [
        IntakeEvidence.yes(
            "cardiac_sciences_enabled",
            "The department runs cardiac sciences.",
            "https://harbourview.example/cardiac",
            "pattern-match",
            1.0,
            "cardiac_sciences_enabled",
        )
    ]
    intake.booleans["heart_transplant"] = "unknown"
    intake.evidence["heart_transplant"] = []
    return intake


class RecordingRunner:
    """Crawl runner double that records calls and returns a fixed outcome."""

    def __init__(self, outcome: CrawlOutcome | None = None, delay: float = 0.0) -> None:
        self.calls: list[CrawlRequest] = []
        self._outcome = outcome or CrawlOutcome(
            target_url=URL,
            pages_fetched=6,
            hospital_name="Harbour View Hospital",
            intake=_intake(),
        )
        self._delay = delay

    async def __call__(self, request: CrawlRequest) -> CrawlOutcome:
        self.calls.append(request)
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._outcome


@pytest.fixture()
def runner() -> RecordingRunner:
    return RecordingRunner()


@pytest.fixture()
def crawl_client(db_session, runner):
    application = create_app()
    application.dependency_overrides[get_db_session] = lambda: db_session
    application.dependency_overrides[get_crawl_runner] = lambda: runner
    with TestClient(application) as client:
        yield client
    application.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("url", "expected_error"),
    [
        ("http://localhost:3000/", "url_not_allowed"),
        ("http://127.0.0.1/", "url_not_allowed"),
        ("http://169.254.169.254/latest/meta-data/", "url_not_allowed"),
        ("http://192.168.1.10/", "url_not_allowed"),
        ("http://10.0.0.8/", "url_not_allowed"),
        ("https://user:pass@harbourview.example/", "url_not_allowed"),
        ("ftp://harbourview.example/", "invalid_url"),
        ("not-a-url", "invalid_url"),
    ],
)
def test_crawl_rejects_unsafe_or_invalid_urls(crawl_client, runner, url, expected_error) -> None:
    response = crawl_client.post("/api/v1/crawl/extract", json={"url": url})

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == expected_error
    assert runner.calls == []


def test_crawl_dns_resolution_blocks_internal_host(crawl_client, runner, monkeypatch) -> None:
    """A public-looking name that resolves internally must still be rejected."""
    from hospital_extractor.crawler import ssrf

    monkeypatch.setattr(ssrf, "default_resolver", lambda host: ["10.1.2.3"])

    response = crawl_client.post("/api/v1/crawl/extract", json={"url": URL})

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "url_not_allowed"
    assert runner.calls == []


def test_crawl_requires_a_url(crawl_client, runner) -> None:
    response = crawl_client.post("/api/v1/crawl/extract", json={})

    assert response.status_code == 422
    assert runner.calls == []


def test_crawl_success_reports_evidence_and_synchronous_execution(
    crawl_client, runner, seeded
) -> None:
    response = crawl_client.post("/api/v1/crawl/extract", json={"url": URL})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["execution"] == "synchronous"
    assert body["job_store_available"] is False
    assert body["target_url"] == URL
    assert body["pages_fetched"] == 6
    assert body["hospital_name"] == "Harbour View Hospital"
    assert any(
        "persistent crawl job store" in note or "crawl_run_id" in note
        for note in body["warnings"]
    )
    assert body["crawl_run_id"]
    assert body["crawl_status"] in {"completed", "completed_with_warnings"}
    assert body["evidence_count"] == len(body["evidence"])

    by_criterion = {item["criterion_key"]: item for item in body["evidence"]}
    assert by_criterion["location"]["value"] == "Harbour Side, Testville, 400001"
    assert by_criterion["location"]["source"] == "website_crawl"
    assert by_criterion["location"]["source_url"] == URL
    assert by_criterion["location"]["retrieved_at"]
    assert "Total beds: 180" in by_criterion["infrastructure"]["value"]
    assert "Cardiac Sciences Enabled" in by_criterion["department_expertise"]["value"]
    assert by_criterion["department_expertise"]["source_url"] == "https://harbourview.example/cardiac"

    assert "affordability" in body["criteria_without_evidence"]
    assert "patient_reviews" in body["criteria_without_evidence"]
    assert "insurance_compatibility" in body["criteria_without_evidence"]


def test_crawl_associates_extraction_with_a_registered_facility(
    crawl_client, runner, seeded
) -> None:
    facility = seeded["northgate"]

    response = crawl_client.post(
        "/api/v1/crawl/extract",
        json={"url": URL, "facility_id": str(facility.id)},
    )

    assert response.status_code == 200
    assert len(runner.calls) == 1


def test_crawl_unknown_facility_returns_404(crawl_client, runner, seeded) -> None:
    response = crawl_client.post(
        "/api/v1/crawl/extract",
        json={"url": URL, "facility_id": str(uuid.uuid4())},
    )

    assert response.status_code == 404
    assert runner.calls == []


def test_crawl_timeout_names_the_missing_job_store(db_session, runner, monkeypatch) -> None:
    monkeypatch.setenv("MEDCOMPARE_CRAWL_TIMEOUT_SECONDS", "1")
    slow = RecordingRunner(delay=2.0)
    application = create_app()
    application.dependency_overrides[get_db_session] = lambda: db_session
    application.dependency_overrides[get_crawl_runner] = lambda: slow

    with TestClient(application) as client:
        response = client.post("/api/v1/crawl/extract", json={"url": URL})

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert detail["error"] == "crawl_exceeded_request_window"
    assert "persistent job store" in detail["required_integration"]
    assert "queue work" in detail["message"]
    assert detail["crawl_run_id"]
