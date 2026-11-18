"""Authorization boundary and SSRF guards for authorized connectors.

An API or remote-feed connector must NOT contact an external source merely
because a URL exists. Every outbound request is gated by an explicit
:class:`AuthorizationPolicy` built from configuration — fail closed, no
request when authorization is missing or the target is not allowlisted.

Credentials are never stored here: only environment-variable *references*
(:class:`CredentialRef`). The secret itself is resolved at request time and
never appears in results, logs, exceptions, or the database.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "ALLOWED_SCHEMES_DEFAULT",
    "AuthorizationDecision",
    "AuthorizationPolicy",
    "CredentialRef",
    "DestinationCheck",
    "Resolver",
    "SsrfError",
    "check_destination",
    "resolve_credential",
]

ALLOWED_SCHEMES_DEFAULT: tuple[str, ...] = ("https", "http")

Resolver = Callable[[str], list[str]]


def _ip_networks(*cidrs: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for cidr in cidrs:
        networks.append(ipaddress.ip_network(cidr))
    return tuple(networks)


# Ranges that are never valid connector destinations, even for an allowlisted
# host or an allow_private_hosts=True policy (metadata services, shared
# address space, documentation ranges, multicast/reserved).
ALWAYS_BLOCKED_CIDRS = _ip_networks(
    "0.0.0.0/8",
    "100.64.0.0/10",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "169.254.0.0/16",
    "::/128",
    "::1/128",
    "::ffff:0.0.0.0/96",
    "fe80::/10",
    "ff00::/8",
)

# Exempt from the not-globally-routable check only when the operator explicitly
# sets allow_private_hosts (e.g. a hospital on an internal network).
PRIVATE_EXEMPT_CIDRS = _ip_networks(
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
)


class SsrfError(ValueError):
    """The target URL is not permitted by the SSRF / network-safety policy."""


@dataclass(frozen=True)
class CredentialRef:
    """A pointer to a secret held in the environment — never the secret itself.

    ``env_var`` is the environment variable name (e.g.
    ``MEDCOMPARE_HOSPITAL_API_EXAMPLE_TOKEN``). ``resolve()`` reads the value at
    request time; the ref itself is safe to log, store, and serialize.
    """

    env_var: str

    def resolve(self) -> str | None:
        """Return the secret from the environment, or ``None`` when unset."""
        value = os.environ.get(self.env_var)
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def __repr__(self) -> str:  # never leak a resolved secret through repr
        return f"CredentialRef(env_var={self.env_var!r})"

    def describe(self) -> str:
        """Human-safe description (env var name only)."""
        return f"env:{self.env_var}"


def resolve_credential(ref: CredentialRef | None) -> str | None:
    """Resolve *ref* to a secret value, or ``None`` when no ref is configured."""
    if ref is None:
        return None
    return ref.resolve()


@dataclass(frozen=True)
class AuthorizationDecision:
    """Outcome of an authorization / SSRF check."""

    allowed: bool
    reason: str = ""
    code: str = ""  # "unauthorized" | "ssrf" | "invalid_configuration"

    @classmethod
    def allow(cls) -> AuthorizationDecision:
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason: str, *, code: str = "unauthorized") -> AuthorizationDecision:
        return cls(allowed=False, reason=reason, code=code)


@dataclass(frozen=True)
class AuthorizationPolicy:
    """Explicit authorization configuration for one source / connector.

    Fail-closed contract:

    - ``authorized=False`` → always deny (no external request).
    - ``authorized=True`` → allow only when the URL scheme/host passes the
      allowlist and private-network rules.
    - A missing/absent policy means the source is unauthorized.
    """

    authorized: bool = False
    allowed_hosts: tuple[str, ...] = ()
    allowed_schemes: tuple[str, ...] = ALLOWED_SCHEMES_DEFAULT
    allow_private_hosts: bool = False
    credential_ref: CredentialRef | None = None
    credential_required: bool = False
    max_redirects: int = 0
    timeout_seconds: float = 30.0
    max_response_bytes: int = 5_000_000
    provider: str = ""

    @classmethod
    def deny_all(cls, reason: str = "source is not authorized") -> AuthorizationPolicy:
        return cls(authorized=False, provider=reason)

    @classmethod
    def from_config(
        cls,
        *,
        authorized: bool,
        allowed_hosts: tuple[str, ...] | list[str] = (),
        allowed_schemes: tuple[str, ...] | list[str] | None = None,
        allow_private_hosts: bool = False,
        credential_env: str | None = None,
        credential_required: bool = False,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 5_000_000,
        provider: str = "",
    ) -> AuthorizationPolicy:
        """Build a policy from configuration fields (never from secrets)."""
        schemes = tuple(allowed_schemes) if allowed_schemes else ALLOWED_SCHEMES_DEFAULT
        return cls(
            authorized=authorized,
            allowed_hosts=tuple(allowed_hosts),
            allowed_schemes=schemes,
            allow_private_hosts=allow_private_hosts,
            credential_ref=CredentialRef(credential_env) if credential_env else None,
            credential_required=credential_required,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            provider=provider,
        )

    @property
    def status(self) -> str:
        """UI/CLI authorization status label."""
        if not self.authorized:
            return "UNAUTHORIZED"
        return "AUTHORIZED"

    def check_url(self, url: str) -> AuthorizationDecision:
        """Decide whether *url* may be requested under this policy."""
        if not self.authorized:
            return AuthorizationDecision.deny(
                "source is not authorized (fail closed; no external request)"
            )
        parsed = urlsplit(url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in self.allowed_schemes:
            return AuthorizationDecision.deny(
                f"scheme {scheme!r} not in allowed schemes {list(self.allowed_schemes)}",
                code="ssrf",
            )
        if parsed.username is not None or parsed.password is not None:
            return AuthorizationDecision.deny(
                "URLs with embedded credentials are not accepted",
                code="ssrf",
            )
        host = (parsed.hostname or "").lower()
        if not host:
            return AuthorizationDecision.deny("URL has no host", code="ssrf")
        if _is_always_blocked_host(host):
            return AuthorizationDecision.deny(
                f"host {host!r} is always blocked (link-local/metadata/multicast/unspecified)",
                code="ssrf",
            )
        if self.allowed_hosts:
            if not _host_allowed(host, self.allowed_hosts):
                return AuthorizationDecision.deny(
                    f"host {host!r} is not in the allowlist",
                    code="ssrf",
                )
        elif not self.allow_private_hosts and _is_private_host(host):
            return AuthorizationDecision.deny(
                f"host {host!r} resolves to a private/loopback address",
                code="ssrf",
            )
        if not self.allow_private_hosts and _is_private_host(host):
            return AuthorizationDecision.deny(
                f"host {host!r} resolves to a private/loopback address",
                code="ssrf",
            )
        if self.credential_required and self.credential_ref is None:
            return AuthorizationDecision.deny(
                "credential_required but no credential reference configured",
                code="invalid_configuration",
            )
        if self.credential_ref is not None and self.credential_ref.resolve() is None:
            return AuthorizationDecision.deny(
                f"credential environment variable {self.credential_ref.env_var!r} is not set",
                code="invalid_configuration",
            )
        return AuthorizationDecision.allow()

    def require(self, url: str) -> None:
        """Raise :class:`SsrfError` when *url* is not permitted."""
        decision = self.check_url(url)
        if not decision.allowed:
            raise SsrfError(decision.reason)

    def safe_dict(self) -> dict[str, Any]:
        """Serializable view for API/CLI — credential reference name only."""
        return {
            "authorized": self.authorized,
            "status": self.status,
            "allowed_hosts": list(self.allowed_hosts),
            "allowed_schemes": list(self.allowed_schemes),
            "allow_private_hosts": self.allow_private_hosts,
            "credential_ref": self.credential_ref.describe() if self.credential_ref else None,
            "credential_required": self.credential_required,
            "timeout_seconds": self.timeout_seconds,
            "max_response_bytes": self.max_response_bytes,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class RequestBudget:
    """Bounded network budget shared by authorized connectors."""

    timeout_seconds: float = 30.0
    max_response_bytes: int = 5_000_000
    max_redirects: int = 0
    allowed_schemes: tuple[str, ...] = field(default=ALLOWED_SCHEMES_DEFAULT)


def _unwrap(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Unwrap IPv4-mapped IPv6 addresses (``::ffff:127.0.0.1``)."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def classify_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_private_hosts: bool,
) -> tuple[bool, str]:
    """Classify *address* for connector destinations.

    Returns ``(allowed, reason)``. ``reason`` is empty when allowed.
    """
    address = _unwrap(address)
    for network in ALWAYS_BLOCKED_CIDRS:
        if address in network:
            return False, (
                f"address {address} is in a range that is never allowed ({network.with_prefixlen})"
            )
    if allow_private_hosts and any(address in network for network in PRIVATE_EXEMPT_CIDRS):
        return True, ""
    if not address.is_global:
        return False, (
            f"address {address} is not globally routable "
            "(set allow_private_hosts only for explicitly private hospital networks)"
        )
    return True, ""


def _is_always_blocked_host(host: str) -> bool:
    """True for literal hosts in always-blocked ranges (metadata/link-local/reserved)."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(_unwrap(address) in network for network in ALWAYS_BLOCKED_CIDRS)


def _private_literal(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    address = _unwrap(address)
    return any(address in network for network in PRIVATE_EXEMPT_CIDRS)


def _host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    for pattern in allowed:
        pattern_l = pattern.lower().rstrip(".")
        if pattern_l.startswith("*."):
            suffix = pattern_l[1:]  # ".example.in"
            if host.endswith(suffix) and host != suffix.lstrip("."):
                return True
        elif host == pattern_l:
            return True
    return False


def _is_private_host(host: str) -> bool:
    """True when *host* is a literal private/loopback address or ``localhost``."""
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    address = _unwrap(address)
    return (
        not address.is_global
        or address.is_loopback
        or _private_literal(address)
        or _is_always_blocked_host(host)
    )


@dataclass(frozen=True)
class DestinationCheck:
    """Outcome of validating the addresses a URL will actually connect to."""

    allowed: bool
    reason: str = ""
    addresses: tuple[str, ...] = ()


def check_destination(
    url: str,
    *,
    resolver: Resolver | None = None,
    allow_private_hosts: bool = False,
) -> DestinationCheck:
    """Validate the concrete addresses behind *url*.

    Named hosts are resolved (default ``socket.getaddrinfo``; injectable for
    tests) and every resolved address is classified with :func:`classify_address`.
    Fails closed when the host cannot be resolved.
    """
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return DestinationCheck(allowed=False, reason="URL has no host")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        resolve = resolver or _default_resolver
        try:
            raw = list(resolve(host))
        except OSError as exc:
            return DestinationCheck(
                allowed=False,
                reason=f"could not resolve host {host!r}: {exc}",
            )
        if not raw:
            return DestinationCheck(
                allowed=False,
                reason=f"host {host!r} did not resolve to any address",
            )
        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for item in raw:
            try:
                addresses.append(ipaddress.ip_address(item))
            except ValueError:
                return DestinationCheck(
                    allowed=False,
                    reason=f"resolver returned an invalid address for {host!r}: {item!r}",
                )
    else:
        addresses = [_unwrap(address)]

    for address in addresses:
        ok, reason = classify_address(address, allow_private_hosts=allow_private_hosts)
        if not ok:
            return DestinationCheck(allowed=False, reason=reason)
    return DestinationCheck(
        allowed=True,
        addresses=tuple(str(address) for address in addresses),
    )


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    addresses: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in addresses:
            addresses.append(addr)
    return addresses
