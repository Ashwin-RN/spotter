"""The account, its settings, and the shared exercise list.

whoami(user_id)
    The signed-in person's account: account_id (the row id), user_id (the sign-in provider's subject,
    the same value the token's sub and the Settings page show), email, name, time zone, today's date
    for them, their targets, what Claude may do, and links. Until a time zone is set, today is the UTC
    date and time_zone_note tells Claude to set it with update_settings.

update_settings(user_id, data, by)
    Change the person's own settings (SETTABLE): time_zone; calorie_target_kcal (800 to 10,000),
    target_sessions_per_week (0 to 14) and target_cardio_minutes_per_week (0 to 3,000), whole numbers,
    null to clear; add_back_exercise_kcal, true or false. The summary names each change.
    claude_can, what Claude may do, is never set here, whoever
    calls: only the person changes it, on the Settings page, through set_claude_can.

set_claude_can(user_id, value)
    The Settings page's switch: read, log or log_delete. Nothing else calls it.

    time_zone must be a real IANA zone name, such as Asia/Kolkata, or UTC. The form is forgiven
    (surrounding spaces, letter case, spaces for underscores); the meaning is not. An abbreviation
    (EST, CET), a UTC offset (+05:30, GMT+5) or a bare city or country is refused, because none of
    them says when the clocks change. A bare city that is the last part of exactly one zone name
    (Kolkata, London) is named as the closest zone in the error; nothing is guessed silently.

    Changing the zone changes no stored entry: entries keep the dates they were logged with.

list_exercises(kind=None, category=None)
    The names entries must use, filtered by kind (lift, cardio) and category.

delete_account(user_id)
    The Settings page's delete, after the web has deleted the sign-in at the provider (decision 11).
    Local only. The person's call-log rows stay, for the counts, with user_id set to null first; then
    the user row goes, and the schema's ON DELETE CASCADE takes every row they own with it. Its own
    call-log row is one of those kept, so calllog.finish still records how it ended.
    Exempt from the write limit, as set_claude_can is: the web deletes the sign-in at the provider
    first, so a refusal here would leave the person half gone. The person can always leave.

Every write here locks the user row first (common.lock_user), then checks the write limit
(common.check_write_limit: 120 writes in 10 minutes), before any other read or work. The two
exceptions, set_claude_can and delete_account, lock the row and skip the limit.
"""

from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update

from app import db, exercises as exercise_list, formatting, links, rules, users
from app.models import (
    CLAUDE_CAN, EXERCISE_KINDS, Bodyweight, CallLog, CardioEntry, FoodEntry, Goal, LiftSession, LiftSet, Measurement,
    SavedFood, User,
)
from app.rules import error, quote
from app.services.common import check_by, check_data, check_write_limit, exercises, lock_user, no_account, truthy

TIME_ZONE_NOTE = ("No time zone is set, so today is the UTC date. Set it now with update_settings: use the "
                  "person's time zone if you know it, otherwise ask once which city they are in. Set it "
                  "yourself; the person has nothing to do.")
TIME_ZONE_MAX = 64  # the column's length
TIME_ZONE_FIX = ("Use an IANA name such as Asia/Kolkata, worked out from the city the person is in. An "
                 "abbreviation or an offset is not enough, because it does not say when the clocks change.")
NEVER_SETTABLE = {"claude_can": "what Claude may do"}


def whoami(user_id: int) -> dict:
    """Returns {account_id, user_id, email, name, time_zone, today, targets, claude_can, links[,
    time_zone_note]} or {error, message, fix}."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        out = {
            "account_id": user.id,
            "user_id": user.subject,
            "email": user.email,
            "name": user.name,
            "time_zone": user.time_zone,
            "today": users.today_for(user).isoformat(),
            "targets": {
                "calorie_target_kcal": user.calorie_target_kcal,
                "target_sessions_per_week": user.target_sessions_per_week,
                "target_cardio_minutes_per_week": user.target_cardio_minutes_per_week,
            },
            "claude_can": user.claude_can,
            "links": {"settings": links.page("settings"), "home": links.page("home")},
        }
        if not isinstance(users.zone_for(user.time_zone), ZoneInfo):
            out["time_zone_note"] = TIME_ZONE_NOTE
        return out


# ---------------------------------------------------------------- settings

def _time_zone(value) -> tuple[str | None, dict | None]:
    """The stored zone name for what the caller sent, or an error. users.canonical_zone is the one
    check, shared with the Settings page and the browser's own zone."""
    text, err = rules.text(value, "time_zone", TIME_ZONE_MAX)
    if err:
        return None, err
    name = users.canonical_zone(text) if text else None
    if name is not None:
        return name, None
    candidates = users.closest_zones(text)
    closest = candidates if len(candidates) == 1 else []
    hint = f" Closest zone: {closest[0]}." if closest else ""
    shown = f"'{quote(text)}' is" if text else "An empty value is"
    return None, error("unknown_time_zone", f"{shown} not a time zone name.", TIME_ZONE_FIX + hint,
                       closest=closest)


def _target(field: str):
    """A target in its range (rules.NUMBERS), or null to clear it: no target, no comparison."""
    def check(value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None, None
        return rules.bounded(field, value)
    return check


ON, OFF = ("1", "true", "yes", "on"), ("0", "false", "no", "off", "")


def _switch(value) -> tuple[bool | None, dict | None]:
    """add_back_exercise_kcal: a bool, or the web's form values ("on", "1"; "off", "0", "")."""
    if isinstance(value, bool):
        return value, None
    if isinstance(value, str) and value.strip().lower() in ON + OFF:
        return truthy(value), None
    return None, error("invalid_field", f"add_back_exercise_kcal '{quote(value)}' is not true or false.",
                       "Give add_back_exercise_kcal as true or false.")


# What update_settings can set: field -> its check, which returns (stored value, error).
SETTABLE = {
    "time_zone": _time_zone,
    "calorie_target_kcal": _target("calorie_target_kcal"),
    "target_sessions_per_week": _target("target_sessions_per_week"),
    "target_cardio_minutes_per_week": _target("target_cardio_minutes_per_week"),
    "add_back_exercise_kcal": _switch,
}
SAID = {
    "time_zone": lambda v: f"Time zone set to {v}",
    "calorie_target_kcal": lambda v: (f"Calorie target set to {formatting.show(v, 'kcal')} a day" if v is not None
                                      else "Calorie target cleared"),
    "target_sessions_per_week": lambda v: (f"Weekly sessions target set to {formatting.show(v, 'count')}"
                                           if v is not None else "Weekly sessions target cleared"),
    "target_cardio_minutes_per_week": lambda v: (
        f"Weekly cardio target set to {formatting.show(v, 'week_minutes', unit=False)} minutes" if v is not None
        else "Weekly cardio target cleared"),
    "add_back_exercise_kcal": lambda v: ("Exercise calories are now added back" if v
                                         else "Exercise calories are no longer added back"),
}


def update_settings(user_id: int, data: dict | None, by: str) -> dict:
    """Returns {time_zone, today, changed, summary, links} or {error, message, fix}.

    changed: the fields this call changed; [] when every value given was already stored.
    Empty or missing data is nothing_to_change. Every refusal comes back at once; on any refusal
    nothing is stored."""
    data, err = check_data(data)
    err = err or check_by(by)
    if err:
        return err
    if not data:
        return error("nothing_to_change", "No setting was given.",
                     f"Give one of: {', '.join(SETTABLE)}, such as time_zone: Asia/Kolkata.")
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        limited = check_write_limit(s, user.id)
        if limited:
            return limited
        errors, values = [], {}
        for field, value in data.items():
            if field in NEVER_SETTABLE:
                errors.append(error("not_allowed", f"{field} ({NEVER_SETTABLE[field]}) cannot be changed here.",
                                    "Only the person can change what Claude may do, on the Settings page."))
            elif field in SETTABLE:
                values[field], err = SETTABLE[field](value)
                errors += [err] if err else []
            else:
                errors.append(error("invalid_field", f"'{quote(field)}' is not a setting that can be changed.",
                                    f"Settings that can be changed: {', '.join(SETTABLE)}."))
        if errors:
            return rules.combine(errors)
        changed = [field for field, value in values.items() if getattr(user, field) != value]
        for field in changed:
            setattr(user, field, values[field])
        if changed:
            user.updated_at = db.utcnow()
        s.flush()
        today = users.today_for(user)
        said = f"Today is {formatting.day(today, today)}."
        if changed:
            summary = ". ".join(SAID[field](values[field]) for field in changed) + f". {said}"
        else:
            summary = f"Nothing changed: every value given is already set. {said}"
        return {"time_zone": user.time_zone, "today": today.isoformat(), "changed": changed, "summary": summary,
                "links": {"settings": links.page("settings")}}


def set_claude_can(user_id: int, value) -> dict:
    """What Claude may do: read, log or log_delete. The Settings page calls this, and nothing else
    does: update_settings refuses the field, and no MCP function reaches it.

    It is the person's own switch, on the web only, and it is exempt from the write limit on purpose:
    it is not in common.WRITE_FUNCTIONS, so it neither counts nor is refused. The person must always be
    able to turn Claude down, however many writes have just been made.

    Returns {claude_can, changed} or {error, message, fix}. A write: it locks the user row first."""
    if not (isinstance(value, str) and value in CLAUDE_CAN):
        return error("invalid_field", f"claude_can '{quote(value)}' is not one of {', '.join(CLAUDE_CAN)}.",
                     f"Use one of: {', '.join(CLAUDE_CAN)}.")
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        changed = user.claude_can != value
        if changed:
            user.claude_can = value
            user.updated_at = db.utcnow()
        return {"claude_can": user.claude_can, "changed": changed}


# ---------------------------------------------------------------- deleting the account

OWNED = (LiftSession, Bodyweight, Measurement, Goal, SavedFood, FoodEntry, CardioEntry)


def delete_account(user_id: int) -> dict:
    """Delete this person's account here and every row they own. The provider's sign-in is the web's
    to delete, first. Returns {deleted: true, rows: {table: rows deleted}} (user, every owned table, and
    lift_set through its sessions) or {error, message, fix}. A write: lock_user first; never the write
    limit (see the module notes)."""
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        rows = {"user": 1}
        for model in OWNED:
            rows[model.__tablename__] = s.scalar(select(func.count()).select_from(model)
                                                 .where(model.user_id == user.id))
        rows["lift_set"] = s.scalar(select(func.count()).select_from(LiftSet).join(LiftSession)
                                    .where(LiftSession.user_id == user.id))
        # The call log keeps its rows, with no owner: they hold no content, only that calls happened.
        s.execute(update(CallLog).where(CallLog.user_id == user.id).values(user_id=None)
                  .execution_options(synchronize_session=False))
        s.expunge(user)
        s.execute(delete(User).where(User.id == user_id).execution_options(synchronize_session=False))
        return {"deleted": True, "rows": rows}


# ---------------------------------------------------------------- exercises

def _categories() -> list[str]:
    return list(dict.fromkeys(exercise_list.CATEGORIES + [row["category"] for row in exercise_list.rows()]))


def list_exercises(kind: str | None = None, category: str | None = None) -> dict:
    """Returns {exercises [{name, kind, category, met, compendium_code}]} or {error, message, fix}.
    met and compendium_code are the 2024 Adult Compendium's for a cardio activity, null for a lift."""
    if kind not in (None, "") and not (isinstance(kind, str) and kind in EXERCISE_KINDS):
        return error("invalid_field", f"kind '{quote(kind)}' is not one of {', '.join(EXERCISE_KINDS)}.",
                     f"Use one of: {', '.join(EXERCISE_KINDS)}, or leave it out for all.")
    categories = _categories()
    if category not in (None, "") and not (isinstance(category, str) and category in categories):
        return error("invalid_field", f"category '{quote(category)}' is not one of {', '.join(categories)}.",
                     f"Use one of: {', '.join(categories)}, or leave it out for all.")
    with db.new_session() as s:
        rows = [{"name": e.name, "kind": e.kind, "category": e.category, "met": e.met,
                 "compendium_code": e.compendium_code}
                for e in exercises(s, kind or None) if not category or e.category == category]
    return {"exercises": rows}
