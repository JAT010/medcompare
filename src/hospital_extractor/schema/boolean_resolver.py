"""Evidence-weighted boolean resolver.

Every yes/no intake field is decided the same way:

1. a corpus of yes/no text patterns is searched against each page's raw text
2. each match becomes one weighted :class:`~.models.intake.IntakeEvidence`
   (``+weight`` for yes, ``-weight`` for no)
3. the weighted sum decides the tri-state value:

   - ``yes`` when the total is strictly positive
   - ``no`` when strictly negative
   - ``unknown`` when zero (no signals, or exactly balanced)

Patterns are declared here so extraction stays pure and declarative; the
resolver itself has zero side effects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hospital_extractor.models.intake import (
    BooleanDecision,
    IntakeEvidence,
    MasterIntake,
)
from hospital_extractor.schema.intake import boolean_keys

__all__ = [
    "BALANCE_NEGATIVES",
    "Detector",
    "NO_PATTERNS",
    "YES_PATTERNS",
    "apply_detectors",
    "detectors",
    "negate",
    "resolve",
]

# Negation wrappers reused across the whole corpus: a yes-term found directly
# after any of these markers is a "no" signal. ``negate()`` builds each
# detector's negative form from it.
_NEGATION = r"(?:\bno\b|\bnon[-\s]?|\bwithout\b)"


def negate(term: str) -> str:
    """Negative form of *term*: ``no`` / ``non`` / ``without`` + term."""
    return rf"{_NEGATION}[^\w]{{0,3}}{term}"


# --- plain "yes" terms ---------------------------------------------------

# regex term strings, one per recognisable feature; the yes corpus and every
# detector share these so the two can never drift apart.
# Conservative by design: only explicit evidence counts. "robotic surgery"
# alone does not prove a Da Vinci system, so it is not a Da Vinci term.
_FEATURE_TERMS: dict[str, tuple[str, ...]] = {
    "da_vinci_robotic_surgery": (r"da\s+vinci",),
    "advanced_laparoscopy_mis": (
        r"laparoscopy",
        r"laparoscopic",
        r"minimal[-\s]?invasive\s+surgery",
    ),
    "mri_3_tesla_or_higher": (r"3[-\s]?tesla", r"three[-\s]?tesla"),
    "jci_accredited": (r"\bjci\b",),
    "icu": (r"\bicu\b", r"intensive\s+care\s+unit"),
    "cardiac_sciences_enabled": (r"cardiac",),
    "bypass_surgery_cabg": (r"bypass", r"\bcabg\b", r"coronary\s+artery\s+bypass"),
    "angioplasty_stenting": (r"angioplasty", r"stenting", r"\bstent\b"),
    "heart_valve_replacement": (r"heart\s+valve\s+replacement", r"\bavr\b", r"\bmvr\b"),
    "heart_valve_repair": (r"heart\s+valve\s+repair",),
    "pacemaker_icd_implantation": (r"pacemaker", r"\bicd\b", r"defibrillator"),
    "heart_transplant": (r"heart\s+transplant",),
    "orthopaedics_joint_replacement_enabled": (
        r"orthopaedic",
        r"orthopedic",
        r"joint\s+replacement",
        r"arthroplasty",
    ),
    "total_knee_replacement": (r"total\s+knee\s+replacement", r"\btkr\b", r"knee\s+replacement"),
    "total_hip_replacement": (r"total\s+hip\s+replacement", r"\bthr\b", r"hip\s+replacement"),
}

# (regex, weight) pairs. Matched in page text they mean the feature is present.
# The negative corpus mirrors each term via ``negate()`` with a 10x weight so a
# single explicit "no X" on the page outweighs incidental positive mentions.
YES_PATTERNS: tuple[tuple[str, float], ...] = tuple(
    (term, 1.0) for terms in _FEATURE_TERMS.values() for term in terms
)
NO_PATTERNS: tuple[tuple[str, float], ...] = tuple(
    (negate(term), 10.0) for terms in _FEATURE_TERMS.values() for term in terms
)

# Alias kept for callers that want to build custom corpora.
BALANCE_NEGATIVES = negate


@dataclass(frozen=True)
class Detector:
    """One user-visible boolean feature (e.g. "Da Vinci Robotic Surgery").

    ``yes_patterns`` / ``no_patterns`` are (regex, weight) corpora searched
    against page text; a match contributes ``+weight`` (yes) or ``-weight``
    (no) to the field's final score.
    """

    name: str
    yes_patterns: tuple[tuple[str, float], ...]
    no_patterns: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        for pattern, _ in (*self.yes_patterns, *self.no_patterns):
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(
                    f"invalid detector pattern for {self.name!r}: {pattern!r} ({exc})"
                ) from exc


def detectors() -> tuple[Detector, ...]:
    """All boolean intake detectors, derived from the canonical schema."""
    result: list[Detector] = []
    for key in sorted(boolean_keys()):
        terms = _FEATURE_TERMS.get(key)
        if not terms:
            continue
        result.append(
            Detector(
                name=key,
                yes_patterns=tuple((term, 1.0) for term in terms),
                no_patterns=tuple((negate(term), 10.0) for term in terms),
            )
        )
    return tuple(result)


def resolve(
    booleans: dict[str, BooleanDecision],
    evidence: dict[str, list[IntakeEvidence]],
    *,
    strengths: dict[str, float] | None = None,
) -> tuple[MasterIntake, list[IntakeEvidence]]:
    """Resolve a master intake from per-field evidence using the weighted rule.

    ``strengths`` may override a detector's default per-field weight (useful
    when a page is authoritative for a given feature).
    """
    intake = MasterIntake()
    all_items: list[IntakeEvidence] = []
    for key in sorted(boolean_keys()):
        items = evidence.get(key, [])
        score = 0.0
        resolved_items: list[IntakeEvidence] = []
        for item in items:
            weight = strengths.get(key, item.strength) if strengths else item.strength
            resolved_items.append(
                IntakeEvidence(
                    field_key=item.field_key,
                    decision=item.decision,
                    text=item.text,
                    source_url=item.source_url,
                    method=item.method,
                    strength=weight,
                    label=item.label,
                )
            )
            all_items.append(resolved_items[-1])
            score += weight if item.decision == "yes" else -weight
        if score > 0.0:
            intake.booleans[key] = "yes"
        elif score < 0.0:
            intake.booleans[key] = "no"
        else:
            intake.booleans[key] = "unknown"
        intake.evidence[key] = resolved_items
    return intake, all_items


def apply_detectors(
    text: str,
    url: str,
    *,
    strength: float = 1.0,
    limit: int = 400,
) -> list[IntakeEvidence]:
    """Run every detector against *text* once.

    Returns one evidence item per (detector, decision); a feature that is both
    affirmed and denied yields two items so the weighted resolver can
    arbitrate. Matches are trimmed to a window around the hit.
    """
    found: list[IntakeEvidence] = []
    for detector in detectors():
        for pattern, weight in detector.yes_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                found.append(
                    IntakeEvidence.yes(
                        detector.name,
                        text=_span(text, match, limit),
                        source_url=url,
                        method="pattern-match",
                        strength=strength * weight,
                        label=detector.name,
                    )
                )
                break
        for pattern, weight in detector.no_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                found.append(
                    IntakeEvidence.no(
                        detector.name,
                        text=_span(text, match, limit),
                        source_url=url,
                        method="pattern-match",
                        strength=strength * weight,
                        label=detector.name,
                    )
                )
                break
    return found


def _span(text: str, match: re.Match[str], limit: int) -> str:
    """Trimmed window of text around *match*, capped at *limit* chars."""
    start = max(0, match.start() - 30)
    end = min(len(text), match.end() + 30)
    window = " ".join(text[start:end].split())
    if len(window) > limit:
        window = window[: limit - 3].rstrip() + "..."
    return window
