"""The sixteen-week seed (evals/sixteen_weeks.py) and the eight data cases that start from it (contract
data-on-request.md, "The eval cases").

Every figure a case looks for is asserted here through Spotter's own reads (services.get_data, services.get_goals,
services.what_is_logged), on nine fixed todays: Monday 16 to Sunday 22 November 2026, then 1 December 2026 and
1 March 2027. The day is fixed by replacing the clock (db.utcnow); the person's zone is Asia/Kolkata, and noon UTC
is the same date there. Then the counts, the duplicate keys, the refusals, and each case judged on a passing and a
failing recorded run.
"""

import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import db, services
from app.models import (
    Bodyweight, CallLog, CardioEntry, Exercise, FoodEntry, Goal, LiftSession, LiftSet, Measurement, SavedFood, User,
)
from app.services import common, lifts
from evals import judge
from evals import sixteen_weeks as seed
from evals.run import LocalServer, person_rows
from evals.sixteen_weeks import sixteen_weeks

ZONE = "Asia/Kolkata"
TODAYS = [date(2026, 11, 16) + timedelta(days=i) for i in range(7)] + [date(2026, 12, 1), date(2027, 3, 1)]
FIRST = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)  # a Monday
MODELS = (LiftSession, Bodyweight, Measurement, SavedFood, FoodEntry, CardioEntry, Goal)
COUNTS = {"lift_sessions": 48, "weigh_ins": 364, "measurements": 16, "saved_foods": 4, "food_entries": 896,
          "cardio_entries": 26, "goals": 2}
SQUAT_LINE = "116.7 kg now against 120 kg on the plan line today: 3.3 kg behind."
WEIGHT_LINE = "80.9 kg now against 79.3 kg on the plan line today: 1.6 kg behind."
CASES = ["data_basketball", "data_lifting", "data_calories", "data_squat_goal", "data_back_on_track", "data_lb",
         "data_empty", "data_year_more"]
W = ["log_lifts", "log_bodyweight", "log_measurement", "log_food", "save_food", "log_cardio", "set_goal",
     "update_settings", "edit_entry", "restore_entry", "delete_entry", "import_entries", "send_feedback",
     "save_routine"]


@pytest.fixture
def clock(monkeypatch):
    """clock(today) sets the clock to noon UTC on that day."""
    def set_to(today: date) -> None:
        now = datetime(today.year, today.month, today.day, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(db, "utcnow", lambda: now)
    set_to(FIRST.date())
    return set_to


def seeded(make_user, **fields) -> int:
    user = make_user(time_zone=ZONE)
    for key, value in fields.items():
        assert "error" not in services.update_settings(user.id, {key: value}, "web")
    out = sixteen_weeks(user.id)
    assert "error" not in out, out
    return user.id


def get(user_id: int, **query) -> dict:
    out = services.get_data(user_id, query)
    assert "error" not in out, out
    return out


def table(answer: dict) -> list[dict]:
    return [dict(zip(answer["columns"], row)) for row in answer["rows"]]


def totals(answer: dict) -> dict:
    return dict(zip(answer["totals"]["columns"], answer["totals"]["values"]))


def rows_of(user_id: int) -> dict:
    with db.new_session() as s:
        out = {m.__tablename__: s.scalar(select(func.count()).select_from(m).where(m.user_id == user_id))
               for m in MODELS}
        out["lift_set"] = s.scalar(select(func.count()).select_from(LiftSet).join(LiftSession)
                                   .where(LiftSession.user_id == user_id))
        return out


# ---------------------------------------------------------------- the figures, on nine fixed todays

@pytest.mark.parametrize("today", TODAYS, ids=[d.isoformat() for d in TODAYS])
def test_every_figure_the_cases_look_for_holds(make_user, clock, today):
    clock(today)
    user_id = seeded(make_user)
    ago = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731

    # The counts it returns, from the oldest weigh-in to today.
    assert sixteen_weeks(make_user(time_zone=ZONE).id) == {"added": COUNTS, "from": ago(363), "to": today.isoformat()}

    # data_basketball: sets by region, any range. Lower a third, upper two thirds, no core or full_body.
    for start in (None, ago(6), ago(111)):
        answer = get(user_id, area="lift_sets", group_by=["region"], totals={"sets": ["sum", "share"]},
                     **({"from_date": start} if start else {}))
        groups = {r["region"]: (r["sets_sum"], r["sets_share"]) for r in table(answer)}
        assert set(groups) == {"upper", "lower"} and groups["upper"][0] == 2 * groups["lower"][0], start
        assert (groups["upper"][1], groups["lower"][1]) == (67, 33)

    # data_lifting and data_squat_goal: the squat at 100 kg x 5 since its 13th A session, so an estimated max of
    # 116.7 kg in every range of the last 7 weeks, and its goal rides with the lift's data.
    squat = get(user_id, area="lift_sets", filters={"exercise": "Back Squat"}, from_date=ago(111), order="date",
                limit=300)
    session_dates = sorted({r["date"] for r in table(squat)})
    assert len(session_dates) == 24
    heavy = sorted({r["date"] for r in table(squat) if r["weight_kg"] == 100})
    assert heavy == session_dates[12:] and all((r["weight_kg"], r["reps"]) == (100, 5) for r in table(squat)
                                               if r["date"] >= session_dates[12])
    for start in (ago(48), ago(27), ago(6)):
        answer = get(user_id, area="lift_sets", filters={"exercise": ["Back Squat"]}, from_date=start,
                     totals={"estimated_max_kg": ["max", "min"], "weight_kg": ["max", "min"]})
        assert totals(answer) == {"estimated_max_kg_max": 116.7, "estimated_max_kg_min": 116.7,
                                  "weight_kg_max": 100, "weight_kg_min": 100}, start
        assert [g["plan_line"] for g in answer["goals"]] == [SQUAT_LINE]

    # data_squat_goal and data_back_on_track: both plan lines, in get_goals and in what_is_logged.
    goals = {g["label"]: g for g in services.get_goals(user_id)["goals"]}
    assert {label: g["plan_line"] for label, g in goals.items()} == {"Body weight 78 kg": WEIGHT_LINE,
                                                                     "Back Squat 150 kg": SQUAT_LINE}
    assert all(g["status"] == "behind" for g in goals.values())
    assert [g["plan_line"] for g in services.what_is_logged(user_id)["goals"]] == [WEIGHT_LINE, SQUAT_LINE]

    # data_calories: the last 28 days of food, by meal and by food.
    by_meal = get(user_id, area="food", group_by=["meal"], totals={"kcal": ["sum", "per_day", "share"]})
    assert (by_meal["from"], by_meal["days_in_range"], by_meal["days_logged"]) == (ago(27), 28, 28)
    assert by_meal["rows"] == [["dinner", 22330, 798, 42], ["lunch", 15960, 570, 30], ["breakfast", 10640, 380, 20],
                               ["snack", 4410, 158, 8]]
    assert totals(by_meal) == {"kcal_sum": 53340, "kcal_per_day": 1905}
    by_food = table(get(user_id, area="food", group_by=["food"], totals={"kcal": ["sum", "max", "count"]}))
    assert by_food[0] == {"food": "Chicken biryani", "kcal_sum": 13440, "kcal_max": 640, "kcal_count": 21}
    named = {r["food"]: r for r in by_food}
    assert named["Dal bowl"]["kcal_count"] == 56
    assert (named["Coke 500 ml"]["kcal_count"], named["Coke 500 ml"]["kcal_max"]) == (21, 210)

    # data_empty: no sleep is logged; the one measurement kind is the waist.
    sleep = get(user_id, area="measurements", filters={"kind": "sleep_hours"})
    assert sleep["rows"] == [] and "note" in sleep
    assert get(user_id, area="measurements", from_date=ago(111), group_by=["kind"],
               totals={"values": ["count"]})["rows"] == [["waist", 16]]

    # data_year_more: the three weigh-ins above 90 kg in 364 days, by ordering and by paging with more.
    top = table(get(user_id, area="weigh_ins", from_date=ago(363), order="-weight_kg", limit=4))
    assert [(r["date"], r["weight_kg"]) for r in top[:3]] == [(ago(340), 92.4), (ago(330), 91.2), (ago(350), 90.6)]
    assert top[3]["weight_kg"] <= 86.8
    seen, page = [], get(user_id, area="weigh_ins", from_date=ago(363))
    while True:
        seen += table(page)
        if "more" not in page:
            break
        page = get(user_id, area="weigh_ins", from_date=ago(363), cursor=page["more"])
    assert len(seen) == 364 and len({r["id"] for r in seen}) == 364
    assert sorted((r["date"], r["weight_kg"]) for r in seen if r["weight_kg"] > 90) == sorted(
        [(ago(340), 92.4), (ago(330), 91.2), (ago(350), 90.6)])
    # A max by week finds all three: they are 10 days apart, each in its own week.
    weekly = table(get(user_id, area="weigh_ins", from_date=ago(363), group_by=["week"],
                       totals={"weight_kg": ["max"]}, limit=300))
    assert sorted(r["weight_kg_max"] for r in weekly if r["weight_kg_max"] > 90) == [90.6, 91.2, 92.4]


@pytest.mark.parametrize("today", TODAYS, ids=[d.isoformat() for d in TODAYS])
def test_the_heaviest_bench_of_the_last_28_days_on_a_lb_account(make_user, clock, today):
    """data_lb: 97.5 kg, the 24th A session's bench, shown 214.95 lb, and no column in kg."""
    clock(today)
    user_id = seeded(make_user, weight_unit="lb")
    answer = get(user_id, area="lift_sets", filters={"exercise": "Bench Press"}, totals={"weight_kg": ["max"]})
    assert totals(answer) == {"weight_lb_max": 214.95}
    assert not any(c.endswith("_kg") for c in answer["columns"])
    with db.new_session() as s:
        heaviest = s.scalar(select(func.max(LiftSet.weight_kg)).join(LiftSession).join(Exercise)
                            .where(LiftSession.user_id == user_id, Exercise.name == "Bench Press",
                                   LiftSession.date >= today - timedelta(days=27)))
    assert heaviest == 97.5
    # data_lb's other two routes reach the same figure.
    assert "214.95" in str(services.get_progress(user_id, "Bench Press"))
    last_bench = max(r["date"] for r in table(answer))
    assert "214.95" in str(services.get_log(user_id, date=last_bench))


# ---------------------------------------------------------------- the rows

def test_every_row_is_the_seeds_and_no_setting_moves(make_user, clock):
    user_id = seeded(make_user)
    today = FIRST.date()
    assert rows_of(user_id) == {"lift_session": 48, "bodyweight": 364, "measurement": 16, "saved_food": 4,
                                "food_entry": 896, "cardio_entry": 26, "goal": 2, "lift_set": 48 * 9}
    with db.new_session() as s:
        for m in MODELS:
            if hasattr(m, "logged_by"):
                assert {r.logged_by for r in s.scalars(select(m).where(m.user_id == user_id))} == {"web"}, m
        sessions = list(s.scalars(select(LiftSession).where(LiftSession.user_id == user_id)))
        assert {t.date.weekday() for t in sessions} == {0, 2, 4} and all(len(t.sets) == 9 for t in sessions)
        assert min(t.date for t in sessions) >= today - timedelta(days=111)
        cardio = list(s.scalars(select(CardioEntry).where(CardioEntry.user_id == user_id)))
        assert {(c.date.weekday(), c.exercise.name, c.minutes, c.distance_km) for c in cardio} == {
            (1, "Outdoor Run", 30.0, 5.0), (3, "Stationary Bike", 30.0, None)}
        assert all(21 <= (today - c.date).days <= 111 for c in cardio)
        assert len({c.estimate_weight_kg for c in cardio}) == 1  # body weight now, for every one
        waist = list(s.scalars(select(Measurement).where(Measurement.user_id == user_id).order_by(Measurement.date)))
        assert [m.value for m in waist] == [92 - 0.25 * k for k in range(16)] and {m.kind for m in waist} == {"waist"}
        assert waist[-1].date == today and {m.date.weekday() for m in waist} == {today.weekday()}
        day_kcal: dict = {}
        for e in s.scalars(select(FoodEntry).where(FoodEntry.user_id == user_id)):
            day_kcal[e.date] = day_kcal.get(e.date, 0) + e.kcal
            assert e.estimated is (e.source == "claude_estimate")
        assert len(day_kcal) == 112
        assert all(k == (1980 if (today - d).days < 21 else 1680) for d, k in day_kcal.items())
        weights = [b.kg for b in s.scalars(select(Bodyweight).where(Bodyweight.user_id == user_id))]
        assert sorted(weights)[-4:] == [86.8, 90.6, 91.2, 92.4]
        user = s.get(User, user_id)
        assert (user.calorie_target_kcal, user.weight_unit, user.time_zone) == (None, None, ZONE)  # settings untouched
        assert s.scalar(select(func.count()).select_from(CallLog)) == 0  # a seed, not a call
        assert {(g.kind, g.status, g.start_value, g.target_value, g.direction) for g in
                s.scalars(select(Goal).where(Goal.user_id == user_id))} == {
            ("bodyweight", "open", 82.0, 78.0, "down"), ("lift", "open", 80.0, 150.0, "up")}


def test_the_weights_are_the_contracts_worked_a_second_way():
    """n days before today: 80 + 0.02 a day past 21, 80 + 0.05 a day short of 21, 1 dp halves up; three highs."""
    assert seed._kg(0) == 81.1 and seed._kg(1) == 81.0 and seed._kg(20) == 80.1 and seed._kg(21) == 80.0
    assert seed._kg(363) == 86.8 and seed._kg(46) == 80.5 and seed._kg(45) == 80.5  # 80.48, 80.5 exactly
    assert (seed._kg(330), seed._kg(340), seed._kg(350)) == (91.2, 92.4, 90.6)
    assert max(seed._kg(n) for n in range(364) if n not in (330, 340, 350)) == 86.8


def test_each_row_carries_its_kinds_own_duplicate_key(make_user, clock):
    """An entry with the same content, logged now, is judged a duplicate, as after the person's own."""
    user_id = seeded(make_user)
    today = FIRST.date()  # a Monday: the new menu, a B session, a waist
    day = today.isoformat()
    with db.new_session() as s:
        ids = {f.name: f.id for f in s.scalars(select(SavedFood).where(SavedFood.user_id == user_id))}
        kg = s.scalar(select(Bodyweight.kg).where(Bodyweight.user_id == user_id, Bodyweight.date == today))
        session = s.scalar(select(LiftSession).where(LiftSession.user_id == user_id, LiftSession.date == today))
        sets = [{"exercise": x.exercise.name, "weight_kg": x.weight_kg, "reps": x.reps}
                for x in sorted(session.sets, key=lambda x: x.id)]
        own_key = session.dedupe_key == lifts.session_key(session.date, session.notes, [
            (x.exercise_id, x.set_number, x.weight_kg, x.reps) for x in session.sets])
        session_id = session.id
    assert services.log_bodyweight(user_id, {"kg": kg, "date": day}, "web")["duplicate"] is True
    assert services.log_measurement(user_id, {"kind": "waist", "value": 88.25, "date": day}, "web")["duplicate"] is True
    run_day, bike_day = (today - timedelta(days=27)).isoformat(), (today - timedelta(days=25)).isoformat()
    assert services.log_cardio(user_id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5,
                                         "date": run_day}, "web")["duplicate"] is True
    assert services.log_cardio(user_id, {"exercise": "Stationary Bike", "minutes": 30, "date": bike_day},
                               "web")["duplicate"] is True
    meals = {
        "breakfast": [{"saved_food_id": ids["Oats bowl"], "servings": 1}, {"description": "Tea with milk", "kcal": 80}],
        "lunch": [{"saved_food_id": ids["Roti"], "servings": 2}, {"saved_food_id": ids["Dal bowl"], "servings": 1},
                  {"description": "Mixed vegetable sabzi", "kcal": 150, "estimated": True}],
        "dinner": [{"description": "Chicken biryani", "kcal": 640}, {"saved_food_id": ids["Dal bowl"], "servings": 1}],
        "snack": [{"description": "Coke 500 ml", "kcal": 210}],
    }
    for meal, entries in meals.items():
        out = services.log_food(user_id, {"date": day, "entries": [{**e, "meal": meal} for e in entries]}, "web")
        assert out["duplicate"] is True, meal
    old_dinner = [{"saved_food_id": ids["Rice bowl"], "servings": 1}, {"saved_food_id": ids["Dal bowl"], "servings": 1},
                  {"description": "Chicken curry", "kcal": 350, "estimated": True}]
    out = services.log_food(user_id, {"date": (today - timedelta(days=30)).isoformat(),
                                      "entries": [{**e, "meal": "dinner"} for e in old_dinner]}, "web")
    assert out["duplicate"] is True
    # A lift has no guard (gym-logging.md decision 3): the same sets are added to that day's session.
    added_to = services.log_lifts(user_id, {"date": day, "sets": sets}, "web")
    assert own_key and added_to["duplicate"] is False and added_to["id"] == session_id
    assert rows_of(user_id)["bodyweight"] == 364 and rows_of(user_id)["food_entry"] == 896  # nothing else written


def test_refused_on_an_account_with_entries_or_a_deleted_saved_foods_name(make_user, clock):
    user = make_user(time_zone=ZONE)
    services.log_bodyweight(user.id, {"kg": 80}, "web")
    assert sixteen_weeks(user.id)["error"] == "not_empty"
    assert rows_of(user.id) == {**{m.__tablename__: 0 for m in MODELS}, "bodyweight": 1, "lift_set": 0}
    other = make_user(time_zone=ZONE)
    with db.session_scope() as s:
        s.add(SavedFood(user_id=other.id, name="Roti", name_key="roti", serving_label="1 roti", kcal_per_serving=120,
                        deleted_at=FIRST))
    assert sixteen_weeks(other.id)["error"] == "not_empty"
    assert rows_of(other.id)["bodyweight"] == 0
    assert sixteen_weeks(987654)["error"] == "no_account"
    seeded_id = seeded(make_user)
    assert sixteen_weeks(seeded_id)["error"] == "not_empty"  # never twice


# ---------------------------------------------------------------- the eight cases

def test_the_eight_cases_load_last_in_order_with_the_contracts_write_list():
    cases = judge.load_cases()
    assert [c["id"] for c in cases[-8:]] == CASES and len(cases) == 51
    assert set(W) == set(common.WRITE_FUNCTIONS) and len(W) == len(common.WRITE_FUNCTIONS)
    for case in cases[-8:]:
        assert judge.case_problems(case) == [], case["id"]
        assert case["seed"][-1] == {"service": "sixteen_weeks"}
        assert case["expect"]["no_call"] == W
        for phrase in case["expect"].get("reply_has", []) + [p for o in case["expect"].get("any_of", [])
                                                              for p in o.get("reply_has", [])]:
            assert "," not in phrase  # no thousands comma
    assert cases[-3]["seed"][0] == {"service": "update_settings", "data": {"weight_unit": "lb"}}
    assert judge.seed_service("sixteen_weeks") is sixteen_weeks


def ran(*calls) -> list[dict]:
    """A recorded run's calls: (name, arguments) or (name, arguments, is_error)."""
    return [{"name": c[0], "arguments": c[1], "is_error": c[2] if len(c) > 2 else False} for c in calls]


WHO = ("whoami", {})
CATALOGUE = ("what_is_logged", {})
LIFT_SETS = ("get_data", {"area": "lift_sets", "group_by": ["region"], "totals": {"sets": ["sum", "share"]}})
WAIST_ROWS = {"measurement": [{"id": i, "kind": "waist", "value": 92 - 0.25 * i, "deleted_at": None}
                              for i in range(16)]}

# id: (passing runs, failing runs). Each run: (calls, reply, rows).
RUNS = {
    "data_basketball": (
        [(ran(WHO, CATALOGUE, LIFT_SETS), "Your sets are 67% upper body and 33% lower body.", {}),
         (ran(LIFT_SETS), "About two thirds of your sets are upper body.", {})],
        [(ran(WHO, CATALOGUE), "Your sets are 67% upper body and 33% lower body.", {}),  # never read the sets
         (ran(("get_data", {"area": "food"})), "Your sets are 67% upper body.", {}),     # read another area
         (ran(LIFT_SETS), "Train more legs and core.", {}),                              # no figure quoted
         (ran(LIFT_SETS, ("log_lifts", {"sets": []})), "Your sets are 67% upper body.", {})]),  # wrote
    "data_lifting": (
        [(ran(LIFT_SETS), "Back Squat has sat at 100 kg for 5 since August: an estimated max of 116.7 kg.", {})],
        [(ran(LIFT_SETS), "Your squat has stalled. Add weight.", {}),
         (ran(("get_data", {"area": "cardio"})), "Back Squat is at 116.7 kg.", {}),  # read another area
         (ran(("get_progress", {"exercise": "Back Squat"})), "Back Squat is at 116.7 kg.", {}),  # no get_data
         (ran(LIFT_SETS, ("set_goal", {})), "Back Squat is at 116.7 kg.", {})]),
    "data_calories": (
        [(ran(("get_data", {"area": "food", "group_by": ["meal"]}), ("get_data", {"area": "food", "group_by": ["food"]})),
          "Dinner is 798 kcal a day, 42% of the total. Chicken biryani is 640 kcal each time.", {})],
        [(ran(("get_data", {"area": "food"})), "Dinner is 798 kcal a day, 42% of the total.", {}),  # biryani unnamed
         (ran(("get_data", {"area": "food"})), "Cut the biryani.", {}),                            # no figure
         (ran(("get_data", {"area": "days"})), "Chicken biryani is 640 kcal each time.", {})]),
    "data_squat_goal": (
        [(ran(("get_data", {"area": "lift_sets", "filters": {"exercise": "Back Squat"}})),
          "116.7 kg now against 120 kg on the plan line today: 3.3 kg behind.", {}),
         (ran(("get_data", {"area": "lift_sets", "filters": {"exercise": ["back squat"]}})), "3.3 kg behind.", {}),
         (ran(("get_progress", {"exercise": "Back Squat"})), "3.3 kg behind the plan line.", {})],
        [(ran(("get_data", {"area": "lift_sets", "filters": {"exercise": "Bench Press"}})), "3.3 kg behind.", {}),
         (ran(("get_data", {"area": "lift_sets", "filters": {"exercise": "Back Squat"}})), "Squats are stuck.", {}),
         (ran(("get_goals", {})), "3.3 kg behind.", {})]),
    "data_back_on_track": (
        [(ran(("get_goals", {})), "80.9 kg now against 79.3 kg on the plan line today: 1.6 kg behind.", {}),
         (ran(CATALOGUE), "Body weight is 1.6 kg behind the plan line.", {})],
        [(ran(("get_goals", {})), "You are a little behind.", {}),
         (ran(("get_trends", {})), "Body weight is 1.6 kg behind the plan line.", {}),
         (ran(("get_goals", {}), ("update_settings", {})), "1.6 kg behind.", {})]),
    "data_lb": (
        [(ran(("get_data", {"area": "lift_sets", "filters": {"exercise": "Bench Press"}})),
          "Your heaviest bench in the last 4 weeks was 214.95 lb for 5, on 13 Nov.", {}),
         (ran(("get_progress", {"exercise": "Bench Press"})), "214.95 lb for 5.", {}),
         (ran(("get_log", {"days": 14})), "214.95 lb.", {})],
        [(ran(("get_data", {"area": "lift_sets"})), "214.95 lb, which is 97.5 kg.", {}),  # converted to kg
         (ran(("get_data", {"area": "lift_sets"})), "About 215 lb.", {}),                 # rounded
         (ran(("get_trends", {})), "214.95 lb.", {})]),
    "data_empty": (
        [(ran(CATALOGUE), "Spotter has no sleep logged. Only your waist is measured.", WAIST_ROWS),
         (ran(("get_data", {"area": "measurements", "filters": {"kind": "sleep_hours"}})),
          "Nothing in measurements matches these filters yet: no sleep is logged.", WAIST_ROWS)],
        [(ran(CATALOGUE), "You slept about 7 hours a night.", WAIST_ROWS),
         (ran(CATALOGUE), "Sleep averages 7.5 h.", WAIST_ROWS),
         (ran(WHO), "No sleep is logged.", WAIST_ROWS),
         (ran(CATALOGUE, ("log_measurement", {"kind": "sleep_hours", "value": 7})), "No sleep is logged.", WAIST_ROWS),
         (ran(CATALOGUE), "No sleep is logged.",
          {"measurement": WAIST_ROWS["measurement"] + [{"id": 99, "kind": "sleep_hours", "value": 7,
                                                        "deleted_at": None}]})]),
    "data_year_more": (
        [(ran(("get_data", {"area": "weigh_ins", "from_date": "2025-11-17", "order": "-weight_kg"})),
          "Yes, three days: 92.4 kg on 11 Dec, 91.2 kg on 21 Dec and 90.6 kg on 1 Dec 2025.", {})],
        [(ran(("get_data", {"area": "weigh_ins"})), "Yes: 92.4 kg on 11 Dec and 91.2 kg on 21 Dec.", {}),  # one missed
         (ran(("get_data", {"area": "days"})), "92.4, 91.2 and 90.6 kg.", {}),
         (ran(("get_trends", {})), "92.4, 91.2 and 90.6 kg.", {})]),
}


@pytest.mark.parametrize("name", CASES)
def test_each_case_judges_a_passing_and_a_failing_run(name):
    case = judge.resolve(next(c for c in judge.load_cases() if c["id"] == name), {"today": "2026-11-16"})
    passing, failing = RUNS[name]
    for calls, reply, rows in passing:
        assert judge.judge(case, calls, reply, rows) == [], (name, reply)
    for calls, reply, rows in failing:
        assert judge.judge(case, calls, reply, rows), (name, reply)
    for write in W:  # the first passing run, with any one write added, fails
        calls, reply, rows = passing[0]
        assert judge.judge(case, calls + ran((write, {})), reply, rows), (name, write)


# The fix round: the review's replies, each judged on a call that takes the case's own route. The failing ones
# below passed the cases as first built.
SLEEP_GOOD = ["Nothing is logged for sleep. Most adults aim for 7 to 9 hours a night.",
              "No sleep is logged. Most adults need 7-9 hours.",
              "No sleep is logged. Most adults need 7\u20139 hours.",
              "No sleep is logged in Spotter. Aim for about 8 hours a night.",
              "No sleep is logged. Between 7 and 9 hours is the usual advice.",
              "Spotter has no sleep logged.\nYou should aim for 7 to 9 hours.",
              "Sleep has 0 entries. You can log sleep_hours as a measurement, in h.",
              "No sleep is logged. Your waist went from 92 cm to 88.25 cm. Adults need 7 or 8 hours.",
              "The average adult needs 7 to 9 hours."]
SLEEP_BAD = ["You have slept 6.5 hours a night on average.",
             "Your sleep: 7 hours most nights, 5h on Friday.",
             "Lately you slept about 6 hours a night.",
             "Your sleep averaged 7.2 hrs over the last 28 days.",
             "No sleep is logged, but you probably get 6 hours.",
             "You slept 6 to 7 hours a night."]
SQUAT_ROUTE = ("get_progress", {"exercise": "Back Squat"})
REVIEWED = {  # id: (the route's calls, rows, replies that pass, replies that fail)
    "data_basketball": (ran(LIFT_SETS), {},
                        ["Your sets are 67% upper body.", "About a third of your sets are lower body.",
                         "Two thirds of your sets are upper body."],
                        ["Add a third day of conditioning to your week.", "Do two thirds more court work.",
                         "Most of your sets are upper body."]),
    "data_lifting": (ran(LIFT_SETS), {},
                     ["Back Squat has stalled at 100 kg x 5.", "Your squat's estimated max is 116.7 kg."],
                     ["Your deadlift went from 100 kg to 157.5 kg in 16 weeks.", "Your squat has stalled.",
                      "Bench is at 116.7 kg."]),
    "data_squat_goal": (ran(SQUAT_ROUTE), {},
                        ["116.7 kg now against 120 kg on the plan line today: 3.3 kg behind.",
                         "Behind by 3.3 kg.", "116.7 kg now, against 120 kg on the plan line."],
                        ["Your squat estimated max went from 93.3 kg to 116.7 kg. It has stalled at 100 kg x 5 "
                         "since Sep.", "Your squat is 3.35 kg short.", "Your squat is behind."]),
    "data_back_on_track": (ran(("get_goals", {})), {},
                           ["80.9 kg now against 79.3 kg on the plan line today: 1.6 kg behind.",
                            "You should be at 79.3 kg today.", "Behind by 1.6 kg."],
                           ["At this pace you reach 81.61 kg by the target date, not 78 kg. Cut the biryani.",
                            "You weighed 81.6 kg in August and 80.9 kg now.", "You are a little behind."]),
    "data_empty": (ran(CATALOGUE), WAIST_ROWS, SLEEP_GOOD, SLEEP_BAD),
}


@pytest.mark.parametrize("name", list(REVIEWED))
def test_the_reviews_replies_judge_as_the_fix_round_says(name):
    case = judge.resolve(next(c for c in judge.load_cases() if c["id"] == name), {"today": "2026-11-16"})
    calls, rows, good, bad = REVIEWED[name]
    for reply in good:
        assert judge.judge(case, calls, reply, rows) == [], (name, reply)
    for reply in bad:
        assert judge.judge(case, calls, reply, rows), (name, reply)


def test_the_squat_and_weight_figures_stand_alone_and_the_routes_still_count():
    """Both data_squat_goal and data_back_on_track hold two any_of at once: a reply arm and a route arm."""
    cases = {c["id"]: c for c in judge.load_cases()}
    squat, weight = cases["data_squat_goal"], cases["data_back_on_track"]
    assert judge.judge(squat, ran(("get_goals", {})), "3.3 kg behind.", {})  # the right words, the wrong read
    assert judge.judge(squat, ran(("get_data", {"area": "lift_sets", "filters": {"exercise": "Back Squat"}})),
                       "120 kg on the plan line.", {}) == []
    assert judge.judge(weight, ran(("get_trends", {})), "79.3 kg on the plan line.", {})
    assert judge.judge(weight, ran(("get_data", {"area": "weigh_ins"})), "1.6 kg behind.", {}) == []


@pytest.mark.parametrize("name", ["data_lb", "data_empty"])
def test_the_seeds_run_through_the_runner_and_the_rows_judge_as_designed(app_settings, clock, name):
    """The runner's own seeding, as the paid run does it: data_lb sets lb first, then the sixteen weeks. data_empty's
    db check reads the seeded rows."""
    case = next(c for c in judge.load_cases() if c["id"] == name)
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    user_id, _, none = server.new_person(ZONE, case["seed"])
    assert none == {} and rows_of(user_id)["bodyweight"] == 364
    with db.new_session() as s:
        assert s.get(User, user_id).weight_unit == ("lb" if name == "data_lb" else None)
    rows = person_rows(user_id)
    passing, failing = RUNS[name]
    calls, reply, _ = passing[0]
    assert judge.judge(case, calls, reply, rows) == []
    assert judge.judge(case, calls + ran(("log_food", {})), reply, rows)
