"""Stage 5 on the web: the Goals page and its form, and the Trends card on Progress.

The page tests replace get_goals, get_trends and set_goal with the contract's result shapes, so they
test the pages whatever the services do. A few run the real services end to end.
"""

import html
import re
from datetime import timedelta

import pytest

from app import services, web
from app.models import Goal
from app.services.common import SAMPLE_NOTE

from .test_web import (  # noqa: F401 - fixtures
    CSP, SITE, TOO_MANY, FakeSeam, calls, client, only_user, post, rows, seam, session_of, sign_in,
    today_of, web_client,
)



def numbers(goal_id: int, **values) -> dict:
    """One goal-numbers row, the contract's shape."""
    base = {"goal_id": goal_id, "label": "Back Squat 100 kg", "kind": "lift", "exercise": "Back Squat",
            "measurement_kind": None, "distance_km": None, "unit": "kg", "start_value": 80.0,
            "start_date": "2026-11-01", "direction": "up", "target_value": 100.0, "target_date": "2027-02-02",
            "latest": 85.0, "status": "on track", "needed_per_week": 1.25, "actual_per_week": 2.5,
            "projected_date": "2027-01-04", "projection": None, "note": None}
    return {**base, **values}


FOUR = [
    numbers(1),
    numbers(2, label="Bodyweight 75 kg", kind="bodyweight", exercise=None, start_value=80.4, latest=79.6,
            direction="down", target_value=75.0, needed_per_week=-0.42, actual_per_week=-0.3, status="behind",
            projected_date=None, note="A projection needs 4 points in the last 6 weeks."),
    numbers(3, label="Waist 80 cm", kind="measurement", exercise=None, measurement_kind="waist", unit="cm",
            start_value=88.0, latest=None, direction="down", target_value=80.0, needed_per_week=None,
            actual_per_week=None, status="not enough data", projected_date=None),
    numbers(4, label="Outdoor Run 5 km in 27:00", kind="cardio_best", exercise="Outdoor Run", distance_km=5.0,
            unit="min", start_value=31.0, latest=29.5, direction="down", target_value=27.0, needed_per_week=-0.5,
            actual_per_week=-0.75, status="reached", projected_date="2026-12-01"),
]
CLOSED = [{"goal_id": 9, "label": "Bench Press 80 kg", "kind": "lift", "status": "hit",
           "closed_at": "2026-11-05T10:00:00+00:00", "target_value": 80.0, "target_date": "2026-12-01"},
          {"goal_id": 10, "label": "Waist 85 cm", "kind": "measurement", "status": "dropped",
           "closed_at": "2026-11-06T10:00:00+00:00", "target_value": 85.0, "target_date": "2026-12-01"}]


def goals_result(goals=(), closed=()) -> dict:
    return {"today": "2026-11-10", "goals": list(goals), "closed": list(closed), "includes_sample": False,
            "how": "", "summary": "", "links": {"goals": SITE + "/goals"}}


@pytest.fixture
def fake_goals(monkeypatch):
    """fake_goals(goals, closed): get_goals answers with these; the calls are recorded."""
    seen = []

    def install(goals=(), closed=()):
        result = goals_result(goals, closed)
        monkeypatch.setattr(services, "get_goals", lambda *args: seen.append(args) or result, raising=False)
        return seen

    return install


@pytest.fixture
def signed(client):
    sign_in(client)
    return client


def goal_item(page: str, goal_id: int) -> str:
    return re.search(rf'<li class="(?:goal|entry-line)" id="goal-{goal_id}">(.*?)</li>', page, re.S).group(1)


# ---------------------------------------------------------------- the page

def test_goals_with_none_offers_the_form_with_every_field(signed, fake_goals):
    seen = fake_goals()
    r = signed.get("/goals")
    assert r.status_code == 200 and "No open goals." in r.text and "Closed goals" not in r.text
    assert seen == [(only_user().id, True)]  # closed goals asked for too
    form = re.search(r'<form method="post" action="/goals">(.*?)</form>', r.text, re.S).group(1)
    assert 'name="csrf_token" value="' + session_of(signed)["csrf"] + '"' in form
    for field in web.GOAL_FIELDS:
        assert f'name="{field}"' in form, field
    kinds = re.search(r'<select id="goal-kind" name="kind" required>(.*?)</select>', form, re.S).group(1)
    assert re.findall(r'<option value="([a-z_]+)"', kinds) == ["lift", "bodyweight", "measurement", "cardio_best"]
    measured = re.search(r'<select id="goal-measurement" name="measurement_kind">(.*?)</select>', form, re.S).group(1)
    assert re.findall(r'<option value="([a-z_]*)"', measured) == ["", "waist", "hips", "chest", "arm", "thigh",
                                                                  "resting_hr"]
    chosen = re.search(r'<select id="goal-exercise" name="exercise">(.*?)</select>', form, re.S).group(1)
    names = [html.unescape(n) for n in re.findall(r'<option value="([^"]*)"', chosen)]
    lifts = [e["name"] for e in services.list_exercises("lift")["exercises"]]
    cardio = [e["name"] for e in services.list_exercises("cardio")["exercises"]]
    assert names == ["", *lifts, *cardio] and "Back Squat" in names and "Outdoor Run" in names
    assert 'name="target_date" type="date"' in form


def test_goals_shows_every_kind_with_its_numbers_and_buttons(signed, fake_goals):
    fake_goals(FOUR, CLOSED)
    page = signed.get("/goals").text
    token = session_of(signed)["csrf"]
    lift = goal_item(page, 1)
    assert "<strong>Back Squat 100 kg</strong> by 2027-02-02" in lift and "tag-good" in lift and ">on track<" in lift
    assert "<dd>80 kg on 2026-11-01</dd>" in lift and "<dd>85 kg</dd>" in lift
    assert "<dd>+1.25 kg a week</dd>" in lift and "<dd>+2.5 kg a week</dd>" in lift
    assert "<dd>reached on 2027-01-04</dd>" in lift
    weight = goal_item(page, 2)
    assert "tag-behind" in weight and "<dd>-0.42 kg a week</dd>" in weight and "<dd>-0.3 kg a week</dd>" in weight
    assert "no date" in weight and "A projection needs 4 points in the last 6 weeks." in weight
    waist = goal_item(page, 3)
    assert "Waist 80 cm" in waist and "<dd>88 cm on 2026-11-01</dd>" in waist and "nothing logged yet" in waist
    assert "not worked out" in waist and "not enough data yet" in waist and ">not enough data<" in waist
    run = goal_item(page, 4)
    assert "Outdoor Run 5 km in 27:00" in run and "<dd>31:00 min on 2026-11-01</dd>" in run
    assert "<dd>29:30 min</dd>" in run and "<dd>-0.5 min a week</dd>" in run and ">reached<" in run
    for goal_id in (1, 2, 3, 4):
        item = goal_item(page, goal_id)
        for status, label in (("hit", "Hit"), ("dropped", "Drop")):
            assert re.search(rf'<form method="post" action="/goals/{goal_id}/status" class="inline">\s*'
                             rf'<input type="hidden" name="csrf_token" value="{token}">\s*'
                             rf'<input type="hidden" name="status" value="{status}">\s*'
                             rf'<button class="link">{label}</button>', item), (goal_id, status)
        assert 'value="open"' not in item
    closed = page.split("<h2>Closed goals</h2>")[1].split("<h2>Set a goal</h2>")[0]
    assert "Bench Press 80 kg" in closed and ">hit<" in closed and "on 2026-11-05" in closed
    assert "Waist 85 cm" in closed and ">dropped<" in closed
    assert closed.count('name="status" value="open"') == 2 and closed.count(">Reopen<") == 2
    assert page.index("Open goals") < page.index("Closed goals") < page.index("Set a goal")


def test_goals_with_many(signed, fake_goals):
    fake_goals([numbers(n, label=f"Lift {n} 100 kg") for n in range(1, 41)])
    page = signed.get("/goals").text
    assert all(f"Lift {n} 100 kg" in page for n in range(1, 41))
    assert page.count('name="status" value="hit"') == 40 and page.count('name="status" value="dropped"') == 40


def test_goals_text_is_escaped(signed, fake_goals):
    fake_goals([numbers(1, label="<script>x</script>", note="<b>n</b>")])
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
    assert '<a href="/goals">Goals</a>' in r.text  # in the navigation


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
    assert r.status_code == 303 and r.headers["location"] == SITE + "/goals?saved=goal"
    assert sent == [(only_user().id, data, "web")]
    assert calls(only_user().id) == [("set_goal", True, None)]
    assert '<p class="ok">Goal set.</p>' in signed.get(r.headers["location"]).text


def test_the_form_cannot_send_a_goal_id_or_a_status(signed, capture_set_goal):
    sent, _ = capture_set_goal
    post(signed, "/goals", kind="lift", exercise="Back Squat", target_value="100", target_date="2027-03-31",
         goal_id="5", status="hit", user_id="99")
    assert sent[0][1] == {"kind": "lift", "exercise": "Back Squat", "target_value": "100", "target_date": "2027-03-31"}


def field_block(page: str, field: str) -> str:
    """What the page shows between a field and the next field: its error, if any."""
    ids = {"kind": "goal-kind", "exercise": "goal-exercise", "measurement_kind": "goal-measurement",
           "distance_km": "goal-distance", "target_value": "goal-target", "target_date": "goal-date",
           "start_value": "goal-start", "text": "goal-text"}
    after = page.split(f'id="{ids[field]}"', 1)[1]
    after = after.split("</select>", 1)[1] if field in ("kind", "exercise", "measurement_kind") else after
    return re.split(r"<label |<button ", after, maxsplit=1)[0]


REFUSALS = [
    ("kind", {"error": "invalid_field", "message": "kind 'x' is not a kind of goal.", "fix": "Use one of: lift."}),
    ("exercise", {"error": "unknown_exercise", "message": "No exercise called 'Squaty'.", "fix": "Closest: Back Squat."}),
    ("exercise", {"error": "missing_field", "message": "A lift goal needs an exercise.", "fix": "Give the name."}),
    ("measurement_kind", {"error": "invalid_field", "message": "steps cannot be a goal: it is a day's amount.",
                          "fix": "Use one of: waist, hips."}),
    ("measurement_kind", {"error": "invalid_kind", "message": "measurement_kind 'wrist' is not a measurement.",
                          "fix": "Use one of: waist."}),
    ("distance_km", {"error": "invalid_field", "message": "distance_km 500 is outside 0.1 to 300.",
                     "fix": "Give distance_km, 0.1 to 300."}),
    ("distance_km", {"error": "missing_field", "message": "A cardio_best goal needs distance_km.", "fix": "Give it."}),
    ("target_value", {"error": "invalid_field", "message": "target_value 900 is outside 20 to 400.",
                      "fix": "Give target_value in kg, 20 to 400."}),
    ("target_value", {"error": "wrong_direction", "message": "That target points the other way.", "fix": "Aim below."}),
    ("target_date", {"error": "date_not_in_future", "message": "target_date 2020-01-01 is not after today.",
                     "fix": "Give a target date after today."}),
    ("target_date", {"error": "invalid_date", "message": "That is not a date.", "fix": "Give YYYY-MM-DD."}),
    ("start_value", {"error": "missing_field", "message": "There is no weigh-in to start this goal from.",
                     "fix": "Log a weigh-in first, or give start_value: today's body weight in kg."}),
    ("text", {"error": "invalid_field", "message": "text is longer than 2,000 characters.", "fix": "Shorten it."}),
    ("form", {"error": "invalid_field", "message": "Something else went wrong.", "fix": "Try another way."}),
    ("start_value", {"error": "invalid_field", "field": "start_value", "message": "No field named here.",
                     "fix": "None here either."}),
]


@pytest.mark.parametrize("field, err", REFUSALS, ids=[f"{f}-{e['error']}-{i}" for i, (f, e) in enumerate(REFUSALS)])
def test_a_refusal_shows_the_services_message_and_fix_beside_its_field(signed, capture_set_goal, fake_goals,
                                                                       field, err):
    _, state = capture_set_goal
    state["answer"] = err
    fake_goals()
    r = post(signed, "/goals", kind="bodyweight", target_value="900", target_date="2020-01-01", text="mine")
    assert r.status_code == 400
    shown = f'<div class="error"><p>{err["message"]}</p><p>{err["fix"]}</p></div>'.replace("'", "&#39;")
    if field == "form":
        assert shown in r.text.split('<form method="post" action="/goals">')[0].split("<h2>Set a goal</h2>")[1]
    else:
        assert shown in field_block(r.text, field)
        others = [f for f in web.GOAL_FIELDS if f != field]
        assert all(shown not in field_block(r.text, f) for f in others)
    assert 'name="target_value" value="900"' in r.text and 'type="date" value="2020-01-01"' in r.text
    assert ">mine</textarea>" in r.text and '<option value="bodyweight" selected>' in r.text
    assert "Goal set." not in r.text


def test_every_refusal_of_a_combined_answer_is_shown(signed, capture_set_goal, fake_goals):
    _, state = capture_set_goal
    errors = [err for _, err in REFUSALS[:4]]
    state["answer"] = {"error": "invalid_field", "message": "Several fields.", "fix": "See errors.", "errors": errors}
    fake_goals()
    r = post(signed, "/goals", kind="lift")
    for err in errors:
        assert err["message"].replace("'", "&#39;") in r.text
    assert "Several fields." not in r.text


def test_a_real_refusal_from_set_goal_lands_beside_the_field(signed, fake_goals):
    fake_goals()
    tomorrow = (today_of(only_user()) + timedelta(days=1)).isoformat()
    r = post(signed, "/goals", kind="bodyweight", target_value="900", target_date=tomorrow)
    assert r.status_code == 400 and "target_value" in field_block(r.text, "target_value")
    r = post(signed, "/goals", kind="bodyweight", target_value="75", target_date="2020-01-01")
    assert r.status_code == 400 and "after today" in field_block(r.text, "target_date")
    r = post(signed, "/goals", kind="bodyweight", target_value="75", target_date=tomorrow)
    assert r.status_code == 400 and "start_value" in field_block(r.text, "start_value")
    r = post(signed, "/goals", kind="lift", target_value="100", target_date=tomorrow)
    assert r.status_code == 400 and "exercise" in field_block(r.text, "exercise")
    assert rows(Goal, only_user().id) == []
    assert [c[0] for c in calls(only_user().id)] == ["set_goal"] * 4


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
    for label in ("Back Squat 100 kg", "Bodyweight 75 kg", "Waist 80 cm", "Outdoor Run 5 km in 28:00"):
        assert f"<strong>{label}</strong>" in page, label
    assert page.count('name="status" value="hit"') == 4


def test_a_daily_amount_is_refused_beside_the_measurement_field(signed):
    by = (today_of(only_user()) + timedelta(days=60)).isoformat()
    r = post(signed, "/goals", kind="measurement", measurement_kind="steps", target_value="10000", target_date=by,
             start_value="5000")
    assert r.status_code == 400 and "a day&#39;s amount" in field_block(r.text, "measurement_kind")


# ---------------------------------------------------------------- Hit, Drop, Reopen

@pytest.mark.parametrize("status, saved", [("hit", "Marked as hit."), ("dropped", "Dropped."), ("open", "Reopened.")])
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
    "maintenance_note": None, "includes_sample": False, "how": "", "summary": "", "links": {},
}
EMPTY_TRENDS = {**TRENDS, "bodyweight": None, "calories": None, "maintenance": None,
                "sessions": {"this_week": 0, "target_per_week": None, "per_week_4w": 0},
                "cardio": {"minutes_this_week": 0, "target_minutes": None, "minutes_per_week_4w": 0},
                "maintenance_note": "Needs food logged on 14 more of the last 21 days."}


def trends_card(client, monkeypatch, trends) -> str:
    monkeypatch.setattr(services, "get_trends", lambda *args: trends, raising=False)
    page = client.get("/progress").text
    found = re.search(r'<div class="card trends">(.*?)</dl>', page, re.S)
    return found.group(1) if found else ""


def test_the_trends_card_shows_every_number(signed, monkeypatch):
    card = trends_card(signed, monkeypatch, TRENDS)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", card))
    assert "Smoothed trend 79.12 kg, 7-day average 79.4 kg." in text
    assert "-0.31 kg a week, from 2026-10-13 to 2026-11-09." in text
    assert "2150 kcal over the 6 logged days of the last 7; 2210 kcal over the 1 logged day of the last 28." in text
    assert "Target 2200 kcal." in text and "12.5% of it is estimates." in text
    assert "3 in the last 7 days, target 4; 2.8 a week over the last 4 weeks." in text
    assert "95 minutes in the last 7 days, target 150; 87.5 a week over the last 4 weeks." in text
    assert "About 2480 kcal a day, from 18 days with food logged between 2026-10-21 and 2026-11-10." in text
    assert "Your calorie target stays yours to set." in text


def test_the_trends_card_without_the_numbers(signed, monkeypatch):
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", trends_card(signed, monkeypatch, EMPTY_TRENDS)))
    assert "No weigh-ins yet." in text and "No food logged in the last 28 days." in text
    assert "Needs food logged on 14 more of the last 21 days." in text
    assert "0 in the last 7 days; 0 a week" in text and "target" not in text.split("Cardio")[1].split("Maintenance")[0]
    assert "About" not in text


def test_the_trends_card_without_a_rate_or_a_week_of_food(signed, monkeypatch):
    trends = {**TRENDS, "bodyweight": {**TRENDS["bodyweight"], "rate_per_week": None, "rate_window": None},
              "calories": {**TRENDS["calories"], "avg_7d": None, "days_logged_7d": 0},
              "maintenance": None, "maintenance_note": "Needs weigh-ins in the last 7 days and around 21 days ago.",
              "note": "The weekly rate needs a weigh-in from 14 to 42 days ago. " + SAMPLE_NOTE}
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", trends_card(signed, monkeypatch, trends)))
    assert "The weekly rate needs a weigh-in from 14 to 42 days ago." in text  # the service's own note
    assert "sample data" not in text  # the banner says that, not the rate line
    assert "kg a week" not in text and "None logged in the last 7 days" in text
    assert "Needs weigh-ins in the last 7 days and around 21 days ago." in text


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
        chart["points"] = [{**p, "smoothed": s} for p, s in zip(chart["points"], (80.0, 80.1, 79.99))]
        return chart

    monkeypatch.setattr(web.progress_data, "bodyweight_chart", with_smoothed)
    page = signed.get("/progress").text
    assert '<polyline class="series smoothed"' in page and "The line is the smoothed trend, now 79.99 kg." in page
    assert "7-day average: <strong>80 kg</strong>" in page  # the number in the text stays the 7-day average
    assert "The line is the smoothed trend, which steadies day-to-day swings." in page


def test_the_real_trends_card(signed):
    uid = only_user().id
    today = today_of(only_user())
    for days_ago in range(0, 30, 2):
        services.log_bodyweight(uid, {"kg": 80 - days_ago * 0.02, "date": (today - timedelta(days=days_ago))
                                      .isoformat()}, "claude")
    trends = services.get_trends(uid)
    page = signed.get("/progress").text
    assert f"Smoothed trend <strong>{trends['bodyweight']['smoothed']:g} kg</strong>" in page
    if trends["bodyweight"]["rate_per_week"] is not None:
        assert f"{web.signed(trends['bodyweight']['rate_per_week'])} kg a week" in page
    assert '<polyline class="series smoothed"' in page


# ================================================================ review fixes

def test_sample_goals_carry_a_sample_tag_open_and_closed(signed, fake_goals):
    fake_goals([numbers(1, is_sample=True), numbers(2, label="Bodyweight 75 kg", is_sample=False)],
               [{**CLOSED[0], "is_sample": True}, CLOSED[1]])
    page = signed.get("/goals").text
    assert '<strong>Back Squat 100 kg</strong> <span class="tag tag-sample">sample</span> by' in goal_item(page, 1)
    assert "tag-sample" not in goal_item(page, 2)
    assert 'Bench Press 80 kg <span class="tag tag-sample">sample</span>' in goal_item(page, 9)
    assert "tag-sample" not in goal_item(page, 10)  # no is_sample key: no tag


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
    page = signed.get(r.headers["location"]).text
    notes = re.findall(r'<p class="notice goal-note">(.*?)</p>', page)
    assert len(notes) == len(shown) and all(n.startswith(s) for n, s in zip(notes, shown))
    assert "start_value" not in page.split("<h2>Set a goal</h2>")[0] and "mark goal 3" not in page


def test_a_reopened_goal_with_a_past_date_says_so(signed, capture_set_goal, fake_goals):
    _, state = capture_set_goal
    state["answer"] = {**state["answer"], "warnings": [
        "Reopened, but its target date 2026-01-01 has passed: give a new target_date, or the projection has nothing "
        "to aim at."]}
    fake_goals()
    r = post(signed, "/goals/4/status", status="open")
    assert r.headers["location"] == SITE + "/goals?saved=open&notes=past_date"
    page = signed.get(r.headers["location"]).text
    assert "Reopened." in page and "Its date has passed, so there is nothing to aim at" in page
    assert "target_date" not in page.split("<h2>Set a goal</h2>")[0]


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


def test_the_cardio_nothing_to_start_from_refusal_lands_under_start_value():
    err = {"error": "missing_field", "message": "There is no Outdoor Run session of 5 km or more to start this goal "
           "from.", "fix": "Log one first (log_cardio with distance_km), or give start_value: the person's time for "
           "5 km now, in minutes."}
    assert web.goal_error_field(err) == "start_value"


def test_signed_zero_has_no_sign():
    assert (web.signed(0), web.signed(0.0), web.signed(1.5), web.signed(-0.25)) == ("0", "0", "+1.5", "-0.25")


def test_no_estimate_share_line_when_none_is_estimated(signed, monkeypatch):
    text = re.sub(r"<[^>]+>", "", trends_card(signed, monkeypatch, {
        **TRENDS, "calories": {**TRENDS["calories"], "estimated_share_pct_28d": 0}}))
    assert "of it is estimates" not in text and "2150 kcal" in text


def test_now_is_named_the_latest_weigh_in_without_one_in_the_last_7_days(signed, monkeypatch):
    old = {**TRENDS, "bodyweight": {**TRENDS["bodyweight"], "now": 80.1,
                                    "series": [{"date": "2026-10-20", "kg": 80.1, "smoothed": 80.0}]}}
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", trends_card(signed, monkeypatch, old)))
    assert "latest weigh-in 80.1 kg" in text and "7-day average" not in text
