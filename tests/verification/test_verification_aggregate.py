"""Comparison semantics tests: MATCH / MISMATCH / INSUFFICIENT_EVIDENCE."""

from __future__ import annotations

from hospital_extractor.verification.compare import build_verification

_V1 = {"hospital": {"name": "Mock General Hospital", "bed_count": 100}}


class _Item:
    def __init__(self, criterion_key: str, value: str) -> None:
        self.criterion_key = criterion_key
        self.value = value


def _changes(*paths: str) -> list[tuple[str, str, str, str, object, object]]:
    return [(path, "modified", "", path.split(".")[-1], 100, 120) for path in paths]


INFRA_MATCH = [_Item("infrastructure", "Total beds: 120; ICU beds: 12")]


def test_all_verified_changes_match() -> None:
    result = build_verification(
        payload=_V1,
        changes=_changes("hospital.bed_count"),
        website_evidence=INFRA_MATCH,
        website_hospital_name=None,
        website_target_url=None,
        hospital_url=None,
    )
    assert result.status == "match"
    checks = result.evidence_json()["checks"]
    assert checks[0]["status"] == "match"


def test_partial_evidence_is_not_verified() -> None:
    result = build_verification(
        payload=_V1,
        changes=_changes("hospital.bed_count", "procedures"),
        website_evidence=INFRA_MATCH,
        website_hospital_name=None,
        website_target_url=None,
        hospital_url=None,
    )
    assert result.status == "insufficient_evidence"


def test_no_website_evidence_at_all_is_insufficient() -> None:
    result = build_verification(
        payload=_V1,
        changes=_changes("hospital.bed_count", "infrastructure.total_beds"),
        website_evidence=[],
        website_hospital_name=None,
        website_target_url=None,
        hospital_url=None,
    )
    assert result.status == "insufficient_evidence"


def test_mismatch_wins_over_partial_evidence() -> None:
    result = build_verification(
        payload=_V1,
        changes=_changes("hospital.bed_count", "procedures"),
        website_evidence=[_Item("infrastructure", "Total beds: 100")],
        website_hospital_name=None,
        website_target_url=None,
        hospital_url=None,
    )
    assert result.status == "mismatch"


def test_conflicting_bed_counts_are_insufficient_not_match() -> None:
    result = build_verification(
        payload=_V1,
        changes=_changes("hospital.bed_count"),
        website_evidence=[
            _Item("infrastructure", "Total beds: 120"),
            _Item("infrastructure", "Total beds: 100"),
        ],
        website_hospital_name=None,
        website_target_url=None,
        hospital_url=None,
    )
    assert result.status == "insufficient_evidence"
    checks = result.evidence_json()["checks"]
    assert checks[0]["status"] == "insufficient_evidence"
    assert "conflicting" in checks[0]["detail"].lower()


def test_conflicting_location_evidence_is_insufficient_not_mismatch() -> None:
    result = build_verification(
        payload=_V1,
        changes=[("contact.pin_code", "modified", "", "pin_code", "000000", "000000")],
        website_evidence=[
            _Item("location", "1 Mock Way, Testville, TS 000001"),
            _Item("location", "2 Other Road, Testville, TS 999999"),
        ],
        website_hospital_name=None,
        website_target_url=None,
        hospital_url=None,
    )
    assert result.status == "insufficient_evidence"
    checks = result.evidence_json()["checks"]
    assert "conflicting" in checks[0]["detail"].lower()
