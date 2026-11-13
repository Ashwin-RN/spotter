"""owner_calls: the owner's read of the call log. mcp_server offers it to the owner alone (OWNER_USER_ID).

owner_calls(data) -> {hours, accounts, calls}, in this order of precedence:
    request_id               that one request's calls, whatever their age: hours is null
    no_account               the calls in the window that had no account (a refused sign-in, or a deleted
                             account), newest first
    account_id, or email     that person's calls in the window, newest first. The email is matched exactly,
                             ignoring case: when it is not one account's, accounts lists every account it
                             is (none, or several) and calls is null
    none of them             one line per account with a call in the window, and one line with no account
                             for the calls that had none, the latest call first
hours (1 to 2,160, 24 when left out) is the window, limit (1 to 200, 50 when left out) the most lines or calls,
failed_only keeps the failed calls (and the lines with one). A failed call is not ok and has finished: a call
still running (calllog.IN_PROGRESS) is not failed. The MCP schema holds the ranges.
A call gives its trace: never an argument's value (app/calllog.py).
"""

from datetime import timedelta

from sqlalchemy import case, func, null, select

from app import calllog, db
from app.models import CallLog, User

HOURS = (1, 2160, 24)  # lowest, highest, when left out
LIMIT = (1, 200, 50)
# The one test of a failed call: not ok, and not still running.
FAILED = CallLog.ok.is_(False) & CallLog.error_code.is_distinct_from(calllog.IN_PROGRESS)


def owner_calls(data: dict) -> dict:
    hours, limit = data.get("hours") or HOURS[2], data.get("limit") or LIMIT[2]
    failed_only = bool(data.get("failed_only"))
    since = db.utcnow() - timedelta(hours=hours)
    with db.new_session() as s:
        calls = select(CallLog).order_by(CallLog.started_at.desc(), CallLog.id.desc()).limit(limit)
        if failed_only:
            calls = calls.where(FAILED)
        if data.get("request_id"):
            return _answer(None, None, s.scalars(calls.where(CallLog.request_id == data["request_id"])))
        in_window = calls.where(CallLog.started_at >= since)
        if data.get("no_account"):
            return _answer(hours, None, s.scalars(in_window.where(CallLog.user_id.is_(None))))
        account_id = data.get("account_id")
        if account_id is None and data.get("email"):
            ids = list(s.scalars(select(User.id).where(func.lower(User.email) == data["email"].lower())))
            if len(ids) != 1:
                return _answer(hours, _lines(s, since, failed_only, limit, ids), None)
            account_id = ids[0]
        if account_id is None:
            return _answer(hours, _lines(s, since, failed_only, limit), None)
        return _answer(hours, None, s.scalars(in_window.where(CallLog.user_id == account_id)))


def _lines(s, since, failed_only: bool, limit: int, ids: list[int] | None = None) -> list[dict]:
    """One line per account with a call in the window, and one for the calls with no account when there are any;
    with ids, those accounts, calls or none."""
    failed = func.coalesce(func.sum(case((FAILED, 1), else_=0)), 0)
    last = func.max(CallLog.started_at)
    lines = (select(User.id, User.email, User.name, func.count(CallLog.id), failed, last)
             .join(CallLog, (CallLog.user_id == User.id) & (CallLog.started_at >= since), isouter=ids is not None)
             .group_by(User.id, User.email, User.name).order_by(last.desc().nulls_last(), User.id).limit(limit))
    nobody = select(null(), null(), null(), func.count(CallLog.id), failed, last).where(
        CallLog.user_id.is_(None), CallLog.started_at >= since).having(func.count(CallLog.id) > 0)
    if ids is not None:
        lines, nobody = lines.where(User.id.in_(ids)), None
    elif failed_only:
        lines, nobody = lines.having(failed > 0), nobody.having(failed > 0)
    found = [*s.execute(lines), *(s.execute(nobody) if nobody is not None else [])]
    found.sort(key=lambda line: (line[5] is None, -(line[5].timestamp() if line[5] else 0)))
    return [{"account_id": i, "email": email, "name": name, "calls": n, "failed_calls": bad,
             "last_call": _utc(at)} for i, email, name, n, bad, at in found[:limit]]


def _answer(hours: int | None, accounts: list | None, calls) -> dict:
    return {"hours": hours, "accounts": accounts, "calls": None if calls is None else [_call(c) for c in calls]}


def _call(c: CallLog) -> dict:
    return {"started_at": _utc(c.started_at), "caller": c.caller, "function": c.function, "ok": c.ok,
            "error": c.error_code, "outcome": c.outcome, "step": c.step, "frame": c.frame, "tried": c.tried,
            "notes": c.notes, "ms": c.ms, "result_bytes": c.result_bytes, "request_id": c.request_id}


def _utc(at) -> str | None:
    return None if at is None else at.strftime("%Y-%m-%dT%H:%M:%SZ")
