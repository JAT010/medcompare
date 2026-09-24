"""Runtime policy for the orchestration workflow."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

__all__ = ["OrchestrationSettings", "orchestration_settings"]


@dataclass(frozen=True)
class OrchestrationSettings:
    """Tuning shared by the orchestration service."""

    # Whether to auto-generate reports after verification/reconciliation
    auto_generate_report: bool = True

    # Whether to auto-submit reports (requires explicit authorization)
    auto_submit_report: bool = False

    # Default report format
    default_report_format: str = "json"

    # Whether to enable mock provider by default
    enable_mock_provider: bool = True

    # Default report type when generating
    default_report_type: str = "reconciliation_report"

    # Whether to create reconciliation cases for unverified results
    create_cases_for_unverified: bool = False

    # Whether external submission requires explicit authorization
    require_authorization_for_external: bool = True


def orchestration_settings(**overrides: object) -> OrchestrationSettings:
    """Settings with environment-variable defaults, then keyword overrides."""
    settings = OrchestrationSettings(
        auto_generate_report=_env_bool("ORCHESTRATION_AUTO_GENERATE_REPORT", True),
        auto_submit_report=_env_bool("ORCHESTRATION_AUTO_SUBMIT_REPORT", False),
        enable_mock_provider=_env_bool("ORCHESTRATION_ENABLE_MOCK_PROVIDER", True),
        require_authorization_for_external=_env_bool(
            "ORCHESTRATION_REQUIRE_AUTHORIZATION", True
        ),
    )
    return replace(settings, **overrides)  # type: ignore[arg-type]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}