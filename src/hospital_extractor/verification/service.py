"""Verification orchestration: compare hospital snapshots with website evidence.

Conservative semantics: ``match`` when every comparable website criterion agrees,
``mismatch`` when any comparable criterion disagrees, otherwise
``insufficient_evidence``. Absence of website evidence is never a mismatch.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hospital_extractor.persistence.models_integration import (
    HospitalDataSnapshot,
    HospitalSnapshotChangeSet,
    HospitalVerificationJob,
    HospitalVerificationResult,
    VerificationJobStatus,
    VerificationResultStatus,
)
from hospital_extractor.reconciliation.service import ReconciliationService
from hospital_extractor.verification.compare import build_verification
from hospital_extractor.verification.evidence import WebsiteEvidence

__all__ = [
    "VerificationService",
    "VerificationServiceError",
    "WebsiteEvidence",
    "job_status_for_result",
]

_RESULT_STATUS_TO_JOB = {
    VerificationResultStatus.MATCH.value: VerificationJobStatus.VERIFIED.value,
    VerificationResultStatus.MISMATCH.value: VerificationJobStatus.MISMATCH.value,
    VerificationResultStatus.INSUFFICIENT_EVIDENCE.value: (
        VerificationJobStatus.INSUFFICIENT_EVIDENCE.value
    ),
    VerificationResultStatus.ERROR.value: VerificationJobStatus.FAILED.value,
}

_COMPARED_NOTE = (
    "Deterministic comparison of detected changes against persisted website "
    "evidence. Absence of evidence is never treated as a mismatch."
)


def job_status_for_result(result_status: str) -> str:
    return _RESULT_STATUS_TO_JOB.get(result_status, VerificationJobStatus.FAILED.value)


class VerificationServiceError(RuntimeError):
    """Verification could not be performed."""


@dataclass(frozen=True)
class VerificationOutcome:
    result: HospitalVerificationResult | None
    job: HospitalVerificationJob
    reconciliation_case: Any | None


class VerificationService:
    """Persists verification results and drives the job to a terminal state."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def existing_result(self, job_id: uuid.UUID) -> HospitalVerificationResult | None:
        return self.session.scalars(
            select(HospitalVerificationResult).where(HospitalVerificationResult.job_id == job_id)
        ).first()

    def run(
        self,
        job: HospitalVerificationJob,
        *,
        website: WebsiteEvidence,
        compared_by: str = "system",
    ) -> VerificationOutcome:
        """Verify *job* against *website* evidence; idempotent per job."""
        existing = self.existing_result(job.id)
        if existing is not None:
            return VerificationOutcome(
                result=existing,
                job=job,
                reconciliation_case=None,
            )

        if job.status not in (
            VerificationJobStatus.RUNNING.value,
            VerificationJobStatus.PENDING.value,
        ):
            raise VerificationServiceError(
                f"job {job.id} is not claimable for verification (status {job.status!r})"
            )

        change_set = (
            self.session.get(HospitalSnapshotChangeSet, job.change_set_id)
            if job.change_set_id
            else None
        )
        snapshot = (
            self.session.get(HospitalDataSnapshot, job.snapshot_id) if job.snapshot_id else None
        )
        changes = self._changes(change_set)

        aggregate = build_verification(
            payload=snapshot.payload_json if snapshot else None,
            changes=changes,
            website_evidence=website.evidence,
            website_hospital_name=website.hospital_name,
            website_target_url=website.target_url,
            hospital_url=None,
        )

        result_status = self._result_status(aggregate.status, website)
        provenance = self._provenance(job, snapshot, change_set, website, compared_by)
        result = HospitalVerificationResult(
            job_id=job.id,
            facility_id=job.facility_id,
            hospital_snapshot_id=snapshot.id if snapshot else None,
            website_crawl_run_id=website.crawl_run_id,
            status=result_status,
            summary=self._summary(result_status, aggregate),
            evidence_json=aggregate.evidence_json(),
            provenance_json=provenance,
            compared_at=datetime.now(UTC),
        )
        self.session.add(result)
        self.session.flush()

        job.status = job_status_for_result(result_status)
        job.finished_at = datetime.now(UTC)
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error_code = None
        job.last_error_message = None
        self.session.flush()

        reconciliation_case = ReconciliationService(self.session).ensure_case_for_result(
            result,
            actor_label=compared_by,
        )
        return VerificationOutcome(
            result=result,
            job=job,
            reconciliation_case=reconciliation_case,
        )

    def _changes(
        self, change_set: HospitalSnapshotChangeSet | None
    ) -> list[tuple[str, str, str, str, Any, Any]]:
        if change_set is None:
            return []
        from hospital_extractor.persistence.models_integration import HospitalDetectedChange

        stmt = (
            select(HospitalDetectedChange)
            .where(HospitalDetectedChange.change_set_id == change_set.id)
            .order_by(HospitalDetectedChange.path)
        )
        return [
            (
                row.path,
                row.change_type,
                row.entity_name,
                row.field,
                row.old_value_json,
                row.new_value_json,
            )
            for row in self.session.scalars(stmt).all()
        ]

    @staticmethod
    def _result_status(aggregate_status: str, website: WebsiteEvidence) -> str:
        if (
            website.status in {"failed", "timed_out", "empty", "unavailable"}
            and not website.evidence
        ):
            if aggregate_status == "mismatch":
                # Website unusable; cannot claim a mismatch either.
                return VerificationResultStatus.INSUFFICIENT_EVIDENCE.value
        if aggregate_status == "match":
            return VerificationResultStatus.MATCH.value
        if aggregate_status == "mismatch":
            return VerificationResultStatus.MISMATCH.value
        return VerificationResultStatus.INSUFFICIENT_EVIDENCE.value

    def _summary(self, result_status: str, aggregate: Any) -> str:
        if result_status == VerificationResultStatus.MATCH.value:
            return (
                f"Website evidence agrees on {aggregate.matched} "
                f"evaluated change(s); {aggregate.insufficient} not evaluated."
            )
        if result_status == VerificationResultStatus.MISMATCH.value:
            return (
                f"Website evidence conflicts on {aggregate.mismatched} "
                f"evaluated change(s); {aggregate.matched} agree, "
                f"{aggregate.insufficient} not evaluated."
            )
        return (
            f"Insufficient website evidence to verify the detected change(s); "
            f"{aggregate.evaluated} of {aggregate.evaluated + aggregate.insufficient} "
            f"change(s) evaluated."
        )

    def _provenance(
        self,
        job: HospitalVerificationJob,
        snapshot: HospitalDataSnapshot | None,
        change_set: HospitalSnapshotChangeSet | None,
        website: WebsiteEvidence,
        compared_by: str,
    ) -> dict[str, Any]:
        return {
            "job_id": str(job.id),
            "trigger_type": job.trigger_type,
            "change_set_id": str(change_set.id) if change_set else None,
            "change_set_status": change_set.status if change_set else None,
            "detected_change_paths": [c[0] for c in self._changes(change_set)],
            "hospital_snapshot": {
                "id": str(snapshot.id) if snapshot else None,
                "source_kind": snapshot.source_kind if snapshot else None,
                "version_number": snapshot.version_number if snapshot else None,
                "content_hash": snapshot.content_hash if snapshot else None,
                "captured_at": snapshot.captured_at.isoformat() if snapshot else None,
                "is_simulation": snapshot.is_simulation if snapshot else None,
            }
            if snapshot
            else None,
            "website": {
                "status": website.status,
                "target_url": website.target_url,
                "crawl_run_id": str(website.crawl_run_id) if website.crawl_run_id else None,
                "hospital_name": website.hospital_name,
                "pages_fetched": website.pages_fetched,
                "retrieved_at": website.retrieved_at.isoformat() if website.retrieved_at else None,
                "evidence_items": len(website.evidence),
            },
            "compared_by": compared_by,
            "compared_at": datetime.now(UTC).isoformat(),
            "notes": _COMPARED_NOTE,
        }
