"""Harvest canonical datasets through registered source adapters.

This is the thin application-level gateway between "which sources are
configured" and "give me canonical :class:`HospitalDataset` objects to
compare/store/profile".  Adapters, registry and mapping live here too so the
web app, CLI and future services never import crawler internals directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.sources.registry import (
    SourceConfig,
    SourceRegistry,
    get_default_registry,
)

__all__ = ["HarvestError", "Harvested", "harvest", "run_harvest"]

# SourceConfig-level fields that must not land in ``extra`` (they drive the
# fail-closed authorization policy built by ``SourceRegistry.build``).
_CONFIG_FIELDS: tuple[str, ...] = (
    "name",
    "base_url",
    "endpoint",
    "provider",
    "hospital_id",
    "authorized",
    "allowed_hosts",
    "allowed_schemes",
    "allow_private_hosts",
    "credential_env",
    "credential_required",
    "timeout_seconds",
    "max_response_bytes",
)


@dataclass(frozen=True)
class Harvested:
    """A canonical dataset plus the reference that produced it."""

    reference: str
    dataset: HospitalDataset


class HarvestError(RuntimeError):
    """At least one configured source failed to produce a dataset."""


async def harvest(
    refs: list[str],
    *,
    source_type: str = "website",
    registry: SourceRegistry | None = None,
    **factory_kwargs: Any,
) -> list[Harvested]:
    """Fetch a canonical dataset for each *ref* through the selected adapter.

    ``source_type`` chooses the registered adapter (``website``,
    ``mock_api``, ``hospital_api``, ``json_feed``).  The same configuration
    kwargs are forwarded to every built source, so refs in *refs* must share a
    source vocabulary (all website URLs, or all demo hospital ids, ...).

    Raises :class:`HarvestError` if any source fails, aggregating the
    per-source message.
    """
    registry = registry or get_default_registry()
    configs = [
        SourceConfig(
            source_type=source_type,
            hospital_ref=ref,
            **_promote_config_fields(factory_kwargs),
        )
        for ref in refs
    ]
    sources = [registry.build(config) for config in configs]
    gathered = await asyncio.gather(
        *(source.fetch() for source in sources),
        return_exceptions=True,
    )
    harvested: list[Harvested] = []
    for ref, _source, outcome in zip(refs, sources, gathered, strict=True):
        if isinstance(outcome, BaseException):
            raise HarvestError(f"{source_type}:{ref} failed: {outcome}") from outcome
        if not isinstance(outcome, HospitalDataset):
            raise HarvestError(f"{source_type}:{ref} yielded a non-dataset result")
        harvested.append(Harvested(reference=ref, dataset=outcome))
    return harvested


def _promote_config_fields(factory_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Split *factory_kwargs* into SourceConfig fields + residual extras."""
    residual = dict(factory_kwargs)
    promoted: dict[str, Any] = {}
    for key in _CONFIG_FIELDS:
        if key in residual:
            promoted[key] = residual.pop(key)
    if isinstance(promoted.get("allowed_hosts"), list):
        promoted["allowed_hosts"] = tuple(promoted["allowed_hosts"])
    if isinstance(promoted.get("allowed_schemes"), list):
        promoted["allowed_schemes"] = tuple(promoted["allowed_schemes"])
    promoted["extra"] = residual
    return promoted


def run_harvest(
    refs: list[str],
    *,
    source_type: str = "website",
    **factory_kwargs: Any,
) -> list[Harvested]:
    """Synchronous wrapper of :func:`harvest` for the CLI."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "run_harvest() cannot run inside a running event loop; use await harvest()"
        )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(harvest(refs, source_type=source_type, **factory_kwargs))
    finally:
        loop.close()