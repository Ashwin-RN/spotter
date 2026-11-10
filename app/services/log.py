"""The log: what a person did, day by day.

get_log(user_id, date=None, days=1)
    days: 1 to 14. date: the last day shown (YYYY-MM-DD); default the person's today; a date after
    today is date_in_future. Every day in the range is listed, newest first, empty days included.

    Each day: its sessions (with sets, and that session's new bests), its weigh-in or null, its
    measurements. A session's new bests come from lifts.history, the one computation that also
    feeds get_progress, so the two never disagree.

    With date left out, an entry dated after today (left there by a change of time zone) is not
    hidden: its day gets a block of its own above today (the one ordering rule, metrics.on_or_before).
    Within a day, entries are in the order they were logged.

    Returns {from, to, days [{date, sessions, weigh_in, measurements}], totals {sessions, weigh_ins,
    measurements}, summary, links {day: <to>, progress}} or {error, message, fix}. A read: no lock,
    no write limit.
"""

from datetime import timedelta

from sqlalchemy import select

from app import db, links, rules, users
from app.models import MEASUREMENT_KINDS, Bodyweight, Measurement, User
from app.services import lifts
from app.services.common import no_account, row_dict

DAYS = (1, 14)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _days(value) -> tuple[int | None, dict | None]:
    lo, hi = DAYS
    fix = f"Give days as a whole number from {lo} to {hi}."
    if value is None:
        return 1, None
    number = rules.to_float(value)
    if number is None or number != int(number) or not lo <= number <= hi:
        return None, rules.error("invalid_field",
                                 f"days '{rules.quote(value)}' is not a whole number from {lo} to {hi}.", fix)
    return int(number), None


def get_log(user_id: int, date=None, days=1) -> dict:
    """See the module notes."""
    count, err = _days(days)
    if err:
        return err
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
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
        start = end - timedelta(days=count - 1)

        def shown(d) -> bool:
            return start <= d <= end or (later_too and d > end)

        history = lifts.history(s, user.id, today)
        bests: dict[int, list[dict]] = {}
        for per_lift in history["bests"].values():
            for b in per_lift:
                best = {k: b[k] for k in ("exercise", "kind", "value", "previous")}
                bests.setdefault(b["session_id"], []).append({**best, "text": lifts.best_text(best)})
        sessions = [t for t in history["sessions"] if shown(t.date)]
        weigh_ins = [b for b in s.scalars(select(Bodyweight).where(
            Bodyweight.user_id == user.id, Bodyweight.deleted_at.is_(None), Bodyweight.date >= start)
            .order_by(Bodyweight.id)) if shown(b.date)]
        kinds = {k: n for n, k in enumerate(MEASUREMENT_KINDS)}
        measured = sorted((m for m in s.scalars(select(Measurement).where(
            Measurement.user_id == user.id, Measurement.deleted_at.is_(None), Measurement.date >= start))
            if shown(m.date)), key=lambda m: (kinds.get(m.kind, len(kinds)), m.id))

        dates = [end - timedelta(days=n) for n in range(count)]
        later = sorted({e.date for e in (*sessions, *weigh_ins, *measured) if e.date > end}, reverse=True)
        out_days = []
        for day in later + dates:
            weighed = [b for b in weigh_ins if b.date == day]
            out_days.append({
                "date": day.isoformat(),
                "sessions": [{**lifts.session_dict(t), "new_bests": bests.get(t.id, [])}
                             for t in sessions if t.date == day],
                "weigh_in": row_dict(weighed[-1]) if weighed else None,
                "measurements": [row_dict(m) for m in measured if m.date == day],
            })
        totals = {"sessions": len(sessions), "weigh_ins": len(weigh_ins), "measurements": len(measured)}
        summary = (f"{_plural(len(out_days), 'day')}, {_plural(totals['sessions'], 'session')}, "
                   f"{_plural(totals['weigh_ins'], 'weigh-in')}, {_plural(totals['measurements'], 'measurement')}.")
        return {"from": start.isoformat(), "to": end.isoformat(), "days": out_days, "totals": totals,
                "summary": summary, "links": {"day": links.page("day", date=end), "progress": links.page("progress")}}
