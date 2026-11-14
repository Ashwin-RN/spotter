"""Stage 5 on the web, the account side: an empty account's Today, no banner and no tag,
the saved-food line, Foods through list_saved_foods, Export my data, Delete my account, and the
provider seam's delete_identity.

The page tests replace the services with the contract's result shapes. A few run the real services
end to end.
"""

import asyncio
import html
import inspect
import io
import re
import zipfile
from datetime import timedelta

import pytest
import workos
from markupsafe import escape
from sqlalchemy import select
from workos.user_management._resource import AsyncUserManagement

from app import db, formatting, services, web, web_auth
from app.formatting import show
from app.models import Bodyweight, CallLog, SavedFood, User

from .conftest import settings
from .test_pages_text import Node, parse, tables
from .test_web import (  # noqa: F401 - fixtures
    CSP, SITE, TOO_MANY, WEB, FakeSeam, calls, client, only_user, post, rows, seam, session_of, sign_in,
    entries_of, meal_of, side_card, stock, stock_food, today_of, user_count, web_client,
)


def plain(page: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", page))


@pytest.fixture
def signed(client):
    sign_in(client)
    return client


@pytest.fixture
def empty_state(monkeypatch):
    """empty_state(empty): what Today is told by account_empty."""
    def install(empty: bool):
        monkeypatch.setattr(services, "account_empty", lambda *a: {"empty": empty})

    return install


def recorder(monkeypatch, name: str, answer: dict) -> list:
    sent = []
    monkeypatch.setattr(services, name, lambda *args: sent.append(args) or dict(answer), raising=False)
    return sent


# ---------------------------------------------------------------- an empty account's Today: what to do first

FIRST_STEP = "Tell your assistant what you ate, lifted or did, and it shows here."


def test_an_empty_today_says_what_to_do_first_and_holds_no_form(signed):
    today = today_of(only_user()).isoformat()
    page = signed.get(f"/day/{today}").text
    hero = re.search(r'<section class="card hero".*?</section>', page, re.S).group(0)
    assert "Nothing logged yet" in hero and FIRST_STEP in plain(hero)
    assert "<form" not in hero and "<button" not in hero and "sample" not in page.casefold()


def test_the_first_step_shows_only_on_an_empty_accounts_today(signed, empty_state):
    today = today_of(only_user())
    yesterday = (today - timedelta(days=1)).isoformat()
    assert FIRST_STEP not in plain(signed.get(f"/day/{yesterday}").text)  # not on another day
    services.log_bodyweight(only_user().id, {"kg": "80", "date": yesterday}, "claude")
    assert FIRST_STEP not in plain(signed.get(f"/day/{today.isoformat()}").text)  # not once anything is there
    empty_state(True)
    assert FIRST_STEP in plain(signed.get(f"/day/{today.isoformat()}").text)  # the service decides


# ---------------------------------------------------------------- no banner, no tag

def test_no_row_carries_a_tag_beyond_its_own_even_with_a_stray_mark(signed, monkeypatch):
    """Decision 1: the sample tag is gone. A read that still carried is_sample (none does) would draw nothing more."""
    uid = only_user().id
    stock_food(only_user())
    services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]}, "claude")
    services.log_measurement(uid, {"kind": "waist", "value": "84"}, "claude")
    today = today_of(only_user()).isoformat()
    real = services.get_log

    def marked(*args):
        result = real(*args)
        for day in result["days"]:
            rows = [*day["sessions"], *day["measurements"], *(day.get("cardio") or []),
                    *(f for meal in ((day.get("food") or {}).get("meals") or {}).values() for f in meal)]
            rows += [day["weigh_in"]] if day.get("weigh_in") else []
            for row in rows:
                row["is_sample"] = True
        return result

    monkeypatch.setattr(services, "get_log", marked)
    page = signed.get(f"/day/{today}").text
    assert "sample" not in page.casefold()
    for title, name, tags in (("Lifts", "Lift session", []),
                              ("Cardio", "Treadmill Run", [("tag-estimate", "estimate")]),
                              ("Weigh-in", None, []), ("Measurements", "Waist", [])):
        [entry] = entries_of(side_card(page, title))
        assert (entry["name"], entry["tags"]) == (name, tags), title
    assert entries_of(side_card(page, "Weigh-in"))[0]["big"] == "80\u00a0kg"
    assert all(len(e["tags"]) == 1 for meal in ("Breakfast", "Lunch") for e in meal_of(page, meal))


def test_the_four_weeks_fill_the_pages_with_no_word_of_sample_data(signed):
    from evals.four_weeks import four_weeks  # noqa: PLC0415
    assert "error" not in four_weeks(only_user().id)
    today = today_of(only_user()).isoformat()
    for path in (f"/day/{today}", "/progress", "/goals", "/foods", "/settings"):
        page = signed.get(path).text
        assert "sample" not in page.casefold(), path
    assert signed.get("/goals").text.count('value="hit"') == 3
    assert "Nothing logged yet" not in signed.get(f"/day/{today}").text


# ---------------------------------------------------------------- the saved-food line (decision 14)

@pytest.mark.parametrize("servings, label, says", [
    (2, "1 roti", "2 rotis"), (1, "1 roti", "1 roti"), (1.5, "1 bowl", "1.5 bowls"), (2, "1 cup", "2 cups"),
    (2, "1 medium apple", "2 medium apples"), (2, "1 large egg", "2 large eggs"), (3, "1 small roti", "3 small rotis"),
    (2, "1 oz", "2 × 1 oz"), (2, "1 g", "2 × 1 g"), (2, "1 tbsp", "2 × 1 tbsp"), (2, "1 tsp", "2 × 1 tsp"),
    (2, "1 ml", "2 × 1 ml"), (2, "1 tomato", "2 × 1 tomato"), (2, "1 child", "2 × 1 child"),
    (2, "1 glass", "2 × 1 glass"), (3, "1 patty", "3 × 1 patty"), (2, "1 medium", "2 × 1 medium"),
    (2, "1 bowl dal", "2 × 1 bowl dal"), (2, "100 g", "2 × 100 g"), (1, "100 g", "100 g"), (1, "1 roti (40 g)", "1 roti (40 g)"),
    (0.5, "half plate", "0.5 × half plate"), (2, None, "2"), (2, "", "2"), (2, "  ", "2"),
])
def test_the_serving_in_its_own_words(servings, label, says):
    assert web.serving_text(servings, label) == says


@pytest.mark.parametrize("description, name, same", [
    ("Roti", "Roti", True), ("roti", "Roti", True), (" ROTI ", "Roti", True), ("Two rotis", "Roti", False),
    (None, "Roti", False),
])
def test_a_description_that_only_repeats_the_name_counts_as_the_name(description, name, same):
    assert web.same_name(description, name) is same


def test_a_lower_case_description_is_not_shown_beside_its_name(signed):
    uid = only_user().id
    fed = stock_food(only_user())
    services.log_food(uid, {"entries": [{"description": "roti", "saved_food_id": fed["roti"], "servings": 1,
                                         "meal": "dinner"}]}, "claude")
    [dinner] = meal_of(signed.get(f"/day/{fed['today']}").text, "Dinner")
    assert (dinner["name"], dinner["figures"], dinner["note"]) == ("Roti", "120\u00a0kcal, 1 roti", None)


def lunch_of(client, today: str) -> list[dict]:
    return meal_of(client.get(f"/day/{today}").text, "Lunch")


def test_a_saved_food_line_shows_a_different_description_once(signed):
    uid = only_user().id
    fed = stock_food(only_user())
    services.log_food(uid, {"entries": [{"description": "Two rotis with ghee", "saved_food_id": fed["roti"],
                                         "servings": 2, "meal": "lunch"}]}, "claude")
    lunch = [(e["name"], e["figures"], e["note"]) for e in lunch_of(signed, fed["today"]) if e["name"] == "Roti"]
    assert lunch == [("Roti", "240\u00a0kcal, 2\u00a0rotis", None),  # the description is the name
                     ("Roti", "240\u00a0kcal, 2\u00a0rotis", "Two rotis with ghee")]


def test_a_deleted_saved_food_still_names_its_entries(signed):
    fed = stock_food(only_user())
    services.delete_entry(only_user().id, "saved_food", fed["roti"])
    roti = lunch_of(signed, fed["today"])[0]
    assert (roti["name"], roti["figures"]) == ("Roti", "240\u00a0kcal, 2\u00a0rotis")


def test_a_food_with_no_saved_food_is_its_description(signed):
    fed = stock_food(only_user())
    dal = lunch_of(signed, fed["today"])[1]
    assert (dal["name"], dal["figures"], dal["note"]) == ("Dal", "180\u00a0kcal", None)


# ---------------------------------------------------------------- Foods reads list_saved_foods

FOODS = {"foods": [
    {"id": 11, "name": "Dal bowl", "serving_label": "1 bowl", "kcal_per_serving": 180, "grams_per_serving": None,
     "times_logged": 7, "last_logged": "2026-11-09", "deleted_at": None},
    {"id": 12, "name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120, "grams_per_serving": 40,
     "times_logged": 0, "last_logged": None, "deleted_at": None},
    {"id": 13, "name": "Gone stew", "serving_label": "1 bowl", "kcal_per_serving": 300, "grams_per_serving": None,
     "times_logged": 2, "last_logged": "2026-10-01", "deleted_at": "2026-11-01T00:00:00+00:00"},
], "summary": ""}


def test_foods_reads_list_saved_foods_and_shows_live_ones(signed, monkeypatch):
    sent = recorder(monkeypatch, "list_saved_foods", FOODS)
    page = signed.get("/foods").text
    assert sent == [(only_user().id,)]
    dal = re.search(r'<tr>\s*<td class="lead">Dal bowl</td>(.*?)</tr>', page, re.S)
    assert dal and re.findall(r'<td(?: class="num")? data-label="([^"]+)">(.*?)</td>', dal.group(1), re.S)[:3] == [
        ("Serving", "1 bowl"), ("kcal", "180"), ("Times logged", "7")]
    assert '<td class="lead">Roti</td>' in page and "Gone stew" not in page
    # The list is a stand-in and the database holds none of its foods, so the stamp the edit page reads first is a
    # stand-in too: with no stamp found a GET is the 404, and never a form with no stamp (edit-history.md).
    recorder(monkeypatch, "get_entry", {"updated_at": "2026-11-10T08:00:00+00:00"})
    assert signed.get("/foods/12/edit").status_code == 200 and signed.get("/foods/13/edit").status_code == 404


# ---------------------------------------------------------------- Foods, rebuilt (pass B15)

LEDE = ("Foods you save are logged by name and amount: Spotter works out the kcal. Saving one by talking to your "
        "assistant works too.")
EMPTY_FOODS = ("No saved foods yet. Tell your assistant a food you eat often and its number, and ask it to save the "
               "food.")
COLUMNS = ["Food", "Serving", "kcal", "Times logged", "Grams (g)", "Edit", "Delete"]
FOLD_FIELDS = ["name", "serving_label", "kcal_per_serving", "grams_per_serving"]
EDIT_FIELDS = FOLD_FIELDS[1:]
FIGURES = {"foods": [
    {"id": 21, "name": "Ghee rice", "serving_label": "1 plate", "kcal_per_serving": 412.25,
     "grams_per_serving": 1250.75, "times_logged": 1234, "last_logged": "2026-11-09",
     "deleted_at": None},
    {"id": 22, "name": "Black coffee", "serving_label": "1 cup", "kcal_per_serving": 0, "grams_per_serving": None,
     "times_logged": 0, "last_logged": None, "deleted_at": None},
    {"id": 23, "name": "Gone stew", "serving_label": "1 bowl", "kcal_per_serving": 300, "grams_per_serving": 10,
     "times_logged": 2, "last_logged": "2026-10-01", "deleted_at": "2026-11-01T00:00:00+00:00"},
], "summary": ""}


def foods_table(page: str) -> tuple[list[str], list[list[str]]]:
    """The Saved foods card's one table: its headers, then each row's cells, each as a person reads it."""
    [card] = parse(page).find_all("section", "saved-foods")
    [table] = tables(card)
    assert table.node.parent.is_("div", "table-wrap") and table.node.attrs.get("aria-labelledby") == "saved-foods-title"
    return table.headers, [[cell.text() for cell in row] for row in table.rows]


def main_parts(page: str) -> list[tuple[str, list[str]]]:
    """Each element in main, in markup order: its tag and classes."""
    [main] = parse(page).find_all("main")
    return [(n.tag, n.classes) for n in main.elements()]


def save_fold(page: str) -> Node:
    [details] = [d for d in parse(page).find_all("details", "add") if d.attrs.get("id") == "save-food"]
    return details


def strips(page: str) -> list[str]:
    """Every strip after a write: a p.ok or a div.ok, its words."""
    dom = parse(page)
    return [n.text().replace(" Undo", "") for n in dom.find_all("p", "ok") + dom.find_all("div", "ok")]


def control(page: str, name: str) -> Node:
    [found] = [n for n in parse(page).iter()
               if n.tag in ("input", "select", "textarea") and n.attrs.get("name") == name]
    return found


def field_lines(page: str, prefix: str) -> dict[str, str]:
    """Each field error under its field, by the field's name: the p.error its input names, the input invalid."""
    out = {}
    for error in parse(page).find_all("p", "error"):
        field = error.attrs["id"].removeprefix(prefix + "-").removesuffix("-error")
        tied = control(page, field)
        assert tied.attrs.get("aria-invalid") == "true", field
        assert error.attrs["id"] in tied.attrs.get("aria-describedby", "").split(), field
        out[field] = error.text()
    return out


def top_lines(page: str) -> list[str]:
    return [p.text() for d in parse(page).find_all("div", "error") for p in d.find_all("p")]


def saved_foods(user_id: int) -> list[tuple]:
    return [(f.name, f.serving_label, f.kcal_per_serving, f.grams_per_serving, f.deleted_at is None)
            for f in rows(SavedFood, user_id)]


@pytest.fixture
def real_save_food(monkeypatch):
    """Every answer save_food really gives through the page, in order."""
    answers, real = [], services.save_food
    monkeypatch.setattr(services, "save_food", lambda *args: answers.append(real(*args)) or answers[-1])
    return answers


def test_foods_in_the_contracts_order(signed):
    """Page head with its h1 and grey line, one strip, Saved foods, Save a food."""
    services.save_food(only_user().id, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    page = signed.get("/foods?saved=saved_food").text
    assert main_parts(page) == [("div", ["page-head"]), ("p", ["ok"]),
                                ("section", ["card", "saved-foods"]), ("div", ["card", "save-food"])]
    [head] = parse(page).find_all("div", "page-head")
    assert [(n.tag, n.classes, n.text()) for n in head.elements()] == [("h1", [], "Foods"), ("p", ["lede"], LEDE)]
    assert '<main class="page foods-page">' in page and "<title>Foods · Spotter</title>" in page


def test_the_saved_foods_columns_and_their_order_with_and_without_grams(signed, monkeypatch):
    recorder(monkeypatch, "list_saved_foods", FIGURES)
    headers, cells = foods_table(signed.get("/foods").text)
    assert headers == COLUMNS and len(cells) == 2  # the deleted food is not shown
    no_grams = {**FIGURES, "foods": [{**f, "grams_per_serving": None} for f in FIGURES["foods"]]}
    recorder(monkeypatch, "list_saved_foods", no_grams)
    page = signed.get("/foods").text
    headers, cells = foods_table(page)
    assert headers == [c for c in COLUMNS if c != "Grams (g)"] and {len(row) for row in cells} == {6}
    assert "Grams" not in page.split('id="saved-foods-title"')[1].split("</section>")[0]


def test_each_cell_is_its_reads_own_field_through_its_filter(signed, monkeypatch):
    """Cell by cell, against list_saved_foods: the name, the serving as written, kcal_per_serving,
    times_logged (count) and grams through show, a food with no grams an empty cell: no dash, no 0."""
    recorder(monkeypatch, "list_saved_foods", FIGURES)
    page = signed.get("/foods").text
    _, cells = foods_table(page)
    live = [f for f in FIGURES["foods"] if f["deleted_at"] is None]
    want = [[f["name"], f["serving_label"],
             show(f["kcal_per_serving"], "kcal_per_serving", page=True, unit=False),
             show(f["times_logged"], "count", page=True),
             "" if f["grams_per_serving"] is None else show(f["grams_per_serving"], "grams", page=True, unit=False),
             "Edit", "Delete"] for f in live]
    assert cells == want
    assert want[0][2:5] == ["412", "1,234", "1,250.75"] and want[1][2:5] == ["0", "0", ""]
    assert '<td class="lead">Black coffee</td>' in page and '<td class="lead">Ghee rice</td>' in page  # no tag
    row = re.search(r'<tr><td class="lead">Black coffee</td>.*?</tr>', page, re.S).group(0)
    assert '<td class="num" data-label="Grams (g)"></td>' in row and "–" not in row and \
        "-" not in re.sub(r'<[^>]+>', "", row)
    assert '<a class="action" href="/foods/22/edit">Edit</a>' in row
    assert re.search(r'<form method="post" action="/foods/22/delete" class="inline">\s*<input type="hidden" '
                     r'name="csrf_token" value="[^"]+">\s*<button class="link">Delete</button>\s*'
                     r'<input type="hidden" name="key" value="[^"]+">\s*</form>', row)


def test_no_saved_foods_is_a_dashed_card_and_the_fold_out_open(signed):
    page = signed.get("/foods").text
    assert main_parts(page) == [("div", ["page-head"]), ("div", ["card", "saved-foods", "is-empty"]),
                                ("div", ["card", "save-food"])]
    assert f'<div class="card saved-foods is-empty">\n  <p class="empty-line">{EMPTY_FOODS}</p>\n</div>' in \
        page.replace("\r\n", "\n")
    assert "<table" not in page and "open" in save_fold(page).attrs


def test_the_fold_out_is_closed_with_saved_foods_and_open_after_a_refusal(signed, real_save_food):
    services.save_food(only_user().id, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    for address in ("/foods", "/foods?saved=saved_food", "/foods?deleted=saved_food:1", "/foods?add=food"):
        assert "open" not in save_fold(signed.get(address).text).attrs, address
    r = post(signed, "/foods", name="Idli", serving_label="1 idli", kcal_per_serving="lots")
    assert r.status_code == 400 and "error" in real_save_food[-1] and "open" in save_fold(r.text).attrs


def test_the_save_a_food_form_holds_its_fields_in_order(signed):
    page = signed.get("/foods").text
    fold = save_fold(page)
    assert [n.text() for n in fold.elements() if n.tag == "summary"] == ["Save a food"]
    [form] = fold.find_all("form")
    assert (form.attrs["method"], form.attrs["action"]) == ("post", "/foods#save-food")
    fields = [(f.find_all("label")[0].text(), f.find_all("input")[0].attrs) for f in form.find_all("div", "field")]
    assert [(label, attrs["name"]) for label, attrs in fields] == [
        ("Name", "name"), ("One serving is", "serving_label"), ("Calories in one serving (kcal)", "kcal_per_serving"),
        ("Grams in one serving (g)", "grams_per_serving")]
    assert [attrs.get("inputmode") for _, attrs in fields] == [None, None, "decimal", "decimal"]
    assert [p.text() for p in form.find_all("p", "hint")] == ["Optional"]
    assert control(page, "grams_per_serving").attrs["aria-describedby"] == "food-grams_per_serving-hint"
    [button] = form.find_all("button")
    assert (button.classes, button.text()) == (["primary"], "Save food")
    assert [p.text() for p in form.find_all("p", "small")] == ["Saving a name you already have updates it."]
    assert [n.tag for n in form.elements()][-3:] == ["div", "p", "input"]  # the button, the grey line, the hidden key


@pytest.mark.parametrize("address, words", [
    ("/foods?saved=saved_food", ["Saved food saved."]), ("/foods?restored=saved_food", ["Saved food restored."]),
    ("/foods?deleted=saved_food:{id}", ["Saved food deleted."]),
    ("/foods?saved=unchanged", []), ("/foods?saved=1", []), ("/foods?deleted={id}", []),
    ("/foods?deleted=saved_food:0{id}", []), ("/foods?deleted=bodyweight:{id}", []), ("/foods?restored=1", []),
    ("/foods", []),
])
def test_each_mark_shows_its_one_strip_or_none(signed, address, words):
    food = services.save_food(only_user().id, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120},
                              "claude")["id"]
    page = signed.get(address.format(id=food)).text
    assert strips(page) == words
    if "deleted=saved_food:" in address and words:
        undo = re.search(r'<div class="ok undo">\s*Saved food deleted\.\s*<form method="post" '
                         rf'action="/foods/{food}/restore" class="inline">\s*<input type="hidden" name="csrf_token" '
                         r'value="([^"]+)">\s*<button class="link">Undo</button>\s*<input type="hidden" name="key" '
                         r'value="[^"]+">\s*</form>\s*</div>', page)
        assert undo and undo.group(1) == session_of(signed)["csrf"]


def test_a_save_a_delete_and_an_undo_land_on_their_strips(signed):
    uid = only_user().id
    r = post(signed, "/foods", name="Idli", serving_label="1 idli", kcal_per_serving="58", grams_per_serving="")
    assert r.headers["location"] == SITE + "/foods?saved=saved_food#top"
    assert strips(signed.get(r.headers["location"]).text) == ["Saved food saved."]
    [food] = rows(SavedFood, uid)
    r = post(signed, f"/foods/{food.id}/delete")
    assert r.headers["location"] == SITE + f"/foods?deleted=saved_food:{food.id}"
    page = signed.get(r.headers["location"]).text
    assert strips(page) == ["Saved food deleted."] and "<table" not in page  # the only food is gone
    r = post(signed, f"/foods/{food.id}/restore")
    assert r.headers["location"] == SITE + "/foods?restored=saved_food"
    page = signed.get(r.headers["location"]).text
    assert strips(page) == ["Saved food restored."] and foods_table(page)[1][0][0] == "Idli"
    assert calls(uid) == [("save_food", True, None), ("delete_entry", True, None), ("restore_entry", True, None)]


def test_a_food_saved_again_as_it_is_writes_nothing_and_says_nothing(signed):
    uid = only_user().id
    services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120,
                             "grams_per_serving": 40}, "claude")
    before = calls(uid)
    for data in ({"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": "120", "grams_per_serving": "40"},
                 {"name": " Roti ", "serving_label": " 1 roti ", "kcal_per_serving": "120.0",
                  "grams_per_serving": "40.00"}):
        r = post(signed, "/foods", **data)
        assert r.headers["location"] == SITE + "/foods?saved=unchanged#top", data
        assert strips(signed.get(r.headers["location"]).text) == []
    assert calls(uid) == before and saved_foods(uid) == [("Roti", "1 roti", 120, 40, True)]


def test_a_save_save_food_finds_unchanged_is_marked_unchanged(signed, real_save_food):
    """120.001 is stored as 120: the web cannot tell before the write, so it reads save_food's own food after it."""
    uid = only_user().id
    services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    real_save_food.clear()
    r = post(signed, "/foods", name="Roti", serving_label="1 roti", kcal_per_serving="120.001")
    assert r.headers["location"] == SITE + "/foods?saved=unchanged#top"
    assert [a["food"]["kcal_per_serving"] for a in real_save_food] == [120]
    assert calls(uid)[-1] == ("save_food", True, None)


@pytest.mark.parametrize("data, stored", [
    ({"kcal_per_serving": "130"}, ("Roti", "1 roti", 130, None, True)),
    ({"grams_per_serving": "40"}, ("Roti", "1 roti", 120, 40, True)),
    ({"name": "roti"}, ("roti", "1 roti", 120, None, True)),  # the name as typed is stored
    ({"serving_label": "1 large roti"}, ("Roti", "1 large roti", 120, None, True)),
])
def test_a_food_saved_again_with_one_change_is_saved(signed, data, stored):
    uid = only_user().id
    food = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    r = post(signed, "/foods", **{"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": "120", **data})
    assert r.headers["location"] == SITE + f"/foods?saved=saved_food:{food['id']}#top"
    assert saved_foods(uid) == [stored]


def test_a_deleted_food_saved_again_as_it_was_comes_back_and_is_saved(signed):
    uid = only_user().id
    food = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    services.delete_entry(uid, "saved_food", food["id"])
    r = post(signed, "/foods", name="Roti", serving_label="1 roti", kcal_per_serving="120")
    assert r.headers["location"] == SITE + "/foods?saved=saved_food#top"
    assert saved_foods(uid) == [("Roti", "1 roti", 120, None, True)]


# Each refusal save_food gives the fold-out, driven through the form: (row id, what is typed over a good food, the
# codes save_food answers with, the fields refused).
GOOD_FOOD = {"name": "Idli", "serving_label": "1 idli", "kcal_per_serving": "58", "grams_per_serving": ""}
FOLD_ROWS = [
    ("name missing", {"name": ""}, ["missing_field"], ["name"]),
    ("name too long", {"name": "x" * 129}, ["invalid_field"], ["name"]),
    ("name with a control character", {"name": "Idli\x07"}, ["invalid_field"], ["name"]),
    ("serving missing", {"serving_label": "  "}, ["missing_field"], ["serving_label"]),
    ("serving too long", {"serving_label": "y" * 65}, ["invalid_field"], ["serving_label"]),
    ("serving with a control character", {"serving_label": "1\x07idli"}, ["invalid_field"], ["serving_label"]),
    ("kcal missing", {"kcal_per_serving": ""}, ["missing_field"], ["kcal_per_serving"]),
    ("kcal not a number", {"kcal_per_serving": "lots"}, ["invalid_field"], ["kcal_per_serving"]),
    ("kcal above 5000", {"kcal_per_serving": "5001"}, ["invalid_field"], ["kcal_per_serving"]),
    ("kcal below 0", {"kcal_per_serving": "-1"}, ["invalid_field"], ["kcal_per_serving"]),
    # A typed comma or unit (rev_b15 M2), each refused as not a number and read with its field's line.
    ("kcal with a comma", {"kcal_per_serving": "1,000"}, ["invalid_field"], ["kcal_per_serving"]),
    ("kcal with a decimal comma", {"kcal_per_serving": "58,5"}, ["invalid_field"], ["kcal_per_serving"]),
    ("kcal with its unit", {"kcal_per_serving": "58 kcal"}, ["invalid_field"], ["kcal_per_serving"]),
    ("grams not a number", {"grams_per_serving": "some"}, ["invalid_field"], ["grams_per_serving"]),
    ("grams below 1", {"grams_per_serving": "0.5"}, ["invalid_field"], ["grams_per_serving"]),
    ("grams above 5000", {"grams_per_serving": "5001"}, ["invalid_field"], ["grams_per_serving"]),
    ("grams with a decimal comma", {"grams_per_serving": "2,5"}, ["invalid_field"], ["grams_per_serving"]),
    ("grams with its unit", {"grams_per_serving": "40g"}, ["invalid_field"], ["grams_per_serving"]),
    ("three at once", {"name": "", "kcal_per_serving": "lots", "grams_per_serving": "0"},
     ["missing_field", "invalid_field", "invalid_field"], ["name", "kcal_per_serving", "grams_per_serving"]),
]
FOOD_LINES = {
    "name": "Give the food a name, up to 128 characters.",
    "serving_label": "Say what one serving is, such as 1 roti, up to 64 characters.",
    "kcal_per_serving": "Give the kcal in one serving as a plain number with no comma, from 0 to 5,000\u00a0kcal.",
    "grams_per_serving": ("Give the grams in one serving as a plain number, such as 40, from 1 to 5,000\u00a0g, or "
                          "leave it empty."),
}


def service_words(answer: dict) -> list[str]:
    """Every message and fix of a refusal, the combined one and each problem's."""
    found = [answer] + [e for e in answer.get("errors") or []]
    return sorted({w for e in found for w in (e.get("message"), e.get("fix")) if w})


def assert_none_of_the_services_words(page: str, answer: dict) -> None:
    for words in service_words(answer):
        assert words not in page and html.escape(words) not in page, words


def assert_typed_kept(page: str, typed: dict) -> None:
    for name, value in typed.items():
        assert control(page, name).attrs.get("value", "") == value, name


@pytest.mark.parametrize("data, codes, fields", [row[1:] for row in FOLD_ROWS], ids=[row[0] for row in FOLD_ROWS])
def test_each_fold_out_refusal_shows_the_pages_own_line_under_its_field(signed, real_save_food, data, codes, fields):
    typed = {**GOOD_FOOD, **data}
    r = post(signed, "/foods", **typed)
    assert r.status_code == 400 and rows(SavedFood, only_user().id) == []
    [answer] = real_save_food
    assert [e["error"] for e in answer["errors"]] == codes
    assert [e["message"].split()[0] for e in answer["errors"]] == fields  # what the placement reads
    assert calls(only_user().id) == [("save_food", False, answer["error"])] and answer["error"] in codes
    assert field_lines(r.text, "food") == {f: FOOD_LINES[f] for f in fields}
    assert top_lines(r.text) == [] and strips(r.text) == []
    assert_none_of_the_services_words(r.text, answer)
    assert_typed_kept(r.text, typed)
    assert "open" in save_fold(r.text).attrs and '<body class="calm"' in r.text
    assert "<title>Foods · Spotter</title>" in r.text


EDIT_ROWS = [row for row in FOLD_ROWS if not row[0].startswith(("name", "three"))] + [
    ("two at once", {"serving_label": "", "grams_per_serving": "some"}, ["missing_field", "invalid_field"],
     ["serving_label", "grams_per_serving"]),
]
ROTI = {"serving_label": "1 roti", "kcal_per_serving": "120", "grams_per_serving": ""}


@pytest.mark.parametrize("data, codes, fields", [row[1:] for row in EDIT_ROWS], ids=[row[0] for row in EDIT_ROWS])
def test_each_edit_refusal_shows_the_pages_own_line_under_its_field(signed, real_save_food, data, codes, fields):
    uid = only_user().id
    food = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    real_save_food.clear()
    typed = {**ROTI, **data}
    r = post(signed, f"/foods/{food['id']}/edit", **typed)
    assert r.status_code == 400 and saved_foods(uid) == [("Roti", "1 roti", 120, None, True)]
    [answer] = real_save_food
    assert [e["error"] for e in answer["errors"]] == codes
    assert [e["message"].split()[0] for e in answer["errors"]] == fields
    assert calls(uid)[-1] == ("save_food", False, answer["error"]) and answer["error"] in codes
    assert field_lines(r.text, "edit") == {f: FOOD_LINES[f] for f in fields}
    assert top_lines(r.text) == []
    assert_none_of_the_services_words(r.text, answer)
    assert_typed_kept(r.text, typed)
    assert "<title>Edit Roti · Spotter</title>" in r.text and '<body class="calm"' in r.text


def test_a_refusal_with_no_field_of_its_own_goes_at_the_top_of_each_form(signed, real_save_food, monkeypatch):
    """save_food refuses a field the forms never send. Handed one, through the page, its real refusal sits at the
    top of the form in the page's own words, beside a field's own line when there is one."""
    real_form = web.food_form
    monkeypatch.setattr(web, "food_form", lambda form: {**real_form(form), "colour": "red"})
    uid = only_user().id
    r = post(signed, "/foods", **GOOD_FOOD)
    assert r.status_code == 400 and rows(SavedFood, uid) == []
    assert real_save_food[-1]["error"] == "invalid_field" and "'colour'" in real_save_food[-1]["message"]
    assert top_lines(r.text) == [web.FOOD_ERROR_GENERAL] and field_lines(r.text, "food") == {}
    assert "open" in save_fold(r.text).attrs
    assert_none_of_the_services_words(r.text, real_save_food[-1])
    r = post(signed, "/foods", **{**GOOD_FOOD, "kcal_per_serving": "lots"})
    assert top_lines(r.text) == [web.FOOD_ERROR_GENERAL]
    assert field_lines(r.text, "food") == {"kcal_per_serving": FOOD_LINES["kcal_per_serving"]}
    food = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    r = post(signed, f"/foods/{food['id']}/edit", **{**ROTI, "kcal_per_serving": "130"})
    assert r.status_code == 400 and real_save_food[-1]["error"] == "invalid_field"
    assert top_lines(r.text) == [web.FOOD_ERROR_GENERAL] and field_lines(r.text, "edit") == {}
    assert_none_of_the_services_words(r.text, real_save_food[-1])
    assert saved_foods(uid) == [("Roti", "1 roti", 120, None, True)]


def test_the_edit_page_head_card_and_fields(signed):
    uid = only_user().id
    food = services.save_food(uid, {"name": "Ghee rice", "serving_label": "1 plate", "kcal_per_serving": 412.25,
                                    "grams_per_serving": 250}, "claude")
    page = signed.get(f"/foods/{food['id']}/edit").text
    assert "<title>Edit Ghee rice · Spotter</title>" in page and '<main class="page form-page">' in page
    assert main_parts(page) == [("div", ["page-head"]), ("div", ["card"])]
    [head] = parse(page).find_all("div", "page-head")
    assert [(n.tag, n.text()) for n in head.elements()] == [("h1", "Edit Ghee rice"), ("nav", "Foods")]
    assert '<a class="back" href="/foods">Foods</a>' in page
    [card] = parse(page).find_all("div", "card")
    [form] = card.find_all("form")
    assert (form.attrs["method"], form.attrs["action"]) == ("post", f"/foods/{food['id']}/edit")
    fields = [(f.find_all("label")[0].text(), f.find_all("input")[0].attrs) for f in form.find_all("div", "field")]
    assert [(label, attrs["name"]) for label, attrs in fields] == [
        ("One serving is", "serving_label"), ("Calories in one serving (kcal)", "kcal_per_serving"),
        ("Grams in one serving (g)", "grams_per_serving")]
    stored = services.list_saved_foods(uid)["foods"][0]
    assert [attrs.get("value") for _, attrs in fields] == [
        stored["serving_label"], formatting.field(stored["kcal_per_serving"], "kcal_per_serving"),
        formatting.field(stored["grams_per_serving"], "grams")] == ["1 plate", "412.25", "250"]
    assert 'name="name"' not in page
    [actions] = form.find_all("div", "form-actions")
    assert [(n.tag, n.classes, n.text()) for n in actions.elements()] == [("button", ["primary"], "Save"),
                                                                       ("a", ["action"], "Cancel")]
    assert actions.elements()[1].attrs["href"] == "/foods"


def test_an_edit_that_changes_one_field_saves_it_and_one_that_changes_none_writes_nothing(signed, real_save_food):
    uid = only_user().id
    food = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    real_save_food.clear()
    r = post(signed, f"/foods/{food['id']}/edit", **{**ROTI, "grams_per_serving": "40"})
    assert r.headers["location"] == SITE + f"/foods?saved=saved_food:{food['id']}"
    assert saved_foods(uid) == [("Roti", "1 roti", 120, 40, True)]
    assert strips(signed.get(r.headers["location"]).text) == ["Saved food saved."]
    before = calls(uid)
    r = post(signed, f"/foods/{food['id']}/edit", serving_label=" 1 roti ", kcal_per_serving="120.00",
             grams_per_serving="40")
    assert r.headers["location"] == SITE + "/foods?saved=unchanged" and calls(uid) == before
    assert strips(signed.get(r.headers["location"]).text) == []
    # 120.004 is stored as 120: written, and save_food's own food says nothing changed.
    r = post(signed, f"/foods/{food['id']}/edit", serving_label="1 roti", kcal_per_serving="120.004",
             grams_per_serving="40")
    assert r.headers["location"] == SITE + "/foods?saved=unchanged" and len(calls(uid)) == len(before) + 1
    assert len(real_save_food) == 2 and saved_foods(uid) == [("Roti", "1 roti", 120, 40, True)]
    # Emptying the grams clears them.
    r = post(signed, f"/foods/{food['id']}/edit", serving_label="1 roti", kcal_per_serving="120", grams_per_serving="")
    assert r.headers["location"] == SITE + f"/foods?saved=saved_food:{food['id']}"
    assert saved_foods(uid) == [("Roti", "1 roti", 120, None, True)]


def test_another_persons_food_is_a_404_on_every_address_and_nothing_of_it_shows(signed, make_user):
    mine = only_user().id
    other = make_user()
    theirs = services.save_food(other.id, {"name": "Secret stew", "serving_label": "1 bowl",
                                           "kcal_per_serving": 321}, "claude")["id"]
    services.delete_entry(other.id, "saved_food", theirs)
    gone = [signed.get(f"/foods/{theirs}/edit"), post(signed, f"/foods/{theirs}/edit", **ROTI),
            post(signed, f"/foods/{theirs}/delete"), post(signed, f"/foods/{theirs}/restore")]
    assert [r.status_code for r in gone] == [404] * 4
    assert all("Secret stew" not in r.text and "321" not in r.text for r in gone)
    # Their deleted food named in the Undo mark: the strip shows, its form posts to an address that answers 404.
    page = signed.get(f"/foods?deleted=saved_food:{theirs}").text
    assert "Secret stew" not in page and post(signed, f"/foods/{theirs}/restore").status_code == 404
    assert [(f.deleted_at is None) for f in rows(SavedFood, other.id)] == [False]
    assert rows(SavedFood, mine) == []


# ---------------------------------------------------------------- Foods, the fix round (the batch after B15)

# The Saved foods table as the contract's row table (line 1172): each cell's tag, classes and data-label, by column.
ROW_CELLS = {"Food": ("td", ["lead"], None), "Serving": ("td", [], "Serving"), "kcal": ("td", ["num"], "kcal"),
             "Times logged": ("td", ["num"], "Times logged"), "Grams (g)": ("td", ["num"], "Grams (g)"),
             "Edit": ("td", [], None), "Delete": ("td", [], None)}


def test_the_saved_foods_table_is_the_contracts_row_table(signed, monkeypatch):
    """rev_b15 section 5 and the fourth look: table.rows in its div.table-wrap, which stacks below 720 px. The Food
    cell is td.lead, each figure a td.num with its column's header as its data-label (shown above it when stacked),
    the Serving cell its label too. Edit and Delete each in a cell of their own, no label. With grams and without."""
    for grams in (True, False):
        data = FIGURES if grams else {**FIGURES, "foods": [{**f, "grams_per_serving": None} for f in FIGURES["foods"]]}
        recorder(monkeypatch, "list_saved_foods", data)
        page = signed.get("/foods").text
        [card] = parse(page).find_all("section", "saved-foods")
        [table] = tables(card)
        assert table.node.classes == ["rows"] and table.node.parent.is_("div", "table-wrap")
        heads = [r for r in table.node.find_all("tr") if r.find_all("th")][0].elements()
        assert [(h.text(), h.classes) for h in heads] == [
            (c, ["num"] if ROW_CELLS[c][1] == ["num"] else []) for c in COLUMNS if grams or c != "Grams (g)"]
        assert [h.find_all("span", "sr")[0].text() for h in heads[-2:]] == ["Edit", "Delete"]
        for row in table.rows:
            assert [(c.tag, c.classes, c.attrs.get("data-label")) for c in row] == \
                [ROW_CELLS[h] for h in table.headers], row
            edit, delete = row[-2:]
            assert [(n.tag, n.classes) for n in edit.elements()] == [("a", ["action"])]
            assert [(n.tag, n.classes) for n in delete.elements()] == [("form", ["inline"])]
            assert [(n.tag, n.classes, n.text()) for n in delete.find_all("button")] == [("button", ["link"], "Delete")]


def test_neither_foods_form_carries_required_and_each_maxlength_is_the_services_limit(signed):
    """rev_b15 section 5: a browser's own bubble never stops an empty field, so the page's own line is read, as on
    Goals and Settings. maxlength stays, each the service's own limit."""
    uid = only_user().id
    food = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    for address in ("/foods", f"/foods/{food['id']}/edit"):
        page = signed.get(address).text
        controls = [n for n in parse(page).iter() if n.tag in ("input", "select", "textarea")]
        assert controls and not [n for n in controls if "required" in n.attrs], address
        assert " required" not in page and "autofocus" not in page, address
    fold = save_fold(signed.get("/foods").text)
    assert {i.attrs["name"]: i.attrs.get("maxlength") for i in fold.find_all("input") if i.attrs.get("type") is None} \
        == {"name": str(web.rules.TEXT_LIMITS["name"]), "serving_label": str(web.rules.TEXT_LIMITS["serving_label"]),
            "kcal_per_serving": None, "grams_per_serving": None}
    edit = signed.get(f"/foods/{food['id']}/edit").text
    assert control(edit, "serving_label").attrs.get("maxlength") == str(web.rules.TEXT_LIMITS["serving_label"])


def test_a_post_of_the_save_a_food_form_lands_at_the_top_and_its_refusal_at_the_form(signed, real_save_food):
    """rev_b15 section 5 and the fourth look: the form posts to /foods#save-food, so a refusal comes back at the form,
    open, with its lines in view. Every redirect that answers that post ends in #top, so the strip is in view. The
    edit page's form and redirect, Delete and Undo carry no fragment. Nothing carries autofocus."""
    uid = only_user().id
    assert web.POST_LANDS == web.GOAL_POST_LANDS == "#top"
    r = post(signed, "/foods", **{**GOOD_FOOD, "kcal_per_serving": "lots"})
    assert r.status_code == 400 and '<form method="post" action="/foods#save-food">' in r.text
    assert 'id="save-food"' in r.text and "open" in save_fold(r.text).attrs and "autofocus" not in r.text
    r = post(signed, "/foods", **GOOD_FOOD)
    assert r.headers["location"] == SITE + "/foods?saved=saved_food#top"
    page = signed.get(r.headers["location"]).text
    assert strips(page) == ["Saved food saved."] and [n.tag for n in parse(page).iter() if n.attrs.get("id") == "top"] \
        == ["body"]
    assert post(signed, "/foods", **GOOD_FOOD).headers["location"] == SITE + "/foods?saved=unchanged#top"
    [food] = rows(SavedFood, uid)
    edit = signed.get(f"/foods/{food.id}/edit").text
    assert re.findall(r'<form class="stack" method="post" action="([^"]+)">', edit) == [f"/foods/{food.id}/edit"]
    assert post(signed, f"/foods/{food.id}/edit", serving_label="2 idli", kcal_per_serving="116").headers["location"] \
        == SITE + f"/foods?saved=saved_food:{food.id}"
    assert post(signed, f"/foods/{food.id}/delete").headers["location"] == SITE + f"/foods?deleted=saved_food:{food.id}"
    assert post(signed, f"/foods/{food.id}/restore").headers["location"] == SITE + "/foods?restored=saved_food"


def test_a_save_a_food_post_with_no_person_behind_it_goes_to_sign_in_at_the_top(signed, monkeypatch):
    """The form's other redirect: a post whose person is gone (the token still good) goes to sign in, and it too ends
    in #top, as Goals' does. A GET's, and the edit page's, are as they were."""
    async def nobody(request):
        return None

    monkeypatch.setattr(web, "current_user", nobody)
    r = post(signed, "/foods", **GOOD_FOOD)
    assert r.status_code == 303 and r.headers["location"] == SITE + "/sign-in#top"
    assert signed.get("/foods").headers["location"] == SITE + "/sign-in"
    assert post(signed, "/foods/1/edit", **ROTI).headers["location"] == SITE + "/sign-in"


# Each field of the two forms at empty, spaces, not a number, a comma, each end of the service's own range and one
# past it (rev_b15 T1). The ends are save_food's (rules.TEXT_LIMITS, rules.NUMBERS, two decimals kept).
_KCAL, _GRAMS = web.rules.NUMBERS["kcal_per_serving"][:2], web.rules.NUMBERS["grams_per_serving"][:2]
FOOD_SWEEP = {
    "name": ["", "   ", "x" * web.rules.TEXT_LIMITS["name"], "x" * (web.rules.TEXT_LIMITS["name"] + 1)],
    "serving_label": ["", "   ", "s" * web.rules.TEXT_LIMITS["serving_label"],
                      "s" * (web.rules.TEXT_LIMITS["serving_label"] + 1)],
    "kcal_per_serving": ["", "   ", "lots", "1,000", "58,5", str(_KCAL[0]), str(_KCAL[0] - 0.01), str(_KCAL[1]),
                         str(_KCAL[1] + 0.01)],
    "grams_per_serving": ["", "   ", "some", "2,5", "40g", str(_GRAMS[0]), str(_GRAMS[0] - 0.01), str(_GRAMS[1]),
                          str(_GRAMS[1] + 0.01)],
}


def test_every_refusal_of_either_form_lands_under_a_field_it_draws(signed, real_save_food):
    """rev_b15 T1: placement reads save_food's message, so a refusal save_food gains with no field the form draws
    would fall to the general line unseen. Each field through the real fold-out and the real edit form, at each value
    of FOOD_SWEEP: every refusal's every problem lands under that field, in its line, and none at the top."""
    uid = only_user().id
    food = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    seen = {"refused": 0, "saved": 0}
    for address, drawn, prefix, good in (("/foods", web.FOOD_FIELDS, "food", GOOD_FOOD),
                                         (f"/foods/{food['id']}/edit", web.FOOD_EDIT_FIELDS, "edit", ROTI)):
        for field in drawn:
            for value in FOOD_SWEEP[field]:
                real_save_food.clear()
                r = post(signed, address, **{**good, field: value})
                if r.status_code == 303:
                    seen["saved"] += 1
                    assert all("error" not in a for a in real_save_food), (address, field, value)
                    continue
                assert r.status_code == 400, (address, field, value)
                seen["refused"] += 1
                [answer] = real_save_food
                assert web.food_errors(answer, drawn) == ({field: web.FIELD_HELP[("saved_food", field)]}, []), \
                    (address, field, value, answer)
                assert field_lines(r.text, prefix) == {field: web.FIELD_HELP[("saved_food", field)]}, (field, value)
                assert top_lines(r.text) == [], (address, field, value)
    assert seen["refused"] >= 30 and seen["saved"] >= 15, seen


def test_a_refusal_with_no_problems_list_says_the_general_line(signed):
    """rev_b15 T2: a refusal that is not a combined one carries no errors list. Each is real: save_food for an
    account that is not there (no_account), with data that is not an object, and by a caller it does not know."""
    answers = [services.save_food(10**9, dict(GOOD_FOOD), "web"), services.save_food(10**9, "Idli", "web"),
               services.save_food(10**9, dict(GOOD_FOOD), "api")]
    assert [a["error"] for a in answers] == ["no_account", "invalid_field", "invalid_by"]
    for answer in answers:
        assert "errors" not in answer
        assert web.food_errors(answer, web.FOOD_FIELDS) == ({}, [web.FOOD_ERROR_GENERAL])
        assert web.food_errors(answer, web.FOOD_EDIT_FIELDS) == ({}, [web.FOOD_ERROR_GENERAL])


def test_a_refusal_with_no_field_opens_the_fold_out_though_foods_are_saved(signed, real_save_food, monkeypatch):
    """rev_b15 T3: with a saved food the fold-out is closed by default, so only the refusal opens it."""
    uid = only_user().id
    services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    assert "open" not in save_fold(signed.get("/foods").text).attrs
    real_form = web.food_form
    monkeypatch.setattr(web, "food_form", lambda form: {**real_form(form), "colour": "red"})
    r = post(signed, "/foods", **GOOD_FOOD)
    assert r.status_code == 400 and real_save_food[-1]["error"] == "invalid_field" and field_lines(r.text, "food") == {}
    assert top_lines(r.text) == [web.FOOD_ERROR_GENERAL] and "open" in save_fold(r.text).attrs


def test_a_refusal_posted_to_a_marked_address_shows_no_strip(signed, real_save_food):
    """rev_b15 T4: the strip shows only on a page after a write, never over a refusal."""
    uid = only_user().id
    food = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "claude")
    for address in ("/foods?saved=saved_food", "/foods?restored=saved_food", f"/foods?deleted=saved_food:{food['id']}"):
        r = post(signed, address, **{**GOOD_FOOD, "kcal_per_serving": "lots"})
        assert r.status_code == 400 and "error" in real_save_food[-1] and strips(r.text) == [], address


@pytest.mark.parametrize("data, stored", [
    ({"kcal_per_serving": "0"}, ("Idli", "1 idli", 0, None)),
    ({"kcal_per_serving": "5000"}, ("Idli", "1 idli", 5000, None)),
    ({"kcal_per_serving": "1e3"}, ("Idli", "1 idli", 1000, None)),
    ({"grams_per_serving": "1"}, ("Idli", "1 idli", 58, 1)),
    ({"grams_per_serving": "5000"}, ("Idli", "1 idli", 58, 5000)),
    ({"name": "n" * 128}, ("n" * 128, "1 idli", 58, None)),
    ({"serving_label": "s" * 64}, ("Idli", "s" * 64, 58, None)),
], ids=["kcal 0", "kcal 5000", "kcal 1e3", "grams 1", "grams 5000", "name 128", "serving 64"])
def test_each_accepted_end_is_saved_through_the_fold_out(signed, data, stored):
    """rev_b15 T6: the ends save_food accepts, posted through the form, are saved and marked so."""
    uid = only_user().id
    assert post(signed, "/foods", **{**GOOD_FOOD, **data}).headers["location"] == SITE + "/foods?saved=saved_food#top"
    assert saved_foods(uid) == [(*stored, True)]
    assert calls(uid)[-1] == ("save_food", True, None)


def test_markup_typed_into_a_food_comes_back_as_typed_escaped_once(signed):
    """rev_b15 T7: a name and a serving with markup, in the table, the edit page's h1, title and field, and what was
    typed after a refusal: each escaped once, never drawn as a tag."""
    uid = only_user().id
    name, serving = '<b>Dal</b> & "x"', "<i>1 bowl</i> &amp;"
    food = services.save_food(uid, {"name": name, "serving_label": serving, "kcal_per_serving": 100}, "claude")["id"]
    page, edit = signed.get("/foods").text, signed.get(f"/foods/{food}/edit").text
    refused = post(signed, f"/foods/{food}/edit", serving_label="<s>2</s> &", kcal_per_serving="lots")
    assert refused.status_code == 400
    assert foods_table(page)[1][0][:2] == [name, serving]
    once = str(escape(name))  # as the page's autoescape writes it
    assert f'<td class="lead">{once}</td>' in page
    assert f"<h1>Edit {once}</h1>" in edit and f"<title>Edit {once} · Spotter</title>" in edit
    assert control(edit, "serving_label").attrs["value"] == serving
    assert control(refused.text, "serving_label").attrs["value"] == "<s>2</s> &"
    for markup in (page, edit, refused.text):
        assert not any(tag in markup for tag in ("<b>Dal", "<i>1 bowl", "<s>2")), markup[:200]
        assert "&amp;lt;" not in markup and "&amp;quot;" not in markup and "&amp;amp;amp;" not in markup


# ---------------------------------------------------------------- Export my data

EXPORT = {"files": {"account.csv": "account_id,email\n1,a@example.com\n", "lift_sessions.csv": "id,date\n",
                    "README.txt": "What each file holds.\n"},
          "rows": {"account.csv": 1, "lift_sessions.csv": 0}, "summary": ""}


def test_export_answers_a_zip_of_every_file(signed, monkeypatch):
    sent = recorder(monkeypatch, "export_data", EXPORT)
    r = post(signed, "/settings/export")
    assert r.status_code == 200 and sent == [(only_user().id,)]
    assert r.headers["content-type"] == "application/zip"
    today = today_of(only_user()).isoformat()
    assert r.headers["content-disposition"] == f'attachment; filename="spotter-export-{today}.zip"'
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        assert archive.namelist() == list(EXPORT["files"])
        for name, text in EXPORT["files"].items():
            assert archive.read(name).decode("utf-8") == text
    assert calls(only_user().id) == []  # a read: no write row, nothing counted against the limit


def test_export_keeps_only_each_files_own_name():
    data = web.export_zip({"../../evil.csv": "x", "a\\b.csv": "y", "": "z"})
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["evil.csv", "b.csv", "file.txt"]


def test_a_failed_export_says_so(signed, monkeypatch):
    recorder(monkeypatch, "export_data", {"error": "no_account", "message": "x", "fix": "y"})
    r = post(signed, "/settings/export")
    assert r.status_code == 500 and "could not put your data together" in r.text


def test_export_is_a_post_with_the_token_only(signed, monkeypatch):
    sent = recorder(monkeypatch, "export_data", EXPORT)
    assert signed.get("/settings/export").status_code in (404, 405)  # no GET: a link can never start a download
    for data in ({}, {"csrf_token": "wrong"}):
        assert signed.post("/settings/export", data=data).status_code == 403
    assert sent == []
    page = signed.get("/settings").text
    form = re.search(r'<form method="post" action="/settings/export">(.*?)</form>', page, re.S).group(1)
    assert f'value="{session_of(signed)["csrf"]}"' in form and "Export my data" in form


def test_the_real_export_has_every_file(signed):
    stock(only_user())
    r = post(signed, "/settings/export")
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        names = set(archive.namelist())
    assert {"account.csv", "lift_sessions.csv", "lift_sets.csv", "bodyweight.csv", "measurements.csv", "goals.csv",
            "saved_foods.csv", "food_entries.csv", "cardio_entries.csv", "README.txt"} <= names


# ---------------------------------------------------------------- Delete my account: the confirm page

def test_the_confirm_page_says_what_goes_by_count(signed, make_user):
    uid = only_user().id
    stocked = stock(only_user())
    stock_food(only_user(), weigh_in=False)
    services.delete_entry(uid, "bodyweight", stocked["old_weigh_in"])  # a deleted row goes too
    services.set_goal(uid, {"kind": "bodyweight", "target_value": 70, "target_date": "2099-01-01"}, "claude")
    other = make_user()
    services.log_bodyweight(other.id, {"kg": "90"}, "claude")
    page = signed.get("/settings/delete").text
    counts = dict(re.findall(r'<li class="entry-line"><span>([^<]+)</span><strong>(\d+)</strong></li>', page))
    assert counts == {"Lift sessions": "2", "Weigh-ins": "2", "Measurements": "3", "Food entries": "3",
                      "Cardio sessions": "1", "Saved foods": "1", "Goals": "1"}
    assert "so does your sign-in" in page and "This cannot be undone." in page
    form = re.search(r'<form method="post" action="/settings/delete">(.*?)</form>', page, re.S).group(1)
    assert f'value="{session_of(signed)["csrf"]}"' in form and "Delete my account" in form
    assert 'href="/settings/delete"' in signed.get("/settings").text


# ---------------------------------------------------------------- Delete my account: the delete

@pytest.fixture
def provider_delete(monkeypatch):
    """The seam's delete_identity, recorded. `fail` makes it raise that exception."""
    state = {"subjects": [], "fail": None, "events": None}

    async def fake(subject):
        state["subjects"].append(subject)
        if state["events"] is not None:
            state["events"].append("provider")
        if state["fail"] is not None:
            raise state["fail"]

    monkeypatch.setattr(web_auth, "delete_identity", fake)
    return state


def contract_delete(events: list | None = None):
    """Stands in for delete_account as the contract has it: call-log rows kept with no user, then the user
    row and everything it owns (the schema's cascade)."""
    def run(user_id):
        if events is not None:
            events.append("local")
        with db.session_scope() as s:
            for row in s.scalars(select(CallLog).where(CallLog.user_id == user_id)):
                row.user_id = None
            s.delete(s.get(User, user_id))
        return {"deleted": True, "rows": {}}

    return run


def test_delete_removes_the_sign_in_first_then_the_rows_then_the_session(signed, provider_delete, monkeypatch):
    events = provider_delete["events"] = []
    monkeypatch.setattr(services, "delete_account", contract_delete(events), raising=False)
    stock(only_user())
    r = post(signed, "/settings/delete")
    assert r.status_code == 200 and '<h1 class="title">Your account is deleted</h1>' in r.text
    assert "Signing in again starts a new, empty account." in r.text
    assert events == ["provider", "local"] and provider_delete["subjects"] == ["user_01WEB"]
    assert user_count() == 0
    assert session_of(signed) == {}  # the session cookie is cleared
    assert "Sign out" not in r.text and 'href="/settings"' not in r.text and 'name="csrf_token"' not in r.text
    assert 'href="/sign-in"' in r.text and "provider.example/logout" not in r.text
    assert signed.get("/settings").headers["location"] == SITE + "/sign-in"
    with db.session_scope() as s:
        logged = [(c.function, c.ok, c.user_id) for c in s.scalars(select(CallLog))]
    assert ("delete_account", True, None) in logged  # the row stays for the counts, with no user


def test_signing_in_again_after_a_delete_starts_a_new_account(signed, provider_delete, monkeypatch):
    monkeypatch.setattr(services, "delete_account", contract_delete(), raising=False)
    services.log_bodyweight(only_user().id, {"kg": "80"}, "claude")
    post(signed, "/settings/delete")
    sign_in(signed)
    assert user_count() == 1  # SQLite may hand the new row the old id; what matters is that it is empty
    with db.session_scope() as s:
        assert s.scalars(select(Bodyweight)).all() == []
    assert "Nothing logged yet" in signed.get(f"/day/{today_of(only_user()).isoformat()}").text


@pytest.mark.parametrize("failure", [web_auth.DeleteFailed("ServerError"), RuntimeError("anything")],
                         ids=["delete failed", "any exception"])
def test_a_provider_failure_deletes_nothing(signed, provider_delete, monkeypatch, failure):
    provider_delete["fail"] = failure
    sent = recorder(monkeypatch, "delete_account", {"deleted": True, "rows": {}})
    r = post(signed, "/settings/delete")
    assert r.status_code == 502 and "could not delete your sign-in, so nothing was deleted" in r.text
    assert "Try again in a few minutes." in r.text
    assert sent == [] and user_count() == 1 and calls(only_user().id) == []
    assert signed.get("/settings").status_code == 200  # still signed in


@pytest.mark.parametrize("local", ["refused", "raises"])
def test_a_local_failure_says_the_sign_in_is_gone_and_the_session_still_works(web_client, provider_delete,
                                                                              monkeypatch, local):
    client = web_client(raise_server_exceptions=False)
    sign_in(client)
    if local == "raises":
        def boom(user_id):
            raise RuntimeError("bug")
        monkeypatch.setattr(services, "delete_account", boom, raising=False)
    else:
        recorder(monkeypatch, "delete_account", {"error": "invalid_field", "message": "x", "fix": "y"})
    r = post(client, "/settings/delete")
    assert "Your sign-in is deleted, but your data here is not deleted yet." in r.text
    assert "you are still signed in here" in r.text and "Do not sign out until this finishes." in r.text
    assert r.status_code == 500 and "Try again now" in r.text
    assert provider_delete["subjects"] == ["user_01WEB"] and user_count() == 1
    assert calls(only_user().id)[-1][0] == "delete_account" and calls(only_user().id)[-1][1] is False
    assert client.get("/settings").status_code == 200  # the account row is still there, so is the session


def test_a_retry_after_a_local_failure_finishes(signed, provider_delete, monkeypatch):
    recorder(monkeypatch, "delete_account", {"error": "invalid_field", "message": "x", "fix": "y"})
    assert post(signed, "/settings/delete").status_code == 500
    monkeypatch.setattr(services, "delete_account", contract_delete(), raising=False)
    r = post(signed, "/settings/delete")  # the provider answers "no such user" now: the seam counts it done
    assert r.status_code == 200 and "Your account is deleted" in r.text and user_count() == 0
    assert provider_delete["subjects"] == ["user_01WEB", "user_01WEB"]


def test_the_real_delete(signed, provider_delete, make_user):
    stock(only_user())
    other = make_user()
    services.log_bodyweight(other.id, {"kg": "90"}, "claude")
    assert post(signed, "/settings/delete").status_code == 200
    with db.session_scope() as s:
        assert s.get(User, other.id) is not None and s.scalars(select(User)).all() != []
        assert [b.kg for b in s.scalars(select(Bodyweight))] == [90]
        assert ("delete_account", None) in [(c.function, c.user_id) for c in s.scalars(select(CallLog))]


def test_delete_needs_the_csrf_token_and_a_post(signed, provider_delete, monkeypatch):
    sent = recorder(monkeypatch, "delete_account", {"deleted": True, "rows": {}})
    for data in ({}, {"csrf_token": "wrong"}):
        assert signed.post("/settings/delete", data=data).status_code == 403
    assert provider_delete["subjects"] == [] and sent == [] and user_count() == 1


# ---------------------------------------------------------------- Settings, rebuilt (pass B16)

SETTINGS_CARDS = [("account", "account-title", "Your account"), ("zone", "zone-title", "Time zone"),
                  ("targets", "targets-title", "Targets"),
                  ("assistant", "assistant-title", "What your assistant may do"),
                  (None, "data-title", "Your data"), (None, "more-title", "More")]
SETTINGS_LINES = {"zone": "Time zone saved.", "targets": "Targets saved.", "assistant": "Your assistant's access saved."}
SETTINGS_MARKS = {"zone": "time_zone", "targets": "targets", "assistant": "claude_can"}


def settings_cards(page: str) -> list[Node]:
    """Each card in Settings' main, in markup order."""
    [main] = parse(page).find_all("main")
    return [card for card in main.elements() if card.is_(cls="card")]


def card_by_title(page: str, title: str) -> Node:
    [card] = [c for c in settings_cards(page) if c.find_all("h2") and c.find_all("h2")[0].text() == title]
    return card


def shown(node: Node) -> str:
    """A node's words, as a person reads them."""
    return re.sub(r"\s+", " ", node.text()).strip()


def test_settings_in_the_contracts_order(signed):
    """The head, then the six titled cards in the contract's order, each a section named by its own one
    h2. No code word, no function's name, no required and no autofocus anywhere on the page."""
    page = signed.get("/settings").text
    [main] = parse(page).find_all("main")
    kids = main.elements()
    assert kids[0].is_("div", "page-head") and [h.text() for h in kids[0].find_all("h1")] == ["Settings"]
    cards = kids[1:]
    assert all(c.is_(cls="card") for c in cards)
    assert [(c.tag, c.classes, c.attrs.get("id"), c.attrs.get("aria-labelledby"), [h.text() for h in c.find_all("h2")])
            for c in cards] == [("section", ["card"], cid, hid, [title]) for cid, hid, title in SETTINGS_CARDS]
    assert all(c.find_all("h2")[0].attrs["id"] == hid for c, (_, hid, _) in zip(cards, SETTINGS_CARDS))
    words = plain(page)
    for code in ("whoami", "claude_can", "update_settings", "set_claude_can", "log_delete", "Claude"):
        assert code not in words, code
    assert " required" not in page and "autofocus" not in page


def test_settings_on_a_laptop_takes_two_columns_by_its_page_class(signed):
    assert '<main class="page settings-page">' in signed.get("/settings").text


@pytest.mark.parametrize("name, email", [("<b>Asha</b> & co", "a<i>@example.com"), (None, None)],
                         ids=["markup-like", "none given"])
def test_the_account_facts_are_the_user_row_value_by_value(signed, name, email):
    """Name, Email, Time zone, Sign-in id, Account id, each the user row's own value, escaped, in that order. Then the
    grey line, which names no function."""
    with db.session_scope() as s:
        user = s.get(User, only_user().id)
        user.name, user.email = name, email
    page = signed.get("/settings").text
    card = card_by_title(page, "Your account")
    [facts] = card.find_all("dl", "facts")
    pairs = [(dt.text(), dd.text()) for dt, dd in zip(facts.find_all("dt"), facts.find_all("dd"))]
    user = only_user()
    assert pairs == [("Name", name or "Not given"), ("Email", email or "Not given"),
                     ("Time zone", "Not chosen yet: today is counted in UTC"),
                     ("Sign-in id", user.subject), ("Account id", str(user.id))]
    assert f"<code>{user.id}</code>" in page and f"<code>{user.subject}</code>" in page
    if name:
        assert "<b>Asha</b>" not in page and html.escape(name, quote=True) in page and "a<i>" not in page
    assert [shown(p) for p in card.find_all("p", "small")] == [
        "When your assistant is connected to this account, asking it who you are shows the same two ids."]
    services.update_settings(user.id, {"time_zone": "Asia/Kolkata"}, "web")
    card = card_by_title(signed.get("/settings").text, "Your account")
    assert [dd.text() for dd in card.find_all("dd")][2] == "Asia/Kolkata"


@pytest.mark.parametrize("card", sorted(SETTINGS_LINES))
def test_each_saved_line_sits_in_its_own_card_and_only_there(signed, card):
    """One saved line at most on the page, in the card whose mark the address carries, after its h2."""
    page = signed.get(f"/settings?saved={SETTINGS_MARKS[card]}").text
    oks = parse(page).find_all("p", "ok")
    assert [html.unescape(o.text()) for o in oks] == [SETTINGS_LINES[card]]
    home = next(a for a in oks[0].ancestors() if a.is_(cls="card"))
    assert home.attrs.get("id") == card and home.elements()[1] is oks[0]
    assert page.count('class="ok') == 1


@pytest.mark.parametrize("query", ["saved=unchanged", "saved=1", "saved=", "saved=Targets", "deleted=targets",
                                   "saved=saved_food"])
def test_a_mark_settings_has_no_words_for_shows_no_saved_line(signed, query):
    page = signed.get(f"/settings?{query}").text
    assert 'class="ok' not in page and "saved." not in plain(page)


def location_of(r) -> str:
    assert r.status_code == 303, (r.status_code, r.text[:300])
    return r.headers["location"]


def test_each_post_that_changes_nothing_lands_on_its_card_marked_unchanged_with_no_line(signed):
    """The zone set to the zone it is (in another spelling too), the targets posted as they are (as typed, and in
    another spelling of the same number), and the switch set to its own value: each lands on saved=unchanged at its
    card, writes nothing, and the page there says nothing was saved."""
    uid = only_user().id
    assert "error" not in services.update_settings(uid, {"time_zone": "Asia/Kolkata", "calorie_target_kcal": 2000,
                                                         "add_back_exercise_kcal": True}, "web")
    before = (only_user().updated_at, len(calls(uid)))
    landed = [location_of(post(signed, "/settings", time_zone="Asia/Kolkata", card="zone")),
              location_of(post(signed, "/settings", time_zone="Asia/Calcutta", card="zone")),
              location_of(post(signed, "/settings/targets", calorie_target_kcal="2000", add_back_exercise_kcal="on")),
              location_of(post(signed, "/settings/targets", calorie_target_kcal=" 2000.0 ",
                               add_back_exercise_kcal="on")),
              location_of(post(signed, "/settings/claude-can", claude_can="log"))]
    assert landed == [SITE + "/settings?saved=unchanged#zone"] * 2 + [SITE + "/settings?saved=unchanged#targets"] * 2 \
        + [SITE + "/settings?saved=unchanged#assistant"]
    assert only_user().updated_at == before[0]
    assert [c[:2] for c in calls(uid)[before[1]:]] == [("update_settings", True)] * 2 + [("set_claude_can", True)]
    assert 'class="ok' not in signed.get("/settings?saved=unchanged").text


def test_targets_update_settings_finds_unchanged_are_marked_unchanged(signed, monkeypatch):
    """When the web's own compare lets a value through that update_settings finds already stored (changed: []), the
    post is still marked unchanged, never saved: the service's own answer decides."""
    uid = only_user().id
    assert "error" not in services.update_settings(uid, {"calorie_target_kcal": 2000}, "web")
    monkeypatch.setattr(web, "targets_from_form", lambda form, user: {"calorie_target_kcal": "2000"})
    assert location_of(post(signed, "/settings/targets", calorie_target_kcal="2000")) == \
        SITE + "/settings?saved=unchanged#targets"
    assert calls(uid)[-1] == ("update_settings", True, None)


def test_each_form_posts_to_its_cards_anchor_and_each_redirect_names_it(signed):
    """The three forms' actions end in their card's id, which is on the page. Export and Sign out carry none, and neither does the automatic zone form. Each redirect that answers one of the three names the same
    card: the Time zone card's form carries its own field for it. The automatic save lands as it always did."""
    page = signed.get("/settings").text
    dom = parse(page)
    actions = [f.attrs.get("action") for f in dom.find_all("form")]
    assert actions == ["/sign-out", "/settings#zone", "/settings/targets#targets", "/settings/claude-can#assistant",
                       "/settings/export", "/sign-out", "/settings"]
    ids = {n.attrs.get("id") for n in dom.iter()}
    assert {"zone", "targets", "assistant"} <= ids
    assert re.search(r'<form id="auto-time-zone" method="post" action="/settings" hidden>', page)
    assert "autofocus" not in page
    [card] = [f for f in dom.find_all("form") if f.attrs.get("action") == "/settings#zone"]
    assert [(i.attrs.get("type"), i.attrs.get("name"), i.attrs.get("value")) for i in card.find_all("input")][1:-1] == [
        ("hidden", web.ZONE_CARD_FIELD, "zone")]
    assert card.find_all("input")[-1].attrs.get("name") == "key"  # the form's key, its last field
    assert location_of(post(signed, "/settings", time_zone="Europe/London", card="zone")) == \
        SITE + "/settings?saved=time_zone#zone"
    assert location_of(post(signed, "/settings/targets", target_sessions_per_week="3")) == \
        SITE + "/settings?saved=targets#targets"
    assert location_of(post(signed, "/settings/claude-can", claude_can="read")) == \
        SITE + "/settings?saved=claude_can#assistant"
    # The automatic save, as before: next=/settings and a next that is not local land on Settings with no anchor.
    assert location_of(post(signed, "/settings", time_zone="Asia/Kolkata", next="/settings")) == \
        SITE + "/settings?saved=time_zone"
    assert location_of(post(signed, "/settings", time_zone="Asia/Kolkata", next="//evil")) == \
        SITE + "/settings?saved=unchanged"


# The refusals each card can meet, every one a real refusal from the service: (id, card, the post, the code
# update_settings or set_claude_can gives, {field: the page's line} or the card's line).
ZONE_LINE = "That time zone was not recognised, so nothing was changed. Choose your time zone from the list."
ZONE_EMPTY = "Choose your time zone from the list."  # nothing chosen: the list's own first option
ZONE_ROWS = [("not a zone", {"time_zone": "Mars/Olympus"}, "unknown_time_zone"),
             ("an abbreviation", {"time_zone": "EST"}, "unknown_time_zone"),
             ("a bare city", {"time_zone": "Kolkata"}, "unknown_time_zone"),
             ("empty", {"time_zone": ""}, "unknown_time_zone"),
             ("too long", {"time_zone": "x" * 100}, "invalid_field"),
             ("a control character", {"time_zone": "Asia/Kolkata\u0007"}, "invalid_field"),
             ("markup", {"time_zone": "<b>Mars</b>"}, "unknown_time_zone")]
CALORIE = "Give the calories as a whole number with no comma, from 800 to 10,000\u00a0kcal, or leave it empty."
SESSIONS = "Give lift sessions a week as a whole number from 0 to 14, or leave it empty."
CARDIO = "Give cardio minutes a week as a plain whole number, such as 150, from 0 to 3,000, or leave it empty."
TARGET_ROWS = [
    ("calories below 800", {"calorie_target_kcal": "799"}, {"calorie_target_kcal": CALORIE}),
    ("calories above 10000", {"calorie_target_kcal": "10001"}, {"calorie_target_kcal": CALORIE}),
    ("calories not a number", {"calorie_target_kcal": "lots"}, {"calorie_target_kcal": CALORIE}),
    ("calories not whole", {"calorie_target_kcal": "2000.5"}, {"calorie_target_kcal": CALORIE}),
    ("calories with a comma", {"calorie_target_kcal": "2,000"}, {"calorie_target_kcal": CALORIE}),
    ("sessions below 0", {"target_sessions_per_week": "-1"}, {"target_sessions_per_week": SESSIONS}),
    ("sessions above 14", {"target_sessions_per_week": "15"}, {"target_sessions_per_week": SESSIONS}),
    ("sessions not a number", {"target_sessions_per_week": "four"}, {"target_sessions_per_week": SESSIONS}),
    ("sessions not whole", {"target_sessions_per_week": "2.5"}, {"target_sessions_per_week": SESSIONS}),
    ("cardio below 0", {"target_cardio_minutes_per_week": "-5"}, {"target_cardio_minutes_per_week": CARDIO}),
    ("cardio above 3000", {"target_cardio_minutes_per_week": "3001"}, {"target_cardio_minutes_per_week": CARDIO}),
    ("cardio not a number", {"target_cardio_minutes_per_week": "<b>x</b>"}, {"target_cardio_minutes_per_week": CARDIO}),
    ("cardio not whole", {"target_cardio_minutes_per_week": "150.5"}, {"target_cardio_minutes_per_week": CARDIO}),
    ("cardio with a comma", {"target_cardio_minutes_per_week": "2,000"}, {"target_cardio_minutes_per_week": CARDIO}),
    ("three at once", {"calorie_target_kcal": "100", "target_sessions_per_week": "99",
                       "target_cardio_minutes_per_week": "abc", "add_back_exercise_kcal": "on"},
     {"calorie_target_kcal": CALORIE, "target_sessions_per_week": SESSIONS, "target_cardio_minutes_per_week": CARDIO}),
    ("one of two", {"calorie_target_kcal": "2100", "target_sessions_per_week": "99"},
     {"target_sessions_per_week": SESSIONS}),
]
SWITCH_LINE = "Choose one of the three settings below."
SWITCH_ROWS = [("another word", {"claude_can": "admin"}), ("empty", {"claude_can": ""}), ("none sent", {}),
               ("another case", {"claude_can": "LOG"}), ("a space after", {"claude_can": "log_delete "}),
               ("markup", {"claude_can": "<b>read</b>"})]


def service_messages(result: dict) -> list[str]:
    """Every message and fix a refusal carries, each problem's too, as sent and as a page escapes it."""
    found = [result] + [e for e in result.get("errors") or [] if isinstance(e, dict)]
    texts = {t for e in found for t in (e.get("message"), e.get("fix")) if t}
    return sorted(texts | {html.escape(t, quote=True) for t in texts})


def real_refusal(function, *args) -> dict:
    """The service's own answer to the same values, called directly on the person's account: a refusal writes
    nothing, so the page's post after it meets the same answer."""
    return function(only_user().id, *args)


@pytest.mark.parametrize("data, code", [row[1:] for row in ZONE_ROWS], ids=[row[0] for row in ZONE_ROWS])
def test_each_zone_refusal_says_the_pages_line_in_its_card(signed, data, code):
    """The line in a div.error at the top of the Time zone card's form, tied to the select. Never update_settings'
    message or fix. 400, nothing saved, the call log holds the refusal's code, and no saved line shows. A post that
    chose nothing is told to choose: it chose no zone that could go unrecognised."""
    line = ZONE_EMPTY if data == {"time_zone": ""} else ZONE_LINE
    result = real_refusal(services.update_settings, dict(data), "web")
    assert result["error"] == code and "field" not in result
    r = post(signed, "/settings", **data)
    assert r.status_code == 400 and only_user().time_zone is None
    assert calls(only_user().id)[-1] == ("update_settings", False, code)
    card = card_by_title(r.text, "Time zone")
    [box] = card.find_all("div", "error")
    assert [(p.classes, p.attrs.get("id"), p.text()) for p in box.elements()] == [(["error"], "time-zone-error",
                                                                                     line)]
    [select] = card.find_all("select")
    assert (select.attrs.get("aria-invalid"), select.attrs.get("aria-describedby")) == ("true", "time-zone-error")
    assert [p.text() for p in parse(r.text).find_all(cls="error")] == [line] * 2  # the box and its line only
    for text in service_messages(result):
        assert text not in r.text, text
    assert "<b>Mars</b>" not in r.text and 'class="ok' not in r.text


def test_a_zone_refusal_with_another_code_says_the_cards_general_line(signed, monkeypatch):
    """A code the card has no line for (here not_allowed, update_settings' real answer when claude_can comes with the
    zone) takes the card's general line, never the service's words."""
    real = services.update_settings
    monkeypatch.setattr(services, "update_settings", lambda uid, data, by: real(uid, {**data, "claude_can": "read"}, by))
    r = post(signed, "/settings", time_zone="Asia/Kolkata")
    result = real_refusal(real, {"time_zone": "Asia/Kolkata", "claude_can": "read"}, "web")
    assert r.status_code == 400 and result["error"] == "not_allowed" and only_user().time_zone is None
    assert calls(only_user().id)[-1] == ("update_settings", False, "not_allowed")
    [box] = card_by_title(r.text, "Time zone").find_all("div", "error")
    assert box.text() == web.ZONE_ERROR_GENERAL
    for text in service_messages(result):
        assert text not in r.text, text


@pytest.mark.parametrize("data, lines", [row[1:] for row in TARGET_ROWS], ids=[row[0] for row in TARGET_ROWS])
def test_each_target_refusal_says_the_pages_line_under_its_field(signed, data, lines):
    """Each refused target's line under its field, tied to its input, what was typed kept in every field and the
    add-back box as posted. update_settings names no field: each row asserts that its codes are invalid_field, the
    code the page's lines are keyed by. Never its message or fix. 400, nothing saved, the call log row."""
    sent = {k: v for k, v in data.items() if k != "add_back_exercise_kcal"}
    result = real_refusal(services.update_settings, dict(sent, add_back_exercise_kcal="add_back_exercise_kcal" in data),
                          "web")
    problems = result.get("errors") or [result]
    assert [p["error"] for p in problems] == ["invalid_field"] * len(lines) and all("field" not in p for p in problems)
    r = post(signed, "/settings/targets", **data)
    assert r.status_code == 400
    user = only_user()
    assert (user.calorie_target_kcal, user.target_sessions_per_week, user.target_cardio_minutes_per_week,
            user.add_back_exercise_kcal) == (None, None, None, False)
    assert calls(user.id)[-1] == ("update_settings", False, "invalid_field")
    card = card_by_title(r.text, "Targets")
    assert not card.find_all("div", "error")
    errors = {p.attrs["id"].removesuffix("-error"): html.unescape(p.text()) for p in card.find_all("p", "error")}
    assert errors == lines
    for field in web.TARGET_FIELDS:
        [box] = [i for i in card.find_all("input") if i.attrs.get("name") == field]
        assert box.attrs.get("value", "") == data.get(field, "")
        if field in lines:
            assert (box.attrs.get("aria-invalid"), box.attrs.get("aria-describedby")) == ("true", f"{field}-error")
        else:
            assert "aria-invalid" not in box.attrs
    [check] = [i for i in card.find_all("input") if i.attrs.get("type") == "checkbox"]
    assert ("checked" in check.attrs) == ("add_back_exercise_kcal" in data)
    for text in service_messages(result):
        assert text not in r.text, text
    assert "<b>x</b>" not in r.text and 'class="ok' not in r.text


def test_a_targets_refusal_tied_to_no_field_goes_at_the_top_of_the_form(signed, monkeypatch):
    """A field the form never sends (update_settings really refuses it: invalid_field, 'colour' is not a setting)
    is placed under none of the fields: the general line sits in a div.error at the top of the targets form."""
    real = web.targets_from_form
    monkeypatch.setattr(web, "targets_from_form", lambda form, user: {**real(form, user), "colour": "red"})
    r = post(signed, "/settings/targets", calorie_target_kcal="2100")
    result = real_refusal(services.update_settings, {"calorie_target_kcal": "2100", "colour": "red"}, "web")
    assert r.status_code == 400 and result["error"] == "invalid_field" and only_user().calorie_target_kcal is None
    card = card_by_title(r.text, "Targets")
    [form] = card.find_all("form")
    [box] = form.find_all("div", "error")
    assert form.elements()[1] is box and box.text() == web.TARGETS_ERROR_GENERAL
    assert not card.find_all("p", "error") and 'value="2100"' in r.text
    for text in service_messages(result):
        assert text not in r.text, text


@pytest.mark.parametrize("data", [row[1] for row in SWITCH_ROWS], ids=[row[0] for row in SWITCH_ROWS])
def test_each_switch_refusal_says_the_pages_line_in_its_card(signed, data):
    """The line in a div.error at the top of the switch's form, tied to each radio, the stored setting still checked,
    nothing changed. Never set_claude_can's message or fix."""
    result = real_refusal(services.set_claude_can, data.get("claude_can"))
    assert result["error"] == "invalid_field" and "field" not in result
    r = post(signed, "/settings/claude-can", **data)
    assert r.status_code == 400 and only_user().claude_can == "log"
    assert calls(only_user().id)[-1] == ("set_claude_can", False, "invalid_field")
    card = card_by_title(r.text, "What your assistant may do")
    [box] = card.find_all("div", "error")
    assert [(p.attrs.get("id"), p.text()) for p in box.elements()] == [("claude-can-error", SWITCH_LINE)]
    radios = [i for i in card.find_all("input") if i.attrs.get("type") == "radio"]
    assert [(i.attrs.get("aria-invalid"), i.attrs.get("aria-describedby")) for i in radios] == \
        [("true", "claude-can-error")] * 3
    assert [i.attrs["value"] for i in radios if "checked" in i.attrs] == ["log"]
    for text in service_messages(result):
        assert text not in r.text, text
    assert "<b>read</b>" not in r.text and 'class="ok' not in r.text


def test_a_switch_refusal_with_another_code_says_the_cards_general_line(signed, monkeypatch):
    """set_claude_can's other refusal, no_account (the account gone between the read and the write), drawn for an
    account that does not exist, takes the card's general line."""
    real = services.set_claude_can
    monkeypatch.setattr(services, "set_claude_can", lambda uid, value: real(uid + 10**6, value))
    r = post(signed, "/settings/claude-can", claude_can="read")
    result = real(only_user().id + 10**6, "read")
    assert r.status_code == 400 and result["error"] == "no_account" and only_user().claude_can == "log"
    [box] = card_by_title(r.text, "What your assistant may do").find_all("div", "error")
    assert box.text() == web.SWITCH_ERROR_GENERAL
    for text in service_messages(result):
        assert text not in r.text, text


@pytest.mark.parametrize("value", ["read", "log", "log_delete"])
def test_the_switch_shows_three_labelled_radio_cards_and_checks_the_stored_one(signed, value):
    """The three settings by their stored values, in order, each a label.choice in fieldset.choices with its legend:
    the label in bold, its line after it, saying your assistant. The current one is checked."""
    services.set_claude_can(only_user().id, value)
    card = card_by_title(signed.get("/settings").text, "What your assistant may do")
    assert [shown(p) for p in card.find_all("p", "muted")] == [
        "Only you can change this, here: your assistant is never offered this switch."]
    [fieldset] = card.find_all("fieldset", "choices")
    assert [lg.text() for lg in fieldset.find_all("legend")] == ["What your assistant may do"]
    choices = fieldset.find_all("label", "choice")
    got = []
    for choice in choices:
        [radio] = choice.find_all("input")
        [bold] = choice.find_all("strong")
        got.append((radio.attrs["type"], radio.attrs["name"], radio.attrs["value"], "checked" in radio.attrs,
                    bold.text(), shown(choice).removeprefix(bold.text()).strip()))
    assert got == [("radio", "claude_can", v, v == value, label, line) for v, label, line in [
        ("read", "Read only", SWITCH_ALLOWS["read"][1]), ("log", "Read and log", SWITCH_ALLOWS["log"][1]),
        ("log_delete", "Read, log and delete", SWITCH_ALLOWS["log_delete"][1])]]
    assert all(not line.startswith(label) for _, _, _, _, label, line in got)  # no line repeats its label
    assert [b.text() for b in card.find_all("button")] == ["Save"]


def test_the_targets_card_holds_its_three_fields_the_add_back_row_and_save(signed):
    assert "error" not in services.update_settings(only_user().id, {"calorie_target_kcal": 2200,
                                                                    "target_sessions_per_week": 0,
                                                                    "add_back_exercise_kcal": True}, "web")
    card = card_by_title(signed.get("/settings").text, "Targets")
    fields = card.find_all("div", "field")
    assert [(f.find_all("label")[0].text(), f.find_all("input")[0].attrs.get("name"),
             f.find_all("input")[0].attrs.get("value"), f.find_all("input")[0].attrs.get("inputmode")) for f in fields] \
        == [("Calories a day (kcal)", "calorie_target_kcal", "2200", "numeric"),
            ("Lift sessions a week", "target_sessions_per_week", "0", "numeric"),
            ("Cardio a week (min)", "target_cardio_minutes_per_week", None, "numeric")]
    [row] = card.find_all("label", "choice")
    [check] = row.find_all("input")
    assert check.attrs.get("type") == "checkbox" and "checked" in check.attrs
    assert shown(row) == "Add cardio back: show a net total, food minus cardio calories"
    assert [b.text() for b in card.find_all("button")] == ["Save targets"]


def test_the_time_zone_card_holds_the_grouped_list_and_save(signed):
    card = card_by_title(signed.get("/settings").text, "Time zone")
    [select] = card.find_all("select")
    assert select.attrs == {"id": "time-zone", "name": "time_zone"} and len(select.find_all("optgroup")) > 5
    assert [b.text() for b in card.find_all("button")] == ["Save time zone"]


def test_your_data_offers_export_and_the_delete_link(signed):
    """Export my data, a secondary button, with its line. Delete my account, a link in the error colour, with its
    line. Nothing else: no Remove sample data, whatever the account holds."""
    from evals.four_weeks import four_weeks  # noqa: PLC0415
    for seeded in (False, True):
        if seeded:
            assert "error" not in four_weeks(only_user().id)
        card = card_by_title(signed.get("/settings").text, "Your data")
        buttons = [(b.classes, b.text()) for b in card.find_all("button")]
        assert buttons == [(["secondary"], "Export my data")]
        [link] = card.find_all("a")
        assert (link.attrs, link.text()) == ({"class": "action is-danger", "href": "/settings/delete"},
                                             "Delete my account")
        lines = [shown(p) for p in card.find_all("p", "small")]
        assert lines == ["A zip of CSV files: everything you have logged, deleted entries included.",
                         "You see what goes before anything is deleted."]


def test_the_more_card_links_saved_foods_and_carries_a_sign_out_that_signs_out(signed):
    """More: Saved foods to /foods, and a second Sign out form (the CSRF field, class sign-out, which the stylesheet
    shows on a phone only), which signs the person out. No Privacy link."""
    page = signed.get("/settings").text
    card = card_by_title(page, "More")
    assert [(a.attrs, a.text()) for a in card.find_all("a")] == [({"class": "action", "href": "/foods"}, "Saved foods")]
    [form] = card.find_all("form")
    assert form.attrs == {"class": "sign-out", "method": "post", "action": "/sign-out"}
    token = session_of(signed)["csrf"]
    assert [(i.tag, i.attrs) for i in form.elements()] == [
        ("input", {"type": "hidden", "name": "csrf_token", "value": token}), ("button", {"class": "secondary"})]
    assert form.find_all("button")[0].text() == "Sign out" and "Privacy" not in page
    r = signed.post(form.attrs["action"], data={"csrf_token": form.find_all("input")[0].attrs["value"]})
    assert r.status_code == 303 and session_of(signed) == {}
    assert signed.get("/settings").headers["location"] == SITE + "/sign-in"


# What each setting lets the assistant call beyond the setting before it, read from mcp_server.NEEDS, beside the line
# the switch says for it (rev_b16 M2 and T2). A change to either fails here until both are read again: a function
# NEEDS gains, moves or loses changes its set, and a reworded line changes its words.
SWITCH_ALLOWS = {
    "read": ({"whoami", "list_exercises", "get_progress", "get_log", "find_food", "get_trends", "get_goals",
              "owner_calls"},  # trace.md decision 16: the owner's alone, at read
             "Your assistant can read what you have logged and change nothing."),
    "log": ({"log_lifts", "log_bodyweight", "log_measurement", "log_food", "log_cardio", "import_entries", "edit_entry",
             "restore_entry", "save_food", "set_goal", "update_settings"},
            "Your assistant can also log and import entries, edit and restore them, save foods, set goals, and change "
            "your time zone and targets. It cannot delete."),
    "log_delete": ({"delete_entry"},
                   "Your assistant can also delete entries and saved foods. A deleted one can always be restored."),
}


def test_each_switch_line_is_held_beside_what_needs_lets_its_setting_call_beyond_the_one_before():
    """Each setting in the switch's order, the functions mcp_server.NEEDS gives exactly that level (what it allows
    beyond the level below), and the line the page says for it."""
    from app import mcp_server  # noqa: PLC0415
    from app.models import CLAUDE_CAN  # noqa: PLC0415

    assert list(SWITCH_ALLOWS) == list(CLAUDE_CAN) == list(mcp_server.LEVELS) == list(web.CLAUDE_CAN_LINES)
    assert {level: ({f for f, need in mcp_server.NEEDS.items() if need == level}, web.CLAUDE_CAN_LINES[level])
            for level in mcp_server.LEVELS} == SWITCH_ALLOWS


def auto_zone_form(page: str) -> tuple[str, dict]:
    """base.html's automatic zone form as the page draws it: its action and the fields it would post."""
    [form] = [f for f in parse(page).find_all("form") if f.attrs.get("id") == "auto-time-zone"]
    return form.attrs["action"], {i.attrs["name"]: i.attrs.get("value", "") for i in form.find_all("input")}


@pytest.mark.parametrize("next_, lands", [
    (None, "/settings?saved=time_zone"),  # as a page drawn with an error sends it: no next
    ("/goals?kind=x#y", "/goals?kind=x&saved=time_zone#y"),
    ("", "/settings?saved=time_zone"), ("/settings", "/settings?saved=time_zone"),
    ("//evil.example", "/settings?saved=time_zone"), ("https://evil.example/x", "/settings?saved=time_zone"),
], ids=["no next", "a local page", "empty", "settings", "another site", "another site in full"])
def test_the_automatic_zone_save_lands_as_it_always_did_from_a_page_drawn_with_an_error(signed, next_, lands):
    """rev_b16 M3: a page drawn with an error (here a refused target, 400) draws the automatic form with no next. Posted
    as drawn, the browser's zone filled in, it lands on Settings with no anchor, as before pass B16. With each next it
    lands where it did. Only the Time zone card's own form, which carries its field, lands on the card."""
    r = post(signed, "/settings/targets", calorie_target_kcal="abc")
    assert r.status_code == 400 and only_user().time_zone is None
    action, fields = auto_zone_form(r.text)
    assert action == "/settings" and set(fields) == {"csrf_token", "time_zone", "key"}  # no next, no card field
    sent = {**fields, "time_zone": "Asia/Kolkata", **({} if next_ is None else {"next": next_})}
    assert location_of(signed.post(action, data=sent)) == SITE + lands
    assert only_user().time_zone == "Asia/Kolkata"


def test_a_target_typed_with_a_comma_gets_its_fields_line_with_the_services_range(signed):
    """rev_b16 M1: "2,000" is refused by update_settings as not a number. Each field's own line shows under it, its
    range read from the service's limits. Each says what is wrong with a typed comma."""
    for field, line in (("calorie_target_kcal", CALORIE), ("target_cardio_minutes_per_week", CARDIO)):
        result = real_refusal(services.update_settings, {field: "2,000"}, "web")
        assert result["error"] == "invalid_field" and "not a number" in result["message"]
        r = post(signed, "/settings/targets", **{field: "2,000"})
        assert r.status_code == 400 and line in html.unescape(r.text) and line == web.TARGET_ERROR_WORDS[
            ("invalid_field", field)]
        lo, hi, _ = web.rules.NUMBERS[field]
        assert web.rules.span(lo, hi, web.TARGET_QUANTITIES[field]) in line
    assert "as a plain whole number, such as 150," in CARDIO and "as a whole number with no comma," in CALORIE


@pytest.mark.parametrize("field, value, stored", [
    ("calorie_target_kcal", "800", 800), ("calorie_target_kcal", "10000", 10000),
    ("target_sessions_per_week", "0", 0), ("target_sessions_per_week", "14", 14),
    ("target_cardio_minutes_per_week", "0", 0), ("target_cardio_minutes_per_week", "3000", 3000),
])
def test_each_targets_accepted_ends_are_saved_through_the_form(signed, field, value, stored):
    """rev_b16 T4: each end of each range, posted through the form, is saved and marked so."""
    lo, hi, _ = web.rules.NUMBERS[field]
    assert stored in (lo, hi)  # the service's own ends
    assert location_of(post(signed, "/settings/targets", **{field: value})) == SITE + "/settings?saved=targets#targets"
    assert getattr(only_user(), field) == stored
    assert calls(only_user().id)[-1] == ("update_settings", True, None)


@pytest.mark.parametrize("address, data", [
    ("/settings?saved=time_zone", {"time_zone": "Mars/Olympus", "card": "zone"}),
    ("/settings/targets?saved=targets", {"calorie_target_kcal": "abc"}),
    ("/settings/claude-can?saved=claude_can", {"claude_can": "admin"}),
], ids=["zone", "targets", "switch"])
def test_a_refusal_posted_to_an_address_with_a_saved_mark_shows_no_saved_line(signed, address, data):
    """rev_b16 T5: the saved line is the mark's only on a page after a write, never over a refusal."""
    r = post(signed, address, **data)
    assert r.status_code == 400 and parse(r.text).find_all("p", "ok") == [] and 'class="ok' not in r.text
    assert parse(r.text).find_all(cls="error")


@pytest.mark.parametrize("address, card", [("/settings/targets", "targets"), ("/settings/claude-can", "assistant")])
def test_a_get_of_a_cards_post_address_goes_to_its_card(signed, address, card):
    """rev_b16, the look's 1.5.7: a refused post leaves its own address in the bar, and a GET of it (a reload, the
    address typed) goes to Settings at that card's anchor rather than a 404. Its post is as it was."""
    r = signed.get(address)
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/settings#{card}"
    assert signed.get(r.headers["location"]).status_code == 200
    signed.cookies.clear()
    r = signed.get(address)
    assert r.status_code == 303 and r.headers["location"] == SITE + f"/settings#{card}"
    assert signed.get(r.headers["location"]).headers["location"] == SITE + "/sign-in"


# ---------------------------------------------------------------- Delete my account, rebuilt (pass B16)

def test_the_delete_page_in_the_contracts_order_with_none_logged(signed):
    """The head, then one card: the bold line, the counts by the one table's names in its order, each 0 on its own
    line, the two lines, and the two controls."""
    page = signed.get("/settings/delete").text
    assert '<main class="page notice-page">' in page
    [main] = parse(page).find_all("main")
    head, card = main.elements()
    assert head.is_("div", "page-head") and [h.text() for h in head.find_all("h1")] == ["Delete my account"]
    assert card.tag == "div" and card.classes == ["card"] and not card.find_all("h2")
    assert [(e.tag, shown(e)) for e in card.elements()] == [
        ("p", "This cannot be undone. Everything below is deleted, deleted entries included."),
        ("ul", " ".join(f"{label}0" for _, label in web.DELETE_COUNTS)),
        ("p", "Your settings go too, and so does your sign-in: signing in again starts a new, empty account."),
        ("p", "To keep a copy, use Export my data on Settings first."),
        ("form", "Delete my account Keep my account")]
    assert card.find_all("strong")[0].text() == "This cannot be undone."
    lines = re.findall(r'<li class="entry-line"><span>([^<]+)</span><strong>(\d+)</strong></li>', page)
    assert lines == [(label, "0") for _, label in web.DELETE_COUNTS]
    assert [label for _, label in web.DELETE_COUNTS] == ["Lift sessions", "Weigh-ins", "Measurements", "Food entries",
                                                         "Cardio sessions", "Saved foods", "Goals"]
    [form] = card.find_all("form")
    assert form.attrs == {"method": "post", "action": "/settings/delete"}
    assert [(b.classes, b.text()) for b in form.find_all("button")] == [(["danger"], "Delete my account")]
    assert [(a.attrs, a.text()) for a in form.find_all("a")] == [({"class": "action", "href": "/settings"},
                                                                  "Keep my account")]
    assert " required" not in page and "autofocus" not in page


def test_signed_out_the_delete_page_and_its_post_delete_nothing(client, make_user, provider_delete):
    """Signed out, the page goes to sign-in and its post, with or without a token, is refused: nothing is deleted at
    the provider or here."""
    make_user()
    assert client.get("/settings/delete").headers["location"] == SITE + "/sign-in"
    for data in ({}, {"csrf_token": "x"}):
        assert client.post("/settings/delete", data=data).status_code == 403
    assert provider_delete["subjects"] == [] and user_count() == 1


# ---------------------------------------------------------------- the write limit and CSRF on every new route

def test_delete_goes_through_at_the_write_limit(signed, provider_delete):
    """The person can always leave: a full write limit never stops Delete my account."""
    uid = only_user().id
    now = db.utcnow()
    with db.session_scope() as s:
        s.add_all(CallLog(function="log_bodyweight", started_at=now, ms=1, ok=True, error_code=None, result_bytes=1,
                          user_id=uid) for _ in range(services.WRITE_LIMIT + 5))
    assert post(signed, "/day/" + today_of(only_user()).isoformat() + "/weigh-in", kg="80").status_code == 429
    r = post(signed, "/settings/delete")
    assert r.status_code == 200 and "Your account is deleted" in r.text and user_count() == 0
    assert provider_delete["subjects"] == ["user_01WEB"]


def test_the_new_pages_carry_no_script_and_the_same_policy(signed):
    services.update_settings(only_user().id, {"time_zone": "Asia/Kolkata"}, "web")
    for path in ("/settings", "/settings/delete", "/goals", "/progress", f"/day/{today_of(only_user()).isoformat()}"):
        r = signed.get(path)
        assert r.headers["content-security-policy"] == CSP and "<script" not in r.text, path
        assert re.search(r"<[^>]+\son[a-z]+\s*=", r.text, re.I) is None and "style=" not in r.text, path


# ---------------------------------------------------------------- the seam: delete_identity

class FakeWorkOS:
    """Stands in for the provider's async client: records what it was built with and who it deleted."""
    built: list[dict] = []
    deleted: list[str] = []
    closed = 0
    fail: Exception | None = None

    def __init__(self, **values):
        FakeWorkOS.built.append(values)
        self.user_management = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        FakeWorkOS.closed += 1

    async def delete_user(self, id):  # noqa: A002 - the library's own name for it
        FakeWorkOS.deleted.append(id)
        if FakeWorkOS.fail is not None:
            raise FakeWorkOS.fail


@pytest.fixture
def fake_workos(monkeypatch, app_settings):
    from app import config

    config.use_settings(settings(**WEB))
    FakeWorkOS.built, FakeWorkOS.deleted, FakeWorkOS.closed, FakeWorkOS.fail = [], [], 0, None
    monkeypatch.setattr(web_auth, "AsyncWorkOSClient", FakeWorkOS)
    return FakeWorkOS


def test_the_library_deletes_a_user_by_id():
    params = inspect.signature(AsyncUserManagement.delete_user).parameters
    assert "id" in params and "user_id" not in params


def test_delete_identity_deletes_the_user_at_the_provider(fake_workos):
    assert asyncio.run(web_auth.delete_identity("user_01GONE")) is None
    assert fake_workos.deleted == ["user_01GONE"] and fake_workos.closed == 1
    [values] = fake_workos.built
    assert values["api_key"] == WEB["workos_api_key"] and values["request_timeout"] == 10 and values["max_retries"] == 0


def test_no_such_user_at_the_provider_counts_as_done(fake_workos, logs):
    fake_workos.fail = workos.NotFoundError("User not found", status_code=404)
    assert asyncio.run(web_auth.delete_identity("user_01GONE")) is None
    assert "already gone" in logs.text


@pytest.mark.parametrize("failure", [workos.ServerError("down", status_code=500),
                                     workos.AuthenticationError("bad key", status_code=401),
                                     OSError("network")], ids=["server", "auth", "network"])
def test_any_other_provider_failure_raises_delete_failed_logged_by_type(fake_workos, logs, failure):
    fake_workos.fail = failure
    with pytest.raises(web_auth.DeleteFailed):
        asyncio.run(web_auth.delete_identity("user_01SECRET"))
    assert type(failure).__name__ in logs.text and "user_01SECRET" not in logs.text and "bad key" not in logs.text


def test_delete_identity_has_a_time_limit(monkeypatch, logs):
    async def slow(subject):
        await asyncio.sleep(5)

    monkeypatch.setattr(web_auth, "_delete_user", slow)
    monkeypatch.setattr(web_auth, "EXCHANGE_SECONDS", 0.05)
    with pytest.raises(web_auth.DeleteFailed):
        asyncio.run(web_auth.delete_identity("user_01SLOW"))
    assert "TimeoutError" in logs.text
