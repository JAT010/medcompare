"""Tests for locality (area) extraction in the contact extractor."""

from __future__ import annotations

from hospital_extractor.extraction.contact import extract_contact
from hospital_extractor.extraction.page_analyzer import analyze_page

PAGE = "https://example.com/contact-us"


def analyze(html: str):
    return analyze_page(html, PAGE)


class TestLocalityExtraction:
    def test_from_jsonld_neighborhood(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Hospital","name":"Saket Hospital",
         "address":{"streetAddress":"Press Enclave Road",
                    "neighborhood":"Koregaon Park",
                    "addressLocality":"Pune",
                    "postalCode":"411001"}}
        </script></head>
        <body><h1>Saket Hospital</h1></body></html>
        """
        contact = extract_contact(analyze(html))
        assert contact is not None
        assert contact.locality == "Koregaon Park"
        assert contact.city == "Pune"

    def test_from_labelled_element(self) -> None:
        html = """
        <html><body>
        <p class="locality">Kothrud</p>
        <p>123 Main Road, Pune, Maharashtra 411038</p>
        </body></html>
        """
        contact = extract_contact(analyze(html))
        assert contact is not None
        assert contact.locality == "Kothrud"

    def test_from_address_segment_before_city(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Hospital","name":"Saket Hospital",
         "address":{"addressLocality":"Pune","postalCode":"411001"}}
        </script></head>
        <body><address>Lane 3, Koregaon Park, Pune, Maharashtra 411001</address></body></html>
        """
        contact = extract_contact(analyze(html))
        assert contact is not None
        assert contact.city == "Pune"
        assert contact.locality == "Koregaon Park"

    def test_no_locality_when_absent(self) -> None:
        html = """
        <html><body>
        <address>123 Main Road, Pune, Maharashtra 411001</address>
        </body></html>
        """
        contact = extract_contact(analyze(html))
        assert contact is not None
        assert contact.locality is None
