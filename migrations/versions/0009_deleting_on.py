"""The switch has two settings: every account starts with deleting on, user.claude_can log_delete.

Revision: 0009
Follows: 0008

Data only. One UPDATE sets claude_can to log_delete on every row that does not hold it, whatever it held:
read, log, or a word Spotter does not know. It touches no other column, updated_at least of all, and changes
no table. The column's own default in the database stays 'log': the model's default decides every new account.

The downgrade changes no row: nothing records what each row held before.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

USER = sa.table("user", sa.column("claude_can", sa.String(16)))  # built, not typed: user is reserved on Postgres


def upgrade() -> None:
    op.execute(USER.update().where(USER.c.claude_can != "log_delete").values(claude_can="log_delete"))


def downgrade() -> None:
    pass  # nothing records what a row held
