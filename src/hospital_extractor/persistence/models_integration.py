"""ORM models for the hospital integration / verification subsystems.

Append-only companion to ``models.py`` and ``models_crawl.py`` (Milestone 3).
Implements the schema documented in ``docs/unified_architecture_phase1_domain.md``.

Immutability is an application-level invariant: snapshot, change, result, and
event rows are insert-only. Deletion is never cascaded; decommissioning is a
status/``enabled`` transition.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hospital_extractor.persistence.base import Base, TimestampMixin, new_uuid

JSONVariant = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def _json_default() -> dict[str, Any]:
    return {}


class IntegrationAuthMode(StrEnum):
    NONE = "none"
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"


class IntegrationStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    CONFIGURED = "configured"
    DISABLED = "disabled"
    ERROR = "error"


class SnapshotSourceKind(StrEnum):
    HOSPITAL_API = "hospital_api"
    MOCK_HOSPITAL_API_SIMULATED = "mock_hospital_api_simulated"


class ChangeSetStatus(StrEnum):
    INITIAL_SNAPSHOT = "initial_snapshot"
    NO_CHANGE = "no_change"
    CHANGES_DETECTED = "changes_detected"


class DetectedChangeType(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


class VerificationJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VerificationTriggerType(StrEnum):
    CHANGE_DETECTED = "change_detected"
    MANUAL = "manual"
    MONITORING = "monitoring"


class VerificationResultStatus(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ERROR = "error"


class MonitoringExecutionStatus(StrEnum):
    ENQUEUED = "enqueued"
    SKIPPED = "skipped"
    ERROR = "error"


class ReconciliationCaseStatus(StrEnum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ReconciliationResolution(StrEnum):
    HOSPITAL_SOURCE_CONFIRMED = "hospital_source_confirmed"
    WEBSITE_CONFIRMED = "website_confirmed"
    SOURCE_DISAGREEMENT = "source_disagreement"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FALSE_POSITIVE = "false_positive"


class HospitalSourceIntegration(TimestampMixin, Base):
    """Explicitly authorized, facility-scoped hospital-provided source."""

    __tablename__ = "hospital_source_integrations"
    __table_args__ = (
        UniqueConstraint(
            "facility_id",
            "name",
            name="uq_hospital_source_integrations_facility_id_name",
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in IntegrationStatus)})",
            name="status_valid",
        ),
        CheckConstraint(
            f"auth_mode IN ({', '.join(repr(v.value) for v in IntegrationAuthMode)})",
            name="auth_mode_valid",
        ),
        Index("ix_hospital_source_integrations_status", "status"),
        Index("ix_hospital_source_integrations_connector_type", "connector_type"),
        Index("ix_hospital_source_integrations_enabled", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_facilities.id",

            ondelete="RESTRICT",

            name="fk_hospital_source_integrations_facility_id",

        ),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default=IntegrationAuthMode.NONE.value
    )
    credential_env_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONVariant, nullable=False, default=_json_default
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=IntegrationStatus.NOT_CONFIGURED.value
    )
    enabled: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)

    events: Mapped[list[HospitalIntegrationEvent]] = relationship(
        back_populates="integration",
        lazy="selectin",
        order_by="HospitalIntegrationEvent.created_at",
    )


class HospitalIntegrationEvent(Base):
    """Immutable audit/config event for an integration. Insert-only."""

    __tablename__ = "hospital_integration_events"
    __table_args__ = (
        Index("ix_hospital_integration_events_integration_created", "integration_id", "created_at"),
        Index("ix_hospital_integration_events_facility_created", "facility_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_source_integrations.id",

            ondelete="RESTRICT",

            name="fk_hospital_integration_events_integration_id",

        ),
        nullable=False,
        index=True,
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_facilities.id",

            ondelete="RESTRICT",

            name="fk_hospital_integration_events_facility_id",

        ),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(
        JSONVariant, nullable=False, default=_json_default
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    integration: Mapped[HospitalSourceIntegration] = relationship(back_populates="events")


class HospitalDataSnapshot(Base):
    """Versioned, immutable capture of a hospital-provided dataset. Insert-only."""

    __tablename__ = "hospital_data_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "facility_id",
            "source_kind",
            "version_number",
            name="uq_hospital_data_snapshots_facility_source_version",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint(
            f"source_kind IN ({', '.join(repr(v.value) for v in SnapshotSourceKind)})",
            name="source_kind_valid",
        ),
        Index("ix_hospital_data_snapshots_facility_captured", "facility_id", "captured_at"),
        Index("ix_hospital_data_snapshots_integration_captured", "integration_id", "captured_at"),
        Index("ix_hospital_data_snapshots_content_hash", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_facilities.id",

            ondelete="RESTRICT",

            name="fk_hospital_data_snapshots_facility_id",

        ),
        nullable=False,
        index=True,
    )
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_source_integrations.id",

            ondelete="RESTRICT",

            name="fk_hospital_data_snapshots_integration_id",

        ),
        nullable=True,
        index=True,
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(
        JSONVariant, nullable=False, default=_json_default
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    is_simulation: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class HospitalSnapshotChangeSet(Base):
    """Immutable parent record of one deterministic snapshot comparison. Insert-only."""

    __tablename__ = "hospital_snapshot_change_sets"
    __table_args__ = (
        UniqueConstraint("snapshot_id", name="uq_hospital_snapshot_change_sets_snapshot_id"),
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in ChangeSetStatus)})",
            name="status_valid",
        ),
        Index("ix_hospital_snapshot_change_sets_facility_detected", "facility_id", "detected_at"),
        Index("ix_hospital_snapshot_change_sets_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_facilities.id",

            ondelete="RESTRICT",

            name="fk_hospital_snapshot_change_sets_facility_id",

        ),
        nullable=False,
        index=True,
    )
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_source_integrations.id",

            ondelete="RESTRICT",

            name="fk_hospital_snapshot_change_sets_integration_id",

        ),
        nullable=True,
        index=True,
    )
    previous_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_data_snapshots.id",

            ondelete="RESTRICT",

            name="fk_hospital_snapshot_change_sets_previous_snapshot_id",

        ),
        nullable=True,
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_data_snapshots.id",

            ondelete="RESTRICT",

            name="fk_hospital_snapshot_change_sets_snapshot_id",

        ),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    added_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    removed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    modified_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    changes: Mapped[list[HospitalDetectedChange]] = relationship(
        back_populates="change_set",
        lazy="selectin",
        order_by="HospitalDetectedChange.path",
    )


class HospitalDetectedChange(Base):
    """One immutable field-level difference within a change set. Insert-only."""

    __tablename__ = "hospital_detected_changes"
    __table_args__ = (
        UniqueConstraint(
            "change_set_id",
            "path",
            "change_type",
            "entity_name",
            name="uq_hospital_detected_changes_set_path_type_entity",
        ),
        CheckConstraint(
            f"change_type IN ({', '.join(repr(v.value) for v in DetectedChangeType)})",
            name="change_type_valid",
        ),
        Index("ix_hospital_detected_changes_facility_detected", "facility_id", "detected_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    change_set_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_snapshot_change_sets.id",

            ondelete="RESTRICT",

            name="fk_hospital_detected_changes_change_set_id",

        ),
        nullable=False,
        index=True,
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_facilities.id",

            ondelete="RESTRICT",

            name="fk_hospital_detected_changes_facility_id",

        ),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    field: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    old_value_json: Mapped[Any | None] = mapped_column(JSONVariant, nullable=True)
    new_value_json: Mapped[Any | None] = mapped_column(JSONVariant, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    change_set: Mapped[HospitalSnapshotChangeSet] = relationship(back_populates="changes")


class HospitalVerificationJob(TimestampMixin, Base):
    """Persistent verification work item claimed by a separate worker process."""

    __tablename__ = "hospital_verification_jobs"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_hospital_verification_jobs_idempotency_key"
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in VerificationJobStatus)})",
            name="status_valid",
        ),
        CheckConstraint(
            f"trigger_type IN ({', '.join(repr(v.value) for v in VerificationTriggerType)})",
            name="trigger_type_valid",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        Index("ix_hospital_verification_jobs_status_scheduled", "status", "scheduled_for"),
        Index("ix_hospital_verification_jobs_facility_created", "facility_id", "created_at"),
        Index("ix_hospital_verification_jobs_lease_expires_at", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_facilities.id",

            ondelete="RESTRICT",

            name="fk_hospital_verification_jobs_facility_id",

        ),
        nullable=False,
        index=True,
    )
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_source_integrations.id",

            ondelete="RESTRICT",

            name="fk_hospital_verification_jobs_integration_id",

        ),
        nullable=True,
        index=True,
    )
    change_set_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_snapshot_change_sets.id",

            ondelete="RESTRICT",

            name="fk_hospital_verification_jobs_change_set_id",

        ),
        nullable=True,
        index=True,
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_data_snapshots.id",

            ondelete="RESTRICT",

            name="fk_hospital_verification_jobs_snapshot_id",

        ),
        nullable=True,
        index=True,
    )
    target_ref: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=VerificationTriggerType.MANUAL.value
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=VerificationJobStatus.PENDING.value
    )
    idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(
        JSONVariant, nullable=False, default=_json_default
    )

    result: Mapped[HospitalVerificationResult | None] = relationship(
        back_populates="job", lazy="selectin", uselist=False
    )


class HospitalVerificationResult(Base):
    """Immutable, conservative outcome of one verification job. Insert-only.

    ``status`` is ``match``, ``mismatch`` or ``insufficient_evidence`` (or
    ``error`` when the check could not be performed). Absence of evidence is
    never reported as mismatch.
    """

    __tablename__ = "hospital_verification_results"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_hospital_verification_results_job_id"),
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in VerificationResultStatus)})",
            name="status_valid",
        ),
        Index("ix_hospital_verification_results_facility_compared", "facility_id", "compared_at"),
        Index("ix_hospital_verification_results_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_verification_jobs.id",

            ondelete="RESTRICT",

            name="fk_hospital_verification_results_job_id",

        ),
        nullable=False,
        index=True,
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_facilities.id",

            ondelete="RESTRICT",

            name="fk_hospital_verification_results_facility_id",

        ),
        nullable=False,
        index=True,
    )
    hospital_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_data_snapshots.id",

            ondelete="RESTRICT",

            name="fk_hospital_verification_results_hospital_snapshot_id",

        ),
        nullable=True,
    )
    website_crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_crawl_runs.id",

            ondelete="RESTRICT",

            name="fk_hospital_verification_results_website_crawl_run_id",

        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONVariant, nullable=False, default=_json_default
    )
    provenance_json: Mapped[dict[str, Any]] = mapped_column(
        JSONVariant, nullable=False, default=_json_default
    )
    compared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    job: Mapped[HospitalVerificationJob] = relationship(back_populates="result")


class HospitalMonitoringSchedule(TimestampMixin, Base):
    """Monitoring schedule; the scheduler creates jobs, it never crawls."""

    __tablename__ = "hospital_monitoring_schedules"
    __table_args__ = (
        UniqueConstraint(
            "facility_id",
            "integration_id",
            "name",
            name="uq_hospital_monitoring_schedules_facility_integration_name",
        ),
        CheckConstraint("interval_seconds >= 60", name="interval_seconds_minimum"),
        Index("ix_hospital_monitoring_schedules_enabled_next", "enabled", "next_run_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_facilities.id",

            ondelete="RESTRICT",

            name="fk_hospital_monitoring_schedules_facility_id",

        ),
        nullable=False,
        index=True,
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_source_integrations.id",

            ondelete="RESTRICT",

            name="fk_hospital_monitoring_schedules_integration_id",

        ),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_enqueued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONVariant, nullable=False, default=_json_default
    )


class HospitalMonitoringExecution(Base):
    """Immutable history of every monitoring-scheduler decision. Insert-only."""

    __tablename__ = "hospital_monitoring_executions"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in MonitoringExecutionStatus)})",
            name="status_valid",
        ),
        Index("ix_hospital_monitoring_executions_schedule_executed", "schedule_id", "executed_at"),
        Index("ix_hospital_monitoring_executions_facility_executed", "facility_id", "executed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_monitoring_schedules.id",

            ondelete="RESTRICT",

            name="fk_hospital_monitoring_executions_schedule_id",

        ),
        nullable=False,
        index=True,
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_facilities.id",

            ondelete="RESTRICT",

            name="fk_hospital_monitoring_executions_facility_id",

        ),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_verification_jobs.id",

            ondelete="RESTRICT",

            name="fk_hospital_monitoring_executions_job_id",

        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class HospitalReconciliationCase(TimestampMixin, Base):
    """Human-reviewable case for an inconclusive or contradicted verification.

    Resolving a case records a decision about the available evidence; it never
    publishes to the hospital or asserts which source is authoritative.
    """

    __tablename__ = "hospital_reconciliation_cases"
    __table_args__ = (
        UniqueConstraint(
            "verification_result_id",
            name="uq_hospital_reconciliation_cases_verification_result_id",
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in ReconciliationCaseStatus)})",
            name="status_valid",
        ),
        CheckConstraint(
            f"resolution IS NULL OR resolution IN "
            f"({', '.join(repr(v.value) for v in ReconciliationResolution)})",
            name="resolution_valid",
        ),
        Index("ix_hospital_reconciliation_cases_facility_status", "facility_id", "status"),
        Index("ix_hospital_reconciliation_cases_opened_at", "opened_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_facilities.id",

            ondelete="RESTRICT",

            name="fk_hospital_reconciliation_cases_facility_id",

        ),
        nullable=False,
        index=True,
    )
    verification_result_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_verification_results.id",

            ondelete="RESTRICT",

            name="fk_hospital_reconciliation_cases_verification_result_id",

        ),
        nullable=True,
        index=True,
    )
    detected_change_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_detected_changes.id",

            ondelete="RESTRICT",

            name="fk_hospital_reconciliation_cases_detected_change_id",

        ),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ReconciliationCaseStatus.OPEN.value
    )
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(
        JSONVariant, nullable=False, default=_json_default
    )

    events: Mapped[list[HospitalReconciliationCaseEvent]] = relationship(
        back_populates="case",
        lazy="selectin",
        order_by="HospitalReconciliationCaseEvent.occurred_at",
    )


class HospitalReconciliationCaseEvent(Base):
    """Immutable audit trail of reconciliation case transitions. Insert-only."""

    __tablename__ = "hospital_reconciliation_case_events"
    __table_args__ = (
        Index(
            "ix_hospital_reconciliation_case_events_case_occurred",
            "case_id",
            "occurred_at",
        ),
        Index(
            "ix_hospital_reconciliation_case_events_facility_occurred",
            "facility_id",
            "occurred_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_reconciliation_cases.id",

            ondelete="RESTRICT",

            name="fk_hospital_reconciliation_case_events_case_id",

        ),
        nullable=False,
        index=True,
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(

            "hospital_facilities.id",

            ondelete="RESTRICT",

            name="fk_hospital_reconciliation_case_events_facility_id",

        ),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    case: Mapped[HospitalReconciliationCase] = relationship(back_populates="events")
