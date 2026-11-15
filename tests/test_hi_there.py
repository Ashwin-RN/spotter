"""The hi there page, the link whoami hands a new person's assistant, and setup led by the assistant (contracts
hi-there.md, package HT1, and first-day.md, package FD1).

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
from sqlalchemy import event, func, select

from app import db, exercises, formatting, links, mcp_schemas, mcp_server, metrics, models, rules, services, web
from app import web_auth
from app.mcp_schemas import SetIn, WhoamiResult
from app.models import (
    Bodyweight, CallLog, CardioEntry, FoodEntry, Goal, LiftSession, LiftSet, Measurement, SavedFood, User,
)
from app.services import account, goals as goal_rules, imports, lifts, measurements, progress, trends
from app.services import log as log_service
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

NOTE = ("Nothing is logged on this account yet. Offer once to set Spotter up together, as setup.how says. Give the "
        "person links.hi_there once, last in your reply: it is a short read on how to set up, what to say and what "
        "Spotter works out. It has nothing to fill in.")
LINK_DESCRIPTION = ("A short read for the person: what to say, what Spotter keeps and how to bring history over from "
                    "another app. Give it when new_account_note is present, or when the person asks what Spotter can "
                    "do.")
FIELD_DESCRIPTION = ("Present only while nothing is logged on the account: no entry, no saved food, no open goal. Give "
                     "the person links.hi_there once, last in your reply.")
NOTE_SENTENCE = "If it carries new_account_note, do what it says."
SENTENCE = "If it carries setup, walk setup.missing as setup.how says."
HOW = ("Bring setup up only when the person asks, or when new_account_note says to offer it. Ask for the first step in "
       "missing, one question at a time, and save each answer with its function as soon as it is given. The person may "
       "skip any step: go to the next. Ask for nothing that is not in missing.")
SETUP_FIELD = ("Present while a setup step other than import is not done yet. missing: those steps, in the order to ask "
               "them: time_zone, weigh_in, lift_session, goal, calorie_target, sessions_target, cardio_target, "
               "saved_food, routine, import. how: when and how to ask.")  # the review's L3: other than import
MISSING_FIELD = "The setup steps not done yet, in the order to ask them."
HOW_FIELD = "When to bring setup up, and how to ask."
TARGETS_FIELD = "The person's targets."
SECTION = """## A new person

- While nothing is logged, `whoami` carries `new_account_note`: no live entry, no saved food and no open goal.
- Claude then gives the person `links.hi_there` once, last in the reply.
- The link is a short read: what to say, what Spotter keeps and how to bring history over. It has nothing to fill in.
- The note goes after the first entry, saved food or open goal. It comes back if the account is emptied again.
- `links.hi_there` is always there. Give it when the person asks what Spotter can do.
"""
SETTING_UP = """## Setting up

- While a setup step other than the import is not done, `whoami` carries `setup`: `missing`, the steps not done yet in order, and `how`.
- A step is done when the account holds it: a time zone, a live weigh-in, a live lift session, an open goal, each target set, a live saved food, a live routine, a live imported row.
- Claude walks the steps only when the person asks to set up, or once on a new account. One question at a time. Any step can be skipped, and comes back the next time the person asks.
"""
CANNOT_LINE = "- It knows nothing about injury, pain, recovery, form or health."
STEPS = ("time_zone", "weigh_in", "lift_session", "goal", "calorie_target", "sessions_target", "cardio_target",
         "saved_food", "routine", "import")
OFFERED_STEPS = list(STEPS)  # save_routine is offered on this tree, so every step is (routines.md decision 20)
NO_ROUTINE_STEPS = [s for s in STEPS if s != "routine"]  # with save_routine not offered, or with nothing given
ROUTINE_FUNCTION = "save_routine"
ROUTINE = {"name": "Push day", "lifts": [{"exercise": "Bench Press", "sets": 3, "reps": 8}]}
FEEDBACK_FUNCTION = "send_feedback"


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
# Part 1, item by item: the setup steps each stands for, and its words as the page reads them.
FIRST_DAY = (
    (("weigh_in", "lift_session"), f"Where you stand. I weigh 82{NO_BREAK}kg. Yesterday I squatted 3 sets of 5 at "
                                   f"80{NO_BREAK}kg. Spotter keeps the weigh-in, and the session's best becomes the "
                                   "mark to beat."),
    (("goal",), f"A goal with a date. I want to weigh 78{NO_BREAK}kg by the end of March. Spotter keeps where you start, "
                "and works out the pace a week the goal needs."),
    (("calorie_target", "sessions_target", "cardio_target"),
     f"Your targets. 2,200{NO_BREAK}kcal a day, 4 lift sessions a week, 150{NO_BREAK}minutes of cardio a week. Each "
     "day's food is shown against the calories, and each week against the other two."),
    (("saved_food",), f"Foods you eat often. Save roti: one roti is 120{NO_BREAK}kcal. After that, two rotis are logged "
                      "from the saved food, not from a guess."),
    (("routine",), f"Your fixed routines. Save my push day: bench press 3 sets of 8 at 60{NO_BREAK}kg, then dips 3 sets "
                   "of 10. After that, log my push day logs those sets."),
    (("import",), "Your old data. Here is the export file from my old app. Your assistant shows a preview first, and is "
                  "told to write only when you say yes."),
)
SAY_PART = (
    "What you can say", "Log it",
    EVERYDAY[0], "Sets said one by one go into that day's session.", EVERYDAY[1],
    SAY[4], SAY[5], EVERYDAY[2], "Its calories are estimated from your own body weight.",
    "Ask", *SAY[:4], "How is my bench press going?", "Its best each session, its new bests and its trend.",
    "What did I do this week?",
    f"Each day's lifts, food, weigh-in, measurements and cardio, up to {count(mcp_server.LOG_DAYS[1])} days at a time.",
    "Fix or undo", f"That was 85{NO_BREAK}kg, not 80.", "The entry is changed where it stands.",
    "Undo that change.", "The entry's last change is put back, once.",
    "Delete yesterday's lunch.", "Your assistant shows you the entry and asks first.",
    "Bring back the lunch I deleted.", "Nothing is erased, so it comes back.",
    "Goals", f"I want to bench 100{NO_BREAK}kg by the end of June.", "A lift goal is an estimated one-rep max.",
    "I hit my squat goal.", "The goal is closed as reached.",
    "{feedback}")
FEEDBACK = "Tell the maker Send feedback: the weight chart confuses me. It reaches the person who runs Spotter."
ESTIMATE_LINE = ("Your assistant is told to quote them. The one number it may estimate is a food's calories, and it must "
                 "say so.")
M = metrics
WORKS_PART = (
    "What Spotter works out", "Spotter works out every number in code, from what you logged.", ESTIMATE_LINE,
    f"Bests. Each weighted set of {count(1)} to {count(M.EPLEY_MAX_REPS)} reps gives an estimated one-rep max, by the "
    "Epley formula.",  # the review's L1: weighted
    "A new best beats every earlier set of that lift, by that estimate or by weight.",
    f"Your lift now is your best session of the last {count(M.RECENT_BEST_DAYS)} days, or your latest before that. "
    "One light day does not pull it down.",
    f"Body weight now. The average of your weigh-ins in the last {count(M.BODYWEIGHT_AVG_DAYS)} days, or your latest "
    "when that week has none.",
    "The weight trend. A smoothed line through every weigh-in: each one moves it a tenth of the way.",
    f"Its rate a week needs a weigh-in in the last {count(M.RATE_LATEST_DAYS)} days and one from "
    f"{count(M.RATE_REFERENCE_DAYS[0])} to {count(M.RATE_REFERENCE_DAYS[1])} days ago.",
    "Goal pace. Each goal shows the pace a week it needs to make its date, and your pace so far.",
    f"Your pace so far is a straight line fitted through the last {count(M.PROJECTION_DAYS // 7)} weeks, from at least "
    f"{count(M.PROJECTION_MIN_POINTS)} points on {count(2)} days.",  # the review's L2: on 2 days
    "Where you land. That line gives the day you reach the target at this pace.",
    "It says on track when the line reaches the target by your date, and behind when it does not.",
    f"There is no day when the line is flat, points away, or gets there more than {count(M.PROJECTED_MAX_DAYS // 365)} "
    "years out.",
    "The plan line. A straight line from your start to your target, by their dates.",
    "It says how far ahead of it or behind it you are today.",
    f"Calories. Your average a day over the last {count(trends.WEEK_DAYS)} and {count(trends.WINDOW_DAYS)} days, "
    "counting only days with food logged.",
    "It says how much of that is estimates.",
    "Cardio calories. From the activity's MET value, the minutes and your own body weight now.",
    "With no weigh-in there is no estimate.",
    "Maintenance. About what you eat to stay the same weight: your food, less your weight change, over "
    f"{count(M.MAINTENANCE_DAYS)} days.",
    f"It needs food on {count(M.MAINTENANCE_MIN_LOGGED)} of those days, a weigh-in in the last "
    f"{count(M.BODYWEIGHT_AVG_DAYS)} days and one from {count(M.MAINTENANCE_DAYS)} to "
    f"{count(M.MAINTENANCE_DAYS + M.BODYWEIGHT_AVG_DAYS - 1)} days ago.",
    "It is an estimate, and it never changes your target.")
ROUTINES = "Routines. Your fixed workouts by name, each with its lifts and usual sets."
KEEPS_PART = (
    "What Spotter keeps", *KEEPS, "Targets. Calories a day, lift sessions a week and cardio minutes a week.",
    "{routines}", "What it does not do",
    "It gives no medical advice, and knows nothing of injury, pain, form or recovery.",
    "It logs weights in kg and distances in km. An import from another app converts pounds and miles.",
    "It counts calories, not protein, carbs or fat.",
    f"It knows lifts by its own list of {count(len(exercises.LIFTS))} names. A name not on the list is refused, never "
    "guessed.",
    "It sends no reminders and no email, and does not sync with a phone or a watch.")
SWITCH_LINE = ('You decide what your assistant may do. A switch on Settings has two settings: "Read, log and delete" and '
               '"Read and log". It starts at "Read, log and delete".')
YOUR_DATA_WORDS = (SWITCH_LINE, "Export. Settings gives you everything you have logged, as a zip of CSV files.",
                   "Undo. Right after a delete, Undo brings the entry back. Your assistant can restore an entry it "
                   "deleted.",
                   "Delete your account. On Settings. It removes every entry and your sign-in, and it cannot be undone.")
HI_WORDS = ("Hi there", "A fitness log you keep by talking to your assistant.",
            "To start, say this to your assistant: Hi Spotter, help me set up.",
            "Your first day", "Your assistant asks one thing at a time, and saves each answer as you give it.",
            "Skip any step. Say it again another day, and it asks only for what is still missing.",
            "{first_day}",
            "If your assistant does not know your time zone, it asks once which city you are in.",
            *SAY_PART, *WORKS_PART, *KEEPS_PART, "Your say", *YOUR_DATA_WORDS,
            "More", "Not connected yet? The steps are on the home page.",
            "Each assistant's own steps are in the README's table.", "{state}",
            "Source code", "Contact", "Spotter is an open project, run by one person.")
STATE_WORDS = {"signed out": "Sign in to see what you logged. Sign in", "signed in": "Your pages start at Today. Open Today",
               "no web sign-in": "", "no server address": ""}
OVERRIDES = {"signed out": {}, "signed in": {}, "no web sign-in": NO_WEB,
             "no server address": {"authkit_domain": None, "mcp_resource_url": None}}
# The one line and link of More that differ, signed in and signed out, as the template draws them (R7: a.plain-link).
IN_LINES = '<p>Your pages start at Today.</p>\n  <p><a class="plain-link" href="/today">Open Today</a></p>'
OUT_LINES = '<p>Sign in to see what you logged.</p>\n  <p><a class="plain-link" href="/sign-in">Sign in</a></p>'
CARDS = ["hi-first-day", "hi-say", "hi-works", "hi-keeps", "hi-yours", "hi-more"]


def offer(monkeypatch, routine: bool = False, feedback: bool = False) -> None:
    """NEEDS with save_routine and send_feedback offered or not. Called once the app is built: the server checks NEEDS
    against its functions at start."""
    for name, on in ((ROUTINE_FUNCTION, routine), (FEEDBACK_FUNCTION, feedback)):
        if on:
            monkeypatch.setitem(mcp_server.NEEDS, name, "log")
        else:
            monkeypatch.delitem(mcp_server.NEEDS, name, raising=False)


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


def card(page: str, name: str) -> str:
    return re.search(rf'<section class="card" aria-labelledby="{name}">.*?</section>', page, re.S).group(0)


def want_words(state: str, feedback: bool = False, routine: bool = False) -> str:
    first_day = " ".join(words for steps, words in FIRST_DAY if routine or "routine" not in steps)
    return re.sub(" +", " ", " ".join(HI_WORDS).format(
        state=STATE_WORDS[state], feedback=FEEDBACK if feedback else "", routines=ROUTINES if routine else "",
        first_day=first_day))


def shown(page: str) -> str:
    return plain_words(page[page.index("<main"):page.index("</footer>")])


# ---------------------------------------------------------------- the page: its words in each state

@pytest.mark.parametrize("on", [False, True], ids=["both off", "both on"])
@pytest.mark.parametrize("state", sorted(OVERRIDES))
def test_the_page_says_the_contracts_words_in_each_state(web_client, monkeypatch, state, on):
    """Both on is NEEDS as built: save_routine and send_feedback are both offered, so step 5, the Routines line and
    the feedback group are drawn (routines.md decision 20). Both off takes the two out, and none of them is drawn
    (tidy.md J3)."""
    if on:
        assert ROUTINE_FUNCTION in mcp_server.NEEDS and FEEDBACK_FUNCTION in mcp_server.NEEDS
        page = hi_in(web_client, state)
    else:
        page = hi_in(web_client, state, lambda: offer(monkeypatch))
    assert shown(page) == want_words(state, feedback=on, routine=on)
    assert (ROUTINES in shown(page), "Your fixed routines." in shown(page)) == (on, on)
    assert re.search(r'</main>\s*<footer class="site-foot">', page) and page.count("<footer") == 1


@pytest.mark.parametrize("routine", [False, True], ids=["no routines", "routines"])
@pytest.mark.parametrize("feedback", [False, True], ids=["no feedback", "feedback"])
@pytest.mark.parametrize("state", ["signed out", "signed in"])
def test_the_routine_lines_and_the_feedback_group_show_while_their_functions_are_offered(web_client, monkeypatch,
                                                                                           state, feedback, routine):
    page = hi_in(web_client, state, lambda: offer(monkeypatch, routine=routine, feedback=feedback))
    assert shown(page) == want_words(state, feedback=feedback, routine=routine)


def test_the_routine_lines_and_the_feedback_group_are_read_at_each_render(client, monkeypatch):
    for routine, feedback in ((False, False), (True, False), (True, True), (False, True), (False, False)):
        offer(monkeypatch, routine=routine, feedback=feedback)
        words = shown(client.get("/hi").text)
        assert (ROUTINES in words, "Your fixed routines." in words, FEEDBACK in words) == (routine, routine, feedback)
        assert words == want_words("signed out", feedback=feedback, routine=routine)


@pytest.mark.parametrize("routine", [False, True], ids=["no routines", "routines"])
def test_the_first_days_items_are_the_setup_steps_on_offer_in_their_order(client, monkeypatch, routine):
    """Decision 6, R12: each item's steps, joined in the page's order, are the steps on offer less time_zone, whose line
    is the small one under the list."""
    offer(monkeypatch, routine=routine)
    part = card(client.get("/hi").text, "hi-first-day")
    titles = [plain_words(t) for t in re.findall(r'<p class="step-title">(.*?)</p>', part)]
    by_title = {item["title"]: item["steps"] for item in web.FIRST_DAY}
    drawn = [step for title in titles for step in by_title[title]]
    assert drawn == [s for s in account.setup_steps(mcp_server.NEEDS) if s != "time_zone"]
    assert drawn == [s for s in STEPS if s != "time_zone" and (routine or s != "routine")]
    assert [steps for steps, _ in FIRST_DAY] == [item["steps"] for item in web.FIRST_DAY]
    assert '<p class="small">If your assistant does not know your time zone' in part
    assert part.index("</ol>") < part.index('<p class="small">')


@pytest.mark.parametrize("on", [False, True], ids=["as offered", "routines and feedback"])
@pytest.mark.parametrize("state", sorted(OVERRIDES))
def test_the_page_keeps_every_rule_a_built_route_keeps(web_client, monkeypatch, state, on):
    """The frame rules (the plain head among them), the page rules, the card rule, every element in main closed, the
    text rules and the title. One h1. No form, field, button or script in main. The links that differ are a.plain-link.
    Six cards, in order."""
    page = hi_in(web_client, state, lambda: offer(monkeypatch, routine=on, feedback=on))
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
    assert cards == [("section", ["card"], i) for i in CARDS]
    assert [n.attrs.get("id") for n in s.dom.find_all("h2")] == CARDS


@pytest.mark.parametrize("state", sorted(OVERRIDES))
def test_the_page_links_to_no_signed_in_page_signed_out_and_names_nothing_it_must_not(web_client, monkeypatch, state):
    page = hi_in(web_client, state, lambda: offer(monkeypatch, routine=True, feedback=True))
    hrefs = re.findall(r'<a\b[^>]*\bhref="([^"]*)"', page)
    signed_in_pages = ("/today", "/day", "/progress", "/goals", "/foods", "/settings")
    if state != "signed in":
        assert not [h for h in hrefs if h.startswith(signed_in_pages)], hrefs
    main = main_of(page)
    assert '<a href="/">the home page</a>' in main and f'<a href="{web.README_TABLE}">the README\'s table</a>' in main
    assert [h for h in re.findall(r'<a\b[^>]*\bhref="([^"]*)"', card(page, "hi-yours"))] == []  # Your say: no link
    words = plain_words(main)
    # No assistant named, no function named, no privacy page. Two lines say what the assistant may do: the landing's
    # switch line in Your say, and the one estimate the assistant may make. Nothing else does.
    assert not re.search(r"\bclaude\b", page, re.I) and not [a for a in web.ASSISTANTS if a in words]
    assert not [n for n in mcp_server.NEEDS if n in main]
    assert not [h for h in hrefs if "privacy" in h.casefold()] and "privacy" not in words.casefold()
    assert words.count(SWITCH_LINE) == 1 and words.count(ESTIMATE_LINE) == 1
    rest = words.replace(SWITCH_LINE, "").replace(ESTIMATE_LINE, "")
    assert not re.search(r"\b(may|allow|allowed|permission)\b", rest, re.I)
    assert "Asha" not in main and "@" not in main  # no account data


def test_each_link_on_the_page_answers(client):
    sign_in(client)
    for _ in ("signed in", "signed out"):
        page = client.get("/hi").text
        for href in re.findall(r'<a\b[^>]*\bhref="(/[^"]*)"', main_of(page)):
            assert client.get(href).status_code in (200, 303), href
        client.cookies.clear()


def test_every_class_in_main_is_one_the_stylesheet_has(client, monkeypatch):
    style = (web.HERE / "static" / "style.css").read_text(encoding="utf-8")
    offer(monkeypatch, routine=True, feedback=True)
    page = client.get("/hi").text
    classes = {c for value in re.findall(r'class="([^"]*)"', main_of(page)) for c in value.split()}
    assert classes and not [c for c in classes if not re.search(rf"\.{re.escape(c)}\b", style)], classes
    assert re.search(r"\.privacy-page > \*", style)


# ---------------------------------------------------------------- the page: each fact from its source

TEMPLATE = web.HERE / "templates" / "hi.html"


def test_no_digit_and_no_count_is_typed_in_the_template():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert not re.search(r"\d", re.sub(r"</?h[1-6]\b", "", source))  # a heading's tag name is the only digit
    for name in ("lifts", "cardio", "measurements", "goal_kinds"):
        assert f'{{{{ keeps.{name}|show("count") }}}}' in source, name
    for name in web.HI_WORKS:
        assert f'{{{{ works.{name}|show("count") }}}}' in source, name
    assert not re.search(r"\{\{ works\.\w+ \}\}", source)  # every number of part 3 through show("count")
    for path in ('href="/"', 'href="/today"', 'href="/sign-in"'):
        assert path in source
    assert "links.page" not in source


def test_every_saying_is_a_constant_and_no_constant_holds_markup():
    """R5: every <q> in the template draws a constant, and nothing turns escaping off."""
    for name in ("hi.html", "home.html"):
        source = (web.HERE / "templates" / name).read_text(encoding="utf-8")
        assert not re.findall(r"<q>(?!\{\{ [\w.]+ \}\}</q>)", source), name
        assert "|safe" not in source and "autoescape" not in source and "Markup" not in source
    strings = [web.SETUP_SAY, *json.dumps([web.FIRST_DAY, web.SAY_GROUPS, web.YOUR_DATA, web.HI_KEEPS]).split('"')]
    assert not [s for s in strings if re.search(r"[<>&]", s)]


def test_the_shared_sayings_are_the_landings_own_objects():
    rows = {label: rows for label, rows in web.SAY_GROUPS}
    log_it, ask = rows["Log it"], rows["Ask"]
    assert log_it[0][0] is web.SAY_EVERYDAY[0] and log_it[1][0] is web.SAY_EVERYDAY[1]
    assert log_it[2] is web.SAY_FIRST[2] and log_it[3][0] is web.SAY_EVERYDAY[2]
    assert ask[0] is web.SAY_FIRST[0] and ask[1] is web.SAY_FIRST[1]
    assert web.SAY_FIRST[0][0] is web.LANDING_EXCHANGE["question"]
    assert [label for label, _ in web.SAY_GROUPS] == ["Log it", "Ask", "Fix or undo", "Goals", "Tell the maker"]
    assert web.SAY_GROUP_NEEDS == {"Tell the maker": FEEDBACK_FUNCTION}


def test_a_no_break_space_sits_before_each_unit_in_a_saying():
    sayings = [web.SETUP_SAY, *(item["say"] for item in web.FIRST_DAY),
               *(item["does"][1] for item in web.FIRST_DAY if len(item["does"]) > 1),
               *(said for _, rows in web.SAY_GROUPS for said, _ in rows)]
    for said in sayings:
        assert not re.search(r"\d (kg|km|kcal|minutes)\b", said), said
    assert sum(said.count(f"{NO_BREAK}kg") + said.count(f"{NO_BREAK}kcal") + said.count(f"{NO_BREAK}minutes")
               for said in sayings) == 12


def test_each_count_and_list_is_what_the_assistant_can_use(client):
    """R6 of hi there: the counts are what list_exercises gives on a fresh database, the goal kinds set_goal's own, the
    meals models.MEALS, the measurements every one of MEASUREMENT_KINDS."""
    page = client.get("/hi").text
    keeps = card(page, "hi-keeps")
    items = [plain_words(li) for li in re.findall(r"<li>(.*?)</li>", keeps, re.S)]
    lifts_count, cardio = (len(services.list_exercises(kind)["exercises"]) for kind in ("lift", "cardio"))
    assert items[0] == f"Lifts. {count(lifts_count)} lifts by name, each session with its sets."
    assert items[1] == f"Cardio. {count(cardio)} activities."
    assert items[2] == (f"Body. Weigh-ins, and {count(len(models.MEASUREMENT_KINDS))} measurements: "
                        f"{listed(measurements.kind_word(k) for k in models.MEASUREMENT_KINDS)}.")
    assert items[3] == f"Food. What you ate, by meal: {listed(models.MEALS)}."
    assert {k for k, _ in web.GOAL_KINDS} == set(SET_GOAL_KINDS) and len(SET_GOAL_KINDS) == len(web.GOAL_KINDS)
    assert items[4] == f"Goals. {count(len(SET_GOAL_KINDS))} kinds: {listed(w for _, w in web.GOAL_KINDS)}."
    assert [i for i in items if i.startswith(f"It knows lifts by its own list of {count(lifts_count)} names.")]


def test_a_list_in_a_sentence_comes_from_one_helper():
    assert web.in_words(["Body weight", "Lift", "Cardio time"]) == "body weight, lift and cardio time"
    assert web.in_words(["Waist", "Hips"]) == "waist and hips" and web.in_words(["Water"]) == "water"


def test_the_landings_sayings_read_on_the_page_as_on_the_landing(client):
    landing = client.get("/").text
    say = re.search(r'<ul class="say">(.*?)</ul>', landing, re.S).group(1)
    landing_first = [plain_words(li) for li in re.findall(r"<li>(.*?)</li>", say, re.S)]
    everyday = landing[landing.index("Everyday logging"):]
    everyday = re.search(r'<ul class="plain-list">(.*?)</ul>', everyday, re.S).group(1)
    landing_everyday = [plain_words(li) for li in re.findall(r"<li>(.*?)</li>", everyday, re.S)]
    assert landing_first == [f"{SAY[n]} {SAY[n + 1]}" for n in (0, 2, 4)] and landing_everyday == list(EVERYDAY)
    hi = shown(client.get("/hi").text)
    for line in (*landing_first, *landing_everyday):
        assert line in hi, line


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
    '    <li>Back squat, 3 sets of 5 at 60 kg</li>',
    '    <li>I weighed 72.5 kg this morning</li>',
    '    <li>30 minutes on the treadmill</li>',
    '  </ul>',
    '</section>',
]


def test_the_landings_say_first_card_is_the_parents_line_for_line(client):
    """The say-list drawn from the two constants renders as the parent's typed list did, each line with its indent."""
    for signed_in in (False, True):
        if signed_in:
            sign_in(client)
        part = re.search(r'<section class="card say-first".*?</section>', client.get("/").text, re.S).group(0)
        assert part.split("\n") == PARENT_SAY_FIRST, part


# The landing's Your data card as the parent sent it (home.html at 6282bf1, lines 131 to 139), line for line.
PARENT_YOUR_DATA = [
    '<section class="card your-data" aria-labelledby="data-title">',
    '  <h2 id="data-title">Your data</h2>',
    '  <ul class="plain-list">',
    '    <li><b>You decide what your assistant may do.</b> A switch on Settings has two settings: "Read, log and '
    'delete" and "Read and log". It starts at "Read, log and delete".</li>',
    '    <li><b>Export.</b> Settings gives you everything you have logged, as a zip of CSV files.</li>',
    '    <li><b>Undo.</b> Right after a delete, Undo brings the entry back. Your assistant can restore an entry it '
    'deleted.</li>',
    '    <li><b>Delete your account.</b> On Settings. It removes every entry and your sign-in, and it cannot be '
    'undone.</li>',
    '  </ul>',
    '</section>',
]


def test_the_landings_your_data_card_is_the_parents_once_its_quote_marks_are_read(client):
    """R8: the four lines, read as words, are the parent's. The markup is the parent's once &#34; reads as a quote
    mark: the template engine writes the quote marks of a drawn sentence that way, and a browser shows the same."""
    for signed_in in (False, True):
        if signed_in:
            sign_in(client)
        part = re.search(r'<section class="card your-data".*?</section>', client.get("/").text, re.S).group(0)
        assert part.count("&#34;") == 6 and part.replace("&#34;", '"').split("\n") == PARENT_YOUR_DATA, part
        assert [plain_words(li) for li in re.findall(r"<li>(.*?)</li>", part, re.S)] == list(YOUR_DATA_WORDS)


def test_your_say_is_the_landings_your_data_from_one_constant(client, monkeypatch):
    """Decision 10: both pages draw web.YOUR_DATA, and the switch's sentence is read from its labels at each render."""
    assert len(web.YOUR_DATA) == 4 and [bold for bold, _ in web.your_data()] == [bold for bold, _ in web.YOUR_DATA]
    assert [f"{bold} {line}" for bold, line in web.your_data()] == list(YOUR_DATA_WORDS)
    for value in web.CLAUDE_CAN:
        monkeypatch.setitem(web.CLAUDE_CAN_LABELS, value, f"Setting {len(value)}")
    landing = re.search(r'<section class="card your-data".*?</section>', client.get("/").text, re.S).group(0)
    hi = card(client.get("/hi").text, "hi-yours")
    items = [[plain_words(li) for li in re.findall(r"<li>(.*?)</li>", part, re.S)] for part in (landing, hi)]
    assert items[0] == items[1] and 'two settings: "Setting 10" and "Setting 3". It starts at "Setting 10".' in items[1][0]
    assert models.CLAUDE_CAN[0] == User.__table__.c.claude_can.default.arg  # the default is named first


def test_the_import_sentences_hold_to_the_import_rule_and_its_kinds():
    assert "The first call is a preview" in mcp_server.IMPORT_RULE
    assert "Write, with dry_run false, only when the person says yes." in mcp_server.IMPORT_RULE
    assert "With dry_run true or left out, nothing is written" in mcp_server.IMPORT_DESCRIPTION
    assert sorted(imports.ORDER) == sorted(("bodyweight", "measurement", "lift_session", "food_entry", "cardio_entry"))


# ---------------------------------------------------------------- the page: part 1's typed facts

def test_the_first_days_lines_hold_to_the_code(make_user):
    # Where you stand: the session's best is the mark to beat.
    assert lifts.first_text("Back Squat", [(80, 5)] * 3).endswith("is the mark to beat.")
    # A goal with a date: set_goal keeps the start, and says the pace a week the goal needs.
    uid = make_user(time_zone="Europe/London").id
    services.log_bodyweight(uid, {"kg": 82}, "claude")
    goal = services.set_goal(uid, {"kind": "bodyweight", "target_value": 78, "target_date": target_date(uid)}, "claude")
    assert goal["goal"]["start_value"] == 82 and goal["progress"]["needed_per_week"] < 0
    # Your targets: the saying's numbers are in the ranges, and set as said.
    said = {"calorie_target_kcal": 2200, "target_sessions_per_week": 4, "target_cardio_minutes_per_week": 150}
    assert all(rules.NUMBERS[field][0] <= value <= rules.NUMBERS[field][1] for field, value in said.items())
    assert services.update_settings(uid, said, "claude")["changed"] == list(said)
    week = services.get_trends(uid)
    assert (week["calories"], week["sessions"]["target_per_week"], week["cardio"]["target_minutes"]) == (None, 4, 150)
    doc = (mcp_server.CAN_AND_CANT).read_text(encoding="utf-8")
    assert "the target and what remains when a target is set" in " ".join(doc.split())
    # Foods you eat often: a saved food goes by its id, and Spotter works out the number.
    assert "A saved or USDA food from find_food goes by its id with servings or grams, and no kcal: Spotter works out " \
           "the number." in mcp_server.FOOD_RULE
    # The time zone line, and the setup note's words: one question at a time, each answer saved.
    assert "ask once which city" in account.TIME_ZONE_NOTE
    assert "one question at a time" in account.SETUP_HOW and "save each answer" in account.SETUP_HOW
    assert "The person may skip any step" in account.SETUP_HOW and "Ask for nothing that is not in missing" in HOW


# ---------------------------------------------------------------- the page: part 2's typed facts

def test_sets_said_one_by_one_go_into_that_days_session(http):
    """R3, the one test that waits for the join with gym logging: log_lifts' published description holds the gym logging
    contract's words. It fails on this work's own tree and passes at the join."""
    [tool] = [t for t in tool_list(http, sign()) if t["name"] == "log_lifts"]
    assert "each later call adds its sets to it" in tool["description"]


def test_what_comes_back_in_part_two_holds_to_the_code(http):
    tools = {t["name"]: t for t in tool_list(http, sign())}
    assert "estimates the calories from the activity's MET value and the person's current body weight" in \
        tools["log_cardio"]["description"]
    lift = inspect.getsource(progress._one)
    assert all(f'"{key}"' in lift for key in ("estimated_max_over_time", "bests", "trend"))
    assert "days: 1 to 14" in log_service.__doc__ and mcp_server.LOG_DAYS[1] == 14
    assert all(word in log_service.__doc__ for word in ("sessions", "weigh-in", "measurements", "food", "cardio"))
    doc = " ".join(mcp_server.CAN_AND_CANT.read_text(encoding="utf-8").split())
    assert "`edit_entry` changes an entry in place and keeps its id" in doc
    assert "It undoes the last edit, once" in doc
    assert "show it to the person and ask, and delete only when they say yes" in mcp_server.DELETE_RULE
    assert "Nothing is ever erased." in doc and "`restore_entry` undoes a delete." in doc
    goal = tools["set_goal"]
    assert "lift (an estimated one-rep max to reach)" in goal["description"]
    assert "hit (reached)" in goal["inputSchema"]["properties"]["status"]["description"]
    assert "Spotter's maker, the person who runs Spotter" in tools["send_feedback"]["description"]


# ---------------------------------------------------------------- the page: part 3, each number and fact

def test_each_number_of_what_spotter_works_out_is_its_constant():
    """Decision 5, R6: the view passes one dict, each value the constant section 4 names."""
    assert web.HI_WORKS == {
        "epley_min": 1, "epley_max": M.EPLEY_MAX_REPS, "recent_days": M.RECENT_BEST_DAYS,
        "average_days": M.BODYWEIGHT_AVG_DAYS, "rate_latest_days": M.RATE_LATEST_DAYS,
        "rate_from": M.RATE_REFERENCE_DAYS[0], "rate_to": M.RATE_REFERENCE_DAYS[1],
        "fit_weeks": M.PROJECTION_DAYS // 7, "fit_points": M.PROJECTION_MIN_POINTS, "fit_days": 2,
        "max_years": M.PROJECTED_MAX_DAYS // 365, "week_days": trends.WEEK_DAYS, "window_days": trends.WINDOW_DAYS,
        "maintenance_days": M.MAINTENANCE_DAYS, "maintenance_logged": M.MAINTENANCE_MIN_LOGGED,
        "maintenance_latest": M.BODYWEIGHT_AVG_DAYS, "maintenance_from": M.MAINTENANCE_DAYS,
        "maintenance_to": M.MAINTENANCE_DAYS + M.BODYWEIGHT_AVG_DAYS - 1}


def test_the_epley_line_holds_at_both_ends():
    """R6: the lower end, 1, has no constant. epley gives no value at 0 and at 11 reps, and one at 1 and at 10. The
    review's L1: only a weighted set gives one, so a bodyweight set, sent at 0 kg, gives none at any reps."""
    low, high = web.HI_WORKS["epley_min"], web.HI_WORKS["epley_max"]
    assert M.epley(100, low - 1) is None and M.epley(100, high + 1) is None
    assert M.epley(100, low) is not None and M.epley(100, high) == 133.33  # Epley: 100 x (1 + 10 / 30)
    assert [M.epley(0, reps) for reps in range(low, high + 1)] == [None] * high
    assert "0 for bodyweight" in SetIn.model_fields["weight_kg"].description


def test_the_fitted_line_needs_its_points_on_two_days():
    """The review's L2: the 2 has no constant. metrics.projection gives no line from 4 points on one day, and gives
    one from the same 4 points spread over 2 days."""
    today, days, points = date(2026, 11, 10), web.HI_WORKS["fit_days"], web.HI_WORKS["fit_points"]
    one_day = [(today, 80 + i / 10) for i in range(points)]
    assert M.projection(one_day, today) is None
    spread = [(today - timedelta(days=i % days), 80 + i / 10) for i in range(points)]
    assert len({d for d, _ in spread}) == days and M.projection(spread, today)["points_used"] == points
    assert M.projection(spread[:-1], today) is None  # 2 days, but one point too few


def test_six_weeks_and_two_years_are_exact():
    assert M.PROJECTION_DAYS % 7 == 0 and M.PROJECTION_DAYS // 7 == 6
    assert M.PROJECTED_MAX_DAYS % 365 == 0 and M.PROJECTED_MAX_DAYS // 365 == 2


def test_the_typed_words_of_what_spotter_works_out_hold_to_the_code():
    today = date(2026, 11, 10)
    assert "never estimate your own, except a food's kcal" in mcp_server.INSTRUCTIONS
    # A new best, by that estimate or by weight.
    assert [p["kind"] for p in M.prs([(100, 5)], [(110, 5)])] == ["estimated_max", "weight"]
    assert [p["kind"] for p in M.prs([(100, 5)], [(100, 6)])] == ["estimated_max"]
    assert [p["kind"] for p in M.prs([(100, 10)], [(120, 12)])] == ["weight"]
    assert M.prs([], [(100, 5)]) == []
    # Your lift now: the best of the last 28 days, or the latest before that.
    old, light = today - timedelta(days=M.RECENT_BEST_DAYS + 5), today - timedelta(days=1)
    assert M.recent_best([(old, 120), (light, 100), (today - timedelta(days=3), 110)], today) == 110
    assert M.recent_best([(old - timedelta(days=9), 130), (old, 120)], today) == 120
    # Body weight now: the 7-day average, or the latest.
    assert M.bodyweight_now([(today, 80), (today - timedelta(days=1), 81)], today) == 80.5
    assert M.bodyweight_now([(today - timedelta(days=20), 79)], today) == 79
    # A tenth of the way.
    assert M.EWMA_ALPHA == 0.1 and M.ewma([(today - timedelta(days=1), 80), (today, 81)], today)[-1][1] == 80.1
    # Where you land: on track when the line reaches the target by the date, behind when not.
    falling = [(today - timedelta(days=d), 80 + d / 7) for d in (21, 14, 7, 0)]
    soon, far = today + timedelta(days=35), today + timedelta(days=7)
    assert M.projection(falling, today, goal_target=76, goal_date=soon, lower_is_better=True)["status"] == "on track"
    assert M.projection(falling, today, goal_target=76, goal_date=far, lower_is_better=True)["status"] == "behind"
    # No day when flat, pointing away, or more than 2 years out.
    assert M.projected_date(0, 80, today, 76, today, lower_is_better=True) is None
    assert M.projected_date(0.1, 80, today, 76, today, lower_is_better=True) is None
    assert M.projected_date(-0.001, 80, today, 76, today, lower_is_better=True) is None
    assert M.projected_date(-0.1, 80, today, 76, today, lower_is_better=True) == today + timedelta(days=40)
    # The plan line: from the start to the target by their dates, and how far ahead or behind today.
    path = M.goal_path(today - timedelta(days=10), 80, today + timedelta(days=10), 78, [], today, True, "weight_kg",
                       now=79.5)
    assert [p["value"] for p in path["plan"]] == [80, 79, 78] and path["vs_plan"] == "behind"
    # Calories: logged days only, and the share that is estimates.
    assert "Calories are averages over logged days" in trends.TRENDS_HOW
    assert '"estimated_share_pct_28d"' in inspect.getsource(trends._calories)
    # Cardio calories, and maintenance never changes the target.
    assert M.cardio_kcal(8, 80, 30) == round(8 * 3.5 * 80 / 200 * 30)
    assert "never changes their calorie target" in trends.TRENDS_HOW


def test_cardio_with_no_weigh_in_has_no_estimate(make_user):
    uid = make_user(time_zone="Europe/London").id
    logged = services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 30}, "claude")
    assert logged["estimate"] is None


# ---------------------------------------------------------------- the page: part 4's typed facts

def test_what_spotter_keeps_and_does_not_do_holds_to_the_code(http, make_user):
    assert web.TARGET_FIELDS == ("calorie_target_kcal", "target_sessions_per_week", "target_cardio_minutes_per_week")
    doc = mcp_server.CAN_AND_CANT.read_text(encoding="utf-8")
    cannot = doc[doc.index("## What it cannot tell you"):doc.index("## Duplicates")]
    assert CANNOT_LINE in cannot and "It gives no medical advice." in cannot
    assert "weight_kg" in SetIn.model_fields
    tools = {t["name"]: t for t in tool_list(http, sign())}
    assert "distance_km" in tools["log_cardio"]["inputSchema"]["properties"]
    assert "lb x 0.45359237, mi x 1.609344" in " ".join(doc.split())
    columns = set(FoodEntry.__table__.c.keys())
    assert "kcal" in columns and not [c for c in columns if re.search(r"protein|carb|fat", c)]
    refused = services.log_lifts(make_user().id, {"sets": [{"exercise": "Zorbinator Press", "weight_kg": 20,
                                                             "reps": 8}]}, "claude")
    assert refused["error"] == "unknown_exercise"
    assert not [n for n in mcp_server.NEEDS if re.search(r"remind|notif|email|sync", n)]


# ---------------------------------------------------------------- the page: open, the same for everyone

@pytest.mark.parametrize("state", ["signed out", "no web sign-in", "no server address"])
def test_signed_out_the_page_reads_no_database_and_keeps_no_cookie(web_client, monkeypatch, state):
    client = web_client(**OVERRIDES[state])
    offer(monkeypatch, routine=True, feedback=True)

    def no_database(*args, **kwargs):
        raise AssertionError("the page read the database")

    for name in ("session_scope", "new_session", "get_engine"):
        monkeypatch.setattr(db, name, no_database)
    r = client.get("/hi")
    assert r.status_code == 200 and "set-cookie" not in r.headers


def test_a_session_naming_a_person_who_is_gone_draws_the_signed_out_page_and_is_cleared(client, monkeypatch):
    """R2: a validly signed session whose person no longer exists costs one read and is cleared, as on the landing."""
    offer(monkeypatch)
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
            assert shown(r.text) == want_words("signed out")


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


# The routines page's three rows (routines.md), straight after the Foods restore row, all behind the page limit.
ROUTINES_ROUTES = [("/routines", "GET HEAD", True), ("/routines/{routine_id}/delete", "POST", True),
                   ("/routines/{routine_id}/restore", "POST", True)]


@pytest.mark.parametrize("state", ["signed out", "no web sign-in", "no server address"])
def test_the_route_tables_differ_from_before_by_hi_alone(state):
    """R14: the parent's rows plus one, /hi straight after /, GET and HEAD, not behind the page limit. At the join,
    routines' three rows too, after the Foods restore row, and nothing else."""
    at = PARENT_ROUTES.index(("/foods/{food_id}/restore", "POST", True)) + 1
    joined = PARENT_ROUTES[:at] + ROUTINES_ROUTES + PARENT_ROUTES[at:]
    before = joined if state == "signed out" else [(p, m, False) for p, m, _ in joined]
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
    assert "/hi" in web.__doc__ and "first-day.md" in web.__doc__ and "hi_there" in links.__doc__
    assert "new_account_note" in account.__doc__ and "SETUP_STEPS" in account.__doc__
    assert "first-day.md" in TEMPLATE.read_text(encoding="utf-8") and "setup_steps" in mcp_schemas.__doc__


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
# The setup step each row stands for, if any.
STEP_OF = {"bodyweight": "weigh_in", "lift_session": "lift_session", "goal": "goal", "saved_food": "saved_food"}
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


def missing(who: dict) -> list[str] | None:
    """setup.missing, or None when whoami carries no setup. how is always the constant's words."""
    if "setup" not in who:
        return None
    assert who["setup"]["how"] == HOW and list(who["setup"]) == ["missing", "how"]
    return who["setup"]["missing"]


def test_an_empty_account_carries_the_note_and_the_link(make_user):
    zoned = services.whoami(make_user(time_zone="Europe/London").id)
    held(zoned, True)
    assert "time_zone_note" not in zoned
    no_zone = services.whoami(make_user().id)  # no time zone: both notes, the new one after the other
    held(no_zone, True)
    keys = list(no_zone)
    assert "time_zone_note" in no_zone and keys.index("time_zone_note") < keys.index("new_account_note")
    assert keys[-2:] == ["new_account_note", "setup"]  # setup last, after the note (R11)
    assert tr.check(NOTE, tr.MESSAGE) == [] and tr.check(NOTE, tr.CLAUDE) == []


@pytest.mark.parametrize("kind, close", STATES, ids=[f"{k} {c}" for k, c in STATES])
def test_the_note_goes_at_the_first_row_and_comes_back_when_it_goes(make_user, kind, close):
    """And the setup steps read the same answers: the row's step goes with the note and comes back with it."""
    user_id = make_user(time_zone="Europe/London").id
    who = services.whoami(user_id)
    held(who, True)
    assert missing(who) == NO_ROUTINE_STEPS[1:]  # nothing given: no routine step
    make_one(kind, user_id)
    who = services.whoami(user_id)
    held(who, False)
    assert missing(who) == [s for s in NO_ROUTINE_STEPS[1:] if s != STEP_OF.get(kind)]
    assert services.account_empty(user_id) == {"empty": False}
    take_it_away(kind, close, user_id)
    who = services.whoami(user_id)
    held(who, True)
    assert missing(who) == NO_ROUTINE_STEPS[1:]  # a deleted row and a closed goal do not count
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
        assert (STEP_OF.get(kind, "weigh_in") in missing(who)) is (new or kind not in STEP_OF)

    agree(True)
    make_one(kind, user_id)
    agree(False)
    take_it_away(kind, close, user_id)
    agree(True)


class OldLinks(BaseModel):
    settings: str
    home: str
    hi_there: str


class OldWhoami(BaseModel):
    """whoami's result model as it was published before this work (at 6282bf1)."""
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
    new_account_note: str | None = None


def test_a_result_with_setup_fails_the_old_schema_on_setup_alone_and_one_with_only_import_left_passes(http):
    """R15, the known limit."""
    token = sign()
    empty = whoami(http, token)
    with pytest.raises(ValidationError) as refused:
        OldWhoami.model_validate(empty)
    # units.md's known limit: whoami now carries units too, which the old schema refuses as it refuses setup.
    assert [(e["type"], e["loc"]) for e in refused.value.errors()] == [("extra_forbidden", ("units",)),
                                                                       ("extra_forbidden", ("setup",))]
    OldWhoami.model_validate({k: v for k, v in empty.items() if k not in ("setup", "units")})
    do_every_step_but_import(empty["account_id"])
    done = whoami(http, token)
    assert "setup" not in done and "new_account_note" not in done
    OldWhoami.model_validate({k: v for k, v in done.items() if k != "units"})


# ---------------------------------------------------------------- whoami: setup

def do_step(step: str, user_id: int) -> None:
    """Do one setup step the way the person's assistant would."""
    if step in ("time_zone", "calorie_target", "sessions_target", "cardio_target"):
        field, value = {"time_zone": ("time_zone", "Europe/London"), "calorie_target": ("calorie_target_kcal", 2200),
                        "sessions_target": ("target_sessions_per_week", 4),
                        "cardio_target": ("target_cardio_minutes_per_week", 150)}[step]
        assert services.update_settings(user_id, {field: value}, "claude")["changed"] == [field]
    elif step == "import":  # a resting heart rate: no other step's row, and no unit to name
        assert services.update_settings(user_id, {"time_zone": "Europe/London"}, "claude")
        day = (date.fromisoformat(services.whoami(user_id)["today"]) - timedelta(days=30)).isoformat()
        done = services.import_entries(user_id, {"dry_run": False, "rows": [
            {"kind": "measurement", "date": day, "measurement_kind": "resting_hr", "value": 60}]}, "claude")
        assert "error" not in done and done["counts"], done
    elif step == "routine":
        assert "error" not in services.save_routine(user_id, ROUTINE, "claude")
    else:
        make_one({"weigh_in": "bodyweight", "lift_session": "lift_session", "goal": "goal",
                  "saved_food": "saved_food"}[step], user_id)


def do_every_step_but_import(user_id: int) -> None:
    for step in OFFERED_STEPS[:-1]:
        do_step(step, user_id)


def test_a_fresh_account_is_asked_every_step_on_offer_in_order(make_user, http):
    assert account.SETUP_STEPS == STEPS and account.SETUP_NEEDS == {"routine": ROUTINE_FUNCTION}
    assert list(account.setup_steps(mcp_server.NEEDS)) == OFFERED_STEPS
    assert missing(services.whoami(make_user().id, mcp_server.NEEDS)) == OFFERED_STEPS
    assert missing(services.whoami(make_user().id)) == NO_ROUTINE_STEPS  # nothing given: no step that needs a function
    assert missing(services.whoami(make_user(time_zone="Asia/Kolkata").id)) == NO_ROUTINE_STEPS[1:]
    who = whoami(http, sign())  # through MCP: the published schema judges it
    WhoamiResult.model_validate(who)
    assert missing(who) == OFFERED_STEPS and list(who)[-2:] == ["setup", "sign_in"]


@pytest.mark.parametrize("step", OFFERED_STEPS)
def test_each_step_done_alone_leaves_only_the_others(make_user, step):
    user_id = make_user().id
    do_step(step, user_id)
    left = [s for s in OFFERED_STEPS if s != step and not (step == "import" and s == "time_zone")]
    assert missing(services.whoami(user_id, mcp_server.NEEDS)) == left


def test_the_steps_done_one_after_another_leave_only_the_missing_ones(http):
    token = sign()
    user_id = whoami(http, token)["account_id"]
    for n, step in enumerate(OFFERED_STEPS[:-1]):
        do_step(step, user_id)
        for who in (services.whoami(user_id, mcp_server.NEEDS), whoami(http, token)):
            left = OFFERED_STEPS[n + 1:]
            assert missing(who) == (left if left != ["import"] else None), step  # import alone keeps no setup


def test_setup_goes_once_only_import_is_missing_and_comes_back_when_a_step_is_undone(make_user):
    user_id = make_user().id
    do_every_step_but_import(user_id)
    assert missing(services.whoami(user_id, mcp_server.NEEDS)) is None
    take_it_away("bodyweight", "deleted", user_id)
    assert missing(services.whoami(user_id, mcp_server.NEEDS)) == ["weigh_in", "import"]
    services.update_settings(user_id, {"calorie_target_kcal": None}, "claude")
    assert missing(services.whoami(user_id, mcp_server.NEEDS)) == ["weigh_in", "calorie_target", "import"]


def test_a_sessions_target_of_zero_counts_as_set(make_user):
    user_id = make_user(time_zone="Europe/London").id
    services.update_settings(user_id, {"target_sessions_per_week": 0}, "claude")
    assert "sessions_target" not in missing(services.whoami(user_id))
    services.update_settings(user_id, {"target_sessions_per_week": None}, "claude")
    assert "sessions_target" in missing(services.whoami(user_id))


def test_an_imported_row_marks_import_done_and_a_row_logged_by_hand_does_not(make_user):
    user_id = make_user(time_zone="Europe/London").id
    for kind in MAKE:  # a live row of every kind, each logged by hand
        make_one(kind, user_id)
    assert missing(services.whoami(user_id))[-1] == "import"
    do_step("import", user_id)
    assert "import" not in missing(services.whoami(user_id))
    with db.session_scope() as session:
        row = session.scalar(select(Measurement).where(Measurement.logged_by == models.IMPORTED))
        row.deleted_at = db.utcnow()  # a deleted imported row does not count
    assert missing(services.whoami(user_id))[-1] == "import"


def test_another_persons_rows_of_every_kind_change_nothing(make_user):
    mine, theirs = make_user().id, make_user().id
    before = services.whoami(mine, mcp_server.NEEDS)
    for kind in MAKE:
        make_one(kind, theirs)
    for step in ("time_zone", "calorie_target", "sessions_target", "cardio_target", "routine", "import"):
        do_step(step, theirs)
    assert missing(services.whoami(theirs, mcp_server.NEEDS)) is None
    after = services.whoami(mine, mcp_server.NEEDS)
    assert after == before and missing(after) == OFFERED_STEPS and after["new_account_note"] == NOTE


def test_the_routine_step_is_on_offer_only_while_save_routine_is(make_user, http, monkeypatch):
    """Decision 8, R9, and routines' decision 20: as built, save_routine is offered, so the step is on offer; taken out
    of NEEDS, it is not. Through the service with what is offered given, and through MCP with NEEDS changed."""
    assert ROUTINE_FUNCTION in mcp_server.NEEDS
    without = {name: level for name, level in mcp_server.NEEDS.items() if name != ROUTINE_FUNCTION}
    user_id = make_user().id
    assert missing(services.whoami(user_id, mcp_server.NEEDS)) == list(STEPS)
    assert missing(services.whoami(user_id, without)) == NO_ROUTINE_STEPS
    for step in NO_ROUTINE_STEPS[:-1]:
        do_step(step, user_id)
    assert missing(services.whoami(user_id, mcp_server.NEEDS)) == ["routine", "import"]
    assert missing(services.whoami(user_id, {ROUTINE_FUNCTION: "log"})) == ["routine", "import"]
    assert missing(services.whoami(user_id, without)) is None
    assert missing(services.whoami(user_id, ())) is None
    token = sign()
    assert missing(whoami(http, token)) == list(STEPS)  # NEEDS as built
    offer(monkeypatch, routine=False, feedback=True)
    assert missing(whoami(http, token)) == NO_ROUTINE_STEPS
    offer(monkeypatch, routine=True, feedback=True)
    assert missing(whoami(http, token)) == list(STEPS)


def test_a_live_routine_of_the_persons_own_marks_the_step_done_and_another_persons_or_a_deleted_one_does_not(
        make_user):
    """The join: whoami reads a live routine of the person's own. A routine does not end a new account (routines.md
    decision 12), so the note stays throughout."""
    mine, theirs = make_user().id, make_user().id
    do_step("routine", theirs)
    assert "routine" not in missing(services.whoami(theirs, mcp_server.NEEDS))
    who = services.whoami(mine, mcp_server.NEEDS)
    assert missing(who) == OFFERED_STEPS and who["new_account_note"] == NOTE
    made = services.save_routine(mine, ROUTINE, "claude")
    assert "error" not in made
    who = services.whoami(mine, mcp_server.NEEDS)
    assert missing(who) == NO_ROUTINE_STEPS and who["new_account_note"] == NOTE
    assert "error" not in services.delete_entry(mine, "routine", made["id"])
    who = services.whoami(mine, mcp_server.NEEDS)
    assert missing(who) == OFFERED_STEPS and who["new_account_note"] == NOTE  # a deleted routine does not count
    assert "error" not in services.restore_entry(mine, "routine", made["id"], "claude")
    assert missing(services.whoami(mine, mcp_server.NEEDS)) == NO_ROUTINE_STEPS


def test_the_step_names_in_the_fields_description_are_the_steps_on_offer(http):
    schema = {t["name"]: t["outputSchema"] for t in tool_list(http, sign())}["whoami"]
    field = schema["properties"]["setup"]
    text = field["description"]
    named = text[text.index("ask them: ") + len("ask them: "):text.index(". how:")].split(", ")
    assert named == list(account.setup_steps(mcp_server.NEEDS)) == OFFERED_STEPS
    assert {"$ref": "#/$defs/Setup"} in field["anyOf"] and "setup" not in schema.get("required", [])
    assert schema["$defs"]["Setup"]["required"] == ["missing", "how"]


def test_whoami_reads_at_most_fourteen_statements_and_takes_no_lock_and_no_write_limit(make_user, monkeypatch):
    """R10: the user row, then the seven questions, then one for a live routine (the join), then up to five for import,
    each a select. Nothing else."""
    def refused(*args, **kwargs):
        raise AssertionError("whoami locked or counted a write")

    fresh, user_id = make_user().id, make_user().id
    for kind in MAKE:  # a live row in every entry table, none imported: the most reads
        make_one(kind, user_id)
    monkeypatch.setattr(account, "lock_user", refused)
    monkeypatch.setattr(account, "check_write_limit", refused)
    monkeypatch.setattr(db, "session_scope", refused)
    for user, want in ((fresh, 1 + 7 + 1), (user_id, 1 + 7 + 1 + 5)):
        statements = []
        engine = db.get_engine()
        record = lambda *a: statements.append(" ".join(a[2].split()))  # noqa: E731
        event.listen(engine, "before_cursor_execute", record)
        try:
            who = services.whoami(user, mcp_server.NEEDS)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert "setup" in who and len(statements) == want <= 14, statements
        assert all(s.startswith("SELECT ") and "FOR UPDATE" not in s for s in statements), statements


def rows_of(user_id: int) -> list[tuple]:
    """Every stored column of every row the account owns, its user row first."""
    with db.session_scope() as session:
        out = [tuple(session.execute(select(User.__table__).where(User.id == user_id)).one())]
        for model in (*account.OWNED, ):
            out += [tuple(r) for r in session.execute(select(model.__table__).where(model.user_id == user_id))]
        out += [tuple(r) for r in session.execute(select(LiftSet.__table__).join(LiftSession)
                                                  .where(LiftSession.user_id == user_id))]
    return out


def test_whoami_writes_nothing_the_same_account_before_and_after_three_calls(make_user):
    """Decision 14: no stored column of any of the account's rows moves."""
    user_id = make_user(time_zone="Europe/London").id
    for kind in MAKE:
        make_one(kind, user_id)
    before = rows_of(user_id)
    assert len(before) == 1 + len(MAKE) + 1  # the user, one row each, and the session's set
    for _ in range(3):
        assert "setup" in services.whoami(user_id, mcp_server.NEEDS)
    assert rows_of(user_id) == before


# ---------------------------------------------------------------- what the assistant reads

def test_the_new_texts_are_the_contracts_and_none_says_page_or_form(http):
    token = sign()
    [tool] = [t for t in tool_list(http, token) if t["name"] == "whoami"]
    schema = tool["outputSchema"]
    doc = read_resource(http, token, "spotter://can-and-cant")["text"]
    section = doc[doc.index("## A new person"):doc.index("## Setting up")].rstrip() + "\n"
    setting_up = doc[doc.index("## Setting up"):doc.index("## What Claude may do")].rstrip() + "\n"
    who = whoami(http, token)
    texts = {"note": who["new_account_note"], "how": who["setup"]["how"],
             "link": schema["$defs"]["WhoamiLinks"]["properties"]["hi_there"]["description"],
             "sentence": tool["description"].split(" It carries units:")[0].rsplit(". ", 1)[-1],  # units.md
             "field": schema["properties"]["new_account_note"]["description"],
             "setup field": schema["properties"]["setup"]["description"],
             "missing": schema["$defs"]["Setup"]["properties"]["missing"]["description"],
             "how field": schema["$defs"]["Setup"]["properties"]["how"]["description"],
             "targets": schema["properties"]["targets"]["description"],
             "section": section, "setting up": setting_up}
    assert texts == {"note": NOTE, "how": HOW, "link": LINK_DESCRIPTION, "sentence": SENTENCE,
                     "field": FIELD_DESCRIPTION, "setup field": SETUP_FIELD, "missing": MISSING_FIELD,
                     "how field": HOW_FIELD, "targets": TARGETS_FIELD, "section": SECTION, "setting up": SETTING_UP}
    for where, text in texts.items():
        assert not re.search(r"\b(page|form)\b", text, re.I), where
        assert not [s for s in sentences(text) if sends_to_a_page(s)], where
        if where in ("section", "setting up"):  # each doc line on its own, the heading aside
            assert [f for line in text.split("\n")[1:] if line for f in tr.check(line, tr.DOC)] == [], where
        else:  # what the model reads, and the two it may say on as written: the note and how
            surfaces = (tr.CLAUDE, tr.MESSAGE) if where in ("note", "how") else (tr.CLAUDE,)
            assert [f for surface in surfaces for f in tr.check(text, surface)] == [], where
    assert tool["description"].split(" It carries units:")[0].endswith(f" {NOTE_SENTENCE} {SENTENCE}")  # units
    assert doc.index("## What \"today\" means") < doc.index("## A new person") < doc.index("## Setting up") \
        < doc.index("## What Claude may do")
    assert re.search(r"\n\n## Setting up\n\n", doc) and "\n\n## What Claude may do" in doc
    cannot = doc[doc.index("## What it cannot tell you"):doc.index("## Duplicates")]
    assert cannot.split("\n")[2] == CANNOT_LINE and "sleep" not in cannot and tr.check(CANNOT_LINE, tr.DOC) == []
    assert "hi_there" in schema["$defs"]["WhoamiLinks"]["required"]
    assert "new_account_note" not in schema.get("required", [])


def test_the_page_sending_test_reads_the_note_and_keeps_its_one_exception(http):
    texts = model_texts(http, sign())
    assert texts["whoami new_account_note"] == NOTE and texts["whoami time_zone_note"]
    texts["whoami setup.how"] = whoami(http, sign())["setup"]["how"]
    sending = [(where, s) for where, text in texts.items() for s in sentences(text) if sends_to_a_page(s)]
    assert sending == []  # the one exception is the not_allowed fix, read by the test in test_mcp.py
    assert len(mcp_server.INSTRUCTIONS) == 3927  # switch decision 18, unchanged here (decision 11)


def test_the_new_names_are_in_whoami_alone_in_the_published_list(http):
    tools = tool_list(http, sign())
    assert tools and "whoami" in [tool["name"] for tool in tools]
    for tool in tools:
        text = json.dumps(tool)
        assert ("hi_there" in text and "new_account_note" in text) is (tool["name"] == "whoami"), tool["name"]
        assert ("setup.missing" in text and '"Setup"' in text) is (tool["name"] == "whoami"), tool["name"]


# ---------------------------------------------------------------- the eval cases

def test_the_two_eval_cases_load_and_judge_as_designed(app_settings):
    """R15 of hi there, by hand both ways: with the link in the reply and without. The first runs on an empty account,
    so its whoami carries the note. The second's seed is a weigh-in the day before, so its whoami does not."""
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


SETUP_CASES = {
    "setup_asks_first": {
        "id": "setup_asks_first", "say": "Hi Spotter, help me set up",
        "expect": {"function": "whoami", "reply_asks": True, "reply_has": ["weigh"],
                   "no_call": ["log_bodyweight", "log_lifts", "set_goal", "update_settings", "save_food", "log_food",
                               "log_cardio", "log_measurement", "import_entries", "save_routine"]},
        "db": [{"table": "bodyweight", "count": 0}, {"table": "goal", "count": 0}]},
    "setup_picks_up": {
        "id": "setup_picks_up", "say": "Help me finish setting up Spotter",
        "seed": [{"as": "weigh_in", "service": "log_bodyweight", "data": {"kg": 80.9, "date": "{{yesterday}}"}},
                 {"as": "goal", "service": "set_goal",
                  "data": {"kind": "bodyweight", "target_value": 77, "target_date": "{{next_month_end:3}}"}}],
        "expect": {"function": "whoami", "reply_asks": True, "reply_has": ["lift"],
                   "no_call": ["log_bodyweight", "set_goal", "log_lifts", "update_settings", "save_food",
                               "import_entries", "save_routine"]},
        "db": [{"table": "bodyweight", "count": 1}, {"table": "goal", "count": 1}]},
}


def test_the_two_setup_eval_cases_load_and_judge_as_designed(app_settings):
    """Decision 15, R16: section 8's two cases, straight after hi_there_not_again, judged by hand both ways. The
    seeded person's own whoami asks first for a weigh-in, then, with a weigh-in and a goal held, for a lift."""
    cases = judge.load_cases()
    ids = [c["id"] for c in cases]
    at = ids.index("hi_there_not_again")
    assert ids[at + 1:at + 3] == list(SETUP_CASES)
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    zone = "Asia/Kolkata"
    dates = dates_for(zone)
    whoami_call = {"name": "whoami", "arguments": {}, "is_error": False}
    for name, first_step, good, word in (("setup_asks_first", "weigh_in", "What do you weigh at the moment?", "weigh"),
                                         ("setup_picks_up", "lift_session", "Any lift you did lately? Say the sets.",
                                          "lift")):
        case = cases[at + 1 + list(SETUP_CASES).index(name)]
        assert case == SETUP_CASES[name] and judge.case_problems(case) == []
        user_id, _, seeded = server.new_person(zone, case.get("seed", []))
        assert missing(services.whoami(user_id, mcp_server.NEEDS))[0] == first_step, name
        resolved = judge.resolve(case, {**dates, **{f"seed.{k}": v for k, v in seeded.items()}})
        rows = person_rows(user_id)
        assert word in good and judge.judge(resolved, [whoami_call], good, rows) == [], name
        assert judge.judge(resolved, [whoami_call], good.replace("?", "."), rows), name  # no question
        assert judge.judge(resolved, [whoami_call], "Which city are you in?", rows), name  # without the word
        assert judge.judge(resolved, [], good, rows), name  # no whoami
        wrote = "log_bodyweight" if name == "setup_asks_first" else "log_lifts"
        assert judge.judge(resolved, [whoami_call, {"name": wrote, "arguments": {}, "is_error": False}], good, rows), \
            name  # a run that also writes


def test_the_quiet_setup_case_loads_straight_before_the_routine_cases_and_judges_as_designed(app_settings):
    """The review's S1: setup_stays_quiet, the last setup case, straight before routines' three, which close the file.
    Its person has a weigh-in from the day before, so whoami
    carries setup and no note, and nobody asked to set up. A reply that only logs passes. One that asks a setup
    question, or speaks of setting up, or writes a setup step, fails."""
    cases = judge.load_cases()
    case = cases[-10]  # units' six follow routines' three (units.md decision 20)
    assert [c["id"] for c in cases[-10:-6]] == ["setup_stays_quiet", "routine_saved", "routine_logged",
                                                "routine_changed"]
    assert case["id"] == "setup_stays_quiet" and judge.case_problems(case) == []
    assert case["say"] == "I weighed 80.4 kg this morning"
    assert case["seed"] == [{"as": "earlier", "service": "log_bodyweight",
                             "data": {"kg": 80.9, "date": "{{yesterday}}"}}]
    assert case["expect"]["reply_lacks"] == ["?"]
    server = LocalServer(Path(tempfile.gettempdir()))  # not started: only its people are used
    server.settings = app_settings
    dates = dates_for("Asia/Kolkata")
    user_id, _, seeded = server.new_person("Asia/Kolkata", case["seed"])
    before = services.whoami(user_id, mcp_server.NEEDS)
    assert "new_account_note" not in before and missing(before)[0] == "lift_session"
    resolved = judge.resolve(case, {**dates, **{f"seed.{k}": v for k, v in seeded.items()}})
    logged = [{"name": "whoami", "arguments": {}, "is_error": False},
              {"name": "log_bodyweight", "arguments": {"kg": 80.4}, "is_error": False}]
    plain = "Logged 80.4 kg for today, down from 80.9 kg yesterday."
    assert judge.judge(resolved, logged, plain, person_rows(user_id))  # not logged yet: the db check fails
    assert "error" not in services.log_bodyweight(user_id, {"kg": 80.4}, "claude")
    rows = person_rows(user_id)
    assert judge.judge(resolved, logged, plain, rows) == []
    for reply in (plain + " Any lift you did lately? Say the sets.",  # a setup question
                  plain + " Want to set a goal next?",
                  plain + " Next, let's carry on with your setup.",  # no question mark, but setup
                  plain + " We can keep setting up: tell me your calorie target.",
                  plain + " Say when you want to finish the set-up."):
        assert judge.judge(resolved, logged, reply, rows), reply
    for wrote in ("log_lifts", "set_goal", "update_settings", "save_food", "import_entries", "save_routine"):
        assert judge.judge(resolved, [*logged, {"name": wrote, "arguments": {}, "is_error": False}], plain, rows), \
            wrote  # a run that also writes a setup step
    assert judge.judge(resolved, logged[:1], plain, rows)  # no log_bodyweight
