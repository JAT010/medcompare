"""Runtime tuning for the verification orchestrator.

All values have safe defaults; ``delay_minutes`` defaults to 0 so tests and
the demo run without waiting, while a production deployment sets a positive
delay via ``--delay-minutes`` or the ``VERIFICATION_DELAY_MINUTES``
environment variable so changes are confirmed only after a cool-down period.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

__all__ = [
    "DEFAULT_TARGET_SOURCE_TYPE",
    "VerificationSettings",
    "verification_settings",
]

DEFAULT_TARGET_SOURCE_TYPE = "website"
DEFAULT_VERIFICATION_DELAY_MINUTES = 0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_MINUTES = 2
DEFAULT_WORKER_INTERVAL_SECONDS = 10.0


@dataclass(frozen=True)
class VerificationSettings:
    """Tuning parameters shared by scheduler, worker and API."""

    delay_minutes: int = DEFAULT_VERIFICATION_DELAY_MINUTES
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_minutes: int = DEFAULT_BACKOFF_MINUTES
    worker_interval_seconds: float = DEFAULT_WORKER_INTERVAL_SECONDS
    target_source_type: str = DEFAULT_TARGET_SOURCE_TYPE


def verification_settings(**overrides: object) -> VerificationSettings:
    """Settings with environment-variable defaults, then keyword overrides.

    Environment fallbacks: ``VERIFICATION_DELAY_MINUTES``,
    ``VERIFICATION_MAX_ATTEMPTS``, ``VERIFICATION_BACKOFF_MINUTES``.
    Deterministic callers (tests, demo) pass explicit keyword values and never
    sleep.
    """
    settings = VerificationSettings(
        delay_minutes=_env_int("VERIFICATION_DELAY_MINUTES", DEFAULT_VERIFICATION_DELAY_MINUTES),
        max_attempts=_env_int("VERIFICATION_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
        backoff_minutes=_env_int("VERIFICATION_BACKOFF_MINUTES", DEFAULT_BACKOFF_MINUTES),
    )
    return replace(settings, **overrides)  # type: ignore[arg-type]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default