"""Demo (mock) hospital API adapter.

Consumes the :mod:`hospital_extractor.mockapi` service over HTTP and maps its
structured payload into the canonical :class:`HospitalDataset` using the exact
same boundary a future authorized hospital adapter will use.  The comparison
engine only ever sees canonical data.
"""

from __future__ import annotations

import httpx

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.sources.api_mapping import (
    MalformedPayloadError,
    map_api_payload_to_dataset,
)
from hospital_extractor.sources.base import HospitalDataSource, SourceFetchError
from hospital_extractor.sources.models import SourceIdentity, SourceType

__all__ = ["DEFAULT_MOCK_API_BASE_URL", "MockHospitalAPIDataSource"]

DEFAULT_MOCK_API_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_TIMEOUT_SECONDS = 15.0


class MockHospitalAPIDataSource(HospitalDataSource):
    """Fetches the fictional demo hospital from the local DEMO API."""

    source_type = SourceType.MOCK_API

    def __init__(
        self,
        hospital_id: str = "demo-northstar",
        *,
        base_url: str | None = None,
        endpoint: str | None = None,
        provider: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.hospital_id = hospital_id
        self.base_url = (base_url or DEFAULT_MOCK_API_BASE_URL).rstrip("/")
        self.endpoint = endpoint or f"/demo/hospital/{hospital_id}"
        if not self.endpoint.startswith("/"):
            self.endpoint = f"/{self.endpoint}"
        self.provider = provider
        self.timeout = timeout
        self._client = client

    def identity(self) -> SourceIdentity:
        return SourceIdentity(hospital_ref=self.hospital_id, hospital_name=None)

    async def fetch(self) -> HospitalDataset:
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        own_client = self._client is None
        try:
            response = await client.get(
                f"{self.base_url}{self.endpoint}",
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise SourceFetchError(f"demo API unreachable: {exc}") from exc
        finally:
            if own_client:
                await client.aclose()

        if response.status_code != 200:
            raise SourceFetchError(
                f"demo API returned HTTP {response.status_code} for {self.endpoint}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceFetchError("demo API returned non-JSON response") from exc

        try:
            dataset = map_api_payload_to_dataset(
                payload,
                endpoint=f"{self.base_url}{self.endpoint}",
                source_type=SourceType.MOCK_API,
                provider=self.provider,
            )
        except MalformedPayloadError as exc:
            raise SourceFetchError(str(exc)) from exc
        return dataset