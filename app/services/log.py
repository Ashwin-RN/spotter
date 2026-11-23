"""The log: what a person did, day by day.

get_log(user_id, date=None, days=1)
    days: 1 to 14. date: the last day shown (YYYY-MM-DD); default the person's today; a date after
    today is date_in_future, and one before the oldest day the account can hold (oldest_day) is date_too_old,
    but with any_date, the day page's: it draws any day.
    Every day in the range is listed, newest first, empty days included.

    Each day: its sessions (with sets, and that session's new bests), its weigh-in or null, its
    measurements, its food (by meal, with the day's numbers from food.day_numbers, the same helper
    log_food uses) and its cardio (each entry with its activity, pace, estimate and machine number).
    A food entry logged from a saved food also carries saved_food_name and serving_label (null
    otherwise), so a page can show it as the name, the servings and the serving (decision 14). Each entry carries
    its protein_g, carbs_g, fat_g, macros_estimated and portion, null when not logged, and a day whose entries hold a
    macro carries food.macros, from the same helper (contract macros-and-asking.md).
    A session's new bests come from lifts.history, the one computation that also
    feeds get_progress, so the two never disagree; an estimated max and the one it beat are rounded
    together (lifts.rounded_best), as get_progress rounds them.

    With date left out, an entry dated after today (left there by a change of time zone) is not
    hidden: its day gets a block of its own above today (the one ordering rule, metrics.on_or_before).
    Within a day, entries are in the order they were logged.

    Each day's summary (contract units.md R14), in the person's units: the sets of each session as
    lifts.sets_text words them, then the weigh-in, then each cardio entry as cardio.summary words it; empty when
    the day has none. A new best's text is in the person's units too, from the unrounded best.

    Returns {from, to, days [{date, sessions, weigh_in, measurements, food, cardio, summary}], totals {sessions,
    weigh_ins, measurements, food_entries, cardio_entries}, summary, links {day: <to>,
    progress}} or {error, message, fix}. A read: no lock, no write limit.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import db, formatting, links, rules, users
from app.models import MEALS, MEASUREMENT_KINDS, Bodyweight, CardioEntry, FoodEntry, Measurement, User
from app.services import cardio, food, lifts
from app.services.common import no_account, row_dict

DAYS = (1, 14)


def oldest_day(user: User):
    """The oldest day an entry of this account can be dated: its created_at date in UTC, less rules.OLDEST_DAYS, less
    one day for the time zone. get_log refuses an end date before it; a window may start before it."""
    return user.created_at.date() - timedelta(days=rules.OLDEST_DAYS + 1)


def _day_summary(day_sessions: list, weigh_in, cardio_rows: list, today, units) -> str:
    """A day's lift sessions, weigh-in and cardio in words (R14), in the person's units; "" for a day with none."""
    parts = [lifts.sets_text([(x.exercise.name, x.weight_kg, x.reps, x.time_s, x.distance_m)
                              for x in sorted(t.sets, key=lambda x: x.id)], units) + "." for t in day_sessions if t.sets]
    if weigh_in is not None:
        parts.append(f"Weigh-in {formatting.show(weigh_in.kg, 'weigh_in', units=units)}.")
    parts += [cardio.summary(c, today, units) for c in cardio_rows]
    return " ".join(parts)


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{formatting.show(n, 'count')} {word if n == 1 else many or word + 's'}"


def _days(value) -> tuple[int | None, dict | None]:
    lo, hi = DAYS
    fix = f"Give days as a whole number from {lo} to {hi}."
    if value is None:
        return 1, None
    number = rules.to_float(value)
    if number is not None and number == int(number) and not lo <= number <= hi:
        return None, rules.error("invalid_field", f"days {rules.as_sent(value)} is outside {lo} to {hi}.", fix)
    if number is None or number != int(number):
        return None, rules.error("invalid_field",
                                 f"days '{rules.quote(value)}' is not a whole number from {lo} to {hi}.", fix)
    return int(number), None


def get_log(user_id: int, date=None, days=1, any_date: bool = False) -> dict:
    """See the module notes. any_date: the day page's read, which shows any day it can draw (oldest_day is
    get_log's floor through MCP alone)."""
    count, err = _days(days)
    if err:
        return err
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        units = users.units_for(user)
        if date is None or (isinstance(date, str) and not date.strip()):
            end, later_too = today, True
        else:
            end, later_too = rules.to_date(date), False
            if end is None:
                return rules.error("invalid_date", f"date '{rules.quote(date)}' is not a date.",
                                   "Give the date as YYYY-MM-DD, or leave it out for today.")
            if end > today:
                return rules.error("date_in_future", f"date {end.isoformat()} is after today ({today.isoformat()}).",
                                   f"Give today or an earlier date. {rules.TODAY_NOTE}")
            floor = oldest_day(user)
            if end < floor and not any_date:  # inputs.md decision 8: before any day an entry of this account can be dated
                return rules.error("date_too_old", f"date {end.isoformat()} is before {floor.isoformat()}, the oldest "
                                   "day this account can hold.",
                                   f"Give a date from {floor.isoformat()} to today, or leave it out for today.")
        start = end - timedelta(days=count - 1)

        def shown(d) -> bool:
            return start <= d <= end or (later_too and d > end)

        # Bounded in SQL (hardening D3): a date sent ends every read there, the lift history too when that day is before
        # today. Undated, a row dated after today keeps its own block, so there is no upper bound.
        bounded = not later_too and end < today
        history = lifts.history(s, user.id, today, last_day=end) if bounded else lifts.history(s, user.id, today)
        until = [] if later_too else [end]
        bests: dict[int, list[dict]] = {}
        for per_lift in history["bests"].values():
            for b in per_lift:
                raw = {k: b[k] for k in ("exercise", "kind", "value", "previous", *lifts.BEST_PLACES) if k in b}
                bests.setdefault(b["session_id"], []).append(lifts.shown_best(raw, units))  # where it stands too
        sessions = [t for t in history["sessions"] if shown(t.date)]
        weigh_ins = [b for b in s.scalars(select(Bodyweight).where(
            Bodyweight.user_id == user.id, Bodyweight.deleted_at.is_(None), Bodyweight.date >= start,
            *(Bodyweight.date <= d for d in until))
            .order_by(Bodyweight.id)) if shown(b.date)]
        kinds = {k: n for n, k in enumerate(MEASUREMENT_KINDS)}
        measured = sorted((m for m in s.scalars(select(Measurement).where(
            Measurement.user_id == user.id, Measurement.deleted_at.is_(None), Measurement.date >= start,
            *(Measurement.date <= d for d in until)))
            if shown(m.date)), key=lambda m: (kinds.get(m.kind, len(kinds)), m.id))

        eaten = [f for f in s.scalars(select(FoodEntry).where(
            FoodEntry.user_id == user.id, FoodEntry.deleted_at.is_(None), FoodEntry.date >= start,
            *(FoodEntry.date <= d for d in until))
            .order_by(FoodEntry.id).options(selectinload(FoodEntry.saved_food))) if shown(f.date)]
        moved = [c for c in s.scalars(select(CardioEntry).where(
            CardioEntry.user_id == user.id, CardioEntry.deleted_at.is_(None), CardioEntry.date >= start,
            *(CardioEntry.date <= d for d in until))
            .order_by(CardioEntry.id).options(selectinload(CardioEntry.exercise))) if shown(c.date)]

        dates = [end - timedelta(days=n) for n in range(count)]
        later = sorted({e.date for e in (*sessions, *weigh_ins, *measured, *eaten, *moved) if e.date > end},
                       reverse=True)
        out_days = []
        for day in later + dates:
            weighed = [b for b in weigh_ins if b.date == day]
            food_rows = [f for f in eaten if f.date == day]
            cardio_rows = [c for c in moved if c.date == day]
            meals = {meal: [{**food.entry_dict(f), **food.saved_parts(f)} for f in food_rows if f.meal == meal]
                     for meal in MEALS}
            day_sessions = [t for t in sessions if t.date == day]
            out_days.append({
                "date": day.isoformat(),
                "sessions": [{**lifts.session_dict(t), "new_bests": bests.get(t.id, [])} for t in day_sessions],
                "weigh_in": row_dict(weighed[-1]) if weighed else None,
                "measurements": [row_dict(m) for m in measured if m.date == day],
                "food": {"meals": meals, **food.day_numbers(user, food_rows, cardio_rows)},
                "cardio": [cardio.cardio_dict(c) for c in cardio_rows],
                "summary": _day_summary(day_sessions, weighed[-1] if weighed else None, cardio_rows, today, units),
            })
        totals = {"sessions": len(sessions), "weigh_ins": len(weigh_ins), "measurements": len(measured),
                  "food_entries": len(eaten), "cardio_entries": len(moved)}
        summary = (f"{_plural(len(out_days), 'day')}, {_plural(totals['sessions'], 'session')}, "
                   f"{_plural(totals['weigh_ins'], 'weigh-in')}, {_plural(totals['measurements'], 'measurement')}, "
                   f"{_plural(totals['food_entries'], 'food entry', 'food entries')}, "
                   f"{_plural(totals['cardio_entries'], 'cardio session')}.")
        return {"from": start.isoformat(), "to": end.isoformat(), "days": out_days, "totals": totals,
                "summary": summary,
                "links": {"day": links.page("day", date=end), "progress": links.page("progress")}}
