"""API routes for the hospital comparison engine."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from hospital_extractor.auth.dependencies import PrincipalDep, require_permission
from hospital_extractor.auth.models import Permission, Principal
from hospital_extractor.comparison.models import ComparisonResult
from hospital_extractor.comparison.service import compare_urls
from hospital_extractor.sources import SourceType
from hospital_extractor.sources.pipeline import HarvestError, harvest
from hospital_extractor.storage import HospitalDatabase, default_database_path
from hospital_extractor.utils.urls import InvalidURLError

__all__ = ["router"]

router = APIRouter()

# Categories accepted by the JSON API and web form.
_CATEGORY_VALUES = ("procedure", "treatment", "speciality", "department")
ALLOWED_CATEGORIES = frozenset(_CATEGORY_VALUES)

# Single authoritative list of allowed source-type values (via SourceType).
SOURCE_VALUES = SourceType.values()


# ---------------------------------------------------------------------------
# Request / response models (API-specific, thin wrappers)
# ---------------------------------------------------------------------------


class CompareRequest(BaseModel):
    """Request body for ``POST /api/v1/compare``."""

    urls: list[str] = Field(min_length=2, max_length=4)
    procedure: str = Field(min_length=1, max_length=200)
    source: str = Field(
        default="website",
        description=(
            "Data source for every hospital: website (default), mock_api "
            "(local demo API), hospital_api (authorized API), or json_feed "
            "(persisted dataset files)"
        ),
    )
    category: (
        Literal["procedure", "treatment", "speciality", "department"] | None
    ) = Field(
        default=None,
        description=(
            "Target category (procedure/treatment/speciality/department); "
            "defaults to auto-detection"
        ),
    )

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        """Reject unknown source types using the authoritative SourceType values."""
        if value not in SourceType.values():
            raise ValueError(
                f"unknown source {value!r}; expected one of {', '.join(SourceType.values())}"
            )
        return value


class CompareResponse(BaseModel):
    """Successful comparison response."""

    result: ComparisonResult


class ErrorResponse(BaseModel):
    """Error response."""

    error: str
    detail: str = ""


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/compare",
    response_model=CompareResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def api_compare(
    request: CompareRequest,
    http_request: Request,
    principal: PrincipalDep,
    _perm: Annotated[Principal, Depends(require_permission(Permission.VIEW_HOSPITAL))],
) -> CompareResponse | JSONResponse:
    """Compare hospitals for a target procedure.

    Crawls, extracts and compares each hospital URL concurrently, then
    returns the structured comparison result.
    """
    try:
        datasets = await _extract_all(request.urls, source_type=request.source)
        await _persist_datasets(http_request, datasets)
        result = compare_urls(datasets, request.procedure, category=request.category)
        return CompareResponse(result=result)
    except HarvestError as exc:
        invalid = _invalid_url_error(exc)
        if invalid is not None:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(error="Invalid URL", detail=invalid).model_dump(),
            )
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="Crawl failed", detail=str(exc)
            ).model_dump(),
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="Internal error", detail=str(exc)
            ).model_dump(),
        )


@router.get("/api/v1/health")
async def health() -> dict[str, str]:
    """Health check endpoint (always unauthenticated — readiness probe)."""
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, principal: PrincipalDep) -> HTMLResponse:
    """Render the main comparison page (authenticated when auth is on)."""
    templates = request.app.state.templates
    template = templates.env.get_template("index.html")
    content = template.render(request=request, principal=principal)
    return HTMLResponse(content=content)


@router.post("/compare", response_class=HTMLResponse)
async def web_compare(
    request: Request,
    principal: PrincipalDep,
    _perm: Annotated[Principal, Depends(require_permission(Permission.VIEW_HOSPITAL))],
) -> HTMLResponse:
    """Handle the comparison form submission via HTMX.

    Returns an HTML fragment that HTMX swaps into the page.
    """
    form = await request.form()
    urls_text = str(form.get("urls", ""))
    procedure = str(form.get("procedure", ""))
    raw_source = str(form.get("source", "") or "").strip().lower()
    source = raw_source if raw_source in SOURCE_VALUES else "website"
    raw_category = str(form.get("category", "") or "").strip().lower()
    category = raw_category if raw_category in ALLOWED_CATEGORIES else None

    urls = [u.strip() for u in urls_text.strip().splitlines() if u.strip()]

    if len(urls) < 2:
        return _error_fragment("Please provide at least 2 hospital URLs.")
    if not procedure.strip():
        return _error_fragment("Please enter a procedure or department name.")

    try:
        datasets = await _extract_all(urls, source_type=source)
        await _persist_datasets(request, datasets)
        result = compare_urls(datasets, procedure, category=category)
    except HarvestError as exc:
        invalid = _invalid_url_error(exc)
        if invalid is not None:
            return _error_fragment(f"Invalid URL: {invalid}")
        return _error_fragment(f"Source failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _error_fragment(f"Error: {exc}")

    templates = request.app.state.templates
    template = templates.env.get_template("result.html")
    content = template.render(request=request, result=result)
    return HTMLResponse(content=content)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _extract_all(
    refs: list[str],
    *,
    source_type: str = "website",
) -> list[tuple[str, Any]]:
    """Fetch canonical datasets through the selected source adapter.

    All source types (``website``, ``mock_api``, ``hospital_api``,
    ``json_feed``) go through the registry — no crawler internals are imported
    here.  Provenance is stamped inside each adapter.  Returns ``(ref, dataset)``
    tuples ready for ``compare_urls`` and persistence.
    """
    harvested = await harvest(
        refs,
        source_type=source_type,
        output_dir="data",
        include_low_value=False,
    )
    return [(item.reference, item.dataset) for item in harvested]


def _invalid_url_error(exc: BaseException) -> str | None:
    """Re-surface an invalid-URL message buried in a :class:`HarvestError` chain.

    Adapters wrap ``InvalidURLError`` inside ``SourceFetchError`` and harvest
    wraps that in ``HarvestError``.  Walk ``__cause__`` so the API can still
    answer a 400 instead of a generic crawl failure.
    """
    seen: set[int] = set()
    cause: BaseException | None = exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, InvalidURLError):
            return str(cause)
        cause = cause.__cause__
    return None


async def _persist_datasets(
    request: Request,
    datasets: list[tuple[str, Any]],
) -> None:
    """Persist harvested canonical datasets into the hospital store.

    Every comparison refreshes the store (source-agnostic — provenance is
    stamped on each record).  The database path can be overridden on app
    state via ``medcompare_db_path`` (used by tests).
    """
    path = getattr(request.app.state, "medcompare_db_path", None) or default_database_path()
    with HospitalDatabase(path) as db:
        for _reference, dataset in datasets:
            db.save_dataset(dataset)


def _error_fragment(message: str) -> HTMLResponse:
    """Return an HTMX-compatible error fragment.

    The comparison form uses ``hx-target="#results"`` on the form element, so
    this fragment is swapped into ``#results`` by default — no custom response
    header is required.
    """
    return HTMLResponse(content=f'<div class="error">{message}</div>')
