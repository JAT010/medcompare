"""Accreditation extraction precision tests (S9): no over-matching on
``quality`` card classes, generic "Certificate ..." headings, or unrelated
4-digit years; the year is bound to the sentence naming the body."""

from __future__ import annotations

from hospital_extractor.extraction.accreditations import extract_accreditations
from hospital_extractor.extraction.page_analyzer import analyze_page


def _extract(body: str, label: str) -> list:
    html = f"<html><head><title>{label}</title></head><body>{body}</body></html>"
    return extract_accreditations(analyze_page(html, f"https://h.test/{label}"))


def test_nabh_card_extracted_with_year() -> None:
    records = _extract(
        '<div class="accreditation-card"><h3>NABH</h3>'
        "<p>National Accreditation Board for Hospitals, awarded 2017</p></div>",
        "nabh-card",
    )
    names = [r.name for r in records]
    assert "NABH" in names
    assert all(r.year == 2017 for r in records if r.name == "NABH")


def test_iso_revision_year_not_issuing_year() -> None:
    records = _extract(
        '<div class="certification-card"><h3>ISO 9001:2015</h3>'
        "<p>Quality management certification, awarded 2020</p></div>",
        "iso-card",
    )
    assert records
    iso = [r for r in records if r.name == "ISO 9001:2015"]
    assert iso
    assert all(r.year == 2020 for r in iso)


def test_section_body_naming_body_extracted() -> None:
    records = _extract(
        "<h2>Accreditation</h2><p>NABH accredited hospital since 2019.</p>",
        "section-named",
    )
    assert records
    assert all(r.year == 2019 for r in records)


def test_quality_score_card_not_accreditation() -> None:
    records = _extract(
        '<div class="quality-score wow"><h3>Patient Satisfaction</h3>'
        "<p>95% in 2024 survey</p></div>",
        "quality-card",
    )
    assert records == []


def test_certificate_courses_section_not_accreditation() -> None:
    records = _extract(
        "<h2>Certificate Courses</h2>"
        "<p>We run certificate courses for nursing students since 2010.</p>",
        "cert-courses",
    )
    assert records == []


def test_section_without_named_body_not_extracted() -> None:
    records = _extract(
        "<h2>Accreditation</h2>"
        "<p>Established in 1998. The hospital follows a strict quality policy.</p>",
        "no-body",
    )
    assert records == []


def test_unrelated_year_before_body_not_used() -> None:
    records = _extract(
        "<h2>Accreditation</h2><p>Established in 1998. NABH accredited.</p>",
        "earlier-year",
    )
    assert records
    assert all(r.year is None for r in records)


def test_heading_only_named_body_accepted() -> None:
    records = _extract(
        "<h2>NABH Accredited</h2><p>Quality is our priority.</p>",
        "heading-body",
    )
    assert records
    assert all(r.year is None for r in records)