"""The four-week seed (evals/four_weeks.py): refused on a non-empty account, every count and tag, deterministic,
unmarked, and the goal and trend numbers it gives on a fixed day, to the decimal. Also the read an empty account's
Today card stands on (services.account_empty).

The day is fixed by replacing the clock (db.utcnow) with NOW. The decimals below are for 2026-11-10 only; the
counts hold on any day. They were worked out a second way, in plain Python from the seed, not copied from the
service's output, and they are the same numbers the seed gave when Spotter itself could add it.
"""

import ast
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import db, services, users
from app.config import ROOT
from app.models import (
    Bodyweight, CallLog, CardioEntry, FoodEntry, Goal, LiftSession, LiftSet, Measurement, SavedFood, User,
)
from evals import four_weeks as seed
from evals.four_weeks import four_weeks

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


def rows_of(user_id) -> dict:
    with db.new_session() as s:
        out = {m.__tablename__: s.scalar(select(func.count()).select_from(m).where(m.user_id == user_id))
               for m in MODELS}
        out["lift_set"] = s.scalar(select(func.count()).select_from(LiftSet).join(LiftSession)
                                   .where(LiftSession.user_id == user_id))
        return out


def added(user):
    r = four_weeks(user.id)
    assert "error" not in r, r
    return r


# ---------------------------------------------------------------- counts, tags, no mark

def test_it_adds_four_weeks_of_everything(user):
    assert services.account_empty(user.id) == {"empty": True}
    r = added(user)
    assert r == {"added": COUNTS, "from": "2026-10-14", "to": "2026-11-10"}
    assert services.account_empty(user.id) == {"empty": False}
    assert rows_of(user.id)["lift_set"] == 6 * (9 + 7)   # six A sessions of 9 sets, six B of 7
    with db.new_session() as s:
        for m in MODELS:
            assert "is_sample" not in m.__table__.c, m   # no mark: the rows read as anyone's
            if hasattr(m, "logged_by"):
                assert {r.logged_by for r in s.scalars(select(m).where(m.user_id == user.id))} == {"web"}
        dates = [d for m in (LiftSession, Bodyweight, Measurement, FoodEntry, CardioEntry)
                 for d in s.scalars(select(m.date).where(m.user_id == user.id))]
        assert min(dates).isoformat() == "2026-10-14" and max(dates).isoformat() == "2026-11-10"
        sources = {e.source for e in s.scalars(select(FoodEntry).where(FoodEntry.user_id == user.id))}
        assert sources == {"saved_food", "you_said", "claude_estimate"}   # every tag the Food card shows
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
        names = sorted(f.name for f in s.scalars(select(SavedFood).where(SavedFood.user_id == user.id)))
        assert names == ["Dal bowl", "Oats bowl", "Rice bowl", "Roti"]
        user_row = s.get(User, user.id)
        assert user_row.calorie_target_kcal is None and user_row.time_zone is None   # settings untouched
        assert s.scalar(select(func.count()).select_from(CallLog)) == 0   # a seed, not a call


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
    """An entry with the same content, logged now, is judged the same way: a duplicate."""
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
    assert r["today"] == "2026-11-10" and r["closed"] == [] and "note" not in r
    weight, squat, run = r["goals"]
    # A body weight or lift goal's start_value and latest come back at 1 dp (stage 6, section D): the
    # 7-day average 79.01 is 79.0, the squat's 93.33 and 107.92 are 93.3 and 107.9. The pace, the
    # projection and the dates are worked out from the unrounded figures, so they do not move. The
    # run, a cardio time goal, keeps its precision.
    assert {k: weight[k] for k in ("label", "kind", "unit", "start_value", "start_date", "direction", "target_value",
                                   "target_date", "latest", "status", "needed_per_week", "actual_per_week",
                                   "projected_date")} == {
        "label": "Body weight 77 kg", "kind": "bodyweight", "unit": "kg", "start_value": 79.9,
        "start_date": "2026-10-14", "direction": "down", "target_value": 77.0, "target_date": "2027-01-05",
        "latest": 79.0, "status": "on track", "needed_per_week": -0.25, "actual_per_week": -0.29,
        "projected_date": "2026-12-26"}
    assert weight["projection"]["projected"] == 76.59 and weight["projection"]["points_used"] == 28
    assert {k: squat[k] for k in ("label", "exercise", "start_value", "start_date", "target_date", "latest", "status",
                                  "needed_per_week", "actual_per_week", "projected_date")} == {
        "label": "Back Squat 120 kg", "exercise": "Back Squat", "start_value": 93.3, "start_date": "2026-10-14",
        "target_date": "2027-02-02", "latest": 107.9, "status": "on track", "needed_per_week": 1.01,
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
    assert weight["projection"]["latest"] == 79.0 and squat["projection"]["latest"] == 107.9
    assert run["projection"]["latest"] == 29.5
    assert r["summary"] == ("3 open goals: Body weight 77 kg by Tue 5 Jan 2027, on track. Back Squat 120 kg by "
                            "Tue 2 Feb 2027, on track. Outdoor Run 5 km in 27:00 by Tue 5 Jan 2027, on track.")


def test_the_plan_line_of_the_three_goals_on_the_fixed_day(user):
    """Stage 6, E1, worked out a second way: the straight line from each goal's start to its target, read
    on 2026-11-10, against the goal's now (79.01, 107.92 and 29.5 from the test above)."""
    added(user)
    weight, squat, run = services.get_goals(user.id)["goals"]
    today = NOW.date()

    def line(start, start_on, target, target_on):
        """The plan line today, by hand: the start plus the change times the share of the days gone."""
        from datetime import date
        a, b = date.fromisoformat(start_on), date.fromisoformat(target_on)
        return start + (target - start) * (today - a).days / (b - a).days, abs(target - start) / (b - a).days

    # Body weight: 79.9 on 14 Oct to 77 on 5 Jan is 83 days; 27 are gone. 79.9 - 2.9 x 27 / 83 = 78.9566.
    # A down goal: ahead_by = plan - now = 78.9566 - 79.01 = -0.0534, behind. Now and plan both print 79 kg,
    # and the gap is real at 1 dp: close-call rule 2, so the sentence and ahead_by carry 2 dp. -0.0534 kg
    # over 2.9 / 83 = 0.0349 kg a day is -1.53 days: -2. The tag says on track: the two disagree (E1).
    plan, per_day = line(79.9, "2026-10-14", 77.0, "2027-01-05")
    assert abs(plan - 78.95663) < 1e-5 and abs((plan - 79.01) / per_day - -1.5276) < 1e-4
    assert {k: weight[k] for k in ("should_be_today", "ahead_by", "days_ahead", "vs_plan", "plan_line")} == {
        "should_be_today": 79.0, "ahead_by": -0.05, "days_ahead": -2, "vs_plan": "behind",
        "plan_line": "79.01 kg now against 78.96 kg on the plan line today: 0.05 kg behind."}
    assert weight["status"] == "on track"
    # Back Squat: 93.33 on 14 Oct to 120 on 2 Feb is 111 days; 27 gone. 93.33 + 26.67 x 27 / 111 = 99.8173.
    # Up: 107.92 - 99.8173 = 8.1027 ahead, 8.1 kg; over 26.67 / 111 = 0.2403 kg a day, 33.72 days: 34.
    plan, per_day = line(93.33, "2026-10-14", 120.0, "2027-02-02")
    assert abs(plan - 99.81730) < 1e-5 and abs((107.92 - plan) / per_day - 33.7232) < 1e-4
    assert {k: squat[k] for k in ("should_be_today", "ahead_by", "days_ahead", "vs_plan", "plan_line")} == {
        "should_be_today": 99.8, "ahead_by": 8.1, "days_ahead": 34, "vs_plan": "ahead",
        "plan_line": "107.9 kg now against 99.8 kg on the plan line today: 8.1 kg ahead."}
    # Outdoor Run, 5 km: 31 min on 17 Oct to 27 on 5 Jan is 80 days; 24 gone. 31 - 4 x 24 / 80 = 29.8 (29:48).
    # Lower is better: 29.8 - 29.5 = 0.3 min ahead, 18 seconds; at 0.05 min a day, 6 days. Minutes at 2 dp.
    plan, per_day = line(31.0, "2026-10-17", 27.0, "2027-01-05")
    assert abs(plan - 29.8) < 1e-9 and abs((plan - 29.5) / per_day - 6) < 1e-9
    assert {k: run[k] for k in ("should_be_today", "ahead_by", "days_ahead", "vs_plan", "plan_line")} == {
        "should_be_today": 29.8, "ahead_by": 0.3, "days_ahead": 6, "vs_plan": "ahead",
        "plan_line": "29:30 now against 29:48 on the plan line today: 18 seconds ahead."}


def test_get_trends_numbers_for_the_fixed_day(user):
    added(user)
    r = services.get_trends(user.id)
    bw = r["bodyweight"]
    # now and the smoothed figures at 1 dp: 79.01 is 79.0, 79.22 is 79.2, 79.71 is 79.7. The rate is worked out
    # from the unrounded line, so it does not move.
    assert {k: bw[k] for k in ("now", "smoothed", "rate_per_week", "rate_window", "weigh_ins_28d")} == {
        "now": 79.0, "smoothed": 79.2, "rate_per_week": -0.24,
        "rate_window": {"from": "2026-10-27", "to": "2026-11-10", "days": 14}, "weigh_ins_28d": 28}
    assert bw["series"][0] == {"date": "2026-10-14", "kg": 79.9, "smoothed": 79.9}
    assert bw["series"][-15] == {"date": "2026-10-27", "kg": 79.4, "smoothed": 79.7}
    assert r["calories"] == {"avg_7d": 2216, "days_logged_7d": 7, "avg_28d": 2196, "days_logged_28d": 28,
                             "estimated_share_pct_28d": 41, "target_kcal": None}
    assert r["sessions"] == {"this_week": 3, "target_per_week": None, "per_week_4w": 3.0}
    assert r["cardio"] == {"minutes_this_week": 84.5, "target_minutes": None, "minutes_per_week_4w": 85.2}
    assert r["maintenance"] == {"kcal": 2513, "intake_avg_kcal": 2198, "weight_change_kg": -0.86, "days_logged": 21,
                                "window_days": 21, "from": "2026-10-21", "to": "2026-11-10"}
    assert r["maintenance_note"] is None and "note" not in r   # a rate, so nothing to say
    # The field keeps whole kcal; the summary says the nearest 10 (D11): 2513 is "about 2,510 kcal".
    assert "Maintenance about 2,510 kcal a day, from 21 logged days of the last 21." in r["summary"]
    assert r["summary"].startswith("Body weight 79 kg now, smoothed 79.2 kg, -0.24 kg a week. Calories 2,216 a day "
                                   "over 7 logged days of the last 7, 2,196 over 28 of the last 28.")


def test_goal_charts_agrees_with_get_goals(user):
    """The Goals page's read and get_goals, on the four weeks: the same plan lines, and neither carries a note."""
    added(user)
    charts, listed = services.goal_charts(user.id), services.get_goals(user.id)
    assert set(charts) == {"today", "goals"} and "note" not in listed
    assert [n["now_basis"] for n in charts["goals"]] == ["average_7d", "best_28d", "best_28d"]
    assert [n["plan_line"] for n in charts["goals"]] == [n["plan_line"] for n in listed["goals"]]


# ---------------------------------------------------------------- the 28-day grid and the run's bests (stage 6)

def seeded_weights() -> list[float]:
    """The 28 weigh-ins a second way: the seed's first 28 draws, in plain Python, as the module notes say."""
    import random
    rng = random.Random(20261110)
    return [round(80.0 - 0.3 * i / 7 + rng.uniform(-0.3, 0.3), 1) for i in range(28)]


def test_the_grid_of_the_fixed_day(user):
    """E2 on 2026-11-10, a Tuesday: the 28 days from Wed 14 Oct. Lifts on Monday, Wednesday and Friday; cardio
    on Tuesday (Treadmill Walk 30), Thursday (Stationary Bike 25) and Saturday (Outdoor Run 31, 30.5, 30, 29.5);
    nothing trained on Sunday. Food and a weigh-in every day."""
    added(user)
    r = services.get_days(user.id)
    assert (r["today"], r["from"], r["to"]) == ("2026-11-10", "2026-10-14", "2026-11-10")
    week_of = lambda week: "".join(  # noqa: E731
        "L" if d["lift_sessions"] else "C" if d["cardio_entries"] else "-" for d in week)
    rows = [r["days"][n:n + 7] for n in range(0, 28, 7)]
    # One line a week, Wednesday to Tuesday: L lifts, C cardio, - nothing trained (the Sunday).
    assert [week_of(w) for w in rows] == ["LCLC-LC"] * 4
    by_weekday = {}
    for d in r["days"]:
        weekday = datetime.fromisoformat(d["date"]).weekday()
        by_weekday.setdefault(weekday, set()).add((d["lift_sessions"], d["cardio_entries"]))
    assert by_weekday == {0: {(1, 0)}, 1: {(0, 1)}, 2: {(1, 0)}, 3: {(0, 1)}, 4: {(1, 0)}, 5: {(0, 1)}, 6: {(0, 0)}}
    assert [d["cardio_minutes"] for d in r["days"] if d["cardio_entries"]] == [
        25.0, 31.0, 30.0, 25.0, 30.5, 30.0, 25.0, 30.0, 30.0, 25.0, 29.5, 30.0]   # Thu 15 Oct first
    assert all(d["weighed"] and d["food_entries"] in (8, 9) and 2100 <= d["kcal"] <= 2300 for d in r["days"])
    with db.new_session() as s:
        kcal = {}
        for e in s.scalars(select(FoodEntry).where(FoodEntry.user_id == user.id)):
            kcal[e.date.isoformat()] = kcal.get(e.date.isoformat(), 0) + e.kcal
    assert {d["date"]: d["kcal"] for d in r["days"]} == kcal
    assert (r["lift_sessions_28d"], r["lift_days_28d"], r["cardio_days_28d"]) == (12, 12, 12)
    assert "note" not in r


def test_the_weeks_and_the_weight_of_the_fixed_day(user):
    """E2's weeks and weight on 2026-11-10, worked out a second way. Each week ends on a Tuesday and holds three
    lift sessions, the walk (30), the bike (25) and one run: 31.0, 30.5, 30.0 and 29.5 in turn."""
    added(user)
    r = services.get_days(user.id)
    assert r["weeks"] == [{"to": to, "lift_sessions": 3, "cardio_minutes": 30 + 25 + run}
                          for to, run in (("2026-10-20", 31.0), ("2026-10-27", 30.5), ("2026-11-03", 30.0),
                                          ("2026-11-10", 29.5))]
    assert [w["cardio_minutes"] for w in r["weeks"]] == [86.0, 85.5, 85.0, 84.5]
    trends = services.get_trends(user.id)
    assert r["weeks"][-1]["cardio_minutes"] == trends["cardio"]["minutes_this_week"] == 84.5
    assert r["weeks"][-1]["lift_sessions"] == trends["sessions"]["this_week"] == 3
    # The three figures, as literals: latest 78.6 kg on 10 Nov, high 80.1 kg on Fri 16 Oct, low 78.6 kg on 10 Nov.
    # No tie on this day: test_trends holds the tie rule.
    assert r["weight"] == {"latest": {"date": "2026-11-10", "kg": 78.6}, "high": {"date": "2026-10-16", "kg": 80.1},
                           "low": {"date": "2026-11-10", "kg": 78.6}}
    # A second working from the stored rows, not from the generator: each figure is held by one weigh-in only.
    with db.new_session() as s:
        stored = [(b.date.isoformat(), b.kg) for b in s.scalars(select(Bodyweight).where(Bodyweight.user_id == user.id)
                                                              .order_by(Bodyweight.date))]
    assert len(stored) == 28 and stored[0] == ("2026-10-14", 79.9) and stored[-1] == ("2026-11-10", 78.6)
    assert [d for d, kg in stored if kg == max(k for _, k in stored)] == ["2026-10-16"]
    assert [d for d, kg in stored if kg == min(k for _, k in stored)] == ["2026-11-10"]
    assert [kg for _, kg in stored] == seeded_weights()               # the rows are the seed's draws
    assert r["weight"]["latest"]["kg"] == trends["bodyweight"]["series"][-1]["kg"]


def test_the_outdoor_run_bests_of_the_fixed_day(user):
    """E3 on 2026-11-10, worked out a second way. Four 5 km runs on the Saturdays: 31.0 on 17 Oct, then 0.5
    less a week. The fastest is 29.5 / 5 = 5.9 min a km, 5:54, on 7 Nov. All four are 5 km, so the longest
    is a four-way tie and goes to the earliest, 17 Oct. Best times: 1 km 5.9, 5 km 29.5; 10 km not reached."""
    added(user)
    r = services.get_cardio(user.id, "Outdoor Run")
    by_date = {s["date"]: s for s in r["sessions"]}
    assert [s["date"] for s in r["sessions"]] == ["2026-11-07", "2026-10-31", "2026-10-24", "2026-10-17"]
    assert [(s["minutes"], s["pace"], s["pace_min_per_km"]) for s in r["sessions"]] == [
        (29.5, "5:54", 5.9), (30.0, "6:00", 6.0), (30.5, "6:06", 6.1), (31.0, "6:12", 6.2)]
    fastest = by_date["2026-11-07"]
    assert r["bests"] == {
        "fastest_pace": {"pace": "5:54", "pace_min_per_km": 29.5 / 5, "date": "2026-11-07", "entry_id": fastest["id"]},
        "longest": {"distance_km": 5.0, "date": "2026-10-17", "entry_id": by_date["2026-10-17"]["id"]},
        "times": [{"distance_km": 1.0, "minutes": 5.9, "date": "2026-11-07", "entry_id": fastest["id"]},
                  {"distance_km": 5.0, "minutes": 29.5, "date": "2026-11-07", "entry_id": fastest["id"]}]}
    assert r["week"] == {"minutes": 84.5, "target_minutes": None}
    assert "note" not in r
    # The 5 km time is the run goal's latest: one rule for both.
    assert services.get_goals(user.id)["goals"][2]["latest"] == r["bests"]["times"][-1]["minutes"]
    summary = services.get_cardio(user.id)
    assert summary == {"today": "2026-11-10", "activities": [
        {"exercise": "Outdoor Run", "sessions_28d": 4, "minutes_28d": 121.0, "fastest_pace": "5:54",
         "last_date": "2026-11-07"},
        {"exercise": "Stationary Bike", "sessions_28d": 4, "minutes_28d": 100.0, "fastest_pace": None,
         "last_date": "2026-11-05"},
        {"exercise": "Treadmill Walk", "sessions_28d": 4, "minutes_28d": 120.0, "fastest_pace": None,
         "last_date": "2026-11-10"}]}


def test_the_pull_up_is_a_reps_only_lift(user):
    """G8: the seed's Pull-Up is 3 x 6 at 0 kg in each B session, so no estimated max; reps_only gives 6, on the
    first B session, Fri 16 Oct (a tie keeps the earliest)."""
    from app.services import lifts
    added(user)
    with db.new_session() as s:
        h = lifts.history(s, user.id, NOW.date())
    assert h["reps_only"] == {"Pull-Up": {"reps": 6, "date": "2026-10-16"}}
    assert "Pull-Up" not in h["best_set"]


def test_the_seed_and_the_window_are_the_contracts():
    assert seed.SEED == 20261110 and seed.DAYS == 28
    assert seed.TARGETS == {"bodyweight": 77, "lift": 120, "cardio_best": 27.0}
    assert seed.GOAL_DAYS == {"bodyweight": 56, "lift": 84, "cardio_best": 56}


# ---------------------------------------------------------------- refusals

def test_refused_on_an_account_with_its_own_rows(user):
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    r = four_weeks(user.id)
    assert r["error"] == "not_empty"
    assert rows_of(user.id) == {**{m.__tablename__: 0 for m in MODELS}, "bodyweight": 1, "lift_set": 0}
    assert four_weeks(987654)["error"] == "no_account"


def test_refused_when_a_deleted_saved_food_holds_one_of_its_names(user):
    """Names are unique per person, deleted ones included: the seed refuses rather than rename a food."""
    roti = services.save_food(user.id, {"name": "Roti", "serving_label": "1 big roti", "kcal_per_serving": 150}, "web")
    services.delete_entry(user.id, "saved_food", roti["id"])
    assert services.account_empty(user.id) == {"empty": True}
    assert four_weeks(user.id)["error"] == "not_empty"
    assert rows_of(user.id)["saved_food"] == 1


def test_app_never_imports_from_evals():
    """Decision 6: the seed lives outside app/, and nothing in app/ reaches for it."""
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(a.name.split(".")[0] == "evals" for a in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "evals", path


# ---------------------------------------------------------------- account_empty: Today's new-account card

@pytest.mark.parametrize("what", ["live saved food", "open goal", "lift session", "food entry", "cardio entry",
                                  "measurement", "weigh-in"])
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
     "measurement": lambda: services.log_measurement(user.id, {"kind": "waist", "value": 84}, "web"),
     "weigh-in": lambda: services.log_bodyweight(user.id, {"kg": 80}, "web")}[what]()
    assert services.account_empty(user.id) == {"empty": False}


def test_deleted_entries_deleted_saved_foods_and_closed_goals_do_not_count(user):
    weigh = services.log_bodyweight(user.id, {"kg": 80}, "web")
    services.delete_entry(user.id, "bodyweight", weigh["id"])
    toast = services.save_food(user.id, {"name": "Toast", "serving_label": "1 slice", "kcal_per_serving": 80}, "web")
    services.delete_entry(user.id, "saved_food", toast["id"])
    for status in ("dropped", "hit"):   # a closed goal does not count, whichever way it closed
        gid = services.set_goal(user.id, {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                                          "target_date": "2027-01-01"}, "web")["id"]
        assert services.account_empty(user.id) == {"empty": False}
        services.set_goal(user.id, {"goal_id": gid, "status": status}, "web")
    assert services.account_empty(user.id) == {"empty": True}
    assert services.account_empty(987654)["error"] == "no_account"


def test_account_empty_reads_one_person_only(make_user):
    a, b = make_user(), make_user()
    added(b)
    assert services.account_empty(a.id) == {"empty": True} and services.account_empty(b.id) == {"empty": False}
