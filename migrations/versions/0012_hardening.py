"""Hardening: the database keeps its own rules.

Revision: 0012
Follows: 0011

Contract hardening.md, decisions 10 to 16:
- saved_food.name_key becomes VARCHAR(384): a name of 128 characters, casefolded, at most 3 characters from one.
- Three indexes for the retention sweep: write_key, entry_version and feedback by created_at alone.
- The rules in RULES, each a CHECK named ck_<table>_<rule>: a sign rule and a wide ceiling on each number a person's
  row holds, looser than every range the services hold, so a range can change with no migration (the ceiling also
  keeps out Postgres's NaN and Infinity); the closed lists; three goal shape rules, each binding only its own kind.
  A null passes a CHECK, so a nullable column's rule holds only for a value.
- One set number per session and lift: unique lift_set (session_id, exercise_id, set_number).
- A food entry's saved food has the entry's owner: unique saved_food (user_id, id), and a key food_entry (user_id,
  saved_food_id) to it with no action of its own, DEFERRABLE INITIALLY DEFERRED, so it is checked at commit, after the
  key on saved_food_id alone (ON DELETE SET NULL) has nulled a link to a saved food deleted for good.
- call_log.id becomes BIGINT on Postgres, and call_log_id_seq with it: a SERIAL's sequence does not follow its
  column. SQLite's call_log is not changed: its INTEGER primary key is already a 64-bit rowid.

The rows are proved first. Before any change, each rule's broken rows are counted, and the duplicate set numbers and
the food entries whose saved food is not their owner's. Any count above 0 stops the upgrade with one line a rule, its
name and its count, never a value, and nothing is changed. Offline (--sql) there are no counts. The downgrade stops
the same way when a name_key is over 128 characters, and on Postgres when a call-log id or the sequence is past
2147483647.

On SQLite each table that takes a rule is rebuilt, one batch a table, with foreign keys off (migrations/env.py): ten
tables. write_key and feedback take only an index. On Postgres nothing is rebuilt: ALTER TABLE adds each rule.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

import sys

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

INT_MAX = 2147483647
LOGGED_BY = "logged_by IN ('web', 'claude', 'import')"

# (table, rule, condition): the CHECK named ck_<table>_<rule>. In the order the tables are rebuilt.
RULES = (
    ("user", "calorie_target_kcal", "calorie_target_kcal > 0 AND calorie_target_kcal <= 100000"),
    ("user", "target_sessions_per_week", "target_sessions_per_week >= 0 AND target_sessions_per_week <= 1000"),
    ("user", "target_cardio_minutes_per_week",
     "target_cardio_minutes_per_week >= 0 AND target_cardio_minutes_per_week <= 100000"),
    ("user", "claude_can", "claude_can IN ('log_delete', 'log')"),
    ("user", "weight_unit", "weight_unit IN ('kg', 'lb')"),
    ("user", "distance_unit", "distance_unit IN ('km', 'mi')"),
    ("lift_session", "logged_by", LOGGED_BY),
    ("lift_set", "weight_kg", "weight_kg >= 0 AND weight_kg <= 1000"),
    ("lift_set", "reps", "reps >= 1 AND reps <= 1000"),
    ("lift_set", "set_number", "set_number >= 1 AND set_number <= 1000"),
    ("bodyweight", "kg", "kg > 0 AND kg <= 1000"),
    ("bodyweight", "logged_by", LOGGED_BY),
    ("measurement", "value", "value >= 0 AND value <= 1000000"),
    ("measurement", "logged_by", LOGGED_BY),
    ("goal", "start_value", "start_value >= 0 AND start_value <= 1000000"),
    ("goal", "target_value", "target_value >= 0 AND target_value <= 1000000"),
    ("goal", "distance_km", "distance_km > 0 AND distance_km <= 10000"),
    ("goal", "exercise", "kind NOT IN ('lift', 'cardio_best') OR exercise_id IS NOT NULL"),
    ("goal", "distance", "kind <> 'cardio_best' OR distance_km IS NOT NULL"),
    ("goal", "measurement", "kind <> 'measurement' OR measurement_kind IS NOT NULL"),
    ("goal", "direction", "direction IN ('up', 'down')"),
    ("goal", "status", "status IN ('open', 'hit', 'dropped')"),
    ("saved_food", "kcal_per_serving", "kcal_per_serving >= 0 AND kcal_per_serving <= 100000"),
    ("saved_food", "grams_per_serving", "grams_per_serving > 0 AND grams_per_serving <= 100000"),
    ("food_entry", "kcal", "kcal >= 0 AND kcal <= 100000"),
    ("food_entry", "servings", "servings > 0 AND servings <= 1000"),
    ("food_entry", "grams", "grams > 0 AND grams <= 100000"),
    ("food_entry", "logged_by", LOGGED_BY),
    ("cardio_entry", "minutes", "minutes > 0 AND minutes <= 10000"),
    ("cardio_entry", "distance_km", "distance_km > 0 AND distance_km <= 10000"),
    ("cardio_entry", "speed_kmh", "speed_kmh > 0 AND speed_kmh <= 1000"),
    ("cardio_entry", "incline_pct", "incline_pct >= 0 AND incline_pct <= 100"),
    ("cardio_entry", "avg_hr", "avg_hr >= 1 AND avg_hr <= 1000"),
    ("cardio_entry", "machine_kcal", "machine_kcal >= 0 AND machine_kcal <= 100000"),
    ("cardio_entry", "estimated_kcal", "estimated_kcal >= 0 AND estimated_kcal <= 1000000"),
    ("cardio_entry", "estimate_weight_kg", "estimate_weight_kg > 0 AND estimate_weight_kg <= 1000"),
    ("cardio_entry", "logged_by", LOGGED_BY),
    ("entry_version", "by", "\"by\" IN ('web', 'claude')"),  # quoted: BY is SQL's own word
    ("entry_version", "why", "why IN ('edit', 'undo')"),
)
TABLES = tuple(dict.fromkeys(table for table, _, _ in RULES))  # the ten tables rebuilt on SQLite

SET_NUMBER = ("session_id", "exercise_id", "set_number")
UQ_SET_NUMBER = "uq_lift_set_session_id_exercise_id_set_number"
UQ_SAVED_FOOD = "uq_saved_food_user_id_id"
FK_OWNER = "fk_food_entry_user_id_saved_food"
SWEEP_INDEXES = (("write_key", "ix_write_key_created_at"), ("entry_version", "ix_entry_version_created_at"),
                 ("feedback", "ix_feedback_created_at"))


def _name(table: str, rule: str) -> str:
    return f"ck_{table}_{rule}"


def _dialect() -> str:
    return op.get_context().dialect.name


def _online() -> bool:
    return not op.get_context().as_sql


def _count(statement) -> int:
    return op.get_bind().execute(statement).scalar() or 0


def _broken_rules() -> list[tuple[str, int]]:
    """(name, rows) for each rule some rows break, the two keys included. Nothing is changed."""
    found = []
    for table, rule, condition in RULES:
        n = _count(sa.select(sa.func.count()).select_from(sa.table(table)).where(sa.text(f"NOT ({condition})")))
        found.append((_name(table, rule), n))
    sets = sa.table("lift_set", *(sa.column(c) for c in ("id", *SET_NUMBER)))
    other = sets.alias("other")
    twin = sa.exists().where(*(other.c[c] == sets.c[c] for c in SET_NUMBER), other.c.id != sets.c.id)
    found.append((UQ_SET_NUMBER, _count(sa.select(sa.func.count()).select_from(sets).where(twin))))
    entries = sa.table("food_entry", sa.column("user_id"), sa.column("saved_food_id"))
    foods = sa.table("saved_food", sa.column("id"), sa.column("user_id"))
    owned = sa.exists().where(foods.c.id == entries.c.saved_food_id, foods.c.user_id == entries.c.user_id)
    found.append((FK_OWNER, _count(sa.select(sa.func.count()).select_from(entries)
                                   .where(entries.c.saved_food_id.is_not(None), sa.not_(owned)))))
    return [(name, n) for name, n in found if n]


def _stop(lines: list[str]) -> None:
    """Stop the run with these lines, before any change: the database stays where it was."""
    sys.exit("\n".join(lines))


def upgrade() -> None:
    if _online():
        broken = _broken_rules()
        if broken:
            _stop([f"0012 stopped: {name} {n} rows. Nothing was changed." for name, n in broken])
    rules = {table: [(rule, condition) for t, rule, condition in RULES if t == table] for table in TABLES}
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            if table == "saved_food":
                batch.alter_column("name_key", type_=sa.String(384), existing_type=sa.String(128),
                                   existing_nullable=False)
                batch.create_unique_constraint(op.f(UQ_SAVED_FOOD), ["user_id", "id"])
            if table == "lift_set":
                batch.create_unique_constraint(op.f(UQ_SET_NUMBER), list(SET_NUMBER))
            if table == "food_entry":
                batch.create_foreign_key(op.f(FK_OWNER), "saved_food", ["user_id", "saved_food_id"],
                                         ["user_id", "id"], deferrable=True, initially="DEFERRED")
            for rule, condition in rules[table]:
                batch.create_check_constraint(op.f(_name(table, rule)), condition)
    for table, index in SWEEP_INDEXES:
        op.create_index(op.f(index), table, ["created_at"])
    if _dialect() == "postgresql":
        op.alter_column("call_log", "id", type_=sa.BigInteger(), existing_type=sa.Integer(), existing_nullable=False)
        op.execute("ALTER SEQUENCE call_log_id_seq AS BIGINT")


def _downgrade_stops() -> list[str]:
    foods = sa.table("saved_food", sa.column("name_key"))
    lines = []
    wide = _count(sa.select(sa.func.count()).select_from(foods).where(sa.func.length(foods.c.name_key) > 128))
    if wide:
        lines.append(f"0012 down stopped: name_key {wide} rows. Nothing was changed.")
    if _dialect() == "postgresql":
        calls = sa.table("call_log", sa.column("id"))
        big = _count(sa.select(sa.func.count()).select_from(calls).where(calls.c.id > INT_MAX))
        if big:
            lines.append(f"0012 down stopped: call_log.id {big} rows. Nothing was changed.")
        value = op.get_bind().execute(sa.text("SELECT last_value FROM call_log_id_seq")).scalar()
        if value is not None and value > INT_MAX:
            lines.append(f"0012 down stopped: call_log_id_seq value past {INT_MAX}. Nothing was changed.")
    return lines


def downgrade() -> None:
    if _online():
        stops = _downgrade_stops()
        if stops:
            _stop(stops)
    if _dialect() == "postgresql":
        op.execute("ALTER SEQUENCE call_log_id_seq AS INTEGER")
        op.alter_column("call_log", "id", type_=sa.Integer(), existing_type=sa.BigInteger(), existing_nullable=False)
    for table, index in reversed(SWEEP_INDEXES):
        op.drop_index(op.f(index), table_name=table)
    rules = {table: [rule for t, rule, _ in RULES if t == table] for table in TABLES}
    for table in reversed(TABLES):  # food_entry's key goes before the unique it points at
        with op.batch_alter_table(table) as batch:
            for rule in reversed(rules[table]):
                batch.drop_constraint(op.f(_name(table, rule)), type_="check")
            if table == "food_entry":
                batch.drop_constraint(op.f(FK_OWNER), type_="foreignkey")
            if table == "lift_set":
                batch.drop_constraint(op.f(UQ_SET_NUMBER), type_="unique")
            if table == "saved_food":
                batch.drop_constraint(op.f(UQ_SAVED_FOOD), type_="unique")
                batch.alter_column("name_key", type_=sa.String(128), existing_type=sa.String(384),
                                   existing_nullable=False)
