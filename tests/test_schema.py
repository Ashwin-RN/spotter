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
# The seven tables 0001 gave an is_sample column and 0004 took it from.
MARKED_TABLES = ("food_entry", "cardio_entry", "lift_session", "bodyweight", "measurement", "goal", "saved_food")


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


def test_every_entry_table_has_the_four_columns_and_two_indexes():
    for model in entry_models():
        table = model.__table__
        assert {"logged_by", "created_at", "updated_at", "deleted_at"} <= set(table.c.keys())
        assert "is_sample" not in table.c
        assert table.c.deleted_at.nullable and not table.c.created_at.nullable
        indexed = {tuple(c.name for c in index.columns) for index in table.indexes}
        assert {("user_id", "date"), ("user_id", "created_at")} <= indexed


# ---------------------------------------------------------------- the exercise list

def test_the_exercise_sync_fills_an_empty_table_and_is_idempotent(db_session):
    db_session.execute(Exercise.__table__.delete())
    db_session.commit()
    every = len(exercises.LIFTS) + len(exercises.CARDIO)
    assert exercises.sync(db_session) == every
    db_session.commit()
    columns = lambda e: (e.id, e.name, e.kind, e.category, e.met, e.compendium_code)  # noqa: E731
    before = [columns(e) for e in db_session.scalars(select(Exercise).order_by(Exercise.id))]
    assert exercises.sync(db_session) == 0
    db_session.commit()
    after = [columns(e) for e in db_session.scalars(select(Exercise).order_by(Exercise.id))]
    assert before == after and len(after) == every
    assert {kind for _, _, kind, *_ in after} == {"lift", "cardio"}
    assert {c for _, _, kind, c, *_ in after if kind == "lift"} == set(exercises.CATEGORIES)
    assert {c for _, _, kind, c, *_ in after if kind == "cardio"} <= set(exercises.CARDIO_CATEGORIES)
    assert all(met and code for _, _, kind, _, met, code in after if kind == "cardio")


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
    assert len(names) == len(set(names)) == 41 + 15
    assert all(len(n) <= 64 for n in names)
    for row in exercises.rows():
        allowed = exercises.CATEGORIES if row["kind"] == "lift" else exercises.CARDIO_CATEGORIES
        assert row["category"] in allowed and len(row["category"]) <= 16
        assert row["compendium_code"] is None or len(row["compendium_code"]) <= 8


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
    # 0001 built the sample mark on seven tables and 0004 drops it from each: no model carries it now.
    assert [t.name for t in Base.metadata.sorted_tables if "is_sample" in t.c] == []
    assert sql.count("is_sample BOOLEAN DEFAULT false NOT NULL") == len(MARKED_TABLES)
    assert sql.count("DROP COLUMN is_sample") == len(MARKED_TABLES)


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


# ---------------------------------------------------------------- migration 0002: one live measurement a day

def _sqlite_at(name: str) -> str:
    folder = Path(tempfile.mkdtemp(dir=TEST_DIR))
    return f"sqlite:///{(folder / name).as_posix()}"


def test_0002_applies_on_a_database_that_holds_rows():
    address = _sqlite_at("with-rows.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, "0001")
    engine = make_engine(address)
    now = "2026-11-10 08:00:00.000000"
    try:
        with engine.begin() as c:
            c.exec_driver_sql("INSERT INTO user (id, provider, subject, claude_can, add_back_exercise_kcal, created_at, "
                              "updated_at, last_seen_at) VALUES (1, 'workos', 's1', 'log', 0, ?, ?, ?)", (now, now, now))
            c.exec_driver_sql("INSERT INTO bodyweight (user_id, date, kg, dedupe_key, logged_by, created_at, updated_at, "
                              "is_sample) VALUES (1, '2026-11-09', 80, 'k', 'web', ?, ?, 0)", (now, now))
            for value in (84, 85):  # two live waists on one day: the older one gives way
                c.exec_driver_sql("INSERT INTO measurement (user_id, date, kind, value, unit, dedupe_key, logged_by, "
                                  "created_at, updated_at, is_sample) VALUES (1, '2026-11-09', 'waist', ?, 'cm', 'k', "
                                  "'web', ?, ?, 0)", (value, now, now))
            c.exec_driver_sql("INSERT INTO measurement (user_id, date, kind, value, unit, dedupe_key, logged_by, "
                              "created_at, updated_at, is_sample) VALUES (1, '2026-11-09', 'hips', 95, 'cm', 'k', "
                              "'web', ?, ?, 0)", (now, now))
        command.upgrade(cfg, "0002")
        with engine.connect() as c:
            names = {i["name"] for i in inspect(c).get_indexes("measurement")}
            rows = c.exec_driver_sql("SELECT kind, value, deleted_at IS NULL FROM measurement ORDER BY id").all()
            kept = c.exec_driver_sql("SELECT count(*) FROM bodyweight WHERE deleted_at IS NULL").scalar()
        assert "uq_measurement_user_id_kind_date_live" in names
        assert [tuple(r) for r in rows] == [("waist", 84, 0), ("waist", 85, 1), ("hips", 95, 1)]
        assert kept == 1
        from sqlalchemy.orm import Session
        from app.models import Measurement
        with Session(engine) as s:  # the time 0002 wrote reads back through the model as UTC
            gone = s.scalar(select(Measurement.deleted_at).where(Measurement.deleted_at.is_not(None)))
            assert gone.tzinfo is not None and gone.utcoffset() == timedelta(0)
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.exec_driver_sql("INSERT INTO measurement (user_id, date, kind, value, unit, dedupe_key, logged_by, "
                              "created_at, updated_at, is_sample) VALUES (1, '2026-11-09', 'waist', 86, 'cm', 'k', "
                              "'web', ?, ?, 0)", (now, now))
        command.downgrade(cfg, "0001")
        with engine.connect() as c:
            left = {i["name"] for i in inspect(c).get_indexes("measurement")}
            assert "uq_measurement_user_id_kind_date_live" not in left
    finally:
        engine.dispose()


def test_0002_applies_on_an_empty_database_and_follows_0001():
    address = _sqlite_at("empty.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, "0001")
    command.upgrade(cfg, "0002")
    engine = make_engine(address)
    try:
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0002"
            assert "uq_measurement_user_id_kind_date_live" in {i["name"] for i in inspect(c).get_indexes("measurement")}
    finally:
        engine.dispose()


def test_the_postgres_sql_builds_the_measurement_index():
    cfg = alembic_config("postgresql+psycopg://spotter@db.invalid/spotter")
    out = io.StringIO()
    with redirect_stdout(out):
        command.upgrade(cfg, "head", sql=True)
    sql = out.getvalue()
    assert "CREATE UNIQUE INDEX uq_measurement_user_id_kind_date_live ON measurement (user_id, kind, date) " \
           "WHERE deleted_at IS NULL" in sql
    assert "UPDATE measurement SET deleted_at = CURRENT_TIMESTAMP" in sql


def test_the_first_migration_is_unchanged_in_what_it_builds():
    """0001 never moves: the new index lives in 0002 only, the new column in 0003 only."""
    text = (ROOT / "migrations" / "versions" / "0001_v1_schema.py").read_text(encoding="utf-8")
    assert "uq_measurement_user_id_kind_date_live" not in text
    assert "measurement_kind" not in text
    assert "measurement_kind" not in (ROOT / "migrations" / "versions" /
                                      "0002_one_live_measurement_a_day.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- migration 0003: goal.measurement_kind

# What SQLite reports back for each model type.
SQLITE_TYPES = {"Integer": "INTEGER", "String": "VARCHAR", "Text": "TEXT", "Float": "FLOAT", "Boolean": "BOOLEAN",
                "Date": "DATE", "UtcDateTime": "DATETIME"}


def _goal_columns(engine) -> list[str]:
    with engine.connect() as c:
        return [col["name"] for col in inspect(c).get_columns("goal")]


def test_0003_applies_on_a_database_that_holds_rows_and_comes_down_again():
    address = _sqlite_at("goal-rows.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, "0002")
    engine = make_engine(address)
    now = "2026-11-10 08:00:00.000000"
    try:
        with engine.begin() as c:
            c.exec_driver_sql("INSERT INTO user (id, provider, subject, claude_can, add_back_exercise_kcal, created_at, "
                              "updated_at, last_seen_at) VALUES (1, 'workos', 's1', 'log', 0, ?, ?, ?)", (now, now, now))
            for kind, target in (("bodyweight", 75), ("lift", 120)):
                c.exec_driver_sql("INSERT INTO goal (user_id, kind, unit, start_value, start_date, direction, "
                                  "target_value, target_date, status, text, created_at, updated_at, is_sample) "
                                  "VALUES (1, ?, 'kg', 80, '2026-11-01', 'down', ?, '2027-01-01', 'open', 't', ?, ?, 0)",
                                  (kind, target, now, now))
        assert "measurement_kind" not in _goal_columns(engine)
        command.upgrade(cfg, "0003")
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0003"
            rows = c.exec_driver_sql("SELECT kind, target_value, measurement_kind FROM goal ORDER BY id").all()
        assert [tuple(r) for r in rows] == [("bodyweight", 75, None), ("lift", 120, None)]
        with engine.begin() as c:  # the new column takes a value
            c.exec_driver_sql("INSERT INTO goal (user_id, kind, measurement_kind, unit, start_value, start_date, "
                              "direction, target_value, target_date, status, text, created_at, updated_at, is_sample) "
                              "VALUES (1, 'measurement', 'waist', 'cm', 88, '2026-11-01', 'down', 84, '2027-01-01', "
                              "'open', '', ?, ?, 0)", (now, now))
        command.downgrade(cfg, "0002")
        assert "measurement_kind" not in _goal_columns(engine)
        with engine.connect() as c:  # every row is kept, and the rebuilt table keeps its index and keys
            kept = c.exec_driver_sql("SELECT kind, target_value FROM goal ORDER BY id").all()
            indexes = {i["name"] for i in inspect(c).get_indexes("goal")}
            keys = {fk["referred_table"] for fk in inspect(c).get_foreign_keys("goal")}
        assert [tuple(r) for r in kept] == [("bodyweight", 75), ("lift", 120), ("measurement", 84)]
        assert "ix_goal_user_id_status" in indexes and keys == {"user", "exercise"}
        command.upgrade(cfg, "0003")
        assert "measurement_kind" in _goal_columns(engine)
    finally:
        engine.dispose()


def test_0003_applies_on_an_empty_database_and_follows_0002():
    address = _sqlite_at("empty-0003.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, "0002")
    command.upgrade(cfg, "0003")
    engine = make_engine(address)
    try:
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0003"
        assert "measurement_kind" in _goal_columns(engine)
    finally:
        engine.dispose()
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("0003").down_revision == "0002"


def test_the_postgres_sql_adds_the_goal_column():
    cfg = alembic_config("postgresql+psycopg://spotter@db.invalid/spotter")
    out = io.StringIO()
    with redirect_stdout(out):
        command.upgrade(cfg, "head", sql=True)
    sql = out.getvalue()
    assert "ALTER TABLE goal ADD COLUMN measurement_kind VARCHAR(16)" in sql
    assert "measurement_kind VARCHAR(16) NOT NULL" not in sql
    down = io.StringIO()
    with redirect_stdout(down):
        command.downgrade(cfg, "0003:0002", sql=True)
    assert "ALTER TABLE goal DROP COLUMN measurement_kind" in down.getvalue()


def test_the_models_and_the_migrations_agree_column_by_column():
    """0001 to 0004 against the models: every table, every column, its type, length and nullability."""
    with db.get_engine().connect() as c:
        found = inspect(c)
        for table in Base.metadata.sorted_tables:
            built = {col["name"]: col for col in found.get_columns(table.name)}
            assert set(built) == set(table.c.keys()), table.name
            for column in table.c:
                got = built[column.name]
                assert got["nullable"] == column.nullable, (table.name, column.name)
                model_type = type(column.type).__name__
                assert type(got["type"]).__name__.upper() == SQLITE_TYPES[model_type], (table.name, column.name)
                if getattr(column.type, "length", None):
                    assert got["type"].length == column.type.length, (table.name, column.name)
    goal = Base.metadata.tables["goal"].c.measurement_kind
    assert goal.nullable and goal.type.length == 16


# ---------------------------------------------------------------- migration 0004: no sample data

NOW = "2026-11-10 08:00:00.000000"


def _columns(engine, table: str) -> list[str]:
    with engine.connect() as c:
        return [col["name"] for col in inspect(c).get_columns(table)]


def _rows_at_0003(c) -> None:
    """Two people. Person 1 holds one marked row of every kind and one of their own, live and deleted; a session's
    sets on both sessions; their own food entry logged against the marked saved food. Person 2 holds only their own."""
    c.exec_driver_sql("INSERT INTO exercise (id, name, kind, category) VALUES (1, 'Back Squat', 'lift', 'legs')")
    for user in (1, 2):
        c.exec_driver_sql("INSERT INTO user (id, provider, subject, claude_can, add_back_exercise_kcal, created_at, "
                          "updated_at, last_seen_at) VALUES (?, 'workos', ?, 'log', 0, ?, ?, ?)",
                          (user, f"s{user}", NOW, NOW, NOW))
    entry = "logged_by, created_at, updated_at, deleted_at, is_sample"
    rows = [(1, 1, None), (1, 0, None), (1, 1, NOW), (2, 0, None)]   # (user, marked, deleted_at)
    for n, (user, marked, gone) in enumerate(rows, start=1):
        times = (NOW, NOW, gone, marked)
        c.exec_driver_sql(f"INSERT INTO lift_session (id, user_id, date, notes, dedupe_key, {entry}) "
                          "VALUES (?, ?, '2026-11-09', '', 'k', 'web', ?, ?, ?, ?)", (n, user, *times))
        c.exec_driver_sql("INSERT INTO lift_set (session_id, exercise_id, set_number, weight_kg, reps) "
                          "VALUES (?, 1, 1, 100, 5)", (n,))
        c.exec_driver_sql(f"INSERT INTO bodyweight (user_id, date, kg, dedupe_key, {entry}) "
                          "VALUES (?, ?, 80, 'k', 'web', ?, ?, ?, ?)", (user, f"2026-11-0{n}", *times))
        c.exec_driver_sql(f"INSERT INTO measurement (user_id, date, kind, value, unit, dedupe_key, {entry}) "
                          "VALUES (?, ?, 'waist', 84, 'cm', 'k', 'web', ?, ?, ?, ?)", (user, f"2026-11-0{n}", *times))
        c.exec_driver_sql(f"INSERT INTO cardio_entry (user_id, date, exercise_id, minutes, notes, dedupe_key, {entry}) "
                          "VALUES (?, '2026-11-09', 1, 30, '', 'k', 'web', ?, ?, ?, ?)", (user, *times))
        c.exec_driver_sql("INSERT INTO goal (user_id, kind, unit, start_value, start_date, direction, target_value, "
                          "target_date, status, text, created_at, updated_at, is_sample) VALUES (?, 'bodyweight', "
                          "'kg', 80, '2026-11-01', 'down', 75, '2027-01-01', 'open', '', ?, ?, ?)",
                          (user, NOW, NOW, marked))
        c.exec_driver_sql("INSERT INTO saved_food (id, user_id, name, name_key, serving_label, kcal_per_serving, "
                          "created_at, updated_at, deleted_at, is_sample) VALUES (?, ?, ?, ?, '1 bowl', 180, ?, ?, ?, ?)",
                          (n, user, f"Food {n}", f"food {n}", *times))
        c.exec_driver_sql(f"INSERT INTO food_entry (user_id, date, meal, description, kcal, source, estimated, "
                          f"saved_food_id, servings, dedupe_key, {entry}) VALUES (?, '2026-11-09', 'lunch', 'Food', "
                          "180, 'saved_food', 0, ?, 1, 'k', 'web', ?, ?, ?, ?)", (user, n, *times))
    # Person 1's own entry, logged against the marked saved food 1: it stays, without the link.
    c.exec_driver_sql(f"INSERT INTO food_entry (id, user_id, date, meal, description, kcal, source, estimated, "
                      f"saved_food_id, servings, dedupe_key, {entry}) VALUES (99, 1, '2026-11-10', 'dinner', 'Food 1', "
                      "540, 'saved_food', 0, 1, 3, 'k', 'web', ?, ?, NULL, 0)", (NOW, NOW))


def _left(c) -> dict:
    """Every table's rows by (user, id), and the sets by session."""
    out = {t: sorted(tuple(r) for r in c.exec_driver_sql(f"SELECT user_id, id FROM {t} ORDER BY id").all())
           for t in MARKED_TABLES}
    out["lift_set"] = [r[0] for r in c.exec_driver_sql("SELECT session_id FROM lift_set ORDER BY session_id").all()]
    return out


def test_0004_deletes_every_marked_row_then_drops_the_column_and_comes_down_again():
    address = _sqlite_at("marked-rows.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, "0003")
    engine = make_engine(address)
    try:
        with engine.begin() as c:
            _rows_at_0003(c)
        with engine.connect() as c:
            before = {t: c.exec_driver_sql(f"SELECT count(*) FROM {t}").scalar() for t in MARKED_TABLES}
            indexes = {t: {i["name"] for i in inspect(c).get_indexes(t)} for t in MARKED_TABLES}
            keys = {t: inspect(c).get_foreign_keys(t) for t in (*MARKED_TABLES, "lift_set")}
        assert before["food_entry"] == 5 and all(before[t] == 4 for t in MARKED_TABLES if t != "food_entry")
        command.upgrade(cfg, "0004")
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0004"
            left = _left(c)
            link = c.exec_driver_sql("SELECT saved_food_id, kcal, servings, source FROM food_entry WHERE id = 99").one()
            assert {t: {i["name"] for i in inspect(c).get_indexes(t)} for t in MARKED_TABLES} == indexes
            assert {t: inspect(c).get_foreign_keys(t) for t in (*MARKED_TABLES, "lift_set")} == keys
        # Rows 1 and 3 of each kind were marked, live and deleted: both go. Row 2 (person 1's own) and row 4
        # (person 2's) stay, a session's sets with it.
        for table in ("lift_session", "saved_food"):
            assert left[table] == [(1, 2), (2, 4)], table
        for table in ("bodyweight", "measurement", "cardio_entry", "goal"):
            assert [user for user, _ in left[table]] == [1, 2], table
        assert left["lift_set"] == [2, 4]
        assert [user for user, _ in left["food_entry"]] == [1, 1, 2] and (1, 99) in left["food_entry"]
        assert tuple(link) == (None, 540, 3, "saved_food")   # keeps its numbers and tag, loses the link
        for table in MARKED_TABLES:
            assert "is_sample" not in _columns(engine, table), table
        with pytest.raises(IntegrityError), engine.begin() as c:   # the live weigh-in index still holds
            c.exec_driver_sql("INSERT INTO bodyweight (user_id, date, kg, dedupe_key, logged_by, created_at, "
                              "updated_at) VALUES (1, '2026-11-02', 81, 'k', 'web', ?, ?)", (NOW, NOW))
        command.downgrade(cfg, "0003")
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0003"
            for table in MARKED_TABLES:
                assert c.exec_driver_sql(f"SELECT DISTINCT is_sample FROM {table}").scalars().all() == [0], table
            assert _left(c) == left   # the deleted rows stay deleted
        command.upgrade(cfg, "head")
        assert all("is_sample" not in _columns(engine, t) for t in MARKED_TABLES)
    finally:
        engine.dispose()


def test_0004_applies_on_an_empty_database_and_follows_0003():
    address = _sqlite_at("empty-0004.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, "0003")
    command.upgrade(cfg, "0004")
    engine = make_engine(address)
    try:
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0004"
        assert all("is_sample" not in _columns(engine, t) for t in MARKED_TABLES)
    finally:
        engine.dispose()
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("0004").down_revision == "0003" and script.get_current_head() == "0004"


def test_the_postgres_sql_deletes_the_marked_rows_and_drops_the_columns():
    cfg = alembic_config("postgresql+psycopg://spotter@db.invalid/spotter")
    out = io.StringIO()
    with redirect_stdout(out):
        command.upgrade(cfg, "0003:0004", sql=True)
    sql = out.getvalue()
    assert "DELETE FROM lift_set WHERE lift_set.session_id IN (SELECT lift_session.id" in sql
    assert "UPDATE food_entry SET saved_food_id=NULL WHERE food_entry.saved_food_id IN (SELECT saved_food.id" in sql
    for table in MARKED_TABLES:
        assert f"DELETE FROM {table} WHERE {table}.is_sample IS true" in sql, table
        assert f"ALTER TABLE {table} DROP COLUMN is_sample" in sql, table
    # Sets first, the saved foods last: after the entries that may name them, and after the links are cleared.
    assert sql.index("DELETE FROM lift_set") < sql.index("DELETE FROM lift_session")
    assert sql.index("DELETE FROM food_entry") < sql.index("UPDATE food_entry") < sql.index("DELETE FROM saved_food")
    assert sql.index("DELETE FROM saved_food") < sql.index("DROP COLUMN")
    down = io.StringIO()
    with redirect_stdout(down):
        command.downgrade(cfg, "0004:0003", sql=True)
    for table in MARKED_TABLES:
        assert f"ALTER TABLE {table} ADD COLUMN is_sample BOOLEAN DEFAULT false NOT NULL" in down.getvalue(), table
