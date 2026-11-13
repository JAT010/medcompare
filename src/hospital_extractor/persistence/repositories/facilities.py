"""Facility repository."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hospital_extractor.persistence.exceptions import (
    DuplicateCanonicalUrlError,
    FacilityNotFoundError,
)
from hospital_extractor.persistence.models import HospitalFacility


class FacilityRepository:
    """Persistence operations for hospital facilities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, facility: HospitalFacility) -> HospitalFacility:
        self.session.add(facility)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise self._translate_integrity_error(facility, exc) from exc
        return facility

    def get(self, facility_id: uuid.UUID) -> HospitalFacility | None:
        return self.session.get(HospitalFacility, facility_id)

    def require(self, facility_id: uuid.UUID) -> HospitalFacility:
        facility = self.get(facility_id)
        if facility is None:
            raise FacilityNotFoundError(facility_id)
        return facility

    def get_by_normalized_canonical_url(
        self, normalized_canonical_url: str
    ) -> HospitalFacility | None:
        stmt = select(HospitalFacility).where(
            HospitalFacility.normalized_canonical_url == normalized_canonical_url
        )
        return self.session.execute(stmt).scalars().first()

    def list(
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
        stmt = select(HospitalFacility)
        if name_query:
            normalized = " ".join(name_query.strip().lower().split())
            stmt = stmt.where(HospitalFacility.normalized_name.contains(normalized))
        if city:
            stmt = stmt.where(HospitalFacility.city.ilike(city))
        if state:
            stmt = stmt.where(HospitalFacility.state.ilike(state))
        if organization_id is not None:
            stmt = stmt.where(HospitalFacility.organization_id == organization_id)
        if status:
            stmt = stmt.where(HospitalFacility.status == status)
        stmt = (
            stmt.order_by(HospitalFacility.name, HospitalFacility.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def save(self, facility: HospitalFacility) -> HospitalFacility:
        self.session.add(facility)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise self._translate_integrity_error(facility, exc) from exc
        return facility

    def _translate_integrity_error(
        self, facility: HospitalFacility, exc: IntegrityError
    ) -> Exception:
        msg = str(getattr(exc, "orig", exc)).lower()
        if (
            "normalized_canonical_url" in msg
            or "uq_hospital_facilities_normalized_canonical_url" in msg
        ):
            url = facility.normalized_canonical_url
            if url:
                existing = self.get_by_normalized_canonical_url(url)
                existing_id = existing.id if existing else uuid.UUID(int=0)
                return DuplicateCanonicalUrlError(url, existing_id)
        return exc
