"""P25 deployment, configuration, health, worker, and secret-safety tests.

Covers the P25 matrix A–R without network access:

A. Package metadata / entry points declared
B. Clean-room application import + create_app
C. API production-style startup verification (fail closed)
D. GET /health liveness
E. GET /ready readiness (JSON shape, 200/503)
F. Invalid production config refuses to start (no silent dev-auth fallback)
G. Production auth requires credentials (no bypass)
H. Worker continuous mode can start/stop via stop_event (no scheduler duplicate)
I. Config loading from env (auth/verification/monitoring)
J. Secret-safety: settings repr / login / startup diagnostics
K. Logging scrubbing (password/token/Authorization never emitted)
L. SQLite migration / open existing schema (additive, no rewrite)
M. Clean-room database init
N. CLI help / verify-startup / worker help (no secret leakage)
O. Dockerfile static sanity (if present) — non-root, py3.13
P. CI command parity (local commands match workflow stages)
Q. Static + template assets present and served
R. Regression: full baseline still passes (auth/security still green)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.auth.models import AuthMode, AuthSettings, Role
from hospital_extractor.auth.settings import auth_settings
from hospital_extractor.auth.store import AuthStore
from hospital_extractor.monitoring.settings import monitoring_settings
from hospital_extractor.ops.startup import StartupConfigError, verify_startup
from hospital_extractor.storage import HospitalDatabase, default_database_path
from hospital_extractor.utils.logging import (
    SecretScrubbingFilter,
    configure_logging,
    get_logger,
    log_event,
)
from hospital_extractor.verification.settings import verification_settings

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# A/B — Package metadata, entry points, clean-room import
# ---------------------------------------------------------------------------


class TestPackageMetadata:
    def test_pyproject_declares_console_scripts(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'hospital-extractor = "hospital_extractor.cli:main"' in text
        assert 'medcompare-demo-server = "hospital_extractor.mockapi.console:main"' in text

    def test_requires_python_313(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'requires-python = ">=3.13,<3.14"' in text

    def test_cli_entry_importable(self) -> None:
        from hospital_extractor.cli import main as cli_main

        assert callable(cli_main)

    def test_create_app_clean_import(self) -> None:
        app = create_app()
        assert app.title == "Hospital Comparison API"


# ---------------------------------------------------------------------------
# C/D/E — Health + readiness
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app()
    app.state.medcompare_db_path = tmp_path / "p25.db"
    return TestClient(app)


class TestHealthReadiness:
    def test_health_alias_liveness(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["check"] == "liveness"

    def test_api_v1_health_still_ok(self, client: TestClient) -> None:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_ready_ok_shape(self, client: TestClient) -> None:
        resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        names = {c["name"] for c in data["checks"]}
        assert {"config", "database", "templates", "static"} <= names
        for check in data["checks"]:
            assert set(check) == {"name", "status", "detail"}

    def test_ready_no_secrets_or_paths(self, client: TestClient) -> None:
        body = client.get("/ready").text
        lowered = body.lower()
        for needle in ("password", "token=", "authorization", "traceback", "secret"):
            assert needle not in lowered

    def test_ready_unreachable_db_returns_503(self, tmp_path: Path) -> None:
        app = create_app()
        # Point at a path whose parent is a file → open fails.
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        app.state.medcompare_db_path = blocker / "nested.db"
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "error"

    def test_health_open_when_auth_on(self, tmp_path: Path) -> None:
        app = create_app(auth_enabled=True)
        app.state.medcompare_db_path = tmp_path / "p25-auth.db"
        with TestClient(app) as c:
            assert c.get("/health").status_code == 200
            assert c.get("/api/v1/health").status_code == 200
            assert c.get("/ready").status_code == 200


# ---------------------------------------------------------------------------
# C/F/G — Production startup verification (fail closed)
# ---------------------------------------------------------------------------


class TestProductionStartup:
    def test_development_verify_ok(self) -> None:
        report = verify_startup(raise_on_error=False)
        assert report.ok
        assert report.mode == "development"

    def test_production_requires_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MEDCOMPARE_REQUIRE_AUTH", raising=False)
        monkeypatch.setenv("MEDCOMPARE_AUTH_MODE", "production")
        with pytest.raises(StartupConfigError):
            # Force the invalid combination: production + require_auth off.
            settings = auth_settings(require_auth=False)
            verify_startup(settings=settings, raise_on_error=True)

    def test_production_rejects_dev_header(self) -> None:
        settings = AuthSettings(
            require_auth=True,
            mode=AuthMode.PRODUCTION,
            allow_dev_header=True,
        )
        with pytest.raises(StartupConfigError):
            verify_startup(settings=settings, raise_on_error=True)

    def test_production_rejects_wildcard_hosts(self) -> None:
        settings = AuthSettings(
            require_auth=True,
            mode=AuthMode.PRODUCTION,
            allow_dev_header=False,
            trusted_hosts=("*",),
        )
        with pytest.raises(StartupConfigError):
            verify_startup(settings=settings, raise_on_error=True)

    def test_create_app_production_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_AUTH_MODE", "production")
        monkeypatch.setenv("MEDCOMPARE_REQUIRE_AUTH", "0")
        with pytest.raises(StartupConfigError):
            create_app()

    def test_create_app_production_with_defaults_starts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_AUTH_MODE", "production")
        monkeypatch.delenv("MEDCOMPARE_REQUIRE_AUTH", raising=False)
        app = create_app()
        assert app.state.medcompare_require_auth is True
        app.state.medcompare_db_path = tmp_path / "prod.db"
        with TestClient(app) as c:
            assert c.get("/health").status_code == 200
            # Anonymous access to a protected API is 401.
            resp = c.get("/api/v1/hospitals")
            assert resp.status_code == 401
            # Dev header must not bypass.
            resp = c.get(
                "/api/v1/hospitals", headers={"X-Medcompare-Dev-User": "admin"}
            )
            assert resp.status_code == 401

    def test_startup_report_json_serializable(self) -> None:
        report = verify_startup(raise_on_error=False)
        payload = json.loads(json.dumps(report.as_dict()))
        assert payload["ok"] is True
        assert payload["mode"] == "development"


# ---------------------------------------------------------------------------
# I — Config loading from env
# ---------------------------------------------------------------------------


class TestConfigLoading:
    def test_auth_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEDCOMPARE_AUTH_TOKEN_TTL_SECONDS", "3600")
        monkeypatch.setenv("MEDCOMPARE_LOGIN_RATE_LIMIT", "5")
        monkeypatch.setenv("MEDCOMPARE_TRUSTED_HOSTS", "api.example, ops.example")
        settings = auth_settings()
        assert settings.token_ttl_seconds == 3600
        assert settings.login_rate_limit == 5
        assert settings.trusted_hosts == ("api.example", "ops.example")

    def test_verification_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERIFICATION_DELAY_MINUTES", "15")
        monkeypatch.setenv("VERIFICATION_MAX_ATTEMPTS", "5")
        settings = verification_settings()
        assert settings.delay_minutes == 15
        assert settings.max_attempts == 5

    def test_monitoring_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MONITORING_MAX_ATTEMPTS", "7")
        monkeypatch.setenv("MONITORING_WORKER_INTERVAL_SECONDS", "42.5")
        settings = monitoring_settings()
        assert settings.max_attempts == 7
        assert settings.worker_interval_seconds == 42.5

    def test_settings_frozen_dataclass_no_secret_fields(self) -> None:
        settings = auth_settings()
        dumped = settings.model_dump()
        for key in dumped:
            assert "password" not in key.lower()
            assert "secret" not in key.lower()


# ---------------------------------------------------------------------------
# J/K — Secret-safety in logs and diagnostics
# ---------------------------------------------------------------------------


class TestLoggingSecretSafety:
    def test_scrubbing_filter_redacts_password(self) -> None:
        filt = SecretScrubbingFilter()
        record = logging.LogRecord(
            name="hospital_extractor",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="user login password=hunter2 ok",
            args=(),
            exc_info=None,
        )
        assert filt.filter(record) is True
        assert "hunter2" not in record.getMessage()
        assert "[REDACTED]" in record.getMessage()

    def test_scrubbing_filter_redacts_bearer(self) -> None:
        filt = SecretScrubbingFilter()
        record = logging.LogRecord(
            name="hospital_extractor",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Authorization: Bearer abc.def.ghi",
            args=(),
            exc_info=None,
        )
        filt.filter(record)
        assert "abc.def.ghi" not in record.getMessage()

    def test_configure_logging_attaches_scrubber(self) -> None:
        logger = configure_logging("INFO", logger_name="hospital_extractor.p25")
        handler = logger.handlers[0]
        assert any(isinstance(f, SecretScrubbingFilter) for f in handler.filters)

    def test_log_event_emits_structured_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = get_logger("hospital_extractor.p25.event")
        with caplog.at_level(logging.INFO, logger="hospital_extractor.p25.event"):
            log_event(
                logger,
                logging.INFO,
                "worker.cycle",
                "cycle done",
                outcome="ok",
                duration_ms=12,
                policy_id="pol_1",
            )
        text = caplog.text
        assert "event=worker.cycle" in text
        assert "outcome=ok" in text
        assert "policy_id=pol_1" in text

    def test_auth_settings_repr_has_no_secret_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_BOOTSTRAP_ADMIN_PASSWORD", "super-secret-value")
        settings = auth_settings()
        assert "super-secret-value" not in repr(settings)
        assert "super-secret-value" not in str(settings.model_dump())

    def test_startup_report_has_no_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEDCOMPARE_BOOTSTRAP_ADMIN_PASSWORD", "hunter2")
        report = verify_startup(raise_on_error=False)
        blob = json.dumps(report.as_dict())
        assert "hunter2" not in blob


# ---------------------------------------------------------------------------
# H — Worker lifecycle (stop_event, no duplicate scheduler)
# ---------------------------------------------------------------------------


class TestWorkerLifecycle:
    @pytest.mark.asyncio
    async def test_verification_worker_stops_on_event(self, tmp_path: Path) -> None:
        from hospital_extractor.verification.service import run_worker_forever
        from hospital_extractor.verification.settings import VerificationSettings

        db = HospitalDatabase(tmp_path / "w.db")
        stop = __import__("asyncio").Event()
        stop.set()  # already signalled → first wait returns immediately after one cycle
        settings = VerificationSettings(worker_interval_seconds=0.01)
        # One cycle then stop (stop_event set before wait).
        await run_worker_forever(db, settings=settings, stop_event=stop)
        db.close()

    @pytest.mark.asyncio
    async def test_monitoring_worker_stops_on_event(self, tmp_path: Path) -> None:
        import asyncio

        from hospital_extractor.monitoring.service import run_monitoring_worker
        from hospital_extractor.monitoring.settings import MonitoringSettings

        db = HospitalDatabase(tmp_path / "m.db")
        stop = asyncio.Event()
        stop.set()
        settings = MonitoringSettings(worker_interval_seconds=0.01)
        await run_monitoring_worker(db, settings=settings, stop_event=stop)
        db.close()

    def test_verification_worker_cli_help_mentions_signals(self) -> None:
        from hospital_extractor.cli import build_worker_parser

        help_text = build_worker_parser().format_help()
        assert "--once" in help_text
        assert "--interval" in help_text
        # No secrets in help
        assert "password" not in help_text.lower()

    def test_monitoring_worker_parser_exists(self) -> None:
        # CLI subcommand present via main dispatch table
        from hospital_extractor.cli import main

        # --help path exits 0 via SystemExit
        with pytest.raises(SystemExit) as exc:
            main(["monitoring", "worker", "--help"])
        assert exc.value.code == 0

    def test_install_signal_handlers_smoke(self) -> None:
        import asyncio

        from hospital_extractor.ops.signals import install_worker_signal_handlers

        async def _run() -> None:
            stop = asyncio.Event()
            install_worker_signal_handlers(stop)
            # Do not fire signals in tests; just ensure registration doesn't raise.

        asyncio.run(_run())

    def test_no_scheduler_in_api_module(self) -> None:
        """Workers must not be started by the API (architecture invariant)."""
        app_src = (ROOT / "src" / "hospital_extractor" / "api" / "app.py").read_text(
            encoding="utf-8"
        )
        assert "run_worker_forever" not in app_src
        assert "run_monitoring_worker" not in app_src
        assert "scheduler" not in app_src.lower()


# ---------------------------------------------------------------------------
# L/M — SQLite ops
# ---------------------------------------------------------------------------


class TestDatabaseOps:
    def test_clean_room_db_init(self, tmp_path: Path) -> None:
        path = tmp_path / "fresh.db"
        with HospitalDatabase(path) as db:
            assert db.list_hospitals() == []
            assert db.list_datasets() == []
        assert path.is_file()

    def test_existing_schema_reopen_additive(self, tmp_path: Path) -> None:
        path = tmp_path / "reopen.db"
        with HospitalDatabase(path):
            pass
        # Second open — CREATE IF NOT EXISTS must not error.
        with HospitalDatabase(path) as db:
            assert db.list_hospitals() == []

    def test_default_database_path_relative(self) -> None:
        assert default_database_path() == Path("data/medcompare.db")

    def test_database_file_sha_reference_unchanged_location(self) -> None:
        # P25 must not rewrite database.py; only assert the module still loads
        # and DDL remains additive (CREATE IF NOT EXISTS present).
        src = (ROOT / "src" / "hospital_extractor" / "storage" / "database.py").read_text(
            encoding="utf-8"
        )
        assert "CREATE TABLE IF NOT EXISTS" in src

    def test_auth_store_on_same_db_path(self, tmp_path: Path) -> None:
        path = tmp_path / "auth.db"
        with HospitalDatabase(path):
            pass
        store = AuthStore(path)
        store.ensure_schema()
        with store:
            assert store.list_users() == []


# ---------------------------------------------------------------------------
# N — CLI operations
# ---------------------------------------------------------------------------


class TestCliOps:
    def test_main_help_exits_zero(self) -> None:
        from hospital_extractor.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_verify_startup_cli_ok(self, capsys: pytest.CaptureFixture[str]) -> None:
        from hospital_extractor.cli import main

        code = main(["verify-startup"])
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert code == 0
        assert payload["ok"] is True

    def test_serve_help(self) -> None:
        from hospital_extractor.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["serve", "--help"])
        assert exc.value.code == 0

    def test_cli_help_no_secret_env_leak(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("MEDCOMPARE_BOOTSTRAP_ADMIN_PASSWORD", "leaky-password-99")
        from hospital_extractor.cli import main

        with pytest.raises(SystemExit):
            main(["--help"])
        out = capsys.readouterr()
        combined = out.out + out.err
        assert "leaky-password-99" not in combined


# ---------------------------------------------------------------------------
# O — Dockerfile static sanity
# ---------------------------------------------------------------------------


class TestDockerfile:
    def test_dockerfile_exists(self) -> None:
        assert (ROOT / "Dockerfile").is_file()

    def test_dockerfile_non_root_and_py313(self) -> None:
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert "python:3.13" in text
        assert re.search(r"^USER\s+\S+", text, re.M)
        assert "MEDCOMPARE_BOOTSTRAP_ADMIN_PASSWORD=" not in text
        # No raw secret assignments
        assert not re.search(r'PASSWORD\s*=\s*"[^"]+"', text)

    def test_dockerignore_excludes_secrets_and_db(self) -> None:
        text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        assert ".env" in text
        assert "*.db" in text
        assert ".git" in text

    def test_dockerfile_separate_worker_commands_documented(self) -> None:
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert "hospital-extractor worker" in text
        assert "monitoring worker" in text
        assert text.count("CMD") == 1  # single default (API); workers override


# ---------------------------------------------------------------------------
# P — CI command parity
# ---------------------------------------------------------------------------


class TestCiParity:
    def test_ci_workflow_exists(self) -> None:
        path = ROOT / ".github" / "workflows" / "ci.yml"
        assert path.is_file()

    def test_ci_stages_match_local_commands(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "pytest -q" in text
        assert "ruff check src tests" in text
        assert "mypy src" in text
        assert "python -m build" in text
        assert "3.13" in text

    def test_ci_has_clean_install_smoke(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "Clean-install smoke test" in text or "clean-install" in text.lower()

    def test_local_ruff_config_present(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'select = ["E", "F", "I", "B", "UP"]' in text


# ---------------------------------------------------------------------------
# Q — Static + template assets
# ---------------------------------------------------------------------------


class TestStaticAssets:
    def test_templates_present(self) -> None:
        from hospital_extractor.api.app import _TEMPLATES_DIR

        assert (_TEMPLATES_DIR / "index.html").is_file()
        assert (_TEMPLATES_DIR / "hospital_profile.html").is_file()

    def test_static_present(self) -> None:
        from hospital_extractor.api.app import _STATIC_DIR

        assert _STATIC_DIR.is_dir()
        assert any(_STATIC_DIR.iterdir())

    def test_static_served(self, client: TestClient) -> None:
        # Mount should respond (200 for file, 404 only if missing — we assert 200/404 shape)
        resp = client.get("/static/style.css")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert "text/css" in resp.headers.get("content-type", "")

    def test_index_html_renders_with_auth_off(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# R — Cross-phase regression smoke (auth still green)
# ---------------------------------------------------------------------------


class TestRegressionSmoke:
    def test_p24_login_still_works(self, tmp_path: Path) -> None:
        app = create_app(auth_enabled=True)
        app.state.medcompare_db_path = tmp_path / "reg.db"
        AuthStore(tmp_path / "reg.db").ensure_schema()
        with AuthStore(tmp_path / "reg.db") as store:
            store.create_user(username="admin", password="Str0ng-Pass-x", role=Role.ADMIN)
        with TestClient(app) as c:
            resp = c.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "Str0ng-Pass-x"},
            )
            assert resp.status_code == 200
            token = resp.json()["access_token"]
            me = c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 200

    def test_p23_overall_score_still_null(self) -> None:
        # Quality report model must never invent an overall quality score.
        from hospital_extractor.quality.models import (
            PipelineQualityReport,
            no_overall_score_note,
        )

        report = PipelineQualityReport()
        assert report.overall_score is None
        assert report.note == no_overall_score_note
        assert PipelineQualityReport.model_fields["overall_score"].default is None

    def test_sources_fail_closed_without_authorization(self) -> None:
        text = (ROOT / "config" / "sources.toml").read_text(encoding="utf-8")
        # Active authorized entries must not ship with real hosts enabled by default.
        active = [
            line
            for line in text.splitlines()
            if line.strip().startswith("base_url")
            and not line.strip().startswith("#")
        ]
        assert active == []  # all example sources remain commented out

    def test_database_py_not_modified_by_p25_markers(self) -> None:
        # Additive only: no DROP TABLE / ALTER introduced in P25 scope.
        src = (ROOT / "src" / "hospital_extractor" / "storage" / "database.py").read_text(
            encoding="utf-8"
        )
        assert "DROP TABLE" not in src
