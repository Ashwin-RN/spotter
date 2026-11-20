"""Reading progress back: one lift in full, or a short line for every lift logged.

get_progress(user_id, exercise=None)
    With an exercise: its estimated max per session, the best set, every new best with its date,
    the trend over the last 6 weeks, and its open goals with the pace each needs against the pace so far.
    Without: one line per lift the person has logged, and every open goal of every kind.
    A plain word that is not a name, such as squat (contract exercise-list.md R19), never asks: with one of its
    candidates logged, that lift in full; with two or more, the every-lift form with only those lifts and their
    goals; with none, its default, as a lift never logged.
    Every number comes from app.metrics, through lifts.history and goals.load_numbers and
    goals.goal_numbers, the same path get_goals and set_goal take. With nothing logged yet, the note
    says so.

    Results are rounded where section D of stage 6 names them, once, as the result is built: an
    estimated max at 1 dp (a best and the one it beat together, lifts.rounded_best), and the goals
    through goals.rounded_numbers. The numbers worked out from stay unrounded.

    summary (contract units.md R14): progress in a few lines, in the person's units, from the unrounded figures.
    One lift: "Bench Press: estimated max now 262.5 lb. Best set 225 lb × 5 on Sun 15 Nov. 12 sessions.", then
    " Trend +1.2 lb a week over 6 weeks." with a trend. Every lift: each lift's first sentence and its sessions,
    one line a lift. A lift never done above 0: "Pull-up: most reps 12, 8 sessions." A lift of another kind than
    standard (lifts.best_kind: help, band, hold, carry) says its record (lifts.records): "Plank: longest hold 90 s,
    3 sessions.", and in the one-lift view its best set and day. A cardio activity: its sessions, then its latest
    as cardio.summary words it. With nothing logged, the note.

lift_chart(lift_history, goal_numbers, lift=None) and bodyweight_chart(weigh_ins, goals, today, since=None)
    The data app.charts draws, from the same numbers. Each body weight point carries its smoothed
    value (metrics.ewma over every weigh-in), the line the chart draws, and the 7-day average there,
    both at 1 dp. since: draw only the weigh-ins from that date on; the smoothed line is unchanged.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import db, formatting, links, metrics, rules, users
from app.models import Exercise, Goal, User
from app.services import cardio, lifts
from app.services.common import exercises, no_account
from app.services.goals import goal_numbers, load_numbers, rounded_numbers

HOW = ("estimated max = Epley, from sets of 1 to 10 reps; one point per session (the session's best). "
       f"The current estimated max is the best session in the last {metrics.RECENT_BEST_DAYS} days, or the latest "
       "session's when none is that recent; best_estimated_max_kg is the best ever. A new best "
       "beats every earlier set of that lift: a higher estimated max or a heavier weight; on a band, more reps; on "
       "an assisted lift, less help, then more reps at it. A hold's longer time counts at its weight, and a carry's "
       "heavier load at its distance. Trend and goal projection = a "
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


def _one(name: str, history: dict, numbers: list[dict], today: date, exact: bool = False, units=None) -> dict:
    """One lift. numbers: goals.rounded_numbers. history's own values are never changed. exact: the estimated
    maxes, the bests and the trend unrounded, as the lb summary takes them (get_progress' exact). Each best's text is
    lifts.best_text of the unrounded best in the person's units, the words log_lifts' new best and get_log carry:
    a hold's weight and a carry's distance are in the text alone (exercise-list.md, note of 2026-11-20)."""
    pts = history["points"].get(name, [])
    trend = metrics.projection(pts, today)
    goals = [g for g in numbers if g["exercise"] == name]
    most = (lambda v: v) if exact else _max  # noqa: E731
    best = dict if exact else lifts.rounded_best
    return {
        "exercise": name,
        "sessions": history["logged"].get(name, 0),
        "last_trained": history["last"][name].isoformat() if name in history["last"] else None,
        "estimated_max_over_time": [{"date": d.isoformat(), "estimated_max_kg": most(v)} for d, v in pts],
        "current_estimated_max_kg": most(metrics.recent_best(pts, today)),
        "best_set": history["best_set"].get(name),
        "bests": [{**best({k: p[k] for k in ("date", "kind", "value", "previous")}),
                   "text": lifts.best_text({k: p[k] for k in ("exercise", "kind", "value", "previous",
                                                              *lifts.BEST_PLACES) if k in p}, units)}
                  for p in history["bests"].get(name, [])],
        "trend": {"slope_per_week": _slope_per_week(pts, today) if exact else trend["slope_per_week"],
                  "points_used": trend["points_used"]} if trend else None,
        "goal": goals[0] if goals else None,
        "goals": goals,
    }


def _sessions(n: int) -> str:
    return f"{formatting.show(n, 'count')} session{'' if n == 1 else 's'}"


# metrics.projection's slope_per_week before its 2 dp, so a lb trend is converted once: the one fit the Goals page's
# So far reads too (goals.goal_numbers). This name stays for the tests that hold it to the projection.
_slope_per_week = metrics.slope_per_week


def _lift_words(name: str, history: dict, today: date, units, whole: bool, records: dict | None = None) -> str:
    """One lift's sentences for the summary. whole: the one-lift view, with the best set and the trend; else its
    first sentence and its sessions, on one line. records: lifts.records. A lift of another kind than standard says
    its record: "Plank: longest hold 90 s. Best set 90 s at 10 kg on Sun 15 Nov. 3 sessions.", or on one line
    "Plank: longest hold 90 s, 3 sessions."."""
    pts = history["points"].get(name, [])
    sessions = _sessions(history["logged"].get(name, 0))
    rec = (records or {}).get(name)
    if rec is not None:
        figure = lifts.record_figure(rec["kind"], (rec["weight_kg"], rec["reps"], rec["time_s"], rec["distance_m"]),
                                     units)
        if not whole:
            return f"{name}: {figure}, {sessions}."
        return (f"{name}: {figure}. Best set {lifts.record_words(name, rec, units)} on "
                f"{formatting.day(rec['date'], today)}. {sessions}.")
    now = metrics.recent_best(pts, today)
    if now is None:
        most = history["reps_only"].get(name)
        if most is not None:
            return f"{name}: most reps {formatting.show(most['reps'], 'reps')}, {sessions}."
        return f"{name}: {sessions}."
    first = f"{name}: estimated max now {formatting.show(now, 'estimated_max', units=units)}"
    if not whole:
        return f"{first}, {sessions}."
    text = first + "."
    best = history["best_set"].get(name)
    if best is not None:
        text += (f" Best set {formatting.show(best['weight_kg'], 'weight_kg', units=units)} \u00d7 "
                 f"{formatting.show(best['reps'], 'reps')} on {formatting.day(best['date'], today)}.")
    text += f" {sessions}."
    trend = metrics.projection(pts, today)
    if trend is not None:
        per_week = trend["slope_per_week"]  # kg: the result's own 2 dp figure
        if units is not None and units.weight == "lb":
            per_week = _slope_per_week(pts, today)
        text += (f" Trend {formatting.show(per_week, 'weight_rate', units=units)} "
                 f"{formatting.unit_word('weight', units)} a week over {metrics.PROJECTION_DAYS // 7} weeks.")
    return text


def get_progress(user_id: int, exercise: str | None = None, *, exact: bool = False, for_page: bool = False) -> dict:
    """One lift over time, or a summary of every lift logged.

    exact: each lift's estimated maxes, bests and trend unrounded, the figures the summary says in lb. For the
    pages of a person who keeps weights in lb, so a page never reads a step off the summary (contract units.md, the
    U2 review's note). No MCP function passes it; the summary and the goals are the same either way.

    for_page (hardening D6): a lift's result, one or every, also carries best_sets and reps_only, lifts.history's
    best_set and reps_only from the same history, so Progress and a lift's page work the history out once, and
    records (lifts.records) when a lift of another kind than standard is logged. A retired lift's name is read too
    (contract exercise-list.md R2: the pages read a retired lift's history); without for_page it is refused as any
    name not on the list, since every matcher leaves retired rows out. No MCP function passes it either.

    With exercise: {today, lift {exercise, sessions, last_trained, estimated_max_over_time [{date,
    estimated_max_kg}], current_estimated_max_kg, best_set, bests [{date, kind, value, previous, text}], trend
    {slope_per_week, points_used} | None, goal, goals}, how, links[, note]}.
    Without: {today, lifts [{exercise, sessions, last_trained, current_estimated_max_kg,
    best_estimated_max_kg, slope_per_week, goal_status}], goals, how, links[, note]}.
    A cardio activity: {today, cardio {exercise, sessions [{id, date, minutes, distance_km, pace, estimate,
    machine_kcal}] (newest first, in the one order), week {minutes, target_minutes}}, how, links[, note]}.
    week counts all cardio in the 7 days ending today, as the weekly cardio target does.
    Each also carries summary (see the module notes).
    An exercise not on the list (lifts and cardio) gives unknown_exercise with the closest names.
    """
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        units = users.units_for(user)
        only, several = None, None
        if exercise not in (None, ""):
            listed = exercises(s) + (_retired(s) if for_page else [])
            names = [e.name for e in listed]
            only, word, candidates = rules.find_exercise(exercise, names)
            if only is None and word is None:
                return rules.unknown_exercise(exercise, names)
            activity = next((e for e in listed if e.name == only), None)
            if activity is not None and activity.kind == "cardio":
                return _cardio(s, user, activity, today, units)
        history = lifts.history(s, user.id, today)
        records = lifts.records(history["sessions"])
        if exercise not in (None, "") and only is None:  # a plain word: the candidates the person logged (R19)
            logged = [c for c in candidates if history["logged"].get(c)]
            if len(logged) >= 2:
                several = set(logged)
            else:
                only = logged[0] if logged else candidates[0]
        goals = list(s.scalars(select(Goal).where(Goal.user_id == user.id, Goal.status == "open")
                               .order_by(Goal.id).options(selectinload(Goal.exercise))))
        loaded = load_numbers(s, user.id, today, goals, lift_history=history)
        numbers = rounded_numbers(goal_numbers(goals, loaded, today, units=units))

        if only is not None:
            one = _one(only, history, numbers, today, exact, units)
            note = None if one["sessions"] else f"No sets of {only} logged yet."
            out = {"today": today.isoformat(), "lift": one, "how": HOW,
                   "summary": note or _lift_words(only, history, today, units, whole=True, records=records),
                   "links": {"progress_lift": links.page("progress_lift", lift=only)}}
            return _with_note(_for_page(out, history, records, units) if for_page else out, note)

        if several is not None:
            numbers = [g for g in numbers if g.get("exercise") in several]
        rows = []
        for name in sorted(n for n in history["logged"] if several is None or n in several):
            one = _one(name, history, numbers, today, exact, units)
            rows.append({"exercise": name, "sessions": one["sessions"], "last_trained": one["last_trained"],
                         "current_estimated_max_kg": one["current_estimated_max_kg"],
                         "best_estimated_max_kg": (one["best_set"] or {}).get("estimated_max_kg"),
                         "slope_per_week": one["trend"]["slope_per_week"] if one["trend"] else None,
                         "goal_status": one["goal"]["status"] if one["goal"] else None})
        note = None if rows else "No lift sessions logged yet."
        out = {"today": today.isoformat(), "lifts": rows, "goals": numbers, "how": HOW,
               "summary": note or "\n".join(_lift_words(r["exercise"], history, today, units, whole=False,
                                                         records=records) for r in rows),
               "links": {"progress": links.page("progress")}}
        return _with_note(_for_page(out, history, records, units) if for_page else out, note)


def _retired(s) -> list[Exercise]:
    """The retired lifts, for a page's read alone (get_progress' for_page)."""
    return list(s.scalars(select(Exercise).where(Exercise.retired.is_(True), Exercise.kind == "lift")
                          .order_by(Exercise.id)))


def _for_page(out: dict, history: dict, records: dict, units=None) -> dict:
    """The result with the page's extras (get_progress' for_page): best_sets and reps_only, and when any lift of
    another kind than standard is logged, records and best_words: {lift: each of its bests in words for a page, in
    the order of its bests}, naming where each stands (lifts.best_phrase), with a no-break space before each unit and no
    lift name: a result's best text has both the other way."""
    out = {**out, "best_sets": history["best_set"], "reps_only": history["reps_only"]}
    if records:
        out["records"] = records
        out["best_words"] = {lift: [lifts.best_phrase(b, units, page=True) for b in history["bests"].get(lift, [])]
                             for lift in records}
    return out


def _with_note(out: dict, note: str | None) -> dict:
    """The result, with its note when it has one."""
    if note:
        out["note"] = note
    return out


def _cardio(s, user, activity, today, units=None) -> dict:
    """One cardio activity: its sessions, newest first in the one order (cardio.sessions_of, the list
    get_cardio gives too), and this week's minutes. The summary: the sessions, then the latest's words."""
    rows = cardio.sessions_of(s, user, activity, today)
    sessions = [{k: d[k] for k in ("id", "date", "minutes", "distance_km", "pace", "estimate", "machine_kcal")}
                for d in (cardio.cardio_dict(c) for c in rows)]
    note = None if sessions else f"No {activity.name} logged yet."
    out = {"today": today.isoformat(),
           "cardio": {"exercise": activity.name, "sessions": sessions, "week": cardio.week_minutes(s, user, today)},
           "how": CARDIO_HOW,
           "summary": note or (f"{activity.name}: {_sessions(len(rows))}. "
                               f"Latest: {cardio.summary(rows[0], today, units)}"),
           "links": {"progress": links.page("progress")}}
    return _with_note(out, note)


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


def bodyweight_chart(weigh_ins: list, goals: list[Goal], today: date, since: date | None = None,
                     exact: bool = False) -> dict | None:
    """Data for bodyweight_svg: weigh-ins with their smoothed value, the 7-day average at each, the
    open bodyweight goal. Every weigh-in is drawn, whatever its date, in the one order and labelled
    with its real date; with since, only those whose effective date is on or after it. smoothed is
    metrics.ewma over every weigh-in, the window's or not (the line get_trends reports). Each average
    is the 7 days ending on that weigh-in's effective date, over every weigh-in's effective date, so
    the last one is bodyweight_now. None with fewer than 2 weigh-ins drawn. exact: smoothed and each average
    unrounded, as get_trends' exact gives them, for a page in lb."""
    shown = (lambda v: v) if exact else _derived  # noqa: E731
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
        "points": [{"date": d.isoformat(), "kg": kg, "smoothed": shown(sm)} for (d, kg), sm in drawn],
        "average": [{"date": d.isoformat(),
                     "kg": shown(metrics.bodyweight_average(aged, metrics.on_or_before(d, today)))}
                    for d, _ in pts],
        "latest": {"date": pts[-1][0].isoformat(), "kg": pts[-1][1]},
        "goal": None if goal is None else {
            "target_kg": goal.target_value, "target_date": goal.target_date.isoformat() if goal.target_date else None},
    }
