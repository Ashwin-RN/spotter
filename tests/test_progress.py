"""What the person reads back: get_progress, whoami and the exercise list."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select

from app import db, services, users
from app.exercises import LIFTS
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
    # epley(70, 5) is 81.67: every estimated max in the result is at 1 dp, 81.7.
    assert squat["current_estimated_max_kg"] == series[-1]["estimated_max_kg"] == 81.7
    assert squat["best_set"] == {"weight_kg": 70, "reps": 5, "date": day(user, -5), "estimated_max_kg": 81.7}
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
    assert squat["sessions"] == 5 and squat["best_estimated_max_kg"] == 81.7   # epley(70, 5), 81.67, at 1 dp
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
    assert r["sessions"] == 1 and r["current_estimated_max_kg"] == 116.7 and r["bests"] == []   # 116.67 at 1 dp
    services.restore_entry(user.id, "lift_session", gone["id"])
    assert services.get_progress(user.id, "Deadlift")["lift"]["sessions"] == 2


def test_a_session_dated_after_today_is_still_shown(user):
    """A row can sit after today when the person moves their time zone back. It is their latest."""
    later = users.today_for(user) + timedelta(days=1)
    with db.session_scope() as s:
        session = LiftSession(user_id=user.id, date=later, notes="", logged_by="web", dedupe_key="e" * 64)
        session.sets = [LiftSet(exercise_id=s.scalar(select(Exercise.id).where(Exercise.name == "Deadlift")),
                                set_number=1, weight_kg=200, reps=5)]
        s.add(session)
    row = services.get_progress(user.id)["lifts"][0]
    assert row["exercise"] == "Deadlift" and row["last_trained"] == later.isoformat()
    assert row["current_estimated_max_kg"] == 233.3   # epley(200, 5), 233.33, at 1 dp
    one = services.get_progress(user.id, "Deadlift")["lift"]
    assert one["sessions"] == 1 and one["estimated_max_over_time"] == [
        {"date": later.isoformat(), "estimated_max_kg": 233.3}]


def test_a_change_of_time_zone_never_hides_what_was_logged(user, monkeypatch):
    """The reviewer's steps: log on the far side of the date line, then move the zone back a day."""
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc))
    assert services.update_settings(user.id, {"time_zone": "Pacific/Kiritimati"}, "claude")["today"] == "2026-11-10"
    weighed = services.log_bodyweight(user.id, {"kg": 80}, "claude")
    lifted = services.log_lifts(user.id, {"sets": [{"exercise": "Deadlift", "weight_kg": 100, "reps": 5}]}, "claude")
    assert weighed["weigh_in"]["date"] == lifted["session"]["date"] == "2026-11-10"
    assert services.update_settings(user.id, {"time_zone": "Pacific/Pago_Pago"}, "claude")["today"] == "2026-11-09"

    one = services.get_progress(user.id, "Deadlift")["lift"]
    assert one["last_trained"] == "2026-11-10" and one["sessions"] == 1
    assert one["current_estimated_max_kg"] == 116.7 and len(one["estimated_max_over_time"]) == 1   # 116.67, 1 dp
    assert services.get_progress(user.id)["lifts"][0]["last_trained"] == "2026-11-10"
    again = services.log_bodyweight(user.id, {"kg": 79.5, "date": "2026-11-08"}, "claude")
    assert again["recent"][0] == {"date": "2026-11-10", "kg": 80}
    assert again["trend"]["latest"] == {"date": "2026-11-10", "kg": 80}
    assert services.get_entry(user.id, "bodyweight", weighed["id"])["entry"]["date"] == "2026-11-10"
    # A goal starts from those entries too.
    goal = services.set_goal(user.id, {"kind": "bodyweight", "target_value": 75, "target_date": "2026-12-31"}, "web")
    assert goal["goal"]["start_value"] == 79.8 and goal["progress"]["latest"] == 79.8   # 79.75 at 1 dp
    # A write for the date the new zone calls the future is still refused.
    assert services.log_bodyweight(user.id, {"kg": 79, "date": "2026-11-10"}, "claude")["error"] == "date_in_future"
    assert services.log_lifts(user.id, {"date": "2026-11-10", "sets": [
        {"exercise": "Deadlift", "weight_kg": 90, "reps": 5}]}, "claude")["error"] == "date_in_future"


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


def test_real_rows_are_not_sample_and_say_nothing_of_it(user):
    build_squat_history(user)
    for r in (services.get_progress(user.id), services.get_progress(user.id, "Back Squat"),
              services.get_progress(user.id, "Elliptical")):
        assert r["includes_sample"] is False and "sample" not in r.get("note", "")


def test_the_body_weight_chart_draws_the_smoothed_line(user):
    from app.services import body
    from app.services.progress import bodyweight_chart
    for days_ago, kg in ((2, 80), (1, 81), (0, 79)):
        services.log_bodyweight(user.id, {"kg": kg, "date": day(user, -days_ago)}, "web")
    with db.new_session() as s:
        chart = bodyweight_chart(body.weigh_ins(s, user.id, users.today_for(user)), [], users.today_for(user))
    # The same line get_trends reports: 80, 80.1, 79.99, each at 1 dp (D4): 79.99 is 80.0.
    assert [(p["kg"], p["smoothed"]) for p in chart["points"]] == [(80, 80.0), (81, 80.1), (79, 80.0)]
    assert [p["smoothed"] for p in chart["points"]] == [p["smoothed"] for p in
                                                       services.get_trends(user.id)["bodyweight"]["series"]]


def test_the_chart_rounds_its_smoothed_and_average_points_and_keeps_each_weigh_in(user):
    """D4: the chart's smoothed line and 7-day averages at 1 dp; a weigh-in's own kg as entered."""
    from app.services import body
    from app.services.progress import bodyweight_chart
    for days_ago, kg in ((2, 80), (1, 81), (0, 79.45)):
        services.log_bodyweight(user.id, {"kg": kg, "date": day(user, -days_ago)}, "web")
    with db.new_session() as s:
        chart = bodyweight_chart(body.weigh_ins(s, user.id, users.today_for(user)), [], users.today_for(user))
    # Smoothed 80, 80.1, 80.1 + 0.1 x (79.45 - 80.1) = 80.035, kept as 80.03 or 80.04: 80.0 at 1 dp either way.
    # Averages 80, 80.5, (80 + 81 + 79.45) / 3 = 80.15, kept as 80.15: 80.2 at 1 dp, the half away from zero.
    assert [p["smoothed"] for p in chart["points"]] == [80.0, 80.1, 80.0]
    assert [p["kg"] for p in chart["average"]] == [80.0, 80.5, 80.2]
    assert [p["kg"] for p in chart["points"]] == [80, 81, 79.45] and chart["latest"]["kg"] == 79.45


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
        services.get_goals(user.id, include_closed=True), services.get_trends(user.id),
        services.export_data(user.id), services.list_saved_foods(user.id), services.sample_state(user.id),
    ]
    for result in results:
        sendable(result)
        assert json.loads(json.dumps(result)) == result  # no dates, Decimals or tuples left in


def sendable(result) -> None:
    """What the MCP layer needs of a result: valid JSON (no NaN), and every string in it, keys
    included, encodable as UTF-8 as it stands. A lone surrogate anywhere makes the encode fail."""
    json.dumps(result, allow_nan=False)

    def walk(value):
        if isinstance(value, str):
            value.encode("utf-8")
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(key)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(result)


HOSTILE = ["\ud83d", "a\udfffb", "\ud800" * 200, "Back Squat\ud83d", "2026-11-09\ud83d", "\x00\ud83d", "x" * 100_000]


def hostile_calls(user_id, goal_id, entry):
    """Every public function, with each string argument in turn replaced by a hostile value."""
    good_set = {"exercise": "Deadlift", "weight_kg": 90, "reps": 5}
    lifts = {"date": "", "notes": "n", "sets": [good_set], "allow_repeat": True}
    weigh = {"kg": 80, "date": "", "allow_repeat": True}
    new_goal = {"kind": "lift", "exercise": "Deadlift", "target_value": 150, "target_date": "2099-01-01",
                "start_value": 90, "text": "t", "status": "open"}
    for bad in HOSTILE:
        yield "list_exercises kind", lambda: services.list_exercises(bad, None)
        yield "list_exercises category", lambda: services.list_exercises(None, bad)
        yield "get_progress exercise", lambda: services.get_progress(user_id, bad)
        yield "update_settings time_zone", lambda: services.update_settings(user_id, {"time_zone": bad}, "claude")
        yield "update_settings field name", lambda: services.update_settings(user_id, {bad: "UTC"}, "claude")
        yield "update_settings by", lambda: services.update_settings(user_id, {"time_zone": "UTC"}, bad)
        measure = {"kind": "waist", "value": 84, "date": "", "allow_repeat": True}
        for field in measure:
            yield f"log_measurement {field}", lambda f=field: services.log_measurement(
                user_id, {**measure, f: bad}, "claude")
        yield "log_measurement by", lambda: services.log_measurement(user_id, measure, bad)
        for kind, changes in (("bodyweight", {"kg": 80, "date": ""}), ("measurement", {"value": 84}),
                              ("lift_session", {"notes": "n", "date": "", "sets": [good_set]})):
            for field in changes:
                yield f"edit_entry {kind} {field}", lambda k=kind, c=changes, f=field: services.edit_entry(
                    user_id, k, entry, {**c, f: bad}, "claude")
            yield f"edit_entry {kind} field name", lambda k=kind: services.edit_entry(user_id, k, entry, {bad: 1},
                                                                                     "claude")
        yield "edit_entry kind", lambda: services.edit_entry(user_id, bad, entry, {"kg": 80}, "claude")
        yield "edit_entry id", lambda: services.edit_entry(user_id, "bodyweight", bad, {"kg": 80}, "claude")
        yield "edit_entry by", lambda: services.edit_entry(user_id, "bodyweight", entry, {"kg": 80}, bad)
        yield "get_log date", lambda: services.get_log(user_id, bad)
        yield "get_log days", lambda: services.get_log(user_id, None, bad)
        yield "set_claude_can value", lambda: services.set_claude_can(user_id, bad)
        meal = {"description": "dal", "kcal": 180, "meal": "lunch", "estimated": False}
        for field in meal:
            yield f"log_food {field}", lambda f=field: services.log_food(
                user_id, {"entries": [{**meal, f: bad}], "allow_repeat": True}, "claude")
        yield "log_food saved_food_id", lambda: services.log_food(
            user_id, {"entries": [{"saved_food_id": bad, "servings": 1}]}, "claude")
        yield "log_food usda_fdc_id", lambda: services.log_food(
            user_id, {"entries": [{"usda_fdc_id": bad, "grams": 100}]}, "claude")
        yield "log_food entries", lambda: services.log_food(user_id, {"entries": bad}, "claude")
        yield "log_food date", lambda: services.log_food(user_id, {"entries": [meal], "date": bad}, "claude")
        saved = {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}
        for field in saved:
            yield f"save_food {field}", lambda f=field: services.save_food(user_id, {**saved, f: bad}, "claude")
        yield "find_food query", lambda: services.find_food(user_id, bad)
        yield "find_food limit", lambda: services.find_food(user_id, "dal", bad)
        session = {"exercise": "Elliptical", "minutes": 20, "distance_km": 3, "notes": "n"}
        for field in session:
            yield f"log_cardio {field}", lambda f=field: services.log_cardio(
                user_id, {**session, f: bad, "allow_repeat": True}, "claude")
        for update in ("calorie_target_kcal", "add_back_exercise_kcal"):
            yield f"update_settings {update}", lambda u=update: services.update_settings(user_id, {u: bad}, "claude")
        for field in lifts:
            yield f"log_lifts {field}", lambda f=field: services.log_lifts(user_id, {**lifts, f: bad}, "web")
        for field in good_set:
            yield f"log_lifts set {field}", lambda f=field: services.log_lifts(
                user_id, {**lifts, "sets": [{**good_set, f: bad}]}, "web")
        yield "log_lifts by", lambda: services.log_lifts(user_id, lifts, bad)
        for field in weigh:
            yield f"log_bodyweight {field}", lambda f=field: services.log_bodyweight(user_id, {**weigh, f: bad}, "web")
        yield "log_bodyweight by", lambda: services.log_bodyweight(user_id, weigh, bad)
        for field in new_goal:
            yield f"set_goal {field}", lambda f=field: services.set_goal(user_id, {**new_goal, f: bad}, "web")
            yield f"set_goal edit {field}", lambda f=field: services.set_goal(
                user_id, {"goal_id": goal_id, f: bad}, "web")
        for new in ({"kind": "measurement", "measurement_kind": "waist", "target_value": 80, "start_value": 90,
                     "target_date": "2099-01-01"},
                    {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_value": 27,
                     "start_value": 30, "target_date": "2099-01-01"}):
            for field in new:
                yield f"set_goal {new['kind']} {field}", lambda n=new, f=field: services.set_goal(
                    user_id, {**n, f: bad}, "web")
        yield "set_goal edit measurement_kind", lambda: services.set_goal(
            user_id, {"goal_id": goal_id, "measurement_kind": bad, "target_value": 160}, "web")
        yield "set_goal edit distance_km", lambda: services.set_goal(
            user_id, {"goal_id": goal_id, "distance_km": bad, "target_value": 160}, "web")
        yield "get_goals include_closed", lambda: services.get_goals(user_id, bad)
        yield "sample by", lambda: services.add_sample_data(user_id, bad)
        yield "remove sample by", lambda: services.remove_sample_data(user_id, bad)
        yield "set_goal goal_id", lambda: services.set_goal(user_id, {"goal_id": bad, "status": "hit"}, "web")
        yield "set_goal by", lambda: services.set_goal(user_id, new_goal, bad)
        for call in (services.get_entry, services.delete_entry, services.restore_entry):
            yield f"{call.__name__} kind", lambda c=call: c(user_id, bad, entry)
            yield f"{call.__name__} id", lambda c=call: c(user_id, "bodyweight", bad)


def test_every_result_survives_utf8_whatever_the_input(user):
    services.log_lifts(user.id, {"sets": [{"exercise": "Deadlift", "weight_kg": 80, "reps": 5}]}, "web")
    entry = services.log_bodyweight(user.id, {"kg": 80}, "web")["id"]
    goal_id = services.set_goal(user.id, {"kind": "lift", "exercise": "Deadlift", "target_value": 150,
                                          "target_date": day(user, 60)}, "web")["id"]
    calls = 0
    for label, call in hostile_calls(user.id, goal_id, entry):
        result = call()
        calls += 1
        try:
            sendable(result)
        except (UnicodeEncodeError, ValueError) as exc:
            raise AssertionError(f"{label}: {exc}") from exc
        if "error" in result:
            assert result["fix"] and len(json.dumps(result)) < 20_000, label
    assert calls > 500


# ---------------------------------------------------------------- whoami

def test_whoami_returns_the_account_and_asks_for_a_time_zone(user):
    r = services.whoami(user.id)
    assert r["account_id"] == user.id and r["user_id"] == "user_01PROGRESS"
    assert r["email"] == "sam@example.com" and r["name"] == "Sam Example"
    assert r["time_zone"] is None and r["today"] == day(user, 0)
    note = r["time_zone_note"]
    # The rule, not the wording: Claude sets the zone itself, and is never told to send the person anywhere.
    assert "update_settings" in note
    rest = note.replace("update_settings", "").casefold()
    assert "page" not in rest and "settings" not in rest and "/" not in rest, note
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
    assert len(every) == len(LIFTS) + 15
    assert every[0] == {"name": "Back Squat", "kind": "lift", "category": "squat", "met": None, "compendium_code": None}
    lifts = services.list_exercises(kind="lift")["exercises"]
    assert lifts == every[:len(LIFTS)] and all(e["kind"] == "lift" for e in lifts)
    assert len(services.list_exercises(kind="cardio")["exercises"]) == 15
    pulls = services.list_exercises(category="pull")["exercises"]
    assert pulls and all(e["category"] == "pull" for e in pulls)
    assert services.list_exercises(kind="lift", category="carry")["exercises"][0]["name"] == "Farmer's Carry"
    assert services.list_exercises(category="legs")["error"] == "invalid_field"
    assert "squat" in services.list_exercises(category="legs")["fix"]
    assert services.list_exercises(kind="swim")["error"] == "invalid_field"


# ---------------------------------------------------------------- texts name only functions Claude has

# The eighteen functions Claude is offered in stage 5. A later stage extends this list.
OFFERED = ("whoami", "list_exercises", "get_progress", "get_log", "log_lifts", "log_bodyweight", "set_goal",
           "update_settings", "log_measurement", "edit_entry", "restore_entry", "delete_entry",
           "find_food", "log_food", "save_food", "log_cardio", "get_trends", "get_goals")
# Functions that exist but Claude cannot call: every other public service function (get_entry,
# set_claude_can, which is the Settings page's alone, entry_kinds, and stage 5's web-only ones:
# sample data, export, delete account, the Foods page's list).
NOT_OFFERED = sorted({name for name in services.__all__ if callable(getattr(services, name))} - set(OFFERED))


def strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)


def test_texts_from_the_offered_functions_name_no_other_function(user):
    assert NOT_OFFERED and not set(NOT_OFFERED) & set(OFFERED)
    lifts = lambda *rows, **data: services.log_lifts(user.id, {"sets": [  # noqa: E731
        {"exercise": e, "weight_kg": w, "reps": n} for e, w, n in rows], **data}, "claude")
    weigh = lambda kg, **data: services.log_bodyweight(user.id, {"kg": kg, **data}, "web")  # noqa: E731
    goal = lambda **data: services.set_goal(user.id, data, "claude")  # noqa: E731
    results = [services.whoami(user.id), services.list_exercises(), services.list_exercises(kind="swim"),
               services.list_exercises(category="legs"), services.get_progress(user.id),
               services.get_progress(user.id, "Deadlift"), services.get_progress(user.id, "Bak Squat")]

    # Lifts: a first session, new bests, a duplicate, and each error cheap to cause.
    results += [lifts(("Back Squat", 60, 5), date=day(user, -14)), lifts(("Back Squat", 70, 5), date=day(user, -7)),
                lifts(("Back Squat", 80, 5)), lifts(("Back Squat", 80, 5)),
                lifts(("Bak Squat", 60, 5)), lifts(("Back Squat", 501, 31)), lifts(("Back Squat", "x", 5.5)),
                lifts(("Back Squat", 60, 5), date=day(user, 1)), lifts(("Back Squat", 60, 5), date="0001-01-01"),
                lifts(("Back Squat", 60, 5), date="soon"), lifts(("Back Squat", 60, 5), notes="a\x00b"),
                services.log_lifts(user.id, {"sets": []}, "claude"), services.log_lifts(user.id, [], "claude"),
                services.log_lifts(user.id, {"sets": []}, "robot"), services.log_lifts(987654, {}, "web")]
    # Weigh-ins: a first one, a duplicate, a replacement, and the errors.
    results += [weigh(80), weigh(80), weigh(79.5), weigh(81, date=day(user, -3)), weigh(401), weigh(None),
                weigh(70, date=day(user, 1)), weigh(70, date=day(user, -800)), weigh(70, date="soon")]
    # Goals: created, warned, edited, closed, reopened, reached, and refused.
    lift_goal = goal(kind="lift", exercise="Back Squat", target_value=120, target_date=day(user, 60), start_value=1)
    body_goal = goal(kind="bodyweight", target_value=75, target_date=day(user, 60))
    results += [lift_goal, body_goal,
                goal(kind="lift", exercise="Back Squat", target_value=130, target_date=day(user, 60)),
                goal(goal_id=lift_goal["id"], target_value=125), goal(goal_id=lift_goal["id"], status="hit"),
                goal(goal_id=lift_goal["id"], status="open"), goal(goal_id=lift_goal["id"], target_value=90),
                goal(goal_id=lift_goal["id"], target_value=50), goal(goal_id=lift_goal["id"]),
                goal(goal_id=lift_goal["id"], kind="bodyweight"), goal(goal_id=987654, status="hit"),
                goal(kind="bodyweight", target_value=80.25, target_date=day(user, 60)),  # the 7-day average
                goal(kind="lift", exercise="Deadlift", target_value=100, target_date=day(user, 60)),
                goal(kind="lift", exercise="Bak Squat", target_value=0, target_date=day(user, 0)),
                goal(kind="calories"), goal(), goal(kind="bodyweight", status="hit", exercise="Deadlift"),
                goal(kind="bodyweight", target_value=70, start_value=80, target_date=day(user, 9), text="a\x00b"),
                goal(kind="lift", exercise="Deadlift", target_value=110, start_value=100, target_date=day(user, 30)),
                lifts(("Deadlift", 100, 5)),  # an estimated max of 116.67: the Deadlift goal is reached
                services.get_progress(user.id), services.get_progress(user.id, "Back Squat"),
                services.get_progress(user.id, "Deadlift")]
    with db.session_scope() as s:  # a reopened goal whose date has passed
        from app.models import Goal
        s.get(Goal, body_goal["id"]).status = "hit"
        s.get(Goal, body_goal["id"]).target_date = users.today_for(user) - timedelta(days=2)
    results.append(goal(goal_id=body_goal["id"], status="open"))
    # Stage 3: measurements, edits, the log, delete and restore, through their main paths.
    measure = lambda **data: services.log_measurement(user.id, data, "claude")  # noqa: E731
    waist = measure(kind="waist", value=84)
    results += [waist, measure(kind="waist", value=84), measure(kind="waist", value=83), measure(kind="wrist", value=1),
                measure(kind="steps", value=1.5), measure(kind="waist", value=84, date=day(user, 1))]
    other_day = services.log_bodyweight(user.id, {"kg": 79, "date": day(user, -5)}, "web")
    results += [services.edit_entry(user.id, "bodyweight", other_day["id"], {"kg": 78}, "claude"),
                services.edit_entry(user.id, "bodyweight", other_day["id"], {"kg": 78}, "claude"),
                services.edit_entry(user.id, "bodyweight", other_day["id"], {"date": day(user, 0)}, "claude"),
                services.edit_entry(user.id, "bodyweight", other_day["id"], {"notes": "x"}, "claude"),
                services.edit_entry(user.id, "bodyweight", other_day["id"], {}, "claude"),
                services.edit_entry(user.id, "goal", 1, {"kg": 1}, "claude"),
                services.edit_entry(user.id, "lift_session", 987654, {"notes": "x"}, "claude"),
                services.get_log(user.id, days=14), services.get_log(user.id, days=15),
                services.get_log(user.id, date=day(user, 1)),
                services.delete_entry(user.id, "bodyweight", other_day["id"]),
                services.restore_entry(user.id, "bodyweight", other_day["id"]),
                services.restore_entry(user.id, "bodyweight", other_day["id"]),
                services.restore_entry(user.id, "measurement", waist["id"]),
                services.delete_entry(user.id, "goal", 1)]
    # Stage 4: food and cardio, through their main paths and their refusals.
    roti = services.save_food(user.id, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    meal = {"entries": [{"saved_food_id": roti["id"], "servings": 2}, {"description": "dal", "kcal": 180,
                                                                         "estimated": True}]}
    eaten = services.log_food(user.id, meal, "claude")
    results += [roti, services.save_food(user.id, {"name": "roti", "serving_label": "1", "kcal_per_serving": 110},
                                         "claude"),
                eaten, services.log_food(user.id, meal, "claude"),
                services.log_food(user.id, {"entries": [{"saved_food_id": roti["id"], "kcal": 100}]}, "claude"),
                services.log_food(user.id, {"entries": [{"usda_fdc_id": 1, "grams": 100}]}, "claude"),
                services.log_food(user.id, {"entries": [{"description": "x"}]}, "claude"),
                services.find_food(user.id, "roti"), services.find_food(user.id, ""),
                services.edit_entry(user.id, "food_entry", eaten["ids"][1], {"kcal": 150}, "claude"),
                services.edit_entry(user.id, "saved_food", roti["id"], {"name": "x"}, "claude"),
                services.log_cardio(user.id, {"exercise": "Elliptical", "minutes": 20}, "claude"),
                services.log_cardio(user.id, {"exercise": "Back Squat", "minutes": 20}, "claude"),
                services.get_progress(user.id, "Elliptical"),
                services.update_settings(user.id, {"calorie_target_kcal": 2000, "add_back_exercise_kcal": True},
                                         "claude"),
                services.delete_entry(user.id, "saved_food", roti["id"]),
                services.get_log(user.id)]
    # Stage 5: goals of every kind, their refusals, and the two new reads, with and without sample rows.
    services.log_measurement(user.id, {"kind": "resting_hr", "value": 60}, "claude")
    services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5}, "claude")
    results += [goal(kind="measurement", measurement_kind="waist", target_value=80, target_date=day(user, 60)),
                goal(kind="measurement", measurement_kind="resting_hr", target_value=55, target_date=day(user, 60)),
                goal(kind="measurement", measurement_kind="steps", target_value=10000, target_date=day(user, 60)),
                goal(kind="measurement", measurement_kind="hips", target_value=90, target_date=day(user, 60)),
                goal(kind="measurement", target_value=80, target_date=day(user, 60)),
                goal(kind="cardio_best", exercise="Outdoor Run", distance_km=5, target_value=27,
                     target_date=day(user, 60)),
                goal(kind="cardio_best", exercise="Outdoor Run", distance_km=10, target_value=55,
                     target_date=day(user, 60)),
                goal(kind="cardio_best", exercise="Outdoor Run", target_value=27, target_date=day(user, 60)),
                goal(kind="cardio_best", exercise="Outdoor Run", distance_km=5, target_value=35,
                     target_date=day(user, 60)),
                goal(kind="sessions", target_value=4, target_date=day(user, 60)),
                services.get_goals(user.id), services.get_goals(user.id, include_closed=True),
                services.get_trends(user.id)]
    with db.session_scope() as s:  # sample data needs an empty account: a second person's reads
        from app.models import User
        fresh = User(provider="workos", subject="user_texts_sample", created_at=db.utcnow(), updated_at=db.utcnow(),
                     last_seen_at=db.utcnow())
        s.add(fresh)
        s.flush()
        fresh_id = fresh.id
    services.add_sample_data(fresh_id, "web")
    results += [services.get_trends(fresh_id), services.get_goals(fresh_id), services.get_log(fresh_id),
                services.get_progress(fresh_id), services.get_progress(fresh_id, "Outdoor Run"),
                services.find_food(fresh_id, "roti")]
    assert any(r.get("includes_sample") for r in results)

    assert any(r.get("duplicate") for r in results) and any(r.get("replaced") for r in results)
    assert any(g["status"] == "reached" for g in services.get_progress(user.id)["goals"])
    codes = {r["error"] for r in results if "error" in r} | {e["error"] for r in results for e in r.get("errors", [])}
    assert codes >= {
        "invalid_field", "unknown_exercise", "date_in_future", "date_too_old", "invalid_date", "not_found",
        "no_account", "invalid_by", "missing_field", "wrong_direction", "target_is_start", "cannot_change_kind",
        "nothing_to_change", "date_not_in_future", "sets_count_out_of_range", "weight_out_of_range"}
    # Settings: refusals, a no-op, and a change (last, since it moves the person's today).
    settings = lambda data: services.update_settings(user.id, data, "claude")  # noqa: E731
    results += [settings({}), settings({"time_zone": "EST"}), settings({"time_zone": "Kolkata"}),
                settings({"claude_can": "log_delete"}), settings({"colour": "blue"}),
                settings({"time_zone": "UTC"}), settings({"time_zone": "UTC"}), settings({"time_zone": "Asia/Kolkata"})]
    texts = [t for r in results for t in strings_in(r)]
    # Nothing sends the person to a page to set a time zone: Claude sets it with update_settings.
    to_a_page = [t for t in texts if "time zone" in t.casefold()
                 and any(where in t.casefold() for where in ("settings page", "in settings", "links.settings"))]
    assert not to_a_page, to_a_page[:3]
    assert any("can be brought back later" in t for t in texts)
    for name in NOT_OFFERED:
        named = [t for t in texts if name in t]
        assert not named, f"{name} is named, but Claude cannot call it: {named[:3]}"
