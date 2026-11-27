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
failed_only keeps the failed calls; in the last mode (none of them) it keeps only the lines with one. With an email
that is not one account's, every matched account keeps its line, with failed_only too: the mode lists every account
the email is, and each line's failed_calls says how many of its calls failed. A failed call is not ok and has
finished: a call still running (calllog.IN_PROGRESS) is not failed. The MCP schema holds the ranges.
function and caller narrow every mode, the lines' counts too (log-and-guide.md L4): function is a function's name,
matched exactly, and a name no row holds gives nothing, never a refusal (the web keeps writing four of the five log
functions' names after they leave the list); caller is web or claude, so a row older than the trace, with no
caller, is left out by either.
A call gives its trace: never an argument's value (app/calllog.py). call_row is that one shape of a call, and
utc its time: owner_feedback's around gives calls the same way (app/services/feedback.py).
The filters are checked first (filters, contract inputs.md decisions 9 and 10): account_id through
common.entry_id, email, request_id and function through rules.text with their column's length as the limit, caller
against models.CALLERS. owner_feedback checks its account_id and email the same way.

owner_accounts() -> {accounts, total, seen, seen_days, call_days, feedback_days}: the owner's page /owner/accounts
reads it (contract owner-accounts.md, decisions 1 and 5). One line per account, newest first by created_at: its id,
email, name, time zone, joined and last_seen, the live rows (deleted_at null, decision 6) of each of the five entry
kinds (ENTRY_COUNTS), its calls in the last CALL_LOG_DAYS and its feedback in the last FEEDBACK_DAYS (decision 7: how
long calllog keeps those rows, read from its constants). One GROUP BY user_id query a table. total is every account,
seen the accounts seen in the last SEEN_DAYS (decision 23). Each line also carries the account's status, status_reason
and status_at, and record the owner's last RECORD_ROWS actions, newest first (decision 21).

set_account_status(by_user, data) -> {changed, account_id, status}, or {error, message, fix}: the owner's suspend or
lift of one account (decisions 9 to 12), from the owner's form on that page. It is not a function and does not go
through the web's write(): no call-log row, no write key, no write limit; its one trace is the owner_action row
(decision 22). The caller has checked that by_user is the owner.
"""

from datetime import timedelta

from sqlalchemy import and_, case, func, null, select

from app import calllog, db, rules
from app.config import get_settings
from app.models import (
    CALLERS, OWNER_ACTIONS, STATUSES, Bodyweight, CallLog, CardioEntry, Feedback, FoodEntry, LiftSession, Measurement,
    OwnerAction, User,
)
from app.services.common import entry_id, lock_user

HOURS = (1, 2160, 24)  # lowest, highest, when left out
LIMIT = (1, 200, 50)
# The one test of a failed call: not ok, and not still running.
FAILED = CallLog.ok.is_(False) & CallLog.error_code.is_distinct_from(calllog.IN_PROGRESS)
# The longest each text filter can be: its column's own length.
TEXT_FILTERS = {"email": User.__table__.c.email.type.length, "request_id": CallLog.__table__.c.request_id.type.length,
                "function": CallLog.__table__.c.function.type.length}
# owner_accounts' entry counts: (a line's field, the entry model). The five models of models.entry_models().
ENTRY_COUNTS = (("lift_sessions", LiftSession), ("bodyweights", Bodyweight), ("measurements", Measurement),
                ("food_entries", FoodEntry), ("cardio_entries", CardioEntry))
SEEN_DAYS = 7  # "seen in the last 7 days": a pick, a week (decision 23), not a number from data
RECORD_ROWS = 20  # the record rows the page shows: a page size (decision 21), not a number from data
REASON_MAX = OwnerAction.__table__.c.reason.type.length  # 200 (decision 9), the column's own length
ACTIVE, SUSPENDED = STATUSES
SUSPEND, LIFT = OWNER_ACTIONS


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


def owner_accounts() -> dict:
    now = db.utcnow()
    call_since = now - timedelta(days=calllog.CALL_LOG_DAYS)
    feedback_since = now - timedelta(days=calllog.FEEDBACK_DAYS)
    with db.new_session() as s:
        people = list(s.scalars(select(User).order_by(User.created_at.desc(), User.id.desc())))
        counts = {field: _per_account(s, model.user_id, model.deleted_at.is_(None)) for field, model in ENTRY_COUNTS}
        counts["calls"] = _per_account(s, CallLog.user_id, CallLog.started_at >= call_since)
        counts["feedback"] = _per_account(s, Feedback.user_id, Feedback.created_at >= feedback_since)
        record = [{"at": utc(a.created_at), "action": a.action, "account_id": a.account_id,
                   "by_account_id": a.by_account_id, "reason": a.reason}
                  for a in s.scalars(select(OwnerAction).order_by(OwnerAction.created_at.desc(), OwnerAction.id.desc())
                                     .limit(RECORD_ROWS))]
    seen_since = now - timedelta(days=SEEN_DAYS)
    lines = [{"account_id": u.id, "email": u.email, "name": u.name, "time_zone": u.time_zone,
              "joined": utc(u.created_at), "last_seen": utc(u.last_seen_at),
              **{field: by_account.get(u.id, 0) for field, by_account in counts.items()},
              "status": u.status, "status_reason": u.status_reason, "status_at": utc(u.status_at)} for u in people]
    return {"accounts": lines, "total": len(people), "record": record,
            "seen": sum(1 for u in people if u.last_seen_at is not None and u.last_seen_at >= seen_since),
            "seen_days": SEEN_DAYS, "call_days": calllog.CALL_LOG_DAYS, "feedback_days": calllog.FEEDBACK_DAYS}


def _per_account(s, user_id, *where) -> dict[int, int]:
    """{account id: rows} of one table, in one GROUP BY user_id query. A row with no account is left out."""
    rows = select(user_id, func.count()).where(user_id.is_not(None), *where).group_by(user_id)
    return dict(s.execute(rows).all())


def set_account_status(by_user: User, data: dict) -> dict:
    """The owner's suspend or lift of one account (decisions 9 to 12): data holds account_id, action (suspend or lift)
    and reason. A suspend needs a reason of 1 to REASON_MAX characters after trimming; a lift takes one or none, for
    the record. The owner's own account is refused. Suspending a suspended account or lifting an active one changes
    nothing and writes no record row: changed is false. Otherwise the user row (locked first, as every write locks it)
    and one owner_action row change in one transaction."""
    errors = []
    account_id = entry_id(data.get("account_id"))
    if account_id is None:
        errors.append(rules.error("invalid_field", f"account_id {rules.as_sent(data.get('account_id'))} is not an "
                                  "account id.", "Give an account's id as a line gives it: a whole number from 1."))
    action = data.get("action")
    if action not in OWNER_ACTIONS:
        errors.append(rules.error("invalid_field", f"action '{rules.quote(action)}' is not one of "
                                  f"{', '.join(OWNER_ACTIONS)}.", f"Use one of: {', '.join(OWNER_ACTIONS)}."))
    reason, err = rules.text(data.get("reason"), "reason", REASON_MAX)
    errors += [{**err, "field": "reason"}] if err else []  # field: the page names the reason's own refusal
    if err is None and action == SUSPEND and not reason:
        errors.append(rules.error("missing_field", "A suspend needs a reason.", "Give the reason, in one line.",
                                  field="reason"))
    if errors:
        return rules.combine(errors)
    now = db.utcnow()
    with db.session_scope() as s:
        target = lock_user(s, account_id)
        if target is None:
            return rules.error("not_found", f"No account has the id {account_id}.", "Give an id the page lists.")
        if target.subject == get_settings().owner_user_id:
            return rules.error("owner_account", "The owner's own account cannot be suspended.", "Nothing to change.")
        status = SUSPENDED if action == SUSPEND else ACTIVE
        if target.status == status:
            return {"changed": False, "account_id": account_id, "status": status}
        target.status, target.status_reason, target.status_at = status, reason if action == SUSPEND else None, now
        s.add(OwnerAction(by_account_id=by_user.id, action=action, account_id=account_id, reason=reason or None,
                          created_at=now))
    return {"changed": True, "account_id": account_id, "status": status}
