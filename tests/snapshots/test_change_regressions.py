"""Regression tests for change-detection edge cases."""

from __future__ import annotations

from hospital_extractor.snapshots.change_detection import compute_changes
from hospital_extractor.snapshots.fingerprint import canonical_digest


def test_duplicate_named_members_do_not_silently_drop_entries() -> None:
    previous = {
        "departments": [
            {"name": "Cardiology", "beds": 10},
            {"name": "Cardiology", "beds": 12},
        ],
    }
    current = {
        "departments": [
            {"name": "Cardiology", "beds": 11},
            {"name": "Cardiology", "beds": 12},
        ],
    }
    changes = compute_changes(previous, current)
    modified = [c for c in changes if c.change_type == "modified"]
    assert modified
    # Index-based fallback for duplicate identities: department[0] beds 10 -> 11
    assert any("beds" in change.path for change in modified)
    added = [c for c in changes if c.change_type == "added"]
    removed = [c for c in changes if c.change_type == "removed"]
    assert not added and not removed


def test_missing_versus_null_are_distinct() -> None:
    previous = {"hospital": {"bed_count": 100, "established_year": None}}
    current = {"hospital": {"bed_count": 100}}
    changes = compute_changes(previous, current)
    removed = [c for c in changes if c.change_type == "removed"]
    assert any(change.path == "hospital.established_year" for change in removed)

    reverse = compute_changes(current, previous)
    added = [c for c in reverse if c.change_type == "added"]
    assert any(change.path == "hospital.established_year" for change in added)


def test_false_is_not_zero_and_zero_is_not_one() -> None:
    assert compute_changes({"flag": False}, {"flag": 0})
    assert not compute_changes({"flag": False}, {"flag": False})
    assert compute_changes({"n": 0}, {"n": 1})
    assert not compute_changes({"n": 1}, {"n": 1})
    assert compute_changes({"s": "1"}, {"s": 1})


def test_explicit_null_versus_false() -> None:
    assert compute_changes({"v": None}, {"v": False})
    assert not compute_changes({"v": None}, {"v": None})
    assert not compute_changes({"v": False}, {"v": False})


def test_missing_versus_false() -> None:
    changes = compute_changes({"cfg": {"enabled": True}}, {})
    assert [c.change_type for c in changes] == ["removed"]


def test_nullable_nested_object_add_and_remove() -> None:
    previous = {"hospital": {"details": None}}
    current = {"hospital": {"details": {"founded": 1990}}}
    changes = compute_changes(previous, current)
    assert [c.change_type for c in changes] == ["modified"]
    assert changes[0].field == "details"


def test_provenance_wrapper_does_not_trigger_changes() -> None:
    from hospital_extractor.snapshots.fingerprint import strip_provenance

    previous = {
        "hospital": {"bed_count": 100},
        "source": {"provider": "P", "variant": "default", "change_token": "a"},
    }
    current = {
        "hospital": {"bed_count": 100},
        "source": {"provider": "P", "variant": "changed", "change_token": "b"},
    }
    assert compute_changes(strip_provenance(previous), strip_provenance(current)) == []


def test_fingerprint_ignores_provenance_wrapper() -> None:
    from hospital_extractor.snapshots.fingerprint import strip_provenance

    first = {"hospital": {"bed_count": 100}, "source": {"variant": "default"}}
    second = {"hospital": {"bed_count": 100}, "source": {"variant": "changed", "x": 1}}
    assert canonical_digest(strip_provenance(first)) == canonical_digest(strip_provenance(second))


def test_false_detection_in_indexed_list() -> None:
    previous = {"flags": [False, False]}
    current = {"flags": [False, True]}
    assert compute_changes(previous, current)
