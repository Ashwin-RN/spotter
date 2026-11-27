"""Macros, package P1 (contract macros-and-asking.md, "The columns" and "Migration 0015"): migration 0015 up, down and
up on SQLite over rows of every kind with each new column set; its rules, which refuse a negative gram; the head; the
offline Postgres SQL; and the models and the migration agreeing, column by column.

The up, down and up tests build their own SQLite files. The services write only at the head: the models are the
head's. The rule tests use the shared test database, built by migration to the head.
"""

import importlib.util
import io
from contextlib import redirect_stdout

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect

from app import db
from app.config import ROOT
from app.db import make_engine
from app.models import FoodEntry, SavedFood, UsdaFood, User

from .conftest import alembic_config
from .test_hardening_schema import (
    _agree, _ids, _insert, _key_check, _master, _plain, _refused, _sqlite_at, _taken, _version, before_0016,
    write_everything,
)

UP, DOWN = "0015", "0014"
POSTGRES = "postgresql+psycopg://spotter@db.invalid/spotter"  # offline: never connects

# table: {column: (type name, length or None)}, as the contract's "The columns" has them. All nullable but macros_read.
NEW_COLUMNS = {
    "food_entry": {"protein_g": ("FLOAT", None), "carbs_g": ("FLOAT", None), "fat_g": ("FLOAT", None),
                   "macros_estimated": ("BOOLEAN", None), "portion": ("VARCHAR", 64)},
    "saved_food": {"protein_g_per_serving": ("FLOAT", None), "carbs_g_per_serving": ("FLOAT", None),
                   "fat_g_per_serving": ("FLOAT", None), "items": ("TEXT", None)},
    "usda_food": {"protein_g_per_100g": ("FLOAT", None), "carbs_g_per_100g": ("FLOAT", None),
                  "fat_g_per_100g": ("FLOAT", None), "macros_read": ("BOOLEAN", None)},
    "user": {"protein_target_g": ("INTEGER", None), "carbs_target_g": ("INTEGER", None),
             "fat_target_g": ("INTEGER", None)},
}
MODELS = {"food_entry": FoodEntry, "saved_food": SavedFood, "usda_food": UsdaFood, "user": User}

# (table, column, whole): each new number rule. A gram on food is 0 to 100,000; a daily amount, a whole number, is above
# 0 and at most 100,000, as calorie_target_kcal's rule is. usda_food is a shared cache and takes none.
RULES = (
    ("food_entry", "protein_g", False), ("food_entry", "carbs_g", False), ("food_entry", "fat_g", False),
    ("saved_food", "protein_g_per_serving", False), ("saved_food", "carbs_g_per_serving", False),
    ("saved_food", "fat_g_per_serving", False),
    ("user", "protein_target_g", True), ("user", "carbs_target_g", True), ("user", "fat_target_g", True),
)
CHECKS = {f"ck_{t}_{c}" for t, c, _ in RULES}


def _condition(column: str, whole: bool) -> str:
    return f"{column} {'>' if whole else '>='} 0 AND {column} <= 100000"

# Each new column set on every row of its table, as the services of P2 and P3 will write them.
SET = {
    "food_entry": {"protein_g": 12.5, "carbs_g": 30.0, "fat_g": 4.2, "macros_estimated": 1, "portion": "a big bowl"},
    "saved_food": {"protein_g_per_serving": 3.1, "carbs_g_per_serving": 18.0, "fat_g_per_serving": 0.4,
                   "items": '[{"description": "Roti", "kcal": 120}, {"description": "Dal", "kcal": 180}]'},
    "usda_food": {"protein_g_per_100g": 1.1, "carbs_g_per_100g": 22.8, "fat_g_per_100g": 0.3, "macros_read": 1},
    "user": {"protein_target_g": 150, "carbs_target_g": 250, "fat_target_g": 70},
}
EMPTY = {t: {c: (0 if c == "macros_read" else None) for c in cols} for t, cols in SET.items()}


def _migration():
    path = ROOT / "migrations" / "versions" / "0015_macros.py"
    spec = importlib.util.spec_from_file_location("migration_0015", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _named(engine: Engine) -> dict[str, list[dict]]:
    """Every table's rows by id, each row as {column: value}."""
    with engine.connect() as c:
        names = [n for n in inspect(c).get_table_names() if n != "alembic_version"]
        return {n: [dict(r._mapping) for r in c.exec_driver_sql(f'SELECT * FROM "{n}" ORDER BY 1').all()]
                for n in names}


def _without_0015(rows: dict[str, list[dict]]) -> dict[str, list[dict]]:
    return {t: [{k: v for k, v in r.items() if k not in NEW_COLUMNS.get(t, {})} for r in rs] for t, rs in rows.items()}


def _with(rows: dict[str, list[dict]], values: dict[str, dict]) -> dict[str, list[dict]]:
    return {t: [{**r, **values.get(t, {})} for r in rs] for t, rs in rows.items()}


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


def _set_every_new_column(engine: Engine) -> None:
    with engine.begin() as c:
        for table, values in SET.items():
            assignments = ", ".join(f"{column} = ?" for column in values)
            c.exec_driver_sql(f'UPDATE "{table}" SET {assignments}', tuple(values.values()))


# ---------------------------------------------------------------- the head

def test_0015_follows_0014_and_is_the_head():
    migration = _migration()
    assert migration.revision == UP and migration.down_revision == DOWN
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert script.get_current_head() == "0017" and script.get_revision(UP).down_revision == DOWN  # 0016, 0017 follow it


def test_the_migration_s_lists_are_the_contract_s():
    migration = _migration()
    added = {t: [c for c, _ in cols] for t, cols in migration.COLUMNS.items()}
    added["usda_food"].append(migration.MACROS_READ)
    assert {t: sorted(cols) for t, cols in added.items()} == {t: sorted(cols) for t, cols in NEW_COLUMNS.items()}
    assert {(t, r): cond for t, r, cond in migration.RULES} == {(t, c): _condition(c, whole) for t, c, whole in RULES}


# ---------------------------------------------------------------- 0015 up, down and up over rows of every kind

def test_0015_up_down_and_up_over_rows_of_every_kind_with_each_new_column_set(monkeypatch):
    address = _sqlite_at("macros.db")
    cfg = alembic_config(address)
    # The rows are written by the head's models, which name columns later migrations add (0017's user.status), so they
    # are written at the head and the database is then brought down to 0015.
    command.upgrade(cfg, "head")
    write_everything(address, monkeypatch)
    command.downgrade(cfg, UP)
    engine = make_engine(address)
    try:
        assert _version(engine) == UP
        _set_every_new_column(engine)
        at_head, master_head = _named(engine), _master(engine)
        assert {t: len(rows) > 0 for t, rows in at_head.items()} == {t: True for t in at_head}  # every table
        for table, values in SET.items():
            assert [{k: r[k] for k in values} for r in at_head[table]] == [values] * len(at_head[table]), table
        assert _key_check(engine) == [] and before_0016(_agree(engine)) == []
        command.downgrade(cfg, DOWN)
        assert _version(engine) == DOWN
        at_0014 = _named(engine)
        assert at_0014 == _without_0015(at_head)  # every row kept, less the new columns
        assert _key_check(engine) == []
        plain = _sqlite_at("plain-0014.db")
        command.upgrade(alembic_config(plain), DOWN)
        assert _shape(address) == _shape(plain)  # down leaves the four tables as 0014 built them
        for _ in range(2):
            command.upgrade(cfg, UP)
            assert _version(engine) == UP
            assert _named(engine) == _with(at_head, EMPTY)  # every row back, each new column empty, no macro read
            assert _key_check(engine) == [] and before_0016(_agree(engine)) == []
            assert _master(engine) == master_head  # the rebuilt tables come back word for word
            command.downgrade(cfg, DOWN)
            assert _named(engine) == at_0014 and _key_check(engine) == []
    finally:
        engine.dispose()


def test_after_the_rebuild_the_tables_keep_every_key_unique_index_and_rule_they_had():
    """0015 on SQLite rebuilds food_entry, saved_food and user: each comes out with what it held at 0014, the deferred
    owner key among them, and the new rules beside."""
    before, after = _sqlite_at("at-0014.db"), _sqlite_at("at-0015.db")
    command.upgrade(alembic_config(before), DOWN)
    command.upgrade(alembic_config(after), UP)
    old, new = _shape(before), _shape(after)
    assert set(old) == set(new)
    for table, shape in old.items():
        columns, pk, keys, uniques, indexes, checks = shape
        n_columns, n_pk, n_keys, n_uniques, n_indexes, n_checks = new[table]
        added = {repr(name) for name in NEW_COLUMNS.get(table, {})} | {repr(name) for name in CHECKS}
        assert [c for c in n_columns if c["name"] not in added] == columns, table  # _plain holds each name's repr
        assert (n_pk, n_keys, n_uniques, n_indexes) == (pk, keys, uniques, indexes), table
        assert [c for c in n_checks if c["name"] not in added] == checks, table
    engine = make_engine(after)
    try:
        with engine.connect() as c:
            found = inspect(c)
            owner = {k["name"]: k for k in found.get_foreign_keys("food_entry")}["fk_food_entry_user_id_saved_food"]
            checks = {t: {k["name"]: k["sqltext"] for k in found.get_check_constraints(t)}
                      for t in ("food_entry", "saved_food", "usda_food", "user")}
    finally:
        engine.dispose()
    assert owner["options"].get("deferrable") is True and owner["options"].get("initially") == "DEFERRED"
    for table, column, whole in RULES:
        assert checks[table][f"ck_{table}_{column}"] == _condition(column, whole)
    assert checks["usda_food"] == {}  # a shared cache: no rule, as its kcal_per_100g has none


# ---------------------------------------------------------------- the rules, on the shared test database

@pytest.fixture
def ids():
    with db.get_engine().begin() as c:
        return _ids(c)


@pytest.mark.parametrize("rule", RULES, ids=lambda r: f"{r[0]}.{r[1]}")
def test_each_new_rule_takes_its_edges_and_null_and_refuses_a_negative_and_one_step_outside(ids, rule):
    """A gram on food takes 0; a daily amount refuses 0 and takes 1."""
    table, column, whole = rule
    engine = db.get_engine()
    step = 1 if whole else 0.1
    lowest = 1 if whole else 0
    for value in (lowest, 100_000, None, 12 if whole else 12.5):
        _taken(engine, table, ids, {column: value})
    for value in (lowest - step, -1, 100_000 + step):
        _refused(engine, table, ids, {column: value})
    if whole:
        _refused(engine, table, ids, {column: 0})


def test_a_food_entry_with_every_new_column_goes_in_and_a_negative_gram_on_it_is_refused(ids):
    engine = db.get_engine()
    row = {"protein_g": 32, "carbs_g": 48, "fat_g": 18, "macros_estimated": False, "portion": "1 wrap"}
    _taken(engine, "food_entry", ids, row)
    for column in ("protein_g", "carbs_g", "fat_g"):
        _refused(engine, "food_entry", ids, {**row, column: -0.1})


def test_the_built_database_holds_the_nine_new_rules():
    with db.get_engine().connect() as c:
        found = inspect(c)
        names = {k["name"] for t in ("food_entry", "saved_food", "user") for k in found.get_check_constraints(t)}
        assert found.get_check_constraints("usda_food") == []
    assert CHECKS <= names and len(CHECKS) == 9


# ---------------------------------------------------------------- the models and the migration agree

def test_the_models_and_the_migration_agree_column_by_column():
    with db.get_engine().connect() as c:
        found = inspect(c)
        built = {t: {col["name"]: col for col in found.get_columns(t)} for t in NEW_COLUMNS}
    for table, columns in NEW_COLUMNS.items():
        model = MODELS[table].__table__
        for name, (type_name, length) in columns.items():
            column, db_column = model.c[name], built[table][name]
            assert type(db_column["type"]).__name__.upper() == type_name, (table, name)
            assert column.type.compile(dialect=db.get_engine().dialect).split("(")[0] == type_name, (table, name)
            assert getattr(db_column["type"], "length", None) == length == getattr(column.type, "length", None)
            nullable = name != "macros_read"
            assert db_column["nullable"] is nullable and column.nullable is nullable, (table, name)
            if name == "macros_read":
                assert db_column["default"] in ("0", "false", "'0'")  # server default false
                assert column.server_default is not None and column.default.arg is False
            else:
                assert db_column["default"] is None and column.server_default is None and column.default is None
    assert not [c for t in MODELS.values() for c in t.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]


def test_the_test_database_at_the_head_matches_the_models():
    assert _agree(db.get_engine()) == []


# ---------------------------------------------------------------- the offline Postgres SQL

def test_the_offline_postgres_sql_adds_the_columns_and_the_rules_and_its_down_drops_them():
    cfg = alembic_config(POSTGRES)
    out = io.StringIO()
    with redirect_stdout(out):
        command.upgrade(cfg, f"{DOWN}:{UP}", sql=True)
    sql = out.getvalue()
    for line in ("ALTER TABLE food_entry ADD COLUMN protein_g FLOAT;",
                 "ALTER TABLE food_entry ADD COLUMN macros_estimated BOOLEAN;",
                 "ALTER TABLE food_entry ADD COLUMN portion VARCHAR(64);",
                 "ALTER TABLE saved_food ADD COLUMN items TEXT;",
                 "ALTER TABLE usda_food ADD COLUMN fat_g_per_100g FLOAT;",
                 "ALTER TABLE usda_food ADD COLUMN macros_read BOOLEAN DEFAULT false NOT NULL;",
                 'ALTER TABLE "user" ADD COLUMN protein_target_g INTEGER;',
                 "ALTER TABLE food_entry ADD CONSTRAINT ck_food_entry_protein_g CHECK (protein_g >= 0 AND protein_g "
                 "<= 100000);",
                 'ALTER TABLE "user" ADD CONSTRAINT ck_user_fat_target_g CHECK (fat_target_g > 0 AND fat_target_g '
                 "<= 100000);"):
        assert sql.count(line) == 1, line
    assert sql.count("ADD COLUMN") == 16 and sql.count("ADD CONSTRAINT") == 9
    for word in ("UPDATE", "INSERT", "DELETE", "SELECT"):
        assert word not in sql.replace("UPDATE alembic_version", ""), word  # no data moves
    down = io.StringIO()
    with redirect_stdout(down):
        command.downgrade(cfg, f"{UP}:{DOWN}", sql=True)
    back = down.getvalue()
    assert back.count("DROP COLUMN") == 16 and back.count("DROP CONSTRAINT") == 9
    assert "ALTER TABLE food_entry DROP CONSTRAINT ck_food_entry_protein_g;" in back
    assert back.index("DROP CONSTRAINT ck_food_entry_protein_g") < back.index("food_entry DROP COLUMN protein_g")
