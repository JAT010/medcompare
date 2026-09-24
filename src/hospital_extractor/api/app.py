"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = ["create_app"]

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    *,
    auth_enabled: bool | None = None,
    auth_mode: str | None = None,
    auth_settings_override: dict[str, Any] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Authentication (P24):

    - Defaults from ``auth_settings()`` (env): development mode with
      ``require_auth=False`` unless ``MEDCOMPARE_REQUIRE_AUTH`` /
      ``MEDCOMPARE_AUTH_MODE=production`` say otherwise — keeps local and
      existing automated tests working without tokens.
    - ``auth_enabled=True`` forces fail-closed auth (tests of 401/403).
    - ``auth_enabled=False`` forces the development bypass principal.
    """
    from hospital_extractor.auth.models import AuthMode
    from hospital_extractor.auth.service import get_auth_service
    from hospital_extractor.auth.settings import auth_settings

    overrides: dict[str, Any] = dict(auth_settings_override or {})
    if auth_enabled is not None:
        overrides["require_auth"] = auth_enabled
    if auth_mode is not None:
        overrides["mode"] = auth_mode
        if auth_enabled is None and auth_mode == "production":
            overrides["require_auth"] = True
    settings = auth_settings(**overrides)

    # P25: production startup verification fails closed before the app is
    # usable — no silent fallback to development auth.
    if settings.mode is AuthMode.PRODUCTION:
        from hospital_extractor.ops.startup import verify_startup

        verify_startup(settings=settings)

    app = FastAPI(
        title="Hospital Comparison API",
        description="Evidence-backed hospital comparison for target procedures.",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

    app.state.medcompare_auth_settings = settings
    app.state.medcompare_auth_service = None  # created on first request
    app.state.medcompare_require_auth = settings.require_auth
    app.state.medcompare_auth_mode = settings.mode.value

    # --- security middleware / handlers -------------------------------------
    _install_request_id_middleware(app)
    _install_sanitized_error_handlers(app)
    _install_trusted_hosts(app, settings.trusted_hosts)
    if settings.cors_origins:
        _install_cors(app, settings.cors_origins)
    _install_login_rate_limit_note(app)

    def _ensure_auth_service(request: Request) -> None:
        db_path = getattr(request.app.state, "medcompare_db_path", None)
        if db_path is None:
            # Lazy: first request wins; tests usually set medcompare_db_path.
            from hospital_extractor.storage import default_database_path

            request.app.state.medcompare_db_path = default_database_path()
            db_path = request.app.state.medcompare_db_path
        if request.app.state.medcompare_auth_service is None:
            request.app.state.medcompare_auth_service = get_auth_service(
                db_path, settings=settings
            )
        _maybe_bootstrap_admin(request.app.state.medcompare_auth_service)

    @app.middleware("http")
    async def _auth_service_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith("/api/") or request.url.path in (
            "/compare",
            "/quality",
            "/search",
        ):
            _ensure_auth_service(request)
        return await call_next(request)

    # Mount static files
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Store templates on app state for access in routes
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Register routes
    from fastapi import Depends

    from hospital_extractor.api.auth import auth_router
    from hospital_extractor.api.monitoring import monitoring_router
    from hospital_extractor.api.ops import ops_router
    from hospital_extractor.api.profile import profile_router
    from hospital_extractor.api.quality import quality_router
    from hospital_extractor.api.reconciliation import reconciliation_router
    from hospital_extractor.api.reporting import reporting_router
    from hospital_extractor.api.routes import router
    from hospital_extractor.api.search import search_router
    from hospital_extractor.api.sources import sources_router
    from hospital_extractor.api.verification import verification_router
    from hospital_extractor.auth.dependencies import current_principal

    # Layer 1: every API router resolves a Principal (401 when require_auth).
    # When require_auth is false this is an explicit development bypass (ADMIN).
    # ``routes`` and ``ops_router`` stay unauthenticated so ``/api/v1/health``,
    # ``/health`` and ``/ready`` remain open probes; compare/index declare
    # auth explicitly.
    auth_dep = [Depends(current_principal)]
    app.include_router(router)
    app.include_router(ops_router)
    app.include_router(profile_router, dependencies=auth_dep)
    app.include_router(verification_router, dependencies=auth_dep)
    app.include_router(reconciliation_router, dependencies=auth_dep)
    app.include_router(reporting_router, dependencies=auth_dep)
    app.include_router(monitoring_router, dependencies=auth_dep)
    app.include_router(sources_router, dependencies=auth_dep)
    app.include_router(search_router, dependencies=auth_dep)
    app.include_router(quality_router, dependencies=auth_dep)
    # auth_router: login must stay reachable without a token; protected
    # endpoints declare their own permissions.
    app.include_router(auth_router)

    return app


def _install_request_id_middleware(app: FastAPI) -> None:
    """Attach a non-secret correlation id to every request."""

    @app.middleware("http")
    async def _request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or f"req_{uuid4().hex[:16]}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def _install_sanitized_error_handlers(app: FastAPI) -> None:
    """Never leak stack traces, paths, SQL, or secrets to clients."""

    def _safe(message: str) -> str:
        # Defensive scrub of common leak shapes (defense in depth).
        lowered = message.lower()
        for needle in ("password", "token", "secret", "authorization:"):
            if needle in lowered:
                return "Internal error"
        return message

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(status_code=exc.status_code, content={"detail": detail})

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Field-level validation only; no raw exception payloads.
        errors = [
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "msg": _safe(str(err.get("msg", "invalid"))),
                "type": str(err.get("type", "")),
            }
            for err in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log full detail server-side; client gets a generic body only.
        import logging

        logging.getLogger("hospital_extractor.api").exception(
            "unhandled error path=%s", request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )


def _install_trusted_hosts(app: FastAPI, hosts: tuple[str, ...]) -> None:
    if not hosts:
        return
    try:
        from starlette.middleware.trustedhost import TrustedHostMiddleware
    except ImportError:  # pragma: no cover
        return
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts))


def _install_cors(app: FastAPI, origins: tuple[str, ...]) -> None:
    try:
        from fastapi.middleware.cors import CORSMiddleware
    except ImportError:  # pragma: no cover
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        max_age=600,
    )


def _install_login_rate_limit_note(app: FastAPI) -> None:
    # Rate limiting for login is enforced inside AuthService._LoginRateLimiter;
    # this hook exists so production operators can see the control is wired.
    app.state.medcompare_login_rate_limited = True


def _maybe_bootstrap_admin(service: Any) -> None:
    """Create the initial ADMIN from env — only when credentials are set.

    Production never ships a hardcoded admin password; if
    ``MEDCOMPARE_BOOTSTRAP_ADMIN_PASSWORD`` is set and no users exist,
    create ``MEDCOMPARE_BOOTSTRAP_ADMIN_USERNAME`` (default ``admin``).
    """
    import os

    password = os.environ.get("MEDCOMPARE_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not password:
        return
    username = os.environ.get("MEDCOMPARE_BOOTSTRAP_ADMIN_USERNAME", "admin")
    from hospital_extractor.auth.models import Role, UserCreate
    from hospital_extractor.auth.service import AuthServiceError

    try:
        store = service.open_store()
    except Exception:  # pragma: no cover
        return
    with store:
        if store.list_users():
            return
        try:
            service.create_user(
                UserCreate(username=username, password=password, role=Role.ADMIN)
            )
        except AuthServiceError:
            pass
        except Exception:  # pragma: no cover
            pass
