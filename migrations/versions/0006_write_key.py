"""The write keys: one new table, write_key.

Revision: 0006
Follows: 0005

One row per person and key a write carried: the function, a fingerprint of what was sent, the first answer
(null until it is kept) and when (app/services/common.py says how a row is claimed). One row per person and
key, by a unique constraint. The rows go with the account (ON DELETE CASCADE).

The downgrade drops the table.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "write_key",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("function", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_write_key"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_write_key_user_id_user", ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "key", name="uq_write_key_user_id_key"),
    )


def downgrade() -> None:
    op.drop_table("write_key")
