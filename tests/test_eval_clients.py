"""The eval runner's Codex and Gemini clients (contract function-list.md, G1): each one's command or request, its
parser on a kept transcript, the token kept out of every command, and a cut-off run that keeps what it had.

Nothing here starts Codex, calls Google or opens a port: Codex's process, the Gemini REST call and the MCP connection
are each replaced at the seam the client gives for it. No test reads .env.evals: the key file here is a temporary one.
"""

import json
import subprocess
import tempfile
from pathlib import Path

import anyio
import pytest

from evals import codex_client, gemini_client, judge
from evals.recorder import CallRecorder
from evals.run import LocalServer, child_env

# ---------------------------------------------------------------- Codex

# The kept transcript of the 2026-11-17 trial, data_lb on codex-cli 0.169.0: every event in order, each result's
# text cut short. The agent message's apostrophe is the trial's own replacement character.
CODEX_DATA_LB = "\n".join(json.dumps(e) for e in [
    {"type": "thread.started", "thread_id": "00000000-0000-0000-0000-000000000000"},  # the trial's id replaced
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message",
                                        "text": "I�ll check your bench press records for the last four weeks."}},
    {"type": "item.started", "item": {"id": "item_1", "type": "mcp_tool_call", "server": "spotter", "tool": "whoami",
                                      "arguments": {}, "result": None, "error": None, "status": "in_progress"}},
    {"type": "item.completed", "item": {"id": "item_1", "type": "mcp_tool_call", "server": "spotter", "tool": "whoami",
                                        "arguments": {}, "result": {"content": [{"type": "text", "text": "{...}"}],
                                                                    "structured_content": {"today": "2026-11-17"}},
                                        "error": None, "status": "completed"}},
    {"type": "item.completed", "item": {"id": "item_2", "type": "mcp_tool_call", "server": "spotter",
                                        "tool": "what_is_logged", "arguments": {},
                                        "result": {"content": [], "structured_content": {}}, "error": None,
                                        "status": "completed"}},
    {"type": "item.completed", "item": {"id": "item_3", "type": "mcp_tool_call", "server": "spotter",
                                        "tool": "get_data",
                                        "arguments": {"area": "lift_sets", "filters": {"exercise": "Bench Press"},
                                                      "fields": ["date", "weight_lb", "reps"], "order": "-weight_lb",
                                                      "limit": 1, "totals": {"weight_lb": ["max"]}},
                                        "result": {"content": [], "structured_content": {}}, "error": None,
                                        "status": "completed"}},
    {"type": "item.completed", "item": {"id": "item_4", "type": "agent_message",
                                        "text": "Your heaviest bench press was **214.95 lb for 5 reps** on **13 November**."
                                                "\n\nThat covers the last four weeks, 21 October to 17 November."}},
    {"type": "turn.completed", "usage": {"input_tokens": 109233, "cached_input_tokens": 77696,
                                         "cache_write_input_tokens": 0, "output_tokens": 236,
                                         "reasoning_output_tokens": 0}},
])


def test_the_codex_command_pins_the_model_and_effort_and_never_holds_the_token():
    command = codex_client.codex_command("codex.exe", "Lunch: dal, 180 kcal", 5050, Path("cwd"), "gpt-6-luna", "xhigh")
    assert command[:6] == ["codex.exe", "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config"]
    assert command[command.index("-s") + 1] == "read-only" and command[command.index("-m") + 1] == "gpt-6-luna"
    overrides = [command[i + 1] for i, part in enumerate(command) if part == "-c"]
    assert overrides == ['web_search="disabled"', 'model_reasoning_effort="xhigh"',
                         'mcp_servers.spotter.url="http://127.0.0.1:5050/mcp"',
                         'mcp_servers.spotter.bearer_token_env_var="SPOTTER_EVAL_TOKEN"',
                         'mcp_servers.spotter.default_tools_approval_mode="approve"']
    assert [command[i + 1] for i, part in enumerate(command) if part == "--disable"] == list(
        codex_client.DISABLED_FEATURES)
    assert command[-1] == "Lunch: dal, 180 kcal"
    bare = codex_client.codex_command("codex.exe", "x", 1, Path("cwd"), "", "")
    assert "-m" not in bare and not any("model_reasoning_effort" in part for part in bare)


def test_the_codex_parser_reads_a_kept_transcript():
    got = codex_client.parse_codex_jsonl("Reading additional input from stdin...\n" + CODEX_DATA_LB)
    assert got["reply"].startswith("Your heaviest bench press was **214.95 lb for 5 reps**")
    assert got["num_turns"] == 1 and got["completed"] is True and got["errors"] == []
    assert (got["input_tokens"], got["cached_tokens"], got["output_tokens"], got["reasoning_tokens"]) == (
        109233 - 77696, 77696, 236, 0)
    assert got["tokens"] == 109233 + 236 == got["input_tokens"] + got["cached_tokens"] + got["output_tokens"]
    assert [i["tool"] for i in got["tool_items"]] == ["whoami", "what_is_logged", "get_data"]
    assert not any(i["is_error"] for i in got["tool_items"])
    assert got["item_types"] == ["agent_message", "mcp_tool_call", "mcp_tool_call", "mcp_tool_call", "agent_message"]


def test_the_codex_parser_marks_a_failed_call_and_a_turn_that_never_finished():
    failed = json.dumps({"type": "item.completed", "item": {"id": "i", "type": "mcp_tool_call", "tool": "log_food",
                                                            "arguments": {}, "result": None, "error": {"message": "x"},
                                                            "status": "failed"}})
    got = codex_client.parse_codex_jsonl(json.dumps({"type": "turn.started"}) + "\n" + failed + "\n"
                                         + json.dumps({"type": "turn.failed", "error": {"message": "usage limit"}}))
    assert got["tool_items"][0]["is_error"] is True and got["completed"] is False and len(got["errors"]) == 1
    assert got["reply"] == "" and got["tokens"] == 0
    for stdout in ("", "not json", "Error: not signed in"):
        with pytest.raises(judge.CliOutputError):
            codex_client.parse_codex_jsonl(stdout)


def test_codex_is_found_as_the_vendor_binary_first():
    with tempfile.TemporaryDirectory() as folder:
        exe = Path(folder) / codex_client.VENDOR_EXE
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        assert codex_client.find_codex({"APPDATA": folder}) == str(exe)
    assert codex_client.find_codex({}) in (None, codex_client.shutil.which("codex"))


class FakeProcess:
    """Stands in for codex exec: its stdout is a kept transcript, or it never finishes."""

    def __init__(self, stdout: str, hang: bool = False) -> None:
        self.stdout_text, self.hang, self.pid, self.returncode, self.killed = stdout, hang, 4242, 0, False

    def communicate(self, timeout=None):
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("codex", timeout)
        return self.stdout_text, "Reading additional input from stdin...\n"


def _codex_server(app_settings) -> LocalServer:
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: its people and its recorder are used
    server.settings = app_settings
    server.recorder = CallRecorder(None)
    return server


def test_a_codex_run_puts_the_token_in_its_environment_only_and_is_judged_on_the_server_s_record(app_settings):
    server = _codex_server(app_settings)
    given = []

    def popen(command, **kwargs):
        given.append((command, kwargs["env"]))
        return FakeProcess(CODEX_DATA_LB)

    case = {"id": "c", "say": "What is the heaviest I have benched?", "expect": {"reply_has": ["214.95"]},
            "calls": {"must": [], "may": [], "must_not": []}}
    env, _ = child_env({"PATH": "/bin"})
    with tempfile.TemporaryDirectory() as folder:
        outcome = codex_client.run_once_codex(server, case, "Asia/Kolkata", "codex.exe", "gpt-6-luna", "medium", env,
                                              Path(folder), "c-1", True, popen=popen)
        kept = (Path(folder) / "out-c-1.jsonl").read_text(encoding="utf-8")
    [(command, child)] = given
    token = child[codex_client.TOKEN_VARIABLE]
    assert token.count(".") == 2 and token not in " ".join(command) and token not in kept
    assert codex_client.TOKEN_VARIABLE not in env  # the parent's copy never holds it
    assert outcome["passed"] is True and outcome["client"] == "codex" and outcome["cost_usd"] is None
    assert (outcome["model"], outcome["effort"], outcome["models"], outcome["num_turns"]) == ("gpt-6-luna", "medium",
                                                                                              [], 1)
    assert outcome["calls"] == []  # the server's record, empty here: never the transcript's own account


def test_a_codex_run_that_is_cut_off_is_stopped_and_fails(app_settings, monkeypatch):
    server = _codex_server(app_settings)
    stopped = []
    process = FakeProcess("", hang=True)
    monkeypatch.setattr(codex_client, "_stop_tree", lambda p: (stopped.append(p.pid), setattr(p, "killed", True)))
    case = {"id": "c", "say": "x", "expect": {"reply_has": ["x"]}}
    with tempfile.TemporaryDirectory() as folder:
        outcome = codex_client.run_once_codex(server, case, "Asia/Kolkata", "codex.exe", None, None, {}, Path(folder),
                                              "c-1", False, popen=lambda command, **kw: process)
    assert stopped == [4242] and outcome["timed_out"] is True and outcome["passed"] is False
    assert outcome["reasons"][0].startswith("codex: codex printed no JSON events")


# ---------------------------------------------------------------- Gemini

KEY = "test-gemini-key-not-a-real-one"


def test_the_key_is_read_from_its_own_file_and_never_named_in_an_error():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / ".env.evals"
        path.write_text(f"# keys\nGOOGLE_API_KEY_1={KEY}\nGOOGLE_API_KEY_2=\n", encoding="utf-8")
        assert gemini_client.load_key(1, path) == KEY
        for n in (2, 3):
            with pytest.raises(KeyError) as e:
                gemini_client.load_key(n, path)
            assert KEY not in str(e.value) and f"GOOGLE_API_KEY_{n}" in str(e.value)
        with pytest.raises(KeyError, match="is not there"):
            gemini_client.load_key(1, Path(folder) / "none")
    assert gemini_client.ENV_FILE == Path(gemini_client.__file__).resolve().parent.parent / ".env.evals"


def _reply(parts, usage=None, finish="STOP", version="gemini-4-hydrogen"):
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
            "usageMetadata": usage or {"promptTokenCount": 100, "cachedContentTokenCount": 40,
                                       "candidatesTokenCount": 5, "thoughtsTokenCount": 7, "totalTokenCount": 112},
            "modelVersion": version}


class FakePost:
    """Stands in for the REST call: answers in turn, and keeps what it was sent (the key only in its header)."""

    def __init__(self, *answers) -> None:
        self.answers, self.sent = list(answers), []

    def __call__(self, url, headers, body, timeout):
        self.sent.append((url, headers, json.loads(json.dumps(body))))
        status, answer = self.answers.pop(0)
        return status, json.dumps(answer).encode("utf-8"), {"X-Gemini-Service-Tier": "standard"}


def test_one_back_off_on_a_429_or_503_and_the_key_only_in_a_header():
    slept = []
    post = FakePost((503, {"error": {"message": f"busy {KEY}", "details": [{"retryDelay": "12s"}]}}), (200, _reply([])))
    api = gemini_client.GeminiAPI(KEY, post=post, sleep=slept.append, per_minute=0)  # no pacing here
    backoffs = []
    status, parsed, _, attempts = api.generate("gemini-4-hydrogen", {"contents": []}, backoffs)
    assert (status, attempts, slept) == (200, 2, [13.0])
    assert backoffs[0]["status"] == 503 and KEY not in json.dumps(backoffs) and gemini_client.KEY_HIDDEN in json.dumps(
        backoffs)
    for url, headers, body in post.sent:
        assert url.endswith("/models/gemini-4-hydrogen:generateContent") and KEY not in url
        assert headers["x-goog-api-key"] == KEY and KEY not in json.dumps(body)
    assert gemini_client.retry_delay({}) == 20.0
    assert gemini_client.retry_delay({"error": {"details": [{"retryDelay": "6497s"}]}}) == gemini_client.MAX_BACKOFF


def test_the_request_carries_the_instructions_every_schema_unchanged_and_the_pinned_thinking_level():
    tools = [{"name": "log_food", "description": "Logs food.",
              "inputSchema": {"type": "object", "properties": {"kcal": {"anyOf": [{"type": "number"}, {"type": "null"}],
                                                                        "title": "Kcal"}},
                              "additionalProperties": False}},
             {"name": "whoami", "description": "Who.", "inputSchema": {"type": "object", "properties": {}}}]
    body = gemini_client.request_body("Call whoami first.", "Lunch: dal, 180 kcal", tools, "json", "high")
    assert body["systemInstruction"] == {"parts": [{"text": "Call whoami first."}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Lunch: dal, 180 kcal"}]}]
    assert body["generationConfig"] == {"thinkingConfig": {"thinkingLevel": "HIGH"}}
    declared = body["tools"][0]["functionDeclarations"]
    assert [d["parametersJsonSchema"] for d in declared] == [t["inputSchema"] for t in tools]
    assert "generationConfig" not in gemini_client.request_body("i", "s", tools, "json", "")
    subset = gemini_client.declarations(tools, "openapi")
    assert subset[0]["parameters"] == {"type": "object", "properties": {"kcal": {"nullable": True, "type": "number",
                                                                                 "title": "Kcal"}}}
    assert "parameters" not in subset[1]  # no arguments, no parameters


class FakeSession:
    """The MCP side of a run: its instructions, its tools, and each call's answer. slow: a call that never ends."""

    instructions = "Spotter's instructions."

    def __init__(self, slow: bool = False) -> None:
        self.slow, self.called = slow, []

    async def list_tools(self, cursor=None):
        class Page:
            tools, next_cursor = [], None
        return Page()

    async def call_tool(self, name, arguments):
        self.called.append((name, arguments))
        if self.slow:
            await anyio.sleep(30)

        class Result:
            def model_dump(self, **kw):
                return {"structuredContent": {"name": name, "today": "2026-11-17"}, "isError": False}
        return Result()


def _connect(session):
    import contextlib

    @contextlib.asynccontextmanager
    async def connect(port, token):
        yield session
    return connect


class FakeRecorder:
    """The server's record for a run whose MCP side is a fake: the handshake carried the instructions."""

    def calls(self, token):
        return []

    def methods(self, token):
        return ["server/discover", "tools/list"]

    def got_instructions(self, token):
        return True


def _gemini_server(app_settings) -> LocalServer:
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: its people are used
    server.settings = app_settings
    server.recorder = FakeRecorder()
    return server


CASE = {"id": "g", "say": "Lunch: dal, 180 kcal", "expect": {"reply_has": ["Logged"]},
        "calls": {"must": [], "may": [], "must_not": []}}


def test_a_gemini_run_sends_each_call_back_with_its_id_and_every_model_part_as_received(app_settings):
    signed = {"functionCall": {"name": "whoami", "args": {}, "id": "call_1"}, "thoughtSignature": "sig-1"}
    post = FakePost((200, _reply([signed])), (200, _reply([{"text": "Logged dal, 180 kcal."}])))
    host = gemini_client.GeminiHost(gemini_client.GeminiAPI(KEY, post=post, sleep=lambda s: None), "gemini-4-hydrogen",
                                    "high")
    session = FakeSession()
    outcome = gemini_client.run_once_gemini(_gemini_server(app_settings), CASE, "Asia/Kolkata", host,
                                            connect=_connect(session), timeout=30)
    assert outcome["passed"] is True and outcome["reply"] == "Logged dal, 180 kcal." and session.called == [("whoami", {})]
    second = post.sent[1][2]["contents"]
    assert second[1] == {"role": "model", "parts": [signed]}  # as received, its thought signature on its part
    assert second[2] == {"role": "user", "parts": [{"functionResponse": {
        "name": "whoami", "response": {"name": "whoami", "today": "2026-11-17"}, "id": "call_1"}}]}
    assert (outcome["num_turns"], outcome["finish_reasons"], outcome["calls_per_turn"]) == (2, ["STOP", "STOP"],
                                                                                            [["whoami"]])
    assert outcome["models"] == ["gemini-4-hydrogen"] and outcome["cost_usd"] is None
    assert (outcome["input_tokens"], outcome["cached_tokens"], outcome["output_tokens"], outcome["tokens"]) == (
        120, 80, 24, 224)
    assert KEY not in json.dumps(outcome)


def test_a_cut_off_gemini_run_keeps_its_usage_finish_reasons_and_back_offs(app_settings):
    """The trial's gap: a run cut off at the time limit kept its transcript but lost the rest."""
    busy = (503, {"error": {"message": "high demand", "details": [{"retryDelay": "1s"}]}})
    post = FakePost(busy, (200, _reply([{"functionCall": {"name": "whoami", "args": {}, "id": "call_1"}}])))
    host = gemini_client.GeminiHost(gemini_client.GeminiAPI(KEY, post=post, sleep=lambda s: None), "gemini-4-hydrogen",
                                    "high")
    outcome = gemini_client.run_once_gemini(_gemini_server(app_settings), CASE, "Asia/Kolkata", host,
                                            connect=_connect(FakeSession(slow=True)), timeout=0.5)
    assert outcome["timed_out"] is True and outcome["passed"] is False
    assert outcome["reasons"][0] == "timed out after 0.5 s"
    assert outcome["usage"]["promptTokenCount"] == 100 and outcome["tokens"] == 112
    assert outcome["finish_reasons"] == ["STOP"] and outcome["num_turns"] == 1
    assert [b["status"] for b in outcome["backoffs"]] == [503]


def test_a_quota_that_persists_stops_the_model_and_a_thinking_refusal_stops_it_too(app_settings):
    server = _gemini_server(app_settings)  # one a test: two made in the same second would name their people alike
    quota = (429, {"error": {"message": f"quota {KEY}", "details": [{"retryDelay": "1s"}]}})
    host = gemini_client.GeminiHost(gemini_client.GeminiAPI(KEY, post=FakePost(quota, quota), sleep=lambda s: None),
                                    "gemini-3.1-pro-preview", "high")
    with pytest.raises(gemini_client.QuotaStop) as stop:
        gemini_client.run_once_gemini(server, CASE, "Asia/Kolkata", host,
                                      connect=_connect(FakeSession()), timeout=30)
    assert KEY not in str(stop.value)
    thinking = (400, {"error": {"message": "thinking_level xhigh is not supported for this model"}})
    host = gemini_client.GeminiHost(gemini_client.GeminiAPI(KEY, post=FakePost(thinking), sleep=lambda s: None),
                                    "gemini-3.8-flash", "xhigh")
    with pytest.raises(gemini_client.ConfigStop):
        gemini_client.run_once_gemini(server, CASE, "Asia/Kolkata", host,
                                      connect=_connect(FakeSession()), timeout=30)
    assert host.schema_mode == "json"  # a thinking refusal never switches the schemas


def test_a_schema_refusal_switches_to_the_subset_once_and_a_setup_fault_is_retried_twice(app_settings):
    refused = (400, {"error": {"message": "Invalid JSON payload: parametersJsonSchema is not supported"}})
    post = FakePost(refused, (200, _reply([{"text": "Logged."}])))
    host = gemini_client.GeminiHost(gemini_client.GeminiAPI(KEY, post=post, sleep=lambda s: None), "gemini-x", "")
    server = _gemini_server(app_settings)
    outcome = gemini_client.run_once_gemini(server, CASE, "Asia/Kolkata", host, connect=_connect(FakeSession()),
                                            timeout=30)
    assert outcome["passed"] is True and host.schema_mode == "openapi" and len(host.schema_refusals) == 1
    assert outcome["setup_faults"][0].startswith("parametersJsonSchema refused")
    busy = (503, {"error": {"message": "high demand"}})
    host = gemini_client.GeminiHost(gemini_client.GeminiAPI(KEY, post=FakePost(*[busy] * 6), sleep=lambda s: None),
                                    "gemini-x", "")
    outcome = gemini_client.run_once_gemini(server, CASE, "Asia/Kolkata", host, connect=_connect(FakeSession()),
                                            timeout=30)
    assert outcome["reasons"] == ["setup faults on every attempt"] and len(outcome["setup_faults"]) == 3


def test_a_handshake_with_no_instructions_is_a_setup_fault_not_a_model_s_miss(app_settings):
    session = FakeSession()
    session.instructions = None
    host = gemini_client.GeminiHost(gemini_client.GeminiAPI(KEY, post=FakePost(), sleep=lambda s: None), "gemini-x", "")
    outcome = gemini_client.run_once_gemini(_gemini_server(app_settings), CASE, "Asia/Kolkata", host,
                                            connect=_connect(session), timeout=30)
    assert outcome["setup_faults"] == ["the handshake carried no instructions"] * 3


def test_a_connection_that_fails_is_a_setup_fault_and_any_other_error_is_raised(app_settings):
    import contextlib

    def failing(error):
        @contextlib.asynccontextmanager
        async def connect(port, token):
            raise error
            yield  # pragma: no cover
        return connect

    server = _gemini_server(app_settings)
    host = gemini_client.GeminiHost(gemini_client.GeminiAPI(KEY, post=FakePost(), sleep=lambda s: None), "gemini-x", "")
    outcome = gemini_client.run_once_gemini(server, CASE, "Asia/Kolkata", host,
                                            connect=failing(ConnectionRefusedError("refused")), timeout=30)
    assert outcome["setup_faults"] == ["the MCP connection failed: ConnectionRefusedError: refused"] * 3
    grouped = failing(ExceptionGroup("task group", [ConnectionResetError("reset")]))
    outcome = gemini_client.run_once_gemini(server, CASE, "Asia/Kolkata", host, connect=grouped, timeout=30)
    assert outcome["setup_faults"][0] == "the MCP connection failed: ConnectionResetError: reset"
    with pytest.raises(KeyError):  # a fault of this code, never retried as the client's
        gemini_client.run_once_gemini(server, CASE, "Asia/Kolkata", host, connect=failing(KeyError("tools")),
                                      timeout=30)


# The kept run of the 2026-11-17 Gemini trial, gemini-4-hydrogen on how_am_i_doing: its three model turns as received,
# each thought signature cut short, and each turn's usage word for word. Turn 2 made two calls at once.
KEPT_HYDROGEN = [
    ({"role": "model", "parts": [{"functionCall": {"name": "whoami", "args": {}, "id": "call_13783808910759029537"},
                                  "thoughtSignature": "ErcECrQEAWkU"}]},
     {"promptTokenCount": 15667, "candidatesTokenCount": 14, "totalTokenCount": 15799, "thoughtsTokenCount": 118,
      "serviceTier": "standard"}),
    ({"role": "model", "parts": [{"functionCall": {"name": "get_trends", "args": {}, "id": "call_15193024361460397808"},
                                  "thoughtSignature": "ErQECrEEAWkU"},
                                 {"functionCall": {"name": "get_goals", "args": {}, "id": "call_15193024361460397501"}}]},
     {"promptTokenCount": 16171, "candidatesTokenCount": 28, "totalTokenCount": 16322, "cachedContentTokenCount": 13613,
      "thoughtsTokenCount": 123, "serviceTier": "standard"}),
    ({"role": "model", "parts": [{"thoughtSignature": "Eqo+Cqc+AWkU", "text": "Body weight is 79 kg now, smoothed 79.2 kg."}]},
     {"promptTokenCount": 19133, "candidatesTokenCount": 365, "totalTokenCount": 22224, "cachedContentTokenCount": 16172,
      "thoughtsTokenCount": 2726, "serviceTier": "standard"}),
]


def test_a_kept_gemini_run_with_two_calls_in_one_turn(app_settings):
    """Both calls of turn 2 run one after another, and both answers go back in one content, in order, each with its
    call's id. The usage of the three turns sums to the trial's own figures."""
    post = FakePost(*[(200, {"candidates": [{"content": content, "finishReason": "STOP"}], "usageMetadata": usage,
                             "modelVersion": "gemini-4-hydrogen"}) for content, usage in KEPT_HYDROGEN])
    host = gemini_client.GeminiHost(gemini_client.GeminiAPI(KEY, post=post, sleep=lambda s: None), "gemini-4-hydrogen",
                                    "high")
    session = FakeSession()
    case = {"id": "how_am_i_doing", "say": "How am I doing?", "expect": {"reply_has": ["79.2"]},
            "calls": {"must": [], "may": [], "must_not": []}}
    outcome = gemini_client.run_once_gemini(_gemini_server(app_settings), case, "Asia/Kolkata", host,
                                            connect=_connect(session), timeout=30)
    assert outcome["passed"] is True and outcome["calls_per_turn"] == [["whoami"], ["get_trends", "get_goals"]]
    assert [name for name, _ in session.called] == ["whoami", "get_trends", "get_goals"]
    third = post.sent[2][2]["contents"]
    assert third[3] == KEPT_HYDROGEN[1][0]  # the model's turn, as received
    assert [part["functionResponse"]["id"] for part in third[4]["parts"]] == ["call_15193024361460397808",
                                                                               "call_15193024361460397501"]
    assert (outcome["input_tokens"], outcome["cached_tokens"], outcome["output_tokens"], outcome["tokens"]) == (
        21186, 29785, 3374, 54345)


def test_each_request_is_paced_under_the_free_tier_s_five_a_minute():
    """The free tier takes 5 requests a minute: each waits until 12 s have passed since the one before."""
    now = [100.0]
    slept = []

    def sleep(seconds):
        slept.append(round(seconds, 3))
        now[0] += seconds

    post = FakePost(*[(200, _reply([]))] * 3)
    api = gemini_client.GeminiAPI(KEY, post=post, sleep=sleep, clock=lambda: now[0])
    assert gemini_client.PER_MINUTE == 5
    api.generate("gemini-4-hydrogen", {}, [])
    now[0] += 5.0  # the model's answer and the calls took 5 s
    api.generate("gemini-4-hydrogen", {}, [])
    now[0] += 20.0  # longer than the gap: no wait
    api.generate("gemini-4-hydrogen", {}, [])
    assert slept == [7.0] and api.paced == [7.0] and len(post.sent) == 3
    unpaced = gemini_client.GeminiAPI(KEY, post=FakePost(*[(200, _reply([]))] * 2), sleep=sleep,
                                      clock=lambda: now[0], per_minute=0)
    unpaced.generate("m", {}, [])
    unpaced.generate("m", {}, [])
    assert slept == [7.0]


def test_a_setup_fault_never_carries_the_person_s_token(app_settings):
    """N3: the token is masked in every stop and fault, as the key is redacted."""
    import contextlib

    @contextlib.asynccontextmanager
    async def connect(port, token):
        raise ConnectionRefusedError(f"refused for Bearer {token}")
        yield  # pragma: no cover

    host = gemini_client.GeminiHost(gemini_client.GeminiAPI(KEY, post=FakePost(), sleep=lambda s: None), "gemini-x", "")
    outcome = gemini_client.run_once_gemini(_gemini_server(app_settings), CASE, "Asia/Kolkata", host, connect=connect,
                                            timeout=30)
    assert outcome["setup_faults"] == ["the MCP connection failed: ConnectionRefusedError: refused for Bearer "
                                       + judge.HIDDEN] * 3
