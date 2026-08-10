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

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hospital_extractor.config import ExtractionConfig, association_rank
from hospital_extractor.extraction.accreditations import extract_accreditations
from hospital_extractor.extraction.awards import extract_awards
from hospital_extractor.extraction.contact import extract_contact
from hospital_extractor.extraction.departments import extract_departments
from hospital_extractor.extraction.doctors import HospitalContext, extract_doctors
from hospital_extractor.extraction.emergency import extract_emergency_services
from hospital_extractor.extraction.facilities import extract_facilities
from hospital_extractor.extraction.hospital import (
    extract_hospital_overview,
    extract_infrastructure,
)
from hospital_extractor.extraction.insurance import extract_insurance
from hospital_extractor.extraction.other import extract_other
from hospital_extractor.extraction.page_analyzer import AnalyzedPage, analyze_page
from hospital_extractor.extraction.procedures import extract_procedures
from hospital_extractor.extraction.programs import extract_programs
from hospital_extractor.extraction.services import extract_services
from hospital_extractor.extraction.specialities import extract_specialities
from hospital_extractor.extraction.technology import extract_technologies
from hospital_extractor.extraction.treatments import extract_treatments
from hospital_extractor.extraction.visiting import extract_visiting
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.quality import DataQuality
from hospital_extractor.models.source import SourceRecord
from hospital_extractor.processing.conflicts import merge_single_record
from hospital_extractor.processing.deduplicate import deduplicate_category
from hospital_extractor.processing.normalize import normalize_record
from hospital_extractor.processing.validate import dataset_issues, validate_records
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES
from hospital_extractor.utils.urls import validate_url

__all__ = ["build_dataset", "analyze_pages", "pages_to_analyzed"]

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
    "emergency": (extract_emergency_services,),
    "patient_service": (extract_services,),
    "insurance": (extract_insurance,),
    "accreditation": (extract_accreditations,),
    "award": (extract_awards,),
    "program": (extract_programs,),
    "visiting": (extract_visiting,),
    "faq": (
        extract_visiting,
        extract_insurance,
        extract_emergency_services,
        extract_facilities,
        extract_services,
        extract_programs,
        extract_other,
    ),
}


@dataclass
class _AnalyzedPageWithCategory:
    page: AnalyzedPage
    category: str


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
) -> HospitalDataset:
    """Build a complete :class:`HospitalDataset` from ``(url, html)`` pages."""
    cfg = config or ExtractionConfig()
    target = validate_url(target_url)
    analyzed = pages_to_analyzed(pages, classifier=classifier)
    results: dict[str, list[object]] = {category.key: [] for category in CANONICAL_CATEGORIES}

    quality = DataQuality(pages_considered=len(analyzed))

    _first_pass(analyzed, results, target=target, cfg=cfg)
    hospital_context = _hospital_context(results, target)
    _second_pass(analyzed, results, context=hospital_context, cfg=cfg)

    dataset = _assemble(results, cfg=cfg, quality=quality)
    return dataset


def _first_pass(
    analyzed: Sequence[_AnalyzedPageWithCategory],
    results: dict[str, list[object]],
    *,
    target: str,
    cfg: ExtractionConfig,
) -> None:
    """Extract hospital identity + contact first so doctors can be judged."""
    for entry in analyzed:
        category = entry.category
        if category == "hospital":
            overview = extract_hospital_overview(entry.page, website=target)
            if overview is not None:
                results["hospital"].append(overview)
            infrastructure = extract_infrastructure(entry.page)
            if infrastructure is not None:
                results["infrastructure"].append(infrastructure)
        elif category == "contact":
            contact = extract_contact(entry.page)
            if contact is not None:
                results["contacts"].append(contact)


def _second_pass(
    analyzed: Sequence[_AnalyzedPageWithCategory],
    results: dict[str, list[object]],
    *,
    context: HospitalContext,
    cfg: ExtractionConfig,
) -> None:
    for entry in analyzed:
        category = entry.category
        if category not in _ROUTING:
            continue
        extractors = _ROUTING[category]
        records: object | None = None
        for extractor in extractors:
            if extractor is extract_doctors:
                records = extract_doctors(entry.page, context)
            elif extractor is extract_other:
                records = extract_other(entry.page, min_answer_length=cfg.faq_min_answer_length)
            else:
                records = extractor(entry.page)
            if records is None:
                continue
            if isinstance(records, list):
                for record in records:
                    _store(results, record)
            else:
                _store(results, records)


def _store(results: dict[str, list[object]], record: object) -> None:
    for category in CANONICAL_CATEGORIES:
        if isinstance(record, category.model):
            results[category.key].append(record)
            return


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
) -> HospitalDataset:
    dataset = HospitalDataset()
    quality.records_extracted = sum(len(records) for records in results.values())

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
        accepted = validation.accepted
        _set_dataset_field(dataset, category.key, accepted)
        _count_low_confidence(quality, accepted)
        if category.key == "doctors":
            quality.uncertain_hospital_associations += sum(
                1
                for record in accepted
                if getattr(record, "hospital_association", None) == "uncertain"
            )

    _finalize_quality(dataset, quality)
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


def _finalize_quality(dataset: HospitalDataset, quality: DataQuality) -> None:
    from hospital_extractor.schema.discovery import (
        discover_categories,
        empty_categories,
    )

    dataset.quality = quality
    quality.categories_discovered = discover_categories(dataset)
    quality.categories_empty = empty_categories(dataset)

    issues = dataset_issues(dataset)
    quality.warnings.extend(issue.message for issue in issues if issue.severity == "warning")

    dataset.sources = _build_sources(dataset)
    quality.pages_extracted = len({source.source_url for source in dataset.sources})


def _build_sources(dataset: HospitalDataset) -> list[SourceRecord]:
    from hospital_extractor.schema.discovery import category_records

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
