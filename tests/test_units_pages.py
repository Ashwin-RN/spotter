"""Units on the pages and the charts (contract units.md, package U2: R15 to R17, decisions 6, 8, 9, 10 and 16).

A person who keeps weights in lb and distances in miles sees lb, mi, mph and per mi on every page and chart, and
every form takes and fills those units; storage stays kg and km. A kg and km account's pages are as they were,
but for three texts that change on every account: the hi page's line, the switch's words and the Units card. That
holds across the commit for the build's own record (the U2 hand-over); here, a kg and km account's pages are held
equal whether the units were never chosen or chosen as kg and km.
"""

import inspect
import random
import re
from datetime import date, timedelta

import pytest

from app import charts, db, formatting, rules, services, users, web
from app.models import Bodyweight, CardioEntry, EntryVersion, Goal, User
from app.services import cardio, goals, lifts
from evals.four_weeks import four_weeks

from . import text_rules as tr
from .test_pages_text import entered_notes, entered_text, names_of, page_findings, parse, step_4_findings
from .test_web import (  # noqa: F401 - fixtures
    FIXED_NOW, SITE, calls, client, only_user, post, rows, seam, session_of, sign_in, web_client,
)
from .test_web_goals import form_parts

LB_MI = formatting.Units("lb", "mi")
DAY = "2026-11-10"  # four_weeks' fixed day, a Tuesday: the clock is pinned to it
RUN_DAY, LIFT_DAY = "2026-11-07", "2026-11-09"  # its Saturday run of 5 km, its Monday lift session
BY = "2027-01-30"
# kg, km or km/h as a word: on a lb and mi page none is left. The contract's one exception, a best time's name (1, 5
# or 10 km), is drawn on none of these pages, so none is let through: "the best 5 km time" would be a km figure.
KG_OR_KM = re.compile(r"\b(?:kg|km)\b|km/h")


def set_units(user_id: int, weight: str | None, distance: str | None) -> None:
    with db.session_scope() as s:
        user = s.get(User, user_id)
        user.weight_unit, user.distance_unit = weight, distance


def stored(user_id: int) -> tuple:
    with db.session_scope() as s:
        user = s.get(User, user_id)
        return user.weight_unit, user.distance_unit


@pytest.fixture
def pinned(client, monkeypatch):
    """A signed-in person on the fixed day, with nothing logged."""
    sign_in(client)
    monkeypatch.setattr(db, "utcnow", lambda: FIXED_NOW)
    return client


def stock(user_id: int) -> dict:
    """four_weeks, and a routine with one weight given: the ids the pages are reached by."""
    assert "error" not in four_weeks(user_id)
    assert "error" not in services.save_routine(user_id, {"name": "Push day", "lifts": [
        {"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 62.5},
        {"exercise": "Dip", "sets": 3, "reps": 10}]}, "claude")
    day = services.get_log(user_id, DAY, 1)["days"][0]
    return {"weigh_in": day["weigh_in"]["id"], "measurement": day["measurements"][0]["id"],
            "run": services.get_log(user_id, RUN_DAY, 1)["days"][0]["cardio"][0]["id"],
            "session": services.get_log(user_id, LIFT_DAY, 1)["days"][0]["sessions"][0]["id"]}


@pytest.fixture
def lb_mi(pinned):
    """The four weeks on a lb and mi account, on the fixed day."""
    uid = only_user().id
    ids = stock(uid)
    set_units(uid, "lb", "mi")
    return {"client": pinned, "uid": uid, **ids}


def addresses(ids: dict) -> dict[str, str]:
    return {"today": f"/day/{DAY}", "run day": f"/day/{RUN_DAY}", "lift day": f"/day/{LIFT_DAY}",
            "progress": "/progress", "squat": "/progress/back-squat", "pull-up": "/progress/pull-up",
            "goals": "/goals", "lift goal form": "/goals?kind=lift", "run goal form": "/goals?kind=cardio_best",
            "routines": "/routines", "weigh-in edit": f"/entries/bodyweight/{ids['weigh_in']}/edit",
            "run edit": f"/entries/cardio_entry/{ids['run']}/edit",
            "session edit": f"/entries/lift_session/{ids['session']}/edit"}


def visible(markup: str) -> str:
    """The words a person reads on a page, its charts' titles and descs among them."""
    return "\n".join(p.text for p in tr.html_text(markup))


def svg_text(markup: str) -> str:
    return "\n".join(re.findall(r"<svg.*?</svg>", markup, re.S))


def form_values(page: str, action_part: str) -> dict[str, str]:
    """The fields of the form whose action holds action_part, as the page drew them: what a person sends when they
    change nothing. The csrf token is post's own."""
    [form] = [f for f in parse(page).find_all("form") if action_part in (f.attrs.get("action") or "")]
    out = {}
    for c in form.iter():
        name = c.attrs.get("name")
        if not name or name == "csrf_token":
            continue
        if c.tag == "input" and c.attrs.get("type") not in ("radio", "checkbox"):
            out[name] = c.attrs.get("value") or ""
        elif c.tag == "select":
            out[name] = next((o.attrs.get("value") for o in c.find_all("option") if "selected" in o.attrs), "")
        elif c.tag == "textarea":
            out[name] = "".join(t for t in c.children if isinstance(t, str))
    return out


# ---------------------------------------------------------------- every page, in lb and mi

def test_every_page_of_a_lb_and_mi_account_says_lb_and_mi_and_never_kg_or_km(lb_mi):
    """The day pages, Progress, a lift's page, Goals with each form, Routines and the edit pages: no kg or km in
    the words a person reads or in a chart, and each in the units in use. Every page keeps the page rules."""
    client, uid = lb_mi["client"], lb_mi["uid"]
    entered, names, notes = entered_text(uid), names_of(uid), entered_notes(uid)
    pages = {}
    for name, path in addresses(lb_mi).items():
        r = client.get(path)
        assert r.status_code == 200, name
        pages[name] = r.text
        assert not KG_OR_KM.search(visible(r.text)), (name, KG_OR_KM.findall(visible(r.text)))
        assert not KG_OR_KM.search(svg_text(r.text)), name
        assert page_findings(path, r.text, entered, uid) == [], name
        assert step_4_findings(path, r.text, names, notes) == [], name
    nb = " "
    today, run_day, lift_day = (visible(pages[k]) for k in ("today", "run day", "lift day"))
    assert "lb trend" in today and f"lb a week" in today and "Weight (lb)" in today
    assert "Distance (mi)" in run_day and "Pace (per mi)" in run_day and "\n3.11\n" in run_day
    assert "Weight (lb)" in lift_day
    sets = services.get_log(uid, LIFT_DAY, 1)["days"][0]["sessions"][0]["sets"]
    heavy = next(s for s in sets if s["weight_kg"])
    cell = formatting.show(heavy["weight_kg"], "weight_kg", unit=False, units=LB_MI)
    assert cell != formatting.show(heavy["weight_kg"], "weight_kg", unit=False)
    for page in (pages["lift day"], pages["session edit"]):  # each set's weight in lb under its lb header
        assert f'<td class="num">{cell}</td>' in page
    progress = visible(pages["progress"])
    assert "Fastest pace (per mi)" in progress and f"Goal 169.75{nb}lb" in progress
    # The Outdoor Run's fastest, 5 km in 29.5 minutes (5:54 per km), per mile: the activity's own read's number.
    assert re.search(r"\nOutdoor Run\n\d+\n[\d,]+\n9:30\n", progress)
    assert f"lb, estimated max now" in visible(pages["squat"])
    goals = visible(pages["goals"])
    for label in ("Body weight 169.75 lb", "Back Squat 264.55 lb", "Outdoor Run 3.11 mi in 27:00"):
        assert label in goals, label
    assert "now, the best 3.11 mi time in 28 days" in goals
    # routines-and-programs.md C5a: a card a routine, each lift's Item cell, then its Plan cell
    assert (f'<td class="lead">Bench Press</td><td class="wide-cell" data-label="Plan">3 × 8 at 137.8{nb}lb</td>'
            in pages["routines"])
    assert '<td class="lead">Dip</td><td class="wide-cell" data-label="Plan">3 × 10</td>' in pages["routines"]
    assert "Speed (mph)" in visible(pages["run edit"]) and "Weight (lb)" in visible(pages["weigh-in edit"])


@pytest.mark.parametrize("weight, distance", [("lb", "km"), ("kg", "mi")])
def test_each_setting_follows_on_its_own(lb_mi, weight, distance):
    """A Canadian lifts in lb and runs in km; a Briton runs in miles and lifts in kg: each word, field and form
    follows its own setting."""
    client, uid = lb_mi["client"], lb_mi["uid"]
    set_units(uid, weight, distance)
    lift_day, run_day = client.get(f"/day/{LIFT_DAY}").text, client.get(f"/day/{RUN_DAY}").text
    assert f"Weight ({weight})" in visible(lift_day)
    assert f"Distance ({distance})" in visible(run_day) and f"Pace (per {distance})" in visible(run_day)
    weigh = form_values(client.get(f"/entries/bodyweight/{lb_mi['weigh_in']}/edit").text, "/edit")
    run = form_values(client.get(f"/entries/cardio_entry/{lb_mi['run']}/edit").text, "/edit")
    assert (weight == "lb") == ("lb" in weigh) == ("kg" not in weigh)
    assert (distance == "mi") == ({"distance_mi", "speed_mph"} <= set(run)) == ("distance_km" not in run)
    goal_form = form_parts(client.get("/goals?kind=cardio_best").text)
    assert (("weight_unit", "lb") in goal_form["hidden"]) == (weight == "lb")
    assert (("Distance (mi)", "input", "distance_mi") in goal_form["fields"]) == (distance == "mi")


def test_a_kg_and_km_accounts_pages_are_the_same_never_chosen_or_chosen(pinned):
    """kg and km chosen read as kg and km never chosen, page by page, byte for byte but each form's fresh key."""
    uid = only_user().id
    ids = stock(uid)

    def masked(text: str) -> str:
        return re.sub(r'name="(csrf_token|key)" value="[^"]*"', r'name="\1" value=""', text)

    paths = [*addresses(ids).values(), "/settings", "/goals?kind=measurement"]
    never = [masked(pinned.get(p).text) for p in paths]
    set_units(uid, "kg", "km")
    assert [masked(pinned.get(p).text) for p in paths] == never
    goal_form = form_parts(pinned.get("/goals?kind=lift").text)
    assert [n for n, _ in goal_form["hidden"]] == ["csrf_token", "kind", "key"]  # no weight_unit on a kg form


# ---------------------------------------------------------------- the day page's forms

def test_the_weigh_in_form_on_a_lb_account_saves_185_as_83_91_kg(pinned):
    uid = only_user().id
    set_units(uid, "lb", None)
    page = pinned.get(f"/day/{DAY}").text
    assert form_values(page, "/weigh-in") == {"lb": "", "key": form_values(page, "/weigh-in")["key"]}
    assert 'for="lb">Weight (lb)</label>' in page
    r = post(pinned, f"/day/{DAY}/weigh-in", lb="185")
    assert r.status_code == 303, r.text[:300]
    [row] = rows(Bodyweight, uid)
    assert row.kg == 83.91
    shown = visible(pinned.get(f"/day/{DAY}").text)
    assert "Weigh-in 185 lb" in shown and "\n185 lb\n" in shown  # the chip, and the Weigh-in card


LB_LINE = "That weight was not saved: give it in lb, from 44.1 to 881.84."


@pytest.mark.parametrize("sent", ["900", "", "lots", "44.09"])
def test_a_refused_weigh_in_in_lb_says_lb_under_its_own_field(pinned, sent):
    uid = only_user().id
    set_units(uid, "lb", None)
    r = post(pinned, f"/day/{DAY}/weigh-in", lb=sent)
    assert r.status_code == 400 and rows(Bodyweight, uid) == []
    assert f'<p class="error" id="lb-error">{LB_LINE}</p>' in r.text
    assert re.search(r'<input id="lb" name="lb"' + (f' value="{sent}"' if sent else ""), r.text)
    assert "kg" not in visible(r.text).replace("kcal", "")


def test_the_cardio_form_on_a_mi_account_saves_3_mi_as_4_83_km_and_its_pace_is_the_summarys(pinned):
    uid = only_user().id
    set_units(uid, None, "mi")
    r = post(pinned, f"/day/{DAY}/cardio", exercise="Outdoor Run", minutes="27", distance_mi="3")
    assert r.status_code == 303, r.text[:300]
    [row] = rows(CardioEntry, uid)
    assert row.distance_km == 4.83
    with db.session_scope() as s:
        entry = s.get(CardioEntry, row.id)
        summary = cardio.summary(entry, entry.date, users.units_for(s.get(User, uid)))
    assert "3 mi, pace 9:00 per mi" in summary
    card = visible(pinned.get(f"/day/{DAY}").text)
    assert "Distance (mi)\n3\nPace (per mi)\n9:00" in card


@pytest.mark.parametrize("sent", ["900", "lots"])
def test_a_refused_distance_in_miles_says_miles_under_its_own_field(pinned, sent):
    uid = only_user().id
    set_units(uid, None, "mi")
    r = post(pinned, f"/day/{DAY}/cardio", exercise="Outdoor Run", minutes="27", distance_mi=sent)
    assert r.status_code == 400 and rows(CardioEntry, uid) == []
    assert ('<p class="error" id="cardio-distance-error">Give the distance in miles, from 0.01 to 186.41, or leave it '
            'empty.</p>') in r.text
    assert f'name="distance_mi" value="{sent}"' in r.text


# ---------------------------------------------------------------- the edit page (decision 16)

def versions(user_id: int) -> int:
    return len(rows(EntryVersion, user_id))


def test_an_untouched_edit_on_a_lb_and_mi_account_changes_nothing_and_keeps_no_version(lb_mi):
    """80 kg fills as 176.35 lb, which is 79.99 kg sent back: posted as drawn, nothing is sent to the service, no
    version is kept and the stored number stays. The same for a run's distance and speed in miles."""
    client, uid = lb_mi["client"], lb_mi["uid"]
    assert "error" not in services.edit_entry(uid, "bodyweight", lb_mi["weigh_in"], {"kg": 80}, "claude")
    before, kept = versions(uid), len(calls(uid))
    for kind, entry_id in (("bodyweight", lb_mi["weigh_in"]), ("cardio_entry", lb_mi["run"])):
        address = f"/entries/{kind}/{entry_id}/edit"
        drawn = form_values(client.get(address).text, "/edit")
        if kind == "bodyweight":
            assert drawn["lb"] == "176.35" and "kg" not in drawn
        else:
            assert (drawn["distance_mi"], drawn["speed_mph"]) == ("3.11", "6.3") and "distance_km" not in drawn
        r = post(client, address, **drawn)
        assert r.status_code == 303 and "saved=unchanged" in r.headers["location"], kind
    assert versions(uid) == before and len(calls(uid)) == kept  # no write reached the service
    [weigh] = [b for b in rows(Bodyweight, uid) if b.id == lb_mi["weigh_in"]]
    [run] = [c for c in rows(CardioEntry, uid) if c.id == lb_mi["run"]]
    assert weigh.kg == 80 and run.distance_km == 5


def test_a_changed_weight_and_distance_on_the_edit_page_are_saved_from_lb_and_miles(lb_mi):
    client, uid = lb_mi["client"], lb_mi["uid"]
    address = f"/entries/bodyweight/{lb_mi['weigh_in']}/edit"
    r = post(client, address, **{**form_values(client.get(address).text, "/edit"), "lb": "184"})
    assert r.status_code == 303 and "saved=bodyweight" in r.headers["location"]
    [weigh] = [b for b in rows(Bodyweight, uid) if b.id == lb_mi["weigh_in"]]
    assert weigh.kg == 83.46  # 184 lb at 2 dp of kg
    address = f"/entries/cardio_entry/{lb_mi['run']}/edit"
    drawn = form_values(client.get(address).text, "/edit")
    r = post(client, address, **{**drawn, "distance_mi": "3.5", "speed_mph": ""})
    assert r.status_code == 303, r.text[:400]
    [run] = [c for c in rows(CardioEntry, uid) if c.id == lb_mi["run"]]
    assert run.distance_km == 5.63  # 3.5 mi at 2 dp of km
    assert versions(uid) == 2


def test_a_refused_edit_in_lb_and_miles_says_its_unit_under_its_field(lb_mi):
    client = lb_mi["client"]
    address = f"/entries/bodyweight/{lb_mi['weigh_in']}/edit"
    r = post(client, address, **{**form_values(client.get(address).text, "/edit"), "lb": "900"})
    assert r.status_code == 400 and f'<p class="error" id="edit-lb-error">{LB_LINE}</p>' in r.text
    address = f"/entries/cardio_entry/{lb_mi['run']}/edit"
    r = post(client, address, **{**form_values(client.get(address).text, "/edit"), "speed_mph": "99"})
    assert r.status_code == 400
    assert ('<p class="error" id="edit-speed_mph-error">Give the speed in mph, from 0.1 to 37.2, or leave it '
            'empty.</p>') in r.text
    assert 'name="speed_mph" value="99"' in r.text


# ---------------------------------------------------------------- the goal form

@pytest.mark.parametrize("data, target, start, label", [
    ({"kind": "bodyweight", "target_value": "175", "start_value": "185"}, 79.38, 83.91, "Body weight 175 lb"),
    ({"kind": "lift", "exercise": "Back Squat", "target_value": "315", "start_value": "225"}, 142.88, 102.06,
     "Back Squat 315 lb"),
], ids=["body weight", "lift"])
def test_the_goal_form_on_a_lb_account_sets_a_lb_target(pinned, data, target, start, label):
    """The form carries weight_unit lb and says lb; its target and start are read in lb and kept in kg."""
    uid = only_user().id
    set_units(uid, "lb", None)
    form = form_parts(pinned.get(f"/goals?kind={data['kind']}").text)
    assert ("weight_unit", "lb") in form["hidden"]
    assert form["fields"][-3][0] in ("Target weight (lb)", "Target estimated max (lb)")
    r = post(pinned, "/goals", **data, weight_unit="lb", target_date=BY)
    assert r.status_code == 303, r.text[:400]
    [goal] = rows(Goal, uid)
    assert (goal.target_value, goal.start_value, goal.unit) == (target, start, "kg")
    assert f"<h2><strong>{label}</strong></h2>" in pinned.get("/goals").text


GOAL_LB_ROWS = [
    ({"kind": "bodyweight", "target_value": "900"}, "target_value",
     "Give the weight you aim for in lb, from 44.1 to 881.84."),
    ({"kind": "bodyweight", "target_value": ""}, "target_value", "Give the weight you aim for, in lb."),
    ({"kind": "lift", "exercise": "Back Squat", "target_value": "lots"}, "target_value",
     "Give the estimated max you aim for in lb, more than 0 and at most 1,469.75."),
    ({"kind": "lift", "exercise": "Back Squat", "target_value": "", "start_value": "225"}, "target_value",
     "Give the estimated max you aim for, in lb."),
    ({"kind": "bodyweight", "target_value": "175", "start_value": "eighty"}, "start_value",
     "Give your weight now in lb, from 44.1 to 881.84."),
    ({"kind": "lift", "exercise": "Back Squat", "target_value": "315", "start_value": "0"}, "start_value",
     "Give your estimated max now in lb, more than 0 and at most 1,469.75."),
]


@pytest.mark.parametrize("data, field, line", GOAL_LB_ROWS, ids=[f"{r[0]['kind']} {r[1]} {r[0][r[1]] or 'empty'}"
                                                                  for r in GOAL_LB_ROWS])
def test_a_refused_goal_in_lb_says_lb_under_its_field(pinned, data, field, line):
    uid = only_user().id
    set_units(uid, "lb", None)
    r = post(pinned, "/goals", **data, weight_unit="lb", target_date=BY)
    assert r.status_code == 400 and rows(Goal, uid) == []
    form = form_parts(r.text)
    assert form["errors"][field] == line and form["top"] == []
    assert ("weight_unit", "lb") in form["hidden"]


@pytest.mark.parametrize("sent, line", [("900", "Give the distance in miles, from 0.07 to 186.41."),
                                        ("", "Give the distance the time is for, in miles.")], ids=["far", "empty"])
def test_a_refused_goal_distance_in_miles_says_miles_under_the_miles_field(pinned, sent, line):
    uid = only_user().id
    set_units(uid, None, "mi")
    r = post(pinned, "/goals", kind="cardio_best", exercise="Outdoor Run", distance_mi=sent, target_value="27",
             start_value="30", target_date=BY)
    assert r.status_code == 400 and rows(Goal, uid) == []
    form = form_parts(r.text)
    assert form["errors"]["distance_mi"] == line and form["top"] == [] and form["invalid"] == {"distance_mi"}


def test_a_goal_distance_in_miles_is_kept_in_km_and_its_label_says_miles(pinned):
    uid = only_user().id
    set_units(uid, None, "mi")
    r = post(pinned, "/goals", kind="cardio_best", exercise="Outdoor Run", distance_mi="3.1", target_value="27",
             start_value="30", target_date=BY)
    assert r.status_code == 303, r.text[:400]
    [goal] = rows(Goal, uid)
    assert goal.distance_km == 4.99
    assert "<h2><strong>Outdoor Run 3.1 mi in 27:00</strong></h2>" in pinned.get("/goals").text


def test_the_goals_page_says_the_services_own_label_and_plan_line_in_lb(lb_mi):
    """goal_charts words its goals in the units the page hands it, by the service's own label and plan_line, so
    each card and its chart say what get_goals says. A waist goal keeps its cm on a lb account."""
    client, uid = lb_mi["client"], lb_mi["uid"]
    assert "error" not in services.set_goal(uid, {"kind": "measurement", "measurement_kind": "waist",
                                                  "target_value": 80, "target_date": BY}, "claude")
    page = client.get("/goals").text
    listed = services.get_goals(uid)["goals"]
    assert len(listed) == 4
    for g in listed:
        card = re.search(rf'<li class="goal" id="goal-{g["goal_id"]}">(.*?)</li>\s*(?=<li class="goal"|</ul>)',
                         page, re.S).group(1)
        assert f"<h2><strong>{g['label']}</strong></h2>" in card
        assert f'<span class="means">{g["plan_line"]}</span>' in card
        assert f"{g['plan_line']}" in re.search(r"<desc[^>]*>(.*?)</desc>", card, re.S).group(1)
    waist = next(g for g in listed if g["kind"] == "measurement")
    assert waist["label"] == "Waist 80 cm" and '<span class="unit">cm</span>' in page


# ---------------------------------------------------------------- the Units card (R16)

def test_the_units_card_saves_both_settings_and_its_next_render_shows_lb(pinned):
    uid = only_user().id
    page = pinned.get("/settings").text
    drawn = form_values(page, "#units")
    assert drawn == {"card": "units", "key": drawn["key"]}
    checked = re.findall(r'name="(weight_unit|distance_unit)" value="(\w+)" checked', page)
    assert checked == [("weight_unit", "kg"), ("distance_unit", "km")]  # the units in use while none is chosen
    r = post(pinned, "/settings", card="units", weight_unit="lb", distance_unit="mi")
    assert r.headers["location"] == SITE + "/settings?saved=units#units"
    assert stored(uid) == ("lb", "mi") and calls(uid)[-1] == ("update_settings", True, None)
    page = pinned.get(r.headers["location"]).text
    card = re.search(r'<section class="card" id="units".*?</section>', page, re.S).group(0)
    assert '<p class="ok">Units saved.</p>' in card and page.count('class="ok') == 1
    assert re.findall(r'name="(weight_unit|distance_unit)" value="(\w+)" checked', page) == [
        ("weight_unit", "lb"), ("distance_unit", "mi")]
    assert "Weight (lb)" in visible(pinned.get(f"/day/{DAY}").text)
    r = post(pinned, "/settings", card="units", weight_unit="lb", distance_unit="mi")
    assert r.headers["location"] == SITE + "/settings?saved=unchanged#units"


def test_saving_the_units_never_chosen_records_kg_and_km(pinned):
    """Saving kg and km ends units_note as surely as lb and mi (decision 12)."""
    uid = only_user().id
    assert stored(uid) == (None, None)
    r = post(pinned, "/settings", card="units", weight_unit="kg", distance_unit="km")
    assert r.headers["location"] == SITE + "/settings?saved=units#units" and stored(uid) == ("kg", "km")


def test_a_refused_units_post_keeps_the_units_and_says_the_cards_line(pinned):
    uid = only_user().id
    r = post(pinned, "/settings", card="units", weight_unit="stone", distance_unit="mi")
    assert r.status_code == 400 and stored(uid) == (None, None)
    card = re.search(r'<section class="card" id="units".*?</section>', r.text, re.S).group(0)
    assert f'<p class="error" id="units-error">{web.UNITS_ERROR_GENERAL}</p>' in card
    assert "stone" not in visible(r.text)


# ---------------------------------------------------------------- the charts (R17)

def test_each_chart_in_lb_and_mi_places_and_labels_its_figures_in_them():
    today = date.fromisoformat(DAY)
    lift = {"lift": "Bench Press", "points": [{"date": "2026-11-02", "value": 102.06},
                                              {"date": "2026-11-09", "value": 104.33}],
            "latest": {"date": "2026-11-09", "value": 104.33},
            "goal": {"target_kg": 100, "target_date": BY, "status": "behind", "projected": None}}
    weight = {"points": [{"date": "2026-11-09", "kg": 83.91, "smoothed": 84.0},
                         {"date": "2026-11-10", "kg": 84.37, "smoothed": 84.1}],
              "latest": {"date": "2026-11-10", "kg": 84.37}, "goal": {"target_kg": 79.38, "target_date": BY}}
    paces = [{"date": "2026-11-07", "pace_min_per_km": 27 / 4.83}, {"date": "2026-10-31", "pace_min_per_km": 6.0}]
    lift_svg = str(charts.chart_svg(lift, today=today, units=LB_MI))
    assert "Latest 230 lb" in lift_svg and "Goal 220.45 lb" in lift_svg and " lb</text>" in lift_svg
    weight_svg = str(charts.bodyweight_svg(weight, today=today, units=LB_MI))
    assert "Latest 186 lb" in weight_svg and "Goal 175 lb" in weight_svg and ": 185 lb</title>" in weight_svg
    pace = str(charts.pace_svg(paces, "Outdoor Run", today, units=LB_MI))
    assert "Pace per mi: Outdoor Run" in pace and "9:00 per mi" in pace
    for svg in (lift_svg, weight_svg, pace):
        assert not KG_OR_KM.search(svg)
    # The same charts with no units, and in kg and km, are the charts as they were: one drawing.
    assert charts.chart_svg(lift, today=today) == charts.chart_svg(lift, today=today, units=formatting.KG_KM)
    assert "Goal 100 kg" in str(charts.chart_svg(lift, today=today))
    assert charts.pace_svg(paces, "Outdoor Run", today) == charts.pace_svg(paces, "Outdoor Run", today,
                                                                           units=formatting.KG_KM)


def test_a_lb_chart_is_the_kg_chart_scaled_and_its_ticks_come_from_the_same_steps():
    """Every point is placed in lb by formatting.convert, never by a figure rounded first; the tick labels name lb on
    the top tick only."""
    weight = {"points": [{"date": "2026-11-01", "kg": 80.0, "smoothed": None},
                         {"date": "2026-11-10", "kg": 90.0, "smoothed": None}],
              "latest": {"date": "2026-11-10", "kg": 90.0}, "goal": None}
    svg = str(charts.bodyweight_svg(weight, today=date.fromisoformat(DAY), units=LB_MI))
    ticks = re.findall(r'<text class="axis end"[^>]*>([^<]*)</text>', svg)
    assert ticks == ["170", "180", "190", "200 lb"]  # 176.37 to 198.42 lb: WHOLE_STEPS' 10, at most 5 ticks


def test_a_goal_chart_draws_a_weight_in_lb_and_a_waist_in_cm():
    plan = {"plan": [{"date": "2026-10-14", "value": 80.0}, {"date": BY, "value": 77.0}], "actual": [
        {"date": "2026-10-14", "value": 80.0}, {"date": DAY, "value": 79.0}]}
    weight = {"goal_id": 1, "label": "Body weight 169.75 lb", "kind": "bodyweight", "unit": "kg",
              "start_date": "2026-10-14", "start_value": 80.0, "target_date": BY, "target_value": 77.0,
              "latest": 79.0, "needed_per_week": -0.2, "actual_per_week": -0.25, "projected_date": None,
              "projection": None, "plan_line": "PLAN", "path": plan}
    svg = str(charts.goal_svg(weight, date.fromisoformat(DAY), units=LB_MI))
    assert "Started 176.4 lb" in svg and "Target 169.75 lb" in svg and "Needed -0.44 lb a week" in svg
    assert not KG_OR_KM.search(svg)
    waist = {**weight, "kind": "measurement", "unit": "cm", "label": "Waist 77 cm"}
    svg = str(charts.goal_svg(waist, date.fromisoformat(DAY), units=LB_MI))
    assert "Target 77 cm" in svg and "Needed -0.2 cm a week" in svg and "lb" not in svg
    assert svg == str(charts.goal_svg(waist, date.fromisoformat(DAY)))


# ---------------------------------------------------------------- the close calls and the lines (decision 10)

def test_a_best_that_reads_the_same_in_lb_widens_with_the_one_it_beat():
    """102.06 and 102.05 kg are both 225 lb. In kg a heaviest weight never widens: it is shown at its stored 2 dp."""
    heaviest = {"kind": "weight", "value": 102.06, "previous": 102.05}
    assert web.best_widened(heaviest, LB_MI) and not web.best_widened(heaviest)
    assert formatting.show(102.06, "weight_kg", widened=True, units=LB_MI) != \
        formatting.show(102.05, "weight_kg", widened=True, units=LB_MI)
    # The kinds since exercise-list.md's bests table (C4e): reps and a hold's seconds are whole, so never widen.
    assert set(lifts.BEST_LABELS) == {"estimated_max", "weight", "least_help", "reps", "time", "carry"}
    assert not web.best_widened({"kind": "reps", "value": 12, "previous": 11}, LB_MI)
    assert web.best_widened({"kind": "least_help", "value": -20.01, "previous": -20.0}, LB_MI)


def test_every_line_the_lb_and_mi_forms_can_draw_keeps_the_text_rules():
    lines = [*web.FIELD_HELP_IN_UNITS.values(), web.UNITS_ERROR_GENERAL, web.GOAL_ERROR_WORDS[("invalid_field",
                                                                                              "distance_mi")]]
    for table in web.GOAL_ERROR_WORDS_IN.values():
        for words in table.values():
            lines += list(words.values()) if isinstance(words, dict) else [words]
    lines.append("Your pages and your assistant show weights and distances in these units. Switching changes no "
                 "entry.")
    for line in lines:
        assert tr.check(line, tr.PAGE) == [], line
        assert all(len(s.split()) < 25 for s in re.split(r"(?<=[.:])\s", line)), line
    # Each range is the service's own, moved inward into that unit: never typed.
    assert web.FIELD_HELP_IN_UNITS[("bodyweight", "lb")].endswith("from {} to {}.".format(
        *rules.inward(rules.BODYWEIGHT_KG, formatting.LB, "weigh_in")))


# ---------------------------------------------------------------- what stays as it is on a lb and mi account

def test_the_landing_and_the_hi_page_are_the_same_on_a_lb_and_mi_account(pinned):
    """Their examples are fixed, the same for every reader (the contract's known limits): kg on every account."""
    uid = only_user().id

    def masked(text: str) -> str:
        return re.sub(r'name="(csrf_token|key)" value="[^"]*"', r'name="\1" value=""', text)

    kg = [masked(pinned.get(p).text) for p in ("/", "/hi")]
    set_units(uid, "lb", "mi")
    assert [masked(pinned.get(p).text) for p in ("/", "/hi")] == kg


@pytest.mark.parametrize("data, field, value", [
    ({"kind": "measurement", "measurement_kind": "waist", "target_value": "80", "start_value": "88"}, "target_value",
     80),
    ({"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": "5", "target_value": "27",
      "start_value": "30"}, "target_value", 27),
], ids=["waist", "cardio time"])
def test_a_waist_and_a_cardio_time_goal_from_a_lb_form_keep_their_own_units(pinned, data, field, value):
    """The lb form's hidden weight_unit rides along on every kind: a cm or a minutes target is kept as typed."""
    uid = only_user().id
    set_units(uid, "lb", None)
    assert ("weight_unit", "lb") in form_parts(pinned.get(f"/goals?kind={data['kind']}").text)["hidden"]
    r = post(pinned, "/goals", **data, weight_unit="lb", target_date=BY)
    assert r.status_code == 303, r.text[:400]
    [goal] = rows(Goal, uid)
    assert getattr(goal, field) == value and goal.unit in ("cm", "min")


def test_the_now_figure_widens_with_plan_line_when_they_read_the_same_in_lb():
    """79.04 and 79.08 kg read 79 and 79.1 kg, and both read 174.3 lb, 0.09 lb apart, a gap that does not round to 0:
    in lb the now figure widens, as plan_line does (decision 10); in kg it does not."""
    goal = {"kind": "bodyweight", "unit": "kg", "latest": 79.04, "should_be_today": 79.08}
    assert web.now_widened(goal, LB_MI) and not web.now_widened(goal)
    assert formatting.show(79.04, "bodyweight_derived", units=LB_MI) == \
        formatting.show(79.08, "bodyweight_derived", units=LB_MI) == "174.3 lb"


def test_the_lb_and_mi_refusal_renders_keep_the_page_rules(pinned):
    """Each form's refusal, drawn in lb or miles, and the Units card's: the page and text rules find nothing."""
    uid = only_user().id
    set_units(uid, "lb", "mi")
    entered, names, notes = entered_text(uid), names_of(uid), entered_notes(uid)
    renders = [("/day/{date}", post(pinned, f"/day/{DAY}/weigh-in", lb="900")),
               ("/day/{date}", post(pinned, f"/day/{DAY}/cardio", exercise="Outdoor Run", minutes="27",
                                    distance_mi="900")),
               ("/goals", post(pinned, "/goals", kind="bodyweight", target_value="900", weight_unit="lb",
                               target_date=BY)),
               ("/goals", post(pinned, "/goals", kind="cardio_best", exercise="Outdoor Run", distance_mi="",
                               target_value="27", target_date=BY)),
               ("/settings", post(pinned, "/settings", card="units", weight_unit="stone", distance_unit="mi"))]
    for route, r in renders:
        assert r.status_code == 400, route
        address = route.replace("{date}", DAY)
        assert page_findings(address, r.text, entered, uid, route=route) == [], route
        assert step_4_findings(address, r.text, names, notes, route=route) == [], route
        assert not KG_OR_KM.search(visible(r.text).split("Units", 1)[0] if route == "/settings" else visible(r.text))


def _ticks(svg: str) -> list[float]:
    """A chart's grid labels as numbers in their own unit: m:ss as seconds, a figure with its comma dropped."""
    out = []
    for label in re.findall(r'<text class="axis end"[^>]*>([^<]*)</text>', svg):
        figure = label.split()[0]
        if ":" in figure:
            minutes, seconds = figure.split(":")
            out.append(int(minutes) * 60 + int(seconds))
        else:
            out.append(float(figure.replace(",", "")))
    return out


def test_each_chart_places_its_points_in_lb_and_mi_and_its_grid_spans_them():
    """Every chart that draws a weight or a pace places it by formatting.convert: its grid spans the figures in lb
    or per mi, never the kg or per km ones."""
    today = date.fromisoformat(DAY)
    lift = {"lift": "Bench Press", "points": [{"date": "2026-11-02", "value": 102.06},
                                              {"date": "2026-11-09", "value": 104.33}],
            "latest": {"date": "2026-11-09", "value": 104.33}, "goal": None}
    ticks = _ticks(str(charts.chart_svg(lift, today=today, units=LB_MI)))
    assert min(ticks) <= 225 and max(ticks) >= 230.01 and max(ticks) < 300  # 102.06 and 104.33 kg in lb
    series = [{"date": "2026-11-09", "kg": 83.91, "smoothed": 84.0}, {"date": "2026-11-10", "kg": 84.37,
                                                                     "smoothed": 84.1}]
    ticks = _ticks(str(charts.weight_sheet_svg(series, today, units=LB_MI)))
    assert len(ticks) == 3 and min(ticks) <= 185.19 and max(ticks) >= 185.41 and max(ticks) < 200
    small = str(charts.weight_small_svg(series, today, units=LB_MI))
    assert small != str(charts.weight_small_svg(series, today)) and "185.2 lb" in small
    plan = {"plan": [{"date": "2026-10-14", "value": 80.0}, {"date": BY, "value": 77.0}],
            "actual": [{"date": "2026-10-14", "value": 80.0}, {"date": DAY, "value": 79.0}]}
    goal = {"goal_id": 1, "label": "Body weight 169.75 lb", "kind": "bodyweight", "unit": "kg",
            "start_date": "2026-10-14", "start_value": 80.0, "target_date": BY, "target_value": 77.0, "latest": 79.0,
            "needed_per_week": -0.2, "actual_per_week": None, "projected_date": None, "projection": None,
            "plan_line": "PLAN", "path": plan}
    ticks = _ticks(str(charts.goal_svg(goal, today, units=LB_MI)))
    assert min(ticks) <= 169.76 and max(ticks) >= 176.37 and max(ticks) < 200  # 77 and 80 kg in lb
    paces = [{"date": "2026-11-07", "pace_min_per_km": 27 / 4.83}, {"date": "2026-10-31", "pace_min_per_km": 6.0}]
    ticks = _ticks(str(charts.pace_svg(paces, "Outdoor Run", today, units=LB_MI)))
    assert min(ticks) <= 540 and max(ticks) >= 579  # 9:00 and 9:39 per mi: 5:35 and 6:00 per km


# ---------------------------------------------------------------- a page says the summary's figure (the U2 review)

def _plain(markup: str) -> str:
    return visible(markup).replace(" ", " ")


def trends_said(user_id: int) -> tuple[str, str, str, str]:
    """get_trends' summary's body weight now, smoothed and rate a week, and the unit it says them in."""
    summary = services.get_trends(user_id)["summary"]
    found = re.match(r"Body weight (\S+) (lb|kg) now, smoothed (\S+) \2, (\S+) \2 a week\.", summary)
    assert found, summary
    now, unit, smoothed, rate = found.groups()
    return now, smoothed, rate, unit


def lifts_said(user_id: int) -> dict[str, tuple[str, str | None]]:
    """{lift: (estimated max now, trend a week)} as get_progress' summaries say them: the every-lift summary's
    estimated max, which each one-lift summary says too, and the one-lift summary's trend."""
    out = {}
    for line in services.get_progress(user_id)["summary"].splitlines():
        found = re.match(r"(.+): estimated max now (\S+) (?:lb|kg), ", line)
        if found:
            name, most = found.groups()
            one = services.get_progress(user_id, name)["summary"]
            assert f"{name}: estimated max now {most} " in one
            trend = re.search(r" Trend (\S+) (?:lb|kg) a week over", one)
            out[name] = (most, trend.group(1) if trend else None)
    return out


@pytest.mark.parametrize("weight, distance", [("lb", "mi"), ("lb", "km"), ("kg", "mi")])
def test_every_body_weight_figure_on_today_and_progress_is_the_trends_summarys(pinned, weight, distance):
    """The four weeks, whose smoothed weight is 174.7 lb and its rate -0.54 lb a week in the summary: the tiles,
    the Weight sheet, its chart and the small chart, Progress' Body weight card and its chart, and the Trends table
    each say the summary's figure, never the kg result's figure shown again in lb (174.6 and -0.53)."""
    uid = only_user().id
    stock(uid)
    set_units(uid, weight, distance)
    now, smoothed, rate, w = trends_said(uid)
    if (weight, distance) == ("lb", "mi"):
        assert (now, smoothed, rate) == ("174.2", "174.7", "-0.54")
    tile = f"Weight: {smoothed} {w} trend {rate} {w} a week"
    sheet = [f"The trend moved {rate} {w} a week from", f"The 7-day average is {now} {w},",
             f"Smoothed trend\n{smoothed} {w}\nEvens out day-to-day swings\n7-day average\n{now} {w}\nLast 7 days\n"
             f"Rate\n{rate} {w} a week\n", f"to {smoothed} {w} on Tue 10 Nov."]
    today = _plain(pinned.get(f"/day/{DAY}").text)
    for words in [tile, *sheet, f"Trend, 4 weeks\n{smoothed} {w}\nWeight, last 4 weeks\n"]:
        assert words in today, words
    assert today.count(f"to {smoothed} {w} on Tue 10 Nov.") == 2  # the sheet's chart and the small chart
    progress = _plain(pinned.get("/progress").text)
    for words in [tile, *sheet, f"The line is the smoothed trend, now {smoothed} {w}.",
                  f"Trend\n{smoothed} {w} Smoothed",
                  f"Smoothed trend\n{smoothed} {w}\n7-day average\n{now} {w}\nWeight rate\n{rate} {w} a week\n"]:
        assert words in progress, words


@pytest.mark.parametrize("weight, distance", [("lb", "mi"), ("lb", "km"), ("kg", "mi")])
def test_every_lift_figure_on_progress_and_a_lifts_page_is_the_progress_summarys(pinned, weight, distance):
    """The four weeks' Lifts table, whose estimated maxes read 174.5, 321.5 and 135 lb in the summary, says each
    lift's estimated max and trend as get_progress' summaries say them; and the Back Squat page's tile, chart card
    and chart desc say its estimated max now."""
    uid = only_user().id
    stock(uid)
    set_units(uid, weight, distance)
    said = lifts_said(uid)
    w = "lb" if weight == "lb" else "kg"
    if (weight, distance) == ("lb", "mi"):
        assert [said[n][0] for n in ("Barbell Row", "Deadlift", "Overhead Press")] == ["174.5", "321.5", "135"]
    progress = _plain(pinned.get("/progress").text)
    for name, (most, trend) in said.items():
        assert re.search(rf"\n{re.escape(name)}\n{re.escape(most)} {w}\n[^\n]+\n{re.escape(trend)} {w} a week\n",
                         progress), (name, most, trend)
    squat = _plain(pinned.get("/progress/back-squat").text)
    most = said["Back Squat"][0]
    for words in [f"Estimated max now: {most} {w}, estimated max now",
                  f"Worked out from your sets, not a tested max\n{most} {w}\n", f"Latest {most} {w} on Fri 6 Nov."]:
        assert words in squat, words


def test_bench_225_lb_for_5_reads_262_5_lb_on_its_page_as_in_every_summary(pinned):
    """225 lb is kept as 102.06 kg; its estimated max, 119.07 kg, is 262.5 lb, and 262.6 lb only from the result's
    119.1 kg. The lift page's tile, chart card, chart desc, its Sessions and Bests cards and Progress' Lifts table
    say what get_progress' summaries and the bests' own words (log_lifts' new_bests) say."""
    uid = only_user().id
    set_units(uid, "lb", "mi")
    first = services.log_lifts(uid, {"date": "2026-11-02", "sets": [
        {"exercise": "Bench Press", "weight_lb": 225, "reps": 5}]}, "claude")
    assert "error" not in first and "225 lb" in first["summary"]
    second = services.log_lifts(uid, {"date": "2026-11-09", "sets": [
        {"exercise": "Bench Press", "weight_lb": 235, "reps": 5}]}, "claude")
    assert "error" not in second
    one = services.get_progress(uid, "Bench Press")["summary"]
    most = re.match(r"Bench Press: estimated max now (\S+) lb\.", one).group(1)
    assert services.get_progress(uid)["summary"].startswith(f"Bench Press: estimated max now {most} lb, ")
    with db.session_scope() as s:
        history = lifts.history(s, uid, date.fromisoformat(DAY))
    words = [lifts.best_text({**p, "exercise": "Bench Press"}, LB_MI) for p in history["bests"]["Bench Press"]]
    assert sorted(b["text"] for b in second["new_bests"]) == sorted(words)
    page = _plain(pinned.get("/progress/bench-press").text)
    for text in [f"Estimated max now: {most} lb, estimated max now",
                 f"Worked out from your sets, not a tested max\n{most} lb\n", f"Latest {most} lb on Mon 9 Nov."]:
        assert text in page, text
    lines = page.splitlines()  # the Sessions card and the Bests card, one line an entry
    assert f"Mon 9 Nov: estimated max {most} lb" in lines and "Mon 2 Nov: estimated max 262.5 lb" in lines
    for text in words:
        assert text.replace("Bench Press ", "Mon 9 Nov: ", 1) in lines, text
    assert f"\nBench Press\n{most} lb\n" in _plain(pinned.get("/progress").text)


def test_only_a_lb_account_reads_the_services_unrounded():
    """In kg the result's rounded figure is already the summary's, and the kg summary quotes the 2 dp rate and
    trend: a kg page, whatever its distances, reads a service as the assistant reads it."""
    read = services.get_trends
    for units in (None, formatting.KG_KM, formatting.Units("kg", "mi")):
        assert web.exact_read(read, units) is read
    for units in (LB_MI, formatting.Units("lb", "km")):
        assert web.exact_read(read, units).keywords == {"exact": True}


# ---------------------------------------------------------------- a form posted after a units switch (the U2 review)

def switch(user_id: int, **data) -> None:
    """The assistant changes the units with update_settings while a page is open."""
    assert "error" not in services.update_settings(user_id, data, "claude")


@pytest.mark.parametrize("drawn, now, field, sent, kg", [
    (None, "lb", "kg", "80", 80.0), ("lb", "kg", "lb", "185", 83.91)])
def test_a_weigh_in_form_posted_after_a_units_switch_is_read_in_its_own_unit(pinned, drawn, now, field, sent, kg):
    uid = only_user().id
    set_units(uid, drawn, None)
    form = form_values(pinned.get(f"/day/{DAY}").text, "/weigh-in")
    assert field in form
    switch(uid, weight_unit=now)
    r = post(pinned, f"/day/{DAY}/weigh-in", **{**form, field: sent})
    assert r.status_code == 303, r.text[:300]
    assert [b.kg for b in rows(Bodyweight, uid)] == [kg]


@pytest.mark.parametrize("drawn, now, field, sent, km", [
    (None, "mi", "distance_km", "5", 5.0), ("mi", "km", "distance_mi", "3", 4.83)])
def test_a_cardio_form_posted_after_a_units_switch_is_read_in_its_own_unit(pinned, drawn, now, field, sent, km):
    """Drawn in km and posted after the switch to miles, the 5 km is kept, never dropped."""
    uid = only_user().id
    set_units(uid, None, drawn)
    form = form_values(pinned.get(f"/day/{DAY}").text, "/cardio")
    assert field in form
    switch(uid, distance_unit=now)
    r = post(pinned, f"/day/{DAY}/cardio", **{**form, "exercise": "Outdoor Run", "minutes": "27", field: sent})
    assert r.status_code == 303, r.text[:300]
    assert [(c.minutes, c.distance_km) for c in rows(CardioEntry, uid)] == [(27.0, km)]


@pytest.mark.parametrize("drawn, now", [((None, None), ("lb", "mi")), (("lb", "mi"), ("kg", "km"))])
def test_an_edit_page_posted_after_a_units_switch_is_read_in_its_own_units(pinned, drawn, now):
    """Drawn in one unit and posted untouched after the switch: nothing is sent and no version is kept. Then a
    changed weight and distance, typed in the unit the page was drawn in, are saved in that unit."""
    uid = only_user().id
    ids = stock(uid)
    set_units(uid, *drawn)
    edits = {kind: f"/entries/{kind}/{ids[key]}/edit" for kind, key in (("bodyweight", "weigh_in"),
                                                                          ("cardio_entry", "run"))}
    forms = {kind: form_values(pinned.get(a).text, "/edit") for kind, a in edits.items()}
    switch(uid, weight_unit=now[0], distance_unit=now[1])
    before, kept = versions(uid), len(calls(uid))
    for kind, address in edits.items():
        r = post(pinned, address, **forms[kind])
        assert r.status_code == 303 and "saved=unchanged" in r.headers["location"], (kind, r.text[:300])
    assert versions(uid) == before and len(calls(uid)) == kept
    weight, distance = ("kg", "distance_km") if drawn[0] is None else ("lb", "distance_mi")
    r = post(pinned, edits["bodyweight"], **{**forms["bodyweight"], weight: "81"})
    assert r.status_code == 303 and "saved=bodyweight" in r.headers["location"], r.text[:300]
    r = post(pinned, edits["cardio_entry"], **{**forms["cardio_entry"], distance: "4"})
    assert r.status_code == 303, r.text[:300]
    [weigh] = [b for b in rows(Bodyweight, uid) if b.id == ids["weigh_in"]]
    [run] = [c for c in rows(CardioEntry, uid) if c.id == ids["run"]]
    assert (weigh.kg, run.distance_km) == ((81.0, 4.0) if drawn[0] is None else (36.74, 6.44))
    assert versions(uid) == before + 2


# ---------------------------------------------------------------- Progress' Cardio card (the U2 review)

def test_a_fastest_pace_that_cannot_be_read_in_miles_leaves_its_cell_empty(lb_mi, monkeypatch):
    """With distances in miles the Cardio card's pace is read per activity. When that read fails the cell is
    empty: never get_cardio's per km pace under a per mi header."""
    client, uid = lb_mi["client"], lb_mi["uid"]
    [run] = [a for a in services.get_cardio(uid)["activities"] if a["exercise"] == "Outdoor Run"]
    real = services.get_cardio

    def failing(user_id, *a):
        return {"error": "internal_error", "message": "m", "fix": "f"} if a else real(user_id, *a)
    monkeypatch.setattr(services, "get_cardio", failing)
    page = client.get("/progress").text
    row = re.search(r'<tr><td class="lead">Outdoor Run</td>.*?</tr>', page, re.S).group(0)
    assert '<td class="num" data-label="Fastest pace (per mi)"></td>' in row
    assert run["fastest_pace"] and run["fastest_pace"] not in row


# ---------------------------------------------------------------- goal_charts takes the units (the U2 review)

def test_goal_charts_words_its_goals_in_the_units_it_is_given_as_get_goals_does(lb_mi):
    uid = lb_mi["uid"]
    charted = services.goal_charts(uid, LB_MI)["goals"]
    listed = services.get_goals(uid)["goals"]
    assert [(g["label"], g["plan_line"]) for g in charted] == [(g["label"], g["plan_line"]) for g in listed]
    assert [g for g in charted if " lb" in g["label"]]
    assert all(" lb" not in g["label"] for g in services.goal_charts(uid)["goals"])  # no units: kg, as before
    assert not hasattr(web, "in_units")


# ---------------------------------------------------------------- a lift goal's So far is the Trend (the fix round)

GOAL_LIFTS = ("Bench Press", "Back Squat", "Deadlift")


def seeded_lifts(user_id: int, seed: int) -> None:
    """Six sessions of each of three lifts on random days of the last 6 weeks, at random kg and reps, and a lift
    goal of each in lb: the points get_progress fits are the goal's own."""
    rng, today = random.Random(seed), date.fromisoformat(DAY)
    for name in GOAL_LIFTS:
        for back in sorted(rng.sample(range(1, 42), 6)):
            done = services.log_lifts(user_id, {"date": (today - timedelta(days=back)).isoformat(), "sets": [
                {"exercise": name, "weight_kg": round(rng.uniform(60, 120), 2), "reps": rng.randint(1, 10)}]},
                "claude")
            assert "error" not in done, done
        made = services.set_goal(user_id, {"kind": "lift", "exercise": name, "target_value": 400,
                                           "target_date": BY}, "claude")
        assert "error" not in made, made


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4, 5, 6, 7])
def test_a_lift_goals_so_far_on_goals_is_the_trend_of_progress_and_of_the_summary(pinned, seed):
    """On a lb account the Goals card's So far and its chart's desc say the lift's Trend as get_progress' one-lift
    summary and the Progress page say it, never the 2 dp kg slope shown again in lb. get_goals and a kg goal_charts
    keep the 2 dp kg figure."""
    uid = only_user().id
    set_units(uid, "lb", None)
    seeded_lifts(uid, seed)
    said = lifts_said(uid)
    goals_page, progress_page = pinned.get("/goals").text, pinned.get("/progress").text
    listed = {g["exercise"]: g for g in services.get_goals(uid)["goals"]}
    in_kg = {g["exercise"]: g for g in services.goal_charts(uid)["goals"]}
    for name in GOAL_LIFTS:
        trend = said[name][1]
        assert trend is not None, name
        card = re.search(rf'<li class="goal" id="goal-{listed[name]["goal_id"]}">.*?</li>', goals_page, re.S)
        assert card, name
        cell = re.search(r"<dt>So far</dt><dd>(\S+?)&nbsp;lb a week</dd>", card.group(0))
        desc = re.search(r"So far (\S+) lb a week\.", card.group(0))
        row = re.search(rf'>{re.escape(name)}</a></td>.*?data-label="Trend">(\S+?)&nbsp;lb a week</td>',
                        progress_page, re.S)
        assert cell and desc and row, name
        assert (cell.group(1), desc.group(1), row.group(1)) == (trend, trend, trend), (seed, name)
        slope = listed[name]["actual_per_week"]
        assert slope == round(slope, 2) == in_kg[name]["actual_per_week"]


# ---------------------------------------------------------------- both names of a pair, posted (the fix round)

@pytest.mark.parametrize("weight", [None, "lb"])
def test_a_weigh_in_posting_both_kg_and_lb_is_refused_and_writes_nothing(pinned, weight):
    uid = only_user().id
    set_units(uid, weight, None)
    r = post(pinned, f"/day/{DAY}/weigh-in", kg="80", lb="185")
    assert r.status_code == 400 and rows(Bodyweight, uid) == []
    assert calls(uid)[-1] == ("log_bodyweight", False, "invalid_field")


@pytest.mark.parametrize("distance", [None, "mi"])
def test_a_cardio_entry_posting_both_distances_is_refused_and_writes_nothing(pinned, distance):
    uid = only_user().id
    set_units(uid, None, distance)
    r = post(pinned, f"/day/{DAY}/cardio", exercise="Outdoor Run", minutes="27", distance_km="5", distance_mi="3")
    assert r.status_code == 400 and rows(CardioEntry, uid) == []
    assert calls(uid)[-1] == ("log_cardio", False, "invalid_field")


@pytest.mark.parametrize("kind, key, extra", [
    ("bodyweight", "weigh_in", {"kg": "81"}), ("cardio_entry", "run", {"distance_km": "6"}),
    ("cardio_entry", "run", {"speed_kmh": "10"})])
def test_an_edit_posting_both_names_of_a_pair_is_refused_and_keeps_no_version(lb_mi, kind, key, extra):
    """The lb and mi edit page as drawn, with the stored unit's name added: refused, never one value dropped."""
    client, uid = lb_mi["client"], lb_mi["uid"]
    address = f"/entries/{kind}/{lb_mi[key]}/edit"
    before = versions(uid)
    r = post(client, address, **{**form_values(client.get(address).text, "/edit"), **extra})
    assert r.status_code == 400, r.text[:300]
    assert calls(uid)[-1] == ("edit_entry", False, "invalid_field")
    assert versions(uid) == before


def test_a_mixed_edit_reads_each_pair_by_its_own_name_as_edit_entry_does(lb_mi):
    """distance_mi beside speed_kmh: edit_entry takes each pair on its own, so the page does too. The distance
    posted as drawn in miles is no change, and the speed is read in km/h, never dropped and never emptied."""
    client, uid = lb_mi["client"], lb_mi["uid"]
    address = f"/entries/cardio_entry/{lb_mi['run']}/edit"
    drawn = form_values(client.get(address).text, "/edit")
    [run] = [c for c in rows(CardioEntry, uid) if c.id == lb_mi["run"]]
    speed = round(run.speed_kmh + 0.5, 1)
    form = {k: v for k, v in drawn.items() if k != "speed_mph"}
    r = post(client, address, **{**form, "speed_kmh": str(speed)})
    assert r.status_code == 303 and "saved=cardio_entry" in r.headers["location"], r.text[:400]
    [after] = [c for c in rows(CardioEntry, uid) if c.id == lb_mi["run"]]
    assert (after.distance_km, after.speed_kmh) == (run.distance_km, speed)


# ---------------------------------------------------------------- the goals module note (the fix round)

def test_each_function_the_goals_module_note_heads_names_its_own_parameters():
    heads = re.findall(r"^(\w+)\(([^)]*)\)", goals.__doc__, re.M)
    assert {"goal_numbers", "goal_charts"} <= {name for name, _ in heads}
    for name, said in heads:
        params = [p.name for p in inspect.signature(getattr(goals, name)).parameters.values()]
        assert [p.split("=")[0].strip() for p in said.split(",")] == params, name
