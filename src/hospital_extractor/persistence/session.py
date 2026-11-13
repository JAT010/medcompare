"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from hospital_extractor.persistence.config import get_database_config

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(*, database_url: str | None = None) -> Engine:
    """Create or return a cached SQLAlchemy engine.

    Uses connection pooling and ``pool_pre_ping``. Never creates tables.
    """
    global _engine, _session_factory
    config = get_database_config()
    url = database_url or config.url
    if _engine is not None and database_url is None:
        return _engine

    engine = create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_timeout=config.pool_timeout_seconds,
        pool_recycle=config.pool_recycle_seconds,
    )
    if database_url is None:
        _engine = engine
        _session_factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    return engine


def get_session_factory(*, database_url: str | None = None) -> sessionmaker[Session]:
    """Return a session factory bound to the configured engine."""
    global _session_factory
    if database_url is not None:
        engine = get_engine(database_url=database_url)
        return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope(*, database_url: str | None = None) -> Iterator[Session]:
    """Provide an explicit transaction scope."""
    factory = get_session_factory(database_url=database_url)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Dispose and clear cached engine/session factory (for tests/support)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
