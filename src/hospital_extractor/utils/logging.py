"""Structured logging setup for the extractor."""

from __future__ import annotations

import logging
import sys

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging(
    level: str = "INFO",
    *,
    logger_name: str = "hospital_extractor",
    fmt: str = _DEFAULT_FORMAT,
) -> logging.Logger:
    """Configure the root extractor logger and return it.

    Attaches a single console handler with a consistent, machine-parseable
    format. Calling again with the same logger does not duplicate handlers.
    """
    normalized_level = level.upper()
    logger = logging.getLogger(logger_name)
    logger.setLevel(normalized_level)
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S"))
        logger.addHandler(handler)
    else:
        logger.handlers[0].setLevel(normalized_level)
    return logger


def get_logger(name: str = "hospital_extractor") -> logging.Logger:
    """Return a child logger of the configured root logger."""
    return logging.getLogger(name)
