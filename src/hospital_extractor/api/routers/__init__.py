"""API routers."""

from __future__ import annotations

from hospital_extractor.api.routers import comparison, consistency, crawl, facilities
from hospital_extractor.api.routers.health import router as health_router
from hospital_extractor.api.routers.integrations import router as integrations_router

__all__ = [
    "comparison",
    "consistency",
    "crawl",
    "facilities",
    "health_router",
    "integrations_router",
]
