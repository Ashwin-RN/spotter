"""The account, its settings, and the shared exercise list.

whoami(user_id, offered=())
    The signed-in person's account: account_id (the row id), user_id (the sign-in provider's subject,
    the same value the token's sub and the Settings page show), email, name, time zone, today's date
    for them, their targets, what Claude may do, and links, links.hi_there always. Until a time zone is
    set, today is the UTC date and time_zone_note tells Claude to set it with update_settings. While the
    account is empty, by account_empty's rule, new_account_note tells Claude to offer setup once and to
    give the person links.hi_there once. While a setup step other than import is missing, setup carries
    the missing steps in SETUP_STEPS' order and SETUP_HOW (contract first-day.md). offered: the function
    names on offer (the server passes mcp_server.NEEDS); a step that needs a function (SETUP_NEEDS) is
    on offer only while it is among them (setup_steps). A read: no lock, no write limit, no write, so
    nothing records that the note was given or a step skipped.

update_settings(user_id, data, by)
    Change the person's own settings (SETTABLE): time_zone; calorie_target_kcal (800 to 10,000),
    target_sessions_per_week (0 to 14) and target_cardio_minutes_per_week (0 to 3,000), whole numbers,
    null to clear; add_back_exercise_kcal, true or false. The summary names each change.
    claude_can, what Claude may do, is never set here, whoever
    calls: only the person changes it, on the Settings page, through set_claude_can.

set_claude_can(user_id, value)
    The Settings page's switch: log_delete (a new account's) or log, models.CLAUDE_CAN. Any other value,
    read among them, is refused. Nothing else calls it.

    time_zone must be a real IANA zone name, such as Asia/Kolkata, or UTC. The form is forgiven
    (surrounding spaces, letter case, spaces for underscores); the meaning is not. An abbreviation
    (EST, CET), a UTC offset (+05:30, GMT+5) or a bare city or country is refused, because none of
    them says when the clocks change. A bare city that is the last part of exactly one zone name
    (Kolkata, London) is named as the closest zone in the error; nothing is guessed silently.

    Changing the zone changes no stored entry: entries keep the dates they were logged with.

account_empty(user_id)
    Today's read for a new account: empty is true when the person has no live entry in the five entry
    tables, no live saved food and no open goal. Deleted rows and closed goals (hit or dropped) do not
    count. A read: no lock, no write limit. The page then shows what to do first. whoami asks the same
    seven questions (_questions, through _has), so the two always agree: account_empty stops at the first
    found, and whoami asks all seven once (_held), since its setup steps read the same answers.

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
    CLAUDE_CAN, EXERCISE_KINDS, IMPORTED, Bodyweight, CallLog, CardioEntry, FoodEntry, Goal, LiftSession, LiftSet,
    Measurement, SavedFood, User, entry_models,
)
from app.rules import error, quote
from app.services.common import check_by, check_data, check_write_limit, exercises, lock_user, no_account, truthy

TIME_ZONE_NOTE = ("No time zone is set, so today is the UTC date. Set it now with update_settings: use the "
                  "person's time zone if you know it, otherwise ask once which city they are in. Set it "
                  "yourself; the person has nothing to do.")
NEW_ACCOUNT_NOTE = ("Nothing is logged on this account yet. Offer once to set Spotter up together, as setup.how says. "
                    "Give the person links.hi_there once, last in your reply: it is a short read on how to set up, "
                    "what to say and what Spotter works out. It has nothing to fill in.")
# The setup steps, in the order the assistant asks for them and the hi there page lists them (contract first-day.md).
SETUP_STEPS = ("time_zone", "weigh_in", "lift_session", "goal", "calorie_target", "sessions_target", "cardio_target",
               "saved_food", "routine", "import")
# A step that needs a function is on offer only while that function is (setup_steps).
SETUP_NEEDS = {"routine": "save_routine"}
SETUP_HOW = ("Bring setup up only when the person asks, or when new_account_note says to offer it. Ask for the first "
             "step in missing, one question at a time, and save each answer with its function as soon as it is given. "
             "The person may skip any step: go to the next. Ask for nothing that is not in missing.")
TIME_ZONE_MAX = 64  # the column's length
TIME_ZONE_FIX = ("Use an IANA name such as Asia/Kolkata, worked out from the city the person is in. An "
                 "abbreviation or an offset is not enough, because it does not say when the clocks change.")
NEVER_SETTABLE = {"claude_can": "what Claude may do"}


def setup_steps(offered=()) -> tuple[str, ...]:
    """The setup steps on offer, in order: each of SETUP_STEPS whose function, if it needs one, is in offered."""
    return tuple(step for step in SETUP_STEPS if step not in SETUP_NEEDS or SETUP_NEEDS[step] in offered)


def whoami(user_id: int, offered=()) -> dict:
    """Returns {account_id, user_id, email, name, time_zone, today, targets, claude_can, links[,
    time_zone_note][, new_account_note][, setup]} or {error, message, fix}. See the module notes."""
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
            "links": {"settings": links.page("settings"), "home": links.page("home"),
                      "hi_there": links.page("hi_there")},
        }
        zoned = isinstance(users.zone_for(user.time_zone), ZoneInfo)
        if not zoned:
            out["time_zone_note"] = TIME_ZONE_NOTE
        held = _held(s, user)
        if not any(held.values()):
            out["new_account_note"] = NEW_ACCOUNT_NOTE
        done = {"time_zone": zoned, "weigh_in": held["bodyweight"], "lift_session": held["lift_session"],
                "goal": held["goal"], "calorie_target": user.calorie_target_kcal is not None,
                "sessions_target": user.target_sessions_per_week is not None,
                "cardio_target": user.target_cardio_minutes_per_week is not None,
                "saved_food": held["saved_food"], "routine": False}  # routine: nothing can hold one yet
        missing = [step for step in setup_steps(offered) if step != "import" and not done[step]]
        if missing:  # import alone never keeps setup alive, so it is asked only now
            if "import" in setup_steps(offered) and not _imported(s, user.id, held):
                missing.append("import")
            out["setup"] = {"missing": missing, "how": SETUP_HOW}
        return out


def _questions(user_id: int) -> dict:
    """The seven questions "empty" asks, by table: a live row of each entry table, a live saved food, an open goal."""
    asks = {m.__tablename__: select(m.id).where(m.user_id == user_id, m.deleted_at.is_(None))
            for m in (*entry_models(), SavedFood)}
    asks["goal"] = select(Goal.id).where(Goal.user_id == user_id, Goal.status == "open")
    return asks


def _has(s, ask) -> bool:
    """One question, one small read."""
    return s.scalar(ask.limit(1)) is not None


def _held(s, user: User) -> dict[str, bool]:
    """Each of the seven questions answered, every one whatever the others answer."""
    return {name: _has(s, ask) for name, ask in _questions(user.id).items()}


def _imported(s, user_id: int, held: dict[str, bool]) -> bool:
    """A live imported row, asked only of the entry tables that hold a live row, stopping at the first found."""
    return any(_has(s, select(m.id).where(m.user_id == user_id, m.deleted_at.is_(None), m.logged_by == IMPORTED))
               for m in entry_models() if held[m.__tablename__])


def _empty(s, user_id: int) -> bool:
    """True when the account has no live entry in the five entry tables, no live saved food and no open goal."""
    return not any(_has(s, ask) for ask in _questions(user_id).values())


def account_empty(user_id: int) -> dict:
    """Returns {empty} or {error, message, fix}. See the module notes."""
    with db.new_session() as s:
        if s.get(User, user_id) is None:
            return no_account()
        return {"empty": _empty(s, user_id)}


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
    """What Claude may do: log_delete or log, nothing else. The Settings page calls this, and nothing else
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
