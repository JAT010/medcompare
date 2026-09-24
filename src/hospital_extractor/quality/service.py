"""Compute quality reports from stored rows only (P23).

Rules:

- Read-only against :class:`HospitalDatabase` (and monitoring/reconciliation
  list helpers that are themselves read-only).
- Never invent counts; empty stores yield zeroed metrics with definitions.
- Never emit an overall score — only independent dimensions.
- UNKNOWN / empty categories are reported as rates, never rewritten to NO.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.quality.models import (
    DatasetQualityReport,
    HospitalQualityReport,
    MetricValue,
    PipelineQualityReport,
    QualityDimension,
    ReportScope,
    SourceQualityReport,
)
from hospital_extractor.storage import DatasetSummary, HospitalDatabase

__all__ = [
    "HospitalQualityNotFoundError",
    "build_dataset_quality_report",
    "build_hospital_quality_report",
    "build_pipeline_quality_report",
    "build_source_quality_reports",
]


class HospitalQualityNotFoundError(LookupError):
    """Requested hospital id has no stored datasets or identity rows."""


# ---------------------------------------------------------------------------
# Metric definitions (static text used in reports)
# ---------------------------------------------------------------------------


def _m(
    name: str,
    label: str,
    dimension: QualityDimension,
    value: int | float | str | bool | None,
    definition: str,
    *,
    unit: str = "count",
    detail: str = "",
) -> MetricValue:
    return MetricValue(
        name=name,
        label=label,
        dimension=dimension,
        unit=unit,
        value=value,
        definition=definition,
        detail=detail,
    )


# Canonical hospital overview fields tracked for field-coverage metrics.
_TRACKED_HOSPITAL_FIELDS: tuple[str, ...] = (
    "name",
    "hospital_type",
    "description",
    "parent_organization",
    "established_year",
    "status",
    "bed_count",
    "doctor_count",
    "staff_count",
    "speciality_count",
    "department_count",
    "website",
    "logo_url",
)

_COLLECTION_KEYS: tuple[str, ...] = (
    "contacts",
    "infrastructure",
    "facilities",
    "departments",
    "specialities",
    "doctors",
    "treatments",
    "procedures",
    "technologies",
    "accreditations",
)


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 6)


def _as_float(value: Any) -> float | None:
    """Coerce a metric value to float when it is numeric; else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, set)):
        return len(value) > 0
    return True


def _hospital_field_coverage(dataset: HospitalDataset) -> tuple[int, int, list[str]]:
    """(populated, tracked, missing_names) for the hospital overview block."""
    hospital = dataset.hospital
    missing: list[str] = []
    populated = 0
    for field in _TRACKED_HOSPITAL_FIELDS:
        value = getattr(hospital, field, None) if hospital is not None else None
        if _is_present(value):
            populated += 1
        else:
            missing.append(field)
    return populated, len(_TRACKED_HOSPITAL_FIELDS), missing


def _collection_coverage(dataset: HospitalDataset) -> tuple[int, int, list[str]]:
    """(non_empty, total_collections, empty_keys) across canonical collections."""
    empty: list[str] = []
    non_empty = 0
    for key in _COLLECTION_KEYS:
        items = getattr(dataset, key, None) or []
        if items:
            non_empty += 1
        else:
            empty.append(key)
    return non_empty, len(_COLLECTION_KEYS), empty


def _unknown_field_count(dataset: HospitalDataset) -> int:
    """Count schema-tracked gaps: empty hospital fields + empty collections.

    Uses ``quality.missing_fields`` when the extractor populated it; otherwise
    falls back to structural emptiness.  Always non-negative.
    """
    quality = dataset.quality
    from_extractor = sum(len(v or []) for v in (quality.missing_fields or {}).values())
    if from_extractor:
        return from_extractor
    _, _, missing_fields = _hospital_field_coverage(dataset)
    _, _, empty_collections = _collection_coverage(dataset)
    return len(missing_fields) + len(empty_collections)


def _authorization_status(dataset: HospitalDataset) -> str:
    source = dataset.source
    if source is None:
        return ""
    try:
        from hospital_extractor.sources.registry import get_default_registry

        registry = get_default_registry()
        if not registry.has(source.source_type.value):
            return "UNKNOWN"
        definition = registry.definition(source.source_type.value)
        if definition.simulated:
            return "SIMULATED / MOCK"
        if definition.requires_authorization:
            return "AUTHORIZED"
        return "PUBLIC"
    except Exception:  # noqa: BLE001 — registry must never break quality reports
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# Dataset report
# ---------------------------------------------------------------------------


def build_dataset_quality_report(
    db: HospitalDatabase,
    summary: DatasetSummary,
    dataset: HospitalDataset | None = None,
    *,
    now: datetime | None = None,
) -> DatasetQualityReport:
    """Field/provenance/unknown/conflict/duplicate/extraction metrics."""
    if dataset is None:
        loaded = db.load_dataset(summary.dataset_id)
        dataset = loaded if loaded is not None else HospitalDataset()

    now = now or datetime.now(UTC)
    quality = dataset.quality

    populated_fields, tracked_fields, missing_fields = _hospital_field_coverage(dataset)
    non_empty_collections, total_collections, empty_collections = _collection_coverage(
        dataset
    )
    unknown_fields = _unknown_field_count(dataset)

    pages_crawled = quality.pages_crawled
    pages_with_info = quality.pages_with_information
    records_extracted = quality.records_extracted
    records_rejected = quality.records_rejected
    duplicates = quality.duplicates_removed
    conflicts = quality.conflicts_detected
    conflicts_unresolved = quality.conflicts_unresolved

    has_source = dataset.source is not None
    has_hospital_source_url = bool(
        dataset.hospital is not None and dataset.hospital.source_url
    )
    provenance_records = sum(
        1
        for category in _COLLECTION_KEYS
        for record in (getattr(dataset, category, None) or [])
        if getattr(record, "source_url", None)
    )
    total_records = sum(
        len(getattr(dataset, category, None) or []) for category in _COLLECTION_KEYS
    )

    captured_at = summary.created_at
    age_days: float | None = None
    if captured_at is not None:
        age_days = round((now - captured_at).total_seconds() / 86400.0, 6)
        if age_days < 0:
            age_days = 0.0

    metrics = [
        _m(
            "records_extracted",
            "Records extracted",
            QualityDimension.EXTRACTION,
            records_extracted,
            "Count of entity records accepted during extraction.",
        ),
        _m(
            "records_rejected",
            "Records rejected",
            QualityDimension.EXTRACTION,
            records_rejected,
            "Count of entity records rejected during extraction.",
        ),
        _m(
            "duplicates_removed",
            "Duplicates removed",
            QualityDimension.DUPLICATE,
            duplicates,
            "Count of duplicate records removed during extraction.",
        ),
        _m(
            "duplicate_rate",
            "Duplicate rate",
            QualityDimension.DUPLICATE,
            _rate(duplicates, records_extracted + duplicates) or 0.0,
            "duplicates_removed / (records_extracted + duplicates_removed); "
            "null denominator yields 0.0 when no records were extracted.",
            unit="ratio",
        ),
        _m(
            "pages_crawled",
            "Pages crawled",
            QualityDimension.CRAWL,
            pages_crawled,
            "Count of pages successfully crawled for this dataset.",
        ),
        _m(
            "pages_with_information",
            "Pages with information",
            QualityDimension.CRAWL,
            pages_with_info,
            "Count of crawled pages that contributed extracted information.",
        ),
        _m(
            "crawl_success_rate",
            "Crawl success rate",
            QualityDimension.CRAWL,
            _rate(pages_with_info, pages_crawled) if pages_crawled else None,
            "pages_with_information / pages_crawled; null when nothing was crawled.",
            unit="ratio",
        ),
        _m(
            "extraction_warning_count",
            "Extraction warnings",
            QualityDimension.EXTRACTION,
            len(quality.warnings or []),
            "Count of warning strings recorded on the DataQuality block.",
        ),
        _m(
            "extraction_failure_count",
            "Extraction failures",
            QualityDimension.EXTRACTION,
            len(quality.failures or []),
            "Count of failure strings recorded on the DataQuality block.",
        ),
        _m(
            "conflicts_detected",
            "Conflicts detected",
            QualityDimension.CONFLICT,
            conflicts,
            "Count of field conflicts recorded during extraction.",
        ),
        _m(
            "conflicts_unresolved",
            "Conflicts unresolved",
            QualityDimension.CONFLICT,
            conflicts_unresolved,
            "Count of conflicts still marked unresolved on DataQuality.",
        ),
        _m(
            "conflict_rate",
            "Conflict rate",
            QualityDimension.CONFLICT,
            _rate(conflicts, max(records_extracted, 1)) or 0.0,
            "conflicts_detected / max(records_extracted, 1).",
            unit="ratio",
        ),
        _m(
            "hospital_fields_populated",
            "Hospital fields populated",
            QualityDimension.FIELD_COVERAGE,
            populated_fields,
            f"Non-empty overview fields among {_TRACKED_HOSPITAL_FIELDS!r}.",
        ),
        _m(
            "hospital_fields_tracked",
            "Hospital fields tracked",
            QualityDimension.FIELD_COVERAGE,
            tracked_fields,
            "Fixed set of overview fields used for coverage.",
        ),
        _m(
            "hospital_field_coverage_rate",
            "Hospital field coverage",
            QualityDimension.FIELD_COVERAGE,
            _rate(populated_fields, tracked_fields),
            "hospital_fields_populated / hospital_fields_tracked.",
            unit="ratio",
        ),
        _m(
            "collections_non_empty",
            "Collections with data",
            QualityDimension.FIELD_COVERAGE,
            non_empty_collections,
            "Canonical entity collections that contain at least one record.",
        ),
        _m(
            "collections_tracked",
            "Collections tracked",
            QualityDimension.FIELD_COVERAGE,
            total_collections,
            "Fixed set of canonical entity collections used for coverage.",
        ),
        _m(
            "collection_coverage_rate",
            "Collection coverage",
            QualityDimension.FIELD_COVERAGE,
            _rate(non_empty_collections, total_collections),
            "collections_non_empty / collections_tracked.",
            unit="ratio",
        ),
        _m(
            "unknown_field_count",
            "Unknown / missing field slots",
            QualityDimension.UNKNOWN_RATE,
            unknown_fields,
            "Schema-tracked slots with no published value (UNKNOWN, never NO).",
        ),
        _m(
            "unknown_field_rate",
            "Unknown field rate",
            QualityDimension.UNKNOWN_RATE,
            _rate(
                unknown_fields,
                tracked_fields + total_collections,
            ),
            "unknown_field_count / (hospital_fields_tracked + collections_tracked).",
            unit="ratio",
        ),
        _m(
            "empty_categories",
            "Empty categories",
            QualityDimension.FIELD_COVERAGE,
            len(empty_collections),
            "Canonical collections with zero records.",
            detail=", ".join(empty_collections),
        ),
        _m(
            "categories_empty_from_extractor",
            "Categories empty (extractor)",
            QualityDimension.FIELD_COVERAGE,
            len(quality.categories_empty or []),
            "categories_empty list from DataQuality.",
            detail=", ".join(quality.categories_empty or []),
        ),
        _m(
            "provenance_dataset_source",
            "Dataset has source metadata",
            QualityDimension.PROVENANCE,
            has_source,
            "True when SourceMetadata is attached to the dataset.",
            unit="bool",
        ),
        _m(
            "provenance_hospital_source_url",
            "Hospital overview has source_url",
            QualityDimension.PROVENANCE,
            has_hospital_source_url,
            "True when hospital.source_url is populated.",
            unit="bool",
        ),
        _m(
            "entity_records_total",
            "Entity records total",
            QualityDimension.PROVENANCE,
            total_records,
            "Sum of records across canonical entity collections.",
        ),
        _m(
            "entity_records_with_source_url",
            "Entity records with source_url",
            QualityDimension.PROVENANCE,
            provenance_records,
            "Entity records that carry a source_url provenance pointer.",
        ),
        _m(
            "entity_provenance_rate",
            "Entity provenance rate",
            QualityDimension.PROVENANCE,
            _rate(provenance_records, total_records) if total_records else None,
            "entity_records_with_source_url / entity_records_total; "
            "null when there are no entity records.",
            unit="ratio",
        ),
        _m(
            "freshness_age_days",
            "Age since capture (days)",
            QualityDimension.FRESHNESS,
            age_days,
            "now − summary.created_at, floored at 0. Null if capture time missing.",
            unit="days",
        ),
        _m(
            "source_type",
            "Source type",
            QualityDimension.SOURCE_HEALTH,
            summary.source_type,
            "Source type of this stored dataset.",
            unit="label",
        ),
        _m(
            "authorization_status",
            "Authorization status",
            QualityDimension.CONNECTOR,
            _authorization_status(dataset),
            "PUBLIC / AUTHORIZED / SIMULATED / MOCK / UNKNOWN from the connector registry.",
            unit="label",
        ),
        _m(
            "low_confidence_records",
            "Low-confidence records",
            QualityDimension.EXTRACTION,
            quality.low_confidence_records,
            "Count of low-confidence records recorded by the extractor.",
        ),
        _m(
            "uncertain_hospital_associations",
            "Uncertain hospital associations",
            QualityDimension.EXTRACTION,
            quality.uncertain_hospital_associations,
            "Count of records with uncertain hospital association.",
        ),
    ]

    return DatasetQualityReport(
        scope=ReportScope.DATASET,
        dataset_id=summary.dataset_id,
        hospital_id=summary.hospital_id,
        hospital_key=summary.hospital_key,
        hospital_name=summary.hospital_name,
        source_type=summary.source_type,
        provider=summary.provider,
        hospital_ref=summary.hospital_ref,
        captured_at=summary.created_at,
        record_count=summary.record_count,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Source reports
# ---------------------------------------------------------------------------


def build_source_quality_reports(
    db: HospitalDatabase,
    hospital_id: str,
    *,
    hospital_name: str | None = None,
    now: datetime | None = None,
) -> list[SourceQualityReport]:
    """One SourceQualityReport per (source_type, provider) for a hospital."""
    summaries = [s for s in db.list_datasets() if s.hospital_id == hospital_id]
    if not summaries:
        raise HospitalQualityNotFoundError(
            f"no stored datasets for hospital {hospital_id!r}"
        )

    grouped: dict[tuple[str, str], list[DatasetSummary]] = {}
    for summary in summaries:
        key = (summary.source_type, summary.provider or "")
        grouped.setdefault(key, []).append(summary)

    reports: list[SourceQualityReport] = []
    for (source_type, provider), items in sorted(grouped.items()):
        # list_datasets is newest-first; keep that order.
        items_sorted = sorted(items, key=lambda s: s.created_at, reverse=True)
        latest = items_sorted[0]
        dataset = db.load_dataset(latest.dataset_id)
        auth = _authorization_status(dataset) if dataset is not None else ""

        dataset_reports = [
            build_dataset_quality_report(db, item, now=now) for item in items_sorted
        ]
        # Aggregate source-level rollups from dataset metric names.
        metric_values = [dr.metric_value("records_extracted", 0) or 0 for dr in dataset_reports]
        records_extracted = sum(int(v) for v in metric_values)
        unknown_rates = [
            rate
            for dr in dataset_reports
            if (rate := _as_float(dr.metric_value("unknown_field_rate"))) is not None
        ]
        avg_unknown = (
            round(sum(unknown_rates) / len(unknown_rates), 6) if unknown_rates else None
        )
        ages = [
            age
            for dr in dataset_reports
            if (age := _as_float(dr.metric_value("freshness_age_days"))) is not None
        ]
        newest_age = min(ages) if ages else None

        metrics = [
            _m(
                "dataset_count",
                "Datasets for this source",
                QualityDimension.DATASET,
                len(items_sorted),
                "Stored dataset rows for this (source_type, provider).",
            ),
            _m(
                "records_extracted_sum",
                "Records extracted (sum)",
                QualityDimension.EXTRACTION,
                records_extracted,
                "Sum of records_extracted across datasets for this source.",
            ),
            _m(
                "average_unknown_field_rate",
                "Average unknown field rate",
                QualityDimension.UNKNOWN_RATE,
                avg_unknown,
                "Mean of dataset unknown_field_rate values; null if no ratios.",
                unit="ratio",
            ),
            _m(
                "newest_freshness_age_days",
                "Newest capture age (days)",
                QualityDimension.FRESHNESS,
                newest_age,
                "Minimum freshness_age_days across datasets for this source.",
                unit="days",
            ),
            _m(
                "authorization_status",
                "Authorization status",
                QualityDimension.CONNECTOR,
                auth,
                "Registry authorization class of the latest dataset for this source.",
                unit="label",
            ),
            _m(
                "source_type",
                "Source type",
                QualityDimension.SOURCE_HEALTH,
                source_type,
                "Source type key for this report.",
                unit="label",
            ),
        ]
        reports.append(
            SourceQualityReport(
                scope=ReportScope.SOURCE,
                hospital_id=hospital_id,
                hospital_name=hospital_name
                or next((s.hospital_name for s in items_sorted if s.hospital_name), None),
                source_type=source_type,
                provider=provider or None,
                dataset_count=len(items_sorted),
                latest_dataset_id=latest.dataset_id,
                latest_captured_at=latest.created_at,
                authorization_status=auth,
                metrics=metrics,
            )
        )
    return reports


# ---------------------------------------------------------------------------
# Hospital report
# ---------------------------------------------------------------------------


def build_hospital_quality_report(
    db: HospitalDatabase,
    hospital_id: str,
    *,
    hospital_name: str | None = None,
    now: datetime | None = None,
) -> HospitalQualityReport:
    """Aggregate dataset + source quality plus pipeline counters for one hospital."""
    now = now or datetime.now(UTC)
    summaries = [s for s in db.list_datasets() if s.hospital_id == hospital_id]
    if not summaries:
        raise HospitalQualityNotFoundError(
            f"no stored datasets for hospital {hospital_id!r}"
        )

    name = hospital_name
    if name is None:
        name = next((s.hospital_name for s in summaries if s.hospital_name), None)
    hospital_key = next((s.hospital_key for s in summaries if s.hospital_key), "")

    dataset_reports = [build_dataset_quality_report(db, s, now=now) for s in summaries]
    source_reports = build_source_quality_reports(
        db, hospital_id, hospital_name=name, now=now
    )

    snapshots = db.list_snapshots(hospital_id)
    change_sets = db.list_change_sets(hospital_id)
    jobs = db.list_verification_jobs(hospital_id)

    from hospital_extractor.monitoring.service import list_policies
    from hospital_extractor.reconciliation.service import list_cases

    policies = list_policies(db, hospital_id=hospital_id)
    cases = list_cases(db, hospital_id=hospital_id)
    runs = db.list_orchestration_runs(hospital_id=hospital_id, limit=1000)
    reports_rows = db.list_reports(hospital_id=hospital_id, limit=1000)

    # Change frequency: count CHANGES_DETECTED among change sets + span days.
    changes_detected = sum(
        1 for cs in change_sets if cs.status.value == "changes_detected"
    )
    change_span_days: float | None = None
    if len(change_sets) >= 2:
        times = sorted(cs.detected_at for cs in change_sets)
        change_span_days = round(
            (times[-1] - times[0]).total_seconds() / 86400.0, 6
        )
    elif len(change_sets) == 1:
        change_span_days = 0.0

    # Verification outcomes by job status.
    job_status_counts: dict[str, int] = {}
    for job in jobs:
        job_status_counts[job.status.value] = job_status_counts.get(job.status.value, 0) + 1

    case_status_counts: dict[str, int] = {}
    for case in cases:
        case_status_counts[case.status.value] = case_status_counts.get(case.status.value, 0) + 1

    policy_enabled = sum(1 for p in policies if p.enabled)
    policy_failed = sum(1 for p in policies if p.status.value == "failed")
    policy_success = sum(1 for p in policies if p.status.value == "succeeded")

    run_status_counts: dict[str, int] = {}
    for run in runs:
        run_status_counts[run.status.value] = run_status_counts.get(run.status.value, 0) + 1
    runs_failed = run_status_counts.get("failed", 0)
    runs_completed_like = (
        run_status_counts.get("completed", 0)
        + run_status_counts.get("no_change", 0)
    )

    # Freshness across datasets.
    ages = [
        age
        for dr in dataset_reports
        if (age := _as_float(dr.metric_value("freshness_age_days"))) is not None
    ]
    newest_age = min(ages) if ages else None
    latest_captured = max((s.created_at for s in summaries), default=None)

    # Snapshot health: distinct source types with at least one snapshot vs datasets.
    snapshot_sources = {s.source_type for s in snapshots}
    dataset_sources = {s.source_type for s in summaries}

    metrics = [
        _m(
            "dataset_count",
            "Datasets",
            QualityDimension.DATASET,
            len(summaries),
            "Stored dataset rows resolved to this hospital identity.",
        ),
        _m(
            "source_count",
            "Distinct sources",
            QualityDimension.SOURCE_HEALTH,
            len({(s.source_type, s.provider or "") for s in summaries}),
            "Distinct (source_type, provider) pairs for this hospital.",
        ),
        _m(
            "snapshot_count",
            "Snapshots",
            QualityDimension.SNAPSHOT,
            len(snapshots),
            "Immutable snapshot rows for this hospital.",
        ),
        _m(
            "snapshot_source_coverage_rate",
            "Snapshot source coverage",
            QualityDimension.SNAPSHOT,
            _rate(len(snapshot_sources & dataset_sources), len(dataset_sources)),
            "Distinct dataset source_types with ≥1 snapshot / distinct dataset source_types.",
            unit="ratio",
        ),
        _m(
            "change_set_count",
            "Change sets",
            QualityDimension.CHANGE_FREQUENCY,
            len(change_sets),
            "Change-detection runs recorded for this hospital.",
        ),
        _m(
            "changes_detected_count",
            "Change sets with changes detected",
            QualityDimension.CHANGE_FREQUENCY,
            changes_detected,
            "Change sets whose status is changes_detected.",
        ),
        _m(
            "change_set_span_days",
            "Change-set time span (days)",
            QualityDimension.CHANGE_FREQUENCY,
            change_span_days,
            "Days between oldest and newest change_set.detected_at; "
            "0.0 for a single change set; null when none.",
            unit="days",
        ),
        _m(
            "change_frequency_per_day",
            "Changes detected per day",
            QualityDimension.CHANGE_FREQUENCY,
            _rate(changes_detected, change_span_days)
            if change_span_days is not None and change_span_days > 0
            else None,
            "changes_detected_count / change_set_span_days; null when span is 0 or missing.",
            unit="ratio",
        ),
        _m(
            "verification_job_count",
            "Verification jobs",
            QualityDimension.VERIFICATION,
            len(jobs),
            "Verification job rows for this hospital.",
        ),
        _m(
            "verification_status_counts",
            "Verification job statuses",
            QualityDimension.VERIFICATION,
            ", ".join(f"{k}={v}" for k, v in sorted(job_status_counts.items())) or "none",
            "Counts keyed by VerificationJobStatus value.",
            unit="label",
        ),
        _m(
            "reconciliation_case_count",
            "Reconciliation cases",
            QualityDimension.RECONCILIATION,
            len(cases),
            "Reconciliation case rows for this hospital.",
        ),
        _m(
            "reconciliation_status_counts",
            "Reconciliation case statuses",
            QualityDimension.RECONCILIATION,
            ", ".join(f"{k}={v}" for k, v in sorted(case_status_counts.items())) or "none",
            "Counts keyed by ReconciliationCaseStatus value.",
            unit="label",
        ),
        _m(
            "monitoring_policy_count",
            "Monitoring policies",
            QualityDimension.MONITORING,
            len(policies),
            "Monitoring policy rows for this hospital.",
        ),
        _m(
            "monitoring_enabled_count",
            "Monitoring policies enabled",
            QualityDimension.MONITORING,
            policy_enabled,
            "Policies with enabled=True.",
        ),
        _m(
            "monitoring_failed_count",
            "Monitoring policies failed",
            QualityDimension.MONITORING,
            policy_failed,
            "Policies whose status is failed.",
        ),
        _m(
            "monitoring_succeeded_count",
            "Monitoring policies succeeded",
            QualityDimension.MONITORING,
            policy_success,
            "Policies whose status is succeeded.",
        ),
        _m(
            "orchestration_run_count",
            "Orchestration runs",
            QualityDimension.PIPELINE,
            len(runs),
            "Orchestration run rows for this hospital.",
        ),
        _m(
            "orchestration_failed_count",
            "Orchestration runs failed",
            QualityDimension.PIPELINE,
            runs_failed,
            "Runs whose status is failed.",
        ),
        _m(
            "orchestration_success_like_count",
            "Orchestration runs completed/no_change",
            QualityDimension.PIPELINE,
            runs_completed_like,
            "Runs whose status is completed or no_change.",
        ),
        _m(
            "report_count",
            "Stored reports",
            QualityDimension.PIPELINE,
            len(reports_rows),
            "Report rows generated for this hospital.",
        ),
        _m(
            "newest_freshness_age_days",
            "Newest capture age (days)",
            QualityDimension.FRESHNESS,
            newest_age,
            "Minimum dataset freshness_age_days for this hospital.",
            unit="days",
        ),
        _m(
            "average_hospital_field_coverage_rate",
            "Average hospital field coverage",
            QualityDimension.FIELD_COVERAGE,
            _mean_of(
                [
                    dr.metric_value("hospital_field_coverage_rate")
                    for dr in dataset_reports
                    if dr.metric_value("hospital_field_coverage_rate") is not None
                ]
            ),
            "Mean of dataset hospital_field_coverage_rate values.",
            unit="ratio",
        ),
        _m(
            "average_unknown_field_rate",
            "Average unknown field rate",
            QualityDimension.UNKNOWN_RATE,
            _mean_of(
                [
                    dr.metric_value("unknown_field_rate")
                    for dr in dataset_reports
                    if dr.metric_value("unknown_field_rate") is not None
                ]
            ),
            "Mean of dataset unknown_field_rate values.",
            unit="ratio",
        ),
        _m(
            "conflicts_detected_sum",
            "Conflicts detected (sum)",
            QualityDimension.CONFLICT,
            sum(int(dr.metric_value("conflicts_detected", 0) or 0) for dr in dataset_reports),
            "Sum of conflicts_detected across datasets.",
        ),
        _m(
            "conflicts_unresolved_sum",
            "Conflicts unresolved (sum)",
            QualityDimension.CONFLICT,
            sum(
                int(dr.metric_value("conflicts_unresolved", 0) or 0)
                for dr in dataset_reports
            ),
            "Sum of conflicts_unresolved across datasets.",
        ),
        _m(
            "records_extracted_sum",
            "Records extracted (sum)",
            QualityDimension.EXTRACTION,
            sum(int(dr.metric_value("records_extracted", 0) or 0) for dr in dataset_reports),
            "Sum of records_extracted across datasets.",
        ),
    ]

    return HospitalQualityReport(
        scope=ReportScope.HOSPITAL,
        hospital_id=hospital_id,
        hospital_name=name,
        hospital_key=hospital_key,
        dataset_count=len(summaries),
        source_count=len({(s.source_type, s.provider or "") for s in summaries}),
        latest_captured_at=latest_captured,
        dataset_reports=dataset_reports,
        source_reports=source_reports,
        metrics=metrics,
    )


def _mean_of(values: list[Any]) -> float | None:
    floats = [f for v in values if (f := _as_float(v)) is not None]
    if not floats:
        return None
    return round(sum(floats) / len(floats), 6)


# ---------------------------------------------------------------------------
# Pipeline report
# ---------------------------------------------------------------------------


def build_pipeline_quality_report(
    db: HospitalDatabase,
    *,
    now: datetime | None = None,
) -> PipelineQualityReport:
    """Whole-store metrics: dataset/hospital counts and pipeline stage outcomes."""
    now = now or datetime.now(UTC)
    summaries = db.list_datasets()

    hospital_ids = sorted({s.hospital_id for s in summaries if s.hospital_id})
    hospital_count = len(hospital_ids)

    # Dataset-level aggregates (load payloads once).
    dataset_reports = []
    for summary in summaries:
        dataset = db.load_dataset(summary.dataset_id)
        dataset_reports.append(build_dataset_quality_report(db, summary, dataset, now=now))

    records_sum = sum(
        int(dr.metric_value("records_extracted", 0) or 0) for dr in dataset_reports
    )
    conflicts_sum = sum(
        int(dr.metric_value("conflicts_detected", 0) or 0) for dr in dataset_reports
    )
    unresolved_sum = sum(
        int(dr.metric_value("conflicts_unresolved", 0) or 0) for dr in dataset_reports
    )
    unknown_rates = [
        rate
        for dr in dataset_reports
        if (rate := _as_float(dr.metric_value("unknown_field_rate"))) is not None
    ]
    coverage_rates = [
        rate
        for dr in dataset_reports
        if (rate := _as_float(dr.metric_value("hospital_field_coverage_rate"))) is not None
    ]
    ages = [
        age
        for dr in dataset_reports
        if (age := _as_float(dr.metric_value("freshness_age_days"))) is not None
    ]

    # Cross-store counters. list_* without hospital filter where supported.
    snapshot_count = 0
    change_set_count = 0
    verification_job_count = 0
    reconciliation_case_count = 0
    monitoring_policy_count = 0
    report_count = 0
    orchestration_run_count = 0

    for hospital_id in hospital_ids:
        snapshot_count += len(db.list_snapshots(hospital_id, limit=1000))
        change_set_count += len(db.list_change_sets(hospital_id, limit=1000))
        verification_job_count += len(db.list_verification_jobs(hospital_id, limit=1000))

    # Global lists (no hospital filter) where the store exposes them.
    report_count = len(db.list_reports(limit=10000))
    orchestration_run_count = len(db.list_orchestration_runs(limit=10000))

    try:
        from hospital_extractor.monitoring.service import list_policies
        from hospital_extractor.reconciliation.service import list_cases

        monitoring_policy_count = len(list_policies(db))
        reconciliation_case_count = len(list_cases(db))
    except Exception:  # noqa: BLE001 — optional subsystems must not break reports
        monitoring_policy_count = 0
        reconciliation_case_count = 0

    # Verification / reconciliation / monitoring outcome rollups.
    job_status: dict[str, int] = {}
    for hospital_id in hospital_ids:
        for job in db.list_verification_jobs(hospital_id, limit=1000):
            job_status[job.status.value] = job_status.get(job.status.value, 0) + 1

    case_status: dict[str, int] = {}
    if reconciliation_case_count:
        try:
            from hospital_extractor.reconciliation.service import list_cases

            for case in list_cases(db):
                case_status[case.status.value] = case_status.get(case.status.value, 0) + 1
        except Exception:  # noqa: BLE001
            case_status = {}

    policy_status: dict[str, int] = {}
    if monitoring_policy_count:
        try:
            from hospital_extractor.monitoring.service import list_policies

            for policy in list_policies(db):
                policy_status[policy.status.value] = policy_status.get(policy.status.value, 0) + 1
        except Exception:  # noqa: BLE001
            policy_status = {}

    run_status: dict[str, int] = {}
    for run in db.list_orchestration_runs(limit=10000):
        run_status[run.status.value] = run_status.get(run.status.value, 0) + 1

    # Snapshot health: total snapshots vs hospitals that have any.
    hospitals_with_snapshot = {
        s.hospital_id
        for hospital_id in hospital_ids
        for s in db.list_snapshots(hospital_id, limit=1)
    }

    # Source authorization mix from dataset reports.
    auth_counts: dict[str, int] = {}
    for dr in dataset_reports:
        auth = str(dr.metric_value("authorization_status", "UNKNOWN") or "UNKNOWN")
        auth_counts[auth] = auth_counts.get(auth, 0) + 1

    metrics = [
        _m(
            "hospital_count",
            "Hospitals",
            QualityDimension.DATASET,
            hospital_count,
            "Distinct hospital_id values among stored datasets.",
        ),
        _m(
            "dataset_count",
            "Datasets",
            QualityDimension.DATASET,
            len(summaries),
            "Stored dataset rows in the store.",
        ),
        _m(
            "snapshot_count",
            "Snapshots",
            QualityDimension.SNAPSHOT,
            snapshot_count,
            "Snapshot rows across all known hospitals.",
        ),
        _m(
            "hospitals_with_snapshot",
            "Hospitals with ≥1 snapshot",
            QualityDimension.SNAPSHOT,
            len(hospitals_with_snapshot),
            "Hospitals that have at least one snapshot.",
        ),
        _m(
            "snapshot_hospital_coverage_rate",
            "Snapshot hospital coverage",
            QualityDimension.SNAPSHOT,
            _rate(len(hospitals_with_snapshot), hospital_count),
            "hospitals_with_snapshot / hospital_count.",
            unit="ratio",
        ),
        _m(
            "change_set_count",
            "Change sets",
            QualityDimension.CHANGE_FREQUENCY,
            change_set_count,
            "Change-detection rows across all hospitals.",
        ),
        _m(
            "verification_job_count",
            "Verification jobs",
            QualityDimension.VERIFICATION,
            verification_job_count,
            "Verification job rows across all hospitals.",
        ),
        _m(
            "verification_status_counts",
            "Verification job statuses",
            QualityDimension.VERIFICATION,
            ", ".join(f"{k}={v}" for k, v in sorted(job_status.items())) or "none",
            "Counts keyed by VerificationJobStatus value.",
            unit="label",
        ),
        _m(
            "reconciliation_case_count",
            "Reconciliation cases",
            QualityDimension.RECONCILIATION,
            reconciliation_case_count,
            "Reconciliation case rows in the store.",
        ),
        _m(
            "reconciliation_status_counts",
            "Reconciliation case statuses",
            QualityDimension.RECONCILIATION,
            ", ".join(f"{k}={v}" for k, v in sorted(case_status.items())) or "none",
            "Counts keyed by ReconciliationCaseStatus value.",
            unit="label",
        ),
        _m(
            "monitoring_policy_count",
            "Monitoring policies",
            QualityDimension.MONITORING,
            monitoring_policy_count,
            "Monitoring policy rows in the store.",
        ),
        _m(
            "monitoring_status_counts",
            "Monitoring policy statuses",
            QualityDimension.MONITORING,
            ", ".join(f"{k}={v}" for k, v in sorted(policy_status.items())) or "none",
            "Counts keyed by MonitoringPolicyStatus value.",
            unit="label",
        ),
        _m(
            "report_count",
            "Reports",
            QualityDimension.PIPELINE,
            report_count,
            "Stored report rows.",
        ),
        _m(
            "orchestration_run_count",
            "Orchestration runs",
            QualityDimension.PIPELINE,
            orchestration_run_count,
            "Orchestration run rows.",
        ),
        _m(
            "orchestration_status_counts",
            "Orchestration run statuses",
            QualityDimension.PIPELINE,
            ", ".join(f"{k}={v}" for k, v in sorted(run_status.items())) or "none",
            "Counts keyed by OrchestrationStatus value.",
            unit="label",
        ),
        _m(
            "orchestration_failed_count",
            "Orchestration runs failed",
            QualityDimension.PIPELINE,
            run_status.get("failed", 0),
            "Runs whose status is failed.",
        ),
        _m(
            "orchestration_success_like_count",
            "Orchestration runs completed/no_change",
            QualityDimension.PIPELINE,
            run_status.get("completed", 0) + run_status.get("no_change", 0),
            "Runs whose status is completed or no_change.",
        ),
        _m(
            "records_extracted_sum",
            "Records extracted (sum)",
            QualityDimension.EXTRACTION,
            records_sum,
            "Sum of records_extracted across all datasets.",
        ),
        _m(
            "conflicts_detected_sum",
            "Conflicts detected (sum)",
            QualityDimension.CONFLICT,
            conflicts_sum,
            "Sum of conflicts_detected across all datasets.",
        ),
        _m(
            "conflicts_unresolved_sum",
            "Conflicts unresolved (sum)",
            QualityDimension.CONFLICT,
            unresolved_sum,
            "Sum of conflicts_unresolved across all datasets.",
        ),
        _m(
            "conflict_rate",
            "Conflict rate",
            QualityDimension.CONFLICT,
            _rate(conflicts_sum, max(records_sum, 1)) or 0.0,
            "conflicts_detected_sum / max(records_extracted_sum, 1).",
            unit="ratio",
        ),
        _m(
            "average_hospital_field_coverage_rate",
            "Average hospital field coverage",
            QualityDimension.FIELD_COVERAGE,
            _mean_of(coverage_rates),
            "Mean of per-dataset hospital_field_coverage_rate values.",
            unit="ratio",
        ),
        _m(
            "average_unknown_field_rate",
            "Average unknown field rate",
            QualityDimension.UNKNOWN_RATE,
            _mean_of(unknown_rates),
            "Mean of per-dataset unknown_field_rate values.",
            unit="ratio",
        ),
        _m(
            "newest_freshness_age_days",
            "Newest capture age (days)",
            QualityDimension.FRESHNESS,
            min(ages) if ages else None,
            "Minimum freshness_age_days across datasets.",
            unit="days",
        ),
        _m(
            "oldest_freshness_age_days",
            "Oldest capture age (days)",
            QualityDimension.FRESHNESS,
            max(ages) if ages else None,
            "Maximum freshness_age_days across datasets.",
            unit="days",
        ),
        _m(
            "authorization_status_counts",
            "Dataset authorization mix",
            QualityDimension.CONNECTOR,
            ", ".join(f"{k}={v}" for k, v in sorted(auth_counts.items())) or "none",
            "Count of datasets per connector authorization class.",
            unit="label",
        ),
    ]

    return PipelineQualityReport(
        scope=ReportScope.PIPELINE,
        hospital_count=hospital_count,
        dataset_count=len(summaries),
        snapshot_count=snapshot_count,
        change_set_count=change_set_count,
        verification_job_count=verification_job_count,
        reconciliation_case_count=reconciliation_case_count,
        monitoring_policy_count=monitoring_policy_count,
        report_count=report_count,
        orchestration_run_count=orchestration_run_count,
        metrics=metrics,
    )
