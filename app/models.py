"""The v1 schema: every table for stages 2 to 5, as the stage 2 contract lists them.

- Every table has an integer id, except the two shared caches, which are keyed on the provider's own id.
- Times are timezone-aware UTC (UtcDateTime). Dates are the person's local date.
- Entry tables take their five shared columns from EntryColumns and their two indexes from entry_indexes().
- Deleting a user deletes everything they own, in the database (ON DELETE CASCADE).
- Allowed values for the string columns are listed here as tuples. The services check them; the
  schema does not, so a later stage can add a value without a migration.

The migration in migrations/versions/ builds these tables. A test checks the two agree.
"""

import datetime as dt
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, MetaData, String, Text, UniqueConstraint, false, text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app import db

# ---------------------------------------------------------------- allowed values

LOGGED_BY = ("web", "claude")
CLAUDE_CAN = ("read", "log", "log_delete")
EXERCISE_KINDS = ("lift", "cardio")
GOAL_KINDS = ("bodyweight", "lift", "cardio_best", "cardio_weekly", "calories", "sessions")
GOAL_DIRECTIONS = ("up", "down")
GOAL_STATUSES = ("open", "hit", "dropped")
MEASUREMENT_KINDS = ("waist", "hips", "chest", "arm", "thigh", "steps", "sleep_hours", "resting_hr", "water_ml")
MEALS = ("breakfast", "lunch", "dinner", "snack")
FOOD_SOURCES = ("you_said", "saved_food", "usda", "claude_estimate")


# ---------------------------------------------------------------- types and base

class UtcDateTime(TypeDecorator):
    """A timezone-aware UTC time on every database. SQLite stores no zone, so values are turned into
    UTC on the way in and marked UTC on the way out. A time without a zone is refused."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("A time without a time zone was given; use app.db.utcnow().")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _now() -> datetime:
    return db.utcnow()  # looked up at call time, so a test can replace the clock


# Stable constraint names on both databases, so a later migration can find them by name.
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _user_id(nullable: bool = False) -> Mapped:
    return mapped_column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=nullable)


class EntryColumns:
    """The five columns every entry table carries."""

    logged_by: Mapped[str] = mapped_column(String(8))  # one of LOGGED_BY
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


def entry_indexes(table: str) -> tuple[Index, Index]:
    """(user_id, date) and (user_id, created_at): on every entry table."""
    return (Index(f"ix_{table}_user_id_date", "user_id", "date"),
            Index(f"ix_{table}_user_id_created_at", "user_id", "created_at"))


# ---------------------------------------------------------------- tables

class User(Base):
    __tablename__ = "user"
    __table_args__ = (
        UniqueConstraint("provider", "subject"),
        Index("ix_user_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(255))
    issuer: Mapped[str | None] = mapped_column(String(255), nullable=True)  # last seen; web sign-in has none
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    time_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)  # IANA name; null = not set yet
    calorie_target_kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_sessions_per_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_cardio_minutes_per_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claude_can: Mapped[str] = mapped_column(String(16), default="log", server_default=text("'log'"))
    add_back_exercise_kcal: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)
    # Set by the code that edits the row; a last_seen_at touch is not an edit.
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)


class Exercise(Base):
    """The shared exercise list. Synced by name from app/exercises.py."""

    __tablename__ = "exercise"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(8))  # one of EXERCISE_KINDS
    category: Mapped[str] = mapped_column(String(16))
    met: Mapped[float | None] = mapped_column(Float, nullable=True)
    compendium_code: Mapped[str | None] = mapped_column(String(8), nullable=True)


class LiftSession(EntryColumns, Base):
    __tablename__ = "lift_session"
    __table_args__ = (
        *entry_indexes("lift_session"),
        Index("ix_lift_session_user_id_dedupe_key_created_at", "user_id", "dedupe_key", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    date: Mapped[dt.date] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")
    dedupe_key: Mapped[str] = mapped_column(String(64))

    sets: Mapped[list["LiftSet"]] = relationship(
        back_populates="session", order_by="LiftSet.set_number",
        cascade="all, delete-orphan", passive_deletes=True)


class LiftSet(Base):
    """One set in a session. Owned through its session; it has no user_id of its own."""

    __tablename__ = "lift_set"
    __table_args__ = (
        Index("ix_lift_set_session_id", "session_id"),
        Index("ix_lift_set_exercise_id", "exercise_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("lift_session.id", ondelete="CASCADE"))
    exercise_id: Mapped[int] = mapped_column(Integer, ForeignKey("exercise.id"))
    set_number: Mapped[int] = mapped_column(Integer)
    weight_kg: Mapped[float] = mapped_column(Float)
    reps: Mapped[int] = mapped_column(Integer)

    session: Mapped[LiftSession] = relationship(back_populates="sets")
    exercise: Mapped[Exercise] = relationship()


class Bodyweight(EntryColumns, Base):
    __tablename__ = "bodyweight"
    __table_args__ = (
        *entry_indexes("bodyweight"),
        # One live weigh-in per person per day. A soft-deleted row does not count.
        Index("uq_bodyweight_user_id_date_live", "user_id", "date", unique=True,
              sqlite_where=text("deleted_at IS NULL"), postgresql_where=text("deleted_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    date: Mapped[dt.date] = mapped_column(Date)
    kg: Mapped[float] = mapped_column(Float)
    dedupe_key: Mapped[str] = mapped_column(String(64))


class Goal(Base):
    __tablename__ = "goal"
    __table_args__ = (Index("ix_goal_user_id_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    kind: Mapped[str] = mapped_column(String(16))  # one of GOAL_KINDS
    exercise_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("exercise.id"), nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(8))
    start_value: Mapped[float] = mapped_column(Float)
    start_date: Mapped[dt.date] = mapped_column(Date)
    direction: Mapped[str] = mapped_column(String(4))  # one of GOAL_DIRECTIONS
    target_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="open")  # one of GOAL_STATUSES
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now, onupdate=_now)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    exercise: Mapped[Exercise | None] = relationship()


class CallLog(Base):
    """One row per MCP call: which function, how long, ok or an error code, the result size.
    Never an argument and never a result."""

    __tablename__ = "call_log"
    __table_args__ = (
        Index("ix_call_log_started_at", "started_at"),
        Index("ix_call_log_user_id_started_at", "user_id", "started_at"),
        Index("ix_call_log_function_started_at", "function", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = _user_id(nullable=True)
    function: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ms: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_bytes: Mapped[int] = mapped_column(Integer)


class Measurement(EntryColumns, Base):
    __tablename__ = "measurement"
    __table_args__ = (
        *entry_indexes("measurement"),
        Index("ix_measurement_user_id_kind_date", "user_id", "kind", "date"),
        # One live value per person, kind and day (migration 0002). A soft-deleted row does not count.
        Index("uq_measurement_user_id_kind_date_live", "user_id", "kind", "date", unique=True,
              sqlite_where=text("deleted_at IS NULL"), postgresql_where=text("deleted_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    date: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))  # one of MEASUREMENT_KINDS
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(8))
    dedupe_key: Mapped[str] = mapped_column(String(64))


class SavedFood(Base):
    __tablename__ = "saved_food"
    __table_args__ = (UniqueConstraint("user_id", "name_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    name: Mapped[str] = mapped_column(String(128))
    name_key: Mapped[str] = mapped_column(String(128))  # the name, casefolded
    serving_label: Mapped[str] = mapped_column(String(64))
    kcal_per_serving: Mapped[float] = mapped_column(Float)
    grams_per_serving: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class FoodEntry(EntryColumns, Base):
    """kcal is copied in when the entry is logged, so editing a saved food never rewrites history."""

    __tablename__ = "food_entry"
    __table_args__ = (
        *entry_indexes("food_entry"),
        Index("ix_food_entry_user_id_saved_food_id", "user_id", "saved_food_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    date: Mapped[dt.date] = mapped_column(Date)
    meal: Mapped[str] = mapped_column(String(10))  # one of MEALS
    description: Mapped[str] = mapped_column(String(200))
    kcal: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))  # one of FOOD_SOURCES
    estimated: Mapped[bool] = mapped_column(Boolean)
    saved_food_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("saved_food.id", ondelete="SET NULL"), nullable=True)
    servings: Mapped[float | None] = mapped_column(Float, nullable=True)
    usda_fdc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grams: Mapped[float | None] = mapped_column(Float, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(64))

    saved_food: Mapped[SavedFood | None] = relationship()


class CardioEntry(EntryColumns, Base):
    __tablename__ = "cardio_entry"
    __table_args__ = (
        *entry_indexes("cardio_entry"),
        Index("ix_cardio_entry_user_id_exercise_id_date", "user_id", "exercise_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    date: Mapped[dt.date] = mapped_column(Date)
    exercise_id: Mapped[int] = mapped_column(Integer, ForeignKey("exercise.id"))
    minutes: Mapped[float] = mapped_column(Float)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    incline_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_kmh: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    machine_kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimate_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)  # the weigh-in the estimate used
    notes: Mapped[str] = mapped_column(Text, default="")
    dedupe_key: Mapped[str] = mapped_column(String(64))

    exercise: Mapped[Exercise] = relationship()


class UsdaFood(Base):
    """Shared cache of food data, keyed on the food's own id."""

    __tablename__ = "usda_food"

    fdc_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    description: Mapped[str] = mapped_column(String(255))
    data_type: Mapped[str] = mapped_column(String(32))
    kcal_per_100g: Mapped[float] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)


class UsdaSearch(Base):
    """Shared cache of food searches. Holds no user id."""

    __tablename__ = "usda_search"

    query_key: Mapped[str] = mapped_column(String(128), primary_key=True)  # the normalised query
    fdc_ids: Mapped[str] = mapped_column(Text)  # a JSON list
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)


# ---------------------------------------------------------------- lists other code checks against

def user_tables() -> list[str]:
    """Every table with a user_id column, read from the schema itself, so a new table appears here
    without anyone remembering to add it. The isolation guard test compares its own list with this."""
    return sorted(t.name for t in Base.metadata.sorted_tables if "user_id" in t.c)


def entry_models() -> list[type[Base]]:
    """Every model built from EntryColumns: the tables with the duplicate guard and soft delete."""
    return sorted((m.class_ for m in Base.registry.mappers if issubclass(m.class_, EntryColumns)),
                  key=lambda cls: cls.__tablename__)
