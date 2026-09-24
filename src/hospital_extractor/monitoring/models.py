"""P20 monitoring models: policies keyed by canonical hospital identity."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MonitoringPolicy",
    "MonitoringPolicyStatus",
    "ScheduleFrequency",
]


class ScheduleFrequency(StrEnum):
    """Supported fixed schedules (UTC; no cron / calendar semantics)."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


class MonitoringPolicyStatus(StrEnum):
    """Lifecycle of one monitoring policy row.

    - ``PENDING``  — waiting for ``next_run_at`` (or cooling down after backoff).
    - ``RUNNING``  — claimed by a worker for one orchestration attempt.
    - ``FAILED``   — retries exhausted for the current window; not due until
      re-enabled or the next schedule boundary advances ``next_run_at``.
    """

    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"


class MonitoringPolicy(BaseModel):
    """One continuous-monitoring schedule for a hospital/source/target.

    Identity is always the canonical ``hospital_id`` (never a display name).
    At most one policy exists per ``(hospital_id, source_type, target_ref)``.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    policy_id: str
    hospital_id: str
    source_type: str = "website"
    target_ref: str = ""
    frequency: ScheduleFrequency
    enabled: bool = True
    status: MonitoringPolicyStatus = MonitoringPolicyStatus.PENDING
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    last_error: str | None = None
    last_orchestration_run_id: str | None = None
    last_change_set_status: str | None = None
    created_at: datetime
    updated_at: datetime
    created_by: str = "system"
