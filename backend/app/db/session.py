"""Engine / session management and the FastAPI dependency."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.base import Base  # noqa: F401  (re-export for Alembic env)

_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        kwargs = {"pool_pre_ping": True}
        if settings.database_url.startswith("sqlite"):
            kwargs = {"connect_args": {"check_same_thread": False}}
        _engine = create_engine(settings.database_url, **kwargs)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_session_factory() -> sessionmaker:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def reset_engine() -> None:
    """Dispose of the cached engine (used by tests when settings change)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding one session per request."""
    session: Session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Standalone session for background tasks and scripts."""
    session: Session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
