"""Export: everything the person owns, as CSV texts the web zips (decision 10).

export_data(user_id)
    A read. One CSV per table the person owns, plus account.csv and README.txt:
      account.csv, lift_sessions.csv, lift_sets.csv, bodyweight.csv, measurements.csv, goals.csv,
      saved_foods.csv, food_entries.csv, cardio_entries.csv, entry_versions.csv
    - entry_versions.csv: what an entry held before each edit, undo or redo still kept (contract
      edit-history.md): its kind, the entry's id, when, who, why, and the values as JSON (_before): as stored,
      without LEFT_OUT, and with names as the other files give them. A set is {exercise, set_number,
      weight_kg, reps}, and a cardio entry's exercise_id is exercise, as in cardio_entries.csv. A food entry's
      saved food stays an id: saved_foods.csv has it. An id that names no exercise is written as null. The
      person's own letters are written as they are, never as escapes.
    - Deleted rows are included, with deleted_at; source is included where the table has it.
    - Times are UTC, ISO 8601 with the offset (2026-11-10T08:00:00+00:00). Dates are the person's own.
    - Names, not ids, for what the shared lists hold: a set's exercise, a goal's exercise, a cardio
      entry's activity, a food entry's saved food (its id too). A blank cell is no value. A whole
      number is written without a decimal point (100, not 100.0).
    - Never the owner's id, a duplicate key or a casefolded name: those are Spotter's own bookkeeping.
      The call log is not exported: it holds no content, only that calls happened.
    Rows are in id order. The csv module writes them (RFC 4180: CRLF line ends, quotes where needed).
    - Each CSV starts with the UTF-8 byte-order mark (D16), so a spreadsheet reads it as UTF-8. The web
      writes the text as it gets it.
    - Headers are lower snake_case, a fixed-unit number column named with its unit (weight_kg,
      avg_hr_bpm). A text cell that starts with =, +, - or @ gets a leading apostrophe, so a
      spreadsheet shows it as text and never runs it as a formula.

Returns {files: {name: text}, rows: {name: count} (every CSV, README.txt aside), summary}. The web
puts the files in a zip; nothing here writes a file.
"""

import csv
import io
import json
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import db, formatting
from app.models import (
    Bodyweight, CardioEntry, EntryVersion, Exercise, FoodEntry, Goal, LiftSession, LiftSet, Measurement, SavedFood,
    User,
)
from app.services.common import no_account

ACCOUNT_COLUMNS = ("account_id", "email", "name", "time_zone", "calorie_target_kcal", "target_sessions_per_week",
                   "target_cardio_minutes_per_week", "add_back_exercise_kcal", "claude_can", "created_at")
LEFT_OUT = ("user_id", "dedupe_key", "name_key")
# file -> (model, {column: the name written in its place})
TABLES = {
    "lift_sessions.csv": (LiftSession, {}),
    "bodyweight.csv": (Bodyweight, {}),
    "measurements.csv": (Measurement, {}),
    "goals.csv": (Goal, {"exercise_id": "exercise"}),
    "saved_foods.csv": (SavedFood, {}),
    "food_entries.csv": (FoodEntry, {"saved_food_id": "saved_food"}),
    "cardio_entries.csv": (CardioEntry, {"exercise_id": "exercise", "avg_hr": "avg_hr_bpm"}),
}
BOM = "\ufeff"           # the byte-order mark each CSV starts with (D16)
FORMULA_START = "=+-@"   # what a spreadsheet would read as the start of a formula
SET_COLUMNS = ("session_id", "exercise", "set_number", "weight_kg", "reps")
VERSION_COLUMNS = ("kind", "entry_id", "created_at", "by", "why", "before")
ORDER = ("account.csv", "lift_sessions.csv", "lift_sets.csv", "bodyweight.csv", "measurements.csv", "goals.csv",
         "saved_foods.csv", "food_entries.csv", "cardio_entries.csv", "entry_versions.csv")

README = """Spotter export

Everything Spotter holds for this account, one CSV file per kind of record.

  account.csv         the account and its settings, one row
  lift_sessions.csv   each lift session
  lift_sets.csv       each set, by session_id (the id in lift_sessions.csv)
  bodyweight.csv      each weigh-in, in kg
  measurements.csv    each measurement, in its unit
  goals.csv           each goal, open or closed
  saved_foods.csv     each saved food
  food_entries.csv    each food entry; source says who supplied the number: you_said (the person),
                      saved_food (worked out from a saved food), usda (the USDA table) or
                      claude_estimate (Claude's estimate)
  cardio_entries.csv  each cardio session; estimated_kcal is the app's estimate, machine_kcal the
                      machine's own number
  entry_versions.csv  what an entry held before each edit, undo or redo: kind and entry_id name the
                      entry, by says who changed it (web or claude), why is edit, undo or redo, and
                      before holds the values as JSON: a set's exercise and a cardio activity by
                      name, a saved food by its id in saved_foods.csv. The newest 10 are kept for
                      an entry, for 90 days

Deleted rows are included: deleted_at says when.
logged_by, in lift_sessions, bodyweight, measurements, food_entries and cardio_entries, says how a row
came in: web (on Spotter's own pages), claude (through the assistant) or import (brought over from
another app's export with import_entries).
Times are UTC (ISO 8601, with +00:00). Dates are the account's own local dates.
A blank cell means no value. Exercises, activities and saved foods are given by name.
"""


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # 100.0 kcal is written 100
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str) and value and value[0] in FORMULA_START:
        return "'" + value  # text, never a formula
    return str(value)


def _csv(header: list[str], rows: list[list]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows([_cell(v) for v in row] for row in rows)
    return BOM + out.getvalue()


def _table(s, model, renamed: dict, user_id: int, names: dict) -> tuple[str, int]:
    columns = [c for c in model.__table__.columns if c.name not in LEFT_OUT]
    header = [renamed.get(c.name, c.name) for c in columns]
    if model is FoodEntry:  # the saved food by name, and its id beside it
        header.insert(header.index("saved_food"), "saved_food_id")
    rows = []
    for row in s.scalars(select(model).where(model.user_id == user_id).order_by(model.id)):
        values = []
        for c in columns:
            value = getattr(row, c.key)
            if c.name == "exercise_id":
                value = names["exercise"].get(value)
            elif c.name == "saved_food_id":
                values.append(value)
                value = names["saved_food"].get(value)
            values.append(value)
        rows.append(values)
    return _csv(header, rows), len(rows)


def _before(version: EntryVersion, exercises: dict) -> str:
    """A version's values for entry_versions.csv. See the module notes."""
    values = {k: x for k, x in json.loads(version.before).items() if k not in LEFT_OUT}
    if "sets" in values:
        values["sets"] = [{"exercise": exercises.get(e), "set_number": n, "weight_kg": w, "reps": r}
                          for e, n, w, r in values["sets"]]
    if version.kind == CardioEntry.__tablename__:  # the activity by name, where the id was
        values = {"exercise" if k == "exercise_id" else k: exercises.get(x) if k == "exercise_id" else x
                  for k, x in values.items()}
    return json.dumps(values, ensure_ascii=False)


def export_data(user_id: int) -> dict:
    """Every row this person owns, deleted rows included. See the module notes."""
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        names = {"exercise": {i: n for i, n in s.execute(select(Exercise.id, Exercise.name))},
                 "saved_food": {i: n for i, n in s.execute(select(SavedFood.id, SavedFood.name).where(
                     SavedFood.user_id == user.id))}}
        files, rows = {}, {}
        account = [user.id, user.email, user.name, user.time_zone, user.calorie_target_kcal,
                   user.target_sessions_per_week, user.target_cardio_minutes_per_week, user.add_back_exercise_kcal,
                   user.claude_can, user.created_at]
        files["account.csv"], rows["account.csv"] = _csv(list(ACCOUNT_COLUMNS), [account]), 1
        for name, (model, renamed) in TABLES.items():
            files[name], rows[name] = _table(s, model, renamed, user.id, names)
        sets = s.scalars(select(LiftSet).join(LiftSession).where(LiftSession.user_id == user.id)
                         .order_by(LiftSet.session_id, LiftSet.id).options(selectinload(LiftSet.exercise)))
        set_rows = [[x.session_id, x.exercise.name, x.set_number, x.weight_kg, x.reps] for x in sets]
        files["lift_sets.csv"], rows["lift_sets.csv"] = _csv(list(SET_COLUMNS), set_rows), len(set_rows)
        versions = s.scalars(select(EntryVersion).where(EntryVersion.user_id == user.id).order_by(EntryVersion.id))
        kept = [[v.kind, v.entry_id, v.created_at, v.by, v.why, _before(v, names["exercise"])] for v in versions]
        files["entry_versions.csv"], rows["entry_versions.csv"] = _csv(list(VERSION_COLUMNS), kept), len(kept)
    files = {name: files[name] for name in ORDER}
    files["README.txt"] = README
    rows = {name: rows[name] for name in ORDER}
    total = sum(rows.values())
    return {"files": files, "rows": rows,
            "summary": (f"{formatting.show(total, 'count')} rows in {formatting.show(len(ORDER), 'count')} CSV "
                        "files, deleted rows included, times in UTC.")}
