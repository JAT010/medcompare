"""Environment-aware settings for the MedCompare API scaffold."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """Settings for the FastAPI application layer.

    No database settings are defined in Milestone 0.
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


def get_settings() -> ApiSettings:
    """Return API settings loaded from the environment."""
    return ApiSettings()
