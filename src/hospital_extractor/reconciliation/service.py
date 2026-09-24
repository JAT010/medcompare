"""Reconciliation service: case creation, evidence, state transitions.

The service enforces the explicit state machine (``ALLOWED_TRANSITIONS``),
leaves an audit trail on every transition, and builds the evidence package for
a case from *existing* immutable snapshots — it never mutates or duplicates
historical data, and it never crawls or captures anything itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from hospital_extractor.models.change import ChangeSet
from hospital_extractor.models.snapshot import DatasetSnapshot
from hospital_extractor.models.verification import (
    ChangeVerification,
    VerificationOutcome,
    VerificationResult,
    VerificationResultStatus,
)
from hospital_extractor.reconciliation.models import (
    ALLOWED_TRANSITIONS,
    ReconciliationCase,
    ReconciliationCaseStatus,
    ReconciliationEvent,
    ReconciliationItem,
    ReconciliationTrigger,
    ResolutionType,
)
from hospital_extractor.reconciliation.settings import (
    ReconciliationSettings,
    reconciliation_settings,
)
from hospital_extractor.storage.database import HospitalDatabase

__all__ = [
    "ReconciliationStateError",
    "build_evidence_package",
    "create_cases_for_result",
    "create_manual_case",
    "dismiss_case",
    "get_case",
    "list_cases",
    "list_events",
    "reopen_case",
    "resolve_case",
    "start_review",
]

_MANUAL_ACTOR = "manual"
_SYSTEM_ACTOR = "system"


def _now() -> datetime:
    return datetime.now(UTC)


class ReconciliationStateError(RuntimeError):
    """A requested state transition is not allowed by the state machine."""

    def __init__(
        self,
        case_id: str,
        current: ReconciliationCaseStatus,
        requested: ReconciliationCaseStatus,
    ) -> None:
        self.case_id = case_id
        self.current = current
        self.requested = requested
        allowed = ", ".join(sorted(a.value for a in ALLOWED_TRANSITIONS[current]))
        super().__init__(
            f"invalid reconciliation transition {current} -> {requested} for {case_id}; "
            f"allowed: {allowed}"
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_case(db: HospitalDatabase, case_id: str) -> ReconciliationCase | None:
    return db.get_reconciliation_case(case_id)


def list_cases(
    db: HospitalDatabase,
    *,
    hospital_id: str | None = None,
    status: ReconciliationCaseStatus | str | None = None,
    limit: int = 100,
) -> list[ReconciliationCase]:
    return db.list_reconciliation_cases(
        hospital_id=hospital_id,
        status=status.value if isinstance(status, ReconciliationCaseStatus) else status,
        limit=limit,
    )


def list_events(db: HospitalDatabase, case_id: str) -> list[ReconciliationEvent]:
    return db.list_reconciliation_events(case_id)


# ---------------------------------------------------------------------------
# Case creation
# ---------------------------------------------------------------------------


def _build_items(
    change_set: ChangeSet | None, verifications: list[ChangeVerification]
) -> list[ReconciliationItem]:
    items: list[ReconciliationItem] = []
    changes = change_set.changes if change_set is not None else []
    for index, verification in enumerate(verifications):
        change = None
        for candidate in changes:
            if (
                candidate.change_type is verification.change_type
                and candidate.path == verification.path
                and (candidate.entity_name or "") == (verification.entity_name or "")
            ):
                change = candidate
                break
        items.append(
            ReconciliationItem(
                item_id=f"ri_{verification.change_type.value}_{index:03d}_{verification.path}",
                path=verification.path,
                category=verification.category,
                entity_key=verification.entity_name or "",
                field=change.field if change is not None else verification.path,
                change_type=verification.change_type,
                outcome=verification.outcome,
                previous_value=change.old_value if change is not None else None,
                authoritative_value=(change.new_value if change is not None else None),
                verification_value=verification.website_value,
            )
        )
    return items


def _new_case(
    result: VerificationResult,
    *,
    trigger: ReconciliationTrigger,
    actor: str,
    now: datetime,
    items: list[ReconciliationItem],
) -> ReconciliationCase:
    return ReconciliationCase(
        reconciliation_case_id=f"rec_{uuid.uuid4().hex[:12]}",
        hospital_id=result.hospital_id,
        change_set_id=result.change_set_id,
        verification_job_id=result.job_id,
        verification_result_id=result.verification_result_id,
        status=ReconciliationCaseStatus.OPEN,
        trigger=trigger,
        created_by=actor,
        created_at=now,
        updated_at=now,
        items=items,
    )


def create_cases_for_result(
    db: HospitalDatabase,
    result: VerificationResult,
    *,
    create_unverified: bool | None = None,
    actor: str | None = None,
    settings: ReconciliationSettings | None = None,
    now: datetime | None = None,
) -> list[ReconciliationCase]:
    """Open reconciliation cases for a verification result, idempotently.

    - ``MISMATCH``  always opens a case (evidence explicitly disagrees).
    - ``UNVERIFIED`` opens a case only when the opt-in policy is on
      (evidence is insufficient, not a disagreement).
    - ``VERIFIED`` / ``FAILED`` never open a case here.

    Keyed on ``verification_result_id`` in storage: processing the same result
    twice returns the existing case — never a duplicate.
    """
    settings = settings or reconciliation_settings()
    actor = actor or settings.default_actor
    timestamp = now or _now()

    if result.status is VerificationResultStatus.MISMATCH:
        trigger = ReconciliationTrigger.MISMATCH
        verifications = [
            change for change in result.change_results
            if change.outcome is VerificationOutcome.MISMATCH
        ]
    elif result.status is VerificationResultStatus.UNVERIFIED:
        create_unverified = (
            settings.create_cases_for_unverified
            if create_unverified is None
            else create_unverified
        )
        if not create_unverified:
            return []
        trigger = ReconciliationTrigger.UNVERIFIED
        verifications = [
            change for change in result.change_results
            if change.outcome is VerificationOutcome.UNVERIFIED
        ]
    else:
        return []

    existing = db.get_reconciliation_case_for_result(result.verification_result_id)
    if existing is not None:
        return [existing]

    change_set = db.get_change_set(result.change_set_id)
    case = _new_case(
        result,
        trigger=trigger,
        actor=actor,
        now=timestamp,
        items=_build_items(change_set, verifications),
    )
    created = db.create_reconciliation_case(case)
    db.append_reconciliation_event(
        ReconciliationEvent(
            event_id=f"rev_{uuid.uuid4().hex[:12]}",
            case_id=created.reconciliation_case_id,
            actor=actor,
            action="created",
            reason=_created_reason(result, trigger),
            from_status=None,
            to_status=ReconciliationCaseStatus.OPEN,
            created_at=timestamp,
        )
    )
    return [created]


def _created_reason(result: VerificationResult, trigger: ReconciliationTrigger) -> str:
    if trigger is ReconciliationTrigger.MISMATCH:
        return (
            f"verification result {result.verification_result_id} is MISMATCH: "
            f"{result.mismatch_count} change(s) contradicted by the target source"
        )
    return (
        f"verification result {result.verification_result_id} is UNVERIFIED: "
        f"target source could not confirm {result.unverified_count} change(s)"
    )


def create_manual_case(
    db: HospitalDatabase,
    hospital_id: str,
    *,
    job_id: str | None = None,
    actor: str = _MANUAL_ACTOR,
    now: datetime | None = None,
) -> tuple[ReconciliationCase | None, str]:
    """Explicit manual request to open a case for a verification result.

    Unlike automatic creation this also allows a ``VERIFIED`` result to become
    a reviewable case.  Idempotent: an existing case for the same result is
    returned.  Returns ``(None, reason)`` when there is nothing to review.
    """
    timestamp = now or _now()
    result: VerificationResult | None = None
    if job_id is not None:
        result = db.get_verification_result(job_id)
    else:
        result = db.get_latest_verification_result(hospital_id)
    if result is None:
        return None, "no verification result available to review"

    existing = db.get_reconciliation_case_for_result(result.verification_result_id)
    if existing is not None:
        return existing, ""

    change_set = db.get_change_set(result.change_set_id)
    case = _new_case(
        result,
        trigger=ReconciliationTrigger.MANUAL,
        actor=actor,
        now=timestamp,
        items=_build_items(change_set, result.change_results),
    )
    created = db.create_reconciliation_case(case)
    db.append_reconciliation_event(
        ReconciliationEvent(
            event_id=f"rev_{uuid.uuid4().hex[:12]}",
            case_id=created.reconciliation_case_id,
            actor=actor,
            action="created",
            reason=f"explicit manual review request for result {result.verification_result_id}",
            from_status=None,
            to_status=ReconciliationCaseStatus.OPEN,
            created_at=timestamp,
        )
    )
    return created, ""


# ---------------------------------------------------------------------------
# State transitions (audited)
# ---------------------------------------------------------------------------


def _transition(
    db: HospitalDatabase,
    case_id: str,
    *,
    to: ReconciliationCaseStatus,
    action: str,
    actor: str,
    reason: str | None,
    now: datetime,
    update: dict[str, object],
) -> ReconciliationCase:
    case = db.get_reconciliation_case(case_id)
    if case is None:
        raise ValueError(f"unknown reconciliation case {case_id!r}")
    allowed = ALLOWED_TRANSITIONS[case.status]
    if to not in allowed:
        raise ReconciliationStateError(case_id, case.status, to)

    changed = case.model_copy(
        update={
            "status": to,
            "updated_at": now,
            **update,
        }
    )
    db.update_reconciliation_case(changed)
    db.append_reconciliation_event(
        ReconciliationEvent(
            event_id=f"rev_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            actor=actor,
            action=action,
            reason=reason,
            from_status=case.status,
            to_status=to,
            created_at=now,
        )
    )
    return changed


def start_review(
    db: HospitalDatabase,
    case_id: str,
    *,
    actor: str = _MANUAL_ACTOR,
    assigned_to: str | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> ReconciliationCase:
    """OPEN / REOPENED -> UNDER_REVIEW (also REOPENED -> UNDER_REVIEW)."""
    updates: dict[str, object] = {}
    if assigned_to is not None:
        updates["assigned_to"] = assigned_to
    return _transition(
        db,
        case_id,
        to=ReconciliationCaseStatus.UNDER_REVIEW,
        action="review_started",
        actor=actor,
        reason=reason or (f"assigned to {assigned_to}" if assigned_to else ""),
        now=now or _now(),
        update=updates,
    )


def resolve_case(
    db: HospitalDatabase,
    case_id: str,
    *,
    resolution: ResolutionType | str,
    reason: str,
    actor: str = _MANUAL_ACTOR,
    now: datetime | None = None,
) -> ReconciliationCase:
    """OPEN / UNDER_REVIEW / REOPENED -> RESOLVED with a recorded decision."""
    if not reason or not reason.strip():
        raise ValueError("a resolution reason is required")
    try:
        resolution_enum = (
            resolution if isinstance(resolution, ResolutionType)
            else ResolutionType(str(resolution))
        )
    except ValueError as exc:
        valid = ", ".join(sorted(r.value for r in ResolutionType))
        raise ValueError(f"invalid resolution {resolution!r}; expected one of {valid}") from exc

    timestamp = now or _now()
    return _transition(
        db,
        case_id,
        to=ReconciliationCaseStatus.RESOLVED,
        action="resolved",
        actor=actor,
        reason=reason,
        now=timestamp,
        update={
            "resolution": resolution_enum,
            "resolution_reason": reason,
            "resolved_at": timestamp,
        },
    )


def dismiss_case(
    db: HospitalDatabase,
    case_id: str,
    *,
    reason: str,
    actor: str = _MANUAL_ACTOR,
    now: datetime | None = None,
) -> ReconciliationCase:
    """OPEN / UNDER_REVIEW / REOPENED -> DISMISSED (no action warranted)."""
    if not reason or not reason.strip():
        raise ValueError("a dismissal reason is required")
    timestamp = now or _now()
    return _transition(
        db,
        case_id,
        to=ReconciliationCaseStatus.DISMISSED,
        action="dismissed",
        actor=actor,
        reason=reason,
        now=timestamp,
        update={"resolution_reason": reason},
    )


def reopen_case(
    db: HospitalDatabase,
    case_id: str,
    *,
    reason: str,
    actor: str = _MANUAL_ACTOR,
    now: datetime | None = None,
) -> ReconciliationCase:
    """RESOLVED / DISMISSED -> REOPENED.  Never creates a new case.

    The previous decision is cleared (audit history preserves it) so the case
    can be reviewed again.
    """
    if not reason or not reason.strip():
        raise ValueError("a reopen reason is required")
    timestamp = now or _now()
    return _transition(
        db,
        case_id,
        to=ReconciliationCaseStatus.REOPENED,
        action="reopened",
        actor=actor,
        reason=reason,
        now=timestamp,
        update={
            "resolution": None,
            "resolution_reason": None,
            "resolved_at": None,
            "assigned_to": None,
        },
    )


# ---------------------------------------------------------------------------
# Evidence package
# ---------------------------------------------------------------------------


def _snapshot_reference(snapshot: DatasetSnapshot) -> dict[str, Any]:
    """A payloa-dfree reference to an immutable snapshot (never duplicated)."""
    return {
        "snapshot_id": snapshot.snapshot_id,
        "hospital_id": snapshot.hospital_id,
        "source_type": snapshot.source_type,
        "provider": snapshot.provider,
        "hospital_ref": snapshot.hospital_ref,
        "captured_at": snapshot.captured_at.isoformat(),
        "status": snapshot.status,
        "content_hash": snapshot.content_hash,
    }


def build_evidence_package(
    db: HospitalDatabase,
    case: ReconciliationCase,
) -> dict[str, Any]:
    """Everything a reviewer needs to judge the case from stored data.

    References existing immutable snapshots instead of copying their payloads;
    the field-level agreement/disagreement lives in ``case.items`` which are
    derived from the already-captured canonical values.
    """
    change_set = db.get_change_set(case.change_set_id)
    job = db.get_verification_job(case.verification_job_id)
    result = db.get_verification_result(case.verification_job_id)

    triggering_snapshot = (
        db.get_snapshot(change_set.snapshot_id) if change_set is not None else None
    )
    previous_snapshot = (
        db.get_snapshot(change_set.previous_snapshot_id)
        if change_set is not None and change_set.previous_snapshot_id is not None
        else None
    )
    verification_snapshot = (
        db.get_snapshot(result.verification_snapshot_id)
        if result is not None and result.verification_snapshot_id is not None
        else None
    )

    return {
        "case": case.model_dump(mode="json"),
        "change_set": (
            change_set.model_dump(mode="json") if change_set is not None else None
        ),
        "job": job.model_dump(mode="json") if job is not None else None,
        "result": result.model_dump(mode="json") if result is not None else None,
        "triggering_snapshot": (
            _snapshot_reference(triggering_snapshot)
            if triggering_snapshot is not None
            else None
        ),
        "previous_snapshot": (
            _snapshot_reference(previous_snapshot) if previous_snapshot is not None else None
        ),
        "verification_snapshot": (
            _snapshot_reference(verification_snapshot)
            if verification_snapshot is not None
            else None
        ),
    }