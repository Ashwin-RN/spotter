"""The web pages and web sign-in, with the provider seam replaced so nothing touches the network.

Clients use the site's https address so the Secure session cookie is sent back, and never follow
redirects (a redirect to the provider would otherwise be fed back into this app).
"""

import asyncio
import base64
import dataclasses
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

from app import config, db, formatting, links, mcp_server, rules, services, users, web, web_auth
from app.config import DEFAULT_SIGNUPS_PER_DAY, Settings
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


# ---------------------------------------------------------------- the landing's top half (pass B17)

# Where the landing's top half ends: the steps' card, which the next pass rebuilds. Everything from it to the end of
# main is the parent commit's, byte for byte (PARENT_TAILS).
LANDING_TOP_END = '<div class="card accent-border">'
NO_BREAK = " "
LANDING_TOP_WORDS = (
    "Spotter",
    "A fitness log you keep by talking to your assistant.",
    "Tell your assistant what you lifted, ate or weighed. Spotter stores it and works out your bests, trends and goal "
    "pace in code, and your assistant quotes those numbers.",
    "{button}",
    "Source code on GitHub",
    "What it looks like", "sample",
    "You", "Am I on track for my goals?",
    "Your assistant", "Yes, each goal is on track.",
    f"Body weight 77{NO_BREAK}kg by Tue 5 Jan 2027: on track. Needed -0.25{NO_BREAK}kg a week, so far -0.29{NO_BREAK}kg a week. "
    "At this pace it is reached on Sat 26 Dec.",
    f"Back Squat 120{NO_BREAK}kg by Tue 2 Feb 2027: on track. Needed +1.01{NO_BREAK}kg a week, so far +4.41{NO_BREAK}kg a week. "
    "At this pace it is reached on Thu 26 Nov.",
    f"Outdoor Run 5{NO_BREAK}km in 27:00 by Tue 5 Jan 2027: on track. Needed -19{NO_BREAK}seconds a week, so far "
    f"-30{NO_BREAK}seconds a week. At this pace it is reached on Sat 12 Dec.",
    "Sample data. The assistant asked Spotter for the goals and quoted them. The Goals page shows the same numbers.",
    "Before you start",
    "You need an AI assistant that can add a remote MCP server with sign-in.",
    "Each assistant's plan limits are in the README's table.",
    "{cap}",
)
# The white button by state (section L, Landing page): its words and address, or the line in its place.
BUTTONS = {"signed out": ("Sign in to start", "/sign-in"), "signed in": ("Open Today", "/today"),
           "no web sign-in": ("Sign-in on the web is not set up on this server yet.", None)}
NO_WEB = {"workos_client_id": None, "workos_api_key": None}


def landing_top(page: str) -> str:
    """The landing's main up to the steps' card: the top half this pass built."""
    main = page[page.index("<main"):]
    return main[:main.index(LANDING_TOP_END)]


def landing_tail(page: str) -> str:
    """The landing's main from the steps' card to its end: the next pass's half."""
    return page[page.index(LANDING_TOP_END):page.index("</main>")]


def plain_words(markup: str) -> str:
    """The text of some markup, tags taken out, entities decoded, ASCII whitespace squashed. A no-break space stays."""
    inline = re.sub(r"</?(?:a|span|strong|b|em|code|q)\b[^>]*>", "", markup)
    return re.sub(r"[ \t\r\n]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inline))).strip()


def landing_in(web_client, state: str, **overrides) -> str:
    client = web_client(**({**NO_WEB, **overrides} if state == "no web sign-in" else overrides))
    if state == "signed in":
        sign_in(client)
    r = client.get("/")
    assert r.status_code == 200
    return r.text


@pytest.mark.parametrize("state", sorted(BUTTONS))
def test_the_landings_top_half_in_its_order_and_words(web_client, state):
    page = landing_in(web_client, state)
    top = landing_top(page)
    blocks = re.findall(r"^<(?:div|section)\b[^>]*>", top, re.M)
    assert blocks == ['<div class="landing-hero">', '<section class="card showcase" aria-labelledby="looks-title">',
                      '<section class="card before" aria-labelledby="before-title">']
    want = " ".join(LANDING_TOP_WORDS).format(button=BUTTONS[state][0], cap="New accounts are capped at 50 a day.")
    assert plain_words(top) == want
    assert top.count("<h1>") == 1 and page.count("<h1") == 1
    hrefs = re.findall(r'<a\b[^>]*\bhref="([^"]*)"', top)
    button = [BUTTONS[state][1]] if BUTTONS[state][1] else []
    assert hrefs == button + [links.REPOSITORY, links.REPOSITORY + "#your-assistant"]
    if state != "signed in":  # signed out, the top half links to no signed-in page
        assert not [h for h in hrefs if h.startswith(("/today", "/day", "/progress", "/goals", "/foods", "/settings"))]
    assert re.findall(r"<img\b[^>]*>", top) == [
        '<img src="/static/landing-goals.svg" width="720" height="445" '
        'alt="A chat with your assistant beside the Goals card it quotes, on sample data">']


@pytest.mark.parametrize("state", sorted(BUTTONS))
def test_the_white_button_in_its_three_states(web_client, state):
    page = landing_in(web_client, state)
    cta = re.search(r'<div class="cta">(.*?)</div>', page, re.S).group(1)
    words, address = BUTTONS[state]
    buttons = re.findall(r'<a class="button primary" href="([^"]*)">([^<]*)</a>', cta)
    if address:
        assert buttons == [(address, words)] and 'class="notice"' not in cta
    else:
        assert buttons == [] and re.findall(r'<p class="notice">([^<]*)</p>', cta) == [words]
        assert "/sign-in" not in page
    assert f'<a class="plain-link" href="{links.REPOSITORY}">Source code on GitHub</a>' in cta
    assert re.sub(r"\s+", " ", plain_words(cta)) == f"{words} Source code on GitHub"


def test_every_repository_link_is_built_from_the_one_constant(web_client):
    assert re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", links.REPOSITORY)
    for state in sorted(BUTTONS):
        page = landing_in(web_client, state)
        repo = [h for h in re.findall(r'\bhref="([^"]*)"', page) if "github" in h.casefold()]
        assert repo == [links.REPOSITORY, links.REPOSITORY + "#your-assistant"], state
        assert web.README_TABLE == links.REPOSITORY + "#your-assistant"
    # No template and no other app file types the address: links.py holds it once.
    app_dir = web.HERE
    typed = [p.relative_to(app_dir).as_posix() for p in sorted(app_dir.rglob("*"))
             if p.suffix in (".py", ".html", ".js", ".css") and "github.com" in p.read_text(encoding="utf-8")]
    assert typed == ["links.py"]
    assert (app_dir / "links.py").read_text(encoding="utf-8").count("github.com") == 1


@pytest.mark.parametrize("cap, line", [
    (50, "New accounts are capped at 50 a day."),
    (7, "New accounts are capped at 7 a day."),
    (1, "New accounts are capped at 1 a day."),
    (1000, "New accounts are capped at 1,000 a day."),
    (0, "Spotter is not taking new accounts right now."),
], ids=["the default", "seven", "one", "a thousand", "closed"])
def test_before_you_start_says_the_cap_from_the_settings(web_client, cap, line):
    for state in ("signed out", "signed in"):
        page = landing_in(web_client, state, signups_per_day=cap)
        before = re.search(r'<section class="card before".*?</section>', page, re.S).group(0)
        items = [plain_words(li) for li in re.findall(r"<li>(.*?)</li>", before, re.S)]
        assert items == ["You need an AI assistant that can add a remote MCP server with sign-in.",
                         "Each assistant's plan limits are in the README's table.", line], state
        assert f'<a href="{links.REPOSITORY}#your-assistant">the README\'s table</a>' in before
        if cap == 0:
            assert "capped" not in page


def test_the_cap_is_read_at_each_render(client):
    assert "New accounts are capped at 50 a day." in client.get("/").text
    config.use_settings(dataclasses.replace(config.get_settings(), signups_per_day=3))
    assert "New accounts are capped at 3 a day." in client.get("/").text
    config.use_settings(dataclasses.replace(config.get_settings(), signups_per_day=0))
    page = client.get("/").text
    assert "Spotter is not taking new accounts right now." in page and "capped" not in page


CASES = web.HERE.parent / "evals" / "cases.json"


def handed_to_home(client, monkeypatch) -> dict:
    """What the landing's view hands its template, on one GET."""
    handed, real = {}, web.page
    monkeypatch.setattr(web, "page", lambda request, name, **kw: handed.update(kw) or real(request, name, **kw))
    assert client.get("/").status_code == 200
    return handed


def test_the_two_start_time_counts_equal_their_sources(client, monkeypatch):
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert isinstance(cases, list) and cases
    assert web.FUNCTION_COUNT == len(mcp_server.NEEDS)
    assert web.EVAL_CASES == len(cases) == web.eval_case_count()
    # Read once at start: a render reads neither again.
    def read_again(*args):
        raise AssertionError("the cases file was read at a render")

    monkeypatch.setattr(web, "eval_case_count", read_again)
    handed = handed_to_home(client, monkeypatch)
    assert (handed["function_count"], handed["eval_cases"], handed["signups_per_day"]) == \
        (len(mcp_server.NEEDS), len(cases), DEFAULT_SIGNUPS_PER_DAY)


@pytest.mark.parametrize("content", [None, b"", b"not json", b"{}", b'{"cases": [{"id": "a"}]}', b"[]", b"[1, 2]",
                                     b'[{"id": "a"}, "b"]', b"\xff\xfe[]"],
                         ids=["missing", "empty", "not json", "an object", "an object of cases", "no cases",
                              "not objects", "one not an object", "not utf-8"])
def test_a_cases_file_that_cannot_be_read_gives_no_count(tmp_path, content, logs):
    path = tmp_path / "cases.json"
    if content is not None:
        path.write_bytes(content)
    assert web.eval_case_count(path) is None
    assert "the landing leaves its line out" in logs.text


def test_a_cases_file_of_cases_gives_their_number(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"id": "a"}, {"id": "b"}, {"id": "c"}]), encoding="utf-8")
    assert web.eval_case_count(path) == 3


def test_with_no_case_count_the_landing_hands_none_and_still_renders(client, monkeypatch):
    monkeypatch.setattr(web, "EVAL_CASES", None)
    handed = handed_to_home(client, monkeypatch)
    assert handed["eval_cases"] is None and handed["function_count"] == len(mcp_server.NEEDS)


def test_no_count_is_typed_into_the_landing_template():
    source = (web.HERE / "templates" / "home.html").read_text(encoding="utf-8")
    for n in {len(mcp_server.NEEDS), len(json.loads(CASES.read_text(encoding="utf-8"))), DEFAULT_SIGNUPS_PER_DAY}:
        assert not re.search(rf"(?<![\d.,]){n}(?![\d.,])", source), n
    for name in ("function_count", "eval_cases", "signups_per_day"):
        assert not re.search(rf"\b{name}\s*(?:[-+*/]|%(?!\}})|\|\s*(?!show\b))", source), name  # drawn as given, or not yet


LANDING_DESCRIPTION = ("A fitness log you keep by talking to your assistant. Tell your assistant what you lifted, ate "
                       "or weighed. Spotter stores it and works out your bests, trends and goal pace in code, and your "
                       "assistant quotes those numbers.")
LANDING_HEAD_TAGS = [f'<meta name="description" content="{LANDING_DESCRIPTION}">',
                     '<meta property="og:title" content="Spotter">',
                     f'<meta property="og:description" content="{LANDING_DESCRIPTION}">',
                     '<meta property="og:type" content="website">',
                     f'<meta property="og:url" content="{SITE}/">']
_HEAD_TAG = re.compile(r'<meta (?:name="description"|property="og:[^"]*")[^>]*>')


def head_tags(page: str) -> list[str]:
    return _HEAD_TAG.findall(page[:page.index("</head>")])


def test_the_landing_carries_its_head_tags_and_no_other_page_does(client):
    pages = {"/ signed out": client.get("/").text, "a 404": client.get("/no-such-page").text,
             "Form expired": client.post("/sign-out").text}
    sign_in(client)
    today = today_of(only_user()).isoformat()
    pages["/ signed in"] = client.get("/").text
    for path in (f"/day/{today}", "/progress", "/progress/back-squat", "/goals", "/foods", "/settings",
                 "/settings/delete", "/day/abc"):
        pages[path] = client.get(path).text
    for name, page in pages.items():
        if name.startswith("/ "):
            assert head_tags(page) == LANDING_HEAD_TAGS, name
            assert "og:image" not in page
        else:
            assert head_tags(page) == [] and 'property="og:' not in page and '<meta name="description"' not in page, name


def test_without_the_site_address_the_landing_leaves_og_url_out(provider, seam):
    unconfigured = Settings(None, None, database_url=settings().database_url, **WEB)
    with TestClient(create_app(unconfigured, fetch=provider.fetch), base_url=SITE, follow_redirects=False) as client:
        page = client.get("/").text
    assert head_tags(page) == LANDING_HEAD_TAGS[:4]
    assert "og:url" not in page and "og:image" not in page


@pytest.mark.parametrize("state", sorted(BUTTONS))
def test_the_top_half_says_no_claude_no_whoami_and_no_privacy(web_client, state):
    page = landing_in(web_client, state)
    top = landing_top(page)
    alts = re.findall(r'\balt="([^"]*)"', top)
    for words in (plain_words(top), *alts, *head_tags(page)):
        assert not re.search(r"\bclaude\b", html.unescape(words), re.I), words
    assert "whoami" not in top.casefold() and "privacy" not in top.casefold()
    assert "whoami" not in " ".join(head_tags(page)).casefold()


# The steps' card and everything under it, as the parent commit (dac88d7) rendered it in each state, by its SHA-256
# and its length: the next pass rebuilds it, so this pass leaves it byte for byte. Delete with that pass.
PARENT_TAILS = {
    "signed out": ("ee4d135adb5164f602dacc90b5baf82b01efcec31cfa06bfd3444452db143183", 2394),
    "signed in": ("b2ca257b30205d8d66a9f29944fd891c90a5fe930de094703f05481e9499ab22", 1961),
    "no web sign-in": ("5c1b2ae07ebc8a4090648685e7c9f6180ef0afea771f425e4d7c3c658341e9b3", 2416),
    "no address": ("10cd78cddc64b8ca74ab3f43105a4ecbbfc2211aba8e6281f5c622186187f2a1", 1822),
}


@pytest.mark.parametrize("state", sorted(PARENT_TAILS))
def test_the_steps_and_everything_under_them_are_the_parents_byte_for_byte(web_client, provider, state):
    if state == "no address":
        unconfigured = Settings(None, None, database_url=settings().database_url, **WEB)
        with TestClient(create_app(unconfigured, fetch=provider.fetch), base_url=SITE, follow_redirects=False) as c:
            page = c.get("/").text
    else:
        page = landing_in(web_client, state)
    tail = landing_tail(page)
    assert (hashlib.sha256(tail.encode()).hexdigest(), len(tail)) == PARENT_TAILS[state]
    assert page.count(LANDING_TOP_END) == 1 and page.index(LANDING_TOP_END) > page.index('<section class="card before"')


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
    r = client.post("/settings", data={"time_zone": "Asia/Kolkata", "card": "zone", "csrf_token": csrf_of(client)})
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=time_zone#zone"
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
    # Nothing chosen has its own line (rev_b16 section 4): the person chose no zone that could go unrecognised.
    line = web.ZONE_ERROR_EMPTY if sent == "" else "That time zone was not recognised"
    assert r.status_code == 400 and line in r.text
    assert sent != "" or "That time zone was not recognised" not in r.text
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
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=unchanged#zone"
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
    """The Time zone card's form, posted with its own field."""
    return client.post("/settings", data={"time_zone": zone, "card": "zone", "csrf_token": csrf_of(client)})


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
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=time_zone#zone"
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
    assert '<select id="time-zone" name="time_zone" aria-invalid="true" aria-describedby="time-zone-error">' in r.text
    assert options(r.text)[0] == ("", True) and len(options(r.text)) > 400


def test_a_refused_browser_value_is_not_retried_by_the_server(client):
    sign_in(client)
    set_zone(client, "Not/AZone")
    assert only_user().time_zone is None  # the page still offers the script; its flag stops a second try


def test_settings_offers_a_list_with_the_current_zone_selected_and_no_typing(client):
    sign_in(client)
    set_zone(client, "Europe/London")
    page = client.get("/settings").text
    assert '<select id="time-zone" name="time_zone">' in page
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
    row = re.search(r'<tr><td class="lead"><a href="/progress/back-squat">Back Squat</a></td>(.*?)</tr>', page, re.S).group(1)
    current = services.get_progress(only_user().id)["lifts"][0]["current_estimated_max_kg"]
    assert formatting.show(current, "estimated_max", page=True) in row and "65\u00a0kg × 5" in row
    assert f'<a href="/day/{stocked["today"].isoformat()}">' in row
    latest = ([], "Latest", "78.4\u00a0kg", formatting.day(stocked["today"], stocked["today"]))
    assert latest in callouts_of(body_weight_card(page))
    assert '<svg class="chart" id="bodyweight-chart"' in page and "<title" in page


BODY_WEIGHT_WORDS = ("No weigh-ins yet.", "No weigh-ins in the last 28 days.",
                     "One weigh-in in the last 28 days. The chart starts from the second one.")


def weigh_card(client, *weigh_ins: tuple[int, str]) -> str:
    """Log each (days back, kg) through the service Claude uses, then return Progress' Body weight card."""
    sign_in(client)
    user = only_user()
    today = today_of(user)
    for back, kg in weigh_ins:
        result = services.log_bodyweight(user.id, {"kg": kg, "date": (today - timedelta(days=back)).isoformat()},
                                         "claude")
        assert "error" not in result, result
    return body_weight_card(client.get("/progress").text)


# The next card on a page, or the end of main: where a card read by its title stops.
_NEXT_CARD = re.compile(r'<(?:div|section) class="card[ "]|</main>')


def body_weight_card(page: str) -> str:
    """Progress' Body weight card: its markup from its h2, with or without an id, up to the next card or the
    end of main. It reads a card whatever its tag and whatever divs it holds."""
    start = re.search(r"<h2(?: [^>]*)?>Body weight</h2>", page)
    assert start, "no Body weight card"
    end = _NEXT_CARD.search(page, start.end())
    return page[start.end():end.start() if end else len(page)]


def callouts_of(card: str) -> list[tuple]:
    """Each callout in the card's dl.callouts, in order: (its classes, its label, its figure, the words under it)."""
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    out = []
    for dl in parse(card).find_all("dl", "callouts"):
        for box in dl.elements():
            dt, dd = box.elements()
            when = slot(dd, "when")
            out.append((box.classes, dt.text(), dd.text()[:-len(when)].strip() if when else dd.text(), when))
    return out


def expected_callouts(user_id: int) -> list[tuple]:
    """Section L's four callouts, from the reads: High, Latest and Low from get_days' weight, each as entered
    with its date, and Trend, the boxed one, get_trends' smoothed weight. With no weigh-in in the 28 days,
    Trend alone."""
    trends, grid = services.get_trends(user_id), services.get_days(user_id)
    weight, today = grid["weight"], grid["today"]
    trend = (["is-key"], "Trend", formatting.show(trends["bodyweight"]["smoothed"], "bodyweight_derived", page=True),
             "Smoothed")
    if weight is None:
        return [trend]
    box = lambda label, point: ([], label, formatting.show(point["kg"], "weigh_in", page=True),  # noqa: E731
                                formatting.day(point["date"], today))
    return [box("High", weight["high"]), box("Latest", weight["latest"]), trend, box("Low", weight["low"])]


def test_body_weight_with_every_weigh_in_older_than_28_days_says_so(client):
    card = weigh_card(client, (60, "81.2"), (50, "80.9"), (40, "80.6"))
    assert "No weigh-ins in the last 28 days." in card
    assert "The chart starts from the second" not in card and "appears from the second" not in card
    assert 'id="bodyweight-chart"' not in card and "Dots are weigh-ins" not in card
    # The Trend callout alone: no weigh-in of the 28 days to call High, Latest or Low.
    assert callouts_of(card) == expected_callouts(only_user().id) == [(["is-key"], "Trend", "81.1\u00a0kg", "Smoothed")]
    assert "7-day average" not in card and "Latest weigh-in" not in card


@pytest.mark.parametrize("weigh_ins", [
    # day 28 back is outside the 28 days, day 27 inside
    ((60, "81.2"), (28, "80.9"), (27, "80.6")),
    ((50, "81.2"), (40, "80.9"), (0, "80.2")),
])
def test_body_weight_with_one_weigh_in_in_the_28_days_waits_for_the_second(client, weigh_ins):
    card = weigh_card(client, *weigh_ins)
    assert "<p class=\"empty-line\">One weigh-in in the last 28 days. The chart starts from the second one.</p>" in card
    assert "No weigh-ins in the last 28 days." not in card and 'id="bodyweight-chart"' not in card
    # The callouts all show: the one weigh-in is High, Latest and Low.
    shown = callouts_of(card)
    assert shown == expected_callouts(only_user().id)
    assert [c[1] for c in shown] == ["High", "Latest", "Trend", "Low"]
    one = (f"{weigh_ins[-1][1]}\u00a0kg", formatting.day(today_of(only_user()) - timedelta(days=weigh_ins[-1][0]),
                                                         today_of(only_user())))
    assert [c[2:] for c in shown if c[1] != "Trend"] == [one] * 3
    assert "7-day average" not in card and "Latest weigh-in" not in card and "Dots are weigh-ins" not in card


@pytest.mark.parametrize("weigh_ins", [
    ((3, "81.0"), (0, "80.0")),
    # drawn, but none in the last 7 days
    ((60, "82.0"), (20, "81.0"), (10, "80.4")),
])
def test_body_weight_with_two_in_the_28_days_draws_the_chart(client, weigh_ins):
    card = weigh_card(client, *weigh_ins)
    assert '<svg class="chart" id="bodyweight-chart"' in card and "Dots are weigh-ins" in card
    assert not [words for words in BODY_WEIGHT_WORDS if words in card]
    assert callouts_of(card) == expected_callouts(only_user().id)
    assert "7-day average" not in card and "Latest weigh-in" not in card


def test_body_weight_with_no_weigh_in_points_to_today(client):
    card = weigh_card(client)
    assert 'No weigh-ins yet. Add one on <a href="/today">Today</a>.' in card
    assert not [words for words in BODY_WEIGHT_WORDS[1:] if words in card]
    assert "Latest" not in card and "7-day average" not in card and 'id="bodyweight-chart"' not in card


def test_one_lifts_progress_shows_its_chart_bests_and_sessions(client, stocked):
    r = client.get("/progress/back-squat")
    assert r.status_code == 200
    assert '<svg class="chart" id="progress-chart"' in r.text and "Estimated max over time: Back Squat" in r.text
    best = [t for t in lift_tiles(r.text) if t[1] == ["tile", "tile-best"]]
    assert [t[3:5] for t in best] == [("65", "kg × 5, best set")]
    assert "estimated max" in r.text and ", up from 70\u00a0kg" in r.text and "(was" not in r.text
    assert r.text.count("estimated max 70\u00a0kg") == 1 and "estimated max 75.8\u00a0kg" in r.text


def test_a_best_and_the_one_it_beat_widen_together_only_when_they_print_the_same(client, monkeypatch):
    """D5 on the lift page: an estimated max pair that prints the same at 1 dp shows both at 2 dp, together.
    A pair that prints apart stays at 1 dp, and a heaviest weight is as entered."""
    sign_in(client)
    monkeypatch.setattr(db, "utcnow", lambda: FIXED_NOW)
    bests = [{"date": "2026-11-09", "kind": "estimated_max", "value": 80.04, "previous": 80.01},
             {"date": "2026-11-09", "kind": "weight", "value": 80.25, "previous": 80.2},
             {"date": "2026-11-02", "kind": "estimated_max", "value": 81.7, "previous": 80.0}]
    lift = {"exercise": "Back Squat", "sessions": 2, "current_estimated_max_kg": 80.04, "last_trained": "2026-11-09",
            "best_set": {"date": "2026-11-09", "weight_kg": 70.0, "reps": 4, "estimated_max_kg": 80.04},
            "estimated_max_over_time": [{"date": "2026-11-02", "estimated_max_kg": 80.01},
                                        {"date": "2026-11-09", "estimated_max_kg": 80.04}],
            "bests": bests, "goals": []}
    monkeypatch.setattr(services, "get_progress", lambda *args: {"today": "2026-11-10", "lift": lift})
    page = client.get("/progress/back-squat").text
    shown = re.search(r'<h2 id="bests-title">Bests</h2>(.*?)</section>', page, re.S).group(1)
    assert "estimated max 80.04\u00a0kg, up from 80.01\u00a0kg" in shown
    assert "heaviest weight 80.25\u00a0kg, up from 80.2\u00a0kg" in shown
    assert "estimated max 81.7\u00a0kg, up from 80\u00a0kg" in shown
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
    # Today's glance block shows the 7-day average of 77.95 and 78.9, 78.4 kg, so the moved weigh-in's old figure
    # is looked for in the rest of the page.
    assert "77.95\u00a0kg" in day_of(client, two_days_ago)
    assert "78.4" not in without_glance(day_of(client, stocked["today"].isoformat()))


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
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=claude_can#assistant"
    after = {c.name: getattr(only_user(), c.key) for c in User.__table__.columns}
    assert after["claude_can"] == value
    differ = {k for k in after if after[k] != before[k]}
    assert differ <= {"claude_can", "updated_at"}
    page = client.get(r.headers["location"]).text
    assert f'name="claude_can" value="{value}" checked' in page
    assert '<p class="ok">Your assistant&#39;s access saved.</p>' in page


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
    assert "Only you can change this, here: your assistant is never offered this switch." in page
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
    """A titled card's markup after its h2, up to the card's own end. Every titled card is a section.card."""
    return re.search(rf'<h2 id="[^"]+">{title}</h2>(.*?)</section>', page, re.S).group(1)


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
    """One of div.col-side's cards, read as a tree: the section.card directly inside it whose first child is
    the h2 with that title."""
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    [side] = parse(page).find_all("div", "col-side")
    [card] = [c for c in side.elements() if c.is_("section", "card") and c.elements()
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
    assert "No saved foods yet." in page and 'action="/foods#save-food"' in page


def test_foods_lists_name_serving_kcal_and_times_logged(client, fed):
    page = client.get("/foods").text
    row = re.search(r'<tr>\s*<td class="lead">Roti</td>(.*?)</tr>', page, re.S).group(1)
    assert re.findall(r'<td(?: class="num")? data-label="([^"]+)">(.*?)</td>', row, re.S)[:3] == [
        ("Serving", "1 roti"), ("kcal", "120"), ("Times logged", "1")]
    assert f'href="/foods/{fed["roti"]}/edit"' in row and f'action="/foods/{fed["roti"]}/delete"' in row


def test_adding_a_food_from_the_page(client):
    sign_in(client)
    r = post(client, "/foods", name="Idli", serving_label="1 idli", kcal_per_serving="58", grams_per_serving="")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/foods?saved=saved_food#top"
    [food] = rows(SavedFood, only_user().id)
    assert (food.name, food.serving_label, food.kcal_per_serving, food.grams_per_serving) == ("Idli", "1 idli", 58, None)
    assert '<p class="ok">Saved food saved.</p>' in client.get(r.headers["location"]).text


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
    assert r.status_code == 303 and r.headers["location"] == SITE + "/foods?saved=saved_food"
    [food] = rows(SavedFood, only_user().id)
    assert (food.kcal_per_serving, food.grams_per_serving) == (110, 40)
    assert next(e.kcal for e in rows(FoodEntry, only_user().id) if e.id == fed["food"][0]) == 240  # history kept


def test_deleting_a_food_then_undo(client, fed):
    r = post(client, f"/foods/{fed['roti']}/delete")
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/foods?deleted=saved_food:{fed['roti']}"
    page = client.get(r.headers["location"]).text
    assert "Saved food deleted." in page and '<td class="lead">Roti</td>' not in page
    token = re.search(rf'action="/foods/{fed["roti"]}/restore" class="inline">\s*'
                      r'<input type="hidden" name="csrf_token" value="([^"]+)">', page).group(1)
    back = client.post(f"/foods/{fed['roti']}/restore", data={"csrf_token": token})
    assert back.status_code == 303 and back.headers["location"] == SITE + "/foods?restored=saved_food"
    assert '<td class="lead">Roti</td>' in client.get("/foods").text
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
    assert r.status_code == 303 and r.headers["location"] == SITE + "/settings?saved=targets#targets"
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
    ("calorie_target_kcal", "100", "Give the calories as a whole number with no comma, from 800 to 10,000 kcal"),
    ("target_sessions_per_week", "30", "Give lift sessions a week as a whole number from 0 to 14"),
    ("target_cardio_minutes_per_week", "abc", "Give cardio minutes a week as a plain whole number, such as 150, from 0 to "
                                              "3,000"),
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
    times = lambda: re.findall(r'<td class="num" data-label="Times logged">(.*?)</td>',  # noqa: E731
                               re.search(r'<tr>\s*<td class="lead">Roti</td>(.*?)</tr>', client.get("/foods").text,
                                         re.S).group(1), re.S)[0]
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
    assert location(r) == SITE + "/settings?saved=unchanged#targets"
    assert 'class="ok' not in client.get(location(r)).text
    # A real change keeps its own mark and its words.
    r = post(client, "/settings/targets", calorie_target_kcal="2000")
    assert location(r) == SITE + "/settings?saved=targets#targets"
    assert '<p class="ok">Targets saved.</p>' in client.get(location(r)).text
    r = post(client, f"/foods/{fed['roti']}/edit", serving_label="1 roti", kcal_per_serving="120")
    assert location(r) == SITE + "/foods?saved=unchanged"
    assert 'class="ok' not in client.get(location(r)).text


def test_the_marks_pages_have_words_for_stay_as_they_are(client, fed):
    # Foods' marks are the rebuilt pages' since pass B15: saved, deleted and restored, each naming its kind.
    assert location(post(client, "/foods", name="Idli", serving_label="1 idli", kcal_per_serving="40")) == \
        SITE + "/foods?saved=saved_food#top"
    assert location(post(client, f"/foods/{fed['roti']}/delete")) == SITE + f"/foods?deleted=saved_food:{fed['roti']}"
    assert location(post(client, f"/foods/{fed['roti']}/restore")) == SITE + "/foods?restored=saved_food"
    # Settings' since pass B16: each card's own mark, at that card's anchor.
    assert location(post(client, "/settings/claude-can", claude_can="read")) == \
        SITE + "/settings?saved=claude_can#assistant"
    assert location(post(client, "/settings", time_zone="Asia/Kolkata", card="zone")) == \
        SITE + "/settings?saved=time_zone#zone"


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
    assert location(r) == SITE + "/settings?saved=unchanged#assistant"
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
             '<section class="card hero"', '<div class="col-main">', '<h2 id="food-title">Food</h2>',
             '<div class="col-side">', '<h2 id="lifts-title">Lifts</h2>', '<h2 id="cardio-title">Cardio</h2>',
             '<h2 id="weigh-in-title">Weigh-in</h2>', '<h2 id="measurements-title">Measurements</h2>']
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
    # The glance block sits between the Today card and col-main, on today's page only (section L, Today).
    glance = [("div", "glance")] if address.startswith(today.isoformat()) else []
    assert [shape(c) for c in children] == [("div", "page-head"), ("p", "notice", "sample-banner"), ("p", "ok"),
                                            ("section", "card", "hero"), *glance, ("div", "col-main"),
                                            ("div", "col-side")]
    head, banner = children[0], children[1]
    assert not head.find_all(cls="sample-banner") and banner.parent is main
    main_col, side = children[-2], children[-1]
    assert card_titles(main_col) == ["Food"] and len(main_col.elements()) == 1
    assert card_titles(side) == ["Lifts", "Cardio", "Weigh-in", "Measurements"]
    assert len(side.elements()) == 4
    if glance:  # the tiles, the sheets they open, the Last 28 days card, then the chart pair, and nothing else
        inside = [shape(c) for c in children[4].elements()]
        assert inside[0] == ("div", "tiles") and inside[-2:] == [("section", "card", "daygrid"), ("div", "pair")]
        assert inside[1:-2] and set(inside[1:-2]) == {("div", "sheet")}
        assert [shape(c)[:3] for c in children[4].elements()[-1].elements()] == [("section", "card", "chart-card")] * 2


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
    assert re.search(r'<section class="card" aria-labelledby="food-title">\s*<div class="card-head">\s*'
                     r'<h2 id="food-title">Food</h2>\s*'
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
    assert '<h2 id="food-title">Food</h2>' in card
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
    assert home.is_("section", "card") and home.elements()[0].text() == card and home.parent.is_("div", "col-side")


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
            if not isinstance(left, str) and not isinstance(right, str) and left.tag in tr.INLINE \
                    and right.tag in tr.INLINE and left.text() and right.text():
                out.append(f"{left.text()!r} touches {right.text()!r}")
    return out


def test_each_side_card_reads_as_text_with_a_space_between_a_name_and_its_figure(client, stocked):
    stock_food(only_user())
    page = day_of(client, stocked["today"].isoformat())
    for title in ("Lifts", "Cardio", "Weigh-in", "Measurements"):
        card = side_card(page, title)
        assert glued(card) == [], title
    measured, weighed = '<h2 id="measurements-title">Measurements</h2>', '<h2 id="weigh-in-title">Weigh-in</h2>'
    pieces = [p.text for p in tr.html_text(page[page.index(measured):]) if p.kind != "attr"]
    assert "Waist 84.5 cm" in pieces and "Steps 9,500 steps" in pieces and "Sleep 7.25 h" in pieces
    assert not any(re.search(r"[A-Za-z]\d", t) for t in pieces), pieces
    weigh = [p.text for p in tr.html_text(page[page.index(weighed):page.index(measured)])]
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
    assert '<p id="edit-value-name">Resting heart rate (bpm)</p>' in edit
    goals = client.get("/goals?kind=measurement").text  # the Measurement form (pass B14: one kind at a time)
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
    assert '<thead><tr><th>Exercise</th><th class="num">Set</th><th class="num">Weight (kg)</th>' \
        '<th class="num">Reps</th></tr></thead>' in weighted
    assert '<tr><td>Back Squat</td><td class="num">1</td><td class="num">102.5</td><td class="num">5</td></tr>' \
        in weighted
    assert '<tr><td>Pull-Up</td><td class="num">1</td><td class="num"></td><td class="num">8</td></tr>' in weighted
    bare = str(module.sets_table([{"exercise": "Pull-Up", "set_number": 1, "weight_kg": 0, "reps": 8}]))
    assert "Weight" not in bare and bare.count("<td") == 3
    assert '<thead><tr><th>Exercise</th><th class="num">Set</th><th class="num">Reps</th></tr></thead>' in bare
    hostile = str(module.sets_table([{"exercise": '<b>"Squat"</b> & co', "set_number": 1, "weight_kg": 60, "reps": 5}]))
    assert "<tr><td>&lt;b&gt;&#34;Squat&#34;&lt;/b&gt; &amp; co</td>" in hostile and "<b>" not in hostile


# One refused edit per kind of control: an input, a typed 0, a select, a textarea and a date. The field it lands
# on, the page's own words there, and what comes back in the control (a select keeps no choice off its list).
EDIT_ERRORS = [
    ("bodyweight", {"kg": "abc"}, "kg", "That weight was not saved: give it in kg, from 20 to 400.", "abc"),
    ("bodyweight", {"kg": "0"}, "kg", "That weight was not saved: give it in kg, from 20 to 400.", "0"),
    ("bodyweight", {"kg": " abc "}, "kg", "That weight was not saved: give it in kg, from 20 to 400.", " abc "),
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
    assert '<body class="calm" data-time-zone="missing" id="top">' in r.text  # no zone saved on this account
    form = edit_form(r.text)
    [field] = [f for f in form.find_all("div", "field") if [c for c in f.elements() if c.attrs.get("name") == name]]
    assert field.classes == ["field", "has-error"]
    label, control, error = field.elements()
    assert label.tag == "label" and control.attrs["name"] == name
    assert control.attrs["aria-invalid"] == "true"
    # The error first, then the measurement's name line on Value.
    assert control.attrs["aria-describedby"] == f"{control.attrs['id']}-error" + (" edit-value-name" if name == "value"
                                                                                  else "")
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
    if kind in ("lift_session", "cardio_entry"):  # a note of two lines
        assert "error" not in services.edit_entry(only_user().id, kind, entry_id, {"notes": "two\nlines"}, "claude")
    drawn = {f["name"]: f["value"] or "" for f in controls_of(edit_of(client, kind, entry_id))}
    # A browser sends a textarea's line breaks as CRLF.
    sent = {name: value.replace("\n", "\r\n") for name, value in drawn.items()}
    if "notes" in drawn and kind != "food_entry":
        assert drawn["notes"] == "two\nlines" and sent["notes"] == "two\r\nlines"
    before = calls(only_user().id)
    assert location(post(client, edit, **sent)) == SITE + f"/day/{on}?saved=unchanged"
    assert calls(only_user().id) == before  # the form sent back as drawn writes nothing
    assert location(post(client, edit, **{**sent, **change})) == SITE + f"/day/{on}?saved={kind}"
    redrawn = {f["name"]: f["value"] or "" for f in controls_of(edit_of(client, kind, entry_id))}
    assert redrawn == {**drawn, **change}
    row = next(r for r in rows(EDIT_MODELS[kind], only_user().id) if r.id == entry_id)
    assert "\r" not in (getattr(row, "notes", None) or "")


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
    assert [(f["name"], f["tag"], f["inputmode"], f["required"], f["maxlength"], f["rows"]) for f in fields] \
        == EDIT_ATTRS[kind]


def test_the_edit_selects_offer_every_meal_and_every_activity(client, every_kind):
    [meal] = [f for f in controls_of(edit_of(client, "food_entry", every_kind["food_entry"])) if f["name"] == "meal"]
    assert meal["options"] == [("breakfast", "Breakfast"), ("lunch", "Lunch"), ("dinner", "Dinner"), ("snack", "Snack")]
    [activity] = [f for f in controls_of(edit_of(client, "cardio_entry", every_kind["cardio_entry"]))
                  if f["name"] == "exercise"]
    names = [e["name"] for e in services.list_exercises("cardio")["exercises"]]
    assert activity["options"] == [(name, name) for name in names] and len(names) >= 15


# ---------------------------------------------------------------- stage 6, step 4: Today's tiles and sheets

FIXED_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)  # the contract's fixed day, a Tuesday
NB = " "
CONTROLS = ("a", "button", "input", "select", "textarea")


def without_glance(page: str) -> str:
    """The day page with its glance block cut out: what the rest of the page shows."""
    if '<div class="glance">' not in page:
        return page
    return page[:page.index('<div class="glance">')] + page[page.index('<div class="col-main">'):]


def glance_node(page: str):
    """div.glance read as a tree, or None."""
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    found = parse(page).find_all("div", "glance")
    return found[0] if found else None


def slot(node, cls: str) -> str | None:
    found = node.find_all(cls=cls)
    return found[0].text() if found else None


def tiles_of(page: str) -> list[dict]:
    """Each element in div.tiles, in markup order: the controls, their twins and the plain tiles."""
    [grid] = glance_node(page).find_all("div", "tiles")
    return [{"tag": t.tag, "classes": t.classes, "attrs": t.attrs, "name": slot(t, "sr"), "value": slot(t, "tile-value"),
             "label": slot(t, "tile-label"), "note": slot(t, "tile-note"), "text": t.text(),
             "tags": t.find_all(cls="tag"), "node": t} for t in grid.elements()]


def sheets_of(page: str) -> dict:
    """Each sheet in the glance block, by its id, read as a person reads it."""
    out = {}
    for s in glance_node(page).find_all("div", "sheet"):
        tables = s.find_all("table")
        rows = [r for r in tables[0].find_all("tr") if r.elements()[0].tag == "td"] if tables else []
        panels = s.find_all("div", "panel")
        out[s.attrs["id"]] = {
            "node": s, "period": slot(s, "period"), "giant": slot(s, "giant"), "title": slot(s, "noun"),
            "sentence": slot(s, "sentence"),
            "panel": [e.text() if e.tag != "svg" else "svg" for e in panels[0].elements()] if panels else None,
            "svg": s.find_all("svg"),
            "head": [c.text() for c in tables[0].find_all("th")] if tables else None,
            "rows": [[(c.text(), c.classes) for c in r.elements()] for r in rows],
            "middle": [shape(e)[:2] for e in s.elements()[3:-1]]}
    return out


def sheet_frame_findings(sheet: dict, sid: str, link: str | None, tag: tuple[str, str] | None = None) -> list[str]:
    """The sheet's frame (section L, the components and Today): a popover dialog named by its title, the head
    with the grab and the bar, the close button the first control with its two attributes, the number and the
    title, then the caller's part, the Open Progress link last. No form, every table in its wrap.
    link None: a Progress sheet, which has no Open Progress link. tag: a (variant, words) tag of its own right
    after the number, as the Maintenance sheet's estimate tag."""
    node, out = sheet["node"], []
    if (node.attrs.get("popover"), node.attrs.get("role"), node.attrs.get("aria-labelledby")) != (
            "", "dialog", f"{sid}-title"):
        out.append(f"not a dialog popover named by its title: {node.attrs}")
    kids = node.elements()
    head = kids[0].elements() if kids else []
    if shape(kids[0]) != ("div", "sheet-head") or [shape(e) for e in head] != [("div", "grab"), ("div", "sheet-bar")] \
            or head[0].attrs.get("aria-hidden") != "true":
        out.append("no sheet-head holding the grab and the bar")
    bar = head[1].elements() if len(head) == 2 else []
    close = {"class": "close", "type": "button", "popovertarget": sid, "popovertargetaction": "hide", "autofocus": ""}
    if not bar or bar[0].tag != "button" or bar[0].attrs != close or bar[0].text() != "Close":
        out.append(f"the close button is not first in the bar: {bar[0].attrs if bar else None}")
    if len(bar) != 2 or shape(bar[1]) != ("p", "period"):
        out.append("no period after the close button")
    controls = [n for n in node.iter() if n.tag in CONTROLS]
    if not controls or not bar or controls[0] is not bar[0]:
        out.append("the close button is not the sheet's first control")
    tagged = [("span", "tag", f"tag-{tag[0]}")] if tag else []
    title = kids[2 + len(tagged)] if len(kids) > 2 + len(tagged) else kids[-1]
    if [shape(k) for k in kids[1:3 + len(tagged)]] != [("p", "giant"), *tagged, ("h2", "noun")] \
            or title.attrs.get("id") != f"{sid}-title":
        out.append("no number then its title")
    if tag and kids[2].text() != tag[1]:
        out.append(f"the tag after the number reads {kids[2].text()!r}")
    last = kids[-1]
    anchor = last.elements()
    if link is None:
        if node.find_all(cls="sheet-link") or any(a.text() == "Open Progress" for a in node.find_all("a")):
            out.append("a Progress sheet holds an Open Progress link")
    elif shape(last) != ("p", "sheet-link") or len(anchor) != 1 \
            or anchor[0].attrs != {"class": "button secondary", "href": link} or anchor[0].text() != "Open Progress":
        out.append("the sheet does not end with its secondary Open Progress link")
    if node.find_all("form") or node.find_all("input"):
        out.append("a sheet holds a form")
    out += [f"a table outside div.table-wrap: {t.text()[:30]!r}" for t in node.find_all("table")
            if not t.parent.is_("div", "table-wrap")]
    return out


@pytest.fixture
def fixed_day(client, monkeypatch):
    """The contract's fixed day: sample data, the clock pinned to Tuesday 10 November 2026."""
    sign_in(client)
    monkeypatch.setattr(db, "utcnow", lambda: FIXED_NOW)
    assert "error" not in services.add_sample_data(only_user().id, "web")
    return day_of(client, "2026-11-10")


def test_the_three_tiles_on_the_fixed_day_carry_section_ls_figures_and_words(fixed_day):
    """Section L's tile table, against the fixed-day values of the read table: 79.22 shown "79.2", a rate of
    -0.24, 2,216 over 7 days and 3 sessions. Each tile that opens a sheet is a button, then its twin link."""
    shown = [(t["tag"], t["classes"], t["name"], t["value"], t["label"], t["note"]) for t in tiles_of(fixed_day)]
    weight = ("Weight:", "79.2", "kg trend", f"-0.24{NB}kg a week")
    calories = ("Eaten a day:", "2,216", "kcal eaten a day", "last 7 days")
    training = ("Training:", "3", "lift sessions", "last 7 days")
    assert shown == [("button", ["tile", "tile-weight"], *weight), ("a", ["tile", "tile-weight", "tile-link"], *weight),
                     ("button", ["tile", "tile-calories"], *calories),
                     ("a", ["tile", "tile-calories", "tile-link"], *calories),
                     ("button", ["tile", "tile-training"], *training),
                     ("a", ["tile", "tile-training", "tile-link"], *training)]
    assert '<span class="tile-note">-0.24&nbsp;kg a week</span>' in fixed_day  # the held fix: a no-break space
    # Each tile reads in section L's order: its hidden name, the figure, the label, then the note.
    assert [[e.classes for e in t["node"].elements()] for t in tiles_of(fixed_day)] == [
        [["sr"], ["tile-value"], ["tile-label"], ["tile-note"]]] * 6


def test_each_tile_button_has_a_twin_link_to_its_part_of_progress_with_the_same_words(fixed_day):
    shown = tiles_of(fixed_day)
    buttons, twins = shown[0::2], shown[1::2]
    assert [b["attrs"] for b in buttons] == [
        {"class": f"tile tile-{v}", "type": "button", "popovertarget": f"sheet-{v}"}
        for v in ("weight", "calories", "training")]
    assert [a["attrs"] for a in twins] == [
        {"class": "tile tile-weight tile-link", "href": "/progress#body-weight"},
        {"class": "tile tile-calories tile-link", "href": "/progress#trends"},
        {"class": "tile tile-training tile-link", "href": "/progress#trends"}]
    for b, a in zip(buttons, twins):
        assert a["text"] == b["text"] and a["text"]
    assert all(t["tags"] == [] for t in shown)  # no tile holds a sample tag, nor any tag


def test_the_weight_sheet_on_the_fixed_day(fixed_day):
    sheet = sheets_of(fixed_day)["sheet-weight"]
    assert sheet_frame_findings(sheet, "sheet-weight", "/progress#body-weight") == []
    assert (sheet["period"], sheet["giant"], sheet["title"]) == ("Last 4 weeks", f"79.2{NB}kg", "Weight trend")
    assert sheet["sentence"] == (f"The trend moved -0.24{NB}kg a week from 27 Oct to 10 Nov. The 7-day average is "
                                 f"79{NB}kg, from 28 weigh-ins in 28 days.")
    assert sheet["middle"] == [("p", "sentence"), ("div", "panel"), ("div", "table-wrap")]
    assert sheet["panel"] == ["Smoothed weight", "svg"]
    [svg] = sheet["svg"]
    assert svg.attrs["id"] == "chart-weight-sheet" and svg.attrs["role"] == "img"
    assert [e.tag for e in svg.elements()][:2] == ["title", "desc"]
    assert sheet["head"] == ["Quantity", "Value", "Remark"]
    assert sheet["rows"] == [
        [("Smoothed trend", []), (f"79.2{NB}kg", ["num"]), ("Evens out day-to-day swings", ["remark"])],
        [("7-day average", []), (f"79{NB}kg", ["num"]), ("Last 7 days", ["remark"])],
        [("Rate", []), (f"-0.24{NB}kg a week", ["num"]), ("27 Oct to 10 Nov", ["remark"])],
        [("Weigh-ins", []), ("28", ["num"]), ("In the last 28 days", ["remark"])]]


def test_the_calories_sheet_on_the_fixed_day(fixed_day):
    sheet = sheets_of(fixed_day)["sheet-calories"]
    assert sheet_frame_findings(sheet, "sheet-calories", "/progress#trends") == []
    assert (sheet["period"], sheet["giant"], sheet["title"]) == ("Last 7 days", f"2,216{NB}kcal",
                                                                 "Eaten a day, on average")
    assert sheet["sentence"] == (f"Over the last 28 days it is 2,196{NB}kcal a day, and 41% of it is estimates. "
                                 "No calorie target is set.")
    assert sheet["middle"] == [("p", "sentence"), ("div", "panel"), ("div", "table-wrap")]
    assert sheet["panel"] == ["Each day", "svg"]
    [svg] = sheet["svg"]
    assert svg.attrs["id"] == "chart-calories-sheet" and svg.attrs["role"] == "img"
    assert sheet["head"] == ["Quantity", "Value", "Remark"]
    assert sheet["rows"] == [
        [("Eaten a day", []), (f"2,216{NB}kcal", ["num"]), ("Last 7 days, 7 logged", ["remark"])],
        [("Eaten a day", []), (f"2,196{NB}kcal", ["num"]), ("Last 28 days, 28 logged", ["remark"])],
        [("From estimates", []), ("41%", ["num"]), ("Last 28 days", ["remark"])],
        [("Calorie target", []), ("", ["num"]), ("Not set. Set one on Settings.", ["remark"])]]
    # The words are unchanged, and Settings is a link to the page that sets the target.
    remark = sheet["node"].find_all("table")[0].find_all("tr")[-1].elements()[-1]
    assert [(a.attrs, a.text()) for a in remark.find_all("a")] == [({"href": "/settings"}, "Settings")]
    assert '<td class="remark">Not set. Set one on <a href="/settings">Settings</a>.</td>' in fixed_day


def test_the_training_sheet_on_the_fixed_day(fixed_day):
    sheet = sheets_of(fixed_day)["sheet-training"]
    assert sheet_frame_findings(sheet, "sheet-training", "/progress#trends") == []
    assert (sheet["period"], sheet["giant"], sheet["title"]) == ("Last 7 days", "3", "Lift sessions")
    assert sheet["sentence"] == (f"3 a week over the last 4 weeks. Cardio came to 85{NB}min in the last 7 days, and "
                                 f"85{NB}min a week over 4 weeks. No session or cardio target is set.")
    assert sheet["middle"] == [("p", "sentence"), ("div", "table-wrap")] and sheet["svg"] == []
    assert sheet["head"] == ["Week ending", "Lift sessions", "Cardio (min)"]  # four weeks, oldest first, no Remark
    assert sheet["rows"] == [[(day, []), ("3", ["num"]), (minutes, ["num"])] for day, minutes in (
        ("Tue 20 Oct", "86"), ("Tue 27 Oct", "86"), ("Tue 3 Nov", "85"), ("Tue 10 Nov", "85"))]


def test_the_glance_reads_as_text_says_no_claude_and_holds_no_form_or_sample_tag(fixed_day):
    glance = glance_node(fixed_day)
    assert glued(glance) == []  # a name never touches its figure
    pieces = [p.text for p in tr.html_text(fixed_day[fixed_day.index('<div class="glance">'):
                                                     fixed_day.index('<div class="col-main">')])]
    assert pieces and not any(re.search(r"claude", t, re.I) for t in pieces)
    assert not glance.find_all("form") and not glance.find_all(cls="tag-sample") and not glance.find_all(cls="tag")
    assert fixed_day.count("tag-sample") == without_glance(fixed_day).count("tag-sample") > 0


def test_a_new_account_shows_the_empty_tiles_and_trainings_true_zero(client):
    sign_in(client)
    page = day_of(client, today_of(only_user()).isoformat())
    shown = tiles_of(page)
    assert [(t["tag"], t["classes"], t["value"], t["label"], t["note"]) for t in shown] == [
        ("div", ["tile", "tile-weight", "is-empty"], "No weigh-ins yet", None, None),
        ("div", ["tile", "tile-calories", "is-empty"], "No food in the last 28 days", None, None),
        ("button", ["tile", "tile-training"], "0", "lift sessions", "last 7 days"),
        ("a", ["tile", "tile-training", "tile-link"], "0", "lift sessions", "last 7 days")]
    assert [[e.classes for e in t["node"].elements()] for t in shown] == [[["sr"], ["tile-value"]]] * 2 + [
        [["sr"], ["tile-value"], ["tile-label"], ["tile-note"]]] * 2  # an empty tile: its name, then its words
    assert '<span class="tile-value">No <span class="nowrap">weigh-ins</span> yet</span>' in page
    assert [t["attrs"] for t in shown[:2]] == [{"class": "tile tile-weight is-empty"},
                                               {"class": "tile tile-calories is-empty"}]
    sheets = sheets_of(page)
    assert list(sheets) == ["sheet-training"] and "sheet-weight" not in page and "sheet-calories" not in page
    sheet = sheets["sheet-training"]
    assert sheet_frame_findings(sheet, "sheet-training", "/progress#trends") == []
    assert sheet["giant"] == "0" and sheet["sentence"] == (
        f"0 a week over the last 4 weeks. Cardio came to 0{NB}min in the last 7 days, and 0{NB}min a week over 4 "
        "weeks. No session or cardio target is set.")
    assert [[c[0] for c in r[1:]] for r in sheet["rows"]] == [["0", "0"]] * 4


def test_food_in_28_days_but_none_in_7_empties_the_calories_tile_and_opens_no_sheet(client):
    sign_in(client)
    ten_ago = (today_of(only_user()) - timedelta(days=10)).isoformat()
    assert "error" not in services.log_food(only_user().id, {"date": ten_ago, "entries": [
        {"description": "Dal", "kcal": 400}]}, "claude")
    page = day_of(client, today_of(only_user()).isoformat())
    [calories] = [t for t in tiles_of(page) if "tile-calories" in t["classes"]]
    assert (calories["tag"], calories["classes"], calories["value"], calories["label"], calories["note"]) == (
        "div", ["tile", "tile-calories", "is-empty"], "No food in the last 7 days", None, None)
    assert "sheet-calories" not in page and 'popovertarget="sheet-calories"' not in page
    assert list(sheets_of(page)) == ["sheet-training"]


@pytest.mark.parametrize("state, opens", [
    ("new", ["sheet-training"]),
    ("food", ["sheet-calories", "sheet-training"]),
    ("weighed", ["sheet-weight", "sheet-training"]),
    ("old food and an old weigh-in", ["sheet-weight", "sheet-training"]),
    ("both", ["sheet-weight", "sheet-calories", "sheet-training"]),
])
def test_which_tiles_open_a_sheet_state_by_state(client, state, opens):
    """Training always opens its sheet. Weight opens one with any weigh-in, even none in 28 days. Calories
    opens one only with food in the last 7 days. A tile without a sheet is an empty div, never a control."""
    sign_in(client)
    uid, today = only_user().id, today_of(only_user())
    ago = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    if state in ("food", "both"):
        services.log_food(uid, {"entries": [{"description": "Dal", "kcal": 400}]}, "claude")
    if state in ("weighed", "both"):
        services.log_bodyweight(uid, {"kg": "80"}, "claude")
    if state == "old food and an old weigh-in":
        assert "error" not in services.log_food(uid, {"date": ago(10), "entries": [{"description": "Dal", "kcal": 400}]},
                                                "claude")
        services.log_bodyweight(uid, {"kg": "80", "date": ago(40)}, "claude")
    page = day_of(client, today.isoformat())
    shown = tiles_of(page)
    assert [t["attrs"].get("popovertarget") for t in shown if t["tag"] == "button"] == opens
    assert list(sheets_of(page)) == opens
    assert [t["tag"] for t in shown].count("a") == len(opens) and len(shown) == 3 + len(opens)
    for t in shown:
        if t["tag"] == "div":
            assert "is-empty" in t["classes"] and t["label"] is None and "popovertarget" not in t["attrs"]


def test_one_weigh_in_has_no_rate_and_the_chart_says_why_it_is_empty(client):
    sign_in(client)
    services.log_bodyweight(only_user().id, {"kg": "80"}, "claude")
    page = day_of(client, today_of(only_user()).isoformat())
    assert tiles_of(page)[0]["note"] == "No weekly rate yet"
    sheet = sheets_of(page)["sheet-weight"]
    assert sheet_frame_findings(sheet, "sheet-weight", "/progress#body-weight") == []
    # The rate's sentence is left out, and the read's own words say why (web.rate_note).
    assert sheet["sentence"] == ("The weekly rate needs a weigh-in from 14 to 42 days ago. The 7-day average is "
                                 f"80{NB}kg, from 1 weigh-in in 28 days.")
    assert sheet["panel"] == ["Smoothed weight",
                              "One weigh-in in the last 28 days. The chart starts from the second one."]
    assert sheet["svg"] == []
    assert sheet["rows"][2] == [("Rate", []), ("", ["num"]), ("No weekly rate yet", ["remark"])]
    assert sheet["rows"][3] == [("Weigh-ins", []), ("1", ["num"]), ("In the last 28 days", ["remark"])]


def test_weigh_ins_but_none_in_28_days_still_open_the_sheet_and_its_chart_says_why(client):
    sign_in(client)
    uid, today = only_user().id, today_of(only_user())
    for n in (40, 45):
        services.log_bodyweight(uid, {"kg": "80", "date": (today - timedelta(days=n)).isoformat()}, "claude")
    page = day_of(client, today.isoformat())
    weight = tiles_of(page)[0]
    assert (weight["tag"], weight["value"], weight["label"], weight["note"]) == ("button", "80", "kg trend",
                                                                                 "No weekly rate yet")
    sheet = sheets_of(page)["sheet-weight"]
    assert sheet["sentence"] == ("The weekly rate needs a weigh-in in the last 7 days. The latest weigh-in is "
                                 f"80{NB}kg. No weigh-ins in the last 28 days.")  # the lead's words
    assert "from 0 weigh-ins" not in page
    assert sheet["panel"] == ["Smoothed weight", "No weigh-ins in the last 28 days."] and sheet["svg"] == []
    assert sheet["rows"][1] == [("Latest weigh-in", []), (f"80{NB}kg", ["num"]),
                                ("No weigh-in in the last 7 days", ["remark"])]
    assert sheet["rows"][3][1] == ("0", ["num"])


def test_two_recent_weigh_ins_draw_the_chart_with_no_rate(client):
    sign_in(client)
    uid, today = only_user().id, today_of(only_user())
    services.log_bodyweight(uid, {"kg": "80.4", "date": (today - timedelta(days=3)).isoformat()}, "claude")
    services.log_bodyweight(uid, {"kg": "80"}, "claude")
    sheet = sheets_of(day_of(client, today.isoformat()))["sheet-weight"]
    assert sheet["sentence"].startswith("The weekly rate needs a weigh-in from 14 to 42 days ago. The 7-day average is ")
    assert sheet["panel"] == ["Smoothed weight", "svg"] and len(sheet["svg"]) == 1


@pytest.mark.parametrize("sessions, cardio, words", [
    (None, None, " No session or cardio target is set."),
    (0, None, " The session target is 0 a week."),
    (None, 150, f" The cardio target is 150{NB}min a week."),
    (None, 0, f" The cardio target is 0{NB}min a week."),
    (3, 150,f" The session target is 3 a week. The cardio target is 150{NB}min a week."),
])
def test_the_training_sentence_names_each_target_that_is_set(client, sessions, cardio, words):
    sign_in(client)
    targets = {k: v for k, v in (("target_sessions_per_week", sessions), ("target_cardio_minutes_per_week", cardio))
               if v is not None}
    if targets:
        assert "error" not in services.update_settings(only_user().id, targets, "web")
    sheet = sheets_of(day_of(client, today_of(only_user()).isoformat()))["sheet-training"]
    assert sheet["sentence"] == (f"0 a week over the last 4 weeks. Cardio came to 0{NB}min in the last 7 days, and "
                                 f"0{NB}min a week over 4 weeks." + words)


def test_a_zero_rate_and_a_calorie_target_show_as_figures(client):
    """A rate of 0 is a rate, and a target shows in its row with a blank remark: zero is a value."""
    sign_in(client)
    uid, today = only_user().id, today_of(only_user())
    assert "error" not in services.update_settings(uid, {"calorie_target_kcal": 2000}, "web")
    for n in (21, 14, 7, 0):
        services.log_bodyweight(uid, {"kg": "80", "date": (today - timedelta(days=n)).isoformat()}, "claude")
    services.log_food(uid, {"entries": [{"description": "Thali", "kcal": 1000}]}, "claude")
    page = day_of(client, today.isoformat())
    assert tiles_of(page)[0]["note"] == f"0{NB}kg a week"
    weight = sheets_of(page)["sheet-weight"]
    window = formatting.days(today - timedelta(days=14), today, today)  # the last weigh-in 14 to 42 days back
    assert weight["sentence"] == (f"The trend moved 0{NB}kg a week from {window}. The 7-day average is 80{NB}kg, "
                                  "from 4 weigh-ins in 28 days.")
    assert weight["rows"][2] == [("Rate", []), (f"0{NB}kg a week", ["num"]), (window, ["remark"])]
    calories = sheets_of(page)["sheet-calories"]
    assert calories["sentence"] == (f"Over the last 28 days it is 1,000{NB}kcal a day, and 0% of it is estimates. "
                                    f"The calorie target is 2,000{NB}kcal a day.")
    assert calories["rows"][3] == [("Calorie target", []), (f"2,000{NB}kcal", ["num"]), ("", ["remark"])]


def test_a_past_day_has_no_glance_block(client, stocked):
    for page in (day_of(client, stocked["yesterday"]),
                 day_of(client, (stocked["today"] - timedelta(days=40)).isoformat())):
        assert glance_node(page) is None
        assert "popover" not in page and 'class="tile' not in page and 'class="sheet' not in page
    assert glance_node(day_of(client, stocked["today"].isoformat())) is not None


def test_a_day_after_today_has_no_glance_block(client):
    sign_in(client)
    user = only_user()
    services.update_settings(user.id, {"time_zone": "Pacific/Kiritimati"}, "web")  # UTC+14
    services.log_bodyweight(user.id, {"kg": "70"}, "claude")
    later = today_of(user).isoformat()
    services.update_settings(user.id, {"time_zone": "Pacific/Pago_Pago"}, "web")  # UTC-11
    page = day_of(client, later)
    assert '<main class="page today past">' in page and glance_node(page) is None and "popover" not in page
    assert glance_node(day_of(client, today_of(user).isoformat())) is not None


def test_a_form_sent_back_with_its_error_keeps_the_glance_block(client):
    sign_in(client)
    today = today_of(only_user()).isoformat()
    r = post(client, f"/day/{today}/weigh-in", kg="abc")
    assert r.status_code == 400 and glance_node(r.text) is not None and list(sheets_of(r.text)) == ["sheet-training"]


@pytest.mark.parametrize("read", ["get_trends", "get_days"])
def test_a_failed_read_draws_no_glance_block(client, monkeypatch, read):
    sign_in(client)
    monkeypatch.setattr(services, read, lambda *a: {"error": "no_account", "message": "x", "fix": "y"})
    page = day_of(client, today_of(only_user()).isoformat())
    assert glance_node(page) is None and '<div class="col-main">' in page


def test_glance_of_hands_back_the_reads_as_they_come(client, monkeypatch):
    sign_in(client)
    monkeypatch.setattr(db, "utcnow", lambda: FIXED_NOW)
    uid = only_user().id
    assert "error" not in services.add_sample_data(uid, "web")
    glance = asyncio.run(web.glance_of(uid))
    trends, days = services.get_trends(uid), services.get_days(uid)
    assert set(glance) == {"trends", "days", "basis", "rate_words", "weight_chart", "eaten_chart", "weight_small",
                           "eaten_small"}
    assert glance["trends"] == trends and glance["days"] == days
    assert (glance["basis"], glance["rate_words"]) == (web.weight_now_label(trends), web.rate_note(trends))
    assert glance["weight_chart"] == web.charts.weight_sheet_svg(trends["bodyweight"]["series"], date(2026, 11, 10))
    assert glance["eaten_chart"] == web.charts.eaten_sheet_svg(days["days"], date(2026, 11, 10))


def test_the_training_sentence_reads_this_week_and_the_four_week_figures_apart(client):
    """On the fixed day this week's figures and the 4-week ones print the same ("3" and "3", "85 min" twice),
    so a swap would pass there. Here two sessions 9 and 10 days ago and 40 min of cardio 10 days ago: 0 this
    week, 0.5 sessions and 10 min a week over 4 weeks. (One session would be 0.25, which get_trends rounds with
    Python's round to 0.2: the service's rounding, not the page's.)"""
    sign_in(client)
    uid, today = only_user().id, today_of(only_user())
    ten_ago = (today - timedelta(days=10)).isoformat()
    for n in (9, 10):
        assert "error" not in services.log_lifts(uid, {"date": (today - timedelta(days=n)).isoformat(), "sets": [
            {"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]}, "claude")
    assert "error" not in services.log_cardio(uid, {"exercise": "Outdoor Walk", "minutes": 40, "date": ten_ago},
                                              "claude")
    page = day_of(client, today.isoformat())
    sheet = sheets_of(page)["sheet-training"]
    assert sheet["giant"] == "0" and tiles_of(page)[-1]["value"] == "0"
    assert sheet["sentence"] == (f"0.5 a week over the last 4 weeks. Cardio came to 0{NB}min in the last 7 days, and "
                                 f"10{NB}min a week over 4 weeks. No session or cardio target is set.")
    # The week ending 7 days ago holds the 10th day back: the third of four, oldest first.
    assert [[c[0] for c in r] for r in sheet["rows"]][2] == [formatting.day(today - timedelta(days=7), today), "2",
                                                             "40"]



# ---------------------------------------------------------------- stage 6, step 4: the Last 28 days card and the pair

PILL_WORDS = {"is-lift": "lift session", "is-cardio": "cardio only", "is-none": "nothing trained"}


def daygrid_of(page: str) -> dict | None:
    """The Last 28 days card, read as a person reads it, or None."""
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    found = parse(page).find_all("section", "daygrid")
    if not found:
        return None
    [card] = found
    head = card.elements()[0]
    [weeks] = card.find_all("div", "week-head")
    [days] = card.find_all("ol", "days")
    [legend] = card.find_all("ul", "legend")
    return {"node": card, "kids": [shape(e) for e in card.elements()],
            "head": [(e.tag, e.classes, e.attrs.get("id"), e.text()) for e in head.elements()],
            "weeks": weeks, "letters": [s.text() for s in weeks.elements()],
            "items": [(li.tag, [shape(a) for a in li.elements()]) for li in days.elements()],
            "pills": [{"classes": a.classes, "href": a.attrs.get("href"), "words": a.text(),
                       "inside": [shape(k) for k in a.elements()]} for li in days.elements() for a in li.elements()],
            "legend": legend, "keys": [(li.elements()[0].classes, li.elements()[0].attrs.get("aria-hidden"), li.text())
                                       for li in legend.elements()]}


def pill_kind(day: dict) -> str:
    """The state get_days' day calls for: a lift session, else cardio, else nothing trained."""
    return "is-lift" if day["lift_sessions"] else ("is-cardio" if day["cardio_entries"] else "is-none")


def pair_of(page: str) -> list[dict]:
    """The chart pair's two cards, each read as a person reads it."""
    [pair] = glance_node(page).find_all("div", "pair")
    out = []
    for c in pair.elements():
        label = c.elements()[0]
        out.append({"node": c, "tag": c.tag, "classes": c.classes, "named": c.attrs.get("aria-labelledby"),
                    "kids": [shape(e) for e in c.elements()],
                    "label": (label.tag, label.classes, label.attrs.get("id"), label.text()),
                    "sub": slot(c, "card-sub"), "figure": slot(c, "figure"), "empty": slot(c, "empty-line"),
                    "values": c.find_all(cls="tile-value"), "svg": c.find_all("svg")})
    return out


def test_the_last_28_days_card_on_the_fixed_day(fixed_day):
    """Section L's card on the fixed day, against get_days: its head and aside, the weekday row, 28 pills oldest
    first, each a link to its day with its state and its hidden words, today on the last, the legend."""
    days = services.get_days(only_user().id)
    grid = daygrid_of(fixed_day)
    assert grid["node"].attrs == {"class": "card daygrid", "aria-labelledby": "days-title"}
    assert grid["kids"] == [("div", "card-head"), ("div", "week-head"), ("ol", "days"), ("ul", "legend")]
    assert days["lift_sessions_28d"] == 12  # the read table's fixed-day count
    assert grid["head"] == [("h2", [], "days-title", "Last 28 days"), ("span", ["aside"], None, "12 lift sessions")]
    # The weekday row: seven letters from weekday_letter, from the first day shown, hidden from reading.
    assert grid["weeks"].attrs == {"class": "week-head", "aria-hidden": "true"}
    assert grid["letters"] == [formatting.weekday_letter(d["date"]) for d in days["days"][:7]] == list("WTFSSMT")
    assert days["days"][0]["date"] == "2026-10-14" and days["days"][-1]["date"] == "2026-11-10"
    # 28 links, oldest first, each to its day, each li holding one a.pill holding only its hidden words.
    pills = grid["pills"]
    assert len(pills) == 28 and grid["items"] == [("li", [("a", *p["classes"])]) for p in pills]
    assert [p["href"] for p in pills] == [f"/day/{d['date']}" for d in days["days"]]
    assert all(p["inside"] == [("span", "sr")] for p in pills)
    # The pill classes on the fixed day: lifts Monday, Wednesday and Friday, cardio Tuesday, Thursday and
    # Saturday, none on Sunday, each as get_days calls it.
    by_weekday = {0: "is-lift", 1: "is-cardio", 2: "is-lift", 3: "is-cardio", 4: "is-lift", 5: "is-cardio", 6: "is-none"}
    for n, (p, d) in enumerate(zip(pills, days["days"])):
        state = by_weekday[date.fromisoformat(d["date"]).weekday()]
        assert state == pill_kind(d), d["date"]
        assert p["classes"] == ["pill", state] + (["is-today"] if n == 27 else []), d["date"]
    assert [p["href"] for p in pills if "is-today" in p["classes"]] == ["/day/2026-11-10"]
    # Each link's hidden words in its three forms, with ", today" after the date on the last only.
    assert [p["words"] for p in pills[-3:]] == ["Sun 8 Nov: nothing trained", "Mon 9 Nov: lift session",
                                               "Tue 10 Nov, today: cardio only"]
    for n, (p, d) in enumerate(zip(pills, days["days"])):
        today = ", today" if n == 27 else ""
        assert p["words"] == f"{formatting.day(d['date'], '2026-11-10')}{today}: {PILL_WORDS[pill_kind(d)]}"
    # The legend's four keys, each key hidden, its words beside it.
    assert grid["legend"].attrs == {"class": "legend", "aria-label": "Key"}
    assert grid["keys"] == [(["key", "is-lift"], "true", "Lift session"), (["key", "is-cardio"], "true", "Cardio only"),
                            (["key", "is-none"], "true", "Nothing trained"), (["key", "is-today"], "true", "Today")]
    assert glued(grid["node"]) == []
    pieces = [p.text for p in tr.html_text(fixed_day[fixed_day.index('<section class="card daygrid"'):
                                                     fixed_day.index('<div class="col-main">')])]
    assert pieces and not any(re.search(r"claude", t, re.I) for t in pieces)


def test_a_new_account_shows_28_days_of_nothing_and_0_lift_sessions(client):
    """Section L: a new account shows 28 is-none pills and "0 lift sessions". The 0 sits in one piece of text
    with its words, so it needs no true-zero allowance: the page's text rules pass as they are."""
    from .test_pages_text import page_findings  # noqa: PLC0415

    sign_in(client)
    today = today_of(only_user())
    page = day_of(client, today.isoformat())
    grid = daygrid_of(page)
    assert grid["head"][1] == ("span", ["aside"], None, "0 lift sessions")
    pills = grid["pills"]
    assert [p["classes"] for p in pills] == [["pill", "is-none"]] * 27 + [["pill", "is-none", "is-today"]]
    assert pills[-1]["words"] == f"{formatting.day(today, today)}, today: nothing trained"
    assert all(p["words"].endswith(": nothing trained") for p in pills)
    assert page_findings(f"/day/{today.isoformat()}", page, [], only_user().id) == []


def test_the_aside_counts_lift_sessions_not_days_and_says_one_in_the_singular(client):
    """One session: "1 lift session". A second on the same day: "2 lift sessions", though one day was trained,
    so the aside reads lift_sessions_28d, never lift_days_28d."""
    sign_in(client)
    uid, today = only_user().id, today_of(only_user())
    on = (today - timedelta(days=3)).isoformat()
    assert "error" not in services.log_lifts(uid, {"date": on, "sets": [
        {"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]}, "claude")
    assert daygrid_of(day_of(client, today.isoformat()))["head"][1][3] == "1 lift session"
    assert "error" not in services.log_lifts(uid, {"date": on, "sets": [
        {"exercise": "Bench Press", "weight_kg": 40, "reps": 5}]}, "claude")
    days = services.get_days(uid)
    assert (days["lift_sessions_28d"], days["lift_days_28d"]) == (2, 1)
    grid = daygrid_of(day_of(client, today.isoformat()))
    assert grid["head"][1][3] == "2 lift sessions"
    assert [p["classes"] for p in grid["pills"]].count(["pill", "is-lift"]) == 1


def test_a_day_with_a_lift_and_cardio_is_a_lift_day_and_cardio_alone_is_cardio_only(client):
    sign_in(client)
    uid, today = only_user().id, today_of(only_user())
    ago = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    for on in (ago(0), ago(5)):
        assert "error" not in services.log_lifts(uid, {"date": on, "sets": [
            {"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]}, "claude")
    for on in (ago(0), ago(2)):
        assert "error" not in services.log_cardio(uid, {"exercise": "Outdoor Walk", "minutes": 30, "date": on},
                                                  "claude")
    pills = daygrid_of(day_of(client, today.isoformat()))["pills"]
    assert pills[-1]["classes"] == ["pill", "is-lift", "is-today"]
    assert pills[-1]["words"] == f"{formatting.day(today, today)}, today: lift session"
    assert pills[-3]["classes"] == ["pill", "is-cardio"] and pills[-3]["words"].endswith(": cardio only")
    assert pills[-6]["classes"] == ["pill", "is-lift"] and pills[-6]["words"].endswith(": lift session")
    assert sum("is-none" in p["classes"] for p in pills) == 25


WEEKDAY_PINS = [FIXED_NOW + timedelta(days=n) for n in range(7)] + [datetime(2027, 1, 2, 12, 0, tzinfo=timezone.utc)]


@pytest.mark.parametrize("now", WEEKDAY_PINS, ids=lambda t: t.date().isoformat())
def test_the_weekday_row_starts_at_the_weekday_of_the_first_day_shown(client, monkeypatch, now):
    """Seven days in a row, so the row starts on each weekday once, and 2 January, when the first days shown
    are in the year before and their words carry it. Each letter is page text the sweeps read, so the page's
    text rules run on each. A new account with no zone: today is the UTC date."""
    from .test_pages_text import page_findings  # noqa: PLC0415

    sign_in(client)
    monkeypatch.setattr(db, "utcnow", lambda: now)
    today = now.date()
    page = day_of(client, today.isoformat())
    grid = daygrid_of(page)
    first = today - timedelta(days=27)
    assert grid["pills"][0]["href"] == f"/day/{first.isoformat()}"
    assert grid["letters"] == [formatting.weekday_letter(first + timedelta(days=n)) for n in range(7)]
    assert grid["letters"][0] == formatting.weekday(first)[0]
    assert grid["pills"][0]["words"] == f"{formatting.day(first, today)}: nothing trained"
    assert grid["pills"][-1]["words"] == f"{formatting.day(today, today)}, today: nothing trained"
    if first.year != today.year:  # across a year end: the year shows on the days before it, never on today
        assert grid["pills"][0]["words"] == "Sun 6 Dec 2026: nothing trained"
        assert grid["pills"][-1]["words"] == "Sat 2 Jan, today: nothing trained"
    assert glued(grid["node"]) == []
    assert page_findings(f"/day/{today.isoformat()}", page, [], only_user().id) == []


def test_the_chart_pair_on_the_fixed_day(fixed_day):
    """Two chart cards: Weight, its 4-week trend and the smoothed weight, then Eaten a day, its 7-day average.
    Each with its compact chart from charts.py, as it is."""
    uid = only_user().id
    trends, days = services.get_trends(uid), services.get_days(uid)
    weight, eaten = pair_of(fixed_day)
    assert (weight["tag"], weight["classes"], weight["named"]) == ("section", ["card", "chart-card"], "weight-card-title")
    assert weight["label"] == ("h2", ["card-label", "tile-weight"], "weight-card-title", "Weight")
    assert (weight["sub"], weight["figure"], weight["empty"]) == ("Trend, 4 weeks", f"79.2{NB}kg", None)
    assert formatting.show(trends["bodyweight"]["smoothed"], "bodyweight_derived", unit=False) == "79.2"
    assert weight["kids"] == [("h2", "card-label", "tile-weight"), ("p", "card-sub"), ("p", "figure"),
                              ("svg", "chart", "small")]
    assert weight["svg"][0].attrs["id"] == "chart-weight-4w"
    assert str(web.charts.weight_small_svg(trends["bodyweight"]["series"], date(2026, 11, 10))) in fixed_day
    assert (eaten["tag"], eaten["classes"], eaten["named"]) == ("section", ["card", "chart-card"], "eaten-card-title")
    assert eaten["label"] == ("h2", ["card-label", "tile-calories"], "eaten-card-title", "Eaten a day")
    assert (eaten["sub"], eaten["figure"], eaten["empty"]) == ("Average, 7 days", f"2,216{NB}kcal", None)
    assert formatting.show(trends["calories"]["avg_7d"], "kcal", unit=False) == "2,216"
    assert eaten["kids"] == [("h2", "card-label", "tile-calories"), ("p", "card-sub"), ("p", "figure"),
                             ("svg", "chart", "small")]
    assert eaten["svg"][0].attrs["id"] == "chart-calories-7d"
    assert str(web.charts.eaten_small_svg(days["days"], date(2026, 11, 10))) in fixed_day
    assert '<p class="figure"><span class="tile-value">79.2&nbsp;<span class="unit">kg</span></span></p>' in fixed_day
    assert not eaten["svg"][0].find_all("line", "goal")  # the sample sets no calorie target


def log_state(state: str, uid: int, today) -> None:
    ago = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    if state == "one weigh-in":
        services.log_bodyweight(uid, {"kg": "80"}, "claude")
    if state == "weigh-ins none in 28 days":
        for n in (40, 45):
            services.log_bodyweight(uid, {"kg": "80", "date": ago(n)}, "claude")
    if state == "food in 28 days none in 7":
        assert "error" not in services.log_food(uid, {"date": ago(10), "entries": [{"description": "Dal", "kcal": 400}]},
                                                "claude")
    if state == "two weigh-ins and food":
        services.log_bodyweight(uid, {"kg": "80.4", "date": ago(3)}, "claude")
        services.log_bodyweight(uid, {"kg": "80"}, "claude")
        services.log_food(uid, {"entries": [{"description": "Dal", "kcal": 400}]}, "claude")


NO_WEIGHT = "No weigh-ins in the last 28 days."
ONE_WEIGHT = "One weigh-in in the last 28 days. The chart starts from the second one."
NO_FOOD = "No food logged in the last 7 days."


@pytest.mark.parametrize("state, words", [
    ("new", (NO_WEIGHT, NO_FOOD)),
    ("one weigh-in", (ONE_WEIGHT, NO_FOOD)),
    ("weigh-ins none in 28 days", (NO_WEIGHT, NO_FOOD)),
    ("food in 28 days none in 7", (NO_WEIGHT, NO_FOOD)),
    ("two weigh-ins and food", (None, None)),
])
def test_each_chart_card_with_no_chart_is_empty_and_says_the_charts_words(client, state, words):
    """The charts table's empty words: keyed on the weigh-ins in the 28 days, none or one, and on food in the 7
    days. An empty card is is-empty, its label and sub, then the words in place of the figure and the chart."""
    sign_in(client)
    uid, today = only_user().id, today_of(only_user())
    log_state(state, uid, today)
    cards = pair_of(day_of(client, today.isoformat()))
    assert [c["label"][3] for c in cards] == ["Weight", "Eaten a day"]
    for card, said in zip(cards, words):
        if said is None:
            assert card["classes"] == ["card", "chart-card"] and card["empty"] is None and len(card["svg"]) == 1
            continue
        assert card["classes"] == ["card", "chart-card", "is-empty"], (state, card["label"])
        assert card["kids"] == [card["kids"][0], ("p", "card-sub"), ("p", "empty-line")]
        assert (card["empty"], card["figure"], card["values"], card["svg"]) == (said, None, [], [])


def test_a_calorie_target_draws_its_line_in_the_eaten_chart(client):
    sign_in(client)
    uid, today = only_user().id, today_of(only_user())
    assert "error" not in services.update_settings(uid, {"calorie_target_kcal": 2000}, "web")
    services.log_food(uid, {"entries": [{"description": "Thali", "kcal": 1000}]}, "claude")
    page = day_of(client, today.isoformat())
    [svg] = pair_of(page)[1]["svg"]
    assert len(svg.find_all("line", "goal")) == 1
    assert str(web.charts.eaten_small_svg(services.get_days(uid)["days"], today, 2000)) in page
    assert pair_of(page)[1]["figure"] == f"1,000{NB}kcal"


def test_food_at_0_kcal_still_draws_the_eaten_card_with_its_figure(client):
    """Food at 0 kcal is food: the chart and the 7-day average agree, so the card is never empty with a figure."""
    sign_in(client)
    services.log_food(only_user().id, {"entries": [{"description": "Black coffee", "kcal": 0}]}, "claude")
    card = pair_of(day_of(client, today_of(only_user()).isoformat()))[1]
    assert card["classes"] == ["card", "chart-card"] and card["figure"] == f"0{NB}kcal" and len(card["svg"]) == 1


def test_food_dated_after_today_counts_as_todays_in_the_eaten_card(client):
    """Food left on a day after today by a change of time zone counts as today's in both reads."""
    sign_in(client)
    user = only_user()
    services.update_settings(user.id, {"time_zone": "Pacific/Kiritimati"}, "web")  # UTC+14
    services.log_food(user.id, {"entries": [{"description": "Dal", "kcal": 400}]}, "claude")
    services.update_settings(user.id, {"time_zone": "Pacific/Pago_Pago"}, "web")  # UTC-11: the food is ahead
    assert services.get_trends(user.id)["calories"]["avg_7d"] == 400
    card = pair_of(day_of(client, today_of(only_user()).isoformat()))[1]
    assert card["classes"] == ["card", "chart-card"] and card["figure"] == f"400{NB}kcal" and len(card["svg"]) == 1


def test_a_past_day_and_a_day_after_today_have_neither_the_grid_nor_the_pair(client, stocked):
    user = only_user()
    pages = [day_of(client, stocked["yesterday"]), day_of(client, (stocked["today"] - timedelta(days=40)).isoformat())]
    services.update_settings(user.id, {"time_zone": "Pacific/Kiritimati"}, "web")
    services.log_bodyweight(user.id, {"kg": "70"}, "claude")
    later = today_of(only_user()).isoformat()
    services.update_settings(user.id, {"time_zone": "Pacific/Pago_Pago"}, "web")
    pages.append(day_of(client, later))
    assert '<main class="page today past">' in pages[-1]
    for page in pages:
        assert daygrid_of(page) is None
        for mark in ("daygrid", "week-head", 'class="days"', "pill", "legend", 'class="pair"', "chart-card",
                     "chart-weight-4w", "chart-calories-7d"):
            assert mark not in page, mark
    assert daygrid_of(day_of(client, today_of(only_user()).isoformat())) is not None


@pytest.mark.parametrize("state", ["new", "sample", "error", "add", "yesterday"])
def test_every_titled_card_on_today_is_a_section_named_by_its_own_h2(client, monkeypatch, state):
    """Section L's card rule: a titled card is a section.card whose aria-labelledby names its own h2. The Today
    card is named by its title or its label, inside it. A card with no title stays a bare div.card. No id
    repeats on the page."""
    from .test_pages_text import parse  # noqa: PLC0415

    sign_in(client)
    monkeypatch.setattr(db, "utcnow", lambda: FIXED_NOW)
    if state != "new":
        assert "error" not in services.add_sample_data(only_user().id, "web")
    on = "2026-11-09" if state == "yesterday" else "2026-11-10"
    if state == "error":
        r = post(client, f"/day/{on}/weigh-in", kg="abc")
        assert r.status_code == 400
        page = r.text
    else:
        page = day_of(client, on + ("?add=food" if state == "add" else ""))
    tree = parse(page)
    titled = []
    for card in tree.find_all(cls="card"):
        heads = card.find_all("h2")
        if "hero" in card.classes:  # titled by its h2 on a new account, else by its eyebrow p
            named = card.attrs["aria-labelledby"]
            assert card.tag == "section" and named in ("today-card-title", "today-card-label")
            assert named in {n.attrs.get("id") for n in card.iter()}
            titled.append("Today card")
            continue
        if not heads:
            assert card.tag == "div", card.classes
            continue
        assert card.tag == "section", heads[0].text()
        named = card.attrs["aria-labelledby"]
        assert named == heads[0].attrs["id"], heads[0].text()
        assert heads[0].parent is card or heads[0].parent.parent is card, heads[0].text()
        titled.append(heads[0].text())
    glance = ["Last 28 days", "Weight", "Eaten a day"] if state != "yesterday" else []
    assert titled == ["Today card", *glance, "Food", "Lifts", "Cardio", "Weigh-in", "Measurements"]
    ids = [n.attrs["id"] for n in tree.iter() if n.attrs.get("id")]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    assert not [n for n in tree.find_all("div", "sheet") if "card" in n.classes]


def test_a_day_logged_at_0_kcal_is_food_its_tile_reads_0_and_its_sheet_opens(client):
    """A logged day can total 0 kcal (section L): the Calories tile reads "0" with its label and note, its sheet
    opens, and the sheet chart writes the day's figure "0" over no bar. The compact Eaten chart writes no figure,
    only the weekday letters, so it draws no bare 0."""
    sign_in(client)
    today = today_of(only_user())
    assert "error" not in services.log_food(only_user().id, {"entries": [{"description": "Black coffee", "kcal": 0}]},
                                            "claude")
    page = day_of(client, today.isoformat())
    [button, twin] = [t for t in tiles_of(page) if "tile-calories" in t["classes"]]
    assert (button["tag"], button["value"], button["label"], button["note"]) == ("button", "0", "kcal eaten a day",
                                                                                 "last 7 days")
    assert button["attrs"]["popovertarget"] == "sheet-calories" and twin["tag"] == "a"
    sheet = sheets_of(page)["sheet-calories"]
    assert sheet_frame_findings(sheet, "sheet-calories", "/progress#trends") == []
    assert sheet["giant"] == f"0{NB}kcal" and sheet["panel"] == ["Each day", "svg"]
    [figure] = [t for t in sheet["svg"][0].find_all("text") if "value" in t.classes]
    assert figure.text() == "0" and not sheet["svg"][0].find_all("rect")
    small = re.search(r'<svg class="chart small" id="chart-calories-7d".*?</svg>', page, re.S).group(0)
    assert ">0</text>" not in small and "value" not in small


# ---------------------------------------------------------------- stage 6, step 4: the edit pages' fix round

@pytest.mark.parametrize("kind", ["lift_session", "cardio_entry"])
def test_a_note_posted_with_crlf_or_a_lone_cr_is_stored_with_lf(client, every_kind, kind):
    entry_id = every_kind[kind]
    drawn = {f["name"]: f["value"] or "" for f in controls_of(edit_of(client, kind, entry_id))}
    r = post(client, f"/entries/{kind}/{entry_id}/edit", **{**drawn, "notes": "first\r\nsecond\rthird"})
    assert location(r) == SITE + f"/day/{every_kind['today']}?saved={kind}"
    row = next(x for x in rows(EDIT_MODELS[kind], only_user().id) if x.id == entry_id)
    assert row.notes == "first\nsecond\nthird"
    assert next(f["value"] for f in controls_of(edit_of(client, kind, entry_id)) if f["name"] == "notes") \
        == "first\nsecond\nthird"


def test_a_refused_note_comes_back_with_its_line_breaks(client, every_kind):
    entry_id = every_kind["lift_session"]
    typed = "  " + "x" * 2000 + "\r\nmore  "
    r = post(client, f"/entries/lift_session/{entry_id}/edit", notes=typed, date=every_kind["today"])
    assert r.status_code == 400
    shown = next(f["value"] for f in controls_of(r.text) if f["name"] == "notes")
    assert shown == "  " + "x" * 2000 + "\nmore  "  # the same line break, read as one newline, and nothing stripped
    assert next(x for x in rows(LiftSession, only_user().id) if x.id == entry_id).notes == "felt strong"


def test_no_add_form_on_today_takes_a_note(client):
    """Today's fold-outs draw no notes field, and the cardio post takes none: a note sent there by hand is not
    stored, so no line break from Today can be stored as CRLF."""
    sign_in(client)
    on = today_of(only_user()).isoformat()
    page = day_of(client, on)
    assert 'name="notes"' not in page and "<textarea" not in page
    r = post(client, f"/day/{on}/cardio", exercise="Outdoor Walk", minutes="30", notes="a\r\nb")
    assert location(r) == SITE + f"/day/{on}?saved=cardio_entry"
    [row] = rows(CardioEntry, only_user().id)
    assert row.notes in (None, "")


def test_the_back_link_names_a_day_last_year_and_a_day_after_today_by_its_date(client):
    """The edge account holds a steps measurement on a day after today and one 366 days back. The back link and
    Cancel on each read formatting.day(on, today): the year on the old one, never Today on the later one."""
    from .test_pages_text import ACCOUNTS  # noqa: PLC0415 - test_pages_text imports this file

    sign_in(client)
    user = only_user()
    ACCOUNTS["edge"](user.id)
    today = today_of(user)
    days = {m.date: m.id for m in rows(Measurement, user.id) if m.deleted_at is None}
    later = [d for d in days if d > today]
    old = [d for d in days if d.year != today.year]
    assert later and old, sorted(days)
    for on in (later[0], old[0]):
        page = edit_of(client, "measurement", days[on])
        words = formatting.day(on, today)
        assert f'<a class="back" href="/day/{on.isoformat()}">{words}</a>' in page
        assert f'<a class="action" href="/day/{on.isoformat()}">Cancel</a>' in page
        assert words != "Today"
    assert str(old[0].year) in formatting.day(old[0], today)


@pytest.mark.parametrize("kind, line", [("waist", "Waist (cm)"), ("steps", "Steps"), ("sleep_hours", "Sleep (h)"),
                                        ("resting_hr", "Resting heart rate (bpm)"), ("water_ml", "Water (ml)")])
def test_the_measurement_line_reads_kind_with_unit_and_describes_the_value(client, kind, line):
    sign_in(client)
    made = services.log_measurement(only_user().id, {"kind": kind, "value": str(rules.MEASUREMENTS[kind][1])}, "claude")
    assert "error" not in made, made
    page = edit_of(client, "measurement", made["id"])
    form = edit_form(page)
    named = form.elements()[1]
    assert named.tag == "p" and named.attrs == {"id": "edit-value-name"} and named.text() == line == \
        web.kind_with_unit(kind)
    assert "Steps (steps)" not in page
    [value] = [n for n in form.iter() if n.attrs.get("name") == "value"]
    assert value.attrs["aria-describedby"] == "edit-value-name"
    # Sent back with its error: the error first, then the line, as field orders an error and a hint.
    r = post(client, f"/entries/measurement/{made['id']}/edit", value="abc", date=today_of(only_user()).isoformat())
    assert r.status_code == 400
    [value] = [n for n in edit_form(r.text).iter() if n.attrs.get("name") == "value"]
    assert value.attrs["aria-describedby"] == "edit-value-error edit-value-name"
    assert value.attrs["aria-invalid"] == "true"


@pytest.mark.parametrize("kind", sorted(rules.MEASUREMENTS))
def test_a_whole_number_measurement_takes_a_numeric_keyboard(client, kind):
    """rules.MEASUREMENTS says which kinds take a whole number only: steps, resting heart rate, water."""
    sign_in(client)
    made = services.log_measurement(only_user().id, {"kind": kind, "value": str(rules.MEASUREMENTS[kind][1])}, "claude")
    [value] = [f for f in controls_of(edit_of(client, "measurement", made["id"])) if f["name"] == "value"]
    whole = rules.MEASUREMENTS[kind][3]
    assert whole is (kind in ("steps", "resting_hr", "water_ml"))
    assert value["inputmode"] == ("numeric" if whole else "decimal")


def test_the_field_names_more_ids_after_its_error_and_its_hint():
    both = parts_field("v", "value", "Value", "Bad.", "x", hint="A hint.", describedby="v-name")
    assert 'aria-describedby="v-error v-hint v-name"' in both
    assert 'aria-describedby="v-name"' in parts_field("v", "value", "Value", None, "x", describedby="v-name")
    assert 'aria-describedby="v-error v-name"' in parts_field("v", "value", "Value", "Bad.", "x", describedby="v-name")
    assert 'aria-describedby="v-hint v-name"' in parts_field("v", "value", "Value", None, "x", hint="A hint.",
                                                             describedby="v-name")  # a hint and no error
    assert "aria-describedby" not in parts_field("v", "value", "Value", None, "x")


# ---------------------------------------------------------------- the glance's two reads across midnight (B7 fix)

BEFORE_MIDNIGHT = datetime(2026, 11, 10, 23, 59, 59, tzinfo=timezone.utc)  # a person with no zone: UTC's date
AFTER_MIDNIGHT = datetime(2026, 11, 11, 0, 0, 1, tzinfo=timezone.utc)


def reads_across_midnight(monkeypatch, trends_at, days_at) -> dict:
    """get_trends and get_days, each read with the clock at the time trends_at() and days_at() name, and a count
    of each one's calls. calls["restart"]() puts the clock back before midnight, where the page's own date is
    read, and zeroes the counts."""
    clock = {"now": BEFORE_MIDNIGHT}
    calls = {"trends": 0, "days": 0}
    real_trends, real_days = services.get_trends, services.get_days

    def trends(uid):
        calls["trends"] += 1
        clock["now"] = trends_at(calls["trends"])
        return real_trends(uid)

    def days(uid):
        calls["days"] += 1
        clock["now"] = days_at(calls["days"])
        return real_days(uid)

    monkeypatch.setattr(db, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(services, "get_trends", trends)
    monkeypatch.setattr(services, "get_days", days)

    def restart():
        clock["now"] = BEFORE_MIDNIGHT
        calls.update(trends=0, days=0)
    return calls, restart


def test_two_reads_on_two_dates_are_read_again_and_the_glance_shows_one_date(client, monkeypatch):
    """get_trends reads at 23:59:59, get_days at 00:00:01: two dates. Both are read once more, after midnight,
    and the glance draws the one date they agree on."""
    sign_in(client)
    uid = only_user().id
    calls, restart = reads_across_midnight(monkeypatch, lambda n: BEFORE_MIDNIGHT if n == 1 else AFTER_MIDNIGHT,
                                           lambda n: AFTER_MIDNIGHT)
    glance = asyncio.run(web.glance_of(uid))
    assert calls == {"trends": 2, "days": 2}
    assert glance["trends"]["today"] == glance["days"]["today"] == "2026-11-11"
    restart()
    r = client.get("/day/2026-11-10")  # the page's own date is read before midnight
    assert r.status_code == 200 and calls == {"trends": 2, "days": 2}
    pills = daygrid_of(r.text)["pills"]
    assert pills[-1]["href"] == "/day/2026-11-11" and pills[-1]["words"] == "Wed 11 Nov, today: nothing trained"
    assert "is-today" in pills[-1]["classes"] and pills[0]["href"] == "/day/2026-10-15"


def test_two_reads_that_still_differ_draw_no_glance_and_the_page_still_answers(client, monkeypatch):
    sign_in(client)
    calls, restart = reads_across_midnight(monkeypatch, lambda n: BEFORE_MIDNIGHT, lambda n: AFTER_MIDNIGHT)
    assert asyncio.run(web.glance_of(only_user().id)) is None and calls == {"trends": 2, "days": 2}
    restart()
    r = client.get("/day/2026-11-10")
    assert r.status_code == 200 and glance_node(r.text) is None and '<div class="col-main">' in r.text
    assert calls == {"trends": 2, "days": 2}


def test_two_reads_on_one_date_are_read_once(client, monkeypatch):
    sign_in(client)
    calls, _ = reads_across_midnight(monkeypatch, lambda n: AFTER_MIDNIGHT, lambda n: AFTER_MIDNIGHT)
    assert asyncio.run(web.glance_of(only_user().id)) is not None and calls == {"trends": 1, "days": 1}


# ---------------------------------------------------------------- stage 6, step 4: Progress' tiles, sheets and Body weight (B8)

@pytest.fixture
def pinned(monkeypatch):
    """The clock at the contract's fixed day: every test below reads dates, so none reads the real clock."""
    monkeypatch.setattr(db, "utcnow", lambda: FIXED_NOW)


@pytest.fixture
def progress_fixed(client, pinned):
    """Progress on the contract's fixed day: sample data, the clock pinned to Tuesday 10 November 2026."""
    sign_in(client)
    assert "error" not in services.add_sample_data(only_user().id, "web")
    return client.get("/progress").text


def tile_words(page: str) -> list[tuple]:
    """Each tile and twin in the page's tiles grid: its tag, classes, hidden name, figure, label, note and tags."""
    return [(t["tag"], t["classes"], t["name"], t["value"], t["label"], t["note"], [g.text() for g in t["tags"]])
            for t in tiles_of(page)]


WEIGHT_FIXED = ("Weight:", "79.2", "kg trend", "-0.24 kg a week", [])
CALORIES_FIXED = ("Eaten a day:", "2,216", "kcal eaten a day", "last 7 days", [])
TRAINING_FIXED = ("Training:", "3", "lift sessions", "last 7 days", [])
MAINTENANCE_FIXED = ("Maintenance:", "2,510", "kcal to maintain", None, ["estimate"])


def test_progress_draws_four_tiles_on_the_fixed_day(progress_fixed):
    """Section L, Progress' tiles: Weight, Calories and Training as on Today, then Maintenance: 2,513 shown "2,510"
    through the maintenance filter, "kcal to maintain", its estimate tag in its own slot under the label. Each
    is a button and its twin, in div.tiles.four."""
    assert '<div class="tiles four">' in progress_fixed
    assert tile_words(progress_fixed) == [
        ("button", ["tile", "tile-weight"], *WEIGHT_FIXED), ("a", ["tile", "tile-weight", "tile-link"], *WEIGHT_FIXED),
        ("button", ["tile", "tile-calories"], *CALORIES_FIXED),
        ("a", ["tile", "tile-calories", "tile-link"], *CALORIES_FIXED),
        ("button", ["tile", "tile-training"], *TRAINING_FIXED),
        ("a", ["tile", "tile-training", "tile-link"], *TRAINING_FIXED),
        ("button", ["tile", "tile-maintenance"], *MAINTENANCE_FIXED),
        ("a", ["tile", "tile-maintenance", "tile-link"], *MAINTENANCE_FIXED)]
    assert services.get_trends(only_user().id)["maintenance"]["kcal"] == 2513
    # Each reads in section L's order: its hidden name, the figure, the label, then the note or the tag.
    assert [[e.classes for e in t["node"].elements()] for t in tiles_of(progress_fixed)] == [
        [["sr"], ["tile-value"], ["tile-label"], ["tile-note"]]] * 6 + [
        [["sr"], ["tile-value"], ["tile-label"], ["tag", "tag-estimate"]]] * 2


def test_progress_twins_point_at_this_page_and_todays_are_unchanged(progress_fixed, client):
    """Contract line 1382: on Progress the twins are #body-weight and #trends, and both targets are on the page.
    Today's twins still go to /progress#body-weight and /progress#trends."""
    shown = tiles_of(progress_fixed)
    assert [t["attrs"] for t in shown] == [
        {"class": "tile tile-weight", "type": "button", "popovertarget": "sheet-weight"},
        {"class": "tile tile-weight tile-link", "href": "#body-weight"},
        {"class": "tile tile-calories", "type": "button", "popovertarget": "sheet-calories"},
        {"class": "tile tile-calories tile-link", "href": "#trends"},
        {"class": "tile tile-training", "type": "button", "popovertarget": "sheet-training"},
        {"class": "tile tile-training tile-link", "href": "#trends"},
        {"class": "tile tile-maintenance", "type": "button", "popovertarget": "sheet-maintenance"},
        {"class": "tile tile-maintenance tile-link", "href": "#trends"}]
    for button, twin in zip(shown[0::2], shown[1::2]):
        assert twin["text"] == button["text"] and twin["text"]
    assert progress_fixed.count('id="body-weight"') == 1 and progress_fixed.count('id="trends"') == 1
    assert '<section class="card bw-card" id="body-weight"' in progress_fixed
    assert '<section class="card trends" id="trends" aria-labelledby="trends-title">' in progress_fixed
    today = tiles_of(day_of(client, "2026-11-10"))
    assert [t["attrs"].get("href") for t in today if t["tag"] == "a"] == [
        "/progress#body-weight", "/progress#trends", "/progress#trends"]


def test_progress_sheets_carry_no_open_progress_link(progress_fixed):
    """Contract line 1385: a Today sheet ends with Open Progress, a Progress sheet has none."""
    sheets = sheets_of(progress_fixed)
    assert list(sheets) == ["sheet-weight", "sheet-calories", "sheet-training", "sheet-maintenance"]
    for sid, sheet in sheets.items():
        tag = ("estimate", "estimate") if sid == "sheet-maintenance" else None
        assert sheet_frame_findings(sheet, sid, None, tag) == [], sid
    assert "Open Progress" not in progress_fixed and "sheet-link" not in progress_fixed


def test_the_maintenance_sheet_on_the_fixed_day(progress_fixed):
    """Contract line 1389: period "{from} to {to}", the figure with an estimate tag of its own right after it,
    "Maintenance, a day", then the two sentences. No panel and no table. Fixed day: 2,513 shown "2,510", 2,198,
    -0.86, 21, 21 Oct to 10 Nov."""
    from .test_pages_text import estimate_findings, parse  # noqa: PLC0415 - test_pages_text imports this file

    sheet = sheets_of(progress_fixed)["sheet-maintenance"]
    assert (sheet["period"], sheet["giant"], sheet["title"]) == ("21 Oct to 10 Nov", "2,510 kcal",
                                                                 "Maintenance, a day")
    kids = sheet["node"].elements()
    assert [shape(k) for k in kids] == [("div", "sheet-head"), ("p", "giant"), ("span", "tag", "tag-estimate"),
                                        ("h2", "noun"), ("p", "sentence"), ("p", "sentence")]
    assert [k.text() for k in kids[-2:]] == [
        "About 2,510 kcal a day, worked out from 21 logged days. Over that window intake averaged "
        "2,198 kcal a day and the 7-day average weight moved -0.86 kg.",
        "An estimate from your intake and weight change. Your calorie target stays yours to set."]
    assert sheet["svg"] == [] and not sheet["node"].find_all("table") and not sheet["node"].find_all(cls="panel")
    # The tag is never inside a figure: T3's own estimate rule, on the whole page.
    assert estimate_findings(parse(progress_fixed)) == []
    assert '<p class="giant">2,510&nbsp;<span class="unit">kcal</span></p>\n  <span class="tag tag-estimate">' \
           'estimate</span>' in progress_fixed


def test_with_no_estimate_the_maintenance_tile_is_empty_and_no_sheet_is_drawn(client, pinned):
    """Without an estimate the Maintenance tile is empty, "Not enough logged yet", dashed, not a control, and its
    sheet is not in the markup. Two states: a new account, and weigh-ins with no food."""
    sign_in(client)
    for state in ("new", "weighed"):
        if state == "weighed":
            for n in (21, 14, 0):
                services.log_bodyweight(only_user().id, {"kg": "80", "date": (date(2026, 11, 10) - timedelta(days=n))
                                                         .isoformat()}, "claude")
        page = client.get("/progress").text
        assert services.get_trends(only_user().id)["maintenance"] is None, state
        [tile] = [t for t in tiles_of(page) if "tile-maintenance" in t["classes"]]
        assert (tile["tag"], tile["attrs"], tile["value"], tile["label"], tile["note"], tile["tags"]) == (
            "div", {"class": "tile tile-maintenance is-empty"}, "Not enough logged yet", None, None, []), state
        assert [e.classes for e in tile["node"].elements()] == [["sr"], ["tile-value"]]
        assert "sheet-maintenance" not in page and "Maintenance, a day" not in page, state


PROGRESS_STATES = ("new", "food today", "a weigh-in today", "old food and an old weigh-in", "food in 28 not 7",
                   "the fixed day", "targets and a zero rate")


def progress_state(state: str) -> None:
    """Log what the state names for the one signed-in person, on the pinned fixed day."""
    uid, today = only_user().id, date(2026, 11, 10)
    ago = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    if state == "food today":
        services.log_food(uid, {"entries": [{"description": "Dal", "kcal": 400}]}, "claude")
    if state == "a weigh-in today":
        services.log_bodyweight(uid, {"kg": "80"}, "claude")
    if state == "old food and an old weigh-in":
        services.log_food(uid, {"date": ago(10), "entries": [{"description": "Dal", "kcal": 400}]}, "claude")
        services.log_bodyweight(uid, {"kg": "80", "date": ago(40)}, "claude")
    if state == "food in 28 not 7":
        services.log_food(uid, {"date": ago(10), "entries": [{"description": "Dal", "kcal": 400}]}, "claude")
    if state == "the fixed day":
        assert "error" not in services.add_sample_data(uid, "web")
    if state == "targets and a zero rate":
        services.update_settings(uid, {"calorie_target_kcal": 2000, "target_sessions_per_week": 0,
                                       "target_cardio_minutes_per_week": 150}, "web")
        for n in (21, 14, 7, 0):
            services.log_bodyweight(uid, {"kg": "80", "date": ago(n)}, "claude")
        services.log_food(uid, {"entries": [{"description": "Thali", "kcal": 1000}]}, "claude")


@pytest.mark.parametrize("state", PROGRESS_STATES)
def test_progress_draws_todays_three_tiles_and_sheets_from_the_same_parts(client, pinned, state):
    """One source for both pages: in every state, Progress' Weight, Calories and Training tiles carry Today's
    words, and their sheets Today's period, number, title, sentences, panel and table. Only the twins'
    addresses differ (#body-weight for /progress#body-weight), and only Today's sheets end with Open Progress."""
    sign_in(client)
    progress_state(state)
    today, progress = day_of(client, "2026-11-10"), client.get("/progress").text
    words = lambda t: (t["tag"], t["classes"], t["name"], t["value"], t["label"], t["note"], t["text"])  # noqa: E731
    on_today = tiles_of(today)
    shared = [t for t in tiles_of(progress) if "tile-maintenance" not in t["classes"]]
    assert [words(t) for t in shared] == [words(t) for t in on_today]
    assert [t["attrs"] for t in shared] == [
        {k: (v.replace("/progress#", "#") if k == "href" else v) for k, v in t["attrs"].items()} for t in on_today]
    today_sheets, progress_sheets = sheets_of(today), sheets_of(progress)
    assert list(progress_sheets)[:len(today_sheets)] == list(today_sheets)
    keys = ("period", "giant", "title", "sentence", "panel", "head", "rows")
    for sid, sheet in today_sheets.items():
        assert {k: progress_sheets[sid][k] for k in keys} == {k: sheet[k] for k in keys}, sid
        assert [shape(e) for e in progress_sheets[sid]["node"].elements()] == \
            [shape(e) for e in sheet["node"].elements()][:-1], sid  # the same, less Open Progress
        assert sheet_frame_findings(progress_sheets[sid], sid, None) == [], sid
    assert set(progress_sheets) - set(today_sheets) <= {"sheet-maintenance"}


EMPTY_WEIGHT = ("div", ["tile", "tile-weight", "is-empty"], "Weight:", "No weigh-ins yet", None, None, [])
NO_FOOD_28 = ("div", ["tile", "tile-calories", "is-empty"], "Eaten a day:", "No food in the last 28 days", None, None, [])
TRAINING_0 = [("button", ["tile", "tile-training"], "Training:", "0", "lift sessions", "last 7 days", []),
              ("a", ["tile", "tile-training", "tile-link"], "Training:", "0", "lift sessions", "last 7 days", [])]
NO_MAINTENANCE = ("div", ["tile", "tile-maintenance", "is-empty"], "Maintenance:", "Not enough logged yet", None, None,
                  [])


@pytest.mark.parametrize("state, shown", [
    ("new", [EMPTY_WEIGHT, NO_FOOD_28, *TRAINING_0, NO_MAINTENANCE]),
    ("food in 28 not 7", [
        EMPTY_WEIGHT,
        ("div", ["tile", "tile-calories", "is-empty"], "Eaten a day:", "No food in the last 7 days", None, None, []),
        *TRAINING_0, NO_MAINTENANCE]),
    ("a weigh-in today", [
        ("button", ["tile", "tile-weight"], "Weight:", "80", "kg trend", "No weekly rate yet", []),
        ("a", ["tile", "tile-weight", "tile-link"], "Weight:", "80", "kg trend", "No weekly rate yet", []),
        NO_FOOD_28, *TRAINING_0, NO_MAINTENANCE]),
    ("food today", [
        EMPTY_WEIGHT,
        ("button", ["tile", "tile-calories"], "Eaten a day:", "400", "kcal eaten a day", "last 7 days", []),
        ("a", ["tile", "tile-calories", "tile-link"], "Eaten a day:", "400", "kcal eaten a day", "last 7 days", []),
        *TRAINING_0, NO_MAINTENANCE]),
])
def test_each_progress_tile_says_its_words_in_each_state(client, pinned, state, shown):
    """The text sweep reads a tile as one piece, so it never sees a tile's own words: these pins are their guard.
    A tile without a sheet is a div, never a control, and opens nothing."""
    sign_in(client)
    progress_state(state)
    page = client.get("/progress").text
    assert tile_words(page) == shown
    assert [t["attrs"].get("popovertarget") for t in tiles_of(page) if t["tag"] == "button"] == list(sheets_of(page))


def bw_node(page: str):
    """The Body weight card read as a tree."""
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    [card] = parse(page).find_all("section", "bw-card")
    return card


def test_the_body_weight_card_on_the_fixed_day(progress_fixed):
    """Section L, the Body weight card: a section named by its own h2, the head "Body weight" and "4 weeks", the
    chart over 28 days beside four callouts in .bw-wrap (High, Latest, Trend boxed, Low), the legend with the open
    goal's target, then the small line in its new words."""
    card = bw_node(progress_fixed)
    assert card.attrs == {"class": "card bw-card", "id": "body-weight", "aria-labelledby": "body-weight-title"}
    assert [shape(k) for k in card.elements()] == [("div", "card-head"), ("div", "bw-wrap"), ("ul", "legend"),
                                                   ("p", "small")]
    head = card.elements()[0].elements()
    assert [(shape(h), h.attrs.get("id"), h.text()) for h in head] == [
        (("h2",), "body-weight-title", "Body weight"), (("span", "aside"), None, "4 weeks")]
    wrap = card.elements()[1].elements()
    assert [(e.tag, e.attrs.get("id")) for e in wrap] == [("svg", "bodyweight-chart"), ("dl", None)]
    assert callouts_of(body_weight_card(progress_fixed)) == [
        ([], "High", "80.1 kg", "Fri 16 Oct"), ([], "Latest", "78.6 kg", "Tue 10 Nov"),
        (["is-key"], "Trend", "79.2 kg", "Smoothed"), ([], "Low", "78.6 kg", "Tue 10 Nov")]
    assert callouts_of(body_weight_card(progress_fixed)) == expected_callouts(only_user().id)
    legend = card.elements()[2]
    assert legend.attrs == {"class": "legend", "aria-label": "Key"}
    assert [([k.classes for k in li.elements()], [k.attrs.get("aria-hidden") for k in li.elements()], li.text())
            for li in legend.elements()] == [
        ([["key", "dot-key"]], ["true"], "Weigh-in"), ([["key", "line"]], ["true"], "Smoothed trend"),
        ([["key", "line", "goal"]], ["true"], "Goal 77 kg")]
    assert card.elements()[3].text() == \
        "Dots are weigh-ins. The line is the smoothed trend, which evens out day-to-day swings."
    assert "steadies" not in progress_fixed and "7-day average:" not in progress_fixed
    assert glued(card) == []  # a figure never touches the words under it


def bw_state(state: str) -> None:
    """Weigh-ins for a Body weight card state, on the pinned fixed day."""
    uid, today = only_user().id, date(2026, 11, 10)

    def weigh(n, kg):
        assert "error" not in services.log_bodyweight(uid, {"kg": kg, "date": (today - timedelta(days=n)).isoformat()},
                                                      "claude")
    if state == "none in 28 days":
        weigh(40, "80")
        weigh(45, "80")
    if state == "one in 28 days":
        weigh(40, "81")
        weigh(3, "80.2")
    if state == "two in 28 days":
        weigh(40, "81")
        weigh(3, "80.6")
        weigh(0, "80.2")


@pytest.mark.parametrize("state, wrap, calls, tail", [
    ("no weigh-ins", None, [], [("p", "empty-line")]),
    ("none in 28 days", [("p", "empty-line"), ("dl", "callouts")],
     [(["is-key"], "Trend", "80 kg", "Smoothed")], [("div", "bw-wrap")]),
    ("one in 28 days", [("p", "empty-line"), ("dl", "callouts")],
     [([], "High", "80.2 kg", "Sat 7 Nov"), ([], "Latest", "80.2 kg", "Sat 7 Nov"),
      (["is-key"], "Trend", "80.9 kg", "Smoothed"), ([], "Low", "80.2 kg", "Sat 7 Nov")],
     [("div", "bw-wrap")]),
    ("two in 28 days", [("svg", "chart"), ("dl", "callouts")],
     [([], "High", "80.6 kg", "Sat 7 Nov"), ([], "Latest", "80.2 kg", "Tue 10 Nov"),
      (["is-key"], "Trend", "80.9 kg", "Smoothed"), ([], "Low", "80.2 kg", "Tue 10 Nov")],
     [("div", "bw-wrap"), ("ul", "legend"), ("p", "small")]),
])
def test_the_body_weight_card_in_each_state(client, pinned, state, wrap, calls, tail):
    """No weigh-in: the card is empty and points to Today. None in the 28 days: their words in the chart's place
    and the Trend callout alone. One: the four callouts, and the ruled words in the chart's place. Two or more:
    the chart, the legend and the small line. No Goal key without an open body weight goal."""
    sign_in(client)
    bw_state(state)
    page = client.get("/progress").text
    card = bw_node(page)
    assert [shape(k) for k in card.elements()] == [("div", "card-head"), *tail]
    assert ("is-empty" in card.classes) == (state == "no weigh-ins")
    words = {"no weigh-ins": "No weigh-ins yet. Add one on Today.", "none in 28 days": "No weigh-ins in the last 28 days.",
             "one in 28 days": "One weigh-in in the last 28 days. The chart starts from the second one."}
    if wrap is None:
        assert card.elements()[1].text() == words[state]
        assert [(a.attrs, a.text()) for a in card.find_all("a")] == [({"href": "/today"}, "Today")]
    else:
        bw_wrap = card.elements()[1]
        assert [shape(e)[:2] for e in bw_wrap.elements()] == wrap
        if state in words:
            assert bw_wrap.elements()[0].text() == words[state]
    assert callouts_of(body_weight_card(page)) == calls
    if calls:
        assert calls == expected_callouts(only_user().id)
    assert ('id="bodyweight-chart"' in page) == (state == "two in 28 days")
    assert "Goal" not in card.text() and "7-day average" not in card.text()
    weight = services.get_trends(only_user().id)["bodyweight"]
    assert (weight or {}).get("weigh_ins_28d", 0) == {"none in 28 days": 0, "one in 28 days": 1,
                                                      "two in 28 days": 2}.get(state, 0)


def test_the_chart_branch_keys_on_the_chart_and_the_words_on_get_trends_count(client, pinned, monkeypatch):
    """Ruled: the card draws the chart whenever there is a chart to draw, and its words in the chart's place key
    on get_trends' weigh_ins_28d, never on a count the web makes. With the count stood in for, the words follow
    it. With a chart stood in for, it is drawn."""
    from markupsafe import Markup  # noqa: PLC0415

    sign_in(client)
    bw_state("one in 28 days")
    real = services.get_trends

    def counted(n):
        def read(uid):
            out = real(uid)
            out["bodyweight"] = {**out["bodyweight"], "weigh_ins_28d": n}
            return out
        return read
    monkeypatch.setattr(services, "get_trends", counted(0))
    assert "No weigh-ins in the last 28 days." in body_weight_card(client.get("/progress").text)
    monkeypatch.setattr(services, "get_trends", counted(1))
    assert "One weigh-in in the last 28 days." in body_weight_card(client.get("/progress").text)
    monkeypatch.setattr(services, "get_trends", real)
    drawn = Markup('<svg class="chart" id="bodyweight-chart" role="img"><title>t</title><desc>d</desc></svg>')
    monkeypatch.setattr(web.charts, "bodyweight_svg", lambda *a, **k: drawn)
    card = body_weight_card(client.get("/progress").text)
    assert 'id="bodyweight-chart"' in card and "One weigh-in" not in card and "Dots are weigh-ins" in card


def test_an_open_body_weight_goal_puts_its_target_in_the_legend(client, pinned):
    sign_in(client)
    bw_state("two in 28 days")
    assert "error" not in services.set_goal(only_user().id, {"kind": "bodyweight", "target_value": 75.5,
                                                             "target_date": "2027-02-01"}, "web")
    legend = [li.text() for li in bw_node(client.get("/progress").text).find_all("ul", "legend")[0].elements()]
    assert legend == ["Weigh-in", "Smoothed trend", "Goal 75.5 kg"]


def test_a_weigh_in_dated_after_today_is_the_cards_one_weigh_in_under_its_own_date(client, pinned):
    """Carried from an earlier review. Logged today in Pacific/Kiritimati (UTC+14), then the zone moves to
    Pacific/Pago_Pago (UTC-11), and one more 40 days back. The later weigh-in counts as today's: it is the one
    weigh-in of the 28 days, so High, Latest and Low show it, under its own date, a day after today."""
    sign_in(client)
    uid = only_user().id
    services.update_settings(uid, {"time_zone": "Pacific/Kiritimati"}, "web")
    assert "error" not in services.log_bodyweight(uid, {"kg": "70"}, "claude")
    services.update_settings(uid, {"time_zone": "Pacific/Pago_Pago"}, "web")
    assert today_of(only_user()) == date(2026, 11, 10)
    assert "error" not in services.log_bodyweight(uid, {"kg": "72", "date": "2026-10-01"}, "claude")
    r = client.get("/progress")
    assert r.status_code == 200
    card = body_weight_card(r.text)
    assert "One weigh-in in the last 28 days. The chart starts from the second one." in card
    assert callouts_of(card) == expected_callouts(uid) == [
        ([], "High", "70 kg", "Wed 11 Nov"), ([], "Latest", "70 kg", "Wed 11 Nov"),
        (["is-key"], "Trend", "71.8 kg", "Smoothed"), ([], "Low", "70 kg", "Wed 11 Nov")]
    assert 'id="bodyweight-chart"' not in card


def test_the_card_reads_the_persons_own_date_not_the_utc_date(client, monkeypatch):
    """Carried from an earlier review. At 20:00 UTC on Tuesday 10 November it is 01:30 on Wednesday 11 November in
    Asia/Kolkata. A weigh-in logged now is that Wednesday's: the latest and the low, with the chart's 28 days
    ending on it."""
    sign_in(client)
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc))
    uid = only_user().id
    services.update_settings(uid, {"time_zone": "Asia/Kolkata"}, "web")
    assert today_of(only_user()) == date(2026, 11, 11)
    assert "error" not in services.log_bodyweight(uid, {"kg": "81", "date": "2026-11-08"}, "claude")
    assert "error" not in services.log_bodyweight(uid, {"kg": "80"}, "claude")
    page = client.get("/progress").text
    card = body_weight_card(page)
    assert callouts_of(card) == expected_callouts(uid) == [
        ([], "High", "81 kg", "Sun 8 Nov"), ([], "Latest", "80 kg", "Wed 11 Nov"),
        (["is-key"], "Trend", "80.9 kg", "Smoothed"), ([], "Low", "80 kg", "Wed 11 Nov")]
    desc = re.search(r'<desc id="bodyweight-chart-desc">(.*?)</desc>', card, re.S).group(1)
    assert desc.startswith("2 weigh-ins from Sun 8 Nov to Wed 11 Nov.")
    assert tiles_of(page)[0]["value"] == "80.9"


def test_progress_runs_head_banner_tiles_body_weight_trends_cardio_then_lifts(client, pinned):
    """Contract lines 1480 to 1488: the page head, the sample banner, the tiles and their sheets, Body weight,
    Trends, Cardio, Lifts. Cardio comes before Lifts in the markup."""
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    sign_in(client)
    progress_state("the fixed day")
    main = next(iter(parse(client.get("/progress").text).find_all("main")))
    assert [shape(c) for c in main.elements()] == [
        ("div", "page-head"), ("p", "notice", "sample-banner"), ("div", "glance"), ("section", "card", "bw-card"),
        ("section", "card", "trends"), ("section", "card", "cardio-card"), ("section", "card", "lifts-card")]
    assert [e.text() for e in main.elements()[0].elements()] == ["Progress"]
    glance = main.elements()[2]
    assert [shape(c) for c in glance.elements()] == [("div", "tiles", "four"), *[("div", "sheet")] * 4]
    assert [c.attrs["id"] for c in glance.elements()[1:]] == ["sheet-weight", "sheet-calories", "sheet-training",
                                                              "sheet-maintenance"]


def test_progress_reads_the_glance_and_gives_its_date_to_the_chart(client, pinned, monkeypatch):
    """Progress draws its tiles, sheets and Body weight card from web.glance_of, and the chart's 28 days end on
    the date that glance names. With no glance (a read failed, or two reads on two dates), none of them is drawn,
    and Trends and Lifts still are."""
    sign_in(client)
    progress_state("the fixed day")
    seen, glances, real_trend, real_glance = [], [], web._trend, web.glance_of

    def spy(user_id, today=None):
        seen.append(today)
        return real_trend(user_id, today)

    async def counted(user_id):
        glances.append(user_id)
        return await real_glance(user_id)
    monkeypatch.setattr(web, "_trend", spy)
    monkeypatch.setattr(web, "glance_of", counted)
    # get_progress names another date: the chart still takes the glance's.
    real_progress = services.get_progress
    monkeypatch.setattr(services, "get_progress", lambda uid, *a: {**real_progress(uid, *a), "today": "2026-10-01"})
    r = client.get("/progress")
    assert r.status_code == 200
    assert glances == [only_user().id] and seen == [date(2026, 11, 10)]
    # The Cardio card reads get_cardio of the page's day, the glance's: it draws, though get_progress names another.
    assert '<h2 id="cardio-title">Cardio</h2>' in r.text
    monkeypatch.setattr(services, "get_progress", real_progress)

    async def none(user_id):
        return None
    monkeypatch.setattr(web, "glance_of", none)
    r = client.get("/progress")
    assert r.status_code == 200 and glance_node(r.text) is None and "bw-card" not in r.text and "popover" not in r.text
    assert '<section class="card trends" id="trends" aria-labelledby="trends-title">' in r.text
    assert '<h2 id="cardio-title">Cardio</h2>' in r.text and '<h2 id="lifts-title">Lifts</h2>' in r.text
    # The page's date then falls back to get_progress' own: the Lifts card's days are worded against it.
    _, lifts_rows = card_table(lower_card(r.text, "lifts-card"))
    assert [row[-1] for row in lifts_rows][:2] == ["Fri 6 Nov", "Fri 6 Nov"]
    # That day itself, not one a day off: the chart is handed get_progress' own today.
    assert seen[-1] == date.fromisoformat(real_progress(only_user().id)["today"]) == date(2026, 11, 10)


def test_trend_draws_the_28_days_ending_on_the_date_it_is_given(client, pinned):
    """_trend's chart covers the 28 days ending on the date Progress hands it, not the clock's: two weigh-ins 0 and
    3 days back draw on the fixed day, and none of them falls in the 28 days ending 28 days later."""
    sign_in(client)
    uid = only_user().id
    for n in (3, 0):
        assert "error" not in services.log_bodyweight(
            uid, {"kg": "80", "date": (date(2026, 11, 10) - timedelta(days=n)).isoformat()}, "claude")
    assert len(web._trend(uid, date(2026, 11, 10))["bodyweight"]["points"]) == 2
    assert web._trend(uid, date(2026, 12, 8))["bodyweight"] is None
    assert len(web._trend(uid)["bodyweight"]["points"]) == 2  # no date: the person's own, the pinned fixed day


# ---------------------------------------------------------------- stage 6, step 4: Progress' Trends, Cardio and Lifts (B9)

def lower_card(page: str, cls: str):
    """The section.card of that class on the page, read as a tree."""
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    [card] = parse(page).find_all("section", cls)
    return card


def card_table(card) -> tuple[list[str], list[list[str]]]:
    """The one table in a card: its column headers, and each body row's cells as a person reads them."""
    from .test_pages_text import tables  # noqa: PLC0415 - test_pages_text imports this file

    [table] = tables(card)
    return table.headers, [[c.text() for c in row] for row in table.rows]


def trends_of(page: str) -> list[tuple[str, str, str]]:
    """The Trends table's rows: (quantity, value, remark)."""
    headers, body = card_table(lower_card(page, "trends"))
    assert headers == ["Quantity", "Value", "Remark"]
    return [tuple(row) for row in body]


NO_WEIGH_INS = [("Smoothed trend", "", "No weigh-ins yet."), ("Weight rate", "", "No weigh-ins yet."),
                ("Weigh-ins", "", "No weigh-ins yet.")]
NO_FOOD_ROWS = [(q, "", "No food logged in the last 28 days.")
                for q in ("Eaten a day", "Eaten a day", "From estimates", "Calorie target")]
NO_TRAINING = [("Lift sessions", "0", "Last 7 days"), ("Lift sessions a week", f"0{NB}a week", "4-week average"),
               ("Cardio", f"0{NB}min", "Last 7 days"), ("Cardio a week", f"0{NB}min", "4-week average")]


def test_the_trends_card_on_the_fixed_day(progress_fixed):
    """Section L's Trends table on the fixed day: Quantity, Value, Remark, one row per quantity in the four groups,
    every figure get_trends' own through the filter the contract names. Fixed day: 79.2 kg smoothed, 79 kg the
    7-day average, -0.24 kg a week from 27 Oct to 10 Nov, 2,216 and 2,196 kcal, 41%, no target, 3 sessions,
    85 min, and maintenance 2,513 shown "2,510" with its tag, 21 logged days, 21 Oct to 10 Nov."""
    t = services.get_trends(only_user().id)
    bw, cal, ses, car, m = t["bodyweight"], t["calories"], t["sessions"], t["cardio"], t["maintenance"]
    show = lambda v, q: formatting.show(v, q, page=True)  # noqa: E731
    on = lambda a, b: formatting.days(a, b, t["today"])  # noqa: E731
    assert trends_of(progress_fixed) == [
        ("Smoothed trend", show(bw["smoothed"], "bodyweight_derived"), ""),
        ("7-day average", show(bw["now"], "bodyweight_derived"), ""),
        ("Weight rate", f"{show(bw['rate_per_week'], 'rate')}{NB}kg a week",
         on(bw["rate_window"]["from"], bw["rate_window"]["to"])),
        ("Weigh-ins", show(bw["weigh_ins_28d"], "count"), "Last 28 days"),
        ("Eaten a day", show(cal["avg_7d"], "kcal"), f"Last 7 days, {show(cal['days_logged_7d'], 'count')} logged"),
        ("Eaten a day", show(cal["avg_28d"], "kcal"), f"Last 28 days, {show(cal['days_logged_28d'], 'count')} logged"),
        ("From estimates", show(cal["estimated_share_pct_28d"], "percent"), "Last 28 days"),
        ("Calorie target", "", "Not set"),
        ("Lift sessions", show(ses["this_week"], "count"), "Last 7 days"),
        ("Lift sessions a week", f"{show(ses['per_week_4w'], 'count_avg')}{NB}a week", "4-week average"),
        ("Cardio", show(car["minutes_this_week"], "week_minutes"), "Last 7 days"),
        ("Cardio a week", show(car["minutes_per_week_4w"], "week_minutes"), "4-week average"),
        ("Maintenance", show(m["kcal"], "maintenance"),
         f"estimate {show(m['days_logged'], 'count')} logged days, {on(m['from'], m['to'])}")]
    assert [row[1] for row in trends_of(progress_fixed)] == [
        f"79.2{NB}kg", f"79{NB}kg", f"-0.24{NB}kg a week", "28", f"2,216{NB}kcal", f"2,196{NB}kcal", "41%", "", "3",
        f"3{NB}a week", f"85{NB}min", f"85{NB}min", f"2,510{NB}kcal"]
    assert (bw["now"], m["kcal"], trends_of(progress_fixed)[2][2]) == (79.0, 2513, "27 Oct to 10 Nov")


def test_the_trends_card_is_a_named_section_holding_one_grouped_table(progress_fixed):
    """The card is section.card.trends#trends named by its own h2, the table in div.table-wrap. One tbody: each group
    after Weight starts with a tr.group, every body cell a td (the quantity, the num value, the remark). The
    estimate tag sits first in the Maintenance remark, never in the value."""
    from .test_pages_text import estimate_findings, parse  # noqa: PLC0415 - test_pages_text imports this file

    card = lower_card(progress_fixed, "trends")
    assert card.attrs == {"class": "card trends", "id": "trends", "aria-labelledby": "trends-title"}
    assert [(shape(k), k.attrs.get("id"), k.text() if k.tag == "h2" else None) for k in card.elements()] == [
        (("h2",), "trends-title", "Trends"), (("div", "table-wrap"), None, None)]
    [table] = card.elements()[1].elements()
    assert shape(table) == ("table", "trends") and [e.tag for e in table.elements()] == ["thead", "tbody"]
    head = table.elements()[0].elements()[0].elements()
    assert [(c.tag, c.attrs, c.text()) for c in head] == [
        ("th", {"scope": "col"}, "Quantity"), ("th", {"scope": "col", "class": "num"}, "Value"),
        ("th", {"scope": "col"}, "Remark")]
    body = table.elements()[1].elements()
    assert [r.classes for r in body] == [[]] * 4 + [["group"]] + [[]] * 3 + [["group"]] + [[]] * 3 + [["group"]]
    assert all([(c.tag, c.classes) for c in r.elements()] == [("td", []), ("td", ["num"]), ("td", ["remark"])]
               for r in body)
    remark = body[-1].elements()[2]
    assert [shape(e) for e in remark.elements()] == [("span", "tag", "tag-estimate")]
    assert isinstance(remark.children[1], str) and remark.children[1].startswith(" ")  # the tag, then its words
    assert body[-1].elements()[1].elements() == []  # the value holds the figure alone
    assert estimate_findings(parse(progress_fixed)) == []


@pytest.mark.parametrize("state, rows", [
    ("new", [*NO_WEIGH_INS, *NO_FOOD_ROWS, *NO_TRAINING,
             ("Maintenance", "", "Needs food logged on 14 more of the last 21 days.")]),
    ("a weigh-in today", [
        ("Smoothed trend", f"80{NB}kg", ""), ("7-day average", f"80{NB}kg", ""),
        ("Weight rate", "", "The weekly rate needs a weigh-in from 14 to 42 days ago."),
        ("Weigh-ins", "1", "Last 28 days"), *NO_FOOD_ROWS, *NO_TRAINING,
        ("Maintenance", "", "Needs food logged on 14 more of the last 21 days.")]),
    ("old food and an old weigh-in", [
        ("Smoothed trend", f"80{NB}kg", ""), ("Latest weigh-in", f"80{NB}kg", ""),
        ("Weight rate", "", "The weekly rate needs a weigh-in in the last 7 days."),
        ("Weigh-ins", "0", "Last 28 days"),
        ("Eaten a day", "", "No food logged in the last 7 days."),
        ("Eaten a day", f"400{NB}kcal", "Last 28 days, 1 logged"), ("From estimates", "0%", "Last 28 days"),
        ("Calorie target", "", "Not set"), *NO_TRAINING,
        ("Maintenance", "", "Needs food logged on 13 more of the last 21 days.")]),
    ("targets and a zero rate", [
        ("Smoothed trend", f"80{NB}kg", ""), ("7-day average", f"80{NB}kg", ""),
        ("Weight rate", f"0{NB}kg a week", "27 Oct to 10 Nov"), ("Weigh-ins", "4", "Last 28 days"),
        ("Eaten a day", f"1,000{NB}kcal", "Last 7 days, 1 logged"),
        ("Eaten a day", f"1,000{NB}kcal", "Last 28 days, 1 logged"), ("From estimates", "0%", "Last 28 days"),
        ("Calorie target", f"2,000{NB}kcal", ""),
        ("Lift sessions", "0", "Last 7 days, target 0"), ("Lift sessions a week", f"0{NB}a week", "4-week average"),
        ("Cardio", f"0{NB}min", f"Last 7 days, target 150{NB}min"), ("Cardio a week", f"0{NB}min", "4-week average"),
        ("Maintenance", "", "Needs food logged on 13 more of the last 21 days.")]),
])
def test_the_trends_card_in_each_state(client, pinned, state, rows):
    """No weigh-ins: each weight row blank, the reason in its remark, and no basis row (there is no basis). No rate:
    the value blank, get_trends' reason. No food in 28 days: the four food rows blank with the contract's words. No
    food in 7: that row's reason. No target: "Not set". Targets: the figure, and each Training target in the remark.
    No estimate: maintenance_note, no tag. A value cell never holds words."""
    sign_in(client)
    progress_state(state)
    page = client.get("/progress").text
    assert trends_of(page) == rows
    body = lower_card(page, "trends").find_all("tbody")[0].elements()
    food = [q for q, _, _ in rows].index("Eaten a day")  # 3 weight rows with no weigh-in, else 4
    assert [n for n, r in enumerate(body) if r.classes] == [food, food + 4, food + 8]
    assert all(r.classes in ([], ["group"]) for r in body)
    assert not lower_card(page, "trends").find_all(cls="tag-estimate")
    note = services.get_trends(only_user().id)["maintenance_note"]
    assert rows[-1][2] == note


def test_a_trends_value_holds_no_words_and_the_reasons_are_the_reads(client, pinned, monkeypatch):
    """Every remark that gives a reason is the read's own words: the rate's from get_trends' note less the sample
    sentence, the estimate's from maintenance_note. Stood in for, they follow the read."""
    sign_in(client)
    progress_state("a weigh-in today")
    real = services.get_trends

    def worded(uid):
        out = real(uid)
        return {**out, "note": "Rate words. " + web.SAMPLE_NOTE, "maintenance_note": "Estimate words."}
    monkeypatch.setattr(services, "get_trends", worded)
    rows = {q: (v, r) for q, v, r in trends_of(client.get("/progress").text)}
    assert rows["Weight rate"] == ("", "Rate words.") and rows["Maintenance"] == ("", "Estimate words.")


CARDIO_HEAD = ["Activity", "Sessions", "Time (min)", "Fastest pace (per km)", "Last"]
CARDIO_SHORT = "Sessions and time cover the last 28 days."  # with no Fastest pace column, its sentences go too
CARDIO_LINE = ("Sessions and time cover the last 28 days. Fastest pace is your best ever. A blank pace means no "
               "distance is logged for that activity.")


def test_the_cardio_card_on_the_fixed_day(progress_fixed):
    """Section L's Cardio card: a row table, one row per activity by name, the name plain words (no activity page is
    built). Sessions and Time (min) are the last 28 days', the pace get_cardio's m:ss, Last a link to that day. An
    activity with no distance leaves its pace cell empty: no words, no element. Then the contract's line."""
    card = lower_card(progress_fixed, "cardio-card")
    assert card.attrs == {"class": "card cardio-card", "aria-labelledby": "cardio-title"}
    assert [shape(k) for k in card.elements()] == [("h2",), ("div", "table-wrap"), ("p", "small")]
    assert card.elements()[0].attrs == {"id": "cardio-title"} and card.elements()[0].text() == "Cardio"
    assert card.elements()[2].text() == CARDIO_LINE
    headers, rows = card_table(card)
    assert headers == CARDIO_HEAD
    assert rows == [["Outdoor Run", "4", "121", "5:54", "Sat 7 Nov"], ["Stationary Bike", "4", "100", "", "Thu 5 Nov"],
                    ["Treadmill Walk", "4", "120", "", "Tue 10 Nov"]]
    read = services.get_cardio(only_user().id)
    assert rows == [[a["exercise"], formatting.show(a["sessions_28d"], "count"),
                     formatting.show(a["minutes_28d"], "week_minutes", unit=False), a["fastest_pace"] or "",
                     formatting.day(a["last_date"], read["today"])] for a in read["activities"]]
    [table] = card.find_all("table")
    assert table.classes == ["rows"] and table.parent.classes == ["table-wrap"]
    for tr_, a in zip(table.find_all("tbody")[0].elements(), read["activities"]):
        cells = tr_.elements()
        assert cells[0].classes == ["lead"] and cells[0].elements() == []  # plain words, not a link
        assert [(c.classes, c.attrs.get("data-label")) for c in cells[1:]] == [
            (["num"], "Sessions"), (["num"], "Time (min)"), (["num"], "Fastest pace (per km)"), ([], "Last")]
        assert [(x.attrs, x.text()) for x in cells[4].find_all("a")] == [
            ({"href": f"/day/{a['last_date']}"}, formatting.day(a["last_date"], read["today"]))]
        if a["fastest_pace"] is None:
            assert cells[3].children == []  # empty, so the stacked table hides it
    assert "/progress/cardio" not in progress_fixed


def cardio_state(state: str) -> None:
    """Cardio for a Cardio card state, on the pinned fixed day."""
    uid, today = only_user().id, date(2026, 11, 10)

    def log(name, minutes, n, km=None):
        entry = {"exercise": name, "minutes": minutes, "date": (today - timedelta(days=n)).isoformat()}
        assert "error" not in services.log_cardio(uid, {**entry, **({"distance_km": km} if km else {})}, "claude")
    if state == "no distance beside a distance":
        log("Outdoor Run", 30, 0, 5)
        log("Stationary Bike", 45.5, 1)
    if state == "no distance anywhere":
        log("Stationary Bike", 40, 0)
    if state == "only older than 28 days":
        log("Outdoor Walk", 30, 40, 2.5)
        log("Outdoor Run", 25, 2, 5)


@pytest.mark.parametrize("state, head, rows", [
    ("no distance beside a distance", CARDIO_HEAD,
     [["Outdoor Run", "1", "30", "6:00", "Tue 10 Nov"], ["Stationary Bike", "1", "46", "", "Mon 9 Nov"]]),
    ("no distance anywhere", ["Activity", "Sessions", "Time (min)", "Last"],
     [["Stationary Bike", "1", "40", "Tue 10 Nov"]]),
    ("only older than 28 days", CARDIO_HEAD,
     [["Outdoor Run", "1", "25", "5:00", "Sun 8 Nov"], ["Outdoor Walk", "0", "0", "12:00", "Thu 1 Oct"]]),
])
def test_the_cardio_card_in_each_state(client, pinned, state, head, rows):
    """One session each; an activity with no distance beside one with (its pace cell empty); no distance on any
    activity (no pace column, since a column is never blank in every row); an activity whose sessions are all older
    than 28 days (0 sessions and 0 minutes, true counts, its best pace and its last day)."""
    sign_in(client)
    cardio_state(state)
    page = client.get("/progress").text
    card = lower_card(page, "cardio-card")
    assert "is-empty" not in card.classes and card_table(card) == (head, rows)
    assert all(c.elements() == [] for c in card.find_all("td", "lead"))
    assert card.elements()[-1].text() == (CARDIO_LINE if "Fastest pace (per km)" in head else CARDIO_SHORT)


def test_with_no_cardio_the_cardio_card_says_what_to_do(client, pinned):
    sign_in(client)
    card = lower_card(client.get("/progress").text, "cardio-card")
    assert card.attrs == {"class": "card cardio-card is-empty", "aria-labelledby": "cardio-title"}
    assert [shape(k) for k in card.elements()] == [("h2",), ("p", "empty-line")]
    assert card.elements()[1].text() == "No cardio logged yet. Tell your assistant what you did, or add it on Today."
    assert [(a.attrs, a.text()) for a in card.find_all("a")] == [({"href": "/today"}, "Today")]
    assert not card.find_all("table")


def test_a_failed_cardio_read_draws_no_cardio_card_and_the_rest_of_progress(client, pinned, monkeypatch):
    sign_in(client)
    progress_state("the fixed day")
    monkeypatch.setattr(services, "get_cardio", lambda *a: {"error": "no_account", "message": "x", "fix": "y"})
    r = client.get("/progress")
    assert r.status_code == 200 and "cardio-card" not in r.text and 'id="trends"' in r.text
    assert '<h2 id="lifts-title">Lifts</h2>' in r.text


LIFTS_HEAD = ["Lift", "Estimated max", "Best set", "Trend", "Last trained"]
LIFTS_LINE = "Estimated max is worked out from your sets, not a tested max. Trend is its change a week."
LIFTS_SHORT = "Estimated max is worked out from your sets, not a tested max."  # with no Trend column
ZERO_KG = re.compile(r"(?<![\d.,])0\s?kg")  # a 0 kg figure, not the end of 100 kg: a tile's "0" "kg" too


def test_the_lifts_card_on_the_fixed_day(progress_fixed):
    """Section L's Lifts card: a row table, one row per lift by name, each a link to its page. Estimated max through
    estimated_max (Back Squat 107.92 shown "107.9"), the best set "{weight} kg × {reps}", the trend "{rate} kg a
    week", the last day a link. Pull-Up, never above 0 kg: "Needs added weight", "6 reps", no trend. No 0 kg."""
    card = lower_card(progress_fixed, "lifts-card")
    assert card.attrs == {"class": "card lifts-card", "aria-labelledby": "lifts-title"}
    assert [shape(k) for k in card.elements()] == [("h2",), ("div", "table-wrap"), ("p", "small")]
    assert card.elements()[0].attrs == {"id": "lifts-title"} and card.elements()[2].text() == LIFTS_LINE
    headers, rows = card_table(card)
    assert headers == LIFTS_HEAD
    kg = lambda n: f"{n}{NB}kg"  # noqa: E731
    assert rows == [
        ["Back Squat", kg("107.9"), f"{kg('92.5')} × 5", f"+4.41{NB}kg a week", "Fri 6 Nov"],
        ["Barbell Row", kg("79.2"), f"{kg('62.5')} × 8", f"+4.78{NB}kg a week", "Fri 6 Nov"],
        ["Bench Press", kg("84.6"), f"{kg('72.5')} × 5", f"+4.41{NB}kg a week", "Fri 6 Nov"],
        ["Deadlift", kg("145.8"), f"{kg('125')} × 5", f"+8.6{NB}kg a week", "Mon 9 Nov"],
        ["Overhead Press", kg("61.3"), f"{kg('52.5')} × 5", f"+4.3{NB}kg a week", "Mon 9 Nov"],
        ["Pull-Up", "Needs added weight", f"6{NB}reps", "", "Mon 9 Nov"]]
    uid = only_user().id
    read = services.get_progress(uid)
    with db.new_session() as s:
        history = web.lifts.history(s, uid, date(2026, 11, 10))
    assert history["reps_only"] == {"Pull-Up": {"reps": 6, "date": "2026-10-16"}}
    show = lambda v, q: formatting.show(v, q, page=True)  # noqa: E731
    for row, lift in zip(rows, read["lifts"]):
        best = history["best_set"].get(lift["exercise"])
        assert row[0] == lift["exercise"]
        if lift["current_estimated_max_kg"] is not None:
            assert row[1] == show(lift["current_estimated_max_kg"], "estimated_max")
            assert row[2] == f"{show(best['weight_kg'], 'weight_kg')} × {formatting.show(best['reps'], 'reps')}"
            assert row[3] == f"{formatting.show(lift['slope_per_week'], 'rate')}{NB}kg a week"
        assert row[4] == formatting.day(lift["last_trained"], read["today"])
    body = card.find_all("tbody")[0].elements()
    for tr_, lift in zip(body, read["lifts"]):
        lead = tr_.elements()[0]
        assert lead.classes == ["lead"] and [(a.attrs, a.text()) for a in lead.elements()] == [
            ({"href": f"/progress/{links.slug(lift['exercise'])}"}, lift["exercise"])]
        assert [c.attrs.get("data-label") for c in tr_.elements()[1:]] == LIFTS_HEAD[1:]
        assert [(a.attrs, a.text()) for a in tr_.elements()[4].find_all("a")] == [
            ({"href": f"/day/{lift['last_trained']}"}, formatting.day(lift["last_trained"], read["today"]))]
    pull = body[-1].elements()
    assert pull[1].classes == ["num", "muted", "wide-cell"] and pull[3].children == []
    assert not ZERO_KG.search(card.text()) and "No estimated max yet" not in progress_fixed
    assert "No best set yet" not in progress_fixed


MIXED = "0 kg sets of 1 to 10 reps, weighted sets above 10"  # the mixed lift (rev_b10), on both pages


def lift_state(state: str) -> None:
    """Lift sessions for a Lifts card state, on the pinned fixed day."""
    uid, today = only_user().id, date(2026, 11, 10)

    def log(n, *sets):
        assert "error" not in services.log_lifts(uid, {"date": (today - timedelta(days=n)).isoformat(), "sets": [
            {"exercise": name, "weight_kg": kg, "reps": reps} for name, kg, reps in sets]}, "claude")
    if state in ("no added weight", "all three"):
        log(1, ("Pull-Up", 0, 8), ("Pull-Up", 0, 6))
    if state in ("no set of 1 to 10 reps", "all three"):
        log(2, ("Bench Press", 40, 15))
    if state == "one session, no trend":
        log(0, ("Back Squat", 101.25, 5))  # a weight at 2 dp: weight_kg keeps it, estimated_max would not
    if state == MIXED:  # a set above 0 kg, so not reps_only, and no weighted set of 1 to 10 reps
        log(3, ("Pull-Up", 0, 8))
        log(1, ("Pull-Up", 10, 15))
    if state == "a flat trend":
        for n in (21, 14, 7, 0):
            log(n, ("Back Squat", 100, 5))
    if state == "all three":
        for n, kg in ((21, 90), (14, 95), (7, 100), (0, 105)):
            log(n, ("Back Squat", kg, 5))


@pytest.mark.parametrize("state, head, rows", [
    ("no added weight", ["Lift", "Estimated max", "Best set", "Last trained"],
     [["Pull-Up", "Needs added weight", f"8{NB}reps", "Mon 9 Nov"]]),
    ("no set of 1 to 10 reps", ["Lift", "Estimated max", "Last trained"],
     [["Bench Press", "Needs a weighted set of 1 to 10 reps", "Sun 8 Nov"]]),
    ("one session, no trend", ["Lift", "Estimated max", "Best set", "Last trained"],
     [["Back Squat", f"118.1{NB}kg", f"101.25{NB}kg × 5", "Tue 10 Nov"]]),
    (MIXED, ["Lift", "Estimated max", "Last trained"], [["Pull-Up", "Needs a weighted set of 1 to 10 reps", "Mon 9 Nov"]]),
    ("all three", LIFTS_HEAD, None),
])
def test_the_lifts_card_in_each_state(client, pinned, state, head, rows):
    """A lift never above 0 kg: "Needs added weight" and its most reps. A weighted lift with no set of 1 to 10 reps:
    "Needs a weighted set of 1 to 10 reps", best set and trend blank. A lift with no trend: no trend. A column no lift fills
    is not drawn; with each state beside a lift that has a trend, every column is, and the blank cells are empty."""
    sign_in(client)
    lift_state(state)
    page = client.get("/progress").text
    card = lower_card(page, "lifts-card")
    got_head, got = card_table(card)
    assert got_head == head
    if rows is None:
        slope = services.get_progress(only_user().id)["lifts"][0]["slope_per_week"]
        rows = [["Back Squat", f"122.5{NB}kg", f"105{NB}kg × 5", f"{formatting.show(slope, 'rate')}{NB}kg a week",
                 "Tue 10 Nov"],
                ["Bench Press", "Needs a weighted set of 1 to 10 reps", "", "", "Sun 8 Nov"],
                ["Pull-Up", "Needs added weight", f"8{NB}reps", "", "Mon 9 Nov"]]
        assert slope is not None and slope > 0
        blanks = [c for c in card.find_all("td") if not c.text()]
        assert len(blanks) == 3 and all(c.children == [] for c in blanks)
    assert got == rows
    assert not ZERO_KG.search(card.text())
    assert card.elements()[-1].text() == (LIFTS_LINE if "Trend" in head else LIFTS_SHORT)


def test_with_no_lifts_the_lifts_card_says_what_to_do(client, pinned):
    sign_in(client)
    card = lower_card(client.get("/progress").text, "lifts-card")
    assert card.attrs == {"class": "card lifts-card is-empty", "aria-labelledby": "lifts-title"}
    assert [shape(k) for k in card.elements()] == [("h2",), ("p", "empty-line")]
    assert card.elements()[1].text() == "No lifts logged yet. Tell your assistant what you lifted and it shows here."
    assert not card.find_all("table") and "Claude" not in card.text()


def test_progress_reads_reps_only_from_the_lift_history(client, pinned, monkeypatch):
    """The no-added-weight row's words and reps are lifts.history's reps_only, handed down by _trend: stood in for,
    the row follows it."""
    sign_in(client)
    lift_state("no set of 1 to 10 reps")
    real = web._trend

    def bare(user_id, today=None):
        return {**real(user_id, today), "reps_only": {"Bench Press": {"reps": 15, "date": "2026-11-08"}}}
    monkeypatch.setattr(web, "_trend", bare)
    _, rows = card_table(lower_card(client.get("/progress").text, "lifts-card"))
    assert rows == [["Bench Press", "Needs added weight", f"15{NB}reps", "Sun 8 Nov"]]


def test_the_estimated_max_goes_through_its_own_filter(client, pinned, monkeypatch):
    """get_progress rounds the estimated max to 1 dp already, so its filter shows only on a read at 2 dp: stood in
    for, 107.92 shows "107.9", as the contract's fixed day has it."""
    sign_in(client)
    lift_state("one session, no trend")
    real = services.get_progress

    def unrounded(uid, *args):
        out = real(uid, *args)
        return {**out, "lifts": [{**row, "current_estimated_max_kg": 107.92} for row in out["lifts"]]} if not args else out
    monkeypatch.setattr(services, "get_progress", unrounded)
    _, rows = card_table(lower_card(client.get("/progress").text, "lifts-card"))
    assert rows[0][:2] == ["Back Squat", f"107.9{NB}kg"]


# ---------------------------------------------------------------- B9's fix round

def test_the_trends_card_reads_the_glances_get_trends_so_one_page_shows_one_day(client, monkeypatch):
    """Read either side of midnight, one page shows one day's figures. The clock moves a day once the glance is
    read: a lift session 6 days back counts in the fixed day's 7 days and not in the next day's. The Trends card's
    Lift sessions still says what the Training tile says, from the one get_trends read."""
    clock = {"now": FIXED_NOW}
    monkeypatch.setattr(db, "utcnow", lambda: clock["now"])
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.log_lifts(uid, {"date": "2026-11-04", "sets": [
        {"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]}, "claude")
    real = web.glance_of

    async def then_midnight(user_id):
        out = await real(user_id)
        clock["now"] = FIXED_NOW + timedelta(days=1)
        return out
    monkeypatch.setattr(web, "glance_of", then_midnight)
    page = client.get("/progress").text
    [training] = [t["value"] for t in tiles_of(page) if t["classes"] == ["tile", "tile-training"]]
    shown = {q: v for q, v, _ in trends_of(page)}
    assert training == shown["Lift sessions"] == "1"
    assert services.get_trends(uid)["sessions"]["this_week"] == 0  # the next day's read would have said 0


def test_with_weigh_ins_in_the_28_days_and_no_chart_nothing_stands_in_the_charts_place(client, pinned, monkeypatch):
    """The words in the chart's place key on get_trends' count: one weigh-in, or none in the 28 days. With two or
    more and no chart drawn, nothing stands in its place: never "No weigh-ins in the last 28 days.", which would be
    false."""
    sign_in(client)
    bw_state("two in 28 days")
    monkeypatch.setattr(web.charts, "bodyweight_svg", lambda *a, **k: None)
    card = bw_node(client.get("/progress").text)
    assert services.get_trends(only_user().id)["bodyweight"]["weigh_ins_28d"] == 2
    assert [shape(e)[:2] for e in card.elements()[1].elements()] == [("dl", "callouts")]
    assert "No weigh-ins" not in card.text() and "One weigh-in" not in card.text()


# ---------------------------------------------------------------- stage 6, step 4: a lift's page (B10)

def lift_main(page: str):
    """A lift's page's main, read as a tree."""
    from .test_pages_text import parse  # noqa: PLC0415 - test_pages_text imports this file

    [main] = parse(page).find_all("main")
    return main


def lift_tiles(page: str) -> list[tuple]:
    """Each tile on a lift's page, in order: (tag, classes, hidden name, figure, label, note)."""
    [grid] = lift_main(page).find_all("div", "tiles")
    return [(t.tag, t.classes, slot(t, "sr"), slot(t, "tile-value"), slot(t, "tile-label"), slot(t, "tile-note"))
            for t in grid.elements()]


def lift_card(page: str, title: str):
    """The card in a lift page's main named by an h2 with these words."""
    [card] = [c for c in lift_main(page).elements() if c.is_(cls="card") and c.find_all("h2")
              and c.find_all("h2")[0].text() == title]
    return card


def card_shape(node) -> tuple[str, ...]:
    """An element's tag and classes, less is-empty: a card's place on the page, whatever its state."""
    return tuple(c for c in shape(node) if c != "is-empty")


def lift_lines(page: str, title: str) -> list[tuple[str, list[str]]]:
    """Each line of a lift page's Bests or Sessions card: its words as a person reads them, and its links."""
    return [(li.text(), [a.attrs["href"] for a in li.find_all("a")]) for li in lift_card(page, title).find_all("li")]


# A lift's page in main, top to bottom (contract lines 1559 to 1568).
LIFT_PAGE = [("div", "page-head"), ("p", "notice", "sample-banner"), ("div", "tiles"), ("section", "card", "chart-card"),
             ("section", "card", "bests-card"), ("section", "card")]
LIFT_EMPTY = "No sets of Back Squat logged yet. Tell your assistant what you lifted and it shows here."
NO_BESTS = "No new bests beyond the first session yet."
NO_SESSIONS = "No session has an estimated max yet."


def test_the_lift_page_on_the_fixed_day(client, progress_fixed):
    """Back Squat on the fixed day: the head and its back link, the banner, three tiles that are not controls, the
    Estimated max chart card, Bests newest first in the new best's own words, Sessions newest first. Every figure is
    get_progress' own through its filter, every day worded against get_progress' today."""
    page = client.get("/progress/back-squat").text
    main = lift_main(page)
    assert [shape(c) for c in main.elements()] == LIFT_PAGE
    head = main.elements()[0]
    assert [shape(e) for e in head.elements()] == [("h1",), ("nav", "day-nav")]
    assert head.elements()[0].text() == "Back Squat"
    assert [(a.attrs, a.text()) for a in head.find_all("a")] == [({"class": "back", "href": "/progress"}, "Progress")]
    assert head.elements()[1].attrs == {"class": "day-nav", "aria-label": "Back"}  # the back nav's name
    assert lift_tiles(page) == [
        ("div", ["tile", "tile-max"], "Estimated max now:", "107.9", "kg, estimated max now", None),
        ("div", ["tile", "tile-best"], "Best set:", "92.5", "kg × 5, best set", "Fri 6 Nov"),
        ("div", ["tile", "tile-sessions"], "Sessions:", "6", "sessions", "last trained Fri 6 Nov")]
    assert not main.elements()[2].find_all("a") and not main.elements()[2].find_all("button")
    chart = main.elements()[3]
    assert chart.attrs == {"class": "card chart-card", "aria-labelledby": "max-title"}
    assert [shape(e) for e in chart.elements()] == [("h2", "card-label", "tile-max"), ("p", "card-sub"),
                                                    ("p", "figure"), ("svg", "chart")]
    assert chart.elements()[0].attrs["id"] == "max-title" and chart.elements()[0].text() == "Estimated max"
    assert chart.elements()[1].text() == "Worked out from your sets, not a tested max"  # the sub, as ruled
    assert chart.elements()[2].text() == f"107.9{NB}kg" and chart.elements()[3].attrs["id"] == "progress-chart"
    assert main.elements()[4].attrs == {"class": "card bests-card", "aria-labelledby": "bests-title"}
    assert main.elements()[5].attrs == {"class": "card", "aria-labelledby": "sessions-title"}
    bests, sessions = lift_lines(page, "Bests"), lift_lines(page, "Sessions")
    # The Bests list is ul.bests, each line one span holding its words and link (a flex row keeps them as one);
    # the Sessions list is ul.entries of li.entry-line.
    [bests_ul] = lift_card(page, "Bests").find_all("ul")
    assert bests_ul.classes == ["bests"] and all(li.classes == [] for li in bests_ul.elements())
    assert all([shape(e) for e in li.elements()] == [("span",)] and li.elements()[0].find_all("a")
               and li.text() == li.elements()[0].text() for li in bests_ul.elements())
    [sessions_ul] = lift_card(page, "Sessions").find_all("ul")
    assert sessions_ul.classes == ["entries"] and sessions_ul.elements()
    assert all(li.classes == ["entry-line"] for li in sessions_ul.elements())
    assert bests[:2] == [(f"Fri 6 Nov: heaviest weight 92.5{NB}kg, up from 90{NB}kg", ["/day/2026-11-06"]),
                         (f"Fri 6 Nov: estimated max 107.9{NB}kg, up from 105{NB}kg", ["/day/2026-11-06"])]
    assert sessions[0] == (f"Fri 6 Nov: estimated max 107.9{NB}kg", ["/day/2026-11-06"])
    # Against the read: get_progress with the lift, and its today.
    read = services.get_progress(only_user().id, "Back Squat")
    lift, today = read["lift"], read["today"]
    assert today == "2026-11-10"
    show = lambda v, q, wide=False: formatting.show(v, q, page=True, widened=wide)  # noqa: E731
    best = lift["best_set"]
    assert [t[3:] for t in lift_tiles(page)] == [
        (formatting.show(lift["current_estimated_max_kg"], "estimated_max", unit=False), "kg, estimated max now", None),
        (formatting.show(best["weight_kg"], "weight_kg", unit=False), f"kg × {formatting.show(best['reps'], 'reps')}, best set",
         formatting.day(best["date"], today)),
        (formatting.show(lift["sessions"], "count"), "sessions", "last trained " + formatting.day(lift["last_trained"], today))]
    want = []
    for b in reversed(lift["bests"]):
        q = "estimated_max" if b["kind"] == "estimated_max" else "weight_kg"
        wide = q == "estimated_max" and formatting.show(b["value"], q) == formatting.show(b["previous"], q)
        want.append((f"{formatting.day(b['date'], today)}: {web.lifts.BEST_LABELS[b['kind']]} {show(b['value'], q, wide)}, "
                     f"up from {show(b['previous'], q, wide)}", [f"/day/{b['date']}"]))
    assert bests == want and len(bests) == 10
    assert sessions == [(f"{formatting.day(p['date'], today)}: estimated max {show(p['estimated_max_kg'], 'estimated_max')}",
                         [f"/day/{p['date']}"]) for p in reversed(lift["estimated_max_over_time"])]
    assert "(" not in lift_card(page, "Bests").text() and "was" not in lift_card(page, "Bests").text()
    assert not ZERO_KG.search(main.text()) and "Claude" not in main.text()


def lift_page_state(state: str) -> None:
    """Lift sessions for a lift page state, on the pinned fixed day."""
    uid, today = only_user().id, date(2026, 11, 10)

    def log(n, *sets):
        assert "error" not in services.log_lifts(uid, {"date": (today - timedelta(days=n)).isoformat(), "sets": [
            {"exercise": name, "weight_kg": kg, "reps": reps} for name, kg, reps in sets]}, "claude")
    if state == "weighted, with bests":
        for n, kg in ((21, 90), (14, 95), (7, 100), (0, 105)):
            log(n, ("Back Squat", kg, 5))
    if state == "no added weight":
        log(5, ("Pull-Up", 0, 10))
        log(1, ("Pull-Up", 0, 8), ("Pull-Up", 0, 6))
    if state == "no set of 1 to 10 reps":
        log(2, ("Bench Press", 40, 15))
    if state == "one session only":
        log(0, ("Deadlift", 101.25, 5))  # a weight at 2 dp: weight_kg keeps it
    if state == "no bests beyond the first":
        log(7, ("Back Squat", 100, 5))
        log(0, ("Back Squat", 95, 5))
    if state == MIXED:
        log(3, ("Pull-Up", 0, 8))
        log(1, ("Pull-Up", 10, 15))


# (state, the lift's address, its tiles as (classes, figure, label, note), the chart card's figure or its words,
# the Bests lines or their words, the Sessions lines or their words)
LIFT_STATES = [
    ("weighted, with bests", "back-squat",
     [(["tile", "tile-max"], "122.5", "kg, estimated max now", None),
      (["tile", "tile-best"], "105", "kg × 5, best set", "Tue 10 Nov"),
      (["tile", "tile-sessions"], "4", "sessions", "last trained Tue 10 Nov")],
     f"122.5{NB}kg",
     [f"Tue 10 Nov: heaviest weight 105{NB}kg, up from 100{NB}kg", f"Tue 10 Nov: estimated max 122.5{NB}kg, up from 116.7{NB}kg",
      f"Tue 3 Nov: heaviest weight 100{NB}kg, up from 95{NB}kg", f"Tue 3 Nov: estimated max 116.7{NB}kg, up from 110.8{NB}kg",
      f"Tue 27 Oct: heaviest weight 95{NB}kg, up from 90{NB}kg", f"Tue 27 Oct: estimated max 110.8{NB}kg, up from 105{NB}kg"],
     [f"Tue 10 Nov: estimated max 122.5{NB}kg", f"Tue 3 Nov: estimated max 116.7{NB}kg",
      f"Tue 27 Oct: estimated max 110.8{NB}kg", f"Tue 20 Oct: estimated max 105{NB}kg"]),
    ("no added weight", "pull-up",
     [(["tile", "tile-max", "is-empty"], "Needs added weight", None, None),
      (["tile", "tile-best"], "10", "reps, best set", "Thu 5 Nov"),
      (["tile", "tile-sessions"], "2", "sessions", "last trained Mon 9 Nov")],
     "The chart starts from a set with added weight.", NO_BESTS, NO_SESSIONS),
    ("no set of 1 to 10 reps", "bench-press",
     [(["tile", "tile-max", "is-empty"], "Needs a weighted set of 1 to 10 reps", None, None),
      (["tile", "tile-best", "is-empty"], "No weighted set of 1 to 10 reps yet", None, None),
      (["tile", "tile-sessions"], "1", "session", "last trained Sun 8 Nov")],
     "The chart starts from a weighted set of 1 to 10 reps.", NO_BESTS, NO_SESSIONS),
    ("one session only", "deadlift",
     [(["tile", "tile-max"], "118.1", "kg, estimated max now", None),
      (["tile", "tile-best"], "101.25", "kg × 5, best set", "Tue 10 Nov"),
      (["tile", "tile-sessions"], "1", "session", "last trained Tue 10 Nov")],
     f"118.1{NB}kg", NO_BESTS, [f"Tue 10 Nov: estimated max 118.1{NB}kg"]),
    ("no bests beyond the first", "back-squat",
     [(["tile", "tile-max"], "116.7", "kg, estimated max now", None),
      (["tile", "tile-best"], "100", "kg × 5, best set", "Tue 3 Nov"),
      (["tile", "tile-sessions"], "2", "sessions", "last trained Tue 10 Nov")],
     f"116.7{NB}kg", NO_BESTS, [f"Tue 10 Nov: estimated max 110.8{NB}kg", f"Tue 3 Nov: estimated max 116.7{NB}kg"]),
    # A set of 8 at 0 kg, then 15 at 10 kg: not reps_only (a set above 0 kg), and no weighted set of 1 to 10 reps.
    (MIXED, "pull-up",
     [(["tile", "tile-max", "is-empty"], "Needs a weighted set of 1 to 10 reps", None, None),
      (["tile", "tile-best", "is-empty"], "No weighted set of 1 to 10 reps yet", None, None),
      (["tile", "tile-sessions"], "2", "sessions", "last trained Mon 9 Nov")],
     "The chart starts from a weighted set of 1 to 10 reps.", NO_BESTS, NO_SESSIONS),
]


@pytest.mark.parametrize("state, slug, tiles, chart, bests, sessions", LIFT_STATES, ids=[s[0] for s in LIFT_STATES])
def test_the_lift_page_in_each_state(client, pinned, state, slug, tiles, chart, bests, sessions):
    """A weighted lift with bests; one never above 0 kg ("Needs added weight", its most reps with their day); a
    weighted lift with no set of 1 to 10 reps; one session; no best beyond the first. The tiles are never controls,
    a state with no figure is an empty tile with its words, the chart card and the two lists say what they hold or
    why there is none. Never 0 kg."""
    sign_in(client)
    lift_page_state(state)
    page = client.get(f"/progress/{slug}").text
    main = lift_main(page)
    assert [card_shape(c) for c in main.elements()] == [s for s in LIFT_PAGE if s[1] != "notice"]
    assert [t[1] for t in lift_tiles(page)] == [t[0] for t in tiles] and all(t[0] == "div" for t in lift_tiles(page))
    assert [t[3:] for t in lift_tiles(page)] == [t[1:] for t in tiles]
    card = lift_card(page, "Estimated max")
    if chart.endswith("kg"):
        assert "is-empty" not in card.classes and card.find_all("svg") and card.find_all("p", "figure")[0].text() == chart
    else:
        assert card.classes == ["card", "chart-card", "is-empty"] and not card.find_all("svg")
        assert [e.text() for e in card.find_all("p", "empty-line")] == [chart]
    for title, want in (("Bests", bests), ("Sessions", sessions)):
        shown = lift_card(page, title)
        if isinstance(want, str):
            assert "is-empty" in shown.classes and [e.text() for e in shown.find_all("p", "empty-line")] == [want]
            assert not shown.find_all("li")
        else:
            assert "is-empty" not in shown.classes and [line for line, _ in lift_lines(page, title)] == want
            assert all(len(links_) == 1 and links_[0].startswith("/day/") for _, links_ in lift_lines(page, title))
    assert not ZERO_KG.search(main.text()) and "No estimated max yet" not in page and "No best set yet" not in page


def test_with_nothing_logged_the_lift_page_is_one_card_saying_what_to_do(client, pinned):
    """No sets of the lift: the head, then one card with no title and the line saying "your assistant". An unknown lift
    is still the 404 page."""
    sign_in(client)
    page = client.get("/progress/back-squat").text
    main = lift_main(page)
    assert [shape(c) for c in main.elements()] == [("div", "page-head"), ("div", "card", "is-empty")]
    card = main.elements()[1]
    assert card.attrs == {"class": "card is-empty"}
    assert [(shape(e), e.text()) for e in card.elements()] == [(("p", "empty-line"), LIFT_EMPTY)]
    assert "Claude" not in main.text()
    missing = client.get("/progress/not-a-lift")
    assert missing.status_code == 404 and "There is nothing here." in missing.text and "lift-page" not in missing.text


def test_the_lift_page_carries_the_sample_banner_as_progress_does(client, pinned, monkeypatch):
    """The banner from sample_state, between the page head and the tiles; none without sample rows."""
    sign_in(client)
    lift_page_state("one session only")
    assert [card_shape(c) for c in lift_main(client.get("/progress/deadlift").text).elements()][:2] == [
        ("div", "page-head"), ("div", "tiles")]
    monkeypatch.setattr(services, "sample_state", lambda *a: {"empty": False, "sample_rows": 3})
    main = lift_main(client.get("/progress/deadlift").text)
    assert [card_shape(c) for c in main.elements()] == LIFT_PAGE
    assert main.elements()[1].text() == "Sample data is on this account. Remove it on Settings."


def test_a_best_with_no_previous_shows_its_value_only(client, pinned, monkeypatch):
    """metrics.prs always gives a previous, so the read is stood in for: a best with none shows its value alone, no
    "up from", no brackets."""
    sign_in(client)
    lift_page_state("one session only")
    real = services.get_progress

    def lone(uid, *args):
        out = real(uid, *args)
        bests = [{"date": "2026-11-10", "kind": "estimated_max", "value": 118.1, "previous": None},
                 {"date": "2026-11-10", "kind": "weight", "value": 101.25, "previous": None}]
        return {**out, "lift": {**out["lift"], "bests": bests}} if args else out
    monkeypatch.setattr(services, "get_progress", lone)
    assert lift_lines(client.get("/progress/deadlift").text, "Bests") == [
        (f"Tue 10 Nov: heaviest weight 101.25{NB}kg", ["/day/2026-11-10"]),
        (f"Tue 10 Nov: estimated max 118.1{NB}kg", ["/day/2026-11-10"])]


def test_the_lift_page_reads_reps_only_on_get_progress_date(client, monkeypatch):
    """One date for the page: get_progress' today. The clock moves a day once get_progress has answered, and the
    reps_only read still names get_progress' date, never the next day's. The tile's day is worded against it."""
    from app.services import lifts as lift_service  # noqa: PLC0415

    clock = {"now": FIXED_NOW}
    monkeypatch.setattr(db, "utcnow", lambda: clock["now"])
    sign_in(client)
    lift_page_state("no added weight")
    real_progress, real_history, named = services.get_progress, lift_service.history, []

    def then_midnight(*args):
        out = real_progress(*args)
        clock["now"] = FIXED_NOW + timedelta(days=1)
        return out

    def history(s, user_id, today):
        named.append(today)
        return real_history(s, user_id, today)
    monkeypatch.setattr(services, "get_progress", then_midnight)
    monkeypatch.setattr(lift_service, "history", history)
    page = client.get("/progress/pull-up").text
    assert len(named) >= 2 and set(named) == {date(2026, 11, 10)}
    assert [t[3:] for t in lift_tiles(page)][1] == ("10", "reps, best set", "Thu 5 Nov")
    assert users.today_for(only_user()) == date(2026, 11, 11)  # the next day's read would have named the 11th


def test_the_lift_pages_estimated_max_goes_through_its_own_filter(client, pinned, monkeypatch):
    """get_progress rounds each estimated max to 1 dp already, so the filter shows only on a read at 2 dp: stood in for,
    107.92 shows "107.9" on the tile and the chart card, and each session's estimated max at 1 dp. weight_kg would keep
    every decimal."""
    sign_in(client)
    lift_page_state("one session only")
    real = services.get_progress

    def unrounded(uid, *args):
        out = real(uid, *args)
        points = [{"date": "2026-11-03", "estimated_max_kg": 80.04}, {"date": "2026-11-10", "estimated_max_kg": 118.13}]
        return {**out, "lift": {**out["lift"], "current_estimated_max_kg": 107.92,
                                "estimated_max_over_time": points}} if args else out
    monkeypatch.setattr(services, "get_progress", unrounded)
    page = client.get("/progress/deadlift").text
    assert lift_tiles(page)[0][3:5] == ("107.9", "kg, estimated max now")
    assert lift_card(page, "Estimated max").find_all("p", "figure")[0].text() == f"107.9{NB}kg"
    assert [line for line, _ in lift_lines(page, "Sessions")] == [
        f"Tue 10 Nov: estimated max 118.1{NB}kg", f"Tue 3 Nov: estimated max 80{NB}kg"]


# ---------------------------------------------------------------- the Progress batch

BANNER = '<p class="notice sample-banner">Sample data is on this account.'
NO_ACCOUNT = {"error": "no_account", "message": "x", "fix": "y"}


def banner_pages(client) -> dict[str, str]:
    """Every page that draws the sample banner, by an address of each, for the one signed-in person: Settings, the
    day page, Foods, Progress, a lift's page and Goals."""
    assert "error" not in services.log_lifts(only_user().id, {"sets": [
        {"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]}, "claude")
    return {"settings": "/settings", "day": f"/day/{today_of(only_user()).isoformat()}", "foods": "/foods",
            "progress": "/progress", "lift": "/progress/back-squat", "goals": "/goals"}


@pytest.mark.parametrize("name", ["settings", "day", "foods", "progress", "lift", "goals"])
def test_a_failed_sample_state_read_draws_the_page_with_no_banner(client, monkeypatch, name):
    """Ruled (rev_b10 section 2): sample_state answering no_account (the account gone between two reads of one
    request) is not a 500. Each page that reads sample_rows draws as with none: no banner (Settings: no Remove
    sample data form). With sample rows each shows."""
    sign_in(client)
    address = banner_pages(client)[name]
    mark = 'action="/settings/sample/remove"' if name == "settings" else BANNER
    monkeypatch.setattr(services, "sample_state", lambda *a: {"empty": False, "sample_rows": 3})
    shown = client.get(address)
    assert shown.status_code == 200 and mark in shown.text, name
    monkeypatch.setattr(services, "sample_state", lambda *a: NO_ACCOUNT)
    r = client.get(address)
    assert r.status_code == 200, (name, r.status_code)
    assert mark not in r.text and "sample-banner" not in r.text
    assert web.sample_rows_of(NO_ACCOUNT) == 0 and web.sample_rows_of({"empty": True, "sample_rows": 0}) == 0


def test_with_a_failed_sample_state_today_is_not_a_new_accounts_page(client, monkeypatch):
    """The day page reads sample_state for its banner and for whether the account is new. A failed read is neither:
    the page draws as for an account with entries, with no banner."""
    sign_in(client)
    today = f"/day/{today_of(only_user()).isoformat()}"
    offer = 'id="today-card-title">Nothing logged yet</h2>'
    assert offer in client.get(today).text  # a new account: the offer of sample data
    monkeypatch.setattr(services, "sample_state", lambda *a: NO_ACCOUNT)
    page = client.get(today).text
    assert offer not in page and "sample-banner" not in page and '<section class="card hero"' in page


def test_a_failed_glance_read_draws_no_glance_and_the_page_still_answers(client, monkeypatch):
    """glance_of's reads failing, on the first try or on the second (after two dates): no glance, and the day page
    and Progress still answer. Carried from the Today passes (rev_b7 N1)."""
    sign_in(client)
    uid = only_user().id
    real_trends, real_days = services.get_trends, services.get_days
    calls = {"trends": 0, "days": 0}
    for failing, on_call in (("days", 1), ("trends", 1), ("days", 2), ("trends", 2)):
        calls.update(trends=0, days=0)

        def trends(user_id, failing=failing, on_call=on_call):
            calls["trends"] += 1
            if failing == "trends" and calls["trends"] == on_call:
                return NO_ACCOUNT
            out = real_trends(user_id)
            # Two dates on the first try, so a second try is made.
            return {**out, "today": "2020-01-01"} if on_call == 2 and calls["trends"] == 1 else out

        def days(user_id, failing=failing, on_call=on_call):
            calls["days"] += 1
            return NO_ACCOUNT if failing == "days" and calls["days"] == on_call else real_days(user_id)
        monkeypatch.setattr(services, "get_trends", trends)
        monkeypatch.setattr(services, "get_days", days)
        assert asyncio.run(web.glance_of(uid)) is None, (failing, on_call)
        assert calls == {"trends": on_call, "days": on_call}, (failing, on_call, calls)  # both read, each try
        for address in (f"/day/{today_of(only_user()).isoformat()}", "/progress"):
            calls.update(trends=0, days=0)
            r = client.get(address)
            assert r.status_code == 200 and glance_node(r.text) is None, (failing, on_call, address)
    monkeypatch.setattr(services, "get_trends", real_trends)
    monkeypatch.setattr(services, "get_days", real_days)
    assert asyncio.run(web.glance_of(uid)) is not None


def test_the_aside_count_goes_through_its_filter(client, monkeypatch):
    """The Last 28 days card's aside prints get_days' count through show, so a count from 1,000 carries its comma.
    Below 1,000 a raw count reads the same, so the read is stood in for (rev_b7 K22)."""
    sign_in(client)
    real = services.get_days
    monkeypatch.setattr(services, "get_days", lambda uid: {**real(uid), "lift_sessions_28d": 1234})
    head = daygrid_of(day_of(client, today_of(only_user()).isoformat()))["head"]
    assert [words for tag, classes, _, words in head if classes == ["aside"]] == ["1,234 lift sessions"]


def test_a_note_stored_with_crlf_is_no_change_when_saved_as_drawn(client, every_kind):
    """A note an assistant stored with CRLF (rules.text keeps CR) is posted back by a browser as drawn, CRLF, and read
    as LF (posted_form): a Save with no change is "unchanged", and nothing is rewritten. A cardio estimate is not
    worked out again, though the weigh-in has moved since it was made. Carried from the Today passes (rev_b5b R1)."""
    for kind, model in (("lift_session", LiftSession), ("cardio_entry", CardioEntry)):
        entry_id = every_kind[kind]
        with db.session_scope() as s:
            s.get(model, entry_id).notes = "old\r\nnote"
            s.get(Bodyweight, every_kind["bodyweight"]).kg = 95.0
        before = next(x for x in rows(model, only_user().id) if x.id == entry_id)
        drawn = {f["name"]: f["value"] or "" for f in controls_of(edit_of(client, kind, entry_id))}
        r = post(client, f"/entries/{kind}/{entry_id}/edit", **{**drawn, "notes": "old\r\nnote"})
        assert location(r) == SITE + f"/day/{every_kind['today']}?saved=unchanged", kind
        after = next(x for x in rows(model, only_user().id) if x.id == entry_id)
        assert after.notes == "old\r\nnote"
        if kind == "cardio_entry":
            assert (after.estimated_kcal, after.estimate_weight_kg) == (before.estimated_kcal, before.estimate_weight_kg)
        # A real change to the note is still saved.
        r = post(client, f"/entries/{kind}/{entry_id}/edit", **{**drawn, "notes": "old\r\nnote, new"})
        assert location(r) == SITE + f"/day/{every_kind['today']}?saved={kind}"


def weight_sheet_panel(page: str) -> list[str]:
    return sheets_of(page)["sheet-weight"]["panel"]


@pytest.mark.parametrize("where", ["today", "progress"])
def test_the_weight_sheet_says_no_weigh_ins_only_when_there_are_none(client, pinned, monkeypatch, where):
    """The Weight sheet's panel, with no chart: one weigh-in in the 28 days, its words; none, "No weigh-ins in the
    last 28 days."; two or more (a chart that is not drawn), nothing in its place, never the false "none". Before
    this batch, two or more with no chart read "No weigh-ins in the last 28 days."."""
    sign_in(client)
    address = "/day/2026-11-10" if where == "today" else "/progress"
    bw_state("none in 28 days")  # 40 and 45 days back
    assert weight_sheet_panel(client.get(address).text) == ["Smoothed weight", "No weigh-ins in the last 28 days."]
    for on, kg in (("2026-11-07", "80.6"), ("2026-11-10", "80.2")):
        assert "error" not in services.log_bodyweight(only_user().id, {"kg": kg, "date": on}, "claude")
    assert services.get_trends(only_user().id)["bodyweight"]["weigh_ins_28d"] == 2
    assert weight_sheet_panel(client.get(address).text) == ["Smoothed weight", "svg"]
    monkeypatch.setattr(web.charts, "weight_sheet_svg", lambda *a, **k: None)
    page = client.get(address).text
    assert weight_sheet_panel(page) == ["Smoothed weight"]
    assert "No weigh-ins in the last 28 days" not in sheets_of(page)["sheet-weight"]["node"].text()


def test_the_lifts_reason_cell_spans_its_row_on_a_phone(client, pinned):
    """P1: a lift's reason sits in a wide-cell, which spans the stacked row on a phone (style.css' table.rows
    td.wide-cell), so it never runs over Last trained. Both reasons, and no other cell."""
    sign_in(client)
    lift_state("all three")
    card = lower_card(client.get("/progress").text, "lifts-card")
    wide = [(c.classes, c.text()) for c in card.find_all("td") if "wide-cell" in c.classes]
    assert wide == [(["num", "muted", "wide-cell"], "Needs a weighted set of 1 to 10 reps"),
                    (["num", "muted", "wide-cell"], "Needs added weight")]


def test_each_progress_table_is_named_by_its_cards_h2(progress_fixed):
    """P5: each table on Progress' cards is named by its card's h2, by aria-labelledby. Row headers stay td."""
    for cls, title in (("trends", "trends-title"), ("cardio-card", "cardio-title"), ("lifts-card", "lifts-title")):
        card = lower_card(progress_fixed, cls)
        [table] = card.find_all("table")
        assert table.attrs.get("aria-labelledby") == title == card.attrs["aria-labelledby"], cls
        assert [h.attrs.get("id") for h in card.find_all("h2")] == [title]
        assert all(r.elements()[0].tag == "td" for r in table.find_all("tbody")[0].elements())


def test_the_cardio_card_counts_the_pages_own_28_days_read_across_midnight(client, monkeypatch):
    """P2 and T9: the glance reads Tue 10 Nov, and get_cardio may read a moment later on Wed 11 Nov, so its 28 days
    would leave out the walk on 14 Oct that Trends counts. get_cardio is then read once more: on the page's day,
    the card draws that day's figures. Still on another day, no Cardio card for that render, and the rest draws."""
    clock = {"now": FIXED_NOW}
    monkeypatch.setattr(db, "utcnow", lambda: clock["now"])
    sign_in(client)
    uid = only_user().id
    for name, minutes, on in (("Outdoor Walk", 30, "2026-10-14"), ("Outdoor Run", 20, "2026-11-10")):
        assert "error" not in services.log_cardio(uid, {"exercise": name, "minutes": minutes, "date": on}, "claude")
    real, later = services.get_cardio, FIXED_NOW + timedelta(days=1)
    for name, at, calls, drawn in (("both on the page's day", [FIXED_NOW], 1, True),
                                   ("the first after midnight", [later, FIXED_NOW], 2, True),
                                   ("both after midnight", [later, later], 2, False)):
        seen = []

        def cardio(user_id, *args, at=at, seen=seen):
            clock["now"] = at[min(len(seen), len(at) - 1)]
            seen.append(clock["now"])
            out = real(user_id, *args)
            clock["now"] = FIXED_NOW
            return out
        monkeypatch.setattr(services, "get_cardio", cardio)
        page = client.get("/progress").text
        assert len(seen) == calls, name
        assert ('class="card cardio-card' in page) == drawn, name
        assert 'id="trends"' in page and '<h2 id="lifts-title">Lifts</h2>' in page and glance_node(page) is not None
        if drawn:
            _, rows = card_table(lower_card(page, "cardio-card"))
            assert rows == [["Outdoor Run", "1", "20", "Tue 10 Nov"], ["Outdoor Walk", "1", "30", "Wed 14 Oct"]], name
    monkeypatch.setattr(services, "get_cardio", real)
    clock["now"] = later
    assert [a["sessions_28d"] for a in services.get_cardio(uid)["activities"]] == [1, 0]  # the next day leaves it out


def test_the_trends_card_draws_from_the_glance_when_a_later_get_trends_would_fail(client, pinned, monkeypatch):
    """T10: the page's own get_trends is read only with no glance. With the glance's read answering and any later
    one failing, the Trends card still draws, from the one read. With no glance (get_days failing), the page reads
    get_trends itself, and the card draws from that read."""
    sign_in(client)
    progress_state("the fixed day")
    real, calls = services.get_trends, []

    def first_only(uid):
        calls.append(uid)
        return real(uid) if len(calls) == 1 else NO_ACCOUNT
    monkeypatch.setattr(services, "get_trends", first_only)
    page = client.get("/progress").text
    assert len(calls) == 1 and glance_node(page) is not None
    assert trends_of(page)[0] == ("Smoothed trend", f"79.2{NB}kg", "")
    calls.clear()
    monkeypatch.setattr(services, "get_trends", lambda uid: (calls.append(uid), real(uid))[1])
    monkeypatch.setattr(services, "get_days", lambda uid: NO_ACCOUNT)
    page = client.get("/progress").text
    assert len(calls) == 2 and glance_node(page) is None  # once in glance_of, once the page's own
    assert trends_of(page)[0] == ("Smoothed trend", f"79.2{NB}kg", "")


def test_trend_with_no_account_answers_empty(client):
    """T4: _trend's answer for an account that is gone: no best sets, no reps_only, no chart. Every key the page
    reads is there."""
    assert web._trend(987654) == {"best_sets": {}, "reps_only": {}, "bodyweight": None}


def test_the_maintenance_row_falls_back_to_its_own_words(client, pinned, monkeypatch):
    """T5: with no estimate and no maintenance_note from the read, the Maintenance row says "Not enough logged yet
    for an estimate.", its value blank and no tag."""
    sign_in(client)
    real = services.get_trends
    monkeypatch.setattr(services, "get_trends", lambda uid: {**real(uid), "maintenance": None, "maintenance_note": None})
    page = client.get("/progress").text
    assert trends_of(page)[-1] == ("Maintenance", "", "Not enough logged yet for an estimate.")
    assert not lower_card(page, "trends").find_all(cls="tag-estimate")


def test_a_flat_lift_trend_reads_0_kg_a_week(client, pinned):
    """T2: the same weight four weeks running is a slope of 0: the Trend reads "0 kg a week", never blank."""
    sign_in(client)
    lift_state("a flat trend")
    assert services.get_progress(only_user().id)["lifts"][0]["slope_per_week"] == 0
    card = lower_card(client.get("/progress").text, "lifts-card")
    assert card_table(card) == (LIFTS_HEAD, [["Back Squat", f"116.7{NB}kg", f"100{NB}kg × 5", f"0{NB}kg a week",
                                              "Tue 10 Nov"]])
    assert card.elements()[-1].text() == LIFTS_LINE


def test_a_last_day_in_the_year_before_carries_its_year(client, monkeypatch):
    """T3: on Tue 5 Jan 2027, a lift and a run last done on Sun 20 Dec 2026 read "Sun 20 Dec 2026" in the Lifts card's
    Last trained and the Cardio card's Last."""
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2027, 1, 5, 12, 0, tzinfo=timezone.utc))
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.log_lifts(uid, {"date": "2026-12-20", "sets": [
        {"exercise": "Back Squat", "weight_kg": 100, "reps": 5}]}, "claude")
    assert "error" not in services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5,
                                                    "date": "2026-12-20"}, "claude")
    page = client.get("/progress").text
    assert card_table(lower_card(page, "lifts-card"))[1] == [["Back Squat", f"116.7{NB}kg", f"100{NB}kg × 5",
                                                              "Sun 20 Dec 2026"]]
    assert card_table(lower_card(page, "cardio-card"))[1] == [["Outdoor Run", "1", "30", "6:00", "Sun 20 Dec 2026"]]


def test_high_latest_and_low_each_show_their_own_weigh_in(client, pinned):
    """rev_b8 finding 2: weigh-ins of 81, 79 and 80 kg at 5, 3 and 0 days back, so High, Latest and Low are three
    weigh-ins. Each callout shows its own, with its date."""
    sign_in(client)
    uid, today = only_user().id, date(2026, 11, 10)
    for n, kg in ((5, "81"), (3, "79"), (0, "80")):
        assert "error" not in services.log_bodyweight(uid, {"kg": kg, "date": (today - timedelta(days=n)).isoformat()},
                                                      "claude")
    card = body_weight_card(client.get("/progress").text)
    assert callouts_of(card) == expected_callouts(uid) == [
        ([], "High", f"81{NB}kg", "Thu 5 Nov"), ([], "Latest", f"80{NB}kg", "Tue 10 Nov"),
        (["is-key"], "Trend", f"80.7{NB}kg", "Smoothed"), ([], "Low", f"79{NB}kg", "Sat 7 Nov")]


def test_the_maintenance_sheet_counts_its_logged_days_not_its_window(client, pinned):
    """rev_b8 finding 3: food on 15 of the last 21 days. The sheet says "worked out from 15 logged days": the read's
    days_logged, which differs from its window_days (21) here."""
    sign_in(client)
    uid, today = only_user().id, date(2026, 11, 10)
    for n in (21, 14, 7, 0):
        assert "error" not in services.log_bodyweight(uid, {"kg": "80", "date": (today - timedelta(days=n)).isoformat()},
                                                      "claude")
    for n in range(15):
        assert "error" not in services.log_food(uid, {"date": (today - timedelta(days=n)).isoformat(), "entries": [
            {"description": "Dal", "kcal": 2000}]}, "claude")
    m = services.get_trends(uid)["maintenance"]
    assert (m["days_logged"], m["window_days"]) == (15, 21)
    sheet = sheets_of(client.get("/progress").text)["sheet-maintenance"]
    assert sheet["sentence"] == (f"About 2,000{NB}kcal a day, worked out from 15 logged days. Over that window intake "
                                 f"averaged 2,000{NB}kcal a day and the 7-day average weight moved 0{NB}kg.")


def test_the_trend_callout_is_the_reads_not_the_charts(client, pinned, monkeypatch):
    """rev_b8 finding 6: the chart's last smoothed point stood in at 85.4, a value that shows differently from
    get_trends' smoothed weight. The Trend callout still shows the read's."""
    sign_in(client)
    bw_state("two in 28 days")
    real = web.progress_data.bodyweight_chart

    def stood_in(*args):
        chart = real(*args)
        chart["points"] = [{**p, "smoothed": 85.4} for p in chart["points"]]
        return chart
    monkeypatch.setattr(web.progress_data, "bodyweight_chart", stood_in)
    page = client.get("/progress").text
    assert 'id="bodyweight-chart"' in page
    read = formatting.show(services.get_trends(only_user().id)["bodyweight"]["smoothed"], "bodyweight_derived",
                           unit=False)
    assert read != "85.4"
    assert callouts_of(body_weight_card(page))[2] == (["is-key"], "Trend", f"{read}{NB}kg", "Smoothed")


def test_the_lift_chart_is_drawn_on_get_progress_today(client, monkeypatch):
    """rev_b10 R6: the chart takes get_progress' today. On Tue 5 Jan 2027, the last session is 8 days back, in the
    year before: the chart drawn on get_progress' today names its days with their year ("Mon 28 Dec 2026"), and one
    drawn without it (on its own last day) would not. The page's chart is the one drawn on get_progress' today."""
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2027, 1, 5, 12, 0, tzinfo=timezone.utc))
    sign_in(client)
    for on, kg in (("2026-12-14", 90), ("2026-12-21", 95), ("2026-12-28", 100)):
        assert "error" not in services.log_lifts(only_user().id, {"date": on, "sets": [
            {"exercise": "Back Squat", "weight_kg": kg, "reps": 5}]}, "claude")
    read = services.get_progress(only_user().id, "Back Squat")
    assert read["today"] == "2027-01-05"
    chart = web.progress_data.lift_chart(web._lift_points(read["lift"]), read["lift"]["goals"], "Back Squat")
    on_read, on_its_own = str(web.charts.chart_svg(chart, today=date(2027, 1, 5))), str(web.charts.chart_svg(chart))
    assert "Mon 28 Dec 2026" in on_read and "2026" not in on_its_own  # so the test can tell them apart
    page = client.get("/progress/back-squat").text
    svg = re.search(r'<svg class="chart" id="progress-chart".*?</svg>', page, re.S).group(0)
    assert svg == on_read


def test_the_lift_page_words_its_days_against_get_progress_today_across_a_year_end(client, monkeypatch):
    """rev_b10 R10: the page hands its today to the template. get_progress answers on Thu 31 Dec 2026 and the clock
    then moves to 1 Jan 2027: the head's days still read without a year, as 31 Dec's page reads them."""
    clock = {"now": datetime(2026, 12, 31, 12, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(db, "utcnow", lambda: clock["now"])
    sign_in(client)
    for on, kg in (("2026-12-24", 95), ("2026-12-31", 100)):
        assert "error" not in services.log_lifts(only_user().id, {"date": on, "sets": [
            {"exercise": "Back Squat", "weight_kg": kg, "reps": 5}]}, "claude")
    real = services.get_progress

    def then_new_year(*args):
        out = real(*args)
        clock["now"] = datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)
        return out
    monkeypatch.setattr(services, "get_progress", then_new_year)
    page = client.get("/progress/back-squat").text
    assert [t[5] for t in lift_tiles(page)] == [None, "Thu 31 Dec", "last trained Thu 31 Dec"]
    assert [line for line, _ in lift_lines(page, "Sessions")] == [
        f"Thu 31 Dec: estimated max 116.7{NB}kg", f"Thu 24 Dec: estimated max 110.8{NB}kg"]
    assert "2026" not in lift_main(page).text()


def test_a_best_whose_previous_is_0_still_shows_up_from(client, pinned, monkeypatch):
    """rev_b10 R9: a previous of 0 is a previous, not none: the line still says "up from". The read is stood in."""
    sign_in(client)
    lift_page_state("one session only")
    real = services.get_progress

    def from_zero(uid, *args):
        out = real(uid, *args)
        bests = [{"date": "2026-11-10", "kind": "weight", "value": 101.25, "previous": 0.0}]
        return {**out, "lift": {**out["lift"], "bests": bests}} if args else out
    monkeypatch.setattr(services, "get_progress", from_zero)
    assert lift_lines(client.get("/progress/deadlift").text, "Bests") == [
        (f"Tue 10 Nov: heaviest weight 101.25{NB}kg, up from 0{NB}kg", ["/day/2026-11-10"])]


def test_the_lift_page_with_nothing_logged_carries_the_sample_banner(client, pinned, monkeypatch):
    """rev_b10 R11: with sample rows and none of this lift's sets, the banner still shows, after the head and before
    the one card."""
    sign_in(client)
    monkeypatch.setattr(services, "sample_state", lambda *a: {"empty": False, "sample_rows": 3})
    main = lift_main(client.get("/progress/back-squat").text)
    assert [shape(c) for c in main.elements()] == [("div", "page-head"), ("p", "notice", "sample-banner"),
                                                   ("div", "card", "is-empty")]
    assert main.elements()[1].text() == "Sample data is on this account. Remove it on Settings."


def test_the_zero_kg_check_sees_a_tiles_figure_and_unit_in_two_spans(client, pinned, monkeypatch):
    """rev_b10's note: a tile reads its figure and its unit in two spans, which main.text() joins with a plain space.
    ZERO_KG sees "0" and "kg" there too: an estimated max stood in at 0.04 kg reads "0" "kg" on the tile."""
    sign_in(client)
    lift_page_state("one session only")
    real = services.get_progress
    monkeypatch.setattr(services, "get_progress", lambda uid, *args: (lambda out: {
        **out, "lift": {**out["lift"], "current_estimated_max_kg": 0.04}} if args else out)(real(uid, *args)))
    page = client.get("/progress/deadlift").text
    main = lift_main(page)
    assert lift_tiles(page)[0][3:5] == ("0", "kg, estimated max now")
    assert ZERO_KG.search(main.text())
    assert ZERO_KG.search(f"0{NB}kg") and ZERO_KG.search("0 kg") and not ZERO_KG.search("100 kg")


# ---------------------------------------------------------------- the fix round after B15 and B16

def test_weigh_ins_imported_in_lb_read_back_in_kg_once_on_their_day_page_and_on_progress(client):
    """rev_impb, the merged tree's section 9: no web test read an imported row back through a page, so a pound
    converted twice passed every one. Weigh-ins imported in lb through import_entries: each stored kg is the pound
    converted once (lb x the service's own factor, kept at 2 dp), each day page shows get_log's own weigh-in, and
    Progress shows get_trends' own figures."""
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.update_settings(uid, {"time_zone": "Asia/Kolkata"}, "web")
    today = today_of(only_user())
    pounds = {(today - timedelta(days=n)).isoformat(): lb for n, lb in ((6, 176.4), (5, 175.8), (4, 175.2), (3, 174.6),
                                                                        (2, 174))}
    result = services.import_entries(uid, {"rows": [{"kind": "bodyweight", "date": d, "weight": lb}
                                                    for d, lb in pounds.items()],
                                           "weight_unit": "lb", "dry_run": False}, "claude")
    assert "error" not in result and result["counts"]["bodyweight"]["written"] == len(pounds), result
    factor = services.imports.UNITS["weight_unit"]["lb"]
    for on, lb in pounds.items():
        [day] = services.get_log(uid, on)["days"]
        kg = day["weigh_in"]["kg"]
        assert abs(kg - lb * factor) < 0.005, (on, lb, kg)  # converted once
        [entry] = entries_of(side_card(client.get(f"/day/{on}").text, "Weigh-in"))
        assert entry["big"] == formatting.show(kg, "weigh_in", page=True), on
    bw = services.get_trends(uid)["bodyweight"]
    page = client.get("/progress").text
    rows = dict((q, v) for q, v, _ in trends_of(page))
    assert rows["Smoothed trend"] == formatting.show(bw["smoothed"], "bodyweight_derived", page=True)
    assert rows["7-day average"] == formatting.show(bw["now"], "bodyweight_derived", page=True)
    assert rows["Weigh-ins"] == formatting.show(bw["weigh_ins_28d"], "count", page=True) == str(len(pounds))
    [weight] = [t for t in tiles_of(page) if "tile-weight" in t["classes"] and t["tag"] == "button"]
    assert weight["value"] == formatting.show(bw["smoothed"], "bodyweight_derived", unit=False, page=True)
    assert 78 < bw["smoothed"] < 81  # kg, not lb, and not a pound converted twice


def test_each_count_on_today_and_progress_takes_its_noun_by_the_count(client):
    """The merged tree's check: "1 lift sessions last 7 days" on the Training tile. Each count with a noun on Today's
    and Progress' tiles and sheets, at 0, 1 and 2: the Training tile's label and its sheet's title, the day page's
    lift sessions in 28 days and the Weight sheet's weigh-ins. Each figure is get_trends' or get_days' own."""
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.update_settings(uid, {"time_zone": "Asia/Kolkata"}, "web")
    today = today_of(only_user())
    for n in (0, 1, 2):
        if n:
            assert "error" not in services.log_lifts(uid, {"date": today.isoformat(), "sets": [
                {"exercise": "Back Squat", "weight_kg": 60 + n, "reps": 5}]}, "claude")
            assert "error" not in services.log_bodyweight(uid, {"kg": 80, "date": (today - timedelta(days=n)).isoformat()},
                                                          "claude")
        trends = services.get_trends(uid)
        assert trends["sessions"]["this_week"] == n and services.get_days(uid)["lift_sessions_28d"] == n
        sessions = "lift session" if n == 1 else "lift sessions"
        day = client.get(f"/day/{today.isoformat()}").text
        for address, page in ((f"/day/{today.isoformat()}", day), ("/progress", client.get("/progress").text)):
            training = [t for t in tiles_of(page) if "tile-training" in t["classes"]]
            assert len(training) == 2 and {(t["value"], t["label"]) for t in training} == {(str(n), sessions)}, address
            sheet = sheets_of(page)["sheet-training"]
            assert (sheet["giant"], sheet["title"]) == (str(n), sessions.capitalize()), address
            if n:
                weigh_ins = "weigh-in" if trends["bodyweight"]["weigh_ins_28d"] == 1 else "weigh-ins"
                assert f"from {n} {weigh_ins} in 28 days." in sheets_of(page)["sheet-weight"]["sentence"], address
        assert re.search(r'<span class="aside">([^<]*)</span>', day).group(1) == f"{n} {sessions}"
