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
             "get_log", "log_measurement", "edit_entry", "restore_entry", "delete_entry",
             "find_food", "log_food", "save_food", "log_cardio", "get_trends", "get_goals", "import_entries"]
READS = {"whoami", "list_exercises", "get_progress", "get_log", "find_food", "get_trends", "get_goals"}
READ_HINTS = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
WRITE_HINTS = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
SETTING_HINTS = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
SETTINGS = {"update_settings", "save_food", "import_entries"}  # idempotent: the same call twice ends in the same state
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

    def keep(name: str, arguments: dict | None = None, as_token: str = token) -> dict:
        result = call(client, as_token, name, arguments)
        assert result["isError"] is False, result
        assert [c["type"] for c in result["content"]] == ["text"]
        assert result["content"][0]["text"] == compact(result["structuredContent"])  # the same dict, as JSON
        seen[name].append(result["structuredContent"])
        return result["structuredContent"]

    who = keep("whoami")  # no time zone yet: carries time_zone_note
    today = date.fromisoformat(who["today"])
    keep("get_trends")  # nothing logged: every block that can be null is null
    keep("get_goals")  # no goals
    keep("list_exercises")
    keep("list_exercises", {"kind": "lift", "category": "push"})
    keep("get_progress")  # nothing logged: note
    keep("get_progress", {"exercise": "Back Squat"})  # nothing logged: note
    keep("log_cardio", {"exercise": "Treadmill Walk", "minutes": 30})  # no weigh-in yet: no estimate, a note
    keep("find_food", {"query": "dal"})  # nothing saved or logged; USDA off: usda null and a note
    estimate = keep("log_food", {"entries": [{"description": "2 rotis", "kcal": 240, "estimated": True},
                                             {"description": "Dal", "kcal": 180, "estimated": True}]})  # note
    keep("log_food", {"entries": [{"description": "2 rotis", "kcal": 240, "estimated": True},
                                  {"description": "Dal", "kcal": 180, "estimated": True}]})  # duplicate
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
    roti = keep("save_food", {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120})  # created
    keep("save_food", {"name": "roti", "serving_label": "1 roti", "kcal_per_serving": 110, "grams_per_serving": 40})
    keep("find_food", {"query": "roti"})  # a saved hit and a recent entry
    keep("update_settings", {"calorie_target_kcal": 2200, "target_cardio_minutes_per_week": 150,
                             "add_back_exercise_kcal": True})  # a target: remaining and net are numbers
    keep("log_food", {"entries": [{"description": "Roti", "saved_food_id": roti["id"], "servings": 2,
                                   "meal": "Dinner"}]})
    cardio = keep("log_cardio", {"exercise": "Treadmill Run", "minutes": 25, "distance_km": 4.5,
                                 "machine_kcal": 280, "notes": "Intervals"})  # an estimate and a pace
    keep("edit_entry", {"kind": "food_entry", "entry_id": estimate["ids"][1], "kcal": 150})  # becomes you_said
    keep("edit_entry", {"kind": "cardio_entry", "entry_id": cardio["id"], "minutes": 30})  # re-estimated
    keep("get_log", {"days": 14})
    keep("get_progress", {"exercise": "Treadmill Run"})  # a cardio activity
    keep("get_trends")  # weigh-ins 30 days apart and food: a rate and calories, no maintenance yet
    keep("get_goals")
    keep("get_goals", {"include_closed": True})  # the squat goal marked hit
    # A second person with sample data: every read carries includes_sample and its note.
    sample_token = sign(claims(sub="user_01SAMPLE", email="sample@example.com", name="Sample"))
    sample_account = whoami(client, sample_token)["account_id"]
    added = services.add_sample_data(sample_account, "web")
    assert "error" not in added, added
    for name in ("get_log", "get_progress", "get_trends", "get_goals"):
        keep(name, as_token=sample_token)
    # A sample goal dropped: the closed list then holds a sample goal too.
    sample_goal = seen["get_goals"][-1]["goals"][0]["goal_id"]
    keep("set_goal", {"goal_id": sample_goal, "status": "dropped"}, as_token=sample_token)
    keep("get_goals", {"include_closed": True}, as_token=sample_token)
    # import_entries, last, so no read above sees its rows: a preview, the real run, the same rows again, and a
    # run whose every row fails (no date passed: from, to and the day link are null or absent).
    keep("import_entries", {"rows": import_rows(today), **IMPORT_UNITS})  # dry_run left out: by_row, a null id
    keep("import_entries", {"rows": import_rows(today), "dry_run": False, **IMPORT_UNITS})  # day taken by id
    again = import_rows(today)
    keep("import_entries", {"rows": [again[1], *again[3:8]], "dry_run": False, **IMPORT_UNITS})  # nothing new
    keep("import_entries", {"rows": [{"kind": "bodyweight", "date": "soon", "weight": 80},
                                     {"kind": "weight", "date": "later"}], "dry_run": True})
    return seen


IMPORT_UNITS = {"weight_unit": "lb", "distance_unit": "mi", "length_unit": "in"}


def import_rows(today: date) -> list[dict]:
    """An export of every kind, in lb, mi and in. Row 1 falls on every_result's 82 kg weigh-in 30 days back (day
    taken by its id), row 3 on row 2's day (day taken by row 2), row 6 repeats row 5, and row 9 names a lift that
    is not on the list."""
    def ago(n: int) -> str:
        return (today - timedelta(days=n)).isoformat()
    deadlift = {"kind": "lift_session", "date": ago(43), "notes": "From the old app",
                "sets": [{"exercise": "Deadlift", "weight": 315, "reps": 5, "times": 3}]}
    return [{"kind": "bodyweight", "date": ago(30), "weight": 181},
            {"kind": "bodyweight", "date": ago(45), "weight": 180},
            {"kind": "bodyweight", "date": ago(45), "weight": 179},
            {"kind": "measurement", "date": ago(44), "measurement_kind": "waist", "value": 33.5},
            deadlift, dict(deadlift),
            {"kind": "food_entry", "date": ago(42), "entries": [{"description": "Oats", "kcal": 350, "meal": "breakfast"}]},
            {"kind": "cardio_entry", "date": ago(41), "exercise": "Outdoor Run", "minutes": 30, "distance": 3.1},
            {"kind": "lift_session", "date": ago(40), "sets": [{"exercise": "Squat (Barbell)", "weight": 225, "reps": 5}]}]


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
    assert list(schemas["set_goal"]["properties"]) == ["kind", "exercise", "measurement_kind", "distance_km",
                                                       "target_value", "target_date", "text", "start_value", "goal_id",
                                                       "status"]
    assert not schemas["set_goal"].get("required")
    assert schemas["get_trends"]["properties"] == {}
    assert list(schemas["get_goals"]["properties"]) == ["include_closed"]
    assert schemas["get_goals"]["properties"]["include_closed"]["type"] == "boolean"
    assert list(schemas["get_progress"]["properties"]) == ["exercise"]
    assert list(schemas["update_settings"]["properties"]) == [
        "time_zone", "calorie_target_kcal", "target_sessions_per_week", "target_cardio_minutes_per_week",
        "add_back_exercise_kcal"]
    assert list(schemas["find_food"]["properties"]) == ["query", "limit"]
    assert list(schemas["log_food"]["properties"]) == ["entries", "date", "allow_repeat"]
    assert list(schemas["log_food"]["$defs"]["FoodEntryIn"]["properties"]) == [
        "description", "meal", "kcal", "estimated", "saved_food_id", "servings", "usda_fdc_id", "grams"]
    assert list(schemas["save_food"]["properties"]) == ["name", "serving_label", "kcal_per_serving", "grams_per_serving"]
    assert list(schemas["log_cardio"]["properties"]) == [
        "exercise", "minutes", "date", "distance_km", "incline_pct", "speed_kmh", "avg_hr", "machine_kcal", "notes",
        "allow_repeat"]
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
        assert keys == wire_fields(model), (name, keys ^ wire_fields(model))  # every field used, no key unmodelled


def unmodelled(value: Any, model, path: str) -> set[str]:
    """Every key in a result, at any depth, that its model has no field for. Nested models ignore extras when they
    validate, so a key they lack would pass silently and go unpublished; this finds it. Fields typed as a plain dict
    (an entry with every stored column) are not walked."""
    import typing

    from pydantic import BaseModel

    def models(annotation) -> list:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return [annotation]
        return [m for arg in typing.get_args(annotation) for m in models(arg)]

    if isinstance(value, list):
        return set().union(set(), *(unmodelled(v, model, path) for v in value))
    if not isinstance(value, dict):
        return set()
    fields = {field.alias or name: field for name, field in model.model_fields.items()}
    found = set()
    for key, inner in value.items():
        if key not in fields:
            found.add(f"{path}.{key}")
        elif nested := models(fields[key].annotation):
            found |= unmodelled(inner, nested[0], f"{path}.{key}")
    return found


def test_every_key_of_every_real_result_is_modelled_at_every_depth(http):
    seen = every_result(http, sign())
    missing = set().union(*(unmodelled(r, RESULTS[name], name) for name, results in seen.items() for r in results))
    assert missing == set()
    assert any(e["saved_food_name"] for r in seen["get_log"] for d in r["days"]
               for meal in d["food"]["meals"].values() for e in meal)  # the saved roti is in there to be walked


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
             ("get_log", {"days": 3}), ("log_measurement", {"kind": "waist", "value": 84}),
             ("find_food", {"query": "dal"}), ("log_food", {"entries": [{"description": "Dal", "kcal": 180}]}),
             ("save_food", {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}),
             ("log_cardio", {"exercise": "Treadmill Walk", "minutes": 30})]
    calls += [("get_trends", {}), ("get_goals", {"include_closed": True})]
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
    # Settings is named only to say that export and delete are the person's own clicks there.
    naming = [s for s in sentences(instructions) if "Settings" in s]
    assert naming and set(naming) <= set(sentences(mcp_server.SETTINGS_ONLY_RULE)), naming


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
    "set_goal": {"kind": "lift", "exercise": "Back Squat", "measurement_kind": "waist", "distance_km": 5.5,
                 "target_value": 150.5, "target_date": "2099-01-01", "text": "Text", "start_value": 100.5, "goal_id": 7,
                 "status": "open"},
    "get_progress": {"exercise": "Back Squat"},
    "update_settings": {"time_zone": "Asia/Kolkata", "calorie_target_kcal": 2200, "target_sessions_per_week": 4,
                        "target_cardio_minutes_per_week": 150, "add_back_exercise_kcal": True},
    "get_log": {"date": "2026-01-02", "days": 7},
    "log_measurement": {"date": "2026-01-02", "allow_repeat": True},
    "edit_entry": {"date": "2026-01-02", "notes": "Notes", "sets": squat(105), "kg": 80.5, "value": 84.5,
                   "meal": "lunch", "description": "Dal", "kcal": 150.0, "exercise": "Treadmill Walk", "minutes": 30.0,
                   "distance_km": 3.5, "incline_pct": 2.0, "speed_kmh": 7.0, "avg_hr": 130, "machine_kcal": 210},
    "restore_entry": {},
    "delete_entry": {},
    "find_food": {"limit": 5},
    "log_food": {"date": "2026-01-02", "allow_repeat": True},
    "save_food": {"grams_per_serving": 40.0},
    "log_cardio": {"date": "2026-01-02", "distance_km": 3.5, "incline_pct": 2.0, "speed_kmh": 7.0, "avg_hr": 130,
                   "machine_kcal": 210, "notes": "Easy", "allow_repeat": True},
    "get_trends": {},
    "get_goals": {"include_closed": True},
    "import_entries": {"dry_run": False, "weight_unit": "lb", "distance_unit": "mi", "length_unit": "in"},
}
REQUIRED = {"log_lifts": {"sets": squat(100)}, "log_bodyweight": {"kg": 80.5},
            "log_measurement": {"kind": "waist", "value": 84.5},
            "edit_entry": {"kind": "lift_session", "entry_id": 7},
            "restore_entry": {"kind": "measurement", "entry_id": 7},
            "delete_entry": {"kind": "bodyweight", "entry_id": 7},
            "find_food": {"query": "banana"},
            "log_food": {"entries": [{"description": "Dal", "kcal": 180.0}]},
            "save_food": {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120.0},
            "log_cardio": {"exercise": "Treadmill Walk", "minutes": 30.0},
            "import_entries": {"rows": [{"kind": "bodyweight", "date": "2026-01-02", "weight": 180.5},
                                        {"kind": "lift_session", "date": "2026-01-03", "notes": "Old app",
                                         "sets": [{"exercise": "Deadlift", "weight": 315.0, "reps": 5, "times": 3}]},
                                        {"kind": "food_entry", "date": "2026-01-03",
                                         "entries": [{"description": "Oats", "kcal": 350.0, "meal": "breakfast"}]},
                                        {"kind": "cardio_entry", "date": "2026-01-04", "exercise": "Outdoor Run",
                                         "minutes": 30.0, "distance": 3.1, "speed": 6.2}]}}
DATA_WRITES = {"log_lifts", "log_bodyweight", "set_goal", "update_settings", "log_measurement", "log_food",
               "save_food", "log_cardio", "import_entries"}


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
    if name == "find_food":
        return (account, arguments["query"]), {k: v for k, v in arguments.items() if k == "limit"}
    if name == "get_log":
        return (account,), {k: v for k, v in arguments.items() if k in ("date", "days")}
    if name == "get_goals":
        return (account,), {k: v for k, v in arguments.items() if k == "include_closed"}
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
    assert "claude_can" not in schema["properties"] and set(schema["properties"]) == {
        "time_zone", "calorie_target_kcal", "target_sessions_per_week", "target_cardio_minutes_per_week",
        "add_back_exercise_kcal"}
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
    "find_food": "read", "log_food": "log", "save_food": "log", "log_cardio": "log",
    "get_trends": "read", "get_goals": "read",
    "import_entries": "log",
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
    # Besides them, only the web's own writes, which no function makes (stage 5: sample data, delete account).
    assert writes <= set(services.WRITE_FUNCTIONS)
    assert set(services.WRITE_FUNCTIONS) - writes <= {"add_sample_data", "remove_sample_data", "delete_account"}


# ---------------------------------------------------------------- food: the tag is the server's (stage 4)

FULL_ENTRY = {"description": "Roti", "meal": "lunch", "kcal": 240.0, "estimated": True, "saved_food_id": 7,
              "servings": 2.0, "usda_fdc_id": 1234, "grams": 80.0}


def all_properties(schema: dict) -> set[str]:
    """Every argument name in an input schema, nested ones included."""
    found = set(schema.get("properties", {}))
    for nested in schema.get("$defs", {}).values():
        found |= set(nested.get("properties", {}))
    return found


def test_source_is_an_argument_nowhere(http):
    for tool in tool_list(http, sign()):
        assert "source" not in all_properties(tool["inputSchema"]), tool["name"]


@pytest.mark.parametrize("entry", [FULL_ENTRY, {"description": "Dal"}, {"saved_food_id": 7, "servings": 2.0},
                                   {"usda_fdc_id": 1234, "grams": 80.0}],
                         ids=["every field", "description only", "a saved food with no description",
                              "a usda food with no description"])
def test_every_food_entry_field_sent_reaches_the_service_and_no_other(http, monkeypatch, entry):
    token = sign()
    whoami(http, token)
    account = one_user().id
    received = spy_on_services(monkeypatch)
    failed(http, token, "log_food", {"entries": [entry, {"description": "Tea", "kcal": 40}]})
    assert received["log_food"] == ((account, {"entries": [entry, {"description": "Tea", "kcal": 40.0}]}, "claude"), {})


def test_a_source_sent_by_the_caller_never_reaches_the_service(http, monkeypatch):
    token = sign()
    whoami(http, token)
    received = spy_on_services(monkeypatch)
    failed(http, token, "log_food", {"entries": [{"description": "Dal", "kcal": 180, "source": "usda"}],
                                     "source": "you_said"})
    assert received["log_food"][0][1] == {"entries": [{"description": "Dal", "kcal": 180.0}]}


def food_tags(result: dict) -> list[tuple[str, float, bool]]:
    return [(e["source"], e["kcal"], e["estimated"]) for e in result["entries"]]


def test_a_stated_number_is_you_said_and_a_worked_out_one_is_claude_estimate(http):
    token = sign()
    stated = ok(http, token, "log_food", {"entries": [{"description": "Dal", "kcal": 180}]})
    assert food_tags(stated) == [("you_said", 180, False)] and "note" not in stated
    guessed = ok(http, token, "log_food", {"entries": [{"description": "2 rotis", "kcal": 240, "estimated": True}]})
    assert food_tags(guessed) == [("claude_estimate", 240, True)]
    assert "estimate" in guessed["note"] and guessed["day"]["estimated_kcal"] == 240


def test_a_saved_food_by_id_is_tagged_saved_food_with_the_apps_number(http):
    token = sign()
    roti = ok(http, token, "save_food", {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120})
    logged = ok(http, token, "log_food", {"entries": [{"description": "Rotis", "saved_food_id": roti["id"],
                                                       "servings": 2}]})
    [entry] = logged["entries"]
    assert (entry["source"], entry["kcal"], entry["estimated"]) == ("saved_food", 240, False)
    assert (entry["saved_food_id"], entry["servings"]) == (roti["id"], 2)


@pytest.mark.parametrize("ids", [{"saved_food_id": 1, "servings": 1}, {"usda_fdc_id": 1234, "grams": 100}],
                         ids=["saved food", "usda food"])
def test_a_number_with_an_id_is_refused_through_mcp(http, ids):
    token = sign()
    if "saved_food_id" in ids:
        ids = {**ids, "saved_food_id": ok(http, token, "save_food", {"name": "Roti", "serving_label": "1 roti",
                                                                     "kcal_per_serving": 120})["id"]}
    result = call(http, token, "log_food", {"entries": [{"description": "Roti", "kcal": 300, **ids}]})
    assert result["isError"] is True
    error = result["structuredContent"]
    assert error["error"] == "invalid_field" and "app supplies the number" in error["fix"]
    assert result["content"][0]["text"] == compact(error)


def test_a_meal_left_out_is_picked_by_the_server_not_asked(http):
    entry = ok(http, sign(), "log_food", {"entries": [{"description": "Dal", "kcal": 180}]})["entries"][0]
    assert entry["meal"] in ("breakfast", "lunch", "dinner", "snack")


def test_the_tag_rule_and_the_meal_rule_are_in_the_instructions():
    text = mcp_server.INSTRUCTIONS
    for words in ("estimated false", "estimated true, always", "say in your reply that it is an estimate",
                  "by its id with servings or grams, and no kcal", "you may estimate, and must say so",
                  "Never ask which meal"):
        assert words in text, words


def test_can_and_cant_states_the_tags_the_cardio_formula_and_the_food_tables():
    text = " ".join(CAN_AND_CANT.read_text(encoding="utf-8").split())
    for words in ("`you_said`", "`claude_estimate`", "`saved_food`", "`usda`", "04:00 to 11:00 breakfast", "otherwise snack (22:00 to 04:00",
                  "MET x 3.5 x body weight in kg / 200 x minutes",
                  "Calorie estimates use MET values from the 2024 Adult Compendium of Physical Activities "
                  "(CC BY-NC-ND 4.0).",
                  "With no weigh-in there is no estimate", "USDA FoodData Central", "IFCT 2017 and INDB",
                  "not openly licensed", "`net_kcal`", "Nineteen functions"):
        assert words in text, words


def test_a_cardio_estimate_names_the_compendium(http):
    token = sign()
    ok(http, token, "log_bodyweight", {"kg": 80})
    result = ok(http, token, "log_cardio", {"exercise": "Treadmill Walk", "minutes": 30})
    assert "Compendium" in result["estimate"]["source"]
    assert result["estimate"]["weight_kg"] == 80


def test_the_real_results_include_the_null_cases_and_the_filled_ones(http):
    seen = every_result(http, sign())
    days = [r["day"] for r in seen["log_food"]]
    assert any(d["target_kcal"] is None and d["remaining_kcal"] is None and d["net_kcal"] is None for d in days)
    assert any(d["target_kcal"] == 2200 and d["remaining_kcal"] is not None and d["net_kcal"] is not None
               for d in days)
    assert any(r["estimate"] is None and r["note"] for r in seen["log_cardio"])  # no weigh-in yet
    assert any(r["estimate"] is not None and r["pace"] is not None for r in seen["log_cardio"])
    assert any(r["usda"] is None and r["usda_note"] for r in seen["find_food"])  # no USDA key in the tests
    assert any(r["saved"] for r in seen["find_food"])
    assert [r["created"] for r in seen["save_food"]] == [True, False]
    assert any("cardio" in r for r in seen["get_progress"])
    trends = seen["get_trends"]
    for block in ("bodyweight", "calories", "maintenance"):
        assert any(r[block] is None for r in trends) and any(r[block] is not None for r in trends), block
    assert any(r["maintenance_note"] for r in trends)
    assert any(r["bodyweight"]["rate_window"] is not None for r in trends if r["bodyweight"])
    assert any(r["closed"] for r in seen["get_goals"]) and any(not r["goals"] for r in seen["get_goals"])
    closed = [g for r in seen["get_goals"] for g in r["closed"]]
    assert any(g["is_sample"] for g in closed) and any(not g["is_sample"] for g in closed)
    for name in ("get_log", "get_progress", "get_trends", "get_goals"):
        assert any(r["includes_sample"] is True and r["note"] for r in seen[name]), name
        assert any(r["includes_sample"] is False for r in seen[name]), name


# ---------------------------------------------------------------- update_settings: null clears a target

TARGETS = {"calorie_target_kcal": 2200, "target_sessions_per_week": 4, "target_cardio_minutes_per_week": 150}


def stored_targets() -> dict:
    row = one_user()
    return {field: getattr(row, field) for field in TARGETS}


@pytest.mark.parametrize("field", sorted(TARGETS))
def test_a_target_sent_as_null_is_cleared_and_one_left_out_is_kept(http, field):
    token = sign()
    ok(http, token, "update_settings", TARGETS)
    assert stored_targets() == TARGETS
    cleared = ok(http, token, "update_settings", {field: None})
    assert cleared["changed"] == [field] and "cleared" in cleared["summary"]
    assert stored_targets() == {**TARGETS, field: None}  # only that one: the others were left out, and kept


def test_leaving_every_target_out_changes_nothing(http):
    token = sign()
    ok(http, token, "update_settings", TARGETS)
    result = call(http, token, "update_settings", {})
    assert result["structuredContent"]["error"] == "nothing_to_change"
    assert stored_targets() == TARGETS


def test_a_null_reaches_the_service_and_a_missing_field_does_not(http, monkeypatch):
    token = sign()
    whoami(http, token)
    received = spy_on_services(monkeypatch)
    failed(http, token, "update_settings", {"calorie_target_kcal": None, "add_back_exercise_kcal": None})
    assert received["update_settings"][0][1] == {"calorie_target_kcal": None, "add_back_exercise_kcal": None}
    failed(http, token, "update_settings", {"time_zone": None, "target_sessions_per_week": 3})
    assert received["update_settings"][0][1] == {"target_sessions_per_week": 3}  # the time zone stays as before


def test_a_null_add_back_is_refused_by_the_service_with_its_fix(http):
    token = sign()
    ok(http, token, "update_settings", {"add_back_exercise_kcal": True})
    error = failed(http, token, "update_settings", {"add_back_exercise_kcal": None})
    assert error["error"] == "invalid_field" and "true or false" in error["fix"]
    assert one_user().add_back_exercise_kcal is True


def test_the_targets_say_null_clears_them_and_publish_no_default(http):
    schema = {t["name"]: t["inputSchema"] for t in tool_list(http, sign())}["update_settings"]
    for field in TARGETS:
        assert "null clears it" in schema["properties"][field]["description"]
        assert "default" not in schema["properties"][field]
    assert not schema.get("required")


# ---------------------------------------------------------------- which activity, which entry

CARDIO_WORDS = ("When the person's words fit more than one activity", "more than 7 km/h is a run, otherwise a walk",
                "with nothing to go on, the walk", "Say in your reply which one was taken", "edit_entry",
                "Never ask first")
DELETE_WORDS = ("Delete only the entry the person named", "ask which", "never delete on a guess",
                "say what was deleted and that restore_entry brings it back")


def test_the_instructions_say_log_the_activity_dont_ask_and_never_delete_on_a_guess():
    for words in CARDIO_WORDS + DELETE_WORDS:
        assert words in mcp_server.INSTRUCTIONS, words


def test_log_cardio_and_delete_entry_carry_the_same_rules(http):
    tools = {t["name"]: t for t in tool_list(http, sign())}
    exercise = tools["log_cardio"]["inputSchema"]["properties"]["exercise"]["description"]
    for words in CARDIO_WORDS:
        assert words in exercise, words
    delete = tools["delete_entry"]["description"]
    for words in DELETE_WORDS + ("still needs the person's go-ahead first",):
        assert words in delete, words


# ---------------------------------------------------------------- edit_entry: null clears a cardio extra

CARDIO_EXTRAS = {"distance_km": 5.0, "incline_pct": 2.0, "avg_hr": 150, "machine_kcal": 300, "notes": "Hard"}


def cardio_now(entry_id: int) -> dict:
    return services.get_entry(one_user().id, "cardio_entry", entry_id)["entry"]


@pytest.fixture
def cardio(http) -> tuple[str, int]:
    """A token and one cardio entry with every extra set."""
    token = sign()
    entry = ok(http, token, "log_cardio", {"exercise": "Treadmill Run", "minutes": 30, **CARDIO_EXTRAS})
    return token, entry["id"]


@pytest.mark.parametrize("field", sorted(CARDIO_EXTRAS))
def test_a_cardio_extra_sent_as_null_is_cleared_and_the_rest_are_kept(http, cardio, field):
    token, entry_id = cardio
    before = cardio_now(entry_id)
    result = ok(http, token, "edit_entry", {"kind": "cardio_entry", "entry_id": entry_id, field: None})
    assert field in result["changed"]
    after = cardio_now(entry_id)
    assert after[field] in (None, "")  # notes clear to empty text
    for other in set(CARDIO_EXTRAS) - {field}:
        if not (field == "distance_km" and other == "speed_kmh"):
            assert after[other] == before[other], other


def test_a_null_speed_is_worked_out_from_distance_and_minutes(http):
    token = sign()
    entry_id = ok(http, token, "log_cardio", {"exercise": "Treadmill Run", "minutes": 30, "speed_kmh": 9})["id"]
    ok(http, token, "edit_entry", {"kind": "cardio_entry", "entry_id": entry_id, "distance_km": 5})
    assert cardio_now(entry_id)["speed_kmh"] == 9  # the stated speed is kept
    result = ok(http, token, "edit_entry", {"kind": "cardio_entry", "entry_id": entry_id, "speed_kmh": None})
    assert result["changed"] == ["speed_kmh"]
    assert cardio_now(entry_id)["speed_kmh"] == pytest.approx(5.0 / 30 * 60, abs=0.01)  # 10 km/h, from the distance


def test_fields_left_out_of_a_cardio_edit_are_untouched(http, cardio):
    token, entry_id = cardio
    before = cardio_now(entry_id)
    ok(http, token, "edit_entry", {"kind": "cardio_entry", "entry_id": entry_id, "minutes": 40})
    after = cardio_now(entry_id)
    assert {k: after[k] for k in CARDIO_EXTRAS} == {k: before[k] for k in CARDIO_EXTRAS}


def test_a_cardio_edit_with_nothing_sent_is_nothing_to_change(http, cardio):
    token, entry_id = cardio
    error = failed(http, token, "edit_entry", {"kind": "cardio_entry", "entry_id": entry_id})
    assert error["error"] == "nothing_to_change"


def test_edit_nulls_reach_the_service_only_for_the_cardio_extras_and_notes(http, monkeypatch):
    token = sign()
    whoami(http, token)
    received = spy_on_services(monkeypatch)
    nulls = {name: None for name in ("distance_km", "incline_pct", "speed_kmh", "avg_hr", "machine_kcal", "notes")}
    failed(http, token, "edit_entry", {"kind": "cardio_entry", "entry_id": 7, **nulls,
                                       "kcal": None, "meal": None, "date": None, "minutes": None})
    assert received["edit_entry"][0][3] == nulls  # food's fields, date and minutes sent as null are not passed


def test_the_cardio_extras_say_what_null_does_and_publish_no_default(http):
    props = {t["name"]: t["inputSchema"] for t in tool_list(http, sign())}["edit_entry"]["properties"]
    for name in ("distance_km", "incline_pct", "avg_hr", "machine_kcal"):
        assert "null clears it" in props[name]["description"] and "default" not in props[name], name
    assert "null works it out from distance and minutes" in props["speed_kmh"]["description"]
    assert "null clears them" in props["notes"]["description"]
    assert all("default" not in props[n] for n in ("speed_kmh", "notes"))


# ================================================================ stage 5: goals and trends

SITE = "https://spotter.example.app"

# The contract's shapes, key for key. Each model's published fields must be exactly these.
GOAL_NUMBER_KEYS = {"goal_id", "label", "kind", "exercise", "measurement_kind", "distance_km", "unit", "start_value",
                    "start_date", "direction", "target_value", "target_date", "latest", "status", "needed_per_week",
                    "actual_per_week", "projected_date", "projection", "note",
                    "is_sample",  # is_sample: beyond the contract's shape, sent by the services for rule 16
                    "should_be_today", "ahead_by", "days_ahead", "vs_plan", "plan_line"}
# The plan line's five fields (stage 6, E1): optional with a null default, so a result without them validates.
PLAN_LINE_KEYS = {"should_be_today", "ahead_by", "days_ahead", "vs_plan", "plan_line"}
CONTRACT_KEYS = {
    "GetTrendsResult": {"today", "bodyweight", "calories", "sessions", "cardio", "maintenance", "maintenance_note",
                        "includes_sample", "how", "summary", "links", "note"},
    "BodyweightTrends": {"now", "smoothed", "rate_per_week", "rate_window", "weigh_ins_28d", "series"},
    "RateWindow": {"from", "to", "days"},
    "SmoothedPoint": {"date", "kg", "smoothed"},
    "CaloriesTrends": {"avg_7d", "days_logged_7d", "avg_28d", "days_logged_28d", "estimated_share_pct_28d",
                       "target_kcal"},
    "SessionsTrends": {"this_week", "target_per_week", "per_week_4w"},
    "CardioTrends": {"minutes_this_week", "target_minutes", "minutes_per_week_4w"},
    "Maintenance": {"kcal", "intake_avg_kcal", "weight_change_kg", "days_logged", "window_days", "from", "to"},
    "TrendsLinks": {"progress", "goals", "settings"},
    "GetGoalsResult": {"today", "goals", "closed", "includes_sample", "how", "summary", "links", "note"},
    "ClosedGoal": {"goal_id", "label", "kind", "status", "closed_at", "target_value", "target_date",
                   "is_sample"},  # is_sample: beyond the contract's shape, as on GoalNumbers
    "GoalNumbers": GOAL_NUMBER_KEYS,
}
# Of the new shapes, only the top-level note is ever left out.
OPTIONAL_KEYS = {"GetTrendsResult": {"note"}, "GetGoalsResult": {"note"}, "GoalNumbers": PLAN_LINE_KEYS}


def numbers(goal_id: int, kind: str, label: str, unit: str, line: bool = True, **which) -> dict:
    """One goal's numbers in the contract's shape: with a trend line, or with too few points for one."""
    projection = {"slope_per_week": -0.3, "projected": 76.2, "status": "on track", "points_used": 4, "latest": 79.1,
                  "weeks_left": 8.0, "needed_per_week": -0.26, "projected_date": "2027-01-02"}
    return {"goal_id": goal_id, "label": label, "kind": kind, "exercise": which.get("exercise"),
            "measurement_kind": which.get("measurement_kind"), "distance_km": which.get("distance_km"), "unit": unit,
            "start_value": 80.0, "start_date": "2026-11-01", "direction": "down", "target_value": 77.0,
            "target_date": "2027-01-05", "latest": 79.1 if line else None,
            "status": "on track" if line else "not enough data", "needed_per_week": -0.26 if line else None,
            "actual_per_week": -0.3 if line else None, "projected_date": "2027-01-02" if line else None,
            "projection": projection if line else None,
            "note": None if line else "A line needs 4 points in the last 6 weeks; there is 1.", "is_sample": False}


EVERY_KIND = [
    numbers(1, "lift", "Back Squat 100 kg", "kg", exercise="Back Squat"),
    numbers(2, "bodyweight", "Body weight 75 kg", "kg"),
    numbers(3, "measurement", "Waist 80 cm", "cm", measurement_kind="waist"),
    numbers(4, "cardio_best", "Outdoor Run 5 km in 28:00", "min", line=False, exercise="Outdoor Run", distance_km=5.0),
]
SAMPLE_NOTE = "These numbers include sample data. The Remove sample data button on Settings takes it out."
TRENDS_LINKS = {"progress": f"{SITE}/progress", "goals": f"{SITE}/goals", "settings": f"{SITE}/settings"}
TRENDS_EMPTY = {
    "today": "2026-11-10", "bodyweight": None, "calories": None,
    "sessions": {"this_week": 0, "target_per_week": None, "per_week_4w": 0.0},
    "cardio": {"minutes_this_week": 0.0, "target_minutes": None, "minutes_per_week_4w": 0.0},
    "maintenance": None, "maintenance_note": "Needs food logged on 14 more of the last 21 days.",
    "includes_sample": False, "how": "How.", "summary": "Nothing logged yet.", "links": TRENDS_LINKS,
}
TRENDS_NO_RATE = {
    **TRENDS_EMPTY,
    "bodyweight": {"now": 80.2, "smoothed": 80.2, "rate_per_week": None, "rate_window": None, "weigh_ins_28d": 1,
                   "series": [{"date": "2026-11-10", "kg": 80.2, "smoothed": 80.2}]},
    "calories": {"avg_7d": None, "days_logged_7d": 0, "avg_28d": 2100, "days_logged_28d": 3,
                 "estimated_share_pct_28d": 0.0, "target_kcal": None},
    "maintenance_note": "Needs weigh-ins in the last 7 days and around 21 days ago.",
    "note": "The rate needs a weigh-in dated 14 to 42 days ago.",
}
TRENDS_FULL = {
    "today": "2026-11-10",
    "bodyweight": {"now": 79.1, "smoothed": 79.4, "rate_per_week": -0.31,
                   "rate_window": {"from": "2026-10-13", "to": "2026-11-10", "days": 28}, "weigh_ins_28d": 28,
                   "series": [{"date": "2026-11-09", "kg": 79.2, "smoothed": 79.43},
                              {"date": "2026-11-10", "kg": 79.0, "smoothed": 79.4}]},
    "calories": {"avg_7d": 2190, "days_logged_7d": 7, "avg_28d": 2205, "days_logged_28d": 28,
                 "estimated_share_pct_28d": 21.5, "target_kcal": 2200},
    "sessions": {"this_week": 3, "target_per_week": 4, "per_week_4w": 3.2},
    "cardio": {"minutes_this_week": 85.0, "target_minutes": 150, "minutes_per_week_4w": 83.8},
    "maintenance": {"kcal": 2530, "intake_avg_kcal": 2205, "weight_change_kg": -0.9, "days_logged": 21,
                    "window_days": 21, "from": "2026-10-21", "to": "2026-11-10"},
    "maintenance_note": None, "includes_sample": True, "how": "How.", "summary": "Down 0.31 kg a week.",
    "links": TRENDS_LINKS, "note": SAMPLE_NOTE,
}
GOALS_EMPTY = {"today": "2026-11-10", "goals": [], "closed": [], "includes_sample": False, "how": "How.",
               "summary": "No goals yet.", "links": {"goals": f"{SITE}/goals"}}
GOALS_FULL = {
    **GOALS_EMPTY, "goals": EVERY_KIND,
    "closed": [{"goal_id": 9, "label": "Bench Press 80 kg", "kind": "lift", "status": "dropped",
                "closed_at": "2026-11-01T10:00:00+00:00", "target_value": 80.0, "target_date": "2027-02-01",
                "is_sample": True}],
    "includes_sample": True, "summary": "Four open goals.", "note": SAMPLE_NOTE,
}


def goal_row(n: dict) -> dict:
    """The stored goal behind a goal's numbers, as set_goal returns it."""
    return {"id": n["goal_id"], "kind": n["kind"], "exercise_id": 3 if n["exercise"] else None,
            "exercise": n["exercise"], "measurement_kind": n["measurement_kind"], "distance_km": n["distance_km"],
            "unit": n["unit"], "start_value": n["start_value"], "start_date": n["start_date"],
            "direction": n["direction"], "target_value": n["target_value"], "target_date": n["target_date"],
            "status": "open", "text": "", "created_at": "2026-11-01T10:00:00+00:00",
            "updated_at": "2026-11-01T10:00:00+00:00", "closed_at": None, "is_sample": False}


def set_goal_result(n: dict) -> dict:
    return {"kind": "goal", "id": n["goal_id"], "goal": goal_row(n), "progress": n, "warnings": [],
            "links": {"goals": f"{SITE}/goals"}}


NEW_SHAPES = [("get_trends", TRENDS_EMPTY), ("get_trends", TRENDS_NO_RATE), ("get_trends", TRENDS_FULL),
              ("get_goals", GOALS_EMPTY), ("get_goals", GOALS_FULL),
              *[("set_goal", set_goal_result(n)) for n in EVERY_KIND]]
NEW_SHAPE_IDS = ["trends empty", "trends without a rate", "trends full", "goals empty", "goals full",
                 *[f"set_goal {n['kind']}" for n in EVERY_KIND]]


def test_the_new_models_have_exactly_the_contracts_keys():
    from app import mcp_schemas

    for name, keys in CONTRACT_KEYS.items():
        model = getattr(mcp_schemas, name)
        assert wire_fields(model) == keys, name
        required = {f.alias or n for n, f in model.model_fields.items() if f.is_required()}
        assert required == keys - OPTIONAL_KEYS.get(name, set()), name
    for name in ("get_log", "get_progress"):
        fields = RESULTS[name].model_fields
        assert {"includes_sample", "note"} <= wire_fields(RESULTS[name]), name
        assert fields["includes_sample"].is_required() and not fields["note"].is_required(), name


@pytest.mark.parametrize("name, result", NEW_SHAPES, ids=NEW_SHAPE_IDS)
def test_the_contracts_shapes_validate_with_their_nulls_and_nothing_else_does(name, result):
    model = RESULTS[name]
    model.model_validate(result)
    with pytest.raises(ValueError):
        model.model_validate({**result, "surprise": 1})
    for key in set(result) - {"note"}:
        with pytest.raises(ValueError):
            model.model_validate({k: v for k, v in result.items() if k != key})


@pytest.mark.parametrize("name, result", [
    ("get_log", {"from": "2026-11-10", "to": "2026-11-10", "days": [], "includes_sample": True, "summary": "s",
                 "totals": {"sessions": 0, "weigh_ins": 1, "measurements": 0, "food_entries": 0, "cardio_entries": 0},
                 "links": {"day": f"{SITE}/day/2026-11-10", "progress": f"{SITE}/progress"}, "note": SAMPLE_NOTE}),
    ("get_progress", {"today": "2026-11-10", "lifts": [], "goals": EVERY_KIND, "includes_sample": True,
                      "how": "How.", "links": {"progress": f"{SITE}/progress"}, "note": SAMPLE_NOTE}),
], ids=["get_log", "get_progress"])
def test_a_read_with_sample_rows_validates_with_its_note(name, result):
    RESULTS[name].model_validate(result)
    with pytest.raises(ValueError):
        RESULTS[name].model_validate({**result, "includes_sample": "yes, some"})


def test_the_new_fields_are_published(http):
    out = {t["name"]: t["outputSchema"] for t in tool_list(http, sign())}
    for name in ("get_log", "get_progress", "get_trends", "get_goals"):
        assert {"includes_sample", "note"} <= set(out[name]["properties"]), name
    for name in ("get_log", "get_progress", "get_trends", "get_goals"):
        assert "includes_sample" in out[name]["required"] and "note" not in out[name]["required"], name
    for name in ("set_goal", "get_goals", "get_progress"):
        assert GOAL_NUMBER_KEYS == set(out[name]["$defs"]["GoalNumbers"]["properties"]), name


# ---------------------------------------------------------------- stage 6 step 3: the plan line's five fields

def planned(n: dict, should: float, ahead: float | None, days: int | None, vs: str | None, line: str | None) -> dict:
    """A goal's numbers with the plan line's five fields. Invented figures, in the shapes of section E1."""
    return {**n, "should_be_today": should, "ahead_by": ahead, "days_ahead": days, "vs_plan": vs, "plan_line": line}


PLANNED = [
    planned(EVERY_KIND[0], 105.2, 2.7, 9, "ahead", "107.9 kg now against 105.2 kg on the plan line today: 2.7 kg ahead."),
    planned(EVERY_KIND[1], 80.3, 0.0, 0, "on the line", "On the plan line today, at 80.3 kg."),
    planned(EVERY_KIND[2], 80.1, -0.06, -2, "behind",  # rule 2: one more decimal in the sentence and in ahead_by
            "80.14 cm now against 80.08 cm on the plan line today: 0.06 cm behind."),
    planned(EVERY_KIND[3], 28.5, None, None, None, None),  # nothing to compare: only the line today
    planned({**EVERY_KIND[0], "goal_id": 5}, 120.0, -3.1, None, "behind",  # the target date has passed
            "116.9 kg now against the target of 120 kg: 3.1 kg behind."),
    planned({**EVERY_KIND[1], "goal_id": 6}, 78.0, 0.0, None, "on the line",  # ended, and at the target
            "78 kg now, at the target."),
    planned({**EVERY_KIND[1], "goal_id": 7}, 75.0, 0.1, 0, "ahead",  # off the line, under half a day's change
            "74.9 kg now against 75 kg on the plan line today: 0.1 kg ahead."),
]
LIFT_DETAIL = {
    "exercise": "Back Squat", "sessions": 3, "last_trained": "2026-11-10",
    "estimated_max_over_time": [{"date": "2026-11-10", "estimated_max_kg": 107.9}],
    "current_estimated_max_kg": 107.9,
    "best_set": {"weight_kg": 95.0, "reps": 4, "date": "2026-11-10", "estimated_max_kg": 107.9},
    "bests": [], "trend": None, "goal": PLANNED[0], "goals": [PLANNED[0], PLANNED[4]],
}
PLANNED_RESULTS = [
    *[("set_goal", set_goal_result(n)) for n in PLANNED],
    ("get_goals", {**GOALS_FULL, "goals": PLANNED}),
    ("get_progress", {"today": "2026-11-10", "lifts": [], "goals": PLANNED, "includes_sample": False,
                      "how": "How.", "links": {"progress": f"{SITE}/progress"}}),
    ("get_progress", {"today": "2026-11-10", "lift": LIFT_DETAIL, "includes_sample": False, "how": "How.",
                      "links": {"progress_lift": f"{SITE}/progress/Back%20Squat"}}),
]
PLANNED_IDS = [*[f"set_goal {n['goal_id']} {n['kind']} {n['vs_plan']}" for n in PLANNED], "get_goals",
               "get_progress goals",
               "get_progress lift"]


@pytest.mark.parametrize("name, result", PLANNED_RESULTS, ids=PLANNED_IDS)
def test_the_plan_line_fields_are_modelled_at_every_depth_nulls_and_all(name, result):
    model = RESULTS[name]
    assert unmodelled(result, model, name) == set()
    parsed = model.model_validate(result).model_dump(by_alias=True)
    goals = {"set_goal": lambda r: [r["progress"]], "get_goals": lambda r: r["goals"],
             "get_progress": lambda r: r.get("goals") or [r["lift"]["goal"], *r["lift"]["goals"]]}[name]
    sent, kept = goals(result), goals(parsed)
    assert sent and [{k: g[k] for k in PLAN_LINE_KEYS} for g in kept] == [
        {k: g[k] for k in PLAN_LINE_KEYS} for g in sent]  # every depth keeps the five as sent, nulls included


def test_goal_numbers_without_the_plan_line_still_validate_and_a_wrong_type_does_not():
    from app.mcp_schemas import GoalNumbers

    for n in EVERY_KIND:  # today's services: no plan-line keys at all
        parsed = GoalNumbers.model_validate(n)
        assert all(getattr(parsed, k) is None for k in PLAN_LINE_KEYS), n["kind"]
    for key, wrong in (("should_be_today", "80 kg"), ("ahead_by", "ahead"), ("days_ahead", 2.5), ("vs_plan", 1),
                       ("plan_line", ["a", "b"])):
        with pytest.raises(ValueError):
            GoalNumbers.model_validate({**PLANNED[0], key: wrong})


def test_the_plan_line_fields_are_published_optional_and_described(http):
    out = {t["name"]: t["outputSchema"] for t in tool_list(http, sign())}
    for name in ("set_goal", "get_goals", "get_progress"):
        goal = out[name]["$defs"]["GoalNumbers"]
        assert PLAN_LINE_KEYS <= set(goal["properties"]), name
        assert not PLAN_LINE_KEYS & set(goal.get("required", [])), name
        for key in PLAN_LINE_KEYS:
            assert goal["properties"][key]["description"], (name, key)
    words = out["get_goals"]["$defs"]["GoalNumbers"]["properties"]
    assert all(w in words["vs_plan"]["description"] for w in ("ahead", "on the line", "behind"))
    assert "say as written" in words["plan_line"]["description"]
    assert "one more decimal" in words["plan_line"]["description"]
    assert "0 on the line" in words["ahead_by"]["description"]
    assert "0 on the line while the line runs" in words["days_ahead"]["description"]
    # The reviewed cases (stage 6 step 3 fix round): each one a sentence that was missing or false before.
    assert "the target when target_date is on or before start_date" in words["should_be_today"]["description"]
    assert "Once the target date has passed, or when the line has no length, it is measured against the target" \
        in words["ahead_by"]["description"]
    assert "0 also comes with ahead or behind, when the gap is under half a day's change" \
        in words["days_ahead"]["description"]
    assert "for a cardio_best time under half a second" in words["vs_plan"]["description"]
    assert "or against the target once the target date has passed or when the line has no length" \
        in words["vs_plan"]["description"]
    assert "Once the target date has passed, or when the line has no length, it compares latest with the target" \
        in words["plan_line"]["description"]
    assert "'78 kg now, at the target.'" in words["plan_line"]["description"]


def test_can_and_cant_names_the_plan_line_its_fields_and_the_rounded_cardio_weight():
    text = " ".join(CAN_AND_CANT.read_text(encoding="utf-8").split())
    for words in ("a straight line from the goal's start to its target",
                  "The plan line and the status answer different questions",
                  "The status compares the trend's projection with the target",
                  "`estimate_weight_kg` is rounded with `estimate.weight_kg`",
                  "A goal's `should_be_today` and `ahead_by` round at the goal unit's step: 1 decimal for kg and cm, "
                  "whole bpm, minutes to 2 decimals.",
                  "`ahead_by` takes one more decimal when latest and `should_be_today` would otherwise read the "
                  "same, never for a cardio time.",
                  *(f"`{key}`" for key in sorted(PLAN_LINE_KEYS))):
        assert words in text, words


@pytest.mark.parametrize("name, result", PLANNED_RESULTS[-3:], ids=PLANNED_IDS[-3:])
def test_a_goal_read_with_the_plan_line_answers_through_the_wrapper(http, monkeypatch, name, result):
    monkeypatch.setattr(services, name, lambda *args, **kwargs: result)
    arguments = {"exercise": "Back Squat"} if "lift" in result else None
    assert ok(http, sign(), name, arguments) == result  # the wrapper's check and the library's both pass it


@pytest.mark.parametrize("name, result", [s for s in NEW_SHAPES if s[0] != "set_goal"],
                         ids=[i for i in NEW_SHAPE_IDS if not i.startswith("set_goal")])
def test_a_new_read_answers_through_the_wrapper_nulls_and_all(http, monkeypatch, name, result):
    received = []

    def fake(*args, **kwargs):
        received.append((args, kwargs))
        return result

    monkeypatch.setattr(services, name, fake, raising=False)
    token = sign()
    assert ok(http, token, name) == result  # the wrapper's check and the library's both pass it
    assert received == [((one_user().id,), {})]


@pytest.mark.parametrize("goal", EVERY_KIND, ids=[n["kind"] for n in EVERY_KIND])
def test_set_goal_of_every_kind_answers_through_the_wrapper(http, monkeypatch, goal):
    received = []
    monkeypatch.setattr(services, "set_goal", lambda account, data, by: received.append(data) or set_goal_result(goal))
    arguments = {"kind": goal["kind"], "target_value": goal["target_value"], "target_date": goal["target_date"],
                 **{k: goal[k] for k in ("exercise", "measurement_kind", "distance_km") if goal[k] is not None}}
    assert ok(http, sign(), "set_goal", arguments) == set_goal_result(goal)
    assert received == [arguments]  # every argument sent, and nothing left out sent as null


def test_the_new_reads_run_at_read_only(http, monkeypatch):
    token = sign()
    whoami(http, token)
    set_switch(one_user().id, "read")
    received = spy_on_services(monkeypatch)
    for name in ("get_trends", "get_goals"):
        assert failed(http, token, name)["error"] == "spy" and name in received, name


# ---------------------------------------------------------------- stage 5: the sentences Claude reads

STAGE_5_WORDS = ("To answer how the person is doing, call get_trends", "whether the person is on track, call get_goals",
                 "takes the last day of that month as target_date", "worked out from today's date",
                 "the person's own clicks on Settings", "no function does either", "Never offer to do them",
                 "the count goes in servings or grams, never in the description", "send the food's name")


def test_every_stage_5_sentence_is_in_the_instructions():
    text = mcp_server.INSTRUCTIONS
    for rule in (mcp_server.TRENDS_RULE, mcp_server.GOALS_RULE, mcp_server.MONTH_END_RULE,
                 mcp_server.SETTINGS_ONLY_RULE, mcp_server.FOOD_NAME_RULE):
        assert rule in text, rule
    for words in STAGE_5_WORDS:
        assert words in text, words


# ---------------------------------------------------------------- stage 6: how Claude replies (T8)

# The contract's paragraph, word for word (stage-6.md, section D). Step 3 adds its plan_line sentence.
REPLY_PARAGRAPH = (
    "How to reply: plain words, one fact a line. Quote each number as Spotter gives it, with its unit: never add "
    "decimals, never round further, and never recalculate one yourself. Where a summary gives a figure, say the "
    "summary's figure. Say \"about\" as a word, never as a symbol. No dashes between clauses: use a colon, a comma "
    "or a full stop. Name an estimate as an estimate. Where a goal carries plan_line, say it as written, and do "
    "not also quote latest, should_be_today or ahead_by."
)
# In the rule 2 case plan_line carries one more decimal than latest: a reply must not give two figures for now.
PLAN_LINE_SENTENCE = ("Where a goal carries plan_line, say it as written, and do not also quote latest, "
                      "should_be_today or ahead_by.")
# The estimate sentence, with the one exception the food rule makes (stage 6 step 3).
ESTIMATE_SENTENCE = "Quote Spotter's numbers; never estimate your own, except a food's kcal, as the food rule says."


def test_the_instructions_carry_the_reply_paragraph():
    assert mcp_server.REPLY_RULE == REPLY_PARAGRAPH
    assert REPLY_PARAGRAPH in mcp_server.INSTRUCTIONS


def test_the_plan_line_sentence_ends_the_reply_paragraph_and_names_a_modelled_field():
    # Step 2 held plan_line out of the instructions; step 3 models it, so the paragraph ends with it.
    assert mcp_server.REPLY_RULE.endswith(" " + PLAN_LINE_SENTENCE)
    assert mcp_server.INSTRUCTIONS.endswith(" " + PLAN_LINE_SENTENCE)  # the last sentence of the instructions
    assert mcp_server.INSTRUCTIONS.count("plan_line") == 1
    from app.mcp_schemas import GoalNumbers

    assert "plan_line" in GoalNumbers.model_fields  # the instructions never name a field no result carries


def test_the_estimate_sentence_leaves_room_for_the_food_rule():
    text = mcp_server.INSTRUCTIONS
    assert ESTIMATE_SENTENCE in text
    assert "never estimate your own. " not in text  # the old sentence, with no exception, is gone
    assert "you may estimate, and must say so" in mcp_server.FOOD_RULE and mcp_server.FOOD_RULE in text


def test_the_descriptions_carry_the_same_sentences(http):
    tools = {t["name"]: t for t in tool_list(http, sign())}
    assert mcp_server.TRENDS_RULE in tools["get_trends"]["description"]
    assert mcp_server.GOALS_RULE in tools["get_goals"]["description"]
    for name in ("get_trends", "get_goals"):
        assert "Quote these numbers" in tools[name]["description"], name
    goal = tools["set_goal"]
    assert mcp_server.MONTH_END_RULE in goal["description"]
    assert mcp_server.MONTH_END_RULE in goal["inputSchema"]["properties"]["target_date"]["description"]
    for kind in ("lift", "bodyweight", "measurement", "cardio_best"):
        assert kind in goal["inputSchema"]["properties"]["kind"]["description"], kind
    target = goal["inputSchema"]["properties"]["target_value"]["description"]
    for words in ("A lift: kg", "Body weight: kg", "A measurement: its own unit", "A cardio_best: minutes"):
        assert words in target, words
    for kind in ("waist", "hips", "chest", "arm", "thigh", "resting_hr"):
        assert kind in goal["inputSchema"]["properties"]["measurement_kind"]["description"], kind
    assert "cardio_best" in goal["inputSchema"]["properties"]["distance_km"]["description"]
    food = tools["log_food"]
    entry = food["inputSchema"]["$defs"]["FoodEntryIn"]
    assert mcp_server.FOOD_NAME_RULE in food["description"]
    assert mcp_server.FOOD_NAME_RULE in entry["properties"]["description"]["description"]
    assert "description" not in entry.get("required", [])  # optional: a saved or USDA food defaults to its name


def test_can_and_cant_states_the_goal_kinds_the_trends_sample_rows_and_the_settings_clicks():
    text = " ".join(CAN_AND_CANT.read_text(encoding="utf-8").split())
    for words in ("`lift`", "`bodyweight`", "`measurement`", "`cardio_best`",
                  "The best session in the last 28 days, else the latest session's",
                  "the 7-day average, else the latest weigh-in", "The latest value of that kind",
                  "The best time for the distance in the last 28 days, else the latest",
                  "Steps, sleep hours and water are a day's amount", "the last day of that month",
                  "exponentially weighted moving average", "alpha 0.1",
                  "the latest, dated in the last 7 days, and the last one dated 14 to 42 days before today",
                  "food logged on at least 14 of the last 21 days", "7,700 kcal a kg",
                  "it never sets the calorie target", "The projected date",
                  "more than 2 years from today", "`includes_sample` is true", "Remove sample data",
                  "the person's own clicks on Settings", "No function does either", "Claude never offers to do them",
                  "stays valid until it expires, an hour at most", "recreates an empty account",
                  "`too_many_reads`", "Nineteen functions", "`get_trends`", "`get_goals`"):
        assert words in text, words
    lines = CAN_AND_CANT.read_text(encoding="utf-8").splitlines()
    rows = [m.group(1) for line in lines if (m := re.match(r"\| `([a-z_]+)` \| ", line))]
    assert [r for r in rows if r in FUNCTIONS] == FUNCTIONS  # the function table: one row each, in order
    read_row = next(line for line in lines if line.startswith("| `read` |"))
    assert set(re.findall(r"`([a-z_]+)`", read_row)) - {"read"} == {n for n, level in CONTRACT_NEEDS.items()
                                                                     if level == "read"}


# ---------------------------------------------------------------- stage 5: find_food's read limit

def test_too_many_reads_reaches_claude_as_an_error_with_the_wait(http, monkeypatch):
    refusal = {"error": "too_many_reads", "message": "120 lookups in the last 10 minutes.",
               "fix": "Wait 3 minutes, then retry.", "wait_minutes": 3}
    monkeypatch.setattr(services, "find_food", lambda *a, **k: refusal)
    result = call(http, sign(), "find_food", {"query": "dal"})
    assert result["isError"] is True and result["structuredContent"] == refusal
    assert result["content"][0]["text"] == compact(refusal)
    with db.session_scope() as session:
        row = session.scalars(select(CallLog)).one()
        assert (row.function, row.ok, row.error_code) == ("find_food", False, "too_many_reads")


def test_the_real_read_limit_reaches_claude(http):
    token = sign()
    account = whoami(http, token)["account_id"]
    now = db.utcnow()
    with db.session_scope() as session:
        session.add_all(CallLog(function="find_food", started_at=now, ms=1, ok=True, error_code=None,
                                result_bytes=10, user_id=account) for _ in range(125))
    error = failed(http, token, "find_food", {"query": "dal"})
    assert error["error"] == "too_many_reads" and error["fix"]


# ---------------------------------------------------------------- stage 5: with the real services

def test_set_goal_of_every_kind_through_mcp(http):
    token = sign()
    today = date.fromisoformat(whoami(http, token)["today"])
    by = (today + timedelta(days=90)).isoformat()
    ok(http, token, "log_lifts", {"sets": squat(90)})
    ok(http, token, "log_bodyweight", {"kg": 80})
    ok(http, token, "log_measurement", {"kind": "waist", "value": 88})
    ok(http, token, "log_cardio", {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5})
    cases = [({"kind": "lift", "exercise": "Back Squat", "target_value": 120}, "kg", "up"),  # now: 105, Epley
             ({"kind": "bodyweight", "target_value": 75}, "kg", "down"),
             ({"kind": "measurement", "measurement_kind": "waist", "target_value": 80}, "cm", "down"),
             ({"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_value": 28}, "min", "down")]
    ids = []
    for arguments, unit, direction in cases:
        result = ok(http, token, "set_goal", {**arguments, "target_date": by})
        progress = result["progress"]
        assert set(progress) == GOAL_NUMBER_KEYS, arguments["kind"]  # the five plan-line keys included
        assert (progress["kind"], progress["unit"], progress["direction"]) == (arguments["kind"], unit, direction)
        assert progress["measurement_kind"] == arguments.get("measurement_kind")
        assert progress["distance_km"] == arguments.get("distance_km")
        assert result["goal"]["unit"] == unit and progress["start_value"] is not None
        ids.append(result["id"])
    goals = ok(http, token, "get_goals")["goals"]
    assert [g["goal_id"] for g in goals] == ids
    assert [g["label"] for g in goals][2:] == ["Waist 80 cm", "Outdoor Run 5 km in 28:00"]


def test_set_goal_refusals_carry_fixes_that_work(http):
    token = sign()
    today = date.fromisoformat(whoami(http, token)["today"])
    by = (today + timedelta(days=90)).isoformat()
    steps = failed(http, token, "set_goal", {"kind": "measurement", "measurement_kind": "steps", "target_value": 10000,
                                             "target_date": by})
    assert steps["fix"] and "waist" in steps["fix"]
    bare = failed(http, token, "set_goal", {"kind": "measurement", "measurement_kind": "waist", "target_value": 80,
                                            "target_date": by})
    assert "start_value" in bare["fix"]  # nothing logged to start from
    ok(http, token, "set_goal", {"kind": "measurement", "measurement_kind": "waist", "target_value": 80,
                                 "target_date": by, "start_value": 88})
    no_distance = failed(http, token, "set_goal", {"kind": "cardio_best", "exercise": "Outdoor Run", "target_value": 28,
                                                   "target_date": by, "start_value": 31})
    assert "distance_km" in no_distance["fix"]
    ok(http, token, "set_goal", {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5,
                                 "target_value": 28, "target_date": by, "start_value": 31})


def test_the_new_reads_are_the_services_own_dicts(http):
    token = sign()
    ok(http, token, "log_bodyweight", {"kg": 80})
    ok(http, token, "set_goal", {"kind": "bodyweight", "target_value": 75, "target_date": "2099-01-01"})
    account = one_user().id
    assert ok(http, token, "get_trends") == services.get_trends(account)
    assert ok(http, token, "get_goals") == services.get_goals(account)
    assert ok(http, token, "get_goals", {"include_closed": True}) == services.get_goals(account, include_closed=True)

# ================================================================ import_entries through MCP (import pass B)

def import_person(http) -> tuple[str, date]:
    """A person with a time zone, as an import needs one. Returns their token and their today."""
    token = sign()
    ok(http, token, "update_settings", {"time_zone": "Asia/Kolkata"})
    return token, date.fromisoformat(ok(http, token, "whoami")["today"])


def entry_rows() -> int:
    """Every entry row of every person, deleted ones too."""
    with db.session_scope() as session:
        return sum(session.scalar(select(func.count()).select_from(m)) for m in services.entry_kinds().values())


def import_calls() -> int:
    with db.session_scope() as session:
        return session.scalar(select(func.count()).select_from(CallLog).where(CallLog.function == "import_entries"))


def logged_by_values(value: Any) -> list[str]:
    """Every logged_by in a result, at any depth."""
    if isinstance(value, list):
        return [v for item in value for v in logged_by_values(item)]
    if not isinstance(value, dict):
        return []
    own = [value["logged_by"]] if "logged_by" in value else []
    return own + [v for k, item in value.items() if k != "logged_by" for v in logged_by_values(item)]


def test_import_entries_publishes_named_fields_and_a_strict_dry_run_with_no_default(http):
    from app.services import imports

    schema = {t["name"]: t["inputSchema"] for t in tool_list(http, sign())}["import_entries"]
    assert list(schema["properties"]) == ["rows", "dry_run", "weight_unit", "distance_unit", "length_unit"]
    assert set(schema["properties"]) == set(imports.CALL_FIELDS) and schema["required"] == ["rows"]
    dry = schema["properties"]["dry_run"]
    assert dry["type"] == "boolean" and "default" not in dry and "anyOf" not in dry  # left out is the preview
    rows = schema["properties"]["rows"]
    assert (rows["minItems"], rows["maxItems"]) == (1, 200) and rows["items"] == {"$ref": "#/$defs/ImportRowIn"}
    # Named fields for every kind: each row, set and food entry is an object, never a positional list.
    row, one_set, one_food = (schema["$defs"][n] for n in ("ImportRowIn", "ImportSetIn", "ImportFoodIn"))
    assert list(row["properties"]) == ["kind", "date", "sets", "notes", "weight", "measurement_kind", "value",
                                       "entries", "exercise", "minutes", "distance", "incline_pct", "speed", "avg_hr",
                                       "machine_kcal"]
    assert row["required"] == ["kind", "date"]
    assert set(row["properties"]) - {"kind", "date"} == {f for fields in imports.ROW_FIELDS.values() for f in fields}
    assert set(one_set["properties"]) == set(imports.SET_FIELDS)
    assert set(one_set["required"]) == {"exercise", "weight", "reps"}
    assert set(one_food["properties"]) == set(imports.FOOD_FIELDS) == set(one_food["required"])
    assert {"speed_kmh", "distance_km", "kg"}.isdisjoint(row["properties"])
    assert "distance_unit an hour" in row["properties"]["speed"]["description"]


def test_who_logged_it_names_import_wherever_a_result_says(http):
    outputs = {t["name"]: t["outputSchema"] for t in tool_list(http, sign())}
    said = {(name, owner): spec["description"] for name, out in outputs.items()
            for owner, obj in [("", out), *out.get("$defs", {}).items()]
            for prop, spec in obj.get("properties", {}).items() if prop == "logged_by"}
    assert {owner for _, owner in said} == {"Session", "WeighIn", "MeasurementOut", "LogSession"}
    assert set(said.values()) == {"Who logged it: claude, web or import."}


@pytest.mark.parametrize("value", ["false", "no", 0, 1, "true", "off", "", None],
                         ids=["'false'", "'no'", "0", "1", "'true'", "'off'", "blank", "null"])
def test_a_dry_run_that_is_not_a_bool_is_refused_before_the_service_and_writes_nothing(http, value):
    """pydantic's default reads "no", "off" and 0 as false. Strict, the schema refuses each, so a loose value
    never reaches the service as a bool: no call-log row, nothing written."""
    token, today = import_person(http)
    row = {"kind": "bodyweight", "date": (today - timedelta(days=3)).isoformat(), "weight": 80}
    result = call(http, token, "import_entries", {"rows": [row], "dry_run": value})
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert re.search(r"^dry_run$", text, re.MULTILINE) and "valid boolean" in text, text
    assert import_calls() == 0  # the wrapper never ran: the library refused the argument
    assert entry_rows() == 0


def test_dry_run_true_false_or_left_out(http):
    token, today = import_person(http)
    row = {"kind": "bodyweight", "date": (today - timedelta(days=3)).isoformat(), "weight": 80}
    for arguments in ({"rows": [row]}, {"rows": [row], "dry_run": True}):
        preview = ok(http, token, "import_entries", arguments)
        assert preview["dry_run"] is True and preview["summary"].startswith("Preview, nothing written.")
        assert entry_rows() == 0
    written = ok(http, token, "import_entries", {"rows": [row], "dry_run": False})
    assert written["dry_run"] is False and written["row_outcomes"]["written"] == 1 and entry_rows() == 1
    assert import_calls() == 3  # one call-log row a call, a preview too: each is one write of the limit


def test_the_wrapper_passes_the_fields_sent_and_a_row_as_sent(http, monkeypatch):
    token = sign()
    whoami(http, token)
    account = one_user().id
    received = spy_on_services(monkeypatch)
    row = {"kind": "cardio_entry", "date": "2026-01-04", "exercise": "Outdoor Run", "minutes": 30.0, "distance": 3.1}
    failed(http, token, "import_entries", {"rows": [row]})
    assert received["import_entries"] == ((account, {"rows": [row]}, "claude"), {})  # no dry_run, no unit added
    failed(http, token, "import_entries", {"rows": [row], "dry_run": True, "weight_unit": "LB"})
    assert received["import_entries"] == ((account, {"rows": [row], "dry_run": True, "weight_unit": "LB"}, "claude"),
                                          {})


def test_a_field_a_row_does_not_take_reaches_the_service_and_fails_its_row(http):
    """A row, set or food entry field the schema does not name is passed on, not dropped: the service refuses its
    row in its own words. Dropped, a misspelt speed_kmh or distance_km would go in as a row without it."""
    token, today = import_person(http)
    on = (today - timedelta(days=3)).isoformat()
    r = ok(http, token, "import_entries", {"rows": [
        {"kind": "cardio_entry", "date": on, "exercise": "Outdoor Run", "minutes": 30, "speed_kmh": 10},
        {"kind": "cardio_entry", "date": on, "exercise": "Outdoor Run", "minutes": 30, "distance_km": 5},
        {"kind": "bodyweight", "date": on, "weight": 80, "kg": 80},
        {"kind": "lift_session", "date": on, "sets": [{"exercise": "Deadlift", "weight": 100, "reps": 5,
                                                       "weight_kg": 100}]},
        {"kind": "food_entry", "date": on, "entries": [{"description": "Oats", "kcal": 350, "meal": "breakfast",
                                                         "estimated": True}]}]})
    assert [f["message"] for f in r["failed"]] == [
        "Row 1: 'speed_kmh' is not a field of a cardio_entry row.",
        "Row 2: 'distance_km' is not a field of a cardio_entry row.",
        "Row 3: 'kg' is not a field of a bodyweight row.", "Row 4, set 1: 'weight_kg' is not a field of a set.",
        "Row 5, entry 1: 'estimated' is not a field of an imported food entry."]
    assert "speed" in r["failed"][0]["fix"].split(": ")[1].split(", ")
    assert r["row_outcomes"]["failed"] == 5 and entry_rows() == 0


def test_a_misspelt_call_field_is_dropped_by_the_library_and_the_preview_shows_it(http):
    """As built: the MCP library drops an argument the function does not name, before the wrapper runs, so the
    service's own refusal of weight_units is never reached through MCP. The preview is the guard: its units and its
    ranges show 176 read as kg."""
    token, today = import_person(http)
    row = {"kind": "bodyweight", "date": (today - timedelta(days=3)).isoformat(), "weight": 176}
    r = ok(http, token, "import_entries", {"rows": [row], "weight_units": "lb"})
    assert r["units"]["weight"] == "kg" and r["ranges"]["bodyweight_kg"] == {"lowest": 176, "highest": 176}
    assert "Every weigh-in is 176 kg." in r["summary"] and entry_rows() == 0


def test_squat_barbell_gets_only_the_closest_name_the_rule_gives(http):
    """The instructions promise no closest name: for Strong's "Squat (Barbell)" the rule offers Barbell Row."""
    token, today = import_person(http)
    r = ok(http, token, "import_entries", {"rows": [
        {"kind": "lift_session", "date": (today - timedelta(days=3)).isoformat(),
         "sets": [{"exercise": "Squat (Barbell)", "weight": 100, "reps": 5}]}]})
    assert r["unknown_names"] == [{"name": "Squat (Barbell)", "kind": "lift", "rows": 1, "closest": ["Barbell Row"]}]
    assert r["failed"][0]["closest"] == ["Barbell Row"]
    assert "Back Squat" not in mcp_server.IMPORT_RULE and "Barbell Row" not in mcp_server.IMPORT_RULE


def test_the_import_rule_is_in_the_instructions_after_the_cardio_rule():
    from . import text_rules as tr

    text, rule = mcp_server.INSTRUCTIONS, mcp_server.IMPORT_RULE
    assert "Importing: " + rule + " Deleting: " in text
    assert text.index(mcp_server.CARDIO_RULE) < text.index(rule) < text.index(mcp_server.DELETE_RULE)
    for words in ("up to 200 rows and up to 1,000 sets and food entries a call",
                  "the 1,000 sets and food entries run out before the 200 rows", "send times for sets that repeat",
                  "Send the first call with dry_run true", "Write only when the person says yes, with dry_run false",
                  "never convert them yourself", "A row's speed is in the call's distance unit an hour",
                  "ask the person once per name", "The closest names the result gives can be wrong",
                  "Never pick one on a guess", "An import never replaces an entry",
                  "Sent again, the same rows write nothing that is already there",
                  "an imported entry the person edited, moved or deleted counts as already there",
                  "One food case is the exception: spotter://can-and-cant names it."):
        assert words in rule, words
    assert "Claude" not in rule and ";" not in rule and "Settings" not in rule
    assert tr.check(rule, tr.CLAUDE) == []
    assert not [s for s in sentences(rule) if sends_to_a_page(s)]


def test_an_imported_row_of_every_kind_reads_back_through_mcp_as_import(http):
    """Rows of all five kinds imported through MCP, then read with every function that shows an entry: each result
    passes its schema at every depth, and wherever it says who logged a row, it says import."""
    token, today = import_person(http)
    set_switch(one_user().id, "log_delete")
    on = {n: (today - timedelta(days=n)).isoformat() for n in (1, 2, 3)}
    rows = [{"kind": "bodyweight", "date": on[2], "weight": 80.5},
            {"kind": "measurement", "date": on[2], "measurement_kind": "waist", "value": 84.5},
            {"kind": "lift_session", "date": on[3], "sets": [{"exercise": "Back Squat", "weight": 100, "reps": 5,
                                                             "times": 3}]},
            {"kind": "food_entry", "date": on[2], "entries": [{"description": "Oats", "kcal": 350, "meal": "breakfast"}]},
            {"kind": "cardio_entry", "date": on[1], "exercise": "Treadmill Walk", "minutes": 30, "distance": 2.5}]
    assert ok(http, token, "import_entries", {"rows": rows, "dry_run": False})["row_outcomes"]["written"] == 5
    seen: list[tuple[str, dict]] = []

    def read(name: str, arguments: dict | None = None) -> dict:
        result = ok(http, token, name, arguments)
        RESULTS[name].model_validate(result)
        assert unmodelled(result, RESULTS[name], name) == set(), name
        seen.append((name, result))
        return result

    log = read("get_log", {"days": 14})
    day = {d["date"]: d for d in log["days"]}
    assert [s["logged_by"] for s in day[on[3]]["sessions"]] == ["import"]
    assert day[on[2]]["weigh_in"]["logged_by"] == "import"
    assert [m["logged_by"] for m in day[on[2]]["measurements"]] == ["import"]
    assert [c["logged_by"] for c in day[on[1]]["cardio"]] == ["import"]
    food = day[on[2]]["food"]["meals"]["breakfast"]
    assert [(e["description"], e["source"], e["estimated"]) for e in food] == [("Oats", "you_said", False)]
    ids = {"lift_session": day[on[3]]["sessions"][0]["id"], "bodyweight": day[on[2]]["weigh_in"]["id"],
           "measurement": day[on[2]]["measurements"][0]["id"], "food_entry": food[0]["id"],
           "cardio_entry": day[on[1]]["cardio"][0]["id"]}
    assert read("get_trends")["bodyweight"]["now"] == 80.5
    assert [line["exercise"] for line in read("get_progress")["lifts"]] == ["Back Squat"]
    assert read("get_progress", {"exercise": "Back Squat"})["lift"]["sessions"] == 1
    assert len(read("get_progress", {"exercise": "Treadmill Walk"})["cardio"]["sessions"]) == 1
    assert read("find_food", {"query": "Oats"})["recent"] == []  # an imported entry is not one of the person's own
    changes = {"lift_session": {"notes": "Edited"}, "bodyweight": {"kg": 81}, "measurement": {"value": 85},
               "food_entry": {"kcal": 360}, "cardio_entry": {"minutes": 31}}
    for kind, entry_id in ids.items():
        edited = read("edit_entry", {"kind": kind, "entry_id": entry_id, **changes[kind]})
        entries = [edited["before"], edited["after"], read("delete_entry", {"kind": kind, "entry_id": entry_id})["entry"],
                   read("restore_entry", {"kind": kind, "entry_id": entry_id})["entry"]]
        # A food entry's shape carries no logged_by anywhere (FoodEntryOut, food.entry_dict): its tag says who
        # supplied the number instead. Every other kind says import, before and after the edit, deleted and back.
        want = [None] * 4 if kind == "food_entry" else ["import"] * 4
        assert [e.get("logged_by") for e in entries] == want, kind
        assert [e["deleted_at"] is None for e in entries] == [True, True, False, True], kind
    assert {v for _, result in seen for v in logged_by_values(result)} == {"import"}
    assert {name for name, result in seen if logged_by_values(result)} == {"get_log", "edit_entry", "delete_entry",
                                                                           "restore_entry"}

def test_can_and_cant_states_the_import_as_built():
    from app.services import imports

    text = " ".join(CAN_AND_CANT.read_text(encoding="utf-8").split())
    for words in ("`import_entries`", "marked `import` in `logged_by`", "With `dry_run` true or left out, nothing is "
                  "written", "`dry_run` is true, false or left out: any other value is refused",
                  "lb x 0.45359237, mi x 1.609344, in x 2.54", "A row's `speed` is in the call's distance unit an hour",
                  f"| Rows a call | {imports.ROWS[0]} to {imports.ROWS[1]} |",
                  "| Sets and food entries a call | 1,000 at most, together |",
                  "| Imported rows in any 24 hours | 10,000, deleted ones counted.", "166 days",
                  "for \"Squat (Barbell)\" the closest is Barbell Row", "Version 1 has no undo",
                  "remembered by its own content and date", "it comes back on its first day",
                  "If they edit it, delete it, and the same file is sent again, it comes back too",
                  "a later export holds fewer entries for that first day",
                  "| Edited an imported row | Already there: the edit stands |",
                  "| Moved an imported row to another date | Already there: not written again on its first date |"):
        assert words in text, words
    assert (imports.ITEMS_MAX, imports.IMPORT_LIMIT, imports.IMPORT_HOURS) == (1_000, 10_000, 24)
