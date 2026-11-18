"""Deterministic semantic fingerprinting of captured hospital payloads.

A fingerprint answers: *do two snapshots represent the same data?* It hashes a
canonical serialization of the payload with run/capture noise removed:

- Volatile timestamp keys (``retrieved_at``, ``captured_at``, ``generated_at``,
  ``fetched_at``) are stripped at every level, so re-fetching identical data
  never looks "changed".
- Object keys are sorted recursively.
- Lists whose members are all objects with a string ``name`` are sorted by name,
  so a reordered collection does not look changed. Other lists keep order
  (order is meaningful there).
- JSON uses compact separators and ``ensure_ascii=False``; the digest is hex
  SHA-256.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = ["VOLATILE_KEYS", "canonical_digest", "strip_provenance"]

VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "retrieved_at",
        "captured_at",
        "generated_at",
        "fetched_at",
        "last_sync_at",
        "last_synced_at",
    }
)

# Top-level wrapper key carrying acquisition provenance (not hospital data).
# Excluded from the fingerprint and from change detection so re-captures of
# the same data compare equal even when provenance metadata differs.
PROVENANCE_KEY = "source"


def strip_provenance(payload: Any) -> Any:
    """Remove the top-level ``source`` provenance wrapper, if present."""
    if not isinstance(payload, dict):
        return payload
    return {key: value for key, value in payload.items() if key != PROVENANCE_KEY}


def _canonicalize(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            str(key): _canonicalize(value)
            for key, value in sorted(node.items(), key=lambda kv: str(kv[0]))
            if str(key) not in VOLATILE_KEYS
        }
    if isinstance(node, list):
        items = [_canonicalize(item) for item in node]
        if all(isinstance(item, dict) and isinstance(item.get("name"), str) for item in items):
            return sorted(items, key=lambda item: str(item.get("name", "")))
        return items
    return node


def canonical_digest(payload: Any) -> str:
    """Return the semantic SHA-256 digest of *payload*."""
    canonical = _canonicalize(payload)
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
