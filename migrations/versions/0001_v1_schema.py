"""v1 schema: every table for stages 2 to 5.

Revision: 0001
Follows: nothing

Runs on SQLite and on Postgres: only portable types, boolean defaults written as sa.false(), and the
one partial index given its WHERE clause for both databases.

Never edit this file once it has run on the host. A change to the schema is a new migration.
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Created in this order; dropped in the reverse order.
TABLES = ["user", "exercise", "lift_session", "lift_set", "bodyweight", "goal", "call_log", "measurement",
          "saved_food", "food_entry", "cardio_entry", "usda_food", "usda_search"]


def _time(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _id(table: str) -> list:
    return [sa.Column("id", sa.Integer(), nullable=False), sa.PrimaryKeyConstraint("id", name=f"pk_{table}")]


def _owner(table: str, nullable: bool = False) -> list:
    return [sa.Column("user_id", sa.Integer(), nullable=nullable),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], name=f"fk_{table}_user_id_user", ondelete="CASCADE")]


def _sample() -> sa.Column:
    return sa.Column("is_sample", sa.Boolean(), server_default=sa.false(), nullable=False)


def _entry_columns() -> list:
    """The five columns every entry table carries."""
    return [sa.Column("logged_by", sa.String(8), nullable=False),
            _time("created_at"), _time("updated_at"), _time("deleted_at", nullable=True),
            _sample()]


def _entry_indexes(table: str) -> None:
    op.create_index(f"ix_{table}_user_id_date", table, ["user_id", "date"])
    op.create_index(f"ix_{table}_user_id_created_at", table, ["user_id", "created_at"])


def _exercise_fk(table: str, nullable: bool = False) -> list:
    return [sa.Column("exercise_id", sa.Integer(), nullable=nullable),
            sa.ForeignKeyConstraint(["exercise_id"], ["exercise.id"], name=f"fk_{table}_exercise_id_exercise")]


def upgrade() -> None:
    op.create_table(
        "user", *_id("user"),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("issuer", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("time_zone", sa.String(64), nullable=True),
        sa.Column("calorie_target_kcal", sa.Integer(), nullable=True),
        sa.Column("target_sessions_per_week", sa.Integer(), nullable=True),
        sa.Column("target_cardio_minutes_per_week", sa.Integer(), nullable=True),
        sa.Column("claude_can", sa.String(16), server_default=sa.text("'log'"), nullable=False),
        sa.Column("add_back_exercise_kcal", sa.Boolean(), server_default=sa.false(), nullable=False),
        _time("created_at"), _time("updated_at"), _time("last_seen_at"),
        sa.UniqueConstraint("provider", "subject", name="uq_user_provider_subject"),
    )
    op.create_index("ix_user_created_at", "user", ["created_at"])

    op.create_table(
        "exercise", *_id("exercise"),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("met", sa.Float(), nullable=True),
        sa.Column("compendium_code", sa.String(8), nullable=True),
        sa.UniqueConstraint("name", name="uq_exercise_name"),
    )

    op.create_table(
        "lift_session", *_id("lift_session"), *_owner("lift_session"),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        *_entry_columns(),
    )
    _entry_indexes("lift_session")
    op.create_index("ix_lift_session_user_id_dedupe_key_created_at", "lift_session",
                    ["user_id", "dedupe_key", "created_at"])

    op.create_table(
        "lift_set", *_id("lift_set"),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["lift_session.id"], name="fk_lift_set_session_id_lift_session",
                                ondelete="CASCADE"),
        *_exercise_fk("lift_set"),
        sa.Column("set_number", sa.Integer(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("reps", sa.Integer(), nullable=False),
    )
    op.create_index("ix_lift_set_session_id", "lift_set", ["session_id"])
    op.create_index("ix_lift_set_exercise_id", "lift_set", ["exercise_id"])

    op.create_table(
        "bodyweight", *_id("bodyweight"), *_owner("bodyweight"),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kg", sa.Float(), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        *_entry_columns(),
    )
    _entry_indexes("bodyweight")
    # One live weigh-in per person per day. A soft-deleted row does not count.
    op.create_index("uq_bodyweight_user_id_date_live", "bodyweight", ["user_id", "date"], unique=True,
                    sqlite_where=sa.text("deleted_at IS NULL"), postgresql_where=sa.text("deleted_at IS NULL"))

    op.create_table(
        "goal", *_id("goal"), *_owner("goal"),
        sa.Column("kind", sa.String(16), nullable=False),
        *_exercise_fk("goal", nullable=True),
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(8), nullable=False),
        sa.Column("start_value", sa.Float(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("direction", sa.String(4), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        _time("created_at"), _time("updated_at"), _time("closed_at", nullable=True),
        _sample(),
    )
    op.create_index("ix_goal_user_id_status", "goal", ["user_id", "status"])

    op.create_table(
        "call_log", *_id("call_log"), *_owner("call_log", nullable=True),
        sa.Column("function", sa.String(64), nullable=False),
        _time("started_at"),
        sa.Column("ms", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("result_bytes", sa.Integer(), nullable=False),
    )
    op.create_index("ix_call_log_started_at", "call_log", ["started_at"])
    op.create_index("ix_call_log_user_id_started_at", "call_log", ["user_id", "started_at"])
    op.create_index("ix_call_log_function_started_at", "call_log", ["function", "started_at"])

    op.create_table(
        "measurement", *_id("measurement"), *_owner("measurement"),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(8), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        *_entry_columns(),
    )
    _entry_indexes("measurement")
    op.create_index("ix_measurement_user_id_kind_date", "measurement", ["user_id", "kind", "date"])

    op.create_table(
        "saved_food", *_id("saved_food"), *_owner("saved_food"),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("name_key", sa.String(128), nullable=False),
        sa.Column("serving_label", sa.String(64), nullable=False),
        sa.Column("kcal_per_serving", sa.Float(), nullable=False),
        sa.Column("grams_per_serving", sa.Float(), nullable=True),
        _time("created_at"), _time("updated_at"), _time("deleted_at", nullable=True),
        _sample(),
        sa.UniqueConstraint("user_id", "name_key", name="uq_saved_food_user_id_name_key"),
    )

    op.create_table(
        "food_entry", *_id("food_entry"), *_owner("food_entry"),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("meal", sa.String(10), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("kcal", sa.Float(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("estimated", sa.Boolean(), nullable=False),
        sa.Column("saved_food_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["saved_food_id"], ["saved_food.id"], name="fk_food_entry_saved_food_id_saved_food",
                                ondelete="SET NULL"),
        sa.Column("servings", sa.Float(), nullable=True),
        sa.Column("usda_fdc_id", sa.Integer(), nullable=True),
        sa.Column("grams", sa.Float(), nullable=True),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        *_entry_columns(),
    )
    _entry_indexes("food_entry")
    op.create_index("ix_food_entry_user_id_saved_food_id", "food_entry", ["user_id", "saved_food_id"])

    op.create_table(
        "cardio_entry", *_id("cardio_entry"), *_owner("cardio_entry"),
        sa.Column("date", sa.Date(), nullable=False),
        *_exercise_fk("cardio_entry"),
        sa.Column("minutes", sa.Float(), nullable=False),
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("incline_pct", sa.Float(), nullable=True),
        sa.Column("speed_kmh", sa.Float(), nullable=True),
        sa.Column("avg_hr", sa.Integer(), nullable=True),
        sa.Column("machine_kcal", sa.Integer(), nullable=True),
        sa.Column("estimated_kcal", sa.Float(), nullable=True),
        sa.Column("estimate_weight_kg", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        *_entry_columns(),
    )
    _entry_indexes("cardio_entry")
    op.create_index("ix_cardio_entry_user_id_exercise_id_date", "cardio_entry", ["user_id", "exercise_id", "date"])

    op.create_table(
        "usda_food",
        sa.Column("fdc_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("data_type", sa.String(32), nullable=False),
        sa.Column("kcal_per_100g", sa.Float(), nullable=False),
        _time("fetched_at"),
        sa.PrimaryKeyConstraint("fdc_id", name="pk_usda_food"),
    )

    op.create_table(
        "usda_search",
        sa.Column("query_key", sa.String(128), nullable=False),
        sa.Column("fdc_ids", sa.Text(), nullable=False),
        _time("fetched_at"),
        sa.PrimaryKeyConstraint("query_key", name="pk_usda_search"),
    )


def downgrade() -> None:
    # Dropping a table drops its indexes with it.
    for table in reversed(TABLES):
        op.drop_table(table)
