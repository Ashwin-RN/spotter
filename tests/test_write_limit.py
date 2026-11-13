"""The write limit: more than 120 write rows in any 10 minutes, this call's own among them, is refused.

Through MCP or the web, a call's own row ("in_progress") is written before the service runs, so it is
counted. A direct service call, as here, has no row of its own: WRITE_LIMIT earlier rows still allow it,
and WRITE_LIMIT + 1 refuse it.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from app import db, services
from app.models import Bodyweight, CallLog, CardioEntry, FoodEntry, Goal, LiftSession, Measurement, SavedFood, User
from app.services.common import WRITE_FUNCTIONS, WRITE_LIMIT, WRITE_WINDOW_MINUTES

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


@pytest.fixture
def user(make_user):
    return make_user()


def calls(user_id, n, minutes_ago=0.0, function="log_lifts", in_progress=False):
    """n call-log rows for this person, started `minutes_ago` minutes before NOW. in_progress: a row
    as the MCP wrapper and the web write it before the service runs."""
    with db.session_scope() as s:
        s.add_all(CallLog(user_id=user_id, function=function, started_at=NOW - timedelta(minutes=minutes_ago),
                          ms=0 if in_progress else 1, ok=not in_progress,
                          error_code="in_progress" if in_progress else None, result_bytes=0 if in_progress else 10)
                  for _ in range(n))


def counts(user_id) -> dict:
    """How many rows of each kind this person has, live or deleted: what a write would change."""
    with db.new_session() as s:
        out = {m.__tablename__: s.scalar(select(func.count()).select_from(m).where(m.user_id == user_id))
               for m in (LiftSession, Bodyweight, Measurement, Goal, FoodEntry, CardioEntry, SavedFood)}
        out["deleted"] = sum(s.scalar(select(func.count()).select_from(m).where(
            m.user_id == user_id, m.deleted_at.is_not(None))) for m in (LiftSession, Bodyweight, Measurement))
        user = s.get(User, user_id)
        out["user"] = (user.time_zone, user.updated_at)
        return out


def setup_entry(user):
    """An entry to edit, and one deleted entry to restore."""
    kept = services.log_bodyweight(user.id, {"kg": 80, "date": "2026-11-01"}, "web")["id"]
    gone = services.log_bodyweight(user.id, {"kg": 81, "date": "2026-11-02"}, "web")["id"]
    services.delete_entry(user.id, "bodyweight", gone)
    return kept, gone


WRITES = {
    "log_lifts": lambda u, ids: services.log_lifts(
        u.id, {"sets": [{"exercise": "Deadlift", "weight_kg": 100, "reps": 5}]}, "claude"),
    "log_bodyweight": lambda u, ids: services.log_bodyweight(u.id, {"kg": 79}, "claude"),
    "log_measurement": lambda u, ids: services.log_measurement(u.id, {"kind": "waist", "value": 84}, "claude"),
    "log_food": lambda u, ids: services.log_food(u.id, {"entries": [{"description": "dal", "kcal": 180}]}, "claude"),
    "save_food": lambda u, ids: services.save_food(
        u.id, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude"),
    "log_cardio": lambda u, ids: services.log_cardio(u.id, {"exercise": "Elliptical", "minutes": 20}, "claude"),
    "set_goal": lambda u, ids: services.set_goal(
        u.id, {"kind": "bodyweight", "target_value": 75, "target_date": "2027-01-01"}, "claude"),
    "update_settings": lambda u, ids: services.update_settings(u.id, {"time_zone": "Asia/Tokyo"}, "claude"),
    "edit_entry": lambda u, ids: services.edit_entry(u.id, "bodyweight", ids[0], {"kg": 77}, "claude"),
    "delete_entry": lambda u, ids: services.delete_entry(u.id, "bodyweight", ids[0]),
    "restore_entry": lambda u, ids: services.restore_entry(u.id, "bodyweight", ids[1]),
    # One call is one write, however many rows it carries: a weigh-in and a session here.
    "import_entries": lambda u, ids: services.import_entries(u.id, {"dry_run": False, "rows": [
        {"kind": "bodyweight", "date": "2026-11-09", "weight": 79},
        {"kind": "lift_session", "date": "2026-11-09", "sets": [{"exercise": "Deadlift", "weight": 100, "reps": 5}]}],
        "weight_unit": "kg"}, "claude"),   # the unit these weights need since the fix round on 7e95ee9
}


def setup_zone(user):
    """An import needs the person's time zone: set it in the row, so no write is spent on it."""
    with db.session_scope() as s:
        s.get(User, user.id).time_zone = "UTC"


# What each write needs first: an import needs a time zone.
SETUPS = {"import_entries": setup_zone}


def prepare(user, name):
    return SETUPS.get(name, setup_entry)(user)


def test_every_write_function_is_in_the_table():
    assert set(WRITES) == set(WRITE_FUNCTIONS) and WRITE_LIMIT == 120 and WRITE_WINDOW_MINUTES == 10


@pytest.mark.parametrize("name", list(WRITES))
def test_more_than_the_limit_is_refused_and_nothing_is_saved(user, name):
    ids = prepare(user, name)
    calls(user.id, WRITE_LIMIT + 1, minutes_ago=3)
    before = counts(user.id)
    r = WRITES[name](user, ids)
    assert r["error"] == "too_many_writes", r
    assert r["wait_minutes"] == 7 and "Wait 7 minutes" in r["fix"] and "Nothing was saved" in r["message"]
    assert counts(user.id) == before


@pytest.mark.parametrize("name", list(WRITES))
def test_the_limit_itself_is_allowed_for_a_call_with_no_row_of_its_own(user, name):
    ids = prepare(user, name)
    calls(user.id, WRITE_LIMIT, minutes_ago=3)
    r = WRITES[name](user, ids)
    assert "error" not in r, r


@pytest.mark.parametrize("name", list(WRITES))
def test_rows_outside_the_window_do_not_count(user, name):
    ids = prepare(user, name)
    calls(user.id, WRITE_LIMIT + 1, minutes_ago=WRITE_WINDOW_MINUTES + 0.5)
    calls(user.id, WRITE_LIMIT, minutes_ago=1)
    assert "error" not in WRITES[name](user, ids)


@pytest.mark.parametrize("name", list(WRITES))
def test_another_persons_rows_do_not_count(user, make_user, name):
    ids = prepare(user, name)
    calls(make_user().id, WRITE_LIMIT * 2)
    assert "error" not in WRITES[name](user, ids)


def test_read_calls_and_unnamed_rows_do_not_count(user):
    calls(user.id, WRITE_LIMIT * 2, function="get_progress")
    calls(user.id, WRITE_LIMIT * 2, function="whoami")
    assert "error" not in WRITES["log_bodyweight"](user, None)


def test_every_write_function_name_counts(user):
    per = WRITE_LIMIT // len(WRITE_FUNCTIONS)
    for name in WRITE_FUNCTIONS:
        calls(user.id, per, function=name)
    calls(user.id, WRITE_LIMIT + 1 - per * len(WRITE_FUNCTIONS), function="log_lifts")
    assert WRITES["log_bodyweight"](user, None)["error"] == "too_many_writes"


def test_reads_are_never_limited(user):
    calls(user.id, WRITE_LIMIT * 3)
    for r in (services.whoami(user.id), services.list_exercises(), services.get_progress(user.id),
              services.get_log(user.id, days=7), services.get_entry(user.id, "bodyweight", 1)):
        assert r.get("error") != "too_many_writes", r


def test_a_part_minute_of_waiting_rounds_up(user):
    calls(user.id, WRITE_LIMIT + 1, minutes_ago=2.5)  # the oldest leaves in 7.5 minutes
    assert WRITES["log_bodyweight"](user, None)["wait_minutes"] == 8


def test_the_wait_is_until_the_oldest_counted_row_leaves_and_at_least_a_minute(user):
    calls(user.id, 1, minutes_ago=9.9)
    calls(user.id, WRITE_LIMIT, minutes_ago=0)
    r = WRITES["log_bodyweight"](user, None)
    assert r["error"] == "too_many_writes" and r["wait_minutes"] == 1 and "Wait 1 minute," in r["fix"]
    calls(user.id, 1, minutes_ago=0)
    with db.session_scope() as s:  # the old row has left the window: the oldest counted is NOW
        s.execute(delete(CallLog).where(CallLog.started_at < NOW - timedelta(minutes=9)))
    assert WRITES["log_bodyweight"](user, None)["wait_minutes"] == WRITE_WINDOW_MINUTES


@pytest.mark.parametrize("name", list(WRITES))
def test_the_limit_with_this_calls_own_in_progress_row_is_refused(user, name):
    """As through MCP or the web: WRITE_LIMIT earlier rows, plus this call's own row written first."""
    ids = prepare(user, name)
    calls(user.id, WRITE_LIMIT, minutes_ago=3)
    calls(user.id, 1, function=name, in_progress=True)
    before = counts(user.id)
    r = WRITES[name](user, ids)
    assert r["error"] == "too_many_writes" and "this one included" in r["message"], r
    assert r["wait_minutes"] == 7 and counts(user.id) == before


@pytest.mark.parametrize("name", list(WRITES))
def test_one_under_the_limit_with_its_own_row_is_allowed(user, name):
    ids = prepare(user, name)
    calls(user.id, WRITE_LIMIT - 1, minutes_ago=3)
    calls(user.id, 1, function=name, in_progress=True)
    assert "error" not in WRITES[name](user, ids)


def test_the_web_side_the_switch_is_not_a_limited_write(user):
    """set_claude_can belongs to the Settings page; it is not in WRITE_FUNCTIONS and is not limited."""
    calls(user.id, WRITE_LIMIT * 2)
    assert services.set_claude_can(user.id, "log_delete") == {"claude_can": "log_delete", "changed": True}


def test_deleting_the_account_is_never_limited(user):
    """The person can always leave: the web deletes the sign-in first, so a refusal here would strand them.
    delete_account is not in WRITE_FUNCTIONS, its own in-progress row does not count, and it is never refused."""
    assert "delete_account" not in WRITE_FUNCTIONS
    setup_entry(user)
    calls(user.id, WRITE_LIMIT * 2, minutes_ago=1)
    calls(user.id, 1, function="delete_account", in_progress=True)
    r = services.delete_account(user.id)
    assert r["deleted"] is True and r["rows"]["bodyweight"] == 2
    with db.new_session() as s:
        assert s.get(User, user.id) is None


def test_delete_account_rows_do_not_count_toward_the_limit(user):
    calls(user.id, WRITE_LIMIT, minutes_ago=1)
    calls(user.id, 50, function="delete_account")
    assert "error" not in WRITES["log_bodyweight"](user, None)
