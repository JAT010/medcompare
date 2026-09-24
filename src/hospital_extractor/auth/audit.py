"""Audit event emission helpers (P24)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from hospital_extractor.auth.models import AuditEvent, Principal
from hospital_extractor.auth.store import AuthStore

__all__ = ["AuditOutcome", "record_audit"]


class AuditOutcome:
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


def record_audit(
    store: AuthStore,
    *,
    action: str,
    outcome: str = AuditOutcome.SUCCESS,
    principal: Principal | None = None,
    actor_id: str = "",
    actor_role: str = "",
    resource_type: str = "",
    resource_id: str = "",
    hospital_id: str = "",
    correlation_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append one audit event. Never pass passwords/tokens in *metadata*."""
    if principal is not None:
        actor_id = principal.user_id
        actor_role = principal.role.value
    event = AuditEvent(
        audit_event_id=f"ae_{uuid4().hex[:16]}",
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        hospital_id=hospital_id,
        outcome=outcome,
        correlation_id=correlation_id,
        metadata=metadata or {},
    )
    return store.append_audit(event)
