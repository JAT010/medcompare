"""Export layer: dynamic Excel workbook + CSV mirrors derived from the dataset."""

from __future__ import annotations

import re

from hospital_extractor.export.csv import export_csv
from hospital_extractor.export.excel import export_excel
from hospital_extractor.models.dataset import HospitalDataset

__all__ = ["dataset_basename", "export_csv", "export_excel"]


def dataset_basename(dataset: HospitalDataset, fallback: str = "hospital") -> str:
    """Filesystem-safe base name from the hospital name, else *fallback*."""
    name = dataset.hospital.name if dataset.hospital and dataset.hospital.name else fallback
    cleaned = re.sub(r"[^\w\-. ]+", " ", name).strip()
    return re.sub(r"\s+", " ", cleaned) or fallback
