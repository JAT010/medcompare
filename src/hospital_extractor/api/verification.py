"""P19-B verification API: list / read / create verification jobs.

These endpoints are read-only with respect to crawling except for
``POST …/verification-jobs`` which merely *schedules* a job (writes a row);
the explicit ``worker`` process performs the verification capture.  The web UI
and these endpoints never trigger a crawl themselves.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from hospital_extractor.auth.audit import record_audit
from hospital_extractor.auth.dependencies import (
    AuthDep,
    PrincipalDep,
    get_request_correlation,
    require_hospital_access,
    require_permission,
)
from hospital_extractor.auth.models import Permission, Principal
from hospital_extractor.models.verification import VerificationJob, VerificationResult
from hospital_extractor.sources import SourceType
from hospital_extractor.storage import HospitalDatabase
from hospital_extractor.storage.database import default_database_path
from hospital_extractor.verification.service import create_manual_job

__all__ = ["verification_router"]

verification_router = APIRouter()


class VerificationJobCreateRequest(BaseModel):
    """Body for ``POST /api/v1/hospitals/{hospital_id}/verification-jobs``."""

    source: str = Field(
        default="website",
        description="Target source to verify against (default: website)",
    )

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        if value not in SourceType.values():
            raise ValueError(
                f"unknown source {value!r}; expected one of {', '.join(SourceType.values())}"
            )
        return value


def _job_json(job: VerificationJob) -> dict[str, Any]:
    return job.model_dump(mode="json")


def _result_json(result: VerificationResult) -> dict[str, Any]:
    data = result.model_dump(mode="json")
    data["verified_count"] = result.verified_count
    data["mismatch_count"] = result.mismatch_count
    data["unverified_count"] = result.unverified_count
    data["not_evaluated_count"] = result.not_evaluated_count
    data["change_count"] = result.change_count
    return data


def _open_db(request: Request) -> HospitalDatabase:
    configured = getattr(request.app.state, "medcompare_db_path", default_database_path())
    return HospitalDatabase(configured)


def _resolve_hospital(db: HospitalDatabase, reference: str) -> str | None:
    return db.find_hospital_id(
        preferred=reference,
        source_ref=reference,
        hospital_name=reference,
    )


@verification_router.get("/api/v1/hospitals/{hospital_id}/verification-jobs")
async def api_list_verification_jobs(
    hospital_id: str,
    request: Request,
    status: str | None = None,
    source: str | None = None,
) -> JSONResponse:
    """List verification jobs for one hospital with their latest results."""
    with _open_db(request) as db:
        resolved = _resolve_hospital(db, hospital_id)
        if resolved is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Hospital not found", "detail": hospital_id},
            )
        jobs = db.list_verification_jobs(resolved, status=status, source_type=source)
        rows = [
            {
                "job": _job_json(job),
                "result": (
                    _result_json(result)
                    if (result := db.get_verification_result(job.verification_job_id))
                    is not None
                    else None
                ),
            }
            for job in jobs
        ]
    return JSONResponse(content={"hospital_id": resolved, "verification_jobs": rows})


@verification_router.get("/api/v1/verification-jobs/{job_id}")
async def api_get_verification_job(job_id: str, request: Request) -> JSONResponse:
    """Return one verification job with its latest result."""
    with _open_db(request) as db:
        job = db.get_verification_job(job_id)
        result = db.get_verification_result(job_id) if job is not None else None
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Verification job not found", "detail": job_id},
        )
    return JSONResponse(
        content={
            "job": _job_json(job),
            "result": _result_json(result) if result is not None else None,
        }
    )


@verification_router.post("/api/v1/hospitals/{hospital_id}/verification-jobs")
async def api_create_verification_job(
    hospital_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[Principal, Depends(require_permission(Permission.VERIFY_CHANGE))],
    body: Annotated[VerificationJobCreateRequest | None, Body()] = None,
) -> JSONResponse:
    """Manually create a verification job for the latest changes (idempotent).

    Idempotent: a second call for the same change set / target reuses the
    existing job.  This endpoint only writes the job row — it never crawls.

    P24: requires VERIFY_CHANGE + hospital grant; writes an audit event.
    """
    require_hospital_access(
        principal,
        service,
        hospital_id,
        correlation_id=get_request_correlation(request),
    )
    target_source_type = body.source if body is not None else "website"
    with _open_db(request) as db:
        resolved = _resolve_hospital(db, hospital_id)
        if resolved is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Hospital not found", "detail": hospital_id},
            )
        job, reason = create_manual_job(
            db, resolved, target_source_type=target_source_type
        )
    if job is None:
        return JSONResponse(
            status_code=409,
            content={"error": "Nothing to verify", "detail": reason},
        )
    with service.open_store() as store:
        record_audit(
            store,
            action="verification.create",
            principal=principal,
            resource_type="verification_job",
            resource_id=job.verification_job_id,
            hospital_id=resolved,
            correlation_id=get_request_correlation(request),
            metadata={"target_source_type": target_source_type},
        )
    return JSONResponse(
        status_code=201,
        content={"verification_job": _job_json(job)},
    )