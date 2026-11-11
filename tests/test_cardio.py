"""log_cardio: the activity list, the estimate with and without a weigh-in, pace, speed and the week."""

from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app import db, exercises, services, users
from app.metrics import cardio_kcal
from app.models import CallLog, CardioEntry, Exercise
from app.services.cardio import NO_WEIGH_IN
from app.services.common import WRITE_LIMIT


@pytest.fixture
def user(make_user):
    return make_user()


def move(user, exercise="Treadmill Walk", minutes=30, by="claude", **data):
    return services.log_cardio(user.id, {"exercise": exercise, "minutes": minutes, **data}, by)


def rows(user_id):
    with db.new_session() as s:
        return list(s.scalars(select(CardioEntry).where(CardioEntry.user_id == user_id).order_by(CardioEntry.id)))


# ---------------------------------------------------------------- the activity list

def test_the_cardio_rows_are_synced_with_met_and_code(db_session):
    cardio = services.list_exercises(kind="cardio")["exercises"]
    assert [e["name"] for e in cardio] == [name for name, *_ in exercises.CARDIO] and len(cardio) == 15
    walk = next(e for e in cardio if e["name"] == "Treadmill Walk")
    assert walk == {"name": "Treadmill Walk", "kind": "cardio", "category": "walk", "met": 3.8,
                    "compendium_code": "17355"}
    assert all(e["met"] and len(e["compendium_code"]) == 5 for e in cardio)
    assert {e["category"] for e in cardio} <= set(exercises.CARDIO_CATEGORIES)
    lifts = services.list_exercises(kind="lift")["exercises"]
    assert all(e["met"] is None and e["compendium_code"] is None for e in lifts)
    assert services.list_exercises(category="run")["exercises"][0]["name"] == "Treadmill Run"


def test_the_sync_adds_cardio_once(db_session):
    assert exercises.sync(db_session) == 0
    db_session.commit()
    with db.session_scope() as s:
        s.execute(delete(Exercise).where(Exercise.kind == "cardio"))
    with db.session_scope() as s:
        assert exercises.sync(s) == 15
    with db.session_scope() as s:
        assert exercises.sync(s) == 0
        assert len(list(s.scalars(select(Exercise).where(Exercise.kind == "cardio")))) == 15


def test_a_lift_name_is_not_a_cardio_activity(user):
    r = move(user, "Back Squat")
    assert r["error"] == "unknown_exercise" and "Back Squat" not in r["closest"]
    assert all(name in r["fix"] for name, *_ in exercises.CARDIO)
    close = move(user, "treadmil run")
    assert close["error"] == "unknown_exercise" and "Treadmill Run" in close["closest"]
    assert move(user, None)["error"] == "missing_field" and "Treadmill Walk" in move(user, None)["fix"]
    assert move(user, " treadmill  walk ")["entry"]["exercise"] == "Treadmill Walk"


# ---------------------------------------------------------------- the estimate

def test_with_a_weigh_in_the_estimate_uses_the_compendium_met_and_body_weight_now(user):
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    r = move(user, "Treadmill Walk", 30, machine_kcal=150)
    assert r["estimate"] == {"kcal": cardio_kcal(3.8, 80, 30), "weight_kg": 80.0, "met": 3.8,
                             "source": "2024 Adult Compendium of Physical Activities, code 17355"}
    assert r["estimate"]["kcal"] == 160 and r["machine_kcal"] == 150 and "Treadmill Run" in r["note"]
    assert set(r) == {"kind", "id", "duplicate", "entry", "pace", "estimate", "machine_kcal", "week", "summary",
                      "links", "note"}
    row = rows(user.id)[0]
    assert (row.estimated_kcal, row.estimate_weight_kg, row.machine_kcal) == (160, 80, 150)
    assert "about 160 kcal estimated" in r["summary"] and "the machine said 150 kcal" in r["summary"]


def test_the_estimate_weight_is_rounded_in_the_result_and_nowhere_else(user):
    """A cardio estimate's weight_kg at 1 dp in the result (section D); the stored column keeps the weight the
    estimate was worked out from, so it can be worked out again."""
    today = users.today_for(user)
    services.log_bodyweight(user.id, {"kg": 80.2, "date": (today - timedelta(days=2)).isoformat()}, "web")
    services.log_bodyweight(user.id, {"kg": 80.3}, "web")                      # the 7-day average is 80.25
    r = move(user, "Treadmill Walk", 30)
    assert r["estimate"]["weight_kg"] == 80.3                                    # 80.25 at 1 dp, half up
    assert rows(user.id)[0].estimate_weight_kg == 80.25
    again = services.get_entry(user.id, "cardio_entry", r["id"])["entry"]["estimate"]
    assert again["weight_kg"] == 80.3 and again["kcal"] == cardio_kcal(3.8, 80.25, 30)
    back = move(user, "Outdoor Walk", 30, date=(today - timedelta(days=1)).isoformat())
    assert "from body weight now, 80.3 kg." in back["summary"]


def test_the_weight_is_the_7_day_average(user):
    today = users.today_for(user)
    services.log_bodyweight(user.id, {"kg": 80, "date": (today - timedelta(days=2)).isoformat()}, "web")
    services.log_bodyweight(user.id, {"kg": 82}, "web")
    assert move(user, "Outdoor Walk", 60)["estimate"]["weight_kg"] == 81.0


def test_without_a_weigh_in_there_is_no_estimate_and_a_note(user):
    r = move(user, "Treadmill Walk", 30, machine_kcal=150)
    assert r["estimate"] is None and r["machine_kcal"] == 150 and "No weigh-in yet" in r["note"]
    assert rows(user.id)[0].estimated_kcal is None and rows(user.id)[0].estimate_weight_kg is None


def test_the_formula():
    assert cardio_kcal(3.8, 80, 30) == 160         # 3.8 x 3.5 x 80 / 200 x 30 = 159.6
    assert cardio_kcal(9.3, 70, 28) == 319         # 318.99
    assert cardio_kcal(5.0, 100, 1) == 9           # 8.75


# ---------------------------------------------------------------- pace and speed

def test_pace_and_speed_from_a_distance(user):
    r = move(user, "Treadmill Run", 28, distance_km=5)
    assert r["pace"] == {"min_per_km": "5:36", "speed_kmh": 10.71} and r["entry"]["speed_kmh"] == 10.71
    given = move(user, "Treadmill Run", 30, distance_km=5, speed_kmh=10.5)
    assert given["pace"] == {"min_per_km": "6:00", "speed_kmh": 10.5}
    assert move(user, "Elliptical", 20)["pace"] is None


def test_a_worked_out_speed_must_be_believable(user):
    r = move(user, "Outdoor Run", 1, distance_km=300)
    assert r["error"] == "invalid_field" and "18,000 km/h" in r["message"] and rows(user.id) == []


@pytest.mark.parametrize("field,bad", [
    ("minutes", 0), ("minutes", 601), ("distance_km", 0), ("distance_km", 301), ("incline_pct", 41),
    ("speed_kmh", 0.05), ("speed_kmh", 61), ("avg_hr", 29), ("avg_hr", 120.5), ("machine_kcal", 0),
    ("machine_kcal", 10.5), ("minutes", "lots"), ("minutes", 10 ** 400),
])
def test_ranges(user, field, bad):
    data = {"minutes": 30, field: bad}
    r = services.log_cardio(user.id, {"exercise": "Elliptical", **data}, "claude")
    assert r["error"] == "invalid_field", r
    assert rows(user.id) == []


def test_minutes_keep_one_decimal_and_other_checks(user):
    assert move(user, "Elliptical", "25.25")["entry"]["minutes"] == round(25.25, 1)
    assert services.log_cardio(user.id, {"exercise": "Elliptical"}, "claude")["error"] == "missing_field"
    assert move(user, notes="a\x00b")["error"] == "invalid_field"
    assert move(user, color="blue")["error"] == "invalid_field"
    assert move(user, date="2999-01-01")["error"] == "date_in_future"
    assert services.log_cardio(user.id, {"exercise": "Elliptical", "minutes": 5}, "robot")["error"] == "invalid_by"
    assert services.log_cardio(987654, {"exercise": "Elliptical", "minutes": 5}, "web")["error"] == "no_account"


# ---------------------------------------------------------------- the week, the duplicate, the limit

def test_the_week_is_the_7_days_ending_today_against_the_target(make_user):
    person = make_user(target_cardio_minutes_per_week=150)
    today = users.today_for(person)
    move(person, minutes=40, date=(today - timedelta(days=7)).isoformat())   # outside the week
    move(person, minutes=30, date=(today - timedelta(days=6)).isoformat())
    r = move(person, "Stationary Bike", 20)
    assert r["week"] == {"minutes": 50.0, "target_minutes": 150}


def test_the_same_session_within_ten_minutes_is_a_duplicate(user):
    first = move(user, distance_km=3)
    again = move(user, distance_km=3, by="web")
    assert again["duplicate"] is True and again["id"] == first["id"] and "allow_repeat" in again["note"]
    assert move(user, distance_km=3.5)["duplicate"] is False
    assert move(user, distance_km=3, allow_repeat=True)["duplicate"] is False
    assert len(rows(user.id)) == 3


def test_cardio_is_limited_and_locks_first(user):
    from sqlalchemy import event
    from sqlalchemy.dialects import sqlite
    from app.services.common import lock_statement
    statements = []
    engine = db.get_engine()
    record = lambda *a: statements.append(" ".join(a[2].split()))  # noqa: E731
    event.listen(engine, "before_cursor_execute", record)
    try:
        move(user)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert statements[0] == " ".join(str(lock_statement("sqlite", user.id).compile(dialect=sqlite.dialect())).split())
    with db.session_scope() as s:
        s.add_all(CallLog(user_id=user.id, function="log_cardio", started_at=db.utcnow(), ms=1, ok=True,
                          result_bytes=1) for _ in range(WRITE_LIMIT + 1))
    assert move(user, minutes=45)["error"] == "too_many_writes" and len(rows(user.id)) == 1


# ---------------------------------------------------------------- progress for an activity

def test_progress_for_an_activity_lists_its_sessions_and_the_week(make_user):
    person = make_user(target_cardio_minutes_per_week=120)
    services.log_bodyweight(person.id, {"kg": 70}, "web")
    today = users.today_for(person)
    move(person, "Outdoor Run", 30, distance_km=5, date=(today - timedelta(days=3)).isoformat())
    newest = move(person, "Outdoor Run", 25, distance_km=4)
    move(person, "Elliptical", 15)
    r = services.get_progress(person.id, "outdoor run")
    assert r["cardio"]["exercise"] == "Outdoor Run" and [x["id"] for x in r["cardio"]["sessions"]][0] == newest["id"]
    first = r["cardio"]["sessions"][0]
    assert set(first) == {"id", "date", "minutes", "distance_km", "pace", "estimate", "machine_kcal"}
    assert first["pace"]["min_per_km"] == "6:15" and first["estimate"]["source"].endswith("code 12050")
    assert r["cardio"]["week"] == {"minutes": 70.0, "target_minutes": 120}
    assert "Compendium" in r["how"]
    assert "note" in services.get_progress(person.id, "Hiking")
    assert "lift" in services.get_progress(person.id, "Back Squat")      # a lift is unchanged



# ---------------------------------------------------------------- review fixes

@pytest.mark.parametrize("speed,ok", [(12.0, True), (8.0, True), (12.1, False), (7.9, False), (30, False)])
def test_a_stated_speed_must_be_within_20_percent_of_distance_over_minutes(user, speed, ok):
    r = move(user, "Treadmill Run", 30, distance_km=5, speed_kmh=speed)    # 5 km in 30 minutes is 10 km/h
    if ok:
        assert r["entry"]["speed_kmh"] == speed
    else:
        assert r["error"] == "invalid_field" and f"speed_kmh {speed:g}" in r["message"] and "10 km/h" in r["message"]
        assert rows(user.id) == []


def test_an_edited_speed_is_held_to_the_same_tolerance(user):
    run = move(user, "Treadmill Run", 30, distance_km=5)
    assert services.edit_entry(user.id, "cardio_entry", run["id"], {"speed_kmh": 30}, "claude")["error"] \
        == "invalid_field"
    assert services.edit_entry(user.id, "cardio_entry", run["id"], {"speed_kmh": 11}, "claude")["changed"] \
        == ["speed_kmh"]


def test_a_back_dated_estimate_says_it_used_body_weight_now(user):
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    earlier = move(user, "Outdoor Walk", 30, date=(users.today_for(user) - timedelta(days=3)).isoformat())
    assert "about 160 kcal estimated from body weight now, 80 kg." in earlier["summary"]
    today = move(user, "Outdoor Walk", 31)
    assert "estimated" in today["summary"] and "body weight now" not in today["summary"]


def test_the_sync_brings_a_corrected_met_to_an_existing_row(db_session, monkeypatch):
    corrected = [(n, c, 4.1 if n == "Treadmill Walk" else m, "17999" if n == "Treadmill Walk" else code)
                 for n, c, m, code in exercises.CARDIO]
    monkeypatch.setattr(exercises, "CARDIO", corrected)
    assert exercises.sync(db_session) == 0
    db_session.commit()
    walk = db_session.scalar(select(Exercise).where(Exercise.name == "Treadmill Walk"))
    assert (walk.met, walk.compendium_code, walk.category, walk.kind) == (4.1, "17999", "walk", "cardio")
    before = [(e.id, e.name, e.met, e.compendium_code) for e in db_session.scalars(select(Exercise).order_by(Exercise.id))]
    assert exercises.sync(db_session) == 0
    db_session.commit()
    assert [(e.id, e.name, e.met, e.compendium_code) for e in db_session.scalars(
        select(Exercise).order_by(Exercise.id))] == before



def test_a_treadmill_walk_names_its_sibling_and_its_estimate(user):
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    walk = move(user, "Treadmill Walk", 30)
    run_kcal = cardio_kcal(9.3, 80, 30)                                               # 391
    assert walk["note"] == (f"Logged as Treadmill Walk. Treadmill Run is the other treadmill activity: its "
                            f"estimate would be about {run_kcal} kcal instead of 160 kcal. edit_entry changes it.")
    run = move(user, "Treadmill Run", 30)
    assert run["note"].endswith(f"Treadmill Walk is the other treadmill activity: its estimate would be about "
                                f"160 kcal instead of {run_kcal} kcal. edit_entry changes it.")


def test_several_siblings_are_all_named_with_their_estimates(user):
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    note = move(user, "Outdoor Walk", 30)["note"]
    assert note == (f"Logged as Outdoor Walk. The other outdoor activities are Outdoor Run at about "
                    f"{cardio_kcal(9.3, 80, 30)} kcal and Outdoor Cycling at about {cardio_kcal(7.0, 80, 30)} kcal. "
                    "edit_entry changes it.")


def test_no_sibling_no_note_and_a_duplicate_keeps_its_own_note(user):
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    assert "note" not in move(user, "Swimming", 30)
    first = move(user, "Treadmill Walk", 20)
    assert "Treadmill Run" in first["note"]
    again = move(user, "Treadmill Walk", 20)
    assert again["duplicate"] is True and again["note"].startswith("This cardio session was already logged")
    assert "Treadmill Run" not in again["note"]


def test_with_no_weigh_in_the_sibling_note_gives_the_mets(make_user):
    person = make_user()
    walk = move(person, "Treadmill Walk", 30)["note"]
    assert walk == (f"{NO_WEIGH_IN} Logged as Treadmill Walk. Treadmill Run is the other treadmill activity, "
                    "with a higher estimate: MET 9.3 against 3.8. edit_entry changes it.")
    run = move(person, "Treadmill Run", 30)["note"]
    assert run.endswith("Treadmill Walk is the other treadmill activity, with a lower estimate: "
                        "MET 3.8 against 9.3. edit_entry changes it.")
    several = move(person, "Outdoor Walk", 30)["note"]
    assert several.endswith("The other outdoor activities are Outdoor Run at MET 9.3 and Outdoor Cycling at MET 7, "
                            "against MET 3.8 for this one. edit_entry changes it.")
