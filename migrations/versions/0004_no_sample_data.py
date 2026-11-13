"""No sample data: every row marked is_sample goes, then the column goes.

Revision: 0004
Follows: 0003

Spotter no longer adds or removes sample data, so a sample row left behind would read as the person's own.
The upgrade hard-deletes every row with is_sample true, live or soft-deleted, as the old Remove sample data
button did: a sample session's sets with it, and a real food entry that names a sample saved food keeps its
numbers and its tag but loses the link. Then it drops is_sample from the seven tables that carry it.

The order: the sets of sample sessions, then the sample entries and goals, then the links from real food
entries to sample saved foods, then the sample saved foods. Each step is its own statement, so nothing
depends on the database's foreign key actions.

The columns go with a plain ALTER TABLE ... DROP COLUMN on both databases, not a batch rebuild. SQLite
(3.35 and later) drops a column in place when nothing indexes or refers to it, and nothing does. A batch
rebuild would drop and recreate lift_session and saved_food, and with foreign keys on, dropping a parent
table fires ON DELETE on its children: every lift set and every food entry's link would go with it.

The downgrade adds the columns back, false on every row, as 0001 built them. The deleted rows stay deleted.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

COLUMN = "is_sample"
# Every table that carries the column, in the order its sample rows are deleted: entries and goals first,
# saved foods last, after the food entries that may name them.
TABLES = ("food_entry", "cardio_entry", "lift_session", "bodyweight", "measurement", "goal", "saved_food")


def _table(name: str, *columns: str) -> sa.TableClause:
    return sa.table(name, sa.column("id", sa.Integer()), sa.column(COLUMN, sa.Boolean()),
                    *(sa.column(c, sa.Integer()) for c in columns))


def upgrade() -> None:
    sessions = _table("lift_session")
    sets = sa.table("lift_set", sa.column("session_id", sa.Integer()))
    foods = _table("saved_food")
    eaten = _table("food_entry", "saved_food_id")
    sample_sessions = sa.select(sessions.c.id).where(sessions.c[COLUMN].is_(True))
    sample_foods = sa.select(foods.c.id).where(foods.c[COLUMN].is_(True))
    op.execute(sa.delete(sets).where(sets.c.session_id.in_(sample_sessions)))
    for name in TABLES:
        if name == "saved_food":
            # A real entry logged against a sample food keeps its numbers and loses the link.
            op.execute(sa.update(eaten).where(eaten.c.saved_food_id.in_(sample_foods)).values(saved_food_id=None))
        table = _table(name)
        op.execute(sa.delete(table).where(table.c[COLUMN].is_(True)))
    for name in TABLES:
        op.drop_column(name, COLUMN)


def downgrade() -> None:
    for name in TABLES:
        op.add_column(name, sa.Column(COLUMN, sa.Boolean(), server_default=sa.false(), nullable=False))
