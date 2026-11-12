"""Any entry by its id: read it, edit it, delete it, restore it.

The kinds are the entry tables themselves (models.entry_models(), named by table), so a table a
later stage adds is readable, deletable and restorable here with no new code. A goal can also be
read by id; it is never deleted, only closed with set_goal (status dropped).

get_entry(user_id, kind, entry_id)       -> {kind, id, entry, links}
edit_entry(user_id, kind, entry_id, changes, by)
                                         -> {kind, id, changed, before, after, summary, links[, note]}
delete_entry(user_id, kind, entry_id)    -> {kind, id, deleted: true, entry, note}
restore_entry(user_id, kind, entry_id)   -> {kind, id, restored: true, entry, replaced [entries][, note]}

- Every lookup goes through common.owned(). Someone else's id, a deleted entry (for read and
  delete) and an id that does not exist all give the same not_found.
- Delete is soft: it sets deleted_at. Restore clears it. A restore of an entry that is not deleted
  is refused with not_deleted.
- One live entry per day. A table with a partial unique index on live rows (bodyweight: one per
  person and date) cannot hold two live rows for the same values. Restoring an entry there
  soft-deletes the live one in its place, and the result names it under "replaced", so the swap
  can itself be undone with another restore. The live one leaves the index before the restored
  one returns, because SQLite checks the index statement by statement.
- An edit changes the entry in place (the id stays): a session's date, notes or whole set list; a
  weigh-in's date or kg; a measurement's date or value. Every value passes the same check a log
  would, so an edit can never store what logging refuses. A new date that already holds a live
  weigh-in (or a measurement of that kind) is refused with day_taken; nothing is replaced. The
  dedupe_key is worked out again, so a repeat of the new content is a duplicate. An imported row
  (logged_by "import") keeps the key it was imported under, so the same import never writes it again.
- Edit, delete and restore are writes: lock_user first, then common.check_write_limit.
- An edit's summary names each change in words, every value through app.formatting: "Weigh-in
  changed: kg from 74.8 to 78.4." The entry's id is the result's id, never in the summary.
"""

from sqlalchemy import Index, select

from app import db, formatting, links, metrics, rules, users
from app.models import (
    IMPORTED, Bodyweight, CardioEntry, FoodEntry, Goal, LiftSession, LiftSet, Measurement, SavedFood, entry_models,
)
from app.rules import error, quote
from app.services import body, cardio, food, lifts, measurements
from app.services.common import (
    check_by, check_data, check_write_limit, exercises, lock_user, no_account, not_found, owned, pair, row_dict,
)
from app.services.goals import goal_dict
from app.services.lifts import session_dict

# What an edit may change, per kind. Every other field is refused.
EDITABLE = {
    "lift_session": ("date", "notes", "sets"),
    "bodyweight": ("date", "kg"),
    "measurement": ("date", "value"),
    "food_entry": ("date", "meal", "description", "kcal"),
    "cardio_entry": ("date", "exercise", "minutes", "distance_km", "incline_pct", "speed_kmh", "avg_hr",
                     "machine_kcal", "notes"),
}
LABELS = {"lift_session": "Lift session", "bodyweight": "Weigh-in", "measurement": "Measurement",
          "food_entry": "Food entry", "cardio_entry": "Cardio entry"}
NOUNS = {"lift_session": "sessions", "bodyweight": "weigh-ins", "measurement": "measurements",
         "food_entry": "meals", "cardio_entry": "cardio sessions"}
SESSION_NOTE = "Bests are worked out from history: get_progress shows them."
KCAL_NOTE = "The number is now the person's own: tagged you_said."
REESTIMATE_NOTE = "The calorie estimate was worked out again, from body weight now."
CARDIO_NUMBERS = ("minutes", "distance_km", "incline_pct", "speed_kmh", "avg_hr", "machine_kcal")


def entry_kinds() -> dict[str, type]:
    """Every entry kind, by table name."""
    return {m.__tablename__: m for m in entry_models()}


def _deletable() -> dict[str, type]:
    """What delete_entry and restore_entry take: every entry kind, and a saved food."""
    return {**entry_kinds(), "saved_food": SavedFood}


def _readable() -> dict[str, type]:
    return {**_deletable(), "goal": Goal}


def _model(kinds: dict[str, type], kind):
    """The model for a kind, or None. Only a string names a kind; anything else is no kind."""
    return kinds.get(kind) if isinstance(kind, str) else None


def _unknown(kind, kinds: dict[str, type]) -> dict:
    if kind == "goal":
        return error("unknown_kind", "A goal is not deleted or restored: it is closed or reopened.",
                     "Use set_goal with goal_id and status dropped (or open).")
    return error("unknown_kind", f"'{quote(kind)}' is not a kind of entry.", f"Use one of: {', '.join(kinds)}.")


def entry_dict(row) -> dict:
    if isinstance(row, LiftSession):
        return session_dict(row)
    if isinstance(row, Goal):
        return goal_dict(row)
    if isinstance(row, FoodEntry):
        return {**food.entry_dict(row), "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None}
    if isinstance(row, CardioEntry):
        return cardio.cardio_dict(row)
    if isinstance(row, SavedFood):
        return {**food.food_dict(row), "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None}
    return row_dict(row)


def _links(row) -> dict:
    if isinstance(row, Goal):
        return {"goals": links.page("goals")}
    if isinstance(row, SavedFood):
        return {"foods": links.page("foods")}
    return {"day": links.page("day", date=row.date)}


def _live_unique(model) -> list[list[str]]:
    """The column sets of each partial unique index on live rows, beyond user_id."""
    out = []
    for index in model.__table__.indexes:
        if not isinstance(index, Index) or not index.unique:
            continue
        where = [index.dialect_options[d].get("where") for d in ("sqlite", "postgresql")]
        if any(w is not None and "deleted_at" in str(w) for w in where):
            out.append([c.name for c in index.columns if c.name != "user_id"])
    return out


def get_entry(user_id: int, kind: str, entry_id) -> dict:
    kinds = _readable()
    model = _model(kinds, kind)
    if model is None:
        return _unknown(kind, kinds)
    with db.new_session() as s:
        row = owned(s, model, user_id, entry_id)
        if row is None:
            return not_found(kind)
        return {"kind": kind, "id": row.id, "entry": entry_dict(row), "links": _links(row)}


def delete_entry(user_id: int, kind: str, entry_id) -> dict:
    kinds = _deletable()
    model = _model(kinds, kind)
    if model is None:
        return _unknown(kind, kinds)
    with db.session_scope() as s:
        if lock_user(s, user_id) is None:
            return no_account()
        limited = check_write_limit(s, user_id)
        if limited:
            return limited
        row = owned(s, model, user_id, entry_id)
        if row is None:
            return not_found(kind)
        row.deleted_at = db.utcnow()
        s.flush()
        return {"kind": kind, "id": row.id, "deleted": True, "entry": entry_dict(row),
                "note": "Deleted. restore_entry with the same kind and id brings it back."}


def restore_entry(user_id: int, kind: str, entry_id) -> dict:
    kinds = _deletable()
    model = _model(kinds, kind)
    if model is None:
        return _unknown(kind, kinds)
    with db.session_scope() as s:
        if lock_user(s, user_id) is None:
            return no_account()
        limited = check_write_limit(s, user_id)
        if limited:
            return limited
        row = owned(s, model, user_id, entry_id, deleted=True)
        if row is None:
            return not_found(kind)
        if row.deleted_at is None:
            return error("not_deleted", "This entry is not deleted, so there is nothing to restore.",
                         "Nothing to do: it is live. get_log shows it on its day.")
        replaced = []
        for columns in _live_unique(model):
            replaced += list(s.scalars(select(model).where(
                model.user_id == user_id, model.deleted_at.is_(None), model.id != row.id,
                *[getattr(model, c) == getattr(row, c) for c in columns])))
        now = db.utcnow()
        for other in replaced:
            other.deleted_at = now
        s.flush()  # the live one leaves the index first
        row.deleted_at = None
        s.flush()
        out = {"kind": kind, "id": row.id, "restored": True, "entry": entry_dict(row),
               "replaced": [entry_dict(r) for r in replaced]}
        if replaced:
            out["note"] = ("Restored. It took the place of the live entry for the same day, which is now deleted: "
                           "restore_entry with that id swaps them back.")
        return out


# ---------------------------------------------------------------- edit

def _set_rows(row: LiftSession) -> list[tuple[int, int, float, int]]:
    return [(x.exercise_id, x.set_number, x.weight_kg, x.reps) for x in sorted(row.sets, key=lambda x: x.id)]


def _quantity(field: str) -> str | None:
    """The quantity a number field is shown in, in an edit summary."""
    return "weigh_in" if field == "kg" else rules.NUMBER_QUANTITIES.get(field)


def _shown(field: str, value, row, today) -> str:
    """One stored value in an edit summary, through the formatter. A number takes its field's quantity,
    with no unit (the field names it), except a measurement's value, which carries its own; a date is
    the page form (D13). Never called on an empty value: the summary says "cleared" or "set to"."""
    if field == "date":
        return formatting.day(value, today)
    if field == "value":
        return measurements.shown(row.kind, value)
    if _quantity(field) and isinstance(value, (int, float)):
        return formatting.show(value, _quantity(field), unit=False)
    return str(value)


def _change(field: str, old, new, row, today) -> tuple[str, str]:
    """A before and an after, widened together when they would print the same (common.pair)."""
    if field == "value":
        return measurements.shown_pair(row.kind, old, new)
    if _quantity(field) and isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return pair(old, new, _quantity(field), unit=False)
    return _shown(field, old, row, today), _shown(field, new, row, today)


def _checked(field: str, value, row, s, today, kind: str) -> tuple[object, dict | None]:
    """A new value for one field, through the same check logging uses."""
    if field == "date":
        if value is None or (isinstance(value, str) and not value.strip()):
            return None, error("invalid_date", "date is empty.", "Give the date as YYYY-MM-DD.")
        return rules.entry_date(value, today, NOUNS[kind])
    if field == "notes":
        return rules.text(value, "notes")
    if field == "sets":
        by_name = {e.name: e for e in exercises(s, "lift")}
        _, checked, err = rules.check_session(None, value, list(by_name), today)
        return (None, err) if err else (lifts.number_sets(checked, by_name), None)
    if field == "kg":
        return body.check_kg(value)
    if field == "value":
        return rules.measurement_value(row.kind, value)
    if field == "meal":
        return rules.match_meal(value)
    if field == "description":
        return rules.required_text(value, "description")
    if field == "kcal":
        return rules.bounded("kcal", value)
    if field == "exercise":
        return cardio.match_cardio(s, value)
    if field in ("distance_km", "incline_pct", "speed_kmh", "avg_hr", "machine_kcal") and value in (None, ""):
        return None, None  # an optional cardio number can be cleared
    return rules.bounded(field, value)


def _day_taken(s, model, row, on, user_id: int) -> dict | None:
    """day_taken when another live row of this person (and, for a measurement, this kind) sits on `on`."""
    same_day = select(model).where(model.user_id == user_id, model.date == on, model.deleted_at.is_(None),
                                   model.id != row.id)
    if model is Measurement:
        same_day = same_day.where(Measurement.kind == row.kind)
    taken = s.scalar(same_day.order_by(model.id).limit(1))
    if taken is None:
        return None
    what = "a weigh-in" if model is Bodyweight else f"a {measurements.kind_word(row.kind).lower()} measurement"
    return error("day_taken", f"{on.isoformat()} already has {what} (id {taken.id}).",
                 f"Edit or delete {taken.id} first, or choose another date. Nothing was changed.",
                 entry_id=taken.id)


def edit_entry(user_id: int, kind: str, entry_id, changes: dict | None, by: str) -> dict:
    """Change one entry in place. changes holds only the fields to change:
    lift_session {date?, notes?, sets?}, bodyweight {date?, kg?}, measurement {date?, value?}.
    sets, when given, replace the whole set list, numbered per exercise in the order given.

    Returns {kind, id, changed [fields whose stored value differs], before, after, summary,
    links {day}[, note]} or {error, message, fix}. The same values again are a success with
    changed []. A goal is edited with set_goal.
    """
    if kind == "goal":
        return error("unknown_kind", "A goal is not edited here.",
                     "Use set_goal with goal_id and the fields to change.")
    if kind == "saved_food":
        return error("unknown_kind", "A saved food is not edited here.",
                     "Use save_food with the same name and the new serving or kcal.")
    if not (isinstance(kind, str) and kind in EDITABLE):
        return error("unknown_kind", f"'{quote(kind)}' is not a kind of entry that can be edited.",
                     f"Use one of: {', '.join(EDITABLE)}.")
    changes, err = check_data(changes)
    err = err or check_by(by)
    if err:
        return err
    if not changes:
        return error("nothing_to_change", "No field to change was given.",
                     f"Give one or more of: {', '.join(EDITABLE[kind])}.")
    unknown = [f for f in changes if f not in EDITABLE[kind]]
    if unknown:
        noun = LABELS[kind].lower()
        return rules.combine([error("invalid_field", f"'{quote(f)}' cannot be edited on a {noun}.",
                                    f"A {noun} can change: {', '.join(EDITABLE[kind])}.") for f in unknown])
    model = entry_kinds()[kind]
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        limited = check_write_limit(s, user.id)
        if limited:
            return limited
        row = owned(s, model, user.id, entry_id)
        if row is None:
            return not_found(kind)
        today = users.today_for(user)
        before = entry_dict(row)
        new, errors = {}, []
        for field, value in changes.items():
            new[field], err = _checked(field, value, row, s, today, kind)
            if err:
                errors += err.get("errors", [err])
        if errors:
            return rules.combine(errors)

        current = {f: getattr(row, f, None) for f in EDITABLE[kind] if f not in ("sets", "exercise")}
        if kind == "lift_session":
            current["sets"] = _set_rows(row)
        if kind == "cardio_entry":
            current["exercise"] = row.exercise
            if "kcal" in current:
                del current["kcal"]
        if kind == "food_entry" and "kcal" in new:
            current["kcal"] = int(row.kcal)
        # A stated number makes a food entry the person's, even when it is the number already there
        # (decision 1: "yes, that's right" is the same number, stated).
        restated = kind == "food_entry" and "kcal" in new and row.source != "you_said"
        changed = [f for f in EDITABLE[kind] if f in new and (new[f] != current[f] or (f == "kcal" and restated))]
        if not changed:
            out = {"kind": kind, "id": row.id, "changed": [], "before": before, "after": before,
                   "summary": f"{LABELS[kind]}: nothing changed. Every value given is already stored.",
                   "links": {"day": links.page("day", date=row.date)}}
            if kind == "lift_session":
                out["note"] = SESSION_NOTE
            return out
        if "date" in changed and kind in ("bodyweight", "measurement"):
            taken = _day_taken(s, model, row, new["date"], user.id)
            if taken:
                return taken

        worked_out = set()
        if kind == "cardio_entry":
            if "speed_kmh" in changed:
                err = cardio.speed_disagrees({f: new.get(f, getattr(row, f)) for f in ("speed_kmh", "distance_km",
                                                                                     "minutes")})
                if err:
                    return err
            moved, err = _cardio_speed(row, new, changed)
            if err:
                return err
            new.update(moved)
            worked_out = set(moved)
            changed += [f for f in moved if f not in changed]
        parts = []
        for field in changed:
            if field == "sets":
                name_of = {e.id: e.name for e in exercises(s, "lift")}

                def written(rows):  # "Back Squat 3 × 5 @ 60 kg", cut to a sane length
                    return quote(lifts.sets_text([(name_of.get(e, "?"), w, r) for e, _, w, r in rows]))

                parts.append(f"sets from {written(current['sets'])} to {written(new['sets'])}")
            elif field == "notes":
                parts.append("notes changed")
            elif field == "description":
                parts.append(f"description from '{quote(current[field])}' to '{quote(new[field])}'")
            elif field == "exercise":
                parts.append(f"activity from {current[field].name} to {new[field].name}")
            elif field == "kcal" and current[field] == new[field]:
                parts.append(f"kcal {_shown(field, new[field], row, today)} confirmed by the person")
            elif new[field] is None:  # a cleared value, in words: never "none", never Python's None
                old = _shown(field, current[field], row, today)
                with_distance = " with the distance" if field in worked_out else ""  # the speed goes with it
                parts.append(f"{field} {old} cleared{with_distance}")
            else:
                how = ", worked out from distance and minutes" if field in worked_out else ""  # the stored value
                if current[field] is None:  # an empty field filled: never Python's None in a summary
                    parts.append(f"{field} set to {_shown(field, new[field], row, today)}{how}")
                else:
                    old, now = _change(field, current[field], new[field], row, today)
                    parts.append(f"{field} from {old} to {now}{how}")
        if "sets" in changed:
            row.sets = [LiftSet(exercise_id=e, set_number=n, weight_kg=w, reps=r) for e, n, w, r in new["sets"]]
        for field in changed:
            if field == "exercise":
                row.exercise_id, row.exercise = new["exercise"].id, new["exercise"]
            elif field != "sets":
                setattr(row, field, new[field])
        note = SESSION_NOTE if kind == "lift_session" else None
        if kind == "food_entry" and "kcal" in changed:
            row.source, row.estimated = "you_said", False  # an edited number is the person's number
            row.saved_food_id = row.servings = row.usda_fdc_id = row.grams = None
            note = KCAL_NOTE
        if kind == "cardio_entry":
            est = cardio.estimate(s, user, row.exercise, row.minutes, today)
            row.estimated_kcal = est["kcal"] if est else None
            row.estimate_weight_kg = est["weight_kg"] if est else None
            note = REESTIMATE_NOTE if est else cardio.NO_WEIGH_IN
        if row.logged_by == IMPORTED:
            pass  # it keeps the key it was imported under: app.services.imports reads it on every re-run
        elif isinstance(row, LiftSession):
            row.dedupe_key = lifts.session_key(row.date, row.notes, new.get("sets", current["sets"]))
        elif isinstance(row, Bodyweight):
            row.dedupe_key = body.weigh_in_key(row.date, row.kg)
        elif isinstance(row, FoodEntry):
            row.dedupe_key = food.entry_key(row.date, [food.entry_tuple(row)])
        elif isinstance(row, CardioEntry):
            row.dedupe_key = cardio.cardio_key(row.date, row.exercise_id, {f: getattr(row, f) for f in CARDIO_NUMBERS})
        else:
            row.dedupe_key = measurements.measurement_key(row.date, row.kind, row.value)
        row.updated_at = db.utcnow()
        s.flush()
        out = {"kind": kind, "id": row.id, "changed": changed, "before": before, "after": entry_dict(row),
               "summary": f"{LABELS[kind]} changed: {'. '.join(parts)}.",
               "links": {"day": links.page("day", date=row.date)}}
        if note:
            out["note"] = note
        return out


def _cardio_speed(row: CardioEntry, new: dict, changed: list) -> tuple[dict, dict | None]:
    """The speed after an edit of distance or minutes. A worked-out speed is worked out again; a speed
    the person gave stays theirs only while it still fits the new distance and minutes (within 20%);
    speed_kmh sent as null is worked out from them."""
    distance = new["distance_km"] if "distance_km" in changed else row.distance_km
    minutes = new["minutes"] if "minutes" in changed else row.minutes
    if "speed_kmh" in changed:
        if new["speed_kmh"] is not None or not distance:
            return {}, None
        values, err = cardio.with_speed({"distance_km": distance, "minutes": minutes, "speed_kmh": None})
        return ({}, err) if err else ({"speed_kmh": values["speed_kmh"]}, None)
    if not ({"distance_km", "minutes"} & set(changed)):
        return {}, None
    was_worked_out = row.speed_kmh is not None and row.speed_kmh == metrics.speed(row.distance_km, row.minutes)
    if row.speed_kmh is not None and not was_worked_out:
        if cardio.speed_disagrees({"speed_kmh": row.speed_kmh, "distance_km": distance, "minutes": minutes}):
            worked = metrics.speed(distance, minutes)
            return {}, error("invalid_field",
                             f"{cardio.ran(distance, minutes)} is {formatting.show(worked, 'speed')}; the speed you "
                             f"gave, {formatting.field(row.speed_kmh, 'speed')}, no longer fits.",
                             "Send speed_kmh too, or speed_kmh as null to have it worked out from distance and "
                             "minutes.")
        return {}, None
    values, err = cardio.with_speed({"distance_km": distance, "minutes": minutes, "speed_kmh": None})
    if err:
        return {}, err
    return ({"speed_kmh": values["speed_kmh"]} if values["speed_kmh"] != row.speed_kmh else {}), None
