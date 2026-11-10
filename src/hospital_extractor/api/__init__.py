"""FastAPI application layer for MedCompare.

Milestone 0 scaffold: health endpoint only, no database or domain-behavior changes.
"""

from __future__ import annotations

from hospital_extractor.api.main import create_app

__all__ = ["create_app"]
