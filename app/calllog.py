"""The call log: one row per MCP function call, and a safe way to describe a crash.

A row holds the function's name, when it started, how long it took, ok or an error code, the size of
the result and whose account it was. Never an argument and never a result: those are the person's data.

write() is synchronous and uses the database, so callers run it in a worker thread.

crash_text(exc) describes an exception by its type and its stack frames only. It never includes the
exception's message: a database error's message can carry the statement's values, which are the
person's data.
"""

import traceback
from datetime import datetime

from app import db
from app.models import CallLog

ERROR_CODE_MAX = 32  # the error_code column's length


def write(function: str, started_at: datetime, ms: int, ok: bool, error_code: str | None, result_bytes: int,
          user_id: int | None) -> None:
    """Store one call-log row."""
    code = error_code[:ERROR_CODE_MAX] if isinstance(error_code, str) else None
    with db.session_scope() as session:
        session.add(CallLog(function=function, started_at=started_at, ms=ms, ok=ok, error_code=code,
                            result_bytes=result_bytes, user_id=user_id))


def crash_text(exc: BaseException) -> str:
    """The exception's type and where it was raised, one frame a line. No message, no source text."""
    frames = traceback.extract_tb(exc.__traceback__)
    lines = [f"  {frame.filename}:{frame.lineno} in {frame.name}" for frame in frames]
    return "\n".join([type(exc).__name__, *lines])
