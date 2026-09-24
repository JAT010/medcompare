"""Extraction engine.

Consumes the crawler's results (no fetching happens here) and turns classified
pages into a normalized :class:`HospitalDataset`: each page is analyzed once,
routed to the matching extractors, and the collected records flow through
normalize -> deduplicate/merge -> validate -> quality + sources.

Page category is the dispatch key (doctor pages -> doctors extractor, ...);
FAQ pages route their question/answer content across the FAQ-driven
categories. The engine never fabricates values and never resolves conflicts.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from hospital_extractor.config import ExtractionConfig, association_rank
from hospital_extractor.extraction.accreditations import extract_accreditations
from hospital_extractor.extraction.contact import extract_contact
from hospital_extractor.extraction.departments import extract_departments
from hospital_extractor.extraction.doctors import HospitalContext, extract_doctors
from hospital_extractor.extraction.facilities import extract_facilities
from hospital_extractor.extraction.hospital import (
    extract_hospital_overview,
    extract_infrastructure,
)
from hospital_extractor.extraction.page_analyzer import (
    AnalyzedPage,
    _same_url,
    analyze_page,
    is_site_root,
    is_target_scoped,
)
from hospital_extractor.extraction.procedures import extract_procedures
from hospital_extractor.extraction.specialities import extract_specialities
from hospital_extractor.extraction.tables import extract_table_records
from hospital_extractor.extraction.technology import extract_technologies
from hospital_extractor.extraction.treatments import extract_treatments
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.quality import DataQuality
from hospital_extractor.models.source import SourceRecord
from hospital_extractor.processing.conflicts import merge_single_record
from hospital_extractor.processing.deduplicate import deduplicate_category
from hospital_extractor.processing.normalize import normalize_record
from hospital_extractor.processing.validate import dataset_issues, validate_records
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES, CATEGORY_LABELS
from hospital_extractor.schema.discovery import category_records
from hospital_extractor.utils.urls import validate_url

__all__ = [
    "CrawlStats",
    "build_dataset",
    "analyze_pages",
    "pages_to_analyzed",
]

if TYPE_CHECKING:
    from hospital_extractor.crawler.classify import PageClassifier

# Page category -> extractors to run (order matters: specific before generic).
# Hospital overview/infrastructure/contact are extracted in the first pass
# (their records feed the doctor-association context), so they are not listed.
_ROUTING: dict[str, tuple[Callable[..., Sequence[object] | object | None], ...]] = {
    "doctor": (extract_doctors,),
    "speciality": (extract_specialities,),
    "department": (extract_departments,),
    "facility": (extract_facilities,),
    "treatment": (extract_treatments,),
    "procedure": (extract_procedures,),
    "technology": (extract_technologies,),
    "accreditation": (extract_accreditations,),
    "faq": (extract_facilities,),
}

# Classified page category -> canonical category keys it attempts to populate.
_PAGE_TO_CATEGORY_KEYS: dict[str, tuple[str, ...]] = {
    "hospital": ("hospital", "infrastructure"),
    "contact": ("contacts",),
    "doctor": ("doctors",),
    "speciality": ("specialities",),
    "department": ("departments",),
    "facility": ("facilities",),
    "treatment": ("treatments",),
    "procedure": ("procedures",),
    "technology": ("technologies",),
    "accreditation": ("accreditations",),
    "faq": ("facilities",),
}

# A fetched page shorter than this is considered chrome/empty, not content that
# "failed" extraction.
_MIN_CONTENT_CHARS = 200
_MAX_WARNINGS = 50


@dataclass
class _AnalyzedPageWithCategory:
    page: AnalyzedPage
    category: str


@dataclass(frozen=True)
class CrawlStats:
    """Optional crawl-phase counters fed into the quality metadata.

    When absent, the engine falls back to the number of analyzed pages.
    """

    pages_discovered: int = 0
    pages_fetched: int = 0
    pages_failed: int = 0
    pages_skipped: int = 0


def pages_to_analyzed(
    pages: Sequence[tuple[str, str]],
    *,
    classifier: PageClassifier | None = None,
) -> list[_AnalyzedPageWithCategory]:
    """Analyze each ``(url, html)`` page once and classify it."""
    from hospital_extractor.crawler.classify import PageClassifier

    cls = classifier or PageClassifier()
    result: list[_AnalyzedPageWithCategory] = []
    for url, html in pages:
        page = analyze_page(html, url)
        category = cls.from_content(html, url).category
        page = replace(page, page_category=category)
        result.append(_AnalyzedPageWithCategory(page=page, category=category))
    return result


def analyze_pages(
    pages: Sequence[tuple[str, str]],
    *,
    classifier: PageClassifier | None = None,
) -> list[AnalyzedPage]:
    """Analyze ``(url, html)`` pages, keeping order (used by CLI/export)."""
    return [entry.page for entry in pages_to_analyzed(pages, classifier=classifier)]


def build_dataset(
    pages: Sequence[tuple[str, str]],
    *,
    target_url: str,
    config: ExtractionConfig | None = None,
    classifier: PageClassifier | None = None,
    crawl_stats: CrawlStats | None = None,
) -> HospitalDataset:
    """Build a complete :class:`HospitalDataset` from ``(url, html)`` pages.

    ``crawl_stats`` optionally carries crawl-phase counters (pages fetched /
    failed) so the quality metadata reflects the whole run; when omitted the
    analyzed page count is used.
    """
    cfg = config or ExtractionConfig()
    target = validate_url(target_url)
    analyzed = pages_to_analyzed(pages, classifier=classifier)
    results: dict[str, list[object]] = {category.key: [] for category in CANONICAL_CATEGORIES}

    quality = DataQuality(pages_considered=len(analyzed))
    if crawl_stats is not None:
        quality.pages_crawled = crawl_stats.pages_fetched
        quality.pages_classified = crawl_stats.pages_fetched
        quality.pages_parsed = len(analyzed)
    else:
        quality.pages_crawled = len(analyzed)
        quality.pages_classified = len(analyzed)
        quality.pages_parsed = len(analyzed)

    attempted = _attempted_category_keys(analyzed)
    _first_pass(analyzed, results, target=target, cfg=cfg)
    hospital_context = _hospital_context(results, target)
    hospital_name = _overview_name(results)
    scoped_analyzed = [
        entry
        for entry in analyzed
        if is_target_scoped(entry.page, anchor_url=target, hospital_name=hospital_name)
    ]
    _second_pass(scoped_analyzed, results, context=hospital_context)
    _table_pass(scoped_analyzed, results)

    dataset = _assemble(
        results,
        cfg=cfg,
        quality=quality,
        analyzed=analyzed,
        attempted=attempted,
        crawl_stats=crawl_stats,
    )
    return dataset


def _overview_name(results: dict[str, list[object]]) -> str | None:
    if not results["hospital"]:
        return None
    name = getattr(results["hospital"][0], "name", None)
    return name if isinstance(name, str) and name else None


def _attempted_category_keys(
    analyzed: Sequence[_AnalyzedPageWithCategory],
) -> set[str]:
    """Canonical category keys that at least one classified page attempted."""
    attempted: set[str] = set()
    for entry in analyzed:
        attempted.update(_PAGE_TO_CATEGORY_KEYS.get(entry.category, ()))
    return attempted


def _first_pass(
    analyzed: Sequence[_AnalyzedPageWithCategory],
    results: dict[str, list[object]],
    *,
    target: str,
    cfg: ExtractionConfig,
) -> None:
    """Extract hospital identity + contact first so doctors can be judged.

    Identity is only taken from target-scoped pages: the anchor page and any
    page that names the hospital. The site root (corporate chrome) and
    other-city hospital/contact pages never feed the master record. The anchor
    page carries the hospital-specific ``LocalBusiness`` JSON-LD (telephone,
    address, geo), so both ``hospital`` and ``contact`` extraction run on it;
    the network-wide ``MedicalOrganization`` block is deliberately not
    hospital-specific enough to override the anchor's identity.
    """
    anchors = [
        entry for entry in analyzed if _same_url(entry.page.url, target)
    ]
    if not anchors:
        # Anchor URL drift (redirect / trailing slash): fall back to the first
        # non-root hospital-classified page so identity is never lost.
        anchors = [
            entry
            for entry in analyzed
            if entry.category == "hospital" and not is_site_root(entry.page.url)
        ]
    for entry in anchors:
        _extract_identity(entry, results, target=target, is_anchor=True)

    name = _overview_name(results)
    for entry in analyzed:
        if entry in anchors:
            continue
        if not is_target_scoped(entry.page, anchor_url=target, hospital_name=name):
            continue
        _extract_identity(entry, results, target=target, is_anchor=False)


def _extract_identity(
    entry: _AnalyzedPageWithCategory,
    results: dict[str, list[object]],
    *,
    target: str,
    is_anchor: bool = False,
) -> None:
    category = entry.category
    if is_anchor or category == "hospital":
        overview = extract_hospital_overview(entry.page, website=target)
        if overview is not None:
            results["hospital"].append(overview)
    elif category != "hospital":
        existing = _overview_name(results)
        if not existing:
            overview = extract_hospital_overview(entry.page, website=target)
            if overview is not None:
                results["hospital"].append(overview)
    if category == "hospital":
        infrastructure = extract_infrastructure(entry.page)
        if infrastructure is not None:
            results["infrastructure"].append(infrastructure)
        contact = extract_contact(entry.page)
        if contact is not None:
            results["contacts"].append(contact)
    elif category == "contact":
        contact = extract_contact(entry.page)
        if contact is not None:
            results["contacts"].append(contact)


def _second_pass(
    analyzed: Sequence[_AnalyzedPageWithCategory],
    results: dict[str, list[object]],
    *,
    context: HospitalContext,
) -> None:
    for entry in analyzed:
        category = entry.category
        if category in _ROUTING:
            extractors = _ROUTING[category]
            records: object | None = None
            for extractor in extractors:
                if extractor is extract_doctors:
                    records = extract_doctors(entry.page, context)
                else:
                    records = extractor(entry.page)
                if records is None:
                    continue
                if isinstance(records, list):
                    for record in records:
                        _store(results, record)
                else:
                    _store(results, records)
        _jsonld_sweep(entry, results, context)


_JSONLD_ENTITY_TYPES: dict[str, tuple[str, ...]] = {
    "Physician": ("doctor",),
    "Person": ("doctor",),
    "MedicalProcedure": ("procedure",),
    "MedicalTherapy": ("treatment",),
}


def _jsonld_sweep(
    entry: _AnalyzedPageWithCategory,
    results: dict[str, list[object]],
    context: HospitalContext,
) -> None:
    """Run extractors for entity types found in JSON-LD but not routed by classification.

    Pages classified as ``hospital`` or ``faq`` may contain rich JSON-LD
    (Physician, MedicalProcedure, ...) that the page-category routing never
    reaches.  This sweep checks the JSON-LD types and calls the matching
    extractors that were skipped by the primary routing.
    """
    category = entry.category
    page_jsonld_types = {
        block_type for block in entry.page.jsonld.blocks for block_type in block.types
    }
    seen_extractors: set[str] = set()
    for ld_type, extractor_categories in _JSONLD_ENTITY_TYPES.items():
        if ld_type not in page_jsonld_types:
            continue
        if category in extractor_categories:
            continue
        extractor_key = extractor_categories[0]
        if extractor_key in seen_extractors:
            continue
        seen_extractors.add(extractor_key)
        if extractor_key == "doctor":
            for doc in extract_doctors(entry.page, context):
                _store(results, doc)
        elif extractor_key == "procedure":
            for proc in extract_procedures(entry.page):
                _store(results, proc)
        elif extractor_key == "treatment":
            for treat in extract_treatments(entry.page):
                _store(results, treat)


def _store(results: dict[str, list[object]], record: object) -> None:
    for category in CANONICAL_CATEGORIES:
        if isinstance(record, category.model):
            results[category.key].append(record)
            return


def _table_pass(
    analyzed: Sequence[_AnalyzedPageWithCategory],
    results: dict[str, list[object]],
) -> None:
    """Extract facts from published HTML tables on every analyzed page.

    Runs after the routed extractors so table records join the same merge /
    dedupe / validate flow (single-record categories merge, list categories
    dedupe by identity).
    """
    for entry in analyzed:
        for record in extract_table_records(entry.page):
            _store(results, record)


def _hospital_context(results: dict[str, list[object]], target: str) -> HospitalContext:
    slug: str | None = None
    name: str | None = None
    city: str | None = None
    location_text: str | None = None

    overviews = results["hospital"]
    if overviews:
        overview = overviews[0]
        name = getattr(overview, "name", None)
        slug = _slug_from(target, name)

    contacts = results["contacts"]
    if contacts:
        contact = contacts[0]
        city = getattr(contact, "city", None)
        address = getattr(contact, "address", None)
        location_parts = [part for part in (city, address) if part]
        location_text = " ".join(location_parts).lower() if location_parts else None

    return HospitalContext(
        target_url=target,
        slug=slug,
        name=name,
        city=city,
        location_text=location_text,
    )


def _slug_from(target: str, name: str | None) -> str | None:
    from urllib.parse import urlsplit

    segments = [s for s in urlsplit(target).path.split("/") if s]
    if not segments:
        return None
    if segments[0] == "hospital-network" and len(segments) >= 2:
        return segments[1]
    return segments[-1]


def _assemble(
    results: dict[str, list[object]],
    *,
    cfg: ExtractionConfig,
    quality: DataQuality,
    analyzed: Sequence[_AnalyzedPageWithCategory],
    attempted: set[str],
    crawl_stats: CrawlStats | None,
) -> HospitalDataset:
    dataset = HospitalDataset()
    quality.records_extracted = sum(len(records) for records in results.values())
    rejected_by_category: Counter[str] = Counter()

    for category in CANONICAL_CATEGORIES:
        raw = results[category.key]
        if not raw:
            continue
        if category.single_record:
            candidates = _merge_single(category.key, raw, quality)
        else:
            candidates = _dedupe_category(category.key, raw, quality)
        if category.key == "doctors":
            candidates = _filter_doctors(candidates, cfg)
        validation = validate_records(category, candidates)
        quality.records_rejected += len(validation.rejected)
        rejected_by_category[category.key] += len(validation.rejected)
        accepted = validation.accepted
        _set_dataset_field(dataset, category.key, accepted)
        _count_low_confidence(quality, accepted)
        if category.key == "doctors":
            quality.uncertain_hospital_associations += sum(
                1
                for record in accepted
                if getattr(record, "hospital_association", None) == "uncertain"
            )

    _finalize_quality(
        dataset,
        quality,
        analyzed=analyzed,
        attempted=attempted,
        rejected_by_category=rejected_by_category,
        crawl_stats=crawl_stats,
    )
    return dataset


def _merge_single(key: str, raw: list[object], quality: DataQuality) -> list[object]:
    normalized = [normalize_record(key, record) for record in raw]
    merged, conflicts = merge_single_record(key, normalized)
    quality.conflicts.extend(conflicts)
    return [merged] if merged is not None else []


def _dedupe_category(key: str, raw: list[object], quality: DataQuality) -> list[object]:
    normalized = [normalize_record(key, record) for record in raw]
    result = deduplicate_category(key, normalized)
    quality.duplicates_removed += result.removed
    quality.conflicts.extend(result.conflicts)
    return result.records


def _filter_doctors(records: list[object], cfg: ExtractionConfig) -> list[object]:
    kept: list[object] = []
    min_rank = association_rank(cfg.doctor_association_include_min)
    for record in records:
        association = getattr(record, "hospital_association", None)
        rank = association_rank(association)
        if rank <= min_rank:
            kept.append(record)
        elif cfg.exclude_not_associated_doctors and association == "not_associated":
            continue
        elif association == "uncertain":
            continue
        else:
            kept.append(record)
    return kept


def _count_low_confidence(quality: DataQuality, records: list[object]) -> None:
    for record in records:
        strength = getattr(record, "source_strength", None)
        confidence = getattr(record, "confidence", None)
        if strength == "low" or (confidence is not None and confidence < 0.5):
            quality.low_confidence_records += 1


def _set_dataset_field(dataset: HospitalDataset, key: str, records: list[object]) -> None:
    if key == "hospital":
        dataset.hospital = records[0] if records else None  # type: ignore[assignment]
        return
    if records:
        setattr(dataset, key, records)


def _finalize_quality(
    dataset: HospitalDataset,
    quality: DataQuality,
    *,
    analyzed: Sequence[_AnalyzedPageWithCategory],
    attempted: set[str],
    rejected_by_category: Counter[str],
    crawl_stats: CrawlStats | None,
) -> None:
    from hospital_extractor.schema.discovery import (
        discover_categories,
        empty_categories,
    )
    from hospital_extractor.schema.missing import analyze_missing_fields

    dataset.quality = quality
    quality.categories_discovered = discover_categories(dataset)
    quality.categories_empty = empty_categories(dataset)

    quality.records_by_category = {
        category.label: len(category_records(dataset, category))
        for category in CANONICAL_CATEGORIES
        if category_records(dataset, category)
    }
    quality.extraction_methods = _count_extraction_methods(dataset)

    dataset.sources = _build_sources(dataset)
    quality.pages_extracted = len({source.source_url for source in dataset.sources})
    quality.pages_with_information = quality.pages_extracted
    quality.pages_without_information = max(0, quality.pages_considered - quality.pages_extracted)

    content_failed_keys = _content_failed_category_keys(dataset, analyzed)
    quality.category_coverage = _category_coverage(
        dataset, attempted, rejected_by_category, content_failed_keys
    )
    quality.missing_fields = analyze_missing_fields(dataset, quality.category_coverage)

    issues = dataset_issues(dataset)
    quality.warnings.extend(issue.message for issue in issues if issue.severity == "warning")

    _collect_warnings(
        quality,
        analyzed=analyzed,
        content_failed_keys=content_failed_keys,
        crawl_stats=crawl_stats,
    )


def _content_failed_category_keys(
    dataset: HospitalDataset, analyzed: Sequence[_AnalyzedPageWithCategory]
) -> set[str]:
    """Category keys whose pages carried content but yielded no records.

    A page counts as "content with failed extraction" when it has meaningful
    visible text yet no record in the dataset points at it. Its classified
    category is then marked ``failed`` in the coverage report.
    """
    pages_with_records = {source.source_url for source in dataset.sources}
    failed: set[str] = set()
    for entry in analyzed:
        page = entry.page
        if page.url in pages_with_records:
            continue
        if len(page.visible_text) < _MIN_CONTENT_CHARS:
            continue
        failed.update(_PAGE_TO_CATEGORY_KEYS.get(entry.category, ()))
    return failed


def _category_coverage(
    dataset: HospitalDataset,
    attempted: set[str],
    rejected_by_category: Counter[str],
    content_failed_keys: set[str],
) -> dict[str, str]:
    """Map every canonical category label to a coverage status.

    ``found``: records exist. ``failed``: extraction was attempted (or records
    were produced) but nothing survived. ``not_applicable``: reserved for
    targets that explicitly do not offer the category (never inferred).
    ``not_found``: no page attempted the category and no records exist — the
    site simply does not publish it. ``not_found`` is deliberately distinct
    from ``failed``.
    """
    coverage: dict[str, str] = {}
    for category in CANONICAL_CATEGORIES:
        if category_records(dataset, category):
            status = "found"
        elif rejected_by_category.get(category.key, 0) > 0:
            status = "failed"
        elif category.key in content_failed_keys:
            status = "failed"
        else:
            status = "not_found"
        coverage[category.label] = status
    return coverage


def _collect_warnings(
    quality: DataQuality,
    *,
    analyzed: Sequence[_AnalyzedPageWithCategory],
    content_failed_keys: set[str],
    crawl_stats: CrawlStats | None,
) -> None:
    """Append non-fatal extraction warnings (the pipeline never stops on them)."""
    warnings = list(quality.warnings)
    seen: set[str] = set(warnings)

    def warn(message: str) -> None:
        if message not in seen and len(warnings) < _MAX_WARNINGS:
            seen.add(message)
            warnings.append(message)

    for entry in analyzed:
        page = entry.page
        if page.jsonld.malformed:
            warn(
                f"malformed JSON-LD ({page.jsonld.malformed} block(s) skipped) "
                f"on {page.url}"
            )
    for key in sorted(content_failed_keys):
        warn(f"page(s) with content but no records extracted for {key}")

    if quality.duplicates_removed:
        warn(
            f"suspicious duplicate records merged: {quality.duplicates_removed} removed"
        )
    if quality.uncertain_hospital_associations:
        warn(
            f"{quality.uncertain_hospital_associations} doctor(s) with uncertain "
            f"hospital association"
        )
    for conflict in quality.conflicts:
        if conflict.resolution_status == "unresolved":
            warn(
                f"unresolved conflict in "
                f"{CATEGORY_LABELS.get(conflict.category, conflict.category)} "
                f"'{conflict.field}': {conflict.primary} vs {conflict.alternative}"
            )
    if crawl_stats is not None and crawl_stats.pages_failed:
        warn(f"{crawl_stats.pages_failed} page(s) inaccessible or failed to fetch")

    quality.warnings = warnings


def _count_extraction_methods(dataset: HospitalDataset) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for category in CANONICAL_CATEGORIES:
        for record in category_records(dataset, category):
            method = getattr(record, "extraction_method", None)
            if isinstance(method, str):
                counts[method] += 1
    return dict(counts)


def _build_sources(dataset: HospitalDataset) -> list[SourceRecord]:
    sources: list[SourceRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for category in CANONICAL_CATEGORIES:
        records = category_records(dataset, category)
        for record in records:
            dump = getattr(record, "model_dump", None)
            if callable(dump):
                data = dict(dump(exclude={"extracted_at"}))
            elif isinstance(record, dict):
                data = dict(record)
            else:
                data = {}
            key = (category.label, str(data.get("source_url", "")), str(data.get("context", "")))
            if key in seen:
                continue
            seen.add(key)
            sources.append(
                SourceRecord(
                    entity=category.label,
                    source_url=data.get("source_url") or "",
                    page_title=data.get("page_title"),
                    context=data.get("context"),
                    extraction_method=data.get("extraction_method"),
                    source_strength=data.get("source_strength"),
                    confidence=data.get("confidence"),
                )
            )
    return sources
