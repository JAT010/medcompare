"""ORM models for hospital registry."""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hospital_extractor.persistence.base import Base, TimestampMixin, new_uuid


class OrganizationStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class FacilityStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class HospitalOrganization(TimestampMixin, Base):
    """Parent hospital organization/brand."""

    __tablename__ = "hospital_organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=OrganizationStatus.ACTIVE.value, index=True
    )

    facilities: Mapped[list[HospitalFacility]] = relationship(
        back_populates="organization", lazy="selectin"
    )


class HospitalFacility(TimestampMixin, Base):
    """Individual physical hospital facility with immutable identity."""

    __tablename__ = "hospital_facilities"
    __table_args__ = (
        UniqueConstraint(
            "normalized_canonical_url",
            name="uq_hospital_facilities_normalized_canonical_url",
        ),
        Index("ix_hospital_facilities_org_name", "organization_id", "normalized_name"),
        Index("ix_hospital_facilities_city_state", "city", "state"),
        Index("ix_hospital_facilities_website_domain", "website_domain"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("hospital_organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    website_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FacilityStatus.ACTIVE.value, index=True
    )

    organization: Mapped[HospitalOrganization | None] = relationship(back_populates="facilities")

# Crawl-run persistence models (Milestone 3)
from hospital_extractor.persistence.models_crawl import (  # noqa: E402,F401
    CrawlRunStatus,
    HospitalCrawlEvidence,
    HospitalCrawlRun,
)
