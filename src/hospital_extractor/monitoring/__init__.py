"""P20 continuous monitoring: scheduled capture policies + worker.

Policies are keyed by the canonical ``hospital_id`` (never a hospital name)
and carry a fixed UTC schedule (``hourly`` / ``daily`` / ``weekly``).  Each
due policy is claimed atomically and delegated to the existing P19-E
orchestration service under a deterministic idempotency key — monitoring
never reimplements capture, verification, reconciliation or reporting.

Crawling remains an explicit background operation: nothing in the web UI or
request path starts the monitoring worker.
"""

from __future__ import annotations

from hospital_extractor.monitoring.models import (
    MonitoringPolicy,
    MonitoringPolicyStatus,
    ScheduleFrequency,
)
from hospital_extractor.monitoring.schedule import next_run_at
from hospital_extractor.monitoring.service import (
    create_policy,
    list_policies,
    run_monitoring_cycle,
    run_policy_now,
    set_policy_enabled,
)
from hospital_extractor.monitoring.settings import (
    MonitoringSettings,
    monitoring_settings,
)

__all__ = [
    "MonitoringPolicy",
    "MonitoringPolicyStatus",
    "MonitoringSettings",
    "ScheduleFrequency",
    "create_policy",
    "list_policies",
    "monitoring_settings",
    "next_run_at",
    "run_monitoring_cycle",
    "run_policy_now",
    "set_policy_enabled",
]
