"""Source adapters.  Every adapter yields the same canonical HospitalDataset."""

from __future__ import annotations

from hospital_extractor.sources.acquisition import (
    AcquisitionResult,
    ConnectorError,
    ConnectorErrorCode,
    ConnectorUnauthorizedError,
)
from hospital_extractor.sources.authorization import (
    AuthorizationDecision,
    AuthorizationPolicy,
    CredentialRef,
    SsrfError,
    assert_safe_url,
)
from hospital_extractor.sources.base import (
    HospitalDataSource,
    SourceFetchError,
    apply_source_provenance,
)
from hospital_extractor.sources.hospital_api import HospitalAPIDataSource
from hospital_extractor.sources.json_feed import JSONFeedDataSource
from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource
from hospital_extractor.sources.mock_authorized import (
    MOCK_SCENARIOS,
    MockAuthorizedApiConnector,
    MockScenario,
)
from hospital_extractor.sources.models import SourceIdentity, SourceMetadata, SourceType
from hospital_extractor.sources.pipeline import (
    Harvested,
    HarvestError,
    harvest,
    run_harvest,
)
from hospital_extractor.sources.registry import (
    SourceConfig,
    SourceDefinition,
    SourceRegistry,
    build_sources_from_config,
    create_source,
    get_default_registry,
    load_sources_config,
)
from hospital_extractor.sources.website import WebsiteDataSource

__all__ = [
    "MOCK_SCENARIOS",
    "AcquisitionResult",
    "AuthorizationDecision",
    "AuthorizationPolicy",
    "ConnectorError",
    "ConnectorErrorCode",
    "ConnectorUnauthorizedError",
    "CredentialRef",
    "HarvestError",
    "Harvested",
    "HospitalAPIDataSource",
    "HospitalDataSource",
    "JSONFeedDataSource",
    "MockAuthorizedApiConnector",
    "MockHospitalAPIDataSource",
    "MockScenario",
    "SsrfError",
    "SourceConfig",
    "SourceDefinition",
    "SourceFetchError",
    "SourceIdentity",
    "SourceMetadata",
    "SourceRegistry",
    "SourceType",
    "WebsiteDataSource",
    "apply_source_provenance",
    "assert_safe_url",
    "build_sources_from_config",
    "create_source",
    "get_default_registry",
    "harvest",
    "load_sources_config",
    "run_harvest",
]
