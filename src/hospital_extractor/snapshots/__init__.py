"""Dataset snapshots: fingerprinting and change detection.

P19-A foundation for the future (P19-B) verification workflow:

- :mod:`hospital_extractor.snapshots.fingerprint` — deterministic semantic
  content hash of a canonical dataset.
- :mod:`hospital_extractor.snapshots.change_detection` — field-aware,
  collection-aware, identity-keyed diff between consecutive snapshots.

Change detection is an **explicit** operation: capture a dataset, compare it
against the latest snapshot of the same hospital+source, persist the new
snapshot and the resulting change set.  Nothing schedules or triggers this
automatically (scheduled crawling is P19-B+).
"""

from hospital_extractor.snapshots.change_detection import (
    build_change_set,
    compute_changes,
)
from hospital_extractor.snapshots.fingerprint import canonical_digest

__all__ = [
    "build_change_set",
    "canonical_digest",
    "compute_changes",
]