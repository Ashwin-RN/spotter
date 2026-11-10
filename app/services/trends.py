"""Trends: "how am I doing?" in numbers worked out from the person's own logs.

get_trends(user_id)
    A read. Five parts, each from app.metrics:
      bodyweight   now (the 7-day average, else the latest weigh-in), the smoothed weight at the latest
                   weigh-in (metrics.ewma, alpha 0.1, over every weigh-in in the one order), its rate a
                   week (metrics.trend_rate: the latest weigh-in in the last 7 days against the last one
                   14 to 42 days ago), and the last 28 days' weigh-ins with their smoothed values.
                   Null with no weigh-in at all. Without the rate's two ends the rate is null and the
                   note says which end is missing.
      calories     the average a logged day over the last 7 and the last 28 days, whole kcal; a logged
                   day is a day with at least one live food entry. The share of the 28 days' kcal that
                   is Claude's estimates. Null when no food is logged in the last 28 days.
      sessions     live lift sessions in the 7 days ending today, the weekly target, and the last 28
                   days / 4, 1 dp: a rolling week, as cardio's (decision 16).
      cardio       minutes in the 7 days ending today (cardio.week_minutes's rule), the target, and the
                   last 28 days / 4, 1 dp.
      maintenance  metrics.maintenance over the 21 days ending today, or null with maintenance_note
                   saying what it still needs. It never sets the calorie target.
    An entry dated after today counts as today's (metrics.on_or_before). includes_sample is true when
    any row read is sample data, and the note then says so (common.SAMPLE_NOTE).
"""

from datetime import timedelta

from sqlalchemy import select

from app import db, links, metrics, users
from app.models import CardioEntry, FoodEntry, LiftSession, User
from app.services import body
from app.services.common import SAMPLE_NOTE, no_account

WINDOW_DAYS = 28
WEEK_DAYS = 7

TRENDS_HOW = (
    f"bodyweight.now is the 7-day average, else the latest weigh-in. smoothed is an exponentially weighted moving "
    f"average over every weigh-in in date order (alpha {metrics.EWMA_ALPHA:g}: each step moves a tenth of the way "
    f"to the new weigh-in), at the latest one. rate_per_week is the smoothed weight's change between the latest "
    f"weigh-in, which must be in the last {metrics.RATE_LATEST_DAYS} days, and the last weigh-in "
    f"{metrics.RATE_REFERENCE_DAYS[0]} to {metrics.RATE_REFERENCE_DAYS[1]} days ago, scaled to 7 days; "
    "rate_window names them. Calories are averages over logged days (a day with at least one food entry) in the "
    "last 7 and 28 days, whole kcal. Sessions and cardio count the 7 days ending today; the per-week figures are "
    f"the last 28 days divided by 4. maintenance is the average intake over the logged days of the last "
    f"{metrics.MAINTENANCE_DAYS} (at least {metrics.MAINTENANCE_MIN_LOGGED} needed), less the change in the 7-day "
    f"average weight over those {metrics.MAINTENANCE_DAYS} days x {metrics.KCAL_PER_KG:,} kcal per kg / "
    f"{metrics.MAINTENANCE_DAYS}; it is an estimate for the person to weigh up and never changes their calorie "
    "target. Quote these numbers; do not work out your own.")


def _plural(n, word: str) -> str:
    return f"{n:g} {word}" if n == 1 else f"{n:g} {word}s"


def _bodyweight(weighed: list, today, start) -> tuple[dict | None, str | None]:
    pts = [(b.date, b.kg) for b in weighed]  # already in the one order
    if not pts:
        return None, None
    smoothed = metrics.ewma(pts, today)
    rate = metrics.trend_rate(smoothed, today)
    series = [{"date": d.isoformat(), "kg": kg, "smoothed": sm}
              for (d, kg), (_, sm) in zip(metrics.in_order(pts, today), smoothed)
              if metrics.on_or_before(d, today) >= start]
    out = {"now": metrics.bodyweight_now(pts, today), "smoothed": smoothed[-1][1],
           "rate_per_week": rate["per_week"] if rate else None,
           "rate_window": {k: rate[k] for k in ("from", "to", "days")} if rate else None,
           "weigh_ins_28d": len(series), "series": series}
    return out, None if rate else metrics.trend_rate_gap(smoothed, today)


def _calories(user, eaten: list, today) -> tuple[dict | None, list]:
    """The calories part, and (date, kcal) for each logged day, for the maintenance estimate."""
    totals: dict = {}
    estimated = 0
    for f in eaten:
        d = metrics.on_or_before(f.date, today)
        totals[d] = totals.get(d, 0) + int(f.kcal)
        estimated += int(f.kcal) if f.source == "claude_estimate" else 0
    if not totals:
        return None, []
    week = [kcal for d, kcal in totals.items() if d >= today - timedelta(days=WEEK_DAYS - 1)]
    whole = sum(totals.values())
    return {"avg_7d": int(round(sum(week) / len(week))) if week else None, "days_logged_7d": len(week),
            "avg_28d": int(round(whole / len(totals))), "days_logged_28d": len(totals),
            "estimated_share_pct_28d": round(100 * estimated / whole) if whole else 0,
            "target_kcal": user.calorie_target_kcal}, sorted(totals.items())


def _summary(weight, calories, sessions, moved, maintenance, maintenance_note) -> str:
    parts = []
    if weight is None:
        parts.append("No weigh-ins yet.")
    else:
        text = f"Body weight {weight['now']:g} kg now, smoothed {weight['smoothed']:g} kg"
        if weight["rate_per_week"] is not None:
            text += f", {weight['rate_per_week']:+g} kg a week"
        parts.append(text + ".")
    if calories is None:
        parts.append("No food logged in the last 28 days.")
    else:
        text = "Calories "
        if calories["avg_7d"] is not None:
            text += f"{calories['avg_7d']:,} a day over {calories['days_logged_7d']} logged days of the last 7, "
        parts.append(text + f"{calories['avg_28d']:,} over {calories['days_logged_28d']} of the last 28.")
    target = sessions["target_per_week"]
    parts.append(f"{_plural(sessions['this_week'], 'lift session')} in the last 7 days"
                 + (f", target {target}" if target is not None else "") + ".")
    target = moved["target_minutes"]
    parts.append(f"{moved['minutes_this_week']:g} cardio minutes in the last 7 days"
                 + (f", target {target}" if target is not None else "") + ".")
    if maintenance is not None:
        parts.append(f"Maintenance about {maintenance['kcal']:,} kcal a day, from {maintenance['days_logged']} logged "
                     f"days of the last {maintenance['window_days']}.")
    else:
        parts.append(f"Maintenance: {maintenance_note[0].lower()}{maintenance_note[1:]}")
    return " ".join(parts)


def get_trends(user_id: int) -> dict:
    """How the person is doing, from their own logs. See the module notes.

    Returns {today, bodyweight {now, smoothed, rate_per_week, rate_window {from, to, days} | null,
    weigh_ins_28d, series [{date, kg, smoothed}]} | null, calories {avg_7d, days_logged_7d, avg_28d,
    days_logged_28d, estimated_share_pct_28d, target_kcal} | null, sessions {this_week, target_per_week,
    per_week_4w}, cardio {minutes_this_week, target_minutes, minutes_per_week_4w}, maintenance {kcal,
    intake_avg_kcal, weight_change_kg, days_logged, window_days, from, to} | null, maintenance_note,
    includes_sample, how, summary, links {progress, goals, settings}[, note]} or {error, message, fix}.
    A read: no lock, no write limit."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        start = today - timedelta(days=WINDOW_DAYS - 1)
        week = today - timedelta(days=WEEK_DAYS - 1)
        weighed = body.weigh_ins(s, user.id, today)
        eaten = list(s.scalars(select(FoodEntry).where(
            FoodEntry.user_id == user.id, FoodEntry.deleted_at.is_(None), FoodEntry.date >= start)))
        trained = list(s.scalars(select(LiftSession).where(
            LiftSession.user_id == user.id, LiftSession.deleted_at.is_(None), LiftSession.date >= start)))
        moved = list(s.scalars(select(CardioEntry).where(
            CardioEntry.user_id == user.id, CardioEntry.deleted_at.is_(None), CardioEntry.date >= start)))

        weight, rate_note = _bodyweight(weighed, today, start)
        calories, intake = _calories(user, eaten, today)
        sessions = {"this_week": sum(1 for t in trained if metrics.on_or_before(t.date, today) >= week),
                    "target_per_week": user.target_sessions_per_week,
                    "per_week_4w": round(len(trained) / 4, 1)}
        minutes_week = sum(c.minutes for c in moved if metrics.on_or_before(c.date, today) >= week)
        cardio = {"minutes_this_week": round(minutes_week, 1), "target_minutes": user.target_cardio_minutes_per_week,
                  "minutes_per_week_4w": round(sum(c.minutes for c in moved) / 4, 1)}
        points = [(b.date, b.kg) for b in weighed]
        maintenance = metrics.maintenance(intake, points, today)
        maintenance_note = None if maintenance else metrics.maintenance_gap(intake, points, today)
        sample = any(row.is_sample for row in (*weighed, *eaten, *trained, *moved))
        out = {"today": today.isoformat(), "bodyweight": weight, "calories": calories, "sessions": sessions,
               "cardio": cardio, "maintenance": maintenance, "maintenance_note": maintenance_note,
               "includes_sample": sample, "how": TRENDS_HOW,
               "summary": _summary(weight, calories, sessions, cardio, maintenance, maintenance_note),
               "links": {"progress": links.page("progress"), "goals": links.page("goals"),
                         "settings": links.page("settings")}}
        note = " ".join(t for t in (rate_note, SAMPLE_NOTE if sample else None) if t)
        if note:
            out["note"] = note
        return out
