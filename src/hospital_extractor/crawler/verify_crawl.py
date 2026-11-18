"""SSRF-protected website verification crawl, persisted as a crawl run.

Used by the verification worker to gather website evidence for a facility.
Mirrors the synchronous crawl endpoint's persistence rules (failed crawls are
persisted and never used as evidence) without sharing endpoint code.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from hospital_extractor.comparison.evidence import evidence_from_intake
from hospital_extractor.comparison.schemas import EvidenceItem
from hospital_extractor.config import load_config
from hospital_extractor.crawler.context import CrawlLimits, build_context
from hospital_extractor.crawler.runner import CrawlAbortedError, run_crawl
from hospital_extractor.crawler.ssrf import SSRFBlockedError, validate_public_url
from hospital_extractor.extraction import CrawlStats, analyze_pages, build_dataset
from hospital_extractor.extraction.intake import project_intake
from hospital_extractor.persistence.crawl.service import CrawlPersistenceService
from hospital_extractor.persistence.models_crawl import CrawlRunStatus
from hospital_extractor.persistence.registry.service import RegistryService
from hospital_extractor.utils.urls import InvalidURLError
from hospital_extractor.verification.evidence import WebsiteEvidence

__all__ = ["crawl_website_for_verification"]

ALL_CRITERIA_KEYS = (
    "location",
    "infrastructure",
    "department_expertise",
    "treatment_facilities",
    "diagnostic_facilities",
)


async def crawl_website_for_verification(
    session: Session,
    *,
    facility_id: uuid.UUID,
    target_url: str,
    max_pages: int = 15,
    output_dir: str = "data",
    crawl_timeout_seconds: float = 120.0,
    subject_name: str | None = None,
) -> WebsiteEvidence:
    """Crawl *target_url* with SSRF guards and return website evidence.

    Persists a crawl run either way; failures are persisted and returned as
    ``status="failed"`` so the caller can decide about retries.
    """
    _ = crawl_timeout_seconds  # enforced by the caller (worker)

    persistence = CrawlPersistenceService(session)
    facility = RegistryService(session).get_facility(facility_id)
    run = persistence.start_run(
        facility_id=facility_id,
        comparison_subject_id=None,
        subject_name=subject_name or facility.name,
        subject_location=facility.city,
        subject_website_url=target_url,
        target_url=target_url,
        requested_url=target_url,
        requested_max_pages=max_pages,
    )

    try:
        normalized = validate_public_url(target_url)
    except (SSRFBlockedError, InvalidURLError) as exc:
        persistence.finalize_run(
            run,
            status=CrawlRunStatus.FAILED,
            evidence=[],
            warnings=["Rejected by SSRF policy."],
            criteria_without_evidence=list(ALL_CRITERIA_KEYS),
            pages_fetched=0,
            hospital_name=None,
            failure_code="url_not_allowed",
            failure_message=str(exc),
        )
        return WebsiteEvidence(
            status="failed",
            target_url=target_url,
            crawl_run_id=run.id,
            hospital_name=None,
            pages_fetched=0,
            retrieved_at=datetime.now(UTC),
            evidence=[],
            warnings=[str(exc)],
        )

    try:
        limits = CrawlLimits(max_pages=max_pages)
        context = build_context(normalized, limits=limits)
        run_key = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        report = await run_crawl(
            context,
            output_dir=output_dir,
            run_id=run_key,
            url_guard=validate_public_url,
        )
    except CrawlAbortedError as exc:
        persistence.finalize_run(
            run,
            status=CrawlRunStatus.FAILED,
            evidence=[],
            warnings=[],
            criteria_without_evidence=list(ALL_CRITERIA_KEYS),
            pages_fetched=0,
            hospital_name=None,
            failure_code="target_unreachable",
            failure_message=str(exc),
        )
        return WebsiteEvidence(
            status="failed",
            target_url=normalized,
            crawl_run_id=run.id,
            pages_fetched=0,
            retrieved_at=datetime.now(UTC),
            warnings=[str(exc)],
        )
    except Exception:  # pragma: no cover - defensive boundary
        persistence.finalize_run(
            run,
            status=CrawlRunStatus.FAILED,
            evidence=[],
            warnings=[],
            criteria_without_evidence=list(ALL_CRITERIA_KEYS),
            pages_fetched=0,
            hospital_name=None,
            failure_code="crawl_failed",
            failure_message="Website extraction failed.",
        )
        return WebsiteEvidence(
            status="failed",
            target_url=normalized,
            crawl_run_id=run.id,
            pages_fetched=0,
            retrieved_at=datetime.now(UTC),
            warnings=["Website extraction failed."],
        )

    pages: list[tuple[str, str]] = []
    for page in report.pages:
        reference = page.raw_content_reference
        if reference is None:
            continue
        pages.append((page.requested_url, reference.read_bytes().decode("utf-8", errors="replace")))

    warnings: list[str] = []
    evidence: list[EvidenceItem] = []
    hospital_name: str | None = None
    if pages:
        crawl_stats = CrawlStats(
            pages_discovered=report.discovered,
            pages_fetched=report.fetched,
            pages_failed=report.failed,
            pages_skipped=report.skipped,
        )
        config = load_config()
        dataset = build_dataset(
            pages,
            target_url=normalized,
            crawl_stats=crawl_stats,
            config=config,
        )
        intake = project_intake(
            dataset,
            pages=analyze_pages(pages),
            target_url=normalized,
        )
        hospital_name = intake.hospital_name
        if hospital_name is None:
            warnings.append("The extracted site did not state a hospital name; check the URL.")
        evidence = evidence_from_intake(
            intake,
            target_url=normalized,
            retrieved_at=datetime.now(UTC),
        )
    else:
        warnings.append(
            "No usable page content was retrieved for this URL, so no evidence could be extracted."
        )

    final_status = "completed" if evidence else ("completed_with_warnings" if warnings else "empty")
    final_status_enum = {
        "completed": CrawlRunStatus.COMPLETED,
        "completed_with_warnings": CrawlRunStatus.COMPLETED_WITH_WARNINGS,
        "empty": CrawlRunStatus.EMPTY,
    }[final_status]
    persistence.finalize_run(
        run,
        status=final_status_enum,
        evidence=evidence,
        warnings=warnings,
        criteria_without_evidence=[],
        pages_fetched=report.fetched,
        hospital_name=hospital_name,
        canonical_url=normalized,
        raw_artifact_ref=run_key,
    )
    return WebsiteEvidence(
        status=final_status,
        target_url=normalized,
        crawl_run_id=run.id,
        hospital_name=hospital_name,
        pages_fetched=report.fetched,
        retrieved_at=datetime.now(UTC),
        evidence=evidence,
        warnings=warnings,
    )
