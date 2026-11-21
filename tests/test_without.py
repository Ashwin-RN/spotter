"""The same model with Spotter and without it (contract docs/contracts/without-spotter.md): evals/without.py and
evals/without_cases.json.

The day is fixed by replacing the clock (db.utcnow), at noon UTC on Saturday 21 November 2026; the person's zone is
Asia/Kolkata, where noon UTC is the same date. Nothing here starts a CLI, calls Google or opens a port: the N arms'
processes go through the popen seam, the Gemini call through post, a W run's runner is replaced, and the local
server is never started.
"""

import datetime as dt
import json
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from app import db
from evals import codex_client, gemini_client, judge, run, without
from evals.run import LocalServer, person_rows
from evals.sixteen_weeks import sixteen_weeks
from evals.four_weeks import four_weeks

ZONE = "Asia/Kolkata"
TODAY = dt.date(2026, 11, 21)  # a Saturday
SOURCE = {c["id"]: c for c in judge.load_cases()}
CASES, MEMORIES = without.load_cases()
BY_ID = {c["id"]: c for c in CASES}
KEY = "fake-gemini-key-for-the-test-0123"


@pytest.fixture
def clock(monkeypatch):
    now = dt.datetime(TODAY.year, TODAY.month, TODAY.day, 12, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(db, "utcnow", lambda: now)


def seeded(make_user, seed=sixteen_weeks) -> dict:
    user = make_user(time_zone=ZONE)
    assert "error" not in seed(user.id)
    return person_rows(user.id)


def day(n: int) -> str:
    return (TODAY - dt.timedelta(days=n)).isoformat()


def line_of(lines: list[str], date: str) -> str:
    [found] = [x for x in lines if x.startswith(date + " ")]
    return found


# ---------------------------------------------------------------- the cases file

def test_the_file_loads_with_the_fourteen_cases_and_their_arms():
    assert [c["id"] for c in CASES] == ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "U1", "U2", "U3", "C1",
                                        "C2"]
    count = {arm: sum(arm in c["arms"] for c in CASES) for arm in without.ARMS}
    assert count == {"W": 14, "N-history": 12, "N-memory": 9, "N-new": 5}
    assert [c["id"] for c in CASES if "N-new" in c["arms"]] == ["R1", "R6", "R9", "C1", "C2"]
    assert not {"P1", "P2", "P3"} & set(BY_ID)  # the problem writes stay in cases.json, run by evals.run
    for case in CASES:
        built = without.w_case(case)
        assert set(built) <= judge.CASE_KEYS and judge.case_problems(built) == [], case["id"]
    assert BY_ID["R1"]["say"] == "what have i averaged a day in calories over the last 4 weeks?"
    assert BY_ID["R9"]["say"] == "how has my sleep been lately?"


def test_a_broken_case_is_named_when_the_file_loads(tmp_path):
    good = json.loads(without.CASES_FILE.read_text(encoding="utf-8"))
    bad = json.loads(json.dumps(good))
    bad["cases"][0]["reply"]["reply_has_pattern"] = ["(unclosed"]
    bad["cases"][1]["arms"] = ["W", "N-later"]
    bad["cases"][2]["band"] = {"unit": "kg", "low": 100, "high": 90, "allowed": [95]}
    bad["cases"][3]["memory"] = "nobody"
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError) as e:
        without.load_cases(path)
    said = str(e.value)
    assert "R1: reply: expect.reply_has_pattern[0]" in said and "R2: arms must be" in said
    assert "R3: band.low and band.high" in said and "R4: memory 'nobody' is not in memories" in said


def test_the_copied_rules_are_the_source_cases_own():
    """R5's and R9's patterns, U1's figure and U3's claim are copied by id, never retyped; so is every W rule."""
    def expect(case_id):
        return SOURCE[case_id]["expect"]
    assert BY_ID["R5"]["reply"] == {k: expect("cardio_month_comparison")[k]
                                    for k in ("reply_has_pattern", "reply_lacks_pattern")}
    assert BY_ID["R9"]["reply"]["reply_lacks_pattern"] == expect("data_empty")["reply_lacks_pattern"]
    assert BY_ID["U1"]["reply"]["reply_has_pattern"] == expect("recent_biryani_again")["reply_has_pattern"]
    assert BY_ID["U3"]["reply"]["reply_lacks_pattern"][0] == expect("number_without_kind")["reply_lacks_pattern"][0]
    for case_id, source in (("U1", "recent_biryani_again"), ("U2", "same_again_no_context"),
                            ("U3", "number_without_kind"), ("C1", "look_deload"), ("C2", "look_10k_friend")):
        case = BY_ID[case_id]
        assert case["source"] == source and case["say"] == SOURCE[source]["say"]
        assert case["w"]["expect"] == expect(source) and case["w"]["calls"] == SOURCE[source]["calls"]
        assert case["seed"] == SOURCE[source]["seed"] and case["w"].get("db") == SOURCE[source].get("db")
    assert BY_ID["R4"]["w"]["expect"]["inputs_include"] == {"area": "weigh_ins"}
    assert BY_ID["R6"]["w"]["expect"]["quotes_one_of"] == expect("gold_squat_goal")["quotes_one_of"]
    assert BY_ID["R9"]["w"]["db"] == SOURCE["data_empty"]["db"]
    # A named exception: C2's reply pattern is its own, as look_10k_friend has none, and it takes "8-week" too.
    assert "reply_has_pattern" not in expect("look_10k_friend") and BY_ID["C2"]["reply"]["reply_has_pattern"]


# ---------------------------------------------------------------- what an N arm is told

def test_the_history_holds_each_truth_and_names_every_activity(make_user, clock):
    rows = seeded(make_user)
    lines = without.history_lines(rows, without.activity_names())
    text = "\n".join(lines)
    assert len(lines) == 364 and lines[0] == f"{day(363)} Sun: weighed 86.8 kg."
    # The three days over 90 kg, and no other.
    heavy = [x for x in lines if float(re.search(r"weighed ([\d.]+) kg", x)[1]) > 90]
    assert [x.split(" ")[0] for x in heavy] == [day(350), day(340), day(330)]
    assert ["weighed 90.6 kg" in heavy[0], "weighed 92.4 kg" in heavy[1], "weighed 91.2 kg" in heavy[2]] == [True] * 3
    # The first bench: 40 kg on Monday 3 August 2026; the best: 97.5 kg.
    first = next(x for x in lines if "bench press" in x)
    assert first.startswith("2026-08-03 Mon:") and "bench press 3 sets of 5 at 40 kg" in first
    assert "bench press 3 sets of 5 at 97.5 kg" in text and "at 100 kg, bench" in text
    # Yesterday's food: 1,980 kcal, each item's figure as Spotter holds it, the estimate as an estimate.
    yesterday = line_of(lines, day(1))
    assert sum(int(x) for x in re.findall(r"\((?:you estimated )?(\d+)(?: kcal)?\)", yesterday)) == 1980
    assert "mixed vegetable sabzi (you estimated 150)" in yesterday and "chicken biryani (640 kcal)" in yesterday
    # Both goals, on the day each was set.
    start = line_of(lines, day(112))
    assert "set a goal: body weight 78 kg by 2027-01-16, I'm 82 now" in start
    assert "back squat goal 150 kg by 2027-02-13, my back squat is 80 now" in start
    # Every activity by its name, with the person's own numbers only: no calorie estimate of Spotter's.
    assert f"{day(23)} Thu: " in text and "stationary bike 30 minutes." in line_of(lines, day(23))
    assert "outdoor run 30 minutes, 5 km." in line_of(lines, day(25))
    assert not any(w in text for w in ("None", "exercise_id", "estimated_kcal", "speed"))
    assert not any("run" in line_of(lines, day(n)) or "bike" in line_of(lines, day(n)) for n in range(21))
    prompt = without.prompt_for("N-history", BY_ID["R1"], rows, without.activity_names(), TODAY, MEMORIES)
    assert len(prompt) > 33_000  # over the Windows command line: it goes by stdin


def test_a_deleted_row_is_never_told(make_user, clock):
    rows = seeded(make_user)
    kept = [r for r in rows["bodyweight"] if r["date"] == day(330)]
    kept[0]["deleted_at"] = "2026-11-20T10:00:00"
    lines = without.history_lines(rows, without.activity_names())
    assert len(lines) == 363 and not any(x.startswith(day(330)) for x in lines)  # that day held the weigh-in alone
    assert "weighed 91.2 kg" not in "\n".join(lines)


def test_the_four_weeks_history_names_every_activity_and_each_goal(make_user, clock):
    rows = seeded(make_user, four_weeks)
    lines = without.history_lines(rows, without.activity_names())
    text = "\n".join(lines)
    assert len(lines) == 28
    for said in ("treadmill walk 30 minutes", "stationary bike 25 minutes, the machine said",
                 "outdoor run 31 minutes, 5 km", "resting hr 64 bpm", "waist 88 cm", "set a goal: body weight 77 kg by", "back squat goal 120 kg by",
                 "outdoor run goal: 5 km in 27 minutes by", "banana and peanuts ("):
        assert said in text, said


def test_the_memory_text_holds_facts_and_habits_and_no_daily_figure(make_user, clock):
    rows = seeded(make_user)
    text = without.memory_text(MEMORIES["sixteen_weeks"], rows)
    assert "body weight 78 kg by 16 January; back squat 150 kg by 13 February" in text and "{" not in text
    assert len(text.splitlines()) == 6
    figures = {format(v, "f") for v, _ in judge._text_figures(text)}
    assert figures == {"78", "150", "81", "5"}, figures  # targets, a rough weight and the run's distance
    with pytest.raises(ValueError):
        without.memory_text("by {lift_by} and {sleep_by}", rows)


def test_each_n_prompt_starts_with_today_and_ends_with_the_message(make_user, clock):
    rows = seeded(make_user)
    names = without.activity_names()
    case = BY_ID["R6"]
    history = without.prompt_for("N-history", case, rows, names, TODAY, MEMORIES)
    assert history.startswith("Today is Saturday, 21 November 2026.\n\n" + without.HISTORY_HEAD + "\n\n2025-11-23 Sun:")
    assert history.endswith("\n\nam i on track for my squat goal?")
    memory = without.prompt_for("N-memory", case, rows, names, TODAY, MEMORIES)
    assert memory == ("Today is Saturday, 21 November 2026.\n\n" + without.MEMORY_HEAD + "\n"
                      + without.memory_text(MEMORIES["sixteen_weeks"], rows) + "\n\nam i on track for my squat goal?")
    assert without.prompt_for("N-new", case, rows, names, TODAY, MEMORIES) == (
        "Today is Saturday, 21 November 2026.\n\nam i on track for my squat goal?")


# ---------------------------------------------------------------- the commands and the request

def test_the_claude_command_has_no_server_no_tool_and_no_prompt():
    command = without.claude_command("claude", "claude-sonnet-5-5", "medium")
    assert command[:2] == ["claude", "-p"]
    assert json.loads(command[command.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert "--strict-mcp-config" in command and command[command.index("--tools") + 1] == ""
    assert command[command.index("--settings") + 1] == run.NO_ADVISOR_SETTINGS
    assert "--no-session-persistence" in command and "--allowedTools" not in command
    assert command[-4:] == ["--effort", "medium", "--model", "claude-sonnet-5-5"]
    assert not any("127.0.0.1" in p or "mcp__" in p or "spotter" in p for p in command)


def test_the_claude_command_tells_the_model_it_is_a_plain_chat():
    # With Claude Code's own system prompt and no tools, a run with no data reached for a file tool and hung.
    command = without.claude_command("claude", "claude-sonnet-5-5", "medium")
    assert command.count("--append-system-prompt") == 1
    assert command[command.index("--append-system-prompt") + 1] == without.PLAIN_CHAT
    assert without.PLAIN_CHAT == ("This is a chat. You have no tools, no files and no working directory: "
                                  "answer from the conversation alone.")
    for words in ("no tools", "no files", "no working directory"):
        assert words in without.PLAIN_CHAT
    assert without.claude_command("claude", None, None)[-2:] == ["--append-system-prompt", without.PLAIN_CHAT]
    assert run.CHILD_SETS == {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}  # the env the runs get: run.child_env


def test_the_codex_command_is_codex_client_s_without_the_server_and_reads_stdin():
    cwd = Path("cwd")
    command = without.codex_command("codex.exe", cwd, "gpt-6-luna", "medium")
    full = codex_client.codex_command("codex.exe", "-", 0, cwd, "gpt-6-luna", "medium")
    assert command[-1] == "-" and len(full) - len(command) == 6
    assert not any("mcp_servers" in p for p in command)
    assert [p for p in full if "mcp_servers" not in p and p != "-c"] == [p for p in command if p != "-c"]
    assert 'web_search="disabled"' in command and command[command.index("-s") + 1] == "read-only"


def test_the_gemini_request_carries_no_tools_and_no_system_text():
    body = without.gemini_body("Today is x.\n\nhi", "high")
    assert body == {"contents": [{"role": "user", "parts": [{"text": "Today is x.\n\nhi"}]}],
                    "generationConfig": {"thinkingConfig": {"thinkingLevel": "HIGH"}}}
    assert without.gemini_body("hi", "") == {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


# ---------------------------------------------------------------- the reply rules, on written replies

RIGHT = {
    "R1": "Over the last 28 days you averaged 1,905 kcal a day.",
    "R2": "Yesterday you ate 1,980 kcal.",
    "R3": "Your bench went from 40 kg to 97.5 kg x 5, up 57.5 kg.",
    "R4": "Yes, three days: 90.6 kg on 2025-12-06, 92.4 kg on 2025-12-16 and 91.2 kg on 2025-12-26.",
    "R5": "You logged 2 cardio sessions in the last 28 days, down from 8 in the 28 days before.",
    "R6": "Not yet: you are behind. 116.7 kg now against 120 kg on the plan line today: 3.3 kg behind.",
    "R7": "Food is up to 1,980 kcal a day, and there has been no cardio in the last 3 weeks.",
    "R8": "Your bench is not stuck: it is still going up, 97.5 kg x 5 last session.",
    "R9": "There is no sleep logged, so I cannot say how it has been.",
    "U1": "Logged chicken biryani at dinner, 640 kcal, as before.",
    "U2": "Same as what? Tell me what to repeat.",
    "U3": "85 of what: body weight, or something else?",
    "C1": "A deload week is a planned lighter week: you cut the volume or the load so the body can recover.",
    "C2": "Week 1: walk and run 20 minutes. Week 8: run 10 km.",
}
WRONG = {
    "R1": "You averaged about 1,900 kcal a day.",
    "R2": "Yesterday you ate about 2,000 calories.",
    "R3": "Your bench went from 40 kg to 95 kg.",
    "R4": "Yes, twice: 91.2 kg and 92.4 kg.",
    "R5": "8 sessions in the last 28 days and 2 before that.",
    "R6": "Yes, you're on track at 100 kg.",
    "R7": "Get back to basics: sleep well, eat well and train.",
    "R8": "Bench plateaus are common: try a close-grip variation.",
    "R9": "You slept about 7 hours a night.",
    "U1": "Logged chicken biryani at dinner, an estimate of 640 kcal.",
    "U2": "I've logged another set of bench press, 80 kg x 5.",
    "U3": "I've logged 85 kg as today's weigh-in. Anything else?",
    "C1": "It is a week off sugar.",
    "C2": "Here is a 6-week plan. Week 6: race day.",
}
NEW_RIGHT = {"R1": "I don't have access to your food logs, so I can't tell you your average.",
             "R6": "I can't see your squat numbers or your goal. What are they?"}
NEW_WRONG = {"R1": "You've averaged about 2,100 kcal a day.", "R6": "Your squat is 100 kg, so you are 3.3 kg behind."}


@pytest.mark.parametrize("case_id", list(RIGHT))
def test_each_case_s_reply_rule_on_a_right_reply_and_a_wrong_one(case_id):
    case = BY_ID[case_id]
    for arm in case["arms"]:
        if arm == "N-new" and case.get("new_reply"):
            continue
        assert without.reply_reasons(case, arm, RIGHT[case_id]) == [], (arm, RIGHT[case_id])
        assert without.reply_reasons(case, arm, WRONG[case_id]), (arm, WRONG[case_id])


@pytest.mark.parametrize("case_id", list(NEW_RIGHT))
def test_n_new_is_judged_on_no_record_and_no_figure_of_its_kind(case_id):
    case = BY_ID[case_id]
    assert without.reply_reasons(case, "N-new", NEW_RIGHT[case_id]) == []
    assert without.reply_reasons(case, "N-new", NEW_WRONG[case_id])
    assert without.reply_reasons(case, "N-new", RIGHT[case_id])  # the right figure without a record is no answer
    assert without.reply_reasons(case, "N-history", NEW_RIGHT[case_id])  # with the history it must answer


def test_a_made_up_figure_beside_the_right_one_fails_the_band():
    r1, r4 = BY_ID["R1"], BY_ID["R4"]
    assert without.reply_reasons(r1, "N-history", "You averaged 1,905 kcal a day; weekdays were 2,140 kcal.") == [
        "the reply says '2,140 kcal': 2140 is inside 1700 to 2300 and is no figure of the record"]
    assert without.reply_reasons(r1, "N-history", "You averaged 1905 calories, about 1900cal most days.")
    assert without.reply_reasons(r4, "N-memory", RIGHT["R4"] + " And 94.5 kg once.")
    # The message's own 90, a date, a figure under the band and a figure far from the unit are not read as one.
    assert without.reply_reasons(r4, "N-history", "Above 90 kg: " + RIGHT["R4"][5:] + " Lowest: 80 kg.") == []
    assert without.reply_reasons(r1, "N-history", "1,905 kcal a day on average, 1,680 kcal before 2026-10-31.") == []
    # In W, a band figure an answered read of the run gave is never a failure; in N it is.
    read = {"name": "get_trends", "is_error": False, "answer": {"food": {"kcal_per_day_7d": 2061}}}
    said = "You averaged 1,905 kcal a day, and 2,061 kcal this week."
    assert without.reply_reasons(r1, "W", said, [read]) == []
    assert without.reply_reasons(r1, "N-history", said)
    assert without.reply_reasons(r1, "W", said, [{**read, "is_error": True}])


def test_spotter_s_own_figure_is_counted_apart():
    assert without.quotes_spotter(BY_ID["R6"], RIGHT["R6"]) is True
    assert without.quotes_spotter(BY_ID["R6"], "You are behind: your top set is 100 kg.") is False
    assert without.quotes_spotter(BY_ID["R2"], RIGHT["R2"]) is False  # a case with no spotter list


# ---------------------------------------------------------------- one run, through the seams

def _result(reply: str, models=("claude-sonnet-5-5",), subtype="success") -> str:
    return json.dumps({"type": "result", "subtype": subtype, "is_error": subtype != "success", "result": reply,
                       "num_turns": 1, "total_cost_usd": 0.0125,
                       "usage": {"input_tokens": 3, "cache_creation_input_tokens": 9000, "cache_read_input_tokens": 0,
                                 "output_tokens": 40},
                       "modelUsage": {m: {"costUSD": 0.01} for m in models}})


class FakeProcess:
    """Stands in for a CLI: keeps what it was given by stdin, prints a kept answer, or never finishes."""

    def __init__(self, stdout: str, hang: bool = False) -> None:
        self.stdout_text, self.hang, self.pid, self.returncode, self.killed = stdout, hang, 4242, 0, False
        self.given = None

    def communicate(self, input=None, timeout=None):
        if input is not None:
            self.given = input
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("cli", timeout)
        return self.stdout_text, ""


class Popen:
    def __init__(self, *outputs: str) -> None:
        self.outputs, self.made = list(outputs), []

    def __call__(self, command, **kwargs):
        process = FakeProcess(self.outputs.pop(0))
        self.made.append((command, kwargs, process))
        return process


def _runner(app_settings, client="claude", popen=None, api=None, keep=False) -> without.Runner:
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: its people are used
    server.settings = app_settings
    exe = {"claude": "claude", "codex": "codex.exe", "gemini": None}[client]
    model = without.DEFAULT_MODELS[client]
    env, _ = run.child_env({"PATH": "/bin", "CLAUDECODE": "1"})
    return without.Runner(client, exe, model, run.DEFAULT_EFFORTS.get(model), env, server, ZONE,
                          Path(tempfile.mkdtemp(prefix="test-without-")), keep, MEMORIES, popen=popen, api=api)


def test_an_n_run_sends_the_prompt_by_stdin_and_is_judged_on_the_reply(app_settings, clock):
    popen = Popen(_result(RIGHT["R4"]))
    outcome = _runner(app_settings, popen=popen).one(BY_ID["R4"], "N-history", 1)
    [(command, kwargs, process)] = popen.made
    assert command == without.claude_command("claude", "claude-sonnet-5-5", "medium")
    assert kwargs["stdin"] == subprocess.PIPE and "CLAUDECODE" not in kwargs["env"]
    assert kwargs["env"]["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1" and Path(kwargs["cwd"]).name == "cwd-R4-N-history-1"
    assert process.given.startswith("Today is Saturday, 21 November 2026.") and "weighed 92.4 kg" in process.given
    assert process.given.endswith(BY_ID["R4"]["say"]) and not any(BY_ID["R4"]["say"] in p for p in command)
    assert outcome["prompt_chars"] == len(process.given) > 33_000
    assert (outcome["status"], outcome["passed"], outcome["reply_passed"], outcome["record_passed"]) == (
        "ok", True, True, None)
    assert outcome["cost_usd"] == 0.0125 and outcome["input_tokens"] == 9003 and outcome["models"] == [
        "claude-sonnet-5-5"]


def test_an_n_run_that_did_not_run_is_a_fault_not_a_wrong_answer(app_settings, clock, monkeypatch):
    runner = _runner(app_settings, popen=Popen("not json", _result("x", subtype="error_max_turns"),
                                               _result(RIGHT["C1"], models=("claude-sonnet-5-5", "claude-fable-5-2"))))
    faults = [runner.one(BY_ID["C1"], "N-new", i) for i in (1, 2, 3)]
    assert [o["status"] for o in faults] == ["fault"] * 3 and not any(o["passed"] for o in faults)
    assert faults[0]["fault"].startswith("CLI: the CLI output is not a result document")
    assert faults[1]["fault"] == "CLI ended with error_max_turns"
    assert faults[2]["fault"] == "another model took part: claude-fable-5-2"
    stopped = []
    monkeypatch.setattr(without, "stop_tree", lambda p: (stopped.append(p.pid), setattr(p, "killed", True)))
    hung = FakeProcess("", hang=True)
    runner.popen = lambda command, **kwargs: hung
    outcome = runner.one(BY_ID["C1"], "N-new", 4)
    assert stopped == [4242] and outcome["fault"] == f"timed out after {run.RUN_TIMEOUT} s"


def test_a_codex_n_run_has_no_server_and_no_token_and_a_tool_used_is_a_fault(app_settings, clock):
    events = [{"type": "item.completed", "item": {"type": "agent_message", "text": RIGHT["C2"]}},
              {"type": "turn.completed", "usage": {"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 9}}]
    tool = [{"type": "item.completed", "item": {"type": "command_execution", "command": "dir"}}] + events
    popen = Popen("\n".join(map(json.dumps, events)), "\n".join(map(json.dumps, tool)))
    runner = _runner(app_settings, "codex", popen=popen)
    good, used = runner.one(BY_ID["C2"], "N-new", 1), runner.one(BY_ID["C2"], "N-new", 2)
    command, kwargs, process = popen.made[0]
    assert command[-1] == "-" and process.given.endswith(BY_ID["C2"]["say"])
    assert codex_client.TOKEN_VARIABLE not in kwargs["env"] and not any("mcp_servers" in p for p in command)
    assert good["passed"] is True
    assert (good["input_tokens"], good["cached_tokens"], good["output_tokens"]) == (100, 20, 9)
    assert used["status"] == "fault" and used["fault"] == "codex used a tool with no tools given: command_execution"
    assert good["codex_listings"] == []


def _listing(tool, server="codex"):
    return {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": server, "tool": tool,
                                               "arguments": {}, "status": "completed", "result": {"content": []}}}


def test_a_codex_n_run_with_only_codex_s_own_resource_listings_is_judged(app_settings, clock):
    """Codex's built-in listings on server "codex" find nothing with no server given: the run is judged on its
    reply, and the listings are kept on the outcome. Another tool, or another server, is still a tool used."""
    events = [{"type": "item.completed", "item": {"type": "agent_message", "text": RIGHT["C2"]}},
              {"type": "turn.completed", "usage": {"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 9}}]
    both = [_listing("list_mcp_resource_templates"), _listing("list_mcp_resources")]
    runs = [both + events, both + [_listing("read_mcp_resource")] + events,
            [_listing("list_mcp_resources", server="spotter")] + events]
    runner = _runner(app_settings, "codex", popen=Popen(*("\n".join(map(json.dumps, r)) for r in runs)))
    listed, other_tool, other_server = (runner.one(BY_ID["C2"], "N-new", i) for i in (1, 2, 3))
    assert listed["status"] == "ok" and listed["passed"] is True
    assert listed["codex_listings"] == ["list_mcp_resource_templates", "list_mcp_resources"]
    assert other_tool["status"] == "fault"
    assert other_tool["fault"] == "codex used a tool with no tools given: codex.read_mcp_resource"
    assert other_server["status"] == "fault"
    assert other_server["fault"] == "codex used a tool with no tools given: spotter.list_mcp_resources"


def test_c2_takes_an_8_week_plan_in_any_spelling_and_still_fails_a_6_week_one():
    for reply in ("Sam's 8-week first 10K plan", "An 8 week plan", "An eight-week plan", "Week 8: run 10 km."):
        assert without.reply_reasons(BY_ID["C2"], "N-new", reply) == [], reply
    for reply in ("Sam's 6-week first 10K plan", "A six week plan. Week 6: race day.", "An 18-week plan"):
        assert without.reply_reasons(BY_ID["C2"], "N-new", reply), reply


def test_a_w_run_gets_the_case_s_own_rules_and_reads_in_two_columns(app_settings, monkeypatch):
    got = []

    def fake_run_once(server, case, zone, claude, model, env, workdir, label, keep, effort=None):
        got.append(case)
        return {**run.blank_outcome("claude", model, effort), "reply": replies.pop(0), "reasons": reasons.pop(0),
                "calls": [], "models": [model]}

    monkeypatch.setattr(run, "run_once", fake_run_once)
    replies = [RIGHT["R2"], RIGHT["R2"], WRONG["R2"], ""]
    reasons = [[], ["no data read was answered without error (calls: none)"], [], ["CLI: the CLI printed nothing"]]
    runner = _runner(app_settings)
    right, unread, wrong, fault = (runner.one(BY_ID["R2"], "W", i) for i in (1, 2, 3, 4))
    assert got[0] == without.w_case(BY_ID["R2"]) and set(got[0]) == {"id", "say", "seed", "expect", "calls"}
    assert (right["reply_passed"], right["record_passed"], right["passed"]) == (True, True, True)
    assert (unread["reply_passed"], unread["record_passed"], unread["passed"]) == (True, False, False)
    assert (wrong["reply_passed"], wrong["passed"]) == (False, False) and wrong["record_reasons"] == []
    assert fault["status"] == "fault" and fault["fault"] == "CLI: the CLI printed nothing"


def test_a_codex_w_run_goes_through_the_same_popen_seam(app_settings, monkeypatch):
    seen = []
    monkeypatch.setattr(codex_client, "run_once_codex", lambda *a, popen=None, **k: seen.append(popen) or {
        **run.blank_outcome("codex", "gpt-6-luna", "medium"), "reply": RIGHT["C1"], "reasons": [], "calls": []})
    popen = Popen()
    assert _runner(app_settings, "codex", popen=popen).one(BY_ID["C1"], "W", 1)["passed"] is True
    assert seen == [popen]


# ---------------------------------------------------------------- main

def _quiet_server(monkeypatch, app_settings):
    monkeypatch.setattr(run.LocalServer, "start", lambda self: setattr(self, "settings", app_settings))
    monkeypatch.setattr(run.LocalServer, "stop", lambda self: None)


def _kept(monkeypatch) -> list[str]:
    kept, real = [], without.tempfile.mkdtemp
    monkeypatch.setattr(without.tempfile, "mkdtemp", lambda prefix: kept.append(real(prefix=prefix)) or kept[-1])
    return kept


def test_main_runs_run_one_of_every_cell_first_and_prints_both_columns(app_settings, clock, monkeypatch, capsys):
    _quiet_server(monkeypatch, app_settings)
    monkeypatch.setattr(without.shutil, "which", lambda name: "claude")
    w_replies = iter([RIGHT["C1"], WRONG["C1"]])
    monkeypatch.setattr(run, "run_once", lambda server, case, *a, **k: {
        **run.blank_outcome("claude", "claude-sonnet-5-5", "medium"), "reply": next(w_replies), "reasons": [],
        "calls": [], "models": ["claude-sonnet-5-5"], "cost_usd": 0.2})
    popen = Popen(_result(RIGHT["C1"]), "garbage")
    kept = _kept(monkeypatch)
    assert without.main(["--case", "C1", "--n", "2", "--time-zone", ZONE, "--keep"], popen=popen) == 0
    out = capsys.readouterr().out
    order = re.findall(r"^  (C1 \S+ \d): (.+?) in", out, re.MULTILINE)
    assert order == [("C1 W 1", "right"), ("C1 N-new 1", "right"), ("C1 W 2", "wrong"), ("C1 N-new 2", "not run")]
    assert re.search(r"^C1\s+W\s+1/2\s+1/2\s+", out, re.MULTILINE)
    assert re.search(r"^C1\s+N-new\s+1/1\s+-\s+0/1\s+1\s", out, re.MULTILINE)
    assert "W on the controls: reply right 1 of 2, with the record 1 of 2" in out
    assert "N-new on the controls: reply right 1 of 1; not run: 1" in out
    assert "cost of W: $0.4000" in out and "read by hand: every N-new run" in out
    results = json.loads((Path(kept[0]) / "results.json").read_text(encoding="utf-8"))
    assert [(r["case"], r["arm"], r["run"], r["status"], r["passed"]) for r in results] == [
        ("C1", "W", 1, "ok", True), ("C1", "N-new", 1, "ok", True), ("C1", "W", 2, "ok", False),
        ("C1", "N-new", 2, "fault", False)]
    assert {"reply", "reasons", "calls", "tokens", "cost_usd", "model"} <= set(results[0])


def test_main_with_gemini_sends_no_tools_and_never_shows_the_key(app_settings, clock, monkeypatch, capsys):
    _quiet_server(monkeypatch, app_settings)
    monkeypatch.setattr(gemini_client, "load_key", lambda n: KEY)
    sent = []

    def post(url, headers, body, timeout):
        sent.append((url, headers, json.loads(json.dumps(body))))
        if len(sent) == 2:  # an error that names the key, raised past the API's own mask
            raise RuntimeError(f"the connection broke while sending {headers['x-goog-api-key']}")
        text = RIGHT["C1"] + f" (a copy of {KEY})"
        answer = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
                  "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 40, "thoughtsTokenCount": 5},
                  "modelVersion": "gemini-4-hydrogen"}
        return 200, json.dumps(answer).encode("utf-8"), {}

    kept = _kept(monkeypatch)
    assert without.main(["--client", "gemini", "--case", "C1", "--case", "C2", "--arm", "N-new", "--n", "1",
                         "--gemini-per-minute", "0", "--time-zone", ZONE, "--keep"], post=post) == 0
    out = capsys.readouterr().out
    (url, headers, body), _ = sent
    assert "tools" not in body and "systemInstruction" not in body and KEY not in url
    assert headers["x-goog-api-key"] == KEY and body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "HIGH"
    assert body["contents"][0]["parts"][0]["text"].endswith(BY_ID["C1"]["say"])
    results = (Path(kept[0]) / "results.json").read_text(encoding="utf-8")
    assert KEY not in out and KEY not in results and gemini_client.KEY_HIDDEN in results
    assert json.loads(results)[0]["passed"] is True and "C1 N-new 1: right" in out and "C2 N-new 1: not run" in out
    assert "the run raised RuntimeError: the connection broke while sending [key hidden]" in out


def test_main_names_an_unknown_case_and_a_slice_with_no_cell(capsys):
    assert without.main(["--case", "P1"]) == 2 and "unknown case: P1" in capsys.readouterr().out
    assert without.main(["--case", "C1", "--arm", "N-memory"]) == 2 and "no case to run" in capsys.readouterr().out


# ---------------------------------------------------------------- the rules on replies a model really gave

def test_the_rules_pass_the_right_replies_kept_from_real_runs():
    """Replies kept from release runs and the golden set (tests/test_sixteen_weeks.py), judged by this run's rules:
    a right one that fails here is a fault of the pattern, not of the model."""
    from tests.test_sixteen_weeks import GOLDEN_RUNS, KEPT_RELEASE_RUNS, RUNS
    kept = {(name, number): reply for name, number, _, _, reply in KEPT_RELEASE_RUNS}
    right = {
        "R2": [reply for _, reply in GOLDEN_RUNS["gold_kcal_yesterday"][0]],
        "R4": [reply for _, reply, _ in RUNS["data_year_more"][0]],
        "R5": [RUNS["data_basketball"][0][2][1]],
        "R6": [kept[("data_squat_goal", 1)]] + [reply for _, reply in GOLDEN_RUNS["gold_squat_goal"][0]],
        "R7": [kept[("data_back_on_track", 3)]],
        "R8": [kept[("data_lifting", 2)]],
        "R9": [reply for _, reply, _ in RUNS["data_empty"][0]],
    }
    for case_id, replies in right.items():
        for reply in replies:
            assert without.reply_reasons(BY_ID[case_id], "W", reply) == [], (case_id, reply[:80])
    wrong = {"R2": [reply for _, reply in GOLDEN_RUNS["gold_kcal_yesterday"][1]][:1],
             "R4": [RUNS["data_year_more"][1][0][1]], "R9": [RUNS["data_empty"][1][0][1], RUNS["data_empty"][1][1][1]]}
    for case_id, replies in wrong.items():
        for reply in replies:
            assert without.reply_reasons(BY_ID[case_id], "W", reply), (case_id, reply)


def test_a_plain_no_record_and_a_question_named_back_are_not_false_fails():
    assert without.reply_reasons(BY_ID["R9"], "N-history", "You have not logged any sleep.") == []
    assert without.reply_reasons(BY_ID["R9"], "N-new", "You haven't told me how you sleep.") == []
    assert without.reply_reasons(BY_ID["R1"], "N-new", "You have not shared any food logs with me.") == []
    assert without.reply_reasons(BY_ID["R6"], "N-new", "You have not told me your squat or your goal.") == []
    assert without.reply_reasons(BY_ID["R6"], "N-history",
                                 "Here is whether you are on track: you are behind, 116.7 kg against 120 kg.") == []
    assert without.reply_reasons(BY_ID["R6"], "N-history", "Yes, you are on track: 116.7 kg against 120 kg.")


def test_u2_s_other_outcome_another_set_is_counted_apart(app_settings, clock):
    u2 = BY_ID["U2"]
    logged = {"name": "log_lifts", "arguments": {}, "is_error": False}
    assert without.counted_apart(u2, "W", "Logged it.", [logged]) is True
    assert without.counted_apart(u2, "W", "Same as what?", [{**logged, "is_error": True}]) is False
    assert without.counted_apart(u2, "N-history", "Logged another set of bench press, 80 kg x 5.", []) is True
    assert without.counted_apart(u2, "N-history", RIGHT["U2"], []) is False
    runner = _runner(app_settings, popen=Popen(_result("I've added another set: bench press, 80 kg x 5."),
                                               _result(RIGHT["U2"])))
    results = [runner.one(u2, "N-history", i) for i in (1, 2)]
    assert [(r["passed"], r["apart"]) for r in results] == [(False, True), (True, False)]
    assert "U2 counted apart, logged another set: N-history 1 of 2" in without.summary(results, CASES)
