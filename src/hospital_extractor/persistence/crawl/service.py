"""Crawl persistence service.

Follows existing session lifecycle: use injected Session, prefer flush(), do not
commit (request/session_scope owns transaction boundaries).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from hospital_extractor.comparison.schemas import EvidenceItem
from hospital_extractor.persistence.exceptions import (
    CrawlRunNotFoundError,
    CrawlRunOwnershipError,
)
from hospital_extractor.persistence.models_crawl import (
    CrawlRunStatus,
    HospitalCrawlEvidence,
    HospitalCrawlRun,
)
from hospital_extractor.persistence.registry.service import RegistryService

USABLE_STATUSES = {
    CrawlRunStatus.COMPLETED.value,
    CrawlRunStatus.COMPLETED_WITH_WARNINGS.value,
}


@dataclass(frozen=True)
class CrawlRunSnapshot:
    run: HospitalCrawlRun
    evidence: list[EvidenceItem]
    criteria_without_evidence: list[str]
    warnings: list[str]


def _dump_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _load_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def _coerce_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def evidence_to_model_items(
    run_id: uuid.UUID, items: Sequence[EvidenceItem]
) -> list[HospitalCrawlEvidence]:
    models: list[HospitalCrawlEvidence] = []
    for index, item in enumerate(items):
        pub = item.published_at
        published_dt = datetime.combine(pub, datetime.min.time(), tzinfo=UTC) if pub else None
        models.append(
            HospitalCrawlEvidence(
                crawl_run_id=run_id,
                sort_index=index,
                criterion_key=item.criterion_key,
                value=item.value,
                status=item.status,
                source=item.source,
                confidence=item.confidence,
                source_name=item.source_name,
                source_url=item.source_url,
                retrieved_at=item.retrieved_at,
                published_at=published_dt,
                note=item.note,
            )
        )
    return models


def model_to_evidence_items(run: HospitalCrawlRun) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for row in run.evidence_items:
        items.append(
            EvidenceItem(
                criterion_key=row.criterion_key,
                value=row.value,
                status=row.status,
                source=row.source,
                confidence=row.confidence,
                source_name=row.source_name,
                source_url=row.source_url,
                retrieved_at=row.retrieved_at,
                published_at=_coerce_date(row.published_at),
                note=row.note,
            )
        )
    return items


class CrawlPersistenceService:
    """Persist and retrieve crawl runs/evidence with server-side ownership checks."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def start_run(
        self,
        *,
        facility_id: uuid.UUID | None,
        comparison_subject_id: uuid.UUID | None,
        subject_name: str | None,
        subject_location: str | None,
        subject_website_url: str | None,
        target_url: str,
        requested_url: str,
        requested_max_pages: int | None,
        raw_artifact_ref: str | None = None,
    ) -> HospitalCrawlRun:
        if facility_id is not None:
            RegistryService(self.session).get_facility(facility_id)
            comparison_subject_id = None
        elif comparison_subject_id is None:
            raise ValueError("comparison_subject_id is required for unregistered crawl runs")

        run = HospitalCrawlRun(
            facility_id=facility_id,
            comparison_subject_id=comparison_subject_id,
            subject_name=subject_name,
            subject_location=subject_location,
            subject_website_url=subject_website_url,
            status=CrawlRunStatus.FAILED.value,
            execution_mode="synchronous",
            target_url=target_url,
            requested_url=requested_url,
            requested_max_pages=requested_max_pages,
            raw_artifact_ref=raw_artifact_ref,
            pages_fetched=0,
            usable_for_comparison=0,
            requested_at=datetime.now(UTC),
        )
        self.session.add(run)
        self.session.flush()
        return run

    def finalize_run(
        self,
        run: HospitalCrawlRun,
        *,
        status: CrawlRunStatus,
        evidence: Sequence[EvidenceItem],
        warnings: Sequence[str],
        criteria_without_evidence: Sequence[str],
        pages_fetched: int,
        hospital_name: str | None,
        canonical_url: str | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
        retrieved_at: datetime | None = None,
        raw_artifact_ref: str | None = None,
    ) -> HospitalCrawlRun:
        now = datetime.now(UTC)
        run.status = status.value
        run.pages_fetched = pages_fetched
        run.hospital_name = hospital_name
        run.canonical_url = canonical_url
        run.warnings_json = _dump_json(list(warnings))
        run.criteria_without_evidence_json = _dump_json(list(criteria_without_evidence))
        run.failure_code = failure_code
        run.failure_message = failure_message
        run.retrieved_at = retrieved_at or now
        run.completed_at = now
        if raw_artifact_ref is not None:
            run.raw_artifact_ref = raw_artifact_ref
        run.usable_for_comparison = 1 if self._is_usable(status, evidence) else 0

        run.evidence_items.clear()
        self.session.flush()
        for row in evidence_to_model_items(run.id, evidence):
            run.evidence_items.append(row)
        self.session.flush()
        return run

    @staticmethod
    def _is_usable(status: CrawlRunStatus, evidence: Sequence[EvidenceItem]) -> bool:
        """Latest usable snapshot policy.

        - FAILED, TIMED_OUT, EMPTY: never usable; never replace prior snapshot.
        - COMPLETED / COMPLETED_WITH_WARNINGS: usable only when valid normalized
          evidence exists. A completed crawl with warnings but evidence is usable.
          A completed/empty crawl with no evidence is not usable, so a prior
          successful snapshot with evidence remains selected by default.
        - Historical runs remain retrievable; explicit selection requires
          server-side ownership validation.
        """
        if status in {CrawlRunStatus.FAILED, CrawlRunStatus.TIMED_OUT, CrawlRunStatus.EMPTY}:
            return False
        if status in {CrawlRunStatus.COMPLETED, CrawlRunStatus.COMPLETED_WITH_WARNINGS}:
            return len(evidence) > 0
        return False

    def get_run(self, run_id: uuid.UUID) -> HospitalCrawlRun:
        run = self.session.get(HospitalCrawlRun, run_id)
        if run is None:
            raise CrawlRunNotFoundError(run_id)
        return run

    def get_run_for_facility(self, run_id: uuid.UUID, facility_id: uuid.UUID) -> HospitalCrawlRun:
        run = self.get_run(run_id)
        if run.facility_id != facility_id:
            raise CrawlRunOwnershipError(run_id, facility_id=facility_id)
        return run

    def get_run_for_subject(
        self, run_id: uuid.UUID, comparison_subject_id: uuid.UUID
    ) -> HospitalCrawlRun:
        run = self.get_run(run_id)
        if run.comparison_subject_id != comparison_subject_id or run.facility_id is not None:
            raise CrawlRunOwnershipError(run_id, comparison_subject_id=comparison_subject_id)
        return run

    def latest_usable_for_facility(self, facility_id: uuid.UUID) -> HospitalCrawlRun | None:
        stmt = (
            select(HospitalCrawlRun)
            .where(
                HospitalCrawlRun.facility_id == facility_id,
                HospitalCrawlRun.usable_for_comparison == 1,
                HospitalCrawlRun.status.in_(sorted(USABLE_STATUSES)),
            )
            .order_by(
                HospitalCrawlRun.completed_at.desc().nullslast(),
                HospitalCrawlRun.created_at.desc(),
            )
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def latest_usable_for_subject(
        self, comparison_subject_id: uuid.UUID
    ) -> HospitalCrawlRun | None:
        stmt = (
            select(HospitalCrawlRun)
            .where(
                HospitalCrawlRun.comparison_subject_id == comparison_subject_id,
                HospitalCrawlRun.facility_id.is_(None),
                HospitalCrawlRun.usable_for_comparison == 1,
                HospitalCrawlRun.status.in_(sorted(USABLE_STATUSES)),
            )
            .order_by(
                HospitalCrawlRun.completed_at.desc().nullslast(),
                HospitalCrawlRun.created_at.desc(),
            )
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def snapshot(self, run: HospitalCrawlRun) -> CrawlRunSnapshot:
        return CrawlRunSnapshot(
            run=run,
            evidence=model_to_evidence_items(run),
            criteria_without_evidence=_load_list(run.criteria_without_evidence_json),
            warnings=_load_list(run.warnings_json),
        )
