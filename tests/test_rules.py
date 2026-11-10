"""app/rules.py: the checks every logged entry passes, and the helpers they rest on."""

import math
import time
from datetime import date, datetime, timedelta

from app import rules
from app.exercises import LIFTS

NAMES = [name for name, _ in LIFTS]
TODAY = date(2026, 11, 6)


def one(**kw):
    return [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5, **kw}]


def test_names_match_ignoring_case_and_spaces():
    assert rules.match_exercise("  back   SQUAT ", NAMES) == ("Back Squat", [])
    _, sets, err = rules.check_session(None, one(exercise="push-up", weight_kg=0), NAMES, TODAY)
    assert err is None and sets == [{"exercise": "Push-Up", "weight_kg": 0.0, "reps": 5}]


def test_unknown_exercise_gives_the_closest_names():
    _, _, err = rules.check_session(None, one(exercise="Gobblet Squat"), NAMES, TODAY)
    assert err["error"] == "unknown_exercise"
    assert "Goblet Squat" in err["closest"] and "Goblet Squat" in err["fix"]
    assert "list_exercises" in err["fix"]


def test_nan_inf_and_bools_are_not_numbers():
    for bad in ("nan", "inf", "-inf", float("nan"), math.inf, True):
        _, _, err = rules.check_session(None, one(weight_kg=bad), NAMES, TODAY)
        assert err["error"] == "not_a_number", bad
        _, _, err = rules.check_session(None, one(reps=bad), NAMES, TODAY)
        assert err["error"] == "not_a_number", bad


def test_every_problem_reported_at_once():
    rows = [{"exercise": "Bak Squat", "weight_kg": 60, "reps": 5},
            {"exercise": "Bench Press", "weight_kg": 501, "reps": 5},
            {"exercise": "Bench Press", "weight_kg": 40, "reps": 31},
            {"exercise": "Bench Press", "weight_kg": 40, "reps": 5.5},
            {"exercise": "", "weight_kg": "x", "reps": 5}]
    _, sets, err = rules.check_session("05/11/2026", rows, NAMES, TODAY)
    assert sets == []
    kinds = [e["error"] for e in err["errors"]]
    assert kinds == ["invalid_date", "unknown_exercise", "weight_out_of_range", "reps_out_of_range",
                     "not_a_whole_number", "missing_field", "not_a_number"]
    assert err["error"] == "invalid_date" and err["message"].startswith("7 problems.")
    assert "Set 2:" in err["errors"][2]["message"] and "set 5" in err["errors"][6]["fix"]
    assert "Back Squat" in err["fix"]


def test_session_date_blank_is_today_and_never_after_today():
    assert rules.check_session("", one(), NAMES, TODAY)[0] == TODAY
    assert rules.check_session(TODAY.isoformat(), one(), NAMES, TODAY)[0] == TODAY
    _, _, err = rules.check_session((TODAY + timedelta(days=1)).isoformat(), one(), NAMES, TODAY)
    assert err["error"] == "date_in_future" and TODAY.isoformat() in err["message"]
    assert "time zone" in err["fix"]


def test_set_count_one_to_sixty():
    assert rules.check_session(None, [], NAMES, TODAY)[2]["error"] == "sets_count_out_of_range"
    assert rules.check_session(None, one() * 61, NAMES, TODAY)[2]["error"] == "sets_count_out_of_range"
    assert rules.check_session(None, "not a list", NAMES, TODAY)[2]["error"] == "sets_count_out_of_range"
    assert rules.check_session(None, one() * 60, NAMES, TODAY)[2] is None


def test_weight_and_reps_limits():
    assert rules.check_session(None, one(weight_kg=500, reps=30), NAMES, TODAY)[2] is None
    assert rules.check_session(None, one(weight_kg=-1), NAMES, TODAY)[2]["error"] == "weight_out_of_range"
    assert rules.check_session(None, one(reps=0), NAMES, TODAY)[2]["error"] == "reps_out_of_range"
    assert rules.check_session(None, one(reps=None), NAMES, TODAY)[2]["error"] == "missing_field"


def test_numbers_too_big_for_a_float_and_negative_zero():
    for big in (10 ** 400, "1" + "0" * 400):
        assert rules.check_session(None, one(weight_kg=big), NAMES, TODAY)[2]["error"] == "not_a_number"
        assert rules.check_session(None, one(reps=big), NAMES, TODAY)[2]["error"] == "not_a_number"
    _, sets, _ = rules.check_session(None, one(weight_kg=-0.0), NAMES, TODAY)
    assert str(sets[0]["weight_kg"]) == "0.0"
    assert rules.to_float(-0.0) == 0.0 and str(rules.to_float("-0")) == "0.0"
    assert rules.to_float(True) is None and rules.to_float([1]) is None


def test_a_datetime_counts_as_its_date():
    when = datetime(2026, 11, 5, 23, 59)
    assert rules.check_session(when, one(), NAMES, TODAY)[0] == date(2026, 11, 5)
    assert rules.to_date(when) == date(2026, 11, 5) and rules.to_date("soon") is None


def test_entries_go_back_two_years_at_most():
    assert rules.entry_date(TODAY - timedelta(days=rules.OLDEST_DAYS), TODAY)[0] is not None
    _, err = rules.entry_date(TODAY - timedelta(days=rules.OLDEST_DAYS + 1), TODAY, "sessions")
    assert err["error"] == "date_too_old" and "sessions" in err["fix"]
    assert rules.check_session("0001-01-01", one(), NAMES, TODAY)[2]["error"] == "date_too_old"


def test_free_text_keeps_newlines_and_refuses_other_control_characters():
    assert rules.text("  line one\n\tline two\r\n ", "notes") == ("line one\n\tline two", None)
    assert rules.text(None, "notes") == ("", None)
    for bad in ("a\x00b", "bell\x07", "esc\x1b[0m", "half\ud800"):
        value, err = rules.text(bad, "notes")
        assert value is None and err["error"] == "invalid_field" and "control characters" in err["fix"], repr(bad)
    _, err = rules.text("x" * (rules.TEXT_MAX + 1), "notes")
    assert err["error"] == "invalid_field" and "Shorten notes" in err["fix"]
    assert rules.text("x" * rules.TEXT_MAX, "notes")[1] is None
    assert rules.text(["a"], "notes")[1]["error"] == "invalid_field"


def test_messages_quote_at_most_80_characters_of_a_value():
    assert rules.quote("short") == "short"
    long = rules.quote("y" * 100_000)
    assert len(long) == rules.QUOTE_MAX and long.endswith("…")
    assert rules.quote(10 ** 5000) == "a number too long to show"
    _, _, err = rules.check_session("d" * 100_000, one(weight_kg="w" * 100_000, exercise="e" * 100_000), NAMES, TODAY)
    assert len(str(err)) < 2_000, len(str(err))


def test_a_name_far_longer_than_any_exercise_skips_the_closest_name_search():
    rows = [{"exercise": "Back Squat" + "x" * 100_000, "weight_kg": 60, "reps": 5}] * 60
    started = time.perf_counter()
    _, _, err = rules.check_session(None, rows, NAMES, TODAY)
    assert time.perf_counter() - started < 0.5
    assert err["errors"][0]["error"] == "unknown_exercise" and err["errors"][0]["closest"] == []
    assert rules.match_exercise({"not": "text"}, NAMES) == (None, [])


def test_combine_keeps_the_first_code_and_joins_the_rest():
    a = rules.error("a", "First.", "Fix one.")
    b = rules.error("b", "Second.", "Fix one.")
    assert rules.combine([]) is None
    assert rules.combine([a]) == {**a, "errors": [a]}
    both = rules.combine([a, b])
    assert both["error"] == "a" and both["message"] == "2 problems. First. Second." and both["fix"] == "Fix one."



# ---------------------------------------------------------------- measurement kinds and values

def test_kinds_are_matched_like_exercise_names():
    assert rules.match_kind("Waist") == ("waist", None)
    assert rules.match_kind("  resting   HR ") == ("resting_hr", None)
    assert rules.match_kind("sleep_hours") == ("sleep_hours", None)
    kind, err = rules.match_kind("wrist")
    assert kind is None and err["error"] == "invalid_kind" and err["closest"] == ["waist"]
    assert all(k in err["fix"] for k in rules.MEASUREMENTS)
    for odd in (None, 5, [], "", "x" * 100_000):
        assert rules.match_kind(odd)[1]["error"] == "invalid_kind"


def test_measurement_values_by_kind():
    assert rules.measurement_value("waist", "84.567") == (84.57, None)
    assert rules.measurement_value("steps", 8000.0) == (8000, None)
    assert rules.measurement_value("steps", 8000.5)[1]["error"] == "invalid_field"
    assert rules.measurement_value("resting_hr", 19)[1]["error"] == "invalid_field"
    assert rules.measurement_value("water_ml", 0) == (0, None)
    assert rules.measurement_value("sleep_hours", -0.0) == (0.0, None)
    assert rules.measurement_value("waist", None)[1]["error"] == "missing_field"
    for bad in (True, "x", float("nan"), 10 ** 400):
        assert rules.measurement_value("waist", bad)[1]["error"] == "invalid_field"
    assert "Give steps, 0 to 200,000, a whole number." == rules.measurement_value("steps", -1)[1]["fix"]
