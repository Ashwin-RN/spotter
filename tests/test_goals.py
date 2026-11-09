"""set_goal: what a new goal stores, how it is edited and closed, and where it stands."""

import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app import db, services, users
from app.metrics import epley
from app.models import Goal


@pytest.fixture
def user(make_user):
    return make_user()


def day(user, offset):
    return (users.today_for(user) + timedelta(days=offset)).isoformat()


def squat(user, days_ago, kg, reps=5):
    r = services.log_lifts(user.id, {"date": day(user, -days_ago),
                                     "sets": [{"exercise": "Back Squat", "weight_kg": kg, "reps": reps}]}, "claude")
    assert "error" not in r, r


def weigh(user, days_ago, kg):
    assert "error" not in services.log_bodyweight(user.id, {"kg": kg, "date": day(user, -days_ago)}, "claude")


def goal(user, by="claude", **data):
    return services.set_goal(user.id, data, by)


def test_a_lift_goal_stores_its_start_and_matches_get_progress(user):
    for days_ago, kg in ((20, 60), (15, 62.5), (10, 65), (5, 67.5)):
        squat(user, days_ago, kg)
    r = goal(user, kind="lift", exercise="back squat", target_value=100, target_date=day(user, 56),
             text="100 by spring")
    assert "error" not in r, r
    g = r["goal"]
    assert r["kind"] == "goal" and r["id"] == g["id"] and r["warnings"] == []
    assert g["exercise"] == "Back Squat" and g["status"] == "open" and g["text"] == "100 by spring"
    assert g["start_value"] == epley(67.5, 5) and g["start_date"] == day(user, 0)
    assert g["direction"] == "up" and g["unit"] == "kg" and g["closed_at"] is None
    progress = services.get_progress(user.id, "Back Squat")["lift"]["goal"]
    assert r["progress"] == progress and progress["status"] in ("on track", "behind")
    assert progress["projection"]["points_used"] == 4
    assert progress["needed_per_week"] == progress["projection"]["needed_per_week"] > 0
    assert progress["actual_per_week"] == progress["projection"]["slope_per_week"] > 0
    assert r["links"]["goals"].endswith("/goals")
    again = goal(user, kind="lift", exercise="Back Squat", target_value=90, target_date=day(user, 56))
    assert f"(id {g['id']})" in again["warnings"][0]


def test_mark_hit_or_dropped_and_reopen(user):
    g = goal(user, kind="bodyweight", target_value=70, start_value=80, target_date=day(user, 30), by="web")["goal"]
    assert g["direction"] == "down" and g["start_value"] == 80
    hit = goal(user, goal_id=g["id"], status="hit")
    assert hit["goal"]["status"] == "hit" and hit["goal"]["closed_at"] and hit["progress"] is None
    assert "no longer shows" in hit["note"]
    dropped = goal(user, goal_id=g["id"], status="dropped")
    assert dropped["goal"]["status"] == "dropped"
    reopened = goal(user, goal_id=str(g["id"]), status="open")
    assert reopened["goal"]["status"] == "open" and reopened["goal"]["closed_at"] is None
    assert reopened["progress"]["status"] == "not enough data" and reopened["progress"]["note"]


def test_failures_come_back_together(user):
    bad = goal(user, kind="lift", exercise="Bak Squat", target_value=0, target_date=day(user, -1))
    assert {e["error"] for e in bad["errors"]} == {"unknown_exercise", "invalid_field", "date_not_in_future"}
    assert "Back Squat" in bad["errors"][0]["closest"]
    assert goal(user, kind="bodyweight", target_value=500, target_date=day(user, 0))["error"]
    missing = goal(user, kind="bodyweight")
    assert [e["error"] for e in missing["errors"]] == ["missing_field", "missing_field"]
    assert goal(user)["error"] == "missing_field"
    later = goal(user, kind="calories", target_value=2000, target_date=day(user, 9))
    assert later["error"] == "invalid_field" and "lift, bodyweight" in later["fix"]
    assert goal(user, kind="lift", exercise="Deadlift", target_value=100, target_date=day(user, 9),
                status="hit")["error"] == "invalid_field"
    assert goal(user, kind="lift", exercise="Deadlift", target_value=100, target_date="soon")["error"] \
        == "invalid_date"
    assert goal(user, goal_id=999999, status="hit") == services.get_entry(user.id, "goal", 999999)
    gid = goal(user, kind="bodyweight", target_value=70, start_value=80, target_date=day(user, 9))["id"]
    assert goal(user, goal_id=gid)["error"] == "nothing_to_change"
    assert goal(user, goal_id=gid, kind="lift")["error"] == "cannot_change_kind"
    assert goal(user, goal_id=gid, status="finished")["error"] == "invalid_field"
    assert services.set_goal(user.id, {"goal_id": gid, "status": "hit"}, "robot")["error"] == "invalid_by"


def test_the_start_comes_from_the_log_or_must_be_given(user):
    none_yet = goal(user, kind="bodyweight", target_value=75, target_date=day(user, 30))
    assert none_yet["error"] == "missing_field" and "log_bodyweight" in none_yet["fix"]
    assert "start_value" in none_yet["fix"]
    lift = goal(user, kind="lift", exercise="Deadlift", target_value=150, target_date=day(user, 30))
    assert lift["error"] == "missing_field" and "log_lifts" in lift["fix"]
    given = goal(user, kind="lift", exercise="Deadlift", target_value=150, start_value=100, target_date=day(user, 30))
    assert given["goal"]["start_value"] == 100 and given["goal"]["direction"] == "up"
    assert goal(user, kind="lift", exercise="Deadlift", target_value=150, start_value=-5,
                target_date=day(user, 30))["error"] == "invalid_field"

    weigh(user, 1, 90)
    logged = goal(user, kind="bodyweight", target_value=85, start_value=70, target_date=day(user, 30))
    assert logged["goal"]["start_value"] == 90 and "start_value was ignored" in logged["warnings"][0]


def test_direction_is_set_once_from_the_start(user):
    weigh(user, 1, 90)
    assert goal(user, kind="bodyweight", target_value=85, target_date=day(user, 30))["goal"]["direction"] == "down"
    assert goal(user, kind="bodyweight", target_value=95, target_date=day(user, 30))["goal"]["direction"] == "up"
    same = goal(user, kind="bodyweight", target_value=90, target_date=day(user, 30))
    assert same["error"] == "target_is_start"
    squat(user, 3, 100)
    below = goal(user, kind="lift", exercise="Back Squat", target_value=110, target_date=day(user, 30))
    assert below["error"] == "wrong_direction" and str(epley(100, 5)) in below["message"]


def test_direction_is_never_guessed_again(user):
    weigh(user, 35, 90)
    g = goal(user, kind="bodyweight", target_value=85, target_date=day(user, 30))
    assert g["goal"]["direction"] == "down" and g["goal"]["start_value"] == 90
    # Started at 90, now 84: past an 85 kg target. Lower is better because the goal points down.
    for days_ago, kg in ((28, 89), (21, 88), (14, 86.5), (7, 85.5), (1, 84)):
        weigh(user, days_ago, kg)
    progress = services.get_progress(user.id)["goals"][0]
    assert progress["status"] == "reached" and progress["direction"] == "down"
    # An edit cannot turn it round: a target above the start is refused, the stored direction stays.
    turned = goal(user, goal_id=g["id"], target_value=95)
    assert turned["error"] == "wrong_direction" and "set a new one" in turned["fix"]
    moved = goal(user, goal_id=g["id"], target_value=80)
    assert moved["goal"]["direction"] == "down" and moved["goal"]["start_value"] == 90
    assert moved["progress"]["projection"]["needed_per_week"] < 0
    with db.new_session() as s:
        stored = s.get(Goal, g["id"])
        assert (stored.direction, stored.start_value, stored.target_value) == ("down", 90, 80)


def test_a_harder_target_needs_more_per_week(user):
    for days_ago, kg in ((20, 60), (15, 62.5), (10, 65), (5, 67.5)):
        squat(user, days_ago, kg)
    r = goal(user, kind="lift", exercise="Back Squat", target_value=90, target_date=day(user, 28))
    harder = goal(user, goal_id=r["id"], target_value=100)
    assert harder["progress"]["needed_per_week"] > r["progress"]["needed_per_week"] > 0


def test_warnings_for_an_ignored_exercise_and_a_reopened_past_date(user):
    r = goal(user, kind="bodyweight", exercise="Deadlift", target_value=70, start_value=80, target_date=day(user, 30))
    assert r["goal"]["exercise"] is None and "'Deadlift' was ignored" in r["warnings"][0]
    goal(user, goal_id=r["id"], status="hit")
    with db.session_scope() as s:
        s.get(Goal, r["id"]).target_date = users.today_for(user) - timedelta(days=3)
    reopened = goal(user, goal_id=r["id"], status="open")
    assert reopened["goal"]["status"] == "open" and "has passed" in reopened["warnings"][0]


def test_a_bodyweight_goal_starts_from_the_7_day_average(user):
    for days_ago, kg in ((21, 81.5), (14, 81.2), (3, 81.08), (0, 79.6)):
        weigh(user, days_ago, kg)
    r = goal(user, kind="bodyweight", target_value=80, target_date=day(user, 30))
    # The 7-day average is 80.34, so a target of 80 points down, and the latest weigh-in has reached it.
    assert r["goal"]["start_value"] == 80.34 and r["goal"]["direction"] == "down"
    assert r["progress"]["latest"] == 80.34 and r["progress"]["status"] == "on track"


def test_a_light_session_does_not_pull_the_start_down(user):
    squat(user, 21, 140)
    squat(user, 0, 100)
    r = goal(user, kind="lift", exercise="Back Squat", target_value=180, target_date=day(user, 60))
    assert r["goal"]["start_value"] == epley(140, 5) and r["progress"]["latest"] == epley(140, 5)
    one = services.get_progress(user.id, "Back Squat")["lift"]
    assert one["current_estimated_max_kg"] == epley(140, 5)
    assert one["estimated_max_over_time"][-1]["estimated_max_kg"] == epley(100, 5)
    row = services.get_progress(user.id)["lifts"][0]
    assert row["current_estimated_max_kg"] == row["best_estimated_max_kg"] == epley(140, 5)
    assert "last 28 days" in services.get_progress(user.id)["how"]


def test_the_lift_target_range_comes_from_the_rules(user):
    from app.services.goals import LIFT_MAX
    assert LIFT_MAX == epley(500, 10) == 666.67
    squat(user, 2, 450, reps=10)  # estimated max 600
    assert goal(user, kind="lift", exercise="Back Squat", target_value=650, target_date=day(user, 60))["goal"]
    too_high = goal(user, kind="lift", exercise="Back Squat", target_value=700, target_date=day(user, 60))
    assert too_high["error"] == "invalid_field" and "666.67" in too_high["fix"]
    below = goal(user, kind="lift", exercise="Back Squat", target_value=590, target_date=day(user, 60))
    assert below["error"] == "wrong_direction" and "above 600 and at most 666.67 kg" in below["fix"]

    squat(user, 1, 500, reps=10)  # estimated max 666.67: the top of the range
    top = goal(user, kind="lift", exercise="Back Squat", target_value=650, target_date=day(user, 60))
    assert top["error"] == "wrong_direction" and "No target above 666.67 kg can be set" in top["fix"]
    for number in re.findall(r"\d+(?:\.\d+)?", top["fix"]):
        assert float(number) <= LIFT_MAX, top["fix"]  # the fix never asks for what the rules refuse


def test_a_target_equal_to_the_start_offers_only_what_works(user):
    weigh(user, 1, 90)
    same = goal(user, kind="bodyweight", target_value=90, target_date=day(user, 30))
    assert same["error"] == "target_is_start" and "above or below 90 kg" in same["fix"]
    assert "date" not in same["fix"] and "hold" not in same["fix"]


def test_a_target_date_of_today_is_refused(user):
    r = goal(user, kind="bodyweight", target_value=70, start_value=80, target_date=day(user, 0))
    assert r["error"] == "date_not_in_future" and day(user, 0) in r["message"]


def test_a_closed_goal_is_not_named_in_the_open_goal_warning(user):
    first = goal(user, kind="bodyweight", target_value=70, start_value=80, target_date=day(user, 30))
    goal(user, goal_id=first["id"], status="dropped")
    second = goal(user, kind="bodyweight", target_value=72, start_value=80, target_date=day(user, 30))
    assert second["warnings"] == []


def test_bad_values_give_errors_not_crashes(user):
    base = {"kind": "bodyweight", "target_value": 70, "start_value": 80, "target_date": day(user, 30)}
    for bad_kind in (["lift"], {"k": 1}, 5):
        assert goal(user, **{**base, "kind": bad_kind})["error"] == "invalid_field"
    for field in ("target_value", "start_value"):
        assert goal(user, **{**base, field: 10 ** 400})["error"] == "invalid_field"
    for bad_status in (["hit"], {"s": 1}):
        assert goal(user, **{**base, "status": bad_status})["error"] == "invalid_field"
    nul = goal(user, **{**base, "text": "a\x00b"})
    assert nul["error"] == "invalid_field" and "control characters" in nul["fix"]
    assert goal(user, **{**base, "text": "x" * 2001})["error"] == "invalid_field"
    when = datetime.combine(users.today_for(user) + timedelta(days=30), datetime.min.time())
    made = goal(user, **{**base, "target_date": when})
    assert made["goal"]["target_date"] == when.date().isoformat()
    edit = goal(user, goal_id=made["id"], text="a\x00b")
    assert edit["error"] == "invalid_field"
    assert goal(user, goal_id=made["id"], text="first line\nsecond")["goal"]["text"] == "first line\nsecond"
    assert goal(user, goal_id=made["id"], kind=["x"])["error"] == "cannot_change_kind"


def agrees(numbers: dict) -> None:
    """One meaning of "where the person is now" across a goal's numbers."""
    lower = numbers["direction"] == "down"
    latest, target = numbers["latest"], numbers["target_value"]
    reached = latest <= target if lower else latest >= target
    assert (numbers["status"] == "reached") is reached, numbers
    if reached:
        assert numbers["needed_per_week"] == 0, numbers
    elif numbers["needed_per_week"] is not None:
        assert (numbers["needed_per_week"] < 0) is lower, numbers  # the pace points at the target
    if numbers["projection"]:
        assert numbers["projection"]["latest"] == latest and numbers["projection"]["status"] == numbers["status"]
        assert numbers["projection"]["needed_per_week"] == numbers["needed_per_week"]


def test_a_lift_goal_reached_on_the_28_day_best_after_a_light_session(user):
    deadlift = lambda days_ago, kg: services.log_lifts(user.id, {"date": day(user, -days_ago), "sets": [  # noqa: E731
        {"exercise": "Deadlift", "weight_kg": kg, "reps": 5}]}, "web")
    deadlift(30, 102.86)                                          # estimated max 120
    goal(user, kind="lift", exercise="Deadlift", target_value=145, target_date=day(user, 60))
    for days_ago in (20, 14, 7):
        deadlift(days_ago, 128.57)                                # about 150
    deadlift(1, 94.29)                                            # about 110: a light day
    one = services.get_progress(user.id, "Deadlift")["lift"]
    numbers = one["goal"]
    assert numbers["latest"] == 150.0 and numbers["status"] == "reached" and numbers["needed_per_week"] == 0
    agrees(numbers)
    assert one["current_estimated_max_kg"] == numbers["latest"]
    row = services.get_progress(user.id)["lifts"][0]
    assert row["current_estimated_max_kg"] == numbers["latest"] and row["goal_status"] == "reached"


def test_a_bodyweight_goal_reads_the_7_day_average_not_one_low_weigh_in(user):
    weigh(user, 30, 85)
    goal(user, kind="bodyweight", target_value=80, target_date=day(user, 60))
    for days_ago, kg in ((20, 79.5), (14, 80), (6, 81), (4, 81.5), (2, 82), (0, 79.9)):
        weigh(user, days_ago, kg)
    numbers = services.get_progress(user.id)["goals"][0]
    assert numbers["latest"] == 81.1 and numbers["status"] != "reached" and numbers["needed_per_week"] < 0
    agrees(numbers)


def test_a_bodyweight_goal_at_target_on_the_average_is_reached_despite_a_high_weigh_in(user):
    weigh(user, 30, 85)
    goal(user, kind="bodyweight", target_value=80, target_date=day(user, 60))
    for days_ago, kg in ((20, 78), (14, 78.5), (6, 79), (3, 79.5), (1, 80.2), (0, 80.8)):
        weigh(user, days_ago, kg)
    numbers = services.get_progress(user.id)["goals"][0]
    assert numbers["latest"] == 79.88 and numbers["status"] == "reached" and numbers["needed_per_week"] == 0
    agrees(numbers)


def test_at_or_past_the_target_is_reached_never_behind_even_without_a_line(user):
    squat(user, 3, 100)                                           # one session: too few points for a line
    r = goal(user, kind="lift", exercise="Back Squat", target_value=120, target_date=day(user, 30))
    assert r["progress"]["status"] == "not enough data" and r["progress"]["needed_per_week"] > 0
    squat(user, 1, 105)                                           # 122.5: past the target
    numbers = services.get_progress(user.id, "Back Squat")["lift"]["goal"]
    assert numbers["projection"] is None and numbers["status"] == "reached" and numbers["needed_per_week"] == 0
    assert numbers["note"] is None
    agrees(numbers)
    for g in services.get_progress(user.id)["goals"]:
        agrees(g)


def test_an_edit_compares_with_the_stored_start_not_todays_numbers(user):
    weigh(user, 0, 85)
    g = goal(user, kind="bodyweight", target_value=80, target_date=day(user, 60))
    assert g["goal"]["start_value"] == 85 and g["goal"]["direction"] == "down"
    for days_ago in (3, 2, 1):
        weigh(user, days_ago, 78)                                 # the 7-day average is now 79.75
    assert goal(user, goal_id=g["id"], target_value=82)["progress"]["latest"] == 79.75  # below 85: accepted
    assert goal(user, goal_id=g["id"], target_value=86)["error"] == "wrong_direction"   # above the stored start
    same = goal(user, goal_id=g["id"], target_value=85)
    assert same["error"] == "wrong_direction" and "below 85" in same["fix"]


def test_the_equal_target_message_names_where_the_start_came_from(user):
    given = goal(user, kind="bodyweight", target_value=80, start_value=80, target_date=day(user, 30))
    assert given["error"] == "target_is_start" and "(the start_value given)" in given["message"]
    weigh(user, 10, 81)                                           # nothing in the last 7 days
    older = goal(user, kind="bodyweight", target_value=81, target_date=day(user, 30))
    assert f"(the latest weigh-in, on {day(user, -10)})" in older["message"]
    weigh(user, 2, 82)
    average = goal(user, kind="bodyweight", target_value=82, target_date=day(user, 30))
    assert "(the 7-day average)" in average["message"]


def test_set_goal_query_count(user):
    squat(user, 1, 60)
    count = [0]

    def counter(*_):
        count[0] += 1

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", counter)
    try:
        r = goal(user, kind="lift", exercise="Back Squat", target_value=90, target_date=day(user, 60))
    finally:
        event.remove(engine, "before_cursor_execute", counter)
    assert "error" not in r and count[0] <= 15, count[0]
