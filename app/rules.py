"""The checks every logged entry passes, whoever sends it: the web form and Claude get the same ones.

Every failure is a plain dict {"error": code, "message": ..., "fix": ...}, never an exception.
The fix says what to send instead, so Claude can correct the call and retry.

  error(code, message, fix, **extra) -> dict
  combine(errors) -> dict | None         several failures as one error, so one retry can fix them all
  quote(value) -> str                    a caller's value as a message repeats it, cut to 80 characters
  text(value, field) -> (str | None, error | None)
        Free text: at most 2,000 characters, no control characters but newlines and tabs.
  to_float(value), to_date(value)        a finite number or a date, else None; never raise. Text is a number
        only in NUMBER_TEXT's one grammar, and a date only as YYYY-MM-DD (ISO_DAY), both ASCII digits
  keep(number, places) -> float          a number at its step, halves away from zero, after its range check
  match_exercise(name, names) -> (canonical name | None, closest names)
  unknown_exercise(name, names, where="") -> dict
  entry_date(value, today, noun) -> (date | None, error | None)
        Blank means today. Never after today (an entry records what has happened), never more
        than two years back.
  check_session(date_value, rows, names, today, pounds=True, reps_only=None) -> (session_date, sets, error | None)
        A lift session: a valid date; 1 to 60 sets; each set's exercise on the list, and what its lift records
        (contract exercise-list.md R6, measures_of): reps 1 to 100 whole, or time_s 1 to 3,600 whole, or
        distance_m 1 to 5,000 with time_s. Weight 0 to 500 kg; -200 to 0 kg on the two assisted lifts (help);
        none, stored as 0, on a band. sets: [{exercise (canonical), weight_kg, reps, time_s, distance_m}]. Every
        failure at once. Each set sends weight_kg or weight_lb (one_of); pounds=False is the import's: weight_kg
        only, in its words of before units. A range is checked on the number unrounded, then kept at its step.
        reps_only: the import's refusal of a lift that records no reps (R11).

Units (contract units.md, decision 15). The one table of factors, UNITS, and its helpers, which the import and
logging both use: converted (a number in lb, mi or in, in the stored unit), inward (a stored range in another
unit, each bound moved inward), call_unit (a refusal said in the unit sent) and in_unit (the same, for a log
field such as weight_lb). one_of(row, kg_field, other_field) is the check of a pair: one of the two, never both.

Ranges (contract inputs.md, decision 5): every range is checked on the number as sent, unrounded, and only then
is the number kept at its step, halves away from zero (keep). A lb or mi number is converted unrounded and checked
against the stored range.

"today" is always passed in by the caller: the person's own date, from users.today_for.
"""

import difflib
import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext

from app import exercises, formatting

HARD_RULES = {
    "reps": (1, 100),
    "weight_kg": (0, 500),
    "help_kg": (-200, 0),        # the weight on Assisted Pull-Up and Assisted Dip: help, as a minus weight
    "time_s": (1, 3600),         # a hold's or a carry's seconds, whole
    "distance_m": (1, 5000),     # a carry's or a sled's metres, 2 dp
}
# The two lifts whose weight is help (contract exercise-list.md, decision 9), by key.
HELP_LIFTS = ("assisted_pull_up", "assisted_dip")
# What a set of a lift records, by the lift's measures (exercise-list.md R6), in a refusal's words.
RECORDS = {("weight", "reps"): "reps", ("reps",): "reps", ("weight", "time"): "time",
           ("weight", "distance", "time"): "distance and time"}
SET_FIELD_WORDS = {"reps": "reps", "time_s": "time", "distance_m": "distance"}
SESSION_SETS = (1, 60)
BODYWEIGHT_KG = (20, 400)
# A routine (contract routines.md): at most this many live a person, its lifts, each lift's sets, its name's length.
# Its sets in all are SESSION_SETS[1], so a logged routine fits one session.
ROUTINES_MAX = 20
ROUTINE_LIFTS = (1, 20)
ROUTINE_LIFT_SETS = (1, 10)
ROUTINE_NAME_MAX = 64

# Measurement kinds: unit, lowest, highest, whole number only. One table, read by logging and editing.
MEASUREMENTS = {
    "waist": ("cm", 30, 300, False),
    "hips": ("cm", 30, 300, False),
    "chest": ("cm", 30, 300, False),
    "arm": ("cm", 10, 100, False),
    "thigh": ("cm", 20, 150, False),
    "steps": ("steps", 0, 200_000, True),
    "sleep_hours": ("h", 0, 24, False),
    "resting_hr": ("bpm", 20, 250, True),
    "water_ml": ("ml", 0, 20_000, True),
}
# The word for each kind, as a person reads it, defined once (measurements.KIND_WORDS reads it). The unit is
# never part of it: it stands beside the figure, so "Water 2,501 ml", never "Water ml 2,501 ml".
KIND_WORDS = {
    "waist": "Waist", "hips": "Hips", "chest": "Chest", "arm": "Arm", "thigh": "Thigh", "steps": "Steps",
    "sleep_hours": "Sleep", "resting_hr": "Resting heart rate", "water_ml": "Water",
}

# Food, cardio and the settings' targets: field -> (lowest, highest, how it is kept). "whole" refuses a
# fraction; "round" keeps a whole number however it was sent; an int keeps that many decimals.
NUMBERS = {
    "kcal": (0, 5_000, "round"),
    "servings": (0.1, 50, 2),
    "grams": (1, 5_000, 2),
    "kcal_per_serving": (0, 5_000, 2),
    "grams_per_serving": (1, 5_000, 2),
    "minutes": (1, 600, 1),
    "distance_km": (0.01, 300, 2),
    "incline_pct": (0, 40, 2),
    "speed_kmh": (0.1, 60, 2),
    "avg_hr": (30, 240, "whole"),
    "machine_kcal": (1, 5_000, "whole"),
    "calorie_target_kcal": (800, 10_000, "whole"),
    "target_sessions_per_week": (0, 14, "whole"),
    "target_cardio_minutes_per_week": (0, 3_000, "whole"),
}
TEXT_LIMITS = {"description": 200, "serving_label": 64, "name": 128}
MEALS = ("breakfast", "lunch", "dinner", "snack")
OLDEST_DAYS = 2 * 365 + 1  # entries go back two years at most, either side of a leap day
GOAL_YEARS = 5             # a goal's target date is at most this many years after today (goal_latest)
TEXT_MAX = 2000            # notes and goal text
# The one grammar of a number sent as text (inputs.md decision 4), ASCII only: outer spaces, at most one sign, digits,
# at most one decimal point, at least one digit. So " 80 ", "+80", "80." and ".5" are numbers; "1_0", "8e1",
# "1,000", other scripts' digits, "nan" and "inf" are not.
NUMBER_TEXT = re.compile(r"\s*[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\s*", re.ASCII)
# A date sent as text (decision 7): YYYY-MM-DD in ASCII digits, nothing else. The pages' addresses read it too.
ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
QUOTE_MAX = 80             # the most of a caller's value an error message repeats
NAME_MAX = 80              # far longer than any exercise name: no closest-name search beyond it
TODAY_NOTE = "Today is the person's own date: their time zone, or UTC until update_settings sets one."
# The quantity (app.formatting) each measurement unit and each NUMBERS field is shown in.
UNIT_QUANTITIES = {"kg": "weigh_in", "cm": "cm", "steps": "steps", "h": "hours", "bpm": "bpm", "ml": "ml"}
NUMBER_QUANTITIES = {
    "kcal": "kcal", "servings": "servings", "grams": "grams", "kcal_per_serving": "kcal_per_serving",
    "grams_per_serving": "grams", "minutes": "session_time", "distance_km": "km", "incline_pct": "incline",
    "speed_kmh": "speed", "avg_hr": "bpm", "machine_kcal": "kcal", "calorie_target_kcal": "kcal",
    "target_sessions_per_week": "count", "target_cardio_minutes_per_week": "week_minutes",
}


# ---------------------------------------------------------------- units: the one table of factors

UNITS = {"weight_unit": {"kg": 1.0, "lb": formatting.LB},
         "distance_unit": {"km": 1.0, "mi": formatting.MI},
         "length_unit": {"cm": 1.0, "in": 2.54}}
UNIT_OF = {factor: unit for table in UNITS.values() for unit, factor in table.items() if factor != 1.0}
UNITS_FIX = "Use kg or lb for weight_unit, km or mi for distance_unit."  # invalid_unit's fix: settings and goals
# A log field sent in lb or mi (decision 4): its stored twin, its factor, and the word for the thing it holds.
OTHER_FIELDS = {"weight_lb": ("weight_kg", formatting.LB, "weight"), "lb": ("kg", formatting.LB, "weight"),
                "distance_mi": ("distance_km", formatting.MI, "distance"),
                "speed_mph": ("speed_kmh", formatting.MI, "speed")}


def converted(value, factor: float):
    """A number in the call's unit, turned into the stored unit. Anything that is not a number, or a
    value already in the stored unit, goes on as sent, so the reused check refuses it in its own words."""
    number = to_float(value)
    if number is None or factor == 1.0:
        return value
    return number * factor


def inward(limits, factor: float, quantity: str) -> tuple[str, str]:
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


def call_unit(err: dict, field: str, raw, factor: float, limits, quantity: str, where: str = "",
              fix_tail: str = "", per: str = "", word: str | None = None) -> dict:
    """A refusal of a number sent in the call's unit (lb, mi, in), said in that unit. A range refusal quotes
    the value as sent and the range. Every refusal of the field, "not a number" and "missing" too, gets a
    fix with the range in the call's unit, never the stored unit's. The range through the formatter, each
    bound moved inward (inward). A refusal on a kg, km or cm call goes on as it is. word: the unit as said,
    when it is not the factor's own ("mph" for a speed sent in miles)."""
    if factor == 1.0:
        return err
    unit = word or (UNIT_OF[factor] + (f" {per}" if per else ""))
    lo, hi = inward(limits, factor, quantity)
    at = f"{where}: " if where else ""
    on = f" on {where.lower()}" if where else ""
    low_open = " must be more than " in err["message"]  # a range with its low end left out (in_range, low_open)
    fix = (f"Give {field}{on} more than {lo} and at most {hi} {unit}{fix_tail}." if low_open
           else f"Give {field}{on} from {lo} to {hi} {unit}{fix_tail}.")
    if " is outside " not in err["message"] and not low_open:
        return {**err, "fix": fix}
    after = "" if field == unit else f" {unit}"  # "lb 900 is outside", never "lb 900 lb"
    if low_open:
        return {**err, "message": f"{at}{field} {as_sent(raw)}{after} must be more than {lo} and at most {hi} {unit}.",
                "fix": fix}
    return {**err, "message": f"{at}{field} {as_sent(raw)}{after} is outside {lo} to {hi} {unit}.", "fix": fix}


def _given(value) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def both_sent(kg_field: str, other_field: str, where: str = "") -> dict:
    """invalid_field: a number and its twin in the other unit, both sent (decision 4)."""
    message = (f"{where} has both {kg_field} and {other_field}: send one." if where
               else f"Both {kg_field} and {other_field} were sent: send one.")
    return error("invalid_field", message, f"Send the {OTHER_FIELDS[other_field][2]} in the unit the person said: "
                                           f"{kg_field} or {other_field}.")


def one_of(row: dict, kg_field: str, other_field: str, where: str = "", needed: bool = False,
           missing_fix: str | None = None) -> tuple[str | None, object, dict | None]:
    """The check of a pair (R6): (the field sent, its value in the stored unit, None) or (None, None, error).
    Both sent, neither None nor blank, is invalid_field. Neither, when needed, is missing_field; when not needed,
    (None, None, None). The other field's value goes through converted, unrounded, so the stored unit's own check
    takes it and keeps it at 2 dp; a refusal of it is said in its unit with in_unit."""
    kg_given, other_given = _given(row.get(kg_field)), _given(row.get(other_field))
    if kg_given and other_given:
        return None, None, both_sent(kg_field, other_field, where)
    if other_given:
        return other_field, converted(row[other_field], OTHER_FIELDS[other_field][1]), None
    if kg_given or not needed:
        return (kg_field if kg_given else None), row.get(kg_field), None
    at = f"{where}: " if where else ""
    on = f" on {where.lower()}" if where else ""
    return None, None, error("missing_field", f"{at}{kg_field} or {other_field} is missing.",
                             missing_fix or f"Give {kg_field} or {other_field}{on}.")


def in_unit(err: dict, other_field: str, raw, limits, quantity: str, where: str = "", fix_tail: str = "",
            word: str | None = None) -> dict:
    """A refusal of a number sent in other_field (weight_lb, lb, distance_mi, speed_mph): its twin's name becomes
    the field's, and call_unit says it in the field's unit with the range moved inward."""
    kg_field, factor, _ = OTHER_FIELDS[other_field]
    name = re.compile(rf"(?<![\w.]){re.escape(kg_field)}(?![\w])")
    err = {**err, "message": name.sub(other_field, err["message"]), "fix": name.sub(other_field, err["fix"])}
    return call_unit(err, other_field, raw, factor, limits, quantity, where, fix_tail, word=word)


def span(lo, hi, quantity: str) -> str:
    """A range for a message or a fix, through the formatter: "0 to 5,000", "0.1 to 60"."""
    return f"{formatting.show(lo, quantity, unit=False)} to {formatting.show(hi, quantity, unit=False)}"


def as_sent(value) -> str:
    """A refused number as the caller sent it, for a message: never rounded to a shown step, so
    "not a whole number, not 2.5" and "5000.6 is outside" say what came in. A whole number shows no ".0"
    (inputs.md, owner's question 2): the library hands 0 on as 0.0, and "0" is what was sent."""
    if isinstance(value, float) and math.isfinite(value) and value.is_integer() and abs(value) < 1e16:
        return quote(int(value))
    return quote(value.strip() if isinstance(value, str) else value)


def quote(value) -> str:
    """A caller's value as an error message repeats it: cut to 80 characters, with an ellipsis, and
    always valid UTF-8 (a lone surrogate is shown as its escape, such as \\ud83d), so the reply can
    be encoded."""
    try:
        shown = value if isinstance(value, str) else repr(value)
    except ValueError:  # an int too long to print
        shown = "a number too long to show"
    shown = shown if len(shown) <= QUOTE_MAX else shown[:QUOTE_MAX - 1] + "…"
    return shown.encode("utf-8", "backslashreplace").decode("utf-8")


def text(value, field: str, limit: int = TEXT_MAX) -> tuple[str | None, dict | None]:
    """Free text a caller sends, stripped. None and blank give "". Newlines and tabs are kept; any
    other control character is refused (the database cannot store some of them), and so is text
    over `limit` characters."""
    if value is None:
        return "", None
    if not isinstance(value, str):
        return None, error("invalid_field", f"{field} must be text, not {quote(value)}.", f"Give {field} as text.")
    value = value.strip()
    if any(unicodedata.category(ch) in ("Cc", "Cs") and ch not in "\n\r\t" for ch in value):
        return None, error("invalid_field", f"{field} holds a control character.",
                           f"Remove control characters from {field}; newlines and tabs are fine.")
    if len(value) > limit:
        return None, error("invalid_field", f"{field} is {len(value)} characters; the most is {limit}.",
                           f"Shorten {field} to {limit} characters or fewer.")
    return value, None


def to_float(value) -> float | None:
    """A finite float, or None. Booleans, text outside NUMBER_TEXT ("1_0", "8e1", "٨٠", "nan"), and numbers
    too big for a float are None. -0.0 becomes 0.0."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and not NUMBER_TEXT.fullmatch(value):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed + 0.0 if math.isfinite(parsed) else None


def keep(number: float, places: int) -> float:
    """A number kept at `places` decimals, halves away from zero (2.5 is 3, 0.125 is 0.13), from its shortest
    repr, so 80.125 is the half it reads as. A number too large to hold a fraction is already at its step."""
    if not math.isfinite(number) or abs(number) >= 2 ** 52:
        return number
    step = Decimal(1).scaleb(-places)
    return float(Decimal(repr(float(number))).quantize(step, rounding=ROUND_HALF_UP)) + 0.0


def to_date(value) -> date | None:
    """A date from a date, a datetime (its date part) or text that is YYYY-MM-DD (ISO_DAY) and a real day, its
    outer spaces forgiven; else None. 20261116 and 2026-W01-1 are not dates here."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not ISO_DAY.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def goal_latest(today: date) -> date:
    """The latest target date a goal takes: the same day GOAL_YEARS on, and 28 February for 29 February."""
    year = today.year + GOAL_YEARS
    try:
        return today.replace(year=year)
    except ValueError:  # 29 February, in a year that has none
        return date(year, 2, 28)


def error(code: str, message: str, fix: str, **extra) -> dict:
    return {"error": code, "message": message, "fix": fix, **extra}


def combine(errors: list[dict]) -> dict | None:
    """Several failures as one error dict: the first one's code and extras, every message and fix
    joined, and the full list under "errors", so Claude can fix everything in one retry."""
    if not errors:
        return None
    if len(errors) == 1:
        return {**errors[0], "errors": errors}
    return {**errors[0],
            "message": f"{len(errors)} problems. " + " ".join(e["message"] for e in errors),
            "fix": " ".join(dict.fromkeys(e["fix"] for e in errors)),
            "errors": errors}


def _key(name: str) -> str:
    return " ".join(str(name or "").split()).casefold()


def match_exercise(name, names: list[str]) -> tuple[str | None, list[str]]:
    """Exact match ignoring case and extra spaces. On a miss, up to three closest names; a name far
    longer than any on the list gets none, without searching."""
    key = _key(name) if isinstance(name, str) else ""
    by_key = {_key(n): n for n in names}
    hit = by_key.get(key)
    if hit:
        return hit, []
    if not key or len(key) > NAME_MAX:
        return None, []
    close = difflib.get_close_matches(key, list(by_key), n=3, cutoff=0.5)
    return None, [by_key[c] for c in close]


def unknown_exercise(name, names: list[str], where: str = "") -> dict:
    _, close = match_exercise(name, names)
    hint = f"Closest names: {', '.join(close)}." if close else "No close name found."
    return error(
        "unknown_exercise",
        f"{where}'{quote(name)}' is not on the exercise list.",
        f"{hint} Use a name exactly as list_exercises gives it.",
        closest=close,
    )


def number(value, kind, field: str, where: str = "") -> tuple[float | int | None, dict | None]:
    """A finite number (kind float, kept at 2 dp, halves away from zero) or a whole one (kind int), else an error.
    `where` names the place, such as "Set 3", in the message and the fix. It checks no range: a caller with one
    checks it on to_float(value), unrounded, as check_session does."""
    at_m = f"{where}: " if where else ""
    at_f = f" on {where.lower()}" if where else ""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, error("missing_field", f"{at_m}{field} is missing.", f"Give {field}{at_f}.")
    parsed = to_float(value)
    if parsed is None:
        return None, error("not_a_number", f"{at_m}{field} '{quote(value)}' is not a number.",
                           f"Give {field}{at_f} as a number.")
    if kind is int:
        if parsed != int(parsed):
            return None, error("not_a_whole_number", f"{at_m}{field} must be a whole number.",
                               f"Give {field}{at_f} as a whole number.")
        return int(parsed), None
    return keep(parsed, 2), None


def _kind_key(name: str) -> str:
    return " ".join(name.replace("_", " ").split()).casefold()


def match_kind(name) -> tuple[str | None, dict | None]:
    """A measurement kind from what the caller sent: case, spaces and underscores forgiven
    ("Waist", "resting hr"), meaning not. Otherwise invalid_kind, with the list and the closest."""
    by_key = {_kind_key(k): k for k in MEASUREMENTS}
    key = _kind_key(name) if isinstance(name, str) else ""
    if key in by_key:
        return by_key[key], None
    close = difflib.get_close_matches(key, list(by_key), n=3, cutoff=0.6) if 0 < len(key) <= NAME_MAX else []
    kinds = ", ".join(MEASUREMENTS)
    hint = f" Closest: {', '.join(by_key[c] for c in close)}." if close else ""
    return None, error("invalid_kind", f"'{quote(name)}' is not a kind of measurement.",
                       f"Use one of: {kinds}.{hint}", closest=[by_key[c] for c in close])


def measurement_value(kind: str, value) -> tuple[float | int | None, dict | None]:
    """A value for this kind: in its range, a whole number where the kind counts, else 2 dp."""
    unit, lo, hi, whole = MEASUREMENTS[kind]
    in_unit = "" if unit == kind else f" in {unit}"
    limits = span(lo, hi, UNIT_QUANTITIES[unit])
    fix = f"Give {KIND_WORDS[kind].lower()}{in_unit}, {limits}" + (", a whole number." if whole else ".")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, error("missing_field", "value is missing.", fix)
    number = to_float(value)
    if number is None:
        return None, error("invalid_field", f"value '{quote(value)}' is not a number.", fix)
    if whole and number != int(number):
        return None, error("invalid_field", f"{kind} must be a whole number, not {as_sent(value)}.", fix)
    if not lo <= number <= hi:
        return None, error("invalid_field", f"{kind} {as_sent(value)} {unit} is outside {limits}.", fix)
    return (int(number) if whole else keep(number, 2)), None


def bounded(field: str, value, where: str = "") -> tuple[float | int | None, dict | None]:
    """A number for one of the NUMBERS fields: in its range and kept its way, else an error.
    `where` names the place, such as "Entry 2", in the message."""
    lo, hi, how = NUMBERS[field]
    at = f"{where}: " if where else ""
    # A range of minutes reads as whole minutes ("1 to 600"), not as a session's m:ss.
    limits = span(lo, hi, "week_minutes" if field == "minutes" else NUMBER_QUANTITIES[field])
    on = f" on {where.lower()}" if where else ""
    if how == "round":  # any number is taken, and kept whole: the fix says both (inputs.md decision 14)
        fix = f"Give {field} as a number from {limits}{on}: it is kept as a whole number."
    else:
        fix = f"Give {field} as {'a whole number' if how == 'whole' else 'a number'} from {limits}{on}."
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, error("missing_field", f"{at}{field} is missing.", fix)
    number = to_float(value)
    if number is None:
        return None, error("invalid_field", f"{at}{field} '{quote(value)}' is not a number.", fix)
    if how == "whole" and number != int(number):
        return None, error("invalid_field", f"{at}{field} must be a whole number, not {as_sent(value)}.", fix)
    if not lo <= number <= hi:  # the number as sent, unrounded: 5000.4 kcal is outside, never kept as 5000
        return None, error("invalid_field", f"{at}{field} {as_sent(value)} is outside {limits}.", fix)
    return (int(keep(number, 0)) if how in ("whole", "round") else keep(number, how)), None


# Letters that draw nothing: the Hangul fillers and the blank Braille pattern.
BLANK_LETTERS = frozenset(map(chr, (0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800)))


def visible(value: str) -> bool:
    """True when the text holds a character a person can see: not only spaces, format characters (Cf) such as a
    zero-width space or a word joiner, marks with nothing to sit on (Mn, Me) and BLANK_LETTERS."""
    return any(not ch.isspace() and ch not in BLANK_LETTERS and unicodedata.category(ch) not in ("Cf", "Mn", "Me")
               for ch in value)


def required_text(value, field: str, where: str = "", limit: int | None = None) -> tuple[str | None, dict | None]:
    """Free text that must be there: 1 to `limit` characters (left out: TEXT_LIMITS[field]), through text(). Text of
    only invisible characters (visible) is missing."""
    at = f"{where}: " if where else ""
    most = TEXT_LIMITS[field] if limit is None else limit
    kept, err = text(value, field, most)
    if err:
        return None, {**err, "message": at + err["message"]}
    if not visible(kept):  # nothing, or only invisible characters such as a zero-width space (owner's question 3)
        return None, error("missing_field", f"{at}{field} is missing.", f"Give {field}, 1 to {most} characters.")
    return kept, None


def match_meal(name, where: str = "") -> tuple[str | None, dict | None]:
    """A meal slot: case and spaces forgiven (" Lunch "), meaning not."""
    key = " ".join(name.split()).casefold() if isinstance(name, str) else ""
    if key in MEALS:
        return key, None
    at = f"{where}: " if where else ""
    return None, error("invalid_field", f"{at}meal '{quote(name)}' is not a meal.",
                       f"Use one of: {', '.join(MEALS)}, or leave meal out and the person's clock picks it.")


def entry_date(value, today: date, noun: str = "entries") -> tuple[date | None, dict | None]:
    """The date an entry happened on. Blank means today. Never after today, and never more than
    two years (OLDEST_DAYS) before it."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return today, None
    parsed = to_date(value)
    if parsed is None:
        return None, error("invalid_date", f"date '{quote(value)}' is not a date.",
                           "Give the date as YYYY-MM-DD, or leave it out for today.")
    if parsed > today:
        return None, error("date_in_future", f"date {parsed.isoformat()} is after today ({today.isoformat()}).",
                           f"Log only {noun} that have happened: use today or an earlier date. {TODAY_NOTE}")
    if parsed < today - timedelta(days=OLDEST_DAYS):
        return None, error("date_too_old", f"date {parsed.isoformat()} is more than 2 years ago.",
                           f"Log {noun} from the last 2 years only; check the year.")
    return parsed, None


def measures_of(name) -> tuple[str, ...]:
    """What a set of this lift records, from the list's own row (app.exercises). A name not on the list, a retired
    row's among them, records weight and reps, as every lift did before."""
    row = exercises.BY_NAME.get(name) if isinstance(name, str) else None
    return row.measures if row is not None else ("weight", "reps")


def records(name) -> str:
    """What a set of this lift records, in words: reps, time, or distance and time."""
    return RECORDS.get(measures_of(name), "reps")


def takes_help(name) -> bool:
    """Whether this lift's weight is help, a minus weight: Assisted Pull-Up and Assisted Dip (decision 9)."""
    row = exercises.BY_NAME.get(name) if isinstance(name, str) else None
    return row is not None and row.key in HELP_LIFTS


def weight_range(name) -> tuple[float, float]:
    """The weight a set of this lift takes, in kg: -200 to 0 on the two assisted lifts, else 0 to 500 (R6)."""
    return HARD_RULES["help_kg"] if takes_help(name) else HARD_RULES["weight_kg"]


def _set_weight(row: dict, n: int, where: str, name, band: bool, pounds: bool) -> tuple[float | None, list[dict]]:
    """A set's weight, checked by its lift (R6): (weight in kg, errors). A band set takes none above 0, and none sent
    stores 0. A minus weight is help on the two assisted lifts only. Every other lift takes 0 to 500 kg."""
    sent_in, value, err = (one_of(row, "weight_kg", "weight_lb", where, needed=not band) if pounds
                           else ("weight_kg", row.get("weight_kg"), None))
    if err:
        return None, [err]
    if sent_in is None:  # a band set with no weight sent
        return 0.0, []
    weight, err = number(value, float, "weight_kg", where)
    kg = to_float(value) if err is None else None  # unrounded: -0.01 lb is refused, never 0 kg
    lo, hi = weight_range(name)
    sent = as_sent(row.get("weight_kg"))  # as sent: a huge number stays short (quote cuts it)
    tail = ""
    if err is None and band and kg != 0:  # any weight but 0, a minus one too: a band has none
        # log_lifts takes no weight on a band; the import's weight is required, so it is sent as 0 there.
        fix = (f"Leave the weight out on set {n}: a band set stores 0 kg." if pounds
               else f"Send weight 0 on set {n}: a band set stores 0 kg.")
        return None, [error("invalid_field", f"{where}: {name} records reps, not weight.", fix)]
    if err is None and takes_help(name):
        tail = ": the help as a minus weight"
        if not lo <= kg <= hi:
            err = error("weight_out_of_range", f"{where}: weight {sent} kg is outside {span(lo, hi, 'weight_kg')} kg.",
                        f"Give weight_kg on set {n} from {span(lo, hi, 'weight_kg')}{tail}.")
    elif err is None and name is not None and kg < 0:
        err = error("weight_out_of_range",
                    f"{where}: a minus weight is help, on Assisted Pull-Up or Assisted Dip only.",
                    f"Give weight_kg on set {n} from {lo} to {hi} (0 for bodyweight).")
    elif err is None and not lo <= kg <= hi:
        err = error("weight_out_of_range", f"{where}: weight {sent} kg is outside {lo} to {hi} kg.",
                    f"Give weight_kg on set {n} from {lo} to {hi} (0 for bodyweight).")
    if err and sent_in == "weight_lb":
        err = in_unit(err, "weight_lb", row["weight_lb"], (lo, hi), "weight_kg", where, tail)
    return (None, [err]) if err else (weight, [])


# A set's reps, time and distance: (kind, the error code of its range, the words after the range in a fix).
SET_NUMBERS = {"reps": (int, "reps_out_of_range", ""), "time_s": (int, "time_out_of_range", ", in seconds"),
               "distance_m": (float, "distance_out_of_range", ", in metres")}
NEEDED = {"reps": "reps", "time": "time_s", "distance and time": "distance_m"}  # the field a set must have


def _set_numbers(row: dict, n: int, where: str, name) -> tuple[dict, list[dict]]:
    """A set's reps, time_s and distance_m, checked by what its lift records (R6): ({field: value or None}, errors).
    A lift of weight and reps, or a band's, needs reps; a hold needs time_s; a carry or a sled needs distance_m and
    takes time_s. A field the lift does not record is invalid_field. A name not matched has its numbers checked for
    their ranges alone, and reps is needed when no time or distance is sent."""
    sent = [f for f in SET_NUMBERS if row.get(f) is not None and not (isinstance(row[f], str) and not row[f].strip())]
    errors = []
    if name is None:
        allowed, needed = set(SET_NUMBERS), (None if sent else "reps")
    else:
        what = records(name)
        measures = measures_of(name)
        allowed = {f for f, word in (("reps", "reps"), ("time_s", "time"), ("distance_m", "distance"))
                   if word in measures}
        needed = NEEDED[what]
        errors = [error("invalid_field", f"{where}: {name} records {what}, not {SET_FIELD_WORDS[f]}.",
                        f"Send {needed} on set {n}.") for f in sent if f not in allowed]
        if errors:
            needed = None  # the fix already says what to send
    out = {}
    for field, (kind, code, tail) in SET_NUMBERS.items():
        out[field] = None
        if field not in allowed or (field not in sent and field != needed):
            continue
        value, err = number(row.get(field), kind, field, where)
        lo, hi = HARD_RULES[field]
        if err is None and not lo <= to_float(row.get(field)) <= hi:  # unrounded, as a weight is
            shown = span(lo, hi, "count")
            err = error(code, f"{where}: {field} {as_sent(row.get(field))} is outside {shown}.",
                        f"Give {field} on set {n} from {shown}{tail}.")
        if err:
            errors.append(err)
        else:
            out[field] = value
    return out, errors


def check_session(date_value, rows, names: list[str], today: date, pounds: bool = True,
                  reps_only: tuple[str, str] | None = None) -> tuple[date | None, list[dict], dict | None]:
    """A lift session, checked. Every failure combined (see combine).

    Returns (session date, sets [{exercise, weight_kg, reps, time_s, distance_m}] with canonical names, None) or
    (None, [], error). A blank date means today. What a set takes comes from its lift's measures on the list
    (contract exercise-list.md R6): reps, a time, or a distance and a time; reps 1 to 100. Weight may be 0
    (bodyweight work), and is help, a minus weight, on the two assisted lifts only. Each set sends weight_kg or
    weight_lb, and a weight_lb is kept in kg (R9). pounds=False takes weight_kg alone, in the words it had before:
    the import's. reps_only, (the words after "records time: ", the fix), refuses a set of a lift that records no
    reps once a lift, its own checks left out: the import's sets are sets of reps (R11).
    """
    errors = []
    session_date, err = entry_date(date_value, today, "sessions")
    if err:
        errors.append(err)
    rows = rows if isinstance(rows, list) else []
    lo, hi = SESSION_SETS
    if not lo <= len(rows) <= hi:
        errors.append(error("sets_count_out_of_range", f"The session has {len(rows)} sets; it needs {lo} to {hi}.",
                            f"Send one row per set, {lo} to {hi} rows: exercise, "
                            f"{'weight_kg or weight_lb' if pounds else 'weight_kg'}, reps."))
    sets, refused = [], set()
    for n, row in enumerate(rows[:hi], start=1):
        row = row if isinstance(row, dict) else {}
        where = f"Set {n}"
        raw = row.get("exercise")
        raw_name = raw.strip() if isinstance(raw, str) else ("" if raw is None else raw)
        name = None
        if not raw_name:
            errors.append(error("missing_field", f"{where}: exercise is missing.", f"Give an exercise name on set {n}."))
        else:
            name, _ = match_exercise(raw_name, names)
            if name is None:
                errors.append(unknown_exercise(raw_name, names, f"{where}: "))
        if reps_only is not None and name is not None and "reps" not in measures_of(name):
            if name not in refused:
                refused.add(name)
                errors.append(error("invalid_field", f"{name} records {records(name)}: {reps_only[0]}", reps_only[1]))
            continue
        weight, found = _set_weight(row, n, where, name, measures_of(name) == ("reps",) if name else False, pounds)
        errors += found
        numbers, found = _set_numbers(row, n, where, name)
        errors += found
        sets.append({"exercise": name, "weight_kg": weight, **numbers})
    if errors:
        return None, [], combine(errors)
    return session_date, sets, None
