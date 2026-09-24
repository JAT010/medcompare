"""SQLite persistence for canonical datasets and their provenance.

The database stores the canonical :class:`HospitalDataset` payload as JSON
plus queryable flat tables (sources, records) so the profile/store layer can
answer "which hospitals do we know about, from which source, and when?".

Rules:

- Canonical JSON is the single source of truth; the flat tables are
  query/index-friendly mirrors of it.
- All values are bound into parameterised statements — no string
  interpolation of user data.
- ``dataset.model_dump_json()`` round-trips with ``read_dataset``/Pydantic
  validation, guaranteeing what you save is what you load.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from hospital_extractor.models.change import ChangeSet
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.snapshot import (
    DatasetSnapshot,
    SnapshotCapture,
    SnapshotSummary,
)
from hospital_extractor.models.verification import (
    VerificationJob,
    VerificationJobStatus,
    VerificationResult,
)
from hospital_extractor.orchestration.models import (
    OrchestrationRun,
    OrchestrationStatus,
)
from hospital_extractor.reconciliation.models import (
    ReconciliationCase,
    ReconciliationCaseStatus,
    ReconciliationEvent,
)
from hospital_extractor.reporting.models import (
    NotificationProviderType,
    Report,
    ReportStatus,
    Submission,
    SubmissionStatus,
)
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES
from hospital_extractor.snapshots.change_detection import build_change_set
from hospital_extractor.snapshots.fingerprint import canonical_digest

__all__ = [
    "DatasetSummary",
    "HospitalDatabase",
    "HospitalSummary",
    "default_database_path",
]

DEFAULT_DB_PATH = Path("data/medcompare.db")


class DatasetSummary(BaseModel):
    """Lightweight overview of one stored dataset."""

    model_config = ConfigDict(str_strip_whitespace=True)

    dataset_id: str
    hospital_ref: str
    hospital_name: str | None = None
    hospital_key: str = ""
    hospital_id: str = ""
    source_type: str
    provider: str | None = None
    created_at: datetime
    record_count: int = 0
    quality_records: int = 0


class HospitalSummary(BaseModel):
    """Registry view of one canonical hospital identity."""

    model_config = ConfigDict(str_strip_whitespace=True)

    hospital_id: str
    name: str | None = None
    created_at: datetime
    dataset_count: int = 0
    snapshot_count: int = 0
    source_count: int = 0


def default_database_path() -> Path:
    """Return the default database file path."""
    return DEFAULT_DB_PATH


class HospitalDatabase:
    """SQLite-backed store for canonical hospital datasets."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        self._create_tables()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> HospitalDatabase:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ DDL
    def _create_tables(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                dataset_id    TEXT PRIMARY KEY,
                hospital_ref  TEXT NOT NULL,
                hospital_name TEXT,
                hospital_key  TEXT NOT NULL DEFAULT '',
                hospital_id   TEXT NOT NULL DEFAULT '',
                source_type   TEXT NOT NULL,
                provider      TEXT,
                created_at    TEXT NOT NULL,
                payload       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sources (
                source_id     TEXT PRIMARY KEY,
                dataset_id    TEXT,
                source_type   TEXT NOT NULL,
                provider      TEXT,
                base_url      TEXT,
                hospital_ref  TEXT,
                hospital_name TEXT,
                retrieved_at  TEXT,
                status        TEXT
            );

            CREATE TABLE IF NOT EXISTS records (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id  TEXT NOT NULL,
                category    TEXT NOT NULL,
                name        TEXT,
                source_url  TEXT,
                source_type TEXT NOT NULL,
                source_id   TEXT,
                payload     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hospitals (
                hospital_id TEXT PRIMARY KEY,
                name        TEXT,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hospital_aliases (
                alias_type  TEXT NOT NULL,
                alias_value TEXT NOT NULL,
                hospital_id TEXT NOT NULL,
                PRIMARY KEY (alias_type, hospital_id)
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id  TEXT PRIMARY KEY,
                hospital_id  TEXT NOT NULL,
                source_type  TEXT NOT NULL,
                source_id    TEXT,
                provider     TEXT,
                hospital_ref TEXT NOT NULL DEFAULT '',
                captured_at  TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                status       TEXT NOT NULL,
                dataset_id   TEXT,
                payload      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS change_sets (
                change_set_id        TEXT PRIMARY KEY,
                snapshot_id          TEXT NOT NULL,
                previous_snapshot_id TEXT,
                hospital_id          TEXT NOT NULL,
                source_type          TEXT NOT NULL,
                detected_at          TEXT NOT NULL,
                status               TEXT NOT NULL,
                added                INTEGER NOT NULL DEFAULT 0,
                removed              INTEGER NOT NULL DEFAULT 0,
                modified             INTEGER NOT NULL DEFAULT 0,
                payload              TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS verification_jobs (
                verification_job_id   TEXT PRIMARY KEY,
                hospital_id           TEXT NOT NULL,
                change_set_id         TEXT NOT NULL,
                triggering_snapshot_id TEXT NOT NULL,
                triggering_source_type TEXT NOT NULL,
                target_source_type    TEXT NOT NULL,
                target_ref            TEXT NOT NULL DEFAULT '',
                status                TEXT NOT NULL,
                scheduled_for         TEXT NOT NULL,
                started_at            TEXT,
                completed_at          TEXT,
                attempt_count         INTEGER NOT NULL DEFAULT 0,
                last_error            TEXT,
                created_at            TEXT NOT NULL,
                updated_at            TEXT NOT NULL,
                payload               TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS verification_results (
                verification_result_id  TEXT PRIMARY KEY,
                job_id                  TEXT NOT NULL,
                hospital_id             TEXT NOT NULL,
                change_set_id           TEXT NOT NULL,
                status                  TEXT NOT NULL,
                 verification_snapshot_id TEXT,
                 checked_at              TEXT NOT NULL,
                 created_at              TEXT NOT NULL,
                 target_source_type      TEXT,
                 payload                 TEXT NOT NULL
             );

             CREATE TABLE IF NOT EXISTS reconciliation_cases (
                reconciliation_case_id TEXT PRIMARY KEY,
                hospital_id             TEXT NOT NULL,
                change_set_id           TEXT NOT NULL,
                verification_job_id     TEXT NOT NULL,
                verification_result_id  TEXT NOT NULL UNIQUE,
                status                  TEXT NOT NULL,
                trigger                 TEXT NOT NULL,
                created_by              TEXT NOT NULL DEFAULT 'system',
                assigned_to             TEXT,
                resolution              TEXT,
                resolution_reason       TEXT,
                resolved_at             TEXT,
                created_at              TEXT NOT NULL,
                updated_at              TEXT NOT NULL,
                payload                 TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reconciliation_events (
                event_id    TEXT PRIMARY KEY,
                case_id     TEXT NOT NULL,
                actor       TEXT NOT NULL,
                action      TEXT NOT NULL,
                reason      TEXT,
                from_status TEXT,
                to_status   TEXT,
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_records_dataset ON records(dataset_id);
            CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_type);
            CREATE INDEX IF NOT EXISTS idx_dataset_source ON datasets(source_type);
            CREATE INDEX IF NOT EXISTS idx_aliases_hospital ON hospital_aliases(hospital_id);
            CREATE INDEX IF NOT EXISTS idx_aliases_lookup
                ON hospital_aliases(alias_type, alias_value);
            CREATE INDEX IF NOT EXISTS idx_snapshots_hospital ON snapshots(hospital_id);
            CREATE INDEX IF NOT EXISTS idx_snapshots_hospital_source
                ON snapshots(hospital_id, source_type);
            CREATE INDEX IF NOT EXISTS idx_changesets_hospital ON change_sets(hospital_id);
            CREATE INDEX IF NOT EXISTS idx_verification_jobs_hospital
                ON verification_jobs(hospital_id);
            CREATE INDEX IF NOT EXISTS idx_verification_jobs_due
                ON verification_jobs(status, scheduled_for);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_verification_jobs_unique
                ON verification_jobs(change_set_id, target_source_type);
            CREATE INDEX IF NOT EXISTS idx_verification_jobs_changeset
                ON verification_jobs(change_set_id);
            CREATE INDEX IF NOT EXISTS idx_verification_results_job
                ON verification_results(job_id);
            CREATE INDEX IF NOT EXISTS idx_verification_results_hospital
                ON verification_results(hospital_id);
            CREATE INDEX IF NOT EXISTS idx_reconciliation_cases_hospital
                ON reconciliation_cases(hospital_id);
            CREATE INDEX IF NOT EXISTS idx_reconciliation_cases_status
                ON reconciliation_cases(status);
            CREATE INDEX IF NOT EXISTS idx_reconciliation_events_case
                ON reconciliation_events(case_id);

            CREATE TABLE IF NOT EXISTS reports (
                report_id             TEXT PRIMARY KEY,
                hospital_id           TEXT NOT NULL,
                reconciliation_case_id TEXT,
                verification_result_id TEXT,
                change_set_id         TEXT,
                report_type           TEXT NOT NULL,
                status                TEXT NOT NULL,
                format                TEXT NOT NULL DEFAULT 'json',
                generated_at          TEXT NOT NULL,
                generated_by          TEXT NOT NULL DEFAULT 'system',
                created_at            TEXT NOT NULL,
                updated_at            TEXT NOT NULL,
                payload               TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS submissions (
                submission_id       TEXT PRIMARY KEY,
                report_id           TEXT NOT NULL,
                provider_type       TEXT NOT NULL,
                idempotency_key     TEXT NOT NULL UNIQUE,
                status              TEXT NOT NULL,
                submitted_at        TEXT,
                external_reference  TEXT,
                response_metadata   TEXT NOT NULL DEFAULT '{}',
                error               TEXT,
                dry_run             INTEGER NOT NULL DEFAULT 0,
                created_by          TEXT NOT NULL DEFAULT 'system',
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orchestration_runs (
                run_id                     TEXT PRIMARY KEY,
                hospital_id                TEXT NOT NULL,
                source_type                TEXT NOT NULL,
                trigger_ref                TEXT NOT NULL DEFAULT '',
                status                     TEXT NOT NULL,
                current_stage              TEXT NOT NULL,
                error                      TEXT,
                error_class                TEXT,
                created_at                 TEXT NOT NULL,
                updated_at                 TEXT NOT NULL,
                created_by                 TEXT NOT NULL DEFAULT 'system',
                idempotency_key            TEXT NOT NULL UNIQUE,
                snapshot_id                TEXT,
                change_set_id              TEXT,
                change_set_status          TEXT,
                verification_job_id        TEXT,
                verification_result_id     TEXT,
                verification_result_status TEXT,
                reconciliation_case_id     TEXT,
                reconciliation_case_status TEXT,
                report_id                  TEXT,
                report_idempotency_key     TEXT,
                submission_id              TEXT,
                submission_idempotency_key TEXT,
                metrics                    TEXT NOT NULL DEFAULT '{}',
                completed_at               TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_reports_hospital ON reports(hospital_id);
            CREATE INDEX IF NOT EXISTS idx_reports_case ON reports(reconciliation_case_id);
            CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
            CREATE INDEX IF NOT EXISTS idx_submissions_report ON submissions(report_id);
            CREATE INDEX IF NOT EXISTS idx_submissions_provider ON submissions(provider_type);
            CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);

            CREATE INDEX IF NOT EXISTS idx_orchestration_runs_hospital
            ON orchestration_runs(hospital_id);
            CREATE INDEX IF NOT EXISTS idx_orchestration_runs_status
            ON orchestration_runs(status);
            CREATE INDEX IF NOT EXISTS idx_orchestration_runs_idempotency
            ON orchestration_runs(idempotency_key);
            """
        )
        self._connection.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Additive schema migrations for databases created before a column existed."""
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(datasets)").fetchall()
        }
        if "hospital_key" not in columns:
            self._connection.execute(
                "ALTER TABLE datasets ADD COLUMN hospital_key TEXT NOT NULL DEFAULT ''"
            )
            self._connection.commit()
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_datasets_hospital_key ON datasets(hospital_key)"
        )
        self._connection.commit()
        added_hospital_id = False
        if "hospital_id" not in columns:
            self._connection.execute(
                "ALTER TABLE datasets ADD COLUMN hospital_id TEXT NOT NULL DEFAULT ''"
            )
            added_hospital_id = True
            self._connection.commit()
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_datasets_hospital_id ON datasets(hospital_id)"
        )
        self._connection.commit()
        if added_hospital_id:
            # Backfill: assign a canonical hospital identity to every existing
            # dataset, resolving on source_ref and registering name aliases.
            rows = self._connection.execute(
                "SELECT dataset_id, hospital_ref, hospital_name FROM datasets"
            ).fetchall()
            for row in rows:
                hospital_id = self.hospital_id_for(
                    source_ref=row["hospital_ref"] or None,
                    hospital_name=row["hospital_name"] or None,
                )
                self._connection.execute(
                    "UPDATE datasets SET hospital_id = ? WHERE dataset_id = ?",
                    (hospital_id, row["dataset_id"]),
                )
            self._connection.commit()
        result_columns = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(verification_results)"
            ).fetchall()
        }
        if result_columns and "target_source_type" not in result_columns:
            self._connection.execute(
                "ALTER TABLE verification_results ADD COLUMN target_source_type TEXT"
            )
            self._connection.commit()

    # ---------------------------------------------------------------- writes

    def save_dataset(
        self,
        dataset: HospitalDataset,
        dataset_id: str | None = None,
        *,
        hospital_id: str | None = None,
    ) -> str:
        """Persist *dataset* and return its dataset id.

        The dataset's hospital is resolved against the canonical registry
        (minting an identity when this is the first time the hospital is seen,
        else reusing the existing one) so every stored capture is tied to one
        stable ``hospital_id`` regardless of source, dataset id or name.
        ``hospital_id`` may override resolution (used by ``save_snapshot`` so a
        verification website capture stays under the host hospital's identity).
        """
        identifier = dataset_id or self._dataset_id(dataset)
        hospital_ref = (
            dataset.source.hospital_ref
            if dataset.source is not None and dataset.source.hospital_ref
            else (dataset.hospital.source_url if dataset.hospital is not None else "")
        )
        hospital_name = dataset.hospital.name if dataset.hospital is not None else None
        if hospital_id is not None:
            resolved_hospital_id = hospital_id
        else:
            resolved_hospital_id = self.hospital_id_for(
                source_ref=hospital_ref or None,
                hospital_name=hospital_name,
            )
        hospital_key = self.hospital_key_for(dataset)
        created_at = _utc_now_iso()
        payload = dataset.model_dump_json()

        self._connection.execute(
            """
            INSERT OR REPLACE INTO datasets
                (dataset_id, hospital_ref, hospital_name, hospital_key,
                 hospital_id, source_type, provider, created_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                hospital_ref,
                hospital_name,
                hospital_key,
                resolved_hospital_id,
                (dataset.source.source_type.value if dataset.source is not None else "website"),
                (dataset.source.provider if dataset.source is not None else None),
                created_at,
                payload,
            ),
        )

        self._replace_sources(identifier, dataset)
        self._replace_records(identifier, dataset)
        self._connection.commit()
        return identifier

    def _replace_sources(self, dataset_id: str, dataset: HospitalDataset) -> None:
        self._connection.execute("DELETE FROM sources WHERE dataset_id = ?", (dataset_id,))
        if dataset.source is not None:
            self._connection.execute(
                """
                INSERT INTO sources
                    (source_id, dataset_id, source_type, provider, base_url,
                     hospital_ref, hospital_name, retrieved_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    # source_id is not unique across hospitals (it derives from
                    # the provider), so it is scoped to the owning dataset.
                    f"{dataset_id}::{dataset.source.source_id}",
                    dataset_id,
                    dataset.source.source_type.value,
                    dataset.source.provider,
                    dataset.source.base_url,
                    dataset.source.hospital_ref,
                    dataset.source.hospital_name,
                    dataset.source.retrieved_at.isoformat(),
                    dataset.source.status.value,
                ),
            )

    def _replace_records(self, dataset_id: str, dataset: HospitalDataset) -> None:
        self._connection.execute("DELETE FROM records WHERE dataset_id = ?", (dataset_id,))
        # The scoped source id keeps records.source_id consistent with the
        # sources table (source_id derives from the provider, which is shared
        # across hospitals, so it is scoped to the owning dataset).
        scoped_source_id = (
            f"{dataset_id}::{dataset.source.source_id}"
            if dataset.source is not None
            else ""
        )
        rows: list[tuple[Any, ...]] = []
        for category in CANONICAL_CATEGORIES:
            records = _category_records(dataset, category.key)
            for record in records:
                payload = record.model_dump_json()
                rows.append(
                    (
                        dataset_id,
                        category.key,
                        getattr(record, "name", None),
                        getattr(record, "source_url", None),
                        getattr(record, "source_type", None) or "website",
                        scoped_source_id,
                        payload,
                    )
                )
        if rows:
            self._connection.executemany(
                """
                INSERT INTO records
                    (dataset_id, category, name, source_url, source_type, source_id, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    # ----------------------------------------------------------------- reads

    def load_dataset(self, dataset_id: str) -> HospitalDataset | None:
        row = self._connection.execute(
            "SELECT payload FROM datasets WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()
        if row is None:
            return None
        return HospitalDataset.model_validate_json(row["payload"])

    def list_datasets(self) -> list[DatasetSummary]:
        rows = self._connection.execute(
            """
            SELECT d.dataset_id, d.hospital_ref, d.hospital_name, d.hospital_key,
                   d.hospital_id, d.source_type, d.provider, d.created_at,
                   (SELECT COUNT(*) FROM records r WHERE r.dataset_id = d.dataset_id)
                       AS record_count,
                   d.payload
            FROM datasets d
            ORDER BY d.created_at DESC
            """
        ).fetchall()
        return [_summary_from_row(row) for row in rows]

    def list_hospital_sources(self, hospital_key: str) -> list[DatasetSummary]:
        """Every stored dataset for a hospital, newest first.

        Grouping by ``hospital_key`` lets one hospital accumulate several
        datasets from different sources (website, demo API, imported feed)
        while staying a single identity in profiles.
        """
        rows = self._connection.execute(
            """
            SELECT d.dataset_id, d.hospital_ref, d.hospital_name, d.hospital_key,
                   d.hospital_id, d.source_type, d.provider, d.created_at,
                   (SELECT COUNT(*) FROM records r WHERE r.dataset_id = d.dataset_id)
                       AS record_count,
                   d.payload
            FROM datasets d
            WHERE d.hospital_key = ?
            ORDER BY d.created_at DESC
            """,
            (hospital_key,),
        ).fetchall()
        return [_summary_from_row(row) for row in rows]

    def search_records(
        self,
        *,
        category: str | None = None,
        name_contains: str | None = None,
        source_type: str | None = None,
    ) -> list[dict[str, str]]:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if name_contains:
            clauses.append("name LIKE ?")
            params.append(f"%{name_contains}%")
        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT dataset_id, category, name, source_url, source_type, payload "
            f"FROM records{where} ORDER BY name LIMIT 500",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_dataset(self, dataset_id: str) -> bool:
        cursor = self._connection.execute(
            "DELETE FROM datasets WHERE dataset_id = ?", (dataset_id,)
        )
        self._connection.execute("DELETE FROM sources WHERE dataset_id = ?", (dataset_id,))
        self._connection.execute("DELETE FROM records WHERE dataset_id = ?", (dataset_id,))
        self._connection.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------ identity registry

    def hospital_id_for(
        self,
        *,
        preferred: str | None = None,
        source_ref: str | None = None,
        hospital_name: str | None = None,
    ) -> str:
        """Resolve a canonical hospital identity (minting one when unseen).

        Resolution order: a trusted ``preferred`` identity that is already
        registered, then an existing ``source_ref`` alias.  Names NEVER
        resolve on this write path — two sources describing different
        hospitals that share a name must stay separate histories (P19-C
        reconciles them later); a name is only registered as a read-time
        alias.  If nothing resolves, a new opaque ``h_…`` identity is minted
        and registered with the aliases we know.  ``preferred`` is never
        minted.  Read paths that must not create rows use
        :meth:`find_hospital_id` instead.
        """
        resolved = self.find_hospital_id(
            preferred=preferred,
            source_ref=source_ref,
        )
        if resolved is not None:
            if hospital_name:
                self._register_hospital_name(resolved, hospital_name)
            return resolved

        hospital_id = f"h_{uuid4().hex[:12]}"
        self._connection.execute(
            "INSERT INTO hospitals (hospital_id, name, created_at) VALUES (?, ?, ?)",
            (hospital_id, hospital_name, _utc_now_iso()),
        )
        if source_ref:
            self._connection.execute(
                "INSERT OR IGNORE INTO hospital_aliases (alias_type, alias_value, hospital_id)"
                " VALUES ('source_ref', ?, ?)",
                (_slug(source_ref), hospital_id),
            )
        if hospital_name:
            self._connection.execute(
                "INSERT OR IGNORE INTO hospital_aliases (alias_type, alias_value, hospital_id)"
                " VALUES ('name_slug', ?, ?)",
                (_slug(hospital_name), hospital_id),
            )
        self._connection.commit()
        return hospital_id

    def find_hospital_id(
        self,
        *,
        preferred: str | None = None,
        source_ref: str | None = None,
        hospital_name: str | None = None,
    ) -> str | None:
        """Resolve a canonical hospital identity WITHOUT writing to the registry.

        ``preferred`` matches exactly a registered ``h_…`` id (a caller that
        knows the id).  Returns ``None`` when there is nothing unambiguous to
        resolve to — it never creates rows.
        """
        if preferred:
            row = self._connection.execute(
                "SELECT hospital_id FROM hospitals WHERE hospital_id = ?",
                (preferred,),
            ).fetchone()
            if row is not None:
                return cast(str, row["hospital_id"])
        if source_ref:
            return self._find_exclusive_alias("source_ref", _slug(source_ref))
        if hospital_name:
            return self._find_exclusive_alias("name_slug", _slug(hospital_name))
        return None

    def _find_exclusive_alias(self, alias_type: str, alias_value: str) -> str | None:
        """Return the hospital owning an alias iff it is unambiguous.

        Several hospitals may legitimately share an alias value (e.g. the same
        name from two different sources).  Resolving such an alias would
        silently merge distinct histories, so an ambiguous alias fails closed.
        """
        hospitals = [
            row["hospital_id"]
            for row in self._connection.execute(
                "SELECT DISTINCT hospital_id FROM hospital_aliases"
                " WHERE alias_type = ? AND alias_value = ?",
                (alias_type, alias_value),
            ).fetchall()
        ]
        return hospitals[0] if len(hospitals) == 1 else None

    def _register_hospital_name(self, hospital_id: str, hospital_name: str) -> None:
        row = self._connection.execute(
            "SELECT name FROM hospitals WHERE hospital_id = ?", (hospital_id,)
        ).fetchone()
        if row is not None and not row["name"]:
            self._connection.execute(
                "UPDATE hospitals SET name = ? WHERE hospital_id = ?",
                (hospital_name, hospital_id),
            )
            self._connection.commit()
        self._connection.execute(
            "INSERT OR IGNORE INTO hospital_aliases (alias_type, alias_value, hospital_id)"
            " VALUES ('name_slug', ?, ?)",
            (_slug(hospital_name), hospital_id),
        )
        self._connection.commit()

    def list_hospitals(self) -> list[HospitalSummary]:
        """Every canonical hospital identity with dataset/snapshot counts."""
        rows = self._connection.execute(
            """
            SELECT h.hospital_id, h.name, h.created_at,
                   (SELECT COUNT(*) FROM datasets d WHERE d.hospital_id = h.hospital_id)
                       AS dataset_count,
                   (SELECT COUNT(*) FROM snapshots s WHERE s.hospital_id = h.hospital_id)
                       AS snapshot_count,
                   (SELECT COUNT(DISTINCT s.source_type)
                       FROM snapshots s WHERE s.hospital_id = h.hospital_id) AS source_count
            FROM hospitals h
            ORDER BY h.created_at ASC
            """
        ).fetchall()
        return [_hospital_from_row(row) for row in rows]

    # ------------------------------------------------------- snapshot storage

    def save_snapshot(
        self,
        dataset: HospitalDataset,
        *,
        dataset_id: str | None = None,
        captured_at: datetime | None = None,
        hospital_id: str | None = None,
    ) -> SnapshotCapture:
        """Capture *dataset* as an immutable snapshot and detect changes.

        Semantics:

        - The hospital's canonical identity is resolved/registered for this
        dataset (same registry as :meth:`save_dataset`) unless ``hospital_id``
        is given, in which case that identity is used verbatim (the
        P19-B verification worker stores a website capture under the host
        hospital's canonical id without minting a second identity).
        - A digest is computed over the dataset's semantic content.  If the
        latest snapshot for (hospital, source) already carries that digest,
        nothing is written: the capture is idempotent and returns a
        ``no_change`` change set referencing the existing snapshot.
        - Otherwise the snapshot row, its change set (``initial_snapshot`` for
        the first capture) and the mutable latest ``datasets`` row are all
        persisted.
        """
        timestamp = captured_at or datetime.now(UTC)
        hospital_ref = (
            dataset.source.hospital_ref
            if dataset.source is not None and dataset.source.hospital_ref
            else (dataset.hospital.source_url if dataset.hospital is not None else "")
        )
        hospital_name = dataset.hospital.name if dataset.hospital is not None else None
        if hospital_id is not None:
            resolved_hospital_id = hospital_id
        else:
            resolved_hospital_id = self.hospital_id_for(
                source_ref=hospital_ref or None,
                hospital_name=hospital_name,
            )
        source_type = (
            dataset.source.source_type.value if dataset.source is not None else "website"
        )
        provider = dataset.source.provider if dataset.source is not None else None
        digest = canonical_digest(dataset)

        latest = self.get_latest_snapshot(resolved_hospital_id, source_type)
        if latest is not None and latest.content_hash == digest:
            change_set = build_change_set(
                latest,
                latest,
                change_set_id=f"c_{uuid4().hex[:12]}",
                detected_at=timestamp,
            )
            return SnapshotCapture(snapshot=latest, change_set=change_set)

        base_dataset_id = dataset_id or self._dataset_id(dataset)
        snapshot = DatasetSnapshot(
            snapshot_id=f"s_{uuid4().hex[:12]}",
            hospital_id=resolved_hospital_id,
            source_type=source_type,
            source_id=f"{dataset.source.source_id}" if dataset.source is not None else "",
            provider=provider,
            hospital_key=self.hospital_key_for(dataset),
            hospital_ref=hospital_ref,
            hospital_name=hospital_name,
            captured_at=timestamp,
            content_hash=digest,
            dataset_id=base_dataset_id,
            status="initial" if latest is None else "detected",
            dataset=dataset,
        )
        self._insert_snapshot(snapshot)
        change_set = build_change_set(latest, snapshot, detected_at=timestamp)
        self._insert_change_set(change_set)
        self.save_dataset(dataset, base_dataset_id, hospital_id=resolved_hospital_id)
        return SnapshotCapture(snapshot=snapshot, change_set=change_set)

    def _insert_snapshot(self, snapshot: DatasetSnapshot) -> None:
        self._connection.execute(
            """
            INSERT INTO snapshots
                (snapshot_id, hospital_id, source_type, source_id, provider,
                 hospital_ref, captured_at, content_hash, status, dataset_id, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.hospital_id,
                snapshot.source_type,
                snapshot.source_id,
                snapshot.provider,
                snapshot.hospital_ref,
                snapshot.captured_at.isoformat(),
                snapshot.content_hash,
                snapshot.status,
                snapshot.dataset_id,
                snapshot.model_dump_json(),
            ),
        )

    def _insert_change_set(self, change_set: ChangeSet) -> None:
        self._connection.execute(
            """
            INSERT INTO change_sets
                (change_set_id, snapshot_id, previous_snapshot_id, hospital_id,
                 source_type, detected_at, status, added, removed, modified, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                change_set.change_set_id,
                change_set.snapshot_id,
                change_set.previous_snapshot_id,
                change_set.hospital_id,
                change_set.source_type,
                change_set.detected_at.isoformat(),
                change_set.status.value,
                change_set.added,
                change_set.removed,
                change_set.modified,
                change_set.model_dump_json(),
            ),
        )
        self._connection.commit()

    def get_snapshot(self, snapshot_id: str) -> DatasetSnapshot | None:
        row = self._connection.execute(
            "SELECT * FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        return _snapshot_from_row(row) if row is not None else None

    def get_latest_snapshot(
        self,
        hospital_id: str,
        source_type: str,
    ) -> DatasetSnapshot | None:
        row = self._connection.execute(
            """
            SELECT * FROM snapshots
            WHERE hospital_id = ? AND source_type = ?
            ORDER BY captured_at DESC, snapshot_id DESC
            LIMIT 1
            """,
            (hospital_id, source_type),
        ).fetchone()
        return _snapshot_from_row(row) if row is not None else None

    def list_snapshots(
        self,
        hospital_id: str,
        *,
        source_type: str | None = None,
        limit: int = 100,
    ) -> list[SnapshotSummary]:
        """Snapshot history for a hospital, newest first."""
        clauses = ["hospital_id = ?"]
        params: list[Any] = [hospital_id]
        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)
        rows = self._connection.execute(
            f"""
            SELECT * FROM snapshots
            WHERE {' AND '.join(clauses)}
            ORDER BY captured_at DESC, snapshot_id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [_snapshot_summary_from_row(row) for row in rows]

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """Remove a snapshot and any change sets that reference it."""
        cursor = self._connection.execute(
            "DELETE FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
        )
        self._connection.execute(
            "DELETE FROM change_sets WHERE snapshot_id = ? OR previous_snapshot_id = ?",
            (snapshot_id, snapshot_id),
        )
        self._connection.commit()
        return cursor.rowcount > 0

    def list_change_sets(
        self,
        hospital_id: str,
        *,
        source_type: str | None = None,
        limit: int = 100,
    ) -> list[ChangeSet]:
        """Audit trail of change-detection runs for a hospital, newest first."""
        clauses = ["hospital_id = ?"]
        params: list[Any] = [hospital_id]
        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)
        rows = self._connection.execute(
            f"""
            SELECT payload FROM change_sets
            WHERE {' AND '.join(clauses)}
            ORDER BY detected_at DESC, change_set_id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [ChangeSet.model_validate_json(row["payload"]) for row in rows]

    def get_change_set(self, change_set_id: str) -> ChangeSet | None:
        """One change set by id (used by the verification worker)."""
        row = self._connection.execute(
            "SELECT payload FROM change_sets WHERE change_set_id = ?",
            (change_set_id,),
        ).fetchone()
        return ChangeSet.model_validate_json(row["payload"]) if row is not None else None

    # ------------------------------------------------- verification job storage

    def create_verification_job(self, job: VerificationJob) -> VerificationJob:
        """Persist a new verification job (idempotent per change-set/target).

        The unique ``(change_set_id, target_source_type)`` index guarantees the
        same ChangeSet can never create a duplicate job for the same target:
        the second ``schedule()`` of the same event returns the existing job.
        """
        try:
            self._connection.execute(
                """
                INSERT INTO verification_jobs
                    (verification_job_id, hospital_id, change_set_id,
                     triggering_snapshot_id, triggering_source_type,
                     target_source_type, target_ref, status, scheduled_for,
                     started_at, completed_at, attempt_count, last_error,
                     created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.verification_job_id,
                    job.hospital_id,
                    job.change_set_id,
                    job.triggering_snapshot_id,
                    job.triggering_source_type,
                    job.target_source_type,
                    job.target_ref,
                    job.status.value,
                    job.scheduled_for.isoformat(),
                    _iso_or_none(job.started_at),
                    _iso_or_none(job.completed_at),
                    job.attempt_count,
                    job.last_error,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                    job.model_dump_json(),
                ),
            )
        except sqlite3.IntegrityError:
            # Duplicate (change_set_id, target_source_type) — return the
            # existing job instead of raising.
            return self.get_verification_job_for_change_set(
                job.change_set_id, job.target_source_type
            ) or self.get_verification_job(job.verification_job_id) or job
        self._connection.commit()
        return self.get_verification_job(job.verification_job_id) or job

    def get_verification_job(self, verification_job_id: str) -> VerificationJob | None:
        row = self._connection.execute(
            "SELECT * FROM verification_jobs WHERE verification_job_id = ?",
            (verification_job_id,),
        ).fetchone()
        return _verification_job_from_row(row) if row is not None else None

    def get_verification_job_for_change_set(
        self, change_set_id: str, target_source_type: str
    ) -> VerificationJob | None:
        row = self._connection.execute(
            "SELECT * FROM verification_jobs WHERE change_set_id = ? AND target_source_type = ?",
            (change_set_id, target_source_type),
        ).fetchone()
        return _verification_job_from_row(row) if row is not None else None

    def list_verification_jobs(
        self,
        hospital_id: str,
        *,
        status: str | None = None,
        source_type: str | None = None,
        limit: int = 100,
    ) -> list[VerificationJob]:
        """Verification jobs for a hospital, most recently created first."""
        clauses = ["hospital_id = ?"]
        params: list[Any] = [hospital_id]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if source_type:
            clauses.append("target_source_type = ?")
            params.append(source_type)
        rows = self._connection.execute(
            f"""
            SELECT * FROM verification_jobs
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC, verification_job_id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [_verification_job_from_row(row) for row in rows]

    def get_due_verification_jobs(self, now: datetime) -> list[VerificationJob]:
        """PENDING jobs whose ``scheduled_for`` has arrived, oldest first."""
        rows = self._connection.execute(
            """
            SELECT * FROM verification_jobs
            WHERE status = ? AND scheduled_for <= ?
            ORDER BY scheduled_for ASC, created_at ASC
            """,
            (VerificationJobStatus.PENDING.value, now.isoformat()),
        ).fetchall()
        return [_verification_job_from_row(row) for row in rows]

    def claim_verification_job(self, verification_job_id: str, started_at: datetime) -> bool:
        """Atomically transition a PENDING job to RUNNING.

        The guarded ``UPDATE ... WHERE status='pending'`` is a single atomic
        statement inside a transaction: exactly one concurrent caller sees
        ``rowcount == 1``, so two workers can never claim the same job.
        """
        cursor = None
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE verification_jobs
                SET status = ?, started_at = ?, attempt_count = attempt_count + 1,
                    last_error = NULL, updated_at = ?
                WHERE verification_job_id = ? AND status = ?
                """,
                (
                    VerificationJobStatus.RUNNING.value,
                    started_at.isoformat(),
                    started_at.isoformat(),
                    verification_job_id,
                    VerificationJobStatus.PENDING.value,
                ),
            )
        assert cursor is not None
        return cursor.rowcount == 1

    def complete_verification_job(
        self,
        verification_job_id: str,
        *,
        status: VerificationJobStatus,
        completed_at: datetime,
    ) -> None:
        """Move a RUNNING job to a terminal verification status."""
        self._connection.execute(
            """
            UPDATE verification_jobs
            SET status = ?, completed_at = ?, updated_at = ?
            WHERE verification_job_id = ?
            """,
            (
                status.value,
                completed_at.isoformat(),
                completed_at.isoformat(),
                verification_job_id,
            ),
        )
        self._connection.commit()

    def requeue_verification_job(
        self,
        verification_job_id: str,
        *,
        scheduled_for: datetime,
        attempt_count: int,
        last_error: str,
        updated_at: datetime,
    ) -> None:
        """Return a RUNNING job to PENDING after a retryable failure."""
        self._connection.execute(
            """
            UPDATE verification_jobs
            SET status = ?, scheduled_for = ?, attempt_count = ?,
                last_error = ?, started_at = NULL, updated_at = ?
            WHERE verification_job_id = ?
            """,
            (
                VerificationJobStatus.PENDING.value,
                scheduled_for.isoformat(),
                attempt_count,
                last_error,
                updated_at.isoformat(),
                verification_job_id,
            ),
        )
        self._connection.commit()

    def fail_verification_job(
        self,
        verification_job_id: str,
        *,
        last_error: str,
        completed_at: datetime,
        updated_at: datetime,
    ) -> None:
        """Move a RUNNING job to FAILED (permanent/configuration, or retries spent)."""
        self._connection.execute(
            """
            UPDATE verification_jobs
            SET status = ?, last_error = ?, completed_at = ?, updated_at = ?
            WHERE verification_job_id = ?
            """,
            (
                VerificationJobStatus.FAILED.value,
                last_error,
                completed_at.isoformat(),
                updated_at.isoformat(),
                verification_job_id,
            ),
        )
        self._connection.commit()

    # ------------------------------------------------- verification result storage

    def save_verification_result(self, result: VerificationResult) -> None:
        self._connection.execute(
            """
            INSERT INTO verification_results
                (verification_result_id, job_id, hospital_id, change_set_id,
                 status, verification_snapshot_id, checked_at, created_at,
                 target_source_type, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.verification_result_id,
                result.job_id,
                result.hospital_id,
                result.change_set_id,
                result.status.value,
                result.verification_snapshot_id,
                result.checked_at.isoformat(),
                result.created_at.isoformat(),
                result.provenance.target_source_type or None,
                result.model_dump_json(),
            ),
        )
        self._connection.commit()

    def get_verification_result(self, job_id: str) -> VerificationResult | None:
        row = self._connection.execute(
            "SELECT payload FROM verification_results WHERE job_id = ?", (job_id,)
        ).fetchone()
        return VerificationResult.model_validate_json(row["payload"]) if row is not None else None

    def list_verification_results(
        self,
        hospital_id: str,
        *,
        target_source_type: str | None = None,
        limit: int = 100,
    ) -> list[VerificationResult]:
        """Verification outcomes for a hospital, newest check first."""
        clauses = ["hospital_id = ?"]
        params: list[Any] = [hospital_id]
        if target_source_type:
            clauses.append("target_source_type = ?")
            params.append(target_source_type)
        rows = self._connection.execute(
            f"""
            SELECT payload FROM verification_results
            WHERE {' AND '.join(clauses)}
            ORDER BY checked_at DESC, verification_result_id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [VerificationResult.model_validate_json(row["payload"]) for row in rows]

    def get_latest_verification_result(
        self,
        hospital_id: str,
        *,
        target_source_type: str | None = None,
    ) -> VerificationResult | None:
        clause = "hospital_id = ?"
        params: list[Any] = [hospital_id]
        if target_source_type:
            clause += " AND target_source_type = ?"
            params.append(target_source_type)
        row = self._connection.execute(
            f"""
            SELECT payload FROM verification_results
            WHERE {clause}
            ORDER BY checked_at DESC, verification_result_id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return VerificationResult.model_validate_json(row["payload"]) if row is not None else None

    # ------------------------------------------------- reconciliation storage

    def create_reconciliation_case(self, case: ReconciliationCase) -> ReconciliationCase:
        """Persist a new reconciliation case (idempotent per verification result).

        The unique ``verification_result_id`` index guarantees the same result
        can never create a duplicate case: the second creation of the same
        result returns the existing case.  Reopening a case never inserts.
        """
        try:
            self._connection.execute(
                """
                INSERT INTO reconciliation_cases
                    (reconciliation_case_id, hospital_id, change_set_id,
                     verification_job_id, verification_result_id, status, trigger,
                     created_by, assigned_to, resolution, resolution_reason,
                     resolved_at, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case.reconciliation_case_id,
                    case.hospital_id,
                    case.change_set_id,
                    case.verification_job_id,
                    case.verification_result_id,
                    case.status.value,
                    case.trigger.value,
                    case.created_by,
                    case.assigned_to,
                    (
                        case.resolution.value
                        if case.resolution is not None
                        else None
                    ),
                    case.resolution_reason,
                    _iso_or_none(case.resolved_at),
                    case.created_at.isoformat(),
                    case.updated_at.isoformat(),
                    case.model_dump_json(),
                ),
            )
        except sqlite3.IntegrityError:
            # Duplicate (verification_result_id) — return the existing case.
            return (
                self.get_reconciliation_case_for_result(case.verification_result_id) or case
            )
        self._connection.commit()
        return self.get_reconciliation_case(case.reconciliation_case_id) or case

    def get_reconciliation_case(self, case_id: str) -> ReconciliationCase | None:
        row = self._connection.execute(
            "SELECT payload FROM reconciliation_cases WHERE reconciliation_case_id = ?",
            (case_id,),
        ).fetchone()
        if row is None:
            return None
        return ReconciliationCase.model_validate_json(row["payload"])

    def get_reconciliation_case_for_result(
        self, verification_result_id: str
    ) -> ReconciliationCase | None:
        row = self._connection.execute(
            "SELECT payload FROM reconciliation_cases WHERE verification_result_id = ?",
            (verification_result_id,),
        ).fetchone()
        if row is None:
            return None
        return ReconciliationCase.model_validate_json(row["payload"])

    def list_reconciliation_cases(
        self,
        *,
        hospital_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ReconciliationCase]:
        """Reconciliation cases, newest first (optionally filtered)."""
        clauses: list[str] = []
        params: list[Any] = []
        if hospital_id:
            clauses.append("hospital_id = ?")
            params.append(hospital_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"""
            SELECT payload FROM reconciliation_cases{where}
            ORDER BY created_at DESC, reconciliation_case_id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [ReconciliationCase.model_validate_json(row["payload"]) for row in rows]

    def update_reconciliation_case(self, case: ReconciliationCase) -> None:
        """Rewrite a case after a valid state transition (audited by service)."""
        self._connection.execute(
            """
            UPDATE reconciliation_cases
            SET status = ?, assigned_to = ?, resolution = ?, resolution_reason = ?,
                resolved_at = ?, updated_at = ?, payload = ?
            WHERE reconciliation_case_id = ?
            """,
            (
                case.status.value,
                case.assigned_to,
                case.resolution.value if case.resolution is not None else None,
                case.resolution_reason,
                _iso_or_none(case.resolved_at),
                case.updated_at.isoformat(),
                case.model_dump_json(),
                case.reconciliation_case_id,
            ),
        )
        self._connection.commit()

    def append_reconciliation_event(self, event: ReconciliationEvent) -> None:
        """Append one audit-trail entry for a case."""
        self._connection.execute(
            """
            INSERT INTO reconciliation_events
                (event_id, case_id, actor, action, reason,
                 from_status, to_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.case_id,
                event.actor,
                event.action,
                event.reason,
                event.from_status.value if event.from_status is not None else None,
                event.to_status.value if event.to_status is not None else None,
                event.created_at.isoformat(),
            ),
        )
        self._connection.commit()

    def list_reconciliation_events(self, case_id: str) -> list[ReconciliationEvent]:
        """Audit trail for one case, in insertion order (oldest first)."""
        rows = self._connection.execute(
            """
            SELECT * FROM reconciliation_events
            WHERE case_id = ?
            ORDER BY rowid ASC
            """,
            (case_id,),
        ).fetchall()
        return [
            ReconciliationEvent(
                event_id=row["event_id"],
                case_id=row["case_id"],
                actor=row["actor"],
                action=row["action"],
                reason=row["reason"],
                from_status=(
                    ReconciliationCaseStatus(row["from_status"])
                    if row["from_status"]
                    else None
                ),
                to_status=(
                    ReconciliationCaseStatus(row["to_status"])
                    if row["to_status"]
                    else None
                ),
                created_at=_parse_iso(row["created_at"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------ report storage

    def save_report(self, report: Report) -> Report:
        """Persist a new report."""
        self._connection.execute(
            """
            INSERT INTO reports
                (report_id, hospital_id, reconciliation_case_id,
                 verification_result_id, change_set_id, report_type, status,
                 format, generated_at, generated_by, created_at, updated_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.report_id,
                report.hospital_id,
                report.reconciliation_case_id,
                report.verification_result_id,
                report.change_set_id,
                report.report_type.value,
                report.status.value,
                report.format,
                report.generated_at.isoformat(),
                report.generated_by,
                report.created_at.isoformat(),
                report.updated_at.isoformat(),
                report.model_dump_json(),
            ),
        )
        self._connection.commit()
        return self.get_report(report.report_id) or report

    def get_report(self, report_id: str) -> Report | None:
        row = self._connection.execute(
            "SELECT payload FROM reports WHERE report_id = ?", (report_id,)
        ).fetchone()
        if row is None:
            return None
        return Report.model_validate_json(row["payload"])

    def get_report_for_case(
        self, reconciliation_case_id: str
    ) -> Report | None:
        row = self._connection.execute(
            "SELECT payload FROM reports WHERE reconciliation_case_id = ?",
            (reconciliation_case_id,),
        ).fetchone()
        if row is None:
            return None
        return Report.model_validate_json(row["payload"])

    def list_reports(
        self,
        *,
        hospital_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Report]:
        clauses: list[str] = []
        params: list[Any] = []
        if hospital_id:
            clauses.append("hospital_id = ?")
            params.append(hospital_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"""
            SELECT payload FROM reports{where}
            ORDER BY created_at DESC, report_id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [Report.model_validate_json(row["payload"]) for row in rows]

    def update_report_status(self, report_id: str, status: ReportStatus) -> None:
        self._connection.execute(
            """
            UPDATE reports
            SET status = ?, updated_at = ?
            WHERE report_id = ?
            """,
            (status.value, _utc_now_iso(), report_id),
        )
        self._connection.commit()

    # --------------------------------------------------------- submission storage

    def save_submission(self, submission: Submission) -> Submission:
        """Persist a new submission (idempotent per idempotency_key)."""
        try:
            self._connection.execute(
                """
                INSERT INTO submissions
                    (submission_id, report_id, provider_type, idempotency_key,
                     status, submitted_at, external_reference, response_metadata,
                     error, dry_run, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission.submission_id,
                    submission.report_id,
                    submission.provider_type.value,
                    submission.idempotency_key,
                    submission.status.value,
                    _iso_or_none(submission.submitted_at),
                    submission.external_reference,
                    json.dumps(submission.response_metadata),
                    submission.error,
                    1 if submission.dry_run else 0,
                    submission.created_by,
                    submission.created_at.isoformat(),
                    submission.updated_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError:
            # Duplicate idempotency_key — return existing submission.
            return self.get_submission_by_idempotency_key(
                submission.idempotency_key
            ) or submission
        self._connection.commit()
        return self.get_submission(submission.submission_id) or submission

    def get_submission(self, submission_id: str) -> Submission | None:
        row = self._connection.execute(
            "SELECT * FROM submissions WHERE submission_id = ?", (submission_id,)
        ).fetchone()
        return _submission_from_row(row) if row is not None else None

    def get_submission_by_idempotency_key(
        self, idempotency_key: str
    ) -> Submission | None:
        row = self._connection.execute(
            "SELECT * FROM submissions WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return _submission_from_row(row) if row is not None else None

    def list_submissions(
        self,
        *,
        report_id: str | None = None,
        provider_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Submission]:
        clauses: list[str] = []
        params: list[Any] = []
        if report_id:
            clauses.append("report_id = ?")
            params.append(report_id)
        if provider_type:
            clauses.append("provider_type = ?")
            params.append(provider_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"""
            SELECT * FROM submissions{where}
            ORDER BY created_at DESC, submission_id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [_submission_from_row(row) for row in rows]

    def update_submission(
        self,
        submission_id: str,
        *,
        status: SubmissionStatus | None = None,
        external_reference: str | None = None,
        response_metadata: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Submission | None:
        updates: list[str] = []
        params: list[Any] = []
        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
        if external_reference is not None:
            updates.append("external_reference = ?")
            params.append(external_reference)
        if response_metadata is not None:
            updates.append("response_metadata = ?")
            params.append(json.dumps(response_metadata))
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        if not updates:
            return self.get_submission(submission_id)

        updates.append("updated_at = ?")
        params.append(_utc_now_iso())
        params.append(submission_id)

        self._connection.execute(
            f"UPDATE submissions SET {', '.join(updates)} WHERE submission_id = ?",
            params,
        )
        self._connection.commit()
        return self.get_submission(submission_id)

    # ------------------------------------------------- orchestration run storage

    @staticmethod
    def hospital_key_for(dataset: HospitalDataset) -> str:
        """A stable per-hospital identity for grouping datasets across sources.

        Derived from the hospital name when known, else from the reference the
        source used for that hospital (URL for website crawls, hospital id for
        an API).  Two datasets describing the same hospital with the same name
        collapse to the same key.
        """
        name = dataset.hospital.name if dataset.hospital is not None else None
        ref = (
            dataset.source.hospital_ref
            if dataset.source is not None and dataset.source.hospital_ref
            else (dataset.hospital.source_url if dataset.hospital is not None else "")
        )
        return _slug(name or ref or "unknown")

    @staticmethod
    def _dataset_id(dataset: HospitalDataset) -> str:
        """A stable-ish, per-(hospital, source, ref) identifier.

        Two different sources describing the same hospital must never share an
        id (they are distinct captures with distinct provenance), so the id
        includes the hospital key, the source type and a token of the hospital
        reference, plus a timestamp.  Saving the same capture twice within a
        second therefore upserts into the same row — the intended "single
        latest capture" semantics.
        """
        key = _slug(HospitalDatabase.hospital_key_for(dataset))[:40]
        source_type = (
            dataset.source.source_type.value if dataset.source is not None else "website"
        )[:16]
        ref = (
            dataset.source.hospital_ref
            if dataset.source is not None and dataset.source.hospital_ref
            else (dataset.hospital.source_url if dataset.hospital is not None else "")
        )
        token = _slug(ref)[:20] or "ref"
        stamp = int(datetime.now(UTC).timestamp())
        return f"{key}_{token}_{source_type}_{stamp}"

    def save_orchestration_run(self, run: OrchestrationRun) -> OrchestrationRun:
        """Persist a new orchestration run."""
        self._connection.execute(
            """
            INSERT INTO orchestration_runs
                (run_id, hospital_id, source_type, trigger_ref, status,
                 current_stage, error, error_class, created_at, updated_at,
                 created_by, idempotency_key,
                 snapshot_id, change_set_id, change_set_status,
                 verification_job_id, verification_result_id, verification_result_status,
                 reconciliation_case_id, reconciliation_case_status,
                 report_id, report_idempotency_key,
                 submission_id, submission_idempotency_key,
                 metrics, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.hospital_id,
                run.source_type,
                run.trigger_ref,
                run.status.value,
                run.current_stage,
                run.error,
                run.error_class,
                run.created_at.isoformat(),
                run.updated_at.isoformat(),
                run.created_by,
                run.idempotency_key,
                run.snapshot_id,
                run.change_set_id,
                run.change_set_status,
                run.verification_job_id,
                run.verification_result_id,
                run.verification_result_status,
                run.reconciliation_case_id,
                run.reconciliation_case_status,
                run.report_id,
                run.report_idempotency_key,
                run.submission_id,
                run.submission_idempotency_key,
                json.dumps(run.metrics),
                _iso_or_none(run.completed_at),
            ),
        )
        self._connection.commit()
        return self.get_orchestration_run(run.run_id) or run

    def get_orchestration_run(self, run_id: str) -> OrchestrationRun | None:
        row = self._connection.execute(
            "SELECT * FROM orchestration_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return _orchestration_run_from_row(row)

    def get_orchestration_run_by_idempotency(
        self, idempotency_key: str
    ) -> OrchestrationRun | None:
        row = self._connection.execute(
            "SELECT * FROM orchestration_runs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return _orchestration_run_from_row(row)

    def update_orchestration_run(self, run: OrchestrationRun) -> OrchestrationRun | None:
        self._connection.execute(
            """
            UPDATE orchestration_runs
            SET status = ?, current_stage = ?, error = ?, error_class = ?,
                updated_at = ?, completed_at = ?,
                snapshot_id = ?, change_set_id = ?, change_set_status = ?,
                verification_job_id = ?, verification_result_id = ?, verification_result_status = ?,
                reconciliation_case_id = ?, reconciliation_case_status = ?,
                report_id = ?, report_idempotency_key = ?,
                submission_id = ?, submission_idempotency_key = ?,
                metrics = ?
            WHERE run_id = ?
            """,
            (
                run.status.value,
                run.current_stage,
                run.error,
                run.error_class,
                _utc_now_iso(),
                _iso_or_none(run.completed_at),
                run.snapshot_id,
                run.change_set_id,
                run.change_set_status,
                run.verification_job_id,
                run.verification_result_id,
                run.verification_result_status,
                run.reconciliation_case_id,
                run.reconciliation_case_status,
                run.report_id,
                run.report_idempotency_key,
                run.submission_id,
                run.submission_idempotency_key,
                json.dumps(run.metrics),
                run.run_id,
            ),
        )
        self._connection.commit()
        return self.get_orchestration_run(run.run_id)

    def list_orchestration_runs(
        self,
        *,
        hospital_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[OrchestrationRun]:
        clauses: list[str] = []
        params: list[Any] = []
        if hospital_id:
            clauses.append("hospital_id = ?")
            params.append(hospital_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"""
            SELECT * FROM orchestration_runs{where}
            ORDER BY created_at DESC, run_id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [_orchestration_run_from_row(row) for row in rows]


# --------------------------------------------------------------- helpers

def _submission_from_row(row: sqlite3.Row) -> Submission:
    return Submission(
        submission_id=row["submission_id"],
        report_id=row["report_id"],
        provider_type=NotificationProviderType(row["provider_type"]),
        idempotency_key=row["idempotency_key"],
        status=SubmissionStatus(row["status"]),
        submitted_at=_parse_iso(row["submitted_at"]) if row["submitted_at"] else None,
        external_reference=row["external_reference"],
        response_metadata=json.loads(row["response_metadata"])
        if row["response_metadata"]
        else {},
        error=row["error"],
        dry_run=bool(row["dry_run"]),
        created_by=row["created_by"],
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
    )


def _summary_from_row(row: sqlite3.Row) -> DatasetSummary:
    """Build a :class:`DatasetSummary` from a query row."""
    quality_records = 0
    try:
        payload = json.loads(row["payload"])
        quality_records = int(
            payload.get("quality", {}).get("records_extracted", 0) or 0
        )
    except (ValueError, TypeError):
        quality_records = 0
    return DatasetSummary(
        dataset_id=row["dataset_id"],
        hospital_ref=row["hospital_ref"],
        hospital_name=row["hospital_name"],
        hospital_key=row["hospital_key"],
        hospital_id=row["hospital_id"],
        source_type=row["source_type"],
        provider=row["provider"],
        created_at=_parse_iso(row["created_at"]),
        record_count=int(row["record_count"]),
        quality_records=quality_records,
    )


def _slug(value: str) -> str:
    """Lowercase alphanumeric slug used for stable hospital grouping."""
    slug = "".join(c if c.isalnum() else "_" for c in value.lower()).strip("_")
    return (slug or "unknown")[:64]


def _hospital_from_row(row: sqlite3.Row) -> HospitalSummary:
    return HospitalSummary(
        hospital_id=row["hospital_id"],
        name=row["name"],
        created_at=_parse_iso(row["created_at"]),
        dataset_count=int(row["dataset_count"]),
        snapshot_count=int(row["snapshot_count"]),
        source_count=int(row["source_count"]),
    )


def _snapshot_from_row(row: sqlite3.Row) -> DatasetSnapshot:
    snapshot = DatasetSnapshot.model_validate_json(row["payload"])
    snapshot.snapshot_id = row["snapshot_id"]
    return snapshot


def _snapshot_summary_from_row(row: sqlite3.Row) -> SnapshotSummary:
    snapshot = DatasetSnapshot.model_validate_json(row["payload"])
    dataset = snapshot.dataset
    record_count = 0
    quality_records = 0
    for category in CANONICAL_CATEGORIES:
        if category.key == "sources":
            continue
        record_count += len(_category_records(dataset, category.key))
        if dataset.quality is not None:
            quality_records = dataset.quality.records_extracted
    return SnapshotSummary(
        snapshot_id=row["snapshot_id"],
        hospital_id=row["hospital_id"],
        source_type=row["source_type"],
        provider=row["provider"],
        hospital_ref=row["hospital_ref"],
        hospital_name=snapshot.hospital_name,
        captured_at=_parse_iso(row["captured_at"]),
        content_hash=row["content_hash"],
        status=row["status"],
        record_count=record_count,
        quality_records=quality_records,
    )


def _category_records(dataset: HospitalDataset, key: str) -> list[Any]:
    if key == "hospital" and dataset.hospital is not None:
        return [dataset.hospital]
    records = getattr(dataset, key, [])
    return list(records) if isinstance(records, list) else []


def _orchestration_run_from_row(row: sqlite3.Row) -> OrchestrationRun:
    return OrchestrationRun(
        run_id=row["run_id"],
        hospital_id=row["hospital_id"],
        source_type=row["source_type"],
        trigger_ref=row["trigger_ref"],
        status=OrchestrationStatus(row["status"]),
        current_stage=row["current_stage"],
        error=row["error"],
        error_class=row["error_class"],
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
        completed_at=_parse_iso(row["completed_at"]) if row["completed_at"] else None,
        created_by=row["created_by"],
        snapshot_id=row["snapshot_id"],
        change_set_id=row["change_set_id"],
        change_set_status=row["change_set_status"],
        verification_job_id=row["verification_job_id"],
        verification_result_id=row["verification_result_id"],
        verification_result_status=row["verification_result_status"],
        reconciliation_case_id=row["reconciliation_case_id"],
        reconciliation_case_status=row["reconciliation_case_status"],
        report_id=row["report_id"],
        report_idempotency_key=row["report_idempotency_key"],
        submission_id=row["submission_id"],
        submission_idempotency_key=row["submission_idempotency_key"],
        metrics=json.loads(row["metrics"]) if row["metrics"] else {},
        idempotency_key=row["idempotency_key"],
    )


def _verification_job_from_row(row: sqlite3.Row) -> VerificationJob:
    """Build a job from a row: payload is the fixed truth, mutable status
    columns override it (claim/complete/requeue/fail write columns only)."""
    job = VerificationJob.model_validate_json(row["payload"])
    job.status = VerificationJobStatus(row["status"])
    job.scheduled_for = _parse_iso(row["scheduled_for"])
    job.started_at = _parse_iso(row["started_at"]) if row["started_at"] else None
    job.completed_at = _parse_iso(row["completed_at"]) if row["completed_at"] else None
    job.attempt_count = int(row["attempt_count"])
    job.last_error = row["last_error"]
    job.updated_at = _parse_iso(row["updated_at"])
    return job


def _parse_iso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
