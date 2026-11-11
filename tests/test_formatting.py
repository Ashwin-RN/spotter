"""T4: app.formatting, the one formatter. Every quantity's rule, the halves, the zero sign, commas,
m:ss and h:mm:ss, widened decimals, is_zero and widen, field, and every date function.

Every date test passes "today" in, so the suite holds on any day.
"""

import decimal
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import formatting as f
from app import rules

NBSP = "\u00a0"
TODAY = date(2026, 11, 10)  # a Tuesday


# ---------------------------------------------------------------- the table

# Every quantity section L of the stage 6 contract passes to show.
NAMED_IN_L = {
    "kcal", "kcal_left", "weigh_in", "session_time", "bodyweight_derived", "rate", "count", "percent",
    "count_avg", "week_minutes", "servings", "weight_kg", "reps", "km", "cm", "bpm", "hours", "steps",
    "ml", "maintenance", "weight_change", "estimated_max", "goal_target", "time_gap", "time_pace",
    "goal_time", "pace", "kcal_per_serving", "grams", "net_kcal",
}
# The rest of section C's quantity table.
IN_C_ONLY = {"met", "speed", "incline"}


def test_every_quantity_has_a_step():
    assert set(f.STEP) == NAMED_IN_L | IN_C_ONLY


@pytest.mark.parametrize("quantity, step", [
    ("weigh_in", 0.01), ("weight_kg", 0.01), ("bodyweight_derived", 0.1), ("estimated_max", 0.1),
    ("reps", 1), ("kcal", 1), ("kcal_per_serving", 1), ("grams", 0.01), ("net_kcal", 1),
    ("kcal_left", 1), ("maintenance", 10), ("percent", 1), ("met", 0.1), ("week_minutes", 1),
    ("km", 0.01), ("speed", 0.1), ("incline", 0.1), ("bpm", 1), ("cm", 0.1), ("hours", 0.01),
    ("steps", 1), ("ml", 1), ("servings", 0.01), ("count", 1), ("count_avg", 0.1), ("rate", 0.01),
    ("weight_change", 0.01), ("goal_target", 0.01),
    ("session_time", 1 / 60), ("pace", 1 / 60), ("goal_time", 1 / 60), ("time_pace", 1 / 60),
    ("time_gap", 1 / 60),
])
def test_step(quantity, step):
    assert f.STEP[quantity] == pytest.approx(step)


# ---------------------------------------------------------------- every quantity's rule

# (quantity, value, shown). Where rounding applies: at a half, just under it and just over it.
RULES = [
    # As entered, up to 2 dp, trailing zeros dropped.
    ("weigh_in", 80.0, "80 kg"), ("weigh_in", 80.1, "80.1 kg"), ("weigh_in", 80.25, "80.25 kg"),
    ("weigh_in", 80.125, "80.13 kg"), ("weigh_in", 80.1249, "80.12 kg"), ("weigh_in", 80.1251, "80.13 kg"),
    ("weight_kg", 60.0, "60 kg"), ("weight_kg", 62.5, "62.5 kg"), ("weight_kg", 1.005, "1.01 kg"),
    ("weight_kg", 1.0049, "1 kg"),
    # 1 dp.
    ("bodyweight_derived", 79.22, "79.2 kg"), ("bodyweight_derived", 79.25, "79.3 kg"),
    ("bodyweight_derived", 79.2499, "79.2 kg"), ("bodyweight_derived", 79.2501, "79.3 kg"),
    ("bodyweight_derived", 80.0, "80 kg"), ("bodyweight_derived", 79.96, "80 kg"),
    ("estimated_max", 107.92, "107.9 kg"), ("estimated_max", 107.95, "108 kg"),
    ("estimated_max", 107.9499, "107.9 kg"), ("estimated_max", 107.9501, "108 kg"),
    # Whole.
    ("reps", 6, "6"), ("reps", 5.5, "6"), ("reps", 5.4999, "5"), ("reps", 5.5001, "6"),
    ("kcal", 2216, "2,216 kcal"), ("kcal", 999, "999 kcal"), ("kcal", 999.5, "1,000 kcal"),
    ("kcal", 999.4999, "999 kcal"), ("kcal", 0, "0 kcal"),
    ("kcal_per_serving", 120.5, "121 kcal"), ("kcal_per_serving", 120.4999, "120 kcal"),
    ("kcal_per_serving", 120.5001, "121 kcal"), ("kcal_per_serving", 1234.56, "1,235 kcal"),
    ("grams", 150.0, "150 g"), ("grams", 1250.5, "1,250.5 g"), ("grams", 12.345, "12.35 g"),
    ("grams", 12.3449, "12.34 g"), ("grams", 12.3451, "12.35 g"),
    ("net_kcal", 1850, "1,850 kcal"), ("net_kcal", -320, "-320 kcal"), ("net_kcal", -1500.5, "-1,501 kcal"),
    ("net_kcal", -0.4999, "0 kcal"), ("net_kcal", 0.5, "1 kcal"),
    ("kcal_left", 300, "300 kcal left"), ("kcal_left", 1250, "1,250 kcal left"),
    ("kcal_left", -250, "250 kcal over"), ("kcal_left", -1250.5, "1,251 kcal over"),
    ("kcal_left", 0, "0 kcal left"), ("kcal_left", -0.4999, "0 kcal left"), ("kcal_left", -0.5, "1 kcal over"),
    # Nearest 10.
    ("maintenance", 2513, "2,510 kcal"), ("maintenance", 2515, "2,520 kcal"),
    ("maintenance", 2514.999, "2,510 kcal"), ("maintenance", 2515.001, "2,520 kcal"), ("maintenance", 995, "1,000 kcal"),
    # Whole, no space.
    ("percent", 41, "41%"), ("percent", 40.5, "41%"), ("percent", 40.4999, "40%"), ("percent", 40.5001, "41%"),
    # 1 dp, no unit.
    ("met", 9.8, "9.8"), ("met", 3.0, "3"), ("met", 3.25, "3.3"), ("met", 3.2499, "3.2"), ("met", 3.2501, "3.3"),
    # m:ss; h:mm:ss from an hour.
    ("session_time", 28.0, "28:00"), ("session_time", 27.5, "27:30"), ("session_time", 0.5, "0:30"),
    ("session_time", 0.125, "0:08"), ("session_time", 0.1249, "0:07"), ("session_time", 59.5, "59:30"), ("session_time", 59.99, "59:59"),
    ("session_time", 59.995, "1:00:00"), ("session_time", 60, "1:00:00"), ("session_time", 75, "1:15:00"),
    ("session_time", 90.5, "1:30:30"), ("session_time", 600, "10:00:00"),
    # Whole minutes.
    ("week_minutes", 84.5, "85 min"), ("week_minutes", 85.2, "85 min"), ("week_minutes", 84.4999, "84 min"),
    ("week_minutes", 84.5001, "85 min"), ("week_minutes", 1500, "1,500 min"),
    # As entered, up to 2 dp.
    ("km", 5.0, "5 km"), ("km", 5.25, "5.25 km"), ("km", 5.125, "5.13 km"), ("km", 5.1249, "5.12 km"),
    # m:ss per km, staying m:ss past 59 minutes.
    ("pace", 5.6, "5:36"), ("pace", 6.0, "6:00"), ("pace", 75, "75:00"), ("pace", 125.5, "125:30"),
    # Up to 1 dp, trailing zeros dropped.
    ("speed", 10.0, "10 km/h"), ("speed", 10.25, "10.3 km/h"), ("speed", 10.2499, "10.2 km/h"),
    ("speed", 10.2501, "10.3 km/h"),
    ("incline", 2.0, "2%"), ("incline", 2.25, "2.3%"), ("incline", 2.2499, "2.2%"), ("incline", 2.2501, "2.3%"),
    # Whole.
    ("bpm", 62, "62 bpm"), ("bpm", 61.5, "62 bpm"), ("bpm", 61.4999, "61 bpm"), ("bpm", 61.5001, "62 bpm"),
    # Up to 1 dp, trailing zeros dropped.
    ("cm", 86.5, "86.5 cm"), ("cm", 86.0, "86 cm"), ("cm", 86.25, "86.3 cm"), ("cm", 86.2499, "86.2 cm"),
    ("cm", 86.2501, "86.3 cm"),
    # Up to 2 dp, h.
    ("hours", 7.5, "7.5 h"), ("hours", 8.0, "8 h"), ("hours", 7.125, "7.13 h"), ("hours", 7.1249, "7.12 h"),
    # Whole, comma from 1,000.
    ("steps", 8500, "8,500 steps"), ("steps", 999, "999 steps"), ("steps", 12345.5, "12,346 steps"),
    ("ml", 2500, "2,500 ml"), ("ml", 750, "750 ml"), ("ml", 999.5, "1,000 ml"),
    # Up to 2 dp.
    ("servings", 1.5, "1.5"), ("servings", 2.0, "2"), ("servings", 0.125, "0.13"), ("servings", 0.1249, "0.12"),
    # Whole; an average up to 1 dp.
    ("count", 3, "3"), ("count", 28, "28"), ("count", 1598, "1,598"), ("count", 2.5, "3"),
    ("count_avg", 3.0, "3"), ("count_avg", 2.75, "2.8"), ("count_avg", 2.7499, "2.7"), ("count_avg", 2.7501, "2.8"),
    # 2 dp, signed, trailing zeros dropped.
    ("rate", -0.24, "-0.24"), ("rate", 0.5, "+0.5"), ("rate", 1.01, "+1.01"), ("rate", -0.125, "-0.13"),
    ("rate", -0.1249, "-0.12"), ("rate", -0.1251, "-0.13"), ("rate", 1.0, "+1"), ("rate", 1234.5, "+1,234.5"),
    ("weight_change", -0.86, "-0.86 kg"), ("weight_change", 0.3, "+0.3 kg"), ("weight_change", 0.125, "+0.13 kg"),
    # Whole seconds a week, signed: the value is minutes a week.
    ("time_pace", -0.31, "-19 seconds a week"), ("time_pace", -0.5, "-30 seconds a week"),
    ("time_pace", -0.2, "-12 seconds a week"), ("time_pace", 0.1, "+6 seconds a week"),
    ("time_pace", 1 / 60, "+1 second a week"), ("time_pace", -1 / 60, "-1 second a week"),
    ("time_pace", -25, "-1,500 seconds a week"),
    # m:ss.
    ("goal_time", 29.5, "29:30"), ("goal_time", 31.0, "31:00"), ("goal_time", 28.0, "28:00"),
    ("goal_time", 75, "75:00"),
    # As entered: up to 2 dp, trailing zeros dropped (kg, cm; bpm is stored whole).
    ("goal_target", 77.0, "77"), ("goal_target", 77.5, "77.5"), ("goal_target", 77.25, "77.25"),
    ("goal_target", 62, "62"), ("goal_target", 77.125, "77.13"),
    # Whole seconds and the word, no sign: the value is minutes.
    ("time_gap", 0.2, "12 seconds"), ("time_gap", 1 / 60, "1 second"), ("time_gap", -0.2, "12 seconds"),
    ("time_gap", 0, "0 seconds"), ("time_gap", 2.0, "120 seconds"), ("time_gap", 20.0, "1,200 seconds"),
]


@pytest.mark.parametrize("quantity, value, shown", RULES)
def test_rule(quantity, value, shown):
    assert f.show(value, quantity) == shown


def test_rules_cover_every_quantity():
    assert {q for q, _, _ in RULES} == set(f.STEP)


@pytest.mark.parametrize("value, quantity, shown", [
    (80.25, "bodyweight_derived", "80.3 kg"),
    (0.15, "bodyweight_derived", "0.2 kg"),
    (79.05, "bodyweight_derived", "79.1 kg"),
    (84.5, "week_minutes", "85 min"),
    (-0.04, "bodyweight_derived", "0 kg"),
    (-0.05, "bodyweight_derived", "-0.1 kg"),
    (-84.5, "net_kcal", "-85 kcal"),
])
def test_halves_away_from_zero(value, quantity, shown):
    # Python's round would give 80.2, 0.1 and 79.0 (the floats sit just under) and 84.
    assert f.show(value, quantity) == shown


# ---------------------------------------------------------------- the zero sign

@pytest.mark.parametrize("quantity, value, shown", [
    ("rate", 0, "0"), ("rate", 0.0, "0"), ("rate", -0.0, "0"), ("rate", -0.004, "0"), ("rate", 0.004, "0"),
    ("rate", -0.0049, "0"), ("rate", -0.005, "-0.01"), ("rate", 0.005, "+0.01"),
    ("weight_change", 0, "0 kg"), ("weight_change", -0.004, "0 kg"), ("weight_change", 0.0049, "0 kg"),
    ("time_pace", 0, "0 seconds a week"), ("time_pace", -0.008, "0 seconds a week"),
    ("time_pace", 0.008, "0 seconds a week"),
    ("net_kcal", -0.4, "0 kcal"), ("bodyweight_derived", -0.04, "0 kg"), ("weigh_in", -0.004, "0 kg"),
    ("cm", -0.04, "0 cm"), ("session_time", -0.001, "0:00"), ("goal_time", -0.001, "0:00"),
])
def test_zero_has_no_sign(quantity, value, shown):
    assert f.show(value, quantity) == shown


@pytest.mark.parametrize("quantity", sorted(f.STEP))
def test_no_minus_zero_or_plus_zero_anywhere(quantity):
    for value in (0, 0.0, -0.0, -1e-9, 1e-9):
        for widened in (False, True):
            text = f.show(value, quantity, widened=widened)
            assert not re.search(r"[+-]0(?![.:]?\d*[1-9])", text), text
            assert not text.startswith(("+", "-")), text


def test_a_total_never_carries_a_plus():
    for quantity in ("kcal", "net_kcal", "count", "week_minutes", "maintenance", "steps", "weigh_in"):
        assert not f.show(500, quantity).startswith("+")


# ---------------------------------------------------------------- thousands

@pytest.mark.parametrize("quantity, value, shown", [
    ("kcal", 1000, "1,000 kcal"), ("kcal", 12345, "12,345 kcal"), ("kcal", 1234567, "1,234,567 kcal"),
    ("grams", 1000, "1,000 g"), ("net_kcal", -1000, "-1,000 kcal"), ("maintenance", 2196, "2,200 kcal"),
    ("steps", 10000, "10,000 steps"), ("ml", 1000, "1,000 ml"), ("kcal_left", -1000, "1,000 kcal over"),
    ("week_minutes", 999, "999 min"),
])
def test_thousands(quantity, value, shown):
    assert f.show(value, quantity) == shown


@pytest.mark.parametrize("quantity, value, entered", [
    ("kcal", 1234, "1234"), ("kcal", 2216.4, "2216"), ("grams", 1250.5, "1250.5"), ("steps", 12000, "12000"),
    ("kcal_per_serving", 1234.56, "1234.56"), ("kcal_per_serving", 120.0, "120"), ("net_kcal", -1500, "-1500"),
    ("speed", 10.25, "10.25"), ("incline", 2.25, "2.25"), ("weigh_in", 80.0, "80"), ("weigh_in", 80.15, "80.15"),
    ("session_time", 27.5, "27.5"), ("session_time", 75.0, "75"), ("rate", 0.5, "0.5"), ("rate", -0.24, "-0.24"),
    ("week_minutes", 150, "150"), ("count", 3, "3"), ("kcal", 2500, "2500"), ("bpm", 62, "62"),
    ("cm", 86.25, "86.25"), ("goal_target", 29.5, "29.5"), ("kcal", -0.4, "0"), ("grams", 12.345, "12.35"),
])
def test_field(quantity, value, entered):
    assert f.field(value, quantity) == entered


@pytest.mark.parametrize("quantity, value", [
    ("kcal", 1234), ("grams", 1250.5), ("kcal_per_serving", 1234.56), ("speed", 10.25), ("incline", 2.25),
    ("weigh_in", 80.15), ("weight_kg", 102.5), ("session_time", 27.5), ("km", 5.25), ("servings", 1.25),
    ("rate", -0.24), ("cm", 86.25), ("hours", 7.25), ("steps", 12000), ("goal_target", 77.25),
    ("goal_time", 29.5), ("net_kcal", -1500),
])
def test_field_parses_back_to_the_stored_value(quantity, value):
    assert rules.to_float(f.field(value, quantity)) == value


@pytest.mark.parametrize("quantity", sorted(f.STEP))
def test_field_has_no_separator_unit_or_plus_and_parses(quantity):
    for value in (1234567.25, 0.5, 80.0, -2.5):
        text = f.field(value, quantity)
        assert re.fullmatch(r"-?\d+(\.\d+)?", text), text
        assert rules.to_float(text) is not None


def test_field_keeps_the_stored_decimals_where_the_page_rounds():
    assert f.show(10.25, "speed") == "10.3 km/h"
    assert f.field(10.25, "speed") == "10.25"
    assert f.show(120.55, "kcal_per_serving") == "121 kcal"
    assert f.field(120.55, "kcal_per_serving") == "120.55"


# ---------------------------------------------------------------- m:ss and clock

@pytest.mark.parametrize("minutes, shown", [
    (28, "28:00"), (27.5, "27:30"), (5.6, "5:36"), (59, "59:00"), (75, "75:00"), (125.25, "125:15"),
    (0, "0:00"), (0.999, "1:00"), (29.9999, "30:00"), (0.125, "0:08"), (0.1249, "0:07"), (0.1251, "0:08"),
])
def test_clock(minutes, shown):
    assert f.clock(minutes) == shown


@pytest.mark.parametrize("minutes, shown", [
    (29.5, "29:30.0"), (29.51, "29:30.6"), (29.5216666, "29:31.3"), (75, "75:00.0"), (0.0175, "0:01.1"),
    (0.01749, "0:01.0"), (0.01751, "0:01.1"),
])
def test_clock_widened(minutes, shown):
    assert f.clock(minutes, widened=True) == shown


def test_clock_and_pace_stay_in_minutes_past_an_hour():
    assert f.clock(90) == "90:00"
    assert f.show(90, "pace") == "90:00"
    assert f.show(90, "goal_time") == "90:00"
    assert f.show(90, "session_time") == "1:30:00"


def test_session_time_hours_widened():
    assert f.show(60, "session_time", widened=True) == "1:00:00.0"
    assert f.show(59.5, "session_time", widened=True) == "59:30.0"


# ---------------------------------------------------------------- widened

@pytest.mark.parametrize("value, quantity, shown", [
    (80.1, "bodyweight_derived", "80.10 kg"), (80.14, "bodyweight_derived", "80.14 kg"),
    (80.08, "bodyweight_derived", "80.08 kg"), (80.0, "bodyweight_derived", "80.00 kg"),
    (80.125, "bodyweight_derived", "80.13 kg"), (107.9, "estimated_max", "107.90 kg"),
    (86.5, "cm", "86.50 cm"), (62, "bpm", "62.0 bpm"), (61.55, "bpm", "61.6 bpm"),
    (29.5, "goal_time", "29:30.0"), (29.51, "goal_time", "29:30.6"),
    (0.5 / 60, "time_gap", "0.5 seconds"), (1 / 60, "time_gap", "1.0 seconds"),
    (0.7 / 60, "time_gap", "0.7 seconds"), (0.06, "bodyweight_derived", "0.06 kg"),
    (2513, "maintenance", "2,513 kcal"), (0.5, "rate", "+0.500"), (-1 / 60, "time_pace", "-1.0 seconds a week"),
])
def test_widened(value, quantity, shown):
    assert f.show(value, quantity, widened=True) == shown


@pytest.mark.parametrize("minutes, shown", [
    (0.2, "12 seconds"), (1 / 60, "1 second"), (2 / 60, "2 seconds"), (0, "0 seconds"),
    (0.025, "2 seconds"), (0.0249, "1 second"), (0.075, "5 seconds"), (0.0749, "4 seconds"),
    (0.0751, "5 seconds"),
])
def test_time_gap_whole(minutes, shown):
    assert f.show(minutes, "time_gap") == shown


@pytest.mark.parametrize("minutes, shown", [
    (0.5 / 60, "0.5 seconds"), (0.1 / 60, "0.1 seconds"), (1 / 60, "1.0 seconds"), (0.2025, "12.2 seconds"), (0.20249, "12.1 seconds"),
    (-0.7 / 60, "0.7 seconds"),
])
def test_time_gap_widened(minutes, shown):
    assert f.show(minutes, "time_gap", widened=True) == shown


AHEAD_OR_BEHIND = "{now} now against {plan} on the plan line today: {gap} {way}."
ON_THE_LINE = "On the plan line today, at {now}."


def test_plan_line_examples_from_the_contract():
    # Section E1's plan_line templates, word for word, filled through show with a plain space.
    q = "bodyweight_derived"
    assert (AHEAD_OR_BEHIND.format(now=f.show(107.92, q), plan=f.show(105.24, q), gap=f.show(2.68, q), way="ahead")
            == "107.9 kg now against 105.2 kg on the plan line today: 2.7 kg ahead.")
    assert (ON_THE_LINE.format(now=f.show(80.25, q)) == "On the plan line today, at 80.3 kg.")
    assert (AHEAD_OR_BEHIND.format(now=f.show(80.14, q, widened=True), plan=f.show(80.08, q, widened=True),
                                   gap=f.show(0.06, q, widened=True), way="behind")
            == "80.14 kg now against 80.08 kg on the plan line today: 0.06 kg behind.")
    assert (AHEAD_OR_BEHIND.format(now=f.show(29.51, "goal_time", widened=True),
                                   plan=f.show(29.5216666, "goal_time", widened=True),
                                   gap=f.show(0.7 / 60, "time_gap", widened=True), way="ahead")
            == "29:30.6 now against 29:31.3 on the plan line today: 0.7 seconds ahead.")


# ---------------------------------------------------------------- page, units and spaces

@pytest.mark.parametrize("quantity, value, shown", [
    ("weigh_in", 80, f"80{NBSP}kg"), ("kcal", 2216, f"2,216{NBSP}kcal"),
    ("kcal_left", 300, f"300{NBSP}kcal left"), ("kcal_left", -30, f"30{NBSP}kcal over"),
    ("time_gap", 0.2, f"12{NBSP}seconds"), ("time_pace", -0.2, f"-12{NBSP}seconds a week"),
    ("percent", 41, "41%"), ("incline", 2.5, "2.5%"), ("rate", -0.24, "-0.24"), ("count", 3, "3"),
    ("goal_time", 29.5, "29:30"), ("speed", 10, f"10{NBSP}km/h"),
])
def test_page_puts_a_no_break_space_before_the_unit(quantity, value, shown):
    assert f.show(value, quantity, page=True) == shown
    assert f.show(value, quantity) == shown.replace(NBSP, " ")


@pytest.mark.parametrize("quantity", sorted(f.STEP))
def test_text_is_ascii_but_the_no_break_space(quantity):
    for value in (0, 1234.567, -61.5, 0.05):
        for page in (False, True):
            for widened in (False, True):
                text = f.show(value, quantity, page=page, widened=widened)
                assert all(ch.isascii() or ch == NBSP for ch in text), text
                assert (NBSP in text) <= page
                assert "  " not in text and text == text.strip()


# ---------------------------------------------------------------- unit=False, the number alone

@pytest.mark.parametrize("quantity, value, widened, alone", [
    ("weigh_in", 80, False, "80"), ("bodyweight_derived", 80.1, True, "80.10"),
    ("kcal", 2216, False, "2,216"), ("maintenance", 2513, False, "2,510"), ("net_kcal", -1500, False, "-1,500"),
    ("weight_change", 0.3, False, "+0.3"), ("weight_change", -0.004, False, "0"), ("percent", 41, False, "41"),
    ("incline", 2.25, True, "2.25"), ("speed", 10, False, "10"), ("bpm", 62, True, "62.0"),
    ("kcal_left", 120, False, "120"), ("kcal_left", -120, False, "120"), ("kcal_left", -1250.5, False, "1,251"),
    ("time_gap", 0.2, False, "12"), ("time_gap", 1 / 60, False, "1"), ("time_gap", 0.0125, True, "0.8"),
    ("time_pace", -0.2, False, "-12"), ("time_pace", 1 / 60, False, "+1"), ("time_pace", 0, False, "0"),
    ("time_pace", -25, False, "-1,500"),
])
def test_unit_false_is_the_number_alone(quantity, value, widened, alone):
    for page in (False, True):
        assert f.show(value, quantity, page=page, widened=widened, unit=False) == alone


@pytest.mark.parametrize("quantity", sorted(f.STEP))
def test_unit_false_is_the_front_of_the_shown_text(quantity):
    for value in (0, 1234.567, -61.5, 0.05):
        for widened in (False, True):
            for page in (False, True):
                whole = f.show(value, quantity, page=page, widened=widened)
                alone = f.show(value, quantity, page=page, widened=widened, unit=False)
                assert whole.startswith(alone)
                assert " " not in alone and NBSP not in alone and not re.search(r"[a-z%]", alone)


@pytest.mark.parametrize("quantity, value", [
    ("rate", -0.24), ("rate", 0.5), ("count", 1598), ("reps", 6), ("servings", 1.5), ("met", 9.8),
    ("count_avg", 2.75), ("goal_target", 77.25), ("goal_time", 29.5), ("pace", 75), ("session_time", 75),
])
def test_unit_false_changes_nothing_without_a_unit(quantity, value):
    for page in (False, True):
        for widened in (False, True):
            assert (f.show(value, quantity, page=page, widened=widened, unit=False)
                    == f.show(value, quantity, page=page, widened=widened))


def test_unit_is_keyword_only():
    with pytest.raises(TypeError):
        f.show(80, "weigh_in", False, False, False)


# ---------------------------------------------------------------- rounded, is_zero, widen

@pytest.mark.parametrize("value, quantity, widened, step, result", [
    (79.25, "bodyweight_derived", False, None, 79.3), (79.2499, "bodyweight_derived", False, None, 79.2),
    (80.25, "bodyweight_derived", False, None, 80.3), (0.15, "bodyweight_derived", False, None, 0.2),
    (79.05, "bodyweight_derived", False, None, 79.1),
    (79.245, "bodyweight_derived", True, None, 79.25), (79.2449, "bodyweight_derived", True, None, 79.24),
    (107.925, "estimated_max", True, None, 107.93),
    (61.5, "bpm", False, None, 62.0), (61.55, "bpm", True, None, 61.6),
    (2513, "maintenance", False, None, 2510.0), (2513, "maintenance", True, None, 2513.0),
    (-0.04, "bodyweight_derived", False, None, 0.0), (-0.005, "rate", False, None, -0.01),
    (1234.56, "weigh_in", False, None, 1234.56), (80.3, "bodyweight_derived", False, None, 80.3),
    (80.25, "weigh_in", False, None, 80.25), (0.1 + 0.2, "bodyweight_derived", False, None, 0.3),
    # step: quantized in minutes, with no conversion to seconds.
    (29.515, "goal_time", False, 0.01, 29.52), (29.5149, "goal_time", False, 0.01, 29.51),
    (-0.315, "goal_time", False, 0.01, -0.32), (29.5, "goal_time", False, 0.01, 29.5),
    (0.2449, "time_gap", False, 0.01, 0.24), (29.5151, "goal_time", True, 0.01, 29.515),
])
def test_rounded(value, quantity, widened, step, result):
    # The exact float, not one near it: 80.30000000000001 is a failure.
    assert repr(f.rounded(value, quantity, widened=widened, step=step)) == repr(result)


@pytest.mark.parametrize("quantity", ["session_time", "pace", "goal_time", "time_pace", "time_gap"])
def test_rounded_in_minutes_needs_a_step(quantity):
    with pytest.raises(ValueError, match="step"):
        f.rounded(29.515, quantity)
    with pytest.raises(ValueError, match="step"):
        f.rounded(29.515, quantity, widened=True)
    assert f.rounded(29.515, quantity, step=0.01) == 29.52


def test_is_zero_in_minutes_needs_no_step():
    assert f.is_zero(0.0083, "goal_time") is True
    assert f.is_zero(0.0084, "time_gap") is False


def test_rounded_returns_a_float_and_never_minus_zero():
    for value in (-0.04, -0.0, -1e-9):
        result = f.rounded(value, "bodyweight_derived")
        assert isinstance(result, float)
        assert str(result) == "0.0"
    assert str(f.rounded(-0.001, "goal_time", step=0.01)) == "0.0"
    assert isinstance(f.rounded(3, "count"), float)


@pytest.mark.parametrize("value, quantity, zero", [
    (0.5, "bpm", False), (-0.5, "bpm", False), (0.4999, "bpm", True), (-0.4999, "bpm", True),
    (0.05, "bodyweight_derived", False), (-0.05, "bodyweight_derived", False),
    (0.0499, "bodyweight_derived", True), (0, "bodyweight_derived", True),
    (0.0084, "goal_time", False), (0.0083, "goal_time", True), (0.125, "time_gap", False),
    (0.005, "rate", False), (0.0049, "rate", True), (4.9999, "maintenance", True), (5, "maintenance", False),
])
def test_is_zero(value, quantity, zero):
    assert f.is_zero(value, quantity) is zero


@pytest.mark.parametrize("now, plan, quantity, result", [
    # Rule 2: the two print the same and the gap is real.
    (60.25, 59.75, "bpm", True),        # both "60", the gap 0.5 exactly: a half rounds away from zero
    (59.75, 60.25, "bpm", True),        # a down goal's way round
    (80.14, 80.08, "bodyweight_derived", True),
    (29.51, 29.5216666, "goal_time", True),
    # They print differently.
    (60.5, 59.5, "bpm", False),
    (80.25, 80.2, "bodyweight_derived", False),
    # The gap rounds to 0.
    (60.2, 60.0, "bpm", False),
    (60.0, 60.0, "bpm", False),
    (60.0, 59.5625, "bpm", False),      # gap 0.4375, under a half
    (80.12, 80.08, "bodyweight_derived", False),
])
def test_widen(now, plan, quantity, result):
    assert f.widen([now, plan], quantity) is result


# At and around a half. The gap is the plain float now - plan, the same float ahead_by carries,
# and is_zero and widen both see it: 80.24 - 80.19 is 0.04999999999999716, so rule 1 applies.
@pytest.mark.parametrize("now, plan, quantity, result", [
    (80.24, 80.19, "bodyweight_derived", False),     # the float gap sits just under 0.05
    (80.2, 80.15, "bodyweight_derived", False),      # the same float gap
    (80.24, 80.1899, "bodyweight_derived", True),    # just past the half
    (80.2401, 80.19, "bodyweight_derived", True),
    (80.19, 80.2401, "bodyweight_derived", True),    # a down goal's way round
    (80.2, 80.1499, "bodyweight_derived", False),    # past the half, but they print differently
    (86.24, 86.19, "cm", False), (86.24, 86.1899, "cm", True), (86.2401, 86.19, "cm", True),
    (29.5075, 29.499167, "goal_time", False),        # a gap of 0.49998 seconds
    (29.5075, 29.4991, "goal_time", True),           # a gap of 0.504 seconds
    (29.5075, 29.4925, "goal_time", True),
    (29.505, 29.4983, "goal_time", False),
    (29.5, 29.4916, "goal_time", False),             # 29:30 against 29:29
])
def test_widen_at_a_half(now, plan, quantity, result):
    assert f.show(now, quantity) == f.show(plan, quantity) or result is False
    assert f.is_zero(now - plan, quantity) is (not result and f.show(now, quantity) == f.show(plan, quantity))
    assert f.widen([now, plan], quantity) is result


def test_widen_takes_any_pair():
    assert f.widen((60.25, 59.75), "bpm") is True


# ---------------------------------------------------------------- the Decimal context and STEP

CALLS = [
    (lambda: f.show(29.51, "goal_time", widened=True), "29:30.6"),
    (lambda: f.show(1234.567, "weigh_in"), "1,234.57 kg"),
    (lambda: f.show(1e21, "kcal"), "1,000,000,000,000,000,000,000 kcal"),
    (lambda: f.show(80.25, "bodyweight_derived"), "80.3 kg"),
    (lambda: f.show(-0.004, "rate"), "0"),
    (lambda: f.rounded(1234.56, "weigh_in"), 1234.56),
    (lambda: f.rounded(29.515, "goal_time", step=0.01), 29.52),
    (lambda: f.rounded(2513, "maintenance"), 2510.0),
    (lambda: f.is_zero(0.05, "bodyweight_derived"), False),
    (lambda: f.is_zero(0.0083, "goal_time"), True),
    (lambda: f.widen([80.24, 80.1899], "bodyweight_derived"), True),
    (lambda: f.widen([80.24, 80.19], "bodyweight_derived"), False),
    (lambda: f.clock(29.51, widened=True), "29:30.6"),
    (lambda: f.clock(75), "75:00"),
    (lambda: f.field(1234.56, "kcal_per_serving"), "1234.56"),
    (lambda: f.field(12345.5, "kcal"), "12346"),
]


def hostile_contexts():
    yield "precision 3", {"prec": 3}
    yield "an Inexact trap", {"traps": [decimal.Inexact, decimal.Rounded]}
    yield "rounding down", {"rounding": decimal.ROUND_DOWN, "prec": 5}
    yield "half even", {"rounding": decimal.ROUND_HALF_EVEN}
    yield "a small exponent range", {"Emax": 5, "Emin": -5, "traps": [decimal.Overflow, decimal.Underflow]}


@pytest.mark.parametrize("name, settings", list(hostile_contexts()))
def test_a_callers_decimal_context_changes_nothing(name, settings):
    assert [call() for call, _ in CALLS] == [expected for _, expected in CALLS]
    before = decimal.getcontext().copy()
    try:
        decimal.setcontext(decimal.Context(**settings))
        assert [call() for call, _ in CALLS] == [expected for _, expected in CALLS], name
    finally:
        decimal.setcontext(before)
    assert decimal.getcontext().prec == before.prec


def test_step_is_read_only():
    with pytest.raises(TypeError):
        f.STEP["kcal"] = 5
    with pytest.raises(TypeError):
        del f.STEP["kcal"]
    assert f.STEP["kcal"] == 1


# ---------------------------------------------------------------- errors and exponents

@pytest.mark.parametrize("quantity", sorted(f.STEP))
def test_show_none_raises_type_error(quantity):
    with pytest.raises(TypeError):
        f.show(None, quantity)


@pytest.mark.parametrize("bad", ["80", True, [80]])
def test_show_refuses_what_is_not_a_number(bad):
    with pytest.raises(TypeError):
        f.show(bad, "kcal")


def test_unknown_quantity_raises_value_error():
    for call in (lambda: f.show(1, "stone"), lambda: f.rounded(1, "stone"), lambda: f.field(1, "stone"),
                 lambda: f.is_zero(1, "stone"), lambda: f.widen([1, 2], "stone")):
        with pytest.raises(ValueError, match="stone"):
            call()


def test_not_finite_raises_value_error():
    with pytest.raises(ValueError):
        f.show(float("nan"), "kcal")
    with pytest.raises(ValueError):
        f.show(float("inf"), "kcal")


EXPONENT = re.compile(r"\d[eE]|[eE][+-]?\d")


@pytest.mark.parametrize("quantity", sorted(f.STEP))
def test_no_exponent_ever(quantity):
    for value in (1e-7, -1e-7, 1e21, -1e21, 123456789.0, 1e300, 5e-324, 1.7976931348623157e308):
        for widened in (False, True):
            assert not EXPONENT.search(f.show(value, quantity, widened=widened))
        assert not EXPONENT.search(f.field(value, quantity))


def test_large_and_small_in_full():
    assert f.show(1e21, "kcal") == "1,000,000,000,000,000,000,000 kcal"
    assert f.show(123456789.0, "weigh_in") == "123,456,789 kg"
    assert f.show(1e-7, "weigh_in") == "0 kg"
    assert f.show(1e-7, "rate") == "0"
    assert f.field(1e21, "kcal") == "1000000000000000000000"
    assert f.field(1e-7, "grams") == "0"
    assert f.clock(1e-7) == "0:00"


# ---------------------------------------------------------------- dates

def forms(d: date):
    """The three ways a date arrives: a date, a datetime and an ISO string."""
    return (d, datetime(d.year, d.month, d.day, 15, 30), d.isoformat())


@pytest.mark.parametrize("d, shown", [
    (date(2026, 11, 10), "Tue 10 Nov"),          # today
    (date(2026, 11, 9), "Mon 9 Nov"),            # yesterday
    (date(2026, 3, 1), "Sun 1 Mar"),             # this year
    (date(2025, 11, 10), "Mon 10 Nov 2025"),     # another year
    (date(2027, 1, 5), "Tue 5 Jan 2027"),
])
def test_day(d, shown):
    for given in forms(d):
        for today in forms(TODAY):
            assert f.day(given, today) == shown


@pytest.mark.parametrize("d, shown", [
    (date(2026, 11, 10), "Today, Tuesday 10 November"),
    (date(2026, 11, 9), "Yesterday, Monday 9 November"),
    (date(2026, 11, 8), "Sunday 8 November"),
    (date(2026, 11, 11), "Wednesday 11 November"),
    (date(2025, 11, 8), "Saturday 8 November 2025"),
])
def test_day_heading(d, shown):
    for given in forms(d):
        for today in forms(TODAY):
            assert f.day_heading(given, today) == shown


def test_day_heading_across_new_year():
    assert f.day_heading(date(2025, 12, 31), date(2026, 1, 1)) == "Yesterday, Wednesday 31 December 2025"
    assert f.day_heading(date(2026, 1, 1), date(2026, 1, 1)) == "Today, Thursday 1 January"


@pytest.mark.parametrize("start, end, shown", [
    (date(2026, 11, 3), date(2026, 11, 9), "3 to 9 Nov"),                      # one month
    (date(2026, 10, 28), date(2026, 11, 3), "28 Oct to 3 Nov"),                # across months
    (date(2025, 12, 29), date(2026, 1, 4), "29 Dec 2025 to 4 Jan 2026"),       # across years
    (date(2025, 11, 3), date(2025, 11, 9), "3 to 9 Nov 2025"),                 # both in one other year
    (date(2025, 10, 28), date(2025, 11, 3), "28 Oct to 3 Nov 2025"),
    (date(2026, 12, 29), date(2027, 1, 4), "29 Dec 2026 to 4 Jan 2027"),
    (date(2026, 10, 27), date(2026, 11, 10), "27 Oct to 10 Nov"),
    (date(2026, 11, 10), date(2026, 11, 10), "10 Nov"),                        # one day
    (date(2025, 11, 10), date(2025, 11, 10), "10 Nov 2025"),
])
def test_days(start, end, shown):
    for a in forms(start):
        for b in forms(end):
            assert f.days(a, b, TODAY.isoformat()) == shown
    assert f.days(start, end, datetime(2026, 11, 10, 23, 59)) == shown


@pytest.mark.parametrize("d, short, letter", [
    (date(2026, 11, 9), "Mon", "M"), (date(2026, 11, 10), "Tue", "T"), (date(2026, 11, 11), "Wed", "W"),
    (date(2026, 11, 12), "Thu", "T"), (date(2026, 11, 13), "Fri", "F"), (date(2026, 11, 14), "Sat", "S"),
    (date(2026, 11, 15), "Sun", "S"),
])
def test_weekday(d, short, letter):
    for given in forms(d):
        assert f.weekday(given) == short
        assert f.weekday_letter(given) == letter


def test_dates_carry_no_padding_or_ordinal():
    for n in range(40):
        text = f.day(TODAY - timedelta(days=n), TODAY)
        assert not re.search(r"\b0\d|st\b|nd\b|rd\b|th\b|,", text), text


def test_iso_datetime_string_is_its_date():
    assert f.day("2026-11-09T23:30:00", TODAY) == "Mon 9 Nov"
    assert f.day("2026-11-09T23:30:00+00:00", TODAY) == "Mon 9 Nov"


def test_bad_dates_raise():
    with pytest.raises(ValueError):
        f.day("not a date", TODAY)
    with pytest.raises(TypeError):
        f.day(None, TODAY)
    with pytest.raises(TypeError):
        f.weekday(20261110)


# ---------------------------------------------------------------- day_utc

@pytest.mark.parametrize("utc, zone, shown", [
    # A zone ahead of UTC: 19:00 UTC on the 9th is 00:30 on the 10th in India.
    (datetime(2026, 11, 9, 19, 0, tzinfo=timezone.utc), "Asia/Kolkata", "Tue 10 Nov"),
    (datetime(2026, 11, 9, 18, 29, tzinfo=timezone.utc), "Asia/Kolkata", "Mon 9 Nov"),
    # A zone behind UTC: 03:00 UTC on the 10th is 22:00 on the 9th in New York.
    (datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc), "America/New_York", "Mon 9 Nov"),
    (datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc), "America/New_York", "Tue 10 Nov"),
    # Across a year: 31 Dec 20:00 UTC is 1 Jan in India.
    (datetime(2025, 12, 31, 20, 0, tzinfo=timezone.utc), "Asia/Kolkata", "Thu 1 Jan"),
    (datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc), "America/New_York", "Wed 31 Dec 2025"),
    # UTC itself, unset and an unknown zone are UTC.
    (datetime(2026, 11, 9, 23, 59, tzinfo=timezone.utc), "UTC", "Mon 9 Nov"),
    (datetime(2026, 11, 9, 23, 59, tzinfo=timezone.utc), None, "Mon 9 Nov"),
    (datetime(2026, 11, 9, 23, 59, tzinfo=timezone.utc), "Not/A_Zone", "Mon 9 Nov"),
])
def test_day_utc(utc, zone, shown):
    today = date(2026, 1, 1) if utc.year == 2025 or utc.month == 1 else TODAY
    naive = utc.replace(tzinfo=None)
    for given in (utc, naive, utc.isoformat(), naive.isoformat(), utc.isoformat().replace("+00:00", "Z")):
        for this_day in forms(today):
            assert f.day_utc(given, zone, this_day) == shown


def test_day_utc_takes_a_tzinfo():
    utc = datetime(2026, 11, 9, 19, 0, tzinfo=timezone.utc)
    assert f.day_utc(utc, ZoneInfo("Asia/Kolkata"), TODAY) == "Tue 10 Nov"
    assert f.day_utc(utc, timezone(timedelta(hours=-5)), TODAY) == "Mon 9 Nov"


def test_day_utc_takes_an_aware_time_in_another_zone():
    # 00:30 in India is 19:00 UTC the day before, then back to India.
    india = datetime(2026, 11, 10, 0, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert f.day_utc(india, "Asia/Kolkata", TODAY) == "Tue 10 Nov"
    assert f.day_utc(india, "UTC", TODAY) == "Mon 9 Nov"


def test_day_utc_plain_date_is_midnight_utc():
    assert f.day_utc(date(2026, 11, 10), "America/New_York", TODAY) == "Mon 9 Nov"
    assert f.day_utc("2026-11-10", "Asia/Kolkata", TODAY) == "Tue 10 Nov"
