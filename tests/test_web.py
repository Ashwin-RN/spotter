"""The web pages and web sign-in, with the provider seam replaced so nothing touches the network.

Clients use the site's https address so the Secure session cookie is sent back, and never follow
redirects (a redirect to the provider would otherwise be fed back into this app).
"""

import asyncio
import base64
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import itsdangerous
import jwt
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.testclient import TestClient

from app import db, links, rules, services, users, web, web_auth
from app.config import Settings
from app.main import create_app
from app.models import Bodyweight, CallLog, LiftSession, Measurement, User

from .conftest import RESOURCE, TEST_DIR, rpc, settings

SITE = "https://spotter.example.app"
API_KEY = "sk_test_web_only_value_7f3a9c"
CLIENT = "client_web_test_01"
SESSION_SECRET = "session-secret-for-tests-only-4b1d-9e7c-long"
WEB = {"workos_client_id": CLIENT, "workos_api_key": API_KEY}
PROVIDER = "https://provider.example/authorize"
CODE = "code-one-time-5e2c"
METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"


class FakeSeam:
    """Stands in for web_auth's three provider functions."""

    def __init__(self) -> None:
        self.identity = web_auth.Identity("user_01WEB", "asha@example.com", "Asha Web", "session_01ABC")
        self.states: list[str] = []
        self.codes: list[str] = []
        self.fail = False

    def sign_in_url(self, state: str) -> str:
        self.states.append(state)
        return f"{PROVIDER}?state={state}"

    async def exchange(self, code: str) -> web_auth.Identity:
        self.codes.append(code)
        if self.fail:
            raise web_auth.SignInFailed("BadRequestError")
        return self.identity

    def sign_out_url(self, session_id: str) -> str:
        return f"https://provider.example/logout?session_id={session_id}"


@pytest.fixture
def seam(monkeypatch) -> FakeSeam:
    fake = FakeSeam()
    monkeypatch.setattr(web_auth, "sign_in_url", fake.sign_in_url)
    monkeypatch.setattr(web_auth, "exchange", fake.exchange)
    monkeypatch.setattr(web_auth, "sign_out_url", fake.sign_out_url)
    return fake


@pytest.fixture
def web_client(provider, seam):
    """web_client(**overrides): a running app with web sign-in on (unless overridden). One per test:
    create_app makes its settings the process-wide ones."""
    opened: list[TestClient] = []

    def make(base_url: str = SITE, raise_server_exceptions: bool = True, **overrides: Any) -> TestClient:
        client = TestClient(create_app(settings(**{**WEB, **overrides}), fetch=provider.fetch),
                            base_url=base_url, follow_redirects=False,
                            raise_server_exceptions=raise_server_exceptions)
        client.__enter__()
        opened.append(client)
        return client

    yield make
    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture
def client(web_client) -> TestClient:
    return web_client()


def user_count() -> int:
    with db.session_scope() as session:
        return session.scalar(select(func.count()).select_from(User))


def only_user() -> User:
    with db.session_scope() as session:
        rows = session.scalars(select(User)).all()
    assert len(rows) == 1
    return rows[0]


def session_of(client: TestClient) -> dict:
    """The decoded session cookie, after checking its signature."""
    value = client.cookies.get(web.SESSION_COOKIE)
    if value is None:
        return {}
    key = settings(**WEB).session_key
    data = itsdangerous.TimestampSigner(key).unsign(value.encode())
    return json.loads(base64.b64decode(data))


def plant_session(client: TestClient, data: dict) -> None:
    """Give the client a validly signed session cookie holding exactly `data`."""
    key = settings(**WEB).session_key
    value = itsdangerous.TimestampSigner(key).sign(base64.b64encode(json.dumps(data).encode())).decode()
    client.cookies.clear()
    client.cookies.set(web.SESSION_COOKIE, value, domain=client.base_url.host)


def raw_session(client: TestClient) -> str:
    value = client.cookies.get(web.SESSION_COOKIE) or ""
    data = value.rsplit(".", 2)[0]
    return base64.b64decode(data).decode() if data else ""


def start(client: TestClient) -> str:
    r = client.get("/sign-in")
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith(PROVIDER)
    return parse_qs(urlparse(location).query)["state"][0]


def sign_in(client: TestClient, code: str = CODE):
    state = start(client)
    return client.get("/auth/callback", params={"code": code, "state": state})


def csrf_of(client: TestClient) -> str:
    r = client.get("/settings")
    assert r.status_code == 200, r.text
    return re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)


# ---------------------------------------------------------------- signed out

def test_settings_signed_out_redirects_to_sign_in(client):
    r = client.get("/settings")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/sign-in"


def test_home_shows_the_mcp_address_and_how_to_connect(client):
    r = client.get("/")
    assert r.status_code == 200
    assert RESOURCE in r.text and "custom connector" in r.text
    assert 'href="/sign-in"' in r.text and 'href="/settings"' not in r.text
    assert "set-cookie" not in r.headers  # rendering the home page signed out keeps no session


def test_home_signed_in_links_to_settings(client):
    sign_in(client)
    r = client.get("/")
    assert 'href="/settings"' in r.text and RESOURCE in r.text


# ---------------------------------------------------------------- sign-in

def test_sign_in_redirects_to_the_provider_and_stores_the_state(client, seam):
    state = start(client)
    assert seam.states == [state] and len(state) >= 32
    assert session_of(client) == {web.STATE: state}


def test_callback_creates_the_user_then_a_second_sign_in_finds_it(client, seam):
    r = sign_in(client)
    assert r.status_code == 303 and r.headers["location"] == SITE + "/today"  # decision 1: sign-in lands on Today
    first = only_user()
    assert (first.subject, first.email, first.name) == ("user_01WEB", "asha@example.com", "Asha Web")
    assert session_of(client)[web.USER] == first.id

    page = client.get("/settings").text
    assert "user_01WEB" in page and f"<code>{first.id}</code>" in page and "asha@example.com" in page

    client.post("/sign-out", data={"csrf_token": session_of(client)["csrf"]})
    sign_in(client)
    assert user_count() == 1 and only_user().id == first.id
    assert seam.codes == [CODE, CODE]


def test_signed_in_sign_in_goes_to_today(client, seam):
    sign_in(client)
    r = client.get("/sign-in")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/today"
    assert len(seam.states) == 1


@pytest.mark.parametrize("params", [
    {"code": CODE, "state": "not-the-state"},
    {"code": CODE},
    {"code": CODE, "state": ""},
    {"code": CODE, "state": "é-not-ascii"},
    {"state": "<right>"},
    {"error": "access_denied", "state": "<right>"},
    {"error": "access_denied", "code": CODE, "state": "<right>"},
], ids=["wrong state", "missing state", "empty state", "non-ascii state", "missing code", "provider error",
        "provider error with code"])
def test_a_bad_callback_creates_nothing(client, seam, params):
    state = start(client)
    params = {k: (state if v == "<right>" else v) for k, v in params.items()}
    r = client.get("/auth/callback", params=params)
    assert r.status_code == 400 and "Sign-in failed" in r.text
    assert user_count() == 0 and seam.codes == []
    assert web.STATE not in session_of(client)  # the state is used up either way


def test_a_reused_state_creates_nothing(client, seam):
    state = start(client)
    assert client.get("/auth/callback", params={"code": CODE, "state": state}).status_code == 303
    client.cookies.clear()
    r = client.get("/auth/callback", params={"code": "code-two", "state": state})
    assert r.status_code == 400 and seam.codes == [CODE]


def test_a_replayed_callback_in_the_same_session_is_refused(client, seam):
    state = start(client)
    client.get("/auth/callback", params={"code": CODE, "state": state})
    r = client.get("/auth/callback", params={"code": CODE, "state": state})
    assert r.status_code == 400 and seam.codes == [CODE]


def test_a_callback_with_no_sign_in_started_creates_nothing(client, seam):
    r = client.get("/auth/callback", params={"code": CODE, "state": "anything"})
    assert r.status_code == 400 and user_count() == 0 and seam.codes == []


def test_a_provider_failure_shows_sign_in_failed(client, seam):
    seam.fail = True
    r = sign_in(client)
    assert r.status_code == 400 and "Try again" in r.text and user_count() == 0
    assert web.USER not in session_of(client)


def test_signups_closed_shows_its_message_and_fix(web_client):
    client = web_client(signups_per_day=0)
    r = sign_in(client)
    assert r.status_code == 403
    assert "not taking new accounts" in r.text and "Try again after midnight UTC." in r.text
    assert user_count() == 0


def test_a_session_naming_a_deleted_user_is_cleared(client):
    sign_in(client)
    with db.session_scope() as session:
        session.delete(session.get(User, only_user().id))
    r = client.get("/settings")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/sign-in"
    assert session_of(client) == {}


SESSION_KEYS = {web.USER, "csrf", web.PROVIDER_SESSION, web.SIGNED_IN_AT}


def test_sign_in_starts_a_fresh_session(client):
    plant_session(client, {web.STATE: "earlier-state", web.USER: 999, "csrf": "stale-token", "other": "left over"})
    r = client.get("/auth/callback", params={"code": CODE, "state": "earlier-state"})
    assert r.status_code == 303
    after = session_of(client)
    assert set(after) == SESSION_KEYS
    assert after[web.USER] == only_user().id and after["csrf"] != "stale-token"


# ---------------------------------------------------------------- how long a session lasts

DAY = 24 * 3600


@pytest.mark.parametrize("age, signed_in", [
    (60, True),
    (14 * DAY - 60, True),
    (14 * DAY + 60, False),
    (400 * DAY, False),
], ids=["a minute", "a minute short of 14 days", "14 days and a minute", "over a year"])
def test_a_session_lasts_14_days_from_sign_in(client, make_user, age, signed_in):
    user = make_user()
    plant_session(client, {web.USER: user.id, "csrf": "t", web.SIGNED_IN_AT: int(time.time()) - age})
    r = client.get("/settings")
    if signed_in:
        assert r.status_code == 200 and user.subject in r.text
    else:
        assert r.status_code == 303 and r.headers["location"] == SITE + "/sign-in"
        assert session_of(client) == {}


@pytest.mark.parametrize("signed_in_at", [None, "yesterday", 1.5, True, [], 10**12],
                         ids=["missing", "text", "float", "bool", "list", "far future"])
def test_a_session_without_a_usable_sign_in_time_is_signed_out(client, make_user, signed_in_at):
    user = make_user()
    data = {web.USER: user.id, "csrf": "t"}
    if signed_in_at is not None:
        data[web.SIGNED_IN_AT] = signed_in_at
    plant_session(client, data)
    r = client.get("/settings")
    assert r.status_code == 303 and session_of(client) == {}


def test_the_cookie_lifetime_is_14_days(client):
    header = set_cookie(sign_in(client))
    assert f"max-age={14 * DAY};" in header


# ---------------------------------------------------------------- the cookie

def set_cookie(response) -> str:
    [header] = [v for k, v in response.headers.multi_items() if k.lower() == "set-cookie"]
    return header.lower()


def test_the_cookie_is_httponly_lax_and_secure_on_https(client):
    header = set_cookie(sign_in(client))
    assert header.startswith(web.SESSION_COOKIE + "=")
    assert "httponly" in header and "samesite=lax" in header and "secure" in header


def test_the_cookie_is_not_secure_on_a_plain_http_site(web_client):
    client = web_client(base_url="http://localhost:8000", mcp_resource_url="http://localhost:8000/mcp")
    header = set_cookie(sign_in(client))
    assert "httponly" in header and "samesite=lax" in header and "secure" not in header


def test_the_cookie_holds_only_the_four_session_values(client, seam):
    sign_in(client)
    session = session_of(client)
    assert set(session) == SESSION_KEYS
    assert session[web.PROVIDER_SESSION] == "session_01ABC"
    assert abs(session[web.SIGNED_IN_AT] - time.time()) < 60
    raw = raw_session(client)
    for value in (CODE, "asha@example.com", "Asha Web", "user_01WEB", API_KEY):
        assert value not in raw and value not in client.cookies.get(web.SESSION_COOKIE)


def test_a_tampered_cookie_is_ignored(client):
    sign_in(client)
    value = client.cookies.get(web.SESSION_COOKIE)
    client.cookies.clear()
    client.cookies.set(web.SESSION_COOKIE, value, domain=client.base_url.host)
    assert client.get("/settings").status_code == 200  # the untouched cookie is still good
    data = {**session_of(client), web.USER: only_user().id + 1}
    forged = base64.b64encode(json.dumps(data).encode()).decode()
    client.cookies.clear()
    client.cookies.set(web.SESSION_COOKIE, forged + "." + value.rsplit(".", 2)[1] + "." + value.rsplit(".", 1)[1],
                       domain=client.base_url.host)
    assert client.get("/settings").status_code == 303


def test_mcp_and_the_metadata_never_read_or_set_the_cookie(client):
    sign_in(client)
    for r in (rpc(client, "initialize"), client.get(METADATA_PATH), client.get("/health")):
        assert "set-cookie" not in r.headers
        assert "cookie" not in r.headers.get("vary", "").lower()
    assert rpc(client, "initialize").status_code == 401  # the cookie is no sign-in for MCP


# ---------------------------------------------------------------- CSRF

@pytest.mark.parametrize("path, data", [("/sign-out", {}), ("/settings", {"time_zone": "Asia/Kolkata"})])
@pytest.mark.parametrize("token", [None, "", "wrong-token", "é"], ids=["missing", "empty", "wrong", "non-ascii"])
def test_a_post_without_the_right_csrf_token_is_403(client, path, data, token):
    sign_in(client)
    if token is not None:
        data = {**data, "csrf_token": token}
    r = client.post(path, data=data)
    assert r.status_code == 403 and "nothing was changed" in r.text
    assert session_of(client).get(web.USER) == only_user().id  # still signed in
    assert only_user().time_zone is None


def test_a_signed_out_post_is_403(client):
    assert client.post("/sign-out").status_code == 403
    assert client.post("/settings", data={"time_zone": "UTC", "csrf_token": "x"}).status_code == 403


def test_another_sessions_token_is_refused(web_client):
    first, second = web_client(), web_client()
    sign_in(first)
    sign_in(second)
    r = second.post("/settings", data={"time_zone": "UTC", "csrf_token": session_of(first)["csrf"]})
    assert r.status_code == 403


def test_every_non_get_web_route_checks_the_token(client):
    sign_in(client)
    posting = [r for r in web.routes(settings(**WEB)) if getattr(r, "methods", None) and "POST" in r.methods]
    assert {r.path for r in posting} == {
        "/sign-out", "/settings", "/settings/claude-can", "/day/{date}/weigh-in", "/day/{date}/measurement",
        "/entries/{kind}/{entry_id}/edit", "/entries/{kind}/{entry_id}/delete", "/entries/{kind}/{entry_id}/restore"}
    filled = {"{date}": "2026-11-09", "{kind}": "bodyweight", "{entry_id}": "1"}
    for route in posting:
        path = route.path
        for marker, value in filled.items():
            path = path.replace(marker, value)
        for data in ({}, {"csrf_token": "wrong"}):
            assert client.post(route.path, data=data).status_code == 403, route.path
            assert client.post(path, data=data).status_code == 403, path


# ---------------------------------------------------------------- sign-out

def test_sign_out_clears_the_session_and_ends_the_provider_session(client):
    sign_in(client)
    r = client.post("/sign-out", data={"csrf_token": csrf_of(client)})
    assert r.status_code == 303
    assert r.headers["location"] == "https://provider.example/logout?session_id=session_01ABC"
    assert session_of(client) == {}
    assert client.get("/settings").status_code == 303


def test_sign_out_without_a_provider_session_goes_home(client, seam):
    seam.identity = web_auth.Identity("user_01WEB", None, None, None)
    sign_in(client)
    r = client.post("/sign-out", data={"csrf_token": csrf_of(client)})
    assert r.status_code == 303 and r.headers["location"] == SITE + "/"
    assert client.get("/settings").headers["location"] == SITE + "/sign-in"


# ---------------------------------------------------------------- time zone

def test_the_time_zone_form_sets_a_valid_zone(client):
    sign_in(client)
    r = client.post("/settings", data={"time_zone": "Asia/Kolkata", "csrf_token": csrf_of(client)})
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=time_zone"
    assert only_user().time_zone == "Asia/Kolkata"
    page = client.get(r.headers["location"]).text
    assert "Time zone saved." in page and "Asia/Kolkata" in page


def test_the_time_zone_form_refuses_a_bad_zone_in_its_own_words(client):
    sign_in(client)
    r = client.post("/settings", data={"time_zone": "Mars/Olympus<b>", "csrf_token": csrf_of(client)})
    assert r.status_code == 400
    assert "That time zone was not recognised, so nothing was changed." in r.text
    assert "Choose your time zone from the list." in r.text
    assert "IANA" not in r.text and "worked out from the city" not in r.text  # the service's text is for Claude
    assert "Olympus" not in r.text  # what was sent is not repeated
    assert only_user().time_zone is None


@pytest.mark.parametrize("sent", ["Kolkata", "EST", "", "x" * 100, "Asia/Kolkata\u0000"])
def test_every_refusal_reads_the_same_on_the_page(client, sent):
    sign_in(client)
    r = set_zone(client, sent)
    assert r.status_code == 400 and "That time zone was not recognised" in r.text
    assert "Closest zone" not in r.text and "Shorten" not in r.text


def test_the_web_stores_a_zone_through_the_one_write_path(client, monkeypatch):
    sign_in(client)
    token = csrf_of(client)
    calls = []
    real = services.update_settings
    monkeypatch.setattr(services, "update_settings", lambda *args: calls.append(args) or real(*args))
    client.post("/settings", data={"time_zone": "Asia/Calcutta", "csrf_token": token})
    assert calls == [(only_user().id, {"time_zone": "Asia/Calcutta"}, "web")]
    assert only_user().time_zone == "Asia/Kolkata"


def test_the_same_zone_again_is_a_success(client):
    sign_in(client)
    set_zone(client, "Europe/London")
    r = set_zone(client, "europe/london")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=time_zone"
    assert only_user().time_zone == "Europe/London"


# ---------------------------------------------------------------- failing closed

def test_without_the_provider_values_the_web_says_not_set_up_and_mcp_still_works(provider, seam):
    with TestClient(create_app(settings(), fetch=provider.fetch), base_url=SITE, follow_redirects=False) as client:
        home = client.get("/")
        assert home.status_code == 200 and "not set up" in home.text and RESOURCE in home.text
        for method, path in (("GET", "/sign-in"), ("GET", "/auth/callback"), ("GET", "/settings")):
            r = client.request(method, path, params={"code": CODE, "state": "x"})
            assert r.status_code == 503 and "not set up" in r.text and "WORKOS_API_KEY" in r.text
            assert "set-cookie" not in r.headers
        assert client.post("/sign-out").status_code == 403
        assert seam.states == [] and seam.codes == []
        r = rpc(client, "initialize")
        assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]


@pytest.mark.parametrize("overrides", [{"workos_api_key": None}, {"workos_client_id": None}])
def test_half_the_provider_values_is_not_set_up(web_client, overrides):
    client = web_client(**overrides)
    assert client.get("/sign-in").status_code == 503


def test_a_short_session_secret_keeps_web_sign_in_off(web_client, logs):
    weak = "short-secret-under-32"
    client = web_client(session_secret=weak)
    r = client.get("/sign-in")
    assert r.status_code == 503 and "SESSION_SECRET is too short" in r.text
    assert "SESSION_SECRET is too short" in logs.text
    assert weak not in r.text and weak not in logs.text
    assert web.problem(settings(**WEB, session_secret="s" * 32)) is None


def test_no_session_key_keeps_web_sign_in_off(monkeypatch):
    monkeypatch.setattr(Settings, "session_key", property(lambda self: None))
    assert web.problem(settings(**WEB)) == "There is no key to sign the session cookie with."


def test_without_the_mcp_settings_the_web_says_so_too(provider, seam):
    unconfigured = Settings(None, None, database_url=settings().database_url, **WEB)
    with TestClient(create_app(unconfigured, fetch=provider.fetch), base_url=SITE, follow_redirects=False) as client:
        home = client.get("/")
        assert home.status_code == 200 and "address is not set up" in home.text
        assert client.get("/sign-in").status_code == 503
        assert client.get("/health").text == "OK"
        assert client.post("/mcp").status_code == 503


def test_health_and_signed_out_pages_never_touch_the_database(provider, seam):
    missing = TEST_DIR / "absent" / "none.db"
    app = create_app(settings(database_url=f"sqlite:///{missing.as_posix()}", **WEB), fetch=provider.fetch)
    try:
        with TestClient(app, base_url=SITE, follow_redirects=False) as client:
            assert client.get("/health").text == "OK"
            assert client.get("/").status_code == 200
            assert client.get("/settings").status_code == 303
            assert client.get("/sign-in").status_code == 303
    finally:
        db.dispose()
    assert not missing.parent.exists()


@pytest.mark.parametrize("error", [
    OperationalError("SELECT 1", {}, Exception("unable to open database file")),
    InterfaceError("SELECT 1", {}, Exception("connection closed")),
    db.DatabaseNotConfigured("DATABASE_URL is not set."),
], ids=["operational", "interface", "not configured"])
def test_a_missing_database_shows_the_not_set_up_page(client, monkeypatch, logs, error):
    def down(*args, **kwargs):
        raise error

    monkeypatch.setattr(users, "user_for_identity", down)
    r = sign_in(client)
    assert r.status_code == 503 and "cannot reach its database" in r.text
    assert "unable to open" not in r.text and "connection closed" not in r.text
    assert type(error).__name__ in logs.text and "Traceback" in logs.text  # logged with its traceback


def test_any_other_error_is_a_500_not_the_database_page(web_client, monkeypatch):
    client = web_client(raise_server_exceptions=False)
    sign_in(client)

    def bug(*args, **kwargs):
        raise AttributeError("a real bug")

    monkeypatch.setattr(services, "update_settings", bug)
    r = client.post("/settings", data={"time_zone": "UTC", "csrf_token": csrf_of(client)})
    assert r.status_code == 500 and "cannot reach its database" not in r.text


def test_a_failing_statement_never_shows_its_bound_values():
    with pytest.raises(OperationalError) as caught:
        with db.get_engine().connect() as connection:
            connection.execute(text("SELECT * FROM no_such_table WHERE email = :email"),
                               {"email": "marker-person@example.com"})
    assert "no_such_table" in str(caught.value)
    assert "marker-person@example.com" not in str(caught.value)


# ---------------------------------------------------------------- what pages show and send

def test_no_page_renders_a_secret(web_client, seam):
    client = web_client(session_secret=SESSION_SECRET)
    keys = (API_KEY, SESSION_SECRET, settings(**WEB).session_key)
    texts = [client.get("/").text, client.get("/sign-in").headers["location"]]
    sign_in(client)
    texts += [client.get("/").text, client.get("/settings").text,
              client.post("/settings", data={"time_zone": "Nowhere", "csrf_token": csrf_of(client)}).text,
              client.post("/settings", data={"time_zone": "UTC"}).text]
    client.cookies.clear()
    seam.fail = True
    texts.append(sign_in(client).text)
    for text in texts:
        for key in keys:
            assert key not in text


def test_https_pages_send_hsts(client):
    for r in (client.get("/"), client.get("/settings"), client.get("/static/style.css")):
        assert r.headers["strict-transport-security"] == "max-age=31536000"


def test_plain_http_pages_do_not_send_hsts(web_client):
    client = web_client(base_url="http://localhost:8000", mcp_resource_url="http://localhost:8000/mcp")
    for r in (client.get("/"), client.get("/settings"), client.get("/static/style.css")):
        assert "strict-transport-security" not in r.headers


def test_pages_carry_security_headers_and_no_store(client):
    responses = [client.get("/"), client.get("/settings"), client.get("/sign-in")]
    sign_in(client)
    responses += [client.get("/settings"), client.post("/sign-out")]
    for r in responses:
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["cache-control"] == "no-store"


def test_the_stylesheet_is_served(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_names_from_the_provider_are_escaped(client, seam):
    seam.identity = web_auth.Identity("user_01WEB", "a@example.com", "<script>alert(1)</script>", None)
    sign_in(client)
    page = client.get("/settings").text
    assert "<script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page


# ---------------------------------------------------------------- the real seam, offline

def test_the_sign_in_address_carries_state_client_id_and_the_callback(app_settings):
    from app import config

    config.use_settings(settings(**WEB))
    url = urlparse(web_auth.sign_in_url("state-123"))
    query = parse_qs(url.query)
    assert (url.scheme, url.netloc, url.path) == ("https", "api.workos.com", "/user_management/authorize")
    assert query == {"provider": ["authkit"], "state": ["state-123"], "redirect_uri": [SITE + "/auth/callback"],
                     "response_type": ["code"], "client_id": [CLIENT]}
    assert API_KEY not in web_auth.sign_in_url("x")


def test_the_sign_out_address_returns_home(app_settings):
    from app import config

    config.use_settings(settings(**WEB))
    url = urlparse(web_auth.sign_out_url("session_01"))
    assert url.path == "/user_management/sessions/logout"
    assert parse_qs(url.query) == {"session_id": ["session_01"], "return_to": [SITE + "/"]}


class FakeUser:
    id = "user_01REAL"
    email = "r@example.com"
    name = None
    first_name = "Ravi"
    last_name = "Real"


class FakeWorkOS:
    """Stands in for the provider's async client: records what it was built with and the code."""
    built: list[dict] = []
    codes: list[str] = []
    closed = 0

    def __init__(self, **values):
        FakeWorkOS.built.append(values)
        self.user_management = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        FakeWorkOS.closed += 1

    async def authenticate_with_code(self, *, code):
        FakeWorkOS.codes.append(code)

        class Result:
            user = FakeUser()
            access_token = jwt.encode({"sid": "session_01REAL", "sub": "user_01REAL"},
                                      "a-test-signing-key-of-thirty-two-bytes", algorithm="HS256")
            refresh_token = "refresh-never-kept"

        return Result()


def test_exchange_returns_the_identity_and_drops_the_tokens(monkeypatch, app_settings):
    from app import config

    config.use_settings(settings(**WEB))
    FakeWorkOS.built, FakeWorkOS.codes, FakeWorkOS.closed = [], [], 0
    monkeypatch.setattr(web_auth, "AsyncWorkOSClient", FakeWorkOS)
    identity = asyncio.run(web_auth.exchange("code-real"))
    assert identity == web_auth.Identity("user_01REAL", "r@example.com", "Ravi Real", "session_01REAL")
    assert FakeWorkOS.codes == ["code-real"] and FakeWorkOS.closed == 1
    [values] = FakeWorkOS.built
    assert values["api_key"] == API_KEY and values["client_id"] == CLIENT
    assert values["request_timeout"] == 10 and values["max_retries"] == 0


def test_exchange_failure_is_logged_by_type_only(monkeypatch, logs):
    async def broken(code):
        raise ValueError(f"provider said no to {code}")

    monkeypatch.setattr(web_auth, "_authenticate", broken)
    with pytest.raises(web_auth.SignInFailed):
        asyncio.run(web_auth.exchange("code-secret-9"))
    assert "ValueError" in logs.text and "code-secret-9" not in logs.text


def test_exchange_has_a_time_limit(monkeypatch, logs):
    async def slow(code):
        await asyncio.sleep(5)

    monkeypatch.setattr(web_auth, "_authenticate", slow)
    monkeypatch.setattr(web_auth, "EXCHANGE_SECONDS", 0.05)
    with pytest.raises(web_auth.SignInFailed):
        asyncio.run(web_auth.exchange("code"))
    assert "TimeoutError" in logs.text


def test_the_access_log_never_shows_a_query_string(client, logs):
    import logging

    access = logging.getLogger("uvicorn.access")
    access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", f"/auth/callback?code={CODE}&state=s1",
                "1.1", 400)
    access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", "/settings", "1.1", 303)
    assert CODE not in logs.text and "state=s1" not in logs.text
    assert '"GET /auth/callback HTTP/1.1" 400' in logs.text and '"GET /settings HTTP/1.1" 303' in logs.text


# ---------------------------------------------------------------- no link is a dead end

SAMPLE_PARAMS = {"day": {"date": "2026-11-09"}, "progress_lift": {"lift": "Back Squat"}}


def addresses() -> dict[str, str]:
    """Every page links.py can name, as a path. A name with parameters and no sample here fails."""
    return {name: urlparse(links.page(name, **SAMPLE_PARAMS.get(name, {}))).path for name in links.NAMES}


def test_every_link_spotter_hands_out_has_a_page(client):
    paths = addresses()
    assert set(paths) == set(links.NAMES)
    for name, path in paths.items():  # signed out
        r = client.get(path)
        if name == "sign_in":
            assert r.status_code == 303 and r.headers["location"].startswith(PROVIDER), name
        elif name == "home":
            assert r.status_code == 200, name
        else:
            assert r.status_code == 303 and r.headers["location"] == SITE + "/sign-in", name
    sign_in(client)
    for name, path in paths.items():  # signed in
        r = client.get(path)
        if name == "sign_in":
            assert r.status_code == 303, name
        elif name == "today":  # Today is the day page for today
            assert r.status_code == 303 and r.headers["location"].startswith(SITE + "/day/"), name
            assert client.get(r.headers["location"]).status_code == 200
        else:
            assert r.status_code == 200, name


def test_placeholder_pages_say_not_built_yet_and_never_echo_the_address(client):
    sign_in(client)
    for name in web.PLACEHOLDERS:
        r = client.get(addresses()[name])
        assert "not built yet" in r.text and "ask Claude to read your numbers back" in r.text
        assert "2026-11-09" not in r.text and "back-squat" not in r.text.lower()
    r = client.get("/day/<script>x</script>")
    assert "<script>x" not in r.text


def test_the_placeholder_list_names_only_pages_links_knows(client):
    assert set(web.PLACEHOLDERS) <= set(links.NAMES)


# ---------------------------------------------------------------- the time zone finds itself

AUTO_FORM = 'id="auto-time-zone"'
SCRIPT_TAG = '<script src="/static/time-zone.js"></script>'
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "base-uri 'none'; frame-ancestors 'none'")


def options(page: str) -> list[tuple[str, bool]]:
    """(value, selected) for every <option> in the page."""
    return [(value, bool(selected)) for value, selected in
            re.findall(r'<option value="([^"]*)"( selected)?>', page)]


def set_zone(client: TestClient, zone: str):
    return client.post("/settings", data={"time_zone": zone, "csrf_token": csrf_of(client)})


def test_a_person_with_no_zone_gets_the_hidden_form_and_the_script_on_every_page(client):
    sign_in(client)
    token = session_of(client)["csrf"]
    for path in ("/", "/settings", "/goals", "/day/2026-11-09"):
        page = client.get(path).text
        assert 'data-time-zone="missing"' in page and AUTO_FORM in page and SCRIPT_TAG in page, path
        form = re.search(r'<form id="auto-time-zone" method="post" action="/settings" hidden>(.*?)</form>',
                         page, re.S).group(1)
        assert f'name="csrf_token" value="{token}"' in form
        assert '<input type="hidden" name="time_zone" value="">' in form
        assert f'<input type="hidden" name="next" value="{path}">' in form  # the page to come back to


def test_a_person_with_a_zone_gets_neither(client):
    sign_in(client)
    set_zone(client, "Asia/Kolkata")
    for path in ("/", "/settings", "/goals"):
        page = client.get(path).text
        assert "data-time-zone" not in page and AUTO_FORM not in page and "<script" not in page, path


def test_signed_out_pages_carry_no_script(client):
    for page in (client.get("/").text, client.get("/sign-in").text):
        assert "<script" not in page and AUTO_FORM not in page


def test_the_script_is_served_as_javascript_with_the_usual_headers(client):
    r = client.get("/static/time-zone.js")
    assert r.status_code == 200
    assert r.headers["content-type"].split(";")[0] in ("text/javascript", "application/javascript")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["strict-transport-security"] == "max-age=31536000"
    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in r.text
    assert "sessionStorage" in r.text and "form.submit()" in r.text


def test_no_page_has_an_inline_script(client):
    pages = [client.get("/").text]
    sign_in(client)
    pages += [client.get(p).text for p in ("/", "/settings", "/goals")]
    pages.append(set_zone(client, "Not/AZone").text)
    for page in pages:
        for attributes, body in re.findall(r"<script([^>]*)>(.*?)</script>", page, re.S):
            assert attributes == ' src="/static/time-zone.js"' and body == ""
        assert re.search(r"<[^>]+\son[a-z]+\s*=", page, re.I) is None  # no inline event handlers


def test_the_content_security_policy_is_exact(client):
    responses = [client.get("/"), client.get("/sign-in")]
    sign_in(client)
    responses += [client.get("/settings"), client.get("/goals")]
    for r in responses:
        assert r.headers["content-security-policy"] == CSP


@pytest.mark.parametrize("browser_zone, stored", [
    ("Asia/Kolkata", "Asia/Kolkata"),
    ("Asia/Calcutta", "Asia/Kolkata"),  # what some browsers still report for India: stored as its current name
    ("Europe/Kiev", "Europe/Kyiv"),
    ("Europe/Oslo", "Europe/Oslo"),  # a country's own zone is never rewritten to another city
    ("America/Argentina/Buenos_Aires", "America/Argentina/Buenos_Aires"),
])
def test_the_browsers_zone_is_saved_like_any_other(client, browser_zone, stored):
    sign_in(client)
    r = set_zone(client, browser_zone)
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=time_zone"
    assert only_user().time_zone == stored
    page = client.get(r.headers["location"]).text
    assert [value for value, selected in options(page) if selected] == [stored]
    assert "Your time zone is set. If it is wrong, choose yours here." in page
    assert "from your browser or from Claude" not in page


@pytest.mark.parametrize("junk", ["", "Mars/Olympus", "EST", "GMT", "Etc/GMT+5", "Kolkata", "<b>x</b>"])
def test_a_junk_zone_is_not_saved_and_the_list_is_shown(client, junk):
    sign_in(client)
    r = set_zone(client, junk)
    assert r.status_code == 400 and only_user().time_zone is None
    assert 'class="error"' in r.text and "<b>x</b>" not in r.text
    assert '<select id="time-zone" name="time_zone" required>' in r.text
    assert options(r.text)[0] == ("", True) and len(options(r.text)) > 400


def test_a_refused_browser_value_is_not_retried_by_the_server(client):
    sign_in(client)
    set_zone(client, "Not/AZone")
    assert only_user().time_zone is None  # the page still offers the script; its flag stops a second try


def test_settings_offers_a_list_with_the_current_zone_selected_and_no_typing(client):
    sign_in(client)
    set_zone(client, "Europe/London")
    page = client.get("/settings").text
    assert '<select id="time-zone" name="time_zone" required>' in page
    assert re.search(r'<input[^>]*name="time_zone"', page) is None  # no free-text field, no hidden one
    assert "list=" not in page and "datalist" not in page and "as a name such as" not in page
    assert [value for value, selected in options(page) if selected] == ["Europe/London"]
    assert ("", False) not in options(page)
    assert '<label for="time-zone">Time zone</label>' in page


def test_with_no_zone_settings_says_utc_and_asks_for_a_choice(client):
    sign_in(client)
    page = client.get("/settings").text
    assert "No time zone yet, so today is counted in UTC. Choose yours from the list." in page
    assert '<option value="" selected>Choose your time zone</option>' in page
    assert options(page)[0] == ("", True) and sum(selected for _, selected in options(page)) == 1


def test_every_option_is_a_zone_the_server_accepts(client):
    sign_in(client)
    values = [value for value, _ in options(client.get("/settings").text) if value]
    assert len(values) == len(set(values)) > 400
    assert set(values) <= users._zone_names()  # every one is a name the zone database holds
    assert {"Asia/Kolkata", "Europe/Oslo", "America/New_York", "UTC"} <= set(values)
    assert "Asia/Calcutta" not in values and "US/Eastern" not in values  # obsolete aliases left out
    for zone in (values[0], values[-1], "UTC"):
        assert set_zone(client, zone).status_code == 303 and only_user().time_zone == zone


def test_the_list_is_grouped_by_region_then_city(client):
    sign_in(client)
    page = client.get("/settings").text
    regions = re.findall(r'<optgroup label="([^"]+)">', page)
    assert regions[:2] == ["Africa", "America"] and regions[-1] == "Other"
    assert regions[:-1] == sorted(regions[:-1])
    europe = re.search(r'<optgroup label="Europe">(.*?)</optgroup>', page, re.S).group(1)
    labels = re.findall(r">([^<]+)</option>", europe)
    assert labels == sorted(labels) and "London" in labels


def test_every_option_survives_the_one_zone_check_unchanged(client):
    sign_in(client)
    values = [value for value, _ in options(client.get("/settings").text) if value]
    assert len(values) > 400 and all(users.canonical_zone(value) == value for value in values)


def test_the_form_is_forgiven_on_the_web_too(client):
    sign_in(client)
    assert set_zone(client, "  america/new york ").status_code == 303
    assert only_user().time_zone == "America/New_York"


def test_without_the_zone_table_every_accepted_name_is_offered(monkeypatch):
    monkeypatch.setattr(users, "_tz_file", lambda name: None)
    users._zone_tables.cache_clear()
    web.zone_groups.cache_clear()
    try:
        names = web.zone_names()
        assert len(names) > 500 and "Asia/Calcutta" in names and "EST" not in names
        assert all(users.canonical_zone(name) == name for name in names)
    finally:
        users._zone_tables.cache_clear()
        web.zone_groups.cache_clear()


def test_a_saved_name_the_list_lacks_is_still_shown_selected():
    groups, selected = web.zone_choices("Etc/GMT+5")
    assert selected == "Etc/GMT+5" and groups[0][0] == "Saved"


def test_an_old_name_already_stored_selects_its_current_entry():
    groups, selected = web.zone_choices("Asia/Calcutta")
    assert selected == "Asia/Kolkata" and groups[0][0] != "Saved"



# ---------------------------------------------------------------- the script's guards, held by reading it

def script_source() -> str:
    """time-zone.js without its comments, so a guard named in a comment does not count."""
    text = (web.HERE / "static" / "time-zone.js").read_text(encoding="utf-8")
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def test_the_script_keeps_its_guards():
    js = script_source()
    send = js.index("form.submit()")
    # exactly one way out to the server, and no other request of any kind
    assert js.count("submit(") == 1 and js.count(".submit") == 1
    for other in ("fetch", "XMLHttpRequest", "sendBeacon", "location", "requestSubmit", "setTimeout", "reload"):
        assert other not in js, other
    # it acts only on a page that says the zone is missing
    marker = re.search(r'if \(!form \|\| document\.body\.getAttribute\("data-time-zone"\) !== "missing"\) return;', js)
    assert marker and marker.start() < send
    # no zone from the browser: nothing sent
    no_zone = re.search(r'if \(typeof zone !== "string" \|\| zone === ""\) return;', js)
    assert no_zone and no_zone.start() < send
    # once per browser session: the flag is read, and returns, before anything is sent ...
    tried = re.search(r'if \(window\.sessionStorage\.getItem\("spotter-time-zone-tried"\)\) return;', js)
    assert tried and tried.start() < send
    # ... and is set before the form is sent
    flag = js.index('window.sessionStorage.setItem("spotter-time-zone-tried", "1");')
    assert tried.start() < flag < send
    # a storage call that fails returns without sending
    guarded = re.search(r"try \{([^{}]*)\} catch \(e\) \{ return; \}", js[tried.start() - 20:send])
    assert guarded and "getItem" in guarded.group(1) and "setItem" in guarded.group(1)


def test_the_offered_list_has_no_old_names(client):
    sign_in(client)
    values = [value for value, _ in options(client.get("/settings").text) if value]
    assert all(users.canonical_zone(value) == value for value in values)
    assert not {"US/Eastern", "Asia/Calcutta", "Europe/Kiev", "Pacific/Truk"} & set(values)
    assert "UTC" in values


def test_with_no_zone_data_settings_still_offers_utc(client, monkeypatch):
    sign_in(client)
    monkeypatch.setattr(users, "_zone_names", lambda: frozenset())
    monkeypatch.setattr(users, "_tz_file", lambda name: None)
    users._zone_tables.cache_clear()
    web.zone_groups.cache_clear()
    try:
        page = client.get("/settings").text
        assert [value for value, _ in options(page)] == ["", "UTC"]
        assert set_zone(client, "UTC").status_code == 303 and only_user().time_zone == "UTC"
    finally:
        users._zone_tables.cache_clear()
        web.zone_groups.cache_clear()



# ================================================================ stage 3: the pages a person uses

def me() -> User:
    return only_user() if user_count() == 1 else None


def today_of(user: User):
    with db.session_scope() as session:
        return users.today_for(session.get(User, user.id))


def stock(user: User) -> dict:
    """One of each, through the services Claude uses: two Back Squat sessions (the second sets new bests),
    two weigh-ins (yesterday's and today's), three measurements today."""
    today = today_of(user)
    yesterday = (today - timedelta(days=1)).isoformat()
    squat = lambda kg: [{"exercise": "Back Squat", "weight_kg": kg, "reps": 5}] * 3  # noqa: E731
    first = services.log_lifts(user.id, {"date": yesterday, "sets": squat(60)}, "claude")
    second = services.log_lifts(user.id, {"sets": squat(65), "notes": "felt strong"}, "claude")
    old_weight = services.log_bodyweight(user.id, {"kg": "78.9", "date": yesterday}, "claude")
    weight = services.log_bodyweight(user.id, {"kg": "78.4"}, "claude")
    measured = [services.log_measurement(user.id, {"kind": kind, "value": value}, "claude")
                for kind, value in (("waist", "84.5"), ("steps", "9500"), ("sleep_hours", "7.25"))]
    for result in (first, second, old_weight, weight, *measured):
        assert "error" not in result, result
    return {"today": today, "yesterday": yesterday, "session": second["id"], "old_session": first["id"],
            "weigh_in": weight["id"], "old_weigh_in": old_weight["id"],
            "measurements": [m["id"] for m in measured]}


@pytest.fixture
def stocked(client):
    sign_in(client)
    return stock(only_user())


def day_of(client, on) -> str:
    r = client.get(f"/day/{on}")
    assert r.status_code == 200, r.text
    return r.text


def rows(model, user_id: int) -> list:
    with db.session_scope() as session:
        return list(session.scalars(select(model).where(model.user_id == user_id).order_by(model.id)))


def calls(user_id: int) -> list[tuple[str, bool, str | None]]:
    with db.session_scope() as session:
        return [(c.function, c.ok, c.error_code) for c in
                session.scalars(select(CallLog).where(CallLog.user_id == user_id).order_by(CallLog.id))]


def post(client, path: str, **data):
    return client.post(path, data={**data, "csrf_token": session_of(client)["csrf"]})


# ---------------------------------------------------------------- Today, and a person with no entries

@pytest.mark.parametrize("utc, local_day", [
    (datetime(2026, 11, 9, 18, 29, 59, tzinfo=timezone.utc), "2026-11-09"),  # 23:59:59 in Kolkata
    (datetime(2026, 11, 9, 18, 30, 0, tzinfo=timezone.utc), "2026-11-10"),   # midnight in Kolkata
])
def test_today_goes_to_the_day_in_the_persons_own_zone(client, monkeypatch, utc, local_day):
    sign_in(client)
    services.update_settings(only_user().id, {"time_zone": "Asia/Kolkata"}, "web")
    monkeypatch.setattr(db, "utcnow", lambda: utc)
    r = client.get("/today")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/day/" + local_day


def test_signed_out_every_new_page_goes_to_sign_in(client):
    for path in ("/today", "/day/2026-11-09", "/progress", "/progress/back-squat",
                 "/entries/bodyweight/1/edit"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"] == SITE + "/sign-in", path


def test_every_page_renders_for_a_person_with_no_entries(client):
    sign_in(client)
    today = today_of(only_user()).isoformat()
    page = day_of(client, today)
    assert f"Today, {today}" in page and "No lifts logged on this day" in page
    assert "No weigh-in on this day" in page and "No measurements on this day" in page
    assert f'action="/day/{today}/weigh-in"' in page and f'action="/day/{today}/measurement"' in page
    assert re.findall(r'<option value="([a-z_]+)">', page) == list(web.MEASUREMENT_KINDS)
    assert 'rel="next"' not in page  # nothing after today
    progress = client.get("/progress")
    assert progress.status_code == 200 and "No lifts logged yet" in progress.text and "No weigh-ins yet" in progress.text
    lift = client.get("/progress/back-squat")
    assert lift.status_code == 200 and "No sets of Back Squat logged yet" in lift.text
    settings_page = client.get("/settings")
    assert settings_page.status_code == 200 and 'name="claude_can" value="log" checked' in settings_page.text


def test_the_measurement_kinds_carry_the_services_units(client):
    sign_in(client)
    page = day_of(client, today_of(only_user()).isoformat())
    for kind, (unit, _, _, _) in rules.MEASUREMENTS.items():
        assert re.search(rf'<option value="{kind}">[^<]+\({re.escape(unit)}\)</option>', page), kind


# ---------------------------------------------------------------- a day with one of each

def test_the_day_shows_sessions_sets_bests_weigh_in_and_measurements(client, stocked):
    page = day_of(client, stocked["today"].isoformat())
    assert page.count("<td>Back Squat</td>") == 3 and page.count("<td>65 kg</td>") == 3
    assert "felt strong" in page
    assert "New best: Back Squat estimated max" in page and "(was 70 kg)" in page
    assert "<strong>78.4 kg</strong>" in page
    assert "Waist: <strong>84.5 cm</strong>" in page and "Steps: <strong>9500 steps</strong>" in page
    assert "Sleep: <strong>7.25 h</strong>" in page
    entries = [("lift_session", stocked["session"]), ("bodyweight", stocked["weigh_in"])] + \
              [("measurement", i) for i in stocked["measurements"]]
    for kind, entry_id in entries:
        assert f'href="/entries/{kind}/{entry_id}/edit"' in page, (kind, entry_id)
        assert f'action="/entries/{kind}/{entry_id}/delete"' in page, (kind, entry_id)
    assert page.count('action="/entries/') == 5
    assert f'href="/day/{stocked["yesterday"]}" rel="prev"' in page


def test_yesterday_links_on_to_today(client, stocked):
    page = day_of(client, stocked["yesterday"])
    assert f'href="/day/{stocked["today"].isoformat()}" rel="next"' in page
    assert "<td>60 kg</td>" in page and "78.9 kg" in page and "New best" not in page


def test_the_edit_page_renders_for_each_kind(client, stocked):
    weigh_in = client.get(f"/entries/bodyweight/{stocked['weigh_in']}/edit")
    assert weigh_in.status_code == 200 and 'name="kg" value="78.4"' in weigh_in.text
    measured = client.get(f"/entries/measurement/{stocked['measurements'][0]}/edit")
    assert measured.status_code == 200 and 'name="value" value="84.5"' in measured.text and "(cm)" in measured.text
    session = client.get(f"/entries/lift_session/{stocked['session']}/edit")
    assert session.status_code == 200 and ">felt strong</textarea>" in session.text
    assert "Sets are changed through Claude" in session.text and session.text.count("<td>Back Squat</td>") == 3
    for r in (weigh_in, measured, session):
        assert f'name="date" type="date" value="{stocked["today"].isoformat()}"' in r.text


def test_progress_shows_each_lift_and_the_body_weight_trend(client, stocked):
    page = client.get("/progress").text
    row = re.search(r'<tr>\s*<td><a href="/progress/back-squat">Back Squat</a></td>(.*?)</tr>', page, re.S).group(1)
    current = services.get_progress(only_user().id)["lifts"][0]["current_estimated_max_kg"]
    assert f"{rules.kg(current)} kg" in row and "65 kg × 5" in row
    assert f'<a href="/day/{stocked["today"].isoformat()}">' in row
    assert "Latest: <strong>78.4 kg</strong>" in page
    assert '<svg class="chart" id="bodyweight-chart"' in page and "<title" in page


def test_one_lifts_progress_shows_its_chart_bests_and_sessions(client, stocked):
    r = client.get("/progress/back-squat")
    assert r.status_code == 200
    assert '<svg class="chart" id="progress-chart"' in r.text and "Estimated max over time: Back Squat" in r.text
    assert "65 kg × 5" in r.text and "estimated max" in r.text and "(was 70 kg)" in r.text
    assert r.text.count("estimated max 70 kg") == 1 and "estimated max 75.83 kg" in r.text


def test_an_unknown_lift_a_bad_date_and_an_unknown_kind_are_404_pages(client, stocked):
    for path in ("/progress/not-a-lift", "/progress/back-squat-x", "/day/2026-02-30", "/day/20261109",
                 "/day/abc", "/day/2026-11-9", "/entries/goal/1/edit", "/entries/bodyweight/abc/edit",
                 "/entries/bodyweight/99999/edit", "/entries/call_log/1/edit"):
        r = client.get(path)
        assert r.status_code == 404 and "There is nothing here." in r.text, path
        assert "not-a-lift" not in r.text and "abc" not in r.text.split("<main>")[1], path
    for path in ("/entries/goal/1/delete", "/entries/bodyweight/99999/delete", "/entries/bodyweight/1x/restore"):
        assert post(client, path).status_code == 404, path


def test_someone_elses_entry_is_a_404_that_leaks_nothing(client, stocked, make_user):
    other = make_user()
    theirs = services.log_bodyweight(other.id, {"kg": "123.45"}, "claude")
    their_measure = services.log_measurement(other.id, {"kind": "chest", "value": "111.11"}, "claude")
    for kind, entry_id in (("bodyweight", theirs["id"]), ("measurement", their_measure["id"])):
        for r in (client.get(f"/entries/{kind}/{entry_id}/edit"),
                  post(client, f"/entries/{kind}/{entry_id}/edit", date="2026-01-01"),
                  post(client, f"/entries/{kind}/{entry_id}/delete"),
                  post(client, f"/entries/{kind}/{entry_id}/restore")):
            assert r.status_code == 404 and "There is nothing here." in r.text
            assert "123.45" not in r.text and "111.11" not in r.text
    assert [b.deleted_at for b in rows(Bodyweight, other.id)] == [None]
    assert [(m.value, m.deleted_at) for m in rows(Measurement, other.id)] == [(111.11, None)]
    assert "123.45" not in day_of(client, stocked["today"].isoformat())


# ---------------------------------------------------------------- logging from the day page

def test_the_weigh_in_form_logs_for_that_day(client, stocked):
    on = stocked["yesterday"]
    r = post(client, f"/day/{on}/weigh-in", kg="79.2")
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/day/{on}"
    live = [b for b in rows(Bodyweight, only_user().id) if b.deleted_at is None and b.date.isoformat() == on]
    assert [(b.kg, b.logged_by) for b in live] == [(79.2, "web")]
    assert "79.2 kg" in day_of(client, on)


def test_the_measurement_form_logs_for_that_day(client):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    r = post(client, f"/day/{on}/measurement", kind="resting_hr", value="58")
    assert r.status_code == 303
    [m] = rows(Measurement, only_user().id)
    assert (m.kind, m.value, m.unit, m.logged_by) == ("resting_hr", 58, "bpm", "web")
    assert "Resting heart rate: <strong>58 bpm</strong>" in day_of(client, on)


@pytest.mark.parametrize("data, near, says", [
    ({"kind": "waist", "value": "500"}, "measurement", "give waist in cm, from 30 to 300."),
    ({"kind": "steps", "value": "12.5"}, "measurement", "give steps, from 0 to 200,000, as a whole number."),
    ({"kind": "wrist", "value": "17"}, "measurement", "Choose a kind from the list."),
    ({"kind": "waist", "value": "abc"}, "measurement", "That value was not saved"),
])
def test_a_refused_measurement_shows_the_pages_own_words(client, data, near, says):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    r = post(client, f"/day/{on}/measurement", **data)
    assert r.status_code == 400 and says in r.text
    assert "is outside" not in r.text and "Give " not in r.text  # the service's text is for Claude
    assert rows(Measurement, only_user().id) == []


@pytest.mark.parametrize("kg", ["500", "abc", "", "19.99"])
def test_a_refused_weigh_in_shows_the_pages_own_words(client, kg):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    r = post(client, f"/day/{on}/weigh-in", kg=kg)
    assert r.status_code == 400 and "That weight was not saved: give it in kg, from 20 to 400." in r.text
    assert rows(Bodyweight, only_user().id) == []


def test_a_weigh_in_after_today_is_refused_by_date(client):
    sign_in(client)
    tomorrow = (today_of(only_user()) + timedelta(days=1)).isoformat()
    r = post(client, f"/day/{tomorrow}/weigh-in", kg="80")
    assert r.status_code == 400 and "That date cannot be used" in r.text
    assert rows(Bodyweight, only_user().id) == []


# ---------------------------------------------------------------- edit, delete, undo through the pages

def test_editing_a_weigh_in_moves_it_and_returns_to_its_new_day(client, stocked):
    two_days_ago = (stocked["today"] - timedelta(days=2)).isoformat()
    r = post(client, f"/entries/bodyweight/{stocked['weigh_in']}/edit", kg="77.95", date=two_days_ago)
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/day/{two_days_ago}"
    row = next(b for b in rows(Bodyweight, only_user().id) if b.id == stocked["weigh_in"])
    assert (row.kg, row.date.isoformat(), row.deleted_at) == (77.95, two_days_ago, None)
    assert "77.95 kg" in day_of(client, two_days_ago) and "78.4" not in day_of(client, stocked["today"].isoformat())


def test_editing_a_measurement_changes_its_value(client, stocked):
    waist = stocked["measurements"][0]
    r = post(client, f"/entries/measurement/{waist}/edit", value="83", date=stocked["today"].isoformat())
    assert r.status_code == 303
    assert next(m.value for m in rows(Measurement, only_user().id) if m.id == waist) == 83
    assert "Waist: <strong>83 cm</strong>" in day_of(client, stocked["today"].isoformat())


def test_editing_a_session_changes_its_notes_and_date_and_keeps_its_sets(client, stocked):
    two_days_ago = (stocked["today"] - timedelta(days=2)).isoformat()
    r = post(client, f"/entries/lift_session/{stocked['session']}/edit", notes="heavy day", date=two_days_ago)
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/day/{two_days_ago}"
    page = day_of(client, two_days_ago)
    assert "heavy day" in page and page.count("<td>65 kg</td>") == 3


def test_an_edit_with_nothing_changed_writes_nothing(client, stocked):
    before = calls(only_user().id)
    r = post(client, f"/entries/bodyweight/{stocked['weigh_in']}/edit", kg="78.40",
             date=stocked["today"].isoformat())
    assert r.status_code == 303 and calls(only_user().id) == before


def test_a_refused_edit_says_why_beside_the_field(client, stocked):
    r = post(client, f"/entries/bodyweight/{stocked['weigh_in']}/edit", kg="900", date=stocked["today"].isoformat())
    assert r.status_code == 400 and "That weight was not saved" in r.text and 'value="900"' in r.text
    taken = post(client, f"/entries/bodyweight/{stocked['weigh_in']}/edit", kg="78.4", date=stocked["yesterday"])
    assert taken.status_code == 400 and "That date cannot be used" in taken.text
    assert "day_taken" not in taken.text and "entry_id" not in taken.text
    assert next(b.kg for b in rows(Bodyweight, only_user().id) if b.id == stocked["weigh_in"]) == 78.4


@pytest.mark.parametrize("which", ["lift_session", "bodyweight", "measurement"])
def test_delete_then_undo_through_the_pages(client, stocked, which):
    entry_id = {"lift_session": stocked["session"], "bodyweight": stocked["weigh_in"],
                "measurement": stocked["measurements"][0]}[which]
    on = stocked["today"].isoformat()
    r = post(client, f"/entries/{which}/{entry_id}/delete")
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/day/{on}?deleted={which}:{entry_id}"
    page = client.get(r.headers["location"]).text
    assert "Deleted." in page and f'action="/entries/{which}/{entry_id}/restore"' in page
    assert f'href="/entries/{which}/{entry_id}/edit"' not in page
    model = {"lift_session": LiftSession, "bodyweight": Bodyweight, "measurement": Measurement}[which]
    assert next(e.deleted_at for e in rows(model, only_user().id) if e.id == entry_id) is not None

    undo_form = re.search(rf'<form method="post" action="/entries/{which}/{entry_id}/restore" class="inline">(.*?)</form>',
                          page, re.S).group(1)
    token = re.search(r'name="csrf_token" value="([^"]+)"', undo_form).group(1)
    back = client.post(f"/entries/{which}/{entry_id}/restore", data={"csrf_token": token})
    assert back.status_code == 303 and back.headers["location"] == SITE + f"/day/{on}"
    assert f'href="/entries/{which}/{entry_id}/edit"' in day_of(client, on)
    assert next(e.deleted_at for e in rows(model, only_user().id) if e.id == entry_id) is None


def test_undo_twice_says_there_is_nothing_to_undo(client, stocked):
    post(client, f"/entries/bodyweight/{stocked['weigh_in']}/delete")
    post(client, f"/entries/bodyweight/{stocked['weigh_in']}/restore")
    r = post(client, f"/entries/bodyweight/{stocked['weigh_in']}/restore")
    assert r.status_code == 409 and "nothing to undo" in r.text


@pytest.mark.parametrize("query", ["goal:1", "bodyweight:abc", "bodyweight", "<b>:1", "bodyweight:1;x"])
def test_a_forged_undo_link_shows_no_undo(client, stocked, query):
    page = client.get(f"/day/{stocked['today'].isoformat()}", params={"deleted": query}).text
    assert "Deleted." not in page and "/restore" not in page


def test_a_day_after_today_left_by_a_zone_change_still_has_a_page(client):
    sign_in(client)
    user = only_user()
    services.update_settings(user.id, {"time_zone": "Pacific/Kiritimati"}, "web")  # UTC+14
    services.log_bodyweight(user.id, {"kg": "70"}, "claude")
    later = today_of(user).isoformat()
    services.update_settings(user.id, {"time_zone": "Pacific/Pago_Pago"}, "web")  # UTC-11: a day or two behind
    today = today_of(user).isoformat()
    assert later > today
    page = day_of(client, later)
    assert "70 kg" in page
    assert f'href="/day/{later}" rel="next"' in day_of(client, today)
    assert client.get("/day/" + (date.fromisoformat(later) + timedelta(days=1)).isoformat()).status_code == 404


# ---------------------------------------------------------------- the switch

@pytest.mark.parametrize("value", ["read", "log_delete", "log"])
def test_the_switch_changes_claude_can_and_nothing_else(client, value):
    sign_in(client)
    services.update_settings(only_user().id, {"time_zone": "Asia/Kolkata"}, "web")
    before = {c.name: getattr(only_user(), c.key) for c in User.__table__.columns}
    r = post(client, "/settings/claude-can", claude_can=value)
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=claude_can"
    after = {c.name: getattr(only_user(), c.key) for c in User.__table__.columns}
    assert after["claude_can"] == value
    differ = {k for k in after if after[k] != before[k]}
    assert differ <= {"claude_can", "updated_at"}
    page = client.get(r.headers["location"]).text
    assert f'name="claude_can" value="{value}" checked' in page and "Saved." in page


@pytest.mark.parametrize("value", [None, "", "LOG", "admin", "log_delete ", "everything", "delete"])
def test_every_other_switch_value_is_refused(client, value):
    sign_in(client)
    data = {} if value is None else {"claude_can": value}
    r = post(client, "/settings/claude-can", **data)
    assert r.status_code == 400 and "Choose one of the three settings below." in r.text
    assert only_user().claude_can == "log"


def test_settings_says_what_each_setting_allows_and_that_claude_never_gets_the_switch(client):
    sign_in(client)
    page = client.get("/settings").text
    assert "Only you can change this, here: Claude is never offered this switch." in page
    for value in ("read", "log", "log_delete"):
        assert f'value="{value}"' in page and web.CLAUDE_CAN_LINES[value] in page
    assert page.count('type="radio" name="claude_can"') == 3


# ---------------------------------------------------------------- the zone save comes back to `next`

def test_the_zone_save_returns_to_the_page_it_came_from(client):
    sign_in(client)
    r = post(client, "/settings", time_zone="Asia/Kolkata", next="/day/2026-11-09")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/day/2026-11-09"
    assert only_user().time_zone == "Asia/Kolkata"


@pytest.mark.parametrize("next_", ["//evil.example", "https://evil.example", "///evil.example", "//evil.example/day",
                                   "https://evil.example/", "http:/evil", "/\\evil.example",
                                   "javascript:alert(1)", "evil", "", "/day/\r\nSet-Cookie:x", "/" + "a" * 600])
def test_a_next_that_is_not_a_local_path_returns_to_settings(client, next_):
    sign_in(client)
    r = post(client, "/settings", time_zone="Asia/Kolkata", next=next_)
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=time_zone"


def test_a_next_of_settings_shows_the_saved_line(client):
    sign_in(client)
    r = post(client, "/settings", time_zone="Asia/Kolkata", next="/settings")
    assert r.headers["location"] == SITE + "/settings?saved=time_zone"


# ---------------------------------------------------------------- every web write leaves a call-log row

def test_every_web_write_leaves_a_call_log_row_under_the_mcp_name(client, stocked):
    uid = only_user().id
    start = len(calls(uid))
    on = stocked["today"].isoformat()
    post(client, f"/day/{stocked['yesterday']}/weigh-in", kg="79")
    post(client, f"/day/{on}/measurement", kind="hips", value="99")
    post(client, f"/entries/measurement/{stocked['measurements'][0]}/edit", value="84", date=on)
    post(client, f"/entries/bodyweight/{stocked['weigh_in']}/delete")
    post(client, f"/entries/bodyweight/{stocked['weigh_in']}/restore")
    post(client, "/settings", time_zone="Europe/London")
    post(client, "/settings/claude-can", claude_can="read")
    post(client, f"/day/{on}/weigh-in", kg="999")  # refused: still a row
    assert calls(uid)[start:] == [
        ("log_bodyweight", True, None), ("log_measurement", True, None), ("edit_entry", True, None),
        ("delete_entry", True, None), ("restore_entry", True, None), ("update_settings", True, None),
        ("set_claude_can", True, None), ("log_bodyweight", False, "invalid_field")]
    assert {name for name, _, _ in calls(uid)[start:]} - {"set_claude_can"} <= set(services.WRITE_FUNCTIONS)


def test_a_crashing_write_still_leaves_a_row(web_client, monkeypatch):
    client = web_client(raise_server_exceptions=False)
    sign_in(client)

    def boom(*args):
        raise RuntimeError("bug")

    monkeypatch.setattr(services, "log_bodyweight", boom)
    assert post(client, f"/day/{today_of(only_user()).isoformat()}/weigh-in", kg="80").status_code == 500
    assert calls(only_user().id) == [("log_bodyweight", False, "internal_error")]


# ---------------------------------------------------------------- the write limit

TOO_MANY = {"error": "too_many_writes", "message": "120 writes in the last 10 minutes; the most is 120.",
            "fix": "Wait 3 minutes, then send it again.", "wait_minutes": 3}


@pytest.mark.parametrize("name, path, data", [
    ("log_bodyweight", "/day/{today}/weigh-in", {"kg": "80"}),
    ("log_measurement", "/day/{today}/measurement", {"kind": "waist", "value": "80"}),
    ("edit_entry", "/entries/bodyweight/{weigh_in}/edit", {"kg": "70", "date": "{today}"}),
    ("delete_entry", "/entries/bodyweight/{weigh_in}/delete", {}),
    ("restore_entry", "/entries/bodyweight/{weigh_in}/restore", {}),
    ("update_settings", "/settings", {"time_zone": "Asia/Kolkata"}),
    ("set_claude_can", "/settings/claude-can", {"claude_can": "read"}),
])
def test_a_limit_refusal_is_a_429_with_the_wait(client, stocked, monkeypatch, name, path, data):
    monkeypatch.setattr(services, name, lambda *args: dict(TOO_MANY))
    fill = {"today": stocked["today"].isoformat(), "weigh_in": stocked["weigh_in"]}
    r = post(client, path.format(**fill), **{k: v.format(**fill) for k, v in data.items()})
    assert r.status_code == 429 and "Try again in 3 minutes." in r.text and "nothing was saved" in r.text
    assert calls(only_user().id)[-1] == (name, False, "too_many_writes")


def test_the_real_limit_reaches_the_page(client, stocked):
    uid = only_user().id
    now = db.utcnow()
    with db.session_scope() as session:
        session.add_all(CallLog(function="log_bodyweight", started_at=now, ms=1, ok=True, error_code=None,
                                result_bytes=1, user_id=uid) for _ in range(services.WRITE_LIMIT))
    r = post(client, f"/day/{stocked['today'].isoformat()}/weigh-in", kg="80")
    assert r.status_code == 429 and re.search(r"Try again in \d+ minutes?\.", r.text)
    assert [b.kg for b in rows(Bodyweight, uid) if b.deleted_at is None and b.date == stocked["today"]] == [78.4]



# ---------------------------------------------------------------- review fixes

def test_the_first_day_there_is_has_no_page_and_the_next_one_no_prev_link(client):
    sign_in(client)
    assert client.get("/day/0001-01-01").status_code == 404
    page = day_of(client, "0001-01-02")
    assert 'rel="prev"' not in page and "/day/0001-01-01" not in page


def test_a_later_days_prev_link_goes_to_a_day_that_has_a_page(client, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 10, 10, 45, tzinfo=timezone.utc))
    sign_in(client)
    user = only_user()
    services.update_settings(user.id, {"time_zone": "Pacific/Kiritimati"}, "web")  # today 2026-11-11
    assert "error" not in services.log_bodyweight(user.id, {"kg": "70"}, "claude")
    services.update_settings(user.id, {"time_zone": "Pacific/Pago_Pago"}, "web")   # today 2026-11-09
    assert today_of(user).isoformat() == "2026-11-09"
    today = day_of(client, "2026-11-09")
    assert 'href="/day/2026-11-11" rel="next"' in today
    later = day_of(client, "2026-11-11")
    assert "70 kg" in later and 'href="/day/2026-11-09" rel="prev"' in later and 'rel="next"' not in later
    assert client.get("/day/2026-11-10").status_code == 404  # after today and empty: no page, and no link to it


HOSTILE_NOTES = '<script>alert(1)</script><img src=x onerror=alert(2)>'


def test_notes_are_shown_escaped_everywhere(client):
    sign_in(client)
    user = only_user()
    on = today_of(user).isoformat()
    made = services.log_lifts(user.id, {"sets": [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5}],
                                        "notes": HOSTILE_NOTES}, "claude")
    refused = post(client, f"/entries/lift_session/{made['id']}/edit", notes=HOSTILE_NOTES + "!", date="2099-01-01")
    assert refused.status_code == 400
    pages = [day_of(client, on), client.get(f"/entries/lift_session/{made['id']}/edit").text, refused.text]
    for page in pages:
        assert "<script>alert(1)" not in page and "<img src=x" not in page
        assert "&lt;script&gt;alert(1)&lt;/script&gt;&lt;img src=x onerror=alert(2)&gt;" in page


# ---------------------------------------------------------------- the call-log row comes first

def test_the_call_log_row_exists_while_the_write_runs_and_is_completed_after(client, monkeypatch):
    sign_in(client)
    uid = only_user().id
    seen = []

    def stand_in(user_id, data, by):
        seen.append(calls(uid))
        return {"kind": "bodyweight", "id": 1, "duplicate": False}

    monkeypatch.setattr(services, "log_bodyweight", stand_in)
    r = post(client, f"/day/{today_of(only_user()).isoformat()}/weigh-in", kg="80")
    assert r.status_code == 303
    assert seen == [[("log_bodyweight", False, "in_progress")]]  # stored before the service ran
    assert calls(uid) == [("log_bodyweight", True, None)]  # the same row, completed


def test_a_refused_write_completes_its_row_with_the_code(client, monkeypatch):
    sign_in(client)
    uid = only_user().id
    seen = []

    def stand_in(user_id, value):
        seen.append(calls(uid))
        return {"error": "invalid_field", "message": "no", "fix": "no"}

    monkeypatch.setattr(services, "set_claude_can", stand_in)
    assert post(client, "/settings/claude-can", claude_can="read").status_code == 400
    assert seen == [[("set_claude_can", False, "in_progress")]]
    assert calls(uid) == [("set_claude_can", False, "invalid_field")]


def test_a_crash_completes_the_row_as_internal_error(web_client, monkeypatch):
    client = web_client(raise_server_exceptions=False)
    sign_in(client)
    uid = only_user().id
    seen = []

    def boom(*args):
        seen.append(calls(uid))
        raise RuntimeError("bug")

    monkeypatch.setattr(services, "delete_entry", boom)
    assert post(client, "/entries/bodyweight/1/delete").status_code == 500
    assert seen == [[("delete_entry", False, "in_progress")]]
    assert calls(uid) == [("delete_entry", False, "internal_error")]
    with db.session_scope() as session:
        row = session.scalar(select(CallLog).where(CallLog.user_id == uid))
        assert row.result_bytes == 0 and row.ms >= 0


def test_a_lost_call_log_never_costs_the_write(client, monkeypatch):
    sign_in(client)

    def down(*args):
        raise OperationalError("INSERT", {}, Exception("log table gone"))

    monkeypatch.setattr(web.calllog, "start", down)
    monkeypatch.setattr(web.calllog, "finish", down)
    r = post(client, f"/day/{today_of(only_user()).isoformat()}/weigh-in", kg="80.5")
    assert r.status_code == 303 and [b.kg for b in rows(Bodyweight, only_user().id)] == [80.5]
