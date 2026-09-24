"""Health and readiness endpoints (P25).

- ``GET /health`` and ``GET /api/v1/health`` — liveness: process is up.
- ``GET /ready`` — readiness: database reachable, configuration sane.

Both return deterministic JSON. Readiness never includes secrets, stack
traces, SQL, or absolute filesystem paths beyond a boolean/detail enum.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

__all__ = ["ops_router", "readiness_payload"]

ops_router = APIRouter()


@ops_router.get("/health")
async def health_alias() -> dict[str, str]:
    """Liveness probe (alias of ``/api/v1/health``)."""
    return {"status": "ok", "check": "liveness"}


async def readiness_payload(request: Request) -> dict[str, Any]:
    """Build the readiness document (shared by ``/ready``)."""
    from hospital_extractor.auth.settings import auth_settings

    checks: list[dict[str, Any]] = []
    ok = True

    # Config: re-read auth settings from env (cheap, no secrets in output).
    try:
        settings = auth_settings()
        config_ok = True
        if settings.mode.value == "production" and not settings.require_auth:
            config_ok = False
        checks.append(
            {
                "name": "config",
                "status": "ok" if config_ok else "error",
                "detail": (
                    f"mode={settings.mode.value}, require_auth={settings.require_auth}"
                ),
            }
        )
        ok = ok and config_ok
    except Exception:
        checks.append({"name": "config", "status": "error", "detail": "invalid"})
        ok = False

    # Database: resolve path and open a trivial read.
    db_path = getattr(request.app.state, "medcompare_db_path", None)
    if db_path is None:
        from hospital_extractor.storage import default_database_path

        db_path = default_database_path()
    try:
        from hospital_extractor.storage import HospitalDatabase

        with HospitalDatabase(db_path) as db:
            # Round-trip a metadata query — fails if schema is unusable.
            db.list_hospitals()
        checks.append({"name": "database", "status": "ok", "detail": "reachable"})
    except Exception:
        checks.append({"name": "database", "status": "error", "detail": "unreachable"})
        ok = False

    # Static + templates presence (packaging sanity).
    from hospital_extractor.api.app import _STATIC_DIR, _TEMPLATES_DIR

    templates_ok = _TEMPLATES_DIR.is_dir() and (_TEMPLATES_DIR / "index.html").is_file()
    checks.append(
        {
            "name": "templates",
            "status": "ok" if templates_ok else "error",
            "detail": "present" if templates_ok else "missing",
        }
    )
    ok = ok and templates_ok

    static_ok = _STATIC_DIR.is_dir()
    checks.append(
        {
            "name": "static",
            "status": "ok" if static_ok else "error",
            "detail": "present" if static_ok else "missing",
        }
    )
    ok = ok and static_ok

    return {"status": "ok" if ok else "error", "checks": checks}


@ops_router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness probe: DB + config + assets. 503 when not ready."""
    payload = await readiness_payload(request)
    status = 200 if payload["status"] == "ok" else 503
    return JSONResponse(status_code=status, content=payload)
