"""Tests for page classification, category routing and FAQ mapping."""

from __future__ import annotations

from hospital_extractor.crawler.classify import PageClassifier
from hospital_extractor.schema.mapper import FAQ_ROUTES, route_faq_question


def test_classifier_routes_structural_urls():
    classifier = PageClassifier()

    assert classifier.from_url("https://example.test/doctors").category == "doctor"
    assert classifier.from_url("https://example.test/faq").category == "faq"
    assert classifier.from_url("https://example.test/technology").category == "technology"
    assert classifier.from_url("https://example.test/contact").category == "contact"


def test_classifier_confidence_high_for_known_paths():
    classifier = PageClassifier()

    classification = classifier.from_url("https://example.test/doctors")
    assert classification.confidence >= 0.9


def test_faq_routes_only_to_facilities():
    routed = {route_faq_question(q) for q in ("diagnostic lab", "parking", "blood bank")}
    assert routed == {"facilities"}


def test_faq_route_requires_known_keyword():
    assert route_faq_question("What is your visiting policy?") is None


def test_faq_routes_are_facilities_only():
    assert all(route == "facilities" for _, route in FAQ_ROUTES)
