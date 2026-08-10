"""Polite asynchronous fetcher: retries, throttling, limits, graceful stop.

Uses ``httpx`` with an injectable client so tests can supply a
``MockTransport``. Defaults are chosen to be safe on production hospital
sites: a descriptive user-agent, bounded retries with exponential backoff,
a minimum delay between requests and a hard page cap.

A 403 response is treated as a hard stop signal (the recon report observed
Akamai returning inconsistent 403s); it is never retried.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

DEFAULT_USER_AGENT = (
    "HospitalExtractor/0.1 (+https://github.com/kartik00052/hospital-website-extractor)"
)
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRIES = 3
DEFAULT_DELAY_SECONDS = 0.5
DEFAULT_MAX_PAGES = 200

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

__all__ = [
    "CrawlError",
    "CrawlStoppedError",
    "Crawler",
    "FetchedPage",
    "FetchError",
    "MaxPagesError",
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
    """One successfully fetched page.

    ``url`` is the normalized request URL, ``final_url`` the post-redirect
    URL, and ``html`` the raw response body bytes.
    """

    url: str
    status_code: int
    html: bytes
    final_url: str


@dataclass(frozen=True)
class ProbeResult:
    """Reachability probe of a hospital website before committing to a crawl."""

    robots_status: int | None
    homepage_status: int | None

    @property
    def forbidden(self) -> bool:
        """True when either probe returned 403 (e.g. Akamai block)."""
        return self.robots_status == 403 or self.homepage_status == 403

    @property
    def reachable(self) -> bool:
        """True when the homepage responded without a hard 403 block."""
        return self.homepage_status is not None and not self.forbidden


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
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.retry_statuses = retry_statuses
        self.delay = delay
        self.max_pages = max_pages
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
        )
        self._owns_client = client is None
        self._pages_fetched = 0
        self._stopped = False
        self._last_request_at: float | None = None

    async def fetch(self, url: str) -> FetchedPage:
        """Fetch one normalized URL, applying throttling and limits."""
        if self._stopped:
            raise CrawlStoppedError("crawl has been stopped")
        if self._pages_fetched >= self.max_pages:
            raise MaxPagesError(f"max_pages={self.max_pages} reached; not fetching {url!r}")
        await self._throttle()
        response = await self._get_with_retries(url)
        self._pages_fetched += 1
        return FetchedPage(
            url=url,
            status_code=response.status_code,
            html=response.content,
            final_url=str(response.url),
        )

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

        A 403 on either URL indicates a hard block; the caller should abort
        with a clear error instead of starting a retry storm.
        """
        robots_status = await self._status(urljoin(base_url, "/robots.txt"))
        homepage_status = await self._status(base_url)
        return ProbeResult(
            robots_status=robots_status,
            homepage_status=homepage_status,
        )

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

    async def _get_with_retries(self, url: str) -> httpx.Response:
        last_error: httpx.HTTPError | None = None
        for attempt in range(self.retries + 1):
            if self._stopped:
                raise CrawlStoppedError("crawl has been stopped")
            try:
                response = await self._client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.retries:
                    await self._backoff(attempt)
                    continue
                break
            if response.status_code in self.retry_statuses and attempt < self.retries:
                await self._backoff(attempt)
                continue
            return response
        raise FetchError(
            f"failed to fetch {url!r} after {self.retries} retries"
            + (f": {last_error!r}" if last_error is not None else "")
        ) from last_error

    async def _status(self, url: str) -> int | None:
        try:
            response = await self._client.get(url)
        except httpx.HTTPError:
            return None
        return response.status_code

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
