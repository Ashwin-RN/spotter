"""An edit keeps the old value (docs/contracts/edit-history.md): the values from before each edit are kept, and
restore_entry with undo_edit, or one click, puts them back.

The six kinds are the five entry tables and a saved food. A service call is made directly, an MCP call and a web
post through the real HTTP path (TestClient), as tests/test_idempotency.py makes them. An age is made by writing
created_at or updated_at, never by waiting.
"""

import csv
import io
import json
import re
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import event, inspect, select, update
from sqlalchemy.exc import OperationalError

from app import calllog, db, mcp_server, services, web
from app.db import make_engine
from app.mcp_schemas import RestoreEntryResult
from app.models import (
    Base, Bodyweight, CallLog, CardioEntry, EntryVersion, FoodEntry, LiftSession, LiftSet, Measurement, SavedFood,
    WriteKey,
)
from app.services import common, entries, export

from . import text_rules as tr
from .conftest import TEST_DIR, alembic_config, sign
from .test_calllog import MARKER, rows
from .test_idempotency import K, age, count, day, drawn_key, writer  # noqa: F401
from .test_mcp import call, failed, ok, tool_list
from .test_pages_text import Rendered, entered_text, error_page_findings, page_findings
from .test_trace import tools_call
from .test_web import SITE, client, only_user, post, seam, session_of, sign_in, web_client  # noqa: F401

MODELS = {"lift_session": LiftSession, "bodyweight": Bodyweight, "measurement": Measurement,
          "food_entry": FoodEntry, "cardio_entry": CardioEntry, "saved_food": SavedFood}
KINDS = sorted(MODELS)
NOT_KEPT = {"id", "user_id", "created_at", "deleted_at"}
ROTI = {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120, "grams_per_serving": 40}


def make(kind: str, uid: int) -> int:
    """One entry of the kind, logged for this person: its id."""
    if kind == "lift_session":
        return services.log_lifts(uid, {"notes": "Heavy", "sets": [
            {"exercise": "Back Squat", "weight_kg": 100, "reps": 5},
            {"exercise": "Bench Press", "weight_kg": 60, "reps": 8},
            {"exercise": "Back Squat", "weight_kg": 105, "reps": 3}]}, "claude")["id"]
    if kind == "bodyweight":
        return services.log_bodyweight(uid, {"kg": 80}, "claude")["id"]
    if kind == "measurement":
        return services.log_measurement(uid, {"kind": "waist", "value": 84}, "claude")["id"]
    if kind == "food_entry":  # logged from a saved food: the link an edit of its kcal drops
        roti = services.save_food(uid, ROTI, "claude")["id"]
        return services.log_food(uid, {"entries": [{"saved_food_id": roti, "servings": 2, "meal": "lunch"}]},
                                 "claude")["ids"][0]
    if kind == "cardio_entry":  # estimated from the body weight now. By the time it is edited that has moved
        assert "error" not in services.log_bodyweight(uid, {"kg": 80, "date": day(-1)}, "claude")
        made = services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5, "notes": "Easy"},
                                   "claude")
        assert made["estimate"] and "error" not in services.log_bodyweight(uid, {"kg": 90}, "claude")
        return made["id"]
    return services.save_food(uid, ROTI, "claude")["id"]


EDITS = {
    "lift_session": lambda: {"notes": "Light", "date": day(-1),
                             "sets": [{"exercise": "Deadlift", "weight_kg": 140, "reps": 2}]},
    "bodyweight": lambda: {"kg": 81.5, "date": day(-2)},
    "measurement": lambda: {"value": 83, "date": day(-1)},
    "food_entry": lambda: {"kcal": 300, "description": "Paratha", "meal": "snack", "date": day(-1)},
    "cardio_entry": lambda: {"exercise": "Hiking", "minutes": 45, "avg_hr": 120, "notes": "Hills"},
}


def edit(kind: str, uid: int, entry_id: int, by: str = "claude") -> dict:
    """An edit that changes the entry: every field the kind lets an edit change, or most of them."""
    if kind == "saved_food":  # the same name in another case, another serving and kcal, the grams cleared
        return services.save_food(uid, {"name": "roti", "serving_label": "2 rotis", "kcal_per_serving": 250}, by)
    return services.edit_entry(uid, kind, entry_id, EDITS[kind](), by)


def undo(uid: int, kind: str, entry_id, **more) -> dict:
    return services.restore_entry(uid, kind, entry_id, "claude", undo_edit=True, **more)


def stored(kind: str, row_id: int, but: tuple = ("updated_at",)) -> dict:
    """Every stored column of the row, read fresh, updated_at aside; for a session, its sets too."""
    with db.new_session() as s:
        row = s.get(MODELS[kind], row_id)
        out = {c.name: getattr(row, c.key) for c in row.__table__.columns if c.name not in but}
        if kind == "lift_session":
            out["sets"] = [(x.exercise_id, x.set_number, x.weight_kg, x.reps) for x in s.scalars(
                select(LiftSet).where(LiftSet.session_id == row_id).order_by(LiftSet.id))]
        return out


def versions(uid: int | None = None) -> list[EntryVersion]:
    with db.session_scope() as s:
        found = select(EntryVersion).order_by(EntryVersion.id)
        return list(s.scalars(found if uid is None else found.where(EntryVersion.user_id == uid)))


def shown(uid: int, kind: str, entry_id: int) -> dict:
    return services.get_entry(uid, kind, entry_id)["entry"]


def flags(result: dict) -> tuple:
    """What an undo's result says happened: (undone, redone, duplicate). One of them is true."""
    return result["undone"], result["redone"], result["duplicate"]


def but_when(entry: dict) -> dict:
    """An entry's public shape, less when it last changed."""
    return {k: v for k, v in entry.items() if k != "updated_at"}


def set_back(kind: str, row_id: int, **ago) -> None:
    """Make a row's last change older by writing its updated_at."""
    model = MODELS[kind]
    with db.session_scope() as s:
        s.execute(update(model).where(model.id == row_id).values(updated_at=db.utcnow() - timedelta(**ago)))


# ---------------------------------------------------------------- decisions 2 to 4: an edit, its undo, and back

@pytest.mark.parametrize("kind", KINDS)
def test_an_edit_then_its_undo_puts_every_stored_column_back_and_the_undo_again_brings_the_edit_back(make_user, kind):
    uid = make_user().id
    entry = make(kind, uid)
    set_back(kind, entry, hours=1)
    first = stored(kind, entry)
    assert "error" not in edit(kind, uid, entry)
    edited, as_edited = stored(kind, entry), shown(uid, kind, entry)
    changed = {name for name in first if first[name] != edited[name]}
    assert {"lift_session": {"date", "notes", "sets", "dedupe_key"}, "bodyweight": {"date", "kg", "dedupe_key"},
            "measurement": {"date", "value", "dedupe_key"},
            # the kcal took the link to the saved food with it, and the tag
            "food_entry": {"date", "meal", "description", "kcal", "source", "saved_food_id", "servings", "dedupe_key"},
            # the estimate was worked out again, from the body weight at the edit
            "cardio_entry": {"exercise_id", "minutes", "avg_hr", "notes", "speed_kmh", "estimated_kcal",
                             "estimate_weight_kg", "dedupe_key"},
            "saved_food": {"name", "serving_label", "kcal_per_serving", "grams_per_serving"}}[kind] == changed
    undone = undo(uid, kind, entry)
    assert stored(kind, entry) == first  # every column, the sets, the food link and the cardio estimate among them
    assert flags(undone) == (True, False, False) and (undone["restored"], undone["replaced"]) == (True, [])
    assert undone["before"] == as_edited and undone["entry"] == shown(uid, kind, entry) != as_edited
    assert undone["summary"].endswith(": the edit was undone.") and "note" not in undone
    back = undo(uid, kind, entry, allow_repeat=True)  # the way back: the edit's own values, kept by the undo
    assert stored(kind, entry) == edited and flags(back) == (False, True, False)
    assert but_when(back["entry"]) == but_when(as_edited) and back["before"] == undone["entry"]
    assert back["summary"].endswith(": the edit is back.")
    again = undo(uid, kind, entry)  # a plain undo right after the way back runs at once, and says what it did
    assert stored(kind, entry) == first and flags(again) == (True, False, False)
    assert again["summary"] == undone["summary"] and again["before"] == back["entry"]
    assert [(v.kind, v.entry_id, v.user_id, v.by, v.why) for v in versions()] == [
        (kind, entry, uid, "claude", why) for why in ("edit", "undo", "redo", "undo")]
    for result in (undone, back, again):
        RestoreEntryResult.model_validate(result)
        text = result["summary"]
        assert tr.check(text, tr.SUMMARY) == [] and text.startswith(common.KIND_LABELS[kind][0].capitalize() + ": ")


@pytest.mark.parametrize("kind", KINDS)
def test_a_version_holds_every_stored_column_but_four_the_hidden_one_too_and_a_session_s_sets(make_user, kind):
    uid = make_user().id
    entry = make(kind, uid)
    first = stored(kind, entry, but=())
    edit(kind, uid, entry)
    [version] = versions()
    held = json.loads(version.before)
    columns = {c.name for c in MODELS[kind].__table__.columns} - NOT_KEPT
    assert set(held) == columns | ({"sets"} if kind == "lift_session" else set())
    assert {"dedupe_key"} <= set(held) or kind == "saved_food"  # the hidden column: an undo puts it back as it was
    for name in columns:
        value = first[name]
        assert held[name] == (value.isoformat() if hasattr(value, "isoformat") else value), name
    assert kind != "lift_session" or [tuple(x) for x in held["sets"]] == first["sets"]
    assert version.created_at >= first["updated_at"] and version.request_id is None  # no request: a direct call


@pytest.mark.parametrize("kind", KINDS)
def test_an_undo_sets_updated_at_to_now_and_leaves_created_at_and_deleted_at_alone(make_user, kind):
    uid = make_user().id
    entry = make(kind, uid)
    edit(kind, uid, entry)
    set_back(kind, entry, hours=1)
    before = stored(kind, entry, but=())
    undo(uid, kind, entry)
    after = stored(kind, entry, but=())
    assert after["updated_at"] > before["updated_at"] and db.utcnow() - after["updated_at"] < timedelta(minutes=1)
    assert (after["created_at"], after["deleted_at"]) == (before["created_at"], None)


def set_ids(session: int) -> list[int]:
    with db.new_session() as s:
        return list(s.scalars(select(LiftSet.id).where(LiftSet.session_id == session).order_by(LiftSet.id)))


def test_a_session_s_sets_come_back_in_the_order_they_were_logged(make_user):
    """Squat, Squat, Bench, Squat: by set number it would be Squat, Bench, Squat, Squat, another order."""
    uid = make_user().id
    logged = [("Back Squat", 100, 5), ("Back Squat", 100, 4), ("Bench Press", 60, 8), ("Back Squat", 105, 3)]
    session = services.log_lifts(
        uid, {"sets": [{"exercise": e, "weight_kg": w, "reps": r} for e, w, r in logged]}, "claude")["id"]
    first = stored("lift_session", session)["sets"]
    assert [(number, weight, reps) for _, number, weight, reps in first] == [
        (1, 100, 5), (2, 100, 4), (1, 60, 8), (3, 105, 3)]
    assert "error" not in edit("lift_session", uid, session)  # the sets become one deadlift
    undone = undo(uid, "lift_session", session)
    assert undone["undone"] is True and stored("lift_session", session)["sets"] == first
    assert [(x["exercise"], x["weight_kg"], x["reps"]) for x in undone["entry"]["sets"]] == logged


def test_an_undo_of_an_edit_that_left_the_sets_alone_leaves_them_alone_too(make_user):
    uid = make_user().id
    session = make("lift_session", uid)
    ids = set_ids(session)
    assert services.edit_entry(uid, "lift_session", session, {"notes": "Light"}, "claude")["changed"] == ["notes"]
    assert undo(uid, "lift_session", session)["entry"]["notes"] == "Heavy" and set_ids(session) == ids
    assert undo(uid, "lift_session", session, allow_repeat=True)["redone"] is True and set_ids(session) == ids
    assert len(ids) == 3
    edit("lift_session", uid, session)  # an edit of the sets: its undo writes them again, as it must
    assert undo(uid, "lift_session", session)["undone"] is True and len(set_ids(session)) == 3
    assert set_ids(session) != ids


def test_an_undo_of_an_imported_entry_s_edit_gives_it_back_the_key_it_was_imported_under(make_user):
    uid = make_user(time_zone="UTC").id
    row = {"kind": "measurement", "date": day(-3), "measurement_kind": "hips", "value": 99}
    assert services.import_entries(uid, {"rows": [row], "dry_run": False, "length_unit": "cm"}, "web")[
        "row_outcomes"]["written"] == 1
    with db.new_session() as s:
        hips = s.scalars(select(Measurement).where(Measurement.user_id == uid)).one().id
    first = stored("measurement", hips)
    assert services.edit_entry(uid, "measurement", hips, {"value": 98, "date": day(-2)}, "claude")["changed"]
    assert undo(uid, "measurement", hips)["undone"] is True and stored("measurement", hips) == first
    again = services.import_entries(uid, {"rows": [row], "length_unit": "cm"}, "web")["row_outcomes"]
    assert first["logged_by"] == "import" and again["already_there"] == 1


# ---------------------------------------------------------------- decision 2: which writes keep one

def test_an_edit_that_changes_nothing_or_is_refused_keeps_no_version(make_user):
    uid = make_user().id
    weigh_in, other = make("bodyweight", uid), services.log_bodyweight(uid, {"kg": 70, "date": day(-1)}, "web")["id"]
    same = services.edit_entry(uid, "bodyweight", weigh_in, {"kg": 80}, "claude")
    assert same["changed"] == [] and versions() == []
    for changes in ({}, {"kg": 5}, {"kg": 81, "date": "soon"}, {"kg": 81, "date": day(-1)}, {"value": 1}):
        refused = services.edit_entry(uid, "bodyweight", weigh_in, changes, "claude")
        assert "error" in refused and versions() == [], changes  # day_taken among them: checked before it is kept
    assert services.edit_entry(uid, "bodyweight", 10 ** 9, {"kg": 81}, "claude")["error"] == "not_found"
    assert services.edit_entry(uid, "bodyweight", other, {"kg": 71}, "bot")["error"] == "invalid_by"
    assert versions() == []
    run = make("cardio_entry", uid)  # a speed that no longer fits is refused after the first checks
    assert services.edit_entry(uid, "cardio_entry", run, {"speed_kmh": 30}, "claude")["error"] == "invalid_field"
    assert versions() == []


def test_a_confirmed_estimate_is_an_edit_and_keeps_a_version(make_user):
    uid = make_user().id
    estimate = {"description": "Dal", "kcal": 180, "estimated": True}
    dal = services.log_food(uid, {"entries": [estimate]}, "claude")["ids"][0]
    first = stored("food_entry", dal)
    assert services.edit_entry(uid, "food_entry", dal, {"kcal": 180}, "web")["changed"] == ["kcal"]  # the same number
    assert [v.by for v in versions()] == ["web"] and stored("food_entry", dal)["source"] == "you_said"
    assert undo(uid, "food_entry", dal)["undone"] is True and stored("food_entry", dal) == first  # an estimate again


def test_save_food_keeps_a_version_only_when_it_changes_a_food_that_exists(make_user):
    uid = make_user().id
    made = services.save_food(uid, ROTI, "claude")
    assert made["created"] is True and versions() == []  # a new food has nothing before it
    set_back("saved_food", made["id"], hours=1)
    first = stored("saved_food", made["id"], but=())
    same = services.save_food(uid, dict(ROTI), "web")
    # The result is what it was before this work. The row is not written, so updated_at stays.
    assert same == {**made, "created": False, "summary": made["summary"].replace("Saved", "Updated", 1)}
    assert versions() == [] and stored("saved_food", made["id"], but=()) == first
    assert services.save_food(uid, {**ROTI, "kcal_per_serving": 9000}, "web")["error"] and versions() == []
    assert services.save_food(uid, {**ROTI, "kcal_per_serving": 130}, "web")["created"] is False
    assert [(v.kind, v.entry_id, v.by, v.why) for v in versions()] == [("saved_food", made["id"], "web", "edit")]
    assert json.loads(versions()[0].before)["kcal_per_serving"] == 120


def test_a_deleted_food_brought_back_keeps_a_version_only_when_the_save_also_changes_it(make_user):
    uid = make_user().id
    roti = services.save_food(uid, ROTI, "claude")["id"]
    assert services.delete_entry(uid, "saved_food", roti)["deleted"] is True
    back = services.save_food(uid, dict(ROTI), "claude")
    assert back["summary"].startswith("Brought back and updated Roti") and versions() == []
    assert stored("saved_food", roti)["deleted_at"] is None
    services.delete_entry(uid, "saved_food", roti)
    assert services.save_food(uid, {**ROTI, "kcal_per_serving": 130}, "claude")["id"] == roti and len(versions()) == 1
    undone = undo(uid, "saved_food", roti)  # the values go back. The food stays live: an undo never deletes
    assert undone["entry"]["kcal_per_serving"] == 120 and undone["entry"]["deleted_at"] is None


# ---------------------------------------------------------------- decision 5: an undo sent twice is run once

def test_an_undo_sent_twice_within_10_minutes_runs_once_and_its_second_row_reads_duplicate(writer):
    http, token, account = writer
    entry = make("bodyweight", account)
    edit("bodyweight", account, entry)
    sent = {"kind": "bodyweight", "entry_id": entry, "undo_edit": True}
    first = ok(http, token, "restore_entry", sent)
    after = stored("bodyweight", entry, but=())
    again = ok(http, token, "restore_entry", sent)
    assert first["undone"] is True and stored("bodyweight", entry, but=()) == after and len(versions()) == 2
    assert again == {"kind": "bodyweight", "id": entry, "restored": True, "entry": first["entry"], "replaced": [],
                     "undone": False, "redone": False, "duplicate": True,
                     "summary": "Weigh-in: the edit was already undone, so nothing changed.",
                     "note": "This edit was already undone in the last 10 minutes, so nothing was changed. To bring "
                             "the edit back, send it again with allow_repeat: true."}
    assert [(r.function, r.outcome, r.ok, r.tried) for r in rows()[-2:]] == [
        ("restore_entry", "written", True, "entry_id kind=bodyweight undo_edit"),
        ("restore_entry", "duplicate", True, "entry_id kind=bodyweight undo_edit")]
    assert tr.check(again["note"], tr.SUMMARY) == [] and tr.check(again["summary"], tr.SUMMARY) == []
    assert common.DUPLICATE_MINUTES == 10 and entries.UNDONE_NOTE == again["note"]
    redo = {**sent, "allow_repeat": True}
    back = ok(http, token, "restore_entry", redo)  # with allow_repeat the edit comes back
    assert flags(back) == (False, True, False) and back["entry"]["kg"] == 81.5 and rows()[-1].outcome == "written"
    assert rows()[-1].tried == "allow_repeat entry_id kind=bodyweight undo_edit"
    held = stored("bodyweight", entry, but=())
    twice = ok(http, token, "restore_entry", redo)  # the way back sent again with no key, as a retry is: it runs once
    assert twice == {"kind": "bodyweight", "id": entry, "restored": True, "entry": back["entry"], "replaced": [],
                     "undone": False, "redone": False, "duplicate": True,
                     "summary": "Weigh-in: the edit is in place, so nothing changed.",
                     "note": "allow_repeat only brings back an edit that was undone, and this entry's last edit is "
                             "in place. To undo it, send undo_edit without allow_repeat."}
    assert stored("bodyweight", entry, but=()) == held and [v.why for v in versions()] == ["edit", "undo", "redo"]
    assert rows()[-1].outcome == "duplicate" and entries.IN_PLACE_NOTE == twice["note"]
    assert tr.check(twice["note"], tr.SUMMARY) == [] and tr.check(twice["summary"], tr.SUMMARY) == []
    plain = ok(http, token, "restore_entry", sent)  # a plain undo right after the way back runs, and its words are true
    assert flags(plain) == (True, False, False) and plain["entry"]["kg"] == 80 and plain["before"] == back["entry"]
    assert plain["summary"] == "Weigh-in: the edit was undone." and rows()[-1].outcome == "written"
    assert stored("bodyweight", entry)["kg"] == 80


def test_what_an_undo_does_goes_by_the_newest_version_and_allow_repeat_never_undoes(make_user):
    """Every row of the table in app/services/entries.py's notes, at both ages where the age is any."""
    uid = make_user().id
    entry = make("bodyweight", uid)
    edit("bodyweight", uid, entry)  # 80 to 81.5

    def in_place(why: str) -> None:  # allow_repeat when no edit is undone: nothing changes, at any age
        for minutes in (0, 11):
            age(EntryVersion, versions()[-1].id, minutes=minutes)
            still = undo(uid, "bodyweight", entry, allow_repeat=True)
            assert flags(still) == (False, False, True) and "before" not in still and still["entry"]["kg"] == 81.5
            assert still["summary"] == "Weigh-in: the edit is in place, so nothing changed."
            assert still["note"] == entries.IN_PLACE_NOTE and versions()[-1].why == why

    in_place("edit")  # an edit's, with allow_repeat
    first = undo(uid, "bodyweight", entry)  # an edit's, without it, 11 minutes old: the undo runs
    assert (flags(first), first["entry"]["kg"]) == ((True, False, False), 80)
    age(EntryVersion, versions()[-1].id, minutes=9)  # an undo's, without it, within 10 minutes: already undone
    same = undo(uid, "bodyweight", entry)
    assert flags(same) == (False, False, True)
    assert same["summary"] == "Weigh-in: the edit was already undone, so nothing changed."
    age(EntryVersion, versions()[-1].id, minutes=11)  # over 10 minutes: an undo is itself an edit, and is undone
    late = undo(uid, "bodyweight", entry)
    assert (flags(late), late["entry"]["kg"]) == ((False, True, False), 81.5)
    assert late["summary"] == "Weigh-in: the edit is back."
    in_place("redo")  # a redo's, with allow_repeat
    old = undo(uid, "bodyweight", entry)  # a redo's, without it, 11 minutes old: the undo runs
    assert (flags(old), old["entry"]["kg"]) == ((True, False, False), 80)
    age(EntryVersion, versions()[-1].id, minutes=11)  # an undo's, with allow_repeat, over 10 minutes: the edit is back
    back = undo(uid, "bodyweight", entry, allow_repeat=True)
    assert (flags(back), back["entry"]["kg"]) == ((False, True, False), 81.5)
    assert [v.why for v in versions()] == ["edit", "undo", "redo", "undo", "redo"]
    assert services.edit_entry(uid, "bodyweight", entry, {"kg": 82}, "claude")["changed"] == ["kg"]
    fresh = undo(uid, "bodyweight", entry)  # a new edit's: its undo runs at once
    assert (flags(fresh), fresh["entry"]["kg"], fresh["summary"]) == (
        (True, False, False), 81.5, "Weigh-in: the edit was undone.")


def test_allow_repeat_sent_with_the_first_undo_twice_through_mcp_changes_nothing_either_time(writer):
    """A model that sends allow_repeat with its first undo, and a client that sends the call again: before this the
    first undid the edit and the second brought it back."""
    http, token, account = writer
    entry = make("bodyweight", account)
    edit("bodyweight", account, entry)  # 80 to 81.5
    held, as_edited = stored("bodyweight", entry, but=()), shown(account, "bodyweight", entry)
    sent = {"kind": "bodyweight", "entry_id": entry, "undo_edit": True, "allow_repeat": True}
    first, second = ok(http, token, "restore_entry", sent), ok(http, token, "restore_entry", sent)
    assert first == second == {
        "kind": "bodyweight", "id": entry, "restored": True, "entry": as_edited, "replaced": [],
        "undone": False, "redone": False, "duplicate": True,
        "summary": "Weigh-in: the edit is in place, so nothing changed.",
        "note": "allow_repeat only brings back an edit that was undone, and this entry's last edit is in place. To "
                "undo it, send undo_edit without allow_repeat."}
    assert [(r.function, r.outcome, r.ok) for r in rows()[-2:]] == [("restore_entry", "duplicate", True)] * 2
    assert stored("bodyweight", entry, but=()) == held and held["kg"] == 81.5
    assert [v.why for v in versions()] == ["edit"]
    assert tr.check(first["note"], tr.SUMMARY) == [] and tr.check(first["summary"], tr.SUMMARY) == []
    RestoreEntryResult.model_validate(first)


def test_an_undo_sent_twice_with_one_key_runs_once_and_its_second_row_reads_repeat(writer):
    http, token, account = writer
    entry = make("lift_session", account)
    edit("lift_session", account, entry)
    sent = {"kind": "lift_session", "entry_id": entry, "undo_edit": True, "key": K}
    first = call(http, token, "restore_entry", sent)
    after = stored("lift_session", entry, but=())
    assert call(http, token, "restore_entry", sent) == first and first["isError"] is False  # the first answer again
    assert stored("lift_session", entry, but=()) == after and [v.why for v in versions()] == ["edit", "undo"]
    assert [(r.outcome, r.ok) for r in rows()[-2:]] == [("written", True), ("repeat", True)]
    assert "key" in rows()[-1].tried.split() and "undo_edit" in rows()[-1].tried.split()


# ---------------------------------------------------------------- decision 6 and what an undo never does

NOTHING = {"error": "nothing_to_undo", "message": "This entry has no earlier values kept.",
           "fix": "Edit it with edit_entry."}


@pytest.mark.parametrize("kind", KINDS)
def test_an_entry_never_edited_has_nothing_to_undo(make_user, kind):
    uid = make_user().id
    entry = make(kind, uid)
    first = stored(kind, entry, but=())
    refused = undo(uid, kind, entry)
    # A saved food is edited with save_food: edit_entry refuses it.
    assert refused == ({**NOTHING, "fix": "Edit it with save_food."} if kind == "saved_food" else NOTHING)
    assert stored(kind, entry, but=()) == first and versions() == []
    assert tr.check(refused["message"], tr.MESSAGE) == [] and tr.check(refused["fix"], tr.MESSAGE) == []


def test_nothing_to_undo_reads_refused_at_the_check_on_its_call_s_row(writer):
    http, token, account = writer
    entry = make("bodyweight", account)
    assert failed(http, token, "restore_entry", {"kind": "bodyweight", "entry_id": entry, "undo_edit": True}) == NOTHING
    row = rows()[-1]
    assert (row.ok, row.error_code, row.outcome, row.step) == (False, "nothing_to_undo", "refused", "check")


@pytest.mark.parametrize("kind", KINDS)
def test_an_undo_on_a_deleted_entry_answers_as_an_edit_does_and_restores_nothing(make_user, kind):
    uid = make_user().id
    entry = make(kind, uid)
    edit(kind, uid, entry)
    assert services.delete_entry(uid, kind, entry)["deleted"] is True
    gone = stored(kind, entry, but=())
    assert undo(uid, kind, entry) == undo(uid, kind, 10 ** 9) == common.not_found(kind)
    if kind != "saved_food":
        assert services.edit_entry(uid, kind, entry, EDITS[kind](), "claude") == common.not_found(kind)
    assert stored(kind, entry, but=()) == gone and len(versions()) == 1  # still deleted, and nothing kept or used
    assert services.restore_entry(uid, kind, entry)["restored"] is True  # a plain restore still brings it back
    assert undo(uid, kind, entry)["undone"] is True  # and then the edit can be undone


def test_an_undo_on_a_goal_or_an_unknown_kind_answers_as_a_plain_restore_does(make_user):
    uid = make_user().id
    for kind in ("goal", "food", None, 7):
        assert undo(uid, kind, 1) == services.restore_entry(uid, kind, 1)
        assert undo(uid, kind, 1)["error"] == "unknown_kind"
    assert undo(10 ** 9, "bodyweight", 1) == common.no_account()


def test_an_undo_with_no_caller_named_is_refused_and_a_plain_restore_needs_none(make_user):
    uid = make_user().id
    entry = make("bodyweight", uid)
    edit("bodyweight", uid, entry)
    assert services.restore_entry(uid, "bodyweight", entry, undo_edit=True)["error"] == "invalid_by"
    assert services.restore_entry(uid, "bodyweight", entry, "bot", undo_edit=True)["error"] == "invalid_by"
    assert stored("bodyweight", entry)["kg"] == 81.5 and len(versions()) == 1
    services.delete_entry(uid, "bodyweight", entry)
    assert services.restore_entry(uid, "bodyweight", entry)["restored"] is True


def test_a_plain_restore_answers_with_the_fields_it_always_had_and_no_others(make_user):
    """Statement for statement it is what it was: SP's restore_statements.py compared them before and after."""
    uid = make_user().id
    first = services.log_bodyweight(uid, {"kg": 80}, "claude")["id"]
    second = services.log_bodyweight(uid, {"kg": 81}, "claude")["id"]  # replaces the first
    edit("bodyweight", uid, second)  # a kept version changes nothing for a plain restore
    assert services.delete_entry(uid, "bodyweight", second)["deleted"] is True
    alone = services.restore_entry(uid, "bodyweight", second)
    assert list(alone) == ["kind", "id", "restored", "entry", "replaced"] and alone["replaced"] == []
    services.edit_entry(uid, "bodyweight", second, {"date": stored("bodyweight", first)["date"].isoformat()}, "claude")
    swapped = services.restore_entry(uid, "bodyweight", first)
    assert list(swapped) == ["kind", "id", "restored", "entry", "replaced", "note"]
    assert swapped["note"] == ("Restored. It took the place of the live entry for the same day, which is now deleted: "
                               "restore_entry with that id swaps them back.")
    assert services.restore_entry(uid, "bodyweight", first) == {
        "error": "not_deleted", "message": "This entry is not deleted, so there is nothing to restore.",
        "fix": "Nothing to do: it is live. get_log shows it on its day. To undo its last edit, send undo_edit true."}
    for result in (alone, swapped):
        RestoreEntryResult.model_validate(result)
    assert [name for name, field in RestoreEntryResult.model_fields.items() if field.is_required()] == [
        "kind", "id", "restored", "entry", "replaced"]


# ---------------------------------------------------------------- decision 7: an undo never replaces another entry

@pytest.mark.parametrize("kind, log, what", [
    ("bodyweight", lambda uid, value, on: services.log_bodyweight(uid, {"kg": value, "date": on}, "claude"),
     "a weigh-in"),
    ("measurement", lambda uid, value, on: services.log_measurement(
        uid, {"kind": "waist", "value": value, "date": on}, "claude"), "a waist measurement")])
def test_an_undo_or_the_way_back_onto_a_day_that_holds_another_is_refused_and_nothing_is_kept(make_user, kind, log,
                                                                                             what):
    uid = make_user().id
    moved = log(uid, 80, day(-3))["id"]
    assert services.edit_entry(uid, kind, moved, {"date": day(-2)}, "claude")["changed"] == ["date"]
    there = log(uid, 70, day(-3))["id"]  # the day it left now holds another live one
    if kind == "measurement":  # a measurement of another kind on that day is no part of it
        assert "error" not in services.log_measurement(uid, {"kind": "hips", "value": 95, "date": day(-3)}, "claude")

    def state(other: int) -> tuple:
        return stored(kind, moved, but=()), stored(kind, other, but=()), [(v.id, v.why) for v in versions()]

    before = state(there)
    refused = undo(uid, kind, moved)
    assert refused == {"error": "day_taken", "message": f"{day(-3)} already has {what} (id {there}).",
                       "fix": f"Edit or delete {there} first, then send the same call again. Nothing was changed.",
                       "entry_id": there}
    assert state(there) == before and before[1]["deleted_at"] is None  # the other one is live, and nothing was kept
    assert tr.check(refused["message"], tr.MESSAGE) == [] and tr.check(refused["fix"], tr.MESSAGE) == []
    # The words are an edit's, but for what to do next: an edit can choose another date, an undo cannot.
    edited = services.edit_entry(uid, kind, there, {"date": day(-2)}, "claude")
    assert (edited["error"], edited["entry_id"], edited["message"]) == (
        "day_taken", moved, refused["message"].replace(day(-3), day(-2)).replace(f"(id {there})", f"(id {moved})"))
    assert edited["fix"] == f"Edit or delete {moved} first, or choose another date. Nothing was changed."
    # With the other one out of the way the undo runs, and replaces nothing.
    assert services.delete_entry(uid, kind, there)["deleted"] is True
    undone = undo(uid, kind, moved)
    assert flags(undone) == (True, False, False) and undone["replaced"] == [] and "note" not in undone
    assert undone["entry"]["date"] == day(-3)
    # The way back is refused the same way: the day the undo left holds another by now.
    other = log(uid, 60, day(-2))["id"]
    held = state(other)
    back = undo(uid, kind, moved, allow_repeat=True)
    assert (back["error"], back["entry_id"], back["fix"]) == (
        "day_taken", other, f"Edit or delete {other} first, then send the same call again. Nothing was changed.")
    assert state(other) == held and held[1]["deleted_at"] is None and [why for _, why in held[2]] == ["edit", "undo"]


def test_an_undo_refused_for_its_day_through_mcp_names_the_entry_in_the_way_and_its_row_reads_refused(writer):
    http, token, account = writer
    moved = services.log_bodyweight(account, {"kg": 80, "date": day(-3)}, "claude")["id"]
    assert services.edit_entry(account, "bodyweight", moved, {"date": day(-2)}, "claude")["changed"] == ["date"]
    there = services.log_bodyweight(account, {"kg": 70, "date": day(-3)}, "claude")["id"]
    refused = failed(http, token, "restore_entry", {"kind": "bodyweight", "entry_id": moved, "undo_edit": True})
    assert (refused["error"], refused["entry_id"]) == ("day_taken", there)
    row = rows()[-1]
    assert (row.function, row.ok, row.error_code, row.outcome, row.step) == (
        "restore_entry", False, "day_taken", "refused", "check")
    assert stored("bodyweight", there)["deleted_at"] is None
    assert stored("bodyweight", moved)["date"].isoformat() == day(-2)
    assert [v.why for v in versions()] == ["edit"]


# ---------------------------------------------------------------- decision 8: how much is kept

def test_the_eleventh_version_drops_the_oldest_and_no_other_entry_s_or_person_s(make_user):
    uid, other = make_user().id, make_user().id
    entry, beside = make("bodyweight", uid), make("measurement", uid)
    services.edit_entry(uid, "measurement", beside, {"value": 83}, "claude")
    theirs = make("bodyweight", other)
    services.edit_entry(other, "bodyweight", theirs, {"kg": 60}, "claude")
    with db.session_scope() as s:  # another person's version that names this very entry: never counted, never dropped
        s.add(EntryVersion(user_id=other, kind="bodyweight", entry_id=entry, by="web", why="edit", before="{}"))
    for kg in range(81, 91):
        services.edit_entry(uid, "bodyweight", entry, {"kg": kg}, "claude")
    mine = [v for v in versions(uid) if v.kind == "bodyweight"]
    assert [json.loads(v.before)["kg"] for v in mine] == list(range(80, 90)) and common.VERSIONS_KEPT == 10
    services.edit_entry(uid, "bodyweight", entry, {"kg": 91}, "claude")  # the eleventh
    mine = [v for v in versions(uid) if v.kind == "bodyweight"]
    assert [json.loads(v.before)["kg"] for v in mine] == list(range(81, 91))  # the oldest went, in the edit itself
    assert [(v.kind, v.entry_id) for v in versions(uid) if v.kind != "bodyweight"] == [("measurement", beside)]
    assert [(v.kind, v.entry_id) for v in versions(other)] == [("bodyweight", theirs), ("bodyweight", entry)]


def test_a_refused_eleventh_edit_drops_nothing(make_user):
    uid = make_user().id
    entry = make("bodyweight", uid)
    for kg in range(81, 91):
        services.edit_entry(uid, "bodyweight", entry, {"kg": kg}, "claude")
    assert services.edit_entry(uid, "bodyweight", entry, {"kg": 5}, "claude")["error"] == "invalid_field"
    assert [json.loads(v.before)["kg"] for v in versions()] == list(range(80, 90))


def plant(uid: int, entry: int, **ago) -> int:
    """A version of this person's weigh-in, this old."""
    with db.session_scope() as s:
        row = EntryVersion(user_id=uid, kind="bodyweight", entry_id=entry, by="web", why="edit",
                           before=json.dumps({"kg": 70.0}), created_at=db.utcnow() - timedelta(**ago))
        s.add(row)
        s.flush()
        return row.id


def test_a_version_over_90_days_old_is_never_put_back_swept_or_not(make_user):
    uid = make_user().id
    entry = make("bodyweight", uid)
    old = plant(uid, entry, days=90, minutes=1)
    assert undo(uid, "bodyweight", entry) == NOTHING and [v.id for v in versions()] == [old]  # there, and not used
    kept = plant(uid, entry, days=89, hours=23)
    assert undo(uid, "bodyweight", entry)["entry"]["kg"] == 70 and kept in {v.id for v in versions()}
    assert (calllog.VERSION_DAYS, common.VERSION_DAYS) == (90, 90)


def test_the_sweep_deletes_a_version_91_days_old_keeps_one_89_days_old_and_runs_on_the_call_log_s_daily_clock(
        make_user, monkeypatch):
    uid = make_user().id
    entry = make("bodyweight", uid)
    old, kept = plant(uid, entry, days=91), plant(uid, entry, days=89)
    young = plant(uid, entry, days=80)
    monkeypatch.setattr(calllog, "_swept", None)  # as on the first finished call after the process starts
    monkeypatch.setattr(calllog, "_swept_keys", db.utcnow())
    calllog.sweep()
    assert [v.id for v in versions()] == [kept, young] and old not in (kept, young)
    again = plant(uid, entry, days=91)
    calllog.sweep()
    assert again in {v.id for v in versions()}  # within the day: no sweep
    later = db.utcnow() + timedelta(hours=24, minutes=1)
    monkeypatch.setattr(db, "utcnow", lambda: later)
    calllog.sweep()  # a day on: the one that was 89 days old is over 90 now, and goes with the planted one
    assert [v.id for v in versions()] == [young] and calllog.SWEEP_HOURS == 24


@pytest.mark.parametrize("failing, what, left", [("entry_version", "entry versions", (1, 0, 0)),
                                                 ("call_log", "call log", (0, 1, 0)),
                                                 ("write_key", "write keys", (0, 0, 1))])
def test_one_delete_of_the_sweep_that_fails_never_costs_the_other_two(make_user, monkeypatch, logs, failing, what,
                                                                      left):
    uid = make_user().id
    plant(uid, 1, days=91)
    with db.session_scope() as s:
        s.add(CallLog(function="whoami", started_at=db.utcnow() - timedelta(days=91), ms=1, ok=True, result_bytes=1))
        s.add(WriteKey(user_id=uid, key="old-key-01", function="log_lifts", fingerprint="f" * 64,
                       created_at=db.utcnow() - timedelta(hours=25)))
    monkeypatch.setattr(calllog, "_swept", None)
    monkeypatch.setattr(calllog, "_swept_keys", None)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(f"DELETE FROM {failing}"):
            raise OperationalError("DELETE", {}, Exception(MARKER))

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", fail)
    try:
        calllog.sweep()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert (count(EntryVersion), count(CallLog), count(WriteKey)) == left  # each delete in a transaction of its own
    assert [r.getMessage() for r in logs.records if "swept" in r.getMessage()] == [
        f"The {what} could not be swept: OperationalError"]
    assert MARKER not in logs.text


# ---------------------------------------------------------------- decisions 3 and 13: through MCP

def test_restore_entry_publishes_undo_edit_and_allow_repeat_and_the_instructions_do_not_change(http):
    [tool] = [t for t in tool_list(http, sign()) if t["name"] == "restore_entry"]
    schema = tool["inputSchema"]
    assert list(schema["properties"]) == ["kind", "entry_id", "undo_edit", "allow_repeat", "key"]
    assert schema["required"] == ["kind", "entry_id"]
    for name in ("undo_edit", "allow_repeat"):
        assert {k: v for k, v in schema["properties"][name].items() if k != "description"} == {
            "default": False, "title": name.replace("_", " ").title(), "type": "boolean"}
    assert schema["properties"]["undo_edit"]["description"] == (
        "true undoes the last edit of this entry, which must be live: the values from before it come back. The "
        "newest 10 are kept for an entry, for 90 days.")
    assert schema["properties"]["allow_repeat"]["description"] == (
        "After an undo, true brings the edit back. It never undoes: sent when no edit is undone, it changes nothing. "
        "Send it only when the person asks for the edit back.")
    # Its entry_id has words of its own too: where a saved food's id comes from.
    ids = {t["name"]: t["inputSchema"]["properties"]["entry_id"]["description"] for t in tool_list(http, sign())
           if "entry_id" in t["inputSchema"]["properties"]}
    assert ids == {**dict.fromkeys(("edit_entry", "delete_entry"), mcp_server.ENTRY_ID_DESCRIPTION),
                   "restore_entry": mcp_server.ENTRY_ID_DESCRIPTION + " A saved food's id comes from save_food or "
                                                                      "find_food."}
    # restore_entry's kind has words of its own: an undo takes a saved food too. The other two keep the shared ones.
    kinds = {t["name"]: t["inputSchema"]["properties"]["kind"]["description"] for t in tool_list(http, sign())
             if "kind" in t["inputSchema"]["properties"] and t["name"].endswith("_entry")}
    shared = "lift_session, bodyweight, measurement, food_entry or cardio_entry."
    assert kinds == {"edit_entry": shared, "delete_entry": shared,
                     "restore_entry": shared + " With undo_edit, saved_food too."}
    assert tool["description"] == (
        "Brings back a deleted or replaced entry. If the day already has a live one of the same kind, the two swap: "
        "the live one is set aside and can itself be restored. With undo_edit true it undoes the last edit of a live "
        "entry instead: every value from before that edit comes back, a session's sets, a food entry's saved or USDA "
        "food and a cardio estimate among them. The same undo again within 10 minutes changes nothing, and "
        "allow_repeat true only brings back an edit that was undone: it never undoes one.")
    texts = [tool["description"], *(p["description"] for p in schema["properties"].values()),
             *(p["description"] for p in tool["outputSchema"]["properties"].values())]
    assert all(tr.check(text, tr.CLAUDE) == [] for text in texts)
    optional = {name for name, field in RestoreEntryResult.model_fields.items() if not field.is_required()}
    # No result gains a required field.
    assert optional == {"note", "undone", "redone", "duplicate", "before", "summary"}
    assert tool["outputSchema"]["properties"]["duplicate"]["description"] == (
        "With undo_edit: true when nothing changed: the edit was already undone in the last 10 minutes, or "
        "allow_repeat was sent and no edit is undone. entry is the entry as it stands. Say so.")
    assert tool["outputSchema"]["properties"]["redone"]["description"] == (
        "With undo_edit: true when this call brought the edit back, after an undo.")
    assert len(mcp_server.INSTRUCTIONS) == 3678 and "undo" not in mcp_server.INSTRUCTIONS
    # No other function's arguments changed: the stale form's field is the web's alone.
    names = {name for t in tool_list(http, sign()) for name in t["inputSchema"]["properties"]}
    assert {"undo_edit", "allow_repeat"} <= names and not {"updated_at", "drawn_at", "by"} & names
    assert {"undo_edit", "allow_repeat"} <= calllog.KNOWN_NAMES and not {"updated_at", "drawn_at"} & calllog.KNOWN_NAMES


def test_an_edit_and_its_undo_through_mcp_keep_versions_by_claude_with_the_request_s_id(writer):
    http, token, account = writer
    entry = make("bodyweight", account)
    r, edited = tools_call(http, token, "edit_entry", {"kind": "bodyweight", "entry_id": entry, "kg": 82})
    u, undone = tools_call(http, token, "restore_entry", {"kind": "bodyweight", "entry_id": entry, "undo_edit": True})
    assert edited["isError"] is False and undone["structuredContent"]["entry"]["kg"] == 80
    assert undone["structuredContent"]["before"] == edited["structuredContent"]["after"]
    kept = [(v.by, v.why, v.request_id) for v in versions()]
    assert kept == [("claude", "edit", r.headers["x-request-id"]), ("claude", "undo", u.headers["x-request-id"])]
    assert [row.request_id for row in rows()[-2:]] == [request for _, _, request in kept]  # joined to its trace row
    assert re.fullmatch(r"[0-9a-f]{16}", kept[0][2]) and kept[0][2] != kept[1][2]


def test_a_saved_food_s_edit_is_undone_through_mcp_with_kind_saved_food(writer):
    http, token, account = writer
    roti = make("saved_food", account)
    assert ok(http, token, "save_food", {**ROTI, "kcal_per_serving": 130})["created"] is False
    sent = {"kind": "saved_food", "entry_id": roti, "undo_edit": True}
    undone = ok(http, token, "restore_entry", sent)
    assert flags(undone) == (True, False, False) and undone["entry"]["kcal_per_serving"] == 120
    assert undone["summary"] == "Saved food: the edit was undone." and rows()[-1].tried == (
        "entry_id kind=saved_food undo_edit")
    back = ok(http, token, "restore_entry", {**sent, "allow_repeat": True})
    assert flags(back) == (False, True, False) and stored("saved_food", roti)["kcal_per_serving"] == 130


def test_the_new_refusals_keep_the_text_rules_and_entry_changed_has_its_step(make_user):
    uid = make_user().id
    entry = make("bodyweight", uid)
    set_back("bodyweight", entry, hours=1)
    drawn = services.get_entry(uid, "bodyweight", entry)["updated_at"]
    services.edit_entry(uid, "bodyweight", entry, {"kg": 82}, "claude")
    stale = services.edit_entry(uid, "bodyweight", entry, {"kg": 85}, "web", drawn)
    assert stale == {"error": "entry_changed", "message": "This entry changed since this page was drawn.",
                     "fix": "Open it again and make the change."}
    for refusal in (stale, NOTHING):
        assert tr.check(refusal["message"], tr.MESSAGE) == [] and tr.check(refusal["fix"], tr.MESSAGE) == []
        assert calllog.step(refusal["error"]) == "check"
    assert calllog.STEPS["entry_changed"] == "check" and "nothing_to_undo" not in calllog.STEPS


# ---------------------------------------------------------------- decision 10: a stale form is refused

@pytest.mark.parametrize("drawn_at", [None, "", "soon", "2026-11-14", "2026-11-14T08:00:00", 7, ["x"]])
def test_a_missing_or_malformed_drawn_at_runs_the_edit_as_it_always_ran(make_user, drawn_at):
    uid = make_user().id
    entry, roti = make("bodyweight", uid), make("saved_food", uid)
    assert services.edit_entry(uid, "bodyweight", entry, {"kg": 82}, "web", drawn_at)["changed"] == ["kg"]
    saved = services.save_food(uid, {**ROTI, "kcal_per_serving": 130}, "web", drawn_at)
    assert saved["food"]["kcal_per_serving"] == 130
    assert roti and len(versions()) == 2


def test_under_the_lock_a_form_as_old_as_the_entry_saves_and_one_older_is_refused_with_nothing_saved(make_user):
    uid = make_user().id
    entry, roti = make("bodyweight", uid), make("saved_food", uid)
    for kind, row in (("bodyweight", entry), ("saved_food", roti)):
        set_back(kind, row, hours=1)
    drawn = {kind: services.get_entry(uid, kind, row)["updated_at"] for kind, row in (("bodyweight", entry),
                                                                                      ("saved_food", roti))}
    assert drawn["bodyweight"] == stored("bodyweight", entry, but=())["updated_at"].isoformat()
    # The form is as old as the entry: it saves. That save is the entry's last change now, so the same form is stale.
    assert services.edit_entry(uid, "bodyweight", entry, {"kg": 82}, "web", drawn["bodyweight"])["changed"] == ["kg"]
    assert services.save_food(uid, {**ROTI, "kcal_per_serving": 130}, "web", drawn["saved_food"])["created"] is False
    before = (stored("bodyweight", entry, but=()), stored("saved_food", roti, but=()), len(versions()))
    late = (services.edit_entry(uid, "bodyweight", entry, {"kg": 85}, "web", drawn["bodyweight"]),
            services.save_food(uid, {**ROTI, "kcal_per_serving": 140}, "web", drawn["saved_food"]))
    assert [r["error"] for r in late] == ["entry_changed", "entry_changed"]
    assert (stored("bodyweight", entry, but=()), stored("saved_food", roti, but=()), len(versions())) == before
    # A stale form is refused before its fields are looked at, and a new food of another name has nothing to be stale.
    wrong = services.edit_entry(uid, "bodyweight", entry, {"kg": 5}, "web", drawn["bodyweight"])
    assert wrong["error"] == "entry_changed"
    assert services.save_food(uid, {**ROTI, "name": "Dal"}, "web", drawn["saved_food"])["created"] is True


def web_person(client) -> int:
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.update_settings(uid, {"time_zone": "UTC"}, "web")
    return uid


def drawn_stamp(page: str) -> str:
    [stamp] = re.findall(r'<input type="hidden" name="updated_at" value="([^"]+)">', page)
    return stamp


def test_the_entry_edit_form_carries_updated_at_and_one_older_than_the_entry_is_refused_with_nothing_saved(client):
    uid = web_person(client)
    entry = make("bodyweight", uid)
    set_back("bodyweight", entry, hours=1)
    address = f"/entries/bodyweight/{entry}/edit"
    stamp = drawn_stamp(client.get(address).text)
    assert stamp == services.get_entry(uid, "bodyweight", entry)["updated_at"]
    assert services.edit_entry(uid, "bodyweight", entry, {"kg": 82}, "claude")["changed"] == ["kg"]  # the assistant
    before = (stored("bodyweight", entry, but=()), len(versions()))
    late = post(client, address, kg="85", date=day(), updated_at=stamp)  # the form as the old page drew it
    assert late.status_code == 409 and (stored("bodyweight", entry, but=()), len(versions())) == before
    assert '<h1 class="title">Not saved</h1>' in late.text and "Reload" not in late.text
    for said in ("This entry changed since this page was drawn, so nothing was saved.",
                 "Go back to the day and open it again."):
        assert said in late.text and tr.check(said, tr.PAGE) == []
    row = rows()[-1]
    assert (row.function, row.caller, row.error_code, row.outcome, row.step) == (
        "edit_entry", "web", "entry_changed", "refused", "check")
    assert row.tried == "entry_id kg kind=bodyweight"  # the stamp is no part of what the call tried
    findings = error_page_findings(Rendered(address, late, True, user_id=uid))
    assert not findings, "\n".join(findings)
    fresh = drawn_stamp(client.get(address).text)  # opened again
    assert fresh != stamp and post(client, address, kg="85", date=day(), updated_at=fresh).status_code == 303
    assert stored("bodyweight", entry)["kg"] == 85


def test_the_food_edit_form_carries_updated_at_and_one_older_than_the_food_is_refused_with_nothing_saved(client):
    uid = web_person(client)
    roti = make("saved_food", uid)
    set_back("saved_food", roti, hours=1)
    address = f"/foods/{roti}/edit"
    stamp = drawn_stamp(client.get(address).text)
    assert stamp == services.get_entry(uid, "saved_food", roti)["updated_at"]
    assert services.save_food(uid, {**ROTI, "kcal_per_serving": 130}, "claude")["created"] is False  # the assistant
    before = (stored("saved_food", roti, but=()), len(versions()))
    fields = {"serving_label": "1 roti", "grams_per_serving": "40"}
    # A refused field comes back with the stamp the form was sent with, never a newer one.
    wrong = post(client, address, kcal_per_serving="lots", updated_at=stamp, **fields)
    assert wrong.status_code == 400 and drawn_stamp(wrong.text) == stamp
    late = post(client, address, kcal_per_serving="140", updated_at=stamp, **fields)
    assert late.status_code == 409 and (stored("saved_food", roti, but=()), len(versions())) == before
    assert '<h1 class="title">Not saved</h1>' in late.text and "This entry" not in late.text
    for said in ("This food changed since this page was drawn, so nothing was saved.",
                 "Go back to Foods and open it again."):
        assert said in late.text and tr.check(said, tr.PAGE) == []
    assert (rows()[-1].function, rows()[-1].error_code, rows()[-1].step) == ("save_food", "entry_changed", "check")
    assert "updated_at" not in rows()[-1].tried and "other" not in rows()[-1].tried
    findings = error_page_findings(Rendered(address, late, True, user_id=uid))
    assert not findings, "\n".join(findings)
    fresh = drawn_stamp(client.get(address).text)
    assert post(client, address, kcal_per_serving="140", updated_at=fresh, **fields).status_code == 303
    assert stored("saved_food", roti)["kcal_per_serving"] == 140


def test_a_save_that_lands_while_the_food_edit_page_is_drawn_is_never_overwritten_by_that_page(client, monkeypatch):
    """The page reads the food's stamp and its values in two reads. The assistant's save lands between them here,
    whichever comes first. The stamp is read first, so the form is older than the save and is refused: the worst
    case is a refusal the person did not need, never the old values back over the newer save."""
    uid = web_person(client)
    roti = make("saved_food", uid)  # 1 roti, 120 kcal, 40 g
    set_back("saved_food", roti, hours=1)
    reads = []

    def between(read):
        def run(*args, **kwargs):
            reads.append(read.__name__)
            if len(reads) == 2:  # the assistant's save, after the page's first read and before its second
                assert services.save_food(uid, {**ROTI, "kcal_per_serving": 130}, "claude")["created"] is False
            return read(*args, **kwargs)
        return run

    monkeypatch.setattr(services, "get_entry", between(services.get_entry))
    monkeypatch.setattr(services, "list_saved_foods", between(services.list_saved_foods))
    page = client.get(f"/foods/{roti}/edit").text
    monkeypatch.undo()
    assert reads == ["get_entry", "list_saved_foods"]  # the stamp, then the values
    [drawn_kcal] = re.findall(r'name="kcal_per_serving" value="([^"]*)"', page)
    stamp = drawn_stamp(page)
    assert drawn_kcal == "130" and stamp != services.get_entry(uid, "saved_food", roti)["updated_at"]
    sent = post(client, f"/foods/{roti}/edit", serving_label="1 big roti", kcal_per_serving=drawn_kcal,
                grams_per_serving="40", updated_at=stamp)  # the form as drawn, one field changed
    assert sent.status_code == 409 and "This food changed since this page was drawn" in sent.text
    assert (stored("saved_food", roti)["kcal_per_serving"], stored("saved_food", roti)["serving_label"]) == (
        130, "1 roti")


def test_an_entry_s_refused_field_comes_back_with_the_stamp_the_form_was_sent_with(client):
    uid = web_person(client)
    entry = make("bodyweight", uid)
    address = f"/entries/bodyweight/{entry}/edit"
    stamp = drawn_stamp(client.get(address).text)
    wrong = post(client, address, kg="heavy", date=day(), updated_at=stamp)
    assert wrong.status_code == 400 and drawn_stamp(wrong.text) == stamp
    none = post(client, address, kg="heavy", date=day())  # a form with no stamp is sent back with none
    assert none.status_code == 400 and 'name="updated_at"' not in none.text


def test_a_second_click_of_save_lands_on_the_day_unchanged_though_the_first_made_its_form_stale(client):
    """The form's own first send is the entry's last change. Its second send differs from the entry in nothing, so
    it writes nothing and lands on the day, as idempotency.md's known limit has it, and never on the refusal."""
    uid = web_person(client)
    entry = make("bodyweight", uid)
    set_back("bodyweight", entry, hours=1)
    address = f"/entries/bodyweight/{entry}/edit"
    page = client.get(address).text
    sent = {"kg": "82", "date": day(), "updated_at": drawn_stamp(page), "key": drawn_key(page, address)}
    first, second = post(client, address, **sent), post(client, address, **sent)
    assert first.headers["location"] == SITE + f"/day/{day()}?saved=bodyweight:{entry}"
    assert second.status_code == 303 and second.headers["location"] == SITE + f"/day/{day()}?saved=unchanged"
    assert len(versions()) == 1 and [r.outcome for r in rows() if r.function == "edit_entry"] == ["written"]


# ---------------------------------------------------------------- decision 9: Undo on the page, and the way back

def strip_form(client, address: str) -> tuple[str, str, str, dict]:
    """The strip a page shows: (its words, its form's address, its button, its hidden fields but the token)."""
    page = client.get(address.removeprefix(SITE))
    assert page.status_code == 200, page.text[:300]
    [(words, action, inner)] = re.findall(
        r'<div class="ok undo">\s*(.*?)\s*<form method="post" action="([^"]+)" class="inline">(.*?)</form>\s*</div>',
        page.text, re.S)
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', inner))
    assert fields.pop("csrf_token") == session_of(client)["csrf"] and list(fields)[-1] == "key"
    [button] = re.findall(r'<button class="link">([^<]+)</button>', inner)
    findings = page_findings(address.removeprefix(SITE), page.text, entered_text(only_user().id), only_user().id)
    assert not findings, "\n".join(findings)
    return words, action, button, fields


WEB_EDITS = {"bodyweight": {"kg": "82"}, "measurement": {"value": "83"}, "lift_session": {"notes": "Light"},
             "food_entry": {"description": "Paratha", "kcal": "300", "meal": "snack"},
             "cardio_entry": {"exercise": "Hiking", "minutes": "45", "notes": "Hills"}}
WORDS = {"bodyweight": "Weigh-in", "measurement": "Measurement", "lift_session": "Lift session",
         "food_entry": "Food entry", "cardio_entry": "Cardio session"}


@pytest.mark.parametrize("kind", sorted(WEB_EDITS))
def test_after_an_edit_the_day_offers_undo_and_after_the_undo_the_way_back(client, kind):
    uid = web_person(client)
    entry = make(kind, uid)
    first = stored(kind, entry)
    address = f"/entries/{kind}/{entry}/edit"
    saved = post(client, address, date=day(), updated_at=drawn_stamp(client.get(address).text), **WEB_EDITS[kind])
    assert saved.status_code == 303 and saved.headers["location"] == SITE + f"/day/{day()}?saved={kind}:{entry}"
    edited = stored(kind, entry)
    words, action, button, fields = strip_form(client, saved.headers["location"])
    assert (words, action, button) == (f"{WORDS[kind]} saved.", f"/entries/{kind}/{entry}/restore", "Undo")
    assert {k: v for k, v in fields.items() if k != "key"} == {"undo_edit": "1"} and edited != first
    undone = post(client, action, **fields)  # one click
    assert undone.status_code == 303 and undone.headers["location"] == SITE + f"/day/{day()}?restored={kind}:{entry}"
    assert stored(kind, entry) == first
    words, back, button, again = strip_form(client, undone.headers["location"])
    assert (words, back, button) == (f"{WORDS[kind]} edit undone.", action, "Redo")
    assert {k: v for k, v in again.items() if k != "key"} == {"undo_edit": "1", "allow_repeat": "1"}
    redone = post(client, back, **again)  # the way back: the undo with allow_repeat
    assert redone.status_code == 303 and redone.headers["location"] == SITE + f"/day/{day()}?saved={kind}:{entry}"
    assert stored(kind, entry) == edited and [(v.by, v.why, v.request_id) for v in versions()] == [
        ("web", why, r.headers["x-request-id"]) for why, r in (("edit", saved), ("undo", undone), ("redo", redone))]
    # The page after Redo offers Undo again, and each button goes on doing what it says.
    words, action, button, fields = strip_form(client, redone.headers["location"])
    assert (words, button, set(fields)) == (f"{WORDS[kind]} saved.", "Undo", {"undo_edit", "key"})
    second = post(client, action, **fields)
    assert second.headers["location"] == undone.headers["location"] and stored(kind, entry) == first
    _, back, button, again = strip_form(client, second.headers["location"])
    assert button == "Redo" and post(client, back, **again).headers["location"] == redone.headers["location"]
    assert stored(kind, entry) == edited and [v.why for v in versions()] == ["edit", "undo", "redo", "undo", "redo"]
    assert [(r.function, r.caller, r.outcome, r.tried) for r in rows()[-4:]] == [
        ("restore_entry", "web", "written", f"entry_id key kind={kind} undo_edit"),
        ("restore_entry", "web", "written", f"allow_repeat entry_id key kind={kind} undo_edit")] * 2
    for text in (f"{WORDS[kind]} edit undone.", "Undo", "Redo"):
        assert tr.check(text, tr.PAGE) == []


def test_undo_and_redo_each_run_once_whether_clicked_twice_or_sent_from_a_page_drawn_again(client):
    uid = web_person(client)
    entry = make("bodyweight", uid)
    address = f"/entries/bodyweight/{entry}/edit"
    saved = post(client, address, kg="82", date=day())
    _, action, _, fields = strip_form(client, saved.headers["location"])
    first, second = post(client, action, **fields), post(client, action, **fields)  # a double click: one key
    assert second.headers["location"] == first.headers["location"] and stored("bodyweight", entry)["kg"] == 80
    _, _, _, drawn_again = strip_form(client, saved.headers["location"])  # the saved page again: a new key
    third = post(client, action, **drawn_again)
    assert third.headers["location"] == SITE + f"/day/{day()}?saved=unchanged"
    assert stored("bodyweight", entry)["kg"] == 80
    # Redo, then Redo from its page drawn again, as Back shows it: the edit comes back once, and stays.
    _, _, button, redo = strip_form(client, first.headers["location"])
    assert button == "Redo" and post(client, action, **redo).headers["location"] == saved.headers["location"]
    _, _, _, redo_again = strip_form(client, first.headers["location"])  # the undone page again: a new key
    fourth = post(client, action, **redo_again)
    assert redo_again["key"] != redo["key"] and fourth.headers["location"] == SITE + f"/day/{day()}?saved=unchanged"
    assert stored("bodyweight", entry)["kg"] == 82
    assert [r.outcome for r in rows() if r.function == "restore_entry"] == [
        "written", "repeat", "duplicate", "written", "duplicate"]
    assert [v.why for v in versions()] == ["edit", "undo", "redo"]


def test_the_page_lands_by_what_happened_and_not_by_the_button_that_was_pressed(client):
    uid = web_person(client)
    entry = make("bodyweight", uid)
    saved = post(client, f"/entries/bodyweight/{entry}/edit", kg="82", date=day())
    _, action, _, fields = strip_form(client, saved.headers["location"])
    undone = post(client, action, **fields)
    _, _, _, old_redo = strip_form(client, undone.headers["location"])  # the Redo page, left open
    # Undo from the saved page drawn again, 11 minutes after the undo: by the table the edit comes back. The form
    # asked for an undo and the result says redone, so the page says saved and offers Undo.
    age(EntryVersion, versions()[-1].id, minutes=11)
    _, _, button, old_undo = strip_form(client, saved.headers["location"])
    late = post(client, action, **old_undo)
    assert button == "Undo" and set(old_undo) == {"undo_edit", "key"}
    assert late.headers["location"] == SITE + f"/day/{day()}?saved=bodyweight:{entry}"
    assert stored("bodyweight", entry)["kg"] == 82 and versions()[-1].why == "redo"
    # Redo from the old page after a newer edit: no edit is undone, so nothing changes and the newer edit stays.
    assert services.edit_entry(uid, "bodyweight", entry, {"kg": 85}, "claude")["changed"] == ["kg"]
    stale = post(client, action, **old_redo)
    assert set(old_redo) == {"undo_edit", "allow_repeat", "key"}
    assert stale.headers["location"] == SITE + f"/day/{day()}?saved=unchanged" and rows()[-1].outcome == "duplicate"
    assert stored("bodyweight", entry)["kg"] == 85 and [v.why for v in versions()] == ["edit", "undo", "redo", "edit"]


def test_undo_and_redo_onto_a_day_that_holds_another_entry_say_so_and_change_nothing(client):
    uid = web_person(client)
    moved = services.log_bodyweight(uid, {"kg": 80, "date": day(-3)}, "web")["id"]
    address = f"/entries/bodyweight/{moved}/edit"
    saved = post(client, address, kg="80", date=day(-2), updated_at=drawn_stamp(client.get(address).text))
    assert saved.headers["location"] == SITE + f"/day/{day(-2)}?saved=bodyweight:{moved}"
    there = services.log_bodyweight(uid, {"kg": 70, "date": day(-3)}, "web")["id"]  # the day it left is taken now

    def refused(location: str, pressed: str, other: int) -> None:
        _, action, button, fields = strip_form(client, location)
        held = stored("bodyweight", moved, but=()), stored("bodyweight", other, but=()), len(versions())
        r = post(client, action, **fields)
        assert button == pressed and r.status_code == 409 and '<h1 class="title">Nothing changed</h1>' in r.text
        for said in ("That would move the entry onto a day that has another one, so nothing was changed.",
                     "Edit or delete the other one first, then try again."):
            assert said in r.text and tr.check(said, tr.PAGE) == []
        assert (stored("bodyweight", moved, but=()), stored("bodyweight", other, but=()), len(versions())) == held
        assert held[1]["deleted_at"] is None  # the other one is still live
        row = rows()[-1]
        assert (row.function, row.caller, row.error_code, row.outcome, row.step) == (
            "restore_entry", "web", "day_taken", "refused", "check")
        findings = error_page_findings(Rendered(action, r, True, user_id=uid))
        assert not findings, "\n".join(findings)

    refused(saved.headers["location"], "Undo", there)
    # The other one out of the way, the undo runs. Then the day the undo left is taken in its turn, and Redo is refused.
    assert services.delete_entry(uid, "bodyweight", there)["deleted"] is True
    _, action, _, fields = strip_form(client, saved.headers["location"])
    undone = post(client, action, **fields)
    assert undone.headers["location"] == SITE + f"/day/{day(-3)}?restored=bodyweight:{moved}"
    other = services.log_bodyweight(uid, {"kg": 60, "date": day(-2)}, "web")["id"]
    refused(undone.headers["location"], "Redo", other)
    assert [v.why for v in versions()] == ["edit", "undo"]


def test_an_edit_that_saves_nothing_new_and_a_log_offer_no_undo(client):
    uid = web_person(client)
    entry = make("bodyweight", uid)
    same = post(client, f"/entries/bodyweight/{entry}/edit", kg="80.004", date=day())  # stored as 80: no change
    assert same.headers["location"] == SITE + f"/day/{day()}?saved=bodyweight" and versions() == []
    logged = post(client, f"/day/{day()}/measurement", kind="waist", value="84")
    assert logged.headers["location"] == SITE + f"/day/{day()}?saved=measurement#top"
    for address in (same.headers["location"], logged.headers["location"]):
        assert '<div class="ok undo">' not in client.get(address.removeprefix(SITE)).text


@pytest.mark.parametrize("query", ["saved=goal:1", "saved=bodyweight:abc", "saved=bodyweight:0", "saved=bodyweight:007",
                                   "restored=saved_food:1", "restored=bodyweight:", "saved=<b>:1", "restored=x:1"])
def test_a_forged_mark_shows_no_undo_and_no_way_back(client, query):
    web_person(client)
    page = client.get(f"/day/{day()}", params=dict([query.split("=", 1)])).text
    assert 'class="ok' not in page and "/restore" not in page and "<b>:1" not in page


def test_undo_from_a_page_for_an_entry_with_nothing_kept_says_so_on_its_own_page(client):
    uid = web_person(client)
    entry, roti = make("bodyweight", uid), make("saved_food", uid)
    for address, said, where in ((f"/entries/bodyweight/{entry}/restore", "That entry has no earlier values kept, "
                                  "so nothing was changed.", "Go back to the day to see it."),
                                 (f"/foods/{roti}/restore", "That food has no earlier values kept, so nothing was "
                                  "changed.", "Go back to Foods to see it.")):
        r = post(client, address, undo_edit="1")
        assert r.status_code == 409 and '<h1 class="title">Nothing to undo</h1>' in r.text
        assert said in r.text and where in r.text and rows()[-1].error_code == "nothing_to_undo"
        findings = error_page_findings(Rendered(address, r, True, user_id=uid))
        assert not findings, "\n".join(findings)
    assert post(client, f"/entries/bodyweight/{10 ** 9}/restore", undo_edit="1").status_code == 404


def test_after_a_food_s_edit_foods_offers_undo_and_after_the_undo_the_way_back(client):
    uid = web_person(client)
    roti = make("saved_food", uid)
    first = stored("saved_food", roti)
    address = f"/foods/{roti}/edit"
    saved = post(client, address, serving_label="2 rotis", kcal_per_serving="250", grams_per_serving="",
                 updated_at=drawn_stamp(client.get(address).text))
    assert saved.status_code == 303 and saved.headers["location"] == SITE + f"/foods?saved=saved_food:{roti}"
    edited = stored("saved_food", roti)
    words, action, button, fields = strip_form(client, saved.headers["location"])
    assert (words, action, button) == ("Saved food saved.", f"/foods/{roti}/restore", "Undo")
    assert {k: v for k, v in fields.items() if k != "key"} == {"undo_edit": "1"}
    undone = post(client, action, **fields)
    assert undone.headers["location"] == SITE + f"/foods?restored=saved_food:{roti}"
    assert stored("saved_food", roti) == first
    words, back, button, again = strip_form(client, undone.headers["location"])
    assert (words, back, button) == ("Saved food edit undone.", action, "Redo")
    assert {k: v for k, v in again.items() if k != "key"} == {"undo_edit": "1", "allow_repeat": "1"}
    redone = post(client, back, **again)
    assert redone.headers["location"] == saved.headers["location"] and stored("saved_food", roti) == edited
    assert [(v.kind, v.by, v.why) for v in versions()] == [
        ("saved_food", "web", why) for why in ("edit", "undo", "redo")]
    assert rows()[-1].tried == "allow_repeat entry_id key kind=saved_food undo_edit"
    _, action, button, fields = strip_form(client, redone.headers["location"])  # after Redo, Undo again
    assert button == "Undo" and post(client, action, **fields).headers["location"] == undone.headers["location"]
    assert stored("saved_food", roti) == first
    assert tr.check("Saved food edit undone.", tr.PAGE) == []


def test_saving_a_name_already_there_offers_undo_and_a_new_food_does_not(client):
    uid = web_person(client)
    new = post(client, "/foods", name="Roti", serving_label="1 roti", kcal_per_serving="120")
    assert new.headers["location"] == SITE + "/foods?saved=saved_food#top" and versions() == []
    with db.new_session() as s:
        [roti] = s.scalars(select(SavedFood.id).where(SavedFood.user_id == uid)).all()
    over = post(client, "/foods", name="Roti", serving_label="1 roti", kcal_per_serving="210")
    assert over.headers["location"] == SITE + f"/foods?saved=saved_food:{roti}#top"
    _, action, button, fields = strip_form(client, over.headers["location"].removesuffix("#top"))
    assert (action, button) == (f"/foods/{roti}/restore", "Undo")
    assert post(client, action, **fields).status_code == 303 and stored("saved_food", roti)["kcal_per_serving"] == 120


# ---------------------------------------------------------------- decision 11: the export

def table(text: str) -> list[dict]:
    assert text.startswith(export.BOM)
    return list(csv.DictReader(io.StringIO(text.removeprefix(export.BOM))))


def test_the_export_has_a_file_of_the_versions_with_the_values_as_json_and_no_hidden_column(make_user):
    uid = make_user().id
    made = {kind: make(kind, uid) for kind in ("lift_session", "saved_food", "cardio_entry", "food_entry")}
    session, roti, run, dal = made.values()
    first = {kind: stored(kind, row, but=()) for kind, row in made.items()}
    edit("lift_session", uid, session)
    edit("saved_food", uid, roti)
    undo(uid, "saved_food", roti)
    undo(uid, "saved_food", roti, allow_repeat=True)
    edit("cardio_entry", uid, run)
    edit("food_entry", uid, dal)
    exported = services.export_data(uid)
    assert list(exported["files"])[-2:] == ["entry_versions.csv", "README.txt"]
    assert exported["rows"]["entry_versions.csv"] == 6
    kept = table(exported["files"]["entry_versions.csv"])
    assert [list(row) for row in kept] == [["kind", "entry_id", "created_at", "by", "why", "before"]] * 6
    assert [(row["kind"], row["entry_id"], row["by"], row["why"]) for row in kept] == [
        ("lift_session", str(session), "claude", "edit"), ("saved_food", str(roti), "claude", "edit"),
        ("saved_food", str(roti), "claude", "undo"), ("saved_food", str(roti), "claude", "redo"),
        ("cardio_entry", str(run), "claude", "edit"), ("food_entry", str(dal), "claude", "edit")]
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00", row["created_at"]) for row in kept)
    values = [json.loads(row["before"]) for row in kept]
    assert not any({"user_id", "dedupe_key", "name_key", "id"} & set(v) for v in values)
    was = first["lift_session"]
    # Names, not ids, for what the shared lists hold: a set as lift_sets.csv gives it, the session's id aside.
    assert values[0] == {"date": was["date"].isoformat(), "notes": "Heavy", "logged_by": "claude",
                         "updated_at": was["updated_at"].isoformat(), "sets": [
                             {"exercise": "Back Squat", "set_number": 1, "weight_kg": 100.0, "reps": 5},
                             {"exercise": "Bench Press", "set_number": 1, "weight_kg": 60.0, "reps": 8},
                             {"exercise": "Back Squat", "set_number": 2, "weight_kg": 105.0, "reps": 3}]}
    assert list(values[0]["sets"][0]) == list(export.SET_COLUMNS[1:])
    # A cardio entry's activity by name, under the name cardio_entries.csv gives its column, where the id was.
    ran, columns = first["cardio_entry"], [c.name for c in CardioEntry.__table__.columns]
    assert list(values[4]) == ["exercise" if name == "exercise_id" else name for name in columns
                               if name not in NOT_KEPT | {"dedupe_key"}]
    assert (values[4]["exercise"], values[4]["minutes"], values[4]["estimated_kcal"]) == (
        "Outdoor Run", ran["minutes"], ran["estimated_kcal"])
    assert "exercise" in table(exported["files"]["cardio_entries.csv"])[0]
    # A food entry's saved food stays an id: saved_foods.csv carries the id it names.
    assert values[5]["saved_food_id"] == roti and table(exported["files"]["saved_foods.csv"])[0]["id"] == str(roti)
    # The row itself holds what was stored: the ids.
    assert json.loads(versions()[0].before)["sets"] == [list(x) for x in was["sets"]]
    assert json.loads(versions()[4].before)["exercise_id"] == ran["exercise_id"]
    assert values[1] == {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120, "grams_per_serving": 40,
                         "updated_at": first["saved_food"]["updated_at"].isoformat()}
    assert values[2]["name"] == "roti" and values[2]["kcal_per_serving"] == 250
    assert json.loads(versions()[0].before)["dedupe_key"] == was["dedupe_key"]  # kept in the row, left out of the file
    readme = " ".join(exported["files"]["README.txt"].split())
    assert ("entry_versions.csv what an entry held before each edit, undo or redo: kind and entry_id name the entry, "
            "by says who changed it (web or claude), why is edit, undo or redo, and before holds the values as JSON: "
            "a set's exercise and a cardio activity by name, a saved food by its id in saved_foods.csv. The newest "
            "10 are kept for an entry, for 90 days") in readme


def test_the_export_writes_a_person_s_own_letters_in_the_json_and_not_as_escapes(make_user):
    uid = make_user().id
    note = "=1+1 caf\u00e9, 5 \u00d7 5"  # an e with an accent and a multiplication sign, as a person types them
    session = services.log_lifts(
        uid, {"notes": note, "sets": [{"exercise": "Deadlift", "weight_kg": 100, "reps": 5}]}, "claude")["id"]
    assert services.edit_entry(uid, "lift_session", session, {"notes": "Light"}, "claude")["changed"] == ["notes"]
    text = services.export_data(uid)["files"]["entry_versions.csv"]
    assert "caf\u00e9, 5 \u00d7 5" in text and "\\u00" not in text
    assert json.loads(table(text)[0]["before"])["notes"] == note


def test_the_export_writes_null_for_an_exercise_id_that_names_no_exercise_and_does_not_stop(make_user):
    uid = make_user().id
    session, run = make("lift_session", uid), make("cardio_entry", uid)
    with db.session_scope() as s:
        s.add(EntryVersion(user_id=uid, kind="lift_session", entry_id=session, by="web", why="edit",
                           before=json.dumps({"notes": None, "sets": [[10 ** 6, 1, 50.0, 5]]})))
        s.add(EntryVersion(user_id=uid, kind="cardio_entry", entry_id=run, by="web", why="edit",
                           before=json.dumps({"exercise_id": 10 ** 6, "minutes": 20.0})))
    kept = table(services.export_data(uid)["files"]["entry_versions.csv"])
    assert [json.loads(row["before"]) for row in kept] == [
        {"notes": None, "sets": [{"exercise": None, "set_number": 1, "weight_kg": 50.0, "reps": 5}]},
        {"exercise": None, "minutes": 20.0}]


# ---------------------------------------------------------------- one person never reaches another's

@pytest.fixture
def two(make_user):
    """A and B, and for B one entry of each kind, edited: (A's id, B's id, {kind: B's entry})."""
    a, b = make_user().id, make_user().id
    theirs = {}
    # The cardio entry first: it logs weigh-ins of its own, and B's weigh-in, made after them, takes today from them.
    # So the undo of its edit, which moves it back onto today, finds the day free.
    for kind in sorted(KINDS, key=lambda kind: kind != "cardio_entry"):
        theirs[kind] = make(kind, b)
        assert "error" not in edit(kind, b, theirs[kind])
    return a, b, theirs


@pytest.mark.parametrize("kind", KINDS)
def test_one_person_can_never_undo_another_s_edit(two, kind):
    a, b, theirs = two
    before, kept = stored(kind, theirs[kind], but=()), [(v.id, v.before) for v in versions()]
    for more in ({}, {"allow_repeat": True}):
        refused = undo(a, kind, theirs[kind], **more)
        assert refused == undo(a, kind, 10 ** 9, **more) == common.not_found(kind)  # word for word a missing id
    assert stored(kind, theirs[kind], but=()) == before and [(v.id, v.before) for v in versions()] == kept
    assert undo(b, kind, theirs[kind])["undone"] is True  # the owner can


def test_one_person_s_version_is_never_read_for_another_person_s_entry(make_user):
    """Every read of entry_version names the person: a row that names this entry under another account is not there."""
    a, b = make_user().id, make_user().id
    entry = make("bodyweight", b)
    with db.session_scope() as s:
        s.add(EntryVersion(user_id=a, kind="bodyweight", entry_id=entry, by="web", why="edit",
                           before=json.dumps({"kg": 20.0})))
    assert undo(b, "bodyweight", entry) == NOTHING and stored("bodyweight", entry)["kg"] == 80
    assert services.edit_entry(b, "bodyweight", entry, {"kg": 82}, "claude")["changed"] == ["kg"]
    assert undo(b, "bodyweight", entry)["entry"]["kg"] == 80  # B's own version, never A's 20 kg
    assert [(v.user_id, json.loads(v.before)["kg"]) for v in versions(a)] == [(a, 20.0)]
    with db.new_session() as s:
        row = s.get(Bodyweight, entry)
        assert common.newest_version(s, row).user_id == b


def test_one_person_can_never_export_another_s_versions(two):
    a, b, theirs = two
    mine = services.export_data(a)
    assert mine["rows"]["entry_versions.csv"] == 0 and table(mine["files"]["entry_versions.csv"]) == []
    assert "Paratha" not in json.dumps(mine["files"]) and "Roti" not in json.dumps(mine["files"])
    assert services.export_data(b)["rows"]["entry_versions.csv"] == len(KINDS) == len(versions(b))


def test_deleting_the_account_deletes_its_versions_and_no_one_else_s(two):
    a, b, theirs = two
    kept = make("bodyweight", a)
    services.edit_entry(a, "bodyweight", kept, {"kg": 82}, "claude")
    assert services.delete_account(b)["deleted"] is True
    assert [(v.user_id, v.kind, v.entry_id) for v in versions()] == [(a, "bodyweight", kept)]


# ---------------------------------------------------------------- decision 1: the table

def test_0007_adds_the_version_table_the_models_match_and_it_comes_down_again():
    address = f"sqlite:///{(Path(tempfile.mkdtemp(dir=TEST_DIR)) / 'versions.db').as_posix()}"
    cfg = alembic_config(address)
    command.upgrade(cfg, "0006")
    engine = make_engine(address)
    now = "2026-11-10 08:00:00.000000"
    row = ('INSERT INTO entry_version (user_id, kind, entry_id, created_at, "by", why, before) '
           "VALUES (?, 'bodyweight', ?, ?, 'web', 'edit', '{}')")
    try:
        with engine.begin() as c:
            for user in (1, 2):
                c.exec_driver_sql("INSERT INTO user (id, provider, subject, claude_can, add_back_exercise_kcal, "
                                  "created_at, updated_at, last_seen_at) VALUES (?, 'workos', ?, 'log', 0, ?, ?, ?)",
                                  (user, f"s{user}", now, now, now))
            c.exec_driver_sql("INSERT INTO write_key (user_id, key, function, fingerprint, created_at) "
                              "VALUES (1, 'one-key-01', 'log_lifts', 'f', ?)", (now,))
            assert "entry_version" not in inspect(c).get_table_names()
        command.upgrade(cfg, "0007")
        with engine.connect() as c:
            found = {col["name"]: col for col in inspect(c).get_columns("entry_version")}
            assert list(found) == ["id", "user_id", "kind", "entry_id", "created_at", "by", "why", "request_id",
                                   "before"]
            assert [name for name, col in found.items() if col["nullable"]] == ["request_id"]
            assert [(found[n]["type"].length) for n in ("kind", "by", "why", "request_id")] == [16, 8, 8, 16]
            [index] = inspect(c).get_indexes("entry_version")
            assert (index["name"], index["column_names"], bool(index["unique"])) == (
                "ix_entry_version_user_id_kind_entry_id_id", ["user_id", "kind", "entry_id", "id"], False)
            [owner] = inspect(c).get_foreign_keys("entry_version")  # the person only: entry_id names one of six tables
            assert (owner["constrained_columns"], owner["referred_table"], owner["options"]) == (
                ["user_id"], "user", {"ondelete": "CASCADE"})
            assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []  # the models match
        with engine.begin() as c:
            for user, entry in ((1, 5), (1, 5), (2, 5), (2, 999999)):  # many for one entry, and an id no table holds
                c.exec_driver_sql(row, (user, entry, now))
        with engine.begin() as c:
            c.exec_driver_sql("DELETE FROM user WHERE id = 1")  # the rows go with the account
            left = c.exec_driver_sql("SELECT user_id, entry_id FROM entry_version ORDER BY id").all()
            assert [tuple(r) for r in left] == [(2, 5), (2, 999999)]
        command.downgrade(cfg, "0006")
        with engine.connect() as c:
            assert "entry_version" not in inspect(c).get_table_names()
            assert c.exec_driver_sql("SELECT count(*) FROM write_key").scalar() == 0  # user 1's, gone with user 1
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0006"
    finally:
        engine.dispose()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("0007").down_revision == "0006" and script.get_current_head() == "0007"


def test_every_kind_s_table_name_fits_the_version_s_kind_column():
    """entry_version.kind holds the entry's table name. A later table with a longer name would fail on Postgres
    only, at its first edit: SQLite does not hold a text column to its length."""
    width = EntryVersion.__table__.c.kind.type.length
    names = [model.__tablename__ for model in entries._deletable().values()]
    assert width == 16 and set(MODELS) == set(names) == set(entries._deletable())
    assert all(len(name) <= width for name in names), names


# ---------------------------------------------------------------- decisions 13 and 15: what is said

def test_can_and_cant_says_an_edit_is_kept_how_to_undo_it_and_the_10_minute_rule():
    text = " ".join(mcp_server.CAN_AND_CANT.read_text(encoding="utf-8").split())
    assert ("An edit keeps the values from before it: `restore_entry` with `undo_edit` brings them back. The newest "
            "10 are kept per entry, for 90 days. The same undo again within 10 minutes changes nothing and comes back "
            "with `duplicate` true. `allow_repeat` true brings back an edit that was undone and never undoes one: "
            "send it only when the person wants the edit back. An undo is itself an edit, so `undo_edit` again over "
            "10 minutes later brings the edit back, and the result says `redone`. A saved food that `save_food` "
            "changed is undone the same way, with kind `saved_food`.") in text
    assert "| `restore_entry` | Brings back a deleted or replaced entry, or undoes an entry's last edit. |" in text
    assert "keeps no old values" not in text and "cannot be undone, only edited again" not in text
    assert (common.VERSIONS_KEPT, common.VERSION_DAYS, common.DUPLICATE_MINUTES) == (10, 90, 10)


def test_the_privacy_page_says_what_an_edit_keeps_for_how_long_and_that_it_is_exported_and_deleted():
    page = (Path(web.HERE) / "templates" / "privacy.html").read_text("utf-8")
    cards = re.findall(r'<section class="card".*?</section>', page, re.S)
    [card] = [c for c in cards if ">What an edit replaces</h2>" in c]
    for said in ("When an entry or a saved food is edited, Spotter keeps the values it held before, so the edit can be "
                 "undone.", "It keeps the newest 10 for each entry, for 90 days.",
                 "They are in your export, and deleting your account deletes them."):
        assert f"<p>{said}</p>" in card and tr.check(said, tr.PAGE) == []
    assert "Claude" not in card and '<time datetime="2026-11-14">Sat 14 Nov 2026</time>' in page
    # The Export card says what is not in the export, and that is still so.
    [exports] = [c for c in cards if ">Export</h2>" in c]
    assert "The log of calls is not in it, and the answers kept for a day are not in it." in exports
    assert export.ORDER[-1] == "entry_versions.csv"
