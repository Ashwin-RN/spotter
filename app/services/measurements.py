"""Measurements: waist, hips, chest, arm, thigh, steps, sleep, resting heart rate, water.

log_measurement(user_id, data, by)
    data: kind (one of rules.MEASUREMENTS; case, spaces and underscores forgiven), value (in the
    kind's unit and range; whole numbers for steps, resting_hr and water_ml; 2 dp otherwise), date
    (YYYY-MM-DD; blank = the person's today; never after today; at most two years back),
    allow_repeat (optional).

    The weigh-in rules, per kind:
    - The same kind, date and value as a live entry logged in the last 10 minutes is a duplicate:
      that entry comes back with duplicate true, and nothing is written.
    - Any other value of that kind on a date that already has a live one replaces it. The old row is
      soft-deleted (restore_entry swaps it back) and named under "replaced".
    One live row per person, kind and date is a partial unique index (migration 0002). The old row
    is soft-deleted and flushed on its own before the new one is added.

    A write: lock_user first, then common.check_write_limit, then everything else.

measurements_for(s, user_id, today, kind=None)
    Live measurements, of one kind or all, in the one order (metrics.order_key).
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db, links, metrics, rules, users
from app.models import Measurement
from app.services.common import (
    check_by, check_data, check_write_limit, dedupe_key, duplicate_note, find_duplicate, lock_user, no_account,
    row_dict, truthy,
)

KIND = "measurement"
RECENT = 10


def measurement_key(on: date, kind: str, value: float) -> str:
    """What makes two measurements the same: the date, the kind and the value."""
    return dedupe_key(on, kind, float(value))


def measurements_for(s: Session, user_id: int, today: date, kind: str | None = None) -> list[Measurement]:
    query = select(Measurement).where(Measurement.user_id == user_id, Measurement.deleted_at.is_(None))
    if kind is not None:
        query = query.where(Measurement.kind == kind)
    return sorted(s.scalars(query), key=lambda m: metrics.order_key(m.date, m.id, today))


def describe(m: Measurement) -> str:
    """'Waist 84 cm on 2026-11-10'."""
    unit = "" if m.unit == m.kind else f" {m.unit}"
    return f"{m.kind.replace('_', ' ').capitalize()} {m.value:g}{unit} on {m.date.isoformat()}"


def log_measurement(user_id: int, data: dict | None, by: str) -> dict:
    """Record one measurement.

    Returns {kind: "measurement", id, duplicate, measurement, replaced, recent [{date, value}] (this
    kind, newest first, up to 10), summary, links {day}[, note]} or {error, message, fix}.
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
        errors = []
        kind, err = rules.match_kind(data.get("kind"))
        if err:
            errors.append(err)
        value = None
        if kind is not None:
            value, err = rules.measurement_value(kind, data.get("value"))
            if err:
                errors.append(err)
        on, err = rules.entry_date(data.get("date"), today, "measurements")
        if err:
            errors.append(err)
        if errors:
            return rules.combine(errors)
        unit = rules.MEASUREMENTS[kind][0]
        key = measurement_key(on, kind, value)

        first = None if truthy(data.get("allow_repeat")) else find_duplicate(s, Measurement, user.id, on, key)
        replaced = note = None
        if first is not None:
            row, note = first, duplicate_note("measurement")
        else:
            live = s.scalar(select(Measurement).where(
                Measurement.user_id == user.id, Measurement.kind == kind, Measurement.date == on,
                Measurement.deleted_at.is_(None)))
            if live is not None:
                live.deleted_at = db.utcnow()
                s.flush()  # out of the one-live-per-day index before the new row exists
                replaced = row_dict(live)
                note = (f"Replaced the {on.isoformat()} {kind.replace('_', ' ')} of {live.value:g} (id {live.id}): "
                        "one a day. restore_entry with that id swaps them back.")
            row = Measurement(user_id=user.id, date=on, kind=kind, value=value, unit=unit, logged_by=by,
                              dedupe_key=key)
            s.add(row)
            s.flush()

        recent = [{"date": m.date.isoformat(), "value": m.value}
                  for m in reversed(measurements_for(s, user.id, today, kind)[-RECENT:])]
        shown_unit = "" if unit == kind else f" {unit}"
        summary = (f"Already logged: {describe(row)}." if first is not None else f"{describe(row)}."
                   + (f" Replaced {replaced['value']:g}{shown_unit}." if replaced else ""))
        out = {"kind": KIND, "id": row.id, "duplicate": first is not None, "measurement": row_dict(row),
               "replaced": replaced, "recent": recent, "summary": summary,
               "links": {"day": links.page("day", date=row.date)}}
        if note:
            out["note"] = note
        return out
