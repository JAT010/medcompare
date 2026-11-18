"""Snapshot capture, fingerprinting and change detection."""

from hospital_extractor.snapshots.change_detection import DetectedChange, compute_changes
from hospital_extractor.snapshots.fingerprint import VOLATILE_KEYS, canonical_digest
from hospital_extractor.snapshots.service import (
    CaptureResult,
    SnapshotService,
    SnapshotServiceError,
)

__all__ = [
    "CaptureResult",
    "DetectedChange",
    "SnapshotService",
    "SnapshotServiceError",
    "VOLATILE_KEYS",
    "canonical_digest",
    "compute_changes",
]
