"""The owner's accounts page (contract owner-accounts.md, package P1): /owner/accounts and services.owner_accounts.

The clock is fixed (db.utcnow), so no test depends on the real date. The web side signs in through the fake provider
seam of tests/test_web.py: its person's subject is the owner's when OWNER_USER_ID names it.
"""

import html
import re
from datetime import datetime, timedelta, timezone

import pytest
from markupsafe import escape
from sqlalchemy import func, select, update

from app import calllog, db, models, services, web
from app.models import Bodyweight, CallLog, Feedback, User
from app.services import owner

from .test_web import NO_WEB, only_user, seam, sign_in, web_client  # noqa: F401 - fixtures

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
OWNER = "user_01WEB"  # the fake seam's subject (tests/test_web.py FakeSeam)
OWNER_EMAIL = "asha@example.com"


@pytest.fixture
def fixed(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)
    return NOW


@pytest.fixture
def owner_client(web_client, fixed):
    """Signed in as the owner, in Asia/Kolkata."""
    client = web_client(owner_user_id=OWNER)
    sign_in(client)
    assert "error" not in services.update_settings(only_user().id, {"time_zone": "Asia/Kolkata"}, "web")
    return client


def log_one_of_each(uid: int) -> dict:
    """One live entry of each of the five kinds, through the services."""
    return {
        "bodyweights": services.log_bodyweight(uid, {"kg": 80}, "claude")["id"],
        "measurements": services.log_measurement(uid, {"kind": "waist", "value": 84}, "claude")["id"],
        "food_entries": services.log_food(uid, {"entries": [{"description": "Dal", "kcal": 180}]}, "claude")["ids"][0],
        "cardio_entries": services.log_cardio(uid, {"exercise": "Treadmill Walk", "minutes": 30}, "claude")["id"],
        "lift_sessions": services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]},
                                            "claude")["id"],
    }


def add_calls(uid: int | None, *ages: timedelta) -> None:
    with db.session_scope() as s:
        s.add_all(CallLog(user_id=uid, function="get_log", started_at=NOW - age, ms=1, ok=True, result_bytes=1)
                  for age in ages)


def add_feedback(uid: int, *ages: timedelta) -> None:
    with db.session_scope() as s:
        s.add_all(Feedback(user_id=uid, text=f"note {n}", topic="other", dedupe_key=f"{uid:032d}{n:032d}",
                           created_at=NOW - age) for n, age in enumerate(ages))


def line_of(found: dict, uid: int) -> dict:
    [line] = [a for a in found["accounts"] if a["account_id"] == uid]
    return line


def table_rows(page: str) -> list[str]:
    body = re.search(r"<tbody>(.*?)</tbody>", page, re.S).group(1)
    return re.findall(r"<tr>(.*?)</tr>", body, re.S)


def cells(row: str) -> dict[str, str]:
    """A row's cells by their data-label, as plain text; the lead cell under "Account". A cell with neither (the
    form's) is left out."""
    out = {}
    for attrs, inner in re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S):
        label = re.search(r'data-label="([^"]*)"', attrs)
        if label or 'class="lead"' in attrs:
            out[html.unescape(label.group(1)) if label else "Account"] = re.sub(r"\s+", " ", html.unescape(
                re.sub(r"<[^>]+>", " ", inner))).strip()
    return out


# ---------------------------------------------------------------- the service

def test_the_five_entry_counts_are_the_five_entry_models():
    assert sorted(model.__tablename__ for _, model in owner.ENTRY_COUNTS) == sorted(
        m.__tablename__ for m in models.entry_models())


def test_one_line_per_account_newest_first_with_its_fields(make_user, fixed):
    old = make_user(email="old@example.com", name="Old", time_zone="UTC", created_at=NOW - timedelta(days=40),
                    last_seen_at=NOW - timedelta(days=8))
    new = make_user(email="new@example.com", name="New", created_at=NOW - timedelta(days=1),
                    last_seen_at=NOW - timedelta(days=6, hours=23))
    found = services.owner_accounts()
    assert [a["account_id"] for a in found["accounts"]] == [new.id, old.id]
    assert line_of(found, old.id) == {
        "account_id": old.id, "email": "old@example.com", "name": "Old", "time_zone": "UTC",
        "joined": "2026-05-01T12:00:00Z", "last_seen": "2026-06-02T12:00:00Z",
        "lift_sessions": 0, "bodyweights": 0, "measurements": 0, "food_entries": 0, "cardio_entries": 0,
        "calls": 0, "feedback": 0, "status": "active", "status_reason": None, "status_at": None}
    assert (found["total"], found["seen"], found["seen_days"]) == (2, 1, 7)  # old was last seen 8 days ago
    assert (found["call_days"], found["feedback_days"]) == (calllog.CALL_LOG_DAYS, calllog.FEEDBACK_DAYS) == (90, 365)


def test_counts_are_live_rows_only_and_each_account_its_own(make_user, fixed):
    one, two = make_user(time_zone="UTC"), make_user(time_zone="UTC")
    log_one_of_each(one.id)
    services.log_bodyweight(one.id, {"kg": 81, "date": (NOW - timedelta(days=1)).date().isoformat()}, "claude")
    gone = services.log_bodyweight(two.id, {"kg": 70}, "claude")["id"]
    with db.session_scope() as s:  # a deleted entry, as delete_entry leaves it: its row stays, deleted_at set
        s.execute(update(Bodyweight).where(Bodyweight.id == gone).values(deleted_at=NOW))
    found = services.owner_accounts()
    first, second = line_of(found, one.id), line_of(found, two.id)
    assert {k: first[k] for k, _ in owner.ENTRY_COUNTS} == {
        "lift_sessions": 1, "bodyweights": 2, "measurements": 1, "food_entries": 1, "cardio_entries": 1}
    assert {k: second[k] for k, _ in owner.ENTRY_COUNTS} == dict.fromkeys(dict(owner.ENTRY_COUNTS), 0)


def test_a_call_or_a_note_older_than_its_window_is_not_counted(make_user, fixed):
    uid = make_user().id
    add_calls(uid, timedelta(days=1), timedelta(days=89, hours=23), timedelta(days=90, minutes=1))
    add_calls(None, timedelta(hours=1))  # a call with no account counts for nobody
    add_feedback(uid, timedelta(days=2), timedelta(days=364), timedelta(days=365, minutes=1))
    line = line_of(services.owner_accounts(), uid)
    assert (line["calls"], line["feedback"]) == (2, 2)
    with db.session_scope() as s:  # left out by the window, not by a sweep: every row is still there
        assert s.scalar(select(func.count()).select_from(CallLog)) == 4
        assert s.scalar(select(func.count()).select_from(Feedback)) == 3


def test_an_account_with_nothing_logged_is_all_zeros(make_user, fixed):
    uid = make_user().id
    line = line_of(services.owner_accounts(), uid)
    assert [line[k] for k in (*dict(owner.ENTRY_COUNTS), "calls", "feedback")] == [0] * 7


# ---------------------------------------------------------------- the page

def test_the_owner_sees_every_account_with_its_counts_in_their_own_zone(owner_client, make_user):
    me = only_user()
    other = make_user(email="bea@example.com", name="Bea", created_at=datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc),
                      last_seen_at=NOW)
    log_one_of_each(other.id)
    add_calls(other.id, timedelta(hours=2), timedelta(days=100))
    add_feedback(other.id, timedelta(days=3))
    r = owner_client.get("/owner/accounts")
    assert r.status_code == 200
    assert OWNER_EMAIL in r.text and "bea@example.com" in r.text
    rows = table_rows(r.text)
    assert len(rows) == 2 and "bea@example.com" in rows[1]  # newest first: the owner signed in after Bea joined
    assert cells(rows[1]) == {
        "Account": f"bea@example.com Account {other.id}", "Name": "Bea", "Time zone": "Not set",
        "Joined": "Tue 2 Jun",  # 20:00 UTC on 1 June is 2 June in Asia/Kolkata, the owner's zone
        "Last seen": "Wed 10 Jun", "Lift sessions": "1", "Weigh-ins": "1", "Measurements": "1",
        "Food entries": "1", "Cardio sessions": "1", "Calls, 90 days": "1", "Feedback, 365 days": "1",
        "Status": "Active"}
    assert cells(rows[0])["Account"] == f"{OWNER_EMAIL} Account {me.id}"
    assert "2 accounts, 2 seen in the last 7 days." in r.text
    assert "Calls cover the last 90 days and feedback the last 365" in r.text


def test_the_owners_page_writes_nothing(owner_client):
    with db.session_scope() as s:
        before = s.scalar(select(func.count()).select_from(CallLog))
    assert owner_client.get("/owner/accounts").status_code == 200
    with db.session_scope() as s:
        assert s.scalar(select(func.count()).select_from(CallLog)) == before


def not_found(r) -> bool:
    return r.status_code == 404 and "Page not found" in r.text and "There is nothing here." in r.text


def test_a_signed_out_visitor_gets_the_404_and_no_email(web_client, make_user, fixed):
    make_user(email="bea@example.com")
    client = web_client(owner_user_id=OWNER)
    r = client.get("/owner/accounts")
    assert not_found(r) and "bea@example.com" not in r.text and "Accounts" not in r.text


def test_a_signed_in_person_who_is_not_the_owner_gets_the_404(web_client, make_user, fixed):
    make_user(email="bea@example.com")
    client = web_client(owner_user_id="user_01SOMEONE_ELSE")
    sign_in(client)
    r = client.get("/owner/accounts")
    assert not_found(r) and "bea@example.com" not in r.text and OWNER_EMAIL not in r.text


def test_with_owner_user_id_unset_nobody_sees_it(web_client, make_user, fixed):
    make_user(email="bea@example.com")
    client = web_client()  # OWNER_USER_ID unset
    sign_in(client)
    r = client.get("/owner/accounts")
    assert not_found(r) and "bea@example.com" not in r.text


def test_the_owner_with_nothing_logged_sees_zeros(owner_client):
    [row] = table_rows(owner_client.get("/owner/accounts").text)
    shown = cells(row)
    assert [shown[label] for _, label in web.OWNER_COUNTS] == ["0"] * 5
    assert (shown["Calls, 90 days"], shown["Feedback, 365 days"]) == ("0", "0")
    assert "1 account, 1 seen in the last 7 days." in owner_client.get("/owner/accounts").text


def test_the_page_is_off_without_web_sign_in(web_client, fixed):
    client = web_client(**NO_WEB, owner_user_id=OWNER)
    r = client.get("/owner/accounts")
    assert r.status_code == 503 and "not set up" in r.text


def test_the_page_is_in_no_list_a_person_or_an_assistant_is_sent_from():
    from app import links
    assert "/owner/accounts" not in links._PATHS.values()
    assert all(path != "/owner/accounts" for path, _, _ in web.PAGES)
    assert ("/owner/accounts", web.owner_accounts_page, ["GET"]) in web.OWNER_PAGES



def test_a_non_owners_404_is_the_404_any_signed_in_page_gives(web_client, fixed):
    """The one 404 (decision 3): the same page a signed-in person gets for a saved food that is not theirs."""
    client = web_client(owner_user_id="user_01SOMEONE_ELSE")
    sign_in(client)
    mine, other = client.get("/owner/accounts"), client.get("/foods/987654/edit")
    assert mine.status_code == other.status_code == 404
    assert re.sub(r'value="[^"]*"', "", mine.text) == re.sub(r'value="[^"]*"', "", other.text)


SCRIPT = "<script>alert(1)</script>"
IMG = '"><img src=x onerror=alert(2)>'


def test_markup_in_an_accounts_fields_and_its_reason_is_escaped(owner_client, make_user):
    me = only_user()
    other = make_user(email=f"{SCRIPT}@example.com", name=IMG, time_zone=f"{SCRIPT}{IMG}")
    result = services.set_account_status(me, {"account_id": other.id, "action": "suspend",
                                                       "reason": f"{SCRIPT} {IMG}"})
    assert result["changed"] is True
    r = owner_client.get("/owner/accounts")
    assert r.status_code == 200
    for raw in (SCRIPT, IMG):
        assert raw not in r.text, raw
    for shown in (escape(f"{SCRIPT}@example.com"), escape(IMG), escape(f"{SCRIPT}{IMG}")):
        assert shown in r.text, shown  # each field, escaped
    record = r.text.split('id="record-title"')[1]
    assert escape(f"{SCRIPT} {IMG}") in record  # the reason, in the record too
