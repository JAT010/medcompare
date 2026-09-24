"""End-to-end extraction tests against the standard six-page fixture."""

from __future__ import annotations

from hospital_extractor.extraction import build_dataset


def test_full_pipeline_populates_categories(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)

    assert dataset.hospital is not None
    assert dataset.hospital.name == "City Hospital"
    assert dataset.hospital.website == "https://example.test/"

    assert len(dataset.contacts) == 1
    contact = dataset.contacts[0]
    assert contact.city == "Pune"
    assert contact.postal_code == "411001"

    assert len(dataset.infrastructure) == 1
    infra = dataset.infrastructure[0]
    assert infra.total_beds == 650
    assert infra.critical_care_beds == 120
    assert infra.operation_theatres == 5

    assert len(dataset.departments) == 3
    assert len(dataset.treatments) == 2

    facility_names = {f.name for f in dataset.facilities}
    assert "24x7 Pharmacy" in facility_names
    assert "Blood Bank" in facility_names


def test_faq_routes_to_facilities(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)

    facility_names = {f.name for f in dataset.facilities}
    assert any("diagnostic" in name.lower() or "lab" in name.lower() for name in facility_names)


def test_duplicate_doctor_removed(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)

    assert dataset.quality.duplicates_removed >= 1
    profiles = [d.profile_url for d in dataset.doctors]
    assert len(profiles) == len(set(profiles))


def test_extraction_source_strength(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)

    assert dataset.hospital.source_strength == "high"
    assert dataset.hospital.extraction_method == "jsonld"


def test_jsonld_sweep_extracts_doctors_from_hospital_page():
    """Physician JSON-LD on a hospital-classified page should yield doctors."""
    hospital_page = (
        '<html><head>'
        '<title>City Hospital - Our Doctors</title>'
        '<script type="application/ld+json">'
        '{"@type":"Hospital","name":"City Hospital","url":"https://example.test/"}'
        '</script>'
        '<script type="application/ld+json">'
        '{"@type":"Physician","name":"Dr. Kumar","medicalSpecialty":"Cardiology",'
        '"worksFor":{"@type":"Hospital","name":"City Hospital"}}'
        '</script>'
        '<script type="application/ld+json">'
        '{"@type":"Physician","name":"Dr. Sharma","medicalSpecialty":"Neurology",'
        '"worksFor":{"@type":"Hospital","name":"City Hospital"}}'
        '</script>'
        '</head><body>'
        '<h1>City Hospital - Our Doctors</h1>'
        '<p>Meet our team of expert doctors.</p>'
        '</body></html>'
    )
    pages = [
        ("https://example.test/", hospital_page),
    ]
    dataset = build_dataset(pages, target_url="https://example.test/")

    assert dataset.hospital is not None
    assert dataset.hospital.name == "City Hospital"
    assert len(dataset.doctors) == 2
    doctor_names = {d.name for d in dataset.doctors}
    assert "Dr. Kumar" in doctor_names
    assert "Dr. Sharma" in doctor_names
    for doctor in dataset.doctors:
        assert doctor.hospital_association in ("confirmed", "probable")


def test_jsonld_sweep_skips_when_already_routed():
    """When a page IS classified as 'doctor', the sweep should not duplicate."""
    doctor_page = (
        '<html><head>'
        '<title>Dr. Kumar - Cardiologist</title>'
        '</head><body>'
        '<h1>Dr. Kumar</h1>'
        '<p>Cardiology specialist at City Hospital.</p>'
        '<div class="doctor-card">'
        '<h3>Dr. Kumar</h3><p>Cardiologist</p>'
        '<a href="/doctor/kumar">Profile</a>'
        '</div>'
        '</body></html>'
    )
    pages = [
        ("https://example.test/doctor/kumar", doctor_page),
    ]
    dataset = build_dataset(pages, target_url="https://example.test/")

    assert len(dataset.doctors) >= 1


def test_only_exact_target_url_feeds_hospital_identity():
    """Only the exact target URL page may become the hospital anchor.

    Same-domain procedure and doctor pages must NOT inject their H1 titles
    as hospital-name candidates merely because they share the domain.
    Regression guard for the _same_domain fallback leak in anchor detection.
    """
    anchor = (
        '<html><head>'
        '<title>Apollo Hospitals</title>'
        '<script type="application/ld+json">'
        '{"@type":"Hospital","name":"Apollo Hospitals",'
        '"url":"https://www.apollohospitals.com/"}'
        '</script>'
        '</head><body>'
        '<h1>Apollo Hospitals</h1>'
        '<p>Leading healthcare provider across India.</p>'
        '</body></html>'
    )
    procedure = (
        '<html><head>'
        '<title>Liver Transplant in India</title>'
        '</head><body>'
        '<h1>Liver Transplant in India: Types, Procedure, Cost</h1>'
        '<p>A liver transplant is a surgical procedure that involves '
        'removing a diseased or damaged liver.</p>'
        '</body></html>'
    )
    doctor = (
        '<html><head>'
        '<title>Best Cardiologist in Delhi</title>'
        '</head><body>'
        '<h1>Best Cardiologist in Delhi</h1>'
        '<p>Expert heart care at Apollo Hospitals.</p>'
        '<div class="doctor-card">'
        '<h3>Dr. Rajesh Kumar</h3><p>Cardiology</p>'
        '</div>'
        '</body></html>'
    )
    pages = [
        ("https://www.apollohospitals.com/", anchor),
        ("https://www.apollohospitals.com/procedures/liver-transplant", procedure),
        ("https://www.apollohospitals.com/online-doctors/cardiologist-delhi", doctor),
    ]
    dataset = build_dataset(pages, target_url="https://www.apollohospitals.com/")

    assert dataset.hospital is not None
    assert dataset.hospital.name == "Apollo Hospitals"
    assert "Liver Transplant" not in dataset.hospital.name
    assert "Cardiologist" not in dataset.hospital.name

    quality_conflicts = dataset.quality.conflicts
    hospital_name_conflicts = [
        c for c in quality_conflicts
        if c.category == "hospital" and c.field == "name"
    ]
    assert hospital_name_conflicts == []


def test_navigation_labels_not_extracted_as_specialities():
    """Generic navigation labels should not become specialities."""
    page_html = (
        '<html><head><title>Our Hospital</title></head><body>'
        '<nav>'
        '<a href="/speciality/cardiology">Our Medical Experts</a>'
        '<a href="/speciality/neurology">For Patients</a>'
        '<a href="/speciality/oncology">Centers of Excellence</a>'
        '</nav>'
        '<h1>City Hospital</h1>'
        '<p>Multi-speciality hospital.</p>'
        '</body></html>'
    )
    pages = [("https://example.test/", page_html)]
    dataset = build_dataset(pages, target_url="https://example.test/")

    spec_names = {s.name for s in dataset.specialities}
    assert "Our Medical Experts" not in spec_names
    assert "For Patients" not in spec_names
    assert "Centers of Excellence" not in spec_names


def test_informational_question_headings_not_specialities():
    """'What is Ventricular Tachycardia?' is condition info, not a speciality."""
    page_html = (
        '<html><head><title>Ventricular Tachycardia</title></head><body>'
        '<h1>What is Ventricular Tachycardia?</h1>'
        '<p>Ventricular tachycardia is a fast heart rhythm.</p>'
        '</body></html>'
    )
    pages = [("https://example.test/conditions/vt", page_html)]
    dataset = build_dataset(pages, target_url="https://example.test/")
    assert len(dataset.specialities) == 0


def test_informational_types_heading_not_speciality():
    """'Types of POTS' is condition info, not a speciality."""
    page_html = (
        '<html><head><title>Types of POTS</title></head><body>'
        '<h1>Types of POTS</h1>'
        '<p>POTS has several subtypes.</p>'
        '</body></html>'
    )
    pages = [("https://example.test/conditions/pots", page_html)]
    dataset = build_dataset(pages, target_url="https://example.test/")
    assert len(dataset.specialities) == 0


def test_informational_symptoms_heading_not_speciality():
    """'Symptoms of Diabetes' is condition info, not a speciality."""
    page_html = (
        '<html><head><title>Diabetes Symptoms</title></head><body>'
        '<h1>Symptoms of Diabetes</h1>'
        '<p>Common symptoms include increased thirst.</p>'
        '</body></html>'
    )
    pages = [("https://example.test/conditions/diabetes", page_html)]
    dataset = build_dataset(pages, target_url="https://example.test/")
    assert len(dataset.specialities) == 0


def test_informational_treatment_heading_not_speciality():
    """'Treatment of Asthma' is condition info, not a speciality."""
    page_html = (
        '<html><head><title>Asthma Treatment</title></head><body>'
        '<h1>Treatment of Asthma</h1>'
        '<p>Inhalers are the primary treatment.</p>'
        '</body></html>'
    )
    pages = [("https://example.test/conditions/asthma", page_html)]
    dataset = build_dataset(pages, target_url="https://example.test/")
    assert len(dataset.specialities) == 0


def test_valid_speciality_from_heading():
    """'Cardiology' is a legitimate speciality name."""
    page_html = (
        '<html><head><title>Cardiology</title></head><body>'
        '<h1>Cardiology</h1>'
        '<p>We offer comprehensive cardiac care.</p>'
        '</body></html>'
    )
    pages = [("https://example.test/speciality/cardiology", page_html)]
    dataset = build_dataset(pages, target_url="https://example.test/")
    assert len(dataset.specialities) == 1
    assert dataset.specialities[0].name == "Cardiology"


def test_valid_speciality_orthopaedics():
    """'Orthopaedics' is a legitimate speciality name."""
    page_html = (
        '<html><head><title>Orthopaedics</title></head><body>'
        '<h1>Orthopaedics</h1>'
        '<p>Expert bone and joint care.</p>'
        '</body></html>'
    )
    pages = [("https://example.test/speciality/orthopaedics", page_html)]
    dataset = build_dataset(pages, target_url="https://example.test/")
    assert len(dataset.specialities) == 1
    assert dataset.specialities[0].name == "Orthopaedics"


def test_navigation_false_positives_still_filtered():
    """Generic navigation labels remain filtered as specialities."""
    page_html = (
        '<html><head><title>Our Hospital</title></head><body>'
        '<nav>'
        '<a href="/speciality/cardiology">Our Medical Experts</a>'
        '<a href="/speciality/neurology">For Patients</a>'
        '<a href="/speciality/oncology">Centers of Excellence</a>'
        '</nav>'
        '<h1>City Hospital</h1>'
        '<p>Multi-speciality hospital.</p>'
        '</body></html>'
    )
    pages = [("https://example.test/", page_html)]
    dataset = build_dataset(pages, target_url="https://example.test/")
    spec_names = {s.name for s in dataset.specialities}
    assert "Our Medical Experts" not in spec_names
    assert "For Patients" not in spec_names
    assert "Centers of Excellence" not in spec_names


def test_condition_detail_page_not_speciality():
    """Condition detail page under /conditions-treatments/ has no specialities."""
    condition_page = (
        '<html><head>'
        '<title>Ventricular Tachycardia Treatment in Delhi</title>'
        '</head><body>'
        '<h1>Ventricular Tachycardia Treatment in Delhi, India</h1>'
        '<p>Ventricular tachycardia is a rapid heart rhythm.</p>'
        '<h2>Types of Ventricular Tachycardia</h2>'
        '<p>There are several types.</p>'
        '</body></html>'
    )
    cond_url = "https://example.test/our-specialities/cardiology/"
    cond_url += "conditions-treatments/vt"
    pages = [(cond_url, condition_page)]
    dataset = build_dataset(pages, target_url="https://example.test/")
    assert len(dataset.specialities) == 0


# ---------------------------------------------------------------------------
# Hospital-identity fallback regression guards (Medanta-style scenario)
#
# The _extract_identity fallback in engine.py extracts hospital identity from
# any target-scoped page when no hospital name is known yet.  These tests
# verify that non-hospital H1 values cannot leak through this path and that
# valid JSON-LD MedicalOrganization blocks are correctly promoted.
# ---------------------------------------------------------------------------


def test_doctor_h1_does_not_become_hospital_name():
    """A doctor page's H1 'Best Cardiologist in Noida' must not become the
    hospital name when the _extract_identity fallback fires.

    In the Medanta-style scenario no anchor (homepage) was crawled, so the
    fallback may call ``extract_hospital_overview`` on a same-domain doctor
    page.  The H1 is not a hospital name and must be rejected.

    Regression guard for identity-contamination via the non-anchor fallback.
    """
    doctor = (
        '<html><head>'
        '<title>Best Cardiologist in Noida | Medanta</title>'
        '</head><body>'
        '<h1>Best Cardiologist in Noida</h1>'
        '<p>Expert heart care at Medanta.</p>'
        '<div class="doctor-card">'
        '<h3>Dr. Amit Chaudhary</h3><p>Cardiology</p>'
        '</div>'
        '</body></html>'
    )
    pages = [
        ("https://www.medanta.org/doctor-listing/cardiac-doctors-in-noida", doctor),
    ]
    dataset = build_dataset(pages, target_url="https://www.medanta.org/")

    if dataset.hospital is not None:
        assert "Cardiologist" not in dataset.hospital.name
        assert "Best" not in dataset.hospital.name


def test_procedure_h1_does_not_become_hospital_name():
    """A procedure page's H1 'Liver Transplant in India' must not become the
    hospital name when the _extract_identity fallback fires.

    Same structural guard as the doctor-page test but targeting the
    procedure extraction path.  The H1 describes a medical procedure, not
    a hospital, and must never leak into the identity record.

    Regression guard for identity-contamination via the non-anchor fallback.
    """
    procedure = (
        '<html><head>'
        '<title>Liver Transplant in India | Medanta</title>'
        '</head><body>'
        '<h1>Liver Transplant in India: Types, Procedure, Cost</h1>'
        '<p>A liver transplant is a surgical procedure that involves '
        'removing a diseased or damaged liver.</p>'
        '</body></html>'
    )
    pages = [
        ("https://www.medanta.org/procedures/liver-transplant", procedure),
    ]
    dataset = build_dataset(pages, target_url="https://www.medanta.org/")

    if dataset.hospital is not None:
        assert "Liver Transplant" not in dataset.hospital.name
        assert "Transplant" not in dataset.hospital.name


def test_medical_organization_jsonld_establishes_identity():
    """A page whose only JSON-LD is a MedicalOrganization block with
    ``name: "Medanta"`` should correctly establish the hospital identity
    through the _extract_identity fallback.

    This is the canonical Medanta scenario: no homepage was crawled, but
    every doctor-listing page carries a network-level MedicalOrganization
    JSON-LD.  The fallback must extract the block's name rather than the
    page's H1.

    Regression guard: confirms the fallback path is not *entirely*
    broken — it must still succeed when valid structured data is present.
    """
    doctor = (
        '<html><head>'
        '<title>Best Cardiologist in Noida | Medanta</title>'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org",'
        '"@type":"MedicalOrganization",'
        '"name":"Medanta",'
        '"url":"https://www.medanta.org/"}'
        '</script>'
        '</head><body>'
        '<h1>Best Cardiologist in Noida</h1>'
        '<p>Expert heart care at Medanta.</p>'
        '</body></html>'
    )
    pages = [
        ("https://www.medanta.org/doctor-listing/cardiac-doctors-in-noida", doctor),
    ]
    dataset = build_dataset(pages, target_url="https://www.medanta.org/")

    assert dataset.hospital is not None
    assert dataset.hospital.name == "Medanta"


def test_hospital_identity_not_overwritten_by_later_pages():
    """Once the hospital identity has been established (via JSON-LD on the
    first page), a subsequent page whose only identity signal is a
    non-hospital H1 must not overwrite it.

    Regression guard: the ``not existing`` guard in the fallback must
    prevent later non-anchor pages from clobbering a valid name.
    """
    doctor_with_jsonld = (
        '<html><head>'
        '<title>Best Cardiologist in Noida | Medanta</title>'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org",'
        '"@type":"MedicalOrganization",'
        '"name":"Medanta",'
        '"url":"https://www.medanta.org/"}'
        '</script>'
        '</head><body>'
        '<h1>Best Cardiologist in Noida</h1>'
        '<p>Expert heart care at Medanta.</p>'
        '</body></html>'
    )
    doctor_without_jsonld = (
        '<html><head>'
        '<title>Best Neurologist in Gurugram | Medanta</title>'
        '</head><body>'
        '<h1>Best Neurologist in Gurugram</h1>'
        '<p>Expert neurology care at Medanta.</p>'
        '</body></html>'
    )
    pages = [
        ("https://www.medanta.org/doctor-listing/cardiac-doctors-in-noida",
         doctor_with_jsonld),
        ("https://www.medanta.org/doctor-listing/neurologists-in-gurugram",
         doctor_without_jsonld),
    ]
    dataset = build_dataset(pages, target_url="https://www.medanta.org/")

    assert dataset.hospital is not None
    assert dataset.hospital.name == "Medanta"
    assert "Neurologist" not in dataset.hospital.name


def test_hospital_identity_conflicts_zero_for_medanta_style():
    """Multiple doctor listing pages with the same MedicalOrganization
    JSON-LD ``name: "Medanta"`` must produce zero hospital-name conflicts.

    Regression guard: the fallback must not create *multiple* hospital
    records that then produce merge conflicts.  The ``not existing`` check
    in the fallback ensures only the first target-scoped page triggers
    identity extraction.
    """
    doctor1 = (
        '<html><head>'
        '<title>Best Cardiologist in Noida | Medanta</title>'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org",'
        '"@type":"MedicalOrganization",'
        '"name":"Medanta",'
        '"url":"https://www.medanta.org/"}'
        '</script>'
        '</head><body>'
        '<h1>Best Cardiologist in Noida</h1>'
        '<p>Expert heart care at Medanta.</p>'
        '</body></html>'
    )
    doctor2 = (
        '<html><head>'
        '<title>Best Neurologist in Gurugram | Medanta</title>'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org",'
        '"@type":"MedicalOrganization",'
        '"name":"Medanta",'
        '"url":"https://www.medanta.org/"}'
        '</script>'
        '</head><body>'
        '<h1>Best Neurologist in Gurugram</h1>'
        '<p>Expert neurology care at Medanta.</p>'
        '</body></html>'
    )
    pages = [
        ("https://www.medanta.org/doctor-listing/cardiac-doctors-in-noida",
         doctor1),
        ("https://www.medanta.org/doctor-listing/neurologists-in-gurugram",
         doctor2),
    ]
    dataset = build_dataset(pages, target_url="https://www.medanta.org/")

    assert dataset.hospital is not None
    assert dataset.hospital.name == "Medanta"

    quality_conflicts = dataset.quality.conflicts
    hospital_name_conflicts = [
        c for c in quality_conflicts
        if c.category == "hospital" and c.field == "name"
    ]
    assert hospital_name_conflicts == []
