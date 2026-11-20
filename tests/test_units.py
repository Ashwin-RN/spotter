"""Units (contract units.md, package U1): a person logs and hears weights in lb and distances in miles, and Spotter
keeps kg and km underneath. The store, the services and every text a model reads. The pages and charts are U2's.

Every figure here is the contract's, worked with half-up Decimal from the 2 dp store: 225 lb is kept as 102.06 kg
and reads 225 lb; 185 lb is 83.91 kg; 135 lb is 61.23 kg; 3 mi is 4.83 km and reads 3 mi, a pace of 9:00 per mi;
Epley on 102.06 kg x 5 is 119.07 kg, 262.5 lb.
"""

import csv
import hashlib
import io
import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select

from app import calllog, db, formatting, mcp_server, metrics, rules, services, users
from app.db import make_engine
from app.formatting import KG_KM, Units
from app.models import Bodyweight, CardioEntry, EntryVersion, Goal, LiftSet, Routine, User
from app.services import common, goals, lifts, progress, trends
from evals import judge
from evals.run import LocalServer, dates_for, person_rows

from . import text_rules as tr
from .conftest import TEST_DIR, alembic_config, sign
from .test_feedback import PUBLISHED, compact
from .test_inputs import without_inputs
from .test_mcp import failed, ok, read_resource, tool_list
from .test_trace import OWNER, last, sign_for

TIMES = "×"
LB, MI = Units("lb", "km"), Units("kg", "mi")
LB_MI = Units("lb", "mi")
WEIGHT_NOTE = common.WEIGHT_UNITS_NOTE
DISTANCE_NOTE = common.DISTANCE_UNITS_NOTE
# Every function as published at ee00eab, before this work (tests/test_feedback.py's PUBLISHED then).
BEFORE_UNITS = {
    "whoami": "e415c4054cff9ffc6565bc294b147b9868cd78d89694240de571bd926a3c0417",
    "list_exercises": "5c8162608e9e239073cc020cb7278ceace0ae3481a94944e28f8a736216c2e27",
    "log_lifts": "9e95e0445afc3a57390021fcc57a0c9774146be42ff37d4447c51b1ac995fe18",
    "log_bodyweight": "f6d2e5b9ad195b9256152099a072142fdd926c7e176e35fc561e036ff66654b7",
    "set_goal": "266f24d8d1467cc4080c620386262b4f5108ce61c002c9975f830028eabc759f",
    "get_progress": "e01a7b022248ac56d33d1d9a812e1c3b582187af14ee63bb7a6789bd0cd359e0",
    "update_settings": "8c4f498c508107ef3610e2e735d5e839647aedd30dabefcaed87471c313355c1",
    "get_log": "0f13ed202bfb439905e0c81120ab93ecf50031da8a720dac9e790916dd104169",
    "log_measurement": "7456c5853a541df971d7b8ab8f382d835e82a6b1ca35c8ece0c60c985db12647",
    "edit_entry": "3fbe15511d1b775980d1d89c5e24414a52b5cf4ccf1def94133f0b7d29673e7e",
    "restore_entry": "ea098328552576986f8c0a410757d424478eb0566b104e2eb76d3de4b24cf8a9",
    "delete_entry": "197b142a4e73b004c557a70b3a39c5ae3d2b156114159a00929838e6df8246ee",
    "find_food": "cf6cce7ada8d743d741108f84359140a7c0571d85ef43b0964b70cfbdc101189",
    "log_food": "6ffc9850d580f531c6cf3bd6d961b186936aa00b5bf3b9af19198a3a8e7acd56",
    "save_food": "0a8e5b4526af8bc103af661c711ad6719ab8588579dc64ceb4b6b4ed6cb218cc",
    "log_cardio": "22bfeaeb1a13afa6438c365ba0d9b699a9eb1b4adf09cc38ae92ea5f4914f764",
    "get_trends": "ffb7403c8b2029888d1c57e431059922e77c4884e21934b0632d0e68c063aa3e",
    "get_goals": "ac0e1ea4076d1f304ef604f676d024b743ff07ffdd01615c84a1aa5702a70cbe",
    "import_entries": "8e1bce9821e8547d62c9768c5b05b9a7c686c7437e2f34e80d8275a4a82d5ffd",
    "save_routine": "d9733bb7c9c1e4438f050b425ba003514ba0b7bc7cc83823660186ac5f6809de",
    "get_routines": "f40c07973f168e4a1f4462109f594893ac56e86aa8c53ebdede94257aad4aa3a",
    "owner_calls": "79ec889297b107ba7f25781a2ace0a058b712960510d994d36788485da57d787",
}
THIRTEEN = {"whoami", "log_lifts", "log_bodyweight", "log_cardio", "edit_entry", "set_goal", "update_settings",
            "get_progress", "get_goals", "get_log", "delete_entry", "save_routine", "get_routines"}


@pytest.fixture
def lb(make_user):
    """A person whose weights show in lb and distances in miles."""
    return make_user(time_zone="Europe/London", weight_unit="lb", distance_unit="mi")


@pytest.fixture
def fresh(make_user):
    """A person who never chose units."""
    return make_user(time_zone="Europe/London")


def today(user) -> str:
    return users.today_for(user).isoformat()


def ago(user, days: int) -> str:
    return (users.today_for(user) - timedelta(days=days)).isoformat()


def said_day(user, days: int = 0) -> str:
    d = users.today_for(user) - timedelta(days=days)
    return formatting.day(d, users.today_for(user))


def bench(weight=None, reps=5, **sent):
    row = {"exercise": "Bench Press", "reps": reps}
    if weight is not None:
        row["weight_kg"] = weight
    return {**row, **sent}


def one_user(user_id: int) -> User:
    with db.session_scope() as s:
        return s.get(User, user_id)


def stored_sets(session_id: int) -> list[float]:
    with db.session_scope() as s:
        return [x.weight_kg for x in s.scalars(select(LiftSet).where(LiftSet.session_id == session_id)
                                               .order_by(LiftSet.id))]


# ---------------------------------------------------------------- the seam: formatting and users (R2 to R5)

@pytest.mark.parametrize("value, quantity, shown", [
    (102.06, "weight_kg", "225 lb"), (21.55, "weight_kg", "47.5 lb"), (20.98, "weight_kg", "46.25 lb"),
    (20, "weight_kg", "44.1 lb"), (100, "weight_kg", "220.45 lb"), (83.91, "weigh_in", "185 lb"),
    (61.23, "weight_kg", "135 lb"), (104.33, "weight_kg", "230 lb"), (119.07, "estimated_max", "262.5 lb"),
    (84, "bodyweight_derived", "185.2 lb"), (-0.25, "weight_change", "-0.55 lb"), (-0.25, "weight_rate", "-0.55"),
    (100, "weight_target", "220.45"), (4.83, "km", "3 mi"), (10, "speed", "6.2 mph"), (27 / 4.83, "pace", "9:00"),
    (80, "cm", "80 cm"), (62, "bpm", "62 bpm"), (300, "kcal", "300 kcal"),
])
def test_a_lb_and_mi_person_reads_each_figure_at_its_step(value, quantity, shown):
    assert formatting.show(value, quantity, units=LB_MI) == shown


def test_kg_and_km_and_none_read_exactly_as_before():
    for value, quantity in ((102.06, "weight_kg"), (119.07, "estimated_max"), (4.83, "km"), (10.25, "speed"),
                            (27 / 4.83, "pace"), (-0.25, "rate"), (84.25, "bodyweight_derived")):
        assert formatting.show(value, quantity) == formatting.show(value, quantity, units=KG_KM) \
            == formatting.show(value, quantity, units=None)
    assert formatting.show(-0.25, "weight_rate") == formatting.show(-0.25, "rate") == "-0.25"
    assert formatting.show(77.25, "weight_target") == formatting.show(77.25, "goal_target") == "77.25"
    assert formatting.field(80, "weigh_in") == "80" and formatting.field(80, "weigh_in", units=KG_KM) == "80"


def test_the_widened_step_in_lb_is_a_hundredth_and_a_thousandth_for_a_change():
    assert formatting.show(102.06, "weight_kg", widened=True, units=LB) == "225.00 lb"
    assert formatting.show(119.07, "estimated_max", widened=True, units=LB) == "262.50 lb"
    assert formatting.show(0.25, "weight_change", widened=True, units=LB) == "+0.551 lb"
    # Two stored numbers 0.01 kg apart read the same at 0.05 lb, and apart at 0.01 lb (decision 9).
    assert formatting.show(102.06, "weight_kg", units=LB) == formatting.show(102.05, "weight_kg", units=LB)
    # Close-call rule 2 in lb: the two print the same and the gap does not round to 0 at the lb step.
    assert formatting.widen([84.02, 83.99], "bodyweight_derived", units=LB)
    assert not formatting.widen([84.02, 83.99], "bodyweight_derived")
    assert not formatting.widen([102.06, 102.05], "weight_kg", units=LB)  # a gap of 0.02 lb rounds to 0


def test_a_form_field_in_lb_is_filled_at_the_shown_step_and_convert_and_unit_word_say_the_unit():
    assert formatting.field(80, "weigh_in", units=LB) == "176.35"
    assert formatting.field(4.83, "km", units=MI) == "3"
    assert formatting.convert(4.83, "km", MI) == pytest.approx(3.00122, abs=1e-5)
    assert formatting.convert(80, "cm", LB_MI) == 80.0 and formatting.convert(80, "weight_kg", KG_KM) == 80.0
    assert [formatting.unit_word(k, LB_MI) for k in ("weight", "distance", "speed")] == ["lb", "mi", "mph"]
    assert [formatting.unit_word(k) for k in ("weight", "distance", "speed")] == ["kg", "km", "km/h"]
    assert formatting.rounded(119.07, "estimated_max") == 119.1  # number fields stay kg: rounded takes no units


def test_units_for_reads_the_two_columns_kg_and_km_while_they_are_null(make_user):
    assert users.units_for(make_user()) == KG_KM and users.units_set(make_user()) == (False, False)
    assert users.units_for(make_user(weight_unit="lb")) == Units("lb", "km")
    assert users.units_for(make_user(distance_unit="mi")) == Units("kg", "mi")
    assert users.units_set(make_user(weight_unit="kg")) == (True, False)


def test_the_moved_factors_are_the_imports_own():
    from app.services import imports

    assert imports.UNITS is rules.UNITS and rules.UNITS["weight_unit"]["lb"] == formatting.LB == 0.45359237
    assert rules.UNITS["distance_unit"]["mi"] == formatting.MI == 1.609344
    assert imports._inward is rules.inward and imports._call_unit is rules.call_unit
    assert [rules.inward(r, f, q) for r, f, q in (((0, 500), formatting.LB, "weight_kg"),
                                                  ((20, 400), formatting.LB, "weigh_in"),
                                                  ((0.01, 300), formatting.MI, "km"),
                                                  ((0.1, 60), formatting.MI, "speed"),
                                                  ((0, goals.LIFT_MAX), formatting.LB, "weight_target"),
                                                  ((0.1, 300), formatting.MI, "km"))] == [
        ("0", "1,102.31"), ("44.1", "881.84"), ("0.01", "186.41"), ("0.1", "37.2"), ("0", "1,469.75"),
        ("0.07", "186.41")]


# ---------------------------------------------------------------- lifts (decisions 4, 8, 10; R8, R9)

def test_225_lb_is_kept_as_102_06_kg_and_reads_225_lb_in_the_summary_the_add_the_day_and_the_bests(lb):
    first = services.log_lifts(lb.id, {"sets": [bench(weight_lb=200)], "date": ago(lb, 7)}, "claude")
    assert "error" not in first, first
    logged = services.log_lifts(lb.id, {"sets": [bench(weight_lb=225)]}, "claude")
    assert stored_sets(logged["id"]) == [102.06] and logged["session"]["sets"][0]["weight_kg"] == 102.06
    assert logged["summary"].startswith(f"Bench Press 1 {TIMES} 5 at 225 lb on {said_day(lb)}. New best: Bench Press "
                                        "estimated max 262.5 lb, up from ")
    best = next(b for b in logged["new_bests"] if b["kind"] == "estimated_max")
    assert best["value"] == 119.1 and best["text"].startswith("Bench Press estimated max 262.5 lb")  # kg field, lb text
    added = services.log_lifts(lb.id, {"sets": [bench(weight_lb=225), bench(weight_lb=225)]}, "claude")
    assert added["summary"].startswith(f"Added Bench Press 2 {TIMES} 5 at 225 lb to the session on {said_day(lb)}. "
                                       "It now has 3 sets.")
    day = services.get_log(lb.id)["days"][0]
    assert day["summary"] == f"Bench Press 3 {TIMES} 5 at 225 lb."
    assert day["sessions"][0]["new_bests"][0]["text"].startswith("Bench Press estimated max 262.5 lb")
    progress = services.get_progress(lb.id, "Bench Press")
    assert progress["summary"].startswith(f"Bench Press: estimated max now 262.5 lb. Best set 225 lb {TIMES} 5 on "
                                          f"{said_day(lb)}. 2 sessions.")
    assert services.get_progress(lb.id)["summary"] == "Bench Press: estimated max now 262.5 lb, 2 sessions."


def test_46_25_and_47_5_read_as_said_and_a_kg_said_20_kg_reads_44_1_lb(lb):
    logged = services.log_lifts(lb.id, {"sets": [bench(weight_lb=46.25), bench(weight_lb=47.5), bench(20)]},
                                "claude")
    assert stored_sets(logged["id"]) == [20.98, 21.55, 20.0]
    assert logged["summary"].startswith(f"Bench Press 46.25 lb {TIMES} 5, 47.5 lb {TIMES} 5, 44.1 lb {TIMES} 5 on ")


def test_two_sets_that_read_the_same_in_lb_group_whatever_kg_they_hold(lb):
    logged = services.log_lifts(lb.id, {"sets": [bench(102.06), bench(102.05)]}, "claude")
    assert logged["summary"].startswith(f"Bench Press 2 {TIMES} 5 at 225 lb on ")
    assert lifts.sets_text([("Bench Press", 102.06, 5), ("Bench Press", 102.05, 5)]) == \
        f"Bench Press 102.06 kg {TIMES} 5, 102.05 kg {TIMES} 5"  # kg: shown equals stored


def test_a_best_that_reads_the_same_as_the_one_it_beat_in_lb_widens_to_a_hundredth(lb):
    services.log_lifts(lb.id, {"sets": [bench(102.05, reps=12)], "date": ago(lb, 3)}, "claude")
    logged = services.log_lifts(lb.id, {"sets": [bench(102.06, reps=12)]}, "claude")
    [best] = logged["new_bests"]
    assert best["kind"] == "weight" and (best["value"], best["previous"]) == (102.06, 102.05)
    assert best["text"] == "Bench Press heaviest weight 225.00 lb, up from 224.98 lb"
    assert lifts.best_text({**best, "text": None}) == "Bench Press heaviest weight 102.06 kg, up from 102.05 kg"


def test_a_set_with_both_weights_is_refused_one_with_neither_is_missing_and_nothing_is_written(lb):
    both = services.log_lifts(lb.id, {"sets": [bench(100), bench(100, weight_lb=220)]}, "claude")
    assert (both["error"], both["message"], both["fix"]) == (
        "invalid_field", "Set 2 has both weight_kg and weight_lb: send one.",
        "Send the weight in the unit the person said: weight_kg or weight_lb.")
    neither = services.log_lifts(lb.id, {"sets": [bench()]}, "claude")
    assert (neither["error"], neither["message"], neither["fix"]) == (
        "missing_field", "Set 1: weight_kg or weight_lb is missing.", "Give weight_kg or weight_lb on set 1.")
    assert services.get_log(lb.id)["totals"]["sessions"] == 0
    assert "units_note" not in both and "units_note" not in neither


def test_a_lb_refusal_quotes_lb_and_the_range_moved_inward(lb):
    refused = services.log_lifts(lb.id, {"sets": [bench(weight_lb=1200)]}, "claude")
    assert (refused["error"], refused["message"], refused["fix"]) == (
        "weight_out_of_range", "Set 1: weight_lb 1200 lb is outside 0 to 1,102.31 lb.",
        "Give weight_lb on set 1 from 0 to 1,102.31 lb.")
    nan = services.log_lifts(lb.id, {"sets": [bench(weight_lb="heavy")]}, "web")
    assert nan["message"] == "Set 1: weight_lb 'heavy' is not a number." and "1,102.31 lb" in nan["fix"]
    count = services.log_lifts(lb.id, {"sets": []}, "claude")
    assert count["fix"] == "Send one row per set, 1 to 60 rows: exercise, weight_kg or weight_lb, reps."


def test_a_kg_person_s_lift_texts_are_today_s_but_the_pair_s_names(make_user):
    kg = make_user(time_zone="Europe/London")
    services.log_lifts(kg.id, {"sets": [bench(97.5)], "date": ago(kg, 7)}, "claude")
    logged = services.log_lifts(kg.id, {"sets": [bench(100)]}, "claude")
    assert logged["summary"] == (f"Bench Press 1 {TIMES} 5 at 100 kg on {said_day(kg)}. New best: Bench Press "
                                 "estimated max 116.7 kg, up from 113.8 kg. New best: Bench Press heaviest weight "
                                 "100 kg, up from 97.5 kg.")
    assert "units_note" not in logged


# ---------------------------------------------------------------- body weight (R14)

def test_185_lb_is_kept_as_83_91_and_the_summary_says_lb(lb):
    logged = services.log_bodyweight(lb.id, {"lb": 185}, "claude")
    assert logged["weigh_in"]["kg"] == 83.91
    assert logged["summary"] == f"Weigh-in 185 lb on {said_day(lb)}."
    again = services.log_bodyweight(lb.id, {"lb": 185}, "claude")
    assert again["duplicate"] and again["summary"] == f"Already logged: 185 lb on {said_day(lb)}. Nothing new was written."
    replaced = services.log_bodyweight(lb.id, {"lb": 184}, "claude")
    assert replaced["summary"] == f"Weigh-in 184 lb on {said_day(lb)}. It replaces 185 lb, kept, not erased."
    assert "of 185 lb" in replaced["note"]


def test_the_weigh_in_summary_carries_the_trends_7_day_average(lb):
    services.log_bodyweight(lb.id, {"kg": 85, "date": ago(lb, 30)}, "claude")
    logged = services.log_bodyweight(lb.id, {"lb": 185}, "claude")
    assert logged["trend"]["method"] == "7-day average"
    assert logged["summary"] == f"Weigh-in 185 lb on {said_day(lb)}. 7-day average 185 lb."


def test_a_weigh_in_with_both_or_neither_is_refused_and_a_lb_range_says_lb(lb):
    both = services.log_bodyweight(lb.id, {"kg": 80, "lb": 176}, "claude")
    assert (both["error"], both["message"], both["fix"]) == (
        "invalid_field", "Both kg and lb were sent: send one.", "Send the weight in the unit the person said: kg or lb.")
    neither = services.log_bodyweight(lb.id, {}, "claude")
    assert (neither["error"], neither["message"], neither["fix"]) == (
        "missing_field", "kg or lb is missing.", "Give the body weight in kg, 20 to 400, or in lb, 44.1 to 881.84.")
    out = services.log_bodyweight(lb.id, {"lb": 900}, "claude")
    assert (out["message"], out["fix"]) == ("lb 900 is outside 44.1 to 881.84 lb.", "Give lb from 44.1 to 881.84 lb.")
    kg = services.log_bodyweight(lb.id, {"kg": 500}, "claude")
    assert kg["fix"] == "Give the body weight in kg, 20 to 400."  # a kg number keeps its words
    with db.session_scope() as s:
        assert s.scalar(select(Bodyweight.id).where(Bodyweight.user_id == lb.id)) is None


# ---------------------------------------------------------------- cardio (R11)

def test_3_miles_is_kept_as_4_83_km_and_reads_3_mi_at_9_00_per_mi(lb):
    services.log_bodyweight(lb.id, {"lb": 185}, "claude")
    logged = services.log_cardio(lb.id, {"exercise": "Outdoor Run", "distance_mi": 3, "minutes": 27}, "claude")
    assert logged["entry"]["distance_km"] == 4.83 and logged["pace"]["min_per_km"] == "5:35"
    assert logged["summary"].startswith(f"Outdoor Run on {said_day(lb)} for 27:00, 3 mi, pace 9:00 per mi, about ")
    back = services.log_cardio(lb.id, {"exercise": "Outdoor Run", "distance_mi": 3, "minutes": 27,
                                       "date": ago(lb, 2)}, "claude")
    assert back["summary"].endswith(" estimated from body weight now, 185 lb.")
    km = services.log_cardio(lb.id, {"exercise": "Outdoor Run", "distance_km": 4.83, "minutes": 27,
                                     "allow_repeat": True}, "claude")
    assert km["summary"] == logged["summary"]  # the setting, not the field sent, decides the words


def test_a_speed_in_mph_is_kept_in_kmh_and_a_speed_refusal_on_a_miles_call_says_mph(lb):
    logged = services.log_cardio(lb.id, {"exercise": "Outdoor Run", "distance_mi": 3, "minutes": 30,
                                         "speed_mph": 6}, "claude")
    assert logged["entry"]["speed_kmh"] == 9.66
    wrong = services.log_cardio(lb.id, {"exercise": "Outdoor Run", "distance_mi": 3, "minutes": 30,
                                        "speed_mph": 9}, "claude")
    assert (wrong["error"], wrong["message"]) == (
        "invalid_field", "speed_mph 9 does not match 3 mi in 30 minutes, which is 6 mph.")
    assert "leave speed_mph out" in wrong["fix"]
    fast = services.log_cardio(lb.id, {"exercise": "Outdoor Run", "distance_mi": 40, "minutes": 30}, "claude")
    assert fast["message"] == "40 mi in 30 minutes is 80 mph, outside 0.1 to 37.2 mph."
    far = services.log_cardio(lb.id, {"exercise": "Outdoor Run", "distance_mi": 200, "minutes": 600}, "claude")
    assert (far["message"], far["fix"]) == ("distance_mi 200 mi is outside 0.01 to 186.41 mi.",
                                            "Give distance_mi from 0.01 to 186.41 mi.")
    quick = services.log_cardio(lb.id, {"exercise": "Outdoor Run", "minutes": 30, "speed_mph": 50}, "claude")
    assert (quick["message"], quick["fix"]) == ("speed_mph 50 mph is outside 0.1 to 37.2 mph.",
                                                "Give speed_mph from 0.1 to 37.2 mph.")
    both = services.log_cardio(lb.id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5,
                                       "distance_mi": 3}, "claude")
    assert both["message"] == "Both distance_km and distance_mi were sent: send one."


def test_a_km_call_keeps_its_speed_words(fresh):
    wrong = services.log_cardio(fresh.id, {"exercise": "Outdoor Run", "distance_km": 5, "minutes": 30,
                                           "speed_kmh": 15}, "claude")
    assert wrong["message"] == "speed_kmh 15 does not match 5 km in 30 minutes, which is 10 km/h."


# ---------------------------------------------------------------- edits, undo and the switch (R13, decision 1)

def test_an_edit_on_a_lb_account_names_the_weight_in_words_and_an_undo_puts_back_the_exact_kg(lb):
    weighed = services.log_bodyweight(lb.id, {"kg": 83.91}, "claude")
    edited = services.edit_entry(lb.id, "bodyweight", weighed["id"], {"lb": 184}, "claude")
    assert edited["changed"] == ["kg"] and edited["after"]["kg"] == 83.46
    assert edited["summary"] == "Weigh-in changed: weight from 185 lb to 184 lb."
    with db.session_scope() as s:
        before = json.loads(s.scalars(select(EntryVersion.before)).one())
    assert before["kg"] == 83.91
    undone = services.restore_entry(lb.id, "bodyweight", weighed["id"], "claude", undo_edit=True)
    assert undone["entry"]["kg"] == 83.91
    refused = services.edit_entry(lb.id, "bodyweight", weighed["id"], {"lb": 10}, "claude")
    assert (refused["message"], refused["fix"]) == ("lb 10 is outside 44.1 to 881.84 lb.",
                                                    "Give lb from 44.1 to 881.84 lb.")
    both = services.edit_entry(lb.id, "bodyweight", weighed["id"], {"kg": 80, "lb": 176}, "claude")
    assert both["message"] == "Both kg and lb were sent: send one."


def test_an_edit_on_a_mi_account_names_distance_and_speed_and_a_kg_account_keeps_the_fields_name(lb, fresh):
    run = services.log_cardio(lb.id, {"exercise": "Outdoor Run", "distance_mi": 3, "minutes": 30}, "claude")
    edited = services.edit_entry(lb.id, "cardio_entry", run["id"], {"distance_mi": 3.4}, "claude")
    assert edited["summary"].startswith("Cardio entry changed: distance from 3 mi to 3.4 mi. speed from 6 mph to "
                                        "6.8 mph, worked out from distance and minutes.")
    kg_run = services.log_cardio(fresh.id, {"exercise": "Outdoor Run", "distance_km": 5, "minutes": 30}, "claude")
    kg_edit = services.edit_entry(fresh.id, "cardio_entry", kg_run["id"], {"distance_mi": 3.4}, "claude")
    assert kg_edit["after"]["distance_km"] == 5.47
    assert kg_edit["summary"].startswith("Cardio entry changed: distance_km from 5 to 5.47.")
    sets = services.log_lifts(lb.id, {"sets": [bench(100)]}, "claude")
    changed = services.edit_entry(lb.id, "lift_session", sets["id"], {"sets": [bench(weight_lb=225)]}, "claude")
    assert changed["summary"] == (f"Lift session changed: sets from Bench Press 1 {TIMES} 5 at 220.45 lb to Bench "
                                  f"Press 1 {TIMES} 5 at 225 lb.")


def test_a_speed_that_no_longer_fits_an_edit_in_miles_is_said_in_mph(lb):
    run = services.log_cardio(lb.id, {"exercise": "Outdoor Run", "distance_km": 5, "minutes": 30,
                                      "speed_kmh": 11}, "claude")
    refused = services.edit_entry(lb.id, "cardio_entry", run["id"], {"distance_mi": 5}, "claude")
    assert refused["message"] == "5 mi in 30 minutes is 10 mph; the speed you gave, 6.8 mph, no longer fits."
    assert refused["fix"].startswith("Send speed_mph too, or speed_mph as null")


def test_switching_kg_to_lb_to_kg_rewrites_no_row(fresh):
    weighed = services.log_bodyweight(fresh.id, {"kg": 80}, "claude")
    services.edit_entry(fresh.id, "bodyweight", weighed["id"], {"kg": 81}, "claude")
    session = services.log_lifts(fresh.id, {"sets": [bench(100)]}, "claude")
    services.log_cardio(fresh.id, {"exercise": "Outdoor Run", "distance_km": 5, "minutes": 30}, "claude")

    def stored():
        with db.session_scope() as s:
            return ([(b.kg, b.updated_at) for b in s.scalars(select(Bodyweight))],
                    [(c.distance_km, c.speed_kmh, c.updated_at) for c in s.scalars(select(CardioEntry))],
                    stored_sets(session["id"]), [v.before for v in s.scalars(select(EntryVersion))])

    held = stored()
    for setting in ({"weight_unit": "lb", "distance_unit": "mi"}, {"weight_unit": "kg", "distance_unit": "km"}):
        assert "error" not in services.update_settings(fresh.id, setting, "claude")
        assert stored() == held


# ---------------------------------------------------------------- units_note (decision 12, R7)

def test_units_note_comes_on_a_lb_or_mi_write_while_unchosen_a_duplicate_included(fresh):
    weighed = services.log_bodyweight(fresh.id, {"lb": 185}, "claude")
    assert weighed["units_note"] == WEIGHT_NOTE
    assert weighed["summary"] == f"Weigh-in 83.91 kg on {said_day(fresh)}."  # the contract's first call, kg shown
    assert services.log_bodyweight(fresh.id, {"lb": 185}, "claude")["units_note"] == WEIGHT_NOTE  # the duplicate
    assert services.log_lifts(fresh.id, {"sets": [bench(weight_lb=225)]}, "claude")["units_note"] == WEIGHT_NOTE
    assert services.log_cardio(fresh.id, {"exercise": "Outdoor Run", "distance_mi": 3, "minutes": 27},
                               "claude")["units_note"] == DISTANCE_NOTE
    assert services.edit_entry(fresh.id, "bodyweight", weighed["id"], {"lb": 184}, "claude")["units_note"] == WEIGHT_NOTE
    goal = services.set_goal(fresh.id, {"kind": "bodyweight", "target_value": 175, "weight_unit": "LB",
                                        "target_date": (users.today_for(fresh) + timedelta(days=60)).isoformat()},
                             "claude")
    assert goal["units_note"] == WEIGHT_NOTE and goal["goal"]["target_value"] == 79.38
    routine = services.save_routine(fresh.id, {"name": "Push day", "lifts": [
        {"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_lb": 135}]}, "claude")
    assert routine["units_note"] == WEIGHT_NOTE
    both = services.edit_entry(fresh.id, "cardio_entry", services.get_log(fresh.id)["days"][0]["cardio"][0]["id"],
                               {"distance_mi": 3.1, "speed_mph": 7}, "claude")
    assert both["units_note"] == DISTANCE_NOTE
    assert common.units_note(one_user(fresh.id), True, True) == f"{WEIGHT_NOTE} {DISTANCE_NOTE}"


def test_units_note_never_comes_on_a_refusal_a_kg_write_an_import_or_once_either_answer_is_saved(fresh, make_user):
    assert "units_note" not in services.log_bodyweight(fresh.id, {"lb": 10}, "claude")
    assert "units_note" not in services.log_bodyweight(fresh.id, {"kg": 80}, "claude")
    imported = services.import_entries(fresh.id, {"rows": [{"kind": "bodyweight", "date": ago(fresh, 3),
                                                            "weight": 180}], "weight_unit": "lb", "dry_run": False},
                                       "claude")
    assert "error" not in imported and "units_note" not in imported
    services.update_settings(fresh.id, {"weight_unit": "kg"}, "claude")  # kg ends it as surely as lb
    assert "units_note" not in services.log_bodyweight(fresh.id, {"lb": 185}, "claude")
    miles = make_user(time_zone="Europe/London", distance_unit="mi")
    assert "units_note" not in services.log_cardio(miles.id, {"exercise": "Outdoor Run", "distance_mi": 3,
                                                              "minutes": 27}, "claude")
    assert services.log_bodyweight(miles.id, {"lb": 185}, "claude")["units_note"] == WEIGHT_NOTE  # each its own


# ---------------------------------------------------------------- whoami and update_settings (decisions 3, 13)

def test_whoami_gives_the_units_kg_and_km_while_null(fresh, lb):
    assert services.whoami(fresh.id)["units"] == {"weight": "kg", "distance": "km"}
    assert services.whoami(lb.id)["units"] == {"weight": "lb", "distance": "mi"}


def test_update_settings_sets_both_forgives_case_refuses_pounds_and_says_each_change(fresh):
    done = services.update_settings(fresh.id, {"weight_unit": " LB ", "distance_unit": "Mi"}, "claude")
    assert done["changed"] == ["weight_unit", "distance_unit"]
    assert done["summary"] == f"Weights now show in lb. Distances now show in miles. Today is {said_day(fresh)}."
    assert (one_user(fresh.id).weight_unit, one_user(fresh.id).distance_unit) == ("lb", "mi")
    back = services.update_settings(fresh.id, {"weight_unit": "kg", "distance_unit": "km"}, "claude")
    assert back["summary"].startswith("Weights now show in kg. Distances now show in km.")
    refused = services.update_settings(fresh.id, {"weight_unit": "pounds"}, "claude")
    assert (refused["error"], refused["message"], refused["fix"]) == (
        "invalid_unit", "weight_unit 'pounds' is not a unit Spotter takes.",
        "Use kg or lb for weight_unit, km or mi for distance_unit.")
    assert one_user(fresh.id).weight_unit == "kg"
    listed = services.update_settings(fresh.id, {"colour": "red"}, "claude")
    assert listed["fix"].endswith("add_back_exercise_kcal, weight_unit, distance_unit.")


@pytest.mark.parametrize("field", ["weight_unit", "distance_unit"])
def test_a_unit_sent_as_null_is_refused_as_blank_never_as_the_word_none(fresh, field):
    refused = services.update_settings(fresh.id, {field: None}, "claude")
    assert (refused["error"], refused["message"]) == ("invalid_unit", f"{field} '' is not a unit Spotter takes.")


# ---------------------------------------------------------------- goals (decision 5, R12)

def test_a_goal_in_lb_or_on_a_lb_account_is_kept_in_kg_and_its_label_reads_lb(lb, fresh):
    by = (users.today_for(lb) + timedelta(days=60)).isoformat()
    services.log_bodyweight(lb.id, {"lb": 185}, "claude")
    told = services.set_goal(lb.id, {"kind": "bodyweight", "target_value": 175, "weight_unit": "lb",
                                     "target_date": by}, "claude")
    assert told["goal"]["target_value"] == 79.38 and told["goal"]["unit"] == "kg"
    assert told["progress"]["label"] == "Body weight 175 lb"
    line = told["progress"]["plan_line"]
    assert line is None or ("lb" in line and "kg" not in line)
    services.log_lifts(lb.id, {"sets": [bench(weight_lb=225)]}, "claude")
    lift = services.set_goal(lb.id, {"kind": "lift", "exercise": "Bench Press", "target_value": 315,
                                     "target_date": by}, "claude")  # no weight_unit: the person's lb
    assert lift["goal"]["target_value"] == 142.88 and lift["progress"]["label"] == "Bench Press 315 lb"
    run = services.set_goal(lb.id, {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_mi": 3.1,
                                    "target_value": 28, "target_date": by, "start_value": 30}, "claude")
    assert run["goal"]["distance_km"] == 4.99 and run["progress"]["label"] == "Outdoor Run 3.1 mi in 28:00"
    waist = services.set_goal(lb.id, {"kind": "measurement", "measurement_kind": "waist", "target_value": 80,
                                      "start_value": 90, "target_date": by, "weight_unit": "lb"}, "claude")
    assert waist["goal"]["target_value"] == 80 and waist["progress"]["label"] == "Waist 80 cm"  # cm never converts
    summary = services.get_goals(lb.id)["summary"]
    assert "Body weight 175 lb by" in summary and "Bench Press 315 lb by" in summary
    kg_said = services.set_goal(fresh.id, {"kind": "bodyweight", "target_value": 175, "start_value": 185,
                                           "weight_unit": "lb", "target_date": by}, "claude")
    assert kg_said["goal"]["target_value"] == 79.38 and kg_said["progress"]["label"] == "Body weight 79.38 kg"


def test_a_goal_refusal_in_lb_says_lb_with_the_range_moved_inward(lb):
    by = (users.today_for(lb) + timedelta(days=60)).isoformat()
    lift = services.set_goal(lb.id, {"kind": "lift", "exercise": "Bench Press", "target_value": 1500,
                                     "start_value": 200, "target_date": by}, "claude")
    # inputs.md D13: a range with its low end left out says so (was "is outside 0 to" and "above 0 to").
    assert (lift["message"], lift["fix"]) == ("target_value 1500 lb must be more than 0 and at most 1,469.75 lb.",
                                              "Give target_value in lb, more than 0 and at most 1,469.75, for a lift "
                                              "goal.")
    body = services.set_goal(lb.id, {"kind": "bodyweight", "target_value": 900, "start_value": 185,
                                     "target_date": by}, "claude")
    assert body["fix"] == "Give target_value in lb, 44.1 to 881.84, for a bodyweight goal."
    far = services.set_goal(lb.id, {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_mi": 200,
                                    "target_value": 28, "target_date": by}, "claude")
    assert far["message"] == "distance_mi 200 mi is outside 0.07 to 186.41 mi." and far["field"] == "distance_mi"
    unit = services.set_goal(lb.id, {"kind": "bodyweight", "target_value": 175, "weight_unit": "pounds",
                                     "target_date": by}, "claude")
    assert (unit["error"], unit["message"], unit["field"]) == (
        "invalid_unit", "weight_unit 'pounds' is not a unit Spotter takes.", "weight_unit")
    none_yet = services.set_goal(lb.id, {"kind": "bodyweight", "target_value": 175, "target_date": by}, "claude")
    assert none_yet["fix"].endswith("today's body weight in lb.")
    with db.session_scope() as s:
        assert s.scalar(select(Goal.id).where(Goal.user_id == lb.id)) is None


def test_a_bare_goal_value_from_the_web_is_kg_and_from_claude_is_the_person_s_unit(lb):
    """The goal form sends no weight_unit and its labels say kg, until U2 (units.md, the note of 2026-11-16)."""
    by = (users.today_for(lb) + timedelta(days=60)).isoformat()
    web = services.set_goal(lb.id, {"kind": "bodyweight", "target_value": 80, "start_value": 90,
                                    "target_date": by}, "web")
    told = services.set_goal(lb.id, {"kind": "lift", "exercise": "Bench Press", "target_value": 80,
                                     "start_value": 70, "target_date": by}, "claude")
    named = services.set_goal(lb.id, {"kind": "lift", "exercise": "Back Squat", "target_value": 176.37,
                                      "start_value": 154.32, "weight_unit": "lb", "target_date": by}, "web")
    with db.session_scope() as s:
        kept = {g.id: (g.target_value, g.start_value) for g in s.scalars(select(Goal).where(Goal.user_id == lb.id))}
    assert kept == {web["id"]: (80, 90), told["id"]: (36.29, 31.75), named["id"]: (80, 70)}
    too_heavy = services.set_goal(lb.id, {"kind": "lift", "exercise": "Deadlift", "target_value": 700,
                                          "start_value": 100, "target_date": by}, "web")
    # A kg refusal, as the form says kg. inputs.md D13: the low end left out says so (was "is outside 0 to 666.67").
    assert too_heavy["message"] == "target_value 700 must be more than 0 and at most 666.67."


def test_a_waist_goal_sent_with_weight_unit_lb_carries_no_weight_units_note(fresh):
    by = (users.today_for(fresh) + timedelta(days=60)).isoformat()
    waist = services.set_goal(fresh.id, {"kind": "measurement", "measurement_kind": "waist", "target_value": 80,
                                         "start_value": 90, "target_date": by, "weight_unit": "lb"}, "claude")
    assert "error" not in waist and "units_note" not in waist
    edited = services.set_goal(fresh.id, {"goal_id": waist["id"], "target_value": 78, "weight_unit": "lb"}, "claude")
    assert "error" not in edited and "units_note" not in edited
    body = services.set_goal(fresh.id, {"kind": "bodyweight", "target_value": 175, "start_value": 185,
                                        "weight_unit": "lb", "target_date": by}, "claude")
    assert body["units_note"] == WEIGHT_NOTE


def test_a_plan_line_in_lb_converts_its_three_figures(lb):
    assert goals.plan_line(84.28, 83.91, -0.37, "behind", "bodyweight_derived", False, LB) == \
        "185.8 lb now against 185 lb on the plan line today: 0.8 lb behind."
    assert goals.plan_line(84.28, 83.91, -0.37, "behind", "bodyweight_derived", False) == \
        "84.3 kg now against 83.9 kg on the plan line today: 0.4 kg behind."


# ---------------------------------------------------------------- trends, progress and the log (R14)

def test_the_trends_summary_and_a_cardio_progress_say_lb_and_mi(lb):
    services.log_bodyweight(lb.id, {"kg": 84, "date": ago(lb, 20)}, "claude")
    services.log_bodyweight(lb.id, {"kg": 84}, "claude")
    trends = services.get_trends(lb.id)
    assert trends["summary"].startswith("Body weight 185.2 lb now, smoothed 185.2 lb")
    assert trends["bodyweight"]["now"] == 84.0  # the number field stays kg
    services.log_cardio(lb.id, {"exercise": "Outdoor Run", "distance_mi": 3, "minutes": 27}, "claude")
    run = services.get_progress(lb.id, "Outdoor Run")
    assert run["summary"].startswith(f"Outdoor Run: 1 session. Latest: Outdoor Run on {said_day(lb)} for 27:00, 3 mi, "
                                     "pace 9:00 per mi")
    assert services.get_log(lb.id)["days"][0]["summary"].startswith("Weigh-in 185.2 lb. Outdoor Run on ")


def test_the_trends_lb_rate_is_converted_from_the_unrounded_rate_and_the_kg_rate_reads_as_before(lb, fresh):
    for person in (lb, fresh):  # smoothed 80 and 79.89 over 20 days: -0.0385 kg a week, -0.04 kg at 2 dp
        services.log_bodyweight(person.id, {"kg": 80, "date": ago(person, 20)}, "claude")
        services.log_bodyweight(person.id, {"kg": 78.9}, "claude")
    assert ", -0.08 lb a week." in services.get_trends(lb.id)["summary"]  # -0.09 lb from the rounded -0.04 kg
    kg = services.get_trends(fresh.id)
    assert kg["bodyweight"]["rate_per_week"] == -0.04 and ", -0.04 kg a week." in kg["summary"]
    sessions, moved = {"this_week": 0, "target_per_week": None}, {"minutes_this_week": 0, "target_minutes": None}
    weight, raw = {"rate_per_week": -0.17}, {"now": 80, "smoothed": 80, "rate_per_week": -0.175}
    said = lambda units: trends._summary(weight, None, sessions, moved, None, "None.", raw, units)  # noqa: E731
    assert ", -0.39 lb a week." in said(LB) and ", -0.17 kg a week." in said(None)


LIFT_TODAY = date(2026, 3, 10)


@pytest.mark.parametrize("last", [100.01, 100.02, 100.04, 100.3, 99.5])
def test_progress_works_the_unrounded_slope_from_the_projection_s_own_points(last):
    pts = [(LIFT_TODAY - timedelta(days=21 - 7 * i), v) for i, v in enumerate([100, 100, 100, last])]
    pts.append((LIFT_TODAY - timedelta(days=60), 50))  # outside the 6 weeks: read by neither
    slope = progress._slope_per_week(pts, LIFT_TODAY)
    assert round(slope, 2) + 0.0 == metrics.projection(pts, LIFT_TODAY)["slope_per_week"]
    assert progress._slope_per_week(pts[:2], LIFT_TODAY) is None


def test_a_lift_s_lb_trend_is_converted_from_the_unrounded_slope_and_the_kg_trend_reads_as_before():
    """100, 100, 100 and 100.02 kg a week apart: 0.006 kg a week, 0.01 kg at 2 dp, 0.0132 lb (0.022 from 0.01)."""
    pts = [(LIFT_TODAY - timedelta(days=21 - 7 * i), v) for i, v in enumerate([100, 100, 100, 100.02])]
    history = {"points": {"Bench Press": pts}, "logged": {"Bench Press": 4}, "reps_only": {}, "best_set": {}}
    assert "Trend +0.01 lb a week over 6 weeks." in progress._lift_words("Bench Press", history, LIFT_TODAY, LB, True)
    assert "Trend +0.01 kg a week over 6 weeks." in progress._lift_words("Bench Press", history, LIFT_TODAY, None,
                                                                         True)


def test_a_lift_never_done_above_0_reads_its_most_reps_and_nothing_logged_reads_the_note(lb):
    assert services.get_progress(lb.id)["summary"] == "No lift sessions logged yet."
    services.log_lifts(lb.id, {"sets": [{"exercise": "Pull-up", "weight_kg": 0, "reps": 12}]}, "claude")
    assert services.get_progress(lb.id)["summary"] == "Pull-Up: most reps 12, 1 session."


# ---------------------------------------------------------------- routines (R10)

def test_a_routine_in_lb_is_kept_in_kg_saved_again_in_kg_changes_nothing_and_reads_lb(lb):
    saved = services.save_routine(lb.id, {"name": "Push day", "lifts": [
        {"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_lb": 135},
        {"exercise": "Dip", "sets": 3, "reps": 10}]}, "claude")
    assert saved["summary"] == f"Saved Push day: Bench Press 3 {TIMES} 8 at 135 lb and Dip 3 {TIMES} 10."
    with db.session_scope() as s:
        row = s.scalars(select(Routine)).one()
        assert json.loads(row.lifts)[0] == {"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 61.23}
    again = services.save_routine(lb.id, {"name": "Push day", "lifts": [
        {"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 61.23},
        {"exercise": "Dip", "sets": 3, "reps": 10}]}, "claude")
    assert again["summary"] == "Push day is already saved as given: nothing changed."
    [routine] = services.get_routines(lb.id)["routines"]
    assert routine["lifts"][0]["weight_kg"] == 61.23
    assert routine["words"] == f"Bench Press 3 {TIMES} 8 at 135 lb and Dip 3 {TIMES} 10"
    both = services.save_routine(lb.id, {"name": "Pull day", "lifts": [
        {"exercise": "Barbell Row", "sets": 3, "reps": 8, "weight_kg": 60, "weight_lb": 135}]}, "claude")
    assert both["message"] == "Lift 1 has both weight_kg and weight_lb: send one."
    heavy = services.save_routine(lb.id, {"name": "Pull day", "lifts": [
        {"exercise": "Barbell Row", "sets": 3, "reps": 8, "weight_lb": 2000}]}, "claude")
    assert (heavy["message"], heavy["fix"]) == ("Lift 1: weight_lb 2000 lb is outside 0 to 1,102.31 lb.",
                                                "Give weight_lb on lift 1 from 0 to 1,102.31 lb, or leave it out.")


# ---------------------------------------------------------------- delete_entry's summary (decision 11, R14)

def test_delete_entry_says_the_entry_in_the_person_s_units_on_the_preview_and_on_the_delete(lb):
    session = services.log_lifts(lb.id, {"sets": [bench(weight_lb=225)] * 3}, "claude")
    preview = services.delete_entry(lb.id, "lift_session", session["id"], "claude")
    assert preview["summary"] == f"Lift session on {said_day(lb)}: Bench Press 3 {TIMES} 5 at 225 lb."
    done = services.delete_entry(lb.id, "lift_session", session["id"], "claude", preview["confirm"])
    assert done["deleted"] and done["summary"] == preview["summary"]
    weighed = services.log_bodyweight(lb.id, {"lb": 185}, "claude")
    assert services.delete_entry(lb.id, "bodyweight", weighed["id"], "claude")["summary"] == \
        f"Weigh-in 185 lb on {said_day(lb)}."
    waist = services.log_measurement(lb.id, {"kind": "waist", "value": 84}, "claude")
    assert "summary" not in services.delete_entry(lb.id, "measurement", waist["id"], "claude")
    routine = services.save_routine(lb.id, {"name": "Push day", "lifts": [
        {"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_lb": 135}]}, "claude")
    assert services.delete_entry(lb.id, "routine", routine["id"], "web")["summary"] == \
        f"Push day: Bench Press 3 {TIMES} 8 at 135 lb."


# ---------------------------------------------------------------- the export (decision 17)

def test_the_export_s_numbers_are_the_same_on_both_accounts_and_account_csv_names_the_units(lb, fresh):
    for person in (lb, fresh):
        services.log_bodyweight(person.id, {"kg": 83.91}, "web")
        services.log_lifts(person.id, {"sets": [bench(102.06)]}, "web")
    files = {p.id: services.export_data(p.id)["files"] for p in (lb, fresh)}

    def table(text):
        return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))

    for name in ("bodyweight.csv", "lift_sets.csv"):
        strip = ("id", "session_id", "created_at", "updated_at")
        rows = {k: [{c: v for c, v in r.items() if c not in strip} for r in table(f[name])] for k, f in files.items()}
        assert rows[lb.id] == rows[fresh.id], name
    assert table(files[lb.id]["account.csv"])[0]["weight_unit"] == "lb"
    assert table(files[lb.id]["account.csv"])[0]["distance_unit"] == "mi"
    assert table(files[fresh.id]["account.csv"])[0]["weight_unit"] == ""
    # exercise-list.md R16: a lift set's distance_m is metres on every account.
    assert "Weights are in kg, distances in km and a lift set's distance_m in metres, in every file" in files[lb.id][
        "README.txt"]


# ---------------------------------------------------------------- through MCP: the schemas, the trace, the list

def test_through_mcp_a_lb_person_logs_and_the_trace_writes_the_names_sent(http):
    token = sign()
    who = ok(http, token, "whoami")
    assert who["units"] == {"weight": "kg", "distance": "km"}
    logged = ok(http, token, "log_lifts", {"sets": [{"exercise": "Bench Press", "weight_lb": 225, "reps": 5}]})
    assert logged["units_note"] == WEIGHT_NOTE and "weight_lb" in last().tried
    weighed = ok(http, token, "log_bodyweight", {"lb": 185})
    assert weighed["weigh_in"]["kg"] == 83.91 and last().tried.split() == ["lb"]
    ok(http, token, "log_cardio", {"exercise": "Outdoor Run", "minutes": 30, "distance_mi": 3, "speed_mph": 6})
    assert {"distance_mi", "speed_mph"} <= set(last().tried.split())
    refused = failed(http, token, "log_bodyweight", {"kg": 80, "lb": 176})
    assert refused["error"] == "invalid_field" and "units_note" not in refused
    assert {"lb", "distance_mi", "speed_mph"} <= calllog.KNOWN_NAMES and "weight_lb" in calllog.KNOWN_NAMES
    assert ok(http, token, "update_settings", {"weight_unit": "lb"})["changed"] == ["weight_unit"]
    assert ok(http, token, "whoami")["units"] == {"weight": "lb", "distance": "km"}


def test_of_the_published_list_exactly_the_thirteen_differ_and_the_instructions_are_4082(make_client):
    client = make_client(owner_user_id=OWNER)
    tools = tool_list(client, sign_for(OWNER))
    hashes = {t["name"]: hashlib.sha256(compact(t).encode()).hexdigest() for t in tools}
    # inputs.md moved every one after: with its changes taken out, exactly the thirteen differ from before units.
    held = {t["name"]: hashlib.sha256(compact(without_inputs(t)).encode()).hexdigest() for t in tools}
    # exercise-list.md's deploy 2 moved import_entries' names part and list_exercises (R8) too.
    # 2026-11-20: log_food's meal words lead with sending the meal the person named.
    assert {name for name, sha in BEFORE_UNITS.items() if held[name] != sha} == THIRTEEN | {"import_entries",
                                                                                         "list_exercises", "log_food"}
    assert {name: hashes[name] for name in PUBLISHED} == PUBLISHED
    # data-on-request.md: DATA_RULE; exercise-list.md C4c
    # 2026-11-20: the sleep routing sentence, the shorter Goals sentence, the meal sentence
    assert len(mcp_server.INSTRUCTIONS) == 4082
    who = next(t for t in tools if t["name"] == "whoami")
    assert who["description"].endswith(
        " If it carries setup, walk setup.missing as setup.how says. It carries units: send each number in the field "
        "of the unit the person said, and say weights and distances as Spotter's summaries give them.")
    for name in THIRTEEN - {"whoami", "update_settings", "get_goals", "get_progress", "get_log", "delete_entry",
                            "get_routines"}:
        out = next(t for t in tools if t["name"] == name)["outputSchema"]
        assert "units_note" in out["properties"] and "units_note" not in out.get("required", []), name


def test_every_new_result_field_is_optional():
    from app import mcp_schemas as m

    for model, name in ((m.WhoamiResult, "units"), (m.LogBodyweightResult, "summary"),
                        (m.GetProgressResult, "summary"), (m.LogDay, "summary"), (m.DeleteEntryResult, "summary"),
                        (m.RoutineToLog, "words"), (m.LogLiftsResult, "units_note"), (m.LogCardioResult, "units_note"),
                        (m.EditEntryResult, "units_note"), (m.SetGoalResult, "units_note"),
                        (m.SaveRoutineResult, "units_note"), (m.LogBodyweightResult, "units_note")):
        field = model.model_fields[name]
        assert not field.is_required() and field.default is None, (model.__name__, name)
    assert not m.SetIn.model_fields["weight_kg"].is_required() and not m.SetIn.model_fields["weight_lb"].is_required()


# ---------------------------------------------------------------- every new text keeps the text rules

def test_every_new_text_passes_its_surface(lb):
    services.log_bodyweight(lb.id, {"kg": 85, "date": ago(lb, 30)}, "claude")
    texts = [WEIGHT_NOTE, DISTANCE_NOTE]
    texts.append(services.log_bodyweight(lb.id, {"lb": 185}, "claude")["summary"])
    texts.append(services.log_bodyweight(lb.id, {"lb": 184.5}, "claude")["summary"])
    services.log_lifts(lb.id, {"sets": [bench(weight_lb=200)], "date": ago(lb, 7)}, "claude")
    texts.append(services.log_lifts(lb.id, {"sets": [bench(weight_lb=225)] * 2}, "claude")["summary"])
    texts.append(services.log_cardio(lb.id, {"exercise": "Outdoor Run", "distance_mi": 3, "minutes": 27},
                                     "claude")["summary"])
    texts += [d["summary"] for d in services.get_log(lb.id, days=8)["days"] if d["summary"]]
    texts += [services.get_progress(lb.id)["summary"], services.get_progress(lb.id, "Bench Press")["summary"],
              services.get_progress(lb.id, "Outdoor Run")["summary"], services.get_trends(lb.id)["summary"]]
    routine = services.save_routine(lb.id, {"name": "Push day", "lifts": [
        {"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_lb": 135}]}, "claude")
    texts += [routine["summary"], services.get_routines(lb.id)["routines"][0]["words"]]
    texts.append(services.delete_entry(lb.id, "routine", routine["id"], "claude")["summary"])
    texts.append(services.update_settings(lb.id, {"weight_unit": "kg", "distance_unit": "km"}, "claude")["summary"])
    for text in texts:
        found = [f for line in text.split("\n") for f in tr.check(line, tr.SUMMARY)]
        assert found == [], (text, found)
    for text in (common.WEIGHT_UNITS_NOTE, rules.both_sent("weight_kg", "weight_lb", "Set 2")["message"],
                 "Set 1: weight_lb 1200 lb is outside 0 to 1,102.31 lb."):
        assert tr.check(text, tr.MESSAGE) == [], text


def test_the_new_published_words_keep_the_text_rules(http):
    tools = {t["name"]: t for t in tool_list(http, sign())}
    words = [tools["whoami"]["description"], tools["whoami"]["outputSchema"]["properties"]["units"]["description"],
             tools["update_settings"]["description"], tools["get_routines"]["description"]]
    for name, fields in (("log_bodyweight", ("kg", "lb")), ("log_cardio", ("distance_mi", "speed_mph")),
                         ("set_goal", ("distance_mi", "weight_unit", "target_value", "start_value")),
                         ("update_settings", ("weight_unit", "distance_unit")),
                         ("edit_entry", ("lb", "distance_mi", "speed_mph"))):
        words += [tools[name]["inputSchema"]["properties"][f]["description"] for f in fields]
    for text in words:
        assert tr.check(text, tr.CLAUDE) == [], text


def test_can_and_cant_says_the_units_as_the_contract_words_them(http):
    doc = " ".join(read_resource(http, sign(), mcp_server.CAN_AND_CANT_URI)["text"].split())
    section = doc[doc.index("## Units"):doc.index("## Goals: four kinds")]
    for words in ("**Kept in kg and km.** Every weight is kept in kg and every distance in km, at 2 decimal places.",
                  "Switching changes no entry.", "Claude says those figures and never converts a number field.",
                  "Spotter converts: lb x 0.45359237, mi x 1.609344, kept at 2 decimal places.",
                  "A routine's weights from `get_routines` go in `weight_kg` as given.",
                  "comes back with `units_note`: Claude asks once, and saves either answer.",
                  "**Not offered:** stones, inches for a body size, a best mile"):
        assert words in section, words
    assert "the sets: exercise, weight, and reps, or a time, or a" in doc and "**Body weight:** one weigh-in a day." in doc
    assert "a weigh-in's weight or date" in doc and "| `update_settings` | Sets the person's time zone, units and" in doc


# ---------------------------------------------------------------- the migration (decision 18)

def test_0011_adds_the_two_columns_on_sqlite_and_comes_down_leaving_no_other_change():
    address = f"sqlite:///{(Path(tempfile.mkdtemp(dir=TEST_DIR)) / 'units.db').as_posix()}"
    cfg = alembic_config(address)
    command.upgrade(cfg, "0010")
    engine = make_engine(address)
    try:
        with engine.connect() as c:
            before = {t: inspect(c).get_columns(t) for t in inspect(c).get_table_names()}
        now = "2026-11-10 08:00:00.000000"
        with engine.begin() as c:
            c.exec_driver_sql("INSERT INTO user (id, provider, subject, claude_can, add_back_exercise_kcal, "
                              "created_at, updated_at, last_seen_at) VALUES (1, 'workos', 's1', 'log_delete', 0, ?, ?, ?)",
                              (now, now, now))
        command.upgrade(cfg, "0011")
        with engine.connect() as c:
            found = {col["name"]: col for col in inspect(c).get_columns("user")}
            for name in ("weight_unit", "distance_unit"):
                assert found[name]["nullable"] and found[name]["type"].length == 2 and found[name]["default"] is None
            assert list(c.exec_driver_sql("SELECT weight_unit, distance_unit FROM user")) == [(None, None)]
        command.downgrade(cfg, "0010")
        with engine.connect() as c:
            after = {t: inspect(c).get_columns(t) for t in inspect(c).get_table_names()}
            assert c.exec_driver_sql("SELECT count(*) FROM user").scalar() == 1  # no row lost
        assert {t: [str(x) for x in cols] for t, cols in after.items()} == \
            {t: [str(x) for x in cols] for t, cols in before.items()}
    finally:
        engine.dispose()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("0011").down_revision == "0010" and script.get_current_head() == "0014"  # exercise list


# ---------------------------------------------------------------- the six eval cases (decision 20)

@pytest.mark.parametrize("name, service, data", [
    ("lb_weigh_in", "log_bodyweight", {"lb": 185}),
    ("units_asked", "log_bodyweight", {"lb": 185}),
    ("lb_routine_changed", "log_lifts", {"sets": [{"exercise": "Bench Press", "weight_lb": 230, "reps": 8}] * 3
                                         + [{"exercise": "Overhead Press", "weight_kg": 61.23, "reps": 10}] * 3}),
    ("miles_run", "log_cardio", {"exercise": "Outdoor Run", "distance_mi": 3, "minutes": 27}),
])
def test_each_case_seeds_through_the_runner_and_its_rows_judge_as_designed(app_settings, name, service, data):
    """R20: the runner's own seeding, update_settings among it, as the paid run will do it."""
    case = next(c for c in judge.load_cases() if c["id"] == name)
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    user_id, _, seeded = server.new_person("Asia/Kolkata", case.get("seed", []))
    want = (case.get("seed") or [{}])[0].get("data", {})
    for field in ("weight_unit", "distance_unit"):
        assert getattr(one_user(user_id), field) == want.get(field)
    resolved = judge.resolve(case, {**dates_for("Asia/Kolkata"), **{f"seed.{k}": v for k, v in seeded.items()}})
    sent = [{"name": service, "arguments": data, "is_error": False}]
    reply = {"lb_weigh_in": "Logged 185 lb.", "units_asked": "Logged 185 lb. Show pounds everywhere?",
             "lb_routine_changed": "Logged your push day.", "miles_run": "Logged 3 mi at 9:00 a mile."}[name]
    assert judge.judge(resolved, sent, reply, person_rows(user_id))  # nothing logged yet: the db check fails
    assert "error" not in getattr(services, service)(user_id, data, "claude")
    assert judge.judge(resolved, sent, reply, person_rows(user_id)) == []


def test_the_six_cases_load_last_and_judge_both_ways(app_settings):
    cases = judge.load_cases()
    at = [c["id"] for c in cases].index("data_basketball")  # function-list.md's G1 cases follow the eight
    six = cases[at - 8:at - 2]  # inputs.md's two follow (its decision 19), then data-on-request.md's eight
    assert [c["id"] for c in six] == ["lb_weigh_in", "lb_lifts", "miles_run", "units_asked", "units_switch",
                                      "lb_routine_changed"]
    assert all(judge.case_problems(c) == [] for c in six) and "update_settings" in judge.SEED_SERVICES
    assert judge.seed_service("update_settings") is services.update_settings
    variables = {"today": "2026-11-15", "yesterday": "2026-11-14", "seed.earlier": 7, "seed.push": 9}
    weigh = judge.resolve(six[0], variables)
    good = [{"name": "log_bodyweight", "arguments": {"lb": 185.0}, "is_error": False}]
    rows = {"bodyweight": [{"id": 1, "kg": 83.91, "date": "2026-11-15", "deleted_at": None}]}
    assert judge.judge(weigh, good, "Logged 185 lb for today.", rows) == []
    assert judge.judge(weigh, good, "Logged 185 lb, which is 83.91 kg.", rows)  # says kg: fails
    sent_kg = [{"name": "log_bodyweight", "arguments": {"kg": 83.91}, "is_error": False}]
    assert judge.judge(weigh, sent_kg, "Logged 185 lb for today.", rows)
    lifts_case = judge.resolve(six[-1], variables)
    sets = [{"exercise": "Bench Press", "weight_lb": 230, "reps": 8}] * 3 + \
           [{"exercise": "Overhead Press", "weight_kg": 61.23, "reps": 10}] * 3
    session = {"lift_session": [{"id": 1, "set_count": 6, "date": "2026-11-15", "deleted_at": None}],
               "lift_set": [{"exercise": "Bench Press", "weight_kg": 104.33, "reps": 8, "deleted_at": None}] * 3
               + [{"exercise": "Overhead Press", "weight_kg": 61.23, "reps": 10, "deleted_at": None}] * 3}  # C4g
    assert judge.judge(lifts_case, [{"name": "log_lifts", "arguments": {"sets": sets}, "is_error": False}],
                       "Logged your push day.", session) == []
    converted = [{**s, "weight_lb": 102.06} if s["exercise"] == "Bench Press" else s for s in sets]
    assert judge.judge(lifts_case, [{"name": "log_lifts", "arguments": {"sets": converted}, "is_error": False}],
                       "Logged your push day.", session)
