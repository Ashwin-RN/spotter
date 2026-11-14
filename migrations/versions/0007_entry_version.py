"""Edit history: one new table, entry_version.

Revision: 0007
Follows: 0006

One row per edit or undo that changed an entry: the person, the entry's table (kind) and its id there, when, who
(web or claude), why (edit or undo), the request's id, and the row as it stood before, as JSON
(app/services/common.py says how one is kept and put back). entry_id has no foreign key: the kinds are six
tables. One index serves the newest versions of a person's entry. The rows go with the account (ON DELETE
CASCADE).

The downgrade drops the table.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entry_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("by", sa.String(8), nullable=False, quote=True),
        sa.Column("why", sa.String(8), nullable=False),
        sa.Column("request_id", sa.String(16), nullable=True),
        sa.Column("before", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_entry_version"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_entry_version_user_id_user", ondelete="CASCADE"),
    )
    op.create_index("ix_entry_version_user_id_kind_entry_id_id", "entry_version",
                    ["user_id", "kind", "entry_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_entry_version_user_id_kind_entry_id_id", table_name="entry_version")
    op.drop_table("entry_version")
