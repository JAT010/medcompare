"""Connector contracts: typed errors, acquisition results, protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from hospital_extractor.utils.hashing import sha256_bytes

__all__ = [
    "AcquisitionResult",
    "ConnectorErrorCode",
    "ConnectorUnauthorizedError",
    "HospitalConnectorError",
    "HospitalSourceConnector",
    "PERMANENT_CONNECTOR_CODES",
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


# Codes that must never be retried by orchestration / the worker.
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


class HospitalConnectorError(RuntimeError):
    """Typed connector failure.

    The message is sanitized — it must never include headers, tokens, or
    response bodies. Callers inspect :attr:`code` and :attr:`retryable`.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ConnectorErrorCode = ConnectorErrorCode.NETWORK_ERROR,
        retryable: bool | None = None,
    ) -> None:
        self.code = code
        self.retryable = code not in PERMANENT_CONNECTOR_CODES if retryable is None else retryable
        super().__init__(f"[{code.value}] {message}")

    @property
    def permanent(self) -> bool:
        """True when the failure can never succeed without config changes."""
        return not self.retryable


class ConnectorUnauthorizedError(HospitalConnectorError):
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

    Carries acquisition metadata + a bounded payload reference. Credentials
    are never fields here; ``evidence`` is a small JSON-safe dict for
    provenance, not a raw response dump.
    """

    connector_type: str
    provider: str | None
    source_ref: str
    requested_url: str
    captured_at: datetime
    status_code: int | None = None
    content_type: str | None = None
    content_hash: str = ""
    payload: Any = None  # parsed JSON — bounded by the connector
    raw_size_bytes: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    simulated: bool = False

    @classmethod
    def from_bytes(
        cls,
        *,
        connector_type: str,
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
            connector_type=connector_type,
            provider=provider,
            source_ref=source_ref,
            requested_url=requested_url,
            captured_at=captured_at or datetime.now(UTC),
            status_code=status_code,
            content_type=content_type,
            content_hash=sha256_bytes(body),
            payload=payload,
            raw_size_bytes=len(body),
            evidence=dict(evidence or {}),
            warnings=list(warnings or []),
            simulated=simulated,
        )

    def safe_dict(self) -> dict[str, Any]:
        """JSON-safe view for API/CLI — never includes secrets."""
        return {
            "connector_type": self.connector_type,
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


class HospitalSourceConnector(Protocol):
    """Acquisition boundary for hospital-provided source data."""

    connector_type: str

    async def acquire(self) -> AcquisitionResult:
        """Fetch and validate one capture; raise a typed connector error otherwise."""
        ...
