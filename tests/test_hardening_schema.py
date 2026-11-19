"""Hardening, package H2 (contract hardening.md, decisions 10 to 16): migration 0012's rules, read from the built
database and tried by direct insert; 0012 up, down and up over rows the services wrote at 0011; its stops; foreign
keys off while a migration runs on SQLite; the wider saved food key; the call log's id; and /ready.

The tests on the shared test database use bound parameters and the inspector only, so they run on Postgres too. The
up, down and up tests build their own SQLite files."""

import itertools
import math
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, event, inspect, select, text
from sqlalchemy import column as sa_column, table as sa_table
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import configure_mappers

from app import config, db, exercises, main, models, rules, services, web
from app.config import DEFAULT_DATABASE_URL, ROOT
from app.db import make_engine
from app.models import Base, CallLog, Exercise, FoodEntry, Routine, SavedFood, UsdaFood, UsdaSearch, User, WriteKey
from app.services import usda

from .conftest import TEST_DIR, alembic_config, settings
from .test_web import client, seam, sign_in, web_client  # noqa: F401 - fixtures

NOW = datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)
HARDENING = "0012"  # the revision this file's migration tests run to
HEAD = "0013"  # the code's head, which /ready reads (exercise-list.md)

# ---------------------------------------------------------------- the rules of D12, as the contract lists them

# (table, column, low, low taken, high, whole): each number rule. "low taken" False is "above low".
NUMBER_RULES = (
    ("bodyweight", "kg", 0, False, 1000, False),
    ("lift_set", "weight_kg", 0, True, 1000, False),
    ("lift_set", "reps", 1, True, 1000, True),
    ("lift_set", "set_number", 1, True, 1000, True),
    ("measurement", "value", 0, True, 1_000_000, False),
    ("food_entry", "kcal", 0, True, 100_000, False),
    ("food_entry", "servings", 0, False, 1000, False),
    ("food_entry", "grams", 0, False, 100_000, False),
    ("saved_food", "kcal_per_serving", 0, True, 100_000, False),
    ("saved_food", "grams_per_serving", 0, False, 100_000, False),
    ("cardio_entry", "minutes", 0, False, 10_000, False),
    ("cardio_entry", "distance_km", 0, False, 10_000, False),
    ("cardio_entry", "speed_kmh", 0, False, 1000, False),
    ("cardio_entry", "incline_pct", 0, True, 100, False),
    ("cardio_entry", "avg_hr", 1, True, 1000, True),
    ("cardio_entry", "machine_kcal", 0, True, 100_000, True),
    ("cardio_entry", "estimated_kcal", 0, True, 1_000_000, False),
    ("cardio_entry", "estimate_weight_kg", 0, False, 1000, False),
    ("goal", "start_value", 0, True, 1_000_000, False),
    ("goal", "target_value", 0, True, 1_000_000, False),
    ("goal", "distance_km", 0, False, 10_000, False),
    ("user", "calorie_target_kcal", 0, False, 100_000, True),
    ("user", "target_sessions_per_week", 0, True, 1000, True),
    ("user", "target_cardio_minutes_per_week", 0, True, 100_000, True),
)
# (table, column, every value on the list, one off it): the closed lists, the owner's pick 1.
LIST_RULES = tuple((t, "logged_by", models.LOGGED_BY, "robot")
                   for t in ("lift_session", "bodyweight", "measurement", "food_entry", "cardio_entry")) + (
    ("user", "claude_can", ("log_delete", "log"), "read"),
    ("user", "weight_unit", ("kg", "lb", None), "st"),
    ("user", "distance_unit", ("km", "mi", None), "yd"),
    ("goal", "direction", ("up", "down"), "flat"),
    ("goal", "status", ("open", "hit", "dropped"), "done"),
    ("entry_version", "by", ("web", "claude"), "import"),
    ("entry_version", "why", ("edit", "undo"), "redo"),
)
SHAPE_RULES = (("goal", "exercise"), ("goal", "distance"), ("goal", "measurement"))
CHECKS = sorted({f"ck_{t}_{c}" for t, c, *_ in NUMBER_RULES} | {f"ck_{t}_{c}" for t, c, *_ in LIST_RULES}
                | {f"ck_{t}_{c}" for t, c in SHAPE_RULES})
UQ_SET_NUMBER = "uq_lift_set_session_id_exercise_id_set_number"
UQ_SAVED_FOOD = "uq_saved_food_user_id_id"
FK_OWNER = "fk_food_entry_user_id_saved_food"
SWEEP_INDEXES = {"write_key": "ix_write_key_created_at", "entry_version": "ix_entry_version_created_at",
                 "feedback": "ix_feedback_created_at"}
# The ten tables 0012 rebuilds on SQLite (decision 16), the order it rebuilds them.
REBUILT = ("user", "lift_session", "lift_set", "bodyweight", "measurement", "goal", "saved_food", "food_entry",
           "cardio_entry", "entry_version")

# Migration 0013 (exercise-list.md): lift_set's weight_kg rule widens to -1,000, and two number rules and the measure
# rule join it. The lists above stay 0012's; the built database is at the head, so its tests read these.
NUMBER_RULES_AT_HEAD = tuple(("lift_set", "weight_kg", -1000, True, 1000, False) if r[:2] == ("lift_set", "weight_kg")
                             else r for r in NUMBER_RULES) + (
    ("lift_set", "time_s", 1, True, 100_000, True),
    ("lift_set", "distance_m", 0, False, 100_000, False),
)
CHECKS_AT_HEAD = sorted({*CHECKS, "ck_lift_set_time_s", "ck_lift_set_distance_m", "ck_lift_set_measure"})


def test_the_lists_are_the_contract_s():
    assert len(NUMBER_RULES) == 24 and len(LIST_RULES) == 12 and len(CHECKS) == 39
    assert sorted({t for t, *_ in NUMBER_RULES} | {t for t, *_ in LIST_RULES}) == sorted(REBUILT)
    assert len(NUMBER_RULES_AT_HEAD) == 26 and len(CHECKS_AT_HEAD) == 42


def _checks(connection) -> dict[str, set[str]]:
    found = inspect(connection)
    return {t: {c["name"] for c in found.get_check_constraints(t)} for t in found.get_table_names()
            if found.get_check_constraints(t)}


def test_every_rule_is_in_the_built_database():
    with db.get_engine().connect() as c:
        checks = _checks(c)
        found = inspect(c)
        uniques = {t: {u["name"]: u["column_names"] for u in found.get_unique_constraints(t)}
                   for t in ("lift_set", "saved_food")}
        keys = {k["name"]: k for k in found.get_foreign_keys("food_entry")}
        indexes = {t: {i["name"]: i["column_names"] for i in found.get_indexes(t)} for t in SWEEP_INDEXES}
        key_width = {col["name"]: col for col in found.get_columns("saved_food")}["name_key"]["type"].length
    assert sorted(name for names in checks.values() for name in names) == CHECKS_AT_HEAD
    assert set(checks) == set(REBUILT)
    for name in CHECKS_AT_HEAD:
        assert any(name.startswith(f"ck_{t}_") and name in checks[t] for t in checks), name
    assert uniques["lift_set"][UQ_SET_NUMBER] == ["session_id", "exercise_id", "set_number"]
    assert uniques["saved_food"][UQ_SAVED_FOOD] == ["user_id", "id"]
    owner = keys[FK_OWNER]
    assert (owner["constrained_columns"], owner["referred_table"], owner["referred_columns"]) == (
        ["user_id", "saved_food_id"], "saved_food", ["user_id", "id"])
    assert owner["options"].get("deferrable") is True and owner["options"].get("initially") == "DEFERRED"
    assert "ondelete" not in owner["options"]  # no action of its own
    alone = keys["fk_food_entry_saved_food_id_saved_food"]
    assert alone["options"]["ondelete"] == "SET NULL"
    assert {t: names[n] for t, n in SWEEP_INDEXES.items() for names in [indexes[t]]} == {
        t: ["created_at"] for t in SWEEP_INDEXES}
    assert key_width == 384


def test_no_rule_on_the_lists_meant_to_grow_nor_on_the_call_log():
    with db.get_engine().connect() as c:
        checks = _checks(c)
    named = {name for names in checks.values() for name in names}
    for left_out in ("ck_goal_kind", "ck_measurement_kind", "ck_measurement_unit", "ck_food_entry_meal",
                     "ck_food_entry_source", "ck_feedback_topic", "ck_exercise_kind", "ck_exercise_category"):
        assert left_out not in named
    assert "call_log" not in checks and "feedback" not in checks and "exercise" not in checks


# ---------------------------------------------------------------- direct inserts against the rules

_n = itertools.count(1)


def _people(c) -> tuple[int, int]:
    users = Base.metadata.tables["user"]
    ids = []
    for _ in range(2):
        ids.append(c.execute(users.insert().values(provider="workos", subject=f"rule_{next(_n)}")
                             .returning(users.c.id)).scalar_one())
    return ids[0], ids[1]


def _ids(c) -> dict:
    """Two people, a lift, a cardio activity, a session and a saved food of the first person's."""
    a, b = _people(c)
    lift = c.execute(select(Exercise.id).where(Exercise.name == "Back Squat")).scalar_one()
    run = c.execute(select(Exercise.id).where(Exercise.name == "Outdoor Run")).scalar_one()
    session = _insert(c, "lift_session", {"user_id": a})
    food = _insert(c, "saved_food", {"user_id": a})
    return {"a": a, "b": b, "lift": lift, "run": run, "session": session, "food": food}


def _base(table: str, ids: dict) -> dict:
    n = next(_n)
    day = date(2020, 1, 1).toordinal() + n
    on = date.fromordinal(day)
    entry = {"logged_by": "web", "dedupe_key": f"k{n}", "date": on}
    rows = {
        "user": {"provider": "workos", "subject": f"rule_{n}"},
        "lift_session": {"user_id": ids.get("a"), "notes": "", **entry},
        "lift_set": {"session_id": ids.get("session"), "exercise_id": ids.get("lift"), "set_number": 100 + n % 800,
                     "weight_kg": 100, "reps": 5},
        "bodyweight": {"user_id": ids.get("a"), "kg": 80, **entry},
        "measurement": {"user_id": ids.get("a"), "kind": "waist", "value": 84, "unit": "cm", **entry},
        "food_entry": {"user_id": ids.get("a"), "meal": "lunch", "description": "Dal", "kcal": 180,
                       "source": "you_said", "estimated": False, **entry},
        "saved_food": {"user_id": ids.get("a"), "name": f"Food {n}", "name_key": f"food {n}", "serving_label": "1 bowl",
                       "kcal_per_serving": 180},
        "cardio_entry": {"user_id": ids.get("a"), "exercise_id": ids.get("run"), "minutes": 30, "notes": "", **entry},
        "goal": {"user_id": ids.get("a"), "kind": "bodyweight", "unit": "kg", "start_value": 80,
                 "start_date": date(2026, 11, 1), "direction": "down", "status": "open", "text": ""},
        "entry_version": {"user_id": ids.get("a"), "kind": "bodyweight", "entry_id": 1, "by": "web", "why": "edit",
                          "before": "{}"},
    }
    return rows[table]


def _insert(c, table: str, values: dict, ids: dict | None = None):
    t = Base.metadata.tables[table]
    row = {**_base(table, ids or values), **values}
    return c.execute(t.insert().values(**row).returning(*t.primary_key.columns)).scalar_one()


def _taken(engine: Engine, table: str, ids: dict, values: dict) -> None:
    with engine.begin() as c:
        _insert(c, table, values, ids)


def _refused(engine: Engine, table: str, ids: dict, values: dict) -> None:
    with pytest.raises(IntegrityError), engine.begin() as c:
        _insert(c, table, values, ids)


@pytest.fixture
def ids():
    with db.get_engine().begin() as c:
        return _ids(c)


@pytest.mark.parametrize("rule", NUMBER_RULES_AT_HEAD, ids=lambda r: f"{r[0]}.{r[1]}")
def test_each_number_rule_takes_its_edges_and_refuses_one_step_outside(ids, rule):
    table, column, low, low_taken, high, whole = rule
    step = 1 if whole else 0.01
    engine = db.get_engine()
    _taken(engine, table, ids, {column: high})
    _refused(engine, table, ids, {column: high + step})
    if low_taken:
        _taken(engine, table, ids, {column: low})
        _refused(engine, table, ids, {column: low - step})
    else:
        _taken(engine, table, ids, {column: low + step})
        _refused(engine, table, ids, {column: low})
    if -high < low:  # lift_set.weight_kg's low is -high since 0013: its edges above say it
        _refused(engine, table, ids, {column: -high})
    if not whole:
        _refused(engine, table, ids, {column: math.inf})  # what "above 0" alone would let through
        if engine.dialect.name == "postgresql":
            _refused(engine, table, ids, {column: math.nan})  # SQLite stores a NaN as null


@pytest.mark.parametrize("rule", LIST_RULES, ids=lambda r: f"{r[0]}.{r[1]}")
def test_each_list_rule_takes_its_values_and_refuses_one_off_its_list(ids, rule):
    table, column, allowed, off = rule
    engine = db.get_engine()
    for value in allowed:
        _taken(engine, table, ids, {column: value})
    _refused(engine, table, ids, {column: off})
    _refused(engine, table, ids, {column: allowed[0].upper()})


def test_the_goal_shape_rules_bind_only_their_own_kind(ids):
    engine = db.get_engine()
    lift = {"kind": "lift", "unit": "kg", "direction": "up"}
    _refused(engine, "goal", ids, lift)  # a lift goal with no exercise
    _taken(engine, "goal", ids, {**lift, "exercise_id": ids["lift"]})
    best = {"kind": "cardio_best", "unit": "min", "direction": "down", "exercise_id": ids["run"]}
    _refused(engine, "goal", ids, best)  # no distance
    _refused(engine, "goal", ids, {**best, "exercise_id": None, "distance_km": 5})  # no exercise
    _taken(engine, "goal", ids, {**best, "distance_km": 5})
    measure = {"kind": "measurement", "unit": "cm"}
    _refused(engine, "goal", ids, measure)  # no measurement kind
    _taken(engine, "goal", ids, {**measure, "measurement_kind": "waist"})
    for kind in ("bodyweight", "cardio_weekly", "calories", "sessions", "added_later"):
        _taken(engine, "goal", ids, {"kind": kind})  # a new kind is free


def test_a_duplicate_set_number_is_refused(ids):
    engine = db.get_engine()
    _taken(engine, "lift_set", ids, {"set_number": 1})
    _refused(engine, "lift_set", ids, {"set_number": 1})
    _taken(engine, "lift_set", ids, {"set_number": 2})
    bench = Base.metadata.tables["exercise"]
    with engine.begin() as c:
        other_lift = c.execute(select(bench.c.id).where(bench.c.name == "Bench Press")).scalar_one()
        other_session = _insert(c, "lift_session", {"user_id": ids["a"]}, ids)
    _taken(engine, "lift_set", ids, {"set_number": 1, "exercise_id": other_lift})  # another lift
    _taken(engine, "lift_set", ids, {"set_number": 1, "session_id": other_session})  # another session


def test_a_link_to_another_person_s_saved_food_is_refused_at_commit_not_at_the_statement(ids):
    engine = db.get_engine()
    _taken(engine, "food_entry", ids, {"user_id": ids["a"], "saved_food_id": ids["food"], "source": "saved_food"})
    with engine.connect() as c:
        c.begin()
        _insert(c, "food_entry", {"user_id": ids["b"], "saved_food_id": ids["food"]}, ids)  # the statement is taken
        with pytest.raises(IntegrityError):
            c.commit()
        c.rollback()
    with engine.connect() as c:
        entries = Base.metadata.tables["food_entry"]
        owners = c.execute(select(entries.c.user_id).where(entries.c.saved_food_id == ids["food"])).scalars().all()
    assert owners == [ids["a"]]


def test_a_hard_delete_of_a_linked_saved_food_nulls_only_the_link(ids):
    engine = db.get_engine()
    foods, entries = Base.metadata.tables["saved_food"], Base.metadata.tables["food_entry"]
    with engine.begin() as c:
        entry = _insert(c, "food_entry", {"user_id": ids["a"], "saved_food_id": ids["food"], "servings": 2}, ids)
    with engine.begin() as c:
        c.execute(foods.delete().where(foods.c.id == ids["food"]))
    with engine.connect() as c:
        row = c.execute(select(entries.c.user_id, entries.c.saved_food_id, entries.c.servings, entries.c.kcal)
                        .where(entries.c.id == entry)).one()
    assert tuple(row) == (ids["a"], None, 2, 180)


def test_an_account_with_a_saved_food_and_a_linked_entry_deletes_whole(ids):
    engine = db.get_engine()
    users, foods, entries = (Base.metadata.tables[t] for t in ("user", "saved_food", "food_entry"))
    with engine.begin() as c:
        _insert(c, "food_entry", {"user_id": ids["a"], "saved_food_id": ids["food"]}, ids)
        _insert(c, "food_entry", {"user_id": ids["b"]}, {**ids, "a": ids["b"]})
    with engine.begin() as c:
        c.execute(users.delete().where(users.c.id == ids["a"]))
    with engine.connect() as c:
        assert c.execute(select(foods.c.id)).scalars().all() == []
        assert c.execute(select(entries.c.user_id)).scalars().all() == [ids["b"]]


def test_the_saved_food_relationship_maps_on_saved_food_id_alone(make_user, db_session):
    configure_mappers()
    assert str(FoodEntry.saved_food.property.primaryjoin) == "saved_food.id = food_entry.saved_food_id"
    user = make_user()
    food = services.save_food(user.id, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "web")
    services.log_food(user.id, {"entries": [{"saved_food_id": food["id"], "servings": 2}]}, "web")
    entry = db_session.scalar(select(FoodEntry).where(FoodEntry.user_id == user.id))
    assert entry.saved_food.name == "Roti" and entry.saved_food.id == food["id"]
    # And under -W error, in a fresh interpreter: no warning from the two paths to saved_food.
    code = ("from sqlalchemy.orm import configure_mappers; from app.models import FoodEntry; configure_mappers(); "
            "print(FoodEntry.saved_food.property.primaryjoin)")
    address = f"sqlite:///{(Path(tempfile.mkdtemp(dir=TEST_DIR)) / 'unused.db').as_posix()}"
    done = subprocess.run([sys.executable, "-W", "error", "-c", code], cwd=ROOT, capture_output=True, text=True,
                          timeout=120, env={**_environment(), "DATABASE_URL": address})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "saved_food.id = food_entry.saved_food_id"


def _environment() -> dict:
    import os
    return dict(os.environ)


# ---------------------------------------------------------------- decision 10: the wider key

def test_a_name_of_128_letters_that_fold_to_three_saves_with_a_384_character_key(make_user, db_session):
    user = make_user()
    name = "ΐ" * 128
    assert len(name.casefold()) == 384
    r = services.save_food(user.id, {"name": name, "serving_label": "1 bowl", "kcal_per_serving": 100}, "web")
    assert "error" not in r, r
    key = db_session.scalar(select(SavedFood.name_key).where(SavedFood.id == r["id"]))
    assert key == name.casefold() and len(key) == 384


def test_every_code_point_folds_to_at_most_three_and_both_keys_hold_three_times_their_name():
    most = max(len(chr(c).casefold()) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF)
    assert most == 3
    assert rules.TEXT_LIMITS["name"] == SavedFood.__table__.c.name.type.length == 128
    assert SavedFood.__table__.c.name_key.type.length == most * rules.TEXT_LIMITS["name"] == 384
    assert Routine.__table__.c.name.type.length == rules.ROUTINE_NAME_MAX == 64
    assert Routine.__table__.c.name_key.type.length == 255 >= most * rules.ROUTINE_NAME_MAX


# ---------------------------------------------------------------- decision 13: the call log's id

def test_on_sqlite_a_new_call_log_row_still_gets_its_id(make_user, db_session):
    user = make_user()
    rows = [CallLog(user_id=user.id, function="whoami", started_at=db.utcnow(), ms=1, ok=True, result_bytes=1)
            for _ in range(3)]
    db_session.add_all(rows)
    db_session.commit()
    ids = [r.id for r in rows]
    assert all(isinstance(i, int) and i > 0 for i in ids) and ids == sorted(ids) and len(set(ids)) == 3
    if db_session.get_bind().dialect.name == "sqlite":
        [pk] = [col for col in inspect(db_session.get_bind()).get_columns("call_log") if col["name"] == "id"]
        assert type(pk["type"]).__name__ == "INTEGER"  # the rowid itself: only INTEGER numbers itself


# ---------------------------------------------------------------- 0012 up, down and up over rows written at 0011

def _sqlite_at(name: str) -> str:
    return f"sqlite:///{(Path(tempfile.mkdtemp(dir=TEST_DIR)) / name).as_posix()}"


def _write_at_0011(address: str, monkeypatch) -> dict:
    """Rows of every kind, written by the services into the database at this address, for two people, which is left
    at 0011. The models are the code head's (0013 added columns no older table has), so the services write at the
    head and the database is taken down to 0011 after. The two saved foods whose 130-character keys stop 0012's
    downgrade are saved at 0011 itself, through the service, which reads no table 0012 or 0013 changed."""
    cfg = alembic_config(address)
    command.upgrade(cfg, HEAD)
    people = write_everything(address, monkeypatch)
    command.downgrade(cfg, "0011")
    config.use_settings(settings(database_url=address))
    try:
        for uid in people:
            assert "error" not in services.save_food(uid, {"name": "ß" * 65, "serving_label": "1 bowl",
                                                           "kcal_per_serving": 90}, "web")
    finally:
        db.dispose()
        config.use_settings(settings())
    return {"people": people}


def write_everything(address: str, monkeypatch) -> list[int]:
    """Rows of every kind, written by the services into the database at this address (at the head), for two people.
    The write key, the call-log rows and the two caches go in through the models: no service called here writes
    them on its own. Returns the two people's ids."""
    monkeypatch.setattr(db, "utcnow", lambda: NOW)
    monkeypatch.setattr(usda, "food", lambda fdc: ({"fdc_id": fdc, "description": "Bananas, raw",
                                                   "data_type": "SR Legacy", "kcal_per_100g": 89.0}, None))
    config.use_settings(settings(database_url=address, usda_api_key="test-key"))
    try:
        with db.session_scope() as s:
            exercises.sync(s)
            people = [User(provider="workos", subject=f"seed_{n}", time_zone="UTC", created_at=NOW, updated_at=NOW,
                           last_seen_at=NOW) for n in (1, 2)]
            s.add_all(people)
        for user in people:
            _everything(user.id)
        with db.session_scope() as s:
            s.add(WriteKey(user_id=people[0].id, key="seed-key-01", function="log_food", fingerprint="f"))
            # The shared caches: usda.food is replaced above, so the cache rows go in as the lookups would leave them.
            s.add(UsdaFood(fdc_id=173944, description="Bananas, raw", data_type="SR Legacy", kcal_per_100g=89.0))
            s.add(UsdaSearch(query_key="banana", fdc_ids="[173944]"))
            s.add_all(CallLog(user_id=people[n % 2].id, function="whoami", started_at=NOW, ms=1, ok=True,
                              result_bytes=10) for n in range(100))
        return [u.id for u in people]
    finally:
        db.dispose()
        config.use_settings(settings())


def _everything(uid: int) -> None:
    def ok(r: dict) -> dict:
        assert "error" not in r and "errors" not in r, r
        return r

    squat = {"exercise": "Back Squat", "weight_kg": 100, "reps": 5}
    session = ok(services.log_lifts(uid, {"notes": "heavy", "sets": [squat, squat, {
        "exercise": "Bench Press", "weight_kg": 60, "reps": 8}]}, "claude"))["id"]
    ok(services.log_lifts(uid, {"date": "2026-11-08", "sets": [{"exercise": "Deadlift", "weight_kg": 140,
                                                                "reps": 3}]}, "web"))
    ok(services.edit_entry(uid, "lift_session", session, {"sets": [squat, {**squat, "weight_kg": 105}]}, "claude"))
    ok(services.restore_entry(uid, "lift_session", session, "claude", undo_edit=True))
    weigh_in = ok(services.log_bodyweight(uid, {"kg": 80.4}, "web"))["id"]
    ok(services.edit_entry(uid, "bodyweight", weigh_in, {"kg": 80.5}, "web"))
    gone = ok(services.log_bodyweight(uid, {"kg": 81, "date": "2026-11-01"}, "web"))["id"]
    ok(services.delete_entry(uid, "bodyweight", gone))
    ok(services.log_measurement(uid, {"kind": "waist", "value": 84}, "web"))
    ok(services.log_measurement(uid, {"kind": "steps", "value": 9000, "date": "2026-11-09"}, "claude"))
    roti = ok(services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120,
                                       "grams_per_serving": 40}, "web"))["id"]
    ok(services.log_food(uid, {"entries": [{"saved_food_id": roti, "servings": 2},
                                           {"description": "dal", "kcal": 180, "estimated": True},
                                           {"usda_fdc_id": 173944, "grams": 120}]}, "claude"))
    ok(services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5, "avg_hr": 150}, "web"))
    ok(services.log_cardio(uid, {"exercise": "Treadmill Walk", "minutes": 20, "speed_kmh": 6, "incline_pct": 5,
                                 "machine_kcal": 150}, "claude"))
    for goal in ({"kind": "bodyweight", "target_value": 75, "target_date": "2027-03-31"},
                 {"kind": "lift", "exercise": "Back Squat", "target_value": 140, "target_date": "2027-03-31"},
                 {"kind": "measurement", "measurement_kind": "waist", "target_value": 80, "target_date": "2027-03-31"},
                 {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_value": 25,
                  "target_date": "2027-03-31"},
                 ):
        ok(services.set_goal(uid, goal, "claude"))
    ok(services.update_settings(uid, {"calorie_target_kcal": 2000, "target_sessions_per_week": 3,
                                      "target_cardio_minutes_per_week": 150, "weight_unit": "lb",
                                      "distance_unit": "mi"}, "web"))
    ok(services.set_claude_can(uid, "log"))
    ok(services.save_routine(uid, {"name": "Push day", "lifts": [
        {"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 60}]}, "claude"))
    ok(services.send_feedback(uid, {"text": "The chart is confusing.", "topic": "pages"}))


def _rows(engine: Engine) -> dict[str, list[tuple]]:
    with engine.connect() as c:
        names = [n for n in inspect(c).get_table_names() if n != "alembic_version"]
        return {n: [tuple(r) for r in c.exec_driver_sql(f'SELECT * FROM "{n}" ORDER BY 1').all()] for n in names}


def _master(engine: Engine) -> dict[str, tuple[str, ...]]:
    """Each table's and index's SQL, word for word, its lines in sorted order: a rebuild may list a table's keys in
    another order, which changes nothing."""
    with engine.connect() as c:
        return {name: tuple(sorted(line.strip().rstrip(",").strip() for line in sql.splitlines()))
                for name, sql in c.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name").all()}


def _version(engine: Engine) -> str:
    with engine.connect() as c:
        return c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()


def _key_check(engine: Engine) -> list:
    with engine.connect() as c:
        return c.exec_driver_sql("PRAGMA foreign_key_check").all()


def _agree(engine: Engine) -> list:
    with engine.connect() as c:
        return compare_metadata(MigrationContext.configure(c), Base.metadata)


def test_0012_up_down_and_up_over_rows_the_services_wrote_at_0011(monkeypatch):
    address = _sqlite_at("hardening.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, "0011")
    _write_at_0011(address, monkeypatch)
    engine = make_engine(address)
    try:
        at_0011, master_0011 = _rows(engine), _master(engine)
        assert {t: len(rows) > 0 for t, rows in at_0011.items()} == {t: True for t in at_0011}  # every table
        assert len(at_0011["goal"]) == 8 and len(at_0011["saved_food"]) == 4 and len(at_0011["user"]) == 2
        linked = [r for r in at_0011["food_entry"] if r[8] is not None]
        assert len(linked) == 2  # one a person, each on their own saved food
        command.upgrade(cfg, HARDENING)
        assert _version(engine) == HARDENING
        assert _rows(engine) == at_0011  # every row, set and link kept
        assert _key_check(engine) == []
        assert before_0013(_agree(engine)) == []  # at 0012: less what 0013 adds
        master_up = _master(engine)
        assert {n for n in master_up if master_up[n] != master_0011.get(n)} == set(REBUILT) | set(
            SWEEP_INDEXES.values())
        # The two 130-character keys 0011 took (65 of the letter sharp s) fit 0012 and stop its downgrade.
        with pytest.raises(SystemExit) as stopped:
            command.downgrade(cfg, "0011")
        assert stopped.value.code == "0012 down stopped: name_key 2 rows. Nothing was changed."
        assert _version(engine) == HARDENING and _rows(engine) == at_0011 and _master(engine) == master_up
        with engine.begin() as c:
            c.exec_driver_sql("DELETE FROM saved_food WHERE length(name_key) > 128")
        at_0011 = _rows(engine)
        assert len(at_0011["saved_food"]) == 2
        command.downgrade(cfg, "0011")
        assert _version(engine) == "0011"
        assert _rows(engine) == at_0011 and _key_check(engine) == []
        assert _checks_of(engine) == {}
        command.upgrade(cfg, HARDENING)
        assert _rows(engine) == at_0011 and _key_check(engine) == [] and before_0013(_agree(engine)) == []
        assert _master(engine) == master_up  # the rebuilt tables come back word for word
        with pytest.raises(IntegrityError), engine.begin() as c:  # and refuse as the first upgrade did
            c.exec_driver_sql("INSERT INTO bodyweight (user_id, date, kg, dedupe_key, logged_by, created_at, "
                              "updated_at) VALUES (1, '2026-10-01', -5, 'k', 'web', '2026-11-10', '2026-11-10')")
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.exec_driver_sql("INSERT INTO lift_set (session_id, exercise_id, set_number, weight_kg, reps) "
                              "SELECT session_id, exercise_id, set_number, weight_kg, reps FROM lift_set LIMIT 1")
    finally:
        engine.dispose()


def _checks_of(engine: Engine) -> dict:
    with engine.connect() as c:
        return _checks(c)


def test_the_downgrade_leaves_0011_s_tables_as_0011_built_them():
    """Down from 0012 gives back each rebuilt table's columns, keys, uniques and indexes, and no rule."""
    plain, round_trip = _sqlite_at("plain.db"), _sqlite_at("round-trip.db")
    command.upgrade(alembic_config(plain), "0011")
    command.upgrade(alembic_config(round_trip), HARDENING)
    command.downgrade(alembic_config(round_trip), "0011")
    shapes = []
    for address in (plain, round_trip):
        engine = make_engine(address)
        try:
            with engine.connect() as c:
                found = inspect(c)
                shapes.append({t: (found.get_columns(t), found.get_pk_constraint(t), found.get_foreign_keys(t),
                                   found.get_unique_constraints(t), found.get_indexes(t), found.get_check_constraints(t))
                               for t in found.get_table_names()})
        finally:
            engine.dispose()
    assert _plain(shapes[1]) == _plain(shapes[0])


def _plain(value):
    """What the inspector said, comparable: a WHERE clause as its text, a list of named things by name."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_plain(v) for v in value]
        if items and all(isinstance(v, dict) and "name" in v for v in items):
            items.sort(key=lambda v: str(v["name"]))
        return items
    if hasattr(value, "compile"):
        return str(value)
    return repr(value)


# ---------------------------------------------------------------- decision 15: the stops

def _plant_one_bad_row_a_rule(c) -> list[str]:
    """At 0011: one row breaking each rule, and nothing else; a duplicate set number (two rows) and a food entry on
    another person's saved food. Returns the lines 0012 must stop with."""
    ids = _ids(c)
    for table, column, low, low_taken, high, whole in NUMBER_RULES:
        values = {column: high + 1}
        if table == "goal" and column == "distance_km":
            values["kind"] = "cardio_best"
            values["exercise_id"] = ids["run"]
        _insert(c, table, values, ids)
    for table, column, _, off in LIST_RULES:
        _insert(c, table, {column: off}, ids)
    _insert(c, "goal", {"kind": "lift", "direction": "up"}, ids)  # no exercise
    _insert(c, "goal", {"kind": "cardio_best", "exercise_id": ids["run"]}, ids)  # no distance
    _insert(c, "goal", {"kind": "measurement"}, ids)  # no measurement kind
    _insert(c, "lift_set", {"set_number": 7}, ids)
    _insert(c, "lift_set", {"set_number": 7}, ids)
    _insert(c, "food_entry", {"user_id": ids["b"], "saved_food_id": ids["food"]}, ids)
    return sorted([f"0012 stopped: {name} 1 rows. Nothing was changed." for name in CHECKS]
                  + [f"0012 stopped: {UQ_SET_NUMBER} 2 rows. Nothing was changed.",
                     f"0012 stopped: {FK_OWNER} 1 rows. Nothing was changed."])


def test_a_bad_row_at_0011_stops_the_upgrade_with_one_line_a_rule_and_changes_nothing():
    address = _sqlite_at("bad-rows.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, "0011")
    engine = make_engine(address)
    try:
        with engine.begin() as c:
            _sync(c)
            expected = _plant_one_bad_row_a_rule(c)
        before, master = _rows(engine), _master(engine)
        with pytest.raises(SystemExit) as stopped:
            command.upgrade(cfg, HARDENING)
        assert sorted(str(stopped.value.code).splitlines()) == expected
        assert _version(engine) == "0011" and _master(engine) == master and _rows(engine) == before
        with engine.begin() as c:  # one bad row alone: one line
            c.exec_driver_sql("DELETE FROM food_entry")
            c.exec_driver_sql("DELETE FROM lift_set")
            for table in REBUILT[::-1]:
                if table not in ("user", "bodyweight", "lift_session", "lift_set"):
                    c.exec_driver_sql(f'DELETE FROM "{table}"')
            c.exec_driver_sql("DELETE FROM lift_session")
            c.exec_driver_sql("DELETE FROM bodyweight WHERE kg <> 1001")
            c.exec_driver_sql("UPDATE bodyweight SET kg = -5")
            c.exec_driver_sql("DELETE FROM user WHERE id NOT IN (SELECT user_id FROM bodyweight)")
        with pytest.raises(SystemExit) as stopped:
            command.upgrade(cfg, HARDENING)
        assert stopped.value.code == "0012 stopped: ck_bodyweight_kg 1 rows. Nothing was changed."
        assert _version(engine) == "0011"
        with engine.begin() as c:
            c.exec_driver_sql("UPDATE bodyweight SET kg = 80")
        command.upgrade(cfg, HARDENING)
        assert _version(engine) == HARDENING and _key_check(engine) == []
    finally:
        engine.dispose()


def _sync(c) -> None:
    """The list's rows into a database before 0013, in 0012's columns: key and retired come with 0013."""
    columns = ("name", "kind", "category", "met", "compendium_code")
    table = sa_table("exercise", *(sa_column(name) for name in columns))
    for row in exercises.rows():
        c.execute(table.insert().values(**{name: row[name] for name in columns}))


def test_a_long_name_key_at_0012_stops_the_downgrade_and_once_gone_the_downgrade_runs():
    address = _sqlite_at("long-key.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, HARDENING)
    engine = make_engine(address)
    try:
        with engine.begin() as c:
            _sync(c)
            ids = _ids(c)
            _insert(c, "saved_food", {"name": "ß" * 65, "name_key": "k" * 129}, ids)
            _insert(c, "saved_food", {"name": "Roti", "name_key": "r" * 128}, ids)  # at 128: no stop
        before, master = _rows(engine), _master(engine)
        with pytest.raises(SystemExit) as stopped:
            command.downgrade(cfg, "0011")
        assert stopped.value.code == "0012 down stopped: name_key 1 rows. Nothing was changed."
        assert _version(engine) == HARDENING and _master(engine) == master and _rows(engine) == before
        with engine.begin() as c:
            c.exec_driver_sql("DELETE FROM saved_food WHERE length(name_key) > 128")
        command.downgrade(cfg, "0011")
        assert _version(engine) == "0011"
    finally:
        engine.dispose()


def test_the_offline_sql_counts_nothing():
    cfg = alembic_config("postgresql+psycopg://spotter@db.invalid/spotter")  # offline: never connects
    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out):
        command.upgrade(cfg, "0011:0012", sql=True)
        command.downgrade(cfg, "0012:0011", sql=True)
    assert "count(" not in out.getvalue().lower() and "last_value" not in out.getvalue()


# ---------------------------------------------------------------- decision 16: foreign keys off in a migration

def test_inside_a_migration_on_sqlite_foreign_keys_are_off_and_the_run_still_commits():
    seen: list[int] = []

    def watch(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("CREATE TABLE _ALEMBIC_TMP_"):
            seen.append(conn.connection.dbapi_connection.execute("PRAGMA foreign_keys").fetchone()[0])

    address = _sqlite_at("pragma.db")
    event.listen(Engine, "before_cursor_execute", watch)
    try:
        command.upgrade(alembic_config(address), HARDENING)
    finally:
        event.remove(Engine, "before_cursor_execute", watch)
    assert len(seen) >= len(REBUILT) and set(seen) == {0}
    engine = make_engine(address)
    try:
        assert _version(engine) == HARDENING
        with engine.connect() as c:
            assert set(inspect(c).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
            assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1  # a new connection: on again
    finally:
        engine.dispose()
    with db.get_engine().connect() as c:  # the app's own engine
        if c.dialect.name == "sqlite":
            assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_a_broken_key_after_the_migrations_fails_the_run():
    address = _sqlite_at("broken-key.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, "0011")
    engine = make_engine(address)
    try:
        with engine.connect() as c:
            c.exec_driver_sql("PRAGMA foreign_keys=OFF")
            c.exec_driver_sql("INSERT INTO exercise (id, name, kind, category) VALUES (1, 'Back Squat', 'lift', 'legs')")
            c.exec_driver_sql("INSERT INTO lift_set (session_id, exercise_id, set_number, weight_kg, reps) "
                              "VALUES (999, 1, 1, 100, 5)")  # a set whose session is not there
            c.commit()
        with pytest.raises(SystemExit) as failed:
            command.upgrade(cfg, HARDENING)
        assert failed.value.code == ("FAILED: the migrations ran, to 0012, and PRAGMA foreign_key_check then found 1 "
                                     "rows, in lift_set.")
        assert _version(engine) == HARDENING  # SQLite committed each migration: the line says where it stands
    finally:
        engine.dispose()


# ---------------------------------------------------------------- decision 14: /ready

@pytest.fixture(autouse=True)
def _head_read_fresh(monkeypatch):
    monkeypatch.setattr(main, "_head", [])


def _set_version(value: str) -> None:
    with db.get_engine().begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": value})


def test_ready_is_ok_at_the_head_and_health_is_as_before(make_client, db_session):
    http = make_client()
    r = http.get("/ready")
    assert (r.status_code, r.text, r.headers["content-type"]) == (200, "OK", "text/plain; charset=utf-8")
    assert http.get("/health").text == "OK" and http.get("/health").status_code == 200
    assert main._head == [ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head()] == [HEAD]
    assert http.head("/ready").status_code == 200
    assert db_session.scalars(select(CallLog)).all() == []  # no call-log row


def test_ready_says_migration_when_the_database_is_at_another_revision(make_client):
    http = make_client()
    _set_version("0011")
    try:
        r = http.get("/ready")
        assert (r.status_code, r.text) == (503, "Not ready: migration")
    finally:
        _set_version(HEAD)
    assert http.get("/ready").status_code == 200


def test_ready_says_migration_when_the_code_s_migrations_cannot_be_read(make_client, monkeypatch):
    monkeypatch.setattr(main, "MIGRATIONS", ROOT / "no-such-folder")
    r = make_client().get("/ready")  # the app started: the head is read on the first /ready only
    assert (r.status_code, r.text) == (503, "Not ready: migration")


def test_ready_says_database_with_no_database_and_never_an_error_s_text(make_client, monkeypatch):
    r = make_client(database_url=DEFAULT_DATABASE_URL).get("/ready")  # a public site with no DATABASE_URL
    assert (r.status_code, r.text) == (503, "Not ready: database")
    assert not (ROOT / "spotter.db").exists()

    def broken():
        raise RuntimeError("password=hunter2 at db.internal")

    monkeypatch.setattr(main, "_database_heads", broken)
    r = make_client().get("/ready")
    assert (r.status_code, r.text) == (503, "Not ready: database")


def test_ready_answers_on_the_app_with_no_sign_in_set_up(make_client):
    http = make_client(authkit_domain=None)
    assert http.post("/mcp").status_code == 503
    assert (http.get("/ready").status_code, http.get("/ready").text) == (200, "OK")
    _set_version("0011")
    try:
        assert (http.get("/ready").status_code, http.get("/ready").text) == (503, "Not ready: migration")
    finally:
        _set_version(HEAD)


def test_loads_of_ready_use_up_none_of_the_page_limit(client, monkeypatch):
    monkeypatch.setattr(web, "PAGE_LIMIT", 3)
    sign_in(client)
    assert [client.get("/ready").status_code for _ in range(8)] == [200] * 8
    assert [client.get("/progress").status_code for _ in range(4)] == [200, 200, 200, 429]
    assert client.get("/ready").status_code == 200  # over the limit, still open
    with db.session_scope() as s:
        assert [c.function for c in s.scalars(select(CallLog))] == []


def test_ready_s_words_are_built_from_parts_under_20_characters():
    import ast
    tree = ast.parse((ROOT / "app" / "main.py").read_text(encoding="utf-8"))
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert "Not ready: migration" not in literals and "Not ready: database" not in literals
    assert main.NOT_READY + "migration" == "Not ready: migration" and len(main.NOT_READY) < 20


# ---------------------------------------------------------------- tools/probe.py reads /ready

def _probe_through(http, monkeypatch):
    from tools import probe

    def request(url, method="GET", body=None, headers=None):
        parts = urlparse(url)
        r = http.request(method, parts.path + (f"?{parts.query}" if parts.query else ""), content=body,
                         headers=headers or {})
        return r.status_code, r.headers, r.text

    monkeypatch.setattr(probe, "request", request)
    return probe


def test_the_probe_checks_ready(make_client, monkeypatch, capsys):
    probe = _probe_through(make_client(), monkeypatch)
    assert probe.main("https://spotter.example.app") == 0
    out = capsys.readouterr().out
    assert "PASS  GET /ready is 200 OK" in out and "FAIL" not in out
    _set_version("0011")
    try:
        assert probe.main("https://spotter.example.app") == 1
    finally:
        _set_version(HEAD)
    out = capsys.readouterr().out
    assert "FAIL  GET /ready is 200 OK  (503 'Not ready: migration')" in out
    assert out.splitlines()[-1] == "FAIL: 1 check(s) failed"


# ---------------------------------------------------------------- the tests of older migrations

# What compare_metadata shows at a revision before 0012 that 0012 adds: the tests of older migrations leave it out.
NEW_IN_0012 = {*SWEEP_INDEXES.values(), FK_OWNER, UQ_SET_NUMBER, UQ_SAVED_FOOD}


def before_0012(diffs: list) -> list:
    """compare_metadata's list at a revision before 0012, less 0012's own entries (name_key's width among them) and
    0013's (before_0013)."""
    def is_0012(d) -> bool:
        if isinstance(d, list):  # a column's changes come nested: here only name_key's width
            return all(x[0] == "modify_type" and (x[2], x[3]) == ("saved_food", "name_key") for x in d)
        return d[0] in ("add_index", "add_fk", "add_constraint") and d[1].name in NEW_IN_0012
    return [d for d in before_0013(diffs) if not is_0012(d)]


# What compare_metadata shows at a revision before 0013 that 0013 adds (exercise-list.md): four columns, reps made
# nullable, and the key's unique. The rules are not in the models.
NEW_COLUMNS_IN_0013 = {("exercise", "key"), ("exercise", "retired"), ("lift_set", "time_s"), ("lift_set", "distance_m")}
UQ_EXERCISE_KEY = "uq_exercise_key"


def before_0013(diffs: list) -> list:
    """compare_metadata's list at a revision before 0013, less 0013's own entries."""
    def is_0013(d) -> bool:
        if isinstance(d, list):  # a column's changes come nested: here only reps made nullable
            return all(x[0] == "modify_nullable" and (x[2], x[3]) == ("lift_set", "reps") for x in d)
        if d[0] == "add_column":
            return (d[2], d[3].name) in NEW_COLUMNS_IN_0013
        return d[0] == "add_constraint" and d[1].name == UQ_EXERCISE_KEY
    return [d for d in diffs if not is_0013(d)]
