"""Tests for logo URL extraction in the hospital overview extractor."""

from __future__ import annotations

from hospital_extractor.extraction.hospital import extract_hospital_overview
from hospital_extractor.extraction.page_analyzer import analyze_page

PAGE = "https://example.com/hospital-network/saket"


def analyze(html: str):
    return analyze_page(html, PAGE)


class TestLogoExtraction:
    def test_logo_from_jsonld_string(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Hospital","name":"Saket Hospital",
         "logo":"https://cdn.example.com/logo.png"}
        </script></head>
        <body><h1>Saket Hospital</h1></body></html>
        """
        overview = extract_hospital_overview(analyze(html))
        assert overview is not None
        assert overview.logo_url == "https://cdn.example.com/logo.png"

    def test_logo_from_jsonld_object(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Hospital","name":"Saket Hospital",
         "logo":{"@type":"ImageObject","url":"/assets/logo.svg"}}
        </script></head>
        <body><h1>Saket Hospital</h1></body></html>
        """
        overview = extract_hospital_overview(analyze(html))
        assert overview is not None
        assert overview.logo_url == "https://example.com/assets/logo.svg"

    def test_logo_from_favicon(self) -> None:
        html = """
        <html><head>
        <link rel="icon" href="/favicon.ico">
        <script type="application/ld+json">
        {"@type":"Hospital","name":"Saket Hospital"}
        </script></head>
        <body><h1>Saket Hospital</h1></body></html>
        """
        overview = extract_hospital_overview(analyze(html))
        assert overview is not None
        assert overview.logo_url == "https://example.com/favicon.ico"

    def test_logo_from_header_image(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Hospital","name":"Saket Hospital"}
        </script></head>
        <body><header><img src="/images/header-logo.png"></header>
        <h1>Saket Hospital</h1></body></html>
        """
        overview = extract_hospital_overview(analyze(html))
        assert overview is not None
        assert overview.logo_url == "https://example.com/images/header-logo.png"

    def test_no_logo(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Hospital","name":"Saket Hospital"}
        </script></head>
        <body><h1>Saket Hospital</h1></body></html>
        """
        overview = extract_hospital_overview(analyze(html))
        assert overview is not None
        assert overview.logo_url is None
