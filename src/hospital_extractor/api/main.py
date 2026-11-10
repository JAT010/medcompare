"""FastAPI application factory for MedCompare."""

from __future__ import annotations

from fastapi import FastAPI

from hospital_extractor.api.routers import health_router
from hospital_extractor.api.settings import ApiSettings, get_settings


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    The health endpoint does not require a database.
    """
    app_settings = settings or get_settings()
    app = FastAPI(title=app_settings.app_name, debug=app_settings.debug)
    app.include_router(health_router, prefix=app_settings.api_v1_prefix)
    return app
