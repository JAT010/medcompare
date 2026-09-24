"""Tests for the export layer (government portal simulator included)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from hospital_extractor.exporters import (
    GovernmentPortalExporter,
    file_sha256,
)
from hospital_extractor.mockapi.app import create_mock_app
from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource


def _demo_dataset() -> object:
    transport = httpx.ASGITransport(app=create_mock_app())
    source = MockHospitalAPIDataSource(
        hospital_id="demo-northstar",
        client=httpx.AsyncClient(transport=transport, base_url="http://testserver"),
    )
    return asyncio.run(source.fetch())


class TestGovernmentPortalExporter:
    def test_writes_submission_and_manifest(self, tmp_path: Path) -> None:
        dataset = _demo_dataset()
        result = GovernmentPortalExporter().export(dataset, tmp_path / "gov")
        files = [artifact.path.name for artifact in result.artifacts]
        assert "submission.json" in files
        assert "manifest.json" in files
        assert result.summary.startswith("SIMULATED submission package")

    def test_submission_is_always_simulated_dry_run(self, tmp_path: Path) -> None:
        dataset = _demo_dataset()
        result = GovernmentPortalExporter().export(dataset, tmp_path / "gov")
        submission_path = next(
            artifact.path
            for artifact in result.artifacts
            if artifact.path.name == "submission.json"
        )
        submission = json.loads(submission_path.read_text(encoding="utf-8"))
        assert submission["simulated"] is True
        assert submission["dry_run"] is True
        assert submission["submitted_at"] is None
        assert "No patient data" in submission["privacy_statement"]
        assert submission["dataset"]["hospital"]["name"] == "Northstar Multispeciality Hospital"

    def test_manifest_hashes_match_files(self, tmp_path: Path) -> None:
        dataset = _demo_dataset()
        result = GovernmentPortalExporter().export(dataset, tmp_path / "gov")
        manifest_path = next(
            artifact.path for artifact in result.artifacts if artifact.path.name == "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["simulated"] is True
        for entry in manifest["artifacts"]:
            file_path = manifest_path.parent / entry["file"]
            assert file_sha256(file_path) == entry["sha256"]

    def test_submission_includes_source_provenance(self, tmp_path: Path) -> None:
        dataset = _demo_dataset()
        result = GovernmentPortalExporter().export(dataset, tmp_path / "gov")
        submission_path = next(
            artifact.path
            for artifact in result.artifacts
            if artifact.path.name == "submission.json"
        )
        submission = json.loads(submission_path.read_text(encoding="utf-8"))
        assert submission["source_summary"]["source_type"] == "mock_api"
        assert submission["source_summary"]["provider"]