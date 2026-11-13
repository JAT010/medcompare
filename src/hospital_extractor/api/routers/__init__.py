"""API routers."""

from __future__ import annotations

from hospital_extractor.api.routers import comparison, crawl, facilities
from hospital_extractor.api.routers.health import router as health_router

__all__ = ["comparison", "crawl", "facilities", "health_router"]
