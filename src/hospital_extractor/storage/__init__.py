"""Persistence layer: raw page cache and intermediate JSON store."""

from __future__ import annotations

from hospital_extractor.storage.json_store import read_dataset, write_dataset
from hospital_extractor.storage.raw import RawStore

__all__ = ["RawStore", "read_dataset", "write_dataset"]
