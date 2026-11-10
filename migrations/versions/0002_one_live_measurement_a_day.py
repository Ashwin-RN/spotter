"""One live measurement per person, kind and day.

Revision: 0002
Follows: 0001

A partial unique index on live rows, the same as bodyweight's one weigh-in a day. Before it is
built, any day that already holds two live values of one kind keeps the one logged last and the
others are soft-deleted, so the index can be built on a database that has rows. Nothing is erased.

Runs on SQLite and on Postgres. Never edit this file once it has run on the host.
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

INDEX = "uq_measurement_user_id_kind_date_live"


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE measurement SET deleted_at = CURRENT_TIMESTAMP "
        "WHERE deleted_at IS NULL AND id NOT IN ("
        "SELECT MAX(id) FROM measurement WHERE deleted_at IS NULL GROUP BY user_id, kind, date)"))
    op.create_index(INDEX, "measurement", ["user_id", "kind", "date"], unique=True,
                    sqlite_where=sa.text("deleted_at IS NULL"), postgresql_where=sa.text("deleted_at IS NULL"))


def downgrade() -> None:
    op.drop_index(INDEX, table_name="measurement")
