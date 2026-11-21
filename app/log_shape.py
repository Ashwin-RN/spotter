"""log's shape (contract docs/contracts/log-and-guide.md): the kinds one log call takes, the words it reads for each,
and route, which turns a log call into a call of the kind's own function, in that function's own shape.

A kind is open text that Spotter checks (OPEN_WORDS, MEASURE_WORDS), never a list in a schema. The numbers lifts and
cardio share go in parts, one part a set or the one cardio session (mcp_schemas.LogPartIn); a kind's own fields go in
details; date, notes, allow_repeat and key go at the top. route gives the kind, its function's name (a key of
mcp_schemas.LOG_ARGUMENTS) and the arguments in that function's own shape, so the kind's argument model, its step and
its service judge them as they judge the function's own call. route refuses only what the shape itself makes wrong:
a field in the wrong place, a key the kind does not take, the part counts, and a time or a distance it cannot convert.

A time goes as time_s or minutes and a distance as distance_m, distance_km or distance_mi, as the person said it
(decision 51). route converts each to the kind's own column: a lift's time_s and distance_m, a cardio session's
minutes and distance_km. A cardio distance_mi goes on as log_cardio takes it, and its service converts it.

said_as_sent says a kind's own refusal in the names log was sent: a set's exercise as movement, "Set 3" as parts[2],
kg as details.kg, "Entry 2" as details.entries[1], and one of the five functions' names as log.

It imports only app.mcp_schemas and app.rules from the app: the judge and the call log read it, so it never loads
app.main, app.mcp_server or app.calllog. It reads the five's argument models and words, never a registered function,
so it holds once the five are no longer registered.
"""

import dataclasses
import difflib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

from app import rules
from app.mcp_schemas import (
    KEY_DESCRIPTION, LOG_ARGUMENTS, NEAR_CUTOFF, NEAR_MISSES, PART_DISTANCE_KM_DESCRIPTION, PART_DISTANCE_M_DESCRIPTION,
    PART_DISTANCE_MI_DESCRIPTION, PART_MINUTES_DESCRIPTION, PART_MOVEMENT_DESCRIPTION, PART_TIME_S_DESCRIPTION, RESULTS,
    LogPartIn,
)

# ---------------------------------------------------------------- the kinds and their words

# The kind each of the five log functions records, in the order guide lists them.
KIND_FUNCTIONS = {"lifts": "log_lifts", "bodyweight": "log_bodyweight", "measurement": "log_measurement",
                  "food": "log_food", "cardio": "log_cardio"}
KINDS = tuple(KIND_FUNCTIONS)
LOG_FUNCTIONS = tuple(KIND_FUNCTIONS.values())

# The word each kind's entries carry in Spotter's own answers and in edit_entry, delete_entry and restore_entry
# (decision 52): log's five words map to these, and every other word goes on as it was sent.
TABLE_WORDS = {"lifts": "lift_session", "bodyweight": "bodyweight", "measurement": "measurement",
               "food": "food_entry", "cardio": "cardio_entry"}


def table_word(kind: Any) -> Any:
    """The table word for one of log's five kind words, exactly as written; any other value as it was sent."""
    return TABLE_WORDS.get(kind, kind) if isinstance(kind, str) else kind


def fold(word: str) -> str:
    """A kind word as the checks read it: lower case, _ and - as spaces, one space between words."""
    return " ".join(word.casefold().replace("_", " ").replace("-", " ").split())


# The words open text takes for each kind, after fold(): the kind itself and the plain words for it.
OPEN_WORDS = {
    "lifts": "lifts", "lift": "lifts", "lifting": "lifts", "weights": "lifts", "weight training": "lifts",
    "strength": "lifts", "gym": "lifts", "sets": "lifts", "workout": "lifts",
    "bodyweight": "bodyweight", "body weight": "bodyweight", "weight": "bodyweight", "weigh in": "bodyweight",
    "weighin": "bodyweight", "scale": "bodyweight",
    "measurement": "measurement", "measurements": "measurement", "body measurement": "measurement",
    "food": "food", "meal": "food", "meals": "food", "eating": "food", "nutrition": "food", "diet": "food",
    "cardio": "cardio", "run": "cardio", "running": "cardio", "walk": "cardio", "walking": "cardio",
    "cycling": "cardio", "ride": "cardio", "swim": "cardio", "swimming": "cardio", "rowing": "cardio",
    "treadmill": "cardio",
    # The words Spotter's own log answers give in kind, where the answer's word is not the kind's own (decision 44).
    "lift session": "lifts", "food entry": "food", "cardio entry": "cardio",
}
# A measurement's own word as the kind: the measurement it names, which route fills in as details.kind.
MEASURE_WORDS = {**{fold(k): k for k in rules.MEASUREMENTS}, "sleep": "sleep_hours",
                 "resting heart rate": "resting_hr", "heart rate": "resting_hr", "pulse": "resting_hr",
                 "water": "water_ml"}
KIND_WORDS = (*OPEN_WORDS, *MEASURE_WORDS)


def resolve_kind(word: Any) -> tuple[str, str | None] | None:
    """(the kind, the measurement a measurement's own word names, else None) for a kind word, or None when Spotter
    logs no such kind."""
    if not isinstance(word, str):
        return None
    folded = fold(word)
    if folded in OPEN_WORDS:
        return OPEN_WORDS[folded], None
    if folded in MEASURE_WORDS:
        return "measurement", MEASURE_WORDS[folded]
    return None


def closest_word(word: Any) -> str | None:
    """The kind word nearest to one Spotter does not take, by spelling, or None."""
    shown = word if isinstance(word, str) else _json(word)
    near = difflib.get_close_matches(fold(shown), list(KIND_WORDS), n=1, cutoff=0.6)
    return near[0] if near else None


def note_word(word: Any) -> str:
    """The kind word as the call row may keep it: one of Spotter's own words, folded, with _ between words; any other
    word is "other". The free text sent is never kept."""
    if isinstance(word, str) and fold(word) in KIND_WORDS:
        return fold(word).replace(" ", "_")
    return "other"


# ---------------------------------------------------------------- the fields

PART_FIELDS = tuple(LogPartIn.model_fields)  # movement, weight_kg, weight_lb, reps, time_s, distance_m, exact, ...
# A part's fields each kind's function takes as they are, and the field each one is in that function.
PART_MAP = {"lifts": {"movement": "exercise", "weight_kg": "weight_kg", "weight_lb": "weight_lb", "reps": "reps",
                      "time_s": "time_s", "distance_m": "distance_m", "exact": "exact"},
            "cardio": {"movement": "exercise", "minutes": "minutes", "distance_km": "distance_km",
                       "distance_mi": "distance_mi"}}
# The other forms of a time or a distance each kind takes, and the column route converts each to (decision 51).
CONVERTED = {"lifts": {"minutes": "time_s", "distance_km": "distance_m", "distance_mi": "distance_m"},
             "cardio": {"time_s": "minutes", "distance_m": "distance_km"}}
# Every part field a kind takes, in LogPartIn's order. A cardio part takes no weight, reps or exact.
PART_TAKES = {kind: tuple(f for f in PART_FIELDS if f in PART_MAP[kind] or f in CONVERTED[kind]) for kind in PART_MAP}
FORMS = (("time_s", "minutes"), ("distance_m", "distance_km", "distance_mi"))  # the forms of one number
TOP_FIELDS = ("date", "notes", "allow_repeat", "key")
NOTES_KINDS = ("lifts", "cardio")  # the kinds whose function takes notes
LOG_FIELDS = ("kind", "parts", "details", *TOP_FIELDS)
DETAIL_FIELDS = {"lifts": ("new_session",),
                 "cardio": ("incline_pct", "speed_kmh", "speed_mph", "avg_hr", "machine_kcal"),
                 "bodyweight": ("kg", "lb"), "food": ("entries",), "measurement": ("kind", "value")}

# Decision 51's conversions, in exact decimals, by (the form sent, the kind's column): a mile in km is rules' own.
_MILE_KM = Decimal(repr(rules.OTHER_FIELDS["distance_mi"][1]))
_CONVERSIONS = {("minutes", "time_s"): lambda n: n * 60, ("distance_km", "distance_m"): lambda n: n * 1000,
                ("distance_mi", "distance_m"): lambda n: n * _MILE_KM * 1000,
                ("time_s", "minutes"): lambda n: n / 60, ("distance_m", "distance_km"): lambda n: n / 1000}

# ---------------------------------------------------------------- log's words (model-read)

LOG_DESCRIPTION = (
    "Records one entry of a kind: lifts, bodyweight, measurement, food or cardio. Lifts and cardio send their "
    "numbers in parts. A kind's own fields go in details, as guide gives them for that kind. A message about "
    "several kinds takes one call for each, and each measurement its own call. Returns what the kind's own log "
    "returns."
)
LOG_KIND_DESCRIPTION = (
    "What is logged, in a word: lifts, bodyweight, measurement, food or cardio, or a measurement's own word, such as "
    "waist, sleep, steps, resting heart rate or water. guide with topic log names every kind."
)
LOG_PARTS_DESCRIPTION = (
    "Lifts: one part a set, in order, 1 to 60, each with movement and what the lift records: a weight in weight_kg "
    "or weight_lb and reps, a hold's time_s, or a carry's distance_m and time_s. Only sets not yet logged: earlier "
    "sets of that date are already in its session. Cardio: exactly one part, the session, with movement and minutes, "
    "and distance_km or distance_mi when the person gave a distance. Bodyweight, measurement and food take no parts: "
    "leave it out."
)
LOG_DETAILS_DESCRIPTION = (
    "The kind's own fields, as an object. Lifts: new_session, true only when the person says this is a separate "
    "workout. Cardio: incline_pct, speed_kmh, speed_mph, avg_hr, machine_kcal. Bodyweight: kg, lb. Food: entries. "
    "Measurement: kind, value. guide gives each with its units and rules: call it with the kind before sending "
    "any. Leave details out when the entry needs none of them."
)
LOG_DATE_DESCRIPTION = "The day it happened, YYYY-MM-DD, at most 2 years back. Leave out for today."
LOG_NOTES_DESCRIPTION = (
    "Lifts and cardio only: how it went, in the person's words. Up to 2,000 characters. For lifts, on a later call "
    "for the same date, they are added to the session's notes."
)
LOG_REPEAT_DESCRIPTION = (
    "true logs it even when it matches an entry of that kind from the last 10 minutes. Send it only when the "
    "person confirms it was a separate entry. Lifts need none: every call adds its sets."
)
LOG_KEY_DESCRIPTION = KEY_DESCRIPTION
# The routing sentence of the instructions, for package L2 to place.
LOG_ROUTING = "Record an entry with log. Call guide with its kind before any details. "
# A part's words by field, as LogPartIn carries them: the five of a time or a distance are written once, in
# mcp_schemas beside the model, and read back here under the same names.
PART_DESCRIPTIONS = {name: field.description for name, field in LogPartIn.model_fields.items()}
SHARED_PART_DESCRIPTIONS = {"time_s": PART_TIME_S_DESCRIPTION, "minutes": PART_MINUTES_DESCRIPTION,
                            "distance_m": PART_DISTANCE_M_DESCRIPTION, "distance_km": PART_DISTANCE_KM_DESCRIPTION,
                            "distance_mi": PART_DISTANCE_MI_DESCRIPTION}
MOVEMENT_DESCRIPTION = PART_MOVEMENT_DESCRIPTION


# ---------------------------------------------------------------- what route gives

@dataclasses.dataclass(frozen=True)
class Routed:
    """One log call, routed. kind is one of KINDS, or None when the word resolved to none. function is the kind's
    function, a key of LOG_ARGUMENTS. arguments are in that function's own shape, or None with a refusal. refusal is
    Spotter's answer for what the shape makes wrong, every problem at once, or None. word is the kind word as the call
    row may keep it (note_word)."""
    kind: str | None
    function: str | None
    arguments: dict | None
    refusal: dict | None
    word: str

    @property
    def model(self):
        """The kind's argument model, or None when no kind resolved."""
        return LOG_ARGUMENTS[self.function] if self.function else None

    @property
    def result(self):
        """The kind's result model, or None when no kind resolved."""
        return RESULTS[self.function] if self.function else None


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def _shown(value: Any) -> str:
    """A refused value as JSON writes it, cut, in single quotes when it is text."""
    if isinstance(value, str):
        return f"'{rules.quote(value)}'"
    return rules.quote(_json(value))


def key_shown(key: Any) -> str:
    """A key as a message names it: a control or format character as its escape, cut. The same as mcp_server's own
    key_shown, which this module does not import."""
    text = str(key)
    return rules.quote("".join(repr(ch)[1:-1] if unicodedata.category(ch)[0] == "C" else ch for ch in text))


def _error(code: str, message: str, fix: str, field: str) -> dict:
    return rules.error(code, message, fix, field=field)


def _words(kind: str) -> str:
    return ", ".join(PART_TAKES[kind])


def _own(kind: str) -> str:
    return ", ".join(DETAIL_FIELDS[kind])


def _near(kind: str, row: str, key: Any, taken) -> str:
    """The sentence after an unknown key's fix: the kind's own near-miss row (mcp_schemas.NEAR_MISSES), else the
    nearest field the kind takes by spelling, said as sent."""
    if not isinstance(key, str):
        return ""
    sentence = NEAR_MISSES.get(row, {}).get(key)
    if not sentence:
        close = difflib.get_close_matches(key, list(taken), n=1, cutoff=NEAR_CUTOFF) if len(key) <= rules.NAME_MAX else []
        sentence = f"The nearest field is {close[0]}." if close else ""
    return " " + said(sentence, kind) if sentence else ""


def _part_problem(kind: str, i: int, key: Any) -> dict | None:
    """A part's key the kind does not take there, or None."""
    at = f"parts[{i}].{key_shown(key)}"
    if key in PART_TAKES[kind]:
        return None
    if key in PART_FIELDS:  # another kind's part field: only a cardio part reaches here
        other = next(k for k in PART_MAP if k != kind)
        return _error("invalid_field", f"{at}: a {kind} part takes no {key}.",
                      f"A {kind} part takes: {_words(kind)}. {key} is a {other} part's field. To log {other}, send "
                      f"kind {other}.", at)
    if key == "exercise":
        return _error("invalid_field", f"{at}: a part names its lift or activity in movement.",
                      f"Send it as parts[{i}].movement.", at)
    if key in DETAIL_FIELDS[kind]:
        return _error("invalid_field", f"{at}: {key} goes in details.", f"Send it as details.{key}.", at)
    if key in TOP_FIELDS:
        return _error("invalid_field", f"{at}: {key} goes at the top of log, beside kind.",
                      f"Send {key} at the top of log, not in a part.", at)
    row = "set" if kind == "lifts" else KIND_FUNCTIONS[kind]
    return _error("invalid_field", f"parts[{i}]: '{key_shown(key)}' is not a field of a {kind} part.",
                  f"A {kind} part takes: {_words(kind)}." + _near(kind, row, key, PART_TAKES[kind]), at)


def _detail_problem(kind: str, key: Any) -> dict | None:
    """A details key the kind does not take, or None. notes goes to the top only for a kind that takes notes."""
    at = f"details.{key_shown(key)}"
    if key in DETAIL_FIELDS[kind]:
        return None
    takes = PART_TAKES.get(kind, ())
    if takes and (key in takes or key in ("exercise", "sets")):
        if kind == "lifts":
            fix = f"Send it in parts, one part a set: {_words(kind)}."
        else:
            fix = f"Send it as parts[0].{'movement' if key == 'exercise' else key}."
        return _error("invalid_field", f"{at}: {'sets go' if key == 'sets' else key + ' goes'} in a part.", fix, at)
    if key in TOP_FIELDS and (key != "notes" or kind in NOTES_KINDS):
        return _error("invalid_field", f"{at}: {key} goes at the top of log, beside kind.",
                      f"Send {key} at the top of log, not in details.", at)
    return _error("invalid_field", f"'{at}' is not a field {kind} takes in details.",
                  f"In details, {kind} takes: {_own(kind)}. guide with topic {kind} gives each."
                  + _near(kind, KIND_FUNCTIONS[kind], key, (*DETAIL_FIELDS[kind], *takes)), at)


def _number(value: Any) -> Decimal | None:
    """A number as the published Number reads one (rules.to_float): a JSON number or number text, never a bool."""
    number = rules.to_float(value)
    if number is None:
        return None
    try:
        return Decimal(repr(number))
    except InvalidOperation:
        return None


def _plain(number: Decimal) -> int | float:
    """A converted number as JSON writes it: a whole one with no point."""
    return int(number) if number == number.to_integral_value() else float(number)


def _listed(fields, last: str) -> str:
    fields = list(fields)
    return fields[0] if len(fields) == 1 else f"{', '.join(fields[:-1])} {last} {fields[-1]}"


def _convert(kind: str, i: int, part: dict, row: dict) -> list[dict]:
    """The forms of decision 51 in one part: two forms of one number refused when either is converted; each form the
    kind does not keep converted into row, in its own column. Returns the problems."""
    problems = []
    for forms in FORMS:
        sent = [f for f in forms if f in PART_TAKES[kind] and part.get(f) is not None]
        if len(sent) > 1 and any(f in CONVERTED[kind] for f in sent):
            what = "time" if forms[0] == "time_s" else "distance"
            problems.append(_error("invalid_field", f"parts[{i}] has {_listed(sent, 'and')}: send one.",
                                   f"Send the {what} once, in the field of the unit the person said: "
                                   f"{_listed((f for f in forms if f in PART_TAKES[kind]), 'or')}.", f"parts[{i}]"))
    if problems:
        return problems
    for sent, column in CONVERTED[kind].items():
        value = part.get(sent)
        if value is None:
            continue
        at = f"parts[{i}].{sent}"
        number = _number(value)
        if number is None:
            problems.append(_error("invalid_field", f"{at} must be a number, not {_shown(value)}.",
                                   f"Give {at} as a number.", at))
            continue
        converted = _CONVERSIONS[(sent, column)](number)
        if column == "time_s" and converted != converted.to_integral_value():
            problems.append(_error("invalid_field", f"{at} {rules.as_sent(value)} is not a whole number of seconds.",
                                   f"Send the time as parts[{i}].time_s, in whole seconds.", at))
            continue
        row[column] = _plain(converted)
    return problems


def _read(value: Any, shape: type) -> Any:
    """A list or an object sent as JSON text, read; anything else as sent."""
    if isinstance(value, str):
        try:
            read = json.loads(value)
        except ValueError:
            return value
        return read if isinstance(read, shape) else value
    return value


def route(arguments: Any) -> Routed:
    """A log call as sent, routed to its kind's function (Routed). Every problem the shape makes is refused at once,
    with the kind when one resolved. A lift part is a set and the one cardio part the session, movement as exercise,
    each time and distance in the kind's own column; details are the kind's own fields; date, notes, allow_repeat and
    key go on as sent, null included, but for a null notes on a kind that takes none. A measurement's own word as the
    kind fills in details.kind. Never raises."""
    arguments = arguments if isinstance(arguments, dict) else {}
    word = arguments.get("kind")
    problems = []
    for key in arguments:
        if key not in LOG_FIELDS:
            where = ("A part's numbers go in parts, a list of parts." if key in PART_FIELDS or key in ("exercise", "sets")
                     else "A kind's own fields go in details, as guide gives them.")
            problems.append(_error("invalid_field", f"'{key_shown(key)}' is not a field of log.",
                                   f"log takes: {', '.join(LOG_FIELDS)}. {where}", key_shown(key)))
    if "kind" not in arguments:
        problems.append(_error("missing_field", "log needs kind: what is logged.",
                               "Send kind, such as lifts or food. guide with topic log lists every kind.", "kind"))
        return Routed(None, None, None, rules.combine(problems), "other")
    found = resolve_kind(word)
    if found is None:
        shown = word if isinstance(word, str) else _json(word)
        near = closest_word(word)
        problems.append(_error("invalid_field", f"'{rules.quote(shown)}' is not a kind Spotter logs.",
                               "Use lifts, bodyweight, measurement, food or cardio. guide with topic log lists every "
                               "kind." + (f" The closest: {near}." if near else ""), "kind"))
        return Routed(None, None, None, rules.combine(problems), note_word(word))
    kind, measure = found
    function = KIND_FUNCTIONS[kind]
    # As the library reads a function's list or object sent as JSON text, before the checks.
    details, parts = _read(arguments.get("details"), dict), _read(arguments.get("parts"), list)
    if details is not None and not isinstance(details, dict):
        problems.append(_error("invalid_field", "details must be an object of the kind's own fields.",
                               f"Send the fields of {kind} in details, as an object: {_own(kind)}. guide with topic "
                               f"{kind} gives them.", "details"))
    if parts is not None and not isinstance(parts, list):
        fix = (f"Send parts as a list, one object a part: {_words(kind)}." if kind in PART_MAP
               else f"{kind} takes no parts: leave parts out.")
        problems.append(_error("invalid_field", "parts must be a list of parts.", fix, "parts"))
    details = dict(details) if isinstance(details, dict) else {}
    parts = parts if isinstance(parts, list) else ([] if parts is None else None)
    if parts is not None and kind not in PART_MAP and parts:
        problems.append(_error("invalid_field", f"{kind} takes no parts.",
                               f"Leave parts out and send the fields of {kind} in details: {_own(kind)}. guide with "
                               f"topic {kind} gives them.", "parts"))
    elif parts is not None and kind == "lifts" and not parts:
        problems.append(_error("missing_field", "lifts needs parts: one part a set.",
                               f"Send each set as a part, in order: {_words(kind)}.", "parts"))
    elif parts is not None and kind == "cardio" and len(parts) != 1:
        if not parts:
            problems.append(_error("missing_field", "cardio needs parts: one part, the session.",
                                   f"Send one part: {_words(kind)}.", "parts"))
        else:
            problems.append(_error("invalid_field", f"cardio takes exactly 1 part, the session: {len(parts)} were "
                                                    "sent.",
                                   f"Send one part: {_words(kind)}. Another session takes its own log call.", "parts"))
    rows = []
    for i, part in enumerate(parts if kind in PART_MAP and parts else []):
        if not isinstance(part, dict):
            problems.append(_error("invalid_field", f"parts[{i}] must be an object of named fields.",
                                   f"Send each part with its named fields: {_words(kind)}.", f"parts[{i}]"))
            continue
        found_here = [p for p in (_part_problem(kind, i, key) for key in part) if p]
        problems += found_here
        row = {PART_MAP[kind][key]: value for key, value in part.items() if key in PART_MAP[kind]}
        if not found_here:
            problems += _convert(kind, i, part, row)
        rows.append(row)
    for key in details:
        problem = _detail_problem(kind, key)
        if problem:
            problems.append(problem)
    same = isinstance(details.get("kind"), str) and MEASURE_WORDS.get(fold(details["kind"])) == measure
    if measure is not None and "kind" in details and not same:  # the same word again, or its own name, agrees
        inner = details["kind"] if isinstance(details["kind"], str) else _json(details["kind"])
        problems.append(_error("invalid_field", f"kind {rules.quote(word)} and details.kind {rules.quote(inner)} differ.",
                               "Send one measurement's kind: as the kind, or in details.kind with kind measurement.",
                               "details.kind"))
    if kind not in NOTES_KINDS and arguments.get("notes") is not None:
        problems.append(_error("invalid_field", f"{kind} takes no notes.",
                               "Leave notes out: only lifts and cardio take notes.", "notes"))
    if problems:
        return Routed(kind, function, None, rules.combine(problems), note_word(word))
    if measure is not None:
        details["kind"] = measure
    # As sent, null included: the kind's own model takes or refuses a null as its function does. Not a null notes on
    # a kind that takes none: log offers notes to every kind, and those functions take none.
    top = {k: arguments[k] for k in TOP_FIELDS if k in arguments
           and not (k == "notes" and arguments[k] is None and kind not in NOTES_KINDS)}
    if kind == "lifts":
        routed = {"sets": rows, **details, **top}
    elif kind == "cardio":
        routed = {**rows[0], **details, **top}
    else:
        routed = {**details, **top}
    return Routed(kind, function, routed, None, note_word(word))


# ---------------------------------------------------------------- a kind's own refusal, as log was sent

# Whole sentences of the kinds' refusals said for the shape, before the names below.
REFUSAL_SWAPS = {
    "lifts": (("Send one row per set, 1 to 60 rows: exercise,", "Send one part per set, 1 to 60 parts: movement,"),
              ("Give an exercise name on", "Give movement on"), (" takes: sets, ", " takes: parts, ")),
    "bodyweight": (("the body weight in kg", "the body weight in details.kg"), ("or in lb,", "or in details.lb,")),
}
# How a refusal names an item of the kind's list, and the list as log was sent it.
ITEM_WORDS = {"lifts": ("set", "parts"), "food": ("entry", "details.entries")}
_FUNCTION_NAME = re.compile(rf"\b(?:{'|'.join(LOG_FUNCTIONS)})\b")


def sent_names(kind: str) -> dict:
    """Each field of the kind's function that log's shape moves, as log was sent it."""
    names = {own: f"details.{own}" for own in DETAIL_FIELDS[kind]}
    if kind == "lifts":
        names["exercise"] = "movement"  # sets is a plain word too: as a field, a hand swap says it
    if kind == "cardio":
        names.update({own: f"parts[0].{sent}" for sent, own in PART_MAP[kind].items()})
    return names


def _name_pattern(names) -> re.Pattern:
    """A field's name where a refusal means the field: not in quotes or a longer name, not a unit after a number
    ("400 kg", "30 minutes"), not plain words ("in kg", "the minutes", "the exercise list", "a kind of")."""
    longest = "|".join(sorted(map(re.escape, names), key=len, reverse=True))
    return re.compile(rf"(?<![\w.'\[])(?<!\d )(?<!in )(?<!the )(?:{longest})(?![\w'\]])(?! list| of )")


_PATTERNS = {kind: (_name_pattern(sent_names(kind)), sent_names(kind)) for kind in KINDS}


def said(text: str, kind: str) -> str:
    """One text of a kind's refusal in the names log was sent."""
    for old, new in REFUSAL_SWAPS.get(kind, ()):
        text = text.replace(old, new)
    item = ITEM_WORDS.get(kind)
    if item:
        text = re.sub(rf"\b{item[0]} (\d+)\b", lambda m: f"{item[1]}[{int(m[1]) - 1}]", text, flags=re.I)
    text = _FUNCTION_NAME.sub("log", text)
    pattern, names = _PATTERNS[kind]
    return pattern.sub(lambda m: names[m[0]], text)


def said_as_sent(answer: Any, kind: Any) -> Any:
    """A kind's own refusal with every field named as log was sent: its message, its fix, a field when it has one,
    and the same inside errors. The code, the numbers and every other word stay. Anything else as it came."""
    if not isinstance(answer, dict) or not isinstance(kind, str) or kind not in KIND_FUNCTIONS:
        return answer

    def one(error: dict) -> dict:
        return {k: said(v, kind) if k in ("message", "fix", "field") and isinstance(v, str) else v
                for k, v in error.items()}

    out = one(answer)
    if isinstance(out.get("errors"), list):
        out["errors"] = [one(e) if isinstance(e, dict) else e for e in out["errors"]]
    return out
