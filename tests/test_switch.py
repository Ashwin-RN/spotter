"""The switch (contract switch): two settings, deleting on for every account, and a delete through an assistant that
takes two calls, the first of which deletes nothing.

Calls go through the real HTTP path (TestClient), as in tests/test_idempotency.py and tests/test_feedback.py, so the
wrapper, the call log and a write's key are the ones a real call has. A row's last change is moved by hand (older),
never by waiting: this machine's clock steps 1 to 2 ms.
"""

import hashlib
import importlib.util
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
from sqlalchemy import event, select, update
from sqlalchemy.dialects import postgresql

from app import calllog, db, links, mcp_server, models, services, web
from app.config import ROOT
from app.db import make_engine
from app.models import CLAUDE_CAN, Base, CallLog, LiftSession, SavedFood, User
from app.services import entries

from . import text_rules as tr
from .conftest import TEST_DIR, alembic_config, lock_sql, sign
from .test_calllog import MARKER, rows
from .test_feedback import PUBLISHED, compact
from .test_idempotency import K, keys, writer  # noqa: F401 - writer is a fixture
from .test_mcp import CAN_AND_CANT, call, failed, model_texts, ok, one_user, set_switch, squat, tool_list
from .test_trace import OWNER, sign_for
from .test_web import client, location, only_user, post, seam, sign_in, web_client  # noqa: F401

KINDS = ["lift_session", "bodyweight", "measurement", "food_entry", "cardio_entry", "saved_food", "routine"]
UNDONE = [kind for kind in KINDS if kind != "routine"]  # a routine keeps no version, so no undo (routines.md 6)
PREVIEW_NOTE = ("Nothing was deleted. Show the person this entry and ask. Send the same kind and id with this confirm "
                "only when they say yes.")
MISMATCH_NOTE = ("That confirm does not match this entry as it stands, so nothing was deleted. Show the person the "
                 "entry and ask again.")
DELETED_NOTE = "Deleted. restore_entry with the same kind and id brings it back."
RULE = ("Delete only the entry the person named. When their words fit more than one entry, ask which first: never call "
        "delete_entry on a guess. The first delete_entry call deletes nothing and returns the entry: show it to the "
        "person and ask, and delete only when they say yes. After a delete, say what was deleted and that restore_entry "
        "brings it back. If the person did not want it deleted, restore it, and say they can turn deleting off on "
        "Settings.")
DESCRIPTION = (
    "Deletes one of the person's entries, saved foods or routines, in two calls. With no confirm, nothing is deleted: "
    "the answer is a preview, with dry_run true, the entry as it stands and a confirm code. Send the same kind and id with that "
    "confirm to delete. A confirm that does not match the entry as it stands deletes nothing and returns the preview "
    "again. Nothing is erased: restore_entry brings a deleted entry back. " + RULE + " Allowed only when the person "
    "lets Claude delete; otherwise the answer is not_allowed.")
CONFIRM_WORDS = ("The confirm code from this entry's preview. Leave it out on the first call. Send it only after the "
                 "person has seen the entry and said yes.")
CLAUDE_CAN_WORDS = ("What the person allows Claude to do. log_delete (the default): everything, delete_entry too. log: "
                    "everything except delete_entry. Only the person can change it; Claude cannot.")
# owner_feedback as published before this work, with the owner's delete split off it (feedback.md).
OWNER_FEEDBACK_BEFORE = "242b0d08f0ca2b4bb696ec818f327ede1d2b2b71b6959dba0e687cc9b628a5f7"


# ---------------------------------------------------------------- helpers

def model_of(kind: str):
    return entries._deletable()[kind]


def make(account: int, kind: str) -> int:
    """One entry of this kind, through its own service."""
    made = {
        "lift_session": lambda: services.log_lifts(account, {"sets": squat(100)}, "claude"),
        "bodyweight": lambda: services.log_bodyweight(account, {"kg": 80}, "claude"),
        "measurement": lambda: services.log_measurement(account, {"kind": "waist", "value": 84}, "claude"),
        "food_entry": lambda: services.log_food(account, {"entries": [{"description": "Dal", "kcal": 180}]}, "claude"),
        "cardio_entry": lambda: services.log_cardio(account, {"exercise": "Treadmill Walk", "minutes": 30}, "claude"),
        "saved_food": lambda: services.save_food(account, {"name": "Roti", "serving_label": "1 roti",
                                                           "kcal_per_serving": 120}, "claude"),
        "routine": lambda: services.save_routine(account, {"name": "Push day", "lifts": [
            {"exercise": "Bench Press", "sets": 3, "reps": 8}]}, "claude"),
    }[kind]()
    assert "error" not in made, made
    return made["ids"][0] if kind == "food_entry" else made["id"]


def edit(account: int, kind: str, entry_id: int) -> None:
    """One change of the entry, through the service that changes it: edit_entry, or save_food for a saved food."""
    if kind == "saved_food":
        done = services.save_food(account, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 130},
                                  "claude")
    elif kind == "routine":  # a routine is changed by saving it again (routines.md decision 5)
        done = services.save_routine(account, {"name": "Push day", "lifts": [
            {"exercise": "Bench Press", "sets": 4, "reps": 8}]}, "claude")
    else:
        changes = {"lift_session": {"notes": "Edited"}, "bodyweight": {"kg": 81}, "measurement": {"value": 85},
                   "food_entry": {"kcal": 200}, "cardio_entry": {"minutes": 31}}[kind]
        done = services.edit_entry(account, kind, entry_id, changes, "claude")
    assert "error" not in done, done


def older(kind: str, entry_id: int, **back) -> None:
    """Move the row's last change back by hand, so the next change gives it another updated_at."""
    model = model_of(kind)
    with db.session_scope() as s:
        s.execute(update(model).where(model.id == entry_id).values(
            updated_at=db.utcnow() - timedelta(**(back or {"hours": 1}))))


def stored(kind: str, entry_id: int) -> dict:
    """Every stored column of the row, and a session's sets."""
    model = model_of(kind)
    with db.session_scope() as s:
        row = s.get(model, entry_id)
        out = {c.name: getattr(row, c.key) for c in model.__table__.columns}
        if isinstance(row, LiftSession):
            out["sets"] = [(x.id, x.exercise_id, x.set_number, x.weight_kg, x.reps) for x in row.sets]
        return out


def code_of(account: int, kind: str, entry_id: int) -> str:
    """The code a preview gives for the entry as it stands, from the service as an assistant calls it."""
    return services.delete_entry(account, kind, entry_id, "claude")["confirm"]


def preview(http, token, kind: str, entry_id: int, **sent) -> dict:
    return ok(http, token, "delete_entry", {"kind": kind, "entry_id": entry_id, **sent})


def live(kind: str, entry_id: int) -> bool:
    return stored(kind, entry_id)["deleted_at"] is None


# ---------------------------------------------------------------- decisions 7, 9 and 10: two calls, for each kind

@pytest.mark.parametrize("kind", KINDS)
def test_the_first_call_deletes_nothing_the_second_with_its_code_deletes_and_a_restore_brings_it_back(writer, kind):
    http, token, account = writer
    entry_id = make(account, kind)
    before = stored(kind, entry_id)
    first = preview(http, token, kind, entry_id)
    assert stored(kind, entry_id) == before  # not a column moved, updated_at least of all
    assert list(first) == ["kind", "id", "deleted", "dry_run", "entry", "confirm", "note"]
    assert (first["kind"], first["id"], first["deleted"], first["dry_run"], first["note"]) == (
        kind, entry_id, False, True, PREVIEW_NOTE)
    assert re.fullmatch(r"[0-9a-f]{12}", first["confirm"]) and first["entry"]["deleted_at"] is None
    assert first["entry"] == services.get_entry(account, kind, entry_id)["entry"]  # the entry as it stands
    second = preview(http, token, kind, entry_id, confirm=first["confirm"])
    assert list(second) == ["kind", "id", "deleted", "entry", "note", "dry_run"] and "confirm" not in second
    assert (second["deleted"], second["dry_run"], second["note"]) == (True, False, DELETED_NOTE)
    assert not live(kind, entry_id) and second["entry"]["deleted_at"] is not None
    assert ok(http, token, "restore_entry", {"kind": kind, "entry_id": entry_id})["restored"] is True
    assert live(kind, entry_id)


@pytest.mark.parametrize("kind", KINDS)
def test_a_preview_sets_nothing_and_writes_nothing_but_the_lock(writer, kind):
    """Through the service as an assistant calls it, every statement recorded: the only write is the lock, which on
    SQLite is an UPDATE that changes nothing. No version is kept, no row is flushed."""
    _, _, account = writer
    entry_id = make(account, kind)
    before = stored(kind, entry_id)
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_):
        statements.append(" ".join(statement.split()))

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = services.delete_entry(account, kind, entry_id, "claude")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert result["dry_run"] is True and stored(kind, entry_id) == before
    writes = [s for s in statements if s.split()[0] in ("INSERT", "UPDATE", "DELETE")]
    lock = lock_sql(account)
    assert statements.count(lock) == 1 and [s for s in writes if s != lock] == []


@pytest.mark.parametrize("kind", KINDS)
def test_the_code_holds_for_the_entry_unchanged_and_moves_with_an_edit_and_with_a_delete_then_a_restore(writer, kind):
    _, _, account = writer
    entry_id = make(account, kind)
    older(kind, entry_id)
    first = code_of(account, kind, entry_id)
    assert code_of(account, kind, entry_id) == first  # the same entry, unchanged: the same code
    edit(account, kind, entry_id)
    edited = code_of(account, kind, entry_id)
    assert edited != first
    older(kind, entry_id, hours=2)
    before_delete = code_of(account, kind, entry_id)
    assert before_delete not in (first, edited)
    assert services.delete_entry(account, kind, entry_id)["deleted"] is True
    assert services.restore_entry(account, kind, entry_id)["restored"] is True
    assert code_of(account, kind, entry_id) not in (first, before_delete)


@pytest.mark.parametrize("kind", UNDONE)
def test_an_undo_of_an_edit_moves_the_code_too(make_user, kind):
    account = make_user().id
    entry_id = make(account, kind)
    edit(account, kind, entry_id)
    older(kind, entry_id)
    before = code_of(account, kind, entry_id)
    assert services.restore_entry(account, kind, entry_id, "claude", undo_edit=True)["undone"] is True
    assert code_of(account, kind, entry_id) != before


def test_every_other_write_that_changes_an_entry_moves_its_code(make_user):
    """A weigh-in or a measurement replaced by another on its day, the live one a restore sets aside, a food entry's
    estimate confirmed as the confirm page sends it, and a deleted saved food that save_food brings back."""
    account = make_user().id
    assert "error" not in services.update_settings(account, {"time_zone": "UTC"}, "web")
    weigh_in, waist = make(account, "bodyweight"), make(account, "measurement")
    older("bodyweight", weigh_in)
    older("measurement", waist)
    codes = {"bodyweight": code_of(account, "bodyweight", weigh_in), "measurement": code_of(account, "measurement",
                                                                                            waist)}
    assert services.log_bodyweight(account, {"kg": 82}, "claude")["replaced"]
    assert services.log_measurement(account, {"kind": "waist", "value": 86}, "claude")["replaced"]
    with db.session_scope() as s:
        assert entries.confirm_code(account, "bodyweight", s.get(models.Bodyweight, weigh_in)) != codes["bodyweight"]
        assert entries.confirm_code(account, "measurement", s.get(models.Measurement, waist)) != codes["measurement"]
    live_one = services.get_log(account)["days"][0]["weigh_in"]["id"]
    older("bodyweight", live_one)
    before = code_of(account, "bodyweight", live_one)
    assert services.restore_entry(account, "bodyweight", weigh_in)["replaced"][0]["id"] == live_one  # set aside
    with db.session_scope() as s:
        assert entries.confirm_code(account, "bodyweight", s.get(models.Bodyweight, live_one)) != before
    estimate = services.log_food(account, {"entries": [{"description": "Dal", "kcal": 180, "estimated": True}]},
                                 "claude")["ids"][0]
    older("food_entry", estimate)
    before = code_of(account, "food_entry", estimate)
    assert services.edit_entry(account, "food_entry", estimate, {"kcal": 180}, "web")["changed"] == ["kcal"]
    assert code_of(account, "food_entry", estimate) != before
    food = make(account, "saved_food")
    assert services.delete_entry(account, "saved_food", food)["deleted"] is True
    older("saved_food", food)
    with db.session_scope() as s:
        before = entries.confirm_code(account, "saved_food", s.get(SavedFood, food))
    make(account, "saved_food")  # the same name again: the deleted food comes back
    assert live("saved_food", food) and code_of(account, "saved_food", food) != before


def test_the_code_is_the_start_of_a_sha_256_of_the_account_the_kind_the_id_and_the_last_change(make_user):
    account = make_user().id
    entry_id = make(account, "bodyweight")
    at = stored("bodyweight", entry_id)["updated_at"]
    sha = hashlib.sha256(f"{account}|bodyweight|{entry_id}|{at.isoformat()}".encode()).hexdigest()
    assert code_of(account, "bodyweight", entry_id) == sha[:12]
    with db.session_scope() as s:
        row = s.get(models.Bodyweight, entry_id)
        assert entries.confirm_code(account + 1, "bodyweight", row) != sha[:12]  # another account: another code
        assert entries.confirm_code(account, "measurement", row) != sha[:12]  # another kind


def other_codes(account: int, kind: str, entry_id: int, make_user) -> dict[str, str]:
    """Codes that are not this entry's as it stands: made up, another entry's, another person's for the same kind and
    id, and one from before an edit."""
    other = make(account, "bodyweight" if kind != "bodyweight" else "measurement")
    with db.session_scope() as s:
        theirs = entries.confirm_code(make_user().id, kind, s.get(model_of(kind), entry_id))
    older(kind, entry_id)
    stale = code_of(account, kind, entry_id)
    edit(account, kind, entry_id)
    return {"made up": "0123456789ab", "another entry's": code_of(account, "bodyweight" if kind != "bodyweight"
                                                                   else "measurement", other),
            "another person's": theirs, "from before an edit": stale, "empty": "", "a marker": MARKER}


@pytest.mark.parametrize("kind", KINDS)
def test_a_confirm_that_does_not_match_deletes_nothing_and_answers_the_preview_with_the_second_note(writer, make_user,
                                                                                                    kind):
    http, token, account = writer
    entry_id = make(account, kind)
    for what, code in other_codes(account, kind, entry_id, make_user).items():
        before = stored(kind, entry_id)
        again = preview(http, token, kind, entry_id, confirm=code)
        assert (again["deleted"], again["dry_run"], again["note"]) == (False, True, MISMATCH_NOTE), what
        assert again["confirm"] == code_of(account, kind, entry_id) != code, what  # the code that does match
        assert stored(kind, entry_id) == before and rows()[-1].outcome == "preview", what


def test_another_person_s_entry_is_not_found_on_both_calls(writer, make_user):
    http, token, _ = writer
    theirs = make(make_user().id, "bodyweight")
    for sent in ({}, {"confirm": "0123456789ab"}):
        assert failed(http, token, "delete_entry", {"kind": "bodyweight", "entry_id": theirs, **sent})["error"] == \
            "not_found"
        assert (rows()[-1].outcome, rows()[-1].step) == ("refused", "check")
    assert live("bodyweight", theirs)


def test_an_unknown_kind_answers_as_before_on_both_calls(writer):
    http, token, account = writer
    for sent in ({}, {"confirm": "0123456789ab"}):
        assert failed(http, token, "delete_entry", {"kind": "goal", "entry_id": 1, **sent}) == \
            services.delete_entry(account, "goal", 1)


# ---------------------------------------------------------------- decision 12: the trace

def test_the_two_rows_read_preview_then_written_and_say_what_each_tried(writer):
    http, token, account = writer
    entry_id = make(account, "bodyweight")
    code = preview(http, token, "bodyweight", entry_id)["confirm"]
    preview(http, token, "bodyweight", entry_id, confirm=code)
    first, second = rows()[-2:]
    assert (first.function, first.outcome, first.ok, first.tried) == ("delete_entry", "preview", True,
                                                                      "entry_id kind=bodyweight")
    assert (second.function, second.outcome, second.ok, second.tried) == ("delete_entry", "written", True,
                                                                          "confirm entry_id kind=bodyweight")
    assert "confirm" in calllog.KNOWN_NAMES


def test_a_marker_sent_as_confirm_reaches_no_call_log_column_and_no_log_line(make_client, logs):
    client = make_client(auth_debug=True)
    token = sign()
    account = ok(client, token, "whoami")["account_id"]
    entry_id = make(account, "bodyweight")
    again = preview(client, token, "bodyweight", entry_id, confirm=MARKER)
    assert again["note"] == MISMATCH_NOTE and MARKER not in json.dumps(again)
    assert failed(client, token, "delete_entry", {"kind": "goal", "entry_id": 1, "confirm": MARKER})
    for row in rows():
        for column in CallLog.__table__.columns:
            assert MARKER not in str(getattr(row, column.key)), column.name
    assert "token verified" in logs.text  # debug logging was on
    assert MARKER not in logs.text and all(MARKER not in str(r.args) for r in logs.records)


# ---------------------------------------------------------------- decision 13: a write's key

def test_a_key_on_the_first_call_stays_free_and_the_second_sent_twice_deletes_once_with_repeat(writer):
    http, token, account = writer
    entry_id = make(account, "bodyweight")
    first = call(http, token, "delete_entry", {"kind": "bodyweight", "entry_id": entry_id, "key": K})
    assert first["structuredContent"]["dry_run"] is True and keys() == []  # a preview keeps no key
    assert call(http, token, "delete_entry", {"kind": "bodyweight", "entry_id": entry_id, "key": K}) == first
    assert [r.outcome for r in rows()[-2:]] == ["preview", "preview"]  # the same key, sent again, ran again
    sent = {"kind": "bodyweight", "entry_id": entry_id, "confirm": first["structuredContent"]["confirm"], "key": K}
    second = call(http, token, "delete_entry", sent)
    deleted_at = stored("bodyweight", entry_id)["deleted_at"]
    assert second["structuredContent"]["deleted"] is True and deleted_at is not None
    [kept] = keys()
    assert (kept.key, kept.function, kept.answer) == (K, "delete_entry", second["content"][0]["text"])
    assert call(http, token, "delete_entry", sent) == second  # the first answer, word for word
    assert stored("bodyweight", entry_id)["deleted_at"] == deleted_at  # deleted once
    assert [r.outcome for r in rows()[-2:]] == ["written", "repeat"]


# ---------------------------------------------------------------- decision 14: deleting turned off

def test_with_deleting_off_both_calls_are_not_allowed_before_the_service_and_the_entry_stays(writer, monkeypatch):
    http, token, account = writer
    entry_id = make(account, "bodyweight")
    sent = {"kind": "bodyweight", "entry_id": entry_id, "confirm": code_of(account, "bodyweight", entry_id),
            "key": K}
    kept = call(http, token, "delete_entry", sent)  # deleted, its key kept
    assert services.restore_entry(account, "bodyweight", entry_id)["restored"] is True
    set_switch(account, "log")
    monkeypatch.setattr(services, "delete_entry", lambda *a, **k: pytest.fail("the service ran"))
    for arguments in ({"kind": "bodyweight", "entry_id": entry_id}, {**sent, "key": "other-key-01"}, sent):
        refused = failed(http, token, "delete_entry", arguments)
        assert refused == {"error": "not_allowed", "message": "This account lets Claude read and log, not delete.",
                           "fix": "The person can change what Claude may do on the Settings page.",
                           "links": {"settings": links.page("settings")}}
        assert (rows()[-1].outcome, rows()[-1].step) == ("refused", "permission")
    assert live("bodyweight", entry_id) and kept["structuredContent"]["deleted"] is True  # not the kept answer
    assert mcp_server.NOT_ALLOWED_MESSAGES == {"log": "This account lets Claude read and log, not delete."}


# ---------------------------------------------------------------- decision 11: a person's own click

@pytest.mark.parametrize("kind", ["bodyweight", "saved_food", "routine"])  # and Routines' (routines.md 11)
def test_both_web_deletes_delete_in_one_post_and_land_with_undo(client, kind):
    sign_in(client)
    account = only_user().id
    assert "error" not in services.update_settings(account, {"time_zone": "UTC"}, "web")
    entry_id = make(account, kind)
    path = {"saved_food": f"/foods/{entry_id}", "routine": f"/routines/{entry_id}"}.get(
        kind, f"/entries/{kind}/{entry_id}")
    landed = location(post(client, path + "/delete"))
    assert not live(kind, entry_id) and f"deleted={kind}:{entry_id}" in landed
    page = client.get(landed).text
    assert '<button class="link">Undo</button>' in page
    location(post(client, path + "/restore"))
    assert live(kind, entry_id)


@pytest.mark.parametrize("kind", KINDS)
def test_the_service_called_as_the_routes_call_it_deletes_at_once_in_today_s_words(make_user, kind):
    account = make_user().id
    entry_id = make(account, kind)
    for by in (None, "web"):  # the routes send no by; anything but claude is the same
        result = services.delete_entry(account, kind, entry_id) if by is None else \
            services.delete_entry(account, kind, entry_id, by)
        assert list(result) == ["kind", "id", "deleted", "entry", "note"]
        assert (result["kind"], result["id"], result["deleted"], result["note"]) == (kind, entry_id, True, DELETED_NOTE)
        assert not live(kind, entry_id)
        assert services.restore_entry(account, kind, entry_id)["restored"] is True


# ---------------------------------------------------------------- decisions 1, 2, 4, 5 and 6: the two settings

def test_a_function_missing_from_needs_is_refused_at_log_and_let_through_at_log_delete():
    """What not_allowed's words say. The guard at both settings is the start check: tests/test_mcp.py's
    test_a_function_without_a_level_stops_the_server_at_start."""
    assert "not_a_function" not in mcp_server.NEEDS
    assert mcp_server.not_allowed("not_a_function", "log") == {
        "error": "not_allowed", "message": mcp_server.NOT_ALLOWED_MESSAGES["log"], "fix": mcp_server.NOT_ALLOWED_FIX,
        "links": {"settings": links.page("settings")}}
    assert mcp_server.not_allowed("not_a_function", "log_delete") is None


def test_the_two_settings_are_the_default_first_and_the_needs_keep_their_three_words():
    assert CLAUDE_CAN == ("log_delete", "log") and mcp_server.LEVELS == ("read", "log", "log_delete")
    assert User.__table__.c.claude_can.default.arg == "log_delete"
    assert User.__table__.c.claude_can.server_default.arg.text == "'log'"  # decision 2: the database's stays
    assert {f for f in mcp_server.NEEDS if mcp_server.not_allowed(f, "log")} == {"delete_entry"}
    assert {f for f in mcp_server.NEEDS if mcp_server.not_allowed(f, "log_delete")} == set()
    assert mcp_server.not_allowed("not_a_function", "log")["error"] == "not_allowed"  # missing from NEEDS
    assert set(mcp_server.READ_FUNCTIONS) == {f for f, need in mcp_server.NEEDS.items() if need == "read"}


@pytest.mark.parametrize("stored_value", ["read", "admin", "", "LOG"])
def test_an_unknown_stored_value_refuses_a_delete_and_lets_a_log_run(writer, stored_value):
    http, token, account = writer
    set_switch(account, stored_value)
    assert mcp_server.level_of(stored_value) == "log"
    assert ok(http, token, "log_bodyweight", {"kg": 80})["duplicate"] is False
    assert failed(http, token, "delete_entry", {"kind": "bodyweight", "entry_id": 1})["error"] == "not_allowed"


def test_a_new_account_starts_at_log_delete_by_a_first_call_through_mcp(http):
    who = ok(http, sign(), "whoami")
    assert who["claude_can"] == "log_delete" == one_user().claude_can


def test_a_new_account_starts_at_log_delete_by_the_web_s_sign_in(client):
    sign_in(client)
    assert only_user().claude_can == "log_delete"
    page = client.get("/settings").text
    radios = re.findall(r'<input type="radio" name="claude_can" value="([a-z_]+)"( checked)?', page)
    assert radios == [("log_delete", " checked"), ("log", "")]  # two, the default first, the stored one checked


def test_set_claude_can_takes_the_two_values_and_refuses_read(make_user):
    account = make_user().id
    assert services.set_claude_can(account, "log") == {"claude_can": "log", "changed": True}
    assert services.set_claude_can(account, "log_delete") == {"claude_can": "log_delete", "changed": True}
    refused = services.set_claude_can(account, "read")
    assert (refused["error"], refused["message"], refused["fix"]) == (
        "invalid_field", "claude_can 'read' is not one of log_delete, log.", "Use one of: log_delete, log.")
    with db.session_scope() as s:
        assert s.get(User, account).claude_can == "log_delete"


def test_a_post_of_read_is_refused_in_the_page_s_words_and_nothing_is_stored(client):
    sign_in(client)
    before = only_user().updated_at
    r = post(client, "/settings/claude-can", claude_can="read")
    assert r.status_code == 400 and "Choose one of the two settings below." in r.text
    assert "claude_can &#39;read&#39;" not in r.text and "Use one of" not in r.text
    assert only_user().claude_can == "log_delete" and only_user().updated_at == before


# ---------------------------------------------------------------- decision 3: migration 0009

def test_0009_turns_every_account_to_log_delete_moves_no_updated_at_and_comes_down_changing_nothing():
    address = f"sqlite:///{(Path(tempfile.mkdtemp(dir=TEST_DIR)) / 'switch.db').as_posix()}"
    cfg = alembic_config(address)
    command.upgrade(cfg, "0008")
    engine = make_engine(address)
    held = {1: "read", 2: "log", 3: "log_delete", 4: "admin"}
    stamps = {user: f"2026-11-0{user} 08:00:00.00000{user}" for user in held}
    try:
        with engine.begin() as c:
            for user, value in held.items():
                c.exec_driver_sql("INSERT INTO user (id, provider, subject, claude_can, add_back_exercise_kcal, "
                                  "created_at, updated_at, last_seen_at) VALUES (?, 'workos', ?, ?, 0, ?, ?, ?)",
                                  (user, f"s{user}", value, stamps[user], stamps[user], stamps[user]))
        command.upgrade(cfg, "0009")
        read = "SELECT id, claude_can, updated_at, created_at, last_seen_at FROM user ORDER BY id"
        with engine.connect() as c:
            after = [tuple(r) for r in c.exec_driver_sql(read).all()]
            assert after == [(u, "log_delete", stamps[u], stamps[u], stamps[u]) for u in held]
            # The models match, but for the table 0010 adds (tests/test_routines.py holds it there).
            assert [(d[0], d[1].name) for d in compare_metadata(MigrationContext.configure(c), Base.metadata)] == [
                ("add_table", "routine")]
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0009"
        command.downgrade(cfg, "0008")
        with engine.connect() as c:
            assert [tuple(r) for r in c.exec_driver_sql(read).all()] == after  # down changes no row
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0008"
    finally:
        engine.dispose()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("0009").down_revision == "0008" and script.get_current_head() == "0010"  # routines


def test_0009_s_statement_is_built_so_postgres_gets_user_quoted(monkeypatch):
    [path] = (ROOT / "migrations" / "versions").glob("0009_*.py")
    spec = importlib.util.spec_from_file_location("migration_0009", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sent = []
    monkeypatch.setattr(module, "op", type("Op", (), {"execute": staticmethod(sent.append)}))
    module.upgrade()
    [statement] = sent
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert sql == "UPDATE \"user\" SET claude_can='log_delete' WHERE \"user\".claude_can != 'log_delete'"
    module.downgrade()
    assert sent == [statement]  # the downgrade sends nothing


# ---------------------------------------------------------------- decisions 15 to 18 and 20: the words a model reads

def test_the_rule_is_in_the_instructions_and_the_description_and_the_published_words_are_the_contract_s(http):
    assert mcp_server.DELETE_RULE == RULE and "Deleting: " + RULE + " " in mcp_server.INSTRUCTIONS
    tool = {t["name"]: t for t in tool_list(http, sign())}["delete_entry"]
    assert tool["description"] == DESCRIPTION
    assert tool["annotations"]["destructiveHint"] is True  # decision 20
    assert list(tool["inputSchema"]["properties"]) == ["kind", "entry_id", "confirm", "key"]
    assert tool["inputSchema"]["required"] == ["kind", "entry_id"]
    assert tool["inputSchema"]["properties"]["confirm"]["description"] == CONFIRM_WORDS
    # Ruling round 1: kind and entry_id name a saved food too, and since routines.md a routine. edit_entry keeps the
    # shared words.
    assert tool["inputSchema"]["properties"]["kind"]["description"] == (
        "lift_session, bodyweight, measurement, food_entry or cardio_entry. Or saved_food or routine.")
    assert tool["inputSchema"]["properties"]["entry_id"]["description"] == mcp_server.RESTORE_ID_DESCRIPTION == (
        mcp_server.ENTRY_ID_DESCRIPTION + " A saved food's id comes from save_food or find_food, a routine's from "
                                          "save_routine or get_routines.")
    edit = {t["name"]: t for t in tool_list(http, sign())}["edit_entry"]["inputSchema"]["properties"]
    assert (edit["kind"]["description"], edit["entry_id"]["description"]) == (
        mcp_server.KIND_DESCRIPTION, mcp_server.ENTRY_ID_DESCRIPTION)
    for words in (tool["inputSchema"]["properties"]["kind"]["description"],
                  tool["inputSchema"]["properties"]["entry_id"]["description"]):
        assert tr.check(words, tr.CLAUDE) == [], words
    out = tool["outputSchema"]
    assert set(out["required"]) == {"kind", "id", "deleted", "dry_run", "entry"}
    words = {name: spec["description"] for name, spec in out["properties"].items()}
    assert words == {
        "kind": "The entry's kind.", "id": "The entry's id. restore_entry takes it, with the same kind.",
        "deleted": "True: the entry is deleted. False: a preview, and nothing was deleted. Nothing is ever erased.",
        "dry_run": "True: a preview, and nothing was deleted. False: the entry was deleted.",
        "entry": "The entry as it stands, with the fields of its kind.",
        "confirm": "On a preview: the code to send back as confirm, with the same kind and id, once the person has "
                   "said yes.",
        "note": "What to do next on a preview, or how to undo a delete."}
    who = {t["name"]: t for t in tool_list(http, sign())}["whoami"]
    assert who["outputSchema"]["properties"]["claude_can"]["description"] == CLAUDE_CAN_WORDS


def test_the_instructions_are_3927_characters_under_the_cap_and_still_end_with_the_plan_line_sentence():
    text = mcp_server.INSTRUCTIONS
    assert len(text) == 3927 < 4096
    assert text.endswith("Where a goal carries plan_line, say it as written, and do not also quote latest, "
                         "should_be_today or ahead_by.")


def test_the_instructions_name_settings_for_three_acts_only():
    named = [s for s in re.split(r"(?<=[.:;])\s+", mcp_server.INSTRUCTIONS) if "Settings" in s]
    assert named == ["If the person did not want it deleted, restore it, and say they can turn deleting off on "
                     "Settings.",  # turning deleting off: the third act (decision 16)
                     "Exporting the person's data and deleting their account are the person's own clicks on Settings:",
                     "say they are on Settings."]


def test_only_whoami_and_delete_entry_differ_in_the_published_list_and_owner_feedback_does_not(make_client):
    client = make_client(owner_user_id=OWNER)
    tools = {t["name"]: hashlib.sha256(compact(t).encode()).hexdigest() for t in tool_list(client, sign_for(OWNER))}
    moved = {name for name, sha in PUBLISHED.items() if tools[name] != sha}
    assert moved == set()  # PUBLISHED holds the two new hashes: tests/test_feedback.py
    assert tools["owner_feedback"] == OWNER_FEEDBACK_BEFORE


def test_every_new_text_keeps_the_text_rules():
    for text in (RULE, DESCRIPTION, CONFIRM_WORDS, CLAUDE_CAN_WORDS):
        assert tr.check(text, tr.CLAUDE) == [], text
    for text in (PREVIEW_NOTE, MISMATCH_NOTE):
        assert tr.check(text, tr.SUMMARY) == [], text
    for text in (*web.CLAUDE_CAN_LINES.values(), *web.CLAUDE_CAN_LABELS.values(), *web.SWITCH_ERROR_WORDS.values(),
                 web.SWITCH_ERROR_GENERAL):
        assert tr.check(text, tr.PAGE) == [], text
    assert entries.PREVIEW_NOTE == PREVIEW_NOTE and entries.MISMATCH_NOTE == MISMATCH_NOTE


# ---------------------------------------------------------------- decision 19: the pages and the docs

def test_read_only_is_a_label_on_no_page(client):
    sign_in(client)
    pages = {path: client.get(path).text for path in ("/", "/settings", "/privacy", "/hi")}
    for path, page in pages.items():
        assert "Read only" not in page and "read only" not in page, path
    words = " ".join(re.sub(r"<[^>]+>", "", pages["/"]).replace("&#34;", '"').split())
    assert 'A switch on Settings has two settings: "Read, log and delete" and "Read and log". It starts at ' \
           '"Read, log and delete".' in words


def test_no_text_says_three_settings_or_names_read_only_as_a_setting(http):
    sources = [ROOT / "README.md", CAN_AND_CANT, ROOT / "docs" / "evals.md", Path(web.HERE) / "web.py",
               ROOT / "evals" / "judge.py", *(Path(web.HERE) / "templates").glob("*.html")]
    texts = {str(p): p.read_text(encoding="utf-8") for p in sources}
    texts.update(model_texts(http, sign()))
    for where, text in texts.items():
        flat = " ".join(text.split())
        for words in ("three settings", "one of the three settings", "one of the three and", "Read only", "set it to read only", "set to read only",
                      "read: read only", "(read, log", "`read`, `log`"):
            assert words not in flat, (where, words)


def test_can_and_cant_and_the_readme_say_the_two_calls_and_the_two_settings():
    text = " ".join(CAN_AND_CANT.read_text(encoding="utf-8").split())
    for said in ("A delete takes two calls. `delete_entry` with no `confirm` deletes nothing: it answers with "
                 "`dry_run` true, the entry as it stands and a `confirm` code.",
                 "When a person did not want an entry deleted, Claude restores it and says deleting can be turned off "
                 "there.",
                 "| `delete_entry` | Deletes an entry in two calls: a preview, then the delete once the person says "
                 "yes. |",
                 "| `log_delete` (the default) | Everything, `delete_entry` too. |",
                 "With `log`, `delete_entry` answers `not_allowed`, and nothing is deleted.",
                 "It is a write, allowed at both settings."):
        assert said in text, said
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert ("**You set the limit.** The assistant can read, log and delete. One switch on Settings turns deleting "
            "off. A delete takes two steps: the assistant is first shown the entry and told to ask you.") in readme
    assert "35 cases, 3 runs each." in readme  # gym logging, first day and routines: was 26
