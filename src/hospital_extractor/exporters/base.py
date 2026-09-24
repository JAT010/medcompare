"""Exporter interface: canonical datasets to durable artifacts.

The export layer takes a :class:`HospitalDataset` and produces files without
caring where the dataset came from — the same export pipeline serves crawled,
API-fetched, demo and imported data.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from hospital_extractor.models.dataset import HospitalDataset

__all__ = [
    "ExportArtifact",
    "ExportResult",
    "DatasetExporter",
    "file_sha256",
]


def file_sha256(path: Path) -> str:
    """Hex SHA-256 of a file's contents."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ExportArtifact:
    """One file produced by an exporter."""

    path: Path
    format: str
    description: str
    sha256: str


@dataclass(frozen=True)
class ExportResult:
    """Result of running one exporter."""

    exporter: str
    destination: Path
    artifacts: tuple[ExportArtifact, ...] = field(default_factory=tuple)
    summary: str = ""


class DatasetExporter(ABC):
    """Turns one canonical dataset into export artifacts."""

    exporter_name: str = "dataset_exporter"

    @abstractmethod
    def export(self, dataset: HospitalDataset, destination: Path | str) -> ExportResult:
        """Write the dataset to *destination* and return the result."""

    def describe(self) -> str:
        """Human-readable description of what this exporter does."""
        return f"{self.exporter_name} (format: see export() artifacts)"