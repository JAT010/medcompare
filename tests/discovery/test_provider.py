from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hospital_extractor.discovery.provider import (
    NOT_CONFIGURED_DETAIL,
    NOT_CONFIGURED_PROVIDER_KEY,
    NotConfiguredDiscoveryProvider,
)
from hospital_extractor.discovery.schemas import (
    DiscoverySearchRequest,
    DiscoverySearchResponse,
    ProviderAttribution,
    ProviderCandidate,
    ProviderStatus,
    SourceReference,
)


def test_not_configured_provider_returns_truthful_status_and_no_candidates():
    provider = NotConfiguredDiscoveryProvider()
    resp = provider.search(DiscoverySearchRequest(query_text="hospitals in Austin"))
    assert resp.status == ProviderStatus.NOT_CONFIGURED
    assert resp.provider == NOT_CONFIGURED_PROVIDER_KEY
    assert resp.candidates == []
    assert resp.total_available == 0
    assert resp.errors and resp.errors[0].code == "provider_not_configured"
    assert NOT_CONFIGURED_DETAIL[:20] in resp.errors[0].message


def test_not_configured_provider_performs_no_network(monkeypatch):
    # Guard: ensure common network entrypoints are not used if accidentally wired.
    import socket

    def _boom(*args, **kwargs):
        raise AssertionError("network access is not allowed")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    provider = NotConfiguredDiscoveryProvider()
    resp = provider.search(DiscoverySearchRequest(query_text="hospital near me"))
    assert resp.status == ProviderStatus.NOT_CONFIGURED
    assert resp.candidates == []


def test_provider_capabilities_truthful():
    caps = NotConfiguredDiscoveryProvider().capabilities()
    assert caps["configured"] is False
    assert caps["live_network"] is False
    assert caps["supported_markets"] == []


def test_not_configured_status_cannot_include_candidates():
    attr = ProviderAttribution(provider="x", notice="n")
    with pytest.raises(ValidationError):
        DiscoverySearchResponse(
            provider="x",
            status=ProviderStatus.NOT_CONFIGURED,
            candidates=[
                ProviderCandidate(
                    provider="x",
                    provider_candidate_id="1",
                    retrieved_at=datetime.now(UTC),
                    attribution=attr,
                )
            ],
            retrieved_at=datetime.now(UTC),
            attribution=attr,
        )


def test_pagination_and_partial_result_schema_validate():
    attr = ProviderAttribution(provider="p", notice="n")
    resp = DiscoverySearchResponse(
        provider="p",
        status=ProviderStatus.PARTIAL,
        candidates=[],
        next_page_token="page-2",
        total_available=10,
        retrieved_at=datetime.now(UTC),
        attribution=attr,
        warnings=["partial due to timeout"],
    )
    assert resp.partial is True
    assert resp.next_page_token == "page-2"


def test_provider_error_states_represented():
    attr = ProviderAttribution(provider="p", notice="n")
    from hospital_extractor.discovery.schemas import ProviderError

    resp = DiscoverySearchResponse(
        provider="p",
        status=ProviderStatus.RATE_LIMITED,
        candidates=[],
        retrieved_at=datetime.now(UTC),
        attribution=attr,
        errors=[
            ProviderError(
                code="rate_limited",
                message="slow down",
                retryable=True,
                retry_after_ms=1000,
            )
        ],
    )
    assert resp.errors[0].retryable is True
    assert resp.errors[0].retry_after_ms == 1000


def test_attribution_and_source_reference_validation():
    attr = ProviderAttribution(provider="p", notice="n")
    cand = ProviderCandidate(
        provider="p",
        provider_candidate_id="abc",
        name="Example",
        source_references=[SourceReference(ref_type="place_id", value="abc", uri="https://example.test/p/abc")],
        retrieved_at=datetime.now(UTC),
        attribution=attr,
    )
    assert cand.source_references[0].value == "abc"
    with pytest.raises(ValidationError):
        SourceReference(ref_type="", value="x")
