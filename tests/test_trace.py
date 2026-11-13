"""The trace (docs/contracts/trace.md): a request id on every call, what each call tried, how far it got and
where it stopped, why USDA failed, a 90-day retention, and owner_calls for the owner alone.

Every call goes through the real HTTP path (TestClient), so the request id, the worker threads and the notes
are the ones a real call has. USDA is replaced at usda.urlopen, as in tests/test_usda.py.
"""

import io
import json
import logging
import re
import socket
import tempfile
import urllib.error
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, insert
from sqlalchemy.exc import OperationalError

from app import calllog, db, main, mcp_server, rules, services, web
from app.config import load_settings
from app.db import make_engine
from app.main import MAX_BODY_BYTES
from app.models import Base, Bodyweight, CallLog
from app.services import usda

from . import text_rules as tr
from .conftest import HEADERS, RESOURCE, TEST_DIR, alembic_config, claims, rpc, sign
from .test_calllog import MARKED_CALLS, MARKER, rows
from .test_mcp import FUNCTIONS, set_switch, squat
from .test_usda import KEY, Fake, banana
from .test_web import client, post, seam, sign_in, web_client  # noqa: F401 - fixtures

ID = re.compile(r"[0-9a-f]{16}")
TRACE = ("request_id", "caller", "tried", "outcome", "step", "frame", "notes")
WEB_SUBJECT = "user_01WEB"  # the person tests/test_web.py's seam signs in
OWNER = "user_01OWNER"


def sign_for(subject: str) -> str:
    return sign(claims(sub=subject))


def tools_call(client, token: str, name: str, arguments: dict | None = None):
    """(the HTTP response, the tools/call result)."""
    r = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, token=token)
    assert r.status_code == 200, r.text
    return r, r.json()["result"]


def last() -> CallLog:
    return rows()[-1]


def today() -> str:
    return db.utcnow().date().isoformat()


def crash(*args, **kwargs):
    raise KeyError(MARKER)


@pytest.fixture
def stderr():
    """Spotter's own handler, writing to a buffer for the test: the real format and the real filter."""
    main._set_up_logging()
    [handler] = [h for h in logging.getLogger("spotter").handlers if type(h) is logging.StreamHandler]
    buffer = io.StringIO()
    before = handler.setStream(buffer)
    yield buffer
    handler.setStream(before)


@pytest.fixture
def usda_reply(monkeypatch):
    """usda_reply(reply): USDA answers with reply, or raises it."""
    def stub(reply):
        monkeypatch.setattr(usda, "urlopen", Fake(reply))
    return stub


# ---------------------------------------------------------------- decision 1: one id per request

def test_every_response_carries_its_own_id_and_no_caller_chooses_it(http):
    chosen = {"X-Request-Id": "chosen-by-the-caller"}
    answers = [http.get("/health", headers=chosen), http.get("/"), http.get("/nowhere"), rpc(http, "initialize"),
               http.post("/mcp", content=b"x" * (MAX_BODY_BYTES + 1), headers={**HEADERS, **chosen})]
    assert answers[-1].status_code == 413 and answers[1].status_code == 200
    ids = [r.headers["x-request-id"] for r in answers]
    assert all(ID.fullmatch(i) for i in ids) and len(set(ids)) == len(ids)


def test_an_mcp_call_s_row_carries_the_id_of_its_response_and_its_result_does_not(http):
    r, result = tools_call(http, sign(), "log_bodyweight", {"kg": 80.5})
    row = last()
    assert row.request_id == r.headers["x-request-id"] and row.caller == "claude"
    assert row.request_id not in r.text  # decision 17: the header only


def test_a_web_write_s_row_carries_the_id_of_its_response(client):
    sign_in(client)
    r = post(client, f"/day/{today()}/weigh-in", kg="80.5")
    assert r.status_code == 303
    row = last()
    assert (row.function, row.caller, row.ok, row.request_id) == ("log_bodyweight", "web", True,
                                                                 r.headers["x-request-id"])


# ---------------------------------------------------------------- decision 2: the id on every log line

def test_the_crash_line_carries_the_id_of_its_call_and_a_line_outside_a_request_a_dash(http, monkeypatch, stderr):
    monkeypatch.setattr(services, "get_progress", crash)
    r, result = tools_call(http, sign(), "get_progress")
    request_id = r.headers["x-request-id"]
    assert result["structuredContent"]["error"] == "internal_error" and last().request_id == request_id
    assert f"ERROR spotter.mcp [{request_id}]: MCP get_progress crashed: KeyError" in stderr.getvalue()
    logging.getLogger("spotter.trace").warning("Outside a request.")
    assert "WARNING spotter.trace [-]: Outside a request." in stderr.getvalue()


# ---------------------------------------------------------------- decision 3: migration 0005

def test_0005_adds_seven_nullable_columns_and_an_index_and_comes_down_again():
    address = f"sqlite:///{(Path(tempfile.mkdtemp(dir=TEST_DIR)) / 'trace.db').as_posix()}"
    cfg = alembic_config(address)
    command.upgrade(cfg, "0004")
    engine = make_engine(address)
    try:
        with engine.begin() as c:
            c.exec_driver_sql("INSERT INTO call_log (function, started_at, ms, ok, result_bytes) "
                              "VALUES ('whoami', '2026-11-10 08:00:00.000000', 1, 1, 10)")
        command.upgrade(cfg, "0005")
        with engine.connect() as c:
            found = {col["name"]: col for col in inspect(c).get_columns("call_log")}
            assert all(found[name]["nullable"] for name in TRACE)
            assert "ix_call_log_request_id" in {i["name"] for i in inspect(c).get_indexes("call_log")}
            old = c.exec_driver_sql(f"SELECT function, {', '.join(TRACE)} FROM call_log").one()
            assert tuple(old) == ("whoami", *[None] * len(TRACE))  # the row already there keeps nulls
            assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []  # the models match
        command.downgrade(cfg, "0004")
        with engine.connect() as c:
            assert {col["name"] for col in inspect(c).get_columns("call_log")} == set(found) - set(TRACE)
            assert "ix_call_log_request_id" not in {i["name"] for i in inspect(c).get_indexes("call_log")}
            assert c.exec_driver_sql("SELECT count(*) FROM call_log").scalar() == 1
    finally:
        engine.dispose()


# ---------------------------------------------------------------- decisions 4 to 6: caller and tried

def test_what_each_call_tried_is_names_and_counts_and_a_web_write_reads_like_an_assistant_s(client):
    sign_in(client)
    token = sign_for(WEB_SUBJECT)  # the same person through MCP
    seen = {}

    def through_mcp(label, name, arguments):
        r, result = tools_call(client, token, name, arguments)
        seen[label] = (last().caller, last().tried)
        return result["structuredContent"]

    through_mcp("food", "log_food", {"date": today(), "entries": [{"description": "Dal", "kcal": 300, "meal": "lunch"},
                                                                  {"description": "Roti", "kcal": 120}]})
    through_mcp("lifts", "log_lifts", {"sets": squat(100, sets=5), "notes": "Felt strong"})
    weigh_in = through_mcp("weigh-in", "log_bodyweight", {"kg": 80})["id"]
    through_mcp("edit", "edit_entry", {"kind": "bodyweight", "entry_id": weigh_in, "kg": 81})
    through_mcp("measurement", "log_measurement", {"kind": "waist", "value": 84})
    through_mcp("goal", "set_goal", {"kind": "bodyweight", "target_value": 75, "target_date": "2099-01-31"})
    through_mcp("import", "import_entries", {"rows": [
        {"kind": "bodyweight", "date": "2026-11-01", "weight": 176},
        {"kind": "bodyweight", "date": "2026-11-02", "weight": 175, "notes": "x"},
        {"kind": "food_entry", "date": "2026-11-02", "entries": [{"description": "Oats", "kcal": 350,
                                                                  "meal": "breakfast"}]}],
        "weight_unit": "lb"})
    through_mcp("settings", "update_settings", {"calorie_target_kcal": 2300})
    assert post(client, f"/entries/bodyweight/{weigh_in}/edit", kg="82", date=today()).status_code == 303
    seen["web edit"] = (last().caller, last().tried)
    assert post(client, "/settings/targets", calorie_target_kcal="2200").status_code == 303
    seen["web settings"] = (last().caller, last().tried)
    assert seen == {
        "food": ("claude", "date entries[2]{description,kcal,meal}"),
        "lifts": ("claude", "notes sets[5]{exercise,reps,weight_kg}"),
        "weigh-in": ("claude", "kg"),
        "edit": ("claude", "entry_id kg kind=bodyweight"),
        "measurement": ("claude", "kind value"),  # a measurement's kind: a name only
        "goal": ("claude", "kind target_date target_value"),  # a goal's kind, though "bodyweight": a name only
        "import": ("claude", "rows[3]{date,entries,kind,notes,weight} rows.kind{bodyweight=2,food_entry=1} "
                             "weight_unit"),
        "settings": ("claude", "calorie_target_kcal"),
        "web edit": ("web", "entry_id kg kind=bodyweight"),  # the same edit, from the web
        "web settings": ("web", "calorie_target_kcal"),
    }


def test_a_name_spotter_does_not_know_is_counted_never_written(http):
    token = sign()
    tools_call(http, token, "log_bodyweight", {"kg": 80, MARKER: 1, "colour": "red"})
    assert last().tried == "kg other[2]"
    tools_call(http, token, "import_entries", {"rows": [{"kind": "bodyweight", "date": "2026-11-01", "weight": 80,
                                                          MARKER: 1, "entries": [{"description": "Oats", "kcal": 1,
                                                                                  "meal": "lunch", "colour": 1}]}],
                                               "weight_unit": "kg"})
    # One level deep: a list inside an item is its name only, so the name inside entries is not even counted.
    assert last().tried == "other[1] rows[1]{date,entries,kind,weight} rows.kind{bodyweight=1} weight_unit"


def test_a_long_tried_is_cut_at_a_whole_name():
    sent = {name: [{"kind": "food_entry"}] * 3 for name in sorted(calllog.KNOWN_NAMES)}
    text = calllog.tried("import_entries", sent)
    assert len(text) <= calllog.TRIED_MAX and text.endswith(" +more") and len(text) > calllog.TRIED_MAX - 60
    assert all(word.split("[")[0].split(".")[0] in calllog.KNOWN_NAMES for word in text.split(" ")[:-1])


def test_the_known_names_are_every_name_in_the_published_tool_schemas(make_client):
    client = make_client(owner_user_id=OWNER)
    tools = rpc(client, "tools/list", token=sign_for(OWNER)).json()["result"]["tools"]
    published = set()
    for tool in tools:
        schema = tool["inputSchema"]
        for obj in [schema, *schema.get("$defs", {}).values()]:
            published |= set(obj.get("properties", {}))
    assert len(tools) == 20 and published == calllog.KNOWN_NAMES


# ---------------------------------------------------------------- decisions 7 to 9: outcome, step, frame

def test_each_outcome(http, monkeypatch):
    token = sign()
    tools_call(http, token, "whoami")
    tools_call(http, token, "log_bodyweight", {"kg": 80})
    tools_call(http, token, "log_bodyweight", {"kg": 80})
    tools_call(http, token, "import_entries", {"rows": [{"kind": "bodyweight", "date": "2026-11-01", "weight": 80}],
                                               "weight_unit": "kg"})  # refused: no time zone yet
    tools_call(http, token, "update_settings", {"time_zone": "UTC"})
    tools_call(http, token, "import_entries", {"rows": [{"kind": "bodyweight", "date": today(), "weight": 80}],
                                               "weight_unit": "kg"})  # a preview
    monkeypatch.setattr(services, "get_trends", crash)
    tools_call(http, token, "get_trends")
    assert [(r.function, r.outcome) for r in rows()] == [
        ("whoami", "read"), ("log_bodyweight", "written"), ("log_bodyweight", "duplicate"),
        ("import_entries", "refused"), ("update_settings", "written"), ("import_entries", "preview"),
        ("get_trends", "failed")]


def test_a_write_that_landed_and_failed_in_its_reply_reads_written(http, monkeypatch):
    real = services.log_bodyweight

    def unsendable(*args):
        return {**real(*args), "summary": chr(0xD83D)}  # a lone surrogate: the reply cannot be sent

    monkeypatch.setattr(services, "log_bodyweight", unsendable)
    r, result = tools_call(http, sign(), "log_bodyweight", {"kg": 80})
    assert result["structuredContent"]["error"] == "internal_error"
    row = last()
    assert (row.ok, row.error_code, row.outcome, row.step) == (False, "internal_error", "written", "reply")
    assert re.fullmatch(r"mcp_server\.py:\d+ (call|_json_text)", row.frame)  # the reply, past the work
    with db.session_scope() as s:
        assert s.query(Bodyweight).count() == 1  # it was written


def test_each_step(make_client, monkeypatch, usda_reply):
    closed = make_client(signups_per_day=0)
    tools_call(closed, sign_for("user_closed"), "whoami")
    http = make_client(usda_api_key=KEY)
    token = sign()
    tools_call(http, token, "whoami")
    set_switch(last().user_id, "read")
    tools_call(http, token, "log_lifts", {"sets": squat(100)})
    set_switch(last().user_id, "log")
    tools_call(http, token, "log_lifts", {"sets": squat(100), "date": "soon"})
    usda_reply(socket.timeout("timed out"))
    tools_call(http, token, "log_food", {"entries": [{"usda_fdc_id": 173944, "grams": 100}]})
    monkeypatch.setattr(services, "log_measurement", lambda *a: {"error": "too_many_writes", "message": "m",
                                                                 "fix": "f", "wait_minutes": 1})
    tools_call(http, token, "log_measurement", {"kind": "waist", "value": 84})
    monkeypatch.setattr(services, "get_log", lambda *a, **k: (_ for _ in ()).throw(
        OperationalError("SELECT", {}, Exception("down"))))
    tools_call(http, token, "get_log")
    monkeypatch.setattr(services, "get_trends", crash)
    tools_call(http, token, "get_trends")
    assert [(r.error_code, r.step) for r in rows()] == [
        ("signups_closed", "account"), (None, None), ("not_allowed", "permission"), ("invalid_date", "check"),
        ("usda_unavailable", "lookup"), ("too_many_writes", "limit"), ("database_unavailable", "database"),
        ("internal_error", "crash")]
    assert all(r.frame is None for r in rows()[:-2]) and all(r.frame for r in rows()[-2:])


def test_a_crash_s_frame_is_the_innermost_line_in_app_and_never_its_message(http, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError(MARKER)

    monkeypatch.setattr(rules, "entry_date", broken)  # what log_bodyweight calls inside its own check
    tools_call(http, sign(), "log_bodyweight", {"kg": 80})
    frame = last().frame
    assert re.fullmatch(r"services/body\.py:\d+ \w+", frame) and MARKER not in frame


def test_a_failed_commit_shows_as_db_py_in_session_scope(http, monkeypatch):
    def commits_nothing(*args):
        with db.session_scope() as s:
            s.add(CallLog(function="x"))  # not null columns left empty: the commit fails

    monkeypatch.setattr(services, "log_bodyweight", commits_nothing)
    tools_call(http, sign(), "log_bodyweight", {"kg": 80})
    assert re.fullmatch(r"db\.py:\d+ session_scope", last().frame)


# ---------------------------------------------------------------- decision 10: notes from a worker thread

def test_a_note_set_in_the_worker_thread_reaches_the_row_of_a_call_that_went_on(make_client, usda_reply):
    http = make_client(usda_api_key=KEY)
    usda_reply(urllib.error.HTTPError("https://x", 429, "Too Many Requests", {}, None))
    r, result = tools_call(http, sign(), "find_food", {"query": "banana"})
    assert result["isError"] is False
    row = last()
    assert (row.ok, row.outcome, row.step, row.notes) == (True, "read", None, "usda=rate_limited")
    calllog.finish(row.id, 1, True, None, 1, notes={"usda": "x" * 200})
    assert last().notes == ("usda=" + "x" * 200)[:128]  # 128 characters at most
    calllog.note("usda", "x")  # outside a call: nothing to note on, and no error


# ---------------------------------------------------------------- decision 11: a web write that loses the database

def test_a_web_write_that_loses_the_database_is_database_unavailable_and_its_page_is_unchanged(client, monkeypatch):
    sign_in(client)

    def down(*args):
        raise OperationalError("INSERT", {}, Exception("down"))

    monkeypatch.setattr(services, "log_bodyweight", down)
    r = post(client, f"/day/{today()}/weigh-in", kg="80.5")
    assert r.status_code == 503 and "cannot reach its database" in r.text
    row = last()
    assert (row.error_code, row.step, row.outcome, row.caller) == ("database_unavailable", "database", "failed", "web")
    assert row.frame and row.request_id == r.headers["x-request-id"]


# ---------------------------------------------------------------- decisions 12 and 13: USDA says why

DID_NOT_ANSWER = "USDA did not answer."
USDA_FAILURES = [
    (urllib.error.HTTPError("https://x", 401, "Unauthorized", {}, None), "key_refused", "USDA refused Spotter's key."),
    (urllib.error.HTTPError("https://x", 403, "Forbidden", {}, None), "key_refused", "USDA refused Spotter's key."),
    (urllib.error.HTTPError("https://x", 429, "Too Many", {}, None), "rate_limited", "USDA is over its limit for now."),
    (urllib.error.HTTPError("https://x", 400, "Bad Request", {}, None), "bad_request", DID_NOT_ANSWER),
    (urllib.error.HTTPError("https://x", 503, "Unavailable", {}, None), "http_503", DID_NOT_ANSWER),
    (urllib.error.HTTPError("https://x", 302, "redirect refused", {}, None), "redirect", DID_NOT_ANSWER),
    (socket.timeout("timed out"), "no_answer", DID_NOT_ANSWER),
    (urllib.error.URLError("no route"), "no_answer", DID_NOT_ANSWER),
    (b"not json at all", "bad_reply", DID_NOT_ANSWER),
    ({"unexpected": True}, "bad_reply", DID_NOT_ANSWER),
]


@pytest.mark.parametrize("reply, reason, message", USDA_FAILURES, ids=[f[1] + str(n) for n, f in
                                                                       enumerate(USDA_FAILURES)])
def test_each_usda_reason_reaches_the_row_the_log_line_and_the_message(make_client, usda_reply, stderr, reply, reason,
                                                                        message):
    http = make_client(usda_api_key=KEY)
    usda_reply(reply)
    r, result = tools_call(http, sign(), "find_food", {"query": MARKER})
    note = result["structuredContent"]["usda_note"]
    assert note == message.removesuffix(".") + ": use a saved food or the person's own number."
    assert note in result["structuredContent"]["summary"]
    assert last().notes == f"usda={reason}"
    line = f"WARNING spotter.usda [{r.headers['x-request-id']}]: USDA lookup failed: {reason}\n"
    assert line in stderr.getvalue()
    for text in (stderr.getvalue(), str([getattr(last(), c.key) for c in CallLog.__table__.columns])):
        assert KEY not in text and MARKER not in text and "https://" not in text
    assert usda.unavailable(reason)["message"] == message


def test_a_usda_food_with_no_energy_value_is_no_energy(make_client, usda_reply, stderr):
    http = make_client(usda_api_key=KEY)
    usda_reply({"fdcId": 173944, "dataType": "SR Legacy", "description": "x", "foodNutrients": []})
    r, result = tools_call(http, sign(), "log_food", {"entries": [{"usda_fdc_id": 173944, "grams": 100}]})
    out = result["structuredContent"]
    assert (out["error"], out["message"]) == ("usda_unavailable", DID_NOT_ANSWER)
    assert (last().notes, last().step) == ("usda=no_energy", "lookup")
    assert "USDA lookup failed: no_energy" in stderr.getvalue()
    usda_reply(banana(fdc_id=1, shape="detail"))  # another food than the one asked for
    assert usda.food(173944)[1]["message"] == DID_NOT_ANSWER


def test_the_new_usda_texts_keep_the_text_rules():
    for reason in ("key_refused", "rate_limited"):
        message = usda.unavailable(reason)["message"]
        assert tr.check(message, tr.MESSAGE) == [], message
        assert tr.check(message.removesuffix(".") + ": use a saved food or the person's own number.", tr.SUMMARY) == []
    assert tr.check(mcp_server.OWNER_ONLY["message"], tr.MESSAGE) == []
    assert tr.check(mcp_server.OWNER_ONLY["fix"], tr.MESSAGE) == []


# ---------------------------------------------------------------- decision 14: 90 days

def plant(days_ago: float) -> int:
    with db.session_scope() as s:
        row = CallLog(function="whoami", started_at=db.utcnow() - timedelta(days=days_ago), ms=1, ok=True,
                      result_bytes=1)
        s.add(row)
        s.flush()
        return row.id


def ids() -> set[int]:
    return {r.id for r in rows()}


def test_the_sweep_deletes_a_row_91_days_old_keeps_one_89_days_old_and_runs_once_a_day(http, monkeypatch):
    monkeypatch.setattr(calllog, "_swept", None)  # as on the first call after the process starts
    old, kept = plant(91), plant(89)
    token = sign()
    tools_call(http, token, "whoami")
    assert old not in ids() and kept in ids()
    again = plant(91)
    tools_call(http, token, "whoami")
    assert again in ids()  # within 24 hours of the last sweep: none
    later = db.utcnow() + timedelta(hours=24, minutes=1)
    monkeypatch.setattr(db, "utcnow", lambda: later)
    tools_call(http, token, "whoami")
    assert again not in ids() and calllog.CALL_LOG_DAYS == 90


def test_a_failing_sweep_never_fails_a_call(http, monkeypatch, logs):
    monkeypatch.setattr(calllog, "_swept", None)

    def broken(*args):
        raise OperationalError("DELETE", {}, Exception(MARKER))

    monkeypatch.setattr(calllog, "delete", broken)
    r, result = tools_call(http, sign(), "log_bodyweight", {"kg": 80})
    assert result["isError"] is False and last().outcome == "written"  # the call's own row is complete
    assert "The call log could not be swept: OperationalError" in logs.text and MARKER not in logs.text


# ---------------------------------------------------------------- decisions 15 and 16: the owner

def test_owner_user_id_is_read_from_the_environment_and_unset_is_nobody():
    base = {"AUTHKIT_DOMAIN": "example.authkit.app", "MCP_RESOURCE_URL": RESOURCE}
    assert load_settings({**base, "OWNER_USER_ID": f" {OWNER} "}).owner_user_id == OWNER
    assert load_settings(base).owner_user_id is None
    assert load_settings({**base, "OWNER_USER_ID": " "}).owner_user_id is None


def test_only_the_owner_s_list_has_owner_calls_and_everyone_else_s_is_the_nineteen_as_before(make_client):
    tool_lists = {}
    for label, overrides, subject in (("owner", {"owner_user_id": OWNER}, OWNER),
                                      ("someone else", {"owner_user_id": OWNER}, "user_01OTHER"),
                                      ("unset", {}, OWNER)):
        client = make_client(**overrides)
        tool_lists[label] = rpc(client, "tools/list", token=sign_for(subject)).json()["result"]["tools"]
    assert [t["name"] for t in tool_lists["owner"]] == [*FUNCTIONS, "owner_calls"]
    for label in ("someone else", "unset"):
        assert [t["name"] for t in tool_lists[label]] == FUNCTIONS
        assert json.dumps(tool_lists[label]) == json.dumps(tool_lists["owner"][:-1])  # byte for byte
    assert web.FUNCTION_COUNT == len(FUNCTIONS) == 19  # the landing's count is unchanged


@pytest.mark.parametrize("overrides, subject", [({"owner_user_id": OWNER}, "user_01OTHER"), ({}, OWNER)],
                         ids=["someone else", "unset"])
def test_anyone_but_the_owner_calling_owner_calls_is_not_allowed(make_client, monkeypatch, overrides, subject):
    monkeypatch.setattr(services, "owner_calls", crash)  # never reached
    client = make_client(**overrides)
    r, result = tools_call(client, sign_for(subject), "owner_calls")
    assert result["isError"] is True and result["structuredContent"] == {
        "error": "not_allowed", "message": "This function is for Spotter's owner.", "fix": "Nothing to retry."}
    assert (last().function, last().step, last().outcome) == ("owner_calls", "permission", "refused")


def test_the_owner_reads_lines_a_person_s_calls_and_one_request(make_client, make_user, monkeypatch):
    client = make_client(owner_user_id=OWNER)
    me = make_user(subject=OWNER, email="owner@example.com", name="Owner")
    asha = make_user(subject="user_01ASHA", email="Asha@Example.com", name="Asha")
    make_user(subject="user_01BEN", email="ben@example.com", name="Ben")
    make_user(subject="user_01BEN2", email="BEN@example.com", name="Ben again")
    ben = sign_for("user_01BEN")
    first, _ = tools_call(client, sign_for("user_01ASHA"), "log_bodyweight", {"kg": 80})
    monkeypatch.setattr(services, "get_trends", crash)
    tools_call(client, sign_for("user_01ASHA"), "get_trends")
    tools_call(client, ben, "whoami")
    with db.session_scope() as s:
        s.execute(insert(CallLog).values(function="whoami", started_at=db.utcnow() - timedelta(hours=30), ms=1, ok=True,
                                         result_bytes=1, user_id=asha.id))
    owner = sign_for(OWNER)

    def read(**arguments):
        r, result = tools_call(client, owner, "owner_calls", arguments)
        assert result["isError"] is False, result
        return result["structuredContent"]

    lines = read()
    assert lines["hours"] == 24 and lines["calls"] is None
    by_id = {line["account_id"]: line for line in lines["accounts"]}
    assert (by_id[asha.id]["calls"], by_id[asha.id]["failed_calls"], by_id[asha.id]["email"]) == (2, 1,
                                                                                                "Asha@Example.com")
    assert ID.fullmatch(first.headers["x-request-id"]) and lines["accounts"][0]["last_call"].endswith("Z")
    calls = read(email="ASHA@EXAMPLE.COM")["calls"]
    assert [(c["function"], c["outcome"], c["step"]) for c in calls] == [("get_trends", "failed", "crash"),
                                                                       ("log_bodyweight", "written", None)]
    assert calls[1]["tried"] == "kg" and calls[1]["caller"] == "claude" and set(calls[1]) == {
        "started_at", "caller", "function", "ok", "error", "outcome", "step", "frame", "tried", "notes", "ms",
        "result_bytes", "request_id"}
    assert read(account_id=asha.id) == read(email="asha@example.com")
    assert len(read(account_id=asha.id, hours=48)["calls"]) == 3  # the call 30 hours back is in a 48-hour window
    assert len(read(account_id=asha.id, limit=1)["calls"]) == 1
    assert [c["function"] for c in read(account_id=asha.id, failed_only=True)["calls"]] == ["get_trends"]
    twice = read(email="ben@EXAMPLE.com")
    assert twice["calls"] is None and {a["name"] for a in twice["accounts"]} == {"Ben", "Ben again"}
    assert read(email="nobody@example.com") == {"hours": 24, "accounts": [], "calls": None}
    one = read(request_id=first.headers["x-request-id"])
    assert [(c["function"], c["request_id"]) for c in one["calls"]] == [("log_bodyweight",
                                                                       first.headers["x-request-id"])]
    assert one["hours"] is None  # no window applies to a request id (fix round, item 5)
    # Failed: Asha's crash only. The owner's own call, still running while it reads, is not (fix round, item 1).
    assert all(line["account_id"] is not None for line in read()["accounts"])  # no call without an account here
    assert [line["account_id"] for line in read(failed_only=True)["accounts"]] == [asha.id]
    for out_of_range in ({"hours": 0}, {"hours": 2161}, {"limit": 0}, {"limit": 201}):
        assert tools_call(client, owner, "owner_calls", out_of_range)[1]["isError"] is True
    assert last().function == "owner_calls" and last().tried == "failed_only" and last().caller == "claude"


def test_the_owner_s_own_running_call_is_in_neither_the_failed_list_nor_the_failed_count(make_client, make_user):
    client = make_client(owner_user_id=OWNER)
    me = make_user(subject=OWNER, email="owner@example.com", name="Owner")
    owner = sign_for(OWNER)
    mine = tools_call(client, owner, "owner_calls", {"account_id": me.id, "failed_only": True})[1]
    assert mine["structuredContent"]["calls"] == []  # its own row was in_progress, not ok, while it read
    lines = tools_call(client, owner, "owner_calls")[1]["structuredContent"]["accounts"]
    assert [(line["account_id"], line["calls"], line["failed_calls"]) for line in lines] == [(me.id, 2, 0)]
    assert tools_call(client, owner, "owner_calls", {"failed_only": True})[1]["structuredContent"]["accounts"] == []


def test_calls_with_no_account_have_their_own_line_and_their_own_list(make_client, make_user):
    client = make_client(owner_user_id=OWNER, signups_per_day=1)
    me = make_user(subject=OWNER, email="owner@example.com", name="Owner")  # the day's one new account is taken
    refused, _ = tools_call(client, sign_for("user_01NEW"), "whoami")  # signups_closed: a row with no account
    with db.session_scope() as s:  # a deleted account's call, and one outside the window
        s.execute(insert(CallLog).values(function="log_food", started_at=db.utcnow() - timedelta(hours=1), ms=1,
                                         ok=True, result_bytes=1))
        s.execute(insert(CallLog).values(function="whoami", started_at=db.utcnow() - timedelta(hours=30), ms=1,
                                         ok=True, result_bytes=1))
    owner = sign_for(OWNER)
    lines = tools_call(client, owner, "owner_calls")[1]["structuredContent"]["accounts"]
    nobody = [line for line in lines if line["account_id"] is None]
    assert [(n["email"], n["name"], n["calls"], n["failed_calls"]) for n in nobody] == [(None, None, 2, 1)]
    assert nobody[0]["last_call"].endswith("Z") and {line["account_id"] for line in lines} == {me.id, None}
    out = tools_call(client, owner, "owner_calls", {"no_account": True, "account_id": me.id})[1]["structuredContent"]
    assert out["accounts"] is None and [(c["function"], c["error"], c["request_id"]) for c in out["calls"]] == [
        ("whoami", "signups_closed", refused.headers["x-request-id"]), ("log_food", None, None)]  # newest first
    assert len(tools_call(client, owner, "owner_calls", {"no_account": True, "hours": 48})[1]
               ["structuredContent"]["calls"]) == 3
    assert last().tried == "hours no_account"
    failed = tools_call(client, owner, "owner_calls", {"failed_only": True})[1]["structuredContent"]["accounts"]
    assert [(line["account_id"], line["failed_calls"]) for line in failed] == [(None, 1)]


# ---------------------------------------------------------------- fix round: every crash has its line and its id

def test_a_web_write_s_crash_logs_one_line_with_its_id_and_never_its_message(web_client, monkeypatch, stderr):
    client = web_client(raise_server_exceptions=False)
    sign_in(client)
    monkeypatch.setattr(services, "log_bodyweight", crash)
    r = post(client, f"/day/{today()}/weigh-in", kg="80.5")
    assert r.status_code == 500  # the designed 500 page, as before
    request_id = r.headers["x-request-id"]
    assert (last().request_id, last().step) == (request_id, "crash")
    assert f"ERROR spotter [{request_id}]: Web log_bodyweight crashed: KeyError" in stderr.getvalue()
    assert MARKER not in stderr.getvalue()


def test_an_unhandled_500_and_a_413_carry_an_id(monkeypatch):
    from starlette.testclient import TestClient

    from app.main import create_app

    from .conftest import settings

    def broken(request):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(main, "health", broken)  # /health has no page layer: Starlette's own error answers
    with TestClient(create_app(settings(), fetch=None), raise_server_exceptions=False) as plain:
        r = plain.get("/health")
        too_large = plain.post("/health", content=b"x" * (MAX_BODY_BYTES + 1))
    assert r.status_code == 500 and ID.fullmatch(r.headers["x-request-id"]) and MARKER not in r.text
    assert too_large.status_code == 413 and ID.fullmatch(too_large.headers["x-request-id"])


# ---------------------------------------------------------------- never a value: the marker in the new columns

def test_a_marker_as_a_value_or_as_a_name_reaches_no_trace_column_and_no_log_line(make_client, logs, monkeypatch,
                                                                                  usda_reply):
    client = make_client(auth_debug=True, usda_api_key=KEY)
    usda_reply(urllib.error.HTTPError(f"https://x/{MARKER}?api_key={KEY}", 429, MARKER, {}, None))
    token = sign()
    for name, arguments in MARKED_CALLS:  # the marker in every string argument, as tests/test_calllog.py sends it
        tools_call(client, token, name, arguments)
    tools_call(client, token, "log_bodyweight", {"kg": 80, MARKER: MARKER})  # an unknown argument
    tools_call(client, token, "import_entries", {"rows": [{"kind": "bodyweight", "date": MARKER, MARKER: MARKER}],
                                                 "weight_unit": "kg"})
    monkeypatch.setattr(services, "get_progress", crash)  # a crash whose message is the marker
    tools_call(client, token, "get_progress")
    logged = rows()
    for name in TRACE:
        assert any(getattr(row, name) for row in logged), name  # every new column is filled somewhere
    for row in logged:
        for column in CallLog.__table__.columns:
            assert MARKER not in str(getattr(row, column.key)), column.name
    assert MARKER not in logs.text and all(MARKER not in str(r.args) for r in logs.records)


def test_a_marker_as_a_form_field_s_name_reaches_no_column_and_no_log_line(client, logs):
    sign_in(client)
    r = post(client, f"/day/{today()}/weigh-in", kg="80.5", **{MARKER: MARKER})
    assert r.status_code == 303
    row = last()
    assert (row.caller, row.tried) == ("web", "date kg")
    assert all(MARKER not in str(getattr(row, c.key)) for c in CallLog.__table__.columns)
    assert MARKER not in logs.text
