"""End-to-end demo-mode tests over two fictional demo hospitals.

Two demo hospitals are served by the in-process simulated API:
``demo-northstar`` (broad multi-speciality) and ``demo-solstice`` (narrow
ortho-cardiac institute).  Verifies:

- both can be harvested through the mock source adapter,
- a comparison spans both hospitals with correct provenance stamps,
- a procedure offered by one but absent from the other is NEVER reported as
  ``no`` (absence never becomes a negative claim), and
- the demo banner text is clearly labelled as simulated.
"""

from __future__ import annotations

import anyio
import httpx
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.comparison.service import compare_urls
from hospital_extractor.mockapi.app import create_mock_app
from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource


def _demo_dataset(hospital_id: str):
    transport = httpx.ASGITransport(app=create_mock_app())
    source = MockHospitalAPIDataSource(
        hospital_id=hospital_id,
        client=httpx.AsyncClient(transport=transport, base_url="http://testserver"),
    )
    return anyio.run(source.fetch)


def _fetch_all(hospital_ids: list[str]) -> list[tuple[str, object]]:
    results: list[tuple[str, object]] = []
    for hospital_id in hospital_ids:
        results.append((hospital_id, _demo_dataset(hospital_id)))
    return results


class TestTwoDemoHospitals:
    def test_both_demo_hospitals_harvestable(self) -> None:
        datasets = _fetch_all(["demo-northstar", "demo-solstice"])
        assert len(datasets) == 2
        for ref, dataset in datasets:
            assert dataset.source is not None
            assert dataset.source.hospital_ref == ref
            assert dataset.source.source_type.value == "mock_api"
            assert dataset.hospital is not None
            assert dataset.hospital.name

    def test_offer_shared_procedure(self) -> None:
        datasets = _fetch_all(["demo-northstar", "demo-solstice"])
        result = compare_urls(datasets, "Total Knee Replacement")
        assert len(result.hospitals) == 2
        by_ref = {h.hospital_id: h for h in result.hospitals}
        assert by_ref["demo-northstar"].source_type == "mock_api"
        assert by_ref["demo-solstice"].source_type == "mock_api"
        for hospital in result.hospitals:
            knee = next(
                d for d in hospital.dimensions if d.dimension_key == "total_knee_replacement"
            )
            assert knee.decision != "no"

    def test_absence_never_reports_no(self) -> None:
        datasets = _fetch_all(["demo-northstar", "demo-solstice"])
        result = compare_urls(datasets, "Heart Valve Repair")
        by_ref = {h.hospital_id: h for h in result.hospitals}
        northstar = by_ref["demo-northstar"]
        solstice = by_ref["demo-solstice"]
        north_dim = next(
            d for d in northstar.dimensions if d.dimension_key == "heart_valve_repair"
        )
        sol_dim = next(
            d for d in solstice.dimensions if d.dimension_key == "heart_valve_repair"
        )
        assert north_dim.decision == "yes"
        assert sol_dim.decision != "no"
        assert sol_dim.decision in {"yes", "unknown"}


class TestDemoUi:
    def test_index_marks_demo_data_as_simulated(self) -> None:
        with TestClient(create_app()) as client:
            page = client.get("/")
            assert page.status_code == 200
            assert "simulated" in page.text.lower()
            assert "demo-northstar" in page.text
            assert "demo-solstice" in page.text