"""Deterministic mock authorized connector for demos and E2E tests (P21).

Clearly identifies itself as MOCK/SIMULATED — never pretends to be a real
hospital integration.  Supports scripted scenarios: success, unchanged,
changed, unauthorized, and transient failure — all offline (mock transport
or in-process fixtures).  No real network I/O.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

import httpx

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.sources.acquisition import (
    AcquisitionResult,
    ConnectorError,
    ConnectorErrorCode,
    ConnectorUnauthorizedError,
)
from hospital_extractor.sources.api_mapping import map_api_payload_to_dataset
from hospital_extractor.sources.authorization import AuthorizationPolicy
from hospital_extractor.sources.base import HospitalDataSource, SourceFetchError
from hospital_extractor.sources.models import SourceIdentity, SourceType
from hospital_extractor.utils.hashing import sha256_bytes

__all__ = [
    "MOCK_SCENARIOS",
    "MockScenario",
    "MockAuthorizedApiConnector",
]


class MockScenario(StrEnum):
    """Scripted outcomes the mock authorized connector can simulate."""

    SUCCESS = "success"
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    UNAUTHORIZED = "unauthorized"
    TRANSIENT_FAILURE = "transient_failure"


MOCK_SCENARIOS: tuple[str, ...] = tuple(member.value for member in MockScenario)

_BASE_PAYLOAD: dict[str, Any] = {
    "hospital": {
        "hospital_id": "mock-authorized-1",
        "name": "Mock Authorized Hospital",
        "bed_count": 100,
        "doctor_count": 20,
        "status": "operational",
    },
    "contact": {"city": "Testville", "phone": "+1-000-000-0000"},
    "departments": [{"name": "Cardiology"}],
    "specialities": [{"name": "Cardiology"}],
    "doctors": [{"name": "Dr. Mock", "speciality": "Cardiology"}],
    "source": {
        "provider": "Mock Authorized Data Services (SIMULATED)",
        "simulated": True,
    },
}


class MockAuthorizedApiConnector(HospitalDataSource):
    """Offline authorized-API connector for demos and E2E tests.

    - ``scenario`` selects the scripted outcome (success / unchanged /
      changed / unauthorized / transient_failure).
    - When a ``client`` is supplied, responses are served through that mock
      transport (still no real network).
    - ``authorization`` is checked fail-closed before any transport call.
    - Provenance always includes ``SIMULATED / MOCK`` so UI and reports never
      imply a real hospital integration.
    """

    source_type = SourceType.HOSPITAL_API

    def __init__(
        self,
        hospital_ref: str = "mock-authorized-1",
        *,
        scenario: MockScenario | str = MockScenario.SUCCESS,
        provider: str | None = None,
        authorization: AuthorizationPolicy | None = None,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://mock-authorized.example.test",
        endpoint: str = "/v1/hospitals/{hospital_ref}",
        change_token: str = "v1",
    ) -> None:
        self.hospital_ref = hospital_ref
        self.scenario = MockScenario(scenario)
        self.provider = provider or "Mock Authorized Data Services (SIMULATED)"
        self.authorization = authorization
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint
        self.change_token = change_token
        self._client = client

    def identity(self) -> SourceIdentity:
        return SourceIdentity(hospital_ref=self.hospital_ref, hospital_name=None)

    @property
    def authorization_status(self) -> str:
        if self.authorization is None:
            return "UNAUTHORIZED"
        return self.authorization.status

    @property
    def simulated(self) -> bool:
        return True

    def _request_url(self) -> str:
        endpoint = self.endpoint.format(hospital_ref=self.hospital_ref)
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        return f"{self.base_url}{endpoint}"

    def _authorize(self, url: str) -> None:
        if self.scenario is MockScenario.UNAUTHORIZED:
            raise ConnectorUnauthorizedError(
                "mock scenario is unauthorized (fail closed; no external request)"
            )
        if self.authorization is None:
            raise ConnectorUnauthorizedError(
                "mock authorized connector has no authorization policy"
            )
        decision = self.authorization.check_url(url)
        if not decision.allowed:
            if decision.code == "unauthorized":
                raise ConnectorUnauthorizedError(decision.reason)
            raise ConnectorError(
                decision.reason,
                code=ConnectorErrorCode.UNAUTHORIZED,
                retryable=False,
            )

    def _payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(json.dumps(_BASE_PAYLOAD))  # deep copy
        hospital = payload.get("hospital")
        source = payload.get("source")
        if isinstance(hospital, dict) and isinstance(source, dict):
            if self.scenario is MockScenario.CHANGED:
                hospital["bed_count"] = 120
                hospital["doctor_count"] = 25
            source["change_token"] = self.change_token
            source["simulated"] = True
            source["provider"] = self.provider
        return payload

    async def acquire(self) -> AcquisitionResult:
        url = self._request_url()
        self._authorize(url)

        if self.scenario is MockScenario.TRANSIENT_FAILURE:
            raise ConnectorError(
                "mock transient network failure",
                code=ConnectorErrorCode.NETWORK_ERROR,
                retryable=True,
            )

        payload = self._payload()
        body = json.dumps(payload, sort_keys=True).encode("utf-8")

        status_code = 200
        if self._client is not None:
            try:
                response = await self._client.get(
                    url, headers={"Accept": "application/json"}
                )
            except httpx.HTTPError as exc:
                raise ConnectorError(
                    "mock transport error",
                    code=ConnectorErrorCode.NETWORK_ERROR,
                    retryable=True,
                ) from exc
            status_code = response.status_code
            if status_code != 200:
                raise ConnectorError(
                    f"mock API returned HTTP {status_code}",
                    code=ConnectorErrorCode.HTTP_ERROR,
                    retryable=status_code >= 500,
                )
            body = response.content
            try:
                payload = response.json()
            except ValueError as exc:
                raise ConnectorError(
                    "mock API returned non-JSON",
                    code=ConnectorErrorCode.PARSE_ERROR,
                    retryable=False,
                ) from exc

        return AcquisitionResult(
            source_type=SourceType.HOSPITAL_API,
            provider=self.provider,
            source_ref=self.hospital_ref,
            requested_url=url,
            captured_at=_utc_now(),
            status_code=status_code,
            content_type="application/json",
            content_hash=sha256_bytes(body),
            payload=payload,
            raw_size_bytes=len(body),
            evidence={
                "acquisition_method": "mock_authorized_api",
                "scenario": self.scenario.value,
                "authorization": self.authorization_status,
                "simulated": True,
            },
            simulated=True,
            warnings=["SIMULATED / MOCK DATA — not a real hospital integration"],
        )

    async def fetch(self) -> HospitalDataset:
        result = await self.acquire()
        try:
            dataset = map_api_payload_to_dataset(
                result.payload,
                endpoint=result.requested_url,
                source_type=SourceType.HOSPITAL_API,
                provider=self.provider,
            )
        except Exception as exc:  # MalformedPayloadError and validation
            raise ConnectorError(
                str(exc),
                code=ConnectorErrorCode.VALIDATION_ERROR,
                retryable=False,
            ) from exc
        if dataset.source is not None:
            dataset.source.notes = (
                "SIMULATED / MOCK DATA — not a real hospital integration. "
                f"scenario={self.scenario.value}"
            )
            dataset.source.raw_payload_metadata = {
                **dataset.source.raw_payload_metadata,
                "content_hash": result.content_hash,
                "scenario": self.scenario.value,
                "simulated": True,
            }
        return dataset


def _utc_now() -> Any:
    from hospital_extractor.models.base import utc_now

    return utc_now()


_ = SourceFetchError
