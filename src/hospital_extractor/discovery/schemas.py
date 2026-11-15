"""Versioned, deterministic discovery interpretation and provider contracts (P1-A).

Strict validation, bounded sizes, deterministic ordering, and explicit semantics.
No persistence, no live provider calls, no LLM, no registry mutation.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DISCOVERY_CONTRACT_VERSION = "discovery.interpret.v1"
DISCOVERY_INTERPRETER = "deterministic_discovery_parser_v1"
PROVIDER_CONTRACT_VERSION = "discovery.provider.v1"

MAX_PROMPT_LENGTH = 2000
MAX_LOCATION_LENGTH = 200
MAX_TERM_LENGTH = 120
MAX_ITEMS = 25
MAX_TEXT_LENGTH = 500
MAX_SOURCE_REFS = 20
MAX_CANDIDATES = 100

_WHITESPACE_RE = re.compile(r"\s+")


class DiscoveryBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConstraintKind(StrEnum):
    REQUIRED = "required"
    PREFERENCE = "preference"


class InformationPresence(StrEnum):
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    EXPLICIT_NEGATIVE = "explicit_negative"


class GeoKind(StrEnum):
    RADIUS = "radius"
    BOUNDARY_TEXT = "boundary_text"


class DiscoveryInterpretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    locale_hint: str | None = Field(default=None, max_length=16)

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("prompt must not be blank")
        return value

    @field_validator("locale_hint")
    @classmethod
    def locale_hint_strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("locale_hint")
    @classmethod
    def locale_hint_normalized(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class RadiusGeo(DiscoveryBaseModel):
    type: Literal["radius"] = "radius"
    center_text: str = Field(min_length=1, max_length=MAX_LOCATION_LENGTH)
    radius_km: float = Field(gt=0)
    original_value: float = Field(gt=0)
    original_unit: str = Field(min_length=1, max_length=16)
    unit_explicit: bool = True


class BoundaryTextGeo(DiscoveryBaseModel):
    type: Literal["boundary_text"] = "boundary_text"
    value: str = Field(min_length=1, max_length=MAX_LOCATION_LENGTH)


GeoFilter = Annotated[RadiusGeo | BoundaryTextGeo, Field(discriminator="type")]


class ExtractedTerm(DiscoveryBaseModel):
    value: str = Field(min_length=1, max_length=MAX_TERM_LENGTH)
    kind: str = Field(min_length=1, max_length=40)
    source_text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class ConstraintItem(DiscoveryBaseModel):
    key: str = Field(min_length=1, max_length=MAX_TERM_LENGTH)
    value: str = Field(min_length=1, max_length=MAX_TERM_LENGTH)
    kind: ConstraintKind
    source_text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class PreferenceItem(DiscoveryBaseModel):
    key: str = Field(min_length=1, max_length=MAX_TERM_LENGTH)
    value: str = Field(min_length=1, max_length=MAX_TERM_LENGTH)
    source_text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class NegativeRequirement(DiscoveryBaseModel):
    field: str = Field(min_length=1, max_length=MAX_TERM_LENGTH)
    value: str = Field(min_length=1, max_length=MAX_TERM_LENGTH)
    source_text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class UnsupportedRequirement(DiscoveryBaseModel):
    original_text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    reason: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    kind: str | None = Field(default=None, max_length=40)


class ClarificationItem(DiscoveryBaseModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    field: str | None = Field(default=None, max_length=80)


class AmbiguityItem(DiscoveryBaseModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    source_text: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class DiscoveryFilters(DiscoveryBaseModel):
    location_text: str | None = Field(default=None, max_length=MAX_LOCATION_LENGTH)
    location_presence: InformationPresence = InformationPresence.MISSING
    geo: GeoFilter | None = None
    hospital_categories: list[ExtractedTerm] = Field(
        default_factory=list, max_length=MAX_ITEMS
    )
    specialties: list[ExtractedTerm] = Field(default_factory=list, max_length=MAX_ITEMS)
    departments: list[ExtractedTerm] = Field(default_factory=list, max_length=MAX_ITEMS)
    required_facilities: list[ExtractedTerm] = Field(
        default_factory=list, max_length=MAX_ITEMS
    )
    explicit_exclusions: list[NegativeRequirement] = Field(
        default_factory=list, max_length=MAX_ITEMS
    )
    constraints: list[ConstraintItem] = Field(default_factory=list, max_length=MAX_ITEMS)
    preferences: list[PreferenceItem] = Field(default_factory=list, max_length=MAX_ITEMS)
    negative_requirements: list[NegativeRequirement] = Field(
        default_factory=list, max_length=MAX_ITEMS
    )
    missing_information: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def _consistency(self) -> DiscoveryFilters:
        # Never convert missing info into negative requirement.
        neg_fields = {n.field for n in self.negative_requirements}
        for missing in self.missing_information:
            if missing in neg_fields:
                raise ValueError(
                    "missing information must not be represented as a negative requirement"
                )
        if (
            self.location_text is None
            and self.location_presence == InformationPresence.EXPLICIT_NEGATIVE
        ):
            raise ValueError("explicit negative location requires location context")
        return self


class DiscoveryInterpretResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    normalized_prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    filters: DiscoveryFilters
    clarification_questions: list[ClarificationItem] = Field(
        default_factory=list, max_length=MAX_ITEMS
    )
    unsupported_requirements: list[UnsupportedRequirement] = Field(
        default_factory=list, max_length=MAX_ITEMS
    )
    ambiguities: list[AmbiguityItem] = Field(default_factory=list, max_length=MAX_ITEMS)
    interpreter: str = Field(min_length=1, max_length=120)
    contract_version: str = Field(min_length=1, max_length=120)
    interpretation_notes: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)


# --- Provider-neutral contracts (no unrestricted payloads) ---


class ProviderStatus(StrEnum):
    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PARTIAL = "partial"
    ERROR = "error"


class SourceReference(DiscoveryBaseModel):
    ref_type: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=500)
    uri: str | None = Field(default=None, max_length=500)


class ProviderAttribution(DiscoveryBaseModel):
    provider: str = Field(min_length=1, max_length=120)
    notice: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    terms_url: str | None = Field(default=None, max_length=500)


class DiscoverySearchRequest(DiscoveryBaseModel):
    query_text: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    filters: DiscoveryFilters | None = None
    page_token: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)
    timeout_ms: int | None = Field(default=None, ge=1, le=120000)


class ProviderCandidate(DiscoveryBaseModel):
    provider: str = Field(min_length=1, max_length=120)
    provider_candidate_id: str = Field(min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    address_text: str | None = Field(default=None, max_length=500)
    locality: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=40)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    website_url: str | None = Field(default=None, max_length=500)
    categories: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    source_references: list[SourceReference] = Field(
        default_factory=list, max_length=MAX_SOURCE_REFS
    )
    retrieved_at: datetime
    attribution: ProviderAttribution


class ProviderError(DiscoveryBaseModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    retryable: bool = False
    retry_after_ms: int | None = Field(default=None, ge=0)


class DiscoverySearchResponse(DiscoveryBaseModel):
    provider: str = Field(min_length=1, max_length=120)
    contract_version: str = PROVIDER_CONTRACT_VERSION
    status: ProviderStatus
    candidates: list[ProviderCandidate] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    next_page_token: str | None = Field(default=None, max_length=200)
    total_available: int | None = Field(default=None, ge=0)
    retrieved_at: datetime
    attribution: ProviderAttribution | None = None
    warnings: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    errors: list[ProviderError] = Field(default_factory=list, max_length=MAX_ITEMS)
    partial: bool = False

    @model_validator(mode="after")
    def _status_consistency(self) -> DiscoverySearchResponse:
        if self.status == ProviderStatus.NOT_CONFIGURED and self.candidates:
            raise ValueError("not_configured provider must not return candidates")
        if self.status == ProviderStatus.PARTIAL:
            object.__setattr__(self, "partial", True)
        return self


def normalize_prompt_text(prompt: str) -> str:
    return _WHITESPACE_RE.sub(" ", prompt.strip())
