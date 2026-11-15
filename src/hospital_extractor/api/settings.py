"""Environment-aware settings for the MedCompare API scaffold."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """Settings for the FastAPI application layer.

    No database settings are defined in Milestone 0; database configuration
    lives in :mod:`hospital_extractor.persistence.config`.
    """

    model_config = SettingsConfigDict(
        env_prefix="MEDCOMPARE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="MedCompare API")
    api_v1_prefix: str = Field(default="/api/v1")
    debug: bool = Field(default=False)

    # Browser origins allowed to call the API (Vite dev server by default).
    # Override with MEDCOMPARE_CORS_ORIGINS as a JSON list.
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "http://localhost:3000",
        ]
    )

    # Website extraction runs inside the request; this bounds how long one may
    # take before the API reports that a persistent job store is required.
    crawl_timeout_seconds: float = Field(default=120.0, ge=1.0, le=900.0)
    crawl_output_dir: str = Field(default="data")


def get_settings() -> ApiSettings:
    """Return API settings loaded from the environment."""
    return ApiSettings()
