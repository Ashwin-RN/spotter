"""The database connection: one engine and one session factory, built from the settings on first use.

The same code runs on SQLite (a laptop, the tests) and Postgres (the host). The schema comes only
from the migrations in migrations/; nothing here creates tables. Importing this module never opens
a connection.

On Postgres the engine is held to fixed limits, so one busy person, a slow query or a stuck transaction
cannot hold the site:
- The pool: DB_POOL_SIZE kept connections and DB_MAX_OVERFLOW more on demand (5 and 5 by default, from
  app.config), a wait of POOL_TIMEOUT seconds for a free one, then sqlalchemy's TimeoutError, which the MCP
  wrapper and the web both show as "the database is busy". Connections are recycled after POOL_RECYCLE.
- Every connection starts with statement_timeout, lock_timeout and idle_in_transaction_session_timeout,
  sent in the connection's startup options. Set there, they are the session's defaults, so no rollback
  can undo them (a SET inside a transaction can be). A query cancelled by one is an OperationalError.
- connect_timeout: a database that does not answer fails in CONNECT_TIMEOUT seconds, not the OS's minutes.
SQLite keeps SQLAlchemy's own pool and no limits: it is a laptop or the tests.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import Engine, create_engine, event, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import DEFAULT_DB_MAX_OVERFLOW, DEFAULT_DB_POOL_SIZE, get_settings

# Postgres only. Fixed on purpose: they are the safety margin, not something to tune per deploy.
POOL_TIMEOUT = 10  # seconds to wait for a free pooled connection
POOL_RECYCLE = 1800  # seconds a connection is kept before it is replaced
CONNECT_TIMEOUT = 5  # seconds to open a connection
STATEMENT_TIMEOUT_MS = 15_000  # one statement
LOCK_TIMEOUT_MS = 10_000  # one wait for a lock
IDLE_IN_TRANSACTION_MS = 60_000  # a transaction left open with nothing running
STARTUP_OPTIONS = (f"-c statement_timeout={STATEMENT_TIMEOUT_MS} -c lock_timeout={LOCK_TIMEOUT_MS} "
                   f"-c idle_in_transaction_session_timeout={IDLE_IN_TRANSACTION_MS}")

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None
# What the engine was built for: the address (kept private, it may hold a password) and the pool numbers.
_address: tuple[str, int, int] | None = None
_lock = threading.Lock()


class DatabaseNotConfigured(RuntimeError):
    """The settings give no usable database (Settings.database_problem says why)."""


def utcnow() -> datetime:
    """The one clock: the current time, timezone-aware, in UTC."""
    return datetime.now(timezone.utc)


def postgres_options(pool_size: int = DEFAULT_DB_POOL_SIZE, max_overflow: int = DEFAULT_DB_MAX_OVERFLOW) -> dict:
    """create_engine's keyword arguments for Postgres: the pool and the connection limits (module notes)."""
    return {"pool_size": pool_size, "max_overflow": max_overflow, "pool_timeout": POOL_TIMEOUT,
            "pool_recycle": POOL_RECYCLE,
            "connect_args": {"connect_timeout": CONNECT_TIMEOUT, "options": STARTUP_OPTIONS}}


def make_engine(address: str, pool_size: int = DEFAULT_DB_POOL_SIZE,
                max_overflow: int = DEFAULT_DB_MAX_OVERFLOW) -> Engine:
    """An engine for this address. On Postgres, with the pool and the limits; the pool numbers are ignored
    on SQLite. Builds no connection."""
    extra = postgres_options(pool_size, max_overflow) if make_url(address).get_backend_name() == "postgresql" else {}
    engine = create_engine(address, pool_pre_ping=True, hide_parameters=True, **extra)  # no bound value in an error
    if engine.dialect.name == "sqlite":
        # Postgres always enforces foreign keys; make SQLite do the same, so cascades behave alike.
        @event.listens_for(engine, "connect")
        def _foreign_keys_on(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def get_engine() -> Engine:
    """The engine for the current settings' database. Rebuilt when the settings point elsewhere.
    Raises DatabaseNotConfigured when there is no usable database, so nothing silently writes to a
    throwaway file."""
    global _engine, _factory, _address
    settings = get_settings()
    if settings.database_problem:
        raise DatabaseNotConfigured(settings.database_problem)
    wanted = (settings.database_url, settings.db_pool_size, settings.db_max_overflow)
    with _lock:
        if _engine is None or wanted != _address:
            _dispose()
            _engine = make_engine(*wanted)
            _factory = sessionmaker(_engine, expire_on_commit=False)
            _address = wanted
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
