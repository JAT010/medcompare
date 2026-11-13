"""Registry service: validation and identity/duplicate rules."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from hospital_extractor.persistence.exceptions import (
    DuplicateCanonicalUrlError,
    InvalidOrganizationReferenceError,
)
from hospital_extractor.persistence.models import (
    FacilityStatus,
    HospitalFacility,
    HospitalOrganization,
    OrganizationStatus,
)
from hospital_extractor.persistence.repositories import FacilityRepository, OrganizationRepository
from hospital_extractor.persistence.url_normalization import normalize_canonical_url


def _normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


@dataclass(frozen=True)
class OrganizationCreate:
    name: str
    website_url: str | None = None
    status: str = OrganizationStatus.ACTIVE.value


@dataclass(frozen=True)
class OrganizationUpdate:
    name: str | None = None
    website_url: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class FacilityCreate:
    name: str
    organization_id: uuid.UUID | None = None
    canonical_url: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    latitude: Decimal | float | None = None
    longitude: Decimal | float | None = None
    status: str = FacilityStatus.ACTIVE.value


@dataclass(frozen=True)
class FacilityUpdate:
    """Partial update for a facility.

    ``organization_id`` and ``canonical_url`` use explicit-set flags because
    ``None`` is a meaningful value (clear organization / clear URL).
    """

    name: str | None = None
    organization_id: uuid.UUID | None = None
    set_organization_id: bool = False
    canonical_url: str | None = None
    set_canonical_url: bool = False
    address: str | None = None
    set_address: bool = False
    city: str | None = None
    set_city: bool = False
    state: str | None = None
    set_state: bool = False
    country: str | None = None
    set_country: bool = False
    postal_code: str | None = None
    set_postal_code: bool = False
    latitude: Decimal | float | None = None
    set_latitude: bool = False
    longitude: Decimal | float | None = None
    set_longitude: bool = False
    status: str | None = None


class RegistryService:
    """Registry operations with validation and explicit transactions."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.organizations = OrganizationRepository(session)
        self.facilities = FacilityRepository(session)

    # -- organizations -----------------------------------------------------

    def create_organization(self, data: OrganizationCreate) -> HospitalOrganization:
        name = data.name.strip()
        if not name:
            raise ValueError("Organization name is required")
        org = HospitalOrganization(
            name=name,
            normalized_name=_normalize_name(name),
            website_url=data.website_url.strip() if data.website_url else None,
            status=data.status,
        )
        self.organizations.create(org)
        self.session.commit()
        self.session.refresh(org)
        return org

    def get_organization(self, organization_id: uuid.UUID) -> HospitalOrganization:
        return self.organizations.require(organization_id)

    def list_organizations(
        self,
        *,
        name_query: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[HospitalOrganization]:
        return self.organizations.list(
            name_query=name_query, status=status, limit=limit, offset=offset
        )

    def update_organization(
        self, organization_id: uuid.UUID, data: OrganizationUpdate
    ) -> HospitalOrganization:
        org = self.organizations.require(organization_id)
        if data.name is not None:
            name = data.name.strip()
            if not name:
                raise ValueError("Organization name is required")
            org.name = name
            org.normalized_name = _normalize_name(name)
        if data.website_url is not None:
            org.website_url = data.website_url.strip() if data.website_url else None
        if data.status is not None:
            org.status = data.status
        self.organizations.save(org)
        self.session.commit()
        self.session.refresh(org)
        return org

    def deactivate_organization(self, organization_id: uuid.UUID) -> HospitalOrganization:
        org = self.organizations.require(organization_id)
        org.status = OrganizationStatus.INACTIVE.value
        self.organizations.save(org)
        self.session.commit()
        self.session.refresh(org)
        return org

    # -- facilities --------------------------------------------------------

    def register_facility(self, data: FacilityCreate) -> HospitalFacility:
        name = data.name.strip()
        if not name:
            raise ValueError("Facility name is required")
        if data.organization_id is not None:
            self._validate_organization(data.organization_id)

        canonical_url: str | None = None
        normalized_canonical_url: str | None = None
        website_domain: str | None = None
        if data.canonical_url is not None and data.canonical_url.strip():
            normalized = normalize_canonical_url(data.canonical_url)
            canonical_url = data.canonical_url.strip()
            normalized_canonical_url = normalized.normalized
            website_domain = normalized.domain
            existing = self.facilities.get_by_normalized_canonical_url(normalized_canonical_url)
            if existing is not None:
                raise DuplicateCanonicalUrlError(normalized_canonical_url, existing.id)

        facility = HospitalFacility(
            organization_id=data.organization_id,
            name=name,
            normalized_name=_normalize_name(name),
            canonical_url=canonical_url,
            normalized_canonical_url=normalized_canonical_url,
            website_domain=website_domain,
            address=data.address,
            city=data.city,
            state=data.state,
            country=data.country,
            postal_code=data.postal_code,
            latitude=data.latitude,
            longitude=data.longitude,
            status=data.status,
        )
        self.facilities.create(facility)
        self.session.commit()
        self.session.refresh(facility)
        return facility

    def get_facility(self, facility_id: uuid.UUID) -> HospitalFacility:
        return self.facilities.require(facility_id)

    def get_facility_by_canonical_url(self, canonical_url: str) -> HospitalFacility | None:
        normalized = normalize_canonical_url(canonical_url)
        return self.facilities.get_by_normalized_canonical_url(normalized.normalized)

    def list_facilities(
        self,
        *,
        name_query: str | None = None,
        city: str | None = None,
        state: str | None = None,
        organization_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[HospitalFacility]:
        return self.facilities.list(
            name_query=name_query,
            city=city,
            state=state,
            organization_id=organization_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    def update_facility(self, facility_id: uuid.UUID, data: FacilityUpdate) -> HospitalFacility:
        facility = self.facilities.require(facility_id)
        original_id = facility.id

        if data.name is not None:
            name = data.name.strip()
            if not name:
                raise ValueError("Facility name is required")
            facility.name = name
            facility.normalized_name = _normalize_name(name)

        if data.set_organization_id:
            org_id = data.organization_id
            if org_id is not None:
                self._validate_organization(org_id)
            facility.organization_id = org_id

        if data.set_canonical_url:
            new_url = data.canonical_url
            if new_url is None or not new_url.strip():
                facility.canonical_url = None
                facility.normalized_canonical_url = None
                facility.website_domain = None
            else:
                normalized = normalize_canonical_url(new_url)
                existing = self.facilities.get_by_normalized_canonical_url(
                    normalized.normalized
                )
                if existing is not None and existing.id != facility.id:
                    raise DuplicateCanonicalUrlError(normalized.normalized, existing.id)
                facility.canonical_url = new_url.strip()
                facility.normalized_canonical_url = normalized.normalized
                facility.website_domain = normalized.domain

        for attr, flag in (
            ("address", data.set_address),
            ("city", data.set_city),
            ("state", data.set_state),
            ("country", data.set_country),
            ("postal_code", data.set_postal_code),
            ("latitude", data.set_latitude),
            ("longitude", data.set_longitude),
        ):
            if flag:
                setattr(facility, attr, getattr(data, attr))

        if data.status is not None:
            facility.status = data.status

        if facility.id != original_id:
            raise AssertionError("Facility UUID must never change")

        self.facilities.save(facility)
        self.session.commit()
        self.session.refresh(facility)
        return facility

    def deactivate_facility(self, facility_id: uuid.UUID) -> HospitalFacility:
        facility = self.facilities.require(facility_id)
        facility.status = FacilityStatus.INACTIVE.value
        self.facilities.save(facility)
        self.session.commit()
        self.session.refresh(facility)
        return facility

    def _validate_organization(self, organization_id: uuid.UUID) -> None:
        if self.organizations.get(organization_id) is None:
            raise InvalidOrganizationReferenceError(organization_id)
