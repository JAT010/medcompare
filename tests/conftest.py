"""Shared fixtures for the extractor test suite."""

from __future__ import annotations

import pytest

from hospital_extractor.config import load_config

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
