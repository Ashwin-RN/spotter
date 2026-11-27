"""Account status (contract owner-accounts.md, package P2): suspend and lift, the MCP gate, the web gate, the owner's
form, the record, and migration 0017.

The clock is fixed where a time is checked (db.utcnow). MCP calls sign their own tokens; the web signs in through the
fake provider seam of tests/test_web.py, whose person is user_01WEB: the owner when OWNER_USER_ID names it.
"""

import io
import re
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, select
from sqlalchemy import text as sql_text

from app import calllog, config, db, mcp_server, services, web, web_auth
from app.db import make_engine
from app.models import Base, CallLog, OwnerAction, User, user_tables
from app.services import export, owner

from .conftest import TEST_DIR, alembic_config, lock_sql, settings
from .test_hardening_schema import _master
from .test_inputs import bases, form_seeds, seeded, tool_call
from .test_mcp import registered_tools, set_switch
from .test_routines import statements_of
from .test_trace import sign_for
from .test_web import post, seam, session_of, sign_in, today_of, web_client  # noqa: F401 - fixtures

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
WEB_PERSON = "user_01WEB"  # the fake web seam's subject
OWNER = "user_01OWNERX"  # the owner's subject in these tests, unless a test makes the web person the owner
PERSON = "user_01PERSON"
# The two sentences, typed here from the contract, not read from the code.
MESSAGE = "This account is suspended."
FIX = ("Nothing to retry. Tell the person their account is suspended, and that they can still export or delete their "
       "data on Spotter's Settings page.")
REASON = "Spam in the feedback notes"


@pytest.fixture
def owner_on(make_user):
    """The owner's row, with OWNER_USER_ID set to it for the code running."""
    config.use_settings(settings(owner_user_id=OWNER))
    return make_user(subject=OWNER, email="owner@example.com")


def status_of(uid: int) -> tuple:
    with db.session_scope() as s:
        u = s.get(User, uid)
        return u.status, u.status_reason, u.status_at


def record() -> list[tuple]:
    with db.session_scope() as s:
        return [(a.by_account_id, a.action, a.account_id, a.reason, a.created_at)
                for a in s.scalars(select(OwnerAction).order_by(OwnerAction.id))]


def suspend(by: User, uid: int, reason: str = REASON) -> dict:
    return services.set_account_status(by, {"account_id": uid, "action": "suspend", "reason": reason})


def lift(by: User, uid: int, reason: str | None = None) -> dict:
    return services.set_account_status(by, {"account_id": uid, "action": "lift", "reason": reason})


def snapshot() -> dict:
    """Every row of every table but the call log, and the user rows less last_seen_at, which a sign-in moves."""
    with db.session_scope() as s:
        out = {}
        for table in Base.metadata.sorted_tables:
            if table.name == "call_log":
                continue
            columns = [c for c in table.c if not (table.name == "user" and c.name == "last_seen_at")]
            out[table.name] = sorted(map(tuple, s.execute(select(*columns)).all()), key=repr)
        return out


# ---------------------------------------------------------------- the service

def test_suspend_then_lift_change_the_row_and_write_one_record_row_each(owner_on, make_user, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)
    uid = make_user().id
    assert status_of(uid) == ("active", None, None)  # every account starts active
    assert suspend(owner_on, uid, f"  {REASON}  ") == {"changed": True, "account_id": uid, "status": "suspended"}
    assert status_of(uid) == ("suspended", REASON, NOW)  # trimmed
    later = NOW + timedelta(days=2)
    monkeypatch.setattr(db, "utcnow", lambda: later)
    assert lift(owner_on, uid, "Talked it over") == {"changed": True, "account_id": uid, "status": "active"}
    assert status_of(uid) == ("active", None, later)  # decision 10: the row says what is true now
    assert record() == [(owner_on.id, "suspend", uid, REASON, NOW), (owner_on.id, "lift", uid, "Talked it over", later)]
    assert lift(owner_on, uid) == {"changed": False, "account_id": uid, "status": "active"}  # decision 12
    assert suspend(owner_on, uid)["changed"] is True and lift(owner_on, uid, "   ")["changed"] is True
    assert record()[-1][3] is None  # a lift with no reason keeps none


def test_suspending_a_suspended_account_changes_nothing_and_writes_no_record_row(owner_on, make_user):
    uid = make_user().id
    suspend(owner_on, uid)
    before, rows = status_of(uid), record()
    assert suspend(owner_on, uid, "Another reason") == {"changed": False, "account_id": uid, "status": "suspended"}
    assert status_of(uid) == before and record() == rows


@pytest.mark.parametrize("reason, ok", [(None, False), ("", False), ("   ", False), ("x" * 200, True),
                                        ("x" * 201, False), ("bad\x00note", False), (" " + "x" * 200 + " ", True),
                                        (12, False)])
def test_a_suspend_needs_a_reason_of_1_to_200_characters(owner_on, make_user, reason, ok):
    uid = make_user().id
    result = suspend(owner_on, uid, reason)
    assert ("error" not in result) is ok, result
    if not ok:
        assert result["field"] == "reason" and status_of(uid)[0] == "active" and record() == []


def test_the_owners_own_account_cannot_be_suspended(owner_on):
    result = suspend(owner_on, owner_on.id)
    assert result["error"] == "owner_account" and status_of(owner_on.id) == ("active", None, None) and record() == []
    assert lift(owner_on, owner_on.id)["error"] == "owner_account"


@pytest.mark.parametrize("account_id", [987654, "abc", 0, -1, True, None, "1.5"])
def test_an_unknown_or_malformed_account_id_changes_nothing(owner_on, account_id):
    result = services.set_account_status(owner_on, {"account_id": account_id, "action": "suspend", "reason": REASON})
    assert result["error"] == ("not_found" if account_id == 987654 else "invalid_field") and record() == []


@pytest.mark.parametrize("action", ["ban", "", None, "Suspend"])
def test_an_unknown_action_changes_nothing(owner_on, make_user, action):
    uid = make_user().id
    result = services.set_account_status(owner_on, {"account_id": uid, "action": action, "reason": REASON})
    assert result["error"] == "invalid_field" and status_of(uid)[0] == "active" and record() == []


def test_owner_accounts_carries_the_status_and_the_last_20_record_rows_newest_first(owner_on, make_user, monkeypatch):
    uid = make_user().id
    for n in range(23):
        monkeypatch.setattr(db, "utcnow", lambda n=n: NOW + timedelta(minutes=n))
        (suspend if n % 2 == 0 else lift)(owner_on, uid, f"note {n}")
    found = services.owner_accounts()
    line = next(a for a in found["accounts"] if a["account_id"] == uid)
    assert (line["status"], line["status_reason"], line["status_at"]) == ("suspended", "note 22", "2026-06-10T12:22:00Z")
    assert len(found["record"]) == owner.RECORD_ROWS == 20
    assert [r["reason"] for r in found["record"]] == [f"note {n}" for n in range(22, 2, -1)]
    assert found["record"][0] == {"at": "2026-06-10T12:22:00Z", "action": "suspend", "account_id": uid,
                                  "by_account_id": owner_on.id, "reason": "note 22"}


def test_a_status_change_locks_the_user_row_first(owner_on, make_user):
    uid = make_user().id
    result, seen = statements_of(lambda: suspend(owner_on, uid))
    assert result["changed"] is True
    assert seen[0][0].replace("?", str(uid), 1).split(" WHERE ")[0] == lock_sql(uid).split(" WHERE ")[0]
    assert seen[0][1][-1] == uid  # the person's row, before anything else is read


# ---------------------------------------------------------------- MCP

@pytest.fixture
def person(make_client, make_user):
    """(client, token, user, base calls) of a person who is not the owner, with a time zone, deleting allowed, and
    one of each thing a base call needs, before any suspension."""
    client = make_client(owner_user_id=OWNER)
    owner_row = make_user(subject=OWNER)
    token = sign_for(PERSON)
    account = tool_call(client, token, "whoami", {})["structuredContent"]["account_id"]
    assert "error" not in services.update_settings(account, {"time_zone": "UTC"}, "web")
    set_switch(account, "log_delete")
    with db.session_scope() as s:
        user = s.get(User, account)
        s.expunge(user)
    calls = bases(user, seeded(user))
    with db.session_scope() as s:
        s.execute(CallLog.__table__.delete())
    return client, token, user, calls, owner_row


def test_a_suspended_account_gets_account_suspended_from_every_function_and_nothing_is_written(person):
    client, token, user, calls, owner_row = person
    assert suspend(owner_row, user.id)["changed"] is True
    names = list(registered_tools(client))
    assert set(names) == set(calls) and "whoami" in names and set(mcp_server.OWNER_FUNCTIONS) <= set(names)
    before = snapshot()
    for name in names:
        result = tool_call(client, token, name, calls[name])
        assert result["isError"] is True, name
        assert result["structuredContent"] == {"error": "account_suspended", "message": MESSAGE, "fix": FIX}, name
        assert REASON not in str(result)  # the owner's note is never in it (decision 18)
    assert snapshot() == before
    with db.session_scope() as s:
        logged = list(s.scalars(select(CallLog).order_by(CallLog.id)))
    assert [r.function for r in logged] == names
    for row in logged:
        assert (row.user_id, row.ok, row.error_code, row.outcome, row.step) == (
            user.id, False, "account_suspended", "refused", "account"), row.function


def test_the_sentences_are_the_contracts_and_the_step_is_account():
    assert mcp_server.ACCOUNT_SUSPENDED == {"error": "account_suspended", "message": MESSAGE, "fix": FIX}
    assert calllog.STEPS["account_suspended"] == "account"


def test_a_suspended_accounts_call_that_does_not_fit_its_function(person):
    """A call with an argument its function does not take, from a suspended account: the suspension answers, and the
    call-log row keeps the account."""
    client, token, user, calls, owner_row = person
    suspend(owner_row, user.id)
    result = tool_call(client, token, "get_log", {"not_a_field": 1})
    assert result["structuredContent"] == {"error": "account_suspended", "message": MESSAGE, "fix": FIX}
    with db.session_scope() as s:
        logged = [(r.function, r.user_id, r.error_code, r.step) for r in s.scalars(select(CallLog))]
    assert logged == [("get_log", user.id, "account_suspended", "account")]


def test_lifting_restores_every_call(person):
    client, token, user, calls, owner_row = person
    suspend(owner_row, user.id)
    assert tool_call(client, token, "whoami", {})["isError"] is True
    assert lift(owner_row, user.id)["changed"] is True
    for name in registered_tools(client):
        result = tool_call(client, token, name, calls[name])
        with db.session_scope() as s:
            s.execute(CallLog.__table__.delete())
        if name in mcp_server.OWNER_FUNCTIONS:  # not the owner: refused as before, never as suspended
            assert result["structuredContent"]["error"] == "not_allowed", name
        else:
            assert result["isError"] is False, (name, result)


def test_the_suspension_answers_before_the_switch(person):
    """Decision 13's order: with the switch at log, delete_entry is not_allowed for an active account; once suspended,
    delete_entry and restore_entry both answer account_suspended, never not_allowed."""
    client, token, user, calls, owner_row = person
    set_switch(user.id, "log")
    before = tool_call(client, token, "delete_entry", calls["delete_entry"])
    assert before["structuredContent"]["error"] == "not_allowed"
    suspend(owner_row, user.id)
    for name in ("delete_entry", "restore_entry"):
        result = tool_call(client, token, name, calls[name])
        assert result["structuredContent"] == {"error": "account_suspended", "message": MESSAGE, "fix": FIX}, name


def test_a_brand_new_accounts_first_call_passes_the_gate(make_client):
    client = make_client()
    result = tool_call(client, sign_for("user_01BRANDNEW"), "whoami", {})
    assert result["isError"] is False and result["structuredContent"]["account_id"] >= 1
    with db.session_scope() as s:
        assert s.scalar(select(User.status).where(User.subject == "user_01BRANDNEW")) == "active"


# ---------------------------------------------------------------- the web

GET_FILL = {"{date}": "2026-06-09", "{kind}": "bodyweight", "{entry_id}": "1", "{food_id}": "1", "{lift}": "Back Squat"}
OPEN = {"/settings/export", "/settings/delete"}  # decision 16


def filled(path: str, ids: dict | None = None) -> str:
    fill = dict(GET_FILL)
    if ids:
        fill.update({"{date}": ids["on"], "{entry_id}": str(ids["entries"]["bodyweight"]),
                     "{food_id}": str(ids["saved_food"]), "{routine_id}": "1", "{program_id}": "1", "{goal_id}": "1"})
    for marker, value in fill.items():
        path = path.replace(marker, value)
    return path


def is_suspended_page(r) -> bool:
    return (r.status_code == 403 and "Account suspended" in r.text and 'action="/settings/export"' in r.text
            and 'href="/settings/delete"' in r.text and 'action="/sign-out"' in r.text)


@pytest.fixture
def suspended_web(web_client, make_user):
    """A signed-in web person, suspended by the owner after signing in, with one of each thing a form changes."""
    client = web_client(owner_user_id=OWNER)
    owner_row = make_user(subject=OWNER)
    sign_in(client)
    user = only_user_but(OWNER)
    assert "error" not in services.update_settings(user.id, {"time_zone": "UTC"}, "web")
    ids = form_seeds(user.id, today_of(user))
    assert suspend(owner_row, user.id)["changed"] is True
    return client, user, ids, owner_row


def only_user_but(subject: str) -> User:
    with db.session_scope() as s:
        [user] = s.scalars(select(User).where(User.subject != subject)).all()
        s.expunge(user)
        return user


def test_every_page_but_the_two_open_ones_is_the_suspended_page(suspended_web):
    client, *_ = suspended_web
    gets = [path for path, _, methods in web.PAGES if "GET" in methods and path not in OPEN]
    assert "/settings" in gets and "/today" in gets
    for path in gets:
        r = client.get(filled(path))
        assert is_suspended_page(r), path
        assert REASON not in r.text  # decision 18


def test_a_suspended_account_with_no_time_zone_gets_no_form_that_posts_to_settings(web_client, make_user):
    """base.html's time zone form posts to /settings, which is gated: a suspended person's pages never carry it, so a
    click on the open Delete page is never taken back to the suspended page. Anyone else's pages keep it."""
    client = web_client(owner_user_id=OWNER)
    owner_row = make_user(subject=OWNER)
    sign_in(client)
    user = only_user_but(OWNER)
    assert status_of(user.id)[0] == "active"
    with db.session_scope() as s:
        assert s.get(User, user.id).time_zone is None
    assert 'id="auto-time-zone"' in client.get("/settings/delete").text  # active, no zone: the form, as before
    suspend(owner_row, user.id)
    for path in ("/settings/delete", "/", "/hi", "/today"):
        r = client.get(path)
        assert r.status_code in (200, 403) and 'id="auto-time-zone"' not in r.text, path


def test_a_suspended_person_can_still_sign_in_and_lands_on_the_suspended_page(web_client, make_user):
    client = web_client(owner_user_id=OWNER)
    owner_row = make_user(subject=OWNER)
    sign_in(client)
    suspend(owner_row, only_user_but(OWNER).id)
    fresh = web_client(owner_user_id=OWNER)
    r = sign_in(fresh)
    assert r.status_code == 303  # decision 15: sign-in is never gated
    assert is_suspended_page(fresh.get(r.headers["location"]))


def test_every_other_post_is_the_suspended_page_and_writes_nothing(suspended_web):
    client, user, ids, _ = suspended_web
    data = {"kg": "80", "kind": "waist", "value": "84", "description": "Dal", "kcal": "100",
            "exercise": "Treadmill Walk", "minutes": "30", "name": "Upma", "serving_label": "1 bowl",
            "kcal_per_serving": "200", "time_zone": "Asia/Kolkata", "claude_can": "log", "weight_unit": "lb",
            "distance_unit": "mi", "calorie_target_kcal": "2000", "target_value": "70", "status": "hit"}
    before = snapshot()
    posts = [path for path, _, methods in web.PAGES if "POST" in methods and path not in OPEN]
    assert len(posts) >= 20
    for path in posts:
        r = post(client, filled(path, ids), **data)
        assert is_suspended_page(r), path
    assert snapshot() == before
    with db.session_scope() as s:
        assert s.scalars(select(CallLog)).all() == []  # no write ran: no call-log row either


def test_export_still_returns_the_file(suspended_web):
    client, user, *_ = suspended_web
    r = post(client, "/settings/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "account.csv" in names and "bodyweight.csv" in names


def test_delete_still_deletes(suspended_web, monkeypatch):
    client, user, *_ = suspended_web
    gone = []

    async def deleted(subject):
        gone.append(subject)

    monkeypatch.setattr(web_auth, "delete_identity", deleted)
    page = client.get("/settings/delete")
    assert page.status_code == 200 and "Delete my account" in page.text
    r = post(client, "/settings/delete")
    assert r.status_code == 200 and "Your account is deleted" in r.text and gone == [WEB_PERSON]
    with db.session_scope() as s:
        assert s.get(User, user.id) is None
    assert len(record()) == 1  # the record outlives the account


def test_a_session_opened_before_the_suspension_is_caught_on_its_next_request(web_client, make_user):
    client = web_client(owner_user_id=OWNER)
    owner_row = make_user(subject=OWNER)
    sign_in(client)
    assert client.get("/progress").status_code == 200
    user = only_user_but(OWNER)
    suspend(owner_row, user.id)
    assert is_suspended_page(client.get("/progress"))
    lift(owner_row, user.id)
    assert client.get("/progress").status_code == 200  # and lifted, at once too


def test_the_pages_outside_pages_stay_open(suspended_web):
    client, *_ = suspended_web
    assert client.get("/").status_code == 200 and client.get("/hi").status_code == 200
    assert client.get("/settings/targets").status_code == 303  # a card address only redirects to /settings
    r = client.post("/sign-out", data={"csrf_token": session_of(client)["csrf"]})
    assert r.status_code == 303 and session_of(client).get(web.USER) is None


def test_a_placeholder_is_gated_too(web_client, make_user, monkeypatch):
    monkeypatch.setattr(web, "PLACEHOLDERS", {"later": "/later"})
    client = web_client(owner_user_id=OWNER)
    owner_row = make_user(subject=OWNER)
    sign_in(client)
    assert client.get("/later").status_code == 200
    suspend(owner_row, only_user_but(OWNER).id)
    assert is_suspended_page(client.get("/later"))


# ---------------------------------------------------------------- the owner's form

@pytest.fixture
def owner_web(web_client, make_user):
    """The web person is the owner; one other account to suspend."""
    client = web_client(owner_user_id=WEB_PERSON)
    sign_in(client)
    other = make_user(email="bea@example.com", subject="user_01BEA")
    return client, other


def test_the_owner_suspends_and_lifts_from_the_page(owner_web):
    client, other = owner_web
    page = client.get("/owner/accounts").text
    assert page.count('action="/owner/accounts/status"') == 1  # the owner's own row has no form
    assert 'name="key"' not in page.split("<tbody>")[1].split("</tbody>")[0]  # decision 22: no write key
    r = post(client, "/owner/accounts/status", account_id=str(other.id), action="suspend", reason=REASON)
    assert r.status_code == 303 and r.headers["location"].endswith("/owner/accounts?saved=suspended")
    page = client.get(r.headers["location"]).text
    assert "Account suspended." in page and REASON in page and "Lift suspension" in page and "Suspended" in page
    assert status_of(other.id)[0] == "suspended"
    r = post(client, "/owner/accounts/status", account_id=str(other.id), action="lift", reason="")
    assert r.headers["location"].endswith("?saved=lifted") and status_of(other.id)[0] == "active"
    assert "Suspension lifted." in client.get(r.headers["location"]).text
    r = post(client, "/owner/accounts/status", account_id=str(other.id), action="lift")
    assert r.headers["location"].endswith("?saved=already_active") and len(record()) == 2
    with db.session_scope() as s:
        assert s.scalars(select(CallLog)).all() == []  # not write(): its one trace is the record


@pytest.mark.parametrize("fields, mark", [
    ({"account_id": "abc", "action": "suspend", "reason": REASON}, "invalid"),
    ({"account_id": "987654", "action": "suspend", "reason": REASON}, "not_found"),
    ({"action": "ban", "reason": REASON}, "invalid"),
    ({"action": "suspend", "reason": "bad\x01note"}, "no_reason"),
    ({"action": "suspend", "reason": "x" * 201}, "no_reason"),
    ({"action": "suspend", "reason": "   "}, "no_reason"),
    ({"action": "suspend"}, "no_reason"),
    ({"action": "lift", "reason": "x" * 201}, "no_reason"),
])
def test_a_bad_form_changes_nothing_and_says_so(owner_web, fields, mark):
    client, other = owner_web
    r = post(client, "/owner/accounts/status", **{"account_id": str(other.id), **fields})
    assert r.headers["location"].endswith(f"?saved={mark}")
    assert web.OWNER_DONE[mark] in client.get(r.headers["location"]).text
    assert status_of(other.id)[0] == "active" and record() == []


def test_the_refused_reason_line_is_true_for_a_suspend_and_a_lift():
    assert web.OWNER_DONE["no_reason"] == (
        "Nothing changed. A reason is at most 200 characters, and a suspend needs one.")


def test_the_owner_cannot_suspend_their_own_account_from_the_page(owner_web):
    client, _ = owner_web
    me = only_user_but("user_01BEA")
    r = post(client, "/owner/accounts/status", account_id=str(me.id), action="suspend", reason=REASON)
    assert r.headers["location"].endswith("?saved=owner_account") and status_of(me.id)[0] == "active"


def test_an_outcome_mark_shows_only_its_own_words(owner_web):
    client, _ = owner_web
    page = client.get("/owner/accounts?saved=<b>hi</b>").text
    assert "<b>hi</b>" not in page and "&lt;b&gt;" not in page


def test_a_non_owner_post_is_the_404_and_changes_nothing(web_client, make_user):
    client = web_client(owner_user_id="user_01SOMEONE_ELSE")
    sign_in(client)
    other = make_user(subject="user_01BEA")
    r = post(client, "/owner/accounts/status", account_id=str(other.id), action="suspend", reason=REASON)
    assert r.status_code == 404 and "Page not found" in r.text
    assert status_of(other.id)[0] == "active" and record() == []
    signed_out = web_client(owner_user_id="user_01SOMEONE_ELSE")
    assert signed_out.post("/owner/accounts/status", data={"account_id": str(other.id)}).status_code == 403


def test_a_post_without_the_csrf_token_is_refused_as_every_form_is(owner_web):
    client, other = owner_web
    for data in ({}, {"csrf_token": "wrong"}):
        r = client.post("/owner/accounts/status", data={"account_id": str(other.id), "action": "suspend",
                                                         "reason": REASON, **data})
        assert r.status_code == 403 and "Form expired" in r.text
    assert status_of(other.id)[0] == "active" and record() == []


# ---------------------------------------------------------------- the record, the delete and the export

def test_the_record_survives_the_accounts_delete_and_holds_no_email(owner_on, make_user):
    other = make_user(email="bea@example.com", name="Bea")
    suspend(owner_on, other.id)
    result = services.delete_account(other.id)
    assert result["deleted"] is True and "owner_action" not in result["rows"]
    assert set(result["rows"]) == {"user", "lift_session", "bodyweight", "measurement", "goal", "saved_food",
                                   "food_entry", "cardio_entry", "routine", "program", "lift_set"}  # unchanged
    assert [(a, act, acc) for a, act, acc, *_ in record()] == [(owner_on.id, "suspend", other.id)]
    columns = {c.name for c in OwnerAction.__table__.c}
    assert columns == {"id", "by_account_id", "action", "account_id", "reason", "created_at"}
    assert "owner_action" not in user_tables() and not OwnerAction.__table__.foreign_keys
    with db.session_scope() as s:
        assert "bea@example.com" not in str(s.execute(select(OwnerAction.__table__)).all())


def test_the_export_carries_no_status_and_no_record(owner_on, make_user):
    other = make_user(email="bea@example.com")
    suspend(owner_on, other.id)
    result = services.export_data(other.id)
    header = result["files"]["account.csv"].lstrip("﻿").splitlines()[0].split(",")
    assert tuple(header) == export.ACCOUNT_COLUMNS and not {"status", "status_reason", "status_at"} & set(header)
    assert REASON not in str(result["files"]) and not any("owner" in name for name in result["files"])


def test_whoami_for_an_active_account_carries_no_status(person):
    client, token, *_ = person
    answer = tool_call(client, token, "whoami", {})["structuredContent"]
    assert not {"status", "status_reason", "status_at"} & set(answer)


# ---------------------------------------------------------------- migration 0017

def _sqlite_at(name: str) -> str:
    return f"sqlite:///{(Path(tempfile.mkdtemp(dir=TEST_DIR)) / name).as_posix()}"


def test_0017_up_down_and_up_on_sqlite_over_a_written_row_and_a_suspended_account():
    address = _sqlite_at("status.db")
    cfg = alembic_config(address)
    command.upgrade(cfg, "0016")
    engine = make_engine(address)
    stamp = "2026-11-27 09:00:00.000000"
    try:
        with engine.begin() as c:
            c.execute(sql_text('INSERT INTO "user" (id, provider, subject, created_at, updated_at, last_seen_at) '
                               "VALUES (7, 'test', 'kept', :t, :t, :t)"), {"t": stamp})
            c.execute(sql_text("INSERT INTO bodyweight (user_id, date, kg, dedupe_key, logged_by, created_at, "
                               "updated_at) VALUES (7, '2026-11-26', 80, 'k', 'web', :t, :t)"), {"t": stamp})
        master = _master(engine)
        engine.dispose()
        command.upgrade(cfg, "0017")
        with engine.begin() as c:
            assert c.execute(sql_text('SELECT status, status_reason, status_at FROM "user"')).all() == [
                ("active", None, None)]  # an existing account reads active
            c.execute(sql_text("UPDATE \"user\" SET status = 'suspended', status_reason = 'spam', status_at = :t"),
                      {"t": stamp})
            c.execute(sql_text("INSERT INTO owner_action (by_account_id, action, account_id, reason, created_at) "
                               "VALUES (1, 'suspend', 7, 'spam', :t)"), {"t": stamp})
            assert c.execute(sql_text("SELECT count(*) FROM bodyweight WHERE user_id = 7")).scalar() == 1
        with engine.connect() as c:
            found = inspect(c)
            assert [(col["name"], str(col["type"]), col["nullable"]) for col in found.get_columns("owner_action")] == [
                ("id", "INTEGER", False), ("by_account_id", "INTEGER", False), ("action", "VARCHAR(8)", False),
                ("account_id", "INTEGER", False), ("reason", "VARCHAR(200)", True), ("created_at", "DATETIME", False)]
            assert found.get_foreign_keys("owner_action") == []
            assert [i["name"] for i in found.get_indexes("owner_action")] == ["ix_owner_action_account_id"]
            assert {k["name"] for k in found.get_check_constraints("owner_action")} == {"ck_owner_action_action"}
            assert "ck_user_status" in {k["name"] for k in found.get_check_constraints("user")}
            assert found.get_pk_constraint("owner_action")["name"] == "pk_owner_action"
        for bad in ("UPDATE \"user\" SET status = 'banned'",
                    "INSERT INTO owner_action (by_account_id, action, account_id, created_at) "
                    "VALUES (1, 'ban', 7, '2026-11-27')"):
            with pytest.raises(Exception, match="CHECK"), engine.begin() as c:
                c.execute(sql_text(bad))
        engine.dispose()
        command.downgrade(cfg, "0016")
        assert _master(engine) == master  # 0016's schema, word for word
        with engine.connect() as c:
            assert c.execute(sql_text('SELECT id, subject FROM "user"')).all() == [(7, "kept")]
            assert c.execute(sql_text("SELECT count(*) FROM bodyweight WHERE user_id = 7")).scalar() == 1
        engine.dispose()
        command.upgrade(cfg, "0017")
        with engine.connect() as c:
            assert c.execute(sql_text('SELECT status FROM "user"')).scalar() == "active"  # the suspension is lost
            assert c.execute(sql_text("SELECT count(*) FROM owner_action")).scalar() == 0  # and the record
            assert c.execute(sql_text("SELECT count(*) FROM bodyweight WHERE user_id = 7")).scalar() == 1
            assert c.execute(sql_text("SELECT version_num FROM alembic_version")).scalar() == "0017"
    finally:
        engine.dispose()
