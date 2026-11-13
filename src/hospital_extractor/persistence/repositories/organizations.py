"""Organization repository."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from hospital_extractor.persistence.exceptions import OrganizationNotFoundError
from hospital_extractor.persistence.models import HospitalOrganization


class OrganizationRepository:
    """Persistence operations for hospital organizations."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, organization: HospitalOrganization) -> HospitalOrganization:
        self.session.add(organization)
        self.session.flush()
        return organization

    def get(self, organization_id: uuid.UUID) -> HospitalOrganization | None:
        return self.session.get(HospitalOrganization, organization_id)

    def require(self, organization_id: uuid.UUID) -> HospitalOrganization:
        org = self.get(organization_id)
        if org is None:
            raise OrganizationNotFoundError(organization_id)
        return org

    def list(
        self,
        *,
        name_query: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[HospitalOrganization]:
        stmt = select(HospitalOrganization)
        if name_query:
            normalized = " ".join(name_query.strip().lower().split())
            stmt = stmt.where(HospitalOrganization.normalized_name.contains(normalized))
        if status:
            stmt = stmt.where(HospitalOrganization.status == status)
        stmt = (
            stmt.order_by(HospitalOrganization.name, HospitalOrganization.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def save(self, organization: HospitalOrganization) -> HospitalOrganization:
        self.session.add(organization)
        self.session.flush()
        return organization
