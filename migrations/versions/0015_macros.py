"""Macros: protein, carbs and fat in grams on food, a portion, a saved meal's items, and three daily amounts.

Revision: 0015
Follows: 0014

Contract macros-and-asking.md, "The columns" and "Migration 0015":
- food_entry: protein_g, carbs_g and fat_g (FLOAT), macros_estimated (BOOLEAN, null when no macro is held) and
  portion (VARCHAR(64), the person's words for how much was eaten).
- saved_food: protein_g_per_serving, carbs_g_per_serving and fat_g_per_serving (FLOAT), and items (TEXT: null for a
  plain food, JSON for a meal).
- usda_food: protein_g_per_100g, carbs_g_per_100g and fat_g_per_100g (FLOAT), and macros_read (BOOLEAN, NOT NULL,
  server default false): a row cached before this revision reads as stale and is fetched again.
- user: protein_target_g, carbs_target_g and fat_target_g (INTEGER), the three daily amounts.
Every new column but macros_read is nullable, and null is "not logged", never 0 g.

The rules in RULES, each a CHECK named ck_<table>_<rule> in 0012's shape: a sign rule and a wide ceiling on each new
number a person's row holds, looser than every range the services hold. A gram on food is 0 to 100,000; a daily amount
is above 0 and at most 100,000, as calorie_target_kcal's rule is. usda_food is a shared cache, not a person's row: as
its kcal_per_100g, its three figures take no rule. A null passes a CHECK.

No data moves: the new columns are empty, so no row can break a rule and nothing is counted first. Every existing entry
reads "not logged". The downgrade drops the rules, then the columns: the macros, portions, meals' items and daily
amounts written since are lost with them.

On SQLite food_entry, saved_food and user are rebuilt by batch, with foreign keys off (migrations/env.py), carrying
every rule, key, unique and index they hold; usda_food takes its columns by ALTER TABLE. On Postgres, ALTER TABLE makes
each change.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

PORTION_LENGTH = 64  # serving_label's own limit, for the same kind of words

# table: its new columns, in the order they are added.
COLUMNS = {
    "food_entry": (
        ("protein_g", sa.Float()),
        ("carbs_g", sa.Float()),
        ("fat_g", sa.Float()),
        ("macros_estimated", sa.Boolean()),
        ("portion", sa.String(PORTION_LENGTH)),
    ),
    "saved_food": (
        ("protein_g_per_serving", sa.Float()),
        ("carbs_g_per_serving", sa.Float()),
        ("fat_g_per_serving", sa.Float()),
        ("items", sa.Text()),
    ),
    "usda_food": (
        ("protein_g_per_100g", sa.Float()),
        ("carbs_g_per_100g", sa.Float()),
        ("fat_g_per_100g", sa.Float()),
    ),
    "user": (
        ("protein_target_g", sa.Integer()),
        ("carbs_target_g", sa.Integer()),
        ("fat_target_g", sa.Integer()),
    ),
}
MACROS_READ = "macros_read"  # usda_food's, the one column that is NOT NULL

# (table, rule, condition): the CHECK named ck_<table>_<rule>. A daily amount is above 0, as calorie_target_kcal's.
RULES = tuple((table, column, f"{column} >= 0 AND {column} <= 100000") for table, column in (
    ("food_entry", "protein_g"),
    ("food_entry", "carbs_g"),
    ("food_entry", "fat_g"),
    ("saved_food", "protein_g_per_serving"),
    ("saved_food", "carbs_g_per_serving"),
    ("saved_food", "fat_g_per_serving"),
)) + tuple(("user", column, f"{column} > 0 AND {column} <= 100000") for column in (
    "protein_target_g",
    "carbs_target_g",
    "fat_target_g",
))
TABLES = tuple(COLUMNS)


def _name(table: str, rule: str) -> str:
    return f"ck_{table}_{rule}"


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            for column, type_ in COLUMNS[table]:
                batch.add_column(sa.Column(column, type_, nullable=True))
            if table == "usda_food":
                batch.add_column(sa.Column(MACROS_READ, sa.Boolean(), server_default=sa.false(), nullable=False))
            for t, rule, condition in RULES:
                if t == table:
                    batch.create_check_constraint(op.f(_name(table, rule)), condition)


def downgrade() -> None:
    for table in reversed(TABLES):
        with op.batch_alter_table(table) as batch:
            for t, rule, _ in reversed(RULES):
                if t == table:
                    batch.drop_constraint(op.f(_name(table, rule)), type_="check")
            if table == "usda_food":
                batch.drop_column(MACROS_READ)
            for column, _ in reversed(COLUMNS[table]):
                batch.drop_column(column)
