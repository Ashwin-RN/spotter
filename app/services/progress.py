"""Reading progress back: one lift in full, or a short line for every lift logged.

get_progress(user_id, exercise=None)
    With an exercise: its estimated max per session, the best set, every new best with its date,
    the trend over the last 6 weeks, and its open goals with the pace each needs against the pace so far.
    Without: one line per lift the person has logged, and every open goal.
    Every number comes from app.metrics, through lifts.history and goals.goal_numbers.

lift_chart(lift_history, goal_numbers, lift=None) and bodyweight_chart(weigh_ins, goals, today)
    The data app.charts draws, from the same numbers.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import db, links, metrics, rules, users
from app.models import Goal, User
from app.services import body, lifts
from app.services.common import exercises, no_account
from app.services.goals import goal_numbers

HOW = ("estimated max = Epley, from sets of 1 to 10 reps; one point per session (the session's best). "
       f"The current estimated max is the best session in the last {metrics.RECENT_BEST_DAYS} days, or the latest "
       "session's when none is that recent; best_estimated_max_kg is the best ever. A new best "
       "is a session best or heaviest weight above every earlier set of that lift. Trend and goal projection = a "
       f"straight line through the last {metrics.PROJECTION_DAYS // 7} weeks, needing "
       f"{metrics.PROJECTION_MIN_POINTS} points. needed_per_week is the steady change that reaches the target by "
       "its date; actual_per_week is the trend so far. Quote these numbers; do not work out your own.")


def _one(name: str, history: dict, numbers: list[dict], today: date) -> dict:
    pts = history["points"].get(name, [])
    trend = metrics.projection(pts, today)
    goals = [g for g in numbers if g["exercise"] == name]
    return {
        "exercise": name,
        "sessions": history["logged"].get(name, 0),
        "last_trained": history["last"][name].isoformat() if name in history["last"] else None,
        "estimated_max_over_time": [{"date": d.isoformat(), "estimated_max_kg": v} for d, v in pts],
        "current_estimated_max_kg": metrics.recent_best(pts, today),
        "best_set": history["best_set"].get(name),
        "bests": [{k: p[k] for k in ("date", "kind", "value", "previous")} for p in history["bests"].get(name, [])],
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
    An exercise not on the list gives unknown_exercise with the closest names.
    """
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        only = None
        if exercise not in (None, ""):
            names = [e.name for e in exercises(s, "lift")]
            only, _ = rules.match_exercise(exercise, names)
            if only is None:
                return rules.unknown_exercise(exercise, names)
        history = lifts.history(s, user.id, today)
        goals = list(s.scalars(select(Goal).where(Goal.user_id == user.id, Goal.status == "open")
                               .order_by(Goal.id).options(selectinload(Goal.exercise))))
        weighed = body.weigh_ins(s, user.id, today) if any(g.kind == "bodyweight" for g in goals) else []
        numbers = goal_numbers(goals, history, weighed, today)

        if only is not None:
            out = {"today": today.isoformat(), "lift": _one(only, history, numbers, today), "how": HOW,
                   "links": {"progress_lift": links.page("progress_lift", lift=only)}}
            if not out["lift"]["sessions"]:
                out["note"] = f"No sets of {only} logged yet."
            return out

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
        if not rows:
            out["note"] = "No lift sessions logged yet."
        return out


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


def bodyweight_chart(weigh_ins: list, goals: list[Goal], today: date) -> dict | None:
    """Data for bodyweight_svg: weigh-ins, the 7-day average at each, the open bodyweight goal.
    None with fewer than 2 weigh-ins. Every weigh-in is drawn, whatever its date, in the one order
    and labelled with its real date. Each average is the 7 days ending on that weigh-in's effective
    date, over effective dates, so the last one is bodyweight_now."""
    pts = metrics.in_order([(b.date, b.kg) for b in weigh_ins], today)
    aged = [(metrics.on_or_before(d, today), kg) for d, kg in pts]
    if len(pts) < 2:
        return None
    goal = next((g for g in goals if g.kind == "bodyweight" and g.status == "open" and g.target_value is not None),
                None)
    return {
        "points": [{"date": d.isoformat(), "kg": kg} for d, kg in pts],
        "average": [{"date": d.isoformat(), "kg": metrics.bodyweight_average(aged, metrics.on_or_before(d, today))}
                    for d, _ in pts],
        "latest": {"date": pts[-1][0].isoformat(), "kg": pts[-1][1]},
        "goal": None if goal is None else {
            "target_kg": goal.target_value, "target_date": goal.target_date.isoformat() if goal.target_date else None},
    }
