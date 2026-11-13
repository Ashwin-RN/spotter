"""Reading progress back: one lift in full, or a short line for every lift logged.

get_progress(user_id, exercise=None)
    With an exercise: its estimated max per session, the best set, every new best with its date,
    the trend over the last 6 weeks, and its open goals with the pace each needs against the pace so far.
    Without: one line per lift the person has logged, and every open goal of every kind.
    Every number comes from app.metrics, through lifts.history and goals.load_numbers and
    goals.goal_numbers, the same path get_goals and set_goal take. With nothing logged yet, the note
    says so.

    Results are rounded where section D of stage 6 names them, once, as the result is built: an
    estimated max at 1 dp (a best and the one it beat together, lifts.rounded_best), and the goals
    through goals.rounded_numbers. The numbers worked out from stay unrounded.

lift_chart(lift_history, goal_numbers, lift=None) and bodyweight_chart(weigh_ins, goals, today, since=None)
    The data app.charts draws, from the same numbers. Each body weight point carries its smoothed
    value (metrics.ewma over every weigh-in), the line the chart draws, and the 7-day average there,
    both at 1 dp. since: draw only the weigh-ins from that date on; the smoothed line is unchanged.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import db, formatting, links, metrics, rules, users
from app.models import Goal, User
from app.services import cardio, lifts
from app.services.common import exercises, no_account
from app.services.goals import goal_numbers, load_numbers, rounded_numbers

HOW = ("estimated max = Epley, from sets of 1 to 10 reps; one point per session (the session's best). "
       f"The current estimated max is the best session in the last {metrics.RECENT_BEST_DAYS} days, or the latest "
       "session's when none is that recent; best_estimated_max_kg is the best ever. A new best "
       "is a session best or heaviest weight above every earlier set of that lift. Trend and goal projection = a "
       f"straight line through the last {metrics.PROJECTION_DAYS // 7} weeks, needing "
       f"{metrics.PROJECTION_MIN_POINTS} points. needed_per_week is the steady change that reaches the target by "
       "its date; actual_per_week is the trend so far. Quote these numbers; do not work out your own.")


CARDIO_HOW = ("Calorie estimates are MET x 3.5 x body weight in kg / 200 x minutes, with MET values from the "
              "2024 Adult Compendium of Physical Activities (estimate.source names the row) and body weight now. "
              "week is the minutes of the 7 days ending today against the weekly cardio target. "
              "Quote these numbers; do not work out your own.")


def _max(value: float | None) -> float | None:
    """An estimated max at 1 dp, for a result."""
    return None if value is None else formatting.rounded(value, "estimated_max")


def _one(name: str, history: dict, numbers: list[dict], today: date) -> dict:
    """One lift. numbers: goals.rounded_numbers. history's own values are never changed."""
    pts = history["points"].get(name, [])
    trend = metrics.projection(pts, today)
    goals = [g for g in numbers if g["exercise"] == name]
    return {
        "exercise": name,
        "sessions": history["logged"].get(name, 0),
        "last_trained": history["last"][name].isoformat() if name in history["last"] else None,
        "estimated_max_over_time": [{"date": d.isoformat(), "estimated_max_kg": _max(v)} for d, v in pts],
        "current_estimated_max_kg": _max(metrics.recent_best(pts, today)),
        "best_set": history["best_set"].get(name),
        "bests": [lifts.rounded_best({k: p[k] for k in ("date", "kind", "value", "previous")})
                  for p in history["bests"].get(name, [])],
        "trend": {"slope_per_week": trend["slope_per_week"], "points_used": trend["points_used"]} if trend else None,
        "goal": goals[0] if goals else None,
        "goals": goals,
    }


def get_progress(user_id: int, exercise: str | None = None) -> dict:
    """One lift over time, or a summary of every lift logged.

    With exercise: {today, lift {exercise, sessions, last_trained, estimated_max_over_time [{date,
    estimated_max_kg}], current_estimated_max_kg, best_set, bests [{date, kind, value, previous}], trend
    {slope_per_week, points_used} | None, goal, goals}, how, links[, note]}.
    Without: {today, lifts [{exercise, sessions, last_trained, current_estimated_max_kg,
    best_estimated_max_kg, slope_per_week, goal_status}], goals, how, links[, note]}.
    A cardio activity: {today, cardio {exercise, sessions [{id, date, minutes, distance_km, pace, estimate,
    machine_kcal}] (newest first, in the one order), week {minutes, target_minutes}}, how, links[, note]}.
    week counts all cardio in the 7 days ending today, as the weekly cardio target does.
    An exercise not on the list (lifts and cardio) gives unknown_exercise with the closest names.
    """
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        only = None
        if exercise not in (None, ""):
            listed = exercises(s)
            names = [e.name for e in listed]
            only, _ = rules.match_exercise(exercise, names)
            if only is None:
                return rules.unknown_exercise(exercise, names)
            activity = next(e for e in listed if e.name == only)
            if activity.kind == "cardio":
                return _cardio(s, user, activity, today)
        history = lifts.history(s, user.id, today)
        goals = list(s.scalars(select(Goal).where(Goal.user_id == user.id, Goal.status == "open")
                               .order_by(Goal.id).options(selectinload(Goal.exercise))))
        loaded = load_numbers(s, user.id, today, goals, lift_history=history)
        numbers = rounded_numbers(goal_numbers(goals, loaded, today))

        if only is not None:
            one = _one(only, history, numbers, today)
            out = {"today": today.isoformat(), "lift": one, "how": HOW,
                   "links": {"progress_lift": links.page("progress_lift", lift=only)}}
            return _with_note(out, None if one["sessions"] else f"No sets of {only} logged yet.")

        rows = []
        for name in sorted(history["logged"]):
            one = _one(name, history, numbers, today)
            rows.append({"exercise": name, "sessions": one["sessions"], "last_trained": one["last_trained"],
                         "current_estimated_max_kg": one["current_estimated_max_kg"],
                         "best_estimated_max_kg": (one["best_set"] or {}).get("estimated_max_kg"),
                         "slope_per_week": one["trend"]["slope_per_week"] if one["trend"] else None,
                         "goal_status": one["goal"]["status"] if one["goal"] else None})
        out = {"today": today.isoformat(), "lifts": rows, "goals": numbers, "how": HOW,
               "links": {"progress": links.page("progress")}}
        return _with_note(out, None if rows else "No lift sessions logged yet.")


def _with_note(out: dict, note: str | None) -> dict:
    """The result, with its note when it has one."""
    if note:
        out["note"] = note
    return out


def _cardio(s, user, activity, today) -> dict:
    """One cardio activity: its sessions, newest first in the one order (cardio.sessions_of, the list
    get_cardio gives too), and this week's minutes."""
    rows = cardio.sessions_of(s, user, activity, today)
    sessions = [{k: d[k] for k in ("id", "date", "minutes", "distance_km", "pace", "estimate", "machine_kcal")}
                for d in (cardio.cardio_dict(c) for c in rows)]
    out = {"today": today.isoformat(),
           "cardio": {"exercise": activity.name, "sessions": sessions, "week": cardio.week_minutes(s, user, today)},
           "how": CARDIO_HOW, "links": {"progress": links.page("progress")}}
    return _with_note(out, None if sessions else f"No {activity.name} logged yet.")


# ---------------------------------------------------------------- chart data

def lift_chart(lift_history: dict, numbers: list[dict], lift: str | None = None) -> dict | None:
    """Data for chart_svg: estimated max over time for one lift, its goal and projection. None with no points.

    lift: shown if it has points; else the first goal lift with points; else the lift with the most points.
    """
    points = lift_history["points"]
    if not points:
        return None
    choices = sorted(points, key=lambda name: (-len(points[name]), name))
    goal_lifts = [g["exercise"] for g in numbers if g.get("exercise")]
    if lift not in points:
        lift = next((name for name in goal_lifts if name in points), choices[0])
    goal = next((g for g in numbers if g.get("exercise") == lift), None)
    pts = points[lift]
    return {
        "lift": lift, "choices": choices,
        "points": [{"date": d.isoformat(), "value": v} for d, v in pts],
        "latest": {"date": pts[-1][0].isoformat(), "value": pts[-1][1]},
        "goal": None if goal is None else {
            "target_kg": goal["target_value"], "target_date": goal["target_date"], "status": goal["status"],
            "projected": goal["projection"]["projected"] if goal["projection"] else None},
    }


def _derived(value: float | None) -> float | None:
    """A smoothed or averaged body weight at 1 dp (D4). A weigh-in's own kg stays as entered."""
    return None if value is None else formatting.rounded(value, "bodyweight_derived")


def bodyweight_chart(weigh_ins: list, goals: list[Goal], today: date, since: date | None = None) -> dict | None:
    """Data for bodyweight_svg: weigh-ins with their smoothed value, the 7-day average at each, the
    open bodyweight goal. Every weigh-in is drawn, whatever its date, in the one order and labelled
    with its real date; with since, only those whose effective date is on or after it. smoothed is
    metrics.ewma over every weigh-in, the window's or not (the line get_trends reports). Each average
    is the 7 days ending on that weigh-in's effective date, over every weigh-in's effective date, so
    the last one is bodyweight_now. None with fewer than 2 weigh-ins drawn."""
    every = metrics.in_order([(b.date, b.kg) for b in weigh_ins], today)
    aged = [(metrics.on_or_before(d, today), kg) for d, kg in every]
    smoothed = [v for _, v in metrics.ewma(every, today)]
    drawn = [(p, sm) for p, sm in zip(every, smoothed)
             if since is None or metrics.on_or_before(p[0], today) >= since]
    if len(drawn) < 2:
        return None
    pts = [p for p, _ in drawn]
    goal = next((g for g in goals if g.kind == "bodyweight" and g.status == "open" and g.target_value is not None),
                None)
    return {
        "points": [{"date": d.isoformat(), "kg": kg, "smoothed": _derived(sm)} for (d, kg), sm in drawn],
        "average": [{"date": d.isoformat(),
                     "kg": _derived(metrics.bodyweight_average(aged, metrics.on_or_before(d, today)))}
                    for d, _ in pts],
        "latest": {"date": pts[-1][0].isoformat(), "kg": pts[-1][1]},
        "goal": None if goal is None else {
            "target_kg": goal.target_value, "target_date": goal.target_date.isoformat() if goal.target_date else None},
    }
