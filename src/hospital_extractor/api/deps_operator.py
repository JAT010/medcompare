"""Operator (admin) authorization gate for sensitive integration operations.

MedCompare has no user accounts, so operator operations are gated by a shared
server-side secret presented as an HTTP bearer token. When
``MEDCOMPARE_OPERATOR_API_KEY`` is not set, operator endpoints are closed
(503 ``operator_api_key_not_configured``). The token is compared in constant
time and is never logged.

This gate is for *operator* operations only (configuring/enabling an
authorized hospital integration, running captures, triggering or cancelling
verification jobs, resolving reconciliation cases and managing monitoring
schedules). Read endpoints follow the app's existing open-read posture.
"""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Header, HTTPException

__all__ = ["require_operator"]

_NOT_CONFIGURED_DETAIL = {
    "error": "operator_api_key_not_configured",
    "message": (
        "Sensitive hospital integration operations are disabled in this deployment "
        "because MEDCOMPARE_OPERATOR_API_KEY is not configured on the server."
    ),
    "required_integration": (
        "Set MEDCOMPARE_OPERATOR_API_KEY on the server and pass the same value as "
        "'Authorization: Bearer <key>' for operator operations."
    ),
}
_INVALID_DETAIL = {
    "error": "operator_token_invalid",
    "message": "The provided operator token is not valid for this deployment.",
}


async def require_operator(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Fail closed unless a valid operator bearer token is presented."""
    key = os.environ.get("MEDCOMPARE_OPERATOR_API_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail=_NOT_CONFIGURED_DETAIL)
    presented = (authorization or "").strip()
    expected = f"Bearer {key}"
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail=_INVALID_DETAIL)
    return None
