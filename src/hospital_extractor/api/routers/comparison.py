"""Comparison endpoints: capability report, prompt interpretation, matrix build.

Evidence source A (MedCompare server) is served here. Evidence source B
(public internet research) returns a truthful 501 naming the missing
integration instead of simulating research, and evidence source C (website
crawling) is exposed through :mod:`hospital_extractor.api.routers.crawl`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from hospital_extractor.api.deps import get_db_session
from hospital_extractor.api.routers.facilities import facility_location
from hospital_extractor.comparison.capabilities import assess_capabilities
from hospital_extractor.comparison.evidence import registry_evidence
from hospital_extractor.comparison.interpret import interpret_prompt
from hospital_extractor.comparison.schemas import (
    CapabilityReport,
    ComparisonColumn,
    ComparisonRequest,
    ComparisonResponse,
    EvidenceItem,
    InterpretRequest,
    InterpretResponse,
    RegisteredHospital,
    SourceStatus,
    UnregisteredHospital,
)
from hospital_extractor.comparison.service import ComparisonSubject, build_comparison
from hospital_extractor.persistence.crawl.service import CrawlPersistenceService
from hospital_extractor.persistence.exceptions import (
    CrawlRunNotFoundError,
    CrawlRunOwnershipError,
    FacilityNotFoundError,
)
from hospital_extractor.persistence.models_crawl import HospitalCrawlRun
from hospital_extractor.persistence.registry.service import RegistryService

__all__ = ["router"]

router = APIRouter(tags=["comparison"])

_REGISTRY_DETAIL = (
    "Evidence read from the MedCompare hospital registry (stored facility "
    "identity and address records) via /api/v1/facilities."
)
_CRAWL_DETAIL = (
    "Evidence produced by explicitly requested website extraction "
    "(POST /api/v1/crawl/extract). Execution is synchronous; completed results "
    "are persisted server-side and retrievable by crawl_run_id (no background job queue)."
)


@router.get("/comparison/capabilities", response_model=CapabilityReport)
def capabilities() -> CapabilityReport:
    """Report which comparison capabilities this deployment can actually serve."""
    return assess_capabilities()


@router.post("/comparison/interpret", response_model=InterpretResponse)
def interpret(request: InterpretRequest) -> InterpretResponse:
    """Parse a natural-language comparison request into explicit criteria."""
    return interpret_prompt(request.prompt)


@router.post("/comparison", response_model=ComparisonResponse)
def compare(
    request: ComparisonRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> ComparisonResponse:
    """Build the side-by-side comparison for the requested hospitals."""
    _reject_unsupported_source(request)

    subjects: list[ComparisonSubject] = []
    warnings: list[str] = []
    for index, hospital in enumerate(request.hospitals):
        subject, subject_warnings = _subject(index, hospital, request, session)
        subjects.append(subject)
        warnings.extend(subject_warnings)

    warnings.extend(_global_warnings(request, subjects))
    try:
        return build_comparison(
            request,
            subjects,
            source_statuses=_source_statuses(),
            extra_warnings=warnings,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_criteria", "message": str(exc)},
        ) from exc


def _reject_unsupported_source(request: ComparisonRequest) -> None:
    if request.source != "public_research":
        return
    report = assess_capabilities()
    search = next(
        (
            capability
            for capability in report.capabilities
            if capability.key == "public_internet_search"
        ),
        None,
    )
    raise HTTPException(
        status_code=501,
        detail={
            "error": "public_research_not_configured",
            "message": (search.detail if search else "Public internet research is not available.")
            + " MedCompare cannot research hospitals for you until this integration exists.",
            "required_integration": search.required_integration if search else None,
        },
    )


def _subject(
    index: int,
    hospital: RegisteredHospital | UnregisteredHospital,
    request: ComparisonRequest,
    session: Session,
) -> tuple[ComparisonSubject, list[str]]:
    if isinstance(hospital, RegisteredHospital):
        return _registered_subject(hospital, request, session)
    return _unregistered_subject(index, hospital, request, session)


def _registered_subject(
    hospital: RegisteredHospital, request: ComparisonRequest, session: Session
) -> tuple[ComparisonSubject, list[str]]:
    service = RegistryService(session)
    persistence = CrawlPersistenceService(session)
    try:
        facility = service.get_facility(hospital.facility_id)
    except FacilityNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "facility_not_found",
                "message": f"No registered facility with id {hospital.facility_id}.",
            },
        ) from exc
    except SQLAlchemyError as exc:  # pragma: no cover - depends on deployment DB
        raise HTTPException(
            status_code=503,
            detail={
                "error": "registry_unavailable",
                "message": "The hospital registry database is not reachable.",
            },
        ) from exc

    organization = facility.organization
    crawl_evidence, used_run_id, warnings = _resolve_registered_crawl_evidence(
        persistence, hospital.facility_id, hospital.crawl_run_id
    )
    # Server-side evidence is authoritative: registry + persisted crawl only.
    # Legacy client-supplied evidence must not override registered facility evidence.
    if hospital.evidence:
        warnings.append(
            f"Legacy client-supplied evidence for {facility.name} was ignored; "
            "registered facility evidence is resolved from server-side records."
        )

    column = ComparisonColumn(
        column_id=str(facility.id),
        kind="registered",
        name=facility.name,
        location=facility_location(facility),
        organization_name=organization.name if organization is not None else None,
        website_url=facility.canonical_url,
        identity_note=(
            "Registered MedCompare facility - this specific branch, not the parent brand."
        ),
        crawl_run_id=used_run_id,
    )
    evidence: tuple[EvidenceItem, ...] = tuple([*registry_evidence(facility), *crawl_evidence])
    return ComparisonSubject(column=column, evidence=evidence), warnings


def _resolve_registered_crawl_evidence(
    persistence: CrawlPersistenceService,
    facility_id: uuid.UUID,
    crawl_run_id: uuid.UUID | None,
) -> tuple[list[EvidenceItem], uuid.UUID | None, list[str]]:
    warnings: list[str] = []
    try:
        if crawl_run_id is not None:
            run = persistence.get_run_for_facility(crawl_run_id, facility_id)
            if not run.usable_for_comparison:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "crawl_run_not_usable",
                        "message": (
                            f"Crawl run {crawl_run_id} is not a usable snapshot "
                            f"(status: {run.status}). Select a completed run with evidence "
                            "or rely on the latest usable snapshot."
                        ),
                    },
                )
            snap = persistence.snapshot(run)
            warnings.extend(_run_warnings(run))
            return snap.evidence, run.id, warnings

        latest = persistence.latest_usable_for_facility(facility_id)
        if latest is None:
            return [], None, warnings
        snap = persistence.snapshot(latest)
        warnings.extend(_run_warnings(latest))
        return snap.evidence, latest.id, warnings
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


def _unregistered_subject(
    index: int,
    hospital: UnregisteredHospital,
    request: ComparisonRequest,
    session: Session,
) -> tuple[ComparisonSubject, list[str]]:
    persistence = CrawlPersistenceService(session)
    evidence: list[EvidenceItem] = []
    warnings: list[str] = []
    used_run_id: uuid.UUID | None = None
    subject_id = hospital.comparison_subject_id

    if hospital.crawl_run_id is not None:
        if subject_id is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "comparison_subject_required",
                    "message": (
                        "An unregistered hospital crawl_run_id must be accompanied by its "
                        "server-issued comparison_subject_id."
                    ),
                },
            )
        try:
            run = persistence.get_run_for_subject(hospital.crawl_run_id, subject_id)
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
        if not run.usable_for_comparison:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "crawl_run_not_usable",
                    "message": (
                        f"Crawl run {hospital.crawl_run_id} is not a usable snapshot "
                        f"(status: {run.status})."
                    ),
                },
            )
        snap = persistence.snapshot(run)
        evidence = snap.evidence
        used_run_id = run.id
        warnings.extend(_run_warnings(run))
        if hospital.evidence:
            warnings.append(
                f"Legacy client-supplied evidence for {hospital.name} was ignored because "
                "a validated persisted crawl run was provided."
            )
    elif hospital.evidence:
        # Legacy/untrusted compatibility path (not server-validated ownership).
        evidence = list(hospital.evidence)
        warnings.append(
            f"Using legacy client-supplied evidence for unregistered hospital "
            f"{hospital.name}; this is not server-validated crawl-run provenance."
        )
    else:
        # For server-source, unregistered cannot be described without persisted/legacy evidence.
        # Preserve existing 400 semantics with clear message.
        if request.source == "medcompare_server":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unregistered_hospital_requires_other_source",
                    "message": (
                        f"{hospital.name} is not registered on the MedCompare server, so server "
                        "data cannot describe it. Use website crawling (with the hospital's URL) "
                        "and a persisted crawl run, or public research when available."
                    ),
                },
            )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "extraction_required",
                "message": (
                    f"No validated website extraction is available for {hospital.name}. "
                    "Submit an extraction for its website first, or compare a registered facility."
                ),
            },
        )

    column = ComparisonColumn(
        column_id=str(subject_id or f"unregistered-{index + 1}"),
        kind="unregistered",
        name=hospital.name,
        location=hospital.location,
        organization_name=None,
        website_url=hospital.website_url,
        identity_note=(
            "Not in the MedCompare registry. Identified only by the supplied "
            "comparison subject/name/location - confirm it is the intended branch."
        ),
        crawl_run_id=used_run_id,
        comparison_subject_id=subject_id,
    )
    return ComparisonSubject(column=column, evidence=tuple(evidence)), warnings


def _run_warnings(run: HospitalCrawlRun) -> list[str]:
    status = run.status
    if status in {"failed", "timed_out", "empty"}:
        return [f"A referenced crawl run is {status}; it is not used as evidence."]
    return []


def _global_warnings(
    request: ComparisonRequest,
    subjects: list[ComparisonSubject],
) -> list[str]:
    warnings: list[str] = []
    if request.source == "website_crawl":
        if not any(
            any(item.source == "website_crawl" for item in subject.evidence)
            for subject in subjects
        ):
            warnings.append(
                "No usable website extraction evidence is available, so only "
                "MedCompare server evidence is shown."
            )
    elif request.source == "medcompare_server":
        for subject in subjects:
            if subject.column.kind == "registered" and subject.column.location is None:
                warnings.append(
                    f"No stored location is registered for {subject.column.name}; "
                    "location cannot be compared for this facility."
                )
    return warnings


def _source_statuses() -> list[SourceStatus]:
    report = assess_capabilities()
    by_key = {capability.key: capability for capability in report.capabilities}
    search = by_key["public_internet_search"]
    crawl = by_key["website_crawling_on_request"]
    return [
        SourceStatus(
            source="medcompare_server",
            status="active",
            detail=_REGISTRY_DETAIL,
        ),
        SourceStatus(
            source="public_research",
            status="active" if search.available else "not_configured",
            detail=search.detail,
            required_integration=search.required_integration,
        ),
        SourceStatus(
            source="website_crawl",
            status="active" if crawl.available else "not_configured",
            detail=_CRAWL_DETAIL if crawl.available else crawl.detail,
            required_integration=None if crawl.available else crawl.required_integration,
        ),
    ]
