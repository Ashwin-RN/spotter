"""Every number Spotter works out. Pure functions on plain values: no database, no clock.

"today" is always passed in by the caller (from users.today_for). Dates are datetime.date values.

Dates after today. A write never accepts one, but a change of time zone can leave an entry dated
after the person's new today. Such an entry is their most recent one, so nothing here drops it:
there is no upper bound on dates, and for any "how long ago" or any window ending today, a date
after today counts as today (on_or_before). See on_or_before for the one ordering rule.

Strength
  epley(weight_kg, reps) -> float | None
      Estimated one-rep max: weight x (1 + reps / 30), 2 dp. None for reps outside 1 to 10 or weight <= 0.
  session_best(sets: [(weight_kg, reps)]) -> float | None
      The highest Epley estimate among one session's sets of one lift.
  prs(prior_sets, session_sets) -> [{"kind": "estimated_max" | "weight", "value", "previous"}]
      New bests for one lift against every earlier set of it. [] when there is no earlier set.
  recent_best(points: [(date, value)], today, days=28) -> float | None
      A lift's current estimated max: the best session in the last 28 days, else the latest session.

Training days
  days_since_last(session_dates, today) -> int | None
  sessions_vs_target(session_dates, per_week, since, today) -> {"logged", "target", "pct", "weeks"} | None
  weekly_sessions(session_dates, since, today, weeks=8) -> [{"start", "end", "logged"}], oldest first

Trends
  projection(points: [(date, value)], today, goal_target=None, goal_date=None, lower_is_better=False,
             current=None)
      -> {"slope_per_week", "projected", "status", "points_used", "latest", "weeks_left", "needed_per_week"} | None
      status: "reached", "on track" or "behind". current replaces the last point as "where the person is now".
  projection_gap(points, today) -> str | None     why there is no line, in words
  reached(value, target, lower_is_better), weeks_left(goal_date, today),
  needed_per_week(current, target, goal_date, today, lower_is_better)
  bodyweight_average(weigh_ins: [(date, kg)], end, days=7) -> float | None
  bodyweight_now(weigh_ins: [(date, kg)], today) -> float | None
      The 7-day average ending today, else the latest weigh-in.
  bodyweight_trend(weigh_ins: [(date, kg)], today) -> dict | None
"""

from datetime import date, timedelta

EPLEY_MAX_REPS = 10
RECENT_BEST_DAYS = 28
PROJECTION_DAYS = 42
PROJECTION_MIN_POINTS = 4
TARGET_MAX_WEEKS = 4
BODYWEIGHT_AVG_DAYS = 7
BODYWEIGHT_COMPARE_DAYS = 28
WEEKLY_CHART_WEEKS = 8


def on_or_before(d: date, today: date) -> date:
    """An entry's effective date: its own date, or today when it is later. Windows and ages use it.

    The ordering rule. Entries are ordered by effective date: the entry's own date, or today when
    it is later. Within one effective date, by the order they were logged (id). "Latest",
    "previous", "prior" and "newest first" all mean this order. Real dates are still what is shown.

    The readers (app.services) put rows in this order; the functions here take (date, value)
    points in the caller's order and re-sort them only by effective date, stably (in_order), so
    the caller's order decides ties.

    Known and accepted: the next day, a later-dated entry is back at its real date, so two entries
    logged on the same evening can swap places. No value changes; only which session is credited
    first with a new best.
    """
    return min(d, today)


def order_key(d: date, entry_id: int, today: date) -> tuple[date, int]:
    """The sort key of the one order, for a stored entry: (effective date, id)."""
    return on_or_before(d, today), entry_id


def in_order(points: list[tuple[date, float]], today: date) -> list[tuple[date, float]]:
    """The points in the one order: stably by effective date, so ties keep the caller's order.
    Points with no date or value are left out. Each keeps its real date."""
    return sorted(((d, v) for d, v in points if d is not None and v is not None),
                  key=lambda p: on_or_before(p[0], today))


# ---------------------------------------------------------------- strength

def epley(weight_kg: float, reps: int) -> float | None:
    """Estimated one-rep max: weight x (1 + reps / 30), rounded to 2 dp.

    None for reps outside 1 to 10 (Epley is unreliable above 10) or weight <= 0.
    Rounded so that comparisons between sessions are exact.
    """
    if weight_kg is None or reps is None:
        return None
    if weight_kg <= 0 or reps < 1 or reps > EPLEY_MAX_REPS:
        return None
    return round(weight_kg * (1 + reps / 30), 2)


def session_best(sets: list[tuple[float, int]]) -> float | None:
    """Highest Epley estimate among one session's sets for one lift. None if no set qualifies."""
    values = [e for e in (epley(w, r) for w, r in sets) if e is not None]
    return max(values) if values else None


def recent_best(points: list[tuple[date, float]], today: date, days: int = RECENT_BEST_DAYS) -> float | None:
    """A lift's current estimated max: the best session estimated max in the `days` days ending
    today (today-27 .. today by default). When no session is that recent, the latest session's
    value. None with no points. A point after today counts as today.

    One light session does not pull it down, and a best from months ago does not hold it up."""
    pts = in_order(points, today)
    if not pts:
        return None
    start = today - timedelta(days=days - 1)
    recent = [v for d, v in pts if on_or_before(d, today) >= start]
    return max(recent) if recent else pts[-1][1]


def max_weight(sets: list[tuple[float, int]]) -> float | None:
    """The heaviest weight lifted for at least one rep. None if there is none above 0."""
    weights = [w for w, r in sets if w is not None and w > 0 and r is not None and r >= 1]
    return max(weights) if weights else None


def prs(prior_sets: list[tuple[float, int]], session_sets: list[tuple[float, int]]) -> list[dict]:
    """New bests set by this session for ONE lift, against every earlier set of that lift.

    Two kinds, each strictly greater than the earlier best:
      "estimated_max": the session's best Epley (1 to 10 rep sets only) above the best earlier Epley.
      "weight": the heaviest weight this session (any reps) above the heaviest earlier weight.
    Each kind needs a baseline, so a lift's first session never reports a new best.
    Returns [{"kind", "value", "previous"}], [] if none.
    """
    if not prior_sets:
        return []
    out = []
    prev_e, now_e = session_best(prior_sets), session_best(session_sets)
    if now_e is not None and prev_e is not None and now_e > prev_e:
        out.append({"kind": "estimated_max", "value": now_e, "previous": prev_e})
    prev_w, now_w = max_weight(prior_sets), max_weight(session_sets)
    if now_w is not None and prev_w is not None and now_w > prev_w:
        out.append({"kind": "weight", "value": now_w, "previous": prev_w})
    return out


# ---------------------------------------------------------------- training days

def _ages(dates: list[date], today: date) -> list[date]:
    """The dates, each as on_or_before(today) sees it. Nothing is dropped."""
    return [on_or_before(d, today) for d in dates if d is not None]


def days_since_last(session_dates: list[date], today: date) -> int | None:
    """Days from the latest session to today. 0 = trained today (or on a date after it). None if none."""
    past = _ages(session_dates, today)
    return (today - max(past)).days if past else None


def sessions_vs_target(session_dates: list[date], per_week: int | None, since: date | None,
                       today: date) -> dict | None:
    """Sessions logged against a weekly target, over the last 4 whole weeks at most.

    weeks  = whole weeks since `since` (when the target started counting), capped at 4.
    target = per_week x weeks.
    logged = sessions dated in the same window: the last (weeks x 7) days ending today, and not
             before `since`. Both numbers cover the same days, so two weeks in is never judged on four.
    pct    = round(100 x logged / target); not capped, so it can exceed 100.
    None without a target, without `since`, or before one whole week has passed.
    """
    if since is None or not per_week or per_week <= 0:
        return None
    weeks = min(TARGET_MAX_WEEKS, max(0, (today - since).days // 7))
    target = per_week * weeks
    if target == 0:
        return None
    start = max(today - timedelta(days=7 * weeks - 1), since)
    logged = sum(1 for d in _ages(session_dates, today) if d >= start)
    return {"logged": logged, "target": target, "pct": round(100 * logged / target), "weeks": weeks}


def weekly_sessions(session_dates: list[date], since: date | None, today: date,
                    weeks: int = WEEKLY_CHART_WEEKS) -> list[dict]:
    """Sessions logged per week, oldest week first, for a weekly chart.

    Weeks are whole 7-day windows ending today (the windows sessions_vs_target uses), only those
    wholly on or after `since`, at most `weeks` of them. Each: {"start": date, "end": date, "logged": int}.
    [] without `since`, or before one whole week has passed.
    """
    if since is None:
        return []
    count = min(weeks, max(0, (today - since).days // 7))
    past = _ages(session_dates, today)
    out = []
    for n in range(count - 1, -1, -1):
        end = today - timedelta(days=7 * n)
        start = end - timedelta(days=6)
        out.append({"start": start, "end": end, "logged": sum(1 for d in past if start <= d <= end)})
    return out


# ---------------------------------------------------------------- trends

def reached(value: float | None, target: float, lower_is_better: bool = False) -> bool:
    """True when `value` is at or past the target, in the goal's direction."""
    if value is None:
        return False
    return value <= target if lower_is_better else value >= target


def weeks_left(goal_date: date, today: date) -> float:
    """Weeks from today to the goal date, 1 dp; 0 when the date is today or past."""
    days = (goal_date - today).days
    return round(days / 7, 1) if days > 0 else 0.0


def needed_per_week(current: float, target: float, goal_date: date, today: date,
                    lower_is_better: bool = False) -> float | None:
    """The steady change per week from `current` that reaches the target by goal_date, 2 dp, signed
    (a weight-loss goal gives a negative value). 0 when the target is already reached; None when
    the date is today or past. It says nothing about whether that pace is realistic."""
    if reached(current, target, lower_is_better):
        return 0.0
    days = (goal_date - today).days
    if days <= 0:
        return None
    return round((target - current) / (days / 7), 2) + 0.0


def projection(points: list[tuple[date, float]], today: date, goal_target: float | None = None,
               goal_date: date | None = None, lower_is_better: bool = False,
               current: float | None = None) -> dict | None:
    """A least-squares straight line through the points of the last 6 weeks.

    Uses points dated from today - 42 days on (one after today sits at today). Needs at least 4 such points on at least 2
    different dates, else None. slope_per_week = the fitted change per 7 days.
    latest = where the person is now: `current` when given (the caller's own measure, such as a
    7-day average), else the last point. It is the one value the status and the pace below read.
    With a goal: projected = the line's value on goal_date. status:
      "reached"   latest is at or past the target (>= when higher is better; <= when lower_is_better)
      "on track"  not yet, but projected meets the target by goal_date
      "behind"    neither
    weeks_left = (goal_date - today) in weeks, 1 dp; 0 when goal_date is today or past.
    needed_per_week = needed_per_week(latest, ...): 0 once reached; None when weeks_left is 0.
    Without a goal: projected, status, weeks_left and needed_per_week are None.
    """
    start = today - timedelta(days=PROJECTION_DAYS)
    # In the one order (in_order), each placed on the line at its effective date.
    pts = [(on_or_before(d, today), v) for d, v in in_order(points, today) if on_or_before(d, today) >= start]
    if len(pts) < PROJECTION_MIN_POINTS or len({d for d, _ in pts}) < 2:
        return None
    x0 = pts[0][0]
    xs = [(d - x0).days for d, _ in pts]
    ys = [v for _, v in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    intercept = my - slope * mx
    latest = current if current is not None else ys[-1]
    out = {"slope_per_week": round(slope * 7, 2), "projected": None, "status": None,
           "points_used": n, "latest": latest, "weeks_left": None, "needed_per_week": None}
    if goal_target is None or goal_date is None:
        return out
    projected = round(intercept + slope * (goal_date - x0).days, 2)
    out["projected"] = projected
    if reached(latest, goal_target, lower_is_better):
        out["status"] = "reached"
    else:
        out["status"] = "on track" if reached(projected, goal_target, lower_is_better) else "behind"
    out["weeks_left"] = weeks_left(goal_date, today)
    out["needed_per_week"] = needed_per_week(latest, goal_target, goal_date, today, lower_is_better)
    return out


def projection_gap(points: list[tuple[date, float]], today: date) -> str | None:
    """Why projection() draws no line, in words; None when it draws one."""
    start = today - timedelta(days=PROJECTION_DAYS)
    days = [on_or_before(d, today) for d, _ in in_order(points, today) if on_or_before(d, today) >= start]
    if len(days) < PROJECTION_MIN_POINTS:
        return f"A projection needs {PROJECTION_MIN_POINTS} points in the last {PROJECTION_DAYS // 7} weeks."
    if len(set(days)) < 2:
        return "A projection needs points on at least 2 different days."
    return None


def bodyweight_average(weigh_ins: list[tuple[date, float]], end: date,
                       days: int = BODYWEIGHT_AVG_DAYS) -> float | None:
    """Mean of the weigh-ins dated end-6 .. end (7 calendar days ending on `end`), 2 dp.

    Every weigh-in in the window counts once, however many days are missing. None if the window has none.
    """
    start = end - timedelta(days=days - 1)
    vals = [kg for d, kg in weigh_ins if d is not None and kg is not None and start <= d <= end]
    return round(sum(vals) / len(vals), 2) if vals else None


def _aged(points: list[tuple[date, float]], today: date) -> list[tuple[date, float]]:
    return [(on_or_before(d, today), v) for d, v in points if d is not None and v is not None]


def bodyweight_now(weigh_ins: list[tuple[date, float]], today: date) -> float | None:
    """Body weight now: the 7-day average ending today when that window has a weigh-in, otherwise
    the latest weigh-in. None with none. A weigh-in after today counts as today's."""
    average = bodyweight_average(_aged(weigh_ins, today), today)
    if average is not None:
        return average
    pts = in_order(weigh_ins, today)
    return pts[-1][1] if pts else None


def bodyweight_trend(weigh_ins: list[tuple[date, float]], today: date) -> dict | None:
    """How body weight is moving. None with no weigh-ins. A weigh-in after today counts as today's
    for the averages. first and latest are first and last in the one order (in_order), shown with
    their real dates.

    "7-day average": the 7-day average ending today against the one ending 28 days ago
    (today-34 .. today-28), when both windows have a weigh-in. Daily weight swings a lot; the
    averages are what to read.
    "simple change": otherwise, the latest weigh-in minus the first one (needs 2 weigh-ins).
    "single weigh-in": only one so far; change_kg is None.
    Returns {method, change_kg, now_avg, then_avg, first {date, kg}, latest {date, kg}, weigh_ins}.
    """
    pts = in_order(weigh_ins, today)
    if not pts:
        return None
    aged = _aged(pts, today)
    out = {"method": "single weigh-in", "change_kg": None, "now_avg": None, "then_avg": None,
           "first": {"date": pts[0][0].isoformat(), "kg": pts[0][1]},
           "latest": {"date": pts[-1][0].isoformat(), "kg": pts[-1][1]}, "weigh_ins": len(pts)}
    now = bodyweight_average(aged, today)
    then = bodyweight_average(aged, today - timedelta(days=BODYWEIGHT_COMPARE_DAYS))
    if now is not None and then is not None:
        out.update(method="7-day average", now_avg=now, then_avg=then, change_kg=round(now - then, 2))
    elif len(pts) >= 2:
        out.update(method="simple change", change_kg=round(pts[-1][1] - pts[0][1], 2))
    return out
