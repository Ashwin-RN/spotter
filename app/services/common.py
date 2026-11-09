"""What every service module shares: the one by-id lookup, the user-row lock, the duplicate guard,
and the small checks and conversions the write functions all need.

Own data only. owned() is the only way any service reads a row by its id. It filters on the
person's user_id and, unless asked for a restore, on deleted_at IS NULL. An id that belongs to
someone else, a soft-deleted row and an id that does not exist all come back as None, and every
caller turns None into not_found(kind): the same dict, word for word, whichever it was.

One write at a time per person. lock_user() is the first statement of every write. On Postgres it
is SELECT ... FOR UPDATE on the user row. SQLite has no row locks, so there it is an UPDATE that
changes nothing: it takes SQLite's write lock, which the transaction holds until it ends. Either
way a second write for the same person waits until the first has committed, so two retries of the
same message run one after the other and the duplicate guard sees the first. It must come before
any read in the transaction: a SQLite connection that has already read cannot wait for the write
lock, and fails at once instead.

The duplicate guard. dedupe_key() is a SHA-256 of an entry's defining values, normalised.
find_duplicate() looks for a live entry of the same person with the same date and key, created in
the last DUPLICATE_MINUTES. It reads the entry's own created_at, and runs for the web and for
Claude alike.
"""

import hashlib
import json
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import db
from app.models import LOGGED_BY, Exercise, User
from app.rules import error, quote, to_float

DUPLICATE_MINUTES = 10

# How each kind is named in messages, and where Claude finds its ids.
KIND_LABELS = {
    "lift_session": ("lift session", "the id that log_lifts returned"),
    "bodyweight": ("weigh-in", "the id that log_bodyweight returned"),
    "goal": ("goal", "the id that set_goal returned, or a goal_id from get_progress"),
}


def not_found(kind: str) -> dict:
    """The one "not found" for a kind. It never repeats the id and never says why, so it reads the
    same for a missing id, someone else's id and a deleted entry."""
    name, source = KIND_LABELS.get(kind, (kind.replace("_", " "), "an id from an earlier result"))
    return error("not_found", f"No {name} with that id.",
                 f"Check the id and the kind: use {source}.")


def no_account() -> dict:
    return error("no_account", "This account no longer exists.", "Sign in again, then retry.")


def check_by(by) -> dict | None:
    if by not in LOGGED_BY:
        return error("invalid_by", f"by must be one of {', '.join(LOGGED_BY)}.",
                     "This is a server problem, not something to retry differently.")
    return None


def check_data(data) -> tuple[dict, dict | None]:
    """The data argument as a dict. None means empty."""
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return {}, error("invalid_field", "data must be an object of named fields.",
                         "Send the fields by name, as the function's schema lists them.")
    return data, None


def truthy(value) -> bool:
    """A yes from a web form ("1", "on", "true", "yes") or from Claude (True)."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return value is True or value == 1


MAX_ID = 2 ** 31 - 1  # the largest id an integer column holds on both databases
ID_DIGITS = len(str(MAX_ID))


def entry_id(value) -> int | None:
    """A row id as an int, or None for anything that cannot be one. Accepted: an int, or a string of
    at most 10 ASCII digits, from 1 to 2**31 - 1. Refused: bools, fractions, other digit
    characters ("²", "１"), and anything out of range."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not (value.isascii() and value.isdigit() and len(value) <= ID_DIGITS):
            return None
        value = int(value)
    if not isinstance(value, int):
        return None
    return value if 1 <= value <= MAX_ID else None


def owned(s: Session, model, user_id: int, raw_id, *, deleted: bool = False):
    """The one by-id lookup. The row, only if it belongs to this person; else None.

    deleted=False (every read, edit and delete): a soft-deleted row is None too.
    deleted=True (restore only): the row whether or not it is deleted.
    """
    number = entry_id(raw_id)
    if number is None:
        return None
    query = select(model).where(model.id == number, model.user_id == user_id)
    if not deleted and "deleted_at" in model.__table__.c:
        query = query.where(model.deleted_at.is_(None))
    return s.scalar(query)


def lock_statement(dialect: str, user_id: int):
    """The statement that locks the user row: an UPDATE that changes nothing on SQLite, else
    SELECT ... FOR UPDATE."""
    if dialect == "sqlite":
        return (update(User).where(User.id == user_id).values(claude_can=User.claude_can)
                .execution_options(synchronize_session=False))
    return select(User.id).where(User.id == user_id).with_for_update()


def lock_user(s: Session, user_id: int) -> User | None:
    """Lock this person's row for the rest of the transaction, then return it. None if there is no
    such account. Call it first, before anything else reads in this session (see the module notes)."""
    dialect = s.get_bind().dialect.name
    if dialect == "sqlite":
        if s.execute(lock_statement(dialect, user_id)).rowcount != 1:
            return None
    elif s.scalar(lock_statement(dialect, user_id)) is None:
        return None
    return s.get(User, user_id)


def _plain(value):
    if isinstance(value, float):
        return f"{value + 0.0:.2f}"  # + 0.0 turns -0.0 into 0.0
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def dedupe_key(*values) -> str:
    """SHA-256 of the defining values, normalised: numbers to 2 dp, dates as ISO text, lists kept
    in the order given (callers sort them first when order does not matter)."""
    text = json.dumps(_plain(list(values)), separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_duplicate(s: Session, model, user_id: int, on: date, key: str):
    """The first live entry of this person with the same date and key, created in the last
    DUPLICATE_MINUTES, or None."""
    since = db.utcnow() - timedelta(minutes=DUPLICATE_MINUTES)
    return s.scalar(select(model).where(
        model.user_id == user_id, model.date == on, model.dedupe_key == key,
        model.deleted_at.is_(None), model.created_at >= since,
    ).order_by(model.created_at, model.id).limit(1))


def duplicate_note(noun: str) -> str:
    return (f"This {noun} was already logged in the last {DUPLICATE_MINUTES} minutes with the same content, "
            "so nothing was written. If it really happened twice, send it again with allow_repeat: true.")


def exercises(s: Session, kind: str | None = None) -> list[Exercise]:
    query = select(Exercise).order_by(Exercise.id)
    if kind is not None:
        query = query.where(Exercise.kind == kind)
    return list(s.scalars(query))


HIDDEN_COLUMNS = ("user_id", "dedupe_key")


def row_dict(row) -> dict:
    """Every column of a row as JSON-ready values, except the owner and the duplicate key.
    Works for any table, so a new entry kind needs no new code here."""
    out = {}
    for column in row.__table__.columns:
        if column.name in HIDDEN_COLUMNS:
            continue
        value = getattr(row, column.key)
        out[column.name] = value.isoformat() if isinstance(value, (date, datetime)) else value
    return out


def in_range(value, field: str, lo: float, hi: float, fix: str, low_open: bool = False) -> tuple[float | None, dict | None]:
    """A finite number from lo to hi (above lo when low_open), 2 dp, or an error carrying `fix`."""
    number = to_float(value)
    if number is None:
        return None, error("invalid_field", f"{field} '{quote(value)}' is not a number.", fix)
    inside = lo < number <= hi if low_open else lo <= number <= hi
    if not inside:
        return None, error("invalid_field", f"{field} {number:g} is outside {lo:g} to {hi:g}.", fix)
    return round(number, 2) + 0.0, None
