"""The call log: one row per function call, from MCP and the web, and a safe way to describe a crash.

A row holds the function's name, when it started, how long it took, ok or an error code, the size of
the result and whose account it was. Never an argument's value and never a result: those are the person's
data. Beside them, the trace (contract trace.md):
    request_id   the request's own id (REQUEST_ID), set by main.RequestId; the X-Request-Id header
    caller       web or claude (CALLERS)
    tried        what was sent, as names and counts only (tried): only KNOWN_NAMES are written
    outcome      read, written, duplicate, preview, refused or failed, or repeat for a key sent again (outcome)
    step         where a failed call stopped, from its error code (step)
    frame        the innermost frame in app/ of a crash, as file:line function (frame_of)
    notes        fixed words Spotter's own code set during the call (note; noted reads one back), as key=word

A call's row is written in two steps, because the write limit counts these rows:
    start(function, started_at, user_id, caller, tried) -> row id    before the service runs: in_progress
    finish(row_id, ms, ok, error_code, result_bytes, ...)              after it: the outcome
So a write is counted from the moment it starts, and writes queued behind one another all see each
other. Both are synchronous and use the database: callers run them in a worker thread. A database
error is logged by type and swallowed: a lost row never costs the person their answer.

Retention: a finished call deletes the rows older than CALL_LOG_DAYS, the first time after the process
starts and then at most once in SWEEP_HOURS (sweep). The same sweep deletes the write keys older than
KEY_HOURS (contract idempotency.md), at most once in KEY_SWEEP_HOURS, and on the call log's own clock the
entry versions older than VERSION_DAYS (contract edit-history.md) and the feedback older than FEEDBACK_DAYS
(contract feedback.md).

The sweep runs beside the calls (contract hardening.md, D8): finish() writes the row and returns, and when a sweep
is due and none runs in this process, start_sweep starts one on a thread of its own (tests/conftest.py puts sweep
itself in its place, so every test sweeps inline). Each delete goes in batches of SWEEP_BATCH rows a statement, each
its own transaction, at most SWEEP_BATCHES a table a run; a table's batches stop at its first failure, with its one
line. A group (the daily three, the hourly keys) is marked swept only when none of its deletes failed and each ended
with a short batch; otherwise its last swept time stays and it is tried again SWEEP_RETRY_MINUTES on.

crash_text(exc) describes an exception by its type and its stack frames only. It never includes the
exception's message: a database error's message can carry the statement's values, which are the
person's data.
"""

import logging
import threading
import traceback
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select, update

from app import db
from app.log_shape import table_word
from app.mcp_schemas import (
    FoodEntryIn, ImportFoodIn, ImportRowIn, ImportSetIn, LogPartIn, LogSetIn, MealItemIn, ProgramBlockIn,
    ProgramDayIn, RoutineLiftIn, SetIn,
)
from app.models import CallLog, EntryVersion, Feedback, WriteKey

log = logging.getLogger("spotter.calllog")

ERROR_CODE_MAX = 32  # the error_code column's length
IN_PROGRESS = "in_progress"  # a started row's error code until finish() records the outcome
TRIED_MAX, FRAME_MAX, NOTES_MAX = 400, 128, 128  # the columns' lengths
CALL_LOG_DAYS = 90
SWEEP_HOURS = 24
KEY_HOURS = 24  # how long a write's key is kept: an older row is absent, swept or not (services.common.lock_user)
KEY_SWEEP_HOURS = 1
VERSION_DAYS = 90  # how long an entry's version is kept: an older one is never put back, swept or not
FEEDBACK_DAYS = 365  # how long a note sent with send_feedback is kept, until the sweep after that
SWEEP_BATCH = 1000  # rows a delete statement of the sweep takes at most
SWEEP_BATCHES = 50  # statements a table takes at most in one sweep
SWEEP_RETRY_MINUTES = 10  # how soon a group whose sweep failed or stopped at the cap is due again
CALLERS = ("web", "claude")
# Spotter's nine tables: the one value tried writes (kind=food_entry), and an import's rows counted by kind.
TABLES = ("lift_session", "bodyweight", "measurement", "food_entry", "cardio_entry", "saved_food", "routine", "program",
          "goal")
# The functions whose kind names a table. Any other kind (a goal's, a measurement's) is a name only. Their kind is
# read as its table word (log_shape.table_word, log-and-guide.md decision 52), as the service reads it: lifts is
# written kind=lift_session.
TABLE_KINDS = frozenset({"edit_entry", "delete_entry", "restore_entry"})
# Every argument name of the MCP functions, and every field of their input models. tried writes no other name.
KNOWN_NAMES = frozenset({
    "account_id", "add_back_exercise_kcal", "allow_repeat", "avg_hr", "calorie_target_kcal", "category", "confirm",
    "date", "days", "description", "distance_km", "distance_mi", "distance_unit", "doing", "dry_run", "email",
    "entries", "entry_id",
    "exercise", "expected", "failed_only", "goal_id", "grams_per_serving", "happened", "hours", "ids", "include_closed",
    "incline_pct", "kcal", "kcal_per_serving", "key", "kg", "kind", "lb", "length_unit", "lifts", "limit",
    "machine_kcal",
    "meal",
    "measurement_kind", "minutes", "name", "new_session", "no_account", "notes", "query", "request_id", "rows",
    "search",  # list_exercises' (exercise-list.md R8)
    "serving_label", "sets", "speed_kmh", "speed_mph", "start_value", "status", "target_cardio_minutes_per_week",
    "target_date",
    "target_sessions_per_week", "target_value", "text", "time_zone", "topic", "undo_edit", "value", "weight_unit",
    # get_data's (contract data-on-request.md, decision 21). The keys inside filters and totals are field names:
    # the known ones are written in braces, the rest counted in other[n].
    "area", "fields", "filters", "from_date", "to_date", "group_by", "totals", "order", "cursor",
    # log's (contract log-and-guide.md): its parts, a part's movement (LogPartIn below), and its details, whose keys are
    # the kinds' own field names, written in braces as get_data's filters are. guide's topic is send_feedback's.
    "parts", "details", "movement",
    # owner_calls' filters (log-and-guide.md L4).
    "function", "caller", "all_accounts",
    # save_food's macros a serving and a meal's items (macros-and-asking.md). A food entry's macros and portion, which
    # edit_entry takes too, are FoodEntryIn's; an item's fields are MealItemIn's, below.
    "protein_g_per_serving", "carbs_g_per_serving", "fat_g_per_serving", "items",
    # update_settings' three daily goals in grams (macros-and-asking.md, P3).
    "protein_target_g", "carbs_target_g", "fat_target_g",
    # save_program's (routines-and-programs.md): a block's and a day's fields are ProgramBlockIn's and ProgramDayIn's.
    "start_on", "blocks",
}) | {name for model in (SetIn, LogSetIn, FoodEntryIn, ImportSetIn, ImportFoodIn, ImportRowIn, RoutineLiftIn, LogPartIn,
                         MealItemIn, ProgramBlockIn, ProgramDayIn)
       for name in model.model_fields}
# The step a failed call stopped at, by its error code. Any other code a service returns is the check.
STEPS = {"no_subject": "account", "signups_closed": "account", "no_account": "account",
         "not_allowed": "permission",
         "invalid_key": "key", "key_reused": "key", "already_done": "key",
         "too_many_reads": "limit", "too_many_writes": "limit", "too_many_imported": "limit",
         "too_much_feedback": "limit",
         "usda_off": "lookup", "usda_unavailable": "lookup",
         "entry_changed": "check",  # a web edit form older than the entry: the service's check under the lock
         "database_unavailable": "database", "internal_error": "crash"}
APP = Path(__file__).resolve().parent

# The request's id, for its length (main.RequestId). A worker thread reads its own copy.
REQUEST_ID: ContextVar[str | None] = ContextVar("spotter_request_id", default=None)
# The notes of the call running: a dict put in the context before its worker thread starts, so a note set
# in the thread reaches the row.
_NOTES: ContextVar[dict | None] = ContextVar("spotter_call_notes", default=None)
_swept: datetime | None = None
_swept_keys: datetime | None = None
_retry: datetime | None = None  # the daily group is not due before this: its last sweep failed or stopped at the cap
_retry_keys: datetime | None = None  # the same, for the write keys
_running = False  # a sweep started by start_sweep runs in this process now
_sweeping = threading.Lock()


def start(function: str, started_at: datetime, user_id: int | None, caller: str | None = None,
          tried: str | None = None) -> int | None:
    """Store a call's row before it runs, committed at once. Returns its id, or None when the database
    cannot take it (the call still runs)."""
    try:
        with db.session_scope() as session:
            row = CallLog(function=function, started_at=started_at, ms=0, ok=False, error_code=IN_PROGRESS,
                          result_bytes=0, user_id=user_id, request_id=REQUEST_ID.get(), caller=caller, tried=tried)
            session.add(row)
            session.flush()
            return row.id
    except Exception as exc:  # noqa: BLE001 - a lost row never costs the person their answer
        log.warning("The call log could not be written: %s", type(exc).__name__)
        return None


def finish(row_id: int | None, ms: int, ok: bool, error_code: str | None, result_bytes: int,
           outcome: str | None = None, step: str | None = None, frame: str | None = None,
           notes: dict | None = None) -> None:
    """Record how the call ended on the row start() wrote, then start a due sweep beside the call (start_sweep).
    Nothing to do when start() wrote none."""
    if row_id is None:
        return
    code = error_code[:ERROR_CODE_MAX] if isinstance(error_code, str) else None
    noted = " ".join(f"{key}={word}" for key, word in (notes or {}).items())[:NOTES_MAX] or None
    try:
        with db.session_scope() as session:
            session.execute(update(CallLog).where(CallLog.id == row_id).values(
                ms=ms, ok=ok, error_code=code, result_bytes=result_bytes, outcome=outcome, step=step,
                frame=frame, notes=noted))
    except Exception as exc:  # noqa: BLE001 - a lost outcome never costs the person their answer
        log.warning("The call log could not be written: %s", type(exc).__name__)
        return
    start_sweep()


def _due(swept: datetime | None, hours: int, retry: datetime | None, now: datetime) -> bool:
    """A group is due when it has never swept in this process or its hours have passed, and no retry time of its own
    lies ahead."""
    return (swept is None or now - swept >= timedelta(hours=hours)) and (retry is None or retry <= now)


def _due_now(now: datetime) -> tuple[bool, bool]:
    """(the daily group is due, the keys are due). Read under _sweeping."""
    return _due(_swept, SWEEP_HOURS, _retry, now), _due(_swept_keys, KEY_SWEEP_HOURS, _retry_keys, now)


def start_sweep() -> None:
    """Start the sweep on a thread of its own, when a group is due and no sweep started here still runs. The call
    that finished never waits on it. tests/conftest.py puts sweep in its place."""
    global _running
    with _sweeping:
        if _running or not any(_due_now(db.utcnow())):
            return
        _running = True
    try:
        threading.Thread(target=_sweep_beside, daemon=True).start()
    except BaseException:
        with _sweeping:
            _running = False
        raise


def _sweep_beside() -> None:
    """The thread start_sweep starts: the sweep, then the next one may start."""
    global _running
    try:
        sweep()
    finally:
        with _sweeping:
            _running = False


def sweep() -> None:
    """Delete the rows older than CALL_LOG_DAYS, the entry versions older than VERSION_DAYS and the feedback older
    than FEEDBACK_DAYS: the first time, then at most once in SWEEP_HOURS. And the write keys older than KEY_HOURS:
    the first time, then at most once in KEY_SWEEP_HOURS. Each delete goes in batches, each its own transaction, so
    one that fails never costs another. A failure is logged by type and swallowed. A group is marked swept only when
    every delete of it finished; otherwise it is due again SWEEP_RETRY_MINUTES on (the module notes)."""
    global _swept, _swept_keys, _retry, _retry_keys
    now = db.utcnow()
    with _sweeping:
        calls, keys = _due_now(now)
    retry = now + timedelta(minutes=SWEEP_RETRY_MINUTES)
    if calls:
        done = [_delete_old("call log", CallLog.started_at, now - timedelta(days=CALL_LOG_DAYS)),
                _delete_old("entry versions", EntryVersion.created_at, now - timedelta(days=VERSION_DAYS)),
                _delete_old("feedback", Feedback.created_at, now - timedelta(days=FEEDBACK_DAYS))]
        with _sweeping:
            if all(done):
                _swept, _retry = now, None
            else:
                _retry = retry
    if keys:
        done = _delete_old("write keys", WriteKey.created_at, now - timedelta(hours=KEY_HOURS))
        with _sweeping:
            if done:
                _swept_keys, _retry_keys = now, None
            else:
                _retry_keys = retry


def _delete_old(what: str, column, before: datetime) -> bool:
    """One of the sweep's deletes: the rows of the column's table older than `before`, at most SWEEP_BATCH a
    statement and SWEEP_BATCHES statements, each in its own transaction. True when it finished: a batch came back
    short. False at the cap, or at the first failure, which is logged by type and stops the table's batches."""
    model = column.class_
    for _ in range(SWEEP_BATCHES):
        try:
            with db.session_scope() as session:
                batch = select(model.id).where(column < before).order_by(model.id).limit(SWEEP_BATCH)
                gone = session.execute(delete(model).where(model.id.in_(batch))
                                       .execution_options(synchronize_session=False)).rowcount
        except Exception as exc:  # noqa: BLE001 - the sweep never fails a call
            log.warning("The %s could not be swept: %s", what, type(exc).__name__)
            return False
        if gone < SWEEP_BATCH:
            return True
    return False


def new_notes() -> dict:
    """A fresh notes dict for the call about to run, put in the current context. Returns it for finish()."""
    notes: dict = {}
    _NOTES.set(notes)
    return notes


def note(key: str, word: str) -> None:
    """Note a part that failed inside a call that went on, in Spotter's own fixed words. No call: nothing."""
    notes = _NOTES.get()
    if notes is not None:
        notes[key] = word


def noted(key: str) -> str | None:
    """The word noted under key in the call running, or None."""
    return (_NOTES.get() or {}).get(key)


def room(key: str) -> int:
    """The characters the notes column has left for the word of a note under key, after the other notes the call
    running holds (finish joins each as key=word, one space between): outside a call, or as its first note, the whole
    column less 'key='. log's kind= and word= are noted first, so a later note, such as the per-hand check's pair=,
    sizes itself to what they leave (log-and-guide.md)."""
    held = " ".join(f"{k}={word}" for k, word in (_NOTES.get() or {}).items() if k != key)
    return NOTES_MAX - len(held) - (1 if held else 0) - len(key) - 1


def unnote(key: str) -> None:
    """Take the note under key off the call running, to make room for another. No call, or no such note: nothing."""
    notes = _NOTES.get()
    if notes is not None:
        notes.pop(key, None)


def tried(function: str, sent) -> str:
    """What a call sent, as names and counts: the known names sent, sorted. A list is name[n], a list of objects
    name[n]{fields any item sent}, an object name{fields}; one level deep. kind shows its value when it is a table
    name and the function's kind is one (TABLE_KINDS), read as its table word, and a list of objects with kinds
    counts them by table (rows.kind{food_entry=2}). Any other name is counted in other[n], never written. Cut at a
    whole name past TRIED_MAX, ending " +more"."""
    other: set = set()

    def known(item: dict) -> list[str]:
        other.update(k for k in item if k not in KNOWN_NAMES)
        return sorted(k for k in item if k in KNOWN_NAMES)

    parts = []
    for name, value in (sent if isinstance(sent, dict) else {}).items():
        if name not in KNOWN_NAMES:
            other.add(name)
        elif isinstance(value, list):
            items = [v for v in value if isinstance(v, dict)]
            inner = "{" + ",".join(sorted(set().union(*(known(v) for v in items)))) + "}" if items else ""
            parts.append((name, f"{name}[{len(value)}]{inner}"))
            kinds = Counter(v.get("kind") for v in items if v.get("kind") in TABLES)
            if kinds:
                parts.append((name + ".kind", name + ".kind{" + ",".join(f"{k}={n}" for k, n in sorted(kinds.items()))
                              + "}"))
        elif isinstance(value, dict):
            parts.append((name, name + "{" + ",".join(known(value)) + "}"))
        else:
            if name == "kind" and function in TABLE_KINDS:
                value = table_word(value)
            table = name == "kind" and function in TABLE_KINDS and value in TABLES
            parts.append((name, f"kind={value}" if table else name))
    if other:
        parts.append(("other", f"other[{len(other)}]"))
    words = [word for _, word in sorted(parts)]
    if len(" ".join(words)) <= TRIED_MAX:
        return " ".join(words)
    kept: list[str] = []
    for word in words:
        if len(" ".join([*kept, word, "+more"])) > TRIED_MAX:
            break
        kept.append(word)
    return " ".join([*kept, "+more"])


def outcome(result: dict | None, write: bool, repeat: bool = False) -> str:
    """Whether the call did it, from the work's result: None when the work itself crashed or lost the database.
    repeat: a key sent again got its first answer back, and nothing ran. A new goal the guard did not set twice
    is a duplicate: its result has no duplicate field, so the call's own note says it (goal=duplicate). An
    owner_delete_feedback that deleted a note wrote, though it sits at read, and its note says so too
    (feedback=deleted)."""
    if repeat:
        return "repeat"
    if result is None:
        return "failed"
    if "error" in result or "errors" in result:
        return "refused"
    if result.get("duplicate") is True or noted("goal") == "duplicate":
        return "duplicate"
    if result.get("dry_run") is True:
        return "preview"
    return "written" if write or noted("feedback") == "deleted" else "read"


def step(error_code: str | None, reply: bool = False) -> str | None:
    """Where a failed call stopped, from its error code; reply when the work finished and only its answer
    failed. None on success."""
    if error_code is None:
        return None
    return "reply" if reply else STEPS.get(error_code, "check")


def frame_of(exc: BaseException) -> str | None:
    """The innermost frame of the exception that lies in app/, as services/food.py:306 day_numbers. No message."""
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        path = Path(frame.filename).resolve()
        if path.is_relative_to(APP):
            return f"{path.relative_to(APP).as_posix()}:{frame.lineno} {frame.name}"[:FRAME_MAX]
    return None


def crash_text(exc: BaseException) -> str:
    """The exception's type and where it was raised, one frame a line. No message, no source text."""
    frames = traceback.extract_tb(exc.__traceback__)
    lines = [f"  {frame.filename}:{frame.lineno} in {frame.name}" for frame in frames]
    return "\n".join([type(exc).__name__, *lines])
