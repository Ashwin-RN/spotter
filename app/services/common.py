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

The write limit. check_write_limit() runs in every write right after lock_user, before any other
read or work. Every call through MCP or the web writes its call-log row before the service runs
(error_code "in_progress", completed afterwards), so this call's own row is already counted. The
rule: more than WRITE_LIMIT write rows started in the last WRITE_WINDOW_MINUTES, this call's own
row among them, is refused (count > WRITE_LIMIT), web and Claude together. Nothing is saved and the
error says how many minutes to wait. delete_account and set_claude_can are exempt: the person must
always be able to leave, and to turn Claude down. A call that reaches the services with no row of its own (a
direct service call, as in the tests) therefore gets one more allowed write than a call through MCP
or the web.

An import call (import_entries) is one write, whatever it carries. Its rows have their own cap, IMPORT_LIMIT
imported rows created in any IMPORT_WINDOW_HOURS, which app.services.imports checks.

The read throttle. check_read_limit() counts one read function's rows the same way (find_food: more
than READ_LIMIT in WRITE_WINDOW_MINUTES is too_many_reads). It takes no lock: a read writes nothing.

The read limit. check_reads() counts every read-level function's rows together, the same way: more than
READS_LIMIT in WRITE_WINDOW_MINUTES is too_many_reads. The MCP wrapper runs it before any read-level
function's work, in its own short session; find_food keeps its own READ_LIMIT as well. A conversation makes
a handful of reads, so READS_LIMIT is only ever reached by a loop.

The duplicate guard. dedupe_key() is a SHA-256 of an entry's defining values, normalised.
find_duplicate() looks for a live entry of the same person with the same date and key, created in
the last DUPLICATE_MINUTES. It reads the entry's own created_at, and runs for the web and for
Claude alike.

A write's own key (contract idempotency.md). A caller may send a key with any write. The MCP wrapper and
the web run such a write through keyed(), which puts the call's claim in the context: the key, the
function's name, a fingerprint of what was sent, and the id of its write_key row once there is one.
lock_user() reads the claim. With the person's row held it looks for their row for that key, older than
KEY_HOURS counting as absent. A live row raises KeyKept: nothing else runs and the transaction rolls back.
No row: it writes one with an empty answer, in the write's own transaction, so the key is there if and
only if the write committed, and two sends at once cannot both pass. With no claim in the context
lock_user() does what it always did. Afterwards settle() keeps the first answer on the row, or deletes the
row of a refusal or a preview. A key sent again gets that answer back (keyed), key_reused when the call is
another one, and already_done when the first answer was not kept. A row with no answer yet is waited for, up
to ANSWER_WAIT, while it is younger than ANSWER_LOST: an older one is already_done at the first look, before
the work and the lock. Every read and delete of write_key names the person's id.
"""

import hashlib
import json
import logging
import math
import re
import time
from contextvars import ContextVar
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app import db, formatting
from app.calllog import KEY_HOURS
from app.models import CALLERS, CallLog, Exercise, User, WriteKey
from app.rules import as_sent, error, quote, span, to_float

log = logging.getLogger("spotter.common")

DUPLICATE_MINUTES = 10
KEY_SHAPE = re.compile(r"[A-Za-z0-9_-]{8,64}")  # a write's key: 8 to 64 letters, digits, - or _
ANSWER_MAX = 65_536  # bytes of UTF-8: a first answer larger than this is not kept
ANSWER_WAIT = 2.0  # seconds a key sent again waits for a first send that has written and not yet kept its answer
ANSWER_EVERY = 0.1  # seconds between its looks
ANSWER_LOST = 60  # seconds: a kept row older than this with no answer lost it, so no send waits for it
# The read that shows what a write did, named in already_done's fix. Any other write: get_log. A delete or a
# restore takes a saved food too, which get_log does not show.
ENTRY_OR_FOOD = "get_log, or find_food for a saved food"
READ_BACK = {"set_goal": "get_goals", "update_settings": "whoami", "save_food": "find_food",
             "delete_entry": ENTRY_OR_FOOD, "restore_entry": ENTRY_OR_FOOD}
# The claim of the call running: {key, function, fingerprint, row}. keyed() puts it in the context of the thread
# the work runs in, and lock_user sets row to the id of the write_key row it wrote.
_CLAIM: ContextVar[dict | None] = ContextVar("spotter_write_claim", default=None)
KEPT = (WriteKey.function, WriteKey.fingerprint, WriteKey.answer, WriteKey.created_at)  # what a look reads of a row

# How each kind is named in messages, and where Claude finds its ids.
KIND_LABELS = {
    "lift_session": ("lift session", "the id that log_lifts returned, or one from get_log"),
    "bodyweight": ("weigh-in", "the id that log_bodyweight returned, or one from get_log"),
    "measurement": ("measurement", "the id that log_measurement returned, or one from get_log"),
    "food_entry": ("food entry", "an id that log_food returned, or one from get_log"),
    "cardio_entry": ("cardio entry", "the id that log_cardio returned, or one from get_log"),
    "saved_food": ("saved food", "the id that save_food returned, or one from find_food"),
    "goal": ("goal", "the id that set_goal returned, or a goal_id from get_progress"),
}

# Every write, by the name its call-log rows carry (the MCP name, which the web uses too).
# Two writes are exempt on purpose, the person's own way out and their own switch: delete_account
# and set_claude_can. Their call-log rows are written but do not count, and they are never refused.
WRITE_FUNCTIONS = ("log_lifts", "log_bodyweight", "log_measurement", "log_food", "save_food", "log_cardio",
                   "set_goal", "update_settings", "edit_entry", "delete_entry", "restore_entry",
                   "import_entries")
WRITE_LIMIT = 120
WRITE_WINDOW_MINUTES = 10
READ_LIMIT = 120  # a throttled read (find_food): its own rows in the same 10 minutes
READS_LIMIT = 300  # every read-level function through MCP, together, in the same 10 minutes
# The import's own cap, beside the write limit: one import_entries call is one call-log row, so one write of the
# 120 however many rows it carries, a dry run too. So the rows themselves are capped here: at most IMPORT_LIMIT
# rows with logged_by "import" created in any IMPORT_WINDOW_HOURS (app.services.imports).
IMPORT_LIMIT = 10_000
IMPORT_WINDOW_HOURS = 24

def not_found(kind: str) -> dict:
    """The one "not found" for a kind. It never repeats the id and never says why, so it reads the
    same for a missing id, someone else's id and a deleted entry."""
    name, source = KIND_LABELS.get(kind, (kind.replace("_", " "), "an id from an earlier result"))
    return error("not_found", f"No {name} with that id.",
                 f"Check the id and the kind: use {source}.")


def no_account() -> dict:
    return error("no_account", "This account no longer exists.", "Sign in again, then retry.")


def check_by(by) -> dict | None:
    if by not in CALLERS:
        return error("invalid_by", f"by must be one of {', '.join(CALLERS)}.",
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
    such account. Call it first, before anything else reads in this session (see the module notes).
    With a claim in the context that has no row yet, the call's key is claimed here, under the lock:
    KeyKept when the person's row for that key is already there."""
    dialect = s.get_bind().dialect.name
    if dialect == "sqlite":
        if s.execute(lock_statement(dialect, user_id)).rowcount != 1:
            return None
    elif s.scalar(lock_statement(dialect, user_id)) is None:
        return None
    claim = _CLAIM.get()
    if claim is not None and claim["row"] is None:
        _claim_key(s, user_id, claim)
    return s.get(User, user_id)


# ---------------------------------------------------------------- a write's own key

class KeyKept(Exception):
    """lock_user's signal: this person's row for the call's key is already there. It carries that row's function,
    fingerprint, answer and created_at. No service catches it: it reaches keyed(), and the transaction rolls back."""

    def __init__(self, function: str, fingerprint: str, answer: str | None, created_at: datetime) -> None:
        super().__init__()
        self.kept = (function, fingerprint, answer, created_at)


def invalid_key() -> dict:
    return error("invalid_key", "key must be 8 to 64 letters, digits, - or _.",
                 "Send 8 to 64 letters, digits, - or _, or leave the key out.")


def key_reused() -> dict:
    return error("key_reused", "This key was used for another call.", "Send a new key for a new write.")


def already_done(function: str) -> dict:
    return error("already_done", "This call already ran. Its answer was not kept.",
                 f"Read it back with {READ_BACK.get(function, 'get_log')}. Send a new key for a new write.")


def is_key(value) -> bool:
    return isinstance(value, str) and KEY_SHAPE.fullmatch(value) is not None


def fingerprint(function: str, sent) -> str:
    """SHA-256 of the function's name and the arguments as sent, without the key, as canonical JSON."""
    plain = {k: v for k, v in sent.items() if k != "key"} if isinstance(sent, dict) else {}
    text = json.dumps([function, plain], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_claim(function: str, key: str, mark: str) -> dict:
    """The claim of a call that sent this key: mark is its fingerprint."""
    return {"key": key, "function": function, "fingerprint": mark, "row": None}


def _claim_key(s: Session, user_id: int, claim: dict) -> None:
    """Under the person's lock: an expired row for the key is deleted, a live one raises KeyKept, and with none
    the call's own row is written, its answer empty. It commits with the write or not at all."""
    mine = (WriteKey.user_id == user_id, WriteKey.key == claim["key"])
    s.execute(delete(WriteKey).where(*mine, WriteKey.created_at < db.utcnow() - timedelta(hours=KEY_HOURS))
              .execution_options(synchronize_session=False))
    kept = s.execute(select(*KEPT).where(*mine)).first()
    if kept is not None:
        raise KeyKept(*kept)
    row = WriteKey(user_id=user_id, key=claim["key"], function=claim["function"], fingerprint=claim["fingerprint"])
    s.add(row)
    s.flush()
    claim["row"] = row.id


def _kept(user_id: int, key: str):
    """This person's live row for the key, as (function, fingerprint, answer, created_at), or None. No lock."""
    with db.new_session() as s:
        return s.execute(select(*KEPT).where(
            WriteKey.user_id == user_id, WriteKey.key == key,
            WriteKey.created_at >= db.utcnow() - timedelta(hours=KEY_HOURS))).first()


def _answer(claim: dict, kept) -> tuple[dict | None, str | None] | None:
    """What a kept row answers this call: key_reused for another call, the first answer for the same one, and
    already_done when it has no answer and is older than ANSWER_LOST. None when there is no row, or a younger one
    with no answer on it yet."""
    if kept is None:
        return None
    function, mark, answer, created_at = kept
    if (function, mark) != (claim["function"], claim["fingerprint"]):
        return key_reused(), None
    if answer is not None:
        return None, answer
    lost = db.utcnow() - created_at > timedelta(seconds=ANSWER_LOST)
    return (already_done(function), None) if lost else None


def _waited(user_id: int, claim: dict, kept) -> tuple[dict | None, str | None] | None:
    """After lock_user's signal. The first send may sit between its commit and keeping its answer, so a young row
    is read again, with no lock, every ANSWER_EVERY for up to ANSWER_WAIT: still empty then is already_done. A row
    older than ANSWER_LOST is not waited for. None when the row has gone: the first send was refused, and its key
    is free."""
    until = time.monotonic() + ANSWER_WAIT
    while kept is not None:
        found = _answer(claim, kept)
        if found is not None:
            return found
        if time.monotonic() >= until:
            return already_done(claim["function"]), None
        time.sleep(ANSWER_EVERY)
        kept = _kept(user_id, claim["key"])
    return None


def keyed(user_id: int, claim: dict, work) -> tuple[dict | None, str | None]:
    """Run a write that carries a key, in the worker thread: (the work's result, None), or (None, the first
    answer's text) for the same call sent again, or (key_reused or already_done, None). First one look with no
    lock, which answers a kept key at once, before any of the work. Then the work, where lock_user decides. The
    work runs once more when the first send was refused while this one waited."""
    held = _CLAIM.set(claim)
    try:
        found = _answer(claim, _kept(user_id, claim["key"]))
        for _ in range(2):
            if found is not None:
                return found
            try:
                return work(), None
            except KeyKept as signal:
                found = _waited(user_id, claim, signal.kept)
        return found or (already_done(claim["function"]), None)
    finally:
        _CLAIM.reset(held)


def settle(user_id: int, claim: dict, result: dict, text: str | None) -> None:
    """After the work, when this call claimed its key's row. A typed error or a preview frees the key: the row is
    deleted. A good result keeps its answer: text, the reply as it is sent, when that is at most ANSWER_MAX
    bytes. Larger, or None because the reply failed, the answer stays empty and the next send is already_done.
    Its own short transaction. A failure is logged by type and swallowed."""
    if claim["row"] is None:
        return
    mine = (WriteKey.id == claim["row"], WriteKey.user_id == user_id)
    if "error" in result or "errors" in result or result.get("dry_run") is True:
        change = delete(WriteKey).where(*mine)
    elif text is not None and len(text.encode("utf-8")) <= ANSWER_MAX:
        change = update(WriteKey).where(*mine).values(answer=text)
    else:
        return
    try:
        with db.session_scope() as s:
            s.execute(change.execution_options(synchronize_session=False))
    except Exception as exc:  # noqa: BLE001 - a key that could not be settled never costs the person their answer
        log.warning("A write's key could not be settled: %s", type(exc).__name__)


def check_write_limit(s: Session, user_id: int) -> dict | None:
    """too_many_writes when this person has more than WRITE_LIMIT write rows started in the last
    WRITE_WINDOW_MINUTES, this call's own row among them (the MCP wrapper and the web write it,
    as "in_progress", before the service runs); else None. wait_minutes: until the oldest counted
    row leaves the window, at least 1. A direct service call has no own row, so it gets one more
    allowed write than a call through MCP or the web. Call it right after lock_user."""
    count, wait = _counted(s, user_id, WRITE_FUNCTIONS)
    if count <= WRITE_LIMIT:
        return None
    return error("too_many_writes",
                 f"{count} write calls in the last {WRITE_WINDOW_MINUTES} minutes, this one included; "
                 f"the most is {WRITE_LIMIT}. "
                 "Nothing was saved.",
                 f"Wait {wait} minute{'s' if wait != 1 else ''}, then send it again.", wait_minutes=wait)


def _counted(s: Session, user_id: int, functions) -> tuple[int, int]:
    """(rows, minutes to wait): this person's call-log rows of these functions started in the last
    WRITE_WINDOW_MINUTES, and the minutes until the oldest of them leaves the window, at least 1.
    The write limit and the read throttle count the same way, here."""
    now = db.utcnow()
    since = now - timedelta(minutes=WRITE_WINDOW_MINUTES)
    count, oldest = s.execute(select(func.count(CallLog.id), func.min(CallLog.started_at)).where(
        CallLog.user_id == user_id, CallLog.function.in_(functions), CallLog.started_at >= since)).one()
    if not count:
        return 0, 0
    leaves = oldest + timedelta(minutes=WRITE_WINDOW_MINUTES) - now
    return count, max(1, math.ceil(leaves.total_seconds() / 60))


def check_read_limit(s: Session, user_id: int, function: str, limit: int = READ_LIMIT) -> dict | None:
    """too_many_reads when this person has more than `limit` call-log rows of `function` started in the
    last WRITE_WINDOW_MINUTES, this call's own row among them (as the write limit counts); else None.
    wait_minutes as the write limit's. A read takes no lock: this is a count, and nothing is written."""
    count, wait = _counted(s, user_id, (function,))
    if count <= limit:
        return None
    return error("too_many_reads",
                 f"{count} {function} calls in the last {WRITE_WINDOW_MINUTES} minutes, this one included; "
                 f"the most is {limit}.",
                 f"Wait {wait} minute{'s' if wait != 1 else ''}, then look it up again. A food already found can be "
                 "logged by its id meanwhile.", wait_minutes=wait)


def check_reads(user_id: int, functions, limit: int = READS_LIMIT) -> dict | None:
    """too_many_reads when this person has more than `limit` call-log rows of these functions (every
    read-level one) started in the last WRITE_WINDOW_MINUTES, this call's own row among them; else None.
    Its own short session, no lock, nothing written: the MCP wrapper calls it before the read's work."""
    with db.new_session() as s:
        count, wait = _counted(s, user_id, tuple(functions))
    if count <= limit:
        return None
    return error("too_many_reads",
                 f"{count} read calls in the last {WRITE_WINDOW_MINUTES} minutes, this one included; "
                 f"the most is {limit}.",
                 f"Wait {wait} minute{'s' if wait != 1 else ''}, then ask again.", wait_minutes=wait)


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
            "so nothing was written. If it happened twice, send it again with allow_repeat: true.")


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


def pair(old, new, quantity: str, unit: bool = True) -> tuple[str, str]:
    """A before and an after through formatting.show. When the two print the same at the shown step
    but the stored values differ, both show one more decimal (widened), so a change never reads as none:
    the rule a new best and the one it beat follow."""
    shown = formatting.show(old, quantity, unit=unit), formatting.show(new, quantity, unit=unit)
    if shown[0] == shown[1] and old != new:
        return (formatting.show(old, quantity, widened=True, unit=unit),
                formatting.show(new, quantity, widened=True, unit=unit))
    return shown


def in_range(value, field: str, lo: float, hi: float, fix: str, low_open: bool = False,
             quantity: str = "goal_target") -> tuple[float | None, dict | None]:
    """A finite number from lo to hi (above lo when low_open), 2 dp, or an error carrying `fix`.
    quantity: how the range is shown in the message (app.formatting)."""
    number = to_float(value)
    if number is None:
        return None, error("invalid_field", f"{field} '{quote(value)}' is not a number.", fix)
    inside = lo < number <= hi if low_open else lo <= number <= hi
    if not inside:
        return None, error("invalid_field", f"{field} {as_sent(value)} is outside {span(lo, hi, quantity)}.", fix)
    return round(number, 2) + 0.0, None
