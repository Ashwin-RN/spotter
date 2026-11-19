"""Lift sessions: logging sets, and reading every session back as numbers.

log_lifts(user_id, data, by)
    data: date (YYYY-MM-DD; blank = the person's today; never after today, at most two years back),
    sets [{exercise, weight_kg or weight_lb, reps or time_s or distance_m and time_s, exact?}] (1 to 60; what a set
    takes is its lift's, rules.check_session), notes (optional, up to 2,000 characters),
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
    characters.
    A set's exercise is the person's own word or a listed name (contract exercise-list.md, R3 to R5, note (i)): a
    plain word, a default's own name too, takes the lift the person's live sets point to among the candidates that
    fit the set (rules.check_session with logging, the history from last_logged): none of those logged, the first
    that fits; one, that one; two or more, the call is refused as pick_lift and nothing is written. A set with exact
    true is taken as written.
    There is no duplicate guard: a set said again is added, and duplicate is always false.
    A set with no reps (a hold, a carry) has no estimated max and no heaviest weight: metrics.epley and
    metrics.max_weight count only sets of reps, so history and _prior read it and Epley skips it. A lift of another
    kind (best_kind: help, band, hold, carry) takes its own bests instead (contract exercise-list.md, "Bests";
    metrics.other_bests): least help or most reps at it, most reps, the longest hold at a weight, the heaviest load
    over a distance. Its first session names its record's figure as the mark to beat.
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

records(sessions)
    From history's sessions, no read of its own: each lift of another kind than standard with its best set by its
    kind, for get_progress' summary and the pages. record_figure and record_words say one in words.

rounded_best(best) and best_text(best, units=None)
    A new best for a result: an estimated max and the one it beat at 1 dp together, or both at 2 dp
    when they would read the same at 1 dp (D5). best_text says the pair the same way, in the person's
    units, from the unrounded best: a pair that reads the same in lb widens to 0.01 lb (decision 10).

Every write here locks the user row first (common.lock_user), then checks the write limit
(common.check_write_limit: 120 writes in 10 minutes), before any other read or work.
"""

from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import db, formatting, links, metrics, rules, users
from app.exercises import BY_NAME as LISTED
from app.models import IMPORTED, Exercise, LiftSession, LiftSet
from app.services.common import (
    check_by, check_data, check_write_limit, dedupe_key, exercises, keep_version, lock_user, no_account, row_dict,
    sent, truthy, with_units_note,
)

KIND = "lift_session"
BEST_LABELS = {"estimated_max": "estimated max", "weight": "heaviest weight", "least_help": "least help",
               "reps": "most reps", "time": "longest hold", "carry": "heaviest load"}
# A best's quantity, by its kind: the value and the one it beat are shown at it.
BEST_QUANTITIES = {"estimated_max": "estimated_max", "reps": "reps", "time": "seconds"}
# Which bests a lift takes, by what a set of it records (contract exercise-list.md, "Bests"); help comes first.
KIND_OF_MEASURES = {("reps",): "band", ("weight", "time"): "hold", ("weight", "distance", "time"): "carry"}
# The kind a record lift's best is first named by: its record's figure (record_figure).
RECORD_BEST = {"help": "least_help", "band": "reps", "hold": "time", "carry": "carry"}
# Why a lift of another kind than standard has no estimated max, on the Progress page's Lifts card.
NO_ESTIMATE = {"help": "Not worked out with help", "band": "Not worked out for a band",
               "hold": "Not worked out for a hold", "carry": "Not worked out for distance"}


def best_kind(name) -> str:
    """Which bests a lift takes (metrics.prs' kind): help on Assisted Pull-Up and Assisted Dip, band on a lift of
    reps alone, hold on a lift of weight and time, carry on a carry or a sled (weight, distance and time), else
    standard (the estimated max and the heaviest weight). A name not on the list, a retired one, is standard."""
    if rules.takes_help(name):
        return "help"
    return KIND_OF_MEASURES.get(rules.measures_of(name), "standard")


def _quantity(p: dict) -> str:
    return BEST_QUANTITIES.get(p["kind"], "weight_kg")


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
    """'Back Squat estimated max 81.7 kg, up from 70 kg': the lift, then best_phrase."""
    return f"{p['exercise']} {best_phrase(p, units)}"


# The keys a best carries for its words only, never in a result: a hold's weight, a carry's distance, help.
BEST_PLACES = ("weight_kg", "previous_weight_kg", "distance_m", "previous_distance_m")


def best_phrase(p: dict, units=None, page: bool = False) -> str:
    """A best in words, after its lift: 'estimated max 81.7 kg, up from 70 kg'. The pair widens together (D5),
    judged in the shown unit. Give it the unrounded best: a figure rounded at a kg step can read 0.1 lb off in lb.
    The other kinds (exercise-list.md, "Bests") name where the best stands, so it never reads against the record
    beside it: 'longest hold 45 s at 10 kg, up from 30 s', 'longest hold 90 s at 10 kg, up from 60 s at 0 kg' when
    the earlier set was at another weight, 'heaviest load 36 kg over 40 m, up from 32 kg', 'most reps 20, up from
    15', 'most reps 8 with 20 kg help, up from 6', and help, a minus weight, as help that went down: 'least help
    20 kg, down from 30 kg', 'no help, down from 10 kg'. A distance is in metres on every account. page: a no-break
    space before each unit."""
    kind, wide = p["kind"], _widened(p, units)

    def show(value, quantity=None, widened=wide) -> str:
        return formatting.show(value, quantity or _quantity(p), page=page, widened=widened, units=units)

    if kind == "least_help":
        if p["value"] == 0:
            return f"no help, down from {show(abs(p['previous']))}"
        return f"least help {show(abs(p['value']))}, down from {show(abs(p['previous']))}"
    value, previous = show(p["value"]), show(p["previous"])
    if kind == "time":
        here, there = p.get("weight_kg"), p.get("previous_weight_kg")
        value += f" at {show(here, 'weight_kg', False)}" if here else ""
        previous += f" at {show(there, 'weight_kg', False)}" if there is not None and there != here else ""
    elif kind == "carry":
        here, there = p.get("distance_m"), p.get("previous_distance_m")
        value += f" over {show(here, 'metres', False)}" if here is not None else ""
        previous += f" over {show(there, 'metres', False)}" if there is not None and there != here else ""
    elif kind == "reps" and p.get("weight_kg") is not None:  # on an assisted lift: the help it was done with
        helped = p["weight_kg"]
        value += " with no help" if helped == 0 else f" with {show(abs(helped), 'weight_kg', False)} help"
    return f"{BEST_LABELS.get(kind, kind)} {value}, up from {previous}"


def first_text(lift: str, lift_sets: list[tuple], units=None) -> str:
    """'First Back Squat session: estimated max 70 kg is the mark to beat.' Heaviest weight when no set
    is 1 to 10 reps; nothing to measure when no set counts at all. A lift of another kind than standard (best_kind)
    names its record's figure: 'First Plank session: longest hold 60 s is the mark to beat.' Sets are (weight,
    reps) or (weight, reps, time_s, distance_m)."""
    kind = best_kind(lift)
    if kind != "standard":
        top = record_of(kind, [(x[0], x[1], *(list(x[2:]) + [None, None])[:2]) for x in lift_sets])
        if top is not None:
            return f"First {lift} session: {record_figure(kind, top, units)} is the mark to beat."
        return f"First {lift} session."
    estimate, heaviest = metrics.session_best(lift_sets), metrics.max_weight(lift_sets)
    if estimate is not None:
        return (f"First {lift} session: estimated max {formatting.show(estimate, 'estimated_max', units=units)} "
                "is the mark to beat.")
    if heaviest is not None:
        return f"First {lift} session: {formatting.show(heaviest, 'weight_kg', units=units)} is the mark to beat."
    return f"First {lift} session."


def _record_key(kind: str, x: tuple) -> tuple | None:
    """A set's place in its kind's order (exercise-list.md, "Bests"), higher is better, or None when it does not
    count: help the weight then the reps, band the reps, hold the time then the weight, carry the weight then the
    distance."""
    w, r, t, d = x
    picked = {"help": (w, r), "band": (r,), "hold": (t, w), "carry": (w, d)}.get(kind)
    return None if picked is None or None in picked else picked


def record_of(kind: str, sets: list[tuple]) -> tuple | None:
    """The best of these (weight, reps, time_s, distance_m) sets by the kind's order, the first on a tie. None with
    none that counts, or for the standard kind."""
    best = None
    for x in sets:
        key = _record_key(kind, x)
        if key is not None and (best is None or key > best[0]):
            best = (key, x)
    return None if best is None else best[1]


def record_figure(kind: str, x: tuple, units=None, page: bool = False) -> str:
    """A record's figure in words, where it stands named as a best names it: 'least help 20 kg' ('no help' at
    0 kg), 'most reps 15', 'longest hold 90 s' ('longest hold 60 s at 10 kg' with weight), 'heaviest load 32 kg
    over 40 m'."""
    w, r, t, d = x

    def show(value, quantity):
        return formatting.show(value, quantity, page=page, units=units)

    if kind == "help":
        return "no help" if w == 0 else f"least help {show(abs(w), 'weight_kg')}"
    if kind == "band":
        return f"most reps {show(r, 'reps')}"
    if kind == "hold":
        return f"longest hold {show(t, 'seconds')}" + (f" at {show(w, 'weight_kg')}" if w else "")
    return f"heaviest load {show(w, 'weight_kg')}" + (f" over {show(d, 'metres')}" if d is not None else "")


def record_words(lift: str, rec: dict, units=None, page: bool = False) -> str:
    """A record's set in words, as the set reads (R14): '90 s', '60 s at 10 kg', '40 m at 32 kg each in 45 s',
    '8 reps with 20 kg help', '15 reps', with ' a side' for one side at a time. page: a no-break space before each
    unit, for a page."""
    row = LISTED.get(lift)
    w, r, t, d = rec["weight_kg"], rec["reps"], rec["time_s"], rec["distance_m"]
    space = "\u00a0" if page else " "

    def show(value, quantity):
        return formatting.show(value, quantity, page=page, units=units)

    if d is not None:
        done = show(d, "metres")
    elif t is not None:
        done = show(t, "seconds")
    else:
        done = f"{show(r, 'reps')}{space}reps"
    if row is not None and row.sides == "each":
        done += " a side"
    if w is not None and w < 0:
        weight = f"with {show(-w, 'weight_kg')} help"
    elif w:
        weight = f"at {show(w, 'weight_kg')}{' each' if row is not None and row.load_type == 'per_hand' else ''}"
    else:
        weight = None
    after = f"in {show(t, 'seconds')}" if d is not None and t is not None else None
    return " ".join(x for x in (done, weight, after) if x)


def records(sessions: list[LiftSession]) -> dict[str, dict]:
    """{lift: {kind, weight_kg, reps, time_s, distance_m, date}}: for each lift of another kind than standard
    (best_kind) in these sessions, its best set by its kind's order (record_of), with its session's real date. The
    sessions in the one order, as history gives them, so a tie keeps the earlier set. No read of its own."""
    kinds: dict[str, str] = {}
    best: dict[str, tuple] = {}
    for t in sessions:
        for x in sorted(t.sets, key=lambda x: x.id):
            name = x.exercise.name
            kind = kinds.setdefault(name, best_kind(name))
            if kind == "standard":
                continue
            row = (x.weight_kg, x.reps, x.time_s, x.distance_m)
            key = _record_key(kind, row)
            if key is not None and (name not in best or key > best[name][0]):
                best[name] = (key, row, t.date)
    return {name: {"kind": kinds[name], "weight_kg": row[0], "reps": row[1], "time_s": row[2],
                   "distance_m": row[3], "date": on.isoformat()} for name, (_, row, on) in best.items()}


def _joined(items: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'; '' for none."""
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _set_words(name: str, w, r, t, d, units=None) -> tuple:
    """One set's words (contract exercise-list.md R14): (what is done, its weight, what follows the weight, whether
    it is a set of reps). What is done is the reps, the seconds or the metres, with " a side" for a lift done one side
    at a time. The weight is "at 25 kg", "at 25 kg each" for a pair, "with 20 kg help" for a minus weight, or None at
    0 kg. After the weight, a carry's time: "in 45 s". The load type and sides are the list's; a name not on it, a
    retired one or "?", reads plain."""
    row = LISTED.get(name)
    side = " a side" if row is not None and row.sides == "each" else ""
    if d is not None:
        done = formatting.show(d, "metres")
    elif t is not None:
        done = formatting.show(t, "seconds")
    else:
        done = formatting.show(r, "reps") if r is not None else ""
    if w is not None and w < 0:
        weight = f"with {formatting.show(-w, 'weight_kg', units=units)} help"
    elif w:
        each = " each" if row is not None and row.load_type == "per_hand" else ""
        weight = f"at {formatting.show(w, 'weight_kg', units=units)}{each}"
    else:
        weight = None
    after = f"in {formatting.show(t, 'seconds')}" if d is not None and t is not None else None
    return done + side, weight, after, t is None and d is None


def sets_text(sets: list[tuple], units=None) -> str:
    """'Back Squat 3 \u00d7 5 at 80 kg and Bench Press 60 kg \u00d7 5, 60 kg \u00d7 4' from (exercise,
    weight, reps[, time_s, distance_m]), in order: one part a lift, joined as a list. Sets are grouped by their shown
    words (R8), so two sets that read 225 lb \u00d7 5 read "2 \u00d7 5 at 225 lb" whatever kg they hold. In kg shown
    equals stored, so no kg text moves. Each kind's words (exercise-list.md R14): a pair "3 \u00d7 10 at 25 kg each",
    one side at a time "3 \u00d7 10 a side at 20 kg each", help "3 \u00d7 8 with 20 kg help", a hold
    "Plank 2 \u00d7 60 s" and "Plank 60 s at 10 kg", a carry "Farmer's Carry 2 \u00d7 40 m at 32 kg each in 45 s",
    a band "Band Pull-Apart 3 \u00d7 15". One set of time or distance reads with no count before it."""
    groups: dict[str, list[tuple]] = {}
    for name, w, r, *rest in sets:
        t, d = (list(rest) + [None, None])[:2]
        groups.setdefault(name, []).append(_set_words(name, w, r, t, d, units))
    times = "\u00d7"
    parts = []
    for name, rows in groups.items():
        if len(set(rows)) == 1:
            done, weight, after, of_reps = rows[0]
            count = f"{formatting.show(len(rows), 'count')} {times} " if of_reps or len(rows) > 1 else ""
            parts.append(" ".join(x for x in (f"{name} {count}{done}", weight, after) if x))
        else:
            parts.append(f"{name} " + ", ".join(_mixed(*row) for row in rows))
    return _joined(parts)


def _mixed(done: str, weight, after, of_reps: bool) -> str:
    """One set among a lift's sets that differ: "60 kg \u00d7 5", "25 kg each \u00d7 10 a side", "8 reps",
    "8 reps with 20 kg help", "60 s at 10 kg", "40 m at 32 kg each in 45 s"."""
    if of_reps and weight and weight.startswith("at "):
        return f"{weight[3:]} \u00d7 {done}"
    if of_reps:
        done = done[:-len(" a side")] + " reps a side" if done.endswith(" a side") else f"{done} reps"
    return " ".join(x for x in (done, weight, after) if x)


def session_dict(t: LiftSession) -> dict:
    """A session with its sets, in the order they were logged. time_s and distance_m are null on a set of reps."""
    out = row_dict(t)
    out["sets"] = [{"exercise": x.exercise.name, "set_number": x.set_number, "weight_kg": x.weight_kg,
                    "reps": x.reps, "time_s": x.time_s, "distance_m": x.distance_m}
                   for x in sorted(t.sets, key=lambda x: x.id)]
    return out


def number_sets(sets: list[dict], by_name: dict, after: dict | None = None) -> list[tuple]:
    """(exercise_id, set_number, weight_kg, reps, time_s, distance_m) for checked sets, numbered per exercise in the
    order given, from 1 or on from after {exercise_id: highest set number held}. Logging, editing and the import use
    it. The six items are a set as a version and the duplicate key hold it (exercise-list.md R15)."""
    counts = dict(after or {})
    keyed = []
    for x in sets:
        ex_id = by_name[x["exercise"]].id
        counts[ex_id] = counts.get(ex_id, 0) + 1
        keyed.append((ex_id, counts[ex_id], x["weight_kg"], x["reps"], x.get("time_s"), x.get("distance_m")))
    return keyed


def set_row(x: LiftSet) -> tuple:
    """A stored set as number_sets gives one: (exercise_id, set_number, weight_kg, reps, time_s, distance_m)."""
    return (x.exercise_id, x.set_number, x.weight_kg, x.reps, x.time_s, x.distance_m)


def new_set(keyed) -> LiftSet:
    """A set to store, from number_sets' six items."""
    e, n, w, r, t, d = keyed
    return LiftSet(exercise_id=e, set_number=n, weight_kg=w, reps=r, time_s=t, distance_m=d)


def text_rows(sets: list[dict]) -> list[tuple]:
    """Checked sets as sets_text reads them: (exercise, weight, reps, time_s, distance_m)."""
    return [(x["exercise"], x["weight_kg"], x["reps"], x.get("time_s"), x.get("distance_m")) for x in sets]


def session_key(on: date, notes: str, keyed: list[tuple]) -> str:
    """What makes two sessions the same: the date, the notes and the set list (order across lifts
    does not matter; the numbering within a lift does). A set with a time or a distance counts its six items, so two
    holds that differ only in time are two sets (exercise-list.md R15); a set of reps alone counts its first four, so
    its key is the key it had before time and distance."""
    return dedupe_key(on, notes, sorted(tuple(x[:4]) if all(v is None for v in x[4:]) else tuple(x) for x in keyed))


def _links(on: date, lifts: list[str]) -> dict:
    return {"day": links.page("day", date=on),
            "progress": {lift: links.page("progress_lift", lift=lift) for lift in lifts}}


def history(s: Session, user_id: int, today: date, last_day: date | None = None) -> dict:
    """Per lift, from the person's live sessions, in the one order: (effective date, id). Every
    value from metrics. No session is left out for its date: one after today (after a change of
    time zone) counts as today's, and sits after the sessions logged before it today.

    last_day (hardening D3): a day before today reads only the sessions dated on or before it, for get_log of an
    old day. A best depends only on the sessions before it in the one order, so theirs are the same. A last day of
    today or later reads every session: one dated after today counts as today's and can come first.

    One ordered pass (hardening D4): per lift, the best estimate so far, the heaviest weight so far and whether an
    earlier set exists; a session's new bests are judged against those (metrics.new_bests), as metrics.prs judges
    one session against every earlier set.

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
               not copy it. It exists because Epley is None at 0 kg, so such a lift has no best_set. A lift
               of another kind than standard (best_kind: help, band, hold, carry) is never in it: records
               gives its best set.
    bests:     {lift: [{date, exercise, kind, value, previous, session_id}]}  kind estimated_max or weight; on a
               lift of another kind, its own (metrics.other_bests), against every earlier set of it
    """
    query = select(LiftSession).where(LiftSession.user_id == user_id, LiftSession.deleted_at.is_(None))
    if last_day is not None and last_day < today:
        query = query.where(LiftSession.date <= last_day)
    sessions = sorted(s.scalars(query.options(selectinload(LiftSession.sets).selectinload(LiftSet.exercise))),
                      key=lambda t: metrics.order_key(t.date, t.id, today))
    points: dict[str, list[tuple[date, float]]] = {}
    logged: dict[str, int] = {}
    last: dict[str, date] = {}
    last_top: dict[str, dict] = {}
    best_set: dict[str, dict] = {}
    bests: dict[str, list[dict]] = {}
    so_far: dict[str, tuple[float | None, float | None]] = {}  # per lift with an earlier set: best estimate, heaviest
    most_reps: dict[str, dict] = {}   # per lift, the most reps in one set, whatever the weight
    weighted: set[str] = set()        # lifts with a set above 0 kg
    kinds: dict[str, str] = {}        # per lift, its bests' kind (best_kind)
    earlier: dict[str, list[tuple]] = {}  # per lift of another kind than standard: every earlier set, in full
    for t in sessions:
        by_lift: dict[str, list[tuple[float, int]]] = {}
        in_full: dict[str, list[tuple]] = {}
        for x in sorted(t.sets, key=lambda x: x.id):
            by_lift.setdefault(x.exercise.name, []).append((x.weight_kg, x.reps))
            in_full.setdefault(x.exercise.name, []).append((x.weight_kg, x.reps, x.time_s, x.distance_m))
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
            best, heaviest = metrics.session_best(lift_sets), metrics.max_weight(lift_sets)
            if best is not None:
                points.setdefault(lift, []).append((t.date, best))
            if lift in so_far:  # a lift's first session has no baseline, so it sets no best (metrics.prs)
                was_best, was_heaviest = so_far[lift]
                for hit in metrics.new_bests(was_best, was_heaviest, best, heaviest):
                    bests.setdefault(lift, []).append({"date": t.date.isoformat(), "exercise": lift, **hit,
                                                       "session_id": t.id})
            else:
                was_best = was_heaviest = None
            so_far[lift] = (_higher(was_best, best), _higher(was_heaviest, heaviest))
            kind = kinds.setdefault(lift, best_kind(lift))
            if kind != "standard":  # its own bests, against every earlier set of it (exercise-list.md, "Bests")
                held = earlier.setdefault(lift, [])
                for hit in metrics.prs(held, in_full[lift], kind):
                    bests.setdefault(lift, []).append({"date": t.date.isoformat(), "exercise": lift, **hit,
                                                       "session_id": t.id})
                held.extend(in_full[lift])
    # The loop compares the unrounded estimate; the output carries it at 1 dp, in a new dict.
    best_set = {lift: {**b, "estimated_max_kg": formatting.rounded(b["estimated_max_kg"], "estimated_max")}
                for lift, b in best_set.items()}
    # A lift of another kind than standard has its own record (records), never "needs added weight".
    reps_only = {lift: most for lift, most in most_reps.items() if lift not in weighted and kinds[lift] == "standard"}
    return {"sessions": sessions, "points": points, "logged": logged, "last": last, "last_top": last_top,
            "best_set": best_set, "reps_only": reps_only, "bests": bests}


def last_logged(s: Session, user_id: int, names: list[str]) -> dict[str, date]:
    """{lift: the date of its latest live session} for those of these lifts the person has logged: the history a
    plain word's step reads in log_lifts (exercise-list.md R5). One statement."""
    if not names:
        return {}
    return dict(s.execute(
        select(Exercise.name, func.max(LiftSession.date))
        .select_from(LiftSet).join(LiftSession, LiftSession.id == LiftSet.session_id)
        .join(Exercise, Exercise.id == LiftSet.exercise_id)
        .where(LiftSession.user_id == user_id, LiftSession.deleted_at.is_(None), Exercise.name.in_(names))
        .group_by(Exercise.name)).all())


def _higher(a: float | None, b: float | None) -> float | None:
    """The higher of two values, either of which may be None."""
    return b if a is None else a if b is None else max(a, b)


def _prior(s: Session, user_id: int, exercise_ids: set, name_of: dict, on: date, today: date,
           before: int | None = None) -> dict[str, list[tuple]]:
    """{lift: [(weight, reps, time_s, distance_m)]}: every set of these lifts in the person's live sessions that come
    before the session in the one order (effective date, then id). A new session (before None): every session whose
    effective date is on or before its date, so for one dated today every session, a later-dated one included. An add
    to session `before`: the sessions dated before it, and those of its effective date with a lower id. One query."""
    query = select(LiftSet.exercise_id, LiftSet.weight_kg, LiftSet.reps, LiftSet.time_s,
                   LiftSet.distance_m).join(LiftSession).where(
        LiftSession.user_id == user_id, LiftSession.deleted_at.is_(None), LiftSet.exercise_id.in_(exercise_ids))
    if before is not None:
        same_day = LiftSession.date == on if on < today else LiftSession.date >= on  # after today counts as today
        query = query.where(or_(LiftSession.date < on, and_(same_day, LiftSession.id < before)))
    elif on < today:
        query = query.where(LiftSession.date <= on)
    prior: dict[str, list[tuple]] = {}
    for exercise_id, weight, reps, time_s, distance_m in s.execute(query):
        prior.setdefault(name_of[exercise_id], []).append((weight, reps, time_s, distance_m))
    return prior


def _full(x: dict) -> tuple:
    """A checked set as metrics.prs reads it: (weight, reps, time_s, distance_m)."""
    return x["weight_kg"], x["reps"], x.get("time_s"), x.get("distance_m")


def _best(lift: str, hit: dict, units=None) -> dict:
    return shown_best({"exercise": lift, **hit}, units)


def shown_best(raw: dict, units=None) -> dict:
    """A new best for a result, from one of history's or metrics.prs': its numbers rounded in kg, its text in the
    person's units from the unrounded one, naming where it stands. NewBest's fields alone: no place key."""
    shown = {k: v for k, v in rounded_best(raw).items() if k in ("exercise", "kind", "value", "previous")}
    return {**shown, "text": best_text(raw, units)}


def best_key(hit: dict) -> tuple:
    """What a best is a best of: its kind, and for a hold its weight, for a carry its distance (metrics.other_bests
    gives one of each). An add reports a best only when the session did not already hold it, at that figure."""
    return hit["kind"], hit.get("weight_kg") if hit["kind"] == "time" else hit.get("distance_m")


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
    held = [set_row(x) for x in had]
    top: dict[int, int] = {}
    for ex_id, n, *_ in held:
        top[ex_id] = max(top.get(ex_id, 0), n)
    keyed = number_sets(sets, by_name, top)
    prior = _prior(s, session.user_id, {k[0] for k in keyed}, name_of, on, today, before=session.id)
    new_bests, firsts = [], []
    for lift in dict.fromkeys(x["exercise"] for x in sets):
        was = [(w, r, t, d) for ex_id, _, w, r, t, d in held if name_of.get(ex_id) == lift]
        now = was + [_full(x) for x in sets if x["exercise"] == lift]
        if not prior.get(lift):
            firsts.append(first_text(lift, now, units))
            continue
        kind = best_kind(lift)
        before = {best_key(b): b["value"] for b in metrics.prs(prior[lift], was, kind)}
        new_bests += [_best(lift, hit, units) for hit in metrics.prs(prior[lift], now, kind)
                      if best_key(hit) not in before or hit["value"] > before[best_key(hit)]]

    keep_version(s, session, by)  # every check has passed: the session as it stands is kept
    session.sets.extend(new_set(k) for k in keyed)
    if notes:
        session.notes = joined
    if session.logged_by != IMPORTED:  # an imported session keeps the key it was imported under, as an edit does
        session.dedupe_key = session_key(on, session.notes, held + keyed)
    session.updated_at = db.utcnow()
    s.flush()
    summary = (f"Added {sets_text(text_rows(sets), units)} to the "
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
        on, sets, err = rules.check_session(data.get("date"), data.get("sets"), list(by_name), today, logging=True,
                                            last_logged=lambda names: last_logged(s, user.id, names))
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
        grouped: dict[str, list[tuple]] = {}
        for x in sets:
            grouped.setdefault(x["exercise"], []).append(_full(x))
        new_bests = [_best(lift, hit, units) for lift, lift_sets in grouped.items()
                     for hit in metrics.prs(prior.get(lift, []), lift_sets, best_kind(lift))]

        session = LiftSession(user_id=user.id, date=on, notes=notes, logged_by=by, dedupe_key=key)
        session.sets = [new_set(k) for k in keyed]
        s.add(session)
        s.flush()

        summary = (f"{sets_text(text_rows(sets), units)} on "
                   f"{formatting.day(on, today)}.")
        firsts = [first_text(lift, lift_sets, units) for lift, lift_sets in grouped.items() if not prior.get(lift)]
        out = {"kind": KIND, "id": session.id, "duplicate": False, "session": session_dict(session),
               "new_bests": new_bests, "summary": summary + _tail(new_bests, firsts), "links": _links(on, lifts)}
        return with_units_note(out, user, _sent_lb(data.get("sets")), False)
