"""P19-C reconciliation API: list / read / review / resolve / dismiss / reopen.

These endpoints are pure database operations — they never crawl, capture or
mutate any hospital data.  A resolution is a recorded review decision about
existing evidence; snapshots stay immutable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from hospital_extractor.auth.audit import AuditOutcome, record_audit
from hospital_extractor.auth.dependencies import (
    AuthDep,
    PrincipalDep,
    get_request_correlation,
    require_permission,
)
from hospital_extractor.auth.models import Permission, Principal
from hospital_extractor.reconciliation.models import (
    ReconciliationCase,
    ReconciliationCaseStatus,
    ResolutionType,
)
from hospital_extractor.reconciliation.service import (
    ReconciliationStateError,
    build_evidence_package,
    dismiss_case,
    get_case,
    list_cases,
    list_events,
    reopen_case,
    resolve_case,
    start_review,
)
from hospital_extractor.storage import HospitalDatabase
from hospital_extractor.storage.database import default_database_path

__all__ = ["reconciliation_router"]

reconciliation_router = APIRouter()

_CASE_URL_PREFIX = "/api/v1/reconciliation-cases"


class ReviewRequest(BaseModel):
    """Body for ``POST …/{case_id}/review``."""

    assigned_to: str | None = None
    reason: str | None = None


class ResolveRequest(BaseModel):
    """Body for ``POST …/{case_id}/resolve``."""

    resolution: ResolutionType
    reason: str = Field(min_length=1, description="Why this resolution was chosen")


class DismissRequest(BaseModel):
    """Body for ``POST …/{case_id}/dismiss``."""

    reason: str = Field(min_length=1, description="Why no action is warranted")


class ReopenRequest(BaseModel):
    """Body for ``POST …/{case_id}/reopen``."""

    reason: str = Field(min_length=1, description="Why the case was reopened")


def _case_json(case: ReconciliationCase) -> dict[str, Any]:
    data = case.model_dump(mode="json")
    data["item_count"] = case.item_count
    return data


def _open_db(request: Request) -> HospitalDatabase:
    configured: Path | str = getattr(
        request.app.state, "medcompare_db_path", default_database_path()
    )
    return HospitalDatabase(configured)


def _resolve_hospital(db: HospitalDatabase, reference: str) -> str | None:
    return db.find_hospital_id(
        preferred=reference,
        source_ref=reference,
        hospital_name=reference,
    )


def _error(status_code: int, message: str, detail: Any = None) -> JSONResponse:
    content: dict[str, Any] = {"error": message}
    if detail is not None:
        content["detail"] = detail
    return JSONResponse(status_code=status_code, content=content)


def _transition_error(exc: ReconciliationStateError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": "Invalid reconciliation state transition",
            "detail": str(exc),
            "case_id": exc.case_id,
            "current": exc.current.value,
            "requested": exc.requested.value,
        },
    )


def _audit_action(
    service: AuthDep,
    *,
    action: str,
    principal: Principal,
    case_id: str,
    correlation_id: str,
    outcome: str = AuditOutcome.SUCCESS,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append one reconciliation audit event (short-lived store)."""
    with service.open_store() as store:
        record_audit(
            store,
            action=action,
            outcome=outcome,
            principal=principal,
            resource_type="reconciliation_case",
            resource_id=case_id,
            correlation_id=correlation_id,
            metadata=metadata or {},
        )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@reconciliation_router.get("/api/v1/hospitals/{hospital_id}/reconciliation-cases")
async def api_list_reconciliation_cases(
    hospital_id: str,
    request: Request,
    status: str | None = None,
) -> JSONResponse:
    """List reconciliation cases for one hospital (newest first)."""
    with _open_db(request) as db:
        resolved = _resolve_hospital(db, hospital_id)
        if resolved is None:
            return _error(404, "Hospital not found", hospital_id)
        cases = list_cases(db, hospital_id=resolved, status=status)
        rows = [
            {
                "case": _case_json(case),
                "events": [
                    event.model_dump(mode="json")
                    for event in list_events(db, case.reconciliation_case_id)
                ],
            }
            for case in cases
        ]
    return JSONResponse(content={"hospital_id": resolved, "cases": rows})


@reconciliation_router.get("/api/v1/reconciliation-cases/{case_id}")
async def api_get_reconciliation_case(case_id: str, request: Request) -> JSONResponse:
    """One full reconciliation case with its evidence package, audit trail,
    report, and submissions."""
    with _open_db(request) as db:
        case = get_case(db, case_id)
        if case is None:
            return _error(404, "Reconciliation case not found", case_id)
        evidence = build_evidence_package(db, case)
        events = list_events(db, case_id)
        report = db.get_report_for_case(case_id)
        submissions = db.list_submissions(report_id=report.report_id) if report else []
    return JSONResponse(
        content={
            "case": _case_json(case),
            "evidence": evidence,
            "events": [event.model_dump(mode="json") for event in events],
            "report": report.model_dump(mode="json") if report else None,
            "submissions": [s.model_dump(mode="json") for s in submissions],
        }
    )


@reconciliation_router.post("/api/v1/reconciliation-cases/{case_id}/review")
async def api_start_review(
    case_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[
        Principal, Depends(require_permission(Permission.REVIEW_RECONCILIATION))
    ],
    body: Annotated[ReviewRequest | None, Body()] = None,
) -> JSONResponse:
    """OPEN / REOPENED -> UNDER_REVIEW."""
    payload = body or ReviewRequest()
    with _open_db(request) as db:
        if get_case(db, case_id) is None:
            return _error(404, "Reconciliation case not found", case_id)
        try:
            case = start_review(
                db,
                case_id,
                actor="manual",
                assigned_to=payload.assigned_to,
                reason=payload.reason,
            )
        except ReconciliationStateError as exc:
            return _transition_error(exc)
    _audit_action(
        service,
        action="reconciliation.review",
        principal=principal,
        case_id=case_id,
        correlation_id=get_request_correlation(request),
        metadata={"assigned_to": payload.assigned_to},
    )
    return JSONResponse(content={"case": _case_json(case)})


@reconciliation_router.post("/api/v1/reconciliation-cases/{case_id}/resolve")
async def api_resolve_case(
    case_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[
        Principal, Depends(require_permission(Permission.REVIEW_RECONCILIATION))
    ],
    body: Annotated[ResolveRequest | None, Body()] = None,
) -> JSONResponse:
    """OPEN / UNDER_REVIEW / REOPENED -> RESOLVED with a recorded decision."""
    with _open_db(request) as db:
        if get_case(db, case_id) is None:
            return _error(404, "Reconciliation case not found", case_id)
        if body is None:
            return _error(422, "A resolution and reason are required")
        try:
            case = resolve_case(
                db,
                case_id,
                resolution=body.resolution,
                reason=body.reason,
                actor="manual",
            )
        except ReconciliationStateError as exc:
            return _transition_error(exc)
    _audit_action(
        service,
        action="reconciliation.resolve",
        principal=principal,
        case_id=case_id,
        correlation_id=get_request_correlation(request),
        metadata={"resolution": body.resolution.value},
    )
    return JSONResponse(content={"case": _case_json(case)})


@reconciliation_router.post("/api/v1/reconciliation-cases/{case_id}/dismiss")
async def api_dismiss_case(
    case_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[
        Principal, Depends(require_permission(Permission.REVIEW_RECONCILIATION))
    ],
    body: Annotated[DismissRequest | None, Body()] = None,
) -> JSONResponse:
    """OPEN / UNDER_REVIEW / REOPENED -> DISMISSED (no action warranted)."""
    with _open_db(request) as db:
        if get_case(db, case_id) is None:
            return _error(404, "Reconciliation case not found", case_id)
        if body is None:
            return _error(422, "A dismissal reason is required")
        try:
            case = dismiss_case(
                db,
                case_id,
                reason=body.reason,
                actor="manual",
            )
        except ReconciliationStateError as exc:
            return _transition_error(exc)
    _audit_action(
        service,
        action="reconciliation.dismiss",
        principal=principal,
        case_id=case_id,
        correlation_id=get_request_correlation(request),
    )
    return JSONResponse(content={"case": _case_json(case)})


@reconciliation_router.post("/api/v1/reconciliation-cases/{case_id}/reopen")
async def api_reopen_case(
    case_id: str,
    request: Request,
    principal: PrincipalDep,
    service: AuthDep,
    _perm: Annotated[
        Principal, Depends(require_permission(Permission.REVIEW_RECONCILIATION))
    ],
    body: Annotated[ReopenRequest | None, Body()] = None,
) -> JSONResponse:
    """RESOLVED / DISMISSED -> REOPENED.  Never creates a new case."""
    with _open_db(request) as db:
        if get_case(db, case_id) is None:
            return _error(404, "Reconciliation case not found", case_id)
        if body is None:
            return _error(422, "A reopen reason is required")
        try:
            case = reopen_case(
                db,
                case_id,
                reason=body.reason,
                actor="manual",
            )
        except ReconciliationStateError as exc:
            return _transition_error(exc)
    _audit_action(
        service,
        action="reconciliation.reopen",
        principal=principal,
        case_id=case_id,
        correlation_id=get_request_correlation(request),
    )
    return JSONResponse(content={"case": _case_json(case)})


# ---------------------------------------------------------------------------
# HTML review pages
# ---------------------------------------------------------------------------


def _display_value(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    return str(value)


def _case_view(
    case: ReconciliationCase,
    evidence: dict[str, Any],
    events: list[Any],
    hospital_name: str,
) -> dict[str, Any]:
    view = _case_json(case)
    view["hospital_name"] = hospital_name
    view["display_values"] = [
        {
            "path": item["path"],
            "category": item["category"],
            "entity_key": item["entity_key"] or "…",
            "field": item["field"] or item["path"],
            "change_type": item["change_type"],
            "outcome": item["outcome"],
            "previous_value": _display_value(item["previous_value"]),
            "authoritative_value": _display_value(item["authoritative_value"]),
            "verification_value": _display_value(item["verification_value"]),
        }
        for item in view.pop("items", [])
    ]
    view["trigger_label"] = ""
    trigger = {
        "mismatch": "Mismatch",
        "unverified": "Unverified",
        "manual": "Manual",
    }.get(case.trigger.value, case.trigger.value)
    view["trigger"] = trigger
    view["status_label"] = {
        "open": "Open",
        "under_review": "Under review",
        "resolved": "Resolved",
        "reopened": "Reopened",
        "dismissed": "Dismissed",
    }[case.status.value]
    view["resolution_label"] = (
        case.resolution.value.replace("_", " ").title()
        if case.resolution is not None
        else None
    )
    view["evidence"] = evidence
    view["events"] = [
        {
            "action": event.action,
            "actor": event.actor,
            "reason": event.reason or "",
            "from_status": event.from_status.value if event.from_status else None,
            "to_status": event.to_status.value if event.to_status else None,
            "created_at": event.created_at.isoformat(),
        }
        for event in events
    ]
    view["allowed_actions"] = _allowed_actions(case.status)
    return view


def _allowed_actions(status: ReconciliationCaseStatus) -> list[str]:
    return {
        "open": ["review", "resolve", "dismiss"],
        "under_review": ["resolve", "dismiss"],
        "resolved": ["reopen"],
        "reopened": ["review", "resolve", "dismiss"],
        "dismissed": ["reopen"],
    }[status.value]


@reconciliation_router.get("/reconciliation-cases/{case_id}", response_class=HTMLResponse)
async def reconciliation_case_page(case_id: str, request: Request) -> HTMLResponse:
    """Render the review screen for one case (progressive disclosure)."""
    with _open_db(request) as db:
        case = get_case(db, case_id)
        if case is None:
            return HTMLResponse(
                content=f'<div class="error">Reconciliation case "{case_id}" not found.</div>'
            )
        evidence = build_evidence_package(db, case)
        events = list_events(db, case_id)
        report = db.get_report_for_case(case_id)
        submissions = db.list_submissions(report_id=report.report_id) if report else []
        hospital_name = ""
        for entry in db.list_hospitals():
            if entry.hospital_id == case.hospital_id:
                hospital_name = entry.name or hospital_name
                break
        view = _case_view(case, evidence, events, hospital_name)
        view["report"] = report.model_dump(mode="json") if report else None
        view["submissions"] = [s.model_dump(mode="json") for s in submissions]
    templates = request.app.state.templates
    return HTMLResponse(
        content=templates.env.get_template("reconciliation_case.html").render(
            request=request,
            case=view,
        )
    )


@reconciliation_router.post("/reconciliation-cases/{case_id}/actions")
async def reconciliation_action_form(
    case_id: str,
    request: Request,
) -> RedirectResponse:
    """HTML form handler for the review buttons (server-side, no JS)."""
    form = await request.form()
    action = str(form.get("action", ""))
    reason = str(form.get("reason", "")).strip()
    resolution = str(form.get("resolution", "")).strip()
    assigned_to = str(form.get("assigned_to", "")).strip() or None

    with _open_db(request) as db:
        if get_case(db, case_id) is None:
            return RedirectResponse(
                f"/reconciliation-cases/{case_id}?error=Case+not+found", status_code=303
            )
        try:
            if action == "review":
                start_review(db, case_id, actor="manual", assigned_to=assigned_to,
                             reason=reason or None)
            elif action == "resolve":
                resolve_case(
                    db,
                    case_id,
                    resolution=ResolutionType(resolution),
                    reason=reason,
                    actor="manual",
                )
            elif action == "dismiss":
                dismiss_case(db, case_id, reason=reason, actor="manual")
            elif action == "reopen":
                reopen_case(db, case_id, reason=reason, actor="manual")
            else:
                return RedirectResponse(
                    f"/reconciliation-cases/{case_id}?error=Unknown+action",
                    status_code=303,
                )
        except ReconciliationStateError as exc:
            return RedirectResponse(
                f"/reconciliation-cases/{case_id}?error={_urlquote(str(exc))}",
                status_code=303,
            )
        except ValueError as exc:
            return RedirectResponse(
                f"/reconciliation-cases/{case_id}?error={_urlquote(str(exc))}",
                status_code=303,
            )
    return RedirectResponse(f"/reconciliation-cases/{case_id}", status_code=303)


def _urlquote(value: str) -> str:
    from urllib.parse import quote

    return quote(value)