"""Routines: a workout saved by name, and the weight to log for each of its lifts (contract routines.md).

save_routine(user_id, data, by)
    data: name (1 to 64 characters), lifts [{exercise, sets (1 to 10), reps (1 to 30), weight_kg? (0 to 500, or
    null) or weight_lb? (0 to 1,102.31), never both}] (1 to 20 lifts, at most 60 sets in all). Returns {kind:
    "routine", id, created, routine {id, name, lifts, saved_on, updated_at}, summary, links {routines}[, units_note]}
    or {error, message, fix}.
    - A weight_lb is kept as weight_kg at 2 dp (contract units.md R10): the stored lifts are always {exercise, sets,
      reps, weight_kg}, in kg, so the same lifts again in kg write nothing. The summary is in the person's units.
    - Every failure at once (rules.combine), nothing written. An exercise is a name on the lift list, case and spaces
      forgiven, and the list's own name is stored. The name is checked as sent, stripped, then stored with its
      spaces folded (R19): no newline, tab or run of spaces reaches a summary.
    - The row is found by user_id and name_key (the name, spaces folded, casefolded), deleted rows included. A new
      name makes a row. A name held replaces its name as sent, spaces folded, its lifts and saved_on (the person's
      today). A deleted one comes back live and replaced, with its id. created is true only for a new name.
    - The cap: a save that would make a routine live that is not (a new name, or a deleted one brought back) while
      rules.ROUTINES_MAX are live is too_many_routines. A replace of a live one is never refused by it.
    - A live routine whose name as sent, spaces folded, and lifts as checked are what is stored writes nothing:
      updated_at and saved_on stay, and the answer says it is already saved. No version is kept: nothing undoes a
      replace.

get_routines(user_id)
    A read: no lock, no write limit. Every live routine by name_key, then id, each lift with weight_kg (always kg),
    weight_from and last_date, and the routine's words: lift_words of its lifts with the weight to log, in the
    person's units. The weight to log is the heaviest set of the lift's latest live session in the one order
    (lifts.history's last_top), unless the routine holds a weight for it and that session is dated before saved_on,
    or there is no such session: then the routine's own weight. With neither, null. history runs once a call, and
    only when a routine is there, so a read runs a fixed number of statements however many routines and sessions.

lift_words(lifts, page=False, units=None)
    The lifts in words, for a summary and the Routines page: "Bench Press 3 × 8 at 60 kg and Dip 3 × 10". A weight
    shows when it is not null, 0 kg included: null means ask, 0 means bodyweight. In the person's units.

Every statement on routine names the person's id: by user_id and name_key for a save, by user_id for the read and
the cap (live_count), by common.owned() for an id. The write locks the user row first (common.lock_user), then checks
the write limit (common.check_write_limit), before any other read or work.
"""

import json

from sqlalchemy import func, select

from app import db, formatting, links, rules, users
from app.models import Routine, User
from app.services import lifts
from app.services.common import (
    check_by, check_data, check_write_limit, exercises, lock_user, no_account, sent, with_units_note,
)

KIND = "routine"
LIFT_FIELDS = ("exercise", "sets", "reps", "weight_kg", "weight_lb")
LIFT_FIELDS_TEXT = "exercise, sets, reps, weight_kg or weight_lb"
HOW = ("weight_kg is the heaviest set of the lift's latest session, unless the routine was saved on a later day than "
       "that session, with a weight of its own. Quote these numbers; do not work out your own.")
NONE_YET = "No routines saved yet."
NONE_NOTE = "No routines saved yet. save_routine saves one."


def too_many() -> dict:
    """The cap's refusal, for a save and for a restore."""
    most = formatting.show(rules.ROUTINES_MAX, "count")
    return rules.error("too_many_routines", f"This account holds {most} routines, the most it keeps.",
                       "Delete one with delete_entry, or save under a name already used to replace that routine.")


def live_count(s, user_id: int) -> int:
    return s.scalar(select(func.count(Routine.id)).where(Routine.user_id == user_id, Routine.deleted_at.is_(None)))


def routine_dict(row: Routine) -> dict:
    return {"id": row.id, "name": row.name, "lifts": json.loads(row.lifts), "saved_on": row.saved_on.isoformat(),
            "updated_at": row.updated_at.isoformat()}


def lift_words(items: list[dict], page: bool = False, units=None) -> str:
    parts = []
    for x in items:
        words = f"{x['exercise']} {formatting.show(x['sets'], 'count')} × {formatting.show(x['reps'], 'reps')}"
        if x["weight_kg"] is not None:
            words += f" at {formatting.show(x['weight_kg'], 'weight_kg', page=page, units=units)}"
        parts.append(words)
    return lifts._joined(parts)


def _whole(value, field: str, where: str, n: int, bounds: tuple, code: str) -> tuple[int | None, dict | None]:
    """sets or reps of one lift: a whole number, then in its range."""
    kept, err = rules.number(value, int, field, where)
    if err:
        return None, err
    lo, hi = bounds
    if not lo <= kept <= hi:
        return None, rules.error(code, f"{where}: {field} {rules.as_sent(value)} is outside {lo} to {hi}.",
                                 f"Give {field} on lift {n} from {lo} to {hi}.")
    return kept, None


def _lift(raw, n: int, names: list[str]) -> tuple[dict, list[dict]]:
    """One lift, checked: {exercise, sets, reps, weight_kg}, or the errors. A missing value stays None. A weight_lb
    is kept as weight_kg (R10), and a refusal of it is said in lb."""
    raw = raw if isinstance(raw, dict) else {}
    where = f"Lift {n}"
    errors = [rules.error("invalid_field", f"{where}: '{rules.quote(k)}' is not a field of a lift.",
                          f"Use: {LIFT_FIELDS_TEXT}.") for k in raw if k not in LIFT_FIELDS]
    sent = raw.get("exercise")
    sent = sent.strip() if isinstance(sent, str) else ("" if sent is None else sent)
    name = None
    if not sent:
        errors.append(rules.error("missing_field", f"{where}: exercise is missing.",
                                  f"Give an exercise name on lift {n}."))
    else:
        name, _ = rules.match_exercise(sent, names)
        if name is None:
            errors.append(rules.unknown_exercise(sent, names, f"{where}: "))
    sets, err = _whole(raw.get("sets"), "sets", where, n, rules.ROUTINE_LIFT_SETS, "invalid_field")
    errors += [err] if err else []
    reps, err = _whole(raw.get("reps"), "reps", where, n, rules.HARD_RULES["reps"], "reps_out_of_range")
    errors += [err] if err else []
    sent_in, weight, err = rules.one_of(raw, "weight_kg", "weight_lb", where)
    if err:
        errors.append(err)
        weight = None
    elif sent_in is None:
        weight = None
    else:
        weight, err = rules.number(weight, float, "weight_kg", where)
        lo, hi = rules.HARD_RULES["weight_kg"]
        if err is None and not lo <= weight <= hi:
            err = rules.error("weight_out_of_range",
                              f"{where}: weight {rules.as_sent(raw.get('weight_kg'))} kg is outside {lo} to {hi} kg.",
                              f"Give weight_kg on lift {n} from {lo} to {hi}, or leave it out.")
        if err and sent_in == "weight_lb":
            err = rules.in_unit(err, "weight_lb", raw["weight_lb"], (lo, hi), "weight_kg", where, ", or leave it out")
        errors += [err] if err else []
    return {"exercise": name, "sets": sets, "reps": reps, "weight_kg": weight}, errors


def _checked(data: dict, names: list[str]) -> tuple[str | None, list[dict], list[dict]]:
    """(the name, the lifts, every failure), in the order of the contract's ruling R5."""
    name, err = rules.required_text(data.get("name"), "name", limit=rules.ROUTINE_NAME_MAX)
    name = " ".join(name.split()) if name else name  # R19: stored and shown with its spaces folded, after the check
    errors = [err] if err else []
    errors += [rules.error("invalid_field", f"'{rules.quote(k)}' is not a field of a routine.", "Use: name, lifts.")
               for k in data if k not in ("name", "lifts")]
    sent = data.get("lifts") if isinstance(data.get("lifts"), list) else []
    lo, hi = rules.ROUTINE_LIFTS
    if not lo <= len(sent) <= hi:
        errors.append(rules.error("invalid_field",
                                  f"A routine holds {lo} to {hi} lifts, not {formatting.show(len(sent), 'count')}.",
                                  f"Send {lo} to {hi} lifts."))
    checked = []
    for n, raw in enumerate(sent[:hi], start=1):
        lift, found = _lift(raw, n, names)
        checked.append(lift)
        errors += found
    most = rules.SESSION_SETS[1]
    total = sum(x["sets"] or 0 for x in checked)
    if checked and all(x["sets"] is not None for x in checked) and total > most:
        errors.append(rules.error(
            "sets_count_out_of_range",
            f"{name or 'This routine'} would hold {formatting.show(total, 'count')} sets. A routine holds at most "
            f"{formatting.show(most, 'count')}, so that it fits in one session.",
            "Send fewer sets, or split it into two routines."))
    return name, checked, errors


def save_routine(user_id: int, data: dict | None, by: str) -> dict:
    """Save a workout by name, or replace the one of that name. See the module notes."""
    data, err = check_data(data)
    err = err or check_by(by)
    if err:
        return err
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        limited = check_write_limit(s, user.id)
        if limited:
            return limited
        units = users.units_for(user)
        name, checked, errors = _checked(data, [e.name for e in exercises(s, "lift")])
        if errors:
            return rules.combine(errors)
        name_key = " ".join(name.split()).casefold()
        row = s.scalar(select(Routine).where(Routine.user_id == user.id, Routine.name_key == name_key))
        created = row is None
        was_deleted = not created and row.deleted_at is not None
        if (created or was_deleted) and live_count(s, user.id) >= rules.ROUTINES_MAX:
            return too_many()
        if not (created or was_deleted) and row.name == name and json.loads(row.lifts) == checked:
            summary = f"{name} is already saved as given: nothing changed."
        else:
            if created:
                row = Routine(user_id=user.id, name_key=name_key)
                s.add(row)
            row.name, row.lifts = name, json.dumps(checked, separators=(",", ":"))
            row.saved_on, row.deleted_at, row.updated_at = users.today_for(user), None, db.utcnow()
            s.flush()
            verb = "Saved" if created else ("Brought back and replaced" if was_deleted else "Replaced")
            summary = f"{verb} {name}: {lift_words(checked, units=units)}."
        out = {"kind": KIND, "id": row.id, "created": created, "routine": routine_dict(row), "summary": summary,
               "links": {"routines": links.page("routines")}}
        lifts_sent = data.get("lifts") if isinstance(data.get("lifts"), list) else []
        return with_units_note(out, user, any(isinstance(x, dict) and sent(x.get("weight_lb")) for x in lifts_sent),
                               False)


def get_routines(user_id: int) -> dict:
    """Every live routine, each lift with the weight to log. Returns {routines, how, summary[, note], links} or
    {error, message, fix}. See the module notes."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        rows = list(s.scalars(select(Routine).where(Routine.user_id == user.id, Routine.deleted_at.is_(None))
                              .order_by(Routine.name_key, Routine.id)))
        last_top = lifts.history(s, user.id, users.today_for(user))["last_top"] if rows else {}
        units = users.units_for(user)
    routines = []
    for row in rows:
        items = []
        for x in json.loads(row.lifts):
            top = last_top.get(x["exercise"])
            if top is not None and (x["weight_kg"] is None or top["date"] >= row.saved_on):
                weight, source = top["weight_kg"], "last_session"
            elif x["weight_kg"] is not None:
                weight, source = x["weight_kg"], "routine"
            else:
                weight, source = None, None
            items.append({**x, "weight_kg": weight, "weight_from": source,
                          "last_date": top["date"].isoformat() if top else None})
        routines.append({"id": row.id, "name": row.name, "saved_on": row.saved_on.isoformat(), "lifts": items,
                         "words": lift_words(items, units=units)})
    n = len(routines)
    names = lifts._joined([r["name"] for r in routines])
    out = {"routines": routines, "how": HOW,
           "summary": f"{formatting.show(n, 'count')} routine{'' if n == 1 else 's'}: {names}." if n else NONE_YET}
    if not n:
        out["note"] = NONE_NOTE
    return {**out, "links": {"routines": links.page("routines")}}
