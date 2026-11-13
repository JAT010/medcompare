"""Shared FastAPI dependencies for the MedCompare API."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from hospital_extractor.persistence.session import session_scope

__all__ = ["get_db_session"]


def get_db_session() -> Iterator[Session]:
    """Yield a registry session scoped to the request.

    Tests override this dependency with an isolated session; the default reads
    ``MEDCOMPARE_DATABASE_URL``.
    """
    with session_scope() as session:
        yield session
