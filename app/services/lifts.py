"""Lift sessions: logging one, and reading every session back as numbers.

log_lifts(user_id, data, by)
    data: date (YYYY-MM-DD; blank = the person's today; never after today, at most two years back),
    sets [{exercise, weight_kg, reps}] (1 to 60), notes (optional, up to 2,000 characters),
    allow_repeat (optional).
    Sets are numbered per exercise in the order given. New bests are found against every earlier
    set of each lift, on or before the session's date.
    The duplicate guard: the same date, notes and sets as a live session logged in the last
    10 minutes returns that session with duplicate true, and nothing is written.

history(s, user_id)
    Every live session, whatever its date, worked through in date order: one point per session per lift
    (that session's best estimated max), the best set, and the new bests with their dates.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import db, links, metrics, rules, users
from app.models import LiftSession, LiftSet
from app.services.common import (
    check_by, check_data, dedupe_key, duplicate_note, exercises, find_duplicate, lock_user, no_account,
    row_dict, truthy,
)

KIND = "lift_session"
BEST_LABELS = {"estimated_max": "estimated max", "weight": "heaviest weight"}


def best_text(p: dict) -> str:
    return f"{p['exercise']} {BEST_LABELS.get(p['kind'], p['kind'])} {p['value']:g} kg (was {p['previous']:g} kg)"


def sets_text(sets: list[tuple[str, float, int]]) -> str:
    """'Back Squat 3 × 5 @ 80 kg; Bench Press 60 kg × 5, 60 kg × 4' from (exercise, weight, reps), in order."""
    groups: dict[str, list[tuple[float, int]]] = {}
    for name, w, r in sets:
        groups.setdefault(name, []).append((w, r))
    parts = []
    for name, rows in groups.items():
        if len(set(rows)) == 1:
            w, r = rows[0]
            parts.append(f"{name} {len(rows)} × {r}" + (f" @ {w:g} kg" if w else ""))
        else:
            parts.append(f"{name} " + ", ".join(f"{w:g} kg × {r}" if w else f"{r} reps" for w, r in rows))
    return "; ".join(parts)


def session_dict(t: LiftSession) -> dict:
    """A session with its sets, in the order they were logged."""
    out = row_dict(t)
    out["sets"] = [{"exercise": x.exercise.name, "set_number": x.set_number, "weight_kg": x.weight_kg,
                    "reps": x.reps} for x in sorted(t.sets, key=lambda x: x.id)]
    return out


def _links(on: date, lifts: list[str]) -> dict:
    return {"day": links.page("day", date=on),
            "progress": {lift: links.page("progress_lift", lift=lift) for lift in lifts}}


def history(s: Session, user_id: int) -> dict:
    """Per lift, from the person's live sessions, in (date, id) order. Every value from metrics.
    No session is left out for its date: one after today (after a change of time zone) is the latest.

    sessions:  the sessions used, oldest first
    points:    {lift: [(date, session best)]}   one point per session with a set of 1 to 10 reps
    logged:    {lift: sessions that include the lift, at any reps}
    last:      {lift: the latest date it was trained}
    best_set:  {lift: {weight_kg, reps, date, estimated_max_kg}}
    bests:     {lift: [{date, exercise, kind, value, previous, session_id}]}
    """
    sessions = list(s.scalars(
        select(LiftSession).where(LiftSession.user_id == user_id, LiftSession.deleted_at.is_(None))
        .order_by(LiftSession.date, LiftSession.id)
        .options(selectinload(LiftSession.sets).selectinload(LiftSet.exercise))))
    points: dict[str, list[tuple[date, float]]] = {}
    logged: dict[str, int] = {}
    last: dict[str, date] = {}
    best_set: dict[str, dict] = {}
    bests: dict[str, list[dict]] = {}
    prior: dict[str, list[tuple[float, int]]] = {}
    for t in sessions:
        by_lift: dict[str, list[tuple[float, int]]] = {}
        for x in sorted(t.sets, key=lambda x: x.id):
            by_lift.setdefault(x.exercise.name, []).append((x.weight_kg, x.reps))
            e = metrics.epley(x.weight_kg, x.reps)
            top = best_set.get(x.exercise.name)
            if e is not None and (top is None or e > top["estimated_max_kg"]):
                best_set[x.exercise.name] = {"weight_kg": x.weight_kg, "reps": x.reps,
                                             "date": t.date.isoformat(), "estimated_max_kg": e}
        for lift, lift_sets in by_lift.items():
            logged[lift] = logged.get(lift, 0) + 1
            last[lift] = t.date
            best = metrics.session_best(lift_sets)
            if best is not None:
                points.setdefault(lift, []).append((t.date, best))
            for hit in metrics.prs(prior.get(lift, []), lift_sets):
                bests.setdefault(lift, []).append({"date": t.date.isoformat(), "exercise": lift, **hit,
                                                   "session_id": t.id})
            prior.setdefault(lift, []).extend(lift_sets)
    return {"sessions": sessions, "points": points, "logged": logged, "last": last, "best_set": best_set,
            "bests": bests}


def log_lifts(user_id: int, data: dict | None, by: str) -> dict:
    """Log one lift session. Returns {kind, id, duplicate, session, new_bests, summary, links[, note]}
    or {error, message, fix}. See the module notes for the rules."""
    data, err = check_data(data)
    err = err or check_by(by)
    if err:
        return err
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        by_name = {e.name: e for e in exercises(s, "lift")}
        on, sets, err = rules.check_session(data.get("date"), data.get("sets"), list(by_name), today)
        notes, notes_err = rules.text(data.get("notes"), "notes")
        if err or notes_err:
            return rules.combine((err["errors"] if err else []) + ([notes_err] if notes_err else []))

        counts: dict[str, int] = {}
        keyed = []  # (exercise_id, set_number, weight_kg, reps), in the order given
        for x in sets:
            counts[x["exercise"]] = counts.get(x["exercise"], 0) + 1
            keyed.append((by_name[x["exercise"]].id, counts[x["exercise"]], x["weight_kg"], x["reps"]))
        key = dedupe_key(on, notes, sorted(keyed))
        lifts = list(counts)

        if not truthy(data.get("allow_repeat")):
            first = find_duplicate(s, LiftSession, user.id, on, key)
            if first is not None:
                return {"kind": KIND, "id": first.id, "duplicate": True, "session": session_dict(first),
                        "new_bests": [], "summary": f"Already logged: lift session {first.id}.",
                        "note": duplicate_note("session"), "links": _links(first.date, lifts)}

        # New bests against every earlier set of these lifts, on or before this date.
        name_of = {e.id: name for name, e in by_name.items()}
        prior: dict[str, list[tuple[float, int]]] = {}
        for exercise_id, weight, reps in s.execute(
                select(LiftSet.exercise_id, LiftSet.weight_kg, LiftSet.reps).join(LiftSession)
                .where(LiftSession.user_id == user.id, LiftSession.deleted_at.is_(None), LiftSession.date <= on,
                       LiftSet.exercise_id.in_({k[0] for k in keyed}))):
            prior.setdefault(name_of[exercise_id], []).append((weight, reps))
        grouped: dict[str, list[tuple[float, int]]] = {}
        for x in sets:
            grouped.setdefault(x["exercise"], []).append((x["weight_kg"], x["reps"]))
        new_bests = []
        for lift, lift_sets in grouped.items():
            for hit in metrics.prs(prior.get(lift, []), lift_sets):
                p = {"exercise": lift, **hit}
                new_bests.append({**p, "text": best_text(p)})

        session = LiftSession(user_id=user.id, date=on, notes=notes, logged_by=by, dedupe_key=key)
        session.sets = [LiftSet(exercise_id=ex_id, set_number=n, weight_kg=w, reps=r) for ex_id, n, w, r in keyed]
        s.add(session)
        s.flush()

        summary = f"{on.isoformat()}: {sets_text([(x['exercise'], x['weight_kg'], x['reps']) for x in sets])}."
        summary += (" New bests: " + "; ".join(p["text"] for p in new_bests) + ".") if new_bests else " No new bests."
        return {"kind": KIND, "id": session.id, "duplicate": False, "session": session_dict(session),
                "new_bests": new_bests, "summary": summary, "links": _links(on, lifts)}
