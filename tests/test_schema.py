"""The schema: the migration builds every table, it agrees with the models, the one-live-weigh-in rule
holds, it can be taken down and rebuilt, and the SQL it writes for Postgres is sound."""

import dataclasses
import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app import config, db, exercises
from app.config import DEFAULT_DATABASE_URL, NO_DATABASE, ROOT
from app.db import make_engine
from app.models import (
    Base, Bodyweight, CallLog, Exercise, Goal, LiftSession, LiftSet, UtcDateTime, entry_models, user_tables,
)

from .conftest import TEST_DATABASE_URL, TEST_DIR, alembic_config

ALL_TABLES = {"user", "exercise", "lift_session", "lift_set", "bodyweight", "goal", "call_log", "measurement",
              "saved_food", "food_entry", "cardio_entry", "usda_food", "usda_search"}


def _tables(address: str) -> set[str]:
    engine = make_engine(address)
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def test_the_migration_builds_every_table():
    assert set(Base.metadata.tables) == ALL_TABLES
    assert _tables(TEST_DATABASE_URL) == ALL_TABLES | {"alembic_version"}


def test_the_migration_and_the_models_agree():
    with db.get_engine().connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert differences == []


def test_downgrade_then_upgrade_works():
    folder = Path(tempfile.mkdtemp(dir=TEST_DIR))
    address = f"sqlite:///{(folder / 'round-trip.db').as_posix()}"
    cfg = alembic_config(address)
    command.upgrade(cfg, "head")
    assert _tables(address) == ALL_TABLES | {"alembic_version"}
    command.downgrade(cfg, "base")
    assert _tables(address) == {"alembic_version"}
    command.upgrade(cfg, "head")
    assert _tables(address) == ALL_TABLES | {"alembic_version"}


def test_the_postgres_sql_is_portable():
    cfg = alembic_config("postgresql+psycopg://spotter@db.invalid/spotter")  # offline: never connects
    out = io.StringIO()
    with redirect_stdout(out):
        command.upgrade(cfg, "head", sql=True)
    sql = out.getvalue()
    assert 'CREATE TABLE "user"' in sql
    assert "CREATE UNIQUE INDEX uq_bodyweight_user_id_date_live ON bodyweight (user_id, date) " \
           "WHERE deleted_at IS NULL" in sql
    assert "BOOLEAN DEFAULT false NOT NULL" in sql and "DEFAULT 0" not in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert sql.count("CREATE TABLE") == len(ALL_TABLES) + 1  # and alembic_version


# ---------------------------------------------------------------- one live weigh-in a day

def _weigh_in(user_id: int, day: date, kg: float, deleted: bool = False) -> Bodyweight:
    now = db.utcnow()
    return Bodyweight(user_id=user_id, date=day, kg=kg, dedupe_key=f"{day}:{kg}", logged_by="claude",
                      deleted_at=now if deleted else None)


def test_a_second_live_weigh_in_the_same_day_is_blocked(make_user, db_session):
    user = make_user()
    db_session.add(_weigh_in(user.id, date(2026, 11, 9), 80.0))
    db_session.commit()
    db_session.add(_weigh_in(user.id, date(2026, 11, 9), 80.5))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_a_weigh_in_is_allowed_again_after_a_soft_delete(make_user, db_session):
    user = make_user()
    first = _weigh_in(user.id, date(2026, 11, 9), 80.0)
    db_session.add(first)
    db_session.commit()
    first.deleted_at = db.utcnow()
    db_session.commit()
    db_session.add(_weigh_in(user.id, date(2026, 11, 9), 80.5))
    db_session.add(_weigh_in(user.id, date(2026, 11, 9), 79.0, deleted=True))  # deleted rows never count
    db_session.commit()
    rows = db_session.scalars(select(Bodyweight).where(Bodyweight.user_id == user.id)).all()
    assert sorted(r.kg for r in rows) == [79.0, 80.0, 80.5]


def test_the_same_day_is_free_for_another_person(make_user, db_session):
    a, b = make_user(), make_user()
    db_session.add_all([_weigh_in(a.id, date(2026, 11, 9), 80.0), _weigh_in(b.id, date(2026, 11, 9), 60.0)])
    db_session.commit()


# ---------------------------------------------------------------- times, cascades, lists

def test_times_come_back_as_utc_with_a_zone(make_user, db_session):
    user = make_user()
    ist = timezone(timedelta(hours=5, minutes=30))
    weigh_in = _weigh_in(user.id, date(2026, 11, 9), 80.0)
    weigh_in.created_at = datetime(2026, 11, 9, 10, 0, tzinfo=ist)
    db_session.add(weigh_in)
    db_session.commit()
    db_session.expire_all()
    stored = db_session.get(Bodyweight, weigh_in.id).created_at
    assert stored.tzinfo is not None and stored.utcoffset() == timedelta(0)
    assert stored == datetime(2026, 11, 9, 4, 30, tzinfo=timezone.utc)


def test_a_time_without_a_zone_is_refused():
    with pytest.raises(ValueError):
        UtcDateTime().process_bind_param(datetime(2026, 11, 9, 10, 0), None)


def test_deleting_a_user_deletes_everything_they_own(make_user, db_session):
    user, other = make_user(), make_user()
    squat = db_session.scalar(select(Exercise).where(Exercise.name == "Back Squat"))
    session = LiftSession(user_id=user.id, date=date(2026, 11, 9), notes="", dedupe_key="k", logged_by="web",
                          sets=[LiftSet(exercise_id=squat.id, set_number=1, weight_kg=100, reps=5)])
    db_session.add_all([
        session, _weigh_in(user.id, date(2026, 11, 9), 80.0), _weigh_in(other.id, date(2026, 11, 9), 70.0),
        Goal(user_id=user.id, kind="lift", exercise_id=squat.id, unit="kg", start_value=100,
             start_date=date(2026, 11, 9), direction="up"),
        CallLog(user_id=user.id, function="whoami", started_at=db.utcnow(), ms=3, ok=True, result_bytes=10),
    ])
    db_session.commit()
    db_session.delete(db_session.get(type(user), user.id))
    db_session.commit()
    db_session.expire_all()
    for model in (LiftSession, LiftSet, Goal, CallLog):
        assert db_session.scalars(select(model)).all() == []
    assert [b.user_id for b in db_session.scalars(select(Bodyweight))] == [other.id]
    assert db_session.get(Exercise, squat.id) is not None  # the shared list stays


def test_the_list_of_tables_with_a_user_id_comes_from_the_schema():
    assert user_tables() == sorted(t.name for t in Base.metadata.tables.values() if "user_id" in t.c)
    assert user_tables() == ["bodyweight", "call_log", "cardio_entry", "food_entry", "goal", "lift_session",
                             "measurement", "saved_food"]
    assert [m.__tablename__ for m in entry_models()] == ["bodyweight", "cardio_entry", "food_entry",
                                                         "lift_session", "measurement"]


def test_every_entry_table_has_the_five_columns_and_two_indexes():
    for model in entry_models():
        table = model.__table__
        assert {"logged_by", "created_at", "updated_at", "deleted_at", "is_sample"} <= set(table.c.keys())
        assert table.c.deleted_at.nullable and not table.c.created_at.nullable
        indexed = {tuple(c.name for c in index.columns) for index in table.indexes}
        assert {("user_id", "date"), ("user_id", "created_at")} <= indexed


# ---------------------------------------------------------------- the exercise list

def test_the_exercise_sync_fills_an_empty_table_and_is_idempotent(db_session):
    db_session.execute(Exercise.__table__.delete())
    db_session.commit()
    assert exercises.sync(db_session) == len(exercises.LIFTS)
    db_session.commit()
    before = [(e.id, e.name, e.kind, e.category) for e in db_session.scalars(select(Exercise).order_by(Exercise.id))]
    assert exercises.sync(db_session) == 0
    db_session.commit()
    after = [(e.id, e.name, e.kind, e.category) for e in db_session.scalars(select(Exercise).order_by(Exercise.id))]
    assert before == after and len(after) == len(exercises.LIFTS)
    assert {kind for _, _, kind, _ in after} == {"lift"}
    assert {category for *_, category in after} == set(exercises.CATEGORIES)


def test_the_sync_adds_only_what_is_missing_and_changes_nothing_else(db_session):
    squat = db_session.scalar(select(Exercise).where(Exercise.name == "Back Squat"))
    squat.category = "changed-here"
    db_session.delete(db_session.scalar(select(Exercise).where(Exercise.name == "Plank")))
    db_session.commit()
    assert exercises.sync(db_session) == 1
    db_session.commit()
    assert db_session.get(Exercise, squat.id).category == "changed-here"
    assert db_session.scalar(select(Exercise).where(Exercise.name == "Plank")) is not None


def test_the_lift_list_has_unique_names_that_fit_the_columns():
    names = [row["name"] for row in exercises.rows()]
    assert len(names) == len(set(names)) == 41
    assert all(len(n) <= 64 for n in names)
    assert all(row["category"] in exercises.CATEGORIES and len(row["category"]) <= 16 for row in exercises.rows())


# ---------------------------------------------------------------- server defaults and sessions

def test_the_migration_and_the_models_agree_on_server_defaults():
    with db.get_engine().connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_server_default": True})
        assert compare_metadata(context, Base.metadata) == []


def test_the_postgres_sql_carries_the_server_defaults():
    cfg = alembic_config("postgresql+psycopg://spotter@db.invalid/spotter")
    out = io.StringIO()
    with redirect_stdout(out):
        command.upgrade(cfg, "head", sql=True)
    sql = out.getvalue()
    assert "claude_can VARCHAR(16) DEFAULT 'log' NOT NULL" in sql
    assert "add_back_exercise_kcal BOOLEAN DEFAULT false NOT NULL" in sql
    sample_tables = [t.name for t in Base.metadata.sorted_tables if "is_sample" in t.c]
    assert len(sample_tables) == 7
    assert sql.count("is_sample BOOLEAN DEFAULT false NOT NULL") == len(sample_tables)


def test_session_scope_rolls_back_on_an_error():
    with pytest.raises(RuntimeError):
        with db.session_scope() as session:
            session.add(Exercise(name="Rolled Back Lift", kind="lift", category="push"))
            session.flush()  # the row is written inside the transaction
            raise RuntimeError("boom")
    with db.session_scope() as session:
        assert session.scalar(select(Exercise).where(Exercise.name == "Rolled Back Lift")) is None


# ---------------------------------------------------------------- no silent SQLite on a real host

def _host_environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "MCP_RESOURCE_URL", "AUTHKIT_DOMAIN")}
    env.update({"MCP_RESOURCE_URL": "https://spotter.example.app/mcp", "AUTHKIT_DOMAIN": "example.authkit.app"})
    return env


def test_get_engine_refuses_a_public_site_without_a_database(app_settings):
    config.use_settings(dataclasses.replace(app_settings, database_url=DEFAULT_DATABASE_URL))
    with pytest.raises(db.DatabaseNotConfigured, match="DATABASE_URL is not set"):
        db.get_engine()
    with pytest.raises(db.DatabaseNotConfigured):
        db.new_session()


def test_a_local_site_still_gets_the_sqlite_default(app_settings):
    config.use_settings(dataclasses.replace(app_settings, database_url=DEFAULT_DATABASE_URL,
                                            mcp_resource_url="http://localhost:8000/mcp"))
    assert str(db.get_engine().url).endswith("/spotter.db")  # built, not connected: no file is made
    db.dispose()
    assert not (ROOT / "spotter.db").exists()


@pytest.mark.parametrize("command_line", [["-m", "alembic", "upgrade", "head"], ["-m", "app.exercises"]])
def test_the_deploy_steps_stop_with_one_clear_line(command_line):
    done = subprocess.run([sys.executable, *command_line], cwd=ROOT, env=_host_environment(),
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 1
    lines = [line for line in (done.stdout + done.stderr).splitlines() if line.strip()]
    assert lines == [f"FAILED: {NO_DATABASE}"]  # one line, the same from both steps
    assert not (ROOT / "spotter.db").exists()


def test_health_needs_no_database(make_client):
    client = make_client(database_url=DEFAULT_DATABASE_URL)  # a public https site with no DATABASE_URL
    r = client.get("/health")
    assert r.status_code == 200 and r.text == "OK"
