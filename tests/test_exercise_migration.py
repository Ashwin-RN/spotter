"""The exercise list's key and sets of time and distance, package C4a.1 (contract exercise-list.md, "Migrations 0013
and 0014", R2, R7): migration 0013 up, down and up over rows of every kind for two people; its keys, its stops and its
rules; the offline Postgres SQL; the sync by key; and the set's two new columns in log_lifts' and get_log's sets.

The up, down and up tests build their own SQLite files. The services write only at the head: the models are the
head's, so a database is taken down after the writes to stand at 0012.
"""

import importlib.util
import io
from contextlib import redirect_stdout

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, select
from sqlalchemy.exc import IntegrityError

from app import db, exercises, mcp_schemas, services
from app.config import ROOT
from app.db import make_engine
from app.models import Exercise, LiftSession, LiftSet
from app.services import common, lifts

from .conftest import alembic_config
from .test_hardening_schema import (
    NOW, UQ_SET_NUMBER, _agree, _key_check, _master, _plain, _sqlite_at, _version, before_0013, write_everything,
)

UP, DOWN = "0013", "0012"
NEW_COLUMNS = {"exercise": ("key", "retired"), "lift_set": ("time_s", "distance_m")}
LIFT_SET_RULES = {"ck_lift_set_weight_kg", "ck_lift_set_reps", "ck_lift_set_set_number", "ck_lift_set_time_s",
                  "ck_lift_set_distance_m", "ck_lift_set_measure"}
POSTGRES = "postgresql+psycopg://spotter@db.invalid/spotter"  # offline: never connects


def _migration():
    path = ROOT / "migrations" / "versions" / "0013_exercise_list.py"
    spec = importlib.util.spec_from_file_location("migration_0013", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _named(engine: Engine) -> dict[str, list[dict]]:
    """Every table's rows by id, each row as {column: value}."""
    with engine.connect() as c:
        names = [n for n in inspect(c).get_table_names() if n != "alembic_version"]
        return {n: [dict(r._mapping) for r in c.exec_driver_sql(f'SELECT * FROM "{n}" ORDER BY 1').all()]
                for n in names}


def _without_0013(rows: dict[str, list[dict]]) -> dict[str, list[dict]]:
    return {t: [{k: v for k, v in r.items() if k not in NEW_COLUMNS.get(t, ())} for r in rs] for t, rs in rows.items()}


def _shape(address: str) -> dict:
    engine = make_engine(address)
    try:
        with engine.connect() as c:
            found = inspect(c)
            return _plain({t: (found.get_columns(t), found.get_pk_constraint(t), found.get_foreign_keys(t),
                               found.get_unique_constraints(t), found.get_indexes(t), found.get_check_constraints(t))
                           for t in found.get_table_names()})
    finally:
        engine.dispose()


def _exec(engine: Engine, sql: str, *params) -> None:
    with engine.begin() as c:
        c.exec_driver_sql(sql, params)


# ---------------------------------------------------------------- 0013 up, down and up over rows of every kind

def test_0013_up_down_and_up_over_rows_of_every_kind_for_two_people_keeps_every_row(monkeypatch):
    address = _sqlite_at("exercise-list.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, UP)
    people = write_everything(address, monkeypatch)
    engine = make_engine(address)
    try:
        at_head, master_head = _named(engine), _master(engine)
        assert {t: len(rows) > 0 for t, rows in at_head.items()} == {t: True for t in at_head}  # every table
        assert {r["user_id"] for r in at_head["lift_session"]} == set(people) and len(at_head["lift_set"]) == 8
        assert {r["name"]: r["key"] for r in at_head["exercise"]} == {r["name"]: r["key"] for r in exercises.rows()}
        assert {r["retired"] for r in at_head["exercise"]} == {0}
        assert all(r["time_s"] is None and r["distance_m"] is None for r in at_head["lift_set"])
        command.downgrade(cfg, DOWN)
        assert _version(engine) == DOWN
        at_0012 = _named(engine)
        assert at_0012 == _without_0013(at_head)  # every row and set kept, less the four columns
        assert _key_check(engine) == [] and before_0013(_agree(engine)) == []
        plain = _sqlite_at("plain-0012.db")
        command.upgrade(alembic_config(plain), DOWN)
        assert _shape(address) == _shape(plain)  # down leaves 0012's two tables as 0012 built them
        for _ in range(2):
            command.upgrade(cfg, UP)
            assert _version(engine) == UP
            assert _named(engine) == at_head  # the keys come back the same, nothing retired
            assert _key_check(engine) == [] and _agree(engine) == []
            assert _master(engine) == master_head  # the rebuilt tables come back word for word
            command.downgrade(cfg, DOWN)
            assert _named(engine) == at_0012 and _key_check(engine) == []
    finally:
        engine.dispose()


def test_after_the_rebuild_lift_set_holds_every_rule_of_0012_its_unique_and_indexes_and_exercise_its_key():
    address = _sqlite_at("rebuilt.db")
    command.upgrade(alembic_config(address), UP)
    engine = make_engine(address)
    try:
        with engine.connect() as c:
            found = inspect(c)
            checks = {k["name"]: k["sqltext"] for k in found.get_check_constraints("lift_set")}
            uniques = {u["name"]: u["column_names"] for u in found.get_unique_constraints("lift_set")}
            indexes = {i["name"]: i["column_names"] for i in found.get_indexes("lift_set")}
            keys = {k["name"]: (k["constrained_columns"], k["referred_table"], k["options"])
                    for k in found.get_foreign_keys("lift_set")}
            columns = {col["name"]: col for col in found.get_columns("lift_set")}
            listed = {col["name"]: col for col in found.get_columns("exercise")}
            exercise_uniques = {u["name"]: u["column_names"] for u in found.get_unique_constraints("exercise")}
    finally:
        engine.dispose()
    assert set(checks) == LIFT_SET_RULES
    assert checks["ck_lift_set_weight_kg"] == "weight_kg >= -1000 AND weight_kg <= 1000"
    assert checks["ck_lift_set_reps"] == "reps >= 1 AND reps <= 1000"  # 0012's, unchanged
    assert checks["ck_lift_set_set_number"] == "set_number >= 1 AND set_number <= 1000"
    assert checks["ck_lift_set_time_s"] == "time_s >= 1 AND time_s <= 100000"
    assert checks["ck_lift_set_distance_m"] == "distance_m > 0 AND distance_m <= 100000"
    assert checks["ck_lift_set_measure"] == "reps IS NOT NULL OR time_s IS NOT NULL OR distance_m IS NOT NULL"
    assert uniques == {UQ_SET_NUMBER: ["session_id", "exercise_id", "set_number"]}
    assert indexes == {"ix_lift_set_session_id": ["session_id"], "ix_lift_set_exercise_id": ["exercise_id"]}
    assert keys == {"fk_lift_set_session_id_lift_session": (["session_id"], "lift_session", {"ondelete": "CASCADE"}),
                    "fk_lift_set_exercise_id_exercise": (["exercise_id"], "exercise", {})}
    assert [n for n in ("reps", "time_s", "distance_m", "weight_kg") if columns[n]["nullable"]] == [
        "reps", "time_s", "distance_m"]
    assert (type(columns["time_s"]["type"]).__name__, type(columns["distance_m"]["type"]).__name__) == (
        "INTEGER", "FLOAT")
    assert (listed["key"]["nullable"], listed["key"]["type"].length) == (False, 64)
    assert listed["retired"]["nullable"] is False and listed["retired"]["default"] == "0"
    assert exercise_uniques == {"uq_exercise_name": ["name"], "uq_exercise_key": ["key"]}


# ---------------------------------------------------------------- the rules, by direct insert at the head

def _a_session(engine: Engine) -> tuple[int, int]:
    """A person, a session of theirs and Back Squat's id, by bound parameters only."""
    with engine.begin() as c:
        user = c.exec_driver_sql("INSERT INTO user (provider, subject, claude_can, add_back_exercise_kcal, created_at, "
                                 "updated_at, last_seen_at) VALUES ('workos', 'rules', 'log', 0, '2026-11-10', "
                                 "'2026-11-10', '2026-11-10') RETURNING id").scalar()
        session = c.exec_driver_sql("INSERT INTO lift_session (user_id, date, notes, dedupe_key, logged_by, "
                                    "created_at, updated_at) VALUES (?, '2026-11-09', '', 'k', 'web', '2026-11-10', "
                                    "'2026-11-10') RETURNING id", (user,)).scalar()
        lift = c.exec_driver_sql("SELECT id FROM exercise WHERE name = 'Back Squat'").scalar()
    return session, lift


SET = "INSERT INTO lift_set (session_id, exercise_id, set_number, weight_kg, reps, time_s, distance_m) " \
      "VALUES (?, ?, ?, ?, ?, ?, ?)"


@pytest.fixture
def at_head():
    address = _sqlite_at("rules.db")
    command.upgrade(alembic_config(address), UP)
    engine = make_engine(address)
    with engine.begin() as c:
        for row in exercises.rows():
            c.exec_driver_sql("INSERT INTO exercise (key, name, kind, category, met, compendium_code) "
                              "VALUES (?, ?, ?, ?, ?, ?)", tuple(row[k] for k in (
                                  "key", "name", "kind", "category", "met", "compendium_code")))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("values", [
    (-20, 8, None, None),       # help on an assisted lift
    (-1000, 8, None, None),     # the rule's edge
    (0, None, 60, None),        # a hold
    (32, None, 45, 40),         # a carry
    (0, None, None, 0.5),       # a distance alone
    (0, None, 100000, None),    # time_s at its ceiling
    (0, None, None, 100000),    # distance_m at its ceiling
    (0, 100, None, None),       # 100 wall balls
])
def test_the_new_rules_take_a_set_of_help_of_time_or_of_distance(at_head, values):
    session, lift = _a_session(at_head)
    _exec(at_head, SET, session, lift, 1, *values)


@pytest.mark.parametrize("values", [
    (-1001, 8, None, None),     # below the weight rule
    (0, None, None, None),      # no reps, no time, no distance
    (0, None, 0, None),         # time_s 0
    (0, None, 100001, None),
    (0, None, None, 0),         # distance_m 0
    (0, None, None, 100000.01),
    (0, 0, None, None),         # reps keeps 0012's 1 to 1,000
])
def test_the_new_rules_refuse_one_step_outside(at_head, values):
    session, lift = _a_session(at_head)
    with pytest.raises(IntegrityError):
        _exec(at_head, SET, session, lift, 1, *values)


# ---------------------------------------------------------------- the keys

def test_the_migration_holds_the_56_live_names_with_their_keys_by_the_rule():
    migration = _migration()
    listed = {row["name"]: row["key"] for row in exercises.rows()}
    # The list holds the 56 live names with their keys, and since the exercise list 211 more (exercise-list.md).
    assert {name: listed[name] for name in migration.LIVE} == migration.LIVE and len(migration.LIVE) == 56
    assert len(listed) == 267
    assert all(migration.key_of(name) == exercises.key_of(name) == key for name, key in listed.items())
    assert migration.revision == UP and migration.down_revision == DOWN
    assert listed["Farmer's Carry"] == "farmers_carry" and listed["Step-Up"] == "step_up"
    for name, key in (("Squat (Barbell)", "squat_barbell"), ("Rock & Roll", "rock_and_roll"),
                      ("Chin-Up / Pull-Up", "chin_up_pull_up"), ("St. Mary’s Row, Wide", "st_marys_row_wide")):
        assert migration.key_of(name) == exercises.key_of(name) == key
    assert all(len(key) <= 64 and key == key.lower() for key in listed.values())


def _at_0012_with(address: str, names: list[str]) -> Engine:
    """A database at 0012 holding the 56 live rows and these names besides, as 0012's sync would leave them."""
    command.upgrade(alembic_config(address), DOWN)
    engine = make_engine(address)
    with engine.begin() as c:
        for name in list(_migration().LIVE) + names:
            c.exec_driver_sql("INSERT INTO exercise (name, kind, category) VALUES (?, 'lift', 'push')", (name,))
    return engine


def test_a_row_not_on_the_list_takes_the_key_its_name_gives_and_only_the_key_is_printed():
    address = _sqlite_at("unknown.db")
    engine = _at_0012_with(address, ["Sled Push (Heavy)", "Zercher Squat"])
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            command.upgrade(alembic_config(address), UP)
        assert out.getvalue().splitlines() == ["0013 keyed an exercise not on the list: sled_push_heavy",
                                               "0013 keyed an exercise not on the list: zercher_squat"]
        assert "Sled Push" not in out.getvalue()
        with engine.connect() as c:
            keys = dict(c.exec_driver_sql("SELECT name, key FROM exercise").all())
        assert keys["Sled Push (Heavy)"] == "sled_push_heavy" and keys["Back Squat"] == "back_squat"
        assert len(keys) == 58 and len(set(keys.values())) == 58
    finally:
        engine.dispose()


def test_two_rows_of_one_key_stop_the_upgrade_and_change_nothing():
    address = _sqlite_at("twins.db")
    engine = _at_0012_with(address, ["Push Up", "push-up ", "Farmers Carry"])
    try:
        before, master = _named(engine), _master(engine)
        out = io.StringIO()
        with pytest.raises(SystemExit) as stopped, redirect_stdout(out):
            command.upgrade(alembic_config(address), UP)
        assert str(stopped.value.code).splitlines() == [
            "0013 stopped: exercise key farmers_carry 2 rows. Nothing was changed.",
            "0013 stopped: exercise key push_up 3 rows. Nothing was changed."]
        assert out.getvalue() == ""  # no key printed: nothing was keyed
        assert _version(engine) == DOWN and _named(engine) == before and _master(engine) == master
        _exec(engine, "DELETE FROM exercise WHERE name IN ('Push Up', 'push-up ', 'Farmers Carry')")
        command.upgrade(alembic_config(address), UP)
        assert _version(engine) == UP
    finally:
        engine.dispose()


# ---------------------------------------------------------------- the downgrade's stops

def test_the_downgrade_stops_on_a_set_with_no_reps_and_on_a_minus_weight_and_runs_once_both_are_gone(at_head):
    session, lift = _a_session(at_head)
    address = str(at_head.url)
    cfg = alembic_config(address)
    _exec(at_head, SET, session, lift, 1, 100, 5, None, None)  # a set of reps: no stop
    _exec(at_head, SET, session, lift, 2, 0, None, 60, None)
    _exec(at_head, SET, session, lift, 3, 0, None, 45, 40)
    before, master = _named(at_head), _master(at_head)
    with pytest.raises(SystemExit) as stopped:
        command.downgrade(cfg, DOWN)
    assert stopped.value.code == "0013 down stopped: lift_set reps 2 rows. Nothing was changed."
    assert _version(at_head) == UP and _named(at_head) == before and _master(at_head) == master
    _exec(at_head, SET, session, lift, 4, -20, 8, None, None)
    with pytest.raises(SystemExit) as stopped:
        command.downgrade(cfg, DOWN)
    assert str(stopped.value.code).splitlines() == [
        "0013 down stopped: lift_set reps 2 rows. Nothing was changed.",
        "0013 down stopped: lift_set weight_kg 1 rows. Nothing was changed."]
    assert _version(at_head) == UP
    _exec(at_head, "DELETE FROM lift_set WHERE reps IS NULL")
    with pytest.raises(SystemExit) as stopped:
        command.downgrade(cfg, DOWN)
    assert stopped.value.code == "0013 down stopped: lift_set weight_kg 1 rows. Nothing was changed."
    _exec(at_head, "DELETE FROM lift_set WHERE weight_kg < 0")
    command.downgrade(cfg, DOWN)
    assert _version(at_head) == DOWN
    with at_head.connect() as c:
        columns = {col["name"]: col["nullable"] for col in inspect(c).get_columns("lift_set")}
        names = {col["name"] for col in inspect(c).get_columns("exercise")}
        exercise_rows = c.exec_driver_sql("SELECT count(*) FROM exercise").scalar()
    assert columns["reps"] is False and "time_s" not in columns and "distance_m" not in columns
    assert "key" not in names and "retired" not in names and exercise_rows == len(exercises.rows())  # the rows stay
    old = "INSERT INTO lift_set (session_id, exercise_id, set_number, weight_kg, reps) VALUES (?, ?, ?, ?, ?)"
    with pytest.raises(IntegrityError):
        _exec(at_head, old, session, lift, 5, -1, 5)  # 0012's weight rule is back
    with pytest.raises(IntegrityError):
        _exec(at_head, old, session, lift, 5, 10, None)  # reps NOT NULL again
    _exec(at_head, old, session, lift, 5, 0, 5)


# ---------------------------------------------------------------- the offline Postgres SQL

def test_the_offline_postgres_sql_holds_the_columns_the_keys_and_the_rules_and_reads_nothing():
    cfg = alembic_config(POSTGRES)
    out = io.StringIO()
    with redirect_stdout(out):
        command.upgrade(cfg, f"{DOWN}:{UP}", sql=True)
    sql = out.getvalue()
    for line in ("ALTER TABLE exercise ADD COLUMN key VARCHAR(64);",
                 "ALTER TABLE exercise ADD COLUMN retired BOOLEAN DEFAULT false NOT NULL;",
                 "UPDATE exercise SET key='back_squat' WHERE exercise.name = 'Back Squat';",
                 "UPDATE exercise SET key='farmers_carry' WHERE exercise.name = 'Farmer''s Carry';",
                 "ALTER TABLE exercise ALTER COLUMN key SET NOT NULL;",
                 "ALTER TABLE exercise ADD CONSTRAINT uq_exercise_key UNIQUE (key);",
                 "ALTER TABLE lift_set ADD COLUMN time_s INTEGER;",
                 "ALTER TABLE lift_set ADD COLUMN distance_m FLOAT;",
                 "ALTER TABLE lift_set ALTER COLUMN reps DROP NOT NULL;",
                 "ALTER TABLE lift_set DROP CONSTRAINT ck_lift_set_weight_kg;",
                 "ALTER TABLE lift_set ADD CONSTRAINT ck_lift_set_weight_kg CHECK (weight_kg >= -1000 AND weight_kg "
                 "<= 1000);",
                 "ALTER TABLE lift_set ADD CONSTRAINT ck_lift_set_time_s CHECK (time_s >= 1 AND time_s <= 100000);",
                 "ALTER TABLE lift_set ADD CONSTRAINT ck_lift_set_distance_m CHECK (distance_m > 0 AND distance_m <= "
                 "100000);",
                 "ALTER TABLE lift_set ADD CONSTRAINT ck_lift_set_measure CHECK (reps IS NOT NULL OR time_s IS NOT "
                 "NULL OR distance_m IS NOT NULL);"):
        assert sql.count(line) == 1, line
    assert sql.count("UPDATE exercise SET key=") == 56
    assert sql.index("ADD COLUMN key") < sql.index("UPDATE exercise") < sql.index("SET NOT NULL")
    assert sql.index("DROP CONSTRAINT ck_lift_set_weight_kg") < sql.index("ADD CONSTRAINT ck_lift_set_weight_kg")
    assert "SELECT" not in sql and "count(" not in sql.lower()  # offline: no read, no count
    assert "CREATE TABLE" not in sql and "DROP TABLE" not in sql and "_alembic_tmp_" not in sql  # nothing rebuilt
    down = io.StringIO()
    with redirect_stdout(down):
        command.downgrade(cfg, f"{UP}:{DOWN}", sql=True)
    back = down.getvalue()
    for line in ("ALTER TABLE lift_set DROP CONSTRAINT ck_lift_set_measure;",
                 "ALTER TABLE lift_set DROP CONSTRAINT ck_lift_set_distance_m;",
                 "ALTER TABLE lift_set DROP CONSTRAINT ck_lift_set_time_s;",
                 "ALTER TABLE lift_set DROP CONSTRAINT ck_lift_set_weight_kg;",
                 "ALTER TABLE lift_set ADD CONSTRAINT ck_lift_set_weight_kg CHECK (weight_kg >= 0 AND weight_kg <= "
                 "1000);",
                 "ALTER TABLE lift_set ALTER COLUMN reps SET NOT NULL;",
                 "ALTER TABLE lift_set DROP COLUMN distance_m;",
                 "ALTER TABLE lift_set DROP COLUMN time_s;",
                 "ALTER TABLE exercise DROP CONSTRAINT uq_exercise_key;",
                 "ALTER TABLE exercise DROP COLUMN retired;",
                 "ALTER TABLE exercise DROP COLUMN key;"):
        assert back.count(line) == 1, line
    assert "SELECT" not in back and "DELETE" not in back and "UPDATE exercise" not in back  # no row goes


# ---------------------------------------------------------------- the sync by key (R2)

def _listed_as(monkeypatch, change) -> None:
    """The list as change(rows) gives it, for the sync."""
    listed = exercises.rows()
    monkeypatch.setattr(exercises, "rows", lambda: change([dict(r) for r in listed]))


def _row(db_session, key: str) -> Exercise:
    db_session.expire_all()
    return db_session.scalar(select(Exercise).where(Exercise.key == key))


@pytest.fixture
def fixed_now(monkeypatch):
    """Today is 2026-11-10 for every write and read here, never the real date."""
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


def _person(make_user) -> int:
    return make_user(time_zone="UTC").id


def _squat_session(uid: int, name: str = "Back Squat") -> dict:
    r = services.log_lifts(uid, {"date": "2026-11-09", "sets": [{"exercise": name, "weight_kg": 100, "reps": 5}]},
                           "claude")
    assert "error" not in r, r
    return r


def test_a_renamed_row_keeps_its_id_its_key_and_its_sets(fixed_now, monkeypatch, make_user, db_session):
    uid = _person(make_user)
    logged = _squat_session(uid)
    squat = _row(db_session, "back_squat")
    _listed_as(monkeypatch, lambda rows: [{**r, "name": "Barbell Back Squat", "category": "hinge"}
                                          if r["key"] == "back_squat" else r for r in rows])
    assert exercises.sync(db_session) == 0
    db_session.commit()
    renamed = _row(db_session, "back_squat")
    assert (renamed.id, renamed.name, renamed.category, renamed.kind, renamed.retired) == (
        squat.id, "Barbell Back Squat", "hinge", "lift", False)
    sets = db_session.scalars(select(LiftSet).where(LiftSet.session_id == logged["id"])).all()
    assert [(x.exercise_id, x.exercise.name) for x in sets] == [(squat.id, "Barbell Back Squat")]
    assert exercises.sync(db_session) == 0  # twice changes nothing
    db_session.commit()
    assert _row(db_session, "back_squat").name == "Barbell Back Squat"


def test_an_unlisted_key_is_retired_never_matched_its_history_still_read_and_listed_again_comes_back(
        fixed_now, monkeypatch, make_user, db_session):
    uid = _person(make_user)
    logged = _squat_session(uid, "Plank")
    plank = _row(db_session, "plank")
    listed = exercises.rows()
    _listed_as(monkeypatch, lambda rows: [r for r in rows if r["key"] != "plank"])
    assert exercises.sync(db_session) == 0
    db_session.commit()
    assert _row(db_session, "plank").retired is True
    assert "Plank" not in [e.name for e in common.exercises(db_session)]
    assert "Plank" not in [e.name for e in common.exercises(db_session, "lift")]
    assert len(common.exercises(db_session)) == len(listed) - 1
    refused = services.log_lifts(uid, {"sets": [{"exercise": "Plank", "weight_kg": 0, "reps": 30}]}, "claude")
    assert refused["error"] == "unknown_exercise", refused
    day = services.get_log(uid, "2026-11-09")
    [session] = day["days"][0]["sessions"]
    assert [x["exercise"] for x in session["sets"]] == ["Plank"]  # the history still reads it
    with db_session.no_autoflush:
        held = db_session.get(LiftSession, logged["id"])
        assert [x.exercise.name for x in held.sets] == ["Plank"]
    monkeypatch.setattr(exercises, "rows", lambda: listed)
    assert exercises.sync(db_session) == 0
    db_session.commit()
    back = _row(db_session, "plank")
    assert (back.id, back.retired) == (plank.id, False)
    assert "Plank" in [e.name for e in common.exercises(db_session, "lift")]


def test_a_rename_onto_a_held_name_stops_the_sync_with_its_line_before_any_change(monkeypatch, db_session):
    before = [(e.id, e.key, e.name, e.category, e.retired) for e in db_session.scalars(select(Exercise))]
    _listed_as(monkeypatch, lambda rows: [{**r, "name": "Front Squat"} if r["key"] == "back_squat"
                                          else {**r, "category": "core"} for r in rows if r["key"] != "plank"])
    with pytest.raises(exercises.SyncStopped) as stopped:
        exercises.sync(db_session)
    assert str(stopped.value) == ("Exercise sync stopped: back_squat and front_squat would hold one name. "
                                  "Nothing was changed.")
    db_session.rollback()
    assert [(e.id, e.key, e.name, e.category, e.retired) for e in db_session.scalars(select(Exercise))] == before


def test_a_new_key_whose_name_another_row_holds_stops_the_sync_too_and_the_deploy_step_exits_with_its_line(
        monkeypatch, db_session):
    _listed_as(monkeypatch, lambda rows: [{**r, "key": "back_squat_barbell"} if r["key"] == "back_squat" else r
                                          for r in rows])
    with pytest.raises(SystemExit) as stopped:
        exercises.main()
    assert stopped.value.code == ("Exercise sync stopped: back_squat_barbell and back_squat would hold one name. "
                                  "Nothing was changed.")
    db_session.expire_all()
    assert db_session.scalar(select(Exercise.key).where(Exercise.name == "Back Squat")) == "back_squat"
    assert db_session.scalar(select(Exercise).where(Exercise.key == "back_squat_barbell")) is None


def test_the_sync_fills_an_empty_table_with_every_key_and_none_retired(db_session):
    db_session.execute(Exercise.__table__.delete())
    db_session.commit()
    assert exercises.sync(db_session) == len(exercises.rows()) == 267
    db_session.commit()
    held = {e.key: (e.name, e.retired) for e in db_session.scalars(select(Exercise))}
    assert held == {r["key"]: (r["name"], False) for r in exercises.rows()}


# ---------------------------------------------------------------- the set's new columns in the results (R7)

def test_log_lifts_and_get_log_give_each_set_its_time_and_distance_as_null(fixed_now, make_user):
    uid = _person(make_user)
    logged = _squat_session(uid)
    assert logged["session"]["sets"] == [{"exercise": "Back Squat", "set_number": 1, "weight_kg": 100, "reps": 5,
                                          "time_s": None, "distance_m": None}]
    day = services.get_log(uid, "2026-11-09")
    [session] = day["days"][0]["sessions"]
    assert session["sets"] == logged["session"]["sets"]


def test_a_set_of_time_reads_back_with_null_reps_and_fits_the_published_set(fixed_now, make_user, db_session):
    uid = _person(make_user)
    logged = _squat_session(uid, "Plank")
    plank = _row(db_session, "plank")
    db_session.add(LiftSet(session_id=logged["id"], exercise_id=plank.id, set_number=2, weight_kg=0, reps=None,
                           time_s=60))
    db_session.commit()
    db_session.expire_all()
    sets = lifts.session_dict(db_session.get(LiftSession, logged["id"]))["sets"]
    assert sets[1] == {"exercise": "Plank", "set_number": 2, "weight_kg": 0, "reps": None, "time_s": 60,
                       "distance_m": None}
    assert mcp_schemas.SetOut.model_validate(sets[1]).model_dump() == sets[1]
    schema = mcp_schemas.SetOut.model_json_schema()
    assert schema["required"] == ["exercise", "set_number", "weight_kg"]
    assert set(schema["properties"]) == {"exercise", "set_number", "weight_kg", "reps", "time_s", "distance_m"}
    # An answer from before this deploy, with no time_s or distance_m, still fits.
    assert mcp_schemas.SetOut.model_validate(
        {"exercise": "Plank", "set_number": 1, "weight_kg": 0, "reps": 30}).time_s is None


def test_deploy_1_still_writes_no_set_without_reps(fixed_now, make_user):
    uid = _person(make_user)
    for sent in ({"exercise": "Plank", "weight_kg": 0}, {"exercise": "Plank", "weight_kg": 0, "time_s": 60}):
        r = services.log_lifts(uid, {"sets": [sent]}, "claude")
        assert "error" in r or "errors" in r, r
    r = services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_kg": -20, "reps": 5}]}, "claude")
    assert "error" in r or "errors" in r, r  # a minus weight waits for deploy 2 too
