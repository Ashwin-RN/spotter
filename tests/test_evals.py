"""The eval runner's own parts: the cases file, the judging, the CLI output parser, the token mask and
the call recorder. Nothing here calls the model or starts the Claude CLI."""

import json
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import db, services
from app.models import User
from app.main import create_app
from evals import judge
from evals.recorder import CallRecorder, parse_body, token_key
from evals.run import LocalServer, child_env, cli_command, dates_for, local_time_zone, mcp_config, person_rows
from tests.conftest import HEADERS, settings

CONTRACT_CASES = {"stated_number", "guessed_number", "saved_food", "meal_not_asked", "yesterday", "treadmill_once",
                  "repeat_sent", "delete_ambiguous", "delete_explicit", "unknown_exercise"}


def call(name, arguments=None, is_error=False):
    return {"name": name, "arguments": arguments or {}, "is_error": is_error}


# ---------------------------------------------------------------- the cases file

def test_cases_file_loads_with_every_contract_case():
    cases = judge.load_cases()
    assert CONTRACT_CASES <= {c["id"] for c in cases}
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
