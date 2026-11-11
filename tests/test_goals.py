"""set_goal: what a new goal stores, how it is edited and closed, and where it stands."""

import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app import db, services, users
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
         "actual_per_week", "projected_date", "projection", "note", "is_sample"}


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
