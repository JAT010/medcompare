"""Typed acquisition results and connector errors (P21).

Connectors produce a common :class:`AcquisitionResult` that carries only what
downstream needs — never credentials, never unbounded raw bodies.  Errors are
typed so orchestration/monitoring can distinguish permanent configuration
problems from transient network failures without parsing message strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from hospital_extractor.models.base import utc_now
from hospital_extractor.sources.base import SourceFetchError
from hospital_extractor.sources.models import SourceType
from hospital_extractor.utils.hashing import sha256_bytes, sha256_text

__all__ = [
    "AcquisitionResult",
    "ConnectorError",
    "ConnectorErrorCode",
    "ConnectorUnauthorizedError",
]


class ConnectorErrorCode(StrEnum):
    """Stable machine-readable connector failure classes."""

    UNAUTHORIZED = "unauthorized"
    INVALID_CONFIGURATION = "invalid_configuration"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    UNSUPPORTED_CONTENT = "unsupported_content"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    PARSE_ERROR = "parse_error"
    VALIDATION_ERROR = "validation_error"


# Codes that must never be retried by monitoring / orchestration.
PERMANENT_CONNECTOR_CODES: frozenset[ConnectorErrorCode] = frozenset(
    {
        ConnectorErrorCode.UNAUTHORIZED,
        ConnectorErrorCode.INVALID_CONFIGURATION,
        ConnectorErrorCode.UNSUPPORTED_CONTENT,
        ConnectorErrorCode.VALIDATION_ERROR,
        ConnectorErrorCode.PARSE_ERROR,
        ConnectorErrorCode.PAYLOAD_TOO_LARGE,
    }
)


class ConnectorError(SourceFetchError):
    """Typed connector failure.

    Subclasses :class:`SourceFetchError` so every existing
    ``except SourceFetchError`` / ``HarvestError`` path keeps working, while
    callers that care can inspect :attr:`code` and :attr:`retryable`.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ConnectorErrorCode = ConnectorErrorCode.NETWORK_ERROR,
        retryable: bool | None = None,
    ) -> None:
        self.code = code
        self.retryable = (
            code not in PERMANENT_CONNECTOR_CODES if retryable is None else retryable
        )
        # Sanitized message only — never include headers, tokens, or bodies.
        super().__init__(f"[{code.value}] {message}")

    @property
    def permanent(self) -> bool:
        """True when the failure can never succeed without config changes."""
        return not self.retryable


class ConnectorUnauthorizedError(ConnectorError):
    """Authorization failed — fail closed before any external request."""

    def __init__(self, message: str = "source is not authorized") -> None:
        super().__init__(
            message,
            code=ConnectorErrorCode.UNAUTHORIZED,
            retryable=False,
        )


@dataclass(frozen=True)
class AcquisitionResult:
    """Source-agnostic result of one connector acquisition.

    Carries acquisition metadata + a bounded payload reference.  Credentials
    are never fields here; ``evidence`` is a small JSON-safe dict for
    provenance, not a raw response dump.
    """

    source_type: SourceType
    provider: str | None
    source_ref: str
    requested_url: str
    captured_at: datetime
    status_code: int | None = None
    content_type: str | None = None
    content_hash: str = ""
    payload: Any = None  # parsed JSON / text / structured feed — bounded
    raw_size_bytes: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    simulated: bool = False

    @classmethod
    def from_bytes(
        cls,
        *,
        source_type: SourceType,
        provider: str | None,
        source_ref: str,
        requested_url: str,
        body: bytes,
        status_code: int | None = None,
        content_type: str | None = None,
        payload: Any = None,
        evidence: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        simulated: bool = False,
        captured_at: datetime | None = None,
    ) -> AcquisitionResult:
        """Build a result, hashing the raw body for deterministic identity."""
        return cls(
            source_type=source_type,
            provider=provider,
            source_ref=source_ref,
            requested_url=requested_url,
            captured_at=captured_at or utc_now(),
            status_code=status_code,
            content_type=content_type,
            content_hash=sha256_bytes(body),
            payload=payload,
            raw_size_bytes=len(body),
            evidence=dict(evidence or {}),
            warnings=list(warnings or []),
            simulated=simulated,
        )

    @classmethod
    def from_text(
        cls,
        *,
        source_type: SourceType,
        provider: str | None,
        source_ref: str,
        requested_url: str,
        text: str,
        status_code: int | None = None,
        content_type: str | None = None,
        payload: Any = None,
        evidence: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        simulated: bool = False,
        captured_at: datetime | None = None,
    ) -> AcquisitionResult:
        """Build a result from decoded text (feeds, local files)."""
        body = text.encode("utf-8")
        return cls(
            source_type=source_type,
            provider=provider,
            source_ref=source_ref,
            requested_url=requested_url,
            captured_at=captured_at or utc_now(),
            status_code=status_code,
            content_type=content_type,
            content_hash=sha256_bytes(body),
            payload=payload if payload is not None else text,
            raw_size_bytes=len(body),
            evidence=dict(evidence or {}),
            warnings=list(warnings or []),
            simulated=simulated,
        )

    @classmethod
    def empty(
        cls,
        *,
        source_type: SourceType,
        source_ref: str,
        requested_url: str = "",
        provider: str | None = None,
    ) -> AcquisitionResult:
        """A deterministic empty result (used for dry-run / no-op paths)."""
        return cls(
            source_type=source_type,
            provider=provider,
            source_ref=source_ref,
            requested_url=requested_url,
            captured_at=utc_now(),
            content_hash=sha256_text(""),
        )

    def safe_dict(self) -> dict[str, Any]:
        """JSON-safe view for API/CLI — never includes secrets."""
        return {
            "source_type": self.source_type.value,
            "provider": self.provider,
            "source_ref": self.source_ref,
            "requested_url": self.requested_url,
            "captured_at": self.captured_at.isoformat(),
            "status_code": self.status_code,
            "content_type": self.content_type,
            "content_hash": self.content_hash,
            "raw_size_bytes": self.raw_size_bytes,
            "evidence": dict(self.evidence),
            "warnings": list(self.warnings),
            "simulated": self.simulated,
        }
