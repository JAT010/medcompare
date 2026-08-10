"""Hashing helpers used for URL deduplication and raw-page storage."""

from __future__ import annotations

import hashlib


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of a byte string."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Return the hex SHA-256 digest of a UTF-8 text string."""
    return sha256_bytes(text.encode("utf-8"))


def short_hash(value: str, length: int = 12) -> str:
    """Return a short, stable hash of *value* for filenames or keys."""
    if length < 1:
        raise ValueError("length must be >= 1")
    return sha256_text(value)[:length]
