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
# log first, as the no-write cases list it (log-and-guide.md: log joined every write list by script).
W = ["log", "log_lifts", "log_bodyweight", "log_measurement", "log_food", "save_food", "log_cardio", "set_goal",
     "update_settings", "edit_entry", "restore_entry", "delete_entry", "import_entries", "send_feedback",
     "save_routine", "save_program"]  # routines-and-programs.md: save_program, which the eight cases' lists gain


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
    # data_basketball also passes on the counts what_is_logged gives: entries, the last 28 days, the 28 before.
    areas = {a["area"]: a for a in services.what_is_logged(user_id)["areas"]}
    assert [(areas[n]["entries"], areas[n]["last_28d"], areas[n]["prior_28d"]) for n in
            ("lift_sessions", "lift_sets", "cardio")] == [(48, 12, 12), (432, 108, 108), (26, 2, 8)]

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
                  {"description": "Mixed vegetable sabzi", "kcal": 150, "estimated": True, "portion": "1"}],
        "dinner": [{"description": "Chicken biryani", "kcal": 640}, {"saved_food_id": ids["Dal bowl"], "servings": 1}],
        "snack": [{"description": "Coke 500 ml", "kcal": 210}],
    }
    for meal, entries in meals.items():
        out = services.log_food(user_id, {"date": day, "entries": [{**e, "meal": meal} for e in entries]}, "web")
        assert out["duplicate"] is True, meal
    old_dinner = [{"saved_food_id": ids["Rice bowl"], "servings": 1}, {"saved_food_id": ids["Dal bowl"], "servings": 1},
                  {"description": "Chicken curry", "kcal": 350, "estimated": True, "portion": "1"}]
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

def test_the_eight_cases_load_in_order_with_the_contracts_write_list():
    """In order, straight after inputs.md's two; function-list.md's G1 cases follow them."""
    cases = judge.load_cases()
    at = [c["id"] for c in cases].index("data_basketball")
    eight = cases[at:at + 8]
    assert [c["id"] for c in eight] == CASES and cases[at - 1]["id"] == "machine_calories"
    assert set(W) == set(common.WRITE_FUNCTIONS) and len(W) == len(common.WRITE_FUNCTIONS)
    for case in eight:
        assert judge.case_problems(case) == [], case["id"]
        assert case["seed"][-1] == {"service": "sixteen_weeks"}
        assert case["expect"]["no_call"] == W
        for phrase in case["expect"].get("reply_has", []) + [p for o in case["expect"].get("any_of", [])
                                                              for p in o.get("reply_has", [])]:
            assert "," not in phrase  # no thousands comma
    assert eight[-3]["seed"][0] == {"service": "update_settings", "data": {"weight_unit": "lb"}}
    assert judge.seed_service("sixteen_weeks") is sixteen_weeks


def ran(*calls) -> list[dict]:
    """A recorded run's calls: (name, arguments) or (name, arguments, is_error)."""
    return [{"name": c[0], "arguments": c[1], "is_error": c[2] if len(c) > 2 else False} for c in calls]


def answer_of(user_id: int, name: str, arguments: dict) -> dict:
    """A data read's answer as Spotter's own service gives it: what evals/recorder.py keeps of the MCP answer."""
    if name == "what_is_logged":
        return services.what_is_logged(user_id)
    if name == "get_data":
        return services.get_data(user_id, arguments)
    if name == "get_goals":
        return services.get_goals(user_id, include_closed=arguments.get("include_closed", False))
    if name == "get_progress":
        return services.get_progress(user_id, arguments.get("exercise"))
    if name == "get_trends":
        return services.get_trends(user_id)
    if name == "get_log":
        return services.get_log(user_id, date=arguments.get("date"), days=arguments.get("days", 1))
    raise KeyError(name)


def with_answers(user_id: int, calls: list[dict]) -> list[dict]:
    """The run's calls, each data read with its answer from this account (judge.DATA_READS)."""
    return [{**c, "answered": True, "answer": answer_of(user_id, c["name"], c["arguments"])}
            if c["name"] in judge.DATA_READS and not c["is_error"] else c for c in calls]


def needs_answers(case: dict) -> bool:
    """A case that counts the run's own figures (quotes_spotter of 1 or more) is judged on real answers."""
    return case["expect"].get("quotes_spotter", 0) > 0


WHO = ("whoami", {})
CATALOGUE = ("what_is_logged", {})
LIFT_SETS = ("get_data", {"area": "lift_sets", "group_by": ["region"], "totals": {"sets": ["sum", "share"]}})
SQUAT_SETS = ("get_data", {"area": "lift_sets", "filters": {"exercise": "Back Squat"}})
WAIST_ROWS = {"measurement": [{"id": i, "kind": "waist", "value": 92 - 0.25 * i, "deleted_at": None}
                              for i in range(16)]}

# id: (passing runs, failing runs). Each run: (calls, reply, rows).
CARDIO_BY_MONTH = ("get_data", {"area": "cardio", "from_date": "2026-07-29", "group_by": ["month"],
                                 "totals": {"minutes": ["sum", "count"], "distance_km": ["sum"]}})
RUNS = {
    # The release run of 2026-11-17 (contract, notes): the data read by what_is_logged or get_data, any area, and
    # any figure Spotter gave, quoted. The owner chose to take what_is_logged alone (option A).
    "data_basketball": (
        [(ran(WHO, CATALOGUE, LIFT_SETS), "Your sets are 67% upper body and 33% lower body.", {}),
         (ran(("get_data", {"area": "food"})), "You did 12 lift sessions in the last 28 days.", {}),  # another area
         (ran(CATALOGUE, CARDIO_BY_MONTH),
          "Cardio has dropped: 2 entries in the last 28 days, against 8 in the 28 days before.", {}),
         (ran(WHO, CATALOGUE, ("get_trends", {}), CARDIO_BY_MONTH),  # the release run's run 1
          'Your squat goal: Spotter says "116.7 kg now against 120 kg on the plan line today: 3.3 kg behind."', {}),
         (ran(WHO, CATALOGUE),  # the release run's runs 2 and 3: what_is_logged alone, its figures quoted
          "Your lifting is steady: 12 sessions in the last 28 days and 12 in the 28 before.\nCardio has dropped: 2 "
          "entries in the last 28 days against 8 in the 28 before, the last on 27 Oct.", {})],
        [(ran(WHO), "You did 12 lift sessions in the last 28 days.", {}),               # a figure, no Spotter read
         (ran(WHO, ("get_trends", {}), ("get_goals", {})), "Your squat goal: 3.3 kg behind.", {}),  # other reads
         (ran(LIFT_SETS), "Train more legs and core.", {}),                              # no figure quoted
         (ran(CATALOGUE), "Most of your training is upper body. Add jumps.", {}),        # no figure quoted
         (ran(LIFT_SETS), "Shoot 33% from three. Do 2 sessions a week of 3 to 5 sets of 8.", {}),  # none Spotter's
         (ran(LIFT_SETS, ("log_lifts", {"sets": []})), "Your sets are 67% upper body.", {})]),  # wrote
    # Route-free since G1 (function-list.md): any data read, and two of that run's own figures quoted.
    "data_lifting": (
        [(ran(SQUAT_SETS), "Back Squat has sat at 100 kg for 5 since August: an estimated max of 116.7 kg.", {}),
         (ran(("get_progress", {"exercise": "Back Squat"})), "Back Squat is at 116.7 kg, from 100 kg for 5.", {}),
         (ran(WHO, ("get_progress", {}), ("get_trends", {}), ("get_goals", {})),  # the release run's runs 2 and 3
          "Your squat goal: 116.7 kg now against 120 kg on the plan line. Estimated max 116.7 kg since Wed 23 Sep.",
          {})],
        [(ran(SQUAT_SETS), "Your squat has stalled. Add weight.", {}),
         (ran(("get_data", {"area": "cardio"})), "Back Squat is at 116.7 kg, from 100 kg for 5.", {}),  # not its read
         (ran(("get_goals", {})), "Back Squat is at 116.7 kg.", {}),  # one of the run's figures, not two
         (ran(WHO), "Back Squat is at 116.7 kg, from 100 kg for 5.", {}),  # no data read
         (ran(SQUAT_SETS, ("set_goal", {})), "Back Squat is at 116.7 kg, from 100 kg for 5.", {})]),
    "data_calories": (
        [(ran(("get_data", {"area": "food", "group_by": ["meal"]}), ("get_data", {"area": "food", "group_by": ["food"]})),
          "Dinner is 798 kcal a day, 42% of the total. Chicken biryani is 640 kcal each time.", {})],
        [(ran(("get_data", {"area": "food"})), "Dinner is 798 kcal a day, 42% of the total.", {}),  # biryani unnamed
         (ran(("get_data", {"area": "food"})), "Cut the biryani.", {}),                            # no figure
         (ran(("get_data", {"area": "days"})), "Chicken biryani is 640 kcal each time.", {})]),
    "data_squat_goal": (  # route-free since G1: one of the run's own figures, and the plan line's
        [(ran(SQUAT_SETS), "116.7 kg now against 120 kg on the plan line today: 3.3 kg behind.", {}),
         (ran(("get_data", {"area": "lift_sets", "filters": {"exercise": ["back squat"]}})), "3.3 kg behind.", {}),
         (ran(("get_progress", {"exercise": "Back Squat"})), "3.3 kg behind the plan line.", {}),
         (ran(("get_goals", {})), "3.3 kg behind.", {})],  # was a failing run: the goals read now counts
        [(ran(("get_data", {"area": "cardio"})), "3.3 kg behind.", {}),  # a figure no answer of the run gave
         (ran(SQUAT_SETS), "Squats are stuck.", {}),
         (ran(WHO), "3.3 kg behind.", {})]),
    "data_back_on_track": (  # route-free since G1: two of the run's own figures, and the plan line's
        [(ran(("get_goals", {})), "80.9 kg now against 79.3 kg on the plan line today: 1.6 kg behind.", {}),
         (ran(CATALOGUE), "Body weight is 80.9 kg against 79.3 kg on the plan line: 1.6 kg behind.", {})],
        [(ran(("get_goals", {})), "You are a little behind.", {}),
         (ran(CATALOGUE), "Body weight is 1.6 kg behind the plan line.", {}),  # one figure, not two
         (ran(WHO), "Body weight is 80.9 kg against 79.3 kg on the plan line: 1.6 kg behind.", {}),
         (ran(("get_goals", {}), ("update_settings", {})), "80.9 kg against 79.3 kg: 1.6 kg behind.", {})]),
    "data_lb": (  # route-free since G1: any read that gave 214.95
        [(ran(("get_data", {"area": "lift_sets", "filters": {"exercise": "Bench Press"}})),
          "Your heaviest bench in the last 4 weeks was 214.95 lb for 5, on 13 Nov.", {}),
         (ran(("get_progress", {"exercise": "Bench Press"})), "214.95 lb for 5.", {}),
         (ran(("get_log", {"days": 14})), "214.95 lb.", {})],
        [(ran(("get_data", {"area": "lift_sets"})), "214.95 lb, which is 97.5 kg.", {}),  # converted to kg
         (ran(("get_data", {"area": "lift_sets"})), "About 215 lb.", {}),                 # rounded
         (ran(("get_trends", {})), "214.95 lb.", {})]),  # a figure the run's answers never gave
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
def test_each_case_judges_a_passing_and_a_failing_run(name, make_user, clock):
    """A case that counts the run's own figures is judged on the answers Spotter gives the seeded account on the
    fixed day; the others need none."""
    case = judge.resolve(next(c for c in judge.load_cases() if c["id"] == name), {"today": "2026-11-16"})
    passing, failing = RUNS[name]
    if needs_answers(case):
        user_id = seeded(make_user, **({"weight_unit": "lb"} if name == "data_lb" else {}))
        passing = [(with_answers(user_id, calls), reply, rows) for calls, reply, rows in passing]
        failing = [(with_answers(user_id, calls), reply, rows) for calls, reply, rows in failing]
    for calls, reply, rows in passing:
        assert judge.judge(case, calls, reply, rows) == [], (name, reply, judge.judge(case, calls, reply, rows))
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
              "The average adult needs 7 to 9 hours.",
              # The release run of 2026-11-17: a figure given as an example of what to say, word for word first.
              "I can't tell you how you've been sleeping, because you haven't logged any sleep in Spotter.\n\nI "
              "searched your whole history, from 19 Nov 2025 to today, and found no sleep entries.\nWaist is the only "
              "measurement you've logged so far.\n\nSpotter can track sleep in hours. If you tell me how long you "
              "slept, for example \"7.5 hours last night\", I'll log it. Once you've logged a week or two, I can tell "
              "you how your sleep is going.",
              "If you tell me how long you slept, for example 7.5 hours, I'll log it.",
              "Tell me your sleep, e.g. 8 hours, and I'll log it.",
              "Log it in hours (e.g. 8 hours) and I'll track it.",
              "Send me a number like 7 hours and I'll log it.",
              "Tell me how long you slept, like 7 hours, and I'll log it.",
              "Just say \"7 hours last night\" and I'll log it.",
              "Just tell me “slept 6.5 hours” and I'll log it.",
              "Spotter can hold sleep hours, such as 7.5 h a night.",
              "For example: 7 hours last night.",
              "For instance, 8 hours."]
SLEEP_BAD = ["You have slept 6.5 hours a night on average.",
             "Your sleep: 7 hours most nights, 5h on Friday.",
             "Lately you slept about 6 hours a night.",
             "Your sleep averaged 7.2 hrs over the last 28 days.",
             "No sleep is logged, but you probably get 6 hours.",
             "You slept 6 to 7 hours a night.",
             # Still a figure for the person: no example marker before it in its sentence, or "like" not just before.
             "You slept 7 hours.",
             "About 6.5 hours a night, by your log.",
             "You sleep about 6.5 hours a night.",
             "You slept about 6.5 hours a night. For example, last night was short.",
             "Most adults need 8 hours, and you slept 6 hours.",
             "No sleep is logged. You probably get 6 hours, e.g. on work nights.",
             "Like most people, you get about 6 hours.",
             "It looks like you slept 6 hours a night."]
SQUAT_ROUTE = ("get_progress", {"exercise": "Back Squat"})
REVIEWED = {  # id: (the route's calls, rows, replies that pass, replies that fail)
    # The eval fix round of 2026-11-17: every figure Spotter gives from the seed passes; advice figures do not.
    "data_basketball": (ran(LIFT_SETS), {},
                        ["Your sets are 67% upper body.", "Lower body is 33% of your sets.",
                         "12 lift sessions in the last 28 days and 12 in the 28 before.",
                         "You did 12 sessions in the last 4 weeks.", "48 sessions since 29 Jul.",
                         "108 sets in the last 28 days, 432 in all.",
                         "Cardio: 2 entries in the last 28 days, against 8 in the 28 days before.",
                         "2 cardio sessions in the past 28 days, down from 8.",
                         "Cardio went from 8 to 2.", "26 cardio entries, none since 27 Oct.",
                         "Back Squat: 100 kg x 5, an estimated max of 116.7 kg.", "Squat goal: 3.3 kg behind.",
                         "116.7 kg now against 120 kg on the plan line.", "You should be at 79.3 kg today."],
                        ["Add a third day of conditioning to your week.", "Do two thirds more court work.",
                         "Most of your sets are upper body.", "About a third of your sets are lower body.",
                         "Shoot 33% from three and you break even.", "Do 2 sessions a week while you're fresh.",
                         "Start from 8 feet out and work back.", "Work up from 8 reps to 12.",
                         "Do 12 sessions of skill work before the season.", "Do 12 sessions over the next 4 weeks.",
                         "A 12-week program: 3 to 5 sets of 3 to 5 reps.", "Line sprints, 8 in a row.",
                         "Add 2 cardio sessions a week.", "Rest 120 seconds between sets.",
                         "Eat 1.6 to 2.2 g of protein per kg.", "Hold it for 116.75 seconds.",
                         "A 1,108 sets program."]),
    "data_lifting": (ran(SQUAT_SETS), {},  # two of the run's own figures since G1
                     ["Back Squat has stalled at 100 kg x 5, an estimated max of 116.7 kg.",
                      "Your squat's estimated max is 116.7 kg, at 100 kg for 5."],
                     ["Your deadlift went from 100 kg to 157.5 kg in 16 weeks.", "Your squat has stalled.",
                      "Bench is at 116.7 kg.", "Back Squat has stalled at 100 kg x 5."]),
    "data_squat_goal": (ran(SQUAT_ROUTE), {},
                        ["116.7 kg now against 120 kg on the plan line today: 3.3 kg behind.",
                         "Behind by 3.3 kg.", "116.7 kg now, against 120 kg on the plan line."],
                        ["Your squat estimated max went from 93.3 kg to 116.7 kg. It has stalled at 100 kg x 5 "
                         "since Sep.", "Your squat is 3.35 kg short.", "Your squat is behind."]),
    "data_back_on_track": (ran(("get_goals", {})), {},  # two of the run's own figures since G1
                           ["80.9 kg now against 79.3 kg on the plan line today: 1.6 kg behind.",
                            "You should be at 79.3 kg today, and you are at 80.9 kg.",
                            "Behind by 1.6 kg: 80.9 kg against 79.3 kg."],
                           ["At this pace you reach 81.61 kg by the target date, not 78 kg. Cut the biryani.",
                            "You weighed 81.6 kg in August and 80.9 kg now.", "You are a little behind."]),
    "data_empty": (ran(CATALOGUE), WAIST_ROWS, SLEEP_GOOD, SLEEP_BAD),
}


@pytest.mark.parametrize("name", list(REVIEWED))
def test_the_reviews_replies_judge_as_the_fix_round_says(name, make_user, clock):
    case = judge.resolve(next(c for c in judge.load_cases() if c["id"] == name), {"today": "2026-11-16"})
    calls, rows, good, bad = REVIEWED[name]
    if needs_answers(case):
        calls = with_answers(seeded(make_user), calls)
    for reply in good:
        assert judge.judge(case, calls, reply, rows) == [], (name, reply)
    for reply in bad:
        assert judge.judge(case, calls, reply, rows), (name, reply)


def test_the_squat_and_weight_figures_stand_alone_and_any_read_that_gave_them_counts(make_user, clock):
    """Route-free since G1 (function-list.md): no read is the right one, but the figure must be one a read of
    that run gave. Was: the routes still count."""
    cases = {c["id"]: c for c in judge.load_cases()}
    squat, weight = cases["data_squat_goal"], cases["data_back_on_track"]
    user_id = seeded(make_user)
    answers = lambda *calls: with_answers(user_id, ran(*calls))  # noqa: E731
    assert judge.judge(squat, answers(("get_goals", {})), "3.3 kg behind.", {}) == []  # the goals read gives it
    assert judge.judge(squat, answers(SQUAT_SETS), "120 kg on the plan line.", {}) == []
    assert judge.judge(squat, answers(("get_data", {"area": "food"})), "120 kg on the plan line.", {})
    assert judge.judge(squat, answers(SQUAT_SETS), "From 93.3 kg to 116.7 kg.", {})  # not the 3.3 inside 93.3
    assert judge.judge(weight, answers(("get_goals", {})), "79.3 kg on the plan line, you are at 80.9 kg.", {}) == []
    assert judge.judge(weight, answers(("get_data", {"area": "food"})), "79.3 kg on the plan line, 80.9 kg now.", {})
    assert judge.judge(weight, answers(("get_goals", {})), "You weighed 81.6 kg in August, 80.9 kg now.", {})


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
    if needs_answers(case):
        calls = with_answers(user_id, calls)
    assert judge.judge(case, calls, reply, rows) == []
    assert judge.judge(case, calls + ran(("log_food", {})), reply, rows)


# ---------------------------------------------------------------- the golden set's new direct cases (function-list.md, G1)

GOLDEN = ["gold_squat_month", "gold_weigh_morning", "gold_kcal_yesterday", "gold_squat_goal", "gold_eat_more",
          "gold_run_week"]


@pytest.mark.parametrize("today", TODAYS, ids=[d.isoformat() for d in TODAYS])
def test_every_figure_the_golden_cases_look_for_holds(make_user, clock, today):
    clock(today)
    user_id = seeded(make_user)
    ago = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    # gold_weigh_morning: this morning's weigh-in is 81.1 kg; 80.9 is the 7-day average the plan line gives.
    weighed = table(get(user_id, area="weigh_ins", from_date=ago(0)))
    assert [r["weight_kg"] for r in weighed] == [81.1]
    # gold_kcal_yesterday: 1,980 kcal, the recent menu's day.
    assert totals(get(user_id, area="food", from_date=ago(1), to_date=ago(1), totals={"kcal": ["sum"]})) == {
        "kcal_sum": 1980}
    # gold_squat_month: the last 28 or 31 days hold only 100 kg x 5, an estimated max of 116.7 kg.
    for start in (ago(27), ago(30)):
        answer = get(user_id, area="lift_sets", filters={"exercise": "Back Squat"}, from_date=start,
                     totals={"estimated_max_kg": ["max"], "weight_kg": ["max", "min"]})
        assert totals(answer) == {"estimated_max_kg_max": 116.7, "weight_kg_max": 100, "weight_kg_min": 100}, start
    # gold_squat_goal and gold_eat_more: both plan lines, and 1,905 kcal a day over 28 days, 1,980 over 7.
    assert {g["plan_line"] for g in services.get_goals(user_id)["goals"]} == {SQUAT_LINE, WEIGHT_LINE}
    calories = services.get_trends(user_id)["calories"]
    assert (calories["avg_7d"], calories["avg_28d"]) == (1980, 1905)
    # gold_run_week: no cardio in the last 7 days, nor since the week's Monday.
    for start in (ago(6), ago(today.weekday())):
        assert get(user_id, area="cardio", from_date=start)["rows"] == [], start


GOLDEN_RUNS = {  # id: (passing runs, failing runs). Each run: (calls, reply); answers from the seeded account.
    "gold_squat_month": (
        [(ran(WHO, SQUAT_SETS), "This month your squat is 100 kg for 5, an estimated max of 116.7 kg."),
         (ran(("get_progress", {"exercise": "Back Squat"})), "Your squat's estimated max is 116.7 kg."),
         (ran(SQUAT_SETS), "This month you squatted 100 kilos for 5, every session."),  # the review's S5: kilos
         (ran(SQUAT_SETS), "This month: 3 x 5 at 100kg.")],
        [(ran(SQUAT_SETS), "Your squat is going well."),
         (ran(WHO), "Your squat's estimated max is 116.7 kg."),       # no data read
         (ran(("get_data", {"area": "food"})), "Your squat's estimated max is 116.7 kg."),  # not that run's figure
         # The review's M2: two of the run's figures quoted, and the asked one made up (get_goals gives no 100).
         (ran(("get_goals", {})), "Your squat started at 80 kg and the target is 150 kg: you are at 100 kg now."),
         # Its M1: 100 from what_is_logged's help text ("the shares sum to 100") is no figure of the person's.
         (ran(CATALOGUE), "Your squat goal started at 100 kg, so this month you are fine.")]),
    "gold_weigh_morning": (
        [(ran(("get_data", {"area": "weigh_ins", "order": "-date", "limit": 1})), "You weighed 81.1 kg this morning."),
         (ran(("get_log", {})), "81.1 kg this morning.")],
        [(ran(("get_data", {"area": "weigh_ins"})), "Your 7-day average is 80.9 kg."),  # not this morning's
         (ran(WHO), "You weighed 81.1 kg this morning."),
         (ran(("get_data", {"area": "food"})), "You weighed 81.1 kg this morning.")]),  # no read of that run gave it
    "gold_kcal_yesterday": (
        [(ran(("get_log", {"date": "2026-11-15"})), "Yesterday you ate 1,980 kcal."),
         (ran(("get_data", {"area": "food", "from_date": "2026-11-15", "to_date": "2026-11-15",
                            "totals": {"kcal": ["sum"]}})), "1980 kcal yesterday.")],
        [(ran(("get_log", {"date": "2026-11-15"})), "About 2,000 kcal yesterday."),
         (ran(("get_data", {"area": "cardio"})), "Yesterday you ate 1,980 kcal.")]),
    "gold_squat_goal": (
        [(ran(("get_goals", {})), SQUAT_LINE), (ran(SQUAT_SETS), "You are 3.3 kg behind."),
         # The review's S5: right, in words other than the plan line's own.
         (ran(("get_goals", {})), "Behind. You are at 116.7 kg; the plan line says 120 kg for today."),
         (ran(("get_goals", {})), "You are behind: 116.7 kg against a plan of 120 kg.")],
        [(ran(("get_goals", {})), "You are behind."), (ran(("get_data", {"area": "food"})), "You are 3.3 kg behind.")]),
    "gold_eat_more": (
        [(ran(("get_trends", {}), ("get_goals", {})),
          "No. You eat 1,980 kcal a day this week, and your weight is 80.9 kg against 79.3 kg on the plan line.")],
        [(ran(("get_trends", {})), "Eat a bit more protein."),
         (ran(("get_trends", {})), "You eat 1,980 kcal a day."),  # one figure, not two
         (ran(WHO), "You eat 1,980 kcal a day, and weigh 80.9 kg.")]),
    "gold_run_week": (
        [(ran(("get_data", {"area": "cardio", "from_date": "2026-11-10"})),
          "You have not run this week. Your last run was 30 minutes and 5 km."),
         (ran(("get_log", {"days": 7})), "No runs this week, so 0 km."),
         (ran(("get_data", {"area": "cardio"})),
          "No run this week. Your last run was 5 km in 30 minutes, three weeks ago."),
         (ran(("get_data", {"area": "cardio"})), "Nothing this week: your last run was on 27 Oct, 5 km in 30 minutes."),
         # The re-check's right replies: the last run named, by "latest", "previous" or its date.
         (ran(("get_data", {"area": "cardio"})), "You have not run this week. The latest was 5 km, on 26 Oct."),
         (ran(("get_data", {"area": "cardio"})), "No run this week, 0 km."),
         (ran(("get_data", {"area": "cardio"})), "Your last run was 5 km on 3 November."),
         (ran(("get_data", {"area": "cardio"})), "No runs this week. Your previous run was 5.5 km in 31 minutes, on 20 Oct.")],
        [(ran(("get_data", {"area": "cardio"})), "This week you ran 5 km in 30 minutes."),  # made up
         (ran(("get_data", {"area": "cardio"})), "You ran 30 minutes this week."),
         (ran(WHO), "No runs this week."),
         # The review's M3: each made up for this week, with no word that there was no run.
         (ran(("get_data", {"area": "cardio"})), "This week: a 5 km run, 30 minutes."),
         (ran(("get_data", {"area": "cardio"})), "Your run on Tuesday was 5 km in 30 minutes. Nice work."),
         (ran(("get_data", {"area": "cardio"})), "Yes, you ran 5 km yesterday, in 30 minutes."),
         (ran(("get_data", {"area": "cardio"})), "You ran twice this week."),
         # The re-check's holes: a no-run word elsewhere, and a run made up in the same reply.
         (ran(("get_data", {"area": "cardio"})), "You haven't run much: just one 5 km run on Tuesday, in 30 minutes."),
         (ran(("get_data", {"area": "cardio"})), "Not much running this week, one 5 km run, 30 minutes."),
         (ran(("get_data", {"area": "cardio"})), "No runs logged since Sunday. On Tuesday you ran 5 km in 30 minutes."),
         (ran(("get_data", {"area": "cardio"})), "Not a bad week: you ran 5 km on Tuesday in 30 minutes."),
         (ran(("get_data", {"area": "cardio"})), "This week, you ran 5 km on Tuesday. No cardio since."),
         (ran(("get_data", {"area": "cardio"})), "No complaints, you ran 5 km on Tuesday in 30 minutes."),
         (ran(("get_data", {"area": "cardio"})), "Nothing logged for cardio before Monday, then you ran 5 km on Tuesday."),
         (ran(("get_data", {"area": "cardio"})), "You haven't run much: you ran twice this week.")]),
}


@pytest.mark.parametrize("name", GOLDEN)
def test_each_golden_case_judges_a_passing_and_a_failing_run(name, make_user, clock):
    case = judge.resolve(next(c for c in judge.load_cases() if c["id"] == name), {"today": "2026-11-16"})
    assert case["kind"] == "direct" and case["seed"] == [{"service": "sixteen_weeks"}]
    assert case["calls"]["must"] == list(judge.DATA_READS) and case["calls"]["must_not"] == W
    user_id = seeded(make_user)
    passing, failing = GOLDEN_RUNS[name]
    for calls, reply in passing:
        calls = with_answers(user_id, calls)
        assert judge.judge(case, calls, reply, {}) == [], (name, reply, judge.judge(case, calls, reply, {}))
        for write in W:  # any one write added fails
            assert judge.judge(case, calls + ran((write, {})), reply, {}), (name, write)
    for calls, reply in failing:
        assert judge.judge(case, with_answers(user_id, calls), reply, {}), (name, reply)


def test_the_best_deadlift_case_has_squats_only_and_no_made_up_deadlift_passes(app_settings):
    """Its own seed, not the sixteen weeks: both of those seeds hold a deadlift (evals/sixteen_weeks.py:56)."""
    case = next(c for c in judge.load_cases() if c["id"] == "gold_best_deadlift")
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    user_id, _, _ = server.new_person(ZONE, case["seed"])
    sessions = person_rows(user_id)["lift_session"]
    assert len(sessions) == 2 and {s["set_count"] for s in sessions} == {3}
    names = {a["area"]: a for a in services.what_is_logged(user_id)["areas"]}
    assert "Deadlift" not in str(names["lift_sets"]) and "Back Squat" in str(names["lift_sets"])
    deadlift = ("get_data", {"area": "lift_sets", "filters": {"exercise": "Deadlift"}})
    calls = with_answers(user_id, ran(WHO, CATALOGUE, deadlift))
    for reply in ("No deadlift is logged yet. Your best squat is 100 kg for 5.",
                  "You have not logged a deadlift. Squats only: 100 kg x 5 at best.",
                  "There is no deadlift in Spotter; your squats top out at 100 kg.",
                  "No deadlift among your 48 lift sessions.", "No deadlift logged in the last 28 days."):  # spans
        assert judge.judge(case, calls, reply, {}) == [], reply
    for reply in ("Your best deadlift is 140 kg.", "Deadlift: 120 kg for 5.", "About 150 kg on the deadlift.",
                  "Your best deadlift is 140.",  # the review's S1: a figure with no unit
                  "Nothing to worry about: your best deadlift is 140.",  # the re-check's: a no word elsewhere
                  "You have not deadlifted much, but your best is 140 kg.",
                  "No deadlift logged last month, but your best deadlift is 140."):  # the no-unit lacks alone
        assert judge.judge(case, calls, reply, {}), reply
    assert judge.judge(case, ran(WHO), "No deadlift is logged yet.", {})  # no data read


# The release run of 2026-11-17 (51 cases at 3 runs): five kept runs of the cases re-judged route-free in G1,
# their calls and replies word for word. Judged again on the answers those calls get on the same day's seed.
KEPT_RELEASE_RUNS = [  # (case, run, passed then, its calls, its reply)
    ('data_lb', 1, True,
     [('whoami', {}), ('what_is_logged', {}),
      ('get_data',
       {'area': 'lift_sets',
        'fields': ['date', 'set_number', 'weight_lb', 'reps', 'estimated_max_lb'],
        'filters': {'exercise': 'Bench Press'},
        'limit': 10,
        'order': '-weight_lb',
        'totals': {'weight_lb': ['max']}})],
     ("Your heaviest bench press in the last 4 weeks (21 Oct to 17 Nov) was 214.95 lb.\nThat was on Fri "
      "13 Nov.\nYou did 3 sets of 5 reps at that weight.\nSpotter's estimated one-rep max from those sets"
      " is 250.8 lb.\nYour bench went up each session in that stretch: 198.4 lb on 30 Oct, 203.95 lb on "
      "4 Nov, 209.45 lb on 9 Nov, then 214.95 lb on 13 Nov.")),
    ('data_squat_goal', 1, True,
     [('whoami', {}), ('list_exercises', {'category': 'squat'}), ('get_goals', {}), ('get_progress', {}),
      ('get_progress', {'exercise': 'Back Squat'})],
     ("Your squats went up fast until late September, and they haven't moved since.\n\nBack Squat: estima"
      "ted max now 116.7 kg, 24 sessions, last trained 13 Nov.\nYour estimated max went from 81.7 kg on "
      "29 Jul to 116.7 kg on 23 Sep, with a new best almost every session.\nYour best set is 100 kg \u00d7 5,"
      " on Wed 23 Sep.\nYou haven't set a new best since then. Every session after it came in at 116.7 k"
      "g.\nTrend: 0 kg a week over 6 weeks.\n\nYour goal is Back Squat 150 kg by Tue 9 Feb 2027, and you'r"
      "e behind.\n116.7 kg now against 120 kg on the plan line today: 3.3 kg behind.\nFrom here it needs "
      "2.78 kg a week, with 12 weeks left. Your pace so far is 0 kg a week.\n\nYour Bench Press and Deadl"
      "ift are still going up, at 4.39 kg and 4.34 kg a week, so only your squat has stalled.\nEvery ses"
      "sion since 23 Sep has the same estimated max, so you're probably repeating the same top set. You"
      " could add 2.5 kg to it, or deload for a week and then build back up.\nI can pull up the sets you"
      "'ve logged since 23 Sep if you want to see exactly what you've been doing.\n\nhttps://spotter.exam"
      "ple.app/progress/back-squat")),
    ('data_back_on_track', 3, True,
     [('whoami', {}), ('get_goals', {}), ('get_trends', {}), ('get_log', {'days': 14}),
      ('get_log', {'date': '2026-11-03', 'days': 14})],
     ("Your habits haven't slipped. What changed is your dinner, starting on Wed 28 Oct.\n\n**Still going"
      " well**\nYou weighed in on 28 of the last 28 days.\nYou logged food on 28 of the last 28 days.\nYou"
      "'re lifting 3 sessions a week.\nDeadlift, Bench Press and Overhead Press have all set new bests r"
      "ecently.\n\n**Up to Tue 27 Oct**\nDinner was a rice bowl, a dal bowl and chicken curry, with no sna"
      "ck.\nThose days came to 1,680 kcal. The chicken curry, 350 kcal, was an estimate.\nYour weigh-ins "
      "stayed between 80 and 80.1 kg.\nYou did cardio: 30 minutes on the stationary bike on Thu 22 Oct a"
      "nd a 5 km run on Tue 27 Oct.\n\n**Since Wed 28 Oct**\nEvery dinner has been chicken biryani, 640 kc"
      "al, plus a dal bowl.\nYou've had a Coke 500 ml, 210 kcal, every day.\nEvery day comes to 1,980 kca"
      "l.\nYour weight is going up 0.23 kg a week over the last 14 days.\nToday's weigh-in was 81.1 kg.\nS"
      "potter estimates your maintenance at about 1,660 kcal a day, from the last 21 days. It's an esti"
      "mate, so treat it as a guide.\nYou haven't logged any cardio since 27 Oct.\n\n**Your goals**\nBody w"
      "eight 78 kg by Tue 12 Jan 2027: behind.\n80.9 kg now against 79.3 kg on the plan line today: 1.6 "
      "kg behind.\nTo get there it needs a loss of 0.37 kg a week.\nBack Squat 150 kg by Tue 9 Feb 2027: "
      "behind.\n116.7 kg now against 120 kg on the plan line today: 3.3 kg behind.\nYou've squatted 3 \u00d7 5"
      " at 100 kg every squat day since Tue 21 Oct.\n\n**How to get back on track**\n1. Drop the daily Cok"
      "e or switch to a zero sugar one. That's 210 kcal every day.\n2. Go back to the October dinner mos"
      "t nights and keep biryani for some nights. Your weight held steady on those 1,680 kcal days. To "
      "start losing again you'll need to eat a little below that, not just get back to it.\n3. Bring bac"
      "k cardio, like the bike ride or the run you did in October.\n4. Add weight to your squat. You've "
      "finished every set at 100 kg, so my suggestion is 2.5 kg more next squat day, the same step your"
      " bench and deadlift take. That's my suggestion, not a figure from Spotter.\n\nIf you choose a dail"
      "y calorie target, I can save it so Spotter shows how much is left each day.\n\nProgress: https://s"
      "potter.example.app/progress\nGoals: https://spotter.example.app/goals")),
    ('data_lifting', 2, False,
     [('whoami', {}), ('get_progress', {}), ('get_trends', {}), ('get_goals', {}),
      ('get_progress', {'exercise': 'Back Squat'}), ('get_log', {'days': 14}), ('get_routines', {}),
      ('get_progress', {'exercise': 'Barbell Row'}), ('get_progress', {'exercise': 'Pull-Up'})],
     ("Your log shows a clear pattern. The lifts where you add weight are going up. The two where you d"
      "on't are flat.\n\n**Going up**\n- Bench Press: estimated max 113.8 kg, trend 4.39 kg a week.\n- Dead"
      "lift: estimated max 183.8 kg, trend 4.34 kg a week.\n- Overhead Press: estimated max 68.5 kg, tre"
      "nd 2.17 kg a week.\n- On all three, you've used a heavier weight than the time before in each ses"
      "sion I looked at.\n\n**Flat**\n- Back Squat: estimated max 116.7 kg since Wed 23 Sep, trend 0 kg a "
      "week over 6 weeks.\n- Your last three squat sessions were all 3 \u00d7 5 at 100 kg, and you finished e"
      "very rep.\n- Barbell Row: best set 60 kg \u00d7 8 on Wed 29 Jul, estimated max 76 kg, and no new bests"
      " since.\n- Pull-Up: most reps 6.\n\nSo your squat hasn't stalled. You just stopped adding weight to"
      " it.\n\n**What to do**\n1. Squat: add 2.5 kg next session. Keep adding it each squat day as long as"
      " you get all 15 reps.\n2. If you miss reps, repeat that weight once. If you miss again, drop abou"
      "t 10% and build back up.\n3. Row: add one rep a session until you reach 3 \u00d7 10, then add 2.5 kg a"
      "nd go back to 3 \u00d7 8.\n4. Pull-Up: add one rep to one set each session. Once you can do 3 \u00d7 8, add"
      " a small weight.\n5. Bench, deadlift and overhead press: keep doing what you're doing.\n\n**Your go"
      "als**\n- Back Squat 150 kg by Tue 9 Feb 2027: 116.7 kg now against 120 kg on the plan line today:"
      " 3.3 kg behind.\n- It needs 2.78 kg a week from here.\n- Body weight 78 kg by Tue 12 Jan 2027: 80."
      "9 kg now against 79.3 kg on the plan line today: 1.6 kg behind.\n- Your weight trend is +0.23 kg "
      "a week, eating 1,980 kcal a day over the last 7 days.\n- Spotter estimates your maintenance at ab"
      "out 1,660 kcal a day. That's an estimate.\n\nThese two goals pull against each other. Your squat g"
      "oes up fastest when you eat enough to gain a little, and your weight goal needs you to eat less."
      " You may want to choose which one comes first for now.\n\nGeneral advice, not from your logs: slee"
      "p and protein matter a lot for strength. Your food log only records calories, so I can't check y"
      "our protein.\n\nDid you keep your squat at 100 kg on purpose, for example because of form, pain or"
      " your program? Or did it just stay there?\n\nYour lift charts: https://spotter.example.app/progres"
      "s")),
    ('how_am_i_doing', 3, True,
     [('whoami', {}), ('get_trends', {})],
     ("You're heading down slowly and steadily.\n\nBody weight is 79 kg now, smoothed 79.2 kg.\nThe trend "
      "is -0.24 kg a week.\nYou weighed in all 28 of the last 28 days.\n\nYou're averaging 2,216 kcal a da"
      "y over the last 7 days.\nOver the last 28 days it's 2,196 kcal a day.\n41% of the last 28 days' ca"
      "lories are estimates, so treat these figures as approximate.\n\nYour maintenance is about 2,510 kc"
      "al a day, from 21 logged days of the last 21. This is an estimate.\nThat puts you a little under "
      "maintenance, which matches the slow loss.\n\nYou did 3 lift sessions in the last 7 days, the same "
      "as your 4 week average.\nYou did 85 cardio minutes in the last 7 days, in line with your usual we"
      "ek.\n\nYou haven't set a calorie, session or cardio target, so I can't say whether you're on targe"
      "t. I can set those up if you'd like.\n\nProgress: https://spotter.example.app/progress")),
]


@pytest.mark.parametrize("run", KEPT_RELEASE_RUNS, ids=[f"{r[0]}-{r[1]}" for r in KEPT_RELEASE_RUNS])
def test_the_re_judged_cases_pass_their_kept_runs(make_user, clock, run):
    """The seeds have no randomness but the day, so the release run's own day gives the answers its model read, ids
    aside. data_lifting run 2 failed then on its route alone (no get_data); route-free, it passes."""
    from evals.four_weeks import four_weeks
    name, number, passed_then, sent, reply = run
    clock(date(2026, 11, 17))
    case = judge.resolve(next(c for c in judge.load_cases() if c["id"] == name), {"today": "2026-11-17"})
    if name == "how_am_i_doing":
        user = make_user(time_zone=ZONE)
        assert "error" not in four_weeks(user.id)
        user_id = user.id
    else:
        user_id = seeded(make_user, **({"weight_unit": "lb"} if name == "data_lb" else {}))
    calls = with_answers(user_id, ran(*sent))
    assert judge.judge(case, calls, reply, {}) == [], (name, number, passed_then)
    assert judge.judge(case, [c for c in calls if c["name"] not in judge.DATA_READS], reply, {})  # without its reads
