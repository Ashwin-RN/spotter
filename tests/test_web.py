"""The web pages and web sign-in, with the provider seam replaced so nothing touches the network.

Clients use the site's https address so the Secure session cookie is sent back, and never follow
redirects (a redirect to the provider would otherwise be fed back into this app).
"""

import asyncio
import base64
import hashlib
import html
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

from app import db, formatting, links, rules, services, users, web, web_auth
from app.config import Settings
from app.main import create_app
from app.models import Bodyweight, CallLog, CardioEntry, FoodEntry, LiftSession, Measurement, SavedFood, User

from . import text_rules as tr
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


def home_steps(page: str) -> list[str]:
    """The landing page's numbered steps, as plain text."""
    steps = re.search(r'<ol class="steps">(.*?)</ol>', page, re.S).group(1)
    items = re.split(r"\n    <li>", steps)[1:]
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", item)).strip() for item in items]


COMMAND = f'<code class="link-box" id="claude-code-command">claude mcp add --transport http --scope user spotter {RESOURCE}</code>'


def test_home_walks_a_newcomer_through_setup_in_six_steps(client):
    page = client.get("/").text
    steps = home_steps(page)
    assert len(steps) == 6
    starts = ["Sign in here.", "Connect Claude.", "Talk to it.", "What you get.", "Want to see it full first?",
              "You decide what Claude may do."]
    assert all(step.startswith(start) for step, start in zip(steps, starts)), steps
    assert "The first sign-in creates your account." in steps[0] and 'href="/sign-in"' in page
    assert "Customize, then Connectors, then \"+ Add\", then Add custom connector." in steps[1]
    assert "Enter a name, such as Spotter" in steps[1] and "Continue" in steps[1] and "--scope user" in steps[1] and RESOURCE in steps[1]
    assert COMMAND in page and "/mcp" in steps[1]
    assert 'press "+" in the message box, then Connectors' in steps[2] and '"whoami"' in steps[2] and "Logged back squat, 3 sets of 5 at 60 kg" in steps[2]
    assert "allow each call" in steps[2]
    for name in ("Today", "Progress", "Goals", "Foods", "Settings"):
        assert name in steps[3]
    assert '"Add sample data" on Today' in steps[4]
    assert "read only, log, or log and delete" in steps[5] and "It starts at log." in steps[5]
    assert "<script" not in page


def test_home_signed_in_keeps_the_connect_steps_and_the_pages(client):
    sign_in(client)
    page = client.get("/").text
    steps = home_steps(page)
    assert [s.split(".")[0] for s in steps] == ["Connect Claude", "Talk to it", "What you get"]
    assert COMMAND in page and "Sign in here" not in page and "Add sample data" not in page
    for path in ("/today", "/progress", "/goals", "/foods", "/settings"):
        assert f'<dt><a href="{path}">' in page, path


def test_home_shows_no_address_and_no_command_when_the_address_is_unset(provider, seam):
    unconfigured = Settings(None, None, database_url=settings().database_url, **WEB)
    with TestClient(create_app(unconfigured, fetch=provider.fetch), base_url=SITE, follow_redirects=False) as client:
        page = client.get("/").text
    assert "address is not set up" in page
    assert "claude mcp add" not in page and "claude-code-command" not in page and "link-box" not in page
    assert "/mcp" not in page and RESOURCE not in page


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


def test_form_expired_keeps_the_signed_in_frame_and_a_database_outage_there_is_not_a_500(client, monkeypatch):
    """The refusal looks the person up first, inside the database guard: signed in, the four tabs, none
    current; signed out, the signed-out header; the database down, the not-set-up page."""
    out = client.post("/settings", data={"time_zone": "UTC"})
    assert out.status_code == 403 and 'class="top-out"' in out.text and 'class="tabs"' not in out.text
    sign_in(client)
    r = client.post("/settings", data={"time_zone": "UTC"})
    assert r.status_code == 403 and "Form expired" in r.text
    assert '<nav class="tabs"' in r.text and 'class="top-out"' not in r.text and 'aria-current' not in r.text

    def down(user_id):
        raise OperationalError("SELECT 1", {}, Exception("unable to open database file"))

    monkeypatch.setattr(web, "_load_user", down)
    r = client.post("/settings", data={"time_zone": "UTC"})
    assert r.status_code == 503 and "cannot reach its database" in r.text


def test_every_non_get_web_route_checks_the_token(client):
    sign_in(client)
    posting = [r for r in web.routes(settings(**WEB)) if getattr(r, "methods", None) and "POST" in r.methods]
    assert {r.path for r in posting} == {
        "/sign-out", "/settings", "/settings/claude-can", "/day/{date}/weigh-in", "/day/{date}/measurement",
        "/entries/{kind}/{entry_id}/edit", "/entries/{kind}/{entry_id}/delete", "/entries/{kind}/{entry_id}/restore",
        "/day/{date}/food", "/day/{date}/cardio", "/foods", "/foods/{food_id}/edit", "/foods/{food_id}/delete",
        "/foods/{food_id}/restore", "/settings/targets", "/entries/food_entry/{entry_id}/confirm",
        "/goals", "/goals/{goal_id}/status", "/settings/sample", "/settings/sample/remove", "/settings/export",
        "/settings/delete"}
    filled = {"{date}": "2026-11-09", "{kind}": "bodyweight", "{entry_id}": "1", "{food_id}": "1", "{goal_id}": "1"}
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
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=unchanged"
    assert only_user().time_zone == "Europe/London"
    assert 'class="ok' not in client.get(r.headers["location"]).text  # nothing changed, so nothing says saved


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


def test_static_files_are_cached_for_an_hour_and_pages_never(client):
    """Stage 6, section H: /static gains one cache line; pages keep no-store; nothing else changes."""
    static = [client.get(path) for path in ("/static/style.css", "/static/time-zone.js", "/static/icons/today.svg")]
    pages = [client.get("/"), client.get("/sign-in")]
    sign_in(client)
    pages += [client.get("/settings"), client.get("/today")]
    for r in static:
        assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=3600"
    for r in pages:
        assert r.headers["cache-control"] == "no-store"
    for r in static + pages:
        assert r.headers["content-security-policy"] == web.PAGE_HEADERS["Content-Security-Policy"]
        assert (r.headers["x-frame-options"], r.headers["x-content-type-options"], r.headers["referrer-policy"]) == \
            ("DENY", "nosniff", "no-referrer")


def test_the_stylesheet_link_carries_the_files_content_version(client):
    version = hashlib.sha256((web.HERE / "static" / "style.css").read_bytes()).hexdigest()[:12]
    sign_in(client)
    for page in (client.get("/").text, client.get("/settings").text, client.get("/progress/no-such-lift").text):
        assert re.findall(r'<link rel="stylesheet" href="([^"]*)">', page) == [f"/static/style.css?v={version}"]
    r = client.get(f"/static/style.css?v={version}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")


def test_the_stylesheet_version_follows_its_bytes(tmp_path):
    one, two = tmp_path / "one.css", tmp_path / "two.css"
    one.write_bytes(b"a { color: var(--text); }\n")
    two.write_bytes(b"a { color: var(--text-2); }\n")
    assert web.style_version(one) != web.style_version(two)
    assert web.style_version(one) == hashlib.sha256(one.read_bytes()).hexdigest()[:12]
    assert re.fullmatch(r"[0-9a-f]{12}", web.style_version())


def test_a_missing_stylesheet_stops_nothing(tmp_path, monkeypatch):
    """Without the file the link goes plain, with no query, and the app still builds its pages."""
    missing = tmp_path / "gone.css"
    assert web.style_version(missing) is None and web.style_href(missing) == "/static/style.css"
    assert web.style_href() == f"/static/style.css?v={web.style_version()}"
    monkeypatch.setattr(web, "STYLESHEET", missing)
    monkeypatch.setattr(web.style_href, "__defaults__", (missing,))
    assert web._environment().globals["style_href"] == "/static/style.css"


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
        assert "not built yet" in r.text and "ask your assistant to read your numbers back" in r.text
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


def h1_text(page: str) -> str:
    """The page's h1 as it reads aloud: its text with the tags taken out, the spaces squashed."""
    h1 = re.search(r"<h1>(.*?)</h1>", page, re.S).group(1)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h1)).strip()


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
    assert h1_text(page) == formatting.day_heading(today, today) and "No lifts logged on this day" in page
    assert "No weigh-in on this day" in page and "No measurements on this day" in page
    assert f'action="/day/{today}/weigh-in"' in page and f'action="/day/{today}/measurement"' in page
    kinds = re.search(r'<select id="measurement-kind" name="kind" required>(.*?)</select>', page, re.S).group(1)
    assert re.findall(r'<option value="([a-z_]+)">', kinds) == list(web.MEASUREMENT_KINDS)
    assert 'rel="next"' not in page  # nothing after today
    progress = client.get("/progress")
    assert progress.status_code == 200 and "No lifts logged yet" in progress.text and "No weigh-ins yet" in progress.text
    lift = client.get("/progress/back-squat")
    assert lift.status_code == 200 and "No sets of Back Squat logged yet" in lift.text
    settings_page = client.get("/settings")
    assert settings_page.status_code == 200 and 'name="claude_can" value="log" checked' in settings_page.text


@pytest.mark.parametrize("on, heading", [("2026-11-10", "Today, Tuesday 10 November"),
                                         ("2026-11-09", "Yesterday, Monday 9 November"),
                                         ("2026-11-01", "Sunday 1 November"),
                                         ("2025-11-10", "Monday 10 November 2025")])
def test_the_day_heading_names_the_day_in_full(client, monkeypatch, on, heading):
    sign_in(client)
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc))
    page = day_of(client, on)
    assert h1_text(page) == heading


def test_the_measurement_kinds_carry_the_services_units(client):
    sign_in(client)
    page = day_of(client, today_of(only_user()).isoformat())
    for kind, (unit, _, _, _) in rules.MEASUREMENTS.items():
        if kind == "steps":  # its unit is its own word: named once, the unit is the word
            assert '<option value="steps">Steps</option>' in page
            continue
        assert re.search(rf'<option value="{kind}">[^<]+\({re.escape(unit)}\)</option>', page), kind


# ---------------------------------------------------------------- a day with one of each

def test_the_day_shows_sessions_sets_bests_weigh_in_and_measurements(client, stocked):
    page = day_of(client, stocked["today"].isoformat())
    assert page.count("<td>Back Squat</td>") == 3 and page.count('<td class="num">65</td>') == 3
    assert "felt strong" in page
    assert "New best: Back Squat estimated max" in page and "up from 70 kg" in page  # the service's own text
    assert entries_of(side_card(page, "Weigh-in"))[0]["big"] == "78.4\u00a0kg"
    assert [(e["name"], e["figures"]) for e in entries_of(side_card(page, "Measurements"))] == \
        [("Waist", "84.5\u00a0cm"), ("Steps", "9,500\u00a0steps"), ("Sleep", "7.25\u00a0h")]
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
    assert '<td class="num">60</td>' in page and "78.9\u00a0kg" in page and "New best" not in page


def test_the_edit_page_renders_for_each_kind(client, stocked):
    weigh_in = client.get(f"/entries/bodyweight/{stocked['weigh_in']}/edit")
    assert weigh_in.status_code == 200 and 'name="kg" value="78.4"' in weigh_in.text
    measured = client.get(f"/entries/measurement/{stocked['measurements'][0]}/edit")
    assert measured.status_code == 200 and 'name="value" value="84.5"' in measured.text and "(cm)" in measured.text
    session = client.get(f"/entries/lift_session/{stocked['session']}/edit")
    assert session.status_code == 200 and ">felt strong</textarea>" in session.text
    assert "Sets are changed through your assistant for now: tell it what to fix." in session.text
    assert session.text.count("<td>Back Squat</td>") == 3
    for r in (weigh_in, measured, session):
        assert f'name="date" type="date" value="{stocked["today"].isoformat()}"' in r.text


def test_progress_shows_each_lift_and_the_body_weight_trend(client, stocked):
    page = client.get("/progress").text
    row = re.search(r'<tr>\s*<td><a href="/progress/back-squat">Back Squat</a></td>(.*?)</tr>', page, re.S).group(1)
    current = services.get_progress(only_user().id)["lifts"][0]["current_estimated_max_kg"]
    assert formatting.show(current, "estimated_max", page=True) in row and "65\u00a0kg × 5" in row
    assert f'<a href="/day/{stocked["today"].isoformat()}">' in row
    assert "Latest: <strong>78.4\u00a0kg</strong>" in page
    assert '<svg class="chart" id="bodyweight-chart"' in page and "<title" in page


BODY_WEIGHT_WORDS = ("No weigh-ins yet.", "No weigh-ins in the last 28 days.", "The chart starts from the second weigh-in.")


def weigh_card(client, *weigh_ins: tuple[int, str]) -> str:
    """Log each (days back, kg) through the service Claude uses, then return Progress' Body weight card."""
    sign_in(client)
    user = only_user()
    today = today_of(user)
    for back, kg in weigh_ins:
        result = services.log_bodyweight(user.id, {"kg": kg, "date": (today - timedelta(days=back)).isoformat()},
                                         "claude")
        assert "error" not in result, result
    page = client.get("/progress").text
    return re.search(r"<h2>Body weight</h2>(.*?)</div>", page, re.S).group(1)


def assert_weight_line(card: str, line: str) -> None:
    """The 7-day average line, or the latest weigh-in in its place: shown whenever there is a weigh-in,
    labelled by the Trends card's own rule (web.weight_now_label), with the Trends card's figure."""
    assert line in card
    trends = services.get_trends(only_user().id)
    label = web.weight_now_label(trends)
    assert line.startswith(label[0].upper() + label[1:] + ": <strong>"), (label, line)
    if label == "7-day average":
        assert formatting.show(trends["bodyweight"]["now"], "bodyweight_derived", page=True) in line
        assert "Latest weigh-in:" not in card
    else:
        assert "7-day average" not in card


def test_body_weight_with_every_weigh_in_older_than_28_days_says_so(client):
    card = weigh_card(client, (60, "81.2"), (50, "80.9"), (40, "80.6"))
    assert "No weigh-ins in the last 28 days." in card
    assert "The chart starts from the second weigh-in." not in card and "appears from the second" not in card
    assert 'id="bodyweight-chart"' not in card and "Dots are weigh-ins" not in card
    assert "Latest: <strong>80.6 kg</strong>" in card
    assert_weight_line(card, "Latest weigh-in: <strong>80.6 kg</strong>.")


@pytest.mark.parametrize("weigh_ins, line", [
    # day 28 back is outside the 28 days, day 27 inside
    (((60, "81.2"), (28, "80.9"), (27, "80.6")), "Latest weigh-in: <strong>80.6 kg</strong>."),
    (((50, "81.2"), (40, "80.9"), (0, "80.2")), "7-day average: <strong>80.2 kg</strong>."),
])
def test_body_weight_with_one_weigh_in_in_the_28_days_waits_for_the_second(client, weigh_ins, line):
    card = weigh_card(client, *weigh_ins)
    assert "The chart starts from the second weigh-in." in card
    assert "No weigh-ins in the last 28 days." not in card and 'id="bodyweight-chart"' not in card
    assert f"Latest: <strong>{weigh_ins[-1][1]} kg</strong>" in card
    assert_weight_line(card, line)


@pytest.mark.parametrize("weigh_ins, line", [
    (((3, "81.0"), (0, "80.0")), "7-day average: <strong>80.5 kg</strong>."),
    # drawn, but none in the last 7 days
    (((60, "82.0"), (20, "81.0"), (10, "80.4")), "Latest weigh-in: <strong>80.4 kg</strong>."),
])
def test_body_weight_with_two_in_the_28_days_draws_the_chart(client, weigh_ins, line):
    card = weigh_card(client, *weigh_ins)
    assert '<svg class="chart" id="bodyweight-chart"' in card and "Dots are weigh-ins" in card
    assert not [words for words in BODY_WEIGHT_WORDS if words in card]
    assert_weight_line(card, line)


def test_body_weight_with_no_weigh_in_points_to_today(client):
    card = weigh_card(client)
    assert 'No weigh-ins yet. Add one on <a href="/today">Today</a>.' in card
    assert not [words for words in BODY_WEIGHT_WORDS[1:] if words in card]
    assert "Latest" not in card and "7-day average" not in card and 'id="bodyweight-chart"' not in card


def test_one_lifts_progress_shows_its_chart_bests_and_sessions(client, stocked):
    r = client.get("/progress/back-squat")
    assert r.status_code == 200
    assert '<svg class="chart" id="progress-chart"' in r.text and "Estimated max over time: Back Squat" in r.text
    assert "65\u00a0kg × 5" in r.text and "estimated max" in r.text and "(was 70\u00a0kg)" in r.text
    assert r.text.count("estimated max 70\u00a0kg") == 1 and "estimated max 75.8\u00a0kg" in r.text


def test_a_best_and_the_one_it_beat_widen_together_only_when_they_print_the_same(client, monkeypatch):
    """D5 on the lift page: an estimated max pair that prints the same at 1 dp shows both at 2 dp, together.
    A pair that prints apart stays at 1 dp, and a heaviest weight is as entered."""
    sign_in(client)
    bests = [{"date": "2026-11-09", "kind": "estimated_max", "value": 80.04, "previous": 80.01},
             {"date": "2026-11-09", "kind": "heaviest_weight", "value": 80.25, "previous": 80.2},
             {"date": "2026-11-02", "kind": "estimated_max", "value": 81.7, "previous": 80.0}]
    lift = {"exercise": "Back Squat", "sessions": 2, "current_estimated_max_kg": 80.04, "last_trained": "2026-11-09",
            "best_set": {"date": "2026-11-09", "weight_kg": 70.0, "reps": 4, "estimated_max_kg": 80.04},
            "estimated_max_over_time": [{"date": "2026-11-02", "estimated_max_kg": 80.01},
                                        {"date": "2026-11-09", "estimated_max_kg": 80.04}],
            "bests": bests, "goals": []}
    monkeypatch.setattr(services, "get_progress", lambda *args: {"today": "2026-11-10", "lift": lift})
    page = client.get("/progress/back-squat").text
    shown = re.search(r"<h2>Bests</h2>(.*?)</div>", page, re.S).group(1)
    assert "estimated max 80.04\u00a0kg (was 80.01\u00a0kg)" in shown
    assert "heaviest weight 80.25\u00a0kg (was 80.2\u00a0kg)" in shown
    assert "estimated max 81.7\u00a0kg (was 80\u00a0kg)" in shown
    assert [web.best_widened(b) for b in bests] == [True, False, False]


def test_an_unknown_lift_a_bad_date_and_an_unknown_kind_are_404_pages(client, stocked):
    for path in ("/progress/not-a-lift", "/progress/back-squat-x", "/day/2026-02-30", "/day/20261109",
                 "/day/abc", "/day/2026-11-9", "/entries/goal/1/edit", "/entries/bodyweight/abc/edit",
                 "/entries/bodyweight/99999/edit", "/entries/call_log/1/edit"):
        r = client.get(path)
        assert r.status_code == 404 and "There is nothing here." in r.text, path
        assert "not-a-lift" not in r.text and "abc" not in re.split(r"<main\b[^>]*>", r.text)[1], path
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
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/day/{on}?saved=bodyweight"
    live = [b for b in rows(Bodyweight, only_user().id) if b.deleted_at is None and b.date.isoformat() == on]
    assert [(b.kg, b.logged_by) for b in live] == [(79.2, "web")]
    assert "79.2\u00a0kg" in day_of(client, on)


def test_the_measurement_form_logs_for_that_day(client):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    r = post(client, f"/day/{on}/measurement", kind="resting_hr", value="58")
    assert r.status_code == 303
    [m] = rows(Measurement, only_user().id)
    assert (m.kind, m.value, m.unit, m.logged_by) == ("resting_hr", 58, "bpm", "web")
    [shown] = entries_of(side_card(day_of(client, on), "Measurements"))
    assert (shown["name"], shown["figures"]) == ("Resting heart rate", "58\u00a0bpm")


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
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/day/{two_days_ago}?saved=bodyweight"
    row = next(b for b in rows(Bodyweight, only_user().id) if b.id == stocked["weigh_in"])
    assert (row.kg, row.date.isoformat(), row.deleted_at) == (77.95, two_days_ago, None)
    assert "77.95\u00a0kg" in day_of(client, two_days_ago) and "78.4" not in day_of(client, stocked["today"].isoformat())


def test_editing_a_measurement_changes_its_value(client, stocked):
    waist = stocked["measurements"][0]
    r = post(client, f"/entries/measurement/{waist}/edit", value="83", date=stocked["today"].isoformat())
    assert r.status_code == 303
    assert next(m.value for m in rows(Measurement, only_user().id) if m.id == waist) == 83
    shown = entries_of(side_card(day_of(client, stocked["today"].isoformat()), "Measurements"))
    assert (shown[0]["name"], shown[0]["figures"]) == ("Waist", "83\u00a0cm")


def test_editing_a_session_changes_its_notes_and_date_and_keeps_its_sets(client, stocked):
    two_days_ago = (stocked["today"] - timedelta(days=2)).isoformat()
    r = post(client, f"/entries/lift_session/{stocked['session']}/edit", notes="heavy day", date=two_days_ago)
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/day/{two_days_ago}?saved=lift_session"
    page = day_of(client, two_days_ago)
    assert "heavy day" in page and page.count('<td class="num">65</td>') == 3


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
    assert f"{web.ENTRY_WORDS[which]} deleted." in page and f'action="/entries/{which}/{entry_id}/restore"' in page
    assert f'href="/entries/{which}/{entry_id}/edit"' not in page
    model = {"lift_session": LiftSession, "bodyweight": Bodyweight, "measurement": Measurement}[which]
    assert next(e.deleted_at for e in rows(model, only_user().id) if e.id == entry_id) is not None

    undo_form = re.search(rf'<form method="post" action="/entries/{which}/{entry_id}/restore" class="inline">(.*?)</form>',
                          page, re.S).group(1)
    token = re.search(r'name="csrf_token" value="([^"]+)"', undo_form).group(1)
    back = client.post(f"/entries/{which}/{entry_id}/restore", data={"csrf_token": token})
    assert back.status_code == 303 and back.headers["location"] == SITE + f"/day/{on}?restored={which}"
    assert f'href="/entries/{which}/{entry_id}/edit"' in day_of(client, on)
    assert next(e.deleted_at for e in rows(model, only_user().id) if e.id == entry_id) is None


def test_undo_twice_says_there_is_nothing_to_undo(client, stocked):
    post(client, f"/entries/bodyweight/{stocked['weigh_in']}/delete")
    post(client, f"/entries/bodyweight/{stocked['weigh_in']}/restore")
    r = post(client, f"/entries/bodyweight/{stocked['weigh_in']}/restore")
    assert r.status_code == 409 and "nothing to undo" in r.text


@pytest.mark.parametrize("query", ["goal:1", "bodyweight:abc", "bodyweight", "<b>:1", "bodyweight:1;x",
                                   # digits to str.isdigit that are no id, and no id at all
                                   "bodyweight:²", "bodyweight:١٢", "bodyweight:５",
                                   "bodyweight:0", "bodyweight:007", "bodyweight:"])
def test_a_forged_undo_link_shows_no_undo(client, stocked, query):
    page = client.get(f"/day/{stocked['today'].isoformat()}", params={"deleted": query}).text
    assert '<div class="ok undo">' not in page and "/restore" not in page
    assert 'class="ok' not in page  # no strip of any kind


def test_the_kinds_that_can_be_edited_are_the_kinds_with_words():
    """day_strip names a deleted entry by ENTRY_WORDS[kind] for any kind undo_target lets through."""
    assert web.EDIT_FIELDS.keys() == web.ENTRY_WORDS.keys()


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
    assert "70\u00a0kg" in page
    assert f'href="/day/{later}" rel="next"' in day_of(client, today)
    assert client.get("/day/" + (date.fromisoformat(later) + timedelta(days=1)).isoformat()).status_code == 404


# ---------------------------------------------------------------- the switch

@pytest.mark.parametrize("value", ["read", "log_delete", "log"])
def test_the_switch_changes_claude_can_and_nothing_else(client, value):
    sign_in(client)
    services.update_settings(only_user().id, {"time_zone": "Asia/Kolkata"}, "web")
    services.set_claude_can(only_user().id, "read" if value != "read" else "log")  # so the post changes it
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
    assert r.status_code == 303 and r.headers["location"] == SITE + "/day/2026-11-09?saved=time_zone"
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
    ("log_food", "/day/{today}/food", {"description": "Apple", "kcal": "80"}),
    ("log_cardio", "/day/{today}/cardio", {"exercise": "Outdoor Walk", "minutes": "20"}),
    ("save_food", "/foods", {"name": "Dosa", "serving_label": "1 dosa", "kcal_per_serving": "170"}),
    ("save_food", "/foods/{food}/edit", {"serving_label": "2 rotis", "kcal_per_serving": "240"}),
    ("delete_entry", "/foods/{food}/delete", {}),
    ("restore_entry", "/foods/{food}/restore", {}),
    ("update_settings", "/settings/targets", {"calorie_target_kcal": "2100"}),
    ("edit_entry", "/entries/food_entry/{estimate}/confirm", {}),
], ids=["weigh-in", "measurement", "edit", "delete", "restore", "zone", "switch", "food", "cardio", "save food",
        "edit food", "delete food", "restore food", "targets", "confirm"])
def test_a_limit_refusal_is_a_429_with_the_wait(client, stocked, monkeypatch, name, path, data):
    uid = only_user().id
    food = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    estimate = services.log_food(uid, {"entries": [{"description": "Dal", "kcal": 180, "estimated": True}]}, "claude")
    monkeypatch.setattr(services, name, lambda *args: dict(TOO_MANY))
    fill = {"today": stocked["today"].isoformat(), "weigh_in": stocked["weigh_in"], "food": food["id"],
            "estimate": estimate["ids"][0]}
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
    assert "70\u00a0kg" in later and 'href="/day/2026-11-09" rel="prev"' in later and 'rel="next"' not in later
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



# ================================================================ stage 4: food and cardio

ATTRIBUTION = ("Calorie estimates use MET values from the 2024 Adult Compendium of Physical Activities "
               "(CC BY-NC-ND 4.0).")


def stock_food(user: User, weigh_in: bool = True) -> dict:
    """One of each, the way Claude logs them: a saved food logged by id, an estimate, a stated number, and
    a treadmill run with a distance and the machine's number (with an estimate when there is a weigh-in)."""
    today = today_of(user).isoformat()
    roti = services.save_food(user.id, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    if weigh_in:
        assert "error" not in services.log_bodyweight(user.id, {"kg": "80"}, "claude")
    food = services.log_food(user.id, {"entries": [
        {"description": "Roti", "saved_food_id": roti["id"], "servings": 2, "meal": "lunch"},
        {"description": "Dal", "kcal": 180, "estimated": True, "meal": "lunch"},
        {"description": "Tea", "kcal": 40, "meal": "breakfast"}]}, "claude")
    run = services.log_cardio(user.id, {"exercise": "Treadmill Run", "minutes": 30, "distance_km": 5,
                                        "machine_kcal": 300}, "claude")
    for result in (roti, food, run):
        assert "error" not in result, result
    return {"today": today, "roti": roti["id"], "food": food["ids"], "run": run["id"],
            "estimate": run["estimate"]}


@pytest.fixture
def fed(client):
    sign_in(client)
    return stock_food(only_user())


def section(page: str, title: str) -> str:
    return re.search(rf'<h2>{title}</h2>(.*?)</div>\s*(?=<div class="card">|</div>\s*<div class="col-side">|$)',
                     page, re.S).group(1)


# One Food entry as the card draws it: line one the name, line two the figures, a grey line for a differing
# description, then the tags' slot and the actions' slot.
FOOD_ENTRY = re.compile(
    r'<li class="entry-line">\s*<div class="entry-main">\s*<span class="entry-name">(?P<name>[^<]*)</span>'
    r'\s*<span class="entry-figures">(?P<figures>[^<]*)</span>\s*(?:<span class="entry-note">(?P<note>[^<]*)</span>)?'
    r'\s*</div>'
    r'\s*<span class="entry-tags">(?P<tags>.*?)</span>\s*<span class="entry-actions">(?P<actions>.*?)</span>\s*</li>',
    re.S)


def food_entries(markup: str) -> list[dict]:
    """Each Food entry in the markup: its name, figures and note as read (entities decoded), its tags as
    (class, words), and its actions as (Confirm, Edit, Delete) present or not."""
    out = []
    for m in FOOD_ENTRY.finditer(markup):
        actions = m["actions"]
        out.append({"name": html.unescape(m["name"]), "figures": html.unescape(m["figures"]),
                    "note": html.unescape(m["note"]) if m["note"] is not None else None,
                    "tags": re.findall(r'<span class="tag ([^"]+)">([^<]*)</span>', m["tags"]),
                    "actions": ("/confirm" in actions, '<a class="action" href="/entries/food_entry/' in actions,
                                "/delete" in actions)})
    return out


def meal_of(page: str, meal: str) -> list[dict]:
    """The entries under one meal's h3."""
    return food_entries(re.search(rf"<h3>{meal}</h3>(.*?)</ul>", page, re.S).group(1))


def side_card(page: str, title: str):
    """One of div.col-side's cards, read as a tree: the div.card directly inside it whose first child is the
    h2 with that title."""
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    [side] = parse(page).find_all("div", "col-side")
    [card] = [c for c in side.elements() if c.is_("div", "card") and c.elements()
              and c.elements()[0].is_("h2") and c.elements()[0].text() == title]
    return card


def entries_of(card) -> list[dict]:
    """Each entry-line of a side card, as read: its name, its line two, its large figure, its labelled figures
    as (label, figure), its Notes lines, its tags as (class, words) with the slot they sit in, its Edit links
    and Delete forms."""
    out = []
    for line in card.find_all(cls="entry-line"):
        [main] = line.find_all("div", "entry-main")

        def first(cls, node=main):
            found = node.find_all(cls=cls)
            return found[0].text() if found else None
        figures = main.find_all("dl", "figures")
        out.append({
            "name": first("entry-name"), "figures": first("entry-figures"), "big": first("entry-big"),
            "labelled": [(d.find_all("dt")[0].text(), d.find_all("dd")[0].text()) for d in figures[0].elements()]
            if figures else [],
            "notes": [p.text() for p in line.find_all("p", "entry-note")],
            "tags": [(t.classes[1], t.text()) for t in line.find_all(cls="tag")],
            "tag_slots": [tuple(t.parent.classes) for t in line.find_all(cls="tag")],
            "edit": [a.attrs["href"] for a in line.find_all("a", "action")],
            "delete": [f.attrs["action"] for f in line.find_all("form") if f.attrs["action"].endswith("/delete")],
        })
    return out


# ---------------------------------------------------------------- the day page

def test_a_day_with_no_food_or_cardio(client):
    sign_in(client)
    page = day_of(client, today_of(only_user()).isoformat())
    food = section(page, "Food")
    # No food at all: the card's one empty line in place of the meals and the totals.
    assert '<p class="empty-line">No food logged on this day. Tell your assistant what you ate, or add it below.</p>' \
        in food
    assert '<div class="meal' not in food and "<dl" not in food and "Target" not in food and "Net" not in food
    assert "No cardio logged on this day" in page
    activities = re.search(r'<select id="cardio-exercise" name="exercise" required>(.*?)</select>', page, re.S).group(1)
    names = [e["name"] for e in services.list_exercises("cardio")["exercises"]]
    assert re.findall(r'<option value="([^"]+)">', activities) == names and len(names) >= 15
    assert ATTRIBUTION not in page and "<footer" not in page


def test_the_day_shows_food_by_meal_with_each_tag_and_the_numbers(client, fed):
    page = day_of(client, fed["today"])
    food = section(page, "Food")
    # decision 14: the saved food's name, then its kcal and the serving in its own words; the description
    # ("Roti", the name) is not shown twice
    roti, dal = meal_of(food, "Lunch")
    assert (roti["name"], roti["figures"], roti["note"], roti["tags"]) == \
        ("Roti", "240\u00a0kcal, 2\u00a0rotis", None, [("tag-saved_food", "saved food")])
    assert (dal["name"], dal["figures"], dal["note"], dal["tags"]) == \
        ("Dal", "180\u00a0kcal", None, [("tag-claude_estimate", "estimate")])
    [tea] = meal_of(food, "Breakfast")
    assert (tea["name"], tea["figures"], tea["tags"]) == ("Tea", "40\u00a0kcal", [("tag-you_said", "you said")])
    assert '<div class="meal empty">\n    <h3>Dinner</h3>' in food and '<div class="meal empty">\n    <h3>Snack</h3>' in food
    numbers = services.get_log(only_user().id, fed["today"], 1)["days"][0]["food"]
    assert numbers["total_kcal"] == 460 and numbers["estimated_kcal"] == 180
    assert '<dt class="total">Total</dt><dd class="total">460\u00a0kcal</dd>' in food
    assert "<dt>From estimates</dt><dd>180\u00a0kcal</dd>" in food
    assert f"<dt>Share from estimates</dt><dd>{formatting.show(numbers['estimated_share_pct'], 'percent')}</dd>" in food
    assert "Target" not in food and "Net" not in food  # no target, add-back off
    for entry_id in fed["food"]:
        assert f'href="/entries/food_entry/{entry_id}/edit"' in page
        assert f'action="/entries/food_entry/{entry_id}/delete"' in page


def test_a_target_shows_target_and_remaining_and_add_back_shows_net(client, fed):
    uid = only_user().id
    services.update_settings(uid, {"calorie_target_kcal": 2200}, "web")
    food = section(day_of(client, fed["today"]), "Food")
    assert "<dt>Target</dt><dd>2,200\u00a0kcal</dd>" in food and "<dt>Left</dt><dd>1,740\u00a0kcal left</dd>" in food
    assert "<dt>Net" not in food
    services.update_settings(uid, {"add_back_exercise_kcal": True}, "web")
    food = section(day_of(client, fed["today"]), "Food")
    net = services.get_log(uid, fed["today"], 1)["days"][0]["food"]["net_kcal"]
    assert net == 460 - fed["estimate"]["kcal"] and f"<dt>Net (food minus cardio)</dt><dd>{net}\u00a0kcal</dd>" in food


def test_the_day_shows_cardio_with_pace_estimate_and_machine_number_and_names_the_compendium(client, fed):
    page = day_of(client, fed["today"])
    [run] = entries_of(side_card(page, "Cardio"))
    assert run["name"] == "Treadmill Run"
    assert run["labelled"] == [("Time (m:ss)", "30:00"), ("Distance (km)", "5"), ("Pace (per km)", "6:00"),
                               ("Calories (kcal)", str(fed["estimate"]["kcal"])), ("Machine (kcal)", "300")]
    assert run["edit"] == [f"/entries/cardio_entry/{fed['run']}/edit"]
    assert page.count(ATTRIBUTION) == 1 and '<footer class="attribution">' in page
    assert "there was no weigh-in when it was logged" not in page


def test_cardio_with_no_weigh_in_has_no_estimate_a_note_and_no_attribution(client):
    sign_in(client)
    stock_food(only_user(), weigh_in=False)
    page = day_of(client, today_of(only_user()).isoformat())
    [run] = entries_of(side_card(page, "Cardio"))
    assert ("Calories (kcal)", "No estimate") in run["labelled"] and ("Machine (kcal)", "300") in run["labelled"]
    assert "there was no weigh-in when it was logged" in page
    assert ATTRIBUTION not in page


def test_the_undo_line_and_the_edit_page_name_a_kind_the_same_way(client, fed):
    uid = only_user().id
    session = services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]}, "claude")
    waist = services.log_measurement(uid, {"kind": "waist", "value": "84"}, "claude")
    ids = {"bodyweight": rows(Bodyweight, uid)[0].id, "measurement": waist["id"], "lift_session": session["id"],
           "food_entry": fed["food"][0], "cardio_entry": fed["run"]}
    named = {}
    for kind, entry_id in ids.items():
        edit = client.get(f"/entries/{kind}/{entry_id}/edit").text
        on_edit = re.search(r"<h1>Edit (.+?)</h1>", edit).group(1)
        day = day_of(client, f"{fed['today']}?deleted={kind}:{entry_id}")
        on_undo = re.search(r'<div class="ok undo">\s*(.+?) deleted\.', day).group(1)
        assert on_undo.casefold() == on_edit, (kind, on_undo, on_edit)
        named[kind] = on_edit
    assert named == {"bodyweight": "weigh-in", "measurement": "measurement", "lift_session": "lift session",
                     "food_entry": "food entry", "cardio_entry": "cardio session"}


def test_a_session_logged_before_the_days_weigh_in_still_says_no_estimate_truly(client):
    """The estimate is stored when the session is logged. A weigh-in later the same day does not give it
    one, so the row and its note must stay true beside the day's weigh-in."""
    sign_in(client)
    stock_food(only_user(), weigh_in=False)
    assert "error" not in services.log_bodyweight(only_user().id, {"kg": "81.25"}, "claude")
    page = day_of(client, today_of(only_user()).isoformat())
    [run] = entries_of(side_card(page, "Cardio"))
    assert ("Calories (kcal)", "No estimate") in run["labelled"] and "No weigh-in" not in str(run)
    assert entries_of(side_card(page, "Weigh-in"))[0]["big"] == "81.25\u00a0kg"
    assert "there was no weigh-in when it was logged" in page and "there is no weigh-in" not in page


def test_a_day_with_thirty_food_entries(client):
    sign_in(client)
    uid = only_user().id
    entries = [{"description": f"Item {n}", "kcal": 10 + n, "meal": ("breakfast", "lunch", "dinner", "snack")[n % 4]}
               for n in range(30)]
    assert "error" not in services.log_food(uid, {"entries": entries}, "claude")
    page = day_of(client, today_of(only_user()).isoformat())
    assert sorted(e["name"] for e in food_entries(page)) == sorted(f"Item {n}" for n in range(30))
    assert page.count('href="/entries/food_entry/') == 30
    assert f'<dd class="total">{sum(10 + n for n in range(30))}\u00a0kcal</dd>' in page


def test_the_attribution_is_nowhere_else(client, fed):
    for path in ("/settings", "/foods", "/progress", f"/entries/food_entry/{fed['food'][0]}/edit", "/"):
        assert ATTRIBUTION not in client.get(path).text, path
    assert client.get(f"/entries/cardio_entry/{fed['run']}/edit").text.count(ATTRIBUTION) == 1


# ---------------------------------------------------------------- the two day-page forms

def test_the_food_form_logs_one_you_said_entry_and_leaves_the_meal_to_the_clock(client, monkeypatch):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    sent = []
    real = services.log_food
    monkeypatch.setattr(services, "log_food", lambda *args: sent.append(args) or real(*args))
    r = post(client, f"/day/{on}/food", description="Poha", kcal="250", meal="")
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/day/{on}?saved=food_entry"
    assert sent == [(only_user().id, {"entries": [{"description": "Poha", "kcal": "250"}], "date": on}, "web")]
    [entry] = rows(FoodEntry, only_user().id)
    assert (entry.description, entry.kcal, entry.source, entry.estimated, entry.logged_by) == \
        ("Poha", 250, "you_said", False, "web")
    assert entry.meal in web.MEALS


def test_the_food_form_sends_a_chosen_meal(client):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    post(client, f"/day/{on}/food", description="Rice", kcal="300", meal="dinner")
    assert [(e.meal, e.source) for e in rows(FoodEntry, only_user().id)] == [("dinner", "you_said")]


@pytest.mark.parametrize("data, says", [
    ({"description": "Rice", "kcal": "6000"}, "That kcal was not saved: give a whole number from 0 to 5,000."),
    ({"description": "Rice", "kcal": "abc"}, "That kcal was not saved"),
    ({"description": "", "kcal": "100"}, "Give a description, up to 200 characters."),
    ({"description": "Rice", "kcal": "100", "meal": "brunch"}, "Choose breakfast, lunch, dinner or snack."),
])
def test_a_refused_food_entry_says_why_in_the_pages_words(client, data, says):
    sign_in(client)
    r = post(client, f"/day/{today_of(only_user()).isoformat()}/food", **data)
    assert r.status_code == 400 and says in r.text and "Entry 1" not in r.text
    assert rows(FoodEntry, only_user().id) == []


def test_the_cardio_form_logs_activity_minutes_and_distance(client, monkeypatch):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    sent = []
    real = services.log_cardio
    monkeypatch.setattr(services, "log_cardio", lambda *args: sent.append(args) or real(*args))
    assert post(client, f"/day/{on}/cardio", exercise="Outdoor Walk", minutes="45", distance_km="").status_code == 303
    assert post(client, f"/day/{on}/cardio", exercise="Outdoor Run", minutes="25", distance_km="5").status_code == 303
    assert [a[1] for a in sent] == [{"exercise": "Outdoor Walk", "minutes": "45", "date": on},
                                    {"exercise": "Outdoor Run", "minutes": "25", "distance_km": "5", "date": on}]
    assert [(c.minutes, c.distance_km, c.logged_by) for c in rows(CardioEntry, only_user().id)] == \
        [(45, None, "web"), (25, 5, "web")]


@pytest.mark.parametrize("data, says", [
    ({"exercise": "Treadmill Run", "minutes": "0"}, "Give the minutes, from 1 to 600."),
    ({"exercise": "Treadmill Run", "minutes": "30", "distance_km": "900"}, "Give the distance in km"),
    ({"exercise": "Back Squat", "minutes": "30"}, "Choose an activity from the list."),
])
def test_a_refused_cardio_entry_says_why_in_the_pages_words(client, data, says):
    sign_in(client)
    r = post(client, f"/day/{today_of(only_user()).isoformat()}/cardio", **data)
    assert r.status_code == 400 and says in r.text and "is outside" not in r.text
    assert rows(CardioEntry, only_user().id) == []


# ---------------------------------------------------------------- the two edit pages

def test_the_food_edit_page_says_a_new_kcal_is_a_number_you_said(client, fed):
    dal = fed["food"][1]
    page = client.get(f"/entries/food_entry/{dal}/edit").text
    assert '<span class="tag tag-claude_estimate">estimate</span>' in page
    assert "If you change the kcal, it becomes a number you said." in page
    assert 'name="description" value="Dal"' in page and 'name="kcal" value="180"' in page
    assert '<option value="lunch" selected>' in page


def test_editing_a_foods_kcal_makes_it_you_said(client, fed):
    dal = fed["food"][1]
    r = post(client, f"/entries/food_entry/{dal}/edit", description="Dal", kcal="150", meal="lunch", date=fed["today"])
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/day/{fed['today']}?saved=food_entry"
    row = next(e for e in rows(FoodEntry, only_user().id) if e.id == dal)
    assert (row.kcal, row.source, row.estimated) == (150, "you_said", False)
    dal_row = next(e for e in meal_of(day_of(client, fed["today"]), "Lunch") if e["name"] == "Dal")
    assert (dal_row["figures"], dal_row["tags"]) == ("150\u00a0kcal", [("tag-you_said", "you said")])


def test_editing_a_foods_description_or_meal_keeps_its_tag(client, fed):
    dal = fed["food"][1]
    post(client, f"/entries/food_entry/{dal}/edit", description="Dal tadka", kcal="180", meal="dinner",
         date=fed["today"])
    row = next(e for e in rows(FoodEntry, only_user().id) if e.id == dal)
    assert (row.description, row.meal, row.source) == ("Dal tadka", "dinner", "claude_estimate")


def test_the_cardio_edit_page_and_a_re_estimate(client, fed):
    page = client.get(f"/entries/cardio_entry/{fed['run']}/edit").text
    assert '<option value="Treadmill Run" selected>' in page and 'name="minutes" value="30"' in page
    assert 'name="distance_km" value="5"' in page and 'name="machine_kcal" value="300"' in page
    assert f"Calorie estimate now: {fed['estimate']['kcal']}\u00a0kcal" in page
    r = post(client, f"/entries/cardio_entry/{fed['run']}/edit", exercise="Treadmill Run", minutes="60",
             distance_km="5", incline_pct="", speed_kmh="", avg_hr="", machine_kcal="", notes="", date=fed["today"])
    assert r.status_code == 303
    row = next(c for c in rows(CardioEntry, only_user().id) if c.id == fed["run"])
    assert row.minutes == 60 and row.machine_kcal is None
    assert abs(row.estimated_kcal - 2 * fed["estimate"]["kcal"]) <= 1  # worked out again for twice the minutes


def test_a_refused_cardio_edit_says_why_beside_the_field(client, fed):
    r = post(client, f"/entries/cardio_entry/{fed['run']}/edit", exercise="Treadmill Run", minutes="700",
             distance_km="5", machine_kcal="300", date=fed["today"])
    assert r.status_code == 400 and "Give the minutes, from 1 to 600." in r.text and 'value="700"' in r.text
    assert next(c.minutes for c in rows(CardioEntry, only_user().id) if c.id == fed["run"]) == 30


@pytest.mark.parametrize("which", ["food_entry", "cardio_entry"])
def test_delete_then_undo_food_and_cardio(client, fed, which):
    entry_id = fed["food"][0] if which == "food_entry" else fed["run"]
    r = post(client, f"/entries/{which}/{entry_id}/delete")
    assert r.headers["location"] == SITE + f"/day/{fed['today']}?deleted={which}:{entry_id}"
    page = client.get(r.headers["location"]).text
    assert f'href="/entries/{which}/{entry_id}/edit"' not in page
    token = re.search(rf'action="/entries/{which}/{entry_id}/restore" class="inline">\s*'
                      r'<input type="hidden" name="csrf_token" value="([^"]+)">', page).group(1)
    assert client.post(f"/entries/{which}/{entry_id}/restore", data={"csrf_token": token}).status_code == 303
    assert f'href="/entries/{which}/{entry_id}/edit"' in day_of(client, fed["today"])


# ---------------------------------------------------------------- Foods

def test_foods_with_none_saved(client):
    sign_in(client)
    page = client.get("/foods").text
    assert "No saved foods yet." in page and 'action="/foods"' in page


def test_foods_lists_name_serving_kcal_and_times_logged(client, fed):
    page = client.get("/foods").text
    row = re.search(r"<tr>\s*<td>Roti</td>(.*?)</tr>", page, re.S).group(1)
    assert re.findall(r"<td>(.*?)</td>", row, re.S)[:3] == ["1 roti", "120", "1"]
    assert f'href="/foods/{fed["roti"]}/edit"' in row and f'action="/foods/{fed["roti"]}/delete"' in row


def test_adding_a_food_from_the_page(client):
    sign_in(client)
    r = post(client, "/foods", name="Idli", serving_label="1 idli", kcal_per_serving="58", grams_per_serving="")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/foods?saved=1"
    [food] = rows(SavedFood, only_user().id)
    assert (food.name, food.serving_label, food.kcal_per_serving, food.grams_per_serving) == ("Idli", "1 idli", 58, None)
    assert "Saved." in client.get(r.headers["location"]).text


@pytest.mark.parametrize("data, field_says", [
    ({"name": "", "serving_label": "1", "kcal_per_serving": "10"}, "Give the food a name"),
    ({"name": "Idli", "serving_label": "", "kcal_per_serving": "10"}, "Say what one serving is"),
    ({"name": "Idli", "serving_label": "1 idli", "kcal_per_serving": "9999"}, "Give the kcal in one serving"),
])
def test_a_refused_food_says_why_beside_the_field(client, data, field_says):
    sign_in(client)
    r = post(client, "/foods", **data)
    assert r.status_code == 400 and field_says in r.text and rows(SavedFood, only_user().id) == []
    assert f'value="{data["name"]}"' in r.text


def test_editing_a_food_updates_it_and_keeps_what_was_logged(client, fed):
    page = client.get(f"/foods/{fed['roti']}/edit").text
    assert 'name="kcal_per_serving" value="120"' in page and 'name="name"' not in page
    r = post(client, f"/foods/{fed['roti']}/edit", serving_label="1 roti", kcal_per_serving="110", grams_per_serving="40")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/foods?saved=1"
    [food] = rows(SavedFood, only_user().id)
    assert (food.kcal_per_serving, food.grams_per_serving) == (110, 40)
    assert next(e.kcal for e in rows(FoodEntry, only_user().id) if e.id == fed["food"][0]) == 240  # history kept


def test_deleting_a_food_then_undo(client, fed):
    r = post(client, f"/foods/{fed['roti']}/delete")
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/foods?deleted={fed['roti']}"
    page = client.get(r.headers["location"]).text
    assert "Saved food deleted." in page and "<td>Roti</td>" not in page
    token = re.search(rf'action="/foods/{fed["roti"]}/restore" class="inline">\s*'
                      r'<input type="hidden" name="csrf_token" value="([^"]+)">', page).group(1)
    back = client.post(f"/foods/{fed['roti']}/restore", data={"csrf_token": token})
    assert back.status_code == 303 and "<td>Roti</td>" in client.get("/foods").text
    assert post(client, f"/foods/{fed['roti']}/restore").status_code == 409


def test_someone_elses_food_is_a_404(client, fed, make_user):
    other = make_user()
    theirs = services.save_food(other.id, {"name": "Secret stew", "serving_label": "1 bowl", "kcal_per_serving": 321},
                                "claude")
    for r in (client.get(f"/foods/{theirs['id']}/edit"),
              post(client, f"/foods/{theirs['id']}/edit", serving_label="x", kcal_per_serving="1"),
              post(client, f"/foods/{theirs['id']}/delete"), post(client, f"/foods/{theirs['id']}/restore"),
              client.get("/foods/abc/edit")):
        assert r.status_code == 404 and "Secret stew" not in r.text and "321" not in r.text
    assert "Secret stew" not in client.get("/foods").text
    assert [f.deleted_at for f in rows(SavedFood, other.id)] == [None]


# ---------------------------------------------------------------- targets on Settings

def test_settings_sets_the_targets_and_add_back(client):
    sign_in(client)
    r = post(client, "/settings/targets", calorie_target_kcal="2200", target_sessions_per_week="4",
             target_cardio_minutes_per_week="150", add_back_exercise_kcal="on")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=targets"
    user = only_user()
    assert (user.calorie_target_kcal, user.target_sessions_per_week, user.target_cardio_minutes_per_week,
            user.add_back_exercise_kcal) == (2200, 4, 150, True)
    page = client.get(r.headers["location"]).text
    assert "Targets saved." in page and 'name="calorie_target_kcal" value="2200"' in page
    assert 'name="add_back_exercise_kcal" checked' in page


def test_settings_clears_a_target_and_turns_add_back_off(client):
    sign_in(client)
    services.update_settings(only_user().id, {"calorie_target_kcal": 2000, "add_back_exercise_kcal": True}, "web")
    post(client, "/settings/targets", calorie_target_kcal="", target_sessions_per_week="",
         target_cardio_minutes_per_week="")
    user = only_user()
    assert user.calorie_target_kcal is None and user.add_back_exercise_kcal is False


@pytest.mark.parametrize("field, value, says", [
    ("calorie_target_kcal", "100", "Give a daily target from 800 to 10,000 kcal"),
    ("target_sessions_per_week", "30", "Give sessions a week from 0 to 14"),
    ("target_cardio_minutes_per_week", "abc", "Give cardio minutes a week from 0 to 3,000"),
])
def test_a_refused_target_says_why_beside_the_field(client, field, value, says):
    sign_in(client)
    r = post(client, "/settings/targets", **{field: value})
    assert r.status_code == 400 and says in r.text and f'name="{field}" value="{value}"' in r.text
    assert getattr(only_user(), field) is None


def test_unchanged_targets_write_nothing(client):
    sign_in(client)
    before = calls(only_user().id)
    assert post(client, "/settings/targets", calorie_target_kcal="").status_code == 303
    assert calls(only_user().id) == before


# ---------------------------------------------------------------- call-log rows and escaping

def test_every_food_and_cardio_write_leaves_a_row_under_the_mcp_name(client, fed):
    uid = only_user().id
    start = len(calls(uid))
    on = fed["today"]
    post(client, f"/day/{on}/food", description="Apple", kcal="80")
    post(client, f"/day/{on}/cardio", exercise="Outdoor Walk", minutes="20")
    post(client, "/foods", name="Dosa", serving_label="1 dosa", kcal_per_serving="170")
    post(client, f"/entries/food_entry/{fed['food'][2]}/edit", description="Tea", kcal="45", meal="breakfast", date=on)
    post(client, f"/foods/{fed['roti']}/delete")
    post(client, f"/foods/{fed['roti']}/restore")
    post(client, "/settings/targets", calorie_target_kcal="2100")
    assert calls(uid)[start:] == [("log_food", True, None), ("log_cardio", True, None), ("save_food", True, None),
                                  ("edit_entry", True, None), ("delete_entry", True, None),
                                  ("restore_entry", True, None), ("update_settings", True, None)]
    assert {name for name, _, _ in calls(uid)[start:]} <= set(services.WRITE_FUNCTIONS)


HTML = '<script>alert(1)</script><img src=x onerror=alert(2)>'
ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;&lt;img src=x onerror=alert(2)&gt;"


def test_html_in_descriptions_and_food_names_is_escaped(client):
    sign_in(client)
    uid = only_user().id
    on = today_of(only_user()).isoformat()
    saved = services.save_food(uid, {"name": HTML, "serving_label": HTML[:60], "kcal_per_serving": 100}, "claude")
    logged = services.log_food(uid, {"entries": [{"description": HTML, "kcal": 100}]}, "claude")
    refused = post(client, f"/entries/food_entry/{logged['ids'][0]}/edit", description=HTML, kcal="99999", date=on)
    pages = [day_of(client, on), client.get(f"/entries/food_entry/{logged['ids'][0]}/edit").text, refused.text,
             client.get("/foods").text, client.get(f"/foods/{saved['id']}/edit").text]
    for page in pages:
        assert "<script>alert(1)" not in page and "<img src=x" not in page
        assert ESCAPED in page or ESCAPED[:40] in page


@pytest.mark.parametrize("path, data", [
    ("/day/{today}/food", {"description": "Apple", "kcal": "80"}),
    ("/day/{today}/cardio", {"exercise": "Outdoor Walk", "minutes": "20"}),
    ("/foods", {"name": "Dosa", "serving_label": "1 dosa", "kcal_per_serving": "170"}),
    ("/settings/targets", {"calorie_target_kcal": "2100"}),
])
def test_the_new_forms_refuse_a_missing_csrf_token(client, path, data):
    sign_in(client)
    path = path.format(today=today_of(only_user()).isoformat())
    for token in (None, "wrong"):
        sent = data if token is None else {**data, "csrf_token": token}
        assert client.post(path, data=sent).status_code == 403
    assert rows(FoodEntry, only_user().id) == [] and rows(CardioEntry, only_user().id) == []
    assert rows(SavedFood, only_user().id) == [] and only_user().calorie_target_kcal is None



# ---------------------------------------------------------------- review: Foods edits and the food form

def test_an_unchanged_food_edit_writes_nothing(client, fed):
    before = calls(only_user().id)
    r = post(client, f"/foods/{fed['roti']}/edit", serving_label="1 roti", kcal_per_serving="120", grams_per_serving="")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/foods?saved=unchanged"
    assert calls(only_user().id) == before


def test_times_logged_counts_live_entries_only(client, fed):
    uid = only_user().id
    second = services.log_food(uid, {"entries": [{"description": "Roti", "saved_food_id": fed["roti"], "servings": 3,
                                                  "meal": "dinner"}]}, "claude")
    times = lambda: re.findall(r"<td>(.*?)</td>", re.search(r"<tr>\s*<td>Roti</td>(.*?)</tr>",  # noqa: E731
                                                              client.get("/foods").text, re.S).group(1), re.S)[2]
    assert times() == "2"
    services.delete_entry(uid, "food_entry", second["ids"][0])
    assert times() == "1"


def test_a_food_edit_never_rewrites_another_food_by_name(client, fed):
    uid = only_user().id
    other = services.save_food(uid, {"name": "Other", "serving_label": "1 cup", "kcal_per_serving": 50}, "claude")
    r = post(client, f"/foods/{fed['roti']}/edit", name="Other", serving_label="1 roti", kcal_per_serving="999")
    assert r.status_code == 303
    foods = {f.id: (f.name, f.kcal_per_serving) for f in rows(SavedFood, uid)}
    assert foods[other["id"]] == ("Other", 50) and foods[fed["roti"]] == ("Roti", 999)


def test_a_crafted_food_form_cannot_name_an_id_or_a_tag(client, fed, monkeypatch):
    on = fed["today"]
    sent = []
    real = services.log_food
    monkeypatch.setattr(services, "log_food", lambda *args: sent.append(args) or real(*args))
    r = post(client, f"/day/{on}/food", description="Ghee", kcal="90", meal="snack", saved_food_id=str(fed["roti"]),
             usda_fdc_id="171", source="usda", estimated="true", grams="10", servings="2")
    assert r.status_code == 303
    assert sent[0][1] == {"entries": [{"description": "Ghee", "kcal": "90", "meal": "snack"}], "date": on}
    [entry] = [e for e in rows(FoodEntry, only_user().id) if e.description == "Ghee"]
    assert (entry.source, entry.estimated, entry.saved_food_id, entry.usda_fdc_id, entry.servings, entry.grams) == \
        ("you_said", False, None, None, None, None)


# ---------------------------------------------------------------- review: confirming an estimate

def test_confirm_makes_an_estimate_the_persons_number(client, fed):
    dal = fed["food"][1]
    start = len(calls(only_user().id))
    r = post(client, f"/entries/food_entry/{dal}/confirm")
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/day/{fed['today']}?saved=confirmed"
    row = next(e for e in rows(FoodEntry, only_user().id) if e.id == dal)
    assert (row.kcal, row.source, row.estimated) == (180, "you_said", False)
    assert calls(only_user().id)[start:] == [("edit_entry", True, None)]
    lunch = re.search(r"<h3>Lunch</h3>(.*?)</ul>", day_of(client, fed["today"]), re.S).group(1)
    dal_row = next(e for e in food_entries(lunch) if e["name"] == "Dal")
    assert (dal_row["figures"], dal_row["tags"]) == ("180\u00a0kcal", [("tag-you_said", "you said")])
    assert "/confirm" not in lunch


def test_confirm_is_offered_on_estimates_only(client, fed):
    page = day_of(client, fed["today"])
    roti, dal, tea = fed["food"]
    assert f'action="/entries/food_entry/{dal}/confirm"' in page
    assert f'action="/entries/food_entry/{roti}/confirm"' not in page
    assert f'action="/entries/food_entry/{tea}/confirm"' not in page
    assert page.count("/confirm") == 1


def test_confirming_a_number_that_is_already_the_persons_writes_nothing(client, fed):
    tea = fed["food"][2]
    before = calls(only_user().id)
    r = post(client, f"/entries/food_entry/{tea}/confirm")
    assert r.status_code == 303 and calls(only_user().id) == before
    assert next(e.source for e in rows(FoodEntry, only_user().id) if e.id == tea) == "you_said"


def test_confirming_someone_elses_estimate_is_a_404(client, fed, make_user):
    other = make_user()
    theirs = services.log_food(other.id, {"entries": [{"description": "Secret soup", "kcal": 777, "estimated": True}]},
                               "claude")
    for path in (f"/entries/food_entry/{theirs['ids'][0]}/confirm", "/entries/food_entry/abc/confirm",
                 "/entries/food_entry/99999/confirm"):
        r = post(client, path)
        assert r.status_code == 404 and "Secret soup" not in r.text and "777" not in r.text
    assert [(e.source, e.kcal) for e in rows(FoodEntry, other.id)] == [("claude_estimate", 777)]


def test_confirm_needs_the_csrf_token(client, fed):
    dal = fed["food"][1]
    for data in ({}, {"csrf_token": "wrong"}):
        assert client.post(f"/entries/food_entry/{dal}/confirm", data=data).status_code == 403
    assert next(e.source for e in rows(FoodEntry, only_user().id) if e.id == dal) == "claude_estimate"


def test_the_rendered_confirm_form_carries_a_token_that_works(client, fed):
    dal = fed["food"][1]
    page = day_of(client, fed["today"])
    form = re.search(rf'<form method="post" action="/entries/food_entry/{dal}/confirm" class="inline">(.*?)</form>',
                     page, re.S).group(1)
    token = re.search(r'name="csrf_token" value="([^"]+)"', form).group(1)
    r = client.post(f"/entries/food_entry/{dal}/confirm", data={"csrf_token": token})
    assert r.status_code == 303
    assert next(e.source for e in rows(FoodEntry, only_user().id) if e.id == dal) == "you_said"


@pytest.mark.parametrize("which", [0, 1, 2])
def test_the_rendered_delete_form_carries_a_token_that_works(client, fed, which):
    """Each Food entry's Delete form, posted as drawn: its own fields only, the token among them."""
    entry_id = fed["food"][which]
    card = food_card(day_of(client, fed["today"]))
    form = re.search(rf'<form method="post" action="/entries/food_entry/{entry_id}/delete" class="inline">(.*?)</form>',
                     card, re.S).group(1)
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form))
    assert set(fields) == {"csrf_token"} and fields["csrf_token"]
    r = client.post(f"/entries/food_entry/{entry_id}/delete", data=fields)
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/day/{fed['today']}?deleted=food_entry:{entry_id}"
    assert next(e.deleted_at for e in rows(FoodEntry, only_user().id) if e.id == entry_id) is not None


# ---------------------------------------------------------------- stage 6, step 4: the 404 and 500 pages

# Section H's page header, exactly, on an https site.
PAGE_HEADER = {"x-frame-options": "DENY", "x-content-type-options": "nosniff", "referrer-policy": "no-referrer",
               "content-security-policy": CSP, "cache-control": "no-store",
               "strict-transport-security": "max-age=31536000"}
# Section L's two designed pages: (h1, the message, the fix).
NOT_FOUND = ("Page not found", "There is nothing here.", "Go back to Today and start from there.")
PROBLEM = ("Something went wrong", "Spotter could not finish this. If you were saving something, it may not be saved.",
           "Check the page and try again.")
SIGNED_OUT_LINKS = [("/", "Home"), ("/sign-in", "Sign in")]
# An address with no hex digit in it, so no stylesheet version can hold it.
UNROUTED = "/zqwy-nowhere"


def assert_page_header(r, hsts: bool = True) -> None:
    for name, value in PAGE_HEADER.items():
        want = value if hsts or name != "strict-transport-security" else None
        assert r.headers.get(name) == want, (name, r.headers.get(name))


def card_links(page: str) -> list[tuple[str, str]]:
    """The message card's links: (address, words), in order."""
    actions = re.search(r'<div class="form-actions">(.*?)</div>', page, re.S).group(1)
    return re.findall(r'<a class="button (?:primary|secondary)" href="([^"]+)">([^<]+)</a>', actions)


def assert_designed(r, status: int, words: tuple[str, str, str], links_: list[tuple[str, str]],
                    signed_in: bool, hsts: bool = True) -> None:
    """The designed page: its status, the page header, one card with the h1, the message and the fix, its
    links, its title, and the frame it was drawn in."""
    h1, said, fix = words
    assert r.status_code == status
    assert_page_header(r, hsts)
    main = re.split(r"<main\b[^>]*>", r.text)[1].split("</main>")[0]
    assert main.count('<div class="card">') == 1
    assert f'<h1 class="title">{h1}</h1>' in main and f"<p>{said}</p>" in main
    assert f'<p class="muted">{fix}</p>' in main
    assert card_links(r.text) == links_
    assert f"<title>{h1} · Spotter</title>" in r.text
    assert ('<header class="top">' in r.text) is signed_in
    assert ('<header class="top-out">' in r.text) is not signed_in


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_an_unrouted_path_is_the_designed_404_signed_out_with_the_page_header(client, method):
    r = client.request(method, UNROUTED)
    assert_designed(r, 404, NOT_FOUND, SIGNED_OUT_LINKS, signed_in=False)
    assert "zqwy" not in r.text and "set-cookie" not in r.headers
    # Signed in it is the same page: an unrouted path carries no session, and leaves the session alone.
    sign_in(client)
    r = client.request(method, UNROUTED + "/deeper")
    assert_designed(r, 404, NOT_FOUND, SIGNED_OUT_LINKS, signed_in=False)
    assert "zqwy" not in r.text and "deeper" not in r.text and "set-cookie" not in r.headers
    assert client.get("/settings").status_code == 200


def test_a_wrong_method_on_a_page_is_the_404_page(client):
    for method, path in (("GET", "/sign-out"), ("GET", "/settings/sample"), ("POST", "/progress")):
        assert_designed(client.request(method, path), 404, NOT_FOUND, SIGNED_OUT_LINKS, signed_in=False)


def test_without_web_sign_in_the_unrouted_404_offers_home_only(web_client):
    client = web_client(workos_client_id=None, workos_api_key=None)
    assert_designed(client.get(UNROUTED), 404, NOT_FOUND, [("/", "Home")], signed_in=False)


def test_a_routed_404_keeps_the_signed_in_frame(client):
    sign_in(client)
    for r in (client.get("/day/zqwy"), post(client, "/entries/bodyweight/99999/delete")):
        assert_designed(r, 404, NOT_FOUND, [("/today", "Today")], signed_in=True)
        assert "zqwy" not in r.text and "99999" not in r.text


def boom(*args, **kwargs):
    raise RuntimeError("zqwy boom")


def test_a_crash_is_the_designed_500_signed_out_and_the_session_survives(web_client, monkeypatch):
    client = web_client(raise_server_exceptions=False)
    sign_in(client)
    monkeypatch.setattr(services, "get_progress", boom)
    r = client.get("/progress")
    assert_designed(r, 500, PROBLEM, [("/", "Home")], signed_in=False)
    assert "zqwy" not in r.text and "RuntimeError" not in r.text and "Traceback" not in r.text
    assert "set-cookie" not in r.headers and 'name="csrf_token"' not in r.text
    # The session is as it was: still signed in, with the same token.
    token = session_of(client)["csrf"]
    after = client.get("/settings")
    assert after.status_code == 200 and '<header class="top">' in after.text and session_of(client)["csrf"] == token
    # A crashing write is the same page, whatever the error's type.
    def missing(*args, **kwargs):
        raise KeyError("zqwy")

    monkeypatch.setattr(services, "log_bodyweight", missing)
    r = post(client, f"/day/{today_of(only_user()).isoformat()}/weigh-in", kg="80")
    assert_designed(r, 500, PROBLEM, [("/", "Home")], signed_in=False)
    assert client.get("/settings").status_code == 200


def test_a_crash_is_still_raised_after_the_page(client, monkeypatch):
    sign_in(client)
    monkeypatch.setattr(services, "get_progress", boom)
    with pytest.raises(RuntimeError, match="zqwy boom"):
        client.get("/progress")


@pytest.mark.parametrize("web_sign_in", [True, False], ids=["sign-in-on", "sign-in-off"])
def test_a_crash_on_a_signed_out_page_is_the_500_page_too(web_client, monkeypatch, web_sign_in):
    """Both arms of web.routes carry the 500 page: with web sign-in off there is no session middleware."""
    client = web_client(raise_server_exceptions=False, **({} if web_sign_in else
                                                          {"workos_client_id": None, "workos_api_key": None}))
    monkeypatch.setattr(web, "current_user", boom)
    assert_designed(client.get("/"), 500, PROBLEM, [("/", "Home")], signed_in=False)


def test_a_500_page_that_cannot_be_drawn_leaves_the_plain_500(web_client, monkeypatch, logs):
    client = web_client(raise_server_exceptions=False)
    monkeypatch.setattr(web, "problem", boom)  # every page, the 500 page too, asks it
    r = client.get("/")
    assert r.status_code == 500 and r.text == "Internal Server Error"
    lines = [rec.getMessage() for rec in logs.records if rec.name == "spotter" and "500 page" in rec.getMessage()]
    assert lines == ["The 500 page could not be drawn: RuntimeError"]  # the type only, never the error's text


def test_a_malformed_form_is_a_400_not_the_500_page(web_client):
    client = web_client(raise_server_exceptions=False)
    sign_in(client)
    r = client.post("/settings", content=b"x", headers={"content-type": "multipart/form-data"})
    assert r.status_code == 400 and "Something went wrong" not in r.text


def test_the_500_wrapper_lets_an_http_exception_pass_and_sends_nothing():
    from starlette.exceptions import HTTPException

    sent = []

    async def raising(scope, receive, send):
        raise HTTPException(400)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/settings", "headers": [], "query_string": b""}
    with pytest.raises(HTTPException):
        asyncio.run(web.PageErrors(raising)(scope, None, send))
    assert sent == []


def test_the_500_wrapper_sends_nothing_once_a_response_has_started():
    sent = []

    async def half(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("zqwy late")

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
    with pytest.raises(RuntimeError):
        asyncio.run(web.PageErrors(half)(scope, None, send))
    assert [m["type"] for m in sent] == ["http.response.start"]


def branch_client(provider, mcp: bool, raise_server_exceptions: bool = True) -> TestClient:
    """A client on create_app's MCP branch (mcp=True) or its no-MCP branch (settings.problem set)."""
    chosen = settings(**WEB) if mcp else Settings(None, None, database_url=settings().database_url, **WEB)
    return TestClient(create_app(chosen, fetch=provider.fetch), base_url=SITE, follow_redirects=False,
                      raise_server_exceptions=raise_server_exceptions)


# What /mcp, /.well-known and /static answer in each create_app branch, by method: the same as before the
# fallback. Recorded on the parent tree (45b3804) with every header but content-length. Allow follows the hash
# seed, so it is compared as a set. (status, headers, how the body starts); HEAD has no body.
SIX_METHODS = ("GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS")
PLAIN = {"content-type": "text/plain; charset=utf-8"}
PLAIN_404 = (404, PLAIN, "Not Found")
TO_MCP = (307, {"location": SITE + "/mcp"}, "")
STATIC_405 = (405, {**PLAIN, "allow": {"GET", "HEAD"}}, "Method Not Allowed")
MCP_401 = (401, {"content-type": "application/json", "www-authenticate":
                 'Bearer error="invalid_token", error_description="Authentication required", '
                 f'resource_metadata="{SITE}{METADATA_PATH}"'}, '{"error": "invalid_token"')
MCP_503 = (503, {"content-type": "application/json"}, '{"error":"not_configured"')
METADATA_200 = (200, {"cache-control": "public, max-age=3600", "content-type": "application/json", "vary": "Origin"},
                '{"resource":')
METADATA_405 = (405, {**PLAIN, "allow": {"GET", "HEAD", "OPTIONS"}}, "Method Not Allowed")
MCP_PATHS_ASKED = ("/mcp", "/mcp/", "/mcp/x", METADATA_PATH, "/.well-known/x", "/.well-known",
                   "/.well-known/oauth-authorization-server", "/static/nope.css", "/static/")


def answer_before(mcp: bool, method: str, path: str) -> tuple:
    """What the parent tree answered."""
    if path == "/mcp":
        return MCP_401 if mcp else MCP_503
    if path == "/mcp/":
        return TO_MCP
    if path == METADATA_PATH and mcp:
        return METADATA_200 if method in ("GET", "HEAD", "OPTIONS") else METADATA_405
    if path.startswith("/static/"):
        return PLAIN_404 if method in ("GET", "HEAD") else STATIC_405
    return PLAIN_404


def headers_of(r) -> dict:
    out = {k: v for k, v in r.headers.items() if k != "content-length"}
    if "allow" in out:
        out["allow"] = {m.strip() for m in out["allow"].split(",")}
    return out


@pytest.mark.parametrize("mcp", [True, False], ids=["mcp-on", "mcp-off"])
def test_mcp_well_known_and_static_answer_as_before_in_both_branches(provider, seam, mcp):
    with branch_client(provider, mcp) as client:
        for path in MCP_PATHS_ASKED:
            for method in SIX_METHODS:
                status, headers, body = answer_before(mcp, method, path)
                r = client.request(method, path)
                assert (r.status_code, headers_of(r)) == (status, headers), (method, path)
                assert r.text.startswith("" if method == "HEAD" else body), (method, path, r.text[:80])
        assert client.get("/static/style.css").status_code == 200


@pytest.mark.parametrize("mcp", [True, False], ids=["mcp-on", "mcp-off"])
def test_an_unrouted_path_is_the_404_page_in_both_branches(provider, seam, mcp):
    offered = SIGNED_OUT_LINKS if mcp else [("/", "Home")]  # without MCP settings there is no web sign-in
    with branch_client(provider, mcp) as client:
        for path in (UNROUTED, "/mcpx", "/.well-knownx", "/MCP", "/Mcp/x", "/.WELL-KNOWN/x"):
            for method in ("GET", "POST", "PUT", "DELETE", "OPTIONS"):
                # Without MCP settings there is no site address, so no HSTS, as on every page there.
                assert_designed(client.request(method, path), 404, NOT_FOUND, offered, signed_in=False, hsts=mcp)
            head = client.head(path)
            assert head.status_code == 404 and head.text == "", path
            assert head.headers["content-type"] == "text/html; charset=utf-8"
            assert_page_header(head, mcp)


@pytest.mark.parametrize("mcp", [True, False], ids=["mcp-on", "mcp-off"])
def test_under_a_root_path_mcp_and_the_metadata_answer_as_the_parent_did(provider, seam, mcp):
    """Served under a prefix, the fallback reads the path with the prefix removed, as Starlette's router does."""
    chosen = settings(**WEB) if mcp else Settings(None, None, database_url=settings().database_url, **WEB)
    with TestClient(create_app(chosen, fetch=provider.fetch), base_url=SITE, follow_redirects=False,
                    root_path="/x") as client:
        for method in SIX_METHODS:
            r = client.request(method, "/x/mcp")
            status, headers, body = MCP_401 if mcp else MCP_503
            assert r.status_code == status and r.headers["content-type"] == "application/json", (method, r.status_code)
            assert "x-frame-options" not in r.headers
        meta = client.get("/x" + METADATA_PATH)
        if mcp:
            assert meta.status_code == 200 and meta.headers["content-type"] == "application/json"
            assert meta.json()["resource"] == RESOURCE
        else:
            assert (meta.status_code, meta.text) == (404, "Not Found")
        assert_designed(client.get("/x/nope"), 404, NOT_FOUND, SIGNED_OUT_LINKS if mcp else [("/", "Home")],
                        signed_in=False, hsts=mcp)
        # A prefix that only looks alike is not stripped: "/xy/mcp" under "/x" is no /mcp.
        assert_designed(client.get("/xy/mcp"), 404, NOT_FOUND, SIGNED_OUT_LINKS if mcp else [("/", "Home")],
                        signed_in=False, hsts=mcp)


@pytest.mark.parametrize("root, path, read", [
    ("", "/mcp", "/mcp"),            # no root_path: the path as it is
    ("/x", "/y/mcp", "/y/mcp"),      # the path does not start with it: as it is
    ("/x", "/x", ""),                # the path is the root_path: empty
    ("/x", "/x/mcp", "/mcp"),        # "/" right after the root_path: the rest, from that "/"
    ("/x", "/xy/mcp", "/xy/mcp"),    # a prefix that only looks alike: as it is
])
def test_route_path_reads_the_path_as_the_router_does(root, path, read):
    scope = {"type": "http", "path": path, **({"root_path": root} if root else {})}
    assert web.route_path(scope) == read


@pytest.mark.parametrize("mcp", [True, False], ids=["mcp-on", "mcp-off"])
def test_a_crash_is_the_500_page_in_both_branches(provider, seam, monkeypatch, mcp):
    monkeypatch.setattr(web, "current_user", boom)
    with branch_client(provider, mcp, raise_server_exceptions=False) as client:
        assert_designed(client.get("/"), 500, PROBLEM, [("/", "Home")], signed_in=False, hsts=mcp)


def test_mcp_without_a_token_is_still_401_and_the_metadata_still_answers(client):
    r = rpc(client, "initialize")
    assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]
    meta = client.get(METADATA_PATH)
    assert meta.status_code == 200 and meta.json()["resource"] == RESOURCE


def test_the_404_page_never_repeats_the_address_or_a_query(client):
    sign_in(client)
    for r in (client.get(UNROUTED + "?q=zqwyx"), client.get("/day/zqwyx?saved=zqwyy"),
              client.get("/progress/zqwyx")):
        assert r.status_code == 404 and "zqwy" not in r.text
        assert 'name="next"' not in r.text


# ---------------------------------------------------------------- stage 6, step 4: every message page

def test_the_not_built_yet_message_says_your_assistant(web_client, monkeypatch):
    """The one message that named Claude, through a placeholder page switched on for the test."""
    monkeypatch.setitem(web.PLACEHOLDERS, "zqwy", "/zqwy-placeholder")
    client = web_client()
    sign_in(client)
    r = client.get("/zqwy-placeholder")
    assert r.status_code == 200 and "Meanwhile, ask your assistant to read your numbers back." in r.text
    assert "Claude" not in r.text


def test_a_message_page_links_to_today_signed_in_and_home_and_sign_in_signed_out(client):
    expired = client.post("/sign-out")  # signed out, no token
    assert expired.status_code == 403 and card_links(expired.text) == SIGNED_OUT_LINKS
    assert '<h1 class="title">Form expired</h1>' in expired.text
    assert '<p class="muted">Go back, reload the page and try again.</p>' in expired.text
    sign_in(client)
    weigh_in = services.log_bodyweight(only_user().id, {"kg": "80"}, "claude")["id"]
    back = post(client, f"/entries/bodyweight/{weigh_in}/restore")  # not deleted: nothing to undo
    assert back.status_code == 409 and card_links(back.text) == [("/today", "Today")]
    assert '<header class="top">' in back.text and '<h1 class="title">Already back</h1>' in back.text


# ---------------------------------------------------------------- stage 6, step 4: the marks on every write

def location(r) -> str:
    assert r.status_code == 303, (r.status_code, r.text[:200])
    return r.headers["location"]


def test_marked_adds_one_mark_before_any_fragment():
    assert web.marked("https://x.example/day/2026-11-09", "saved", "bodyweight") == \
        "https://x.example/day/2026-11-09?saved=bodyweight"
    assert web.marked("/day/2026-11-09?add=food#food", "saved", "time_zone") == \
        "/day/2026-11-09?add=food&saved=time_zone#food"
    assert web.marked("/day/x", "deleted", "bodyweight:5") == "/day/x?deleted=bodyweight:5"
    assert web.marked("/day/x", "saved", "a b&c") == "/day/x?saved=a+b%26c"


@pytest.mark.parametrize("path, data, kind", [
    ("weigh-in", {"kg": "79.2"}, "bodyweight"),
    ("measurement", {"kind": "waist", "value": "80"}, "measurement"),
    ("food", {"description": "Poha", "kcal": "250"}, "food_entry"),
    ("cardio", {"exercise": "Outdoor Walk", "minutes": "30"}, "cardio_entry"),
])
def test_each_day_write_is_marked_saved_with_its_kind_and_a_duplicate_is_not(client, path, data, kind):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    assert location(post(client, f"/day/{on}/{path}", **data)) == SITE + f"/day/{on}?saved={kind}"
    # The same again within minutes is a duplicate: nothing is written, so it is never marked saved.
    assert location(post(client, f"/day/{on}/{path}", **data)) == SITE + f"/day/{on}?saved=unchanged"


def test_an_edit_is_marked_with_its_kind_and_an_unchanged_one_is_not(client, stocked):
    on = stocked["today"].isoformat()
    edit = f"/entries/measurement/{stocked['measurements'][0]}/edit"
    assert location(post(client, edit, value="83", date=on)) == SITE + f"/day/{on}?saved=measurement"
    assert location(post(client, edit, value="83", date=on)) == SITE + f"/day/{on}?saved=unchanged"


def test_an_edit_the_service_finds_unchanged_is_not_marked_saved(client, stocked, monkeypatch):
    on = stocked["today"].isoformat()
    monkeypatch.setattr(services, "edit_entry",
                        lambda *args: {"error": "nothing_to_change", "message": "x", "fix": "y"})
    r = post(client, f"/entries/bodyweight/{stocked['weigh_in']}/edit", kg="70", date=on)
    assert location(r) == SITE + f"/day/{on}?saved=unchanged"


def test_confirm_is_marked_confirmed_and_a_number_already_yours_is_not(client, fed):
    roti, dal, tea = fed["food"]
    day = SITE + f"/day/{fed['today']}"
    assert location(post(client, f"/entries/food_entry/{dal}/confirm")) == day + "?saved=confirmed"
    assert location(post(client, f"/entries/food_entry/{tea}/confirm")) == day + "?saved=unchanged"


def test_undo_is_marked_restored_with_its_kind(client, fed):
    run = fed["run"]
    assert location(post(client, f"/entries/cardio_entry/{run}/delete")) == \
        SITE + f"/day/{fed['today']}?deleted=cardio_entry:{run}"
    assert location(post(client, f"/entries/cardio_entry/{run}/restore")) == \
        SITE + f"/day/{fed['today']}?restored=cardio_entry"


def test_unchanged_targets_and_an_unchanged_food_are_never_marked_saved(client, fed):
    r = post(client, "/settings/targets", calorie_target_kcal="")
    assert location(r) == SITE + "/settings?saved=unchanged"
    assert 'class="ok' not in client.get(location(r)).text
    # A real change keeps its own mark and its words.
    r = post(client, "/settings/targets", calorie_target_kcal="2000")
    assert location(r) == SITE + "/settings?saved=targets"
    assert '<p class="ok">Targets saved.</p>' in client.get(location(r)).text
    r = post(client, f"/foods/{fed['roti']}/edit", serving_label="1 roti", kcal_per_serving="120")
    assert location(r) == SITE + "/foods?saved=unchanged"
    assert 'class="ok' not in client.get(location(r)).text


def test_the_marks_pages_have_words_for_stay_as_they_are(client, fed):
    assert location(post(client, "/foods", name="Idli", serving_label="1 idli", kcal_per_serving="40")) == \
        SITE + "/foods?saved=1"
    assert location(post(client, f"/foods/{fed['roti']}/delete")) == SITE + f"/foods?deleted={fed['roti']}"
    assert location(post(client, f"/foods/{fed['roti']}/restore")) == SITE + "/foods?saved=1"
    assert location(post(client, "/settings/claude-can", claude_can="read")) == SITE + "/settings?saved=claude_can"
    assert location(post(client, "/settings", time_zone="Asia/Kolkata")) == SITE + "/settings?saved=time_zone"


def test_the_automatic_zone_save_is_marked_time_zone_on_the_page_it_returns_to(client):
    sign_in(client)
    r = post(client, "/settings", time_zone="Asia/Kolkata", next="/day/2026-11-09?add=food#food")
    assert location(r) == SITE + "/day/2026-11-09?add=food&saved=time_zone#food"
    r = post(client, "/settings", time_zone="Europe/London", next="/progress")
    assert location(r) == SITE + "/progress?saved=time_zone"
    assert 'class="ok' not in client.get(location(r)).text
    # The same zone again changes nothing: marked so, on the page it returns to too.
    r = post(client, "/settings", time_zone="Europe/London", next="/progress")
    assert location(r) == SITE + "/progress?saved=unchanged"


@pytest.mark.parametrize("query, passed", [
    ("saved=sample", "?saved=sample"),
    ("deleted=sample", "?deleted=sample"),
    ("restored=bodyweight", "?restored=bodyweight"),
    ("deleted=bodyweight:5", "?deleted=bodyweight:5"),
    ("restored=x&deleted=sample&saved=sample", "?saved=sample&deleted=sample&restored=x"),
    # A value is passed on encoded: it can never become a second mark or another address.
    ("saved=x%26deleted%3Dbodyweight:5", "?saved=x%26deleted%3Dbodyweight:5"),
    ("saved=a%23b%0D%0Ac", "?saved=a%23b%0D%0Ac"),
    ("add=food&kind=lift&notes=x&q=1", ""),  # only the three marks of a write go on
    ("", ""),  # a plain visit to Today stays plain, so it is not calm
])
def test_today_passes_the_marks_on_to_the_day(client, query, passed):
    sign_in(client)
    r = client.get("/today" + ("?" + query if query else ""))
    assert location(r) == SITE + f"/day/{today_of(only_user()).isoformat()}" + passed


def test_sample_data_added_and_removed_land_on_the_marked_day(client):
    sign_in(client)
    day = SITE + f"/day/{today_of(only_user()).isoformat()}"
    added = client.get(location(post(client, "/settings/sample")))
    assert location(added) == day + "?saved=sample"
    removed = client.get(location(post(client, "/settings/sample/remove")))
    assert location(removed) == day + "?deleted=sample"
    page = client.get(location(removed)).text
    assert '<div class="ok undo">' not in page and "/restore" not in page  # no Undo for sample data (D15)


# ---------------------------------------------------------------- the fix round: the 500 wrapper's edges

def test_the_same_switch_again_is_marked_unchanged_with_no_strip(client):
    sign_in(client)
    assert only_user().claude_can == "log"
    r = post(client, "/settings/claude-can", claude_can="log")
    assert location(r) == SITE + "/settings?saved=unchanged"
    assert 'class="ok' not in client.get(location(r)).text


def full_scope(path: str = "/progress") -> dict:
    return {"type": "http", "method": "GET", "path": path, "root_path": "", "scheme": "https", "query_string": b"",
            "headers": [(b"host", b"spotter.example.app")], "server": ("spotter.example.app", 443)}


def test_a_send_that_fails_while_the_500_page_goes_out_leaves_the_crash_raised(client, logs):
    """The original crash is what leaves PageErrors, never the error of the failed send."""
    sent = []

    async def crashing(scope, receive, send):
        raise RuntimeError("zqwy original")

    async def send(message):
        sent.append(message)
        raise OSError("zqwy client gone")

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(RuntimeError, match="zqwy original"):
        asyncio.run(web.PageErrors(crashing)(full_scope(), receive, send))
    assert [m["type"] for m in sent] == ["http.response.start"]
    assert "The 500 page could not be sent: OSError" in [r.getMessage() for r in logs.records]


def test_a_cancel_while_the_500_page_goes_out_passes_through(client, logs):
    """A cancel is not a failed send: it leaves PageErrors as it came, never swallowed in place of the crash."""
    sent = []

    async def crashing(scope, receive, send):
        raise RuntimeError("zqwy original")

    async def send(message):
        sent.append(message)
        raise asyncio.CancelledError()

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(web.PageErrors(crashing)(full_scope(), receive, send))
    assert [m["type"] for m in sent] == ["http.response.start"]
    assert not [r for r in logs.records if "could not be sent" in r.getMessage()]


def test_a_cancelled_request_passes_through_with_nothing_sent(client):
    """A cancelled request is not a crash: nothing is sent. The same harness does send the page on a crash."""
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    async def cancelled(scope, receive, send):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(web.PageErrors(cancelled)(full_scope(), receive, send))
    assert sent == []

    async def crashing(scope, receive, send):
        raise ValueError("zqwy")

    with pytest.raises(ValueError):
        asyncio.run(web.PageErrors(crashing)(full_scope(), receive, send))
    assert [m["type"] for m in sent][:1] == ["http.response.start"] and sent[0]["status"] == 500


def test_a_crash_while_drawing_the_unrouted_404_is_the_500_page(web_client, monkeypatch):
    client = web_client(raise_server_exceptions=False)
    monkeypatch.setattr(web, "not_found_page", boom)
    assert_designed(client.get(UNROUTED), 500, PROBLEM, [("/", "Home")], signed_in=False)


# ---------------------------------------------------------------- stage 6, step 4: the top of Today

def hero_of(page: str) -> str:
    """The Today card's markup."""
    return re.search(r'<section class="card hero".*?</section>', page, re.S).group(0)


def hero_lines(page: str) -> list[str]:
    """The Today card's text as a person reads it, one line per block, the rolling digits left out."""
    return [p.text for p in tr.html_text(hero_of(page)) if p.kind != "attr"]


def chips_of(page: str) -> list[tuple[str, str]]:
    """Each chip on the Today card: its classes after "chip", and its words."""
    return [(cls, tr.squash(re.sub(r"<[^>]+>", "", inner)))
            for cls, inner in re.findall(r'<li class="chip ([^"]+)">(.*?)</li>', hero_of(page), re.S)]


def kg(value) -> str:
    return formatting.show(value, "weigh_in", page=True)


def strips_of(page: str) -> list[str]:
    """The words of each strip after a write (class "ok"), up to its first tag."""
    return re.findall(r'<(?:p|div) class="ok[^"]*">\s*([^<]*?)\s*<', page)


def test_a_new_accounts_today_card_offers_sample_data(client):
    sign_in(client)
    page = day_of(client, today_of(only_user()).isoformat())
    hero = hero_of(page)
    assert hero.startswith('<section class="card hero" aria-labelledby="today-card-title">')
    assert '<h2 class="hero-title" id="today-card-title">Nothing logged yet</h2>' in hero
    assert hero_lines(page)[:2] == ["Today", "Nothing logged yet"]
    assert hero_lines(page)[2] == ("To see Spotter full before you log anything, add sample data: four weeks of "
                                   "lifts, weigh-ins, meals, cardio, measurements and three goals. Every sample row "
                                   "is marked, and one button on Settings removes it all.")
    assert chips_of(page) == [("chip-food is-missing", "No food yet"), ("chip-weight is-missing", "No weigh-in yet"),
                              ("chip-lifts is-missing", "No lifts yet")]
    form = re.search(r'<form method="post" action="/settings/sample">(.*?)</form>', hero, re.S).group(1)
    assert '<button class="primary wide">Add sample data</button>' in form and 'name="csrf_token"' in form
    assert "Add food" not in hero and 'class="big"' not in hero and "Eaten" not in hero


def test_a_new_account_on_another_day_shows_the_food_state_without_yet(client):
    sign_in(client)
    yesterday = (today_of(only_user()) - timedelta(days=1)).isoformat()
    page = day_of(client, yesterday)
    assert hero_of(page).startswith('<section class="card hero" aria-labelledby="today-card-label">')
    assert hero_lines(page) == ["Eaten", "No food", "No calorie target set", "No weigh-in", "No lifts",
                                "No cardio", "Add food"]
    assert [cls for cls, _ in chips_of(page)] == ["chip-weight is-missing", "chip-lifts is-missing",
                                                  "chip-cardio is-missing"]
    assert f'<a class="button primary wide" href="/day/{yesterday}?add=food#add-food">Add food</a>' in page
    assert 'action="/settings/sample"' not in page and 'aria-label="Logged on this day"' in hero_of(page)


def test_a_day_with_food_shows_the_days_kcal_big_with_its_unit(client, fed):
    page = day_of(client, fed["today"])
    assert hero_lines(page) == ["Eaten today", "460 kcal", "No calorie target set", f"Weigh-in {kg(80)}",
                                "No lifts yet", "Treadmill Run " + formatting.show(30, "session_time"), "Add food"]
    assert re.search(r'<p class="big">.*?460.*?&nbsp;<span class="unit">kcal</span></p>', hero_of(page), re.S)
    assert f'href="/day/{fed["today"]}?add=food#add-food"' in page and 'id="add-food"' in page
    assert 'aria-label="Logged today"' in hero_of(page) and "tag-sample" not in hero_of(page)


def test_food_that_totals_zero_is_a_day_with_food(client):
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.log_food(uid, {"entries": [{"description": "Black coffee", "kcal": 0}]}, "claude")
    lines = hero_lines(day_of(client, today_of(only_user()).isoformat()))
    assert lines[:2] == ["Eaten today", "0 kcal"] and "No food yet" not in lines


@pytest.mark.parametrize("target, eaten, line", [
    (None, 460, "No calorie target set"),
    (2000, 460, "2,000 kcal target, 1,540 kcal left"),
    (1000, 1160, "1,000 kcal target, 160 kcal over"),
    (1200, 1200, "1,200 kcal target, 0 kcal left"),
])
def test_the_line_under_the_number_is_the_target_and_what_is_left(client, target, eaten, line):
    sign_in(client)
    uid = only_user().id
    if target is not None:
        assert "error" not in services.update_settings(uid, {"calorie_target_kcal": target}, "web")
    assert "error" not in services.log_food(uid, {"entries": [{"description": "Thali", "kcal": eaten}]}, "claude")
    page = day_of(client, today_of(only_user()).isoformat())
    assert hero_lines(page)[2] == line
    assert f'<p class="figure-label">{line}</p>' in hero_of(page)


def test_the_target_line_shows_on_a_day_with_no_food_too(client):
    sign_in(client)
    services.update_settings(only_user().id, {"calorie_target_kcal": 2000}, "web")
    yesterday = (today_of(only_user()) - timedelta(days=1)).isoformat()
    assert hero_lines(day_of(client, yesterday))[1:3] == ["No food", "2,000 kcal target, 2,000 kcal left"]


def test_each_chip_present_on_another_day_and_missing_today_with_yet(client):
    sign_in(client)
    uid = only_user().id
    today = today_of(only_user())
    yesterday = (today - timedelta(days=1)).isoformat()
    squat = [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]
    for result in (services.log_bodyweight(uid, {"kg": "79", "date": yesterday}, "claude"),
                   services.log_lifts(uid, {"date": yesterday, "sets": squat}, "claude"),
                   services.log_cardio(uid, {"exercise": "Outdoor Walk", "minutes": 45, "date": yesterday}, "claude"),
                   services.log_food(uid, {"entries": [{"description": "Poha", "kcal": 250}]}, "claude")):
        assert "error" not in result, result
    on_today = day_of(client, today.isoformat())
    assert hero_lines(on_today)[3:6] == ["No weigh-in yet", "No lifts yet", "No cardio yet"]
    assert [c for c, _ in chips_of(on_today)] == ["chip-weight is-missing", "chip-lifts is-missing",
                                                  "chip-cardio is-missing"]
    before = day_of(client, yesterday)
    assert hero_lines(before)[:2] == ["Eaten", "No food"]
    assert '<p class="big words">No food</p>' in hero_of(before)
    assert chips_of(before) == [("chip-weight", f"Weigh-in {kg(79)}"), ("chip-lifts", "Lifts logged"),
                                ("chip-cardio", "Outdoor Walk " + formatting.show(45, "session_time"))]
    assert f"<b>{kg(79)}</b>" in hero_of(before)


def test_each_chip_present_today_and_missing_on_another_day_without_yet(client):
    sign_in(client)
    uid = only_user().id
    today = today_of(only_user())
    bench = [{"exercise": "Bench Press", "weight_kg": 40, "reps": 8}]
    for result in (services.log_bodyweight(uid, {"kg": "80.25"}, "claude"),
                   services.log_lifts(uid, {"sets": bench}, "claude"),
                   services.log_cardio(uid, {"exercise": "Treadmill Run", "minutes": 75, "distance_km": 12}, "claude"),
                   services.log_cardio(uid, {"exercise": "Outdoor Walk", "minutes": 20}, "claude")):
        assert "error" not in result, result
    page = day_of(client, today.isoformat())
    # The day's first cardio entry. The chips add nothing up: the weigh-in as logged, one line for lifts.
    assert chips_of(page) == [("chip-weight", f"Weigh-in {kg(80.25)}"), ("chip-lifts", "Lifts logged"),
                              ("chip-cardio", "Treadmill Run " + formatting.show(75, "session_time"))]
    assert hero_lines(page)[:2] == ["Eaten today", "No food yet"]
    assert '<p class="big words">No food yet</p>' in hero_of(page)
    two_ago = (today - timedelta(days=2)).isoformat()
    assert hero_lines(day_of(client, two_ago))[3:6] == ["No weigh-in", "No lifts", "No cardio"]


DAY_STRIP_CASES = [
    ("saved=bodyweight", "Weigh-in saved."),
    ("saved=measurement", "Measurement saved."),
    ("saved=lift_session", "Lift session saved."),
    ("saved=food_entry", "Food entry saved."),
    ("saved=cardio_entry", "Cardio session saved."),
    ("saved=confirmed", "Estimate confirmed."),
    ("restored=bodyweight", "Weigh-in restored."),
    ("restored=lift_session", "Lift session restored."),
    ("saved=sample", "Sample data added."),
    ("deleted=sample", "Sample data removed."),
    # One strip only: the Undo strip first, then deleted, saved and restored.
    ("saved=bodyweight&restored=measurement", "Weigh-in saved."),
    ("restored=measurement&deleted=sample", "Sample data removed."),
    ("saved=bodyweight&deleted=sample", "Sample data removed."),
    ("saved=unchanged&restored=cardio_entry", "Cardio session restored."),
]


@pytest.mark.parametrize("query, words", DAY_STRIP_CASES)
def test_each_strip_says_its_words_by_its_address(client, query, words):
    sign_in(client)
    page = day_of(client, f"{today_of(only_user()).isoformat()}?{query}")
    assert strips_of(page) == [words]
    assert f'<p class="ok">{words}</p>' in page
    assert '<div class="ok undo">' not in page and "/restore" not in page  # no Undo, deleted=sample included


def test_the_undo_strip_names_the_kind_and_carries_its_form(client, stocked):
    on = stocked["today"].isoformat()
    page = day_of(client, f"{on}?deleted=bodyweight:{stocked['weigh_in']}&saved=measurement")
    assert strips_of(page) == ["Weigh-in deleted."]
    strip = re.search(r'<div class="ok undo">(.*?)</div>', page, re.S).group(1)
    assert f'<form method="post" action="/entries/bodyweight/{stocked["weigh_in"]}/restore" class="inline">' in strip
    assert f'value="{session_of(client)["csrf"]}"' in strip and '<button class="link">Undo</button>' in strip


@pytest.mark.parametrize("query", ["saved=unchanged", "saved=time_zone", "saved=x", "saved=goal", "saved=1",
                                   "restored=goal", "restored=sample", "restored=confirmed", "deleted=bodyweight",
                                   "saved=Bodyweight", "saved=%3Cb%3Eweigh%3C%2Fb%3E", "add=food", "saved=",
                                   "saved=unchanged&add=food", "saved=time_zone&add=food"])
def test_no_strip_for_unchanged_the_zone_or_a_value_the_table_does_not_name(client, query):
    sign_in(client)
    page = day_of(client, f"{today_of(only_user()).isoformat()}?{query}")
    assert 'class="ok' not in page and "/restore" not in page
    assert "<b>weigh" not in page and "&lt;b&gt;" not in page  # the address is never echoed


def test_day_strip_reads_only_the_tables():
    assert web.day_strip({}) is None
    assert web.day_strip({"saved": "unchanged"}) is None and web.day_strip({"saved": "time_zone"}) is None
    assert web.day_strip({"deleted": "food_entry:7"}) == {"words": "Food entry deleted.", "undo": ("food_entry", "7")}
    assert web.day_strip({"deleted": "sample"}) == {"words": "Sample data removed.", "undo": None}
    for kind, words in web.ENTRY_WORDS.items():
        assert web.day_strip({"saved": kind})["words"] == f"{words} saved."
        assert web.day_strip({"restored": kind})["words"] == f"{words} restored."


def test_the_top_of_today_runs_head_banner_strip_card_then_the_columns(client, monkeypatch):
    sign_in(client)
    monkeypatch.setattr(services, "sample_state", lambda *a: {"empty": False, "sample_rows": 3}, raising=False)
    page = day_of(client, f"{today_of(only_user()).isoformat()}?saved=sample")
    order = ['<div class="page-head">', '<p class="notice sample-banner">', '<p class="ok">Sample data added.</p>',
             '<section class="card hero"', '<div class="col-main">', "<h2>Food</h2>", '<div class="col-side">',
             "<h2>Lifts</h2>", "<h2>Cardio</h2>", "<h2>Weigh-in</h2>", "<h2>Measurements</h2>"]
    at = [page.index(mark) for mark in order]
    assert at == sorted(at) and all(page.count(mark) == 1 for mark in order)
    head = re.search(r'<div class="page-head">.*?</div>', page, re.S).group(0)
    assert '<nav class="day-nav" aria-label="Days">' in head and "Next day" not in head
    assert re.search(r'<a class="back" href="/day/[\d-]+" rel="prev">Previous day</a>', head)


def shape(node) -> tuple[str, ...]:
    """An element as its tag and its classes, for comparing a page's structure."""
    return (node.tag, *node.classes)


def card_titles(wrapper) -> list[str]:
    """The cards directly inside a wrapper, each by its first h2."""
    return [next(iter(card.find_all("h2"))).text() for card in wrapper.elements()
            if card.is_(cls="card")]


@pytest.mark.parametrize("address", ["{today}?saved=sample", "{yesterday}?saved=sample"])
def test_each_wrapper_holds_exactly_its_cards_and_the_banner_spans_the_page(client, monkeypatch, address):
    """Read as a tree, not as text: main's own children run page head, banner, strip, Today card, col-main,
    col-side. col-main holds the Food card alone, col-side the four cards after it in order. The banner is a
    child of main, after the page head has closed, never inside it."""
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    sign_in(client)
    monkeypatch.setattr(services, "sample_state", lambda *a: {"empty": False, "sample_rows": 3}, raising=False)
    today = today_of(only_user())
    address = address.format(today=today.isoformat(), yesterday=(today - timedelta(days=1)).isoformat())
    main = next(iter(parse(day_of(client, address)).find_all("main")))
    children = main.elements()
    assert [shape(c) for c in children] == [("div", "page-head"), ("p", "notice", "sample-banner"), ("p", "ok"),
                                            ("section", "card", "hero"), ("div", "col-main"), ("div", "col-side")]
    head, banner = children[0], children[1]
    assert not head.find_all(cls="sample-banner") and banner.parent is main
    assert card_titles(children[4]) == ["Food"] and len(children[4].elements()) == 1
    assert card_titles(children[5]) == ["Lifts", "Cardio", "Weigh-in", "Measurements"]
    assert len(children[5].elements()) == 4


def test_todays_h1_is_today_with_the_day_under_it(client, monkeypatch):
    sign_in(client)
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc))
    page = day_of(client, "2026-11-10")
    assert '<h1>Today<span class="sr">,</span> <span class="h1-date">Tuesday 10 November</span></h1>' in page
    assert "<h1>Sunday 1 November</h1>" in day_of(client, "2026-11-01")


def test_a_past_day_has_both_links_the_past_class_and_eaten(client, stocked):
    page = day_of(client, stocked["yesterday"])
    assert '<main class="page today past">' in page
    head = re.search(r'<div class="page-head">.*?</div>', page, re.S).group(0)
    before = (date.fromisoformat(stocked["yesterday"]) - timedelta(days=1)).isoformat()
    assert re.findall(r'<a class="back" href="/day/([\d-]+)" rel="(prev|next)">', head) == [
        (before, "prev"), (stocked["today"].isoformat(), "next")]
    assert hero_lines(page)[0] == "Eaten" and "Eaten today" not in page
    assert '<main class="page today">' in day_of(client, stocked["today"].isoformat())


def test_a_day_after_today_is_a_past_day(client):
    sign_in(client)
    user = only_user()
    services.update_settings(user.id, {"time_zone": "Pacific/Kiritimati"}, "web")  # UTC+14
    services.log_bodyweight(user.id, {"kg": "70"}, "claude")
    later = today_of(user).isoformat()
    services.update_settings(user.id, {"time_zone": "Pacific/Pago_Pago"}, "web")  # UTC-11
    today = today_of(user).isoformat()
    page = day_of(client, later)
    assert '<main class="page today past">' in page and hero_lines(page)[0] == "Eaten"
    assert chips_of(page)[0] == ("chip-weight", f"Weigh-in {kg(70)}") and chips_of(page)[1][1] == "No lifts"
    assert 'rel="prev">Previous day</a>' in page and h1_text(page) == formatting.day_heading(later, today)
    assert "Nothing logged yet" not in page


def test_the_sample_banner_sits_above_the_today_card(client):
    sign_in(client)
    assert post(client, "/settings/sample").status_code == 303
    page = day_of(client, today_of(only_user()).isoformat())
    banner = page.index('<p class="notice sample-banner">Sample data is on this account. Remove it on '
                        '<a href="/settings">Settings</a>.</p>')
    assert page.index('<div class="page-head">') < banner < page.index('<section class="card hero"')
    assert "tag-sample" not in hero_of(page) and "Nothing logged yet" not in page


REEL = "<span class=\"strip\">" + "".join(f"<span>{n}</span>" for n in range(10)) + "</span>"


@pytest.mark.parametrize("kcal, shown", [(1234, "1,234"), (460, "460"), (0, "0"), (5000, "5,000")])
def test_the_rolling_number_sits_over_the_plain_number_hidden_from_reading(client, kcal, shown):
    sign_in(client)
    assert "error" not in services.log_food(only_user().id, {"entries": [{"description": "Thali", "kcal": kcal}]},
                                            "claude")
    page = day_of(client, today_of(only_user()).isoformat())
    assert formatting.show(kcal, "kcal", unit=False) == shown
    columns = "".join(f'<span class="reel d{c}">{REEL}</span>' if c.isdigit() else f"<span>{c}</span>" for c in shown)
    big = (f'<p class="big"><span class="rolling"><span class="rolling-text">{shown}</span>'
           f'<span class="roll" aria-hidden="true" hidden>{columns}</span></span>&nbsp;<span class="unit">kcal</span></p>')
    assert big in hero_of(page)
    assert page.count('class="roll"') == 1 and page.count('class="rolling"') == 1  # the one big number only
    assert hero_lines(page)[1] == f"{shown} kcal"  # read once, as plain text


def test_no_rolling_number_without_a_number(client):
    sign_in(client)
    assert 'class="roll' not in day_of(client, today_of(only_user()).isoformat())  # a new account
    yesterday = (today_of(only_user()) - timedelta(days=1)).isoformat()
    assert 'class="roll' not in day_of(client, yesterday)  # No food


@pytest.mark.parametrize("state", ["new", "food", "none", "past", "strip"])
def test_the_top_of_today_never_says_claude(client, state):
    sign_in(client)
    today = today_of(only_user())
    address = today.isoformat()
    if state in ("food", "strip"):
        stock_food(only_user())
    if state == "none":
        services.log_bodyweight(only_user().id, {"kg": "80"}, "claude")
    if state == "past":
        address = (today - timedelta(days=3)).isoformat()
    if state == "strip":
        address += "?saved=confirmed"
    page = day_of(client, address)
    top = page[page.index("<main"):page.index('<div class="col-main">')]
    assert "Today" in top or "Eaten" in top
    assert not re.search(r"claude", " ".join(p.text for p in tr.html_text(top)), re.I)


# ---------------------------------------------------------------- stage 6, step 4: the Food card

def food_card(page: str) -> str:
    """The Food card's markup: everything inside div.col-main."""
    return page[page.index('<div class="col-main">'):page.index('<div class="col-side">')]


def foldout_of(page: str) -> str:
    return re.search(r'<details class="add" id="add-food"[^>]*>.*?</details>', food_card(page), re.S).group(0)


def test_the_meals_run_breakfast_to_snack_and_an_empty_meal_says_nothing_logged(client):
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.log_food(uid, {"entries": [
        {"description": "Biscuits", "kcal": 90, "meal": "snack"},
        {"description": "Poha", "kcal": 250, "meal": "breakfast"}]}, "claude")
    card = food_card(day_of(client, today_of(only_user()).isoformat()))
    meals = re.findall(r'<div class="meal( empty)?">\n    <h3>(\w+)</h3>', card)
    assert meals == [("", "Breakfast"), (" empty", "Lunch"), (" empty", "Dinner"), ("", "Snack")]
    for meal in ("Lunch", "Dinner"):
        assert re.search(rf'<div class="meal empty">\s*<h3>{meal}</h3>\s*<p class="small">Nothing logged.</p>\s*</div>',
                         card)
    assert card.count("Nothing logged.") == 2 and "No food logged on this day" not in card
    assert [e["name"] for e in meal_of(card, "Breakfast")] == ["Poha"]


def test_the_food_card_head_names_the_card_and_links_to_saved_foods(client):
    sign_in(client)
    card = food_card(day_of(client, today_of(only_user()).isoformat()))
    assert re.search(r'<div class="card">\s*<div class="card-head">\s*<h2>Food</h2>\s*'
                     r'<a class="aside" href="/foods">Saved foods</a>\s*</div>', card)


def log_one(monkeypatch, kind: str) -> dict:
    """One food entry of each kind the Food card draws, logged as the assistant would."""
    uid = only_user().id
    if kind.startswith("saved"):
        roti = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
        entry = {"saved_food_id": roti["id"], "servings": 3}
        if kind == "saved, its own description":
            entry["description"] = "Three rotis with ghee"
    elif kind == "usda":
        monkeypatch.setattr(services.food.usda, "food", lambda fdc: ({
            "fdc_id": fdc, "description": "Bananas, raw", "data_type": "SR Legacy", "kcal_per_100g": 89.0}, None))
        entry = {"usda_fdc_id": 173944, "grams": 150}
    elif kind == "stated":
        entry = {"description": "Thali", "kcal": 650}
    else:
        entry = {"description": "Biryani", "kcal": 700, "estimated": True}
    result = services.log_food(uid, {"entries": [{**entry, "meal": "lunch"}]}, "claude")
    assert "error" not in result, result
    return result


@pytest.mark.parametrize("kind, name, figures, note, tag", [
    ("saved", "Roti", "360 kcal, 3 rotis", None, ("tag-saved_food", "saved food")),
    ("saved, its own description", "Roti", "360 kcal, 3 rotis", "Three rotis with ghee",
     ("tag-saved_food", "saved food")),
    ("usda", "Bananas, raw", "134 kcal, 150 g", None, ("tag-usda", "USDA")),
    ("stated", "Thali", "650 kcal", None, ("tag-you_said", "you said")),
    ("estimate", "Biryani", "700 kcal", None, ("tag-claude_estimate", "estimate")),
])
def test_each_kind_of_entry_draws_its_lines_its_tag_and_its_actions(client, monkeypatch, kind, name, figures, note,
                                                                     tag):
    sign_in(client)
    result = log_one(monkeypatch, kind)
    page = day_of(client, today_of(only_user()).isoformat())
    [entry] = meal_of(food_card(page), "Lunch")
    assert (entry["name"], entry["figures"], entry["note"], entry["tags"]) == (name, figures, note, [tag])
    # Confirm on an estimate only, Edit and Delete on every entry.
    assert entry["actions"] == (kind == "estimate", True, True)
    entry_id = result["ids"][0]
    line = re.search(r'<li class="entry-line">(.*?)</li>', food_card(page), re.S).group(1)
    assert f'<a class="action" href="/entries/food_entry/{entry_id}/edit">Edit</a>' in line
    assert f'<form method="post" action="/entries/food_entry/{entry_id}/delete" class="inline">' in line
    assert (f'<form method="post" action="/entries/food_entry/{entry_id}/confirm" class="inline">' in line) is \
        (kind == "estimate")


def test_the_tags_sit_in_their_own_slot_apart_from_every_figure(client, fed):
    card = food_card(day_of(client, fed["today"]))
    figures = re.findall(r'<span class="entry-figures">(.*?)</span>', card)
    assert len(figures) == 3 and not [f for f in figures if "<" in f]
    assert card.count('<span class="entry-tags">') == 3
    assert re.findall(r'<span class="tag (tag-[a-z_]+)">', card) == ["tag-you_said", "tag-saved_food",
                                                                   "tag-claude_estimate"]


def test_a_sample_food_named_with_sample_shows_the_sample_mark_once(client):
    """A deleted saved food of the person's holds the name, so sample data stores its own as "Roti (sample)"
    (sample._free_name). The card shows the name and the sample tag, never the word as well."""
    sign_in(client)
    uid = only_user().id
    mine = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 110}, "web")
    assert "error" not in services.delete_entry(uid, "saved_food", mine["id"])
    assert "error" not in services.add_sample_data(uid, "web")
    stored = [e.description for e in rows(FoodEntry, uid) if e.saved_food_id is not None]
    assert "Roti (sample)" in stored  # the real path names it so
    card = food_card(day_of(client, today_of(only_user()).isoformat()))
    rotis = [e for e in meal_of(card, "Lunch") if e["name"] == "Roti"]
    assert rotis and all(e["note"] is None and e["tags"] == [("tag-saved_food", "saved food"), ("tag-sample", "sample")]
                         for e in rotis)
    assert "(sample)" not in card


@pytest.mark.parametrize("entry, shown", [
    ({"saved_food_name": "Roti (sample)", "description": "Roti (sample)", "is_sample": True}, "Roti"),
    ({"saved_food_name": "Roti (Sample)", "description": "x", "is_sample": True}, "Roti"),
    ({"saved_food_name": "Roti (sample)", "description": "Roti (sample)", "is_sample": False}, "Roti (sample)"),
    ({"saved_food_name": None, "description": "Tea (sample)", "is_sample": True}, "Tea (sample)"),
    ({"saved_food_name": "", "description": "My lunch (sample)", "is_sample": True}, "My lunch (sample)"),
    ({"saved_food_name": None, "description": "Tea", "is_sample": True}, "Tea"),
    ({"saved_food_name": "Dal", "description": "Dal tadka", "is_sample": False}, "Dal"),
])
def test_the_name_on_line_one_drops_the_sample_word_only_on_a_sample_row(entry, shown):
    assert web.food_name(entry) == shown


@pytest.mark.parametrize("estimate, target, add_back, terms", [
    (False, None, False, ["Total"]),
    (True, None, False, ["Total", "From estimates", "Share from estimates"]),
    (False, 2000, False, ["Total", "Target", "Left"]),
    (False, None, True, ["Total", "Net (food minus cardio)"]),
    (True, 2000, True, ["Total", "From estimates", "Share from estimates", "Target", "Left",
                        "Net (food minus cardio)"]),
])
def test_each_totals_row_shows_by_its_condition(client, estimate, target, add_back, terms):
    sign_in(client)
    uid = only_user().id
    changes = {"add_back_exercise_kcal": add_back, **({"calorie_target_kcal": target} if target else {})}
    assert "error" not in services.update_settings(uid, changes, "web")
    entries = [{"description": "Thali", "kcal": 1500}] + ([{"description": "Lassi", "kcal": 500, "estimated": True}]
                                                          if estimate else [])
    assert "error" not in services.log_food(uid, {"entries": entries}, "claude")
    card = food_card(day_of(client, today_of(only_user()).isoformat()))
    totals = re.search(r'<dl class="facts totals">(.*?)</dl>', card, re.S).group(1)
    assert re.findall(r"<dt[^>]*>([^<]*)</dt>", totals) == terms
    numbers = services.get_log(uid, today_of(only_user()).isoformat(), 1)["days"][0]["food"]
    shown = dict(re.findall(r"<dt[^>]*>([^<]*)</dt><dd[^>]*>([^<]*)</dd>", totals))
    want = {"Total": formatting.show(numbers["total_kcal"], "kcal", page=True)}
    if estimate:
        want["From estimates"] = formatting.show(numbers["estimated_kcal"], "kcal", page=True)
        want["Share from estimates"] = formatting.show(numbers["estimated_share_pct"], "percent", page=True)
    if target:
        want["Target"] = formatting.show(target, "kcal", page=True)
        want["Left"] = formatting.show(numbers["remaining_kcal"], "kcal_left", page=True)
    if add_back:
        want["Net (food minus cardio)"] = formatting.show(numbers["net_kcal"], "net_kcal", page=True)
    assert shown == want
    assert '<dt class="total">Total</dt><dd class="total">' in totals  # bold and larger, by the stylesheet


def test_an_estimate_of_zero_kcal_still_shows_the_estimates_rows(client):
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.log_food(uid, {"entries": [{"description": "Thali", "kcal": 500},
                                                              {"description": "Pickle", "kcal": 0, "estimated": True}]},
                                            "claude")
    card = food_card(day_of(client, today_of(only_user()).isoformat()))
    assert "<dt>From estimates</dt><dd>0 kcal</dd>" in card and "<dt>Share from estimates</dt><dd>0%</dd>" in card


def test_over_target_the_left_row_says_over(client):
    sign_in(client)
    uid = only_user().id
    services.update_settings(uid, {"calorie_target_kcal": 1000}, "web")
    services.log_food(uid, {"entries": [{"description": "Thali", "kcal": 1160}]}, "claude")
    card = food_card(day_of(client, today_of(only_user()).isoformat()))
    assert "<dt>Left</dt><dd>160 kcal over</dd>" in card and "-160" not in card


def test_the_food_card_says_no_of_which_and_brackets_no_figure(client):
    sign_in(client)
    uid = only_user().id
    services.update_settings(uid, {"calorie_target_kcal": 2000, "add_back_exercise_kcal": True}, "web")
    stock_food(only_user())
    card = food_card(day_of(client, today_of(only_user()).isoformat()))
    text = " ".join(p.text for p in tr.html_text(card) if p.kind != "attr")
    assert "From estimates" in text and "Net (food minus cardio)" in text
    assert not re.search(r"\bof which\b", text, re.I)
    assert not re.search(r"\([^()]*\d[^()]*\)", text), text  # no figure inside brackets
    assert not re.search(r"\(\s*<span", card)  # no tag inside brackets


def test_the_fold_out_is_closed_by_default_and_open_by_its_mark(client, fed):
    on = fed["today"]
    closed = foldout_of(day_of(client, on))
    assert closed.startswith('<details class="add" id="add-food">\n  <summary>Add food</summary>')
    for query in ("add=food", "add=weigh-in&add=food", "add=food&add=weigh-in", "saved=food_entry&add=food"):
        assert foldout_of(day_of(client, f"{on}?{query}")).startswith('<details class="add" id="add-food" open>'), query
    for query in ("add=weigh-in", "add=", "add=Food", "saved=food_entry", "addfood=1"):
        assert foldout_of(day_of(client, f"{on}?{query}")).startswith('<details class="add" id="add-food">'), query
    page = day_of(client, f"{on}?add=food")
    assert page.count('id="add-food"') == 1 and '<form id="add-food"' not in page
    assert f'href="/day/{on}?add=food#add-food"' in hero_of(page)  # the Today card's link lands on it


def test_the_fold_out_holds_the_contracts_fields_and_button(client):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    out = foldout_of(day_of(client, on))
    assert "<summary>Add food</summary>" in out and f'<form method="post" action="/day/{on}/food">' in out
    labels = re.findall(r'<label for="([^"]+)">([^<]+)</label>', out)
    assert labels == [("food-description", "Food"), ("food-kcal", "Calories (kcal)"), ("food-meal", "Meal")]
    assert '<input id="food-kcal" name="kcal" inputmode="numeric" required autocomplete="off">' in out
    assert '<input id="food-description" name="description" required maxlength="200" autocomplete="off">' in out
    choices = re.findall(r'<option value="([^"]*)">([^<]+)</option>', out)
    assert choices == [("", "By the clock"), ("breakfast", "Breakfast"), ("lunch", "Lunch"), ("dinner", "Dinner"),
                       ("snack", "Snack")]
    assert '<div class="form-actions"><button class="primary">Save food</button></div>' in out
    assert re.search(r'<div class="field-row">\s*<div class="field">\s*<label for="food-kcal">', out)


@pytest.mark.parametrize("data, field, says", [
    ({"description": "", "kcal": "100", "meal": ""}, "description", "Give a description, up to 200 characters."),
    ({"description": "Rice", "kcal": "abc", "meal": "dinner"}, "kcal",
     "That kcal was not saved: give a whole number from 0 to 5,000."),
    ({"description": "Rice", "kcal": "6000", "meal": ""}, "kcal",
     "That kcal was not saved: give a whole number from 0 to 5,000."),
    ({"description": "Rice", "kcal": "100", "meal": "brunch"}, "meal", "Choose breakfast, lunch, dinner or snack."),
])
def test_a_food_error_opens_the_fold_out_under_its_field_tied_to_its_input(client, data, field, says):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    r = post(client, f"/day/{on}/food", **data)
    assert r.status_code == 400
    out = foldout_of(r.text)
    assert out.startswith('<details class="add" id="add-food" open>')
    control = "select" if field == "meal" else "input"
    box = re.search(rf'<div class="field has-error">\s*<label for="food-{field}">[^<]*</label>\s*<{control} [^>]*>.*?'
                    rf'<p class="error" id="food-{field}-error">([^<]*)</p>\s*</div>', out, re.S)
    assert box and box.group(1) == says
    tag = re.search(rf'<{control} id="food-{field}"[^>]*>', out).group(0)
    assert 'aria-invalid="true"' in tag and f'aria-describedby="food-{field}-error"' in tag
    assert out.count('aria-invalid="true"') == 1 and out.count('class="error"') == 1 and out.count("has-error") == 1
    # What was typed stays.
    if data["description"]:
        assert f'name="description" value="{data["description"]}"' in out
    if data["kcal"]:
        assert f'name="kcal" value="{data["kcal"]}"' in out
    if data["meal"] in web.MEALS:
        assert f'<option value="{data["meal"]}" selected>' in out
    else:
        assert "selected" not in out


def test_a_refusal_the_form_has_no_field_for_sits_in_the_fold_out_whole(client, monkeypatch):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    monkeypatch.setattr(services, "log_food", lambda *a: {"error": "date_too_old", "message": "x", "fix": "y"})
    r = post(client, f"/day/{on}/food", description="Rice", kcal="100")
    assert r.status_code == 400
    out = foldout_of(r.text)
    assert out.startswith('<details class="add" id="add-food" open>')
    assert re.search(r'<form method="post" action="[^"]+">\s*<input type="hidden" name="csrf_token" value="[^"]+">\s*'
                     r'<div class="error"><p>That date cannot be used: [^<]+</p></div>', out)
    assert "aria-invalid" not in out and '<p class="error"' not in out


def test_another_cards_error_leaves_the_food_fold_out_closed(client):
    sign_in(client)
    r = post(client, f"/day/{today_of(only_user()).isoformat()}/weigh-in", kg="abc")
    assert r.status_code == 400 and foldout_of(r.text).startswith('<details class="add" id="add-food">')


@pytest.mark.parametrize("servings, label, says", [
    (2, "1 roti", "2 rotis"), (2, "1 medium apple", "2 medium apples"), (1, "1 roti", "1 roti"),
    (2, "100 g", "2 × 100 g"), (2, None, "2"),
])
def test_on_the_page_a_no_break_space_goes_before_the_serving_words(servings, label, says):
    assert web.serving_text(servings, label, page=True) == says
    assert web.serving_text(servings, label) == says.replace(" ", " ")


@pytest.mark.parametrize("state", ["empty", "fed", "add", "error", "sample", "past"])
def test_the_food_card_never_says_claude(client, state):
    sign_in(client)
    uid = only_user().id
    on = today_of(only_user())
    if state in ("fed", "add", "past"):
        stock_food(only_user())
    if state == "sample":
        services.add_sample_data(uid, "web")
    address = on.isoformat() + ("?add=food" if state == "add" else "")
    if state == "past":
        address = (on - timedelta(days=1)).isoformat()
    if state == "error":
        page = post(client, f"/day/{on.isoformat()}/food", description="Rice", kcal="abc").text
    else:
        page = day_of(client, address)
    card = food_card(page)
    assert "<h2>Food</h2>" in card
    assert not re.search(r"claude", " ".join(p.text for p in tr.html_text(card)), re.I)


def test_food_that_totals_zero_still_draws_the_card_with_its_totals(client):
    """A logged 0 kcal entry is food: the card shows it, Total 0 kcal and, with add-back on, Net 0 kcal."""
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.update_settings(uid, {"add_back_exercise_kcal": True}, "web")
    assert "error" not in services.log_food(uid, {"entries": [{"description": "Black coffee", "kcal": 0}]}, "claude")
    card = food_card(day_of(client, today_of(only_user()).isoformat()))
    assert [(e["name"], e["figures"]) for e in food_entries(card)] == [("Black coffee", "0\u00a0kcal")]
    assert '<dd class="total">0\u00a0kcal</dd>' in card and "<dt>Net (food minus cardio)</dt><dd>0\u00a0kcal</dd>" in card
    assert "No food logged on this day" not in card


def test_what_was_typed_comes_back_escaped(client):
    sign_in(client)
    r = post(client, f"/day/{today_of(only_user()).isoformat()}/food", description='<b>"Rice"</b>', kcal="abc")
    out = foldout_of(r.text)
    assert 'value="&lt;b&gt;&#34;Rice&#34;&lt;/b&gt;"' in out and "<b>" not in out


# ---------------------------------------------------------------- stage 6, step 4: the side cards

SIDE_EMPTY = {
    "Lifts": "No lifts logged on this day. Tell your assistant what you lifted and it shows here.",
    "Cardio": "No cardio logged on this day. Tell your assistant what you did, or add it below.",
    "Weigh-in": "No weigh-in on this day.",
    "Measurements": "No measurements on this day.",
}
NO_ESTIMATE_NOTE = ("No calorie estimate on a row that says No estimate: there was no weigh-in when it was logged. "
                    "Log a weigh-in and new cardio gets one.")
# The three side fold-outs: (id, the mark that opens it, its card).
SIDE_FOLDOUTS = (("add-cardio", "cardio", "Cardio"), ("add-weigh-in", "weigh-in", "Weigh-in"),
                 ("add-measurement", "measurement", "Measurements"))


def sessions_of(card) -> list:
    return card.find_all("div", "session")


def table_of(node) -> tuple[list[str], list[list[str]]]:
    """A table's headers and its body rows' cell texts."""
    [table] = node.find_all("table")
    headers = [th.text() for th in table.find_all("th")]
    rows_ = [[td.text() for td in tr_.find_all("td")] for tr_ in table.find_all("tr") if tr_.find_all("td")]
    return headers, rows_


def foldouts_of(page: str) -> dict:
    """Every details.add on the page by its id."""
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    return {d.attrs["id"]: d for d in parse(page).find_all("details", "add")}


def test_a_lift_session_shows_its_line_its_sets_its_bests_and_its_notes(client, stocked):
    card = side_card(day_of(client, stocked["today"].isoformat()), "Lifts")
    [session] = sessions_of(card)
    [line] = entries_of(session)
    assert (line["name"], line["figures"], line["tags"]) == ("Lift session", None, [])
    assert line["edit"] == [f"/entries/lift_session/{stocked['session']}/edit"]
    assert line["delete"] == [f"/entries/lift_session/{stocked['session']}/delete"]
    # The sets in a table, in its scrolling wrap, the unit in the header and none in a cell.
    [table] = session.find_all("table")
    assert table.parent.is_("div", "table-wrap")
    headers, cells = table_of(session)
    assert headers == ["Exercise", "Set", "Weight (kg)", "Reps"]
    assert cells == [["Back Squat", "1", "65", "5"], ["Back Squat", "2", "65", "5"], ["Back Squat", "3", "65", "5"]]
    # Each new best on its own line, in the service's words.
    bests = [li.text() for ul in session.find_all("ul", "bests") for li in ul.find_all("li")]
    texts = [b["text"] for b in services.get_log(only_user().id, stocked["today"].isoformat(), 1)["days"][0]
             ["sessions"][0]["new_bests"]]
    assert texts and bests == [f"New best: {t}" for t in texts]
    # The notes on a line of their own, labelled Notes, after the sets and the bests.
    [notes] = session.find_all("p", "entry-note")
    assert notes.text() == "Notes felt strong" and [b.text() for b in notes.find_all("b")] == ["Notes"]
    assert session.elements()[-1] is notes and notes.parent is session
    # Under the sessions, the small line. No empty line.
    assert [p.text() for p in card.find_all("p", "small")] == ["Lifts are logged through your assistant."]
    assert card.elements()[-1].is_("p", "small") and not card.find_all("p", "empty-line")


def test_a_session_with_no_notes_has_no_notes_line(client, stocked):
    card = side_card(day_of(client, stocked["yesterday"]), "Lifts")
    [session] = sessions_of(card)
    assert not session.find_all("p", "entry-note") and not session.find_all("ul", "bests")
    assert table_of(session)[1][0] == ["Back Squat", "1", "60", "5"]


def test_each_side_card_with_nothing_logged_says_its_empty_line(client):
    sign_in(client)
    page = day_of(client, today_of(only_user()).isoformat())
    for title, words in SIDE_EMPTY.items():
        card = side_card(page, title)
        assert [p.text() for p in card.find_all("p", "empty-line")] == [words], title
        assert not card.find_all(cls="entry-line") and not card.find_all("table"), title
    assert not side_card(page, "Lifts").find_all("p", "small")  # the small line goes with sessions only
    assert not side_card(page, "Cardio").find_all("p", "small")  # nor the No estimate footnote


def test_a_set_at_0_kg_leaves_its_weight_blank_and_a_session_with_none_weighted_has_no_weight_column(client):
    sign_in(client)
    uid = only_user().id
    pull = [{"exercise": "Pull-Up", "weight_kg": 0, "reps": 6}] * 2
    mixed = [{"exercise": "Deadlift", "weight_kg": 100, "reps": 5}, {"exercise": "Pull-Up", "weight_kg": 0, "reps": 8}]
    for sets in (pull, mixed):
        assert "error" not in services.log_lifts(uid, {"sets": sets}, "claude")
    first, second = sessions_of(side_card(day_of(client, today_of(only_user()).isoformat()), "Lifts"))
    assert table_of(first) == (["Exercise", "Set", "Reps"], [["Pull-Up", "1", "6"], ["Pull-Up", "2", "6"]])
    assert table_of(second) == (["Exercise", "Set", "Weight (kg)", "Reps"],
                                [["Deadlift", "1", "100", "5"], ["Pull-Up", "1", "", "8"]])


def test_the_lifts_card_has_no_fold_out(client, stocked):
    card = side_card(day_of(client, f"{stocked['today'].isoformat()}?add=lifts"), "Lifts")
    forms = card.find_all("form")
    assert not card.find_all("details") and not card.find_all("select")
    assert [f.attrs["action"] for f in forms] == [f"/entries/lift_session/{stocked['session']}/delete"]


@pytest.mark.parametrize("data, labelled", [
    ({"exercise": "Treadmill Run", "minutes": 30, "distance_km": 5, "machine_kcal": 300},
     [("Time (m:ss)", "30:00"), ("Distance (km)", "5"), ("Pace (per km)", "6:00"), ("Calories (kcal)", "{kcal}"),
      ("Machine (kcal)", "300")]),
    ({"exercise": "Outdoor Walk", "minutes": 45}, [("Time (m:ss)", "45:00"), ("Calories (kcal)", "{kcal}")]),
    ({"exercise": "Treadmill Run", "minutes": 31.5, "machine_kcal": 280},
     [("Time (m:ss)", "31:30"), ("Calories (kcal)", "{kcal}"), ("Machine (kcal)", "280")]),
    ({"exercise": "Outdoor Run", "minutes": 75, "distance_km": 12.5},
     [("Time (h:mm:ss)", "1:15:00"), ("Distance (km)", "12.5"), ("Pace (per km)", "6:00"),
      ("Calories (kcal)", "{kcal}")]),
])
def test_each_cardio_figure_shows_when_the_entry_has_it_and_is_left_out_when_not(client, data, labelled):
    sign_in(client)
    assert "error" not in services.log_bodyweight(only_user().id, {"kg": "80"}, "claude")
    logged = services.log_cardio(only_user().id, data, "claude")
    assert "error" not in logged
    [entry] = entries_of(side_card(day_of(client, today_of(only_user()).isoformat()), "Cardio"))
    kcal = formatting.show(logged["estimate"]["kcal"], "kcal", unit=False)
    assert entry["name"] == data["exercise"]
    assert entry["labelled"] == [(label, figure.format(kcal=kcal)) for label, figure in labelled]
    # The estimate tag in its own slot beside the sample slot, never inside a figure.
    assert entry["tags"] == [("tag-estimate", "estimate")] and entry["tag_slots"] == [("entry-tags",)]
    assert entry["edit"] == [f"/entries/cardio_entry/{logged['id']}/edit"]
    assert entry["delete"] == [f"/entries/cardio_entry/{logged['id']}/delete"]


def test_cardio_with_no_estimate_says_so_in_the_calories_figure_and_keeps_the_footnote(client):
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.log_cardio(uid, {"exercise": "Outdoor Walk", "minutes": 20}, "claude")
    card = side_card(day_of(client, today_of(only_user()).isoformat()), "Cardio")
    [entry] = entries_of(card)
    assert entry["labelled"] == [("Time (m:ss)", "20:00"), ("Calories (kcal)", "No estimate")]
    assert entry["tags"] == [] and not card.find_all(cls="tag-estimate")
    assert [p.text() for p in card.find_all("p", "small")] == [NO_ESTIMATE_NOTE]
    # With an estimate on every row, no footnote.
    assert "error" not in services.log_bodyweight(uid, {"kg": "80"}, "claude")
    assert "error" not in services.delete_entry(uid, "cardio_entry", rows(CardioEntry, uid)[0].id)
    assert "error" not in services.log_cardio(uid, {"exercise": "Outdoor Walk", "minutes": 20}, "claude")
    card = side_card(day_of(client, today_of(only_user()).isoformat()), "Cardio")
    assert not card.find_all("p", "small") and entries_of(card)[0]["tags"] == [("tag-estimate", "estimate")]


def test_the_footnote_shows_when_any_entry_says_no_estimate_not_only_the_first(client):
    sign_in(client)
    uid = only_user().id
    weigh_in = services.log_bodyweight(uid, {"kg": "80"}, "claude")
    assert "error" not in services.log_cardio(uid, {"exercise": "Outdoor Walk", "minutes": 20}, "claude")
    assert "error" not in services.delete_entry(uid, "bodyweight", weigh_in["id"])
    assert "error" not in services.log_cardio(uid, {"exercise": "Outdoor Walk", "minutes": 25}, "claude")
    card = side_card(day_of(client, today_of(only_user()).isoformat()), "Cardio")
    first, second = entries_of(card)
    assert first["tags"] == [("tag-estimate", "estimate")]
    assert ("Calories (kcal)", "No estimate") in second["labelled"] and second["tags"] == []
    assert [p.text() for p in card.find_all("p", "small")] == [NO_ESTIMATE_NOTE]


def test_the_cardio_card_has_no_minutes_header_and_no_per_km_in_a_figure(client, fed):
    card = side_card(day_of(client, fed["today"]), "Cardio")
    text = card.text()
    assert not card.find_all("table") and not card.find_all("th")
    assert "Minutes" not in text and "/km" not in text and " /km" not in text
    [pace] = [dd for dd in card.find_all("dd") if dd.parent.find_all("dt")[0].text() == "Pace (per km)"]
    assert pace.text() == "6:00"


def test_a_cardio_note_sits_on_its_own_line_labelled_notes(client):
    sign_in(client)
    assert "error" not in services.log_cardio(only_user().id, {"exercise": "Outdoor Walk", "minutes": 20,
                                                               "notes": "easy pace"}, "claude")
    card = side_card(day_of(client, today_of(only_user()).isoformat()), "Cardio")
    [entry] = entries_of(card)
    assert entry["notes"] == ["Notes easy pace"]
    [note] = card.find_all("p", "entry-note")
    assert note.parent.is_("div", "entry-main") and not note.inside("dl")


def test_the_weigh_in_shows_large_with_its_actions_and_the_fold_out_replaces_it(client, stocked):
    page = day_of(client, stocked["today"].isoformat())
    card = side_card(page, "Weigh-in")
    [entry] = entries_of(card)
    assert entry["big"] == "78.4 kg" and entry["tags"] == []
    [big] = card.find_all(cls="entry-big")
    assert [u.text() for u in big.find_all("span", "unit")] == ["kg"]
    assert '<span class="entry-big">78.4&nbsp;<span class="unit">kg</span></span>' in page
    assert entry["edit"] == [f"/entries/bodyweight/{stocked['weigh_in']}/edit"]
    assert entry["delete"] == [f"/entries/bodyweight/{stocked['weigh_in']}/delete"]
    fold = foldouts_of(page)["add-weigh-in"]
    assert fold.find_all("summary")[0].text() == "Replace weigh-in"
    assert [b.text() for b in fold.find_all("button")] == ["Replace weigh-in"]


def test_with_no_weigh_in_the_fold_out_adds_one(client):
    sign_in(client)
    fold = foldouts_of(day_of(client, today_of(only_user()).isoformat()))["add-weigh-in"]
    assert fold.find_all("summary")[0].text() == "Add weigh-in"
    assert [b.text() for b in fold.find_all("button")] == ["Add weigh-in"]


def test_each_measurement_kind_is_named_by_kind_word(client):
    from app.services import measurements  # noqa: PLC0415

    sign_in(client)
    uid = only_user().id
    values = {"waist": "84.5", "hips": "98", "chest": "101.2", "arm": "35", "thigh": "58", "steps": "9500",
              "sleep_hours": "7.25", "resting_hr": "58", "water_ml": "2501"}
    assert set(values) == set(web.MEASUREMENT_KINDS)
    ids = []
    for kind, value in values.items():
        logged = services.log_measurement(uid, {"kind": kind, "value": value}, "claude")
        assert "error" not in logged
        ids.append(logged["id"])
    entries = entries_of(side_card(day_of(client, today_of(only_user()).isoformat()), "Measurements"))
    assert [(e["name"], e["figures"]) for e in entries] == [
        (measurements.kind_word(k), formatting.show(float(values[k]), web.measurement_quantity(k), page=True))
        for k in web.MEASUREMENT_KINDS]
    assert [e["name"] for e in entries][-2:] == ["Resting heart rate", "Water"]
    assert [e["edit"] for e in entries] == [[f"/entries/measurement/{i}/edit"] for i in ids]
    assert [e["delete"] for e in entries] == [[f"/entries/measurement/{i}/delete"] for i in ids]
    assert all(e["tags"] == [] for e in entries)


def test_the_measurement_fold_out_names_each_kind_by_kind_word_with_its_unit(client):
    from app.services import measurements  # noqa: PLC0415

    sign_in(client)
    fold = foldouts_of(day_of(client, today_of(only_user()).isoformat()))["add-measurement"]
    choices = [(o.attrs["value"], o.text()) for o in fold.find_all("option")]
    assert choices == [(k, web.kind_with_unit(k)) for k in web.MEASUREMENT_KINDS]
    assert [k for k, _ in choices] == list(web.MEASUREMENT_KINDS)
    assert all(words.startswith(measurements.kind_word(k)) for k, words in choices)
    assert ("resting_hr", "Resting heart rate (bpm)") in choices and ("water_ml", "Water (ml)") in choices
    assert ("steps", "Steps") in choices


@pytest.mark.parametrize("kind, words", [
    ("waist", "Waist (cm)"), ("hips", "Hips (cm)"), ("chest", "Chest (cm)"), ("arm", "Arm (cm)"),
    ("thigh", "Thigh (cm)"), ("steps", "Steps"), ("sleep_hours", "Sleep (h)"),
    ("resting_hr", "Resting heart rate (bpm)"), ("water_ml", "Water (ml)"),
])
def test_a_kind_is_named_with_its_unit_and_once_when_the_unit_is_its_word(kind, words):
    assert web.kind_with_unit(kind) == words
    assert kind in rules.MEASUREMENTS


def test_every_kind_has_its_case_for_kind_with_unit():
    assert set(rules.MEASUREMENTS) == {"waist", "hips", "chest", "arm", "thigh", "steps", "sleep_hours",
                                       "resting_hr", "water_ml"}


def test_each_side_fold_out_holds_the_contracts_fields_and_button(client):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    folds = foldouts_of(day_of(client, on))
    want = {
        "add-cardio": ("Add cardio", f"/day/{on}/cardio",
                       [("cardio-exercise", "Activity"), ("cardio-minutes", "Time (min)"),
                        ("cardio-distance", "Distance (km)")], "Save cardio"),
        "add-weigh-in": ("Add weigh-in", f"/day/{on}/weigh-in", [("kg", "Weight (kg)")], "Add weigh-in"),
        "add-measurement": ("Add measurement", f"/day/{on}/measurement",
                            [("measurement-kind", "Kind"), ("measurement-value", "Value")], "Save measurement"),
    }
    for fold_id, (summary, action, labels, button) in want.items():
        fold = folds[fold_id]
        assert fold.find_all("summary")[0].text() == summary, fold_id
        [form] = fold.find_all("form")
        assert (form.attrs["method"], form.attrs["action"]) == ("post", action), fold_id
        assert [(lb.attrs["for"], lb.text()) for lb in form.find_all("label")] == labels, fold_id
        assert [b.text() for b in form.find_all("button")] == [button], fold_id
        assert all(f.parent.is_("div", "field") or f.attrs.get("type") == "hidden"
                   for f in form.find_all("input") + form.find_all("select")), fold_id
        for control in form.find_all("input"):
            if control.attrs.get("type") != "hidden":
                assert control.attrs.get("inputmode") == "decimal", (fold_id, control.attrs)
    # Time and Distance side by side. Distance is optional, the rest are required.
    [row] = folds["add-cardio"].find_all("div", "field-row")
    assert [lb.text() for lb in row.find_all("label")] == ["Time (min)", "Distance (km)"]
    required = {c.attrs["id"]: "required" in c.attrs for i, f in folds.items() if i != "add-food"
                for c in f.find_all("input") + f.find_all("select") if c.attrs.get("type") != "hidden"}
    assert required == {"cardio-exercise": True, "cardio-minutes": True, "cardio-distance": False, "kg": True,
                        "measurement-kind": True, "measurement-value": True}
    activities = [(o.attrs["value"], o.text()) for o in folds["add-cardio"].find_all("option")]
    assert activities == [(e["name"], e["name"]) for e in services.list_exercises("cardio")["exercises"]]


@pytest.mark.parametrize("fold_id, mark, card", SIDE_FOLDOUTS)
def test_each_side_fold_out_is_closed_by_default_and_open_by_its_mark(client, fed, fold_id, mark, card):
    on = fed["today"]
    assert not any("open" in d.attrs for d in foldouts_of(day_of(client, on)).values())
    for query in (f"add={mark}", f"add=food&add={mark}", f"add={mark}&add=food", f"saved=bodyweight&add={mark}"):
        folds = foldouts_of(day_of(client, f"{on}?{query}"))
        opened = {i for i, d in folds.items() if "open" in d.attrs}
        assert opened == {fold_id} | ({"add-food"} if "food" in query else set()), query
    for query in (f"add={mark.capitalize()}", "add=", f"add{mark}=1", "add=lifts"):
        assert not any("open" in d.attrs for d in foldouts_of(day_of(client, f"{on}?{query}")).values()), query
    page = day_of(client, f"{on}?add={mark}")
    assert page.count(f'id="{fold_id}"') == 1
    home = foldouts_of(page)[fold_id].parent
    assert home.is_("div", "card") and home.elements()[0].text() == card and home.parent.is_("div", "col-side")


@pytest.mark.parametrize("path, data, field, control, says, kept", [
    ("cardio", {"exercise": "Outdoor Run", "minutes": "0", "distance_km": "5"}, "cardio-minutes", "input",
     "Give the minutes, from 1 to 600.", {"cardio-minutes": "0", "cardio-distance": "5", "cardio-exercise": "Outdoor Run"}),
    ("cardio", {"exercise": "Outdoor Run", "minutes": "30", "distance_km": "900"}, "cardio-distance", "input",
     "Give the distance in km, from 0.01 to 300, or leave it empty.",
     {"cardio-minutes": "30", "cardio-distance": "900", "cardio-exercise": "Outdoor Run"}),
    ("cardio", {"exercise": "Back Squat", "minutes": "30", "distance_km": ""}, "cardio-exercise", "select",
     "Choose an activity from the list.", {"cardio-minutes": "30", "cardio-distance": None, "cardio-exercise": None}),
    ("weigh-in", {"kg": "abc"}, "kg", "input", "That weight was not saved: give it in kg, from 20 to 400.",
     {"kg": "abc"}),
    ("measurement", {"kind": "waist", "value": "500"}, "measurement-value", "input",
     "That value was not saved: give waist in cm, from 30 to 300.",
     {"measurement-value": "500", "measurement-kind": "waist"}),
    ("measurement", {"kind": "resting_hr", "value": "300"}, "measurement-value", "input",
     "That value was not saved: give resting heart rate in bpm, from 20 to 250, as a whole number.",
     {"measurement-value": "300", "measurement-kind": "resting_hr"}),
    ("measurement", {"kind": "wrist", "value": "17"}, "measurement-kind", "select", "Choose a kind from the list.",
     {"measurement-value": "17", "measurement-kind": None}),
])
def test_a_side_error_opens_its_fold_out_under_its_field_tied_to_its_input(client, path, data, field, control,
                                                                            says, kept):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    r = post(client, f"/day/{on}/{path}", **data)
    assert r.status_code == 400
    folds = foldouts_of(r.text)
    fold_id = f"add-{path}"
    assert {i for i, d in folds.items() if "open" in d.attrs} == {fold_id}
    fold = folds[fold_id]
    # The error right under its control, in the page's words, tied by aria-describedby, the control invalid.
    [error] = fold.find_all("p", "error")
    assert error.text() == says and error.attrs["id"] == f"{field}-error"
    box = error.parent
    assert box.is_("div", "field") and "has-error" in box.classes
    assert [e.tag for e in box.elements()] == ["label", control, "p"]
    [target] = [c for c in box.elements() if c.tag == control]
    assert target.attrs["id"] == field and target.attrs.get("aria-invalid") == "true"
    assert target.attrs.get("aria-describedby") == f"{field}-error"
    assert sum(c.attrs.get("aria-invalid") == "true" for c in fold.iter()) == 1
    assert len(fold.find_all(cls="has-error")) == 1 and not fold.find_all("div", "error")
    # What was typed stays: an input's value, a select's choice when it is one of the list.
    for control_id, value in kept.items():
        [c] = [n for n in fold.iter() if n.attrs.get("id") == control_id]
        if c.tag == "input":
            assert c.attrs.get("value") == value, control_id
        else:
            chosen = [o.attrs["value"] for o in c.find_all("option") if "selected" in o.attrs]
            assert chosen == ([value] if value else []), control_id


@pytest.mark.parametrize("path, service", [("cardio", "log_cardio"), ("weigh-in", "log_bodyweight"),
                                           ("measurement", "log_measurement")])
def test_a_side_refusal_the_form_has_no_field_for_sits_at_the_top_of_its_form(client, monkeypatch, path, service):
    sign_in(client)
    on = today_of(only_user()).isoformat()
    monkeypatch.setattr(services, service, lambda *a: {"error": "date_too_old", "message": "x", "fix": "y"})
    data = {"cardio": {"exercise": "Outdoor Run", "minutes": "30"}, "weigh-in": {"kg": "80"},
            "measurement": {"kind": "waist", "value": "84"}}[path]
    r = post(client, f"/day/{on}/{path}", **data)
    assert r.status_code == 400
    fold = foldouts_of(r.text)[f"add-{path}"]
    assert "open" in fold.attrs
    [form] = fold.find_all("form")
    hidden, first = form.elements()[:2]
    assert hidden.attrs.get("name") == "csrf_token" and first.is_("div", "error")
    assert [p.text() for p in first.find_all("p")][0].startswith("That date cannot be used: ")
    assert not fold.find_all("p", "error") or all(p.inside("div", "error") for p in fold.find_all("p", "error"))
    assert not any(n.attrs.get("aria-invalid") for n in fold.iter())


def test_another_cards_error_leaves_the_side_fold_outs_closed(client):
    sign_in(client)
    r = post(client, f"/day/{today_of(only_user()).isoformat()}/food", description="Rice", kcal="abc")
    assert r.status_code == 400
    assert {i for i, d in foldouts_of(r.text).items() if "open" in d.attrs} == {"add-food"}


def test_what_was_typed_in_a_side_fold_out_comes_back_escaped(client):
    sign_in(client)
    r = post(client, f"/day/{today_of(only_user()).isoformat()}/weigh-in", kg='<b>"80"</b>')
    assert r.status_code == 400
    out = r.text[r.text.index('id="add-weigh-in"'):]
    assert 'value="&lt;b&gt;&#34;80&#34;&lt;/b&gt;"' in out and "<b>&#34;80" not in out


@pytest.mark.parametrize("state", ["empty", "stocked", "fed", "add", "error", "sample", "past"])
def test_the_side_cards_never_say_claude(client, state):
    sign_in(client)
    uid = only_user().id
    on = today_of(only_user())
    if state in ("stocked", "add", "past"):
        stock(only_user())
    if state in ("fed", "add"):
        stock_food(only_user(), weigh_in=state == "fed")
    if state == "sample":
        services.add_sample_data(uid, "web")
    address = on.isoformat() + ("?add=cardio&add=weigh-in&add=measurement" if state == "add" else "")
    if state == "past":
        address = (on - timedelta(days=1)).isoformat()
    if state == "error":
        page = post(client, f"/day/{on.isoformat()}/measurement", kind="waist", value="abc").text
    else:
        page = day_of(client, address)
    for title in ("Lifts", "Cardio", "Weigh-in", "Measurements"):
        card = side_card(page, title)
        assert not re.search(r"claude", card.text(), re.I), (state, title)
        assert not any(re.search(r"claude", v, re.I) for n in card.iter() for k, v in n.attrs.items()
                       if k in tr.READ_ATTRIBUTES), (state, title)



def glued(node) -> list[str]:
    """Two inline elements side by side with no whitespace between them, each holding words: read as text
    with no stylesheet, they run together as one word ("Waist84.5 cm")."""
    out = []
    for n in [node, *node.iter()]:
        kids = n.children
        for left, right in zip(kids, kids[1:]):
            if not isinstance(left, str) and not isinstance(right, str) and left.tag in tr.INLINE                     and right.tag in tr.INLINE and left.text() and right.text():
                out.append(f"{left.text()!r} touches {right.text()!r}")
    return out


def test_each_side_card_reads_as_text_with_a_space_between_a_name_and_its_figure(client, stocked):
    stock_food(only_user())
    page = day_of(client, stocked["today"].isoformat())
    for title in ("Lifts", "Cardio", "Weigh-in", "Measurements"):
        card = side_card(page, title)
        assert glued(card) == [], title
    pieces = [p.text for p in tr.html_text(page[page.index("<h2>Measurements</h2>"):]) if p.kind != "attr"]
    assert "Waist 84.5 cm" in pieces and "Steps 9,500 steps" in pieces and "Sleep 7.25 h" in pieces
    assert not any(re.search(r"[A-Za-z]\d", t) for t in pieces), pieces
    weigh = [p.text for p in tr.html_text(page[page.index("<h2>Weigh-in</h2>"):page.index("<h2>Measurements</h2>")])]
    assert "80 kg" in weigh  # stock_food's weigh-in replaced the day's 78.4 kg


def test_measurement_labels_are_gone_and_no_template_holds_a_kinds_word(client):
    """The web reads every measurement name from measurements.kind_word: web.MEASUREMENT_LABELS and its
    lookup are gone, and no template or web.py carries its own copy of a kind's word or the fallback."""
    from pathlib import Path  # noqa: PLC0415

    assert not hasattr(web, "MEASUREMENT_LABELS") and not hasattr(web, "measurement_label")
    templates = sorted((Path(web.__file__).parent / "templates").glob("*.html"))
    assert len(templates) > 5
    sources = {p.name: p.read_text(encoding="utf-8") for p in templates}
    sources["web.py"] = Path(web.__file__).read_text(encoding="utf-8")
    for name, src in sources.items():
        for word in rules.KIND_WORDS.values():
            for literal in (f'"{word}"', f"'{word}'"):
                assert literal not in src, (name, literal)
        assert 'replace("_", " ").capitalize()' not in src, name
    # Nor as text a person reads: an element's own words, or a word between template tags, read through
    # Jinja's parser. privacy.html is exempt by name: it lists the kinds in a sentence of prose about what is
    # stored ("Waist, hips, chest, ..."), which names the data and labels no figure.
    for name, src in sources.items():
        if name in ("web.py", "privacy.html"):
            continue
        for piece in tr.template_text(src):
            for word in rules.KIND_WORDS.values():
                assert not re.search(rf"\b{re.escape(word)}\b", piece.text, re.I), (name, word, piece.text[:80])
    # The edit page and the Goals form read it too.
    sign_in(client)
    uid = only_user().id
    hr = services.log_measurement(uid, {"kind": "resting_hr", "value": "58"}, "claude")
    edit = client.get(f"/entries/measurement/{hr['id']}/edit").text
    assert "<p>Resting heart rate (bpm)</p>" in edit
    goals = client.get("/goals").text
    assert '<option value="resting_hr">Resting heart rate (bpm)</option>' in goals


# ---------------------------------------------------------------- stage 6, step 4: the Food card review's fixes

def test_servings_whose_saved_food_went_with_the_sample_data_are_counted(client):
    """A person's own entry logged against a sample food keeps its servings when Remove sample data takes the
    food away (the link is set to null). Line two counts them in words."""
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.add_sample_data(uid, "web")
    roti = next(f["id"] for f in services.list_saved_foods(uid)["foods"] if f["name"] == "Roti")
    mine = services.log_food(uid, {"entries": [{"saved_food_id": roti, "servings": 3, "meal": "snack"}]}, "claude")
    assert "error" not in mine
    on = today_of(only_user()).isoformat()
    [before] = meal_of(food_card(day_of(client, on)), "Snack")[-1:]
    assert (before["name"], before["figures"]) == ("Roti", "360 kcal, 3 rotis")
    assert "error" not in services.remove_sample_data(uid, "web")
    [row] = [e for e in rows(FoodEntry, uid) if e.id == mine["ids"][0]]
    assert (row.saved_food_id, row.servings, row.deleted_at) == (None, 3, None)  # the real path unlinks it
    [after] = meal_of(food_card(day_of(client, on)), "Snack")
    assert (after["name"], after["figures"], after["note"]) == ("Roti", "360 kcal, 3 servings", None)


@pytest.mark.parametrize("servings, says", [(3, "3 servings"), (1, "1 serving"), (1.5, "1.5 servings"),
                                            (0.25, "0.25 servings")])
def test_servings_with_no_saved_food_are_counted_through_the_servings_rule(servings, says):
    entry = {"saved_food_name": None, "servings": servings, "source": "saved_food", "description": "Roti"}
    assert web.serving_words(entry) == says
    assert says.split(" ")[0] == formatting.show(servings, "servings")
    assert web.serving_words({**entry, "servings": None}) == ""


def test_a_name_ending_in_a_digit_stays_apart_from_its_kcal(client):
    """Read without the stylesheet, the name, line two and the grey line keep a space between them, so a
    number in the name never runs into the kcal."""
    from .test_pages_text import page_findings  # noqa: PLC0415 - test_pages_text imports this file

    sign_in(client)
    uid = only_user().id
    assert "error" not in services.log_food(uid, {"entries": [{"description": "Protein bar 2", "kcal": 250}]},
                                            "claude")
    on = today_of(only_user()).isoformat()
    card = food_card(day_of(client, on))
    line = re.search(r'<li class="entry-line">.*?</li>', card, re.S).group(0)
    assert "Protein bar 2 250 kcal" in " ".join(p.text for p in tr.html_text(line))
    assert page_findings(f"/day/{on}", card, [], uid) == []  # nothing masked: the text rules read it all
    joined = card.replace("</span>\n          <span", "</span><span")
    assert joined != card and page_findings(f"/day/{on}", joined, [], uid)  # touching, it reads "2250"


def test_a_sample_row_renamed_with_sample_keeps_the_word(client):
    """Only a saved food's name ever gets " (sample)" from sample data. A description is the person's own."""
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.add_sample_data(uid, "web")
    tea = next(e for e in rows(FoodEntry, uid) if e.description == "Tea with milk" and e.is_sample)
    on = tea.date.isoformat()
    assert "error" not in services.edit_entry(uid, "food_entry", tea.id, {"description": "My lunch (sample)"}, "claude")
    card = food_card(day_of(client, on))
    [mine] = [e for e in food_entries(card) if e["name"].startswith("My lunch")]
    assert mine["name"] == "My lunch (sample)" and ("tag-sample", "sample") in mine["tags"]


def parts_field(*args, **kwargs) -> str:
    return str(web.templates.env.get_template("_parts.html").module.field(*args, **kwargs))


@pytest.mark.parametrize("value, drawn", [(0, ' value="0"'), ("0", ' value="0"'),
                                          ("", None), (None, None), ("72.5", ' value="72.5"')])
def test_the_field_keeps_a_zero_and_draws_no_value_for_nothing(value, drawn):
    shown = parts_field("kg", "kg", "Weight (kg)", None, value, inputmode="decimal")
    tag = re.search(r"<input [^>]*>", shown).group(0)
    assert (drawn in tag) if drawn else ("value=" not in tag)
    chosen = parts_field("k", "k", "Kind", None, value, choices=[(0, "Zero"), ("72.5", "Some")])
    assert ("selected" in chosen) is (drawn is not None)


@pytest.mark.parametrize("error, hint, described", [
    (None, "Leave it empty for a walk on a machine.", "d-hint"),
    ("Give the distance in km.", "Leave it empty for a walk on a machine.", "d-error d-hint"),
    ("Give the distance in km.", None, "d-error"),
    (None, None, None),
])
def test_the_field_ties_its_hint_and_its_error_to_the_control(error, hint, described):
    for kwargs in ({"inputmode": "decimal"}, {"choices": [("a", "A")]}):
        shown = parts_field("d", "distance_km", "Distance (km)", error, "", hint=hint, **kwargs)
        control = re.search(r"<(?:input|select) [^>]*>", shown).group(0)
        if described:
            assert f'aria-describedby="{described}"' in control
        else:
            assert "aria-describedby" not in control
        assert ('aria-invalid="true"' in control) is bool(error)
        blocks = re.findall(r'<p class="(error|hint)" id="([^"]+)">([^<]*)</p>', shown)
        want = ([("error", "d-error", error)] if error else []) + ([("hint", "d-hint", hint)] if hint else [])
        assert blocks == want  # the error right under the control, then the hint
        if want:
            assert shown.index(control) < shown.index("<p ")


def test_a_hint_on_the_page_passes_the_tie_rule():
    from .test_page_rules import describedby_findings, seen  # noqa: PLC0415 - test_page_rules imports this file

    good = parts_field("d", "distance_km", "Distance (km)", "Give the distance.", "x", hint="Optional.")
    assert describedby_findings(seen(good)) == []


@pytest.mark.parametrize("required", [True, False])
def test_the_fields_select_carries_required_when_asked(required):
    shown = parts_field("cardio-exercise", "exercise", "Activity", None, "", choices=[("Walk", "Walk")],
                        required=required)
    select = re.search(r"<select [^>]*>", shown).group(0)
    assert (" required" in select) is required
    assert select == f'<select id="cardio-exercise" name="exercise"{" required" if required else ""}>'


def test_the_unused_actions_macro_is_gone():
    day = (web.HERE / "templates" / "day.html").read_text(encoding="utf-8")
    assert "macro actions" not in day and not re.search(r"\{\{\s*actions\(", day)


# ---------------------------------------------------------------- stage 6, step 4: the edit pages

def stock_every_kind(user: User) -> dict:
    """One entry of each kind, today, with figures where formatting.field, show and str() each differ: a whole
    80.0 kg, a 1,250 kcal estimate, 75.5 minutes (1:15:30 on the day page), 1,100 machine kcal, 10.25 km/h."""
    today = today_of(user).isoformat()
    weigh_in = services.log_bodyweight(user.id, {"kg": 80.0}, "claude")
    waist = services.log_measurement(user.id, {"kind": "waist", "value": 84.55}, "claude")
    session = services.log_lifts(user.id, {"notes": "felt strong", "sets": [
        {"exercise": "Back Squat", "weight_kg": 60, "reps": 5}, {"exercise": "Pull-Up", "weight_kg": 0, "reps": 8}]},
        "claude")
    food = services.log_food(user.id, {"entries": [
        {"description": "Biryani", "kcal": 1250, "estimated": True, "meal": "lunch"},
        {"description": "Tea", "kcal": 40, "meal": "breakfast"}]}, "claude")
    run = services.log_cardio(user.id, {"exercise": "Treadmill Run", "minutes": 75.5, "distance_km": 12.5,
                                        "incline_pct": 2.5, "speed_kmh": 10.25, "avg_hr": 140, "machine_kcal": 1100,
                                        "notes": "Long one"}, "claude")
    for result in (weigh_in, waist, session, food, run):
        assert "error" not in result, result
    return {"today": today, "bodyweight": weigh_in["id"], "measurement": waist["id"], "lift_session": session["id"],
            "food_entry": food["ids"][0], "you_said": food["ids"][1], "cardio_entry": run["id"],
            "estimate": run["estimate"]}


@pytest.fixture
def every_kind(client):
    sign_in(client)
    return stock_every_kind(only_user())


def edit_of(client, kind: str, entry_id) -> str:
    r = client.get(f"/entries/{kind}/{entry_id}/edit")
    assert r.status_code == 200, r.text
    return r.text


def edit_form(page: str):
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    [form] = parse(page).find_all("form", "stack")
    return form


def edit_card(page: str):
    """The edit page's one plain card: the div.card directly inside main."""
    from .test_pages_text import parse  # noqa: PLC0415

    [main] = parse(page).find_all("main")
    [card] = [c for c in main.elements() if c.is_("div", "card")]
    return card


def controls_of(page: str) -> list[dict]:
    """Each field of the edit form, in order: its label, its control's tag and name, the value it holds (an
    input's value, a select's chosen option, a textarea's text), and its inputmode, type and required."""
    out = []
    for field in edit_form(page).find_all("div", "field"):
        [label] = field.find_all("label")
        [control] = [n for n in field.elements() if n.tag in ("input", "select", "textarea")]
        assert label.attrs["for"] == control.attrs["id"]
        if control.tag == "select":
            chosen = [o.attrs["value"] for o in control.find_all("option") if "selected" in o.attrs]
            value = chosen[0] if chosen else None
        elif control.tag == "textarea":
            value = "".join(c for c in control.children if isinstance(c, str))
        else:
            value = control.attrs.get("value")
        out.append({"label": label.text(), "tag": control.tag, "name": control.attrs["name"], "value": value,
                    "inputmode": control.attrs.get("inputmode"), "type": control.attrs.get("type"),
                    "required": "required" in control.attrs, "maxlength": control.attrs.get("maxlength"),
                    "rows": control.attrs.get("rows"),
                    "options": [(o.attrs["value"], o.text()) for o in control.find_all("option")]})
    return out


def title_of(page: str) -> str:
    return re.search(r"<title>(.*?)</title>", page).group(1)


# Section L's labels for each kind, in order, each naming its unit (a measurement's under its name).
EDIT_LABELS = {
    "bodyweight": ["Weight (kg)", "Date"],
    "measurement": ["Value", "Date"],
    "lift_session": ["Notes", "Date"],
    "food_entry": ["Food", "Calories (kcal)", "Meal", "Date"],
    "cardio_entry": ["Activity", "Time (min)", "Distance (km)", "Incline (%)", "Speed (km/h)",
                     "Average heart rate (bpm)", "Machine's calories (kcal)", "Notes", "Date"],
}
# The values the form holds as drawn, through formatting.field: none is what show or str() would give.
EDIT_VALUES = {
    "bodyweight": {"kg": "80"},
    "measurement": {"value": "84.55"},
    "lift_session": {"notes": "felt strong"},
    "food_entry": {"description": "Biryani", "kcal": "1250", "meal": "lunch"},
    "cardio_entry": {"exercise": "Treadmill Run", "minutes": "75.5", "distance_km": "12.5", "incline_pct": "2.5",
                     "speed_kmh": "10.25", "avg_hr": "140", "machine_kcal": "1100", "notes": "Long one"},
}
EDIT_QUANTITIES = {"kg": "weigh_in", "value": "cm", "kcal": "kcal", "minutes": "session_time", "distance_km": "km",
                   "incline_pct": "incline", "speed_kmh": "speed", "avg_hr": "bpm", "machine_kcal": "kcal"}
EDIT_MODELS = {"bodyweight": Bodyweight, "measurement": Measurement, "lift_session": LiftSession,
               "food_entry": FoodEntry, "cardio_entry": CardioEntry}


@pytest.mark.parametrize("kind", list(EDIT_LABELS))
def test_each_edit_page_has_its_head_one_card_its_fields_and_save_and_cancel(client, every_kind, kind):
    from .test_pages_text import claude_findings, parse  # noqa: PLC0415

    today, entry_id = every_kind["today"], every_kind[kind]
    page = edit_of(client, kind, entry_id)
    words = web.ENTRY_WORDS[kind].lower()
    assert title_of(page) == f"Edit {words} · Spotter"
    [main] = parse(page).find_all("main")
    assert main.classes == ["page", "form-page"]
    # The head: the h1 in sentence case, then the link back to the day, named as the day page is.
    head = main.elements()[0]
    assert head.is_("div", "page-head")
    h1, nav = head.elements()
    assert h1.tag == "h1" and h1.text() == f"Edit {words}" and h1.elements() == []
    assert nav.is_("nav", "day-nav") and nav.attrs["aria-label"] == "Back"
    [back] = nav.elements()
    assert back.is_("a", "back") and back.attrs["href"] == f"/day/{today}" and back.text() == "Today"
    # One plain card holding the form, then the Sets card on a lift session only.
    cards = main.elements()[1:]
    assert cards[0].is_("div", "card") and "aria-labelledby" not in cards[0].attrs
    assert [c.tag for c in cards] == (["div", "section"] if kind == "lift_session" else ["div"])
    [form] = [n for n in main.find_all("form") if "stack" in n.classes]
    assert form.parent is cards[0]
    assert form.classes == ["stack"] and form.attrs["method"] == "post"
    assert form.attrs["action"] == f"/entries/{kind}/{entry_id}/edit"
    # The fields, stacked (no field-row), each label naming its unit, each value through formatting.field.
    assert not form.find_all(cls="field-row")
    fields = controls_of(page)
    assert [f["label"] for f in fields] == EDIT_LABELS[kind]
    assert {f["name"]: f["value"] for f in fields if f["name"] != "date"} == EDIT_VALUES[kind]
    assert fields[-1] == {"label": "Date", "tag": "input", "name": "date", "value": today, "inputmode": None,
                          "type": "date", "required": True, "maxlength": None, "rows": None, "options": []}
    row = next(r for r in rows(EDIT_MODELS[kind], only_user().id) if r.id == entry_id)
    for f in fields:
        if f["name"] in EDIT_QUANTITIES:
            assert f["value"] == formatting.field(getattr(row, f["name"]), EDIT_QUANTITIES[f["name"]]), f
            assert f["inputmode"] in ("decimal", "numeric"), f
    # Save (white), then Cancel, a link to the day, the form's last row.
    [actions] = form.find_all("div", "form-actions")
    save, cancel = actions.elements()
    assert save.is_("button", "primary") and save.text() == "Save"
    assert cancel.is_("a", "action") and cancel.attrs["href"] == f"/day/{today}" and cancel.text() == "Cancel"
    assert form.elements()[-1] is actions
    assert claude_findings(f"/entries/{kind}/{entry_id}/edit", page) == []
    assert "claude" not in re.sub(r"<[^>]+>", " ", page).casefold()


def test_the_measurement_value_sits_under_its_name_and_unit(client, every_kind):
    form = edit_form(edit_of(client, "measurement", every_kind["measurement"]))
    named = form.elements()[1]
    assert named.tag == "p" and named.text() == "Waist (cm)"
    assert form.elements()[2].find_all("label")[0].text() == "Value"


def test_the_back_link_and_cancel_name_a_past_day_by_its_date(client):
    sign_in(client)
    user = only_user()
    today = today_of(user)
    yesterday = (today - timedelta(days=1)).isoformat()
    made = services.log_bodyweight(user.id, {"kg": "79.2", "date": yesterday}, "claude")
    page = edit_of(client, "bodyweight", made["id"])
    assert f'<a class="back" href="/day/{yesterday}">{formatting.day(yesterday, today)}</a>' in page
    assert f'<a class="action" href="/day/{yesterday}">Cancel</a>' in page
    assert ">Today<" not in page.split("<main", 1)[1]


def test_the_back_link_and_cancel_keep_the_stored_day_when_a_date_was_typed_wrong(client, every_kind):
    for typed in ("2099-01-01", "abc", ""):
        r = post(client, f"/entries/bodyweight/{every_kind['bodyweight']}/edit", kg="80", date=typed)
        assert r.status_code == 400, typed
        assert f'<a class="back" href="/day/{every_kind["today"]}">Today</a>' in r.text
        assert f'<a class="action" href="/day/{every_kind["today"]}">Cancel</a>' in r.text
        assert next(f["value"] for f in controls_of(r.text) if f["name"] == "date") == (typed or None)


@pytest.mark.parametrize("which, tag, words", [("food_entry", "tag-claude_estimate", "estimate"),
                                               ("you_said", "tag-you_said", "you said")])
def test_the_food_edit_card_opens_with_its_source_tag_and_the_you_said_line(client, every_kind, which, tag, words):
    card = edit_card(edit_of(client, "food_entry", every_kind[which]))
    first = card.elements()[0]
    assert first.is_("p", "small")
    assert first.text() == f"This number is {words}. If you change the kcal, it becomes a number you said."
    [shown] = first.elements()
    assert shown.tag == "span" and shown.classes == ["tag", tag] and shown.text() == words
    assert card.elements()[1].tag == "form"


def test_no_other_edit_page_carries_a_source_tag(client, every_kind):
    for kind in ("bodyweight", "measurement", "lift_session", "cardio_entry"):
        page = edit_of(client, kind, every_kind[kind])
        assert 'class="tag' not in page and "number you said" not in page, kind


def test_the_cardio_edit_card_ends_with_the_estimate_now_and_the_page_shows_the_footer(client, every_kind):
    run = every_kind["cardio_entry"]
    line = (f"Calorie estimate now: {formatting.show(every_kind['estimate']['kcal'], 'kcal', page=True)}. "
            "It is worked out again when you save.")
    refused = post(client, f"/entries/cardio_entry/{run}/edit", exercise="Treadmill Run", minutes="0",
                   date=every_kind["today"])
    assert refused.status_code == 400
    for page in (edit_of(client, "cardio_entry", run), refused.text):
        card = edit_card(page)
        last = card.elements()[-1]
        assert last.is_("p", "small") and last.text() == line
        assert card.elements()[-2].tag == "form"
        assert page.count(ATTRIBUTION) == 1 and '<footer class="attribution">' in page
    for kind in ("bodyweight", "measurement", "lift_session", "food_entry"):
        page = edit_of(client, kind, every_kind[kind])
        assert "Calorie estimate now" not in page and ATTRIBUTION not in page, kind


def test_a_cardio_entry_with_no_estimate_shows_no_estimate_line_and_no_footer(client):
    sign_in(client)
    run = services.log_cardio(only_user().id, {"exercise": "Outdoor Walk", "minutes": 40}, "claude")
    assert run["estimate"] is None
    page = edit_of(client, "cardio_entry", run["id"])
    assert "Calorie estimate now" not in page and ATTRIBUTION not in page and "<footer" not in page
    assert edit_card(page).elements()[-1].tag == "form"
    # The figures the entry lacks draw no value at all.
    assert {f["name"]: f["value"] for f in controls_of(page)} == {
        "exercise": "Outdoor Walk", "minutes": "40", "distance_km": None, "incline_pct": None, "speed_kmh": None,
        "avg_hr": None, "machine_kcal": None, "notes": "", "date": today_of(only_user()).isoformat()}
    refused = post(client, f"/entries/cardio_entry/{run['id']}/edit", exercise="Outdoor Walk", minutes="0")
    assert refused.status_code == 400 and "Calorie estimate now" not in refused.text and "<footer" not in refused.text


def sets_card_of(page: str):
    from .test_pages_text import parse, tables  # noqa: PLC0415

    dom = parse(page)
    [card] = dom.find_all("section", "card")
    return dom, card, tables(card)


def test_the_lift_edit_page_adds_a_read_only_sets_card(client, every_kind):
    page = edit_of(client, "lift_session", every_kind["lift_session"])
    dom, card, [table] = sets_card_of(page)
    h2 = card.elements()[0]
    assert h2.tag == "h2" and h2.text() == "Sets" and card.attrs["aria-labelledby"] == h2.attrs["id"]
    assert [n for n in dom.iter() if n.attrs.get("id") == h2.attrs["id"]] == [h2]
    wrap = card.elements()[1]
    assert wrap.is_("div", "table-wrap") and wrap.elements() == [table.node]
    assert table.headers == ["Exercise", "Set", "Weight (kg)", "Reps"]
    assert [[c.text() for c in r] for r in table.rows] == [["Back Squat", "1", "60", "5"], ["Pull-Up", "1", "", "8"]]
    assert [c.classes for c in table.rows[0]] == [[], ["num"], ["num"], ["num"]]
    line = card.elements()[2]
    assert line.is_("p", "small")
    assert line.text() == "Sets are changed through your assistant for now: tell it what to fix."
    assert len(card.elements()) == 3
    # Read-only: nothing in it can be pressed or typed into.
    assert not [n for n in card.iter() if n.tag in ("form", "input", "select", "textarea", "button", "a")]


def test_a_session_with_no_weighted_set_shows_no_weight_column_on_its_edit_page(client):
    sign_in(client)
    made = services.log_lifts(only_user().id, {"sets": [{"exercise": "Pull-Up", "weight_kg": 0, "reps": 8},
                                                         {"exercise": "Pull-Up", "weight_kg": 0, "reps": 6}]}, "claude")
    _, _, [table] = sets_card_of(edit_of(client, "lift_session", made["id"]))
    assert table.headers == ["Exercise", "Set", "Reps"]
    assert [[c.text() for c in r] for r in table.rows] == [["Pull-Up", "1", "8"], ["Pull-Up", "2", "6"]]


def test_the_sets_card_reads_as_todays_lifts_card(client, every_kind):
    """The edit page draws its sets through parts.sets_table. Today's Lifts card, drawn by its own copy until a
    later pass moves it onto the macro, reads the same."""
    from .test_pages_text import tables  # noqa: PLC0415

    _, _, [edited] = sets_card_of(edit_of(client, "lift_session", every_kind["lift_session"]))
    [today] = tables(side_card(day_of(client, every_kind["today"]), "Lifts"))
    assert edited.headers == today.headers
    assert [[(c.text(), c.classes) for c in r] for r in edited.rows] == \
        [[(c.text(), c.classes) for c in r] for r in today.rows]


def test_the_sets_table_macro_drops_the_weight_column_and_blanks_a_zero():
    module = web.templates.env.get_template("_parts.html").module
    weighted = str(module.sets_table([{"exercise": "Back Squat", "set_number": 1, "weight_kg": 102.5, "reps": 5},
                                      {"exercise": "Pull-Up", "set_number": 1, "weight_kg": 0, "reps": 8}]))
    assert weighted.startswith('<div class="table-wrap">\n  <table>') and weighted.endswith("</table>\n</div>")
    assert '<th class="num">Weight (kg)</th>' in weighted
    assert '<tr><td>Back Squat</td><td class="num">1</td><td class="num">102.5</td><td class="num">5</td></tr>' \
        in weighted
    assert '<tr><td>Pull-Up</td><td class="num">1</td><td class="num"></td><td class="num">8</td></tr>' in weighted
    bare = str(module.sets_table([{"exercise": "Pull-Up", "set_number": 1, "weight_kg": 0, "reps": 8}]))
    assert "Weight" not in bare and bare.count("<td") == 3


# One refused edit per kind of control: an input, a typed 0, a select, a textarea and a date. The field it lands
# on, the page's own words there, and what comes back in the control (a select keeps no choice off its list).
EDIT_ERRORS = [
    ("bodyweight", {"kg": "abc"}, "kg", "That weight was not saved: give it in kg, from 20 to 400.", "abc"),
    ("bodyweight", {"kg": "0"}, "kg", "That weight was not saved: give it in kg, from 20 to 400.", "0"),
    ("measurement", {"value": "500"}, "value", "That value was not saved: give waist in cm, from 30 to 300.", "500"),
    ("lift_session", {"notes": "x" * 2001}, "notes", "Those notes were not saved: keep them under 2,000 characters.",
     "x" * 2001),
    ("food_entry", {"description": "Biryani", "kcal": "abc", "meal": "lunch"}, "kcal",
     "That kcal was not saved: give a whole number from 0 to 5,000.", "abc"),
    ("food_entry", {"description": "Biryani", "kcal": "1250", "meal": "brunch"}, "meal",
     "Choose breakfast, lunch, dinner or snack.", None),
    ("cardio_entry", {"exercise": "Treadmill Run", "minutes": "0"}, "minutes", "Give the minutes, from 1 to 600.", "0"),
    ("cardio_entry", {"exercise": "Back Squat", "minutes": "30"}, "exercise", "Choose an activity from the list.", None),
    ("cardio_entry", {"exercise": "Treadmill Run", "minutes": "30", "notes": "x" * 2001}, "notes",
     "Keep the notes under 2,000 characters.", "x" * 2001),
    ("food_entry", {"description": "", "kcal": "1250", "meal": "lunch"}, "description",
     "Give a description, up to 200 characters.", None),
    ("cardio_entry", {"exercise": "Treadmill Run", "minutes": "30", "distance_km": "500"}, "distance_km",
     "Give the distance in km, from 0.01 to 300, or leave it empty.", "500"),
    ("cardio_entry", {"exercise": "Treadmill Run", "minutes": "30", "incline_pct": "50"}, "incline_pct",
     "Give the incline in %, from 0 to 40, or leave it empty.", "50"),
    ("cardio_entry", {"exercise": "Treadmill Run", "minutes": "30", "speed_kmh": "100"}, "speed_kmh",
     "Give the speed in km/h, from 0.1 to 60, or leave it empty.", "100"),
    ("cardio_entry", {"exercise": "Treadmill Run", "minutes": "30", "avg_hr": "300"}, "avg_hr",
     "Give the average heart rate, 30 to 240, as a whole number, or leave it empty.", "300"),
    ("cardio_entry", {"exercise": "Treadmill Run", "minutes": "30", "machine_kcal": "9000"}, "machine_kcal",
     "Give the machine's number, 1 to 5,000, as a whole number, or leave it empty.", "9000"),
    ("bodyweight", {"kg": "80", "date": "2099-01-01"}, "date",
     "That date cannot be used: choose a day up to today, from the last two years, with nothing else on it.",
     "2099-01-01"),
]


@pytest.mark.parametrize("kind, data, name, words, typed", EDIT_ERRORS,
                         ids=[f"{k}-{n}-{(t or 'none')[:8]}" for k, _, n, _, t in EDIT_ERRORS])
def test_each_edit_error_sits_under_its_field_tied_to_it_and_keeps_what_was_typed(client, every_kind, kind, data,
                                                                                   name, words, typed):
    entry_id = every_kind[kind]
    before = next(r for r in rows(EDIT_MODELS[kind], only_user().id) if r.id == entry_id)
    r = post(client, f"/entries/{kind}/{entry_id}/edit", **{"date": every_kind["today"], **data})
    assert r.status_code == 400
    assert title_of(r.text) == f"Edit {web.ENTRY_WORDS[kind].lower()} · Spotter"
    assert '<body class="calm"' in r.text
    form = edit_form(r.text)
    [field] = [f for f in form.find_all("div", "field") if [c for c in f.elements() if c.attrs.get("name") == name]]
    assert field.classes == ["field", "has-error"]
    label, control, error = field.elements()
    assert label.tag == "label" and control.attrs["name"] == name
    assert control.attrs["aria-invalid"] == "true"
    assert control.attrs["aria-describedby"] == f"{control.attrs['id']}-error"
    assert error.is_("p", "error") and error.attrs["id"] == f"{control.attrs['id']}-error" and error.text() == words
    assert next(f for f in controls_of(r.text) if f["name"] == name)["value"] == typed
    # Only the refused field is marked, nothing sits at the top of the form, and nothing was written.
    assert [c.attrs["name"] for c in form.iter() if c.attrs.get("aria-invalid") == "true"] == [name]
    assert not form.find_all("div", "error")
    after = next(x for x in rows(EDIT_MODELS[kind], only_user().id) if x.id == entry_id)
    assert (after.date, after.deleted_at) == (before.date, before.deleted_at)
    assert "claude" not in re.sub(r"<[^>]+>", " ", r.text).casefold()


def test_what_was_typed_into_an_edit_comes_back_escaped(client, every_kind):
    r = post(client, f"/entries/food_entry/{every_kind['food_entry']}/edit", description='<b>"x"</b>', kcal="abc",
             meal="lunch", date=every_kind["today"])
    assert r.status_code == 400
    assert 'value="&lt;b&gt;&#34;x&#34;&lt;/b&gt;"' in r.text and '<b>"x"</b>' not in r.text


def test_an_edit_refusal_with_no_field_of_its_own_sits_at_the_top_of_the_form(client, every_kind, monkeypatch):
    monkeypatch.setattr(services, "edit_entry", lambda *args: {"error": "invalid_kind", "message": "x", "fix": "y"})
    r = post(client, f"/entries/measurement/{every_kind['measurement']}/edit", value="83", date=every_kind["today"])
    assert r.status_code == 400 and title_of(r.text) == "Edit measurement · Spotter"
    form = edit_form(r.text)
    token, strip = form.elements()[:2]
    assert token.attrs == {"type": "hidden", "name": "csrf_token", "value": token.attrs["value"]}
    assert strip.is_("div", "error") and [p.tag for p in strip.elements()] == ["p"]
    assert strip.text() == "Choose a kind from the list."
    assert not form.find_all("p", "error") and "aria-invalid" not in r.text and "has-error" not in r.text


@pytest.mark.parametrize("kind, change", [("bodyweight", {"kg": "79.5"}), ("measurement", {"value": "83"}),
                                          ("lift_session", {"notes": "heavy day"}),
                                          ("food_entry", {"kcal": "1200"}), ("cardio_entry", {"minutes": "60"})])
def test_an_edit_lands_on_the_day_marked_saved_and_the_form_as_drawn_is_unchanged(client, every_kind, kind, change):
    on, entry_id = every_kind["today"], every_kind[kind]
    edit = f"/entries/{kind}/{entry_id}/edit"
    drawn = {f["name"]: f["value"] or "" for f in controls_of(edit_of(client, kind, entry_id))}
    before = calls(only_user().id)
    assert location(post(client, edit, **drawn)) == SITE + f"/day/{on}?saved=unchanged"
    assert calls(only_user().id) == before  # the form sent back as drawn writes nothing
    assert location(post(client, edit, **{**drawn, **change})) == SITE + f"/day/{on}?saved={kind}"
    redrawn = {f["name"]: f["value"] or "" for f in controls_of(edit_of(client, kind, entry_id))}
    assert redrawn == {**drawn, **change}


@pytest.mark.parametrize("kind", list(EDIT_LABELS))
def test_another_persons_entry_and_a_deleted_one_answer_404_on_the_edit_page(client, every_kind, make_user, kind):
    other = make_user()
    theirs = stock_every_kind(other)
    for r in (client.get(f"/entries/{kind}/{theirs[kind]}/edit"),
              post(client, f"/entries/{kind}/{theirs[kind]}/edit", date=every_kind["today"])):
        assert r.status_code == 404 and "There is nothing here." in r.text
        assert "Biryani" not in r.text and "Long one" not in r.text and "felt strong" not in r.text
    mine = every_kind[kind]
    assert post(client, f"/entries/{kind}/{mine}/delete").status_code == 303
    for r in (client.get(f"/entries/{kind}/{mine}/edit"), post(client, f"/entries/{kind}/{mine}/edit", date="")):
        assert r.status_code == 404 and "There is nothing here." in r.text


def test_the_field_draws_a_date_input_and_a_textarea_when_asked():
    date_input = parts_field("edit-date", "date", "Date", None, "2026-11-10", type="date", required=True)
    assert '<input id="edit-date" name="date" type="date" value="2026-11-10" required autocomplete="off">' in date_input
    notes = parts_field("edit-notes", "notes", "Notes", None, "a <b> note", rows=3)
    assert '<textarea id="edit-notes" name="notes" rows="3">a &lt;b&gt; note</textarea>' in notes
    assert "<input" not in notes
    empty = parts_field("edit-notes", "notes", "Notes", None, "", rows=2)
    assert '<textarea id="edit-notes" name="notes" rows="2"></textarea>' in empty
    refused = parts_field("edit-notes", "notes", "Notes", "Too long.", "x", rows=2, maxlength=2000)
    assert ('<textarea id="edit-notes" name="notes" rows="2" maxlength="2000" aria-invalid="true" '
            'aria-describedby="edit-notes-error">x</textarea>\n  <p class="error" id="edit-notes-error">Too long.</p>') \
        in refused
    assert "type=" not in parts_field("kg", "kg", "Weight (kg)", None, "72")
    asked = parts_field("edit-notes", "notes", "Notes", None, "", rows=2, required=True)
    assert '<textarea id="edit-notes" name="notes" rows="2" required></textarea>' in asked


# Each control's attributes as section L and the old page set them: (name, tag, inputmode, required, maxlength,
# rows). Every number input has an inputmode: numeric where only a whole number is taken.
EDIT_ATTRS = {
    "bodyweight": [("kg", "input", "decimal", True, None, None)],
    "measurement": [("value", "input", "decimal", True, None, None)],
    "lift_session": [("notes", "textarea", None, False, None, "3")],
    "food_entry": [("description", "input", None, True, "200", None), ("kcal", "input", "numeric", True, None, None),
                   ("meal", "select", None, False, None, None)],
    "cardio_entry": [("exercise", "select", None, False, None, None), ("minutes", "input", "decimal", True, None, None),
                     ("distance_km", "input", "decimal", False, None, None),
                     ("incline_pct", "input", "decimal", False, None, None),
                     ("speed_kmh", "input", "decimal", False, None, None),
                     ("avg_hr", "input", "numeric", False, None, None),
                     ("machine_kcal", "input", "numeric", False, None, None),
                     ("notes", "textarea", None, False, None, "2")],
}


@pytest.mark.parametrize("kind", list(EDIT_ATTRS))
def test_each_edit_control_carries_its_inputmode_required_and_limits(client, every_kind, kind):
    fields = controls_of(edit_of(client, kind, every_kind[kind]))[:-1]  # the Date field is held above
    assert [(f["name"], f["tag"], f["inputmode"], f["required"], f["maxlength"], f["rows"]) for f in fields]         == EDIT_ATTRS[kind]


def test_the_edit_selects_offer_every_meal_and_every_activity(client, every_kind):
    [meal] = [f for f in controls_of(edit_of(client, "food_entry", every_kind["food_entry"])) if f["name"] == "meal"]
    assert meal["options"] == [("breakfast", "Breakfast"), ("lunch", "Lunch"), ("dinner", "Dinner"), ("snack", "Snack")]
    [activity] = [f for f in controls_of(edit_of(client, "cardio_entry", every_kind["cardio_entry"]))
                  if f["name"] == "exercise"]
    names = [e["name"] for e in services.list_exercises("cardio")["exercises"]]
    assert activity["options"] == [(name, name) for name in names] and len(names) >= 15
