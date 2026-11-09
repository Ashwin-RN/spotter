"""The token verifier, the userinfo lookup and the settings, tested directly (contract items 5 to 8, 10, 12, 14)."""

import base64
import hashlib
import hmac
import json
import logging
import threading
import time

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
    "bad signature": lambda: sign(key=OTHER_KEY, kid=SIGNING_KEY.kid),
    "alg none": lambda: unsigned({"alg": "none", "typ": "JWT", "kid": SIGNING_KEY.kid}, claims()),
    "alg none, built by the library": lambda: jwt.encode(claims(), None, algorithm="none",
                                                         headers={"kid": SIGNING_KEY.kid}),
    "HS256 with a shared secret": lambda: jwt.encode(claims(), "a-shared-secret-long-enough-for-hs256-32b",
                                                     algorithm="HS256", headers={"kid": SIGNING_KEY.kid}),
    "HS256 with the public key as secret": hs256_with_public_key,
    "no expiry": lambda: sign(claims(exp=None)),
    "no subject": lambda: sign(claims(sub=None)),
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


def test_non_rsa_and_non_signing_keys_are_ignored(provider):
    ec_like = {"kty": "EC", "kid": "key-1", "crv": "P-256", "x": "AA", "y": "AA"}
    enc = {**SIGNING_KEY.jwk(), "use": "enc"}

    async def only_bad_keys(url, headers=None):
        return {"keys": [ec_like, enc]}

    v = JwksTokenVerifier(settings(), only_bad_keys, Clock())
    assert verify(v, sign()) is None


def test_the_real_fetch_runs_off_the_event_loop(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"keys": [SIGNING_KEY.jwk()]}).encode()

    def fake_urlopen(request, timeout):
        seen["thread"] = threading.get_ident()
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["agent"] = request.get_header("User-agent")
        return Response()

    monkeypatch.setattr(auth.urllib.request, "urlopen", fake_urlopen)

    async def go():
        seen["loop_thread"] = threading.get_ident()
        return await JwksTokenVerifier(settings()).verify_token(sign())

    assert anyio.run(go) is not None
    assert seen["url"] == f"{ISSUER}/oauth2/jwks"
    assert seen["thread"] != seen["loop_thread"]
    assert seen["timeout"] > 0
    assert seen["agent"] == auth.USER_AGENT


# ---------------------------------------------------------------- items 13 and 14: logs

def test_debug_logs_claim_names_and_four_public_claims_only(provider, caplog):
    caplog.set_level(logging.DEBUG)
    token = sign(claims(email="asha@example.com"))
    assert verify(verifier(provider, auth_debug=True), token) is not None
    text = caplog.text
    assert token not in text and "Bearer" not in text
    record = next(r for r in caplog.records if r.getMessage().startswith("token verified"))
    message = record.getMessage()
    for name in ("client_id", "email", "exp", "iat", "iss", "scope", "sub", "aud"):
        assert repr(name) in message  # every claim name is listed
    assert ISSUER in message and RESOURCE in message and "openid profile email" in message
    assert "client_01TEST" in message
    assert "user_01TEST" not in text and "asha@example.com" not in text  # values beyond the four are not


def test_without_debug_a_verified_call_logs_nothing(provider, caplog):
    caplog.set_level(logging.DEBUG, logger="spotter")
    assert verify(verifier(provider), sign()) is not None
    assert [r for r in caplog.records if r.name.startswith("spotter")] == []


def test_debug_refusal_names_the_reason_but_not_the_token(provider, caplog):
    caplog.set_level(logging.DEBUG)
    token = sign(claims(aud="https://other.example/mcp"))
    assert verify(verifier(provider, auth_debug=True), token) is None
    assert token not in caplog.text
    assert "InvalidAudienceError" in caplog.text and "https://other.example/mcp" in caplog.text


# ---------------------------------------------------------------- item 10: userinfo

def lookup(provider: FakeProvider, token: str = "the-token"):
    return anyio.run(UserinfoClient(settings(), provider.fetch, Clock()).lookup, token)


def test_userinfo_is_asked_with_the_token(provider):
    assert lookup(provider) == ("asha@example.com", "Asha Test")
    assert provider.sent_headers[-1] == {"Authorization": "Bearer the-token"}
    assert provider.calls[-1] == f"{ISSUER}/oauth2/userinfo"


def test_userinfo_builds_a_name_from_its_parts(provider):
    provider.userinfo = {"email": "asha@example.com", "given_name": "Asha", "family_name": "Test"}
    assert lookup(provider) == ("asha@example.com", "Asha Test")


@pytest.mark.parametrize("endpoint", [
    "https://evil.example/oauth2/userinfo",
    "https://example.authkit.app.evil.example/userinfo",
    "http://example.authkit.app/oauth2/userinfo",
    "https://example.authkit.app:8443/oauth2/userinfo",
    "not a url",
])
def test_userinfo_on_another_host_is_never_called(provider, endpoint):
    provider.metadata["userinfo_endpoint"] = endpoint
    assert lookup(provider) == (None, None)
    assert all(h == {} for h in provider.sent_headers)  # the token went nowhere


def test_userinfo_failures_give_nulls(provider):
    provider.userinfo = OSError("down")
    assert lookup(provider) == (None, None)
    provider.userinfo = {"email": 42, "name": ["x"]}
    assert lookup(provider) == (None, None)
    del provider.metadata["userinfo_endpoint"]
    assert lookup(provider) == (None, None)


# ---------------------------------------------------------------- item 12: settings

def test_settings_are_read_from_the_environment():
    s = load_settings({"AUTHKIT_DOMAIN": " example.authkit.app ", "MCP_RESOURCE_URL": RESOURCE, "AUTH_DEBUG": "1"})
    assert s.configured and s.auth_debug
    assert s.issuer == ISSUER and s.jwks_url == f"{ISSUER}/oauth2/jwks"
    assert not load_settings({}).configured and not load_settings({}).auth_debug


@pytest.mark.parametrize("env, says", [
    ({"MCP_RESOURCE_URL": RESOURCE}, "AUTHKIT_DOMAIN"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app"}, "MCP_RESOURCE_URL"),
    ({"AUTHKIT_DOMAIN": "https://example.authkit.app", "MCP_RESOURCE_URL": RESOURCE}, "bare host"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app/", "MCP_RESOURCE_URL": RESOURCE}, "bare host"),
    ({"AUTHKIT_DOMAIN": "example.authkit.app", "MCP_RESOURCE_URL": "spotter.example.app/mcp"}, "not an http"),
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
