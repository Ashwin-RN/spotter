"""get_entry, delete_entry, restore_entry: any entry by id, soft delete, and restore."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import db, services, users
from app.models import Bodyweight, LiftSession, LiftSet, Measurement, entry_models
from app.services.common import MAX_ID, entry_id
from app.services.entries import entry_kinds

MISSING = 10 ** 9


@pytest.fixture
def user(make_user):
    return make_user()


def log_session(user):
    return services.log_lifts(user.id, {"sets": [{"exercise": "Deadlift", "weight_kg": 100, "reps": 5},
                                                 {"exercise": "Deadlift", "weight_kg": 100, "reps": 4}]}, "claude")


def log_weigh_in(user):
    return services.log_bodyweight(user.id, {"kg": 80}, "web")


LOGGERS = {"lift_session": log_session, "bodyweight": log_weigh_in}


@pytest.mark.parametrize("kind", list(LOGGERS))
def test_read_delete_and_restore(user, kind):
    made = LOGGERS[kind](user)
    entry_id = made["id"]

    read = services.get_entry(user.id, kind, entry_id)
    assert read["kind"] == kind and read["id"] == entry_id and read["entry"]["id"] == entry_id
    assert read["entry"]["deleted_at"] is None and "user_id" not in read["entry"]
    assert read["links"]["day"].endswith(f"/day/{read['entry']['date']}")
    assert services.get_entry(user.id, kind, str(entry_id)) == read

    deleted = services.delete_entry(user.id, kind, entry_id)
    assert deleted["deleted"] is True and deleted["entry"]["deleted_at"] and "restore_entry" in deleted["note"]
    missing = services.get_entry(user.id, kind, MISSING)
    assert services.get_entry(user.id, kind, entry_id) == missing
    assert services.delete_entry(user.id, kind, entry_id) == services.delete_entry(user.id, kind, MISSING) == missing

    restored = services.restore_entry(user.id, kind, entry_id)
    assert restored["restored"] is True and restored["entry"]["deleted_at"] is None and restored["replaced"] == []
    assert services.get_entry(user.id, kind, entry_id)["entry"] == restored["entry"]
    again = services.restore_entry(user.id, kind, entry_id)
    assert again["error"] == "not_deleted" and again["fix"]
    assert services.restore_entry(user.id, kind, MISSING) == missing


def test_not_found_is_one_dict_for_every_way_an_id_can_miss(user):
    session = log_session(user)
    services.delete_entry(user.id, "lift_session", session["id"])
    missing = services.get_entry(user.id, "lift_session", MISSING)
    assert missing["error"] == "not_found" and str(MISSING) not in missing["message"] and missing["fix"]
    for bad in (session["id"], "abc", None, True, 0, -3, 1.5, "", [1]):
        assert services.get_entry(user.id, "lift_session", bad) == missing, bad
        assert services.delete_entry(user.id, "lift_session", bad if bad != session["id"] else MISSING) == missing


def test_entry_id_takes_ints_and_short_ascii_digit_strings_in_range():
    assert entry_id(12) == 12 and entry_id("12") == 12 and entry_id(" 12 ") == 12
    assert entry_id(MAX_ID) == MAX_ID and entry_id(str(MAX_ID)) == MAX_ID
    for bad in (True, False, 0, -1, "0", "-3", MAX_ID + 1, 2 ** 63, -(2 ** 63), "9" * 11, "9" * 5000,
                "²", "１", "１２", "3.0", 3.0, 1.5, None, "", [1], {"id": 1}):
        assert entry_id(bad) is None, bad


def test_any_kind_that_is_not_a_name_is_unknown(user):
    made = log_weigh_in(user)
    for kind in (["bodyweight"], {"bodyweight": 1}, None, 5, "x" * 100_000):
        for call in (services.get_entry, services.delete_entry, services.restore_entry):
            r = call(user.id, kind, made["id"])
            assert r["error"] == "unknown_kind" and len(str(r)) < 1_000, (call.__name__, r)
    assert services.get_entry(user.id, "bodyweight", made["id"])["entry"]["deleted_at"] is None


def test_a_lift_session_keeps_its_sets_through_delete_and_restore(user):
    made = log_session(user)
    services.delete_entry(user.id, "lift_session", made["id"])
    with db.new_session() as s:
        assert len(list(s.scalars(select(LiftSet).where(LiftSet.session_id == made["id"])))) == 2
    restored = services.restore_entry(user.id, "lift_session", made["id"])
    assert restored["entry"]["sets"] == made["session"]["sets"]


def test_unknown_kinds_and_goals(user):
    assert services.get_entry(user.id, "food", 1)["error"] == "unknown_kind"
    assert "lift_session" in services.delete_entry(user.id, "food", 1)["fix"]
    for call in (services.delete_entry, services.restore_entry):
        refused = call(user.id, "goal", 1)
        assert refused["error"] == "unknown_kind" and "set_goal" in refused["fix"]
    goal = services.set_goal(user.id, {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                                       "target_date": "2099-01-01"}, "claude")
    read = services.get_entry(user.id, "goal", goal["id"])
    assert read["entry"] == goal["goal"] and read["links"]["goals"].endswith("/goals")


def test_every_entry_table_is_a_kind_with_no_new_code(user):
    assert set(entry_kinds()) == {m.__tablename__ for m in entry_models()}
    # A table no stage 2 function writes yet still reads, deletes and restores by id.
    with db.session_scope() as s:
        row = Measurement(user_id=user.id, date=db.utcnow().date(), kind="waist", value=82.5, unit="cm",
                          logged_by="web", dedupe_key="0" * 64)
        s.add(row)
    assert services.get_entry(user.id, "measurement", row.id)["entry"]["value"] == 82.5
    assert services.delete_entry(user.id, "measurement", row.id)["deleted"] is True
    assert services.restore_entry(user.id, "measurement", row.id)["restored"] is True


def test_delete_marks_the_row_and_leaves_it_in_place(user):
    made = log_session(user)
    services.delete_entry(user.id, "lift_session", made["id"])
    with db.new_session() as s:
        row = s.get(LiftSession, made["id"])
        assert row is not None and row.deleted_at is not None and row.updated_at >= row.created_at


# ---------------------------------------------------------------- edit

def edit(user, kind, entry, by="claude", **changes):
    return services.edit_entry(user.id, kind, entry, changes, by)


def back(user, days):
    return (users.today_for(user) - timedelta(days=days)).isoformat()


def test_editing_a_weigh_in_returns_before_after_and_a_one_line_summary(user):
    w = services.log_bodyweight(user.id, {"kg": 74.8}, "web")
    with db.session_scope() as s:  # an hour back: a clock that steps 1 to 2 ms gives a log and an edit one reading
        s.get(Bodyweight, w["id"]).updated_at -= timedelta(hours=1)
    r = edit(user, "bodyweight", w["id"], kg=78.4)
    assert set(r) == {"kind", "id", "changed", "before", "after", "summary", "links"}
    assert r["kind"] == "bodyweight" and r["id"] == w["id"] and r["changed"] == ["kg"]
    assert r["before"]["kg"] == 74.8 and r["after"]["kg"] == 78.4
    assert r["summary"] == "Weigh-in changed: kg from 74.8 to 78.4."   # the id is the result's id, not in words
    assert r["after"]["updated_at"] > r["before"]["updated_at"]
    assert services.get_entry(user.id, "bodyweight", w["id"])["entry"]["kg"] == 78.4


def test_the_same_values_again_are_a_success_that_changes_nothing(user):
    w = services.log_bodyweight(user.id, {"kg": 74.8}, "web")
    r = edit(user, "bodyweight", w["id"], kg="74.80", date=back(user, 0))
    assert r["changed"] == [] and r["before"] == r["after"] and "nothing changed" in r["summary"]


def test_an_edit_passes_the_same_checks_as_a_log(user):
    w = services.log_bodyweight(user.id, {"kg": 74.8}, "web")
    m = services.log_measurement(user.id, {"kind": "steps", "value": 8000}, "web")
    s = log_session(user)
    assert edit(user, "bodyweight", w["id"], kg=401)["error"] == "invalid_field"
    assert edit(user, "bodyweight", w["id"], date=back(user, -1))["error"] == "date_in_future"
    assert edit(user, "bodyweight", w["id"], date=back(user, 800))["error"] == "date_too_old"
    assert edit(user, "bodyweight", w["id"], date="")["error"] == "invalid_date"
    assert edit(user, "measurement", m["id"], value=8000.5)["error"] == "invalid_field"
    assert edit(user, "measurement", m["id"], value=200_001)["error"] == "invalid_field"
    assert edit(user, "lift_session", s["id"], notes="a\x00b")["error"] == "invalid_field"
    assert edit(user, "lift_session", s["id"], sets=[])["error"] == "sets_count_out_of_range"
    bad = edit(user, "lift_session", s["id"], sets=[{"exercise": "Bak Squat", "weight_kg": 501, "reps": 5}])
    assert {e["error"] for e in bad["errors"]} == {"unknown_exercise", "weight_out_of_range"}
    both = edit(user, "bodyweight", w["id"], kg="x", date="soon")
    assert {e["error"] for e in both["errors"]} == {"invalid_field", "invalid_date"}
    assert services.get_entry(user.id, "bodyweight", w["id"])["entry"]["kg"] == 74.8


def test_fields_a_kind_has_no_business_with_are_refused(user):
    w = services.log_bodyweight(user.id, {"kg": 74.8}, "web")
    r = edit(user, "bodyweight", w["id"], notes="x", value=3)
    assert [e["error"] for e in r["errors"]] == ["invalid_field", "invalid_field"]
    assert "date, kg" in r["fix"]
    assert edit(user, "bodyweight", w["id"])["error"] == "nothing_to_change"
    assert services.edit_entry(user.id, "bodyweight", w["id"], None, "web")["error"] == "nothing_to_change"
    assert services.edit_entry(user.id, "bodyweight", w["id"], ["kg"], "web")["error"] == "invalid_field"
    assert services.edit_entry(user.id, "bodyweight", w["id"], {"kg": 70}, "robot")["error"] == "invalid_by"


def test_kinds_goal_and_unknown(user):
    goal = services.set_goal(user.id, {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                                       "target_date": "2099-01-01"}, "web")
    r = services.edit_entry(user.id, "goal", goal["id"], {"target_value": 71}, "claude")
    assert r["error"] == "unknown_kind" and "set_goal" in r["fix"]
    for kind in ("usda_food", "saved_food", ["bodyweight"], None, "x" * 10_000):
        r = services.edit_entry(user.id, kind, 1, {"kg": 70}, "claude")
        assert r["error"] == "unknown_kind" and len(str(r)) < 1_000


def test_sets_replace_the_whole_list_renumbered_per_exercise(user):
    s = log_session(user)
    sets = [{"exercise": "back squat", "weight_kg": 60, "reps": 5},
            {"exercise": "Deadlift", "weight_kg": 100, "reps": 5},
            {"exercise": "Back Squat", "weight_kg": 65, "reps": 3}]
    r = edit(user, "lift_session", s["id"], sets=sets, notes="moved the bar")
    assert r["changed"] == ["notes", "sets"]
    assert r["note"] == "Bests are worked out from history: get_progress shows them."
    assert [(x["exercise"], x["set_number"], x["weight_kg"]) for x in r["after"]["sets"]] == [
        ("Back Squat", 1, 60), ("Deadlift", 1, 100), ("Back Squat", 2, 65)]
    assert r["summary"] == ("Lift session changed: notes changed. sets from Deadlift 100 kg × 5, 100 kg × 4 to "
                            "Back Squat 60 kg × 5, 65 kg × 3 and Deadlift 1 × 5 at 100 kg.")
    with db.new_session() as s2:
        assert len(list(s2.scalars(select(LiftSet).where(LiftSet.session_id == s["id"])))) == 3
    progress = services.get_progress(user.id, "Deadlift")["lift"]
    assert progress["sessions"] == 1 and progress["best_set"]["weight_kg"] == 100


def test_a_new_date_onto_a_taken_day_is_refused(user):
    first = services.log_bodyweight(user.id, {"kg": 80, "date": back(user, 1)}, "web")
    second = services.log_bodyweight(user.id, {"kg": 81}, "web")
    r = edit(user, "bodyweight", second["id"], date=back(user, 1))
    assert r["error"] == "day_taken" and r["entry_id"] == first["id"] and str(first["id"]) in r["fix"]
    assert services.get_entry(user.id, "bodyweight", second["id"])["entry"]["date"] == back(user, 0)
    waist = services.log_measurement(user.id, {"kind": "waist", "value": 84, "date": back(user, 1)}, "web")
    hips = services.log_measurement(user.id, {"kind": "hips", "value": 95}, "web")
    other = services.log_measurement(user.id, {"kind": "waist", "value": 85}, "web")
    assert edit(user, "measurement", other["id"], date=back(user, 1))["entry_id"] == waist["id"]
    moved = edit(user, "measurement", hips["id"], date=back(user, 1))   # another kind: the day is free
    assert moved["changed"] == ["date"] and moved["links"]["day"].endswith(f"/day/{back(user, 1)}")
    services.delete_entry(user.id, "bodyweight", first["id"])            # a deleted row does not hold the day
    assert edit(user, "bodyweight", second["id"], date=back(user, 1))["changed"] == ["date"]
    # The message names the kind by its word (measurements.KIND_WORDS), never its unit.
    services.log_measurement(user.id, {"kind": "resting_hr", "value": 60, "date": back(user, 1)}, "web")
    later = services.log_measurement(user.id, {"kind": "resting_hr", "value": 61}, "web")
    taken = edit(user, "measurement", later["id"], date=back(user, 1))
    assert taken["error"] == "day_taken" and " already has a resting heart rate measurement (id " in taken["message"]


def test_an_edit_re_keys_so_a_repeat_of_the_new_content_is_a_duplicate(user):
    w = services.log_bodyweight(user.id, {"kg": 74.8, "date": back(user, 2)}, "web")
    edit(user, "bodyweight", w["id"], kg=78.4)
    again = services.log_bodyweight(user.id, {"kg": 78.4, "date": back(user, 2)}, "web")
    assert again["duplicate"] is True and again["id"] == w["id"]
    m = services.log_measurement(user.id, {"kind": "waist", "value": 84}, "web")
    edit(user, "measurement", m["id"], value=83)
    assert services.log_measurement(user.id, {"kind": "waist", "value": 83}, "web")["duplicate"] is True
    s = log_session(user)
    new_sets = [{"exercise": "Deadlift", "weight_kg": 110, "reps": 5}]
    edit(user, "lift_session", s["id"], sets=new_sets)
    assert services.log_lifts(user.id, {"sets": new_sets}, "web")["id"] == s["id"]


def test_a_deleted_or_missing_entry_is_not_found(user):
    w = services.log_bodyweight(user.id, {"kg": 74.8}, "web")
    services.delete_entry(user.id, "bodyweight", w["id"])
    missing = edit(user, "bodyweight", MISSING, kg=70)
    assert missing["error"] == "not_found" and edit(user, "bodyweight", w["id"], kg=70) == missing
    m = services.log_measurement(user.id, {"kind": "waist", "value": 84}, "web")
    assert "log_measurement" in edit(user, "measurement", MISSING, value=80)["fix"]
    assert edit(user, "measurement", m["id"], value=80)["changed"] == ["value"]



def test_a_weight_change_in_the_sets_says_what_changed(user):
    three = lambda kg: [{"exercise": "Back Squat", "weight_kg": kg, "reps": 5}] * 3  # noqa: E731
    s = services.log_lifts(user.id, {"sets": three(60)}, "claude")
    r = edit(user, "lift_session", s["id"], sets=three(65))
    assert r["summary"] == "Lift session changed: sets from Back Squat 3 × 5 at 60 kg to Back Squat 3 × 5 at 65 kg."


def test_a_long_set_list_is_cut_in_the_summary(user):
    many = [{"exercise": name, "weight_kg": 20 + n, "reps": 5}
            for n, name in enumerate(["Back Squat", "Deadlift", "Bench Press", "Overhead Press", "Barbell Row"] * 6)]
    s = services.log_lifts(user.id, {"sets": many}, "claude")
    r = edit(user, "lift_session", s["id"], sets=many[:-1])
    before, after = r["summary"].split(": sets from ", 1)[1].rstrip(".").split(" to Back Squat ")
    after = "Back Squat " + after
    assert len(before) <= 80 and before.endswith("…") and len(after) <= 80



# ---------------------------------------------------------------- food and cardio edits (decision 11)

def meal_of(user, **entry):
    return services.log_food(user.id, {"entries": [entry]}, "claude")


def test_editing_a_food_entrys_kcal_makes_it_the_persons_number(user):
    guess = meal_of(user, description="dal", kcal=180, estimated=True)
    r = edit(user, "food_entry", guess["ids"][0], kcal=150)
    assert r["changed"] == ["kcal"] and r["after"]["kcal"] == 150
    assert (r["before"]["source"], r["after"]["source"], r["after"]["estimated"]) == ("claude_estimate", "you_said", False)
    assert r["summary"] == "Food entry changed: kcal from 180 to 150." and "you_said" in r["note"]
    roti = services.save_food(user.id, {"name": "Roti", "serving_label": "1", "kcal_per_serving": 120}, "web")
    by_id = meal_of(user, saved_food_id=roti["id"], servings=2)
    r = edit(user, "food_entry", by_id["ids"][0], kcal=250)
    after = r["after"]
    assert (after["source"], after["saved_food_id"], after["servings"], after["usda_fdc_id"], after["grams"]) == (
        "you_said", None, None, None, None)
    assert services.get_log(user.id)["days"][0]["food"]["estimated_kcal"] == 0


def test_editing_a_food_entrys_description_or_meal_keeps_its_tag(user):
    guess = meal_of(user, description="dal", kcal=180, estimated=True, meal="lunch")
    r = edit(user, "food_entry", guess["ids"][0], description="dal tadka", meal=" Dinner ")
    assert r["changed"] == ["meal", "description"] and r["after"]["source"] == "claude_estimate"
    assert r["after"]["meal"] == "dinner" and "note" not in r
    assert r["summary"] == "Food entry changed: meal from lunch to dinner. description from 'dal' to 'dal tadka'."
    stated = edit(user, "food_entry", guess["ids"][0], kcal=180)                   # the same number, stated
    assert stated["changed"] == ["kcal"] and stated["after"]["source"] == "you_said"
    assert "kcal 180 confirmed by the person" in stated["summary"]
    assert edit(user, "food_entry", guess["ids"][0], kcal=180)["changed"] == []    # already the person's


def test_a_food_edit_passes_the_same_checks_as_a_log(user):
    e = meal_of(user, description="dal", kcal=180)["ids"][0]
    assert edit(user, "food_entry", e, kcal=5001)["error"] == "invalid_field"
    assert edit(user, "food_entry", e, meal="brunch")["error"] == "invalid_field"
    assert edit(user, "food_entry", e, description="")["error"] == "missing_field"
    assert edit(user, "food_entry", e, saved_food_id=1)["error"] == "invalid_field"
    assert edit(user, "food_entry", e, source="you_said")["error"] == "invalid_field"


def test_an_edited_food_entry_is_re_keyed(user):
    e = meal_of(user, description="dal", kcal=180)["ids"][0]
    edit(user, "food_entry", e, kcal=150)
    again = meal_of(user, description="dal", kcal=150)
    assert again["duplicate"] is True and again["ids"] == [e]


def test_editing_a_cardio_entry_re_estimates_it(user):
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    walk = services.log_cardio(user.id, {"exercise": "Outdoor Walk", "minutes": 30, "distance_km": 3}, "claude")
    assert walk["estimate"]["kcal"] == 160 and walk["entry"]["speed_kmh"] == 6.0
    services.log_bodyweight(user.id, {"kg": 90}, "web")          # body weight now moves: today's replaces 80
    r = edit(user, "cardio_entry", walk["id"], minutes=45)
    assert r["changed"] == ["minutes", "speed_kmh"] and r["after"]["speed_kmh"] == 4.0
    assert r["after"]["estimate"]["kcal"] == 269 and r["after"]["estimate"]["weight_kg"] == 90
    assert r["after"]["estimated_kcal"] == 269 and "worked out again" in r["note"]
    assert r["after"]["pace"]["min_per_km"] == "15:00"


def test_a_speed_the_person_gave_stays_theirs(user):
    run = services.log_cardio(user.id, {"exercise": "Treadmill Run", "minutes": 30, "distance_km": 5,
                                        "speed_kmh": 10.5}, "claude")
    r = edit(user, "cardio_entry", run["id"], minutes=28)
    assert r["changed"] == ["minutes"] and r["after"]["speed_kmh"] == 10.5


def test_a_cardio_activity_can_change_within_cardio_names_only(user):
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    walk = services.log_cardio(user.id, {"exercise": "Outdoor Walk", "minutes": 30}, "claude")
    r = edit(user, "cardio_entry", walk["id"], exercise="hiking")
    assert r["changed"] == ["exercise"] and r["after"]["exercise"] == "Hiking"
    assert r["after"]["estimate"]["met"] == 6.0 and "activity from Outdoor Walk to Hiking" in r["summary"]
    lift = edit(user, "cardio_entry", walk["id"], exercise="Back Squat")
    assert lift["error"] == "unknown_exercise" and "Treadmill Walk" in lift["fix"]


def test_a_cardio_edit_without_a_weigh_in_clears_the_estimate_with_a_note(user):
    walk = services.log_cardio(user.id, {"exercise": "Outdoor Walk", "minutes": 30, "machine_kcal": 120}, "claude")
    r = edit(user, "cardio_entry", walk["id"], machine_kcal=130, notes="windy")
    assert r["changed"] == ["machine_kcal", "notes"] and r["after"]["estimate"] is None
    assert r["after"]["machine_kcal"] == 130 and "No weigh-in yet" in r["note"]
    assert edit(user, "cardio_entry", walk["id"], machine_kcal=None)["after"]["machine_kcal"] is None
    assert edit(user, "cardio_entry", walk["id"], minutes=0)["error"] == "invalid_field"
    assert edit(user, "cardio_entry", walk["id"], kcal=100)["error"] == "invalid_field"


# ---------------------------------------------------------------- saved foods by id

def test_a_saved_food_is_read_deleted_and_restored_by_id_but_edited_by_save_food(user):
    roti = services.save_food(user.id, {"name": "Roti", "serving_label": "1", "kcal_per_serving": 120}, "web")
    read = services.get_entry(user.id, "saved_food", roti["id"])
    assert read["entry"]["name"] == "Roti" and read["links"]["foods"].endswith("/foods")
    assert services.delete_entry(user.id, "saved_food", roti["id"])["entry"]["deleted_at"]
    assert services.get_entry(user.id, "saved_food", roti["id"])["error"] == "not_found"
    assert services.restore_entry(user.id, "saved_food", roti["id"])["entry"]["deleted_at"] is None
    refused = services.edit_entry(user.id, "saved_food", roti["id"], {"kcal_per_serving": 1}, "web")
    assert refused["error"] == "unknown_kind" and "save_food" in refused["fix"]
    assert "save_food" in services.get_entry(user.id, "saved_food", MISSING)["fix"]



@pytest.mark.parametrize("change,ok", [
    ({"distance_km": 5.5}, True),           # 5.5 km in 30 min is 11 km/h: the stated 11 fits exactly
    ({"distance_km": 6.5}, True),           # 13 km/h: 11 is 15% off, inside 20%
    ({"distance_km": 8}, False),            # 16 km/h: 31% off
    ({"minutes": 32}, True),                # 9.38 km/h: 11 is 17% over, inside 20%
    ({"minutes": 33}, False),               # 9.09 km/h: 11 is 21% over, outside
    ({"minutes": 60}, False),               # 5 km/h
])
def test_a_stated_speed_is_rechecked_when_distance_or_minutes_change(user, change, ok):
    run = services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5,
                                        "speed_kmh": 11}, "claude")
    assert run["entry"]["speed_kmh"] == 11
    r = edit(user, "cardio_entry", run["id"], **change)
    if ok:
        assert "error" not in r and r["after"]["speed_kmh"] == 11, r
    else:
        assert r["error"] == "invalid_field" and "the speed you gave, 11, no longer fits" in r["message"], r
        assert "speed_kmh" in r["fix"]
        assert services.get_entry(user.id, "cardio_entry", run["id"])["entry"]["distance_km"] == 5


def test_a_stated_speed_can_be_replaced_or_cleared_with_the_new_distance(user):
    run = services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5,
                                        "speed_kmh": 11}, "claude")
    assert edit(user, "cardio_entry", run["id"], distance_km=8, speed_kmh=16)["after"]["speed_kmh"] == 16
    cleared = edit(user, "cardio_entry", run["id"], distance_km=7, speed_kmh=None)
    assert cleared["after"]["speed_kmh"] == 14.0 and cleared["after"]["pace"]["min_per_km"] == "4:17"



def test_an_empty_field_filled_by_an_edit_says_set_to_never_none(user):
    """A cardio entry's optional numbers, and a session's notes, filled where they were empty."""
    walk = services.log_cardio(user.id, {"exercise": "Outdoor Walk", "minutes": 30}, "claude")
    r = edit(user, "cardio_entry", walk["id"], distance_km=3, incline_pct=2.5, avg_hr=120, machine_kcal=1500)
    assert r["summary"] == ("Cardio entry changed: distance_km set to 3. incline_pct set to 2.5. avg_hr set to 120. "
                            "machine_kcal set to 1,500. speed_kmh set to 6, worked out from distance and minutes.")
    given = services.log_cardio(user.id, {"exercise": "Treadmill Run", "minutes": 30, "distance_km": 5}, "claude")
    assert edit(user, "cardio_entry", given["id"], speed_kmh=11)["summary"] == \
        "Cardio entry changed: speed_kmh from 10 to 11."
    plain = services.log_cardio(user.id, {"exercise": "Elliptical", "minutes": 20}, "claude")
    assert edit(user, "cardio_entry", plain["id"], speed_kmh=9.5)["summary"] == "Cardio entry changed: speed_kmh set to 9.5."
    session = log_session(user)
    assert edit(user, "lift_session", session["id"], notes="first notes")["summary"] == \
        "Lift session changed: notes changed."


def test_a_change_that_prints_the_same_shows_one_more_decimal(user):
    """Two values that read the same at the shown step but differ are both widened: never "from 1 to 1"."""
    waist = services.log_measurement(user.id, {"kind": "waist", "value": 84.25}, "claude")
    r = edit(user, "measurement", waist["id"], value=84.34)
    assert r["summary"] == "Measurement changed: value from 84.25 cm to 84.34 cm."    # both 84.3 at 1 dp
    assert edit(user, "measurement", waist["id"], value=86)["summary"] == "Measurement changed: value from 84.3 cm to 86 cm."
    run = services.log_cardio(user.id, {"exercise": "Treadmill Run", "minutes": 30, "distance_km": 5,
                                        "incline_pct": 1.04, "speed_kmh": 10.04}, "claude")
    r = edit(user, "cardio_entry", run["id"], incline_pct=1.01, speed_kmh=10.01)
    assert r["summary"] == "Cardio entry changed: incline_pct from 1.04 to 1.01. speed_kmh from 10.04 to 10.01."


def test_a_speed_worked_out_in_an_edit_is_summarised_as_stored(user):
    run = services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5,
                                        "speed_kmh": 11}, "claude")
    cleared = edit(user, "cardio_entry", run["id"], distance_km=8, speed_kmh=None)
    assert cleared["after"]["speed_kmh"] == 16.0
    assert "speed_kmh from 11 to 16, worked out from distance and minutes" in cleared["summary"]
    assert "None" not in cleared["summary"]
    walk = services.log_cardio(user.id, {"exercise": "Outdoor Walk", "minutes": 30, "distance_km": 3}, "claude")
    longer = edit(user, "cardio_entry", walk["id"], minutes=45)        # a worked-out speed, worked out again
    # minutes are a session's length, m:ss (D7); one sentence a change.
    assert longer["summary"].endswith("minutes from 30:00 to 45:00. speed_kmh from 6 to 4, worked out from distance "
                                      "and minutes.")
    gone = edit(user, "cardio_entry", walk["id"], distance_km=None)    # the distance goes, and the speed with it
    assert gone["after"]["speed_kmh"] is None and gone["after"]["pace"] is None
    assert "distance_km 3 cleared. speed_kmh 4 cleared with the distance" in gone["summary"]
    assert "worked out" not in gone["summary"] and "none" not in gone["summary"].casefold()
