"""Account status: a status on the user row, and the owner's record of each suspend and lift.

Revision: 0017
Follows: 0016

Contract owner-accounts.md, decisions 8, 10 and 19:
- user: status (VARCHAR(16), NOT NULL, server default 'active'), status_reason (VARCHAR(200), the owner's note while
  suspended) and status_at (when the status last changed), with the rule ck_user_status: active or suspended.
- owner_action: one row a suspend or a lift: id, by_account_id (the owner's account), action (suspend or lift, the rule
  ck_owner_action_action), account_id (the account it names), reason, created_at, and an index on account_id. The two
  account columns are plain integers with no foreign key, and neither is named user_id: the record outlives a deleted
  account, is never the person's own table, is not exported and is not wiped on a delete. It holds no email and no
  name (decision 20).

Every existing account reads active: the server default fills the column, so no row can break the rule and nothing
is counted first. The downgrade drops the table, then the rule and the three columns: the suspensions and the record
are lost with them.

On SQLite user is rebuilt by batch, with foreign keys off (migrations/env.py), carrying every rule, key, unique and
index it holds; owner_action is a new table. On Postgres, ALTER TABLE makes each change.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

STATUS_LENGTH = 16
REASON_LENGTH = 200  # decision 9: a one-line note
ACTION_LENGTH = 8
STATUS_RULE = "status IN ('active', 'suspended')"  # models.STATUSES
ACTION_RULE = "action IN ('suspend', 'lift')"  # models.OWNER_ACTIONS
USER_COLUMNS = ("status", "status_reason", "status_at")


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.add_column(sa.Column("status", sa.String(STATUS_LENGTH), server_default=sa.text("'active'"),
                                   nullable=False))
        batch.add_column(sa.Column("status_reason", sa.String(REASON_LENGTH), nullable=True))
        batch.add_column(sa.Column("status_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(op.f("ck_user_status"), STATUS_RULE)
    op.create_table(
        "owner_action",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("by_account_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(ACTION_LENGTH), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(REASON_LENGTH), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_owner_action"),
        sa.CheckConstraint(ACTION_RULE, name=op.f("ck_owner_action_action")),
    )
    op.create_index(op.f("ix_owner_action_account_id"), "owner_action", ["account_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_owner_action_account_id"), table_name="owner_action")
    op.drop_table("owner_action")
    with op.batch_alter_table("user") as batch:
        batch.drop_constraint(op.f("ck_user_status"), type_="check")
        for column in reversed(USER_COLUMNS):
            batch.drop_column(column)
