"""What the person reads back: get_progress, whoami and the exercise list."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from app import db, services, users
from app.exercises import LIFTS
from app.metrics import epley
from app.models import Exercise, LiftSession, LiftSet
from tests.conftest import RESOURCE

SITE = RESOURCE.rsplit("/mcp", 1)[0]
MISSING_ID = 10 ** 9


@pytest.fixture
def user(make_user):
    return make_user(subject="user_01PROGRESS", email="sam@example.com", name="Sam Example")


def day(user, offset):
    return (users.today_for(user) + timedelta(days=offset)).isoformat()


def lift(user, days_ago, *rows):
    r = services.log_lifts(user.id, {"date": day(user, -days_ago), "sets": [
        {"exercise": e, "weight_kg": w, "reps": n} for e, w, n in rows]}, "claude")
    assert "error" not in r, r
    return r


def build_squat_history(user):
    for days_ago, kg in ((25, 60), (20, 62.5), (15, 65), (10, 67.5), (5, 70)):
        lift(user, days_ago, ("Back Squat", kg, 5), ("Back Squat", kg - 10, 8))
    lift(user, 4, ("Bench Press", 40, 8))
    lift(user, 3, ("Pull-Up", 0, 8))  # no estimated max at 0 kg, but it was trained


# ---------------------------------------------------------------- get_progress

def test_one_lift_in_full(user):
    build_squat_history(user)
    services.set_goal(user.id, {"kind": "lift", "exercise": "Back Squat", "target_value": 100,
                                "target_date": day(user, 56)}, "claude")
    r = services.get_progress(user.id, "back squat")
    squat = r["lift"]
    assert r["today"] == day(user, 0) and squat["exercise"] == "Back Squat"
    series = squat["estimated_max_over_time"]
    assert len(series) == squat["sessions"] == 5 and series[0] == {"date": day(user, -25), "estimated_max_kg": 70.0}
    assert squat["current_estimated_max_kg"] == series[-1]["estimated_max_kg"] == epley(70, 5)
    assert squat["best_set"] == {"weight_kg": 70, "reps": 5, "date": day(user, -5), "estimated_max_kg": epley(70, 5)}
    assert squat["last_trained"] == day(user, -5)
    assert len(squat["bests"]) == 8 and {b["kind"] for b in squat["bests"]} == {"estimated_max", "weight"}
    assert squat["trend"]["slope_per_week"] > 0 and squat["trend"]["points_used"] == 5
    assert squat["goal"]["label"] == "Back Squat 100 kg" and squat["goals"] == [squat["goal"]]
    assert squat["goal"]["status"] in ("on track", "behind") and squat["goal"]["needed_per_week"] > 0
    assert r["links"] == {"progress_lift": f"{SITE}/progress/back-squat"}
    assert "Epley" in r["how"] and "note" not in r


def test_a_summary_of_every_lift_logged(user):
    build_squat_history(user)
    services.log_bodyweight(user.id, {"kg": 80}, "claude")
    services.set_goal(user.id, {"kind": "bodyweight", "target_value": 75, "target_date": day(user, 60)}, "web")
    r = services.get_progress(user.id)
    assert [row["exercise"] for row in r["lifts"]] == ["Back Squat", "Bench Press", "Pull-Up"]
    squat, bench, pull = r["lifts"]
    assert squat["sessions"] == 5 and squat["best_estimated_max_kg"] == epley(70, 5)
    assert squat["slope_per_week"] > 0 and squat["goal_status"] is None
    assert bench["sessions"] == 1 and bench["slope_per_week"] is None
    assert pull == {"exercise": "Pull-Up", "sessions": 1, "last_trained": day(user, -3),
                    "current_estimated_max_kg": None, "best_estimated_max_kg": None, "slope_per_week": None,
                    "goal_status": None}
    assert [g["kind"] for g in r["goals"]] == ["bodyweight"] and r["goals"][0]["direction"] == "down"
    assert r["links"] == {"progress": f"{SITE}/progress"}


def test_nothing_logged_yet(user):
    r = services.get_progress(user.id)
    assert r["lifts"] == [] and r["goals"] == [] and "No lift sessions" in r["note"]
    untrained = services.get_progress(user.id, "Face Pull")
    assert untrained["lift"]["sessions"] == 0 and untrained["lift"]["estimated_max_over_time"] == []
    assert "No sets of Face Pull" in untrained["note"]


def test_an_unknown_lift_gives_the_closest_names(user):
    r = services.get_progress(user.id, "Bak Squat")
    assert r["error"] == "unknown_exercise" and "Back Squat" in r["closest"]


def test_deleted_sessions_are_left_out(user):
    lift(user, 2, ("Deadlift", 100, 5))
    gone = lift(user, 1, ("Deadlift", 140, 5))
    services.delete_entry(user.id, "lift_session", gone["id"])
    r = services.get_progress(user.id, "Deadlift")["lift"]
    assert r["sessions"] == 1 and r["current_estimated_max_kg"] == epley(100, 5) and r["bests"] == []
    services.restore_entry(user.id, "lift_session", gone["id"])
    assert services.get_progress(user.id, "Deadlift")["lift"]["sessions"] == 2


def test_a_session_dated_after_today_is_left_out(user):
    """A row can sit after today when the person moves their time zone back. Reads ignore it."""
    with db.session_scope() as s:
        session = LiftSession(user_id=user.id, date=users.today_for(user) + timedelta(days=1), notes="",
                              logged_by="web", dedupe_key="e" * 64)
        session.sets = [LiftSet(exercise_id=s.scalar(select(Exercise.id).where(Exercise.name == "Deadlift")),
                                set_number=1, weight_kg=200, reps=5)]
        s.add(session)
    assert services.get_progress(user.id)["lifts"] == []
    assert services.get_progress(user.id, "Deadlift")["lift"]["sessions"] == 0


def test_get_progress_query_count_stays_small(user):
    build_squat_history(user)
    services.set_goal(user.id, {"kind": "lift", "exercise": "Back Squat", "target_value": 100,
                                "target_date": day(user, 56)}, "claude")
    count = [0]

    def counter(*_):
        count[0] += 1

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", counter)
    try:
        r = services.get_progress(user.id)
        one = services.get_progress(user.id, "Back Squat")
    finally:
        event.remove(engine, "before_cursor_execute", counter)
    assert r["lifts"] and one["lift"]["sessions"] and count[0] < 20, count[0]


def test_no_account():
    assert services.get_progress(987654)["error"] == "no_account"


def test_every_result_is_plain_json(user):
    build_squat_history(user)
    weigh = services.log_bodyweight(user.id, {"kg": 80}, "web")
    replace = services.log_bodyweight(user.id, {"kg": 79}, "web")
    goal = services.set_goal(user.id, {"kind": "lift", "exercise": "Back Squat", "target_value": 100,
                                       "target_date": day(user, 56)}, "claude")
    results = [
        services.whoami(user.id), services.list_exercises(), services.get_progress(user.id),
        services.get_progress(user.id, "Back Squat"), weigh, replace, goal,
        services.log_lifts(user.id, {"sets": [{"exercise": "Deadlift", "weight_kg": 90, "reps": 5}]}, "claude"),
        services.get_entry(user.id, "goal", goal["id"]),
        services.delete_entry(user.id, "bodyweight", replace["id"]),
        services.restore_entry(user.id, "bodyweight", weigh["id"]),
        services.get_entry(user.id, "bodyweight", MISSING_ID),
    ]
    for result in results:
        assert json.loads(json.dumps(result)) == result  # no dates, Decimals or tuples left in


# ---------------------------------------------------------------- whoami

def test_whoami_returns_the_account_and_asks_for_a_time_zone(user):
    r = services.whoami(user.id)
    assert r["account_id"] == user.id and r["user_id"] == "user_01PROGRESS"
    assert r["email"] == "sam@example.com" and r["name"] == "Sam Example"
    assert r["time_zone"] is None and "Ask the person" in r["time_zone_note"] and r["today"] == day(user, 0)
    assert r["targets"] == {"calorie_target_kcal": None, "target_sessions_per_week": None,
                            "target_cardio_minutes_per_week": None}
    assert r["claude_can"] == "log"
    assert r["links"] == {"settings": f"{SITE}/settings", "home": f"{SITE}/"}


def test_whoami_with_a_time_zone_and_targets(make_user):
    person = make_user(time_zone="Asia/Kolkata", target_sessions_per_week=4, calorie_target_kcal=2200)
    r = services.whoami(person.id)
    assert r["time_zone"] == "Asia/Kolkata" and "time_zone_note" not in r
    assert r["today"] == users.today_for(person).isoformat()
    assert r["targets"]["target_sessions_per_week"] == 4 and r["targets"]["calorie_target_kcal"] == 2200
    unknown = make_user(time_zone="Mars/Olympus_Mons")
    assert "time_zone_note" in services.whoami(unknown.id)


def test_whoami_for_a_missing_account():
    r = services.whoami(987654)
    assert r["error"] == "no_account" and r["fix"]


# ---------------------------------------------------------------- the exercise list

def test_list_exercises_and_filters():
    every = services.list_exercises()["exercises"]
    assert len(every) == len(LIFTS) and every[0] == {"name": "Back Squat", "kind": "lift", "category": "squat"}
    assert services.list_exercises(kind="lift")["exercises"] == every
    assert services.list_exercises(kind="cardio")["exercises"] == []
    pulls = services.list_exercises(category="pull")["exercises"]
    assert pulls and all(e["category"] == "pull" for e in pulls)
    assert services.list_exercises(kind="lift", category="carry")["exercises"][0]["name"] == "Farmer's Carry"
    assert services.list_exercises(category="legs")["error"] == "invalid_field"
    assert "squat" in services.list_exercises(category="legs")["fix"]
    assert services.list_exercises(kind="swim")["error"] == "invalid_field"
