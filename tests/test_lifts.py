"""log_lifts: validation, set numbering, new bests, the duplicate guard, today, and the user-row lock."""

import math
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql, sqlite

from app import db, services, users
from app.metrics import epley
from app.models import LiftSession, LiftSet
from app.services.common import lock_statement
from tests.conftest import RESOURCE

SITE = RESOURCE.rsplit("/mcp", 1)[0]


def sets(*rows):
    return [{"exercise": e, "weight_kg": w, "reps": r} for e, w, r in rows]


def session_count(user_id):
    with db.new_session() as s:
        return s.scalar(select(func.count(LiftSession.id)).where(LiftSession.user_id == user_id))


def count_queries(fn):
    statements = []

    def record(_conn, _cursor, statement, *_):
        statements.append(statement)

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return result, statements


@pytest.fixture
def user(make_user):
    return make_user()


def log(user, *rows, by="claude", **data):
    return services.log_lifts(user.id, {"sets": sets(*rows), **data}, by)


# ---------------------------------------------------------------- logging

def test_the_summary_tells_a_first_session_from_no_new_best(user):
    first = log(user, ("Back Squat", 60, 5))
    assert first["new_bests"] == [] and first["summary"].endswith("First Back Squat session: estimated max 70 kg is the mark to beat.")
    same = log(user, ("Back Squat", 60, 5), allow_repeat=True)
    assert same["new_bests"] == [] and same["summary"].endswith(" No new bests.") and "First" not in same["summary"]
    mixed = log(user, ("Back Squat", 70, 5), ("Deadlift", 80, 12))
    assert [p["kind"] for p in mixed["new_bests"]] == ["estimated_max", "weight"]
    # epley(70, 5) is 81.67: 81.7 at 1 dp, and 70 is the one it beat. One sentence a best.
    assert mixed["summary"].endswith("New best: Back Squat estimated max 81.7 kg, up from 70 kg. New best: Back Squat "
                                     "heaviest weight 70 kg, up from 60 kg. First Deadlift session: 80 kg is the mark "
                                     "to beat.")


def test_the_best_set_is_found_on_the_exact_estimate_and_shown_at_1_dp(user):
    """history compares the unrounded estimates, then rounds the best set into a new dict."""
    log(user, ("Back Squat", 100, 5))                               # epley 116.67
    log(user, ("Back Squat", 100.03, 5))                            # epley 116.70: higher, though both read 116.7
    best = services.get_progress(user.id, "Back Squat")["lift"]["best_set"]
    assert best["weight_kg"] == 100.03 and best["estimated_max_kg"] == 116.7
    row = services.get_progress(user.id)["lifts"][0]
    assert row["best_estimated_max_kg"] == 116.7 and row["current_estimated_max_kg"] == 116.7


def test_an_empty_list_joins_to_nothing():
    from app.services.lifts import _joined, sets_text
    assert _joined([]) == "" and _joined(["A"]) == "A" and _joined(["A", "B", "C"]) == "A, B and C"
    assert sets_text([]) == ""


def test_a_best_and_the_one_it_beat_widen_together_when_they_read_the_same(user):
    """D5: 116.70 against 116.67 both read 116.7 at 1 dp, so both come back and show at 2 dp."""
    log(user, ("Back Squat", 100, 5))                               # epley 116.67
    close = log(user, ("Back Squat", 100.03, 5))                    # epley 116.7016: 116.70 at 2 dp
    best = next(p for p in close["new_bests"] if p["kind"] == "estimated_max")
    assert (best["value"], best["previous"]) == (116.7, 116.67)
    assert best["text"] == "Back Squat estimated max 116.70 kg, up from 116.67 kg"
    clear = log(user, ("Back Squat", 110, 5))                       # epley 128.33 against 116.7: 1 dp apart
    best = next(p for p in clear["new_bests"] if p["kind"] == "estimated_max")
    assert (best["value"], best["previous"]) == (128.3, 116.7)
    assert best["text"] == "Back Squat estimated max 128.3 kg, up from 116.7 kg"
    logged = [b for day in services.get_log(user.id)["days"] for t in day["sessions"] for b in t["new_bests"]
              if b["kind"] == "estimated_max"]
    progress = [b for b in services.get_progress(user.id, "Back Squat")["lift"]["bests"] if b["kind"] == "estimated_max"]
    assert sorted((b["value"], b["previous"]) for b in logged) == sorted((b["value"], b["previous"]) for b in progress)         == [(116.7, 116.67), (128.3, 116.7)]
    assert {b["text"] for b in logged} == {"Back Squat estimated max 116.70 kg, up from 116.67 kg",
                                           "Back Squat estimated max 128.3 kg, up from 116.7 kg"}



def test_log_lifts_numbers_sets_per_exercise_and_links_each_lift(user):
    r = log(user, ("back squat", 60, 5), ("Back Squat", 60, 5), ("Deadlift", 80, 5), ("Back Squat", 60, 4),
            notes="  good depth ")
    assert "error" not in r, r
    date = users.today_for(user)
    today = date.isoformat()
    assert r["kind"] == "lift_session" and r["duplicate"] is False and r["new_bests"] == []
    session = r["session"]
    assert r["id"] == session["id"] and session["date"] == today and session["logged_by"] == "claude"
    assert session["notes"] == "good depth" and session["deleted_at"] is None
    assert [(x["exercise"], x["set_number"], x["reps"]) for x in session["sets"]] == [
        ("Back Squat", 1, 5), ("Back Squat", 2, 5), ("Deadlift", 1, 5), ("Back Squat", 3, 4)]
    assert "user_id" not in session and "dedupe_key" not in session
    assert r["links"] == {"day": f"{SITE}/day/{today}",
                          "progress": {"Back Squat": f"{SITE}/progress/back-squat",
                                       "Deadlift": f"{SITE}/progress/deadlift"}}
    # The date in the D12 form (D13), the lifts as a list, the "@" a word.
    assert r["summary"].startswith("Back Squat 60 kg × 5, 60 kg × 5, 60 kg × 4 and Deadlift 1 × 5 at "
                                   f"80 kg on {date:%a} {date.day} {date:%b}. ")
    assert "No new bests" not in r["summary"]
    assert ("First Back Squat session: estimated max 70 kg is the mark to beat. "
            "First Deadlift session: estimated max 93.3 kg is the mark to beat." in r["summary"])   # 93.33 at 1 dp
    with db.new_session() as s:
        row = s.get(LiftSession, r["id"])
        assert row.user_id == user.id and row.logged_by == "claude" and len(row.dedupe_key) == 64
        assert sorted((x.exercise.name, x.set_number) for x in row.sets) == [
            ("Back Squat", 1), ("Back Squat", 2), ("Back Squat", 3), ("Deadlift", 1)]


def test_new_bests_against_every_earlier_set_of_the_lift(user):
    assert log(user, ("Back Squat", 60, 5), ("Back Squat", 60, 5))["new_bests"] == []  # first time: no baseline
    second = log(user, ("Back Squat", 65, 5), ("Bench Press", 40, 8))
    assert {(p["exercise"], p["kind"]) for p in second["new_bests"]} == {("Back Squat", "estimated_max"),
                                                                          ("Back Squat", "weight")}
    squat = {p["kind"]: p for p in second["new_bests"]}
    # epley(65, 5) is 75.83: 75.8 at 1 dp, apart from 70 at 1 dp, so the pair stays at 1 dp.
    assert squat["estimated_max"]["value"] == 75.8 and squat["estimated_max"]["previous"] == 70.0
    assert squat["weight"]["value"] == 65 and squat["weight"]["previous"] == 60
    assert squat["weight"]["text"] == "Back Squat heaviest weight 65 kg, up from 60 kg"
    assert "New best: Back Squat estimated max 75.8 kg, up from 70 kg." in second["summary"]
    assert log(user, ("Back Squat", 62.5, 5))["new_bests"] == []
    # More reps at the same top weight: a new estimated max, not a new heaviest weight.
    assert [p["kind"] for p in log(user, ("Back Squat", 65, 8))["new_bests"]] == ["estimated_max"]


def test_an_older_session_is_compared_only_with_what_came_before_it(user):
    today = users.today_for(user)
    log(user, ("Deadlift", 100, 5))
    backfill = log(user, ("Deadlift", 90, 5), date=(today - timedelta(days=10)).isoformat())
    assert backfill["new_bests"] == []  # nothing before it: no baseline, and today's heavier set is later
    # Between a lighter earlier session and a heavier later one: a new best against the earlier one only.
    middle = log(user, ("Deadlift", 95, 5), date=(today - timedelta(days=5)).isoformat())
    assert {(p["kind"], p["previous"]) for p in middle["new_bests"]} == {("weight", 90), ("estimated_max", epley(90, 5))}


def test_a_deleted_session_does_not_count_toward_new_bests(user):
    log(user, ("Deadlift", 60, 5))
    heavy = log(user, ("Deadlift", 100, 5))
    services.delete_entry(user.id, "lift_session", heavy["id"])
    after = log(user, ("Deadlift", 70, 5))
    assert {(p["kind"], p["previous"]) for p in after["new_bests"]} == {("weight", 60), ("estimated_max", epley(60, 5))}


def test_every_validation_failure_comes_back_at_once(user):
    bad = services.log_lifts(user.id, {"sets": [
        {"exercise": "Bak Squat", "weight_kg": 60, "reps": 5},
        {"exercise": "Bench Press", "weight_kg": 501, "reps": 5},
        {"exercise": "Bench Press", "weight_kg": -1, "reps": 5},
        {"exercise": "Bench Press", "weight_kg": 40, "reps": 31},
        {"exercise": "Bench Press", "weight_kg": 40, "reps": 0},
        {"exercise": "Bench Press", "weight_kg": 40, "reps": 5.5},
        {"exercise": "Bench Press", "weight_kg": float("nan"), "reps": 5},
        {"exercise": "Bench Press", "weight_kg": math.inf, "reps": 5},
        {"exercise": "", "weight_kg": 40, "reps": 5},
    ]}, "claude")
    kinds = [e["error"] for e in bad["errors"]]
    assert kinds == ["unknown_exercise", "weight_out_of_range", "weight_out_of_range", "reps_out_of_range",
                     "reps_out_of_range", "not_a_whole_number", "not_a_number", "not_a_number", "missing_field"]
    assert bad["error"] == "unknown_exercise" and "Back Squat" in bad["errors"][0]["closest"]
    assert bad["message"].startswith("9 problems") and bad["fix"]
    assert "Set 2:" in bad["errors"][1]["message"] and "Set 7:" in bad["errors"][6]["message"]
    assert session_count(user.id) == 0


def test_dates_and_set_counts(user):
    today = users.today_for(user)
    one = ("Back Squat", 60, 5)
    future = log(user, one, date=(today + timedelta(days=1)).isoformat())
    assert future["error"] == "date_in_future" and today.isoformat() in future["message"]
    assert log(user, one, date="05/11/2026")["error"] == "invalid_date"
    assert services.log_lifts(user.id, {"sets": []}, "claude")["error"] == "sets_count_out_of_range"
    assert services.log_lifts(user.id, {"sets": sets(one) * 61}, "claude")["error"] == "sets_count_out_of_range"
    assert "error" not in services.log_lifts(user.id, {"sets": sets(one) * 60}, "claude")
    assert "error" not in log(user, ("Deadlift", 80, 5), date=today.isoformat())
    assert session_count(user.id) == 2


def test_notes_are_text_without_control_characters_and_not_too_long(user):
    for bad, words in (("a\x00b", "control characters"), ("x" * 2001, "Shorten notes"), (["a list"], "as text")):
        r = log(user, ("Deadlift", 80, 5), notes=bad)
        assert r["error"] == "invalid_field" and words in r["fix"], r
    both = log(user, ("Deadlift", 80, 31), notes="a\x00b")  # a bad set and bad notes: both reported
    assert [e["error"] for e in both["errors"]] == ["reps_out_of_range", "invalid_field"]
    assert session_count(user.id) == 0
    ok = log(user, ("Deadlift", 80, 5), notes="felt good\n\tgrip went on the last set")
    assert ok["session"]["notes"] == "felt good\n\tgrip went on the last set"


def test_sessions_go_back_two_years_at_most(user):
    assert log(user, ("Deadlift", 80, 5), date="0001-01-01")["error"] == "date_too_old"
    assert log(user, ("Deadlift", 80, 5), date=(users.today_for(user) - timedelta(days=800)).isoformat())["error"] \
        == "date_too_old"
    assert "error" not in log(user, ("Deadlift", 80, 5), date=(users.today_for(user) - timedelta(days=700)).isoformat())


def test_a_datetime_date_and_negative_zero_weight(user):
    when = datetime.combine(users.today_for(user) - timedelta(days=1), datetime.min.time())
    r = services.log_lifts(user.id, {"date": when, "sets": [{"exercise": "Push-Up", "weight_kg": -0.0, "reps": 10}]},
                           "web")
    assert r["session"]["date"] == when.date().isoformat() and str(r["session"]["sets"][0]["weight_kg"]) == "0.0"
    same = services.log_lifts(user.id, {"date": when.date().isoformat(),
                                        "sets": [{"exercise": "Push-Up", "weight_kg": 0, "reps": 10}]}, "web")
    assert same["duplicate"] is True and same["id"] == r["id"]  # -0.0 and 0 are the same entry


def test_bad_arguments_get_an_error_with_a_fix(user):
    assert services.log_lifts(user.id, {"sets": sets(("Deadlift", 80, 5))}, "someone")["error"] == "invalid_by"
    assert services.log_lifts(user.id, ["not", "a", "dict"], "web")["error"] == "invalid_field"
    gone = services.log_lifts(987654, {"sets": sets(("Deadlift", 80, 5))}, "web")
    assert gone["error"] == "no_account" and gone["fix"]
    assert session_count(user.id) == 0


# ---------------------------------------------------------------- today is the person's own date

def at(monkeypatch, when: datetime):
    monkeypatch.setattr(db, "utcnow", lambda: when)


def test_the_default_date_is_today_in_the_persons_time_zone(make_user, monkeypatch):
    at(monkeypatch, datetime(2026, 11, 9, 23, 30, tzinfo=timezone.utc))
    ahead = make_user(time_zone="Pacific/Kiritimati")   # UTC+14: already 10 November
    plain = make_user()                                  # no time zone yet: UTC, 9 November
    assert log(ahead, ("Deadlift", 80, 5))["session"]["date"] == "2026-11-10"
    assert log(plain, ("Deadlift", 80, 5))["session"]["date"] == "2026-11-09"
    # The future check uses the same today.
    assert "error" not in log(ahead, ("Deadlift", 85, 5), date="2026-11-10")
    assert log(plain, ("Deadlift", 85, 5), date="2026-11-10")["error"] == "date_in_future"


def test_a_time_zone_behind_utc_is_still_on_the_day_before(make_user, monkeypatch):
    at(monkeypatch, datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc))
    west = make_user(time_zone="America/Los_Angeles")
    assert log(west, ("Deadlift", 80, 5))["session"]["date"] == "2026-11-09"
    assert log(west, ("Deadlift", 85, 5), date="2026-11-10")["error"] == "date_in_future"


# ---------------------------------------------------------------- the duplicate guard

def age_all(model, user_id, minutes):
    """created_at is a UTC time; move every row of this person back by `minutes`."""
    with db.session_scope() as s:
        for row in s.scalars(select(model).where(model.user_id == user_id)):
            row.created_at = row.created_at - timedelta(minutes=minutes)


NARRATED = {"sets": sets(("Back Squat", 60, 5), ("Back Squat", 60, 4)), "notes": "felt heavy"}


def test_the_same_session_within_ten_minutes_is_not_logged_twice(user):
    first = services.log_lifts(user.id, NARRATED, "claude")
    again = services.log_lifts(user.id, NARRATED, "claude")
    assert again["duplicate"] is True and again["id"] == first["id"] and again["kind"] == "lift_session"
    assert again["session"] == first["session"] and again["new_bests"] == []
    assert "allow_repeat" in again["note"] and session_count(user.id) == 1
    # Not a duplicate: different sets, or different notes.
    assert services.log_lifts(user.id, {**NARRATED, "sets": sets(("Back Squat", 60, 5))}, "claude")["duplicate"] is False
    assert services.log_lifts(user.id, {**NARRATED, "notes": "second time"}, "claude")["duplicate"] is False
    assert session_count(user.id) == 3
    # Older than 10 minutes: logged again. The guard reads the entry's own created_at.
    age_all(LiftSession, user.id, 11)
    assert services.log_lifts(user.id, NARRATED, "claude")["duplicate"] is False
    assert session_count(user.id) == 4


def test_the_guard_runs_for_the_web_too_and_across_both(user):
    first = services.log_lifts(user.id, NARRATED, "web")
    assert services.log_lifts(user.id, NARRATED, "web")["id"] == first["id"]
    from_claude = services.log_lifts(user.id, NARRATED, "claude")
    assert from_claude["duplicate"] is True and from_claude["session"]["logged_by"] == "web"
    assert session_count(user.id) == 1


def test_allow_repeat_logs_it_anyway(user):
    first = services.log_lifts(user.id, NARRATED, "claude")
    second = services.log_lifts(user.id, {**NARRATED, "allow_repeat": True}, "claude")
    assert second["duplicate"] is False and second["id"] != first["id"]
    with db.new_session() as s:
        keys = list(s.scalars(select(LiftSession.dedupe_key).where(LiftSession.user_id == user.id)))
    assert len(keys) == 2 and keys[0] == keys[1]
    # A third copy, without allow_repeat, is a duplicate of the first.
    assert services.log_lifts(user.id, NARRATED, "web")["id"] == first["id"]


def test_the_order_of_lifts_does_not_matter_but_the_order_within_a_lift_does(user):
    first = log(user, ("Back Squat", 60, 5), ("Bench Press", 40, 8), ("Back Squat", 60, 4))
    assert log(user, ("Bench Press", 40, 8), ("Back Squat", 60, 5), ("Back Squat", 60, 4))["id"] == first["id"]
    assert log(user, ("Back Squat", 60, 4), ("Back Squat", 60, 5), ("Bench Press", 40, 8))["duplicate"] is False


def test_a_deleted_session_is_not_a_duplicate(user):
    first = services.log_lifts(user.id, NARRATED, "claude")
    services.delete_entry(user.id, "lift_session", first["id"])
    again = services.log_lifts(user.id, NARRATED, "claude")
    assert again["duplicate"] is False and again["id"] != first["id"]


def test_a_date_is_part_of_the_entry(user):
    today = users.today_for(user)
    first = services.log_lifts(user.id, NARRATED, "claude")
    yesterday = services.log_lifts(user.id, {**NARRATED, "date": (today - timedelta(days=1)).isoformat()}, "claude")
    assert yesterday["duplicate"] is False and yesterday["id"] != first["id"]


# ---------------------------------------------------------------- the lock and the query count

def test_log_lifts_query_count(user):
    log(user, ("Back Squat", 60, 5))  # some history
    r, statements = count_queries(lambda: log(user, ("Deadlift", 80, 5), ("Deadlift", 80, 5)))
    assert "error" not in r and len(statements) <= 12, statements


WRITES = {
    "log_lifts": lambda u: services.log_lifts(u.id, {"sets": sets(("Deadlift", 80, 5))}, "claude"),
    "log_bodyweight": lambda u: services.log_bodyweight(u.id, {"kg": 80}, "claude"),
    "set_goal": lambda u: services.set_goal(u.id, {"kind": "bodyweight", "target_value": 75, "start_value": 80,
                                                   "target_date": "2099-01-01"}, "claude"),
    "delete_entry": lambda u: services.delete_entry(u.id, "bodyweight", 1),
    "restore_entry": lambda u: services.restore_entry(u.id, "bodyweight", 1),
    "update_settings": lambda u: services.update_settings(u.id, {"time_zone": "Asia/Kolkata"}, "claude"),
    "log_measurement": lambda u: services.log_measurement(u.id, {"kind": "waist", "value": 84}, "claude"),
    "edit_entry": lambda u: services.edit_entry(u.id, "bodyweight", 1, {"kg": 70}, "claude"),
}


@pytest.mark.parametrize("name", list(WRITES))
def test_every_write_locks_the_user_row_before_anything_else(user, name):
    result, statements = count_queries(lambda: WRITES[name](user))
    assert statements, f"{name} ran no SQL at all: {result}"
    assert flat(statements[0]) == LOCK_SQL, statements[0]  # the lock itself, not merely some UPDATE of the user
    assert sum(flat(s) == LOCK_SQL for s in statements) == 1


def flat(statement: str) -> str:
    return " ".join(statement.split())


LOCK_SQL = flat(str(lock_statement("sqlite", 1).compile(dialect=sqlite.dialect())))


def test_the_postgres_lock_is_select_for_update_on_the_user_row():
    sql = " ".join(str(lock_statement("postgresql", 42).compile(dialect=postgresql.dialect())).split())
    assert sql.startswith("SELECT") and 'FROM "user"' in sql and sql.endswith("FOR UPDATE"), sql
    assert '"user".id = %(id_1)s' in sql
    sqlite_sql = str(lock_statement("sqlite", 42).compile(dialect=sqlite.dialect()))
    assert sqlite_sql.startswith("UPDATE user SET claude_can"), sqlite_sql


def test_two_retries_at_once_give_one_session(user, monkeypatch):
    # Hold each call inside the guard for a moment, so without the lock both would pass it before
    # either wrote. With the lock, the second waits for the first to commit, then sees it.
    from app.services import lifts
    guard = lifts.find_duplicate

    def slow_guard(*args, **kwargs):
        found = guard(*args, **kwargs)
        time.sleep(0.2)
        return found

    monkeypatch.setattr(lifts, "find_duplicate", slow_guard)
    barrier = threading.Barrier(2)
    results, failures = [], []

    def retry():
        try:
            barrier.wait(timeout=5)
            results.append(services.log_lifts(user.id, NARRATED, "claude"))
        except Exception as exc:  # reported below, so a crash in a thread fails the test
            failures.append(exc)

    threads = [threading.Thread(target=retry) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not failures, failures
    assert sorted(r["duplicate"] for r in results) == [False, True]
    assert len({r["id"] for r in results}) == 1 and session_count(user.id) == 1


def test_another_persons_sets_never_count_toward_new_bests(make_user):
    strong, starting = make_user(), make_user()
    log(strong, ("Back Squat", 200, 5))
    log(starting, ("Back Squat", 50, 5))
    second = log(starting, ("Back Squat", 55, 5))
    assert {p["previous"] for p in second["new_bests"]} == {58.3, 50}   # epley(50, 5), 58.33, at 1 dp
    with db.new_session() as s:
        assert s.scalar(select(func.count(LiftSet.id)).join(LiftSession)
                        .where(LiftSession.user_id == starting.id)) == 2
