"""Stage 5 on the web: the Goals page and its form, and the Trends card on Progress.

The page tests replace get_goals, goal_charts, get_trends and set_goal with the contract's result shapes, so
they test the pages whatever the services do. A few run the real services end to end.
"""

import html
import re
from datetime import timedelta

import pytest
from markupsafe import escape

from app import charts, db, formatting, rules, services, web
from app.models import CallLog, Goal
from app.services import goals as goal_rules

from .test_web import (  # noqa: F401 - fixtures
    CSP, FIXED_NOW, SITE, TOO_MANY, FakeSeam, body_weight_card, callouts_of, calls, client, only_user, post, rows,
    seam, session_of, sign_in, today_of, web_client,
)



def numbers(goal_id: int, **values) -> dict:
    """One goal-numbers row, the contract's shape, as goal_charts gives it: with the plan line's five fields, path,
    now_basis and line_ended. get_goals' rows are the same less the last three (fake_goals leaves them in)."""
    base = {"goal_id": goal_id, "label": "Back Squat 100 kg", "kind": "lift", "exercise": "Back Squat",
            "measurement_kind": None, "distance_km": None, "unit": "kg", "start_value": 80.0,
            "start_date": "2026-11-01", "direction": "up", "target_value": 100.0, "target_date": "2027-02-02",
            "latest": 85.0, "status": "on track", "needed_per_week": 1.25, "actual_per_week": 2.5,
            "projected_date": "2027-01-04", "projection": None, "note": None,
            "should_be_today": 82.0, "ahead_by": 3.0, "days_ahead": 14, "vs_plan": "ahead",
            "plan_line": "85 kg now against 82 kg on the plan line today: 3 kg ahead.",
            "path": {"plan": [], "actual": []}, "now_basis": "best_28d", "line_ended": False}
    return {**base, **values}


NOTHING_LOGGED = {"latest": None, "ahead_by": None, "days_ahead": None, "vs_plan": None, "plan_line": None,
                  "now_basis": None}
FOUR = [
    numbers(1),
    numbers(2, label="Body weight 75 kg", kind="bodyweight", exercise=None, start_value=80.4, latest=79.6,
            direction="down", target_value=75.0, needed_per_week=-0.42, actual_per_week=-0.3, status="behind",
            projected_date=None, should_be_today=79.0, ahead_by=-0.6, days_ahead=-5, vs_plan="behind",
            plan_line="79.6 kg now against 79 kg on the plan line today: 0.6 kg behind.", now_basis="average_7d"),
    numbers(3, label="Waist 80 cm", kind="measurement", exercise=None, measurement_kind="waist", unit="cm",
            start_value=88.0, direction="down", target_value=80.0, needed_per_week=None,
            actual_per_week=None, status="not enough data", projected_date=None, should_be_today=87.2,
            note="A projection needs 4 points in the last 6 weeks.", **NOTHING_LOGGED),
    numbers(4, label="Outdoor Run 5 km in 27:00", kind="cardio_best", exercise="Outdoor Run", distance_km=5.0,
            unit="min", start_value=31.0, latest=29.5, direction="down", target_value=27.0, needed_per_week=-0.5,
            actual_per_week=-0.75, status="reached", projected_date="2026-12-01", should_be_today=30.2,
            ahead_by=0.7, days_ahead=9, plan_line="29:30 now against 30:12 on the plan line today: 42 seconds ahead.",
            now_basis="latest"),
]
CLOSED = [{"goal_id": 9, "label": "Bench Press 80 kg", "kind": "lift", "status": "hit",
           "closed_at": "2026-11-05T10:00:00+00:00", "target_value": 80.0, "target_date": "2026-12-01"},
          {"goal_id": 10, "label": "Waist 85 cm", "kind": "measurement", "status": "dropped",
           "closed_at": "2026-11-06T10:00:00+00:00", "target_value": 85.0, "target_date": "2026-12-01"}]


def goals_result(goals=(), closed=()) -> dict:
    return {"today": "2026-11-10", "goals": list(goals), "closed": list(closed),
            "how": "", "summary": "", "links": {"goals": SITE + "/goals"}}


def charts_result(goals=()) -> dict:
    """goal_charts' shape: the open goals with their plan line, path, now_basis and line_ended."""
    return {"today": "2026-11-10", "goals": list(goals)}


@pytest.fixture
def fake_goals(monkeypatch):
    """fake_goals(goals, closed): get_goals and goal_charts answer with these, both on the one date; get_goals'
    calls are recorded."""
    seen = []

    def install(goals=(), closed=()):
        result, charted = goals_result(goals, closed), charts_result(goals)
        monkeypatch.setattr(services, "get_goals", lambda *args: seen.append(args) or result, raising=False)
        monkeypatch.setattr(services, "goal_charts", lambda *args: charted, raising=False)
        return seen

    return install


@pytest.fixture
def signed(client):
    sign_in(client)
    return client


SET_GOAL_H2 = '<h2 id="set-goal-title">Set a goal</h2>'
CLOSED_H2 = '<h2 id="closed-goals-title">Closed goals</h2>'


def goal_item(page: str, goal_id: int) -> str:
    return re.search(rf'<li class="(?:goal|entry-line)" id="goal-{goal_id}">(.*?)</li>', page, re.S).group(1)


# ---------------------------------------------------------------- the page

def test_goals_with_none_offers_the_body_weight_form(signed, fake_goals):
    """No kind named: the form is Body weight's, its token and its kind in hidden fields (the one-kind form,
    contract line 1667; the old every-field form is gone)."""
    seen = fake_goals()
    r = signed.get("/goals")
    assert r.status_code == 200 and "No open goals." in r.text and "Closed goals" not in r.text
    assert seen == [(only_user().id, True)]  # closed goals asked for too
    form = form_parts(r.text)
    assert form["hidden"][:2] == [("csrf_token", session_of(signed)["csrf"]), ("kind", "bodyweight")]
    assert [name for name, _ in form["hidden"]] == ["csrf_token", "kind", "key"]  # the form's key, its last field
    assert form["fields"] == FORM_FIELDS["bodyweight"]
    assert '<input id="goal-date" name="target_date" type="date" autocomplete="off">' in r.text


def test_goals_shows_every_kind_with_its_numbers_and_buttons(signed, fake_goals):
    fake_goals(FOUR, CLOSED)
    page = signed.get("/goals").text
    token = session_of(signed)["csrf"]
    lift = goal_item(page, 1)
    assert "<strong>Back Squat 100 kg</strong></h2>" in lift and '<p class="goal-by">by Tue 2 Feb 2027</p>' in lift
    assert "tag-good" in lift and ">On track<" in lift
    assert '<dd>80\u00a0kg<span class="sub">Sun 1 Nov</span></dd>' in lift
    assert "<dt>Plan line today</dt><dd>82\u00a0kg</dd>" in lift
    assert '<span class="big">85&nbsp;<span class="unit">kg</span></span>' in lift
    assert "<dd>+1.25&nbsp;kg a week</dd>" in lift and "<dd>+2.5&nbsp;kg a week</dd>" in lift
    assert '<dd>Mon 4 Jan 2027<span class="sub">reached</span></dd>' in lift
    weight = goal_item(page, 2)
    assert "tag-behind" in weight and "<dd>-0.42&nbsp;kg a week</dd>" in weight
    assert "<dd>-0.3&nbsp;kg a week</dd>" in weight
    assert '<dd class="words">No date yet</dd>' in weight
    waist = goal_item(page, 3)
    assert "Waist 80 cm" in waist and '<dd>88\u00a0cm<span class="sub">Sun 1 Nov</span></dd>' in waist
    assert "Nothing logged yet" in waist
    assert '<dd class="words">Not worked out yet</dd>' in waist
    assert '<dd class="words">Not enough data yet</dd>' in waist
    assert ">Not enough data<" in waist
    assert "A projection needs 4 points in the last 6 weeks." in waist  # Not enough data: the goal's own note
    run = goal_item(page, 4)
    assert "Outdoor Run 5 km in 27:00" in run and '<dd>31:00<span class="sub">Sun 1 Nov</span></dd>' in run
    assert '<span class="big">29:30</span>' in run and "<dd>-30\u00a0seconds a week</dd>" in run and ">Reached<" in run
    assert '<dt>At this pace</dt><dd class="words">Reached</dd>' in run
    for goal_id in (1, 2, 3, 4):
        item = goal_item(page, goal_id)
        for status, label, button in (("hit", "Hit", "secondary"), ("dropped", "Drop", "link")):
            assert re.search(rf'<form method="post" action="/goals/{goal_id}/status" class="inline">\s*'
                             rf'<input type="hidden" name="csrf_token" value="{token}">\s*'
                             rf'<input type="hidden" name="status" value="{status}">\s*'
                             rf'<button class="{button}">{label}</button>', item), (goal_id, status)
        assert 'value="open"' not in item
    closed = page.split(CLOSED_H2)[1].split(SET_GOAL_H2)[0]
    assert "Bench Press 80 kg" in closed and ">Hit<" in closed and "Closed Thu 5 Nov" in closed
    assert "Waist 85 cm" in closed and ">Dropped<" in closed
    assert closed.count('name="status" value="open"') == 2 and closed.count(">Reopen<") == 2
    assert page.index("Open goals") < page.index("Closed goals") < page.index("Set a goal")


def test_goals_with_many(signed, fake_goals):
    fake_goals([numbers(n, label=f"Lift {n} 100 kg") for n in range(1, 41)])
    page = signed.get("/goals").text
    assert all(f"Lift {n} 100 kg" in page for n in range(1, 41))
    assert page.count('name="status" value="hit"') == 40 and page.count('name="status" value="dropped"') == 40


def test_goals_text_is_escaped(signed, fake_goals):
    fake_goals([numbers(1, label="<script>x</script>", note="<b>n</b>", status="not enough data")])
    page = signed.get("/goals").text
    assert "<script>x" not in page and "&lt;script&gt;x&lt;/script&gt;" in page and "&lt;b&gt;n" in page


def test_goals_page_needs_sign_in_and_has_no_script(client, fake_goals):
    fake_goals(FOUR, CLOSED)
    r = client.get("/goals")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/sign-in"
    sign_in(client)
    services.update_settings(only_user().id, {"time_zone": "Asia/Kolkata"}, "web")
    r = client.get("/goals")
    assert r.headers["content-security-policy"] == CSP and "<script" not in r.text
    assert re.search(r"<[^>]+\son[a-z]+\s*=", r.text, re.I) is None and "style=" not in r.text
    assert '<a class="tab tab-goals" href="/goals" aria-current="page">Goals</a>' in r.text  # in the navigation


def test_a_get_goals_failure_is_a_404_page(signed, monkeypatch):
    monkeypatch.setattr(services, "get_goals", lambda *a: {"error": "no_account", "message": "x", "fix": "y"},
                        raising=False)
    r = signed.get("/goals")
    assert r.status_code == 404 and "There is nothing here." in r.text


# ---------------------------------------------------------------- the new-goal form

@pytest.fixture
def capture_set_goal(monkeypatch):
    """set_goal records what it is sent and answers `answer` (a success by default)."""
    sent = []
    state = {"answer": {"kind": "goal", "id": 1, "goal": {}, "progress": None, "warnings": [], "links": {}}}

    def fake(*args):
        sent.append(args)
        return state["answer"]

    monkeypatch.setattr(services, "set_goal", fake)
    return sent, state


@pytest.mark.parametrize("form, data", [
    ({"kind": "lift", "exercise": "Back Squat", "measurement_kind": "", "distance_km": "", "target_value": "100",
      "target_date": "2027-03-31", "start_value": "", "text": ""},
     {"kind": "lift", "exercise": "Back Squat", "target_value": "100", "target_date": "2027-03-31"}),
    ({"kind": "bodyweight", "exercise": "", "measurement_kind": "", "distance_km": "", "target_value": "75",
      "target_date": "2027-03-31", "start_value": "80.5", "text": "for the wedding"},
     {"kind": "bodyweight", "target_value": "75", "target_date": "2027-03-31", "start_value": "80.5",
      "text": "for the wedding"}),
    ({"kind": "measurement", "exercise": "", "measurement_kind": "waist", "distance_km": "", "target_value": "80",
      "target_date": "2027-03-31", "start_value": "", "text": ""},
     {"kind": "measurement", "measurement_kind": "waist", "target_value": "80", "target_date": "2027-03-31"}),
    ({"kind": "cardio_best", "exercise": "Outdoor Run", "measurement_kind": "", "distance_km": "5",
      "target_value": "28", "target_date": "2027-03-31", "start_value": "", "text": ""},
     {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": "5", "target_value": "28",
      "target_date": "2027-03-31"}),
], ids=["lift", "bodyweight", "measurement", "cardio_best"])
def test_the_form_sends_what_was_filled_in_to_set_goal(signed, capture_set_goal, fake_goals, form, data):
    sent, _ = capture_set_goal
    fake_goals()
    r = post(signed, "/goals", **form)
    assert r.status_code == 303 and r.headers["location"] == SITE + "/goals?saved=goal#top"
    assert sent == [(only_user().id, data, "web")]
    assert calls(only_user().id) == [("set_goal", True, None)]
    assert '<p class="ok">Goal set.</p>' in signed.get(r.headers["location"]).text


def test_the_form_cannot_send_a_goal_id_or_a_status(signed, capture_set_goal):
    sent, _ = capture_set_goal
    post(signed, "/goals", kind="lift", exercise="Back Squat", target_value="100", target_date="2027-03-31",
         goal_id="5", status="hit", user_id="99")
    assert sent[0][1] == {"kind": "lift", "exercise": "Back Squat", "target_value": "100", "target_date": "2027-03-31"}


# ---------------------------------------------------------------- Set a goal: one kind at a time (pass B14)

def form_parts(page: str) -> dict:
    """The Set a goal card as a person meets it: the segments (href, words, current), the form's hidden fields, its
    fields in order (label, tag, name), the top error's lines, each field's error, hint and value, the grey line."""
    from .test_pages_text import parse  # noqa: PLC0415

    [aside] = parse(page).find_all("aside", "set-goal")
    [nav] = aside.find_all("nav", "segments")
    [form] = aside.find_all("form")
    labels = {label.attrs.get("for"): label.text() for label in form.find_all("label")}
    controls = [c for c in form.iter() if c.tag in ("input", "select", "textarea")]
    shown = [c for c in controls if c.attrs.get("type") != "hidden"]
    errors = {p.attrs.get("id"): p.text() for p in form.find_all("p", "error")}

    def value(c):
        if c.tag == "textarea":
            return "".join(t for t in c.children if isinstance(t, str)) or None
        if c.tag == "select":
            return next((o.attrs.get("value") for o in c.find_all("option") if "selected" in o.attrs), None)
        return c.attrs.get("value")

    return {
        "form": (form.attrs.get("method"), form.attrs.get("action"), form.classes),
        "segments": [(a.attrs.get("href"), a.text(), a.attrs.get("aria-current")) for a in nav.find_all("a")],
        "hidden": [(c.attrs.get("name"), c.attrs.get("value")) for c in controls if c.attrs.get("type") == "hidden"],
        "fields": [(labels.get(c.attrs.get("id")), c.tag, c.attrs.get("name")) for c in shown],
        "top": [p.text() for d in form.find_all("div", "error") for p in d.find_all("p")],
        "errors": {c.attrs["name"]: errors.get(f"{c.attrs['id']}-error") for c in shown},
        "invalid": {c.attrs["name"] for c in shown if c.attrs.get("aria-invalid") == "true"},
        "described": {c.attrs["name"]: c.attrs.get("aria-describedby") for c in shown},
        "hints": {p.attrs.get("id"): p.text() for p in form.find_all("p", "hint")},
        "grey": [p.text() for p in form.find_all("p", "small")],
        "values": {c.attrs["name"]: value(c) for c in shown},
        "options": {c.attrs["name"]: [(o.attrs.get("value"), o.text()) for o in c.find_all("option")]
                    for c in shown if c.tag == "select"},
        "buttons": [(b.classes, b.text()) for b in form.find_all("button")],
    }


BY, NOTES = ("By", "input", "target_date"), ("Notes", "textarea", "text")
# Each kind's fields, in order (contract lines 1669 to 1674).
FORM_FIELDS = {
    "bodyweight": [("Target weight (kg)", "input", "target_value"), BY, NOTES],
    "lift": [("Lift", "select", "exercise"), ("Target estimated max (kg)", "input", "target_value"), BY, NOTES],
    "cardio_best": [("Activity", "select", "exercise"), ("Distance (km)", "input", "distance_km"),
                    ("Target time (min)", "input", "target_value"), BY, NOTES],
    "measurement": [("Measurement", "select", "measurement_kind"), ("Target", "input", "target_value"), BY, NOTES],
}
START_LABELS = {"bodyweight": "Where you are now (kg)", "lift": "Where you are now (kg)",
                "cardio_best": "Where you are now (min)", "measurement": "Where you are now"}
SEGMENTS = [("bodyweight", "Body weight"), ("lift", "Lift"), ("cardio_best", "Cardio time"),
            ("measurement", "Measurement")]
GREY = "The goal starts from your 7-day average today."
HINT = "Minutes, such as 27.5 for 27:30"
# The Measurement choices, each named by web.kind_with_unit: its word, then its unit in brackets.
MEASURE_CHOICES = [("waist", "Waist (cm)"), ("hips", "Hips (cm)"), ("chest", "Chest (cm)"), ("arm", "Arm (cm)"),
                   ("thigh", "Thigh (cm)"), ("resting_hr", "Resting heart rate (bpm)")]


@pytest.mark.parametrize("address, kind", [
    ("/goals", "bodyweight"), ("/goals?kind=bodyweight", "bodyweight"), ("/goals?kind=lift", "lift"),
    ("/goals?kind=cardio_best", "cardio_best"), ("/goals?kind=measurement", "measurement"),
    ("/goals?kind=", "bodyweight"), ("/goals?kind=nope", "bodyweight"), ("/goals?kind=Lift", "bodyweight"),
    ("/goals?kind=sessions", "bodyweight"), ("/goals?kind=steps", "bodyweight"),
    ("/goals?kind=%3Cb%3E", "bodyweight"), ("/goals?saved=goal&kind=measurement", "measurement"),
])
def test_the_four_segments_are_links_and_the_kind_the_address_names_is_current(signed, fake_goals, address, kind):
    """Four links in nav.segments, each to /goals?kind={kind}#set-goal, the current one aria-current="true". No kind,
    or one the page does not know, is Body weight. The form is the current kind's: its fields and none of another's."""
    fake_goals()
    r = signed.get(address)
    assert r.status_code == 200
    form = form_parts(r.text)
    assert form["segments"] == [(f"/goals?kind={k}#set-goal", words, "true" if k == kind else None)
                                for k, words in SEGMENTS]
    assert ("kind", kind) in form["hidden"] and form["fields"] == FORM_FIELDS[kind]
    assert form["buttons"] == [(["primary"], "Set goal")] and form["top"] == [] and not form["invalid"]
    assert '<nav class="segments" aria-label="Kind of goal">' in r.text and "&lt;b&gt;" not in r.text


@pytest.mark.parametrize("kind", list(FORM_FIELDS))
def test_each_kinds_form_holds_its_fields_in_order_its_empty_choice_and_its_own_lines(signed, fake_goals, kind):
    """Each list's empty choice names its own kind (contract line 1676). The grey line is Body weight's, the hint
    Cardio time's, tied to its input. Where you are now is not drawn on a fresh form."""
    fake_goals()
    form = form_parts(signed.get(f"/goals?kind={kind}").text)
    assert form["fields"] == FORM_FIELDS[kind]
    others = {f for k, fields in FORM_FIELDS.items() if k != kind for _, _, f in fields} - \
        {f for _, _, f in FORM_FIELDS[kind]}
    assert not others & set(form["values"]) and "start_value" not in form["values"]
    lifts = [(e["name"], e["name"]) for e in services.list_exercises("lift")["exercises"]]
    cardio = [(e["name"], e["name"]) for e in services.list_exercises("cardio")["exercises"]]
    empty = {"lift": ("exercise", [("", "No lift"), *lifts]),
             "cardio_best": ("exercise", [("", "No activity"), *cardio]),
             "measurement": ("measurement_kind", [("", "No measurement"), *MEASURE_CHOICES])}
    assert form["options"] == ({empty[kind][0]: empty[kind][1]} if kind in empty else {})
    assert form["grey"] == ([GREY] if kind == "bodyweight" else [])
    assert form["hints"] == ({"goal-target-hint": HINT} if kind == "cardio_best" else {})
    assert form["described"]["target_value"] == ("goal-target-hint" if kind == "cardio_best" else None)
    assert "No lift or activity" not in str(form)


def test_the_measurement_choices_are_kind_with_unit_of_each_kind(signed, fake_goals):
    fake_goals()
    form = form_parts(signed.get("/goals?kind=measurement").text)
    assert form["options"]["measurement_kind"][1:] == MEASURE_CHOICES
    assert MEASURE_CHOICES == [(k, web.kind_with_unit(k)) for k in web.GOAL_MEASUREMENTS]


@pytest.fixture
def real_set_goal(monkeypatch):
    """set_goal as it is, each answer recorded: every refusal a test reads is set_goal's own."""
    seen, real = [], services.set_goal

    def spy(*args):
        seen.append(real(*args))
        return seen[-1]

    monkeypatch.setattr(services, "set_goal", spy)
    return seen


def problems(result: dict) -> list[dict]:
    return [e for e in result.get("errors") or [] if isinstance(e, dict)] or [result]


def services_words_shown(page: str, result: dict) -> list[str]:
    """Each message and fix of a refusal that the page shows, escaped or not: none is the answer."""
    said = [result.get("message"), result.get("fix")] + [e.get(k) for e in problems(result) for k in ("message", "fix")]
    return [w for w in said if w and (w in page or html.escape(w) in page)]


def day_after(days: int) -> str:
    return (today_of(only_user()) + timedelta(days=days)).isoformat()


def filled(data: dict) -> dict:
    """A form to post, its BY a day 60 after the person's own today and its TODAY their today."""
    days = {"BY": day_after(60), "TODAY": day_after(0)}
    return {k: days.get(v, v) for k, v in data.items()}


# Every row of the Set a goal form's error table (web.GOAL_ERROR_WORDS), each driven by a real set_goal refusal
# posted through the form on a new account, nothing logged: (id, the form posted, the refusal's (code, field), the
# form's kind, where the line lands (a field, or "top"), the page's line).
FORM_LIFT = {"kind": "lift", "exercise": "Back Squat", "target_value": "140", "target_date": "BY"}
FORM_RUN = {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": "5", "target_value": "27", "target_date": "BY"}
FORM_WAIST = {"kind": "measurement", "measurement_kind": "waist", "target_value": "80", "target_date": "BY"}
FORM_WEIGHT = {"kind": "bodyweight", "target_value": "75", "target_date": "BY"}
KIND_LINE = "Choose a kind of goal above: Body weight, Lift, Cardio time or Measurement."
# The wrong-way lines (lane B's rewording, rev_b14 M1): true with the start inside its range and at its end.
WRONG_WAY = {"lift": "A lift goal aims above your estimated max now, and this target is not above it.",
             "cardio_best": "A cardio time goal aims below your time now, and this target is not below it."}
LIFT_TOP, TIME_LEAST = goal_rules.TARGETS["lift"][1], goal_rules.TARGETS["cardio_best"][0]  # set_goal's own ends
DAILY_LINE = "Choose a measurement from the list. Steps, sleep and water are daily amounts, not goals to reach by a date."
ERROR_ROWS = [
    ("kind missing", {"target_value": "75", "target_date": "BY"}, ("missing_field", "kind"), "bodyweight", "top",
     KIND_LINE),
    ("kind a setting", {**FORM_WEIGHT, "kind": "sessions"}, ("invalid_field", "kind"), "bodyweight", "top", KIND_LINE),
    ("kind unknown", {**FORM_WEIGHT, "kind": "x"}, ("invalid_field", "kind"), "bodyweight", "top", KIND_LINE),
    ("lift with no lift", {**FORM_LIFT, "exercise": ""}, ("missing_field", "exercise"), "lift", "exercise",
     "Choose a lift from the list."),
    ("lift unknown", {**FORM_LIFT, "exercise": "Squaty"}, ("unknown_exercise", "exercise"), "lift", "exercise",
     "Choose a lift from the list."),
    ("lift given an activity", {**FORM_LIFT, "exercise": "Outdoor Run"}, ("invalid_field", "exercise"), "lift",
     "exercise", "Choose a lift from the list."),
    ("cardio with no activity", {**FORM_RUN, "exercise": ""}, ("missing_field", "exercise"), "cardio_best", "exercise",
     "Choose an activity from the list."),
    ("cardio unknown", {**FORM_RUN, "exercise": "Moonwalk"}, ("unknown_exercise", "exercise"), "cardio_best", "exercise",
     "Choose an activity from the list."),
    ("cardio given a lift", {**FORM_RUN, "exercise": "Back Squat"}, ("invalid_field", "exercise"), "cardio_best",
     "exercise", "Choose an activity from the list."),
    ("no distance", {**FORM_RUN, "distance_km": ""}, ("missing_field", "distance_km"), "cardio_best", "distance_km",
     "Give the distance the time is for, in km."),
    ("distance too far", {**FORM_RUN, "distance_km": "900"}, ("invalid_field", "distance_km"), "cardio_best",
     "distance_km", "Give the distance in km, from 0.1 to 300."),
    ("distance not a number", {**FORM_RUN, "distance_km": "five"}, ("invalid_field", "distance_km"), "cardio_best",
     "distance_km", "Give the distance in km, from 0.1 to 300."),
    ("no measurement", {**FORM_WAIST, "measurement_kind": ""}, ("missing_field", "measurement_kind"), "measurement",
     "measurement_kind", "Choose a measurement from the list."),
    ("measurement unknown", {**FORM_WAIST, "measurement_kind": "wrist"}, ("invalid_kind", "measurement_kind"),
     "measurement", "measurement_kind", "Choose a measurement from the list."),
    ("sleep as a goal", {**FORM_WAIST, "measurement_kind": "sleep_hours"}, ("invalid_field", "measurement_kind"),
     "measurement", "measurement_kind", DAILY_LINE),
    ("water as a goal", {**FORM_WAIST, "measurement_kind": "water_ml"}, ("invalid_field", "measurement_kind"),
     "measurement", "measurement_kind", DAILY_LINE),
    ("steps as a goal", {**FORM_WAIST, "measurement_kind": "steps"}, ("invalid_field", "measurement_kind"),
     "measurement", "measurement_kind", DAILY_LINE),
    ("weight with no target", {**FORM_WEIGHT, "target_value": ""}, ("missing_field", "target_value"), "bodyweight",
     "target_value", "Give the weight you aim for, in kg."),
    ("lift with no target", {**FORM_LIFT, "target_value": ""}, ("missing_field", "target_value"), "lift", "target_value",
     "Give the estimated max you aim for, in kg."),
    ("cardio with no target", {**FORM_RUN, "target_value": ""}, ("missing_field", "target_value"), "cardio_best",
     "target_value", "Give the time you aim for, in minutes."),
    ("measurement with no target", {**FORM_WAIST, "target_value": ""}, ("missing_field", "target_value"), "measurement",
     "target_value", "Give a target for your waist, in cm."),
    ("weight out of range", {**FORM_WEIGHT, "target_value": "900"}, ("invalid_field", "target_value"), "bodyweight",
     "target_value", "Give the weight you aim for in kg, from 20 to 400."),
    ("lift not a number", {**FORM_LIFT, "target_value": "lots"}, ("invalid_field", "target_value"), "lift",
     "target_value", "Give the estimated max you aim for in kg, more than 0 and at most 666.67."),
    ("cardio out of range", {**FORM_RUN, "target_value": "700"}, ("invalid_field", "target_value"), "cardio_best",
     "target_value", "Give the time you aim for in minutes, from 1 to 600."),
    ("waist out of range", {**FORM_WAIST, "target_value": "20"}, ("invalid_field", "target_value"), "measurement",
     "target_value", "Give a target for your waist in cm, from 30 to 300."),
    ("heart rate not whole", {**FORM_WAIST, "measurement_kind": "resting_hr", "target_value": "52.5"},
     ("invalid_field", "target_value"), "measurement", "target_value",
     "Give a target for your resting heart rate in bpm, from 20 to 250, as a whole number."),
    ("lift below the start", {**FORM_LIFT, "target_value": "90", "start_value": "100"}, ("wrong_direction", "target_value"),
     "lift", "target_value", WRONG_WAY["lift"]),
    # The start at the top of the lift's range: no higher target can be set, and the line asks for none.
    ("lift at the top of its range", {**FORM_LIFT, "target_value": "600", "start_value": str(LIFT_TOP)},
     ("wrong_direction", "target_value"), "lift", "target_value", WRONG_WAY["lift"]),
    ("cardio slower than the start", {**FORM_RUN, "target_value": "35", "start_value": "30"},
     ("wrong_direction", "target_value"), "cardio_best", "target_value", WRONG_WAY["cardio_best"]),
    # The start at the bottom of a cardio time's range: no faster time can be set, and the line asks for none.
    ("cardio at the bottom of its range", {**FORM_RUN, "target_value": "5", "start_value": str(TIME_LEAST)},
     ("wrong_direction", "target_value"), "cardio_best", "target_value", WRONG_WAY["cardio_best"]),
    ("weight at the start", {**FORM_WEIGHT, "target_value": "80", "start_value": "80"}, ("target_is_start", "target_value"),
     "bodyweight", "target_value", "That target is where you are now: give one above or below it."),
    ("waist at the start", {**FORM_WAIST, "target_value": "88", "start_value": "88"}, ("target_is_start", "target_value"),
     "measurement", "target_value", "That target is where you are now: give one above or below it."),
    ("no date", {**FORM_WEIGHT, "target_date": ""}, ("missing_field", "target_date"), "bodyweight", "target_date",
     "Choose the day you aim to reach it by."),
    ("not a date", {**FORM_LIFT, "target_date": "31/12/2027"}, ("invalid_date", "target_date"), "lift", "target_date",
     "That is not a day: choose one after today."),
    ("today", {**FORM_RUN, "target_date": "TODAY"}, ("date_not_in_future", "target_date"), "cardio_best", "target_date",
     "Choose a day after today."),
    ("weight, nothing to start from", FORM_WEIGHT, ("missing_field", "start_value"), "bodyweight", "start_value",
     "Nothing is logged to start from yet: give your weight now, or log a weigh-in first."),
    ("lift, nothing to start from", FORM_LIFT, ("missing_field", "start_value"), "lift", "start_value",
     "No session of this lift is logged yet: give your estimated max now, or log a session first."),
    ("cardio, nothing to start from", FORM_RUN, ("missing_field", "start_value"), "cardio_best", "start_value",
     "No session of this activity at this distance or longer is logged yet: give your time for it now, or log one "
     "first."),
    ("waist, nothing to start from", FORM_WAIST, ("missing_field", "start_value"), "measurement", "start_value",
     "No waist measurement is logged yet: give where you are now, or log one first."),
    ("weight start not a number", {**FORM_WEIGHT, "start_value": "eighty"}, ("invalid_field", "start_value"), "bodyweight",
     "start_value", "Give your weight now in kg, from 20 to 400."),
    ("lift start out of range", {**FORM_LIFT, "start_value": "0"}, ("invalid_field", "start_value"), "lift",
     "start_value", "Give your estimated max now in kg, more than 0 and at most 666.67."),
    ("cardio start out of range", {**FORM_RUN, "start_value": "0.5"}, ("invalid_field", "start_value"), "cardio_best",
     "start_value", "Give your time now in minutes, from 1 to 600."),
    ("arm start out of range", {**FORM_WAIST, "measurement_kind": "arm", "target_value": "38", "start_value": "5"},
     ("invalid_field", "start_value"), "measurement", "start_value",
     "Give your arm measurement now, in cm, from 10 to 100."),
    ("heart rate start not whole", {**FORM_WAIST, "measurement_kind": "resting_hr", "target_value": "52",
                                    "start_value": "60.5"},
     ("invalid_field", "start_value"), "measurement", "start_value",
     "Give your resting heart rate measurement now, in bpm, from 20 to 250, as a whole number."),
    ("notes too long", {**FORM_WEIGHT, "text": "x" * 2001}, ("invalid_field", "text"), "bodyweight", "text",
     "Keep the notes to 2,000 characters or fewer, in plain text."),
    ("notes with a control character", {**FORM_LIFT, "text": "a\x01b"}, ("invalid_field", "text"), "lift", "text",
     "Keep the notes to 2,000 characters or fewer, in plain text."),
]
FREE_TEXT = ("text",)  # the form's one free-text field, drawn back as a textarea's text
FIELD_IDS = {"exercise": "goal-exercise", "measurement_kind": "goal-measurement", "distance_km": "goal-distance",
             "target_value": "goal-target", "target_date": "goal-date", "start_value": "goal-start",
             "text": "goal-text"}


@pytest.mark.parametrize("data, key, kind, where, line", [row[1:] for row in ERROR_ROWS],
                         ids=[row[0] for row in ERROR_ROWS])
def test_each_refusal_shows_the_pages_own_line_under_its_field(signed, real_set_goal, data, key, kind, where, line):
    """A real set_goal refusal, placed by its error code and its field: the page's own line under that field, tied
    to its input, or at the top of the form when the form draws no such field. None of the service's words show.
    The form comes back with the posted kind and what was typed. The refused post is logged as a set_goal call
    with its code. Every rule a page keeps, the check that it never says Claude among them, reads this render: so
    every line of the table is read by them, not only the sweeps' sample."""
    from .test_pages_text import entered_notes, entered_text, names_of, page_findings, step_4_findings  # noqa: PLC0415

    r = post(signed, "/goals", **filled(data))
    assert r.status_code == 400
    [result] = real_set_goal
    assert [(e["error"], e.get("field")) for e in problems(result)] == [key]
    uid = only_user().id
    assert calls(uid) == [("set_goal", False, key[0])]
    # What the person typed as free text is theirs, as entered_text is: the notes, the one field the page shows back
    # as text (a textarea). A select's value, a figure and a date are never masked (rev_b14 T-3): before, every
    # posted value was, short ones like "0" and list values like "waist" among them.
    typed = [v for f, v in filled(data).items() if f in FREE_TEXT and v]
    assert set(typed) <= {v for v in filled(data).values() if v}  # never more than before: nothing caught passes
    assert page_findings("/goals", r.text, entered_text(uid) + typed, uid, route="/goals") == []
    assert step_4_findings("/goals", r.text, names_of(uid), entered_notes(uid), route="/goals") == []
    form = form_parts(r.text)
    assert ("kind", kind) in form["hidden"]
    assert [words for _, words, current in form["segments"] if current] == [dict(SEGMENTS)[kind]]
    if where == "top":
        assert form["top"] == [line] and not any(form["errors"].values()) and not form["invalid"]
    else:
        assert form["errors"][where] == line and form["top"] == []
        assert form["invalid"] == {where} and {f for f, e in form["errors"].items() if e} == {where}
        assert f"{FIELD_IDS[where]}-error" in form["described"][where].split()
    assert services_words_shown(r.text, result) == []
    choices = {f: {v for v, _ in o} for f, o in form["options"].items()}
    kept = {f: v for f, v in filled(data).items() if f in form["values"]}
    assert {f: form["values"][f] for f in kept} == \
        {f: v if v and (f not in choices or v in choices[f]) else None for f, v in kept.items()}
    assert rows(Goal, only_user().id) == []


def test_the_rows_drive_every_line_of_the_table_and_no_other():
    """Each (code, field) of web.GOAL_ERROR_WORDS, and each kind of a line that differs by kind, is driven by a row
    above: no line goes unproved, and no row lands on a line the table lacks."""
    driven = {(key, kind) for _, _, key, kind, _, _ in ERROR_ROWS}
    assert {key for key, _ in driven} == set(web.GOAL_ERROR_WORDS)
    by_kind = {(key, kind) for key, words in web.GOAL_ERROR_WORDS.items() if isinstance(words, dict) for kind in words}
    assert {(key, kind) for key, kind in driven if isinstance(web.GOAL_ERROR_WORDS[key], dict)} == by_kind


# Each measurement the form's list offers: (its kind, a target inside its range, its two start lines). The lines name
# it as a measurement, one form for every kind (rev_b14 R3 V1: "No hips is logged yet", "where your hips is now").
MEASUREMENT_START_LINES = {
    "waist": ("80", "No waist measurement is logged yet: give where you are now, or log one first.",
              "Give your waist measurement now, in cm, from 30 to 300."),
    "hips": ("95", "No hips measurement is logged yet: give where you are now, or log one first.",
             "Give your hips measurement now, in cm, from 30 to 300."),
    "chest": ("100", "No chest measurement is logged yet: give where you are now, or log one first.",
              "Give your chest measurement now, in cm, from 30 to 300."),
    "arm": ("38", "No arm measurement is logged yet: give where you are now, or log one first.",
            "Give your arm measurement now, in cm, from 10 to 100."),
    "thigh": ("60", "No thigh measurement is logged yet: give where you are now, or log one first.",
              "Give your thigh measurement now, in cm, from 20 to 150."),
    "resting_hr": ("52", "No resting heart rate measurement is logged yet: give where you are now, or log one first.",
                   "Give your resting heart rate measurement now, in bpm, from 20 to 250, as a whole number."),
}


@pytest.mark.parametrize("what", sorted(MEASUREMENT_START_LINES))
def test_each_measurements_start_lines_name_it_as_a_measurement(signed, real_set_goal, what):
    """Both of Where you are now's lines for every measurement the form offers, each a real set_goal refusal posted
    through the form with nothing logged: no start (missing_field), then a start below the range (invalid_field)."""
    assert sorted(MEASUREMENT_START_LINES) == sorted(web.GOAL_MEASUREMENTS)
    target, missing, invalid = MEASUREMENT_START_LINES[what]
    form = {**FORM_WAIST, "measurement_kind": what, "target_value": target}
    for data, key, line in ((form, ("missing_field", "start_value"), missing),
                            ({**form, "start_value": "5"}, ("invalid_field", "start_value"), invalid)):
        real_set_goal.clear()
        r = post(signed, "/goals", **filled(data))
        assert r.status_code == 400
        [result] = real_set_goal
        assert [(e["error"], e.get("field")) for e in problems(result)] == [key], what
        parts = form_parts(r.text)
        assert parts["errors"]["start_value"] == line and parts["top"] == [], what
        assert services_words_shown(r.text, result) == []


@pytest.mark.parametrize("what, start", [("sleep_hours", "Sleep cannot be a goal"),
                                         ("water_ml", "Water cannot be a goal")])
def test_sleep_and_water_land_beside_the_measurement_field_by_their_field(signed, real_set_goal, what, start):
    """Contract lines 748 and 1693: placed by the refusal's field, measurement_kind, never by its words."""
    r = post(signed, "/goals", **filled({**FORM_WAIST, "measurement_kind": what}))
    [result] = real_set_goal
    assert result["message"].startswith(start) and result["field"] == "measurement_kind"
    form = form_parts(r.text)
    assert form["errors"]["measurement_kind"] == DAILY_LINE and form["top"] == [] and start not in r.text


def test_three_problems_at_once_each_go_under_their_own_field(signed, real_set_goal):
    r = post(signed, "/goals", **filled({**FORM_LIFT, "exercise": "", "target_value": "lots", "target_date": "TODAY"}))
    [result] = real_set_goal
    assert [(e["error"], e["field"]) for e in problems(result)] == [
        ("missing_field", "exercise"), ("invalid_field", "target_value"), ("date_not_in_future", "target_date")]
    form = form_parts(r.text)
    assert {f: e for f, e in form["errors"].items() if e} == {
        "exercise": "Choose a lift from the list.",
        "target_value": "Give the estimated max you aim for in kg, more than 0 and at most 666.67.",
        "target_date": "Choose a day after today."}
    assert form["invalid"] == {"exercise", "target_value", "target_date"} and form["top"] == []
    assert services_words_shown(r.text, result) == []


def test_a_refusal_the_table_does_not_know_shows_one_general_line_at_the_top(signed, monkeypatch):
    """Real set_goal refusals the form cannot reach, made by handing set_goal what the form never sends: invalid_by,
    which names no field, and a status on a new goal, a field the table has no line for. Each shows the one general
    line at the top and nothing of the service's. Beside a known problem, that one keeps its own line."""
    real, seen = services.set_goal, []
    for change, known in (({"by": "api"}, None), ({"status": "won"}, None),
                          ({"status": "hit", "target_value": "lots"}, "target_value")):
        def odd(user_id, data, by, change=change):
            seen.append(real(user_id, {**data, **{k: v for k, v in change.items() if k != "by"}},
                             change.get("by", by)))
            return seen[-1]

        monkeypatch.setattr(services, "set_goal", odd)
        r = post(signed, "/goals", **filled(FORM_LIFT))
        result = seen[-1]
        assert r.status_code == 400 and result.get("error"), result
        assert any(web.goal_error_words(e, "lift") is None for e in problems(result)), result
        form = form_parts(r.text)
        assert form["top"] == [web.GOAL_ERROR_GENERAL] == ["That goal was not set. Check each field and try again."]
        assert {f for f, e in form["errors"].items() if e} == ({known} if known else set())
        assert services_words_shown(r.text, result) == []
    assert [[(e["error"], e.get("field")) for e in problems(r)] for r in seen] == [
        [("invalid_by", None)], [("invalid_field", "status")],
        [("invalid_field", "status"), ("invalid_field", "target_value")]]


# Every refusal set_goal can build, read from app/services/goals.py: (function, how it is built, the field it names).
# A rules.error call, or a dict literal with an "error" key, gives its code. "<_at>" wraps a refusal built
# elsewhere, "<dict>" is a dict literal that sets the field of one. "<name>" is a call to a function of app outside
# goals.py that can build a refusal: its codes are read from its own source, through every function of app it calls
# (OUTSIDE_CODES). A call whose refusal is unpacked into _, or cut off by [0], drops it, so it is no site. A field
# passed through a name is read for each value the name can take, each its own site: a for loop's tuple, a
# parameter's argument at every call, a name set from a call of goals.py. Each is answered by the table's lines
# (their keys) or by why the form never reaches it. A refusal set_goal gains makes the read differ from this table,
# so it cannot go unseen: give it a line in web.GOAL_ERROR_WORDS, or say here why the form never reaches it. Which
# kinds can reach a line is not in the source: the sweep test
# test_each_kinds_form_through_real_set_goal_meets_no_refusal_without_a_line drives the service for that.
NOT_FROM_THE_FORM = "not from the form"
NO_GOAL_ID = NOT_FROM_THE_FORM + ": the form sends no goal_id"
SERVICE_REFUSALS = {
    ("_value", "<in_range>", None): [("invalid_field", "target_value"), ("invalid_field", "start_value")],
    ("_value", "<_at>", "target_value"): [("invalid_field", "target_value")],
    ("_value", "<_at>", "start_value"): [("invalid_field", "start_value")],
    ("_value", "invalid_field", "target_value"): [("invalid_field", "target_value")],
    ("_value", "invalid_field", "start_value"): [("invalid_field", "start_value")],
    ("_future_date", "invalid_date", "target_date"): [("invalid_date", "target_date")],
    ("_future_date", "date_not_in_future", "target_date"): [("date_not_in_future", "target_date")],
    ("_kind_error", "invalid_field", "kind"): [("invalid_field", "kind")],
    ("_kind_error", "missing_field", "kind"): [("missing_field", "kind")],
    ("_measurement_kind", "missing_field", "measurement_kind"): [("missing_field", "measurement_kind")],
    ("_measurement_kind", "invalid_field", "measurement_kind"): [("invalid_field", "measurement_kind")],
    ("_measurement_kind", "invalid_kind", "measurement_kind"): [("invalid_kind", "measurement_kind")],
    ("_wrong_kind", "invalid_field", "exercise"): [("invalid_field", "exercise")],
    ("_identity", "<match_cardio>", None): [("missing_field", "exercise"), ("unknown_exercise", "exercise")],
    ("_identity", "<dict>", "exercise"): [("missing_field", "exercise")],  # match_cardio's missing_field, renamed
    ("_identity", "missing_field", "exercise"): [("missing_field", "exercise")],
    ("_identity", "<in_range>", None): [("invalid_field", "distance_km")],
    ("_identity", "missing_field", "distance_km"): [("missing_field", "distance_km")],
    ("_identity", "<_at>", "exercise"): [("unknown_exercise", "exercise")],
    ("_identity", "<_at>", "distance_km"): [("invalid_field", "distance_km")],
    ("_identity", "<unknown_exercise>", None): [("unknown_exercise", "exercise")],
    ("_no_start", "missing_field", "start_value"): [("missing_field", "start_value")],
    ("_wrong_way", "wrong_direction", "target_value"): [("wrong_direction", "target_value")],
    ("set_goal", "<check_data>", None): NOT_FROM_THE_FORM + ": the web always sends an object",
    ("set_goal", "<check_by>", None): NOT_FROM_THE_FORM + ": the web always sends by web",
    ("set_goal", "<check_write_limit>", None): NOT_FROM_THE_FORM + ": the too many writes page answers it",
    ("set_goal", "<no_account>", None): NOT_FROM_THE_FORM + ": the page needs a signed-in person",
    ("set_goal", "<text>", None): [("invalid_field", "text")],
    ("set_goal", "<_at>", "text"): [("invalid_field", "text")],
    ("set_goal", "invalid_field", "status"): NOT_FROM_THE_FORM + ": the form sends no status",
    ("set_goal", "<not_found>", None): NO_GOAL_ID,
    ("set_goal", "nothing_to_change", None): NO_GOAL_ID,
    ("set_goal", "cannot_change_kind", "kind"): NO_GOAL_ID,
    ("set_goal", "cannot_change_kind", "exercise"): NO_GOAL_ID,
    ("set_goal", "cannot_change_kind", "measurement_kind"): NO_GOAL_ID,
    ("set_goal", "cannot_change_kind", "distance_km"): NO_GOAL_ID,
    ("set_goal", "missing_field", "target_value"): [("missing_field", "target_value")],
    ("set_goal", "missing_field", "target_date"): [("missing_field", "target_date")],
    ("set_goal", "target_is_start", "target_value"): [("target_is_start", "target_value")],
    ("get_goals", "<no_account>", None): NOT_FROM_THE_FORM + ": get_goals, not set_goal",
    ("goal_charts", "<no_account>", None): NOT_FROM_THE_FORM + ": goal_charts, not set_goal",
}
# How many times each is built, where more than once: a second copy of one is a new refusal too.
SERVICE_REFUSAL_COUNTS = {("_kind_error", "invalid_field", "kind"): 2, ("_wrong_kind", "invalid_field", "exercise"): 2,
                          ("_identity", "<_at>", "exercise"): 2, ("_no_start", "missing_field", "start_value"): 4,
                          ("_wrong_way", "wrong_direction", "target_value"): 2,
                          ("set_goal", "invalid_field", "status"): 2}
# The codes each function of app outside goals.py that set_goal's module calls can build, read from its source.
OUTSIDE_CODES = {"check_by": {"invalid_by"}, "check_data": {"invalid_field"}, "check_write_limit": {"too_many_writes"},
                 "in_range": {"invalid_field"}, "match_cardio": {"missing_field", "unknown_exercise"},
                 "no_account": {"no_account"}, "not_found": {"not_found"}, "text": {"invalid_field"},
                 "unknown_exercise": {"unknown_exercise"}, "match_kind": {"invalid_kind"}}


def _named(node):
    import ast  # noqa: PLC0415

    if node is None:
        return None
    return node.value if isinstance(node, ast.Constant) else f"<{node.id}>" if isinstance(node, ast.Name) else "<?>"


def _resolve(node, scope: dict):
    """What a called name or dotted name is in a module's scope, or None."""
    import ast  # noqa: PLC0415

    if isinstance(node, ast.Name):
        return scope.get(node.id)
    if isinstance(node, ast.Attribute):
        owner = _resolve(node.value, scope)
        return getattr(owner, node.attr, None) if owner is not None else None
    return None


def codes_built(fn, seen: set | None = None) -> set:
    """Every error code a function of app can build, read from its source: each rules.error call's code, each dict
    literal's or dict() call's "error", and those of every function of app it calls."""
    import ast  # noqa: PLC0415
    import inspect  # noqa: PLC0415
    import textwrap  # noqa: PLC0415

    seen = set() if seen is None else seen
    if fn in seen:
        return set()
    seen.add(fn)
    out = set()
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn)))):
        if isinstance(node, ast.Dict):
            out |= {_named(v) for k, v in zip(node.keys, node.values)
                    if isinstance(k, ast.Constant) and k.value == "error"}
        elif isinstance(node, ast.Call):
            target = _resolve(node.func, fn.__globals__)
            if target is rules.error:
                out.add(_named(node.args[0]) if node.args else "<?>")
            elif target is dict:
                out |= {_named(k.value) for k in node.keywords if k.arg == "error"}
            elif inspect.isfunction(target) and target.__module__.startswith("app."):
                out |= codes_built(target, seen)
    return out


def service_refusals() -> tuple[dict[tuple, int], dict[str, set]]:
    """Each refusal app/services/goals.py builds, named as SERVICE_REFUSALS names it, and how many times; and the
    codes each outside function it calls can build, by "<name>"."""
    import ast  # noqa: PLC0415
    import inspect  # noqa: PLC0415
    from collections import Counter  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    tree = ast.parse((Path(__file__).parents[1] / "app" / "services" / "goals.py").read_text(encoding="utf-8"))
    scope = vars(goal_rules)
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def dropped(call) -> bool:
        """The refusal a call gives is thrown away: `hit, _ = f(...)` or `f(...)[0]`."""
        up = parents.get(call)
        if isinstance(up, ast.Subscript) and up.value is call:
            return isinstance(up.slice, ast.Constant) and up.slice.value == 0
        return isinstance(up, ast.Assign) and up.value is call and len(up.targets) == 1 and \
            isinstance(up.targets[0], ast.Tuple) and isinstance(up.targets[0].elts[-1], ast.Name) and \
            up.targets[0].elts[-1].id == "_"

    def values(node, fn) -> list:
        """The values a field can take: a constant as it is; a name, each value a for loop's tuple, the
        parameter's argument at every call, or a call of goals.py it is set from gives it; else "<?>"."""
        if not isinstance(node, ast.Name):
            return [_named(node)]
        name, out = node.id, []
        for loop in (n for n in ast.walk(fn) if isinstance(n, ast.For) and isinstance(n.target, ast.Tuple)):
            slots = [e.id if isinstance(e, ast.Name) else None for e in loop.target.elts]
            if name in slots:
                at = slots.index(name)
                items = loop.iter.elts if isinstance(loop.iter, (ast.Tuple, ast.List)) else [None]
                out += [_named(i.elts[at]) if isinstance(i, ast.Tuple) else "<?>" for i in items]
        params = [a.arg for a in fn.args.args]
        if name in params:
            at = params.index(name)
            calls_of = (n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == fn.name)
            for call in calls_of:
                given = call.args[at] if at < len(call.args) else \
                    next((k.value for k in call.keywords if k.arg == name), None)
                out.append(given.value if isinstance(given, ast.Constant) else "<?>")
        for assign in (n for n in ast.walk(fn) if isinstance(n, ast.Assign)):
            if not any(isinstance(t, ast.Name) and t.id == name for t in assign.targets):
                continue
            made = assign.value
            if isinstance(made, ast.Constant) and made.value is None:
                continue
            maker = functions.get(getattr(made.func, "id", None)) if isinstance(made, ast.Call) else None
            if maker is None:
                out.append("<?>")
                continue
            for ret in (r for r in ast.walk(maker) if isinstance(r, ast.Return)):
                if not (isinstance(ret.value, ast.Constant) and ret.value.value is None):
                    out.append(ret.value.value if isinstance(ret.value, ast.Constant) else "<?>")
        return sorted(set(out), key=str) or ["<?>"]

    # Each function's nodes, and the module's own: every node outside every function, so a refusal built at module
    # level (a constant, a table of builders) is read too. A module-level field is read as it is written.
    inside = {n for fn in functions.values() for n in ast.walk(fn)}
    owners = [(fn.name, fn, list(ast.walk(fn))) for fn in functions.values()] + \
        [("<module>", None, [n for n in ast.walk(tree) if n not in inside])]
    found, outside = Counter(), {}
    for owner, fn, nodes in owners:
        def field_values(node, fn=fn):
            return values(node, fn) if fn is not None else [_named(node)]

        for call in (n for n in nodes if isinstance(n, ast.Call)):
            if not isinstance(call.func, (ast.Name, ast.Attribute)):
                # A callee no name gives, such as a table's entry or a call's result: what it builds cannot be read,
                # so it is a site of its own, "<?>", which no key answers.
                found[(owner, "<?>", None)] += 1
                continue
            target = _resolve(call.func, scope)
            name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", None)
            if target is rules.error:
                field = next((k.value for k in call.keywords if k.arg == "field"), None)
                for value in field_values(field) if field is not None else [None]:
                    found[(owner, _named(call.args[0]) if call.args else "<?>", value)] += 1
            elif name == "_at":
                for value in field_values(call.args[1]):
                    found[(owner, "<_at>", value)] += 1
            elif inspect.isfunction(target) and target.__module__.startswith("app.") and \
                    target.__module__ != goal_rules.__name__ and not dropped(call):
                codes = codes_built(target)
                if codes:
                    outside[f"<{target.__name__}>"] = codes
                    found[(owner, f"<{target.__name__}>", None)] += 1
        if owner == "_at":  # the field setter itself: every call of it is read above
            continue
        for d in (n for n in nodes if isinstance(n, ast.Dict)):
            keys = {k.value: v for k, v in zip(d.keys, d.values) if isinstance(k, ast.Constant)}
            if "error" in keys or "field" in keys:
                code = _named(keys["error"]) if "error" in keys else "<dict>"
                for value in field_values(keys["field"]) if "field" in keys else [None]:
                    found[(owner, code, value)] += 1
    return dict(found), outside


def test_every_refusal_set_goal_builds_has_a_line_or_a_reason():
    """A refusal set_goal gains fails here until the table gives it a line or this test says why the form never
    reaches it: a new code from a function it calls, a refusal built as a dict literal, a new value of a field passed
    through a name, a refusal built at module level, a builder called through a table or a call's result (rev_b14
    T-1: "<module>" and "<?>" sites, which no key answers). Every line a refusal is answered by is in the table, and is about that refusal: its code and
    field, the field _at or a dict literal sets, or each code an outside function can build."""
    found, outside = service_refusals()
    assert found == {key: SERVICE_REFUSAL_COUNTS.get(key, 1) for key in SERVICE_REFUSALS}
    assert outside == {f"<{name}>": codes for name, codes in OUTSIDE_CODES.items() if name != "match_kind"}
    for site, answer in SERVICE_REFUSALS.items():
        if isinstance(answer, str):
            assert answer.startswith(NOT_FROM_THE_FORM), site
            continue
        assert answer and all(key in web.GOAL_ERROR_WORDS for key in answer), site
        _, built, field = site
        if built in outside:  # each code the function can build, and no other
            assert {code for code, _ in answer} == outside[built], site
        elif built in ("<_at>", "<dict>"):  # the field set here
            assert {f for _, f in answer} == {field}, site
        else:  # rules.error, or a dict literal with its code: that code and field
            assert answer == [(built, field)], site


def test_the_reader_reads_a_bare_error_call_and_skips_only_a_dropped_refusal():
    """in_range calls error imported bare from rules, and its code is read. match_kind can build a refusal, but
    goals.py drops it in both its calls (`hit, _ =` and `[0]`), so it is no site."""
    from app.services import common  # noqa: PLC0415

    assert codes_built(rules.match_kind) == OUTSIDE_CODES["match_kind"]
    assert codes_built(common.in_range) == {"invalid_field"}  # in_range calls error, imported bare from rules
    found, _ = service_refusals()
    assert not [key for key in found if key[1] == "<match_kind>"]


def test_where_you_are_now_shows_only_for_its_refusal_or_when_typed(signed, real_set_goal):
    """Not on a fresh form, nor after a refusal about another field with none typed. With its own refusal it shows
    before the target, with the line under it. Typed, it shows with what was typed and no line."""
    for kind in FORM_FIELDS:
        assert "start_value" not in form_parts(signed.get(f"/goals?kind={kind}").text)["values"], kind
    form = form_parts(post(signed, "/goals", **filled({**FORM_WEIGHT, "target_value": "lots"})).text)
    assert "start_value" not in form["values"] and form["errors"]["target_value"]
    for kind, data in (("bodyweight", FORM_WEIGHT), ("lift", FORM_LIFT), ("cardio_best", FORM_RUN), ("measurement", FORM_WAIST)):
        form = form_parts(post(signed, "/goals", **filled(data)).text)
        start = (START_LABELS[kind], "input", "start_value")
        assert form["fields"] == FORM_FIELDS[kind][:-3] + [start] + FORM_FIELDS[kind][-3:], kind
        assert form["errors"]["start_value"] and form["values"]["start_value"] is None
        assert form["described"]["start_value"] == ("goal-start-error goal-start-hint" if kind == "cardio_best"
                                                     else "goal-start-error") and form["invalid"] == {"start_value"}
    form = form_parts(post(signed, "/goals", **filled({**FORM_WEIGHT, "target_value": "lots", "start_value": "80"})).text)
    assert form["values"]["start_value"] == "80" and form["errors"]["start_value"] is None
    assert form["fields"][0] == ("Where you are now (kg)", "input", "start_value")
    assert [r["error"] for r in real_set_goal] == ["invalid_field", "missing_field", "missing_field", "missing_field",
                                                   "missing_field", "invalid_field"]


@pytest.mark.parametrize("data", [
    {"kind": "bodyweight", "target_value": "lots", "target_date": "BY", "text": "for the wedding"},
    {"kind": "lift", "exercise": "Bench Press", "target_value": "lots", "target_date": "BY", "text": "by spring"},
    {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": "5", "target_value": "lots",
     "target_date": "BY", "text": "a 5 km race"},
    {"kind": "measurement", "measurement_kind": "resting_hr", "target_value": "lots", "target_date": "BY",
     "text": "calmer"},
], ids=lambda d: d["kind"])
def test_after_a_refusal_the_posted_kind_is_current_and_what_was_typed_stays(signed, real_set_goal, data):
    r = post(signed, "/goals", **filled(data))
    assert r.status_code == 400 and real_set_goal[0]["field"] == "target_value"
    form = form_parts(r.text)
    assert [s for s in form["segments"] if s[2]] == [(f"/goals?kind={data['kind']}#set-goal",
                                                      dict(SEGMENTS)[data["kind"]], "true")]
    assert ("kind", data["kind"]) in form["hidden"] and form["fields"] == FORM_FIELDS[data["kind"]]
    assert form["values"] == {k: v for k, v in filled(data).items() if k != "kind"}
    assert "Goal set." not in r.text and 'class="ok' not in r.text


@pytest.mark.parametrize("data", [
    {"kind": "bodyweight", "target_value": "75", "target_date": "BY", "text": "for the wedding"},
    {"kind": "lift", "exercise": "Back Squat", "target_value": "140", "target_date": "BY", "text": ""},
    {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": "5", "target_value": "27",
     "target_date": "BY", "text": ""},
    {"kind": "measurement", "measurement_kind": "waist", "target_value": "80", "target_date": "BY", "text": ""},
], ids=lambda d: d["kind"])
def test_a_goal_of_each_kind_set_from_its_own_form_ends_on_goals_with_goal_set(signed, data):
    """Each kind's own fields, with something logged to start from: the goal is set and the page says Goal set."""
    uid = only_user().id
    services.log_bodyweight(uid, {"kg": 80}, "claude")
    services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}]}, "claude")
    services.log_measurement(uid, {"kind": "waist", "value": "88"}, "claude")
    services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 31, "distance_km": 5}, "claude")
    form = form_parts(signed.get(f"/goals?kind={data['kind']}").text)
    assert {f for _, _, f in form["fields"]} == set(data) - {"kind"}
    r = post(signed, "/goals", **filled(data))
    assert r.status_code == 303 and r.headers["location"] == SITE + "/goals?saved=goal#top"
    assert '<p class="ok">Goal set.</p>' in signed.get(r.headers["location"]).text
    [goal] = rows(Goal, uid)
    assert (goal.kind, goal.status) == (data["kind"], "open")


def test_a_body_weight_goal_with_nothing_logged_asks_where_you_are_now_then_is_set(signed):
    uid = only_user().id
    r = post(signed, "/goals", **filled(FORM_WEIGHT))
    assert r.status_code == 400 and form_parts(r.text)["errors"]["start_value"]
    r = post(signed, "/goals", **filled({**FORM_WEIGHT, "start_value": "80"}))
    assert r.status_code == 303 and r.headers["location"] == SITE + "/goals?saved=goal#top"
    [goal] = rows(Goal, uid)
    assert (goal.start_value, goal.target_value, goal.direction) == (80, 75, "down")


def test_a_two_line_note_is_stored_with_lf(signed, real_set_goal):
    """The post reads through web.posted_form, as the edit pages do: a browser's CRLF is stored as LF. A refusal
    keeps the note's lines as LF too."""
    r = post(signed, "/goals", **filled({**FORM_WEIGHT, "start_value": "80", "text": "first line\r\nsecond line"}))
    assert r.status_code == 303
    [goal] = rows(Goal, only_user().id)
    assert goal.text == "first line\nsecond line"
    r = post(signed, "/goals", **filled({**FORM_WEIGHT, "target_value": "lots", "text": "one\r\ntwo\rthree"}))
    assert r.status_code == 400 and form_parts(r.text)["values"]["text"] == "one\ntwo\nthree"


def test_a_hint_and_an_error_on_one_field_are_both_tied_to_it(signed, real_set_goal):
    r = post(signed, "/goals", **filled({**FORM_RUN, "target_value": "700"}))
    form = form_parts(r.text)
    assert form["described"]["target_value"] == "goal-target-error goal-target-hint"
    assert form["hints"] == {"goal-target-hint": HINT} and form["invalid"] == {"target_value"}
    assert '<div class="field has-error">\n  <label for="goal-target">Target time (min)</label>' in \
        r.text.replace("\r\n", "\n")


def test_cardio_times_where_you_are_now_has_the_targets_hint_tied_to_it_with_its_error(signed, real_set_goal):
    """Cardio time's start field carries the target's hint (lane B's ruling, A4), tied to its input, and with an
    error under it both are tied. No other kind's start field has a hint."""
    form = form_parts(post(signed, "/goals", **filled({**FORM_RUN, "start_value": "0.5"})).text)
    assert form["errors"]["start_value"] == "Give your time now in minutes, from 1 to 600."
    assert form["described"]["start_value"] == "goal-start-error goal-start-hint"
    assert form["hints"] == {"goal-start-hint": HINT, "goal-target-hint": HINT} and form["invalid"] == {"start_value"}
    form = form_parts(post(signed, "/goals", **filled({**FORM_RUN, "start_value": "30", "target_value": "lots"})).text)
    assert form["described"] == {**form["described"], "start_value": "goal-start-hint",
                                 "target_value": "goal-target-error goal-target-hint"}
    for data in (FORM_WEIGHT, FORM_LIFT, FORM_WAIST):
        form = form_parts(post(signed, "/goals", **filled(data)).text)
        assert form["errors"]["start_value"] and form["described"]["start_value"] == "goal-start-error", data["kind"]
        assert "goal-start-hint" not in form["hints"], data["kind"]
    assert [r["error"] for r in real_set_goal] == ["invalid_field", "invalid_field"] + ["missing_field"] * 3


def test_the_grey_line_shows_only_while_where_you_are_now_is_not_shown(signed, real_set_goal):
    """Body weight's grey line says the goal starts from the 7-day average. Beside Where you are now, asked for or
    typed, the goal starts from the number given, so the line is not drawn (rev_b14 M2, the lead's ruling A3)."""
    fresh = form_parts(signed.get("/goals?kind=bodyweight").text)
    assert fresh["grey"] == [GREY] and "start_value" not in fresh["values"]
    other = form_parts(post(signed, "/goals", **filled({**FORM_WEIGHT, "target_value": "lots"})).text)
    assert other["grey"] == [GREY] and "start_value" not in other["values"]
    asked = form_parts(post(signed, "/goals", **filled(FORM_WEIGHT)).text)
    assert asked["errors"]["start_value"] == "Nothing is logged to start from yet: give your weight now, or log a " \
                                             "weigh-in first."
    assert asked["grey"] == []
    typed = form_parts(post(signed, "/goals",
                            **filled({**FORM_WEIGHT, "target_value": "lots", "start_value": "80"})).text)
    assert typed["values"]["start_value"] == "80" and typed["grey"] == []
    assert [(r["error"], r["field"]) for r in real_set_goal] == [
        ("invalid_field", "target_value"), ("missing_field", "start_value"), ("invalid_field", "target_value")]


@pytest.mark.parametrize("measurement", ["", "wrist"], ids=["none chosen", "one the page does not know"])
def test_a_measurement_with_no_target_and_none_known_chosen_asks_for_the_target_alone(signed, real_set_goal,
                                                                                    measurement):
    """With no measurement the page knows, the target's line names none: "Give the target you aim for."
    (web.GOAL_ERROR_UNNAMED), beside the measurement's own line."""
    r = post(signed, "/goals", **filled({"kind": "measurement", "measurement_kind": measurement, "target_date": "BY"}))
    [result] = real_set_goal
    assert {(e["error"], e["field"]) for e in problems(result)} == {
        ("missing_field" if not measurement else "invalid_kind", "measurement_kind"), ("missing_field", "target_value")}
    form = form_parts(r.text)
    assert {f: e for f, e in form["errors"].items() if e} == {"measurement_kind": "Choose a measurement from the list.",
                                                            "target_value": "Give the target you aim for."}
    assert web.GOAL_ERROR_UNNAMED == {("missing_field", "target_value"): "Give the target you aim for."}
    assert form["top"] == [] and services_words_shown(r.text, result) == []


def test_the_form_posts_to_its_card_and_a_set_goal_lands_at_the_top_with_no_autofocus(signed, real_set_goal):
    """A refused post comes back to /goals#set-goal, the form's action, so the window lands on the card. Every
    redirect that answers the post ends in #top, the id body carries, so the page after "Goal set." lands at its
    top with the strip in view and does not take on the post's #set-goal. Nothing on the page carries autofocus:
    with a fragment in the address a browser skips it. The status forms carry no fragment, and neither do their
    redirects."""
    from .test_pages_text import parse  # noqa: PLC0415

    def autofocused(page: str) -> list:
        return [n.tag for n in parse(page).iter() if "autofocus" in n.attrs]

    for kind in FORM_FIELDS:
        page = signed.get(f"/goals?kind={kind}").text
        assert form_parts(page)["form"] == ("post", "/goals#set-goal", ["goal-form"]) and autofocused(page) == []
    for data in (FORM_WEIGHT, FORM_LIFT, FORM_RUN, FORM_WAIST, {"target_value": "75", "target_date": "BY"}):
        r = post(signed, "/goals", **filled(data))
        assert r.status_code == 400 and form_parts(r.text)["form"][1] == "/goals#set-goal" and autofocused(r.text) == []
        assert 'id="set-goal"' in r.text
    r = post(signed, "/goals", **filled({**FORM_WEIGHT, "start_value": "80"}))
    assert r.status_code == 303 and r.headers["location"] == SITE + "/goals?saved=goal#top"
    page = signed.get(r.headers["location"]).text
    [body] = parse(page).find_all("body")
    assert body.attrs.get("id") == "top"
    assert [n.tag for n in parse(page).iter() if n.attrs.get("id") == "top"] == ["body"]  # the one id="top"
    assert '<p class="ok">Goal set.</p>' in page and autofocused(page) == []
    # A second for the same thing, with its note. Another target: the very same goal again is not set twice
    # (idempotency.md decision 12).
    again = post(signed, "/goals", **filled({**FORM_WEIGHT, "target_value": "74", "start_value": "80"}))
    assert again.headers["location"] == SITE + "/goals?saved=goal&notes=duplicate#top"
    first, second = [g.id for g in rows(Goal, only_user().id)]
    r = post(signed, f"/goals/{first}/status", status="dropped")
    assert r.headers["location"] == SITE + "/goals?saved=dropped"
    actions = re.findall(r'<form method="post" action="(/goals[^"]*)"', signed.get("/goals").text)
    assert sorted(actions) == sorted(["/goals#set-goal", *(f"/goals/{n}/status" for n in (first, second, second))])


def test_a_goal_post_with_no_person_behind_it_goes_to_sign_in_at_the_top(signed, capture_set_goal, monkeypatch):
    """The form's other redirect: a post whose person is gone (the token still good) goes to sign in, and it too
    ends in #top. A GET's sign-in redirect is as it was."""
    sent, _ = capture_set_goal

    async def nobody(request):
        return None

    monkeypatch.setattr(web, "current_user", nobody)
    r = post(signed, "/goals", **FORM_WEIGHT)
    assert r.status_code == 303 and r.headers["location"] == SITE + "/sign-in#top" and sent == []
    r = signed.get("/goals")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/sign-in"


MARKUP = '<b>x</b> & "q" \'a\''


@pytest.mark.parametrize("data", [
    {"kind": "bodyweight", "target_value": MARKUP, "target_date": MARKUP, "start_value": MARKUP, "text": MARKUP},
    {"kind": "lift", "exercise": MARKUP, "target_value": MARKUP, "target_date": MARKUP, "text": MARKUP},
    {"kind": "cardio_best", "exercise": MARKUP, "distance_km": MARKUP, "target_value": MARKUP,
     "target_date": MARKUP, "text": MARKUP + "\r\nline two"},
    {"kind": "measurement", "measurement_kind": MARKUP, "target_value": MARKUP, "target_date": MARKUP,
     "start_value": MARKUP, "text": MARKUP},
    {"kind": MARKUP, "target_value": MARKUP, "target_date": MARKUP, "text": MARKUP},
], ids=["bodyweight", "lift", "cardio", "measurement", "kind"])
def test_markup_typed_into_every_field_comes_back_as_typed_escaped_once(signed, real_set_goal, data):
    """rev_b14 T7, the reviewer's probe kept: markup typed into each field of each kind, and as the kind, is refused
    and comes back escaped once. Each field keeps what was typed (a choice that is not on its list keeps none)."""
    r = post(signed, "/goals", **data)
    assert r.status_code == 400 and real_set_goal[-1].get("error")
    once = str(escape(MARKUP))  # as the page escapes it
    assert "<b>x</b>" not in r.text and str(escape(once)) not in r.text and once in r.text
    form = form_parts(r.text)
    assert ("kind", web.goal_kind(data["kind"])) in form["hidden"]
    typed = {f: v.replace("\r\n", "\n") for f, v in data.items() if f != "kind"}
    assert {f: form["values"][f] for f in typed} == {f: None if f in form["options"] else v for f, v in typed.items()}


def sweep_posts() -> list[dict]:
    """Each kind's form as a person, or a hand, can fill it on an account with nothing logged: every field empty,
    not a number, each range's ends and past them, the other kind's names, and a start typed with the target above,
    below and at it, the start inside its range and at its ends. Every range is the service's own."""
    w_lo, w_hi = rules.BODYWEIGHT_KG
    l_lo, l_hi = goal_rules.TARGETS["lift"]
    t_lo, t_hi = goal_rules.TARGETS["cardio_best"]
    d_lo, d_hi = goal_rules.DISTANCE_KM
    s = str
    lift, run = {"kind": "lift", "exercise": "Back Squat"}, {"kind": "cardio_best", "exercise": "Outdoor Run",
                                                             "distance_km": "5"}
    out = [{"kind": "bodyweight", "target_date": ""}, {"kind": "bodyweight", "target_value": "lots"},
           {"kind": "bodyweight", "target_value": s(w_lo - 1)}, {"kind": "bodyweight", "target_value": s(w_hi + 1)},
           {"kind": "bodyweight", "target_value": "lots", "start_value": "eighty"}]
    out += [{"kind": "bodyweight", "start_value": s(start), "target_value": s(target)}
            for start, target in ((80, 75), (80, 85), (80, 80), (w_lo, w_lo), (w_hi, w_hi), (w_lo, w_hi), (w_hi, w_lo))]
    out += [{"kind": "lift", "target_date": ""}, {**lift, "exercise": "Outdoor Run"}, {**lift, "exercise": "Moonwalk"},
            {**lift, "target_value": s(l_lo)}, {**lift, "target_value": s(l_hi + 1)}, {**lift, "start_value": s(l_lo)}]
    out += [{**lift, "start_value": s(start), "target_value": s(target)}
            for start, target in ((100, 120), (100, 90), (100, 100), (l_hi, 600), (l_hi, l_hi), (1, l_hi))]
    out += [{"kind": "cardio_best", "target_date": ""}, {**run, "exercise": "Back Squat"},
            {**run, "exercise": "Moonwalk"}, {**run, "distance_km": s(d_lo / 2)}, {**run, "distance_km": s(d_hi + 1)},
            {**run, "distance_km": s(d_lo), "target_value": "lots"}, {**run, "target_value": s(t_lo / 2)},
            {**run, "target_value": s(t_hi + 1)}, {**run, "start_value": s(t_hi + 1)}]
    out += [{**run, "start_value": s(start), "target_value": s(target)}
            for start, target in ((30, 25), (30, 35), (30, 30), (t_lo, 5), (t_lo, t_lo), (t_hi, t_lo))]
    out += [{"kind": "measurement", "target_date": ""}, {"kind": "measurement", "measurement_kind": "wrist"},
            *({"kind": "measurement", "measurement_kind": daily, "target_value": "5"}
              for daily in goal_rules.DAILY_AMOUNTS)]
    for kind in goal_rules.GOAL_MEASUREMENTS:
        _, lo, hi, whole = rules.MEASUREMENTS[kind]
        given = {"kind": "measurement", "measurement_kind": kind}
        out += [{**given}, {**given, "target_value": s(lo - 1)}, {**given, "target_value": s(lo + 0.5)},
                {**given, "start_value": s(lo), "target_value": s(hi)},
                {**given, "start_value": s(hi), "target_value": s(hi)},
                {**given, "start_value": s(hi), "target_value": s(lo)},
                {**given, "start_value": s(hi + 1), "target_value": s(lo)}]
    return out


def test_each_kinds_form_through_real_set_goal_meets_no_refusal_without_a_line(signed, real_set_goal):
    """rev_b14 T1 and T4, by the service itself: which kinds can reach which refusal is not in the source. Every
    refusal set_goal gives a sweep of each kind's form has the page's own line for that kind, and so the general line
    never shows. The sweep reaches what it is for: the wrong way for a lift and a cardio time, inside the range and
    at its end; a target at the start for a body weight and a measurement; a body weight goal set either way."""
    reached, set_ = set(), []
    for data in sweep_posts():
        real_set_goal.clear()
        r = post(signed, "/goals", **filled({"target_date": "BY", **data}))
        [result] = real_set_goal
        if r.status_code == 303:
            set_.append((data["kind"], result["goal"]["direction"]))
            continue
        assert r.status_code == 400, (data, r.status_code)
        kind = web.goal_kind(data["kind"])
        for e in problems(result):
            reached.add((kind, e["error"], e.get("field")))
            assert web.goal_error_words(e, kind, data.get("measurement_kind") or "") is not None, (data, e)
        assert web.GOAL_ERROR_GENERAL not in form_parts(r.text)["top"], data
    assert {("lift", "wrong_direction", "target_value"), ("cardio_best", "wrong_direction", "target_value"),
            ("bodyweight", "target_is_start", "target_value"), ("measurement", "target_is_start", "target_value"),
            ("measurement", "invalid_field", "target_value"), ("cardio_best", "invalid_field", "distance_km"),
            ("lift", "invalid_field", "exercise"), ("cardio_best", "unknown_exercise", "exercise")} <= reached
    assert {("bodyweight", "up"), ("bodyweight", "down"), ("measurement", "up"), ("measurement", "down")} <= set(set_)


def test_a_real_goal_set_from_the_form(signed):
    tomorrow = (today_of(only_user()) + timedelta(days=60)).isoformat()
    r = post(signed, "/goals", kind="bodyweight", target_value="75", target_date=tomorrow, start_value="80")
    assert r.status_code == 303
    [goal] = rows(Goal, only_user().id)
    assert (goal.kind, goal.target_value, goal.start_value, goal.direction, goal.status) == \
        ("bodyweight", 75, 80, "down", "open")


def test_a_goal_of_every_kind_through_the_form_shows_on_the_page(signed):
    uid = only_user().id
    today = today_of(only_user())
    services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_kg": 80, "reps": 5}]}, "claude")
    services.log_measurement(uid, {"kind": "waist", "value": "88"}, "claude")
    services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 31, "distance_km": 5}, "claude")
    by = (today + timedelta(days=60)).isoformat()
    for form in ({"kind": "lift", "exercise": "Back Squat", "target_value": "100"},
                 {"kind": "bodyweight", "target_value": "75", "start_value": "80"},
                 {"kind": "measurement", "measurement_kind": "waist", "target_value": "80"},
                 {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": "5", "target_value": "28"}):
        assert post(signed, "/goals", target_date=by, **form).status_code == 303, form
    page = signed.get("/goals").text
    for label in ("Back Squat 100 kg", "Body weight 75 kg", "Waist 80 cm", "Outdoor Run 5 km in 28:00"):
        assert f"<strong>{label}</strong>" in page, label
    assert page.count('name="status" value="hit"') == 4


# ---------------------------------------------------------------- Hit, Drop, Reopen

@pytest.mark.parametrize("status, saved", [("hit", "Goal marked as hit."), ("dropped", "Goal dropped."),
                                           ("open", "Goal reopened.")])
def test_the_status_buttons_go_through_set_goal(signed, capture_set_goal, fake_goals, status, saved):
    sent, _ = capture_set_goal
    fake_goals()
    r = post(signed, "/goals/7/status", status=status)
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/goals?saved={status}"
    assert sent == [(only_user().id, {"goal_id": "7", "status": status}, "web")]
    assert calls(only_user().id) == [("set_goal", True, None)]
    assert f'<p class="ok">{saved}</p>' in signed.get(r.headers["location"]).text


def test_drop_then_reopen_a_real_goal(signed):
    uid = only_user().id
    made = services.set_goal(uid, {"kind": "bodyweight", "target_value": 75, "start_value": 80,
                                   "target_date": (today_of(only_user()) + timedelta(days=30)).isoformat()}, "claude")
    assert post(signed, f"/goals/{made['id']}/status", status="dropped").status_code == 303
    [goal] = rows(Goal, uid)
    assert goal.status == "dropped" and goal.closed_at is not None
    assert post(signed, f"/goals/{made['id']}/status", status="open").status_code == 303
    [goal] = rows(Goal, uid)
    assert goal.status == "open" and goal.closed_at is None


def test_someone_elses_goal_is_a_404_and_untouched(signed, make_user):
    other = make_user()
    theirs = services.set_goal(other.id, {"kind": "bodyweight", "target_value": 75, "start_value": 80,
                                          "target_date": "2099-01-01"}, "claude")
    for path in (f"/goals/{theirs['id']}/status", "/goals/abc/status", "/goals/99999/status"):
        r = post(signed, path, status="hit")
        assert r.status_code == 404 and "There is nothing here." in r.text
    assert [g.status for g in rows(Goal, other.id)] == ["open"]


def test_a_bad_status_is_refused_plainly(signed):
    uid = only_user().id
    made = services.set_goal(uid, {"kind": "bodyweight", "target_value": 75, "start_value": 80,
                                   "target_date": "2099-01-01"}, "claude")
    r = post(signed, f"/goals/{made['id']}/status", status="won")
    assert r.status_code == 400 and "That goal was not changed." in r.text and "GOAL_STATUSES" not in r.text
    assert [g.status for g in rows(Goal, uid)] == ["open"]


# ---------------------------------------------------------------- the write limit and CSRF

@pytest.mark.parametrize("path, data", [
    ("/goals", {"kind": "lift", "exercise": "Back Squat", "target_value": "100", "target_date": "2027-01-01"}),
    ("/goals/1/status", {"status": "hit"}),
], ids=["new goal", "status"])
def test_a_goal_write_over_the_limit_is_a_429(signed, monkeypatch, path, data):
    monkeypatch.setattr(services, "set_goal", lambda *args: dict(TOO_MANY))
    r = post(signed, path, **data)
    assert r.status_code == 429 and "Try again in 3 minutes." in r.text and "nothing was saved" in r.text
    assert calls(only_user().id)[-1] == ("set_goal", False, "too_many_writes")


@pytest.mark.parametrize("path", ["/goals", "/goals/1/status"])
def test_goal_forms_need_the_csrf_token(signed, capture_set_goal, path):
    sent, _ = capture_set_goal
    for data in ({"status": "hit", "kind": "lift"}, {"status": "hit", "kind": "lift", "csrf_token": "wrong"}):
        assert signed.post(path, data=data).status_code == 403
    assert sent == [] and calls(only_user().id) == []


def test_signed_out_goal_posts_are_refused(client, capture_set_goal):
    sent, _ = capture_set_goal
    assert client.post("/goals/1/status", data={"status": "hit", "csrf_token": "x"}).status_code == 403
    assert sent == []


# ================================================================ Progress: the Trends card

TRENDS = {
    "today": "2026-11-10",
    "bodyweight": {"now": 79.4, "smoothed": 79.12, "rate_per_week": -0.31,
                   "rate_window": {"from": "2026-10-13", "to": "2026-11-09", "days": 27}, "weigh_ins_28d": 25,
                   "series": [{"date": "2026-10-20", "kg": 80.1, "smoothed": 80.0},
                              {"date": "2026-11-09", "kg": 79.2, "smoothed": 79.12}]},
    "calories": {"avg_7d": 2150, "days_logged_7d": 6, "avg_28d": 2210, "days_logged_28d": 1,
                 "estimated_share_pct_28d": 12.5, "target_kcal": 2200},
    "sessions": {"this_week": 3, "target_per_week": 4, "per_week_4w": 2.8},
    "cardio": {"minutes_this_week": 95, "target_minutes": 150, "minutes_per_week_4w": 87.5},
    "maintenance": {"kcal": 2480, "intake_avg_kcal": 2210, "weight_change_kg": -0.74, "days_logged": 18,
                    "window_days": 21, "from": "2026-10-21", "to": "2026-11-10"},
    "maintenance_note": None, "how": "", "summary": "", "links": {},
}
EMPTY_TRENDS = {**TRENDS, "bodyweight": None, "calories": None, "maintenance": None,
                "sessions": {"this_week": 0, "target_per_week": None, "per_week_4w": 0},
                "cardio": {"minutes_this_week": 0, "target_minutes": None, "minutes_per_week_4w": 0},
                "maintenance_note": "Needs food logged on 14 more of the last 21 days."}


def trends_card(client, monkeypatch, trends) -> str:
    """Progress' Trends card with get_trends stood in for: its markup inside the card, whatever the card's tag, or
    "" when the page draws none."""
    monkeypatch.setattr(services, "get_trends", lambda *args: trends, raising=False)
    page = client.get("/progress").text
    found = re.search(r'<(div|section) class="card trends"[^>]*>(.*?)</\1>', page, re.S)
    return found.group(2) if found else ""


def trends_rows(card: str) -> list[tuple[str, str, str]]:
    """The Trends table's body rows as a person reads them: (quantity, value, remark), each cell's text squashed,
    a no-break space kept."""
    from .test_pages_text import parse, tables  # noqa: PLC0415 - test_pages_text imports test_web

    return [tuple(c.text() for c in row) for t in tables(parse(card)) for row in t.rows]


NB = " "
NO_WEIGH_INS = [("Smoothed trend", "", "No weigh-ins yet."), ("Weight rate", "", "No weigh-ins yet."),
                ("Weigh-ins", "", "No weigh-ins yet.")]
NO_FOOD = [(q, "", "No food logged in the last 28 days.")
           for q in ("Eaten a day", "Eaten a day", "From estimates", "Calorie target")]


def test_the_trends_card_shows_every_number(signed, monkeypatch):
    """Section L's Trends table, every figure through show: Quantity, Value, Remark, in the contract's order. A value
    cell holds a figure and its unit; the words are the remark's."""
    assert trends_rows(trends_card(signed, monkeypatch, TRENDS)) == [
        ("Smoothed trend", f"79.1{NB}kg", ""), ("7-day average", f"79.4{NB}kg", ""),  # 1 dp
        ("Weight rate", f"-0.31{NB}kg a week", "13 Oct to 9 Nov"), ("Weigh-ins", "25", "Last 28 days"),
        ("Eaten a day", f"2,150{NB}kcal", "Last 7 days, 6 logged"),
        ("Eaten a day", f"2,210{NB}kcal", "Last 28 days, 1 logged"),
        ("From estimates", "13%", "Last 28 days"),  # percent is whole
        ("Calorie target", f"2,200{NB}kcal", ""),
        ("Lift sessions", "3", "Last 7 days, target 4"), ("Lift sessions a week", f"2.8{NB}a week", "4-week average"),
        ("Cardio", f"95{NB}min", f"Last 7 days, target 150{NB}min"), ("Cardio a week", f"88{NB}min", "4-week average"),
        ("Maintenance", f"2,480{NB}kcal", "estimate 18 logged days, 21 Oct to 10 Nov")]


def test_the_trends_card_without_the_numbers(signed, monkeypatch):
    """No weigh-ins, no food in 28 days, no estimate: each value cell blank, the reason in its remark. Training
    always shows a number."""
    assert trends_rows(trends_card(signed, monkeypatch, EMPTY_TRENDS)) == [
        *NO_WEIGH_INS, *NO_FOOD,
        ("Lift sessions", "0", "Last 7 days"), ("Lift sessions a week", f"0{NB}a week", "4-week average"),
        ("Cardio", f"0{NB}min", "Last 7 days"), ("Cardio a week", f"0{NB}min", "4-week average"),
        ("Maintenance", "", "Needs food logged on 14 more of the last 21 days.")]


def test_the_trends_card_without_a_rate_or_a_week_of_food(signed, monkeypatch):
    trends = {**TRENDS, "bodyweight": {**TRENDS["bodyweight"], "rate_per_week": None, "rate_window": None},
              "calories": {**TRENDS["calories"], "avg_7d": None, "days_logged_7d": 0},
              "maintenance": None, "maintenance_note": "Needs weigh-ins in the last 7 days and around 21 days ago.",
              "note": "The weekly rate needs a weigh-in from 14 to 42 days ago."}
    shown = dict(((q, n), (v, r)) for n, (q, v, r) in enumerate(trends_rows(trends_card(signed, monkeypatch, trends))))
    # The service's own note, as written.
    assert shown[("Weight rate", 2)] == ("", "The weekly rate needs a weigh-in from 14 to 42 days ago.")
    assert shown[("Eaten a day", 4)] == ("", "No food logged in the last 7 days.")
    assert shown[("Eaten a day", 5)] == (f"2,210{NB}kcal", "Last 28 days, 1 logged")
    assert shown[("Maintenance", 12)] == ("", "Needs weigh-ins in the last 7 days and around 21 days ago.")
    assert "sample data" not in trends_card(signed, monkeypatch, trends)


def test_a_trends_failure_leaves_the_rest_of_progress(signed, monkeypatch):
    assert trends_card(signed, monkeypatch, {"error": "no_account", "message": "x", "fix": "y"}) == ""
    assert "No lifts logged yet" in signed.get("/progress").text


def test_the_progress_chart_draws_the_smoothed_line_and_keeps_the_average_in_the_text(signed, monkeypatch):
    uid = only_user().id
    today = today_of(only_user())
    for days_ago, kg in ((2, 80.0), (1, 81.0), (0, 79.0)):
        services.log_bodyweight(uid, {"kg": kg, "date": (today - timedelta(days=days_ago)).isoformat()}, "claude")
    real = web.progress_data.bodyweight_chart

    def with_smoothed(*args):
        chart = real(*args)
        chart["points"] = [{**p, "smoothed": s} for p, s in zip(chart["points"], (85.0, 85.2, 85.4))]
        return chart

    monkeypatch.setattr(web.progress_data, "bodyweight_chart", with_smoothed)
    page = signed.get("/progress").text
    assert '<polyline class="series smoothed"' in page and "The line is the smoothed trend, now 85.4 kg." in page
    # The card's own figure is the read's: the Trend callout is get_trends' smoothed weight, not the chart's. The
    # stand-in shows as 85.4, which the read's smoothed weight never does here (rev_b8 finding 6), so a page that
    # printed the chart's figure in the callout fails.
    smoothed = formatting.show(services.get_trends(uid)["bodyweight"]["smoothed"], "bodyweight_derived", unit=False)
    assert smoothed != "85.4"
    assert callouts_of(body_weight_card(page))[2] == (["is-key"], "Trend", f"{smoothed}\u00a0kg", "Smoothed")
    assert "7-day average" not in body_weight_card(page)
    assert "The line is the smoothed trend, which evens out day-to-day swings." in page


def test_the_real_trends_card(signed):
    uid = only_user().id
    today = today_of(only_user())
    for days_ago in range(0, 30, 2):
        services.log_bodyweight(uid, {"kg": 80 - days_ago * 0.02, "date": (today - timedelta(days=days_ago))
                                      .isoformat()}, "claude")
    trends = services.get_trends(uid)
    page = signed.get("/progress").text
    shown = {q: (v, r) for q, v, r in trends_rows(re.search(r'<section class="card trends"[^>]*>(.*?)</section>',
                                                              page, re.S).group(1))}
    smoothed = formatting.show(trends["bodyweight"]["smoothed"], "bodyweight_derived", page=True)
    assert shown["Smoothed trend"] == (smoothed, "")
    rate = trends["bodyweight"]["rate_per_week"]
    if rate is not None:
        assert shown["Weight rate"][0] == f"{formatting.show(rate, 'rate', page=True)}{NB}kg a week"
    assert '<polyline class="series smoothed"' in page


# ================================================================ review fixes

def test_no_goal_carries_a_sample_tag_open_or_closed(signed, fake_goals):
    """Decision 1: a goal's head holds its label and date alone, and a closed goal's tags its status alone, even
    when a stray is_sample key arrives."""
    fake_goals([numbers(1, is_sample=True), numbers(2, label="Body weight 75 kg")],
               [{**CLOSED[0], "is_sample": True}, CLOSED[1]])
    page = signed.get("/goals").text
    assert re.search(r'<strong>Back Squat 100 kg</strong></h2>\s*<p class="goal-by">by [^<]+</p>\s*</div>\s*</div>',
                     goal_item(page, 1))
    assert '<span class="entry-tags"><span class="tag tag-closed">Hit</span></span>' in goal_item(page, 9)
    assert "sample" not in page.casefold()


@pytest.mark.parametrize("warnings, shown", [
    (["There is already an open goal Back Squat 90 kg by 2027-01-01 (open) (id 3). Both are kept; if the new one "
      "replaces it, mark goal 3 dropped."], ["You already had an open goal like this one. Both are kept"]),
    (["start_value was ignored: the goal starts from the logged 80 kg (the 7-day average)."],
     ["The goal starts from what you have logged, not from the number you typed"]),
    (["exercise 'Back Squat' was ignored: only a lift or cardio_best goal has an exercise.",
      "distance_km '5' was ignored: only a cardio_best goal has one."],
     ["Fields this kind of goal does not use were left out."]),
    (["start_value was ignored: x.", "There is already an open goal y."],
     ["You already had an open goal", "The goal starts from what you have logged"]),
    ([], []),
], ids=["duplicate", "start", "ignored fields", "two", "none"])
def test_a_saved_goals_warnings_show_in_the_pages_own_words(signed, capture_set_goal, fake_goals, warnings, shown):
    _, state = capture_set_goal
    state["answer"] = {**state["answer"], "warnings": warnings}
    fake_goals()
    r = post(signed, "/goals", kind="lift", exercise="Back Squat", target_value="100", target_date="2027-03-31")
    assert r.status_code == 303 and r.headers["location"].startswith(SITE + "/goals?saved=goal")
    assert r.headers["location"].endswith("#top") and r.headers["location"].count("#") == 1
    page = signed.get(r.headers["location"]).text
    notes = re.findall(r'<p class="notice goal-note">(.*?)</p>', page)
    assert len(notes) == len(shown) and all(n.startswith(s) for n, s in zip(notes, shown))
    assert "start_value" not in page.split(SET_GOAL_H2)[0] and "mark goal 3" not in page


def test_a_reopened_goal_with_a_past_date_says_so(signed, capture_set_goal, fake_goals):
    _, state = capture_set_goal
    state["answer"] = {**state["answer"], "warnings": [
        "Reopened, but its target date 2026-01-01 has passed: give a new target_date, or the projection has nothing "
        "to aim at."]}
    fake_goals()
    r = post(signed, "/goals/4/status", status="open")
    assert r.headers["location"] == SITE + "/goals?saved=open&notes=past_date"
    page = signed.get(r.headers["location"]).text
    assert "Goal reopened." in page and "Its date has passed, so there is nothing to aim at" in page
    assert "target_date" not in page.split(SET_GOAL_H2)[0]


@pytest.mark.parametrize("notes", ["<b>x</b>", "duplicate,<script>", "x" * 100, "start,start"])
def test_a_forged_notes_address_shows_only_known_notes(signed, fake_goals, notes):
    fake_goals()
    page = signed.get("/goals", params={"saved": "goal", "notes": notes}).text
    found = re.findall(r'<p class="notice goal-note">(.*?)</p>', page)
    assert "<b>x" not in page and "<script>" not in page
    assert found == {"start,start": [web.GOAL_NOTE_TEXT["start"]],
                     "duplicate,<script>": [web.GOAL_NOTE_TEXT["duplicate"]]}.get(notes, [])
    assert re.findall(r'class="notice goal-note"', signed.get("/goals", params={"notes": "start"}).text) == []


def test_a_field_of_only_spaces_is_left_out(signed, capture_set_goal):
    sent, _ = capture_set_goal
    post(signed, "/goals", kind="bodyweight", exercise="   ", measurement_kind=" ", distance_km="  ",
         target_value=" 75 ", target_date="2027-03-31", start_value="   ", text="  ")
    assert sent[0][1] == {"kind": "bodyweight", "target_value": "75", "target_date": "2027-03-31"}


@pytest.mark.parametrize("rate, shown", [(0, "0"), (0.0, "0"), (0.004, "0"), (-0.004, "0"), (1.5, "+1.5"),
                                         (-0.25, "-0.25")])
def test_the_weekly_rate_carries_a_sign_and_zero_has_none(signed, monkeypatch, rate, shown):
    trends = {**TRENDS, "bodyweight": {**TRENDS["bodyweight"], "rate_per_week": rate}}
    card = trends_card(signed, monkeypatch, trends)
    assert ("Weight rate", f"{shown}{NB}kg a week", "13 Oct to 9 Nov") in trends_rows(card)
    assert "+0&nbsp;" not in card and "-0&nbsp;" not in card


def test_the_estimate_share_row_shows_0_percent_when_none_is_estimated(signed, monkeypatch):
    """The table has a From estimates row whatever the share: none estimated is 0%, a figure with its unit, not a
    line left out."""
    shown = trends_rows(trends_card(signed, monkeypatch, {
        **TRENDS, "calories": {**TRENDS["calories"], "estimated_share_pct_28d": 0}}))
    assert ("From estimates", "0%", "Last 28 days") in shown
    assert ("Eaten a day", f"2,150{NB}kcal", "Last 7 days, 6 logged") in shown


def test_now_is_named_the_latest_weigh_in_without_one_in_the_last_7_days(signed, monkeypatch):
    old = {**TRENDS, "bodyweight": {**TRENDS["bodyweight"], "now": 80.1,
                                    "series": [{"date": "2026-10-20", "kg": 80.1, "smoothed": 80.0}]}}
    card = trends_card(signed, monkeypatch, old)
    assert trends_rows(card)[1] == ("Latest weigh-in", f"80.1{NB}kg", "") and "7-day average" not in card


# ================================================================ Goals A (pass B12): each open goal as its own card

def goal_card(page: str, goal_id: int) -> dict:
    """One open goal card as a person reads it, part by part, each part's text squashed (a no-break space kept)."""
    from .test_pages_text import parse, tr  # noqa: PLC0415 - test_pages_text imports test_web

    [li] = [n for n in parse(page).find_all("li", "goal") if n.attrs.get("id") == f"goal-{goal_id}"]
    [head] = li.find_all("div", "goal-head")
    [now] = li.find_all("p", "goal-now")
    [standing] = li.find_all("dl", "standing")
    plan, projection = standing.elements()
    [plan_dd], [projection_dd] = plan.find_all("dd"), projection.find_all("dd")
    [tag] = projection_dd.find_all("span", "tag")
    [facts] = li.find_all("dl", "goal-facts")
    [actions] = li.find_all("div", "goal-actions")
    charts = li.find_all(cls="goal-chart")
    return {
        "chart": [(c.tag, [s.attrs.get("id") for s in c.find_all("svg")]) for c in charts],
        "legend": [[(k.find_all("span")[0].classes, k.text()) for k in p.elements()]
                   for c in charts for p in c.find_all("p", "legend")],
        "cells": [(dt.text(), _fact_value(dd), [s.text() for s in dd.find_all("span", "sub")], dd.classes)
                  for dt, dd in zip(facts.find_all("dt"), facts.find_all("dd"))],
        "parts": [(e.tag, e.classes) for e in li.elements()],
        "label": [h.text() for h in head.find_all("h2")], "strong": [s.text() for s in head.find_all("strong")],
        "by": [p.text() for p in head.find_all("p", "goal-by")], "head_tags": head.find_all("span", "tag"),
        "now": next((b.text() for b in now.find_all("span", "big")), None),
        "now_words": [f.text() for f in now.find_all("span", "figure-label")],
        "boxes": [b.find_all("dt")[0].text() for b in (plan, projection)],
        "plan_plain": plan_dd.classes == ["plain"],
        "heading": tr.squash("".join(c for c in plan_dd.children if isinstance(c, str))),
        "plan_lines": [m.text() for m in plan_dd.find_all("span", "means")],
        "tag": (tag.classes, tag.text()), "means": [m.text() for m in projection_dd.find_all("span", "means")],
        "facts": [dt.text() for dt in facts.find_all("dt")],
        "ask": [p.text() for p in li.elements() if p.is_("p", "goal-ask") or p.is_("p", "muted")],
        "actions": [(b.classes, b.text()) for b in actions.find_all("button")],
    }


def _fact_value(dd) -> str:
    """A fact's own value: the dd's text less the line under it (span.sub), a no-break space kept."""
    from .test_pages_text import tr  # noqa: PLC0415

    return tr.squash("".join(c for c in dd.children if isinstance(c, str)))


def one_card(signed, fake_goals, **values) -> dict:
    """The card of one faked goal_charts goal: numbers(1) with these values."""
    fake_goals([numbers(1, **values)])
    return goal_card(signed.get("/goals").text, 1)


WEIGHT = {"label": "Body weight 75 kg", "kind": "bodyweight", "exercise": None, "start_value": 80.4, "latest": 79.6,
          "direction": "down", "target_value": 75.0}
RUN = {"label": "Outdoor Run 5 km in 27:00", "kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5.0,
       "unit": "min", "start_value": 31.0, "latest": 29.5, "direction": "down", "target_value": 27.0,
       "should_be_today": 29.8, "plan_line": "29:30 now against 29:48 on the plan line today: 18 seconds ahead."}
WAIST = {"label": "Waist 80 cm", "kind": "measurement", "exercise": None, "measurement_kind": "waist", "unit": "cm",
         "start_value": 88.0, "latest": 86.5, "should_be_today": 87.0, "direction": "down", "target_value": 80.0}
HEART = {**WAIST, "label": "Resting heart rate 52 bpm", "measurement_kind": "resting_hr", "unit": "bpm",
         "start_value": 58.0, "latest": 57.0, "should_be_today": 57.6, "target_value": 52.0}


@pytest.mark.parametrize("values, basis, now, words", [
    (WEIGHT, "average_7d", f"79.6{NB}kg", "now, the 7-day average"),
    (WEIGHT, "latest_weigh_in", f"79.6{NB}kg", "now, the latest weigh-in"),
    ({}, "best_28d", f"85{NB}kg", "now, the best estimated max in 28 days"),
    ({}, "latest", f"85{NB}kg", "now, the latest session's estimated max"),
    (RUN, "best_28d", "29:30", f"now, the best 5{NB}km time in 28 days"),
    (RUN, "latest", "29:30", f"now, the latest 5{NB}km time"),
    ({**RUN, "distance_km": 10.55}, "latest", "29:30", f"now, the latest 10.55{NB}km time"),
    (WAIST, "latest", f"86.5{NB}cm", "now, the latest waist"),
    (HEART, "latest", f"57{NB}bpm", "now, the latest resting heart rate"),
], ids=["weight-average", "weight-latest", "lift-best", "lift-latest", "run-best", "run-latest", "run-10.55",
        "waist", "resting-hr"])
def test_the_now_figure_says_what_it_is_by_its_now_basis(signed, fake_goals, values, basis, now, words):
    """Section L's now table, each kind and now_basis: the figure at 44 px in the goal's unit, then its words."""
    card = one_card(signed, fake_goals, **{**values, "now_basis": basis})
    assert (card["now"], card["now_words"]) == (now, [words])


@pytest.mark.parametrize("vs_plan, ended, heading", [
    ("ahead", False, "Ahead of the plan line today"), ("on the line", False, "On the plan line today"),
    ("behind", False, "Behind the plan line today"), ("ahead", True, "Ahead of the target"),
    ("on the line", True, "At the target"), ("behind", True, "Behind the target"),
])
def test_the_heading_of_against_the_plan_line_is_vs_plan_and_line_ended(signed, fake_goals, vs_plan, ended, heading):
    """Each cell of the heading table. Under the heading, the goal's plan_line as written: the page builds none."""
    line = "a sentence the service wrote, as it wrote it."
    card = one_card(signed, fake_goals, vs_plan=vs_plan, line_ended=ended, plan_line=line, days_ahead=None)
    assert card["boxes"] == ["Against the plan line", "By projection"]
    assert (card["heading"], card["plan_lines"], card["plan_plain"]) == (heading, [line], False)


@pytest.mark.parametrize("vs_plan, days, shown", [
    ("ahead", 1, f"1{NB}day ahead"), ("behind", -1, f"1{NB}day behind"), ("ahead", 34, f"34{NB}days ahead"),
    ("behind", -2, f"2{NB}days behind"), ("ahead", 1234, f"1,234{NB}days ahead"), ("ahead", 0, None),
    ("behind", 0, None), ("ahead", None, None), ("behind", None, None),
])
def test_the_days_line_shows_only_when_days_ahead_is_neither_0_nor_null(signed, fake_goals, vs_plan, days, shown):
    card = one_card(signed, fake_goals, vs_plan=vs_plan, days_ahead=days)
    assert card["plan_lines"] == [numbers(1)["plan_line"]] + ([shown] if shown else [])


def test_on_the_plan_line_the_card_shows_the_figure_the_heading_and_plan_line_only(signed, fake_goals):
    """Rule 3: the now figure, the heading and plan_line, no days line, and no Plan line today row."""
    card = one_card(signed, fake_goals, latest=82.0, should_be_today=82.0, ahead_by=0.0, days_ahead=0,
                    vs_plan="on the line", plan_line="On the plan line today, at 82 kg.")
    assert card["now"] == f"82{NB}kg" and card["heading"] == "On the plan line today"
    assert card["plan_lines"] == ["On the plan line today, at 82 kg."]
    assert "Plan line today" not in card["facts"] and card["facts"] == ["Started", "Needed", "So far", "At this pace"]


def test_a_goal_with_nothing_logged_says_so_in_now_and_in_the_plan_box(signed, fake_goals):
    card = one_card(signed, fake_goals, status="not enough data",
                    note="A projection needs 4 points in the last 6 weeks.", **NOTHING_LOGGED)
    assert card["now"] is None and card["now_words"] == ["Nothing logged yet"]
    assert (card["plan_plain"], card["heading"], card["plan_lines"]) == (True, "Nothing logged yet", [])
    assert card["tag"] == (["tag", "tag-status", "tag-nodata"], "Not enough data")


ASK = "Ask your assistant how to reach this target by its date."  # the lead's ruling (pass B13)
TAG_WORDS = {"reached": "Reached", "on track": "On track", "behind": "Behind", "not enough data": "Not enough data"}


@pytest.mark.parametrize("status, tag, means", [
    ("reached", ["tag", "tag-status", "tag-good"], ["The target is reached."]),
    ("on track", ["tag", "tag-status", "tag-good"], ["At this pace the target is reached by the date."]),
    ("behind", ["tag", "tag-status", "tag-behind"], ["At this pace the target is not reached by the date."]),
    ("not enough data", ["tag", "tag-status", "tag-nodata"], ["A projection needs 4 points in the last 6 weeks."]),
])
def test_by_projection_shows_the_tag_once_and_what_it_means(signed, fake_goals, status, tag, means):
    """Each of the four tags with its meaning. Not enough data means the goal's own note. Only Behind adds the line
    after the facts, which names no figure of its own."""
    note = "A projection needs 4 points in the last 6 weeks." if status == "not enough data" else None
    fake_goals([numbers(1, status=status, note=note)])
    page = signed.get("/goals").text
    card = goal_card(page, 1)
    assert card["tag"] == (tag, TAG_WORDS[status]) and card["means"] == means
    assert card["ask"] == ([ASK] if status == "behind" else [])
    assert page.count(f">{TAG_WORDS[status]}</span>") == 1  # shown once
    if status == "behind":
        assert card["parts"][card["parts"].index(("dl", ["goal-facts"])) + 1] == ("p", ["goal-ask"])
        assert not re.search(r"\d", ASK) and "Back Squat" not in ASK


def test_a_note_shows_only_as_not_enough_datas_meaning(signed, fake_goals):
    card = one_card(signed, fake_goals, status="on track", note="A note the service gave.")
    assert "A note the service gave." not in str(card)


def test_the_now_figure_prints_what_plan_line_prints_widened_when_it_is(signed, fake_goals):
    """Rule 2: through show with page=True, widened exactly when formatting.widen([latest, should_be_today]) is."""
    wide = one_card(signed, fake_goals, **{**WEIGHT, "latest": 79.01, "should_be_today": 78.9566})
    assert wide["now"] == f"79.01{NB}kg"
    plain = one_card(signed, fake_goals, **{**WEIGHT, "latest": 79.01, "should_be_today": 78.5})
    assert plain["now"] == f"79{NB}kg"
    run = one_card(signed, fake_goals, **{**RUN, "latest": 29.5 + 0.4 / 60, "should_be_today": 29.5 - 0.4 / 60})
    assert run["now"] == "29:30.4"  # the two print 29:30, 0.8 s apart: tenths of a second
    assert formatting.widen([79.01, 78.9566], "bodyweight_derived")
    assert not formatting.widen([79.01, 78.5], "bodyweight_derived")


def test_the_head_holds_the_label_in_strong_and_the_date_under_it(signed, fake_goals):
    fake_goals([numbers(1), numbers(2)])
    page = signed.get("/goals").text
    card = goal_card(page, 1)
    assert (card["label"], card["strong"], card["by"], card["head_tags"]) == (
        ["Back Squat 100 kg"], ["Back Squat 100 kg"], ["by Tue 2 Feb 2027"], [])
    assert card["actions"] == [(["secondary"], "Hit"), (["link"], "Drop")]
    # A stand-in read with no plan draws no chart (numbers' path is empty): the card runs on without it.
    assert card["parts"] == [("div", ["goal-head"]), ("p", ["goal-now", "figure"]), ("dl", ["standing"]),
                               ("dl", ["goal-facts"]), ("div", ["goal-actions"])]


def goals_page_state(state: str, monkeypatch) -> int:
    """One Goals state on the fixed day for the signed-in person, and their id. "four weeks" and "edge" are the
    sweeps' accounts; "nothing logged" a body weight goal from a given start; "line ended" a goal ten days past its date;
    "no goals" a new account."""
    from .test_pages_text import edge_account, four_week_account  # noqa: PLC0415

    monkeypatch.setattr(db, "utcnow", lambda: FIXED_NOW)
    uid = only_user().id
    if state == "four weeks":
        four_week_account(uid)
    elif state == "edge":
        edge_account(uid)
    elif state == "nothing logged":
        services.set_goal(uid, {"kind": "bodyweight", "target_value": 75, "start_value": 80,
                                "target_date": "2027-01-05"}, "claude")
    elif state == "line ended":
        services.set_goal(uid, {"kind": "bodyweight", "target_value": 75, "start_value": 80,
                                "target_date": "2026-11-13"}, "claude")
        services.log_bodyweight(uid, {"kg": 79, "date": "2026-11-10"}, "claude")
        later = FIXED_NOW + timedelta(days=10)
        monkeypatch.setattr(db, "utcnow", lambda: later)
    return uid


GOAL_STATES = ("four weeks", "edge", "nothing logged", "line ended", "no goals")


def plan_words(g: dict) -> list[str]:
    """What the Against the plan line box holds under its heading, from the goal's own fields: plan_line as
    written, then the days line when days_ahead is neither 0 nor null."""
    days = g["days_ahead"]
    return [g["plan_line"]] + ([f"{formatting.show(abs(days), 'count')}{NB}{'day' if abs(days) == 1 else 'days'} "
                                f"{g['vs_plan']}"] if days else [])


@pytest.mark.parametrize("state", GOAL_STATES)
def test_each_card_on_the_page_is_its_goal_charts_goal(signed, monkeypatch, state):
    """Goal by goal, on the fixed day: each card's parts against goal_charts' own fields, every day worded against
    goal_charts' today, the figures through show. The open goals are goal_charts', in its order."""
    uid = goals_page_state(state, monkeypatch)
    charted = services.goal_charts(uid)
    page = signed.get("/goals").text
    found = re.findall(r'<li class="goal" id="goal-(\d+)">', page)
    assert found == [str(g["goal_id"]) for g in charted["goals"]]
    assert bool(found) == (state != "no goals")
    for g in charted["goals"]:
        card, q = goal_card(page, g["goal_id"]), web.goal_quantity(g)
        assert card["label"] == card["strong"] == [g["label"]]
        assert card["by"] == [f"by {formatting.day(g['target_date'], charted['today'])}"]
        assert card["head_tags"] == []
        assert card["tag"][1] == TAG_WORDS[g["status"]]
        assert card["ask"] == ([ASK] if g["status"] == "behind" else [])
        assert card["cells"] == fact_cells(g, charted["today"])
        assert card["chart"] == [("figure", [f"chart-goal-{g['goal_id']}"])]
        if g["latest"] is None:
            assert card["now"] is None and card["plan_plain"] and g["plan_line"] is None
            continue
        wide = formatting.widen([g["latest"], g["should_be_today"]], q)
        unit = "" if q == "goal_time" else f"{NB}{g['unit']}"
        assert card["now"] == formatting.show(g["latest"], q, page=True, widened=wide, unit=False) + unit
        assert card["heading"] == web.PLAN_HEADINGS[(g["vs_plan"], g["line_ended"])]
        assert card["plan_lines"] == plan_words(g)
        assert card["means"] == [g["note"] if g["status"] == "not enough data" else web.STATUS_MEANS[g["status"]]]


@pytest.mark.parametrize("state", ["four weeks", "edge", "line ended"])
def test_rule_2_on_the_real_page_the_now_figure_is_plan_lines(signed, monkeypatch, state):
    """Each real goal, on the four-week account (one widens), the edge account (every kind, on the line) and past its
    date: the now figure, its no-break space made plain, is the figure plan_line gives for now."""
    goals_page_state(state, monkeypatch)
    page = signed.get("/goals").text
    goals = services.goal_charts(only_user().id)["goals"]
    assert goals
    for g in goals:
        now = goal_card(page, g["goal_id"])["now"].replace(NB, " ")
        said = re.match(r"(?:On the plan line today, at )?(.+?)(?: now against | now, at the target\.|\.$)",
                        g["plan_line"]).group(1)
        assert now == said, (now, g["plan_line"])
    if state == "four weeks":
        assert goal_card(page, 1)["now"] == f"79.01{NB}kg"
    if state == "edge":
        assert {g["kind"] for g in goals} == {"bodyweight", "lift", "cardio_best", "measurement"}


def test_the_two_statements_disagree_on_the_four_week_body_weight_goal_and_both_show(signed, monkeypatch):
    """Section E1's case on the fixed day: behind the plan line today, and on track by projection."""
    goals_page_state("four weeks", monkeypatch)
    card = goal_card(signed.get("/goals").text, 1)
    assert card["label"] == ["Body weight 77 kg"]
    assert card["heading"] == "Behind the plan line today"
    assert card["plan_lines"] == ["79.01 kg now against 78.96 kg on the plan line today: 0.05 kg behind.",
                                  f"2{NB}days behind"]
    assert card["tag"] == (["tag", "tag-status", "tag-good"], "On track")
    assert card["means"] == ["At this pace the target is reached by the date."] and card["ask"] == []


def test_a_goal_past_its_date_says_the_target_and_no_days(signed, monkeypatch):
    goals_page_state("line ended", monkeypatch)
    [g] = services.goal_charts(only_user().id)["goals"]
    assert g["line_ended"] is True and g["days_ahead"] is None
    card = goal_card(signed.get("/goals").text, g["goal_id"])
    assert card["heading"] == "Behind the target"
    assert card["plan_lines"] == ["79 kg now against the target of 75 kg: 4 kg behind."]
    assert card["now_words"] == ["now, the latest weigh-in"]


@pytest.mark.parametrize("first, again", [("dropped", "dropped"), ("hit", "hit"), (None, "open")],
                         ids=["dropped twice", "hit twice", "open goal reopened"])
def test_a_status_set_to_what_it_already_is_writes_nothing_and_says_nothing(signed, first, again):
    """The second post changes nothing: no set_goal call, the address marked unchanged, the page calm with no saved
    words. The first post, when there is one, still says its words."""
    uid = only_user().id
    made = services.set_goal(uid, {"kind": "bodyweight", "target_value": 75, "start_value": 80,
                                   "target_date": (today_of(only_user()) + timedelta(days=30)).isoformat()}, "claude")
    if first:
        r = post(signed, f"/goals/{made['id']}/status", status=first)
        assert r.headers["location"] == SITE + f"/goals?saved={first}"
        assert f'<p class="ok">{web.GOAL_SAVED[first]}</p>' in signed.get(r.headers["location"]).text
    before = [g.closed_at for g in rows(Goal, uid)]
    writes = len(calls(uid))
    r = post(signed, f"/goals/{made['id']}/status", status=again)
    assert r.status_code == 303 and r.headers["location"] == SITE + "/goals?saved=unchanged"
    assert len(calls(uid)) == writes and [g.closed_at for g in rows(Goal, uid)] == before
    assert [g.status for g in rows(Goal, uid)] == [again]
    page = signed.get(r.headers["location"]).text
    assert 'class="ok' not in page and '<body class="calm" data-time-zone="missing" id="top">' in page


def test_a_status_that_changes_still_goes_through_set_goal(signed):
    uid = only_user().id
    made = services.set_goal(uid, {"kind": "bodyweight", "target_value": 75, "start_value": 80,
                                   "target_date": (today_of(only_user()) + timedelta(days=30)).isoformat()}, "claude")
    for status in ("hit", "open", "dropped", "hit"):
        r = post(signed, f"/goals/{made['id']}/status", status=status)
        assert r.headers["location"] == SITE + f"/goals?saved={status}", status
    assert calls(uid) == [("set_goal", True, None)] * 4


def test_the_frame_runs_head_strips_goals_closed_then_set_a_goal(signed, monkeypatch):
    """Section L's order, which is the markup order: the page head, then in div.goals-main the saved line, each goal note, ul.goals and the Closed goals card; Set a goal beside it, an aside named by its h2."""
    from .test_pages_text import parse  # noqa: PLC0415

    uid = goals_page_state("four weeks", monkeypatch)
    services.set_goal(uid, {"goal_id": 3, "status": "dropped"}, "claude")
    page = signed.get("/goals?saved=goal&notes=duplicate,start").text
    [main] = parse(page).find_all("main")
    shape = lambda n: (n.tag, n.classes, n.attrs.get("id"), n.attrs.get("aria-labelledby"))  # noqa: E731
    assert [shape(n) for n in main.elements()] == [
        ("div", ["page-head"], None, None), ("div", ["goals-main"], None, None),
        ("aside", ["card", "set-goal"], "set-goal", "set-goal-title")]
    head, goals_main, aside = main.elements()
    assert [(n.tag, n.text()) for n in head.elements()] == [("h1", "Goals")]
    assert [shape(n) for n in goals_main.elements()] == [
        ("p", ["ok"], None, None),
        ("p", ["notice", "goal-note"], None, None), ("p", ["notice", "goal-note"], None, None),
        ("ul", ["goals"], None, None), ("section", ["card"], None, "closed-goals-title")]
    assert [li.attrs["id"] for li in goals_main.elements()[3].elements()] == ["goal-1", "goal-2"]
    assert goals_main.elements()[3].attrs.get("aria-label") == "Open goals"
    assert [(h.tag, h.attrs.get("id")) for h in aside.elements()[:1]] == [("h2", "set-goal-title")]
    assert "Open goals</h2>" not in page and page.count('<li class="goal"') == 2


def test_the_page_reads_goal_charts_and_get_goals_of_one_day(signed, monkeypatch):
    """Read across the person's midnight the two reads can name two dates: both are read once more. If they still
    differ, goal_charts' date is the page's."""
    got, charted = [], []
    days = iter(["2026-12-31", "2027-01-01"])

    def listed(*args):
        got.append(args)
        return {**goals_result(closed=CLOSED), "today": next(days, "2027-01-01")}

    def charts(*args):
        charted.append(args)
        return {**charts_result([numbers(1)]), "today": "2027-01-01"}

    monkeypatch.setattr(services, "get_goals", listed)
    monkeypatch.setattr(services, "goal_charts", charts)
    page = signed.get("/goals").text
    assert len(got) == len(charted) == 2 and goal_card(page, 1)["by"] == ["by Tue 2 Feb"]
    got.clear()
    charted.clear()
    monkeypatch.setattr(services, "get_goals", lambda *a: got.append(a) or {**goals_result(closed=CLOSED),
                                                                             "today": "2026-12-31"})
    page = signed.get("/goals").text
    assert len(got) == len(charted) == 2 and goal_card(page, 1)["by"] == ["by Tue 2 Feb"]
    assert "Closed Thu 5 Nov 2026" in goal_item(page, 9)  # a closed day worded against goal_charts' 2027
    got.clear()
    charted.clear()
    fake_same = {**goals_result(closed=CLOSED), "today": "2027-01-01"}
    monkeypatch.setattr(services, "get_goals", lambda *a: got.append(a) or fake_same)
    signed.get("/goals")
    assert len(got) == len(charted) == 1  # one day at once: read once


def test_a_goal_charts_failure_is_a_404_page(signed, monkeypatch):
    monkeypatch.setattr(services, "goal_charts", lambda *a: {"error": "no_account", "message": "x", "fix": "y"},
                        raising=False)
    r = signed.get("/goals")
    assert r.status_code == 404 and "There is nothing here." in r.text


# ================================================================ Goals B (pass B13): the chart, the facts, closed

# A stand-in read's path: the plan from start to target through today, the actual line ending at today below the
# goal's latest (85), so the ring at today shows latest, not the series' last value.
PATH = {"plan": [{"date": "2026-11-01", "value": 80.0}, {"date": "2026-11-10", "value": 82.0},
                 {"date": "2027-02-02", "value": 100.0}],
        "actual": [{"date": "2026-11-01", "value": 80.0}, {"date": "2026-11-05", "value": 83.0},
                   {"date": "2026-11-10", "value": 84.0}]}
LEGEND = {"plan": (["key", "line", "plan"], "Plan line"), "line": (["key", "line"], "Actual"),
          "dot": (["key", "dot-key"], "Actual"), "projection": (["key", "line", "projection"], "Projection"),
          "today": (["key", "line", "today"], "Today")}


def fact_cells(g: dict, today: str) -> list[tuple]:
    """The facts list a goal card holds, worked out here from goal_charts' own fields: (label, value, the line under
    it, the dd's classes). Plan line today prints what plan_line prints (rule 2) and is left out on the line (rule 3);
    each missing fact has its own words; a reached goal's At this pace says so."""
    q = web.goal_quantity(g)
    wide = g["latest"] is not None and formatting.widen([g["latest"], g["should_be_today"]], q)

    def pace(value):
        if g["kind"] == "cardio_best":
            return formatting.show(value, "time_pace", page=True)
        return f"{formatting.show(value, 'rate', page=True)}{NB}{g['unit']} a week"

    cells = [("Started", formatting.show(g["start_value"], q, page=True), [formatting.day(g["start_date"], today)], [])]
    if g["vs_plan"] != "on the line":
        cells.append(("Plan line today", formatting.show(g["should_be_today"], q, page=True, widened=wide), [], []))
    for name, value, missing in (("Needed", g["needed_per_week"], "Not worked out yet"),
                                 ("So far", g["actual_per_week"], "Not enough data yet")):
        cells.append((name, pace(value), [], []) if value is not None else (name, missing, [], ["words"]))
    if g["status"] == "reached":
        cells.append(("At this pace", "Reached", [], ["words"]))
    elif g["projected_date"]:
        cells.append(("At this pace", formatting.day(g["projected_date"], today), ["reached"], []))
    else:
        cells.append(("At this pace", "No date yet", [], ["words"]))
    return cells


def svg_of(page: str, goal_id: int) -> str:
    return re.search(rf'<svg class="chart" id="chart-goal-{goal_id}".*?</svg>', page, re.S).group(0)


def chart_marks(svg: str) -> dict:
    """What a goal chart draws, read from its markup: the desc, each plain dot's and the ring's title, the series
    points, the plan line, the projection with its end dot, the today line and the target tick."""
    from .test_pages_text import parse  # noqa: PLC0415

    root = parse(svg)
    a = lambda n, *names: tuple(float(n.attrs[k]) for k in names)  # noqa: E731
    lines = {cls: [a(n, "x1", "y1", "x2", "y2") for n in root.find_all("line", cls)]
             for cls in ("plan", "projection", "today-line", "target-tick")}
    circles = root.find_all("circle")
    return {"desc": root.find_all("desc")[0].text(), **lines,
            "dots": [c.find_all("title")[0].text() for c in circles if c.classes == ["dot"]],
            "rings": [(a(c, "cx", "cy"), c.find_all("title")[0].text()) for c in circles if "is-latest" in c.classes],
            "ends": [a(c, "cx", "cy") for c in circles if "proj-end" in c.classes],
            "series": [p.attrs["points"].split() for p in root.find_all("polyline", "series")]}


def legend_of(g: dict) -> list[tuple]:
    """The legend a goal's chart calls for, from the read: the plan line and today always; Actual as a line from two
    points, a ringed dot from one; the projection when the chart has one to draw (contract, the goal path chart)."""
    actual = g["path"]["actual"]
    projected = g["latest"] is not None and bool(g["projected_date"]
                                                 or (g["projection"] or {}).get("projected") is not None)
    if len(actual) > 1:
        line = [LEGEND["line"]]
    else:
        line = [LEGEND["dot"]] if g["latest"] is not None else []
    return [LEGEND["plan"]] + line + ([LEGEND["projection"]] if projected else []) + [LEGEND["today"]]


@pytest.mark.parametrize("state", ["four weeks", "edge", "nothing logged", "line ended"])
def test_each_goal_chart_is_its_read_drawn_on_the_pages_today(signed, monkeypatch, state):
    """Each real goal on the fixed day: the chart is charts.goal_svg of goal_charts' own goal on goal_charts' today.
    Its dots are the read's path.actual, the ring at today holds latest, the plan line ends at the target tick, the
    projection runs from the ring to its end dot, the desc says plan_line word for word and its other figures
    unwidened, and the legend names what the chart draws."""
    uid = goals_page_state(state, monkeypatch)
    charted = services.goal_charts(uid)
    today, page = charted["today"], signed.get("/goals").text
    assert charted["goals"]
    for g in charted["goals"]:
        q, svg = web.goal_quantity(g), svg_of(page, g["goal_id"])
        assert svg == str(charts.goal_svg(g, today))
        drawn = chart_marks(svg)
        start = formatting.show(g["start_value"], q)
        assert drawn["desc"].startswith(f"Started {start} on {formatting.day(g['start_date'], today)}. "
                                        f"{g['plan_line'] or 'Nothing logged yet.'} Target ")
        assert drawn["dots"] == [f"{formatting.day(p['date'], today)}: {formatting.show(p['value'], q)}"
                                 for p in g["path"]["actual"] if p["date"] < today]
        rings = [title for _, title in drawn["rings"]]
        assert rings == ([f"{formatting.day(today, today)}: {formatting.show(g['latest'], q)}"]
                         if g["latest"] is not None else [])
        actual = g["path"]["actual"]
        assert [len(s) for s in drawn["series"]] == ([len(actual)] if len(actual) > 1 else [])
        [(_, _, plan_x2, plan_y2)] = drawn["plan"]
        [(_, tick_y, tick_x2, _)] = drawn["target-tick"]
        assert (plan_x2, plan_y2) == (tick_x2, tick_y)
        [(today_x, _, _, _)] = drawn["today-line"]
        if drawn["rings"]:
            [((ring_x, ring_y), _)] = drawn["rings"]
            assert ring_x == today_x
        if drawn["projection"]:
            [(x1, y1, x2, y2)] = drawn["projection"]
            assert (x1, y1) == (ring_x, ring_y) and drawn["ends"] == [(x2, y2)]
        else:
            assert drawn["ends"] == []
        assert goal_card(page, g["goal_id"])["legend"] == [legend_of(g)]


def test_the_four_week_charts_on_the_fixed_day(signed, monkeypatch):
    """The three goals of the four weeks: each a line of points with a projection to its projected date, and the body weight
    desc says the widened plan_line while its start stays unwidened."""
    goals_page_state("four weeks", monkeypatch)
    page = signed.get("/goals").text
    for goal_id in (1, 2, 3):
        drawn = chart_marks(svg_of(page, goal_id))
        assert len(drawn["series"]) == len(drawn["rings"]) == len(drawn["projection"]) == 1, goal_id
        assert goal_card(page, goal_id)["legend"] == [[LEGEND[k] for k in ("plan", "line", "projection", "today")]]
    assert chart_marks(svg_of(page, 1))["desc"] == (
        "Started 79.9 kg on Wed 14 Oct. 79.01 kg now against 78.96 kg on the plan line today: 0.05 kg behind. "
        "Target 77 kg by Tue 5 Jan 2027. Needed -0.25 kg a week. So far -0.29 kg a week. "
        "At this pace it is reached on Sat 26 Dec.")


def test_a_chart_with_one_point_draws_the_plan_line_one_ringed_dot_and_today(signed, monkeypatch):
    """The edge account's goals set today: one point at most in path.actual, the ring at today holding latest, no
    series line. With no projection there is no projection line and no Projection in the legend."""
    uid = goals_page_state("edge", monkeypatch)
    page = signed.get("/goals").text
    single = [g for g in services.goal_charts(uid)["goals"] if not g["projection"]]
    assert len(single) >= 3
    for g in single:
        assert len(g["path"]["actual"]) <= 1 and g["latest"] is not None and g["projected_date"] is None
        drawn = chart_marks(svg_of(page, g["goal_id"]))
        assert (len(drawn["plan"]), len(drawn["rings"]), len(drawn["today-line"])) == (1, 1, 1)
        assert drawn["series"] == drawn["dots"] == drawn["projection"] == drawn["ends"] == []
        assert goal_card(page, g["goal_id"])["legend"] == [[LEGEND["plan"], LEGEND["dot"], LEGEND["today"]]]


def test_a_chart_with_nothing_logged_draws_the_plan_line_and_today_only(signed, monkeypatch):
    uid = goals_page_state("nothing logged", monkeypatch)
    [g] = services.goal_charts(uid)["goals"]
    page = signed.get("/goals").text
    drawn = chart_marks(svg_of(page, g["goal_id"]))
    assert len(drawn["plan"]) == len(drawn["today-line"]) == len(drawn["target-tick"]) == 1
    assert drawn["rings"] == drawn["dots"] == drawn["series"] == drawn["projection"] == []
    assert "Nothing logged yet." in drawn["desc"]
    assert goal_card(page, g["goal_id"])["legend"] == [[LEGEND["plan"], LEGEND["today"]]]


def test_a_chart_draws_no_projection_without_one_and_one_to_the_date_without_a_projected_date(signed, fake_goals):
    """No projection and no projected date: no projection line, and the legend has no Projection. A projection
    without a projected date runs to the target date at the projected value (the contract's goal path rules), so
    the legend names it."""
    fake_goals([numbers(1, path=PATH, projected_date=None, projection=None, status="behind"),
                numbers(2, path=PATH, projected_date=None, projection={"projected": 95.0}, status="behind")])
    page = signed.get("/goals").text
    none, to_date = chart_marks(svg_of(page, 1)), chart_marks(svg_of(page, 2))
    assert none["projection"] == none["ends"] == [] and len(to_date["projection"]) == 1
    assert goal_card(page, 1)["legend"] == [[LEGEND["plan"], LEGEND["line"], LEGEND["today"]]]
    assert goal_card(page, 2)["legend"] == [[LEGEND["plan"], LEGEND["line"], LEGEND["projection"], LEGEND["today"]]]
    [(_, _, plan_x2, _)] = to_date["plan"]
    assert to_date["ends"][0][0] == plan_x2  # at the target date
    for goal_id in (1, 2):
        assert goal_card(page, goal_id)["cells"][-1] == ("At this pace", "No date yet", [], ["words"])
        assert "At this pace it is reached" not in chart_marks(svg_of(page, goal_id))["desc"]


def test_a_projected_date_far_past_the_target_is_held_on_the_chart_and_said_in_full(signed, fake_goals):
    """A projected date years past the target: the chart holds the line at half the plan's length past the target,
    inside the drawing, and the desc and At this pace keep the real day."""
    goal = numbers(1, path=PATH, projected_date="2029-06-01", status="behind")
    fake_goals([goal])
    page = signed.get("/goals").text
    assert svg_of(page, 1) == str(charts.goal_svg(goal, "2026-11-10"))
    drawn = chart_marks(svg_of(page, 1))
    [(end_x, _)] = drawn["ends"]
    [(_, _, target_x, _)] = drawn["plan"]
    assert target_x < end_x <= charts.GOAL_W
    assert drawn["desc"].endswith("At this pace it is reached on Fri 1 Jun 2029.")
    assert goal_card(page, 1)["cells"][-1] == ("At this pace", "Fri 1 Jun 2029", ["reached"], [])


def test_the_legend_is_a_p_of_spans_in_the_chart_block_hidden_from_a_reader(signed, fake_goals):
    """Inside a goal card the legend is p.legend of span items, each a key and its words, in the chart's
    figure.goal-chart after the svg, aria-hidden: the desc says it all. No li in the card."""
    from .test_pages_text import parse  # noqa: PLC0415

    fake_goals([numbers(1, path=PATH)])
    page = signed.get("/goals").text
    [figure] = parse(page).find_all("figure", "goal-chart")
    assert [(n.tag, n.classes) for n in figure.elements()] == [("svg", ["chart"]), ("p", ["legend"])]
    legend = figure.elements()[1]
    assert legend.attrs.get("aria-hidden") == "true"
    assert all(s.tag == "span" and [k.tag for k in s.elements()] == ["span"] for s in legend.elements())
    assert goal_card(page, 1)["legend"] == [[LEGEND[k] for k in ("plan", "line", "projection", "today")]]
    assert not re.search(r"<li\b", goal_item(page, 1))


def test_a_stand_in_read_with_no_plan_draws_no_chart_and_no_legend():
    assert web.goal_chart(numbers(1), "2026-11-10") == {"svg": "", "legend": []}
    assert web.goal_chart(numbers(1, path=PATH), "2026-11-10")["legend"] == [
        (" ".join(LEGEND[k][0]), LEGEND[k][1]) for k in ("plan", "line", "projection", "today")]


def test_the_chart_takes_the_pages_one_today(signed, monkeypatch):
    """get_goals and goal_charts name two days, then still two: the chart is drawn on goal_charts' today, the page's
    date, not get_goals'."""
    goal = numbers(1, path=PATH)
    monkeypatch.setattr(services, "get_goals", lambda *a: {**goals_result(), "today": "2026-11-09"})
    monkeypatch.setattr(services, "goal_charts", lambda *a: {**charts_result([goal]), "today": "2026-11-10"})
    page = signed.get("/goals").text
    assert svg_of(page, 1) == str(charts.goal_svg(goal, "2026-11-10")) != str(charts.goal_svg(goal, "2026-11-09"))


FACT_STATES = {
    "lift": {}, "behind no date": {"status": "behind", "projected_date": None},
    "nothing worked out": {"needed_per_week": None, "actual_per_week": None, "projected_date": None,
                           "status": "not enough data"},
    "reached": {"status": "reached"}, "reached no date": {"status": "reached", "projected_date": None},
    "weight widened": {**WEIGHT, "latest": 79.01, "should_be_today": 78.9566, "vs_plan": "behind",
                       "needed_per_week": -0.25, "actual_per_week": -0.29},
    "weight plain": {**WEIGHT, "latest": 79.01, "should_be_today": 78.5, "vs_plan": "behind"},
    "run widened": {**RUN, "latest": 29.5 + 0.4 / 60, "should_be_today": 29.5 - 0.4 / 60, "needed_per_week": -0.31,
                    "actual_per_week": -0.5},
    "waist": {**WAIST, "vs_plan": "behind"}, "heart": {**HEART, "vs_plan": "ahead"},
    "on the line": {"latest": 82.0, "should_be_today": 82.0, "ahead_by": 0.0, "days_ahead": 0,
                    "vs_plan": "on the line"},
    "nothing logged": {"status": "not enough data", "note": "A projection needs 4 points in the last 6 weeks.",
                       **NOTHING_LOGGED},
}


@pytest.mark.parametrize("state", sorted(FACT_STATES))
def test_each_fact_is_its_reads_field_cell_by_cell(signed, fake_goals, state):
    """The facts list, cell by cell, against the read's own fields: Started with its day under it, Plan line today
    (rule 2's figure, left out on the line), Needed and So far a week, At this pace with "reached" under it, and each
    missing fact's words."""
    values = FACT_STATES[state]
    card = one_card(signed, fake_goals, **values)
    assert card["cells"] == fact_cells(numbers(1, **values), "2026-11-10")


def test_the_facts_words_for_each_missing_fact_and_a_reached_goal(signed, fake_goals):
    card = one_card(signed, fake_goals, **FACT_STATES["nothing worked out"])
    assert [(c[0], c[1], c[3]) for c in card["cells"][2:]] == [("Needed", "Not worked out yet", ["words"]),
                                                               ("So far", "Not enough data yet", ["words"]),
                                                               ("At this pace", "No date yet", ["words"])]
    for values in (FACT_STATES["reached"], FACT_STATES["reached no date"]):
        card = one_card(signed, fake_goals, **values)
        assert card["cells"][-1] == ("At this pace", "Reached", [], ["words"])
    card = one_card(signed, fake_goals)
    assert card["cells"][0] == ("Started", f"80{NB}kg", ["Sun 1 Nov"], [])
    assert card["cells"][-1] == ("At this pace", "Mon 4 Jan 2027", ["reached"], [])


def test_plan_line_today_prints_the_figure_plan_line_prints_widened_and_not(signed, fake_goals):
    """Rule 2: Plan line today is the plan figure plan_line prints, widened exactly when the now figure is."""
    plan = lambda card: dict((c[0], c[1]) for c in card["cells"])["Plan line today"]  # noqa: E731
    wide = one_card(signed, fake_goals, **FACT_STATES["weight widened"])
    assert plan(wide) == f"78.96{NB}kg" and wide["now"] == f"79.01{NB}kg"
    plain = one_card(signed, fake_goals, **FACT_STATES["weight plain"])
    assert plan(plain) == f"78.5{NB}kg" and plain["now"] == f"79{NB}kg"
    run = one_card(signed, fake_goals, **FACT_STATES["run widened"])
    assert plan(run) == "29:29.6" and run["now"] == "29:30.4"


@pytest.mark.parametrize("state", ["four weeks", "edge", "line ended"])
def test_plan_line_today_on_the_real_page_is_the_plan_figure_plan_line_says(signed, monkeypatch, state):
    """Each real goal off the plan line: Plan line today, its no-break space made plain, is the plan figure in
    plan_line, "on the plan line today" or "the target of". On the line, no Plan line today row (rule 3)."""
    uid = goals_page_state(state, monkeypatch)
    page = signed.get("/goals").text
    goals = services.goal_charts(uid)["goals"]
    for g in goals:
        cells = dict((c[0], c[1]) for c in goal_card(page, g["goal_id"])["cells"])
        if g["vs_plan"] == "on the line":
            assert "Plan line today" not in cells
            continue
        said = re.search(r" now against (?:the target of )?(.+?)(?: on the plan line today)?: ", g["plan_line"])
        assert cells["Plan line today"].replace(NB, " ") == said.group(1), (cells, g["plan_line"])
    if state == "four weeks":
        assert dict((c[0], c[1]) for c in goal_card(page, 1)["cells"])["Plan line today"] == f"78.96{NB}kg"
    if state == "edge":
        assert {g["vs_plan"] for g in goals} == {"on the line"}


def test_rule_3_leaves_out_plan_line_today_on_the_line_only(signed, fake_goals):
    on = one_card(signed, fake_goals, **FACT_STATES["on the line"])
    assert on["facts"] == ["Started", "Needed", "So far", "At this pace"]
    for vs_plan in ("ahead", "behind"):
        off = one_card(signed, fake_goals, vs_plan=vs_plan)
        assert off["facts"] == ["Started", "Plan line today", "Needed", "So far", "At this pace"], vs_plan


def test_a_passed_target_date_makes_plan_line_today_the_target(signed, monkeypatch):
    """Ten days past the date: should_be_today is the target, so Plan line today is the target, as plan_line says,
    and Needed has nothing to work out."""
    uid = goals_page_state("line ended", monkeypatch)
    [g] = services.goal_charts(uid)["goals"]
    assert g["line_ended"] and g["should_be_today"] == g["target_value"] == 75
    card = goal_card(signed.get("/goals").text, g["goal_id"])
    assert card["cells"] == [("Started", f"80{NB}kg", ["Tue 10 Nov"], []), ("Plan line today", f"75{NB}kg", [], []),
                             ("Needed", "Not worked out yet", [], ["words"]),
                             ("So far", "Not enough data yet", [], ["words"]),
                             ("At this pace", "No date yet", [], ["words"])]


@pytest.mark.parametrize("state", GOAL_STATES)
def test_the_behind_line_holds_no_label_and_no_figure_in_every_state_that_shows_it(signed, monkeypatch, state):
    """Wherever a goal is Behind by projection, the one line straight after the facts, in its own p.goal-ask, in the
    lead's words: no label, no figure, nothing the plan box says. Nowhere else."""
    uid = goals_page_state(state, monkeypatch)
    page = signed.get("/goals").text
    goals = services.goal_charts(uid)["goals"]
    for g in goals:
        card = goal_card(page, g["goal_id"])
        if g["status"] != "behind":
            assert card["ask"] == [] and ("p", ["goal-ask"]) not in card["parts"]
            continue
        assert card["ask"] == [ASK]
        assert card["parts"][card["parts"].index(("dl", ["goal-facts"])) + 1] == ("p", ["goal-ask"])
        assert g["label"] not in ASK and not re.search(r"\d", ASK)
        assert "gap" not in ASK and "track" not in ASK
    assert any(g["status"] == "behind" for g in goals) == (state == "edge")
    assert page.count('class="goal-ask"') == sum(g["status"] == "behind" for g in goals)


def test_a_card_runs_head_now_standing_chart_facts_the_behind_line_and_actions(signed, fake_goals):
    fake_goals([numbers(1, path=PATH), numbers(2, path=PATH, status="behind")])
    page = signed.get("/goals").text
    parts = [("div", ["goal-head"]), ("p", ["goal-now", "figure"]), ("dl", ["standing"]), ("figure", ["goal-chart"]),
             ("dl", ["goal-facts"])]
    assert goal_card(page, 1)["parts"] == parts + [("div", ["goal-actions"])]
    assert goal_card(page, 2)["parts"] == parts + [("p", ["goal-ask"]), ("div", ["goal-actions"])]
    assert goal_card(page, 1)["chart"] == [("figure", ["chart-goal-1"])]


def closed_rows(page: str) -> list[dict]:
    """The Closed goals card's rows as a person reads them, part by part."""
    from .test_pages_text import parse  # noqa: PLC0415

    [card] = [s for s in parse(page).find_all("section", "card")
              if s.attrs.get("aria-labelledby") == "closed-goals-title"]
    [ul] = card.find_all("ul", "entries")
    out = []
    for li in ul.elements():
        main, tags, actions = li.elements()
        forms = [(f.attrs.get("action"),
                  [(i.attrs.get("name"), i.attrs.get("value")) for i in f.find_all("input")
                   if i.attrs.get("name") == "status"],
                  [(b.classes, b.text()) for b in f.find_all("button")]) for f in actions.find_all("form")]
        out.append({"li": (li.tag, li.classes, li.attrs.get("id")), "main": (main.tag, main.classes),
                    "parts": [(s.classes, s.text()) for s in main.elements()],
                    "tags": [(t.classes, t.text()) for t in tags.find_all("span", "tag")], "where": tags.classes,
                    "actions": actions.classes, "forms": forms})
    return out


def test_closed_goals_each_say_their_label_the_day_in_the_persons_zone_the_tag_and_reopen(signed, fake_goals):
    """Each closed goal: its label, "Closed {day}" in grey with the day in the person's own zone through day_utc
    (either side of their midnight), the tag Hit or Dropped as tag-closed, and Reopen as a link button."""
    services.update_settings(only_user().id, {"time_zone": "Asia/Kolkata"}, "web")
    closed = [{**CLOSED[0], "closed_at": "2026-11-05T18:29:00+00:00"},   # 23:59 on Thu 5 Nov in India
              {**CLOSED[1], "closed_at": "2026-11-05T18:31:00+00:00"}]  # 00:01 on Fri 6 Nov
    fake_goals([numbers(1)], closed)
    page = signed.get("/goals").text
    reopen = lambda n: [(f"/goals/{n}/status", [("status", "open")], [(["link"], "Reopen")])]  # noqa: E731
    assert closed_rows(page) == [
        {"li": ("li", ["entry-line"], "goal-9"), "main": ("div", ["entry-main"]),
         "parts": [(["entry-name"], "Bench Press 80 kg"), (["entry-figures"], "Closed Thu 5 Nov")],
         "tags": [(["tag", "tag-closed"], "Hit")], "where": ["entry-tags"], "actions": ["entry-actions"],
         "forms": reopen(9)},
        {"li": ("li", ["entry-line"], "goal-10"), "main": ("div", ["entry-main"]),
         "parts": [(["entry-name"], "Waist 85 cm"), (["entry-figures"], "Closed Fri 6 Nov")],
         "tags": [(["tag", "tag-closed"], "Dropped")], "where": ["entry-tags"],
         "actions": ["entry-actions"], "forms": reopen(10)}]
    assert "Bench Press 80 kg" in goal_item(page, 9) and "Waist 85 cm" in goal_item(page, 10)  # goal_item reads a row


def test_a_closed_goal_without_a_closing_time_says_no_day(signed, fake_goals):
    fake_goals([], [{**CLOSED[0], "closed_at": None}])
    [row] = closed_rows(signed.get("/goals").text)
    assert row["parts"] == [(["entry-name"], "Bench Press 80 kg")] and row["tags"] == [(["tag", "tag-closed"], "Hit")]


def test_no_open_goals_is_one_dashed_card_that_points_to_the_form_and_the_assistant(signed, fake_goals):
    from .test_pages_text import CLAUDE_SKIPS, parse  # noqa: PLC0415

    fake_goals()
    page = signed.get("/goals").text
    [main] = parse(page).find_all("div", "goals-main")
    empty = [n for n in main.elements() if n.is_(cls="card")]
    assert [(n.tag, n.classes, [(p.tag, p.classes, p.text()) for p in n.elements()]) for n in empty] == [
        ("div", ["card", "is-empty"], [("p", ["empty-line"], "No open goals. Set one, or tell your assistant "
                                                             "what you are aiming for.")])]
    assert '<ul class="goals"' not in page and "Claude" not in page.split("<main")[1]
    assert "/goals" not in CLAUDE_SKIPS


EMPTY_LINE = "No open goals. Set one, or tell your assistant what you are aiming for."  # the lead's ruling, A7


def test_no_open_goals_beside_closed_goals_is_the_empty_card_then_the_closed_card(signed, fake_goals):
    """rev_b13: with closed goals and none open, div.goals-main holds the one dashed card with its line, then the
    Closed goals card with both rows."""
    from .test_pages_text import parse  # noqa: PLC0415

    fake_goals([], CLOSED)
    page = signed.get("/goals").text
    [main] = parse(page).find_all("div", "goals-main")
    shape = [(n.tag, n.classes, n.attrs.get("aria-labelledby")) for n in main.elements()]
    assert shape == [("div", ["card", "is-empty"], None), ("section", ["card"], "closed-goals-title")]
    assert [(p.tag, p.classes, p.text()) for p in main.elements()[0].elements()] == [("p", ["empty-line"], EMPTY_LINE)]
    assert [row["li"][2] for row in closed_rows(page)] == ["goal-9", "goal-10"]


def test_a_closed_goals_label_and_a_charts_title_are_escaped_through_the_page(signed, fake_goals):
    """rev_b13: markup in a closed goal's label, and in an open goal's label where its chart's title says it, reaches
    the page escaped, once."""
    fake_goals([numbers(1, label="<script>x</script>", path=PATH)], [{**CLOSED[0], "label": "<script>y</script>"}])
    page = signed.get("/goals").text
    assert "<script>x" not in page and "<script>y" not in page
    assert "&lt;script&gt;y&lt;/script&gt;" in goal_item(page, 9) and "&amp;lt;" not in page
    svg = svg_of(page, 1)
    title = re.search(r"<title[^>]*>(.*?)</title>", svg, re.S).group(1)
    assert title.startswith("&lt;script&gt;x&lt;/script&gt; by ") and "<script" not in svg


def test_a_reopen_among_several_goals_reads_that_goal_alone(signed):
    """An open goal 12 beside a dropped goal 1: Reopen on goal 1 writes and says so; Reopen on goal 12 writes nothing.
    The status read is the one goal by its id (get_entry), its id parsed as set_goal parses it."""
    uid = only_user().id
    by = (today_of(only_user()) + timedelta(days=30)).isoformat()
    first = services.set_goal(uid, {"kind": "bodyweight", "target_value": 75, "start_value": 80, "target_date": by},
                              "claude")["id"]
    for n in range(11):  # each its own target: the same goal again is not set twice (idempotency.md decision 12)
        made = services.set_goal(uid, {"kind": "lift", "exercise": "Back Squat", "target_value": 140 + n,
                                       "start_value": 100, "target_date": by}, "claude")["id"]
    services.set_goal(uid, {"goal_id": first, "status": "dropped"}, "claude")
    assert (first, made) == (1, 12)
    writes = len(calls(uid))
    r = post(signed, f"/goals/{first}/status", status="open")
    assert r.headers["location"] == SITE + "/goals?saved=open" and len(calls(uid)) == writes + 1
    assert [g.status for g in rows(Goal, uid) if g.id == first] == ["open"]
    r = post(signed, f"/goals/{made}/status", status="open")
    assert r.headers["location"] == SITE + "/goals?saved=unchanged" and len(calls(uid)) == writes + 1
    r = post(signed, f"/goals/0{first}/status", status="open")
    assert r.headers["location"] == SITE + "/goals?saved=unchanged" and len(calls(uid)) == writes + 1


def test_another_persons_goal_is_never_read_as_unchanged(signed, make_user):
    """Their goal is open and the post says open: the read finds no goal of this person's by that id, so the post
    goes to set_goal, which answers not found. Their goal stays as it was."""
    me = only_user().id
    other = make_user()
    theirs = services.set_goal(other.id, {"kind": "bodyweight", "target_value": 75, "start_value": 80,
                                          "target_date": "2099-01-01"}, "claude")["id"]
    for status in ("open", "dropped"):
        r = post(signed, f"/goals/{theirs}/status", status=status)
        assert r.status_code == 404 and "There is nothing here." in r.text, status
    assert [g.status for g in rows(Goal, other.id)] == ["open"]
    assert [c[0] for c in calls(me)] == ["set_goal", "set_goal"]


def test_at_the_write_limit_a_status_that_changes_nothing_lands_as_unchanged(signed):
    """Ruled: nothing is written, so the post lands as unchanged, not refused. A status that changes is refused."""
    uid = only_user().id
    made = services.set_goal(uid, {"kind": "bodyweight", "target_value": 75, "start_value": 80,
                                   "target_date": (today_of(only_user()) + timedelta(days=30)).isoformat()},
                             "claude")["id"]
    with db.session_scope() as s:
        now = db.utcnow()
        s.add_all(CallLog(function="log_bodyweight", started_at=now, ms=1, ok=True, error_code=None, result_bytes=1,
                          user_id=uid) for _ in range(services.WRITE_LIMIT))
    writes = len(calls(uid))
    r = post(signed, f"/goals/{made}/status", status="open")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/goals?saved=unchanged"
    assert len(calls(uid)) == writes
    r = post(signed, f"/goals/{made}/status", status="dropped")
    assert r.status_code == 429 and [g.status for g in rows(Goal, uid)] == ["open"]
