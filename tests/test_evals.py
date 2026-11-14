"""The eval runner's own parts: the cases file, the judging, the CLI output parser, the token mask and
the call recorder. Nothing here calls the model or starts the Claude CLI."""

import datetime as dt
import json
import re
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app import db, metrics, services
from app.models import EntryVersion, User
from app.main import create_app
from evals import judge
from evals.recorder import CallRecorder, parse_body, token_key
from evals.run import (
    LocalServer, child_env, cli_command, dates_for, local_time_zone, mcp_config, next_month_end, person_rows,
)
from tests.conftest import HEADERS, settings

CONTRACT_CASES = {"stated_number", "guessed_number", "saved_food", "meal_not_asked", "yesterday", "treadmill_once",
                  "repeat_sent", "delete_ambiguous", "delete_explicit", "unknown_exercise",
                  "goal_date", "how_am_i_doing", "on_track", "export_is_a_click", "delete_is_a_click", "drop_goal",
                  "decimals_and_dashes", "import_preview_first", "import_again", "import_unknown_lift", "undo_edit"}
EM_DASH, EN_DASH = "\u2014", "\u2013"
WRITES = {"log_lifts", "log_bodyweight", "log_measurement", "log_food", "save_food", "log_cardio", "set_goal",
          "update_settings", "edit_entry", "restore_entry", "delete_entry", "import_entries"}


def call(name, arguments=None, is_error=False):
    return {"name": name, "arguments": arguments or {}, "is_error": is_error}


# ---------------------------------------------------------------- the cases file

def test_cases_file_loads_with_every_contract_case():
    cases = judge.load_cases()
    assert len(cases) == 21  # was 20: edit history adds undo_edit
    assert CONTRACT_CASES == {c["id"] for c in cases}
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
        "switch must be one of read, log, log_delete"]
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
    explicit = cases["delete_explicit"]
    assert explicit["switch"] == "log_delete"
    expect = judge.resolve(explicit["expect"], {"seed.weigh_in": 5})
    assert judge.judge_calls(expect, [call("get_log"), call("delete_entry", {"kind": "bodyweight",
                                                                             "entry_id": 5})], "Deleted.") == []
    assert judge.judge_calls(expect, [call("delete_entry", {"kind": "bodyweight", "entry_id": 5}, is_error=True)], "")
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

    doing = cases["how_am_i_doing"]["expect"]
    assert judge.judge_calls(doing, [call("whoami"), call("get_trends"), call("get_goals")], "Down 0.3 kg a week.") == []
    assert judge.judge_calls(doing, [call("get_progress")], "Fine.")
    assert judge.judge_calls(doing, [call("get_trends"), call("log_bodyweight")], "Logged.")
    on_track = cases["on_track"]["expect"]
    assert judge.judge_calls(on_track, [call("get_goals")], "Two of three on track.") == []
    assert judge.judge_calls(on_track, [call("get_trends")], "Looks good.")

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


def _decimals_case() -> dict:
    return {c["id"]: c for c in judge.load_cases()}["decimals_and_dashes"]


def test_the_decimals_case_is_well_formed():
    case = _decimals_case()
    assert case["say"] == "How am I doing?"
    assert judge.case_problems(case) == []
    expect = case["expect"]
    assert expect["function"] == "get_trends"
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


def test_the_decimals_case_needs_get_trends_and_no_write():
    expect = _decimals_case()["expect"]
    assert judge.judge_calls(expect, [call("get_trends"), call("get_goals")], "Body weight 79.3 kg.") == []
    assert judge.judge_calls(expect, [call("get_progress")], "Body weight 79.3 kg.") == [
        "get_trends was not called (calls: get_progress)"]
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
                             "Written. Anything else?") == ["import_entries sent dry_run = false"]
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
                    "cost_usd": 0.0, "usage": {}, "tokens": 0, "models": [], "permission_denials": []}


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


def test_child_env_drops_the_session_markers_only():
    env, dropped = child_env({"CLAUDECODE": "1", "CLAUDE_CODE_SSE_PORT": "1", "CLAUDE_PID": "2", "AI_AGENT": "x",
                              "CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin"})
    assert env == {"CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin"}
    assert dropped == ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_SSE_PORT", "CLAUDE_PID"]


# ---------------------------------------------------------------- the recorder, on the real server

def test_parse_body_reads_json_and_event_streams():
    assert parse_body("application/json", b'{"id": 1}') == {"id": 1}
    assert parse_body("text/event-stream", b'event: message\ndata: {"id": 2}\n\n') == {"id": 2}
    assert parse_body("application/json", b"") is None


def test_recorder_keeps_calls_per_token(provider, make_user, token_for):
    make_user(subject="user_rec_a", email="a@example.com", name="A")
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
    assert calls[2]["is_error"] is True and calls[2]["error"] == "not_allowed"  # the switch defaults to log
    assert recorder.calls(b) == [] and recorder.methods(b) == ["tools/list"]
    assert recorder.calls("never-used") == []
    assert a not in json.dumps(recorder._calls) and token_key(a) in recorder._calls


def test_a_seed_service_is_found_by_its_name():
    """four_weeks is the evals' own seed, outside app/; every other seed is Spotter's own service. A name that is
    neither, add_sample_data among them, is no seed."""
    from evals.four_weeks import four_weeks
    assert judge.NO_DATA_SERVICES == {"four_weeks"} and judge.seed_service("four_weeks") is four_weeks
    assert judge.seed_service("save_food") is services.save_food
    for name in ("add_sample_data", "remove_sample_data", "drop_table"):
        with pytest.raises(KeyError):
            judge.seed_service(name)
        assert not hasattr(services, name)
