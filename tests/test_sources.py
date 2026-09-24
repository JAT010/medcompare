"""Source-adapter tests: canonical boundary, provenance, error handling.

All tests are offline — the demo adapter talks to the real demo FastAPI app
through an ASGI transport, and the authorized-API adapter uses a mocked
transport.  No network access is ever attempted.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from hospital_extractor.mockapi.app import create_mock_app
from hospital_extractor.sources.api_mapping import (
    MalformedPayloadError,
    map_api_payload_to_dataset,
)
from hospital_extractor.sources.base import SourceFetchError
from hospital_extractor.sources.hospital_api import HospitalAPIDataSource
from hospital_extractor.sources.json_feed import JSONFeedDataSource
from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource
from hospital_extractor.sources.models import SourceType
from hospital_extractor.sources.pipeline import HarvestError, harvest
from hospital_extractor.sources.registry import (
    SourceRegistry,
    build_sources_from_config,
    create_source,
    get_default_registry,
    load_sources_config,
)
from hospital_extractor.storage import write_dataset

DEMO_JSON = {
    "hospital": {"hospital_id": "h1", "name": "Demo Hospital"},
    "departments": [],
    "specialities": [{"name": "Cardiology"}],
    "doctors": [
        {"name": "Dr. A", "speciality": "Cardiology", "experience_years": 12}
    ],
    "procedures": [
        {"name": "Angioplasty", "speciality": "Cardiology", "description": "Stent"}
    ],
    "accreditations": [{"name": "NABH", "organization": "NABH", "year": 2019}],
}


def _mock_client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_mock_app())
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def demo_client() -> httpx.AsyncClient:
    return _mock_client()


class TestSourceModels:
    def test_source_type_labels(self) -> None:
        assert SourceType.WEBSITE.label == "Website"
        assert SourceType.MOCK_API.label == "Demo API"
        assert SourceType.HOSPITAL_API.label == "Hospital API"
        assert SourceType.JSON_FEED.label == "Imported Data"

    def test_source_type_values_roundtrip(self) -> None:
        assert SourceType("mock_api") is SourceType.MOCK_API


class TestMockAdapter:
    def test_fetch_returns_canonical_dataset(self, demo_client: httpx.AsyncClient) -> None:
        source = MockHospitalAPIDataSource(
            hospital_id="demo-northstar", client=demo_client
        )
        task = asyncio.run(source.fetch())
        assert task.hospital is not None
        assert "Northstar" in (task.hospital.name or "")
        assert task.hospital.bed_count == 750
        assert task.procedures
        assert any(p.name == "Total Knee Replacement" for p in task.procedures)

    def test_provenance_stamped_on_dataset_and_records(
        self, demo_client: httpx.AsyncClient
    ) -> None:
        ds = asyncio.run(
            MockHospitalAPIDataSource(
                hospital_id="demo-northstar", client=demo_client
            ).fetch()
        )
        assert ds.source is not None
        assert ds.source.source_type is SourceType.MOCK_API
        assert "SIMULATED/MOCK" in (ds.source.notes or "")
        assert ds.doctors[0].source_type == "mock_api"
        assert ds.source.base_url is not None
        assert "demo" in ds.source.base_url
        assert all(
            record.source_type == "mock_api"
            for record in ds.doctors + ds.specialities + ds.procedures
        )

    def test_fetch_unknown_hospital_raises_source_error(self) -> None:
        source = MockHospitalAPIDataSource(hospital_id="nope", client=_mock_client())
        with pytest.raises(SourceFetchError):
            asyncio.run(source.fetch())

    def test_fetch_malformed_response_raises_source_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"not": "a valid payload"})

        transport = httpx.MockTransport(handler)
        source = MockHospitalAPIDataSource(
            hospital_id="demo-northstar",
            client=httpx.AsyncClient(transport=transport, base_url="http://x"),
        )
        with pytest.raises(SourceFetchError):
            asyncio.run(source.fetch())

    def test_fetch_non_json_response_raises_source_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        transport = httpx.MockTransport(handler)
        source = MockHospitalAPIDataSource(
            hospital_id="demo-northstar",
            client=httpx.AsyncClient(transport=transport, base_url="http://x"),
        )
        with pytest.raises(SourceFetchError):
            asyncio.run(source.fetch())

    def test_fetch_http_error_raises_source_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        source = MockHospitalAPIDataSource(
            hospital_id="demo-northstar",
            client=httpx.AsyncClient(transport=transport, base_url="http://x"),
        )
        with pytest.raises(SourceFetchError):
            asyncio.run(source.fetch())


class TestHospitalAPIAdapter:
    @staticmethod
    def _ok_source(payload: dict[str, object] | None = None) -> HospitalAPIDataSource:
        body = payload if payload is not None else DEMO_JSON

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        transport = httpx.MockTransport(handler)
        return HospitalAPIDataSource(
            hospital_ref="h1",
            base_url="https://api.example.in",
            provider="Example Hospital",
            client=httpx.AsyncClient(transport=transport, base_url="https://api.example.in"),
        )

    def test_fetch_maps_authorized_payload(self) -> None:
        ds = asyncio.run(self._ok_source().fetch())
        assert ds.hospital is not None
        assert ds.hospital.name == "Demo Hospital"
        assert ds.source is not None
        assert ds.source.source_type is SourceType.HOSPITAL_API
        assert ds.source.provider == "Example Hospital"
        assert (ds.doctors[0].experience or "").endswith("years")

    def test_fetch_404_raises_source_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        transport = httpx.MockTransport(handler)
        source = HospitalAPIDataSource(
            hospital_ref="h1",
            base_url="https://api.example.in",
            client=httpx.AsyncClient(transport=transport, base_url="https://api.example.in"),
        )
        with pytest.raises(SourceFetchError):
            asyncio.run(source.fetch())

    def test_malformed_payload_raises_mapping_error_as_source_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": True})

        transport = httpx.MockTransport(handler)
        source = HospitalAPIDataSource(
            hospital_ref="h1",
            base_url="https://api.example.in",
            client=httpx.AsyncClient(transport=transport, base_url="https://api.example.in"),
        )
        with pytest.raises(SourceFetchError):
            asyncio.run(source.fetch())


class TestJSONFeedAdapter:
    def test_reads_persisted_canonical_dataset_preserves_provenance(self, tmp_path: Path) -> None:
        source = MockHospitalAPIDataSource(hospital_id="demo-northstar", client=_mock_client())
        original = asyncio.run(source.fetch())
        feed_path = write_dataset(original, tmp_path / "feed.json")
        assert feed_path.is_file()

        fetched = asyncio.run(JSONFeedDataSource(str(feed_path)).fetch())
        assert fetched.source is not None
        # Provenance must survive re-import: the feed records the acquisition
        # but never overwrites the original capture's source metadata.
        assert fetched.source.source_type is SourceType.MOCK_API
        assert fetched.source.raw_payload_metadata.get("imported_via") == "json_feed"
        assert fetched.hospital is not None
        assert fetched.hospital.name == original.hospital.name
        assert [p.name for p in fetched.procedures] == [p.name for p in original.procedures]
        assert fetched.doctors[0].source_type == "mock_api"

    def test_missing_feed_raises_source_error(self, tmp_path: Path) -> None:
        with pytest.raises(SourceFetchError):
            asyncio.run(JSONFeedDataSource(str(tmp_path / "missing.json")).fetch())


class TestMapping:
    def test_map_payload_to_dataset_builds_quality(self) -> None:
        ds = map_api_payload_to_dataset(
            DEMO_JSON,
            endpoint="http://test/#/hospital/h1",
            source_type=SourceType.HOSPITAL_API,
            provider="Provider",
        )
        assert ds.quality.records_extracted >= 1
        assert "Specialities" in ds.quality.records_by_category
        assert ds.quality.records_by_category["Specialities"] == 1

    def test_map_payload_rejects_missing_hospital(self) -> None:
        with pytest.raises(MalformedPayloadError):
            map_api_payload_to_dataset(
                {"departments": []},
                endpoint="http://test",
                source_type=SourceType.HOSPITAL_API,
            )


class TestProvenanceApplication:
    def test_apply_source_provenance_stamps_all_records(self) -> None:
        ds = map_api_payload_to_dataset(
            DEMO_JSON,
            endpoint="http://test",
            source_type=SourceType.HOSPITAL_API,
        )
        assert ds.sources, "provenance worksheet should be rebuilt"
        for record in ds.doctors:
            assert record.source_type == "hospital_api"


class TestRegistry:
    def test_default_registry_registers_all_sources(self) -> None:
        registry = get_default_registry()
        assert registry.source_types() == ["hospital_api", "json_feed", "mock_api", "website"]

    def test_create_source_builds_each_adapter_type(self) -> None:
        assert isinstance(create_source("website", hospital_ref="https://x.in"), object)
        assert isinstance(
            create_source("mock_api", hospital_ref="demo-northstar"),
            MockHospitalAPIDataSource,
        )
        assert isinstance(
            create_source("json_feed", hospital_ref="data/x.json"),
            JSONFeedDataSource,
        )
        assert isinstance(
            create_source("hospital_api", hospital_ref="h1", base_url="https://x"),
            HospitalAPIDataSource,
        )

    def test_create_source_unknown_type_raises(self) -> None:
        with pytest.raises(KeyError):
            create_source("not-a-source", hospital_ref="x")

    def test_load_sources_config_from_toml(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "sources.toml"
        toml_path.write_text(
            "[[sources]]\n"
            'name = "demo"\n'
            'source_type = "mock_api"\n'
            'hospital_ref = "demo-northstar"\n',
            encoding="utf-8",
        )
        configs = load_sources_config(toml_path)
        assert len(configs) == 1
        assert configs[0].source_type == "mock_api"

    def test_absent_config_is_empty(self, tmp_path: Path) -> None:
        assert load_sources_config(tmp_path / "none.toml") == []

    def test_build_sources_from_config_uses_registry(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "sources.toml"
        toml_path.write_text(
            "[[sources]]\n"
            'name = "demo"\n'
            'source_type = "mock_api"\n'
            'hospital_ref = "demo-northstar"\n',
            encoding="utf-8",
        )
        registry = SourceRegistry()

        def factory(hospital_ref: str, **_: object) -> MockHospitalAPIDataSource:
            return MockHospitalAPIDataSource(
                hospital_id=hospital_ref,
                client=_mock_client(),
            )

        registry.register(SourceType.MOCK_API, factory)
        built = build_sources_from_config(registry, toml_path)
        assert "demo" in built
        assert isinstance(built["demo"], MockHospitalAPIDataSource)


class TestHarvestPipeline:
    def test_harvest_through_registered_mock_adapter(self) -> None:
        registry = SourceRegistry()

        def factory(hospital_ref: str, **_: object) -> MockHospitalAPIDataSource:
            return MockHospitalAPIDataSource(
                hospital_id=hospital_ref,
                client=_mock_client(),
            )

        registry.register(SourceType.MOCK_API, factory)
        result = asyncio.run(
            harvest(
                ["demo-northstar"],
                source_type="mock_api",
                registry=registry,
            )
        )
        assert len(result) == 1
        assert result[0].dataset.hospital is not None

    def test_harvest_raises_aggregate_error_on_failure(self) -> None:
        registry = SourceRegistry()

        class BoomSource(MockHospitalAPIDataSource):
            async def fetch(self) -> object:
                raise SourceFetchError("boom")

        registry.register(SourceType.MOCK_API, lambda **k: BoomSource(hospital_id="x"))
        with pytest.raises(HarvestError, match="boom"):
            asyncio.run(harvest(["x"], source_type="mock_api", registry=registry))


class TestDemoAPIEndpoints:
    def test_health(self, demo_client: httpx.AsyncClient) -> None:
        response = asyncio.run(demo_client.get("/demo/health"))
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_full_payload_shape(self, demo_client: httpx.AsyncClient) -> None:
        response = asyncio.run(demo_client.get("/demo/hospital/demo-northstar"))
        assert response.status_code == 200
        body = response.json()
        assert body["hospital"]["hospital_id"] == "demo-northstar"
        assert body["source"]["provider"]
        assert body["procedures"][0]["name"] == "Total Knee Replacement"

    def test_section_returns_items(self, demo_client: httpx.AsyncClient) -> None:
        response = asyncio.run(
            demo_client.get("/demo/hospital/demo-northstar/procedures")
        )
        assert response.status_code == 200
        body = response.json()
        assert body["section"] == "procedures"
        assert body["items"][0]["name"] == "Total Knee Replacement"