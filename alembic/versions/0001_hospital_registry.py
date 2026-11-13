"""create hospital registry tables

Revision ID: 0001_hospital_registry
Revises:
Create Date: 2026-11-10 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_hospital_registry"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hospital_organizations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_hospital_organizations_normalized_name",
        "hospital_organizations",
        ["normalized_name"],
        unique=False,
    )
    op.create_index(
        "ix_hospital_organizations_status",
        "hospital_organizations",
        ["status"],
        unique=False,
    )

    op.create_table(
        "hospital_facilities",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("normalized_canonical_url", sa.Text(), nullable=True),
        sa.Column("website_domain", sa.String(length=255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("state", sa.String(length=128), nullable=True),
        sa.Column("country", sa.String(length=128), nullable=True),
        sa.Column("postal_code", sa.String(length=32), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["hospital_organizations.id"],
            name="fk_hospital_facilities_organization_id_hospital_organizations",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "normalized_canonical_url",
            name="uq_hospital_facilities_normalized_canonical_url",
        ),
    )
    op.create_index(
        "ix_hospital_facilities_normalized_name",
        "hospital_facilities",
        ["normalized_name"],
        unique=False,
    )
    op.create_index(
        "ix_hospital_facilities_status",
        "hospital_facilities",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_hospital_facilities_organization_id",
        "hospital_facilities",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_hospital_facilities_org_name",
        "hospital_facilities",
        ["organization_id", "normalized_name"],
        unique=False,
    )
    op.create_index(
        "ix_hospital_facilities_city_state",
        "hospital_facilities",
        ["city", "state"],
        unique=False,
    )
    op.create_index(
        "ix_hospital_facilities_website_domain",
        "hospital_facilities",
        ["website_domain"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_hospital_facilities_website_domain", table_name="hospital_facilities")
    op.drop_index("ix_hospital_facilities_city_state", table_name="hospital_facilities")
    op.drop_index("ix_hospital_facilities_org_name", table_name="hospital_facilities")
    op.drop_index("ix_hospital_facilities_organization_id", table_name="hospital_facilities")
    op.drop_index("ix_hospital_facilities_status", table_name="hospital_facilities")
    op.drop_index("ix_hospital_facilities_normalized_name", table_name="hospital_facilities")
    op.drop_table("hospital_facilities")
    op.drop_index("ix_hospital_organizations_status", table_name="hospital_organizations")
    op.drop_index("ix_hospital_organizations_normalized_name", table_name="hospital_organizations")
    op.drop_table("hospital_organizations")
