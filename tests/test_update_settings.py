"""update_settings: the time zone, set by Claude in the first conversation, and the switch it can never touch."""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import sqlite

from app import db, services, users
from app.models import Bodyweight, User
from app.services.common import lock_statement

KEYS = {"time_zone", "today", "changed", "summary", "links"}


@pytest.fixture
def user(make_user):
    return make_user()


def stored(user_id) -> User:
    with db.new_session() as s:
        return s.get(User, user_id)


def zone(user, value, by="claude"):
    return services.update_settings(user.id, {"time_zone": value}, by)


def test_setting_the_time_zone_returns_the_fixed_shape(user):
    before = stored(user.id).updated_at
    r = zone(user, "Asia/Kolkata")
    assert set(r) == KEYS and r["time_zone"] == "Asia/Kolkata" and r["changed"] == ["time_zone"]
    assert r["summary"] == f"Time zone set to Asia/Kolkata. Today is {r['today']}."
    assert r["links"]["settings"].endswith("/settings")
    json.dumps(r).encode("utf-8")
    row = stored(user.id)
    assert row.time_zone == "Asia/Kolkata" and row.updated_at >= before
    assert "time_zone_note" not in services.whoami(user.id)


def test_the_same_value_again_changes_nothing_and_says_so(user):
    zone(user, "Europe/London")
    stamp = stored(user.id).updated_at
    again = zone(user, "europe/london", by="web")
    assert set(again) == KEYS and again["changed"] == [] and "Nothing changed" in again["summary"]
    assert stored(user.id).updated_at == stamp


def test_nothing_given_is_nothing_to_change(user):
    for data in ({}, None):
        r = services.update_settings(user.id, data, "claude")
        assert r["error"] == "nothing_to_change" and "time_zone" in r["fix"]


@pytest.mark.parametrize("given,kept", [
    (" asia/kolkata ", "Asia/Kolkata"), ("ASIA/KOLKATA", "Asia/Kolkata"), ("America/New York", "America/New_York"),
    ("america/new  york", "America/New_York"), ("UTC", "UTC"), ("utc", "UTC"),
    ("America/Argentina/Buenos_Aires", "America/Argentina/Buenos_Aires"),
])
def test_the_form_is_forgiven(user, given, kept):
    assert zone(user, given)["time_zone"] == kept and stored(user.id).time_zone == kept


@pytest.mark.parametrize("given,closest", [
    ("CET", []), ("EST", []), ("PST", []), ("BST", []), ("GMT", []), ("+05:30", []), ("UTC+5:30", []), ("GMT+5", []),
    ("Etc/GMT+5", []), ("Bangalore", []), ("France", []), ("Japan", []), ("Asia/Bangalore", []), ("", []), ("   ", []),
    ("Kolkata", ["Asia/Kolkata"]), ("london", ["Europe/London"]), ("New York", ["America/New_York"]),
])
def test_abbreviations_offsets_and_bare_places_are_refused(user, given, closest):
    r = zone(user, given)
    assert r["error"] == "unknown_time_zone" and r["closest"] == closest, r
    assert "IANA name such as Asia/Kolkata" in r["fix"] and "when the clocks change" in r["fix"]
    if closest:
        assert f"Closest zone: {closest[0]}." in r["fix"]
    assert stored(user.id).time_zone is None


def test_values_that_are_not_clean_text_are_refused(user):
    for bad, words in ((5, "as text"), (["Asia/Kolkata"], "as text"), ("Asia/\x00Kolkata", "control characters"),
                       ("A" * 65, "Shorten time_zone")):
        r = zone(user, bad)
        assert r["error"] == "invalid_field" and words in r["fix"], r
    assert zone(user, None)["error"] == "unknown_time_zone"
    long = zone(user, "Z" * 100_000)
    assert len(json.dumps(long)) < 1_000
    assert stored(user.id).time_zone is None


def test_an_unknown_field_names_what_can_be_set(user):
    r = services.update_settings(user.id, {"colour": "blue"}, "claude")
    assert r["error"] == "invalid_field" and "'colour'" in r["message"] and "time_zone" in r["fix"]


@pytest.mark.parametrize("by", ["claude", "web"])
def test_what_claude_may_do_can_never_be_set_here(user, by):
    for data in ({"claude_can": "log_delete"}, {"claude_can": "read"},
                 {"claude_can": "log_delete", "time_zone": "Asia/Kolkata"}):
        r = services.update_settings(user.id, data, by)
        assert r["error"] == "not_allowed", r
        assert "Only the person" in r["fix"] and "Settings page" in r["fix"]
    row = stored(user.id)
    assert row.claude_can == "log" and row.time_zone is None  # nothing stored, the zone included


def test_bad_arguments(user):
    assert services.update_settings(user.id, {"time_zone": "UTC"}, "robot")["error"] == "invalid_by"
    assert services.update_settings(user.id, ["time_zone"], "claude")["error"] == "invalid_field"
    assert services.update_settings(987654, {"time_zone": "UTC"}, "claude")["error"] == "no_account"
    errors = services.update_settings(user.id, {"time_zone": "CET", "colour": "x"}, "claude")["errors"]
    assert [e["error"] for e in errors] == ["unknown_time_zone", "invalid_field"]


def test_today_follows_the_zone(user, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 9, 23, 30, tzinfo=timezone.utc))
    assert services.whoami(user.id)["today"] == "2026-11-09"                 # UTC until set
    assert zone(user, "Pacific/Kiritimati")["today"] == "2026-11-10"         # UTC+14
    assert services.whoami(user.id)["today"] == "2026-11-10"
    west = zone(user, "America/Los_Angeles")
    assert west["today"] == "2026-11-09" and "Today is 2026-11-09." in west["summary"]


def test_a_change_of_zone_changes_no_entry(user):
    logged = services.log_bodyweight(user.id, {"kg": 80}, "claude")
    zone(user, "Pacific/Kiritimati")
    with db.new_session() as s:
        row = s.get(Bodyweight, logged["id"])
        assert row.date.isoformat() == logged["weigh_in"]["date"] and row.deleted_at is None


@pytest.mark.parametrize("data", [{"time_zone": "Asia/Kolkata"}, {"time_zone": "UTC"}])
def test_the_lock_is_the_first_statement_whether_or_not_anything_changes(make_user, data):
    person = make_user(time_zone="UTC")  # the second case changes nothing, the first changes the zone
    statements = []

    def record(_conn, _cursor, statement, *_):
        statements.append(" ".join(statement.split()))

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        r = services.update_settings(person.id, data, "claude")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert "error" not in r, r
    lock = " ".join(str(lock_statement("sqlite", person.id).compile(dialect=sqlite.dialect())).split())
    assert statements and statements[0] == lock, statements[:2]


def test_one_persons_call_never_changes_anothers(make_user):
    a, b = make_user(), make_user(time_zone="Europe/Paris")
    zone(a, "Asia/Tokyo")
    assert stored(b.id).time_zone == "Europe/Paris" and stored(a.id).time_zone == "Asia/Tokyo"


# ---------------------------------------------------------------- one stored name per zone

def test_an_old_name_is_stored_as_its_current_name_and_whoami_shows_it(user):
    r = zone(user, "Asia/Calcutta")
    assert r["time_zone"] == "Asia/Kolkata" and r["changed"] == ["time_zone"]
    assert stored(user.id).time_zone == "Asia/Kolkata"
    assert services.whoami(user.id)["time_zone"] == "Asia/Kolkata"
    assert zone(user, "asia/kolkata")["changed"] == []  # the same zone, so nothing changed


def test_the_web_and_claude_store_the_same_name(make_user):
    a, b = make_user(), make_user()
    services.update_settings(a.id, {"time_zone": "Asia/Calcutta"}, "web")  # the browser's report, as the page sends it
    zone(b, "Asia/Calcutta")  # the same report, through Claude
    assert stored(a.id).time_zone == stored(b.id).time_zone == "Asia/Kolkata"


def test_without_the_alias_data_claude_stores_an_old_name_as_given(user, monkeypatch):
    real = users._tz_file
    monkeypatch.setattr(users, "_tz_file", lambda name: None if name == "tzdata.zi" else real(name))
    users._zone_tables.cache_clear()
    try:
        assert zone(user, "Asia/Calcutta")["time_zone"] == "Asia/Calcutta"
    finally:
        users._zone_tables.cache_clear()
