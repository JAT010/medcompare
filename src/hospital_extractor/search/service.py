"""Search and field-comparison services over the hospital store (P22).

Reads canonical datasets from SQLite, applies deterministic filters, and
produces provenance-preserving results.  Never ranks hospitals and never
converts UNKNOWN into NO.
"""

from __future__ import annotations

from typing import Any

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.search.filters import (
    DEFAULT_COMPARISON_FIELDS,
    compare_field_values,
    evidence_for,
    label_for_field,
    matches_filter,
    resolve_field_values,
    validate_field_path,
)
from hospital_extractor.search.models import (
    ComparisonField,
    ComparisonFieldCell,
    ComparisonRequest,
    ComparisonResult,
    EvidenceReference,
    Filter,
    Query,
    QueryResult,
)
from hospital_extractor.storage import DatasetSummary, HospitalDatabase

__all__ = [
    "HospitalNotFoundError",
    "compare_stored_hospitals",
    "search_datasets",
]


class HospitalNotFoundError(LookupError):
    """A requested hospital id has no stored dataset."""


def search_datasets(db: HospitalDatabase, query: Query) -> QueryResult:
    """Run *query* against every stored dataset.

    Results are ordered by hospital name (case-insensitive) then dataset
    id so pagination is stable.  Each match keeps hospital identity,
    source provenance, capture time and per-field evidence.
    """
    for filter_ in query.filters:
        validate_field_path(filter_.field)

    candidates = _candidate_datasets(db, query)
    all_matches: list[dict[str, Any]] = []

    for summary, dataset in candidates:
        if not _text_matches(dataset, query.text):
            continue
        field_hits = _evaluate_filters(dataset, query.filters, summary)
        if query.filters and field_hits is None:
            continue
        if field_hits is None:
            field_hits = []
        all_matches.append(_match_payload(summary, dataset, field_hits))

    # Stable order: hospital name, then dataset id (pagination-safe).
    all_matches.sort(
        key=lambda item: (
            str(item.get("hospital_name") or "").casefold(),
            str(item.get("dataset_id") or ""),
        )
    )
    total = len(all_matches)
    matches = all_matches[query.offset : query.offset + query.limit]

    return QueryResult(
        total=total,
        limit=query.limit,
        offset=query.offset,
        matches=matches,
        filters=list(query.filters),
    )


def compare_stored_hospitals(
    db: HospitalDatabase,
    request: ComparisonRequest,
) -> ComparisonResult:
    """Compare 2–4 stored hospitals field-by-field (no ranking)."""
    fields = request.fields or list(DEFAULT_COMPARISON_FIELDS)
    for field in fields:
        validate_field_path(field)

    loaded: list[tuple[str, str | None, str, DatasetSummary, HospitalDataset]] = []
    for hospital_id in request.hospital_ids:
        summary, dataset = _latest_for_hospital(db, hospital_id)
        name = (
            dataset.hospital.name
            if dataset.hospital is not None and dataset.hospital.name
            else summary.hospital_name
        )
        loaded.append((hospital_id, name, summary.dataset_id, summary, dataset))

    hospital_names = {hospital_id: name for hospital_id, name, _, _, _ in loaded}
    comparison_fields: list[ComparisonField] = []

    for path in fields:
        cells: list[ComparisonFieldCell] = []
        for hospital_id, name, dataset_id, summary, dataset in loaded:
            source_type = summary.source_type
            value, unknown, evidence = compare_field_values(
                dataset,
                path,
                hospital_ref=summary.hospital_ref,
                hospital_name=name,
                source_type=source_type,
            )
            cells.append(
                ComparisonFieldCell(
                    hospital_id=hospital_id,
                    hospital_name=name,
                    dataset_id=dataset_id,
                    value=value,
                    unknown=unknown,
                    source_type=source_type,
                    provider=summary.provider,
                    captured_at=summary.created_at,
                    evidence=evidence,
                )
            )
        comparison_fields.append(
            ComparisonField(field=path, label=label_for_field(path), cells=cells)
        )

    return ComparisonResult(
        hospital_ids=list(request.hospital_ids),
        hospital_names=hospital_names,
        fields=comparison_fields,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _candidate_datasets(
    db: HospitalDatabase,
    query: Query,
) -> list[tuple[DatasetSummary, HospitalDataset]]:
    """Summaries + payloads to evaluate, optionally narrowed by hospital id."""
    if query.hospital_ids:
        wanted = set(query.hospital_ids)
        summaries = [s for s in db.list_datasets() if s.hospital_id in wanted]
        # Preserve newest-first from the store, then load payloads.
        ordered = summaries
    else:
        ordered = db.list_datasets()

    pairs: list[tuple[DatasetSummary, HospitalDataset]] = []
    for summary in ordered:
        dataset = db.load_dataset(summary.dataset_id)
        if dataset is None:
            continue
        pairs.append((summary, dataset))
    return pairs


def _text_matches(dataset: HospitalDataset, text: str | None) -> bool:
    """Case-insensitive free-text match across hospital + entity names."""
    if not text:
        return True
    needle = text.casefold()
    haystacks: list[str] = []
    if dataset.hospital is not None:
        haystacks.extend(
            part
            for part in (
                dataset.hospital.name,
                dataset.hospital.description,
                dataset.hospital.hospital_type,
            )
            if part
        )
        haystacks.append(dataset.hospital.source_url)
    for category_key in (
        "contacts",
        "facilities",
        "departments",
        "specialities",
        "doctors",
        "treatments",
        "procedures",
        "technologies",
        "accreditations",
    ):
        for record in getattr(dataset, category_key, []) or []:
            name = getattr(record, "name", None)
            if name:
                haystacks.append(str(name))
            city = getattr(record, "city", None)
            if city:
                haystacks.append(str(city))
    if dataset.source is not None and dataset.source.hospital_ref:
        haystacks.append(dataset.source.hospital_ref)
    return any(needle in str(item).casefold() for item in haystacks)


def _evaluate_filters(
    dataset: HospitalDataset,
    filters: list[Filter],
    summary: DatasetSummary,
) -> list[dict[str, Any]] | None:
    """Return field-hit descriptors when every filter matches, else None.

    With no filters, returns an empty list (match-all).
    """
    if not filters:
        return []

    hits: list[dict[str, Any]] = []
    for filter_ in filters:
        values = resolve_field_values(
            dataset,
            filter_.field,
            hospital_ref=summary.hospital_ref,
            hospital_name=summary.hospital_name,
            source_type=summary.source_type,
        )
        if not matches_filter(values, filter_):
            return None
        # Record the resolved values for provenance (may be empty → UNKNOWN).
        evidence = [
            evidence_for(origin, source_type=summary.source_type, text=str(value))
            for value, origin in values
        ]
        if values:
            rendered: Any
            if len(values) == 1:
                rendered = values[0][0]
                if hasattr(rendered, "value"):
                    rendered = rendered.value
            else:
                rendered = sorted({str(v) for v, _ in values}, key=str.casefold)
            hits.append(
                {
                    "field": filter_.field,
                    "value": rendered,
                    "unknown": False,
                    "evidence": [e.model_dump(mode="json") for e in evidence],
                }
            )
        else:
            hits.append(
                {
                    "field": filter_.field,
                    "value": None,
                    "unknown": True,
                    "evidence": [],
                }
            )
    return hits


def _match_payload(
    summary: DatasetSummary,
    dataset: HospitalDataset,
    field_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    name = (
        dataset.hospital.name
        if dataset.hospital is not None and dataset.hospital.name
        else summary.hospital_name
    )
    return {
        "dataset_id": summary.dataset_id,
        "hospital_id": summary.hospital_id,
        "hospital_key": summary.hospital_key,
        "hospital_name": name,
        "hospital_ref": summary.hospital_ref,
        "source_type": summary.source_type,
        "provider": summary.provider,
        "captured_at": summary.created_at.isoformat(),
        "record_count": summary.record_count,
        "fields": field_hits,
        "evidence": [e.model_dump(mode="json") for e in _top_level_evidence(dataset, summary)],
    }


def _top_level_evidence(
    dataset: HospitalDataset,
    summary: DatasetSummary,
) -> list[EvidenceReference]:
    """Dataset-level provenance for the match row."""
    source_url = ""
    page_title = None
    confidence = None
    if dataset.hospital is not None:
        source_url = dataset.hospital.source_url
        page_title = dataset.hospital.page_title
        confidence = dataset.hospital.confidence
    if dataset.source is not None:
        source_url = source_url or dataset.source.base_url or dataset.source.endpoint or ""
        return [
            EvidenceReference(
                source_url=source_url,
                source_type=dataset.source.source_type.value,
                page_title=page_title,
                captured_at=dataset.source.retrieved_at,
                confidence=confidence,
                extraction_method=dataset.hospital.extraction_method
                if dataset.hospital is not None
                else None,
                source_strength=dataset.hospital.source_strength
                if dataset.hospital is not None
                else None,
                context=dataset.source.hospital_ref or None,
            )
        ]
    return [
        EvidenceReference(
            source_url=source_url,
            source_type=summary.source_type,
            page_title=page_title,
            captured_at=summary.created_at,
            confidence=confidence,
        )
    ]


def _latest_for_hospital(
    db: HospitalDatabase,
    hospital_id: str,
) -> tuple[DatasetSummary, HospitalDataset]:
    """Newest stored dataset for a canonical hospital identity.

    Prefers ``list_hospital_sources`` when the id is a hospital_key-like
    grouping; otherwise scans ``list_datasets`` (already newest-first).
    """
    exact = [s for s in db.list_datasets() if s.hospital_id == hospital_id]
    if not exact:
        # Allow a dataset id as a direct reference (CLI / tests).
        dataset = db.load_dataset(hospital_id)
        if dataset is not None:
            for summary in db.list_datasets():
                if summary.dataset_id == hospital_id:
                    return summary, dataset
        raise HospitalNotFoundError(
            f"no stored dataset for hospital {hospital_id!r}"
        )
    chosen = exact[0]
    dataset = db.load_dataset(chosen.dataset_id)
    if dataset is None:
        raise HospitalNotFoundError(
            f"dataset {chosen.dataset_id!r} for hospital {hospital_id!r} is missing"
        )
    return chosen, dataset
