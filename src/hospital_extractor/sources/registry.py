"""Connector registry: build a connector from a persisted integration row.

Security contract:
- Unknown connector types are rejected.
- Live connectors (``hospital_rest_json``) require an allowlisted base_url at
  an allowed host; without an explicit allowlist they fail closed.
- Credentials come only from the environment variable named by
  ``credential_env_ref``; the row itself never holds a secret.
- ``enabled=false`` means the connector is never built.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from hospital_extractor.persistence.models_integration import (
    HospitalSourceIntegration,
    IntegrationAuthMode,
    IntegrationStatus,
    SnapshotSourceKind,
)
from hospital_extractor.sources.authorization import AuthorizationPolicy
from hospital_extractor.sources.base import (
    ConnectorErrorCode,
    ConnectorUnauthorizedError,
    HospitalConnectorError,
    HospitalSourceConnector,
)
from hospital_extractor.sources.hospital_api import (
    DEFAULT_ENDPOINT_TEMPLATE,
    HospitalRestJsonConnector,
)
from hospital_extractor.sources.mock_hospital_api import (
    MOCK_CONNECTOR_TYPE,
    VARIANTS,
    MockHospitalApiConnector,
)

__all__ = [
    "LIVE_CONNECTOR_TYPES",
    "KNOWN_CONNECTOR_TYPES",
    "MOCK_CONNECTOR_TYPE",
    "build_connector",
    "build_policy_for_integration",
]

LIVE_CONNECTOR_TYPES: frozenset[str] = frozenset({"hospital_rest_json"})
KNOWN_CONNECTOR_TYPES: frozenset[str] = LIVE_CONNECTOR_TYPES | frozenset({MOCK_CONNECTOR_TYPE})


def build_policy_for_integration(integration: HospitalSourceIntegration) -> AuthorizationPolicy:
    """Build the fail-closed policy from an integration row (never from secrets)."""
    config = _config(integration)
    allowed_hosts = _str_list(config.get("allowed_hosts"))
    allow_private_hosts = bool(config.get("allow_private_hosts", False))
    timeout_seconds = _float(config.get("timeout_seconds"), 30.0)
    max_response_bytes = _int(config.get("max_response_bytes"), 5_000_000)
    credential_required = integration.auth_mode != IntegrationAuthMode.NONE.value
    provider = config.get("provider") if isinstance(config.get("provider"), str) else "hospital"
    return AuthorizationPolicy.from_config(
        authorized=integration.enabled and integration.status == IntegrationStatus.CONFIGURED.value,
        allowed_hosts=allowed_hosts,
        allow_private_hosts=allow_private_hosts,
        credential_env=integration.credential_env_ref,
        credential_required=credential_required,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        provider=str(provider),
    )


def build_connector(
    integration: HospitalSourceIntegration,
    *,
    client: httpx.AsyncClient | None = None,
) -> HospitalSourceConnector:
    """Validate *integration* and build its connector. Fail closed on any error."""
    if not integration.enabled:
        raise ConnectorUnauthorizedError("integration is disabled; enable it before use")
    connector_type = integration.connector_type
    if connector_type not in KNOWN_CONNECTOR_TYPES:
        raise HospitalConnectorError(
            f"unsupported connector_type {connector_type!r}",
            code=ConnectorErrorCode.INVALID_CONFIGURATION,
            retryable=False,
        )

    policy = build_policy_for_integration(integration)
    config = _config(integration)
    hospital_ref = _hospital_ref(integration, config)

    if connector_type == MOCK_CONNECTOR_TYPE:
        if integration.status != IntegrationStatus.CONFIGURED.value:
            raise HospitalConnectorError(
                "mock connector requires status 'configured'",
                code=ConnectorErrorCode.INVALID_CONFIGURATION,
                retryable=False,
            )
        variant = config.get("payload_variant", "default")
        if not isinstance(variant, str) or variant not in VARIANTS:
            raise HospitalConnectorError(
                f"payload_variant must be one of {sorted(VARIANTS)} — "
                "unknown variants fail closed rather than silently returning the baseline",
                code=ConnectorErrorCode.INVALID_CONFIGURATION,
                retryable=False,
            )
        return MockHospitalApiConnector(
            hospital_ref,
            variant=variant if isinstance(variant, str) else "default",
            authorization=policy,
            provider="SIMULATED MedCompare mock hospital API (not a real hospital)",
        )

    # Live connector: strict base_url + allowlist validation before any request.
    base_url = (integration.base_url or "").strip()
    if not base_url:
        raise HospitalConnectorError(
            "hospital_rest_json connector requires a configured base_url",
            code=ConnectorErrorCode.INVALID_CONFIGURATION,
            retryable=False,
        )
    parsed = urlsplit(base_url)
    if (parsed.scheme or "").lower() not in {"https", "http"}:
        raise HospitalConnectorError(
            "base_url must use http or https",
            code=ConnectorErrorCode.INVALID_CONFIGURATION,
            retryable=False,
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise HospitalConnectorError(
            "base_url has no host",
            code=ConnectorErrorCode.INVALID_CONFIGURATION,
            retryable=False,
        )
    allowed_hosts = _str_list(config.get("allowed_hosts"))
    if not allowed_hosts:
        raise HospitalConnectorError(
            "hospital_rest_json connector requires a non-empty allowlist of hosts",
            code=ConnectorErrorCode.INVALID_CONFIGURATION,
            retryable=False,
        )
    if not _host_allowed(host, allowed_hosts):
        raise HospitalConnectorError(
            f"base_url host {host!r} is not in the configured allowlist",
            code=ConnectorErrorCode.INVALID_CONFIGURATION,
            retryable=False,
        )
    endpoint_path = config.get("endpoint_path")
    auth_header_name = config.get("auth_header_name", "Authorization")
    auth_header_scheme = config.get("auth_header_scheme", "Bearer")
    return HospitalRestJsonConnector(
        hospital_ref,
        base_url=base_url,
        authorization=policy,
        endpoint_template=(
            endpoint_path
            if isinstance(endpoint_path, str) and endpoint_path.strip()
            else DEFAULT_ENDPOINT_TEMPLATE
        ),
        provider=integration.name,
        client=client,
        auth_header_name=(
            str(auth_header_name) if isinstance(auth_header_name, str) else "Authorization"
        ),
        auth_header_scheme=(
            str(auth_header_scheme) if isinstance(auth_header_scheme, str) else "Bearer"
        ),
    )


def source_kind_for(integration: HospitalSourceIntegration) -> str:
    """Snapshot source kind this integration produces."""
    if integration.connector_type == MOCK_CONNECTOR_TYPE:
        return SnapshotSourceKind.MOCK_HOSPITAL_API_SIMULATED.value
    return SnapshotSourceKind.HOSPITAL_API.value


def _config(integration: HospitalSourceIntegration) -> dict[str, Any]:
    config = integration.config_json
    return dict(config) if isinstance(config, dict) else {}


def _hospital_ref(integration: HospitalSourceIntegration, config: dict[str, Any]) -> str:
    ref = config.get("hospital_ref")
    if isinstance(ref, str) and ref.strip():
        return ref.strip()
    return str(integration.facility_id)


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _host_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for pattern in allowed:
        pattern_l = pattern.lower().rstrip(".")
        if pattern_l.startswith("*."):
            suffix = pattern_l[1:]
            if host.endswith(suffix) and host != suffix.lstrip("."):
                return True
        elif host == pattern_l:
            return True
    return False
