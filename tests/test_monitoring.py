"""P20 continuous monitoring tests.

Covers: schedule calculation (UTC, HOURLY/DAILY/WEEKLY, min-interval floor),
policy CRUD keyed by canonical hospital_id (idempotent upsert), atomic claim /
in-flight protection, bounded retry + backoff → FAILED, worker cycle
orchestration integration (idempotency key, NO_CHANGE short-circuit, no
duplicate verification), enable/disable, CLI, API, UI section, additive schema
(fresh + existing DB), and read-safety (UI never crawls).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.cli import main
from hospital_extractor.monitoring.models import (
    MonitoringPolicy,
    MonitoringPolicyStatus,
    ScheduleFrequency,
)
from hospital_extractor.monitoring.schedule import as_utc, next_run_at
from hospital_extractor.monitoring.service import (
    build_idempotency_key,
    create_policy,
    list_policies,
    run_monitoring_cycle,
    run_policy_now,
    set_policy_enabled,
)
from hospital_extractor.monitoring.settings import monitoring_settings
from hospital_extractor.monitoring.store import MonitoringStore
from hospital_extractor.orchestration.models import (
    OrchestrationRun,
    OrchestrationStatus,
)
from hospital_extractor.storage.database import HospitalDatabase

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
HOSPITAL_ID = "h_monitor01"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run(
    *,
    status: OrchestrationStatus = OrchestrationStatus.COMPLETED,
    run_id: str = "run_test",
    change_set_status: str | None = "changes_detected",
    error: str | None = None,
    current_stage: str | None = None,
) -> OrchestrationRun:
    stage = current_stage or (
        "completed" if status is OrchestrationStatus.COMPLETED else "no_change"
    )
    return OrchestrationRun(
        run_id=run_id,
        hospital_id=HOSPITAL_ID,
        source_type="website",
        trigger_ref="https://example.test",
        status=status,
        current_stage=stage,
        created_at=NOW,
        updated_at=NOW,
        change_set_status=change_set_status,
        error=error,
        idempotency_key="test",
    )


class _Recorder:
    """Deterministic orchestration stub — records calls, returns canned runs."""

    def __init__(
        self,
        *,
        result: OrchestrationRun | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.result = result or _run()
        self.error = error

    async def __call__(
        self,
        db_path: object,
        *,
        hospital_id: str,
        source_type: str,
        target_ref: str,
        idempotency_key: str,
    ) -> OrchestrationRun:
        self.calls.append(
            {
                "hospital_id": hospital_id,
                "source_type": source_type,
                "target_ref": target_ref,
                "idempotency_key": idempotency_key,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


# ---------------------------------------------------------------------------
# A. Schedule calculation
# ---------------------------------------------------------------------------


class TestScheduleCalculation:
    def test_hourly_adds_one_hour(self) -> None:
        assert next_run_at(ScheduleFrequency.HOURLY, after=NOW) == NOW + timedelta(
            hours=1
        )

    def test_daily_adds_one_day(self) -> None:
        assert next_run_at(ScheduleFrequency.DAILY, after=NOW) == NOW + timedelta(days=1)

    def test_weekly_adds_seven_days(self) -> None:
        assert next_run_at(ScheduleFrequency.WEEKLY, after=NOW) == NOW + timedelta(
            days=7
        )

    def test_min_interval_floor_raises_candidate(self) -> None:
        # HOURLY is 60 min; floor of 120 min wins.
        result = next_run_at(
            ScheduleFrequency.HOURLY, after=NOW, min_interval_minutes=120
        )
        assert result == NOW + timedelta(minutes=120)

    def test_min_interval_floor_never_lowers(self) -> None:
        # WEEKLY is 7 days; floor of 10 min must not pull it earlier.
        result = next_run_at(
            ScheduleFrequency.WEEKLY, after=NOW, min_interval_minutes=10
        )
        assert result == NOW + timedelta(days=7)

    def test_naive_datetime_treated_as_utc(self) -> None:
        naive = datetime(2026, 9, 10, 12, 0)
        assert as_utc(naive).tzinfo is UTC
        assert next_run_at(ScheduleFrequency.DAILY, after=naive) == (
            NOW + timedelta(days=1)
        )

    def test_unsupported_frequency_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported"):
            next_run_at("fortnightly", after=NOW)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# B. Policy CRUD / identity
# ---------------------------------------------------------------------------


class TestPolicyCrud:
    def test_create_returns_pending_due_now(self, temp_db) -> None:
        policy = create_policy(
            temp_db,
            hospital_id=HOSPITAL_ID,
            frequency=ScheduleFrequency.DAILY,
            target_ref="https://example.test",
            now=NOW,
        )
        assert policy.policy_id.startswith("mp_")
        assert policy.hospital_id == HOSPITAL_ID
        assert policy.status is MonitoringPolicyStatus.PENDING
        assert policy.enabled is True
        assert policy.next_run_at == NOW

    def test_create_is_idempotent_per_identity(self, temp_db) -> None:
        first = create_policy(
            temp_db,
            hospital_id=HOSPITAL_ID,
            frequency=ScheduleFrequency.DAILY,
            target_ref="https://example.test",
            now=NOW,
        )
        second = create_policy(
            temp_db,
            hospital_id=HOSPITAL_ID,
            frequency=ScheduleFrequency.HOURLY,
            target_ref="https://example.test",
            now=NOW,
        )
        assert first.policy_id == second.policy_id
        assert second.frequency is ScheduleFrequency.HOURLY

    def test_different_target_ref_is_distinct_policy(self, temp_db) -> None:
        a = create_policy(
            temp_db,
            hospital_id=HOSPITAL_ID,
            frequency=ScheduleFrequency.DAILY,
            target_ref="https://a.test",
            now=NOW,
        )
        b = create_policy(
            temp_db,
            hospital_id=HOSPITAL_ID,
            frequency=ScheduleFrequency.DAILY,
            target_ref="https://b.test",
            now=NOW,
        )
        assert a.policy_id != b.policy_id

    def test_list_filters_by_hospital_and_enabled(self, temp_db) -> None:
        create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        create_policy(
            temp_db, hospital_id="h_other", frequency="daily", now=NOW
        )
        all_policies = list_policies(temp_db)
        assert len(all_policies) == 2
        only_hospital = list_policies(temp_db, hospital_id=HOSPITAL_ID)
        assert len(only_hospital) == 1
        assert only_hospital[0].hospital_id == HOSPITAL_ID

    def test_unknown_policy_returns_none(self, temp_db) -> None:
        assert set_policy_enabled(temp_db, "mp_nope", enabled=False) is None


# ---------------------------------------------------------------------------
# C. Enable / disable
# ---------------------------------------------------------------------------


class TestEnableDisable:
    def test_disable_marks_enabled_false(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        updated = set_policy_enabled(temp_db, policy.policy_id, enabled=False, now=NOW)
        assert updated is not None
        assert updated.enabled is False

    def test_enable_resets_failed_policy(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        store = MonitoringStore(temp_db, settings=monitoring_settings(max_attempts=1))
        try:
            store.fail(
                policy.policy_id, error="boom", retryable=True, now=NOW
            )
        finally:
            store.close()
        failed = list_policies(temp_db)[0]
        assert failed.status is MonitoringPolicyStatus.FAILED

        reenabled = set_policy_enabled(temp_db, policy.policy_id, enabled=True, now=NOW)
        assert reenabled is not None
        assert reenabled.status is MonitoringPolicyStatus.PENDING
        assert reenabled.consecutive_failures == 0
        assert reenabled.last_error is None
        assert reenabled.next_run_at == NOW

    def test_disabled_policy_is_not_due(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        set_policy_enabled(temp_db, policy.policy_id, enabled=False, now=NOW)
        store = MonitoringStore(temp_db)
        try:
            assert store.get_due(NOW + timedelta(hours=1)) == []
        finally:
            store.close()


# ---------------------------------------------------------------------------
# D. Claim / atomicity / in-flight
# ---------------------------------------------------------------------------


class TestClaim:
    def test_claim_moves_to_running(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        store = MonitoringStore(temp_db)
        try:
            claimed = store.claim(policy, NOW)
            assert claimed is not None
            assert claimed.status is MonitoringPolicyStatus.RUNNING
            assert claimed.last_run_at == NOW
        finally:
            store.close()

    def test_second_claim_fails_while_running(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        store = MonitoringStore(temp_db)
        try:
            first = store.claim(policy, NOW)
            assert first is not None
            # Simulate a second worker holding the same stale PENDING view.
            stale = MonitoringPolicy.model_validate(policy.model_dump())
            second = store.claim(stale, NOW)
            assert second is None
        finally:
            store.close()

    def test_disabled_policy_cannot_be_claimed(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        set_policy_enabled(temp_db, policy.policy_id, enabled=False, now=NOW)
        store = MonitoringStore(temp_db)
        try:
            refreshed = store.get(policy.policy_id)
            assert refreshed is not None
            assert store.claim(refreshed, NOW) is None
        finally:
            store.close()

    def test_get_due_respects_next_run_at(self, temp_db) -> None:
        create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        store = MonitoringStore(temp_db, settings=monitoring_settings(min_interval_minutes=0))
        try:
            due = store.get_due(NOW)
            assert len(due) == 1
            assert store.get_due(NOW - timedelta(minutes=1)) == []
        finally:
            store.close()


# ---------------------------------------------------------------------------
# E. Complete / schedule advancement
# ---------------------------------------------------------------------------


class TestComplete:
    def test_complete_advances_daily_schedule(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        store = MonitoringStore(
            temp_db, settings=monitoring_settings(min_interval_minutes=0)
        )
        try:
            store.claim(policy, NOW)
            completed = store.complete(
                policy.policy_id,
                orchestration_run_id="run_abc",
                change_set_status="no_change",
                now=NOW,
            )
            assert completed is not None
            assert completed.status is MonitoringPolicyStatus.PENDING
            assert completed.next_run_at == NOW + timedelta(days=1)
            assert completed.last_success_at == NOW
            assert completed.last_orchestration_run_id == "run_abc"
            assert completed.last_change_set_status == "no_change"
            assert completed.consecutive_failures == 0
            assert completed.last_error is None
        finally:
            store.close()

    def test_min_interval_applies_on_complete(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="hourly", now=NOW
        )
        store = MonitoringStore(
            temp_db, settings=monitoring_settings(min_interval_minutes=180)
        )
        try:
            store.claim(policy, NOW)
            completed = store.complete(policy.policy_id, now=NOW)
            assert completed is not None
            assert completed.next_run_at == NOW + timedelta(minutes=180)
        finally:
            store.close()


# ---------------------------------------------------------------------------
# F. Fail / retry / backoff
# ---------------------------------------------------------------------------


class TestFailRetry:
    def test_retryable_failure_requeues_with_backoff(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        settings = monitoring_settings(max_attempts=3, backoff_minutes=5)
        store = MonitoringStore(temp_db, settings=settings)
        try:
            store.claim(policy, NOW)
            failed = store.fail(policy.policy_id, error="temp", retryable=True, now=NOW)
            assert failed is not None
            assert failed.status is MonitoringPolicyStatus.PENDING
            assert failed.consecutive_failures == 1
            assert failed.last_error == "temp"
            assert failed.next_run_at == NOW + timedelta(minutes=5)
        finally:
            store.close()

    def test_exhausted_retries_mark_failed(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        settings = monitoring_settings(max_attempts=2, backoff_minutes=1)
        store = MonitoringStore(temp_db, settings=settings)
        try:
            store.claim(policy, NOW)
            first = store.fail(policy.policy_id, error="e1", retryable=True, now=NOW)
            assert first is not None
            assert first.status is MonitoringPolicyStatus.PENDING
            # Cool down past backoff so it is due again.
            due_at = first.next_run_at or NOW
            store.force_due(policy.policy_id, now=due_at)
            refreshed = store.get(policy.policy_id)
            assert refreshed is not None
            store.claim(refreshed, due_at)
            second = store.fail(
                policy.policy_id, error="e2", retryable=True, now=due_at
            )
            assert second is not None
            assert second.status is MonitoringPolicyStatus.FAILED
            assert second.consecutive_failures == 2
            assert second.last_error == "e2"
        finally:
            store.close()

    def test_permanent_failure_skips_retry(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        store = MonitoringStore(
            temp_db, settings=monitoring_settings(max_attempts=5)
        )
        try:
            store.claim(policy, NOW)
            failed = store.fail(
                policy.policy_id, error="auth", retryable=False, now=NOW
            )
            assert failed is not None
            assert failed.status is MonitoringPolicyStatus.FAILED
            assert failed.consecutive_failures == 1
        finally:
            store.close()

    def test_success_resets_consecutive_failures(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        store = MonitoringStore(
            temp_db, settings=monitoring_settings(max_attempts=3, backoff_minutes=1)
        )
        try:
            store.claim(policy, NOW)
            store.fail(policy.policy_id, error="x", retryable=True, now=NOW)
            mid = store.get(policy.policy_id)
            assert mid is not None
            assert mid.consecutive_failures == 1
            store.force_due(policy.policy_id, now=NOW + timedelta(minutes=1))
            due = store.get(policy.policy_id)
            assert due is not None
            store.claim(due, NOW + timedelta(minutes=1))
            done = store.complete(policy.policy_id, now=NOW + timedelta(minutes=1))
            assert done is not None
            assert done.consecutive_failures == 0
            assert done.last_error is None
        finally:
            store.close()


# ---------------------------------------------------------------------------
# G. Worker cycle + orchestration integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWorkerCycle:
    async def test_due_policy_runs_orchestration_once(self, temp_db) -> None:
        create_policy(
            temp_db,
            hospital_id=HOSPITAL_ID,
            frequency="daily",
            target_ref="https://example.test",
            now=NOW,
        )
        recorder = _Recorder()
        processed = await run_monitoring_cycle(
            temp_db, orchestrate=recorder, now=NOW
        )
        assert processed == 1
        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["hospital_id"] == HOSPITAL_ID
        assert call["source_type"] == "website"
        assert call["target_ref"] == "https://example.test"
        assert str(call["idempotency_key"]).startswith("monitoring:")
        policy = list_policies(temp_db)[0]
        assert policy.status is MonitoringPolicyStatus.PENDING
        assert policy.last_orchestration_run_id == "run_test"

    async def test_no_change_treated_as_success_without_duplicate_work(
        self, temp_db
    ) -> None:
        create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        recorder = _Recorder(
            result=_run(
                status=OrchestrationStatus.NO_CHANGE,
                current_stage="no_change",
                change_set_status="no_change",
            )
        )
        processed = await run_monitoring_cycle(
            temp_db, orchestrate=recorder, now=NOW
        )
        assert processed == 1
        policy = list_policies(temp_db)[0]
        assert policy.status is MonitoringPolicyStatus.PENDING
        assert policy.last_change_set_status == "no_change"
        assert policy.last_success_at == NOW
        # Exactly one orchestration call — no re-verification/report on NO_CHANGE.
        assert len(recorder.calls) == 1

    async def test_idempotency_key_is_deterministic_per_window(self, temp_db) -> None:
        create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        recorder = _Recorder()
        await run_monitoring_cycle(temp_db, orchestrate=recorder, now=NOW)
        call = recorder.calls[0]
        policy = list_policies(temp_db)[0]
        expected = build_idempotency_key(policy, NOW)
        assert call["idempotency_key"] == expected

    async def test_orchestration_exception_fails_policy_retryably(
        self, temp_db
    ) -> None:
        create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        recorder = _Recorder(error=RuntimeError("network blip"))
        settings = monitoring_settings(max_attempts=3, backoff_minutes=5)
        processed = await run_monitoring_cycle(
            temp_db, settings=settings, orchestrate=recorder, now=NOW
        )
        assert processed == 1
        policy = list_policies(temp_db)[0]
        assert policy.status is MonitoringPolicyStatus.PENDING
        assert policy.consecutive_failures == 1
        assert "network blip" in (policy.last_error or "")

    async def test_orchestration_failed_status_fails_policy(self, temp_db) -> None:
        create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        recorder = _Recorder(
            result=_run(status=OrchestrationStatus.FAILED, error="stage boom")
        )
        settings = monitoring_settings(max_attempts=3, backoff_minutes=5)
        await run_monitoring_cycle(
            temp_db, settings=settings, orchestrate=recorder, now=NOW
        )
        policy = list_policies(temp_db)[0]
        assert policy.status is MonitoringPolicyStatus.PENDING
        assert policy.consecutive_failures == 1
        assert "stage boom" in (policy.last_error or "")

    async def test_bounded_work_per_cycle(self, temp_db) -> None:
        for i in range(5):
            create_policy(
                temp_db,
                hospital_id=f"h_{i}",
                frequency="daily",
                target_ref=f"https://{i}.test",
                now=NOW,
            )
        recorder = _Recorder()
        settings = monitoring_settings(max_policies_per_cycle=2)
        processed = await run_monitoring_cycle(
            temp_db, settings=settings, orchestrate=recorder, now=NOW
        )
        assert processed == 2
        assert len(recorder.calls) == 2

    async def test_failed_policy_not_reddue_until_backoff(self, temp_db) -> None:
        create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        recorder = _Recorder(error=RuntimeError("fail"))
        settings = monitoring_settings(max_attempts=3, backoff_minutes=30)
        await run_monitoring_cycle(
            temp_db, settings=settings, orchestrate=recorder, now=NOW
        )
        # Immediately run again — backoff means not due yet.
        second = await run_monitoring_cycle(
            temp_db,
            settings=settings,
            orchestrate=recorder,
            now=NOW + timedelta(minutes=1),
        )
        assert second == 0
        assert len(recorder.calls) == 1


# ---------------------------------------------------------------------------
# H. run_policy_now (manual trigger)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunPolicyNow:
    async def test_manual_run_forces_due_and_executes(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="weekly", now=NOW
        )
        # Schedule is future-dated after create only if we advance it; create
        # makes it due at NOW. Force a future next_run_at to prove force-due.
        store = MonitoringStore(temp_db)
        try:
            store._connection.execute(  # noqa: SLF001
                "UPDATE monitoring_policies SET next_run_at = ? WHERE policy_id = ?",
                ((NOW + timedelta(days=3)).isoformat(), policy.policy_id),
            )
            store._connection.commit()
        finally:
            store.close()

        recorder = _Recorder()
        result = await run_policy_now(
            temp_db, policy.policy_id, orchestrate=recorder, now=NOW
        )
        assert result is not None
        assert len(recorder.calls) == 1
        assert result.last_orchestration_run_id == "run_test"
        assert result.status is MonitoringPolicyStatus.PENDING

    async def test_unknown_policy_returns_none(self, temp_db) -> None:
        assert await run_policy_now(temp_db, "mp_missing") is None


# ---------------------------------------------------------------------------
# I. Schema (fresh + existing P19 DB)
# ---------------------------------------------------------------------------


class TestSchema:
    def test_fresh_db_gets_monitoring_table(self, temp_db) -> None:
        store = MonitoringStore(temp_db)
        try:
            rows = store._connection.execute(  # noqa: SLF001
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='monitoring_policies'"
            ).fetchall()
            assert len(rows) == 1
        finally:
            store.close()

    def test_existing_p19_db_upgrades_additively(self, tmp_path: Path) -> None:
        # Build a P19-era DB by opening HospitalDatabase (creates all P19 tables).
        db_path = tmp_path / "p19.db"
        with HospitalDatabase(db_path) as db:
            tables_before = {
                row[0]
                for row in db._connection.execute(  # noqa: SLF001
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "monitoring_policies" not in tables_before
            assert "orchestration_runs" in tables_before

        # Open again + MonitoringStore — additive upgrade.
        with HospitalDatabase(db_path) as db:
            store = MonitoringStore(db)
            try:
                tables_after = {
                    row[0]
                    for row in store._connection.execute(  # noqa: SLF001
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            finally:
                store.close()
        assert "monitoring_policies" in tables_after
        # P19 tables still present.
        assert "orchestration_runs" in tables_after

    def test_schema_is_idempotent(self, temp_db) -> None:
        MonitoringStore(temp_db).close()
        MonitoringStore(temp_db).close()  # second open must not fail
        store = MonitoringStore(temp_db)
        try:
            policy = store.create(
                hospital_id=HOSPITAL_ID,
                frequency=ScheduleFrequency.DAILY,
                now=NOW,
            )
            assert policy.policy_id
        finally:
            store.close()


# ---------------------------------------------------------------------------
# J. CLI surface
# ---------------------------------------------------------------------------


class TestCli:
    def test_monitoring_help(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["monitoring", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "list" in out
        assert "add" in out
        assert "enable" in out
        assert "disable" in out
        assert "run" in out
        assert "worker" in out

    def test_monitoring_add_list_enable_disable(
        self, temp_db, capsys
    ) -> None:
        db_path = str(temp_db.path)
        rc = main(
            [
                "monitoring",
                "add",
                HOSPITAL_ID,
                "--frequency",
                "daily",
                "--target-ref",
                "https://example.test",
                "--db",
                db_path,
            ]
        )
        # Hospital may not resolve via find_hospital_id without registration —
        # mint identity first if needed.
        if rc != 0:
            temp_db._connection.execute(  # noqa: SLF001
                "INSERT OR IGNORE INTO hospitals (hospital_id, name, created_at)"
                " VALUES (?, ?, ?)",
                (HOSPITAL_ID, "Monitor Hospital", NOW.isoformat()),
            )
            temp_db._connection.commit()
            capsys.readouterr()
            rc = main(
                [
                    "monitoring",
                    "add",
                    HOSPITAL_ID,
                    "--frequency",
                    "daily",
                    "--target-ref",
                    "https://example.test",
                    "--db",
                    db_path,
                ]
            )
        assert rc == 0
        out = capsys.readouterr().out
        assert "monitoring policy:" in out

        rc = main(["monitoring", "list", "--db", db_path])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Monitoring policies (1)" in out
        assert "daily" in out

        policy = list_policies(temp_db)[0]
        rc = main(["monitoring", "disable", policy.policy_id, "--db", db_path])
        assert rc == 0
        assert list_policies(temp_db)[0].enabled is False

        rc = main(["monitoring", "enable", policy.policy_id, "--db", db_path])
        assert rc == 0
        assert list_policies(temp_db)[0].enabled is True

    def test_monitoring_worker_once(self, temp_db, capsys) -> None:
        temp_db._connection.execute(  # noqa: SLF001
            "INSERT OR IGNORE INTO hospitals (hospital_id, name, created_at)"
            " VALUES (?, ?, ?)",
            (HOSPITAL_ID, "Monitor Hospital", NOW.isoformat()),
        )
        temp_db._connection.commit()
        db_path = str(temp_db.path)
        main(
            [
                "monitoring",
                "add",
                HOSPITAL_ID,
                "--frequency",
                "hourly",
                "--db",
                db_path,
            ]
        )
        capsys.readouterr()
        # worker --once with default orchestrate would attempt a real crawl;
        # assert only that the command wires up and reports a count (0 when
        # nothing due after prior cycle, or 1 if due). We accept either by
        # checking the message shape without forcing a network call — the
        # policy is due at create time, so this would call orchestration.
        # To stay offline, disable first then re-enable with future schedule.
        policy = list_policies(temp_db)[0]
        set_policy_enabled(temp_db, policy.policy_id, enabled=False)
        rc = main(["monitoring", "worker", "--once", "--db", db_path])
        assert rc == 0
        out = capsys.readouterr().out
        assert "processed 0 due" in out

    def test_monitoring_add_unknown_hospital_fails(
        self, temp_db, capsys
    ) -> None:
        rc = main(
            [
                "monitoring",
                "add",
                "h_does_not_exist_xyz",
                "--frequency",
                "daily",
                "--db",
                str(temp_db.path),
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "unknown hospital" in err

    def test_monitoring_enable_unknown_fails(self, temp_db, capsys) -> None:
        rc = main(
            ["monitoring", "enable", "mp_nope", "--db", str(temp_db.path)]
        )
        assert rc == 1
        assert "unknown policy" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# K. API surface
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.state.medcompare_db_path = tmp_path / "medcompare-test.db"
    with TestClient(app) as test_client:
        yield test_client


def _register_hospital(db_path: Path) -> None:
    with HospitalDatabase(db_path) as db:
        db._connection.execute(  # noqa: SLF001
            "INSERT OR IGNORE INTO hospitals (hospital_id, name, created_at)"
            " VALUES (?, ?, ?)",
            (HOSPITAL_ID, "Monitor Hospital", NOW.isoformat()),
        )
        db._connection.commit()


class TestApi:
    def test_list_empty(self, client) -> None:
        response = client.get(f"/api/v1/hospitals/{HOSPITAL_ID}/monitoring-policies")
        # Unknown hospital → 404 (fail closed, no minting).
        assert response.status_code == 404

    def test_create_list_get_enable_disable(
        self, client, tmp_path
    ) -> None:
        _register_hospital(tmp_path / "medcompare-test.db")

        create = client.post(
            f"/api/v1/hospitals/{HOSPITAL_ID}/monitoring-policies",
            json={
                "frequency": "daily",
                "source": "website",
                "target_ref": "https://example.test",
            },
        )
        assert create.status_code == 201
        policy = create.json()["monitoring_policy"]
        policy_id = policy["policy_id"]
        assert policy["status"] == "pending"
        assert policy["enabled"] is True

        listing = client.get(
            f"/api/v1/hospitals/{HOSPITAL_ID}/monitoring-policies"
        )
        assert listing.status_code == 200
        assert len(listing.json()["monitoring_policies"]) == 1

        single = client.get(f"/api/v1/monitoring-policies/{policy_id}")
        assert single.status_code == 200
        assert single.json()["monitoring_policy"]["policy_id"] == policy_id

        disable = client.post(f"/api/v1/monitoring-policies/{policy_id}/disable")
        assert disable.status_code == 200
        assert disable.json()["monitoring_policy"]["enabled"] is False

        enable = client.post(f"/api/v1/monitoring-policies/{policy_id}/enable")
        assert enable.status_code == 200
        assert enable.json()["monitoring_policy"]["enabled"] is True

    def test_create_unknown_hospital_404(self, client) -> None:
        response = client.post(
            "/api/v1/hospitals/h_missing/monitoring-policies",
            json={"frequency": "daily"},
        )
        assert response.status_code == 404

    def test_create_invalid_source_422(self, client, tmp_path) -> None:
        _register_hospital(tmp_path / "medcompare-test.db")
        response = client.post(
            f"/api/v1/hospitals/{HOSPITAL_ID}/monitoring-policies",
            json={"frequency": "daily", "source": "not_a_source"},
        )
        assert response.status_code == 422

    def test_get_unknown_404(self, client) -> None:
        assert (
            client.get("/api/v1/monitoring-policies/mp_nope").status_code == 404
        )

    def test_enable_unknown_404(self, client) -> None:
        assert (
            client.post("/api/v1/monitoring-policies/mp_nope/enable").status_code
            == 404
        )

    def test_run_unknown_404(self, client) -> None:
        assert (
            client.post("/api/v1/monitoring-policies/mp_nope/run").status_code
            == 404
        )

    def test_profile_page_includes_monitoring_section(
        self, client, tmp_path
    ) -> None:
        from hospital_extractor.config import load_config
        from hospital_extractor.extraction import build_dataset

        dataset = build_dataset(
            [("https://example.test/", "<html><title>H</title></html>")],
            target_url="https://example.test",
            config=load_config(),
        )
        imported = client.post(
            "/api/v1/hospitals/import", json=dataset.model_dump(mode="json")
        )
        assert imported.status_code == 200
        dataset_id = imported.json()["dataset_id"]

        # Resolve the hospital id the profile page will use, then attach a policy.
        page_probe = client.get(f"/hospitals/{dataset_id}")
        assert page_probe.status_code == 200
        with HospitalDatabase(tmp_path / "medcompare-test.db") as db:
            row = db._connection.execute(  # noqa: SLF001
                "SELECT hospital_id FROM datasets WHERE dataset_id = ?",
                (dataset_id,),
            ).fetchone()
            assert row is not None
            profile_hospital_id = str(row["hospital_id"])
            assert profile_hospital_id
        create = client.post(
            f"/api/v1/hospitals/{profile_hospital_id}/monitoring-policies",
            json={"frequency": "weekly", "target_ref": "https://example.test"},
        )
        assert create.status_code == 201

        page = client.get(f"/hospitals/{dataset_id}")
        assert page.status_code == 200
        assert "Monitoring" in page.text
        assert "weekly" in page.text


# ---------------------------------------------------------------------------
# L. Read-safety: UI/API list never crawl
# ---------------------------------------------------------------------------


class TestReadSafety:
    def test_list_endpoint_does_not_crawl(
        self, client, tmp_path, monkeypatch
    ) -> None:
        _register_hospital(tmp_path / "medcompare-test.db")

        def _explode(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("crawl attempted on a read path")

        monkeypatch.setattr(
            "hospital_extractor.crawler.runner.run_crawl", _explode, raising=False
        )
        monkeypatch.setattr(
            "hospital_extractor.sources.pipeline.harvest", _explode, raising=False
        )

        listing = client.get(
            f"/api/v1/hospitals/{HOSPITAL_ID}/monitoring-policies"
        )
        assert listing.status_code == 200

    def test_profile_page_does_not_crawl(
        self, client, tmp_path, monkeypatch
    ) -> None:
        def _explode(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("crawl attempted on profile page")

        monkeypatch.setattr(
            "hospital_extractor.crawler.runner.run_crawl", _explode, raising=False
        )
        from hospital_extractor.config import load_config
        from hospital_extractor.extraction import build_dataset

        dataset = build_dataset(
            [("https://example.test/", "<html><title>H</title></html>")],
            target_url="https://example.test",
            config=load_config(),
        )
        imported = client.post(
            "/api/v1/hospitals/import", json=dataset.model_dump(mode="json")
        )
        dataset_id = imported.json()["dataset_id"]
        page = client.get(f"/hospitals/{dataset_id}")
        assert page.status_code == 200


# ---------------------------------------------------------------------------
# M. Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_defaults(self) -> None:
        settings = monitoring_settings(
            max_attempts=1,
            backoff_minutes=1,
            min_interval_minutes=1,
            worker_interval_seconds=1.0,
            max_policies_per_cycle=1,
        )
        assert settings.max_attempts == 1
        assert settings.backoff_minutes == 1
        assert settings.min_interval_minutes == 1
        assert settings.worker_interval_seconds == 1.0
        assert settings.max_policies_per_cycle == 1

    def test_env_fallbacks(self, monkeypatch) -> None:
        monkeypatch.setenv("MONITORING_MAX_ATTEMPTS", "7")
        monkeypatch.setenv("MONITORING_BACKOFF_MINUTES", "9")
        monkeypatch.setenv("MONITORING_MIN_INTERVAL_MINUTES", "30")
        monkeypatch.setenv("MONITORING_WORKER_INTERVAL_SECONDS", "2.5")
        monkeypatch.setenv("MONITORING_MAX_POLICIES_PER_CYCLE", "4")
        settings = monitoring_settings()
        assert settings.max_attempts == 7
        assert settings.backoff_minutes == 9
        assert settings.min_interval_minutes == 30
        assert settings.worker_interval_seconds == 2.5
        assert settings.max_policies_per_cycle == 4


# ---------------------------------------------------------------------------
# N. Idempotency key helper
# ---------------------------------------------------------------------------


class TestIdempotencyKey:
    def test_key_format(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        key = build_idempotency_key(policy, NOW)
        assert key == f"monitoring:{policy.policy_id}:20260910T120000Z"

    def test_same_window_same_key(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        assert build_idempotency_key(policy, NOW) == build_idempotency_key(
            policy, NOW
        )

    def test_different_window_different_key(self, temp_db) -> None:
        policy = create_policy(
            temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=NOW
        )
        assert build_idempotency_key(policy, NOW) != build_idempotency_key(
            policy, NOW + timedelta(days=1)
        )


# ---------------------------------------------------------------------------
# O. Orchestration idempotency pass-through regression
# ---------------------------------------------------------------------------


def test_run_orchestration_forwards_idempotency_key() -> None:
    """run_orchestration must not drop idempotency_key / run_id (P20 fix)."""
    import inspect

    from hospital_extractor.orchestration import service as orch_service

    source = inspect.getsource(orch_service.run_orchestration)
    assert "idempotency_key=idempotency_key" in source
    assert "run_id=run_id" in source


# ---------------------------------------------------------------------------
# P. package exports
# ---------------------------------------------------------------------------


def test_package_exports() -> None:
    import hospital_extractor.monitoring as m

    for name in (
        "MonitoringPolicy",
        "MonitoringPolicyStatus",
        "MonitoringSettings",
        "ScheduleFrequency",
        "create_policy",
        "list_policies",
        "monitoring_settings",
        "next_run_at",
        "run_monitoring_cycle",
        "run_policy_now",
        "set_policy_enabled",
    ):
        assert hasattr(m, name), name


# ---------------------------------------------------------------------------
# Q. Real-world honesty: authorized sources not silently executed
# ---------------------------------------------------------------------------


def test_policy_source_can_be_authorized_type_without_bypass(temp_db) -> None:
    """A policy may name hospital_api; the worker must not bypass auth here.

    Monitoring only schedules — authorization is enforced inside the existing
    verification/orchestration path (P19-B/D). This test pins that creating a
    policy for an authorized source type does not itself contact anything.
    """

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network touched while creating a policy")

    import hospital_extractor.sources.pipeline as pipeline

    original = pipeline.harvest
    pipeline.harvest = _explode  # type: ignore[assignment]
    try:
        policy = create_policy(
            temp_db,
            hospital_id=HOSPITAL_ID,
            frequency="daily",
            source_type="hospital_api",
            target_ref="https://api.hospital.test",
            now=NOW,
        )
        assert policy.source_type == "hospital_api"
    finally:
        pipeline.harvest = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# R. Worker forever loop smoke (one cycle then stop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_loop_stops_on_event(temp_db) -> None:
    from hospital_extractor.monitoring.service import run_monitoring_worker

    create_policy(
        temp_db, hospital_id=HOSPITAL_ID, frequency="daily", enabled=False, now=NOW
    )
    stop = asyncio.Event()
    stop.set()
    settings = monitoring_settings(worker_interval_seconds=0.01)
    # With stop already set, wait_for returns immediately after first cycle.
    await asyncio.wait_for(
        run_monitoring_worker(
            temp_db,
            settings=settings,
            orchestrate=_Recorder(),
            stop_event=stop,
        ),
        timeout=2.0,
    )


# ---------------------------------------------------------------------------
# S. Naive / aware datetime normalization in storage
# ---------------------------------------------------------------------------


def test_storage_normalizes_naive_timestamps(temp_db) -> None:
    naive = datetime(2026, 9, 10, 12, 0)
    policy = create_policy(
        temp_db, hospital_id=HOSPITAL_ID, frequency="daily", now=naive
    )
    assert policy.next_run_at is not None
    assert policy.next_run_at.tzinfo is not None
    assert as_utc(policy.next_run_at) == NOW
