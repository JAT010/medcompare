"""Deterministic URL normalization for hospital facility canonical URLs."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

from hospital_extractor.persistence.exceptions import InvalidFacilityUrlError

__all__ = ["NormalizedUrl", "normalize_canonical_url", "extract_website_domain"]

_ALLOWED_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class NormalizedUrl:
    """Normalized canonical URL and derived domain."""

    original: str
    normalized: str
    domain: str


def _idna_host(host: str) -> str:
    host = host.strip().lower().rstrip(".")
    if not host:
        raise InvalidFacilityUrlError("URL host is required")
    try:
        # Normalize internationalized domain names consistently.
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise InvalidFacilityUrlError(f"Invalid URL host: {host!r}") from exc


def normalize_canonical_url(url: str) -> NormalizedUrl:
    """Normalize an HTTP(S) canonical URL deterministically.

    - No network requests and no redirect following.
    - Accepts only http/https.
    - Normalizes scheme/hostname case and IDN.
    - Removes default ports and fragments.
    - Normalizes path to a stable form for identity/deduplication:
      root is ``/``; non-root paths have trailing slash removed.
    - Preserves meaningful query parameters and their order; does not remove
      arbitrary query parameters.
    """
    if url is None:
        raise InvalidFacilityUrlError("canonical_url must be a string")
    raw = url.strip()
    if not raw:
        raise InvalidFacilityUrlError("canonical_url must not be empty")

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise InvalidFacilityUrlError(
            f"Only http/https URLs are allowed, got scheme {parts.scheme!r}"
        )
    if not parts.hostname:
        raise InvalidFacilityUrlError("URL hostname is required")

    host = _idna_host(parts.hostname)
    port = parts.port
    if port is not None and port == _DEFAULT_PORTS.get(scheme):
        port = None
    netloc = host if port is None else f"{host}:{port}"

    path = parts.path or "/"
    # percent-encode path consistently without decoding meaning; keep safe chars
    path = quote(path, safe="/%:@!$&'()*+,;=-._~")
    if path != "/":
        path = "/" + path.lstrip("/")
        while "//" in path:
            path = path.replace("//", "/")
        # Stable facility identity: strip trailing slash for non-root paths.
        path = path.rstrip("/") or "/"
    else:
        path = "/"

    # Preserve query exactly as parsed text order/content (no drops/reordering).
    query = parts.query

    normalized = urlunsplit((scheme, netloc, path, query, ""))
    return NormalizedUrl(original=url, normalized=normalized, domain=host)


def extract_website_domain(url: str) -> str:
    """Return normalized hostname for an HTTP(S) URL."""
    return normalize_canonical_url(url).domain
