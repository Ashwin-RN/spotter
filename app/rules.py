"""The checks every logged entry passes, whoever sends it: the web form and Claude get the same ones.

Every failure is a plain dict {"error": code, "message": ..., "fix": ...}, never an exception.
The fix says what to send instead, so Claude can correct the call and retry.

  error(code, message, fix, **extra) -> dict
  combine(errors) -> dict | None         several failures as one error, so one retry can fix them all
  quote(value) -> str                    a caller's value as a message repeats it, cut to 80 characters
  text(value, field) -> (str | None, error | None)
        Free text: at most 2,000 characters, no control characters but newlines and tabs.
  to_float(value), to_date(value)        a finite number or a date, else None; never raise
  match_exercise(name, names) -> (canonical name | None, closest names)
  unknown_exercise(name, names, where="") -> dict
  entry_date(value, today, noun) -> (date | None, error | None)
        Blank means today. Never after today (an entry records what has happened), never more
        than two years back.
  check_session(date_value, rows, names, today) -> (session_date, sets, error | None)
        A lift session: a valid date; 1 to 60 sets; each set's exercise on the list, weight 0 to
        500 kg, reps 1 to 30 whole. sets: [{exercise (canonical), weight_kg, reps}]. Every failure at once.

"today" is always passed in by the caller: the person's own date, from users.today_for.
"""

import difflib
import math
import unicodedata
from datetime import date, datetime, timedelta

from app import formatting

HARD_RULES = {
    "reps": (1, 30),
    "weight_kg": (0, 500),
}
SESSION_SETS = (1, 60)
BODYWEIGHT_KG = (20, 400)

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
TEXT_MAX = 2000            # notes and goal text
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


def span(lo, hi, quantity: str) -> str:
    """A range for a message or a fix, through the formatter: "0 to 5,000", "0.1 to 60"."""
    return f"{formatting.show(lo, quantity, unit=False)} to {formatting.show(hi, quantity, unit=False)}"


def as_sent(value) -> str:
    """A refused number as the caller sent it, for a message: never rounded to a shown step, so
    "not a whole number, not 2.5" and "5000.6 is outside" say what came in."""
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
    """A finite float, or None. Booleans, text that is not a number, and numbers too big for a
    float are None. -0.0 becomes 0.0."""
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed + 0.0 if math.isfinite(parsed) else None


def to_date(value) -> date | None:
    """A date from a date, a datetime (its date part) or ISO text; else None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


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
    """A finite number (kind float, rounded to 2 dp) or a whole one (kind int), else an error.
    `where` names the place, such as "Set 3", in the message and the fix."""
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
    return round(parsed, 2) + 0.0, None


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
    return (int(number) if whole else round(number, 2) + 0.0), None


def bounded(field: str, value, where: str = "") -> tuple[float | int | None, dict | None]:
    """A number for one of the NUMBERS fields: in its range and kept its way, else an error.
    `where` names the place, such as "Entry 2", in the message."""
    lo, hi, keep = NUMBERS[field]
    at = f"{where}: " if where else ""
    kind = "a whole number" if keep in ("whole", "round") else "a number"
    # A range of minutes reads as whole minutes ("1 to 600"), not as a session's m:ss.
    limits = span(lo, hi, "week_minutes" if field == "minutes" else NUMBER_QUANTITIES[field])
    fix = f"Give {field} as {kind} from {limits}{' on ' + where.lower() if where else ''}."
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, error("missing_field", f"{at}{field} is missing.", fix)
    number = to_float(value)
    if number is None:
        return None, error("invalid_field", f"{at}{field} '{quote(value)}' is not a number.", fix)
    if keep == "whole" and number != int(number):
        return None, error("invalid_field", f"{at}{field} must be a whole number, not {as_sent(value)}.", fix)
    kept = int(round(number)) if keep in ("whole", "round") else round(number, keep) + 0.0
    if not lo <= kept <= hi:
        return None, error("invalid_field", f"{at}{field} {as_sent(value)} is outside {limits}.", fix)
    return kept, None


def required_text(value, field: str, where: str = "") -> tuple[str | None, dict | None]:
    """Free text that must be there: 1 to TEXT_LIMITS[field] characters, through text()."""
    at = f"{where}: " if where else ""
    kept, err = text(value, field, TEXT_LIMITS[field])
    if err:
        return None, {**err, "message": at + err["message"]}
    if not kept:
        return None, error("missing_field", f"{at}{field} is missing.", f"Give {field}, 1 to {TEXT_LIMITS[field]} characters.")
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


def check_session(date_value, rows, names: list[str], today: date) -> tuple[date | None, list[dict], dict | None]:
    """A lift session, checked. Every failure combined (see combine).

    Returns (session date, sets [{exercise, weight_kg, reps}] with canonical names, None) or (None, [], error).
    A blank date means today. Weight may be 0 (bodyweight work).
    """
    errors = []
    session_date, err = entry_date(date_value, today, "sessions")
    if err:
        errors.append(err)
    rows = rows if isinstance(rows, list) else []
    lo, hi = SESSION_SETS
    if not lo <= len(rows) <= hi:
        errors.append(error("sets_count_out_of_range", f"The session has {len(rows)} sets; it needs {lo} to {hi}.",
                            f"Send one row per set, {lo} to {hi} rows: exercise, weight_kg, reps."))
    w_lo, w_hi = HARD_RULES["weight_kg"]
    r_lo, r_hi = HARD_RULES["reps"]
    sets = []
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
        weight, err = number(row.get("weight_kg"), float, "weight_kg", where)
        if err:
            errors.append(err)
        elif not w_lo <= weight <= w_hi:
            sent = as_sent(row.get("weight_kg"))  # as sent: a huge number stays short (quote cuts it)
            errors.append(error("weight_out_of_range", f"{where}: weight {sent} kg is outside {w_lo} to {w_hi} kg.",
                                f"Give weight_kg on set {n} from {w_lo} to {w_hi} (0 for bodyweight)."))
        reps, err = number(row.get("reps"), int, "reps", where)
        if err:
            errors.append(err)
        elif not r_lo <= reps <= r_hi:
            errors.append(error("reps_out_of_range", f"{where}: reps {as_sent(row.get('reps'))} is outside "
                                f"{r_lo} to {r_hi}.",
                                f"Give reps on set {n} from {r_lo} to {r_hi}."))
        sets.append({"exercise": name, "weight_kg": weight, "reps": reps})
    if errors:
        return None, [], combine(errors)
    return session_date, sets, None
