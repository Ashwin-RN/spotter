"""get_trends: the smoothed weight and its rate, calories, sessions and cardio against the targets, and the
maintenance estimate, each worked by hand from a few entries."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import db, services, users


@pytest.fixture
def user(make_user):
    return make_user()


def day(user, offset):
    return (users.today_for(user) + timedelta(days=offset)).isoformat()


def weigh(user, days_ago, kg):
    assert "error" not in services.log_bodyweight(user.id, {"kg": kg, "date": day(user, -days_ago)}, "web")


def eat(user, days_ago, kcal, estimated=False, description="dal"):
    r = services.log_food(user.id, {"date": day(user, -days_ago), "entries": [
        {"description": description, "kcal": kcal, "estimated": estimated}]}, "web")
    assert "error" not in r, r
    return r["ids"][0]


def test_an_empty_account(user):
    r = services.get_trends(user.id)
    assert r["today"] == day(user, 0) and r["bodyweight"] is None and r["calories"] is None
    assert r["sessions"] == {"this_week": 0, "target_per_week": None, "per_week_4w": 0.0}
    assert r["cardio"] == {"minutes_this_week": 0, "target_minutes": None, "minutes_per_week_4w": 0.0}
    assert r["maintenance"] is None
    assert r["maintenance_note"] == "Needs food logged on 14 more of the last 21 days."
    assert r["includes_sample"] is False and "note" not in r
    assert set(r["links"]) == {"progress", "goals", "settings"}
    assert r["summary"].startswith("No weigh-ins yet. No food logged in the last 28 days. 0 lift sessions")
    assert services.get_trends(987654)["error"] == "no_account"


def test_the_smoothed_weight_and_its_rate_by_hand(user):
    for days_ago, kg in ((30, 80), (14, 82), (0, 82)):
        weigh(user, days_ago, kg)
    bw = services.get_trends(user.id)["bodyweight"]
    # Smoothed: 80; 80 + 0.1 x 2 = 80.2; 80.2 + 0.1 x 1.8 = 80.38. Rate: (80.38 - 80.2) / 14 x 7 = 0.09.
    # The rate is worked out from 80.38; the smoothed figures come back at 1 dp: 80.38 is 80.4.
    assert bw["smoothed"] == 80.4 and bw["rate_per_week"] == 0.09
    assert bw["rate_window"] == {"from": day(user, -14), "to": day(user, 0), "days": 14}
    assert bw["now"] == 82 and bw["weigh_ins_28d"] == 2   # the one 30 days back is outside the 28
    assert bw["series"] == [{"date": day(user, -14), "kg": 82, "smoothed": 80.2},
                            {"date": day(user, 0), "kg": 82, "smoothed": 80.4}]


def test_without_both_ends_the_rate_is_null_and_the_note_says_which(user):
    weigh(user, 0, 80)
    r = services.get_trends(user.id)
    assert r["bodyweight"]["rate_per_week"] is None and r["bodyweight"]["rate_window"] is None
    assert r["note"] == "The weekly rate needs a weigh-in from 14 to 42 days ago."
    services.log_bodyweight(user.id, {"kg": 81, "date": day(user, -20)}, "web")
    services.delete_entry(user.id, "bodyweight", services.get_log(user.id)["days"][0]["weigh_in"]["id"])
    r = services.get_trends(user.id)
    assert r["note"] == "The weekly rate needs a weigh-in in the last 7 days."
    assert r["bodyweight"]["now"] == 81 and r["bodyweight"]["smoothed"] == 81


def test_calories_by_hand(user):
    services.update_settings(user.id, {"calorie_target_kcal": 2200}, "web")
    eat(user, 0, 2000)
    eat(user, 0, 500, estimated=True, description="curry")
    eat(user, 3, 1800)
    eat(user, 10, 2200, estimated=True)
    eat(user, 30, 4000)                                # outside the 28 days
    gone = eat(user, 4, 3000, description="deleted")
    services.delete_entry(user.id, "food_entry", gone)
    c = services.get_trends(user.id)["calories"]
    # 7 days: 2500 and 1800 -> 2150 over 2 days. 28: (2500 + 1800 + 2200) / 3 = 2166.67 -> 2167.
    # Estimated: (500 + 2200) / 6500 = 41.5% -> 42.
    assert c == {"avg_7d": 2150, "days_logged_7d": 2, "avg_28d": 2167, "days_logged_28d": 3,
                 "estimated_share_pct_28d": 42, "target_kcal": 2200}


def test_calories_with_nothing_this_week(user):
    eat(user, 10, 1900)
    c = services.get_trends(user.id)["calories"]
    assert c["avg_7d"] is None and c["days_logged_7d"] == 0 and c["avg_28d"] == 1900
    assert "1,900 over 1 of the last 28" in services.get_trends(user.id)["summary"]


def test_sessions_and_cardio_are_rolling_weeks(user):
    services.update_settings(user.id, {"target_sessions_per_week": 4, "target_cardio_minutes_per_week": 150}, "web")
    for days_ago in (0, 6, 7, 13, 20, 27, 28):
        services.log_lifts(user.id, {"date": day(user, -days_ago), "sets": [
            {"exercise": "Deadlift", "weight_kg": 100, "reps": 5}]}, "web")
    for days_ago, minutes in ((0, 30), (6, 20), (7, 45), (27, 25), (28, 60)):
        services.log_cardio(user.id, {"exercise": "Elliptical", "minutes": minutes, "date": day(user, -days_ago)}, "web")
    r = services.get_trends(user.id)
    # Sessions: 2 in the 7 days (0 and 6 days ago); 6 in the 28 (28 days ago is outside) -> 1.5 a week.
    assert r["sessions"] == {"this_week": 2, "target_per_week": 4, "per_week_4w": 1.5}
    # Cardio: 50 minutes this week; 30 + 20 + 45 + 25 = 120 in the 28 -> 30 a week.
    assert r["cardio"] == {"minutes_this_week": 50, "target_minutes": 150, "minutes_per_week_4w": 30.0}
    assert "2 lift sessions in the last 7 days, target 4." in r["summary"]
    assert "50 cardio minutes in the last 7 days, target 150." in r["summary"]


def test_the_maintenance_estimate_by_hand(user):
    for days_ago in range(14):
        eat(user, days_ago, 2000)
    weigh(user, 27, 80.6)
    weigh(user, 0, 80.0)
    r = services.get_trends(user.id)
    # 2000 - (80.0 - 80.6) x 7,700 / 21 = 2000 + 220.
    assert r["maintenance"] == {"kcal": 2220, "intake_avg_kcal": 2000, "weight_change_kg": -0.6, "days_logged": 14,
                                "window_days": 21, "from": day(user, -20), "to": day(user, 0)}
    assert r["maintenance_note"] is None and "Maintenance about 2,220 kcal a day" in r["summary"]
    assert r["calories"]["target_kcal"] is None   # the estimate never sets the target
    assert "never changes their calorie target" in r["how"]


def test_the_maintenance_refusals(user):
    for days_ago in range(13):
        eat(user, days_ago, 2000)
    weigh(user, 27, 80.6)
    weigh(user, 0, 80.0)
    r = services.get_trends(user.id)
    assert r["maintenance"] is None and r["maintenance_note"] == "Needs food logged on 1 more of the last 21 days."
    eat(user, 13, 2000)
    services.delete_entry(user.id, "bodyweight", services.get_log(user.id)["days"][0]["weigh_in"]["id"])
    r = services.get_trends(user.id)
    assert r["maintenance"] is None
    assert r["maintenance_note"] == "Needs weigh-ins in the last 7 days and around 21 days ago."
    assert "Maintenance: needs weigh-ins" in r["summary"]


def test_another_persons_rows_never_count(user, make_user):
    other = make_user()
    weigh(other, 0, 70)
    eat(other, 0, 3000)
    services.log_lifts(other.id, {"sets": [{"exercise": "Deadlift", "weight_kg": 100, "reps": 5}]}, "web")
    r = services.get_trends(user.id)
    assert r["bodyweight"] is None and r["calories"] is None and r["sessions"]["this_week"] == 0


def test_the_how_names_the_rules_it_used(user):
    how = services.get_trends(user.id)["how"]
    for words in ("alpha 0.1", "last 7 days", "14 to 42 days ago", "7,700", "at least 14", "divided by 4"):
        assert words in how, words


# ---------------------------------------------------------------- get_days: the 28-day grid (stage 6, E2)

DAYS_KEYS = {"today", "from", "to", "days", "weeks", "weight", "lift_sessions_28d", "lift_days_28d",
             "cardio_days_28d", "includes_sample"}
DAY_KEYS = {"date", "lift_sessions", "cardio_entries", "cardio_minutes", "food_entries", "kcal", "weighed",
            "is_sample"}


def lift(user, days_ago, kg=100):
    r = services.log_lifts(user.id, {"date": day(user, -days_ago), "allow_repeat": True, "sets": [
        {"exercise": "Deadlift", "weight_kg": kg, "reps": 5}]}, "web")
    assert "error" not in r, r
    return r["id"]


def move(user, days_ago, minutes, exercise="Elliptical"):
    r = services.log_cardio(user.id, {"exercise": exercise, "minutes": minutes, "date": day(user, -days_ago),
                                      "allow_repeat": True}, "web")
    assert "error" not in r, r
    return r["id"]


def cell(r, user, days_ago):
    return next(d for d in r["days"] if d["date"] == day(user, -days_ago))


def later_by(monkeypatch, days):
    """Move the clock forward by days, so an entry logged for "today" sits after the real today."""
    then = db.utcnow() + timedelta(days=days)
    monkeypatch.setattr(db, "utcnow", lambda: then)


def test_get_days_on_an_empty_account(user):
    r = services.get_days(user.id)
    assert set(r) == DAYS_KEYS and r["today"] == r["to"] == day(user, 0) and r["from"] == day(user, -27)
    assert [d["date"] for d in r["days"]] == [day(user, n) for n in range(-27, 1)]
    assert all(d == {"date": d["date"], "lift_sessions": 0, "cardio_entries": 0, "cardio_minutes": 0.0,
                     "food_entries": 0, "kcal": None, "weighed": False, "is_sample": False} for d in r["days"])
    assert all(isinstance(d["cardio_minutes"], float) for d in r["days"] + r["weeks"])
    assert r["weeks"] == [{"to": day(user, -21 + 7 * n), "lift_sessions": 0, "cardio_minutes": 0.0} for n in range(4)]
    assert r["weight"] is None and (r["lift_sessions_28d"], r["lift_days_28d"], r["cardio_days_28d"]) == (0, 0, 0)
    assert r["includes_sample"] is False and "note" not in r
    assert services.get_days(987654)["error"] == "no_account"


def test_get_days_counts_each_day_by_hand(user, make_user):
    for days_ago in (0, 0, 6, 27, 28):                         # two sessions today; 28 days ago is outside
        lift(user, days_ago)
    gone = lift(user, 3)
    services.delete_entry(user.id, "lift_session", gone)       # a deleted row is out
    for days_ago, minutes in ((0, 10.1), (0, 20.2), (13, 45), (28, 60)):
        move(user, days_ago, minutes)
    dropped = move(user, 9, 50)
    services.delete_entry(user.id, "cardio_entry", dropped)
    eat(user, 0, 500)
    eat(user, 0, 700, description="rice")
    eat(user, 27, 1800)
    eaten = eat(user, 5, 3000, description="deleted")
    services.delete_entry(user.id, "food_entry", eaten)
    weigh(user, 10, 80)
    weighed = services.log_bodyweight(user.id, {"kg": 81, "date": day(user, -4)}, "web")["id"]
    services.delete_entry(user.id, "bodyweight", weighed)
    other = make_user()                                         # another person's rows never count
    lift(other, 1)
    move(other, 1, 30)
    eat(other, 1, 2000)
    weigh(other, 1, 70)
    r = services.get_days(user.id)
    assert {k for d in r["days"] for k in d} == DAY_KEYS
    # 10.1 + 20.2 is 30.299999999999997 as floats: 30.3 at 1 dp, as get_trends rounds.
    assert cell(r, user, 0) == {"date": day(user, 0), "lift_sessions": 2, "cardio_entries": 2,
                                "cardio_minutes": 30.3, "food_entries": 2, "kcal": 1200, "weighed": False,
                                "is_sample": False}
    assert cell(r, user, 27) == {"date": day(user, -27), "lift_sessions": 1, "cardio_entries": 0, "cardio_minutes": 0.0,
                                 "food_entries": 1, "kcal": 1800, "weighed": False, "is_sample": False}
    assert cell(r, user, 13)["cardio_minutes"] == 45.0 and cell(r, user, 10)["weighed"] is True
    # Deleted rows are out, and the other person's rows on the day before today never show.
    assert cell(r, user, 3)["lift_sessions"] == 0 and cell(r, user, 5)["kcal"] is None
    assert cell(r, user, 9)["cardio_entries"] == 0 and cell(r, user, 4)["weighed"] is False
    assert cell(r, user, 1) == {"date": day(user, -1), "lift_sessions": 0, "cardio_entries": 0, "cardio_minutes": 0.0,
                                "food_entries": 0, "kcal": None, "weighed": False, "is_sample": False}
    # 28 days: sessions today (2), 6 and 27 days ago; three days with one; cardio on two days.
    assert (r["lift_sessions_28d"], r["lift_days_28d"], r["cardio_days_28d"]) == (4, 3, 2)
    # Weeks end 21, 14, 7 and 0 days ago: 27 days ago is in the first (27 to 21), 13 in the third (13 to 7),
    # 6 and 0 in the last.
    assert r["weeks"] == [{"to": day(user, -21), "lift_sessions": 1, "cardio_minutes": 0.0},
                          {"to": day(user, -14), "lift_sessions": 0, "cardio_minutes": 0.0},
                          {"to": day(user, -7), "lift_sessions": 0, "cardio_minutes": 45.0},
                          {"to": day(user, 0), "lift_sessions": 3, "cardio_minutes": 30.3}]
    assert sum(w["lift_sessions"] for w in r["weeks"]) == r["lift_sessions_28d"]
    assert r["weight"] == {"latest": {"date": day(user, -10), "kg": 80}, "high": {"date": day(user, -10), "kg": 80},
                           "low": {"date": day(user, -10), "kg": 80}}


def test_get_days_weight_takes_the_latest_of_a_tie(user):
    for days_ago, kg in ((30, 90), (27, 70), (20, 80), (15, 82), (10, 80), (5, 82), (0, 81)):  # 30 days ago: outside
        weigh(user, days_ago, kg)
    w = services.get_days(user.id)["weight"]
    assert w == {"latest": {"date": day(user, 0), "kg": 81}, "high": {"date": day(user, -5), "kg": 82},
                 "low": {"date": day(user, -27), "kg": 70}}
    weigh(user, 27, 80)                                            # replaces the 70: now a tie for low at 80
    w = services.get_days(user.id)["weight"]
    assert w["low"] == {"date": day(user, -10), "kg": 80} and w["high"] == {"date": day(user, -5), "kg": 82}
    assert w["latest"]["kg"] == services.get_trends(user.id)["bodyweight"]["series"][-1]["kg"]


def test_get_days_counts_an_entry_dated_after_today_as_todays(user, monkeypatch):
    weigh(user, 0, 80)
    eat(user, 0, 600)
    later_by(monkeypatch, 2)
    weigh(user, 0, 79.5)                                           # two days after the real today
    eat(user, 0, 400, description="later")
    lift(user, 0)
    move(user, 0, 25)
    monkeypatch.undo()
    r = services.get_days(user.id)
    today = cell(r, user, 0)
    assert today == {"date": day(user, 0), "lift_sessions": 1, "cardio_entries": 1, "cardio_minutes": 25.0,
                     "food_entries": 2, "kcal": 1000, "weighed": True, "is_sample": False}
    assert r["days"][-1] is today and len(r["days"]) == 28        # no cell of its own
    assert r["weight"]["latest"] == {"date": day(user, 2), "kg": 79.5}   # last in the one order, its real date
    assert r["weight"]["low"] == {"date": day(user, 2), "kg": 79.5}
    assert r["weeks"][-1] == {"to": day(user, 0), "lift_sessions": 1, "cardio_minutes": 25.0}
    assert r["weight"]["latest"]["kg"] == services.get_trends(user.id)["bodyweight"]["series"][-1]["kg"]
    # get_log lists it as its own day; get_days counts it as today's (E2).
    assert [d["food"]["total_kcal"] for d in services.get_log(user.id)["days"]] == [400, 600]


def test_one_sample_row_in_the_window_says_so_and_an_older_one_does_not(user):
    from app.models import Bodyweight
    from app.services.common import SAMPLE_NOTE
    weigh(user, 40, 85)
    weigh(user, 3, 80)

    def mark(days_ago):
        with db.session_scope() as s:
            s.scalar(select(Bodyweight).where(Bodyweight.user_id == user.id, Bodyweight.date
                                              == users.today_for(user) - timedelta(days=days_ago))).is_sample = True

    mark(40)
    r = services.get_days(user.id)
    assert r["includes_sample"] is False and "note" not in r              # outside the 28 days
    mark(3)
    r = services.get_days(user.id)
    assert r["includes_sample"] is True and r["note"] == SAMPLE_NOTE
    assert cell(r, user, 3)["is_sample"] is True and cell(r, user, 2)["is_sample"] is False


def test_get_days_agrees_with_get_trends_and_get_log(user):
    """E2: the grid's sums are get_trends' totals, compared as round(x, 1) as get_trends rounds; a day's kcal is
    get_log's food.total_kcal."""
    for days_ago in (0, 2, 5, 6, 7, 11, 19, 27, 28):
        lift(user, days_ago)
    for days_ago, minutes in ((0, 12.3), (1, 33.4), (6, 20.1), (7, 41.4), (16, 17.7), (27, 9.9), (28, 50)):
        move(user, days_ago, minutes)
    for days_ago, kcal in ((0, 1999), (3, 2450), (9, 1200), (20, 2100), (26, 880), (28, 3000)):
        eat(user, days_ago, kcal)
    for days_ago, kg in ((25, 81.2), (12, 80.6), (1, 80.15)):
        weigh(user, days_ago, kg)
    grid, trends = services.get_days(user.id), services.get_trends(user.id)
    week = grid["days"][-7:]
    assert sum(d["lift_sessions"] for d in week) == trends["sessions"]["this_week"] == grid["weeks"][-1]["lift_sessions"]
    assert round(grid["lift_sessions_28d"] / 4, 1) == trends["sessions"]["per_week_4w"]
    assert sum(w["lift_sessions"] for w in grid["weeks"]) == grid["lift_sessions_28d"] == 8
    every = sum(d["cardio_minutes"] for d in grid["days"])
    assert round(every / 4, 1) == trends["cardio"]["minutes_per_week_4w"]
    assert round(sum(d["cardio_minutes"] for d in week), 1) == trends["cardio"]["minutes_this_week"]
    assert grid["weeks"][-1]["cardio_minutes"] == trends["cardio"]["minutes_this_week"]
    assert grid["weight"]["latest"]["kg"] == trends["bodyweight"]["series"][-1]["kg"]
    logged = services.get_log(user.id, days=14)["days"] + services.get_log(user.id, date=day(user, -14), days=14)["days"]
    by_date = {d["date"]: d["food"]["total_kcal"] for d in logged}
    assert len(by_date) == 28
    for d in grid["days"]:
        assert (d["kcal"] or 0) == by_date[d["date"]], d
        assert (d["kcal"] is None) == (d["food_entries"] == 0)


# ---------------------------------------------------------------- the fix round

def test_a_session_today_and_one_after_today_are_one_day(user, monkeypatch):
    """The day counts read effective dates: a session today and one dated after today are two sessions on one
    day, for lifts and for cardio."""
    lift(user, 0)
    move(user, 0, 20)
    later_by(monkeypatch, 2)
    lift(user, 0, kg=110)                                           # two days after the real today
    move(user, 0, 30)
    monkeypatch.undo()
    r = services.get_days(user.id)
    assert (r["lift_sessions_28d"], r["lift_days_28d"]) == (2, 1)
    assert r["cardio_days_28d"] == 1
    assert cell(r, user, 0)["lift_sessions"] == 2 and cell(r, user, 0)["cardio_entries"] == 2


def test_a_days_only_sample_row_can_be_a_lift_session(user):
    from app.models import LiftSession
    sample = lift(user, 4)
    lift(user, 2)
    with db.session_scope() as s:
        s.get(LiftSession, sample).is_sample = True
    r = services.get_days(user.id)
    assert cell(r, user, 4)["is_sample"] is True and cell(r, user, 2)["is_sample"] is False
    assert r["includes_sample"] is True


def test_a_days_only_sample_row_can_be_a_food_or_cardio_entry(user):
    from app.models import CardioEntry, FoodEntry
    food = eat(user, 5, 900)
    session = move(user, 6, 30)
    eat(user, 7, 800, description="real")
    with db.session_scope() as s:
        s.get(FoodEntry, food).is_sample = True
        s.get(CardioEntry, session).is_sample = True
    r = services.get_days(user.id)
    assert [cell(r, user, n)["is_sample"] for n in (5, 6, 7)] == [True, True, False]


def test_a_restored_row_comes_back_in_the_grid(user):
    session, minutes, food = lift(user, 3), move(user, 3, 40), eat(user, 3, 1500)
    weighed = services.log_bodyweight(user.id, {"kg": 80, "date": day(user, -3)}, "web")["id"]
    for kind, entry in (("lift_session", session), ("cardio_entry", minutes), ("food_entry", food),
                        ("bodyweight", weighed)):
        services.delete_entry(user.id, kind, entry)
    empty = cell(services.get_days(user.id), user, 3)
    assert (empty["lift_sessions"], empty["cardio_entries"], empty["kcal"], empty["weighed"]) == (0, 0, None, False)
    for kind, entry in (("lift_session", session), ("cardio_entry", minutes), ("food_entry", food),
                        ("bodyweight", weighed)):
        assert services.restore_entry(user.id, kind, entry)["restored"] is True
    r = services.get_days(user.id)
    back = cell(r, user, 3)
    assert (back["lift_sessions"], back["cardio_entries"], back["cardio_minutes"], back["kcal"], back["weighed"]) == \
        (1, 1, 40.0, 1500, True)
    assert r["weight"]["latest"] == {"date": day(user, -3), "kg": 80}
