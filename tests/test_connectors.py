"""P21 authorized source connector framework tests.

Covers A–AC of the P21 plan: website backward compatibility, authorized
API/feed success paths, fail-closed unauthorized behaviour (no network),
credential hygiene, SSRF/allowlist/timeout/HTTP/parse/size guards, typed
acquisition results, registry resolution, orchestration/monitoring
integration, mock authorized connector, CLI/API/UI surfaces, clean-room DB,
and regression of the existing suite.

All network access is mocked (httpx.MockTransport / in-process fixtures).
No real hospital systems are contacted.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.cli import main
from hospital_extractor.mockapi.app import create_mock_app
from hospital_extractor.models.base import utc_now
from hospital_extractor.monitoring.models import MonitoringPolicyStatus, ScheduleFrequency
from hospital_extractor.monitoring.service import (
    create_policy,
    list_policies,
    run_monitoring_cycle,
)
from hospital_extractor.orchestration.models import (
    OrchestrationRun,
    OrchestrationStage,
    OrchestrationStatus,
)
from hospital_extractor.sources import (
    AcquisitionResult,
    AuthorizationPolicy,
    ConnectorError,
    ConnectorErrorCode,
    ConnectorUnauthorizedError,
    CredentialRef,
    HospitalAPIDataSource,
    JSONFeedDataSource,
    MockAuthorizedApiConnector,
    MockScenario,
    SourceConfig,
    SourceRegistry,
    WebsiteDataSource,
    harvest,
)
from hospital_extractor.sources.api_mapping import map_api_payload_to_dataset
from hospital_extractor.sources.authorization import SsrfError, assert_safe_url
from hospital_extractor.sources.base import SourceFetchError, SourceIdentity
from hospital_extractor.sources.models import SourceType
from hospital_extractor.sources.pipeline import HarvestError
from hospital_extractor.sources.registry import (
    SourceDefinition,
    load_sources_config,
)
from hospital_extractor.sources.registry import (
    get_default_registry as default_registry,
)
from hospital_extractor.storage import HospitalDatabase
from hospital_extractor.utils.hashing import sha256_bytes

DEMO_JSON: dict[str, Any] = {
    "hospital": {"hospital_id": "h1", "name": "Demo Hospital", "bed_count": 100},
    "contact": {"city": "Testville", "phone": "+1-000-000-0000"},
    "departments": [{"name": "Cardiology"}],
    "specialities": [{"name": "Cardiology"}],
    "doctors": [{"name": "Dr. A", "speciality": "Cardiology"}],
}

API_BASE = "https://api.example.in"


def _ok_transport(payload: dict[str, Any] | None = None) -> httpx.MockTransport:
    body = payload if payload is not None else DEMO_JSON

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _client_for(transport: httpx.MockTransport, base: str = API_BASE) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport, base_url=base)


def _authorized_policy(
    *,
    hosts: tuple[str, ...] = ("api.example.in",),
    credential_env: str | None = None,
    credential_required: bool = False,
    allow_private: bool = False,
    timeout: float = 5.0,
    max_bytes: int = 5_000_000,
) -> AuthorizationPolicy:
    return AuthorizationPolicy.from_config(
        authorized=True,
        allowed_hosts=hosts,
        allow_private_hosts=allow_private,
        credential_env=credential_env,
        credential_required=credential_required,
        timeout_seconds=timeout,
        max_response_bytes=max_bytes,
        provider="Example Provider",
    )


def _api_source(
    transport: httpx.MockTransport | None = None,
    *,
    authorization: AuthorizationPolicy | None = None,
    hospital_ref: str = "h1",
) -> HospitalAPIDataSource:
    return HospitalAPIDataSource(
        hospital_ref=hospital_ref,
        base_url=API_BASE,
        provider="Example Hospital",
        client=_client_for(transport or _ok_transport()),
        authorization=authorization,
    )


# ---------------------------------------------------------------------------
# A. Website connector backward compatibility
# ---------------------------------------------------------------------------


class TestWebsiteBackwardCompat:
    def test_website_fetch_still_returns_canonical_dataset(
        self, pages: list[tuple[str, str]], tmp_path: Path
    ) -> None:
        import functools
        import http.server
        import threading
        from urllib.parse import urlsplit

        site_dir = tmp_path / "site"
        site_dir.mkdir(parents=True)
        for url, html in pages:
            path = urlsplit(url).path
            last = path.rstrip("/").rsplit("/", 1)[-1] or "index"
            (site_dir / f"{last}.html").write_text(html, encoding="utf-8")
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(site_dir)
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            source = WebsiteDataSource(
                identity=SourceIdentity(hospital_ref=url),
                output_dir=tmp_path / "crawl_out",
            )
            dataset = asyncio.run(source.fetch())
        finally:
            server.shutdown()
            thread.join(timeout=5)
        assert dataset.source is not None
        assert dataset.source.source_type is SourceType.WEBSITE
        assert dataset.hospital is not None
        assert "City Hospital" in (dataset.hospital.name or "")

    def test_website_acquire_returns_bounded_result(
        self, pages: list[tuple[str, str]], tmp_path: Path
    ) -> None:
        import functools
        import http.server
        import threading
        from urllib.parse import urlsplit

        site_dir = tmp_path / "site2"
        site_dir.mkdir(parents=True)
        for url, html in pages:
            path = urlsplit(url).path
            last = path.rstrip("/").rsplit("/", 1)[-1] or "index"
            (site_dir / f"{last}.html").write_text(html, encoding="utf-8")
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(site_dir)
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            source = WebsiteDataSource(
                identity=SourceIdentity(hospital_ref=url),
                output_dir=tmp_path / "crawl_out2",
            )
            result = asyncio.run(source.acquire())
        finally:
            server.shutdown()
            thread.join(timeout=5)
        assert isinstance(result, AcquisitionResult)
        assert result.source_type is SourceType.WEBSITE
        assert result.content_hash
        assert result.payload is None  # page corpus stays out of the shared result
        assert result.evidence.get("acquisition_method") == "website_crawl"

    def test_website_acquire_invalid_url_is_typed_error(self) -> None:
        source = WebsiteDataSource(identity=SourceIdentity(hospital_ref="not a url"))
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.INVALID_CONFIGURATION
        assert excinfo.value.permanent

    def test_website_authorization_status_is_public_crawl(self) -> None:
        source = WebsiteDataSource(identity=SourceIdentity(hospital_ref="https://x.in"))
        assert source.authorization_status == "PUBLIC_CRAWL"

    def test_website_fetch_invalid_url_still_raises(self) -> None:
        source = WebsiteDataSource(identity=SourceIdentity(hospital_ref="not a url"))
        with pytest.raises(SourceFetchError):
            asyncio.run(source.fetch())


# ---------------------------------------------------------------------------
# B. API connector success (authorized + mock transport)
# ---------------------------------------------------------------------------


class TestAuthorizedApiSuccess:
    def test_acquire_returns_typed_result(self) -> None:
        source = _api_source(authorization=_authorized_policy())
        result = asyncio.run(source.acquire())
        assert isinstance(result, AcquisitionResult)
        assert result.source_type is SourceType.HOSPITAL_API
        assert result.status_code == 200
        # Content hash is over the raw response body (httpx JSON encoding).
        assert len(result.content_hash) == 64
        assert result.payload == DEMO_JSON
        assert result.evidence.get("authorization") == "AUTHORIZED"
        assert result.safe_dict()["status_code"] == 200

    def test_fetch_maps_payload_to_dataset_with_provenance(self) -> None:
        source = _api_source(authorization=_authorized_policy())
        dataset = asyncio.run(source.fetch())
        assert dataset.hospital is not None
        assert dataset.hospital.name == "Demo Hospital"
        assert dataset.source is not None
        assert dataset.source.source_type is SourceType.HOSPITAL_API
        meta = dataset.source.raw_payload_metadata
        assert meta.get("content_hash")
        assert meta.get("status_code") == 200
        assert meta.get("acquisition_method") == "http_get"

    def test_hospital_identity_is_preserved(self) -> None:
        source = _api_source(hospital_ref="north-star")
        assert source.identity().hospital_ref == "north-star"

    def test_authorization_status_property(self) -> None:
        assert _api_source(authorization=_authorized_policy()).authorization_status == (
            "AUTHORIZED"
        )
        assert _api_source(authorization=None).authorization_status == "UNRESTRICTED"
        assert _api_source(
            authorization=AuthorizationPolicy.deny_all()
        ).authorization_status == "UNAUTHORIZED"

    def test_direct_construction_without_policy_still_works(self) -> None:
        """Backward-compat: unit tests may construct without a policy."""
        source = HospitalAPIDataSource(
            hospital_ref="h1",
            base_url=API_BASE,
            client=_client_for(_ok_transport()),
        )
        dataset = asyncio.run(source.fetch())
        assert dataset.hospital is not None


# ---------------------------------------------------------------------------
# C. Feed connector success (local + authorized remote)
# ---------------------------------------------------------------------------


class TestFeedSuccess:
    def test_local_feed_requires_no_authorization(self, tmp_path: Path) -> None:
        feed_path = tmp_path / "feed.json"
        feed_payload = {"hospital": {"hospital_id": "h1", "name": "Feed Hos"}}
        feed_path.write_text(json.dumps(feed_payload), encoding="utf-8")
        source = JSONFeedDataSource(str(feed_path))
        assert source.authorization_status == "LOCAL_FILE"
        dataset = asyncio.run(source.fetch())
        assert dataset.hospital is not None
        assert dataset.hospital.name == "Feed Hos"
        assert dataset.source is not None
        assert dataset.source.source_type is SourceType.JSON_FEED

    def test_remote_feed_authorized_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=DEMO_JSON)

        client = _client_for(httpx.MockTransport(handler), "https://feeds.example.in")
        source = JSONFeedDataSource(
            "https://feeds.example.in/hospital.json",
            authorization=_authorized_policy(hosts=("feeds.example.in",)),
            client=client,
        )
        assert source.is_remote
        assert source.authorization_status == "AUTHORIZED"
        result = asyncio.run(source.acquire())
        assert result.status_code == 200
        assert result.payload == DEMO_JSON

    def test_remote_feed_fetch_maps_structured_payload(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=DEMO_JSON)

        client = _client_for(httpx.MockTransport(handler), "https://feeds.example.in")
        source = JSONFeedDataSource(
            "https://feeds.example.in/hospital.json",
            authorization=_authorized_policy(hosts=("feeds.example.in",)),
            client=client,
        )
        dataset = asyncio.run(source.fetch())
        assert dataset.hospital is not None
        assert dataset.source is not None
        assert dataset.source.source_type is SourceType.JSON_FEED


# ---------------------------------------------------------------------------
# D–E. Unauthorized fail-closed (no external request)
# ---------------------------------------------------------------------------


def _must_not_connect_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unauthorized connector attempted network I/O: {request.url}")

    return httpx.MockTransport(handler)


class TestFailClosedUnauthorized:
    def test_api_unauthorized_policy_fails_before_network(self) -> None:
        source = _api_source(
            transport=_must_not_connect_transport(),
            authorization=AuthorizationPolicy.deny_all(),
        )
        with pytest.raises(ConnectorUnauthorizedError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.UNAUTHORIZED
        assert not excinfo.value.retryable

    def test_api_unauthorized_fetch_fails_before_network(self) -> None:
        source = _api_source(
            transport=_must_not_connect_transport(),
            authorization=AuthorizationPolicy.deny_all(),
        )
        with pytest.raises(ConnectorUnauthorizedError):
            asyncio.run(source.fetch())

    def test_api_registry_build_unauthorized_hospital_api(self) -> None:
        registry = default_registry()
        source = registry.build(
            SourceConfig(source_type="hospital_api", hospital_ref="h1", authorized=False)
        )
        # attach a must-not-connect client to prove no socket is opened
        assert isinstance(source, HospitalAPIDataSource)
        source._client = _client_for(_must_not_connect_transport())  # type: ignore[attr-defined]
        with pytest.raises(ConnectorUnauthorizedError):
            asyncio.run(source.acquire())

    def test_remote_feed_without_policy_fails_before_network(self) -> None:
        source = JSONFeedDataSource(
            "https://feeds.example.in/hospital.json",
            client=_client_for(_must_not_connect_transport(), "https://feeds.example.in"),
        )
        with pytest.raises(ConnectorUnauthorizedError):
            asyncio.run(source.acquire())

    def test_remote_feed_denied_policy_fails_before_network(self) -> None:
        source = JSONFeedDataSource(
            "https://feeds.example.in/hospital.json",
            authorization=AuthorizationPolicy.deny_all(),
            client=_client_for(_must_not_connect_transport(), "https://feeds.example.in"),
        )
        with pytest.raises(ConnectorUnauthorizedError):
            asyncio.run(source.acquire())

    def test_remote_feed_allowlist_mismatch_fails_before_network(self) -> None:
        source = JSONFeedDataSource(
            "https://evil.example.in/hospital.json",
            authorization=_authorized_policy(hosts=("feeds.example.in",)),
            client=_client_for(
                _must_not_connect_transport(), "https://evil.example.in"
            ),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.UNAUTHORIZED

    def test_harvest_hospital_api_unauthorized_raises_harvest_error(self) -> None:
        with pytest.raises(HarvestError) as excinfo:
            asyncio.run(harvest(["h1"], source_type="hospital_api"))
        assert "unauthorized" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# F. Credentials never stored / logged / serialized
# ---------------------------------------------------------------------------


class TestCredentialHygiene:
    def test_credential_ref_repr_and_describe_hide_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_CONNECTOR_TEST_TOKEN", "super-secret-token-value")
        ref = CredentialRef("MEDCOMPARE_CONNECTOR_TEST_TOKEN")
        assert ref.resolve() == "super-secret-token-value"
        assert "super-secret" not in repr(ref)
        assert "super-secret" not in ref.describe()
        assert ref.describe() == "env:MEDCOMPARE_CONNECTOR_TEST_TOKEN"

    def test_policy_safe_dict_exposes_env_name_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_CONNECTOR_API_TOKEN", "tok-abc-123")
        policy = _authorized_policy(credential_env="MEDCOMPARE_CONNECTOR_API_TOKEN")
        safe = policy.safe_dict()
        assert safe["credential_ref"] == "env:MEDCOMPARE_CONNECTOR_API_TOKEN"
        blob = json.dumps(safe)
        assert "tok-abc-123" not in blob

    def test_api_acquire_does_not_put_secret_in_result_or_evidence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_CONNECTOR_API_TOKEN", "tok-secret-xyz")
        seen_headers: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers.append(request.headers.get("authorization", ""))
            return httpx.Response(200, json=DEMO_JSON)

        source = HospitalAPIDataSource(
            hospital_ref="h1",
            base_url=API_BASE,
            client=_client_for(httpx.MockTransport(handler)),
            authorization=_authorized_policy(credential_env="MEDCOMPARE_CONNECTOR_API_TOKEN"),
        )
        result = asyncio.run(source.acquire())
        # Credential was attached at request time only.
        assert seen_headers == ["Bearer tok-secret-xyz"]
        # Never appears in the typed result.
        assert "tok-secret-xyz" not in json.dumps(result.safe_dict())
        assert "tok-secret-xyz" not in str(result.evidence)
        assert "tok-secret-xyz" not in repr(result)

    def test_api_missing_credential_env_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MEDCOMPARE_CONNECTOR_MISSING_TOKEN", raising=False)
        source = _api_source(
            transport=_must_not_connect_transport(),
            authorization=_authorized_policy(
                credential_env="MEDCOMPARE_CONNECTOR_MISSING_TOKEN",
                credential_required=True,
            ),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.INVALID_CONFIGURATION
        assert not excinfo.value.retryable

    def test_secret_not_persisted_to_database(
        self, monkeypatch: pytest.MonkeyPatch, temp_db: HospitalDatabase
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_CONNECTOR_DB_TOKEN", "db-secret-token")
        source = _api_source(
            authorization=_authorized_policy(credential_env="MEDCOMPARE_CONNECTOR_DB_TOKEN")
        )
        dataset = asyncio.run(source.fetch())
        temp_db.save_dataset(dataset)
        # Raw SQLite dump must never contain the secret.
        rows = temp_db._connection.execute("SELECT * FROM datasets").fetchall()
        blob = " ".join(
            " ".join(str(v) for v in tuple(row)) for row in rows  # type: ignore[arg-type]
        )
        assert "db-secret-token" not in blob
        loaded = temp_db.list_datasets()
        assert loaded
        assert "db-secret-token" not in json.dumps(
            [s.model_dump(mode="json") for s in loaded], default=str
        )

    def test_secret_never_logged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEDCOMPARE_CONNECTOR_LOG_TOKEN", "log-secret-token")
        records: list[str] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        handler = _Capture()
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            policy = _authorized_policy(credential_env="MEDCOMPARE_CONNECTOR_LOG_TOKEN")
            source = _api_source(authorization=policy)
            asyncio.run(source.fetch())
            _ = policy.safe_dict()
            _ = CredentialRef("MEDCOMPARE_CONNECTOR_LOG_TOKEN").describe()
        finally:
            root.removeHandler(handler)
        assert "log-secret-token" not in "\n".join(records)


# ---------------------------------------------------------------------------
# G–H. Allowlist + SSRF private-host guards
# ---------------------------------------------------------------------------


class TestSsrfGuards:
    def test_private_ip_denied_by_default(self) -> None:
        policy = AuthorizationPolicy.from_config(
            authorized=True, allowed_hosts=("127.0.0.1",), allow_private_hosts=False
        )
        decision = policy.check_url("https://127.0.0.1/api")
        assert not decision.allowed
        assert decision.code == "ssrf"

    def test_localhost_denied_by_default(self) -> None:
        policy = AuthorizationPolicy.from_config(authorized=True, allowed_hosts=("localhost",))
        decision = policy.check_url("http://localhost/api")
        assert not decision.allowed

    def test_private_ip_allowed_when_explicitly_permitted(self) -> None:
        policy = AuthorizationPolicy.from_config(
            authorized=True,
            allowed_hosts=("127.0.0.1",),
            allow_private_hosts=True,
        )
        assert policy.check_url("http://127.0.0.1:8001/api").allowed

    def test_host_not_in_allowlist_denied(self) -> None:
        policy = _authorized_policy(hosts=("api.example.in",))
        decision = policy.check_url("https://evil.example.in/api")
        assert not decision.allowed
        assert decision.code == "ssrf"
        assert "allowlist" in decision.reason

    def test_wildcard_allowlist_matches_subdomain(self) -> None:
        policy = AuthorizationPolicy.from_config(
            authorized=True, allowed_hosts=("*.example.in",)
        )
        assert policy.check_url("https://api.example.in/x").allowed
        assert not policy.check_url("https://example.in.evil.com/x").allowed

    def test_disallowed_scheme_denied(self) -> None:
        policy = _authorized_policy()
        decision = policy.check_url("ftp://api.example.in/x")
        assert not decision.allowed
        assert decision.code == "ssrf"

    def test_assert_safe_url_raises_ssrf_error(self) -> None:
        with pytest.raises(SsrfError):
            assert_safe_url(
                "https://evil.example.in/x",
                AuthorizationPolicy.from_config(
                    authorized=True, allowed_hosts=("api.example.in",)
                ),
            )

    def test_unauthorized_policy_never_allows_url(self) -> None:
        policy = AuthorizationPolicy.deny_all()
        decision = policy.check_url("https://api.example.in/x")
        assert not decision.allowed
        assert decision.code == "unauthorized"

    def test_api_acquire_host_not_allowlisted_fails_before_network(self) -> None:
        source = HospitalAPIDataSource(
            hospital_ref="h1",
            base_url="https://evil.example.in",
            client=_client_for(_must_not_connect_transport(), "https://evil.example.in"),
            authorization=_authorized_policy(hosts=("api.example.in",)),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.UNAUTHORIZED
        assert not excinfo.value.retryable


# ---------------------------------------------------------------------------
# I–M. Timeout, HTTP error, malformed JSON/feed, oversized payload
# ---------------------------------------------------------------------------


class TestNetworkBoundaries:
    def test_timeout_maps_to_retryable_timeout_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        source = _api_source(
            transport=httpx.MockTransport(handler),
            authorization=_authorized_policy(),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.TIMEOUT
        assert excinfo.value.retryable

    def test_http_404_is_permanent_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        source = _api_source(
            transport=httpx.MockTransport(handler),
            authorization=_authorized_policy(),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.HTTP_ERROR
        assert not excinfo.value.retryable

    def test_http_500_is_retryable_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        source = _api_source(
            transport=httpx.MockTransport(handler),
            authorization=_authorized_policy(),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.HTTP_ERROR
        assert excinfo.value.retryable

    def test_connect_error_is_retryable_network_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        source = _api_source(
            transport=httpx.MockTransport(handler),
            authorization=_authorized_policy(),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.NETWORK_ERROR
        assert excinfo.value.retryable

    def test_non_json_body_is_permanent_parse_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        source = _api_source(
            transport=httpx.MockTransport(handler),
            authorization=_authorized_policy(),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.PARSE_ERROR
        assert not excinfo.value.retryable

    def test_json_root_not_object_is_validation_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[1, 2, 3])

        source = _api_source(
            transport=httpx.MockTransport(handler),
            authorization=_authorized_policy(),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.VALIDATION_ERROR

    def test_oversized_response_is_payload_too_large(self) -> None:
        big = {"hospital": {"hospital_id": "h1", "name": "X" * 5000}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=big)

        source = HospitalAPIDataSource(
            hospital_ref="h1",
            base_url=API_BASE,
            client=_client_for(httpx.MockTransport(handler)),
            authorization=_authorized_policy(max_bytes=100),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.PAYLOAD_TOO_LARGE
        assert not excinfo.value.retryable

    def test_local_feed_malformed_json_is_parse_error(self, tmp_path: Path) -> None:
        feed = tmp_path / "bad.json"
        feed.write_text("{not-json", encoding="utf-8")
        source = JSONFeedDataSource(str(feed))
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.PARSE_ERROR

    def test_local_feed_missing_file_is_invalid_configuration(self, tmp_path: Path) -> None:
        source = JSONFeedDataSource(str(tmp_path / "missing.json"))
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.INVALID_CONFIGURATION

    def test_local_feed_oversized_is_payload_too_large(self, tmp_path: Path) -> None:
        feed = tmp_path / "big.json"
        feed.write_text(json.dumps({"hospital": {"name": "x" * 500}}), encoding="utf-8")
        source = JSONFeedDataSource(str(feed), max_response_bytes=10)
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.PAYLOAD_TOO_LARGE

    def test_remote_feed_malformed_json_is_parse_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not-json")

        source = JSONFeedDataSource(
            "https://feeds.example.in/x.json",
            authorization=_authorized_policy(hosts=("feeds.example.in",)),
            client=_client_for(httpx.MockTransport(handler), "https://feeds.example.in"),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.PARSE_ERROR

    def test_remote_feed_timeout_is_retryable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("slow")

        source = JSONFeedDataSource(
            "https://feeds.example.in/x.json",
            authorization=_authorized_policy(hosts=("feeds.example.in",)),
            client=_client_for(httpx.MockTransport(handler), "https://feeds.example.in"),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.TIMEOUT
        assert excinfo.value.retryable

    def test_feed_payload_without_hospital_key_is_validation_error(
        self, tmp_path: Path
    ) -> None:
        feed = tmp_path / "emptyish.json"
        feed.write_text(json.dumps({"departments": []}), encoding="utf-8")
        source = JSONFeedDataSource(str(feed))
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.fetch())
        assert excinfo.value.code is ConnectorErrorCode.VALIDATION_ERROR


# ---------------------------------------------------------------------------
# N–O. Content hash + provenance
# ---------------------------------------------------------------------------


class TestAcquisitionResult:
    def test_from_bytes_hashes_body(self) -> None:
        body = b'{"ok": true}'
        result = AcquisitionResult.from_bytes(
            source_type=SourceType.HOSPITAL_API,
            provider=None,
            source_ref="h1",
            requested_url=f"{API_BASE}/hospital/h1",
            body=body,
            status_code=200,
            content_type="application/json",
            payload={"ok": True},
        )
        assert result.content_hash == sha256_bytes(body)
        assert result.raw_size_bytes == len(body)
        assert result.safe_dict()["content_hash"] == result.content_hash

    def test_from_text_hashes_utf8(self) -> None:
        result = AcquisitionResult.from_text(
            source_type=SourceType.JSON_FEED,
            provider=None,
            source_ref="f",
            requested_url="/tmp/f.json",
            text="hello",
        )
        assert result.content_hash == sha256_bytes(b"hello")
        assert result.payload == "hello"

    def test_empty_result_is_deterministic_hash(self) -> None:
        a = AcquisitionResult.empty(source_type=SourceType.WEBSITE, source_ref="x")
        b = AcquisitionResult.empty(source_type=SourceType.WEBSITE, source_ref="x")
        assert a.content_hash == b.content_hash
        assert a.content_hash

    def test_safe_dict_never_contains_payload_or_secrets(self) -> None:
        result = AcquisitionResult.from_text(
            source_type=SourceType.JSON_FEED,
            provider=None,
            source_ref="f",
            requested_url="/tmp/f.json",
            text="payload-stays-out",
            payload={"token": "should-not-serialize"},
        )
        safe = result.safe_dict()
        assert "payload" not in safe
        assert "token" not in json.dumps(safe)

    def test_error_message_prefixes_code(self) -> None:
        err = ConnectorError("boom", code=ConnectorErrorCode.TIMEOUT)
        assert str(err).startswith("[timeout]")
        assert isinstance(err, SourceFetchError)


# ---------------------------------------------------------------------------
# P–R. Registry resolution + hospital identity
# ---------------------------------------------------------------------------


class TestRegistryResolution:
    def test_default_registry_registers_all_types(self) -> None:
        registry = default_registry()
        assert registry.source_types() == [
            "hospital_api",
            "json_feed",
            "mock_api",
            "website",
        ]

    def test_connector_for_each_registered_type(self) -> None:
        from hospital_extractor.sources.hospital_api import HospitalAPIDataSource as H
        from hospital_extractor.sources.json_feed import JSONFeedDataSource as J
        from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource as M
        from hospital_extractor.sources.website import WebsiteDataSource as W

        registry = default_registry()
        assert registry.connector_for("hospital_api") is H
        assert registry.connector_for("json_feed") is J
        assert registry.connector_for("mock_api") is M
        assert registry.connector_for("website") is W

    def test_hospital_api_definition_requires_authorization(self) -> None:
        definition = default_registry().definition("hospital_api")
        assert definition.requires_authorization is True
        assert definition.simulated is False

    def test_mock_api_definition_is_simulated(self) -> None:
        definition = default_registry().definition("mock_api")
        assert definition.simulated is True
        assert definition.requires_authorization is False

    def test_unknown_source_type_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            default_registry().connector_for("not-a-source")
        with pytest.raises(KeyError):
            default_registry().definition("not-a-source")

    def test_hospital_identity_roundtrip_via_registry_build(self) -> None:
        source = default_registry().build(
            SourceConfig(source_type="hospital_api", hospital_ref="ref-42")
        )
        assert source.identity().hospital_ref == "ref-42"

    def test_source_config_authorization_policy_fail_closed(self) -> None:
        config = SourceConfig(source_type="hospital_api", hospital_ref="h1", authorized=False)
        policy = config.authorization_policy(requires_authorization=True)
        assert policy.authorized is False
        assert policy.status == "UNAUTHORIZED"
        assert not policy.check_url("https://api.example.in/x").allowed

    def test_source_config_authorization_policy_authorized(self) -> None:
        config = SourceConfig(
            source_type="hospital_api",
            hospital_ref="h1",
            authorized=True,
            allowed_hosts=("api.example.in",),
        )
        policy = config.authorization_policy(requires_authorization=True)
        assert policy.authorized is True
        assert policy.check_url("https://api.example.in/x").allowed

    def test_source_config_credential_env_checked_at_request_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_CONNECTOR_SC_TEST", "tok")
        config = SourceConfig(
            source_type="hospital_api",
            hospital_ref="h1",
            authorized=True,
            allowed_hosts=("api.example.in",),
            credential_env="MEDCOMPARE_CONNECTOR_SC_TEST",
        )
        policy = config.authorization_policy(requires_authorization=True)
        assert policy.check_url("https://api.example.in/x").allowed

    def test_load_sources_config_parses_auth_fields(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "sources.toml"
        toml_path.write_text(
            "[[sources]]\n"
            'name = "auth-api"\n'
            'source_type = "hospital_api"\n'
            'hospital_ref = "h1"\n'
            'base_url = "https://api.example.in"\n'
            "authorized = true\n"
            'allowed_hosts = ["api.example.in"]\n'
            'credential_env = "MEDCOMPARE_CONNECTOR_AUTH_API_TOKEN"\n'
            "credential_required = true\n"
            "timeout_seconds = 12.5\n"
            "max_response_bytes = 123456\n",
            encoding="utf-8",
        )
        configs = load_sources_config(toml_path)
        assert len(configs) == 1
        cfg = configs[0]
        assert cfg.authorized is True
        assert cfg.allowed_hosts == ("api.example.in",)
        assert cfg.credential_env == "MEDCOMPARE_CONNECTOR_AUTH_API_TOKEN"
        assert cfg.credential_required is True
        assert cfg.timeout_seconds == 12.5
        assert cfg.max_response_bytes == 123456


# ---------------------------------------------------------------------------
# Mock authorized connector (scenarios + E2E)
# ---------------------------------------------------------------------------


class TestMockAuthorizedConnector:
    @pytest.mark.parametrize(
        ("scenario", "expected_beds"),
        [
            (MockScenario.SUCCESS, 100),
            (MockScenario.UNCHANGED, 100),
            (MockScenario.CHANGED, 120),
        ],
    )
    def test_success_scenarios_fetch_dataset(
        self, scenario: MockScenario, expected_beds: int
    ) -> None:
        source = MockAuthorizedApiConnector(
            scenario=scenario,
            authorization=_authorized_policy(
                hosts=("mock-authorized.example.test",)
            ),
        )
        dataset = asyncio.run(source.fetch())
        assert dataset.hospital is not None
        assert dataset.hospital.bed_count == expected_beds
        assert dataset.source is not None
        assert "SIMULATED" in (dataset.source.notes or "")
        assert dataset.source.raw_payload_metadata.get("simulated") is True
        assert dataset.source.raw_payload_metadata.get("scenario") == scenario.value

    def test_unauthorized_scenario_fails_closed(self) -> None:
        source = MockAuthorizedApiConnector(
            scenario=MockScenario.UNAUTHORIZED,
            authorization=_authorized_policy(hosts=("mock-authorized.example.test",)),
        )
        with pytest.raises(ConnectorUnauthorizedError):
            asyncio.run(source.acquire())

    def test_missing_policy_fails_closed(self) -> None:
        source = MockAuthorizedApiConnector(authorization=None)
        with pytest.raises(ConnectorUnauthorizedError):
            asyncio.run(source.acquire())

    def test_transient_failure_is_retryable(self) -> None:
        source = MockAuthorizedApiConnector(
            scenario=MockScenario.TRANSIENT_FAILURE,
            authorization=_authorized_policy(hosts=("mock-authorized.example.test",)),
        )
        with pytest.raises(ConnectorError) as excinfo:
            asyncio.run(source.acquire())
        assert excinfo.value.code is ConnectorErrorCode.NETWORK_ERROR
        assert excinfo.value.retryable

    def test_acquire_marks_simulated(self) -> None:
        source = MockAuthorizedApiConnector(
            authorization=_authorized_policy(hosts=("mock-authorized.example.test",))
        )
        result = asyncio.run(source.acquire())
        assert result.simulated is True
        assert any("SIMULATED" in w for w in result.warnings)
        assert result.evidence.get("simulated") is True
        assert result.evidence.get("authorization") == "AUTHORIZED"

    def test_mock_is_always_marked_simulated_property(self) -> None:
        source = MockAuthorizedApiConnector()
        assert source.simulated is True
        assert source.source_type is SourceType.HOSPITAL_API


# ---------------------------------------------------------------------------
# S. P19-E orchestration capture via harvest (website + mock API)
# ---------------------------------------------------------------------------


def _make_run(source_type: str = "website", ref: str = "https://example.test") -> OrchestrationRun:
    now = utc_now()
    return OrchestrationRun(
        run_id="run_p21test",
        hospital_id="h_p21",
        source_type=source_type,
        trigger_ref=ref,
        status=OrchestrationStatus.RUNNING,
        current_stage=OrchestrationStage.PENDING,
        created_at=now,
        updated_at=now,
        created_by="system",
        idempotency_key="p21-orch",
    )


class TestOrchestrationCaptureIntegration:
    def test_capture_stage_harvests_website_via_registry(
        self,
        tmp_path: Path,
        pages: list[tuple[str, str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import functools
        import http.server
        import threading
        from urllib.parse import urlsplit

        site_dir = tmp_path / "site"
        site_dir.mkdir(parents=True)
        for url, html in pages:
            path = urlsplit(url).path
            last = path.rstrip("/").rsplit("/", 1)[-1] or "index"
            (site_dir / f"{last}.html").write_text(html, encoding="utf-8")
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(site_dir)
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        db_path = tmp_path / "orch.db"
        monkeypatch.setattr(
            "hospital_extractor.orchestration.service.default_database_path",
            lambda: db_path,
        )
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            from hospital_extractor.orchestration.service import OrchestrationService

            with HospitalDatabase(db_path) as db:
                service = OrchestrationService(db)
                run = _make_run("website", url)
                snapshot_id, change_set_id = asyncio.run(
                    service._run_capture_stage(run, "h_p21", "website", url, None)  # noqa: SLF001
                )
            assert snapshot_id
            assert change_set_id
            with HospitalDatabase(db_path) as db:
                snapshots = db.list_snapshots("h_p21")
            assert snapshots
            assert snapshots[0].source_type == "website"
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def test_capture_stage_harvests_mock_api_via_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = SourceRegistry()

        def factory(hospital_ref: str, **_: Any) -> Any:
            transport = httpx.ASGITransport(app=create_mock_app())
            from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource

            return MockHospitalAPIDataSource(
                hospital_id=hospital_ref,
                client=httpx.AsyncClient(transport=transport, base_url="http://testserver"),
            )

        registry.register(SourceType.MOCK_API, factory)
        db_path = tmp_path / "orch_mock.db"
        monkeypatch.setattr(
            "hospital_extractor.orchestration.service.default_database_path",
            lambda: db_path,
        )
        from hospital_extractor.orchestration.service import OrchestrationService

        with HospitalDatabase(db_path) as db:
            service = OrchestrationService(db, registry=registry)
            run = _make_run("mock_api", "demo-northstar")
            snapshot_id, _ = asyncio.run(
                service._run_capture_stage(  # noqa: SLF001
                    run, "h_p21", "mock_api", "demo-northstar", None
                )
            )
        assert snapshot_id
        with HospitalDatabase(db_path) as db:
            snapshots = db.list_snapshots("h_p21")
        assert snapshots
        assert snapshots[0].source_type == "mock_api"

    def test_capture_stage_hospital_api_unauthorized_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P19-E must not silently succeed when the API source is unauthorized."""
        db_path = tmp_path / "orch_unauth.db"
        monkeypatch.setattr(
            "hospital_extractor.orchestration.service.default_database_path",
            lambda: db_path,
        )
        from hospital_extractor.orchestration.service import OrchestrationService

        with HospitalDatabase(db_path) as db:
            service = OrchestrationService(db)
            run = _make_run("hospital_api", "h1")
            with pytest.raises(HarvestError) as excinfo:
                asyncio.run(
                    service._run_capture_stage(  # noqa: SLF001
                        run, "h_p21", "hospital_api", "h1", None
                    )
                )
        assert "unauthorized" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# T–U. P20 monitoring with API connector (authorized + unauthorized)
# ---------------------------------------------------------------------------


def _completed_run(source_type: str, ref: str) -> OrchestrationRun:
    now = utc_now()
    return OrchestrationRun(
        run_id="run_mon_api",
        hospital_id="h_mon_api",
        source_type=source_type,
        trigger_ref=ref,
        status=OrchestrationStatus.COMPLETED,
        current_stage=OrchestrationStage.COMPLETED,
        created_at=now,
        updated_at=now,
        created_by="system",
        idempotency_key="mon-api",
        change_set_status="changes_detected",
    )


class TestMonitoringWithApiConnector:
    def test_due_policy_runs_authorized_api_connector_orchestration(
        self, temp_db: HospitalDatabase
    ) -> None:
        """Authorized hospital_api policy completes through orchestrate hook."""
        create_policy(
            temp_db,
            hospital_id="h_mon_api",
            frequency=ScheduleFrequency.DAILY,
            source_type="hospital_api",
            target_ref="h1",
        )
        calls: list[dict[str, str]] = []

        async def orchestrate(
            db_path: object,
            *,
            hospital_id: str,
            source_type: str,
            target_ref: str,
            idempotency_key: str,
        ) -> OrchestrationRun:
            calls.append(
                {
                    "hospital_id": hospital_id,
                    "source_type": source_type,
                    "target_ref": target_ref,
                    "idempotency_key": idempotency_key,
                }
            )
            # Prove the authorized API connector path is reachable offline.
            source = _api_source(authorization=_authorized_policy())
            await source.fetch()
            return _completed_run(source_type, target_ref)

        processed = asyncio.run(
            run_monitoring_cycle(temp_db, orchestrate=orchestrate)
        )
        assert processed == 1
        assert calls and calls[0]["source_type"] == "hospital_api"
        refreshed = list_policies(temp_db, hospital_id="h_mon_api")
        assert refreshed
        # complete() schedules the next run (PENDING) and stamps success.
        assert refreshed[0].status is MonitoringPolicyStatus.PENDING
        assert refreshed[0].last_success_at is not None
        assert refreshed[0].last_error is None
        assert refreshed[0].last_orchestration_run_id == "run_mon_api"

    def test_unauthorized_api_policy_fails_closed(
        self, temp_db: HospitalDatabase
    ) -> None:
        """A hospital_api policy with no authorization fails; never succeeds."""
        create_policy(
            temp_db,
            hospital_id="h_mon_unauth",
            frequency=ScheduleFrequency.DAILY,
            source_type="hospital_api",
            target_ref="h1",
        )
        attempts: list[str] = []

        async def orchestrate(
            db_path: object,
            *,
            hospital_id: str,
            source_type: str,
            target_ref: str,
            idempotency_key: str,
        ) -> OrchestrationRun:
            attempts.append(source_type)
            # Mirror real behaviour: harvest with default (unauthorized) config.
            # Unauthorized is a permanent configuration error — no retries.
            try:
                await harvest([target_ref], source_type=source_type)
            except HarvestError as exc:
                from hospital_extractor.monitoring.service import MonitoringPermanentError

                raise MonitoringPermanentError(str(exc)) from exc
            raise AssertionError("unauthorized harvest must not succeed")

        processed = asyncio.run(run_monitoring_cycle(temp_db, orchestrate=orchestrate))
        assert processed == 1
        assert attempts == ["hospital_api"]
        refreshed = list_policies(temp_db, hospital_id="h_mon_unauth")
        assert refreshed
        assert refreshed[0].status is MonitoringPolicyStatus.FAILED
        assert "unauthorized" in (refreshed[0].last_error or "").lower()

    def test_authorized_mock_connector_through_harvest(
        self, temp_db: HospitalDatabase
    ) -> None:
        """Registry-built authorized mock connector harvests through pipeline."""
        registry = SourceRegistry()

        def factory(hospital_ref: str, **kwargs: Any) -> Any:
            return MockAuthorizedApiConnector(
                hospital_ref=hospital_ref,
                authorization=kwargs.get("authorization"),
                scenario=MockScenario.SUCCESS,
            )

        registry.register(
            SourceType.HOSPITAL_API,
            factory,
            definition=SourceDefinition(
                SourceType.HOSPITAL_API, simulated=False, requires_authorization=True
            ),
            connector_type=MockAuthorizedApiConnector,
        )
        results = asyncio.run(
            harvest(
                ["mock-authorized-1"],
                source_type="hospital_api",
                registry=registry,
                authorized=True,
                allowed_hosts=("mock-authorized.example.test",),
            )
        )
        assert len(results) == 1
        assert results[0].dataset.hospital is not None
        assert results[0].dataset.source is not None
        assert results[0].dataset.source.raw_payload_metadata.get("simulated") is True


# ---------------------------------------------------------------------------
# V. Retry compatibility (permanent vs retryable)
# ---------------------------------------------------------------------------


class TestRetryCompatibility:
    @pytest.mark.parametrize(
        ("code", "retryable"),
        [
            (ConnectorErrorCode.UNAUTHORIZED, False),
            (ConnectorErrorCode.INVALID_CONFIGURATION, False),
            (ConnectorErrorCode.UNSUPPORTED_CONTENT, False),
            (ConnectorErrorCode.VALIDATION_ERROR, False),
            (ConnectorErrorCode.PARSE_ERROR, False),
            (ConnectorErrorCode.NETWORK_ERROR, True),
            (ConnectorErrorCode.TIMEOUT, True),
            (ConnectorErrorCode.PAYLOAD_TOO_LARGE, False),
        ],
    )
    def test_code_retryability(self, code: ConnectorErrorCode, retryable: bool) -> None:
        err = ConnectorError("x", code=code)
        assert err.retryable is retryable
        assert err.permanent is (not retryable)

    def test_http_error_retryable_follows_status(self) -> None:
        assert ConnectorError("x", code=ConnectorErrorCode.HTTP_ERROR, retryable=True).retryable
        assert not ConnectorError(
            "x", code=ConnectorErrorCode.HTTP_ERROR, retryable=False
        ).retryable

    def test_connector_error_is_source_fetch_error(self) -> None:
        assert issubclass(ConnectorError, SourceFetchError)
        with pytest.raises(SourceFetchError):
            raise ConnectorError("boom", code=ConnectorErrorCode.NETWORK_ERROR)

    def test_unauthorized_error_is_not_retryable(self) -> None:
        err = ConnectorUnauthorizedError()
        assert err.code is ConnectorErrorCode.UNAUTHORIZED
        assert not err.retryable
        assert err.permanent


# ---------------------------------------------------------------------------
# W. CLI sources / connectors
# ---------------------------------------------------------------------------


class TestCliSourcesConnectors:
    def test_sources_definitions_lists_all_types(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["sources", "definitions"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hospital_api" in out
        assert "requires_authorization" in out
        assert "website" in out

    def test_sources_list_handles_empty_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["sources", "list"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Configured sources" in out

    def test_connectors_list(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["connectors", "list"])
        assert code == 0
        out = capsys.readouterr().out
        assert "HospitalAPIDataSource" in out
        assert "JSONFeedDataSource" in out
        assert "UNAUTHORIZED" in out  # hospital_api default status label

    def test_connectors_test_hospital_api_unauthorized_exit_1(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["connectors", "test", "hospital_api"])
        assert code == 1
        out = capsys.readouterr().out
        assert "UNAUTHORIZED" in out
        assert "fail closed" in out.lower()

    def test_connectors_test_website_offline_ok(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["connectors", "test", "website"])
        assert code == 0
        out = capsys.readouterr().out
        assert "OK (offline check" in out

    def test_connectors_test_mock_api_offline_ok(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["connectors", "test", "mock_api"])
        assert code == 0
        out = capsys.readouterr().out
        assert "SIMULATED" in out or "simulated" in out

    def test_connectors_test_json_feed_offline_ok(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["connectors", "test", "json_feed"])
        assert code == 0

    def test_connectors_test_fetch_without_ref_is_invalid(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # website requires no auth; --fetch without --ref fails configuration
        code = main(["connectors", "test", "website", "--fetch"])
        assert code == 2
        out = capsys.readouterr().out
        assert "INVALID_CONFIGURATION" in out or "--ref" in out

    def test_connectors_test_unauthorized_fetch_refused(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            ["connectors", "test", "hospital_api", "--ref", "h1", "--fetch"]
        )
        assert code == 1
        out = capsys.readouterr().out
        assert "UNAUTHORIZED" in out


# ---------------------------------------------------------------------------
# X. API sources / connectors endpoints
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path: Path) -> Any:
    app = create_app()
    app.state.medcompare_db_path = tmp_path / "sources-api.db"
    with TestClient(app) as client:
        yield client


class TestSourcesApi:
    def test_list_sources(self, api_client: TestClient) -> None:
        resp = api_client.get("/api/v1/sources")
        assert resp.status_code == 200
        sources = resp.json()["sources"]
        by_type = {s["source_type"]: s for s in sources}
        assert set(by_type) == {"website", "mock_api", "hospital_api", "json_feed"}
        assert by_type["hospital_api"]["requires_authorization"] is True
        assert by_type["hospital_api"]["authorization_status"] == "UNAUTHORIZED"
        assert by_type["mock_api"]["simulated"] is True
        assert by_type["website"]["connector"] == "WebsiteDataSource"

    def test_list_connectors(self, api_client: TestClient) -> None:
        resp = api_client.get("/api/v1/connectors")
        assert resp.status_code == 200
        rows = {r["source_type"]: r for r in resp.json()["connectors"]}
        assert rows["hospital_api"]["connector"] == "HospitalAPIDataSource"
        assert rows["hospital_api"]["resolved"] is True
        assert rows["json_feed"]["connector"] == "JSONFeedDataSource"

    def test_list_configured_sources(self, api_client: TestClient) -> None:
        resp = api_client.get("/api/v1/sources/configured")
        assert resp.status_code == 200
        assert isinstance(resp.json()["configured_sources"], list)

    def test_test_source_unknown_returns_404(self, api_client: TestClient) -> None:
        resp = api_client.get("/api/v1/sources/not-a-source/test")
        assert resp.status_code == 404
        assert resp.json()["error"] == "Unknown source type"

    def test_test_source_hospital_api_unauthorized_returns_401(
        self, api_client: TestClient
    ) -> None:
        resp = api_client.get("/api/v1/sources/hospital_api/test")
        assert resp.status_code == 401
        body = resp.json()
        assert body["source_type"] == "hospital_api"
        assert body["authorization"]["status"] == "UNAUTHORIZED"
        assert body["fetch_allowed"] is False
        assert "no external request" in body["note"]

    def test_test_source_website_offline_probe(self, api_client: TestClient) -> None:
        resp = api_client.get("/api/v1/sources/website/test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["fetch_allowed"] is False  # probe never fetches
        assert body["requires_authorization"] is False

    def test_test_source_mock_api_simulated(self, api_client: TestClient) -> None:
        resp = api_client.get("/api/v1/sources/mock_api/test")
        assert resp.status_code == 200
        assert resp.json()["simulated"] is True


# ---------------------------------------------------------------------------
# Y. UI connector section
# ---------------------------------------------------------------------------


class TestUiConnectorSection:
    def test_profile_page_renders_source_connector(
        self, api_client: TestClient, tmp_path: Path
    ) -> None:
        # Import a mock dataset so the profile page has source metadata.
        transport = httpx.ASGITransport(app=create_mock_app())
        from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource

        source = MockHospitalAPIDataSource(
            hospital_id="demo-northstar",
            client=httpx.AsyncClient(transport=transport, base_url="http://testserver"),
        )
        dataset = asyncio.run(source.fetch())
        payload = dataset.model_dump(mode="json")
        imported = api_client.post("/api/v1/hospitals/import", json=payload)
        assert imported.status_code == 200
        dataset_id = imported.json()["dataset_id"]

        page = api_client.get(f"/hospitals/{dataset_id}")
        assert page.status_code == 200
        html = page.text
        assert "Source connector" in html
        assert "MockHospitalAPIDataSource" in html
        assert "SIMULATED / MOCK" in html

    def test_profile_page_missing_still_renders(self, api_client: TestClient) -> None:
        page = api_client.get("/hospitals/nope")
        assert page.status_code == 200
        assert "not found" in page.text


# ---------------------------------------------------------------------------
# Z. Clean-room database (fresh DB + connector provenance)
# ---------------------------------------------------------------------------


class TestCleanRoomDatabase:
    def test_fresh_db_persists_api_dataset_with_provenance(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "clean.db"
        source = _api_source(authorization=_authorized_policy())
        dataset = asyncio.run(source.fetch())
        with HospitalDatabase(db_path) as db:
            dataset_id = db.save_dataset(dataset)
            restored = db.load_dataset(dataset_id)
            summaries = db.list_datasets()
        assert restored is not None
        assert restored.source is not None
        assert restored.source.source_type is SourceType.HOSPITAL_API
        assert restored.source.raw_payload_metadata.get("content_hash")
        assert summaries[0].source_type == "hospital_api"

    def test_fresh_db_persists_mock_authorized_dataset(self, tmp_path: Path) -> None:
        db_path = tmp_path / "clean_mock.db"
        source = MockAuthorizedApiConnector(
            authorization=_authorized_policy(hosts=("mock-authorized.example.test",))
        )
        dataset = asyncio.run(source.fetch())
        with HospitalDatabase(db_path) as db:
            dataset_id = db.save_dataset(dataset)
            restored = db.load_dataset(dataset_id)
        assert restored is not None
        assert restored.source is not None
        assert restored.source.raw_payload_metadata.get("simulated") is True
        assert "SIMULATED" in (restored.source.notes or "")

    def test_snapshot_capture_from_api_dataset(self, tmp_path: Path) -> None:
        db_path = tmp_path / "clean_snap.db"
        source = _api_source(authorization=_authorized_policy())
        dataset = asyncio.run(source.fetch())
        with HospitalDatabase(db_path) as db:
            capture = db.save_snapshot(dataset, hospital_id="h_clean")
            assert capture.snapshot.snapshot_id
            assert capture.change_set.change_set_id
            snapshots = db.list_snapshots("h_clean")
        assert snapshots
        assert snapshots[0].source_type == "hospital_api"


# ---------------------------------------------------------------------------
# Harvest pipeline (authorized factory kwargs promotion)
# ---------------------------------------------------------------------------


class TestHarvestAuthorizationPromotion:
    def test_harvest_promotes_authorized_fields(self) -> None:
        registry = SourceRegistry()
        captured: list[SourceConfig] = []

        def factory(**kwargs: Any) -> Any:
            class _Stub:
                async def fetch(self) -> Any:
                    return map_api_payload_to_dataset(
                        DEMO_JSON,
                        endpoint="https://api.example.in",
                        source_type=SourceType.HOSPITAL_API,
                    )

            _ = kwargs
            return _Stub()

        original_build = SourceRegistry.build

        def tracking_build(self: SourceRegistry, config: SourceConfig) -> Any:
            captured.append(config)
            return original_build(self, config)

        with patch.object(SourceRegistry, "build", tracking_build):
            registry.register(SourceType.HOSPITAL_API, factory)
            asyncio.run(
                harvest(
                    ["h1"],
                    source_type="hospital_api",
                    registry=registry,
                    authorized=True,
                    allowed_hosts=["api.example.in"],
                    base_url="https://api.example.in",
                    credential_env="MEDCOMPARE_CONNECTOR_H",
                )
            )
        assert captured
        cfg = captured[0]
        assert cfg.authorized is True
        assert cfg.allowed_hosts == ("api.example.in",)
        assert cfg.base_url == "https://api.example.in"
        assert cfg.credential_env == "MEDCOMPARE_CONNECTOR_H"

    def test_harvest_default_hospital_api_fails_closed(self) -> None:
        with pytest.raises(HarvestError):
            asyncio.run(harvest(["h1"], source_type="hospital_api"))

    def test_harvest_mock_api_still_works(self) -> None:
        registry = SourceRegistry()

        def factory(hospital_ref: str, **_: Any) -> Any:
            from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource

            transport = httpx.ASGITransport(app=create_mock_app())
            return MockHospitalAPIDataSource(
                hospital_id=hospital_ref,
                client=httpx.AsyncClient(transport=transport, base_url="http://testserver"),
            )

        registry.register(SourceType.MOCK_API, factory)
        results = asyncio.run(
            harvest(["demo-northstar"], source_type="mock_api", registry=registry)
        )
        assert len(results) == 1
        assert results[0].dataset.hospital is not None
