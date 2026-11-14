"""Feedback: one new table, feedback.

Revision: 0008
Follows: 0007

One row per note a person sent Spotter's maker with send_feedback: the person, when, the note, its three
optional parts (empty when left out), an optional topic, the request's id, and a fingerprint of the note for the
10-minute rule (app/services/feedback.py). No column says read or done: the owner deletes a note once it is dealt
with. One index serves a person's notes by time. The rows go with the account (ON DELETE CASCADE).

The downgrade drops the table.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("doing", sa.Text(), nullable=False),
        sa.Column("expected", sa.Text(), nullable=False),
        sa.Column("happened", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(16), nullable=True),
        sa.Column("request_id", sa.String(16), nullable=True),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_feedback"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_feedback_user_id_user", ondelete="CASCADE"),
    )
    op.create_index("ix_feedback_user_id_created_at", "feedback", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_feedback_user_id_created_at", table_name="feedback")
    op.drop_table("feedback")
