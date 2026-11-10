"""A measurement goal says which measurement: goal.measurement_kind.

Revision: 0003
Follows: 0002

One nullable column, String(16), on goal. Every goal already stored keeps its rows and gets null:
only a measurement goal (waist, hips, chest, arm, thigh or resting_hr) sets it. Kind values need no
migration; the services check them.

The downgrade drops the column in a batch: SQLite rebuilds the table, copying every row, and
Postgres drops the column in place. No table refers to goal, so the rebuild touches nothing else.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

COLUMN = "measurement_kind"


def upgrade() -> None:
    op.add_column("goal", sa.Column(COLUMN, sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("goal") as batch:
        batch.drop_column(COLUMN)
