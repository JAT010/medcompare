"""Comparison provenance + conflict-surface tests.

Verifies record-level source provenance survives into comparison evidence and
that contradictory evidence across sources is surfaced as a conflict — never
silently resolved.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from hospital_extractor.comparison.dimensions import detect_dimension_conflicts
from hospital_extractor.comparison.models import (
    ComparisonEvidence,
    DimensionResult,
    SourceConflict,
)
from hospital_extractor.comparison.service import compare_urls
from hospital_extractor.mockapi.app import create_mock_app
from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource


def _demo_dataset() -> Any:
    transport = httpx.ASGITransport(app=create_mock_app())
    source = MockHospitalAPIDataSource(
        hospital_id="demo-northstar",
        client=httpx.AsyncClient(transport=transport, base_url="http://testserver"),
    )
    return asyncio.run(source.fetch())


class TestProvenanceSurvivesComparison:
    def test_evidence_carries_source_type(self) -> None:
        dataset = _demo_dataset()
        dataset.hospital.source_type = "mock_api"
        result = compare_urls([("demo-northstar", dataset)], "Total Knee Replacement")
        hospital = result.hospitals[0]
        source_types = {
            ev.source_type
            for dim in hospital.dimensions
            for ev in dim.evidence
            if ev.source_type
        }
        assert "mock_api" in source_types

    def test_boolean_dimension_evidence_source(self) -> None:
        dataset = _demo_dataset()
        result = compare_urls([("demo-northstar", dataset)], "Total Knee Replacement")
        total_knee = next(
            (
                d
                for d in result.hospitals[0].dimensions
                if d.dimension_key == "total_knee_replacement"
            ),
            None,
        )
        assert total_knee is not None
        assert all(ev.source_type for ev in total_knee.evidence)


class TestConflictDetection:
    def test_mixed_yes_no_evidence_yields_conflict_without_winner(self) -> None:
        dimension = DimensionResult(
            dimension_key="procedure_available",
            label="Procedure Available",
            decision="yes",
            evidence=[
                ComparisonEvidence(
                    hospital_id="h",
                    dimension_key="procedure_available",
                    decision="yes",
                    text="Listed in procedures.",
                    source_url="https://a.test/p",
                    method="api-json",
                    source_type="mock_api",
                ),
                ComparisonEvidence(
                    hospital_id="h",
                    dimension_key="procedure_available",
                    decision="no",
                    text="Explicitly states not offered.",
                    source_url="https://b.test/p",
                    method="api-json",
                    source_type="hospital_api",
                ),
            ],
        )
        conflicts = detect_dimension_conflicts([dimension])
        assert len(conflicts) == 1
        conflict = conflicts[0]
        assert isinstance(conflict, SourceConflict)
        assert conflict.dimension_key == "procedure_available"
        assert len(conflict.facts) == 2
        decisions = {fact.decision for fact in conflict.facts}
        assert decisions == {"yes", "no"}

    def test_yes_and_unknown_evidence_is_not_a_conflict(self) -> None:
        dimension = DimensionResult(
            dimension_key="accreditations",
            label="Accreditations",
            decision="yes",
            evidence=[
                ComparisonEvidence(
                    hospital_id="h",
                    dimension_key="accreditations",
                    decision="yes",
                    text="NABH 2015.",
                    source_url="https://a.test",
                    method="api-json",
                    source_type="website",
                ),
                ComparisonEvidence(
                    hospital_id="h",
                    dimension_key="accreditations",
                    decision="unknown",
                    text="No accreditation info disclosed.",
                    source_url="https://b.test",
                    method="api-json",
                    source_type="website",
                ),
            ],
        )
        assert detect_dimension_conflicts([dimension]) == []

    def test_empty_and_placeholder_evidence_yields_no_conflict(self) -> None:
        from hospital_extractor.comparison.models import NO_EVIDENCE_MESSAGE

        dimension = DimensionResult(
            dimension_key="x",
            label="X",
            decision="unknown",
            evidence=[
                ComparisonEvidence(
                    hospital_id="h",
                    dimension_key="x",
                    decision="unknown",
                    text=NO_EVIDENCE_MESSAGE,
                    source_url="",
                    method="comparison-engine",
                )
            ],
        )
        assert detect_dimension_conflicts([dimension]) == []