"""Deterministic benchmark fixtures with explicit ground truth (P26).

Fixtures A–U cover the scenarios required by the P26 specification without
network access. Ground truth is the construction of the fixture itself
(controlled synthetic data), not "the test passed."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.metadata import FetchStatus, SourceMetadata, SourceType
from hospital_extractor.models.treatment import Treatment

__all__ = ["FixtureBundle", "build_fixtures"]


def _source(url: str) -> SourceMetadata:
    return SourceMetadata(
        source_type=SourceType("website"),
        source_id=f"website:{url}",
        hospital_ref=url,
        base_url=url,
        retrieved_at="2026-01-01T00:00:00Z",
        status=FetchStatus.SUCCESS,
        hospital_name="Fixture Hospital",
    )


def _dataset(
    *,
    name: str = "Fixture Hospital",
    url: str = "https://fixture-a.example",
    bed_count: int | None = 300,
    treatments: list[str] | None = None,
    with_provenance: bool = True,
) -> HospitalDataset:
    src_url = url if with_provenance else ""
    hospital = HospitalOverview(
        name=name,
        bed_count=bed_count,
        source_url=src_url,
    )
    names = treatments if treatments is not None else ["Knee Replacement"]
    return HospitalDataset(
        source=_source(url),
        hospital=hospital,
        treatments=[Treatment(name=n, source_url=src_url) for n in names],
    )


@dataclass
class FixtureBundle:
    """All named fixtures for one benchmark run (A–U)."""

    unchanged: HospitalDataset
    modified: HospitalDataset
    with_added: HospitalDataset
    with_removed: HospitalDataset
    duplicate_records: list[str]
    conflict_pairs: list[tuple[str, str]]
    unknown: HospitalDataset
    verified_claim: HospitalDataset
    mismatched_claim: HospitalDataset
    unverified_target: HospitalDataset
    search_a: HospitalDataset
    search_b: HospitalDataset
    search_c: HospitalDataset
    reconciliation_transitions: dict[str, list[str]] = field(default_factory=dict)
    ground_truth: dict[str, Any] = field(default_factory=dict)


def build_fixtures() -> FixtureBundle:
    """Construct the deterministic A–U fixture set."""
    unchanged = _dataset()
    modified = _dataset(bed_count=450)  # B: 300 → 450
    with_added = _dataset(treatments=["Knee Replacement", "Hip Replacement"])  # C
    with_removed = _dataset(treatments=["Knee Replacement"])  # D vs C
    unknown = _dataset(bed_count=None)  # G
    verified_claim = _dataset(treatments=["Knee Replacement", "Cardiac Surgery"])
    mismatched_claim = _dataset(bed_count=420)  # claim 300 vs website 420
    unverified_target = _dataset(treatments=["Knee Replacement"])  # lacks new

    duplicate_records = ["Knee Replacement", "Knee Replacement", "Hip Replacement"]
    conflict_pairs = [
        ("https://fixture-a.example", "300 beds"),
        ("https://fixture-b.example", "350 beds"),
    ]

    search_a = _dataset(name="Alpha Hospital", url="https://alpha.example", bed_count=100)
    search_b = _dataset(
        name="Beta Hospital",
        url="https://beta.example",
        bed_count=200,
        treatments=["Cardiac Catheterization"],
    )
    search_c = _dataset(
        name="Gamma Hospital",
        url="https://gamma.example",
        bed_count=None,
        treatments=["Knee Replacement", "Angioplasty"],
    )

    return FixtureBundle(
        unchanged=unchanged,
        modified=modified,
        with_added=with_added,
        with_removed=with_removed,
        duplicate_records=duplicate_records,
        conflict_pairs=conflict_pairs,
        unknown=unknown,
        verified_claim=verified_claim,
        mismatched_claim=mismatched_claim,
        unverified_target=unverified_target,
        search_a=search_a,
        search_b=search_b,
        search_c=search_c,
        reconciliation_transitions={
            "open": ["under_review", "resolved", "dismissed"],
            "under_review": ["resolved", "dismissed"],
            "resolved": ["reopened"],
            "reopened": ["under_review", "resolved", "dismissed"],
            "dismissed": ["reopened"],
        },
        ground_truth={
            "unchanged_bed_count": 300,
            "modified_bed_count": 450,
            "added_treatment_count": 2,
            "removed_treatment_count": 1,
            "duplicate_tokens_total": 3,
            "duplicate_tokens_unique": 2,
            "conflict_pair_count": 2,
            "unknown_bed_count": None,
            "mismatch_claim_beds": 300,
            "mismatch_website_beds": 420,
            "search_hospitals": 3,
            "search_unknown_bed_hospitals": 1,
            "verified_has_cardiac": True,
            "unverified_target_has_cardiac": False,
        },
    )
