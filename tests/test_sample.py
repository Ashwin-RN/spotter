"""Sample data: refused on a non-empty account, every count and tag, deterministic, removed completely,
and the goal and trend numbers it gives on a fixed day, to the decimal.

The day is fixed by replacing the clock (db.utcnow) with NOW. The live exit test compares Spotter's
numbers with these, for the same day: to check another day, set NOW to it and rerun the numbers
(the decimals below are for 2026-11-10 only; the counts hold on any day). The decimals were worked
out a second way, in plain Python from the seed, not copied from the service's output.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import db, services, users
from app.models import (
    Bodyweight, CardioEntry, FoodEntry, Goal, LiftSession, LiftSet, Measurement, SavedFood, User,
)
from app.services import sample

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)   # a Tuesday
MODELS = (LiftSession, Bodyweight, Measurement, SavedFood, FoodEntry, CardioEntry, Goal)
COUNTS = {"lift_sessions": 12, "weigh_ins": 28, "measurements": 8, "saved_foods": 4, "food_entries": 240,
          "cardio_entries": 12, "goals": 3}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


@pytest.fixture
def user(make_user):
    return make_user()


def rows_of(user_id, only_sample=False) -> dict:
    with db.new_session() as s:
        out = {}
        for m in MODELS:
            q = select(func.count()).select_from(m).where(m.user_id == user_id)
            out[m.__tablename__] = s.scalar(q.where(m.is_sample.is_(True)) if only_sample else q)
        out["lift_set"] = s.scalar(select(func.count()).select_from(LiftSet).join(LiftSession)
                                   .where(LiftSession.user_id == user_id))
        return out


def added(user):
    r = services.add_sample_data(user.id, "web")
    assert "error" not in r, r
    return r


# ---------------------------------------------------------------- counts, tags, marks

def test_it_adds_four_weeks_of_everything(user):
    assert services.sample_state(user.id) == {"empty": True, "sample_rows": 0}
    r = added(user)
    assert r["added"] == COUNTS and r["from"] == "2026-10-14" and r["to"] == "2026-11-10"
    assert set(r["links"]) == {"today", "progress", "goals"} and "Remove sample data" in r["summary"]
    assert services.sample_state(user.id) == {"empty": False, "sample_rows": sum(COUNTS.values())}
    assert rows_of(user.id)["lift_set"] == 6 * (9 + 7)   # six A sessions of 9 sets, six B of 7
    with db.new_session() as s:
        for m in MODELS:
            assert all(r.is_sample for r in s.scalars(select(m).where(m.user_id == user.id))), m
            if hasattr(m, "logged_by"):
                assert {r.logged_by for r in s.scalars(select(m).where(m.user_id == user.id))} == {"web"}
        dates = [d for m in (LiftSession, Bodyweight, Measurement, FoodEntry, CardioEntry)
                 for d in s.scalars(select(m.date).where(m.user_id == user.id))]
        assert min(dates).isoformat() == "2026-10-14" and max(dates).isoformat() == "2026-11-10"
        sources = {e.source for e in s.scalars(select(FoodEntry).where(FoodEntry.user_id == user.id))}
        assert sources == {"saved_food", "you_said", "claude_estimate"}   # every tag a sample can carry
        sessions = list(s.scalars(select(LiftSession).where(LiftSession.user_id == user.id)))
        assert {t.date.weekday() for t in sessions} == {0, 2, 4}
        cardio = list(s.scalars(select(CardioEntry).where(CardioEntry.user_id == user.id)))
        assert {(c.date.weekday(), c.exercise.name) for c in cardio} == {
            (1, "Treadmill Walk"), (3, "Stationary Bike"), (5, "Outdoor Run")}
        assert all(c.machine_kcal for c in cardio if c.exercise.name == "Stationary Bike")
        assert sorted(c.minutes for c in cardio if c.exercise.name == "Outdoor Run") == [29.5, 30.0, 30.5, 31.0]
        assert {c.estimate_weight_kg for c in cardio} == {79.01}       # body weight now, for every one
        waist = [m.value for m in s.scalars(select(Measurement).where(Measurement.kind == "waist")
                                            .order_by(Measurement.date))]
        assert waist == [88.0, 87.5, 87.0, 86.5]
        user_row = s.get(User, user.id)
        assert user_row.calorie_target_kcal is None and user_row.time_zone is None   # settings untouched


def test_every_day_lands_between_2100_and_2300_kcal(user):
    added(user)
    day_totals = {}
    with db.new_session() as s:
        for e in s.scalars(select(FoodEntry).where(FoodEntry.user_id == user.id)):
            day_totals[e.date] = day_totals.get(e.date, 0) + e.kcal
            assert e.estimated is (e.source == "claude_estimate")
            if e.source == "saved_food":
                assert e.saved_food_id and e.servings and e.description == e.saved_food.name
    assert len(day_totals) == 28 and all(2100 <= t <= 2300 for t in day_totals.values())
    days = services.get_log(user.id, days=14)["days"]
    snacks = sum(1 for e in days if e["food"]["meals"]["snack"])
    assert 0 < snacks < 14   # some days, not all
    shown = [e for d in days for meal in d["food"]["meals"].values() for e in meal]
    assert shown and all(e["is_sample"] is True for e in shown)   # each one can be tagged on the day page


def test_two_runs_on_the_same_day_give_the_same_rows(make_user):
    a, b = make_user(), make_user()
    added(a)
    added(b)

    def picture(user_id):
        with db.new_session() as s:
            return {
                "weigh_ins": [(w.date, w.kg) for w in s.scalars(select(Bodyweight).where(Bodyweight.user_id == user_id)
                                                                .order_by(Bodyweight.date))],
                "food": [(e.date, e.meal, e.description, e.kcal, e.source)
                         for e in s.scalars(select(FoodEntry).where(FoodEntry.user_id == user_id).order_by(FoodEntry.id))],
                "cardio": [(c.date, c.minutes, c.machine_kcal, c.estimated_kcal)
                           for c in s.scalars(select(CardioEntry).where(CardioEntry.user_id == user_id)
                                              .order_by(CardioEntry.id))],
                "keys": sorted(w.dedupe_key for w in s.scalars(select(Bodyweight).where(Bodyweight.user_id == user_id)))}

    assert picture(a.id) == picture(b.id)
    ga, gb = services.get_goals(a.id), services.get_goals(b.id)
    strip = lambda goals: [{k: v for k, v in g.items() if k != "goal_id"} for g in goals["goals"]]  # noqa: E731
    assert strip(ga) == strip(gb)


def test_each_row_carries_its_kinds_own_duplicate_key(user):
    """A real entry with the same content, logged now, is judged the same way: a duplicate."""
    added(user)
    day = "2026-11-10"
    with db.new_session() as s:
        kg = s.scalar(select(Bodyweight.kg).where(Bodyweight.user_id == user.id, Bodyweight.date == NOW.date()))
        roti = s.scalar(select(SavedFood.id).where(SavedFood.name == "Roti"))
        dal = s.scalar(select(SavedFood.id).where(SavedFood.name == "Dal bowl"))
        oats = s.scalar(select(SavedFood.id).where(SavedFood.name == "Oats bowl"))
        sabzi = s.scalar(select(FoodEntry.kcal).where(FoodEntry.user_id == user.id, FoodEntry.date == NOW.date(),
                                                      FoodEntry.description == "Mixed vegetable sabzi"))
    assert services.log_bodyweight(user.id, {"kg": kg, "date": day}, "web")["duplicate"] is True
    assert services.log_measurement(user.id, {"kind": "waist", "value": 86.5, "date": day}, "web")["duplicate"] is True
    assert services.log_cardio(user.id, {"exercise": "Treadmill Walk", "minutes": 30, "date": day},
                               "web")["duplicate"] is True
    breakfast = services.log_food(user.id, {"date": day, "entries": [
        {"saved_food_id": oats, "servings": 1, "meal": "breakfast"},
        {"description": "Tea with milk", "kcal": 80, "meal": "breakfast"}]}, "web")
    assert breakfast["duplicate"] is True
    lunch = services.log_food(user.id, {"date": day, "entries": [
        {"saved_food_id": roti, "servings": 2, "meal": "lunch"}, {"saved_food_id": dal, "servings": 1, "meal": "lunch"},
        {"description": "Mixed vegetable sabzi", "kcal": sabzi, "estimated": True, "meal": "lunch"}]}, "web")
    assert lunch["duplicate"] is True
    monday = "2026-11-09"   # a B session: Deadlift 1x5, Overhead Press 3x5, Pull-Up 3x6
    with db.new_session() as s:
        t = s.scalar(select(LiftSession).where(LiftSession.user_id == user.id, LiftSession.date == NOW.date() -
                                               timedelta(days=1)))
        sets = [{"exercise": x.exercise.name, "weight_kg": x.weight_kg, "reps": x.reps}
                for x in sorted(t.sets, key=lambda x: x.id)]
    assert services.log_lifts(user.id, {"date": monday, "sets": sets}, "web")["duplicate"] is True
    assert rows_of(user.id)["bodyweight"] == 28   # nothing was written


# ---------------------------------------------------------------- the numbers, to the decimal (2026-11-10)

def test_get_goals_numbers_for_the_fixed_day(user):
    added(user)
    r = services.get_goals(user.id)
    assert r["today"] == "2026-11-10" and r["includes_sample"] is True and r["closed"] == []
    assert "Remove sample data" in r["note"]
    weight, squat, run = r["goals"]
    assert all(g["is_sample"] is True for g in r["goals"])   # each one can be tagged on the Goals page
    assert {k: weight[k] for k in ("label", "kind", "unit", "start_value", "start_date", "direction", "target_value",
                                   "target_date", "latest", "status", "needed_per_week", "actual_per_week",
                                   "projected_date")} == {
        "label": "Bodyweight 77 kg", "kind": "bodyweight", "unit": "kg", "start_value": 79.9,
        "start_date": "2026-10-14", "direction": "down", "target_value": 77.0, "target_date": "2027-01-05",
        "latest": 79.01, "status": "on track", "needed_per_week": -0.25, "actual_per_week": -0.29,
        "projected_date": "2026-12-26"}
    assert weight["projection"]["projected"] == 76.59 and weight["projection"]["points_used"] == 28
    assert {k: squat[k] for k in ("label", "exercise", "start_value", "start_date", "target_date", "latest", "status",
                                  "needed_per_week", "actual_per_week", "projected_date")} == {
        "label": "Back Squat 120 kg", "exercise": "Back Squat", "start_value": 93.33, "start_date": "2026-10-14",
        "target_date": "2027-02-02", "latest": 107.92, "status": "on track", "needed_per_week": 1.01,
        "actual_per_week": 4.41, "projected_date": "2026-11-26"}
    assert squat["projection"]["projected"] == 163.17 and squat["projection"]["points_used"] == 6
    assert {k: run[k] for k in ("label", "kind", "exercise", "distance_km", "unit", "start_value", "start_date",
                                "direction", "target_value", "target_date", "latest", "status", "needed_per_week",
                                "actual_per_week", "projected_date")} == {
        "label": "Outdoor Run 5 km in 27:00", "kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5.0,
        "unit": "min", "start_value": 31.0, "start_date": "2026-10-17", "direction": "down", "target_value": 27.0,
        "target_date": "2027-01-05", "latest": 29.5, "status": "on track", "needed_per_week": -0.31,
        "actual_per_week": -0.5, "projected_date": "2026-12-12"}
    assert run["projection"]["projected"] == 25.29
    assert r["summary"].startswith("3 open goals: Bodyweight 77 kg by 2027-01-05, on track;")


def test_get_trends_numbers_for_the_fixed_day(user):
    added(user)
    r = services.get_trends(user.id)
    bw = r["bodyweight"]
    assert {k: bw[k] for k in ("now", "smoothed", "rate_per_week", "rate_window", "weigh_ins_28d")} == {
        "now": 79.01, "smoothed": 79.22, "rate_per_week": -0.24,
        "rate_window": {"from": "2026-10-27", "to": "2026-11-10", "days": 14}, "weigh_ins_28d": 28}
    assert bw["series"][0] == {"date": "2026-10-14", "kg": 79.9, "smoothed": 79.9}
    assert bw["series"][-15] == {"date": "2026-10-27", "kg": 79.4, "smoothed": 79.71}
    assert r["calories"] == {"avg_7d": 2216, "days_logged_7d": 7, "avg_28d": 2196, "days_logged_28d": 28,
                             "estimated_share_pct_28d": 41, "target_kcal": None}
    assert r["sessions"] == {"this_week": 3, "target_per_week": None, "per_week_4w": 3.0}
    assert r["cardio"] == {"minutes_this_week": 84.5, "target_minutes": None, "minutes_per_week_4w": 85.2}
    assert r["maintenance"] == {"kcal": 2513, "intake_avg_kcal": 2198, "weight_change_kg": -0.86, "days_logged": 21,
                                "window_days": 21, "from": "2026-10-21", "to": "2026-11-10"}
    assert r["maintenance_note"] is None and r["includes_sample"] is True and "Remove sample data" in r["note"]


def test_every_read_says_it_includes_sample_rows_until_they_go(user):
    added(user)
    reads = {"get_log": lambda: services.get_log(user.id, days=7), "get_progress": lambda: services.get_progress(user.id),
             "get_progress lift": lambda: services.get_progress(user.id, "Back Squat"),
             "get_progress cardio": lambda: services.get_progress(user.id, "Outdoor Run"),
             "get_goals": lambda: services.get_goals(user.id), "get_trends": lambda: services.get_trends(user.id)}
    for name, read in reads.items():
        r = read()
        assert r["includes_sample"] is True and r["note"].endswith(services.common.SAMPLE_NOTE), name
        assert "Settings" in r["note"] and "remove_sample_data" not in r["note"], name
    # With every sample goal dropped, the sample sessions alone still make get_progress say so.
    for g in services.get_goals(user.id)["goals"]:
        services.set_goal(user.id, {"goal_id": g["goal_id"], "status": "dropped"}, "web")
    summary = services.get_progress(user.id)
    assert summary["goals"] == [] and summary["includes_sample"] is True
    assert services.remove_sample_data(user.id, "web")["removed"] == COUNTS
    for name, read in reads.items():
        r = read()
        assert r["includes_sample"] is False and services.common.SAMPLE_NOTE not in r.get("note", ""), name


# ---------------------------------------------------------------- refusals

def test_refused_on_an_account_with_its_own_rows(user):
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    r = services.add_sample_data(user.id, "web")
    assert r["error"] == "not_empty" and r["message"] == "Sample data goes only into an empty account."
    assert "day pages" in r["fix"] and "delete" in r["fix"].lower()
    assert rows_of(user.id, only_sample=True) == {**{m.__tablename__: 0 for m in MODELS}, "lift_set": 0}
    assert services.sample_state(user.id) == {"empty": False, "sample_rows": 0}


@pytest.mark.parametrize("what", ["live saved food", "open goal", "lift session", "food entry", "cardio entry",
                                  "measurement"])
def test_each_live_thing_keeps_an_account_from_being_empty(user, what):
    goal_date = (users.today_for(user) + timedelta(days=30)).isoformat()
    {"live saved food": lambda: services.save_food(user.id, {"name": "Toast", "serving_label": "1 slice",
                                                             "kcal_per_serving": 80}, "web"),
     "open goal": lambda: services.set_goal(user.id, {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                                                      "target_date": goal_date}, "web"),
     "lift session": lambda: services.log_lifts(user.id, {"sets": [{"exercise": "Deadlift", "weight_kg": 100,
                                                                    "reps": 5}]}, "web"),
     "food entry": lambda: services.log_food(user.id, {"entries": [{"description": "dal", "kcal": 180}]}, "web"),
     "cardio entry": lambda: services.log_cardio(user.id, {"exercise": "Elliptical", "minutes": 20}, "web"),
     "measurement": lambda: services.log_measurement(user.id, {"kind": "waist", "value": 84}, "web")}[what]()
    assert services.sample_state(user.id)["empty"] is False
    assert services.add_sample_data(user.id, "web")["error"] == "not_empty"


def test_deleted_entries_deleted_saved_foods_and_closed_goals_do_not_count(user):
    """Amended decision 9: only live entries, live saved foods and open goals keep an account from being empty."""
    weigh = services.log_bodyweight(user.id, {"kg": 80}, "web")
    services.delete_entry(user.id, "bodyweight", weigh["id"])
    roti = services.save_food(user.id, {"name": "Roti", "serving_label": "1 big roti", "kcal_per_serving": 150}, "web")
    services.delete_entry(user.id, "saved_food", roti["id"])
    for status in ("dropped", "hit"):   # a closed goal does not count, whichever way it closed
        gid = services.set_goal(user.id, {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                                          "target_date": "2027-01-01"}, "web")["id"]
        services.set_goal(user.id, {"goal_id": gid, "status": status}, "web")
    assert services.sample_state(user.id) == {"empty": True, "sample_rows": 0}
    assert added(user)["added"] == COUNTS
    with db.new_session() as s:   # the deleted Roti keeps its name; the sample one is told apart
        names = sorted(f.name for f in s.scalars(select(SavedFood).where(SavedFood.user_id == user.id)))
        assert names == ["Dal bowl", "Oats bowl", "Rice bowl", "Roti", "Roti (sample)"]
    assert services.restore_entry(user.id, "saved_food", roti["id"])["restored"] is True
    # Removal leaves the person's own rows, deleted ones included.
    services.remove_sample_data(user.id, "web")
    assert rows_of(user.id) == {**{m.__tablename__: 0 for m in MODELS}, "bodyweight": 1, "saved_food": 1, "goal": 2,
                                "lift_set": 0}


def test_a_hit_goal_alone_does_not_block_and_an_open_one_does(user):
    gid = services.set_goal(user.id, {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                                      "target_date": "2027-01-01"}, "web")["id"]
    assert services.sample_state(user.id)["empty"] is False
    services.set_goal(user.id, {"goal_id": gid, "status": "hit"}, "web")
    assert services.sample_state(user.id)["empty"] is True and "error" not in services.add_sample_data(user.id, "web")


def test_one_kind_of_sample_row_alone_still_flags_every_read(user):
    """Rule 16 per kind: only the sample meals left, the sample goals dropped, a real goal set."""
    added(user)
    with db.session_scope() as s:
        for m in (LiftSession, Bodyweight, Measurement, CardioEntry):
            for row in s.scalars(select(m).where(m.user_id == user.id, m.is_sample.is_(True))):
                row.deleted_at = NOW
    for g in services.get_goals(user.id)["goals"]:
        services.set_goal(user.id, {"goal_id": g["goal_id"], "status": "dropped"}, "web")
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    real = services.set_goal(user.id, {"kind": "bodyweight", "target_value": 75, "target_date": "2027-01-01"}, "web")
    assert real["goal"]["is_sample"] is False
    log = services.get_log(user.id, days=7)
    trends = services.get_trends(user.id)
    assert log["includes_sample"] is True and trends["includes_sample"] is True   # the meals alone
    assert trends["bodyweight"]["weigh_ins_28d"] == 1 and trends["calories"]["days_logged_28d"] == 28
    # get_goals reads no food: its flag is the goals' own, and the open goal is real.
    goals = services.get_goals(user.id)
    assert goals["includes_sample"] is False and [g["is_sample"] for g in goals["goals"]] == [False]
    closed = services.get_goals(user.id, include_closed=True)
    assert closed["includes_sample"] is True and [c["is_sample"] for c in closed["closed"]] == [True, True, True]


def test_sample_weigh_ins_under_a_real_goal_flag_get_goals(user):
    """The goals' numbers read the weigh-ins: sample ones in them make get_goals say so, real goal or not."""
    added(user)
    with db.session_scope() as s:
        for m in (LiftSession, Measurement, CardioEntry, FoodEntry):
            for row in s.scalars(select(m).where(m.user_id == user.id)):
                row.deleted_at = NOW
    for g in services.get_goals(user.id)["goals"]:
        services.set_goal(user.id, {"goal_id": g["goal_id"], "status": "dropped"}, "web")
    real = services.set_goal(user.id, {"kind": "bodyweight", "target_value": 75, "target_date": "2027-01-01"}, "web")
    assert real["goal"]["is_sample"] is False and real["goal"]["start_value"] == 79.01
    r = services.get_goals(user.id)
    assert [g["is_sample"] for g in r["goals"]] == [False] and r["includes_sample"] is True
    assert services.get_progress(user.id)["includes_sample"] is True


def test_a_sample_goal_alone_flags_get_goals(user):
    added(user)
    with db.session_scope() as s:   # every sample entry deleted: only the goals are left
        for m in (LiftSession, Bodyweight, Measurement, CardioEntry, FoodEntry):
            for row in s.scalars(select(m).where(m.user_id == user.id)):
                row.deleted_at = NOW
    r = services.get_goals(user.id)
    assert r["includes_sample"] is True and all(g["is_sample"] for g in r["goals"])
    assert all(g["status"] == "not enough data" for g in r["goals"])


def test_removal_says_which_of_the_persons_own_entries_lose_their_link(user):
    added(user)
    with db.new_session() as s:
        roti = s.scalar(select(SavedFood.id).where(SavedFood.user_id == user.id, SavedFood.name == "Roti"))
    mine = services.log_food(user.id, {"entries": [{"saved_food_id": roti, "servings": 3, "meal": "snack"},
                                                   {"saved_food_id": roti, "servings": 1, "meal": "lunch"}]}, "web")
    r = services.remove_sample_data(user.id, "web")
    assert r["summary"].endswith(" 2 of your own entries were logged against a sample food; they keep their numbers "
                                 "and lose the link.")
    after = [services.get_entry(user.id, "food_entry", i)["entry"] for i in mine["ids"]]
    assert [(e["kcal"], e["source"], e["saved_food_id"], e["servings"]) for e in after] == [
        (360, "saved_food", None, 3.0), (120, "saved_food", None, 1.0)]
    for i in mine["ids"]:   # an empty account again, so sample data can go back in
        services.delete_entry(user.id, "food_entry", i)
    added(user)
    with db.new_session() as s:
        roti = s.scalar(select(SavedFood.id).where(SavedFood.user_id == user.id, SavedFood.name == "Roti"))
    services.log_food(user.id, {"entries": [{"saved_food_id": roti, "servings": 2}]}, "web")
    one = services.remove_sample_data(user.id, "web")["summary"]
    assert one.endswith(" 1 of your own entries was logged against a sample food; it keeps its numbers and loses the "
                        "link.")
    added_again = services.remove_sample_data(user.id, "web")["summary"]
    assert "own entr" not in added_again


def test_never_twice(user):
    added(user)
    r = services.add_sample_data(user.id, "web")
    assert r["error"] == "already_sample" and "Remove sample data" in r["fix"]
    assert services.sample_state(user.id)["sample_rows"] == sum(COUNTS.values())


def test_bad_callers(user):
    assert services.add_sample_data(user.id, "robot")["error"] == "invalid_by"
    assert services.remove_sample_data(user.id, "robot")["error"] == "invalid_by"
    assert services.add_sample_data(987654, "web")["error"] == "no_account"
    assert services.remove_sample_data(987654, "web")["error"] == "no_account"
    assert services.sample_state(987654)["error"] == "no_account"


# ---------------------------------------------------------------- removal

def test_removal_is_complete_and_leaves_real_rows_and_other_people(user, make_user):
    other = make_user()
    added(user)
    added(other)
    # The person logs for real on top, and edits, deletes and restores sample rows.
    real = services.log_food(user.id, {"entries": [{"description": "real lunch", "kcal": 500}]}, "web")["ids"][0]
    with db.new_session() as s:
        waist = s.scalar(select(Measurement.id).where(Measurement.user_id == user.id, Measurement.kind == "waist")
                         .order_by(Measurement.id))
        squat_goal = s.scalar(select(Goal.id).where(Goal.user_id == user.id, Goal.kind == "lift"))
        walk = s.scalar(select(CardioEntry.id).where(CardioEntry.user_id == user.id).order_by(CardioEntry.id))
    services.delete_entry(user.id, "measurement", waist)
    services.set_goal(user.id, {"goal_id": squat_goal, "status": "dropped"}, "claude")
    assert services.edit_entry(user.id, "cardio_entry", walk, {"minutes": 35}, "web")["changed"]
    r = services.remove_sample_data(user.id, "web")
    assert r["removed"] == COUNTS and set(r["links"]) == {"today"}
    left = rows_of(user.id)
    assert left == {**{m.__tablename__: 0 for m in MODELS}, "food_entry": 1, "lift_set": 0}
    assert services.get_entry(user.id, "food_entry", real)["entry"]["description"] == "real lunch"
    assert rows_of(other.id, only_sample=True)["food_entry"] == 240   # the other person's are untouched
    again = services.remove_sample_data(user.id, "web")
    assert again["removed"] == {k: 0 for k in COUNTS} and "no sample data" in again["summary"]
    services.delete_entry(user.id, "food_entry", real)
    assert services.sample_state(user.id) == {"empty": True, "sample_rows": 0}
    assert added(user)["added"] == COUNTS   # and it can be added again


def test_add_and_remove_are_limited_writes(user):
    from app.models import CallLog
    with db.session_scope() as s:
        s.add_all(CallLog(user_id=user.id, function="log_lifts", started_at=NOW, ms=1, ok=True, result_bytes=1)
                  for _ in range(services.WRITE_LIMIT + 1))
    assert services.add_sample_data(user.id, "web")["error"] == "too_many_writes"
    assert services.remove_sample_data(user.id, "web")["error"] == "too_many_writes"
    assert services.sample_state(user.id)["sample_rows"] == 0


def test_the_seed_and_the_window_are_the_contracts():
    assert sample.SEED == 20261110 and sample.DAYS == 28
    assert sample.TARGETS == {"bodyweight": 77, "lift": 120, "cardio_best": 27.0}
    assert sample.GOAL_DAYS == {"bodyweight": 56, "lift": 84, "cardio_best": 56}
