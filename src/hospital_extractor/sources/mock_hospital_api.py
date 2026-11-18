"""Deterministic simulated hospital API for tests and demos.

Clearly identifies itself as SIMULATED — it never pretends to be a real
hospital integration and never performs network I/O. It still enforces the
authorization policy (fail closed) so the authorization boundary is exercised
exactly like a real connector.

Payload variants are fixed and versioned; ``variant="changed"`` returns a
small deterministic delta (bed capacity + one added procedure) so change
detection and verification can be demonstrated end to end without any real
hospital system.
"""

from __future__ import annotations

import json
from typing import Any

from hospital_extractor.sources.authorization import AuthorizationPolicy
from hospital_extractor.sources.base import (
    AcquisitionResult,
    ConnectorErrorCode,
    ConnectorUnauthorizedError,
    HospitalConnectorError,
)

SIMULATED_PROVIDER = "SIMULATED MedCompare mock hospital API (not a real hospital)"
SIMULATED_WARNING = (
    "Simulated hospital API response. Not from a real hospital; "
    "used for tests and demonstrations only."
)

_PAYLOAD_V1: dict[str, Any] = {
    "hospital": {
        "hospital_id": "mock-authorized-1",
        "name": "Mock Authorized Hospital",
        "hospital_type": "multi-speciality",
        "established_year": 2001,
        "status": "operational",
        "bed_count": 100,
        "doctor_count": 24,
        "staff_count": 120,
        "website": "https://mock-authorized.example.test",
    },
    "contact": {
        "address": "1 Mock Way",
        "city": "Testville",
        "state": "TS",
        "country": "Testland",
        "postal_code": "000000",
        "phone": "+1-000-000-0000",
        "email": "care@mock-authorized.example.test",
    },
    "infrastructure": {
        "total_beds": 100,
        "icu_beds": 12,
        "operation_theatres": 4,
        "critical_care_beds": 20,
    },
    "departments": [
        {"name": "Cardiology"},
        {"name": "Orthopaedics"},
    ],
    "procedures": [{"name": "Angioplasty"}],
    "accreditations": [{"name": "Mock Accreditation Board"}],
    "source": {
        "provider": SIMULATED_PROVIDER,
        "simulated": True,
    },
}

_PAYLOAD_CHANGED: dict[str, Any] = {
    **_PAYLOAD_V1,
    "hospital": {
        **_PAYLOAD_V1["hospital"],
        "bed_count": 120,
        "doctor_count": 25,
    },
    "infrastructure": {
        **_PAYLOAD_V1["infrastructure"],
        "total_beds": 120,
    },
    "procedures": [
        {"name": "Angioplasty"},
        {"name": "Robot-assisted surgery"},
    ],
    "source": {
        "provider": SIMULATED_PROVIDER,
        "simulated": True,
        "change_token": "v2",
    },
}

_PAYLOAD_CHANGED_BEDS: dict[str, Any] = {
    **_PAYLOAD_V1,
    "hospital": {
        **_PAYLOAD_V1["hospital"],
        "bed_count": 120,
    },
    "infrastructure": {
        **_PAYLOAD_V1["infrastructure"],
        "total_beds": 120,
    },
    "source": {
        "provider": SIMULATED_PROVIDER,
        "simulated": True,
        "change_token": "beds-only",
    },
}

VARIANTS: dict[str, dict[str, Any]] = {
    "default": _PAYLOAD_V1,
    "changed": _PAYLOAD_CHANGED,
    "changed_beds": _PAYLOAD_CHANGED_BEDS,
}

__all__ = [
    "MOCK_CONNECTOR_TYPE",
    "MockHospitalApiConnector",
    "SIMULATED_PROVIDER",
    "SIMULATED_WARNING",
    "VARIANTS",
]

MOCK_CONNECTOR_TYPE = "mock_hospital_api_simulated"


class MockHospitalApiConnector:
    """Deterministic simulated connector with fixed payload variants."""

    connector_type = MOCK_CONNECTOR_TYPE

    def __init__(
        self,
        hospital_ref: str = "mock-authorized-1",
        *,
        variant: str = "default",
        authorization: AuthorizationPolicy | None = None,
        provider: str | None = None,
    ) -> None:
        self.hospital_ref = hospital_ref
        self.variant = variant if variant in VARIANTS else "default"
        self.provider = provider or SIMULATED_PROVIDER
        self.authorization = authorization

    @property
    def authorization_status(self) -> str:
        if self.authorization is None:
            return "UNAUTHORIZED"
        return self.authorization.status

    @property
    def simulated(self) -> bool:
        return True

    def _request_url(self) -> str:
        return f"https://mock-authorized.example.test/v1/hospitals/{self.hospital_ref}"

    def _authorize(self, url: str) -> None:
        if self.authorization is None:
            raise ConnectorUnauthorizedError("simulated connector requires an authorization policy")
        decision = self.authorization.check_url(url)
        if not decision.allowed:
            if decision.code == "unauthorized":
                raise ConnectorUnauthorizedError(decision.reason)
            raise HospitalConnectorError(
                decision.reason,
                code=ConnectorErrorCode.UNAUTHORIZED,
                retryable=False,
            )

    async def acquire(self) -> AcquisitionResult:
        url = self._request_url()
        self._authorize(url)

        payload: dict[str, Any] = json.loads(json.dumps(VARIANTS[self.variant]))
        payload["source"]["variant"] = self.variant
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

        return AcquisitionResult.from_bytes(
            connector_type=self.connector_type,
            provider=self.provider,
            source_ref=self.hospital_ref,
            requested_url=url,
            body=body,
            status_code=200,
            content_type="application/json",
            payload=payload,
            evidence={
                "acquisition_method": "mock_authorized_api",
                "variant": self.variant,
                "authorization": self.authorization_status,
                "simulated": True,
            },
            warnings=[SIMULATED_WARNING],
            simulated=True,
        )
