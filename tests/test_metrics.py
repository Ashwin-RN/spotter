"""app/metrics.py: every formula Spotter quotes. Pure functions, so no database is needed."""

from datetime import date, timedelta

import pytest

from app.metrics import (
    bodyweight_average, bodyweight_now, bodyweight_trend, days_since_last, epley, prs, projection, recent_best,
    session_best, sessions_vs_target, weekly_sessions,
)

TODAY = date(2026, 11, 6)


def ago(n):
    return TODAY - timedelta(days=n)


LIFT = [(ago(21), 80), (ago(14), 80.5), (ago(7), 81), (TODAY, 81.5)]        # +0.5 kg a week
CUT = [(ago(21), 90), (ago(14), 89.8), (ago(7), 89.6), (TODAY, 89.4)]       # -0.2 kg a week


# ---------------------------------------------------------------- Epley, session best, new bests

def test_epley_value():
    assert epley(100, 5) == pytest.approx(116.67)
    assert epley(100, 1) == pytest.approx(103.33)
    assert epley(90, 10) == pytest.approx(120.0)


def test_epley_out_of_range():
    assert epley(100, 11) is None
    assert epley(100, 0) is None
    assert epley(0, 5) is None
    assert epley(-10, 5) is None


def test_session_best_ignores_high_rep_sets():
    assert session_best([(100, 5), (110, 3), (60, 20)]) == epley(110, 3)
    assert session_best([(60, 20)]) is None
    assert session_best([]) is None


def test_prs_both_kinds():
    out = prs([(100, 5), (100, 5)], [(105, 5)])
    kinds = {p["kind"]: p for p in out}
    assert kinds["estimated_max"]["value"] == epley(105, 5)
    assert kinds["estimated_max"]["previous"] == epley(100, 5)
    assert kinds["weight"] == {"kind": "weight", "value": 105, "previous": 100}


def test_prs_estimated_max_only():
    # Same top weight, more reps: a new estimated max, not a new heaviest weight.
    assert [p["kind"] for p in prs([(100, 5)], [(100, 8)])] == ["estimated_max"]


def test_prs_weight_only_from_high_reps():
    # Heavier for 12 reps: a new heaviest weight; Epley ignores 12 reps.
    assert [p["kind"] for p in prs([(100, 5)], [(102, 12)])] == ["weight"]


def test_prs_equal_is_not_a_new_best():
    assert prs([(100, 5)], [(100, 5)]) == []


def test_prs_first_session_has_no_baseline():
    assert prs([], [(100, 5)]) == []


def test_prs_no_qualifying_baseline():
    # Earlier sets were all 12+ reps: no Epley baseline, so only the weight kind can fire.
    assert [p["kind"] for p in prs([(80, 12)], [(85, 5)])] == ["weight"]


def test_prs_empty_session():
    assert prs([(100, 5)], []) == []


def test_recent_best_is_the_best_session_of_the_last_28_days():
    heavy, light = epley(140, 5), epley(100, 5)
    assert recent_best([(ago(21), heavy), (TODAY, light)], TODAY) == heavy   # one light session does not pull it down
    assert recent_best([(ago(27), heavy), (TODAY, light)], TODAY) == heavy   # 27 days back is inside
    assert recent_best([(ago(28), heavy), (TODAY, light)], TODAY) == light   # 28 days back is outside
    assert recent_best([(ago(40), heavy), (ago(35), light)], TODAY) == light  # none that recent: the latest
    # A point after today (left there by a change of time zone) is the most recent of all.
    assert recent_best([(TODAY + timedelta(days=1), heavy), (ago(3), light)], TODAY) == heavy
    assert recent_best([(TODAY + timedelta(days=1), light)], TODAY) == light
    assert recent_best([], TODAY) is None


# ---------------------------------------------------------------- training days

def test_days_since_last():
    assert days_since_last([ago(9), ago(12)], TODAY) == 9
    assert days_since_last([TODAY], TODAY) == 0
    assert days_since_last([], TODAY) is None
    assert days_since_last([TODAY + timedelta(days=2)], TODAY) == 0      # after today counts as today, never negative
    assert days_since_last([ago(5), TODAY + timedelta(days=1)], TODAY) == 0


def test_sessions_vs_target_full_four_weeks():
    dates = [ago(i) for i in range(0, 28, 7)] + [ago(i) for i in range(2, 28, 7)] + [ago(i) for i in range(4, 28, 7)]
    assert sessions_vs_target(dates, 3, ago(60), TODAY) == {"logged": 12, "target": 12, "pct": 100, "weeks": 4}


def test_sessions_vs_target_caps_at_four_weeks_and_ignores_older_sessions():
    dates = [ago(i) for i in range(0, 70, 3)]   # every 3rd day for 10 weeks
    a = sessions_vs_target(dates, 3, ago(70), TODAY)
    assert a["target"] == 12 and a["weeks"] == 4
    assert a["logged"] == 10       # days 0, 3, ..., 27


def test_sessions_vs_target_partial_weeks():
    # Counting for 15 days: 2 whole weeks, so the window is the last 14 days.
    a = sessions_vs_target([ago(1), ago(5), ago(10), ago(14)], 3, ago(15), TODAY)
    assert a["target"] == 6 and a["weeks"] == 2
    assert a["logged"] == 3        # ago(14) is outside the 14-day window
    assert a["pct"] == 50


def test_sessions_vs_target_none_cases():
    assert sessions_vs_target([TODAY], 3, None, TODAY) is None         # nothing to count from
    assert sessions_vs_target([TODAY], 3, ago(3), TODAY) is None       # under one whole week
    assert sessions_vs_target([TODAY], 0, ago(30), TODAY) is None
    assert sessions_vs_target([TODAY], None, ago(30), TODAY) is None


def test_sessions_vs_target_counts_a_date_after_today_as_today():
    a = sessions_vs_target([TODAY + timedelta(days=1), ago(1)], 2, ago(7), TODAY)
    assert a["logged"] == 2 and a["target"] == 2 and a["pct"] == 100


def test_weekly_sessions_whole_weeks_since():
    dates = [TODAY, ago(6), ago(7), ago(20), TODAY + timedelta(days=1)]
    weeks = weekly_sessions(dates, ago(22), TODAY)
    assert [w["logged"] for w in weeks] == [1, 1, 3]           # oldest first; the date after today is this week's
    assert weeks[-1]["end"] == TODAY and weeks[-1]["start"] == ago(6)
    assert weeks[0]["start"] >= ago(22)
    assert len(weekly_sessions(dates, ago(200), TODAY)) == 8
    assert weekly_sessions(dates, ago(6), TODAY) == []
    assert weekly_sessions(dates, None, TODAY) == []


# ---------------------------------------------------------------- projection

def test_projection_on_track():
    pts = [(ago(35), 100), (ago(28), 102), (ago(21), 104), (ago(14), 106), (ago(7), 108), (TODAY, 110)]
    p = projection(pts, TODAY, goal_target=115, goal_date=TODAY + timedelta(days=21))
    assert p["slope_per_week"] == pytest.approx(2.0)
    assert p["projected"] == pytest.approx(116.0)
    assert p["status"] == "on track"
    assert p["points_used"] == 6


def test_projection_behind():
    p = projection(LIFT, TODAY, goal_target=100, goal_date=TODAY + timedelta(days=28))
    assert p["status"] == "behind"
    assert p["projected"] == pytest.approx(83.5)


def test_a_point_after_today_is_the_latest_and_sits_at_today_on_the_line():
    pts = [(ago(21), 100), (ago(14), 101), (ago(7), 102), (TODAY + timedelta(days=1), 103)]
    p = projection(pts, TODAY)
    assert p["points_used"] == 4 and p["latest"] == 103
    assert p["slope_per_week"] == projection(pts[:3] + [(TODAY, 103)], TODAY)["slope_per_week"]


def test_projection_uses_last_six_weeks_only():
    old = [(ago(100), 10), (ago(90), 10), (ago(43), 10)]   # 43 days back is just outside the 42-day window
    recent = [(ago(21), 100), (ago(14), 100), (ago(7), 100)]
    assert projection(old + recent, TODAY) is None        # only 3 recent points
    p = projection(old + recent + [(TODAY, 100)], TODAY)
    assert p["slope_per_week"] == 0 and p["points_used"] == 4
    assert projection(recent + [(ago(42), 100)], TODAY)["points_used"] == 4  # 42 days back is inside it


def test_projection_not_enough_points_or_one_date():
    assert projection([], TODAY) is None
    assert projection([(TODAY, 1), (TODAY, 2), (TODAY, 3), (TODAY, 4)], TODAY) is None


def test_projection_without_goal():
    p = projection([(ago(21), 100), (ago(14), 101), (ago(7), 102), (TODAY, 103)], TODAY)
    assert p["projected"] is None and p["status"] is None
    assert p["slope_per_week"] == pytest.approx(1.0)
    assert p["weeks_left"] is None and p["needed_per_week"] is None
    assert set(p) == {"slope_per_week", "projected", "status", "points_used", "latest", "weeks_left",
                      "needed_per_week"}


def test_projection_lower_is_better():
    pts = [(ago(21), 90), (ago(14), 89), (ago(7), 88), (TODAY, 87)]
    goal = TODAY + timedelta(days=28)
    assert projection(pts, TODAY, 84, goal, lower_is_better=True)["status"] == "on track"
    assert projection(pts, TODAY, 80, goal, lower_is_better=True)["status"] == "behind"


def test_a_goal_already_met_is_reached_even_while_the_line_falls():
    pts = [(ago(21), 120), (ago(14), 118), (ago(7), 117), (TODAY, 116)]
    assert projection(pts, TODAY, goal_target=110, goal_date=TODAY + timedelta(days=60))["status"] == "reached"


def test_needed_per_week_for_a_lift_goal():
    p = projection(LIFT, TODAY, goal_target=100, goal_date=TODAY + timedelta(days=28))
    assert p["weeks_left"] == 4.0
    assert p["needed_per_week"] == pytest.approx(4.63, abs=0.01)    # (100 - 81.5) / 4
    assert p["slope_per_week"] == 0.5


def test_needed_per_week_uses_exact_days_and_rounds_weeks_to_one_place():
    p = projection(LIFT, TODAY, goal_target=100, goal_date=TODAY + timedelta(days=10))
    assert p["weeks_left"] == 1.4
    assert p["needed_per_week"] == round(18.5 / (10 / 7), 2) == 12.95


def test_needed_per_week_is_negative_for_a_weight_loss_goal():
    p = projection(CUT, TODAY, goal_target=85, goal_date=TODAY + timedelta(days=56), lower_is_better=True)
    assert p["weeks_left"] == 8.0 and p["needed_per_week"] == -0.55    # (85 - 89.4) / 8
    assert p["slope_per_week"] == -0.2 and p["status"] == "behind"


def test_current_replaces_the_last_point_but_not_the_slope():
    goal = TODAY + timedelta(days=28)
    plain = projection(LIFT, TODAY, goal_target=100, goal_date=goal)
    lifted = projection(LIFT, TODAY, goal_target=100, goal_date=goal, current=90)
    assert lifted["slope_per_week"] == plain["slope_per_week"] and lifted["projected"] == plain["projected"]
    assert lifted["latest"] == 90 and lifted["needed_per_week"] == round(10 / 4, 2)
    assert projection(LIFT, TODAY, goal_target=85, goal_date=goal, current=90)["status"] == "reached"
    # The last point (89.4) would say reached; at 90 now it is not yet, though the falling line gets there.
    assert projection(CUT, TODAY, goal_target=89.5, goal_date=goal, lower_is_better=True,
                      current=90)["status"] == "on track"


def test_goal_date_today_or_past_gives_zero_weeks_and_no_needed():
    for goal_date in (TODAY, ago(3)):
        p = projection(LIFT, TODAY, goal_target=100, goal_date=goal_date)
        assert p["weeks_left"] == 0 and p["needed_per_week"] is None
        assert p["projected"] is not None and p["status"] == "behind"


def test_target_already_reached_needs_nothing_more():
    p = projection(LIFT, TODAY, goal_target=81, goal_date=TODAY + timedelta(days=28))
    assert p["status"] == "reached" and p["needed_per_week"] == 0
    p = projection(CUT, TODAY, goal_target=90, goal_date=TODAY + timedelta(days=28), lower_is_better=True)
    assert p["status"] == "reached" and p["needed_per_week"] == 0
    exactly = projection(LIFT, TODAY, goal_target=81.5, goal_date=TODAY + timedelta(days=28))
    assert exactly["status"] == "reached" and exactly["needed_per_week"] == 0  # at the target counts


# ---------------------------------------------------------------- bodyweight

def test_bodyweight_average_seven_days_ending_on_a_date():
    end = date(2026, 3, 31)
    pts = [(date(2026, 3, 24), 99.0),   # 8 days before: outside
           (date(2026, 3, 25), 80.0), (date(2026, 3, 28), 81.0), (date(2026, 3, 31), 82.5),
           (date(2026, 4, 1), 50.0)]    # after the end: outside
    assert bodyweight_average(pts, end) == 81.17   # gaps in the window do not matter
    assert bodyweight_average(pts, date(2026, 3, 10)) is None


def test_bodyweight_now_is_the_7_day_average_else_the_latest():
    assert bodyweight_now([(ago(3), 81.08), (TODAY, 79.6)], TODAY) == 80.34
    assert bodyweight_now([(ago(20), 82.0), (ago(10), 81.0)], TODAY) == 81.0   # nothing in the last 7 days
    assert bodyweight_now([(TODAY + timedelta(days=1), 70.0)], TODAY) == 70.0     # after today: today's
    assert bodyweight_now([(ago(2), 80.0), (TODAY + timedelta(days=1), 81.0)], TODAY) == 80.5
    assert bodyweight_now([], TODAY) is None


def test_bodyweight_trend_averages_then_simple_change_then_single():
    today = date(2026, 6, 30)
    four_weeks = [(today - timedelta(days=n), 90.0) for n in (28, 30, 33)] + \
                 [(today - timedelta(days=n), 88.0) for n in (0, 3)]
    t = bodyweight_trend(four_weeks, today)
    assert t["method"] == "7-day average" and t["now_avg"] == 88.0 and t["then_avg"] == 90.0
    assert t["change_kg"] == -2.0 and t["weigh_ins"] == 5
    later = bodyweight_trend(four_weeks + [(today + timedelta(days=1), 85.0)], today)  # counts in today's average
    assert later["now_avg"] == 87.0 and later["weigh_ins"] == 6
    assert later["latest"] == {"date": (today + timedelta(days=1)).isoformat(), "kg": 85.0}  # keeps its own date
    recent = [(today - timedelta(days=10), 80.0), (today - timedelta(days=2), 81.5)]
    t = bodyweight_trend(recent, today)
    assert t["method"] == "simple change" and t["change_kg"] == 1.5 and t["first"]["kg"] == 80.0
    assert bodyweight_trend(recent[:1], today)["method"] == "single weigh-in"
    assert bodyweight_trend([], today) is None
