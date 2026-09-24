"""Deterministic next-run calculation for monitoring schedules.

All timestamps are UTC.  Frequencies are fixed intervals from a reference
instant — there is no cron expression, timezone database, or calendar
awareness (weekly means +7 days from the reference, not “Monday 09:00”).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hospital_extractor.monitoring.models import ScheduleFrequency

__all__ = ["as_utc", "next_run_at"]


def as_utc(value: datetime) -> datetime:
    """Normalize to timezone-aware UTC (naive values are treated as UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def next_run_at(
    frequency: ScheduleFrequency,
    *,
    after: datetime,
    min_interval_minutes: int = 0,
) -> datetime:
    """Next due time for *frequency*, at least *min_interval_minutes* after *after*.

    - HOURLY  → after + 1 hour
    - DAILY   → after + 1 day
    - WEEKLY  → after + 7 days

    ``min_interval_minutes`` raises the floor (never lowers it) so rapid
    enable/disable or manual runs cannot create a tight loop.
    """
    reference = as_utc(after)
    if frequency is ScheduleFrequency.HOURLY:
        candidate = reference + timedelta(hours=1)
    elif frequency is ScheduleFrequency.DAILY:
        candidate = reference + timedelta(days=1)
    elif frequency is ScheduleFrequency.WEEKLY:
        candidate = reference + timedelta(weeks=1)
    else:  # pragma: no cover — exhaustive on StrEnum
        raise ValueError(f"unsupported schedule frequency: {frequency!r}")
    floor = reference + timedelta(minutes=max(0, min_interval_minutes))
    return candidate if candidate >= floor else floor
