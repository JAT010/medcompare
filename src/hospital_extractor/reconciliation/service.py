"""Reconciliation: human review of contradictory/inconclusive verifications.

Cases are created idempotently per verification result. Resolving a case
records a decision about the available evidence; nothing is published to the
hospital and no source is declared universally authoritative.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from hospital_extractor.persistence.models_integration import (
    HospitalReconciliationCase,
    HospitalReconciliationCaseEvent,
    HospitalVerificationResult,
    ReconciliationCaseStatus,
    ReconciliationResolution,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ReconciliationService",
    "ReconciliationError",
]

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    ReconciliationCaseStatus.OPEN.value: frozenset(
        {
            ReconciliationCaseStatus.UNDER_REVIEW.value,
            ReconciliationCaseStatus.RESOLVED.value,
            ReconciliationCaseStatus.DISMISSED.value,
        }
    ),
    ReconciliationCaseStatus.UNDER_REVIEW.value: frozenset(
        {
            ReconciliationCaseStatus.RESOLVED.value,
            ReconciliationCaseStatus.DISMISSED.value,
            ReconciliationCaseStatus.OPEN.value,
        }
    ),
    ReconciliationCaseStatus.RESOLVED.value: frozenset({ReconciliationCaseStatus.OPEN.value}),
    ReconciliationCaseStatus.DISMISSED.value: frozenset({ReconciliationCaseStatus.OPEN.value}),
}

_TRIGGER_FOR_RESULT = {
    "mismatch": "mismatch",
    "insufficient_evidence": "unverified",
    "error": "unverified",
}


class ReconciliationError(RuntimeError):
    """Invalid reconciliation operation."""


@dataclass(frozen=True)
class CaseTransition:
    case: HospitalReconciliationCase
    event: HospitalReconciliationCaseEvent


class ReconciliationService:
    """Case lifecycle with an append-only audit trail."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_case_for_result(
        self,
        result: HospitalVerificationResult,
        *,
        trigger: str | None = None,
        actor_label: str = "system",
    ) -> HospitalReconciliationCase | None:
        """Create (idempotently) a case for *result* when it needs review."""
        resolved, trigger_value = self._trigger(result, trigger)
        if not resolved:
            return None
        existing = self.session.scalars(
            select(HospitalReconciliationCase).where(
                HospitalReconciliationCase.verification_result_id == result.id
            )
        ).first()
        if existing is not None:
            return existing
        case = HospitalReconciliationCase(
            facility_id=result.facility_id,
            verification_result_id=result.id,
            status=ReconciliationCaseStatus.OPEN.value,
            resolution=None,
            summary=(
                f"Verification of hospital change against the website produced "
                f"'{result.status}'. Manual review is required."
            ),
            notes=None,
            opened_at=datetime.now(UTC),
            provenance_json={
                "verification_result_id": str(result.id),
                "job_id": str(result.job_id),
                "trigger": trigger_value,
                "hospital_snapshot_id": (
                    str(result.hospital_snapshot_id) if result.hospital_snapshot_id else None
                ),
                "website_crawl_run_id": (
                    str(result.website_crawl_run_id) if result.website_crawl_run_id else None
                ),
            },
        )
        self.session.add(case)
        self.session.flush()
        self._event(
            case,
            from_status=None,
            to_status=ReconciliationCaseStatus.OPEN.value,
            actor_label=actor_label,
            note=f"Case opened automatically from verification result ({trigger_value}).",
        )
        return case

    def list_cases(
        self,
        facility_id: uuid.UUID,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[HospitalReconciliationCase]:
        stmt = select(HospitalReconciliationCase).where(
            HospitalReconciliationCase.facility_id == facility_id
        )
        if status:
            stmt = stmt.where(HospitalReconciliationCase.status == status)
        stmt = (
            stmt.order_by(HospitalReconciliationCase.opened_at.desc()).limit(limit).offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def get_case(self, case_id: uuid.UUID) -> HospitalReconciliationCase | None:
        return self.session.get(HospitalReconciliationCase, case_id)

    def get_case_for_facility(
        self, case_id: uuid.UUID, facility_id: uuid.UUID
    ) -> HospitalReconciliationCase | None:
        case = self.get_case(case_id)
        if case is None or case.facility_id != facility_id:
            return None
        return case

    def transition(
        self,
        case: HospitalReconciliationCase,
        *,
        to_status: str,
        actor_label: str,
        note: str | None = None,
        resolution: str | None = None,
    ) -> CaseTransition:
        allowed = ALLOWED_TRANSITIONS.get(case.status, frozenset())
        if to_status not in allowed:
            raise ReconciliationError(
                f"cannot transition reconciliation case from {case.status!r} to {to_status!r}"
            )
        if to_status == ReconciliationCaseStatus.RESOLVED.value:
            if resolution is None:
                raise ReconciliationError("resolution is required when resolving a case")
            if resolution not in {r.value for r in ReconciliationResolution}:
                raise ReconciliationError(f"unknown resolution {resolution!r}")
        from_status = case.status
        case.status = to_status
        case.notes = note if note is not None else case.notes
        if to_status == ReconciliationCaseStatus.RESOLVED.value:
            case.resolution = resolution
            case.resolved_at = datetime.now(UTC)
            case.resolved_by_label = actor_label
        elif to_status == ReconciliationCaseStatus.OPEN.value:
            case.resolution = None
            case.resolved_at = None
            case.resolved_by_label = None
        event = self._event(
            case,
            from_status=from_status,
            to_status=to_status,
            actor_label=actor_label,
            note=note,
        )
        self.session.flush()
        return CaseTransition(case=case, event=event)

    def _event(
        self,
        case: HospitalReconciliationCase,
        *,
        from_status: str | None,
        to_status: str,
        actor_label: str | None,
        note: str | None,
    ) -> HospitalReconciliationCaseEvent:
        event = HospitalReconciliationCaseEvent(
            case_id=case.id,
            facility_id=case.facility_id,
            from_status=from_status,
            to_status=to_status,
            actor_label=actor_label,
            note=note,
            occurred_at=datetime.now(UTC),
        )
        self.session.add(event)
        self.session.flush()
        return event

    @staticmethod
    def _trigger(result: HospitalVerificationResult, trigger: str | None) -> tuple[bool, str]:
        if trigger:
            return True, trigger
        derived = _TRIGGER_FOR_RESULT.get(result.status)
        return (derived is not None), derived or ""


def trigger_for_status(result_status: str) -> str:
    return _TRIGGER_FOR_RESULT.get(result_status, "unverified")
