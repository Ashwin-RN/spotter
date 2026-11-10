"""The assembled app through its real endpoints: /health, /mcp, the metadata route, then the seven
functions and two resources of stage 2 (stage 1 contract items 1 to 4, 6, 9 to 13, 15; stage 2's MCP surface)."""

import asyncio
import json
import logging
import re
import threading
import time
from datetime import date, timedelta
from typing import Any

import jwt
import pytest
from sqlalchemy import func, select
from starlette.testclient import TestClient

from app import db, links, mcp_server, services
from app.config import Settings
from app.main import create_app
from app.mcp_schemas import RESULTS
from app.mcp_server import CAN_AND_CANT, REQUIRED_SCOPES, SCOPES
from app.models import Bodyweight, CallLog, LiftSession, User

from .conftest import DOMAIN, HEADERS, ISSUER, OTHER_KEY, RESOURCE, SIGNING_KEY, TEST_DIR, claims, rpc, sign, whoami
from .test_auth import hs256_with_public_key, unsigned

METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"
SCOPE_PARAM = re.compile(r'(?:^Bearer |, )scope=')  # an RFC 6750 scope= parameter, not "insufficient_scope"


# ---------------------------------------------------------------- item 1: health

def test_health_needs_no_sign_in(http, provider):
    r = http.get("/health")
    assert r.status_code == 200 and r.text == "OK"
    assert provider.calls == []  # touches nothing else


# ---------------------------------------------------------------- items 2 to 4: finding the sign-in

def test_no_token_gets_401_pointing_at_the_metadata(http):
    r = rpc(http, "initialize")
    assert r.status_code == 401
    header = r.headers["www-authenticate"]
    assert header.startswith("Bearer ")
    assert re.search(r'resource_metadata="([^"]+)"', header).group(1) == \
        "https://spotter.example.app" + METADATA_PATH


def test_the_metadata_names_this_resource_and_the_provider(http):
    r = http.get(METADATA_PATH)
    assert r.status_code == 200
    body = r.json()
    assert body["resource"] == RESOURCE  # character for character
    assert body["authorization_servers"] == [ISSUER]
    assert '"resource":"https://spotter.example.app/mcp"' in r.text


def test_the_metadata_advertises_the_four_scopes(http):
    assert SCOPES == ["openid", "profile", "email", "offline_access"]
    assert http.get(METADATA_PATH).json()["scopes_supported"] == SCOPES


def test_the_401_does_not_name_scopes_of_its_own(http):
    header = rpc(http, "initialize").headers["www-authenticate"]
    assert SCOPE_PARAM.search(header) is None  # so a client asks for scopes_supported: all four


def test_a_trailing_slash_resource_is_published_as_written(make_client):
    client = make_client(mcp_resource_url=RESOURCE + "/")
    header = rpc(client, "initialize").headers["www-authenticate"]
    url = re.search(r'resource_metadata="([^"]+)"', header).group(1)
    assert url == "https://spotter.example.app" + METADATA_PATH + "/"
    body = client.get(METADATA_PATH + "/").json()
    assert body["resource"] == RESOURCE + "/" and body["scopes_supported"] == SCOPES


# ---------------------------------------------------------------- scopes

@pytest.mark.parametrize("scope", ["profile email offline_access", "", None],
                         ids=["no openid", "empty scope", "no scope claim"])
def test_a_token_without_openid_gets_403_pointing_at_the_metadata(http, scope):
    r = rpc(http, "tools/call", {"name": "whoami", "arguments": {}}, token=sign(claims(scope=scope)))
    assert r.status_code == 403
    header = r.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in header
    assert f'resource_metadata="https://spotter.example.app{METADATA_PATH}"' in header
    assert SCOPE_PARAM.search(header) is None
    assert r.json()["error"] == "insufficient_scope"


def test_a_token_with_the_four_scopes_gets_through(http):
    who = whoami(http, sign(claims(scope="email offline_access openid profile")))
    assert who["sign_in"]["scopes"] == ["email", "offline_access", "openid", "profile"]


def test_only_openid_is_required(http):
    assert REQUIRED_SCOPES == ["openid"]
    assert whoami(http, sign(claims(scope="openid")))["sign_in"]["scopes"] == ["openid"]


def test_forged_host_headers_do_not_change_the_resource(http):
    forged = {"Host": "evil.example", "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http"}
    body = http.get(METADATA_PATH, headers=forged).json()
    assert body["resource"] == RESOURCE and body["authorization_servers"] == [ISSUER]
    header = http.post("/mcp", headers={**HEADERS, **forged}, json={}).headers["www-authenticate"]
    assert "evil.example" not in header and "https://spotter.example.app" + METADATA_PATH in header


# ---------------------------------------------------------------- item 6: refused at the endpoint

@pytest.mark.parametrize("token", [
    sign(claims(aud="https://other.example/mcp")),
    sign(claims(iss="https://evil.authkit.app")),
    sign(claims(exp=int(time.time()) - 60, iat=int(time.time()) - 3600)),
    sign(claims(exp=int(time.time()) - 10, iat=int(time.time()) - 3600)),  # inside the iat skew, still refused
    sign(key=OTHER_KEY, kid="key-1"),
    unsigned({"alg": "none", "typ": "JWT", "kid": SIGNING_KEY.kid}, claims()),
    jwt.encode(claims(), "a-shared-secret-long-enough-for-hs256-32b", algorithm="HS256",
               headers={"kid": SIGNING_KEY.kid}),
    hs256_with_public_key(),
    sign(claims(sub="")),
    sign(claims(exp=str(int(time.time()) + 600))),
    "not-a-jwt",
], ids=["another audience", "another issuer", "expired", "expired seconds ago", "bad signature", "alg none",
        "HS256 with a shared secret", "HS256 with the public key as secret", "empty subject", "exp as a string",
        "not a JWT"])
def test_bad_tokens_get_401(http, token):
    r = rpc(http, "tools/call", {"name": "whoami", "arguments": {}}, token=token)
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]


# ---------------------------------------------------------------- items 9 to 11: whoami

def test_whoami_names_the_person_from_the_token(http, provider):
    payload = claims(email="asha@example.com", name="Asha Test")
    who = whoami(http, sign(payload))
    row = one_user()
    assert (who["user_id"], who["account_id"]) == ("user_01TEST", row.id)
    assert (who["email"], who["name"]) == ("asha@example.com", "Asha Test")
    assert who["sign_in"] == {
        "client_id": "client_01TEST",
        "scopes": ["openid", "profile", "email"],
        "issuer": ISSUER,
        "expires_at": payload["exp"],
    }
    assert provider.count("/oauth2/userinfo") == 0  # the token said it all


def test_whoami_asks_userinfo_when_the_token_does_not_say(http, provider):
    token = sign()
    who = whoami(http, token)
    assert (who["email"], who["name"]) == ("asha@example.com", "Asha Test")
    sent = [h for u, h in zip(provider.calls, provider.sent_headers) if h]
    assert sent == [{"Authorization": f"Bearer {token}"}]
    assert [u for u, h in zip(provider.calls, provider.sent_headers) if h] == [f"{ISSUER}/oauth2/userinfo"]


def test_whoami_gives_nulls_when_userinfo_fails(http, provider):
    provider.userinfo = OSError("down")
    who = whoami(http, sign())
    assert who["email"] is None and who["name"] is None and who["user_id"] == "user_01TEST"


def test_whoami_never_sends_the_token_to_another_host(http, provider):
    provider.openid["userinfo_endpoint"] = "https://evil.example/userinfo"
    who = whoami(http, sign())
    assert who["email"] is None and who["name"] is None
    assert all(not h for h in provider.sent_headers)


# ---------------------------------------------------------------- item 12: failing closed

UNCONFIGURED = [
    Settings(None, None),
    Settings(DOMAIN, None),
    Settings(None, RESOURCE),
    Settings("https://" + DOMAIN, RESOURCE),
]


@pytest.mark.parametrize("settings", UNCONFIGURED, ids=["both missing", "no resource", "no domain", "bad domain"])
def test_without_settings_the_app_starts_and_mcp_is_503(settings, provider):
    with TestClient(create_app(settings, fetch=provider.fetch)) as client:
        assert client.get("/health").text == "OK"
        for token in (None, sign()):
            r = rpc(client, "tools/call", {"name": "whoami", "arguments": {}}, token=token)
            assert r.status_code == 503
            assert set(r.json()) == {"error", "message", "fix"}
        assert client.get("/mcp").status_code == 503
        assert client.get(METADATA_PATH).status_code == 404
    assert provider.calls == []  # no token was even looked at


def test_the_module_app_builds_from_the_environment(monkeypatch):
    import importlib

    import app.main

    monkeypatch.delenv("AUTHKIT_DOMAIN", raising=False)
    monkeypatch.delenv("MCP_RESOURCE_URL", raising=False)
    module = importlib.reload(app.main)
    try:
        client = TestClient(module.app)
        assert client.get("/health").status_code == 200 and client.post("/mcp").status_code == 503
    finally:
        importlib.reload(app.main)


# ---------------------------------------------------------------- item 13: logs

def test_a_verified_debug_call_never_logs_the_token(make_client, logs):
    client = make_client(auth_debug=True)
    token = sign()
    whoami(client, token)
    rpc(client, "tools/call", {"name": "whoami", "arguments": {}}, token=token + "x")  # a refused one too
    text = logs.text
    assert "token verified" in text  # debug was on
    assert token not in text
    assert token.split(".")[2] not in text  # not even the signature
    assert "Bearer " not in text and "authorization" not in text.lower()


# ---------------------------------------------------------------- item 15: transport

def test_calls_are_stateless_and_answered_in_json(http):
    token = sign()
    init = rpc(http, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "0"}}, token=token)
    assert init.status_code == 200
    assert init.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in init.headers
    assert init.json()["result"]["serverInfo"]["name"] == "Spotter"
    # A call with no initialize and no session id stands alone.
    assert whoami(http, token)["user_id"] == "user_01TEST"


def test_spotter_logs_through_its_own_handler_added_once(provider):
    create_app(Settings(None, None), fetch=provider.fetch)
    create_app(Settings(None, None), fetch=provider.fetch)
    logger = logging.getLogger("spotter")
    own = [h for h in logger.handlers if type(h) is logging.StreamHandler]
    assert len(own) == 1
    assert logger.level == logging.INFO and logger.propagate is False


# ================================================================ stage 2: the seven functions

FUNCTIONS = ["whoami", "list_exercises", "log_lifts", "log_bodyweight", "set_goal", "get_progress", "update_settings",
             "get_log", "log_measurement", "edit_entry", "restore_entry", "delete_entry"]
READS = {"whoami", "list_exercises", "get_progress", "get_log"}
READ_HINTS = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
WRITE_HINTS = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
SETTING_HINTS = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
SETTINGS = {"update_settings"}
DELETE_HINTS = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}
DELETES = {"delete_entry"}


def wire_fields(model) -> set[str]:
    """A result model's top-level keys as they appear in a result (get_log's `from` is an alias)."""
    return {field.alias or name for name, field in model.model_fields.items()}


def compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def call(client: TestClient, token: str, name: str, arguments: dict | None = None) -> dict:
    """The raw tools/call result: content, structuredContent, isError."""
    r = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, token=token)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def ok(client: TestClient, token: str, name: str, arguments: dict | None = None) -> dict:
    result = call(client, token, name, arguments)
    assert result["isError"] is False, result
    return result["structuredContent"]


def failed(client: TestClient, token: str, name: str, arguments: dict | None = None) -> dict:
    result = call(client, token, name, arguments)
    assert result["isError"] is True, result
    return result["structuredContent"]


def tool_list(client: TestClient, token: str) -> list[dict]:
    return rpc(client, "tools/list", token=token).json()["result"]["tools"]


def one_user() -> User:
    with db.session_scope() as session:
        rows = session.scalars(select(User)).all()
    assert len(rows) == 1
    return rows[0]


def sessions_logged() -> int:
    with db.session_scope() as session:
        return session.scalar(select(func.count()).select_from(LiftSession))


def squat(weight: float, reps: int = 5, sets: int = 3) -> list[dict]:
    return [{"exercise": "Back Squat", "weight_kg": weight, "reps": reps} for _ in range(sets)]


def every_result(client: TestClient, token: str) -> dict[str, list[dict]]:
    """One of every result shape each function has, through MCP."""
    seen: dict[str, list[dict]] = {name: [] for name in FUNCTIONS}

    def keep(name: str, arguments: dict | None = None) -> dict:
        result = call(client, token, name, arguments)
        assert result["isError"] is False, result
        assert [c["type"] for c in result["content"]] == ["text"]
        assert result["content"][0]["text"] == compact(result["structuredContent"])  # the same dict, as JSON
        seen[name].append(result["structuredContent"])
        return result["structuredContent"]

    who = keep("whoami")  # no time zone yet: carries time_zone_note
    today = date.fromisoformat(who["today"])
    keep("list_exercises")
    keep("list_exercises", {"kind": "lift", "category": "push"})
    keep("get_progress")  # nothing logged: note
    keep("get_progress", {"exercise": "Back Squat"})  # nothing logged: note
    for days_ago, weight in ((21, 100), (14, 102.5), (7, 105)):
        keep("log_lifts", {"sets": squat(weight), "date": (today - timedelta(days=days_ago)).isoformat()})
    sets = squat(110) + [{"exercise": "Bench Press", "weight_kg": 60, "reps": 8}]
    keep("log_lifts", {"sets": sets, "notes": "Felt strong"})  # new bests
    keep("log_lifts", {"sets": sets, "notes": "Felt strong"})  # duplicate: note
    keep("log_bodyweight", {"kg": 82, "date": (today - timedelta(days=30)).isoformat()})
    keep("log_bodyweight", {"kg": 80.4})
    keep("log_bodyweight", {"kg": 80.4})  # duplicate: note
    keep("log_bodyweight", {"kg": 80.1})  # replaces today's: replaced and note
    target_date = (today + timedelta(days=60)).isoformat()
    lift_goal = keep("set_goal", {"kind": "lift", "exercise": "Back Squat", "target_value": 140,
                                  "target_date": target_date, "text": "A big squat"})
    keep("set_goal", {"kind": "bodyweight", "target_value": 76, "target_date": target_date})
    keep("get_progress", {"exercise": "Back Squat"})
    keep("get_progress")
    keep("set_goal", {"goal_id": lift_goal["id"], "status": "hit"})  # closed: note
    keep("update_settings", {"time_zone": "Asia/Kolkata"})  # changed
    keep("update_settings", {"time_zone": "Asia/Kolkata"})  # already set: nothing changed
    keep("whoami")  # a zone is set: no note
    first = keep("log_measurement", {"kind": "waist", "value": 84})
    keep("log_measurement", {"kind": "waist", "value": 84})  # duplicate: note
    second = keep("log_measurement", {"kind": "Waist", "value": 83.5})  # replaces: replaced and note
    keep("get_log")
    log = keep("get_log", {"days": 14})
    session_id = next(s["id"] for day in log["days"] for s in day["sessions"])
    keep("edit_entry", {"kind": "lift_session", "entry_id": session_id, "notes": "Edited"})  # a session: note
    keep("edit_entry", {"kind": "measurement", "entry_id": second["id"], "value": 83})
    set_switch(who["account_id"], "log_delete")
    keep("delete_entry", {"kind": "measurement", "entry_id": second["id"]})
    keep("restore_entry", {"kind": "measurement", "entry_id": second["id"]})  # nothing in its place
    keep("restore_entry", {"kind": "measurement", "entry_id": first["id"]})  # swaps: replaced and note
    return seen


def test_the_function_list_is_exactly_the_twelve(http):
    tools = tool_list(http, sign())
    assert [t["name"] for t in tools] == FUNCTIONS
    for tool in tools:
        assert tool["title"] and tool["description"], tool["name"]
        hints = (READ_HINTS if tool["name"] in READS else SETTING_HINTS if tool["name"] in SETTINGS
                 else DELETE_HINTS if tool["name"] in DELETES else WRITE_HINTS)
        assert tool["annotations"] == hints, tool["name"]
        schema = tool["inputSchema"]
        for obj in [schema, *schema.get("$defs", {}).values()]:
            for prop, spec in obj.get("properties", {}).items():
                assert spec.get("description"), f"{tool['name']}.{prop} has no description"
        out = tool["outputSchema"]
        assert out["type"] == "object" and out["properties"] and out["additionalProperties"] is False, tool["name"]
        assert set(out["properties"]) == wire_fields(RESULTS[tool["name"]])


def test_every_result_field_is_described(http):
    for tool in tool_list(http, sign()):
        out = tool["outputSchema"]
        for obj in [out, *out.get("$defs", {}).values()]:
            for prop, spec in obj.get("properties", {}).items():
                assert spec.get("description"), f"{tool['name']} result field {prop} has no description"


def test_the_arguments_are_typed_parameters(http):
    schemas = {t["name"]: t["inputSchema"] for t in tool_list(http, sign())}
    assert list(schemas["log_lifts"]["properties"]) == ["sets", "date", "notes", "allow_repeat"]
    assert schemas["log_lifts"]["required"] == ["sets"]
    assert list(schemas["log_bodyweight"]["properties"]) == ["kg", "date", "allow_repeat"]
    assert set(schemas["set_goal"]["properties"]) == {"kind", "exercise", "target_value", "target_date", "text",
                                                      "start_value", "goal_id", "status"}
    assert list(schemas["get_progress"]["properties"]) == ["exercise"]
    assert list(schemas["update_settings"]["properties"]) == ["time_zone"]
    assert list(schemas["list_exercises"]["properties"]) == ["kind", "category"]
    assert schemas["whoami"]["properties"] == {}
    one_set = schemas["log_lifts"]["$defs"]["SetIn"]
    assert one_set["properties"]["reps"]["type"] == "integer"
    assert set(one_set["required"]) == {"exercise", "weight_kg", "reps"}


def test_every_real_result_validates_and_its_keys_are_the_models(http):
    seen = every_result(http, sign())
    for name, results in seen.items():
        model = RESULTS[name]
        keys: set[str] = set()
        for result in results:
            model.model_validate(result)
            keys |= set(result)
        assert keys == wire_fields(model), name  # every field is used, and no key lacks a field


def test_a_service_field_with_no_schema_entry_fails_the_check():
    with pytest.raises(ValueError):
        RESULTS["list_exercises"].model_validate({"exercises": [], "surprise": 1})


def test_results_are_the_services_own_dicts(http):
    token = sign()
    ok(http, token, "log_lifts", {"sets": squat(100)})
    account = one_user().id
    assert ok(http, token, "list_exercises") == services.list_exercises()
    assert ok(http, token, "get_progress") == services.get_progress(account)
    assert ok(http, token, "get_progress", {"exercise": "back squat"}) == services.get_progress(account, "back squat")
    who = ok(http, token, "whoami")
    assert {k: v for k, v in who.items() if k != "sign_in"} == services.whoami(account)


def test_an_error_is_flagged_and_carries_the_fix_as_structure_and_text(http):
    result = call(http, sign(), "log_lifts", {"sets": [{"exercise": "Back Squatt", "weight_kg": 100, "reps": 5}]})
    assert result["isError"] is True
    error = result["structuredContent"]
    assert error["error"] == "unknown_exercise" and error["message"] and error["fix"]
    assert error["closest"][0] == "Back Squat" and error["errors"]  # extra keys pass through
    assert result["content"][0]["text"] == compact(error)
    assert sessions_logged() == 0


def test_a_list_exercises_error_is_flagged(http):
    error = failed(http, sign(), "list_exercises", {"kind": "swimming"})
    assert error["error"] == "invalid_field" and "lift" in error["fix"]


def test_log_lifts_twice_is_a_duplicate_and_allow_repeat_logs_it_again(http):
    token = sign()
    first = ok(http, token, "log_lifts", {"sets": squat(100), "notes": "Easy"})
    again = ok(http, token, "log_lifts", {"sets": squat(100), "notes": "Easy"})
    assert first["duplicate"] is False and again["duplicate"] is True
    assert again["id"] == first["id"] and "nothing was written" in again["note"]
    assert sessions_logged() == 1
    repeat = ok(http, token, "log_lifts", {"sets": squat(100), "notes": "Easy", "allow_repeat": True})
    assert repeat["duplicate"] is False and repeat["id"] != first["id"]
    assert sessions_logged() == 2


@pytest.mark.parametrize("name, arguments, field", [
    ("log_bodyweight", {"kg": "heavy"}, "kg"),
    ("log_lifts", {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": "five"}]}, "sets.0.reps"),
    ("log_lifts", {"sets": "not a list"}, "sets"),
    ("set_goal", {"goal_id": "twelve", "status": "dropped"}, "goal_id"),
    ("log_lifts", {"sets": squat(100), "allow_repeat": "perhaps"}, "allow_repeat"),
], ids=["kg as text", "reps as text", "sets not a list", "goal_id as text", "allow_repeat not a bool"])
def test_a_wrong_typed_argument_is_a_tool_error_naming_the_field(http, name, arguments, field):
    result = call(http, sign(), name, arguments)
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert re.search(rf"^{re.escape(field)}$", text, re.MULTILINE), text
    assert sessions_logged() == 0


def test_whoami_user_id_is_the_tokens_sub_and_account_id_is_the_row(http):
    who = whoami(http, sign(claims(sub="user_01OTHER")))
    row = one_user()
    assert who["user_id"] == "user_01OTHER" == row.subject and who["account_id"] == row.id
    assert who["claude_can"] == "log"


def test_whoami_notes_the_time_zone_until_one_is_set(http):
    token = sign()
    first = whoami(http, token)
    assert first["time_zone"] is None and "time zone" in first["time_zone_note"]
    services.update_settings(first["account_id"], {"time_zone": "Europe/London"}, "web")
    after = whoami(http, token)
    assert after["time_zone"] == "Europe/London" and "time_zone_note" not in after


# ---------------------------------------------------------------- userinfo

def test_userinfo_is_asked_when_the_row_lacks_email_or_name_and_stored(http, provider):
    whoami(http, sign())
    assert provider.count("/oauth2/userinfo") == 1
    row = one_user()
    assert (row.email, row.name) == ("asha@example.com", "Asha Test")


def test_a_complete_row_is_never_looked_up(http, provider, make_user):
    make_user(subject="user_01TEST", email="kept@example.com", name="Kept Name")
    for _ in range(3):
        who = whoami(http, sign())
    assert provider.count("/oauth2/userinfo") == 0
    assert (who["email"], who["name"]) == ("kept@example.com", "Kept Name")


def test_once_stored_userinfo_is_not_asked_again(http, provider, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(mcp_server, "clock", lambda: now[0])
    token = sign()
    ok(http, token, "whoami")
    now[0] += mcp_server.LOOKUP_EVERY * 10  # long after the window: the row is complete, so still no lookup
    for name in ("whoami", "list_exercises", "get_progress"):
        ok(http, token, name)
    assert provider.count("/oauth2/userinfo") == 1


def test_a_failing_userinfo_is_asked_once_within_the_window(http, provider, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(mcp_server, "clock", lambda: now[0])
    provider.userinfo = OSError("down")
    token = sign()
    for _ in range(3):
        whoami(http, token)
    assert provider.count("/oauth2/userinfo") == 1
    now[0] += mcp_server.LOOKUP_EVERY - 1
    whoami(http, token)
    assert provider.count("/oauth2/userinfo") == 1
    now[0] += 2  # the window has passed
    whoami(http, token)
    assert provider.count("/oauth2/userinfo") == 2


def test_a_partial_answer_is_stored_and_the_rest_waits_for_the_window(http, provider):
    provider.userinfo = {"sub": "user_01TEST", "email": "only@example.com"}
    whoami(http, sign())
    whoami(http, sign())
    assert provider.count("/oauth2/userinfo") == 1
    row = one_user()
    assert (row.email, row.name) == ("only@example.com", None)


def test_the_lookup_memo_is_bounded():
    memo = mcp_server.LookupMemo(every=600, size=3)
    assert all(memo.should_ask(f"user_{i}") for i in range(5))
    assert len(memo._asked) == 3 and "user_0" not in memo._asked
    assert memo.should_ask("user_0") is True and memo.should_ask("user_4") is False


# ---------------------------------------------------------------- identity and database errors

def test_signups_closed_is_the_calls_error(make_client):
    client = make_client(signups_per_day=0)
    error = failed(client, sign(), "whoami")
    assert error["error"] == "signups_closed" and error["fix"]
    assert failed(client, sign(), "log_lifts", {"sets": squat(100)})["error"] == "signups_closed"


def test_a_database_that_cannot_be_reached_is_database_unavailable(make_client, logs):
    missing = TEST_DIR / "absent-mcp" / "none.db"
    client = make_client(database_url=f"sqlite:///{missing.as_posix()}")
    try:
        result = call(client, sign(), "whoami")
    finally:
        db.dispose()
    assert result["isError"] is True
    assert result["structuredContent"]["error"] == "database_unavailable" and result["structuredContent"]["fix"]
    assert "OperationalError" in logs.text and "call log could not be written" in logs.text
    assert not missing.parent.exists()


# ---------------------------------------------------------------- nothing blocks the event loop

def test_no_database_session_is_opened_on_the_event_loop(http, monkeypatch):
    opened: list[tuple[int, bool]] = []
    loop_threads: set[int] = set()
    real_new_session, real_token = db.new_session, mcp_server.get_access_token

    def recording_session():
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        opened.append((threading.get_ident(), on_loop))
        return real_new_session()

    def recording_token():
        loop_threads.add(threading.get_ident())  # the wrapper reads the token on the loop
        return real_token()

    monkeypatch.setattr(db, "new_session", recording_session)
    monkeypatch.setattr(mcp_server, "get_access_token", recording_token)
    token = sign()
    calls = [("whoami", {}), ("list_exercises", {}), ("log_lifts", {"sets": squat(100)}),
             ("log_bodyweight", {"kg": 80}), ("get_progress", {}),
             ("set_goal", {"kind": "lift", "exercise": "Back Squat", "target_value": 150,
                           "target_date": (date.today() + timedelta(days=90)).isoformat()}),
             ("update_settings", {"time_zone": "Europe/London"}),
             ("get_log", {"days": 3}), ("log_measurement", {"kind": "waist", "value": 84})]
    for name, arguments in calls:
        ok(http, token, name, arguments)
    assert len(loop_threads) >= 1
    assert len(opened) >= 3 * len(calls)  # the person, the service and the call log, each call
    assert not any(on_loop for _, on_loop in opened)
    assert not {thread for thread, _ in opened} & loop_threads


# ---------------------------------------------------------------- resources and the instructions

def read_resource(client: TestClient, token: str, uri: str) -> dict:
    r = rpc(client, "resources/read", {"uri": uri}, token=token)
    assert r.status_code == 200, r.text
    contents = r.json()["result"]["contents"]
    assert len(contents) == 1
    return contents[0]


def test_the_two_resources_are_listed_and_read_back(http):
    token = sign()
    listed = rpc(http, "resources/list", token=token).json()["result"]["resources"]
    assert {r["uri"]: r["mimeType"] for r in listed} == {"spotter://can-and-cant": "text/markdown",
                                                          "spotter://exercises": "application/json"}
    doc = read_resource(http, token, "spotter://can-and-cant")
    assert doc["mimeType"] == "text/markdown" and doc["text"] == CAN_AND_CANT.read_text(encoding="utf-8")
    exercises = read_resource(http, token, "spotter://exercises")
    assert exercises["mimeType"] == "application/json"
    assert json.loads(exercises["text"]) == services.list_exercises() == ok(http, token, "list_exercises")


def test_resource_reads_are_not_in_the_call_log(http):
    token = sign()
    read_resource(http, token, "spotter://exercises")
    read_resource(http, token, "spotter://can-and-cant")
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(CallLog)) == 0


def test_can_and_cant_names_exactly_the_twelve_functions():
    text = CAN_AND_CANT.read_text(encoding="utf-8")
    named = set(re.findall(r"`([a-z_]+)`", text))
    service_functions = {name for name in services.__all__ if callable(getattr(services, name))}
    assert named & service_functions <= set(FUNCTIONS)  # it names no function Claude does not have (restore_entry, say)
    assert set(FUNCTIONS) <= named  # and it names every function Claude has


def test_can_and_cant_states_the_limit_the_delete_consent_and_that_edits_keep_nothing():
    text = " ".join(CAN_AND_CANT.read_text(encoding="utf-8").split())
    assert f"at most {services.WRITE_LIMIT} writes in any {services.WRITE_WINDOW_MINUTES} minutes" in text
    assert "a refused one too" in text  # refused writes count against the limit
    assert "Claude can delete only when the person has allowed it" in text
    assert "An edit keeps no old values" in text


def test_the_instructions_say_how_to_use_spotter(http):
    init = rpc(http, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "0"}}, token=sign())
    instructions = init.json()["result"]["instructions"]
    assert instructions == mcp_server.INSTRUCTIONS
    for words in ("Call whoami first", "list_exercises", "time_zone_note", "update_settings", "which city",
                  "Never send the person to a page", "fix", "allow_repeat", "links last"):
        assert words in instructions
    assert "Settings" not in instructions


# ---------------------------------------------------------------- tools/mcp_call.py

def _mcp_call_module():
    import importlib.util

    from app.config import ROOT

    spec = importlib.util.spec_from_file_location("mcp_call", ROOT / "tools" / "mcp_call.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mcp_call_takes_https_or_localhost_only():
    tool = _mcp_call_module()
    assert tool.endpoint("https://spotter.example.app") == "https://spotter.example.app/mcp"
    assert tool.endpoint("https://spotter.example.app/mcp/") == "https://spotter.example.app/mcp"
    assert tool.endpoint("http://localhost:8000") == "http://localhost:8000/mcp"
    assert tool.endpoint("http://127.0.0.1:8000/mcp") == "http://127.0.0.1:8000/mcp"
    assert tool.endpoint("http://spotter.example.app/mcp") is None
    assert tool.endpoint("ftp://localhost/mcp") is None


def test_mcp_call_refuses_redirects():
    import urllib.error
    import urllib.request

    tool = _mcp_call_module()
    request = urllib.request.Request("https://spotter.example.app/mcp", headers={"Authorization": "Bearer x"})
    with pytest.raises(urllib.error.HTTPError):
        tool._RefuseRedirects().redirect_request(request, None, 302, "Found", {}, "https://elsewhere.example/")


def test_mcp_call_needs_the_token_in_the_environment(monkeypatch, capsys):
    tool = _mcp_call_module()
    monkeypatch.delenv("SPOTTER_TOKEN", raising=False)
    assert tool.main(["mcp_call.py", "https://spotter.example.app", "whoami", "{}"]) == 2
    assert "SPOTTER_TOKEN" in capsys.readouterr().out
    assert tool.main(["mcp_call.py", "http://spotter.example.app", "whoami", "{}"]) == 2


# ---------------------------------------------------------------- every argument reaches the service

# Every optional argument of every function, with a value to send. The required ones go in REQUIRED.
OPTIONAL_ARGUMENTS = {
    "whoami": {},
    "list_exercises": {"kind": "lift", "category": "push"},
    "log_lifts": {"date": "2026-01-02", "notes": "Notes", "allow_repeat": True},
    "log_bodyweight": {"date": "2026-01-02", "allow_repeat": True},
    "set_goal": {"kind": "lift", "exercise": "Back Squat", "target_value": 150.5, "target_date": "2099-01-01",
                 "text": "Text", "start_value": 100.5, "goal_id": 7, "status": "open"},
    "get_progress": {"exercise": "Back Squat"},
    "update_settings": {"time_zone": "Asia/Kolkata"},
    "get_log": {"date": "2026-01-02", "days": 7},
    "log_measurement": {"date": "2026-01-02", "allow_repeat": True},
    "edit_entry": {"date": "2026-01-02", "notes": "Notes", "sets": squat(105), "kg": 80.5, "value": 84.5},
    "restore_entry": {},
    "delete_entry": {},
}
REQUIRED = {"log_lifts": {"sets": squat(100)}, "log_bodyweight": {"kg": 80.5},
            "log_measurement": {"kind": "waist", "value": 84.5},
            "edit_entry": {"kind": "lift_session", "entry_id": 7},
            "restore_entry": {"kind": "measurement", "entry_id": 7},
            "delete_entry": {"kind": "bodyweight", "entry_id": 7}}
DATA_WRITES = {"log_lifts", "log_bodyweight", "set_goal", "update_settings", "log_measurement"}


def spy_on_services(monkeypatch) -> dict[str, tuple]:
    """Replace each service with one that records what it was given and answers with an error."""
    received: dict[str, tuple] = {}
    for name in FUNCTIONS:
        def spy(*args, _name=name, **kwargs):
            received[_name] = (args, kwargs)
            return {"error": "spy", "message": "Recorded.", "fix": "None."}

        monkeypatch.setattr(services, name, spy, raising=False)
    return received


def service_input(name: str, account: int, arguments: dict) -> tuple:
    """What the service should receive for these arguments: (positional, keyword)."""
    if name in DATA_WRITES:
        return (account, arguments, "claude"), {}
    if name == "list_exercises":
        return (arguments.get("kind"), arguments.get("category")), {}
    if name == "get_progress":
        return (account, arguments.get("exercise")), {}
    if name == "get_log":
        return (account,), {k: v for k, v in arguments.items() if k in ("date", "days")}
    if name == "edit_entry":
        changes = {k: v for k, v in arguments.items() if k not in ("kind", "entry_id")}
        return (account, arguments["kind"], arguments["entry_id"], changes, "claude"), {}
    if name in ("restore_entry", "delete_entry"):
        return (account, arguments["kind"], arguments["entry_id"]), {}
    return (account,), {}


def test_the_argument_table_lists_every_optional_argument(http):
    for tool in tool_list(http, sign()):
        schema = tool["inputSchema"]
        optional = set(schema["properties"]) - set(schema.get("required", []))
        assert optional == set(OPTIONAL_ARGUMENTS[tool["name"]]), tool["name"]
        assert set(schema.get("required", [])) == set(REQUIRED.get(tool["name"], {})), tool["name"]


@pytest.mark.parametrize("name", FUNCTIONS)
def test_every_argument_sent_reaches_the_service_and_no_other(http, monkeypatch, name):
    token = sign()
    whoami(http, token)
    account = one_user().id
    received = spy_on_services(monkeypatch)
    every = {**REQUIRED.get(name, {}), **OPTIONAL_ARGUMENTS[name]}
    set_switch(account, "log_delete")  # every function allowed, so every service is reached
    failed(http, token, name, every)
    assert received[name] == service_input(name, account, every)
    fewest = REQUIRED.get(name, {})
    failed(http, token, name, fewest)
    assert received[name] == service_input(name, account, fewest)  # what was not sent is left out


def test_writes_through_mcp_are_stored_as_by_claude(http):
    token = sign()
    ok(http, token, "log_lifts", {"sets": squat(100)})
    ok(http, token, "log_bodyweight", {"kg": 80})
    with db.session_scope() as session:
        assert [s.logged_by for s in session.scalars(select(LiftSession))] == ["claude"]
        assert [b.logged_by for b in session.scalars(select(Bodyweight))] == ["claude"]
    # A goal stores no author column; the service is told who sets it, which the spy test above checks.


def test_set_goal_passes_start_value_for_a_lift_with_nothing_logged(http):
    goal = ok(http, sign(), "set_goal", {"kind": "lift", "exercise": "Back Squat", "target_value": 150,
                                         "target_date": "2099-01-01", "start_value": 100})
    assert goal["goal"]["start_value"] == 100 and goal["warnings"] == []


# ---------------------------------------------------------------- the library's own log lines

def test_the_library_logs_only_warnings_and_never_a_callers_tool_name(make_client, logs):
    client = make_client(auth_debug=True)
    token = sign()
    whoami(client, token)
    result = call(client, token, "zz-caller-chosen-tool")
    assert result["isError"] is True
    assert logging.getLogger("mcp").level == logging.WARNING
    assert "token verified" in logs.text  # Spotter's own debug lines still show
    assert "zz-caller-chosen-tool" not in logs.text and "Terminating session" not in logs.text
    assert not [r for r in logs.records if r.name.startswith("mcp") and r.levelno < logging.WARNING]
    logging.getLogger("mcp.server.streamable_http").warning("a library warning")
    assert "a library warning" in logs.text  # and the library's warnings still do


# ---------------------------------------------------------------- tools/mcp_call.py against a local server

class _Server:
    """A local HTTP server on 127.0.0.1 that answers every request with `answer(handler)`."""

    def __init__(self, answer):
        import http.server

        self.requests: list[dict] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - the library's name
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                outer.requests.append(dict(self.headers))
                answer(self)

            do_GET = do_POST  # noqa: N815

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()


@pytest.fixture
def local_server():
    opened: list[_Server] = []

    def make(answer) -> _Server:
        server = _Server(answer)
        opened.append(server)
        return server

    yield make
    for server in opened:
        server.close()


def _reply(handler, status: int, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else b""
    handler.send_response(status)
    for k, v in (headers or {}).items():
        handler.send_header(k, v)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _sent_token(handler) -> str:
    return handler.headers.get("Authorization", "").removeprefix("Bearer ")


LIVE_TOKEN = "eyJ.live-token-value-for-tests.sig"


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_mcp_call_refuses_a_redirect_and_never_asks_the_second_address(local_server, monkeypatch, capsys, code):
    tool = _mcp_call_module()
    second = local_server(lambda h: _reply(h, 200, {"jsonrpc": "2.0", "id": 1, "result": {"isError": False}}))
    first = local_server(lambda h: _reply(h, code, headers={"Location": second.url + "/mcp"}))
    monkeypatch.setenv("SPOTTER_TOKEN", LIVE_TOKEN)
    assert tool.main(["mcp_call.py", first.url + "/mcp", "whoami", "{}"]) == 1
    assert len(first.requests) == 1 and second.requests == []
    assert "Redirect refused" in capsys.readouterr().out


@pytest.mark.parametrize("where", ["result", "redirect location", "WWW-Authenticate", "JSON-RPC error"])
def test_mcp_call_never_prints_the_token_even_when_the_server_echoes_it(local_server, monkeypatch, capsys, where):
    def answer(h):
        token = _sent_token(h)
        if where == "result":
            _reply(h, 200, {"jsonrpc": "2.0", "id": 1, "result": {
                "isError": False, "structuredContent": {"echo": token}, "content": [{"type": "text", "text": token}]}})
        elif where == "redirect location":
            _reply(h, 302, headers={"Location": f"https://elsewhere.example/?t={token}"})
        elif where == "WWW-Authenticate":
            _reply(h, 401, headers={"WWW-Authenticate": f'Bearer error="invalid_token", error_description="{token}"'})
        else:
            _reply(h, 200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": f"bad {token}"}})

    tool = _mcp_call_module()
    server = local_server(answer)
    monkeypatch.setenv("SPOTTER_TOKEN", LIVE_TOKEN)
    tool.main(["mcp_call.py", server.url, "whoami", "{}"])
    assert _sent_token_seen(server) == LIVE_TOKEN  # the server really had it to echo
    printed = capsys.readouterr()
    assert LIVE_TOKEN not in printed.out and LIVE_TOKEN not in printed.err
    assert tool.HIDDEN in printed.out


def _sent_token_seen(server: _Server) -> str:
    return server.requests[0].get("Authorization", "").removeprefix("Bearer ")


# ---------------------------------------------------------------- update_settings: Claude sets the time zone

def _local_dates(zone: str) -> set[str]:
    """Today in that zone. Read before and after a call, so a call that crosses midnight still matches."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return {datetime.now(ZoneInfo(zone)).date().isoformat()}


def test_claude_sets_the_time_zone_and_the_note_goes(http):
    token = sign()
    first = whoami(http, token)
    assert first["time_zone"] is None and "time_zone_note" in first
    before = _local_dates("Asia/Kolkata")
    result = ok(http, token, "update_settings", {"time_zone": "Asia/Kolkata"})
    after = whoami(http, token)
    assert result["time_zone"] == "Asia/Kolkata" and result["changed"] == ["time_zone"]
    assert result["today"] in before | _local_dates("Asia/Kolkata")
    assert after["time_zone"] == "Asia/Kolkata" and "time_zone_note" not in after
    assert after["today"] == result["today"]
    assert one_user().time_zone == "Asia/Kolkata"
    again = ok(http, token, "update_settings", {"time_zone": "Asia/Kolkata"})
    assert again["changed"] == []


@pytest.mark.parametrize("zone", ["IST", "+05:30", "UTC+5:30", "Kolkata"])
def test_an_abbreviation_or_an_offset_is_refused_with_a_fix(http, zone):
    token = sign()
    whoami(http, token)
    result = call(http, token, "update_settings", {"time_zone": zone})
    assert result["isError"] is True
    error = result["structuredContent"]
    assert error["error"] in ("unknown_time_zone", "invalid_field") and error["fix"]
    assert result["content"][0]["text"] == compact(error)
    assert one_user().time_zone is None


def test_the_permission_switch_cannot_be_set_through_update_settings(http, monkeypatch):
    # By the schema: the only argument is time_zone.
    schema = {t["name"]: t["inputSchema"] for t in tool_list(http, sign())}["update_settings"]
    assert set(schema["properties"]) == {"time_zone"}
    assert not any("claude_can" in t["inputSchema"].get("properties", {}) for t in tool_list(http, sign()))
    # And in practice: an extra claude_can is dropped before the service; the stored switch is unchanged.
    token = sign()
    whoami(http, token)
    ok(http, token, "update_settings", {"time_zone": "Europe/London", "claude_can": "log_delete"})
    assert one_user().claude_can == "log" and whoami(http, token)["claude_can"] == "log"
    received = spy_on_services(monkeypatch)
    failed(http, token, "update_settings", {"time_zone": "Europe/London", "claude_can": "log_delete"})
    assert received["update_settings"][0][1] == {"time_zone": "Europe/London"}


def test_one_persons_time_zone_never_changes_anothers(http):
    a, b = sign(claims(sub="user_01ZONE_A")), sign(claims(sub="user_01ZONE_B"))
    whoami(http, a)
    whoami(http, b)
    ok(http, a, "update_settings", {"time_zone": "Asia/Kolkata"})
    other = whoami(http, b)
    assert other["time_zone"] is None and "time_zone_note" in other
    ok(http, b, "update_settings", {"time_zone": "America/New_York"})
    assert whoami(http, a)["time_zone"] == "Asia/Kolkata"
    assert whoami(http, b)["time_zone"] == "America/New_York"


# ---------------------------------------------------------------- the time zone is set by Claude, never on a page

ZONE_WORDS = re.compile(r"time[ _]zone|\bzone\b", re.IGNORECASE)
PAGE_WORDS = re.compile(r"settings page|settings link|links\.settings|/settings|\bpage\b|\bform\b", re.IGNORECASE)
NEGATION = re.compile(r"\b(never|not|no)\b|n't\b", re.IGNORECASE)
# Words that tell someone to do something there: a sentence naming a page and one of these sends a person to it.
ACTION_WORDS = re.compile(r"\b(set|sets|change|changes|turn|switch|type|enter|go|send|point|visit|open|ask)\b",
                          re.IGNORECASE)
# The one exception in Spotter: only the person can change what Claude may do, and only on the Settings page.
PAGE_SEND_EXCEPTION = mcp_server.NOT_ALLOWED_FIX


def sends_to_a_page(sentence: str) -> bool:
    return bool(PAGE_WORDS.search(sentence) and (ZONE_WORDS.search(sentence) or ACTION_WORDS.search(sentence))
                and not NEGATION.search(sentence))


def model_texts(client: TestClient, token: str) -> dict[str, str]:
    """Every text the calling model reads, by where it comes from."""
    init = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "0"}}, token=token)
    texts = {"instructions": init.json()["result"]["instructions"]}
    for tool in tool_list(client, token):
        texts[f"{tool['name']} description"] = tool["description"]
        for kind in ("inputSchema", "outputSchema"):
            schema = tool[kind]
            for owner, obj in [("", schema), *schema.get("$defs", {}).items()]:
                for prop, spec in obj.get("properties", {}).items():
                    texts[f"{tool['name']} {kind} {owner}.{prop}"] = spec.get("description", "")
    for resource in rpc(client, "resources/list", token=token).json()["result"]["resources"]:
        texts[f"{resource['uri']} description"] = resource.get("description", "")
        texts[resource["uri"]] = read_resource(client, token, resource["uri"])["text"]
    texts["whoami time_zone_note"] = whoami(client, token).get("time_zone_note", "")  # the service's own words
    return texts


def sentences(text: str) -> list[str]:
    """Sentences, with markdown table cells and list items split apart and line breaks undone."""
    flat = " ".join(text.replace("|", "\n").split())
    return [s for s in re.split(r"(?<=[.!?:])\s+|\s-\s", flat) if s]


def test_nothing_the_model_reads_sends_the_person_to_a_page_but_the_permission_switch(http):
    token = sign()
    texts = model_texts(http, token)
    set_switch(one_user().id, "read")
    refused = failed(http, token, "log_bodyweight", {"kg": 80})  # the not_allowed answer is read too
    texts["not_allowed message"], texts["not_allowed fix"] = refused["message"], refused["fix"]
    sending = [(where, s) for where, text in texts.items() for s in sentences(text) if sends_to_a_page(s)]
    assert sending == [("not_allowed fix", PAGE_SEND_EXCEPTION)]  # the permission switch, and nothing else
    for where in ("instructions", "spotter://can-and-cant"):
        naming = [s for s in sentences(texts[where]) if "update_settings" in s and ZONE_WORDS.search(s)]
        assert naming, f"{where} does not say that update_settings sets the time zone"


def test_the_page_check_allows_settings_mentioned_for_other_reasons():
    fine = ["The Settings page shows the same number.", "The Settings page: the person's account details.",
            "Never send the person to a page for it.", "Set the time zone with update_settings.",
            "The page for the session's day.", "Claude can delete when the person has allowed it in Settings."]
    caught = ["Ask the person their time zone and send them to the Settings page to set it.",
              "If whoami carries time_zone_note, point them to the Settings link.",
              "Ask their time zone and point them to links.settings.",
              "Ask the person to turn deleting on in the Settings page.",
              PAGE_SEND_EXCEPTION]
    for text in fine + caught:
        assert sends_to_a_page(text) is (text in caught), text


def set_switch(account: int, value: str) -> None:
    """Set the permission switch on the row directly, as the Settings page would."""
    with db.session_scope() as session:
        session.get(User, account).claude_can = value


# ---------------------------------------------------------------- the permission switch (stage 3)

# The contract's table: what each function needs, written out here, not read from NEEDS.
CONTRACT_NEEDS = {
    "whoami": "read", "list_exercises": "read", "get_progress": "read", "get_log": "read",
    "log_lifts": "log", "log_bodyweight": "log", "set_goal": "log", "update_settings": "log",
    "log_measurement": "log", "edit_entry": "log", "restore_entry": "log", "delete_entry": "log_delete",
}
ALLOWS = {"read": {"read"}, "log": {"read", "log"}, "log_delete": {"read", "log", "log_delete"}}


def test_every_registered_function_has_a_level_and_every_level_a_function(http):
    registered = {t["name"] for t in tool_list(http, sign())}
    assert registered == set(mcp_server.NEEDS) == set(CONTRACT_NEEDS) == set(FUNCTIONS)
    assert mcp_server.NEEDS == CONTRACT_NEEDS


def test_a_function_without_a_level_stops_the_server_at_start(app_settings, monkeypatch):
    from app.auth import JwksTokenVerifier, UserinfoClient

    monkeypatch.delitem(mcp_server.NEEDS, "get_log")
    with pytest.raises(RuntimeError, match="get_log"):
        mcp_server.build_server(app_settings, JwksTokenVerifier(app_settings), UserinfoClient(app_settings))


@pytest.mark.parametrize("level", ["read", "log", "log_delete"])
def test_the_switch_gates_every_function_as_the_contract_says(http, monkeypatch, level):
    token = sign()
    whoami(http, token)
    account = one_user().id
    set_switch(account, level)
    received = spy_on_services(monkeypatch)
    for name in FUNCTIONS:
        received.pop(name, None)
        error = failed(http, token, name, REQUIRED.get(name, {}))
        if CONTRACT_NEEDS[name] in ALLOWS[level]:
            assert error["error"] == "spy" and name in received, (level, name)  # the service ran
        else:
            assert name not in received, (level, name)  # refused before the service
            assert error == {"error": "not_allowed", "message": mcp_server.NOT_ALLOWED_MESSAGES[level],
                             "fix": "The person can change what Claude may do on the Settings page.",
                             "links": {"settings": links.page("settings")}}, (level, name)


def test_a_refused_write_writes_nothing_and_leaves_a_call_log_row(http):
    token = sign()
    ok(http, token, "log_lifts", {"sets": squat(100)})
    account = one_user().id
    set_switch(account, "read")
    result = call(http, token, "log_lifts", {"sets": squat(110)})
    assert result["isError"] is True and result["structuredContent"]["error"] == "not_allowed"
    assert result["content"][0]["text"] == compact(result["structuredContent"])
    assert sessions_logged() == 1
    with db.session_scope() as session:
        row = session.scalars(select(CallLog).order_by(CallLog.id.desc())).first()
        assert (row.function, row.ok, row.error_code, row.user_id) == ("log_lifts", False, "not_allowed", account)


def test_delete_is_refused_at_log_and_the_entry_stays(http):
    token = sign()
    logged = ok(http, token, "log_bodyweight", {"kg": 80})
    error = failed(http, token, "delete_entry", {"kind": "bodyweight", "entry_id": logged["id"]})
    assert error["error"] == "not_allowed" and error["message"] == "This account lets Claude read and log, not delete."
    with db.session_scope() as session:
        assert session.get(Bodyweight, logged["id"]).deleted_at is None
    set_switch(one_user().id, "log_delete")
    assert ok(http, token, "delete_entry", {"kind": "bodyweight", "entry_id": logged["id"]})["deleted"] is True
    assert ok(http, token, "restore_entry", {"kind": "bodyweight", "entry_id": logged["id"]})["restored"] is True


def test_an_unknown_switch_value_counts_as_read_only():
    assert mcp_server.level_of("everything") == "read" and mcp_server.level_of(None) == "read"
    assert mcp_server.not_allowed("log_lifts", "everything")["error"] == "not_allowed"
    assert mcp_server.not_allowed("whoami", "everything") is None


def test_delete_carries_the_destructive_hint_and_no_other_function_does(http):
    hints = {t["name"]: t["annotations"]["destructiveHint"] for t in tool_list(http, sign())}
    assert {name for name, destructive in hints.items() if destructive} == {"delete_entry"}


def test_whoami_explains_the_three_switch_values(http):
    schema = {t["name"]: t["outputSchema"] for t in tool_list(http, sign())}["whoami"]
    text = schema["properties"]["claude_can"]["description"]
    for word in ("read", "log", "log_delete", "delete_entry"):
        assert word in text


def test_the_instructions_say_what_to_do_on_not_allowed_and_how_to_look_back():
    text = mcp_server.INSTRUCTIONS
    assert "not_allowed" in text and "do not retry" in text and "get_log" in text


# ---------------------------------------------------------------- the write limit reaches Claude

def test_too_many_writes_reaches_claude_as_an_error_with_the_wait(http, monkeypatch):
    refusal = {"error": "too_many_writes", "message": "120 writes in the last 10 minutes.",
               "fix": "Wait 4 minutes, then retry.", "wait_minutes": 4}
    monkeypatch.setattr(services, "log_lifts", lambda *a: refusal)
    result = call(http, sign(), "log_lifts", {"sets": squat(100)})
    assert result["isError"] is True and result["structuredContent"] == refusal
    with db.session_scope() as session:
        row = session.scalars(select(CallLog)).one()
        assert (row.ok, row.error_code) == (False, "too_many_writes")


# ---------------------------------------------------------------- get_log's days are published with their range

def test_get_log_publishes_the_days_range(http):
    days = {t["name"]: t["inputSchema"] for t in tool_list(http, sign())}["get_log"]["properties"]["days"]
    assert (days["minimum"], days["maximum"]) == (1, 14)


def test_the_write_limit_counts_the_same_names_the_switch_gates_as_writes():
    # The services count call-log rows by function name; every write Claude can make must be among them.
    writes = {name for name, level in mcp_server.NEEDS.items() if level != "read"}
    assert set(services.WRITE_FUNCTIONS) == writes
