"""P19-C reconciliation workflow: human-reviewable decisions for verified changes.

A case is created when verification is inconclusive (MISMATCH always;
UNVERIFIED when the opt-in policy is on; any result on an explicit manual
request).  It references the existing ChangeSet, VerificationJob,
VerificationResult and the immutable snapshots involved — never duplicating
their data — and records a small explicit state machine plus an audit trail.

Note: the ``service`` module is intentionally NOT re-exported here: it imports
``storage.database`` while ``storage.database`` imports the reconcilis models,
so eager re-export would create a circular import.  Import the service module
explicitly (``from hospital_extractor.reconciliation.service import …``).
"""

from __future__ import annotations

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

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ReconciliationCase",
    "ReconciliationCaseStatus",
    "ReconciliationEvent",
    "ReconciliationItem",
    "ReconciliationSettings",
    "ReconciliationTrigger",
    "ResolutionType",
    "reconciliation_settings",
]