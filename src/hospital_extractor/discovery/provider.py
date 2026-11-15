"""Provider-neutral discovery interfaces and a truthful not-configured provider.

P1-A: no live provider integration, no network requests, no fabricated
candidates. Future providers may differ in supported filters, fields, coverage,
and pagination semantics; this contract does not claim interchangeability.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .schemas import (
    DiscoverySearchRequest,
    DiscoverySearchResponse,
    ProviderAttribution,
    ProviderError,
    ProviderStatus,
)

NOT_CONFIGURED_PROVIDER_KEY = "not_configured"
NOT_CONFIGURED_DETAIL = (
    "No live discovery provider is configured. Deterministic discovery contracts "
    "are available, but external hospital/provider search is not performed and no "
    "candidates can be returned."
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _not_configured_attribution() -> ProviderAttribution:
    return ProviderAttribution(
        provider=NOT_CONFIGURED_PROVIDER_KEY,
        notice="No external discovery provider configured; no third-party data was retrieved.",
        terms_url=None,
    )


@runtime_checkable
class DiscoveryProvider(Protocol):
    """Provider-neutral interface. Implementations must be truthful and bounded."""

    provider_key: str

    def search(self, request: DiscoverySearchRequest) -> DiscoverySearchResponse:
        """Execute a discovery search.

        Must not fabricate candidates. May return not_configured/rate_limited/
        timeout/partial/error with explicit details and provenance.
        """

    def capabilities(self) -> dict[str, object]:
        """Describe supported markets/filters/pagination/attribution (truthful)."""


class NotConfiguredDiscoveryProvider:
    """Truthful placeholder: no network, no simulated hospitals, no candidates."""

    provider_key: str = NOT_CONFIGURED_PROVIDER_KEY

    def search(self, request: DiscoverySearchRequest) -> DiscoverySearchResponse:
        # No network requests; request is accepted only to produce a truthful response.
        _ = request
        now = _utcnow()
        return DiscoverySearchResponse(
            provider=self.provider_key,
            status=ProviderStatus.NOT_CONFIGURED,
            candidates=[],
            next_page_token=None,
            total_available=0,
            retrieved_at=now,
            attribution=_not_configured_attribution(),
            warnings=[],
            errors=[
                ProviderError(
                    code="provider_not_configured",
                    message=NOT_CONFIGURED_DETAIL,
                    retryable=False,
                    retry_after_ms=None,
                )
            ],
            partial=False,
        )

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider_key,
            "configured": False,
            "live_network": False,
            "supported_markets": [],
            "supported_filters": [],
            "pagination_mode": "none",
            "attribution_required": False,
            "notes": [NOT_CONFIGURED_DETAIL],
        }
