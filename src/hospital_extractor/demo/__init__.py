"""Deterministic demo environment seeding (fictional data only)."""

from __future__ import annotations

from hospital_extractor.demo.seed import (
    DEMO_ADMIN_PASSWORD_ENV,
    DEMO_DB_PATH,
    DEMO_USER_PASSWORD_ENV,
    SeedSummary,
    seed_demo,
)

__all__ = [
    "DEMO_ADMIN_PASSWORD_ENV",
    "DEMO_DB_PATH",
    "DEMO_USER_PASSWORD_ENV",
    "SeedSummary",
    "seed_demo",
]
