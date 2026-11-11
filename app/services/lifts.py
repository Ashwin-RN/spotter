"""Lift sessions: logging one, and reading every session back as numbers.

log_lifts(user_id, data, by)
    data: date (YYYY-MM-DD; blank = the person's today; never after today, at most two years back),
    sets [{exercise, weight_kg, reps}] (1 to 60), notes (optional, up to 2,000 characters),
    allow_repeat (optional).
    Sets are numbered per exercise in the order given. New bests are found against every earlier
    set of each lift: every session that comes before this one in the one order (see
    metrics.on_or_before). For a session dated today that is every session, a later-dated one
    included; for a back-dated session, the ones on or before its date.
    The duplicate guard: the same date, notes and sets as a live session logged in the last
    10 minutes returns that session with duplicate true, and nothing is written.

history(s, user_id, today)
    Every live session, whatever its date, worked through in the one order (effective date, then
    id; see metrics.on_or_before): one point per session per lift (that session's best estimated
    max), the best set, and the new bests with their dates. today orders; it never drops a row.
    The best set's estimated max comes back at 1 dp; every other value at the working precision.

rounded_best(best) and best_text(best)
    A new best for a result: an estimated max and the one it beat at 1 dp together, or both at 2 dp
    when they would read the same at 1 dp (D5). best_text says the pair the same way.

Every write here locks the user row first (common.lock_user), then checks the write limit
(common.check_write_limit: 120 writes in 10 minutes), before any other read or work.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import db, formatting, links, metrics, rules, users
from app.models import LiftSession, LiftSet
from app.services.common import (
    check_by, check_data, check_write_limit, dedupe_key, duplicate_note, exercises, find_duplicate, lock_user,
    no_account, row_dict, truthy,
)

KIND = "lift_session"
BEST_LABELS = {"estimated_max": "estimated max", "weight": "heaviest weight"}


def _widened(p: dict) -> bool:
    """Whether a best of kind estimated_max and the one it beat read the same at 1 dp: then both show
    and round at 2 dp. metrics.prs needs the new one strictly above at 2 dp, so 2 dp tells them apart."""
    return p["kind"] == "estimated_max" and (formatting.show(p["value"], "estimated_max")
                                             == formatting.show(p["previous"], "estimated_max"))


def rounded_best(p: dict) -> dict:
    """A copy of a best with value and previous rounded together for a result (D5). A heaviest weight
    stays as entered."""
    if p["kind"] != "estimated_max":
        return dict(p)
    wide = _widened(p)
    return {**p, "value": formatting.rounded(p["value"], "estimated_max", widened=wide),
            "previous": formatting.rounded(p["previous"], "estimated_max", widened=wide)}


def best_text(p: dict) -> str:
    """'Back Squat estimated max 81.7 kg, up from 70 kg'. The pair widens together (D5)."""
    if p["kind"] == "estimated_max":
        wide = _widened(p)
        value, previous = (formatting.show(p[k], "estimated_max", widened=wide) for k in ("value", "previous"))
    else:
        value, previous = (formatting.show(p[k], "weight_kg") for k in ("value", "previous"))
    return f"{p['exercise']} {BEST_LABELS.get(p['kind'], p['kind'])} {value}, up from {previous}"


def first_text(lift: str, lift_sets: list[tuple[float, int]]) -> str:
    """'First Back Squat session: estimated max 70 kg is the mark to beat.' Heaviest weight when no set
    is 1 to 10 reps; nothing to measure when no set counts at all."""
    estimate, heaviest = metrics.session_best(lift_sets), metrics.max_weight(lift_sets)
    if estimate is not None:
        return f"First {lift} session: estimated max {formatting.show(estimate, 'estimated_max')} is the mark to beat."
    if heaviest is not None:
        return f"First {lift} session: {formatting.show(heaviest, 'weight_kg')} is the mark to beat."
    return f"First {lift} session."


def _joined(items: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'; '' for none."""
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def sets_text(sets: list[tuple[str, float, int]]) -> str:
    """'Back Squat 3 \u00d7 5 at 80 kg and Bench Press 60 kg \u00d7 5, 60 kg \u00d7 4' from (exercise,
    weight, reps), in order: one part a lift, joined as a list."""
    groups: dict[str, list[tuple[float, int]]] = {}
    for name, w, r in sets:
        groups.setdefault(name, []).append((w, r))
    times = "\u00d7"
    parts = []
    for name, rows in groups.items():
        if len(set(rows)) == 1:
            w, r = rows[0]
            parts.append(f"{name} {formatting.show(len(rows), 'count')} {times} {formatting.show(r, 'reps')}"
                         + (f" at {formatting.show(w, 'weight_kg')}" if w else ""))
        else:
            parts.append(f"{name} " + ", ".join(
                f"{formatting.show(w, 'weight_kg')} {times} {formatting.show(r, 'reps')}" if w
                else f"{formatting.show(r, 'reps')} reps" for w, r in rows))
    return _joined(parts)


def session_dict(t: LiftSession) -> dict:
    """A session with its sets, in the order they were logged."""
    out = row_dict(t)
    out["sets"] = [{"exercise": x.exercise.name, "set_number": x.set_number, "weight_kg": x.weight_kg,
                    "reps": x.reps} for x in sorted(t.sets, key=lambda x: x.id)]
    return out


def number_sets(sets: list[dict], by_name: dict) -> list[tuple[int, int, float, int]]:
    """(exercise_id, set_number, weight_kg, reps) for checked sets, numbered per exercise in the
    order given. Logging and editing both use it."""
    counts: dict[str, int] = {}
    keyed = []
    for x in sets:
        counts[x["exercise"]] = counts.get(x["exercise"], 0) + 1
        keyed.append((by_name[x["exercise"]].id, counts[x["exercise"]], x["weight_kg"], x["reps"]))
    return keyed


def session_key(on: date, notes: str, keyed: list[tuple]) -> str:
    """What makes two sessions the same: the date, the notes and the set list (order across lifts
    does not matter; the numbering within a lift does)."""
    return dedupe_key(on, notes, sorted(keyed))


def _links(on: date, lifts: list[str]) -> dict:
    return {"day": links.page("day", date=on),
            "progress": {lift: links.page("progress_lift", lift=lift) for lift in lifts}}


def history(s: Session, user_id: int, today: date) -> dict:
    """Per lift, from the person's live sessions, in the one order: (effective date, id). Every
    value from metrics. No session is left out for its date: one after today (after a change of
    time zone) counts as today's, and sits after the sessions logged before it today.

    sessions:  the sessions used, oldest first
    points:    {lift: [(date, session best)]}   one point per session with a set of 1 to 10 reps
    logged:    {lift: sessions that include the lift, at any reps}
    last:      {lift: the real date of the latest session (in the one order) that trained it}
    best_set:  {lift: {weight_kg, reps, date, estimated_max_kg}}
    bests:     {lift: [{date, exercise, kind, value, previous, session_id}]}
    """
    sessions = sorted(s.scalars(
        select(LiftSession).where(LiftSession.user_id == user_id, LiftSession.deleted_at.is_(None))
        .options(selectinload(LiftSession.sets).selectinload(LiftSet.exercise))),
        key=lambda t: metrics.order_key(t.date, t.id, today))
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
    # The loop compares the unrounded estimate; the output carries it at 1 dp, in a new dict.
    best_set = {lift: {**b, "estimated_max_kg": formatting.rounded(b["estimated_max_kg"], "estimated_max")}
                for lift, b in best_set.items()}
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
        limited = check_write_limit(s, user.id)
        if limited:
            return limited
        today = users.today_for(user)
        by_name = {e.name: e for e in exercises(s, "lift")}
        on, sets, err = rules.check_session(data.get("date"), data.get("sets"), list(by_name), today)
        notes, notes_err = rules.text(data.get("notes"), "notes")
        if err or notes_err:
            return rules.combine((err["errors"] if err else []) + ([notes_err] if notes_err else []))

        keyed = number_sets(sets, by_name)
        key = session_key(on, notes, keyed)
        lifts = list(dict.fromkeys(x["exercise"] for x in sets))

        if not truthy(data.get("allow_repeat")):
            first = find_duplicate(s, LiftSession, user.id, on, key)
            if first is not None:
                done = sets_text([(x.exercise.name, x.weight_kg, x.reps) for x in sorted(first.sets, key=lambda x: x.id)])
                return {"kind": KIND, "id": first.id, "duplicate": True, "session": session_dict(first),
                        "new_bests": [],
                        "summary": f"Already logged: {done} on {formatting.day(first.date, today)}.",
                        "note": duplicate_note("session"), "links": _links(first.date, lifts)}

        # New bests against every set that comes before this session in the one order: every
        # session whose effective date is on or before this one's (all logged before it). For a
        # session dated today that is every session; a later-dated one counts as today's.
        name_of = {e.id: name for name, e in by_name.items()}
        prior: dict[str, list[tuple[float, int]]] = {}
        earlier = select(LiftSet.exercise_id, LiftSet.weight_kg, LiftSet.reps).join(LiftSession).where(
            LiftSession.user_id == user.id, LiftSession.deleted_at.is_(None),
            LiftSet.exercise_id.in_({k[0] for k in keyed}))
        if on < today:
            earlier = earlier.where(LiftSession.date <= on)
        for exercise_id, weight, reps in s.execute(earlier):
            prior.setdefault(name_of[exercise_id], []).append((weight, reps))
        grouped: dict[str, list[tuple[float, int]]] = {}
        for x in sets:
            grouped.setdefault(x["exercise"], []).append((x["weight_kg"], x["reps"]))
        new_bests = []
        for lift, lift_sets in grouped.items():
            for hit in metrics.prs(prior.get(lift, []), lift_sets):
                p = rounded_best({"exercise": lift, **hit})
                new_bests.append({**p, "text": best_text(p)})

        session = LiftSession(user_id=user.id, date=on, notes=notes, logged_by=by, dedupe_key=key)
        session.sets = [LiftSet(exercise_id=ex_id, set_number=n, weight_kg=w, reps=r) for ex_id, n, w, r in keyed]
        s.add(session)
        s.flush()

        summary = (f"{sets_text([(x['exercise'], x['weight_kg'], x['reps']) for x in sets])} on "
                   f"{formatting.day(on, today)}.")
        # A lift's first session has nothing to beat, so it sets the mark rather than a best; the
        # summary says so, or "No new bests" reads as a missed one.
        firsts = [first_text(lift, lift_sets) for lift, lift_sets in grouped.items() if not prior.get(lift)]
        if new_bests:
            summary += "".join(f" New best: {p['text']}." for p in new_bests)
        if firsts:
            summary += " " + " ".join(firsts)
        if not new_bests and not firsts:
            summary += " No new bests."
        return {"kind": KIND, "id": session.id, "duplicate": False, "session": session_dict(session),
                "new_bests": new_bests, "summary": summary, "links": _links(on, lifts)}
