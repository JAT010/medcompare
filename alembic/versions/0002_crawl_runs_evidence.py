"""create crawl run and evidence tables

Revision ID: 0002_crawl_runs_evidence
Revises: 0001_hospital_registry
Create Date: 2026-11-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_crawl_runs_evidence"
down_revision: str | Sequence[str] | None = "0001_hospital_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hospital_crawl_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("facility_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("comparison_subject_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("subject_name", sa.String(length=255), nullable=True),
        sa.Column("subject_location", sa.String(length=255), nullable=True),
        sa.Column("subject_website_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "execution_mode",
            sa.String(length=32),
            nullable=False,
            server_default="synchronous",
        ),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("hospital_name", sa.String(length=255), nullable=True),
        sa.Column("pages_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_max_pages", sa.Integer(), nullable=True),
        sa.Column("raw_artifact_ref", sa.String(length=255), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=True),
        sa.Column("criteria_without_evidence_json", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "usable_for_comparison",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["hospital_facilities.id"],
            name="fk_hospital_crawl_runs_facility_id_hospital_facilities",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_hospital_crawl_runs_facility_id", "hospital_crawl_runs", ["facility_id"]
    )
    op.create_index(
        "ix_hospital_crawl_runs_facility_created",
        "hospital_crawl_runs",
        ["facility_id", "created_at"],
    )
    op.create_index(
        "ix_hospital_crawl_runs_subject_created",
        "hospital_crawl_runs",
        ["comparison_subject_id", "created_at"],
    )
    op.create_index(
        "ix_hospital_crawl_runs_status_created",
        "hospital_crawl_runs",
        ["status", "created_at"],
    )
    op.create_index("ix_hospital_crawl_runs_status", "hospital_crawl_runs", ["status"])

    op.create_table(
        "hospital_crawl_evidence",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("crawl_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("sort_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("criterion_key", sa.String(length=80), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["hospital_crawl_runs.id"],
            name="fk_hospital_crawl_evidence_crawl_run_id_hospital_crawl_runs",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_hospital_crawl_evidence_crawl_run_id",
        "hospital_crawl_evidence",
        ["crawl_run_id"],
    )
    op.create_index(
        "ix_hospital_crawl_evidence_run_criterion",
        "hospital_crawl_evidence",
        ["crawl_run_id", "criterion_key"],
    )
    op.create_index(
        "ix_hospital_crawl_evidence_criterion_key",
        "hospital_crawl_evidence",
        ["criterion_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hospital_crawl_evidence_criterion_key", table_name="hospital_crawl_evidence"
    )
    op.drop_index(
        "ix_hospital_crawl_evidence_run_criterion", table_name="hospital_crawl_evidence"
    )
    op.drop_index(
        "ix_hospital_crawl_evidence_crawl_run_id", table_name="hospital_crawl_evidence"
    )
    op.drop_table("hospital_crawl_evidence")
    op.drop_index("ix_hospital_crawl_runs_status", table_name="hospital_crawl_runs")
    op.drop_index(
        "ix_hospital_crawl_runs_status_created", table_name="hospital_crawl_runs"
    )
    op.drop_index(
        "ix_hospital_crawl_runs_subject_created", table_name="hospital_crawl_runs"
    )
    op.drop_index(
        "ix_hospital_crawl_runs_facility_created", table_name="hospital_crawl_runs"
    )
    op.drop_index("ix_hospital_crawl_runs_facility_id", table_name="hospital_crawl_runs")
    op.drop_table("hospital_crawl_runs")
