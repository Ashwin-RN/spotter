"""Routines: one new table, routine.

Revision: 0010
Follows: 0009

One row per workout a person saved by name with save_routine (contract routines.md): the person, the name as
given, the name's key (spaces folded, casefolded), the lifts as JSON text, the person's date of the last save, and
the three times. The key is unique per person, deleted rows included, and that one unique constraint serves every
read by user_id: no other index. The rows go with the account (ON DELETE CASCADE).

The downgrade drops the table. No other table changes.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routine",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("name_key", sa.String(255), nullable=False),
        sa.Column("lifts", sa.Text(), nullable=False),
        sa.Column("saved_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_routine"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_routine_user_id_user", ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "name_key", name="uq_routine_user_id_name_key"),
    )


def downgrade() -> None:
    op.drop_table("routine")
