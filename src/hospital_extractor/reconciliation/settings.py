"""Runtime policy for the reconciliation workflow.

``create_cases_for_unverified`` defaults to ``False``: a MISMATCH always opens
a case, but an UNVERIFIED verdict (evidence insufficient — not a disagreement)
only opens one when the deployment opts in.  Env fallback
``RECONCILIATION_CREATE_UNVERIFIED_CASES``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

__all__ = ["ReconciliationSettings", "reconciliation_settings"]


@dataclass(frozen=True)
class ReconciliationSettings:
    """Tuning shared by the worker hook and the service layer."""

    create_cases_for_unverified: bool = False
    default_actor: str = "system"


def reconciliation_settings(**overrides: object) -> ReconciliationSettings:
    """Settings with environment-variable defaults, then keyword overrides."""
    settings = ReconciliationSettings(
        create_cases_for_unverified=_env_bool(
            "RECONCILIATION_CREATE_UNVERIFIED_CASES", False
        ),
    )
    return replace(settings, **overrides)  # type: ignore[arg-type]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}