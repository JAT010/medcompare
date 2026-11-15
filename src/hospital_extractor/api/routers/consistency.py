"""Database-to-website consistency audit endpoints.

The internal side is the stored MedCompare registry record. The website side is
a validated, persisted crawl run for the same physical facility. A run that the
caller names explicitly is ownership-checked; otherwise the latest usable
snapshot is selected with the same policy as the comparison endpoint.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from hospital_extractor.api.deps import get_db_session
from hospital_extractor.comparison.schemas import EvidenceItem
from hospital_extractor.consistency.schemas import (
    ConsistencyAuditRequest,
    ConsistencyAuditResponse,
)
from hospital_extractor.consistency.service import build_audit
from hospital_extractor.persistence.crawl.service import CrawlPersistenceService
from hospital_extractor.persistence.exceptions import (
    CrawlRunNotFoundError,
    CrawlRunOwnershipError,
    FacilityNotFoundError,
)
from hospital_extractor.persistence.models import HospitalFacility
from hospital_extractor.persistence.models_crawl import HospitalCrawlRun
from hospital_extractor.persistence.registry.service import RegistryService

__all__ = ["router"]

router = APIRouter(tags=["consistency"])


@router.post("/consistency/audit", response_model=ConsistencyAuditResponse)
def audit_consistency(
    request: ConsistencyAuditRequest,
    session: Annotated[Session, Depends(get_db_session)],
) -> ConsistencyAuditResponse:
    """Audit one registered physical facility against its website extraction."""
    return _audit(request.facility_id, request.crawl_run_id, session)


@router.get(
    "/consistency/audit/{facility_id}",
    response_model=ConsistencyAuditResponse,
)
def get_consistency_audit(
    facility_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
    crawl_run_id: uuid.UUID | None = None,
) -> ConsistencyAuditResponse:
    """Audit a facility using its latest usable snapshot or a named crawl run."""
    return _audit(facility_id, crawl_run_id, session)


def _audit(
    facility_id: uuid.UUID,
    crawl_run_id: uuid.UUID | None,
    session: Session,
) -> ConsistencyAuditResponse:
    registry = RegistryService(session)
    persistence = CrawlPersistenceService(session)
    try:
        facility = registry.get_facility(facility_id)
    except FacilityNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "facility_not_found",
                "message": f"No registered facility with id {facility_id}.",
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

    run, evidence, warnings = _resolve_snapshot(
        persistence, facility, crawl_run_id
    )
    return build_audit(facility, run, evidence, warnings=warnings)


def _resolve_snapshot(
    persistence: CrawlPersistenceService,
    facility: HospitalFacility,
    crawl_run_id: uuid.UUID | None,
) -> tuple[HospitalCrawlRun | None, list[EvidenceItem], list[str]]:
    try:
        if crawl_run_id is not None:
            run = persistence.get_run_for_facility(crawl_run_id, facility.id)
            if not run.usable_for_comparison:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "crawl_run_not_usable",
                        "message": (
                            f"Crawl run {crawl_run_id} is not a usable snapshot "
                            f"(status: {run.status}). Select a completed run with evidence "
                            "or omit crawl_run_id to use the latest usable snapshot."
                        ),
                    },
                )
            snapshot = persistence.snapshot(run)
            return run, snapshot.evidence, snapshot.warnings

        latest = persistence.latest_usable_for_facility(facility.id)
        if latest is None:
            return None, [], []
        snapshot = persistence.snapshot(latest)
        return latest, snapshot.evidence, snapshot.warnings
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
    except SQLAlchemyError as exc:  # pragma: no cover - depends on deployment DB
        raise HTTPException(
            status_code=503,
            detail={
                "error": "registry_unavailable",
                "message": "The hospital registry database is not reachable.",
            },
        ) from exc
