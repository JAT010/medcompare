"""Backend-authoritative candidate authorization boundary.

The recommendation service never invents permissions. It receives an
``AuthorizationScope`` from an injected ``HospitalAuthorizer`` that the real
backend must implement using its existing permission/grant checks. The default
authorizer denies everything, so forgetting to wire the real check fails closed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AuthorizationScope:
    """Hospitals the authenticated user is allowed to see for this request."""

    user_id: str
    authorized_hospital_ids: frozenset[str]

    def allows(self, hospital_id: str) -> bool:
        return hospital_id in self.authorized_hospital_ids

    @classmethod
    def deny_all(cls, user_id: str) -> AuthorizationScope:
        return cls(user_id=user_id, authorized_hospital_ids=frozenset())

    @classmethod
    def from_ids(cls, user_id: str, hospital_ids: Iterable[str]) -> AuthorizationScope:
        return cls(user_id=user_id, authorized_hospital_ids=frozenset(hospital_ids))


class HospitalAuthorizer(Protocol):
    """Protocol implemented by the existing backend permission layer."""

    def scope_for(self, user_id: str) -> AuthorizationScope:
        """Return the authoritative scope for *user_id*."""
        ...


class DenyAllAuthorizer:
    """Fail-closed default used when no backend authorizer is wired."""

    def scope_for(self, user_id: str) -> AuthorizationScope:
        return AuthorizationScope.deny_all(user_id)


class StaticAuthorizer:
    """Test/dev double only; never a production permission source."""

    def __init__(self, scopes: Mapping[str, Iterable[str]]) -> None:
        self._scopes: dict[str, frozenset[str]] = {
            user_id: frozenset(hospital_ids) for user_id, hospital_ids in scopes.items()
        }

    def scope_for(self, user_id: str) -> AuthorizationScope:
        return AuthorizationScope.from_ids(user_id, self._scopes.get(user_id, frozenset()))
