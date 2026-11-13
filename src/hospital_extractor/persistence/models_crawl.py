"""Crawl-run persistence for website extraction evidence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hospital_extractor.persistence.base import Base, TimestampMixin, new_uuid


class CrawlRunStatus(StrEnum):
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    EMPTY = "empty"


class HospitalCrawlRun(TimestampMixin, Base):
    """One synchronous website extraction attempt with persisted results."""

    __tablename__ = "hospital_crawl_runs"
    __table_args__ = (
        Index("ix_hospital_crawl_runs_facility_created", "facility_id", "created_at"),
        Index("ix_hospital_crawl_runs_subject_created", "comparison_subject_id", "created_at"),
        Index("ix_hospital_crawl_runs_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("hospital_facilities.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    comparison_subject_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    subject_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_website_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="synchronous")
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    hospital_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    pages_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_max_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_artifact_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    warnings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    criteria_without_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usable_for_comparison: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    evidence_items: Mapped[list[HospitalCrawlEvidence]] = relationship(
        back_populates="crawl_run",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="HospitalCrawlEvidence.sort_index",
    )


class HospitalCrawlEvidence(TimestampMixin, Base):
    """Normalized evidence extracted from one crawl run."""

    __tablename__ = "hospital_crawl_evidence"
    __table_args__ = (
        Index("ix_hospital_crawl_evidence_run_criterion", "crawl_run_id", "criterion_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)
    crawl_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("hospital_crawl_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    criterion_key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    crawl_run: Mapped[HospitalCrawlRun] = relationship(back_populates="evidence_items")
