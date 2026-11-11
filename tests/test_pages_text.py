"""T3, the text part (stage 6, step 2): the words on every page a person reads, held to the page rules.

Every GET path in web.PAGES is rendered with fixture values for each parameter, signed in, and / signed
in and out, on three accounts: empty, sample, and an edge case (a zero rate, over target, a day over
1,000 kcal, a cardio time goal, every measurement kind). The pages behind an address mark (?saved=,
?deleted=, ?notes=) are rendered too, since a bare GET never shows their text.

Each response is read with text_rules.html_text, and each piece checked with text_rules.check for its
surface: a "title" piece as a title, every other piece as a page. Text the person entered (names, notes,
descriptions, the sign-in id, the email, the zone name) is masked first: it is theirs, not the page's.

Two cases need care.
- The empty-figure rule includes a bare "0". A true count of zero is a real figure, so "0" passes only in
  a cell named in TRUE_ZEROS, and only as many times as the account's own data holds that zero.
- A zone name such as "America/New_York" on Settings is the person's own setting, not a code word: it is
  masked with the entered text.

Confirmations (the line after a write, class "ok") name their subject: a bare word such as "Deleted." fails.
Every number on a page has a comma from 1,000 and no trailing zero, so a figure that skipped the formatter fails.

Step 4 adds, on the same pages:
- Furniture, on every page now: the head's title is exactly the page's title from section L's table,
  "{Page} · Spotter", and the head carries the three favicon links.
- No visible text says Claude outside the landing's list of assistants (ul.assistants). A page that still
  says Claude carries one named skip in CLAUDE_SKIPS until its own pass rebuilds it.
- Structure (a Notes column, the Trends table's remark column, units in headers, estimate tags in their own
  slot, no column blank in every row): checked only on the routes in BUILT. Each step 4 pass adds its own
  route there when it rebuilds the page.
- The 404 and 500 pages (an unrouted path, a routed 404, a forced 500) and one render of the message
  template for each status section L lists, under the same text, furniture and Claude checks
  (ERROR_PAGES, render_error_page).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser

import pytest
from sqlalchemy import select

from app import db, formatting, services, web
from app.models import Bodyweight, CardioEntry, FoodEntry, Goal, LiftSession, Measurement, SavedFood, User

from . import text_rules as tr
from .test_web import client, only_user, seam, sign_in, today_of, web_client  # noqa: F401 - fixtures


@dataclass(frozen=True)
class Skip:
    """A finding T3 lets through: on paths starting with `path`, of `rule`, matching `match`, and when `tag`
    is given, only in a piece inside that element."""
    path: str
    rule: str
    match: str
    why: str
    tag: str = ""


CONFIRMATION = "confirmation"  # this file's own rule: a confirmation names its subject

# What today's pages keep through step 2 (contract section C, "Step 2 does not reshape today's pages").
SKIPS = (
    Skip("/foods", CONFIRMATION, "Saved.", "foods.html:7, the bare Saved., replaced in step 4"),
    Skip("/settings", CONFIRMATION, "Saved.", "settings.html:72, the bare Saved., replaced in step 4"),
    Skip("/", tr.CODE_WORD, "whoami", "home.html:37, the first sentence offered, replaced in step 4"),
    Skip("/settings", tr.CODE_WORD, "whoami", "settings.html:15, replaced in step 4"),
    # Not figures: the names in the one attribution line (rule 15), "2024 Adult Compendium" and "CC BY-NC-ND 4.0".
    Skip("", tr.THOUSANDS, "2024", "a year in the Compendium's name, web.ATTRIBUTION", tag="footer"),
    Skip("", tr.TRAILING_ZERO, "4.0", "the licence's version, web.ATTRIBUTION", tag="footer"),
    # The Not set up yet page names the setting the server's owner must fill in (web.problem), and
    # test_web.py holds that it does. Kept as it is in pass B2, for the lead to rule on. T3 renders it at
    # /sign-in, an address with no page of its own, so the skip excuses no other page.
    Skip("/sign-in", tr.CODE_WORD, "WORKOS_CLIENT_ID", "web.problem, the setting to fill in", tag="p"),
    Skip("/sign-in", tr.CODE_WORD, "WORKOS_API_KEY", "web.problem, the setting to fill in", tag="p"),
)
# Beyond the page surface's rules: every number on a page has a comma from 1,000 and no trailing zero
# (contract section C, "Every number, every quantity"). A raw figure from a service trips one of them.
NUMBER_RULES = ("thousands", "trailing_zero")
# Also kept through step 2, and not checked by this file's rules at all: units in headers, the estimate
# tag beside the day's total ("of which", day.html:78), remarks inside value cells ("(food minus cardio)",
# day.html:84), the Notes columns, and the Goals page printing the service's message and fix
# (goals.html:66-67, which only a refused POST renders).

# Where a bare "0" is a true count the page reads from the data, and how many the account holds.
TRUE_ZEROS = {
    ("/foods", "td"): lambda user_id: sum(
        (f["times_logged"] == 0) + (f["kcal_per_serving"] == 0)
        for f in services.list_saved_foods(user_id)["foods"] if f.get("deleted_at") is None),
}


# ---------------------------------------------------------------- the three accounts

def _ok(result):
    assert "error" not in result and "errors" not in result, result
    return result


def empty_account(user_id: int) -> None:
    """A new account: no entries, no time zone."""


def sample_account(user_id: int) -> None:
    _ok(services.update_settings(user_id, {"time_zone": "Asia/Kolkata"}, "web"))
    _ok(services.add_sample_data(user_id, "web"))


def edge_account(user_id: int) -> None:
    """A zero rate, over target, a day over 1,000 kcal, every measurement kind, every kind of goal (a
    cardio time among them), a closed goal, a cardio session over an hour and one with no estimate, a
    lift with no estimated max, a saved food never logged, and a zone name with an underscore."""
    _ok(services.update_settings(user_id, {"time_zone": "America/New_York", "calorie_target_kcal": 2000,
                                           "target_sessions_per_week": 0,
                                           "target_cardio_minutes_per_week": 150,
                                           "add_back_exercise_kcal": True}, "web"))
    with db.session_scope() as s:
        today = web.users.today_for(s.get(User, user_id))
    ago = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    # Cardio before any weigh-in: no calorie estimate.
    _ok(services.log_cardio(user_id, {"exercise": "Outdoor Walk", "minutes": 40, "date": ago(26)}, "claude"))
    for n in (21, 14, 10, 7, 3, 0):  # the same weight throughout: a rate of zero
        _ok(services.log_bodyweight(user_id, {"kg": 80, "date": ago(n)}, "claude"))
    for n, kg in ((12, 95), (5, 100), (0, 102.5)):
        _ok(services.log_lifts(user_id, {"date": ago(n), "notes": "Felt good",
                                         "sets": [{"exercise": "Back Squat", "weight_kg": kg, "reps": 5}] * 3},
                               "claude"))
    _ok(services.log_lifts(user_id, {"date": ago(2), "sets": [{"exercise": "Bench Press", "weight_kg": 40,
                                                              "reps": 15}]}, "claude"))
    for n, minutes in ((20, 31.5), (9, 30.25), (1, 29.4)):
        _ok(services.log_cardio(user_id, {"exercise": "Outdoor Run", "minutes": minutes, "distance_km": 5,
                                          "date": ago(n)}, "claude"))
    _ok(services.log_cardio(user_id, {"exercise": "Treadmill Run", "minutes": 75, "distance_km": 12.5,
                                      "machine_kcal": 1100, "notes": "Long one"}, "claude"))
    kinds = {"waist": 84.5, "hips": 98, "chest": 101.2, "arm": 35, "thigh": 56.75, "steps": 12345,
             "sleep_hours": 7.25, "resting_hr": 58, "water_ml": 2500}
    assert set(kinds) == set(web.MEASUREMENT_KINDS)
    for kind, value in kinds.items():
        _ok(services.log_measurement(user_id, {"kind": kind, "value": value}, "claude"))
        _ok(services.log_measurement(user_id, {"kind": kind, "value": value, "date": ago(15)}, "claude"))
    paneer = _ok(services.save_food(user_id, {"name": "Paneer tikka", "serving_label": "1 plate",
                                              "kcal_per_serving": 450, "grams_per_serving": 1250}, "web"))
    _ok(services.save_food(user_id, {"name": "Black coffee", "serving_label": "1 cup", "kcal_per_serving": 0},
                           "web"))
    _ok(services.log_food(user_id, {"entries": [
        {"description": "Biryani", "kcal": 1250, "meal": "lunch"},
        {"saved_food_id": paneer["id"], "servings": 1.5, "meal": "dinner"},
        {"description": "Lassi", "kcal": 300, "estimated": True, "meal": "snack"}]}, "claude"))
    goals = (
        {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_value": 27,
         "target_date": ago(-60)},
        {"kind": "bodyweight", "target_value": 75, "target_date": ago(-90)},
        {"kind": "lift", "exercise": "Back Squat", "target_value": 120, "target_date": ago(-70)},
        {"kind": "measurement", "measurement_kind": "waist", "target_value": 80, "target_date": ago(-80)},
        {"kind": "measurement", "measurement_kind": "resting_hr", "target_value": 52, "target_date": ago(-80)},
    )
    for goal in goals:
        _ok(services.set_goal(user_id, goal, "web"))
    closed = _ok(services.set_goal(user_id, {"kind": "measurement", "measurement_kind": "arm", "target_value": 38,
                                             "target_date": ago(-30)}, "web"))
    _ok(services.set_goal(user_id, {"goal_id": closed["id"], "status": "hit"}, "web"))


def far_days(user_id: int) -> None:
    """A day after today that holds an entry, and a day in another year. Logged on Pacific/Kiritimati
    (UTC+14), then the zone moves to Pacific/Pago_Pago (UTC-11): Kiritimati's today is always at least a day
    after Pago Pago's. The day page of a later day is a past day, titled by its date."""
    _ok(services.update_settings(user_id, {"time_zone": "Pacific/Kiritimati"}, "web"))
    _ok(services.log_measurement(user_id, {"kind": "steps", "value": 4321}, "claude"))
    _ok(services.update_settings(user_id, {"time_zone": "Pacific/Pago_Pago"}, "web"))
    with db.session_scope() as s:
        today = web.users.today_for(s.get(User, user_id))
    _ok(services.log_measurement(user_id, {"kind": "steps", "value": 1234,
                                           "date": (today - timedelta(days=366)).isoformat()}, "claude"))


def edge_and_far_days(user_id: int) -> None:
    """The edge account, then far_days: its zone ends on Pacific/Pago_Pago, a zone name with an underscore."""
    edge_account(user_id)
    far_days(user_id)


# The sweeps' accounts. tests/test_consistency.py takes edge_account alone.
ACCOUNTS = {"empty": empty_account, "sample": sample_account, "edge": edge_and_far_days}


# ---------------------------------------------------------------- the addresses

def _ids(model, user_id: int) -> list[int]:
    with db.session_scope() as s:
        return list(s.scalars(select(model.id).where(model.user_id == user_id, model.deleted_at.is_(None))
                              .order_by(model.id)))


ENTRY_MODELS = (("bodyweight", Bodyweight), ("measurement", Measurement), ("lift_session", LiftSession),
                ("food_entry", FoodEntry), ("cardio_entry", CardioEntry))


def entry_dates(user_id: int) -> list[str]:
    """Every day that holds a live entry."""
    with db.session_scope() as s:
        return sorted({d.isoformat() for _, model in ENTRY_MODELS
                       for d in s.scalars(select(model.date).where(model.user_id == user_id,
                                                                    model.deleted_at.is_(None)))})


def get_paths(user_id: int) -> dict[str, list[str]]:
    """Every GET path in web.PAGES, with the addresses that fill in its parameters."""
    today = today_of(only_user())
    dates = sorted({today.isoformat(), (today - timedelta(days=1)).isoformat(),
                    (today - timedelta(days=40)).isoformat(), *entry_dates(user_id)})
    entries = []
    for kind, model in ENTRY_MODELS:
        entries += [f"/entries/{kind}/{i}/edit" for i in _ids(model, user_id)]
    lifts = {row["exercise"] for row in services.get_progress(user_id)["lifts"]} | {"Back Squat"}
    foods = _ids(SavedFood, user_id)
    # The undo line for every kind, for one of its entries (or any id: the line names the kind, not the entry).
    undo = [f"/day/{today.isoformat()}?deleted={kind}:{next(iter(_ids(model, user_id)), 1)}"
            for kind, model in ENTRY_MODELS]
    goal_marks = [f"/goals?saved={s}" for s in web.GOAL_SAVED] + \
        ["/goals?saved=goal&notes=" + ",".join(code for code, _, _ in web.GOAL_NOTES)]
    return {
        "/settings": ["/settings", "/settings?saved=time_zone", "/settings?saved=claude_can",
                      "/settings?saved=targets"],
        "/today": ["/today"],
        "/day/{date}": [f"/day/{d}" for d in dates] + undo,
        "/foods": ["/foods", "/foods?saved=1"] + [f"/foods?deleted={i}" for i in foods[:1]],
        "/foods/{food_id}/edit": [f"/foods/{i}/edit" for i in foods],
        "/entries/{kind}/{entry_id}/edit": entries,
        "/progress": ["/progress"],
        "/progress/{lift}": [f"/progress/{web.links.slug(name)}" for name in sorted(lifts)],
        "/goals": ["/goals"] + goal_marks,
        "/settings/delete": ["/settings/delete"],
    }


def test_every_get_page_has_fixture_addresses(client):
    """A GET page added to web.PAGES fails here until this file renders it."""
    sign_in(client)
    listed = {path for path, _, methods in web.PAGES if "GET" in methods}
    assert set(get_paths(only_user().id)) == listed


# ---------------------------------------------------------------- reading a page

def entered_text(user_id: int) -> list[str]:
    """Everything on a page that the person (or Claude for them) typed, or that names them."""
    with db.session_scope() as s:
        user = s.get(User, user_id)
        out = [user.name, user.email, user.subject, user.time_zone]
        out += list(s.scalars(select(FoodEntry.description).where(FoodEntry.user_id == user_id)))
        out += list(s.scalars(select(LiftSession.notes).where(LiftSession.user_id == user_id)))
        out += list(s.scalars(select(CardioEntry.notes).where(CardioEntry.user_id == user_id)))
        out += list(s.scalars(select(SavedFood.name).where(SavedFood.user_id == user_id)))
        out += list(s.scalars(select(SavedFood.serving_label).where(SavedFood.user_id == user_id)))
        out += list(s.scalars(select(Goal.text).where(Goal.user_id == user_id)))
    return [v for v in out if v]


_OK = re.compile(r'<(?:p|div) class="ok(?: [^"]*)?">\s*([^<]*)')


def confirmations(markup: str) -> list[str]:
    """The text of each confirmation line (class "ok"), up to its first tag."""
    return [tr.squash(m.group(1)) for m in _OK.finditer(markup)]


def skipped(path: str, rule: str, match: str, tag: str = "") -> bool:
    bare = path.split("?")[0]
    return any(rule == s.rule and match == s.match and (not s.tag or s.tag == tag)
               and (bare == s.path if s.path == "/" else bare.startswith(s.path)) for s in SKIPS)


def page_findings(path: str, markup: str, entered: list[str], user_id: int | None) -> list[str]:
    """Every finding on one rendered page, after the skips and the true zeros."""
    out = []
    zeros: dict[tuple, int] = {}
    for piece in tr.html_text(markup):
        text = tr.mask_entered(piece.text, entered)
        surface = tr.TITLE if piece.kind == "title" else tr.PAGE
        for f in tr.check(text, surface, rules=tr.RULES[surface] + NUMBER_RULES):
            if skipped(path, f.rule, f.match, piece.tag):
                continue
            if f.rule == tr.EMPTY_FIGURE and f.match == "0":
                key = (path.split("?")[0], piece.tag)
                if key in TRUE_ZEROS:
                    zeros[key] = zeros.get(key, 0) + 1
                    continue
            out.append(f"{path} line {piece.line} <{piece.tag}>: {f}")
    for key, seen in zeros.items():
        held = TRUE_ZEROS[key](user_id) if user_id is not None else 0
        if seen > held:
            out.append(f"{path}: {seen} bare 0 in <{key[1]}>, but the data holds {held} true zeros")
    for line in confirmations(markup):
        if len(re.findall(r"[A-Za-z]+", line)) < 2 and not skipped(path, CONFIRMATION, line):
            out.append(f"{path}: the confirmation {line!r} does not name its subject")
    return out


# ---------------------------------------------------------------- step 4: which pages are rebuilt

# The routes whose page a step 4 pass has rebuilt: "/" or a GET path template from web.PAGES, such as
# "/day/{date}". The structure checks below and the page rules in tests/test_page_rules.py run on these
# routes only. A pass adds its own route here when its page lands.
BUILT: frozenset[str] = frozenset()

ROUTES = ("/", *(path for path, _, methods in web.PAGES if "GET" in methods))


def route_of(address: str) -> str:
    """The route an address renders: "/day/2026-11-09?saved=x" gives "/day/{date}". An address no GET
    route matches comes back bare."""
    bare = address.split("?")[0].split("#")[0]
    for route in ROUTES:
        if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", route), bare):
            return route
    return bare


# ---------------------------------------------------------------- a small tree of the page

@dataclass(eq=False)
class Node:
    """One element of a rendered page: its tag, attributes, parent and children (elements and text)."""
    tag: str
    attrs: dict
    parent: Node | None = None
    children: list = field(default_factory=list)

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def is_(self, tag: str = "", cls: str = "") -> bool:
        return (not tag or self.tag == tag) and (not cls or cls in self.classes)

    def iter(self):
        """Every element under this one, in document order."""
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.iter()

    def find_all(self, tag: str = "", cls: str = "") -> list[Node]:
        return [n for n in self.iter() if n.is_(tag, cls)]

    def elements(self) -> list[Node]:
        return [c for c in self.children if isinstance(c, Node)]

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def inside(self, tag: str = "", cls: str = "") -> bool:
        return any(a.is_(tag, cls) for a in self.ancestors())

    def text(self) -> str:
        parts = []

        def walk(node):
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                else:
                    walk(child)
        walk(self)
        return tr.squash("".join(parts))


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self.open = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.open)
        self.open.children.append(node)
        if tag not in tr.VOID:
            self.open = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in tr.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.open
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:  # close it and every element left open inside it
            self.open = node.parent

    def handle_data(self, data):
        self.open.children.append(data)


def parse(markup: str) -> Node:
    tree = _Tree()
    tree.feed(markup)
    tree.close()
    return tree.root


def head_of(dom: Node) -> Node | None:
    return next(iter(dom.find_all("head")), None)


# ---------------------------------------------------------------- furniture: the title and the favicon

# Section L, Favicon and titles: the three links every page head carries, attribute for attribute.
FAVICON_LINKS = ({"rel": "icon", "href": "/static/favicon.svg", "type": "image/svg+xml"},
                 {"rel": "icon", "href": "/static/favicon.ico", "sizes": "32x32"},
                 {"rel": "apple-touch-icon", "href": "/static/apple-touch-icon.png"})
SEPARATOR = " · Spotter"
TITLE_SHAPE = re.compile(r"\S(?:.*\S)?" + re.escape(SEPARATOR))
LANDING = "A fitness log you keep by talking to your assistant"
# Section L's titles table, for the pages named by a fixed word.
NAMED = {"/": LANDING, "/progress": "Progress", "/goals": "Goals", "/foods": "Foods", "/settings": "Settings",
         "/settings/delete": "Delete my account"}
EDIT_KINDS = {"bodyweight": "weigh-in", "measurement": "measurement", "lift_session": "lift session",
              "food_entry": "food entry", "cardio_entry": "cardio session"}


@dataclass
class Names:
    """What one account's titles are made of: its date, its lifts by slug, its saved foods by id."""
    today: date
    lifts: dict[str, str]
    foods: dict[str, str]


def names_of(user_id: int) -> Names:
    today = today_of(only_user())
    lifts = {row["exercise"] for row in services.get_progress(user_id)["lifts"]} | {"Back Squat"}
    foods = services.list_saved_foods(user_id)["foods"]
    return Names(today, {web.links.slug(n): n for n in lifts}, {str(f["id"]): f["name"] for f in foods})


def expected_title(address: str, dom: Node, names: Names | None, message: bool = False,
                   route: str | None = None) -> str:
    """Section L's title for the page at this address. Only a page drawn by the message template (a 404, a
    500, every message) takes its h1. A form sent back with its error keeps its page's title: `route` names
    that page when the address is the one the form was posted to."""
    route, parts = route or route_of(address), address.split("?")[0].split("/")
    if message:
        h1 = dom.find_all("h1")
        name = h1[0].text() if h1 else "a page with no h1"
    elif route in NAMED:
        name = NAMED[route]
    elif route == "/day/{date}":
        on = date.fromisoformat(parts[2])
        name = "Today" if on == names.today else formatting.day(on, names.today)
    elif route == "/progress/{lift}":
        name = names.lifts[parts[2]]
    elif route == "/entries/{kind}/{entry_id}/edit":
        name = "Edit " + EDIT_KINDS[parts[2]]
    elif route == "/foods/{food_id}/edit":
        name = "Edit " + names.foods[parts[2]]
    else:
        raise AssertionError(f"no title is known for {route}: add it to expected_title")
    return name + SEPARATOR


def furniture_findings(address: str, markup: str, names: Names | None, dom: Node | None = None,
                       message: bool = False, route: str | None = None) -> list[str]:
    """The head's title is the page's own, "{Page} · Spotter", and the head carries the three favicon links."""
    dom = dom or parse(markup)
    head = head_of(dom)
    if head is None:
        return [f"{address}: no head"]
    out = []
    titles = [n for n in head.elements() if n.tag == "title"]
    if len(titles) != 1:
        out.append(f"{address}: {len(titles)} titles in the head")
    else:
        title, want = titles[0].text(), expected_title(address, dom, names, message, route)
        if title != want or not TITLE_SHAPE.fullmatch(title) or tr.check_iso_dates(title):
            out.append(f"{address}: the title is {title!r}, not {want!r}")
    links = [n.attrs for n in head.elements() if n.tag == "link"]
    for link in FAVICON_LINKS:
        if link not in links:
            out.append(f"{address}: the head lacks the favicon link {link}")
    return out


# ---------------------------------------------------------------- "your assistant", never Claude

CLAUDE = re.compile(r"\bclaude\b", re.IGNORECASE)
_ASSISTANTS = re.compile(r'<ul\b[^>]*\bclass="(?:[^"]*\s)?assistants(?:\s[^"]*)?"[^>]*>')
_UL = re.compile(r"<ul\b|</ul\s*>")

# The sentences that still say Claude, by route, each until its page's own step 4 pass rebuilds it (a
# ruling of the lead's). A skip covers its exact sentences on its route and nothing else, so a new line that
# says Claude fails even on a skipped page. The pass deletes its sentences (and the route once it has none):
# test_no_claude_skip_outlives_its_page fails on a sentence no page says any more. A route in BUILT has none.
LIFTS_EMPTY = "Tell Claude what you lifted and it shows here."
CLAUDE_SKIPS: dict[str, tuple[str, ...]] = {
    "/": (  # home.html, signed in and out: rebuilt with its list of assistants
        "A personal fitness record you keep by talking to Claude.",
        "Tell Claude what you lifted, ate or weighed.",
        "Connect Claude and start",
        "Connect Claude.",
        "On claude.ai or the Claude desktop app: Customize, then Connectors, then \"+ Add\", then Add custom "
        "connector.",
        "In a new chat, check that the Spotter connector is switched on for that chat: on claude.ai, press "
        "\"+\" in the message box, then Connectors.",
        "The first time, Claude may ask you to allow each call.",
        "In Claude Code, run this once, then type /mcp and pick spotter to sign in.",
        "claude mcp add --transport http --scope user spotter https://spotter.example.app/mcp",
        "You decide what Claude may do.",
        "your targets, what Claude may do, export and delete.",
    ),
    "/settings": (  # settings.html: whoami, the switch's card and lines, the data card
        "Claude's whoami shows the same sign-in id and account id when it is connected to this account.",
        "What Claude may do",
        "Only you can change this, here: Claude is never offered this switch.",
        "Read only: Claude can read what you have logged and change nothing.",
        "Read and log: Claude can also log, edit and restore entries and set goals, but not delete.",
        "Read, log and delete: Claude can also delete entries.",
        "Claude does not do them for you.",
    ),
    "/day/{date}": (LIFTS_EMPTY,),  # day.html: the empty Lifts line
    "/foods": ("Saving one by talking to Claude works too.",),  # foods.html: the line under the h1
    "/entries/{kind}/{entry_id}/edit": ("Sets are changed through Claude for now: tell it what to fix.",),
    "/progress": (LIFTS_EMPTY,),  # progress.html: the empty Lifts line
    "/progress/{lift}": (LIFTS_EMPTY,),  # progress_lift.html: the empty line
    "/goals": ("Set one below, or tell Claude what you are aiming for.",),  # goals.html: no open goals
}
_SENTENCE = re.compile(r"(?<=[.?!])\s+")


def without_assistants(markup: str) -> str:
    """The page with the landing's list of assistants (ul.assistants, nested lists included) cut out."""
    start = _ASSISTANTS.search(markup)
    if start is None:
        return markup
    depth = 0
    for tag in _UL.finditer(markup, start.start()):
        depth += -1 if tag.group().startswith("</") else 1
        if depth == 0:
            return markup[:start.start()] + without_assistants(markup[tag.end():])
    return markup[:start.start()]


def claude_sentences(address: str, markup: str, route: str | None = None) -> list[tuple[str, str]]:
    """Every sentence of visible text that says Claude, with its element: page text, the title and read
    attributes. Only the landing's list of assistants may."""
    shown = without_assistants(markup) if (route or route_of(address)) == "/" else markup
    return [(p.tag, sentence) for p in tr.html_text(shown) for sentence in _SENTENCE.split(p.text)
            if CLAUDE.search(sentence)]


def claude_hits(address: str, markup: str, route: str | None = None) -> list[str]:
    return [f"{address} <{tag}>: says Claude: {sentence!r}"
            for tag, sentence in claude_sentences(address, markup, route)]


def claude_findings(address: str, markup: str, seen: set[tuple[str, str]] | None = None,
                    route: str | None = None) -> list[str]:
    """claude_hits, less the sentences CLAUDE_SKIPS lists for this route. `seen` collects (route, sentence)
    for each skipped sentence the page still says."""
    route = route or route_of(address)
    out = []
    for tag, sentence in claude_sentences(address, markup, route):
        if sentence in CLAUDE_SKIPS.get(route, ()):
            if seen is not None:
                seen.add((route, sentence))
            continue
        out.append(f"{address} <{tag}>: says Claude: {sentence!r}")
    return out


# ---------------------------------------------------------------- structure (the routes in BUILT)

@dataclass
class Table:
    node: Node
    headers: list[str]
    rows: list[list[Node]]

    def column(self, header: str) -> int | None:
        return self.headers.index(header) if header in self.headers else None

    def headed(self) -> dict[Node, str]:
        """Each body cell and its column's header."""
        return {cell: self.headers[n] if n < len(self.headers) else ""
                for row in self.rows for n, cell in enumerate(row)}


def tables(dom: Node) -> list[Table]:
    """Each table: its column headers (the cells of its first all-th row) and its body rows. A colspan
    is not read: each cell is one column."""
    out = []
    for table in dom.find_all("table"):
        trs = [r for r in table.find_all("tr") if next(a for a in r.ancestors() if a.tag == "table") is table]
        heads = [r for r in trs if r.elements() and all(c.tag == "th" for c in r.elements())]
        headers = [c.text() for c in heads[0].elements()] if heads else []
        rows = [[c for c in r.elements() if c.tag in ("td", "th")] for r in trs if r not in heads]
        out.append(Table(table, headers, rows))
    return out


def _blank(cell: Node) -> bool:
    return not cell.text() and not cell.elements()


def blank_column_findings(dom: Node) -> list[str]:
    """A column blank in every row is not shown (section C, an empty figure)."""
    out = []
    for t in tables(dom):
        if not t.rows:
            continue
        for n, header in enumerate(t.headers):
            if all(n >= len(row) or _blank(row[n]) for row in t.rows):
                out.append(f"the column {header!r} is blank in every row")
    return out


def remark_findings(dom: Node) -> list[str]:
    """D20: the Trends card is a table with a Remark column, each of its cells a remark cell."""
    out = []
    for card in dom.find_all(cls="trends"):
        held = [t for t in tables(dom) if t.node is card or card in t.node.ancestors()]
        if not held:
            out.append("the Trends card holds no table")
        for t in held:
            n = t.column("Remark")
            if n is None:
                out.append(f"the Trends table has no Remark column: {t.headers}")
                continue
            for row in t.rows:
                if n >= len(row) or "remark" not in row[n].classes:
                    out.append(f"a Trends row's Remark cell is not a remark cell: {[c.text() for c in row]}")
    return out


_BRACKETED = re.compile(r"\(([^()]+)\)")
_BARE_FRACTION = re.compile(r"-?[\d,]*\d[.:]\d+")


def units_findings(dom: Node) -> list[str]:
    """Units in headers: a column whose header names its unit in brackets never repeats it in a cell, and a
    value cell holding a bare figure that is not a whole count has a unit in its header."""
    out = []
    for t in tables(dom):
        for row in t.rows:
            for n, cell in enumerate(row):
                header = t.headers[n] if n < len(t.headers) else ""
                unit = _BRACKETED.search(header)
                text = cell.text()
                if unit and re.search(r"\d\s*" + re.escape(unit.group(1)) + r"(?![\w/])", text):
                    out.append(f"the cell {text!r} repeats its header's unit {header!r}")
                if "num" in cell.classes and _BARE_FRACTION.fullmatch(text) and not unit:
                    out.append(f"the figure {text!r} has no unit in its cell or its header {header!r}")
    return out


ESTIMATE_TAGS = ("tag-estimate", "tag-claude_estimate")
VALUE_SLOTS = ("num", "tile-value", "big", "giant")


def estimate_findings(dom: Node) -> list[str]:
    """An estimate is a tag in its own slot: the word "estimate", never inside a value, never in brackets,
    never "of which"."""
    out = []
    for tag in (n for n in dom.iter() if set(n.classes) & set(ESTIMATE_TAGS)):
        if tag.text() != "estimate":
            out.append(f"an estimate tag reads {tag.text()!r}")
        if any(set(a.classes) & set(VALUE_SLOTS) for a in tag.ancestors()):
            out.append(f"an estimate tag sits inside a value: {tag.parent.text()!r}")
        siblings = tag.parent.children
        before = siblings[siblings.index(tag) - 1] if siblings.index(tag) else ""
        if isinstance(before, str) and before.rstrip().endswith("("):
            out.append(f"an estimate tag in brackets: {tag.parent.text()!r}")
    if re.search(r"\bof which\b", dom.text(), re.IGNORECASE):
        out.append('the page says "of which"')
    return out


FORM_TEXT = ("textarea", "option", "select", "script", "style")


def own_text(node: Node) -> str:
    """A block's own words: its text and its inline elements' text, not the blocks inside it."""
    parts = []

    def walk(n):
        for child in n.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag in tr.INLINE:
                walk(child)
    walk(node)
    return tr.squash("".join(parts))


def notes_findings(dom: Node, notes: list[str]) -> list[str]:
    """A note sits in its own column or line: a cell under a Notes header, a dd after a Notes dt, or a line
    that holds only the label Notes and the note. A form field holding a note to edit is not a line."""
    out = []
    headed = {cell: header for t in tables(dom) for cell, header in t.headed().items()}
    blocks = [n for n in dom.iter() if n.tag not in tr.INLINE and n.tag not in FORM_TEXT
              and not any(a.tag in FORM_TEXT for a in n.ancestors())]
    for note in {tr.squash(n) for n in notes if n and n.strip()}:
        for node in (b for b in blocks if note in own_text(b)):
            if headed.get(node) == "Notes":
                continue
            if node.tag == "dd":
                terms = [e for e in node.parent.elements() if e.tag in ("dt", "dd")]
                dt = next((e for e in reversed(terms[:terms.index(node)]) if e.tag == "dt"), None)
                if dt is not None and dt.text() == "Notes":
                    continue
            if own_text(node).replace(note, "", 1).strip(" :") == "Notes":
                continue
            out.append(f"the note {note!r} is not in a Notes column or line: <{node.tag}> {own_text(node)[:60]!r}")
    return out


def structure_findings(address: str, markup: str, notes: list[str], dom: Node | None = None) -> list[str]:
    dom = dom or parse(markup)
    found = (blank_column_findings(dom) + remark_findings(dom) + units_findings(dom) + estimate_findings(dom)
             + notes_findings(dom, notes))
    return [f"{address}: {f}" for f in found]


def entered_notes(user_id: int) -> list[str]:
    """The notes a person gave their lift sessions and cardio sessions."""
    with db.session_scope() as s:
        out = list(s.scalars(select(LiftSession.notes).where(LiftSession.user_id == user_id)))
        out += list(s.scalars(select(CardioEntry.notes).where(CardioEntry.user_id == user_id)))
    return [n for n in out if n]


def step_4_findings(address: str, markup: str, names: Names | None, notes: list[str],
                    seen: set[tuple[str, str]] | None = None, message: bool = False,
                    route: str | None = None) -> list[str]:
    """T3's step 4 checks on one page: furniture and Claude on every page, structure on the routes in BUILT.
    `message`: the page is the message template. `route`: the page a form was sent back to, when the
    address is the one it was posted to."""
    dom = parse(markup)
    out = (furniture_findings(address, markup, names, dom, message, route)
           + claude_findings(address, markup, seen, route))
    if not message and (route or route_of(address)) in BUILT:
        out += structure_findings(address, markup, notes, dom)
    return out


# ---------------------------------------------------------------- the test

# The skipped sentences each route still says, by account, gathered as the sweep runs.
CLAUDE_SEEN: dict[str, set[tuple[str, str]]] = {}


@pytest.mark.parametrize("account", sorted(ACCOUNTS))
def test_every_page_passes_the_page_rules(client, account):
    seen: set[tuple[str, str]] = set()
    out = client.get("/")
    assert out.status_code == 200
    findings = page_findings("/", out.text, [], None) + step_4_findings("/", out.text, None, [], seen)
    sign_in(client)
    user_id = only_user().id
    ACCOUNTS[account](user_id)
    entered = entered_text(user_id)
    names, notes = names_of(user_id), entered_notes(user_id)
    rendered = 0
    for template, addresses in get_paths(user_id).items():
        for path in ["/"] + addresses if template == "/settings" else addresses:
            r = client.get(path)
            if template == "/today":
                assert r.status_code == 303, path  # only redirects: no text of its own
                continue
            assert r.status_code == 200, (path, r.status_code)
            rendered += 1
            findings += page_findings(path, r.text, entered, user_id)
            findings += step_4_findings(path, r.text, names, notes, seen)
    assert rendered >= 12
    if account == "edge":  # far_days: a day after today, and a day in another year
        days = [date.fromisoformat(a.split("/")[2]) for a in get_paths(user_id)["/day/{date}"] if "?" not in a]
        assert any(d > names.today for d in days) and any(d.year != names.today.year for d in days), days
    assert not findings, "\n".join(findings)
    CLAUDE_SEEN[account] = seen


def test_no_claude_skip_outlives_its_page():
    """Each sentence in CLAUDE_SKIPS is still said on its route, on at least one account: the pass that
    rebuilds a page deletes its sentences. Runs after the sweep above, on what it gathered: run the whole
    file, since alone it skips."""
    if set(CLAUDE_SEEN) != set(ACCOUNTS):
        pytest.skip("needs the sweep on all three accounts in the same run")
    still = set().union(*CLAUDE_SEEN.values())
    listed = {(route, s) for route, sentences in CLAUDE_SKIPS.items() for s in sentences}
    assert not listed - still, f"no page says these now: {sorted(listed - still)}"
    assert all(CLAUDE_SKIPS.values()), "a route with no sentence left: delete its line"


def test_no_rebuilt_route_keeps_a_claude_skip():
    assert not BUILT & set(CLAUDE_SKIPS), BUILT & set(CLAUDE_SKIPS)


# ---------------------------------------------------------------- the checks themselves

def test_a_bare_zero_passes_only_as_a_true_count(client):
    sign_in(client)
    user_id = only_user().id
    _ok(services.save_food(user_id, {"name": "Black coffee", "serving_label": "1 cup", "kcal_per_serving": 5},
                           "web"))
    page = client.get("/foods").text
    assert "<td>0</td>" in page  # never logged: a true count
    assert page_findings("/foods", page, entered_text(user_id), user_id) == []
    # The same 0 where the data holds no zero is an invented figure.
    invented = page.replace("<td>5</td>", "<td>0</td>")
    assert any("true zeros" in f for f in page_findings("/foods", invented, entered_text(user_id), user_id))
    # And a bare 0 anywhere else is an empty figure.
    assert any("empty_figure" in f for f in page_findings("/progress", "<dl><dd>0</dd></dl>", [], user_id))


def test_a_zone_name_is_masked_and_a_bare_confirmation_fails(client):
    sign_in(client)
    user_id = only_user().id
    _ok(services.update_settings(user_id, {"time_zone": "America/New_York"}, "web"))
    page = client.get("/settings").text
    assert "America/New_York" in page
    assert page_findings("/settings", page, entered_text(user_id), user_id) == []
    assert any("New_York" in f for f in page_findings("/settings", page, [], user_id))
    bare = '<div class="ok undo">\n  Deleted.\n  <form></form></div>'
    assert any("does not name its subject" in f for f in page_findings("/day/x", bare, [], user_id))
    assert page_findings("/foods", '<p class="ok">Saved.</p>', [], user_id) == []


# ---------------------------------------------------------------- the step 4 checks themselves

HEAD = ('<!doctype html><html><head><title>{title}</title>'
        '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">'
        '<link rel="icon" href="/static/favicon.ico" sizes="32x32">'
        '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png"></head>'
        '<body><main class="page"><h1>Not found</h1>{body}</main></body></html>')
NAMES = Names(date(2026, 11, 11), {"back-squat": "Back Squat"}, {"7": "Paneer tikka"})


def test_route_of_names_the_route_an_address_renders():
    assert route_of("/day/2026-11-09?saved=x") == "/day/{date}"
    assert route_of("/progress/back-squat") == "/progress/{lift}" and route_of("/progress") == "/progress"
    assert route_of("/settings/delete") == "/settings/delete" and route_of("/foods/3/edit") == "/foods/{food_id}/edit"
    assert route_of("/") == "/" and route_of("/nowhere") == "/nowhere"


@pytest.mark.parametrize("address, title", [
    ("/", "A fitness log you keep by talking to your assistant · Spotter"),
    ("/day/2026-11-11", "Today · Spotter"), ("/day/2026-11-09", "Mon 9 Nov · Spotter"),
    ("/day/2025-11-09", "Sun 9 Nov 2025 · Spotter"), ("/progress/back-squat", "Back Squat · Spotter"),
    ("/entries/bodyweight/3/edit", "Edit weigh-in · Spotter"), ("/foods/7/edit", "Edit Paneer tikka · Spotter"),
    ("/settings/delete", "Delete my account · Spotter"), ("/day/abc", "Not found · Spotter"),
])
def test_the_title_is_section_ls(address, title):
    message = address == "/day/abc"  # a 404: the message template, titled by its h1
    assert furniture_findings(address, HEAD.format(title=title, body=""), NAMES, message=message) == []


def test_a_form_sent_back_with_its_error_keeps_its_pages_title():
    page = HEAD.format(title="Today · Spotter", body="").replace("<h1>Not found</h1>", "<h1>Today, Wednesday</h1>")
    assert furniture_findings("/day/2026-11-11/weigh-in", page, NAMES, route="/day/{date}") == []
    assert furniture_findings("/settings/targets", page.replace("Today ·", "Settings ·"), NAMES,
                              route="/settings") == []
    assert furniture_findings("/day/2026-11-11/weigh-in", page.replace("<title>Today", "<title>Today, Wednesday"),
                              NAMES, route="/day/{date}")
    assert furniture_findings("/day/2026-11-11", page.replace("<title>Today", "<title>Not saved"), NAMES)


@pytest.mark.parametrize("address, title", [
    ("/progress", "Progress - Spotter"), ("/progress", "Progress ·Spotter"), ("/progress", "Progress"),
    ("/progress", "Goals · Spotter"), ("/day/2026-11-11", "Wed 11 Nov · Spotter"),
    ("/day/2026-11-09", "2026-11-09 · Spotter"), ("/foods/7/edit", "Paneer tikka · Spotter"),
])
def test_a_wrong_title_is_a_finding(address, title):
    assert furniture_findings(address, HEAD.format(title=title, body=""), NAMES)


def test_a_missing_favicon_link_or_a_second_title_is_a_finding():
    good = HEAD.format(title="Progress · Spotter", body="")
    assert furniture_findings("/progress", good, NAMES) == []
    for link in ('<link rel="icon" href="/static/favicon.ico" sizes="32x32">',
                 '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">'):
        assert furniture_findings("/progress", good.replace(link, ""), NAMES)
    assert furniture_findings("/progress", good.replace("</head>", "<title>x</title></head>"), NAMES)
    # A chart's own title in the body is not the page's title.
    assert furniture_findings("/progress", good.replace("<h1>", "<svg><title>Weight</title></svg><h1>"), NAMES) == []


def test_claude_is_said_only_in_the_landings_list_of_assistants():
    listed = ('<ul class="assistants"><li><a href="#">Claude apps</a></li>'
              '<li>Claude Code<ul><li>Run this once</li></ul><code>claude mcp add</code></li></ul>')
    assert claude_hits("/", f"<p>Tell your assistant.</p>{listed}<p>Any other client.</p>") == []
    assert claude_hits("/", f"<p>Ask Claude.</p>{listed}")
    assert claude_hits("/", f"{listed}<p>Ask Claude.</p>")
    assert claude_hits("/settings", listed)  # the list is the landing's only
    assert claude_hits("/goals", '<a aria-label="Ask Claude" href="/">x</a>')
    assert claude_hits("/goals", "<head><title>Claude · Spotter</title></head>")
    assert claude_hits("/goals", '<span class="tag tag-claude_estimate">estimate</span>') == []
    # A skip covers its own sentences on its own route, and nothing else.
    route, sentence = next((r, s[0]) for r, s in CLAUDE_SKIPS.items() if s and r != "/")
    address = route.replace("{date}", "2026-11-11").replace("{lift}", "back-squat") + "?saved=goal"
    address = address.replace("{kind}/{entry_id}", "bodyweight/1")
    seen: set[tuple[str, str]] = set()
    assert claude_findings(address, f"<p>{sentence}</p>", seen) == [] and seen == {(route, sentence)}
    assert claude_findings(address, f"<p>{sentence} Ask Claude.</p>")  # a new sentence on a skipped page
    assert claude_findings("/privacy", f"<p>{sentence}</p>")  # the sentence on another route


def test_the_structure_checks_find_what_they_name():
    def found(body, notes=()):
        return structure_findings("/x", f"<main>{body}</main>", list(notes))

    table = ('<table><thead><tr><th>Date</th><th class="num">Time (min)</th><th>Notes</th></tr></thead>'
             '<tbody>{rows}</tbody></table>')
    good = table.format(rows='<tr><td>Mon</td><td class="num">31.5</td><td>Felt good</td></tr>'
                             '<tr><td>Tue</td><td class="num">30</td><td></td></tr>')
    assert found(good, ["Felt good"]) == []
    assert found(table.format(rows='<tr><td>Mon</td><td class="num">31.5</td><td></td></tr>'))  # blank Notes column
    assert found(table.format(rows='<tr><td>Mon</td><td class="num">31.5 min</td><td>x</td></tr>'))  # unit twice
    assert found('<table><tr><th>Lift</th><th class="num">Max</th></tr>'
                 '<tr><td>Squat</td><td class="num">102.5</td></tr></table>')  # a bare figure, no unit
    assert found('<table><tr><th>Lift</th><th class="num">Sessions</th></tr>'
                 '<tr><td>Squat</td><td class="num">3</td></tr></table>') == []  # a whole count needs none
    # Notes: a cell under Notes, a dd after a Notes dt, or a line of its own labelled Notes.
    assert found('<dl><dt>Notes</dt><dd>Felt good</dd></dl>', ["Felt good"]) == []
    assert found('<p class="notes"><span>Notes</span> Felt good</p>', ["Felt good"]) == []
    assert found('<textarea name="notes">Felt good</textarea>', ["Felt good"]) == []
    assert found('<p>Back Squat, 3 sets. Felt good</p>', ["Felt good"])
    assert found('<dl><dt>Date</dt><dd>Felt good</dd></dl>', ["Felt good"])
    # The Trends table and its Remark column.
    trends = ('<section class="card trends"><div class="table-wrap"><table><thead><tr><th>Quantity</th>'
              '<th class="num">Value</th><th>Remark</th></tr></thead><tbody>{rows}</tbody></table></div></section>')
    assert found(trends.format(rows='<tr><td>Weigh-ins</td><td class="num">28</td>'
                                    '<td class="remark">Last 28 days</td></tr>')) == []
    assert found(trends.format(rows='<tr><td>Weigh-ins</td><td class="num">28</td><td>Last 28 days</td></tr>'))
    assert found(trends.replace("<th>Remark</th>", "<th>Window</th>").format(
        rows='<tr><td>Weigh-ins</td><td class="num">28</td><td class="remark">Last 28 days</td></tr>'))
    assert found('<div class="card trends"><dl class="facts"><dt>Body weight</dt><dd>80 kg</dd></dl></div>')
    # Estimate tags.
    assert found('<p>2,510 kcal</p><span class="tag tag-estimate">estimate</span>') == []
    assert found('<p class="big">2,510 <span class="tag tag-estimate">estimate</span></p>')
    assert found('<p>2,510 kcal (<span class="tag tag-estimate">estimate</span>)</p>')
    assert found('<p><span class="tag tag-claude_estimate">about</span></p>')
    assert found('<p>2,000 kcal, of which 300 kcal estimated</p>')


# ---------------------------------------------------------------- step 4: the 404 and 500 pages, every message

@dataclass
class Rendered:
    """One error or message page as a person gets it, and the frame it is drawn in."""
    address: str
    response: object
    signed_in: bool
    web_ready: bool = True
    calm: bool = False  # a POST answered 400 is calm (section L, The calm page)
    user_id: int | None = None


# Section L's 404 and 500 pages, and one render of the message template for each status it lists:
# case -> (status, h1).
ERROR_PAGES = {
    "an unrouted path": (404, "Page not found"),
    "an unrouted post": (404, "Page not found"),
    "a routed 404": (404, "Page not found"),
    "a crash": (500, "Something went wrong"),
    "form expired": (403, "Form expired"),
    "too many changes": (429, "Too many changes"),
    "already back": (409, "Already back"),
    "sample already added": (409, "Already added"),
    "sample into a full account": (409, "Not added"),
    "not saved": (400, "Not saved"),
    "not changed": (400, "Not changed"),
    "not set up yet": (503, "Not set up yet"),
    "not set up yet, no database": (503, "Not set up yet"),
    "account deleted": (200, "Your account is deleted"),
    "sign-in failed": (400, "Sign-in failed"),
    "no new accounts today": (403, "No new accounts today"),
}


def render_error_page(case: str, web_client, seam, monkeypatch) -> Rendered:
    """Ask the app for one of ERROR_PAGES the way a person would reach it."""
    from .test_web import TOO_MANY, post

    def boom(*args, **kwargs):
        raise RuntimeError("zqwy boom")

    if case == "not set up yet":
        client = web_client(workos_client_id=None, workos_api_key=None)
        return Rendered("/sign-in", client.get("/sign-in"), False, web_ready=False)
    if case == "no new accounts today":
        return Rendered("/auth/callback", sign_in(web_client(signups_per_day=0)), False)
    if case == "sign-in failed":
        client = web_client()
        seam.fail = True
        return Rendered("/auth/callback", sign_in(client), False)
    client = web_client(raise_server_exceptions=False)
    if case == "an unrouted path":
        return Rendered("/zqwy-nowhere", client.get("/zqwy-nowhere"), False)
    if case == "an unrouted post":
        return Rendered("/zqwy-nowhere", client.post("/zqwy-nowhere", data={"x": "1"}), False)
    if case == "form expired":
        return Rendered("/sign-out", client.post("/sign-out"), False)
    sign_in(client)
    user_id = only_user().id
    _ok(services.update_settings(user_id, {"time_zone": "Asia/Kolkata"}, "web"))
    today = today_of(only_user())
    signed = lambda address, r, **kw: Rendered(address, r, True, user_id=user_id, **kw)  # noqa: E731
    if case == "a routed 404":
        return signed("/day/zqwy", client.get("/day/zqwy"))
    if case == "not set up yet, no database":
        from sqlalchemy.exc import OperationalError

        def down(*args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("unable to open"))

        monkeypatch.setattr(services, "get_progress", down)
        return signed("/progress", client.get("/progress"))
    if case == "a crash":  # the 500 page is drawn signed out
        monkeypatch.setattr(services, "get_progress", boom)
        return Rendered("/progress", client.get("/progress"), False, user_id=user_id)
    if case == "too many changes":
        monkeypatch.setattr(services, "log_bodyweight", lambda *args: dict(TOO_MANY))
        return signed(f"/day/{today}/weigh-in", post(client, f"/day/{today}/weigh-in", kg="80"))
    if case == "already back":
        weigh_in = _ok(services.log_bodyweight(user_id, {"kg": "80"}, "claude"))["id"]
        return signed("/entries/bodyweight/1/restore", post(client, f"/entries/bodyweight/{weigh_in}/restore"))
    if case in ("sample already added", "sample into a full account"):
        code = "already_sample" if case == "sample already added" else "not_empty"
        monkeypatch.setattr(services, "add_sample_data",
                            lambda *args: {"error": code, "message": "x", "fix": "y"})
        return signed("/settings/sample", post(client, "/settings/sample"))
    if case == "not saved":  # a weigh-in for a day after today, which has no page
        later = (today + timedelta(days=1)).isoformat()
        return signed(f"/day/{later}/weigh-in", post(client, f"/day/{later}/weigh-in", kg="80"), calm=True)
    if case == "not changed":
        goal = _ok(services.set_goal(user_id, {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                                               "target_date": (today + timedelta(days=90)).isoformat()}, "web"))
        r = post(client, f"/goals/{goal['id']}/status", status="zqwy")
        return signed("/goals/1/status", r, calm=True)
    if case == "account deleted":
        async def deleted(subject):
            return None

        monkeypatch.setattr(web.web_auth, "delete_identity", deleted)
        return Rendered("/settings/delete", post(client, "/settings/delete"), False, user_id=None)
    raise AssertionError(f"no way to reach {case}")


def error_page_findings(page: Rendered) -> list[str]:
    """T3 on an error or message page: the text rules, the furniture (its title is its h1) and Claude."""
    r = page.response
    entered = entered_text(page.user_id) if page.user_id is not None else []
    return (page_findings(page.address, r.text, entered, page.user_id)
            + furniture_findings(page.address, r.text, None, message=True)
            + claude_findings(page.address, r.text))


# Section L, "404 and 500 pages", as written there: the two designed pages and every message it names, by
# status and h1. The sample refusals (409) are named there without a title.
CONTRACT_STATUSES = {404, 500, 403, 429, 409, 400, 503, 200}
CONTRACT_PAGES = {(404, "Page not found"), (500, "Something went wrong"), (403, "Form expired"),
                  (429, "Too many changes"), (409, "Already back"), (400, "Not saved"), (400, "Not changed"),
                  (503, "Not set up yet"), (200, "Your account is deleted"), (400, "Sign-in failed"),
                  (403, "No new accounts today")}


def test_the_error_pages_cover_every_status_and_page_the_contract_lists():
    assert {status for status, _ in ERROR_PAGES.values()} == CONTRACT_STATUSES
    assert CONTRACT_PAGES <= set(ERROR_PAGES.values()), CONTRACT_PAGES - set(ERROR_PAGES.values())
    assert sum(1 for status, _ in ERROR_PAGES.values() if status == 409) >= 3  # Already back, two sample refusals


@pytest.mark.parametrize("case", sorted(ERROR_PAGES))
def test_each_error_and_message_page_passes_the_page_rules(case, web_client, seam, monkeypatch):
    page = render_error_page(case, web_client, seam, monkeypatch)
    status, h1 = ERROR_PAGES[case]
    assert page.response.status_code == status, (case, page.response.status_code, page.response.text[:300])
    assert [n.text() for n in parse(page.response.text).find_all("h1")] == [h1]
    findings = error_page_findings(page)
    assert not findings, "\n".join(findings)


def test_the_error_page_checks_find_a_wrong_title_and_claude():
    good = ('<!doctype html><html><head><title>Page not found · Spotter</title>'
            '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">'
            '<link rel="icon" href="/static/favicon.ico" sizes="32x32">'
            '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png"></head>'
            '<body><main class="page"><div class="card"><h1 class="title">Page not found</h1>'
            '<p>There is nothing here.</p></div></main></body></html>')

    class R:
        status_code, text = 404, good

    assert error_page_findings(Rendered("/zqwy", R(), False)) == []
    R.text = good.replace("<title>Page not found", "<title>Not found")
    assert error_page_findings(Rendered("/zqwy", R(), False))
    R.text = good.replace("nothing here.", "nothing here. Ask Claude.")
    assert error_page_findings(Rendered("/zqwy", R(), False))
    # A message answered 200 still takes its h1 for its title, not its route's.
    R.status_code, R.text = 200, good
    assert error_page_findings(Rendered("/settings/delete", R(), False)) == []
