"""Structured logging setup for the extractor.

Provides:

- A consistent, machine-parseable text format (default) and an optional
  structured key=value format for production operators.
- A secret-scrubbing filter so passwords, tokens, cookies, and Authorization
  headers never reach the log stream (defense in depth alongside API sanitizing).

Event fields commonly used by workers/ops (request_id, run_id, hospital_id,
source_id, policy_id, stage, outcome, duration_ms) are attached via
``LoggerAdapter`` / ``extra=`` and rendered when present.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_STRUCTURED_FORMAT = (
    "%(asctime)s level=%(levelname)s logger=%(name)s "
    "event=%(event)s %(message)s"
)

# Patterns that must never appear in log output (case-insensitive).
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(password|passwd|pwd)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\b(token|secret|api[_-]?key|access[_-]?key)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\bauthorization\s*[=:]\s*\S+(?:\s+\S+)?"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)\bset-cookie\s*[=:]\s*\S+"),
    re.compile(r"(?i)\bMEDCOMPARE_BOOTSTRAP_ADMIN_PASSWORD\s*[=:]\s*\S+"),
)

_REDACTED = "[REDACTED]"


class SecretScrubbingFilter(logging.Filter):
    """Rewrite secret-looking substrings out of the formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        scrubbed = message
        for pattern in _SECRET_PATTERNS:
            scrubbed = pattern.sub(_REDACTED, scrubbed)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = ()
        return True


class _EventDefaults(logging.Filter):
    """Ensure structured format keys exist even when callers omit them."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "event"):
            record.event = record.funcName or record.name
        return True


def configure_logging(
    level: str = "INFO",
    *,
    logger_name: str = "hospital_extractor",
    fmt: str | None = None,
    structured: bool = False,
) -> logging.Logger:
    """Configure the root extractor logger and return it.

    Attaches a single console handler with a consistent format. Calling again
    with the same logger does not duplicate handlers; the level is updated.
    A :class:`SecretScrubbingFilter` is always attached.
    """
    normalized_level = level.upper()
    logger = logging.getLogger(logger_name)
    logger.setLevel(normalized_level)
    logger.propagate = False

    if fmt is None:
        fmt = _STRUCTURED_FORMAT if structured else _DEFAULT_FORMAT

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S"))
        handler.addFilter(SecretScrubbingFilter())
        if structured:
            handler.addFilter(_EventDefaults())
        logger.addHandler(handler)
    else:
        logger.handlers[0].setLevel(normalized_level)
        # Ensure scrubbing filter is present on reconfiguration.
        if not any(isinstance(f, SecretScrubbingFilter) for f in logger.handlers[0].filters):
            logger.handlers[0].addFilter(SecretScrubbingFilter())
    return logger


def get_logger(name: str = "hospital_extractor") -> logging.Logger:
    """Return a child logger of the configured root logger."""
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str = "",
    **fields: Any,
) -> None:
    """Emit a structured operational log line.

    ``fields`` may include request_id, run_id, hospital_id, source_id,
    policy_id, stage, outcome, duration_ms — rendered as ``key=value`` after
    the message. Secret-looking keys are not special-cased here; the
    scrubbing filter redacts values that look like credentials.
    """
    parts = [f"event={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger.log(level, "%s %s", message or event, " ".join(parts))
