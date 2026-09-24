"""SQLite-backed monitoring policy store (P20).

Deliberately separate from ``storage/database.py``: that module is frozen for
P19-E integrity, so monitoring owns its own additive schema
(``CREATE TABLE IF NOT EXISTS`` — fresh databases and existing P19 databases
both work without a hand-written migration step).

Conceptual operations (all local, no distributed queues):

- :meth:`MonitoringStore.ensure_schema` — idempotent DDL for this store.
- :meth:`create` — upsert one policy per (hospital, source, target).
- :meth:`get_due` — enabled PENDING policies whose ``next_run_at`` has arrived.
- :meth:`claim` — atomic PENDING → RUNNING (safe across local workers).
- :meth:`complete` — RUNNING → PENDING with the next schedule computed.
- :meth:`fail` — RUNNING → PENDING (bounded retry w/ backoff) or FAILED.
"""

from __future__ import annotations

import builtins
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from hospital_extractor.monitoring.models import (
    MonitoringPolicy,
    MonitoringPolicyStatus,
    ScheduleFrequency,
)
from hospital_extractor.monitoring.schedule import as_utc, next_run_at
from hospital_extractor.monitoring.settings import (
    MonitoringSettings,
    monitoring_settings,
)
from hospital_extractor.storage.database import HospitalDatabase

__all__ = ["MonitoringStore"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS monitoring_policies (
    policy_id                  TEXT PRIMARY KEY,
    hospital_id                TEXT NOT NULL,
    source_type                TEXT NOT NULL DEFAULT 'website',
    target_ref                 TEXT NOT NULL DEFAULT '',
    frequency                  TEXT NOT NULL,
    enabled                    INTEGER NOT NULL DEFAULT 1,
    status                     TEXT NOT NULL DEFAULT 'pending',
    next_run_at                TEXT,
    last_run_at                TEXT,
    last_success_at            TEXT,
    last_failure_at            TEXT,
    consecutive_failures       INTEGER NOT NULL DEFAULT 0,
    last_error                 TEXT,
    last_orchestration_run_id  TEXT,
    last_change_set_status     TEXT,
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL,
    created_by                 TEXT NOT NULL DEFAULT 'system'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_monitoring_policies_identity
    ON monitoring_policies (hospital_id, source_type, target_ref);

CREATE INDEX IF NOT EXISTS idx_monitoring_policies_due
    ON monitoring_policies (enabled, status, next_run_at);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat()


def _parse(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    text = str(value)
    if not text:
        return None
    return as_utc(datetime.fromisoformat(text))


def _policy_from_row(row: sqlite3.Row) -> MonitoringPolicy:
    return MonitoringPolicy(
        policy_id=str(row["policy_id"]),
        hospital_id=str(row["hospital_id"]),
        source_type=str(row["source_type"]),
        target_ref=str(row["target_ref"] or ""),
        frequency=ScheduleFrequency(str(row["frequency"])),
        enabled=bool(row["enabled"]),
        status=MonitoringPolicyStatus(str(row["status"])),
        next_run_at=_parse(row["next_run_at"]),
        last_run_at=_parse(row["last_run_at"]),
        last_success_at=_parse(row["last_success_at"]),
        last_failure_at=_parse(row["last_failure_at"]),
        consecutive_failures=int(row["consecutive_failures"]),
        last_error=(str(row["last_error"]) if row["last_error"] else None),
        last_orchestration_run_id=(
            str(row["last_orchestration_run_id"])
            if row["last_orchestration_run_id"]
            else None
        ),
        last_change_set_status=(
            str(row["last_change_set_status"]) if row["last_change_set_status"] else None
        ),
        created_at=_parse(row["created_at"]) or _now(),
        updated_at=_parse(row["updated_at"]) or _now(),
        created_by=str(row["created_by"] or "system"),
    )


class MonitoringStore:
    """Persistence and lifecycle for :class:`MonitoringPolicy` rows.

    Opens a connection to the same SQLite file as *db* so monitoring DDL and
    claims never touch (or depend on edits to) ``storage/database.py``.
    """

    def __init__(
        self,
        db: HospitalDatabase,
        *,
        settings: MonitoringSettings | None = None,
    ) -> None:
        self._db = db
        self.settings = settings or monitoring_settings()
        self._connection = sqlite3.connect(str(db.path))
        self._connection.row_factory = sqlite3.Row
        self.ensure_schema()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> MonitoringStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def ensure_schema(self) -> None:
        """Create monitoring tables/indexes if absent (additive, idempotent)."""
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    # ------------------------------------------------------------- CRUD

    def create(
        self,
        *,
        hospital_id: str,
        frequency: ScheduleFrequency,
        source_type: str = "website",
        target_ref: str = "",
        enabled: bool = True,
        created_by: str = "system",
        now: datetime | None = None,
    ) -> MonitoringPolicy:
        """Create or update the policy for this identity (idempotent upsert).

        First creation makes the policy due immediately (``next_run_at = now``)
        so the next worker cycle picks it up; subsequent schedule boundaries
        are computed from successful/failed run timestamps.
        """
        timestamp = as_utc(now or _now())
        existing = self.get_by_identity(
            hospital_id=hospital_id,
            source_type=source_type,
            target_ref=target_ref,
        )
        if existing is not None:
            updates: dict[str, object] = {
                "frequency": frequency.value,
                "enabled": int(enabled),
                "updated_at": _iso(timestamp),
            }
            if enabled and existing.status is MonitoringPolicyStatus.FAILED:
                updates["status"] = MonitoringPolicyStatus.PENDING.value
                updates["consecutive_failures"] = 0
                updates["last_error"] = None
                updates["next_run_at"] = _iso(timestamp)
            self._connection.execute(
                """
                UPDATE monitoring_policies
                SET frequency = :frequency,
                    enabled = :enabled,
                    status = COALESCE(:status, status),
                    consecutive_failures = COALESCE(:consecutive_failures, consecutive_failures),
                    last_error = CASE WHEN :status IS NOT NULL THEN :last_error ELSE last_error END,
                    next_run_at = COALESCE(:next_run_at, next_run_at),
                    updated_at = :updated_at
                WHERE policy_id = :policy_id
                """,
                {
                    "frequency": updates["frequency"],
                    "enabled": updates["enabled"],
                    "status": updates.get("status"),
                    "consecutive_failures": updates.get("consecutive_failures"),
                    "last_error": updates.get("last_error"),
                    "next_run_at": updates.get("next_run_at"),
                    "updated_at": updates["updated_at"],
                    "policy_id": existing.policy_id,
                },
            )
            self._connection.commit()
            refreshed = self.get(existing.policy_id)
            assert refreshed is not None
            return refreshed

        policy = MonitoringPolicy(
            policy_id=f"mp_{uuid4().hex[:12]}",
            hospital_id=hospital_id,
            source_type=source_type,
            target_ref=target_ref,
            frequency=frequency,
            enabled=enabled,
            status=MonitoringPolicyStatus.PENDING,
            next_run_at=timestamp if enabled else None,
            created_at=timestamp,
            updated_at=timestamp,
            created_by=created_by,
        )
        self._connection.execute(
            """
            INSERT INTO monitoring_policies (
                policy_id, hospital_id, source_type, target_ref, frequency,
                enabled, status, next_run_at, consecutive_failures,
                created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                policy.policy_id,
                policy.hospital_id,
                policy.source_type,
                policy.target_ref,
                policy.frequency.value,
                int(policy.enabled),
                policy.status.value,
                _iso(policy.next_run_at),
                _iso(policy.created_at),
                _iso(policy.updated_at),
                policy.created_by,
            ),
        )
        self._connection.commit()
        return policy

    def get(self, policy_id: str) -> MonitoringPolicy | None:
        row = self._connection.execute(
            "SELECT * FROM monitoring_policies WHERE policy_id = ?", (policy_id,)
        ).fetchone()
        return _policy_from_row(row) if row is not None else None

    def get_by_identity(
        self,
        *,
        hospital_id: str,
        source_type: str,
        target_ref: str,
    ) -> MonitoringPolicy | None:
        row = self._connection.execute(
            """
            SELECT * FROM monitoring_policies
            WHERE hospital_id = ? AND source_type = ? AND target_ref = ?
            """,
            (hospital_id, source_type, target_ref),
        ).fetchone()
        return _policy_from_row(row) if row is not None else None

    def list(
        self,
        *,
        hospital_id: str | None = None,
        enabled: bool | None = None,
    ) -> builtins.list[MonitoringPolicy]:
        clauses: list[str] = []
        params: list[object] = []
        if hospital_id is not None:
            clauses.append("hospital_id = ?")
            params.append(hospital_id)
        if enabled is not None:
            clauses.append("enabled = ?")
            params.append(int(enabled))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT * FROM monitoring_policies{where}"
            " ORDER BY created_at ASC",
            params,
        ).fetchall()
        return [_policy_from_row(row) for row in rows]

    def set_enabled(
        self,
        policy_id: str,
        *,
        enabled: bool,
        now: datetime | None = None,
    ) -> MonitoringPolicy | None:
        """Enable or disable a policy; enabling a FAILED policy resets it."""
        timestamp = as_utc(now or _now())
        policy = self.get(policy_id)
        if policy is None:
            return None
        if enabled and policy.status is MonitoringPolicyStatus.FAILED:
            self._connection.execute(
                """
                UPDATE monitoring_policies
                SET enabled = 1, status = ?, consecutive_failures = 0,
                    last_error = NULL, next_run_at = ?, updated_at = ?
                WHERE policy_id = ?
                """,
                (
                    MonitoringPolicyStatus.PENDING.value,
                    _iso(timestamp),
                    _iso(timestamp),
                    policy_id,
                ),
            )
        elif (
            enabled
            and policy.status is MonitoringPolicyStatus.PENDING
            and policy.next_run_at is None
        ):
            self._connection.execute(
                """
                UPDATE monitoring_policies
                SET enabled = 1, next_run_at = ?, updated_at = ?
                WHERE policy_id = ?
                """,
                (_iso(timestamp), _iso(timestamp), policy_id),
            )
        else:
            self._connection.execute(
                "UPDATE monitoring_policies SET enabled = ?, updated_at = ? WHERE policy_id = ?",
                (int(enabled), _iso(timestamp), policy_id),
            )
        self._connection.commit()
        return self.get(policy_id)

    # ------------------------------------------------------- scheduling

    def get_due(
        self,
        now: datetime | None = None,
        *,
        limit: int | None = None,
    ) -> builtins.list[MonitoringPolicy]:
        """Enabled PENDING policies with ``next_run_at <= now`` (oldest first)."""
        timestamp = as_utc(now or _now())
        sql = (
            "SELECT * FROM monitoring_policies"
            " WHERE enabled = 1 AND status = ? AND next_run_at IS NOT NULL"
            " AND next_run_at <= ?"
            " ORDER BY next_run_at ASC, created_at ASC"
        )
        params: list[object] = [MonitoringPolicyStatus.PENDING.value, _iso(timestamp)]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self._connection.execute(sql, params).fetchall()
        return [_policy_from_row(row) for row in rows]

    def claim(
        self,
        policy: MonitoringPolicy,
        now: datetime | None = None,
    ) -> MonitoringPolicy | None:
        """Atomically move *policy* to RUNNING; ``None`` if another worker claimed it."""
        timestamp = as_utc(now or _now())
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE monitoring_policies
                SET status = ?, last_run_at = ?, updated_at = ?
                WHERE policy_id = ? AND status = ? AND enabled = 1
                """,
                (
                    MonitoringPolicyStatus.RUNNING.value,
                    _iso(timestamp),
                    _iso(timestamp),
                    policy.policy_id,
                    MonitoringPolicyStatus.PENDING.value,
                ),
            )
        if cursor.rowcount != 1:
            return None
        return self.get(policy.policy_id)

    def complete(
        self,
        policy_id: str,
        *,
        orchestration_run_id: str | None = None,
        change_set_status: str | None = None,
        now: datetime | None = None,
    ) -> MonitoringPolicy | None:
        """RUNNING → PENDING with the next schedule computed from *now*."""
        timestamp = as_utc(now or _now())
        policy = self.get(policy_id)
        if policy is None:
            return None
        due = next_run_at(
            policy.frequency,
            after=timestamp,
            min_interval_minutes=self.settings.min_interval_minutes,
        )
        self._connection.execute(
            """
            UPDATE monitoring_policies
            SET status = ?, enabled = 1,
                next_run_at = ?, last_run_at = ?, last_success_at = ?,
                consecutive_failures = 0, last_error = NULL,
                last_orchestration_run_id = ?, last_change_set_status = ?,
                updated_at = ?
            WHERE policy_id = ?
            """,
            (
                MonitoringPolicyStatus.PENDING.value,
                _iso(due),
                _iso(timestamp),
                _iso(timestamp),
                orchestration_run_id,
                change_set_status,
                _iso(timestamp),
                policy_id,
            ),
        )
        self._connection.commit()
        return self.get(policy_id)

    def fail(
        self,
        policy_id: str,
        *,
        error: str,
        retryable: bool,
        orchestration_run_id: str | None = None,
        now: datetime | None = None,
    ) -> MonitoringPolicy | None:
        """Handle a failed attempt.

        Retryable failures with attempts remaining re-queue to PENDING with
        linear backoff (``backoff_minutes * consecutive_failures``); permanent
        errors and exhausted attempts go to FAILED (never retried forever
        within the same window).
        """
        timestamp = as_utc(now or _now())
        policy = self.get(policy_id)
        if policy is None:
            return None

        failures = policy.consecutive_failures + 1
        if not retryable or failures >= self.settings.max_attempts:
            self._connection.execute(
                """
                UPDATE monitoring_policies
                SET status = ?, last_failure_at = ?, last_error = ?,
                    consecutive_failures = ?, last_run_at = ?,
                    last_orchestration_run_id = COALESCE(?, last_orchestration_run_id),
                    updated_at = ?
                WHERE policy_id = ?
                """,
                (
                    MonitoringPolicyStatus.FAILED.value,
                    _iso(timestamp),
                    error,
                    failures,
                    _iso(timestamp),
                    orchestration_run_id,
                    _iso(timestamp),
                    policy_id,
                ),
            )
        else:
            # Linear backoff independent of min_interval (that floor applies to
            # successful schedule advancement, not failure cool-downs).
            due = timestamp + timedelta(
                minutes=self.settings.backoff_minutes * failures
            )
            self._connection.execute(
                """
                UPDATE monitoring_policies
                SET status = ?, next_run_at = ?, last_failure_at = ?,
                    last_error = ?, consecutive_failures = ?, last_run_at = ?,
                    last_orchestration_run_id = COALESCE(?, last_orchestration_run_id),
                    updated_at = ?
                WHERE policy_id = ?
                """,
                (
                    MonitoringPolicyStatus.PENDING.value,
                    _iso(due),
                    _iso(timestamp),
                    error,
                    failures,
                    _iso(timestamp),
                    orchestration_run_id,
                    _iso(timestamp),
                    policy_id,
                ),
            )
        self._connection.commit()
        return self.get(policy_id)

    def force_due(self, policy_id: str, *, now: datetime | None = None) -> None:
        """Mark a PENDING policy due immediately (manual ``run`` path only)."""
        timestamp = as_utc(now or _now())
        self._connection.execute(
            """
            UPDATE monitoring_policies
            SET next_run_at = ?, updated_at = ?
            WHERE policy_id = ? AND status = ?
            """,
            (
                _iso(timestamp),
                _iso(timestamp),
                policy_id,
                MonitoringPolicyStatus.PENDING.value,
            ),
        )
        self._connection.commit()

    def path(self) -> Path:
        """The SQLite file this store is attached to (for diagnostics)."""
        return Path(self._db.path)
