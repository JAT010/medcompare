"""Structured feed connector (P21) — local files + authorized remote feeds.

Local JSON feed files (the original path) require no network and no
authorization.  Remote ``http(s)`` feeds are gated by an explicit
:class:`AuthorizationPolicy` before any request — fail closed.

Parsing is separate from domain normalization: this module produces an
:class:`AcquisitionResult` (or rehydrates a canonical dataset for local
feeds); mapping into hospital entities stays in the shared pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.sources.acquisition import (
    AcquisitionResult,
    ConnectorError,
    ConnectorErrorCode,
    ConnectorUnauthorizedError,
)
from hospital_extractor.sources.authorization import (
    AuthorizationPolicy,
    resolve_credential,
)
from hospital_extractor.sources.base import (
    HospitalDataSource,
    SourceFetchError,
    apply_source_provenance,
)
from hospital_extractor.sources.models import SourceIdentity, SourceMetadata, SourceType
from hospital_extractor.storage import read_dataset
from hospital_extractor.utils.hashing import sha256_bytes

__all__ = ["JSONFeedDataSource"]

DEFAULT_FEED_TIMEOUT = 30.0
DEFAULT_MAX_FEED_BYTES = 5_000_000


class JSONFeedDataSource(HospitalDataSource):
    """Yields a canonical dataset from a local JSON feed or authorized URL.

    ``path`` may be a filesystem path (no network) or an ``http(s)`` URL
    (requires ``authorization`` with ``authorized=True`` and an allowlisted
    host).  Credentials, when present, are environment-backed references only.
    """

    source_type = SourceType.JSON_FEED

    def __init__(
        self,
        path: str,
        *,
        name: str | None = None,
        authorization: AuthorizationPolicy | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_FEED_TIMEOUT,
        max_response_bytes: int = DEFAULT_MAX_FEED_BYTES,
    ) -> None:
        self.raw_ref = path
        self.path = Path(path) if not _is_remote(path) else None
        self.feed_name = name or (self.path.stem if self.path else path)
        self.authorization = authorization
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._client = client

    @property
    def is_remote(self) -> bool:
        return _is_remote(self.raw_ref)

    def identity(self) -> SourceIdentity:
        return SourceIdentity(hospital_ref=self.raw_ref, hospital_name=None)

    @property
    def authorization_status(self) -> str:
        if not self.is_remote:
            return "LOCAL_FILE"
        if self.authorization is None:
            return "UNAUTHORIZED"
        return self.authorization.status

    async def acquire(self) -> AcquisitionResult:
        """Read the feed (local or authorized remote) into an acquisition result."""
        if not self.is_remote:
            return self._acquire_local()
        return await self._acquire_remote()

    def _acquire_local(self) -> AcquisitionResult:
        assert self.path is not None
        if not self.path.is_file():
            raise ConnectorError(
                f"feed file not found: {self.path}",
                code=ConnectorErrorCode.INVALID_CONFIGURATION,
                retryable=False,
            )
        body = self.path.read_bytes()
        if len(body) > self.max_response_bytes:
            raise ConnectorError(
                f"feed exceeds size limit of {self.max_response_bytes} bytes",
                code=ConnectorErrorCode.PAYLOAD_TOO_LARGE,
                retryable=False,
            )
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConnectorError(
                "feed is not valid UTF-8 text",
                code=ConnectorErrorCode.UNSUPPORTED_CONTENT,
                retryable=False,
            ) from exc
        try:
            payload: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConnectorError(
                f"feed is not valid JSON: {exc.msg}",
                code=ConnectorErrorCode.PARSE_ERROR,
                retryable=False,
            ) from exc
        return AcquisitionResult(
            source_type=SourceType.JSON_FEED,
            provider=str(self.path),
            source_ref=str(self.path),
            requested_url=str(self.path),
            captured_at=_utc_now(),
            status_code=None,
            content_type="application/json",
            content_hash=sha256_bytes(body),
            payload=payload,
            raw_size_bytes=len(body),
            evidence={"acquisition_method": "local_file", "path": str(self.path)},
        )

    async def _acquire_remote(self) -> AcquisitionResult:
        url = self.raw_ref
        if self.authorization is None or not self.authorization.authorized:
            raise ConnectorUnauthorizedError(
                "remote feed is not authorized (fail closed; no external request)"
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

        headers = {"Accept": "application/json"}
        if self.authorization.credential_ref is not None:
            secret = resolve_credential(self.authorization.credential_ref)
            if secret is None:
                raise ConnectorError(
                    f"credential environment variable "
                    f"{self.authorization.credential_ref.env_var!r} is not set",
                    code=ConnectorErrorCode.INVALID_CONFIGURATION,
                    retryable=False,
                )
            headers["Authorization"] = f"Bearer {secret}"

        timeout = self.authorization.timeout_seconds
        max_bytes = self.authorization.max_response_bytes
        client = self._client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        own_client = self._client is None
        try:
            try:
                response = await client.get(url, headers=headers)
            except httpx.TimeoutException as exc:
                raise ConnectorError(
                    f"feed request timed out after {timeout}s",
                    code=ConnectorErrorCode.TIMEOUT,
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise ConnectorError(
                    "feed endpoint unreachable",
                    code=ConnectorErrorCode.NETWORK_ERROR,
                    retryable=True,
                ) from exc
        finally:
            if own_client:
                await client.aclose()

        if response.status_code != 200:
            raise ConnectorError(
                f"feed returned HTTP {response.status_code}",
                code=ConnectorErrorCode.HTTP_ERROR,
                retryable=response.status_code >= 500,
            )
        body = response.content
        if len(body) > max_bytes:
            raise ConnectorError(
                f"feed exceeds size limit of {max_bytes} bytes",
                code=ConnectorErrorCode.PAYLOAD_TOO_LARGE,
                retryable=False,
            )
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ConnectorError(
                "feed returned non-JSON response",
                code=ConnectorErrorCode.PARSE_ERROR,
                retryable=False,
            ) from exc

        return AcquisitionResult(
            source_type=SourceType.JSON_FEED,
            provider=self.authorization.provider or None,
            source_ref=url,
            requested_url=url,
            captured_at=_utc_now(),
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            content_hash=sha256_bytes(body),
            payload=payload,
            raw_size_bytes=len(body),
            evidence={
                "acquisition_method": "http_get",
                "authorization": self.authorization_status,
            },
        )

    async def fetch(self) -> HospitalDataset:
        result = await self.acquire()
        payload = result.payload

        # Canonical-dataset feed (local file or remote returning a dataset dump).
        if isinstance(payload, dict) and _looks_like_canonical(payload):
            return self._dataset_from_canonical(payload, result)

        # Structured hospital feed: map through the shared API payload mapper.
        if isinstance(payload, dict) and "hospital" in payload:
            from hospital_extractor.sources.api_mapping import (
                MalformedPayloadError,
                map_api_payload_to_dataset,
            )

            try:
                dataset = map_api_payload_to_dataset(
                    payload,
                    endpoint=result.requested_url,
                    source_type=SourceType.JSON_FEED,
                    provider=result.provider,
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
                }
            return dataset

        raise ConnectorError(
            "feed payload is neither a canonical dataset nor a structured "
            "hospital object (missing 'hospital' key)",
            code=ConnectorErrorCode.VALIDATION_ERROR,
            retryable=False,
        )

    def _dataset_from_canonical(
        self, payload: dict[str, Any], result: AcquisitionResult
    ) -> HospitalDataset:
        if self.path is not None and self.path.is_file():
            dataset = read_dataset(self.path)
        else:
            try:
                dataset = HospitalDataset.model_validate(payload)
            except Exception as exc:  # noqa: BLE001 — pydantic ValidationError
                raise ConnectorError(
                    "feed payload failed canonical dataset validation",
                    code=ConnectorErrorCode.VALIDATION_ERROR,
                    retryable=False,
                ) from exc

        if dataset.source is None:
            metadata = SourceMetadata(
                source_type=SourceType.JSON_FEED,
                source_id=f"json_feed:{self.feed_name}",
                provider=result.provider or str(self.raw_ref),
                base_url=result.requested_url,
                endpoint=result.requested_url,
                hospital_ref=str(self.raw_ref),
                hospital_name=dataset.hospital.name if dataset.hospital else None,
                raw_payload_metadata={
                    "file": str(self.path) if self.path else self.raw_ref,
                    "content_hash": result.content_hash,
                },
                notes="Imported from a persisted canonical dataset feed.",
            )
            apply_source_provenance(dataset, metadata)
        else:
            payload_meta = dict(dataset.source.raw_payload_metadata)
            payload_meta["imported_via"] = "json_feed"
            payload_meta["feed_path"] = str(self.path) if self.path else self.raw_ref
            payload_meta["content_hash"] = result.content_hash
            dataset.source.raw_payload_metadata = payload_meta
            note = (
                "Re-imported from a persisted canonical dataset feed; "
                "original provenance preserved."
            )
            dataset.source.notes = (
                f"{dataset.source.notes}; {note}" if dataset.source.notes else note
            )
        return dataset


def _is_remote(ref: str) -> bool:
    scheme = urlparse(ref).scheme.lower()
    return scheme in {"http", "https"}


def _looks_like_canonical(payload: dict[str, Any]) -> bool:
    # Canonical dumps always carry entity source_url / quality; structured
    # hospital feeds share the ``hospital`` key but use the API mapper shape.
    if "quality" in payload:
        return True
    hospital = payload.get("hospital")
    return isinstance(hospital, dict) and bool(hospital.get("source_url"))


def _utc_now() -> Any:
    from hospital_extractor.models.base import utc_now

    return utc_now()


_ = SourceFetchError
