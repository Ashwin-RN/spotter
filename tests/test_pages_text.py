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

The structure and furniture part of T3 (Notes columns, units in headers, estimate tags in their own slot,
remark columns, blank columns, the 404 and 500 pages) is step 4, and is not checked here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import db, services, web
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


ACCOUNTS = {"empty": empty_account, "sample": sample_account, "edge": edge_account}


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


# ---------------------------------------------------------------- the test

@pytest.mark.parametrize("account", sorted(ACCOUNTS))
def test_every_page_passes_the_page_rules(client, account):
    out = client.get("/")
    assert out.status_code == 200
    findings = page_findings("/", out.text, [], None)
    sign_in(client)
    user_id = only_user().id
    ACCOUNTS[account](user_id)
    entered = entered_text(user_id)
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
    assert rendered >= 12
    assert not findings, "\n".join(findings)


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
