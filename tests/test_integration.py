"""Live integration tests against the Max Smart Super Speciality Hospital,
Saket site (https://www.maxhealthcare.in).

Opt-in: run with ``pytest -m integration``. Tests hit the real site and are
tolerant by design — the assertions check for meaningful, hospital-scoped
extraction, never exact counts (the site changes over time). Any fetch failure
skips rather than fails.
"""

from __future__ import annotations

import httpx
import pytest

from hospital_extractor.extraction.engine import build_dataset

SMART_SAKET = "https://www.maxhealthcare.in/hospital-network/max-smart-super-speciality-hospital-saket"
SUPER_SAKET = "https://www.maxhealthcare.in/hospital-network/max-super-speciality-hospital-saket"
SITE = "https://www.maxhealthcare.in"

UA = "curl/8.0.0"


def _fetch(url: str) -> str:
    response = httpx.get(url, headers={"User-Agent": UA}, timeout=60, follow_redirects=True)
    response.raise_for_status()
    return response.text


@pytest.mark.integration
def test_smart_saket_profile_extracts_hospital() -> None:
    try:
        html = _fetch(SMART_SAKET)
    except httpx.HTTPError as exc:
        pytest.skip(f"network unavailable: {exc}")

    dataset = build_dataset([(SMART_SAKET, html)], target_url=SITE)
    quality = dataset.quality

    assert dataset.hospital is not None
    assert dataset.hospital.name is not None
    assert "Max Smart" in dataset.hospital.name

    assert dataset.infrastructure
    assert dataset.infrastructure[0].total_beds == 650

    assert dataset.contacts
    assert dataset.contacts[0].phone is not None or dataset.contacts[0].address is not None

    assert quality.records_extracted > 0
    assert quality.category_coverage.get("Hospital Overview") == "found"
    assert quality.category_coverage.get("Capacity & Infrastructure") == "found"


@pytest.mark.integration
def test_two_saket_hospitals_are_not_merged() -> None:
    try:
        smart_html = _fetch(SMART_SAKET)
        super_html = _fetch(SUPER_SAKET)
    except httpx.HTTPError as exc:
        pytest.skip(f"network unavailable: {exc}")

    smart = build_dataset([(SMART_SAKET, smart_html)], target_url=SMART_SAKET)
    super_ = build_dataset([(SUPER_SAKET, super_html)], target_url=SUPER_SAKET)

    assert smart.hospital is not None and smart.hospital.name is not None
    assert super_.hospital is not None and super_.hospital.name is not None
    assert smart.hospital.name != super_.hospital.name


@pytest.mark.integration
def test_profile_page_yields_conflict_free_single_anchor() -> None:
    """One profile page must produce no self-conflicts and full provenance."""
    try:
        html = _fetch(SMART_SAKET)
    except httpx.HTTPError as exc:
        pytest.skip(f"network unavailable: {exc}")

    dataset = build_dataset([(SMART_SAKET, html)], target_url=SITE)
    quality = dataset.quality
    assert quality.conflicts == []
    for record in [dataset.hospital, *dataset.infrastructure]:
        assert record is not None
        assert record.page_url == record.source_url
        assert record.confidence is not None
