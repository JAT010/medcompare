"""Deterministic semantic fingerprint tests (P19-A).

The fingerprint must reflect semantic content only: provenance timestamps,
per-record extraction details, dataset source/quality metadata and collection
ordering all change while the underlying facts do not.
"""

from __future__ import annotations

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.quality import DataQuality
from hospital_extractor.models.source import SourceRecord
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.snapshots.fingerprint import PROVENANCE_FIELDS, canonical_digest


def _spec(name: str, description: str = "care", source_url: str = "https://x") -> Speciality:
    return Speciality(
        name=name,
        description=description,
        source_url=source_url,
        extracted_at="2026-01-01T00:00:00Z",
    )


def _doctor(name: str, profile_url: str, source_url: str = "https://x") -> Doctor:
    return Doctor(
        name=name,
        profile_url=profile_url,
        source_url=source_url,
        extracted_at="2026-01-01T00:00:00Z",
    )


def _dataset(
    specialities: list[Speciality] | None = None,
    doctors: list[Doctor] | None = None,
    bed_count: int | None = 300,
    name: str = "City Hospital",
    **kwargs: object,
) -> HospitalDataset:
    building: dict[str, object] = {
        "hospital": HospitalOverview(
            name=name,
            bed_count=bed_count,
            source_url="https://x/home",
            extracted_at="2026-01-01T00:00:00Z",
        ),
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


class TestFingerprintStability:
    def test_identical_datasets_have_identical_digests(self) -> None:
        ds1 = _dataset(specialities=[_spec("Cardiology", "heart")])
        ds2 = _dataset(specialities=[_spec("Cardiology", "heart")])
        assert canonical_digest(ds1) == canonical_digest(ds2)

    def test_collection_order_does_not_change_digest(self) -> None:
        cards = [_spec("Cardiology"), _spec("Oncology"), _spec("Ortho")]
        shuffled = [cards[2], cards[0], cards[1]]
        assert canonical_digest(_dataset(specialities=cards)) == canonical_digest(
            _dataset(specialities=shuffled)
        )

    def test_any_semantic_change_alters_digest(self) -> None:
        base = _dataset(specialities=[_spec("Cardiology")], bed_count=300)
        bed_changed = _dataset(specialities=[_spec("Cardiology")], bed_count=310)
        name_changed = _dataset(specialities=[_spec("Cardiology")], name="Other Hospital")
        added = _dataset(specialities=[_spec("Cardiology"), _spec("Oncology")])
        assert canonical_digest(base) != canonical_digest(bed_changed)
        assert canonical_digest(base) != canonical_digest(name_changed)
        assert canonical_digest(base) != canonical_digest(added)


class TestFingerprintProvenanceInsensitivity:
    def test_provenance_fields_are_ignored(self) -> None:
        ds1 = _dataset(specialities=[_spec("Cardiology", source_url="https://a/spec")])
        ds2 = _dataset(specialities=[_spec("Cardiology", source_url="https://b/other")])
        assert canonical_digest(ds1) == canonical_digest(ds2)

        # The whole provenance-field contract is excluded.
        assert "extracted_at" in PROVENANCE_FIELDS
        assert "confidence" in PROVENANCE_FIELDS
        assert "source_url" in PROVENANCE_FIELDS

    def test_dataset_source_metadata_ignored(self) -> None:
        from hospital_extractor.models.metadata import FetchStatus, SourceMetadata, SourceType

        ds1 = _dataset()
        ds2 = _dataset(source=SourceMetadata(
            source_type=SourceType("website"),
            source_id="website:a",
            hospital_ref="a",
            base_url="https://a",
            retrieved_at="2026-06-01T12:00:00Z",
            status=FetchStatus.SUCCESS,
        ))
        assert canonical_digest(ds1) == canonical_digest(ds2)

    def test_quality_ignored(self) -> None:
        ds1 = _dataset()
        ds2 = _dataset(quality=DataQuality(pages_crawled=99, records_extracted=12))
        ds2.sources.append(
            SourceRecord(
                source_id="s",
                category="sources",
                name="feed",
                source_url="https://x/source",
            )
        )
        assert canonical_digest(ds1) == canonical_digest(ds2)

    def test_doctor_identity_uses_profile_url(self) -> None:
        # Rename + re-time a doctor: same semantic content if the profile link
        # is unchanged … except name IS semantic for a doctor record.
        renamed = _dataset(doctors=[_doctor("Dr. X", "https://x/profile/1")])
        _ = renamed
        assert canonical_digest(_dataset(doctors=[_doctor("Dr. Y", "https://x/profile/1")])) != (
            canonical_digest(renamed)
        ), "name is semantic content and must be reflected"