"""The token verifier, the userinfo lookup and the settings, tested directly (contract items 5 to 8, 10, 12, 14)."""

import base64
import hashlib
import hmac
import http.client
import io
import json
import threading
import time
import urllib.error
import urllib.request
import urllib.response

import anyio
import jwt
import pytest

import app.auth as auth
from app.auth import JwksTokenVerifier, UserinfoClient
from app.config import load_settings

from .conftest import ISSUER, OTHER_KEY, RESOURCE, SIGNING_KEY, FakeProvider, claims, settings, sign


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def verifier(provider: FakeProvider, clock: Clock | None = None, **overrides) -> JwksTokenVerifier:
    return JwksTokenVerifier(settings(**overrides), provider.fetch, clock or Clock())


def verify(v: JwksTokenVerifier, token: str):
    return anyio.run(v.verify_token, token)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unsigned(header: dict, payload: dict, signature: bytes = b"") -> str:
    return ".".join([b64(json.dumps(header).encode()), b64(json.dumps(payload).encode()), b64(signature)])


class FakeWeb:
    """Answers urllib requests in place of the network, at the HTTP handler level, and records each one.
    Used to test the real fetch: its redirect refusal and its body limit."""

    def __init__(self, routes: dict[str, tuple[int, dict[str, str], bytes]]):
        self.routes = routes
        self.seen: list[tuple[str, dict[str, str]]] = []

    def answer(self, req: urllib.request.Request):
        self.seen.append((req.full_url, dict(req.header_items())))
        status, headers, body = self.routes.get(req.full_url, (404, {}, b"{}"))
        message = http.client.HTTPMessage()
        for k, v in headers.items():
            message[k] = v
        response = urllib.response.addinfourl(io.BytesIO(body), message, req.full_url, status)
        response.msg = "fake"
        return response

    def install(self, monkeypatch) -> None:
        web = self

        class Plain(urllib.request.HTTPHandler):
            def http_open(self, req):
                return web.answer(req)

        class Secure(urllib.request.HTTPSHandler):
            def https_open(self, req):
                return web.answer(req)

        monkeypatch.setattr(auth, "_OPENER", auth.build_opener(Plain, Secure))


def ok(body: dict) -> tuple[int, dict[str, str], bytes]:
    return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()


# ---------------------------------------------------------------- item 5: what is accepted

def test_a_valid_token_is_accepted(provider):
    payload = claims()
    token = sign(payload)
    access = verify(verifier(provider), token)
    assert access is not None
    assert access.token == token
    assert access.subject == "user_01TEST"
    assert access.client_id == "client_01TEST"
    assert access.scopes == ["openid", "profile", "email"]
    assert access.resource == RESOURCE
    assert access.expires_at == payload["exp"]
    assert access.claims["iss"] == ISSUER


def test_aud_as_a_list_is_accepted_when_it_contains_the_resource(provider):
    token = sign(claims(aud=["https://other.example/api", RESOURCE]))
    assert verify(verifier(provider), token) is not None


def test_a_provider_clock_a_little_ahead_is_tolerated(provider):
    now = int(time.time())
    assert verify(verifier(provider), sign(claims(iat=now + 10, nbf=now + 10))) is not None


def test_a_trailing_slash_resource_is_compared_exactly(provider):
    slash = RESOURCE + "/"
    assert verify(verifier(provider, mcp_resource_url=slash), sign(claims(aud=slash))) is not None
    assert verify(verifier(provider, mcp_resource_url=slash), sign(claims(aud=RESOURCE))) is None


# ---------------------------------------------------------------- item 6: what is refused

def hs256_with_public_key() -> str:
    """The classic confusion attack: HS256 signed with the server's own public key as the secret."""
    header = {"alg": "HS256", "typ": "JWT", "kid": SIGNING_KEY.kid}
    signing_input = ".".join([b64(json.dumps(header).encode()), b64(json.dumps(claims()).encode())])
    mac = hmac.new(SIGNING_KEY.public_pem, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64(mac)}"


REFUSED = {
    "another audience": lambda: sign(claims(aud="https://other.example/mcp")),
    "audience with an added slash": lambda: sign(claims(aud=RESOURCE + "/")),
    "audience list without the resource": lambda: sign(claims(aud=["https://other.example/mcp"])),
    "another issuer": lambda: sign(claims(iss="https://evil.authkit.app")),
    "issuer with an added slash": lambda: sign(claims(iss=ISSUER + "/")),
    "expired": lambda: sign(claims(exp=int(time.time()) - 60, iat=int(time.time()) - 3600)),
    "expired seconds ago, inside the iat skew": lambda: sign(claims(exp=int(time.time()) - 10,
                                                                    iat=int(time.time()) - 3600)),
    "bad signature": lambda: sign(key=OTHER_KEY, kid=SIGNING_KEY.kid),
    "alg none": lambda: unsigned({"alg": "none", "typ": "JWT", "kid": SIGNING_KEY.kid}, claims()),
    "alg none, built by the library": lambda: jwt.encode(claims(), None, algorithm="none",
                                                         headers={"kid": SIGNING_KEY.kid}),
    "HS256 with a shared secret": lambda: jwt.encode(claims(), "a-shared-secret-long-enough-for-hs256-32b",
                                                     algorithm="HS256", headers={"kid": SIGNING_KEY.kid}),
    "HS256 with the public key as secret": hs256_with_public_key,
    "no expiry": lambda: sign(claims(exp=None)),
    "no subject": lambda: sign(claims(sub=None)),
    "empty subject": lambda: sign(claims(sub="")),
    "no key id": lambda: jwt.encode(claims(), SIGNING_KEY.private_pem, algorithm="RS256"),
    "not a JWT": lambda: "not-a-jwt",
    "three junk parts": lambda: "aaa.bbb.ccc",
    "empty": lambda: "",
}


@pytest.mark.parametrize("case", list(REFUSED))
def test_bad_tokens_are_refused(provider, case):
    assert verify(verifier(provider), REFUSED[case]()) is None


def test_a_bad_algorithm_never_triggers_a_key_set_fetch(provider):
    v = verifier(provider)
    verify(v, REFUSED["alg none"]())
    verify(v, REFUSED["HS256 with a shared secret"]())
    assert provider.count("/oauth2/jwks") == 0


# ---------------------------------------------------------------- items 7 and 8: the key set

def test_the_key_set_is_cached(provider):
    v = verifier(provider)
    for _ in range(3):
        assert verify(v, sign()) is not None
    assert provider.count("/oauth2/jwks") == 1


def test_the_key_set_is_refetched_after_an_hour(provider):
    clock = Clock()
    v = verifier(provider, clock)
    verify(v, sign())
    clock.now += 3599
    verify(v, sign())
    assert provider.count("/oauth2/jwks") == 1
    clock.now += 2
    verify(v, sign())
    assert provider.count("/oauth2/jwks") == 2


def test_an_unknown_key_id_refreshes_once_a_minute_at_most_then_is_refused(provider):
    clock = Clock()
    v = verifier(provider, clock)
    assert verify(v, sign()) is not None  # first fetch
    clock.now += 61
    stranger = sign(key=OTHER_KEY)
    assert verify(v, stranger) is None
    assert provider.count("/oauth2/jwks") == 2  # one refresh
    for _ in range(5):
        assert verify(v, stranger) is None
    clock.now += 30
    assert verify(v, stranger) is None
    assert provider.count("/oauth2/jwks") == 2  # no more within the minute
    clock.now += 31
    assert verify(v, stranger) is None
    assert provider.count("/oauth2/jwks") == 3  # a minute later, one more


def test_a_rotated_key_is_picked_up_by_the_refresh(provider):
    clock = Clock()
    v = verifier(provider, clock)
    verify(v, sign())
    provider.keys = [SIGNING_KEY, OTHER_KEY]
    clock.now += 61
    assert verify(v, sign(key=OTHER_KEY)) is not None


def test_a_failed_key_set_fetch_refuses_without_raising(provider):
    provider.fail_jwks = True
    assert verify(verifier(provider), sign()) is None


def test_after_a_failed_fetch_the_next_attempt_comes_within_seconds(provider):
    clock = Clock()
    v = verifier(provider, clock)
    provider.fail_jwks = True
    assert verify(v, sign()) is None
    provider.fail_jwks = False
    clock.now += 4
    assert verify(v, sign()) is None
    assert provider.count("/oauth2/jwks") == 1  # not yet
    clock.now += 2
    assert verify(v, sign()) is not None
    assert provider.count("/oauth2/jwks") == 2  # well inside the minute


def test_a_cached_key_does_not_wait_for_a_slow_refresh():
    clock = Clock()

    async def go():
        started, release = anyio.Event(), anyio.Event()
        calls = 0

        async def slow_second_fetch(url, headers=None):
            nonlocal calls
            calls += 1
            if calls > 1:
                started.set()
                await release.wait()
            return {"keys": [SIGNING_KEY.jwk()]}

        v = JwksTokenVerifier(settings(), slow_second_fetch, clock)
        assert await v.verify_token(sign()) is not None
        clock.now += 61
        results = {}

        async def stranger():
            results["stranger"] = await v.verify_token(sign(key=OTHER_KEY))  # unknown key id: refreshes

        async with anyio.create_task_group() as tg:
            tg.start_soon(stranger)
            await started.wait()  # the refresh is now in flight
            with anyio.fail_after(1):
                results["cached"] = await v.verify_token(sign())
            release.set()
        return results

    results = anyio.run(go)
    assert results["cached"] is not None and results["stranger"] is None


def test_non_rsa_and_non_signing_keys_are_ignored(provider):
    ec_like = {"kty": "EC", "kid": "key-1", "crv": "P-256", "x": "AA", "y": "AA"}
    enc = {**SIGNING_KEY.jwk(), "use": "enc"}

    async def only_bad_keys(url, headers=None):
        return {"keys": [ec_like, enc]}

    v = JwksTokenVerifier(settings(), only_bad_keys, Clock())
    assert verify(v, sign()) is None


# ---------------------------------------------------------------- the real fetch

def test_the_real_fetch_runs_off_the_event_loop(monkeypatch):
    seen = {}

    class Opener:
        def open(self, request, timeout):
            seen["thread"] = threading.get_ident()
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            seen["agent"] = request.get_header("User-agent")
            return io.BytesIO(json.dumps({"keys": [SIGNING_KEY.jwk()]}).encode())

    monkeypatch.setattr(auth, "_OPENER", Opener())

    async def go():
        seen["loop_thread"] = threading.get_ident()
        return await JwksTokenVerifier(settings()).verify_token(sign())

    assert anyio.run(go) is not None
    assert seen["url"] == f"{ISSUER}/oauth2/jwks"
    assert seen["thread"] != seen["loop_thread"]
    assert seen["timeout"] > 0
    assert seen["agent"] == auth.USER_AGENT


def test_the_real_fetch_refuses_redirects(monkeypatch):
    web = FakeWeb({
        f"{ISSUER}/.well-known/openid-configuration": ok({"userinfo_endpoint": f"{ISSUER}/oauth2/userinfo"}),
        f"{ISSUER}/oauth2/userinfo": (302, {"Location": "http://evil.example/steal"}, b""),
        "http://evil.example/steal": ok({"sub": "user_01TEST", "email": "stolen@example.com", "name": "Stolen"}),
    })
    web.install(monkeypatch)
    client = UserinfoClient(settings(), auth.fetch_json, Clock())
    assert anyio.run(client.lookup, "the-token", "user_01TEST") == (None, None)
    assert [url for url, _ in web.seen] == [f"{ISSUER}/.well-known/openid-configuration", f"{ISSUER}/oauth2/userinfo"]
    with pytest.raises(urllib.error.HTTPError) as refused:
        auth._get_json(f"{ISSUER}/oauth2/userinfo", None)
    assert refused.value.code == 302
    assert all(not url.startswith("http://evil") for url, _ in web.seen)


def test_the_real_fetch_caps_the_body(monkeypatch):
    big = {"keys": [], "padding": "x" * (auth.MAX_BODY + 10)}
    FakeWeb({f"{ISSUER}/oauth2/jwks": ok(big)}).install(monkeypatch)
    with pytest.raises(ValueError, match="too large"):
        auth._get_json(f"{ISSUER}/oauth2/jwks", None)


def test_a_cancelled_real_fetch_does_not_wait_for_its_thread(monkeypatch):
    release = threading.Event()

    class Hanging:
        def open(self, request, timeout):
            release.wait(10)
            raise OSError("released")

    monkeypatch.setattr(auth, "_OPENER", Hanging())
    client = UserinfoClient(settings(), auth.fetch_json, Clock(), timeout=0.3)
    started = time.monotonic()
    try:
        assert anyio.run(client.lookup, "the-token", "user_01TEST") == (None, None)
        assert time.monotonic() - started < 2
    finally:
        release.set()


# ---------------------------------------------------------------- items 13 and 14: logs

def test_debug_logs_claim_names_and_four_public_claims_only(provider, logs):
    token = sign(claims(email="asha@example.com"))
    assert verify(verifier(provider, auth_debug=True), token) is not None
    text = logs.text
    assert token not in text and "Bearer" not in text
    record = next(r for r in logs.records if r.getMessage().startswith("token verified"))
    message = record.getMessage()
    for name in ("client_id", "email", "exp", "iat", "iss", "scope", "sub", "aud"):
        assert repr(name) in message  # every claim name is listed
    assert ISSUER in message and RESOURCE in message and "openid profile email" in message
    assert "client_01TEST" in message
    assert "user_01TEST" not in text and "asha@example.com" not in text  # values beyond the four are not


def test_without_debug_a_verified_call_logs_nothing(provider, logs):
    assert verify(verifier(provider), sign()) is not None
    assert [r for r in logs.records if r.name.startswith("spotter")] == []


def test_debug_refusal_names_the_reason_but_not_the_token(provider, logs):
    token = sign(claims(aud="https://other.example/mcp"))
    assert verify(verifier(provider, auth_debug=True), token) is None
    assert token not in logs.text
    assert "InvalidAudienceError" in logs.text and "https://other.example/mcp" in logs.text


def test_debug_refusal_cuts_long_claim_values(provider, logs):
    token = sign(claims(iss="https://" + "a" * 1000, aud=["https://" + "b" * 1000]))
    assert verify(verifier(provider, auth_debug=True), token) is None
    line = next(r.getMessage() for r in logs.records if r.getMessage().startswith("token refused"))
    assert "a" * 200 not in line and "b" * 200 not in line and "(cut)" in line


# ---------------------------------------------------------------- item 10: userinfo

def lookup(provider: FakeProvider, token: str = "the-token", subject: str = "user_01TEST", clock=None):
    return anyio.run(UserinfoClient(settings(), provider.fetch, clock or Clock()).lookup, token, subject)


def test_userinfo_is_found_through_openid_discovery_and_asked_with_the_token(provider):
    assert lookup(provider) == ("asha@example.com", "Asha Test")
    assert provider.calls[0] == f"{ISSUER}/.well-known/openid-configuration"
    assert provider.calls[-1] == f"{ISSUER}/oauth2/userinfo"
    assert provider.sent_headers[-1] == {"Authorization": "Bearer the-token"}
    assert provider.count("/oauth-authorization-server") == 0


def test_userinfo_falls_back_to_the_oauth_metadata(provider):
    provider.openid = OSError("down")
    assert lookup(provider) == (None, None)  # as the real provider is: null there
    provider.metadata["userinfo_endpoint"] = f"{ISSUER}/oauth2/userinfo"
    assert lookup(provider) == ("asha@example.com", "Asha Test")


def test_userinfo_builds_a_name_from_its_parts(provider):
    provider.userinfo = {"sub": "user_01TEST", "email": "asha@example.com", "given_name": "Asha",
                         "family_name": "Test"}
    assert lookup(provider) == ("asha@example.com", "Asha Test")


@pytest.mark.parametrize("info", [
    {"sub": "user_02SOMEONE_ELSE", "email": "other@example.com", "name": "Other"},
    {"email": "nosub@example.com", "name": "No Sub"},
])
def test_userinfo_about_someone_else_is_ignored(provider, info):
    provider.userinfo = info
    assert lookup(provider) == (None, None)


@pytest.mark.parametrize("endpoint", [
    "https://evil.example/oauth2/userinfo",
    "https://example.authkit.app.evil.example/userinfo",
    "http://example.authkit.app/oauth2/userinfo",
    "https://example.authkit.app:8443/oauth2/userinfo",
    "https://example.authkit.app:notaport/oauth2/userinfo",
    "not a url",
])
def test_userinfo_on_another_host_is_never_called(provider, endpoint):
    provider.openid["userinfo_endpoint"] = endpoint
    assert lookup(provider) == (None, None)
    assert all(h == {} for h in provider.sent_headers)  # the token went nowhere


def test_userinfo_failures_give_nulls(provider):
    provider.userinfo = OSError("down")
    assert lookup(provider) == (None, None)
    provider.userinfo = {"sub": "user_01TEST", "email": 42, "name": ["x"]}
    assert lookup(provider) == (None, None)
    provider.openid["userinfo_endpoint"] = None
    assert lookup(provider) == (None, None)


def test_a_missing_userinfo_endpoint_is_remembered(provider):
    provider.openid["userinfo_endpoint"] = None
    clock = Clock()
    client = UserinfoClient(settings(), provider.fetch, clock)
    for _ in range(3):
        assert anyio.run(client.lookup, "the-token", "user_01TEST") == (None, None)
    assert provider.count("/openid-configuration") == 1 and provider.count("/oauth-authorization-server") == 1
    clock.now += auth.USERINFO_ENDPOINT_TTL + 1
    anyio.run(client.lookup, "the-token", "user_01TEST")
    assert provider.count("/openid-configuration") == 2


def test_a_userinfo_lookup_that_never_answers_gives_nulls_in_time():
    async def never(url, headers=None):
        await anyio.sleep_forever()

    client = UserinfoClient(settings(), never, Clock(), timeout=0.3)

    async def go():
        with anyio.move_on_after(3):  # the test's own guard, so a missing bound fails instead of hanging
            return await client.lookup("the-token", "user_01TEST")
        return "still waiting"

    started = time.monotonic()
    assert anyio.run(go) == (None, None)
    assert time.monotonic() - started < 2
    assert auth.USERINFO_TIMEOUT <= 6


# ---------------------------------------------------------------- item 12: settings

def test_settings_are_read_from_the_environment():
    s = load_settings({"AUTHKIT_DOMAIN": " example.authkit.app ", "MCP_RESOURCE_URL": RESOURCE, "AUTH_DEBUG": "1"})
    assert s.configured and s.auth_debug
    assert s.issuer == ISSUER and s.jwks_url == f"{ISSUER}/oauth2/jwks"
    assert not load_settings({}).configured and not load_settings({}).auth_debug


def test_a_mixed_case_domain_is_lowercased_so_tokens_still_match(provider):
    s = load_settings({"AUTHKIT_DOMAIN": "Example.AuthKit.app", "MCP_RESOURCE_URL": RESOURCE})
    assert s.configured and s.authkit_domain == "example.authkit.app" and s.issuer == ISSUER
    assert anyio.run(JwksTokenVerifier(s, provider.fetch, Clock()).verify_token, sign()) is not None


@pytest.mark.parametrize("env, says", [
    ({"MCP_RESOURCE_URL": RESOURCE}, "AUTHKIT_DOMAIN"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app"}, "MCP_RESOURCE_URL"),
    ({"AUTHKIT_DOMAIN": "https://example.authkit.app", "MCP_RESOURCE_URL": RESOURCE}, "bare lowercase host"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app/", "MCP_RESOURCE_URL": RESOURCE}, "bare lowercase host"),
    ({"AUTHKIT_DOMAIN": "exa mple.authkit.app", "MCP_RESOURCE_URL": RESOURCE}, "bare lowercase host"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app\tx", "MCP_RESOURCE_URL": RESOURCE}, "bare lowercase host"),
    ({"AUTHKIT_DOMAIN": "exa_mple.authkit.app", "MCP_RESOURCE_URL": RESOURCE}, "bare lowercase host"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app:443", "MCP_RESOURCE_URL": RESOURCE}, "bare lowercase host"),
    ({"AUTHKIT_DOMAIN": "user@example.authkit.app", "MCP_RESOURCE_URL": RESOURCE}, "bare lowercase host"),
    ({"AUTHKIT_DOMAIN": "example..authkit.app", "MCP_RESOURCE_URL": RESOURCE}, "bare lowercase host"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app", "MCP_RESOURCE_URL": "spotter.example.app/mcp"}, "not an http"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app", "MCP_RESOURCE_URL": RESOURCE + "?x=1"}, "?query"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app", "MCP_RESOURCE_URL": RESOURCE + "?"}, "?query"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app", "MCP_RESOURCE_URL": RESOURCE + "#top"}, "#fragment"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app", "MCP_RESOURCE_URL": "HTTPS://Spotter.example.app/mcp"},
     "not as written"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app", "MCP_RESOURCE_URL": "https://spotter.example.app:443/mcp"},
     "not as written"),
])
def test_unusable_settings_leave_the_server_unconfigured(env, says):
    s = load_settings(env)
    assert not s.configured and says in s.problem


def test_a_trailing_slash_resource_is_kept_as_written():
    s = load_settings({"AUTHKIT_DOMAIN": "example.authkit.app", "MCP_RESOURCE_URL": RESOURCE + "/"})
    assert s.configured and s.mcp_resource_url == RESOURCE + "/"
