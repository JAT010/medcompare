"""SSRF protection tests: URL validation and redirect revalidation."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from hospital_extractor.crawler.context import build_context
from hospital_extractor.crawler.crawler import Crawler
from hospital_extractor.crawler.ssrf import (
    SSRFBlockedError,
    check_url_safety,
    is_blocked_ip,
    validate_public_url,
)
from hospital_extractor.utils.urls import InvalidURLError

PUBLIC_URL = "https://hospital.example/"


def test_public_url_passes_structural_check() -> None:
    assert check_url_safety(PUBLIC_URL) == PUBLIC_URL


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:3000/",
        "https://localhost/",
        "http://127.0.0.1/",
        "http://127.8.9.10/path",
        "http://10.0.0.5/",
        "http://192.168.1.10/",
        "http://172.16.4.4/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[fd00::1]/",
        "http://db.local/",
        "http://service.internal/",
        "https://metadata.google.internal/",
        "ftp://hospital.example/",
        "https://user:pass@hospital.example/",
    ],
)
def test_unsafe_targets_are_rejected(url: str) -> None:
    with pytest.raises((SSRFBlockedError, InvalidURLError)):
        check_url_safety(url)


def test_blocked_ip_helper_covers_shared_and_reserved_ranges() -> None:
    for address in ("100.64.0.1", "192.0.0.192", "0.0.0.0", "255.255.255.255", "not-an-ip"):
        assert is_blocked_ip(address) is True
    assert is_blocked_ip("93.184.216.34") is False
    assert is_blocked_ip("2606:4700:4700::1111") is False


def test_dns_resolution_rejects_host_resolving_to_loopback() -> None:
    with pytest.raises(SSRFBlockedError, match="non-public address"):
        validate_public_url(PUBLIC_URL, resolver=lambda _host: ["127.0.0.1"])


def test_dns_resolution_rejects_unresolvable_host() -> None:
    def _fail(_host: str) -> list[str]:
        raise OSError("name or service not known")

    with pytest.raises(SSRFBlockedError, match="could not resolve"):
        validate_public_url(PUBLIC_URL, resolver=_fail)


def test_dns_resolution_accepts_public_address() -> None:
    assert (
        validate_public_url(PUBLIC_URL, resolver=lambda _host: ["93.184.216.34"]) == PUBLIC_URL
    )


def test_build_context_rejects_private_target() -> None:
    with pytest.raises(SSRFBlockedError):
        build_context("http://127.0.0.1/")


def test_crawler_follows_safe_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "/about"})
        return httpx.Response(200, text="<html><body>About</body></html>")

    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        crawler = Crawler(client=client, delay=0.0)
        page = await crawler.fetch("https://example.test/")
        assert page.status_code == 200
        assert page.final_url == "https://example.test/about"
        await client.aclose()

    asyncio.run(run())


def test_crawler_revalidates_redirect_destination() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.test":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        return httpx.Response(200, text="should never be reached")

    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        crawler = Crawler(client=client, delay=0.0)
        with pytest.raises(SSRFBlockedError):
            await crawler.fetch("https://example.test/")
        await client.aclose()

    asyncio.run(run())


def test_crawler_rejects_private_entry_url() -> None:
    async def run() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, text="ok"))
        )
        crawler = Crawler(client=client, delay=0.0)
        with pytest.raises(SSRFBlockedError):
            await crawler.fetch("http://192.168.0.10/")
        await client.aclose()

    asyncio.run(run())
