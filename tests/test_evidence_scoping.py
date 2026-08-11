"""Regression tests for evidence scoping fixes.

Guards against the real-world Max Smart Hospital false positives: nav / footer
chrome, site-root promo text, other-city doctor pages, and network-brand logos
leaking into a hospital's intake row. Each case feeds a minimal site through
the full pipeline and asserts the boolean/intake outcome stays conservative.
"""

from __future__ import annotations

from hospital_extractor.extraction import analyze_page, analyze_pages, build_dataset
from hospital_extractor.extraction.contact import extract_contact
from hospital_extractor.extraction.hospital import extract_hospital_overview
from hospital_extractor.extraction.intake import project_intake
from hospital_extractor.schema.boolean_resolver import apply_detectors, resolve

BASE = "https://example.test"

_SAKET = "Max Smart Super Speciality Hospital, Saket"
_TARGET = f"{BASE}/hospital/max-smart-saket"


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head>"
        f"<title>{title}</title></head><body>{body}</body></html>"
    )


def _saket_page(body: str) -> str:
    return _page(_SAKET, f"<main><h1>{_SAKET}</h1>{body}</main>")


def _project(pages: list[tuple[str, str]], config) -> object:
    dataset = build_dataset(pages, target_url=_TARGET, config=config)
    return project_intake(dataset, pages=analyze_pages(pages), target_url=_TARGET)


def test_nav_and_footer_chrome_are_not_boolean_evidence(config):
    home = _saket_page(
        "<header><nav><a>Da Vinci Robotic Surgery</a>"
        "<a>Knee Replacement</a><a>Heart Valve Repair</a></nav></header>"
        f"<p>{_SAKET} is a multi-speciality hospital.</p>"
        "<footer><a>Bypass Surgery</a></footer>"
    )
    intake = _project([(_TARGET, home)], config)
    for key in (
        "da_vinci_robotic_surgery",
        "total_knee_replacement",
        "heart_valve_repair",
        "bypass_surgery_cabg",
    ):
        assert intake.booleans[key] == "unknown", key
        assert intake.evidence[key] == [], key


def test_site_wide_carousel_widget_is_not_boolean_evidence(config):
    home = _saket_page(
        f"<p>{_SAKET} is a 650 bedded hospital.</p>"
        '<div class="meet-doctor-section-carousel">'
        "<p>Dr. X performs angioplasty and pacemaker implantation.</p></div>"
    )
    intake = _project([(_TARGET, home)], config)
    assert intake.booleans["angioplasty_stenting"] == "unknown"
    assert intake.booleans["pacemaker_icd_implantation"] == "unknown"


def test_main_content_is_boolean_evidence(config):
    home = _saket_page(
        f"<p>{_SAKET} is equipped with a 3.0 Tesla Digital Broadband MRI and "
        "a Da Vinci Xi robotic system.</p>"
    )
    intake = _project([(_TARGET, home)], config)
    assert intake.booleans["mri_3_tesla_or_higher"] == "yes"
    assert intake.booleans["da_vinci_robotic_surgery"] == "yes"


def test_other_city_doctor_page_is_excluded(config):
    target = _saket_page(f"<p>{_SAKET} offers cardiac care.</p>")
    doctor = _page(
        "Best Heart Specialists in Lucknow",
        "<main><h1>Heart Specialists in Lucknow</h1>"
        "<p>We offer heart valve repair and bypass surgery in Lucknow.</p></main>",
    )
    pages = [(_TARGET, target), (f"{BASE}/doctor/heart-specialists-lucknow", doctor)]
    intake = _project(pages, config)
    assert intake.booleans["heart_valve_repair"] == "unknown"
    assert intake.booleans["bypass_surgery_cabg"] == "unknown"
    assert intake.evidence["heart_valve_repair"] == []
    assert intake.evidence["bypass_surgery_cabg"] == []


def test_site_root_promo_text_is_excluded(config):
    root = _page(
        "Best Hospitals in Delhi NCR",
        "<main><h1>Max Healthcare Hospitals</h1>"
        "<p>Best Knee Replacement Hospital in India, treated by experts at "
        f"{_SAKET} and other Max hospitals.</p></main>",
    )
    target = _saket_page(f"<p>{_SAKET} is a 650 bedded hospital.</p>")
    intake = _project([(f"{BASE}/", root), (_TARGET, target)], config)
    assert intake.booleans["total_knee_replacement"] == "unknown"
    assert intake.evidence["total_knee_replacement"] == []


def _network_logo_html(page_level: str, hospital_type: str = "LocalBusiness") -> str:
    return _page(
        _SAKET,
        '<script type="application/ld+json">{"@context":"https://schema.org",'
        '"@type":"MedicalOrganization","name":"Max Healthcare",'
        '"url":"https://example.test/","logo":"https://example.test/img/brandLogo.svg"}</script>'
        f'<script type="application/ld+json">{{"@context":"https://schema.org",'
        f'"@type":"{hospital_type}","name":"{_SAKET}","url":"{_TARGET}","logo":"{page_level}"'
        f'}}</script>',
    )


def test_network_brand_logo_is_rejected():
    page = analyze_page(_network_logo_html(""), _TARGET)
    overview = extract_hospital_overview(page, website=_TARGET)
    assert overview is not None
    assert overview.logo_url is None


def test_page_level_logo_is_kept():
    page = analyze_page(_network_logo_html(f"{BASE}/hospital/logo.png", "Hospital"), _TARGET)
    overview = extract_hospital_overview(page, website=_TARGET)
    assert overview is not None
    assert overview.logo_url == f"{BASE}/hospital/logo.png"


def test_locality_falls_back_to_last_address_part_when_city_elsewhere():
    html = _page(
        _SAKET,
        '<script type="application/ld+json">{"@context":"https://schema.org",'
        f'"@type":"Hospital","name":"{_SAKET}","url":"{_TARGET}",'
        '"address":{"@type":"PostalAddress",'
        '"streetAddress":"12, Press Enclave Marg, Saket Institutional Area, Saket",'
        '"addressLocality":"New Delhi","postalCode":"110017"}}</script>',
    )
    page = analyze_page(html, _TARGET)
    contact = extract_contact(page)
    assert contact is not None
    assert contact.locality == "Saket"


def test_locality_prefers_inline_city_part_when_present():
    html = _page(
        "City Hospital",
        '<script type="application/ld+json">{"@context":"https://schema.org",'
        '"@type":"Hospital","name":"City Hospital","url":"https://example.test/",'
        '"address":{"@type":"PostalAddress",'
        '"streetAddress":"12 MG Road, Koregaon Park",'
        '"addressLocality":"Pune","postalCode":"411001"}}</script>',
    )
    page = analyze_page(html, f"{BASE}/")
    contact = extract_contact(page)
    assert contact is not None
    assert contact.locality == "Koregaon Park"


def test_decimal_tesla_mri_matches_detector():
    items = apply_detectors("3.0 Tesla Digital Broadband MRI", f"{BASE}/page")
    mri = [item for item in items if item.field_key == "mri_3_tesla_or_higher"]
    assert mri and mri[0].decision == "yes"
    intake, _ = resolve({}, {"mri_3_tesla_or_higher": mri})
    assert intake.booleans["mri_3_tesla_or_higher"] == "yes"


def test_doctor_listing_filter_widget_does_not_scope_page(config):
    target = _saket_page(f"<p>{_SAKET} is a 650 bedded hospital.</p>")
    doctor_listing = _page(
        "Best Heart Specialists in Delhi",
        "<main><form class=\"sidebar-filter\"><label>Hospital</label>"
        f"<ul><li>{_SAKET}</li><li>Max Super Speciality Hospital, Patparganj</li></ul></form>"
        '<div class="doctor-card"><h3>Dr. A</h3>'
        "<p>Bypass Surgery, Pacemaker Implantation</p></div></main>",
    )
    pages = [(_TARGET, target), (f"{BASE}/doctors/delhi/heart-specialists", doctor_listing)]
    intake = _project(pages, config)
    assert intake.hospital_name == _SAKET
    assert intake.booleans["bypass_surgery_cabg"] == "unknown"
    assert intake.booleans["pacemaker_icd_implantation"] == "unknown"
    assert intake.evidence["bypass_surgery_cabg"] == []
    assert intake.evidence["pacemaker_icd_implantation"] == []


def test_site_root_network_overview_does_not_pollute_hospital_name(config):
    root = _page(
        "Max Healthcare - Network Hospitals",
        '<script type="application/ld+json">{"@context":"https://schema.org",'
        '"@type":"MedicalOrganization","name":"Max Healthcare",'
        '"url":"https://example.test/","logo":"https://example.test/img/brandLogo.svg"}</script>'
        "<main><h1>Max Healthcare Hospitals</h1>"
        "<p>Max Healthcare runs 20 hospitals across India with 5000 beds.</p></main>",
    )
    target = _page(
        _SAKET,
        '<script type="application/ld+json">{"@context":"https://schema.org",'
        f'"@type":"LocalBusiness","name":"{_SAKET}","url":"{_TARGET}",'
        '"address":{"streetAddress":"12, Press Enclave Marg",'
        '"addressLocality":"New Delhi"}}</script>'
        f"<main><h1>{_SAKET}</h1><p>{_SAKET} is a 650 bedded hospital.</p></main>",
    )
    intake = _project([(f"{BASE}/", root), (_TARGET, target)], config)
    assert intake.hospital_name == _SAKET
    assert intake.logo_image_url is None
