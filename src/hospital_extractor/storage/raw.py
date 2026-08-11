"""Raw HTML cache on disk.

Every fetched page is stored under ``data/raw/<run>/<hash>.html`` where
``<hash>`` is a stable short digest of the normalized URL. Storing by URL
hash makes re-runs idempotent: a page already cached for the run is not
refetched.
"""

from __future__ import annotations

from pathlib import Path

from hospital_extractor.crawler.crawler import FetchedPage
from hospital_extractor.utils.hashing import short_hash
from hospital_extractor.utils.urls import InvalidURLError, normalize_url

__all__ = ["RawStore"]


class RawStore:
    """Persists raw HTML pages under ``root/<run>/<hash>.html``."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path_for(self, run_id: str, url: str) -> Path:
        """Return the cache file path for *url* under *run_id*."""
        return self.root / run_id / f"{self._url_key(url)}.html"

    def exists(self, run_id: str, url: str) -> bool:
        """Return True when the page is already cached for the run."""
        return self.path_for(run_id, url).is_file()

    def load(self, run_id: str, url: str) -> FetchedPage:
        """Read a cached page back as a :class:`FetchedPage`.

        Only HTML pages are stored, so the cached content is treated as
        ``text/html`` with a 200 status.
        """
        return FetchedPage(
            url=url,
            status_code=200,
            html=self.path_for(run_id, url).read_bytes(),
            final_url=url,
            content_type="text/html",
        )

    def save(self, run_id: str, page: FetchedPage) -> Path:
        """Write the raw HTML body of *page* and return the file path."""
        path = self.path_for(run_id, page.url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(page.html)
        return path

    def _url_key(self, url: str) -> str:
        try:
            normalized = normalize_url(url)
        except InvalidURLError:
            normalized = url
        return short_hash(normalized, length=16)
