"""Generic authorized hospital API connector (P21).

Extends the existing :class:`HospitalAPIDataSource` with an explicit
authorization boundary, SSRF guard, environment-backed credentials, bounded
timeouts / response sizes, and a common :class:`AcquisitionResult`.

Direct construction without an ``authorization`` policy remains backward
compatible (used by unit tests with mock transports).  Sources built through
the registry always receive a policy — fail closed when not authorized.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.sources.acquisition import (
    AcquisitionResult,
    ConnectorError,
    ConnectorErrorCode,
    ConnectorUnauthorizedError,
)
from hospital_extractor.sources.api_mapping import (
    MalformedPayloadError,
    map_api_payload_to_dataset,
)
from hospital_extractor.sources.authorization import (
    AuthorizationPolicy,
    resolve_credential,
)
from hospital_extractor.sources.base import HospitalDataSource, SourceFetchError
from hospital_extractor.sources.models import SourceIdentity, SourceType

__all__ = ["DEFAULT_HOSPITAL_API_TIMEOUT", "HospitalAPIDataSource"]

DEFAULT_HOSPITAL_API_TIMEOUT = 30.0
DEFAULT_MAX_RESPONSE_BYTES = 5_000_000


class HospitalAPIDataSource(HospitalDataSource):
    """Fetch a hospital profile from an authorized JSON API endpoint.

    ``base_url`` + ``hospital_ref`` form the request URL using the classic
    ``GET {base_url}/hospital/{ref}`` contract; a custom ``endpoint_template``
    (format string with ``{hospital_ref}``) overrides it.

    Authorization: when ``authorization`` is a policy, every request is gated
    by :meth:`AuthorizationPolicy.check_url` **before** any socket is opened.
    Credentials arrive only as ``CredentialRef`` environment references and
    are attached as headers at request time — never stored on the instance in
    results, logs, or exceptions.
    """

    source_type = SourceType.HOSPITAL_API

    def __init__(
        self,
        hospital_ref: str,
        *,
        base_url: str | None = None,
        endpoint_template: str = "/hospital/{hospital_ref}",
        provider: str | None = None,
        headers: dict[str, str] | None = None,
        mapping: dict[str, object] | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_HOSPITAL_API_TIMEOUT,
        authorization: AuthorizationPolicy | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        auth_header_name: str = "Authorization",
        auth_header_scheme: str = "Bearer",
    ) -> None:
        self.hospital_ref = hospital_ref
        self.base_url = (base_url or "").rstrip("/")
        self.endpoint_template = endpoint_template
        self.provider = provider
        self.headers = dict(headers or {})
        self.mapping = dict(mapping or {})
        self.timeout = timeout
        self.authorization = authorization
        self.max_response_bytes = max_response_bytes
        self.auth_header_name = auth_header_name
        self.auth_header_scheme = auth_header_scheme
        self._client = client

    def identity(self) -> SourceIdentity:
        return SourceIdentity(hospital_ref=self.hospital_ref)

    @property
    def authorization_status(self) -> str:
        """``AUTHORIZED`` / ``UNAUTHORIZED`` / ``UNRESTRICTED`` for UI/CLI."""
        if self.authorization is None:
            return "UNRESTRICTED"
        return self.authorization.status

    def _request_url(self) -> str:
        if not self.base_url:
            raise ConnectorError(
                "hospital_api source requires a configured base_url",
                code=ConnectorErrorCode.INVALID_CONFIGURATION,
                retryable=False,
            )
        endpoint = self.endpoint_template.format(hospital_ref=self.hospital_ref)
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        return f"{self.base_url}{endpoint}"

    def _authorize(self, url: str) -> None:
        """Fail closed before any network I/O when a policy is configured."""
        if self.authorization is None:
            return
        decision = self.authorization.check_url(url)
        if not decision.allowed:
            if decision.code == "unauthorized":
                raise ConnectorUnauthorizedError(decision.reason)
            raise ConnectorError(
                decision.reason,
                code=(
                    ConnectorErrorCode.INVALID_CONFIGURATION
                    if decision.code == "invalid_configuration"
                    else ConnectorErrorCode.UNAUTHORIZED
                ),
                retryable=False,
            )

    def _request_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", **self.headers}
        if self.authorization is not None and self.authorization.credential_ref is not None:
            secret = resolve_credential(self.authorization.credential_ref)
            if secret is None:
                raise ConnectorError(
                    f"credential environment variable "
                    f"{self.authorization.credential_ref.env_var!r} is not set",
                    code=ConnectorErrorCode.INVALID_CONFIGURATION,
                    retryable=False,
                )
            if self.auth_header_scheme:
                headers[self.auth_header_name] = f"{self.auth_header_scheme} {secret}"
            else:
                headers[self.auth_header_name] = secret
        return headers

    async def acquire(self) -> AcquisitionResult:
        """Perform one authorized GET and return a typed acquisition result."""
        url = self._request_url()
        self._authorize(url)

        timeout = (
            self.authorization.timeout_seconds
            if self.authorization is not None
            else self.timeout
        )
        max_bytes = (
            self.authorization.max_response_bytes
            if self.authorization is not None
            else self.max_response_bytes
        )

        client = self._client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        own_client = self._client is None
        try:
            try:
                response = await client.get(url, headers=self._request_headers())
            except httpx.TimeoutException as exc:
                raise ConnectorError(
                    f"hospital API timed out after {timeout}s",
                    code=ConnectorErrorCode.TIMEOUT,
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise ConnectorError(
                    "hospital API unreachable",
                    code=ConnectorErrorCode.NETWORK_ERROR,
                    retryable=True,
                ) from exc
        finally:
            if own_client:
                await client.aclose()

        if response.status_code != 200:
            raise ConnectorError(
                f"hospital API returned HTTP {response.status_code}",
                code=ConnectorErrorCode.HTTP_ERROR,
                retryable=response.status_code >= 500,
            )

        body = response.content
        if len(body) > max_bytes:
            raise ConnectorError(
                f"response exceeds size limit of {max_bytes} bytes",
                code=ConnectorErrorCode.PAYLOAD_TOO_LARGE,
                retryable=False,
            )

        content_type = response.headers.get("content-type", "")
        try:
            payload: Any = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ConnectorError(
                "hospital API returned non-JSON response",
                code=ConnectorErrorCode.PARSE_ERROR,
                retryable=False,
            ) from exc
        if not isinstance(payload, dict):
            raise ConnectorError(
                "hospital API JSON root must be an object",
                code=ConnectorErrorCode.VALIDATION_ERROR,
                retryable=False,
            )

        return AcquisitionResult.from_bytes(
            source_type=SourceType.HOSPITAL_API,
            provider=self.provider,
            source_ref=self.hospital_ref,
            requested_url=url,
            body=body,
            status_code=response.status_code,
            content_type=content_type or None,
            payload=payload,
            evidence={
                "acquisition_method": "http_get",
                "endpoint": url,
                "authorization": self.authorization_status,
            },
        )

    async def fetch(self) -> HospitalDataset:
        result = await self.acquire()
        url = str(result.requested_url)
        try:
            dataset = map_api_payload_to_dataset(
                result.payload,
                endpoint=url,
                source_type=SourceType.HOSPITAL_API,
                provider=self.provider,
            )
        except MalformedPayloadError as exc:
            raise ConnectorError(
                str(exc),
                code=ConnectorErrorCode.VALIDATION_ERROR,
                retryable=False,
            ) from exc
        if dataset.source is not None:
            dataset.source.raw_payload_metadata = {
                **dataset.source.raw_payload_metadata,
                "content_hash": result.content_hash,
                "status_code": result.status_code,
                "acquisition_method": "http_get",
            }
        return dataset


# Re-export for callers that only need the base error type.
_ = SourceFetchError
