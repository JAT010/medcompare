"""Tests for the sitemap discovery orchestrator."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine

from hospital_extractor.crawler.candidates import SOURCE_SITEMAP
from hospital_extractor.crawler.discovery import (
    discover_sitemap_candidates,
    scope_rank,
)

INDEX = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap-2.xml</loc></sitemap>
</sitemapindex>
"""

URLSET = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/hospital-network/saket-hospital</loc></url>
  <url><loc>https://example.com/doctor/cardiologists-in-saket</loc></url>
  <url><loc>https://example.com/procedures/x</loc></url>
</urlset>
"""

URLSET_2 = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/contact-us</loc></url>
  <url><loc>https://example.com/facilities/x</loc></url>
  <url><loc>https://example.com/doctor/orthopaedists-in-saket</loc></url>
</urlset>
"""

Fetcher = Callable[[str], Coroutine[object, object, bytes | None]]


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


class TestScopeRank:
    def test_hospital_slug_is_top_scope(self) -> None:
        url = "https://example.com/hospital-network/saket-hospital/contact"
        assert scope_rank(url, "saket-hospital", None) == 0

    def test_location_doctor_listing_second(self) -> None:
        url = "https://example.com/doctor/cardiologists-in-saket"
        assert scope_rank(url, "saket-hospital", "saket") == 1

    def test_everything_else_last(self) -> None:
        url = "https://example.com/procedures/total-knee-replacement"
        assert scope_rank(url, "saket-hospital", "saket") == 2


class TestDiscoverSitemapCandidates:
    def test_walks_index_to_urlsets(self) -> None:
        async def fetcher(url: str) -> bytes | None:
            if url == "https://example.com/sitemap-index.xml":
                return INDEX.encode()
            if url == "https://example.com/sitemap-1.xml":
                return URLSET.encode()
            if url == "https://example.com/sitemap-2.xml":
                return URLSET_2.encode()
            return None

        result = _run(
            discover_sitemap_candidates(
                fetcher,
                "https://example.com/",
                robots_sitemaps=("https://example.com/sitemap-index.xml",),
            )
        )
        assert result.sitemap_urls == (
            "https://example.com/sitemap-1.xml",
            "https://example.com/sitemap-2.xml",
            "https://example.com/sitemap-index.xml",
        )
        assert len(result.candidates) == 6
        assert all(c.source_of_discovery == SOURCE_SITEMAP for c in result.candidates)
        assert all(c.sitemap_name in result.sitemap_urls for c in result.candidates)

    def test_fallback_to_default_sitemap(self) -> None:
        async def fetcher(url: str) -> bytes | None:
            if url == "https://example.com/sitemap.xml":
                return URLSET.encode()
            return None

        result = _run(discover_sitemap_candidates(fetcher, "https://example.com/"))
        assert "https://example.com/sitemap.xml" in result.sitemap_urls
        assert len(result.candidates) == 3

    def test_unfetchable_sitemaps_skipped(self) -> None:
        async def fetcher(url: str) -> bytes | None:
            return None

        result = _run(
            discover_sitemap_candidates(
                fetcher,
                "https://example.com/",
                robots_sitemaps=("https://example.com/sitemap.xml",),
            )
        )
        assert result.candidates == []
        assert result.sitemap_urls == ("https://example.com/sitemap.xml",)

    def test_duplicate_urls_deduped_across_sitemaps(self) -> None:
        async def fetcher(url: str) -> bytes | None:
            return URLSET.encode()

        result = _run(
            discover_sitemap_candidates(
                fetcher,
                "https://example.com/",
                robots_sitemaps=(
                    "https://example.com/sitemap-1.xml",
                    "https://example.com/sitemap-2.xml",
                ),
            )
        )
        assert len(result.candidates) == 3

    def test_max_sitemaps_bounds_walk(self) -> None:
        async def fetcher(url: str) -> bytes | None:
            if url in ("https://example.com/sitemap-1.xml", "https://example.com/sitemap-2.xml"):
                return URLSET.encode()
            return None

        result = _run(
            discover_sitemap_candidates(
                fetcher,
                "https://example.com/",
                robots_sitemaps=("https://example.com/sitemap.xml",),
                max_sitemaps=1,
            )
        )
        assert result.sitemap_urls == ("https://example.com/sitemap.xml",)

    def test_nested_depth_capped(self) -> None:
        async def fetcher(url: str) -> bytes | None:
            if url == "https://example.com/sitemap-index.xml":
                return INDEX.encode()
            return None

        result = _run(
            discover_sitemap_candidates(
                fetcher,
                "https://example.com/",
                robots_sitemaps=("https://example.com/sitemap-index.xml",),
                max_sitemap_depth=0,
            )
        )
        assert result.sitemap_urls == ("https://example.com/sitemap-index.xml",)
        assert result.candidates == []

    def test_malformed_sitemap_does_not_abort(self) -> None:
        async def fetcher(url: str) -> bytes | None:
            if url == "https://example.com/sitemap.xml":
                return b"<urlset><url>"
            return None

        result = _run(
            discover_sitemap_candidates(
                fetcher,
                "https://example.com/",
                robots_sitemaps=("https://example.com/sitemap.xml",),
            )
        )
        assert result.candidates == []
