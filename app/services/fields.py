"""The field list: every area get_data reads and every field in it, as data (contract data-on-request.md).

Both what_is_logged and get_data read AREAS and nothing else names an area or a field: a later area is a table, its
loader and its Field entries; a later field is a column and one Field (the scaling rule). Every rule an area or a
field has is a hook here (R9): a row's own quantity, the quantity of each total, a guard on a total, a ratio avg, the
divisor of per_day, how a filter value is matched and refused, whether a field is a default column, a group's key and
shown cell, a fan, which groups an area takes, its own days logged, its default order and the goals that ride with
it. app/services/data.py holds no area's or field's name: the words it shares with a field live here (ROW_KEYS,
FIELD_COLUMNS, PERIOD_CELLS, TIME_GROUPS, SUMMARY_AREAS).

A Row is a plain dict: date (the stored date), id, and each field's stored inputs. A loader takes (session, user,
first day, last day), either end None for open, and returns the person's live rows, deleted ones left out, an entry
dated after today counted as today's (metrics.on_or_before), in a fixed number of statements whatever the rows. Its
reads are kept on the session (session.info) for the call, so the days area and what_is_logged read each table once.

Every stored weight is kg and every distance km; a field's value is in stored units. Its column name follows the
person's units (column): weight_kg or weight_lb, distance_km or distance_mi, speed_kmh or speed_mph, pace_per_km or
pace_per_mi. Either name finds the field (field_named, R3).

INTERNAL_COLUMNS names each area table's columns no field reads, and OTHER_TABLES every table no area reads, with
why: a test holds the two and the fields' columns to app/models.py.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app import exercises, formatting, metrics, rules, users
from app.models import (
    FOOD_SOURCES, MEALS, Bodyweight, CardioEntry, Exercise, FoodEntry, LiftSession, LiftSet, Measurement, SavedFood,
    UsdaFood, User,
)
from app.services.goals import DAILY_AMOUNTS, KINDS_NOW

Row = dict

# ---------------------------------------------------------------- the words data.py shares with a field (R9)

ROW_KEYS = ("date", "id")
FIELD_COLUMNS = ("field", "meaning", "unit", "from", "totals", "filter", "group", "filled")
PERIOD_CELLS = ("days", "days_logged")
TIME_GROUPS = ("day", "week", "month")
SUMMARY_AREAS = ("lift_sessions", "cardio", "weigh_ins", "measurements", "food")  # what_is_logged's summary counts
TOTALS = ("sum", "avg", "max", "min", "count", "per_day", "share")
T = ("sum", "avg", "max", "min", "per_day", "share")  # every total beside count


# ---------------------------------------------------------------- the query a hook sees

@dataclass(frozen=True)
class Query:
    """A call's checked inputs: the area, the fields shown, the filters as matched (a field's base name to a frozenset
    of names or a bool), the groups, the totals as (field, total) in the order sent, the range, today and the
    person's units."""

    area: str
    fields: tuple[str, ...]
    filters: dict
    groups: tuple[str, ...]
    totals: tuple[tuple[str, str], ...]
    start: date
    end: date
    today: date
    units: formatting.Units


# ---------------------------------------------------------------- the two shapes

@dataclass(frozen=True)
class Field:
    name: str                 # the base name: weight_kg, distance_km, speed_kmh, pace_per_km, kcal
    meaning: str              # one line, model-read: what_is_logged gives it
    quantity: str | None      # the formatting quantity a value is shown at; None for text, ids, dates, bools
    unit: str | None          # weight, distance, speed, pace (the name and value follow the person's units),
    # a fixed unit word (kcal, min, bpm, %, g), or None
    stored: bool              # stored, or derived at read time
    totals: tuple[str, ...]   # beside count, which every field takes
    filter: bool
    group: bool
    columns: tuple[str, ...]  # the stored columns it reads, "table.column": what the tie test checks
    value: Callable[[Row], object]   # the value on a row, in stored units (kg, km, minutes)
    row_quantity: Callable[[Row], str] | None = None   # a quantity per row: measurements.value, by its unit
    ratio: tuple[str, str] | None = None   # avg as sum(first) / sum(second) over rows holding both
    per_day_by: str = "area"  # area: divide by days_logged; rows: by the days this field's rows hold (R10)
    guard: Callable[[str, Query], dict | None] | None = None   # a refusal for a total in this query, or None
    fan: Callable[[Row], list[tuple[str, float]]] | None = None   # several keys a row, each with its weight (R13)
    weighted: bool = False    # under a fanned group, sum and per_day multiply by the fan's weight, share of those
    match: Callable[[object], tuple[object, dict | None]] | None = None   # a filter value to its stored form
    default: bool = True      # a default column with no fields sent
    key: Callable[[Row], object] | None = None      # a group's key when it is not value
    shown: Callable[[list[Row]], object] | None = None   # a group's cell from its rows
    total_quantity: dict[str, str] = field(default_factory=dict)   # total to quantity; else the field's


@dataclass(frozen=True)
class Area:
    name: str
    meaning: str
    noun: tuple[str, str]     # the summary's words, one and many
    count_field: str          # the field that counts rows
    tables: tuple[str, ...]   # the tables it reads
    load: Callable[[Session, User, date | None, date | None], list[Row]]
    fields: tuple[Field, ...]
    groups: tuple[str, ...] = TIME_GROUPS   # the time groups it takes
    ranged: bool = True       # False: every live row whatever the range; its loader counts in range
    logged_days: Callable[[Session, User, date | None, date | None], set[date]] | None = None
    order: tuple[str, ...] = ("-date", "-id")   # the default order of rows
    goals: Callable[[list[dict], Query], list[dict]] = lambda numbers, query: []
    goal_kinds: tuple[str, ...] = ()   # the goal kinds its goals hook can give: no other goal is read for it
    entries_are_days: bool = False     # what_is_logged counts its days with any entry as its entries


# ---------------------------------------------------------------- names and units

_SWAPS = {"weight": ("_kg", "_lb"), "distance": ("_km", "_mi"), "speed": ("_kmh", "_mph"),
          "pace": ("_per_km", "_per_mi")}


def _converted(f: Field, units) -> bool:
    if f.unit == "weight":
        return units.weight == "lb"
    return f.unit in _SWAPS and units.distance == "mi"


def column(f: Field, units) -> str:
    """The field's name in the person's units: weight_lb on a lb account."""
    if f.unit in _SWAPS and _converted(f, units) and f.name.endswith(_SWAPS[f.unit][0]):
        return f.name[:-len(_SWAPS[f.unit][0])] + _SWAPS[f.unit][1]
    return f.name


def names_of(f: Field) -> tuple[str, ...]:
    """Every name the field answers to: its base name and, for a field in units, the other unit's (R3)."""
    if f.unit in _SWAPS and f.name.endswith(_SWAPS[f.unit][0]):
        return f.name, f.name[:-len(_SWAPS[f.unit][0])] + _SWAPS[f.unit][1]
    return (f.name,)


def field_named(area: Area, name) -> Field | None:
    """The area's field of this name, in either unit."""
    if not isinstance(name, str):
        return None
    return next((f for f in area.fields if name in names_of(f)), None)


def unit_word(f: Field, units) -> str | None:
    """The field's unit in the person's units, as what_is_logged gives it."""
    if f.unit == "weight":
        return formatting.unit_word("weight", units)
    if f.unit in ("distance", "speed"):
        return formatting.unit_word(f.unit, units)
    if f.unit == "pace":
        return "m:ss per " + formatting.unit_word("distance", units)
    return f.unit


def is_pace(f: Field) -> bool:
    """A pace is text, m:ss, and sorts by its minutes."""
    return f.unit == "pace"


# ---------------------------------------------------------------- filter values (the refusal table's words)

NAME_FIX = "Send one name or a list of names, or leave the filter out."


def as_sent(value) -> str:
    """A refused value as JSON writes it, cut by rules.quote, in single quotes when it is text: 5, 'yes', [1]."""
    if isinstance(value, str):
        return f"'{rules.quote(value)}'"
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    return rules.quote(text)


def _sent_names(where: str, value) -> tuple[list[str] | None, dict | None]:
    """A name or a list of names, or the shape refusal."""
    if isinstance(value, str):
        return [value], None
    if isinstance(value, list) and not value:
        return None, rules.error("invalid_field", f"{where} holds no names.", NAME_FIX)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value, None
    return None, rules.error("invalid_field", f"{where} takes a name or a list of names, not {as_sent(value)}.",
                             NAME_FIX)


def _each(where: str, one: Callable[[str], tuple[str | None, dict | None]]):
    """A match hook: every name through one, every refusal at once."""
    def match(value) -> tuple[frozenset | None, dict | None]:
        names, err = _sent_names(where, value)
        if err:
            return None, err
        found, errors = set(), []
        for name in names:
            hit, problem = one(name)
            if problem:
                errors.append(problem)
            else:
                found.add(hit)
        return (None, rules.combine(errors)) if errors else (frozenset(found), None)
    return match


def on_list(field_name: str, names: list[str]):
    """exercise, exercises and activity: rules.match_exercise over their list, unknown_exercise on a miss."""
    def one(name):
        hit, _ = rules.match_exercise(name, names)
        return (hit, None) if hit else (None, rules.unknown_exercise(name, names, f"filters.{field_name}: "))
    return _each(f"filters.{field_name}", one)


def one_of(field_name: str, word: str, options) -> Callable:
    """category, region, muscle and source: case and outer spaces forgiven, against their list."""
    by_key = {o.casefold(): o for o in options}

    def one(name):
        hit = by_key.get(name.strip().casefold())
        if hit:
            return hit, None
        return None, rules.error("invalid_field", f"{word} '{rules.quote(name)}' is not a {word}.",
                                 f"Use one of: {', '.join(options)}.")
    return _each(f"filters.{field_name}", one)


def _kind(name):
    return rules.match_kind(name)


def _meal(name):
    hit, err = rules.match_meal(name, "filters")
    return (hit, None) if err is None else (None, {**err, "fix": f"Use one of: {', '.join(MEALS)}."})


def true_or_false(field_name: str):
    def match(value):
        if isinstance(value, bool):
            return value, None
        return None, rules.error("invalid_field", f"filters.{field_name} takes true or false, not {as_sent(value)}.",
                                 f"Send {field_name} as true or false, or leave it out.")
    return match


# ---------------------------------------------------------------- reading the tables

def _range(column_, start: date | None, end: date | None, today: date) -> list:
    """Both ends taken, None open; an entry dated after today is today's, so a range ending today has no upper end."""
    conditions = []
    if start is not None:
        conditions.append(column_ >= start)
    if end is not None and end < today:
        conditions.append(column_ <= end)
    return conditions


def _kept(s: Session, key: tuple, read: Callable[[], object]):
    """A read kept on the session for the call, so each table is read once."""
    kept = s.info.setdefault("data_reads", {})
    if key not in kept:
        kept[key] = read()
    return kept[key]


def _lifting(s: Session, user: User, start, end) -> list[tuple]:
    """Every live session in range with its sets and lifts, in one statement: (session id, date, notes, set id, set
    number, weight, reps, lift, category), a session with no set once with nulls."""
    today = users.today_for(user)

    def read():
        return list(s.execute(
            select(LiftSession.id, LiftSession.date, LiftSession.notes, LiftSet.id, LiftSet.set_number,
                   LiftSet.weight_kg, LiftSet.reps, Exercise.name, Exercise.category)
            .outerjoin(LiftSet, LiftSet.session_id == LiftSession.id)
            .outerjoin(Exercise, Exercise.id == LiftSet.exercise_id)
            .where(LiftSession.user_id == user.id, LiftSession.deleted_at.is_(None),
                   *_range(LiftSession.date, start, end, today))
            .order_by(LiftSession.id, LiftSet.id)))
    return _kept(s, ("lifting", start, end), read)


def load_sets(s: Session, user: User, start, end) -> list[Row]:
    return [{"date": d, "id": set_id, "session_id": sid, "exercise": lift, "category": category,
             "set_number": number, "weight_kg": weight, "reps": reps}
            for sid, d, _, set_id, number, weight, reps, lift, category in _lifting(s, user, start, end)
            if set_id is not None]


def load_sessions(s: Session, user: User, start, end) -> list[Row]:
    out: dict[int, Row] = {}
    for sid, d, notes, set_id, _, weight, reps, lift, _ in _lifting(s, user, start, end):
        row = out.setdefault(sid, {"date": d, "id": sid, "notes": notes, "sets": []})
        if set_id is not None:
            row["sets"].append((lift, weight, reps))
    return list(out.values())


def load_cardio(s: Session, user: User, start, end) -> list[Row]:
    today = users.today_for(user)
    c = CardioEntry

    def read():
        return [{"date": d, "id": i, "activity": name, "category": category, "minutes": minutes, "distance_km": km,
                 "speed_kmh": kmh, "incline_pct": incline, "avg_hr": hr, "machine_kcal": machine,
                 "estimated_kcal": estimate, "estimate_weight_kg": weight, "notes": notes}
                for i, d, name, category, minutes, km, kmh, incline, hr, machine, estimate, weight, notes in s.execute(
                    select(c.id, c.date, Exercise.name, Exercise.category, c.minutes, c.distance_km, c.speed_kmh,
                           c.incline_pct, c.avg_hr, c.machine_kcal, c.estimated_kcal, c.estimate_weight_kg, c.notes)
                    .join(Exercise, Exercise.id == c.exercise_id)
                    .where(c.user_id == user.id, c.deleted_at.is_(None), *_range(c.date, start, end, today))
                    .order_by(c.id))]
    return _kept(s, ("cardio", start, end), read)


def load_weigh_ins(s: Session, user: User, start, end) -> list[Row]:
    today = users.today_for(user)

    def read():
        return [{"date": d, "id": i, "kg": kg} for i, d, kg in s.execute(
            select(Bodyweight.id, Bodyweight.date, Bodyweight.kg)
            .where(Bodyweight.user_id == user.id, Bodyweight.deleted_at.is_(None),
                   *_range(Bodyweight.date, start, end, today)).order_by(Bodyweight.id))]
    return _kept(s, ("weigh_ins", start, end), read)


def load_measurements(s: Session, user: User, start, end) -> list[Row]:
    today = users.today_for(user)
    m = Measurement

    def read():
        return [{"date": d, "id": i, "kind": kind, "value": value, "unit": unit}
                for i, d, kind, value, unit in s.execute(
                    select(m.id, m.date, m.kind, m.value, m.unit)
                    .where(m.user_id == user.id, m.deleted_at.is_(None), *_range(m.date, start, end, today))
                    .order_by(m.id))]
    return _kept(s, ("measurements", start, end), read)


def load_food(s: Session, user: User, start, end) -> list[Row]:
    today = users.today_for(user)
    e = FoodEntry

    def read():
        return [{"date": d, "id": i, "meal": meal, "description": text, "kcal": kcal, "source": source,
                 "estimated": estimated, "saved_food_id": saved, "servings": servings, "grams": grams,
                 "usda_fdc_id": fdc, "saved_name": saved_name, "usda_name": usda_name}
                for i, d, meal, text, kcal, source, estimated, saved, servings, grams, fdc, saved_name, usda_name
                in s.execute(
                    select(e.id, e.date, e.meal, e.description, e.kcal, e.source, e.estimated, e.saved_food_id,
                           e.servings, e.grams, e.usda_fdc_id, SavedFood.name, UsdaFood.description)
                    .outerjoin(SavedFood, and_(SavedFood.id == e.saved_food_id, SavedFood.user_id == e.user_id))
                    .outerjoin(UsdaFood, UsdaFood.fdc_id == e.usda_fdc_id)
                    .where(e.user_id == user.id, e.deleted_at.is_(None), *_range(e.date, start, end, today))
                    .order_by(e.id))]
    return _kept(s, ("food", start, end), read)


def load_saved_foods(s: Session, user: User, start, end) -> list[Row]:
    """Every live saved food whatever the range: times_logged counts its live entries in the range, last_logged is
    its latest live entry all time (as food._usage), and date is the day it was saved, in the person's zone."""
    today = users.today_for(user)
    zone = users.zone_for(user.time_zone)

    def read():
        e = FoodEntry
        in_range = _range(e.date, start, end, today)
        counted = func.sum(case((and_(*in_range), 1), else_=0)) if in_range else func.count()
        used = {fid: (n or 0, last) for fid, n, last in s.execute(
            select(e.saved_food_id, counted, func.max(e.date))
            .where(e.user_id == user.id, e.deleted_at.is_(None), e.saved_food_id.is_not(None))
            .group_by(e.saved_food_id))}
        return [{"date": f.created_at.astimezone(zone).date(), "id": f.id, "name": f.name,
                 "serving_label": f.serving_label, "kcal_per_serving": f.kcal_per_serving,
                 "grams_per_serving": f.grams_per_serving, "times_logged": used.get(f.id, (0, None))[0],
                 "last_logged": used.get(f.id, (0, None))[1]}
                for f in s.scalars(select(SavedFood).where(SavedFood.user_id == user.id,
                                                            SavedFood.deleted_at.is_(None)).order_by(SavedFood.id))]
    return _kept(s, ("saved_foods", start, end), read)


def saved_food_days(s: Session, user: User, start, end) -> set[date]:
    """The days in range with a food entry of a saved food."""
    today = users.today_for(user)
    return {metrics.on_or_before(r["date"], today) for r in load_food(s, user, start, end)
            if r["saved_food_id"] is not None}


def _first_day(s: Session, user: User) -> date | None:
    """The earliest day any entry is dated, all time."""
    days = [r["date"] for load in (load_sessions, load_cardio, load_food, load_weigh_ins, load_measurements)
            for r in load(s, user, None, None)]
    return min(days) if days else None


def load_days(s: Session, user: User, start, end) -> list[Row]:
    """One row a day of the range, empty days included, from the other areas' loaders. Left open, the range runs
    from the first day anything is dated to today."""
    today = users.today_for(user)
    first = _first_day(s, user) if start is None else start
    last = today if end is None else end
    if first is None or first > last:
        return []
    out = {first + timedelta(days=n): {"date": first + timedelta(days=n), "id": None, "lift_sessions": 0,
                                       "lift_sets": 0, "volume_kg": 0, "cardio_entries": 0, "cardio_minutes": 0,
                                       "food_entries": 0, "kcal": None, "weigh_in": None, "measurements": 0}
           for n in range((last - first).days + 1)}

    def day(row) -> Row | None:
        return out.get(metrics.on_or_before(row["date"], today))

    for row in load_sessions(s, user, start, end):
        if (d := day(row)) is not None:
            d["lift_sessions"] += 1
            d["lift_sets"] += len(row["sets"])
            d["volume_kg"] += sum(w * r for _, w, r in row["sets"])
    for row in load_cardio(s, user, start, end):
        if (d := day(row)) is not None:
            d["cardio_entries"] += 1
            d["cardio_minutes"] += row["minutes"]
    for row in load_food(s, user, start, end):
        if (d := day(row)) is not None:
            d["food_entries"] += 1
            d["kcal"] = (d["kcal"] or 0) + int(row["kcal"])
    for row in sorted(load_weigh_ins(s, user, start, end), key=lambda r: (r["date"], r["id"])):
        if (d := day(row)) is not None:
            d["weigh_in"] = row["kg"]  # the day's live weigh-in; the latest when a later-dated one meets today's
    for row in load_measurements(s, user, start, end):
        if (d := day(row)) is not None:
            d["measurements"] += 1
    return list(out.values())


def _any_entry(row: Row) -> bool:
    return bool(row["lift_sessions"] or row["cardio_entries"] or row["food_entries"] or row["measurements"]
                or row["weigh_in"] is not None)


def entry_days(s: Session, user: User, start, end) -> set[date]:
    """The days with any entry."""
    return {r["date"] for r in load_days(s, user, start, end) if _any_entry(r)}


# ---------------------------------------------------------------- the goals that ride with the data

LIFT_CATEGORY = dict(exercises.LIFTS)
CARDIO_CATEGORY = {name: category for name, category, _, _ in exercises.CARDIO}


def _lift_passes(name: str | None, filters: dict) -> bool:
    """A lift goal rides when its lift passes every lift filter sent: a muscle at either weight (R13)."""
    checks = {"exercise": lambda: name, "exercises": lambda: name, "category": lambda: LIFT_CATEGORY.get(name),
              "region": lambda: exercises.REGIONS.get(name)}
    for key, value_of in checks.items():
        if key in filters and value_of() not in filters[key]:
            return False
    return "muscle" not in filters or bool(set(exercises.MUSCLES.get(name, {})) & filters["muscle"])


def lift_goals(numbers: list[dict], query: Query) -> list[dict]:
    return [n for n in numbers if n["kind"] == "lift" and _lift_passes(n["exercise"], query.filters)]


def cardio_goals(numbers: list[dict], query: Query) -> list[dict]:
    f = query.filters
    return [n for n in numbers if n["kind"] == "cardio_best"
            and ("activity" not in f or n["exercise"] in f["activity"])
            and ("category" not in f or CARDIO_CATEGORY.get(n["exercise"]) in f["category"])]


def weight_goals(numbers: list[dict], query: Query) -> list[dict]:
    return [n for n in numbers if n["kind"] == "bodyweight"]


def measurement_goals(numbers: list[dict], query: Query) -> list[dict]:
    return [n for n in numbers if n["kind"] == "measurement"
            and ("kind" not in query.filters or n["measurement_kind"] in query.filters["kind"])]


def every_goal(numbers: list[dict], query: Query) -> list[dict]:
    return list(numbers)


# ---------------------------------------------------------------- the rules of measurements.value

DAILY = frozenset(DAILY_AMOUNTS)
DAILY_TEXT = "steps, sleep_hours or water_ml"


def value_guard(total: str, query: Query) -> dict | None:
    """value's totals need one kind; sum, per_day and share need a day's amount (steps, sleep_hours, water_ml).
    share needs a one-kind filter even under group_by kind: a share across kinds would be a percent of mixed units.
    count is never guarded."""
    if total == "count":
        return None
    kinds = query.filters.get("kind")
    grouped = "kind" in query.groups
    if not grouped and (kinds is None or len(kinds) != 1):
        return rules.error("invalid_field", "value holds more than one kind, so its totals mix units.",
                           "Filter on one kind, such as {\"kind\": \"waist\"}, or send group_by kind.")
    if total not in ("sum", "per_day", "share"):
        return None
    if grouped:
        if kinds is None or not kinds <= DAILY:
            return rules.error("invalid_field", f"value's {total} needs a kind filter of {DAILY_TEXT}: grouped by "
                                                "kind, it could take in a kind that is not a day's amount.",
                               f"Filter kind to {DAILY_TEXT}, or use avg, max or min.")
        if total == "share" and len(kinds) != 1:  # several day's amounts: a share across them mixes units
            return rules.error("invalid_field", "value's share needs one kind: it is a part of one kind's total.",
                               "Filter kind to one kind, such as {\"kind\": \"steps\"}, or leave share out.")
        return None
    (kind,) = kinds
    if kind not in DAILY:
        return rules.error("invalid_field", f"{kind} takes no {total}: it is not a day's amount.",
                           f"Use avg, max or min for {kind}. sum and per_day are for steps, sleep_hours and water_ml.")
    return None


# ---------------------------------------------------------------- small value hooks

def _at(key: str) -> Callable[[Row], object]:
    return lambda row: row.get(key)


def _one(row: Row) -> int:
    return 1


def _volume(row: Row) -> float | None:
    return None if row["weight_kg"] is None or row["reps"] is None else row["weight_kg"] * row["reps"]


def _primary(row: Row) -> str | None:
    return next((m for m, w in exercises.MUSCLES.get(row["exercise"], {}).items() if w == 1), None)


def _muscles(row: Row) -> list[tuple[str, float]]:
    return list(exercises.MUSCLES.get(row["exercise"], {}).items())


def _lifts_in(row: Row) -> list[str]:
    return list(dict.fromkeys(lift for lift, _, _ in row["sets"]))


def _pace(row: Row) -> float | None:
    return row["minutes"] / row["distance_km"] if row["distance_km"] else None


def _food_name(row: Row) -> str:
    return row["saved_name"] or row["usda_name"] or row["description"]


def _food_key(row: Row) -> tuple:
    if row["saved_food_id"] is not None:
        return "saved", row["saved_food_id"]
    if row["usda_fdc_id"] is not None:
        return "usda", row["usda_fdc_id"]
    return "text", " ".join(row["description"].split()).casefold()


def _food_shown(rows: list[Row]) -> str:
    """The saved food's name, else the cached USDA description, else the description on the group's latest entry."""
    latest = max(rows, key=lambda r: (r["date"], r["id"]))
    return latest["saved_name"] or latest["usda_name"] or latest["description"]


def _measurement_quantity(row: Row) -> str:
    return rules.UNIT_QUANTITIES[row["unit"]]


LIFT_NAMES = [name for name, _ in exercises.LIFTS]
CARDIO_NAMES = [name for name, _, _, _ in exercises.CARDIO]
AVG = {"avg": "count_avg", "per_day": "count_avg"}
SUM = {"sum": "count_avg", "per_day": "count_avg"}


# ---------------------------------------------------------------- the areas, in what_is_logged's order

LIFT_SETS = Area(
    "lift_sets", "One set of a lift, in a live session.", ("set", "sets"), "sets",
    ("lift_set", "lift_session", "exercise"), load_sets, (
        Field("date", "The day trained.", None, None, True, (), False, False, ("lift_session.date",), _at("date")),
        Field("session_id", "The session's id, for edit_entry with kind lift_session.", None, None, True, (), False,
              False, ("lift_set.session_id",), _at("session_id")),
        Field("exercise", "The lift, as list_exercises names it.", None, None, True, (), True, True,
              ("lift_set.exercise_id", "exercise.name"), _at("exercise"), match=on_list("exercise", LIFT_NAMES)),
        Field("category", "The lift's movement: squat, hinge, push, pull, carry, core or accessory.", None, None, True,
              (), True, True, ("exercise.category",), _at("category"),
              match=one_of("category", "category", exercises.CATEGORIES)),
        Field("region", "The body region the lift trains: upper, lower, core or full_body.", None, None, False, (),
              True, True, (), lambda row: exercises.REGIONS.get(row["exercise"]),
              match=one_of("region", "region", exercises.REGION_NAMES)),
        Field("muscle", "A muscle the lift works, as free-exercise-db names it, such as chest or triceps. As a column, "
                        "the lift's primary muscle. Grouped by, a set counts 1 for each primary muscle and 0.5 for "
                        "each secondary, in sets and volume. Null for Suitcase Carry.", None, None, False, (), True,
              True, (), _primary, fan=_muscles, match=one_of("muscle", "muscle", exercises.MUSCLE_NAMES),
              default=False),
        Field("set_number", "The set's number within its lift in the session, from 1.", "count", None, True, (), False,
              False, ("lift_set.set_number",), _at("set_number")),
        Field("weight_kg", "The weight lifted. 0 for bodyweight work.", "weight_kg", "weight", True,
              ("avg", "max", "min"), False, False, ("lift_set.weight_kg",), _at("weight_kg")),
        Field("reps", "Reps in the set.", "reps", None, True, ("sum", "avg", "max", "min", "per_day"), False, False,
              ("lift_set.reps",), _at("reps"), total_quantity=AVG),
        Field("estimated_max_kg", "Epley's estimated one-rep max: weight times 1 plus reps over 30. Null outside 1 to "
                                  "10 reps or at 0 kg.", "estimated_max", "weight", False, ("avg", "max", "min"),
              False, False, (), lambda row: metrics.epley(row["weight_kg"], row["reps"])),
        Field("volume_kg", "weight x reps. A 0 kg set counts 0.", "volume", "weight", False, T, False, False, (),
              _volume, weighted=True),
        Field("sets", "One for each set: count it, or sum it for sets weighted by muscle.", "count", None, False,
              ("sum", "per_day", "share"), False, False, (), _one, weighted=True, total_quantity=SUM),
    ),
    goals=lift_goals, goal_kinds=("lift",))

LIFT_SESSIONS = Area(
    "lift_sessions", "One live lift session.", ("session", "sessions"), "sessions",
    ("lift_session", "lift_set", "exercise"), load_sessions, (
        Field("date", "The day trained.", None, None, True, (), False, False, ("lift_session.date",), _at("date")),
        Field("id", "The session's id, for edit_entry with kind lift_session.", None, None, True, (), False, False,
              ("lift_session.id",), _at("id")),
        Field("exercises", "The lifts in it, in the order done. A filter keeps the sessions that hold any of them.",
              None, None, False, (), True, False, ("lift_set.exercise_id", "exercise.name"),
              lambda row: ", ".join(_lifts_in(row)) or None, fan=lambda row: [(n, 1.0) for n in _lifts_in(row)],
              match=on_list("exercises", LIFT_NAMES)),
        Field("sets", "Sets in it.", "count", None, False, ("sum", "avg", "max", "min", "per_day"), False, False,
              ("lift_set.id",), lambda row: len(row["sets"]), total_quantity=AVG),
        Field("volume_kg", "weight x reps over its sets.", "volume", "weight", False, T, False, False,
              ("lift_set.weight_kg", "lift_set.reps"), lambda row: sum(w * r for _, w, r in row["sets"])),
        Field("notes", "The person's notes.", None, None, True, (), False, False, ("lift_session.notes",),
              _at("notes")),
        Field("sessions", "One for each session: count it.", "count", None, False, ("share",), False, False, (), _one,
              total_quantity={"per_day": "count_avg"}),
    ),
    goals=lift_goals, goal_kinds=("lift",))

CARDIO = Area(
    "cardio", "One live cardio entry.", ("entry", "entries"), "entries", ("cardio_entry", "exercise"), load_cardio, (
        Field("date", "The day.", None, None, True, (), False, False, ("cardio_entry.date",), _at("date")),
        Field("id", "The entry's id, for edit_entry with kind cardio_entry.", None, None, True, (), False, False,
              ("cardio_entry.id",), _at("id")),
        Field("activity", "The activity, as list_exercises names it.", None, None, True, (), True, True,
              ("cardio_entry.exercise_id", "exercise.name"), _at("activity"),
              match=on_list("activity", CARDIO_NAMES)),
        Field("category", "walk, run, cycle, row, swim, machine or other.", None, None, True, (), True, False,
              ("exercise.category",), _at("category"),
              match=one_of("category", "category", exercises.CARDIO_CATEGORIES)),
        Field("minutes", "How long.", "minutes", "min", True, T, False, False, ("cardio_entry.minutes",),
              _at("minutes")),
        Field("distance_km", "How far, when given.", "km", "distance", True, T, False, False,
              ("cardio_entry.distance_km",), _at("distance_km")),
        Field("pace_per_km", "Minutes per distance as m:ss. Its avg is total minutes over total distance. Its min is "
                             "the fastest.", "pace", "pace", False, ("avg", "max", "min"), False, False, (), _pace,
              ratio=("minutes", "distance_km")),
        Field("speed_kmh", "Speed, as given or worked out from distance and minutes.", "speed", "speed", True,
              ("max", "min"), False, False, ("cardio_entry.speed_kmh",), _at("speed_kmh")),
        Field("incline_pct", "Incline, when given.", "incline", "%", True, ("avg", "max", "min"), False, False,
              ("cardio_entry.incline_pct",), _at("incline_pct")),
        Field("avg_hr", "Average heart rate, when given.", "bpm", "bpm", True, ("avg", "max", "min"), False, False,
              ("cardio_entry.avg_hr",), _at("avg_hr"), total_quantity={"avg": "count_avg"}),
        Field("machine_kcal", "The machine's or watch's calories, when given.", "kcal", "kcal", True, T, False, False,
              ("cardio_entry.machine_kcal",), _at("machine_kcal")),
        Field("estimated_kcal", "Spotter's estimate: MET x 3.5 x body weight / 200 x minutes.", "kcal", "kcal", True, T,
              False, False, ("cardio_entry.estimated_kcal",), _at("estimated_kcal")),
        Field("estimate_weight_kg", "The body weight the estimate used.", "bodyweight_derived", "weight", True, (),
              False, False, ("cardio_entry.estimate_weight_kg",), _at("estimate_weight_kg")),
        Field("notes", "The person's notes.", None, None, True, (), False, False, ("cardio_entry.notes",),
              _at("notes")),
        Field("entries", "One for each entry: count it.", "count", None, False, ("per_day", "share"), False, False, (),
              _one, total_quantity={"per_day": "count_avg"}),
    ),
    goals=cardio_goals, goal_kinds=("cardio_best",))

WEIGH_INS = Area(
    "weigh_ins", "One live weigh-in: at most one a day.", ("weigh-in", "weigh-ins"), "weigh_ins", ("bodyweight",),
    load_weigh_ins, (
        Field("date", "The day.", None, None, True, (), False, False, ("bodyweight.date",), _at("date")),
        Field("id", "The weigh-in's id, for edit_entry with kind bodyweight.", None, None, True, (), False, False,
              ("bodyweight.id",), _at("id")),
        Field("weight_kg", "Body weight.", "weigh_in", "weight", True, ("avg", "max", "min"), False, False,
              ("bodyweight.kg",), _at("kg")),
        Field("weigh_ins", "One for each weigh-in: count it.", "count", None, False, (), False, False, (), _one),
    ),
    goals=weight_goals, goal_kinds=("bodyweight",))

MEASUREMENTS = Area(
    "measurements", "One live measurement: at most one a kind a day.", ("measurement", "measurements"), "values",
    ("measurement",), load_measurements, (
        Field("date", "The day.", None, None, True, (), False, False, ("measurement.date",), _at("date")),
        Field("id", "The measurement's id, for edit_entry with kind measurement.", None, None, True, (), False, False,
              ("measurement.id",), _at("id")),
        Field("kind", "waist, hips, chest, arm, thigh, steps, sleep_hours, resting_hr or water_ml.", None, None, True,
              (), True, True, ("measurement.kind",), _at("kind"), match=_each("filters.kind", _kind)),
        Field("value", "The value, in unit. Its totals need one kind. sum, per_day and share only for steps, "
                       "sleep_hours and water_ml. Its per_day divides by the days with that kind.", None, None, True, T,
              False, False, ("measurement.value",), _at("value"), row_quantity=_measurement_quantity,
              per_day_by="rows", guard=value_guard),
        Field("unit", "The kind's unit: cm, steps, h, bpm or ml.", None, None, True, (), False, False,
              ("measurement.unit",), _at("unit")),
        Field("values", "One for each measurement: count it.", "count", None, False, (), False, False, (), _one),
    ),
    goals=measurement_goals, goal_kinds=("measurement",))

FOOD = Area(
    "food", "One live food entry.", ("entry", "entries"), "entries", ("food_entry", "saved_food", "usda_food"),
    load_food, (
        Field("date", "The day eaten.", None, None, True, (), False, False, ("food_entry.date",), _at("date")),
        Field("id", "The entry's id, for edit_entry with kind food_entry.", None, None, True, (), False, False,
              ("food_entry.id",), _at("id")),
        Field("meal", "breakfast, lunch, dinner or snack.", None, None, True, (), True, True, ("food_entry.meal",),
              _at("meal"), match=_each("filters.meal", _meal)),
        Field("description", "What was eaten, as written.", None, None, True, (), False, False,
              ("food_entry.description",), _at("description")),
        Field("food", "The saved or USDA food's name, else the description: what group_by food groups on.", None,
              None, False, (), False, True, ("saved_food.name", "usda_food.description"), _food_name, key=_food_key,
              shown=_food_shown),
        Field("kcal", "Calories, whole.", "kcal", "kcal", True, T, False, False, ("food_entry.kcal",),
              lambda row: int(row["kcal"])),
        Field("source", "Who gave the number: you_said, saved_food, usda or claude_estimate.", None, None, True, (),
              True, False, ("food_entry.source",), _at("source"), match=one_of("source", "source", FOOD_SOURCES)),
        Field("estimated", "true when the number is an estimate.", None, None, True, (), True, False,
              ("food_entry.estimated",), _at("estimated"), match=true_or_false("estimated")),
        Field("saved_food_id", "The saved food it came from, or null.", None, None, True, (), False, False,
              ("food_entry.saved_food_id",), _at("saved_food_id")),
        Field("servings", "Servings of the saved food.", "servings", None, True, ("sum", "avg"), False, False,
              ("food_entry.servings",), _at("servings")),
        Field("grams", "Grams of the USDA food.", "grams", "g", True, ("sum", "avg"), False, False,
              ("food_entry.grams",), _at("grams")),
        Field("usda_fdc_id", "The USDA food's id, or null.", None, None, True, (), False, False,
              ("food_entry.usda_fdc_id",), _at("usda_fdc_id")),
        Field("entries", "One for each entry: count it.", "count", None, False, ("per_day", "share"), False, False, (),
              _one, total_quantity={"per_day": "count_avg"}),
    ))

SAVED_FOODS = Area(
    "saved_foods", "One live saved food. The range counts its use.", ("saved food", "saved foods"), "foods",
    ("saved_food", "food_entry"), load_saved_foods, (
        Field("id", "The saved food's id, for log_food.", None, None, True, (), False, False, ("saved_food.id",),
              _at("id")),
        Field("name", "Its name.", None, None, True, (), False, False, ("saved_food.name",), _at("name")),
        Field("serving_label", "What one serving is.", None, None, True, (), False, False,
              ("saved_food.serving_label",), _at("serving_label")),
        Field("kcal_per_serving", "Calories in one serving.", "kcal_per_serving", "kcal", True, ("avg", "max", "min"),
              False, False, ("saved_food.kcal_per_serving",), _at("kcal_per_serving")),
        Field("grams_per_serving", "Grams in one serving, or null.", "grams", "g", True, (), False, False,
              ("saved_food.grams_per_serving",), _at("grams_per_serving")),
        Field("times_logged", "Live entries of it in the range.", "count", None, False, ("sum", "max"), False, False,
              (), _at("times_logged"), total_quantity=AVG),
        Field("last_logged", "The day of its latest live entry, or null.", None, None, False, (), False, False, (),
              _at("last_logged")),
        Field("foods", "One for each saved food: count it.", "count", None, False, (), False, False, (), _one),
    ),
    groups=(), ranged=False, logged_days=saved_food_days, order=("-times_logged", "name"))

DAYS = Area(
    "days", "One row a day of the range, empty days included.", ("day", "days"), "days", (), load_days, (
        Field("date", "The day.", None, None, False, (), False, False, (), _at("date")),
        Field("lift_sessions", "Lift sessions that day.", "count", None, False, ("sum", "avg", "max"), False, False,
              (), _at("lift_sessions"), total_quantity=AVG),
        Field("lift_sets", "Sets that day.", "count", None, False, ("sum", "avg", "max"), False, False, (),
              _at("lift_sets"), total_quantity=AVG),
        Field("volume_kg", "weight x reps that day.", "volume", "weight", False, ("sum", "avg", "max"), False, False,
              (), _at("volume_kg")),
        Field("cardio_entries", "Cardio entries that day.", "count", None, False, ("sum", "avg", "max"), False, False,
              (), _at("cardio_entries"), total_quantity=AVG),
        Field("cardio_minutes", "Cardio minutes that day.", "minutes", "min", False, ("sum", "avg", "max"), False,
              False, (), _at("cardio_minutes")),
        Field("food_entries", "Food entries that day.", "count", None, False, ("sum", "avg", "max"), False, False, (),
              _at("food_entries"), total_quantity=AVG),
        Field("kcal", "Calories that day, or null with no food.", "kcal", "kcal", False, ("sum", "avg", "max", "min"),
              False, False, (), _at("kcal")),
        Field("weight_kg", "The day's weigh-in, or null.", "weigh_in", "weight", False, ("avg", "max", "min"), False,
              False, (), _at("weigh_in")),
        Field("measurements", "Measurements that day.", "count", None, False, ("sum",), False, False, (),
              _at("measurements"), total_quantity=AVG),
        Field("days", "One for each day: count it.", "count", None, False, (), False, False, (), _one),
    ),
    groups=("week", "month"), logged_days=entry_days, order=("-date",), goals=every_goal,
    goal_kinds=KINDS_NOW, entries_are_days=True)

AREAS: dict[str, Area] = {a.name: a for a in (LIFT_SETS, LIFT_SESSIONS, CARDIO, WEIGH_INS, MEASUREMENTS, FOOD,
                                               SAVED_FOODS, DAYS)}

_ENTRY = ("user_id", "dedupe_key", "logged_by", "created_at", "updated_at", "deleted_at")
INTERNAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "lift_session": _ENTRY, "bodyweight": _ENTRY, "measurement": _ENTRY, "food_entry": _ENTRY, "cardio_entry": _ENTRY,
    "lift_set": ("id",),
    "saved_food": ("user_id", "name_key", "created_at", "updated_at", "deleted_at"),
    "exercise": ("id", "kind", "met", "compendium_code"),
    "usda_food": ("fdc_id", "data_type", "kcal_per_100g", "fetched_at"),
}
OTHER_TABLES: dict[str, str] = {
    "user": "The settings: whoami gives them.",
    "goal": "get_goals reads goals; the open ones ride along with the data.",
    "routine": "get_routines reads them.",
    "call_log": "Internal: what calls did.",
    "write_key": "Internal: a write's own key.",
    "entry_version": "Internal: an entry as it stood before an edit.",
    "feedback": "Internal: the notes people send Spotter's maker.",
    "usda_search": "Internal: the USDA lookup's cache.",
}
