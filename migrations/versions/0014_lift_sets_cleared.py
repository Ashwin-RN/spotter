"""The one-off delete: every lift set logged before the exercise list goes, for every account (deploy 2).

Revision: 0014
Follows: 0013

Contract exercise-list.md, decision 35, R9 and "0014: the one-off delete". Data only: no schema change. It deletes,
in this order:
- every lift_set row (on SQLite migrations run with foreign keys off, migrations/env.py, so the sets go by their own
  statement, never by the cascade);
- every lift_session row, live and soft-deleted, so restore_entry brings none back;
- every entry_version row of kind lift_session, so undo_edit puts no old set back;
- every write_key row of function log_lifts, so no kept answer claims sets that are gone.
Lift goals, routines, call-log rows, food, weigh-ins, measurements, cardio and every other write key stay.

Online it prints one line of counts, numbers only: "0014 deleted: 412 lift sets, 61 lift sessions, 18 versions,
2 keys." Offline (--sql) there is no count: the four DELETE statements alone, as stdout is the SQL script.

The downgrade does nothing and restores nothing: the delete is one-way. A down past 0014 and an up again runs the
delete again, and every lift set logged since goes too: on the host that is the owner's call alone, said first.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

lift_set = sa.table("lift_set", sa.column("id", sa.Integer))
lift_session = sa.table("lift_session", sa.column("id", sa.Integer))
entry_version = sa.table("entry_version", sa.column("kind", sa.String))
write_key = sa.table("write_key", sa.column("function", sa.String))

# (the table's words in the counts line, the statement), in the contract's order.
DELETES = (
    ("lift sets", sa.delete(lift_set)),
    ("lift sessions", sa.delete(lift_session)),
    ("versions", sa.delete(entry_version).where(entry_version.c.kind == "lift_session")),
    ("keys", sa.delete(write_key).where(write_key.c.function == "log_lifts")),
)


def upgrade() -> None:
    if op.get_context().as_sql:
        for _, statement in DELETES:
            op.execute(statement)
        return
    bind = op.get_bind()
    counts = [f"{bind.execute(statement).rowcount:,} {words}" for words, statement in DELETES]
    print(f"0014 deleted: {', '.join(counts)}.")


def downgrade() -> None:
    """Nothing: the delete is one-way (decision 35)."""
