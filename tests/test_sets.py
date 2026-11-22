"""Sets of time and distance, the reps limit of 100, minus weights and the one-off delete's neighbours, package C4b
(contract exercise-list.md): what a set takes by its lift (R6), word for word; a hold, a carry, a band set and a help
set logged, read, edited, undone and exported (R14, R15, R16); the duplicate key over time and distance; routines,
goals and imports, which take only lifts of reps (R10, R11); a version of four items from before 0013 put back; the
sets table on the pages; log_lifts' own set model through /mcp (R3, for now with an exact name). Every new message,
fix and summary keeps the text rules.

Every date is a fixed today through db.utcnow, never the real date.
"""

import csv
import io
import json
import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import db, rules, services, web
from app.formatting import KG_KM, Units
from app.mcp_schemas import LogSetIn, SetIn
from app.models import EntryVersion, Exercise, Goal, LiftSession, Routine, User
from app.services import common, lifts

from . import text_rules as tr
from .test_idempotency import writer  # noqa: F401 - a fixture
from .test_mcp import failed, ok, registered_tools, tool_list
from .test_web import client, seam, sign_in, web_client  # noqa: F401 - fixtures

NOW = datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)
DAY = "2026-11-09"


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


@pytest.fixture
def uid(make_user) -> int:
    return make_user(time_zone="UTC").id


def log(uid: int, *sets: dict, **data) -> dict:
    return services.log_lifts(uid, {"date": DAY, "sets": list(sets), "new_session": True, **data}, "claude")


def refusal(r: dict) -> list[tuple[str, str, str]]:
    assert "error" in r, r
    return [(e["error"], e["message"], e["fix"]) for e in r["errors"]]


def clean(*texts: str, surface: str = tr.MESSAGE) -> None:
    for text in texts:
        assert tr.check(text, surface) == [], text


# ---------------------------------------------------------------- R6: what a set takes, word for word

R6_REFUSALS = {
    "a hold sent reps": ({"exercise": "Plank", "weight_kg": 0, "reps": 30},
                         ("invalid_field", "Set 1: Plank records time, not reps.", "Send time_s on set 1.")),
    "a hold sent nothing": ({"exercise": "Plank", "weight_kg": 0},
                            ("missing_field", "Set 1: time_s is missing.", "Give time_s on set 1.")),
    "a hold sent a distance": ({"exercise": "Dead Hang", "weight_kg": 0, "time_s": 30, "distance_m": 5},
                               ("invalid_field", "Set 1: Dead Hang records time, not distance.",
                                "Send time_s on set 1.")),
    "a carry sent reps": ({"exercise": "Farmer's Carry", "weight_kg": 32, "reps": 2},
                          ("invalid_field", "Set 1: Farmer's Carry records distance and time, not reps.",
                           "Send distance_m on set 1.")),
    "a carry sent only a time": ({"exercise": "Sled Push", "weight_kg": 100, "time_s": 30},
                                 ("missing_field", "Set 1: distance_m is missing.", "Give distance_m on set 1.")),
    "a lift of reps sent a time": ({"exercise": "Bench Press", "weight_kg": 60, "time_s": 30},
                                   ("invalid_field", "Set 1: Bench Press records reps, not time.",
                                    "Send reps on set 1.")),
    "a lift of reps sent no reps": ({"exercise": "Bench Press", "weight_kg": 60},
                                    ("missing_field", "Set 1: reps is missing.", "Give reps on set 1.")),
    "a band sent a time": ({"exercise": "Band Pull-Apart", "time_s": 30},
                           ("invalid_field", "Set 1: Band Pull-Apart records reps, not time.", "Send reps on set 1.")),
    "a band sent a weight": ({"exercise": "Band Pull-Apart", "weight_kg": 5, "reps": 15},
                             ("invalid_field", "Set 1: Band Pull-Apart records reps, not weight.",
                              "Leave the weight out on set 1: a band set stores 0 kg.")),
    "a band sent a minus weight": ({"exercise": "Band Pull-Apart", "weight_kg": -5, "reps": 15},
                                   ("invalid_field", "Set 1: Band Pull-Apart records reps, not weight.",
                                    "Leave the weight out on set 1: a band set stores 0 kg.")),
    "reps 101": ({"exercise": "Wall Ball", "weight_kg": 6, "reps": 101},
                 ("reps_out_of_range", "Set 1: reps 101 is outside 1 to 100.", "Give reps on set 1 from 1 to 100.")),
    "reps 0": ({"exercise": "Wall Ball", "weight_kg": 6, "reps": 0},
               ("reps_out_of_range", "Set 1: reps 0 is outside 1 to 100.", "Give reps on set 1 from 1 to 100.")),
    "time_s 0": ({"exercise": "Plank", "weight_kg": 0, "time_s": 0},
                 ("time_out_of_range", "Set 1: time_s 0 is outside 1 to 3,600.",
                  "Give time_s on set 1 from 1 to 3,600, in seconds.")),
    "time_s 3601": ({"exercise": "Plank", "weight_kg": 0, "time_s": 3601},
                    ("time_out_of_range", "Set 1: time_s 3601 is outside 1 to 3,600.",
                     "Give time_s on set 1 from 1 to 3,600, in seconds.")),
    "time_s of a fraction": ({"exercise": "Plank", "weight_kg": 0, "time_s": 60.5},
                             ("not_a_whole_number", "Set 1: time_s must be a whole number.",
                              "Give time_s on set 1 as a whole number.")),
    "distance_m 0.99": ({"exercise": "Farmer's Carry", "weight_kg": 32, "distance_m": 0.99},
                        ("distance_out_of_range", "Set 1: distance_m 0.99 is outside 1 to 5,000.",
                         "Give distance_m on set 1 from 1 to 5,000, in metres.")),
    "distance_m 5000.01": ({"exercise": "Farmer's Carry", "weight_kg": 32, "distance_m": 5000.01},
                           ("distance_out_of_range", "Set 1: distance_m 5000.01 is outside 1 to 5,000.",
                            "Give distance_m on set 1 from 1 to 5,000, in metres.")),
    # Pull-Up and Dip are their words' own names, and since C4c a minus weight on a word is the assisted lift (note
    # (i)): exact true keeps these two on the lift as written, where R6 refuses the minus weight.
    "a minus weight on a lift with no help": ({"exercise": "Pull-Up", "weight_kg": -20, "reps": 8, "exact": True},
                                              ("weight_out_of_range",
                                               "Set 1: a minus weight is help, on Assisted Pull-Up or Assisted Dip "
                                               "only.", "Give weight_kg on set 1 from 0 to 500 (0 for bodyweight).")),
    "help past -200": ({"exercise": "Assisted Pull-Up", "weight_kg": -200.01, "reps": 8},
                       ("weight_out_of_range", "Set 1: weight -200.01 kg is outside -200 to 0 kg.",
                        "Give weight_kg on set 1 from -200 to 0: the help as a minus weight.")),
    "a weight above 0 on an assisted lift": ({"exercise": "Assisted Dip", "weight_kg": 10, "reps": 8},
                                             ("weight_out_of_range", "Set 1: weight 10 kg is outside -200 to 0 kg.",
                                              "Give weight_kg on set 1 from -200 to 0: the help as a minus weight.")),
    "help in lb past -200 kg": ({"exercise": "Assisted Pull-Up", "weight_lb": -441, "reps": 8},
                                ("weight_out_of_range", "Set 1: weight_lb -441 lb is outside -440.92 to 0 lb.",
                                 "Give weight_lb on set 1 from -440.92 to 0 lb: the help as a minus weight.")),
    "a minus weight in lb on a lift with no help": ({"exercise": "Dip", "weight_lb": -10, "reps": 8, "exact": True},
                                                    ("weight_out_of_range",
                                                     "Set 1: a minus weight is help, on Assisted Pull-Up or Assisted "
                                                     "Dip only.", "Give weight_lb on set 1 from 0 to 1,102.31 lb.")),
    "a weight above 500": ({"exercise": "Back Squat", "weight_kg": 501, "reps": 5},
                           ("weight_out_of_range", "Set 1: weight 501 kg is outside 0 to 500 kg.",
                            "Give weight_kg on set 1 from 0 to 500 (0 for bodyweight).")),
}


@pytest.mark.parametrize("case", sorted(R6_REFUSALS))
def test_each_refusal_of_r6_in_its_words_and_nothing_written(uid, case):
    sent, want = R6_REFUSALS[case]
    r = log(uid, sent)
    assert refusal(r) == [want]
    assert services.get_log(uid, DAY)["days"][0]["sessions"] == []
    clean(*want[1:])


TAKEN = [
    ({"exercise": "Wall Ball", "weight_kg": 6, "reps": 100}, (6, 100, None, None)),
    ({"exercise": "Push-Up", "weight_kg": 0, "reps": 1}, (0, 1, None, None)),
    ({"exercise": "Plank", "weight_kg": 0, "time_s": 1}, (0, None, 1, None)),
    ({"exercise": "Plank", "weight_kg": 10, "time_s": 3600}, (10, None, 3600, None)),
    ({"exercise": "Farmer's Carry", "weight_kg": 32, "distance_m": 40}, (32, None, None, 40)),
    ({"exercise": "Sled Push", "weight_kg": 150, "distance_m": 5000, "time_s": 600}, (150, None, 600, 5000)),
    ({"exercise": "Sandbag Lunge", "weight_kg": 20, "distance_m": 12.345}, (20, None, None, 12.35)),
    ({"exercise": "Band Pull-Apart", "reps": 15}, (0, 15, None, None)),
    ({"exercise": "Band Lateral Walk", "weight_kg": 0, "reps": 20}, (0, 20, None, None)),
    ({"exercise": "Assisted Pull-Up", "weight_kg": -200, "reps": 8}, (-200, 8, None, None)),
    ({"exercise": "Assisted Dip", "weight_kg": 0, "reps": 8}, (0, 8, None, None)),
    ({"exercise": "Assisted Pull-Up", "weight_lb": -44.09, "reps": 8}, (-20, 8, None, None)),
]


@pytest.mark.parametrize("sent, stored", TAKEN, ids=lambda v: str(v)[:40])
def test_each_set_r6_takes_is_stored_as_its_lift_records_it(uid, sent, stored):
    r = log(uid, sent)
    assert "error" not in r, r
    [x] = r["session"]["sets"]
    assert (x["weight_kg"], x["reps"], x["time_s"], x["distance_m"]) == stored


def test_what_a_set_takes_comes_from_the_list_and_a_name_not_on_it_takes_weight_and_reps():
    assert rules.HARD_RULES["reps"] == (1, 100)
    assert (rules.records("Bench Press"), rules.records("Plank"), rules.records("Farmer's Carry"),
            rules.records("Band Pull-Apart")) == ("reps", "time", "distance and time", "reps")
    assert rules.measures_of("Not On The List") == ("weight", "reps") and rules.records(None) == "reps"
    assert rules.weight_range("Assisted Dip") == (-200, 0) and rules.weight_range("Dip") == (0, 500)
    # A name not matched has each number it sends checked for its range alone, and reps needed when none is sent.
    _, _, err = rules.check_session(None, [{"exercise": "Zorb", "weight_kg": 0, "time_s": 9999}], [], NOW.date())
    assert [e["error"] for e in err["errors"]] == ["unknown_exercise", "time_out_of_range"]
    _, _, err = rules.check_session(None, [{"exercise": "Zorb", "weight_kg": 0}], [], NOW.date())
    assert [e["error"] for e in err["errors"]] == ["unknown_exercise", "missing_field"]
    # A name matched but not on the list (a test's own, or a row the list no longer has) records weight and reps.
    _, _, err = rules.check_session(None, [{"exercise": "Zorb", "weight_kg": 0, "time_s": 60}], ["Zorb"], NOW.date())
    assert [e["message"] for e in err["errors"]] == ["Set 1: Zorb records reps, not time."]


# ---------------------------------------------------------------- R14: the reply's words for a set

WORDS = [
    ([("Dumbbell Bench Press", 25, 10)] * 3, "Dumbbell Bench Press 3 × 10 at 25 kg each"),
    ([("Bulgarian Split Squat", 20, 10)] * 3, "Bulgarian Split Squat 3 × 10 a side at 20 kg each"),
    ([("Assisted Pull-Up", -20, 8)] * 3, "Assisted Pull-Up 3 × 8 with 20 kg help"),
    ([("Plank", 0, None, 60, None)] * 2, "Plank 2 × 60 s"),
    ([("Plank", 10, None, 60, None)], "Plank 60 s at 10 kg"),
    ([("Farmer's Carry", 32, None, 45, 40)] * 2, "Farmer's Carry 2 × 40 m at 32 kg each in 45 s"),
    ([("Band Pull-Apart", 0, 15)] * 3, "Band Pull-Apart 3 × 15"),
    ([("Side Plank", 0, None, 30, None)] * 2, "Side Plank 2 × 30 s a side"),
    ([("Suitcase Carry", 24, None, None, 20)], "Suitcase Carry 20 m a side at 24 kg"),
    ([("Plank", 0, None, 60, None), ("Plank", 10, None, 45, None)], "Plank 60 s, 45 s at 10 kg"),
    ([("Assisted Dip", -30, 8), ("Assisted Dip", -20, 6)], "Assisted Dip 8 reps with 30 kg help, 6 reps with 20 kg help"),
    ([("Dumbbell Bench Press", 25, 10), ("Dumbbell Bench Press", 27.5, 8)],
     "Dumbbell Bench Press 25 kg each × 10, 27.5 kg each × 8"),
    ([("Bulgarian Split Squat", 0, 10), ("Bulgarian Split Squat", 0, 8)],
     "Bulgarian Split Squat 10 reps a side, 8 reps a side"),
    ([("Back Squat", 80, 5)] * 3, "Back Squat 3 × 5 at 80 kg"),  # a bar's weight, as before
    ([("Retired Lift", 25, 10)], "Retired Lift 1 × 10 at 25 kg"),  # a name not on the list reads plain
]


@pytest.mark.parametrize("rows, words", WORDS, ids=lambda v: str(v)[:30])
def test_the_words_of_each_kind_of_set(rows, words):
    assert lifts.sets_text(rows) == words
    clean(words, surface=tr.SUMMARY)


def test_the_words_follow_the_person_s_units_and_time_and_distance_stay_seconds_and_metres():
    lb = Units("lb", "mi")
    assert lifts.sets_text([("Farmer's Carry", 32, None, 45, 40)], lb) == "Farmer's Carry 40 m at 70.55 lb each in 45 s"
    assert lifts.sets_text([("Assisted Pull-Up", -20, 8)], lb) == "Assisted Pull-Up 1 × 8 with 44.1 lb help"
    assert lifts.sets_text([("Plank", 0, None, 90, None)], KG_KM) == "Plank 90 s"


# ---------------------------------------------------------------- a hold, a carry, a band and help, end to end

SESSION = [
    {"exercise": "Plank", "weight_kg": 0, "time_s": 60},
    {"exercise": "Plank", "weight_kg": 0, "time_s": 60},
    {"exercise": "Farmer's Carry", "weight_kg": 32, "distance_m": 40, "time_s": 45},
    {"exercise": "Band Pull-Apart", "reps": 15},
    {"exercise": "Assisted Pull-Up", "weight_kg": -20, "reps": 8},
]
SESSION_WORDS = ("Plank 2 × 60 s, Farmer's Carry 40 m at 32 kg each in 45 s, Band Pull-Apart 1 × 15 and "
                 "Assisted Pull-Up 1 × 8 with 20 kg help")
STORED = [("Plank", 1, 0, None, 60, None), ("Plank", 2, 0, None, 60, None), ("Farmer's Carry", 1, 32, None, 45, 40),
          ("Band Pull-Apart", 1, 0, 15, None, None), ("Assisted Pull-Up", 1, -20, 8, None, None)]


def _sets(session: dict) -> list[tuple]:
    return [(x["exercise"], x["set_number"], x["weight_kg"], x["reps"], x["time_s"], x["distance_m"])
            for x in session["sets"]]


def test_a_hold_a_carry_a_band_and_help_are_logged_read_edited_undone_and_exported(uid):
    logged = log(uid, *SESSION)
    assert _sets(logged["session"]) == STORED
    assert logged["summary"].startswith(f"{SESSION_WORDS} on Mon 9 Nov. ")
    clean(logged["summary"], surface=tr.SUMMARY)
    day = services.get_log(uid, DAY)["days"][0]
    assert _sets(day["sessions"][0]) == STORED and day["summary"] == SESSION_WORDS + "."

    edited = services.edit_entry(uid, "lift_session", logged["id"], {"sets": [
        {"exercise": "Plank", "weight_kg": 0, "time_s": 90}, *SESSION[1:]]}, "claude")
    assert edited["changed"] == ["sets"], edited
    now = ("Plank 90 s, 60 s, Farmer's Carry 40 m at 32 kg each in 45 s, Band Pull-Apart 1 × 15 and Assisted Pull-Up "
           "1 × 8 with 20 kg help")
    assert edited["summary"] == f"Lift session changed: sets from {rules.quote(SESSION_WORDS)} to {rules.quote(now)}."
    clean(now, surface=tr.SUMMARY)  # the summary cuts each side at 80 characters (rules.quote), as before
    assert _sets(edited["after"])[0] == ("Plank", 1, 0, None, 90, None)
    # A time alone changed is a change: the edit's compare and the key hold time and distance (R15).
    with db.new_session() as s:
        [version] = s.scalars(select(EntryVersion).where(EntryVersion.entry_id == logged["id"])).all()
        assert json.loads(version.before)["sets"][2][2:] == [32.0, None, 45, 40.0]

    undone = services.restore_entry(uid, "lift_session", logged["id"], "claude", undo_edit=True)
    assert undone["undone"] is True and _sets(undone["entry"]) == STORED

    files = services.export_data(uid)["files"]
    rows = list(csv.DictReader(io.StringIO(files["lift_sets.csv"].lstrip("﻿"))))
    assert list(rows[0]) == ["session_id", "exercise", "set_number", "weight_kg", "reps", "time_s", "distance_m"]
    assert [(r["exercise"], r["weight_kg"], r["reps"], r["time_s"], r["distance_m"]) for r in rows] == [
        ("Plank", "0", "", "60", ""), ("Plank", "0", "", "60", ""), ("Farmer's Carry", "32", "", "45", "40"),
        ("Band Pull-Apart", "0", "15", "", ""), ("Assisted Pull-Up", "-20", "8", "", "")]
    kept = [json.loads(r["before"]) for r in csv.DictReader(io.StringIO(files["entry_versions.csv"].lstrip("﻿")))]
    assert kept[0]["sets"][2] == {"exercise": "Farmer's Carry", "set_number": 1, "weight_kg": 32.0, "reps": None,
                                  "time_s": 45, "distance_m": 40.0}
    assert "a lift set's distance_m in metres" in files["README.txt"]


def test_an_add_to_the_day_s_session_keeps_its_time_and_distance_and_says_them(uid):
    first = log(uid, SESSION[0])
    added = services.log_lifts(uid, {"date": DAY, "sets": [SESSION[2]]}, "claude")
    assert added["id"] == first["id"]
    assert added["summary"].startswith("Added Farmer's Carry 40 m at 32 kg each in 45 s to the session on Mon 9 Nov.")
    assert _sets(added["session"]) == [STORED[0], STORED[2]]


def test_a_set_with_no_reps_sets_no_best_and_reads_no_estimate(uid):
    first = log(uid, *SESSION)
    assert first["new_bests"] == []
    # A heavier hold that is shorter sets no weight best without reps (and, since C4e, no time best: 45 s is short
    # of the 60 s held before, the mark with no earlier set as heavy).
    again = log(uid, {"exercise": "Plank", "weight_kg": 20, "time_s": 45})
    assert again["new_bests"] == [] and "No new bests." in again["summary"]
    history = services.get_progress(uid)
    assert "error" not in history, history


def test_two_holds_that_differ_only_in_time_are_two_sets_in_the_key_and_a_set_of_reps_keeps_its_old_key():
    on = NOW.date()
    hold = [(7, 1, 0.0, None, 60, None)]
    assert lifts.session_key(on, "", hold) != lifts.session_key(on, "", [(7, 1, 0.0, None, 90, None)])
    assert lifts.session_key(on, "", [(7, 1, 30.0, None, 45, 40.0)]) != lifts.session_key(
        on, "", [(7, 1, 30.0, None, 45, 50.0)])
    # A set of reps alone keys as its four items did before time and distance.
    assert lifts.session_key(on, "", [(7, 1, 80.0, 5, None, None)]) == lifts.session_key(on, "", [(7, 1, 80.0, 5)])


def test_a_four_item_version_from_before_0013_is_put_back_with_no_time_and_no_distance(uid):
    logged = log(uid, {"exercise": "Back Squat", "weight_kg": 100, "reps": 5})
    with db.session_scope() as s:
        squat = s.scalar(select(Exercise.id).where(Exercise.name == "Back Squat"))
        s.add(EntryVersion(user_id=uid, kind="lift_session", entry_id=logged["id"], by="web", why="edit",
                           before=json.dumps({"notes": "old", "sets": [[squat, 1, 90.0, 5], [squat, 2, 95.0, 3]]})))
    undone = services.restore_entry(uid, "lift_session", logged["id"], "claude", undo_edit=True)
    assert undone["undone"] is True
    assert _sets(undone["entry"]) == [("Back Squat", 1, 90, 5, None, None), ("Back Squat", 2, 95, 3, None, None)]
    assert undone["entry"]["notes"] == "old"
    # Equal sets of four and of six are the same sets: an undo back to them leaves the rows as they are. The set
    # objects themselves are compared, not their ids: SQLite gives a rebuilt set the id a deleted one had.
    with db.session_scope() as s:
        row = s.get(LiftSession, logged["id"])
        held = list(row.sets)
        common.put_back(s, row, {"sets": [[squat, 1, 90.0, 5], [squat, 2, 95.0, 3]]})
        assert len(row.sets) == len(held) and all(now is was for now, was in zip(row.sets, held))


# ---------------------------------------------------------------- R10 and R11: routines, goals and imports take reps

def test_a_routine_refuses_a_hold_a_carry_and_a_sled_sent_with_reps_and_takes_reps_up_to_100(uid):
    """routines-and-programs.md C5b: a routine holds a hold, a carry and a sled, each with what it records, so one sent
    with reps is refused as log refuses a set of it (the note of 2026-11-22); "a routine holds lifts of sets and reps"
    is gone. tests/test_routine_items.py holds each kind saved."""
    lifts_sent = [{"exercise": "Bench Press", "sets": 3, "reps": 100}, {"exercise": "Plank", "sets": 2, "reps": 1},
                  {"exercise": "Farmer's Carry", "sets": 2, "reps": 1}, {"exercise": "Sled Push", "sets": 1, "reps": 1}]
    refused = services.save_routine(uid, {"name": "Hyrox day", "lifts": lifts_sent}, "claude")
    assert refusal(refused) == [
        ("invalid_field", "Lift 2: Plank records time, not reps.", "Send time_s on lift 2."),
        ("invalid_field", "Lift 3: Farmer's Carry records distance and time, not reps.", "Send distance_m on lift 3."),
        ("invalid_field", "Lift 4: Sled Push records distance and time, not reps.", "Send distance_m on lift 4.")]
    clean(*(e["message"] for e in refused["errors"]))
    saved = services.save_routine(uid, {"name": "Hyrox day", "lifts": lifts_sent[:1]}, "claude")
    assert saved["created"] is True and saved["routine"]["lifts"][0]["reps"] == 100


def test_a_routine_takes_r6_s_weight_range_so_help_round_trips_and_says_help(uid, client):
    # A routine takes for each lift the weight log_lifts takes (R6): help, -200 to 0 kg, on the two assisted lifts.
    lifts_sent = [{"exercise": "Assisted Pull-Up", "sets": 3, "reps": 8, "weight_kg": -20},
                  {"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 60}]
    saved = services.save_routine(uid, {"name": "Pull day", "lifts": lifts_sent}, "claude")
    assert saved["routine"]["lifts"][0]["weight_kg"] == -20
    assert saved["summary"] == "Saved Pull day: Assisted Pull-Up 3 × 8 with 20 kg help and Bench Press 3 × 8 at 60 kg."
    read = services.get_routines(uid)
    [routine] = read["routines"]
    assert (routine["lifts"][0]["weight_kg"], routine["lifts"][0]["weight_from"]) == (-20, "routine")
    assert routine["words"] == "Assisted Pull-Up 3 × 8 with 20 kg help and Bench Press 3 × 8 at 60 kg"
    clean(saved["summary"], routine["words"], surface=tr.SUMMARY)
    # What get_routines hands out from the last session saves back as it is.
    services.log_lifts(uid, {"sets": [{"exercise": "Assisted Pull-Up", "weight_kg": -30, "reps": 8}]}, "claude")  # today
    top = services.get_routines(uid)["routines"][0]["lifts"][0]
    assert (top["weight_kg"], top["weight_from"]) == (-30, "last_session")
    again = services.save_routine(uid, {"name": "Pull day", "lifts": [
        {k: top[k] for k in ("exercise", "sets", "reps", "weight_kg")}]}, "claude")
    assert "error" not in again and again["routine"]["lifts"][0]["weight_kg"] == -30
    refusals = {
        "Bench Press": ("Lift 1: a minus weight is help, on Assisted Pull-Up or Assisted Dip only.",
                        "Give weight_kg on lift 1 from 0 to 500, or leave it out."),
        "Assisted Dip": ("Lift 1: weight 10 kg is outside -200 to 0 kg.",
                         "Give weight_kg on lift 1 from -200 to 0: the help as a minus weight, or leave it out."),
    }
    for name, (message, fix) in refusals.items():
        weight = -20 if name == "Bench Press" else 10
        r = services.save_routine(uid, {"name": "Other", "lifts": [{"exercise": name, "sets": 3, "reps": 8,
                                                                    "weight_kg": weight}]}, "claude")
        assert refusal(r) == [("weight_out_of_range", message, fix)]
        clean(message, fix)
    # The Routines page says help the same way.
    sign_in(client)
    page_user = only_user_of(client)
    services.save_routine(page_user, {"name": "Pull day", "lifts": lifts_sent}, "claude")
    page = client.get("/routines")
    # routines-and-programs.md C5a: the page's Item and Plan cells
    assert page.status_code == 200 and ('<td class="lead">Assisted Pull-Up</td><td class="wide-cell" '
                                        'data-label="Plan">3 × 8 with 20 kg help</td>') in page.text
    assert "-20" not in page.text


def test_get_routines_leaves_out_a_lift_saved_before_that_now_records_time_or_distance_and_says_so(uid):
    saved = services.save_routine(uid, {"name": "Core day", "lifts": [{"exercise": "Dead Bug", "sets": 3,
                                                                      "reps": 10}]}, "claude")
    old = [{"exercise": "Dead Bug", "sets": 3, "reps": 10, "weight_kg": None},
           {"exercise": "Plank", "sets": 3, "reps": 30, "weight_kg": None},
           {"exercise": "Farmer's Carry", "sets": 2, "reps": 1, "weight_kg": 32}]
    with db.session_scope() as s:  # as a routine saved before C4 holds them
        s.get(Routine, saved["id"]).lifts = json.dumps(old)
    read = services.get_routines(uid)
    [routine] = read["routines"]
    assert [x["exercise"] for x in routine["lifts"]] == ["Dead Bug"] and routine["words"] == "Dead Bug 3 × 10"
    assert read["summary"] == ("1 routine: Core day. Plank is left out: it records time now. Ask the person how long, "
                               "and log it with time_s. Farmer's Carry is left out: it records distance and time now. "
                               "Ask the person how far, and log it with distance_m.")
    clean(read["summary"], surface=tr.SUMMARY)
    with db.new_session() as s:
        assert json.loads(s.get(Routine, saved["id"]).lifts) == old  # the routine keeps them


def test_a_lift_goal_refuses_a_hold_and_a_carry(uid):
    for name, what in (("Plank", "time"), ("Farmer's Carry", "distance and time")):
        r = services.set_goal(uid, {"kind": "lift", "exercise": name, "target_value": 100,
                                    "target_date": "2027-03-31"}, "claude")
        assert (r["error"], r["message"], r["fix"]) == (
            "invalid_field", f"{name} records {what}: a lift goal needs a lift of weight and reps.",
            "Use a lift from list_exercises with kind lift."), r
        clean(r["message"], r["fix"])
    assert "error" not in services.set_goal(uid, {"kind": "lift", "exercise": "Wall Ball", "target_value": 40,
                                                  "start_value": 20, "target_date": "2027-03-31"}, "claude")


def test_a_lift_goal_refuses_a_band_lift_and_an_assisted_lift_even_with_a_session_logged(uid):
    # An outside review (exercise-list.md, note of 2026-11-20): a band stores 0 kg and help a minus weight, so Epley
    # gives no estimated max and such a goal never moves. It was refused as "no logged ... to start this goal from"
    # with a session there, or taken with start_value.
    fix = "Use a lift from list_exercises with kind lift, logged with a weight above 0."
    logged = {"Band Pull-Apart": {"reps": 15}, "Band-Assisted Pull-Up": {"reps": 8},
              "Band Lateral Walk": {"reps": 12}, "Assisted Pull-Up": {"weight_kg": -20, "reps": 8},
              "Assisted Dip": {"weight_kg": -10, "reps": 10}}
    for name, sent in logged.items():
        out = services.log_lifts(uid, {"date": DAY, "sets": [{"exercise": name, "exact": True, **sent}]}, "claude")
        assert "error" not in out, out
        how = "is logged with help" if name.startswith("Assisted") else "records reps, not weight"
        for start in ({}, {"start_value": 20}):
            r = services.set_goal(uid, {"kind": "lift", "exercise": name, "target_value": 100,
                                        "target_date": "2027-03-31", **start}, "claude")
            assert (r["error"], r["message"], r["fix"], r["field"]) == (
                "invalid_field", f"{name} {how}: a lift goal needs a lift of weight and reps.", fix, "exercise"), r
        clean(r["message"], r["fix"])
    with db.new_session() as s:
        assert s.scalars(select(Goal)).all() == []


def test_a_pull_up_or_push_up_goal_behaves_as_before(uid, make_user):
    # Not changed by the note of 2026-11-20. Logged at 0 kg alone there is no estimated max to start from, so the goal
    # is refused as nothing logged to start from; a start_value, or a set with added weight, starts it.
    goal = {"kind": "lift", "target_value": 100, "target_date": "2027-03-31"}
    for name in ("Pull-Up", "Push-Up"):
        out = services.log_lifts(uid, {"date": DAY, "sets": [{"exercise": name, "exact": True, "weight_kg": 0,
                                                              "reps": 10}]}, "claude")
        assert "error" not in out, out
        r = services.set_goal(uid, {**goal, "exercise": name}, "claude")
        assert (r["error"], r["message"], r["field"]) == (
            "missing_field", f"There is no logged {name} to start this goal from.", "start_value"), r
        started = services.set_goal(uid, {**goal, "exercise": name, "start_value": 20}, "claude")
        assert "error" not in started, started
    other = make_user(time_zone="UTC").id
    weighted = services.log_lifts(other, {"date": DAY, "sets": [
        {"exercise": "Pull-Up", "exact": True, "weight_kg": 10, "reps": 5}]}, "claude")
    assert "error" not in weighted, weighted
    r = services.set_goal(other, {**goal, "exercise": "Pull-Up"}, "claude")
    assert "error" not in r and r["goal"]["start_value"] == 11.7, r  # Epley of 10 kg x 5, at 1 dp


def test_an_import_row_of_a_hold_fails_alone_once_a_lift_and_help_follows_r6(uid):
    plank = {"kind": "lift_session", "date": DAY, "sets": [
        {"exercise": "Plank", "weight": 0, "reps": 60}, {"exercise": "Plank", "weight": 0, "reps": 45},
        {"exercise": "Bench Press", "weight": 60, "reps": 5}]}
    helped = {"kind": "lift_session", "date": "2026-11-08", "sets": [
        {"exercise": "Assisted Pull-Up", "weight": -20, "reps": 8}, {"exercise": "Bench Press", "weight": 60, "reps": 100}]}
    minus = {"kind": "lift_session", "date": "2026-11-07", "sets": [{"exercise": "Bench Press", "weight": -5, "reps": 5}]}
    r = services.import_entries(uid, {"rows": [plank, helped, minus], "weight_unit": "kg", "dry_run": False}, "claude")
    assert r["row_outcomes"] == {"written": 1, "already_there": 0, "day_taken": 0, "failed": 2}, r
    first, third = r["failed"]
    assert (first["row"], first["error"], first["message"], first["fix"]) == (
        1, "invalid_field", "Row 1: Plank records time: an import takes sets of reps.",
        "Leave it out and tell the person.")
    assert (third["row"], third["message"]) == (
        3, "Row 3, set 1: a minus weight is help, on Assisted Pull-Up or Assisted Dip only.")
    clean(first["message"], first["fix"], third["message"], third["fix"])
    sessions = services.get_log(uid, "2026-11-09", 3)["days"]
    assert [_sets(t) for d in sessions for t in d["sessions"]] == [[("Assisted Pull-Up", 1, -20, 8, None, None),
                                                                    ("Bench Press", 1, 60, 100, None, None)]]
    band = services.import_entries(uid, {"rows": [{"kind": "lift_session", "date": "2026-11-05", "sets": [
        {"exercise": "Band Pull-Apart", "weight": 5, "reps": 15}]}], "weight_unit": "kg", "dry_run": True}, "claude")
    assert (band["failed"][0]["message"], band["failed"][0]["fix"]) == (
        "Row 1, set 1: Band Pull-Apart records reps, not weight.", "Send weight 0 on set 1: a band set stores 0 kg.")
    clean(band["failed"][0]["fix"])
    zero = services.import_entries(uid, {"rows": [{"kind": "lift_session", "date": "2026-11-05", "sets": [
        {"exercise": "Band Pull-Apart", "weight": 0, "reps": 15}]}], "weight_unit": "kg", "dry_run": True}, "claude")
    assert zero["failed"] == []
    lb = services.import_entries(uid, {"rows": [{"kind": "lift_session", "date": "2026-11-06", "sets": [
        {"exercise": "Assisted Dip", "weight": 10, "reps": 8}]}], "weight_unit": "lb", "dry_run": True}, "claude")
    assert lb["failed"][0]["message"] == "Row 1, set 1: weight 10 lb is outside -440.92 to 0 lb."
    assert lb["failed"][0]["fix"] == "Give weight on set 1 from -440.92 to 0 lb: the help as a minus weight."


# ---------------------------------------------------------------- the sets table on the pages

def only_user_of(client) -> int:
    """The id of the person the page client signed in as: the newest account."""
    with db.new_session() as s:
        return s.scalars(select(User.id).order_by(User.id.desc())).first()


def _table(sets: list[dict]) -> str:
    return str(web.templates.env.get_template("_parts.html").module.sets_table(sets, KG_KM))


def test_the_sets_table_gains_time_and_distance_only_when_a_set_holds_them():
    plain = _table([{"exercise": "Back Squat", "set_number": 1, "weight_kg": 80, "reps": 5, "time_s": None,
                     "distance_m": None}])
    assert "<th class=\"num\">Reps</th>" in plain and "Time (s)" not in plain and "Distance (m)" not in plain
    mixed = _table([{"exercise": "Plank", "set_number": 1, "weight_kg": 0, "reps": None, "time_s": 60,
                     "distance_m": None},
                    {"exercise": "Farmer's Carry", "set_number": 1, "weight_kg": 32, "reps": None, "time_s": 45,
                     "distance_m": 12.5}])
    assert "Time (s)" in mixed and "Distance (m)" in mixed and ">Reps<" not in mixed
    cells = re.findall(r"<td[^>]*>(.*?)</td>", mixed)
    assert cells[:5] == ["Plank", "1", "", "60", ""] and cells[5:] == ["Farmer&#39;s Carry", "1", "32", "45", "12.5"]
    helped = _table([{"exercise": "Assisted Dip", "set_number": 1, "weight_kg": -20, "reps": 8, "time_s": None,
                      "distance_m": None}])
    assert ">-20<" in helped


# ---------------------------------------------------------------- through /mcp: log_lifts' own set model (R3)

def test_log_lifts_takes_its_own_set_model_and_edit_entry_takes_set_in_with_the_same_fields_for_now(writer):
    """Since C4c (R3) log_lifts' sets also take exact and the person's own words: tests/test_matcher.py."""
    http, token, account = writer
    tools = registered_tools(http)  # log_lifts, though no list holds it now (log-and-guide.md)
    logged = tools["log_lifts"]["inputSchema"]
    edit = tools["edit_entry"]["inputSchema"]
    assert "LogSetIn" in logged["$defs"] and "SetIn" not in logged["$defs"] and "SetIn" in edit["$defs"]
    assert list(LogSetIn.model_fields) == [*SetIn.model_fields, "exact"] and list(SetIn.model_fields) == [
        "exercise", "weight_kg", "weight_lb", "reps", "time_s", "distance_m"]
    props = logged["$defs"]["LogSetIn"]["properties"]
    same = {k: v for k, v in props.items() if k not in ("exercise", "exact")}
    assert same == {k: v for k, v in edit["$defs"]["SetIn"]["properties"].items() if k != "exercise"}
    assert logged["$defs"]["LogSetIn"]["required"] == ["exercise"]
    assert props["reps"]["description"] == (  # one side's: exercise-list.md, note of 2026-11-19, C4f (z)
        "Reps in this set, a whole number from 1 to 100. Leave out for a hold or a carry. On a lift done one side at a "
        "time, one side's reps.")
    assert props["time_s"]["description"] == (
        "A hold's or a carry's time in seconds, 1 to 3,600. On a lift done one side at a time, one side's time.")
    assert props["distance_m"]["description"] == (
        "A carry's or a sled's distance in metres, 1 to 5,000. On a lift done one side at a time, one side's distance.")
    for field in ("weight_kg", "weight_lb"):  # a pair's: one weight in both hands (Goblet Squat) is that weight
        assert ("Use 0 for bodyweight work. For a pair of dumbbells or kettlebells, or two cables, one hand's weight. "
                "Help on Assisted Pull-Up or Assisted Dip as a minus weight.") in props[field]["description"]
    hold = ok(http, token, "log_lifts", {"date": DAY, "sets": [{"exercise": "Plank", "weight_kg": 0, "time_s": 60}]})
    assert hold["session"]["sets"][0]["time_s"] == 60 and hold["summary"].startswith("Plank 60 s on ")
    wrong = failed(http, token, "log_lifts", {"sets": [{"exercise": "Plank", "weight_kg": 0, "seconds": 60}]})
    assert wrong["message"] == "Set 1: 'seconds' is not a field of a set."
    assert wrong["fix"].startswith("Use: exercise, weight_kg, weight_lb, reps, time_s, distance_m, exact.")
    again = failed(http, token, "edit_entry", {"kind": "lift_session", "entry_id": hold["id"],
                                               "sets": [{"exercise": "Plank", "weight_kg": 0, "seconds": 90}]})
    assert again["fix"].startswith("Use: exercise, weight_kg, weight_lb, reps, time_s, distance_m.")
    refused = failed(http, token, "log_lifts", {"sets": [{"exercise": "Plank", "weight_kg": 0, "reps": 60}]})
    assert (refused["error"], refused["message"]) == ("invalid_field", "Set 1: Plank records time, not reps.")


def test_every_result_over_a_hold_a_carry_and_help_passes_its_published_model_through_mcp(writer):
    http, token, account = writer
    logged = ok(http, token, "log_lifts", {"date": DAY, "sets": SESSION})
    assert _sets(logged["session"]) == STORED
    day = ok(http, token, "get_log", {"date": DAY})
    assert _sets(day["days"][0]["sessions"][0]) == STORED
    edited = ok(http, token, "edit_entry", {"kind": "lift_session", "entry_id": logged["id"], "sets": [
        {"exercise": "Dead Hang", "weight_kg": 0, "time_s": 45}, *SESSION[2:]]})
    assert edited["changed"] == ["sets"] and _sets(edited["after"])[0] == ("Dead Hang", 1, 0, None, 45, None)
    undone = ok(http, token, "restore_entry", {"kind": "lift_session", "entry_id": logged["id"], "undo_edit": True})
    assert _sets(undone["entry"]) == STORED
    saved = ok(http, token, "save_routine", {"name": "Core day", "lifts": [
        {"exercise": "Dead Bug", "sets": 3, "reps": 100}]})
    with db.session_scope() as s:
        s.get(Routine, saved["id"]).lifts = json.dumps([
            {"exercise": "Dead Bug", "sets": 3, "reps": 100, "weight_kg": None},
            {"exercise": "Side Plank", "sets": 2, "reps": 30, "weight_kg": None}])
    read = ok(http, token, "get_routines")
    assert [x["exercise"] for x in read["routines"][0]["lifts"]] == ["Dead Bug"]
    assert read["summary"].endswith("Side Plank is left out: it records time now. Ask the person how long, and log it "
                                    "with time_s.")


def test_the_new_schema_words_keep_the_text_rules():
    for model in (SetIn, LogSetIn):
        for field in model.model_fields.values():
            clean(field.description, surface=tr.CLAUDE)
