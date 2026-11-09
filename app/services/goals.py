"""Goals: a lift to reach, or a body weight to reach, by a date.

set_goal(user_id, data, by)
    A new goal: kind lift or bodyweight (the other kinds arrive later). A lift goal names an
    exercise. Both need target_value in kg (a lift above 0 to LIFT_MAX, the estimated max of the
    heaviest weight the rules allow at 10 reps; a body weight 20 to 400) and target_date (after
    today). text is optional, up to 2,000 characters.
    When a goal is created, Spotter stores where it starts and which way it points:
      start_value  a lift: its current estimated max (the best session in the last 28 days,
                   metrics.recent_best); a body weight: the 7-day average, or the latest weigh-in
                   when the last 7 days have none (metrics.bodyweight_now). The goal's "latest"
                   uses the same measure. With nothing logged yet, start_value must be given.
      start_date   today.
      direction    a lift: up, so the target must be above the start. A body weight: down when
                   the target is below the start, up when above; a target equal to the start is refused.
    The direction is never worked out again. An edit that would point the target the other way is
    refused: drop the goal and set a new one.

    With goal_id: one of the person's own goals. status (open, hit, dropped), target_value,
    target_date and text change; kind and exercise cannot. Closing a goal sets closed_at;
    reopening clears it.

goal_numbers(goals, lift_history, weigh_ins, today)
    For each open goal with a target and a date: where it stands, from metrics.projection.
"""

from datetime import date

from sqlalchemy import select

from app import db, links, metrics, rules, users
from app.models import GOAL_STATUSES, Goal
from app.services import body, lifts
from app.services.common import (
    check_by, check_data, exercises, in_range, lock_user, no_account, not_found, owned, row_dict,
)

KIND = "goal"
KINDS_NOW = ("lift", "bodyweight")
# The highest estimated max the rules allow: the heaviest weight at the most reps Epley counts.
LIFT_MAX = metrics.epley(rules.HARD_RULES["weight_kg"][1], metrics.EPLEY_MAX_REPS)
TARGETS = {"lift": (0, LIFT_MAX), "bodyweight": (20, 400)}  # kg; a lift target must be above 0
UNIT = "kg"


def _now_value(kind: str, points: list, today: date) -> float | None:
    """Where the person is now, the one measure a goal starts from and is compared with: a lift's
    best session in the last 28 days (metrics.recent_best), or the 7-day average body weight
    (metrics.bodyweight_now)."""
    return metrics.recent_best(points, today) if kind == "lift" else metrics.bodyweight_now(points, today)


def goal_dict(g: Goal) -> dict:
    return {**row_dict(g), "exercise": g.exercise.name if g.exercise else None}


def _label(g: Goal) -> str:
    what = g.exercise.name if g.kind == "lift" and g.exercise else "Bodyweight"
    return f"{what} {g.target_value:g} {g.unit}"


def _text(g: Goal) -> str:
    text = _label(g) if g.target_value is not None else g.kind
    text += f" by {g.target_date.isoformat()}" if g.target_date else ""
    return f"{text} ({g.status})"


def goal_numbers(goals: list[Goal], lift_history: dict, weigh_ins: list, today: date) -> list[dict]:
    """Where each open lift or bodyweight goal stands. The stored direction says which way is better."""
    out = []
    for g in goals:
        if g.status != "open" or g.kind not in KINDS_NOW or g.target_value is None or g.target_date is None:
            continue
        if g.kind == "lift":
            if g.exercise is None:
                continue
            points = lift_history["points"].get(g.exercise.name, [])
        else:
            points = [(b.date, b.kg) for b in weigh_ins]
        p = metrics.projection(points, today, g.target_value, g.target_date, g.direction == "down")
        out.append({
            "goal_id": g.id, "label": _label(g), "kind": g.kind,
            "exercise": g.exercise.name if g.kind == "lift" else None, "unit": g.unit,
            "start_value": g.start_value, "start_date": g.start_date.isoformat(), "direction": g.direction,
            "target_value": g.target_value, "target_date": g.target_date.isoformat(),
            "latest": _now_value(g.kind, points, today),
            "status": p["status"] if p else "not enough data",
            "needed_per_week": p["needed_per_week"] if p else None,
            "actual_per_week": p["slope_per_week"] if p else None,
            "projection": p,
            "note": None if p else (f"A projection needs {metrics.PROJECTION_MIN_POINTS} points in the last "
                                    f"{metrics.PROJECTION_DAYS // 7} weeks."),
        })
    return out


def _future_date(value, today: date) -> tuple[date | None, dict | None]:
    parsed = rules.to_date(value)
    if parsed is None:
        return None, rules.error("invalid_date", f"target_date '{rules.quote(value)}' is not a date.",
                                 "Give the date as YYYY-MM-DD.")
    if parsed <= today:
        return None, rules.error("date_not_in_future", f"target_date {parsed.isoformat()} is not after today "
                                 f"({today.isoformat()}).", f"Give a target date after today. {rules.TODAY_NOTE}")
    return parsed, None


def _target(value, kind: str) -> tuple[float | None, dict | None]:
    lo, hi = TARGETS[kind]
    span = f"above {lo:g} to {hi:g}" if kind == "lift" else f"{lo:g} to {hi:g}"
    return in_range(value, "target_value", lo, hi, f"Give target_value in kg, {span}, for a {kind} goal.",
                    low_open=kind == "lift")


def _start(kind: str, exercise, lift_history: dict, weigh_ins: list, given, today: date,
           warnings: list) -> tuple[float | None, dict | None]:
    """Where a new goal starts: the person's own numbers, or start_value when nothing is logged yet."""
    if kind == "lift":
        points = lift_history["points"].get(exercise.name, [])
        what = f"logged {exercise.name}"
        fix = (f"Log a session with {exercise.name} first (log_lifts), or give start_value: the person's current "
               "one-rep max in kg.")
        lo, hi = 0, LIFT_MAX
    else:
        points = [(b.date, b.kg) for b in weigh_ins]
        what = "weigh-in"
        fix = "Log a weigh-in first (log_bodyweight), or give start_value: today's body weight in kg."
        lo, hi = TARGETS["bodyweight"]
    current = _now_value(kind, points, today)
    if current is not None:
        if given not in (None, ""):
            warnings.append(f"start_value was ignored: the goal starts from the logged {current:g} kg.")
        return current, None
    if given in (None, ""):
        return None, rules.error("missing_field", f"There is no {what} to start this goal from.", fix)
    return in_range(given, "start_value", lo, hi, fix)


def _wrong_way(kind: str, direction: str, start: float, target: float) -> dict | None:
    """None when the target lies the goal's way from the start. Otherwise an error whose fix names
    only targets the rules accept."""
    if (direction == "up" and target > start) or (direction == "down" and target < start):
        return None
    lo, hi = TARGETS[kind]
    side = "above" if direction == "up" else "below"
    message = (f"target_value {target:g} kg is not {side} the start of {start:g} kg, "
               f"and this goal points {direction}.")
    if (direction == "up" and start >= hi) or (direction == "down" and start <= lo):
        return rules.error("wrong_direction", message,
                           f"No target {side} {start:g} kg can be set: {hi if direction == 'up' else lo:g} kg is the "
                           f"{'most' if direction == 'up' else 'least'} a {kind} goal takes. Mark this goal dropped, "
                           "or set a goal for another lift.")
    span = f"above {start:g} and at most {hi:g} kg" if direction == "up" else f"below {start:g} and at least {lo:g} kg"
    return rules.error("wrong_direction", message,
                       f"Give a target {span}. To aim the other way, mark this goal dropped and set a new one.")


def set_goal(user_id: int, data: dict | None, by: str) -> dict:
    """Add a goal, or change one (goal_id). See the module notes.

    Returns {kind, id, goal, progress, warnings, links[, note]} or {error, message, fix}. progress is the
    goal's entry from goal_numbers (status "on track", "behind" or "not enough data"), or None for a goal
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
        if data.get("goal_id") not in (None, ""):
            goal = owned(s, Goal, user.id, data["goal_id"])
            if goal is None:
                return not_found(KIND)
            kind = goal.kind
            if data.get("kind") not in (None, "", goal.kind) or (
                    data.get("exercise") and goal.exercise
                    and rules.match_exercise(data["exercise"], [goal.exercise.name])[0] is None):
                errors.append(rules.error("cannot_change_kind", "A goal's kind and exercise cannot change.",
                                          "Mark this goal dropped (goal_id and status dropped) and set a new one."))
            if data.get("start_value") not in (None, ""):
                warnings.append("start_value was ignored: a goal keeps the start it was set with.")
        else:
            given = data.get("kind") or None
            kind = given if isinstance(given, str) and given in KINDS_NOW else None
            if kind is None:
                errors.append(rules.error(
                    "missing_field" if given is None else "invalid_field",
                    "kind is missing." if given is None else f"kind '{rules.quote(given)}' cannot be set yet.",
                    f"Use one of: {', '.join(KINDS_NOW)}."))
            if status_ok and status not in (None, "open"):
                errors.append(rules.error("invalid_field", f"A new goal starts open; status '{status}' needs goal_id.",
                                          "Leave status out to set a goal, or give goal_id to close one."))

        exercise = None
        if goal is None and kind == "bodyweight" and data.get("exercise"):
            warnings.append(f"exercise '{rules.quote(data['exercise'])}' was ignored: only a lift goal has an exercise.")
        if goal is None and kind == "lift":
            by_name = {e.name: e for e in exercises(s, "lift")}
            if not data.get("exercise"):
                errors.append(rules.error("missing_field", "A lift goal needs an exercise.",
                                          "Give the exercise name as list_exercises gives it."))
            else:
                hit, _ = rules.match_exercise(data["exercise"], list(by_name))
                if hit is None:
                    errors.append(rules.unknown_exercise(data["exercise"], list(by_name)))
                else:
                    exercise = by_name[hit]

        target = target_date = None
        if data.get("target_value") not in (None, "") and kind in TARGETS:
            target, err = _target(data["target_value"], kind)
            errors += [err] if err else []
        if data.get("target_date") not in (None, ""):
            target_date, err = _future_date(data["target_date"], today)
            errors += [err] if err else []
        if goal is None and kind in TARGETS:
            for field, value in (("target_value", target), ("target_date", target_date)):
                if value is None and data.get(field) in (None, ""):
                    errors.append(rules.error("missing_field", f"A {kind} goal needs {field}.",
                                              f"Give {field}" + (" (YYYY-MM-DD, after today)." if field == "target_date"
                                                                 else " in kg.")))
        if goal is not None and status is None and target is None and target_date is None and text is None:
            errors.append(rules.error("nothing_to_change", "Nothing to change on this goal.",
                                      "Give status (hit, dropped or open), target_value, target_date or text."))
        if errors:
            return rules.combine(errors)

        lift_history = lifts.history(s, user.id, today)
        weighed = body.weigh_ins(s, user.id, today)
        if goal is None:
            start, err = _start(kind, exercise, lift_history, weighed, data.get("start_value"), today, warnings)
            if err:
                return err
            if kind == "lift":
                direction = "up"
            elif target == start:
                return rules.error("target_is_start", f"target_value {target:g} kg is the start weight itself "
                                   "(the 7-day average).", f"Give a target above or below {start:g} kg.")
            else:
                direction = "down" if target < start else "up"
            err = _wrong_way(kind, direction, start, target)
            if err:
                return err
            same = select(Goal).where(Goal.user_id == user.id, Goal.status == "open", Goal.kind == kind)
            same = list(s.scalars(same.where(Goal.exercise_id == exercise.id) if exercise
                                  else same.where(Goal.exercise_id.is_(None))))
            goal = Goal(user_id=user.id, kind=kind, exercise_id=exercise.id if exercise else None, unit=UNIT,
                        start_value=start, start_date=today, direction=direction, target_value=target,
                        target_date=target_date, status="open", text=text or "")
            goal.exercise = exercise
            s.add(goal)
            s.flush()
            for g in same:
                warnings.append(f"There is already an open goal {_text(g)} (id {g.id}). Both are kept; "
                                f"if the new one replaces it, mark goal {g.id} dropped.")
        else:
            if target is not None:
                err = _wrong_way(goal.kind, goal.direction, goal.start_value, target)
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

        progress = next(iter(goal_numbers([goal], lift_history, weighed, today)), None)
        out = {"kind": KIND, "id": goal.id, "goal": goal_dict(goal), "progress": progress, "warnings": warnings,
               "links": {"goals": links.page("goals")}}
        if goal.status != "open":
            out["note"] = f"This goal is {goal.status}: it no longer shows in progress."
        return out
