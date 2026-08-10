"""Data-quality metadata accumulated during extraction.

This is the internal quality report attached to a :class:`HospitalDataset`.
It feeds the Data Quality worksheet in a later export phase; it is not a
canonical extraction category.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hospital_extractor.models.conflict import Conflict

__all__ = ["DataQuality"]


class DataQuality(BaseModel):
    """Aggregate quality statistics for one extraction run."""

    model_config = ConfigDict(str_strip_whitespace=True)

    pages_considered: int = 0
    pages_extracted: int = 0
    records_extracted: int = 0
    records_rejected: int = 0
    duplicates_removed: int = 0
    conflicts: list[Conflict] = Field(default_factory=list)
    categories_discovered: list[str] = Field(default_factory=list)
    categories_empty: list[str] = Field(default_factory=list)
    low_confidence_records: int = 0
    uncertain_hospital_associations: int = 0
    warnings: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)

    @property
    def conflicts_detected(self) -> int:
        return len(self.conflicts)
