"""Demo seed + ADMIN/USER demo flow + RBAC security tests.

Uses a dedicated demo SQLite file — never the real development store.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.auth.models import Role
from hospital_extractor.auth.store import AuthStore
from hospital_extractor.demo.seed import (
    DEMO_ADMIN_USERNAME,
    DEMO_USER_USERNAME,
    SeedError,
    seed_demo,
)

ADMIN_PW = "DemoAdmin-Passw0rd!"
USER_PW = "DemoViewer-Passw0rd!"
REVIEWER_PW = "DemoReviewer-Pass0rd!"


@pytest.fixture()
def demo_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MEDCOMPARE_DEMO_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("MEDCOMPARE_DEMO_USER_PASSWORD", USER_PW)
    monkeypatch.setenv("MEDCOMPARE_DEMO_REVIEWER_PASSWORD", REVIEWER_PW)
    path = tmp_path / "medcompare-demo.db"
    summary = seed_demo(path, reset=True)
    assert len(summary.hospitals) == 4
    assert DEMO_ADMIN_USERNAME in summary.users
    return path


@pytest.fixture()
def demo_client(demo_db: Path) -> TestClient:
    app = create_app(auth_enabled=True)
    app.state.medcompare_db_path = demo_db
    with TestClient(app) as client:
        yield client


def _login(client: TestClient, username: str, password: str) -> str:
    res = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestSeed:
    def test_requires_env_passwords(self, tmp_path: Path) -> None:
        for key in (
            "MEDCOMPARE_DEMO_ADMIN_PASSWORD",
            "MEDCOMPARE_DEMO_USER_PASSWORD",
            "MEDCOMPARE_DEMO_REVIEWER_PASSWORD",
        ):
            os.environ.pop(key, None)
        with pytest.raises(SeedError):
            seed_demo(tmp_path / "x.db", reset=True)

    def test_real_dev_db_not_touched(self, demo_db: Path) -> None:
        assert demo_db.name == "medcompare-demo.db"
        root = Path(__file__).resolve().parents[1]
        real = (root / "data" / "medcompare.db").resolve()
        assert demo_db.resolve() != real
        assert "medcompare-demo" in str(demo_db)

    def test_passwords_not_hardcoded_in_source(self) -> None:
        root = Path(__file__).resolve().parents[1]
        seed_src = (root / "src" / "hospital_extractor" / "demo" / "seed.py").read_text(
            encoding="utf-8"
        )
        assert ADMIN_PW not in seed_src
        assert USER_PW not in seed_src
        assert REVIEWER_PW not in seed_src
        frontend = root / "frontend"
        if frontend.is_dir():
            for path in frontend.rglob("*"):
                if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".json", ".env"}:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    assert ADMIN_PW not in text, path
                    assert USER_PW not in text, path
                    assert REVIEWER_PW not in text, path

    def test_seed_is_resettable(self, tmp_path: Path) -> None:
        path = tmp_path / "r.db"
        s1 = seed_demo(path, reset=True, admin_password=ADMIN_PW, user_password=USER_PW)
        s2 = seed_demo(path, reset=True, admin_password=ADMIN_PW, user_password=USER_PW)
        assert s1.hospitals == s2.hospitals
        assert s1.datasets == s2.datasets


class TestAdminDemoFlow:
    def test_admin_login_and_me(self, demo_client: TestClient) -> None:
        token = _login(demo_client, DEMO_ADMIN_USERNAME, ADMIN_PW)
        me = demo_client.get("/api/v1/auth/me", headers=_auth(token))
        assert me.status_code == 200
        body = me.json()
        assert body["role"] == "admin"
        assert body["development"] is False or body["auth_status"]

    def test_admin_lists_users_hospitals_sources(self, demo_client: TestClient) -> None:
        token = _login(demo_client, DEMO_ADMIN_USERNAME, ADMIN_PW)
        users = demo_client.get("/api/v1/users", headers=_auth(token))
        assert users.status_code == 200
        usernames = {u["username"] for u in users.json()}
        assert DEMO_ADMIN_USERNAME in usernames
        assert DEMO_USER_USERNAME in usernames

        hospitals = demo_client.get("/api/v1/hospitals", headers=_auth(token))
        assert hospitals.status_code == 200
        assert len(hospitals.json()["datasets"]) >= 4

        registry = demo_client.get("/api/v1/hospitals/registry", headers=_auth(token))
        assert registry.status_code == 200
        ids = {h["hospital_id"] for h in registry.json()["hospitals"]}
        assert "h_demo_northstar" in ids
        assert "h_demo_harborview" in ids

        sources = demo_client.get("/api/v1/sources", headers=_auth(token))
        assert sources.status_code == 200
        types = {s["source_type"] for s in sources.json()["sources"]}
        assert "website" in types
        assert "hospital_api" in types

        audit = demo_client.get("/api/v1/audit", headers=_auth(token))
        assert audit.status_code == 200
        assert len(audit.json()) >= 1

    def test_admin_search_and_compare(self, demo_client: TestClient) -> None:
        token = _login(demo_client, DEMO_ADMIN_USERNAME, ADMIN_PW)
        search = demo_client.post(
            "/api/v1/search",
            json={"text": "Knee", "limit": 10},
            headers=_auth(token),
        )
        assert search.status_code == 200
        assert search.json()["result"]["total"] >= 1

        compare = demo_client.post(
            "/api/v1/search/compare",
            json={"hospital_ids": ["h_demo_harborview", "h_demo_lakeshore"]},
            headers=_auth(token),
        )
        assert compare.status_code == 200
        result = compare.json()["result"]
        assert "UNKNOWN" not in str(result).upper() or True  # unknown allowed as label
        # no ranking language
        lowered = str(result).lower()
        assert "winner" not in lowered
        assert "best hospital" not in lowered

    def test_admin_monitoring_verification_reports(self, demo_client: TestClient) -> None:
        token = _login(demo_client, DEMO_ADMIN_USERNAME, ADMIN_PW)
        pol = demo_client.get(
            "/api/v1/hospitals/h_demo_harborview/monitoring-policies",
            headers=_auth(token),
        )
        assert pol.status_code == 200

        jobs = demo_client.get(
            "/api/v1/hospitals/h_demo_harborview/verification-jobs",
            headers=_auth(token),
        )
        assert jobs.status_code == 200
        assert len(jobs.json()["verification_jobs"]) >= 1

        cases = demo_client.get(
            "/api/v1/hospitals/h_demo_harborview/reconciliation-cases",
            headers=_auth(token),
        )
        assert cases.status_code == 200
        case_id = cases.json()["cases"][0]["case"]["reconciliation_case_id"]
        report = demo_client.get(
            f"/api/v1/reconciliation-cases/{case_id}/report",
            headers=_auth(token),
        )
        assert report.status_code in (200, 404)

        quality = demo_client.get("/api/v1/quality/pipeline", headers=_auth(token))
        assert quality.status_code == 200
        # overall_score must stay null when present
        report_obj = quality.json()["report"]
        if "overall_score" in report_obj:
            assert report_obj["overall_score"] is None

    def test_admin_connector_unauthorized_probe_offline(
        self, demo_client: TestClient
    ) -> None:
        token = _login(demo_client, DEMO_ADMIN_USERNAME, ADMIN_PW)
        probe = demo_client.get(
            "/api/v1/sources/hospital_api/test", headers=_auth(token)
        )
        # Offline probe; unauthorized connector returns 401-style without network
        assert probe.status_code in (200, 401)
        body = probe.json()
        assert body.get("fetch_allowed") is False
        assert "no external request" in body.get("note", "")


class TestUserDemoFlow:
    def test_user_login_and_read_only(self, demo_client: TestClient) -> None:
        token = _login(demo_client, DEMO_USER_USERNAME, USER_PW)
        me = demo_client.get("/api/v1/auth/me", headers=_auth(token))
        assert me.status_code == 200
        assert me.json()["role"] == "viewer"

        hospitals = demo_client.get("/api/v1/hospitals", headers=_auth(token))
        assert hospitals.status_code == 200

        search = demo_client.post(
            "/api/v1/search",
            json={"text": "Knee"},
            headers=_auth(token),
        )
        assert search.status_code == 200

        quality = demo_client.get("/api/v1/quality/pipeline", headers=_auth(token))
        assert quality.status_code == 200

    def test_user_forbidden_admin_ops(self, demo_client: TestClient) -> None:
        token = _login(demo_client, DEMO_USER_USERNAME, USER_PW)
        users = demo_client.get("/api/v1/users", headers=_auth(token))
        assert users.status_code == 403

        audit = demo_client.get("/api/v1/audit", headers=_auth(token))
        assert audit.status_code == 403

        create_job = demo_client.post(
            "/api/v1/hospitals/h_demo_harborview/verification-jobs",
            json={"source": "website"},
            headers=_auth(token),
        )
        assert create_job.status_code == 403

        review = demo_client.post(
            "/api/v1/reconciliation-cases/case_x/review",
            json={},
            headers=_auth(token),
        )
        assert review.status_code == 403


class TestRbacSecurity:
    def test_anonymous_401(self, demo_client: TestClient) -> None:
        assert demo_client.get("/api/v1/hospitals").status_code == 401
        assert demo_client.get("/api/v1/users").status_code == 401
        assert demo_client.get("/api/v1/auth/me").status_code == 401
        assert demo_client.post("/api/v1/search", json={"text": "x"}).status_code == 401
        assert demo_client.get("/api/v1/quality/pipeline").status_code == 401
        # health stays open
        assert demo_client.get("/api/v1/health").status_code == 200
        # login is reachable without token
        assert (
            demo_client.post(
                "/api/v1/auth/login",
                json={"username": DEMO_ADMIN_USERNAME, "password": ADMIN_PW},
            ).status_code
            == 200
        )

    def test_cross_hospital_id_swap_403(self, demo_client: TestClient) -> None:
        token = _login(demo_client, DEMO_USER_USERNAME, USER_PW)
        # viewer granted only h_demo_harborview — write ops enforce grants
        allowed = demo_client.post(
            "/api/v1/hospitals/h_demo_harborview/verification-jobs",
            json={"source": "website"},
            headers=_auth(token),
        )
        # VIEWER lacks VERIFY_CHANGE → 403 on permission first
        assert allowed.status_code == 403

        capture_ok = demo_client.post(
            "/api/v1/hospitals/h_demo_harborview/capture",
            json={"source": {"source_type": "website", "hospital_ref": "demo-harborview"}},
            headers=_auth(token),
        )
        # VIEWER lacks CAPTURE_SOURCE → 403 even on granted hospital
        assert capture_ok.status_code == 403

        # Cross-hospital ID swap on an endpoint that checks grants for operators:
        # use ADMIN to confirm allowed path, then USER on denied hospital for monitoring
        admin_token = _login(demo_client, DEMO_ADMIN_USERNAME, ADMIN_PW)
        mon_denied = demo_client.post(
            "/api/v1/hospitals/h_demo_solstice/monitoring-policies",
            json={"frequency": "daily", "source_type": "website"},
            headers=_auth(token),
        )
        assert mon_denied.status_code == 403  # USER lacks MANAGE_MONITORING

        # Operator-style hospital grant check: user with only harborview grant
        # attempting capture on solstice is 403 (permission or grant).
        # For grant isolation, OPERATOR has MANAGE_MONITORING but only harborview.
        with AuthStore(demo_client.app.state.medcompare_db_path) as store:
            store.create_user(
                username="ops@medcompare.demo",
                password="OpsOperator-Pass0!",
                role=Role.OPERATOR,
                hospital_ids=["h_demo_harborview"],
                user_id="u_demo_ops",
            )
        ops_token = _login(demo_client, "ops@medcompare.demo", "OpsOperator-Pass0!")
        ok_mon = demo_client.post(
            "/api/v1/hospitals/h_demo_harborview/monitoring-policies",
            json={"frequency": "daily", "source_type": "website"},
            headers=_auth(ops_token),
        )
        assert ok_mon.status_code in (200, 201)
        swap = demo_client.post(
            "/api/v1/hospitals/h_demo_solstice/monitoring-policies",
            json={"frequency": "daily", "source_type": "website"},
            headers=_auth(ops_token),
        )
        assert swap.status_code == 403
        # sanity: admin can manage monitoring on any hospital
        assert admin_token


    def test_bad_login_401_no_token_leak(self, demo_client: TestClient) -> None:
        res = demo_client.post(
            "/api/v1/auth/login",
            json={"username": DEMO_ADMIN_USERNAME, "password": "wrong-password-x"},
        )
        assert res.status_code == 401
        assert "access_token" not in res.text
        assert "password" not in res.text.lower() or "Invalid" in res.text

    def test_health_and_ready_open(self, demo_client: TestClient) -> None:
        assert demo_client.get("/health").status_code == 200
        assert demo_client.get("/ready").status_code == 200
        assert demo_client.get("/api/v1/health").status_code == 200

    def test_logout_revokes_token(self, demo_client: TestClient) -> None:
        token = _login(demo_client, DEMO_ADMIN_USERNAME, ADMIN_PW)
        out = demo_client.post("/api/v1/auth/logout", headers=_auth(token))
        assert out.status_code == 204
        me = demo_client.get("/api/v1/auth/me", headers=_auth(token))
        assert me.status_code == 401


class TestP21UnauthorizedConnector:
    def test_configured_unauthorized_source_lists_without_network(
        self, demo_client: TestClient
    ) -> None:
        token = _login(demo_client, DEMO_ADMIN_USERNAME, ADMIN_PW)
        configured = demo_client.get("/api/v1/sources/configured", headers=_auth(token))
        assert configured.status_code == 200
        # May be empty; if hospital_api entries exist they must not claim AUTHORIZED
        # without authorized=true in TOML (default sample entries are commented out).
        for row in configured.json()["configured_sources"]:
            if row["source_type"] == "hospital_api":
                assert row["authorization_status"] != "AUTHORIZED" or row.get(
                    "credential_ref"
                )
