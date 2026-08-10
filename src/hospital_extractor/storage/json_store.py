"""JSON persistence for the intermediate dataset.

``HospitalDataset`` is the primary internal model; the JSON document written
here is its canonical serialization (hospital, entities, sources, quality,
conflicts). Excel/CSV export are derived from it — this store is never derived
from the exports.
"""

from __future__ import annotations

import json
from pathlib import Path

from hospital_extractor.models.dataset import HospitalDataset

__all__ = ["write_dataset", "read_dataset"]

JSON_INDENT = 2


def write_dataset(dataset: HospitalDataset, path: Path | str) -> Path:
    """Serialize *dataset* to JSON at *path* and return the file path."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(dataset.model_dump_json())
    output.write_text(json.dumps(payload, indent=JSON_INDENT, ensure_ascii=False), encoding="utf-8")
    return output


def read_dataset(path: Path | str) -> HospitalDataset:
    """Load a :class:`HospitalDataset` previously written by :func:`write_dataset`."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return HospitalDataset.model_validate(payload)
