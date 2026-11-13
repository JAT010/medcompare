"""Server-side crawl run persistence and retrieval.

Extends the synchronous extract endpoint to persist crawl attempts/results and
exposes validated retrieval endpoints. Raw filesystem paths and internal traces
are never returned.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from hospital_extractor.api.deps import get_db_session
from hospital_extractor.api.settings import ApiSettings, get_settings
from hospital_extractor.comparison.capabilities import assess_capabilities
from hospital_extractor.comparison.evidence import evidence_from_intake
from hospital_extractor.comparison.schemas import (
    CrawlRequest,
    CrawlResponse,
    CrawlRunDetail,
    EvidenceItem,
)
from hospital_extractor.config import load_config
from hospital_extractor.crawler.context import CrawlLimits, build_context
from hospital_extractor.crawler.runner import CrawlAbortedError, run_crawl
from hospital_extractor.crawler.ssrf import SSRFBlockedError, validate_public_url
from hospital_extractor.extraction import CrawlStats, analyze_pages, build_dataset
from hospital_extractor.extraction.intake import project_intake
from hospital_extractor.models.intake import MasterIntake
from hospital_extractor.persistence.crawl.service import CrawlPersistenceService
from hospital_extractor.persistence.exceptions import (
    CrawlRunNotFoundError,
    CrawlRunOwnershipError,
    FacilityNotFoundError,
)
from hospital_extractor.persistence.models_crawl import CrawlRunStatus, HospitalCrawlRun
from hospital_extractor.persistence.registry.service import RegistryService
from hospital_extractor.recommendation.criteria import CRITERIA
from hospital_extractor.utils.urls import InvalidURLError

__all__ = ["CrawlOutcome", "CrawlRunner", "extract_crawl", "get_crawl_runner", "router"]

router = APIRouter(tags=["crawl"])

_NO_JOB_STORE_NOTE = (
    "Extraction ran synchronously in this request. This deployment has no persistent "
    "crawl job store/worker, so no background job queue or polling endpoint exists. "
    "Completed results are persisted and retrievable by crawl_run_id."
)


@dataclass
class CrawlOutcome:
    target_url: str
    pages_fetched: int
    hospital_name: str | None = None
    intake: MasterIntake | None = None
    warnings: list[str] = field(default_factory=list)
    raw_artifact_ref: str | None = None


CrawlRunner = Callable[[CrawlRequest], Awaitable[CrawlOutcome]]


async def run_extraction(request: CrawlRequest, settings: ApiSettings) -> CrawlOutcome:
    limits = CrawlLimits(max_pages=request.max_pages)
    context = build_context(request.url, limits=limits)
    run_key = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    report = await run_crawl(
        context,
        output_dir=settings.crawl_output_dir,
        run_id=run_key,
        url_guard=validate_public_url,
    )

    pages: list[tuple[str, str]] = []
    for page in report.pages:
        reference = page.raw_content_reference
        if reference is None:
            continue
        pages.append(
            (page.requested_url, reference.read_bytes().decode("utf-8", errors="replace"))
        )

    warnings: list[str] = []
    if not pages:
        warnings.append(
            "No usable page content was retrieved for this URL, so no evidence could be "
            "extracted."
        )
        return CrawlOutcome(
            target_url=context.target_url,
            pages_fetched=report.fetched,
            warnings=warnings,
            raw_artifact_ref=run_key,
        )

    crawl_stats = CrawlStats(
        pages_discovered=report.discovered,
        pages_fetched=report.fetched,
        pages_failed=report.failed,
        pages_skipped=report.skipped,
    )
    config = load_config()
    dataset = build_dataset(
        pages, target_url=context.target_url, crawl_stats=crawl_stats, config=config
    )
    intake = project_intake(
        dataset, pages=analyze_pages(pages), target_url=context.target_url
    )
    if intake.hospital_name is None:
        warnings.append(
            "The extracted site did not state a hospital name; check that the URL belongs "
            "to the intended facility."
        )
    return CrawlOutcome(
        target_url=context.target_url,
        pages_fetched=report.fetched,
        hospital_name=intake.hospital_name,
        intake=intake,
        warnings=warnings,
        raw_artifact_ref=run_key,
    )


def get_crawl_runner() -> CrawlRunner:
    settings = get_settings()

    async def runner(request: CrawlRequest) -> CrawlOutcome:
        return await run_extraction(request, settings)

    return runner


@router.post("/crawl/extract", response_model=CrawlResponse)
async def extract_crawl(
    request: CrawlRequest,
    runner: Annotated[CrawlRunner, Depends(get_crawl_runner)],
    session: Annotated[Session, Depends(get_db_session)],
) -> CrawlResponse:
    settings = get_settings()
    _validate_target(request.url)
    facility_id = _resolve_facility(request, session)
    subject_id = _resolve_subject_id(request, facility_id)
    persistence = CrawlPersistenceService(session)

    try:
        run = persistence.start_run(
            facility_id=facility_id,
            comparison_subject_id=subject_id,
            subject_name=request.name,
            subject_location=request.location,
            subject_website_url=request.url,
            target_url=request.url,
            requested_url=request.url,
            requested_max_pages=request.max_pages,
        )
    except FacilityNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "facility_not_found", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_request", "message": str(exc)},
        ) from exc
    except SQLAlchemyError as exc:  # pragma: no cover
        raise HTTPException(
            status_code=503,
            detail={
                "error": "registry_unavailable",
                "message": "The hospital registry database is not reachable.",
            },
        ) from exc

    try:
        outcome = await asyncio.wait_for(
            runner(request), timeout=settings.crawl_timeout_seconds
        )
    except TimeoutError as exc:
        jobs = next(
            (
                c
                for c in assess_capabilities().capabilities
                if c.key == "asynchronous_crawl_jobs"
            ),
            None,
        )
        warnings = ["Extraction did not finish within the request window.", _NO_JOB_STORE_NOTE]
        persistence.finalize_run(
            run,
            status=CrawlRunStatus.TIMED_OUT,
            evidence=[],
            warnings=warnings,
            criteria_without_evidence=_all_criteria_keys(),
            pages_fetched=0,
            hospital_name=None,
            failure_code="crawl_exceeded_request_window",
            failure_message="Extraction did not finish within the synchronous request window.",
        )
        raise HTTPException(
            status_code=504,
            detail={
                "error": "crawl_exceeded_request_window",
                "message": (
                    f"Extraction did not finish within {settings.crawl_timeout_seconds:.0f} "
                    "seconds. Website extraction can take longer than comparing server data, "
                    "and this deployment cannot queue work to continue in the background."
                ),
                "crawl_run_id": str(run.id),
                "comparison_subject_id": (
                    str(run.comparison_subject_id) if run.comparison_subject_id else None
                ),
                "required_integration": jobs.required_integration if jobs else None,
            },
        ) from exc
    except SSRFBlockedError as exc:
        persistence.finalize_run(
            run,
            status=CrawlRunStatus.FAILED,
            evidence=[],
            warnings=[_NO_JOB_STORE_NOTE],
            criteria_without_evidence=_all_criteria_keys(),
            pages_fetched=0,
            hospital_name=None,
            failure_code="url_not_allowed",
            failure_message=str(exc),
        )
        raise HTTPException(
            status_code=422,
            detail={"error": "url_not_allowed", "message": str(exc), "crawl_run_id": str(run.id)},
        ) from exc
    except InvalidURLError as exc:
        persistence.finalize_run(
            run,
            status=CrawlRunStatus.FAILED,
            evidence=[],
            warnings=[_NO_JOB_STORE_NOTE],
            criteria_without_evidence=_all_criteria_keys(),
            pages_fetched=0,
            hospital_name=None,
            failure_code="invalid_url",
            failure_message=str(exc),
        )
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_url", "message": str(exc), "crawl_run_id": str(run.id)},
        ) from exc
    except CrawlAbortedError as exc:
        persistence.finalize_run(
            run,
            status=CrawlRunStatus.FAILED,
            evidence=[],
            warnings=[_NO_JOB_STORE_NOTE],
            criteria_without_evidence=_all_criteria_keys(),
            pages_fetched=0,
            hospital_name=None,
            failure_code="target_unreachable",
            failure_message=str(exc),
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "target_unreachable",
                "message": f"{exc} The website could not be crawled.",
                "crawl_run_id": str(run.id),
            },
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive
        persistence.finalize_run(
            run,
            status=CrawlRunStatus.FAILED,
            evidence=[],
            warnings=[_NO_JOB_STORE_NOTE],
            criteria_without_evidence=_all_criteria_keys(),
            pages_fetched=0,
            hospital_name=None,
            failure_code="crawl_failed",
            failure_message="Extraction failed.",
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "crawl_failed",
                "message": "Extraction failed.",
                "crawl_run_id": str(run.id),
            },
        ) from exc

    evidence: list[EvidenceItem] = []
    if outcome.intake is not None:
        evidence = evidence_from_intake(
            outcome.intake, target_url=outcome.target_url, retrieved_at=datetime.now(UTC)
        )

    answered = {item.criterion_key for item in evidence}
    without_evidence: list[str] = sorted(
        criterion.key for criterion in CRITERIA if criterion.key not in answered
    )
    warnings = [*outcome.warnings, _NO_JOB_STORE_NOTE]
    status = _status_for(evidence, warnings, outcome.pages_fetched)

    try:
        persistence.finalize_run(
            run,
            status=status,
            evidence=evidence,
            warnings=warnings,
            criteria_without_evidence=without_evidence,
            pages_fetched=outcome.pages_fetched,
            hospital_name=outcome.hospital_name,
            canonical_url=outcome.target_url,
            raw_artifact_ref=outcome.raw_artifact_ref,
        )
    except Exception as exc:  # pragma: no cover - defensive
        session.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "error": "persistence_failed",
                "message": "Extraction completed but results could not be persisted.",
            },
        ) from exc

    return _to_response(
        run,
        evidence=evidence,
        warnings=warnings,
        without_evidence=without_evidence,
    )


@router.get("/crawl/runs/{crawl_run_id}", response_model=CrawlRunDetail)
def get_crawl_run(
    crawl_run_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
    facility_id: uuid.UUID | None = None,
    comparison_subject_id: uuid.UUID | None = None,
) -> CrawlRunDetail:
    persistence = CrawlPersistenceService(session)
    try:
        run = persistence.get_run(crawl_run_id)
        if facility_id is not None:
            run = persistence.get_run_for_facility(crawl_run_id, facility_id)
        elif comparison_subject_id is not None:
            run = persistence.get_run_for_subject(crawl_run_id, comparison_subject_id)
    except CrawlRunNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "crawl_run_not_found", "message": str(exc)},
        ) from exc
    except CrawlRunOwnershipError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "crawl_run_ownership_mismatch", "message": str(exc)},
        ) from exc
    snap = persistence.snapshot(run)
    return _to_detail(run, snap.evidence, snap.warnings, snap.criteria_without_evidence)


def _status_for(
    evidence: list[EvidenceItem], warnings: list[str], pages_fetched: int
) -> CrawlRunStatus:
    if pages_fetched == 0 and not evidence:
        return CrawlRunStatus.EMPTY
    if evidence and warnings:
        return CrawlRunStatus.COMPLETED_WITH_WARNINGS
    if evidence:
        return CrawlRunStatus.COMPLETED
    return CrawlRunStatus.EMPTY


def _all_criteria_keys() -> list[str]:
    return sorted(criterion.key for criterion in CRITERIA)


def _to_response(
    run: HospitalCrawlRun,
    *,
    evidence: list[EvidenceItem],
    warnings: list[str],
    without_evidence: list[str],
) -> CrawlResponse:
    return CrawlResponse(
        status="completed" if run.status != CrawlRunStatus.FAILED.value else "failed",
        execution="synchronous",
        job_store_available=False,
        target_url=run.target_url,
        pages_fetched=run.pages_fetched,
        hospital_name=run.hospital_name,
        evidence=evidence,
        criteria_without_evidence=without_evidence,
        warnings=warnings,
        crawl_run_id=run.id,
        facility_id=run.facility_id,
        comparison_subject_id=run.comparison_subject_id,
        crawl_status=run.status,
        evidence_count=len(evidence),
        completed_at=run.completed_at,
        retrieved_at=run.retrieved_at,
    )


def _to_detail(
    run: HospitalCrawlRun,
    evidence: list[EvidenceItem],
    warnings: list[str],
    without_evidence: list[str],
) -> CrawlRunDetail:
    return CrawlRunDetail(
        crawl_run_id=run.id,
        facility_id=run.facility_id,
        comparison_subject_id=run.comparison_subject_id,
        subject_name=run.subject_name,
        subject_location=run.subject_location,
        subject_website_url=run.subject_website_url,
        status=run.status,
        execution_mode=run.execution_mode,
        target_url=run.target_url,
        requested_url=run.requested_url,
        hospital_name=run.hospital_name,
        pages_fetched=run.pages_fetched,
        evidence_count=len(evidence),
        evidence=evidence,
        criteria_without_evidence=without_evidence,
        warnings=warnings,
        usable_for_comparison=bool(run.usable_for_comparison),
        requested_at=run.requested_at,
        completed_at=run.completed_at,
        retrieved_at=run.retrieved_at,
        failure_code=run.failure_code,
        failure_message=run.failure_message,
    )


def _validate_target(url: str) -> None:
    try:
        validate_public_url(url)
    except SSRFBlockedError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "url_not_allowed", "message": str(exc)},
        ) from exc
    except InvalidURLError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_url", "message": str(exc)},
        ) from exc


def _resolve_facility(request: CrawlRequest, session: Session) -> uuid.UUID | None:
    if request.facility_id is None:
        return None
    service = RegistryService(session)
    try:
        service.get_facility(request.facility_id)
    except FacilityNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "facility_not_found",
                "message": f"No registered facility with id {request.facility_id}.",
            },
        ) from exc
    except SQLAlchemyError as exc:  # pragma: no cover
        raise HTTPException(
            status_code=503,
            detail={
                "error": "registry_unavailable",
                "message": "The hospital registry database is not reachable.",
            },
        ) from exc
    return request.facility_id


def _resolve_subject_id(
    request: CrawlRequest, facility_id: uuid.UUID | None
) -> uuid.UUID | None:
    if facility_id is not None:
        return None
    # Explicit stable server-generated identity for unregistered subjects.
    # Client may pass one to continue the same subject; if absent, generate.
    subject_id = request.comparison_subject_id
    if subject_id is not None:
        return subject_id
    return uuid.uuid4()
