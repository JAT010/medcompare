"""Deterministic hospital discovery contracts (M4 P1-A, planning-implemented).

P1-A provides versioned, deterministic interpretation, provider-neutral
interfaces/DTOs, a truthful not-configured provider, and conservative facility
identity semantics. It does NOT persist discovery state, run migrations, call
live providers or networks, use an LLM, or mutate the hospital registry.
"""

from .interpret import interpret_discovery_prompt
from .provider import NotConfiguredDiscoveryProvider
from .schemas import (
    DISCOVERY_CONTRACT_VERSION,
    DISCOVERY_INTERPRETER,
    DiscoveryInterpretRequest,
    DiscoveryInterpretResponse,
)

__all__ = [
    "DISCOVERY_CONTRACT_VERSION",
    "DISCOVERY_INTERPRETER",
    "DiscoveryInterpretRequest",
    "DiscoveryInterpretResponse",
    "NotConfiguredDiscoveryProvider",
    "interpret_discovery_prompt",
]
