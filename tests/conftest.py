"""Shared fixtures for the extractor test suite."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hospital_extractor.config import load_config
from hospital_extractor.models.change import ChangeSet, ChangeSetStatus, ChangeType
from hospital_extractor.models.verification import (
    ChangeVerification,
    VerificationOutcome,
    VerificationResult,
    VerificationResultStatus,
)
from hospital_extractor.reconciliation.service import create_cases_for_result
from hospital_extractor.reconciliation.settings import reconciliation_settings
from hospital_extractor.storage import HospitalDatabase

BASE = "https://example.test"

HOME_HTML = """<!doctype html><html><head><title>City Hospital - Multi Speciality</title></head>
<body>
<h1>City Hospital</h1>
<p>City Hospital is a multi-speciality hospital established in 1998. We have 650+ beds, 45 doctors
and 30 specialities. Our ICU has 120 critical care beds and we run 5 operation theatres and
2 cath labs.</p>
<script type="application/ld+json">{
  "@context": "https://schema.org",
  "@type": "Hospital",
  "name": "City Hospital",
  "url": "https://example.test/",
  "description": "A 650-bed multi-speciality tertiary care hospital in Bengaluru.",
  "logo": "https://example.test/static/logo.png",
  "telephone": "+91-80-12345678",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "12 MG Road, Koregaon Park",
    "addressLocality": "Pune",
    "addressRegion": "Maharashtra",
    "postalCode": "411001",
    "addressCountry": "IN"
  },
  "geo": {"latitude": 18.5204, "longitude": 73.8567},
  "foundingDate": "1998"
}</script>
</body></html>"""

CONTACT_HTML = """<!doctype html><html><head><title>Contact Us - City Hospital</title></head>
<body>
<h1>Contact Us</h1>
<section><h2>Address</h2><p>12 MG Road, Koregaon Park, Pune, Maharashtra 411001</p></section>
<section><h2>Emergency</h2><p>+91-80-11111111</p></section>
<section><h2>Appointment</h2><p>+91-80-22222222</p></section>
<a href="tel:+91-80-12345678">Call</a>
<a href="mailto:care@cityhospital.test">Email</a>
<a href="https://maps.google.com/?q=cityhospital">Map</a>
</body></html>"""

DOCTORS_HTML = """<!doctype html><html><head><title>Our Doctors - City Hospital</title></head>
<body>
<h1>Our Doctors</h1>
<div class="doctor-card"><h3>Dr. Anil Kumar</h3><p>Cardiologist</p>
<a href="/doctor/anil-kumar">Profile</a></div>
<div class="doctor-card"><h3>Dr. Meera Nair</h3><p>Orthopaedic Surgeon</p>
<a href="/doctor/meera-nair">Profile</a></div>
<div class="doctor-card"><h3>Dr. Anil Kumar</h3><p>Cardiologist</p>
<a href="/doctor/anil-kumar">Profile</a></div>
</body></html>"""

DEPARTMENTS_HTML = """<!doctype html><html><head><title>Departments - City Hospital</title></head>
<body>
<h1>Departments</h1>
<div class="department-card"><h3>Cardiology</h3><p>Heart care</p></div>
<div class="department-card"><h3>Orthopaedics</h3><p>Bone and joint</p></div>
<div class="department-card"><h3>Oncology</h3><p>Cancer care</p></div>
</body></html>"""

TREATMENTS_HTML = """<!doctype html><html><head><title>Treatments - City Hospital</title></head>
<body>
<h1>Treatments</h1>
<div class="treatment-card"><h3>Angioplasty</h3><p>Cardiology</p></div>
<div class="treatment-card"><h3>Knee Replacement</h3><p>Orthopaedics</p></div>
</body></html>"""

FACILITIES_HTML = """<!doctype html><html><head><title>Facilities - City Hospital</title></head>
<body>
<h1>Facilities</h1>
<div class="facility-card"><h3>24x7 Pharmacy</h3></div>
<div class="facility-card"><h3>Blood Bank</h3></div>
<h2>FAQs</h2>
<div class="faq"><h3>Do you have a diagnostic lab?</h3>
<p>Yes, our radiology lab is available round the clock.</p></div>
</body></html>"""


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def pages():
    """The standard six-page fixture used across the pipeline tests."""
    return [
        (f"{BASE}/", HOME_HTML),
        (f"{BASE}/contact", CONTACT_HTML),
        (f"{BASE}/doctors", DOCTORS_HTML),
        (f"{BASE}/departments", DEPARTMENTS_HTML),
        (f"{BASE}/treatments", TREATMENTS_HTML),
        (f"{BASE}/facilities", FACILITIES_HTML),
    ]


@pytest.fixture
def temp_db():
    """A temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = HospitalDatabase(db_path)
    yield db
    db.close()
    db_path.unlink(missing_ok=True)


@pytest.fixture
def populated_db(temp_db):
    """A database with sample reconciliation data."""

    hospital_id = "h_test123"
    change_set_id = "cs_test123"
    job_id = "v_test123"
    result_id = "r_test123"
    now = datetime.now(UTC)

    # Create a minimal dataset
    from hospital_extractor.extraction import build_dataset
    dataset = build_dataset(
        [(f"{BASE}/", HOME_HTML)],
        target_url=BASE,
        config=load_config(),
    )

    # Save dataset first
    _ = temp_db.save_dataset(dataset)

    # Save snapshot and change set
    snap = temp_db.save_snapshot(dataset, captured_at=now)
    change_set = ChangeSet(
        change_set_id=change_set_id,
        snapshot_id=snap.snapshot.snapshot_id,
        previous_snapshot_id=None,
        hospital_id=hospital_id,
        source_type="mock_api",
        detected_at=now,
        status=ChangeSetStatus.CHANGES_DETECTED,
        added=0,
        removed=0,
        modified=1,
        payload=snap.change_set.model_dump_json(),
    )
    temp_db._insert_change_set(change_set)

    # Save verification result
    result = VerificationResult(
        verification_result_id=result_id,
        job_id=job_id,
        hospital_id=hospital_id,
        change_set_id=change_set_id,
        status=VerificationResultStatus.MISMATCH,
        change_results=[
            ChangeVerification(
                path="hospital.bed_count",
                category="hospital",
                change_type=ChangeType.MODIFIED,
                outcome=VerificationOutcome.MISMATCH,
                claimed_value=650,
                website_value=600,
            )
        ],
        verification_snapshot_id=snap.snapshot.snapshot_id,
        checked_at=now,
        explanation="Test mismatch",
        created_at=now,
    )
    temp_db.save_verification_result(result)

    # Create reconciliation case
    create_cases_for_result(
        temp_db,
        result,
        settings=reconciliation_settings(),
        actor="system",
        now=now,
    )

    return temp_db