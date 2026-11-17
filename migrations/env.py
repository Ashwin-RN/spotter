"""How Alembic reaches the database.

The address comes from app.config, never from alembic.ini. On a public https site with no DATABASE_URL
it stops with an error rather than migrating a local file. A caller can pass its own address in
config.attributes["database_url"] (the tests do), or an open connection in config.attributes["connection"].

On SQLite, a migration runs with foreign keys off (contract hardening.md, decision 16). A table rebuild drops the old
table, and with foreign keys on, that fires ON DELETE on every row that points at it: 0012's rebuild of lift_session
would take every lift set with it. The pragma does nothing inside a transaction, and SQLAlchemy begins one on the pragma
itself, so the connection commits straight after it: without that commit alembic never commits the run, and the run
is lost with no error. After the migrations, PRAGMA foreign_key_check must find no row, or the run fails. The app's
own connections keep foreign keys on (app/db.py). An open connection passed in is used as it is.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import make_engine  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


# render_as_batch: SQLite cannot alter a table in place, so later migrations rebuild it instead.
OPTIONS = dict(target_metadata=target_metadata, compare_type=True, render_as_batch=True)


def _address() -> str:
    if config.attributes.get("database_url"):
        return config.attributes["database_url"]
    settings = get_settings()
    if settings.database_problem:
        # Never migrate a throwaway SQLite file on the host: one line on stderr, exit status 1.
        sys.exit(f"FAILED: {settings.database_problem}")
    return settings.database_url


def _foreign_keys_off(connection) -> None:
    """SQLite: foreign keys off on this connection, then commit, before alembic begins (module notes)."""
    if connection.dialect.name != "sqlite":
        return
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    connection.commit()


def _foreign_keys_checked(connection) -> None:
    """SQLite: after the migrations, every key must still hold. One line naming the tables and the revision the
    database is now at, never a value. SQLite has committed each migration by then: the line says so."""
    if connection.dialect.name != "sqlite":
        return
    broken = connection.exec_driver_sql("PRAGMA foreign_key_check").all()  # a read: nothing to commit
    if broken:
        tables = ", ".join(sorted({row[0] for row in broken}))
        at = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
        sys.exit(f"FAILED: the migrations ran, to {at}, and PRAGMA foreign_key_check then found {len(broken)} rows, "
                 f"in {tables}.")


def run_offline() -> None:
    """Write the SQL out instead of running it: alembic upgrade head --sql."""
    context.configure(url=_address(), literal_binds=True, **OPTIONS)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, **OPTIONS)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = make_engine(_address())
    try:
        with engine.connect() as connection:
            _foreign_keys_off(connection)
            context.configure(connection=connection, **OPTIONS)
            with context.begin_transaction():
                context.run_migrations()
            _foreign_keys_checked(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
