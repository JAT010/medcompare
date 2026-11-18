"""Monitoring schedules: periodic captures that detect drift over time.

The scheduler never crawls the website and never writes snapshots itself; it
triggers the same capture → change detection → verification-job pipeline as a
manual capture, recording one immutable execution row per decision.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hospital_extractor.persistence.models_integration import (
    HospitalMonitoringExecution,
    HospitalMonitoringSchedule,
    HospitalSourceIntegration,
    IntegrationStatus,
    MonitoringExecutionStatus,
)
from hospital_extractor.verification.orchestrator import capture_and_enqueue

__all__ = [
    "MonitoringError",
    "MonitoringExecutionResult",
    "MonitoringService",
]


class MonitoringError(RuntimeError):
    """Monitoring schedule operation failed."""


class MonitoringExecutionResult:
    def __init__(
        self,
        execution: HospitalMonitoringExecution,
        schedule: HospitalMonitoringSchedule,
    ) -> None:
        self.execution = execution
        self.schedule = schedule


class MonitoringService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_schedule(
        self,
        *,
        facility_id: uuid.UUID,
        integration_id: uuid.UUID,
        name: str,
        interval_seconds: int,
        enabled: bool = False,
        config_json: dict[str, Any] | None = None,
    ) -> HospitalMonitoringSchedule:
        if interval_seconds < 60:
            raise MonitoringError("interval_seconds must be at least 60")
        schedule = HospitalMonitoringSchedule(
            facility_id=facility_id,
            integration_id=integration_id,
            name=name.strip(),
            interval_seconds=interval_seconds,
            enabled=enabled,
            next_run_at=datetime.now(UTC) if enabled else None,
            last_status=None,
            config_json=config_json or {},
        )
        self.session.add(schedule)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise MonitoringError(
                f"a schedule named {name!r} already exists for this facility/integration"
            ) from exc
        return schedule

    def set_enabled(
        self, schedule: HospitalMonitoringSchedule, enabled: bool
    ) -> HospitalMonitoringSchedule:
        schedule.enabled = enabled
        if enabled and schedule.next_run_at is None:
            schedule.next_run_at = datetime.now(UTC)
        if not enabled:
            schedule.next_run_at = None
        self.session.flush()
        return schedule

    def due_schedules(self, now: datetime | None = None) -> list[HospitalMonitoringSchedule]:
        now = now or datetime.now(UTC)
        stmt = (
            select(HospitalMonitoringSchedule)
            .where(
                HospitalMonitoringSchedule.enabled.is_(True),
                HospitalMonitoringSchedule.next_run_at.is_not(None),
                HospitalMonitoringSchedule.next_run_at <= now,
            )
            .order_by(HospitalMonitoringSchedule.next_run_at)
        )
        return list(self.session.scalars(stmt).all())

    async def process_due(self, *, now: datetime | None = None) -> list[MonitoringExecutionResult]:
        """Run captures for due schedules; record one execution per schedule."""
        now = now or datetime.now(UTC)
        results: list[MonitoringExecutionResult] = []
        for schedule in self.due_schedules(now):
            results.append(await self._process_one(schedule, now))
        return results

    async def _process_one(
        self, schedule: HospitalMonitoringSchedule, now: datetime
    ) -> MonitoringExecutionResult:
        integration = self.session.get(HospitalSourceIntegration, schedule.integration_id)
        assert integration is not None

        def execution(
            status: str,
            *,
            job_id: uuid.UUID | None,
            reason: str | None,
            scheduled_for: datetime | None,
        ) -> MonitoringExecutionResult:
            row = HospitalMonitoringExecution(
                schedule_id=schedule.id,
                facility_id=schedule.facility_id,
                job_id=job_id,
                status=status,
                reason=reason,
                scheduled_for=scheduled_for,
                executed_at=now,
            )
            self.session.add(row)
            # Advance the schedule even on failure to avoid a hot loop.
            schedule.next_run_at = now + timedelta(seconds=schedule.interval_seconds)
            schedule.last_enqueued_at = now if job_id is not None else schedule.last_enqueued_at
            schedule.last_status = status
            self.session.flush()
            return MonitoringExecutionResult(row, schedule)

        if (
            integration.status
            not in {
                IntegrationStatus.CONFIGURED.value,
            }
            or not integration.enabled
        ):
            return execution(
                MonitoringExecutionStatus.SKIPPED.value,
                job_id=None,
                reason="integration_not_configured",
                scheduled_for=schedule.next_run_at,
            )

        config = schedule.config_json if isinstance(schedule.config_json, dict) else {}
        target_ref = config.get("target_ref")
        target_ref = target_ref if isinstance(target_ref, str) and target_ref else None
        try:
            outcome = await capture_and_enqueue(
                self.session,
                integration,
                target_ref=target_ref,
                trigger_type="monitoring",
                captured_by="monitoring",
            )
        except Exception as exc:  # connector failures are audit-recorded, not fatal
            return execution(
                MonitoringExecutionStatus.ERROR.value,
                job_id=None,
                reason=f"capture_failed: {type(exc).__name__}"[:255],
                scheduled_for=schedule.next_run_at,
            )

        job = outcome.verification_job
        if job is None:
            return execution(
                MonitoringExecutionStatus.SKIPPED.value,
                job_id=None,
                reason=outcome.job_skip_reason or "no_detected_changes",
                scheduled_for=schedule.next_run_at,
            )
        return execution(
            MonitoringExecutionStatus.ENQUEUED.value,
            job_id=job.id,
            reason=None,
            scheduled_for=schedule.next_run_at,
        )

    def list_schedules(
        self,
        facility_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[HospitalMonitoringSchedule]:
        stmt = (
            select(HospitalMonitoringSchedule)
            .where(HospitalMonitoringSchedule.facility_id == facility_id)
            .order_by(HospitalMonitoringSchedule.name, HospitalMonitoringSchedule.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def list_executions(
        self,
        facility_id: uuid.UUID,
        *,
        schedule_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[HospitalMonitoringExecution]:
        stmt = select(HospitalMonitoringExecution).where(
            HospitalMonitoringExecution.facility_id == facility_id
        )
        if schedule_id is not None:
            stmt = stmt.where(HospitalMonitoringExecution.schedule_id == schedule_id)
        stmt = (
            stmt.order_by(HospitalMonitoringExecution.executed_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())
