"""Tests for crawl context construction."""

from __future__ import annotations

import pytest

from hospital_extractor.crawler.context import CrawlLimits, build_context
from hospital_extractor.utils.urls import InvalidURLError


class TestBuildContext:
    def test_validates_and_normalizes_url(self) -> None:
        context = build_context("HTTPS://www.MaxHealthcare.in/")
        assert context.target_url == "https://www.maxhealthcare.in/"
        assert context.target_domain == "www.maxhealthcare.in"

    def test_derives_hospital_identity_from_slug(self) -> None:
        context = build_context(
            "https://www.maxhealthcare.in/hospital-network/max-smart-super-speciality-hospital-saket"
        )
        assert context.target_hospital_identity == "max-smart-super-speciality-hospital-saket"
        assert context.normalized_target_path == (
            "/hospital-network/max-smart-super-speciality-hospital-saket"
        )

    def test_rejects_invalid_url(self) -> None:
        with pytest.raises(InvalidURLError):
            build_context("not-a-url")

    def test_user_agent_becomes_primary(self) -> None:
        context = build_context("https://example.com/", user_agent="MyBot/1.0")
        assert context.user_agents[0] == "MyBot/1.0"
        assert context.user_agent == "MyBot/1.0"

    def test_custom_limits_used(self) -> None:
        limits = CrawlLimits(max_pages=5, max_depth=1, delay=2.0)
        context = build_context("https://example.com/", limits=limits)
        assert context.crawl_limits.max_pages == 5
        assert context.crawl_limits.max_depth == 1
        assert context.crawl_limits.delay == 2.0

    def test_default_limits_applied(self) -> None:
        context = build_context("https://example.com/")
        assert context.crawl_limits.max_pages == CrawlLimits().max_pages
        assert context.crawl_limits.max_response_bytes == 20 * 1024 * 1024
