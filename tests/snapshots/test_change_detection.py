"""Fingerprint and change-detection unit tests — deterministic and offline."""

from __future__ import annotations

import copy

from hospital_extractor.snapshots.change_detection import compute_changes
from hospital_extractor.snapshots.fingerprint import canonical_digest
from hospital_extractor.sources.mock_hospital_api import VARIANTS


def _v1() -> dict:
    return copy.deepcopy(VARIANTS["default"])


def _changed() -> dict:
    return copy.deepcopy(VARIANTS["changed"])


class TestCanonicalDigest:
    def test_key_order_does_not_matter(self) -> None:
        payload = {"b": 2, "a": {"z": 1, "y": 2}}
        shuffled = {"a": {"y": 2, "z": 1}, "b": 2}
        assert canonical_digest(payload) == canonical_digest(shuffled)

    def test_volatile_keys_are_ignored(self) -> None:
        first = {"hospital": {"name": "X", "retrieved_at": "2026-01-01T00:00:00Z"}}
        second = {"hospital": {"name": "X", "retrieved_at": "2026-02-02T00:00:00Z"}}
        assert canonical_digest(first) == canonical_digest(second)

    def test_named_list_reorder_is_stable(self) -> None:
        first = {"departments": [{"name": "Cardiology"}, {"name": "Oncology"}]}
        second = {"departments": [{"name": "Oncology"}, {"name": "Cardiology"}]}
        assert canonical_digest(first) == canonical_digest(second)

    def test_scalar_list_order_matters(self) -> None:
        first = {"tags": ["a", "b"]}
        second = {"tags": ["b", "a"]}
        assert canonical_digest(first) != canonical_digest(second)

    def test_semantic_change_changes_digest(self) -> None:
        assert canonical_digest(_v1()) != canonical_digest(_changed())

    def test_identical_payloads_match(self) -> None:
        assert canonical_digest(_v1()) == canonical_digest(_v1())


class TestComputeChanges:
    def test_scalar_modification(self) -> None:
        changes = compute_changes(_v1(), _changed())
        by_path = {c.path: c for c in changes}
        assert by_path["hospital.bed_count"].change_type == "modified"
        assert by_path["hospital.bed_count"].old_value == 100
        assert by_path["hospital.bed_count"].new_value == 120
        assert by_path["hospital.bed_count"].field == "bed_count"
        assert by_path["infrastructure.total_beds"].change_type == "modified"

    def test_collection_member_added(self) -> None:
        changes = compute_changes(_v1(), _changed())
        added = [c for c in changes if c.change_type == "added"]
        robot = [c for c in added if c.entity_name == "Robot-assisted surgery"]
        assert robot, "expected an added procedure change"
        assert robot[0].path == "procedures"

    def test_no_changes_for_identical_payloads(self) -> None:
        assert compute_changes(_v1(), _v1()) == []

    def test_removed_member(self) -> None:
        changes = compute_changes(_changed(), _v1())
        removed = [c for c in changes if c.change_type == "removed"]
        assert any(
            c.entity_name == "Robot-assisted surgery" and c.path == "procedures" for c in removed
        )

    def test_added_and_removed_keys(self) -> None:
        previous = {"hospital": {"name": "X", "bed_count": 100}}
        current = {"hospital": {"name": "X", "documented_capacity": 120}}
        changes = compute_changes(previous, current)
        by_type = {(c.change_type, c.field) for c in changes}
        assert ("added", "documented_capacity") in by_type
        assert ("removed", "bed_count") in by_type

    def test_deterministic_ordering(self) -> None:
        first = compute_changes(_v1(), _changed())
        second = compute_changes(_v1(), _changed())
        assert [
            (c.path, c.entity_name, c.change_type, c.old_value, c.new_value) for c in first
        ] == [(c.path, c.entity_name, c.change_type, c.old_value, c.new_value) for c in second]
