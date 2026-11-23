"""Cardio and timed items in a routine (docs/contracts/routines-and-programs.md, package C5b, as its note of 2026-11-22
redraws it): a hold, a carry or a sled and a cardio activity saved beside lifts, in the order done, each kind in its
own stored shape, checked by its log function's own checks, read back with log_with and last time, logged by the
assistant's calls, and shown on the Routines page.

The exit check line by line: each kind saved, read, logged and read back as last; each refused shape with log's codes
and words, "Lift n" and "on lift n"; a cardio item's heart rate and machine calories kept, said in words and on the
page only, never a field to log; miles kept as km; a hold in lb; a cardio item's sets refused; 20 items with cardio
taken and 21 refused; 60 sets with cardio beside them taken; an unchanged save of every kind, in every form, writes
nothing; log_with for every kind; log_shape.convert_item, the one entry the conversions go through; the three eval
cases. The clock is tests/test_routines.py's own (db.utcnow replaced): no test reads the real date.
"""

import json
import re

import pytest
from sqlalchemy import text

from app import db, formatting, log_shape, mcp_schemas, mcp_server, rules, services
from app.exercises import BY_NAME
from app.mcp_schemas import GetRoutinesResult, SaveRoutineResult
from app.models import CardioEntry, Routine
from app.services import cardio, routines
from evals import judge

from . import text_rules as tr
from .conftest import sign
from .test_golden_cases import C5B_RUNS, player  # noqa: F401 - player is a fixture
from .test_idempotency import writer  # noqa: F401 - a fixture
from .test_mcp import failed, ok, tool_list
from .test_routines import (  # noqa: F401 - clock and user are fixtures
    GET_DESCRIPTION, NB, ON_ROUTINE, SAVE_DESCRIPTION, START, X, clock, day, given, read, routine_rows, save,
    statements_of, table, user,
)
from .test_web import client, only_user, seam, sign_in, web_client  # noqa: F401 - fixtures

LEGS = [{"exercise": "Rowing Machine", "minutes": 10}, {"exercise": "Back Squat", "sets": 4, "reps": 5},
        {"exercise": "Plank", "sets": 3, "time_s": 60},
        {"exercise": "Incline Treadmill Walk", "minutes": 20, "incline_pct": 12}]
LEGS_SAID = (f"Saved Legs day: Rowing Machine 10 min, Back Squat 4 {X} 5, Plank 3 {X} 60 s and Incline Treadmill Walk "
             "20 min at 12% incline.")  # the contract's "Said, call by call", word for word
CARDIO_NULLS = {"distance_km": None, "incline_pct": None, "speed_kmh": None, "avg_hr": None, "machine_kcal": None}
LEGS_STORED = [{"exercise": "Rowing Machine", "minutes": 10.0, **CARDIO_NULLS},
               {"exercise": "Back Squat", "sets": 4, "reps": 5, "weight_kg": None},
               {"exercise": "Plank", "sets": 3, "time_s": 60, "weight_kg": None},
               {"exercise": "Incline Treadmill Walk", "minutes": 20.0, **{**CARDIO_NULLS, "incline_pct": 12.0}}]
CARRY = {"exercise": "Farmer's Carry", "sets": 3, "distance_m": 40, "time_s": 45, "weight_kg": 32}
SHAPES = {"lift": ["exercise", "sets", "reps", "weight_kg"], "hold": ["exercise", "sets", "time_s", "weight_kg"],
          "carry": ["exercise", "sets", "distance_m", "time_s", "weight_kg"],
          "cardio": ["exercise", "minutes", "distance_km", "incline_pct", "speed_kmh", "avg_hr", "machine_kcal"]}


def stored(routine_id: int) -> str:
    with db.new_session() as s:
        return s.get(Routine, routine_id).lifts


def one(item: dict, person: int, name: str = "Day") -> dict:
    """A routine of one item beside a Dip, so every refusal reads Lift 2."""
    return services.save_routine(person, {"name": name, "lifts": [{"exercise": "Dip", "sets": 3, "reps": 10}, item]},
                                 "claude")


def refusals(answer: dict) -> list[tuple[str, str, str]]:
    assert "error" in answer, answer
    return [(e["error"], e["message"], e["fix"]) for e in answer["errors"]]


# ---------------------------------------------------------------- the kinds, stored and said (decisions 4 to 6, R6)

def test_each_kind_is_stored_in_its_own_shape_in_the_order_done_and_said_in_r6_s_words(user):
    made = save(user.id, "Legs day", LEGS)
    assert made["summary"] == LEGS_SAID
    assert stored(made["id"]) == json.dumps(LEGS_STORED, separators=(",", ":"))  # key order and nulls, byte for byte
    SaveRoutineResult.model_validate(made)
    # The answer gives the routine as saved; a cardio item's weight_kg is null, which its model needs.
    assert made["routine"]["lifts"] == [{**LEGS_STORED[0], "weight_kg": None}, *LEGS_STORED[1:3],
                                        {**LEGS_STORED[3], "weight_kg": None}]
    carried = save(user.id, "Carry day", [CARRY, {"exercise": "Sled Push", "sets": 2, "distance_m": 20}])
    assert carried["summary"] == (f"Saved Carry day: Farmer's Carry 3 {X} 40 m at 32 kg each in 45 s and Sled Push 2 {X} "
                                  "20 m.")
    rows = json.loads(stored(carried["id"]))
    assert rows == [{"exercise": "Farmer's Carry", "sets": 3, "distance_m": 40.0, "time_s": 45, "weight_kg": 32.0},
                    {"exercise": "Sled Push", "sets": 2, "distance_m": 20.0, "time_s": None, "weight_kg": None}]
    kinds = {"Back Squat": "lift", "Plank": "hold", "Farmer's Carry": "carry", "Rowing Machine": "cardio"}
    for row in [*json.loads(stored(made["id"])), *rows]:
        assert list(row) == SHAPES[routines.item_kind(row["exercise"])], row
    assert {name: routines.item_kind(name) for name in kinds} == kinds
    side = save(user.id, "Sides", [{"exercise": "Side Plank", "sets": 2, "time_s": 30, "weight_kg": 5},
                                   {"exercise": "Suitcase Carry", "sets": 2, "distance_m": 30, "weight_kg": 24}])
    assert side["summary"] == (f"Saved Sides: Side Plank 2 {X} 30 s a side at 5 kg and Suitcase Carry 2 {X} 30 m a side "
                               "at 24 kg.")


def test_a_lift_of_reps_is_stored_exactly_as_before_c5b(user):
    """Binding point 1: {exercise, sets, reps, weight_kg}, never a key more, whatever else the routine holds."""
    made = save(user.id, "Mixed", [{"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 60}, *LEGS])
    for row in json.loads(stored(made["id"])):
        if routines.item_kind(row["exercise"]) == "lift":
            assert list(row) == ["exercise", "sets", "reps", "weight_kg"], row
    assert stored(made["id"]).startswith('[{"exercise":"Bench Press","sets":3,"reps":8,"weight_kg":60.0},')


FORMS = {  # the same routine sent in each form log takes: every one stores the same
    "a hold's time": ([{"exercise": "Plank", "sets": 3, "time_s": 90}], [{"exercise": "Plank", "sets": 3, "minutes": 1.5}]),
    "a carry's distance": ([{**CARRY, "distance_m": 40}], [{**CARRY, "distance_m": None, "distance_km": 0.04}]),
    "a carry's time": ([{**CARRY, "time_s": 120}], [{**CARRY, "time_s": None, "minutes": 2}]),
    "a cardio time": ([{"exercise": "Rowing Machine", "minutes": 1.5}], [{"exercise": "Rowing Machine", "time_s": 90}]),
    "a cardio distance": ([{"exercise": "Outdoor Run", "minutes": 30, "distance_km": 2.5}],
                          [{"exercise": "Outdoor Run", "minutes": 30, "distance_m": 2500}]),
    "miles": ([{"exercise": "Outdoor Run", "minutes": 30, "distance_mi": 3.1, "speed_mph": 6.2}],
              [{"exercise": "Outdoor Run", "minutes": 30, "distance_km": 4.99, "speed_kmh": 9.98}]),
    "a heart rate": ([{"exercise": "Rowing Machine", "minutes": 10, "avg_hr": 140, "machine_kcal": 120}],
                     [{"exercise": "Rowing Machine", "time_s": 600, "avg_hr": 140, "machine_kcal": 120}]),
    "a hold in lb": ([{"exercise": "Plank", "sets": 3, "time_s": 60, "weight_lb": 22}],
                     [{"exercise": "Plank", "sets": 3, "time_s": 60, "weight_kg": 9.98}]),
}


@pytest.mark.parametrize("case", list(FORMS))
def test_an_unchanged_save_of_every_kind_in_every_form_writes_nothing(user, clock, case):
    """Each form is kept in the kind's one form before the store, so saving the same routine again, in either form,
    writes nothing: updated_at and saved_on stay, and no statement writes the routine."""
    first, other = FORMS[case]
    made = save(user.id, "Same", first)
    assert "error" not in made, made
    before = table()
    clock.at(3)
    for lifts_ in (first, [{k: v for k, v in x.items() if v is not None} for x in other]):
        again, seen = statements_of(lambda: save(user.id, "Same", lifts_))
        assert again["summary"] == "Same is already saved as given: nothing changed.", (case, again)
        assert table() == before and not [sql for sql, _ in seen if ON_ROUTINE.search(sql) and not
                                          sql.startswith("SELECT")], case


def test_each_form_is_kept_as_log_keeps_it(writer, clock):
    """The forms log takes, kept as log stores them (the note of 2026-11-22): a hold's minutes as whole seconds, a
    carry's km or miles as metres, a cardio time in seconds as minutes at 1 dp, metres and miles as km at 2 dp, mph
    as km/h at 2 dp, a weight in lb as kg at 2 dp. Each is the row the same numbers make through log."""
    http, token, account = writer
    sent = [{"exercise": "Plank", "sets": 2, "minutes": 1.5}, {"exercise": "Farmer's Carry", "sets": 2,
                                                                  "distance_mi": 0.03, "weight_lb": 70},
            {"exercise": "Treadmill Walk", "time_s": 61, "distance_m": 150},
            {"exercise": "Outdoor Run", "minutes": 25.25, "distance_mi": 3.1, "speed_mph": 7.4}]
    made = ok(http, token, "save_routine", {"name": "Forms", "lifts": sent})
    rows = json.loads(stored(made["id"]))
    assert rows[0] == {"exercise": "Plank", "sets": 2, "time_s": 90, "weight_kg": None}
    assert rows[1] == {"exercise": "Farmer's Carry", "sets": 2, "distance_m": 48.28, "time_s": None, "weight_kg": 31.75}
    assert rows[2] == {"exercise": "Treadmill Walk", "minutes": 1.0, **{**CARDIO_NULLS, "distance_km": 0.15}}
    assert rows[3] == {"exercise": "Outdoor Run", "minutes": 25.3, **{**CARDIO_NULLS, "distance_km": 4.99,
                                                                     "speed_kmh": 11.91}}
    ok(http, token, "log", {"kind": "lifts", "parts": [
        {"movement": "Plank", "minutes": 1.5, "weight_kg": 0, "exact": True},
        {"movement": "Farmer's Carry", "distance_mi": 0.03, "weight_lb": 70, "exact": True}]})
    for part, details in ((sent[2] | {"movement": "Treadmill Walk"}, {}),
                          ({"movement": "Outdoor Run", "minutes": 25.25, "distance_mi": 3.1}, {"speed_mph": 7.4})):
        part = {k: v for k, v in part.items() if k != "exercise"}
        logged = ok(http, token, "log", {"kind": "cardio", "parts": [part], **({"details": details} if details else {}),
                                         "allow_repeat": True})
        entry = logged["entry"]
        row = rows[2] if part["movement"] == "Treadmill Walk" else rows[3]
        assert (entry["minutes"], entry["distance_km"]) == (row["minutes"], row["distance_km"]), (entry, row)
        if details:
            assert entry["speed_kmh"] == row["speed_kmh"]
    with db.new_session() as s:
        sets = s.execute(text("SELECT time_s, distance_m, weight_kg FROM lift_set ORDER BY id")).all()
    assert [tuple(r) for r in sets] == [(90, None, 0.0), (None, 48.28, 31.75)]


# ---------------------------------------------------------------- the checks (R5)

MILES_FAST = "Lift 2: 150 mi in 1 minutes is 8,999.9 mph, outside 0.1 to 37.2 mph."  # log_cardio's own figure
REFUSED = {  # an item at lift 2: (code, message, fix), log's own words at the place
    "a hold with reps": ({"exercise": "Plank", "sets": 3, "reps": 60},
                         ("invalid_field", "Lift 2: Plank records time, not reps.", "Send time_s on lift 2.")),
    "a hold with no time": ({"exercise": "Plank", "sets": 3},
                            ("missing_field", "Lift 2: time_s is missing.", "Give time_s on lift 2.")),
    "a carry with no distance": ({"exercise": "Farmer's Carry", "sets": 3, "time_s": 40},
                                 ("missing_field", "Lift 2: distance_m is missing.", "Give distance_m on lift 2.")),
    "a hold of 4,000 seconds": ({"exercise": "Plank", "sets": 3, "time_s": 4000},
                                ("time_out_of_range", "Lift 2: time_s 4000 is outside 1 to 3,600.",
                                 "Give time_s on lift 2 from 1 to 3,600, in seconds.")),
    "a hold of 70 minutes": ({"exercise": "Plank", "sets": 3, "minutes": 70},
                             ("time_out_of_range", "Lift 2: minutes 70 is outside 1 to 3,600 seconds.",
                              "Give minutes on lift 2 for a time of 1 to 3,600 seconds.")),
    "a hold of part of a second": ({"exercise": "Plank", "sets": 3, "minutes": 0.505},
                                   ("invalid_field", "Lift 2: minutes 0.505 is not a whole number of seconds.",
                                    "Send the time as time_s on lift 2, in whole seconds.")),
    "a hold's time twice": ({"exercise": "Plank", "sets": 3, "minutes": 1, "time_s": 60},
                            ("invalid_field", "Lift 2 has time_s and minutes: send one.",
                             "Send the time once, in the field of the unit the person said: time_s or minutes.")),
    "a carry of 6 km": ({"exercise": "Farmer's Carry", "sets": 3, "distance_km": 6},
                        ("distance_out_of_range", "Lift 2: distance_km 6 is outside 1 to 5,000 metres.",
                         "Give distance_km on lift 2 for a distance of 1 to 5,000 metres.")),
    "a hold's minutes of words": ({"exercise": "Plank", "sets": 3, "minutes": "long"},
                                  ("invalid_field", "Lift 2: minutes must be a number, not 'long'.",
                                   "Give minutes on lift 2 as a number.")),
    "a hold's sets of 11": ({"exercise": "Plank", "sets": 11, "time_s": 60},
                            ("invalid_field", "Lift 2: sets 11 is outside 1 to 10.", "Give sets on lift 2 from 1 to 10.")),
    "a hold of 1,200 lb": ({"exercise": "Plank", "sets": 3, "time_s": 60, "weight_lb": 1200},
                           ("weight_out_of_range", "Lift 2: weight_lb 1200 lb is outside 0 to 1,102.31 lb.",
                            "Give weight_lb on lift 2 from 0 to 1,102.31 lb, or leave it out.")),
    "700 minutes of cardio": ({"exercise": "Rowing Machine", "minutes": 700},
                              ("invalid_field", "Lift 2: minutes 700 is outside 1 to 600.",
                               "Give minutes as a number from 1 to 600 on lift 2.")),
    "40,000 seconds of cardio": ({"exercise": "Rowing Machine", "time_s": 40000},
                                 ("invalid_field", "Lift 2: time_s 40000 is outside 60 to 36,000 seconds.",
                                  "Give time_s as a number from 60 to 36,000 seconds on lift 2.")),
    "cardio with no minutes": ({"exercise": "Rowing Machine"},
                               ("missing_field", "Lift 2: minutes is missing.",
                                "Give minutes as a number from 1 to 600 on lift 2.")),
    "200 miles": ({"exercise": "Outdoor Run", "minutes": 30, "distance_mi": 200},
                  ("invalid_field", "Lift 2: distance_mi 200 mi is outside 0.01 to 186.41 mi.",
                   "Give distance_mi on lift 2 from 0.01 to 186.41 mi.")),
    "km and miles": ({"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5, "distance_mi": 3},
                     ("invalid_field", "Lift 2 has both distance_km and distance_mi: send one.",
                      "Send the distance in the unit the person said: distance_km or distance_mi.")),
    "metres and miles": ({"exercise": "Outdoor Run", "minutes": 30, "distance_m": 5000, "distance_mi": 3},
                         ("invalid_field", "Lift 2 has distance_m and distance_mi: send one.",
                          "Send the distance once, in the field of the unit the person said: distance_m, distance_km "
                          "or distance_mi.")),
    "a heart rate of 300": ({"exercise": "Rowing Machine", "minutes": 10, "avg_hr": 300},
                            ("invalid_field", "Lift 2: avg_hr 300 is outside 30 to 240.",
                             "Give avg_hr as a whole number from 30 to 240 on lift 2.")),
    "machine calories of 0": ({"exercise": "Rowing Machine", "minutes": 10, "machine_kcal": 0},
                              ("invalid_field", "Lift 2: machine_kcal 0 is outside 1 to 5,000.",
                               "Give machine_kcal as a whole number from 1 to 5,000 on lift 2.")),
    "an incline of 50": ({"exercise": "Treadmill Walk", "minutes": 10, "incline_pct": 50},
                         ("invalid_field", "Lift 2: incline_pct 50 is outside 0 to 40.",
                          "Give incline_pct as a number from 0 to 40 on lift 2.")),
    "a speed that does not match": ({"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5, "speed_kmh": 20},
                                    ("invalid_field",
                                     "Lift 2: speed_kmh 20 does not match 5 km in 30 minutes, which is 10 km/h.",
                                     "Check the distance, the minutes and the speed on lift 2, or leave speed_kmh out "
                                     "and the app works it out.")),
    "a speed in mph that does not match": ({"exercise": "Outdoor Run", "minutes": 30, "distance_mi": 3, "speed_mph": 12},
                                           ("invalid_field",
                                            "Lift 2: speed_mph 12 does not match 3 mi in 30 minutes, which is 6 mph.",
                                            "Check the distance, the minutes and the speed on lift 2, or leave "
                                            "speed_mph out and the app works it out.")),
    "a worked-out speed past 60 km/h": ({"exercise": "Outdoor Run", "minutes": 1, "distance_km": 300},
                                        ("invalid_field", "Lift 2: 300 km in 1 minutes is 18,000 km/h, outside 0.1 "
                                         "to 60.", "Check the distance and the minutes on lift 2.")),
    "a worked-out speed past 37.2 mph": ({"exercise": "Outdoor Run", "minutes": 1, "distance_mi": 150},
                                         ("invalid_field", MILES_FAST, "Check the distance and the minutes on lift "
                                          "2.")),
    "50 mph": ({"exercise": "Outdoor Run", "minutes": 30, "speed_mph": 50},
               ("invalid_field", "Lift 2: speed_mph 50 mph is outside 0.1 to 37.2 mph.",
                "Give speed_mph on lift 2 from 0.1 to 37.2 mph.")),
    "a cardio item's sets": ({"exercise": "Rowing Machine", "minutes": 10, "sets": 3},
                             ("invalid_field", "Lift 2: Rowing Machine is cardio: it takes minutes, not sets.",
                              "Send minutes on lift 2, with distance, incline or speed when the person gave them.")),
    "a cardio item's weight": ({"exercise": "Rowing Machine", "minutes": 10, "weight_lb": 20},
                               ("invalid_field", "Lift 2: Rowing Machine is cardio: it takes minutes, not weight_lb.",
                                "Send minutes on lift 2, with distance, incline or speed when the person gave them.")),
    "a lift's heart rate": ({"exercise": "Back Squat", "sets": 3, "reps": 5, "avg_hr": 140},
                            ("invalid_field", "Lift 2: Back Squat is a lift: it takes sets and reps, not avg_hr.",
                             "Leave avg_hr out on lift 2.")),
    "a hold's incline": ({"exercise": "Plank", "sets": 3, "time_s": 60, "incline_pct": 5},
                         ("invalid_field", "Lift 2: Plank is a lift: it takes sets and time, not incline_pct.",
                          "Leave incline_pct out on lift 2.")),
    "a carry's heart rate": ({**CARRY, "avg_hr": 140},  # the fix round's N1: a list, never "and ... and"
                             ("invalid_field", "Lift 2: Farmer's Carry is a lift: it takes sets, distance and time, "
                              "not avg_hr.", "Leave avg_hr out on lift 2.")),
    "a lift's time": ({"exercise": "Back Squat", "sets": 3, "reps": 5, "time_s": 30},
                      ("invalid_field", "Lift 2: Back Squat records reps, not time.", "Send reps on lift 2.")),
    "a lift's minutes": ({"exercise": "Back Squat", "sets": 3, "reps": 5, "minutes": 0.5},
                         ("invalid_field", "Lift 2: Back Squat records reps, not time.", "Send reps on lift 2.")),
    "a lift's distance": ({"exercise": "Back Squat", "sets": 3, "reps": 5, "distance_km": 1},
                          ("invalid_field", "Lift 2: Back Squat records reps, not distance.", "Send reps on lift 2.")),
}


@pytest.mark.parametrize("case", list(REFUSED))
def test_each_shape_is_refused_by_its_log_function_s_own_check_at_lift_n_and_nothing_is_written(user, case):
    item, want = REFUSED[case]
    before = table()
    answer = one(item, user.id)
    assert refusals(answer) == [want] and table() == before
    for text in want[1:]:
        assert tr.check(text, tr.CLAUDE) == [], text


def test_a_cardio_refusal_is_rules_own_given_the_place_and_a_hold_s_is_log_lifts_at_lift_n(user):
    """R5: the checks' own refusals, worded after the check: what rules.bounded and rules.call_unit say given "Lift 2",
    and rules._set_numbers' with "on lift 2" for "on set 2"."""
    for field, value in (("minutes", 700), ("incline_pct", 41), ("speed_kmh", 61), ("avg_hr", 29),
                         ("machine_kcal", 5001), ("distance_km", 301), ("minutes", "ten")):
        item = {"exercise": "Treadmill Run", "minutes": 30, field: value}
        _, err = rules.bounded(field, value, "Lift 2")
        assert refusals(one(item, user.id)) == [(err["error"], err["message"], err["fix"])], field
    for item in ({"exercise": "Outdoor Run", "minutes": 1, "distance_km": 300},  # a worked-out speed: with_speed's
                 {"exercise": "Outdoor Run", "minutes": 1, "distance_mi": 150},
                 {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5, "speed_kmh": 20}):
        own = services.log_cardio(user.id, item, "claude")
        assert [m for _, m, _ in refusals(one(item, user.id))] == [f"Lift 2: {own['message']}"], item
    numbers, errors = rules._set_numbers({"time_s": 0}, 2, "Lift 2", "Plank")
    [err] = errors
    assert refusals(one({"exercise": "Plank", "sets": 3, "time_s": 0}, user.id)) == [
        (err["error"], err["message"], err["fix"].replace("on set 2", "on lift 2"))]


def test_every_failure_comes_back_at_once_in_item_order(user):
    sent = [{"exercise": "Plank", "sets": 3, "reps": 60}, {"exercise": "Rowing Machine", "minutes": 700, "sets": 2},
            {"exercise": "Back Squat", "sets": 3, "reps": 5, "avg_hr": 140}]
    answer = services.save_routine(user.id, {"name": "All", "lifts": sent}, "claude")
    assert [e["message"] for e in answer["errors"]] == [
        "Lift 1: Plank records time, not reps.", "Lift 2: Rowing Machine is cardio: it takes minutes, not sets.",
        "Lift 2: minutes 700 is outside 1 to 600.", "Lift 3: Back Squat is a lift: it takes sets and reps, not avg_hr."]
    assert answer["message"].startswith("4 problems. Lift 1: Plank records time, not reps.") and table() == []


# ---------------------------------------------------------------- the limits (decision 10)

def test_20_items_with_cardio_are_taken_21_refused_and_60_sets_beside_cardio_are_taken(user):
    lifts_ = [{"exercise": "Back Squat", "sets": 10, "reps": 5}] * 6  # 60 sets
    cardio_ = [{"exercise": "Rowing Machine", "minutes": 5}] * 14
    assert save(user.id, "Twenty", lifts_ + cardio_)["created"] is True  # 20 items, 60 sets: cardio counts no set
    refused = save(user.id, "Twenty one", lifts_ + cardio_ + [{"exercise": "Outdoor Walk", "minutes": 5}])
    assert refusals(refused) == [("invalid_field", "A routine holds 1 to 20 lifts, not 21.", "Send 1 to 20 lifts.")]
    over = save(user.id, "Over", lifts_ + [{"exercise": "Plank", "sets": 1, "time_s": 30}] + cardio_[:2])
    assert [e["error"] for e in over["errors"]] == ["sets_count_out_of_range"]  # a hold's sets count, cardio's none
    assert save(user.id, "All cardio", cardio_ + cardio_[:6])["created"] is True


# ---------------------------------------------------------------- names (the note of 2026-11-22)

def test_a_name_on_both_lists_is_the_lift():
    """No name on today's list reaches both, so a name on both is made here: the lifts are matched first."""
    assert routines._match("Bench Press", ["Bench Press"], ["Bench Press"], "Lift 1", None) == (
        "Bench Press", "lift", None)
    assert routines._match("rower", ["Bench Press"], ["Rowing Machine"], "Lift 1", None) == (
        "Rowing Machine", "cardio", None)


def test_an_item_s_keys_are_r7_s_in_this_order():
    assert routines.ITEM_KEYS == ("exercise", "log_with", "sets", "reps", "time_s", "distance_m", "weight_kg",
                                  "weight_from", "minutes", "distance_km", "incline_pct", "speed_kmh", "last_date",
                                  "last")


def test_a_name_is_matched_on_the_lifts_first_exactly_as_before_and_then_on_the_cardio_list(user):
    with db.new_session() as s:
        lifts_ = [e.name for e in services.common.exercises(s, "lift")]
        cardio_ = [e.name for e in services.common.exercises(s, "cardio")]
    for name in lifts_ + [a for row in BY_NAME.values() if row.kind == "lift" for a in getattr(row, "aliases", ())]:
        found, _ = rules.match_exercise(name, lifts_)
        assert routines._match(name, lifts_, cardio_, "Lift 1", None)[:2] == (found, routines.item_kind(found)), name
    assert one({"exercise": "rower", "minutes": 10}, user.id)["routine"]["lifts"][1]["exercise"] == "Rowing Machine"
    assert one({"exercise": "rowing machine", "minutes": 10}, user.id, "Two")["created"] is True
    plain = refusals(one({"exercise": "row", "sets": 3, "reps": 8}, user.id))  # a plain word of the lifts: as before
    assert plain == [("unknown_exercise", "Lift 2: 'row' names more than one lift.",
                      "Ask the person which: Barbell Row, Dumbbell Row, Seated Cable Row, Machine Row or Bent-Over "
                      "Dumbbell Row.")]
    # A miss on both: near names from both lists, as the item's fields point.
    as_cardio = one({"exercise": "rowng machin", "minutes": 10}, user.id)
    as_lift = one({"exercise": "rowng machin", "sets": 3, "reps": 5}, user.id)
    bare = one({"exercise": "rowng machin"}, user.id)
    lift_close = rules.unknown_exercise("rowng machin", lifts_)["closest"]
    cardio_close = rules.match_exercise("rowng machin", cardio_)[1]
    assert as_cardio["errors"][0]["closest"] == cardio_close + lift_close
    assert as_lift["errors"][0]["closest"] == lift_close  # a lift's fields: the lifts' alone, as before C5b
    assert bare["errors"][0]["closest"] == lift_close + cardio_close
    assert as_cardio["errors"][0]["fix"] == (f"Closest names: {', '.join(cardio_close + lift_close)}. Use a name "
                                             "exactly as list_exercises gives it.")
    assert [e["error"] for e in as_cardio["errors"]] == ["unknown_exercise"]  # checked as cardio: no sets asked
    assert cardio_close[0] == "Rowing Machine" and lift_close


# ---------------------------------------------------------------- the read (R3, R4, R7)

def test_get_routines_gives_each_kind_its_plan_and_log_with_and_never_a_plan_s_heart_rate(user):
    save(user.id, "Legs day", [{**LEGS[0], "avg_hr": 140, "machine_kcal": 120}, *LEGS[1:], CARRY])
    found = read(user.id)
    [routine] = found["routines"]
    assert routine["lifts"] == [
        given(exercise="Rowing Machine", log_with="cardio", minutes=10.0),
        given(exercise="Back Squat", sets=4, reps=5),
        given(exercise="Plank", sets=3, time_s=60),
        given(exercise="Incline Treadmill Walk", log_with="cardio", minutes=20.0, incline_pct=12.0),
        given(exercise="Farmer's Carry", sets=3, distance_m=40.0, time_s=45, weight_kg=32.0, weight_from="routine")]
    assert [x["log_with"] for x in routine["lifts"]] == ["cardio", "lifts", "lifts", "cardio", "lifts"]
    assert not any({"avg_hr", "machine_kcal"} & set(x) for x in routine["lifts"])  # binding point 2
    assert routine["words"] == (f"Rowing Machine 10 min, heart rate 140 bpm, the machine said 120 kcal, Back Squat 4 "
                                f"{X} 5, Plank 3 {X} 60 s, Incline Treadmill Walk 20 min at 12% incline and Farmer's "
                                f"Carry 3 {X} 40 m at 32 kg each in 45 s")
    assert json.loads(stored(routine["id"]))[0]["avg_hr"] == 140  # stored, said, never a field


def test_a_hold_s_and_a_carry_s_weight_to_log_and_last_follow_the_lift_s_rule(user, clock):
    save(user.id, "Core", [{"exercise": "Plank", "sets": 3, "time_s": 60, "weight_kg": 10}, CARRY,
                           {"exercise": "Dead Hang", "sets": 2, "time_s": 30}])
    services.log_lifts(user.id, {"date": day(-1), "sets": [
        {"exercise": "Plank", "weight_kg": 20, "time_s": 45, "exact": True},
        {"exercise": "Plank", "weight_kg": 15, "time_s": 50, "exact": True},
        {"exercise": "Dead Hang", "weight_kg": 0, "time_s": 40, "exact": True}]}, "claude")
    plank, carry, hang = read(user.id)["routines"][0]["lifts"]
    # A session dated before saved_on loses to the routine's own weight (decision 7), and counts where it has none.
    assert (plank["weight_kg"], plank["weight_from"], plank["last_date"]) == (10.0, "routine", day(-1))
    assert plank["last"] == {"date": day(-1), "words": "20 kg 45 s, 15 kg 50 s".replace("20 kg 45 s", "45 s at 20 kg")
                             .replace("15 kg 50 s", "50 s at 15 kg"),
                             "sets": [{"weight_kg": 20.0, "reps": None, "time_s": 45, "distance_m": None},
                                      {"weight_kg": 15.0, "reps": None, "time_s": 50, "distance_m": None}]}
    assert (carry["weight_kg"], carry["weight_from"], carry["last"]) == (32.0, "routine", None)
    assert (hang["weight_kg"], hang["weight_from"], hang["last"]["words"]) == (0.0, "last_session", "40 s")
    clock.at(1)
    services.log_lifts(user.id, {"sets": [{"exercise": "Plank", "weight_kg": 25, "time_s": 60, "exact": True}]},
                       "claude")
    assert read(user.id)["routines"][0]["lifts"][0]["weight_kg"] == 25.0  # a session after the save wins
    alone = save(user.id, "No weight", [{"exercise": "Plank", "sets": 3, "time_s": 60}])
    assert alone["routine"]["lifts"][0]["weight_kg"] is None  # decision 8: none given reads null, and the assistant asks


def cardio_log(person: int, **data) -> None:
    done = services.log_cardio(person, {"exercise": "Rowing Machine", "minutes": 10, **data}, "claude")
    assert "error" not in done, done


def test_cardio_s_last_is_the_activity_s_latest_live_entry_with_its_heart_rate_in_words_only(user, clock):
    save(user.id, "Row", [{"exercise": "Rowing Machine", "minutes": 10, "avg_hr": 140}])
    assert read(user.id)["routines"][0]["lifts"][0]["last"] is None
    cardio_log(user.id, minutes=12, date=day(-3))
    cardio_log(user.id, minutes=11, distance_km=2.2, avg_hr=150, machine_kcal=130, incline_pct=2, date=day(-1))
    [row] = read(user.id)["routines"][0]["lifts"]
    assert row["last"] == {"date": day(-1), "minutes": 11.0, "distance_km": 2.2, "incline_pct": 2.0, "speed_kmh": 12.0,
                           "words": "11 min at 2% incline, 2.2 km, pace 5:00 per km, heart rate 150 bpm, the machine "
                                    "said 130 kcal"}
    assert not {"avg_hr", "machine_kcal"} & set(row["last"]) and row["last_date"] is None
    cardio_log(user.id, minutes=9, date=day(-1))  # the same day, a later id: it is the latest
    assert read(user.id)["routines"][0]["lifts"][0]["last"]["minutes"] == 9.0
    with db.session_scope() as s:  # deleted: the one before counts
        entry = s.query(CardioEntry).order_by(CardioEntry.id.desc()).first()
        entry.deleted_at = db.utcnow()
    assert read(user.id)["routines"][0]["lifts"][0]["last"]["minutes"] == 11.0
    clock.at(2)
    cardio_log(user.id, minutes=30)  # logged two days on, then read on today: dated after today, the latest
    clock.at(0)
    assert read(user.id)["routines"][0]["lifts"][0]["last"]["date"] == day(2)
    services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 40}, "claude")  # another activity: not it
    assert read(user.id)["routines"][0]["lifts"][0]["last"]["minutes"] == 30.0
    GetRoutinesResult.model_validate(read(user.id))


def test_cardio_s_last_and_plan_are_said_in_the_person_s_units_and_its_numbers_stay_km(make_user, clock):
    person = make_user()
    assert "error" not in services.update_settings(person.id, {"distance_unit": "mi"}, "web")
    made = save(person.id, "Run", [{"exercise": "Outdoor Run", "minutes": 30, "distance_mi": 3, "speed_mph": 6}])
    assert made["summary"] == "Saved Run: Outdoor Run 30 min at 6 mph, 3 mi." and "units_note" not in made
    cardio_log(person.id, exercise="Outdoor Run", minutes=27, distance_mi=3)
    [row] = read(person.id)["routines"][0]["lifts"]
    assert (row["distance_km"], row["speed_kmh"], row["last"]["distance_km"]) == (4.83, 9.66, 4.83)
    assert row["last"]["words"] == "27 min, 3 mi, pace 9:00 per mi"
    fresh = make_user()
    noted = save(fresh.id, "Run", [{"exercise": "Outdoor Run", "minutes": 30, "distance_mi": 3}])
    assert noted["units_note"]  # a distance in miles before the person chose: asked once, as log_cardio asks


# ---------------------------------------------------------------- logged by the assistant's calls, read back as last

ASKED = {"Back Squat": 100, "Plank": 0}  # what the person answers when weight_kg is null


def test_the_assistant_logs_each_kind_with_log_as_log_with_says_and_reads_it_back_as_last(writer, clock):
    http, token, account = writer
    ok(http, token, "save_routine", {"name": "Legs day", "lifts": [*LEGS, CARRY]})
    got = ok(http, token, "get_routines")
    items = got["routines"][0]["lifts"]
    parts, cardio_calls = [], []
    for x in items:
        if x["log_with"] == "lifts":
            weight = x["weight_kg"] if x["weight_kg"] is not None else ASKED[x["exercise"]]  # null: asked
            numbers = {k: x[k] for k in ("reps", "time_s", "distance_m") if x[k] is not None}
            parts += [{"movement": x["exercise"], "weight_kg": weight, **numbers, "exact": True}] * x["sets"]
        else:
            details = {k: x[k] for k in ("incline_pct", "speed_kmh") if x[k] is not None}
            cardio_calls.append({"kind": "cardio", "parts": [{"movement": x["exercise"], "minutes": x["minutes"]}],
                                 **({"details": details} if details else {})})
    assert ok(http, token, "log", {"kind": "lifts", "parts": parts})["kind"] == "lift_session"
    for sent in cardio_calls:
        assert ok(http, token, "log", sent)["kind"] == "cardio_entry"
    back = ok(http, token, "get_routines")["routines"][0]["lifts"]
    today = START.date().isoformat()
    assert [x["last"]["date"] for x in back] == [today] * 5
    assert [x["last"]["words"] for x in back] == ["10 min", f"4 {X} 5 at 100 kg", f"3 {X} 60 s",
                                                  "20 min at 12% incline", f"3 {X} 40 m at 32 kg each in 45 s"]
    assert back[0]["last"]["minutes"] == 10.0 and back[3]["last"]["incline_pct"] == 12.0
    assert [x["weight_from"] for x in back] == [None, "last_session", "last_session", None, "last_session"]


# ---------------------------------------------------------------- the page (decision 14, R6)

def test_the_page_shows_each_kind_s_plan_and_last_time_with_a_plan_s_heart_rate(client, clock):
    sign_in(client)
    me = only_user().id
    save(me, "Legs day", [{**LEGS[0], "avg_hr": 140, "machine_kcal": 120}, *LEGS[1:], CARRY])
    cardio_log(me, minutes=11, distance_km=2.2, avg_hr=150, date=day(-1))
    services.log_lifts(me, {"date": day(-1), "sets": [{"exercise": "Plank", "weight_kg": 0, "time_s": 55,
                                                      "exact": True}]}, "claude")
    r = client.get("/routines")
    assert r.status_code == 200
    assert routine_rows(r.text.replace("&#39;", "'")) == [
        ("Rowing Machine", f"10{NB}min, heart rate 140{NB}bpm, the machine said 120{NB}kcal",
         "Mon 9 Nov: 11 min, 2.2 km, pace 5:00 per km, heart rate 150 bpm"),
        ("Back Squat", f"4 {X} 5", "Not logged yet."),
        ("Plank", f"3 {X} 60{NB}s at 0{NB}kg", "Mon 9 Nov: 55 s"),
        ("Incline Treadmill Walk", f"20{NB}min at 12% incline", "Not logged yet."),
        ("Farmer's Carry", f"3 {X} 40{NB}m at 32{NB}kg each in 45{NB}s", "Not logged yet.")]
    for row in routine_rows(r.text.replace("&#39;", "'")):
        for text in row:
            assert tr.check(text.replace(NB, " "), tr.PAGE) == [], text


# ---------------------------------------------------------------- the published words (the note of 2026-11-22)

def test_the_published_words_name_log_and_no_function_of_the_five_and_keep_the_text_rules(http):
    tools = {t["name"]: t for t in tool_list(http, sign())}
    saving, getting = tools["save_routine"], tools["get_routines"]
    assert (saving["description"], getting["description"]) == (SAVE_DESCRIPTION, GET_DESCRIPTION)
    texts = [saving["description"], getting["description"],
             *(v["description"] for v in saving["inputSchema"]["$defs"]["RoutineLiftIn"]["properties"].values()),
             *(v["description"] for v in getting["outputSchema"]["$defs"]["LiftToLog"]["properties"].values()),
             *(v["description"] for v in getting["outputSchema"]["$defs"]["LastTime"]["properties"].values()),
             *(v["description"] for v in saving["outputSchema"]["$defs"]["SavedRoutineLift"]["properties"].values())]
    five = re.compile(rf"\b(?:{'|'.join(log_shape.LOG_FUNCTIONS)})\b")
    for text in texts:
        assert not five.search(text), text  # binding point 7
        assert tr.check(text, tr.CLAUDE) == [], text
    props = saving["inputSchema"]["$defs"]["RoutineLiftIn"]["properties"]
    for field, words in (("time_s", mcp_schemas.PART_TIME_S_DESCRIPTION), ("avg_hr", mcp_schemas.CARDIO_AVG_HR_DESCRIPTION),
                         ("speed_mph", mcp_schemas.CARDIO_SPEED_MPH_DESCRIPTION)):
        assert props[field]["description"] == words  # imported, never copied
    log_with = getting["outputSchema"]["$defs"]["LiftToLog"]["properties"]["log_with"]["description"]
    assert log_with == "The kind to send with log for this item: lifts or cardio."
    for text in ("Lift 2: Rowing Machine is cardio: it takes minutes, not sets.",
                 "Send minutes on lift 2, with distance, incline or speed when the person gave them.", LEGS_SAID):
        assert tr.check(text, tr.SUMMARY) == [], text
    print(f"\nsave_routine description: {len(SAVE_DESCRIPTION)} characters; get_routines: {len(GET_DESCRIPTION)}")


def test_log_with_is_a_kind_word_log_takes_for_every_kind():
    for kind, word in routines.LOG_WITH.items():
        assert log_shape.resolve_kind(word) == (word, None) and word in log_shape.KINDS, kind
    assert routines.LOG_WITH == {"lift": "lifts", "hold": "lifts", "carry": "lifts", "cardio": "cardio"}


# ---------------------------------------------------------------- log_shape.convert_item (binding point 4)

def test_convert_item_names_the_place_keeps_log_s_rules_and_runs_no_check_on_a_refusal():
    ran = []

    def check(row):
        ran.append(row)
        return row, []
    assert log_shape.convert_item("lifts", {"minutes": 1.5, "reps": 3}, "Lift 4", check) == ({"reps": 3, "time_s": 90}, [])
    assert log_shape.convert_item("cardio", {"time_s": 90, "distance_m": 2500, "distance_mi": None}, "Lift 1",
                                  check)[0] == {"distance_mi": None, "minutes": 1.5, "distance_km": 2.5}
    for item, message in (({"minutes": 1, "time_s": 60}, "Lift 4 has time_s and minutes: send one."),
                          ({"minutes": 0.01}, "Lift 4: minutes 0.01 is not a whole number of seconds."),
                          ({"minutes": "x"}, "Lift 4: minutes must be a number, not 'x'."),
                          ({"minutes": 1e308}, "Lift 4: minutes 1e+308 is outside 1 to 3,600 seconds.")):
        ran.clear()
        values, problems = log_shape.convert_item("lifts", item, "Lift 4", check)
        assert values is None and [p["message"] for p in problems] == [message] and ran == []
        assert "field" not in problems[0]  # an item's refusal names no field, as save_routine's never do

    def ranged(row):
        return row, [rules.error("time_out_of_range", "Lift 4: time_s 4200 is outside 1 to 3,600.",
                                 "Give time_s on lift 4 from 1 to 3,600, in seconds.")]
    _, [said] = log_shape.convert_item("lifts", {"minutes": 70}, "Lift 4", ranged)
    assert (said["message"], said["fix"]) == ("Lift 4: minutes 70 is outside 1 to 3,600 seconds.",
                                              "Give minutes on lift 4 for a time of 1 to 3,600 seconds.")
    assert log_shape.out_of_range("cardio", "Lift 3", "time_s", 40000, "minutes") == (
        "Lift 3: time_s 40000 is outside 60 to 36,000 seconds.",
        "Give time_s as a number from 60 to 36,000 seconds on lift 3.")
    # log's own words for a part are as they were
    assert log_shape.out_of_range("lifts", 2, "minutes", 70, "time_s") == (
        "parts[2]: minutes 70 is outside 1 to 3,600 seconds.", "Give minutes on parts[2] for a time of 1 to 3,600 seconds.")


# ---------------------------------------------------------------- the three eval cases (the note of 2026-11-22)

C5B_CASES = ["routine_items_saved", "routine_items_logged", "routine_cardio_changed", "routine_resave_keeps_plan"]


def test_the_four_cases_follow_c5a_s_and_are_usable():
    cases = judge.load_cases()
    ids = [c["id"] for c in cases]
    at = ids.index("routine_same_as_last_time")  # C5a's two, then these four; macros-and-asking.md's 11 follow
    assert ids[at:at + 6] == ["routine_same_as_last_time", "routine_last_time_read", *C5B_CASES]
    for case in cases[at + 2:at + 6]:
        assert judge.case_problems(case) == [] and (case["kind"], case["core"]) == ("direct", False), case["id"]
    by_id = {c["id"]: c for c in cases}
    for case_id in C5B_CASES[1:3]:
        case = by_id[case_id]
        assert case["expect"]["ok_calls"] == {"log_lifts": 1, "log_cardio": 1}
        assert case["calls"]["must"] == ["log_lifts", "log_cardio"]  # the five's names, his ruling of 2026-11-22
        assert "log_cardio" not in case["calls"]["must_not"] + case["expect"]["no_call"]
        assert case["expect"]["function"] == "log_cardio"
        [seed] = case["seed"]
        plan = seed["data"]["lifts"]
        assert plan[0] == {"exercise": "Rowing Machine", "minutes": 10, "avg_hr": 140, "machine_kcal": 120}
        assert plan[2] == {"exercise": "Plank", "sets": 3, "time_s": 60, "weight_kg": 0}
    assert by_id["routine_items_logged"]["expect"]["inputs_forbid"] == {"avg_hr": 140, "machine_kcal": 120}
    assert set(by_id["routine_items_saved"]["calls"]["must_not"]) == set(common_writes()) - {"save_routine"}
    # The fix round's S3: either time form stores the same row; no heart rate, calories or speed of the model's own
    assert {"lifts.*.avg_hr", "lifts.*.machine_kcal", "lifts.*.speed_kmh"} <= set(
        by_id["routine_items_saved"]["expect"]["inputs_exclude"])
    # The fix round: a resave measures whether a plan's heart rate and calories, never fields of an item, are kept
    resave = by_id["routine_resave_keeps_plan"]
    assert resave["seed"][0]["data"]["lifts"][0] == {"exercise": "Rowing Machine", "minutes": 10, "avg_hr": 140,
                                                     "machine_kcal": 120}
    assert resave["expect"]["inputs_include"] == {"lifts": [
        {"exercise": "Rowing Machine", "avg_hr": 140, "machine_kcal": 120}, {"exercise": "Dip", "sets": 3, "reps": 10}]}
    assert set(resave["calls"]["must_not"]) == set(common_writes()) - {"save_routine"}


def common_writes() -> list[str]:
    return list(judge.WRITE_FUNCTIONS)


# Each of C5B_RUNS' failing runs (tests/test_golden_cases.py), and the reason it exists to fail for, in order.
FAILS_FOR = {
    "routine_items_saved": ["save_routine succeeded 0 times, expected 1 (1 refused)",  # a hold sent with reps
                            "save_routine sent lifts.*.weight_kg",  # a weight of its own
                            "log (cardio) was called and should not have been",  # a log call as well
                            "save_routine sent lifts.*.avg_hr"],  # a heart rate the person never gave
    "routine_resave_keeps_plan": ["no save_routine call sent"],  # the plan's heart rate and calories dropped
    "routine_items_logged": ["log (cardio) sent avg_hr = 140",  # the plan's heart rate, logged as today's
                             "log_cardio succeeded 0 times, expected 1",  # one log call where two are needed
                             "log_lifts succeeded 0 times, expected 1 (1 refused)"],  # a hold logged with reps
    "routine_cardio_changed": ["log (cardio) sent minutes = 10",  # minutes 10 when 15 was said
                               "no log_cardio call sent",  # the heart rate and calories said, lost
                               "log_lifts succeeded 0 times, expected 1"],  # one log call where two are needed
}


@pytest.mark.parametrize("case_id", C5B_CASES)
def test_each_failing_run_of_the_four_cases_fails_for_its_stated_reason(case_id, player):
    """Binding point 8: every failing RUNS entry is played as a run would be, on the golden set's fixed day (its clock
    seam), and fails for the reason it was written for; each passing one passes."""
    case = next(c for c in judge.load_cases() if c["id"] == case_id)
    for calls, reply in C5B_RUNS[case_id]["pass"]:
        assert player.verdict(case, player.person(case), calls, reply) == [], case_id
    assert len(C5B_RUNS[case_id]["fail"]) == len(FAILS_FOR[case_id])
    for (calls, reply), reason in zip(C5B_RUNS[case_id]["fail"], FAILS_FOR[case_id]):
        why = player.verdict(case, player.person(case), calls, reply)
        assert any(w.startswith(reason) for w in why), (case_id, reason, why)
    if case_id == "routine_items_logged":
        assert "log (cardio) sent machine_kcal = 120" in player.verdict(
            case, player.person(case), C5B_RUNS[case_id]["fail"][0][0], "Logged.")
    if case_id == "routine_cardio_changed":  # the rows catch the lost numbers too
        extra = player.verdict(case, player.person(case), C5B_RUNS[case_id]["fail"][1][0], "Logged.")
        assert any(w.startswith("cardio_entry") for w in extra), extra


# ---------------------------------------------------------------- the size of the answer (R14)

# get_routines' answer as sent, in UTF-8 bytes (mcp_server._json_text), with C5b's items: measured here on 2026-11-22
# and pinned, no cap. tests/test_routines.py measures R14's two again with C5b's fields: 6 routines of 8 lifts, and
# every limit with the lift whose words run longest, which stays the largest answer: a lift's last holds up to 60 sets.
MIXED_BYTES = 28229
CARDIO_LIMITS_BYTES = 238623  # the fix round (N4): with the speed, every number at its longest; 225,023 before


def answer_bytes(person_id: int) -> int:
    found = services.get_routines(person_id)
    GetRoutinesResult.model_validate(found)
    return mcp_server._json_text(found)[1]


def test_the_answer_s_size_with_cardio_and_timed_items_is_measured_and_pinned(make_user, clock):
    """Mixed: 6 routines of 8 items, a cardio warm-up with a heart rate and machine calories, 5 lifts of 4 sets, a
    plank of 3 sets and a cardio finisher with an incline, each logged once. Cardio at the limits: 20 routines of 64
    characters, each of 20 cardio items, every item the activity whose name runs longest, with every number it holds
    at its longest within its range, the speed too, and its latest entry the same."""
    with db.new_session() as s:
        names = [e.name for e in services.common.exercises(s, "lift")
                 if rules.measures_of(e.name) == ("weight", "reps") and not rules.takes_help(e.name)]
        activities = [e.name for e in services.common.exercises(s, "cardio")]
    mixed = make_user()
    for r in range(6):
        group = names[r * 5:(r + 1) * 5]
        save(mixed.id, f"Day {r + 1}", [{"exercise": "Rowing Machine", "minutes": 10, "avg_hr": 140, "machine_kcal": 120},
                                        *({"exercise": n, "sets": 4, "reps": 8, "weight_kg": 60} for n in group),
                                        {"exercise": "Plank", "sets": 3, "time_s": 60, "weight_kg": 0},
                                        {"exercise": "Incline Treadmill Walk", "minutes": 20, "incline_pct": 12}])
        done = services.log_lifts(mixed.id, {"date": day(-r - 1), "sets": [
            *({"exercise": n, "weight_kg": w, "reps": reps, "exact": True} for n in group
              for w, reps in ((62.5, 8), (62.5, 8), (60, 7), (57.5, 6))),
            *({"exercise": "Plank", "weight_kg": 0, "time_s": t, "exact": True} for t in (60, 55, 50))]}, "claude")
        assert "error" not in done, done
    cardio_log(mixed.id, minutes=11, distance_km=2.2, avg_hr=150, machine_kcal=130, date=day(-1))
    cardio_log(mixed.id, exercise="Incline Treadmill Walk", minutes=20, distance_km=2, incline_pct=12, avg_hr=128,
               machine_kcal=210, date=day(-1))
    longest = max(activities, key=len)
    # every field at its longest within its range, in the JSON and in the words: the speed within 20% of the distance
    # over the minutes, as log_cardio checks it
    top = {"minutes": 599.9, "distance_km": 299.99, "incline_pct": 39.94, "speed_kmh": 35.94, "avg_hr": 240,
           "machine_kcal": 5000}
    limits = make_user()
    for r in range(rules.ROUTINES_MAX):
        made = save(limits.id, f"{r:02d}" + "R" * (rules.ROUTINE_NAME_MAX - 2),
                    [{"exercise": longest, **top}] * rules.ROUTINE_LIFTS[1])
        assert "error" not in made, made
    cardio_log(limits.id, exercise=longest, **top, date=day(-1))
    sizes = (answer_bytes(mixed.id), answer_bytes(limits.id))
    print(f"\nget_routines answer: mixed {sizes[0]:,} bytes, cardio at the limits {sizes[1]:,} bytes ({longest})")
    assert sizes == (MIXED_BYTES, CARDIO_LIMITS_BYTES)
