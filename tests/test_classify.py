"""Tests for the deterministic page classifier."""

from __future__ import annotations

from hospital_extractor.crawler.classify import (
    LOW_VALUE_CATEGORIES,
    PageClassifier,
    is_low_value_category,
)

DOCTOR_PAGE = """\
<html>
<head><title>Cardiologists in Saket | Our Specialists</title></head>
<body>
  <h1>Our Specialists</h1>
  <script type="application/ld+json">
    {"@type": "Physician", "name": "Dr. A. Cardiologist"}
  </script>
  <p>Dr. A. Cardiologist is a physician at the hospital.</p>
</body>
</html>
"""

HOSPITAL_PAGE = """\
<html>
<head><title>Saket Hospital | About Us</title></head>
<body>
  <h1>About Our Hospital</h1>
  <p>Welcome to the hospital.</p>
</body>
</html>
"""

HOSPITAL_PAGE_WITH_EMERGENCY_FOOTER = """\
<html>
<head><title>Best Hospital in Saket</title></head>
<body>
  <h1>About Our Hospital</h1>
  <p>Welcome to the hospital. We care for patients.</p>
  <footer>24x7 Emergency and ambulance. Emergency ward. Ambulance. 24/7 helpline.</footer>
</body>
</html>
"""

DOCTOR_PAGE_WITH_EMERGENCY_FOOTER = """\
<html>
<head><title>Cardiologists in Saket | Our Specialists</title></head>
<body>
  <h1>Our Specialists</h1>
  <script type="application/ld+json">
    {"@type": "Physician", "name": "Dr. A. Cardiologist"}
  </script>
  <p>A physician at the hospital.</p>
  <footer>24x7 Emergency and ambulance. Emergency ward. Ambulance. 24/7 helpline.</footer>
</body>
</html>
"""

BLOG_FAQ_PAGE = """\
<html>
<head><title>10 Breast Cancer FAQs</title></head>
<body>
  <h1>10 Breast Cancer FAQs</h1>
  <p>Answers to your questions. In case of emergency call the 24x7 ambulance.</p>
</body>
</html>
"""

EMERGENCY_PAGE = """\
<html>
<head><title>24x7 Emergency & Trauma Care</title></head>
<body>
  <h1>Emergency Services</h1>
  <p>Ambulance services round the clock. Emergency ward, 24/7 helpline.</p>
</body>
</html>
"""


class TestClassifierFromUrl:
    def test_doctor_listing(self) -> None:
        classification = PageClassifier().from_url(
            "https://example.com/doctor/cardiologists-in-saket"
        )
        assert classification.category == "doctor"
        assert classification.confidence > 0.5

    def test_hospital_profile(self) -> None:
        classification = PageClassifier().from_url(
            "https://example.com/hospital-network/saket-hospital"
        )
        assert classification.category == "hospital"

    def test_speciality_centre(self) -> None:
        classification = PageClassifier().from_url("https://example.com/cardiology-centre")
        assert classification.category == "speciality"

    def test_blog_is_low_value(self) -> None:
        classification = PageClassifier().from_url("https://example.com/blog/hello")
        assert classification.category == "blog"
        assert is_low_value_category(classification.category)

    def test_blog_faqs_slug_stays_blog(self) -> None:
        classification = PageClassifier().from_url(
            "https://example.com/blogs/10-breast-cancer-faqs"
        )
        assert classification.category == "blog"
        assert is_low_value_category(classification.category)

    def test_unknown_path(self) -> None:
        classification = PageClassifier().from_url("https://example.com/c/i9c2ke87")
        assert classification.category == "unknown"

    def test_low_value_set_matches_catalog(self) -> None:
        assert LOW_VALUE_CATEGORIES == frozenset({"blog", "career", "news", "booking"})


class TestClassifierFromContent:
    def test_jsonld_physician_dominates(self) -> None:
        classification = PageClassifier().from_content(DOCTOR_PAGE, "https://example.com/doctor/x")
        assert classification.category == "doctor"
        assert classification.confidence >= 0.85

    def test_title_based_hospital(self) -> None:
        classification = PageClassifier().from_content(
            HOSPITAL_PAGE, "https://example.com/hospital-network/saket-hospital"
        )
        assert classification.category == "hospital"

    def test_empty_content_unknown(self) -> None:
        classification = PageClassifier().from_content("<html></html>", "https://example.com/x")
        assert classification.category == "unknown"
        assert classification.confidence == 0.0

    def test_hospital_survives_emergency_footer_boilerplate(self) -> None:
        classification = PageClassifier().from_content(
            HOSPITAL_PAGE_WITH_EMERGENCY_FOOTER,
            "https://example.com/hospital-network/max-super-speciality-hospital-saket",
        )
        assert classification.category == "hospital"
        assert classification.confidence >= 0.8

    def test_doctor_survives_emergency_footer_boilerplate(self) -> None:
        classification = PageClassifier().from_content(
            DOCTOR_PAGE_WITH_EMERGENCY_FOOTER,
            "https://example.com/doctor/cardiologists-in-saket",
        )
        assert classification.category == "doctor"

    def test_blog_with_emergency_text_stays_blog(self) -> None:
        classification = PageClassifier().from_content(
            BLOG_FAQ_PAGE, "https://example.com/blogs/10-breast-cancer-faqs"
        )
        assert classification.category == "blog"
        assert is_low_value_category(classification.category)

    def test_real_emergency_page_is_emergency(self) -> None:
        classification = PageClassifier().from_content(
            EMERGENCY_PAGE, "https://example.com/emergency"
        )
        assert classification.category == "emergency"
