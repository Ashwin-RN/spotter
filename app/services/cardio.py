"""Cardio: minutes of an activity, with distance, pace and a calorie estimate.

log_cardio(user_id, data, by)
    data: exercise (a cardio name from the exercise list, matched like a lift name), minutes (1 to
    600, one decimal), date?, distance_km?, incline_pct?, speed_kmh?, avg_hr?, machine_kcal?, notes?,
    allow_repeat?
    - The estimate: MET x 3.5 x kg / 200 x minutes (metrics.cardio_kcal), the MET from the 2024
      Adult Compendium of Physical Activities (app.exercises), kg = body weight now
      (metrics.bodyweight_now over body.weigh_ins). No weigh-in: no estimate, and a note. The
      estimate and the weight it used are stored, so it can be worked out again.
    - The machine's number is stored beside it, never in its place.
    - Pace is minutes per km as m:ss when a distance is given. speed_kmh is stored as given, or
      worked out from distance and minutes. A stated speed more than 20% away from distance / minutes
      is refused, with both numbers. A back-dated session's estimate still uses body weight now, and
      its summary says so.
    - week: the minutes of the 7 days ending today against target_cardio_minutes_per_week.
    - When other activities share the logged one's first word (Treadmill Walk, Treadmill Run), the
      note says which was logged and what the others' estimates would be (or, with no weigh-in, their
      METs), so the person can correct it with edit_entry. A duplicate's note, and the no-weigh-in note, come first.
    - The duplicate guard: the same date, activity and numbers within 10 minutes is a duplicate.

estimate(s, user, exercise, minutes, today) -> {kcal, weight_kg, met, source} | None
cardio_dict(entry) -> the entry's columns plus exercise, pace, estimate and machine_kcal
    The estimate's weight_kg, and the same weight in the column's own key estimate_weight_kg, come back
    at 1 dp here, and only here: the stored column and estimate() keep the working weight, so an
    estimate can be worked out again. The two keys are always equal.

cardio_times(rows, distance_km) -> [(date, minutes, entry_id)]
    The one rule for a time at a distance, for goals and for bests: a session counts at or over the
    distance, and its time is its pace x the distance, 2 dp.
sessions_of(s, user, activity, today) -> [CardioEntry]
    An activity's live sessions, newest first in the one order. get_progress and get_cardio both list them.

get_cardio(user_id, exercise=None)
    A read, for the web only. With an activity: its sessions (newest first, each with its pace as m:ss and
    as minutes per km), its bests (the fastest pace, the longest distance, the best time at 1, 5 and 10 km,
    each when reached) and this week's minutes. Without: one row per activity with a live session ever,
    by name: sessions and minutes in the last 28 days, the fastest pace ever and the last day.
    A session with no distance has no pace and is in no best. A tie goes to the earlier session in the
    one order. A lift's name, or a name not on the list, is unknown_exercise with the cardio names.
    Paces are compared exactly, as fractions of the minutes and the distance entered (exact_pace), so
    float noise never decides a tie: 20.1 min over 4.02 km and 25 min over 5 km are the same 5:00.
    Accepted: fastest_pace and the 1 km time can still name different sessions when two paces differ by
    less than the 2 dp of a minute a time is rounded to (20.1 over 4.01 km against 20.2 over 4.03 km).

Every write here locks the user row first (common.lock_user), then checks the write limit
(common.check_write_limit), before any other read or work.
"""

from datetime import date, timedelta
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import db, formatting, links, metrics, rules, users
from app.exercises import COMPENDIUM
from app.models import CardioEntry, User
from app.services import body
from app.services.common import (
    SAMPLE_NOTE, check_by, check_data, check_write_limit, dedupe_key, duplicate_note, exercises, find_duplicate,
    lock_user, no_account, row_dict, truthy,
)

KIND = "cardio_entry"
NUMBER_FIELDS = ("minutes", "distance_km", "incline_pct", "speed_kmh", "avg_hr", "machine_kcal")
FIELDS = ("exercise", "date", "notes", "allow_repeat", *NUMBER_FIELDS)
NO_WEIGH_IN = ("No weigh-in yet, so no calorie estimate: the estimate needs body weight. Log a weigh-in and "
               "the next cardio entry gets one.")


def source(code: str) -> str:
    return f"{COMPENDIUM}, code {code}"


def estimate(s, user, exercise, minutes: float, today: date) -> dict | None:
    """The calorie estimate for this activity and time, from body weight now; None with no weigh-in."""
    kg = metrics.bodyweight_now([(b.date, b.kg) for b in body.weigh_ins(s, user.id, today)], today)
    if kg is None or exercise.met is None:
        return None
    return {"kcal": metrics.cardio_kcal(exercise.met, kg, minutes), "weight_kg": kg, "met": exercise.met,
            "source": source(exercise.compendium_code)}


def pace_of(entry: CardioEntry) -> dict | None:
    if not entry.distance_km:
        return None
    return {"min_per_km": metrics.pace(entry.distance_km, entry.minutes), "speed_kmh": entry.speed_kmh}


def cardio_dict(entry: CardioEntry) -> dict:
    ex = entry.exercise
    weight = entry.estimate_weight_kg
    weight = None if weight is None else formatting.rounded(weight, "bodyweight_derived")  # one rounding, two keys
    est = None
    if entry.estimated_kcal is not None:
        est = {"kcal": int(entry.estimated_kcal), "weight_kg": weight, "met": ex.met,
               "source": source(ex.compendium_code)}
    return {**row_dict(entry), "estimate_weight_kg": weight, "exercise": ex.name, "pace": pace_of(entry),
            "estimate": est, "machine_kcal": entry.machine_kcal}


def cardio_key(on, exercise_id: int, values: dict) -> str:
    """What makes two cardio entries the same: the date, the activity and the numbers given."""
    return dedupe_key(on, exercise_id, *[values.get(f) for f in NUMBER_FIELDS])


def week_minutes(s, user, today: date) -> dict:
    """Minutes in the 7 days ending today (an entry dated after today counts as today's)."""
    start = today - timedelta(days=6)
    total = sum(s.scalars(select(CardioEntry.minutes).where(
        CardioEntry.user_id == user.id, CardioEntry.deleted_at.is_(None), CardioEntry.date >= start)))
    return {"minutes": round(total, 1), "target_minutes": user.target_cardio_minutes_per_week}


def match_cardio(s, name) -> tuple[object | None, dict | None]:
    """A cardio activity from the exercise list. A lift's name, or any other, is unknown_exercise
    with the cardio names."""
    by_name = {e.name: e for e in exercises(s, "cardio")}
    if name in (None, ""):
        return None, rules.error("missing_field", "exercise is missing.",
                                 f"Give the activity, one of: {', '.join(by_name)}.")
    hit, _ = rules.match_exercise(name, list(by_name))
    if hit is None:
        err = rules.unknown_exercise(name, list(by_name))
        return None, {**err, "fix": f"{err['fix']} Cardio activities: {', '.join(by_name)}."}
    return by_name[hit], None


def check_numbers(data: dict) -> tuple[dict, list[dict]]:
    """minutes (required) and the optional numbers, each in its range."""
    values, errors = {}, []
    for field in NUMBER_FIELDS:
        if field == "minutes" or data.get(field) not in (None, ""):
            values[field], err = rules.bounded(field, data.get(field))
            errors += [err] if err else []
        else:
            values[field] = None
    return values, errors


SPEED_TOLERANCE = 0.20  # a stated speed may differ from distance / minutes by up to this share


def speed_disagrees(values: dict) -> dict | None:
    """invalid_field when a stated speed is more than 20% away from what distance and minutes give."""
    speed, distance, minutes = values.get("speed_kmh"), values.get("distance_km"), values.get("minutes")
    if speed is None or not distance or not minutes:
        return None
    worked = metrics.speed(distance, minutes)
    if abs(speed - worked) <= SPEED_TOLERANCE * worked:
        return None
    return rules.error("invalid_field",
                       f"speed_kmh {formatting.field(speed, 'speed')} does not match {ran(distance, minutes)}, "
                       f"which is {formatting.show(worked, 'speed')}.",
                       "Check the distance, the minutes and the speed, or leave speed_kmh out and the app works it out.")


def ran(distance: float, minutes: float) -> str:
    """'5 km in 30.5 minutes', as stored: the numbers Claude sent, back in a message."""
    return f"{formatting.field(distance, 'km')} km in {formatting.field(minutes, 'session_time')} minutes"


def with_speed(values: dict) -> tuple[dict, dict | None]:
    """speed_kmh as given (and within 20% of distance / minutes), or worked out from distance and minutes;
    the worked-out one must be in range."""
    disagrees = speed_disagrees(values)
    if disagrees:
        return values, disagrees
    if values.get("speed_kmh") is None and values.get("distance_km") and values.get("minutes"):
        worked = metrics.speed(values["distance_km"], values["minutes"])
        lo, hi, _ = rules.NUMBERS["speed_kmh"]
        if not lo <= worked <= hi:
            return values, rules.error("invalid_field",
                                       f"{ran(values['distance_km'], values['minutes'])} is "
                                       f"{formatting.show(worked, 'speed')}, outside {rules.span(lo, hi, 'speed')}.",
                                       "Check the distance and the minutes.")
        values = {**values, "speed_kmh": worked}
    return values, None


def _joined(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def sibling_note(s, exercise, est: dict | None, minutes: float) -> str | None:
    """When other cardio activities share this one's first word (Treadmill Walk and Treadmill Run),
    say which was logged and what the others would give, so a guess is visible and fixable. With an
    estimate: each sibling's own estimate, from the same weight and minutes. Without one: each
    sibling's MET against this one's, and whether its estimate would be higher or lower. Derived from
    the names; None for an activity with no sibling."""
    first = exercise.name.split()[0].casefold()
    others = [e for e in exercises(s, "cardio") if e.id != exercise.id and e.name.split()[0].casefold() == first
              and e.met and exercise.met]
    if not others:
        return None
    word = exercise.name.split()[0].lower()
    head = f"Logged as {exercise.name}."
    tail = "edit_entry changes it."
    met = lambda e: formatting.show(e.met, "met")  # noqa: E731
    if est is not None:
        kcal = {e.id: formatting.show(metrics.cardio_kcal(e.met, est["weight_kg"], minutes), "kcal") for e in others}
        if len(others) == 1:
            other = others[0]
            return (f"{head} {other.name} is the other {word} activity: its estimate would be about "
                    f"{kcal[other.id]} instead of {formatting.show(est['kcal'], 'kcal')}. {tail}")
        listed = _joined([f"{e.name} at about {kcal[e.id]}" for e in others])
        return f"{head} The other {word} activities are {listed}. {tail}"
    if len(others) == 1:
        other = others[0]
        way = "higher" if other.met > exercise.met else "lower"
        return (f"{head} {other.name} is the other {word} activity, with a {way} estimate: "
                f"MET {met(other)} against {met(exercise)}. {tail}")
    listed = _joined([f"{e.name} at MET {met(e)}" for e in others])
    return f"{head} The other {word} activities are {listed}, against MET {met(exercise)} for this one. {tail}"


def summary(entry: CardioEntry, today: date) -> str:
    """'Outdoor Run on Tue 10 Nov for 30:00, 5 km, pace 6:00 per km, about 300 kcal estimated.'"""
    text = (f"{entry.exercise.name} on {formatting.day(entry.date, today)} for "
            f"{formatting.show(entry.minutes, 'session_time')}")
    if entry.distance_km:
        text += (f", {formatting.show(entry.distance_km, 'km')}, pace "
                 f"{metrics.pace(entry.distance_km, entry.minutes)} per km")
    if entry.estimated_kcal is not None:
        text += f", about {formatting.show(entry.estimated_kcal, 'kcal')} estimated"
        if entry.date != today:  # decision 7: body weight now, even for an earlier day; say so
            text += f" from body weight now, {formatting.show(entry.estimate_weight_kg, 'bodyweight_derived')}"
    if entry.machine_kcal is not None:
        text += f", the machine said {formatting.show(entry.machine_kcal, 'kcal')}"
    return text + "."


def log_cardio(user_id: int, data: dict | None, by: str) -> dict:
    """Returns {kind: "cardio_entry", id, duplicate, entry, pace, estimate, machine_kcal, week, summary,
    links {day, progress}[, note]} or {error, message, fix}."""
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
        errors = [rules.error("invalid_field", f"'{rules.quote(k)}' is not a field of a cardio entry.",
                              f"Use: {', '.join(FIELDS)}.") for k in data if k not in FIELDS]
        exercise, err = match_cardio(s, data.get("exercise"))
        errors += [err] if err else []
        values, errs = check_numbers(data)
        errors += errs
        on, err = rules.entry_date(data.get("date"), today, "cardio sessions")
        errors += [err] if err else []
        notes, err = rules.text(data.get("notes"), "notes")
        errors += [err] if err else []
        if not errors:
            values, err = with_speed(values)
            errors += [err] if err else []
        if errors:
            return rules.combine(errors)

        given = {f: data.get(f) for f in NUMBER_FIELDS}
        key = cardio_key(on, exercise.id, {f: values[f] if given[f] not in (None, "") else None for f in NUMBER_FIELDS})
        first = None if truthy(data.get("allow_repeat")) else find_duplicate(s, CardioEntry, user.id, on, key)
        note = None
        if first is not None:
            row, note = first, duplicate_note("cardio session")
        else:
            est = estimate(s, user, exercise, values["minutes"], today)
            row = CardioEntry(user_id=user.id, date=on, exercise_id=exercise.id, notes=notes or "", logged_by=by,
                              dedupe_key=key, estimated_kcal=est["kcal"] if est else None,
                              estimate_weight_kg=est["weight_kg"] if est else None,
                              **{f: values[f] for f in NUMBER_FIELDS})
            row.exercise = exercise
            s.add(row)
            s.flush()
            if est is None:
                note = NO_WEIGH_IN
            sibling = sibling_note(s, exercise, est, values["minutes"])
            if sibling:
                note = f"{note} {sibling}" if note else sibling
        shown = cardio_dict(row)
        out = {"kind": KIND, "id": row.id, "duplicate": first is not None,
               "entry": {k: v for k, v in shown.items() if k not in ("pace", "estimate")},
               "pace": shown["pace"], "estimate": shown["estimate"], "machine_kcal": row.machine_kcal,
               "week": week_minutes(s, user, today),
               "summary": ("Already logged: " if first is not None else "") + summary(row, today),
               "links": {"day": links.page("day", date=row.date), "progress": links.page("progress")}}
        if note:
            out["note"] = note
        return out


# ---------------------------------------------------------------- reading an activity back

WINDOW_DAYS = 28
BEST_DISTANCES_KM = (1.0, 5.0, 10.0)  # the best times get_cardio gives, each when reached


def cardio_times(rows: list, distance_km: float) -> list[tuple[date, float, int]]:
    """(date, minutes for distance_km, entry id) for each session at or over that distance, in the order
    given: its pace x the distance, 2 dp. A shorter session says nothing about a longer time and is left
    out. The one rule for a goal's time and for a best time."""
    return [(c.date, round(c.minutes / c.distance_km * distance_km, 2), c.id) for c in rows
            if c.distance_km and c.distance_km >= distance_km]


def sessions_of(s, user, activity, today: date) -> list[CardioEntry]:
    """An activity's live sessions, newest first in the one order (effective date, then id)."""
    rows = s.scalars(select(CardioEntry).where(
        CardioEntry.user_id == user.id, CardioEntry.exercise_id == activity.id, CardioEntry.deleted_at.is_(None)))
    return sorted(rows, key=lambda c: metrics.order_key(c.date, c.id, today), reverse=True)


def _pace_min_per_km(entry: CardioEntry) -> float | None:
    """Minutes per km, unrounded; None without a distance."""
    return entry.minutes / entry.distance_km if entry.distance_km else None


def exact_pace(entry: CardioEntry) -> Fraction:
    """Minutes per km as an exact fraction of the values entered: the one key a best pace is picked by,
    so two equal paces tie however their floats round."""
    return Fraction(repr(entry.minutes)) / Fraction(repr(entry.distance_km))


def _first_best(candidates: list, key):
    """The best of candidates given oldest first in the one order, by key (lower is better): the earlier
    one keeps a tie. None for none."""
    best = None
    for c in candidates:
        if best is None or key(c) < key(best):
            best = c
    return best


def _bests(oldest_first: list[CardioEntry]) -> dict:
    """{fastest_pace, longest, times} from one activity's sessions, oldest first in the one order."""
    paced = [c for c in oldest_first if c.distance_km]
    fastest = _first_best(paced, exact_pace)
    longest = _first_best(paced, lambda c: -c.distance_km)
    times = []
    for km in BEST_DISTANCES_KM:
        best = _first_best(cardio_times(paced, km), lambda t: t[1])
        if best is not None:
            times.append({"distance_km": km, "minutes": best[1], "date": best[0].isoformat(), "entry_id": best[2]})
    return {
        "fastest_pace": None if fastest is None else {
            "pace": metrics.pace(fastest.distance_km, fastest.minutes), "pace_min_per_km": _pace_min_per_km(fastest),
            "date": fastest.date.isoformat(), "entry_id": fastest.id},
        "longest": None if longest is None else {
            "distance_km": longest.distance_km, "date": longest.date.isoformat(), "entry_id": longest.id},
        "times": times,
    }


def _session(entry: CardioEntry) -> dict:
    shown = cardio_dict(entry)
    return {"id": entry.id, "date": shown["date"], "minutes": entry.minutes, "distance_km": entry.distance_km,
            "pace": metrics.pace(entry.distance_km, entry.minutes), "pace_min_per_km": _pace_min_per_km(entry),
            "estimate": shown["estimate"], "machine_kcal": entry.machine_kcal, "notes": entry.notes,
            "is_sample": bool(entry.is_sample)}


def _with_note(out: dict, sample: bool, note: str | None) -> dict:
    """includes_sample, and the note: the result's own first, then the sample sentence (rule 16)."""
    out["includes_sample"] = sample
    text = " ".join(t for t in (note, SAMPLE_NOTE if sample else None) if t)
    if text:
        out["note"] = text
    return out


def _activities(s, user, today: date) -> dict:
    """Every activity with a live session ever, by name."""
    rows = sorted(s.scalars(select(CardioEntry).where(CardioEntry.user_id == user.id,
                                                       CardioEntry.deleted_at.is_(None))
                            .options(selectinload(CardioEntry.exercise))),
                  key=lambda c: metrics.order_key(c.date, c.id, today))
    start = today - timedelta(days=WINDOW_DAYS - 1)
    by_activity: dict[str, list[CardioEntry]] = {}
    for c in rows:
        by_activity.setdefault(c.exercise.name, []).append(c)
    activities = []
    for name in sorted(by_activity):
        mine = by_activity[name]
        recent = [c for c in mine if metrics.on_or_before(c.date, today) >= start]
        fastest = _bests(mine)["fastest_pace"]
        activities.append({"exercise": name, "sessions_28d": len(recent),
                           "minutes_28d": round(sum((c.minutes for c in recent), 0.0), 1),
                           "fastest_pace": fastest["pace"] if fastest else None,
                           "last_date": mine[-1].date.isoformat()})
    return _with_note({"today": today.isoformat(), "activities": activities}, any(c.is_sample for c in rows), None)


def get_cardio(user_id: int, exercise: str | None = None) -> dict:
    """One cardio activity's sessions and bests, or a row for every activity. See the module notes.

    With exercise: {today, exercise, sessions [{id, date, minutes, distance_km, pace, pace_min_per_km,
    estimate, machine_kcal, notes, is_sample}] (newest first, in the one order), bests {fastest_pace {pace,
    pace_min_per_km, date, entry_id} | null, longest {distance_km, date, entry_id} | null, times
    [{distance_km, minutes, date, entry_id}]}, week {minutes, target_minutes}, includes_sample[, note]}.
    Without: {today, activities [{exercise, sessions_28d, minutes_28d, fastest_pace, last_date}],
    includes_sample[, note]}. Or {error, message, fix}. Web only: no MCP function returns it. A read: no
    lock, no write limit."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        if exercise in (None, ""):
            return _activities(s, user, today)
        activity, err = match_cardio(s, exercise)
        if err:
            return err
        rows = sessions_of(s, user, activity, today)
        out = {"today": today.isoformat(), "exercise": activity.name, "sessions": [_session(c) for c in rows],
               "bests": _bests(list(reversed(rows))), "week": week_minutes(s, user, today)}
        return _with_note(out, any(c.is_sample for c in rows), None if rows else f"No {activity.name} logged yet.")
