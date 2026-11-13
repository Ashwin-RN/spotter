"""The call trace: seven nullable columns on call_log and an index on request_id.

Revision: 0005
Follows: 0004

request_id, caller, tried, outcome, step, frame and notes (app/calllog.py says what each holds). All
nullable: the rows already there keep nulls, and code from before this runs against the new table.

The downgrade drops the index, then the seven columns, with a plain ALTER TABLE ... DROP COLUMN on both
databases (SQLite 3.35 and later drops a column in place once nothing indexes it).

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

COLUMNS = (("request_id", 16), ("caller", 8), ("tried", 400), ("outcome", 16), ("step", 16), ("frame", 128),
           ("notes", 128))
INDEX = "ix_call_log_request_id"


def upgrade() -> None:
    for name, length in COLUMNS:
        op.add_column("call_log", sa.Column(name, sa.String(length), nullable=True))
    op.create_index(INDEX, "call_log", ["request_id"])


def downgrade() -> None:
    op.drop_index(INDEX, table_name="call_log")
    for name, _ in COLUMNS:
        op.drop_column("call_log", name)
