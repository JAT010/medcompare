"""URL utilities: validation, normalization, domain scoping.

The crawler and page-discovery layers depend on these helpers to keep URL
handling consistent: normalize before deduplication, reject off-domain links,
and never fetch binary assets.
"""

from __future__ import annotations

import posixpath
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

# File extensions we never want to fetch during a crawl.
SKIPPED_ASSET_EXTENSIONS = frozenset(
    {
        "jpg",
        "jpeg",
        "png",
        "gif",
        "webp",
        "svg",
        "ico",
        "bmp",
        "avif",
        "mp4",
        "webm",
        "mov",
        "avi",
        "mp3",
        "wav",
        "ogg",
        "woff",
        "woff2",
        "ttf",
        "otf",
        "eot",
        "pdf",
        "zip",
        "gz",
        "tar",
        "rar",
        "7z",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "css",
        "js",
        "webmanifest",
    }
)

_DEFAULT_PORTS: dict[str, str] = {"http": "80", "https": "443"}


class InvalidURLError(ValueError):
    """Raised when a URL is malformed, missing a host, or not http(s)."""


def normalize_url(url: str) -> str:
    """Return a canonical form of *url* suitable for deduplication.

    Applies: scheme/host lowercasing, default-port removal, fragment removal,
    dot-segment resolution, trailing-slash removal (except root) and sorted
    query parameters. Raises :class:`InvalidURLError` for unparseable URLs or
    URLs without a host.
    """
    raw = url.strip()
    if not raw:
        raise InvalidURLError("URL must not be empty")
    try:
        parts = urlsplit(raw)
    except ValueError as exc:  # pragma: no cover - depends on stdlib behaviour
        raise InvalidURLError(f"Malformed URL: {url!r}") from exc

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidURLError(f"URL has no host: {url!r}")

    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None and str(port) == _DEFAULT_PORTS.get(scheme):
        port = None
    # IPv6 literals must stay bracketed in the authority component, otherwise
    # the host is re-parsed as everything before the first colon (e.g.
    # "fd00::1" would be reinterpreted as host "fd00"), which also defeats
    # address-based checks downstream.
    authority_host = f"[{host}]" if ":" in host else host
    netloc = authority_host if port is None else f"{authority_host}:{port}"

    path = parts.path or "/"
    path = _clean_path(path)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def _clean_path(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    if path != "/":
        path = path.rstrip("/")
    normalized = posixpath.normpath(path)
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized or "/"


def validate_url(url: str) -> str:
    """Validate *url* as user input and return its normalized http(s) form.

    This is the single entry point for the CLI-supplied hospital website URL.
    """
    normalized = normalize_url(url)
    if not normalized.startswith(("http://", "https://")):
        raise InvalidURLError(f"Only http(s) URLs are supported: {url!r}")
    return normalized


def resolve_url(base_url: str, href: str) -> str | None:
    """Resolve *href* against *base_url* and normalize.

    Returns ``None`` for empty, fragment-only, ``javascript:``, ``mailto:``
    and ``tel:`` hrefs, or when the joined URL cannot be normalized.
    """
    if not href:
        return None
    stripped = href.strip()
    if not stripped or stripped.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    try:
        joined = urljoin(base_url, stripped)
        return normalize_url(joined)
    except ValueError:
        return None


def is_same_domain(url: str, base_url: str, *, allow_subdomains: bool = False) -> bool:
    """Return True if *url* belongs to the same domain as *base_url*.

    ``www.`` prefixes are treated as equivalent. Sub-domains are only accepted
    when ``allow_subdomains`` is True.
    """
    url_host = (urlsplit(url).hostname or "").lower().rstrip(".")
    base_host = (urlsplit(base_url).hostname or "").lower().rstrip(".")
    if not url_host or not base_host:
        return False
    if url_host == base_host:
        return True
    if url_host.removeprefix("www.") == base_host.removeprefix("www."):
        return True
    if allow_subdomains and url_host.endswith("." + base_host):
        return True
    return False


def is_fetchable(url: str) -> bool:
    """Return True if *url* points at HTML-like content we should fetch.

    Non-http(s) schemes and known binary/asset extensions are excluded.
    Extension-less URLs (the common CMS pattern) are fetchable.
    """
    try:
        parts = urlsplit(normalize_url(url))
    except InvalidURLError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    path = parts.path
    if not path or path == "/":
        return True
    if "." not in path.rsplit("/", 1)[-1]:
        return True
    ext = path.rsplit(".", 1)[-1].lower()
    return ext not in SKIPPED_ASSET_EXTENSIONS


def is_same_page(url_a: str, url_b: str) -> bool:
    """Return True if two normalized URLs identify the same page.

    Query parameters are ignored because CMS-generated pages often append
    tracking parameters (``?utm_*``, ``?page=``) to identical content.
    """
    try:
        a = urlsplit(normalize_url(url_a))
        b = urlsplit(normalize_url(url_b))
    except InvalidURLError:
        return False
    return (a.scheme, a.netloc, a.path) == (b.scheme, b.netloc, b.path)
