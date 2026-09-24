"""P18 — End-to-end source-agnostic integration validation.

Proves that the real website path and the local demo-API path both flow
through the SAME canonical :class:`HospitalDataset` -> SQLite -> profile /
comparison stack with YES / NO / UNKNOWN / CONFLICT semantics.

All fixtures are generated locally (in-process HTTP servers + the demo API
app via ASGI transport) — nothing touches the internet.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import http.server
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.comparison.service import compare_urls
from hospital_extractor.extraction import CrawlStats, build_dataset
from hospital_extractor.mockapi.data import mock_hospital_payload
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.metadata import (
    FetchStatus,
    SourceIdentity,
    SourceMetadata,
    SourceType,
)
from hospital_extractor.sources.base import apply_source_provenance
from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource
from hospital_extractor.sources.website import WebsiteDataSource
from hospital_extractor.storage import HospitalDatabase
from hospital_extractor.utils.urls import InvalidURLError

# Home page URL of the standard fixture site (see the conftest ``pages`` fixture).
_BASE_URL = "https://example.test"


def _website_dataset(
    pages: list[tuple[str, str]],
    *,
    name: str = "City Hospital",
    target_url: str = _BASE_URL,
) -> HospitalDataset:
    """Build a canonical website dataset exactly like the real crawler does."""
    stats = CrawlStats(
        pages_discovered=len(pages),
        pages_fetched=len(pages),
        pages_failed=0,
        pages_skipped=0,
    )
    dataset = build_dataset(
        pages, target_url=target_url, crawl_stats=stats
    )
    apply_source_provenance(
        dataset,
        SourceMetadata(
            source_type=SourceType.WEBSITE,
            source_id="website:example.test",
            provider="example.test",
            base_url=target_url,
            hospital_ref=target_url,
            hospital_name=name,
            status=FetchStatus.SUCCESS,
        ),
    )
    return dataset


def _payload_server(payload: dict[str, Any]) -> FastAPI:
    """A tiny in-process FastAPI app that serves one mock-style payload."""

    app = FastAPI()

    @app.get("/demo/hospital/demo-northstar")
    async def hospital() -> dict[str, Any]:
        return payload

    return app


def _mock_dataset(payload: dict[str, Any] | None = None) -> HospitalDataset:
    """Fetch a canonical dataset through the real mock-API adapter (ASGI, offline)."""
    body = payload if payload is not None else mock_hospital_payload("demo-northstar")
    transport = httpx.ASGITransport(app=_payload_server(body))
    client = httpx.AsyncClient(transport=transport, base_url="http://demo")
    source = MockHospitalAPIDataSource(hospital_id="demo-northstar", client=client)
    try:
        return asyncio.run(source.fetch())
    finally:
        asyncio.run(client.aclose())


def _negated_payload() -> dict[str, Any]:
    """demo-northstar payload where Total Knee Replacement is explicitly denied."""
    payload = copy.deepcopy(mock_hospital_payload("demo-northstar"))
    for procedure in payload["procedures"]:
        if procedure["name"] == "Total Knee Replacement":
            procedure["description"] = (
                "Total knee replacement is not available at this demo centre; "
                "knee cases are routed elsewhere."
            )
    return payload


def _no_knee_payload() -> dict[str, Any]:
    """demo-northstar payload with every knee mention removed → UNKNOWN boolean."""
    payload = copy.deepcopy(mock_hospital_payload("demo-northstar"))
    payload["procedures"] = [
        p for p in payload["procedures"] if "knee" not in p["name"].lower()
    ]
    payload["treatments"] = [
        t for t in payload["treatments"] if "knee" not in t["name"].lower()
    ]
    return payload


def _knee_bool_dimension(hospital: Any) -> Any:
    for dimension in hospital.dimensions:
        if dimension.dimension_key == "total_knee_replacement":
            return dimension
    raise AssertionError("total_knee_replacement dimension missing")


class TestWebsiteSourceEndToEnd:
    """Phase 2 — the REAL website source against a local HTTP server."""

    def test_website_source_to_sqlite_round_trip(
        self, tmp_path: Path, pages: list[tuple[str, str]]
    ) -> None:
        site_dir = tmp_path / "site"
        site_dir.mkdir(parents=True)
        _write_site(site_dir, pages)

        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(site_dir)
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            source = WebsiteDataSource(
                identity=SourceIdentity(hospital_ref=url),
                output_dir=tmp_path / "crawl_out",
            )
            started = time.perf_counter()
            dataset = asyncio.run(source.fetch())
            elapsed = time.perf_counter() - started
        finally:
            server.shutdown()
            thread.join(timeout=5)

        print(f"\n[timing] website crawl+extract = {elapsed:.3f}s")
        assert dataset.source is not None
        assert dataset.source.source_type is SourceType.WEBSITE
        assert dataset.source.provider == "127.0.0.1"
        assert dataset.hospital is not None
        assert "City Hospital" in (dataset.hospital.name or "")
        assert dataset.hospital.source_url == url

        with HospitalDatabase(tmp_path / "med.db") as db:
            dataset_id = db.save_dataset(dataset)
            restored = db.load_dataset(dataset_id)
            summary = db.list_datasets()[0]
        assert restored is not None
        assert restored.hospital is not None
        assert restored.hospital.name == dataset.hospital.name
        assert summary.source_type == "website"
        assert summary.record_count > 0

    def test_invalid_url_from_website_source(self, tmp_path: Path) -> None:
        source = WebsiteDataSource(
            identity=SourceIdentity(hospital_ref="not a url"),
            output_dir=tmp_path / "crawl_out",
        )
        with pytest.raises(Exception) as excinfo:
            asyncio.run(source.fetch())
        assert isinstance(excinfo.value.__cause__, InvalidURLError) or isinstance(
            excinfo.value, InvalidURLError
        )


def _write_site(site_dir: Path, pages: list[tuple[str, str]]) -> None:
    for url, html in pages:
        path = urlsplit(url).path
        last = path.rstrip("/").rsplit("/", 1)[-1] or "index"
        (site_dir / f"{last}.html").write_text(html, encoding="utf-8")


class TestMockApiSourceEndToEnd:
    """Phase 3 — demo API through the real adapter → canonical → SQLite."""

    def test_mock_api_to_store_keeps_provenance_and_timestamps(self, tmp_path: Path) -> None:
        dataset = _mock_dataset()
        assert dataset.source is not None
        assert dataset.source.source_type is SourceType.MOCK_API
        assert dataset.source.provider == "Northstar Demo Data Services"
        assert dataset.source.raw_payload_metadata.get("last_updated")

        with HospitalDatabase(tmp_path / "med.db") as db:
            dataset_id = db.save_dataset(dataset)
            restored = db.load_dataset(dataset_id)
            summary = db.list_datasets()[0]

        assert restored is not None
        assert restored.source is not None
        assert restored.source.source_type is SourceType.MOCK_API
        assert restored.source.provider == "Northstar Demo Data Services"
        assert restored.source.raw_payload_metadata.get("last_updated") is not None
        assert restored.hospital is not None
        assert restored.hospital.name == "Northstar Multispeciality Hospital"
        assert summary.source_type == "mock_api"
        assert summary.quality_records > 0

    def test_negated_payload_resolves_to_no(self) -> None:
        dataset = _mock_dataset(_negated_payload())
        result = compare_urls([("h", dataset)], "Total Knee Replacement")
        dimension = _knee_bool_dimension(result.hospitals[0])
        assert dimension.decision == "no"


class TestCanonicalEquivalence:
    """Phase 4 — both sources produce the same canonical shape."""

    def test_website_and_mock_are_equivalent_canonical_datasets(
        self, pages: list[tuple[str, str]]
    ) -> None:
        website = _website_dataset(pages)
        mock = _mock_dataset()

        for dataset in (website, mock):
            assert isinstance(dataset, HospitalDataset)
            assert dataset.hospital is not None
            assert dataset.hospital.name
            assert dataset.source is not None
            assert dataset.source.source_type in (SourceType.WEBSITE, SourceType.MOCK_API)
            # canonical category walk used by storage/comparison
            assert getattr(dataset, "procedures", []) is not None
            assert getattr(dataset, "treatments", []) is not None
            assert getattr(dataset, "doctors", []) is not None

    def test_same_comparison_engine_for_both_sources(
        self, pages: list[tuple[str, str]]
    ) -> None:
        """Phase 5 — one engine, identical dimension labels for either source."""
        website = _website_dataset(pages)
        mock = _mock_dataset()
        result = compare_urls(
            [("web", website), ("api", mock)],
            "Total Knee Replacement",
        )
        assert len(result.hospitals) == 2
        first_labels = {d.label for d in result.hospitals[0].dimensions}
        second_labels = {d.label for d in result.hospitals[1].dimensions}
        assert first_labels == second_labels


class TestCrossSourceConflict:
    """Phase 6 — YES + NO across sources → CONFLICT, never a silent winner."""

    def test_yes_and_no_yields_conflict_without_winner(
        self, pages: list[tuple[str, str]]
    ) -> None:
        website = _website_dataset(pages)  # YES (catalogue lists it)
        mock = _mock_dataset(_negated_payload())  # NO (explicit denial)
        result = compare_urls(
            [("city-hospital", website), ("city-hospital", mock)],
            "Total Knee Replacement",
        )
        assert len(result.hospitals) == 1
        hospital = result.hospitals[0]
        assert hospital.source_types == ["website", "mock_api"]
        assert hospital.providers == ["example.test", "Northstar Demo Data Services"]

        dimension = _knee_bool_dimension(hospital)
        assert dimension.decision == "unknown"  # no winner picked
        assert dimension.conflicts
        conflict = dimension.conflicts[0]
        assert conflict.dimension_key == "total_knee_replacement"
        assert {fact.decision for fact in conflict.facts} == {"yes", "no"}

    def test_yes_and_unknown_is_not_a_conflict(self, pages: list[tuple[str, str]]) -> None:
        website = _website_dataset(pages)  # YES
        mock = _mock_dataset(_no_knee_payload())  # UNKNOWN for the knee boolean
        result = compare_urls(
            [("city-hospital", website), ("city-hospital", mock)],
            "Total Knee Replacement",
        )
        hospital = result.hospitals[0]
        dimension = _knee_bool_dimension(hospital)
        assert dimension.decision == "yes"
        assert not dimension.conflicts

    def test_no_and_unknown_is_not_a_conflict(self, pages: list[tuple[str, str]]) -> None:
        mock = _mock_dataset(_negated_payload())  # NO
        home_only = _website_dataset(pages[:1])  # UNKNOWN (home page only)
        result = compare_urls(
            [("city-hospital", home_only), ("city-hospital", mock)],
            "Total Knee Replacement",
        )
        hospital = result.hospitals[0]
        dimension = _knee_bool_dimension(hospital)
        assert dimension.decision == "no"
        assert not dimension.conflicts


class TestProfileLayer:
    """Phase 8 — profiles expose every source of one hospital, with metrics."""

    def test_available_sources_and_demo_banner(
        self, tmp_path: Path, pages: list[tuple[str, str]]
    ) -> None:
        website = _website_dataset(pages)
        mock = _mock_dataset()
        mock.hospital.name = "City Hospital"  # same identity, different source

        db_path = tmp_path / "med.db"
        with HospitalDatabase(db_path) as db:
            website_id = db.save_dataset(website)
            mock_id = db.save_dataset(mock)
            key = db.hospital_key_for(mock)
            group = db.list_hospital_sources(key)
            assert len(group) == 2

        app = create_app()
        app.state.medcompare_db_path = db_path
        with TestClient(app) as client:
            listing = client.get("/api/v1/hospitals", params={"hospital_key": key})
            assert listing.status_code == 200
            types = {d["source_type"] for d in listing.json()["datasets"]}
            assert types == {"website", "mock_api"}

            profile_html = client.get(f"/hospitals/{mock_id}").text
            assert "Other sources for this hospital" in profile_html
            assert "SIMULATED / DEMO" in profile_html
            assert f"/hospitals/{website_id}" in profile_html

            body = client.get(f"/api/v1/hospitals/{mock_id}").json()["dataset"]
            assert body["source"]["raw_payload_metadata"]["last_updated"]
            assert body["quality"]["records_extracted"] > 0


class TestComparisonUi:
    """Phase 9 — the web UI renders a cross-source CONFLICT honestly."""

    def test_conflict_renders_conlict_badge_and_both_sources(
        self, tmp_path: Path, pages: list[tuple[str, str]]
    ) -> None:
        website = _website_dataset(pages)
        mock = _mock_dataset(_negated_payload())

        app = create_app()
        app.state.medcompare_db_path = tmp_path / "med.db"
        with TestClient(app) as client, patch(
            "hospital_extractor.api.routes._extract_all",
            new_callable=AsyncMock,
        ) as extract:
            extract.return_value = [
                ("city-hospital", website),
                ("city-hospital", mock),
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
        assert "CONFLICT" in html
        assert "Conflicting evidence" in html
        assert 'source-badge source-website' in html
        assert 'source-badge source-mock_api' in html


class TestIntegrationDemoHarness:
    """Phase 11 — the ``integration-demo`` CLI subcommand (fully local)."""

    def test_harness_runs_end_to_end(self, tmp_path: Path, capsys) -> None:
        from hospital_extractor.cli import _run_integration_demo

        code = asyncio.run(
            _run_integration_demo(
                [
                    "--db",
                    str(tmp_path / "demo.db"),
                    "--hospitals",
                    "demo-northstar",
                    "demo-solstice",
                ]
            )
        )
        assert code == 0
        captured = capsys.readouterr().out
        assert "integration-demo" in captured
        assert "procedure_available" in captured

        with HospitalDatabase(tmp_path / "demo.db") as db:
            refs = {s.hospital_ref for s in db.list_datasets()}
        assert refs == {"demo-northstar", "demo-solstice"}


class TestApiInvalidUrlStill400:
    """Regression: the API keeps answering 400 for an invalid URL through harvest."""

    def test_invalid_url_returns_400(self) -> None:
        app = create_app()
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/compare",
                json={
                    "urls": ["not a url", "also not a url"],
                    "procedure": "Knee Replacement",
                },
            )
        assert response.status_code == 400
        assert "Invalid URL" in response.json()["error"]