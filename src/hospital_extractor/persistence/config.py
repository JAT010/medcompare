"""Database configuration for the persistence layer."""

from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://medcompare:medcompare_dev_only@localhost:5432/medcompare"
)


class PersistenceSettings(BaseSettings):
    """Environment-aware persistence settings.

    Existing CLI/domain code must remain usable without database configuration.
    """

    model_config = SettingsConfigDict(
        env_prefix="MEDCOMPARE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default=DEFAULT_DATABASE_URL)
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout_seconds: int = Field(default=30, ge=1)
    db_pool_recycle_seconds: int = Field(default=1800, ge=1)


@dataclass(frozen=True)
class DatabaseConfig:
    """Resolved database configuration."""

    url: str
    pool_size: int
    max_overflow: int
    pool_timeout_seconds: int
    pool_recycle_seconds: int


def get_database_url() -> str:
    """Return configured database URL (MEDCOMPARE_DATABASE_URL preferred)."""
    env = os.getenv("MEDCOMPARE_DATABASE_URL")
    if env:
        return env
    return PersistenceSettings().database_url


def get_database_config() -> DatabaseConfig:
    settings = PersistenceSettings()
    return DatabaseConfig(
        url=os.getenv("MEDCOMPARE_DATABASE_URL", settings.database_url),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout_seconds=settings.db_pool_timeout_seconds,
        pool_recycle_seconds=settings.db_pool_recycle_seconds,
    )
