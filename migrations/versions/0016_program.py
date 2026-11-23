"""Programs: one new table, program.

Revision: 0016
Follows: 0015

One row a person for the training program saved with save_program (contract routines-and-programs.md R8 and its note
of 2026-11-23): the person, the program's name, the person's date week 1 starts, the blocks as JSON text, and the
three times. No saved_on: nothing reads it. The person is unique, deleted rows included, so a person holds one program
ever, replaced by the next save; that one unique constraint serves every read by user_id: no other index. The row goes
with the account (ON DELETE CASCADE). No number column, so no rule of 0012's kind.

The downgrade drops the table, and every program saved since with it. No other table changes.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "program",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("start_on", sa.Date(), nullable=False),
        sa.Column("blocks", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_program"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_program_user_id_user", ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_program_user_id"),
    )


def downgrade() -> None:
    op.drop_table("program")
