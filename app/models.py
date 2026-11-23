"""The v1 schema: every table for stages 2 to 5, as the stage 2 contract lists them, and the one column
stage 5 added (goal.measurement_kind, migration 0003). Migration 0004 dropped one column from seven tables.
Migration 0005 added the call log's trace columns, 0006 the write keys (write_key), 0007 the values an
entry held before each edit (entry_version), and 0008 the notes people send Spotter's maker (feedback). 0009
changes data only: every account's claude_can becomes log_delete. 0010 adds the routines (routine). 0011 adds
the person's units (user.weight_unit and user.distance_unit). 0012 (contract hardening.md) widens saved_food.name_key, adds
the sweep's three created_at indexes, one set number per session and lift, a food entry's saved food tied to its owner,
a 64-bit call-log id on Postgres, and the database's own rules. 0013 (contract exercise-list.md) gives each exercise a
stable key and a retired mark, and a set its time_s and distance_m, with reps now nullable. 0015 (contract
macros-and-asking.md) adds protein, carbs and fat in grams to a food entry, a saved food and the USDA cache, a food
entry's macros_estimated mark and portion, a saved meal's items, the cache's macros_read mark, and the person's three
daily amounts in grams.

- Every table has an integer id, except the two shared caches, which are keyed on the provider's own id.
- Times are timezone-aware UTC (UtcDateTime). Dates are the person's local date.
- Entry tables take their four shared columns from EntryColumns and their two indexes from entry_indexes().
- Deleting a user deletes everything they own, in the database (ON DELETE CASCADE).
- Allowed values for the string columns are listed here as tuples, and the services check them. Since 0012 the
  database also checks these closed lists: LOGGED_BY on the five entry tables, CLAUDE_CAN, the two units, GOAL_DIRECTIONS,
  GOAL_STATUSES, and entry_version's by (CALLERS) and why. A new value in one of those needs a one-line migration. The
  lists meant to grow stay unchecked: goal and measurement kinds, a measurement's unit, meals, food sources, feedback
  topics, an exercise's kind and category.
- Since 0012 the database also holds a sign rule and a wide ceiling on every number a person's row holds, and three
  goal shape rules. The rules' text lives only in migrations/versions/0012_hardening.py and, for the rules 0013 changes
  and adds on lift_set, in migrations/versions/0013_exercise_list.py, and for 0015's new numbers in
  migrations/versions/0015_macros.py: the models declare none, and compare_metadata does not read them.

The migration in migrations/versions/ builds these tables. A test checks the two agree.
"""

import datetime as dt
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, Integer, MetaData, String, Text,
    UniqueConstraint, false, text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app import db

# ---------------------------------------------------------------- allowed values

IMPORTED = "import"  # logged_by of every row import_entries writes, whoever called it
LOGGED_BY = ("web", "claude", IMPORTED)
CALLERS = ("web", "claude")  # who a write's `by` may name: the import's mark is never a caller
CLAUDE_CAN = ("log_delete", "log")  # the two settings, the default first (contract switch: read only is gone)
EXERCISE_KINDS = ("lift", "cardio")
GOAL_KINDS = ("bodyweight", "lift", "measurement", "cardio_best", "cardio_weekly", "calories", "sessions")
GOAL_DIRECTIONS = ("up", "down")
GOAL_STATUSES = ("open", "hit", "dropped")
MEASUREMENT_KINDS = ("waist", "hips", "chest", "arm", "thigh", "steps", "sleep_hours", "resting_hr", "water_ml")
MEALS = ("breakfast", "lunch", "dinner", "snack")
FOOD_SOURCES = ("you_said", "saved_food", "usda", "claude_estimate")
FEEDBACK_TOPICS = ("logging", "food", "lifts", "cardio", "goals", "import", "pages", "setup", "other")


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
    """The four columns every entry table carries."""

    logged_by: Mapped[str] = mapped_column(String(8))  # one of LOGGED_BY
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


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
    # The model's default decides every new account. The database's stays 'log': SQLite would have to rebuild user.
    claude_can: Mapped[str] = mapped_column(String(16), default="log_delete", server_default=text("'log'"))
    add_back_exercise_kcal: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)
    # Set by the code that edits the row; a last_seen_at touch is not an edit.
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)
    # How pages and summaries show weights (kg or lb) and distances (km or mi); null = not chosen, shown as kg
    # and km. Every stored number stays in kg and km whatever these say (contract units.md).
    weight_unit: Mapped[str | None] = mapped_column(String(2), nullable=True)
    distance_unit: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # The daily amounts in grams beside calorie_target_kcal (0015); null = no goal set.
    protein_target_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    carbs_target_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fat_target_g: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Exercise(Base):
    """The shared exercise list. Synced by key from app/exercises.py. A row is never deleted: one whose key is no
    longer listed is retired, and keeps its sets."""

    __tablename__ = "exercise"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)  # lower snake, from the name by the rule (0013)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(8))  # one of EXERCISE_KINDS
    category: Mapped[str] = mapped_column(String(16))
    met: Mapped[float | None] = mapped_column(Float, nullable=True)
    compendium_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    retired: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())  # no longer on the list


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
        UniqueConstraint("session_id", "exercise_id", "set_number"),  # one set number per session and lift (0012)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("lift_session.id", ondelete="CASCADE"))
    exercise_id: Mapped[int] = mapped_column(Integer, ForeignKey("exercise.id"))
    set_number: Mapped[int] = mapped_column(Integer)
    weight_kg: Mapped[float] = mapped_column(Float)
    reps: Mapped[int | None] = mapped_column(Integer, nullable=True)  # null for a hold or a carry (0013)
    time_s: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)  # metres

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
    # A measurement goal's kind (one of MEASUREMENT_KINDS); null for every other kind. Migration 0003.
    measurement_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
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

    exercise: Mapped[Exercise | None] = relationship()


class CallLog(Base):
    """One row per MCP call and web write: which function, how long, ok or an error code, the result size,
    and its trace (app/calllog.py). Never an argument's value and never a result."""

    __tablename__ = "call_log"
    __table_args__ = (
        Index("ix_call_log_started_at", "started_at"),
        Index("ix_call_log_user_id_started_at", "user_id", "started_at"),
        Index("ix_call_log_function_started_at", "function", "started_at"),
        Index("ix_call_log_request_id", "request_id"),
    )

    # 64 bits (0012): BIGINT on Postgres, with its sequence. SQLite's INTEGER primary key is already a 64-bit rowid,
    # and only INTEGER keeps it numbering itself.
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id: Mapped[int | None] = _user_id(nullable=True)
    function: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ms: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_bytes: Mapped[int] = mapped_column(Integer)
    # The trace (migration 0005): null on rows older than it.
    request_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    caller: Mapped[str | None] = mapped_column(String(8), nullable=True)
    tried: Mapped[str | None] = mapped_column(String(400), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    step: Mapped[str | None] = mapped_column(String(16), nullable=True)
    frame: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(128), nullable=True)


class WriteKey(Base):
    """One row per person and key a write carried (migration 0006): which function, a fingerprint of what was
    sent, and the first answer once it is kept. Written in the write's own transaction, under the person's lock
    (services.common.lock_user). Never an argument. Deleted with the account."""

    __tablename__ = "write_key"
    __table_args__ = (UniqueConstraint("user_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    key: Mapped[str] = mapped_column(String(64))
    function: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)  # null until the first answer is kept
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now, index=True)  # the sweep's (0012)


class EntryVersion(Base):
    """The values an entry held before an edit or an undo (migration 0007, contract edit-history.md): the row as it
    stood, as JSON, a lift session's sets with it. Kept in the write's own transaction, before the row changes
    (services.common.keep_version). kind is the entry's table and entry_id its id there, with no foreign key: the
    kinds are six tables. Deleted with the account."""

    __tablename__ = "entry_version"
    __table_args__ = (Index("ix_entry_version_user_id_kind_entry_id_id", "user_id", "kind", "entry_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    kind: Mapped[str] = mapped_column(String(16))
    entry_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now, index=True)  # the sweep's (0012)
    by: Mapped[str] = mapped_column("by", String(8), quote=True)  # one of CALLERS. Quoted: BY is SQL's own word
    why: Mapped[str] = mapped_column(String(8))  # edit or undo
    request_id: Mapped[str | None] = mapped_column(String(16), nullable=True)  # the call log's, null outside a request
    before: Mapped[str] = mapped_column(Text)  # JSON


class Feedback(Base):
    """A note a person sent Spotter's maker with send_feedback (migration 0008, contract feedback.md): their words,
    three optional parts and a topic. dedupe_key is a fingerprint of the five, for the 10-minute rule: never shown,
    never exported. No column says read or done: the owner deletes a note once it is dealt with. Deleted with the
    account."""

    __tablename__ = "feedback"
    __table_args__ = (Index("ix_feedback_user_id_created_at", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now, index=True)  # the sweep's (0012)
    text: Mapped[str] = mapped_column(Text)
    doing: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")
    happened: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str | None] = mapped_column(String(16), nullable=True)  # one of FEEDBACK_TOPICS
    request_id: Mapped[str | None] = mapped_column(String(16), nullable=True)  # the call log's, null outside a request
    dedupe_key: Mapped[str] = mapped_column(String(64))


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
    __table_args__ = (
        UniqueConstraint("user_id", "name_key"),
        UniqueConstraint("user_id", "id"),  # what a food entry's owner key points at (0012)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    name: Mapped[str] = mapped_column(String(128))
    # The name, casefolded: one character can fold to three, so 128 at 3 each (0012).
    name_key: Mapped[str] = mapped_column(String(384))
    serving_label: Mapped[str] = mapped_column(String(64))
    kcal_per_serving: Mapped[float] = mapped_column(Float)
    grams_per_serving: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Grams a serving (0015); null = not logged, never 0 g. A meal's is null when any of its items lacks it.
    protein_g_per_serving: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g_per_serving: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g_per_serving: Mapped[float | None] = mapped_column(Float, nullable=True)
    items: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON for a meal; null for a plain food (0015)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class Routine(Base):
    """A workout saved by name (migration 0010, contract routines.md). Not an entry table: no entry kind, no duplicate
    key, so the log, the import and the trends never see it. lifts is JSON text, each item {exercise, sets, reps,
    weight_kg (null when none was given)}; saved_on is the person's date of the last save. Deleted with the account."""

    __tablename__ = "routine"
    __table_args__ = (UniqueConstraint("user_id", "name_key"),)  # the one index: every read is by user_id

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = _user_id()
    name: Mapped[str] = mapped_column(String(64))
    # The name, spaces folded, casefolded. Room to spare: one character can fold to three.
    name_key: Mapped[str] = mapped_column(String(255))
    lifts: Mapped[str] = mapped_column(Text)  # JSON
    saved_on: Mapped[dt.date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class FoodEntry(EntryColumns, Base):
    """kcal is copied in when the entry is logged, so editing a saved food never rewrites history.

    A saved food has the entry's own owner (0012): the key on (user_id, saved_food_id) to saved_food (user_id, id) is
    checked at commit, after the key on saved_food_id alone has nulled a link to a saved food deleted for good."""

    __tablename__ = "food_entry"
    __table_args__ = (
        *entry_indexes("food_entry"),
        Index("ix_food_entry_user_id_saved_food_id", "user_id", "saved_food_id"),
        ForeignKeyConstraint(["user_id", "saved_food_id"], ["saved_food.user_id", "saved_food.id"],
                             deferrable=True, initially="DEFERRED"),
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
    # Grams for what was eaten (0015); null = not logged, never 0 g.
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    # True: the macros are the assistant's estimate; false: the person's own. Null when no macro is held (0015).
    macros_estimated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    portion: Mapped[str | None] = mapped_column(String(64), nullable=True)  # how much was eaten, the person's words
    dedupe_key: Mapped[str] = mapped_column(String(64))

    # Two keys lead to saved_food: the relationship goes by saved_food_id alone, on saved_food.id.
    saved_food: Mapped[SavedFood | None] = relationship(foreign_keys=[saved_food_id])


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
    # Grams a 100 g (0015); null = the food data gives none.
    protein_g_per_100g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g_per_100g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g_per_100g: Mapped[float | None] = mapped_column(Float, nullable=True)
    # False on a row cached before 0015: stale, fetched again, as one older than 30 days is.
    macros_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


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
