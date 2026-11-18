"""FastAPI application factory for MedCompare."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hospital_extractor.api.routers import (
    comparison,
    consistency,
    crawl,
    facilities,
    health_router,
    integrations_router,
)
from hospital_extractor.api.settings import ApiSettings, get_settings


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    The health endpoint does not require a database; the registry endpoints
    read ``MEDCOMPARE_DATABASE_URL`` when they are called.
    """
    app_settings = settings or get_settings()
    app = FastAPI(title=app_settings.app_name, debug=app_settings.debug)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router, prefix=app_settings.api_v1_prefix)
    app.include_router(facilities.router, prefix=app_settings.api_v1_prefix)
    app.include_router(comparison.router, prefix=app_settings.api_v1_prefix)
    app.include_router(consistency.router, prefix=app_settings.api_v1_prefix)
    app.include_router(crawl.router, prefix=app_settings.api_v1_prefix)
    app.include_router(integrations_router, prefix=app_settings.api_v1_prefix)
    return app
