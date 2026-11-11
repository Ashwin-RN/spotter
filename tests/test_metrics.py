"""app/metrics.py: every formula Spotter quotes. Pure functions, so no database is needed."""

from datetime import date, timedelta

from app import metrics

import pytest

from app.metrics import (
    bodyweight_average, bodyweight_now, bodyweight_trend, days_since_last, epley, ewma, in_order, maintenance,
    maintenance_gap, order_key, prs, projection, projection_gap, recent_best,
    session_best, sessions_vs_target, trend_rate, trend_rate_gap, weekly_sessions,
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


def test_recent_best_for_a_time_is_the_lowest():
    # A cardio time for a distance: lower is better, with the same 28-day window and the same fallback.
    assert recent_best([(ago(21), 30.0), (ago(3), 31.5), (TODAY, 32.0)], TODAY, lower_is_better=True) == 30.0
    assert recent_best([(ago(28), 29.0), (TODAY, 32.0)], TODAY, lower_is_better=True) == 32.0
    assert recent_best([(ago(40), 29.0), (ago(35), 33.0)], TODAY, lower_is_better=True) == 33.0


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
    assert p["weeks_left"] is None and p["needed_per_week"] is None and p["projected_date"] is None
    assert set(p) == {"slope_per_week", "projected", "projected_date", "status", "points_used", "latest",
                      "weeks_left", "needed_per_week"}


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


# ---------------------------------------------------------------- the one order

def test_in_order_sorts_by_effective_date_and_keeps_the_callers_order_for_ties():
    later = TODAY + timedelta(days=1)
    pts = [(ago(3), 82.0), (later, 80.0), (TODAY, 81.0)]   # the caller's order: 80 logged before 81
    assert in_order(pts, TODAY) == [(ago(3), 82.0), (later, 80.0), (TODAY, 81.0)]
    assert in_order([(later, 1.0), (ago(1), 2.0)], TODAY) == [(ago(1), 2.0), (later, 1.0)]
    assert order_key(later, 7, TODAY) == (TODAY, 7)


def test_latest_means_last_in_the_one_order():
    later = TODAY + timedelta(days=1)
    pts = [(ago(40), 90.0), (later, 80.0), (TODAY, 81.0)]
    t = bodyweight_trend(pts, TODAY)
    assert t["latest"] == {"date": TODAY.isoformat(), "kg": 81.0} and t["change_kg"] == -9.0
    assert bodyweight_now([(ago(20), 70.0), (ago(10), 71.0)], TODAY) == 71.0
    assert recent_best([(ago(60), 150.0), (ago(40), 120.0)], TODAY) == 120.0
    line = projection([(ago(21), 100), (ago(14), 101), (later, 103), (TODAY, 102)], TODAY)
    assert line["latest"] == 102


def test_projection_gap_says_why_there_is_no_line():
    assert projection_gap([(TODAY, 1)] * 3, TODAY) == "A projection needs 4 points in the last 6 weeks."
    same_day = [(TODAY, 1), (TODAY, 2), (TODAY + timedelta(days=1), 3), (TODAY, 4)]
    assert projection(same_day, TODAY) is None
    assert projection_gap(same_day, TODAY) == "A projection needs points on at least 2 different days."
    assert projection_gap(LIFT, TODAY) is None



# ---------------------------------------------------------------- cardio

def test_cardio_kcal_is_met_times_3_5_times_kg_over_200_times_minutes():
    from app.metrics import cardio_kcal
    assert cardio_kcal(3.8, 80, 30) == 160 and cardio_kcal(9.3, 70, 28) == 319 and cardio_kcal(1, 0, 10) == 0


# ---------------------------------------------------------------- projected date (decision 8)

STEADY = [(ago(3), 10), (ago(2), 11), (ago(1), 12), (TODAY, 13)]   # exactly +1 a day: 13 today


def test_the_projected_date_is_the_day_the_line_reaches_the_target():
    # LIFT is +0.5 kg a week and sits at 81.5 today: 100 is 18.5 kg away, 18.5 / (0.5 / 7) = 259 days.
    p = projection(LIFT, TODAY, goal_target=100, goal_date=TODAY + timedelta(days=28))
    assert p["projected_date"] == (TODAY + timedelta(days=259)).isoformat()
    # CUT is -0.2 kg a week at 89.4 today: 85 is 4.4 kg below, 4.4 / (0.2 / 7) = 154 days.
    down = projection(CUT, TODAY, goal_target=85, goal_date=TODAY + timedelta(days=28), lower_is_better=True)
    assert down["projected_date"] == (TODAY + timedelta(days=154)).isoformat()
    # +1 a day from 13: 13.5 is crossed during the first day, so tomorrow; 20 in exactly 7 days.
    assert projection(STEADY, TODAY, 13.5, TODAY + timedelta(days=9))["projected_date"] == \
        (TODAY + timedelta(days=1)).isoformat()
    assert projection(STEADY, TODAY, 20, TODAY + timedelta(days=9))["projected_date"] == \
        (TODAY + timedelta(days=7)).isoformat()


def test_the_projected_date_is_null_when_the_line_points_away():
    assert projection(CUT, TODAY, goal_target=95, goal_date=TODAY + timedelta(days=28))["projected_date"] is None
    assert projection(LIFT, TODAY, goal_target=70, goal_date=TODAY + timedelta(days=28),
                      lower_is_better=True)["projected_date"] is None


def test_the_projected_date_is_null_when_the_line_is_flat():
    flat = [(ago(21), 100), (ago(14), 100), (ago(7), 100), (TODAY, 100)]
    assert projection(flat, TODAY, 110, TODAY + timedelta(days=28))["projected_date"] is None
    # A slope that shows as 0.00 a week is flat too: 0.004 kg over three weeks.
    nearly = [(ago(21), 100), (ago(14), 100), (ago(7), 100), (TODAY, 100.004)]
    assert projection(nearly, TODAY, 100.005, TODAY + timedelta(days=28))["projected_date"] is None


def test_the_projected_date_is_null_more_than_two_years_out():
    # +1 a day from 13: 743 is exactly 730 days away (kept), 744 is 731 (dropped).
    assert projection(STEADY, TODAY, 743, TODAY + timedelta(days=9))["projected_date"] == \
        (TODAY + timedelta(days=730)).isoformat()
    assert projection(STEADY, TODAY, 744, TODAY + timedelta(days=9))["projected_date"] is None
    assert projection(LIFT, TODAY, goal_target=200, goal_date=TODAY + timedelta(days=28))["projected_date"] is None


def test_a_line_already_past_the_target_projects_today():
    p = projection(LIFT, TODAY, goal_target=81, goal_date=TODAY + timedelta(days=28))
    assert p["status"] == "reached" and p["projected_date"] == TODAY.isoformat()


# ---------------------------------------------------------------- smoothed weight (decision 5)

def test_ewma_by_hand():
    # s0 = 80; s1 = 80 + 0.1 x (81 - 80) = 80.1; s2 = 80.1 + 0.1 x (79 - 80.1) = 79.99.
    assert ewma([(ago(2), 80), (ago(1), 81), (TODAY, 79)], TODAY) == [(ago(2), 80.0), (ago(1), 80.1), (TODAY, 79.99)]
    assert ewma([], TODAY) == []
    assert ewma([(TODAY, 80.0)], TODAY) == [(TODAY, 80.0)]


def test_ewma_carries_the_unrounded_value_and_rounds_what_it_returns():
    # 80.003 shows as 80.0 but the next step starts from 80.003: 80.003 + 0.1 x 0.027 = 80.0057, shown 80.01.
    # Rounding each step first would give 80.0 again.
    assert [s for _, s in ewma([(ago(2), 80), (ago(1), 80.03), (TODAY, 80.03)], TODAY)] == [80.0, 80.0, 80.01]


def test_ewma_follows_the_one_order_and_another_alpha():
    later = TODAY + timedelta(days=1)
    # Given out of order: sorted by effective date; the later-dated one counts as today, after the earlier entry.
    out = ewma([(TODAY, 82), (ago(5), 80), (later, 84)], TODAY)
    assert out == [(ago(5), 80.0), (TODAY, 80.2), (later, 80.58)]   # 80 + 0.2; 80.2 + 0.1 x 3.8
    assert [s for _, s in ewma([(ago(1), 80), (TODAY, 90)], TODAY, alpha=0.5)] == [80.0, 85.0]


# ---------------------------------------------------------------- the rate's two ends (decision 6)

def test_trend_rate_by_hand():
    smoothed = [(ago(43), 83.0), (ago(42), 82.0), (ago(14), 81.0), (ago(13), 80.9), (ago(3), 80.4), (TODAY, 80.3)]
    # to = today (80.3); from = the last one dated 14 to 42 days ago: ago(14), 81.0. (80.3 - 81.0) / 14 x 7.
    assert trend_rate(smoothed, TODAY) == {"per_week": -0.35, "from": ago(14).isoformat(), "to": TODAY.isoformat(),
                                           "days": 14}
    assert trend_rate_gap(smoothed, TODAY) is None


def test_the_rate_ends_at_their_boundaries():
    # The latest may be 6 days old, not 7.
    assert trend_rate([(ago(20), 81.0), (ago(6), 80.0)], TODAY)["days"] == 14
    assert trend_rate([(ago(20), 81.0), (ago(7), 80.0)], TODAY) is None
    # The reference may be 14 or 42 days old, not 13 or 43.
    assert trend_rate([(ago(42), 82.0), (TODAY, 80.0)], TODAY) == {"per_week": -0.33, "from": ago(42).isoformat(),
                                                                   "to": TODAY.isoformat(), "days": 42}
    assert trend_rate([(ago(43), 82.0), (TODAY, 80.0)], TODAY) is None
    assert trend_rate([(ago(13), 82.0), (TODAY, 80.0)], TODAY) is None
    # The latest is the last in the one order: a later-dated weigh-in counts as today.
    later = TODAY + timedelta(days=2)
    assert trend_rate([(ago(14), 81.0), (later, 80.3)], TODAY) == {"per_week": -0.35, "from": ago(14).isoformat(),
                                                                   "to": later.isoformat(), "days": 14}


def test_the_rate_says_which_end_is_missing():
    assert trend_rate_gap([(ago(20), 81.0), (ago(8), 80.0)], TODAY) == \
        "The weekly rate needs a weigh-in in the last 7 days."
    assert trend_rate_gap([(ago(3), 80.0), (TODAY, 80.0)], TODAY) == \
        "The weekly rate needs a weigh-in from 14 to 42 days ago."
    assert trend_rate_gap([(ago(50), 80.0)], TODAY) == \
        "The weekly rate needs a weigh-in in the last 7 days and one from 14 to 42 days ago."
    assert trend_rate([], TODAY) is None and trend_rate_gap([], TODAY).startswith("The weekly rate needs")


# ---------------------------------------------------------------- maintenance (decision 7)

# Now: the 7 days ending today average 80.0 (79.8, 80.2). Then: the 7 ending 21 days ago (ago 27 .. 21)
# average 80.6 (80.4, 80.8). The 99s sit outside both windows and must not count.
WEIGHED = [(ago(28), 99.0), (ago(27), 80.8), (ago(21), 80.4), (ago(7), 99.0), (ago(3), 80.2), (TODAY, 79.8)]
ATE = [(ago(n), 2100) for n in range(7)] + [(ago(n), 1900) for n in range(7, 14)]   # 14 days, average 2000


def test_maintenance_by_hand():
    # Weight change 80.0 - 80.6 = -0.6 kg. 2000 - (-0.6 x 7,700 / 21) = 2000 + 220 = 2220.
    m = maintenance(ATE, WEIGHED, TODAY)
    assert m == {"kcal": 2220, "intake_avg_kcal": 2000, "weight_change_kg": -0.6, "days_logged": 14,
                 "window_days": 21, "from": ago(20).isoformat(), "to": TODAY.isoformat()}
    assert maintenance_gap(ATE, WEIGHED, TODAY) is None
    # Gaining 0.3 kg on 2000 a day: 2000 - 0.3 x 7,700 / 21 = 2000 - 110 = 1890.
    gaining = [(ago(27), 79.7), (TODAY, 80.0)]
    assert maintenance(ATE, gaining, TODAY)["kcal"] == 1890


def test_maintenance_needs_14_logged_days_in_the_21():
    thirteen = ATE[:13]
    assert maintenance(thirteen, WEIGHED, TODAY) is None
    assert maintenance_gap(thirteen, WEIGHED, TODAY) == "Needs food logged on 1 more of the last 21 days."
    # A day 21 days back is outside the window; two entries on one day are one logged day, added up.
    outside = ATE[:13] + [(ago(21), 2000)]
    assert maintenance(outside, WEIGHED, TODAY) is None
    doubled = ATE[:13] + [(ago(5), 1000), (ago(5), 900)]
    assert maintenance(doubled, WEIGHED, TODAY) is None   # still 13 days
    twice = ATE + [(TODAY, 140)]                           # today: 2100 + 140
    assert maintenance(twice, WEIGHED, TODAY)["intake_avg_kcal"] == 2010
    assert maintenance_gap([], WEIGHED, TODAY) == "Needs food logged on 14 more of the last 21 days."


def test_maintenance_needs_both_weight_averages():
    no_then = [w for w in WEIGHED if w[0] > ago(21)]
    no_now = [w for w in WEIGHED if w[0] < ago(6)]
    for weighed in (no_then, no_now, []):
        assert maintenance(ATE, weighed, TODAY) is None
        assert maintenance_gap(ATE, weighed, TODAY) == "Needs weigh-ins in the last 7 days and around 21 days ago."


def test_pace_is_minutes_per_km_as_m_ss():
    from app.metrics import pace, speed
    assert pace(5, 28) == "5:36" and pace(3, 30) == "10:00" and pace(10, 45.5) == "4:33"
    assert pace(1, 59.999) == "60:00" and pace(None, 30) is None and pace(0, 30) is None
    assert speed(5, 30) == 10.0 and speed(5, 28) == 10.71 and speed(None, 30) is None


def test_pace_goes_through_the_formatter_with_halves_away_from_zero():
    """metrics.pace calls formatting.clock: 12.5 minutes over 60 km is 12.5 seconds a km, so 0:13 (round() gave 0:12)."""
    assert metrics.pace(60, 12.5) == "0:13"
    assert metrics.pace(5, 28) == "5:36" and metrics.pace(4, 300) == "75:00" and metrics.pace(None, 30) is None


# ---------------------------------------------------------------- the plan line (stage 6, E1)

def test_goal_now_reads_each_kinds_own_measure():
    lifts = [(ago(40), 120.0), (ago(20), 105.0), (ago(3), 110.0), (ago(1), 100.0)]
    assert metrics.goal_now("lift", lifts, TODAY) == 110.0                           # the best of 28 days
    assert metrics.goal_now("lift", lifts[:1], TODAY) == 120.0                      # none recent: the latest
    times = [(ago(40), 25.0), (ago(10), 31.0), (ago(2), 29.5), (ago(1), 30.5)]
    assert metrics.goal_now("cardio_best", times, TODAY) == 29.5                     # the lowest of 28 days
    weighed = [(ago(10), 70.0), (ago(3), 80.0), (ago(1), 81.0)]
    assert metrics.goal_now("bodyweight", weighed, TODAY) == 80.5                    # the 7-day average
    assert metrics.goal_now("bodyweight", weighed[:1], TODAY) == 70.0               # none in 7 days: the latest
    waist = [(ago(1), 86.0), (ago(3), 88.0), (TODAY + timedelta(days=2), 84.0)]
    assert metrics.goal_now("measurement", waist, TODAY) == 84.0                    # last in the one order
    assert all(metrics.goal_now(kind, [], TODAY) is None for kind in ("lift", "bodyweight", "cardio_best",
                                                                      "measurement"))


def test_goal_now_series_is_goal_now_as_it_stood_on_each_date_with_a_point():
    weighed = [(ago(12), 82.0), (ago(9), 81.0), (ago(5), 80.0), (ago(5), 79.0), (TODAY + timedelta(days=1), 78.0)]
    series = metrics.goal_now_series("bodyweight", weighed, TODAY, since=ago(9))
    # ago(12) is before since: it counts in the averages but has no point. Two weigh-ins on one date: one point.
    # The weigh-in dated after today stands at today.
    assert series == [(ago(9), 81.5), (ago(5), 80.0), (TODAY, 79.0)]
    assert series[-1][1] == metrics.goal_now("bodyweight", weighed, TODAY)
    lifts = [(ago(40), 120.0), (ago(20), 105.0)]
    assert metrics.goal_now_series("lift", lifts, TODAY, since=ago(40)) == [(ago(40), 120.0), (ago(20), 120.0)]
    assert metrics.goal_now_series("lift", lifts, TODAY, since=TODAY) == []
    assert metrics.goal_now_series("lift", [], TODAY, since=ago(40)) == []


# A down goal from 80 kg ten days ago to 78 kg in ten days: 0.1 kg a day, 79 kg on the line today.
DOWN = dict(start_date=ago(10), start_value=80.0, target_date=TODAY + timedelta(days=10), target_value=78.0,
            today=TODAY, lower_is_better=True, quantity="bodyweight_derived")
# An up goal from 100 kg ten days ago to 120 kg in ten days: 1 kg a day, 110 kg on the line today.
UP = dict(start_date=ago(10), start_value=100.0, target_date=TODAY + timedelta(days=10), target_value=120.0,
          today=TODAY, lower_is_better=False, quantity="estimated_max")


def standing(path: dict) -> tuple:
    return path["should_be_today"], path["ahead_by"], path["days_ahead"], path["vs_plan"]


def test_goal_path_nothing_logged_has_the_line_and_nothing_to_compare():
    path = metrics.goal_path(**DOWN, actual=[])
    assert standing(path) == (79.0, None, None, None)
    assert path["plan"] == [{"date": ago(10).isoformat(), "value": 80.0}, {"date": TODAY.isoformat(), "value": 79.0},
                            {"date": (TODAY + timedelta(days=10)).isoformat(), "value": 78.0}]
    assert path["actual"] == []


def test_goal_path_one_point_is_worked_out_and_now_defaults_to_the_last_point():
    path = metrics.goal_path(**DOWN, actual=[(TODAY, 79.5)])
    assert standing(path) == (79.0, -0.5, -5, "behind")       # down: 79 - 79.5
    assert path["actual"] == [{"date": TODAY.isoformat(), "value": 79.5}]
    assert standing(metrics.goal_path(**UP, actual=[(TODAY, 112.0)])) == (110.0, 2.0, 2, "ahead")   # up: 112 - 110


def test_goal_path_now_is_the_goals_now_not_the_last_point():
    # A 28-day best can move without a new point: now is given, and the series is left as it stood.
    path = metrics.goal_path(**UP, actual=[(ago(9), 115.0)], now=108.0)
    assert standing(path) == (110.0, -2.0, -2, "behind")
    assert path["actual"] == [{"date": ago(9).isoformat(), "value": 115.0}]


def test_goal_path_on_the_first_day_and_before_the_start():
    first = {**DOWN, "start_date": TODAY, "target_date": TODAY + timedelta(days=20)}
    path = metrics.goal_path(**first, actual=[(TODAY, 79.8)])
    assert standing(path) == (80.0, pytest.approx(0.2), 2, "ahead")           # the start itself; 0.1 kg a day
    assert [p["date"] for p in path["plan"]] == [TODAY.isoformat(), (TODAY + timedelta(days=20)).isoformat()]
    early = {**DOWN, "start_date": TODAY + timedelta(days=2), "target_date": TODAY + timedelta(days=22)}
    assert standing(metrics.goal_path(**early, actual=[], now=80.5)) == (80.0, -0.5, -5, "behind")


def test_goal_path_after_the_target_date_and_with_no_length():
    passed = {**DOWN, "start_date": ago(30), "target_date": ago(5)}
    assert standing(metrics.goal_path(**passed, actual=[], now=78.4)) == (78.0, pytest.approx(-0.4), None, "behind")
    assert len(metrics.goal_path(**passed, actual=[], now=78.4)["plan"]) == 2   # today is not between the ends
    flat = {**DOWN, "start_date": ago(1), "target_date": ago(1)}
    assert standing(metrics.goal_path(**flat, actual=[], now=77.5)) == (78.0, 0.5, None, "ahead")
    backwards = {**DOWN, "start_date": TODAY, "target_date": ago(1)}
    assert standing(metrics.goal_path(**backwards, actual=[], now=78.0)) == (78.0, 0.0, None, "on the line")


def test_goal_path_a_goal_already_met_is_ahead():
    assert standing(metrics.goal_path(**DOWN, actual=[], now=77.5)) == (79.0, 1.5, 15, "ahead")
    assert standing(metrics.goal_path(**UP, actual=[], now=125.0)) == (110.0, 15.0, 15, "ahead")


def test_goal_path_on_the_line_is_the_formatters_rule_and_zeroes_both():
    for now in (79.04, 78.96, 79.0):
        assert standing(metrics.goal_path(**DOWN, actual=[], now=now)) == (79.0, 0.0, 0, "on the line"), now
    assert standing(metrics.goal_path(**UP, actual=[], now=110.04)) == (110.0, 0.0, 0, "on the line")
    assert metrics.goal_path(**DOWN, actual=[], now=79.06)["vs_plan"] == "behind"   # 0.06 rounds to 0.1
    # A cardio time on its own step: under half a second is on the line, half a second is not.
    run = dict(start_date=ago(10), start_value=31.0, target_date=TODAY + timedelta(days=10), target_value=29.0,
               today=TODAY, lower_is_better=True, quantity="goal_time")
    assert metrics.goal_path(**run, actual=[], now=30.0 + 0.4 / 60)["vs_plan"] == "on the line"
    assert metrics.goal_path(**run, actual=[], now=30.0 - 0.6 / 60)["vs_plan"] == "ahead"


def test_goal_path_days_round_halves_away_from_zero():
    # 80 kg to 70 kg over 20 days: 0.5 kg a day, 75 kg today. 1.25 kg is 2.5 days either way.
    steep = {**DOWN, "target_value": 70.0}
    assert metrics.goal_path(**steep, actual=[], now=76.25)["days_ahead"] == -3
    assert metrics.goal_path(**steep, actual=[], now=73.75)["days_ahead"] == 3
    assert isinstance(metrics.goal_path(**steep, actual=[], now=73.75)["days_ahead"], int)
    assert metrics.goal_path(**steep, actual=[], now=74.9)["days_ahead"] == 0     # 0.1 kg: under a day ahead


def test_goal_path_a_point_after_today_counts_as_today():
    weighed = [(ago(3), 79.4), (TODAY + timedelta(days=1), 78.8)]
    actual = metrics.goal_now_series("bodyweight", weighed, TODAY, DOWN["start_date"])
    path = metrics.goal_path(**DOWN, actual=actual, now=metrics.goal_now("bodyweight", weighed, TODAY))
    assert path["actual"][-1] == {"date": TODAY.isoformat(), "value": 79.1}
    assert standing(path) == (79.0, pytest.approx(-0.1), -1, "behind")


def test_goal_path_says_when_the_line_has_ended():
    """ended: the plan figure is the target. After the target date, or a line with no length; never while it runs."""
    assert metrics.goal_path(**DOWN, actual=[], now=79.0)["ended"] is False
    assert metrics.goal_path(**{**DOWN, "start_date": TODAY, "target_date": TODAY + timedelta(days=1)},
                             actual=[], now=79.0)["ended"] is False
    assert metrics.goal_path(**{**DOWN, "target_date": TODAY}, actual=[], now=79.0)["ended"] is False   # its last day
    assert metrics.goal_path(**{**DOWN, "start_date": ago(30), "target_date": ago(5)}, actual=[], now=79.0)["ended"]
    assert metrics.goal_path(**{**DOWN, "start_date": ago(1), "target_date": ago(1)}, actual=[], now=79.0)["ended"]


@pytest.mark.parametrize("start_date, target_date", [(TODAY + timedelta(days=1), TODAY + timedelta(days=1)),
                                                     (TODAY + timedelta(days=1), TODAY)],
                         ids=["start tomorrow, target tomorrow", "the target before the start"])
def test_goal_path_no_length_before_the_target_date(start_date, target_date):
    """The target date has not passed, yet the line has no length: the target, no days figure, ended, up and down."""
    down = {**DOWN, "start_date": start_date, "target_date": target_date}
    path = metrics.goal_path(**down, actual=[], now=80.8)
    assert standing(path) == (78.0, pytest.approx(-2.8), None, "behind") and path["ended"] is True
    assert standing(metrics.goal_path(**down, actual=[], now=78.02)) == (78.0, 0.0, None, "on the line")
    up = {**UP, "start_date": start_date, "target_date": target_date}
    path = metrics.goal_path(**up, actual=[], now=118.0)
    assert standing(path) == (120.0, -2.0, None, "behind") and path["ended"] is True
    assert standing(metrics.goal_path(**up, actual=[], now=120.03)) == (120.0, 0.0, None, "on the line")


def test_goal_path_an_up_goal_after_the_target_with_no_length_and_before_the_start():
    passed = {**UP, "start_date": ago(30), "target_date": ago(5)}
    path = metrics.goal_path(**passed, actual=[], now=118.5)
    assert standing(path) == (120.0, -1.5, None, "behind") and path["ended"] and len(path["plan"]) == 2
    assert standing(metrics.goal_path(**passed, actual=[], now=121.0)) == (120.0, 1.0, None, "ahead")
    flat = {**UP, "start_date": ago(1), "target_date": ago(1)}
    assert standing(metrics.goal_path(**flat, actual=[], now=125.0)) == (120.0, 5.0, None, "ahead")
    backwards = {**UP, "start_date": TODAY, "target_date": ago(1)}
    assert standing(metrics.goal_path(**backwards, actual=[], now=120.0)) == (120.0, 0.0, None, "on the line")
    # Before the start (a time zone change): the start itself, no days elapsed; 1 kg a day over 20 days.
    early = {**UP, "start_date": TODAY + timedelta(days=2), "target_date": TODAY + timedelta(days=22)}
    path = metrics.goal_path(**early, actual=[], now=103.0)
    assert standing(path) == (100.0, 3.0, 3, "ahead") and path["ended"] is False
    assert [p["date"] for p in path["plan"]] == [(TODAY + timedelta(days=2)).isoformat(),
                                                 (TODAY + timedelta(days=22)).isoformat()]


def test_goal_path_an_up_goal_reads_a_point_after_today_as_today():
    lifts = [(ago(3), 108.0), (TODAY + timedelta(days=1), 112.5)]
    actual = metrics.goal_now_series("lift", lifts, TODAY, UP["start_date"])
    assert actual == [(ago(3), 108.0), (TODAY, 112.5)]
    path = metrics.goal_path(**UP, actual=actual, now=metrics.goal_now("lift", lifts, TODAY))
    assert path["actual"][-1] == {"date": TODAY.isoformat(), "value": 112.5}
    assert standing(path) == (110.0, 2.5, 3, "ahead")                    # 2.5 days at 1 kg a day: halves away, 3
    assert standing(metrics.goal_path(**UP, actual=actual)) == (110.0, 2.5, 3, "ahead")   # now left out: the last point
