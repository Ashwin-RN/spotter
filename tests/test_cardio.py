"""log_cardio: the activity list, the estimate with and without a weigh-in, pace, speed and the week."""

from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app import db, exercises, formatting, services, users
from app.metrics import cardio_kcal
from app.models import CallLog, CardioEntry, Exercise, User
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


def carrying_both(result, out=None) -> list[dict]:
    """Every dict at any depth that carries estimate_weight_kg beside an estimate with a weight_kg."""
    out = [] if out is None else out
    if isinstance(result, dict):
        if "estimate_weight_kg" in result and isinstance(result.get("estimate"), dict):
            out.append(result)
        for value in result.values():
            carrying_both(value, out)
    elif isinstance(result, list):
        for value in result:
            carrying_both(value, out)
    return out


def test_estimate_weight_kg_is_rounded_with_the_estimate_in_every_result(user):
    """Section D: a cardio result carries the stored weight twice, as estimate_weight_kg and estimate.weight_kg.
    Both are the same 1 dp figure in every result; the stored column keeps 80.25."""
    today = users.today_for(user)
    services.log_bodyweight(user.id, {"kg": 80.2, "date": (today - timedelta(days=2)).isoformat()}, "web")
    services.log_bodyweight(user.id, {"kg": 80.3}, "web")                      # the 7-day average is 80.25
    logged = move(user, "Treadmill Walk", 30)
    entry_id = logged["id"]
    results = [logged, services.get_entry(user.id, "cardio_entry", entry_id),
               services.get_log(user.id, days=1),
               services.edit_entry(user.id, "cardio_entry", entry_id, {"minutes": 35}, "web"),
               services.delete_entry(user.id, "cardio_entry", entry_id),
               services.restore_entry(user.id, "cardio_entry", entry_id)]
    found = [d for r in results for d in carrying_both(r)]
    assert len(found) >= len(results)
    assert all(d["estimate_weight_kg"] == d["estimate"]["weight_kg"] == 80.3 for d in found), found
    assert rows(user.id)[0].estimate_weight_kg == 80.25                        # the stored column is untouched


def test_without_an_estimate_estimate_weight_kg_is_null_too(user):
    r = move(user, "Treadmill Walk", 30)
    assert r["estimate"] is None and r["entry"]["estimate_weight_kg"] is None


# ---------------------------------------------------------------- get_cardio: an activity's bests (stage 6, E3)

ONE_KEYS = {"today", "exercise", "sessions", "bests", "week"}
SESSION_KEYS = {"id", "date", "minutes", "distance_km", "pace", "pace_min_per_km", "estimate", "machine_kcal", "notes"}
SUMMARY_KEYS = {"today", "activities"}


def ago(user, days):
    return (users.today_for(user) - timedelta(days=days)).isoformat()


def ran(user, days_ago, minutes, km=None, exercise="Outdoor Run", **data):
    sent = {"date": ago(user, days_ago), "allow_repeat": True, **data}
    if km is not None:
        sent["distance_km"] = km
    r = move(user, exercise, minutes, **sent)
    assert "error" not in r, r
    return r["id"]


def runs(user):
    """Six live Outdoor Runs, one deleted. By hand: paces 6.2, 6.0, 6.22, 5.0, none, 5.0 min per km."""
    ids = {"A": ran(user, 20, 31, 5), "B": ran(user, 15, 60, 10), "C": ran(user, 10, 28, 4.5),
           "D": ran(user, 8, 25, 5, notes="hilly"), "E": ran(user, 5, 40), "F": ran(user, 3, 50, 10)}
    gone = ran(user, 2, 20, 10)                                  # 2:00 a km, every best: but deleted
    services.delete_entry(user.id, "cardio_entry", gone)
    return ids


def test_every_best_by_hand_with_its_ties(user, make_user):
    ids = runs(user)
    other = make_user()
    ran(other, 1, 15, 10)                                         # another person's faster run never counts
    r = services.get_cardio(user.id, "outdoor run")
    assert set(r) == ONE_KEYS and r["exercise"] == "Outdoor Run" and r["today"] == ago(user, 0)
    # Fastest pace: D and F both 5:00 a km; the tie goes to D, the earlier in the one order. E has no distance.
    assert r["bests"]["fastest_pace"] == {"pace": "5:00", "pace_min_per_km": 5.0, "date": ago(user, 8),
                                          "entry_id": ids["D"]}
    # Longest: B and F both 10 km; B is earlier.
    assert r["bests"]["longest"] == {"distance_km": 10.0, "date": ago(user, 15), "entry_id": ids["B"]}
    # Times, the goal rule: a session at or over the distance, its pace x the distance. 1 km: D and F tie at 5:00,
    # D first. 5 km: A 31, B 30, D 25, F 25: D first. 10 km: B 60, F 50. C (4.5 km) is in the 1 km best only.
    assert r["bests"]["times"] == [
        {"distance_km": 1.0, "minutes": 5.0, "date": ago(user, 8), "entry_id": ids["D"]},
        {"distance_km": 5.0, "minutes": 25.0, "date": ago(user, 8), "entry_id": ids["D"]},
        {"distance_km": 10.0, "minutes": 50.0, "date": ago(user, 3), "entry_id": ids["F"]}]
    assert [s["id"] for s in r["sessions"]] == [ids[k] for k in "FEDCBA"]         # newest first
    assert all(set(s) == SESSION_KEYS for s in r["sessions"])
    no_distance = next(s for s in r["sessions"] if s["id"] == ids["E"])
    assert no_distance["pace"] is None and no_distance["pace_min_per_km"] is None and no_distance["distance_km"] is None
    hilly = next(s for s in r["sessions"] if s["id"] == ids["D"])
    assert hilly["notes"] == "hilly" and next(s for s in r["sessions"] if s["id"] == ids["A"])["notes"] == ""
    # pace is the m:ss of pace_min_per_km, through the formatter's one m:ss function; the float is unrounded.
    c = next(s for s in r["sessions"] if s["id"] == ids["C"])
    assert c["pace_min_per_km"] == 28 / 4.5 and c["pace"] == "6:13"
    assert all(s["pace"] == formatting.show(s["pace_min_per_km"], "pace") for s in r["sessions"] if s["pace"])
    assert "note" not in r


def test_one_session_reaches_only_the_distances_it_covers(user):
    only = ran(user, 1, 6.5, 1.2)
    bests = services.get_cardio(user.id, "Outdoor Run")["bests"]
    assert bests["times"] == [{"distance_km": 1.0, "minutes": 5.42, "date": ago(user, 1), "entry_id": only}]
    assert bests["longest"] == {"distance_km": 1.2, "date": ago(user, 1), "entry_id": only}
    ran(user, 0, 30, exercise="Elliptical")                         # no distance: no best at all
    e = services.get_cardio(user.id, "Elliptical")
    assert e["bests"] == {"fastest_pace": None, "longest": None, "times": []} and len(e["sessions"]) == 1


def test_a_tie_on_one_day_goes_to_the_session_logged_first(user):
    first = ran(user, 2, 30, 5)
    ran(user, 2, 30, 5)
    bests = services.get_cardio(user.id, "Outdoor Run")["bests"]
    assert bests["fastest_pace"]["entry_id"] == bests["longest"]["entry_id"] == first
    assert {t["entry_id"] for t in bests["times"]} == {first}


def test_an_activity_with_no_sessions_says_so(user):
    ran(user, 1, 30, 5)
    r = services.get_cardio(user.id, "Hiking")
    assert set(r) == ONE_KEYS | {"note"} and r["note"] == "No Hiking logged yet."
    assert r["sessions"] == [] and r["bests"] == {"fastest_pace": None, "longest": None, "times": []}
    assert r["note"] == services.get_progress(user.id, "Hiking")["note"]          # the note get_progress gives
    assert r["week"] == {"minutes": 30.0, "target_minutes": None}                 # every activity's minutes


def test_a_lift_name_or_an_unknown_one_is_refused_with_the_cardio_names(user):
    lift = services.get_cardio(user.id, "Back Squat")
    assert lift["error"] == "unknown_exercise" and "Back Squat" not in lift["closest"]
    assert all(name in lift["fix"] for name, *_ in exercises.CARDIO)
    close = services.get_cardio(user.id, "treadmil run")
    assert close["error"] == "unknown_exercise" and "Treadmill Run" in close["closest"]
    assert not set(close["closest"]) & {name for name, *_ in exercises.LIFTS}
    assert services.get_cardio(987654, "Outdoor Run")["error"] == "no_account"
    assert services.get_cardio(987654)["error"] == "no_account"


def test_get_cardio_agrees_with_get_progress(make_user):
    person = make_user(target_cardio_minutes_per_week=150)
    services.log_bodyweight(person.id, {"kg": 70}, "web")
    ids = runs(person)
    ran(person, 0, 20, exercise="Elliptical", machine_kcal=180)
    ran(person, 4, 25, exercise="Elliptical", machine_kcal=210)
    keys = ("id", "date", "minutes", "distance_km", "estimate", "machine_kcal")
    # Every activity logged: the runs carry distances and no machine number; the Elliptical carries a machine number.
    for activity in ("Outdoor Run", "Elliptical"):
        mine, theirs = services.get_cardio(person.id, activity), services.get_progress(person.id, activity)
        assert [s["id"] for s in mine["sessions"]] == [s["id"] for s in theirs["cardio"]["sessions"]] != []
        assert mine["week"] == theirs["cardio"]["week"] == {"minutes": 135.0, "target_minutes": 150}  # E, F, two Ellipticals
        for a, b in zip(mine["sessions"], theirs["cardio"]["sessions"]):
            assert {k: a[k] for k in keys} == {k: b[k] for k in keys}
            assert a["pace"] == (b["pace"]["min_per_km"] if b["pace"] else None)   # the same m:ss, as a string here
        assert set(theirs["cardio"]["sessions"][0]) == {"id", "date", "minutes", "distance_km", "pace", "estimate",
                                                       "machine_kcal"}             # get_progress's shape is unchanged
    elliptical = services.get_cardio(person.id, "Elliptical")["sessions"]
    assert [s["machine_kcal"] for s in elliptical] == [180, 210] and all(s["estimate"] for s in elliptical)
    assert ids


def test_the_best_times_and_a_cardio_goal_read_one_rule(user):
    """cardio_times: one rule for a time at a distance, for goals and for bests. A 5 km goal's latest is the best
    5 km time of the last 28 days, the bests' 5 km time when every session is that recent."""
    from app.services import cardio, goals
    ids = runs(user)
    goal = services.set_goal(user.id, {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5,
                                       "target_value": 22, "target_date": ago(user, -60)}, "web")
    five = next(t for t in services.get_cardio(user.id, "Outdoor Run")["bests"]["times"] if t["distance_km"] == 5)
    assert goal["progress"]["latest"] == five["minutes"] == 25.0
    with db.new_session() as s:
        rows = list(reversed(cardio.sessions_of(s, s.get(User, user.id), s.get(Exercise, goal["goal"]["exercise_id"]),
                                                users.today_for(user))))
        times = cardio.cardio_times(rows, 5)
    assert [(d.isoformat(), m, i) for d, m, i in times] == [
        (ago(user, 20), 31.0, ids["A"]), (ago(user, 15), 30.0, ids["B"]), (ago(user, 8), 25.0, ids["D"]),
        (ago(user, 3), 25.0, ids["F"])]
    assert not hasattr(goals, "cardio_times")                                    # moved, not copied


def test_the_summary_form_lists_every_activity_with_a_session_by_name(user, make_user):
    ran(user, 40, 20, 5)                                         # 4:00 a km, older than 28 days: the best ever
    ran(user, 3, 30, 5)
    ran(user, 27, 25.5, exercise="Treadmill Walk")
    ran(user, 1, 30.2, exercise="Treadmill Walk")
    ran(user, 35, 30, exercise="Elliptical")                     # only an old session: listed, nothing in 28 days
    gone = ran(user, 2, 30, exercise="Hiking")
    services.delete_entry(user.id, "cardio_entry", gone)           # only a deleted session: not listed
    ran(make_user(), 1, 30, exercise="Swimming")                   # another person's activity
    r = services.get_cardio(user.id)
    assert set(r) == SUMMARY_KEYS and r["today"] == ago(user, 0)
    assert r["activities"] == [
        {"exercise": "Elliptical", "sessions_28d": 0, "minutes_28d": 0.0, "fastest_pace": None,
         "last_date": ago(user, 35)},
        {"exercise": "Outdoor Run", "sessions_28d": 1, "minutes_28d": 30.0, "fastest_pace": "4:00",
         "last_date": ago(user, 3)},
        {"exercise": "Treadmill Walk", "sessions_28d": 2, "minutes_28d": 55.7, "fastest_pace": None,
         "last_date": ago(user, 1)}]
    assert all(isinstance(a["minutes_28d"], float) for a in r["activities"])
    for a in r["activities"]:
        best = services.get_cardio(user.id, a["exercise"])["bests"]["fastest_pace"]
        assert a["fastest_pace"] == (best["pace"] if best else None)
    assert services.get_cardio(user.id, "") == r
    empty = services.get_cardio(make_user().id)
    assert empty == {"today": empty["today"], "activities": []}


def test_a_session_dated_after_today_stands_at_today(user, monkeypatch):
    ran(user, 0, 30, 5)
    later = db.utcnow() + timedelta(days=2)
    monkeypatch.setattr(db, "utcnow", lambda: later)
    after = ran(user, 0, 32, 5)                                    # two days after the real today
    monkeypatch.undo()
    ran(user, 0, 31, 5)                                            # today, logged after it
    r = services.get_cardio(user.id, "Outdoor Run")
    assert [s["date"] for s in r["sessions"]] == [ago(user, 0), ago(user, -2), ago(user, 0)]
    assert r["sessions"][1]["id"] == after                         # today's, in the order logged
    assert r["week"]["minutes"] == 93.0
    summary = services.get_cardio(user.id)["activities"][0]
    assert summary["sessions_28d"] == 3 and summary["last_date"] == ago(user, 0)


def test_the_reads_feed_the_charts(user):
    """No note on either form when sessions are logged; the sessions and days draw lane B's charts."""
    from app import charts
    runs(user)
    one, every = services.get_cardio(user.id, "Outdoor Run"), services.get_cardio(user.id)
    assert "note" not in one and "note" not in every
    assert services.get_cardio(user.id, "Elliptical")["note"] == "No Elliptical logged yet."
    today = users.today_for(user)
    # Each chart function draws a non-empty svg from the real read. Lane B pins its own descriptions.
    services.log_food(user.id, {"entries": [{"description": "dal", "kcal": 450}]}, "web")
    days = services.get_days(user.id)["days"]
    for svg in (charts.pace_svg(one["sessions"], one["exercise"], today), charts.eaten_small_svg(days, today, 2000),
                charts.eaten_sheet_svg(days, today)):
        assert str(svg).startswith("<svg") and str(svg).rstrip().endswith("</svg>") and len(str(svg)) > 100


# ---------------------------------------------------------------- the fix round: exact ties, rounding, old rows

def test_an_exact_pace_tie_goes_to_the_earlier_despite_float_noise(user):
    """20.1 min over 4.02 km and 25 min over 5 km are both exactly 5:00 a km. As floats they are 5.000000000000001
    and 5.0, so a float comparison credits the later run. Paces are compared exactly, so the earlier one keeps
    the tie, and fastest_pace names the session the 1 km time names."""
    earlier = ran(user, 3, 20.1, 4.02)
    ran(user, 1, 25, 5)
    assert 20.1 / 4.02 != 25 / 5                                       # the float noise is real
    bests = services.get_cardio(user.id, "Outdoor Run")["bests"]
    assert bests["fastest_pace"]["entry_id"] == earlier and bests["fastest_pace"]["date"] == ago(user, 3)
    assert bests["fastest_pace"]["pace"] == "5:00" and bests["fastest_pace"]["pace_min_per_km"] == 20.1 / 4.02
    assert bests["times"][0] == {"distance_km": 1.0, "minutes": 5.0, "date": ago(user, 3), "entry_id": earlier}
    assert services.get_cardio(user.id)["activities"][0]["fastest_pace"] == "5:00"
    from app.services import cardio
    with db.new_session() as s:
        rows = list(s.scalars(select(CardioEntry).where(CardioEntry.user_id == user.id)))
    assert cardio.exact_pace(rows[0]) == cardio.exact_pace(rows[1])  # one exact key for the tie


def test_pace_min_per_km_is_unrounded(user):
    """10 min over 1.4 km is 7.142857 min a km, 7:09. At 2 dp it would be 7.14, 7:08: so the field is never
    rounded, and its m:ss is the pace string."""
    only = ran(user, 1, 10, 1.4)
    r = services.get_cardio(user.id, "Outdoor Run")
    assert formatting.show(round(10 / 1.4, 2), "pace") == "7:08"         # what a rounded field would print
    assert r["sessions"][0]["pace_min_per_km"] == 10 / 1.4 and r["sessions"][0]["pace"] == "7:09"
    assert r["bests"]["fastest_pace"] == {"pace": "7:09", "pace_min_per_km": 10 / 1.4, "date": ago(user, 1),
                                          "entry_id": only}
    assert formatting.show(r["bests"]["fastest_pace"]["pace_min_per_km"], "pace") == r["bests"]["fastest_pace"]["pace"]


def test_a_session_older_than_28_days_still_counts_in_the_summary(user):
    """The summary form reads every live session ever, for the fastest pace and the last day: an old session is
    in its numbers."""
    ran(user, 40, 30, 5)
    ran(user, 2, 31, 5)
    r = services.get_cardio(user.id)
    assert r["activities"][0]["sessions_28d"] == 1 and r["activities"][0]["fastest_pace"] == "6:00"   # the old one


def test_a_restored_session_comes_back(user):
    kept = ran(user, 2, 30, 5)
    gone = ran(user, 1, 25, 5)
    services.delete_entry(user.id, "cardio_entry", gone)
    assert [s["id"] for s in services.get_cardio(user.id, "Outdoor Run")["sessions"]] == [kept]
    assert services.restore_entry(user.id, "cardio_entry", gone)["restored"] is True
    r = services.get_cardio(user.id, "Outdoor Run")
    assert [s["id"] for s in r["sessions"]] == [gone, kept] and r["bests"]["fastest_pace"]["entry_id"] == gone
    assert services.get_cardio(user.id)["activities"][0]["sessions_28d"] == 2
