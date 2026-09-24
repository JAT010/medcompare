"""Simulated Government Portal exporter.

Writes a compliance-style submission package for the fictional "National
Hospital Compliance Portal".  Everything happens LOCALLY — nothing is ever
uploaded anywhere.  This is a simulator so regulators, auditors and product
teams can exercise the submission contract (envelope schema, manifest,
integrity hashes) without a real portal.

Privacy & safety rules enforced here:

- No patient data is ever included (the canonical dataset holds only public
  hospital-intelligence fields).
- The package is always stamped ``simulated: true``.
- ``dry_run`` is the default and the exporter can never mark a package
  ``submitted`` — submission is left to an operator + real portal contract
  that does not exist in this repository.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hospital_extractor.exporters.base import (
    DatasetExporter,
    ExportArtifact,
    ExportResult,
    file_sha256,
)
from hospital_extractor.models.dataset import HospitalDataset

__all__ = [
    "GovernmentPortalExporter",
    "GOV_SUBMISSION_NAME",
    "GOV_MANIFEST_NAME",
]

GOV_SUBMISSION_NAME = "submission.json"
GOV_MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = "1.0"


def _hospital_ref(dataset: HospitalDataset) -> str:
    """Best available stable hospital identifier for export packages."""
    if dataset.source is not None and dataset.source.hospital_ref:
        return dataset.source.hospital_ref
    if dataset.hospital is not None and dataset.hospital.name:
        return dataset.hospital.name
    return "hospital"


class GovernmentPortalExporter(DatasetExporter):
    """Dry-run exporter to the (simulated) national compliance portal."""

    exporter_name = "government_portal"

    def export(self, dataset: HospitalDataset, destination: Path | str) -> ExportResult:
        dest = Path(destination)
        dest.mkdir(parents=True, exist_ok=True)

        hospital_ref = _hospital_ref(dataset)
        submission_path = dest / GOV_SUBMISSION_NAME
        submission = self._submission(dataset)
        submission_path.write_text(
            json.dumps(submission, indent=2, default=str),
            encoding="utf-8",
        )
        manifest_path = dest / GOV_MANIFEST_NAME

        artifacts = [
            ExportArtifact(
                path=submission_path,
                format="application/json",
                description="Simulated government portal submission envelope",
                sha256=file_sha256(submission_path),
            ),
        ]
        manifest = self._manifest(artifacts)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, default=str),
            encoding="utf-8",
        )
        artifacts.append(
            ExportArtifact(
                path=manifest_path,
                format="application/json",
                description="Submission manifest with integrity hashes",
                sha256=file_sha256(manifest_path),
            )
        )

        hospital_name = dataset.hospital.name if dataset.hospital is not None else hospital_ref
        summary = (
            f"SIMULATED submission package for {hospital_name} "
            f"({len(artifacts)} artifacts, dry-run, not submitted to any portal)."
        )
        return ExportResult(
            exporter=self.exporter_name,
            destination=submission_path.parent,
            artifacts=tuple(artifacts),
            summary=summary,
        )

    def _submission(self, dataset: HospitalDataset) -> dict[str, Any]:
        source_block: dict[str, Any] = {}
        if dataset.source is not None:
            source_block = {
                "source_type": dataset.source.source_type.value,
                "provider": dataset.source.provider,
                "hospital_ref": dataset.source.hospital_ref,
                "retrieved_at": dataset.source.retrieved_at.isoformat(),
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "exporter": "MedCompare — Hospital Intelligence Platform",
            "target": "National Hospital Compliance Portal (SIMULATED)",
            "simulated": True,
            "dry_run": True,
            "submitted_at": None,
            "created_at": datetime.now(UTC).isoformat(),
            "hospital_id": _hospital_ref(dataset),
            "hospital_name": dataset.hospital.name if dataset.hospital else None,
            "dataset": json.loads(dataset.model_dump_json()),
            "source_summary": source_block,
            "privacy_statement": (
                "No patient data is included. This package contains public "
                "hospital-intelligence facts and their provenance only."
            ),
        }

    def _manifest(self, artifacts: list[ExportArtifact]) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "exporter": self.exporter_name,
            "simulated": True,
            "dry_run": True,
            "created_at": datetime.now(UTC).isoformat(),
            "artifacts": [
                {
                    "file": artifact.path.name,
                    "format": artifact.format,
                    "description": artifact.description,
                    "sha256": artifact.sha256,
                }
                for artifact in artifacts
            ],
        }