"""Connector tests — deterministic, offline (mock transports only)."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from hospital_extractor.persistence.models_integration import (
    HospitalSourceIntegration,
    IntegrationAuthMode,
    IntegrationStatus,
    SnapshotSourceKind,
)
from hospital_extractor.sources.authorization import AuthorizationPolicy, CredentialRef
from hospital_extractor.sources.base import (
    ConnectorErrorCode,
    ConnectorUnauthorizedError,
    HospitalConnectorError,
)
from hospital_extractor.sources.hospital_api import HospitalRestJsonConnector
from hospital_extractor.sources.mock_hospital_api import MockHospitalApiConnector
from hospital_extractor.sources.registry import (
    build_connector,
    build_policy_for_integration,
    source_kind_for,
)

TEST_TOKEN = "unit-test-token-not-a-secret-0001"


def PUBLIC_RESOLVER(_host: str) -> list[str]:
    return ["93.184.216.34"]


def _integration(
    connector_type: str = "mock_hospital_api_simulated",
    *,
    enabled: bool = True,
    status: IntegrationStatus = IntegrationStatus.CONFIGURED,
    config: dict | None = None,
    base_url: str | None = None,
    auth_mode: IntegrationAuthMode = IntegrationAuthMode.NONE,
    credential_env_ref: str | None = None,
) -> HospitalSourceIntegration:
    return HospitalSourceIntegration(
        facility_id=uuid.uuid4(),
        name="simulated source",
        connector_type=connector_type,
        base_url=base_url,
        auth_mode=auth_mode.value,
        credential_env_ref=credential_env_ref,
        config_json=config or {},
        status=status.value,
        enabled=enabled,
    )


def _authorized_policy(**overrides: object) -> AuthorizationPolicy:
    policy = AuthorizationPolicy.from_config(
        authorized=True,
        allowed_hosts=["mock-authorized.example.test", "api.example.test"],
        **overrides,  # type: ignore[arg-type]
    )
    return policy


class TestAuthorizationPolicy:
    def test_unauthorized_policy_denies_everything(self) -> None:
        policy = AuthorizationPolicy(authorized=False)
        decision = policy.check_url("https://api.example.test/v1/x")
        assert not decision.allowed
        assert decision.code == "unauthorized"

    def test_private_literal_host_denied_without_flag(self) -> None:
        policy = AuthorizationPolicy.from_config(authorized=True)
        decision = policy.check_url("http://10.0.0.5/v1/x")
        assert not decision.allowed

    def test_always_blocked_link_local_even_when_allowlisted(self) -> None:
        policy = AuthorizationPolicy.from_config(
            authorized=True,
            allowed_hosts=["169.254.169.254"],
        )
        decision = policy.check_url("http://169.254.169.254/latest/meta-data/")
        assert not decision.allowed
        assert decision.code == "ssrf"

    def test_allowlisted_host_allowed(self) -> None:
        policy = AuthorizationPolicy.from_config(
            authorized=True,
            allowed_hosts=["api.example.test"],
        )
        assert policy.check_url("https://api.example.test/v1/h").allowed

    def test_wildcard_allowlist(self) -> None:
        policy = AuthorizationPolicy.from_config(
            authorized=True,
            allowed_hosts=["*.example.test"],
        )
        assert policy.check_url("https://api.example.test/x").allowed
        assert not policy.check_url("https://example.test.evil/x").allowed

    def test_credential_required_without_ref_denies(self) -> None:
        policy = AuthorizationPolicy.from_config(
            authorized=True,
            allowed_hosts=["api.example.test"],
            credential_required=True,
        )
        decision = policy.check_url("https://api.example.test/v1/h")
        assert not decision.allowed
        assert decision.code == "invalid_configuration"

    def test_credential_required_env_missing_denies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MEDCOMPARE_TEST_MISSING_TOKEN", raising=False)
        policy = AuthorizationPolicy.from_config(
            authorized=True,
            allowed_hosts=["api.example.test"],
            credential_env="MEDCOMPARE_TEST_MISSING_TOKEN",
            credential_required=True,
        )
        decision = policy.check_url("https://api.example.test/v1/h")
        assert not decision.allowed
        assert decision.code == "invalid_configuration"

    def test_credential_ref_resolves_only_at_call_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_TEST_TOKEN", TEST_TOKEN)
        ref = CredentialRef("MEDCOMPARE_TEST_TOKEN")
        assert ref.resolve() == TEST_TOKEN
        assert TEST_TOKEN not in repr(ref)
        assert ref.describe() == "env:MEDCOMPARE_TEST_TOKEN"


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


class TestMockHospitalApi:
    def test_requires_authorization_policy(self) -> None:
        connector = MockHospitalApiConnector("mock-authorized-1", authorization=None)
        with pytest.raises(ConnectorUnauthorizedError):
            _run(connector.acquire())

    def test_fail_closed_when_not_authorized(self) -> None:
        connector = MockHospitalApiConnector(
            "mock-authorized-1",
            authorization=AuthorizationPolicy(authorized=False),
        )
        with pytest.raises(ConnectorUnauthorizedError):
            _run(connector.acquire())

    def test_default_variant_is_deterministic_and_labeled_simulated(self) -> None:
        policy = AuthorizationPolicy.from_config(
            authorized=True,
            allowed_hosts=["mock-authorized.example.test"],
        )
        connector = MockHospitalApiConnector("mock-authorized-1", authorization=policy)
        first = _run(connector.acquire())
        second = _run(connector.acquire())
        assert first.content_hash == second.content_hash
        assert first.simulated is True
        assert first.payload["hospital"]["bed_count"] == 100
        assert first.evidence["simulated"] is True
        assert first.warnings

    def test_changed_variant_has_deterministic_delta(self) -> None:
        policy = AuthorizationPolicy.from_config(
            authorized=True,
            allowed_hosts=["mock-authorized.example.test"],
        )
        connector = MockHospitalApiConnector(
            "mock-authorized-1",
            variant="changed",
            authorization=policy,
        )
        result = _run(connector.acquire())
        assert result.payload["hospital"]["bed_count"] == 120
        assert result.payload["hospital"]["doctor_count"] == 25
        assert {p["name"] for p in result.payload["procedures"]} == {
            "Angioplasty",
            "Robot-assisted surgery",
        }


class TestHospitalRestJsonConnector:
    def test_authorization_checked_before_network(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json={"hospital": {"name": "X"}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def run() -> None:
            connector = HospitalRestJsonConnector(
                "ref",
                base_url="https://api.example.test",
                authorization=AuthorizationPolicy(authorized=False),
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
            with pytest.raises(ConnectorUnauthorizedError):
                await connector.acquire()
            await client.aclose()

        asyncio.run(run())
        assert calls == []

    def test_credentials_attached_at_request_time_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.headers.get("Authorization", ""))
            return httpx.Response(200, json={"hospital": {"name": "X"}})

        monkeypatch.setenv("MEDCOMPARE_TEST_API_TOKEN", TEST_TOKEN)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def run() -> None:
            policy = AuthorizationPolicy.from_config(
                authorized=True,
                allowed_hosts=["api.example.test"],
                credential_env="MEDCOMPARE_TEST_API_TOKEN",
                credential_required=True,
            )
            connector = HospitalRestJsonConnector(
                "ref",
                base_url="https://api.example.test",
                authorization=policy,
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
            await connector.acquire()
            await client.aclose()

        asyncio.run(run())
        assert captured == [f"Bearer {TEST_TOKEN}"]

    def test_error_messages_never_contain_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal error")

        monkeypatch.setenv("MEDCOMPARE_TEST_API_TOKEN", TEST_TOKEN)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def run() -> Exception:
            policy = AuthorizationPolicy.from_config(
                authorized=True,
                allowed_hosts=["api.example.test"],
                credential_env="MEDCOMPARE_TEST_API_TOKEN",
                credential_required=True,
            )
            connector = HospitalRestJsonConnector(
                "ref",
                base_url="https://api.example.test",
                authorization=policy,
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
            with pytest.raises(HospitalConnectorError) as exc_info:
                await connector.acquire()
            await client.aclose()
            return exc_info.value

        error = asyncio.run(run())
        assert TEST_TOKEN not in str(error)
        assert error.code is ConnectorErrorCode.HTTP_ERROR
        assert error.retryable is True

    def test_redirect_is_not_followed(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def run() -> HospitalConnectorError:
            policy = AuthorizationPolicy.from_config(
                authorized=True,
                allowed_hosts=["api.example.test"],
            )
            connector = HospitalRestJsonConnector(
                "ref",
                base_url="https://api.example.test",
                authorization=policy,
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
            with pytest.raises(HospitalConnectorError) as exc_info:
                await connector.acquire()
            await client.aclose()
            return exc_info.value

        error = asyncio.run(run())
        assert error.code is ConnectorErrorCode.HTTP_ERROR
        assert calls == ["https://api.example.test/hospitals/ref"]

    def test_oversized_response_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * 64)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def run() -> HospitalConnectorError:
            policy = AuthorizationPolicy.from_config(
                authorized=True,
                allowed_hosts=["api.example.test"],
                max_response_bytes=10,
            )
            connector = HospitalRestJsonConnector(
                "ref",
                base_url="https://api.example.test",
                authorization=policy,
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
            with pytest.raises(HospitalConnectorError) as exc_info:
                await connector.acquire()
            await client.aclose()
            return exc_info.value

        error = asyncio.run(run())
        assert error.code is ConnectorErrorCode.PAYLOAD_TOO_LARGE
        assert error.permanent is True

    def test_unexpected_content_type_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def run() -> HospitalConnectorError:
            policy = AuthorizationPolicy.from_config(
                authorized=True,
                allowed_hosts=["api.example.test"],
            )
            connector = HospitalRestJsonConnector(
                "ref",
                base_url="https://api.example.test",
                authorization=policy,
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
            with pytest.raises(HospitalConnectorError) as exc_info:
                await connector.acquire()
            await client.aclose()
            return exc_info.value

        error = asyncio.run(run())
        assert error.code is ConnectorErrorCode.UNSUPPORTED_CONTENT

    def test_json_content_type_with_garbage_body_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"not json at all",
                headers={"content-type": "application/json"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def run() -> HospitalConnectorError:
            policy = AuthorizationPolicy.from_config(
                authorized=True,
                allowed_hosts=["api.example.test"],
            )
            connector = HospitalRestJsonConnector(
                "ref",
                base_url="https://api.example.test",
                authorization=policy,
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
            with pytest.raises(HospitalConnectorError) as exc_info:
                await connector.acquire()
            await client.aclose()
            return exc_info.value

        error = asyncio.run(run())
        assert error.code is ConnectorErrorCode.PARSE_ERROR

    def test_non_object_json_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["not", "an", "object"])

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def run() -> HospitalConnectorError:
            policy = AuthorizationPolicy.from_config(
                authorized=True,
                allowed_hosts=["api.example.test"],
            )
            connector = HospitalRestJsonConnector(
                "ref",
                base_url="https://api.example.test",
                authorization=policy,
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
            with pytest.raises(HospitalConnectorError) as exc_info:
                await connector.acquire()
            await client.aclose()
            return exc_info.value

        error = asyncio.run(run())
        assert error.code is ConnectorErrorCode.VALIDATION_ERROR


class TestRegistry:
    def test_disabled_integration_rejected(self) -> None:
        integration = _integration(enabled=False)
        with pytest.raises(ConnectorUnauthorizedError):
            build_connector(integration)

    def test_unknown_connector_type_rejected(self) -> None:
        integration = _integration(connector_type="ftp_share")
        with pytest.raises(HospitalConnectorError) as exc_info:
            build_connector(integration)
        assert exc_info.value.code is ConnectorErrorCode.INVALID_CONFIGURATION

    def test_live_connector_requires_allowlist(self) -> None:
        integration = _integration(
            connector_type="hospital_rest_json",
            base_url="https://api.example.test",
            config={},
        )
        with pytest.raises(HospitalConnectorError) as exc_info:
            build_connector(integration)
        assert "allowlist" in str(exc_info.value)

    def test_live_connector_rejects_unallowlisted_host(self) -> None:
        integration = _integration(
            connector_type="hospital_rest_json",
            base_url="https://evil.example.net",
            config={"allowed_hosts": ["api.example.test"]},
        )
        with pytest.raises(HospitalConnectorError) as exc_info:
            build_connector(integration)
        assert "allowlist" in str(exc_info.value)

    def test_live_connector_builds_with_allowlist(self) -> None:
        integration = _integration(
            connector_type="hospital_rest_json",
            base_url="https://api.example.test",
            config={"allowed_hosts": ["api.example.test"]},
        )
        connector = build_connector(integration)
        assert connector.connector_type == "hospital_rest_json"
        assert connector.authorization_status == "AUTHORIZED"  # type: ignore[attr-defined]

    def test_mock_integration_builds_simulated_connector(self) -> None:
        integration = _integration(config={"payload_variant": "changed"})
        connector = build_connector(integration)
        assert connector.connector_type == "mock_hospital_api_simulated"
        assert connector.simulated is True  # type: ignore[attr-defined]

    def test_mock_integration_requires_configured_status(self) -> None:
        integration = _integration(status=IntegrationStatus.NOT_CONFIGURED)
        with pytest.raises(HospitalConnectorError):
            build_connector(integration)

    def test_policy_marks_unconfigured_integration_unauthorized(self) -> None:
        integration = _integration(status=IntegrationStatus.NOT_CONFIGURED)
        policy = build_policy_for_integration(integration)
        assert policy.status == "UNAUTHORIZED"

    def test_source_kind_mapping(self) -> None:
        assert (
            source_kind_for(_integration(connector_type="hospital_rest_json"))
            == SnapshotSourceKind.HOSPITAL_API.value
        )
        assert (
            source_kind_for(_integration()) == SnapshotSourceKind.MOCK_HOSPITAL_API_SIMULATED.value
        )
