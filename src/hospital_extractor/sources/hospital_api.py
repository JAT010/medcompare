"""Authorized hospital REST/JSON connector.

Explicitly authorized hospital-provided data sources only. The connector is
always constructed with an :class:`AuthorizationPolicy` (fail closed when not
authorized), never follows redirects, bounds the response size and timeout,
and resolves credentials from the environment at request time. Credentials
are never stored on the instance in a form that could be logged, and never
appear in results or errors.
"""

from __future__ import annotations

from typing import Any

import httpx

from hospital_extractor.sources.authorization import (
    AuthorizationPolicy,
    Resolver,
    check_destination,
    resolve_credential,
)
from hospital_extractor.sources.base import (
    AcquisitionResult,
    ConnectorErrorCode,
    ConnectorUnauthorizedError,
    HospitalConnectorError,
)

DEFAULT_ENDPOINT_TEMPLATE = "/hospitals/{hospital_ref}"
DEFAULT_MAX_RESPONSE_BYTES = 5_000_000

__all__ = ["HospitalRestJsonConnector"]


class HospitalRestJsonConnector:
    """Fetch one hospital record from an authorized JSON endpoint."""

    connector_type = "hospital_rest_json"

    def __init__(
        self,
        hospital_ref: str,
        *,
        base_url: str | None,
        authorization: AuthorizationPolicy,
        endpoint_template: str = DEFAULT_ENDPOINT_TEMPLATE,
        provider: str | None = None,
        client: httpx.AsyncClient | None = None,
        auth_header_name: str = "Authorization",
        auth_header_scheme: str = "Bearer",
        accept: str = "application/json",
        resolver: Resolver | None = None,
    ) -> None:
        self.hospital_ref = hospital_ref
        self.base_url = (base_url or "").rstrip("/")
        self.authorization = authorization
        self.endpoint_template = endpoint_template
        self.provider = provider
        self.auth_header_name = auth_header_name
        self.auth_header_scheme = auth_header_scheme
        self.accept = accept
        self._client = client
        self._resolver = resolver

    @property
    def authorization_status(self) -> str:
        return self.authorization.status

    def _request_url(self) -> str:
        if not self.base_url:
            raise HospitalConnectorError(
                "hospital_rest_json source requires a configured base_url",
                code=ConnectorErrorCode.INVALID_CONFIGURATION,
                retryable=False,
            )
        template = self.endpoint_template or DEFAULT_ENDPOINT_TEMPLATE
        endpoint = template.format(hospital_ref=self.hospital_ref)
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        return f"{self.base_url}{endpoint}"

    def _request_headers(self) -> dict[str, str]:
        headers = {"Accept": self.accept}
        if self.authorization.credential_ref is not None:
            secret = resolve_credential(self.authorization.credential_ref)
            if secret is None:
                raise HospitalConnectorError(
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
        url = self._request_url()
        decision = self.authorization.check_url(url)
        if not decision.allowed:
            if decision.code in {"unauthorized", "ssrf"}:
                raise ConnectorUnauthorizedError(decision.reason)
            raise HospitalConnectorError(
                decision.reason,
                code=ConnectorErrorCode.INVALID_CONFIGURATION,
                retryable=False,
            )
        # Validate the addresses the request will actually connect to, not
        # just the hostname string (DNS-rebinding protection).
        destination = check_destination(
            url,
            resolver=self._resolver,
            allow_private_hosts=self.authorization.allow_private_hosts,
        )
        if not destination.allowed:
            raise ConnectorUnauthorizedError(destination.reason)

        # Embedded userinfo is stripped by urlsplit; the request is made with
        # the configured credential_env_ref only.

        timeout = self.authorization.timeout_seconds
        max_bytes = self.authorization.max_response_bytes
        client = self._client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,  # never route through environment proxies
        )
        own_client = self._client is None
        try:
            try:
                response = await client.get(url, headers=self._request_headers())
            except httpx.TimeoutException as exc:
                raise HospitalConnectorError(
                    f"hospital API timed out after {timeout}s",
                    code=ConnectorErrorCode.TIMEOUT,
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise HospitalConnectorError(
                    "hospital API unreachable",
                    code=ConnectorErrorCode.NETWORK_ERROR,
                    retryable=True,
                ) from exc
        finally:
            if own_client:
                await client.aclose()

        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > max_bytes:
                raise HospitalConnectorError(
                    f"response exceeds size limit of {max_bytes} bytes",
                    code=ConnectorErrorCode.PAYLOAD_TOO_LARGE,
                    retryable=False,
                )

        if response.status_code in {401, 403}:
            raise ConnectorUnauthorizedError(
                f"hospital API returned HTTP {response.status_code} "
                "(credential rejected; check the authorized integration configuration)"
            )
        if response.status_code != 200:
            raise HospitalConnectorError(
                f"hospital API returned HTTP {response.status_code}",
                code=ConnectorErrorCode.HTTP_ERROR,
                retryable=response.status_code >= 500 or response.status_code == 429,
            )

        body = response.content
        if len(body) > max_bytes:
            raise HospitalConnectorError(
                f"response exceeds size limit of {max_bytes} bytes",
                code=ConnectorErrorCode.PAYLOAD_TOO_LARGE,
                retryable=False,
            )

        content_type = response.headers.get("content-type", "")
        if content_type:
            lowered = content_type.lower()
            if "json" not in lowered:
                raise HospitalConnectorError(
                    f"hospital API returned unexpected content type {content_type!r}",
                    code=ConnectorErrorCode.UNSUPPORTED_CONTENT,
                    retryable=False,
                )
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise HospitalConnectorError(
                "hospital API returned non-JSON response",
                code=ConnectorErrorCode.PARSE_ERROR,
                retryable=False,
            ) from exc
        if not isinstance(payload, dict):
            raise HospitalConnectorError(
                "hospital API JSON root must be an object",
                code=ConnectorErrorCode.VALIDATION_ERROR,
                retryable=False,
            )

        return AcquisitionResult.from_bytes(
            connector_type=self.connector_type,
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
            simulated=False,
        )
