"""Production-style startup verification (P25).

Fail-closed checks run before (or as part of) creating the API app so that
invalid production configuration never silently falls back to development
auth or an unusable store. All failure messages are operator-safe: no
passwords, tokens, full paths of secrets, or stack traces are required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hospital_extractor.auth.models import AuthMode, AuthSettings

__all__ = [
    "StartupCheck",
    "StartupConfigError",
    "StartupReport",
    "verify_startup",
]


class StartupConfigError(RuntimeError):
    """Raised when production startup verification fails (fail closed)."""


@dataclass(frozen=True)
class StartupCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class StartupReport:
    ok: bool
    mode: str
    checks: tuple[StartupCheck, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks
            ],
        }


def verify_startup(
    *,
    settings: AuthSettings | None = None,
    db_path: Path | str | None = None,
    templates_dir: Path | str | None = None,
    static_dir: Path | str | None = None,
    raise_on_error: bool = True,
) -> StartupReport:
    """Run deterministic startup checks.

    Production mode fails closed: any failed check raises
    :class:`StartupConfigError` when ``raise_on_error`` is true.
    Development mode reports the same checks but only raises when a
    hard prerequisite (templates) is missing if explicitly requested.
    """
    if settings is None:
        from hospital_extractor.auth.settings import auth_settings

        settings = auth_settings()

    checks: list[StartupCheck] = []
    mode = settings.mode.value

    checks.append(_check_auth_mode(settings))
    checks.append(_check_require_auth(settings))
    checks.append(_check_dev_header(settings))
    checks.append(_check_trusted_hosts(settings))
    checks.append(_check_db_path(db_path))
    checks.append(_check_templates(templates_dir))
    checks.append(_check_static(static_dir))

    ok = all(c.ok for c in checks)
    report = StartupReport(ok=ok, mode=mode, checks=tuple(checks))

    failed = [c for c in checks if not c.ok]
    # Always raise in production when any check fails (fail closed).
    # In development, only raise on template/dir prerequisites so local
    # demos still start; auth misconfiguration is reported, not fatal.
    must_raise = settings.mode is AuthMode.PRODUCTION or any(
        c.name in {"templates", "static"} for c in failed
    )
    if failed and must_raise and raise_on_error:
        names = ", ".join(c.name for c in failed)
        raise StartupConfigError(
            f"startup verification failed ({mode}): {names}"
        )
    return report


def _check_auth_mode(settings: AuthSettings) -> StartupCheck:
    ok = settings.mode in (AuthMode.DEVELOPMENT, AuthMode.PRODUCTION)
    detail = settings.mode.value
    return StartupCheck("auth_mode", ok, detail)


def _check_require_auth(settings: AuthSettings) -> StartupCheck:
    if settings.mode is AuthMode.PRODUCTION:
        ok = settings.require_auth is True
        detail = "production requires require_auth=true"
    else:
        ok = True
        detail = f"require_auth={settings.require_auth}"
    return StartupCheck("require_auth", ok, detail)


def _check_dev_header(settings: AuthSettings) -> StartupCheck:
    if settings.mode is AuthMode.PRODUCTION:
        ok = settings.allow_dev_header is False
        detail = "dev header disabled in production"
    else:
        ok = True
        detail = f"allow_dev_header={settings.allow_dev_header}"
    return StartupCheck("dev_header", ok, detail)


def _check_trusted_hosts(settings: AuthSettings) -> StartupCheck:
    hosts = settings.trusted_hosts
    if settings.mode is AuthMode.PRODUCTION:
        # Wildcard-only trust is not fail-closed for Host header checks.
        ok = bool(hosts) and "*" not in hosts
        detail = f"{len(hosts)} host(s)" if ok else "must list explicit hosts (no wildcard)"
    else:
        ok = True
        detail = f"{len(hosts)} host(s)"
    return StartupCheck("trusted_hosts", ok, detail)


def _check_db_path(db_path: Path | str | None) -> StartupCheck:
    if db_path is None:
        from hospital_extractor.storage import default_database_path

        db_path = default_database_path()
    path = Path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Touch-create only if missing so we know the directory is writable.
        if not path.exists():
            probe = path.parent / f".{path.name}.startup-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        else:
            # Open read-only-ish: ensure we can stat a file we own/create.
            with path.open("a+b"):
                pass
        return StartupCheck("database", True, "writable")
    except OSError as exc:
        return StartupCheck("database", False, f"not writable ({exc.__class__.__name__})")


def _check_templates(templates_dir: Path | str | None) -> StartupCheck:
    if templates_dir is None:
        from hospital_extractor.api.app import _TEMPLATES_DIR as resolved

        path = Path(resolved)
    else:
        path = Path(templates_dir)
    ok = path.is_dir() and (path / "index.html").is_file()
    return StartupCheck("templates", ok, "present" if ok else "missing index.html")


def _check_static(static_dir: Path | str | None) -> StartupCheck:
    if static_dir is None:
        from hospital_extractor.api.app import _STATIC_DIR as resolved

        path = Path(resolved)
    else:
        path = Path(static_dir)
    ok = path.is_dir() and any(path.iterdir())
    return StartupCheck("static", ok, "present" if ok else "missing")
