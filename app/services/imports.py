"""Import: a person's history brought over from another app, many dated rows in one call.

import_entries(user_id, data, by)
    data: rows (1 to 200 dated rows of mixed kinds), dry_run (true unless false is sent), weight_unit
    (kg or lb), distance_unit (km or mi), length_unit (cm or in). A unit is never assumed: a call whose
    rows hold a weight (a weigh-in's or a set's) needs weight_unit, a distance or a speed needs
    distance_unit, a body size (waist, hips, chest, arm, thigh) needs length_unit. A call that holds a
    quantity without its unit is refused whole, missing_unit, a preview too. A call that holds none of a
    quantity needs no unit for it, and a unit sent that no row needs is taken.
    by is checked as every write's is (web or claude). Every row is stored with logged_by "import",
    whoever calls.

    Each row: kind (lift_session, bodyweight, measurement, food_entry or cardio_entry) and date, plus
    the fields its own log function takes. Only the fields that carry a unit are renamed: weight (a
    weigh-in, or a set), distance and speed (cardio), and value for a body size, each in the call's
    unit. Spotter converts them (lb x 0.45359237, mi x 1.609344, in x 2.54), and the existing checks
    keep them at the column's 2 dp, so the same file gives the same stored numbers every time. A
    refusal of a converted number quotes it as sent, with the range, in the call's unit.
      lift_session  sets [{exercise, weight, reps, times?}] (times 1 to 20: one item for "3 sets of
                    5"), notes?. At most 60 sets once times is counted.
      bodyweight    weight
      measurement   measurement_kind, value
      food_entry    entries [{description, kcal, meal}] (1 to 30): the other app's own numbers, stored
                    as you_said, estimated false.
      cardio_entry  exercise, minutes, distance?, incline_pct?, speed? (in the call's distance unit an
                    hour, converted as distance is), avg_hr?, machine_kcal?, notes?

    Each row is checked by the rules its own log function uses (app.rules, lifts, body, food,
    cardio); a refusal names the row by its number as sent, from 1. A row that fails is left out, and
    the others go on. A session with one bad set fails whole. A name not on the exercise list is never
    guessed: the row fails with the closest names, and unknown_names groups each name once.

    The date: YYYY-MM-DD is that date. A timestamp with a non-zero offset keeps its own local date.
    One in UTC (Z or +00:00) is turned into the person's zone. One with no offset is refused. Then the
    usual check: never after today, at most two years back.

    The rule for every kind, judged against what the person held before the call:
      already there  the row matches an entry of that kind on that date: a live one, or a soft-deleted
                     one that was imported, so a row the person deleted never comes back. Or an
                     imported row of this person, on any date, live or deleted, holds the key this row
                     would be stored under: an edit keeps an imported row's key (entries.edit_entry),
                     so a row the person edited, moved or deleted is not written again, while the
                     person's time zone stays as it was (a UTC row can land on another date after a change)
      day taken      no match, and that date holds a live entry of that kind: never added to, never
                     replaced. The result names the entry.
      written        otherwise
    Weigh-ins and measurements allow one live row a day (the schema's own indexes), so for them a row
    written earlier in the call counts as held for the rows after it. For sessions, food and cardio
    each held entry matches one row only, and rows of the same call do not take each other's day, so
    two identical sessions in one call both go in, and both match when it runs again. For them every row of
    the call first takes the imported rows that hold its own key, and only then does the value match run
    for what is left, so a row the person moved onto the day of another row is matched by its own row.

    Order of work: weigh-ins, measurements, lift sessions, food, cardio; within a kind by date. So
    imported cardio gets its calorie estimate from body weight now, imported weigh-ins included.

    Whole-call refusals, nothing written: the write limit (the call is one write, a dry run too),
    time_zone_not_set, invalid_field (a field the call does not take, a dry_run
    that is neither true nor false), invalid_unit, rows_out_of_range, missing_unit, items_out_of_range (more than
    1,000 set items and food entries), and too_many_imported (more than 10,000 imported rows created in
    any 24 hours, counted over the five entry tables, deleted ones included).

    dry_run true or left out writes nothing (only true, false or left out are taken): it reads what the person holds and reports what a real run
    would do, the import limit included. The same rows sent with dry_run false then write exactly what it counted.

Every write here locks the user row first (common.lock_user), then checks the write limit
(common.check_write_limit), before any other read or work.
"""

import math
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app import db, formatting, links, metrics, rules, users
from app.models import IMPORTED, Bodyweight, CardioEntry, FoodEntry, LiftSession, LiftSet, Measurement
from app.services import body, cardio, common, food, lifts, measurements
from app.services.common import check_by, check_data, check_write_limit, exercises, lock_user, no_account
from app.services.entries import SESSION_NOTE

KIND = "import"
ROWS = (1, 200)
ITEMS_MAX = 1_000          # set items and food entries together, in one call
IMPORT_LIMIT = common.IMPORT_LIMIT          # imported rows created in any IMPORT_HOURS
IMPORT_HOURS = common.IMPORT_WINDOW_HOURS
LISTED = 50                # the most rows failed and day_taken each list
TIMES = (1, 20)
PROBLEMS_SHOWN = 3         # the most problems one row's message repeats

ORDER = ("bodyweight", "measurement", "lift_session", "food_entry", "cardio_entry")
MODELS = {"bodyweight": Bodyweight, "measurement": Measurement, "lift_session": LiftSession,
          "food_entry": FoodEntry, "cardio_entry": CardioEntry}
ROW_FIELDS = {
    "lift_session": ("sets", "notes"),
    "bodyweight": ("weight",),
    "measurement": ("measurement_kind", "value"),
    "food_entry": ("entries",),
    "cardio_entry": ("exercise", "minutes", "distance", "incline_pct", "speed", "avg_hr", "machine_kcal", "notes"),
}
SET_FIELDS = ("exercise", "weight", "reps", "times")
FOOD_FIELDS = ("description", "kcal", "meal")
CALL_FIELDS = ("rows", "dry_run", "weight_unit", "distance_unit", "length_unit")
UNITS = {"weight_unit": {"kg": 1.0, "lb": 0.45359237},
         "distance_unit": {"km": 1.0, "mi": 1.609344},
         "length_unit": {"cm": 1.0, "in": 2.54}}
BODY_SIZES = tuple(k for k, (unit, *_rest) in rules.MEASUREMENTS.items() if unit == "cm")
NOUNS = {"bodyweight": ("weigh-in", "weigh-ins"), "measurement": ("measurement", "measurements"),
         "lift_session": ("lift session", "lift sessions"), "food_entry": ("food entry", "food entries"),
         "cardio_entry": ("cardio session", "cardio sessions")}
DATE_NOUNS = {"bodyweight": "weigh-ins", "measurement": "measurements", "lift_session": "sessions",
              "food_entry": "meals", "cardio_entry": "cardio sessions"}

DATE_FIX = "Give each row its date: YYYY-MM-DD, or its timestamp with its offset."
UNIT_FIX = "Use kg or lb for weight_unit, km or mi for distance_unit, cm or in for length_unit."
MEAL_FIX = "Give meal on each entry: breakfast, lunch, dinner or snack, as the export names it."
CARDIO_ESTIMATE_NOTE = "Calorie estimates for imported cardio use body weight now, as every earlier-dated entry's does."
NO_WEIGH_IN_NOTE = "No weigh-in yet, so imported cardio has no calorie estimate."

_AT = re.compile(r"^(Set|Entry) (\d+): ")


# ---------------------------------------------------------------- small pieces

def _count(n: int) -> str:
    return formatting.show(n, "count")


def _n(n: int, one: str, many: str) -> str:
    """'1 row', '214 rows': the count through the formatter, then the word."""
    return f"{_count(n)} {one if n == 1 else many}"


def _at(n: int, message: str) -> str:
    """'Row 4: ' before a message, or 'Row 4, set 2: ' where the message names its set or entry."""
    m = _AT.match(message)
    if m:
        return f"Row {n}, {m.group(1).lower()} {m.group(2)}: {message[m.end():]}"
    return f"Row {n}: {message}"


def _renamed(err: dict, old: str, new: str, fix: bool = True) -> dict:
    """A reused check's refusal with its field named as the import names it ('weight', not 'weight_kg'): in
    the message, and in the fix unless fix is False (where the old word is a unit, not the field)."""
    word = re.compile(rf"(?<![\w.]){re.escape(old)}(?![\w])")
    return {**err, "message": word.sub(new, err["message"]), "fix": word.sub(new, err["fix"]) if fix else err["fix"]}


def _converted(value, factor: float):
    """A number in the call's unit, turned into the stored unit. Anything that is not a number, or a
    value already in the stored unit, goes on as sent, so the reused check refuses it in its own words."""
    number = rules.to_float(value)
    if number is None or factor == 1.0:
        return value
    return number * factor


UNIT_OF = {factor: unit for table in UNITS.values() for unit, factor in table.items() if factor != 1.0}


def _inward(limits, factor: float, quantity: str) -> tuple[str, str]:
    """A stored range in the call's unit, through the formatter at the quantity's step, each bound moved
    inward: the lower one rounded up, the upper one down. So a number refused for being outside the range is
    never inside the range its message quotes: 44.09 lb is outside 44.1 to 881.84 lb, not 44.09 to 881.85."""
    step = Decimal(repr(formatting.STEP[quantity]))
    shown = []
    for bound, rounding in zip(limits, (ROUND_CEILING, ROUND_FLOOR)):
        with localcontext() as ctx:
            ctx.prec = 50
            steps = (Decimal(repr(float(bound))) / Decimal(repr(factor)) / step).to_integral_value(rounding=rounding)
            shown.append(formatting.show(float(steps * step), quantity, unit=False))
    return shown[0], shown[1]


def _call_unit(err: dict, field: str, raw, factor: float, limits, quantity: str, where: str = "",
               fix_tail: str = "", per: str = "") -> dict:
    """A refusal of a number sent in the call's unit (lb, mi, in), said in that unit. A range refusal quotes
    the value as sent and the range. Every refusal of the field, "not a number" and "missing" too, gets a
    fix with the range in the call's unit, never the stored unit's. The range through the formatter, each
    bound moved inward (_inward). A refusal on a kg, km or cm call goes on as it is."""
    if factor == 1.0:
        return err
    unit = UNIT_OF[factor] + (f" {per}" if per else "")
    lo, hi = _inward(limits, factor, quantity)
    at = f"{where}: " if where else ""
    on = f" on {where.lower()}" if where else ""
    fix = f"Give {field}{on} from {lo} to {hi} {unit}{fix_tail}."
    if " is outside " not in err["message"]:
        return {**err, "fix": fix}
    return {**err, "message": f"{at}{field} {rules.as_sent(raw)} {unit} is outside {lo} to {hi} {unit}.", "fix": fix}


_SPEED_SENT = re.compile(r"^(speed_kmh \S+) does not match ")
# A speed out of range on a km call: "speed_kmh 70 is outside 0.1 to 60." lost its unit with the rename to speed.
_SPEED_RANGE = re.compile(r"^(speed_kmh \S+) is outside (\S+ to \S+)\.$")


def _speed_in_call_unit(err: dict, raw: dict, values: dict, factor: float) -> dict:
    """with_speed's two refusals on a mi call, in miles: otherwise they quote the converted distance and speed.
    On a km call the log function's words, with the speed sent given its unit, as on a mi call."""
    if factor == 1.0:
        return {**err, "message": _SPEED_SENT.sub(r"\1 km/h does not match ", err["message"], count=1)}
    worked = metrics.speed(values["distance_km"], values["minutes"])
    shown = formatting.show(worked / factor, "speed", unit=False)
    ran = f"{rules.as_sent(raw.get('distance'))} mi in {formatting.field(values['minutes'], 'session_time')} minutes"
    if values.get("speed_kmh") is not None and "does not match" in err["message"]:
        return {**err, "message": f"speed {rules.as_sent(raw.get('speed'))} mi an hour does not match {ran}, "
                                  f"which is {shown} mi an hour."}
    lo, hi = _inward(rules.NUMBERS["speed_kmh"][:2], factor, "speed")
    return {**err, "message": f"{ran} is {shown} mi an hour, outside {lo} to {hi} mi an hour."}


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _sent_name(name) -> str:
    """A missed name as unknown_names gives it: stripped text, or a value that is not text as a message quotes it."""
    return name.strip() if isinstance(name, str) else rules.quote(name)


def _name_key(name) -> str:
    return " ".join(name.split()).casefold() if isinstance(name, str) else ""


def _dry_run(value) -> tuple[bool | None, dict | None]:
    """True, false or left out (true). Anything else, text and 0 or 1 included, is refused: a write is never
    a guess."""
    if value is None or value is True:
        return True, None
    if value is False:
        return False, None
    return None, rules.error("invalid_field", f"dry_run '{rules.quote(value)}' is not true or false.",
                             "Send dry_run true to check the rows, or false to write them after the person has said yes.")


def _units(data: dict) -> tuple[dict, dict | None]:
    """The units the call named, each as the field's own key. A unit left out is not in it: never assumed."""
    chosen = {}
    for field, table in UNITS.items():
        raw = data.get(field)
        if _blank(raw):
            continue
        key = raw.strip().casefold() if isinstance(raw, str) else None
        if key not in table:
            return {}, rules.error("invalid_unit", f"{field} '{rules.quote(raw)}' is not a unit Spotter takes.",
                                   UNIT_FIX)
        chosen[field] = key
    return chosen, None


# What each unit measures, in a refusal: "2 rows hold a weight."
UNIT_HELD = {"weight_unit": "a weight", "distance_unit": "a distance or a speed", "length_unit": "a body size"}


def _held_quantity(row) -> set[str]:
    """The units this row needs, from the row as sent: a weigh-in's or a set's weight, a cardio entry's distance
    or speed, a body size's value. A field left out or blank holds nothing. A row that is not what its kind
    takes needs nothing here: its own checks refuse it as before."""
    if not isinstance(row, dict):
        return set()
    kind, needs = row.get("kind"), set()
    if kind == "bodyweight" and not _blank(row.get("weight")):
        needs.add("weight_unit")
    elif kind == "lift_session" and isinstance(row.get("sets"), list):
        if any(isinstance(x, dict) and not _blank(x.get("weight")) for x in row["sets"]):
            needs.add("weight_unit")
    elif kind == "cardio_entry" and any(not _blank(row.get(f)) for f in ("distance", "speed")):
        needs.add("distance_unit")
    elif kind == "measurement" and not _blank(row.get("value")):
        measured, _ = rules.match_kind(row.get("measurement_kind"))
        if measured in BODY_SIZES:
            needs.add("length_unit")
    return needs


def _missing_units(rows: list, units: dict) -> dict | None:
    """missing_unit when the rows hold a quantity whose unit the call did not name: one refusal for every such
    unit, each with how many rows hold it."""
    held = {field: 0 for field in UNITS}
    for row in rows:
        for field in _held_quantity(row):
            held[field] += 1
    missing = [field for field in UNITS if held[field] and field not in units]
    if not missing:
        return None
    said = " ".join(f"{field} is missing, and {_n(held[field], 'row holds', 'rows hold')} {UNIT_HELD[field]}."
                    for field in missing)
    if len(missing) == 1:
        fix = f"Send {missing[0]} as the export writes it: {' or '.join(UNITS[missing[0]])}."
    else:
        fix = (f"Send {lifts._joined(missing)} as the export writes them: "
               + ", ".join(f"{field} {' or '.join(UNITS[field])}" for field in missing) + ".")
    fix += " If the export does not say which unit, ask the person. Spotter never assumes a unit."
    return rules.error("missing_unit", said, fix, missing_units=missing)


# ---------------------------------------------------------------- the date rule

def _date(raw, user, today: date, noun: str) -> tuple[date | None, dict | None]:
    """The stored date for a row's date: see the module notes."""
    if _blank(raw):
        return None, rules.error("missing_field", "date is missing.", DATE_FIX)
    if not isinstance(raw, str):  # a number such as 20261109 is not a date, whatever it reads as
        return None, rules.error("invalid_date", f"date '{rules.quote(raw)}' is not a date.", DATE_FIX)
    text = raw.strip()
    on = None
    if text:
        try:
            on = date.fromisoformat(text)
        except ValueError:
            try:
                moment = datetime.fromisoformat(text)
            except ValueError:
                moment = None
            if moment is not None:
                offset = moment.utcoffset()
                if offset is None:
                    return None, rules.error("timestamp_without_offset", f"'{rules.quote(text)}' has no offset.",
                                             "Send the timestamp with its offset, such as +05:30, or the date alone "
                                             "when it is the person's local date.")
                try:
                    on = moment.astimezone(users.zone_for(user.time_zone)).date() if offset == timedelta(0) \
                        else moment.date()
                except OverflowError:  # near year 1 or 9999 there is no local date: its own date is refused below
                    on = moment.date()
    if on is None:
        _, err = rules.entry_date(text, today, noun)
        return None, {**err, "fix": DATE_FIX}
    return rules.entry_date(on, today, noun)


# ---------------------------------------------------------------- the row checks

def _unknown_fields(raw: dict, allowed: tuple, what: str, takes: str, where: str = "") -> list[dict]:
    at = f"{where}: " if where else ""
    return [rules.error("invalid_field", f"{at}'{rules.quote(k)}' is not a field of {what}.", f"{takes}.")
            for k in raw if k not in allowed]


def _check_lift(raw: dict, on, today, by_name: dict, factor: float) -> tuple[dict | None, list[dict], list]:
    """(checked, errors, missed names): a session through rules.check_session, set by set as sent, then
    times counted out. Weights are converted first."""
    errors = []
    items = raw.get("sets")
    sent = items if isinstance(items, list) else []
    converted, times, missed = [], [], []
    names = list(by_name)
    lo, hi = rules.SESSION_SETS
    if len(sent) > hi:  # as log_lifts refuses it: never a session cut short
        return None, [rules.error("sets_count_out_of_range", f"The session has {_count(len(sent))} set items. It needs "
                                  f"{lo} to {hi}.", f"Send {lo} to {hi} items a session: one a set, or one with times "
                                  "for sets that repeat.")], missed
    for k, item in enumerate(sent, start=1):
        item = item if isinstance(item, dict) else {}
        where = f"Set {k}"
        errors += _unknown_fields(item, SET_FIELDS, "a set", "A set takes: exercise, weight, reps, times", where)
        count = 1
        if not _blank(item.get("times")):
            count, err = rules.number(item.get("times"), int, "times", where)
            if err is None and not TIMES[0] <= count <= TIMES[1]:
                err = rules.error("invalid_field", f"{where}: times {rules.as_sent(item.get('times'))} is outside "
                                  f"{TIMES[0]} to {TIMES[1]}.", f"Give times on set {k} from {TIMES[0]} to {TIMES[1]}.")
            if err:
                errors.append(err)
                count = 1
        times.append(count)
        converted.append({"exercise": item.get("exercise"), "weight_kg": _converted(item.get("weight"), factor),
                          "reps": item.get("reps")})
    if not isinstance(items, list):
        converted = items  # check_session refuses it in its own words
    _, sets, err = rules.check_session(on or today, converted, names, today)
    if err:
        for e in err["errors"]:
            at = _AT.match(e["message"])
            if e["error"] == "weight_out_of_range" and at:
                k = int(at.group(2))
                e = _call_unit(e, "weight", sent[k - 1].get("weight"), factor, rules.HARD_RULES["weight_kg"],
                               "weight_kg", f"Set {k}", ", 0 for bodyweight")
            errors.append(_renamed(e, "weight_kg", "weight"))
        for e in err["errors"]:  # the set a missed name came from, by the number its message names
            at = _AT.match(e["message"]) if e["error"] == "unknown_exercise" else None
            if at:
                missed.append(_sent_name(converted[int(at.group(2)) - 1]["exercise"]))
    notes, err = rules.text(raw.get("notes"), "notes")
    errors += [err] if err else []
    if errors:
        return None, errors, missed
    expanded = [x for x, count in zip(sets, times) for _ in range(count)]
    if len(expanded) > hi:
        return None, [rules.error("sets_count_out_of_range",
                                  f"The session has {_count(len(expanded))} sets once times is counted. It needs {lo} "
                                  f"to {hi}.", f"Send at most {hi} sets a session, times counted.")], missed
    keyed = lifts.number_sets(expanded, by_name)
    return {"sets": expanded, "notes": notes, "keyed": keyed,
            "match": tuple(sorted((ex, n, round(w, 2), r) for ex, n, w, r in keyed))}, [], missed


def _check_bodyweight(raw: dict, factor: float) -> tuple[dict | None, list[dict]]:
    if _blank(raw.get("weight")):
        lo, hi = rules.BODYWEIGHT_KG
        return None, [rules.error("missing_field", "weight is missing.",
                                  f"Give the weight in weight_unit: {lo} to {hi} kg once converted.")]
    kg, err = body.check_kg(_converted(raw.get("weight"), factor))
    if err:
        err = _renamed(err, "kg", "weight", fix=False) if err["message"].startswith("kg ") else err
        return None, [_call_unit(err, "weight", raw.get("weight"), factor, rules.BODYWEIGHT_KG, "weigh_in")]
    return {"kg": kg, "match": kg}, []


def _check_measurement(raw: dict, factor: float) -> tuple[dict | None, list[dict]]:
    if _blank(raw.get("measurement_kind")):
        return None, [rules.error("missing_field", "measurement_kind is missing.",
                                  f"Use one of: {', '.join(rules.MEASUREMENTS)}.")]
    kind, err = rules.match_kind(raw.get("measurement_kind"))
    if err:
        return None, [err]
    value = raw.get("value")
    if kind in BODY_SIZES:
        value = _converted(value, factor)
    value, err = rules.measurement_value(kind, value)
    if err:
        if kind in BODY_SIZES:
            err = _call_unit(err, kind, raw.get("value"), factor, rules.MEASUREMENTS[kind][1:3], "cm")
        return None, [err]
    return {"measurement_kind": kind, "value": value, "unit": rules.MEASUREMENTS[kind][0],
            "match": (kind, float(value))}, []


def _check_food(s, user_id: int, raw: dict) -> tuple[dict | None, list[dict]]:
    """Each entry through food._check_entry, as log_food checks it, with a meal required and no ids:
    the other app's own number, stored as you_said."""
    items = raw.get("entries")
    lo, hi = food.ENTRIES
    if not isinstance(items, list) or not lo <= len(items) <= hi:
        count = len(items) if isinstance(items, list) else 0
        return None, [rules.error("invalid_field", f"entries holds {_count(count)}. It needs {lo} to {hi}.",
                                  "Send entries as a list: one object per food, each with description, kcal and meal.")]
    errors, checked = [], []
    for k, item in enumerate(items, start=1):
        where = f"Entry {k}"
        if not isinstance(item, dict):
            errors.append(rules.error("invalid_field", f"{where}: an entry must be an object of named fields.",
                                      "Send each entry with description, kcal and meal."))
            continue
        bad = _unknown_fields(item, FOOD_FIELDS, "an imported food entry",
                              "An imported food entry takes: description, kcal, meal", where)
        if bad:
            errors += bad
            continue
        if _blank(item.get("meal")):
            errors.append(rules.error("missing_field", f"{where}: meal is missing.", MEAL_FIX))
            continue
        meal, err = rules.match_meal(item.get("meal"), where)
        if err:
            errors.append({**err, "fix": MEAL_FIX})
            continue
        entry, errs = food._check_entry(s, user_id, {**item, "meal": meal}, where, meal, {})
        errors += errs
        if entry is not None:
            checked.append(entry)
    if errors:
        return None, errors
    return {"entries": checked, "match": [(e["meal"], e["description"].casefold(), int(e["kcal"])) for e in checked]}, []


def _check_cardio(s, raw: dict, factor: float, activities: dict) -> tuple[dict | None, list[dict], list]:
    """cardio.match_cardio, check_numbers and with_speed, as log_cardio runs them, distance converted."""
    errors, missed = [], []
    name = raw.get("exercise")
    key = (type(name).__name__, name if isinstance(name, str) else repr(name))  # as sent: the refusal echoes it
    if key not in activities:
        activities[key] = cardio.match_cardio(s, name)
    exercise, err = activities[key]
    if err:
        errors.append(err)
        if err["error"] == "unknown_exercise":
            missed.append(_sent_name(name))
    data = {f: raw.get(f) for f in cardio.NUMBER_FIELDS if f not in ("distance_km", "speed_kmh")}
    data["distance_km"] = _converted(raw.get("distance"), factor)
    data["speed_kmh"] = _converted(raw.get("speed"), factor)
    values, errs = cardio.check_numbers(data)
    for e in errs:
        if e["message"].startswith("distance_km "):
            e = _call_unit(e, "distance", raw.get("distance"), factor, rules.NUMBERS["distance_km"][:2], "km")
        elif e["message"].startswith("speed_kmh ") and factor == 1.0:  # the log function's words, with the unit
            e = {**e, "message": _SPEED_RANGE.sub(r"\1 km/h is outside \2 km/h.", e["message"], count=1)}
        elif e["message"].startswith("speed_kmh "):
            e = _call_unit(e, "speed", raw.get("speed"), factor, rules.NUMBERS["speed_kmh"][:2], "speed",
                           per="an hour")
        errors.append(_renamed(_renamed(e, "distance_km", "distance"), "speed_kmh", "speed"))
    notes, err = rules.text(raw.get("notes"), "notes")
    errors += [err] if err else []
    if not errors:
        values, err = cardio.with_speed(values)
        if err:
            errors.append(_renamed(_speed_in_call_unit(err, raw, values, factor), "speed_kmh", "speed"))
    if errors:
        return None, errors, missed
    given = {f: values[f] if not _blank(data.get(f)) else None for f in cardio.NUMBER_FIELDS}
    km = values["distance_km"]
    return {"exercise": exercise, "values": values, "given": given, "notes": notes,
            "match": (exercise.id, round(values["minutes"], 1), None if km is None else round(km, 2))}, [], missed


def _failure(n: int, kind, on, raw_date, errors: list[dict]) -> dict:
    """One failed row: its number, kind and date, the first problem's code, every problem's message up to
    PROBLEMS_SHOWN, and the closest names where a name missed."""
    if len(errors) == 1:
        message = _at(n, errors[0]["message"])
    else:
        shown = " ".join(e["message"] for e in errors[:PROBLEMS_SHOWN])
        more = len(errors) - PROBLEMS_SHOWN
        message = f"Row {n}: {_count(len(errors))} problems. {shown}" + (f" And {_count(more)} more." if more > 0 else "")
    out = {"row": n, "kind": kind if kind in ORDER else (rules.quote(kind) if isinstance(kind, str) else None),
           "date": on.isoformat() if on else (rules.quote(raw_date) if isinstance(raw_date, str) else None),
           "error": errors[0]["error"], "message": message,
           "fix": " ".join(dict.fromkeys(e["fix"] for e in errors[:PROBLEMS_SHOWN]))}
    closest = next((e.get("closest") for e in errors if e["error"] == "unknown_exercise"), None)
    if closest is not None:
        out["closest"] = closest
    return out


# ---------------------------------------------------------------- what the person already holds

class _Held:
    """One kind's entries on the call's dates (live ones and soft-deleted imported ones), and this person's
    imported rows on any date that hold one of the call's keys. Each with its match value, and for an
    imported row the key it was stored under. live_days: (date, part) to a live entry, for "day taken"."""

    def __init__(self, kind: str):
        self.kind = kind
        self.entries: list[dict] = []
        self.ids: set[int] = set()
        self.live_days: dict[tuple, dict] = {}

    def add(self, on: date, part, match, entry_id, live: bool, by_row: int | None = None, key: str | None = None) -> dict:
        held = {"date": on, "part": part, "match": match, "id": entry_id, "used": False, "by_row": by_row, "key": key}
        self.entries.append(held)
        if entry_id is not None:
            self.ids.add(entry_id)
        if live and (on, part) not in self.live_days:
            self.live_days[(on, part)] = held
        return held

    def find_key(self, key: str, consume: bool) -> dict | None:
        """An imported row stored under this key, wherever the person has since moved, changed or deleted it."""
        for held in self.entries:
            if held["key"] == key and not (consume and held["used"]):
                if consume:
                    held["used"] = True
                return held
        return None

    def find(self, on: date, part, match, consume: bool) -> dict | None:
        for held in self.entries:
            if held["date"] == on and held["part"] == part and held["match"] == match and not (consume and held["used"]):
                if consume:
                    held["used"] = True
                return held
        return None


def _held(s, user_id: int, kind: str, dates: set, keys: set) -> _Held:
    model = MODELS[kind]
    out = _Held(kind)
    if not dates:
        return out
    on_dates = select(model).where(model.user_id == user_id, model.date.in_(dates),
                                   or_(model.deleted_at.is_(None), model.logged_by == IMPORTED))
    by_key = select(model).where(model.user_id == user_id, model.logged_by == IMPORTED, model.dedupe_key.in_(keys))
    for query in (on_dates, by_key):
        if kind == "lift_session":
            query = query.options(selectinload(LiftSession.sets))
        for row in s.scalars(query.order_by(model.id)):
            if row.id in out.ids:
                continue
            live, key = row.deleted_at is None, row.dedupe_key if row.logged_by == IMPORTED else None
            if kind == "bodyweight":
                out.add(row.date, None, round(row.kg, 2), row.id, live, key=key)
            elif kind == "measurement":
                out.add(row.date, row.kind, (row.kind, float(row.value)), row.id, live, key=key)
            elif kind == "lift_session":
                match = tuple(sorted((x.exercise_id, x.set_number, round(x.weight_kg, 2), x.reps) for x in row.sets))
                out.add(row.date, None, match, row.id, live, key=key)
            elif kind == "food_entry":
                out.add(row.date, None, (row.meal, row.description.casefold(), int(row.kcal)), row.id, live, key=key)
            else:
                km = None if row.distance_km is None else round(row.distance_km, 2)
                out.add(row.date, row.exercise_id, (row.exercise_id, round(row.minutes, 1), km), row.id, live, key=key)
    return out


def _row_key(r: dict) -> str:
    """The key this row is stored under when written whole: its own kind's (lifts.session_key, body.weigh_in_key,
    measurements.measurement_key, food.entry_key over the row's entries, cardio.cardio_key)."""
    kind, on = r["kind"], r["date"]
    if kind == "bodyweight":
        return body.weigh_in_key(on, r["kg"])
    if kind == "measurement":
        return measurements.measurement_key(on, r["measurement_kind"], r["value"])
    if kind == "lift_session":
        return lifts.session_key(on, r["notes"], r["keyed"])
    if kind == "food_entry":
        return food.entry_key(on, [food.entry_tuple(e) for e in r["entries"]])
    return cardio.cardio_key(on, r["exercise"].id, r["given"])


def _imported_since(s, user_id: int, since: datetime) -> list[datetime]:
    """When each imported row of this person created since then was made, oldest first, over the five
    entry tables, deleted ones included."""
    times = []
    for model in MODELS.values():
        times += s.scalars(select(model.created_at).where(model.user_id == user_id, model.logged_by == IMPORTED,
                                                          model.created_at >= since))
    return sorted(times)


# ---------------------------------------------------------------- the import

def import_entries(user_id: int, data: dict | None, by: str) -> dict:
    """Bring rows over from another app. Returns {kind: "import", dry_run, rows, from, to, units, counts,
    row_outcomes, ranges, day_taken, failed, unknown_names, more_failed, more_day_taken,
    imported_last_24h, import_limit, within_import_limit, summary, links[, note]} or {error, message,
    fix}. See the module notes."""
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
        refused = _refused_whole(s, user, data)
        if refused:
            return refused
        dry, _ = _dry_run(data.get("dry_run"))
        units, _ = _units(data)
        return _run(s, user, data["rows"], dry, units)


def _refused_whole(s, user, data: dict) -> dict | None:
    if user.time_zone is None:
        return rules.error("time_zone_not_set",
                           "The person's time zone is not set, so today has no date to check against.",
                           "Set it with update_settings first: use the person's time zone if you know it, otherwise "
                           "ask once which city they are in. Then send the import again.")
    unknown = [k for k in data if k not in CALL_FIELDS]
    if unknown:
        return rules.error("invalid_field", f"'{rules.quote(unknown[0])}' is not a field of an import.",
                           f"Use: {', '.join(CALL_FIELDS)}.")
    _, err = _dry_run(data.get("dry_run"))
    if err:
        return err
    units, err = _units(data)
    if err:
        return err
    rows = data.get("rows")
    lo, hi = ROWS
    if not isinstance(rows, list) or not lo <= len(rows) <= hi:
        count = len(rows) if isinstance(rows, list) else 0
        return rules.error("rows_out_of_range", f"rows holds {_count(count)}. It needs {lo} to {hi}.",
                           f"Send at most {hi} rows a call, oldest first, and the rest in the next call.")
    err = _missing_units(rows, units)  # from the rows as sent, before any row is checked
    if err:
        return err
    items = sum(len(r.get(field)) for r in rows if isinstance(r, dict)
                for field in ("sets", "entries") if isinstance(r.get(field), list))
    if items > ITEMS_MAX:
        return rules.error("items_out_of_range",
                           f"These rows hold {_count(items)} sets and food entries. The most in one call is "
                           f"{_count(ITEMS_MAX)}.", "Send fewer rows in this call, and the rest in the next.")
    return None


def _check_rows(s, user, rows: list, units: dict, today: date) -> tuple[list[dict], list[dict], dict]:
    """(checked rows, failed rows, unknown names) for every row as sent."""
    lift_names = {e.name: e for e in exercises(s, "lift")}
    activities: dict[str, tuple] = {}
    # A unit the call left out is needed by no row it holds (_missing_units), so its factor is never applied
    # to a number: 1, the stored unit, for a malformed row that its own checks refuse.
    factors = {f: UNITS[f][units[f]] if f in units else 1.0 for f in UNITS}
    checked, failed, missed_names = [], [], {}
    for n, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            failed.append(_failure(n, None, None, None, [rules.error(
                "invalid_field", "a row must be an object of named fields.",
                "Send each row with kind and date, and the fields of its kind.")]))
            continue
        kind = raw.get("kind")
        if _blank(kind):
            failed.append(_failure(n, None, None, raw.get("date"), [rules.error(
                "missing_field", "kind is missing.", f"Use one of: {', '.join(ORDER)}.")]))
            continue
        if kind not in ORDER:
            failed.append(_failure(n, kind, None, raw.get("date"), [rules.error(
                "unknown_kind", f"'{rules.quote(kind)}' is not a kind of row.", f"Use one of: {', '.join(ORDER)}.")]))
            continue
        fields = ROW_FIELDS[kind]
        errors = _unknown_fields(raw, ("kind", "date", *fields), f"a {kind} row", f"A {kind} row takes: date, "
                                 + ", ".join(fields))
        on, err = _date(raw.get("date"), user, today, DATE_NOUNS[kind])
        errors += [err] if err else []
        missed = []
        if kind == "lift_session":
            row, errs, missed = _check_lift(raw, on, today, lift_names, factors["weight_unit"])
            missed = [(name, "lift") for name in missed]
        elif kind == "bodyweight":
            row, errs = _check_bodyweight(raw, factors["weight_unit"])
        elif kind == "measurement":
            row, errs = _check_measurement(raw, factors["length_unit"])
        elif kind == "food_entry":
            row, errs = _check_food(s, user.id, raw)
        else:
            row, errs, missed = _check_cardio(s, raw, factors["distance_unit"], activities)
            missed = [(name, "cardio") for name in missed]
        errors += errs
        once = {(_name_key(name), where): name for name, where in missed}
        for (key, where), name in once.items():
            group = missed_names.setdefault((key, where), {"name": rules.quote(name), "kind": where,
                                                                        "rows": 0, "closest": None})
            group["rows"] += 1
            if group["closest"] is None:
                pool = list(lift_names) if where == "lift" else [e.name for e in exercises(s, "cardio")]
                group["closest"] = rules.match_exercise(name, pool)[1]
        if errors:
            failed.append({**_failure(n, kind, on, raw.get("date"), errors), "on": on,
                           "entries": len(raw["entries"]) if kind == "food_entry" and isinstance(raw.get("entries"), list)
                           and raw["entries"] else 1})
            continue
        checked.append({**row, "n": n, "kind": kind, "date": on})
    return checked, failed, missed_names


def _decide(s, user, checked: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(rows to write, day_taken rows, every checked row). Each checked row gets "outcome",
    and a food row its "matched" and "unmatched" entries."""
    for r in checked:
        r["key"] = _row_key(r)
    dates = {kind: {r["date"] for r in checked if r["kind"] == kind} for kind in ORDER}
    keys = {kind: {r["key"] for r in checked if r["kind"] == kind} for kind in ORDER}
    held = {kind: _held(s, user.id, kind, dates[kind], keys[kind]) for kind in ORDER}
    pre_live = {kind: dict(h.live_days) for kind, h in held.items()}  # before the call: sessions, food, cardio
    in_order = sorted(checked, key=lambda r: (ORDER.index(r["kind"]), r["date"], r["n"]))
    # Sessions, food and cardio: every row of the call takes the imported rows that hold its own key first,
    # each holder once, and only then does the value match run for what is left. A holder is the import of a
    # row of the same kind, date and content (the key covers them all), so a key match takes no other row's
    # entry. A value match first could take, on its own date, a row the person moved there from another
    # day, and leave that day's row with nothing to match: it was written again.
    for r in in_order:
        if r["kind"] in ("bodyweight", "measurement"):
            continue
        h = held[r["kind"]]
        if r["kind"] == "food_entry":
            taken = 0
            while taken < len(r["entries"]) and h.find_key(r["key"], consume=True):  # moved or changed too
                taken += 1
            r["by_key"] = taken
        else:
            r["by_key"] = h.find_key(r["key"], consume=True) is not None
    to_write, day_taken = [], []
    for r in in_order:
        kind, on, h = r["kind"], r["date"], held[r["kind"]]
        if kind in ("bodyweight", "measurement"):
            part = r.get("measurement_kind")
            if h.find(on, part, r["match"], consume=False) or h.find_key(r["key"], consume=False):
                r["outcome"] = "already_there"
                continue
            taker = h.live_days.get((on, part))
            if taker is not None:
                r["outcome"], r["taker"] = "day_taken", taker
                day_taken.append(r)
                continue
            r["outcome"] = "written"
            r["held"] = h.add(on, part, r["match"], None, True, by_row=r["n"])
            to_write.append(r)
            continue
        part = r["exercise"].id if kind == "cardio_entry" else None
        if kind == "food_entry":
            k = r["by_key"]
            r["matched"], r["unmatched"] = list(r["entries"][:k]), []
            for entry, match in zip(r["entries"][k:], r["match"][k:]):
                r["matched" if h.find(on, None, match, consume=True) else "unmatched"].append(entry)
            if not r["unmatched"]:
                r["outcome"] = "already_there"
                continue
        elif r["by_key"] or h.find(on, part, r["match"], consume=True):
            r["outcome"] = "already_there"
            continue
        taker = pre_live[kind].get((on, part))
        if taker is not None:
            r["outcome"], r["taker"] = "day_taken", taker
            day_taken.append(r)
            continue
        r["outcome"] = "written"
        to_write.append(r)
    return to_write, day_taken, checked


def _written_count(r: dict) -> int:
    return len(r["unmatched"]) if r["kind"] == "food_entry" else 1


def _write(s, user, to_write: list[dict], today: date) -> dict:
    """The rows, written in the order of work: weigh-ins first, so cardio's estimate sees them. Returns
    {"cardio_estimated": bool | None} (None when no cardio was written)."""
    by_kind = {kind: [r for r in to_write if r["kind"] == kind] for kind in ORDER}
    for r in by_kind["bodyweight"]:
        r["row"] = Bodyweight(user_id=user.id, date=r["date"], kg=r["kg"], logged_by=IMPORTED,
                              dedupe_key=body.weigh_in_key(r["date"], r["kg"]))
    for r in by_kind["measurement"]:
        r["row"] = Measurement(user_id=user.id, date=r["date"], kind=r["measurement_kind"], value=r["value"],
                               unit=r["unit"], logged_by=IMPORTED,
                               dedupe_key=measurements.measurement_key(r["date"], r["measurement_kind"], r["value"]))
    for r in by_kind["lift_session"]:
        session = LiftSession(user_id=user.id, date=r["date"], notes=r["notes"], logged_by=IMPORTED,
                              dedupe_key=lifts.session_key(r["date"], r["notes"], r["keyed"]))
        session.sets = [LiftSet(exercise_id=ex, set_number=n, weight_kg=w, reps=reps) for ex, n, w, reps in r["keyed"]]
        r["row"] = session
    for r in by_kind["food_entry"]:
        key = food.entry_key(r["date"], [food.entry_tuple(e) for e in r["unmatched"]])
        r["rows"] = [FoodEntry(user_id=user.id, date=r["date"], meal=e["meal"], description=e["description"],
                               kcal=e["kcal"], source=e["source"], estimated=e["estimated"], saved_food_id=None,
                               servings=None, usda_fdc_id=None, grams=None, logged_by=IMPORTED, dedupe_key=key)
                     for e in r["unmatched"]]
    s.add_all(r["row"] for kind in ("bodyweight", "measurement") for r in by_kind[kind])
    s.flush()  # weigh-ins in place before the estimate reads body weight now
    s.add_all([r["row"] for r in by_kind["lift_session"]] + [x for r in by_kind["food_entry"] for x in r["rows"]])
    estimated = None
    if by_kind["cardio_entry"]:
        kg = metrics.bodyweight_now([(b.date, b.kg) for b in body.weigh_ins(s, user.id, today)], today)
        estimated = kg is not None
        for r in by_kind["cardio_entry"]:
            ex, values = r["exercise"], r["values"]
            # cardio.estimate's own sum, with body weight now read once for the call, not once a row
            est = None if kg is None or ex.met is None else {
                "kcal": metrics.cardio_kcal(ex.met, kg, values["minutes"]), "weight_kg": kg}
            r["row"] = CardioEntry(user_id=user.id, date=r["date"], exercise_id=ex.id, notes=r["notes"] or "",
                                   logged_by=IMPORTED, dedupe_key=cardio.cardio_key(r["date"], ex.id, r["given"]),
                                   estimated_kcal=est["kcal"] if est else None,
                                   estimate_weight_kg=est["weight_kg"] if est else None,
                                   **{f: values[f] for f in cardio.NUMBER_FIELDS})
            s.add(r["row"])
    s.flush()
    for r in to_write:
        if "held" in r:  # a weigh-in or measurement of this call that holds its day for the rows after it
            r["held"]["id"] = r["row"].id
    return {"cardio_estimated": estimated}


def _wait_hours(times: list[datetime], over: int, now: datetime) -> int:
    """Hours until enough of the counted rows leave the window for this call to fit, at least 1."""
    leaves = times[min(over, len(times)) - 1] + timedelta(hours=IMPORT_HOURS) - now
    return max(1, math.ceil(leaves.total_seconds() / 3600))


def _run(s, user, rows: list, dry: bool, units: dict) -> dict:
    today = users.today_for(user)
    now = db.utcnow()
    checked, failed, missed = _check_rows(s, user, rows, units, today)
    to_write, day_taken, decided = _decide(s, user, checked)
    writing = sum(_written_count(r) for r in to_write)
    times = _imported_since(s, user.id, now - timedelta(hours=IMPORT_HOURS))
    within = len(times) + writing <= IMPORT_LIMIT
    if not dry and not within:
        over = len(times) + writing - IMPORT_LIMIT
        wait = _wait_hours(times, over, now)
        return rules.error("too_many_imported",
                           f"{_count(len(times))} rows were imported in the last {IMPORT_HOURS} hours. This call "
                           f"would take it past {_count(IMPORT_LIMIT)}. Nothing was saved.",
                           f"Wait {_count(wait)} hour{'s' if wait != 1 else ''}, then send it again.", wait_hours=wait)
    cardio_estimated = None
    if not dry and to_write:
        cardio_estimated = _write(s, user, to_write, today)["cardio_estimated"]
    elif any(r["kind"] == "cardio_entry" for r in to_write):  # a preview: a weigh-in held or to be written
        cardio_estimated = any(r["kind"] == "bodyweight" for r in to_write) or metrics.bodyweight_now(
            [(b.date, b.kg) for b in body.weigh_ins(s, user.id, today)], today) is not None
    return _result(user, rows, dry, units, decided, failed, day_taken, missed, to_write, len(times), within,
                   cardio_estimated, today)


def _ranges(decided: list[dict]) -> dict:
    def span(values):
        return {"lowest": min(values), "highest": max(values)} if values else None
    weights = [r["kg"] for r in decided if r["kind"] == "bodyweight"]
    sizes = [r["value"] for r in decided if r["kind"] == "measurement" and r["measurement_kind"] in BODY_SIZES]
    sets = [x["weight_kg"] for r in decided if r["kind"] == "lift_session" for x in r["sets"]]
    km = [r["values"]["distance_km"] for r in decided if r["kind"] == "cardio_entry"
          if r["values"]["distance_km"] is not None]
    return {"bodyweight_kg": span(weights), "body_size_cm": span(sizes),
            "heaviest_set_kg": max(sets) if sets else None, "longest_km": max(km) if km else None}


def _counts(decided: list[dict], failed: list[dict]) -> dict:
    counts = {kind: {"written": 0, "already_there": 0, "day_taken": 0, "failed": 0} for kind in ORDER}
    for r in decided:
        c = counts[r["kind"]]
        if r["kind"] == "food_entry":
            gone = r["unmatched"] if r["outcome"] in ("written", "day_taken") else []
            c["already_there"] += len(r["matched"])
            c[r["outcome"]] += len(gone)
        else:
            c[r["outcome"]] += 1
    for f in failed:
        if f["kind"] in counts:
            counts[f["kind"]]["failed"] += f["entries"] if f["kind"] == "food_entry" else 1
    return counts


def _result(user, rows, dry, units, decided, failed, day_taken, missed, to_write, imported, within,
            cardio_estimated, today) -> dict:
    outcomes = {"written": 0, "already_there": 0, "day_taken": 0, "failed": len(failed)}
    for r in decided:
        outcomes[r["outcome"]] += 1
    counts = _counts(decided, failed)
    ranges = _ranges(decided)
    seen, repeated = set(), 0
    for r in sorted(decided, key=lambda r: r["n"]):
        same = (r["kind"], r["date"], tuple(r["match"]) if isinstance(r["match"], list) else r["match"])
        repeated += same in seen
        seen.add(same)
    # Matched entries of food rows not already there whole: a written row, or a day-taken one, as counts has them.
    entries_there = sum(len(r["matched"]) for r in decided
                        if r["kind"] == "food_entry" and r["outcome"] in ("written", "day_taken"))
    dated = [r["date"] for r in decided] + [f["on"] for f in failed if f.get("on")]  # dates that passed
    start, end = (min(dated), max(dated)) if dated else (None, None)
    taken = [{"row": r["n"], "kind": r["kind"], "date": r["date"].isoformat(),
              "entry_id": _taker_id(r["taker"]), **({"by_row": r["taker"]["by_row"]} if r["taker"]["by_row"] else {})}
             for r in sorted(day_taken, key=lambda r: r["n"])]
    out = {
        "kind": KIND, "dry_run": dry, "rows": len(rows),
        "from": start.isoformat() if start else None, "to": end.isoformat() if end else None,
        "units": {name: units[field] for name, field in (("weight", "weight_unit"), ("distance", "distance_unit"),
                                                          ("length", "length_unit")) if field in units},
        "counts": counts, "row_outcomes": outcomes, "repeated_rows": repeated, "ranges": ranges,
        "day_taken": taken[:LISTED],
        "failed": [{k: v for k, v in f.items() if k not in ("entries", "on")} for f in failed[:LISTED]],
        "unknown_names": list(missed.values()),
        "more_failed": max(0, len(failed) - LISTED), "more_day_taken": max(0, len(taken) - LISTED),
        "imported_last_24h": imported, "import_limit": IMPORT_LIMIT, "within_import_limit": within,
        "summary": _summary(dry, len(rows), outcomes, counts, ranges, start, end, today, repeated, entries_there),
        "links": {"progress": links.page("progress"), **({"day": links.page("day", date=end)} if end else {})},
    }
    note = _note(dry, to_write, cardio_estimated, within, imported, out)
    if note:
        out["note"] = note
    return out


def _taker_id(taker: dict) -> int | None:
    """The id of the entry that holds the day: a held one's, or a row of this call's once written. None for a
    row of this call in a preview: by_row names it."""
    return taker["id"]


def _span(start, end, today) -> str:
    if start is None:
        return ""
    if start == end:
        return f" on {formatting.day(start, today)}"
    return f" from {formatting.days(start, end, today)}"


def _listing(counts: dict) -> str:
    return lifts._joined([_n(counts[k]["written"], *NOUNS[k]) for k in ORDER if counts[k]["written"]])


def _summary(dry: bool, total: int, outcomes: dict, counts: dict, ranges: dict, start, end, today,
             repeated: int = 0, entries_there: int = 0) -> str:
    """The preview, written and run-again sentences: see the module notes."""
    span = _span(start, end, today)
    there, taken, refused = outcomes["already_there"], outcomes["day_taken"], outcomes["failed"]
    listing = _listing(counts)
    if dry:
        parts = ["Preview, nothing written.", f"{_n(total, 'row', 'rows')}{span}.",
                 f"Would write {listing}." if listing else "Would write nothing."]
        if entries_there:
            parts.append(f"{_n(entries_there, 'food entry', 'food entries')} in those rows "
                         f"{'is' if entries_there == 1 else 'are'} already there.")
        if repeated:
            parts.append(f"{_n(repeated, 'row repeats', 'rows repeat')} an earlier row of this call.")
        if there:
            parts.append(f"{_n(there, 'row is', 'rows are')} already there.")
        if taken:
            parts.append(f"{_n(taken, 'row falls', 'rows fall')} on days Spotter already holds.")
        if refused:
            parts.append(f"{_n(refused, 'row was', 'rows were')} refused.")
        weights = ranges["bodyweight_kg"]
        if weights:
            lo, hi = (formatting.show(weights[k], "weigh_in") for k in ("lowest", "highest"))
            parts.append(f"Every weigh-in is {lo}." if lo == hi else f"Weigh-ins run from {lo} to {hi}.")
        sizes = ranges["body_size_cm"]
        if sizes:
            lo, hi = (formatting.show(sizes[k], "cm") for k in ("lowest", "highest"))
            parts.append(f"Every body size is {lo}." if lo == hi else f"Body sizes run from {lo} to {hi}.")
        if ranges["heaviest_set_kg"]:
            parts.append(f"The heaviest set is {formatting.show(ranges['heaviest_set_kg'], 'weight_kg')}.")
        if ranges["longest_km"]:
            parts.append(f"The longest distance is {formatting.show(ranges['longest_km'], 'km')}.")
        return " ".join(parts)
    again = f" {_n(repeated, 'row', 'rows')} repeated an earlier row of this call." if repeated else ""
    if there == total:
        if total == 1:
            return f"Nothing new: the row{span} is already there."
        return f"Nothing new: all {_n(total, 'row', 'rows')}{span} are already there.{again}"
    parts = [f"Imported {_count(outcomes['written'])} of {_n(total, 'row', 'rows')}{span}"
             + (f": {listing}." if listing else ".")]
    if entries_there:
        parts.append(f"{_n(entries_there, 'food entry', 'food entries')} in those rows "
                     f"{'was' if entries_there == 1 else 'were'} already there.")
    if repeated:
        parts.append(again.strip())
    if there and entries_there:  # beside the food entries' sentence: say these are rows
        parts.append(f"{_n(there, 'row was', 'rows were')} already there.")
    elif there:
        parts.append(f"{_count(there)} {'was' if there == 1 else 'were'} already there.")
    if taken:  # beside the food entries' sentence, these are named rows too
        parts.append(f"{_n(taken, 'row', 'rows') if entries_there else _count(taken)} "
                     f"{'was' if taken == 1 else 'were'} left out: "
                     f"{'its day already holds' if taken == 1 else 'their days already hold'} an entry.")
    if refused and entries_there:  # beside the food entries' sentence: say these are rows, as above
        parts.append(f"{_n(refused, 'row was', 'rows were')} refused.")
    elif refused:
        parts.append(f"{_count(refused)} {'was' if refused == 1 else 'were'} refused.")
    parts.append("Nothing was replaced.")
    return " ".join(parts)


def _note(dry: bool, to_write: list[dict], cardio_estimated, within: bool, imported: int, out: dict) -> str | None:
    notes = []
    if any(r["kind"] == "lift_session" for r in to_write):
        notes.append(SESSION_NOTE)
    if cardio_estimated is not None:
        notes.append(CARDIO_ESTIMATE_NOTE if cardio_estimated else NO_WEIGH_IN_NOTE)
    if out["more_failed"]:
        notes.append(f"The first {_count(LISTED)} refused rows are listed, and {_count(out['more_failed'])} more "
                     f"{'was' if out['more_failed'] == 1 else 'were'} refused.")
    if out["more_day_taken"]:
        notes.append(f"The first {_count(LISTED)} rows left out for a held day are listed, and "
                     f"{_count(out['more_day_taken'])} more {'was' if out['more_day_taken'] == 1 else 'were'} left out.")
    if dry and not within:
        notes.append(f"Sent to be written, this call would be refused: {_count(imported)} rows were imported in the "
                     f"last {IMPORT_HOURS} hours, and the most is {_count(IMPORT_LIMIT)}.")
    return " ".join(notes) or None
