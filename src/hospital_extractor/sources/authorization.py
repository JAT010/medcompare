"""Authorization boundary and SSRF guards for authorized connectors (P21).

An API or remote-feed connector must NOT contact an external source merely
because a URL exists.  Every outbound request is gated by an explicit
:class:`AuthorizationPolicy` built from configuration — fail closed, no
request when authorization is missing or the target is not allowlisted.

Credentials are never stored here: only environment-variable *references*
(:class:`CredentialRef`).  The secret itself is resolved at request time and
never appears in results, logs, exceptions, or the database.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "ALLOWED_SCHEMES_DEFAULT",
    "AuthorizationDecision",
    "AuthorizationPolicy",
    "CredentialRef",
    "SsrfError",
    "assert_safe_url",
    "resolve_credential",
]


ALLOWED_SCHEMES_DEFAULT: tuple[str, ...] = ("https", "http")


class SsrfError(ValueError):
    """The target URL is not permitted by the SSRF / network-safety policy."""


@dataclass(frozen=True)
class CredentialRef:
    """A pointer to a secret held in the environment — never the secret itself.

    ``env_var`` is the environment variable name (e.g.
    ``MEDCOMPARE_CONNECTOR_EXAMPLE_TOKEN``).  ``resolve()`` reads the value at
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
    - ``requires_authorization`` on the SourceDefinition means a policy MUST
      be present; a missing policy is treated as unauthorized.
    """

    authorized: bool = False
    allowed_hosts: tuple[str, ...] = ()
    allowed_schemes: tuple[str, ...] = ALLOWED_SCHEMES_DEFAULT
    allow_private_hosts: bool = False
    credential_ref: CredentialRef | None = None
    credential_required: bool = False
    max_redirects: int = 3
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
        max_redirects: int = 3,
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
            max_redirects=max_redirects,
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
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in self.allowed_schemes:
            return AuthorizationDecision.deny(
                f"scheme {scheme!r} not in allowed schemes {list(self.allowed_schemes)}",
                code="ssrf",
            )
        host = (parsed.hostname or "").lower()
        if not host:
            return AuthorizationDecision.deny("URL has no host", code="ssrf")
        if self.allowed_hosts:
            if not _host_allowed(host, self.allowed_hosts):
                return AuthorizationDecision.deny(
                    f"host {host!r} is not in the allowlist",
                    code="ssrf",
                )
        elif not self.allow_private_hosts:
            # No allowlist configured: still refuse private/loopback targets
            # unless explicitly permitted (e.g. local mock services).
            if _is_private_host(host):
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

    def check(self, url: str) -> AuthorizationDecision:
        """Alias of :meth:`check_url` (readability at call sites)."""
        return self.check_url(url)

    def require(self, url: str) -> None:
        """Raise :class:`SsrfError` (or a generic ``ValueError``) when denied.

        Callers that need typed connector errors translate the decision
        themselves; this helper is for low-level guards.
        """
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
        # Not a literal IP — do not perform DNS here (deterministic, no
        # network side effects during the policy check).  Hostname allowlists
        # remain the primary control for named hosts.
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def assert_safe_url(url: str, policy: AuthorizationPolicy | None = None) -> None:
    """Raise :class:`SsrfError` when *url* fails the default or *policy* guard."""
    active = policy or AuthorizationPolicy(authorized=True, allow_private_hosts=False)
    decision = active.check_url(url)
    if not decision.allowed:
        raise SsrfError(decision.reason)


def resolve_host_address(host: str) -> list[str]:
    """Resolve *host* to IP literals (used by optional deep SSRF checks).

    Not called on the hot path of :meth:`AuthorizationPolicy.check_url` so
    policy evaluation stays offline and deterministic; connectors may call it
    before opening a socket when a stricter network boundary is desired.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    addresses: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in addresses:
            addresses.append(addr)
    return addresses


@dataclass(frozen=True)
class RequestBudget:
    """Bounded network budget shared by authorized connectors."""

    timeout_seconds: float = 30.0
    max_response_bytes: int = 5_000_000
    max_redirects: int = 3
    allowed_schemes: tuple[str, ...] = field(default=ALLOWED_SCHEMES_DEFAULT)
