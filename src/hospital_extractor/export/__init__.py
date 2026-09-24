"""Export layer: the final 25-field master CSV/XLSX outputs."""

from __future__ import annotations

import re

from hospital_extractor.export.master_csv import export_master_csv
from hospital_extractor.export.master_excel import export_master_excel
from hospital_extractor.models.dataset import HospitalDataset

__all__ = [
    "dataset_basename",
    "export_master_csv",
    "export_master_excel",
]


def dataset_basename(dataset: HospitalDataset, fallback: str = "hospital") -> str:
    """Filesystem-safe base name from the hospital name, else *fallback*."""
    name = dataset.hospital.name if dataset.hospital and dataset.hospital.name else fallback
    cleaned = re.sub(r"[^\w\-. ]+", " ", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.rstrip(" .") or fallback
