"""SSRF protections for user-supplied URLs and redirect destinations.

Two levels of checking are provided so callers can choose how strict to be:

- :func:`check_url_safety` performs structural checks only (scheme, embedded
  credentials, blocked host names and literal IP addresses). It performs no
  DNS lookups, so it is safe to apply to every hop of a crawl in environments
  where the target host may not resolve (tests, offline tooling).
- :func:`validate_public_url` adds DNS resolution and rejects hosts whose
  addresses are not globally routable. It is the check applied to URLs that a
  user typed into the application before any request leaves the process.

Both reject loopback, private, link-local, shared, reserved, multicast and
unspecified addresses (which is what covers cloud-instance metadata endpoints
such as ``169.254.169.254``), and both are re-applied to every redirect
destination so a public host cannot redirect the crawler into an internal
network.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit

from hospital_extractor.utils.urls import InvalidURLError, validate_url

__all__ = [
    "BLOCKED_HOSTNAMES",
    "BLOCKED_HOSTNAME_SUFFIXES",
    "SSRFBlockedError",
    "check_url_safety",
    "default_resolver",
    "is_blocked_hostname",
    "is_blocked_ip",
    "validate_public_url",
]


class SSRFBlockedError(InvalidURLError):
    """Raised when a URL targets a non-public or otherwise unsafe destination."""


# Host names that never point at a public hospital website.
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "instance-data",
        "metadata.google.internal",
        "metadata.goog",
    }
)

# Suffix-based blocks cover "db.local", "service.internal" and friends.
BLOCKED_HOSTNAME_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".corp",
    ".home.arpa",
)


Resolver = Callable[[str], Sequence[str]]


def is_blocked_ip(value: str) -> bool:
    """True when *value* is not a globally routable address (or is invalid).

    IPv4-mapped IPv6 addresses (``::ffff:127.0.0.1``) are unwrapped first so
    they cannot be used to smuggle a loopback address past the check.
    """
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(value)
    except ValueError:
        return True
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return bool(
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def is_blocked_hostname(host: str) -> bool:
    """True when *host* is a name that must never be fetched."""
    normalized = (host or "").strip().lower().rstrip(".")
    if not normalized:
        return True
    if normalized in BLOCKED_HOSTNAMES:
        return True
    return any(normalized.endswith(suffix) for suffix in BLOCKED_HOSTNAME_SUFFIXES)


def check_url_safety(url: str) -> str:
    """Structurally validate *url* and return its normalized http(s) form.

    Rejects non-http(s) schemes, embedded credentials, loopback/private/
    link-local hosts by name, and literal IP addresses that are not publicly
    routable. No DNS resolution is performed.
    """
    raw = (url or "").strip()
    parts = urlsplit(raw)
    if parts.username is not None or parts.password is not None:
        raise SSRFBlockedError(f"URLs with embedded credentials are not accepted: {url!r}")

    normalized = validate_url(raw)
    host = urlsplit(normalized).hostname or ""
    if is_blocked_hostname(host):
        raise SSRFBlockedError(f"URL host {host!r} is not a public target: {url!r}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if is_blocked_ip(host):
            raise SSRFBlockedError(
                f"URL host {host!r} is a non-public address: {url!r}"
            )
    return normalized


def default_resolver(host: str) -> Sequence[str]:
    """Resolve *host* to IP address strings using the system resolver."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            addresses.append(str(sockaddr[0]))
    return addresses


def validate_public_url(
    url: str,
    *,
    resolver: Resolver | None = None,
) -> str:
    """Validate *url* for a user-supplied target and return its normalized form.

    Performs every :func:`check_url_safety` check and additionally resolves the
    host, rejecting it when any resolved address is non-public. *resolver* is
    injectable so tests never depend on live DNS.
    """
    normalized = check_url_safety(url)
    host = urlsplit(normalized).hostname or ""
    resolve: Resolver = resolver or default_resolver
    try:
        addresses = list(resolve(host))
    except OSError as exc:
        raise SSRFBlockedError(f"could not resolve host {host!r}: {exc}") from exc
    if not addresses:
        raise SSRFBlockedError(f"host {host!r} did not resolve to any address")
    for address in addresses:
        if is_blocked_ip(address):
            raise SSRFBlockedError(
                f"host {host!r} resolves to non-public address {address}"
            )
    return normalized
