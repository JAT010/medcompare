"""Insurance & payment record."""

from __future__ import annotations

from hospital_extractor.models.base import NamedEntity


class InsuranceProvider(NamedEntity):
    """An insurance provider, TPA or government scheme accepted by the hospital."""

    name: str | None = None
    tpa: bool | None = None
    cashless: bool | None = None
    government_scheme: bool | None = None
    description: str | None = None
