"""The call log: one row per function call, from MCP and the web, and a safe way to describe a crash.

A row holds the function's name, when it started, how long it took, ok or an error code, the size of
the result and whose account it was. Never an argument and never a result: those are the person's data.

A call's row is written in two steps, because the write limit counts these rows:
    start(function, started_at, user_id) -> row id    before the service runs: the row says in_progress
    finish(row_id, ms, ok, error_code, result_bytes)   after it: the outcome
So a write is counted from the moment it starts, and writes queued behind one another all see each
other. Both are synchronous and use the database: callers run them in a worker thread. A database
error is logged by type and swallowed: a lost row never costs the person their answer.

crash_text(exc) describes an exception by its type and its stack frames only. It never includes the
exception's message: a database error's message can carry the statement's values, which are the
person's data.
"""

import logging
import traceback
from datetime import datetime

from sqlalchemy import update

from app import db
from app.models import CallLog

log = logging.getLogger("spotter.calllog")

ERROR_CODE_MAX = 32  # the error_code column's length
IN_PROGRESS = "in_progress"  # a started row's error code until finish() records the outcome


def start(function: str, started_at: datetime, user_id: int | None) -> int | None:
    """Store a call's row before it runs, committed at once. Returns its id, or None when the database
    cannot take it (the call still runs)."""
    try:
        with db.session_scope() as session:
            row = CallLog(function=function, started_at=started_at, ms=0, ok=False, error_code=IN_PROGRESS,
                          result_bytes=0, user_id=user_id)
            session.add(row)
            session.flush()
            return row.id
    except Exception as exc:  # noqa: BLE001 - a lost row never costs the person their answer
        log.warning("The call log could not be written: %s", type(exc).__name__)
        return None


def finish(row_id: int | None, ms: int, ok: bool, error_code: str | None, result_bytes: int) -> None:
    """Record how the call ended on the row start() wrote. Nothing to do when start() wrote none."""
    if row_id is None:
        return
    code = error_code[:ERROR_CODE_MAX] if isinstance(error_code, str) else None
    try:
        with db.session_scope() as session:
            session.execute(update(CallLog).where(CallLog.id == row_id).values(
                ms=ms, ok=ok, error_code=code, result_bytes=result_bytes))
    except Exception as exc:  # noqa: BLE001 - a lost outcome never costs the person their answer
        log.warning("The call log could not be written: %s", type(exc).__name__)


def crash_text(exc: BaseException) -> str:
    """The exception's type and where it was raised, one frame a line. No message, no source text."""
    frames = traceback.extract_tb(exc.__traceback__)
    lines = [f"  {frame.filename}:{frame.lineno} in {frame.name}" for frame in frames]
    return "\n".join([type(exc).__name__, *lines])
