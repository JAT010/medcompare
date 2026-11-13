"""Polite asynchronous fetcher: retries, throttling, limits, graceful stop.

Uses ``httpx`` with an injectable client so tests can supply a
``MockTransport``. Defaults are chosen to be safe on production hospital
sites: a descriptive user-agent, bounded retries with exponential backoff,
a minimum delay between requests, a hard page cap and a response-size cap.

A 403 response is treated as a hard stop signal (the recon report observed
Akamai returning inconsistent 403s); it is never retried.

The fetcher only downloads the HTML body it is asked for. Binary assets,
stylesheets and scripts are never fetched by the normal page path; the
content type of every response is recorded so callers can decide what to
keep.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from hospital_extractor.crawler.ssrf import check_url_safety

DEFAULT_USER_AGENT = (
    "HospitalExtractor/0.1 (+https://github.com/kartik00052/hospital-website-extractor)"
)
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRIES = 3
DEFAULT_DELAY_SECONDS = 0.5
DEFAULT_MAX_PAGES = 200
DEFAULT_MAX_RESPONSE_BYTES = 20 * 1024 * 1024
PROBE_BODY_LIMIT = 64 * 1024

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Redirects are followed manually so every destination is revalidated before a
# request is sent to it; auto-following would let a public host redirect the
# crawler into a private network.
MAX_REDIRECTS = 20
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# Content types we consider HTML-like page content.
HTML_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/xml",
        "application/xml",
        "text/plain",
    }
)

__all__ = [
    "CrawlError",
    "CrawlStoppedError",
    "Crawler",
    "FetchError",
    "FetchResult",
    "FetchedPage",
    "HTML_CONTENT_TYPES",
    "MaxPagesError",
    "ProbeOutcome",
    "ProbeResult",
]


class CrawlError(RuntimeError):
    """Base class for crawler failures."""


class FetchError(CrawlError):
    """A URL could not be fetched after exhausting retries."""


class MaxPagesError(CrawlError):
    """The crawl page budget was exhausted."""


class CrawlStoppedError(CrawlError):
    """The crawl was stopped programmatically via :meth:`Crawler.stop`."""


@dataclass(frozen=True)
class FetchedPage:
    """One fetched page.

    ``url`` is the normalized request URL, ``final_url`` the post-redirect
    URL, and ``html`` the raw response body bytes (possibly truncated).
    """

    url: str
    status_code: int
    html: bytes
    final_url: str
    content_type: str | None = None
    truncated: bool = False

    @property
    def is_html(self) -> bool:
        """True when the response looks like HTML/text page content.

        A missing content type is treated as HTML (many servers omit it).
        """
        if self.content_type is None:
            return True
        media_type = self.content_type.split(";", 1)[0].strip().lower()
        return media_type in HTML_CONTENT_TYPES or media_type.startswith("text/")


@dataclass(frozen=True)
class FetchResult:
    """Raw outcome of one HTTP exchange (used by pages and sitemaps)."""

    status_code: int
    content: bytes
    final_url: str
    content_type: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class ProbeOutcome:
    """Access probe result for a single URL."""

    url: str
    status_code: int | None
    final_url: str | None
    content_type: str | None


@dataclass(frozen=True)
class ProbeResult:
    """Reachability probe of a hospital website before committing to a crawl."""

    robots: ProbeOutcome
    homepage: ProbeOutcome

    @property
    def robots_status(self) -> int | None:
        return self.robots.status_code

    @property
    def homepage_status(self) -> int | None:
        return self.homepage.status_code

    @property
    def forbidden(self) -> bool:
        """True when either probe returned 403 (e.g. Akamai block)."""
        return self.robots.status_code == 403 or self.homepage.status_code == 403

    @property
    def reachable(self) -> bool:
        """True when the homepage responded without a hard 403 block."""
        return self.homepage.status_code is not None and not self.forbidden


class Crawler:
    """Polite async crawler with retries, throttling and page limits."""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        retry_statuses: frozenset[int] = RETRYABLE_STATUSES,
        delay: float = DEFAULT_DELAY_SECONDS,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        client: httpx.AsyncClient | None = None,
        url_guard: Callable[[str], str] | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.retry_statuses = retry_statuses
        self.delay = delay
        self.max_pages = max_pages
        self.max_response_bytes = max_response_bytes
        self._url_guard: Callable[[str], str] = url_guard or check_url_safety
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._pages_fetched = 0
        self._stopped = False
        self._last_request_at: float | None = None

    async def fetch(self, url: str) -> FetchedPage:
        """Fetch one normalized page URL, applying throttling and limits.

        Counts against the page budget. Raises :class:`MaxPagesError` when
        the budget is exhausted and :class:`CrawlStoppedError` after
        :meth:`stop`.
        """
        if self._stopped:
            raise CrawlStoppedError("crawl has been stopped")
        if self._pages_fetched >= self.max_pages:
            raise MaxPagesError(f"max_pages={self.max_pages} reached; not fetching {url!r}")
        await self._throttle()
        result = await self._get_with_retries(url)
        self._pages_fetched += 1
        return FetchedPage(
            url=url,
            status_code=result.status_code,
            html=result.content,
            final_url=result.final_url,
            content_type=result.content_type,
            truncated=result.truncated,
        )

    async def fetch_resource(self, url: str) -> FetchResult:
        """Fetch a non-page resource (sitemap, robots) without page accounting.

        Throttled and retried like pages but does not consume the page
        budget and does not enforce content-type expectations.
        """
        if self._stopped:
            raise CrawlStoppedError("crawl has been stopped")
        await self._throttle()
        return await self._get_with_retries(url)

    async def crawl(self, urls: Iterable[str]) -> AsyncIterator[FetchedPage]:
        """Yield fetched pages for *urls*, stopping early on limits."""
        for url in urls:
            if self._stopped or self._pages_fetched >= self.max_pages:
                return
            try:
                yield await self.fetch(url)
            except MaxPagesError:
                return
            except CrawlStoppedError:
                return

    async def probe(self, base_url: str) -> ProbeResult:
        """Check ``robots.txt`` and the homepage before crawling.

        Captures status, effective URL and content type for each. A 403 on
        either URL indicates a hard block; the caller should abort with a
        clear error instead of starting a retry storm.
        """
        robots = await self._probe_url(urljoin(base_url, "/robots.txt"))
        homepage = await self._probe_url(base_url)
        return ProbeResult(robots=robots, homepage=homepage)

    def stop(self) -> None:
        """Request a graceful stop after the in-flight request completes."""
        self._stopped = True

    async def aclose(self) -> None:
        """Close the underlying client when this crawler owns it."""
        if self._owns_client:
            await self._client.aclose()

    @property
    def pages_fetched(self) -> int:
        return self._pages_fetched

    async def _get_with_retries(self, url: str) -> FetchResult:
        last_error: httpx.HTTPError | None = None
        for attempt in range(self.retries + 1):
            if self._stopped:
                raise CrawlStoppedError("crawl has been stopped")
            try:
                result = await self._stream_once(url)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.retries:
                    await self._backoff(attempt)
                    continue
                break
            if result.status_code in self.retry_statuses and attempt < self.retries:
                await self._backoff(attempt)
                continue
            return result
        raise FetchError(
            f"failed to fetch {url!r} after {self.retries} retries"
            + (f": {last_error!r}" if last_error is not None else "")
        ) from last_error

    async def _stream_once(self, url: str, *, body_limit: int | None = None) -> FetchResult:
        """GET *url*, following redirects manually with a safety check per hop.

        The guard runs against the request URL *and* every redirect destination
        before any request is sent, so an open redirect on an otherwise public
        site cannot move the crawler onto a private address.
        """
        current = self._url_guard(url)
        for hop in range(MAX_REDIRECTS + 1):
            response = await self._send_no_redirect(current)
            if response.status_code not in REDIRECT_STATUSES:
                return await self._finish(response, body_limit)
            location = response.headers.get("location")
            if not location:
                # Malformed redirect: report the response itself instead of failing.
                return await self._finish(response, body_limit)
            await response.aclose()
            if hop >= MAX_REDIRECTS:
                raise FetchError(f"too many redirects fetching {url!r}")
            current = self._url_guard(urljoin(current, location))
        raise FetchError(f"too many redirects fetching {url!r}")

    async def _finish(
        self, response: httpx.Response, body_limit: int | None
    ) -> FetchResult:
        """Read a non-redirect response body (bounded) and close the response."""
        try:
            content, truncated = await self._read_body_limited(response, body_limit)
        finally:
            await response.aclose()
        return FetchResult(
            status_code=response.status_code,
            content=content,
            final_url=str(response.url),
            content_type=response.headers.get("content-type"),
            truncated=truncated,
        )

    async def _send_no_redirect(self, url: str) -> httpx.Response:
        """Send one streamed GET request without following redirects."""
        request = self._client.build_request("GET", url)
        return await self._client.send(request, stream=True, follow_redirects=False)

    async def _read_body_limited(
        self, response: httpx.Response, body_limit: int | None = None
    ) -> tuple[bytes, bool]:
        limit = body_limit or self.max_response_bytes
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                return b"".join(chunks), True
            chunks.append(chunk)
        return b"".join(chunks), False

    async def _probe_url(self, url: str) -> ProbeOutcome:
        try:
            result = await self._stream_once(url, body_limit=PROBE_BODY_LIMIT)
        except httpx.HTTPError:
            return ProbeOutcome(url=url, status_code=None, final_url=None, content_type=None)
        return ProbeOutcome(
            url=url,
            status_code=result.status_code,
            final_url=result.final_url,
            content_type=result.content_type,
        )

    async def _throttle(self) -> None:
        if self._last_request_at is None:
            self._last_request_at = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_request_at
        wait = self.delay - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_at = time.monotonic()

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(self.delay * (2**attempt))
