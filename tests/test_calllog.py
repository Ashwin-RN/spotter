"""The call log: one row per MCP function call, and never an argument, a result or a crash's message.

The marker test sends one distinctive string in every string argument of every function, with
AUTH_DEBUG on, and then looks for it in every call-log column and every captured log line.
"""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import calllog, db, services
from app.models import CallLog

from .conftest import HEADERS, rpc, sign
from .test_mcp import FUNCTIONS, call, compact, one_user, squat, tool_list

MARKER = "zq-marker-5b1e"


def rows() -> list[CallLog]:
    with db.session_scope() as session:
        return list(session.scalars(select(CallLog).order_by(CallLog.id)))


def check_row(row: CallLog, function: str, ok: bool, error_code: str | None, result: dict, user_id: int | None,
              before, after) -> None:
    assert row.function == function
    assert before <= row.started_at <= after
    assert isinstance(row.ms, int) and 0 <= row.ms < 60_000  # a monotonic clock: not comparable to before/after
    assert row.ok is ok and row.error_code == error_code
    text = result["content"][0]["text"]
    assert row.result_bytes == len(text.encode("utf-8")) and text == compact(result["structuredContent"])
    assert row.user_id == user_id


def timed(client, token, name, arguments=None):
    before = db.utcnow()
    result = call(client, token, name, arguments)
    return before, result, db.utcnow()


# ---------------------------------------------------------------- one row per call

def test_a_success_writes_one_row_with_every_column(http):
    before, result, after = timed(http, sign(), "log_bodyweight", {"kg": 80.5})
    assert result["isError"] is False
    [row] = rows()
    check_row(row, "log_bodyweight", True, None, result, one_user().id, before, after)


def test_a_duplicate_is_an_ok_row(http):
    token = sign()
    call(http, token, "log_lifts", {"sets": squat(100)})
    result = call(http, token, "log_lifts", {"sets": squat(100)})
    assert result["structuredContent"]["duplicate"] is True
    assert [(r.ok, r.error_code) for r in rows()] == [(True, None), (True, None)]


def test_a_service_error_writes_a_not_ok_row_with_its_code(http):
    before, result, after = timed(http, sign(), "log_lifts", {"sets": squat(100), "date": "not-a-date"})
    assert result["structuredContent"]["error"] == "invalid_date"
    [row] = rows()
    check_row(row, "log_lifts", False, "invalid_date", result, one_user().id, before, after)


def test_a_crash_writes_a_not_ok_row(http, monkeypatch, logs):
    def crash(*args, **kwargs):
        raise KeyError("boom")

    monkeypatch.setattr(services, "get_progress", crash)
    before, result, after = timed(http, sign(), "get_progress")
    assert result["isError"] is True and result["structuredContent"]["error"] == "internal_error"
    [row] = rows()
    check_row(row, "get_progress", False, "internal_error", result, one_user().id, before, after)
    assert "MCP get_progress crashed: KeyError" in logs.text


def test_a_long_error_code_is_cut_to_the_column(http, monkeypatch):
    code = "an_error_code_far_longer_than_thirty_two_characters"
    monkeypatch.setattr(services, "get_progress", lambda *a: {"error": code, "message": "m", "fix": "f"})
    result = call(http, sign(), "get_progress")
    assert result["structuredContent"]["error"] == code  # the caller sees all of it
    assert rows()[0].error_code == code[:32]


def test_an_identity_error_writes_a_row_with_no_user(make_client):
    client = make_client(signups_per_day=0)
    result = call(client, sign(), "whoami")
    assert result["structuredContent"]["error"] == "signups_closed"
    [row] = rows()
    assert (row.function, row.ok, row.error_code, row.user_id) == ("whoami", False, "signups_closed", None)


def test_every_function_writes_its_own_row(http):
    token = sign()
    for name in FUNCTIONS:
        call(http, token, name, {"sets": squat(100)} if name == "log_lifts" else
             {"kg": 80} if name == "log_bodyweight" else {})
    assert [r.function for r in rows()] == FUNCTIONS


# ---------------------------------------------------------------- the marker

# Every string argument of every function carries the marker; the rest are valid values. The first call of
# each function fails at the service (the marker is not a date, a kind or an exercise); the second, where
# one exists, succeeds and stores the marker as the person's own text.
MARKED_CALLS = [
    ("whoami", {}),
    ("list_exercises", {"kind": MARKER, "category": MARKER}),
    ("log_lifts", {"sets": [{"exercise": MARKER, "weight_kg": 100, "reps": 5}], "date": MARKER, "notes": MARKER}),
    ("log_lifts", {"sets": squat(100), "notes": MARKER}),
    ("log_bodyweight", {"kg": 80, "date": MARKER}),
    ("set_goal", {"kind": MARKER, "exercise": MARKER, "target_value": 150, "target_date": MARKER, "text": MARKER,
                  "start_value": 100, "status": MARKER}),
    ("set_goal", {"kind": "lift", "exercise": "Back Squat", "target_value": 150, "text": MARKER,
                  "target_date": "2099-01-01"}),
    ("get_progress", {"exercise": MARKER}),
    ("update_settings", {"time_zone": MARKER}),
]


def string_fields(schema: dict) -> set[str]:
    """Every string-typed argument, nested ones as parent.child."""
    def is_string(spec: dict) -> bool:
        return spec.get("type") == "string" or any(s.get("type") == "string" for s in spec.get("anyOf", []))

    found = set()
    for prop, spec in schema["properties"].items():
        if is_string(spec):
            found.add(prop)
        ref = (spec.get("items") or {}).get("$ref")
        if ref:
            nested = schema["$defs"][ref.rsplit("/", 1)[1]]
            found |= {f"{prop}.{p}" for p, s in nested["properties"].items() if is_string(s)}
    return found


def marked_fields(arguments: dict) -> set[str]:
    found = {k for k, v in arguments.items() if v == MARKER}
    for k, v in arguments.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            found |= {f"{k}.{p}" for p, value in v[0].items() if value == MARKER}
    return found


def test_the_marker_table_covers_every_string_argument(http):
    for tool in tool_list(http, sign()):
        marked = set().union(*(marked_fields(a) for name, a in MARKED_CALLS if name == tool["name"]))
        assert string_fields(tool["inputSchema"]) == marked, tool["name"]


def test_a_marker_in_every_argument_reaches_no_log_row_and_no_log_line(make_client, logs):
    client = make_client(auth_debug=True)
    token = sign()
    errors = 0
    for name, arguments in MARKED_CALLS:
        result = call(client, token, name, arguments)
        errors += result["isError"]
    assert errors == 6  # the six marked calls the services refuse; the other three succeed
    logged = rows()
    assert len(logged) == len(MARKED_CALLS)
    for row in logged:
        for column in CallLog.__table__.columns:
            assert MARKER not in str(getattr(row, column.key)), column.name
    assert "token verified" in logs.text  # debug logging was on
    for record in logs.records:
        assert MARKER not in record.getMessage()
        assert MARKER not in str(record.args) and MARKER not in (record.exc_text or "")
    assert MARKER not in logs.text


def test_a_crash_whose_message_holds_the_marker_never_logs_it(http, monkeypatch, logs):
    def crash(*args, **kwargs):
        raise RuntimeError(f"failed while saving {MARKER}")

    monkeypatch.setattr(services, "log_bodyweight", crash)
    result = call(http, sign(), "log_bodyweight", {"kg": 80})
    assert result["structuredContent"]["error"] == "internal_error"
    assert MARKER not in result["content"][0]["text"]
    [row] = rows()
    assert row.ok is False and row.error_code == "internal_error"
    assert "RuntimeError" in logs.text and "test_calllog.py" in logs.text  # logged: type and frames
    assert MARKER not in logs.text
    assert all(MARKER not in (r.exc_text or "") and r.exc_info is None for r in logs.records)


def test_a_database_error_holding_the_marker_never_logs_it(http, monkeypatch, logs):
    def down(*args, **kwargs):
        raise OperationalError(f"SELECT '{MARKER}'", {"name": MARKER}, Exception(MARKER))

    monkeypatch.setattr(services, "log_bodyweight", down)
    result = call(http, sign(), "log_bodyweight", {"kg": 80})
    assert result["structuredContent"]["error"] == "database_unavailable"
    assert "OperationalError" in logs.text and MARKER not in logs.text and MARKER not in result["content"][0]["text"]
    assert rows()[0].error_code == "database_unavailable"


def test_a_result_that_breaks_its_schema_is_an_internal_error_not_a_leak(http, monkeypatch, logs):
    monkeypatch.setattr(services, "get_progress", lambda *a: {"today": MARKER, "how": 1, "links": {}})
    result = call(http, sign(), "get_progress")
    assert result["isError"] is True and result["structuredContent"]["error"] == "internal_error"
    assert "ValidationError" in logs.text and MARKER not in logs.text
    assert rows()[0].ok is False


def test_crash_text_is_type_and_frames_only():
    try:
        raise ValueError(MARKER)
    except ValueError as exc:
        text = calllog.crash_text(exc)
    assert text.startswith("ValueError\n") and "test_calllog.py" in text and MARKER not in text


# ---------------------------------------------------------------- a failing call-log write

def test_a_failing_call_log_write_does_not_fail_the_call(http, monkeypatch, logs):
    def broken(*args, **kwargs):
        raise OperationalError("INSERT", {}, Exception("disk full"))

    monkeypatch.setattr(calllog, "write", broken)
    result = call(http, sign(), "log_bodyweight", {"kg": 80})
    assert result["isError"] is False and result["structuredContent"]["weigh_in"]["kg"] == 80
    assert "call log could not be written: OperationalError" in logs.text
    assert "disk full" not in logs.text
    assert rows() == []


def test_started_at_is_utc_and_ms_is_whole(http):
    call(http, sign(), "whoami")
    row = rows()[0]
    assert row.started_at.utcoffset() == timedelta(0) and isinstance(row.ms, int)


def test_the_call_log_has_no_column_for_content():
    assert {c.name for c in CallLog.__table__.columns} == {
        "id", "user_id", "function", "started_at", "ms", "ok", "error_code", "result_bytes"}


# ---------------------------------------------------------------- a lone surrogate

# What a JSON escape for half of a surrogate pair becomes once parsed: a string that cannot be encoded as UTF-8.
SURROGATE = chr(0xD83D)


def with_surrogates(arguments: dict) -> dict:
    """The marked calls, with a lone surrogate wherever the marker was."""
    def swap(value):
        if value == MARKER:
            return SURROGATE
        if isinstance(value, list):
            return [{k: swap(v) for k, v in item.items()} if isinstance(item, dict) else swap(item) for item in value]
        return value

    return {k: swap(v) for k, v in arguments.items()}


def test_a_lone_surrogate_in_every_string_argument_always_gets_an_answer(make_client, logs):
    client = make_client(auth_debug=True)  # raises if the app raises
    token = sign()
    for name, arguments in MARKED_CALLS:
        sent = with_surrogates(arguments)
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": sent}}
        wire = json.dumps(body).encode("ascii")  # the surrogate travels as a JSON escape, as a client sends it
        assert (b"d83d" in wire) == bool(marked_fields(arguments)), name
        r = client.post("/mcp", content=wire, headers={**HEADERS, "Authorization": f"Bearer {token}"})
        assert r.status_code < 500, (name, r.status_code)
        answer = r.json()
        if "error" in answer:  # the MCP library refused the request itself
            assert answer["error"]["message"], name
            continue
        result = answer["result"]
        result["content"][0]["text"].encode("utf-8")  # what came back is valid UTF-8
        if result["isError"] and result.get("structuredContent"):
            assert result["structuredContent"]["fix"], name
    for record in logs.records:
        assert record.exc_info is None, record.getMessage()
        assert "unexpected exception" not in record.getMessage()
        assert SURROGATE not in record.getMessage()
    assert all(SURROGATE not in str(getattr(row, c.key)) for row in rows() for c in CallLog.__table__.columns)


@pytest.mark.parametrize("answer", [
    {"exercises": [{"name": SURROGATE, "kind": "lift", "category": "push"}]},
    {"error": "invalid_field", "message": f"kind '{SURROGATE}' is not one of lift, cardio.", "fix": "Use lift."},
], ids=["in a result", "in an error"])
def test_a_result_that_cannot_be_sent_is_an_internal_error(http, monkeypatch, logs, answer):
    monkeypatch.setattr(services, "list_exercises", lambda *a: answer)
    result = call(http, sign(), "list_exercises")
    assert result["isError"] is True and result["structuredContent"]["error"] == "internal_error"
    [row] = rows()
    assert row.ok is False and row.error_code == "internal_error"
    assert "UnicodeEncodeError" in logs.text and SURROGATE not in logs.text


# ---------------------------------------------------------------- values JSON cannot carry, a busy database

def test_a_result_holding_nan_is_an_internal_error(http, monkeypatch, logs):
    summary = {"today": "2026-11-09", "goals": [], "how": "How.", "links": {"progress": "https://x.example/progress"},
               "lifts": [{"exercise": "Back Squat", "sessions": 1, "last_trained": None,
                          "current_estimated_max_kg": float("nan"), "best_estimated_max_kg": None,
                          "slope_per_week": None, "goal_status": None}]}
    monkeypatch.setattr(services, "get_progress", lambda *a: summary)
    result = call(http, sign(), "get_progress")
    assert result["isError"] is True and result["structuredContent"]["error"] == "internal_error"
    assert "NaN" not in result["content"][0]["text"]
    [row] = rows()
    assert row.ok is False and row.error_code == "internal_error"
    assert "ValueError" in logs.text


def test_a_pool_timeout_is_database_unavailable(http, monkeypatch, logs):
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    def busy(*args, **kwargs):
        raise PoolTimeout(f"QueuePool limit reached {MARKER}")

    monkeypatch.setattr(services, "log_bodyweight", busy)
    result = call(http, sign(), "log_bodyweight", {"kg": 80})
    assert result["structuredContent"]["error"] == "database_unavailable"
    assert rows()[0].error_code == "database_unavailable"
    assert "TimeoutError" in logs.text and MARKER not in logs.text


# ---------------------------------------------------------------- a resource that crashes

class _Unreadable:
    def read_text(self, encoding=None):
        raise OSError(f"cannot read {MARKER}")


@pytest.mark.parametrize("uri, how", [
    ("spotter://exercises", "crash"), ("spotter://exercises", "database"), ("spotter://can-and-cant", "crash"),
])
def test_a_resource_that_fails_never_shows_or_logs_its_message(http, monkeypatch, logs, uri, how):
    from app import mcp_server

    def crash(*args, **kwargs):
        raise RuntimeError(f"exercise list broke {MARKER}")

    def down(*args, **kwargs):
        raise OperationalError(f"SELECT '{MARKER}'", {"x": MARKER}, Exception(MARKER))

    if uri == "spotter://can-and-cant":
        monkeypatch.setattr(mcp_server, "CAN_AND_CANT", _Unreadable())
    else:
        monkeypatch.setattr(services, "list_exercises", crash if how == "crash" else down)
    r = rpc(http, "resources/read", {"uri": uri}, token=sign())
    assert r.status_code == 200
    assert "error" in r.json() and r.json()["error"]["message"]
    assert MARKER not in r.text and MARKER not in logs.text
    assert ("MCP resource crashed" if how == "crash" else "could not use the database") in logs.text
