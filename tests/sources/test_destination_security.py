"""Security boundary tests: destination validation and SSRF protection."""

from __future__ import annotations

import asyncio
import ipaddress
import uuid

import httpx
import pytest

from hospital_extractor.persistence.models_integration import (
    HospitalSourceIntegration,
    IntegrationAuthMode,
    IntegrationStatus,
)
from hospital_extractor.sources.authorization import (
    AuthorizationPolicy,
    check_destination,
    classify_address,
)
from hospital_extractor.sources.base import ConnectorUnauthorizedError
from hospital_extractor.sources.hospital_api import HospitalRestJsonConnector
from hospital_extractor.sources.registry import build_connector


def _addr(cidr: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(cidr)


class TestClassifyAddress:
    @pytest.mark.parametrize(
        "address",
        [
            "169.254.169.254",  # cloud metadata (link-local)
            "169.254.1.1",
            "100.64.0.1",  # shared address space (RFC 6598)
            "192.0.2.1",  # documentation range
            "198.51.100.1",
            "203.0.113.7",
            "198.18.0.23",  # benchmarking range
            "224.0.0.1",  # multicast
            "240.0.0.1",  # reserved
            "0.0.0.0",
            "::ffff:169.254.169.254",  # IPv4-mapped metadata
            "::1",  # loopback
            "fe80::1",  # IPv6 link-local
        ],
    )
    def test_always_blocked_even_in_private_mode(self, address: str) -> None:
        ok, _ = classify_address(_addr(address), allow_private_hosts=True)
        assert ok is False

    @pytest.mark.parametrize(
        "address",
        ["10.1.2.3", "172.16.5.4", "192.168.9.10"],
    )
    def test_rfc1918_blocked_in_strict_mode(self, address: str) -> None:
        ok, _ = classify_address(_addr(address), allow_private_hosts=False)
        assert ok is False

    @pytest.mark.parametrize(
        "address",
        ["10.1.2.3", "172.16.5.4", "192.168.9.10"],
    )
    def test_rfc1918_allowed_only_when_private_mode(self, address: str) -> None:
        ok, _ = classify_address(_addr(address), allow_private_hosts=True)
        assert ok is True

    def test_loopback_blocked_in_private_mode(self) -> None:
        ok, _ = classify_address(_addr("127.0.0.1"), allow_private_hosts=True)
        assert ok is False

    def test_global_address_allowed(self) -> None:
        ok, _ = classify_address(_addr("93.184.216.34"), allow_private_hosts=False)
        assert ok is True


class TestCheckDestination:
    def test_host_resolving_to_metadata_blocked(self) -> None:
        check = check_destination(
            "https://metadata.example/",
            resolver=lambda _host: ["169.254.169.254"],
            allow_private_hosts=True,
        )
        assert not check.allowed

    def test_host_resolving_to_private_blocked_in_strict_mode(self) -> None:
        check = check_destination(
            "https://internal.example/",
            resolver=lambda _host: ["192.168.1.10"],
            allow_private_hosts=False,
        )
        assert not check.allowed

    def test_host_resolving_to_private_allowed_in_private_mode(self) -> None:
        check = check_destination(
            "https://internal.example/",
            resolver=lambda _host: ["192.168.1.10"],
            allow_private_hosts=True,
        )
        assert check.allowed

    def test_host_resolving_to_mixed_blocked_if_any_bad(self) -> None:
        check = check_destination(
            "https://mixed.example/",
            resolver=lambda _host: ["93.184.216.34", "169.254.169.254"],
            allow_private_hosts=False,
        )
        assert not check.allowed

    def test_unresolvable_host_fails_closed(self) -> None:
        def fail(_host: str) -> list[str]:
            raise OSError("name or service not known")

        check = check_destination("https://nope.example/", resolver=fail)
        assert not check.allowed
        assert "resolve" in check.reason

    def test_literal_metadata_ip_blocked(self) -> None:
        check = check_destination("http://169.254.169.254/latest/meta-data/")
        assert not check.allowed

    def test_embedded_credentials_rejected(self) -> None:
        policy = AuthorizationPolicy(authorized=True)
        decision = policy.check_url("https://user:password@api.example.test/v1/x")
        assert not decision.allowed
        assert decision.code == "ssrf"


def _integration(
    connector_type: str = "mock_hospital_api_simulated",
    *,
    config: dict | None = None,
    enabled: bool = True,
) -> HospitalSourceIntegration:
    return HospitalSourceIntegration(
        facility_id=uuid.uuid4(),
        name="x",
        connector_type=connector_type,
        auth_mode=IntegrationAuthMode.NONE.value,
        config_json=config or {},
        status=IntegrationStatus.CONFIGURED.value,
        enabled=enabled,
    )


class TestConnectorDestinationEnforcement:
    def test_named_host_resolving_to_metadata_never_requested(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json={"hospital": {"name": "X"}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def run() -> None:
            policy = AuthorizationPolicy.from_config(
                authorized=True,
                allowed_hosts=["internal.example"],
                allow_private_hosts=True,  # would permit RFC1918, never metadata
            )
            connector = HospitalRestJsonConnector(
                "ref",
                base_url="https://internal.example",
                authorization=policy,
                client=client,
                resolver=lambda _host: ["169.254.169.254"],
            )
            with pytest.raises(ConnectorUnauthorizedError):
                await connector.acquire()
            await client.aclose()

        asyncio.run(run())
        assert calls == []

    def test_literal_documentation_ip_blocked(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, text="{}")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def run() -> None:
            policy = AuthorizationPolicy.from_config(
                authorized=True,
                allowed_hosts=["203.0.113.9"],
                allow_private_hosts=True,
            )
            connector = HospitalRestJsonConnector(
                "ref",
                base_url="https://203.0.113.9",
                authorization=policy,
                client=client,
            )
            with pytest.raises(ConnectorUnauthorizedError):
                await connector.acquire()
            await client.aclose()

        asyncio.run(run())
        assert calls == []


class TestMockConnectorVariantValidation:
    def test_unknown_payload_variant_fails_closed(self) -> None:
        integration = _integration(config={"payload_variant": "surprise"})
        with pytest.raises(Exception, match="payload_variant"):
            build_connector(integration)
