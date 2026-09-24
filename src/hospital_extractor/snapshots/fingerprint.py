"""Deterministic semantic fingerprinting of canonical datasets.

A fingerprint answers a single question: *do two snapshots represent the same
data?*  It hashes a canonical serialization of the dataset's SEMANTIC content
only, so an unchanged hospital never looks "changed" just because a re-capture
happened later, crawled faster, or bumped a timestamp.

What IS reflected by the fingerprint:

- ``hospital`` overview, ``contacts``, ``infrastructure`` single-record
  fields (name, type, beds, address, ...)
- the ordered member sets of every collection category: facilities,
  departments, specialities, doctors, treatments, procedures, technologies,
  accreditations

What is NOT reflected (run / provenance / identity noise):

- ``dataset.source`` — retrieval timestamps, endpoint identity, raw payload
  metadata, notes
- ``dataset.quality`` — crawl/page/timing/dedup statistics
- every record's provenance fields: ``source_url``, ``page_url``,
  ``page_title``, ``extracted_at``, ``source_type``, ``context``,
  ``extraction_method``, ``source_strength``, ``confidence``
- the ``sources`` provenance worksheet (derived, not semantic)

Collections are hashed ORDER-INSENSITIVELY: the canonical sort key of each
item is hashed in sorted order, so a doctor list that merely reorders does not
produce a different fingerprint.

Canonical serialization rules: JSON with ``sort_keys=True``, compact
separators and ``ensure_ascii=False`` so Unicode text hashes consistently
across platforms.  The digest is a hex-encoded SHA-256.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES

__all__ = ["PROVENANCE_FIELDS", "canonical_digest"]

# Provenance-only fields carried by every entity record.  They never reflect
# semantic content, so they are excluded from the fingerprint.
PROVENANCE_FIELDS = frozenset(
    {
        "source_url",
        "page_url",
        "page_title",
        "extracted_at",
        "source_type",
        "context",
        "extraction_method",
        "source_strength",
        "confidence",
    }
)


def _entity_signature(record: Any) -> str:
    """Deterministic canonical string for one entity record."""
    data = {
        key: value
        for key, value in record.model_dump(mode="json").items()
        if key not in PROVENANCE_FIELDS
    }
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_digest(dataset: HospitalDataset) -> str:
    """Return the semantic SHA-256 fingerprint of *dataset*.

    Two datasets for the same hospital+source with equal fingerprints are
    guaranteed to compare as "no change" (the reverse is also true: any real
    field-level change alters the fingerprint).
    """
    doc: dict[str, Any] = {}
    for category in CANONICAL_CATEGORIES:
        key = category.key
        if category.single_record:
            if key == "hospital":
                record = getattr(dataset, "hospital", None)
                doc[key] = _entity_signature(record) if record is not None else None
                continue
            # contact / infrastructure are single-merge but stored as lists.
        items = getattr(dataset, key, [])
        signatures = sorted(_entity_signature(item) for item in items or [])
        doc[key] = signatures
    # The provenance worksheet is derived, not semantic — leave it out.
    blob = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()