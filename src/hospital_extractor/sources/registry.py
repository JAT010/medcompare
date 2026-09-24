"""Source registry and configuration-driven selection (P21 extended).

Sources are registered by ``source_type`` so callers select a source by name
instead of hard-coding a concrete class.  Configuration arrives from
``config/sources.toml`` or constructor arguments.

P21 adds:

- explicit authorization fields on :class:`SourceConfig` (fail closed);
- :meth:`SourceRegistry.connector_for` resolving a source type to its
  connector class without a second registry;
- factories that attach :class:`AuthorizationPolicy` before any network use.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hospital_extractor.sources.authorization import AuthorizationPolicy
from hospital_extractor.sources.base import HospitalDataSource
from hospital_extractor.sources.models import SourceIdentity, SourceType

__all__ = [
    "DEFAULT_SOURCES_CONFIG_PATH",
    "SourceConfig",
    "SourceDefinition",
    "SourceRegistry",
    "build_sources_from_config",
    "create_source",
    "get_default_registry",
    "load_sources_config",
]

DEFAULT_SOURCES_CONFIG_PATH = Path("config/sources.toml")

# A factory builds a fully-configured data source from keyword arguments.
SourceFactory = Callable[..., HospitalDataSource]


@dataclass(frozen=True)
class SourceDefinition:
    """Static capabilities of one source type (registry-declared, not UI strings).

    Consumers ask the registry whether a source is simulated/authorized instead
    of hard-coding a source_type value — so a future ``hospital_api`` source
    needs no template or CLI changes.
    """

    source_type: SourceType
    simulated: bool = False
    requires_authorization: bool = False

    @property
    def display_name(self) -> str:
        """User-facing label (kept in lock-step with ``SourceType.label``)."""
        return self.source_type.label


@dataclass(frozen=True)
class SourceConfig:
    """One configured source (used by configuration-driven selection).

    ``source_type`` selects the registered adapter.  ``hospital_ref`` names
    the target hospital in that source's vocabulary.  Authorization fields
    are explicit — a source that requires authorization and is not marked
    ``authorized=True`` fails closed before any external request.
    """

    source_type: str
    hospital_ref: str = ""
    name: str | None = None
    base_url: str | None = None
    endpoint: str | None = None
    provider: str | None = None
    hospital_id: str | None = None
    # P21 authorization / network safety (no secrets — env var *names* only).
    authorized: bool = False
    allowed_hosts: tuple[str, ...] = ()
    allowed_schemes: tuple[str, ...] = ()
    allow_private_hosts: bool = False
    credential_env: str | None = None
    credential_required: bool = False
    timeout_seconds: float = 30.0
    max_response_bytes: int = 5_000_000
    extra: dict[str, Any] = field(default_factory=dict)

    def authorization_policy(self, *, requires_authorization: bool) -> AuthorizationPolicy:
        """Build the fail-closed policy for this configuration.

        When the source type requires authorization and ``authorized`` is
        false, the policy denies everything (no external request).  When
        authorization is not required (website, local feed, mock), a permissive
        policy is returned so SSRF host checks still apply if an allowlist is
        configured.
        """
        if requires_authorization and not self.authorized:
            return AuthorizationPolicy.deny_all(
                f"source type {self.source_type!r} requires explicit authorization"
            )
        return AuthorizationPolicy.from_config(
            authorized=self.authorized or not requires_authorization,
            allowed_hosts=self.allowed_hosts,
            allowed_schemes=self.allowed_schemes or None,
            allow_private_hosts=self.allow_private_hosts,
            credential_env=self.credential_env,
            credential_required=self.credential_required,
            timeout_seconds=self.timeout_seconds,
            max_response_bytes=self.max_response_bytes,
            provider=self.provider or "",
        )


class SourceRegistry:
    """Holds ``source_type -> factory`` registrations and their definitions."""

    def __init__(self) -> None:
        self._factories: dict[str, SourceFactory] = {}
        self._definitions: dict[SourceType, SourceDefinition] = {}
        self._connector_types: dict[str, type[HospitalDataSource]] = {}

    def register(
        self,
        source_type: str | SourceType,
        factory: SourceFactory,
        *,
        definition: SourceDefinition | None = None,
        connector_type: type[HospitalDataSource] | None = None,
    ) -> None:
        """Register *factory* (and optional *definition* / *connector_type*)."""
        key = source_type.value if isinstance(source_type, SourceType) else source_type
        self._factories[key] = factory
        enum = source_type if isinstance(source_type, SourceType) else SourceType(source_type)
        self._definitions[enum] = definition or SourceDefinition(source_type=enum)
        if connector_type is not None:
            self._connector_types[key] = connector_type

    def definition(self, source_type: str | SourceType) -> SourceDefinition:
        """Static capabilities for a registered source type."""
        try:
            enum = source_type if isinstance(source_type, SourceType) else SourceType(source_type)
        except ValueError as exc:
            raise KeyError(f"no source definition registered for {source_type!r}") from exc
        try:
            return self._definitions[enum]
        except KeyError:
            raise KeyError(f"no source definition registered for {enum.value!r}") from None

    def definitions(self) -> list[SourceDefinition]:
        """All registered definitions, in source-type value order."""
        ordered = sorted(self._definitions, key=lambda s: s.value)
        return [self._definitions[enum] for enum in ordered]

    def connector_for(self, source_type: str | SourceType) -> type[HospitalDataSource]:
        """Resolve a source type to its connector class.

        Raises :class:`KeyError` for unknown types — callers must fail clearly
        rather than inventing a default connector.
        """
        key = source_type.value if isinstance(source_type, SourceType) else str(source_type)
        if key not in self._definitions and key not in self._factories:
            raise KeyError(f"no source adapter registered for {key!r}")
        connector = self._connector_types.get(key)
        if connector is not None:
            return connector
        # Lazy default mapping for built-ins when connector_type was omitted.
        defaults: dict[str, type[HospitalDataSource]] = {
            "website": _load_website_type(),
            "mock_api": _load_mock_type(),
            "hospital_api": _load_hospital_api_type(),
            "json_feed": _load_json_feed_type(),
        }
        if key in defaults:
            return defaults[key]
        raise KeyError(f"no connector class registered for {key!r}")

    def build(self, config: SourceConfig) -> HospitalDataSource:
        """Build a data source from *config*."""
        factory = self._factories.get(config.source_type)
        if factory is None:
            raise KeyError(f"no source adapter registered for {config.source_type!r}")
        definition = self._definitions.get(SourceType(config.source_type))
        requires_auth = definition.requires_authorization if definition else False
        policy = config.authorization_policy(requires_authorization=requires_auth)
        explicit = {
            "hospital_ref",
            "base_url",
            "endpoint",
            "provider",
            "hospital_id",
            "name",
        }
        extras = {key: value for key, value in config.extra.items() if key not in explicit}
        return factory(
            hospital_ref=config.hospital_ref,
            base_url=config.base_url,
            endpoint=config.endpoint,
            provider=config.provider,
            hospital_id=config.hospital_id,
            name=config.name,
            authorization=policy,
            authorized=config.authorized,
            allowed_hosts=list(config.allowed_hosts),
            credential_env=config.credential_env,
            **extras,
        )

    def source_types(self) -> list[str]:
        """Registered source types (sorted)."""
        return sorted(self._factories)

    def has(self, source_type: str) -> bool:
        return source_type in self._factories


def _load_website_type() -> type[HospitalDataSource]:
    from hospital_extractor.sources.website import WebsiteDataSource

    return WebsiteDataSource


def _load_mock_type() -> type[HospitalDataSource]:
    from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource

    return MockHospitalAPIDataSource


def _load_hospital_api_type() -> type[HospitalDataSource]:
    from hospital_extractor.sources.hospital_api import HospitalAPIDataSource

    return HospitalAPIDataSource


def _load_json_feed_type() -> type[HospitalDataSource]:
    from hospital_extractor.sources.json_feed import JSONFeedDataSource

    return JSONFeedDataSource


def _website_factory(**kwargs: Any) -> HospitalDataSource:
    from hospital_extractor.sources.website import WebsiteDataSource

    return WebsiteDataSource(
        identity=SourceIdentity(hospital_ref=kwargs["hospital_ref"]),
        limits=kwargs.get("limits"),
        include_low_value=kwargs.get("include_low_value", False),
        output_dir=kwargs.get("output_dir") or "data",
        user_agent=kwargs.get("user_agent"),
    )


def _mock_factory(**kwargs: Any) -> HospitalDataSource:
    from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource

    return MockHospitalAPIDataSource(
        hospital_id=kwargs.get("hospital_id") or _hospital_id_from_ref(kwargs["hospital_ref"]),
        base_url=kwargs.get("base_url"),
        endpoint=kwargs.get("endpoint"),
        provider=kwargs.get("provider"),
    )


def _hospital_api_factory(**kwargs: Any) -> HospitalDataSource:
    from hospital_extractor.sources.hospital_api import HospitalAPIDataSource

    # Authorization policy is always attached by registry.build(); direct
    # HospitalAPIDataSource(...) construction without a policy remains valid
    # for offline unit tests with mock transports.
    authorization = kwargs.get("authorization")
    extra = kwargs.get("extra") or {}
    endpoint_template = kwargs.get("endpoint_template") or extra.get(
        "endpoint_template", "/hospital/{hospital_ref}"
    )
    headers = kwargs.get("headers") or extra.get("headers") or {}
    timeout = float(
        kwargs.get("timeout")
        or extra.get("timeout")
        or (authorization.timeout_seconds if authorization is not None else 30.0)
    )
    return HospitalAPIDataSource(
        hospital_ref=kwargs["hospital_ref"],
        base_url=kwargs.get("base_url") or "http://127.0.0.1:8001",
        endpoint_template=endpoint_template,
        provider=kwargs.get("provider"),
        headers=headers,
        mapping=kwargs.get("mapping") or extra.get("mapping") or {},
        timeout=timeout,
        authorization=authorization,
    )


def _json_feed_factory(**kwargs: Any) -> HospitalDataSource:
    from hospital_extractor.sources.json_feed import JSONFeedDataSource

    authorization = kwargs.get("authorization")
    return JSONFeedDataSource(
        path=kwargs["hospital_ref"],
        name=kwargs.get("name"),
        authorization=authorization,
        timeout=float(
            kwargs.get("timeout")
            or (authorization.timeout_seconds if authorization is not None else 30.0)
        ),
    )


def _hospital_id_from_ref(ref: str) -> str:
    """Default demo hospital id: last path segment or the raw reference."""
    segment = ref.rstrip("/").rsplit("/", 1)[-1]
    return segment or ref


def get_default_registry() -> SourceRegistry:
    """A registry preloaded with the built-in adapters."""
    registry = SourceRegistry()
    registry.register(
        SourceType.WEBSITE,
        _website_factory,
        definition=SourceDefinition(SourceType.WEBSITE, simulated=False),
        connector_type=_load_website_type(),
    )
    registry.register(
        SourceType.MOCK_API,
        _mock_factory,
        definition=SourceDefinition(
            SourceType.MOCK_API, simulated=True, requires_authorization=False
        ),
        connector_type=_load_mock_type(),
    )
    registry.register(
        SourceType.HOSPITAL_API,
        _hospital_api_factory,
        definition=SourceDefinition(
            SourceType.HOSPITAL_API, simulated=False, requires_authorization=True
        ),
        connector_type=_load_hospital_api_type(),
    )
    registry.register(
        SourceType.JSON_FEED,
        _json_feed_factory,
        definition=SourceDefinition(SourceType.JSON_FEED, simulated=False),
        connector_type=_load_json_feed_type(),
    )
    return registry


def create_source(source_type: str, **kwargs: Any) -> HospitalDataSource:
    """Build a single source by type using the default registry."""
    return get_default_registry().build(SourceConfig(source_type=source_type, **kwargs))


def load_sources_config(path: Path | str = DEFAULT_SOURCES_CONFIG_PATH) -> list[SourceConfig]:
    """Load ``[sources]`` entries from a TOML file (optional)."""
    config_path = Path(path)
    if not config_path.is_file():
        return []
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    entries = raw.get("sources", [])
    sources: list[SourceConfig] = []
    for entry in entries:
        source_type = entry.get("source_type")
        if not source_type:
            continue
        sources.append(
            SourceConfig(
                source_type=source_type,
                hospital_ref=entry.get("hospital_ref", ""),
                name=entry.get("name"),
                base_url=entry.get("base_url"),
                endpoint=entry.get("endpoint"),
                provider=entry.get("provider"),
                hospital_id=entry.get("hospital_id"),
                authorized=bool(entry.get("authorized", False)),
                allowed_hosts=tuple(entry.get("allowed_hosts", ())),
                allowed_schemes=tuple(entry.get("allowed_schemes", ())),
                allow_private_hosts=bool(entry.get("allow_private_hosts", False)),
                credential_env=entry.get("credential_env"),
                credential_required=bool(entry.get("credential_required", False)),
                timeout_seconds=float(entry.get("timeout_seconds", 30.0)),
                max_response_bytes=int(entry.get("max_response_bytes", 5_000_000)),
                extra=dict(entry.get("extra", {})),
            )
        )
    return sources


def build_sources_from_config(
    registry: SourceRegistry | None = None,
    path: Path | str = DEFAULT_SOURCES_CONFIG_PATH,
) -> dict[str, HospitalDataSource]:
    """Build ``name -> source`` for every configured source."""
    registry = registry or get_default_registry()
    built: dict[str, HospitalDataSource] = {}
    for config in load_sources_config(path):
        built[config.name or f"{config.source_type}:{config.hospital_ref}"] = registry.build(config)
    return built
