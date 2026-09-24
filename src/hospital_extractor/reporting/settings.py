"""Runtime policy for the reporting workflow."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

__all__ = ["ReportingSettings", "reporting_settings"]


@dataclass(frozen=True)
class ReportingSettings:
    """Tuning shared by the reporting service."""

    default_report_format: str = "json"
    enable_mock_provider: bool = True
    require_authorization_for_external: bool = True


def reporting_settings(**overrides: object) -> ReportingSettings:
    """Settings with environment-variable defaults, then keyword overrides."""
    settings = ReportingSettings(
        enable_mock_provider=_env_bool("REPORTING_ENABLE_MOCK_PROVIDER", True),
        require_authorization_for_external=_env_bool(
            "REPORTING_REQUIRE_AUTHORIZATION", True
        ),
    )
    return replace(settings, **overrides)  # type: ignore[arg-type]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}