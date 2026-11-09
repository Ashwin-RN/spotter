"""The web pages and web sign-in, with the provider seam replaced so nothing touches the network.

Clients use the site's https address so the Secure session cookie is sent back, and never follow
redirects (a redirect to the provider would otherwise be fed back into this app).
"""

import asyncio
import base64
import json
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import itsdangerous
import jwt
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.testclient import TestClient

from app import db, links, services, users, web, web_auth
from app.config import Settings
from app.main import create_app
from app.models import User

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
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings"
    first = only_user()
    assert (first.subject, first.email, first.name) == ("user_01WEB", "asha@example.com", "Asha Web")
    assert session_of(client)[web.USER] == first.id

    page = client.get("/settings").text
    assert "user_01WEB" in page and f"<code>{first.id}</code>" in page and "asha@example.com" in page

    client.post("/sign-out", data={"csrf_token": session_of(client)["csrf"]})
    sign_in(client)
    assert user_count() == 1 and only_user().id == first.id
    assert seam.codes == [CODE, CODE]


def test_signed_in_sign_in_goes_to_settings(client, seam):
    sign_in(client)
    r = client.get("/sign-in")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings"
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
    assert {r.path for r in posting} == {"/sign-out", "/settings"}
    for route in posting:
        assert client.post(route.path).status_code == 403


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
