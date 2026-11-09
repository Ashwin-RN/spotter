"""The database connection: one engine and one session factory, built from the settings on first use.

The same code runs on SQLite (a laptop, the tests) and Postgres (the host). The schema comes only
from the migrations in migrations/; nothing here creates tables. Importing this module never opens
a connection.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None
_address: str | None = None  # the address the engine was built for, kept private (it may hold a password)
_lock = threading.Lock()


def utcnow() -> datetime:
    """The one clock: the current time, timezone-aware, in UTC."""
    return datetime.now(timezone.utc)


def make_engine(address: str) -> Engine:
    engine = create_engine(address, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        # Postgres always enforces foreign keys; make SQLite do the same, so cascades behave alike.
        @event.listens_for(engine, "connect")
        def _foreign_keys_on(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def get_engine() -> Engine:
    """The engine for the current settings' database. Rebuilt when the settings point elsewhere."""
    global _engine, _factory, _address
    address = get_settings().database_url
    with _lock:
        if _engine is None or address != _address:
            _dispose()
            _engine = make_engine(address)
            _factory = sessionmaker(_engine, expire_on_commit=False)
            _address = address
        return _engine


def session_factory() -> sessionmaker[Session]:
    get_engine()
    factory = _factory
    assert factory is not None
    return factory


def new_session() -> Session:
    """A session the caller closes. Rows stay readable after commit (expire_on_commit is off)."""
    return session_factory()()


@contextmanager
def session_scope() -> Iterator[Session]:
    """One unit of work: commits when the block ends, rolls back if it raises, always closes."""
    session = new_session()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def dispose() -> None:
    """Close every pooled connection and forget the engine. The next use builds a new one."""
    with _lock:
        _dispose()


def _dispose() -> None:
    global _engine, _factory, _address
    if _engine is not None:
        _engine.dispose()
    _engine = _factory = _address = None
