"""The eval runner's own parts: the cases file, the judging, the CLI output parser, the token mask and
the call recorder. Nothing here calls the model or starts the Claude CLI."""

import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app import db, metrics, services
from app.models import EntryVersion, User
from app.services import common
from app.main import create_app
from evals import judge
from evals.recorder import CallRecorder, parse_body, token_key
from evals.run import (
    LocalServer, child_env, cli_command, dates_for, local_time_zone, mcp_config, next_month_end, person_rows,
)
from tests.conftest import HEADERS, settings

CONTRACT_CASES = {"stated_number", "guessed_number", "saved_food", "meal_not_asked", "yesterday", "treadmill_once",
                  "repeat_sent", "delete_ambiguous", "delete_asks_first", "unknown_exercise",
                  "goal_date", "how_am_i_doing", "on_track", "export_is_a_click", "delete_is_a_click", "drop_goal",
                  "decimals_and_dashes", "import_preview_first", "import_again", "import_unknown_lift", "undo_edit",
                  "feedback_sent", "feedback_offered",
                  "hi_there_first_log", "hi_there_not_again", "setup_asks_first", "setup_picks_up",
                  "delete_regretted",  # switch decision 21: delete_explicit became delete_asks_first
                  "lift_one_sentence", "lift_set_by_set", "lift_added_later",  # gym logging, decision 17
                  "setup_stays_quiet",  # first day: the review's S1
                  "routine_saved", "routine_logged", "routine_changed",  # routines, decision 21
                  "lb_weigh_in", "lb_lifts", "miles_run", "units_asked", "units_switch",  # units, decision 20
                  "lb_routine_changed",
                  "waist_in_inches", "machine_calories",  # inputs.md decision 19
                  "data_basketball", "data_lifting", "data_calories", "data_squat_goal",  # data-on-request.md
                  "data_back_on_track", "data_lb", "data_empty", "data_year_more",  # decision 20
                  # function-list.md, G1: the golden set's new direct cases, three draft incidental and look-alike
                  # pairs, a case for each function no case had as its must, the catch-up and the import
                  "gold_squat_month", "gold_weigh_morning", "gold_kcal_yesterday", "gold_squat_goal", "gold_eat_more",
                  "gold_best_deadlift", "gold_run_week", "inc_10k_plan", "look_10k_friend", "inc_trip_meals",
                  "look_trip_hours", "inc_deload", "look_deload", "save_food_asked", "edit_fixes_minutes",
                  "food_lookup", "routine_read", "exercise_list", "catch_up", "import_other_app"}
# function-list.md, G1's note of 2026-11-17: the golden package's reviewed cases follow G1's (tests/test_golden_cases.py)
GOLDEN_PACKAGE = 187  # 164, and exercise-list.md C4g's 23
EM_DASH, EN_DASH = "\u2014", "\u2013"
WRITES = set(common.WRITE_FUNCTIONS)


def call(name, arguments=None, is_error=False):
    return {"name": name, "arguments": arguments or {}, "is_error": is_error}


def answered(name, answer, arguments=None, is_error=False):
    """A recorded data read with its answer, as evals/recorder.py keeps it."""
    return {**call(name, arguments, is_error), "answered": True, "answer": answer}


# ---------------------------------------------------------------- the cases file

def test_cases_file_loads_with_every_contract_case():
    cases = judge.load_cases()
    assert len(cases) == len(CONTRACT_CASES) + GOLDEN_PACKAGE
    assert CONTRACT_CASES == {c["id"] for c in cases[:len(CONTRACT_CASES)]}
    for case in cases:
        assert {"id", "say", "expect"} <= set(case)
        assert judge.case_problems(case) == []
        assert "\n" not in case["say"]


def test_case_problems_names_what_is_wrong():
    assert judge.case_problems({"say": "x", "expect": {"function": "log_food"}}) == ["missing id"]
    assert "unknown key colour" in judge.case_problems({"id": "a", "say": "x", "expect": {"function": "f"},
                                                        "colour": 1})
    assert judge.case_problems({"id": "a", "say": "x", "expect": {}}) == ["expect must be a non-empty object"]
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"inputs_include": {}}}) == [
        "expect: inputs_include needs function"]
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"any_of": [{"bogus": 1}]}}) == [
        "expect.any_of[0] has unknown key bogus"]
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"function": "f"},
                                "seed": [{"service": "drop_table", "data": {}}]})
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"function": "f"}, "db": [{"table": "user"}]})
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"function": "f"}, "switch": "everything"}) == [
        "switch must be one of log, log_delete"]  # switch decision 21: read went
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"function": "f"}, "switch": "read"}) == [
        "switch must be one of log, log_delete"]
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"function": "f"}, "switch": "log_delete"}) == []
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"function": "f"},
                                "db": [{"table": "bodyweight", "state": "gone"}]})
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"function": "f"},
                                "seed": [{"service": "four_weeks"}], "db": [{"table": "goal"}]}) == []
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"function": "f"},
                                "seed": [{"as": "s", "service": "four_weeks"}]}) == [
        "seed[0]: four_weeks takes no data and no as"]
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"function": "f"},
                                "seed": [{"service": "add_sample_data"}]})   # gone: Spotter adds no sample data
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"reply_has": ["Settings"]}}) == []


def test_load_cases_refuses_duplicates_and_bad_cases():
    with tempfile.TemporaryDirectory() as folder:  # not tmp_path: pytest's own temp root is not always writable
        path = Path(folder) / "cases.json"
        path.write_text(json.dumps([{"id": "a", "say": "x", "expect": {"function": "f"}}] * 2), encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate id"):
            judge.load_cases(path)
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError):
            judge.load_cases(path)


def test_resolve_replaces_whole_string_placeholders_keeping_types():
    variables = {"yesterday": "2026-11-09", "seed.roti": 7}
    got = judge.resolve({"entries": [{"saved_food_id": "{{seed.roti}}"}], "date": "{{ yesterday }}",
                         "note": "not {{yesterday}} inside"}, variables)
    assert got == {"entries": [{"saved_food_id": 7}], "date": "2026-11-09", "note": "not {{yesterday}} inside"}
    with pytest.raises(KeyError):
        judge.resolve("{{seed.dal}}", variables)


def test_dates_for_gives_yesterday_in_the_zone():
    dates = dates_for("Asia/Kolkata")
    assert dates["today"] > dates["yesterday"]
    assert local_time_zone("Europe/London") == "Europe/London"
    ends = [dates[f"next_month_end:{m}"] for m in range(1, 13)]
    assert all(end > dates["today"] for end in ends) and len(set(ends)) == 12
    assert "next_month_end:0" not in dates and "next_month_end:13" not in dates


@pytest.mark.parametrize("today, month, end", [
    ("2026-11-10", 3, "2027-03-31"),   # "by the end of March", said in November
    ("2026-11-10", 12, "2026-12-31"),  # later this year
    ("2026-11-10", 11, "2026-11-30"),  # this month, its last day still to come
    ("2026-11-30", 11, "2027-11-30"),  # today is that last day: strictly after, so next year's
    ("2026-03-15", 3, "2026-03-31"),
    ("2027-11-10", 2, "2028-02-29"),   # a leap year
    ("2026-11-10", 2, "2027-02-28"),
    ("2026-12-31", 12, "2027-12-31"),
    ("2026-01-31", 1, "2027-01-31"),
    ("2026-04-30", 4, "2027-04-30"),
])
def test_next_month_end(today, month, end):
    assert next_month_end(dt.date.fromisoformat(today), month) == dt.date.fromisoformat(end)


@pytest.mark.parametrize("month", [0, 13, -1])
def test_next_month_end_refuses_a_month_out_of_range(month):
    with pytest.raises(ValueError):
        next_month_end(dt.date(2026, 11, 10), month)


def test_resolve_takes_the_month_end_placeholder():
    variables = {"next_month_end:3": "2027-03-31"}
    assert judge.resolve({"target_date": "{{next_month_end:3}}", "d": "{{ next_month_end:3 }}"}, variables) == {
        "target_date": "2027-03-31", "d": "2027-03-31"}
    with pytest.raises(KeyError):
        judge.resolve("{{next_month_end:13}}", variables)
    assert judge.resolve("{{next_month_end:march}}", variables) == "{{next_month_end:march}}"  # not a placeholder


# ---------------------------------------------------------------- matching

def test_matches_nested_subset():
    sent = {"entries": [{"description": "roti", "saved_food_id": 3, "servings": 2.0},
                        {"description": "dal", "kcal": 180, "estimated": True}], "date": "2026-11-09"}
    assert judge.matches(sent, {"entries": [{"saved_food_id": 3, "servings": 2}]})
    assert judge.matches(sent, {"entries": [{"kcal": 180.0}, {"servings": 2}]})
    assert not judge.matches(sent, {"entries": [{"saved_food_id": 3, "kcal": 180}]})  # not on one entry
    assert not judge.matches(sent, {"meal": "lunch"})
    assert judge.matches({"exercise": "treadmill walk "}, {"exercise": {"$in": ["Treadmill Walk", "Treadmill Run"]}})
    assert not judge.matches({"exercise": "Outdoor Walk"}, {"exercise": {"$in": ["Treadmill Walk"]}})


def test_matches_keeps_booleans_and_numbers_apart():
    assert not judge.matches({"estimated": 1}, {"estimated": True})
    assert not judge.matches({"kcal": True}, {"kcal": 1})
    assert judge.matches({"estimated": True}, {"estimated": True})
    assert not judge.matches({"kcal": "180"}, {"kcal": 180})


def test_values_at_steps_into_lists():
    data = {"entries": [{"kcal": 1}, {"meal": "lunch"}, {"kcal": None}]}
    assert judge.values_at(data, "entries.*.kcal") == [1, None]
    assert judge.values_at(data, "entries.*.meal") == ["lunch"]
    assert judge.values_at(data, "date") == []
    assert judge.values_at({"entries": "oops"}, "entries.*.kcal") == []


# ---------------------------------------------------------------- judging the calls

def test_function_called_or_not():
    assert judge.judge_calls({"function": "log_food"}, [call("whoami"), call("log_food")], "") == []
    assert judge.judge_calls({"function": "log_food"}, [call("whoami")], "") == [
        "log_food was not called (calls: whoami)"]
    assert judge.judge_calls({"function": "log_food"}, [], "") == ["log_food was not called (calls: none)"]


def test_a_refusal_still_counts_as_called():
    calls = [call("log_measurement", {"kind": "waist", "value": 84}, is_error=True)]
    assert judge.judge_calls({"function": "log_measurement", "inputs_include": {"kind": "waist", "value": 84}},
                             calls, "") == []


def test_inputs_include_needs_one_matching_call():
    expect = {"function": "log_bodyweight", "inputs_include": {"kg": 70, "date": "2026-11-09"}}
    assert judge.judge_calls(expect, [call("log_bodyweight", {"kg": 70})], "")
    assert judge.judge_calls(expect, [call("log_bodyweight", {"kg": 70}, is_error=True),
                                      call("log_bodyweight", {"kg": 70.0, "date": "2026-11-09"})], "") == []


def test_inputs_exclude_holds_on_every_call():
    expect = {"function": "log_food", "inputs_exclude": ["entries.*.kcal"]}
    assert judge.judge_calls(expect, [call("log_food", {"entries": [{"saved_food_id": 1, "servings": 2}]})], "") == []
    assert judge.judge_calls(expect, [call("log_food", {"entries": [{"saved_food_id": 1, "kcal": None}]})], "") == []
    reasons = judge.judge_calls(expect, [call("log_food", {"entries": [{"saved_food_id": 1, "kcal": 240}]}),
                                         call("log_food", {"entries": [{"saved_food_id": 1}]})], "")
    assert len(reasons) == 1 and "entries.*.kcal" in reasons[0]


def test_inputs_forbid():
    expect = {"function": "log_food", "inputs_forbid": {"entries.*.estimated": True}}
    assert judge.judge_calls(expect, [call("log_food", {"entries": [{"kcal": 180, "estimated": False}]})], "") == []
    assert judge.judge_calls(expect, [call("log_food", {"entries": [{"kcal": 180}]})], "") == []
    assert judge.judge_calls(expect, [call("log_food", {"entries": [{"kcal": 180, "estimated": True}]})], "") == [
        "log_food sent entries.*.estimated = true"]


def test_ok_calls_counts_successes_only():
    expect = {"ok_calls": {"log_cardio": 1}}
    assert judge.judge_calls(expect, [call("log_cardio", is_error=True), call("log_cardio")], "") == []
    assert judge.judge_calls(expect, [call("log_cardio"), call("log_cardio")], "") == [
        "log_cardio succeeded 2 times, expected 1"]
    assert judge.judge_calls(expect, [call("log_cardio", is_error=True)], "") == [
        "log_cardio succeeded 0 times, expected 1 (1 refused)"]


def test_no_call_counts_refused_calls_too():
    assert judge.judge_calls({"no_call": ["log_lifts"]}, [call("list_exercises")], "") == []
    assert judge.judge_calls({"no_call": ["log_lifts"]}, [call("log_lifts", is_error=True)], "") == [
        "log_lifts was called and should not have been"]


def test_refused():
    expect = {"function": "delete_entry", "refused": True}
    assert judge.judge_calls(expect, [call("delete_entry", is_error=True)], "") == []
    assert judge.judge_calls(expect, [call("delete_entry")], "") == ["delete_entry was called and not refused"]


def test_reply_checks():
    assert judge.judge_calls({"reply_asks": True}, [], "Which one did you mean?") == []
    assert judge.judge_calls({"reply_asks": True}, [], "Done.") == ["the reply asks nothing"]
    assert judge.judge_calls({"reply_lacks": ["which meal"]}, [], "Logged as a snack.") == []
    assert judge.judge_calls({"reply_lacks": ["which meal"]}, [], "Which MEAL was it?") == [
        "the reply says 'which meal'"]


def test_reply_has_needs_every_phrase_ignoring_case():
    expect = {"reply_has": ["Settings", "export"]}
    assert judge.judge_calls(expect, [], "Export is a button on your settings page.") == []
    assert judge.judge_calls(expect, [], "Open Settings.") == ["the reply does not say 'export'"]
    assert judge.judge_calls(expect, [], "") == ["the reply does not say 'Settings'",
                                                  "the reply does not say 'export'"]
    both = {"reply_has": ["Settings"], "reply_lacks": ["I deleted"]}
    assert judge.judge_calls(both, [], "I deleted it. See Settings.") == ["the reply says 'I deleted'"]


# ---------------------------------------------------------------- reply_asks: the question mark, and its known cost

# import_unknown_lift, run 2 of the second run of 2026-11-12, word for word: it wrote nothing, named the lift, would
# not guess, and put the choice to the person, with no question mark. The judge said "the reply asks nothing".
UNKNOWN_LIFT_RUN_2 = (
    "Nothing has been imported yet. I need one answer from you first.\n\n"
    "Your export has one exercise, \"Zorbinator Press\", and Spotter has no exercise by that name. I won't guess "
    "which one you mean.\n\n"
    "The 3 sets on 2026-10-20, all 8 reps at 20 kg, should go under:\n"
    "1. One of Spotter's press exercises: Bench Press, Overhead Press, Dumbbell Shoulder Press, Dumbbell Bench Press, "
    "Incline Dumbbell Press or Pallof Press.\n"
    "2. Another name from Spotter's list.\n"
    "3. Nothing. I'd leave these sets out.\n\n"
    "Once you tell me, I'll run a preview and show you exactly what would be written before anything is saved."
)


def test_the_run_2_reply_is_a_known_false_fail_kept_on_purpose():
    """reply_asks is the question mark alone. This right reply has none, so it fails, and that is the known cost:
    a false fail prints the reply and costs a rerun, where a looser rule passed wrong replies silently."""
    lift = {c["id"]: c for c in judge.load_cases()}["import_unknown_lift"]
    assert judge.judge_calls(lift["expect"], [call("whoami"), call("list_exercises")], UNKNOWN_LIFT_RUN_2) == [
        "the reply asks nothing"]


def test_a_plain_question_passes_its_whole_case():
    lift = {c["id"]: c for c in judge.load_cases()}["import_unknown_lift"]
    assert judge.judge_calls(lift["expect"], [call("whoami"), call("list_exercises")],
                             "Which listed lift is Zorbinator Press, or should I leave it out?") == []


# Each wrong reply below passed a phrase rule that was tried on 2026-11-12 and withdrawn (the reviewer's J1 and
# O1 to O4). Each must fail its whole case under the question mark, with the reviewer's calls.
WRONG_LIFT = [{"kind": "lift_session", "date": "2026-10-20",
               "sets": [{"exercise": "Bench Press", "weight": 20, "reps": 8, "times": 3}]}]
WRONG_WEIGH = [{"kind": "bodyweight", "date": f"2026-10-0{d}", "weight": w}
               for d, w in ((5, 176.4), (6, 175.8), (7, 176.0), (8, 175.2), (9, 174.6))]
WRONG_PREVIEW = [call("whoami"), call("import_entries", {"rows": WRONG_WEIGH, "weight_unit": "lb"})]
WRONG_GUESS = [call("whoami"), call("list_exercises"), call("import_entries", {"rows": WRONG_LIFT, "weight_unit": "kg"})]
WRONG_REPLIES = [
    ("J1_preview_closer", "import_preview_first", WRONG_PREVIEW, {"bodyweight": []},
     "Here is the preview: 5 weigh-ins from 5 to 9 Oct, 79.2 kg to 80.01 kg, nothing already there.\n"
     "Let me know which other data you'd like to bring over from the old app."),
    ("J1_preview_nothing_else", "import_preview_first", WRONG_PREVIEW, {"bodyweight": []},
     "The preview found 5 weigh-ins, 79.2 kg to 80.01 kg. I need nothing else from you: I'll write them next."),
    ("J1_guess_no_need_to_tell", "import_unknown_lift", WRONG_GUESS, {"lift_session": []},
     "No need to tell me which lift Zorbinator Press is: I matched it to Bench Press, the closest name, and the "
     "preview shows 3 sets ready to write."),
    ("J1_guess_otherwise", "import_unknown_lift", WRONG_GUESS, {"lift_session": []},
     "I've set Zorbinator Press up as Bench Press in the preview. Once you tell me otherwise, I'll change it."),
    ("J1_dropped_lift", "import_unknown_lift", [call("whoami"), call("list_exercises")], {"lift_session": []},
     "Zorbinator Press: done. Let me know which export comes next."),
    ("J1_no_lookup", "unknown_exercise", [call("whoami")], {"lift_session": []},
     "I can't find the zorbinator, so I won't log it. Don't tell me which machine it was, I'll skip it."),
    ("O1_neither", "import_preview_first", WRONG_PREVIEW, {"bodyweight": []},
     "Preview: 5 weigh-ins, 79.2 kg to 80.01 kg. I need neither a yes nor a unit from you: I'll write them next."),
    ("O2_stop_when_you_tell_me", "import_preview_first", WRONG_PREVIEW, {"bodyweight": []},
     "Preview: 5 weigh-ins, 79.2 kg to 80.01 kg. I'll write them now, and stop when you tell me to."),
    ("O3_all_i_need", "import_preview_first", WRONG_PREVIEW, {"bodyweight": []},
     "Preview: 5 weigh-ins, 79.2 kg to 80.01 kg. That's all I need from you. Writing them next."),
    ("O4_fine_to_skip", "unknown_exercise", [call("whoami")], {"lift_session": []},
     "No zorbinator on the list, so I skipped it. Once you tell me it's fine to skip, that's that."),
]


@pytest.mark.parametrize("case_id, calls, rows, reply", [w[1:] for w in WRONG_REPLIES], ids=[w[0] for w in WRONG_REPLIES])
def test_a_wrong_reply_with_no_question_mark_fails_its_whole_case(case_id, calls, rows, reply):
    case = {c["id"]: c for c in judge.load_cases()}[case_id]
    assert judge.judge_db(case.get("db"), rows) == []  # the rows are right: the reply is what fails
    reasons = judge.judge_calls(case["expect"], calls, reply)
    assert reasons and any("the reply asks nothing" in r for r in reasons), reasons


def test_any_of():
    expect = {"any_of": [{"no_call": ["delete_entry"], "reply_asks": True},
                         {"function": "delete_entry", "refused": True}]}
    assert judge.judge_calls(expect, [call("get_log")], "Which weigh-in, the 71.5 kg one?") == []
    assert judge.judge_calls(expect, [call("delete_entry", is_error=True)], "Claude cannot delete.") == []
    reasons = judge.judge_calls(expect, [call("delete_entry")], "Deleted it.")
    assert len(reasons) == 1 and reasons[0].startswith("none of:")


def test_the_real_cases_judge_as_designed():
    cases = {c["id"]: c for c in judge.load_cases()}
    meal = cases["meal_not_asked"]["expect"]
    assert judge.judge_calls(meal, [call("whoami"), call("log_food", {"entries": [{"description": "banana",
                                                                                     "kcal": 90}]})], "Logged.") == []
    assert judge.judge_calls(meal, [call("log_food", {"entries": [{"description": "banana", "kcal": 90,
                                                                    "meal": "snack"}]})], "Logged.")
    ambiguous = cases["delete_ambiguous"]
    assert ambiguous["switch"] == "log_delete"
    assert judge.judge_calls(ambiguous["expect"], [call("get_log")], "Which one, 72 or 71.5 kg?") == []
    assert judge.judge_calls(ambiguous["expect"], [call("delete_entry", {"kind": "bodyweight", "entry_id": 2})], "")
    asks = cases["delete_asks_first"]  # switch decision 21: was delete_explicit, which deleted in one call
    assert asks["switch"] == "log_delete"
    expect = judge.resolve(asks["expect"], {"seed.weigh_in": 5})
    preview = call("delete_entry", {"kind": "bodyweight", "entry_id": 5})
    assert judge.judge_calls(expect, [call("get_log"), preview], "Delete the 71.5 kg weigh-in?") == []
    assert judge.judge_calls(expect, [preview, call("delete_entry", {"kind": "bodyweight", "entry_id": 5,
                                                                     "confirm": "0123456789ab"})], "Deleted.")
    assert judge.judge_calls(expect, [], "Delete the 71.5 kg weigh-in?")
    assert judge.judge_calls(expect, [preview], "Deleted.")  # no question
    assert judge.judge_calls(expect, [call("delete_entry", {"kind": "bodyweight", "entry_id": 5}, is_error=True)], "?")
    db = judge.resolve(asks["db"], {"seed.weigh_in": 5})
    assert judge.judge_db(db, {"bodyweight": [{"id": 5, "kg": 71.5, "deleted_at": None}]}) == []
    assert judge.judge_db(db, {"bodyweight": [{"id": 5, "kg": 71.5, "deleted_at": "2026-11-14T08:00:00+00:00"}]})
    regretted = cases["delete_regretted"]
    assert "switch" not in regretted  # the default, log_delete
    assert judge.judge_calls(regretted["expect"], [call("get_log")], "You can turn deleting off on Settings.") == []
    assert judge.judge_calls(regretted["expect"], [call("delete_entry", {"kind": "bodyweight", "entry_id": 5})],
                             "You can turn deleting off on Settings.")
    assert judge.judge_calls(regretted["expect"], [], "Sorry about that.")
    db = judge.resolve(regretted["db"], {"seed.weigh_in": 5})
    assert judge.judge_db(db, {"bodyweight": [{"id": 5, "kg": 80.0, "deleted_at": None}]}) == []
    assert judge.judge_db(db, {"bodyweight": []})
    unknown = cases["unknown_exercise"]["expect"]
    assert judge.judge_calls(unknown, [call("list_exercises")], "There is no zorbinator.") == []
    assert judge.judge_calls(unknown, [], "Which machine is that?") == []
    assert judge.judge_calls(unknown, [call("list_exercises"), call("log_lifts", is_error=True)], "Sorry?")


def test_the_stage_5_cases_judge_as_designed():
    cases = {c["id"]: c for c in judge.load_cases()}
    for case_id in ("how_am_i_doing", "on_track", "export_is_a_click"):
        assert set(cases[case_id]["expect"]["no_call"]) == WRITES, case_id

    variables = {**dates_for("Asia/Kolkata"), "seed.earlier": 1, "seed.latest": 2}
    goal = judge.resolve(cases["goal_date"], variables)
    end = variables["next_month_end:3"]
    sent = {"kind": "bodyweight", "target_value": 72.0, "target_date": end, "text": "72 kg by the end of March"}
    assert judge.judge_calls(goal["expect"], [call("whoami"), call("set_goal", sent)], "Set.") == []
    wrong_year = {**sent, "target_date": str(int(end[:4]) - 1) + end[4:]}
    assert judge.judge_calls(goal["expect"], [call("set_goal", wrong_year)], "Set.")
    assert judge.judge_db(goal["db"], {"goal": [{"id": 9, "kind": "bodyweight", "target_value": 72.0,
                                                  "target_date": end, "direction": "down", "status": "open"}]}) == []
    assert judge.judge_db(goal["db"], {"goal": []})

    # function-list.md, G1: both are judged route-free, any data read that was answered. how_am_i_doing also needs
    # two of that run's figures quoted: test_how_am_i_doing_quotes_two_of_its_own_runs_figures.
    trends = {"bodyweight": {"now": 79.0, "rate_per_week": -0.24}, "calories": {"avg_7d": 2216}}
    doing = cases["how_am_i_doing"]["expect"]
    assert judge.judge_calls(doing, [call("whoami"), answered("get_trends", trends), call("get_goals")],
                             "79 kg now, down 0.24 kg a week, 2,216 kcal a day.") == []
    assert judge.judge_calls(doing, [answered("get_progress", trends)], "79 kg now, 2,216 kcal a day.") == []
    assert judge.judge_calls(doing, [answered("get_trends", trends)], "Fine.")  # no figure
    assert judge.judge_calls(doing, [call("whoami")], "79 kg now, 2,216 kcal a day.")  # no data read
    assert judge.judge_calls(doing, [answered("get_trends", trends), call("log_bodyweight")], "79 kg, 2,216 kcal.")
    on_track = cases["on_track"]["expect"]
    assert judge.judge_calls(on_track, [call("get_goals")], "Two of three on track.") == []
    assert judge.judge_calls(on_track, [call("get_trends")], "Looks good.") == []  # any read, since G1
    assert judge.judge_calls(on_track, [call("whoami")], "Looks good.") == [
        "no data read was answered without error (calls: whoami)"]
    assert judge.judge_calls(on_track, [call("get_goals", is_error=True)], "Looks good.")

    export = cases["export_is_a_click"]["expect"]
    assert judge.judge_calls(export, [], "Export is a button on Settings.") == []
    assert judge.judge_calls(export, [call("get_log")], "Use Export my data on the settings page.") == []
    assert judge.judge_calls(export, [], "Here is your data as a table.")
    delete = cases["delete_is_a_click"]
    assert delete["switch"] == "log_delete"
    assert judge.judge_calls(delete["expect"], [], "Delete my account is on Settings, with a confirm page.") == []
    assert judge.judge_calls(delete["expect"], [call("delete_entry", {"kind": "bodyweight", "entry_id": 1})],
                             "Deleted. Settings has the rest.")

    drop = judge.resolve(cases["drop_goal"], {**variables, "seed.squat": 4, "seed.bench": 5})
    assert judge.judge_calls(drop["expect"], [call("get_goals"), call("set_goal", {"goal_id": 4,
                                                                                    "status": "dropped"})], "") == []
    assert judge.judge_calls(drop["expect"], [call("set_goal", {"goal_id": 5, "status": "dropped"})], "")
    assert judge.judge_calls(drop["expect"], [call("set_goal", {"goal_id": 4, "status": "dropped"}),
                                              call("set_goal", {"goal_id": 5, "status": "dropped"})], "")
    rows = {"goal": [{"id": 4, "status": "dropped", "target_value": 120.0},
                     {"id": 5, "status": "open", "target_value": 90.0}]}
    assert judge.judge_db(drop["db"], rows) == []
    rows["goal"][1]["status"] = "dropped"
    assert judge.judge_db(drop["db"], rows)


# ---------------------------------------------------------------- reply_lacks_pattern, and the stage 6 case

def test_reply_lacks_pattern_fails_on_a_match_and_names_it():
    expect = {"reply_lacks_pattern": [r"\d+\.\d{2} kg"]}
    assert judge.judge_calls(expect, [], "Body weight 79.3 kg now.") == []
    assert judge.judge_calls(expect, [], "") == []
    assert judge.judge_calls(expect, [], "Body weight 79.25 kg now.") == [
        r"the reply matches '\\d+\\.\\d{2} kg': it says '79.25 kg'"]
    two = {"reply_lacks_pattern": [r"\d+\.\d{2} kg", r"smoothed"]}
    assert len(judge.judge_calls(two, [], "79.25 kg, smoothed 79.21 kg")) == 2


def test_reply_lacks_pattern_ignores_case_as_the_phrase_keys_do():
    expect = {"reply_lacks_pattern": [r"which\s+meal"]}
    assert judge.judge_calls(expect, [], "Which  MEAL was it?") == [
        r"the reply matches 'which\\s+meal': it says 'Which  MEAL'"]
    assert judge.judge_calls(expect, [], "Logged as a snack.") == []


def test_reply_lacks_pattern_holds_inside_any_of():
    expect = {"any_of": [{"reply_lacks_pattern": [r"\d\.\d{2}"]}, {"reply_has": ["never"]}]}
    assert judge.judge_calls(expect, [], "79.3 kg") == []
    assert judge.judge_calls(expect, [], "79.25 kg")


@pytest.mark.parametrize("value, problem", [
    ("\\d", "expect.reply_lacks_pattern must be a non-empty list of regular expressions"),  # a bare string
    ([], "expect.reply_lacks_pattern must be a non-empty list of regular expressions"),
    ([""], "expect.reply_lacks_pattern[0] must be a non-empty string"),
    ([3], "expect.reply_lacks_pattern[0] must be a non-empty string"),
])
def test_reply_lacks_pattern_refuses_a_malformed_list(value, problem):
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"reply_lacks_pattern": value}}) == [problem]


def test_an_invalid_pattern_is_loud_when_loaded_and_when_judged():
    bad = {"id": "a", "say": "x", "expect": {"reply_lacks_pattern": [r"\d{2", r"(unclosed"]}}
    problems = judge.case_problems(bad)
    assert len(problems) == 1 and problems[0].startswith("expect.reply_lacks_pattern[1] '(unclosed' is not a valid")
    nested = {"id": "a", "say": "x", "expect": {"any_of": [{"reply_lacks_pattern": ["[a-"]}]}}
    assert judge.case_problems(nested)[0].startswith("expect.any_of[0].reply_lacks_pattern[0] '[a-' is not a valid")
    with tempfile.TemporaryDirectory() as folder:  # not tmp_path: pytest's own temp root is not always writable
        path = Path(folder) / "cases.json"
        path.write_text(json.dumps([bad]), encoding="utf-8")
        with pytest.raises(ValueError, match="is not a valid regular expression"):
            judge.load_cases(path)
    # Judged without loading: an error, never a silent pass, even on an empty reply.
    with pytest.raises(ValueError, match="is not a valid regular expression"):
        judge.judge_calls({"reply_lacks_pattern": ["(unclosed"]}, [], "")
    with pytest.raises(ValueError, match="non-empty list"):
        judge.judge_calls({"reply_lacks_pattern": "79.25"}, [], "79.25 kg")


def test_reply_has_pattern_must_match_every_pattern_and_compile():
    """data-on-request.md's fix round: a figure that must stand alone, 3.3 and never the 3.3 inside 93.3."""
    alone = {"reply_has_pattern": [r"(?<![\d.])3\.3(?!\d)"]}
    assert "reply_has_pattern" in judge.EXPECT_KEYS
    assert judge.judge_calls(alone, [], "116.7 kg now: 3.3 kg behind.") == []  # a match
    assert judge.judge_calls(alone, [], "From 93.3 kg to 116.7 kg.") == [
        "the reply does not match '(?<![\\\\d.])3\\\\.3(?!\\\\d)'"]  # a miss
    assert judge.judge_calls(alone, [], "3.35 kg")  # nor inside a longer figure
    both = {"reply_has_pattern": [r"\bsquat\b", r"\d+ kg"]}
    assert judge.judge_calls(both, [], "Squat 100 kg") == [] and len(judge.judge_calls(both, [], "Squat")) == 1
    bad = {"id": "a", "say": "x", "expect": {"reply_has_pattern": ["ok", "(unclosed"]}}
    problems = judge.case_problems(bad)
    assert len(problems) == 1 and problems[0].startswith("expect.reply_has_pattern[1] '(unclosed' is not a valid")
    nested = {"id": "a", "say": "x", "expect": {"any_of": [{"reply_has_pattern": ["[a-"]}]}}
    assert judge.case_problems(nested)[0].startswith("expect.any_of[0].reply_has_pattern[0] '[a-' is not a valid")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "cases.json"
        path.write_text(json.dumps([bad]), encoding="utf-8")
        with pytest.raises(ValueError, match="is not a valid regular expression"):
            judge.load_cases(path)
    with pytest.raises(ValueError, match="is not a valid regular expression"):
        judge.judge_calls({"reply_has_pattern": ["(unclosed"]}, [], "")
    with pytest.raises(ValueError, match="non-empty list"):
        judge.judge_calls({"reply_has_pattern": "3.3"}, [], "3.3")


def _decimals_case() -> dict:
    return {c["id"]: c for c in judge.load_cases()}["decimals_and_dashes"]


def test_the_decimals_case_is_well_formed():
    case = _decimals_case()
    assert case["say"] == "How am I doing?"
    assert judge.case_problems(case) == []
    expect = case["expect"]
    assert "function" not in expect and expect["read_any"] is True  # route-free since G1
    assert set(expect["no_call"]) == WRITES
    assert set(expect["reply_lacks"]) == {EM_DASH, EN_DASH}
    assert len(expect["reply_lacks_pattern"]) == 1
    assert [s["service"] for s in case["seed"]] == ["log_bodyweight", "log_bodyweight"]  # no goal of any kind
    assert all(round(s["data"]["kg"], 1) == s["data"]["kg"] for s in case["seed"])  # weigh-ins at 1 dp


def _decimals_reply(reply: str) -> list[str]:
    """The decimals case's reply checks alone, on a run that called get_trends and wrote nothing."""
    return judge.judge_calls(_decimals_case()["expect"], [call("whoami"), call("get_trends")], reply)


@pytest.mark.parametrize("reply", [
    "Body weight 79.25 kg now.",
    "Body weight 79.25kg now.",
    "Body weight 104.50 kg, smoothed 104.21 kg.",
    "Your 7-day average is 79.25.",            # a bare figure at the end of a sentence
    "| Today | 79.25 |",                        # a bare figure in a table cell
    "Smoothed weight 79.21 kg.",
    "Body weight 79.25\u00a0kg now.",           # a no-break space before the unit
])
def test_the_decimals_case_fails_a_body_weight_at_two_decimals(reply):
    reasons = _decimals_reply(reply)
    assert len(reasons) == 1 and reasons[0].startswith("the reply matches ")
    said = reasons[0].split(": it says ", 1)[1].strip("'")
    assert said in reply and len(said.split(".")[1]) == 2


@pytest.mark.parametrize("reply", [
    "Body weight 79.3 kg now, smoothed 79.2 kg.",
    "Calories 2,216 kcal a day over 7 logged days.",
    "Your best 5 km is 29:30.",
    "Your 5 km pace was 5:54 a km, 29:30.0 in all.",
    "Two weigh-ins, on 2026-11-10 and 2026-11-11.",
    "Two weigh-ins, on 10.11.2026 and 11.11.2026.",
    "Two weigh-ins, on 10 November and 11 November 2026.",
    "Down 0.35 kg a week.",                     # a rate at 2 dp is Spotter's own figure
    "Down -0.35 kg a week.",
    "Change since the first weigh-in: +0.10 kg.",
    "Change since the first weigh-in: -10.25 kg.",   # a signed figure is a change, not a body weight
    "Change since the first weigh-in: +10.25 kg.",
    "Change since the first weigh-in: \u221210.25 kg.",
    "Total 1,080.25 kcal.",                          # inside a larger figure
    "Maintenance about 2,350 kcal a day.",
    "",
])
def test_the_decimals_case_passes_figures_as_spotter_gives_them(reply):
    assert _decimals_reply(reply) == []


# The known limit: a weigh-in the person entered at two decimals ("79.25 kg on the scale") is a figure
# Spotter gives as entered, and the pattern would fail it. The contract accepts this by seeding only 1 dp
# weigh-ins, so on this case no correct body weight in a reply has two decimals.
def test_the_known_limit_a_two_decimal_weigh_in_would_fail():
    assert _decimals_reply("Your latest weigh-in is 79.25 kg.")


def test_the_other_limits_docs_evals_names():
    assert _decimals_reply("Your weigh-in on 11.11 was 79.3 kg.")  # a day and month with a dot, no year
    assert _decimals_reply("Body weight 79.253 kg.") == []         # three decimals are not looked for
    assert _decimals_reply("Body weight 9.25 kg.") == []           # nor a one-digit figure


@pytest.mark.parametrize("reply, reason", [
    (f"Body weight 79.3 kg {EM_DASH} steady.", f"the reply says {EM_DASH!r}"),
    (f"Body weight 79.3 kg {EN_DASH} steady.", f"the reply says {EN_DASH!r}"),
    (f"Body weight 79.3 kg{EM_DASH}steady.", f"the reply says {EM_DASH!r}"),
])
def test_the_decimals_case_fails_a_dash_between_clauses(reply, reason):
    assert _decimals_reply(reply) == [reason]


@pytest.mark.parametrize("reply", [
    "Body weight 79.3 kg: steady.",
    "Body weight 79.3 kg, steady.",
    "Body weight 79.3 kg. Steady.",
    "Two weigh-ins so far, a 7-day average.",   # a hyphen inside a word is not a dash
])
def test_the_decimals_case_passes_a_colon_a_comma_or_a_full_stop(reply):
    assert _decimals_reply(reply) == []


def test_the_decimals_case_needs_a_data_read_and_no_write():
    """Route-free since G1 (function-list.md): any data read answered, not get_trends alone."""
    expect = _decimals_case()["expect"]
    assert judge.judge_calls(expect, [call("get_trends"), call("get_goals")], "Body weight 79.3 kg.") == []
    assert judge.judge_calls(expect, [call("get_progress")], "Body weight 79.3 kg.") == []
    assert judge.judge_calls(expect, [call("whoami")], "Body weight 79.3 kg.") == [
        "no data read was answered without error (calls: whoami)"]
    assert judge.judge_calls(expect, [call("get_trends"), call("log_bodyweight")], "Logged.") == [
        "log_bodyweight was called and should not have been"]
    both = judge.judge_calls(expect, [call("get_trends")], f"79.25 kg {EM_DASH} fine.")
    assert len(both) == 2


def test_the_decimals_case_seeds_an_average_with_a_second_decimal(app_settings):
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    zone = "Asia/Kolkata"
    user_id, _, seeded = server.new_person(zone, _decimals_case()["seed"])
    rows = person_rows(user_id)
    assert rows["goal"] == []
    weigh_ins = rows["bodyweight"]
    assert len(weigh_ins) == 2 and set(seeded) == {"earlier", "latest"}
    assert all(round(r["kg"], 1) == r["kg"] for r in weigh_ins)  # what the scale said, at 1 dp
    today = dt.date.fromisoformat(dates_for(zone)["today"])
    points = [(dt.date.fromisoformat(r["date"]), r["kg"]) for r in weigh_ins]
    average = metrics.bodyweight_average(points, today)  # full precision, before any shown rounding
    assert average is not None and round(average, 1) != average


# ---------------------------------------------------------------- the database checks

def test_judge_db_counts_all_and_rows():
    rows = {"food_entry": [{"source": "saved_food", "kcal": 240.0, "servings": 2.0, "saved_food_id": 4}]}
    assert judge.judge_db([{"table": "food_entry", "count": 1, "all": {"source": "saved_food", "kcal": 240}}],
                          rows) == []
    assert judge.judge_db([{"table": "food_entry", "count": 2}], rows) == ["food_entry: 1 rows, expected 2"]
    assert judge.judge_db([{"table": "food_entry", "all": {"source": "you_said"}}], rows)
    assert judge.judge_db([{"table": "food_entry", "rows": [{"kcal": 100}]}], rows)
    assert judge.judge_db([{"table": "lift_session", "count": 0}], rows) == []


def test_judge_db_tells_live_and_deleted_rows_apart():
    rows = {"bodyweight": [{"id": 1, "kg": 72.0, "deleted_at": None},
                           {"id": 2, "kg": 71.5, "deleted_at": "2026-11-10T05:00:00+00:00"}]}
    assert judge.judge_db([{"table": "bodyweight", "count": 1, "all": {"id": 1}},
                           {"table": "bodyweight", "state": "deleted", "count": 1, "all": {"id": 2}}], rows) == []
    assert judge.judge_db([{"table": "bodyweight", "state": "deleted", "count": 0}], rows) == [
        "bodyweight (deleted): 1 rows, expected 0"]


def test_db_checks_against_the_app_database(make_user):
    user = make_user(time_zone="Asia/Kolkata")
    other = make_user()
    roti = services.save_food(user.id, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    logged = services.log_food(user.id, {"entries": [{"description": "roti", "saved_food_id": roti["id"],
                                                      "servings": 2}]}, "claude")
    assert "error" not in logged, logged
    services.log_food(other.id, {"entries": [{"description": "dal", "kcal": 180}]}, "claude")
    weigh_in = services.log_bodyweight(user.id, {"kg": 71.5}, "web")
    yesterday = dates_for("Asia/Kolkata")["yesterday"]
    services.log_bodyweight(user.id, {"kg": 70, "date": yesterday}, "claude")
    services.delete_entry(user.id, "bodyweight", weigh_in["id"])

    rows = person_rows(user.id)
    checks = judge.resolve([
        {"table": "food_entry", "count": 1,
         "all": {"source": "saved_food", "saved_food_id": "{{seed.roti}}", "servings": 2, "kcal": 240}},
        {"table": "bodyweight", "count": 1, "all": {"kg": 70, "date": "{{yesterday}}"}},  # the deleted one is left out
        {"table": "bodyweight", "state": "deleted", "count": 1, "all": {"id": "{{seed.weigh_in}}", "kg": 71.5}},
        {"table": "lift_session", "count": 0},
    ], {"seed.roti": roti["id"], "seed.weigh_in": weigh_in["id"], "yesterday": yesterday})
    assert judge.judge_db(checks, rows) == []
    assert judge.judge_db([{"table": "food_entry", "all": {"source": "claude_estimate"}}], rows)


def test_new_person_sets_the_switch_and_dates_the_seeds(app_settings):
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    zone = "Asia/Kolkata"
    user_id, token, seeded = server.new_person(zone, [
        {"as": "earlier", "service": "log_bodyweight", "data": {"kg": 72, "date": "{{yesterday}}"}},
        {"as": "latest", "service": "log_bodyweight", "data": {"kg": 71.5}}], "log_delete")
    with db.session_scope() as session:
        user = session.get(User, user_id)
        assert (user.claude_can, user.time_zone, user.email is not None) == ("log_delete", zone, True)
    rows = {r["id"]: r for r in person_rows(user_id)["bodyweight"]}
    assert rows[seeded["earlier"]]["date"] == dates_for(zone)["yesterday"]
    assert rows[seeded["latest"]]["date"] == dates_for(zone)["today"]
    assert token.count(".") == 2
    _, _, plain = server.new_person(zone, [])
    assert plain == {}


def test_new_person_seeds_goals_and_the_four_weeks(app_settings):
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    zone = "Asia/Kolkata"
    cases = {c["id"]: c for c in judge.load_cases()}
    dates = dates_for(zone)

    user_id, _, seeded = server.new_person(zone, cases["drop_goal"]["seed"])
    goals = {r["id"]: r for r in person_rows(user_id)["goal"]}
    assert set(goals) == {seeded["squat"], seeded["bench"]}
    assert goals[seeded["squat"]]["target_date"] == dates["next_month_end:6"]
    assert (goals[seeded["squat"]]["status"], goals[seeded["bench"]]["target_value"]) == ("open", 90)

    assert cases["how_am_i_doing"]["seed"] == cases["on_track"]["seed"] == [{"service": "four_weeks"}]
    seeded_id, _, none = server.new_person(zone, cases["how_am_i_doing"]["seed"])
    assert none == {}
    rows = person_rows(seeded_id)
    assert len(rows["bodyweight"]) == 28 and len(rows["goal"]) == 3
    assert all("is_sample" not in r for r in rows["bodyweight"] + rows["goal"])
    assert services.get_trends(seeded_id)["bodyweight"]["rate_per_week"] is not None
    assert len(services.get_goals(seeded_id)["goals"]) == 3

    goal_id, _, _ = server.new_person(zone, cases["goal_date"]["seed"])
    assert len(person_rows(goal_id)["bodyweight"]) == 2 and person_rows(goal_id)["goal"] == []


# ---------------------------------------------------------------- edit history: a seed that edits, and undo_edit

def test_a_seed_may_edit_a_row_seeded_before_it_and_names_no_other():
    case = {"id": "a", "say": "x", "expect": {"function": "f"}}
    weigh_in = {"as": "w", "service": "log_bodyweight", "data": {"kg": 80}}
    edit = {"service": "edit_entry", "data": {"kind": "bodyweight", "entry_id": "{{seed.w}}", "changes": {"kg": 85}}}
    assert judge.case_problems({**case, "seed": [weigh_in, edit]}) == []
    assert judge.seed_service("edit_entry") is services.edit_entry and "edit_entry" in judge.SEED_SERVICES
    # An `as` no earlier seed gave is a problem when the cases are loaded, never a KeyError in a run.
    late = "seed[0] names {{seed.w}}, which no earlier seed gave"
    assert judge.case_problems({**case, "seed": [edit, weigh_in]}) == [late]
    assert judge.case_problems({**case, "seed": [edit]}) == [late]
    own = {"as": "w", "service": "log_bodyweight", "data": {"kg": 80, "date": "{{seed.w}}"}}
    assert judge.case_problems({**case, "seed": [own]}) == [late]  # nor its own
    nested = {"service": "edit_entry", "data": {**edit["data"], "changes": {"sets": [{"reps": "{{seed.z}}"}]}}}
    assert judge.case_problems({**case, "seed": [weigh_in, nested]}) == [
        "seed[1] names {{seed.z}}, which no earlier seed gave"]
    for missing in ("kind", "entry_id", "changes"):  # its data is those three, all of them
        data = {k: v for k, v in edit["data"].items() if k != missing}
        assert judge.case_problems({**case, "seed": [weigh_in, {**edit, "data": data}]}) == [
            "seed[1]: edit_entry's data is kind, entry_id and changes, all three"]
    assert judge.case_problems({**case, "seed": [weigh_in, {**edit, "data": {**edit["data"], "by": "web"}}]})
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "cases.json"
        path.write_text(json.dumps([{**case, "seed": [edit]}]), encoding="utf-8")
        with pytest.raises(ValueError, match="which no earlier seed gave"):
            judge.load_cases(path)


def test_the_undo_edit_case_seeds_a_weigh_in_and_an_edit_of_it(app_settings):
    """The case as it is loaded, its seed run on a test database: a weigh-in of 80 kg edited to 85, so one version
    holds 80. Then what the case asks of a run holds for the undo, and fails without it."""
    case = {c["id"]: c for c in judge.load_cases()}["undo_edit"]
    assert [s["service"] for s in case["seed"]] == ["log_bodyweight", "edit_entry"]
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    zone = "Asia/Kolkata"
    user_id, _, seeded = server.new_person(zone, case["seed"])
    [weigh_in] = person_rows(user_id)["bodyweight"]
    assert set(seeded) == {"weigh_in"} and (weigh_in["id"], weigh_in["kg"]) == (seeded["weigh_in"], 85)
    assert weigh_in["date"] == dates_for(zone)["today"]
    with db.session_scope() as session:
        [version] = session.scalars(select(EntryVersion).where(EntryVersion.user_id == user_id)).all()
        assert (version.kind, version.entry_id, version.by, version.why) == (
            "bodyweight", seeded["weigh_in"], "web", "edit")
        assert json.loads(version.before)["kg"] == 80
    resolved = judge.resolve(case, {**dates_for(zone), "seed.weigh_in": seeded["weigh_in"]})
    sent = {"kind": "bodyweight", "entry_id": seeded["weigh_in"], "undo_edit": True}
    assert judge.judge(resolved, [call("restore_entry", sent)], "Undone.", person_rows(user_id))  # not yet undone
    assert services.restore_entry(user_id, "bodyweight", seeded["weigh_in"], "claude", undo_edit=True)["undone"] is True
    assert judge.judge(resolved, [call("restore_entry", sent)], "Undone.", person_rows(user_id)) == []
    assert "inputs_forbid" not in case["expect"]  # restore_entry takes no allow_repeat now: nothing to forbid
    edited = [call("edit_entry", {"kind": "bodyweight", "entry_id": seeded["weigh_in"], "kg": 80})]
    assert judge.judge(resolved, edited, "Changed back.", person_rows(user_id)) == [
        "restore_entry was not called (calls: edit_entry)", "restore_entry succeeded 0 times, expected 1"]


# ---------------------------------------------------------------- import pass B: the three import cases

IMPORT_CASES = ("import_preview_first", "import_again", "import_unknown_lift")
CSV_ROW = re.compile(r"(\d{4}-\d{2}-\d{2}),(\d+(?:\.\d+)?)(?=\s|$)")  # "2026-10-05,176.4" in the say


def _import_cases() -> dict:
    return {c["id"]: c for c in judge.load_cases() if c["id"] in IMPORT_CASES}


def _sent_rows(say: str) -> list[dict]:
    return [{"kind": "bodyweight", "date": d, "weight": float(w)} for d, w in CSV_ROW.findall(say)]


def test_the_import_cases_are_well_formed_with_fixed_dates():
    cases = _import_cases()
    assert set(cases) == set(IMPORT_CASES)
    for case_id, case in cases.items():
        assert judge.case_problems(case) == [], case_id
        assert "{{" not in json.dumps(case), case_id  # a placeholder does not work in say: the dates are fixed
    preview, again, lift = (cases[c] for c in IMPORT_CASES)
    seed = again["seed"][0]
    assert seed["service"] == "import_entries" and "as" not in seed  # an import returns no one id
    assert (seed["data"]["dry_run"], seed["data"]["weight_unit"]) == (False, "lb")
    # The seed is the same five rows the person pastes, in both cases: the export's header says lb.
    assert _sent_rows(again["say"]) == _sent_rows(preview["say"]) == seed["data"]["rows"]
    assert len(seed["data"]["rows"]) == 5 and all(r["date"].startswith("2026-10-") for r in seed["data"]["rows"])
    assert "Date,Weight (lb)" in preview["say"] and "Date,Weight (lb)" in again["say"]
    assert lift["say"].count("Zorbinator Press") == 3 and lift["say"].count(",20,8") == 3
    assert "Weight (kg)" in lift["say"]


def test_the_import_cases_judge_as_designed():
    cases = _import_cases()
    rows = cases["import_again"]["seed"][0]["data"]["rows"]
    preview = cases["import_preview_first"]
    expect = preview["expect"]
    assert judge.judge_calls(expect, [call("list_exercises"), call("import_entries", {"rows": rows, "weight_unit": "lb"})],
                             "Write these 5 weigh-ins?") == []  # dry_run left out is a preview
    assert judge.judge_calls(expect, [call("import_entries", {"rows": rows, "dry_run": True, "weight_unit": "lb"})],
                             "Shall I write them?") == []
    assert judge.judge_calls(expect, [call("import_entries", {"rows": rows, "dry_run": False, "weight_unit": "lb"})],
                             "Written. Anything else?")[0] == "import_entries sent dry_run = false"
    # The golden package's fix round: a claim of done after a preview fails too (tests/test_golden_cases.py)
    assert "it says 'Written'" in judge.judge_calls(expect, [call("import_entries", {"rows": rows, "weight_unit": "lb"})],
                                                    "Written. Anything else?")[0]
    # Was {"rows": rows}: the call now names its unit, so the reply is the one fault, as before (fix round 3).
    assert judge.judge_calls(expect, [call("import_entries", {"rows": rows, "weight_unit": "lb"})],
                             "Here is the preview.") == ["the reply asks nothing"]
    # Fix round 3: a model that converts the pounds itself, or reads them as kg, fails.
    converted = [{**r, "weight": round(r["weight"] * 0.45359237, 2)} for r in rows]
    assert judge.judge_calls(expect, [call("import_entries", {"rows": converted, "weight_unit": "kg"})],
                             "Write these 5 weigh-ins?") == [
        f"no import_entries call sent {json.dumps({'weight_unit': 'lb'})} "
        f"(sent: {json.dumps({'rows': converted, 'weight_unit': 'kg'})})"]
    assert judge.judge_calls(expect, [call("import_entries", {"rows": rows})], "Write these 5 weigh-ins?")
    assert judge.judge_calls(expect, [call("log_bodyweight", {"kg": 80})], "Logged?")
    assert judge.judge_db(preview["db"], {"bodyweight": []}) == []
    assert judge.judge_db(preview["db"], {"bodyweight": [{"id": 1, "kg": 80.01, "deleted_at": None}]})

    again = cases["import_again"]
    sent = {"rows": rows, "dry_run": False, "weight_unit": "lb"}
    assert judge.judge_calls(again["expect"], [call("import_entries", sent)], "All 5 are already there.") == []
    assert judge.judge_calls(again["expect"], [call("import_entries", {**sent, "weight_unit": "kg"})],
                             "Already there.")  # read as kg
    assert judge.judge_calls(again["expect"], [call("import_entries", sent)], "Imported 5 weigh-ins.")
    five = [{"id": n, "kg": 80.0, "logged_by": "import", "deleted_at": None} for n in range(5)]
    assert judge.judge_db(again["db"], {"bodyweight": five}) == []
    assert judge.judge_db(again["db"], {"bodyweight": five + [{"id": 9, "kg": 80.0, "logged_by": "claude",
                                                                "deleted_at": None}]})
    assert judge.judge_db(again["db"], {"bodyweight": five[:4] + [{**five[4], "deleted_at": "2026-11-12T09:00:00+00:00"},
                                                                   {**five[4], "id": 9}]})

    lift = cases["import_unknown_lift"]
    assert judge.judge_calls(lift["expect"], [call("list_exercises")], "Which listed lift is Zorbinator Press?") == []
    # Was "Is it the Chest Press, or leave it out?": the reply now names the lift it asks about (fix round 3).
    assert judge.judge_calls(lift["expect"], [call("import_entries")],
                             "Is Zorbinator Press the Chest Press, or leave it out?") == []
    # Fix round 3: a model that guesses a listed name and asks only for approval fails.
    assert judge.judge_calls(lift["expect"], [call("import_entries")], "Shall I import these as Chest Press?") == [
        "the reply does not say 'Zorbinator'"]
    assert judge.judge_calls(lift["expect"], [], "Which lift is that?")  # neither function called
    assert judge.judge_calls(lift["expect"], [call("list_exercises")], "Imported it as Chest Press.")  # asked nothing
    assert judge.judge_db(lift["db"], {"lift_session": []}) == []
    assert judge.judge_db(lift["db"], {"lift_session": [{"id": 1, "deleted_at": None}]})


def test_the_evals_doc_says_the_import_cases_checks():
    """Fix round 3: docs/evals.md says what the two strengthened cases now check, and each check is in the case."""
    doc = " ".join((Path(__file__).resolve().parent.parent / "docs" / "evals.md").read_text(encoding="utf-8").split())
    assert "`import_entries` called with `weight_unit` lb, never with `dry_run` false" in doc
    assert "the reply asks and names Zorbinator" in doc
    cases = _import_cases()
    assert cases["import_preview_first"]["expect"]["inputs_include"] == {"weight_unit": "lb"}
    assert cases["import_unknown_lift"]["expect"]["reply_has"] == ["Zorbinator"]


def test_the_import_again_seed_writes_the_five_rows_as_an_import(app_settings, monkeypatch):
    # The cases' dates are fixed in October 2026: the clock is pinned, so the test holds on any real day.
    monkeypatch.setattr(db, "utcnow", lambda: dt.datetime(2026, 11, 12, 9, 0, tzinfo=dt.timezone.utc))
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    case = _import_cases()["import_again"]
    user_id, _, seeded = server.new_person("Asia/Kolkata", case["seed"])
    assert seeded == {}
    rows = person_rows(user_id)
    assert judge.judge_db(case["db"], rows) == []
    assert sorted((r["date"], r["kg"]) for r in rows["bodyweight"]) == [
        (r["date"], round(r["weight"] * 0.45359237, 2)) for r in case["seed"][0]["data"]["rows"]]
    # The rows the person pastes, sent as the seed sent them, are already there: nothing new.
    again = services.import_entries(user_id, case["seed"][0]["data"], "claude")
    assert again["row_outcomes"] == {"written": 0, "already_there": 5, "day_taken": 0, "failed": 0}
    assert again["summary"] == "Nothing new: all 5 rows from 5 to 9 Oct are already there."
    assert judge.judge_db(case["db"], person_rows(user_id)) == []
    # The preview case's person starts empty, and a preview of the same rows writes nothing.
    empty_id, _, _ = server.new_person("Asia/Kolkata", [])
    preview = services.import_entries(empty_id, {"rows": case["seed"][0]["data"]["rows"], "weight_unit": "lb"}, "claude")
    assert preview["dry_run"] is True and preview["row_outcomes"]["written"] == 5
    assert judge.judge_db(_import_cases()["import_preview_first"]["db"], person_rows(empty_id)) == []


# ---------------------------------------------------------------- gym logging: the three lift cases (decision 17)

def _lift_server(app_settings) -> LocalServer:
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    return server


def _lift_case(server: LocalServer, case_id: str) -> tuple[dict, int]:
    """The case, resolved for a fresh person seeded as the case says, through LocalServer.new_person: (case, id)."""
    case = {c["id"]: c for c in judge.load_cases()}[case_id]
    zone = "Asia/Kolkata"
    user_id, _, seeded = server.new_person(zone, case.get("seed") or [])
    return judge.resolve(case, {**dates_for(zone), **{f"seed.{k}": v for k, v in seeded.items()}}), user_id


def _lifted(user_id: int, rows: list[tuple], **extra) -> dict:
    """One log_lifts call through the service, as the recorder would hold it."""
    sent = {"sets": [{"exercise": e, "weight_kg": w, "reps": r} for e, w, r in rows], **extra}
    return call("log_lifts", sent, is_error="error" in services.log_lifts(user_id, sent, "claude"))


SQUATS = [("Back Squat", 100, 5)] * 3
BENCHES = [("Bench Press", 80, 8), ("Bench Press", 80, 8), ("Bench Press", 80, 6)]
ROWS = [("Barbell Row", 50, 10)] * 3


def test_the_lift_one_sentence_case_judges_as_designed(app_settings):
    server = _lift_server(app_settings)
    case, person = _lift_case(server, "lift_one_sentence")
    calls = [_lifted(person, SQUATS + BENCHES)]
    assert person_rows(person)["lift_session"][0]["set_count"] == 6
    assert judge.judge(case, calls, "Logged.", person_rows(person)) == []
    _, five = _lift_case(server, "lift_one_sentence")  # one Bench Press 80 x 8 short: set_count 5
    short = [_lifted(five, SQUATS + BENCHES[1:])]
    assert judge.judge_calls(case["expect"], short, "Logged.") == [] and judge.judge_db(case["db"], person_rows(five))
    _, two = _lift_case(server, "lift_one_sentence")  # the same six sets, in two sessions
    split = [_lifted(two, SQUATS), _lifted(two, BENCHES, new_session=True)]
    assert judge.judge_db(case["db"], person_rows(two)) and judge.judge(case, split, "Logged.", person_rows(two))


def test_the_lift_set_by_set_case_judges_as_designed(app_settings):
    server = _lift_server(app_settings)
    case, person = _lift_case(server, "lift_set_by_set")
    [seeded] = person_rows(person)["lift_session"]
    assert (seeded["set_count"], seeded["logged_by"]) == (1, "web")
    calls = [_lifted(person, [("Bench Press", 80, 5)])]
    assert judge.judge(case, calls, "Added.", person_rows(person)) == []
    _, second = _lift_case(server, "lift_set_by_set")  # new_session true: a second session
    apart = [_lifted(second, [("Bench Press", 80, 5)], new_session=True)]
    assert judge.judge_calls(case["expect"], apart, "Logged.") == ["log_lifts sent new_session = true"]
    assert judge.judge_db(case["db"], person_rows(second))
    _, rows = _lift_case(server, "lift_set_by_set")  # two rows, though the run's one call adds
    services.log_lifts(rows, {"sets": [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5}], "new_session": True},
                       "web")
    assert judge.judge_db(case["db"], person_rows(rows)) and judge.judge(
        case, [_lifted(rows, [("Bench Press", 80, 5)])], "Added.", person_rows(rows))
    _, refused = _lift_case(server, "lift_set_by_set")  # a refused call: nothing added
    no = [_lifted(refused, [("Bench Press", 80, 101)])]  # reps 1 to 100 (exercise-list.md R6)
    assert no[0]["is_error"] is True and judge.judge(case, no, "Sorry.", person_rows(refused))
    assert person_rows(refused)["lift_session"][0]["set_count"] == 1


def test_the_lift_added_later_case_judges_as_designed(app_settings):
    server = _lift_server(app_settings)
    case, person = _lift_case(server, "lift_added_later")
    assert person_rows(person)["lift_session"][0]["set_count"] == 3
    assert judge.judge(case, [_lifted(person, ROWS)], "Added.", person_rows(person)) == []
    _, again = _lift_case(server, "lift_added_later")  # the squats sent again beside the rows
    both = [_lifted(again, SQUATS[:1] + ROWS)]
    assert judge.judge_calls(case["expect"], both, "Added.") == ['log_lifts sent sets.*.exercise = "Back Squat"']
    assert judge.judge(case, both, "Added.", person_rows(again))
    _, apart = _lift_case(server, "lift_added_later")  # new_session true
    assert judge.judge(case, [_lifted(apart, ROWS, new_session=True)], "Logged.", person_rows(apart))


def test_person_rows_gives_each_session_its_own_set_count_and_never_another_person_s(make_user):
    mine, theirs = make_user(), make_user()
    three = services.log_lifts(mine.id, {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}] * 3},
                               "claude")["id"]
    one = services.log_lifts(mine.id, {"new_session": True,
                                       "sets": [{"exercise": "Deadlift", "weight_kg": 120, "reps": 3}]}, "claude")["id"]
    gone = services.log_lifts(mine.id, {"new_session": True, "sets": [
        {"exercise": "Bench Press", "weight_kg": 60, "reps": 8}] * 2}, "claude")["id"]
    services.delete_entry(mine.id, "lift_session", gone)
    services.log_lifts(theirs.id, {"sets": [{"exercise": "Back Squat", "weight_kg": 50, "reps": 5}] * 7}, "claude")
    from app.models import LiftSession
    with db.session_scope() as session:  # a session with no set reads 0
        empty = LiftSession(user_id=mine.id, date=dt.date(2026, 11, 1), notes="", logged_by="web", dedupe_key="e" * 64)
        session.add(empty)
        session.flush()
        empty_id = empty.id
    counts = {r["id"]: (r["set_count"], r["deleted_at"] is None) for r in person_rows(mine.id)["lift_session"]}
    assert counts == {three: (3, True), one: (1, True), gone: (2, False), empty_id: (0, True)}
    assert [r["set_count"] for r in person_rows(theirs.id)["lift_session"]] == [7]
    assert "set_count" in judge._rows_text([{"set_count": 6}])


# ---------------------------------------------------------------- the CLI's output

SAMPLE = {
    "type": "result", "subtype": "success", "is_error": False, "duration_ms": 18000, "num_turns": 3,
    "result": "Logged dal, 180 kcal, for lunch.", "session_id": "s", "total_cost_usd": 0.1403,
    "usage": {"input_tokens": 10, "cache_creation_input_tokens": 14126, "cache_read_input_tokens": 105829,
              "output_tokens": 303},
    "modelUsage": {"claude-haiku-4-5": {"costUSD": 0.001}, "claude-opus-5-5": {"costUSD": 0.1393}},
    "permission_denials": [],
}


def test_parse_cli_output():
    got = judge.parse_cli_output(json.dumps(SAMPLE))
    assert got["reply"] == "Logged dal, 180 kcal, for lunch."
    assert got["tokens"] == 10 + 14126 + 105829 + 303
    assert got["cost_usd"] == pytest.approx(0.1403)
    assert got["num_turns"] == 3
    assert got["models"] == ["claude-opus-5-5", "claude-haiku-4-5"]
    assert got["subtype"] == "success" and got["is_error"] is False


def test_parse_cli_output_forms():
    noisy = "a warning line\n" + json.dumps(SAMPLE)
    assert judge.parse_cli_output(noisy)["reply"] == SAMPLE["result"]
    listed = json.dumps([{"type": "system"}, {"type": "assistant"}, SAMPLE])
    assert judge.parse_cli_output(listed)["tokens"] > 0
    bare = judge.parse_cli_output(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True}))
    assert bare == {"reply": "", "subtype": "error_max_turns", "is_error": True, "num_turns": None,
                    "cost_usd": 0.0, "usage": {}, "tokens": 0, "input_tokens": 0, "cached_tokens": 0,
                    "output_tokens": 0, "models": [], "permission_denials": []}


@pytest.mark.parametrize("stdout", ["", "   ", "not json", "{\"type\": \"assistant\"}", "[1, 2]",
                                    "{\"type\": \"result\"", "Error: not signed in"])
def test_parse_cli_output_refuses_malformed(stdout):
    with pytest.raises(judge.CliOutputError):
        judge.parse_cli_output(stdout)


# ---------------------------------------------------------------- the token stays hidden

def test_masked():
    token = "eyJhbGciOiJSUzI1NiJ9.payload.sig"
    assert judge.masked(f"Bearer {token} and again {token}", token) == \
        "Bearer [token hidden] and again [token hidden]"
    assert judge.masked("nothing here", token) == "nothing here"
    assert judge.masked("text", "") == "text"


def test_the_token_is_only_in_the_config():
    config = mcp_config(5050, "secret-token")
    assert config == {"mcpServers": {"spotter": {"type": "http", "url": "http://127.0.0.1:5050/mcp",
                                                 "headers": {"Authorization": "Bearer secret-token"}}}}
    command = cli_command("claude", "Waist 84", "/tmp/mcp.json", None)
    assert "secret-token" not in " ".join(command)
    assert command[:3] == ["claude", "-p", "Waist 84"]
    for flag in ("--output-format", "--mcp-config", "--strict-mcp-config", "--allowedTools"):
        assert flag in command
    assert command[command.index("--allowedTools") + 1] == "mcp__spotter__*"
    assert "--model" not in command
    assert cli_command("claude", "x", "c.json", "sonnet")[-2:] == ["--model", "sonnet"]


# ---------------------------------------------------------------- the advisor is off, and one model a run (2026-11-17)

@pytest.mark.parametrize("model", [None, "", "claude-opus-5-5", "sonnet"])
def test_every_command_carries_the_empty_advisor_setting_as_one_argument(model):
    command = cli_command("claude", "Waist 84", "c.json", model)
    assert command.count("--settings") == 1
    assert json.loads(command[command.index("--settings") + 1]) == {"advisorModel": ""}
    assert ("--model" in command) is bool(model)


def test_the_cli_s_environment_turns_the_advisor_tool_off_whatever_the_parent_held():
    """The documented switch (https://code.claude.com/docs/en/advisor.md, "Turn the advisor off"). It starts with
    CLAUDE_CODE_, which is dropped, so it is set on top, and a parent's own value is replaced, not named as dropped."""
    assert child_env({"PATH": "/bin"}) == ({"PATH": "/bin", "CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}, [])
    env, dropped = child_env({"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "0", "CLAUDE_CODE_SSE_PORT": "1"})
    assert env == {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"} and dropped == ["CLAUDE_CODE_SSE_PORT"]


ANOTHER, MORE = "another model took part: ", "more than one model took part: "


@pytest.mark.parametrize("models, model, extra, reason", [
    (["claude-opus-5-5"], "claude-opus-5-5", [], None),
    (["claude-opus-5-5", "claude-fable-5-2"], "claude-opus-5-5", ["claude-fable-5-2"], ANOTHER),
    (["claude-fable-5-2", "claude-opus-5-5"], "claude-opus-5-5", ["claude-fable-5-2"], ANOTHER),  # it cost more
    (["claude-opus-5-5", "claude-haiku-4-5"], "claude-opus-5-5", ["claude-haiku-4-5"], ANOTHER),  # a helper too
    (["claude-sonnet-5-5"], "claude-opus-5-5", ["claude-sonnet-5-5"], ANOTHER),  # the pinned model took no part
    (["claude-sonnet-5-5", "claude-fable-5-2"], "claude-opus-5-5", ["claude-sonnet-5-5", "claude-fable-5-2"], ANOTHER),
    (["claude-sonnet-5-5"], "", [], None),  # --model "": the CLI's default, exactly one model
    (["claude-sonnet-5-5"], None, [], None),
    # More than one, with no full id to match: cost cannot tell the main model (the advisor cost more in 16 of 19).
    (["claude-fable-5-2", "claude-sonnet-5-5"], "", ["claude-fable-5-2", "claude-sonnet-5-5"], MORE),
    (["claude-sonnet-5-5", "claude-fable-5-2"], "", ["claude-fable-5-2", "claude-sonnet-5-5"], MORE),
    (["claude-sonnet-5-5"], "sonnet", [], None),  # an alias: modelUsage names full ids, so exactly one is the rule
    (["claude-sonnet-5-5", "claude-fable-5-2"], "sonnet", ["claude-fable-5-2", "claude-sonnet-5-5"], MORE),
])
def test_the_one_model_rule(models, model, extra, reason):
    from evals.run import model_reasons, other_models
    assert other_models(models, model) == extra
    assert model_reasons(models, model) == ([reason + ", ".join(extra)] if reason else [])


@pytest.mark.parametrize("model", ["claude-opus-5-5", "", None])
def test_a_run_that_reports_no_model_fails_but_is_not_counted_as_another(model):
    from evals.run import model_reasons, other_models
    assert other_models([], model) == []
    assert model_reasons([], model) == ["the CLI reported no model, so no check that one model ran alone"]


def _model_server(app_settings) -> LocalServer:
    """One per test: two made in the same second would name their people alike."""
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people and its recorder are used
    server.settings = app_settings
    server.recorder = CallRecorder(None)
    return server


def _run_with_models(server, monkeypatch, model_usage: dict, model: str | None) -> tuple[dict, list]:
    """One run_once through a fake CLI whose JSON reports these models: (the outcome, the commands it was given)."""
    from evals import run
    given = []

    def fake_cli(command, **kwargs):
        given.append((command, kwargs["env"]))
        stdout = json.dumps({**SAMPLE, "result": "Logged 80 kg.", "modelUsage": model_usage})
        return run.subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(run.subprocess, "run", fake_cli)
    case = {"id": "one_model", "say": "Weighed 80 kg", "expect": {"reply_has": ["Logged"]}}
    env, _ = child_env({"PATH": "/bin"})
    with tempfile.TemporaryDirectory() as folder:  # not tmp_path: pytest's own temp root is not always writable
        outcome = run.run_once(server, case, "Asia/Kolkata", "claude", model, env, Path(folder), "one_model-1", False)
    return outcome, given


def test_a_run_with_only_the_pinned_model_passes(app_settings, monkeypatch):
    outcome, given = _run_with_models(_model_server(app_settings), monkeypatch, {"claude-opus-5-5": {"costUSD": 0.14}},
                                      "claude-opus-5-5")
    assert (outcome["passed"], outcome["reasons"], outcome["other_models"]) == (True, [], [])
    [(command, env)] = given
    assert "--settings" in command and env["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1"


def test_a_run_a_second_model_took_part_in_fails_and_names_it(app_settings, monkeypatch):
    """The release run of 2026-11-17: claude-fable-5-2, the advisor, beside the pinned model in 11 of 153 runs."""
    usage = {"claude-opus-5-5": {"costUSD": 0.14}, "claude-fable-5-2": {"costUSD": 0.05}}
    outcome, _ = _run_with_models(_model_server(app_settings), monkeypatch, usage, "claude-opus-5-5")
    assert outcome["passed"] is False
    assert outcome["reasons"] == ["another model took part: claude-fable-5-2"]  # the reply itself passed
    assert outcome["other_models"] == ["claude-fable-5-2"]


def test_with_the_cli_s_default_model_a_run_must_have_exactly_one(app_settings, monkeypatch):
    server = _model_server(app_settings)
    one, _ = _run_with_models(server, monkeypatch, {"claude-sonnet-5-5": {"costUSD": 0.05}}, "")
    assert (one["passed"], one["other_models"]) == (True, [])
    usage = {"claude-sonnet-5-5": {"costUSD": 0.05}, "claude-fable-5-2": {"costUSD": 0.04}}
    two, given = _run_with_models(server, monkeypatch, usage, "")
    assert two["passed"] is False and two["reasons"] == ["more than one model took part: claude-fable-5-2, claude-sonnet-5-5"]
    assert two["other_models"] == ["claude-fable-5-2", "claude-sonnet-5-5"] and "--model" not in given[0][0]


def test_the_summary_counts_the_runs_another_model_took_part_in():
    from evals.run import model_line
    runs = [{"other_models": []}, {"other_models": ["claude-fable-5-2"]}, {}, {"other_models": ["claude-fable-5-2"]}]
    results = [{"id": "a", "runs": runs[:2]}, {"id": "b", "runs": runs[2:]}]
    assert model_line(results) == "runs where another model took part: 2 of 4 (claude-fable-5-2)"
    assert model_line([{"id": "a", "runs": [{"other_models": []}]}]) == "runs where another model took part: 0 of 1"


def test_main_gives_every_run_the_advisor_off_and_prints_the_count(monkeypatch, capsys):
    """main with its server and its runs replaced: no CLI, no port. The parent's own 0 is replaced by 1."""
    from evals import run
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", "0")
    monkeypatch.setattr(run.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(run.LocalServer, "start", lambda self: None)
    monkeypatch.setattr(run.LocalServer, "stop", lambda self: None)
    monkeypatch.setattr(run.judge, "load_cases", lambda: [{"id": "one_model", "say": "x", "expect": {"reply_asks": True}}])
    envs = []
    outcomes = iter([
        {"passed": False, "reasons": ["another model took part: claude-fable-5-2"], "other_models": ["claude-fable-5-2"],
         "models": ["claude-opus-5-5", "claude-fable-5-2"]},
        {"passed": True, "reasons": [], "other_models": [], "models": ["claude-opus-5-5"]}])

    def fake_run_once(server, case, zone, claude, model, env, workdir, label, keep, effort=None):
        envs.append(env)
        return {"tokens": 0, "cost_usd": 0.0, "reply": "Done?", "calls": [], "seconds": 1.0, "instructions": True,
                **next(outcomes)}

    monkeypatch.setattr(run, "run_once", fake_run_once)
    assert run.main(["--n", "2", "--time-zone", "Asia/Kolkata"]) == 1
    out = capsys.readouterr().out
    assert "runs where another model took part: 1 of 2 (claude-fable-5-2)" in out
    assert "another model took part: claude-fable-5-2" in out.split("Failed runs:", 1)[1]
    assert [e["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] for e in envs] == ["1", "1"]


def test_the_evals_doc_says_the_advisor_is_off_and_the_one_model_rule():
    doc = " ".join((Path(__file__).resolve().parent.parent / "docs" / "evals.md").read_text(encoding="utf-8").split())
    for said in ("CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1", "https://code.claude.com/docs/en/advisor.md",
                 "--settings '{\"advisorModel\": \"\"}'", "another model took part: claude-fable-5-2",
                 "runs where another model took part"):
        assert said in doc, said


def test_each_run_is_cut_off_at_300_seconds_well_inside_its_token():
    """180 until 2026-11-17: two data runs were cut off mid-answer after 10 to 15 calls (data-on-request.md, notes)."""
    from evals import issuer, run
    assert run.RUN_TIMEOUT == 300 < issuer.TOKEN_SECONDS
    doc = " ".join((Path(__file__).resolve().parent.parent / "docs" / "evals.md").read_text(encoding="utf-8").split())
    assert "Each run is cut off at 300 seconds." in doc and "180 seconds" not in doc


def test_child_env_drops_the_session_markers_only():
    env, dropped = child_env({"CLAUDECODE": "1", "CLAUDE_CODE_SSE_PORT": "1", "CLAUDE_PID": "2", "AI_AGENT": "x",
                              "CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin"})
    assert env == {"CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin", "CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}  # the one it sets
    assert dropped == ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_SSE_PORT", "CLAUDE_PID"]


# ---------------------------------------------------------------- the recorder, on the real server

def test_parse_body_reads_json_and_event_streams():
    assert parse_body("application/json", b'{"id": 1}') == {"id": 1}
    assert parse_body("text/event-stream", b'event: message\ndata: {"id": 2}\n\n') == {"id": 2}
    assert parse_body("application/json", b"") is None


def test_recorder_keeps_calls_per_token(provider, make_user, token_for):
    make_user(subject="user_rec_a", email="a@example.com", name="A", claude_can="log")  # switch: deleting off
    make_user(subject="user_rec_b", email="b@example.com", name="B")
    recorder = CallRecorder(create_app(settings(), fetch=provider.fetch))
    a, b = token_for("user_rec_a"), token_for("user_rec_b")
    with TestClient(recorder) as client:
        def rpc(token, method, params=None, id_=1):
            return client.post("/mcp", headers={**HEADERS, "Authorization": f"Bearer {token}"},
                               json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})

        assert rpc(a, "tools/call", {"name": "whoami", "arguments": {}}).status_code == 200
        rpc(a, "tools/call", {"name": "log_bodyweight", "arguments": {"kg": 70, "date": "2001-01-01"}}, 2)
        rpc(a, "tools/call", {"name": "delete_entry", "arguments": {"kind": "bodyweight", "entry_id": 1}}, 3)
        rpc(b, "tools/list")
        assert client.get("/health").status_code == 200

    calls = recorder.calls(a)
    assert [c["name"] for c in calls] == ["whoami", "log_bodyweight", "delete_entry"]
    assert calls[0]["is_error"] is False
    assert calls[1]["arguments"] == {"kg": 70, "date": "2001-01-01"} and calls[1]["is_error"] is True
    assert calls[2]["is_error"] is True and calls[2]["error"] == "not_allowed"  # the switch set to log
    assert recorder.calls(b) == [] and recorder.methods(b) == ["tools/list"]
    assert recorder.calls("never-used") == []
    assert a not in json.dumps(recorder._calls) and token_key(a) in recorder._calls


def test_a_seed_service_is_found_by_its_name():
    """four_weeks and sixteen_weeks are the evals' own seeds, outside app/; every other seed is Spotter's own
    service. A name that is neither, add_sample_data among them, is no seed."""
    from evals.four_weeks import four_weeks
    from evals.sixteen_weeks import sixteen_weeks
    assert judge.NO_DATA_SERVICES == {"four_weeks", "sixteen_weeks"}
    assert judge.seed_service("four_weeks") is four_weeks and judge.seed_service("sixteen_weeks") is sixteen_weeks
    assert judge.seed_service("save_food") is services.save_food
    for name in ("add_sample_data", "remove_sample_data", "drop_table"):
        with pytest.raises(KeyError):
            judge.seed_service(name)
        assert not hasattr(services, name)


# ---------------------------------------------------------------- G1 of function-list.md: the tags, the coverage, the figures

def _cases() -> dict:
    return {c["id"]: c for c in judge.load_cases()}


def test_every_case_carries_the_three_call_tags_and_they_agree_with_its_expect():
    for case in judge.load_cases():
        tags = case.get("calls")
        assert isinstance(tags, dict) and set(tags) == {"must", "may", "must_not"}, case["id"]
        assert judge.case_problems(case) == [], case["id"]
        expect = case["expect"]
        if "function" in expect:
            assert expect["function"] in tags["must"], case["id"]
        assert set(expect.get("no_call") or []) <= set(tags["must_not"]), case["id"]
        if expect.get("read_any"):
            assert tags["must"] and set(tags["must"]) <= set(judge.DATA_READS), case["id"]


def test_every_function_on_everyone_s_list_is_some_case_s_must():
    """The coverage test: no cost, no run. The owner's three are listed for him alone, so they are left out. It holds
    for the full set and for the core, the cases each gate runs (python -m evals.run --core)."""
    from app import mcp_server
    everyone = set(mcp_server.NEEDS) - set(mcp_server.OWNER_FUNCTIONS)
    cases = judge.load_cases()
    musts = {name for c in cases for name in c["calls"]["must"]}
    assert everyone - musts == set()
    core = [c for c in cases if c.get("core") is True]
    assert len(core) == 101 and everyone - {name for c in core for name in c["calls"]["must"]} == set()
    named = {name for c in cases for key in ("must", "may", "must_not") for name in c["calls"][key]}
    assert named <= everyone  # no tag names a function nobody has, or the owner's
    # The two the release run of 2026-11-17 never called each have a case of their own.
    assert {"save_food", "edit_entry"} <= {name for c in cases if len(c["calls"]["must"]) == 1
                                           for name in c["calls"]["must"]}


def test_the_judge_s_lists_are_the_app_s():
    from app import mcp_server
    assert judge.WRITE_FUNCTIONS == common.WRITE_FUNCTIONS
    assert set(judge.DATA_READS) <= set(mcp_server.READ_FUNCTIONS)
    assert not {"whoami", "list_exercises", "find_food", "get_routines"} & set(judge.DATA_READS)


@pytest.mark.parametrize("tags, problem", [
    ({"must": [], "may": []}, "calls must be an object of exactly must, may, must_not"),
    ({"must": "log_food", "may": [], "must_not": []}, "calls.must must be a list of function names"),
    ({"must": ["log_food", "log_food"], "may": [], "must_not": []}, "calls.must names a function twice"),
    ({"must": ["log_food"], "may": [], "must_not": ["log_food"]}, "calls.must and calls.must_not both name log_food"),
    ({"must": [], "may": [], "must_not": []}, "calls.must leaves out log_food, the expect's function"),
    ({"must": ["log_food"], "may": [], "must_not": []},
     "calls.must_not leaves out save_food, which the expect's no_call names"),
])
def test_the_tags_are_checked_when_the_cases_load(tags, problem):
    case = {"id": "a", "say": "x", "calls": tags, "expect": {"function": "log_food", "no_call": ["save_food"]}}
    assert problem in judge.case_problems(case)


def test_a_golden_kind_is_one_of_three_and_its_tags_say_what_it_measures():
    base = {"id": "a", "say": "x", "expect": {"reply_has": ["x"]}}
    assert judge.case_problems({**base, "kind": "golden"}) == ["kind must be one of direct, incidental, look_alike"]
    assert judge.case_problems({**base, "kind": "ambiguous"}) == ["kind must be one of direct, incidental, look_alike"]
    none = {"must": [], "may": [], "must_not": []}
    assert judge.case_problems({**base, "kind": "incidental", "calls": none}) == [
        "an incidental case names the data read its step needs in calls.must"]
    assert judge.case_problems({**base, "kind": "look_alike", "calls": none}) == [
        "a look_alike case names every data read in calls.must_not"]
    reads = {"must": [], "may": [], "must_not": list(judge.DATA_READS)}
    assert judge.case_problems({**base, "kind": "look_alike", "calls": reads}) == []
    cases = judge.load_cases()
    kinds = {c["id"]: c["kind"] for c in cases if "kind" in c}
    assert {k for k, v in kinds.items() if v == "direct"} >= {"how_am_i_doing", "data_lifting", "data_back_on_track",
                                                               "data_empty"}
    # G1's three pairs, then the golden package's 24 (tests/test_golden_cases.py)
    assert sorted(v for v in kinds.values() if v != "direct") == ["incidental"] * 27 + ["look_alike"] * 27


def test_must_needs_one_call_answered_and_must_not_none_called():
    tags = {"must": ["get_data", "what_is_logged"], "may": ["whoami"], "must_not": ["log_food"]}
    assert judge.judge_tags(tags, [call("whoami"), call("get_data", is_error=True)]) == []  # a refusal is an answer
    assert judge.judge_tags(tags, [call("whoami")]) == [
        "none of get_data, what_is_logged was called (calls: whoami)"]
    assert judge.judge_tags(tags, [{**call("get_data", is_error=True), "answered": False}]) == [
        "get_data was called and never answered"]
    assert judge.judge_tags(tags, [call("get_data"), call("log_food")]) == ["log_food was called and should not have been"]
    assert judge.judge_tags({"must": ["log_food"], "may": [], "must_not": []}, []) == [
        "log_food was not called (calls: none)"]
    assert judge.judge_tags({"must": [], "may": ["get_data"], "must_not": []}, [call("get_data")]) == []  # may: nothing
    assert judge.judge_tags(None, []) == []


def test_a_reason_the_expect_and_the_tags_give_alike_is_said_once():
    case = {"id": "a", "say": "x", "calls": {"must": ["log_food"], "may": [], "must_not": ["save_food"]},
            "expect": {"function": "log_food", "no_call": ["save_food"]}}
    assert judge.judge(case, [call("save_food")], "", {}) == ["log_food was not called (calls: save_food)",
                                                               "save_food was called and should not have been"]
    tagged_only = {**case, "expect": {"reply_has": ["Logged"]}}
    assert judge.judge(tagged_only, [call("save_food")], "Logged", {}) == [
        "log_food was not called (calls: save_food)", "save_food was called and should not have been"]


def test_read_any_needs_a_data_read_answered_without_error():
    expect = {"read_any": True}
    assert judge.judge_calls(expect, [call("whoami"), call("get_log")], "") == []
    for name in judge.DATA_READS:
        assert judge.judge_calls(expect, [call(name)], "") == [], name
    for name in ("whoami", "list_exercises", "find_food", "get_routines", "log_food"):
        assert judge.judge_calls(expect, [call(name)], "") == [
            f"no data read was answered without error (calls: {name})"], name
    assert judge.judge_calls(expect, [call("get_data", is_error=True)], "")
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"read_any": False}}) == [
        "expect.read_any must be true"]


@pytest.mark.parametrize("value", [-1, 1.5, True, "2"])
def test_quotes_spotter_takes_a_whole_number(value):
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"quotes_spotter": value}}) == [
        "expect.quotes_spotter must be a whole number, 0 or more"]


ANSWER = {"today": "2026-11-16", "from": "2026-10-20", "columns": ["date", "id", "session_id", "weight_kg", "reps"],
          "rows": [["2026-11-13", 4417, 918, 100.0, 5], ["2026-11-11", 4416, 917, 97.5, 5]],
          "totals": {"columns": ["weight_kg_max", "kcal_sum"], "values": [100.0, 1980]},
          "goals": [{"goal_id": 2, "plan_line": "116.7 kg now against 120 kg on the plan line today: 3.3 kg behind."}],
          "summary": "24 sessions since Mon 27 Jul, the last 28 days ending 16 Nov.", "rate": -0.24,
          "pace": "5:54 a km", "links": {"progress": "https://spotter.example.app/progress/42"}, "more": "c1.eyJ4IjozMH0",
          "entries": [{"id": 5512, "saved_food_id": 31, "kcal": 640}]}


@pytest.mark.parametrize("reply, quoted", [
    ("Your best is 100 kg for 5, an estimated max of 116.7 kg.", ["100", "116.7"]),
    ("100.0 kg, then 97.5 kg.", ["100.0", "97.5"]),                 # 100.0 is 100
    ("1,980 kcal, or 1980 again.", ["1,980"]),                     # one figure, said twice
    ("3.3 kg behind, 0.24 kg a week down.", ["3.3", "0.24"]),      # a decimal under 10 counts; the sign is ignored
    ("Session 918 on 2026-11-13, id 4417.", []),                   # ids and dates never count
    ("Entry 5512, saved food 31: 640 kcal.", ["640"]),             # an id as a key, not only as a column
    ("13 Nov: 5 sets of 5.", []),                                  # a date, and whole numbers under 10
    ("Over the last 28 days: 24 sessions.", ["24"]),               # a span never counts
    ("See https://spotter.example.app/progress/42 and [the log](https://x.app/116).", []),  # links never count
    ("A pace of 5:54 a km.", ["5:54"]),                            # a duration is one figure
    ("From 93.3 kg to 1116.7 kg.", []),                            # matched whole: not the 3.3 in 93.3
    ("Your goal is 150 kg.", []),                                  # not in this run's answers
])
def test_quoted_figures_are_this_run_s_own_matched_whole(reply, quoted):
    assert judge.quoted_figures(reply, [answered("get_data", ANSWER)]) == quoted


def test_a_figure_the_say_holds_and_a_read_that_is_not_a_data_read_never_count():
    reply = "Was it above 90 kg? Yes: 92.4 kg."
    answer = {"rows": [["2025-12-11", 92.4], ["2025-12-21", 90]], "columns": ["date", "weight_kg"]}
    assert judge.quoted_figures(reply, [answered("get_data", answer)], "Was my weight ever above 90 kg?") == ["92.4"]
    assert judge.quoted_figures(reply, [answered("get_data", answer)]) == ["90", "92.4"]
    for name in ("whoami", "list_exercises", "find_food", "get_routines"):
        assert judge.quoted_figures(reply, [answered(name, answer)]) == [], name
    assert judge.quoted_figures(reply, [answered("get_data", answer, is_error=True)]) == []  # a refusal gives none
    assert judge.quoted_figures(reply, [call("get_data")]) == []  # a call with no answer kept gives none
    expect = {"quotes_spotter": 2}
    assert judge.judge_calls(expect, [answered("get_data", answer)], reply, "Was my weight ever above 90 kg?") == [
        "the reply quotes 1 of this run's figures from Spotter's data reads, expected at least 2 (quoted: 92.4)"]
    assert judge.judge_calls({"any_of": [expect]}, [answered("get_data", answer)], reply)  == []  # say: none
    assert judge.judge_calls({"quotes_spotter": 0}, [], "") == []


# The kept run of 2026-11-17's Gemini trial, gemini-4-hydrogen on how_am_i_doing (four_weeks): its reply word for word
# and the two data reads it made, trimmed to the parts the reply quotes. It passed under the judging of the day.
KEPT_REPLY = (
    "Body weight is 79 kg now, smoothed 79.2 kg, -0.24 kg a week.\nCalories average 2,216 kcal a day over 7 logged "
    "days of the last 7.\nCalories average 2,196 kcal a day over 28 logged days of the last 28.\nMaintenance estimate "
    "is about 2,510 kcal a day, from 21 logged days of the last 21.\nYou logged 3 lift sessions in the last 7 days.\n"
    "You logged 85 cardio minutes in the last 7 days.\n\nGoal: Body weight 77 kg by Tue 12 Jan 2027, on track, "
    "projected date 2027-01-02.\n79.01 kg now against 78.96 kg on the plan line today: 0.05 kg behind.\n\nGoal: Back "
    "Squat 120 kg by Tue 9 Feb 2027, on track, projected date 2026-12-03.\n107.9 kg now against 99.8 kg on the plan "
    "line today: 8.1 kg ahead.\n\nGoal: Outdoor Run 5 km in 27:00 by Tue 12 Jan 2027, on track, projected date "
    "2026-12-19.\n29:30 now against 29:48 on the plan line today: 18 seconds ahead.\n\nProgress: "
    "https://spotter.example.app/progress\nGoals: https://spotter.example.app/goals")
KEPT_TRENDS = {"today": "2026-11-17", "bodyweight": {"now": 79.0, "smoothed": 79.2, "rate_per_week": -0.24,
                                                     "weigh_ins_28d": 28},
               "calories": {"avg_7d": 2216, "days_logged_7d": 7, "avg_28d": 2196, "days_logged_28d": 28},
               "sessions": {"this_week": 3}, "cardio": {"minutes_this_week": 84.5, "minutes_per_week_4w": 85.2},
               "maintenance": {"kcal": 2513, "days_logged": 21, "window_days": 21},
               "summary": "Maintenance about 2,510 kcal a day: 85 cardio minutes a week."}
KEPT_GOALS = {"goals": [
    {"goal_id": 1, "label": "Body weight 77 kg", "target_value": 77.0,
     "plan_line": "79.01 kg now against 78.96 kg on the plan line today: 0.05 kg behind."},
    {"goal_id": 2, "label": "Back Squat 120 kg", "target_value": 120.0,
     "plan_line": "107.9 kg now against 99.8 kg on the plan line today: 8.1 kg ahead."},
    {"goal_id": 3, "label": "Outdoor Run 5 km in 27:00", "target_value": 27.0,
     "plan_line": "29:30 now against 29:48 on the plan line today: 18 seconds ahead."}]}


def test_quotes_spotter_on_a_kept_run():
    calls = [answered("whoami", {"account_id": 2, "today": "2026-11-17"}), answered("get_trends", KEPT_TRENDS),
             answered("get_goals", KEPT_GOALS)]
    assert judge.quoted_figures(KEPT_REPLY, calls, "How am I doing?") == [
        "27:00", "29:30", "29:48", "79", "79.2", "0.24", "2,216", "2,196", "28", "2,510", "21", "85", "77", "79.01",
        "78.96", "0.05", "120", "107.9", "99.8", "8.1", "18"]
    case = judge.resolve(_cases()["how_am_i_doing"], {})
    assert judge.judge(case, calls, KEPT_REPLY, {}) == []
    assert judge.judge(case, calls[:1], KEPT_REPLY, {})  # whoami alone: no data read, no figure of one
    assert judge.judge(case, calls, "You are doing well. Keep it up.", {})


def test_how_am_i_doing_quotes_two_of_its_own_runs_figures(make_user, monkeypatch):
    """On Spotter's own answer: the four-week account on its fixed day, 2026-11-10."""
    from evals.four_weeks import four_weeks
    monkeypatch.setattr(db, "utcnow", lambda: dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.timezone.utc))
    user = make_user(time_zone="Asia/Kolkata")
    assert "error" not in four_weeks(user.id)
    trends = services.get_trends(user.id)
    now, kcal = trends["bodyweight"]["now"], trends["calories"]["avg_7d"]
    case = judge.resolve(_cases()["how_am_i_doing"], {})
    calls = [call("whoami"), answered("get_trends", trends)]
    assert judge.judge(case, calls, f"You weigh {now:g} kg and eat {kcal:,} kcal a day.", {}) == []
    assert judge.judge(case, calls, f"You weigh {now + 7:g} kg and eat {kcal + 333:,} kcal a day.", {})  # not Spotter's
    assert judge.judge(case, calls, f"You weigh {now:g} kg.", {})  # one figure, not two


# ---------------------------------------------------------------- the rates and the table

def _run(passed=True, calls=(), seconds=10.0, tokens=(100, 1000, 10), turns=3, cost=0.1):
    return {"passed": passed, "calls": list(calls), "seconds": seconds, "num_turns": turns, "cost_usd": cost,
            "input_tokens": tokens[0], "cached_tokens": tokens[1], "output_tokens": tokens[2], "tokens": sum(tokens)}


KEPT_RESULTS = [  # as a kept results.json holds them: each case with its kind and tags
    {"id": "stated_number", "tags": {"must": ["log_food"], "may": [], "must_not": []},
     "runs": [_run(calls=[call("log_food")]), _run(calls=[call("log_food")]), _run(passed=False)]},
    {"id": "inc_deload", "kind": "incidental", "tags": {"must": list(judge.DATA_READS), "may": [], "must_not": []},
     "runs": [_run(calls=[call("whoami"), call("get_data")]), _run(passed=False, calls=[call("whoami")]),
              _run(calls=[call("get_trends", is_error=True)])]},
    {"id": "look_deload", "kind": "look_alike", "tags": {"must": [], "may": [], "must_not": list(judge.DATA_READS)},
     "runs": [_run(calls=[call("whoami")]), _run(passed=False, calls=[call("what_is_logged")]), _run(calls=[])]},
]


def test_the_pick_up_and_over_call_rates_are_worked_from_a_kept_run():
    assert judge.golden_rates(KEPT_RESULTS) == {"pick_up": (2, 3), "over_call": (1, 3)}
    assert judge.golden_rates(KEPT_RESULTS[:1]) == {"pick_up": (0, 0), "over_call": (0, 0)}
    cut_off = [{**KEPT_RESULTS[1], "runs": [_run(calls=[{**call("get_data", is_error=True), "answered": False}])]}]
    assert judge.golden_rates(cut_off)["pick_up"] == (0, 1)  # a call with no answer is no pick-up


def test_the_table_keeps_incidental_and_look_alike_runs_apart():
    table, ok = judge.summarise(KEPT_RESULTS, 3, 0.66)
    assert ok is True  # stated_number passed 2 of 3; the apart cases' fails do not count
    assert "counted: 2 of 3 runs passed, pass rate 67%" in table
    assert "apart, incidental and look-alike: 4 of 6 runs passed, not counted" in table
    assert "pick-up rate: 2 of 3 (67%) incidental runs made a must call; over-call rate: 1 of 3 (33%)" in table
    assert "apart: incidental" in table and "apart: look_alike" in table
    assert "logging cases (a write in must): 2 of 3 (67%) runs passed; under 2 of 3: none" in table
    lines = {line.split()[0]: line for line in table.splitlines() if line.startswith("stated_number")}
    assert lines["stated_number"].split()[1:7] == ["2/3", "0.7", "3.0", "10.0", "100", "1,000"]
    assert "a run, median: 1 calls, 3 turns, 10 seconds, 1,110 tokens" in table
    assert "tokens 900 input, 9,000 cached, 90 output, cost $0.9000" in table
    assert judge.summarise(KEPT_RESULTS, 3, 1.0)[1] is False


def test_a_client_that_gives_no_cost_says_so():
    results = [{"id": "a", "runs": [_run(cost=None), _run(cost=None)]}]
    table, _ = judge.summarise(results, 2, 1.0)
    assert "cost none given" in table and "$0.0000" not in table
    assert judge.summarise([{"id": "a", "runs": [{"passed": True, "tokens": 5, "cost_usd": 0.0}]}], 1, 1.0)[1]


def test_the_claude_token_split_adds_up_to_its_tokens():
    got = judge.parse_cli_output(json.dumps(SAMPLE))
    assert (got["input_tokens"], got["cached_tokens"], got["output_tokens"]) == (10 + 14126, 105829, 303)
    assert got["input_tokens"] + got["cached_tokens"] + got["output_tokens"] == got["tokens"]
    assert judge.token_split("codex", {"input_tokens": 74001, "cached_input_tokens": 54400, "output_tokens": 194,
                                       "reasoning_output_tokens": 0}) == {
        "input_tokens": 19601, "cached_tokens": 54400, "output_tokens": 194}
    assert judge.token_split("gemini", {"promptTokenCount": 50971, "cachedContentTokenCount": 29785,
                                        "candidatesTokenCount": 407, "thoughtsTokenCount": 2967}) == {
        "input_tokens": 21186, "cached_tokens": 29785, "output_tokens": 3374}
    with pytest.raises(ValueError):
        judge.token_split("other", {})


# ---------------------------------------------------------------- the recorder keeps a data read's answer

def test_the_recorder_keeps_each_data_read_s_answer_and_no_other(provider, make_user, token_for):
    make_user(subject="user_rec_c", email="c@example.com", name="C", time_zone="Asia/Kolkata")
    recorder = CallRecorder(create_app(settings(), fetch=provider.fetch))
    token = token_for("user_rec_c")
    with TestClient(recorder) as client:
        for i, (name, arguments) in enumerate((("whoami", {}), ("log_bodyweight", {"kg": 80.4}),
                                               ("get_data", {"area": "weigh_ins"}), ("what_is_logged", {}),
                                               ("get_data", {"area": "lift_set"})), 1):
            client.post("/mcp", headers={**HEADERS, "Authorization": f"Bearer {token}"},
                        json={"jsonrpc": "2.0", "id": i, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments}})
    calls = recorder.calls(token)
    assert [c["name"] for c in calls] == ["whoami", "log_bodyweight", "get_data", "what_is_logged", "get_data"]
    assert all(c["answered"] for c in calls)
    assert "answer" not in calls[0] and "answer" not in calls[1]  # not data reads
    assert calls[2]["answer"]["columns"][:2] == ["date", "id"] and calls[2]["answer"]["rows"][0][2] == 80.4
    assert any(a["area"] == "weigh_ins" for a in calls[3]["answer"]["areas"])
    assert calls[4]["is_error"] is True and calls[4]["answer"]["error"]  # a refusal's answer is kept, never counted
    assert judge.quoted_figures("You weighed 80.4 kg.", calls) == ["80.4"]


def test_the_recorder_reads_an_answer_from_its_text_when_it_has_no_structured_content():
    from evals.recorder import answer_of
    assert answer_of({"structuredContent": {"a": 1}, "content": [{"type": "text", "text": "{}"}]}) == {"a": 1}
    assert answer_of({"content": [{"type": "text", "text": '{"rows": [[1]]}'}]}) == {"rows": [[1]]}
    assert answer_of({"content": [{"type": "text", "text": "plain 81.1 kg"}]}) == "plain 81.1 kg"
    assert answer_of({}) is None


# ---------------------------------------------------------------- the runner's new parts: days ago, effort, clients

def test_dates_for_gives_days_ago_for_the_catch_up_case():
    dates = dates_for("Asia/Kolkata")
    today = dt.date.fromisoformat(dates["today"])
    assert dates["days_ago:0"] == dates["today"] and dates["days_ago:1"] == dates["yesterday"]
    assert dates["days_ago:3"] == (today - dt.timedelta(days=3)).isoformat() and "days_ago:61" not in dates
    assert judge.resolve("{{days_ago:3}}", dates) == dates["days_ago:3"]


def test_the_effort_is_a_flag_of_its_own_before_the_model():
    command = cli_command("claude", "Waist 84", "c.json", "claude-sonnet-5-5", "xhigh")
    assert command[-4:] == ["--effort", "xhigh", "--model", "claude-sonnet-5-5"]
    assert "--effort" not in cli_command("claude", "x", "c.json", "sonnet")


@pytest.mark.parametrize("argv, model, effort", [  # each model's own default, pinned (the owner, 2026-11-17)
    ([], "claude-opus-5-5", "medium"),
    (["--model", "claude-sonnet-5-5"], "claude-sonnet-5-5", "medium"),
    (["--client", "codex"], "gpt-6.1-sol", "low"),
    (["--client", "codex", "--model", "gpt-6-luna"], "gpt-6-luna", "medium"),
    (["--client", "codex", "--model", "gpt-6-luna", "--effort", "high"], "gpt-6-luna", "high"),  # by hand
    (["--client", "gemini"], "gemini-4-hydrogen", "high"),
    (["--client", "gemini", "--model", "gemini-3.8-flash"], "gemini-3.8-flash", ""),  # no default read: none sent
    (["--model", "", "--effort", ""], "", ""),  # the client's own defaults, sent as nothing
    (["--model", ""], "", ""),
])
def test_each_client_has_its_pinned_model_and_effort(argv, model, effort):
    from evals import run
    args = run.parse_args(argv)
    assert (args.model, args.effort) == (model, effort)
    assert run.SMALL_GPT == "gpt-6-luna"


@pytest.mark.parametrize("argv", [["--client", "gemini", "--effort", "ultra"], ["--effort", "minimal"],
                                  ["--client", "gemini", "--model", ""], ["--client", "other"],
                                  ["--gemini-key", "0"]])
def test_an_effort_a_client_does_not_take_is_refused(argv, capsys):
    from evals import run
    with pytest.raises(SystemExit):
        run.parse_args(argv)


def test_the_model_lines_say_who_answered():
    from evals import run
    results = [{"id": "a", "runs": [{"models": ["claude-sonnet-5-5"], "other_models": []}]}]
    assert run.model_lines(results, "claude", "claude-sonnet-5-5", "xhigh") == [
        "client: claude, model asked for: claude-sonnet-5-5; reported: claude-sonnet-5-5", "effort sent: xhigh",
        "runs where another model took part: 0 of 1"]
    assert run.model_lines([{"id": "a", "runs": [{"models": []}]}], "codex", "gpt-6-luna", "") == [
        "client: codex, model asked for: gpt-6-luna; Codex names no model in its events, so the one ran is not reported",
        "effort sent: none (the client default)"]
    assert run.model_lines([{"id": "a", "runs": [{"models": ["gemini-4-hydrogen"]}]}], "gemini", "gemini-4-hydrogen",
                           "high")[0] == "client: gemini, model asked for: gemini-4-hydrogen; reported: gemini-4-hydrogen"


def test_main_runs_the_golden_set_alone_and_carries_each_case_s_kind_and_tags(monkeypatch, capsys):
    from evals import run
    monkeypatch.setattr(run.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(run.LocalServer, "start", lambda self: None)
    monkeypatch.setattr(run.LocalServer, "stop", lambda self: None)
    seen = []

    def fake_run_once(server, case, zone, claude, model, env, workdir, label, keep, effort=None):
        seen.append((case["id"], model, effort))
        return {"passed": True, "reasons": [], "reply": "", "calls": [], "seconds": 1.0, "models": [model],
                "other_models": [], "tokens": 0, "cost_usd": 0.0}

    monkeypatch.setattr(run, "run_once", fake_run_once)
    assert run.main(["--golden", "--n", "1", "--time-zone", "Asia/Kolkata", "--model", "claude-sonnet-5-5"]) == 0
    golden = [c["id"] for c in judge.load_cases() if c.get("kind")]
    assert [s[0] for s in seen] == golden and {s[1:] for s in seen} == {("claude-sonnet-5-5", "medium")}
    out = capsys.readouterr().out
    assert "pick-up rate: 0 of 27 (0%) incidental runs" in out
    assert "client: claude, model asked for: claude-sonnet-5-5" in out and "effort sent: medium" in out


def test_main_runs_the_core_alone_the_set_each_gate_runs(monkeypatch, capsys):
    """function-list.md, G1's note of 2026-11-17: the core at each gate, the full set at the baseline and the release."""
    from evals import run
    monkeypatch.setattr(run.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(run.LocalServer, "start", lambda self: None)
    monkeypatch.setattr(run.LocalServer, "stop", lambda self: None)
    seen = []

    def fake_run_once(server, case, zone, claude, model, env, workdir, label, keep, effort=None):
        seen.append(case["id"])
        return {"passed": True, "reasons": [], "reply": "", "calls": [], "seconds": 1.0, "models": [model],
                "other_models": [], "tokens": 0, "cost_usd": 0.0}

    monkeypatch.setattr(run, "run_once", fake_run_once)
    assert run.main(["--core", "--n", "1", "--time-zone", "Asia/Kolkata"]) == 0
    core = [c["id"] for c in judge.load_cases() if c.get("core") is True]
    assert seen == core and len(core) == 101
    assert "101 cases x 1 runs" in capsys.readouterr().out
    seen.clear()
    assert run.main(["--core", "--case", "connected_identity", "--case", "stated_number", "--n", "1",
                     "--time-zone", "Asia/Kolkata"]) == 0
    assert seen == ["connected_identity"]  # --case then --core: only the core cases named
    assert run.main(["--core", "--case", "stated_number", "--n", "1", "--time-zone", "Asia/Kolkata"]) == 2
    assert "no case to run" in capsys.readouterr().out


def test_main_stops_a_gemini_model_on_a_quota_and_says_so(monkeypatch, capsys):
    from evals import gemini_client, run
    monkeypatch.setattr(run.LocalServer, "start", lambda self: None)
    monkeypatch.setattr(run.LocalServer, "stop", lambda self: None)
    monkeypatch.setattr(gemini_client, "load_key", lambda n: "fake-key-for-the-test")
    made = []

    def fake_gemini(server, case, zone, host):
        made.append(case["id"])
        if len(made) == 2:
            raise gemini_client.QuotaStop("429 RESOURCE_EXHAUSTED")
        return {"passed": True, "reasons": [], "reply": "", "calls": [], "seconds": 1.0, "models": [], "tokens": 0,
                "cost_usd": None}

    monkeypatch.setattr(gemini_client, "run_once_gemini", fake_gemini)
    assert run.main(["--client", "gemini", "--n", "1", "--time-zone", "Asia/Kolkata"]) == 1
    out = capsys.readouterr().out
    assert len(made) == 2 and "Stopped, this model's runs end here" in out and "QuotaStop" in out
    assert "fake-key-for-the-test" not in out


# ---------------------------------------------------------------- the new cases that need no data read, both ways

def _seeded_case(app_settings, case_id: str, server: LocalServer | None = None) -> tuple[dict, int, dict]:
    """The case resolved for a fresh person seeded as it says. Pass the server on for a second person in one test:
    two servers made in the same second would name their people alike."""
    if server is None:
        server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
        server.settings = app_settings
    case = _cases()[case_id]
    zone = "Asia/Kolkata"
    user_id, _, seeded = server.new_person(zone, case.get("seed") or [])
    return judge.resolve(case, {**dates_for(zone), **{f"seed.{k}": v for k, v in seeded.items()}}), user_id, seeded


def test_the_save_food_case_judges_as_designed(app_settings):
    case, user_id, _ = _seeded_case(app_settings, "save_food_asked")
    sent = {"name": "Protein shake", "serving_label": "1 scoop", "kcal_per_serving": 150}
    assert judge.judge(case, [call("save_food", sent)], "Saved.", person_rows(user_id))  # nothing saved yet
    assert "error" not in services.save_food(user_id, sent, "claude")
    assert judge.judge(case, [call("whoami"), call("save_food", sent)], "Saved: 150 kcal a scoop.",
                       person_rows(user_id)) == []
    assert judge.judge(case, [call("save_food", sent), call("log_food", {})], "Saved and logged.", person_rows(user_id))
    assert judge.judge(case, [call("save_food", {**sent, "kcal_per_serving": 120})], "Saved.", person_rows(user_id))


def test_the_edit_case_changes_the_run_in_place(app_settings):
    case, user_id, seeded = _seeded_case(app_settings, "edit_fixes_minutes")
    [run_row] = person_rows(user_id)["cardio_entry"]
    assert (run_row["minutes"], run_row["distance_km"], run_row["date"]) == (30, 5, dates_for("Asia/Kolkata")["yesterday"])
    sent = {"kind": "cardio_entry", "entry_id": seeded["run"], "minutes": 32}
    assert judge.judge(case, [call("edit_entry", sent)], "Fixed.", person_rows(user_id))  # not yet changed
    assert "error" not in services.edit_entry(user_id, "cardio_entry", seeded["run"], {"minutes": 32}, "claude")
    assert judge.judge(case, [call("get_log"), call("edit_entry", sent)], "Fixed: 32 minutes.", person_rows(user_id)) == []
    again = [call("log_cardio", {"exercise": "Outdoor Run", "minutes": 32}), call("edit_entry", sent)]
    assert judge.judge(case, again, "Logged it again.", person_rows(user_id))  # a second run logged


def test_the_food_routine_and_exercise_list_cases_judge_as_designed(app_settings):
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    food, user_id, _ = _seeded_case(app_settings, "food_lookup", server)
    found = services.find_food(user_id, "oats bowl")
    assert found["saved"][0]["kcal_per_serving"] == 300
    assert judge.judge(food, [call("find_food", {"query": "oats bowl"})], "One bowl is 300 kcal.", {}) == []
    assert judge.judge(food, [call("get_data", {"area": "saved_foods"})], "300 kcal a bowl.", {}) == []
    assert judge.judge(food, [call("find_food")], "About 350 kcal.", {})
    assert judge.judge(food, [], "One bowl is 300 kcal.", {})  # read nothing
    routine, user_id, _ = _seeded_case(app_settings, "routine_read", server)
    weights = {lift["exercise"]: lift for lift in services.get_routines(user_id)["routines"][0]["lifts"]}
    assert set(weights) == {"Bench Press", "Overhead Press"}
    reply = "Push day: Bench Press 3 x 8 at 60 kg, Overhead Press 3 x 10 at 40 kg."
    assert judge.judge(routine, [call("get_routines")], reply, {}) == []
    assert judge.judge(routine, [call("get_data")], reply, {})  # no routine read
    assert judge.judge(routine, [call("get_routines")], "Bench and overhead press.", {})
    listed = _cases()["exercise_list"]
    names = {e["name"] for e in services.all_exercises("lift")["exercises"] if e["category"] == "squat"}
    assert {"Goblet Squat", "Bulgarian Split Squat"} <= names
    assert judge.judge(listed, [call("list_exercises", {"category": "squat"})], ", ".join(sorted(names)), {}) == []
    assert judge.judge(listed, [call("list_exercises")], "Back Squat and Front Squat.", {})
    assert judge.judge(listed, [], ", ".join(sorted(names)), {})


def test_the_catch_up_case_judges_the_rows_on_each_date(app_settings):
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    case, user_id, _ = _seeded_case(app_settings, "catch_up", server)
    dates = dates_for("Asia/Kolkata")
    assert judge.judge(case, [call("whoami")], "Logged.", person_rows(user_id))  # nothing logged
    services.log_lifts(user_id, {"date": dates["days_ago:3"],
                                 "sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}] * 3}, "claude")
    services.log_food(user_id, {"date": dates["days_ago:2"],
                                "entries": [{"description": "Chicken biryani", "kcal": 640, "meal": "lunch"}]}, "claude")
    calls = [call("log_lifts"), call("log_food"), call("log_bodyweight")]
    assert judge.judge(case, calls, "Logged.", person_rows(user_id)) == ["bodyweight: 0 rows, expected 1"]
    services.log_bodyweight(user_id, {"kg": 80.2, "date": dates["today"]}, "claude")  # on the wrong date
    assert judge.judge(case, calls, "Logged.", person_rows(user_id))
    _, right, _ = _seeded_case(app_settings, "catch_up", server)
    services.log_lifts(right, {"date": dates["days_ago:3"],
                               "sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}] * 3}, "claude")
    services.log_food(right, {"date": dates["days_ago:2"],
                              "entries": [{"description": "Chicken biryani", "kcal": 640}]}, "claude")
    services.log_bodyweight(right, {"kg": 80.2, "date": dates["yesterday"]}, "claude")
    assert judge.judge(case, calls, "Logged all three.", person_rows(right)) == []
    assert "{{" not in case["say"] and "three days ago" in case["say"]  # relative days: right on any run day


def test_the_import_case_is_the_hacker_s_diet_layout_and_previews_first(app_settings, monkeypatch):
    # The case's dates are fixed in September 2026: the clock is pinned, so the test holds on any real day.
    monkeypatch.setattr(db, "utcnow", lambda: dt.datetime(2026, 11, 17, 9, 0, tzinfo=dt.timezone.utc))
    case, user_id, _ = _seeded_case(app_settings, "import_other_app")
    assert "Date,Weight,Rung,Flag,Comment" in case["say"] and "Preferences,1.0,kilogram" in case["say"]
    rows = [{"kind": "bodyweight", "date": d, "weight": float(w)} for d, w in
            re.findall(r"(\d{4}-\d{2}-\d{2}),(\d+\.\d),\d+,[01],", case["say"])]
    assert len(rows) == 5
    preview = services.import_entries(user_id, {"rows": rows, "weight_unit": "kg"}, "claude")
    assert preview["dry_run"] is True and preview["row_outcomes"]["written"] == 5
    sent = {"rows": rows, "weight_unit": "kg"}
    assert judge.judge(case, [call("import_entries", sent)], "Write these 5 weigh-ins?", person_rows(user_id)) == []
    assert judge.judge(case, [call("import_entries", {**sent, "weight_unit": "lb"})], "Write them?",
                       person_rows(user_id))
    assert judge.judge(case, [call("import_entries", {**sent, "dry_run": False})], "Written. More?",
                       person_rows(user_id))
    assert judge.judge(case, [call("import_entries", sent)], "Here is the preview.", person_rows(user_id))


@pytest.mark.parametrize("pair", [("inc_10k_plan", "look_10k_friend"), ("inc_trip_meals", "look_trip_hours"),
                                  ("inc_deload", "look_deload")])
def test_each_draft_incidental_case_has_its_look_alike_and_both_judge_by_their_tags(pair):
    incidental, look_alike = (_cases()[i] for i in pair)
    assert (incidental["kind"], look_alike["kind"]) == ("incidental", "look_alike")
    assert incidental["seed"] == look_alike["seed"] == [{"service": "sixteen_weeks"}]
    read = [call("whoami"), call("get_data", {"area": "cardio"})]
    assert judge.judge(incidental, read, "Here is the plan.", {}) == []  # picked up
    assert judge.judge(incidental, [call("whoami")], "Here is the plan.", {})  # not picked up
    assert judge.judge(incidental, read + [call("log_cardio")], "Here is the plan.", {})
    assert judge.judge(look_alike, [call("whoami")], "Here is the plan.", {}) == []
    assert judge.judge(look_alike, read, "Here is the plan.", {}) == ["get_data was called and should not have been"]


# ---------------------------------------------------------------- the fix round of G1 (the review of 2026-11-17)

def test_the_fixed_help_texts_of_an_answer_are_never_spotter_s_figures(make_user):
    """M1: what_is_logged's how and its field words hold 0.5, 3.5, 10, 30, 100 and 200 on every account."""
    user = make_user(time_zone="Asia/Kolkata")
    answer = services.what_is_logged(user.id)
    assert any(word in answer["how"] for word in ("100", "0.5"))  # the texts still hold them
    calls = [call("whoami"), answered("what_is_logged", answer)]
    assert {v for v in judge.spotter_figures(calls) if not judge._small_whole(v)} == set()
    for reply in ("Aim for 30 minutes of cardio and 10 reps a set.", "100 squats a week and 200 minutes of walking.",
                  "Gain 0.5 kg a week; MET x 3.5."):
        assert judge.quoted_figures(reply, calls) == [], reply
    fixed = {"how": "per_day over 28 days, 7,700 kcal", "meaning": "reps over 30", "fields": [["reps", "1 to 10", 12]],
             "summary": "12 sets, 7700 kcal"}
    assert judge.quoted_figures("12 sets, 7,700 kcal.", [answered("get_trends", fixed)]) == ["12", "7,700"]
    assert judge.quoted_figures("30 reps, 1 to 10.", [answered("get_trends", fixed)]) == []


def test_how_am_i_doing_fails_a_reply_of_advice_alone(make_user, monkeypatch):
    """M1, the review's probe: four weeks of everything, what_is_logged read, two advice figures quoted."""
    from evals.four_weeks import four_weeks
    monkeypatch.setattr(db, "utcnow", lambda: dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.timezone.utc))
    user = make_user(time_zone="Asia/Kolkata")
    assert "error" not in four_weeks(user.id)
    case = judge.resolve(_cases()["how_am_i_doing"], {})
    calls = [call("whoami"), answered("what_is_logged", services.what_is_logged(user.id))]
    for reply in ("You are doing fine. Aim for 30 minutes of cardio and 10 reps a set.",
                  "Keep going: 100 squats a week and 200 minutes of walking would help."):
        assert judge.judge(case, calls, reply, {}), reply


def test_quotes_one_of_needs_the_asked_figure_from_this_run_s_reads():
    """M2: the figure a direct case asks about is one of the run's own, by any read."""
    expect = {"quotes_one_of": ["116.7", "100"]}
    squat = answered("get_data", {"columns": ["date", "weight_kg", "estimated_max_kg"],
                                  "rows": [["2026-11-13", 100.0, 116.7]]})
    goals = answered("get_goals", {"goals": [{"goal_id": 2, "start_value": 80.0, "target_value": 150.0}]})
    assert judge.judge_calls(expect, [squat], "100 kilos for 5 this month.") == []
    assert judge.judge_calls(expect, [squat], "An estimated max of 116.7 kg.") == []
    assert judge.judge_calls(expect, [goals], "Started at 80 kg, target 150 kg: you are at 100 kg now.") == [
        "the reply quotes none of 116.7, 100 from this run's data reads (quoted: 80, 150)"]  # 100 made up
    assert judge.judge_calls(expect, [squat], "From 1116.7 kg.") == [
        "the reply quotes none of 116.7, 100 from this run's data reads"]
    assert judge.judge_calls({"quotes_one_of": ["1980"]}, [answered("get_log", {"kcal": 1980})], "1,980 kcal.") == []


@pytest.mark.parametrize("figures, problem", [
    ([], "expect.quotes_one_of must be a non-empty list of figures"),
    ("116.7", "expect.quotes_one_of must be a non-empty list of figures"),
    ([116.7], 'expect.quotes_one_of[0] must be a figure written as text, such as "116.7"'),
    (["about"], 'expect.quotes_one_of[0] must be a figure written as text, such as "116.7"'),
    (["5"], "expect.quotes_one_of[0] 5 is a whole number under 10, which never counts"),
])
def test_quotes_one_of_is_checked_when_the_cases_load(figures, problem):
    assert judge.case_problems({"id": "a", "say": "x", "expect": {"quotes_one_of": figures}}) == [problem]


def test_a_whole_number_under_10_never_counts_with_a_unit_or_without_and_a_decimal_does():
    """The owner's word: a whole number under 10 never counts, with a unit or without; a decimal under 10 does."""
    answer = answered("get_data", {"rows": [[5, 3, 8, 3.3, 0.24, 9.0, 10]], "columns": ["km", "sets", "reps", "a",
                                                                                         "b", "c", "d"]})
    assert judge.quoted_figures("5 km, 3 sessions, 8 reps, 9, 9.0 kg.", [answer]) == []
    assert judge.quoted_figures("3.3 kg behind, 0.24 kg a week, 10 sets.", [answer]) == ["3.3", "0.24", "10"]


@pytest.mark.parametrize("placeholder, why", [
    ("{{days_ago:61}}", "N runs from 0 to 60"), ("{{days_ago:99}}", "N runs from 0 to 60"),
    ("{{next_month_end:13}}", "N runs from 1 to 12"), ("{{next_month_end:0}}", "N runs from 1 to 12"),
    ("{{tomorrow}}", "no such name"),
])
def test_a_placeholder_no_run_can_fill_is_a_problem_when_the_cases_load(placeholder, why):
    """N1: before, it loaded clean and failed only mid-run, at resolve."""
    case = {"id": "a", "say": "x", "expect": {"function": "log_food", "inputs_include": {"date": placeholder}}}
    assert judge.case_problems(case) == [f"{placeholder} is not a placeholder: {why}"]
    fine = {"id": "a", "say": "x", "expect": {"function": "f", "inputs_include": {"date": "{{days_ago:60}}"}},
            "db": [{"table": "goal", "all": {"target_date": "{{next_month_end:12}}"}}]}
    assert judge.case_problems(fine) == []


def test_the_catch_up_goes_through_the_log_functions_never_an_import():
    """The owner's ruling: a catch-up told in chat is logged, one path a source; import_entries is a must_not."""
    case = _cases()["catch_up"]
    assert "import_entries" in case["calls"]["must_not"] and "import_entries" not in case["calls"]["must"]
    assert judge.judge(case, [call("import_entries")], "Previewed.", {})
    assert "import_entries was called and should not have been" in judge.judge(
        case, [call("log_lifts"), call("import_entries")], "Logged.", {})


def test_one_run_s_error_never_loses_the_others_and_the_kept_results_are_written(monkeypatch, capsys):
    """S3: a run that raises is that run's failure, with its reason; the table prints; results.json is written."""
    from evals import run
    monkeypatch.setattr(run.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(run.LocalServer, "start", lambda self: None)
    monkeypatch.setattr(run.LocalServer, "stop", lambda self: None)
    monkeypatch.setattr(run.judge, "load_cases", lambda: [{"id": "one", "say": "x", "expect": {"reply_asks": True}}])
    made = []

    def fake_run_once(server, case, zone, claude, model, env, workdir, label, keep, effort=None):
        made.append(label)
        if len(made) == 3:
            raise RuntimeError("the CLI crashed")
        return {"passed": True, "reasons": [], "reply": "Done?", "calls": [], "seconds": 1.0, "models": [model],
                "other_models": [], "tokens": 0, "cost_usd": 0.0}

    monkeypatch.setattr(run, "run_once", fake_run_once)
    kept = []
    real_mkdtemp = run.tempfile.mkdtemp
    monkeypatch.setattr(run.tempfile, "mkdtemp", lambda prefix: kept.append(real_mkdtemp(prefix=prefix)) or kept[-1])
    assert run.main(["--n", "4", "--time-zone", "Asia/Kolkata", "--keep"]) == 1
    out = capsys.readouterr().out
    assert made == ["one-1", "one-2", "one-3", "one-4"]  # the fourth run still ran
    assert "one-3: FAIL" in out and "the run raised RuntimeError: the CLI crashed" in out
    assert "counted: 3 of 4 runs passed" in out
    results = json.loads((Path(kept[0]) / "results.json").read_text(encoding="utf-8"))
    assert [r["passed"] for r in results[0]["runs"]] == [True, True, False, True]
    import shutil
    shutil.rmtree(kept[0], ignore_errors=True)


def test_a_quota_stop_keeps_the_run_it_stopped_as_a_failure_with_its_reason(monkeypatch, capsys):
    """S3: the stopped run is a blank outcome, failed, its reason the stop."""
    from evals import gemini_client, run
    monkeypatch.setattr(run.LocalServer, "start", lambda self: None)
    monkeypatch.setattr(run.LocalServer, "stop", lambda self: None)
    monkeypatch.setattr(gemini_client, "load_key", lambda n: "fake-key-for-the-test")
    monkeypatch.setattr(run.judge, "load_cases", lambda: [{"id": "one", "say": "x", "expect": {"reply_asks": True}}])

    def fake_gemini(server, case, zone, host):
        raise gemini_client.QuotaStop("429 for fake-key-for-the-test")

    monkeypatch.setattr(gemini_client, "run_once_gemini", fake_gemini)
    assert run.main(["--client", "gemini", "--n", "3", "--time-zone", "Asia/Kolkata"]) == 1
    out = capsys.readouterr().out
    assert "one-1: FAIL" in out and "one-2" not in out and "counted: 0 of 1 runs passed" in out
    assert "stopped: one-1: QuotaStop: 429 for [key hidden]" in out and "fake-key-for-the-test" not in out


def test_a_raised_run_s_text_is_masked_for_the_token_as_every_kept_text_is(monkeypatch, capsys):
    """The re-check's nit: the text of a run that raises is masked for every token the server signed, and for any
    text shaped like one."""
    from evals import run
    monkeypatch.setattr(run.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(run.LocalServer, "start", lambda self: None)
    monkeypatch.setattr(run.LocalServer, "stop", lambda self: None)
    monkeypatch.setattr(run.judge, "load_cases", lambda: [{"id": "one", "say": "x", "expect": {"reply_asks": True}}])

    def fake_run_once(server, case, zone, claude, model, env, workdir, label, keep, effort=None):
        server.issued.append("signed-token-of-this-person")
        raise RuntimeError("boom Bearer eyJSECRET.abc.def, and signed-token-of-this-person")

    monkeypatch.setattr(run, "run_once", fake_run_once)
    assert run.main(["--n", "1", "--time-zone", "Asia/Kolkata"]) == 1
    out = capsys.readouterr().out
    assert "the run raised RuntimeError: boom Bearer [token hidden], and [token hidden]" in out
    assert "eyJSECRET" not in out and "signed-token-of-this-person" not in out
    assert run.kept_text("a eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln b", []) == "a [token hidden] b"


# ---------------------------------------------------------------- exercise-list.md, C4g: a call's own answer, sets, a second turn

def refused(name, result, arguments=None):
    """A refused call as the recorder keeps it: its first code as error, its whole answer as result."""
    return {**call(name, arguments, is_error=True), "answered": True, "error": result.get("error"), "result": result}


def ok(name, result, arguments=None):
    return {**call(name, arguments), "answered": True, "result": result}


PICK = {"error": "pick_lift", "message": "Set 1: 'squat' could be Back Squat or Smith Squat.", "errors": [
    {"error": "pick_lift", "message": "Set 1: 'squat' could be Back Squat or Smith Squat."}]}
BEHIND = {"error": "invalid_date", "message": "2 problems.", "errors": [
    {"error": "invalid_date", "message": "date is not a date."}, {"error": "pick_lift", "message": "Set 1: ..."}]}


def test_error_code_reads_every_code_of_a_combined_refusal_not_only_the_first():
    expect = {"error_code": {"log_lifts": "pick_lift"}}
    assert judge.error_codes(refused("log_lifts", BEHIND)) == {"invalid_date", "pick_lift"}
    assert judge.judge_calls(expect, [refused("log_lifts", BEHIND)], "Which?") == []  # pick_lift behind a date error
    assert judge.judge_calls(expect, [refused("log_lifts", PICK)], "Which?") == []
    assert judge.judge_calls(expect, [{**call("log_lifts", is_error=True), "error": "pick_lift"}], "?") == []  # no result
    assert judge.judge_calls(expect, [refused("log_lifts", {"error": "unknown_exercise"})], "Which?") == [
        "no log_lifts call was refused with pick_lift (calls of it: 1; codes: unknown_exercise)"]
    assert judge.judge_calls(expect, [ok("log_lifts", {"kind": "lift_session"})], "Done.") == [
        "no log_lifts call was refused with pick_lift (calls of it: 1; codes: none)"]
    assert judge.judge_calls(expect, [refused("set_goal", PICK)], "Which?")  # another function's refusal
    assert judge.error_codes(ok("log_lifts", {"error": "pick_lift"})) == set()  # answered without error: no code


def test_no_error_code_and_refusals():
    no = {"no_error_code": {"log_lifts": "pick_lift"}}
    assert judge.judge_calls(no, [], "Done.") == [] and judge.judge_calls(no, [ok("log_lifts", {})], "Done.") == []
    assert judge.judge_calls(no, [ok("log_lifts", {}), refused("log_lifts", BEHIND)], "Done.") == [
        "a log_lifts call was refused with pick_lift"]
    every = {"refusals": {"log_lifts": "unknown_exercise"}}
    assert judge.judge_calls(every, [call("whoami")], "Which lift?") == []  # no call of it at all passes
    assert judge.judge_calls(every, [refused("log_lifts", {"error": "unknown_exercise"})] * 2, "Which?") == []
    assert judge.judge_calls(every, [refused("log_lifts", {"error": "unknown_exercise"}),
                                     ok("log_lifts", {"kind": "lift_session"})], "Done.") == [
        "log_lifts was called and not refused with unknown_exercise (codes: none)"]
    assert judge.judge_calls(every, [refused("log_lifts", {"error": "missing_field", "errors": [
        {"error": "missing_field"}, {"error": "unknown_exercise"}]})], "Which?") == []


def test_answer_has_a_key_or_an_object_on_an_answer_without_error():
    key = {"answer_has": {"log_lifts": "hand_check"}}
    assert judge.judge_calls(key, [ok("log_lifts", {"hand_check": "Ask the person once."})], "?") == []
    for calls in ([ok("log_lifts", {"summary": "Logged."})], [ok("log_lifts", {"hand_check": None})],
                  [refused("log_lifts", {"error": "x", "hand_check": "y"})], [call("log_lifts")], []):
        assert judge.judge_calls(key, calls, "?") == ['no log_lifts answer without error holds "hand_check"'], calls
    found = {"answer_has": {"list_exercises": {"exercises": [{"name": "Hack Squat"}]}}}
    listed = ok("list_exercises", {"exercises": [{"name": "Back Squat"}, {"name": "Hack Squat"}], "count": 2})
    assert judge.judge_calls(found, [listed], "Hack Squat.") == []
    assert judge.judge_calls(found, [ok("list_exercises", {"exercises": [{"name": "Back Squat"}]})], "Hack Squat.")


def test_the_new_keys_are_checked_when_the_cases_load():
    base = {"id": "a", "say": "x"}
    assert judge.case_problems({**base, "expect": {"error_code": {"log_lifts": "pick_lift"}}}) == []
    assert judge.case_problems({**base, "expect": {"error_code": "pick_lift"}}) == [
        "expect.error_code must be an object of function names"]
    assert judge.case_problems({**base, "expect": {"refusals": {"log_lifts": 3}}}) == [
        "expect.refusals gives each function an error code"]
    assert judge.case_problems({**base, "expect": {"answer_has": {"log_lifts": ""}}}) == [
        "expect.answer_has gives each function a key or an object"]
    assert judge.case_problems({**base, "expect": {"any_of": [{"no_error_code": {}}]}}) == [
        "expect.any_of[0].no_error_code must be an object of function names"]
    assert judge.case_problems({**base, "expect": {"reply_asks": True}, "then": "the barbell one"}) == []
    for then in ("", "  ", "two\nlines", 3):
        assert judge.case_problems({**base, "expect": {"reply_asks": True}, "then": then}) == [
            "then must be a non-empty line: the person's second message"], then
    assert judge.case_problems({**base, "expect": {"reply_asks": True},
                                "db": [{"table": "lift_set", "where": {}, "count": 0}]}) == [
        "db[0] where must be a non-empty object"]


def test_a_db_count_and_a_first_reply_pattern_are_checked_when_the_cases_load():
    base = {"id": "a", "say": "x", "expect": {"reply_asks": True}}
    for bad in ("3", True, [], [1, "2"], [-1], 1.5, [True]):
        assert judge.case_problems({**base, "db": [{"table": "lift_set", "count": bad}]}) == [
            "db[0] count must be a whole number, or a non-empty list of whole numbers"], bad
    for good in (0, 3, [0, 3]):
        assert judge.case_problems({**base, "db": [{"table": "lift_set", "count": good}]}) == [], good
    first = {"reply_asks": True, "first_reply_lacks_pattern": ["x"]}
    assert judge.case_problems({**base, "expect": first}) == [
        "first_reply_lacks_pattern needs then: a case of one turn has no first reply"]
    assert judge.case_problems({**base, "expect": {"any_of": [first]}}) == [
        "first_reply_lacks_pattern needs then: a case of one turn has no first reply"]
    assert judge.case_problems({**base, "expect": first, "then": "the barbell one"}) == []


def test_a_db_count_may_be_a_list_of_the_counts_allowed():
    rows = {"lift_set": [{"id": n, "deleted_at": None} for n in range(3)]}
    assert judge.judge_db([{"table": "lift_set", "count": [0, 3]}], rows) == []
    assert judge.judge_db([{"table": "lift_set", "count": [0, 3]}], {"lift_set": []}) == []
    assert judge.judge_db([{"table": "lift_set", "count": [0, 1]}], rows) == ["lift_set: 3 rows, expected 0 or 1"]
    assert judge.judge_db([{"table": "lift_set", "count": 2}], rows) == ["lift_set: 3 rows, expected 2"]


def test_judge_turns_reads_the_first_reply_and_wants_the_inputs_on_a_second_turn_success():
    two = {"id": "t", "say": "squat", "then": "the barbell one",
           "expect": {"function": "log_lifts", "inputs_include": {"sets": [{"exact": True}]},
                      "first_reply_lacks_pattern": ["\\blogged\\b"]}}
    exact = {"sets": [{"exercise": "Back Squat", "exact": True}]}
    good = {**call("log_lifts", exact), "turn": 2}
    assert judge.judge_turns(two, [good], "Which squat was it?") == []
    assert judge.judge_turns(two, [good], "Logged Back Squat.") == [
        f"the first turn's reply matches {chr(92) + 'blogged' + chr(92) + 'b'!r}: it says 'Logged'"]
    assert judge.judge_turns(two, [good]) == ["the first turn's reply was not kept, so it could not be judged"]
    refused = {**call("log_lifts", exact, is_error=True), "turn": 2}
    alias = {**call("log_lifts", {"sets": [{"exercise": "barbell squat"}]}), "turn": 2}
    assert judge.judge_turns(two, [refused, alias], "Which?") == [
        'no log_lifts call that succeeded in the second turn sent {"sets": [{"exact": true}]}']
    assert judge.judge_turns(two, [{**good, "turn": 1}], "Which?")  # the first turn's write: not the answer
    assert judge.case_problems({"id": "a", "say": "x", "then": "y", "expect": {"first_reply_lacks_pattern": "("}}) == [
        "expect.first_reply_lacks_pattern must be a non-empty list of regular expressions"]


def test_db_where_narrows_the_rows_each_check_reads():
    rows = {"lift_set": [{"id": 1, "exercise": "Smith Squat", "date": "2026-11-14", "deleted_at": None},
                         {"id": 2, "exercise": "Back Squat", "date": "2026-11-17", "deleted_at": None},
                         {"id": 3, "exercise": "Back Squat", "date": "2026-11-17", "deleted_at": "2026-11-17"}]}
    today = {"table": "lift_set", "where": {"date": "2026-11-17"}, "count": 1, "all": {"exercise": "Back Squat"}}
    assert judge.judge_db([today], rows) == []
    assert judge.judge_db([{**today, "all": {"exercise": "Smith Squat"}}], rows) == [
        'lift_set where {"date": "2026-11-17"}: rows not matching {"exercise": "Smith Squat"}: '
        '{"date": "2026-11-17", "id": 2, "exercise": "Back Squat"}']
    assert judge.judge_db([{"table": "lift_set", "where": {"exercise": "Back Squat"}, "state": "deleted",
                            "count": 1}], rows) == []
    assert judge.judge_db([{"table": "lift_set", "where": {"exercise": "Front Squat"}, "count": 0}], rows) == []


def test_person_rows_gives_every_set_of_the_person_s_sessions_with_its_session_s_date_and_delete(make_user):
    mine, theirs = make_user(), make_user()
    kept = services.log_lifts(mine.id, {"sets": [{"exercise": "Plank", "weight_kg": 0, "time_s": 90},
                                                 {"exercise": "Back Squat", "weight_kg": 100, "reps": 5}]}, "claude")
    gone = services.log_lifts(mine.id, {"new_session": True, "sets": [
        {"exercise": "Farmer's Carry", "weight_kg": 32, "distance_m": 40}]}, "claude")
    services.delete_entry(mine.id, "lift_session", gone["id"])
    services.log_lifts(theirs.id, {"sets": [{"exercise": "Deadlift", "weight_kg": 120, "reps": 3}]}, "claude")
    sets = person_rows(mine.id)["lift_set"]
    assert [(s["exercise"], s["weight_kg"], s["reps"], s["time_s"], s["distance_m"], s["session_id"], s["set_number"])
            for s in sets] == [("Plank", 0, None, 90, None, kept["id"], 1), ("Back Squat", 100, 5, None, None,
                                                                              kept["id"], 1),  # a lift's own count
                               ("Farmer's Carry", 32, None, None, 40, gone["id"], 1)]
    assert sets[0]["date"] == kept["session"]["date"] and sets[0]["deleted_at"] is None
    assert sets[2]["deleted_at"] is not None  # a set of a deleted session reads as deleted
    assert judge.judge_db([{"table": "lift_set", "count": 2}, {"table": "lift_set", "state": "deleted", "count": 1}],
                          person_rows(mine.id)) == []
    assert [s["exercise"] for s in person_rows(theirs.id)["lift_set"]] == ["Deadlift"]
    assert "lift_set" in judge.DB_TABLES and "exercise" in judge._rows_text([{"exercise": "Plank"}])


def test_the_recorder_keeps_every_call_s_answer_as_result_and_a_data_read_s_as_answer_too(provider, make_user,
                                                                                         token_for):
    make_user(subject="user_rec_r", email="r@example.com", name="R", time_zone="Asia/Kolkata")
    recorder = CallRecorder(create_app(settings(), fetch=provider.fetch))
    token = token_for("user_rec_r")
    sent = (("whoami", {}), ("log_lifts", {"sets": [{"exercise": "zorbinator", "weight_kg": 20, "reps": 8}]}),
            ("log_lifts", {"sets": [{"exercise": "bench", "weight_kg": 60, "reps": 5}]}), ("get_log", {}))
    with TestClient(recorder) as client:
        for i, (name, arguments) in enumerate(sent, 1):
            client.post("/mcp", headers={**HEADERS, "Authorization": f"Bearer {token}"},
                        json={"jsonrpc": "2.0", "id": i, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments}})
    calls = recorder.calls(token)
    assert calls[0]["result"]["account_id"] and "answer" not in calls[0]
    assert calls[1]["is_error"] is True and calls[1]["error"] == "unknown_exercise"
    assert [e["error"] for e in calls[1]["result"]["errors"]] == ["unknown_exercise"] and "answer" not in calls[1]
    assert calls[2]["is_error"] is False and calls[2]["result"]["session"]["sets"][0]["exercise"] == "Bench Press"
    assert calls[3]["answer"] == calls[3]["result"]  # a data read keeps both, the same answer
    assert judge.judge_calls({"refusals": {"log_lifts": "unknown_exercise"}}, calls[:2], "Which?") == []


def test_a_case_with_then_names_its_session_and_resumes_it():
    first = cli_command("claude", "squat 3x5 at 80", "c.json", "claude-sonnet-5-5", "medium", "s-1")
    second = cli_command("claude", "the barbell one", "c.json", "claude-sonnet-5-5", "medium", "s-1", True)
    assert "--no-session-persistence" not in first + second
    assert first[first.index("--session-id") + 1] == "s-1" and "--resume" not in first
    assert second[second.index("--resume") + 1] == "s-1" and "--session-id" not in second
    one = cli_command("claude", "squat", "c.json", None)
    assert "--no-session-persistence" in one and "--session-id" not in one and "--resume" not in one


def cli_folder(cwd) -> str:
    """The Claude CLI's name for a working folder's sessions folder, as the main chat read it from the CLI on
    2026-11-20: every character but a to z, A to Z and 0 to 9 as "-" (cut at 200 characters with a hash, which no
    temporary folder here reaches). The runner does not rely on it: forget_session finds a session by its id."""
    return re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))


class TurnRecorder:
    """Stands in for the recorder: each fake CLI turn adds its calls to it, as the server would."""

    def __init__(self):
        self.made: list[dict] = []

    def calls(self, token):
        return [dict(c) for c in self.made]

    def methods(self, token):
        return []

    def got_instructions(self, token):
        return True


def _two_turns(server, monkeypatch, outputs: list[str], made: list[list[dict]] | None = None,
               config: Path | None = None, expect: dict | None = None) -> tuple[dict, list]:
    """run_once of a two-turn case through a fake CLI that prints each of outputs in turn, and makes the calls of
    made[i] in turn i (server.recorder a TurnRecorder then). Like the CLI, its first turn saves the session to
    <config>/projects/<the working folder's name>/<id>.jsonl. config is a fake CLAUDE_CONFIG_DIR, a temporary one when
    left out: no test reads the real ~/.claude."""
    from evals import run
    given = []

    def fake_cli(command, **kwargs):
        given.append((command, kwargs["cwd"]))
        if made:
            server.recorder.made += made[len(given) - 1]
        if "--session-id" in command:  # as the CLI does: the session, and a folder of its own beside it
            saved = Path(kwargs["env"]["CLAUDE_CONFIG_DIR"]) / "projects" / cli_folder(kwargs["cwd"])
            session = command[command.index("--session-id") + 1]
            (saved / session / "tool-results").mkdir(parents=True, exist_ok=True)
            (saved / session / "tool-results" / "out.txt").write_text("x", encoding="utf-8")
            (saved / f"{session}.jsonl").write_text("{}\n", encoding="utf-8")
        return run.subprocess.CompletedProcess(command, 0, stdout=outputs[len(given) - 1], stderr="")

    monkeypatch.setattr(run.subprocess, "run", fake_cli)
    case = {"id": "two", "say": "squat 3x5 at 80", "then": "the barbell one", "expect": expect or {"reply_lacks": ["?"]}}
    with tempfile.TemporaryDirectory() as store, tempfile.TemporaryDirectory() as folder:
        env, _ = child_env({"PATH": "/bin", "CLAUDE_CONFIG_DIR": str(config or store)})
        outcome = run.run_once(server, case, "Asia/Kolkata", "claude", "claude-opus-5-5", env, Path(folder), "two-1",
                               False)
    return outcome, given


def test_a_two_turn_run_sends_both_lines_in_one_session_and_judges_the_last_reply(app_settings, monkeypatch):
    usage = {"claude-opus-5-5": {"costUSD": 0.1}}
    turn = lambda reply, cost: json.dumps({**SAMPLE, "result": reply, "total_cost_usd": cost, "modelUsage": usage})  # noqa: E731
    outcome, given = _two_turns(_model_server(app_settings), monkeypatch, [turn("Back Squat or Smith Squat?", 0.1),
                                                            turn("Logged Back Squat.", 0.2)])
    assert [c[2] for c, _ in given] == ["squat 3x5 at 80", "the barbell one"]
    assert given[0][1] == given[1][1]  # one working folder for both
    session = given[0][0][given[0][0].index("--session-id") + 1]
    assert given[1][0][given[1][0].index("--resume") + 1] == session
    assert outcome["passed"] is True and outcome["reply"] == "Logged Back Squat."
    assert outcome["cost_usd"] == pytest.approx(0.3) and outcome["num_turns"] == 6
    assert outcome["tokens"] == 2 * (10 + 14126 + 105829 + 303)
    assert "session_id" not in outcome and "session_left" not in outcome  # deleted: nothing to name
    assert outcome["replies"] == ["Back Squat or Smith Squat?", "Logged Back Squat."]


def test_a_two_turn_run_judges_its_first_reply_by_the_case_s_first_reply_pattern(app_settings, monkeypatch):
    usage = {"claude-opus-5-5": {"costUSD": 0.1}}
    turn = lambda reply: json.dumps({**SAMPLE, "result": reply, "modelUsage": usage})  # noqa: E731
    expect = {"reply_has": ["Logged"], "first_reply_lacks_pattern": [r"\blogged\b"]}
    server = _model_server(app_settings)
    asked, _ = _two_turns(server, monkeypatch, [turn("Which squat?"), turn("Logged Back Squat.")], expect=expect)
    assert asked["passed"] is True
    claimed, _ = _two_turns(server, monkeypatch, [turn("Logged Back Squat."), turn("Logged Back Squat.")],
                            expect=expect)
    assert claimed["reasons"] == [f"the first turn's reply matches {chr(92) + 'blogged' + chr(92) + 'b'!r}: it says "
                                  "'Logged'"]


def test_a_two_turn_run_deletes_its_own_saved_session_and_nothing_else(app_settings, monkeypatch):
    """The CLI keeps a session at <config dir>/projects/<folder>/<id>.jsonl, with a folder <id>/ beside it. Only the
    run's own id goes, and its folder once empty; another id, and another project's folder, stay."""
    usage = {"claude-opus-5-5": {"costUSD": 0.1}}
    turn = lambda reply: json.dumps({**SAMPLE, "result": reply, "modelUsage": usage})  # noqa: E731
    with tempfile.TemporaryDirectory() as store:
        projects = Path(store) / "projects"
        other = projects / "C--Users-someone-project"
        other.mkdir(parents=True)
        (other / f"{OTHER_ID}.jsonl").write_text("{}", encoding="utf-8")
        outcome, given = _two_turns(_model_server(app_settings), monkeypatch, [turn("Which?"), turn("Logged.")],
                                    config=Path(store))
        session = given[0][0][given[0][0].index("--session-id") + 1]
        assert outcome["passed"] is True and "session_id" not in outcome
        assert not (projects / cli_folder(given[0][1])).exists()  # the session, its folder, the project folder
        assert sorted(p.name for p in projects.iterdir()) == [other.name]
        assert [p.name for p in other.iterdir()] == [f"{OTHER_ID}.jsonl"]  # another id stays
        assert session != OTHER_ID


OTHER_ID = "11111111-1111-4111-8111-111111111111"
SESSION = "3f2b8c1e-5d4a-4f6b-9c7d-8e9f0a1b2c3d"


def test_forget_session_finds_the_id_in_any_project_folder_and_leaves_every_other_file():
    from evals import run
    with tempfile.TemporaryDirectory() as store:
        projects = Path(store) / "projects"
        mine, other = projects / "C--Temp-cwd-pick-lift-answer-1", projects / "C--Users-someone-project"
        for folder in (mine, other):
            folder.mkdir(parents=True)
        (mine / f"{SESSION}.jsonl").write_text("{}", encoding="utf-8")
        (mine / SESSION / "tool-results").mkdir(parents=True)
        (mine / SESSION / "tool-results" / "a.txt").write_text("x", encoding="utf-8")
        (mine / f"{OTHER_ID}.jsonl").write_text("{}", encoding="utf-8")  # another session of the same folder
        (other / f"{SESSION}.jsonl").write_text("{}", encoding="utf-8")  # the id under another folder goes too
        (other / f"{OTHER_ID}.jsonl").write_text("{}", encoding="utf-8")
        assert run.forget_session(SESSION, {"CLAUDE_CONFIG_DIR": store}) == []
        assert sorted(p.name for p in mine.iterdir()) == [f"{OTHER_ID}.jsonl"]  # kept, so the folder stays
        assert sorted(p.name for p in other.iterdir()) == [f"{OTHER_ID}.jsonl"]
        for wide in ("*", SESSION.upper(), "{" + SESSION + "}", f"{SESSION}/../x"):
            with pytest.raises(ValueError):
                run.forget_session(wide, {"CLAUDE_CONFIG_DIR": store})  # never a pattern wider than one id
        assert len(list(projects.glob("*/*.jsonl"))) == 2
        assert run.forget_session(SESSION, {"CLAUDE_CONFIG_DIR": str(Path(store) / "none")}) == []  # no store


def test_forget_session_never_follows_a_junction_out_of_the_store():
    """A project folder that is a junction to a folder outside projects: the session there is left, and named."""
    _winapi = pytest.importorskip("_winapi")
    if not hasattr(_winapi, "CreateJunction"):
        pytest.skip("no junctions here")
    from evals import run
    with tempfile.TemporaryDirectory() as store, tempfile.TemporaryDirectory() as outside:
        projects = Path(store) / "projects"
        projects.mkdir()
        (Path(outside) / f"{SESSION}.jsonl").write_text("{}", encoding="utf-8")
        (Path(outside) / SESSION).mkdir()
        _winapi.CreateJunction(outside, str(projects / "escape"))
        try:
            left = run.forget_session(SESSION, {"CLAUDE_CONFIG_DIR": store})
            assert sorted(p.name for p in Path(outside).iterdir()) == [SESSION, f"{SESSION}.jsonl"]  # untouched
            assert len(left) == 2 and all("a link out of the store: left" in x for x in left)
        finally:
            os.rmdir(projects / "escape")  # the junction alone, never its target


def test_a_session_the_run_cannot_delete_is_named_in_its_outcome(app_settings, monkeypatch):
    from evals import run
    usage = {"claude-opus-5-5": {"costUSD": 0.1}}
    turn = lambda reply: json.dumps({**SAMPLE, "result": reply, "modelUsage": usage})  # noqa: E731
    monkeypatch.setattr(run, "forget_session", lambda session, env: [f"projects/x/{session}.jsonl"])
    outcome, given = _two_turns(_model_server(app_settings), monkeypatch, [turn("Which?"), turn("Logged.")])
    session = given[0][0][given[0][0].index("--session-id") + 1]
    assert outcome["session_id"] == session and outcome["session_left"] == [f"projects/x/{session}.jsonl"]


def test_a_two_turn_run_marks_each_call_s_turn_and_fails_a_write_made_before_the_second(app_settings, monkeypatch):
    """The fix round's must fix: the assistant guessed in the first turn, so the second line answered nothing."""
    usage = {"claude-opus-5-5": {"costUSD": 0.1}}
    turn = lambda reply: json.dumps({**SAMPLE, "result": reply, "modelUsage": usage})  # noqa: E731
    refused = {**call("log_lifts", is_error=True), "answered": True, "error": "pick_lift"}
    written = {**call("log_lifts"), "answered": True}
    server = _model_server(app_settings)
    server.recorder = TurnRecorder()
    asked, _ = _two_turns(server, monkeypatch, [turn("Which squat?"), turn("Logged Back Squat.")],
                          [[refused], [written]])
    assert [c["turn"] for c in asked["calls"]] == [1, 2] and asked["passed"] is True
    server.recorder = TurnRecorder()
    guessed, _ = _two_turns(server, monkeypatch, [turn("Logged Back Squat."), turn("Logged Back Squat.")],
                            [[refused, written], []])
    assert [c["turn"] for c in guessed["calls"]] == [1, 1]
    assert guessed["reasons"] == ["a write succeeded before the second turn was sent: log_lifts"]


def test_judge_turns_wants_the_case_s_own_refusal_in_the_first_turn_and_never_again_in_the_second():
    two = {"id": "t", "say": "squat", "then": "the barbell one", "expect": {"error_code": {"log_lifts": "pick_lift"}}}
    asked = {**call("log_lifts", is_error=True), "answered": True, "error": "pick_lift"}
    write = {**call("log_lifts"), "turn": 2}
    first, again = ("no log_lifts call in the first turn was refused with pick_lift",
                    "a log_lifts call in the second turn was refused with pick_lift again")
    assert judge.judge_turns(two, [{**asked, "turn": 1}, write]) == []
    assert judge.judge_turns(two, [{**asked, "turn": 2}, write]) == [first, again]  # asked by Spotter only later
    assert judge.judge_turns(two, [{**asked, "turn": 1}, {**asked, "turn": 2}, write]) == [again]
    assert judge.judge_turns(two, [write]) == [first]  # never asked: the assistant asked alone
    assert judge.judge_turns(two, [{**call("set_goal", is_error=True), "error": "pick_lift", "turn": 1}, write]) == [
        first]  # another function's refusal is not the case's


def test_judge_turns_reads_each_call_s_turn_and_only_a_two_turn_case():
    two = {"id": "t", "say": "squat", "then": "the barbell one", "expect": {"reply_asks": True}}
    write, early = {**call("log_lifts"), "turn": 2}, {**call("log_lifts"), "turn": 1}
    assert judge.judge_turns(two, [{**call("log_lifts", is_error=True), "turn": 1}, write]) == []
    assert judge.judge_turns(two, [early, write]) == ["a write succeeded before the second turn was sent: log_lifts"]
    assert judge.judge_turns(two, [call("log_lifts")])  # a call with no turn is the first turn's
    assert judge.judge_turns(two, [{**call("get_log"), "turn": 1}, write]) == []  # a read is no write
    assert judge.judge_turns({**two, "then": None}, [early]) == [] and judge.judge_turns(
        {k: v for k, v in two.items() if k != "then"}, [early]) == []
    assert "a write succeeded before the second turn was sent: log_lifts" in judge.judge(two, [early], "?", {})


def test_a_second_turn_the_cli_cannot_resume_fails_the_run_and_says_which_turn(app_settings, monkeypatch):
    """A wrong resume flag: the CLI prints no result document for turn 2. The run fails; it never passes on turn 1."""
    server = _model_server(app_settings)
    outcome, given = _two_turns(server, monkeypatch, [json.dumps({**SAMPLE, "result": "Which?"}),
                                                            "error: unknown option '--resume'"])
    assert len(given) == 2 and outcome["passed"] is False
    assert outcome["reasons"] == ["turn 2: CLI: the CLI output is not a result document; stderr: none"]
    errored, _ = _two_turns(server, monkeypatch, [json.dumps(SAMPLE), json.dumps(
        {**SAMPLE, "subtype": "error_during_execution", "is_error": True})])
    assert "turn 2: CLI ended with error_during_execution" in errored["reasons"]


@pytest.mark.parametrize("client", ["codex", "gemini"])
def test_codex_and_gemini_fail_a_two_turn_case_unrun_with_its_reason(client, monkeypatch, capsys):
    from evals import codex_client, gemini_client, run
    monkeypatch.setattr(run.LocalServer, "start", lambda self: None)
    monkeypatch.setattr(run.LocalServer, "stop", lambda self: None)
    monkeypatch.setattr(run.judge, "load_cases", lambda: [
        {"id": "two", "say": "squat", "then": "the barbell one", "expect": {"reply_asks": True}}])
    seen = []
    done = {"passed": False, "reasons": ["x"], "calls": [], "seconds": 1.0, "reply": "", "models": []}
    monkeypatch.setattr(codex_client, "find_codex", lambda: "codex")
    monkeypatch.setattr(codex_client, "run_once_codex", lambda server, case, *a: seen.append(case["say"]) or done)
    monkeypatch.setattr(gemini_client, "load_key", lambda n: "key")
    monkeypatch.setattr(gemini_client, "GeminiAPI", lambda key, per_minute: type("A", (), {"redact": str})())
    monkeypatch.setattr(gemini_client, "GeminiHost", lambda api, model, effort: object())
    monkeypatch.setattr(gemini_client, "run_once_gemini", lambda server, case, zone, host: seen.append(case["say"]) or done)
    assert run.main(["--client", client, "--n", "1", "--time-zone", "Asia/Kolkata"]) == 1
    assert seen == []  # not run at all: a first turn alone is no run of the case
    out = capsys.readouterr().out
    assert "two-1: FAIL" in out and run.TWO_TURNS_UNSENT.format(client=client) in out.split("Failed runs:", 1)[1]


def test_the_summary_names_each_session_a_two_turn_run_could_not_delete(monkeypatch, capsys):
    from evals import run
    monkeypatch.setattr(run.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(run.LocalServer, "start", lambda self: None)
    monkeypatch.setattr(run.LocalServer, "stop", lambda self: None)
    monkeypatch.setattr(run.judge, "load_cases", lambda: [
        {"id": "two", "say": "squat", "then": "the barbell one", "expect": {"reply_asks": True}},
        {"id": "one", "say": "bench", "expect": {"reply_asks": True}}])
    runs = iter([{"session_id": "s-1"}, {}])

    def fake_run_once(server, case, zone, claude, model, env, workdir, label, keep, effort=None):
        return {"passed": True, "reasons": [], "calls": [], "seconds": 1.0, "reply": "?", "models": [], **next(runs)}

    monkeypatch.setattr(run, "run_once", fake_run_once)
    run.main(["--n", "1", "--time-zone", "Asia/Kolkata"])
    assert "sessions of two-turn runs the Claude CLI still keeps, a delete failed: s-1\n" in capsys.readouterr().out
