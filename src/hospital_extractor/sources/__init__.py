"""Hospital-provided source connectors (authorized hospital APIs)."""

from hospital_extractor.sources.authorization import (
    AuthorizationDecision,
    AuthorizationPolicy,
    CredentialRef,
    SsrfError,
    resolve_credential,
)
from hospital_extractor.sources.base import (
    PERMANENT_CONNECTOR_CODES,
    AcquisitionResult,
    ConnectorErrorCode,
    ConnectorUnauthorizedError,
    HospitalConnectorError,
    HospitalSourceConnector,
)
from hospital_extractor.sources.hospital_api import HospitalRestJsonConnector
from hospital_extractor.sources.mock_hospital_api import (
    MOCK_CONNECTOR_TYPE,
    SIMULATED_PROVIDER,
    SIMULATED_WARNING,
    MockHospitalApiConnector,
)
from hospital_extractor.sources.registry import (
    KNOWN_CONNECTOR_TYPES,
    LIVE_CONNECTOR_TYPES,
    build_connector,
    build_policy_for_integration,
    source_kind_for,
)

__all__ = [
    "AcquisitionResult",
    "AuthorizationDecision",
    "AuthorizationPolicy",
    "ConnectorErrorCode",
    "ConnectorUnauthorizedError",
    "CredentialRef",
    "HospitalConnectorError",
    "HospitalRestJsonConnector",
    "HospitalSourceConnector",
    "KNOWN_CONNECTOR_TYPES",
    "LIVE_CONNECTOR_TYPES",
    "MOCK_CONNECTOR_TYPE",
    "MockHospitalApiConnector",
    "PERMANENT_CONNECTOR_CODES",
    "SIMULATED_PROVIDER",
    "SIMULATED_WARNING",
    "SsrfError",
    "build_connector",
    "build_policy_for_integration",
    "resolve_credential",
    "source_kind_for",
]
