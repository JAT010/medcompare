"""Runtime tuning for continuous monitoring (P20).

All values have safe defaults; deterministic callers (tests, CLI flags)
pass explicit keyword overrides and never sleep.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

__all__ = [
    "DEFAULT_BACKOFF_MINUTES",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_POLICIES_PER_CYCLE",
    "DEFAULT_MIN_INTERVAL_MINUTES",
    "DEFAULT_WORKER_INTERVAL_SECONDS",
    "MonitoringSettings",
    "monitoring_settings",
]

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_MINUTES = 5
DEFAULT_MIN_INTERVAL_MINUTES = 60
DEFAULT_WORKER_INTERVAL_SECONDS = 30.0
DEFAULT_MAX_POLICIES_PER_CYCLE = 20


@dataclass(frozen=True)
class MonitoringSettings:
    """Tuning shared by the monitoring store, service and worker."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_minutes: int = DEFAULT_BACKOFF_MINUTES
    min_interval_minutes: int = DEFAULT_MIN_INTERVAL_MINUTES
    worker_interval_seconds: float = DEFAULT_WORKER_INTERVAL_SECONDS
    max_policies_per_cycle: int = DEFAULT_MAX_POLICIES_PER_CYCLE


def monitoring_settings(**overrides: object) -> MonitoringSettings:
    """Settings with environment-variable defaults, then keyword overrides.

    Environment fallbacks: ``MONITORING_MAX_ATTEMPTS``,
    ``MONITORING_BACKOFF_MINUTES``, ``MONITORING_MIN_INTERVAL_MINUTES``,
    ``MONITORING_WORKER_INTERVAL_SECONDS``, ``MONITORING_MAX_POLICIES_PER_CYCLE``.
    """
    settings = MonitoringSettings(
        max_attempts=_env_int("MONITORING_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
        backoff_minutes=_env_int("MONITORING_BACKOFF_MINUTES", DEFAULT_BACKOFF_MINUTES),
        min_interval_minutes=_env_int(
            "MONITORING_MIN_INTERVAL_MINUTES", DEFAULT_MIN_INTERVAL_MINUTES
        ),
        worker_interval_seconds=_env_float(
            "MONITORING_WORKER_INTERVAL_SECONDS", DEFAULT_WORKER_INTERVAL_SECONDS
        ),
        max_policies_per_cycle=_env_int(
            "MONITORING_MAX_POLICIES_PER_CYCLE", DEFAULT_MAX_POLICIES_PER_CYCLE
        ),
    )
    return replace(settings, **overrides)  # type: ignore[arg-type]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default
