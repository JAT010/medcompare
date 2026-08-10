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
    pages_crawled: int = 0
    pages_classified: int = 0
    pages_parsed: int = 0
    pages_extracted: int = 0
    pages_with_information: int = 0
    pages_without_information: int = 0
    records_extracted: int = 0
    records_rejected: int = 0
    duplicates_removed: int = 0
    conflicts: list[Conflict] = Field(default_factory=list)
    categories_discovered: list[str] = Field(default_factory=list)
    categories_empty: list[str] = Field(default_factory=list)
    # label -> found | not_found | not_applicable | failed
    category_coverage: dict[str, str] = Field(default_factory=dict)
    # label -> field names expected by the schema but not populated
    missing_fields: dict[str, list[str]] = Field(default_factory=dict)
    records_by_category: dict[str, int] = Field(default_factory=dict)
    extraction_methods: dict[str, int] = Field(default_factory=dict)
    low_confidence_records: int = 0
    uncertain_hospital_associations: int = 0
    warnings: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)

    @property
    def conflicts_detected(self) -> int:
        return len(self.conflicts)

    @property
    def conflicts_resolved(self) -> int:
        return sum(1 for conflict in self.conflicts if conflict.resolution_status == "resolved")

    @property
    def conflicts_unresolved(self) -> int:
        return sum(
            1 for conflict in self.conflicts if conflict.resolution_status == "unresolved"
        )
