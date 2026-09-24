"""P19-D Reporting API: generate, retrieve, submit, and track reports.

Endpoints:
- GET  /api/v1/reconciliation-cases/{id}/report          - get existing report
- POST /api/v1/reconciliation-cases/{id}/report          - generate report
- GET  /api/v1/reports/{report_id}                       - get report by id
- GET  /api/v1/reports/{report_id}/format                - get formatted report
- POST /api/v1/reports/{report_id}/submit                - submit report
- GET  /api/v1/reports/{report_id}/submissions           - list submissions
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from hospital_extractor.auth.audit import AuditOutcome, record_audit
from hospital_extractor.auth.dependencies import (
    AuthDep,
    PrincipalDep,
    get_request_correlation,
    require_permission,
)
from hospital_extractor.auth.models import Permission, Principal
from hospital_extractor.reconciliation.service import get_case
from hospital_extractor.reporting.formatters import format_report
from hospital_extractor.reporting.government import MockGovernmentProvider
from hospital_extractor.reporting.models import (
    NotificationConfig,
    NotificationProviderType,
    ReportStatus,
    ReportType,
    SubmissionStatus,
)
from hospital_extractor.reporting.providers import (
    AuthorizationError,
    ConfigurationError,
    LocalProvider,
    MockProvider,
)
from hospital_extractor.reporting.service import (
    ReportGenerationError,
    generate_report,
)
from hospital_extractor.storage import HospitalDatabase
from hospital_extractor.storage.database import default_database_path

__all__ = ["reporting_router"]

reporting_router = APIRouter()


class ReportRequest(BaseModel):
    """Body for generating a report."""

    report_type: ReportType = ReportType.RECONCILIATION_REPORT
    format: str = "json"
    generated_by: str | None = "system"


class SubmitRequest(BaseModel):
    """Body for submitting a report."""

    provider_type: NotificationProviderType = NotificationProviderType.MOCK
    dry_run: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


def _open_db(request: Request) -> HospitalDatabase:
    configured: Path | str = getattr(
        request.app.state, "medcompare_db_path", default_database_path()
    )
    return HospitalDatabase(configured)


def _error(status_code: int, message: str, detail: Any = None) -> JSONResponse:
    content: dict[str, Any] = {"error": message}
    if detail is not None:
        content["detail"] = detail
    return JSONResponse(status_code=status_code, content=content)


def _get_provider(
    provider_type: NotificationProviderType,
) -> LocalProvider | MockProvider | MockGovernmentProvider:
    """Get provider instance by type."""
    if provider_type == NotificationProviderType.LOCAL:
        return LocalProvider()
    if provider_type == NotificationProviderType.MOCK:
        return MockProvider()
    if provider_type == NotificationProviderType.GOVERNMENT_PORTAL:
        return MockGovernmentProvider()
    return LocalProvider()


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@reporting_router.get("/api/v1/reconciliation-cases/{case_id}/report")
async def api_get_case_report(
    case_id: str,
    request: Request,
) -> JSONResponse:
    """Get the report for a reconciliation case (if exists)."""
    with _open_db(request) as db:
        case = get_case(db, case_id)
        if case is None:
            return _error(404, "Reconciliation case not found", case_id)
        report = db.get_report_for_case(case_id)
        if report is None:
            return _error(404, "No report generated for this case", case_id)
    return JSONResponse(content=report.model_dump(mode="json"))


@reporting_router.post("/api/v1/reconciliation-cases/{case_id}/report")
async def api_generate_case_report(
    case_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[Principal, Depends(require_permission(Permission.GENERATE_REPORT))],
    body: Annotated[ReportRequest | None, Body()] = None,
) -> JSONResponse:
    """Generate a report for a reconciliation case."""
    payload = body or ReportRequest()
    with _open_db(request) as db:
        case = get_case(db, case_id)
        if case is None:
            return _error(404, "Reconciliation case not found", case_id)
        try:
            report = generate_report(
                db,
                case,
                report_type=payload.report_type,
                format=payload.format,
                generated_by=payload.generated_by or "manual",
            )
            db.save_report(report)
        except ReportGenerationError as exc:
            return _error(500, "Report generation failed", str(exc))
    with service.open_store() as store:
        record_audit(
            store,
            action="report.generate",
            principal=principal,
            resource_type="report",
            resource_id=report.report_id,
            hospital_id=report.hospital_id,
            correlation_id=get_request_correlation(request),
            metadata={"report_type": payload.report_type.value},
        )
    return JSONResponse(content=report.model_dump(mode="json"), status_code=201)


@reporting_router.get("/api/v1/reports/{report_id}")
async def api_get_report(report_id: str, request: Request) -> JSONResponse:
    """Get a report by ID."""
    with _open_db(request) as db:
        report = db.get_report(report_id)
        if report is None:
            return _error(404, "Report not found", report_id)
    return JSONResponse(content=report.model_dump(mode="json"))


@reporting_router.get("/api/v1/reports/{report_id}/format", response_model=None)
async def api_get_report_formatted(
    report_id: str,
    request: Request,
    format: str = Query(default="json", description="Output format: json, html, markdown"),
) -> HTMLResponse | PlainTextResponse | JSONResponse:
    """Get a formatted report."""
    with _open_db(request) as db:
        report = db.get_report(report_id)
        if report is None:
            return _error(404, "Report not found", report_id)

    if format.lower() == "html":
        content = format_report(report, "html")
        return HTMLResponse(content=content)
    if format.lower() in ("markdown", "md"):
        content = format_report(report, "markdown")
        return PlainTextResponse(content=content, media_type="text/markdown")
    content = format_report(report, "json")
    return JSONResponse(content=content)


@reporting_router.post("/api/v1/reports/{report_id}/submit")
async def api_submit_report(
    report_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[Principal, Depends(require_permission(Permission.SUBMIT_REPORT))],
    body: Annotated[SubmitRequest | None, Body()] = None,
) -> JSONResponse:
    """Submit a report to an external provider."""
    payload = body or SubmitRequest()
    with _open_db(request) as db:
        report = db.get_report(report_id)
        if report is None:
            return _error(404, "Report not found", report_id)

        provider = _get_provider(payload.provider_type)
        config = NotificationConfig(
            provider_type=payload.provider_type,
            config=payload.config,
            enabled=True,
        )

        try:
            provider.validate_configuration(config)
        except ConfigurationError as exc:
            return _error(400, f"Provider not configured: {exc}")

        try:
            submission = await provider.submit(report, config, dry_run=payload.dry_run)
            db.save_submission(submission)
            # Update report status if submitted
            if not payload.dry_run and submission.status in (
                SubmissionStatus.SUBMITTED,
                SubmissionStatus.ACKNOWLEDGED,
            ):
                db.update_report_status(report_id, ReportStatus.SUBMITTED)
        except AuthorizationError as exc:
            with service.open_store() as store:
                record_audit(
                    store,
                    action="report.submit",
                    outcome=AuditOutcome.FAILURE,
                    principal=principal,
                    resource_type="report",
                    resource_id=report_id,
                    correlation_id=get_request_correlation(request),
                    metadata={"reason": "authorization_required"},
                )
            return _error(403, f"Authorization required: {exc}")
        except Exception:  # noqa: BLE001
            with service.open_store() as store:
                record_audit(
                    store,
                    action="report.submit",
                    outcome=AuditOutcome.FAILURE,
                    principal=principal,
                    resource_type="report",
                    resource_id=report_id,
                    correlation_id=get_request_correlation(request),
                )
            return _error(500, "Submission failed")

    with service.open_store() as store:
        record_audit(
            store,
            action="report.submit",
            principal=principal,
            resource_type="report",
            resource_id=report_id,
            hospital_id=report.hospital_id,
            correlation_id=get_request_correlation(request),
            metadata={
                "dry_run": payload.dry_run,
                "provider_type": payload.provider_type.value,
                "submission_status": submission.status.value,
            },
        )
    return JSONResponse(content=submission.model_dump(mode="json"), status_code=201)


@reporting_router.get("/api/v1/reports/{report_id}/submissions")
async def api_list_submissions(
    report_id: str,
    request: Request,
) -> JSONResponse:
    """List all submissions for a report."""
    with _open_db(request) as db:
        report = db.get_report(report_id)
        if report is None:
            return _error(404, "Report not found", report_id)
        submissions = db.list_submissions(report_id=report_id)
        rows = [s.model_dump(mode="json") for s in submissions]
    return JSONResponse(content={"report_id": report_id, "submissions": rows})


@reporting_router.get("/api/v1/hospitals/{hospital_id}/reports")
async def api_list_hospital_reports(
    hospital_id: str,
    request: Request,
    status: str | None = None,
) -> JSONResponse:
    """List all reports for a hospital."""
    with _open_db(request) as db:
        resolved = db.find_hospital_id(
            preferred=hospital_id,
            source_ref=hospital_id,
            hospital_name=hospital_id,
        )
        if resolved is None:
            return _error(404, "Hospital not found", hospital_id)
        reports = db.list_reports(hospital_id=resolved, status=status)
        rows = [r.model_dump(mode="json") for r in reports]
    return JSONResponse(content={"hospital_id": resolved, "reports": rows})