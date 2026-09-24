"""HTTP API for the hospital comparison engine.

Provides a lightweight FastAPI application with:

- ``POST /api/v1/compare`` — JSON API for programmatic comparison
- ``GET /`` — HTML frontend with HTMX-driven comparison
- ``GET /health`` / ``GET /api/v1/health`` — liveness probe
- ``GET /ready`` — readiness probe (database + config + assets)

The API does NOT implement a job queue, database, websocket or Redis
infrastructure in V1.  Extraction runs synchronously within the request.
"""

from __future__ import annotations

from hospital_extractor.api.app import create_app

__all__ = ["create_app"]
