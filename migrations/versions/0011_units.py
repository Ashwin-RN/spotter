"""Units: two new columns on user, weight_unit and distance_unit.

Revision: 0011
Follows: 0010

How the person's pages and summaries show weights (kg or lb) and distances (km or mi), set with update_settings or
on Settings (contract units.md). Each is a nullable String(2) with no server default: null means not chosen, and
shows kg and km. Every stored number stays in kg and km, so no row changes.

The downgrade drops both columns, with no table rebuild (SQLite drops a column in place). No other table changes.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("weight_unit", sa.String(2), nullable=True))
    op.add_column("user", sa.Column("distance_unit", sa.String(2), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "distance_unit")
    op.drop_column("user", "weight_unit")
