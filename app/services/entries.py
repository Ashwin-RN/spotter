"""Any entry by its id: read it, edit it, delete it, restore it.

The kinds are the entry tables themselves (models.entry_models(), named by table), so a table a
later stage adds is readable, deletable and restorable here with no new code. A goal can also be
read by id; it is never deleted, only closed with set_goal (status dropped).

get_entry(user_id, kind, entry_id)       -> {kind, id, entry, links, updated_at}
edit_entry(user_id, kind, entry_id, changes, by[, drawn_at])
                                         -> {kind, id, changed, before, after, summary, links[, note]}
delete_entry(user_id, kind, entry_id[, by, confirm])
                                         -> {kind, id, deleted: true, entry, note}
                                            by claude: also dry_run false, or a preview
                                            {kind, id, deleted: false, dry_run: true, entry, confirm, note}
restore_entry(user_id, kind, entry_id[, by, undo_edit])
                                         -> {kind, id, restored: true, entry, replaced [entries][, note]}
                                            with undo_edit, also {undone, duplicate, summary[, before]}

- Every lookup goes through common.owned(). Someone else's id, a deleted entry (for read and
  delete) and an id that does not exist all give the same not_found.
- Delete is soft: it sets deleted_at. Restore clears it. A restore of an entry that is not deleted
  is refused with not_deleted.
- A delete through an assistant (by claude) takes two calls (contract switch). After the lock, the write limit and
  the lookup, a call whose confirm is not the entry's code (confirm_code) is a preview: nothing of the row is set,
  and the answer carries the entry, the code and a note. With the code, it deletes. The code is worked out from the
  account, the kind, the id and updated_at, which moves with every change of the row, a delete and a restore among
  them, so the code holds only for the entry as the person saw it. It is not a secret: it guards against a skipped
  step, not against a caller that means to skip it. Any other caller (the web's own clicks, a direct call) deletes at
  once, as before, and its answer has no dry_run.
- One live entry per day. A table with a partial unique index on live rows (bodyweight: one per
  person and date) cannot hold two live rows for the same values. Restoring an entry there
  soft-deletes the live one in its place, and the result names it under "replaced", so the swap
  can itself be undone with another restore. The live one leaves the index before the restored
  one returns, because SQLite checks the index statement by statement. From the web (by web) the
  restore is refused with day_taken instead, and nothing changes: no page brings a set-aside
  entry back.
- An edit changes the entry in place (the id stays): a session's date, notes or whole set list; a
  weigh-in's date or kg; a measurement's date or value. Every value passes the same check a log
  would, so an edit can never store what logging refuses. A new date that already holds a live
  weigh-in (or a measurement of that kind) is refused with day_taken; nothing is replaced. The
  dedupe_key is worked out again, so a repeat of the new content is a duplicate (a lift session has
  no guard). An imported row (logged_by "import") keeps the key it was imported under, so the same
  import never writes it again.
- An edit keeps the row as it stood before it (common.keep_version, contract edit-history.md), and so does
  save_food on a food that exists. restore_entry with undo_edit puts the newest kept version back on a live
  entry: every stored column, a session's sets, a food entry's link, a cardio estimate. What it replaces is
  kept as a version in turn (why undo). An undo is one step, once: when the newest version is an undo's,
  the last edit is already undone and nothing changes (duplicate true), at any age. To get an edit back the
  entry is edited again, and that edit can be undone like any other. An undo never replaces another entry:
  one that would move a weigh-in or a measurement onto a day that holds another live one is refused with
  day_taken, as an edit is, before anything is kept, and replaced is always empty. With undo_edit off,
  restore_entry is what it was, but for one more sentence in not_deleted's fix and the web's refusal above.
- A routine (contract routines.md) is deleted and restored by id as a saved food is, the two calls included. A
  restore that would make one live past rules.ROUTINES_MAX is too_many_routines, as a save is. It is never edited
  here and keeps no version: undo_edit on it is nothing_to_undo.
- A web edit form sends the entry's updated_at as its page drew it (drawn_at, from get_entry). An entry that
  changed since is refused with entry_changed, under the lock, and nothing is saved.
- Edit, delete and restore are writes: lock_user first, then common.check_write_limit.
- An edit's summary names each change in words, every value through app.formatting: "Weigh-in
  changed: kg from 74.8 to 78.4." The entry's id is the result's id, never in the summary.
- Units (contract units.md R13). An edit takes lb beside kg, distance_mi beside distance_km and speed_mph beside
  speed_kmh (one of each pair), and weight_lb in sets, each kept in kg and km; a refusal of one is said in its unit.
  The summary follows the person's setting: on a lb account a changed weight is named in words with its unit
  ("Weigh-in changed: weight from 185 lb to 184 lb."), on a mi account a changed distance or speed ("distance from
  3 mi to 3.4 mi"), and sets go through lifts.sets_text; a kg weight, a km distance and a km/h speed keep the
  field's name, as before. A weight in lb or a distance or speed in miles, while the person has not chosen,
  carries units_note. delete_entry's preview and delete carry summary: the entry in one line in the person's units
  for a session, a weigh-in, a cardio entry and a routine, none for the other kinds.
- Kind words (contract log-and-guide.md, decision 52). edit_entry, delete_entry and restore_entry take log's five
  kind words as well as the table words: log_shape.table_word maps lifts, food and cardio to lift_session,
  food_entry and cardio_entry at the door, before the kind check and before confirm_code hashes the kind. So a
  preview by one word and a confirm by the other match, and every answer echoes the table word.
"""

import hashlib
import json
from datetime import timezone

from sqlalchemy import Index, select

from app import db, formatting, links, metrics, rules, users
from app.log_shape import table_word
from app.models import (
    CLAUDE_CAN, IMPORTED, Bodyweight, CardioEntry, Exercise, FoodEntry, Goal, LiftSession, Measurement, Routine,
    SavedFood, entry_models,
)
from app.rules import error, quote
from app.services import body, cardio, food, lifts, measurements, routines
from app.services.common import (
    KIND_LABELS, check_by, check_data, check_write_limit, entry_changed, exercises, keep_version, lock_user,
    newest_version, no_account, not_found, owned, pair, put_back, read_version, row_dict, sent, truthy,
    with_units_note,
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
# A number an edit may send in the other unit, per kind (decision 4): (stored field, field in lb or mi). Each is
# taken beside EDITABLE, which the refusals still list.
IN_OTHER_UNITS = {"bodyweight": (("kg", "lb"),),
                  "cardio_entry": (("distance_km", "distance_mi"), ("speed_kmh", "speed_mph"))}
# How a refusal of one is said in its unit: (its stored range, the quantity it is shown at, the unit word).
OTHER_RANGES = {"lb": (rules.BODYWEIGHT_KG, "weigh_in", None),
                "distance_mi": (rules.NUMBERS["distance_km"][:2], "km", None),
                "speed_mph": (rules.NUMBERS["speed_kmh"][:2], "speed", "mph")}
ONE_A_DAY = ("bodyweight", "measurement")  # the kinds _day_taken is asked about: one live entry a day
# A delete through an assistant: the preview's two notes (contract switch, "A delete, call by call").
PREVIEW_NOTE = ("Nothing was deleted. Show the person this entry and ask. Send the same kind and id with this confirm "
                "only when they say yes.")
MISMATCH_NOTE = ("That confirm does not match this entry as it stands, so nothing was deleted. Show the person the "
                 "entry and ask again.")
ROUTINE_LIVE = "Nothing to do: it is live. get_routines shows it."  # not_deleted's fix for a routine


def entry_kinds() -> dict[str, type]:
    """Every entry kind, by table name."""
    return {m.__tablename__: m for m in entry_models()}


def _deletable() -> dict[str, type]:
    """What delete_entry and restore_entry take: every entry kind, a saved food and a routine."""
    return {**entry_kinds(), "saved_food": SavedFood, "routine": Routine}


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
    if isinstance(row, Routine):
        return {**routines.routine_dict(row), "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None}
    return row_dict(row)


def _links(row) -> dict:
    if isinstance(row, Goal):
        return {"goals": links.page("goals")}
    if isinstance(row, SavedFood):
        return {"foods": links.page("foods")}
    if isinstance(row, Routine):
        return {"routines": links.page("routines")}
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
        return {"kind": kind, "id": row.id, "entry": entry_dict(row), "links": _links(row),
                "updated_at": row.updated_at.isoformat()}


def confirm_code(user_id: int, kind: str, row) -> str:
    """The code a delete through an assistant must send back: the first 12 hex characters of a SHA-256 over the
    account, the kind, the row's id and its updated_at. Worked out, never stored. Not a secret: see the module notes."""
    at = row.updated_at.astimezone(timezone.utc).isoformat()
    return hashlib.sha256(f"{user_id}|{kind}|{row.id}|{at}".encode()).hexdigest()[:12]


def delete_entry(user_id: int, kind: str, entry_id, by: str | None = None, confirm=None, may_delete=None) -> dict:
    """may_delete (hardening D1): with by claude, the switch is checked again on the locked row, so a switch turned to
    log while the call waited refuses it. may_delete(claude_can) is None to go on, or the refusal, returned as it is:
    the MCP wrapper passes its own not_allowed. With by claude and none passed, only log_delete goes on; anything else
    raises before anything changes. The web passes no by: the person's own click is never checked."""
    kind = table_word(kind)
    kinds = _deletable()
    model = _model(kinds, kind)
    if model is None:
        return _unknown(kind, kinds)
    with db.session_scope() as s:
        user = lock_user(s, user_id)
        if user is None:
            return no_account()
        if by == "claude":
            if may_delete is not None:
                refused = may_delete(user.claude_can)
                if refused is not None:
                    return refused
            elif user.claude_can != CLAUDE_CAN[0]:  # fails closed: log_delete, the one setting that lets a delete on
                raise PermissionError
        limited = check_write_limit(s, user_id)
        if limited:
            return limited
        row = owned(s, model, user_id, entry_id)
        if row is None:
            return not_found(kind)
        if by == "claude":
            code = confirm_code(user_id, kind, row)
            if confirm != code:  # a preview: nothing of the row is set
                return _with_summary({"kind": kind, "id": row.id, "deleted": False, "dry_run": True,
                                      "entry": entry_dict(row), "confirm": code,
                                      "note": PREVIEW_NOTE if confirm is None else MISMATCH_NOTE}, row, user)
        row.deleted_at = db.utcnow()
        s.flush()
        out = {"kind": kind, "id": row.id, "deleted": True, "entry": entry_dict(row),
               "note": "Deleted. restore_entry with the same kind and id brings it back."}
        return _with_summary({**out, "dry_run": False} if by == "claude" else out, row, user)


def entry_words(row, today, units=None) -> str | None:
    """The entry in one line, in the person's units (R14), for delete_entry: a session, a weigh-in, a cardio entry
    and a routine. None for any other kind."""
    if isinstance(row, LiftSession):
        sets = [(x.exercise.name, x.weight_kg, x.reps, x.time_s, x.distance_m)
                for x in sorted(row.sets, key=lambda x: x.id)]
        return f"Lift session on {formatting.day(row.date, today)}: {lifts.sets_text(sets, units)}."
    if isinstance(row, Bodyweight):
        return body.weigh_in_text(row.kg, row.date, today, units)
    if isinstance(row, CardioEntry):
        return cardio.summary(row, today, units)
    if isinstance(row, Routine):
        return f"{row.name}: {routines.lift_words(json.loads(row.lifts), units=units)}."
    return None


def _with_summary(out: dict, row, user) -> dict:
    """delete_entry's answer, with summary when the kind has one."""
    words = entry_words(row, users.today_for(user), users.units_for(user))
    return {**out, "summary": words} if words is not None else out


def restore_entry(user_id: int, kind: str, entry_id, by: str | None = None, undo_edit: bool = False) -> dict:
    kind = table_word(kind)
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
        if truthy(undo_edit):
            return _undo_edit(s, model, kind, user_id, entry_id, by)
        row = owned(s, model, user_id, entry_id, deleted=True)
        if row is None:
            return not_found(kind)
        if row.deleted_at is None:
            return error("not_deleted", "This entry is not deleted, so there is nothing to restore.",
                         ROUTINE_LIVE if kind == "routine" else
                         "Nothing to do: it is live. get_log shows it on its day. To undo its last edit, send "
                         "undo_edit true.")
        if kind == "routine" and routines.live_count(s, user_id) >= rules.ROUTINES_MAX:
            return routines.too_many()  # a restore makes a routine live, so the cap counts it (decision 4)
        if by == "web" and kind in ONE_A_DAY:
            # No page brings a set-aside entry back, so the web's restore never replaces another entry.
            taken = _day_taken(s, model, row, row.date, user_id, "then restore this one again")
            if taken:
                return taken
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


def _undo_edit(s, model, kind: str, user_id: int, entry_id, by) -> dict:
    """restore_entry with undo_edit, under the lock and after the write limit: a live entry goes back to its
    newest kept version, and what it held is kept as a version in turn (why undo). See the module notes."""
    err = check_by(by)
    if err:
        return err
    row = owned(s, model, user_id, entry_id)
    if row is None:
        return not_found(kind)
    if kind == "routine":  # a replace keeps no version (decision 6): said again, it is back
        return error("nothing_to_undo", "A routine has no earlier values kept.",
                     "Save it again with save_routine, as it was.")
    version = newest_version(s, row)
    editor = "save_food" if kind == "saved_food" else "edit_entry"
    if version is None:
        return error("nothing_to_undo", "This entry has no earlier values kept.", f"Edit it with {editor}.")
    label, before = KIND_LABELS[kind][0].capitalize(), entry_dict(row)
    out = {"kind": kind, "id": row.id, "restored": True, "entry": before, "replaced": [], "undone": False,
           "duplicate": False}
    if version.why == "undo":  # one step, once: the newest version is what that undo replaced
        return {**out, "duplicate": True, "summary": f"{label}: the last edit was already undone, so nothing changed.",
                "note": f"There is nothing more to undo. To change it, use {editor}."}
    values = read_version(model, version)
    if kind in ONE_A_DAY and values.get("date", row.date) != row.date:
        # An undo never replaces another entry: a day that holds another live one refuses it, as it does an edit.
        taken = _day_taken(s, model, row, values["date"], user_id, "then send the same call again")
        if taken:
            return taken
    keep_version(s, row, by, "undo")
    put_back(s, row, values)
    return {**out, "entry": entry_dict(row), "undone": True, "before": before,
            "summary": f"{label}: the edit was undone."}


# ---------------------------------------------------------------- edit

def _set_rows(row: LiftSession) -> list[tuple]:
    """The session's sets as lifts.number_sets gives them: six items each, time and distance among them."""
    return [lifts.set_row(x) for x in sorted(row.sets, key=lambda x: x.id)]


def _quantity(field: str) -> str | None:
    """The quantity a number field is shown in, in an edit summary."""
    return "weigh_in" if field == "kg" else rules.NUMBER_QUANTITIES.get(field)


def _in_words(field: str, units) -> str | None:
    """The word a summary names a changed weight, distance or speed by when the person's setting shows it in lb or
    mi (R13), its figure carrying its unit; None where the field's name stays, as before."""
    if field == "kg" and units.weight == "lb":
        return "weight"
    if field in ("distance_km", "speed_kmh") and units.distance == "mi":
        return field.split("_")[0]
    return None


def _stored_units(kind: str, changes: dict) -> tuple[dict, dict, list[dict]]:
    """(the changes with each lb or mile field in its stored field, in the order sent, {stored field: the value as
    sent in lb or mi}, errors). Both of a pair is invalid_field. A null in lb or mi stands for the stored field's."""
    pairs = dict((other, stored) for stored, other in IN_OTHER_UNITS.get(kind, ()))
    out, sent_in, errors = {}, {}, []
    for field, value in changes.items():
        stored = pairs.get(field)
        if stored is None:
            out.setdefault(field, value)
        elif stored in changes:
            errors.append(rules.both_sent(stored, field))
        else:
            out[stored] = rules.converted(value, rules.OTHER_FIELDS[field][1]) if sent(value) else value
            if sent(value):
                sent_in[stored] = (field, value)
    return out, sent_in, errors


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


def _day_taken(s, model, row, on, user_id: int, then: str = "or choose another date") -> dict | None:
    """day_taken when another live row of this person (and, for a measurement, this kind) sits on `on`. `then` is
    what the fix says after "first": an edit can choose another date, an undo can only be sent again."""
    same_day = select(model).where(model.user_id == user_id, model.date == on, model.deleted_at.is_(None),
                                   model.id != row.id)
    if model is Measurement:
        same_day = same_day.where(Measurement.kind == row.kind)
    taken = s.scalar(same_day.order_by(model.id).limit(1))
    if taken is None:
        return None
    what = "a weigh-in" if model is Bodyweight else f"a {measurements.kind_word(row.kind).lower()} measurement"
    return error("day_taken", f"{on.isoformat()} already has {what} (id {taken.id}).",
                 f"Edit or delete {taken.id} first, {then}. Nothing was changed.",
                 entry_id=taken.id)


def edit_entry(user_id: int, kind: str, entry_id, changes: dict | None, by: str, drawn_at=None) -> dict:
    """Change one entry in place. changes holds only the fields to change:
    lift_session {date?, notes?, sets?}, bodyweight {date?, kg?}, measurement {date?, value?}.
    sets, when given, replace the whole set list, numbered per exercise in the order given.

    Returns {kind, id, changed [fields whose stored value differs], before, after, summary,
    links {day}[, note]} or {error, message, fix}. The same values again are a success with
    changed []. A goal is edited with set_goal. drawn_at, from a web form only: the entry's updated_at as
    its page drew it. An entry that changed since is entry_changed.
    """
    kind = table_word(kind)
    if kind == "goal":
        return error("unknown_kind", "A goal is not edited here.",
                     "Use set_goal with goal_id and the fields to change.")
    if kind == "saved_food":
        return error("unknown_kind", "A saved food is not edited here.",
                     "Use save_food with the same name and the new serving or kcal.")
    if kind == "routine":
        return error("unknown_kind", "A routine is not edited here.",
                     "Use save_routine with the same name and the whole routine.")
    if not (isinstance(kind, str) and kind in EDITABLE):
        return error("unknown_kind", f"'{quote(kind)}' is not a kind of entry that can be edited.",
                     f"Use one of: {', '.join(EDITABLE)}.")
    changes, err = check_data(changes)
    err = err or check_by(by)
    if err:
        return err
    sent_changes = changes
    changes, sent_in, errors = _stored_units(kind, changes)
    if errors:
        return rules.combine(errors)
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
        stale = entry_changed(row, drawn_at)
        if stale:
            return stale
        today = users.today_for(user)
        units = users.units_for(user)
        before = entry_dict(row)
        new, errors = {}, []
        for field, value in changes.items():
            new[field], err = _checked(field, value, row, s, today, kind)
            if err and field in sent_in:
                other, raw = sent_in[field]
                limits, quantity, word = OTHER_RANGES[other]
                err = rules.in_unit(err, other, raw, limits, quantity, word=word)
            if err:
                errors += err.get("errors", [err])
        if errors:
            return rules.combine(errors)
        miles = any(sent(sent_changes.get(f)) for f in ("distance_mi", "speed_mph"))
        pounds = sent(sent_changes.get("lb")) or (isinstance(sent_changes.get("sets"), list) and any(
            isinstance(x, dict) and sent(x.get("weight_lb")) for x in sent_changes["sets"]))

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
            return with_units_note(out, user, pounds, miles)
        if "date" in changed and kind in ONE_A_DAY:
            taken = _day_taken(s, model, row, new["date"], user.id)
            if taken:
                return taken

        worked_out = set()
        if kind == "cardio_entry":
            if "speed_kmh" in changed:
                values = {f: new.get(f, getattr(row, f)) for f in ("speed_kmh", "distance_km", "minutes")}
                err = cardio.speed_disagrees(values)
                if err:
                    return cardio.in_miles(err, sent_changes, values) if miles else err
            moved, err = _cardio_speed(row, new, changed)
            if err:
                values = {f: new[f] if f in changed else getattr(row, f) for f in ("distance_km", "minutes")}
                return cardio.in_miles(err, sent_changes, {**values, "speed_kmh": row.speed_kmh}) if miles else err
            new.update(moved)
            worked_out = set(moved)
            changed += [f for f in moved if f not in changed]
        parts = []
        for field in changed:
            if field == "sets":
                name_of = dict(s.execute(select(Exercise.id, Exercise.name)).all())  # a retired lift too

                def written(rows):  # "Back Squat 3 × 5 @ 60 kg", cut to a sane length
                    return quote(lifts.sets_text([(name_of.get(e, "?"), w, r, t, d) for e, _, w, r, t, d in rows],
                                                 units))

                parts.append(f"sets from {written(current['sets'])} to {written(new['sets'])}")
            elif field == "notes":
                parts.append("notes changed")
            elif field == "description":
                parts.append(f"description from '{quote(current[field])}' to '{quote(new[field])}'")
            elif field == "exercise":
                parts.append(f"activity from {current[field].name} to {new[field].name}")
            elif field == "kcal" and current[field] == new[field]:
                parts.append(f"kcal {_shown(field, new[field], row, today)} confirmed by the person")
            elif _in_words(field, units):
                word, quantity = _in_words(field, units), _quantity(field)
                shown = lambda v: formatting.show(v, quantity, units=units)  # noqa: E731
                if new[field] is None:
                    with_distance = " with the distance" if field in worked_out else ""
                    parts.append(f"{word} {shown(current[field])} cleared{with_distance}")
                elif current[field] is None:
                    how = ", worked out from distance and minutes" if field in worked_out else ""
                    parts.append(f"{word} set to {shown(new[field])}{how}")
                else:
                    how = ", worked out from distance and minutes" if field in worked_out else ""
                    old, now = pair(current[field], new[field], quantity, units=units)
                    parts.append(f"{word} from {old} to {now}{how}")
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
        keep_version(s, row, by)  # every check has passed and something changes: the row as it stands is kept
        if "sets" in changed:  # old first (hardening D9): no moment holds two sets with one session, lift and number
            row.sets = []
            s.flush()
            row.sets = [lifts.new_set(k) for k in new["sets"]]
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
        return with_units_note(out, user, pounds, miles)


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
