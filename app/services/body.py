"""Bodyweight: one live weigh-in per day.

log_bodyweight(user_id, data, by)
    data: kg (20 to 400), date (YYYY-MM-DD; blank = the person's today; never after today; at most
    two years back), allow_repeat (optional).

    - The same weight on the same date as a live weigh-in logged in the last 10 minutes is a
      duplicate: that weigh-in comes back with duplicate true, and nothing is written.
    - Any other weigh-in on a date that already has a live one replaces it, whether the weight
      differs or the same weight comes again later (or with allow_repeat). The old row is
      soft-deleted, so restore_entry can bring it back, and the result names it under "replaced".

    The schema allows one live row per person and date (a partial unique index). The old row is
    soft-deleted and flushed on its own before the new one is added, so the order is explicit.

weigh_ins(s, user_id, today)
    Every live weigh-in up to today, oldest first.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db, links, metrics, rules, users
from app.models import Bodyweight
from app.services.common import (
    check_by, check_data, dedupe_key, duplicate_note, find_duplicate, in_range, lock_user, no_account, row_dict,
    truthy,
)

KIND = "bodyweight"
BODYWEIGHT_KG = (20, 400)
RECENT_WEIGH_INS = 5
HOW = ("7-day average = mean of the weigh-ins in the 7 days ending on a date. The trend compares the average "
       "ending today with the one ending 4 weeks ago; without both, it is the simple change from the first "
       "weigh-in to the latest. Quote these numbers; do not work out your own.")


def weigh_ins(s: Session, user_id: int, today: date) -> list[Bodyweight]:
    return list(s.scalars(select(Bodyweight).where(
        Bodyweight.user_id == user_id, Bodyweight.deleted_at.is_(None), Bodyweight.date <= today,
    ).order_by(Bodyweight.date)))


def _check(data: dict, today: date) -> tuple[date | None, float | None, dict | None]:
    errors = []
    on, err = rules.entry_date(data.get("date"), today, "weigh-ins")
    if err:
        errors.append(err)
    lo, hi = BODYWEIGHT_KG
    fix = f"Give the body weight in kg, {lo} to {hi}."
    kg = None
    if data.get("kg") in (None, ""):
        errors.append(rules.error("missing_field", "kg is missing.", fix))
    else:
        kg, err = in_range(data["kg"], "kg", lo, hi, fix)
        if err:
            errors.append(err)
    if errors:
        return None, None, rules.combine(errors)
    return on, kg, None


def log_bodyweight(user_id: int, data: dict | None, by: str) -> dict:
    """Record a weigh-in and return the trend.

    Returns {kind, id, duplicate, weigh_in, replaced, recent [{date, kg}] (newest first), trend, how,
    links[, note]} or {error, message, fix}. replaced: the weigh-in this one replaced, or None.
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
        on, kg, err = _check(data, today)
        if err:
            return err
        key = dedupe_key(on, kg)

        first = None if truthy(data.get("allow_repeat")) else find_duplicate(s, Bodyweight, user.id, on, key)
        if first is not None:
            row, replaced, note = first, None, duplicate_note("weigh-in")
        else:
            live = s.scalar(select(Bodyweight).where(Bodyweight.user_id == user.id, Bodyweight.date == on,
                                                     Bodyweight.deleted_at.is_(None)))
            replaced = note = None
            if live is not None:
                live.deleted_at = db.utcnow()
                # Its own flush, so the old row is out of the one-live-per-day index before the new
                # row exists, by construction rather than by the order SQLAlchemy picks within a flush.
                s.flush()
                replaced = row_dict(live)
                note = (f"Replaced the {on.isoformat()} weigh-in of {live.kg:g} kg (id {live.id}): one weigh-in a "
                        "day. restore_entry with that id brings it back.")
            row = Bodyweight(user_id=user.id, date=on, kg=kg, logged_by=by, dedupe_key=key)
            s.add(row)
            s.flush()

        points = [(b.date, b.kg) for b in weigh_ins(s, user.id, today)]
        out = {
            "kind": KIND, "id": row.id, "duplicate": first is not None, "weigh_in": row_dict(row),
            "replaced": replaced,
            "recent": [{"date": d.isoformat(), "kg": w} for d, w in reversed(points[-RECENT_WEIGH_INS:])],
            "trend": metrics.bodyweight_trend(points, today), "how": HOW,
            "links": {"progress": links.page("progress")},
        }
        if note:
            out["note"] = note
        return out
