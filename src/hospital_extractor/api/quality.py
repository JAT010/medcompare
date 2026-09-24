"""Data quality & observability API (P23).

JSON endpoints return typed reports; Markdown is available via ``?format=markdown``.
All metrics are computed from stored rows only — never an overall score.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from hospital_extractor.quality import (
    DatasetQualityReport,
    HospitalQualityNotFoundError,
    HospitalQualityReport,
    PipelineQualityReport,
    SourceQualityReport,
    build_dataset_quality_report,
    build_hospital_quality_report,
    build_pipeline_quality_report,
    build_source_quality_reports,
    format_quality_json,
    format_quality_markdown,
)
from hospital_extractor.storage import HospitalDatabase, default_database_path

__all__ = ["quality_router"]

quality_router = APIRouter()


class ErrorResponse(BaseModel):
    error: str
    detail: str = ""


class HospitalQualityResponse(BaseModel):
    report: HospitalQualityReport


class DatasetQualityResponse(BaseModel):
    report: DatasetQualityReport


class SourceQualityResponse(BaseModel):
    reports: list[SourceQualityReport]


class PipelineQualityResponse(BaseModel):
    report: PipelineQualityReport


def _open_db(request: Request) -> HospitalDatabase:
    path = getattr(request.app.state, "medcompare_db_path", None) or default_database_path()
    return HospitalDatabase(path)


def _wants_markdown(request: Request, format_param: str | None) -> bool:
    if format_param and format_param.lower() in ("markdown", "md"):
        return True
    accept = request.headers.get("accept", "")
    return "text/markdown" in accept


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@quality_router.get(
    "/api/v1/quality/pipeline",
    response_model=PipelineQualityResponse,
)
async def api_pipeline_quality(request: Request) -> PipelineQualityResponse:
    """Whole-store quality metrics from stored rows only."""
    with _open_db(request) as db:
        report = build_pipeline_quality_report(db)
    return PipelineQualityResponse(report=report)


@quality_router.get(
    "/api/v1/quality/hospitals/{hospital_id}",
    response_model=None,
    responses={404: {"model": ErrorResponse}},
)
async def api_hospital_quality(
    hospital_id: str,
    request: Request,
    format: str | None = None,
) -> HospitalQualityResponse | JSONResponse | PlainTextResponse:
    """Quality report for one canonical hospital identity."""
    try:
        with _open_db(request) as db:
            report = build_hospital_quality_report(db, hospital_id)
    except HospitalQualityNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(error="Hospital not found", detail=str(exc)).model_dump(),
        )
    if _wants_markdown(request, format):
        return PlainTextResponse(format_quality_markdown(report), media_type="text/markdown")
    return HospitalQualityResponse(report=report)


@quality_router.get(
    "/api/v1/quality/hospitals/{hospital_id}/sources",
    response_model=SourceQualityResponse,
    responses={404: {"model": ErrorResponse}},
)
async def api_hospital_source_quality(
    hospital_id: str,
    request: Request,
) -> SourceQualityResponse | JSONResponse:
    """One SourceQualityReport per (source_type, provider) for a hospital."""
    try:
        with _open_db(request) as db:
            reports = build_source_quality_reports(db, hospital_id)
    except HospitalQualityNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(error="Hospital not found", detail=str(exc)).model_dump(),
        )
    return SourceQualityResponse(reports=reports)


@quality_router.get(
    "/api/v1/quality/datasets/{dataset_id}",
    response_model=None,
    responses={404: {"model": ErrorResponse}},
)
async def api_dataset_quality(
    dataset_id: str,
    request: Request,
    format: str | None = None,
) -> DatasetQualityResponse | JSONResponse | PlainTextResponse:
    """Quality report for one stored dataset."""
    with _open_db(request) as db:
        summary = next(
            (s for s in db.list_datasets() if s.dataset_id == dataset_id),
            None,
        )
        if summary is None:
            return JSONResponse(
                status_code=404,
                content=ErrorResponse(
                    error="Dataset not found",
                    detail=f"no stored dataset {dataset_id!r}",
                ).model_dump(),
            )
        report = build_dataset_quality_report(db, summary)
    if _wants_markdown(request, format):
        return PlainTextResponse(format_quality_markdown(report), media_type="text/markdown")
    return DatasetQualityResponse(report=report)


# ---------------------------------------------------------------------------
# UI helpers (quality panel data for the profile page)
# ---------------------------------------------------------------------------


def quality_panel_payload(
    db: HospitalDatabase,
    hospital_id: str,
    dataset_id: str | None = None,
) -> dict[str, Any] | None:
    """UI-friendly metrics subset for the hospital profile quality panel."""
    if not hospital_id:
        return None
    try:
        hospital_report = build_hospital_quality_report(db, hospital_id)
    except HospitalQualityNotFoundError:
        return None

    dataset_report: DatasetQualityReport | None = None
    if dataset_id:
        summary = next(
            (s for s in db.list_datasets() if s.dataset_id == dataset_id),
            None,
        )
        if summary is not None:
            dataset_report = build_dataset_quality_report(db, summary)

    def _pick(
        report: HospitalQualityReport | DatasetQualityReport,
        names: list[str],
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name in names:
            metric = report.metric(name)
            if metric is not None:
                out[name] = {
                    "label": metric.label,
                    "value": metric.value,
                    "unit": metric.unit,
                    "dimension": metric.dimension.value,
                    "definition": metric.definition,
                    "detail": metric.detail,
                }
        return out

    dataset_names = [
        "hospital_field_coverage_rate",
        "collection_coverage_rate",
        "unknown_field_rate",
        "conflicts_detected",
        "conflicts_unresolved",
        "conflict_rate",
        "duplicates_removed",
        "duplicate_rate",
        "records_extracted",
        "pages_crawled",
        "pages_with_information",
        "crawl_success_rate",
        "entity_provenance_rate",
        "freshness_age_days",
        "authorization_status",
        "empty_categories",
    ]
    hospital_names = [
        "dataset_count",
        "source_count",
        "snapshot_count",
        "change_set_count",
        "changes_detected_count",
        "verification_job_count",
        "reconciliation_case_count",
        "monitoring_policy_count",
        "report_count",
        "newest_freshness_age_days",
        "average_hospital_field_coverage_rate",
        "average_unknown_field_rate",
    ]

    return {
        "hospital_id": hospital_id,
        "hospital_name": hospital_report.hospital_name,
        "generated_at": hospital_report.generated_at.isoformat(),
        "note": hospital_report.note,
        "overall_score": None,
        "hospital_metrics": _pick(hospital_report, hospital_names),
        "dataset_metrics": (
            _pick(dataset_report, dataset_names) if dataset_report is not None else {}
        ),
        "source_reports": [
            {
                "source_type": sr.source_type,
                "provider": sr.provider,
                "dataset_count": sr.dataset_count,
                "authorization_status": sr.authorization_status,
                "latest_captured_at": (
                    sr.latest_captured_at.isoformat() if sr.latest_captured_at else None
                ),
                "metrics": {
                    m.name: m.value for m in sr.metrics
                },
            }
            for sr in hospital_report.source_reports
        ],
    }


@quality_router.get("/quality", response_class=HTMLResponse)
async def quality_index_page(request: Request) -> HTMLResponse:
    """Pipeline quality overview page."""
    with _open_db(request) as db:
        report = build_pipeline_quality_report(db)
        datasets = db.list_datasets()
    templates = request.app.state.templates
    content = templates.env.get_template("quality.html").render(
        request=request,
        report=report,
        datasets=datasets,
        markdown=format_quality_markdown(report),
    )
    return HTMLResponse(content=content)


@quality_router.get(
    "/api/v1/quality/format/{scope}",
    response_model=None,
    response_class=PlainTextResponse,
)
async def api_quality_format(
    scope: str,
    request: Request,
    id: str | None = None,
    format: str = "markdown",
) -> PlainTextResponse | JSONResponse:
    """Render a quality report as Markdown or JSON.

    ``scope`` is ``pipeline`` (id ignored) or ``hospital``/``dataset`` (needs ``id``).
    """
    with _open_db(request) as db:
        try:
            if scope == "pipeline":
                report: (
                    PipelineQualityReport
                    | HospitalQualityReport
                    | DatasetQualityReport
                ) = build_pipeline_quality_report(db)
            elif scope == "hospital":
                if not id:
                    return JSONResponse(
                        status_code=400,
                        content=ErrorResponse(
                            error="Missing id", detail="id query param required"
                        ).model_dump(),
                    )
                report = build_hospital_quality_report(db, id)
            elif scope == "dataset":
                if not id:
                    return JSONResponse(
                        status_code=400,
                        content=ErrorResponse(
                            error="Missing id", detail="id query param required"
                        ).model_dump(),
                    )
                summary = next(
                    (s for s in db.list_datasets() if s.dataset_id == id), None
                )
                if summary is None:
                    return JSONResponse(
                        status_code=404,
                        content=ErrorResponse(
                            error="Dataset not found", detail=f"no dataset {id!r}"
                        ).model_dump(),
                    )
                report = build_dataset_quality_report(db, summary)
            else:
                return JSONResponse(
                    status_code=400,
                    content=ErrorResponse(
                        error="Unknown scope",
                        detail="scope must be pipeline, hospital, or dataset",
                    ).model_dump(),
                )
        except HospitalQualityNotFoundError as exc:
            return JSONResponse(
                status_code=404,
                content=ErrorResponse(error="Hospital not found", detail=str(exc)).model_dump(),
            )

    if format.lower() in ("markdown", "md"):
        return PlainTextResponse(format_quality_markdown(report), media_type="text/markdown")
    if format.lower() == "json":
        return PlainTextResponse(format_quality_json(report), media_type="application/json")
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error="Unknown format", detail="format must be markdown or json"
        ).model_dump(),
    )
