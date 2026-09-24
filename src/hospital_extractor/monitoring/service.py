"""P20 continuous monitoring: policies, worker cycle, orchestration hook.

Monitoring does **not** reimplement capture/verification/reporting — each due
policy claims atomically and delegates to the existing P19-E orchestration
service with a deterministic idempotency key.  A ``no_change`` orchestration
short-circuits inside orchestration itself; monitoring only records the
outcome and computes the next schedule.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from hospital_extractor.monitoring.models import (
    MonitoringPolicy,
    MonitoringPolicyStatus,
    ScheduleFrequency,
)
from hospital_extractor.monitoring.schedule import as_utc
from hospital_extractor.monitoring.settings import (
    MonitoringSettings,
    monitoring_settings,
)
from hospital_extractor.monitoring.store import MonitoringStore
from hospital_extractor.orchestration.models import OrchestrationRun, OrchestrationStatus
from hospital_extractor.storage.database import HospitalDatabase

__all__ = [
    "OrchestrateFn",
    "MonitoringPermanentError",
    "MonitoringRetryableError",
    "build_idempotency_key",
    "create_policy",
    "list_policies",
    "run_monitoring_cycle",
    "run_monitoring_worker",
    "run_policy_now",
    "set_policy_enabled",
]

OrchestrateFn = Callable[..., Awaitable[OrchestrationRun]]


class MonitoringPermanentError(RuntimeError):
    """The policy can never succeed as configured (no retries)."""


class MonitoringRetryableError(RuntimeError):
    """Temporary failure — retryable up to the bounded attempt limit."""


def _now() -> datetime:
    return datetime.now(UTC)


def create_policy(
    db: HospitalDatabase,
    *,
    hospital_id: str,
    frequency: ScheduleFrequency | str,
    source_type: str = "website",
    target_ref: str = "",
    enabled: bool = True,
    created_by: str = "system",
    settings: MonitoringSettings | None = None,
    now: datetime | None = None,
) -> MonitoringPolicy:
    """Create (or idempotently update) a monitoring policy for one identity."""
    store = MonitoringStore(db, settings=settings)
    try:
        freq = (
            frequency
            if isinstance(frequency, ScheduleFrequency)
            else ScheduleFrequency(str(frequency))
        )
        return store.create(
            hospital_id=hospital_id,
            frequency=freq,
            source_type=source_type,
            target_ref=target_ref,
            enabled=enabled,
            created_by=created_by,
            now=now,
        )
    finally:
        store.close()


def list_policies(
    db: HospitalDatabase,
    *,
    hospital_id: str | None = None,
    enabled: bool | None = None,
    settings: MonitoringSettings | None = None,
) -> list[MonitoringPolicy]:
    """List monitoring policies (optionally filtered by hospital / enabled)."""
    store = MonitoringStore(db, settings=settings)
    try:
        return store.list(hospital_id=hospital_id, enabled=enabled)
    finally:
        store.close()


def set_policy_enabled(
    db: HospitalDatabase,
    policy_id: str,
    *,
    enabled: bool,
    settings: MonitoringSettings | None = None,
    now: datetime | None = None,
) -> MonitoringPolicy | None:
    """Enable or disable one policy (enable resets a FAILED policy)."""
    store = MonitoringStore(db, settings=settings)
    try:
        return store.set_enabled(policy_id, enabled=enabled, now=now)
    finally:
        store.close()


def build_idempotency_key(policy: MonitoringPolicy, window: datetime) -> str:
    """Deterministic orchestration key for one policy + schedule window."""
    window_key = as_utc(window).strftime("%Y%m%dT%H%M%SZ")
    return f"monitoring:{policy.policy_id}:{window_key}"


async def _default_orchestrate(
    db_path: Path | str,
    *,
    hospital_id: str,
    source_type: str,
    target_ref: str,
    idempotency_key: str,
) -> OrchestrationRun:
    from hospital_extractor.orchestration.service import run_orchestration

    return await run_orchestration(
        hospital_id=hospital_id,
        source_type=source_type,
        target_ref=target_ref,
        idempotency_key=idempotency_key,
        db_path=db_path,
    )


async def _execute_policy(
    store: MonitoringStore,
    policy: MonitoringPolicy,
    *,
    orchestrate: OrchestrateFn,
    now: datetime,
    db_path: Path | str,
) -> None:
    """Run one claimed policy through orchestration; complete or fail it."""
    window = policy.next_run_at or now
    key = build_idempotency_key(policy, window)
    try:
        run = await orchestrate(
            db_path,
            hospital_id=policy.hospital_id,
            source_type=policy.source_type,
            target_ref=policy.target_ref,
            idempotency_key=key,
        )
    except MonitoringPermanentError as exc:
        store.fail(policy.policy_id, error=str(exc), retryable=False, now=now)
        return
    except Exception as exc:  # noqa: BLE001 — orchestration failures become policy failures
        store.fail(policy.policy_id, error=str(exc), retryable=True, now=now)
        return

    # Orchestration already short-circuits NO_CHANGE internally (no duplicate
    # verification/reconciliation/report).  Monitoring only records the result.
    if run.status is OrchestrationStatus.FAILED:
        store.fail(
            policy.policy_id,
            error=run.error or "orchestration failed",
            retryable=True,
            orchestration_run_id=run.run_id,
            now=now,
        )
        return

    store.complete(
        policy.policy_id,
        orchestration_run_id=run.run_id,
        change_set_status=run.change_set_status,
        now=now,
    )


async def run_monitoring_cycle(
    db: HospitalDatabase,
    *,
    settings: MonitoringSettings | None = None,
    orchestrate: OrchestrateFn | None = None,
    now: datetime | None = None,
) -> int:
    """Process every due policy once; returns the number of policies processed.

    - Claim atomically (PENDING → RUNNING) with a per-cycle bound.
    - Delegate to orchestration under a deterministic idempotency key.
    - On failure: re-queue with backoff until ``max_attempts``, then FAILED.
    """
    settings = settings or monitoring_settings()
    timestamp = as_utc(now or _now())
    store = MonitoringStore(db, settings=settings)
    orchestrator: OrchestrateFn = orchestrate or _default_orchestrate
    processed = 0
    try:
        due = store.get_due(timestamp, limit=settings.max_policies_per_cycle)
        for candidate in due:
            claimed = store.claim(candidate, timestamp)
            if claimed is None:
                continue  # another worker got it (per-policy in-flight protection)
            await _execute_policy(
                store,
                claimed,
                orchestrate=orchestrator,
                now=timestamp,
                db_path=db.path,
            )
            processed += 1
    finally:
        store.close()
    return processed


async def run_monitoring_worker(
    db: HospitalDatabase,
    *,
    settings: MonitoringSettings | None = None,
    orchestrate: OrchestrateFn | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Continuous monitoring worker loop (the CLI ``monitoring worker``)."""
    settings = settings or monitoring_settings()
    interval = settings.worker_interval_seconds
    while True:
        await run_monitoring_cycle(db, settings=settings, orchestrate=orchestrate)
        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except TimeoutError:
                continue


async def run_policy_now(
    db: HospitalDatabase,
    policy_id: str,
    *,
    settings: MonitoringSettings | None = None,
    orchestrate: OrchestrateFn | None = None,
    now: datetime | None = None,
) -> MonitoringPolicy | None:
    """Manually trigger one policy immediately (atomic claim + idempotent)."""
    settings = settings or monitoring_settings()
    timestamp = as_utc(now or _now())
    store = MonitoringStore(db, settings=settings)
    orchestrator: OrchestrateFn = orchestrate or _default_orchestrate
    try:
        policy = store.get(policy_id)
        if policy is None:
            return None
        if policy.status is MonitoringPolicyStatus.RUNNING:
            return policy
        store.set_enabled(policy_id, enabled=True, now=timestamp)
        due = store.get(policy_id)
        if due is None:
            return None
        if due.next_run_at is None or as_utc(due.next_run_at) > timestamp:
            store.force_due(policy_id, now=timestamp)
            refreshed = store.get(policy_id)
            if refreshed is None:
                return None
            due = refreshed
        claimed = store.claim(due, timestamp)
        if claimed is None:
            return store.get(policy_id)
        await _execute_policy(
            store,
            claimed,
            orchestrate=orchestrator,
            now=timestamp,
            db_path=db.path,
        )
        return store.get(policy_id)
    finally:
        store.close()
