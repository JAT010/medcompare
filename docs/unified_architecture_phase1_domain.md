# Unified Architecture — Phase 1 Schema Design (Hospital Integration Subsystems)

Append-only design for the PostgreSQL subsystems added by migration
`0003_hospital_integration_subsystems`. It does not modify the M1–M3 models or
migrations. Terms "immutable" / "append-only" describe application-level
invariants backed by schema conventions (no cascade deletes, no update-on-touch
behavior); the database does not itself forbid `UPDATE` statements.

## Conventions

- Primary keys are application-generated UUIDv4 (`new_uuid()`), matching M1–M3.
- Timestamps are `TIMESTAMPTZ`, set application-side (`default=`) and via
  `server_default=now()`; `updated_at` advances on ORM updates only for
  workflow-state tables. Audit/history tables are never updated by the app.
- Structured payloads are stored as JSON. Column type is
  `JSONB` on PostgreSQL (via `JSON().with_variant(postgresql.JSONB, "postgresql")`),
  falling back to `JSON` elsewhere (e.g. SQLite in tests).
- Enumerations are `VARCHAR(...)` columns holding one of a fixed set of
  lowercase string values (same convention as `HospitalCrawlRun.status`); values
  are enforced by `CHECK` constraints for new tables.
- All foreign keys use `ON DELETE RESTRICT`. No cascade deletes anywhere in
  this subsystem: deleting a facility must never silently erase provenance or
  audit history. Decommissioning is expressed as `status`/`enabled` transitions,
  never as row removal.
- Facility identity is always `hospital_facilities.id`. The parent organization
  or a display name is never used as identity.
- Secrets are never stored. Integrations keep environment-variable *reference
  names* and non-secret options only; the secret value is resolved at request
  time by the connector and never persisted, logged, or returned.

## Entities and relationships

### `hospital_source_integrations` (facility-scoped connector configuration)
Mutable configuration/state row for one explicitly authorized hospital-provided
source for one facility.

- `id` UUID PK
- `facility_id` UUID NOT NULL → `hospital_facilities.id` RESTRICT, indexed
- `name` VARCHAR(255) NOT NULL (operator-facing label)
- `connector_type` VARCHAR(64) NOT NULL (e.g. `hospital_rest_json`,
  `mock_hospital_api_simulated`)
- `base_url` TEXT NULL (validated/allowlisted at the application layer; stored
  only as the configured root)
- `auth_mode` VARCHAR(32) NOT NULL DEFAULT `none`
  (`none` | `api_key` | `bearer_token`) — mode only, never credential value
- `credential_env_ref` VARCHAR(255) NULL (environment variable *name*; never the secret)
- `config_json` JSONB/JSON NOT NULL DEFAULT `{}` (non-secret mapping/options)
- `status` VARCHAR(32) NOT NULL DEFAULT `not_configured`
  (`not_configured` | `configured` | `disabled` | `error`) with CHECK
- `enabled` BOOLEAN NOT NULL DEFAULT FALSE
- timestamps

Constraints/indexes:
- UNIQUE (`facility_id`, `name`): one named configuration per facility
- INDEX (`facility_id`, `status`), (`connector_type`), (`enabled`)

Deletion policy: RESTRICT everywhere; rows are disabled, never hard-deleted by
the application.

### `hospital_integration_events` (immutable configuration/audit log)
One row per meaningful integration state transition (created, updated, enabled,
disabled, health check result, error). Written once, never updated.

- `id` UUID PK
- `integration_id` UUID NOT NULL → `hospital_source_integrations.id` RESTRICT
- `facility_id` UUID NOT NULL → `hospital_facilities.id` RESTRICT
- `event_type` VARCHAR(64) NOT NULL
- `detail_json` JSONB/JSON NOT NULL DEFAULT `{}`
- `created_at` (record time; the row is immutable)

Indexes: (`integration_id`, `created_at`), (`facility_id`, `created_at`).

### `hospital_data_snapshots` (versioned, immutable capture)
Immutable capture of a hospital-provided dataset at one point in time.

- `id` UUID PK
- `facility_id` UUID NOT NULL → `hospital_facilities.id` RESTRICT
- `integration_id` UUID NULL → `hospital_source_integrations.id` RESTRICT
- `source_kind` VARCHAR(32) NOT NULL (`hospital_api`, `mock_hospital_api_simulated`)
- `version_number` INTEGER NOT NULL CHECK `>= 1`
- `content_hash` VARCHAR(128) NOT NULL (semantic fingerprint)
- `payload_json` JSONB/JSON NOT NULL (normalized dataset/evidence, includes provenance)
- `provenance_json` JSONB/JSON NOT NULL DEFAULT `{}`
- `captured_at` TIMESTAMPTZ NOT NULL
- `captured_by` VARCHAR(64) NOT NULL DEFAULT `system` (label only, not an
  authenticated principal; the app performs no fake auth)
- `is_simulation` BOOLEAN NOT NULL DEFAULT TRUE (captures are simulated until a
  real authorized integration is configured)

Constraints/indexes:
- UNIQUE (`facility_id`, `source_kind`, `version_number`)
- INDEX (`facility_id`, `captured_at` DESC), (`integration_id`, `captured_at` DESC),
  (`content_hash`)

Deletion policy: RESTRICT. Never updated. `version_number` is assigned by the
capture service as `max(existing version_number for facility+source_kind) + 1`
inside the same transaction.

### `hospital_snapshot_change_sets` (immutable result of one comparison)
Parent record grouping field-level changes detected between two snapshots of the
same facility and source kind.

- `id` UUID PK
- `facility_id` UUID NOT NULL → `hospital_facilities.id` RESTRICT
- `previous_snapshot_id` UUID NULL → `hospital_data_snapshots.id` RESTRICT
- `snapshot_id` UUID NOT NULL → `hospital_data_snapshots.id` RESTRICT, UNIQUE
  (exactly one change set per snapshot)
- `integration_id` UUID NULL → `hospital_source_integrations.id` RESTRICT
- `status` VARCHAR(32) NOT NULL
  (`initial_snapshot` | `no_change` | `changes_detected`) with CHECK
- `added_count` / `removed_count` / `modified_count` INTEGER NOT NULL DEFAULT 0
- `detected_at` TIMESTAMPTZ NOT NULL

Indexes: (`facility_id`, `detected_at` DESC), (`status`).
Immutable; deleted never.

### `hospital_detected_changes` (immutable field-level audit)
One row per field-level difference belonging to a change set.

- `id` UUID PK
- `change_set_id` UUID NOT NULL → `hospital_snapshot_change_sets.id` RESTRICT
- `facility_id` UUID NOT NULL → `hospital_facilities.id` RESTRICT
- `path` VARCHAR(255) NOT NULL (e.g. `hospital.bed_count`, `procedures`)
- `change_type` VARCHAR(32) NOT NULL (`added` | `removed` | `modified`) with CHECK
- `entity_name` VARCHAR(255) NOT NULL DEFAULT `''` (collection member label; `''`
  for single-record categories)
- `field` VARCHAR(255) NOT NULL DEFAULT `''`
- `old_value_json` JSONB/JSON NULL
- `new_value_json` JSONB/JSON NULL
- `source_url` TEXT NULL
- `detected_at` TIMESTAMPTZ NOT NULL

Constraints/indexes:
- UNIQUE (`change_set_id`, `path`, `change_type`, `entity_name`)
- INDEX (`facility_id`, `detected_at` DESC), (`change_set_id`)

Immutable; never updated.

### `hospital_verification_jobs` (persistent work queue)
A verification is a request to compare a detected change against website
evidence. Claimed by a separate worker process, never by request handlers.

- `id` UUID PK
- `facility_id` UUID NOT NULL → `hospital_facilities.id` RESTRICT
- `integration_id` UUID NULL → `hospital_source_integrations.id` RESTRICT
- `change_set_id` UUID NULL → `hospital_snapshot_change_sets.id` RESTRICT
- `snapshot_id` UUID NULL → `hospital_data_snapshots.id` RESTRICT
  (triggering hospital snapshot to verify against)
- `target_ref` TEXT NOT NULL (e.g. facility canonical URL; the worker resolves
  and re-validates it, it is never trusted blindly)
- `trigger_type` VARCHAR(32) NOT NULL (`change_detected` | `manual` | `monitoring`) with CHECK
- `status` VARCHAR(32) NOT NULL
  (`pending` | `running` | `verified` | `mismatch` | `insufficient_evidence` |
  `failed` | `cancelled`) with CHECK
- `idempotency_key` VARCHAR(192) NOT NULL UNIQUE
- `attempt_count` INTEGER NOT NULL DEFAULT 0 CHECK `>= 0`
- `max_attempts` INTEGER NOT NULL DEFAULT 3 CHECK `>= 1`
- `scheduled_for` TIMESTAMPTZ NOT NULL
- `started_at` / `finished_at` TIMESTAMPTZ NULL
- `lease_owner` VARCHAR(128) NULL, `lease_expires_at` TIMESTAMPTZ NULL
- `last_error_code` VARCHAR(64) NULL, `last_error_message` TEXT NULL
- `input_json` JSONB/JSON NOT NULL DEFAULT `{}` (bounded refs; no secrets)
- `result_id` UUID NULL (set when a result is written; FK added to results table)
- timestamps

Constraints/indexes:
- UNIQUE (`change_set_id`, `target_ref`) where `change_set_id IS NOT NULL` is NOT
  used; idempotency is carried by the unique `idempotency_key` instead so that
  one `(change_set_id, target)` pair maps to one deterministic key.
- INDEX (`status`, `scheduled_for`) for the worker poll query,
  (`facility_id`, `created_at` DESC), (`lease_expires_at`) (stale lease sweep)

Claiming (Phase 5) uses `SELECT ... FOR UPDATE SKIP LOCKED` on
`(status, scheduled_for)` inside a transaction; leases expire and are
re-claimable; `attempt_count` bounds retries; idempotency_key prevents
duplicate jobs from repeated scheduling.

### `hospital_verification_results` (immutable outcome per job)
One immutable row per job completion. `status` is conservative:
`match` | `mismatch` | `insufficient_evidence` — absence of evidence is never
reported as `mismatch`. `job_id` is UNIQUE (at most one result per job).

- `id` UUID PK
- `job_id` UUID NOT NULL UNIQUE → `hospital_verification_jobs.id` RESTRICT
- `facility_id` UUID NOT NULL → `hospital_facilities.id` RESTRICT
- `hospital_snapshot_id` UUID NULL → `hospital_data_snapshots.id` RESTRICT
- `website_crawl_run_id` UUID NULL → `hospital_crawl_runs.id` RESTRICT
- `status` VARCHAR(32) NOT NULL (`match` | `mismatch` | `insufficient_evidence` | `error`) with CHECK
- `summary` TEXT NULL
- `evidence_json` JSONB/JSON NOT NULL DEFAULT `{}` (per-change verdicts)
- `provenance_json` JSONB/JSON NOT NULL DEFAULT `{}` (both sides: source kind,
  URLs, timestamps, snapshot/crawl ids, capture times)
- `compared_at` TIMESTAMPTZ NOT NULL
- timestamps

Indexes: (`facility_id`, `compared_at` DESC), (`status`). Immutable.

### `hospital_monitoring_schedules` (schedule configuration/state)
Continuous monitoring creates verification jobs; it never crawls directly.

- `id` UUID PK
- `facility_id` UUID NOT NULL → `hospital_facilities.id` RESTRICT
- `integration_id` UUID NOT NULL → `hospital_source_integrations.id` RESTRICT
- `name` VARCHAR(255) NOT NULL
- `interval_seconds` INTEGER NOT NULL CHECK `>= 60`
- `enabled` BOOLEAN NOT NULL DEFAULT FALSE
- `next_run_at` TIMESTAMPTZ NULL
- `last_enqueued_at` TIMESTAMPTZ NULL
- `last_status` VARCHAR(32) NULL
- `config_json` JSONB/JSON NOT NULL DEFAULT `{}`
- timestamps

Constraints/indexes: UNIQUE (`facility_id`, `integration_id`, `name`),
INDEX (`enabled`, `next_run_at`).

### `hospital_monitoring_executions` (immutable schedule-run history)
Append-only record of every scheduler decision (job enqueued, skipped because
disabled/not due, error). Rows are never updated.

- `id` UUID PK
- `schedule_id` UUID NOT NULL → `hospital_monitoring_schedules.id` RESTRICT
- `facility_id` UUID NOT NULL → `hospital_facilities.id` RESTRICT
- `job_id` UUID NULL → `hospital_verification_jobs.id` RESTRICT
- `status` VARCHAR(32) NOT NULL (`enqueued` | `skipped` | `error`) with CHECK
- `reason` VARCHAR(255) NULL
- `scheduled_for` TIMESTAMPTZ NULL
- `executed_at` TIMESTAMPTZ NOT NULL

Indexes: (`schedule_id`, `executed_at` DESC), (`facility_id`, `executed_at` DESC).

### `hospital_reconciliation_cases` (human review workflow)
Reviewable case linking an inconclusive/contradicted verification to its
provenance. Resolution is a decision about evidence; it never auto-publishes to
the hospital or asserts which source is correct.

- `id` UUID PK
- `facility_id` UUID NOT NULL → `hospital_facilities.id` RESTRICT
- `verification_result_id` UUID NULL → `hospital_verification_results.id` RESTRICT
- `detected_change_id` UUID NULL → `hospital_detected_changes.id` RESTRICT
- `status` VARCHAR(32) NOT NULL (`open` | `under_review` | `resolved` | `dismissed`) with CHECK
- `resolution` VARCHAR(32) NULL
  (`hospital_source_confirmed` | `website_confirmed` | `source_disagreement` |
  `insufficient_evidence` | `false_positive`) with CHECK when set
- `summary` TEXT NULL
- `notes` TEXT NULL
- `opened_at` TIMESTAMPTZ NOT NULL
- `resolved_at` TIMESTAMPTZ NULL
- `resolved_by_label` VARCHAR(128) NULL (free-text operator label; the app has
  no authentication system and does not pretend otherwise)
- `provenance_json` JSONB/JSON NOT NULL DEFAULT `{}`
- timestamps

Constraint: UNIQUE (`verification_result_id`) when set (one case per result).
Indexes: (`facility_id`, `status`), (`opened_at` DESC).

### `hospital_reconciliation_case_events` (immutable audit trail)
Every case transition writes one event; resolved cases never change silently.

- `id` UUID PK
- `case_id` UUID NOT NULL → `hospital_reconciliation_cases.id` RESTRICT
- `facility_id` UUID NOT NULL → `hospital_facilities.id` RESTRICT
- `from_status` VARCHAR(32) NULL
- `to_status` VARCHAR(32) NOT NULL
- `actor_label` VARCHAR(128) NULL
- `note` TEXT NULL
- `occurred_at` TIMESTAMPTZ NOT NULL

Index: (`case_id`, `occurred_at`).

## Cross-cutting

- **Immutability:** snapshots, change sets, detected changes, integration
  events, monitoring executions, results and case events are insert-only from
  the application's perspective. Workflow state (jobs, schedules, cases) may
  transition, and every case transition is mirrored into
  `hospital_reconciliation_case_events`.
- **Deletion:** all FKs `ON DELETE RESTRICT`; nothing cascades. Decommissioning
  is a status/`enabled=false` transition.
- **No fake auth:** `captured_by`, `actor_label`, `resolved_by_label`,
  `captured_by` are operator-entered labels with no security meaning. All
  sensitive integration/verification operations (creating/enabling an
  integration, triggering captures, resolving cases, enqueuing jobs) are kept
  disabled/undisclosed in the API until an explicit operator authorization gate
  exists (Phase 6); no endpoint trusts a caller-supplied user identity.
- **Provenance:** every verification result carries both sides — the hospital
  snapshot id/hash/capture time and the website crawl run id/url/retrieval
  time — so findings are attributable end to end.
- **Simulation honesty:** `is_simulation = true` on snapshots and the
  `mock_hospital_api_simulated` connector type make simulated data explicit in
  storage and API responses; nothing in this subsystem claims a live hospital
  connection.
