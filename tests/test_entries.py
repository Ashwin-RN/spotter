"""get_entry, delete_entry, restore_entry: any entry by id, soft delete, and restore."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import db, services, users
from app.models import LiftSession, LiftSet, Measurement, entry_models
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
    r = edit(user, "bodyweight", w["id"], kg=78.4)
    assert set(r) == {"kind", "id", "changed", "before", "after", "summary", "links"}
    assert r["kind"] == "bodyweight" and r["id"] == w["id"] and r["changed"] == ["kg"]
    assert r["before"]["kg"] == 74.8 and r["after"]["kg"] == 78.4
    assert r["summary"] == f"Weigh-in {w['id']} changed: kg 74.8 -> 78.4."
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
    for kind in ("food_entry", ["bodyweight"], None, "x" * 10_000):
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
    assert r["summary"] == (f"Lift session {s['id']} changed: notes changed; "
                            "sets Deadlift 100 kg × 5, 100 kg × 4 -> Back Squat 60 kg × 5, 65 kg × 3; Deadlift 1 × 5 @ 100 kg.")
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
    assert r["summary"] == f"Lift session {s['id']} changed: sets Back Squat 3 × 5 @ 60 kg -> Back Squat 3 × 5 @ 65 kg."


def test_a_long_set_list_is_cut_in_the_summary(user):
    many = [{"exercise": name, "weight_kg": 20 + n, "reps": 5}
            for n, name in enumerate(["Back Squat", "Deadlift", "Bench Press", "Overhead Press", "Barbell Row"] * 6)]
    s = services.log_lifts(user.id, {"sets": many}, "claude")
    r = edit(user, "lift_session", s["id"], sets=many[:-1])
    before, after = r["summary"].split(": sets ", 1)[1].rstrip(".").split(" -> ")
    assert len(before) <= 80 and before.endswith("…") and len(after) <= 80
