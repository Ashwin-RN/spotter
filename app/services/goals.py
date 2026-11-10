"""Goals: something to reach by a date. Four kinds, one shape.

set_goal(user_id, data, by)
    A new goal of one of four kinds (KINDS_NOW). Every kind needs target_value and target_date
    (after today); text is optional, up to 2,000 characters.
      lift         exercise (a lift name). target_value: an estimated max in kg, above 0 to LIFT_MAX (the
                   estimated max of the heaviest weight the rules allow at 10 reps).
      bodyweight   target_value: kg, 20 to 400.
      measurement  measurement_kind: waist, hips, chest, arm, thigh or resting_hr. target_value in that
                   kind's unit and range (rules.MEASUREMENTS; resting_hr whole). steps, sleep_hours and
                   water_ml are refused: they are a day's amount, met or missed each day, not a state to
                   reach by a date.
      cardio_best  exercise (a cardio activity) and distance_km (0.1 to 300, 2 dp). target_value: minutes
                   for that distance, 1 to 600, 1 dp. A session counts when its distance is at or over
                   the goal's; its time for the goal distance is its pace x the goal distance.
    A weekly sessions or cardio minutes target is a setting (update_settings), not a goal.

    When a goal is created, Spotter stores where it starts and which way it points:
      start_value  where the person is now (_now_value), the same measure the goal's "latest" uses:
                   a lift: the best session estimated max in the last 28 days, else the latest
                     (metrics.recent_best).
                   body weight: the 7-day average, else the latest weigh-in (metrics.bodyweight_now).
                   a measurement: the latest live value of that kind, in the one order.
                   a cardio best: the best (lowest) qualifying time in the last 28 days, else the latest.
                   With nothing logged yet, start_value must be given, in the goal's unit.
      start_date   today.
      direction    a lift: up, so the target must be above the start. A cardio best: down, so below it.
                   Body weight and a measurement: down when the target is below the start, up when
                   above; a target equal to the start is refused.
      unit         kg, cm or bpm (the measurement's own), or min.
    The direction is never worked out again. An edit that would point the target the other way is
    refused: drop the goal and set a new one. A second open goal for the same thing (kind, exercise,
    measurement kind, distance) is warned about, not refused.

    With goal_id: one of the person's own goals. status (open, hit, dropped), target_value,
    target_date and text change; kind, exercise, measurement_kind and distance_km cannot. Closing a
    goal sets closed_at; reopening clears it.

get_goals(user_id, include_closed=False)
    A read. Every open goal's numbers (goal_numbers), by id; with include_closed, the closed goals in
    a short form too.

load_numbers(s, user_id, today, goals, lift_history=None) -> dict
    Reads what the kinds among `goals` need, and nothing else: lift history, weigh-ins, measurements
    of the kinds named, cardio sessions of the activities named. set_goal, get_goals and get_progress
    all read through it, so the three never disagree.

goal_numbers(goals, loaded, today)
    For each open goal with a target and a date: where it stands, in one shape for every kind.
    latest is the measure above, and status, needed_per_week, the projection and projected_date all
    read that same value: "reached" once latest is at or past the target, else "on track" or "behind"
    from the line, else "not enough data".

Every write here locks the user row first (common.lock_user), then checks the write limit
(common.check_write_limit: 120 writes in 10 minutes), before any other read or work.
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import db, links, metrics, rules, users
from app.models import GOAL_STATUSES, CardioEntry, Goal, Measurement, User
from app.services import body, cardio, lifts
from app.services.common import (
    SAMPLE_NOTE, check_by, check_data, check_write_limit, exercises, in_range, lock_user, no_account, not_found,
    owned, row_dict, truthy,
)

KIND = "goal"
KINDS_NOW = ("lift", "bodyweight", "measurement", "cardio_best")
SETTINGS_KINDS = ("sessions", "cardio_weekly", "calories")  # weekly or daily amounts: settings, not goals
GOAL_MEASUREMENTS = ("waist", "hips", "chest", "arm", "thigh", "resting_hr")
DAILY_AMOUNTS = ("steps", "sleep_hours", "water_ml")
# The highest estimated max the rules allow: the heaviest weight at the most reps Epley counts.
LIFT_MAX = metrics.epley(rules.HARD_RULES["weight_kg"][1], metrics.EPLEY_MAX_REPS)
TARGETS = {"lift": (0, LIFT_MAX), "bodyweight": rules.BODYWEIGHT_KG, "cardio_best": (1, 600)}  # a lift: above 0
DISTANCE_KM = (0.1, 300)
UNITS = {"lift": "kg", "bodyweight": "kg", "cardio_best": "min"}  # a measurement: its kind's own unit
UNIT = "kg"
NAMES = {"resting_hr": "Resting heart rate"}
WITH_EXERCISE = ("lift", "cardio_best")

GOALS_HOW = (
    "Where each goal is now (latest), the same measure its start used: a lift, the best session estimated max "
    f"(Epley) in the last {metrics.RECENT_BEST_DAYS} days, else the latest session; body weight, the 7-day "
    "average, else the latest weigh-in; a measurement, its latest value; a cardio best, the best (lowest) time "
    f"in the last {metrics.RECENT_BEST_DAYS} days, else the latest, from sessions at or over the goal's distance, "
    "each one's pace x the goal distance. needed_per_week is the steady change from latest that reaches the "
    f"target by its date; actual_per_week is the slope of a straight line through the last "
    f"{metrics.PROJECTION_DAYS // 7} weeks ({metrics.PROJECTION_MIN_POINTS} points needed); both are in the goal's "
    "unit, signed. projected_date is the day that line reaches the target: null when it points away, is flat, or "
    "gets there more than 2 years out. status: reached, on track (the line gets there by the date), behind, or "
    "not enough data. Quote these numbers; do not work out your own.")


# ---------------------------------------------------------------- the kinds, in one place

def unit_for(kind: str, measurement_kind: str | None = None) -> str:
    if kind == "measurement":
        return rules.MEASUREMENTS[measurement_kind][0]
    return UNITS.get(kind, UNIT)


def measurement_name(kind: str) -> str:
    """'Waist', 'Resting heart rate'."""
    return NAMES.get(kind, kind.replace("_", " ").capitalize())


def clock(minutes: float) -> str:
    """Minutes as m:ss: 28 is "28:00", 27.5 is "27:30"."""
    seconds = round(minutes * 60)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _limits(kind: str, measurement_kind: str | None) -> tuple[float, float, bool, bool, int]:
    """(lowest, highest, lowest left out, whole numbers only, decimals) for a goal value of this kind."""
    if kind == "measurement":
        _, lo, hi, whole = rules.MEASUREMENTS[measurement_kind]
        return lo, hi, False, whole, 0 if whole else 2
    lo, hi = TARGETS[kind]
    return lo, hi, kind == "lift", False, 1 if kind == "cardio_best" else 2


def _what(kind: str, measurement_kind: str | None) -> str:
    """How messages name the kind: 'a lift goal', 'a waist goal'."""
    if kind == "measurement":
        return f"a {measurement_name(measurement_kind).lower()} goal"
    return f"a {kind} goal"


def _value(value, field: str, kind: str, measurement_kind: str | None) -> tuple[float | None, dict | None]:
    """target_value or start_value, in the goal's unit and range."""
    lo, hi, low_open, whole, places = _limits(kind, measurement_kind)
    unit = unit_for(kind, measurement_kind)
    span = f"above {lo:g} to {hi:g}" if low_open else f"{lo:g} to {hi:g}"
    fix = (f"Give {field} in {unit}, {span}{', a whole number' if whole else ''}, for "
           f"{_what(kind, measurement_kind)}.")
    if kind == "cardio_best":
        fix = f"Give {field} as the minutes for the goal's distance, {span}, for a cardio_best goal."
    number, err = in_range(value, field, lo, hi, fix, low_open=low_open)
    if err:
        return None, err
    if whole and number != int(number):
        return None, rules.error("invalid_field", f"{field} must be a whole number, not {number:g}.", fix)
    return round(number, places) + 0.0, None


# ---------------------------------------------------------------- reading what the goals need

NO_LIFTS = {"points": {}, "sessions": []}


def load_numbers(s, user_id: int, today: date, goals: list, lift_history: dict | None = None) -> dict:
    """What the goals' kinds need, read once. goals: Goal rows, or unsaved ones (a goal being set).
    lift_history: lifts.history when the caller has it already, so it is not read twice.

    Returns {lifts: lifts.history, weigh_ins: [Bodyweight], measurements: {kind: [Measurement]},
    cardio: {exercise_id: [CardioEntry with a distance]}, sample: bool}, every list in the one order.
    sample: whether any row read is sample data."""
    kinds = {g.kind for g in goals}
    if lift_history is None:
        lift_history = lifts.history(s, user_id, today) if "lift" in kinds else NO_LIFTS
    out = {"lifts": lift_history, "weigh_ins": [], "measurements": {}, "cardio": {}}
    if "bodyweight" in kinds:
        out["weigh_ins"] = body.weigh_ins(s, user_id, today)
    wanted = {g.measurement_kind for g in goals if g.kind == "measurement" and g.measurement_kind}
    if wanted:
        rows = s.scalars(select(Measurement).where(Measurement.user_id == user_id, Measurement.deleted_at.is_(None),
                                                   Measurement.kind.in_(wanted)))
        for m in sorted(rows, key=lambda m: metrics.order_key(m.date, m.id, today)):
            out["measurements"].setdefault(m.kind, []).append(m)
    activities = {g.exercise_id for g in goals if g.kind == "cardio_best" and g.exercise_id}
    if activities:
        rows = s.scalars(select(CardioEntry).where(
            CardioEntry.user_id == user_id, CardioEntry.deleted_at.is_(None), CardioEntry.exercise_id.in_(activities),
            CardioEntry.distance_km.is_not(None)))
        for c in sorted(rows, key=lambda c: metrics.order_key(c.date, c.id, today)):
            out["cardio"].setdefault(c.exercise_id, []).append(c)
    used = [*out["weigh_ins"], *(m for ms in out["measurements"].values() for m in ms),
            *(c for cs in out["cardio"].values() for c in cs)]
    if "lift" in kinds:
        used += lift_history["sessions"]
    out["sample"] = any(row.is_sample for row in used)
    return out


def cardio_times(rows: list, distance_km: float) -> list[tuple[date, float]]:
    """(date, minutes for distance_km) for each session at or over that distance: its pace x the
    distance, 2 dp. A shorter session says nothing about a longer time and is left out."""
    return [(c.date, round(c.minutes / c.distance_km * distance_km, 2)) for c in rows
            if c.distance_km and c.distance_km >= distance_km]


def _points(g, loaded: dict, today: date) -> list[tuple[date, float]]:
    """The goal's own series, in the one order."""
    if g.kind == "lift":
        return loaded["lifts"]["points"].get(g.exercise.name, [])
    if g.kind == "bodyweight":
        return [(b.date, b.kg) for b in loaded["weigh_ins"]]
    if g.kind == "measurement":
        return [(m.date, m.value) for m in loaded["measurements"].get(g.measurement_kind, [])]
    return cardio_times(loaded["cardio"].get(g.exercise_id, []), g.distance_km)


def _now_value(kind: str, points: list, today: date) -> float | None:
    """Where the person is now, the one measure a goal starts from and is compared with."""
    if kind == "lift":
        return metrics.recent_best(points, today)
    if kind == "bodyweight":
        return metrics.bodyweight_now(points, today)
    if kind == "cardio_best":
        return metrics.recent_best(points, today, lower_is_better=True)
    ordered = metrics.in_order(points, today)
    return ordered[-1][1] if ordered else None


def _source(g, points: list, today: date) -> str:
    """Which measure _now_value used, in words, for messages. "Latest" is last in the one order."""
    past = [d for d, _ in metrics.in_order(points, today)]
    aged = [metrics.on_or_before(d, today) for d in past]
    recent = [d for d in aged if d >= today - timedelta(days=metrics.RECENT_BEST_DAYS - 1)]
    if g.kind == "lift":
        return (f"the best session in the last {metrics.RECENT_BEST_DAYS} days" if recent
                else f"the latest session, on {past[-1].isoformat()}")
    if g.kind == "cardio_best":
        return (f"the best time in the last {metrics.RECENT_BEST_DAYS} days" if recent
                else f"the latest time, on {past[-1].isoformat()}")
    if g.kind == "measurement":
        return f"the latest {measurement_name(g.measurement_kind).lower()}, on {past[-1].isoformat()}"
    if any(d >= today - timedelta(days=metrics.BODYWEIGHT_AVG_DAYS - 1) for d in aged):
        return "the 7-day average"
    return f"the latest weigh-in, on {past[-1].isoformat()}"


# ---------------------------------------------------------------- where a goal stands

def goal_dict(g: Goal) -> dict:
    return {**row_dict(g), "exercise": g.exercise.name if g.exercise else None}


def label(g) -> str:
    """'Back Squat 100 kg', 'Bodyweight 75 kg', 'Waist 80 cm', 'Outdoor Run 5 km in 28:00'."""
    target = g.target_value
    if g.kind == "lift" and g.exercise:
        return f"{g.exercise.name} {target:g} kg"
    if g.kind == "measurement" and g.measurement_kind in rules.MEASUREMENTS:
        return f"{measurement_name(g.measurement_kind)} {target:g} {g.unit}"
    if g.kind == "cardio_best" and g.exercise and g.distance_km:
        return f"{g.exercise.name} {g.distance_km:g} km in {clock(target)}"
    return f"Bodyweight {target:g} {g.unit}" if g.kind == "bodyweight" else f"{g.kind} {target:g} {g.unit}"


def _text(g: Goal) -> str:
    text = label(g) if g.target_value is not None else g.kind
    text += f" by {g.target_date.isoformat()}" if g.target_date else ""
    return f"{text} ({g.status})"


def _counts(g) -> bool:
    """A goal goal_numbers can work out: open, a kind of now, with a target, a date and what its kind needs."""
    if g.status != "open" or g.kind not in KINDS_NOW or g.target_value is None or g.target_date is None:
        return False
    if g.kind in WITH_EXERCISE and g.exercise is None:
        return False
    if g.kind == "measurement" and g.measurement_kind not in rules.MEASUREMENTS:
        return False
    return g.kind != "cardio_best" or bool(g.distance_km)


def goal_numbers(goals: list, loaded: dict, today: date) -> list[dict]:
    """Where each open goal stands, one shape for every kind. The stored direction says which way is
    better. loaded: load_numbers for these goals."""
    out = []
    for g in goals:
        if not _counts(g):
            continue
        points = _points(g, loaded, today)
        # One meaning of "where the person is now" for latest, status, the pace and the projection.
        lower = g.direction == "down"
        now = _now_value(g.kind, points, today)
        p = metrics.projection(points, today, g.target_value, g.target_date, lower, current=now)
        if p is not None:
            status, needed = p["status"], p["needed_per_week"]
        else:  # too few points for a line: the target can still be reached, and the pace still worked out
            status = "reached" if metrics.reached(now, g.target_value, lower) else "not enough data"
            needed = (None if now is None else
                      metrics.needed_per_week(now, g.target_value, g.target_date, today, lower))
        out.append({
            "goal_id": g.id, "label": label(g), "kind": g.kind,
            "exercise": g.exercise.name if g.kind in WITH_EXERCISE else None,
            "measurement_kind": g.measurement_kind if g.kind == "measurement" else None,
            "distance_km": g.distance_km if g.kind == "cardio_best" else None,
            "unit": g.unit,
            "start_value": g.start_value, "start_date": g.start_date.isoformat(), "direction": g.direction,
            "target_value": g.target_value, "target_date": g.target_date.isoformat(),
            "latest": now,
            "status": status,
            "needed_per_week": needed,
            "actual_per_week": p["slope_per_week"] if p else None,
            "projected_date": p["projected_date"] if p else None,
            "projection": p,
            "note": None if p or status == "reached" else metrics.projection_gap(points, today),
            "is_sample": bool(g.is_sample),
        })
    return out


# ---------------------------------------------------------------- checks for set_goal

def _future_date(value, today: date) -> tuple[date | None, dict | None]:
    parsed = rules.to_date(value)
    if parsed is None:
        return None, rules.error("invalid_date", f"target_date '{rules.quote(value)}' is not a date.",
                                 "Give the date as YYYY-MM-DD.")
    if parsed <= today:
        return None, rules.error("date_not_in_future", f"target_date {parsed.isoformat()} is not after today "
                                 f"({today.isoformat()}).", f"Give a target date after today. {rules.TODAY_NOTE}")
    return parsed, None


def _kind_error(given) -> dict:
    fix = f"Use one of: {', '.join(KINDS_NOW)}."
    if given is None:
        return rules.error("missing_field", "kind is missing.", fix)
    if isinstance(given, str) and given in SETTINGS_KINDS:
        return rules.error("invalid_field", f"kind '{given}' is not a goal: a weekly or daily amount is a setting.",
                           f"{fix} A weekly sessions, weekly cardio minutes or daily calorie target is set with "
                           "update_settings.")
    return rules.error("invalid_field", f"kind '{rules.quote(given)}' is not a kind of goal.", fix)


def _measurement_kind(value) -> tuple[str | None, dict | None]:
    """A measurement goal's kind: waist, hips, chest, arm, thigh or resting_hr. A day's amount is refused."""
    fix = f"Use one of: {', '.join(GOAL_MEASUREMENTS)}."
    if value in (None, ""):
        return None, rules.error("missing_field", "A measurement goal needs measurement_kind.", fix)
    hit, _ = rules.match_kind(value)
    if hit in DAILY_AMOUNTS:
        what = hit.replace("_", " ")
        return None, rules.error("invalid_field", f"{what} cannot be a goal: it is a day's amount, met or missed "
                                 "every day, not a state to reach by a date.", fix)
    if hit is None:
        return None, rules.error("invalid_kind", f"measurement_kind '{rules.quote(value)}' is not a measurement.",
                                 fix)
    return hit, None


def _wrong_kind(s, name, other: str) -> dict | None:
    """invalid_field when `name` is on the exercise list as the other kind (other: "cardio" or "lift"):
    a cardio activity sent for a lift goal, or a lift for a cardio_best goal. None when it is not."""
    hit, _ = rules.match_exercise(name, [e.name for e in exercises(s, other)])
    if hit is None:
        return None
    if other == "cardio":
        return rules.error("invalid_field", f"{hit} is a cardio activity; a lift goal needs a lift.",
                           "Use a lift from list_exercises with kind lift. For a time over a distance in "
                           f"{hit}, set kind cardio_best with distance_km.")
    return rules.error("invalid_field", f"{hit} is a lift; a cardio_best goal needs a cardio activity.",
                       "Use an activity from list_exercises with kind cardio. For an estimated max in "
                       f"{hit}, set kind lift.")


def _identity(s, kind: str, data: dict, errors: list, warnings: list):
    """What a new goal is of: (exercise, measurement_kind, distance_km), each checked; the problems go
    into errors and an ignored field into warnings."""
    exercise = measurement_kind = distance = None
    present = lambda k: data.get(k) not in (None, "")  # noqa: E731
    if kind == "lift":
        by_name = {e.name: e for e in exercises(s, "lift")}
        if not present("exercise"):
            errors.append(rules.error("missing_field", "A lift goal needs an exercise.",
                                      "Give the exercise name as list_exercises gives it."))
        else:
            hit, _ = rules.match_exercise(data["exercise"], list(by_name))
            if hit is None:
                errors.append(_wrong_kind(s, data["exercise"], "cardio")
                              or rules.unknown_exercise(data["exercise"], list(by_name)))
            else:
                exercise = by_name[hit]
    elif kind == "cardio_best":
        exercise, err = cardio.match_cardio(s, data.get("exercise"))
        if err and err["error"] == "missing_field":
            err = {**err, "message": "A cardio_best goal needs an activity: " + err["message"]}
        elif err:
            err = _wrong_kind(s, data["exercise"], "lift") or err
        errors += [err] if err else []
        if not present("distance_km"):
            errors.append(rules.error("missing_field", "A cardio_best goal needs distance_km.",
                                      f"Give distance_km, {DISTANCE_KM[0]:g} to {DISTANCE_KM[1]:g}: the distance the "
                                      "time is for."))
        else:
            lo, hi = DISTANCE_KM
            distance, err = in_range(data["distance_km"], "distance_km", lo, hi,
                                     f"Give distance_km, {lo:g} to {hi:g}: the distance the time is for.")
            errors += [err] if err else []
    elif present("exercise"):
        warnings.append(f"exercise '{rules.quote(data['exercise'])}' was ignored: only a lift or cardio_best goal "
                        "has an exercise.")
    if kind == "measurement":
        measurement_kind, err = _measurement_kind(data.get("measurement_kind"))
        errors += [err] if err else []
    elif present("measurement_kind"):
        warnings.append(f"measurement_kind '{rules.quote(data['measurement_kind'])}' was ignored: only a "
                        "measurement goal has one.")
    if kind != "cardio_best" and present("distance_km"):
        warnings.append(f"distance_km '{rules.quote(data['distance_km'])}' was ignored: only a cardio_best goal "
                        "has one.")
    return exercise, measurement_kind, distance


def _changes_identity(goal: Goal, data: dict) -> bool:
    """True when an edit names a different kind, exercise, measurement kind or distance."""
    if data.get("kind") not in (None, "", goal.kind):
        return True
    if data.get("exercise") and goal.exercise and rules.match_exercise(data["exercise"], [goal.exercise.name])[0] \
            is None:
        return True
    if data.get("measurement_kind") not in (None, "") and rules.match_kind(data["measurement_kind"])[0] \
            != goal.measurement_kind:
        return True
    if data.get("distance_km") not in (None, ""):
        number = rules.to_float(data["distance_km"])
        return number is None or goal.distance_km is None or round(number, 2) != goal.distance_km
    return False


def _no_start(g) -> dict:
    """missing_field for a new goal with nothing logged to start from, naming what to log or send."""
    if g.kind == "lift":
        return rules.error("missing_field", f"There is no logged {g.exercise.name} to start this goal from.",
                           f"Log a session with {g.exercise.name} first (log_lifts), or give start_value: the "
                           "person's current one-rep max in kg.")
    if g.kind == "bodyweight":
        return rules.error("missing_field", "There is no weigh-in to start this goal from.",
                           "Log a weigh-in first (log_bodyweight), or give start_value: today's body weight in kg.")
    if g.kind == "measurement":
        name = measurement_name(g.measurement_kind).lower()
        return rules.error("missing_field", f"There is no {name} measurement to start this goal from.",
                           f"Log one first (log_measurement), or give start_value: the person's {name} now, in "
                           f"{g.unit}.")
    return rules.error("missing_field", f"There is no {g.exercise.name} session of {g.distance_km:g} km or more to "
                       "start this goal from.",
                       f"Log one first (log_cardio with distance_km), or give start_value: the person's time for "
                       f"{g.distance_km:g} km now, in minutes.")


def _start(g, loaded: dict, given, today: date, warnings: list) -> tuple[float | None, str | None, dict | None]:
    """Where a new goal starts, and where that number came from: the person's own numbers, or
    start_value when nothing is logged yet."""
    points = _points(g, loaded, today)
    current = _now_value(g.kind, points, today)
    if current is not None:
        source = _source(g, points, today)
        if given not in (None, ""):
            warnings.append(f"start_value was ignored: the goal starts from the logged {current:g} {g.unit} "
                            f"({source}).")
        return current, source, None
    if given in (None, ""):
        return None, None, _no_start(g)
    value, err = _value(given, "start_value", g.kind, g.measurement_kind)
    return value, "the start_value given", err


def _wrong_way(kind: str, measurement_kind: str | None, direction: str, start: float, target: float) -> dict | None:
    """None when the target lies the goal's way from the start. Otherwise an error whose fix names
    only targets the rules accept."""
    if (direction == "up" and target > start) or (direction == "down" and target < start):
        return None
    lo, hi, *_ = _limits(kind, measurement_kind)
    unit = unit_for(kind, measurement_kind)
    side = "above" if direction == "up" else "below"
    message = (f"target_value {target:g} {unit} is not {side} the start of {start:g} {unit}, "
               f"and this goal points {direction}.")
    if (direction == "up" and start >= hi) or (direction == "down" and start <= lo):
        return rules.error("wrong_direction", message,
                           f"No target {side} {start:g} {unit} can be set: {hi if direction == 'up' else lo:g} {unit} "
                           f"is the {'most' if direction == 'up' else 'least'} {_what(kind, measurement_kind)} takes. "
                           "Mark this goal dropped, or set another goal.")
    span = (f"above {start:g} and at most {hi:g} {unit}" if direction == "up"
            else f"below {start:g} and at least {lo:g} {unit}")
    return rules.error("wrong_direction", message,
                       f"Give a target {span}. To aim the other way, mark this goal dropped and set a new one.")


# ---------------------------------------------------------------- set_goal

def set_goal(user_id: int, data: dict | None, by: str) -> dict:
    """Add a goal, or change one (goal_id). See the module notes.

    Returns {kind, id, goal, progress, warnings, links[, note]} or {error, message, fix}. progress is the
    goal's entry from goal_numbers (status "reached", "on track", "behind" or "not enough data"), or None for a goal
    that is not open. Every validation failure comes back at once.
    """
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
        today = users.today_for(user)
        errors, warnings = [], []
        status = data.get("status") or None
        status_ok = status is None or (isinstance(status, str) and status in GOAL_STATUSES)
        if not status_ok:
            errors.append(rules.error("invalid_field",
                                      f"status '{rules.quote(status)}' is not one of {', '.join(GOAL_STATUSES)}.",
                                      "Use hit (reached), dropped, or open."))
        text = None
        if data.get("text") is not None:  # left out: an edit keeps the text it has
            text, err = rules.text(data["text"], "text")
            errors += [err] if err else []

        goal = None
        exercise = measurement_kind = distance = None
        renamed = False  # an edit that names another kind, exercise, measurement or distance
        if data.get("goal_id") not in (None, ""):
            goal = owned(s, Goal, user.id, data["goal_id"])
            if goal is None:
                return not_found(KIND)
            kind, measurement_kind = goal.kind, goal.measurement_kind
            renamed = _changes_identity(goal, data)
            if renamed:
                errors.append(rules.error("cannot_change_kind",
                                          "A goal's kind, exercise, measurement kind and distance cannot change.",
                                          "Mark this goal dropped (goal_id and status dropped) and set a new one."))
            if data.get("start_value") not in (None, ""):
                warnings.append("start_value was ignored: a goal keeps the start it was set with.")
        else:
            given = data.get("kind") or None
            kind = given if isinstance(given, str) and given in KINDS_NOW else None
            if kind is None:
                errors.append(_kind_error(given))
            else:
                exercise, measurement_kind, distance = _identity(s, kind, data, errors, warnings)
            if status_ok and status not in (None, "open"):
                errors.append(rules.error("invalid_field", f"A new goal starts open; status '{status}' needs goal_id.",
                                          "Leave status out to set a goal, or give goal_id to close one."))

        # A value can be checked once its range is known: the kind, and for a measurement, which one.
        ranged = kind in KINDS_NOW and (kind != "measurement" or measurement_kind is not None)
        target = target_date = None
        if data.get("target_value") not in (None, "") and ranged:
            target, err = _value(data["target_value"], "target_value", kind, measurement_kind)
            errors += [err] if err else []
        if data.get("target_date") not in (None, ""):
            target_date, err = _future_date(data["target_date"], today)
            errors += [err] if err else []
        if goal is None and kind in KINDS_NOW:
            for field, value in (("target_value", target), ("target_date", target_date)):
                if value is None and data.get(field) in (None, ""):
                    how = (" (YYYY-MM-DD, after today)." if field == "target_date"
                           else " in minutes for the distance." if kind == "cardio_best"
                           else f" in {unit_for(kind, measurement_kind)}." if ranged else ".")
                    errors.append(rules.error("missing_field", f"A {kind} goal needs {field}.", f"Give {field}{how}"))
        # cannot_change_kind already says what to do; "nothing to change" beside it would contradict it.
        if goal is not None and not renamed and status is None and target is None and target_date is None \
                and text is None:
            errors.append(rules.error("nothing_to_change", "Nothing to change on this goal.",
                                      "Give status (hit, dropped or open), target_value, target_date or text."))
        if errors:
            return rules.combine(errors)

        if goal is None:
            goal = Goal(user_id=user.id, kind=kind, exercise_id=exercise.id if exercise else None,
                        measurement_kind=measurement_kind, distance_km=distance,
                        unit=unit_for(kind, measurement_kind), start_date=today, target_value=target,
                        target_date=target_date, status="open", text=text or "")
            goal.exercise = exercise
            loaded = load_numbers(s, user.id, today, [goal])
            start, source, err = _start(goal, loaded, data.get("start_value"), today, warnings)
            if err:
                return err
            if kind == "lift":
                direction = "up"
            elif kind == "cardio_best":
                direction = "down"
            elif target == start:
                return rules.error("target_is_start", f"target_value {target:g} {goal.unit} is the start itself "
                                   f"({source}).", f"Give a target above or below {start:g} {goal.unit}.")
            else:
                direction = "down" if target < start else "up"
            err = _wrong_way(kind, measurement_kind, direction, start, target)
            if err:
                return err
            same = select(Goal).where(Goal.user_id == user.id, Goal.status == "open", Goal.kind == kind)
            for column, value in ((Goal.exercise_id, goal.exercise_id), (Goal.measurement_kind, measurement_kind),
                                  (Goal.distance_km, distance)):
                same = same.where(column == value if value is not None else column.is_(None))
            same = list(s.scalars(same.order_by(Goal.id).options(selectinload(Goal.exercise))))
            goal.start_value, goal.direction = start, direction
            s.add(goal)
            s.flush()
            for g in same:
                warnings.append(f"There is already an open goal {_text(g)} (id {g.id}). Both are kept; "
                                f"if the new one replaces it, mark goal {g.id} dropped.")
        else:
            loaded = load_numbers(s, user.id, today, [goal])
            if target is not None:
                err = _wrong_way(goal.kind, goal.measurement_kind, goal.direction, goal.start_value, target)
                if err:
                    return err
            if status == "open" and goal.status != "open" and target_date is None and goal.target_date \
                    and goal.target_date <= today:
                warnings.append(f"Reopened, but its target date {goal.target_date.isoformat()} has passed: "
                                "give a new target_date, or the projection has nothing to aim at.")
            if status is not None and status != goal.status:
                goal.closed_at = None if status == "open" else db.utcnow()
                goal.status = status
            goal.target_value = target if target is not None else goal.target_value
            goal.target_date = target_date or goal.target_date
            goal.text = text if text is not None else goal.text
            s.flush()

        progress = next(iter(goal_numbers([goal], loaded, today)), None)
        out = {"kind": KIND, "id": goal.id, "goal": goal_dict(goal), "progress": progress, "warnings": warnings,
               "links": {"goals": links.page("goals")}}
        if goal.status != "open":
            out["note"] = f"This goal is {goal.status}: it no longer shows in progress."
        return out


# ---------------------------------------------------------------- get_goals

def _closed(g: Goal) -> dict:
    return {"goal_id": g.id, "label": label(g) if g.target_value is not None else g.kind, "kind": g.kind,
            "status": g.status, "closed_at": g.closed_at.isoformat() if g.closed_at else None,
            "target_value": g.target_value, "target_date": g.target_date.isoformat() if g.target_date else None,
            "is_sample": bool(g.is_sample)}


def _summary(numbers: list[dict], closed: list[dict]) -> str:
    if not numbers:
        text = "No open goals."
    else:
        word = "goal" if len(numbers) == 1 else "goals"
        text = f"{len(numbers)} open {word}: " + "; ".join(
            f"{n['label']} by {n['target_date']}, {n['status']}" for n in numbers) + "."
    if closed:
        text += f" {len(closed)} closed."
    return text


def get_goals(user_id: int, include_closed=False) -> dict:
    """Every open goal and where it stands; the closed ones too with include_closed.

    Returns {today, goals [goal numbers, by id], closed [{goal_id, label, kind, status, closed_at,
    target_value, target_date}] (empty unless include_closed), includes_sample, how, summary,
    links {goals}[, note]} or {error, message, fix}. A read: no lock, no write limit."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        wanted = truthy(include_closed)
        query = select(Goal).where(Goal.user_id == user.id).order_by(Goal.id).options(selectinload(Goal.exercise))
        rows = list(s.scalars(query if wanted else query.where(Goal.status == "open")))
        open_goals = [g for g in rows if g.status == "open"]
        loaded = load_numbers(s, user.id, today, open_goals)
        numbers = goal_numbers(open_goals, loaded, today)
        shown_ids = {n["goal_id"] for n in numbers}
        closed_rows = [g for g in rows if g.status != "open"] if wanted else []
        closed = [_closed(g) for g in closed_rows]
        sample = loaded["sample"] or any(g.is_sample for g in rows if g.id in shown_ids or g in closed_rows)
        out = {"today": today.isoformat(), "goals": numbers, "closed": closed, "includes_sample": sample,
               "how": GOALS_HOW, "summary": _summary(numbers, closed), "links": {"goals": links.page("goals")}}
        if sample:
            out["note"] = SAMPLE_NOTE
        return out
