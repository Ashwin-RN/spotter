"""The assembled app through its real endpoints: /health, /mcp, the metadata route and whoami
(contract items 1 to 4, 6, 9 to 13, 15)."""

import logging
import re
import time

import jwt
import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.mcp_server import REQUIRED_SCOPES, SCOPES

from .conftest import DOMAIN, HEADERS, ISSUER, OTHER_KEY, RESOURCE, SIGNING_KEY, claims, rpc, sign, whoami
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
    assert who["scopes"] == ["email", "offline_access", "openid", "profile"]


def test_only_openid_is_required(http):
    assert REQUIRED_SCOPES == ["openid"]
    assert whoami(http, sign(claims(scope="openid")))["scopes"] == ["openid"]


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
    assert who == {
        "user_id": "user_01TEST",
        "email": "asha@example.com",
        "name": "Asha Test",
        "scopes": ["openid", "profile", "email"],
        "client_id": "client_01TEST",
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


def test_whoami_is_read_only_titled_and_typed(http):
    tools = rpc(http, "tools/list", token=sign()).json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["whoami"]
    tool = tools[0]
    assert tool["title"] == "Who am I"
    assert tool["annotations"]["readOnlyHint"] is True
    schema = tool["outputSchema"]
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"user_id", "email", "name", "scopes", "client_id", "issuer", "expires_at"}


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
