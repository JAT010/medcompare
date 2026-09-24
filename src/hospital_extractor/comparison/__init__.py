"""Hospital comparison engine.

Sits on top of the existing extraction pipeline and compares multiple
hospitals for a target procedure, department or speciality.  The comparison
layer *consumes* :class:`~hospital_extractor.models.dataset.HospitalDataset`
objects — it never re-crawls or re-extracts.

Usage::

    from hospital_extractor.comparison import compare_urls
    from hospital_extractor.extraction import build_dataset

    datasets = [(url, build_dataset(pages, target_url=url)) for url, pages in ...]
    result = compare_urls(datasets, "Knee Replacement")
"""

from __future__ import annotations

from hospital_extractor.comparison.models import (
    NO_EVIDENCE_MESSAGE,
    BooleanDecision,
    ComparisonEvidence,
    ComparisonResult,
    ComparisonTarget,
    DimensionResult,
    EntityMatch,
    HospitalComparison,
)
from hospital_extractor.comparison.service import compare_datasets, compare_urls

__all__ = [
    "BooleanDecision",
    "ComparisonEvidence",
    "ComparisonResult",
    "ComparisonTarget",
    "DimensionResult",
    "EntityMatch",
    "HospitalComparison",
    "NO_EVIDENCE_MESSAGE",
    "compare_datasets",
    "compare_urls",
]
