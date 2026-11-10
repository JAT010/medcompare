"""Health check endpoint for the API scaffold."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Return service health without requiring a database."""
    return {"status": "ok"}
