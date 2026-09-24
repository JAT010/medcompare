"""Auth settings factory (P24) — env defaults, explicit overrides."""

from __future__ import annotations

import os

from hospital_extractor.auth.models import AuthMode, AuthSettings

__all__ = ["auth_settings"]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_tuple(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def auth_settings(**overrides: object) -> AuthSettings:
    """Settings with environment defaults, then keyword overrides.

    Environment:

    - ``MEDCOMPARE_REQUIRE_AUTH`` — force auth on/off (default: implied by mode)
    - ``MEDCOMPARE_AUTH_MODE`` — ``development`` | ``production``
    - ``MEDCOMPARE_AUTH_TOKEN_TTL_SECONDS``
    - ``MEDCOMPARE_TRUSTED_HOSTS`` — comma-separated
    - ``MEDCOMPARE_CORS_ORIGINS`` — comma-separated
    - ``MEDCOMPARE_LOGIN_RATE_LIMIT`` / ``MEDCOMPARE_LOGIN_RATE_WINDOW_SECONDS``
    """
    mode_raw = os.environ.get("MEDCOMPARE_AUTH_MODE", AuthMode.DEVELOPMENT.value)
    try:
        mode = AuthMode(mode_raw.strip().lower())
    except ValueError:
        mode = AuthMode.DEVELOPMENT

    require_set = "MEDCOMPARE_REQUIRE_AUTH" in os.environ
    # Production fails closed unless explicitly disabled (tests may opt out).
    default_require = mode is AuthMode.PRODUCTION
    require_auth = (
        _env_bool("MEDCOMPARE_REQUIRE_AUTH", default_require)
        if require_set
        else default_require
    )

    trusted = _env_tuple("MEDCOMPARE_TRUSTED_HOSTS") or (
        "localhost",
        "127.0.0.1",
        "testserver",
    )
    cors = _env_tuple("MEDCOMPARE_CORS_ORIGINS")

    data: dict[str, object] = {
        "require_auth": require_auth,
        "mode": mode,
        "token_ttl_seconds": _env_int("MEDCOMPARE_AUTH_TOKEN_TTL_SECONDS", 12 * 3600),
        "allow_dev_header": mode is AuthMode.DEVELOPMENT,
        "login_rate_limit": _env_int("MEDCOMPARE_LOGIN_RATE_LIMIT", 10),
        "login_rate_window_seconds": _env_int("MEDCOMPARE_LOGIN_RATE_WINDOW_SECONDS", 60),
        "trusted_hosts": trusted,
        "cors_origins": cors,
    }
    data.update(overrides)
    settings = AuthSettings.model_validate(data)
    # Dev header only ever in development mode (fail closed in production).
    if settings.mode is AuthMode.PRODUCTION and settings.allow_dev_header:
        settings = AuthSettings.model_validate(
            {**settings.model_dump(), "allow_dev_header": False}
        )
    return settings
