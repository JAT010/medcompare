"""CSV export derived from the finished dataset.

One CSV per non-empty canonical category (``<category_key>.csv``) plus a
``sources.csv`` mirror. Empty categories produce no files. Nothing here
re-scrapes the website.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES
from hospital_extractor.schema.discovery import category_records, discover_fields

__all__ = ["export_csv"]


def export_csv(dataset: HospitalDataset, output_dir: Path | str) -> dict[str, Path]:
    """Write category CSVs under *output_dir*; returns ``{category_key: path}``."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    for category in CANONICAL_CATEGORIES:
        records = category_records(dataset, category)
        if not records:
            continue
        fields = discover_fields(category, records)
        rows = [
            {field: _cell_value(_field_value(record, field)) for field in fields}
            for record in records
        ]
        frame = pd.DataFrame(rows, columns=fields)
        path = out / f"{category.key}.csv"
        frame.to_csv(path, index=False, encoding="utf-8")
        written[category.key] = path

    if dataset.sources:
        sources_rows = [
            {
                "entity": source.entity,
                "source_url": source.source_url,
                "page_title": source.page_title or "",
                "context": source.context or "",
                "method": source.extraction_method or "",
                "strength": source.source_strength or "",
            }
            for source in dataset.sources
        ]
        sources_path = out / "sources.csv"
        pd.DataFrame(sources_rows).to_csv(sources_path, index=False, encoding="utf-8")
        written["sources"] = sources_path

    return written


def _field_value(record: object, name: str) -> object:
    dump = getattr(record, "model_dump", None)
    if callable(dump):
        return dict(dump()).get(name)
    if isinstance(record, dict):
        return record.get(name)
    return None


def _cell_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    return value
