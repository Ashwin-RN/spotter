"""How Alembic reaches the database.

The address comes from app.config, never from alembic.ini. On a public https site with no DATABASE_URL
it stops with an error rather than migrating a local file. A caller can pass its own address in
config.attributes["database_url"] (the tests do), or an open connection in config.attributes["connection"].
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
            context.configure(connection=connection, **OPTIONS)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
