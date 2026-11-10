"""Configuration for the extraction pipeline.

Loaded from ``config/default.toml`` when present; every value has a sensible
default so the pipeline runs with zero configuration. Extraction-specific
limits (association thresholds, description truncation) live here rather than
being scattered through extractors.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ExtractionConfig",
    "UNKNOWN_FALSE",
    "UNKNOWN_KEEP",
    "UNKNOWN_POLICIES",
    "UNKNOWN_POLICY_DEFAULT",
    "load_config",
]

DEFAULT_CONFIG_PATH = Path("config/default.toml")

_ASSOCIATION_RANK = {"confirmed": 0, "probable": 1, "uncertain": 2, "not_associated": 3}

# How UNKNOWN boolean decisions are submitted in the final master CSV.
# Preserving UNKNOWN is the safe default: an unresolved capability must not be
# presented as verified absence. UNKNOWN_FALSE is an explicit legacy
# compatibility mode for consumers that only accept TRUE/FALSE.
UNKNOWN_KEEP = "keep_unknown"  # default: UNKNOWN stays UNKNOWN in the submission
UNKNOWN_FALSE = "false"  # legacy: UNKNOWN -> FALSE (recorded in the quality report)
UNKNOWN_POLICY_DEFAULT = UNKNOWN_KEEP
UNKNOWN_POLICIES = frozenset({UNKNOWN_FALSE, UNKNOWN_KEEP})


@dataclass(frozen=True)
class ExtractionConfig:
    """Deterministic, conservative extraction settings."""

    # Minimum association strength for a doctor to enter the target dataset.
    # Doctors with a weaker association are excluded; uncertain ones are kept
    # and flagged in the data-quality metadata.
    doctor_association_include_min: str = "probable"
    exclude_not_associated_doctors: bool = True
    # Description fields longer than this are truncated (avoids dumping pages).
    description_max_length: int = 800
    # Max characters of visible text scanned for capacity/contact facts.
    text_scan_limit: int = 12000
    # Report extraction warnings at or below this confidence.
    low_confidence_threshold: float = 0.5
    # Submission policy for UNKNOWN boolean intake fields ("keep_unknown" or
    # legacy "false"). Applied only at export time; the projection's
    # yes/no/unknown state and evidence trail are never mutated.
    boolean_unknown_policy: str = UNKNOWN_POLICY_DEFAULT

    def __post_init__(self) -> None:
        if self.boolean_unknown_policy not in UNKNOWN_POLICIES:
            raise ValueError(
                "boolean_unknown_policy must be one of "
                f"{sorted(UNKNOWN_POLICIES)}, got {self.boolean_unknown_policy!r}"
            )


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> ExtractionConfig:
    """Load extraction settings from a TOML file, falling back to defaults.

    Unknown keys are ignored; missing keys keep their defaults. The file is
    optional — the pipeline must run without it.
    """
    config_path = Path(path)
    raw: dict[str, Any] = {}
    if config_path.is_file():
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
        raw = raw.get("extraction", raw)
    known = {field.name for field in fields(ExtractionConfig)}
    values = {key: value for key, value in raw.items() if key in known}
    return ExtractionConfig(**values)


def association_rank(association: str | None) -> int:
    """Rank an association state; unknown states rank lowest."""
    if association is None:
        return len(_ASSOCIATION_RANK)
    return _ASSOCIATION_RANK.get(association, len(_ASSOCIATION_RANK))
