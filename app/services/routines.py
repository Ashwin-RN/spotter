"""Routines: a workout saved by name, its items in the order done, and the weight to log for each lift (contracts
routines.md and routines-and-programs.md).

save_routine(user_id, data, by)
    data: name (1 to 64 characters), lifts (1 to 20 items, at most 60 sets in all: cardio counts toward the 20 items,
    not the 60 sets). An item is one of four kinds by its list row (routines-and-programs.md decisions 4 to 6, R5):
    - a lift of reps: exercise, sets (1 to 10), reps (1 to 100), weight_kg? (0 to 500, -200 to 0 on the two assisted
      lifts as log_lifts takes it, or null) or weight_lb? (0 to 1,102.31), never both. A band lift takes no weight but
      0, and is stored at 0 kg, as log_lifts stores its sets. Stored exactly {exercise, sets, reps, weight_kg}, as
      before C5b, so an unchanged save of a routine saved before writes nothing.
    - a hold: sets and its time; stored {exercise, sets, time_s, weight_kg}.
    - a carry or a sled: sets, its distance and its time when given; stored {exercise, sets, distance_m, time_s,
      weight_kg}. A hold's or a carry's weight is a lift's, null when none was given (decision 8).
    - cardio: minutes, and distance, incline, speed, heart rate and the machine's calories when given; stored
      {exercise, minutes, distance_km, incline_pct, speed_kmh, avg_hr, machine_kcal}, nulls kept, miles as km and mph
      as km/h at 2 dp, as log_cardio stores them. sets, reps or a weight on one is refused.
    A time comes as time_s or minutes and a distance as distance_m, distance_km or distance_mi, the forms log takes,
    and each is kept in the kind's one form by log_shape.convert_item, before the store: a lift's time_s and
    distance_m, cardio's minutes and distance_km. Each kind is checked by its log function's own checks: a lift of
    reps by today's, word for word; a hold or a carry by rules._set_numbers; cardio by cardio.check_pairs,
    check_numbers and with_speed. Every refusal reads "Lift n: ..." with its fix "on lift n". Returns {kind:
    "routine", id, created, routine {id, name, lifts, saved_on, updated_at}, summary, links {routines}[, units_note]}
    or {error, message, fix}. routine.lifts is as stored, a cardio item's weight_kg null.
    - A weight_lb is kept as weight_kg at 2 dp (contract units.md R10), a distance_mi as distance_km and a speed_mph
      as speed_kmh: the same items again in kg write nothing. The summary is in the person's units.
    - Every failure at once (rules.combine), nothing written. An exercise is a name on the list, case and spaces
      forgiven, and the list's own name is stored. A name is matched on the lifts first, exactly as before C5b, and a
      plain word of the lifts is refused as before; only a name that reaches neither is matched on the cardio list. A
      miss on both names the lifts' near names, then the cardio list's. The name is checked as sent, stripped, then
      stored with its spaces folded (R19): no newline, tab or run of spaces reaches a summary.
    - The row is found by user_id and name_key (the name, spaces folded, casefolded), deleted rows included. A new
      name makes a row. A name held replaces its name as sent, spaces folded, its lifts and saved_on (the person's
      today). A deleted one comes back live and replaced, with its id. created is true only for a new name.
    - The cap: a save that would make a routine live that is not (a new name, or a deleted one brought back) while
      rules.ROUTINES_MAX are live is too_many_routines. A replace of a live one is never refused by it.
    - A live routine whose name as sent, spaces folded, and items as checked are what is stored writes nothing:
      updated_at and saved_on stay, and the answer says it is already saved. No version is kept: nothing undoes a
      replace.

get_routines(user_id)
    A read: no lock, no write limit. Every live routine by name_key, then id, each item in ITEM_KEYS (R7): its plan
    (sets, reps, time_s, distance_m, minutes, distance_km, incline_pct, speed_kmh, null where the kind has none),
    weight_kg (always kg), weight_from, last_date, last, and log_with, the kind word log takes for it: lifts or
    cardio. A plan's avg_hr and machine_kcal are never a field: they are said in the routine's words and on the page.
    The routine's words: lift_words of its items with the weight to log, in the person's units. A lift's, a hold's or
    a carry's weight to log is the heaviest set of its latest live session in the one order (latest_sessions, the
    figures lifts.history's last_top gives), unless the routine holds a weight for it and that session is dated
    before saved_on, or there is no such session: then the routine's own weight. With neither, null. last is that
    same session (decision 11, R4): {date, sets [{weight_kg, reps, time_s, distance_m}] in set order, words}, the
    words as lifts.sets_text says the lift's sets, without its name, in the person's units; null when the lift was
    never logged. A cardio item's weight_kg, weight_from, last_date, sets and reps are null, and its last is the
    activity's latest live entry (latest_cardio): {date, minutes, distance_km, incline_pct, speed_kmh, words}, its
    heart rate and the machine's calories in its words only. The weights and last are one statement, run only when a
    routine places a lift, and cardio's last one more, run only when a routine places a cardio item (hardening D5),
    so a read runs a fixed number of statements however many routines and sessions.
    The read never raises on an item (decision 3, R2). An item is placed (placed) only when its exercise is a kind on
    the list, or a name the list no longer holds (as before C5a: a rename leaves the old name in the JSON; the main
    chat's C5a ruling 1), read as a lift of reps, and it holds its kind's numbers as numbers; any other key on it is
    ignored, so a field a later build adds never breaks this read.
    Anything else is left out of the items and of the routine's words, and the summary says so once a line
    (left_out): a hold saved with reps before 0013, never read as seconds (decision 9), or a carry with no distance,
    with how to log it (exercise-list.md R10); a cardio item with no minutes, to log as cardio; any other name or
    shape, plainly. The routine keeps it.

page_read(user_id)
    get_routines' own answer, and for the Routines page each routine's left-out lines by its id, in the page's words
    (left_out with page=True: no function or field named), and each routine's plan of each item it gives, in the
    page's words (item_text with page=True), a cardio plan's heart rate and the machine's calories included: one
    read, so the page's weights and last times are the assistant's.

lift_words(items, page=False, units=None)
    The items in words, for a summary and the Routines page: "Rowing Machine 10 min, Bench Press 3 × 8 at 60 kg,
    Plank 3 × 60 s and Dip 3 × 10" (R6). A weight shows when it is not null, 0 kg included: null means ask, 0 means
    bodyweight. In the person's units. Only the items get_routines places are said: any other is passed over, so a
    delete's preview of any routine never raises.

Every statement on routine names the person's id: by user_id and name_key for a save, by user_id for the read and
the cap (live_count), by common.owned() for an id. The write locks the user row first (common.lock_user), then checks
the write limit (common.check_write_limit), before any other read or work.
"""

import json
import math
import re

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import aliased

from app import db, formatting, links, log_shape, metrics, rules, users
from app.exercises import BY_NAME as LISTED
from app.models import CardioEntry, Exercise, LiftSession, LiftSet, Routine, User
from app.services import cardio, lifts
from app.services.common import (
    check_by, check_data, check_write_limit, exercises, lock_user, no_account, sent, with_units_note,
)

KIND = "routine"
# An item's fields (routines-and-programs.md, the note of 2026-11-22): a lift's, a hold's or a carry's, a time and a
# distance in log's forms, and a cardio activity's own numbers. Only exercise is needed by every kind.
TIME_FORMS = ("time_s", "minutes")
DISTANCE_FORMS = ("distance_m", "distance_km", "distance_mi")
CARDIO_ONLY = ("incline_pct", "speed_kmh", "speed_mph", "avg_hr", "machine_kcal")
LIFT_ONLY = ("sets", "reps", "weight_kg", "weight_lb")
LIFT_FIELDS = ("exercise", "sets", "reps", "weight_kg", "weight_lb", *TIME_FORMS, *DISTANCE_FORMS, *CARDIO_ONLY)
LIFT_FIELDS_TEXT = ("exercise, sets, reps, weight_kg or weight_lb, time_s or minutes, distance_m, distance_km or "
                    "distance_mi, incline_pct, speed_kmh or speed_mph, avg_hr, machine_kcal")
CARDIO_STORED = cardio.NUMBER_FIELDS  # minutes, distance_km, incline_pct, speed_kmh, avg_hr, machine_kcal: decision 6
# What get_routines gives each item, in this order (R7, the note of 2026-11-22): never avg_hr or machine_kcal, which
# a plan holds in its words only, so no field offers a plan's heart rate or calories as numbers to log.
ITEM_KEYS = ("exercise", "log_with", "sets", "reps", "time_s", "distance_m", "weight_kg", "weight_from", "minutes",
             "distance_km", "incline_pct", "speed_kmh", "last_date", "last")
LOG_WITH = {"lift": "lifts", "hold": "lifts", "carry": "lifts", "cardio": "cardio"}  # log's kind word for each kind
# What a lift, a hold and a carry take, as a list, by what it records (rules.records): a refusal's words.
TAKES = {"reps": "sets and reps", "time": "sets and time", "distance and time": "sets, distance and time"}
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


def _a_number(value) -> bool:
    """A finite JSON number, never a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def item_kind(name) -> str | None:
    """What an item of this name is (decision 4), by its list row: "lift" (a lift of reps, a band's too, or a name off
    the list, which reads as reps), "hold", "carry" (a carry or a sled) or "cardio"; None for a row of no such kind."""
    row = LISTED.get(name) if isinstance(name, str) else None
    if row is None:
        return "lift"
    if row.kind == "cardio":
        return "cardio"
    if row.kind != "lift":
        return None
    return {"reps": "lift", "time": "hold", "distance and time": "carry"}.get(rules.RECORDS.get(row.measures))


def placed(item) -> dict | None:
    """The item as get_routines places it, in its kind's stored shape (decision 6), or None when it cannot be (R2):
    a lift of reps {exercise, sets, reps, weight_kg}, a name off the list read so; a hold {exercise, sets, time_s,
    weight_kg}; a carry {exercise, sets, distance_m, time_s, weight_kg}; cardio {exercise, minutes, distance_km,
    incline_pct, speed_kmh, avg_hr, machine_kcal}. Sets, reps and a hold's time are whole numbers; minutes and a
    carry's distance numbers; every other number a number, null or not there. A hold saved with reps before 0013 holds
    no time_s and is never read as seconds (decision 9). Any other key is ignored."""
    if not isinstance(item, dict):
        return None
    name = item.get("exercise")
    if not isinstance(name, str) or not name.strip():  # a blank name is no lift: "An item is left out."
        return None
    kind = item_kind(name)
    if kind == "cardio":
        values = {k: item.get(k) for k in CARDIO_STORED}
        if not _a_number(values["minutes"]) or any(v is not None and not _a_number(v) for v in values.values()):
            return None
        return {"exercise": name, **values}
    # The main chat's C5a ruling 1 (2026-11-21): a name off the list is placed, as before C5a. A sync's rename gives
    # the row its new name and leaves the old one in the routine's JSON, and measures_of reads it as reps.
    sets, weight = item.get("sets"), item.get("weight_kg")
    if kind is None or not _whole_number(sets) or (weight is not None and not _a_number(weight)):
        return None
    if kind == "lift":
        reps = item.get("reps")
        return {"exercise": name, "sets": sets, "reps": reps, "weight_kg": weight} if _whole_number(reps) else None
    time_s = item.get("time_s")
    if kind == "hold":
        return {"exercise": name, "sets": sets, "time_s": time_s, "weight_kg": weight} if _whole_number(time_s) else None
    distance = item.get("distance_m")
    if not _a_number(distance) or (time_s is not None and not _whole_number(time_s)):
        return None
    return {"exercise": name, "sets": sets, "distance_m": distance, "time_s": time_s, "weight_kg": weight}


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


def _lift_text(x: dict, page: bool, units) -> str:
    """A lift of reps, as before C5b: '3 × 5 at 60 kg', '3 × 8 with 20 kg help', a band's '3 × 15'. A weight shows
    when it is not null, 0 kg included."""
    words = f"{formatting.show(x['sets'], 'count')} × {formatting.show(x['reps'], 'reps')}"
    if rules.measures_of(x["exercise"]) == ("reps",):
        pass
    elif x["weight_kg"] is not None and x["weight_kg"] < 0:  # help, as lifts.sets_text says it (R14)
        words += f" with {formatting.show(-x['weight_kg'], 'weight_kg', page=page, units=units)} help"
    elif x["weight_kg"] is not None:
        words += f" at {formatting.show(x['weight_kg'], 'weight_kg', page=page, units=units)}"
    return words


def _timed_text(x: dict, page: bool, units) -> str:
    """A hold or a carry (R6), as lifts.sets_text says a set of one: '3 × 60 s', '3 × 60 s at 10 kg', '3 × 40 m at
    32 kg each in 45 s', with ' a side' for one done one side at a time. A weight shows when it is not null, 0 kg
    included, as a lift's does."""
    row = LISTED.get(x["exercise"])
    side = " a side" if row is not None and row.sides == "each" else ""
    carry = "distance_m" in x
    done = (formatting.show(x["distance_m"], "metres", page=page) if carry
            else formatting.show(x["time_s"], "seconds", page=page))
    words = f"{formatting.show(x['sets'], 'count')} × {done}{side}"
    if x["weight_kg"] is not None:
        each = " each" if row is not None and row.load_type == "per_hand" else ""
        words += f" at {formatting.show(x['weight_kg'], 'weight_kg', page=page, units=units)}{each}"
    if carry and x["time_s"] is not None:
        words += f" in {formatting.show(x['time_s'], 'seconds', page=page)}"
    return words


def cardio_text(x: dict, page: bool = False, units=None, pace: bool = False) -> str:
    """A cardio plan or session in words (R4, R6), without its name: '10 min', '20 min at 12% incline', '30 min, 5 km',
    then the heart rate and the machine's calories when held: ', heart rate 140 bpm, the machine said 120 kcal'.
    pace: a session's, after its distance, as cardio.summary gives it ('30 min, 5 km, pace 6:00 per km'), and its
    speed only when it holds no distance. In the person's units."""
    words = formatting.show(x["minutes"], "minutes", page=page)
    at = []
    if x.get("incline_pct") is not None:
        at.append(f"{formatting.show(x['incline_pct'], 'incline', page=page)} incline")
    if x.get("speed_kmh") is not None and not (pace and x.get("distance_km")):
        at.append(formatting.show(x["speed_kmh"], "speed", page=page, units=units))
    if at:
        words += " at " + " and ".join(at)
    if x.get("distance_km"):
        words += f", {formatting.show(x['distance_km'], 'km', page=page, units=units)}"
        if pace:
            miles = units is not None and units.distance == "mi"
            shown = metrics.pace(x["distance_km"] / formatting.MI if miles else x["distance_km"], x["minutes"])
            words += f", pace {shown} per {formatting.unit_word('distance', units)}"
    if x.get("avg_hr") is not None:
        words += f", heart rate {formatting.show(x['avg_hr'], 'bpm', page=page)}"
    if x.get("machine_kcal") is not None:
        words += f", the machine said {formatting.show(x['machine_kcal'], 'kcal', page=page)}"
    return words


def item_text(x: dict, page: bool = False, units=None) -> str:
    """A placed item's plan in words, without its name, by its kind (R6)."""
    if "minutes" in x:
        return cardio_text(x, page, units)
    if "reps" in x:
        return _lift_text(x, page, units)
    return _timed_text(x, page, units)


def lift_words(items: list[dict], page: bool = False, units=None) -> str:
    """The items in words, for a summary and the Routines page: "Rowing Machine 10 min, Bench Press 3 × 8 at 60 kg,
    Plank 3 × 60 s and Dip 3 × 10". In the person's units. Only the items get_routines places are said: any other is
    passed over, so a delete's preview of any routine never raises."""
    return lifts._joined([f"{x['exercise']} {item_text(x, page, units)}" for x in filter(None, map(placed, items))])


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


def _on_lift(err: dict, n: int) -> dict:
    """A set's refusal (rules._set_numbers) at lift n: its fix's "on set n" is "on lift n", as log says a set's
    refusal at its part (routines-and-programs.md R5, the note of 2026-11-22)."""
    return {**err, "fix": err["fix"].replace(f"on set {n}", f"on lift {n}")}


def _at_lift(err: dict, n: int) -> dict:
    """A cardio refusal (cardio.check_pairs, check_numbers, said_in_miles, with_speed, in_miles), which names no
    place, at lift n, as the check that made it says a place given one: rules.both_sent's "Lift 2 has both ...",
    rules.bounded's "Lift 2: minutes 700 is outside 1 to 600." with "... from 1 to 600 on lift 2.", rules.call_unit's
    "Give distance_mi on lift 2 from ...". The speed's fix names the place after what to check."""
    place, on = f"Lift {n}", f" on lift {n}"
    message, fix = err["message"], err["fix"]
    both = re.fullmatch(r"Both (\S+) and (\S+) were sent: send one\.", message)
    message = f"{place} has both {both[1]} and {both[2]}: send one." if both else f"{place}: {message}"
    if both:
        pass  # rules.both_sent's fix names no place
    elif re.match(r"Give \S+ as ", fix):
        fix = re.sub(r"(: it is kept as a whole number)?\.$", lambda m: f"{on}{m[1] or ''}.", fix, count=1)
    elif re.match(r"Give \S+ ", fix):
        fix = re.sub(r"^Give (\S+) ", lambda m: f"Give {m[1]}{on} ", fix, count=1)
    elif ", or " in fix:
        fix = fix.replace(", or ", f"{on}, or ", 1)
    else:
        fix = re.sub(r"\.$", f"{on}.", fix, count=1)
    return {**err, "message": message, "fix": fix}


def _match(sent_name: str, names: list[str], cardio_names: list[str], where: str,
           looks: str | None) -> tuple[str | None, str | None, dict | None]:
    """(the list's name, its kind, None) or (None, None, the refusal). A name is matched on the lifts first, exactly as
    before C5b, and a plain word of the lifts is refused as before. Only a name that reaches neither is matched on the
    cardio list, and a plain word of it refused as log_cardio refuses one. A miss on both gives near names from both
    lists, as the item's own fields point (looks): a lift's fields, the lifts' near names alone, as before C5b; cardio's,
    the cardio list's first; neither, the lifts' first."""
    name, _ = rules.match_exercise(sent_name, names)
    if name is not None:
        return name, item_kind(name), None
    if rules.find_exercise(sent_name, names)[1]:
        return None, None, rules.unknown_exercise(sent_name, names, f"{where}: ")
    hit, close = rules.match_exercise(sent_name, cardio_names)
    if hit is not None:
        return hit, "cardio", None
    if rules.find_exercise(sent_name, cardio_names)[1]:
        return None, None, rules.unknown_exercise(sent_name, cardio_names, f"{where}: ")
    miss = rules.unknown_exercise(sent_name, names, f"{where}: ")
    if looks == "lift":
        return None, None, miss
    near = list(dict.fromkeys(close + miss["closest"] if looks == "cardio" else miss["closest"] + close))
    if near == miss["closest"]:
        return None, None, miss
    return None, None, {**miss, "fix": f"Closest names: {', '.join(near)}. Use a name exactly as list_exercises gives "
                                      "it.", "closest": near}


def _sets_check(name, n: int, where: str, only_sent: bool = False):
    """A lift's numbers through rules._set_numbers, at lift n. only_sent: a lift of reps, whose reps are checked as
    before C5b: only a time or a distance sent is checked, and refused as log refuses it ("records reps, not
    time")."""
    def check(row: dict) -> tuple[dict, list[dict]]:
        given = {f: row.get(f) for f in rules.SET_NUMBERS if sent(row.get(f))}
        if only_sent and not given:
            return {}, []
        numbers, errors = rules._set_numbers(given if only_sent else row, n, where, name)
        return numbers, [_on_lift(e, n) for e in errors]
    return check


def _cardio_check(n: int):
    """A cardio item's numbers through log_cardio's own checks, at lift n: the pairs (a mile number in km, unrounded),
    each number in its range (a refusal of a mile number said in miles), then cardio.with_speed, as log_cardio runs
    it: a stated speed against the distance and the minutes, and a worked-out one in its range. The values are kept
    as log_cardio stores them: miles as km and mph as km/h at 2 dp, minutes at 1 dp. with_speed is run for its
    refusal alone: no speed is worked out into the plan."""
    def check(row: dict) -> tuple[dict, list[dict]]:
        data, in_miles, errors = cardio.check_pairs(row)
        values, found = cardio.check_numbers(data)
        errors += [cardio.said_in_miles(e, in_miles) for e in found]
        if not errors:
            _, err = cardio.with_speed(values)  # its refusal only: the plan keeps the speed as given
            if err:
                errors.append(cardio.in_miles(err, row, values) if in_miles else err)
        return values, [_at_lift(e, n) for e in errors]
    return check


def _weight(raw: dict, n: int, where: str, name, band: bool) -> tuple[float | None, list[dict]]:
    """A lift's, a hold's or a carry's weight, as before C5b: weight_kg or weight_lb, kept in kg (R10), null when
    none was given, a band lift's 0 kg. A refusal of a weight_lb is said in lb."""
    errors = []
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
    return weight, errors


def _lift(raw, n: int, names: list[str], cardio_names: list[str]) -> tuple[dict, list[dict]]:
    """One item, checked by its kind (decisions 4 to 6, R5): a lift {exercise, sets, reps, weight_kg}, a hold
    {exercise, sets, time_s, weight_kg}, a carry or a sled {exercise, sets, distance_m, time_s, weight_kg}, cardio
    {exercise, minutes, distance_km, incline_pct, speed_kmh, avg_hr, machine_kcal}, or the errors. A missing value
    stays None. A time and a distance come in log's forms and are kept in the kind's one form
    (log_shape.convert_item). A name that reaches no row is checked as its fields say: cardio when it sends a time or
    a cardio number and no sets or reps, else a lift of reps, as before C5b."""
    raw = raw if isinstance(raw, dict) else {}
    where = f"Lift {n}"
    errors = [rules.error("invalid_field", f"{where}: '{rules.quote(k)}' is not a field of a lift.",
                          f"Use: {LIFT_FIELDS_TEXT}.") for k in raw if k not in LIFT_FIELDS]
    name_sent = raw.get("exercise")
    name_sent = name_sent.strip() if isinstance(name_sent, str) else ("" if name_sent is None else name_sent)
    given = {k for k in LIFT_FIELDS if sent(raw.get(k))}
    # What the item's own fields point to, for a name that reaches no row: cardio's numbers and no sets or reps, a
    # lift's fields, or neither.
    looks = ("cardio" if given & {*TIME_FORMS, *CARDIO_ONLY} and not given & {"sets", "reps"}
             else "lift" if given & set(LIFT_ONLY) else None)
    name, kind = None, None
    if not name_sent:
        errors.append(rules.error("missing_field", f"{where}: exercise is missing.",
                                  f"Give an exercise name on lift {n}."))
    else:
        name, kind, err = _match(name_sent, names, cardio_names, where, looks)
        errors += [err] if err else []
    if kind is None:  # checked as its fields say: cardio's, or a lift of reps, as before C5b
        kind = "cardio" if looks == "cardio" else "lift"
    if kind == "cardio":
        if name is not None:
            errors += [rules.error("invalid_field", f"{where}: {name} is cardio: it takes minutes, not {k}.",
                                   f"Send minutes on lift {n}, with distance, incline or speed when the person gave "
                                   "them.") for k in LIFT_ONLY if k in given]
        numbers = {k: raw[k] for k in (*TIME_FORMS, *DISTANCE_FORMS, *CARDIO_ONLY) if k in raw}
        values, found = log_shape.convert_item("cardio", numbers, where, _cardio_check(n))
        errors += found
        values = values or {}
        return {"exercise": name, **{k: values.get(k) for k in CARDIO_STORED}}, errors
    if name is not None:
        takes = TAKES[rules.records(name)]
        errors += [rules.error("invalid_field", f"{where}: {name} is a lift: it takes {takes}, not {k}.",
                               f"Leave {k} out on lift {n}.") for k in CARDIO_ONLY if k in given]
    sets, err = _whole(raw.get("sets"), "sets", where, n, rules.ROUTINE_LIFT_SETS, "invalid_field")
    errors += [err] if err else []
    numbers = {k: raw[k] for k in ("reps", *TIME_FORMS, *DISTANCE_FORMS) if k in raw}
    values: dict = {}
    if kind == "lift":
        reps, err = _whole(raw.get("reps"), "reps", where, n, rules.HARD_RULES["reps"], "reps_out_of_range")
        errors += [err] if err else []
        numbers.pop("reps", None)
        _, found = log_shape.convert_item("lifts", numbers, where, _sets_check(name, n, where, only_sent=True))
        errors += found
    else:
        values, found = log_shape.convert_item("lifts", numbers, where, _sets_check(name, n, where))
        errors += found
        values = values or {}
    band = name is not None and rules.measures_of(name) == ("reps",)  # a band lift: no weight, stored as 0 kg (R6)
    weight, found = _weight(raw, n, where, name, band)
    errors += found
    if kind == "lift":
        return {"exercise": name, "sets": sets, "reps": reps, "weight_kg": weight}, errors
    if kind == "hold":
        return {"exercise": name, "sets": sets, "time_s": values.get("time_s"), "weight_kg": weight}, errors
    return {"exercise": name, "sets": sets, "distance_m": values.get("distance_m"), "time_s": values.get("time_s"),
            "weight_kg": weight}, errors


def _checked(data: dict, names: list[str], cardio_names: list[str]) -> tuple[str | None, list[dict], list[dict]]:
    """(the name, the items, every failure), in the order of the contract's ruling R5."""
    name, err = rules.required_text(data.get("name"), "name", limit=rules.ROUTINE_NAME_MAX)
    name = " ".join(name.split()) if name else name  # R19: stored and shown with its spaces folded, after the check
    errors = [err] if err else []
    errors += [rules.error("invalid_field", f"'{rules.quote(k)}' is not a field of a routine.", "Use: name, lifts.")
               for k in data if k not in ("name", "lifts")]
    sent_items = data.get("lifts") if isinstance(data.get("lifts"), list) else []
    lo, hi = rules.ROUTINE_LIFTS
    if not lo <= len(sent_items) <= hi:
        errors.append(rules.error("invalid_field",
                                  f"A routine holds {lo} to {hi} lifts, not {formatting.show(len(sent_items), 'count')}.",
                                  f"Send {lo} to {hi} lifts."))
    checked = []
    for n, raw in enumerate(sent_items[:hi], start=1):
        item, found = _lift(raw, n, names, cardio_names)
        checked.append(item)
        errors += found
    lifted = [x for x in checked if "sets" in x]  # cardio counts toward the 20 items, not the 60 sets (decision 10)
    most = rules.SESSION_SETS[1]
    total = sum(x["sets"] or 0 for x in lifted)
    if lifted and all(x["sets"] is not None for x in lifted) and total > most:
        errors.append(rules.error(
            "sets_count_out_of_range",
            f"{name or 'This routine'} would hold {formatting.show(total, 'count')} sets. A routine holds at most "
            f"{formatting.show(most, 'count')}, so that it fits in one session.",
            "Send fewer sets, or split it into two routines."))
    return name, checked, errors


def _as_saved(item) -> dict:
    """An item as the save's answer gives it: as stored, and a cardio item's weight_kg null, which it never holds,
    as the answer's model needs one for every item."""
    return item if not isinstance(item, dict) or "weight_kg" in item else {**item, "weight_kg": None}


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
        name, checked, errors = _checked(data, [e.name for e in exercises(s, "lift")],
                                         [e.name for e in exercises(s, "cardio")])
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
        saved = routine_dict(row)
        out = {"kind": KIND, "id": row.id, "created": created,
               "routine": {**saved, "lifts": [_as_saved(x) for x in saved["lifts"]]}, "summary": summary,
               "links": {"routines": links.page("routines")}}
        lifts_sent = data.get("lifts") if isinstance(data.get("lifts"), list) else []
        pounds = any(isinstance(x, dict) and sent(x.get("weight_lb")) for x in lifts_sent)
        miles = any(isinstance(x, dict) and (sent(x.get("distance_mi")) or sent(x.get("speed_mph")))
                    for x in lifts_sent)
        return with_units_note(out, user, pounds, miles)


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


def latest_cardio(s, user_id: int, today, names: set[str]) -> dict[str, dict]:
    """{activity: {date, minutes, distance_km, incline_pct, speed_kmh, avg_hr, machine_kcal}} for these activities
    (R3, the note of 2026-11-22): each one's latest live entry in the one order (effective date, then id), with its
    real date. One statement, a window over each activity's entries, run only when a routine places a cardio item."""
    effective = case((CardioEntry.date > today, today), else_=CardioEntry.date)  # metrics.on_or_before
    ranked = (
        select(Exercise.name.label("activity"), CardioEntry.date.label("day"),
               *(getattr(CardioEntry, k).label(k) for k in CARDIO_STORED),
               func.row_number().over(partition_by=Exercise.name,
                                      order_by=(effective.desc(), CardioEntry.id.desc())).label("n"))
        .select_from(CardioEntry).join(Exercise, CardioEntry.exercise_id == Exercise.id)
        .where(CardioEntry.user_id == user_id, CardioEntry.deleted_at.is_(None), Exercise.name.in_(names))
        .subquery())
    found = s.execute(select(ranked.c.activity, ranked.c.day, *(ranked.c[k] for k in CARDIO_STORED))
                      .where(ranked.c.n == 1))
    return {row.activity: {"date": row.day, **{k: row._mapping[k] for k in CARDIO_STORED}} for row in found}


def last_time(name: str, latest: dict | None, units=None) -> dict | None:
    """A lift's last (R4): {date, sets, words}, the words as lifts.sets_text says the lift's sets, without its name
    ("62.5 kg × 8, 62.5 kg × 8, 57.5 kg × 7", "3 × 60 s"). None when never logged."""
    if latest is None:
        return None
    words = lifts.sets_text([(name, *x) for x in latest["sets"]], units)
    return {"date": latest["date"].isoformat(),
            "sets": [{"weight_kg": w, "reps": r, "time_s": t, "distance_m": d} for w, r, t, d in latest["sets"]],
            "words": words[len(name) + 1:]}


def cardio_last(latest: dict | None, units=None) -> dict | None:
    """A cardio item's last (R4, the note of 2026-11-22): {date, minutes, distance_km, incline_pct, speed_kmh, words}.
    The session's heart rate and the machine's calories are in its words only, so same as last time never sends them
    as today's: "30 min, 5 km, pace 6:00 per km, heart rate 145 bpm, the machine said 180 kcal". None when never
    logged."""
    if latest is None:
        return None
    return {"date": latest["date"].isoformat(), "minutes": latest["minutes"], "distance_km": latest["distance_km"],
            "incline_pct": latest["incline_pct"], "speed_kmh": latest["speed_kmh"],
            "words": cardio_text(latest, units=units, pace=True)}


def _item(x: dict, row: Routine, tops: dict, sessions: dict, units) -> tuple[dict, dict]:
    """(the item as get_routines gives it, in ITEM_KEYS' order, and the item as its words read it, with the weight to
    log). A lift's, a hold's or a carry's weight to log is the heaviest set of its latest session unless the routine
    holds a weight and that session is dated before saved_on, or there is none: then the routine's own (decision 7).
    A cardio item has no weight, and its last is the activity's latest entry."""
    kind = item_kind(x["exercise"])
    shown = {k: x.get(k) for k in ITEM_KEYS}
    shown["log_with"] = LOG_WITH[kind]
    if kind == "cardio":
        shown["last"] = cardio_last(sessions.get(x["exercise"]), units)
        return shown, x
    top = tops.get(x["exercise"])
    if top is not None and (x["weight_kg"] is None or top["date"] >= row.saved_on):
        weight, source = top["weight_kg"], "last_session"
    elif x["weight_kg"] is not None:
        weight, source = x["weight_kg"], "routine"
    else:
        weight, source = None, None
    shown.update(weight_kg=weight, weight_from=source, last_date=top["date"].isoformat() if top else None,
                 last=last_time(x["exercise"], top, units))
    return shown, {**x, "weight_kg": weight}


def _read(user_id: int) -> tuple[dict, dict[int, list[str]], dict[int, list[str]]]:
    """get_routines' answer, each routine's left-out lines for the page by its id, in its order, and each routine's
    plans for the page by its id, one an item it places, in the page's words."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account(), {}, {}
        rows = list(s.scalars(select(Routine).where(Routine.user_id == user.id, Routine.deleted_at.is_(None))
                              .order_by(Routine.name_key, Routine.id)))
        stored = {row.id: _stored(row) for row in rows}
        found = [x for items in stored.values() for x in filter(None, map(placed, items))]
        named = {x["exercise"] for x in found if item_kind(x["exercise"]) != "cardio"}
        activities = {x["exercise"] for x in found if item_kind(x["exercise"]) == "cardio"}
        today = users.today_for(user)
        tops = latest_sessions(s, user.id, today, named) if named else {}
        sessions = latest_cardio(s, user.id, today, activities) if activities else {}
        units = users.units_for(user)
    routines, lines, left, plans = [], [], {}, {}
    for row in rows:
        items, said, page_lines = [], [], []
        for raw in stored[row.id]:
            x = placed(raw)
            if x is None:
                lines.append(left_out(raw))
                page_lines.append(left_out(raw, page=True))
                continue
            shown, words = _item(x, row, tops, sessions, units)
            items.append(shown)
            said.append(words)
        routines.append({"id": row.id, "name": row.name, "saved_on": row.saved_on.isoformat(), "lifts": items,
                         "words": lift_words(said, units=units)})
        left[row.id] = list(dict.fromkeys(page_lines))
        plans[row.id] = [item_text(x, page=True, units=units) for x in said]
    n = len(routines)
    names = lifts._joined([r["name"] for r in routines])
    out = {"routines": routines, "how": HOW,
           "summary": f"{formatting.show(n, 'count')} routine{'' if n == 1 else 's'}: {names}." if n else NONE_YET}
    out["summary"] += "".join(f" {line}" for line in dict.fromkeys(lines))
    if not n:
        out["note"] = NONE_NOTE
    return {**out, "links": {"routines": links.page("routines")}}, left, plans


def get_routines(user_id: int) -> dict:
    """Every live routine, each item with its plan, the weight to log and last time. Returns {routines, how,
    summary[, note], links} or {error, message, fix}. See the module notes."""
    return _read(user_id)[0]


def page_read(user_id: int) -> tuple[dict, dict[int, list[str]], dict[int, list[str]]]:
    """For the Routines page: get_routines' answer, each routine's left-out lines by its id, and each routine's plan
    of each item it gives, in the page's words, by its id: a cardio plan's heart rate and the machine's calories are
    said there, never in a field of the answer."""
    return _read(user_id)
