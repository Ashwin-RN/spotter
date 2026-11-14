"""The hi there page and the link whoami hands a new person's assistant (contract hi-there.md, package HT1).

The page's words in each state are written out here from the contract. Only its counts and lists are filled, from the
tables the contract names. whoami is read through the service and through MCP, so the published schema judges it.
"""

import inspect
import json
import re
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select

from app import db, exercises, formatting, links, mcp_server, models, services, web, web_auth
from app.mcp_schemas import WhoamiResult
from app.models import Bodyweight, CallLog, CardioEntry, FoodEntry, Goal, LiftSession, Measurement, SavedFood
from app.services import account, goals as goal_rules, imports, measurements
from evals import judge
from evals.run import LocalServer, dates_for, person_rows

from . import text_rules as tr
from .conftest import claims, rpc, settings, sign, whoami
from .test_docs import top_level_pages
from .test_mcp import model_texts, read_resource, sends_to_a_page, sentences, tool_list
from .test_page_rules import FRAME, FRAME_RULES, Seen, page_rule_findings, titled_card_findings, unclosed_findings
from .test_pages_text import ACCOUNTS, BUILT, NAMED, page_findings, step_4_findings
from .test_web import NO_BREAK, NO_WEB, SITE, WEB, client, head_tags, only_user, plain_words, seam  # noqa: F401
from .test_web import plant_session, session_of, sign_in, web_client  # noqa: F401 - fixtures

# ---------------------------------------------------------------- the contract's words

NOTE = ("Nothing is logged on this account yet. Give the person links.hi_there once, last in your reply: it is a short "
        "read on what to say, what Spotter keeps and how to bring history over from another app. It has nothing to "
        "fill in.")
LINK_DESCRIPTION = ("A short read for the person: what to say, what Spotter keeps and how to bring history over from "
                    "another app. Give it when new_account_note is present, or when the person asks what Spotter can "
                    "do.")
FIELD_DESCRIPTION = ("Present only while nothing is logged on the account: no entry, no saved food, no open goal. Give "
                     "the person links.hi_there once, last in your reply.")
SENTENCE = "If it carries new_account_note, do what it says."
SECTION = """## A new person

- While nothing is logged, `whoami` carries `new_account_note`: no live entry, no saved food and no open goal.
- Claude then gives the person `links.hi_there` once, last in the reply.
- The link is a short read: what to say, what Spotter keeps and how to bring history over. It has nothing to fill in.
- The note goes after the first entry, saved food or open goal. It comes back if the account is emptied again.
- `links.hi_there` is always there. Give it when the person asks what Spotter can do.
"""


def listed(words) -> str:
    words = [w.lower() for w in words]
    return ", ".join(words[:-1]) + " and " + words[-1]


def count(n: int) -> str:
    return formatting.show(n, "count")


SET_GOAL_KINDS = [k for k in models.GOAL_KINDS if k not in goal_rules.SETTINGS_KINDS]  # set_goal's own
KEEPS = (f"Lifts. {count(len(exercises.LIFTS))} lifts by name, each session with its sets.",
         f"Cardio. {count(len(exercises.CARDIO))} activities.",
         f"Body. Weigh-ins, and {count(len(models.MEASUREMENT_KINDS))} measurements: "
         f"{listed(measurements.kind_word(k) for k in models.MEASUREMENT_KINDS)}.",
         f"Food. What you ate, by meal: {listed(models.MEALS)}.",
         f"Goals. {count(len(SET_GOAL_KINDS))} kinds: {listed(words for _, words in web.GOAL_KINDS)}.")
SAY = ("Am I on track for my goals?", "Each goal against its plan line, and the date it is reached at this pace.",
       "How am I doing?", "Your weight trend, calories a day, lift sessions, cardio and maintenance.",
       "Lunch was two rotis and dal.", "Logs the meal. Each number is tagged: you said, saved food, USDA or estimate.")
EVERYDAY = (f"Back squat, 3 sets of 5 at 60{NO_BREAK}kg", f"I weighed 72.5{NO_BREAK}kg this morning",
            f"30{NO_BREAK}minutes on the treadmill")
HI_WORDS = ("Hi there", "A fitness log you keep by talking to your assistant.",
            "Tell your assistant what you lifted, ate or weighed. Spotter stores it and works out your bests, trends "
            "and goal pace in code, and your assistant quotes those numbers.",
            "Say it to your assistant", *SAY, "Everyday logging", *EVERYDAY,
            "What Spotter keeps", *KEEPS,
            "Bring your history over", "Your assistant can bring your history over from another app.",
            "Give it the app's export file and ask.", "It shows a preview first, and the preview writes nothing.",
            "Your assistant is told to write only when you say yes.", "It takes weigh-ins, measurements, lift sessions, food and cardio.",
            "More", "Not connected yet? The steps are on the home page.",
            "Each assistant's own steps are in the README's table.", "{state}", "{feedback}",
            "Source code", "Contact", "Spotter is an open project, run by one person.")
STATE_WORDS = {"signed out": "Sign in to see what you logged. Sign in", "signed in": "Your pages start at Today. Open Today",
               "no web sign-in": "", "no server address": ""}
FEEDBACK = "Something wrong or confusing? Tell your assistant to send feedback. It reaches the person who runs Spotter."
OVERRIDES = {"signed out": {}, "signed in": {}, "no web sign-in": NO_WEB,
             "no server address": {"authkit_domain": None, "mcp_resource_url": None}}
# The one line and link of More that differ, signed in and signed out, as the template draws them (R7: a.plain-link).
IN_LINES = '<p>Your pages start at Today.</p>\n  <p><a class="plain-link" href="/today">Open Today</a></p>'
OUT_LINES = '<p>Sign in to see what you logged.</p>\n  <p><a class="plain-link" href="/sign-in">Sign in</a></p>'
FEEDBACK_FUNCTION = "send_feedback"


def hi_in(web_client, state: str, then=lambda: None) -> str:
    """/hi in a state. `then` runs once the app is built: the server checks NEEDS against its functions at start."""
    client = web_client(**OVERRIDES[state])
    then()
    if state == "signed in":
        sign_in(client)
    r = client.get("/hi")
    assert r.status_code == 200
    return r.text


def main_of(page: str) -> str:
    """What main holds, without its own tag (its class differs signed in and out: "out")."""
    start = page.index("<main")
    return page[page.index(">", start) + 1:page.index("</main>")]


def want_words(state: str, feedback: bool) -> str:
    return re.sub(" +", " ", " ".join(HI_WORDS).format(state=STATE_WORDS[state], feedback=FEEDBACK if feedback else ""))


# ---------------------------------------------------------------- the page: its words in each state

@pytest.mark.parametrize("state", sorted(OVERRIDES))
def test_the_page_says_the_contracts_words_in_each_state(web_client, monkeypatch, state):
    page = hi_in(web_client, state, lambda: monkeypatch.delitem(mcp_server.NEEDS, FEEDBACK_FUNCTION, raising=False))
    assert plain_words(page[page.index("<main"):page.index("</footer>")]) == want_words(state, False)
    assert re.search(r'</main>\s*<footer class="site-foot">', page) and page.count("<footer") == 1


@pytest.mark.parametrize("state", ["signed out", "signed in"])
def test_the_feedback_line_shows_while_the_function_is_offered(web_client, monkeypatch, state):
    client = web_client()  # built first: the server checks NEEDS against its own functions at start
    monkeypatch.setitem(mcp_server.NEEDS, FEEDBACK_FUNCTION, "read")
    if state == "signed in":
        sign_in(client)
    page = client.get("/hi").text
    assert plain_words(page[page.index("<main"):page.index("</footer>")]) == want_words(state, True)


def test_the_feedback_line_is_read_at_each_render(client, monkeypatch):
    monkeypatch.delitem(mcp_server.NEEDS, FEEDBACK_FUNCTION, raising=False)
    assert FEEDBACK not in client.get("/hi").text
    monkeypatch.setitem(mcp_server.NEEDS, FEEDBACK_FUNCTION, "read")
    assert FEEDBACK in client.get("/hi").text
    monkeypatch.delitem(mcp_server.NEEDS, FEEDBACK_FUNCTION)
    assert FEEDBACK not in client.get("/hi").text


@pytest.mark.parametrize("state", sorted(OVERRIDES))
def test_the_page_keeps_every_rule_a_built_route_keeps(web_client, state):
    """The frame rules (the plain head among them), the page rules, the card rule, every element in main closed, the
    text rules and the title. One h1. No form, field, button or script in main. The links that differ are a.plain-link."""
    page = hi_in(web_client, state)
    signed_in = state == "signed in"
    s = Seen("/hi", page, signed_in, web_ready=state in ("signed out", "signed in"), zone_missing=signed_in)
    assert "/hi" in BUILT and NAMED["/hi"] == "Hi there" and FRAME["/hi"] == (None, {"privacy-page"})
    main = page[page.index("<main"):page.index("</main>") + len("</main>")]
    findings = [f for rule in FRAME_RULES for f in rule(s)] + page_rule_findings(s) + titled_card_findings(s)
    findings += unclosed_findings(main) + page_findings("/hi", page, [], None) + step_4_findings("/hi", page, None, [])
    assert not findings, "\n".join(findings)
    assert "<title>Hi there · Spotter</title>" in page and head_tags(page) == [] and "og:" not in page
    [node] = s.dom.find_all("main")
    assert len(s.dom.find_all("h1")) == 1 and s.dom.find_all("h1")[0].text() == "Hi there"
    assert not [n.tag for n in node.iter() if n.tag in ("form", "input", "select", "textarea", "button", "script")]
    assert not [n for n in node.iter() if n.tag in ("img", "svg", "picture")]
    assert [(n.attrs.get("href"), n.classes) for n in node.find_all("a", "plain-link")] == \
        {"signed in": [("/today", ["plain-link"])], "signed out": [("/sign-in", ["plain-link"])]}.get(state, [])
    cards = [(n.tag, n.classes, n.attrs.get("aria-labelledby")) for n in s.dom.find_all(cls="card")]
    assert cards == [("section", ["card"], i) for i in ("hi-say", "hi-keeps", "hi-history", "hi-more")]


@pytest.mark.parametrize("state", sorted(OVERRIDES))
def test_the_page_links_to_no_signed_in_page_signed_out_and_names_nothing_it_must_not(web_client, state):
    page = hi_in(web_client, state)
    hrefs = re.findall(r'<a\b[^>]*\bhref="([^"]*)"', page)
    signed_in_pages = ("/today", "/day", "/progress", "/goals", "/foods", "/settings")
    if state != "signed in":
        assert not [h for h in hrefs if h.startswith(signed_in_pages)], hrefs
    main = main_of(page)
    assert '<a href="/">the home page</a>' in main and f'<a href="{web.README_TABLE}">the README\'s table</a>' in main
    words = plain_words(main)
    # No assistant named, no function named, no privacy page, nothing on what the assistant may or may not do.
    assert not re.search(r"\bclaude\b", page, re.I) and not [a for a in web.ASSISTANTS if a in words]
    assert not [n for n in mcp_server.NEEDS if n in main]
    assert not [h for h in hrefs if "privacy" in h.casefold()] and "privacy" not in words.casefold()
    assert not re.search(r"\b(may|allow|allowed|permission)\b", words, re.I)
    assert "Asha" not in main and "@" not in main  # no account data


def test_each_link_on_the_page_answers(client):
    sign_in(client)
    for _ in ("signed in", "signed out"):
        page = client.get("/hi").text
        for href in re.findall(r'<a\b[^>]*\bhref="(/[^"]*)"', main_of(page)):
            assert client.get(href).status_code in (200, 303), href
        client.cookies.clear()


def test_every_class_in_main_is_one_the_stylesheet_has(client):
    style = (web.HERE / "static" / "style.css").read_text(encoding="utf-8")
    page = client.get("/hi").text
    classes = {c for value in re.findall(r'class="([^"]*)"', main_of(page)) for c in value.split()}
    assert classes and not [c for c in classes if not re.search(rf"\.{re.escape(c)}\b", style)], classes
    assert re.search(r"\.privacy-page > \*", style)


# ---------------------------------------------------------------- the page: each fact from its source

def test_no_digit_and_no_count_is_typed_in_the_template():
    source = (web.HERE / "templates" / "hi.html").read_text(encoding="utf-8")
    assert not re.search(r"\d", re.sub(r"</?h[1-6]\b", "", source))  # a heading's tag name is the only digit
    for name in ("lifts", "cardio", "measurements", "goal_kinds"):
        assert f'{{{{ keeps.{name}|show("count") }}}}' in source, name
    for path in ('href="/"', 'href="/today"', 'href="/sign-in"'):
        assert path in source
    assert "links.page" not in source


def test_each_count_and_list_is_what_the_assistant_can_use(client):
    """R6: the counts are what list_exercises gives on a fresh database, the goal kinds set_goal's own, the meals
    models.MEALS, the measurements every one of MEASUREMENT_KINDS."""
    page = client.get("/hi").text
    keeps = re.search(r'<section class="card" aria-labelledby="hi-keeps">.*?</section>', page, re.S).group(0)
    items = [plain_words(li) for li in re.findall(r"<li>(.*?)</li>", keeps, re.S)]
    lifts, cardio = (len(services.list_exercises(kind)["exercises"]) for kind in ("lift", "cardio"))
    assert items[0] == f"Lifts. {count(lifts)} lifts by name, each session with its sets."
    assert items[1] == f"Cardio. {count(cardio)} activities."
    assert items[2] == (f"Body. Weigh-ins, and {count(len(models.MEASUREMENT_KINDS))} measurements: "
                        f"{listed(measurements.kind_word(k) for k in models.MEASUREMENT_KINDS)}.")
    assert items[3] == f"Food. What you ate, by meal: {listed(models.MEALS)}."
    assert {k for k, _ in web.GOAL_KINDS} == set(SET_GOAL_KINDS) and len(SET_GOAL_KINDS) == len(web.GOAL_KINDS)
    assert items[4] == f"Goals. {count(len(SET_GOAL_KINDS))} kinds: {listed(w for _, w in web.GOAL_KINDS)}."


def test_a_list_in_a_sentence_comes_from_one_helper():
    assert web.in_words(["Body weight", "Lift", "Cardio time"]) == "body weight, lift and cardio time"
    assert web.in_words(["Waist", "Hips"]) == "waist and hips" and web.in_words(["Water"]) == "water"


def test_the_say_list_is_the_landings_item_for_item(client):
    assert web.SAY_FIRST[0][0] is web.LANDING_EXCHANGE["question"]

    def lists(page: str) -> tuple[list[str], list[str]]:
        say = re.search(r'<ul class="say">(.*?)</ul>', page, re.S).group(1)
        everyday = page[page.index("Everyday logging"):]
        everyday = re.search(r'<ul class="plain-list">(.*?)</ul>', everyday, re.S).group(1)
        return ([plain_words(li) for li in re.findall(r"<li>(.*?)</li>", say, re.S)],
                [plain_words(li) for li in re.findall(r"<li>(.*?)</li>", everyday, re.S)])

    hi, landing = lists(client.get("/hi").text), lists(client.get("/").text)
    assert hi == landing == ([f"{SAY[n]} {SAY[n + 1]}" for n in (0, 2, 4)], list(EVERYDAY))


# The landing's say-first card as the parent sent it (home.html at d890103, lines 99 to 112), line for line.
PARENT_SAY_FIRST = [
    '<section class="card say-first" aria-labelledby="say-title">',
    '  <h2 id="say-title">Things to say first</h2>',
    '  <ul class="say">',
    '    <li><q>Am I on track for my goals?</q> <span class="does">Each goal against its plan line, and the date it '
    'is reached at this pace.</span></li>',
    '    <li><q>How am I doing?</q> <span class="does">Your weight trend, calories a day, lift sessions, cardio and '
    'maintenance.</span></li>',
    '    <li><q>Lunch was two rotis and dal.</q> <span class="does">Logs the meal. Each number is tagged: you said, '
    'saved food, USDA or estimate.</span></li>',
    '  </ul>',
    '  <p class="list-label">Everyday logging</p>',
    '  <ul class="plain-list">',
    '    <li>Back squat, 3 sets of 5 at 60\u00a0kg</li>',
    '    <li>I weighed 72.5\u00a0kg this morning</li>',
    '    <li>30\u00a0minutes on the treadmill</li>',
    '  </ul>',
    '</section>',
]


def test_the_landings_say_first_card_is_the_parents_line_for_line(client):
    """The say-list drawn from the two constants renders as the parent's typed list did, each line with its indent."""
    for signed_in in (False, True):
        if signed_in:
            sign_in(client)
        card = re.search(r'<section class="card say-first".*?</section>', client.get("/").text, re.S).group(0)
        assert card.split("\n") == PARENT_SAY_FIRST, card


def test_the_import_sentences_hold_to_the_import_rule_and_its_kinds():
    assert "The first call is a preview" in mcp_server.IMPORT_RULE
    assert "Write, with dry_run false, only when the person says yes." in mcp_server.IMPORT_RULE
    assert "With dry_run true or left out, nothing is written" in mcp_server.IMPORT_DESCRIPTION
    assert sorted(imports.ORDER) == sorted(("bodyweight", "measurement", "lift_session", "food_entry", "cardio_entry"))


# ---------------------------------------------------------------- the page: open, the same for everyone

@pytest.mark.parametrize("state", ["signed out", "no web sign-in", "no server address"])
def test_signed_out_the_page_reads_no_database_and_keeps_no_cookie(web_client, monkeypatch, state):
    client = web_client(**OVERRIDES[state])

    def no_database(*args, **kwargs):
        raise AssertionError("the page read the database")

    for name in ("session_scope", "new_session", "get_engine"):
        monkeypatch.setattr(db, name, no_database)
    r = client.get("/hi")
    assert r.status_code == 200 and "set-cookie" not in r.headers


def test_a_session_naming_a_person_who_is_gone_draws_the_signed_out_page_and_is_cleared(client, monkeypatch):
    """R2: a validly signed session whose person no longer exists costs one read and is cleared, as on the landing."""
    monkeypatch.delitem(mcp_server.NEEDS, FEEDBACK_FUNCTION, raising=False)
    real, loaded = web._load_user, []
    monkeypatch.setattr(web, "_load_user", lambda user_id: loaded.append(user_id) or real(user_id))
    for path in ("/hi", "/"):
        plant_session(client, {web.USER: 999_999, "csrf": "t", web.SIGNED_IN_AT: int(time.time())})
        assert session_of(client)[web.USER] == 999_999
        loaded.clear()
        r = client.get(path)
        assert r.status_code == 200 and session_of(client) == {} and loaded == [999_999], (path, loaded)
        assert '<header class="top-out">' in r.text and 'class="tabs"' not in r.text, path
        if path == "/hi":
            shown = r.text[r.text.index("<main"):r.text.index("</footer>")]
            assert plain_words(shown) == want_words("signed out", False)


def test_the_page_builds_no_link_with_links_page(client, monkeypatch):
    def no_link(*args, **kwargs):
        raise AssertionError("the page built a link")

    sign_in(client)
    monkeypatch.setattr(links, "page", no_link)
    assert client.get("/hi").status_code == 200
    client.cookies.clear()
    assert client.get("/hi").status_code == 200


def test_main_is_the_same_for_everyone_and_differs_signed_out_by_one_line_and_link(web_client, seam):
    """R13: one person with nothing logged, one with four weeks of entries, and a signed-out visitor."""
    empty = web_client()
    sign_in(empty)
    new_main = main_of(empty.get("/hi").text)
    full = web_client()
    seam.identity = web_auth.Identity("user_02WEB", "ben@example.com", "Ben Web", "session_02DEF")
    sign_in(full)
    with db.session_scope() as session:
        ben = session.scalar(select(models.User).where(models.User.subject == "user_02WEB"))
    ACCOUNTS["four weeks"](ben.id)
    assert services.account_empty(ben.id) == {"empty": False}
    full_main = main_of(full.get("/hi").text)
    assert full_main == new_main and "Ben" not in full_main
    out_main = main_of(web_client().get("/hi").text)
    assert IN_LINES in new_main and OUT_LINES in out_main and out_main != new_main
    assert new_main.replace(IN_LINES, OUT_LINES) == out_main


def test_loads_of_the_page_use_up_none_of_the_page_limit(client, monkeypatch):
    """R14: more loads than the page limit allows all answer 200 and count for nothing. A page behind the limit
    still has every one of its loads after them."""
    monkeypatch.setattr(web, "PAGE_LIMIT", 3)
    sign_in(client)
    assert [client.get("/hi").status_code for _ in range(8)] == [200] * 8
    assert [client.get("/progress").status_code for _ in range(4)] == [200, 200, 200, 429]
    assert client.get("/hi").status_code == 200  # over the limit, still open


# The parent's route table with web sign-in set up, at d890103, row by row: (path, methods, behind the page limit).
# With web sign-in off, or with no server address, the parent's table is these rows with nothing behind the limit.
PARENT_ROUTES = [
    ("/", "GET HEAD", False), ("/sign-in", "GET HEAD", False), ("/auth/callback", "GET HEAD", False),
    ("/sign-out", "POST", False), ("/settings", "GET HEAD POST", True), ("/settings/claude-can", "POST", True),
    ("/today", "GET HEAD", True), ("/day/{date}", "GET HEAD", True), ("/day/{date}/weigh-in", "POST", True),
    ("/day/{date}/measurement", "POST", True), ("/day/{date}/food", "POST", True), ("/day/{date}/cardio", "POST", True),
    ("/foods", "GET HEAD POST", True), ("/foods/{food_id}/edit", "GET HEAD POST", True),
    ("/foods/{food_id}/delete", "POST", True), ("/foods/{food_id}/restore", "POST", True),
    ("/settings/targets", "POST", True), ("/entries/{kind}/{entry_id}/edit", "GET HEAD POST", True),
    ("/entries/food_entry/{entry_id}/confirm", "POST", True), ("/entries/{kind}/{entry_id}/delete", "POST", True),
    ("/entries/{kind}/{entry_id}/restore", "POST", True), ("/progress", "GET HEAD", True),
    ("/progress/{lift}", "GET HEAD", True), ("/goals", "GET HEAD POST", True), ("/goals/{goal_id}/status", "POST", True),
    ("/settings/export", "POST", True), ("/settings/delete", "GET HEAD POST", True),
    ("/settings/targets", "GET HEAD", False), ("/settings/claude-can", "GET HEAD", False), ("/static", "", False),
]


def handlers(endpoint) -> list[str]:
    """The chain of handlers a route runs, by name: the endpoint and every function its closures hold."""
    names, todo = [], [endpoint]
    while todo:
        fn = todo.pop(0)
        names.append(fn.__qualname__)
        todo += [c.cell_contents for c in fn.__closure__ or () if inspect.isfunction(c.cell_contents)]
    return names


def route_rows(overrides: dict) -> list[tuple[str, str, bool]]:
    return [(r.path, " ".join(sorted(getattr(r, "methods", None) or ())),
             "page_limited.<locals>.run" in handlers(r.endpoint) if hasattr(r, "endpoint") else False)
            for r in web.routes(settings(**{**WEB, **overrides}))]


@pytest.mark.parametrize("state", ["signed out", "no web sign-in", "no server address"])
def test_the_route_tables_differ_from_before_by_hi_alone(state):
    """R14: the parent's rows plus one, /hi straight after /, GET and HEAD, not behind the page limit."""
    before = PARENT_ROUTES if state == "signed out" else [(p, m, False) for p, m, _ in PARENT_ROUTES]
    assert route_rows(OVERRIDES[state]) == before[:1] + [("/hi", "GET HEAD", False)] + before[1:]


def test_a_load_of_the_page_writes_nothing(client):
    sign_in(client)
    with db.session_scope() as session:
        before = session.scalar(select(func.count()).select_from(CallLog))
    assert client.get("/hi").status_code == 200
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(CallLog)) == before


def test_the_link_name_builds_and_has_a_page_signed_in_and_out(client):
    address = links.page("hi_there")
    assert address == SITE + "/hi" and "hi_there" in links.NAMES
    assert client.get(address).status_code == 200
    sign_in(client)
    assert client.get(address).status_code == 200
    assert "Hi there" not in top_level_pages()  # not one of the README's account pages (decision 15)


def test_the_footer_and_the_notes_say_what_is_now_so(client):
    hi, landing = client.get("/hi").text, client.get("/").text
    foot = re.compile(r'<footer class="site-foot">.*?</footer>', re.S)
    assert foot.search(hi).group(0) == foot.search(landing).group(0)
    assert "/hi" in web.__doc__ and "hi_there" in links.__doc__ and "new_account_note" in account.__doc__


# ---------------------------------------------------------------- whoami: the link and the note

def target_date(user_id: int) -> str:
    return (date.fromisoformat(services.whoami(user_id)["today"]) + timedelta(days=90)).isoformat()


MAKE = {
    "bodyweight": (Bodyweight, lambda uid: services.log_bodyweight(uid, {"kg": 80}, "claude")),
    "measurement": (Measurement, lambda uid: services.log_measurement(uid, {"kind": "waist", "value": 84}, "claude")),
    "lift_session": (LiftSession, lambda uid: services.log_lifts(
        uid, {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}]}, "claude")),
    "food_entry": (FoodEntry, lambda uid: services.log_food(uid, {"entries": [{"description": "Dal", "kcal": 180}]},
                                                            "claude")),
    "cardio_entry": (CardioEntry, lambda uid: services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 30},
                                                                  "claude")),
    "saved_food": (SavedFood, lambda uid: services.save_food(
        uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")),
    "goal": (Goal, lambda uid: services.set_goal(uid, {"kind": "bodyweight", "target_value": 75, "start_value": 80,
                                                        "target_date": target_date(uid)}, "claude")),
}
# Each row, then the way it goes: a delete, or a goal closed.
STATES = [(kind, "deleted") for kind in MAKE if kind != "goal"] + [("goal", "dropped"), ("goal", "hit")]


def make_one(kind: str, user_id: int) -> None:
    assert "error" not in MAKE[kind][1](user_id)


def take_it_away(kind: str, close: str, user_id: int) -> None:
    model = MAKE[kind][0]
    with db.session_scope() as session:
        [row_id] = session.scalars(select(model.id).where(model.user_id == user_id)).all()
    done = (services.set_goal(user_id, {"goal_id": row_id, "status": close}, "claude") if kind == "goal"
            else services.delete_entry(user_id, kind, row_id))
    assert "error" not in done, done


def held(who: dict, new: bool) -> None:
    """The note while the account is new and not after, the link always."""
    assert who["links"]["hi_there"] == SITE + "/hi" and who["links"]["home"] == SITE + "/"
    assert who.get("new_account_note") == (NOTE if new else None)


def test_an_empty_account_carries_the_note_and_the_link(make_user):
    zoned = services.whoami(make_user(time_zone="Europe/London").id)
    held(zoned, True)
    assert "time_zone_note" not in zoned
    no_zone = services.whoami(make_user().id)  # no time zone: both notes, the new one after the other
    held(no_zone, True)
    keys = list(no_zone)
    assert "time_zone_note" in no_zone and keys.index("time_zone_note") < keys.index("new_account_note")
    assert tr.check(NOTE, tr.MESSAGE) == []


@pytest.mark.parametrize("kind, close", STATES, ids=[f"{k} {c}" for k, c in STATES])
def test_the_note_goes_at_the_first_row_and_comes_back_when_it_goes(make_user, kind, close):
    user_id = make_user(time_zone="Europe/London").id
    held(services.whoami(user_id), True)
    make_one(kind, user_id)
    held(services.whoami(user_id), False)
    assert services.account_empty(user_id) == {"empty": False}
    take_it_away(kind, close, user_id)
    held(services.whoami(user_id), True)
    assert services.account_empty(user_id) == {"empty": True}


@pytest.mark.parametrize("kind, close", STATES, ids=[f"{k} {c}" for k, c in STATES])
def test_whoami_through_mcp_and_todays_card_agree_on_new_in_each_state(client, kind, close):
    """The same account on the web and over MCP: Today's Nothing logged yet card shows exactly while whoami, as
    published, carries the note. The web's person has no time zone, so whoami carries both notes while it is new."""
    sign_in(client)
    user_id, token = only_user().id, sign(claims(sub=only_user().subject))

    def agree(new: bool) -> None:
        who = whoami(client, token)  # through MCP: the library checks it against the published schema
        WhoamiResult.model_validate(who)
        held(who, new)
        assert "time_zone_note" in who and services.account_empty(user_id) == {"empty": new}
        day = client.get(client.get("/today").headers["location"]).text
        assert ('<h2 class="hero-title" id="today-card-title">Nothing logged yet</h2>' in day) is new

    agree(True)
    make_one(kind, user_id)
    agree(False)
    take_it_away(kind, close, user_id)
    agree(True)


def test_a_result_passes_the_schema_as_it_was_and_an_empty_ones_fails_on_the_note_alone(http):
    """R10, the known limit: whoami's result model as it was published before this work."""
    class OldLinks(BaseModel):
        settings: str
        home: str

    class OldWhoami(BaseModel):
        model_config = ConfigDict(extra="forbid")
        account_id: int
        user_id: str
        email: str | None
        name: str | None
        time_zone: str | None
        today: str
        targets: dict
        claude_can: str
        links: OldLinks
        sign_in: dict
        time_zone_note: str | None = None

    token = sign()
    empty = whoami(http, token)
    with pytest.raises(ValidationError) as refused:
        OldWhoami.model_validate(empty)
    assert [(e["type"], e["loc"]) for e in refused.value.errors()] == [("extra_forbidden", ("new_account_note",))]
    OldWhoami.model_validate({k: v for k, v in empty.items() if k != "new_account_note"})
    services.log_bodyweight(empty["account_id"], {"kg": 80}, "claude")
    logged = whoami(http, token)
    assert "new_account_note" not in logged and "hi_there" in logged["links"]
    OldWhoami.model_validate(logged)


# ---------------------------------------------------------------- what the assistant reads

def test_the_five_new_texts_are_the_contracts_and_none_says_page_or_form(http):
    token = sign()
    [tool] = [t for t in tool_list(http, token) if t["name"] == "whoami"]
    schema = tool["outputSchema"]
    doc = read_resource(http, token, "spotter://can-and-cant")["text"]
    section = doc[doc.index("## A new person"):doc.index("## What Claude may do")].rstrip() + "\n"
    texts = {"note": whoami(http, token)["new_account_note"],
             "link": schema["$defs"]["WhoamiLinks"]["properties"]["hi_there"]["description"],
             "sentence": tool["description"].rsplit(". ", 1)[-1],
             "field": schema["properties"]["new_account_note"]["description"],
             "section": section}
    assert texts == {"note": NOTE, "link": LINK_DESCRIPTION, "sentence": SENTENCE, "field": FIELD_DESCRIPTION,
                     "section": SECTION}
    for where, text in texts.items():
        assert not re.search(r"\b(page|form)\b", text, re.I), where
        assert not [s for s in sentences(text) if sends_to_a_page(s)], where
    assert tool["description"].endswith(" " + SENTENCE)
    assert doc.index("## What \"today\" means") < doc.index("## A new person") < doc.index("## What Claude may do")
    assert "hi_there" in schema["$defs"]["WhoamiLinks"]["required"]
    assert "new_account_note" not in schema.get("required", [])


def test_the_page_sending_test_reads_the_note_and_keeps_its_one_exception(http):
    texts = model_texts(http, sign())
    assert texts["whoami new_account_note"] == NOTE and texts["whoami time_zone_note"]
    sending = [(where, s) for where, text in texts.items() for s in sentences(text) if sends_to_a_page(s)]
    assert sending == []  # the one exception is the not_allowed fix, read by the test in test_mcp.py
    assert len(mcp_server.INSTRUCTIONS) == 3927  # switch decision 18


def test_the_new_names_are_in_whoami_alone_in_the_published_list(http):
    tools = tool_list(http, sign())
    assert tools and "whoami" in [tool["name"] for tool in tools]
    for tool in tools:
        text = json.dumps(tool)
        assert ("hi_there" in text and "new_account_note" in text) is (tool["name"] == "whoami"), tool["name"]


# ---------------------------------------------------------------- the eval cases

def test_the_two_eval_cases_load_and_judge_as_designed(app_settings):
    """R15, by hand both ways: with the link in the reply and without. The first runs on an empty account, so its
    whoami carries the note. The second's seed is a weigh-in the day before, so its whoami does not."""
    cases = {c["id"]: c for c in judge.load_cases()}
    first, again = cases["hi_there_first_log"], cases["hi_there_not_again"]
    for case in (first, again):
        assert judge.case_problems(case) == [] and case["say"] == "I weighed 80.4 kg this morning"
        assert case["expect"]["function"] == "log_bodyweight" and case["expect"]["inputs_include"] == {"kg": 80.4}
    assert "seed" not in first and first["expect"]["reply_has"] == ["/hi"]
    assert again["seed"] == [{"as": "earlier", "service": "log_bodyweight",
                              "data": {"kg": 80.9, "date": "{{yesterday}}"}}]
    assert again["expect"]["reply_lacks"] == ["/hi"]
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    zone = "Asia/Kolkata"
    dates = dates_for(zone)
    linked = f"Logged 80.4 kg for today.\n{SITE}/hi"
    plain = "Logged 80.4 kg for today."
    calls = [{"name": "log_bodyweight", "arguments": {"kg": 80.4}, "is_error": False}]
    for case, new, good, bad in ((first, True, linked, plain), (again, False, plain, linked)):
        user_id, _, seeded = server.new_person(zone, case.get("seed", []))
        assert ("new_account_note" in services.whoami(user_id)) is new
        resolved = judge.resolve(case, {**dates, **{f"seed.{k}": v for k, v in seeded.items()}})
        assert judge.judge(resolved, calls, good, person_rows(user_id))  # not logged yet: the db check fails
        assert "error" not in services.log_bodyweight(user_id, {"kg": 80.4}, "claude")
        assert judge.judge(resolved, calls, good, person_rows(user_id)) == [], case["id"]
        assert judge.judge(resolved, calls, bad, person_rows(user_id)), case["id"]
