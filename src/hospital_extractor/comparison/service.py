"""Comparison service — the orchestrator.

Takes one or more :class:`~hospital_extractor.models.dataset.HospitalDataset`
objects and a resolved :class:`~hospital_extractor.comparison.target.ComparisonTarget`,
runs entity matching and dimension computation for each hospital, and
assembles a complete :class:`~hospital_extractor.comparison.models.ComparisonResult`.

The engine consumes *canonical datasets only* — it never imports crawler,
adapter or mock-API internals, and it never branches on ``source_type`` for
comparison logic.  Source type is provenance metadata, nothing more.

Multiple datasets may share the same ``hospital_id`` (e.g. the same hospital
from a website source AND a demo API source).  In that case their evidence is
merged into a single hospital column; contradictory claims across the sources
surface as a :class:`SourceConflict` (no silent winner).
"""

from __future__ import annotations

from collections import defaultdict

from hospital_extractor.comparison.dimensions import (
    apply_conflicts,
    compute_dimensions,
)
from hospital_extractor.comparison.matcher import match_booleans, match_entities
from hospital_extractor.comparison.models import (
    NO_EVIDENCE_MESSAGE,
    ComparisonEvidence,
    ComparisonResult,
    ComparisonTarget,
    DimensionResult,
    HospitalComparison,
)
from hospital_extractor.comparison.target import resolve_target
from hospital_extractor.models.dataset import HospitalDataset

__all__ = [
    "compare_datasets",
    "compare_urls",
]


def compare_datasets(
    datasets: list[tuple[str, HospitalDataset]],
    target: ComparisonTarget,
) -> ComparisonResult:
    """Compare multiple hospitals for *target*.

    ``datasets`` is a list of ``(hospital_id, dataset)`` tuples.  The
    ``hospital_id`` is an opaque string identifying the hospital.  When several
    tuples share the same ``hospital_id`` they are treated as the *same
    hospital* reported by different sources and merged into one column.

    Returns a fully-populated :class:`ComparisonResult`.
    """
    buckets: dict[str, list[HospitalDataset]] = defaultdict(list)
    for hospital_id, dataset in datasets:
        buckets[hospital_id].append(dataset)

    hospitals: list[HospitalComparison] = []
    for hospital_id, group in buckets.items():
        if len(group) == 1:
            hospitals.append(_compare_single(group[0], hospital_id, target))
        else:
            hospitals.append(_compare_merged(group, hospital_id, target))

    return ComparisonResult(
        target=target,
        hospitals=hospitals,
    )


def compare_urls(
    datasets: list[tuple[str, HospitalDataset]],
    procedure_name: str,
    *,
    category: str | None = None,
) -> ComparisonResult:
    """Convenience wrapper: resolve the target from a procedure name, then compare.

    ``procedure_name`` is the user-supplied text (e.g. "Knee Replacement").
    ``category`` may explicitly set the target category.
    """
    target = resolve_target(procedure_name, category=category)
    return compare_datasets(datasets, target)


def _compare_single(
    dataset: HospitalDataset,
    hospital_id: str,
    target: ComparisonTarget,
) -> HospitalComparison:
    """Comparison for one hospital from a single source (the common case)."""
    entity_matches = match_entities(dataset, target, hospital_id=hospital_id)
    boolean_matches = match_booleans(dataset, target, hospital_id=hospital_id)
    dimensions = compute_dimensions(
        dataset,
        entity_matches,
        boolean_matches,
        target,
        hospital_id=hospital_id,
    )
    warnings = _collect_warnings(dataset)

    return HospitalComparison(
        hospital_id=hospital_id,
        hospital_name=dataset.hospital.name if dataset.hospital is not None else None,
        target=target,
        dimensions=dimensions,
        evidence_coverage=_evidence_coverage(dimensions),
        quality_warnings=warnings,
        source_type=dataset.source.source_type.value if dataset.source is not None else "website",
        provider=dataset.source.provider if dataset.source is not None else None,
        source_types=(
            [dataset.source.source_type.value] if dataset.source is not None else ["website"]
        ),
        providers=(
            [dataset.source.provider]
            if dataset.source is not None and dataset.source.provider
            else []
        ),
    )


def _compare_merged(
    datasets: list[HospitalDataset],
    hospital_id: str,
    target: ComparisonTarget,
) -> HospitalComparison:
    """Comparison for a single hospital reported by multiple sources.

    Evidence from every source is merged per dimension.  Where the sources
    contradict each other (a concrete ``yes`` from one, a concrete ``no`` from
    another) the merged dimension carries a :class:`SourceConflict` and its
    decision is left as ``unknown`` — the presentation layer renders CONFLICT
    and no winner is chosen.
    """
    names: list[str] = []
    all_warnings: list[str] = []
    per_source_dims: list[list[DimensionResult]] = []
    source_types: list[str] = []
    providers: list[str] = []

    for dataset in datasets:
        if dataset.hospital is not None and dataset.hospital.name:
            names.append(dataset.hospital.name)
        all_warnings.extend(_collect_warnings(dataset))
        if dataset.source is not None:
            s_type = dataset.source.source_type.value
            if s_type not in source_types:
                source_types.append(s_type)
            if dataset.source.provider and dataset.source.provider not in providers:
                providers.append(dataset.source.provider)
        entity_matches = match_entities(dataset, target, hospital_id=hospital_id)
        boolean_matches = match_booleans(dataset, target, hospital_id=hospital_id)
        per_source_dims.append(
            compute_dimensions(
                dataset,
                entity_matches,
                boolean_matches,
                target,
                hospital_id=hospital_id,
            )
        )

    dimensions = _merge_dimensions(per_source_dims)
    apply_conflicts(dimensions)

    return HospitalComparison(
        hospital_id=hospital_id,
        hospital_name=names[0] if names else None,
        target=target,
        dimensions=dimensions,
        evidence_coverage=_evidence_coverage(dimensions),
        quality_warnings=_dedupe(all_warnings),
        source_type=source_types[0] if source_types else "website",
        provider=providers[0] if providers else None,
        source_types=source_types,
        providers=providers,
    )


def _merge_dimensions(
    per_source_dimensions: list[list[DimensionResult]],
) -> list[DimensionResult]:
    """Merge per-source dimension lists into one by ``dimension_key``.

    Evidence items are concatenated (the ``NO_EVIDENCE_MESSAGE`` placeholder is
    dropped); conflicting real evidence is preserved verbatim so the downstream
    conflict detector can see both sides.
    """
    if not per_source_dimensions:
        return []
    key_order = [dim.dimension_key for dim in per_source_dimensions[0]]
    by_key: dict[str, list[DimensionResult]] = defaultdict(list)
    for dims in per_source_dimensions:
        for dim in dims:
            by_key[dim.dimension_key].append(dim)

    merged: list[DimensionResult] = []
    for key in key_order:
        subs = by_key[key]
        real_evidence = [
            ev
            for sub in subs
            for ev in sub.evidence
            if ev.text != NO_EVIDENCE_MESSAGE
        ]
        value = next((sub.value for sub in subs if sub.value is not None), None)
        merged.append(
            DimensionResult(
                dimension_key=key,
                label=subs[0].label,
                decision=_merged_decision(real_evidence, subs),
                value=value,
                evidence=real_evidence,
            )
        )
    return merged


def _merged_decision(
    real_evidence: list[ComparisonEvidence],
    subs: list[DimensionResult],
) -> str:
    """Aggregate decision for a merged dimension, WITHOUT conflict handling.

    This deliberately does not know about conflicts: the "conflict ⇒ decision
    unknown" rule lives only in :func:`apply_conflicts`, which callers must
    apply after merging (see :func:`_compare_merged`).  A provisional
    ``yes``/``no`` returned here for contradictory evidence is always
    overwritten to ``unknown`` by that single authority.

    - yes present   → "yes"
    - no  present   → "no"
    - nothing real  → first source's decision
    """
    if any(ev.decision == "yes" for ev in real_evidence):
        return "yes"
    if any(ev.decision == "no" for ev in real_evidence):
        return "no"
    return subs[0].decision if subs else "unknown"


def _collect_warnings(dataset: HospitalDataset) -> list[str]:
    warnings: list[str] = []
    quality = dataset.quality
    if quality.warnings:
        warnings.extend(quality.warnings[:10])
    if quality.categories_empty:
        warnings.append(f"Empty categories: {', '.join(quality.categories_empty)}")
    return warnings


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _evidence_coverage(
    dimensions: list[DimensionResult],
) -> float:
    """Fraction of dimensions that have at least one real evidence item.

    A dimension with only the ``NO_EVIDENCE_MESSAGE`` placeholder is
    considered to have no evidence.
    """
    comparable = [
        d for d in dimensions
        if d.dimension_key != "evidence_coverage"
    ]
    if not comparable:
        return 0.0
    with_real_evidence = sum(
        1 for d in comparable
        if any(ev.text != NO_EVIDENCE_MESSAGE for ev in d.evidence)
    )
    return with_real_evidence / len(comparable)