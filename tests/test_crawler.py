"""Fixture-based tests for the crawler foundation.

No live network access: robots.txt, sitemaps, and page HTML are canned
fixtures; HTTP fetching is exercised through ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine

import httpx
import pytest

from hospital_extractor.crawler.crawler import (
    Crawler,
    CrawlStoppedError,
    FetchError,
    MaxPagesError,
)
from hospital_extractor.crawler.discovery import (
    SitemapData,
    discover_seeds,
    extract_internal_links,
    hospital_slug_from_url,
    parse_sitemap,
)
from hospital_extractor.crawler.url_filter import UrlFilter, parse_robots_txt

ROBOTS_TXT = """\
# Max-style robots.txt
User-agent: *
Disallow: /book-an-appointment/doctor
Disallow: *?speciality=*
Disallow: *?doctor=*
Disallow: /disease
Allow: /book-an-appointment/doctor/wellness

User-agent: hospital-extractor
Disallow: /private
"""

SITEMAP_INDEX = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.maxhealthcare.in/speciality-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://www.maxhealthcare.in/doctors-profiles-sitemap.xml</loc></sitemap>
</sitemapindex>
"""

SITEMAP_URLSET = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.maxhealthcare.in/hospital-network/max-smart-super-speciality-hospital-saket</loc></url>
  <url><loc>https://www.maxhealthcare.in/doctor/cardiologists-in-saket</loc></url>
  <url><loc>https://www.maxhealthcare.in/doctor/orthopaedists-in-delhi</loc></url>
  <url><loc>https://www.maxhealthcare.in/procedures/total-knee-replacement</loc></url>
  <url><loc>https://www.maxhealthcare.in/book-an-appointment/doctor/some-doctor</loc></url>
</urlset>
"""

PAGE_HTML = """\
<html>
<body>
  <a href="/hospital-network/max-smart-super-speciality-hospital-saket">Hospital</a>
  <a href="https://www.maxhealthcare.in/doctor/cardiologists-in-saket">Cardiologists</a>
  <a href="https://other.example.com/off-site">Off site</a>
  <a href="javascript:void(0)">JS</a>
  <a href="mailto:info@maxhealthcare.in">Mail</a>
  <a href="#faq">Anchor</a>
  <a href="../relative">Relative</a>
  <a href="/hospital-network/max-smart-super-speciality-hospital-saket">Dup</a>
</body>
</html>
"""


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


class TestParseRobotsTxt:
    def test_wildcard_group_selected_by_default(self) -> None:
        policy = parse_robots_txt(ROBOTS_TXT, "SomeCrawler/1.0")
        assert policy.can_fetch("https://example.com/book-an-appointment/doctor/x") is False
        assert policy.can_fetch("https://example.com/procedures/total-knee-replacement") is True

    def test_exact_user_agent_group_wins(self) -> None:
        policy = parse_robots_txt(ROBOTS_TXT, "hospital-extractor")
        assert policy.can_fetch("https://example.com/private") is False
        assert policy.can_fetch("https://example.com/book-an-appointment/doctor/x") is True

    def test_disallow_prefix(self) -> None:
        policy = parse_robots_txt(ROBOTS_TXT, "SomeCrawler/1.0")
        assert policy.can_fetch("https://example.com/disease") is False
        assert policy.can_fetch("https://example.com/disease/diabetes") is False
        assert policy.can_fetch("https://example.com/diseases") is False
        assert policy.can_fetch("https://example.com/healthy") is True

    def test_allow_overrides_longer_disallow(self) -> None:
        policy = parse_robots_txt(ROBOTS_TXT, "SomeCrawler/1.0")
        assert policy.can_fetch("https://example.com/book-an-appointment/doctor/wellness") is True

    def test_wildcard_query_pattern(self) -> None:
        policy = parse_robots_txt(ROBOTS_TXT, "SomeCrawler/1.0")
        assert (
            policy.can_fetch(
                "https://example.com/doctor/cardiologists-in-delhi?speciality=cardiology"
            )
            is False
        )
        assert policy.can_fetch("https://example.com/doctor/cardiologists-in-delhi?page=2") is True

    def test_end_anchor(self) -> None:
        policy = parse_robots_txt("User-agent: *\nDisallow: /exact$\n", "x")
        assert policy.can_fetch("https://example.com/exact") is False
        assert policy.can_fetch("https://example.com/exact/child") is True

    def test_empty_disallow_allows_everything(self) -> None:
        policy = parse_robots_txt("User-agent: *\nDisallow:\n", "x")
        assert policy.can_fetch("https://example.com/anything") is True

    def test_comments_and_unknown_fields_ignored(self) -> None:
        content = "User-agent: *\n# comment\nSitemap: /sitemap.xml\nDisallow: /a\n"
        policy = parse_robots_txt(content, "x")
        assert policy.can_fetch("https://example.com/a/b") is False
        assert policy.can_fetch("https://example.com/b") is True


class TestParseSitemap:
    def test_urlset_returns_urls(self) -> None:
        data = parse_sitemap(SITEMAP_URLSET, "https://www.maxhealthcare.in/")
        assert len(data.urls) == 5
        assert data.child_sitemaps == ()
        assert "https://www.maxhealthcare.in/procedures/total-knee-replacement" in data.urls

    def test_sitemap_index_returns_children(self) -> None:
        data = parse_sitemap(SITEMAP_INDEX)
        assert data.urls == ()
        assert data.child_sitemaps == (
            "https://www.maxhealthcare.in/speciality-sitemap.xml",
            "https://www.maxhealthcare.in/doctors-profiles-sitemap.xml",
        )

    def test_relative_locs_resolved_against_base(self) -> None:
        xml = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>/doctor/cardiologists-in-saket</loc></url>"
            "</urlset>"
        )
        data = parse_sitemap(xml, "https://www.maxhealthcare.in/")
        assert data.urls == ("https://www.maxhealthcare.in/doctor/cardiologists-in-saket",)

    def test_malformed_xml_returns_empty(self) -> None:
        assert parse_sitemap("<urlset><url>", "https://example.com/") == SitemapData()


class TestExtractInternalLinks:
    def test_same_domain_links_deduped_in_order(self) -> None:
        links = extract_internal_links(PAGE_HTML, "https://www.maxhealthcare.in/")
        assert links == [
            "https://www.maxhealthcare.in/hospital-network/max-smart-super-speciality-hospital-saket",
            "https://www.maxhealthcare.in/doctor/cardiologists-in-saket",
            "https://www.maxhealthcare.in/relative",
        ]

    def test_url_filter_applied(self) -> None:
        robots = parse_robots_txt(ROBOTS_TXT, "HospitalExtractor")
        url_filter = UrlFilter("https://www.maxhealthcare.in/", robots=robots)
        html = (
            '<a href="/doctor/cardiologists-in-saket?speciality=cardiology">A</a>'
            '<a href="/procedures/total-knee-replacement">B</a>'
        )
        links = extract_internal_links(html, "https://www.maxhealthcare.in/", url_filter=url_filter)
        assert links == ["https://www.maxhealthcare.in/procedures/total-knee-replacement"]


class TestHospitalSlug:
    def test_hospital_network_slug(self) -> None:
        assert (
            hospital_slug_from_url(
                "https://www.maxhealthcare.in/hospital-network/max-smart-super-speciality-hospital-saket"
            )
            == "max-smart-super-speciality-hospital-saket"
        )

    def test_falls_back_to_last_segment(self) -> None:
        assert hospital_slug_from_url("https://www.maxhealthcare.in/contact-us") == "contact-us"

    def test_root_url_has_no_slug(self) -> None:
        assert hospital_slug_from_url("https://www.maxhealthcare.in/") is None


class TestDiscoverSeeds:
    def test_filters_and_orders_by_scope(self) -> None:
        robots = parse_robots_txt(ROBOTS_TXT, "HospitalExtractor")
        url_filter = UrlFilter("https://www.maxhealthcare.in/", robots=robots)
        seeds = discover_seeds(
            "https://www.maxhealthcare.in/",
            SITEMAP_URLSET,
            hospital_slug="max-smart-super-speciality-hospital-saket",
            location="saket",
            url_filter=url_filter,
        )
        assert "https://www.maxhealthcare.in/book-an-appointment/doctor/some-doctor" not in seeds
        assert seeds[0] == (
            "https://www.maxhealthcare.in/hospital-network/"
            "max-smart-super-speciality-hospital-saket"
        )
        assert seeds[1] == "https://www.maxhealthcare.in/doctor/cardiologists-in-saket"
        assert seeds[-1] == "https://www.maxhealthcare.in/procedures/total-knee-replacement"


class TestUrlFilter:
    def test_allows_plain_fetchable_page(self) -> None:
        url_filter = UrlFilter("https://example.com/")
        assert url_filter.is_allowed("https://example.com/procedures/total-knee-replacement")

    def test_rejects_off_domain(self) -> None:
        url_filter = UrlFilter("https://example.com/")
        assert not url_filter.is_allowed("https://other.example.com/procedures")

    def test_rejects_assets(self) -> None:
        url_filter = UrlFilter("https://example.com/")
        assert not url_filter.is_allowed("https://example.com/logo.png")
        assert not url_filter.is_allowed("https://example.com/paper.pdf")

    def test_rejects_path_prefix(self) -> None:
        url_filter = UrlFilter("https://example.com/", block_path_prefixes=("/blog/",))
        assert not url_filter.is_allowed("https://example.com/blog/health")
        assert url_filter.is_allowed("https://example.com/news/health")

    def test_rejects_query_param(self) -> None:
        url_filter = UrlFilter(
            "https://example.com/", block_query_params=("utm_source", "speciality")
        )
        assert not url_filter.is_allowed("https://example.com/a?utm_source=newsletter")
        assert not url_filter.is_allowed("https://example.com/doctor/x?speciality=cardiology")

    def test_robots_restriction_applied(self) -> None:
        robots = parse_robots_txt(ROBOTS_TXT, "HospitalExtractor")
        url_filter = UrlFilter("https://example.com/", robots=robots)
        assert not url_filter.is_allowed("https://example.com/book-an-appointment/doctor/x")

    def test_subdomain_opt_in(self) -> None:
        strict = UrlFilter("https://example.com/")
        assert not strict.is_allowed("https://care.example.com/a")
        lax = UrlFilter("https://example.com/", allow_subdomains=True)
        assert lax.is_allowed("https://care.example.com/a")


MockHandler = Callable[[httpx.Request], httpx.Response]


def _crawler_client(handler: MockHandler) -> tuple[Crawler, httpx.AsyncClient]:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        headers={"User-Agent": "HospitalExtractor/0.1 test"},
    )
    crawler = Crawler(client=client, delay=0.0)
    return crawler, client


class TestCrawler:
    def test_fetch_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["user-agent"].startswith("HospitalExtractor")
            return httpx.Response(200, text="<html><title>Hi</title></html>")

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            page = await crawler.fetch("https://example.com/procedures/x")
            assert page.status_code == 200
            assert page.html == b"<html><title>Hi</title></html>"
            assert page.final_url == "https://example.com/procedures/x"
            assert crawler.pages_fetched == 1
            await client.aclose()

        _run(scenario())

    def test_retries_transient_error_then_succeeds(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if len(calls) == 1:
                return httpx.Response(500)
            return httpx.Response(200, text="ok")

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            page = await crawler.fetch("https://example.com/a")
            assert page.status_code == 200
            assert len(calls) == 2
            await client.aclose()

        _run(scenario())

    def test_no_retry_on_404(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(404)

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            page = await crawler.fetch("https://example.com/missing")
            assert page.status_code == 404
            assert len(calls) == 1
            await client.aclose()

        _run(scenario())

    def test_403_is_not_retried(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(403)

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            page = await crawler.fetch("https://example.com/blocked")
            assert page.status_code == 403
            assert len(calls) == 1
            await client.aclose()

        _run(scenario())

    def test_returns_last_response_after_retries_exhausted(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(500)

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        crawler = Crawler(client=client, retries=1, delay=0.0)

        async def scenario() -> None:
            page = await crawler.fetch("https://example.com/bad")
            assert page.status_code == 500
            assert len(calls) == 2
            await client.aclose()

        _run(scenario())

    def test_transport_error_retried_then_raises(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            raise httpx.ConnectError("boom", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        crawler = Crawler(client=client, retries=1, delay=0.0)

        async def scenario() -> None:
            with pytest.raises(FetchError):
                await crawler.fetch("https://example.com/down")
            assert len(calls) == 2
            await client.aclose()

        _run(scenario())

    def test_follows_redirects(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/old":
                return httpx.Response(302, headers={"Location": "/new"})
            return httpx.Response(200, text="ok")

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            page = await crawler.fetch("https://example.com/old")
            assert page.final_url == "https://example.com/new"
            assert len(calls) == 2
            await client.aclose()

        _run(scenario())

    def test_max_pages_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        crawler = Crawler(client=client, max_pages=1, delay=0.0)

        async def scenario() -> None:
            await crawler.fetch("https://example.com/a")
            with pytest.raises(MaxPagesError):
                await crawler.fetch("https://example.com/b")
            await client.aclose()

        _run(scenario())

    def test_stop_blocks_further_fetch(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            crawler.stop()
            with pytest.raises(CrawlStoppedError):
                await crawler.fetch("https://example.com/a")
            await client.aclose()

        _run(scenario())

    def test_crawl_stops_at_page_budget(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        crawler = Crawler(client=client, max_pages=2, delay=0.0)

        async def scenario() -> None:
            urls = ["https://example.com/a", "https://example.com/b", "https://example.com/c"]
            pages = [page async for page in crawler.crawl(urls)]
            assert len(pages) == 2
            await client.aclose()

        _run(scenario())

    def test_throttle_enforces_delay(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        crawler = Crawler(client=client, delay=0.05)

        async def scenario() -> None:
            start = time.monotonic()
            await crawler.fetch("https://example.com/a")
            await crawler.fetch("https://example.com/b")
            elapsed = time.monotonic() - start
            assert elapsed >= 0.05
            await client.aclose()

        _run(scenario())

    def test_probe_reports_reachable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow:")
            return httpx.Response(200, text="<html></html>")

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            result = await crawler.probe("https://example.com/")
            assert result.reachable
            assert not result.forbidden
            assert result.robots_status == 200
            assert result.homepage_status == 200
            await client.aclose()

        _run(scenario())

    def test_probe_flags_forbidden_403(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *")
            return httpx.Response(403)

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            result = await crawler.probe("https://example.com/")
            assert result.forbidden
            assert not result.reachable
            await client.aclose()

        _run(scenario())

    def test_fetch_records_content_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="<html></html>", headers={"Content-Type": "text/html; charset=utf-8"}
            )

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            page = await crawler.fetch("https://example.com/a")
            assert page.content_type == "text/html; charset=utf-8"
            assert page.is_html
            await client.aclose()

        _run(scenario())

    def test_missing_content_type_treated_as_html(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html></html>")

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            page = await crawler.fetch("https://example.com/a")
            assert page.content_type is None
            assert page.is_html
            await client.aclose()

        _run(scenario())

    def test_non_html_content_type_flag(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="{}", headers={"Content-Type": "application/json"})

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            page = await crawler.fetch("https://example.com/api")
            assert not page.is_html
            await client.aclose()

        _run(scenario())

    def test_fetch_resource_not_counted_against_page_budget(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        crawler = Crawler(client=client, max_pages=1, delay=0.0)

        async def scenario() -> None:
            resource = await crawler.fetch_resource("https://example.com/sitemap.xml")
            assert resource.status_code == 200
            assert resource.content == b"ok"
            assert crawler.pages_fetched == 0

            await crawler.fetch("https://example.com/a")
            assert crawler.pages_fetched == 1

            resource = await crawler.fetch_resource("https://example.com/robots.txt")
            assert resource.status_code == 200
            assert crawler.pages_fetched == 1

            with pytest.raises(MaxPagesError):
                await crawler.fetch("https://example.com/b")
            await client.aclose()

        _run(scenario())

    def test_response_body_truncated_over_limit(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="a" * 1000)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        crawler = Crawler(client=client, max_response_bytes=10, delay=0.0)

        async def scenario() -> None:
            page = await crawler.fetch("https://example.com/big")
            assert page.truncated
            assert len(page.html) < 1000
            await client.aclose()

        _run(scenario())

    def test_probe_captures_content_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *")
            return httpx.Response(
                200, text="<html></html>", headers={"Content-Type": "text/html; charset=utf-8"}
            )

        crawler, client = _crawler_client(handler)

        async def scenario() -> None:
            result = await crawler.probe("https://example.com/")
            assert result.homepage.content_type == "text/html; charset=utf-8"
            assert result.robots.final_url == "https://example.com/robots.txt"
            await client.aclose()

        _run(scenario())
