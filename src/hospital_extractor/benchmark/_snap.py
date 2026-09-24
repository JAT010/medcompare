"""Snapshot helpers for benchmark fixtures (avoids circular imports)."""

from __future__ import annotations

from datetime import UTC, datetime

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.snapshot import DatasetSnapshot
from hospital_extractor.snapshots.fingerprint import canonical_digest

__all__ = ["make_snapshot"]


def make_snapshot(dataset: HospitalDataset, snapshot_id: str) -> DatasetSnapshot:
    url = dataset.source.base_url if dataset.source else "https://fixture.example"
    return DatasetSnapshot(
        snapshot_id=snapshot_id,
        hospital_id="h_fixture",
        source_type="website",
        source_id=dataset.source.source_id if dataset.source else "website:fixture",
        hospital_ref=url,
        hospital_name=dataset.hospital.name if dataset.hospital else None,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_hash=canonical_digest(dataset),
        dataset=dataset,
    )
