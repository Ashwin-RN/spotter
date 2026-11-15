"""Lift sessions: logging sets, and reading every session back as numbers.

log_lifts(user_id, data, by)
    data: date (YYYY-MM-DD; blank = the person's today; never after today, at most two years back),
    sets [{exercise, weight_kg or weight_lb, reps}] (1 to 60), notes (optional, up to 2,000 characters),
    new_session (optional). allow_repeat is read by nothing: an old client may still send it. A weight_lb is
    kept in kg (rules.check_session); the summary and every best's text are in the person's units
    (contract units.md), and a set sent in lb while the person has not chosen carries units_note.
    Which session (contract gym-logging.md): the person's live session with that date as stored, the
    highest id, under the lock. new_session true, or no such session: a new one, its sets numbered per
    exercise in the order given. Otherwise an add to it, whoever started it (web, import, assistant).
    An add is an edit of the session: the session as it stands is kept as a version (why edit), the new
    sets are appended and carry each exercise's set numbers on, the notes are joined on a new line, the
    key is worked out again over the whole session (an imported one keeps its key), and updated_at moves.
    It is refused, with nothing written, when the session would pass 60 sets or its notes 2,000
    characters. There is no duplicate guard: a set said again is added, and duplicate is always false.
    New bests are found against every earlier set of each lift: every session that comes before this
    one in the one order (see metrics.on_or_before). For a new session dated today that is every
    session, a later-dated one included; for a back-dated one, the ones on or before its date. An add
    reports the bests of the whole session that it did not hold before the add.

history(s, user_id, today)
    Every live session, whatever its date, worked through in the one order (effective date, then
    id; see metrics.on_or_before): one point per session per lift (that session's best estimated
    max), the best set, the most reps of a lift never done above 0 kg (reps_only), and the new bests
    with their dates. today orders; it never drops a row.
    The best set's estimated max comes back at 1 dp; every other value at the working precision.

rounded_best(best) and best_text(best, units=None)
    A new best for a result: an estimated max and the one it beat at 1 dp together, or both at 2 dp
    when they would read the same at 1 dp (D5). best_text says the pair the same way, in the person's
    units, from the unrounded best: a pair that reads the same in lb widens to 0.01 lb (decision 10).

Every write here locks the user row first (common.lock_user), then checks the write limit
(common.check_write_limit: 120 writes in 10 minutes), before any other read or work.
"""

from datetime import date

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app import db, formatting, links, metrics, rules, users
from app.models import IMPORTED, LiftSession, LiftSet
from app.services.common import (
    check_by, check_data, check_write_limit, dedupe_key, exercises, keep_version, lock_user, no_account, row_dict,
    sent, truthy, with_units_note,
)

KIND = "lift_session"
BEST_LABELS = {"estimated_max": "estimated max", "weight": "heaviest weight"}


def _quantity(p: dict) -> str:
    return "estimated_max" if p["kind"] == "estimated_max" else "weight_kg"


def _widened(p: dict, units=None) -> bool:
    """Whether a best and the one it beat read the same in the shown unit: then both widen. In kg only an
    estimated max can (at 1 dp, so both show and round at 2 dp); a heaviest weight is shown at its stored 2 dp.
    In lb either can: 102.06 and 102.05 kg are both 225 lb. metrics.prs needs the new one strictly above at
    2 dp, and 0.01 lb tells two such numbers apart (decision 10)."""
    quantity = _quantity(p)
    return (formatting.show(p["value"], quantity, units=units)
            == formatting.show(p["previous"], quantity, units=units))


def rounded_best(p: dict) -> dict:
    """A copy of a best with value and previous rounded together for a result (D5). A heaviest weight
    stays as entered."""
    if p["kind"] != "estimated_max":
        return dict(p)
    wide = _widened(p)  # in kg: the number fields never move with the units
    return {**p, "value": formatting.rounded(p["value"], "estimated_max", widened=wide),
            "previous": formatting.rounded(p["previous"], "estimated_max", widened=wide)}


def best_text(p: dict, units=None) -> str:
    """'Back Squat estimated max 81.7 kg, up from 70 kg'. The pair widens together (D5), judged in the shown
    unit. Give it the unrounded best: a figure rounded at a kg step can read 0.1 lb off in lb."""
    wide = _widened(p, units)
    value, previous = (formatting.show(p[k], _quantity(p), widened=wide, units=units) for k in ("value", "previous"))
    return f"{p['exercise']} {BEST_LABELS.get(p['kind'], p['kind'])} {value}, up from {previous}"


def first_text(lift: str, lift_sets: list[tuple[float, int]], units=None) -> str:
    """'First Back Squat session: estimated max 70 kg is the mark to beat.' Heaviest weight when no set
    is 1 to 10 reps; nothing to measure when no set counts at all."""
    estimate, heaviest = metrics.session_best(lift_sets), metrics.max_weight(lift_sets)
    if estimate is not None:
        return (f"First {lift} session: estimated max {formatting.show(estimate, 'estimated_max', units=units)} "
                "is the mark to beat.")
    if heaviest is not None:
        return f"First {lift} session: {formatting.show(heaviest, 'weight_kg', units=units)} is the mark to beat."
    return f"First {lift} session."


def _joined(items: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'; '' for none."""
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def sets_text(sets: list[tuple[str, float, int]], units=None) -> str:
    """'Back Squat 3 \u00d7 5 at 80 kg and Bench Press 60 kg \u00d7 5, 60 kg \u00d7 4' from (exercise,
    weight, reps), in order: one part a lift, joined as a list. Sets are grouped by their shown weight and reps
    (R8), so two sets that read 225 lb \u00d7 5 read "2 \u00d7 5 at 225 lb" whatever kg they hold. In kg shown
    equals stored, so no kg text moves."""
    groups: dict[str, list[tuple[str | None, int]]] = {}
    for name, w, r in sets:
        groups.setdefault(name, []).append((formatting.show(w, "weight_kg", units=units) if w else None, r))
    times = "\u00d7"
    parts = []
    for name, rows in groups.items():
        if len(set(rows)) == 1:
            w, r = rows[0]
            parts.append(f"{name} {formatting.show(len(rows), 'count')} {times} {formatting.show(r, 'reps')}"
                         + (f" at {w}" if w else ""))
        else:
            parts.append(f"{name} " + ", ".join(
                f"{w} {times} {formatting.show(r, 'reps')}" if w
                else f"{formatting.show(r, 'reps')} reps" for w, r in rows))
    return _joined(parts)


def session_dict(t: LiftSession) -> dict:
    """A session with its sets, in the order they were logged."""
    out = row_dict(t)
    out["sets"] = [{"exercise": x.exercise.name, "set_number": x.set_number, "weight_kg": x.weight_kg,
                    "reps": x.reps} for x in sorted(t.sets, key=lambda x: x.id)]
    return out


def number_sets(sets: list[dict], by_name: dict, after: dict | None = None) -> list[tuple[int, int, float, int]]:
    """(exercise_id, set_number, weight_kg, reps) for checked sets, numbered per exercise in the
    order given, from 1 or on from after {exercise_id: highest set number held}. Logging and editing
    both use it."""
    counts = dict(after or {})
    keyed = []
    for x in sets:
        ex_id = by_name[x["exercise"]].id
        counts[ex_id] = counts.get(ex_id, 0) + 1
        keyed.append((ex_id, counts[ex_id], x["weight_kg"], x["reps"]))
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
    last_top:  {lift: {date, weight_kg}}  that latest session's real date and its heaviest set of the lift. For
               get_routines' weight to log (contract routines.md): get_progress and the pages do not copy it.
    best_set:  {lift: {weight_kg, reps, date, estimated_max_kg}}
    reps_only: {lift: {reps, date}}  a lift with no set above 0 kg: the most reps in one set, and
               the real date of its session. A tie keeps the earlier set in the one order, as best_set
               does. A lift leaves it once any live set of it is above 0 kg. Web only: get_progress does
               not copy it. It exists because Epley is None at 0 kg, so such a lift has no best_set.
    bests:     {lift: [{date, exercise, kind, value, previous, session_id}]}
    """
    sessions = sorted(s.scalars(
        select(LiftSession).where(LiftSession.user_id == user_id, LiftSession.deleted_at.is_(None))
        .options(selectinload(LiftSession.sets).selectinload(LiftSet.exercise))),
        key=lambda t: metrics.order_key(t.date, t.id, today))
    points: dict[str, list[tuple[date, float]]] = {}
    logged: dict[str, int] = {}
    last: dict[str, date] = {}
    last_top: dict[str, dict] = {}
    best_set: dict[str, dict] = {}
    bests: dict[str, list[dict]] = {}
    prior: dict[str, list[tuple[float, int]]] = {}
    most_reps: dict[str, dict] = {}   # per lift, the most reps in one set, whatever the weight
    weighted: set[str] = set()        # lifts with a set above 0 kg
    for t in sessions:
        by_lift: dict[str, list[tuple[float, int]]] = {}
        for x in sorted(t.sets, key=lambda x: x.id):
            by_lift.setdefault(x.exercise.name, []).append((x.weight_kg, x.reps))
            e = metrics.epley(x.weight_kg, x.reps)
            top = best_set.get(x.exercise.name)
            if e is not None and (top is None or e > top["estimated_max_kg"]):
                best_set[x.exercise.name] = {"weight_kg": x.weight_kg, "reps": x.reps,
                                             "date": t.date.isoformat(), "estimated_max_kg": e}
            if x.weight_kg is not None and x.weight_kg > 0:
                weighted.add(x.exercise.name)
            most = most_reps.get(x.exercise.name)
            if x.reps is not None and (most is None or x.reps > most["reps"]):
                most_reps[x.exercise.name] = {"reps": x.reps, "date": t.date.isoformat()}
        for lift, lift_sets in by_lift.items():
            logged[lift] = logged.get(lift, 0) + 1
            last[lift] = t.date
            last_top[lift] = {"date": t.date, "weight_kg": max(w for w, _ in lift_sets)}
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
    reps_only = {lift: most for lift, most in most_reps.items() if lift not in weighted}
    return {"sessions": sessions, "points": points, "logged": logged, "last": last, "last_top": last_top,
            "best_set": best_set, "reps_only": reps_only, "bests": bests}


def _prior(s: Session, user_id: int, exercise_ids: set, name_of: dict, on: date, today: date,
           before: int | None = None) -> dict[str, list[tuple[float, int]]]:
    """{lift: [(weight, reps)]}: every set of these lifts in the person's live sessions that come before the
    session in the one order (effective date, then id). A new session (before None): every session whose effective
    date is on or before its date, so for one dated today every session, a later-dated one included. An add to
    session `before`: the sessions dated before it, and those of its effective date with a lower id. One query."""
    query = select(LiftSet.exercise_id, LiftSet.weight_kg, LiftSet.reps).join(LiftSession).where(
        LiftSession.user_id == user_id, LiftSession.deleted_at.is_(None), LiftSet.exercise_id.in_(exercise_ids))
    if before is not None:
        same_day = LiftSession.date == on if on < today else LiftSession.date >= on  # after today counts as today
        query = query.where(or_(LiftSession.date < on, and_(same_day, LiftSession.id < before)))
    elif on < today:
        query = query.where(LiftSession.date <= on)
    prior: dict[str, list[tuple[float, int]]] = {}
    for exercise_id, weight, reps in s.execute(query):
        prior.setdefault(name_of[exercise_id], []).append((weight, reps))
    return prior


def _best(lift: str, hit: dict, units=None) -> dict:
    """A new best for a result: its numbers rounded in kg, its text in the person's units from the unrounded one."""
    raw = {"exercise": lift, **hit}
    return {**rounded_best(raw), "text": best_text(raw, units)}


def _sent_lb(sets) -> bool:
    """Whether any set the caller sent gave its weight in lb: for units_note."""
    return isinstance(sets, list) and any(isinstance(x, dict) and sent(x.get("weight_lb")) for x in sets)


def _tail(new_bests: list[dict], firsts: list[str]) -> str:
    """What follows the sets in a summary. A lift's first session has nothing to beat, so it sets the mark rather
    than a best; the summary says so, or "No new bests" reads as a missed one."""
    tail = "".join(f" New best: {p['text']}." for p in new_bests)
    if firsts:
        tail += " " + " ".join(firsts)
    return tail or " No new bests."


def _count(n: int) -> str:
    return formatting.show(n, "count")


def _add(s: Session, session: LiftSession, sets: list[dict], notes: str, by_name: dict, by: str, today: date,
         units=None) -> dict:
    """The call's sets added to the date's session, as an edit of it (see the module notes). Refused past
    rules.SESSION_SETS[1] sets or rules.TEXT_MAX characters of notes, before anything is kept or written."""
    on, had = session.date, sorted(session.sets, key=lambda x: x.id)
    joined = "\n".join(t for t in (session.notes, notes) if t)
    most, errors = rules.SESSION_SETS[1], []
    if len(had) + len(sets) > most:
        errors.append(rules.error(
            "sets_count_out_of_range",
            f"The session on {formatting.day(on, today)} has {_count(len(had))} set{'' if len(had) == 1 else 's'}; "
            f"{_count(len(sets))} more would "
            f"make {_count(len(had) + len(sets))}, and a session holds at most {_count(most)}.",
            "Send new_session true to start another session that day, or send fewer sets."))
    if len(joined) > rules.TEXT_MAX:
        errors.append(rules.error(
            "invalid_field", f"The session's notes would be {_count(len(joined))} characters; the most is "
                             f"{_count(rules.TEXT_MAX)}.",
            "Send shorter notes, or change the session's notes with edit_entry."))
    if errors:
        return rules.combine(errors)

    name_of = {e.id: name for name, e in by_name.items()}
    held = [(x.exercise_id, x.set_number, x.weight_kg, x.reps) for x in had]
    top: dict[int, int] = {}
    for ex_id, n, _, _ in held:
        top[ex_id] = max(top.get(ex_id, 0), n)
    keyed = number_sets(sets, by_name, top)
    prior = _prior(s, session.user_id, {k[0] for k in keyed}, name_of, on, today, before=session.id)
    new_bests, firsts = [], []
    for lift in dict.fromkeys(x["exercise"] for x in sets):
        was = [(w, r) for ex_id, _, w, r in held if name_of.get(ex_id) == lift]
        now = was + [(x["weight_kg"], x["reps"]) for x in sets if x["exercise"] == lift]
        if not prior.get(lift):
            firsts.append(first_text(lift, now, units))
            continue
        before = {b["kind"]: b["value"] for b in metrics.prs(prior[lift], was)}
        new_bests += [_best(lift, hit, units) for hit in metrics.prs(prior[lift], now)
                      if hit["kind"] not in before or hit["value"] > before[hit["kind"]]]

    keep_version(s, session, by)  # every check has passed: the session as it stands is kept
    session.sets.extend(LiftSet(exercise_id=e, set_number=n, weight_kg=w, reps=r) for e, n, w, r in keyed)
    if notes:
        session.notes = joined
    if session.logged_by != IMPORTED:  # an imported session keeps the key it was imported under, as an edit does
        session.dedupe_key = session_key(on, session.notes, held + keyed)
    session.updated_at = db.utcnow()
    s.flush()
    summary = (f"Added {sets_text([(x['exercise'], x['weight_kg'], x['reps']) for x in sets], units)} to the "
               "session on "
               f"{formatting.day(on, today)}. It now has {_count(len(held) + len(keyed))} sets.")
    lifts = list(dict.fromkeys(name_of.get(ex_id) for ex_id, *_ in held + keyed))
    return {"kind": KIND, "id": session.id, "duplicate": False, "session": session_dict(session),
            "new_bests": new_bests, "summary": summary + _tail(new_bests, firsts), "links": _links(on, lifts)}


def log_lifts(user_id: int, data: dict | None, by: str) -> dict:
    """Log sets of lifting: into the date's session, or a new one. Returns {kind, id, duplicate (always false),
    session, new_bests, summary, links[, units_note]} or {error, message, fix}. See the module notes for the rules."""
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

        # Which session: the person's live one with this date as stored, the latest by id (module notes).
        target = None if truthy(data.get("new_session")) else s.scalar(
            select(LiftSession).where(LiftSession.user_id == user.id, LiftSession.date == on,
                                      LiftSession.deleted_at.is_(None)).order_by(LiftSession.id.desc()).limit(1))
        units = users.units_for(user)
        if target is not None:
            return with_units_note(_add(s, target, sets, notes, by_name, by, today, units), user,
                                   _sent_lb(data.get("sets")), False)

        keyed = number_sets(sets, by_name)
        key = session_key(on, notes, keyed)
        lifts = list(dict.fromkeys(x["exercise"] for x in sets))
        prior = _prior(s, user.id, {k[0] for k in keyed}, {e.id: name for name, e in by_name.items()}, on, today)
        grouped: dict[str, list[tuple[float, int]]] = {}
        for x in sets:
            grouped.setdefault(x["exercise"], []).append((x["weight_kg"], x["reps"]))
        new_bests = [_best(lift, hit, units) for lift, lift_sets in grouped.items()
                     for hit in metrics.prs(prior.get(lift, []), lift_sets)]

        session = LiftSession(user_id=user.id, date=on, notes=notes, logged_by=by, dedupe_key=key)
        session.sets = [LiftSet(exercise_id=ex_id, set_number=n, weight_kg=w, reps=r) for ex_id, n, w, r in keyed]
        s.add(session)
        s.flush()

        summary = (f"{sets_text([(x['exercise'], x['weight_kg'], x['reps']) for x in sets], units)} on "
                   f"{formatting.day(on, today)}.")
        firsts = [first_text(lift, lift_sets, units) for lift, lift_sets in grouped.items() if not prior.get(lift)]
        out = {"kind": KIND, "id": session.id, "duplicate": False, "session": session_dict(session),
               "new_bests": new_bests, "summary": summary + _tail(new_bests, firsts), "links": _links(on, lifts)}
        return with_units_note(out, user, _sent_lb(data.get("sets")), False)
