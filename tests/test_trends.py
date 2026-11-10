"""get_trends: the smoothed weight and its rate, calories, sessions and cardio against the targets, and the
maintenance estimate, each worked by hand from a few entries."""

from datetime import timedelta

import pytest

from app import services, users


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
    assert bw["smoothed"] == 80.38 and bw["rate_per_week"] == 0.09
    assert bw["rate_window"] == {"from": day(user, -14), "to": day(user, 0), "days": 14}
    assert bw["now"] == 82 and bw["weigh_ins_28d"] == 2   # the one 30 days back is outside the 28
    assert bw["series"] == [{"date": day(user, -14), "kg": 82, "smoothed": 80.2},
                            {"date": day(user, 0), "kg": 82, "smoothed": 80.38}]


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
