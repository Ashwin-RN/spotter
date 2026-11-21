"""owner_calls: the owner's read of the call log. mcp_server offers it to the owner alone (OWNER_USER_ID).

owner_calls(data) -> {hours, accounts, calls}, in this order of precedence:
    request_id               that one request's calls, whatever their age: hours is null
    no_account               the calls in the window that had no account (a refused sign-in, or a deleted
                             account), newest first
    account_id, or email     that person's calls in the window, newest first. The email is matched exactly,
                             ignoring case: when it is not one account's, accounts lists every account it
                             is (none, or several) and calls is null
    all_accounts             every call in the window, of every account and of none, newest first: each call
                             carries its account_id, null for a call that had no account (log-and-guide.md L4)
    none of them             one line per account with a call in the window, and one line with no account
                             for the calls that had none, the latest call first
hours (1 to 2,160, 24 when left out) is the window, limit (1 to 200, 50 when left out) the most lines or calls,
failed_only keeps the failed calls (and the lines with one). A failed call is not ok and has finished: a call
still running (calllog.IN_PROGRESS) is not failed. The MCP schema holds the ranges.
function and caller narrow every mode, the lines' counts too (log-and-guide.md L4): function is a function's name,
matched exactly, and a name no row holds gives nothing, never a refusal (the web keeps writing four of the five log
functions' names after they leave the list); caller is web or claude, so a row older than the trace, with no
caller, is left out by either.
A call gives its trace: never an argument's value (app/calllog.py). call_row is that one shape of a call, and
utc its time: owner_feedback's around gives calls the same way (app/services/feedback.py).
The filters are checked first (filters, contract inputs.md decisions 9 and 10): account_id through
common.entry_id, email, request_id and function through rules.text with their column's length as the limit, caller
against models.CALLERS. owner_feedback checks its account_id and email the same way.
"""

from datetime import timedelta

from sqlalchemy import and_, case, func, null, select

from app import calllog, db, rules
from app.models import CALLERS, CallLog, User
from app.services.common import entry_id

HOURS = (1, 2160, 24)  # lowest, highest, when left out
LIMIT = (1, 200, 50)
# The one test of a failed call: not ok, and not still running.
FAILED = CallLog.ok.is_(False) & CallLog.error_code.is_distinct_from(calllog.IN_PROGRESS)
# The longest each text filter can be: its column's own length.
TEXT_FILTERS = {"email": User.__table__.c.email.type.length, "request_id": CallLog.__table__.c.request_id.type.length,
                "function": CallLog.__table__.c.function.type.length}


def filters(data: dict, names: tuple) -> tuple[dict, dict | None]:
    """The owner's filters among `names` (account_id, email, request_id, function, caller), checked: (data with each
    kept as checked, every refusal at once or None). account_id passes through common.entry_id, a row id's one check;
    email, request_id and function through rules.text, in its own words, at most their column's length; caller is
    one of models.CALLERS, exactly."""
    out, errors = dict(data), []
    if "account_id" in names and data.get("account_id") is not None:
        out["account_id"] = entry_id(data["account_id"])
        if out["account_id"] is None:
            errors.append(rules.error("invalid_field",
                                      f"account_id {rules.as_sent(data['account_id'])} is not an account id.",
                                      "Give an account's id as a line gives it: a whole number from 1."))
    for name in ("email", "request_id", "function"):
        if name in names and data.get(name) is not None:
            out[name], err = rules.text(data[name], name, TEXT_FILTERS[name])
            errors += [err] if err else []
    if "caller" in names and data.get("caller") is not None and data["caller"] not in CALLERS:
        errors.append(rules.error("invalid_field",
                                  f"caller '{rules.quote(data['caller'])}' is not one of {', '.join(CALLERS)}.",
                                  f"Use one of: {', '.join(CALLERS)}, or leave caller out."))
    return out, rules.combine(errors)


def owner_calls(data: dict) -> dict:
    data, err = filters(data, ("account_id", "email", "request_id", "function", "caller"))
    if err:
        return err
    hours, limit = data.get("hours") or HOURS[2], data.get("limit") or LIMIT[2]
    failed_only = bool(data.get("failed_only"))
    since = db.utcnow() - timedelta(hours=hours)
    narrow = _narrow(data)
    with db.new_session() as s:
        calls = select(CallLog).order_by(CallLog.started_at.desc(), CallLog.id.desc()).where(*narrow).limit(limit)
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
                return _answer(hours, _lines(s, since, failed_only, limit, narrow, ids), None)
            account_id = ids[0]
        if account_id is None and data.get("all_accounts"):
            return _answer(hours, None, s.scalars(in_window), whose=True)
        if account_id is None:
            return _answer(hours, _lines(s, since, failed_only, limit, narrow), None)
        return _answer(hours, None, s.scalars(in_window.where(CallLog.user_id == account_id)))


def _narrow(data: dict) -> list:
    """function and caller, as conditions on a call: each one sent narrows every mode. A blank function is left out."""
    return [*([CallLog.function == data["function"]] if data.get("function") else []),
            *([CallLog.caller == data["caller"]] if data.get("caller") is not None else [])]


def _lines(s, since, failed_only: bool, limit: int, narrow: list, ids: list[int] | None = None) -> list[dict]:
    """One line per account with a call in the window, and one for the calls with no account when there are any;
    with ids, those accounts, calls or none. narrow (function, caller) sits in the join, beside the window, so with
    ids an account with no such call still has its line, at 0."""
    failed = func.coalesce(func.sum(case((FAILED, 1), else_=0)), 0)
    last = func.max(CallLog.started_at)
    joined = and_(CallLog.user_id == User.id, CallLog.started_at >= since, *narrow)
    lines = (select(User.id, User.email, User.name, func.count(CallLog.id), failed, last)
             .join(CallLog, joined, isouter=ids is not None)
             .group_by(User.id, User.email, User.name).order_by(last.desc().nulls_last(), User.id).limit(limit))
    nobody = select(null(), null(), null(), func.count(CallLog.id), failed, last).where(
        CallLog.user_id.is_(None), CallLog.started_at >= since, *narrow).having(func.count(CallLog.id) > 0)
    if ids is not None:
        lines, nobody = lines.where(User.id.in_(ids)), None
    elif failed_only:
        lines, nobody = lines.having(failed > 0), nobody.having(failed > 0)
    found = [*s.execute(lines), *(s.execute(nobody) if nobody is not None else [])]
    found.sort(key=lambda line: (line[5] is None, -(line[5].timestamp() if line[5] else 0)))
    return [{"account_id": i, "email": email, "name": name, "calls": n, "failed_calls": bad,
             "last_call": utc(at)} for i, email, name, n, bad, at in found[:limit]]


def _answer(hours: int | None, accounts: list | None, calls, whose: bool = False) -> dict:
    """whose (all_accounts): each call also carries its account_id, as a line names an account. Every other mode's
    calls are call_row's shape alone."""
    if calls is not None:
        calls = [{**call_row(c), "account_id": c.user_id} if whose else call_row(c) for c in calls]
    return {"hours": hours, "accounts": accounts, "calls": calls}


def call_row(c: CallLog) -> dict:
    return {"started_at": utc(c.started_at), "caller": c.caller, "function": c.function, "ok": c.ok,
            "error": c.error_code, "outcome": c.outcome, "step": c.step, "frame": c.frame, "tried": c.tried,
            "notes": c.notes, "ms": c.ms, "result_bytes": c.result_bytes, "request_id": c.request_id}


def utc(at) -> str | None:
    return None if at is None else at.strftime("%Y-%m-%dT%H:%M:%SZ")
