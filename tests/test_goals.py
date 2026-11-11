"""set_goal: what a new goal stores, how it is edited and closed, and where it stands."""

import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app import db, formatting, services, users
from app.metrics import epley
from app.models import Goal
from app.services import goals as goal_service


@pytest.fixture
def user(make_user):
    return make_user()


def day(user, offset):
    return (users.today_for(user) + timedelta(days=offset)).isoformat()


def page_day(user, offset):
    """The D12 form a summary gives a date in: 'Sun 10 Jan', with the year when it is not this year's."""
    today = users.today_for(user)
    d = today + timedelta(days=offset)
    return f"{d:%a} {d.day} {d:%b}" + ("" if d.year == today.year else f" {d.year}")


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
    assert g["start_value"] == 78.8 and g["start_date"] == day(user, 0)   # epley(67.5, 5) is 78.75: 1 dp, half up
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
    assert goal(user, goal_id=999999, status="hit") == {**services.get_entry(user.id, "goal", 999999),
                                                        "field": "goal_id"}
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
    # The result carries it at 1 dp: 80.3.
    assert r["goal"]["start_value"] == 80.3 and r["goal"]["direction"] == "down"
    assert r["progress"]["latest"] == 80.3 and r["progress"]["status"] == "on track"


def test_the_ignored_start_is_said_as_the_start_field_prints_it(user):
    """A kg goal's start_value is 1 dp in the result, so the warning says it at 1 dp too."""
    for days_ago, kg in ((3, 80.25), (0, 80.2)):            # the 7-day average: 80.225, kept as 80.23
        weigh(user, days_ago, kg)
    r = goal(user, kind="bodyweight", target_value=75, start_value=90, target_date=day(user, 30))
    assert r["goal"]["start_value"] == 80.2
    assert r["warnings"] == ["start_value was ignored: the goal starts from the logged 80.2 kg (the 7-day average)."]
    squat(user, 1, 102.5)                                    # epley 119.58
    lift = goal(user, kind="lift", exercise="Back Squat", target_value=150, start_value=100, target_date=day(user, 30))
    assert lift["goal"]["start_value"] == 119.6
    assert lift["warnings"] == ["start_value was ignored: the goal starts from the logged 119.6 kg (the best session "
                                "in the last 28 days)."]


def test_a_light_session_does_not_pull_the_start_down(user):
    squat(user, 21, 140)
    squat(user, 0, 100)
    r = goal(user, kind="lift", exercise="Back Squat", target_value=180, target_date=day(user, 60))
    # epley(140, 5) is 163.33 and epley(100, 5) is 116.67: the results carry them at 1 dp.
    assert r["goal"]["start_value"] == 163.3 and r["progress"]["latest"] == 163.3
    one = services.get_progress(user.id, "Back Squat")["lift"]
    assert one["current_estimated_max_kg"] == 163.3
    assert one["estimated_max_over_time"][-1]["estimated_max_kg"] == 116.7
    row = services.get_progress(user.id)["lifts"][0]
    assert row["current_estimated_max_kg"] == row["best_estimated_max_kg"] == 163.3
    assert "last 28 days" in services.get_progress(user.id)["how"]


def test_goal_numbers_stay_unrounded_and_rounded_numbers_rounds_a_copy(user):
    """Section D: the maths keeps its precision; the result rounds a kg goal's start, latest and projection.latest
    to 1 dp, on a copy. cm, bpm and cardio time goals keep the precision they have."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    for days_ago, kg in ((20, 60), (15, 62.5), (10, 65), (5, 67.5)):   # the last is epley 78.75
        squat(user, days_ago, kg)
    weigh(user, 3, 81.08)
    weigh(user, 0, 79.6)                                               # the 7-day average: 80.34
    services.log_measurement(user.id, {"kind": "waist", "value": 84.25}, "claude")
    services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 29.3, "distance_km": 5.1}, "claude")
    for data in ({"kind": "lift", "exercise": "Back Squat", "target_value": 100},
                 {"kind": "bodyweight", "target_value": 75},
                 {"kind": "measurement", "measurement_kind": "waist", "target_value": 80},
                 {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_value": 27}):
        assert "error" not in goal(user, target_date=day(user, 60), **data)
    today = users.today_for(user)
    with db.new_session() as s:
        rows = list(s.scalars(select(Goal).where(Goal.user_id == user.id).order_by(Goal.id)
                              .options(selectinload(Goal.exercise))))
        raw = goal_service.goal_numbers(rows, goal_service.load_numbers(s, user.id, today, rows), today)
    before = [{**n, "projection": dict(n["projection"]) if n["projection"] else None} for n in raw]
    shown = goal_service.rounded_numbers(raw)
    assert raw == before                                               # nothing rounded in place
    lift, weight, waist, run = raw
    assert (lift["start_value"], lift["latest"], lift["projection"]["latest"]) == (78.75, 78.75, 78.75)
    assert (weight["start_value"], weight["latest"]) == (80.34, 80.34)
    lift, weight, waist, run = shown
    assert (lift["start_value"], lift["latest"], lift["projection"]["latest"]) == (78.8, 78.8, 78.8)
    assert (weight["start_value"], weight["latest"]) == (80.3, 80.3)
    assert (waist["start_value"], waist["latest"]) == (84.25, 84.25)            # cm: as it is
    assert (run["start_value"], run["latest"]) == (28.73, 28.73)               # 29.3 / 5.1 x 5, 2 dp: as it is
    assert lift["projection"] is not raw[0]["projection"]
    assert services.get_goals(user.id)["goals"] == shown
    assert [g["start_value"] for g in (services.get_entry(user.id, "goal", n["goal_id"])["entry"] for n in raw)] \
        == [78.8, 80.3, 84.25, 28.73]                                         # goal_dict, the second caller


def test_a_status_that_is_not_one_says_hit_is_the_persons_mark(user):
    """D14: "hit (reached)" goes. Reached is the numbers; hit is the person's own mark."""
    r = goal(user, kind="bodyweight", target_value=70, start_value=80, target_date=day(user, 30), status="won")
    assert r["error"] == "invalid_field" and "(reached)" not in r["fix"]
    assert "Use hit, dropped or open: hit is the person's own mark that a goal is done." in r["fix"]


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
    # The 7-day average is 79.88: 79.9 at 1 dp, still at or below the target of 80.
    assert numbers["latest"] == 79.9 and numbers["status"] == "reached" and numbers["needed_per_week"] == 0
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
    assert goal(user, goal_id=g["id"], target_value=82)["progress"]["latest"] == 79.8  # 79.75 at 1 dp; below 85
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


# ---------------------------------------------------------------- stage 5: four kinds, one shape

SHAPE = {"goal_id", "label", "kind", "exercise", "measurement_kind", "distance_km", "unit", "start_value",
         "start_date", "direction", "target_value", "target_date", "latest", "status", "needed_per_week",
         "actual_per_week", "projected_date", "projection", "note", "is_sample",
         "should_be_today", "ahead_by", "days_ahead", "vs_plan", "plan_line"}   # the plan line's five (E1)


def measure(user, days_ago, kind, value):
    r = services.log_measurement(user.id, {"kind": kind, "value": value, "date": day(user, -days_ago)}, "claude")
    assert "error" not in r, r


def run(user, days_ago, km, minutes, activity="Outdoor Run"):
    r = services.log_cardio(user.id, {"exercise": activity, "minutes": minutes, "distance_km": km,
                                      "date": day(user, -days_ago)}, "claude")
    assert "error" not in r, r


def test_a_measurement_goal_by_hand(user):
    # Waist 90, 89, 88, 87 a week apart: the latest is 87, the line falls 1 cm a week.
    for days_ago, cm in ((21, 90), (14, 89), (7, 88), (0, 87)):
        measure(user, days_ago, "waist", cm)
    r = goal(user, kind="measurement", measurement_kind="Waist", target_value=80, target_date=day(user, 56))
    assert "error" not in r, r
    g, p = r["goal"], r["progress"]
    assert g["measurement_kind"] == "waist" and g["unit"] == "cm" and g["start_value"] == 87
    assert g["direction"] == "down" and g["exercise"] is None
    assert set(p) == SHAPE and p["label"] == "Waist 80 cm" and p["measurement_kind"] == "waist"
    # needed: (80 - 87) / 8 weeks = -0.875 -> -0.88. Actual: -1 a week. 7 cm at 1 a week: 49 days.
    assert p["latest"] == 87 and p["needed_per_week"] == -0.88 and p["actual_per_week"] == -1.0
    assert p["status"] == "on track" and p["projected_date"] == day(user, 49)
    assert p["projection"]["projected_date"] == p["projected_date"]
    assert services.get_goals(user.id)["goals"] == [p]
    assert services.get_progress(user.id)["goals"] == [p]


def test_resting_heart_rate_is_a_whole_number_goal_in_bpm(user):
    measure(user, 3, "resting_hr", 64)
    r = goal(user, kind="measurement", measurement_kind="resting hr", target_value=58, target_date=day(user, 60))
    assert r["goal"]["unit"] == "bpm" and r["progress"]["label"] == "Resting heart rate 58 bpm"
    assert r["progress"]["status"] == "not enough data" and r["progress"]["needed_per_week"] < 0
    half = goal(user, kind="measurement", measurement_kind="resting_hr", target_value=58.5, target_date=day(user, 60))
    assert half["error"] == "invalid_field" and "whole number" in half["message"]
    low = goal(user, kind="measurement", measurement_kind="resting_hr", target_value=10, target_date=day(user, 60))
    assert low["error"] == "invalid_field" and "20 to 250" in low["fix"] and "bpm" in low["fix"]


def test_a_measurement_goal_points_either_way_and_equal_is_refused(user):
    measure(user, 2, "arm", 35)
    assert goal(user, kind="measurement", measurement_kind="arm", target_value=38,
                target_date=day(user, 60))["goal"]["direction"] == "up"
    same = goal(user, kind="measurement", measurement_kind="arm", target_value=35, target_date=day(user, 60))
    assert same["error"] == "target_is_start" and "(the latest arm, on" in same["message"] and "cm" in same["fix"]
    # The latest in the one order, not the lowest or the first.
    measure(user, 1, "arm", 36)
    assert goal(user, kind="measurement", measurement_kind="arm", target_value=40,
                target_date=day(user, 60))["goal"]["start_value"] == 36


@pytest.mark.parametrize("daily", ["steps", "sleep_hours", "water ml"])
def test_a_days_amount_is_not_a_goal(user, daily):
    r = goal(user, kind="measurement", measurement_kind=daily, target_value=10, target_date=day(user, 30))
    assert r["error"] == "invalid_field" and "day's amount" in r["message"]
    assert "waist, hips, chest, arm, thigh, resting_hr" in r["fix"]


def test_a_measurement_goal_needs_its_kind_and_a_start(user):
    missing = goal(user, kind="measurement", target_value=80, target_date=day(user, 30))
    assert missing["error"] == "missing_field" and "measurement_kind" in missing["message"]
    wrong = goal(user, kind="measurement", measurement_kind="wrist", target_value=80, target_date=day(user, 30))
    assert wrong["error"] == "invalid_kind" and "waist" in wrong["fix"]
    none_yet = goal(user, kind="measurement", measurement_kind="hips", target_value=95, target_date=day(user, 30))
    assert none_yet["error"] == "missing_field" and "log_measurement" in none_yet["fix"] and "cm" in none_yet["fix"]
    given = goal(user, kind="measurement", measurement_kind="hips", target_value=95, start_value=100,
                 target_date=day(user, 30))
    assert given["goal"]["start_value"] == 100 and given["goal"]["direction"] == "down"
    out_of_range = goal(user, kind="measurement", measurement_kind="hips", target_value=500, target_date=day(user, 30))
    assert out_of_range["error"] == "invalid_field" and "30 to 300" in out_of_range["fix"]


def test_a_cardio_best_goal_by_hand(user):
    # 5 km runs a week apart: 31, 30.5, 30, then a 10 km in 58 (5:48 a km, so 29 for 5 km). A 3 km run says
    # nothing about a 5 km time and is left out. Times for 5 km: 31, 30.5, 30, 29 -> best in 28 days: 29.
    run(user, 21, 5, 31)
    run(user, 14, 5, 30.5)
    run(user, 7, 5, 30)
    run(user, 2, 3, 15)
    run(user, 0, 10, 58)
    r = goal(user, kind="cardio_best", exercise="outdoor run", distance_km=5, target_value=27, target_date=day(user, 56))
    assert "error" not in r, r
    g, p = r["goal"], r["progress"]
    assert g["unit"] == "min" and g["distance_km"] == 5 and g["direction"] == "down" and g["start_value"] == 29
    assert set(p) == SHAPE and p["label"] == "Outdoor Run 5 km in 27:00" and p["exercise"] == "Outdoor Run"
    assert p["latest"] == 29 and p["projection"]["points_used"] == 4
    # needed: (27 - 29) / 8 = -0.25 a week. The line through (0, 31) (7, 30.5) (14, 30) (21, 29): x mean 10.5,
    # y mean 30.125; sum of products -22.75, sum of squares 245; -22.75 / 245 x 7 = -0.65 a week.
    assert p["needed_per_week"] == -0.25 and p["actual_per_week"] == -0.65 and p["status"] == "on track"
    assert services.get_goals(user.id)["goals"] == [p]


def test_a_cardio_best_now_is_the_best_of_28_days_else_the_latest(user):
    run(user, 40, 5, 25)
    run(user, 35, 5, 33)
    r = goal(user, kind="cardio_best", exercise="Outdoor Run", distance_km=5, target_value=30, target_date=day(user, 30))
    assert r["goal"]["start_value"] == 33   # nothing in 28 days: the latest
    run(user, 3, 5, 32)
    run(user, 1, 5, 34)                    # a slow day does not pull it back
    assert services.get_goals(user.id)["goals"][0]["latest"] == 32
    run(user, 0, 5, 29.5)
    numbers = services.get_goals(user.id)["goals"][0]
    assert numbers["latest"] == 29.5 and numbers["status"] == "reached" and numbers["needed_per_week"] == 0
    agrees(numbers)


def test_deleted_measurements_and_sessions_stay_out_of_now(user):
    measure(user, 3, "waist", 86)
    lower = services.log_measurement(user.id, {"kind": "waist", "value": 84, "date": day(user, -1)}, "claude")["id"]
    services.delete_entry(user.id, "measurement", lower)
    waist = goal(user, kind="measurement", measurement_kind="waist", target_value=80, target_date=day(user, 30))
    assert waist["goal"]["start_value"] == 86 and waist["progress"]["latest"] == 86
    run(user, 5, 5, 30)
    faster = services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 25, "distance_km": 5,
                                           "date": day(user, -1)}, "claude")["id"]
    services.delete_entry(user.id, "cardio_entry", faster)
    best = goal(user, kind="cardio_best", exercise="Outdoor Run", distance_km=5, target_value=27,
                target_date=day(user, 30))
    assert best["goal"]["start_value"] == 30 and best["progress"]["latest"] == 30
    assert [g["latest"] for g in services.get_goals(user.id)["goals"]] == [86, 30]
    services.restore_entry(user.id, "measurement", lower)
    services.restore_entry(user.id, "cardio_entry", faster)
    assert [g["latest"] for g in services.get_goals(user.id)["goals"]] == [84, 25]


def test_a_cardio_best_goal_refusals(user):
    run(user, 2, 5, 30)
    base = {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_date": day(user, 30)}
    assert goal(user, **{**base, "target_value": 31})["error"] == "wrong_direction"   # slower than now
    slower = goal(user, **{**base, "target_value": 30})
    assert slower["error"] == "wrong_direction" and "below 30 and at least 1 min" in slower["fix"]
    assert goal(user, **{**base, "target_value": 0.5})["error"] == "invalid_field"
    assert goal(user, **{**base, "target_value": 700})["error"] == "invalid_field"
    assert goal(user, **{**base, "distance_km": 0.05, "target_value": 20})["error"] == "invalid_field"
    no_distance = goal(user, **{**base, "distance_km": None, "target_value": 25})
    assert no_distance["error"] == "missing_field" and "distance_km" in no_distance["message"]
    no_activity = goal(user, **{**base, "exercise": None, "target_value": 25})
    assert no_activity["error"] == "missing_field" and "Outdoor Run" in no_activity["fix"]
    a_lift = goal(user, **{**base, "exercise": "back squat", "target_value": 25})
    assert a_lift["error"] == "invalid_field"
    assert a_lift["message"] == "Back Squat is a lift; a cardio_best goal needs a cardio activity."
    assert "list_exercises with kind cardio" in a_lift["fix"] and "kind lift" in a_lift["fix"]
    assert goal(user, **{**base, "exercise": "Bak Squat", "target_value": 25})["error"] == "unknown_exercise"
    longer = goal(user, **{**base, "distance_km": 10, "target_value": 55})
    assert longer["error"] == "missing_field" and "10 km or more" in longer["message"] and "log_cardio" in longer["fix"]
    one_dp = goal(user, **{**base, "target_value": 27.26})
    assert one_dp["goal"]["target_value"] == 27.3


def test_a_lift_goal_takes_no_cardio_activity_and_fields_that_do_not_apply_are_warned(user):
    wrong = goal(user, kind="lift", exercise="outdoor run", target_value=100, start_value=80,
                 target_date=day(user, 30))
    assert wrong["error"] == "invalid_field"
    assert wrong["message"] == "Outdoor Run is a cardio activity; a lift goal needs a lift."
    assert "list_exercises with kind lift" in wrong["fix"] and "cardio_best" in wrong["fix"]
    unknown = goal(user, kind="lift", exercise="Outdor Rnu", target_value=100, start_value=80, target_date=day(user, 30))
    assert unknown["error"] == "unknown_exercise"
    r = goal(user, kind="bodyweight", target_value=70, start_value=80, target_date=day(user, 30),
             measurement_kind="waist", distance_km=5)
    assert r["goal"]["measurement_kind"] is None and r["goal"]["distance_km"] is None
    assert any("measurement_kind 'waist' was ignored" in w for w in r["warnings"])
    assert any("distance_km '5' was ignored" in w for w in r["warnings"])


def test_a_weekly_amount_is_a_setting_not_a_goal(user):
    for kind in ("sessions", "cardio_weekly", "calories"):
        r = goal(user, kind=kind, target_value=4, target_date=day(user, 30))
        assert r["error"] == "invalid_field" and "update_settings" in r["fix"]
        assert "lift, bodyweight, measurement, cardio_best" in r["fix"]
    assert "update_settings" not in goal(user, kind="vibes")["fix"]


def test_identity_cannot_change_on_an_edit(user):
    measure(user, 1, "waist", 90)
    run(user, 1, 5, 30)
    waist = goal(user, kind="measurement", measurement_kind="waist", target_value=85, target_date=day(user, 30))["id"]
    best = goal(user, kind="cardio_best", exercise="Outdoor Run", distance_km=5, target_value=28,
                target_date=day(user, 30))["id"]
    assert goal(user, goal_id=waist, measurement_kind="hips", target_value=84)["error"] == "cannot_change_kind"
    # Only the identity sent: one refusal, cannot_change_kind, not "nothing to change" beside it.
    alone = goal(user, goal_id=best, distance_km=10)
    assert [e["error"] for e in alone["errors"]] == ["cannot_change_kind"]
    assert goal(user, goal_id=best, distance_km=5)["error"] == "nothing_to_change"   # the same distance
    assert goal(user, goal_id=best, distance_km=10, target_value=27)["error"] == "cannot_change_kind"
    assert goal(user, goal_id=best, exercise="Treadmill Run", target_value=27)["error"] == "cannot_change_kind"
    # Naming the same ones again is fine.
    assert goal(user, goal_id=waist, measurement_kind="Waist", target_value=84)["goal"]["target_value"] == 84
    assert goal(user, goal_id=best, distance_km=5, target_value=27.5)["goal"]["target_value"] == 27.5
    assert goal(user, goal_id=best, target_value=31)["error"] == "wrong_direction"   # still down from 30
    assert goal(user, goal_id=waist, target_value=500)["error"] == "invalid_field"   # the waist's own range


def test_a_second_open_goal_for_the_same_thing_is_warned(user):
    run(user, 1, 5, 30)
    measure(user, 1, "waist", 90)
    first = goal(user, kind="cardio_best", exercise="Outdoor Run", distance_km=5, target_value=28,
                 target_date=day(user, 30))
    same = goal(user, kind="cardio_best", exercise="Outdoor Run", distance_km=5, target_value=27,
                target_date=day(user, 60))
    assert f"(id {first['id']})" in same["warnings"][0] and "Outdoor Run 5 km in 28:00" in same["warnings"][0]
    # Another distance is another goal. The 5 km run counts for 3 km too: 30 / 5 x 3 = 18 minutes.
    other_distance = goal(user, kind="cardio_best", exercise="Outdoor Run", distance_km=3, start_value=20,
                          target_value=16, target_date=day(user, 30))
    assert other_distance["goal"]["start_value"] == 18
    assert other_distance["warnings"] == ["start_value was ignored: the goal starts from the logged 18 min (the best "
                                          "time in the last 28 days)."]
    goal(user, kind="measurement", measurement_kind="waist", target_value=85, target_date=day(user, 30))
    hips = goal(user, kind="measurement", measurement_kind="hips", start_value=100, target_value=95,
                target_date=day(user, 30))
    assert hips["warnings"] == []
    again = goal(user, kind="measurement", measurement_kind="waist", target_value=84, target_date=day(user, 30))
    assert "Waist 85 cm" in again["warnings"][0]


def test_get_goals_lists_open_goals_and_on_request_closed_ones(user):
    empty = services.get_goals(user.id)
    assert empty["goals"] == [] and empty["closed"] == [] and empty["summary"] == "No open goals."
    assert empty["includes_sample"] is False and "note" not in empty and set(empty["links"]) == {"goals"}
    weigh(user, 1, 80)
    a = goal(user, kind="bodyweight", target_value=75, target_date=day(user, 60))["id"]
    b = goal(user, kind="lift", exercise="Deadlift", start_value=100, target_value=150, target_date=day(user, 60))["id"]
    goal(user, goal_id=b, status="hit")
    r = services.get_goals(user.id)
    assert [g["goal_id"] for g in r["goals"]] == [a] and r["closed"] == []
    assert r["summary"] == f"1 open goal: Body weight 75 kg by {page_day(user, 60)}, not enough data."
    full = services.get_goals(user.id, include_closed=True)
    assert full["closed"] == [{"goal_id": b, "label": "Deadlift 150 kg", "kind": "lift", "status": "hit",
                               "closed_at": full["closed"][0]["closed_at"], "target_value": 150,
                               "target_date": day(user, 60), "is_sample": False}]
    assert full["closed"][0]["closed_at"] and full["summary"].endswith(" 1 closed.")
    assert "Epley" in r["how"] and "projected_date" in r["how"] and "2 years" in r["how"]
    assert services.get_goals(987654)["error"] == "no_account"


def test_goals_of_every_kind_agree_across_set_goal_get_goals_and_get_progress(user):
    squat(user, 14, 100)
    squat(user, 7, 102.5)
    weigh(user, 3, 82)
    measure(user, 2, "chest", 100)
    run(user, 1, 5, 30)
    made = [goal(user, kind="lift", exercise="Back Squat", target_value=140, target_date=day(user, 60)),
            goal(user, kind="bodyweight", target_value=78, target_date=day(user, 60)),
            goal(user, kind="measurement", measurement_kind="chest", target_value=104, target_date=day(user, 60)),
            goal(user, kind="cardio_best", exercise="Outdoor Run", distance_km=5, target_value=28,
                 target_date=day(user, 60))]
    listed = services.get_goals(user.id)["goals"]
    assert [m["progress"] for m in made] == listed == services.get_progress(user.id)["goals"]
    assert [g["kind"] for g in listed] == ["lift", "bodyweight", "measurement", "cardio_best"]
    for numbers in listed:
        assert set(numbers) == SHAPE
        agrees(numbers)


def test_get_goals_query_count_stays_small(user):
    squat(user, 1, 60)
    run(user, 1, 5, 30)
    measure(user, 1, "waist", 90)
    weigh(user, 1, 80)
    for data in ({"kind": "lift", "exercise": "Back Squat", "target_value": 90},
                 {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_value": 28},
                 {"kind": "measurement", "measurement_kind": "waist", "target_value": 85},
                 {"kind": "bodyweight", "target_value": 75}):
        assert "error" not in goal(user, target_date=day(user, 60), **data)
    count = [0]

    def counter(*_):
        count[0] += 1

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", counter)
    try:
        r = services.get_goals(user.id, include_closed=True)
    finally:
        event.remove(engine, "before_cursor_execute", counter)
    assert len(r["goals"]) == 4 and count[0] <= 12, count[0]


def test_points_on_one_day_get_a_note_that_says_so(user):
    for kg in (60, 62.5, 65, 67.5):                                # four sessions, all today
        squat(user, 0, kg)
    r = goal(user, kind="lift", exercise="Back Squat", target_value=100, target_date=day(user, 30))
    assert r["progress"]["projection"] is None
    assert r["progress"]["note"] == "A projection needs points on at least 2 different days."


# ---------------------------------------------------------------- stage 6: the plan line (E1)

PLAN = ("should_be_today", "ahead_by", "days_ahead", "vs_plan", "plan_line")


def plan_of(numbers: dict) -> dict:
    return {k: numbers[k] for k in PLAN}


def weight_goal(user, start: float, now: float) -> tuple[dict, dict]:
    """A body weight goal set today from one weigh-in of `start` (so the plan line today is the start), then
    today's weigh-in replaced by `now`. Returns (the goal's numbers unrounded, as get_goals gives them)."""
    weigh(user, 0, start)
    made = goal(user, kind="bodyweight", target_value=75, target_date=day(user, 60))
    assert "error" not in made and made["goal"]["start_value"] == formatting.rounded(start, "bodyweight_derived"), made
    weigh(user, 0, now)
    raw = next(n for n in services.goal_charts(user.id)["goals"] if n["goal_id"] == made["id"])
    shown = next(n for n in services.get_goals(user.id)["goals"] if n["goal_id"] == made["id"])
    assert raw["should_be_today"] == start and raw["latest"] == now
    return raw, shown


def test_every_kind_carries_the_five_fields_unrounded_and_rounded(user):
    """Each goal set today from what is logged, so its plan line today is its start; then one more entry moves
    now. Unrounded in goal_numbers (goal_charts), rounded at the unit's step in get_goals, plan_line built from
    the unrounded figures."""
    squat(user, 5, 100)                                                   # epley 116.67
    measure(user, 0, "waist", 90)
    measure(user, 0, "resting_hr", 64)
    run(user, 2, 5, 30)
    for data in ({"kind": "lift", "exercise": "Back Squat", "target_value": 130},
                 {"kind": "measurement", "measurement_kind": "waist", "target_value": 85},
                 {"kind": "measurement", "measurement_kind": "resting_hr", "target_value": 58},
                 {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_value": 27}):
        assert "error" not in goal(user, target_date=day(user, 60), **data)
    squat(user, 0, 102.5)                                                 # epley 119.58: the 28-day best
    measure(user, 0, "waist", 89.5)
    measure(user, 0, "resting_hr", 66)
    run(user, 0, 5, 29.7)
    raw = services.goal_charts(user.id)["goals"]
    lift, waist, resting, best = raw
    # Up: 119.58 - 116.67 = 2.91 kg ahead; the line climbs 13.33 / 60 = 0.2222 kg a day: 13.1 days, 13.
    assert lift["should_be_today"] == 116.67 and lift["ahead_by"] == pytest.approx(2.91) and lift["days_ahead"] == 13
    # Down: 90 - 89.5 = 0.5 cm ahead at 5 / 60 cm a day: 6 days. 64 - 66 = -2 bpm at 0.1 a day: -20.
    assert (waist["should_be_today"], waist["ahead_by"], waist["days_ahead"]) == (90, 0.5, 6)
    assert (resting["should_be_today"], resting["ahead_by"], resting["days_ahead"]) == (64, -2, -20)
    # A cardio time: 30 - 29.7 = 0.3 minutes, 18 seconds ahead, at 3 / 60 a day: 6 days.
    assert best["should_be_today"] == 30 and best["ahead_by"] == pytest.approx(0.3) and best["days_ahead"] == 6
    assert [n["plan_line"] for n in raw] == [
        "119.6 kg now against 116.7 kg on the plan line today: 2.9 kg ahead.",
        "89.5 cm now against 90 cm on the plan line today: 0.5 cm ahead.",
        "66 bpm now against 64 bpm on the plan line today: 2 bpm behind.",
        "29:42 now against 30:00 on the plan line today: 18 seconds ahead."]
    shown = services.get_goals(user.id)["goals"]
    assert [plan_of(n) for n in shown] == [
        {**plan_of(lift), "should_be_today": 116.7, "ahead_by": 2.9},
        {**plan_of(waist), "should_be_today": 90.0, "ahead_by": 0.5},
        {**plan_of(resting), "should_be_today": 64.0, "ahead_by": -2.0},
        {**plan_of(best), "should_be_today": 30.0, "ahead_by": 0.3}]
    assert all(isinstance(n["days_ahead"], int) for n in shown)
    assert [n["vs_plan"] for n in shown] == ["ahead", "ahead", "behind", "ahead"]
    # The three results that carry a goal's numbers carry the same five, all together.
    assert shown == services.get_progress(user.id)["goals"]
    assert services.get_progress(user.id, "Back Squat")["lift"]["goal"] == shown[0]
    again = goal(user, goal_id=shown[1]["goal_id"], text="a note")["progress"]
    assert plan_of(again) == plan_of(shown[1])


def test_nothing_logged_has_the_line_and_four_nulls(user):
    r = goal(user, kind="lift", exercise="Deadlift", start_value=100, target_value=150, target_date=day(user, 60))
    assert plan_of(r["progress"]) == {"should_be_today": 100.0, "ahead_by": None, "days_ahead": None,
                                      "vs_plan": None, "plan_line": None}
    raw = services.goal_charts(user.id)["goals"][0]
    assert raw["now_basis"] is None and raw["path"]["actual"] == []
    assert raw["path"]["plan"] == [{"date": day(user, 0), "value": 100.0}, {"date": day(user, 60), "value": 150.0}]


def test_now_is_the_goals_latest_even_when_the_newest_point_is_old(user):
    """The 28-day best on the start day is a session 20 days old, and no point is dated since the start: the plan
    line's now is still latest, never the last point of the series."""
    squat(user, 40, 110)
    squat(user, 20, 90)                                                   # epley 105: the best of 28 days
    r = goal(user, kind="lift", exercise="Back Squat", target_value=130, target_date=day(user, 60))
    p = r["progress"]
    assert p["latest"] == 105.0 and p["vs_plan"] == "on the line" and p["plan_line"] == "On the plan line today, at 105 kg."
    raw = services.goal_charts(user.id)["goals"][0]
    assert raw["path"]["actual"] == [] and raw["now_basis"] == "best_28d"


def test_rule_1_on_the_line_when_the_gap_rounds_to_zero(user):
    raw, shown = weight_goal(user, 80.08, 80.11)                         # 0.03 kg: 0 at 1 dp
    assert plan_of(shown) == {"should_be_today": 80.1, "ahead_by": 0.0, "days_ahead": 0, "vs_plan": "on the line",
                              "plan_line": "On the plan line today, at 80.1 kg."}
    assert raw["ahead_by"] == 0 and raw["days_ahead"] == 0


def test_rule_2_widens_the_sentence_and_ahead_by_never_should_be_today(user):
    raw, shown = weight_goal(user, 80.08, 80.14)                         # both 80.1 kg; the gap 0.06 kg is real
    assert formatting.widen([80.14, 80.08], "bodyweight_derived")
    assert plan_of(shown) == {"should_be_today": 80.1, "ahead_by": -0.06, "days_ahead": -1, "vs_plan": "behind",
                              "plan_line": "80.14 kg now against 80.08 kg on the plan line today: 0.06 kg behind."}
    assert shown["latest"] == 80.1                                         # a standalone field never widens


def test_rule_3_on_the_line_prints_only_the_now_figure(user):
    raw, shown = weight_goal(user, 80.16, 80.14)                         # 80.1 and 80.2 kg, the gap 0.02 kg
    assert formatting.show(80.14, "bodyweight_derived") != formatting.show(80.16, "bodyweight_derived")
    assert plan_of(shown) == {"should_be_today": 80.2, "ahead_by": 0.0, "days_ahead": 0, "vs_plan": "on the line",
                              "plan_line": "On the plan line today, at 80.1 kg."}


def test_the_printed_gap_is_rounded_from_the_real_gap_not_the_printed_figures(user):
    """Section D, accepted: 80.26 against 80.14 prints 80.3 kg and 80.1 kg, and the gap 0.12 prints 0.1 kg, not the
    0.2 the printed figures would give."""
    raw, shown = weight_goal(user, 80.14, 80.26)
    assert raw["ahead_by"] == pytest.approx(-0.12)
    assert shown["plan_line"] == "80.3 kg now against 80.1 kg on the plan line today: 0.1 kg behind."
    assert (shown["ahead_by"], shown["should_be_today"]) == (-0.1, 80.1)


@pytest.mark.parametrize("start, now", [(80.08, 80.13), (80.13, 80.08), (80.03, 80.08), (80.0, 80.05),
                                        (80.1, 80.15), (80.2, 80.25), (80.35, 80.3), (79.95, 80.0)])
def test_at_the_half_step_the_four_move_together(make_user, start, now):
    """A gap of 0.05 kg as floats lands either side of the half step. Whichever side it lands, vs_plan "on the
    line", ahead_by 0 and the on-the-line sentence are all true or all false, and is_zero of the float gap decides.
    On the line, days_ahead is 0. Off it, days_ahead can be 0 too (a gap under half a day of the line's change), so
    nothing is asserted of it there."""
    user = make_user()
    raw, shown = weight_goal(user, start, now)
    on = formatting.is_zero(start - now, "bodyweight_derived")
    holds_together(raw, shown, on)


def holds_together(raw: dict, shown: dict, on: bool) -> None:
    for n in (raw, shown):
        assert (n["vs_plan"] == "on the line") is on, n
        assert (n["ahead_by"] == 0) is on, n
        assert n["plan_line"].startswith("On the plan line today") is on, n
        if on:
            assert n["days_ahead"] == 0, n


def half_step_goal(kind: str, start: float, now: float):
    """An unsaved goal of `kind` on its start day (so its plan line today is `start`), with one point today of
    `now`, and the `loaded` goal_numbers reads: the same path every read takes, with no database."""
    from types import SimpleNamespace as Row
    today = datetime(2026, 11, 10).date()
    exercise = Row(name="Outdoor Run")
    measured = {"cm": "waist", "bpm": "resting_hr"}.get(kind)
    g = Row(id=1, kind="cardio_best" if kind == "time" else "measurement", status="open", is_sample=False,
            exercise=exercise if kind == "time" else None, exercise_id=7 if kind == "time" else None,
            measurement_kind=measured, distance_km=5.0 if kind == "time" else None,
            unit={"cm": "cm", "bpm": "bpm", "time": "min"}[kind], start_value=start, start_date=today,
            target_value=start - 5, target_date=today + timedelta(days=50), direction="down")
    loaded = {"lifts": goal_service.NO_LIFTS, "weigh_ins": [], "sample": False,
              "measurements": {measured: [Row(date=today, value=now, id=1)]} if measured else {},
              "cardio": {7: [Row(date=today, minutes=now, distance_km=5.0, id=1)]} if kind == "time" else {}}
    return g, loaded, today


# For each unit: half its shown step, and starts either side of it from now. A cardio time's now is read at 2 dp
# of a minute from its sessions, so the start (the plan line on the first day) is what moves.
HALF = {"cm": 0.05, "bpm": 0.5, "time": 0.5 / 60}
HALF_STEP_PAIRS = [(kind, now + sign * HALF[kind] * scale, now)
                   for kind, now in (("cm", 90.0), ("bpm", 64.0), ("time", 30.0))
                   for sign in (1, -1) for scale in (0.999, 1.0, 1.001)]
QUANTITY = {"cm": "cm", "bpm": "bpm", "time": "goal_time"}


@pytest.mark.parametrize("kind, start, now", HALF_STEP_PAIRS)
def test_at_the_half_step_cm_bpm_and_a_cardio_time_move_together(kind, start, now):
    """The same rule in cm, bpm and a cardio time, through goal_numbers and rounded_numbers: is_zero of the float gap
    decides on the line, and vs_plan, ahead_by, the sentence and days_ahead move with it."""
    g, loaded, today = half_step_goal(kind, start, now)
    raw = goal_service.goal_numbers([g], loaded, today)[0]
    shown = goal_service.rounded_numbers([raw])[0]
    assert raw["latest"] == now and raw["should_be_today"] == start
    holds_together(raw, shown, formatting.is_zero(start - now, QUANTITY[kind]))


def test_the_cm_bpm_and_time_cases_land_on_both_sides():
    for kind in HALF:
        sides = {formatting.is_zero(start - now, QUANTITY[kind]) for k, start, now in HALF_STEP_PAIRS if k == kind}
        assert sides == {True, False}, kind


def test_the_half_step_cases_land_on_both_sides():
    """The cases above are worth having only if the floats fall both ways."""
    sides = {formatting.is_zero(a - b, "bodyweight_derived")
             for a, b in [(80.08, 80.13), (80.13, 80.08), (80.03, 80.08), (80.0, 80.05), (80.1, 80.15),
                          (80.2, 80.25), (80.35, 80.3), (79.95, 80.0)]}
    assert sides == {True, False}


def test_after_the_target_date_the_line_has_ended(user, monkeypatch):
    weigh(user, 0, 80)
    made = goal(user, kind="bodyweight", target_value=78, target_date=day(user, 5))
    later = db.utcnow() + timedelta(days=10)
    monkeypatch.setattr(db, "utcnow", lambda: later)
    weigh(user, 0, 78.5)
    shown = services.get_goals(user.id)["goals"][0]
    assert shown["goal_id"] == made["id"]
    assert plan_of(shown) == {"should_be_today": 78.0, "ahead_by": -0.5, "days_ahead": None, "vs_plan": "behind",
                              "plan_line": "78.5 kg now against the target of 78 kg: 0.5 kg behind."}
    assert services.goal_charts(user.id)["goals"][0]["line_ended"] is True
    weigh(user, 0, 78.03)                                                 # on the line at the target: no days figure
    assert plan_of(services.get_goals(user.id)["goals"][0]) == {
        "should_be_today": 78.0, "ahead_by": 0.0, "days_ahead": None, "vs_plan": "on the line",
        "plan_line": "78 kg now, at the target."}                         # the line has ended: no "plan line today"


def no_length(user, start_offset: int, target_offset: int, now: float) -> dict:
    """A body weight goal from 80.8 to 78 whose stored dates give a line with no length, as a westward time zone
    move and an edit can leave it: start_date and target_date moved to the offsets given, today unchanged. Then
    today's weigh-in replaced by `now`. Returns get_goals' numbers for it."""
    weigh(user, 0, 80.8)
    made = goal(user, kind="bodyweight", target_value=78, target_date=day(user, 60))
    today = users.today_for(user)
    with db.session_scope() as s:
        row = s.get(Goal, made["id"])
        row.start_date, row.target_date = today + timedelta(days=start_offset), today + timedelta(days=target_offset)
    weigh(user, 0, now)
    return services.get_goals(user.id)["goals"][0]


@pytest.mark.parametrize("start_offset, target_offset", [(1, 1), (1, 0)], ids=["start tomorrow, target tomorrow",
                                                                               "the target before the start"])
def test_a_line_with_no_length_before_its_target_date_compares_with_the_target(user, start_offset, target_offset):
    """The target date has not passed, but the line has no length: the plan figure is the target, there is no days
    figure, and the sentence names the target (metrics.goal_path's ended, worked out once)."""
    shown = no_length(user, start_offset, target_offset, 80.8)
    assert plan_of(shown) == {"should_be_today": 78.0, "ahead_by": -2.8, "days_ahead": None, "vs_plan": "behind",
                              "plan_line": "80.8 kg now against the target of 78 kg: 2.8 kg behind."}
    assert services.goal_charts(user.id)["goals"][0]["line_ended"] is True
    weigh(user, 0, 78.02)                                                 # at the target: on the line, still no days
    assert plan_of(services.get_goals(user.id)["goals"][0]) == {
        "should_be_today": 78.0, "ahead_by": 0.0, "days_ahead": None, "vs_plan": "on the line",
        "plan_line": "78 kg now, at the target."}


def test_a_running_line_is_not_ended(user):
    weigh(user, 0, 80)
    goal(user, kind="bodyweight", target_value=78, target_date=day(user, 5))
    assert services.goal_charts(user.id)["goals"][0]["line_ended"] is False


def test_the_series_is_built_only_for_goal_charts(user, monkeypatch):
    """No MCP goal read pays for the chart's actual line: get_goals, set_goal and get_progress never build it."""
    from app import metrics
    squat(user, 3, 100)
    weigh(user, 1, 80)
    real = metrics.goal_now_series
    calls = []
    monkeypatch.setattr(metrics, "goal_now_series", lambda *a, **k: calls.append(a) or real(*a, **k))
    assert "error" not in goal(user, kind="lift", exercise="Back Squat", target_value=130, target_date=day(user, 60))
    assert "error" not in goal(user, kind="bodyweight", target_value=75, target_date=day(user, 60))
    services.get_goals(user.id)
    services.get_progress(user.id)
    services.get_progress(user.id, "Back Squat")
    assert calls == []
    assert len(services.goal_charts(user.id)["goals"]) == 2 and len(calls) == 2


def test_goal_charts_is_get_goals_unrounded_with_path_and_now_basis(user):
    for days_ago, kg in ((20, 60), (15, 62.5), (10, 65), (5, 67.5)):
        squat(user, days_ago, kg)
    weigh(user, 3, 81.08)
    measure(user, 1, "chest", 100)
    run(user, 1, 5, 30)
    for data in ({"kind": "lift", "exercise": "Back Squat", "target_value": 100},
                 {"kind": "bodyweight", "target_value": 75},
                 {"kind": "measurement", "measurement_kind": "chest", "target_value": 104},
                 {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_value": 27}):
        assert "error" not in goal(user, target_date=day(user, 60), **data)
    charts = services.goal_charts(user.id)
    listed = services.get_goals(user.id)
    assert set(charts) == {"today", "goals", "includes_sample"} and charts["today"] == listed["today"]
    assert charts["includes_sample"] is False
    web_only = ("path", "now_basis", "line_ended")
    bare = [{k: v for k, v in n.items() if k not in web_only} for n in charts["goals"]]
    assert goal_service.rounded_numbers(bare) == listed["goals"]
    assert all(set(n) == SHAPE | set(web_only) for n in charts["goals"])
    assert not any(set(web_only) & set(n) for n in listed["goals"])                # never in an MCP result
    assert [n["line_ended"] for n in charts["goals"]] == [False] * 4
    assert [n["now_basis"] for n in charts["goals"]] == ["best_28d", "average_7d", "latest", "best_28d"]
    lift = charts["goals"][0]
    assert lift["path"]["plan"] == [{"date": day(user, 0), "value": 78.75}, {"date": day(user, 60), "value": 100.0}]
    assert services.goal_charts(987654)["error"] == "no_account"


def test_now_basis_follows_the_rule_the_messages_use(user):
    squat(user, 40, 100)
    weigh(user, 10, 80)
    run(user, 35, 5, 30)
    for data in ({"kind": "lift", "exercise": "Back Squat", "target_value": 150},
                 {"kind": "bodyweight", "target_value": 75},
                 {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_value": 27}):
        assert "error" not in goal(user, target_date=day(user, 60), **data)
    # Nothing in 28 days, nothing in 7: each now is the latest point.
    assert [n["now_basis"] for n in services.goal_charts(user.id)["goals"]] == ["latest", "latest_weigh_in", "latest"]
    again = goal(user, kind="bodyweight", target_value=76, start_value=90, target_date=day(user, 60))
    assert any(w.endswith("(the latest weigh-in, on " + day(user, -10) + ").") for w in again["warnings"])


def test_a_measurement_goal_names_its_kind_by_the_one_table(user):
    assert goal_service.measurement_name("resting_hr") == "Resting heart rate"
    assert goal_service.measurement_name("water_ml") == "Water"
    daily = goal(user, kind="measurement", measurement_kind="water_ml", target_value=2000, target_date=day(user, 30))
    assert daily["message"].startswith("Water cannot be a goal: it is a day's amount")


def test_a_cardio_time_in_rule_2_keeps_minutes_at_2_dp_and_widens_only_the_sentence(user):
    """10.01 km in 60.1 min is 30.02 min for 5 km (30:01.2); then 15.01 km in 90.1 min is 30.01 (30:00.6). Both print
    30:01 at whole seconds and the gap, 0.6 seconds, is real: rule 2. The fields stay minutes at 2 dp."""
    run(user, 0, 10.01, 60.1)
    made = goal(user, kind="cardio_best", exercise="Outdoor Run", distance_km=5, target_value=27,
                target_date=day(user, 60))
    assert made["goal"]["start_value"] == 30.02
    run(user, 0, 15.01, 90.1)
    shown = services.get_goals(user.id)["goals"][0]
    assert plan_of(shown) == {"should_be_today": 30.02, "ahead_by": 0.01, "days_ahead": 0, "vs_plan": "ahead",
                              "plan_line": "30:00.6 now against 30:01.2 on the plan line today: 0.6 seconds ahead."}


def test_a_bpm_goal_in_rule_2_widens_ahead_by_to_1_dp(user, monkeypatch):
    """64 bpm, a goal of 62 over 40 days, read 10 days on: the plan line is 63.5. Both print 64 bpm and the gap of
    0.5 bpm is real (it rounds to 1): rule 2. should_be_today stays whole; ahead_by takes 1 dp; 0.5 over 0.05 a day
    is 10 days behind."""
    measure(user, 0, "resting_hr", 64)
    assert "error" not in goal(user, kind="measurement", measurement_kind="resting_hr", target_value=62,
                               target_date=day(user, 40))
    later = db.utcnow() + timedelta(days=10)
    monkeypatch.setattr(db, "utcnow", lambda: later)
    raw = services.goal_charts(user.id)["goals"][0]
    assert (raw["latest"], raw["should_be_today"], raw["ahead_by"]) == (64, 63.5, -0.5)
    assert plan_of(services.get_goals(user.id)["goals"][0]) == {
        "should_be_today": 64.0, "ahead_by": -0.5, "days_ahead": -10, "vs_plan": "behind",
        "plan_line": "64.0 bpm now against 63.5 bpm on the plan line today: 0.5 bpm behind."}


# ---------------------------------------------------------------- each refusal names its field

def weight_goal_id(user) -> int:
    return goal(user, kind="bodyweight", target_value=70, start_value=80, target_date=day(user, 30))["id"]


def waist_goal_id(user) -> int:
    measure(user, 1, "waist", 90)
    return goal(user, kind="measurement", measurement_kind="waist", target_value=85, target_date=day(user, 30))["id"]


def run_goal_id(user) -> int:
    run(user, 1, 5, 30)
    return goal(user, kind="cardio_best", exercise="Outdoor Run", distance_km=5, target_value=28,
                target_date=day(user, 30))["id"]


def by_date(user, **data):
    return goal(user, target_date=day(user, 30), **data)


def over_the_write_limit(user) -> int:
    """A goal's id, then more write rows in the window than the limit allows."""
    from app.models import CallLog
    from app.services.common import WRITE_LIMIT
    goal_id = weight_goal_id(user)
    with db.session_scope() as s:
        s.add_all(CallLog(user_id=user.id, function="set_goal", started_at=db.utcnow(), ms=1, ok=True,
                          result_bytes=1) for _ in range(WRITE_LIMIT + 1))
    return goal_id


def logged_then(first, then):
    """Log something, then make the call: one lambda for a case that needs a start logged."""
    first()
    return then()


LIFT = {"kind": "lift", "exercise": "Deadlift"}
RUN = {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5}
RESTING = {"kind": "measurement", "measurement_kind": "resting_hr"}
WEIGHT = {"kind": "bodyweight", "target_value": 70, "start_value": 80}

# Every refusal set_goal returns: the call, then each problem's (error, field). None: no field key at all, a
# refusal about the whole request. The Goals page places each one by its field, never by its words.
FIELD_CASES = {
    "kind_missing": (lambda u: goal(u), [("missing_field", "kind")]),
    "kind_a_setting": (lambda u: goal(u, kind="calories"), [("invalid_field", "kind")]),
    "kind_unknown": (lambda u: goal(u, kind="vibes"), [("invalid_field", "kind")]),
    "lift_no_exercise": (lambda u: by_date(u, kind="lift", target_value=100, start_value=80),
                         [("missing_field", "exercise")]),
    "lift_unknown_exercise": (lambda u: by_date(u, kind="lift", exercise="Bak Squat", target_value=100,
                                                start_value=80), [("unknown_exercise", "exercise")]),
    "lift_given_cardio": (lambda u: by_date(u, kind="lift", exercise="outdoor run", target_value=100,
                                            start_value=80), [("invalid_field", "exercise")]),
    "cardio_no_activity": (lambda u: by_date(u, **{**RUN, "exercise": None}, target_value=25, start_value=30),
                           [("missing_field", "exercise")]),
    "cardio_unknown_activity": (lambda u: by_date(u, **{**RUN, "exercise": "Bak Squat"}, target_value=25,
                                                  start_value=30), [("unknown_exercise", "exercise")]),
    "cardio_given_lift": (lambda u: by_date(u, **{**RUN, "exercise": "back squat"}, target_value=25,
                                            start_value=30), [("invalid_field", "exercise")]),
    "cardio_no_distance": (lambda u: by_date(u, **{**RUN, "distance_km": None}, target_value=25, start_value=30),
                           [("missing_field", "distance_km")]),
    "cardio_distance_out_of_range": (lambda u: by_date(u, **{**RUN, "distance_km": 0.05}, target_value=25,
                                                       start_value=30), [("invalid_field", "distance_km")]),
    "measurement_no_kind": (lambda u: by_date(u, kind="measurement", target_value=80),
                            [("missing_field", "measurement_kind")]),
    "measurement_steps": (lambda u: by_date(u, kind="measurement", measurement_kind="steps", target_value=10),
                          [("invalid_field", "measurement_kind")]),
    "measurement_sleep": (lambda u: by_date(u, kind="measurement", measurement_kind="sleep_hours", target_value=8),
                          [("invalid_field", "measurement_kind")]),
    "measurement_water": (lambda u: by_date(u, kind="measurement", measurement_kind="water_ml", target_value=2000),
                          [("invalid_field", "measurement_kind")]),
    "measurement_unknown": (lambda u: by_date(u, kind="measurement", measurement_kind="wrist", target_value=80),
                            [("invalid_kind", "measurement_kind")]),
    "target_out_of_range": (lambda u: by_date(u, **{**WEIGHT, "target_value": 500}),
                            [("invalid_field", "target_value")]),
    "target_not_a_number": (lambda u: by_date(u, **{**WEIGHT, "target_value": "heavy"}),
                            [("invalid_field", "target_value")]),
    "target_not_whole": (lambda u: by_date(u, **RESTING, target_value=55.5, start_value=60),
                         [("invalid_field", "target_value")]),
    "target_missing": (lambda u: by_date(u, kind="bodyweight", start_value=80), [("missing_field", "target_value")]),
    "date_missing": (lambda u: goal(u, **WEIGHT), [("missing_field", "target_date")]),
    "date_not_a_date": (lambda u: goal(u, **WEIGHT, target_date="soon"), [("invalid_date", "target_date")]),
    "date_today": (lambda u: goal(u, **WEIGHT, target_date=day(u, 0)), [("date_not_in_future", "target_date")]),
    "status_not_one": (lambda u: by_date(u, **WEIGHT, status="won"), [("invalid_field", "status")]),
    "status_on_a_new_goal": (lambda u: by_date(u, **WEIGHT, status="hit"), [("invalid_field", "status")]),
    "text_control_character": (lambda u: by_date(u, **WEIGHT, text="a\x00b"), [("invalid_field", "text")]),
    "start_out_of_range": (lambda u: by_date(u, **LIFT, target_value=150, start_value=-5),
                           [("invalid_field", "start_value")]),
    "start_not_whole": (lambda u: by_date(u, **RESTING, target_value=55, start_value=60.5),
                        [("invalid_field", "start_value")]),
    "no_start_lift": (lambda u: by_date(u, **LIFT, target_value=150), [("missing_field", "start_value")]),
    "no_start_weight": (lambda u: by_date(u, kind="bodyweight", target_value=75), [("missing_field", "start_value")]),
    "no_start_measurement": (lambda u: by_date(u, kind="measurement", measurement_kind="hips", target_value=95),
                             [("missing_field", "start_value")]),
    "no_start_cardio": (lambda u: by_date(u, **RUN, target_value=25), [("missing_field", "start_value")]),
    "target_is_start": (lambda u: logged_then(lambda: weigh(u, 1, 90),
                                              lambda: by_date(u, kind="bodyweight", target_value=90)),
                        [("target_is_start", "target_value")]),
    "target_the_wrong_way": (lambda u: logged_then(lambda: squat(u, 2, 450, reps=10),
                                                   lambda: by_date(u, kind="lift", exercise="Back Squat",
                                                                   target_value=590)),
                             [("wrong_direction", "target_value")]),
    "target_past_the_top": (lambda u: logged_then(lambda: squat(u, 1, 500, reps=10),
                                                  lambda: by_date(u, kind="lift", exercise="Back Squat",
                                                                  target_value=650)),
                            [("wrong_direction", "target_value")]),
    "edit_unknown_goal": (lambda u: goal(u, goal_id=999999, status="hit"), [("not_found", "goal_id")]),
    "edit_kind": (lambda u: goal(u, goal_id=weight_goal_id(u), kind="lift"), [("cannot_change_kind", "kind")]),
    "edit_exercise": (lambda u: goal(u, goal_id=run_goal_id(u), exercise="Treadmill Run"),
                      [("cannot_change_kind", "exercise")]),
    "edit_measurement_kind": (lambda u: goal(u, goal_id=waist_goal_id(u), measurement_kind="hips"),
                              [("cannot_change_kind", "measurement_kind")]),
    "edit_distance": (lambda u: goal(u, goal_id=run_goal_id(u), distance_km=10),
                      [("cannot_change_kind", "distance_km")]),
    "edit_target_the_wrong_way": (lambda u: goal(u, goal_id=weight_goal_id(u), target_value=85),
                                  [("wrong_direction", "target_value")]),
    # A bad value on an edit changes nothing, so nothing_to_change, with no field, comes beside it.
    "edit_target_out_of_range": (lambda u: goal(u, goal_id=weight_goal_id(u), target_value=500),
                                 [("invalid_field", "target_value"), ("nothing_to_change", None)]),
    "edit_date_today": (lambda u: goal(u, goal_id=weight_goal_id(u), target_date=day(u, 0)),
                        [("date_not_in_future", "target_date"), ("nothing_to_change", None)]),
    "edit_status_not_one": (lambda u: goal(u, goal_id=weight_goal_id(u), status="finished"),
                            [("invalid_field", "status")]),
    "edit_text_control_character": (lambda u: goal(u, goal_id=weight_goal_id(u), text="a\x00b"),
                                    [("invalid_field", "text"), ("nothing_to_change", None)]),
    "whole_data_not_an_object": (lambda u: services.set_goal(u.id, ["kind"], "claude"), [("invalid_field", None)]),
    "whole_by_unknown": (lambda u: goal(u, by="robot", **WEIGHT), [("invalid_by", None)]),
    "whole_no_account": (lambda u: services.set_goal(u.id + 999999, {"kind": "bodyweight"}, "claude"),
                         [("no_account", None)]),
    "whole_write_limit": (lambda u: goal(u, goal_id=over_the_write_limit(u), status="hit"),
                          [("too_many_writes", None)]),
    "whole_nothing_to_change": (lambda u: goal(u, goal_id=weight_goal_id(u)), [("nothing_to_change", None)]),
}


@pytest.mark.parametrize("case", list(FIELD_CASES), ids=list(FIELD_CASES))
def test_each_refusal_names_the_input_it_is_about(user, case):
    call, expected = FIELD_CASES[case]
    r = call(user)
    problems = r.get("errors") or [r]
    assert [(p["error"], p.get("field")) for p in problems] == expected, r
    for p in problems:
        assert ("field" in p) == (p.get("field") is not None), p  # a whole-request refusal has no field key
    assert r.get("field") == problems[0].get("field") and ("field" in r) == ("field" in problems[0])


def test_problems_together_each_name_their_own_field(user):
    """rules.combine keeps every problem's field in errors. The top level carries the first one's."""
    r = goal(user, kind="lift", exercise="Bak Squat", target_value=0, target_date=day(user, -1))
    assert [(e["error"], e["field"]) for e in r["errors"]] == [
        ("unknown_exercise", "exercise"), ("invalid_field", "target_value"), ("date_not_in_future", "target_date")]
    assert r["field"] == "exercise"
    both = goal(user, kind="bodyweight")
    assert [e["field"] for e in both["errors"]] == ["target_value", "target_date"]


@pytest.mark.parametrize("daily, word", [("steps", "Steps"), ("sleep_hours", "Sleep"), ("water_ml", "Water")])
def test_a_days_amount_names_the_measurement_kind_field_whatever_its_word(user, daily, word):
    """The message starts with the kind's word ("Sleep cannot be a goal"), which names no field. The
    field key carries it."""
    r = goal(user, kind="measurement", measurement_kind=daily, target_value=10, target_date=day(user, 30))
    assert r["message"].startswith(f"{word} cannot be a goal") and r["field"] == "measurement_kind"
