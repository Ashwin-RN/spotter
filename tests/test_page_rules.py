"""T5, the markup part (stage 6, step 4): the rules every rendered page's markup keeps, on every GET page
in web.PAGES on the three accounts tests/test_pages_text.py uses, and / signed in and out.

Two tiers.
- The frame rules hold on every page now: no style= and no <style>, no data: URL, no outside resource (the
  SVG namespace attribute names none, so it is exempt), no inline event handler; the only script is
  time-zone.js, under its rule; the head carries the dark-only metas and one stylesheet link; the tabs and
  the header follow section L's Navigation table; main.page carries its page and width classes; the body
  is calm exactly when the page is marked. The icon and favicon files exist, are clean and answer 200.
- The page rules hold on the routes in test_pages_text.BUILT, which each step 4 pass extends when it
  rebuilds its page. A rule that already holds on every page today (EVERYWHERE) runs on every page.

Every expectation here is written from the contract, never read from web.py, so a wrong table in the app
turns a test red.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app import services, web
from app.models import Bodyweight, SavedFood

from .test_pages_text import (ACCOUNTS, BUILT, ENTRY_MODELS, ERROR_PAGES, FAVICON_LINKS, Node, _ids, entered_notes,
                              entered_text, estimate_findings, get_paths, names_of, page_findings, parse,
                              render_error_page, route_of, step_4_findings)
from .test_web import client, only_user, post, seam, sign_in, today_of, web_client  # noqa: F401 - fixtures

STATIC = Path(web.HERE) / "static"


# ---------------------------------------------------------------- what section L says, by route

# Navigation: the four tabs in order, (variant, address, label).
TABS = [("today", "/today", "Today"), ("progress", "/progress", "Progress"), ("goals", "/goals", "Goals"),
        ("settings", "/settings", "Settings")]
# Navigation's current-tab table and the Hooks table's page and width classes: route -> (tab, classes).
FRAME = {
    "/": (None, {"landing"}),
    "/day/{date}": ("today", {"today"}),
    "/entries/{kind}/{entry_id}/edit": ("today", {"form-page"}),
    "/progress": ("progress", {"progress"}),
    "/progress/{lift}": ("progress", {"lift-page"}),
    "/goals": ("goals", {"goals-page"}),
    "/foods": ("settings", {"foods-page"}),
    "/foods/{food_id}/edit": ("settings", {"form-page"}),
    "/settings": ("settings", {"settings-page"}),
    "/settings/delete": ("settings", {"notice-page"}),
}
MESSAGE = (None, {"notice-page"})  # 404, 500 and every message page
# The calm page: the address marks of a page after a write.
MARKS = ("saved", "deleted", "restored", "notes", "kind", "add")


@dataclass
class Seen:
    """One response and what the frame rules expect of it."""
    address: str
    markup: str
    signed_in: bool
    web_ready: bool = True
    zone_missing: bool = False
    message: bool = False       # a message or error page: no tab, notice-page
    calm: bool | None = None    # None: worked out from the address
    today: str = ""
    posted_to: str = ""         # a form sent back with its error: the route whose page it is
    dom: Node = field(init=False)

    def __post_init__(self):
        self.dom = parse(self.markup)

    @property
    def route(self) -> str:
        return self.posted_to or route_of(self.address)

    def expected(self) -> tuple[str | None, set[str]]:
        tab, classes = MESSAGE if self.message else FRAME[self.route]
        classes = {"page"} | classes
        if self.route == "/day/{date}" and not self.message and self.address.split("?")[0].split("/")[2] != self.today:
            classes.add("past")
        if not self.signed_in:
            classes.add("out")
        return (tab if self.signed_in else None), classes

    def expected_calm(self) -> bool:
        if self.calm is not None:
            return self.calm
        return bool(set(parse_qs(urlsplit(self.address).query, keep_blank_values=True)) & set(MARKS))


# ---------------------------------------------------------------- the frame rules

# Attributes that load something: each must name a Spotter address.
LOADS = {("script", "src"), ("link", "href"), ("img", "src"), ("img", "srcset"), ("iframe", "src"),
         ("frame", "src"), ("source", "src"), ("source", "srcset"), ("video", "src"), ("video", "poster"),
         ("audio", "src"), ("track", "src"), ("object", "data"), ("embed", "src"), ("input", "src"),
         ("image", "href"), ("use", "href"), ("form", "action"), ("button", "formaction")}
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.I)


def outside(value: str, srcset: bool = False) -> bool:
    """True when an address names another site (a scheme, or //host). A srcset is a comma-separated list of
    candidates, each an address and a size: every candidate is read."""
    candidates = [c.split()[0] for c in value.split(",") if c.strip()] if srcset else [value.strip()]
    return any(_SCHEME.match(c) or c.startswith("//") for c in candidates)


def resource_findings(s: Seen) -> list[str]:
    out = []
    if re.search(r"<style\b", s.markup, re.I):
        out.append("a <style> element")
    for node in s.dom.iter():
        for name, value in node.attrs.items():
            if name == "style":
                out.append(f"style= on <{node.tag}>")
            if name.startswith("on"):
                out.append(f"an inline handler {name}= on <{node.tag}>")
            if re.match(r"\s*data:", value, re.I) or re.search(r"url\(\s*['\"]?\s*data:", value, re.I):
                out.append(f"a data: URL in {name}= on <{node.tag}>")
            if ((node.tag, name) in LOADS or name.endswith(":href")) and outside(value, name == "srcset"):
                out.append(f"an outside resource {name}={value!r} on <{node.tag}>")
    return out


def script_findings(s: Seen) -> list[str]:
    """The one script is time-zone.js, empty, only on a page for a person with no time zone saved."""
    scripts = s.dom.find_all("script")
    want = 1 if s.signed_in and s.zone_missing else 0
    out = [] if len(scripts) == want else [f"{len(scripts)} scripts, not {want}"]
    for script in scripts:
        if script.attrs != {"src": "/static/time-zone.js"} or script.text():
            out.append(f"a script that is not time-zone.js: {script.attrs}")
    return out


# The head, element for element: the charset, viewport and referrer metas, the two dark-only metas (line
# 984), the title, the three favicon links (lines 1938 to 1940) and the one stylesheet link (section H), and
# nothing else. The landing's link-preview tags join it in the landing's own pass.
HEAD = sorted([("meta", (("charset", "utf-8"),)),
               ("meta", (("content", "width=device-width, initial-scale=1, viewport-fit=cover"), ("name", "viewport"))),
               ("meta", (("content", "no-referrer"), ("name", "referrer"))),
               ("meta", (("content", "dark"), ("name", "color-scheme"))),
               ("meta", (("content", "#000000"), ("name", "theme-color"))),
               ("title", ()),
               ("link", (("href", "STYLESHEET"), ("rel", "stylesheet"))),
               *(("link", tuple(sorted(link.items()))) for link in FAVICON_LINKS)])
_STYLESHEET = re.compile(r"/static/style\.css(\?v=[0-9a-f]{12})?")


def head_findings(s: Seen) -> list[str]:
    """The head holds exactly HEAD: dark only, the favicon set, and the one stylesheet linked once with its
    version query (section H)."""
    heads = s.dom.find_all("head")
    if len(heads) != 1:
        return [f"{len(heads)} heads"]
    got = []
    for n in heads[0].elements():
        attrs = dict(n.attrs)
        if n.tag == "link" and attrs.get("rel") == "stylesheet" and _STYLESHEET.fullmatch(attrs.get("href", "")):
            attrs["href"] = "STYLESHEET"
        got.append((n.tag, tuple(sorted(attrs.items()))))
    return [] if sorted(got) == HEAD else [f"the head holds {sorted(got)}, not {HEAD}"]


def _tab_shape(node: Node) -> tuple:
    return (node.tag, node.classes, node.attrs.get("href"), node.text())


def nav_findings(s: Seen) -> list[str]:
    """Section L, Navigation: signed in, header.top with the brand, the four tabs in order with
    aria-current on the one the table names, and the Sign out form. Signed out, header.top-out with the
    brand and Sign in when web sign-in is set up, and no tabs."""
    out = []
    want_tab, _ = s.expected()
    tops, outs = s.dom.find_all("header", "top"), s.dom.find_all("header", "top-out")
    tabs = [n for n in s.dom.iter() if "tab" in n.classes]
    if not s.signed_in:
        if tops or tabs or s.dom.find_all(cls="tabs") or s.dom.find_all("form", "sign-out"):
            out.append("a signed-out page carries the tab bar")
        if len(outs) != 1:
            return out + [f"{len(outs)} header.top-out"]
        got = [_element(n) for n in outs[0].elements()]
        want = [BRAND] + ([("a", {"class": "plain-link", "href": "/sign-in"}, "Sign in")] if s.web_ready else [])
        if got != want:
            out.append(f"the signed-out header holds {got}, not {want}")
        return out
    if outs or len(tops) != 1:
        return out + [f"{len(tops)} header.top and {len(outs)} header.top-out on a signed-in page"]
    top = tops[0]
    # The top bar, child for child: the brand, the tab bar (named, never hidden) and the Sign out form.
    children = [(n.tag, n.attrs) for n in top.elements()]
    if children[:1] != [BRAND[:2]] or (top.elements() and top.elements()[0].text() != BRAND[2]) or \
            children[1:] != [("nav", NAV), ("form", SIGN_OUT)]:
        out.append(f"the top bar holds {[_element(n) for n in top.elements()]}")
    navs = top.find_all("nav", "tabs")
    if len(navs) != 1:
        return out + [f"{len(navs)} nav.tabs in the top bar"]
    got = [_tab_shape(a) for a in navs[0].elements()]
    want = [("a", ["tab", f"tab-{name}"], href, label) for name, href, label in TABS]
    if got != want:
        out.append(f"the tabs are {got}")
    current = [a.attrs.get("href") for a in tabs if "aria-current" in a.attrs]
    if current != [href for name, href, _ in TABS if name == want_tab]:
        out.append(f"aria-current is on {current}, not on the {want_tab} tab")
    if any(a.attrs.get("aria-current") not in (None, "page") for a in tabs):
        out.append("a tab's aria-current is not page")
    forms = top.find_all("form", "sign-out")
    if len(forms) != 1 or forms[0].attrs.get("method") != "post" or forms[0].attrs.get("action") != "/sign-out":
        return out + ["the top bar has no Sign out form posting to /sign-out"]
    inside = forms[0].elements()
    if [(n.tag, n.attrs.get("type"), n.attrs.get("name")) for n in inside] != \
            [("input", "hidden", "csrf_token"), ("button", None, None)] or not inside[0].attrs.get("value"):
        out.append("the Sign out form is not its CSRF field and its button")
    if [b.text() for b in forms[0].find_all("button")] != ["Sign out"]:
        out.append("the Sign out form has no Sign out button")
    return out


BRAND = ("a", {"class": "brand", "href": "/"}, "Spotter")
NAV = {"class": "tabs", "aria-label": "Main"}
SIGN_OUT = {"class": "sign-out", "method": "post", "action": "/sign-out"}


def _element(n: Node) -> tuple:
    return (n.tag, n.attrs, n.text())


def main_findings(s: Seen) -> list[str]:
    mains = s.dom.find_all("main")
    _, want = s.expected()
    if len(mains) != 1:
        return [f"{len(mains)} main elements"]
    got = mains[0].classes
    return [] if got[:1] == ["page"] and set(got) == want and len(got) == len(want) else \
        [f"main carries {got}, not {sorted(want)}"]


def calm_findings(s: Seen) -> list[str]:
    bodies = s.dom.find_all("body")
    calm = [n.tag for n in s.dom.iter() if "calm" in n.classes]
    want = ["body"] if s.expected_calm() else []
    return [] if len(bodies) == 1 and calm == want else [f"calm on {calm}, not on {want}"]


FRAME_RULES = (resource_findings, script_findings, head_findings, nav_findings, main_findings, calm_findings)


def frame_findings(s: Seen) -> list[str]:
    return [f"{s.address}: {f}" for rule in FRAME_RULES for f in rule(s)]


# ---------------------------------------------------------------- the page rules

def svg_findings(s: Seen) -> list[str]:
    """Every svg has a role, and a title and a desc of its own."""
    out = []
    for svg in s.dom.find_all("svg"):
        own = [e.tag for e in svg.elements()]
        if "role" not in svg.attrs or "title" not in own or "desc" not in own:
            out.append(f"an svg without role, title and desc: {svg.attrs.get('id') or svg.attrs}")
    return out


def _by_id(s: Seen) -> dict[str, Node]:
    return {n.attrs["id"]: n for n in s.dom.iter() if n.attrs.get("id")}


def popover_findings(s: Seen) -> list[str]:
    """Every popovertarget names a popover on the page, and every popover names its title."""
    ids, out = _by_id(s), []
    for node in s.dom.iter():
        target = node.attrs.get("popovertarget")
        if target is not None and (target not in ids or "popover" not in ids[target].attrs):
            out.append(f"popovertarget={target!r} names no popover")
        if "popover" in node.attrs:
            labels = (node.attrs.get("aria-labelledby") or "").split()
            if not labels or any(label not in ids for label in labels):
                out.append(f"a popover's aria-labelledby {labels} names no element")
    return out


def labelledby_findings(s: Seen) -> list[str]:
    """Every aria-labelledby, on any element, names one or more ids, each on the page. A card named by an id
    that is gone is named by nothing."""
    ids, out = _by_id(s), []
    for node in s.dom.iter():
        if "aria-labelledby" not in node.attrs:
            continue
        labels = node.attrs["aria-labelledby"].split()
        missing = [label for label in labels if label not in ids]
        if not labels or missing:
            out.append(f"<{node.tag} class={node.attrs.get('class', '')!r}> aria-labelledby names no element: "
                       f"{missing or labels!r}")
    return out


def twin_findings(s: Seen) -> list[str]:
    """Every tile button with a sheet has a twin a.tile-link with the same words."""
    twins = {a.text() for a in s.dom.find_all("a", "tile-link")}
    return [f"the tile {b.text()!r} has no twin link" for b in s.dom.find_all("button", "tile")
            if "popovertarget" in b.attrs and b.text() not in twins]


ERROR_MARKS = ("has-error", "error")


def _has_error(node: Node) -> bool:
    return any(set(n.classes) & set(ERROR_MARKS) or n.attrs.get("aria-invalid") == "true" for n in node.iter())


def foldout_findings(s: Seen) -> list[str]:
    """Section L's fold-out rule: closed by default; open when its form has an error, when the address
    carries its mark (?add=food opens #add-food), or on Foods with no saved foods. Sent open after an error."""
    add = parse_qs(urlsplit(s.address).query, keep_blank_values=True).get("add", [])
    out = []
    for details in s.dom.find_all("details", "add"):
        error = _has_error(details)
        allowed = error or (details.attrs.get("id") in {f"add-{a}" for a in add}) or \
            (s.route == "/foods" and not s.dom.find_all("table"))
        if "open" in details.attrs and not allowed:
            out.append(f"the fold-out {details.attrs.get('id') or details.text()[:30]!r} is open")
        if error and "open" not in details.attrs:
            out.append(f"the fold-out {details.attrs.get('id') or details.text()[:30]!r} holds an error, closed")
    return out


def goal_li_findings(s: Seen) -> list[str]:
    return [f"an li inside li.goal {g.attrs.get('id')}" for g in s.dom.find_all("li", "goal") if g.find_all("li")]


def token_findings(s: Seen) -> list[str]:
    """Every form that posts holds its CSRF token: a hidden input named csrf_token with a value. Without it
    the server answers Form expired (403), and nothing the person pressed is saved."""
    out = []
    for form in s.dom.find_all("form"):
        if (form.attrs.get("method") or "").casefold() != "post":
            continue
        if not any(i.attrs.get("type") == "hidden" and i.attrs.get("name") == "csrf_token" and i.attrs.get("value")
                   for i in form.find_all("input")):
            out.append(f"the form posting to {form.attrs.get('action')!r} holds no csrf_token")
    return out


def table_wrap_findings(s: Seen) -> list[str]:
    return [f"a table outside div.table-wrap: {t.text()[:40]!r}" for t in s.dom.find_all("table")
            if not (t.parent.tag == "div" and "table-wrap" in t.parent.classes)]


# The inputs that take a number, by name, and any type=number: the day forms, the edit pages, Foods, the
# targets, the goal form. Written out here, not read from web.py.
NUMBER_NAMES = {"kg", "value", "kcal", "minutes", "distance_km", "incline_pct", "speed_kmh", "avg_hr", "machine_kcal",
                "kcal_per_serving", "grams_per_serving", "calorie_target_kcal", "target_sessions_per_week",
                "target_cardio_minutes_per_week", "target_value", "start_value", "servings", "grams"}


def inputmode_findings(s: Seen) -> list[str]:
    return [f"the number input {i.attrs.get('name')!r} has no inputmode" for i in s.dom.find_all("input")
            if (i.attrs.get("type") == "number" or i.attrs.get("name") in NUMBER_NAMES)
            and i.attrs.get("type") != "hidden" and not i.attrs.get("inputmode")]


CONTROLS = ("input", "select", "textarea")


def describedby_findings(s: Seen) -> list[str]:
    """Every field error (p.error) is tied to its input: an id the input's aria-describedby names, with
    aria-invalid="true". A form error not tied to one field is a div.error."""
    out = []
    controls = [n for n in s.dom.iter() if n.tag in CONTROLS]
    described = {d: c for c in controls for d in (c.attrs.get("aria-describedby") or "").split()}
    for error in s.dom.find_all("p", "error"):
        if error.inside("div", "error"):
            continue
        control = described.get(error.attrs.get("id", ""))
        if control is None or control.attrs.get("aria-invalid") != "true":
            out.append(f"the error {error.text()[:50]!r} is tied to no input")
    errors = {e.attrs.get("id") for e in s.dom.find_all("p", "error")}
    for control in controls:
        if control.attrs.get("aria-invalid") == "true" and not set(described) & errors & \
                set((control.attrs.get("aria-describedby") or "").split()):
            out.append(f"the input {control.attrs.get('name')!r} is invalid with no error named")
    return out


PAGE_RULES = (svg_findings, popover_findings, twin_findings, foldout_findings, goal_li_findings,
              labelledby_findings, token_findings, table_wrap_findings, inputmode_findings, describedby_findings)
# The page rules that already hold on every page today, so they run on every page. The other three (a table
# outside div.table-wrap, a number input with no inputmode, an error tied to no input) fail on today's
# pages, so they run on the routes in BUILT only.
EVERYWHERE = (svg_findings, popover_findings, twin_findings, foldout_findings, goal_li_findings,
              labelledby_findings, token_findings)


def page_rule_findings(s: Seen) -> list[str]:
    rules = PAGE_RULES if s.route in BUILT else EVERYWHERE
    return [f"{s.address}: {f}" for rule in rules for f in rule(s)]


# ---------------------------------------------------------------- the form errors each page sends back

def error_posts(today: str, user_id: int) -> dict[str, list[tuple[str, dict]]]:
    """By route: the POSTs that send the page back with a form error, status 400."""
    foods = [f["id"] for f in services.list_saved_foods(user_id)["foods"]]
    out = {
        "/day/{date}": [(f"/day/{today}/weigh-in", {"kg": "abc"}),
                        (f"/day/{today}/food", {"description": "Rice", "kcal": "abc"}),
                        (f"/day/{today}/food", {"description": "", "kcal": "100"}),
                        (f"/day/{today}/food", {"description": "Rice", "kcal": "100", "meal": "brunch"}),
                        (f"/day/{today}/cardio", {"exercise": "Outdoor Run", "minutes": "0"}),
                        (f"/day/{today}/cardio", {"exercise": "Back Squat", "minutes": "30"}),
                        (f"/day/{today}/measurement", {"kind": "waist", "value": "abc"}),
                        (f"/day/{today}/measurement", {"kind": "wrist", "value": "17"})],
        "/foods": [("/foods", {"name": "", "serving_label": "1 bowl", "kcal_per_serving": "abc"})],
        "/settings": [("/settings", {"time_zone": "Not/AZone"}),
                      ("/settings/targets", {"calorie_target_kcal": "abc"})],
        "/goals": [("/goals", {"kind": "bodyweight", "target_value": "abc", "target_date": "2099-01-01"})],
    }
    if foods:
        out["/foods/{food_id}/edit"] = [(f"/foods/{foods[0]}/edit",
                                         {"serving_label": "1 bowl", "kcal_per_serving": "abc"})]
    # Each kind's edit page sent back with its error, on the first entry of each kind the account holds: an
    # input's error, a typed 0, a select's, a textarea's and a date's.
    edits = {"bodyweight": [{"kg": "abc"}, {"kg": "0"}],
             "measurement": [{"value": "abc"}],
             "lift_session": [{"notes": "x" * 2001, "date": "2099-01-01"}],
             "food_entry": [{"description": "Rice", "kcal": "abc", "meal": "lunch"},
                            {"description": "Rice", "kcal": "100", "meal": "brunch"}],
             "cardio_entry": [{"exercise": "Outdoor Run", "minutes": "0"}, {"exercise": "Back Squat", "minutes": "30"},
                              {"exercise": "Outdoor Run", "minutes": "30", "notes": "x" * 2001}]}
    posts = []
    for kind, model in ENTRY_MODELS:
        first = next(iter(_ids(model, user_id)), None)
        if first is not None:
            posts += [(f"/entries/{kind}/{first}/edit", data) for data in edits[kind]]
    if posts:
        out["/entries/{kind}/{entry_id}/edit"] = posts
    return out


# ---------------------------------------------------------------- the sweep

def sweep(client, account: str) -> list[Seen]:
    """Every page the rules read, on one account: / signed out, every GET page signed in, two 404 pages,
    and each page sent back with its form error."""
    seen = [Seen("/", client.get("/").text, signed_in=False)]
    expired = client.post("/sign-out")  # no token: Form expired, signed out
    assert expired.status_code == 403
    seen.append(Seen("/sign-out", expired.text, False, message=True))
    sign_in(client)
    user = only_user()
    ACCOUNTS[account](user.id)
    zone_missing = not only_user().time_zone
    today = today_of(user).isoformat()
    for template, addresses in get_paths(user.id).items():
        for address in ["/"] + addresses if template == "/settings" else addresses:
            r = client.get(address)
            if template == "/today":
                assert r.status_code == 303, address
                continue
            assert r.status_code == 200, (address, r.status_code)
            seen.append(Seen(address, r.text, True, zone_missing=zone_missing, today=today))
    for address in ("/day/abc", "/progress/not-a-lift"):
        r = client.get(address)
        assert r.status_code == 404, address
        seen.append(Seen(address, r.text, True, zone_missing=zone_missing, message=True, today=today))
    expired = client.post(f"/day/{today}/weigh-in", data={"kg": "80"})  # no token: Form expired, signed in
    assert expired.status_code == 403
    seen.append(Seen(f"/day/{today}/weigh-in", expired.text, True, zone_missing=zone_missing, message=True,
                     today=today))
    for route, posts in error_posts(today, user.id).items():
        for address, data in posts:
            r = post(client, address, **data)
            assert r.status_code == 400, (address, r.status_code, r.text[:300])
            seen.append(Seen(address, r.text, True, zone_missing=not only_user().time_zone, calm=True,
                             today=today, posted_to=route))
    return seen


# The error renders whose page still prints a service's own words, held by route until that page's pass.
# Goals: goals.html prints set_goal's message and fix ("target_value 'abc' is not a number."). The contract
# gives the fix to the Goals pass: "The Goals page's own error words, in place of the service's message and
# fix, are step 4, in lane B."
TEXT_HELD = {"/goals": "goals.html prints the service's message and fix, until the Goals pass"}


@pytest.mark.parametrize("account", sorted(ACCOUNTS))
def test_every_page_keeps_the_frame_and_page_rules(client, account):
    seen = sweep(client, account)
    assert len(seen) >= 20
    findings = [f for s in seen for f in frame_findings(s) + page_rule_findings(s)]
    # T3's title and Claude checks on what only this sweep reaches: the 404s, the 403s and each form sent
    # back with its error.
    names, notes = names_of(only_user().id), entered_notes(only_user().id)
    for s in seen:
        if s.message or s.posted_to:
            findings += step_4_findings(s.address, s.markup, names, notes, message=s.message,
                                        route=s.posted_to or None)
    # T1's text rules on each form sent back with its error, which the text sweep's GETs never reach. One
    # route is held, by name, until its own pass (TEXT_HELD): the hold fails once the page passes.
    entered = entered_text(only_user().id)
    held = {route: [] for route in TEXT_HELD}
    for s in seen:
        if s.posted_to:
            found = page_findings(s.address, s.markup, entered, only_user().id, route=s.posted_to)
            if s.posted_to in TEXT_HELD:
                held[s.posted_to] += found
            else:
                findings += found
    findings += [f"{route}: its error render passes the text rules now, delete its TEXT_HELD line"
                 for route, found in held.items() if not found]
    assert not findings, "\n".join(findings)


def test_the_signed_out_header_without_web_sign_in(web_client):
    client = web_client(workos_client_id=None, workos_api_key=None)
    r = client.get("/")
    assert r.status_code == 200
    s = Seen("/", r.text, signed_in=False, web_ready=False)
    assert frame_findings(s) == []
    assert "/sign-in" not in r.text


# ---------------------------------------------------------------- the calm page

def test_calm_appears_exactly_when_the_page_is_marked(client):
    sign_in(client)
    user = only_user()
    services.update_settings(user.id, {"time_zone": "Asia/Kolkata"}, "web")
    today = today_of(user).isoformat()
    day = f"/day/{today}"

    def body_classes(r) -> list[str]:
        return next(iter(parse(r.text).find_all("body"))).classes

    for address in (day, "/settings", "/goals", "/foods", "/progress", day + "?saving=1", day + "?add_more=1",
                    day + "?Saved=1", "/goals?kinds=lift", day + "?next=/x?saved=1"):
        assert "calm" not in body_classes(client.get(address)), address
    for mark in MARKS:
        for address in (f"{day}?{mark}=x", f"{day}?{mark}=", f"/goals?{mark}=lift", f"/settings?x=1&{mark}=1"):
            assert body_classes(client.get(address)) == ["calm"], address
    # A form sent back with its error is calm; a POST that answers anything else is not.
    refused = post(client, f"{day}/weigh-in", kg="abc")
    assert refused.status_code == 400 and body_classes(refused) == ["calm"]
    missing = post(client, "/entries/bodyweight/99999/delete")
    assert missing.status_code == 404 and "calm" not in body_classes(missing)
    expired = client.post(f"{day}/weigh-in", data={"kg": "80"})
    assert expired.status_code == 403 and "calm" not in body_classes(expired)


def test_a_failed_sign_in_is_not_calm(client, seam):
    seam.fail = True
    r = client.get("/sign-in")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    failed = client.get("/auth/callback", params={"code": "x", "state": state})
    assert failed.status_code == 400 and "calm" not in next(iter(parse(failed.text).find_all("body"))).classes


# ---------------------------------------------------------------- the files the frame links

FAVICONS = {"favicon.svg": "image/svg+xml", "favicon.ico": "image/", "apple-touch-icon.png": "image/png"}
ICONS = sorted(p.name for p in (STATIC / "icons").iterdir())
SVG_NS = "http://www.w3.org/2000/svg"


def svg_clean(text: str) -> list[str]:
    """A clean SVG: well formed, no script, no style, no data:, no handler, nothing loaded from outside.
    The SVG namespace names no resource, so it is exempt."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [f"not well formed: {exc}"]
    out = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("script", "style", "foreignObject", "image", "use"):
            out.append(f"a <{tag}> element")
        for name, value in el.attrib.items():
            local = name.split("}")[-1]
            if local == "style" or local.startswith("on"):
                out.append(f"{local}= on <{tag}>")
            if "data:" in value or "url(" in value or (local == "href" and not value.startswith("#")):
                out.append(f"{local}={value!r} on <{tag}>")
    rest = text.replace(SVG_NS, "")
    if re.search(r"https?:|data:|<!ENTITY|<!DOCTYPE", rest, re.I):
        out.append("an outside address, data: or a DTD")
    return out


@pytest.mark.parametrize("name", sorted(FAVICONS))
def test_each_favicon_exists_and_answers_200(client, name):
    assert (STATIC / name).is_file()
    r = client.get(f"/static/{name}")
    assert r.status_code == 200 and r.content == (STATIC / name).read_bytes()
    assert r.headers["content-type"].startswith(FAVICONS[name]), r.headers["content-type"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_the_favicons_are_clean():
    assert svg_clean((STATIC / "favicon.svg").read_text(encoding="utf-8")) == []
    assert (STATIC / "favicon.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"  # an icon file's header
    assert (STATIC / "apple-touch-icon.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("name", ICONS)
def test_each_icon_is_clean_and_answers_200(client, name):
    assert svg_clean((STATIC / "icons" / name).read_text(encoding="utf-8")) == []
    r = client.get(f"/static/icons/{name}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")


def test_a_dirty_svg_is_caught():
    good = f'<svg xmlns="{SVG_NS}" viewBox="0 0 24 24"><path d="M0 0h24"/></svg>'
    assert svg_clean(good) == []
    for bad in (good.replace("<path", "<script>x</script><path"), good.replace("<path", '<path style="x"'),
                good.replace("<path", '<path onload="x"'),
                good.replace("<path", '<image href="https://x/y.png"/><path'),
                good.replace('d="M0 0h24"', 'fill="url(data:x)"'), good.replace("</svg>", ""),
                good.replace("<path", '<a href="http://x"><path').replace("</svg>", "</a></svg>")):
        assert svg_clean(bad), bad


# ---------------------------------------------------------------- the rules themselves, on snippets

HEAD_HTML = ('<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, '
             'viewport-fit=cover"><meta name="referrer" content="no-referrer"><meta name="color-scheme" content="dark">'
             '<meta name="theme-color" content="#000000"><title>Goals · Spotter</title>'
             '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">'
             '<link rel="icon" href="/static/favicon.ico" sizes="32x32">'
             '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">'
             '<link rel="stylesheet" href="/static/style.css?v=0123456789ab">')
PAGE = ('<!doctype html><html><head>' + HEAD_HTML + '</head><body>'
        '<header class="top"><a class="brand" href="/">Spotter</a><nav class="tabs" aria-label="Main">'
        '<a class="tab tab-today" href="/today">Today</a><a class="tab tab-progress" href="/progress">Progress</a>'
        '<a class="tab tab-goals" href="/goals" aria-current="page">Goals</a>'
        '<a class="tab tab-settings" href="/settings">Settings</a></nav>'
        '<form class="sign-out" method="post" action="/sign-out"><input type="hidden" name="csrf_token" value="t">'
        '<button class="link">Sign out</button></form></header>'
        '<main class="page goals-page">{body}</main></body></html>')


def seen(body: str = "", address: str = "/goals", **kw) -> Seen:
    return Seen(address, PAGE.replace("{body}", body), True, **kw)


def test_the_frame_rules_pass_a_good_page():
    assert frame_findings(seen()) == []
    assert frame_findings(seen('<svg xmlns="http://www.w3.org/2000/svg" role="img"><title>x</title><desc>y</desc></svg>'
                               '<a href="https://github.com/x">Source code</a>')) == []


@pytest.mark.parametrize("body", [
    '<p style="color: red">x</p>', "<style>p {}</style>", '<img src="data:image/png;base64,AA">',
    '<img src="https://example.com/x.png">', '<link rel="stylesheet" href="//cdn.example/x.css">',
    '<script src="/static/time-zone.js"></script>', "<script>alert(1)</script>", '<button onclick="x()">x</button>',
    '<form action="https://example.com/x" method="post"></form>', '<div class="calm">x</div>',
    '<a class="tab tab-goals" href="/goals" aria-current="page">Goals</a>',
])
def test_each_frame_rule_finds_its_break(body):
    assert frame_findings(seen(body))


def test_the_nav_rules_find_a_wrong_tab_bar():
    current = 'href="/goals" aria-current="page"'
    assert frame_findings(Seen("/progress", PAGE.replace("goals-page", "progress").replace("{body}", ""), True))
    assert frame_findings(Seen("/goals", PAGE.replace(current, 'href="/goals"').replace("{body}", ""), True))
    both = PAGE.replace('href="/today"', 'href="/today" aria-current="page"').replace("{body}", "")
    assert frame_findings(Seen("/goals", both, True))
    swapped = PAGE.replace("tab tab-today", "tab tab-x").replace("{body}", "")
    assert frame_findings(Seen("/goals", swapped, True))
    no_out = PAGE.replace('<form class="sign-out"', '<form class="inline"').replace("{body}", "")
    assert frame_findings(Seen("/goals", no_out, True))
    assert frame_findings(Seen("/goals", PAGE.replace("goals-page", "goals").replace("{body}", ""), True))
    out = ('<html><head>' + HEAD_HTML + '</head><body><header class="top-out">'
           '<a class="brand" href="/">Spotter</a><a class="plain-link" href="/sign-in">Sign in</a></header>'
           '<main class="page out landing"></main></body></html>')
    assert frame_findings(Seen("/", out, False)) == []
    assert frame_findings(Seen("/", out, False, web_ready=False))
    assert frame_findings(Seen("/", out.replace(' class="plain-link" href="/sign-in">Sign in</a>', ""), False))
    assert frame_findings(Seen("/", out.replace("</header>", '<nav class="tabs"></nav></header>'), False))
    home = out.replace('class="brand" href="/">Spotter', 'class="brand" href="/">Home')
    assert frame_findings(Seen("/", home, False))


@pytest.mark.parametrize("old, new", [
    ('<nav class="tabs" aria-label="Main">', '<nav class="tabs">'),
    ('<nav class="tabs" aria-label="Main">', '<nav class="tabs" aria-label="Main" hidden>'),
    ('<a class="brand" href="/">Spotter</a>', '<a class="brand" href="/">Home</a>'),
    ('</nav>', '</nav><a class="plain-link" href="/sign-in">Sign in</a>'),
    ('<button class="link">Sign out</button>', '<button class="link">Sign out</button><input name="x">'),
    ('<meta charset="utf-8">', ''), ('<meta name="referrer" content="no-referrer">', ''),
    (', viewport-fit=cover', ''), ('<meta name="theme-color" content="#000000">', ''),
    ('<link rel="icon" href="/static/favicon.ico" sizes="32x32">',
     '<link rel="icon" href="/static/favicon.ico" sizes="32x32">' * 2),
    ('</head>', '<link rel="manifest" href="/static/x.webmanifest"></head>'),
    ('<link rel="stylesheet" href="/static/style.css?v=0123456789ab">', '<link rel="stylesheet" href="/static/x.css">'),
])
def test_the_head_and_the_top_bar_are_exact(old, new):
    assert frame_findings(seen()) == []
    assert PAGE.count(old) == 1, old
    assert frame_findings(Seen("/goals", PAGE.replace(old, new).replace("{body}", ""), True))


def test_every_srcset_candidate_is_read():
    assert not outside("/static/a.png 1x, /static/b.png 2x", srcset=True)
    assert outside("/static/a.png 1x, https://example.com/b.png 2x", srcset=True)
    assert outside("//example.com/a.png 1x", srcset=True)
    assert frame_findings(seen('<img src="/static/a.png" srcset="/static/a.png 1x, https://example.com/b.png 2x">'))
    assert not outside("/day/2026-11-09?a=1,2")  # a comma inside one address is not a list


def test_calm_follows_the_address_and_the_post():
    calm = PAGE.replace("<body>", '<body class="calm">').replace("{body}", "")
    assert frame_findings(Seen("/goals?saved=goal", calm, True)) == []
    assert frame_findings(Seen("/goals?saved=", calm, True)) == []
    assert frame_findings(Seen("/goals", calm, True))
    assert frame_findings(Seen("/goals?saved=goal", PAGE.replace("{body}", ""), True))
    assert frame_findings(Seen("/goals", calm, True, calm=True)) == []


@pytest.mark.parametrize("rule, good, bad", [
    (svg_findings, '<svg role="img"><title>x</title><desc>y</desc></svg>', '<svg role="img"><title>x</title></svg>'),
    (popover_findings,
     '<button popovertarget="s">x</button><div id="s" popover aria-labelledby="t"><h2 id="t">x</h2></div>',
     '<button popovertarget="s">x</button><div id="s"></div>'),
    (popover_findings, '<div id="s" popover aria-labelledby="t"><h2 id="t">x</h2></div>',
     '<div id="s" popover aria-labelledby="nope"></div>'),
    (twin_findings,
     '<button class="tile" popovertarget="s">80 kg Weight</button><a class="tile-link" href="#">80 kg Weight</a>',
     '<button class="tile" popovertarget="s">80 kg Weight</button><a class="tile-link" href="#">80 kg</a>'),
    (foldout_findings, '<details class="add"><summary>Add weigh-in</summary></details>',
     '<details class="add" open><summary>Add weigh-in</summary></details>'),
    (foldout_findings, '<details class="add" open><summary>Add</summary><p class="error" id="e">x</p></details>',
     '<details class="add"><summary>Add</summary><div class="field has-error"></div></details>'),
    (labelledby_findings, '<section class="card hero" aria-labelledby="l"><p id="l">Eaten</p></section>',
     '<section class="card hero" aria-labelledby="l"><p>Eaten</p></section>'),
    (labelledby_findings, '<section aria-labelledby="a b"><h2 id="a">x</h2><p id="b">y</p></section>',
     '<section aria-labelledby="a b"><h2 id="a">x</h2></section>'),
    (labelledby_findings, '<ul aria-label="Logged"><li>x</li></ul>', '<section aria-labelledby=""><h2>x</h2></section>'),
    (goal_li_findings, '<ul class="goals"><li class="goal"><p class="legend"><span>Plan</span></p></li></ul>',
     '<ul class="goals"><li class="goal"><ul class="legend"><li>Plan</li></ul></li></ul>'),
    (table_wrap_findings, '<div class="table-wrap"><table><tr><td>x</td></tr></table></div>',
     '<div class="card"><table><tr><td>x</td></tr></table></div>'),
    (inputmode_findings, '<input name="kg" inputmode="decimal"><input type="number" name="x" inputmode="numeric">',
     '<input name="kcal">'),
    (describedby_findings,
     '<input name="kg" aria-invalid="true" aria-describedby="kg-error"><p class="error" id="kg-error">x</p>',
     '<input name="kg" aria-invalid="true"><p class="error" id="kg-error">x</p>'),
    (describedby_findings, '<div class="error"><p class="error">Not saved.</p></div>',
     '<input name="kg" aria-describedby="kg-error"><p class="error" id="kg-error">x</p>'),
])
def test_each_page_rule_passes_its_good_markup_and_finds_its_bad(rule, good, bad):
    assert rule(seen(good)) == []
    assert rule(seen(bad))


def test_an_open_fold_out_is_allowed_by_its_mark_and_on_foods_with_none_saved():
    food = '<details class="add" id="add-food" open><summary>Add food</summary></details>'
    assert foldout_findings(seen(food, address="/day/2026-11-11?add=food")) == []
    assert foldout_findings(seen(food, address="/day/2026-11-11?add=weigh-in"))
    assert foldout_findings(seen(food, address="/day/2026-11-11"))
    save = '<details class="add" id="save-food" open><summary>Save a food</summary></details>'
    assert foldout_findings(seen(save, address="/foods")) == []
    assert foldout_findings(seen(save + '<div class="table-wrap"><table></table></div>', address="/foods"))


def _request(method: str = "GET", query: str = ""):
    from starlette.requests import Request
    return Request({"type": "http", "method": method, "path": "/x", "query_string": query.encode(), "headers": []})


def test_a_view_can_override_its_frame_row():
    """web.frame() reads FRAMES by template name; a view passing page_class, width_class, current_tab or
    calm overrides that row, and the keys never reach the template's own context."""
    context = {"page_class": "cardio-page", "width_class": "form-page", "current_tab": "goals", "calm": True}
    shown = web.frame(_request(), "progress.html", 200, True, context)
    assert shown["main_class"] == "page cardio-page form-page" and shown["current_tab"] == "goals"
    assert shown["calm"] is True and context == {}
    plain = web.frame(_request(), "progress.html", 200, True, {})
    assert plain["main_class"] == "page progress" and plain["current_tab"] == "progress" and plain["calm"] is False
    assert web.frame(_request(), "progress.html", 200, False, {})["current_tab"] is None  # no tab signed out
    assert web.frame(_request(), "unlisted.html", 200, True, {})["main_class"] == "page"
    assert web.frame(_request("POST"), "goals.html", 400, True, {})["calm"] is True
    assert web.frame(_request(query="add="), "goals.html", 200, True, {"calm": False})["calm"] is False
    # past needs both dates: a day page with no today in its context is not a past day.
    on, today = date(2026, 11, 9), date(2026, 11, 11)
    assert web.frame(_request(), "day.html", 200, True, {"on": on})["main_class"] == "page today"
    assert web.frame(_request(), "day.html", 200, True, {"on": on, "today": today})["main_class"] == "page today past"
    assert web.frame(_request(), "day.html", 200, True, {"on": today, "today": today})["main_class"] == "page today"


# ---------------------------------------------------------------- the 404 and 500 pages, every message

@pytest.mark.parametrize("case", sorted(ERROR_PAGES))
def test_each_error_and_message_page_keeps_the_frame(case, web_client, seam, monkeypatch):
    """Section L's message frame on the 404 and 500 pages and on each message status: no tab, notice-page,
    the signed-out frame for an unrouted path and a crash, calm only on a POST answered 400."""
    page = render_error_page(case, web_client, seam, monkeypatch)
    assert page.response.status_code == ERROR_PAGES[case][0], case
    s = Seen(page.address, page.response.text, page.signed_in, web_ready=page.web_ready, message=True,
             calm=page.calm)
    findings = frame_findings(s) + page_rule_findings(s)
    assert not findings, "\n".join(findings)


# ---------------------------------------------------------------- the page after each write is calm

def follow(client, r) -> tuple[str, str]:
    """Follow a write's redirects (through /today) to the page a person lands on: (its address, its markup)."""
    for _ in range(3):
        assert r.status_code == 303, (r.status_code, r.text[:200])
        parts = urlsplit(r.headers["location"])
        address = parts.path + (f"?{parts.query}" if parts.query else "")
        r = client.get(address)
        if r.status_code == 200:
            return address, r.text
    raise AssertionError(f"no page after {address}")


def test_the_page_after_every_write_is_calm(client):
    """Every web write's redirect carries a mark (section L, The calm page), so the page it lands on is calm.
    A write that changed nothing lands calm too, with no strip."""
    sign_in(client)
    user = only_user()
    today = today_of(user).isoformat()
    # (the write, its page's address, its markup, whether the person had no zone yet, their today then)
    landed = []

    def write(label: str, path: str, **data):
        address, markup = follow(client, post(client, path, **data))
        landed.append((label, address, markup, not only_user().time_zone, today_of(only_user()).isoformat()))
        return address

    # Sample data first: it goes only into an empty account.
    assert write("add sample data", "/settings/sample").endswith("?saved=sample")
    assert write("remove sample data", "/settings/sample/remove").endswith("?deleted=sample")
    assert write("the automatic zone save", "/settings", time_zone="Asia/Kolkata", next="/progress") == \
        "/progress?saved=time_zone"
    # The person's today is read again now the zone is saved: before it, it was UTC's date, a day behind
    # Asia/Kolkata from 18:30 to 24:00 UTC, so the writes below would land on a past day.
    today = today_of(only_user()).isoformat()
    # It lands on the person's today, read fresh: without the read above it is their yesterday in the window.
    assert write("a weigh-in", f"/day/{today}/weigh-in", kg="80").startswith(
        f"/day/{today_of(only_user()).isoformat()}?")
    assert write("the same weigh-in again", f"/day/{today}/weigh-in", kg="80").endswith("?saved=unchanged")
    write("a measurement", f"/day/{today}/measurement", kind="waist", value="84")
    write("a food entry", f"/day/{today}/food", description="Poha", kcal="250")
    write("a cardio session", f"/day/{today}/cardio", exercise="Outdoor Walk", minutes="30")
    weigh_in = _ids(Bodyweight, user.id)[0]
    write("an edit", f"/entries/bodyweight/{weigh_in}/edit", kg="79", date=today)
    assert write("an unchanged edit", f"/entries/bodyweight/{weigh_in}/edit", kg="79", date=today) \
        .endswith("?saved=unchanged")
    estimate = services.log_food(user.id, {"entries": [{"description": "Dal", "kcal": 180, "estimated": True}]},
                                 "claude")["ids"][0]
    write("confirm", f"/entries/food_entry/{estimate}/confirm")
    assert write("confirm a number already yours", f"/entries/food_entry/{estimate}/confirm") \
        .endswith("?saved=unchanged")
    write("delete", f"/entries/bodyweight/{weigh_in}/delete")
    assert write("undo", f"/entries/bodyweight/{weigh_in}/restore").endswith("?restored=bodyweight")
    write("a saved food", "/foods", name="Idli", serving_label="1 idli", kcal_per_serving="40")
    food = _ids(SavedFood, user.id)[0]
    assert write("an unchanged saved food", f"/foods/{food}/edit", serving_label="1 idli",
                 kcal_per_serving="40") == "/foods?saved=unchanged"
    write("delete a saved food", f"/foods/{food}/delete")
    write("restore a saved food", f"/foods/{food}/restore")
    write("the targets", "/settings/targets", calorie_target_kcal="2000")
    assert write("unchanged targets", "/settings/targets", calorie_target_kcal="2000") == "/settings?saved=unchanged"
    write("the switch", "/settings/claude-can", claude_can="read")
    write("the zone", "/settings", time_zone="Asia/Kolkata")
    write("a goal", "/goals", kind="bodyweight", target_value="70", target_date="2099-01-01")
    goal = services.get_goals(user.id)["goals"][0]["goal_id"]
    write("drop a goal", f"/goals/{goal}/status", status="dropped")

    findings = []
    for name, address, markup, zone_missing, then in landed:
        s = Seen(address, markup, True, zone_missing=zone_missing, today=then)
        if not s.expected_calm():
            findings.append(f"{name}: {address} carries no mark")
        findings += [f"{name}: {f}" for f in frame_findings(s)]
        if address.endswith("saved=unchanged") and (s.dom.find_all("p", "ok") or s.dom.find_all("div", "ok")):
            findings.append(f"{name}: {address} shows a strip though nothing changed")
    assert len(landed) == 24
    assert not findings, "\n".join(findings)


@pytest.mark.parametrize("address", ["/day/{today}?saved=bodyweight", "/day/{today}?saved=confirmed",
                                     "/day/{today}?restored=cardio_entry", "/day/{today}?saved=sample",
                                     "/day/{today}?deleted=sample", "/day/{today}?saved=time_zone",
                                     "/day/{today}?saved=unchanged", "/foods?saved=unchanged",
                                     "/settings?saved=unchanged", "/goals?saved=unchanged",
                                     "/progress?saved=time_zone"])
def test_each_new_mark_makes_its_page_calm_with_no_strip(client, address):
    """The marks pass B2 adds: each page is calm. The two that must never have words show no strip."""
    sign_in(client)
    services.update_settings(only_user().id, {"time_zone": "Asia/Kolkata"}, "web")
    today = today_of(only_user()).isoformat()
    address = address.format(today=today)
    r = client.get(address)
    assert r.status_code == 200
    s = Seen(address, r.text, True, today=today)
    assert s.expected_calm() and frame_findings(s) == []
    if address.endswith(("saved=unchanged", "saved=time_zone")):
        assert not s.dom.find_all("p", "ok") and not s.dom.find_all("div", "ok")


# ---------------------------------------------------------------- the Food card under the page rules (pass B4)

# The page rules that do not yet run on the whole day page (it is not in BUILT), run here on the Food card
# alone, with T3's estimate check. They hold whether or not BUILT names the day page.
CARD_RULES = (table_wrap_findings, inputmode_findings, describedby_findings, foldout_findings)


def food_card_of(s: Seen) -> Seen:
    """The same response, its tree cut down to the Food card: the section.card in div.col-main headed Food."""
    [main] = s.dom.find_all("div", "col-main")
    [card] = [c for c in main.elements() if c.is_("section", "card") and [h.text() for h in c.find_all("h2")] == ["Food"]]
    s.dom = card
    return s


def card_findings(s: Seen) -> list[str]:
    card = food_card_of(s)
    return [f"{s.address}: {f}" for f in [f for rule in CARD_RULES for f in rule(card)] + estimate_findings(card.dom)]


@pytest.mark.parametrize("account", sorted(ACCOUNTS))
def test_the_food_card_keeps_the_page_rules_on_every_day_and_every_error(client, account):
    sign_in(client)
    user = only_user()
    ACCOUNTS[account](user.id)
    today = today_of(user).isoformat()
    findings, opened = [], 0
    for address in get_paths(user.id)["/day/{date}"]:
        r = client.get(address)
        assert r.status_code == 200, address
        s = Seen(address, r.text, True, today=today)
        findings += card_findings(s)
        opened += "open" in s.dom.find_all("details", "add")[0].attrs
    for address, data in error_posts(today, user.id)["/day/{date}"]:
        r = post(client, address, **data)
        assert r.status_code == 400, address
        s = Seen(address, r.text, True, today=today, posted_to="/day/{date}")
        findings += card_findings(s)
        if address.endswith("/food"):
            assert "open" in s.dom.find_all("details", "add")[0].attrs and s.dom.find_all("p", "error"), data
    assert opened == 2  # the two ?add=food addresses, and no other
    assert not findings, "\n".join(findings)


SIDE_FOLDOUTS = ("add-cardio", "add-weigh-in", "add-measurement")


def test_the_sweeps_open_each_side_fold_out_by_its_mark_and_by_its_error(client):
    """The day page is in BUILT, so every rule runs on it. The sweeps' own addresses must reach each side
    fold-out open by its mark (?add=cardio, ?add=weigh-in, ?add=measurement) and by its form's error, or the
    rules never read an open one."""
    from .test_pages_text import CLAUDE_SKIPS  # noqa: PLC0415

    assert "/day/{date}" in BUILT and "/day/{date}" not in CLAUDE_SKIPS
    sign_in(client)
    user = only_user()
    today = today_of(user).isoformat()
    by_mark, by_error = set(), {}
    for address in get_paths(user.id)["/day/{date}"]:
        r = client.get(address)
        assert r.status_code == 200, address
        by_mark |= {d.attrs["id"] for d in Seen(address, r.text, True, today=today).dom.find_all("details", "add")
                    if "open" in d.attrs}
    for address, data in error_posts(today, user.id)["/day/{date}"]:
        r = post(client, address, **data)
        assert r.status_code == 400, address
        for d in Seen(address, r.text, True, today=today, posted_to="/day/{date}").dom.find_all("details", "add"):
            if "open" in d.attrs:
                by_error.setdefault(d.attrs["id"], []).append(
                    [c.tag for c in d.iter() if c.attrs.get("aria-invalid") == "true"])
    assert set(SIDE_FOLDOUTS) <= by_mark
    for fold in SIDE_FOLDOUTS:
        assert fold in by_error, fold
    # A select's error as well as an input's, on the two fold-outs that have a select.
    for fold in ("add-cardio", "add-measurement"):
        assert ["select"] in by_error[fold] and ["input"] in by_error[fold], (fold, by_error[fold])


def test_the_card_rules_catch_each_break_on_the_real_card(client):
    """Each rule, on the real Food card: a fold-out sent open with no mark and no error, one with an error sent
    closed, an error tied to nothing, a number input with no inputmode, and an estimate tag in brackets."""
    sign_in(client)
    services.log_food(only_user().id, {"entries": [{"description": "Dal", "kcal": 180, "estimated": True}]}, "claude")
    today = today_of(only_user()).isoformat()
    day = f"/day/{today}"
    page = client.get(day).text
    error = post(client, f"{day}/food", description="Rice", kcal="abc").text
    assert card_findings(Seen(day, page, True, today=today)) == []
    assert card_findings(Seen(f"{day}/food", error, True, today=today, posted_to="/day/{date}")) == []

    def broken(markup: str, old: str, new: str, address: str = day) -> list[str]:
        assert markup.count(old) == 1, old
        return card_findings(Seen(address, markup.replace(old, new), True, today=today, posted_to="/day/{date}"))

    closed, opened = '<details class="add" id="add-food">', '<details class="add" id="add-food" open>'
    assert any("is open" in f for f in broken(page, closed, opened))
    assert any("holds an error, closed" in f for f in broken(error, opened, closed, f"{day}/food"))
    assert any("tied to no input" in f for f in broken(error, ' aria-describedby="food-kcal-error"', "", f"{day}/food"))
    assert any("invalid with no error" in f for f in broken(error, ' id="food-kcal-error"', "", f"{day}/food"))
    assert any("no inputmode" in f for f in broken(page, ' name="kcal" inputmode="numeric"', ' name="kcal"'))
    assert any("in brackets" in f for f in broken(page, '<span class="entry-tags"><span class="tag tag-claude_estimate">',
                                                  '<span class="entry-tags">(<span class="tag tag-claude_estimate">'))
    assert any("table outside" in f for f in broken(page, '<dl class="facts totals">', '<table><tr><td>x</td></tr></table>'
                                                    '<dl class="facts totals">'))


# ---------------------------------------------------------------- every form that posts holds its token

@pytest.mark.parametrize("bad", [
    '<form method="post" action="/x"><button>Go</button></form>',
    '<form method="POST" action="/x"><input type="hidden" name="csrf_token" value=""></form>',
    '<form method="post" action="/x"><input type="text" name="csrf_token" value="t"></form>',
    '<form method="post" action="/x"><input type="hidden" name="token" value="t"></form>',
    '<form method="post" action="/x"><input type="hidden" name="csrf_token"></form>',
])
def test_the_token_rule_finds_a_form_that_posts_without_its_token(bad):
    good = ('<form method="post" action="/x" class="inline"><input type="hidden" name="csrf_token" value="t">'
            '<button class="link">Go</button></form><form method="get" action="/y"><button>Find</button></form>')
    assert token_findings(seen(good)) == []
    assert token_findings(seen(good + bad))
    assert token_findings in EVERYWHERE and token_findings in PAGE_RULES


def test_the_token_rule_reads_every_form_on_a_full_day_page(client):
    sign_in(client)
    services.update_settings(only_user().id, {"time_zone": "Asia/Kolkata"}, "web")
    services.add_sample_data(only_user().id, "web")
    today = today_of(only_user()).isoformat()
    page = client.get(f"/day/{today}").text
    s = Seen(f"/day/{today}", page, True, today=today)
    posting = [f for f in s.dom.find_all("form") if f.attrs.get("method") == "post"]
    assert len(posting) >= 15 and token_findings(s) == []  # sign-out, each Confirm and Delete, each fold-out
    one = re.search(r'(<form method="post" action="/entries/food_entry/\d+/delete" class="inline">\s*)'
                    r'<input type="hidden" name="csrf_token" value="[^"]+">', page)
    broken = page.replace(one.group(0), one.group(1), 1)
    assert [f for f in token_findings(Seen(f"/day/{today}", broken, True, today=today)) if "food_entry" in f]


EDIT_ROUTE = "/entries/{kind}/{entry_id}/edit"


def test_the_sweeps_reach_each_kinds_edit_page_and_its_errors(client):
    """The edit pages are in BUILT, so every rule runs on them. The sweeps' own addresses must reach each kind's
    edit page, and each kind's page sent back with an error, on an input, a select and a textarea, or the rules
    never read them."""
    from .test_pages_text import CLAUDE_SKIPS  # noqa: PLC0415

    assert EDIT_ROUTE in BUILT and EDIT_ROUTE not in CLAUDE_SKIPS
    sign_in(client)
    user = only_user()
    ACCOUNTS["sample"](user.id)
    today = today_of(user).isoformat()
    kinds = {kind for kind, _ in ENTRY_MODELS}
    assert {a.split("/")[2] for a in get_paths(user.id)[EDIT_ROUTE]} == kinds
    invalid: dict[str, set[str]] = {}
    for address, data in error_posts(today, user.id)[EDIT_ROUTE]:
        r = post(client, address, **data)
        assert r.status_code == 400, address
        invalid.setdefault(address.split("/")[2], set()).update(
            n.tag for n in parse(r.text).iter() if n.attrs.get("aria-invalid") == "true")
    assert set(invalid) == kinds and all(invalid.values()), invalid
    assert set().union(*invalid.values()) == {"input", "select", "textarea"}


# ---------------------------------------------------------------- Today's glance block: tiles, twins, sheets

def glance_page(client, account: str = "sample") -> tuple[str, str]:
    """Today's page on an account, with its glance block: (address, markup)."""
    sign_in(client)
    ACCOUNTS[account](only_user().id)
    address = f"/day/{today_of(only_user()).isoformat()}"
    r = client.get(address)
    assert r.status_code == 200 and '<div class="glance">' in r.text
    return address, r.text


def test_the_popover_and_twin_rules_catch_each_break_on_the_real_glance(client):
    """The first page with a tile and a sheet: each rule passes the real glance block, and finds each break in
    it. A tile's popovertarget naming no popover, a sheet that is no popover, a sheet's title gone, a tile with
    no twin, a twin with other words, a chart with no desc."""
    address, page = glance_page(client)
    today = address.split("/")[2]
    rules = (popover_findings, twin_findings, svg_findings, labelledby_findings)

    def found(markup: str) -> list[str]:
        return [f for rule in rules for f in rule(Seen(address, markup, True, today=today))]

    assert found(page) == []
    assert page.count('popovertarget="sheet-') == 6  # three tiles, three close buttons

    def broken(old: str, new: str, pattern: bool = False) -> list[str]:
        if pattern:
            assert len(re.findall(old, page, re.S)) == 1, old
            return found(re.sub(old, new, page, count=1, flags=re.S))
        assert page.count(old) == 1, old
        return found(page.replace(old, new))

    button = '<button class="tile tile-weight" type="button" popovertarget="sheet-weight">'
    assert any("names no popover" in f for f in broken(button, button.replace("sheet-weight", "sheet-nope")))
    sheet = '<div class="sheet" popover id="sheet-calories"'
    assert any("names no popover" in f for f in broken(sheet, '<div class="sheet" id="sheet-calories"'))
    title = '<h2 class="noun" id="sheet-training-title">'
    assert any("aria-labelledby" in f for f in broken(title, '<h2 class="noun">'))
    twin = r'<a class="tile tile-training tile-link" href="/progress#trends">.*?</a>'
    assert any("has no twin link" in f for f in broken(twin, "", pattern=True))
    other = r'(<a class="tile tile-calories tile-link" href="/progress#trends">.*?)kcal eaten a day'
    assert any("has no twin link" in f for f in broken(other, r"\1kcal a day", pattern=True))
    desc = r'<desc id="chart-weight-sheet-desc">.*?</desc>'
    assert any("without role, title and desc" in f for f in broken(desc, "", pattern=True))


def _one_weigh_in(user_id: int, today) -> None:
    services.log_bodyweight(user_id, {"kg": "80"}, "claude")


def _none_in_28_days(user_id: int, today) -> None:
    for n in (40, 45):
        services.log_bodyweight(user_id, {"kg": "80", "date": (today - timedelta(days=n)).isoformat()}, "claude")


def _two_recent_no_rate(user_id: int, today) -> None:
    services.log_bodyweight(user_id, {"kg": "80.4", "date": (today - timedelta(days=3)).isoformat()}, "claude")
    services.log_bodyweight(user_id, {"kg": "80"}, "claude")


def _food_in_28_not_7(user_id: int, today) -> None:
    services.log_food(user_id, {"date": (today - timedelta(days=10)).isoformat(),
                                "entries": [{"description": "Dal", "kcal": 400}]}, "claude")


def _targets_and_a_zero_rate(user_id: int, today) -> None:
    services.update_settings(user_id, {"calorie_target_kcal": 2000, "target_sessions_per_week": 0,
                                       "target_cardio_minutes_per_week": 150}, "web")
    for n in (21, 14, 7, 0):
        services.log_bodyweight(user_id, {"kg": "80", "date": (today - timedelta(days=n)).isoformat()}, "claude")
    services.log_food(user_id, {"entries": [{"description": "Thali", "kcal": 1000}]}, "claude")


def _one_lift_session_and_cardio_today(user_id: int, today) -> None:
    services.log_lifts(user_id, {"sets": [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]}, "claude")
    services.log_cardio(user_id, {"exercise": "Outdoor Walk", "minutes": 30}, "claude")


EMPTY_WEIGHT_CARD = '<section class="card chart-card is-empty" aria-labelledby="weight-card-title">'
EMPTY_EATEN_CARD = '<section class="card chart-card is-empty" aria-labelledby="eaten-card-title">'

# Each state, and markup only that state draws: proof the page reached it.
GLANCE_STATES = {
    _one_weigh_in: ("One weigh-in in the last 28 days. The chart starts from the second one.", EMPTY_WEIGHT_CARD),
    _none_in_28_days: ("No weigh-ins in the last 28 days.", "No weigh-in in the last 7 days", EMPTY_WEIGHT_CARD),
    _two_recent_no_rate: ("The weekly rate needs a weigh-in from 14 to 42 days ago.", 'id="chart-weight-sheet"',
                          'id="chart-weight-4w"'),
    _food_in_28_not_7: ('<span class="tile-value">No food in the last 7 days</span>', EMPTY_EATEN_CARD),
    _one_lift_session_and_cardio_today: ('<span class="aside">1 lift session</span>', 'class="pill is-lift is-today"',
                                         ", today: lift session</span>"),
    _targets_and_a_zero_rate: ("The trend moved 0&nbsp;kg a week", "The session target is 0 a week.", '<line class="goal"',
                               "The calorie target is 2,000 kcal a day."),
}


@pytest.mark.parametrize("state", list(GLANCE_STATES), ids=lambda f: f.__name__.strip("_"))
def test_the_glance_states_the_sweeps_never_reach_keep_every_rule(client, state):
    """The sweeps' three accounts never show one weigh-in in 28 days, weigh-ins but none in 28 days, weigh-ins
    with no rate, food in 28 days but none in 7, or a 0 target. Each such page, and the page sent back with an
    error, keeps the frame rules, the page rules, T1's text rules and T3's step 4 checks."""
    sign_in(client)
    user = only_user()
    today = today_of(user)
    state(user.id, today)
    address = f"/day/{today.isoformat()}"
    names, notes, entered = names_of(user.id), entered_notes(user.id), entered_text(user.id)
    r = client.get(address)
    error = post(client, f"{address}/weigh-in", kg="abc")
    assert r.status_code == 200 and error.status_code == 400
    findings = []
    zone_missing = not only_user().time_zone
    for s in (Seen(address, r.text, True, zone_missing=zone_missing, today=today.isoformat()),
              Seen(f"{address}/weigh-in", error.text, True, zone_missing=zone_missing, today=today.isoformat(),
                   calm=True, posted_to="/day/{date}")):
        assert '<div class="glance">' in s.markup
        assert all(marker in s.markup for marker in GLANCE_STATES[state]), state.__name__
        findings += frame_findings(s) + page_rule_findings(s)
        findings += page_findings(s.address, s.markup, entered, user.id, route=s.posted_to or None)
        findings += step_4_findings(s.address, s.markup, names, notes, route=s.posted_to or None)
    assert not findings, "\n".join(findings)


@pytest.mark.parametrize("account", sorted(ACCOUNTS))
def test_the_sweeps_reach_todays_glance_and_past_days_without_it(client, account):
    """Both sweeps render every address of get_paths: today's page on each account carries the glance block,
    and every other day, a day after today among them, carries none."""
    sign_in(client)
    user = only_user()
    ACCOUNTS[account](user.id)
    today = today_of(only_user()).isoformat()
    on_today, other = 0, 0
    for address in get_paths(user.id)["/day/{date}"]:
        r = client.get(address)
        assert r.status_code == 200, address
        has = '<div class="glance">' in r.text
        if address.split("?")[0].split("/")[2] == today:
            assert has, address
            on_today += 1
        else:
            assert not has and "popover" not in r.text, address
            other += 1
    assert on_today >= 10 and other >= 2


# ---------------------------------------------------------------- Today's titled cards and day pills (pass B7)

def titled_card_findings(s: Seen) -> list[str]:
    """Section L's card rule on one page: a card with an h2 is a section.card named by aria-labelledby, each but
    the Today card by its own first h2, and a card with no title is a bare div.card. No id repeats."""
    out = []
    for card in s.dom.find_all(cls="card"):
        heads = card.find_all("h2")
        named = card.attrs.get("aria-labelledby", "")
        if "hero" in card.classes:
            if card.tag != "section" or named not in {n.attrs.get("id") for n in card.iter()}:
                out.append("the Today card is not a section named by an element inside it")
        elif not heads:
            if card.tag != "div":
                out.append(f"a card with no title is a <{card.tag}>")
        elif card.tag != "section" or not named or named != heads[0].attrs.get("id"):
            out.append(f"the {heads[0].text()!r} card is a <{card.tag}> named by {named!r}, not by its own h2")
    ids = [n.attrs["id"] for n in s.dom.iter() if n.attrs.get("id")]
    out += [f"the id {i!r} is on the page {ids.count(i)} times" for i in sorted(set(ids)) if ids.count(i) > 1]
    return out


def pill_findings(s: Seen, grid: dict) -> list[str]:
    """Today's ol.days against get_days: one li a day, oldest first, each one a.pill to its day with the one
    state class the day calls for, is-today on the last only, and hidden words naming the day."""
    lists = s.dom.find_all("ol", "days")
    if len(lists) != 1:
        return [f"{len(lists)} day grids"]
    out = []
    items = lists[0].elements()
    if len(items) != len(grid["days"]):
        out.append(f"{len(items)} pills for {len(grid['days'])} days")
    for n, (li, day) in enumerate(zip(items, grid["days"])):
        links = li.elements()
        if li.tag != "li" or len(links) != 1 or not links[0].is_("a", "pill"):
            out.append(f"{day['date']}: not one a.pill in its li")
            continue
        a = links[0]
        state = "is-lift" if day["lift_sessions"] else ("is-cardio" if day["cardio_entries"] else "is-none")
        want = ["pill", state] + (["is-today"] if n == len(items) - 1 else [])
        if a.classes != want:
            out.append(f"{day['date']}: classes {a.classes}, not {want}")
        if a.attrs.get("href") != f"/day/{day['date']}":
            out.append(f"{day['date']}: links to {a.attrs.get('href')!r}")
        words = a.find_all("span", "sr")
        if len(words) != 1 or not words[0].text() or a.text() != words[0].text():
            out.append(f"{day['date']}: no hidden words, or words outside them")
    return out


@pytest.mark.parametrize("account", sorted(ACCOUNTS))
def test_every_day_page_names_its_titled_cards_and_today_draws_its_pills(client, account):
    """On each account, every day address the sweeps render and every day page sent back with its error: the
    card rule and no repeated id. On today's page, the day pills against get_days (section L, Day pill)."""
    sign_in(client)
    user = only_user()
    ACCOUNTS[account](user.id)
    today = today_of(only_user()).isoformat()
    grid = services.get_days(user.id)
    pages = [(address, client.get(address)) for address in get_paths(user.id)["/day/{date}"]]
    pages += [(address, post(client, address, **data)) for address, data in error_posts(today, user.id)["/day/{date}"]]
    findings, on_today = [], 0
    for address, r in pages:
        assert r.status_code in (200, 400), address
        s = Seen(address, r.text, True, today=today)
        findings += [f"{address}: {f}" for f in titled_card_findings(s)]
        if address.split("?")[0].split("/")[2] == today:
            on_today += 1
            findings += [f"{address}: {f}" for f in pill_findings(s, grid)]
        elif s.dom.find_all("ol", "days"):
            findings.append(f"{address}: a day grid off today")
    assert on_today >= 10 and len(pages) > on_today
    assert not findings, "\n".join(findings)


def test_the_card_and_pill_checks_catch_each_break_on_the_real_page(client):
    """Both checks pass the real page, and find each break in it."""
    address, page = glance_page(client)
    today = address.split("/")[2]
    grid = services.get_days(only_user().id)

    def found(markup: str) -> list[str]:
        s = Seen(address, markup, True, today=today)
        return titled_card_findings(s) + pill_findings(s, grid)

    assert found(page) == []

    def broken(old: str, new: str) -> list[str]:
        assert page.count(old) == 1, old
        return found(page.replace(old, new))

    lifts = '<section class="card" aria-labelledby="lifts-title">'
    assert broken(lifts, '<div class="card">')  # a titled card as a bare div
    assert broken(lifts, '<section class="card" aria-labelledby="food-title">')  # named by another card's h2
    assert broken('<h2 id="cardio-title">', '<h2 id="food-title">')  # a repeated id that names no card
    # A repeated id, every card still named by its own h2: only the id check finds it.
    twice = broken('<h2 id="cardio-title">Cardio</h2>', '<h2 id="cardio-title">Cardio</h2><span id="food-title"></span>')
    assert twice == ["the id 'food-title' is on the page 2 times"]
    assert broken('class="card daygrid" aria-labelledby="days-title"', 'class="card daygrid"')
    first = f'<a class="pill {"is-lift" if grid["days"][0]["lift_sessions"] else "is-cardio"}" href="/day/{grid["days"][0]["date"]}">'
    assert broken(first, first.replace('class="pill ', 'class="pill is-today '))  # today on the first
    assert broken(first, first.replace(grid["days"][0]["date"], grid["days"][1]["date"]))  # the wrong day
    last = 'class="pill is-cardio is-today"' if 'class="pill is-cardio is-today"' in page else 'is-today"'
    assert broken(last, last.replace(" is-today", ""))  # no today
    assert found(page.replace('<span class="sr">', '<span>'))  # the words no longer hidden
