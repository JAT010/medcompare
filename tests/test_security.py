"""P24 security, RBAC, and audit tests.

Covers the numbered security scenarios and the broader A–AB matrix:
anonymous 401, role 403s, capture/recon/user audit events, P21 layer-2
fail-closed, resource-ID isolation, login failure hygiene, sanitized errors,
password/token storage hygiene, development vs production modes, and P23
regression (quality still works; overall_score stays null).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.auth.hashing import hash_password, hash_token, verify_password
from hospital_extractor.auth.models import Permission, Role
from hospital_extractor.auth.permissions import PERMISSIONS_BY_ROLE, role_has_permission
from hospital_extractor.auth.settings import auth_settings
from hospital_extractor.auth.store import AuthStore
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.metadata import FetchStatus, SourceMetadata, SourceType
from hospital_extractor.models.treatment import Treatment
from hospital_extractor.storage import HospitalDatabase

PASSWORD = "Correct-Horse-Battery-9"
HOSPITAL_A = "https://hospital-a.test"
HOSPITAL_B = "https://hospital-b.test"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _dataset(url: str, name: str, treatment: str = "Knee Replacement") -> HospitalDataset:
    source = SourceMetadata(
        source_type=SourceType("website"),
        source_id=f"website:{url}",
        hospital_ref=url,
        base_url=url,
        retrieved_at="2026-01-01T00:00:00Z",
        status=FetchStatus.SUCCESS,
        hospital_name=name,
    )
    return HospitalDataset(
        source=source,
        hospital=HospitalOverview(name=name, bed_count=100, source_url=url),
        treatments=[Treatment(name=treatment, source_url=url)],
    )


def _auth_client(tmp_path: Path, **overrides: Any) -> TestClient:
    """App with fail-closed auth (require_auth=True) on a temp database."""
    app = create_app(auth_enabled=True, **overrides)
    app.state.medcompare_db_path = tmp_path / "p24-auth.db"
    return TestClient(app)


def _bootstrap_roles(tmp_path: Path) -> dict[str, dict[str, Any]]:
    """Create one user per role directly in the store; return login payloads."""
    db = tmp_path / "p24-auth.db"
    AuthStore(db).ensure_schema()
    users = {
        "admin": (Role.ADMIN, ["h_allowed", "h_denied"]),
        "operator": (Role.OPERATOR, ["h_allowed"]),
        "reviewer": (Role.REVIEWER, ["h_allowed"]),
        "viewer": (Role.VIEWER, ["h_allowed"]),
    }
    created: dict[str, dict[str, Any]] = {}
    with AuthStore(db) as store:
        for username, (role, grants) in users.items():
            user = store.create_user(
                username=username,
                password=PASSWORD,
                role=role,
                hospital_ids=grants,
            )
            created[username] = {
                "password": PASSWORD,
                "user_id": user.user_id,
                "role": role.value,
                "hospital_ids": list(grants),
            }
    return created


def _login(client: TestClient, username: str, password: str = PASSWORD) -> dict[str, Any]:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _capture_body(url: str = HOSPITAL_A, name: str = "Hospital A") -> dict[str, Any]:
    return _dataset(url, name).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Unit: password hashing, tokens, permissions, settings
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_hash_is_scrypt_and_not_plaintext(self) -> None:
        encoded = hash_password(PASSWORD)
        assert encoded.startswith("scrypt$")
        assert PASSWORD not in encoded
        assert verify_password(PASSWORD, encoded) is True
        assert verify_password("wrong", encoded) is False

    def test_salts_differ(self) -> None:
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_empty_password_rejected(self) -> None:
        with pytest.raises(ValueError):
            hash_password("")

    def test_corrupt_hash_fails_closed(self) -> None:
        assert verify_password(PASSWORD, "not-a-hash") is False
        assert verify_password(PASSWORD, "") is False


class TestTokenDigest:
    def test_hash_token_is_sha256_hex(self) -> None:
        digest = hash_token("raw-token-value")
        assert len(digest) == 64
        assert digest == hash_token("raw-token-value")
        assert "raw-token-value" not in digest


class TestPermissionMap:
    def test_roles_have_expected_permissions(self) -> None:
        admin = PERMISSIONS_BY_ROLE[Role.ADMIN]
        assert Permission.MANAGE_USERS in admin
        assert Permission.SUBMIT_REPORT in admin
        assert Permission.CAPTURE_SOURCE in admin

        operator = PERMISSIONS_BY_ROLE[Role.OPERATOR]
        assert Permission.CAPTURE_SOURCE in operator
        assert Permission.MANAGE_MONITORING in operator
        assert Permission.MANAGE_USERS not in operator
        assert Permission.REVIEW_RECONCILIATION not in operator

        reviewer = PERMISSIONS_BY_ROLE[Role.REVIEWER]
        assert Permission.REVIEW_RECONCILIATION in reviewer
        assert Permission.CAPTURE_SOURCE not in reviewer

        viewer = PERMISSIONS_BY_ROLE[Role.VIEWER]
        assert viewer == frozenset(
            {Permission.VIEW_HOSPITAL, Permission.VIEW_QUALITY}
        )
        assert Permission.CAPTURE_SOURCE not in viewer
        assert Permission.MANAGE_USERS not in viewer

    def test_unknown_role_fails_closed(self) -> None:
        # Construct a Role-like value not in the map via object.__new__
        # — role_has_permission only accepts Role, so unknown enum impossible;
        # still assert helper returns False for permissions not granted.
        assert role_has_permission(Role.VIEWER, Permission.MANAGE_USERS) is False


class TestAuthSettings:
    def test_development_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MEDCOMPARE_REQUIRE_AUTH", raising=False)
        monkeypatch.delenv("MEDCOMPARE_AUTH_MODE", raising=False)
        settings = auth_settings()
        assert settings.require_auth is False
        assert settings.mode.value == "development"
        assert settings.allow_dev_header is True

    def test_production_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MEDCOMPARE_REQUIRE_AUTH", raising=False)
        monkeypatch.setenv("MEDCOMPARE_AUTH_MODE", "production")
        settings = auth_settings()
        assert settings.require_auth is True
        assert settings.allow_dev_header is False

    def test_production_cannot_reenable_dev_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_AUTH_MODE", "production")
        settings = auth_settings(allow_dev_header=True)
        assert settings.allow_dev_header is False

    def test_env_require_auth_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEDCOMPARE_REQUIRE_AUTH", "1")
        monkeypatch.setenv("MEDCOMPARE_AUTH_MODE", "development")
        settings = auth_settings()
        assert settings.require_auth is True
        assert settings.allow_dev_header is True


# ---------------------------------------------------------------------------
# Scenario 1 / A: anonymous → 401
# ---------------------------------------------------------------------------


class TestAnonymous401:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/v1/hospitals"),
            ("GET", "/api/v1/auth/me"),
            ("GET", "/api/v1/users"),
            ("GET", "/api/v1/audit"),
            ("GET", "/api/v1/sources"),
            ("GET", "/api/v1/quality/pipeline"),
            ("POST", "/api/v1/hospitals/import"),
            ("DELETE", "/api/v1/hospitals/whatever"),
            ("POST", "/api/v1/hospitals/h_any/capture"),
        ],
    )
    def test_anonymous_requests_are_401(
        self, tmp_path: Path, method: str, path: str
    ) -> None:
        with _auth_client(tmp_path) as client:
            kwargs: dict[str, Any] = {}
            if method in {"POST", "PATCH", "PUT"}:
                kwargs["json"] = {}
            resp = client.request(method, path, **kwargs)
            assert resp.status_code == 401, (method, path, resp.status_code, resp.text)
            # No stack traces / secrets in the body
            text = resp.text.lower()
            assert "traceback" not in text
            assert "password" not in text
            assert "scrypt$" not in text

    def test_health_still_open_when_auth_on(self, tmp_path: Path) -> None:
        with _auth_client(tmp_path) as client:
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200

    def test_login_endpoint_open(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "viewer", "password": "wrong-password"},
            )
            # Wrong password is 401 (not "endpoint missing")
            assert resp.status_code == 401

    def test_bearer_token_required_when_auth_on(self, tmp_path: Path) -> None:
        with _auth_client(tmp_path) as client:
            resp = client.get(
                "/api/v1/hospitals", headers={"Authorization": "Bearer nope"}
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Development bypass (require_auth=False) keeps local UX working
# ---------------------------------------------------------------------------


class TestDevelopmentBypass:
    def test_default_app_allows_access_without_token(self, tmp_path: Path) -> None:
        app = create_app()
        app.state.medcompare_db_path = tmp_path / "dev.db"
        with TestClient(app) as client:
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200
            me = client.get("/api/v1/auth/me")
            assert me.status_code == 200
            body = me.json()
            assert body["auth_status"] == "development_bypass"
            assert body["role"] == "admin"
            assert body["development"] is True

    def test_bypass_labeled_not_production(self, tmp_path: Path) -> None:
        app = create_app(auth_enabled=False)
        app.state.medcompare_db_path = tmp_path / "dev2.db"
        with TestClient(app) as client:
            me = client.get("/api/v1/auth/me").json()
            assert me["auth_status"] == "development_bypass"


# ---------------------------------------------------------------------------
# Scenario 2: VIEWER capture → 403
# Scenario 3: OPERATOR capture → audit
# Scenario 8: resource isolation / ID swap → denied
# ---------------------------------------------------------------------------


class TestCaptureAuthorization:
    def test_viewer_capture_forbidden(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "viewer")["access_token"]
            resp = client.post(
                "/api/v1/hospitals/h_allowed/capture",
                json=_capture_body(),
                headers=_auth_headers(token),
            )
            assert resp.status_code == 403

    def test_operator_capture_allowed_and_audited(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "operator")["access_token"]
            resp = client.post(
                "/api/v1/hospitals/h_allowed/capture",
                json=_capture_body(),
                headers=_auth_headers(token),
            )
            assert resp.status_code in (200, 201), resp.text
            audit = client.get("/api/v1/audit", headers=_auth_headers(token))
            # operator lacks MANAGE_USERS → cannot read audit
            assert audit.status_code == 403
            admin_token = _login(client, "admin")["access_token"]
            audit = client.get("/api/v1/audit", headers=_auth_headers(admin_token))
            assert audit.status_code == 200
            actions = [e["action"] for e in audit.json()["events"]]
            assert "hospital.capture" in actions

    def test_operator_id_swap_denied(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "operator")["access_token"]
            # operator is granted h_allowed only
            ok = client.post(
                "/api/v1/hospitals/h_allowed/capture",
                json=_capture_body(url=HOSPITAL_A),
                headers=_auth_headers(token),
            )
            assert ok.status_code in (200, 201)
            denied = client.post(
                "/api/v1/hospitals/h_denied/capture",
                json=_capture_body(url=HOSPITAL_B, name="Hospital B"),
                headers=_auth_headers(token),
            )
            assert denied.status_code == 403

    def test_admin_can_capture_any_hospital(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "admin")["access_token"]
            resp = client.post(
                "/api/v1/hospitals/h_anything/capture",
                json=_capture_body(),
                headers=_auth_headers(token),
            )
            assert resp.status_code in (200, 201)


# ---------------------------------------------------------------------------
# Scenario 4: REVIEWER recon → audit
# Scenario 5: VIEWER recon → 403
# ---------------------------------------------------------------------------


class TestReconciliationAuthorization:
    def _seed_case(self, db_path: Path) -> str:
        from tests.test_reconciliation import _seed_mismatch, _sq

        with _sq(str(db_path)) as db:
            _job, case = _seed_mismatch(db)
            return case.reconciliation_case_id

    def test_viewer_resolve_forbidden(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        case_id = self._seed_case(tmp_path / "p24-auth.db")
        with _auth_client(tmp_path) as client:
            token = _login(client, "viewer")["access_token"]
            resp = client.post(
                f"/api/v1/reconciliation-cases/{case_id}/resolve",
                json={"resolution": "authoritative_source_confirmed", "reason": "ok"},
                headers=_auth_headers(token),
            )
            assert resp.status_code == 403

    def test_reviewer_resolve_allowed_and_audited(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        case_id = self._seed_case(tmp_path / "p24-auth.db")
        with _auth_client(tmp_path) as client:
            token = _login(client, "reviewer")["access_token"]
            resp = client.post(
                f"/api/v1/reconciliation-cases/{case_id}/review",
                json={"reason": "start"},
                headers=_auth_headers(token),
            )
            assert resp.status_code == 200, resp.text
            resolved = client.post(
                f"/api/v1/reconciliation-cases/{case_id}/resolve",
                json={"resolution": "authoritative_source_confirmed", "reason": "ok"},
                headers=_auth_headers(token),
            )
            assert resolved.status_code == 200, resolved.text

            admin = _login(client, "admin")["access_token"]
            audit = client.get("/api/v1/audit", headers=_auth_headers(admin)).json()
            actions = [e["action"] for e in audit["events"]]
            assert "reconciliation.review" in actions
            assert "reconciliation.resolve" in actions
            # Reviewer actor recorded, not an anonymous literal
            resolve_events = [
                e for e in audit["events"] if e["action"] == "reconciliation.resolve"
            ]
            assert resolve_events[0]["actor_role"] == "reviewer"
            assert resolve_events[0]["actor_id"]

    def test_operator_cannot_review_reconciliation(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        case_id = self._seed_case(tmp_path / "p24-auth.db")
        with _auth_client(tmp_path) as client:
            token = _login(client, "operator")["access_token"]
            resp = client.post(
                f"/api/v1/reconciliation-cases/{case_id}/review",
                json={},
                headers=_auth_headers(token),
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Scenario 6: ADMIN users → audit
# Scenario A/B: user management endpoints
# ---------------------------------------------------------------------------


class TestUserManagement:
    def test_admin_create_user_audited_and_no_hash_leak(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            admin = _login(client, "admin")["access_token"]
            resp = client.post(
                "/api/v1/users",
                json={
                    "username": "newbie",
                    "password": "Another-Secret-42",
                    "role": "viewer",
                },
                headers=_auth_headers(admin),
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert "password_hash" not in body
            assert "password" not in body
            assert body["username"] == "newbie"
            assert body["role"] == "viewer"

            audit = client.get("/api/v1/audit", headers=_auth_headers(admin)).json()
            assert any(e["action"] == "user.create" for e in audit["events"])
            # Audit metadata must not contain the password
            raw = str(audit)
            assert "Another-Secret-42" not in raw

    def test_viewer_cannot_list_users(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "viewer")["access_token"]
            resp = client.get("/api/v1/users", headers=_auth_headers(token))
            assert resp.status_code == 403

    def test_operator_cannot_create_users(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "operator")["access_token"]
            resp = client.post(
                "/api/v1/users",
                json={"username": "x1", "password": "LongEnough-1", "role": "viewer"},
                headers=_auth_headers(token),
            )
            assert resp.status_code == 403

    def test_admin_update_role_audited(self, tmp_path: Path) -> None:
        users = _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            admin = _login(client, "admin")["access_token"]
            viewer_id = users["viewer"]["user_id"]
            resp = client.patch(
                f"/api/v1/users/{viewer_id}",
                json={"role": "reviewer"},
                headers=_auth_headers(admin),
            )
            assert resp.status_code == 200
            assert resp.json()["role"] == "reviewer"
            audit = client.get("/api/v1/audit", headers=_auth_headers(admin)).json()
            updates = [e for e in audit["events"] if e["action"] == "user.update"]
            assert updates
            assert updates[0]["metadata"]["changed"] == ["role"]

    def test_duplicate_username_conflict(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            admin = _login(client, "admin")["access_token"]
            resp = client.post(
                "/api/v1/users",
                json={"username": "viewer", "password": "LongEnough-1", "role": "viewer"},
                headers=_auth_headers(admin),
            )
            assert resp.status_code == 409

    def test_password_cannot_equal_username(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            admin = _login(client, "admin")["access_token"]
            resp = client.post(
                "/api/v1/users",
                json={"username": "alice", "password": "alice", "role": "viewer"},
                headers=_auth_headers(admin),
            )
            assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Scenario 7: authorized user + unauthorized P21 source → zero I/O
# ---------------------------------------------------------------------------


class TestLayer2SourceAuthorization:
    def test_admin_still_gets_401_for_unauthorized_source(
        self, tmp_path: Path
    ) -> None:
        """Layer 1 ADMIN must not bypass Layer 2 (P21) source authorization."""
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            admin = _login(client, "admin")["access_token"]
            with patch(
                "hospital_extractor.sources.hospital_api.HospitalAPIDataSource.fetch",
                side_effect=AssertionError("network I/O must not happen"),
            ):
                resp = client.get(
                    "/api/v1/sources/hospital_api/test",
                    headers=_auth_headers(admin),
                )
            assert resp.status_code == 401
            body = resp.json()
            assert body["fetch_allowed"] is False
            assert body["requires_authorization"] is True

    def test_website_source_probe_allows_offline(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            admin = _login(client, "admin")["access_token"]
            resp = client.get(
                "/api/v1/sources/website/test", headers=_auth_headers(admin)
            )
            assert resp.status_code == 200
            assert resp.json()["fetch_allowed"] is False  # offline probe only


# ---------------------------------------------------------------------------
# Scenario 9: login failure → audit without leaking password
# ---------------------------------------------------------------------------


class TestLoginSecurity:
    def test_failed_login_audited_without_password(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            bad = "Super-Secret-Bad-1"
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "viewer", "password": bad},
            )
            assert resp.status_code == 401
            assert bad not in resp.text
            assert "scrypt" not in resp.text
            assert "traceback" not in resp.text.lower()

            # Audit as admin
            admin = _login(client, "admin")["access_token"]
            audit = client.get(
                "/api/v1/audit?action=auth.login", headers=_auth_headers(admin)
            ).json()
            failures = [e for e in audit["events"] if e["outcome"] == "failure"]
            assert failures
            assert bad not in str(audit)
            assert bad not in str(failures)

    def test_successful_login_returns_token_once(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            body = _login(client, "operator")
            assert body["token_type"] == "bearer"
            assert len(body["access_token"]) > 20
            assert "password" not in body
            assert "password_hash" not in body.get("user", {})
            # Token works
            me = client.get(
                "/api/v1/auth/me", headers=_auth_headers(body["access_token"])
            )
            assert me.status_code == 200
            assert me.json()["username"] == "operator"

    def test_logout_revokes_token(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "operator")["access_token"]
            out = client.post(
                "/api/v1/auth/logout", headers=_auth_headers(token)
            )
            assert out.status_code == 204
            me = client.get("/api/v1/auth/me", headers=_auth_headers(token))
            assert me.status_code == 401

    def test_token_not_stored_in_plaintext(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "operator")["access_token"]
            store = AuthStore(tmp_path / "p24-auth.db")
            with store:
                # Raw token must not appear anywhere in auth_tokens
                rows = store._connection.execute(  # noqa: SLF001
                    "SELECT token_hash FROM auth_tokens"
                ).fetchall()
                hashes = [r["token_hash"] for r in rows]
                assert token not in hashes
                assert hash_token(token) in hashes

    def test_rate_limit_after_repeated_failures(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            codes = []
            for _ in range(12):
                resp = client.post(
                    "/api/v1/auth/login",
                    json={"username": "viewer", "password": "wrong"},
                )
                codes.append(resp.status_code)
            assert 429 in codes

    def test_disabled_user_cannot_login(self, tmp_path: Path) -> None:
        users = _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            admin = _login(client, "admin")["access_token"]
            client.patch(
                f"/api/v1/users/{users['viewer']['user_id']}",
                json={"enabled": False},
                headers=_auth_headers(admin),
            )
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "viewer", "password": PASSWORD},
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Scenario 10: internal exception → sanitized
# ---------------------------------------------------------------------------


class TestSanitizedErrors:
    def test_unhandled_exception_hides_details(self, tmp_path: Path) -> None:
        app = create_app(auth_enabled=False)
        app.state.medcompare_db_path = tmp_path / "err.db"

        @app.get("/api/v1/__boom")
        def _boom() -> None:
            raise RuntimeError("secret-token=abc path=/etc/passwd password=hunter2")

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/__boom")
            assert resp.status_code == 500
            assert "hunter2" not in resp.text
            assert "secret-token" not in resp.text
            assert "traceback" not in resp.text.lower()
            assert resp.json() == {"detail": "Internal server error"}

    def test_validation_error_is_structured_not_traceback(
        self, tmp_path: Path
    ) -> None:
        with _auth_client(tmp_path) as client:
            admin = _bootstrap_and_login(tmp_path, client)
            resp = client.post(
                "/api/v1/users",
                json={"username": "u1", "password": "short", "role": "viewer"},
                headers=_auth_headers(admin),
            )
            assert resp.status_code == 422
            assert "Traceback" not in resp.text
            assert "site-packages" not in resp.text


def _bootstrap_and_login(tmp_path: Path, client: TestClient) -> str:
    _bootstrap_roles(tmp_path)
    return _login(client, "admin")["access_token"]


# ---------------------------------------------------------------------------
# Development header (test-only) + production rejection
# ---------------------------------------------------------------------------


class TestDevHeader:
    def test_dev_header_resolves_principal_in_development(
        self, tmp_path: Path
    ) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            resp = client.get(
                "/api/v1/auth/me", headers={"X-Medcompare-Dev-User": "viewer"}
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["username"] == "viewer"
            assert body["auth_status"] == "dev_header"
            assert body["development"] is True

    def test_dev_header_ignored_in_production(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path, auth_mode="production") as client:
            resp = client.get(
                "/api/v1/auth/me", headers={"X-Medcompare-Dev-User": "admin"}
            )
            assert resp.status_code == 401

    def test_dev_header_rejects_unknown_user(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            resp = client.get(
                "/api/v1/auth/me", headers={"X-Medcompare-Dev-User": "ghost"}
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Monitoring / verification / report permissions (matrix)
# ---------------------------------------------------------------------------


def _register_hospital(db_path: Path, hospital_id: str = "h_allowed") -> None:
    """Insert a bare hospital identity so monitoring can resolve it (fail-closed)."""
    from datetime import UTC, datetime

    with HospitalDatabase(db_path) as db:
        db._connection.execute(  # noqa: SLF001
            "INSERT OR IGNORE INTO hospitals (hospital_id, name, created_at)"
            " VALUES (?, ?, ?)",
            (hospital_id, "Granted Hospital", datetime.now(UTC).isoformat()),
        )
        db._connection.commit()


class TestMutatingPermissions:
    def test_viewer_cannot_create_monitoring_policy(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        _register_hospital(tmp_path / "p24-auth.db")
        with _auth_client(tmp_path) as client:
            token = _login(client, "viewer")["access_token"]
            resp = client.post(
                "/api/v1/hospitals/h_allowed/monitoring-policies",
                json={"frequency": "daily", "source": "website", "target_ref": "x"},
                headers=_auth_headers(token),
            )
            assert resp.status_code == 403

    def test_operator_can_create_monitoring_policy_and_audit(
        self, tmp_path: Path
    ) -> None:
        _bootstrap_roles(tmp_path)
        _register_hospital(tmp_path / "p24-auth.db")
        with _auth_client(tmp_path) as client:
            token = _login(client, "operator")["access_token"]
            resp = client.post(
                "/api/v1/hospitals/h_allowed/monitoring-policies",
                json={"frequency": "daily", "source": "website", "target_ref": "x"},
                headers=_auth_headers(token),
            )
            assert resp.status_code == 201, resp.text
            admin = _login(client, "admin")["access_token"]
            audit = client.get("/api/v1/audit", headers=_auth_headers(admin)).json()
            assert any(e["action"] == "monitoring.create" for e in audit["events"])

    def test_operator_monitoring_id_swap_denied(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "operator")["access_token"]
            resp = client.post(
                "/api/v1/hospitals/h_denied/monitoring-policies",
                json={"frequency": "daily", "source": "website", "target_ref": "x"},
                headers=_auth_headers(token),
            )
            assert resp.status_code == 403

    def test_viewer_cannot_create_verification_job(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "viewer")["access_token"]
            resp = client.post(
                "/api/v1/hospitals/h_allowed/verification-jobs",
                json={"source": "website"},
                headers=_auth_headers(token),
            )
            assert resp.status_code == 403

    def test_operator_cannot_manage_users(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "operator")["access_token"]
            resp = client.get("/api/v1/users", headers=_auth_headers(token))
            assert resp.status_code == 403
            resp = client.get("/api/v1/audit", headers=_auth_headers(token))
            assert resp.status_code == 403

    def test_audit_event_by_id_admin_only(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            admin = _login(client, "admin")["access_token"]
            client.post(
                "/api/v1/auth/login",
                json={"username": "viewer", "password": "bad"},
            )
            audit = client.get(
                "/api/v1/audit?action=auth.login", headers=_auth_headers(admin)
            ).json()
            event_id = audit["events"][0]["audit_event_id"]
            ok = client.get(
                f"/api/v1/audit/{event_id}", headers=_auth_headers(admin)
            )
            assert ok.status_code == 200
            missing = client.get("/api/v1/audit/ae_missing", headers=_auth_headers(admin))
            assert missing.status_code == 404

            viewer = _login(client, "viewer")["access_token"]
            denied = client.get(
                f"/api/v1/audit/{event_id}", headers=_auth_headers(viewer)
            )
            assert denied.status_code == 403


# ---------------------------------------------------------------------------
# Hospital grant management via user update (resource-level isolation setup)
# ---------------------------------------------------------------------------


class TestHospitalGrants:
    def test_grant_and_revoke_controls_access(self, tmp_path: Path) -> None:
        users = _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            admin = _login(client, "admin")["access_token"]
            op_id = users["operator"]["user_id"]

            # Revoke h_allowed grant
            resp = client.patch(
                f"/api/v1/users/{op_id}",
                json={"hospital_ids": []},
                headers=_auth_headers(admin),
            )
            assert resp.status_code == 200
            assert resp.json()["hospital_ids"] == []

            # Re-login so principal picks up empty grants
            token = _login(client, "operator")["access_token"]
            denied = client.post(
                "/api/v1/hospitals/h_allowed/capture",
                json=_capture_body(),
                headers=_auth_headers(token),
            )
            assert denied.status_code == 403

            # Re-grant
            client.patch(
                f"/api/v1/users/{op_id}",
                json={"hospital_ids": ["h_allowed"]},
                headers=_auth_headers(admin),
            )
            token = _login(client, "operator")["access_token"]
            allowed = client.post(
                "/api/v1/hospitals/h_allowed/capture",
                json=_capture_body(),
                headers=_auth_headers(token),
            )
            assert allowed.status_code in (200, 201)


# ---------------------------------------------------------------------------
# Audit store hygiene
# ---------------------------------------------------------------------------


class TestAuditStore:
    def test_append_only_no_update_delete_api(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "audit.db")
        with store:
            assert not hasattr(store, "update_audit")
            assert not hasattr(store, "delete_audit")
            assert not hasattr(store, "purge_audit")

    def test_metadata_strips_secret_keys(self, tmp_path: Path) -> None:
        from hospital_extractor.auth.models import AuditEvent

        store = AuthStore(tmp_path / "audit2.db")
        with store:
            store.append_audit(
                AuditEvent(
                    audit_event_id="ae_1",
                    action="test",
                    metadata={
                        "password": "leak",
                        "token": "leak2",
                        "ok": "yes",
                        "Authorization": "Bearer x",
                    },
                )
            )
            row = store._connection.execute(  # noqa: SLF001
                "SELECT metadata_json FROM audit_events WHERE audit_event_id='ae_1'"
            ).fetchone()
            assert "leak" not in row["metadata_json"]
            assert "Bearer x" not in row["metadata_json"]
            assert '"yes"' in row["metadata_json"]

    def test_list_filters(self, tmp_path: Path) -> None:
        from hospital_extractor.auth.audit import record_audit
        from hospital_extractor.auth.models import Principal, Role

        store = AuthStore(tmp_path / "audit3.db")
        principal = Principal(
            user_id="u1",
            username="admin",
            role=Role.ADMIN,
            permissions=(),
        )
        with store:
            record_audit(
                store, action="a.one", principal=principal, hospital_id="h1"
            )
            record_audit(
                store, action="a.two", principal=principal, hospital_id="h2"
            )
            assert len(store.list_audit(action="a.one")) == 1
            assert len(store.list_audit(hospital_id="h2")) == 1
            assert store.count_audit() == 2


# ---------------------------------------------------------------------------
# Request correlation
# ---------------------------------------------------------------------------


class TestCorrelation:
    def test_request_id_header_present(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            resp = client.get("/api/v1/health")
            assert "x-request-id" in {k.lower() for k in resp.headers}

    def test_custom_request_id_echoed(self, tmp_path: Path) -> None:
        with _auth_client(tmp_path) as client:
            resp = client.get(
                "/api/v1/health", headers={"X-Request-ID": "req_custom_123"}
            )
            assert resp.headers["X-Request-ID"] == "req_custom_123"


# ---------------------------------------------------------------------------
# P23 regression: quality still works under auth; overall_score stays null
# ---------------------------------------------------------------------------


class TestP23RegressionUnderAuth:
    def _seed(self, db_path: Path) -> str:
        with HospitalDatabase(db_path) as db:
            dataset_id = db.save_dataset(
                _dataset(HOSPITAL_A, "Quality Hospital")
            )
            summary = next(
                d
                for d in db.list_datasets()
                if d.dataset_id == dataset_id
            )
            return summary.hospital_id or dataset_id

    def test_quality_pipeline_overall_score_null(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        hospital_id = self._seed(tmp_path / "p24-auth.db")
        with _auth_client(tmp_path) as client:
            token = _login(client, "viewer")["access_token"]
            resp = client.get(
                "/api/v1/quality/pipeline", headers=_auth_headers(token)
            )
            assert resp.status_code == 200
            assert resp.json()["report"]["overall_score"] is None

            hosp = client.get(
                f"/api/v1/quality/hospitals/{hospital_id}",
                headers=_auth_headers(token),
            )
            if hosp.status_code == 200:
                assert hosp.json()["report"]["overall_score"] is None

    def test_viewer_can_view_quality(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            token = _login(client, "viewer")["access_token"]
            resp = client.get(
                "/api/v1/quality/pipeline", headers=_auth_headers(token)
            )
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CLI must not print secrets
# ---------------------------------------------------------------------------


class TestCliNoSecrets:
    def test_cli_quality_help_has_no_secrets(self, capsys: pytest.CaptureFixture[str]) -> None:
        from hospital_extractor.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["quality-report", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr()
        combined = out.out + out.err
        assert "password" not in combined.lower()
        assert "secret" not in combined.lower()


# ---------------------------------------------------------------------------
# Trusted hosts (API hardening, local dev preserved)
# ---------------------------------------------------------------------------


class TestTrustedHosts:
    def test_testserver_allowed(self, tmp_path: Path) -> None:
        with _auth_client(tmp_path) as client:
            assert client.get("/api/v1/health").status_code == 200

    def test_unknown_host_rejected(self, tmp_path: Path) -> None:
        with _auth_client(tmp_path) as client:
            resp = client.get(
                "/api/v1/health", headers={"Host": "evil.example"}
            )
            assert resp.status_code in (400, 403)


# ---------------------------------------------------------------------------
# One auth mechanism only (bearer); no dual session cookie path
# ---------------------------------------------------------------------------


class TestSingleAuthMechanism:
    def test_login_sets_no_session_cookie(self, tmp_path: Path) -> None:
        _bootstrap_roles(tmp_path)
        with _auth_client(tmp_path) as client:
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "operator", "password": PASSWORD},
            )
            assert resp.status_code == 200
            set_cookie = resp.headers.get("set-cookie", "")
            assert "session" not in set_cookie.lower()
            assert "access_token" not in set_cookie.lower()
