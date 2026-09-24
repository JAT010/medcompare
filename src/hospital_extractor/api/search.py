"""Search + field-comparison API and server-rendered UI (P22)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from hospital_extractor.search import (
    ComparisonRequest,
    ComparisonResult,
    Filter,
    FilterOperator,
    HospitalNotFoundError,
    InvalidQueryError,
    Query,
    QueryResult,
    compare_stored_hospitals,
    search_datasets,
)
from hospital_extractor.storage import HospitalDatabase, default_database_path

__all__ = ["search_router"]

search_router = APIRouter()


class SearchResponse(BaseModel):
    result: QueryResult


class CompareStoredResponse(BaseModel):
    result: ComparisonResult


class ErrorResponse(BaseModel):
    error: str
    detail: str = ""


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@search_router.post(
    "/api/v1/search",
    response_model=SearchResponse,
    responses={400: {"model": ErrorResponse}},
)
async def api_search(query: Query, request: Request) -> SearchResponse | JSONResponse:
    """Run a structured search over stored hospital datasets."""
    try:
        with _open_db(request) as db:
            result = search_datasets(db, query)
        return SearchResponse(result=result)
    except InvalidQueryError as exc:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="Invalid query", detail=str(exc)).model_dump(),
        )


@search_router.post(
    "/api/v1/search/compare",
    response_model=CompareStoredResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def api_compare_stored(
    body: ComparisonRequest,
    request: Request,
) -> CompareStoredResponse | JSONResponse:
    """Field-by-field comparison of 2–4 stored hospitals (no ranking)."""
    try:
        with _open_db(request) as db:
            result = compare_stored_hospitals(db, body)
        return CompareStoredResponse(result=result)
    except InvalidQueryError as exc:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="Invalid query", detail=str(exc)).model_dump(),
        )
    except HospitalNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(error="Hospital not found", detail=str(exc)).model_dump(),
        )


# ---------------------------------------------------------------------------
# Server-rendered UI
# ---------------------------------------------------------------------------


@search_router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request) -> HTMLResponse:
    """Search + compare form with stored-hospital multi-select."""
    with _open_db(request) as db:
        datasets = db.list_datasets()
    templates = request.app.state.templates
    template = templates.env.get_template("search.html")
    content = template.render(
        request=request,
        datasets=datasets,
        result=None,
        compare_result=None,
        error=None,
        mode="search",
    )
    return HTMLResponse(content=content)


@search_router.post("/search", response_class=HTMLResponse)
async def search_submit(request: Request) -> HTMLResponse:
    """Handle the search form (HTMX or classic POST)."""
    form = await request.form()
    error: str | None = None
    result: QueryResult | None = None
    compare_result: ComparisonResult | None = None
    mode = str(form.get("mode", "search") or "search").strip()

    with _open_db(request) as db:
        datasets = db.list_datasets()
        try:
            if mode == "compare":
                hospital_ids = [
                    str(value)
                    for value in form.getlist("hospital_ids")
                    if str(value).strip()
                ]
                fields_raw = str(form.get("fields", "") or "").strip()
                fields = (
                    [part.strip() for part in fields_raw.split(",") if part.strip()]
                    if fields_raw
                    else []
                )
                compare_result = compare_stored_hospitals(
                    db, ComparisonRequest(hospital_ids=hospital_ids, fields=fields)
                )
            else:
                filters = _filters_from_form(form)
                text = str(form.get("text", "") or "").strip() or None
                hospital_ids = [
                    str(value)
                    for value in form.getlist("hospital_ids")
                    if str(value).strip()
                ]
                limit_raw = str(form.get("limit", "50") or "50")
                try:
                    limit = max(1, min(500, int(limit_raw)))
                except ValueError:
                    limit = 50
                result = search_datasets(
                    db,
                    Query(
                        filters=filters,
                        hospital_ids=hospital_ids,
                        text=text,
                        limit=limit,
                    ),
                )
        except (InvalidQueryError, HospitalNotFoundError, ValueError) as exc:
            error = str(exc)

    templates = request.app.state.templates
    template = templates.env.get_template("search.html")
    content = template.render(
        request=request,
        datasets=datasets,
        result=result,
        compare_result=compare_result,
        error=error,
        mode=mode,
    )
    return HTMLResponse(content=content)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_db(request: Request) -> HospitalDatabase:
    path = getattr(request.app.state, "medcompare_db_path", None) or default_database_path()
    return HospitalDatabase(path)


def _filters_from_form(form: Any) -> list[Filter]:
    """Build filters from repeated form groups filter_field/operator/value."""
    fields = form.getlist("filter_field")
    operators = form.getlist("filter_operator")
    values = form.getlist("filter_value")
    filters: list[Filter] = []
    for field, operator, value in zip(fields, operators, values, strict=False):
        field_name = str(field).strip()
        if not field_name:
            continue
        op_name = str(operator).strip() or FilterOperator.EQUALS.value
        raw_value = str(value)
        filters.append(
            Filter(field=field_name, operator=FilterOperator(op_name), value=_coerce(raw_value))
        )
    return filters


def _coerce(raw: str) -> Any:
    text = raw.strip()
    if text == "":
        return ""
    lowered = text.casefold()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text
