"""End-to-end tests for the crawl runner against a mocked site."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path

import httpx
import pytest

import hospital_extractor.crawler.runner as runner_module
from hospital_extractor.crawler.context import CrawlLimits, build_context
from hospital_extractor.crawler.crawler import Crawler
from hospital_extractor.crawler.report import format_summary
from hospital_extractor.crawler.runner import TargetInaccessibleError, run_crawl

ROBOTS = """\
User-agent: *
Disallow: /book-an-appointment
Sitemap: https://example.com/sitemap-index.xml
"""

SITEMAP_INDEX = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-pages.xml</loc></sitemap>
</sitemapindex>
"""

SITEMAP_PAGES = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/hospital-network/saket-hospital</loc></url>
  <url><loc>https://example.com/doctor/cardiologists-in-saket</loc></url>
  <url><loc>https://example.com/procedures/total-knee-replacement</loc></url>
  <url><loc>https://example.com/contact-us</loc></url>
  <url><loc>https://example.com/blog/hello</loc></url>
  <url><loc>https://example.com/blogs/10-breast-cancer-faqs</loc></url>
  <url><loc>https://example.com/book-an-appointment/doctor/x</loc></url>
</urlset>
"""

HOSPITAL_PAGE = """\
<html>
<head><title>Saket Hospital | About Us</title></head>
<body>
  <h1>About Our Hospital</h1>
  <p>Welcome to the hospital.</p>
  <a href="/contact-us">Contact</a>
</body>
</html>
"""

DOCTOR_PAGE = """\
<html>
<head><title>Cardiologists in Saket | Our Specialists</title></head>
<body>
  <h1>Our Specialists</h1>
  <script type="application/ld+json">{"@type": "Physician"}</script>
  <p>A physician at the hospital.</p>
</body>
</html>
"""

PROCEDURE_PAGE = (
    "<html><head><title>Total Knee Replacement</title></head><body><p>surgery</p></body></html>"
)
CONTACT_PAGE = "<html><head><title>Contact Us</title></head><body><p>call us</p></body></html>"
BLOG_PAGE = "<html><head><title>Health Blog</title></head><body><p>read</p></body></html>"
BLOG_FAQ_PAGE = (
    "<html><head><title>10 Breast Cancer FAQs</title></head><body>"
    "<h1>10 Breast Cancer FAQs</h1>"
    "<p>In case of emergency call the 24x7 ambulance.</p></body></html>"
)

MockHandler = Callable[[httpx.Request], httpx.Response]


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _site_handler() -> MockHandler:
    pages: dict[str, str] = {
        "/hospital-network/saket-hospital": HOSPITAL_PAGE,
        "/doctor/cardiologists-in-saket": DOCTOR_PAGE,
        "/procedures/total-knee-replacement": PROCEDURE_PAGE,
        "/contact-us": CONTACT_PAGE,
        "/blog/hello": BLOG_PAGE,
        "/blogs/10-breast-cancer-faqs": BLOG_FAQ_PAGE,
    }
    xml: dict[str, str] = {
        "/sitemap-index.xml": SITEMAP_INDEX,
        "/sitemap-pages.xml": SITEMAP_PAGES,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS, headers={"Content-Type": "text/plain"})
        if request.url.path in xml:
            return httpx.Response(
                200, text=xml[request.url.path], headers={"Content-Type": "text/xml"}
            )
        if request.url.path in pages:
            return httpx.Response(
                200,
                text=pages[request.url.path],
                headers={"Content-Type": "text/html; charset=utf-8"},
            )
        return httpx.Response(404)

    return handler


@pytest.fixture
def mock_site(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., list[httpx.AsyncClient]]]:
    """Patch the runner to build crawlers against a MockTransport site."""
    clients: list[httpx.AsyncClient] = []

    def install(handler: MockHandler | None = None) -> list[httpx.AsyncClient]:
        site_handler = handler or _site_handler()

        def make_crawler(**kwargs: object) -> Crawler:
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(site_handler),
                follow_redirects=True,
            )
            clients.append(client)
            return Crawler(client=client, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(runner_module, "Crawler", make_crawler)
        return clients

    yield install

    for client in clients:
        _run(client.aclose())


def test_run_crawl_full_pipeline(
    tmp_path: Path, mock_site: Callable[..., list[httpx.AsyncClient]]
) -> None:
    mock_site()
    context = build_context(
        "https://example.com/hospital-network/saket-hospital",
        limits=CrawlLimits(max_pages=20, max_depth=2, delay=0.0),
    )

    report = _run(run_crawl(context, output_dir=tmp_path, run_id="test-run"))

    assert report.probe.reachable
    assert report.robots_policy is not None
    assert report.robots_url == "https://example.com/robots.txt"
    assert report.sitemap_urls == (
        "https://example.com/sitemap-index.xml",
        "https://example.com/sitemap-pages.xml",
    )

    assert report.discovered == 4
    assert report.fetched == 4
    assert report.skipped == 0
    assert report.failed == 0

    fetched = {page.requested_url for page in report.pages}
    assert fetched == {
        "https://example.com/hospital-network/saket-hospital",
        "https://example.com/doctor/cardiologists-in-saket",
        "https://example.com/procedures/total-knee-replacement",
        "https://example.com/contact-us",
    }
    assert report.category_counts() == {"contact": 1, "doctor": 1, "hospital": 1, "procedure": 1}

    raw_files = list((tmp_path / "raw" / "test-run").glob("*.html"))
    assert len(raw_files) == 4

    summary = format_summary(report)
    assert "Pages fetched:    4" in summary
    assert "hospital: 1" in summary


def test_run_crawl_includes_low_value_when_requested(
    tmp_path: Path, mock_site: Callable[..., list[httpx.AsyncClient]]
) -> None:
    mock_site()
    context = build_context(
        "https://example.com/hospital-network/saket-hospital",
        limits=CrawlLimits(max_pages=20, max_depth=2, delay=0.0),
    )

    report = _run(run_crawl(context, output_dir=tmp_path, run_id="run-low", include_low_value=True))

    assert report.fetched == 6
    assert report.category_counts()["blog"] == 2


def test_run_crawl_raises_when_target_blocked(
    tmp_path: Path, mock_site: Callable[..., list[httpx.AsyncClient]]
) -> None:
    def blocked_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *")
        return httpx.Response(403)

    mock_site(blocked_handler)
    context = build_context(
        "https://example.com/hospital-network/saket-hospital",
        limits=CrawlLimits(max_pages=20, delay=0.0),
    )

    with pytest.raises(TargetInaccessibleError):
        _run(run_crawl(context, output_dir=tmp_path, run_id="run-blocked"))


def test_cli_parser_defaults() -> None:
    from hospital_extractor.cli import build_parser

    args = build_parser().parse_args(["https://example.com/"])
    assert args.url == "https://example.com/"
    assert args.max_pages > 0
    assert args.max_depth == 2
    assert args.output_dir == "data"
    assert args.include_low_value is False
