"""Field-aware, collection-aware change detection tests (P19-A).

Covers ADDED / REMOVED / MODIFIED classification, reorder insensitivity,
doctor identity via ``profile_url``, first-snapshot semantics and the
empty-history ``initial_snapshot`` behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hospital_extractor.models.change import ChangeSetStatus, ChangeType
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.snapshot import DatasetSnapshot
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.snapshots.change_detection import build_change_set, compute_changes
from hospital_extractor.snapshots.fingerprint import canonical_digest


def _spec(name: str, description: str = "care") -> Speciality:
    return Speciality(name=name, description=description, source_url=f"https://x/{name}")


def _doctor(name: str, profile_url: str) -> Doctor:
    return Doctor(
        name=name,
        profile_url=profile_url,
        source_url=profile_url,
    )


def _dataset(
    specialities: list[Speciality] | None = None,
    doctors: list[Doctor] | None = None,
    bed_count: int | None = 300,
    **kwargs: object,
) -> HospitalDataset:
    building: dict[str, object] = {
        "hospital": HospitalOverview(name="City", bed_count=bed_count, source_url="https://x"),
        "contacts": [],
        "infrastructure": [],
        "facilities": [],
        "departments": [],
        "specialities": specialities or [],
        "doctors": doctors or [],
        "treatments": [],
        "procedures": [],
        "technologies": [],
        "accreditations": [],
    }
    building.update(kwargs)
    return HospitalDataset(**building)  # type: ignore[arg-type]


def _snapshot(version: int, dataset: HospitalDataset) -> DatasetSnapshot:
    return DatasetSnapshot(
        snapshot_id=f"s_{version:012x}",
        hospital_id="h_000000000000",
        source_type="website",
        hospital_ref="city.test",
        hospital_name="City",
        captured_at=datetime(2026, 1, version, tzinfo=UTC),
        content_hash=canonical_digest(dataset),
        dataset=dataset,
    )


class TestComputeChanges:
    def test_nothing_changed_yields_empty(self) -> None:
        ds = _dataset(specialities=[_spec("Cardiology")])
        assert compute_changes(ds, ds) == []

    def test_added_and_removed_items(self) -> None:
        before = _dataset(specialities=[_spec("Cardiology"), _spec("Oncology")])
        after = _dataset(specialities=[_spec("Cardiology"), _spec("Ortho")])
        changes = compute_changes(before, after)
        kinds = sorted(c.change_type for c in changes)
        assert ChangeType.REMOVED in kinds
        assert ChangeType.ADDED in kinds
        removed = next(c for c in changes if c.change_type is ChangeType.REMOVED)
        assert removed.entity_name == "Oncology"
        assert removed.category == "specialities"
        added = next(c for c in changes if c.change_type is ChangeType.ADDED)
        assert added.entity_name == "Ortho"

    def test_modified_field(self) -> None:
        before = _dataset(bed_count=300)
        after = _dataset(bed_count=410)
        changes = compute_changes(before, after)
        assert len(changes) == 1
        assert changes[0].change_type is ChangeType.MODIFIED
        assert changes[0].path == "hospital.bed_count"
        assert changes[0].old_value == 300
        assert changes[0].new_value == 410

    def test_reorder_is_not_removed_plus_added(self) -> None:
        cards = [_spec("Cardiology"), _spec("Oncology"), _spec("Ortho")]
        before = _dataset(specialities=cards)
        after = _dataset(specialities=[cards[2], cards[0], cards[1]])
        assert compute_changes(before, after) == []

    def test_doctor_matched_by_profile_url(self) -> None:
        before = _dataset(
            doctors=[_doctor("Dr. Anil", "https://x/doctors/anil")],
        )
        after = _dataset(
            doctors=[_doctor("Dr. Anil Kumar", "https://x/doctors/anil")],
        )
        changes = compute_changes(before, after)
        # Same profile_url → MODIFIED (name), never a remove+add.
        assert len(changes) == 1
        assert changes[0].change_type is ChangeType.MODIFIED
        assert changes[0].path == "doctors.name"
        assert changes[0].entity_name == "Dr. Anil Kumar"

    def test_none_to_value_is_modified(self) -> None:
        before = _dataset(specialities=[_spec("Cardiology", description=None)])
        after = _dataset(specialities=[_spec("Cardiology", description="heart & vessels")])
        changes = compute_changes(before, after)
        assert len(changes) == 1
        assert changes[0].change_type is ChangeType.MODIFIED
        assert changes[0].old_value is None
        assert changes[0].new_value == "heart & vessels"

    def test_first_snapshot_initial_with_no_changes(self) -> None:
        current = _snapshot(2, _dataset(specialities=[_spec("Cardiology")]))
        change_set = build_change_set(None, current)
        assert change_set.status is ChangeSetStatus.INITIAL_SNAPSHOT
        assert change_set.changes == []
        assert change_set.previous_snapshot_id is None

    def test_identical_hash_is_no_change(self) -> None:
        dataset = _dataset()
        previous = _snapshot(1, dataset)
        current = _snapshot(2, dataset)
        change_set = build_change_set(previous, current)
        assert change_set.status is ChangeSetStatus.NO_CHANGE
        assert change_set.previous_snapshot_id == previous.snapshot_id

    def test_real_change_is_changes_detected(self) -> None:
        previous = _snapshot(1, _dataset(bed_count=200))
        current = _snapshot(2, _dataset(bed_count=300))
        change_set = build_change_set(previous, current)
        assert change_set.status is ChangeSetStatus.CHANGES_DETECTED
        assert change_set.modified == 1
        assert change_set.added == 0
        assert change_set.removed == 0
        # Provenance is stamped from the current snapshot.
        assert change_set.changes[0].source_type == "website"
        assert change_set.changes[0].captured_at == current.captured_at

    def test_changes_are_deterministic(self) -> None:
        before = _dataset(specialities=[_spec("Cardiology"), _spec("Oncology")])
        after = _dataset(specialities=[_spec("Ortho")])
        expected = compute_changes(before, after)
        for _ in range(3):
            assert compute_changes(before, after) == expected