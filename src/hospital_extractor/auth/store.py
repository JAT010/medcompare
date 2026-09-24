"""SQLite auth + audit store (P24).

Follows the MonitoringStore pattern: additive ``CREATE TABLE IF NOT EXISTS``
on the same database file as the hospital store — **no changes** to the
frozen ``storage/database.py``.

Tables:

- ``users`` — credentials (scrypt hash only), role, enabled, hospital grants
- ``auth_tokens`` — SHA-256 digests of bearer tokens (raw token never stored)
- ``audit_events`` — append-only security audit log (no update/delete API)
- ``user_hospital_access`` — resource-level hospital grants
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from hospital_extractor.auth.hashing import hash_password, new_user_id
from hospital_extractor.auth.models import (
    AuditEvent,
    Role,
    TokenRecord,
    User,
)

__all__ = ["AuthStore"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        TEXT PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'viewer',
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_tokens (
    token_id    TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens (user_id);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash ON auth_tokens (token_hash);

CREATE TABLE IF NOT EXISTS audit_events (
    audit_event_id TEXT PRIMARY KEY,
    timestamp      TEXT NOT NULL,
    actor_id       TEXT NOT NULL DEFAULT '',
    actor_role     TEXT NOT NULL DEFAULT '',
    action         TEXT NOT NULL,
    resource_type  TEXT NOT NULL DEFAULT '',
    resource_id    TEXT NOT NULL DEFAULT '',
    hospital_id    TEXT NOT NULL DEFAULT '',
    outcome        TEXT NOT NULL DEFAULT 'success',
    correlation_id TEXT NOT NULL DEFAULT '',
    metadata_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_events_ts ON audit_events (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_action ON audit_events (action);
CREATE INDEX IF NOT EXISTS idx_audit_events_hospital ON audit_events (hospital_id);

CREATE TABLE IF NOT EXISTS user_hospital_access (
    user_id     TEXT NOT NULL,
    hospital_id TEXT NOT NULL,
    granted_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, hospital_id)
);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class AuthStore:
    """Users, tokens, hospital grants, and append-only audit events."""

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._connection = sqlite3.connect(str(self._path))
        self._connection.row_factory = sqlite3.Row
        self.ensure_schema()

    @classmethod
    def for_database(cls, db: Any) -> AuthStore:
        """Open the auth store on the same SQLite file as *db*."""
        path = getattr(db, "path", db)
        return cls(path)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> AuthStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def ensure_schema(self) -> None:
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    # ------------------------------------------------------------------ users

    def create_user(
        self,
        *,
        username: str,
        password: str,
        role: Role = Role.VIEWER,
        enabled: bool = True,
        hospital_ids: list[str] | None = None,
        user_id: str | None = None,
    ) -> User:
        uid = user_id or new_user_id()
        now = _now_iso()
        self._connection.execute(
            """
            INSERT INTO users
                (user_id, username, password_hash, role, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (uid, username, hash_password(password), role.value, int(enabled), now, now),
        )
        self._connection.commit()
        for hospital_id in hospital_ids or []:
            self.grant_hospital(uid, hospital_id)
        user = self.get_user(uid)
        assert user is not None
        return user

    def get_user(self, user_id: str) -> User | None:
        row = self._connection.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return self._user_from_row(row) if row is not None else None

    def get_user_by_username(self, username: str) -> User | None:
        row = self._connection.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        return self._user_from_row(row) if row is not None else None

    def list_users(self) -> list[User]:
        rows = self._connection.execute(
            "SELECT * FROM users ORDER BY username COLLATE NOCASE"
        ).fetchall()
        return [self._user_from_row(row) for row in rows]

    def update_user(
        self,
        user_id: str,
        *,
        role: Role | None = None,
        enabled: bool | None = None,
        password: str | None = None,
        hospital_ids: list[str] | None = None,
    ) -> User | None:
        user = self.get_user(user_id)
        if user is None:
            return None
        fields: list[str] = []
        params: list[Any] = []
        if role is not None:
            fields.append("role = ?")
            params.append(role.value)
        if enabled is not None:
            fields.append("enabled = ?")
            params.append(int(enabled))
        if password is not None:
            fields.append("password_hash = ?")
            params.append(hash_password(password))
        if fields:
            fields.append("updated_at = ?")
            params.append(_now_iso())
            params.append(user_id)
            self._connection.execute(
                f"UPDATE users SET {', '.join(fields)} WHERE user_id = ?",  # noqa: S608
                params,
            )
            self._connection.commit()
        if hospital_ids is not None:
            self._connection.execute(
                "DELETE FROM user_hospital_access WHERE user_id = ?", (user_id,)
            )
            self._connection.commit()
            for hospital_id in hospital_ids:
                self.grant_hospital(user_id, hospital_id)
        return self.get_user(user_id)

    def _user_from_row(self, row: sqlite3.Row) -> User:
        grants = self.list_hospital_grants(str(row["user_id"]))
        return User(
            user_id=str(row["user_id"]),
            username=str(row["username"]),
            password_hash=str(row["password_hash"]),
            role=Role(str(row["role"])),
            enabled=bool(row["enabled"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            hospital_ids=grants,
        )

    # ----------------------------------------------------------------- grants

    def grant_hospital(self, user_id: str, hospital_id: str) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO user_hospital_access (user_id, hospital_id, granted_at)
            VALUES (?, ?, ?)
            """,
            (user_id, hospital_id, _now_iso()),
        )
        self._connection.commit()

    def revoke_hospital(self, user_id: str, hospital_id: str) -> None:
        self._connection.execute(
            "DELETE FROM user_hospital_access WHERE user_id = ? AND hospital_id = ?",
            (user_id, hospital_id),
        )
        self._connection.commit()

    def list_hospital_grants(self, user_id: str) -> list[str]:
        rows = self._connection.execute(
            "SELECT hospital_id FROM user_hospital_access WHERE user_id = ? "
            "ORDER BY hospital_id",
            (user_id,),
        ).fetchall()
        return [str(row["hospital_id"]) for row in rows]

    def has_hospital_grant(self, user_id: str, hospital_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM user_hospital_access WHERE user_id = ? AND hospital_id = ?",
            (user_id, hospital_id),
        ).fetchone()
        return row is not None

    # ----------------------------------------------------------------- tokens

    def save_token(self, user_id: str, token_hash: str, expires_at: datetime) -> TokenRecord:
        token_id = f"t_{uuid4().hex[:16]}"
        record = TokenRecord(
            token_id=token_id,
            user_id=user_id,
            token_hash=token_hash,
            created_at=datetime.now(UTC),
            expires_at=expires_at,
        )
        self._connection.execute(
            """
            INSERT INTO auth_tokens
                (token_id, user_id, token_hash, created_at, expires_at, revoked_at)
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (
                record.token_id,
                record.user_id,
                record.token_hash,
                record.created_at.isoformat(),
                record.expires_at.isoformat(),
            ),
        )
        self._connection.commit()
        return record

    def get_token_by_hash(self, token_hash: str) -> TokenRecord | None:
        row = self._connection.execute(
            "SELECT * FROM auth_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None:
            return None
        return TokenRecord(
            token_id=str(row["token_id"]),
            user_id=str(row["user_id"]),
            token_hash=str(row["token_hash"]),
            created_at=_parse_dt(row["created_at"]),
            expires_at=_parse_dt(row["expires_at"]),
            revoked_at=_parse_dt(row["revoked_at"]) if row["revoked_at"] else None,
        )

    def revoke_token(self, token_hash: str) -> bool:
        cursor = self._connection.execute(
            "UPDATE auth_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (_now_iso(), token_hash),
        )
        self._connection.commit()
        return cursor.rowcount > 0

    def revoke_user_tokens(self, user_id: str) -> int:
        cursor = self._connection.execute(
            "UPDATE auth_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (_now_iso(), user_id),
        )
        self._connection.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------ audit

    def append_audit(self, event: AuditEvent) -> AuditEvent:
        """Append-only: no UPDATE/DELETE helpers are exposed on purpose."""
        self._connection.execute(
            """
            INSERT INTO audit_events
                (audit_event_id, timestamp, actor_id, actor_role, action,
                 resource_type, resource_id, hospital_id, outcome,
                 correlation_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.audit_event_id,
                event.timestamp.isoformat(),
                event.actor_id,
                event.actor_role,
                event.action,
                event.resource_type,
                event.resource_id,
                event.hospital_id,
                event.outcome,
                event.correlation_id,
                _safe_metadata_json(event.metadata),
            ),
        )
        self._connection.commit()
        return event

    def list_audit(
        self,
        *,
        action: str | None = None,
        hospital_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if hospital_id:
            clauses.append("hospital_id = ?")
            params.append(hospital_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT * FROM audit_events{where} "  # noqa: S608
            "ORDER BY timestamp DESC, audit_event_id DESC LIMIT ?",
            (*params, max(1, min(limit, 1000))),
        ).fetchall()
        return [self._audit_from_row(row) for row in rows]

    def get_audit(self, audit_event_id: str) -> AuditEvent | None:
        row = self._connection.execute(
            "SELECT * FROM audit_events WHERE audit_event_id = ?", (audit_event_id,)
        ).fetchone()
        return self._audit_from_row(row) if row is not None else None

    def count_audit(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS c FROM audit_events").fetchone()
        return int(row["c"]) if row is not None else 0

    def _audit_from_row(self, row: sqlite3.Row) -> AuditEvent:
        import json

        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return AuditEvent(
            audit_event_id=str(row["audit_event_id"]),
            timestamp=_parse_dt(row["timestamp"]),
            actor_id=str(row["actor_id"] or ""),
            actor_role=str(row["actor_role"] or ""),
            action=str(row["action"]),
            resource_type=str(row["resource_type"] or ""),
            resource_id=str(row["resource_id"] or ""),
            hospital_id=str(row["hospital_id"] or ""),
            outcome=str(row["outcome"] or "success"),
            correlation_id=str(row["correlation_id"] or ""),
            metadata=metadata,
        )


def _safe_metadata_json(metadata: dict[str, Any]) -> str:
    """Serialize metadata after stripping any secret-looking keys."""
    import json

    forbidden = {
        "password",
        "password_hash",
        "token",
        "access_token",
        "authorization",
        "secret",
        "api_key",
        "credential",
        "cookie",
        "set-cookie",
    }
    safe = {
        key: value
        for key, value in metadata.items()
        if str(key).lower() not in forbidden
    }
    return json.dumps(safe, default=str)
