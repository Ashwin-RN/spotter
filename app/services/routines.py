"""Routines: a workout saved by name, and the weight to log for each of its lifts (contract routines.md).

save_routine(user_id, data, by)
    data: name (1 to 64 characters), lifts [{exercise, sets (1 to 10), reps (1 to 100), weight_kg? (0 to 500, -200
    to 0 on the two assisted lifts as log_lifts takes it, or null) or weight_lb? (0 to 1,102.31), never both}] (1 to
    20 lifts, at most 60 sets in all). A band lift takes no weight but 0, and is stored at 0 kg, as log_lifts stores
    its sets. Returns {kind: "routine", id, created, routine {id, name, lifts, saved_on,
    updated_at}, summary, links {routines}[, units_note]} or {error, message, fix}.
    - A weight_lb is kept as weight_kg at 2 dp (contract units.md R10): the stored lifts are always {exercise, sets,
      reps, weight_kg}, in kg, so the same lifts again in kg write nothing. The summary is in the person's units.
    - Every failure at once (rules.combine), nothing written. An exercise is a name on the lift list, case and spaces
      forgiven, and the list's own name is stored. A routine holds lifts of sets and reps only (exercise-list.md
      R10): a hold, a carry or a sled is refused, to be logged with log_lifts. The name is checked as sent, stripped,
      then stored with its
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
    weight_from, last_date and last, and the routine's words: lift_words of its lifts with the weight to log, in the
    person's units. The weight to log is the heaviest set of the lift's latest live session in the one order
    (latest_sessions, the figures lifts.history's last_top gives), unless the routine holds a weight for it and that
    session is dated before saved_on, or there is no such session: then the routine's own weight. With neither, null.
    last is that same session (routines-and-programs.md decision 11, R4): {date, sets [{weight_kg, reps, time_s,
    distance_m}] in set order, words}, the words as lifts.sets_text says the lift's sets, without its name, in the
    person's units; null when the lift was never logged. The weights and last are one statement, run only when a
    routine names a lift it places (hardening D5), so a read runs a fixed number of statements however many routines
    and sessions.
    The read never raises on an item (routines-and-programs.md decision 3, R2). An item is placed (placed) only when
    its exercise is a lift of reps on the list, or a name the list no longer holds (as before C5a: a rename leaves the
    old name in the JSON; the main chat's C5a ruling 1), and it holds whole-number sets and reps, and a weight_kg that
    is a number, null or not there; any other key on it is ignored, so a field a later build adds never breaks this
    read.
    Anything else is left out of the lifts to log and of the routine's words, and the summary says so once a line
    (left_out): a lift saved before that now records a time, or a distance and time, with how to log it
    (exercise-list.md R10); a cardio row, to log as cardio; any other name or shape, plainly. The routine
    keeps it.

page_read(user_id)
    get_routines' own answer, and for the Routines page each routine's left-out lines by its id, in the page's words
    (left_out with page=True: no function or field named): one read, so the page's weights and last times are the
    assistant's.

lift_words(lifts, page=False, units=None)
    The lifts in words, for a summary and the Routines page: "Bench Press 3 × 8 at 60 kg and Dip 3 × 10". A weight
    shows when it is not null, 0 kg included: null means ask, 0 means bodyweight. In the person's units. Only the
    items get_routines places are said: any other is passed over, so a delete's preview of any routine never raises.

Every statement on routine names the person's id: by user_id and name_key for a save, by user_id for the read and
the cap (live_count), by common.owned() for an id. The write locks the user row first (common.lock_user), then checks
the write limit (common.check_write_limit), before any other read or work.
"""

import json

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import aliased

from app import db, formatting, links, rules, users
from app.exercises import BY_NAME as LISTED
from app.models import Exercise, LiftSession, LiftSet, Routine, User
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


# A lift a routine held before it recorded a time, or a distance and time (exercise-list.md R10): how to ask for it.
ASK_FOR = {"time": ("how long", "time_s"), "distance and time": ("how far", "distance_m")}


def left_out_line(lift: str, page: bool = False) -> str:
    """get_routines' line for a lift a routine holds that is no longer a lift of sets and reps: 'Plank is left out:
    it records time now. Ask the person how long, and log it with time_s.' A lift ASK_FOR has no row for: 'Plank is
    left out.' page=True: the Routines page's line, without what the assistant is told: 'Plank is left out: it
    records time now.'"""
    what = rules.records(lift)
    if what not in ASK_FOR:
        return f"{lift} is left out."
    ask, field = ASK_FOR[what]
    if page:
        return f"{lift} is left out: it records {what} now."
    return f"{lift} is left out: it records {what} now. Ask the person {ask}, and log it with {field}."


def _whole_number(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def placed(item) -> dict | None:
    """The item as get_routines places it, {exercise, sets, reps, weight_kg}, or None when it cannot be (R2): a name
    that is a lift of reps on the list (a band lift too) or no name on the list at all, whole-number sets and reps,
    and a weight_kg that is a number, null or not there. Any other key is ignored."""
    if not isinstance(item, dict):
        return None
    name = item.get("exercise")
    if not isinstance(name, str) or not name.strip():  # a blank name is no lift: "An item is left out."
        return None
    row = LISTED.get(name)
    # The main chat's C5a ruling 1 (2026-11-21): a name off the list is placed, as before C5a. A sync's rename gives
    # the row its new name and leaves the old one in the routine's JSON, and measures_of reads it as reps. Only a
    # listed cardio row, or a listed lift whose measures are not reps, is left out.
    if row is not None and (row.kind != "lift" or rules.RECORDS.get(row.measures) != "reps"):
        return None
    sets, reps, weight = item.get("sets"), item.get("reps"), item.get("weight_kg")
    if not (_whole_number(sets) and _whole_number(reps)):
        return None
    if weight is not None and (isinstance(weight, bool) or not isinstance(weight, (int, float))):
        return None
    return {"exercise": name, "sets": sets, "reps": reps, "weight_kg": weight}


def left_out(item, page: bool = False) -> str:
    """The line for an item get_routines does not place (R2): a hold or carry by left_out_line, a cardio row 'Rowing
    Machine is left out: log it as cardio.', any other name 'Zorb Press is left out.', and with no name 'An
    item is left out.' page=True: the Routines page's line, which names no function or field (the page's text
    rules): 'Rowing Machine is left out.', 'Plank is left out: it records time now.'"""
    name = item.get("exercise") if isinstance(item, dict) else None
    if not isinstance(name, str) or not name.strip():
        return "An item is left out."
    name = " ".join(name.split())
    row = LISTED.get(name)
    if row is not None and row.kind == "cardio":
        return f"{name} is left out." if page else f"{name} is left out: log it as cardio."
    if row is not None and row.kind == "lift":
        return left_out_line(name, page)
    return f"{name} is left out."


def _stored(row: Routine) -> list:
    """A routine's items as stored: its JSON list, or none when it holds something else."""
    items = json.loads(row.lifts)
    return items if isinstance(items, list) else []


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
    """'Bench Press 3 × 5 at 60 kg and Assisted Pull-Up 3 × 8 with 20 kg help'. A band lift says no weight: it has
    none (exercise-list.md R6)."""
    parts = []
    for x in filter(None, map(placed, items)):
        words = f"{x['exercise']} {formatting.show(x['sets'], 'count')} × {formatting.show(x['reps'], 'reps')}"
        if rules.measures_of(x["exercise"]) == ("reps",):
            pass
        elif x["weight_kg"] is not None and x["weight_kg"] < 0:  # help, as lifts.sets_text says it (R14)
            words += f" with {formatting.show(-x['weight_kg'], 'weight_kg', page=page, units=units)} help"
        elif x["weight_kg"] is not None:
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
        elif "reps" not in rules.measures_of(name):
            errors.append(rules.error("invalid_field", f"{where}: {name} records {rules.records(name)}: a routine "
                                      "holds lifts of sets and reps.", "Log its sets instead."))
    sets, err = _whole(raw.get("sets"), "sets", where, n, rules.ROUTINE_LIFT_SETS, "invalid_field")
    errors += [err] if err else []
    reps, err = _whole(raw.get("reps"), "reps", where, n, rules.HARD_RULES["reps"], "reps_out_of_range")
    errors += [err] if err else []
    band = name is not None and rules.measures_of(name) == ("reps",)  # a band lift: no weight, stored as 0 kg (R6)
    sent_in, weight, err = rules.one_of(raw, "weight_kg", "weight_lb", where)
    if err:
        errors.append(err)
        weight = None
    elif sent_in is None:
        weight = 0.0 if band else None
    elif band:
        weight, err = rules.number(weight, float, "weight_kg", where)
        if err is None and weight != 0:  # any weight but 0, a minus one too, in log_lifts' words
            err = rules.error("invalid_field", f"{where}: {name} records reps, not weight.",
                              f"Leave the weight out on lift {n}: a band lift stores 0 kg.")
        errors += [err] if err else []
        weight = 0.0 if err is None else None
    else:
        sent_kg = weight  # in kg, converted unrounded from lb: the range is checked on it, then it is kept
        weight, err = rules.number(weight, float, "weight_kg", where)
        # The range log_lifts takes for the lift (exercise-list.md R6): help, -200 to 0 kg, on the two assisted lifts
        # only, so a weight get_routines hands out from the last session can be saved back.
        lo, hi = rules.weight_range(name)
        kg = rules.to_float(sent_kg) if err is None else None
        tail = ", or leave it out"
        if err is None and rules.takes_help(name):
            tail = ": the help as a minus weight, or leave it out"
            if not lo <= kg <= hi:
                shown = rules.span(lo, hi, "weight_kg")
                err = rules.error("weight_out_of_range",
                                  f"{where}: weight {rules.as_sent(raw.get('weight_kg'))} kg is outside {shown} kg.",
                                  f"Give weight_kg on lift {n} from {shown}{tail}.")
        elif err is None and name is not None and kg < 0:
            err = rules.error("weight_out_of_range",
                              f"{where}: a minus weight is help, on Assisted Pull-Up or Assisted Dip only.",
                              f"Give weight_kg on lift {n} from {lo} to {hi}, or leave it out.")
        elif err is None and not lo <= kg <= hi:
            err = rules.error("weight_out_of_range",
                              f"{where}: weight {rules.as_sent(raw.get('weight_kg'))} kg is outside {lo} to {hi} kg.",
                              f"Give weight_kg on lift {n} from {lo} to {hi}, or leave it out.")
        if err and sent_in == "weight_lb":
            err = rules.in_unit(err, "weight_lb", raw["weight_lb"], (lo, hi), "weight_kg", where, tail)
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


def latest_sessions(s, user_id: int, today, names: set[str]) -> dict[str, dict]:
    """{lift: {date, weight_kg, sets}} for these lifts (hardening D5; routines-and-programs.md R3): each lift's latest
    live session in the one order (effective date, then id), its real date, its heaviest set of the lift, as
    lifts.history's last_top gives them, and every set of the lift in it in set order, as (weight_kg, reps, time_s,
    distance_m). One statement: a window over each lift's sessions, newest first, and that session's sets of the
    lift, so the weight to log and last time are always the same session."""
    effective = case((LiftSession.date > today, today), else_=LiftSession.date)  # metrics.on_or_before
    per_session = (
        select(Exercise.name.label("lift"), LiftSession.id.label("session_id"), LiftSession.date.label("day"),
               func.row_number().over(partition_by=Exercise.name,
                                      order_by=(effective.desc(), LiftSession.id.desc())).label("n"))
        .select_from(LiftSet).join(LiftSession, LiftSet.session_id == LiftSession.id)
        .join(Exercise, LiftSet.exercise_id == Exercise.id)
        .where(LiftSession.user_id == user_id, LiftSession.deleted_at.is_(None), Exercise.name.in_(names))
        .group_by(Exercise.name, LiftSession.id, LiftSession.date)
        .subquery())
    latest = select(per_session.c.lift, per_session.c.session_id, per_session.c.day).where(
        per_session.c.n == 1).subquery()
    done, lift = aliased(LiftSet), aliased(Exercise)
    found = s.execute(
        select(latest.c.lift, latest.c.day, done.weight_kg, done.reps, done.time_s, done.distance_m)
        .select_from(latest).join(done, done.session_id == latest.c.session_id)
        .join(lift, and_(done.exercise_id == lift.id, lift.name == latest.c.lift))
        .order_by(latest.c.lift, done.set_number, done.id))
    out: dict[str, dict] = {}
    for name, day, weight, reps, time_s, distance_m in found:
        one = out.setdefault(name, {"date": day, "weight_kg": weight, "sets": []})
        one["weight_kg"] = max(one["weight_kg"], weight)
        one["sets"].append((weight, reps, time_s, distance_m))
    return out


def latest_tops(s, user_id: int, today, names: set[str]) -> dict[str, dict]:
    """{lift: {date, weight_kg}}: latest_sessions' weights alone, as lifts.history's last_top gives them."""
    return {name: {"date": x["date"], "weight_kg": x["weight_kg"]}
            for name, x in latest_sessions(s, user_id, today, names).items()}


def last_time(name: str, latest: dict | None, units=None) -> dict | None:
    """An item's last (R4): {date, sets, words}, the words as lifts.sets_text says the lift's sets, without its name
    ("62.5 kg × 8, 62.5 kg × 8, 57.5 kg × 7", "3 × 60 s"). None when never logged."""
    if latest is None:
        return None
    words = lifts.sets_text([(name, *x) for x in latest["sets"]], units)
    return {"date": latest["date"].isoformat(),
            "sets": [{"weight_kg": w, "reps": r, "time_s": t, "distance_m": d} for w, r, t, d in latest["sets"]],
            "words": words[len(name) + 1:]}


def _read(user_id: int) -> tuple[dict, dict[int, list[str]]]:
    """get_routines' answer, and each routine's left-out lines for the page by its id, in its order."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account(), {}
        rows = list(s.scalars(select(Routine).where(Routine.user_id == user.id, Routine.deleted_at.is_(None))
                              .order_by(Routine.name_key, Routine.id)))
        stored = {row.id: _stored(row) for row in rows}
        named = {x["exercise"] for items in stored.values() for x in filter(None, map(placed, items))}
        latest = latest_sessions(s, user.id, users.today_for(user), named) if named else {}
        units = users.units_for(user)
    routines, lines, left = [], [], {}
    for row in rows:
        items, page_lines = [], []
        for raw in stored[row.id]:
            x = placed(raw)
            if x is None:
                lines.append(left_out(raw))
                page_lines.append(left_out(raw, page=True))
                continue
            top = latest.get(x["exercise"])
            if top is not None and (x["weight_kg"] is None or top["date"] >= row.saved_on):
                weight, source = top["weight_kg"], "last_session"
            elif x["weight_kg"] is not None:
                weight, source = x["weight_kg"], "routine"
            else:
                weight, source = None, None
            items.append({**x, "weight_kg": weight, "weight_from": source,
                          "last_date": top["date"].isoformat() if top else None,
                          "last": last_time(x["exercise"], top, units)})
        routines.append({"id": row.id, "name": row.name, "saved_on": row.saved_on.isoformat(), "lifts": items,
                         "words": lift_words(items, units=units)})
        left[row.id] = list(dict.fromkeys(page_lines))
    n = len(routines)
    names = lifts._joined([r["name"] for r in routines])
    out = {"routines": routines, "how": HOW,
           "summary": f"{formatting.show(n, 'count')} routine{'' if n == 1 else 's'}: {names}." if n else NONE_YET}
    out["summary"] += "".join(f" {line}" for line in dict.fromkeys(lines))
    if not n:
        out["note"] = NONE_NOTE
    return {**out, "links": {"routines": links.page("routines")}}, left


def get_routines(user_id: int) -> dict:
    """Every live routine, each lift with the weight to log and last time. Returns {routines, how, summary[, note],
    links} or {error, message, fix}. See the module notes."""
    return _read(user_id)[0]


def page_read(user_id: int) -> tuple[dict, dict[int, list[str]]]:
    """For the Routines page: get_routines' answer, and each routine's left-out lines by its id."""
    return _read(user_id)
