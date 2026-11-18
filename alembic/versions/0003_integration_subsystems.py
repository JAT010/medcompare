"""create hospital integration and verification subsystem tables

Revision ID: 0003_integration_subsystems
Revises: 0002_crawl_runs_evidence
Create Date: 2026-11-15 00:00:00.000000

Append-only migration. Does not alter 0001/0002. Tables implemented here:
hospital_source_integrations, hospital_integration_events,
hospital_data_snapshots, hospital_snapshot_change_sets,
hospital_detected_changes, hospital_verification_jobs,
hospital_verification_results, hospital_monitoring_schedules,
hospital_monitoring_executions, hospital_reconciliation_cases,
hospital_reconciliation_case_events.

All foreign keys use ON DELETE RESTRICT. Audit/history rows are immutable by
application convention. JSON columns are JSONB on PostgreSQL, JSON elsewhere.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_integration_subsystems"
down_revision: str | Sequence[str] | None = "0002_crawl_runs_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONVariant = sa.JSON().with_variant(postgresql.JSONB, "postgresql")

_INTEGRATION_STATUSES = "'not_configured', 'configured', 'disabled', 'error'"
_AUTH_MODES = "'none', 'api_key', 'bearer_token'"
_SOURCE_KINDS = "'hospital_api', 'mock_hospital_api_simulated'"
_CHANGE_SET_STATUSES = "'initial_snapshot', 'no_change', 'changes_detected'"
_CHANGE_TYPES = "'added', 'removed', 'modified'"
_JOB_STATUSES = (
    "'pending', 'running', 'verified', 'mismatch', 'insufficient_evidence', 'failed', 'cancelled'"
)
_TRIGGER_TYPES = "'change_detected', 'manual', 'monitoring'"
_RESULT_STATUSES = "'match', 'mismatch', 'insufficient_evidence', 'error'"
_EXECUTION_STATUSES = "'enqueued', 'skipped', 'error'"
_CASE_STATUSES = "'open', 'under_review', 'resolved', 'dismissed'"
_RESOLUTIONS = (
    "'hospital_source_confirmed', 'website_confirmed', 'source_disagreement', "
    "'insufficient_evidence', 'false_positive'"
)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.create_table(
        "hospital_source_integrations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("connector_type", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column(
            "auth_mode",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
        sa.Column("credential_env_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "config_json",
            JSONVariant,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="not_configured",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            f"status IN ({_INTEGRATION_STATUSES})",
            name="status_valid",
        ),
        sa.CheckConstraint(
            f"auth_mode IN ({_AUTH_MODES})", name="auth_mode_valid"
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_source_integrations_facility_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "facility_id",
            "name",
            name="uq_hospital_source_integrations_facility_id_name",
        ),
    )
    op.create_index(
        "ix_hospital_source_integrations_facility_id",
        "hospital_source_integrations",
        ["facility_id"],
    )
    op.create_index(
        "ix_hospital_source_integrations_status",
        "hospital_source_integrations",
        ["status"],
    )
    op.create_index(
        "ix_hospital_source_integrations_connector_type",
        "hospital_source_integrations",
        ["connector_type"],
    )
    op.create_index(
        "ix_hospital_source_integrations_enabled",
        "hospital_source_integrations",
        ["enabled"],
    )

    op.create_table(
        "hospital_integration_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("integration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "detail_json",
            JSONVariant,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["hospital_source_integrations.id"],
            name="fk_hospital_integration_events_integration_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_integration_events_facility_id",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_hospital_integration_events_integration_id",
        "hospital_integration_events",
        ["integration_id"],
    )
    op.create_index(
        "ix_hospital_integration_events_facility_id",
        "hospital_integration_events",
        ["facility_id"],
    )
    op.create_index(
        "ix_hospital_integration_events_integration_created",
        "hospital_integration_events",
        ["integration_id", "created_at"],
    )
    op.create_index(
        "ix_hospital_integration_events_facility_created",
        "hospital_integration_events",
        ["facility_id", "created_at"],
    )

    op.create_table(
        "hospital_data_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("integration_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("payload_json", JSONVariant, nullable=False),
        sa.Column(
            "provenance_json",
            JSONVariant,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_by", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("is_simulation", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created_at(),
        sa.CheckConstraint(
            "version_number >= 1", name="version_number_positive"
        ),
        sa.CheckConstraint(
            f"source_kind IN ({_SOURCE_KINDS})", name="source_kind_valid"
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_data_snapshots_facility_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["hospital_source_integrations.id"],
            name="fk_hospital_data_snapshots_integration_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "facility_id",
            "source_kind",
            "version_number",
            name="uq_hospital_data_snapshots_facility_source_version",
        ),
    )
    op.create_index(
        "ix_hospital_data_snapshots_facility_id",
        "hospital_data_snapshots",
        ["facility_id"],
    )
    op.create_index(
        "ix_hospital_data_snapshots_integration_id",
        "hospital_data_snapshots",
        ["integration_id"],
    )
    op.create_index(
        "ix_hospital_data_snapshots_facility_captured",
        "hospital_data_snapshots",
        ["facility_id", "captured_at"],
    )
    op.create_index(
        "ix_hospital_data_snapshots_integration_captured",
        "hospital_data_snapshots",
        ["integration_id", "captured_at"],
    )
    op.create_index(
        "ix_hospital_data_snapshots_content_hash",
        "hospital_data_snapshots",
        ["content_hash"],
    )

    op.create_table(
        "hospital_snapshot_change_sets",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("integration_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("previous_snapshot_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("added_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("removed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("modified_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            f"status IN ({_CHANGE_SET_STATUSES})",
            name="status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_snapshot_change_sets_facility_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["hospital_source_integrations.id"],
            name="fk_hospital_snapshot_change_sets_integration_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_snapshot_id"],
            ["hospital_data_snapshots.id"],
            name="fk_hospital_snapshot_change_sets_previous_snapshot_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["hospital_data_snapshots.id"],
            name="fk_hospital_snapshot_change_sets_snapshot_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("snapshot_id", name="uq_hospital_snapshot_change_sets_snapshot_id"),
    )
    op.create_index(
        "ix_hospital_snapshot_change_sets_facility_id",
        "hospital_snapshot_change_sets",
        ["facility_id"],
    )
    op.create_index(
        "ix_hospital_snapshot_change_sets_integration_id",
        "hospital_snapshot_change_sets",
        ["integration_id"],
    )
    op.create_index(
        "ix_hospital_snapshot_change_sets_snapshot_id",
        "hospital_snapshot_change_sets",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_hospital_snapshot_change_sets_facility_detected",
        "hospital_snapshot_change_sets",
        ["facility_id", "detected_at"],
    )
    op.create_index(
        "ix_hospital_snapshot_change_sets_status",
        "hospital_snapshot_change_sets",
        ["status"],
    )

    op.create_table(
        "hospital_detected_changes",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("change_set_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("entity_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("field", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("old_value_json", JSONVariant, nullable=True),
        sa.Column("new_value_json", JSONVariant, nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            f"change_type IN ({_CHANGE_TYPES})",
            name="change_type_valid",
        ),
        sa.ForeignKeyConstraint(
            ["change_set_id"],
            ["hospital_snapshot_change_sets.id"],
            name="fk_hospital_detected_changes_change_set_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_detected_changes_facility_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "change_set_id",
            "path",
            "change_type",
            "entity_name",
            name="uq_hospital_detected_changes_set_path_type_entity",
        ),
    )
    op.create_index(
        "ix_hospital_detected_changes_change_set_id",
        "hospital_detected_changes",
        ["change_set_id"],
    )
    op.create_index(
        "ix_hospital_detected_changes_facility_id",
        "hospital_detected_changes",
        ["facility_id"],
    )
    op.create_index(
        "ix_hospital_detected_changes_facility_detected",
        "hospital_detected_changes",
        ["facility_id", "detected_at"],
    )

    op.create_table(
        "hospital_verification_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("integration_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("change_set_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("target_ref", sa.Text(), nullable=False),
        sa.Column(
            "trigger_type",
            sa.String(length=32),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("idempotency_key", sa.String(length=192), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "input_json",
            JSONVariant,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            f"status IN ({_JOB_STATUSES})", name="status_valid"
        ),
        sa.CheckConstraint(
            f"trigger_type IN ({_TRIGGER_TYPES})",
            name="trigger_type_valid",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="attempt_count_non_negative"
        ),
        sa.CheckConstraint(
            "max_attempts >= 1", name="max_attempts_positive"
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_verification_jobs_facility_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["hospital_source_integrations.id"],
            name="fk_hospital_verification_jobs_integration_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["change_set_id"],
            ["hospital_snapshot_change_sets.id"],
            name="fk_hospital_verification_jobs_change_set_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["hospital_data_snapshots.id"],
            name="fk_hospital_verification_jobs_snapshot_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_hospital_verification_jobs_idempotency_key",
        ),
    )
    op.create_index(
        "ix_hospital_verification_jobs_facility_id",
        "hospital_verification_jobs",
        ["facility_id"],
    )
    op.create_index(
        "ix_hospital_verification_jobs_integration_id",
        "hospital_verification_jobs",
        ["integration_id"],
    )
    op.create_index(
        "ix_hospital_verification_jobs_change_set_id",
        "hospital_verification_jobs",
        ["change_set_id"],
    )
    op.create_index(
        "ix_hospital_verification_jobs_snapshot_id",
        "hospital_verification_jobs",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_hospital_verification_jobs_status_scheduled",
        "hospital_verification_jobs",
        ["status", "scheduled_for"],
    )
    op.create_index(
        "ix_hospital_verification_jobs_facility_created",
        "hospital_verification_jobs",
        ["facility_id", "created_at"],
    )
    op.create_index(
        "ix_hospital_verification_jobs_lease_expires_at",
        "hospital_verification_jobs",
        ["lease_expires_at"],
    )

    op.create_table(
        "hospital_verification_results",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("hospital_snapshot_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("website_crawl_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "evidence_json",
            JSONVariant,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "provenance_json",
            JSONVariant,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("compared_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            f"status IN ({_RESULT_STATUSES})", name="status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["hospital_verification_jobs.id"],
            name="fk_hospital_verification_results_job_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_verification_results_facility_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_snapshot_id"],
            ["hospital_data_snapshots.id"],
            name="fk_hospital_verification_results_hospital_snapshot_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["website_crawl_run_id"],
            ["hospital_crawl_runs.id"],
            name="fk_hospital_verification_results_website_crawl_run_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("job_id", name="uq_hospital_verification_results_job_id"),
    )
    op.create_index(
        "ix_hospital_verification_results_job_id",
        "hospital_verification_results",
        ["job_id"],
    )
    op.create_index(
        "ix_hospital_verification_results_facility_id",
        "hospital_verification_results",
        ["facility_id"],
    )
    op.create_index(
        "ix_hospital_verification_results_facility_compared",
        "hospital_verification_results",
        ["facility_id", "compared_at"],
    )
    op.create_index(
        "ix_hospital_verification_results_status",
        "hospital_verification_results",
        ["status"],
    )

    op.create_table(
        "hospital_monitoring_schedules",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("integration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column(
            "config_json",
            JSONVariant,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "interval_seconds >= 60",
            name="interval_seconds_minimum",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_monitoring_schedules_facility_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["hospital_source_integrations.id"],
            name="fk_hospital_monitoring_schedules_integration_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "facility_id",
            "integration_id",
            "name",
            name="uq_hospital_monitoring_schedules_facility_integration_name",
        ),
    )
    op.create_index(
        "ix_hospital_monitoring_schedules_facility_id",
        "hospital_monitoring_schedules",
        ["facility_id"],
    )
    op.create_index(
        "ix_hospital_monitoring_schedules_integration_id",
        "hospital_monitoring_schedules",
        ["integration_id"],
    )
    op.create_index(
        "ix_hospital_monitoring_schedules_enabled_next",
        "hospital_monitoring_schedules",
        ["enabled", "next_run_at"],
    )

    op.create_table(
        "hospital_monitoring_executions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("schedule_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            f"status IN ({_EXECUTION_STATUSES})",
            name="status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["hospital_monitoring_schedules.id"],
            name="fk_hospital_monitoring_executions_schedule_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_monitoring_executions_facility_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["hospital_verification_jobs.id"],
            name="fk_hospital_monitoring_executions_job_id",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_hospital_monitoring_executions_schedule_id",
        "hospital_monitoring_executions",
        ["schedule_id"],
    )
    op.create_index(
        "ix_hospital_monitoring_executions_facility_id",
        "hospital_monitoring_executions",
        ["facility_id"],
    )
    op.create_index(
        "ix_hospital_monitoring_executions_schedule_executed",
        "hospital_monitoring_executions",
        ["schedule_id", "executed_at"],
    )
    op.create_index(
        "ix_hospital_monitoring_executions_facility_executed",
        "hospital_monitoring_executions",
        ["facility_id", "executed_at"],
    )

    op.create_table(
        "hospital_reconciliation_cases",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("verification_result_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("detected_change_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="open",
        ),
        sa.Column("resolution", sa.String(length=32), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_label", sa.String(length=128), nullable=True),
        sa.Column(
            "provenance_json",
            JSONVariant,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            f"status IN ({_CASE_STATUSES})", name="status_valid"
        ),
        sa.CheckConstraint(
            f"resolution IS NULL OR resolution IN ({_RESOLUTIONS})",
            name="resolution_valid",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_reconciliation_cases_facility_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verification_result_id"],
            ["hospital_verification_results.id"],
            name="fk_hospital_reconciliation_cases_verification_result_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["detected_change_id"],
            ["hospital_detected_changes.id"],
            name="fk_hospital_reconciliation_cases_detected_change_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "verification_result_id",
            name="uq_hospital_reconciliation_cases_verification_result_id",
        ),
    )
    op.create_index(
        "ix_hospital_reconciliation_cases_facility_id",
        "hospital_reconciliation_cases",
        ["facility_id"],
    )
    op.create_index(
        "ix_hospital_reconciliation_cases_verification_result_id",
        "hospital_reconciliation_cases",
        ["verification_result_id"],
    )
    op.create_index(
        "ix_hospital_reconciliation_cases_detected_change_id",
        "hospital_reconciliation_cases",
        ["detected_change_id"],
    )
    op.create_index(
        "ix_hospital_reconciliation_cases_facility_status",
        "hospital_reconciliation_cases",
        ["facility_id", "status"],
    )
    op.create_index(
        "ix_hospital_reconciliation_cases_opened_at",
        "hospital_reconciliation_cases",
        ["opened_at"],
    )

    op.create_table(
        "hospital_reconciliation_case_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("case_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor_label", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["hospital_reconciliation_cases.id"],
            name="fk_hospital_reconciliation_case_events_case_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_reconciliation_case_events_facility_id",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_hospital_reconciliation_case_events_case_id",
        "hospital_reconciliation_case_events",
        ["case_id"],
    )
    op.create_index(
        "ix_hospital_reconciliation_case_events_facility_id",
        "hospital_reconciliation_case_events",
        ["facility_id"],
    )
    op.create_index(
        "ix_hospital_reconciliation_case_events_case_occurred",
        "hospital_reconciliation_case_events",
        ["case_id", "occurred_at"],
    )
    op.create_index(
        "ix_hospital_reconciliation_case_events_facility_occurred",
        "hospital_reconciliation_case_events",
        ["facility_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hospital_reconciliation_case_events_facility_occurred",
        table_name="hospital_reconciliation_case_events",
    )
    op.drop_index(
        "ix_hospital_reconciliation_case_events_case_occurred",
        table_name="hospital_reconciliation_case_events",
    )
    op.drop_index(
        "ix_hospital_reconciliation_case_events_facility_id",
        table_name="hospital_reconciliation_case_events",
    )
    op.drop_index(
        "ix_hospital_reconciliation_case_events_case_id",
        table_name="hospital_reconciliation_case_events",
    )
    op.drop_table("hospital_reconciliation_case_events")
    op.drop_index(
        "ix_hospital_reconciliation_cases_opened_at",
        table_name="hospital_reconciliation_cases",
    )
    op.drop_index(
        "ix_hospital_reconciliation_cases_facility_status",
        table_name="hospital_reconciliation_cases",
    )
    op.drop_index(
        "ix_hospital_reconciliation_cases_detected_change_id",
        table_name="hospital_reconciliation_cases",
    )
    op.drop_index(
        "ix_hospital_reconciliation_cases_verification_result_id",
        table_name="hospital_reconciliation_cases",
    )
    op.drop_index(
        "ix_hospital_reconciliation_cases_facility_id",
        table_name="hospital_reconciliation_cases",
    )
    op.drop_table("hospital_reconciliation_cases")
    op.drop_index(
        "ix_hospital_monitoring_executions_facility_executed",
        table_name="hospital_monitoring_executions",
    )
    op.drop_index(
        "ix_hospital_monitoring_executions_schedule_executed",
        table_name="hospital_monitoring_executions",
    )
    op.drop_index(
        "ix_hospital_monitoring_executions_facility_id",
        table_name="hospital_monitoring_executions",
    )
    op.drop_index(
        "ix_hospital_monitoring_executions_schedule_id",
        table_name="hospital_monitoring_executions",
    )
    op.drop_table("hospital_monitoring_executions")
    op.drop_index(
        "ix_hospital_monitoring_schedules_enabled_next",
        table_name="hospital_monitoring_schedules",
    )
    op.drop_index(
        "ix_hospital_monitoring_schedules_integration_id",
        table_name="hospital_monitoring_schedules",
    )
    op.drop_index(
        "ix_hospital_monitoring_schedules_facility_id",
        table_name="hospital_monitoring_schedules",
    )
    op.drop_table("hospital_monitoring_schedules")
    op.drop_index(
        "ix_hospital_verification_results_status",
        table_name="hospital_verification_results",
    )
    op.drop_index(
        "ix_hospital_verification_results_facility_compared",
        table_name="hospital_verification_results",
    )
    op.drop_index(
        "ix_hospital_verification_results_facility_id",
        table_name="hospital_verification_results",
    )
    op.drop_index(
        "ix_hospital_verification_results_job_id",
        table_name="hospital_verification_results",
    )
    op.drop_table("hospital_verification_results")
    op.drop_index(
        "ix_hospital_verification_jobs_lease_expires_at",
        table_name="hospital_verification_jobs",
    )
    op.drop_index(
        "ix_hospital_verification_jobs_facility_created",
        table_name="hospital_verification_jobs",
    )
    op.drop_index(
        "ix_hospital_verification_jobs_status_scheduled",
        table_name="hospital_verification_jobs",
    )
    op.drop_index(
        "ix_hospital_verification_jobs_snapshot_id",
        table_name="hospital_verification_jobs",
    )
    op.drop_index(
        "ix_hospital_verification_jobs_change_set_id",
        table_name="hospital_verification_jobs",
    )
    op.drop_index(
        "ix_hospital_verification_jobs_integration_id",
        table_name="hospital_verification_jobs",
    )
    op.drop_index(
        "ix_hospital_verification_jobs_facility_id",
        table_name="hospital_verification_jobs",
    )
    op.drop_table("hospital_verification_jobs")
    op.drop_index(
        "ix_hospital_detected_changes_facility_detected",
        table_name="hospital_detected_changes",
    )
    op.drop_index(
        "ix_hospital_detected_changes_facility_id",
        table_name="hospital_detected_changes",
    )
    op.drop_index(
        "ix_hospital_detected_changes_change_set_id",
        table_name="hospital_detected_changes",
    )
    op.drop_table("hospital_detected_changes")
    op.drop_index(
        "ix_hospital_snapshot_change_sets_status",
        table_name="hospital_snapshot_change_sets",
    )
    op.drop_index(
        "ix_hospital_snapshot_change_sets_facility_detected",
        table_name="hospital_snapshot_change_sets",
    )
    op.drop_index(
        "ix_hospital_snapshot_change_sets_snapshot_id",
        table_name="hospital_snapshot_change_sets",
    )
    op.drop_index(
        "ix_hospital_snapshot_change_sets_integration_id",
        table_name="hospital_snapshot_change_sets",
    )
    op.drop_index(
        "ix_hospital_snapshot_change_sets_facility_id",
        table_name="hospital_snapshot_change_sets",
    )
    op.drop_table("hospital_snapshot_change_sets")
    op.drop_index(
        "ix_hospital_data_snapshots_content_hash",
        table_name="hospital_data_snapshots",
    )
    op.drop_index(
        "ix_hospital_data_snapshots_integration_captured",
        table_name="hospital_data_snapshots",
    )
    op.drop_index(
        "ix_hospital_data_snapshots_facility_captured",
        table_name="hospital_data_snapshots",
    )
    op.drop_index(
        "ix_hospital_data_snapshots_integration_id",
        table_name="hospital_data_snapshots",
    )
    op.drop_index(
        "ix_hospital_data_snapshots_facility_id",
        table_name="hospital_data_snapshots",
    )
    op.drop_table("hospital_data_snapshots")
    op.drop_index(
        "ix_hospital_integration_events_facility_created",
        table_name="hospital_integration_events",
    )
    op.drop_index(
        "ix_hospital_integration_events_integration_created",
        table_name="hospital_integration_events",
    )
    op.drop_index(
        "ix_hospital_integration_events_facility_id",
        table_name="hospital_integration_events",
    )
    op.drop_index(
        "ix_hospital_integration_events_integration_id",
        table_name="hospital_integration_events",
    )
    op.drop_table("hospital_integration_events")
    op.drop_index(
        "ix_hospital_source_integrations_enabled",
        table_name="hospital_source_integrations",
    )
    op.drop_index(
        "ix_hospital_source_integrations_connector_type",
        table_name="hospital_source_integrations",
    )
    op.drop_index(
        "ix_hospital_source_integrations_status",
        table_name="hospital_source_integrations",
    )
    op.drop_index(
        "ix_hospital_source_integrations_facility_id",
        table_name="hospital_source_integrations",
    )
    op.drop_table("hospital_source_integrations")
