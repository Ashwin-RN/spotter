"""Macros on food (contract macros-and-asking.md, package P2): protein, carbs and fat in grams on a food entry, each
marked as the person's or an estimate, the portion asked before an estimate is written, the day's macros, a saved
meal, the USDA figures, the edits and the undo, the call log's atwater note. An account with no macro answers as
before. No test touches the network: USDA answers through services.usda.urlopen, replaced."""

import json

import pytest
from sqlalchemy import func, select

from app import calllog, config, db, rules, services
from app.mcp_schemas import FoodEntryIn, LOG_FOOD_DESCRIPTION, LOG_FOOD_MACROS, SavedFood as SavedFoodModel
from app.models import CallLog, EntryVersion, FoodEntry, SavedFood, UsdaFood
from app.services import common, food, usda
from tests import text_rules as tr
from tests.conftest import settings as make_settings
from tests.test_mcp import failed, ok, registered_tools
from tests.test_trace import sign_for
from tests.test_usda import KEY, banana, fake

WRAP = {"description": "Chicken wrap", "kcal": 520, "protein_g": 32, "carbs_g": 48, "fat_g": 18,
        "macros_estimated": False}
PORTION_FIX = ("Ask the person once how much they had, then send it as portion in {}, in their words. Nothing was "
               "logged.")


@pytest.fixture
def user(make_user):
    return make_user()


@pytest.fixture
def keyed():
    config.use_settings(make_settings(usda_api_key=KEY))


def eat(user, *entries, by="claude", **data):
    return services.log_food(user.id, {"entries": list(entries), **data}, by)


def rows(user_id) -> list[FoodEntry]:
    with db.new_session() as s:
        return list(s.scalars(select(FoodEntry).where(FoodEntry.user_id == user_id).order_by(FoodEntry.id)))


def saved(user, name="Roti", kcal=120, **per):
    return services.save_food(user.id, {"name": name, "serving_label": "1 roti", "kcal_per_serving": kcal, **per},
                              "claude")


def notes() -> list[str | None]:
    with db.session_scope() as s:
        return [r.notes for r in s.scalars(select(CallLog).order_by(CallLog.id))]


def macros_of(entry: dict) -> tuple:
    return tuple(entry[k] for k in ("protein_g", "carbs_g", "fat_g", "macros_estimated"))


def with_banana(fdc_id=173944, kcal=89, protein=1.09, fat=0.33, carbs=22.84, shape="detail"):
    """A USDA food with its macros, in either of the two nutrient shapes FoodData Central uses."""
    item = banana(fdc_id, kcal, shape=shape)
    for nid, number, value in ((1003, "203", protein), (1004, "204", fat), (1005, "205", carbs)):
        if value is None:
            continue
        if shape == "detail":
            item["foodNutrients"].append({"nutrient": {"id": nid, "number": number, "unitName": "g"}, "amount": value})
        else:
            item["foodNutrients"] = [n for n in item["foodNutrients"] if n["nutrientId"] != nid]
            item["foodNutrients"].append({"nutrientId": nid, "nutrientNumber": number, "unitName": "G", "value": value})
    return item


# ---------------------------------------------------------------- a food entry's new fields

def test_stated_macros_are_kept_with_their_mark_and_a_left_out_mark_counts_as_an_estimate(user):
    r = eat(user, WRAP, {"description": "Dal", "kcal": 180, "protein_g": 9.04},
            {"description": "Tea", "kcal": 40})
    assert [macros_of(e) for e in r["entries"]] == [(32, 48, 18, False), (9, None, None, True),
                                                    (None, None, None, None)]
    assert [e.source for e in rows(user.id)] == ["you_said"] * 3  # the kcal's tag is its own
    assert [e.protein_g for e in rows(user.id)] == [32, 9, None]  # kept at 1 dp; null is not logged, never 0 g


def test_the_mark_is_null_with_no_macro_whatever_was_sent_and_macros_round_to_one_place(user):
    r = eat(user, {"description": "Tea", "kcal": 40, "macros_estimated": True},
            {"description": "Egg", "kcal": 78, "protein_g": 6.25, "fat_g": 5.349, "macros_estimated": True})
    assert macros_of(r["entries"][0]) == (None, None, None, None)
    assert macros_of(r["entries"][1]) == (6.3, None, 5.3, True)


@pytest.mark.parametrize(("field", "top"), [("protein_g", 1250), ("carbs_g", 1250), ("fat_g", 555)])
def test_each_macro_takes_its_range_from_the_kcal_cap(user, field, top):
    assert eat(user, {"description": "x", "kcal": 1, field: top}, allow_repeat=True)["entries"][0][field] == top
    assert eat(user, {"description": "x", "kcal": 1, field: 0}, allow_repeat=True)["entries"][0][field] == 0
    for bad in (top + 0.1, -1):
        refused = eat(user, {"description": "x", "kcal": 1, field: bad}, allow_repeat=True)
        assert refused["error"] == "invalid_field" and f"Entry 1: {field} " in refused["message"], refused
    assert len(rows(user.id)) == 2


def test_a_macro_or_its_mark_never_travels_with_an_id(user):
    roti = saved(user)["id"]
    for field, value in (("protein_g", 5), ("carbs_g", 5), ("fat_g", 1), ("macros_estimated", False)):
        refused = eat(user, {"saved_food_id": roti, "servings": 1, field: value})
        assert refused["error"] == "invalid_field", refused
        assert refused["message"] == f"Entry 1: {field} was sent with saved_food_id."
        assert refused["fix"] == f"Leave {field} out: the app supplies the number for a saved or USDA food."
        refused = eat(user, {"usda_fdc_id": 1234, "grams": 100, field: value})
        assert refused["message"] == f"Entry 1: {field} was sent with usda_fdc_id."
    assert rows(user.id) == []


# ---------------------------------------------------------------- the portion, asked first

def test_an_estimate_with_no_portion_is_refused_with_the_question_and_nothing_is_written(user):
    for sent in ({}, {"portion": ""}, {"portion": "   "}, {"portion": None}, {"portion": "​"}):
        refused = eat(user, {"description": "Dal", "kcal": 180},
                      {"description": "Rice", "kcal": 200, "estimated": True, **sent})
        assert (refused["error"], refused["message"], refused["fix"]) == (
            "missing_field", "Entry 2: portion is missing for an estimate.", PORTION_FIX.format("entry 2")), sent
    assert rows(user.id) == []
    logged = eat(user, {"description": "Dal", "kcal": 180},
                 {"description": "Rice", "kcal": 200, "estimated": True, "portion": " a big bowl "})
    assert [e["portion"] for e in logged["entries"]] == [None, "a big bowl"]
    assert [e.portion for e in rows(user.id)] == [None, "a big bowl"]


def test_the_portion_is_never_asked_of_a_stated_number_a_saved_food_or_a_usda_food(user, keyed, monkeypatch):
    fake(monkeypatch, with_banana())
    roti = saved(user)["id"]
    r = eat(user, {"description": "Dal", "kcal": 180}, {"saved_food_id": roti}, {"usda_fdc_id": 173944, "grams": 120})
    assert "error" not in r and [e["portion"] for e in r["entries"]] == [None, None, None]
    kept = eat(user, {"description": "Dal", "kcal": 180, "portion": "2 bowls"},
               {"description": "Tea", "kcal": 40, "portion": "  "}, allow_repeat=True)
    assert [e["portion"] for e in kept["entries"]] == ["2 bowls", None]  # kept on any entry; a blank one left out


def test_a_portion_holds_64_characters_and_the_text_rules(user):
    words = "a" * 64
    assert eat(user, {"description": "Dal", "kcal": 1, "estimated": True, "portion": words})["entries"][0][
        "portion"] == words
    long = eat(user, {"description": "Dal", "kcal": 1, "estimated": True, "portion": words + "a"}, allow_repeat=True)
    assert long["error"] == "invalid_field" and long["message"] == "Entry 1: portion is 65 characters; the most is 64."
    control = eat(user, {"description": "Dal", "kcal": 1, "portion": "a\x00b"}, allow_repeat=True)
    assert control["error"] == "invalid_field"
    for text in (rules.portion_missing("Entry 2")["message"], rules.portion_missing("Entry 2")["fix"]):
        assert tr.check(text, tr.MESSAGE) == [], text


def test_a_portion_sent_as_a_number_is_refused_as_text_is_asked_for(http):
    """inputs.md: a wrong type is refused, never coerced. The fix asks for the portion as text."""
    token = sign_for("user_01PORTIONNUMBER")
    refused = failed(http, token, "log_food", {"entries": [{"description": "Idli", "kcal": 150, "estimated": True,
                                                            "portion": 3}]})
    assert refused["message"] == "Entry 1: portion must be text, not 3."
    assert refused["fix"] == "Give portion on entry 1 as text."


def test_a_chat_opened_before_holds_no_portion_field_and_its_estimate_is_refused_then_logged(http):
    """Note of 2026-11-22, point 7: a chat opened before C3 holds log_food with no portion. Its estimate is refused
    with the fix, then the same call with portion is logged."""
    token = sign_for("user_01OLDCHAT")
    estimate = {"description": "2 rotis", "kcal": 240, "estimated": True}
    refused = failed(http, token, "log_food", {"entries": [estimate]})
    assert (refused["error"], refused["message"]) == ("missing_field", "Entry 1: portion is missing for an estimate.")
    assert "send it as portion in entry 1" in refused["fix"]
    logged = ok(http, token, "log_food", {"entries": [{**estimate, "portion": "2"}]})
    assert logged["entries"][0]["source"] == "claude_estimate" and logged["entries"][0]["portion"] == "2"


def test_through_log_the_portion_refusal_names_its_entry_as_sent(http):
    token = sign_for("user_01LOGPORTION")
    entries = [{"description": "Dal", "kcal": 180}, {"description": "Rice", "kcal": 200, "estimated": True}]
    refused = failed(http, token, "log", {"kind": "food", "details": {"entries": entries}})
    assert refused["message"] == "details.entries[1]: portion is missing for an estimate."
    assert refused["fix"] == PORTION_FIX.format("details.entries[1]")
    own = failed(http, token, "log_food", {"entries": entries})
    assert own["message"] == "Entry 2: portion is missing for an estimate."
    entries[1]["portion"] = "a plate"
    entries[1]["carbs_g"] = 45
    logged = ok(http, token, "log", {"kind": "food", "details": {"entries": entries}})
    assert [(e["portion"], e["carbs_g"], e["macros_estimated"]) for e in logged["entries"]] == [
        (None, None, None), ("a plate", 45, True)]


# ---------------------------------------------------------------- saved and USDA foods

def test_a_saved_food_s_macros_are_its_grams_a_serving_times_the_servings(user):
    roti = saved(user, protein_g_per_serving=3.1, carbs_g_per_serving=18)["id"]
    r = eat(user, {"saved_food_id": roti, "servings": 2.5})
    assert macros_of(r["entries"][0]) == (7.8, 45, None, False)  # fat has no figure: null, never 0 g
    plain = saved(user, name="Plain")["id"]
    assert macros_of(eat(user, {"saved_food_id": plain})["entries"][0]) == (None, None, None, None)


def test_a_worked_out_macro_is_held_to_the_range_a_sent_one_is(user):
    big = saved(user, name="Shake", kcal=100, protein_g_per_serving=1000)["id"]
    refused = eat(user, {"saved_food_id": big, "servings": 2})
    assert refused["error"] == "invalid_field"
    assert refused["message"] == ("Entry 1: 2 servings of Shake works out at 2,000 g of protein, outside 0 to 1,250 "
                                  "for one entry.")


def test_usda_reads_the_three_macros_by_id_or_number_and_a_missing_one_stays_null(keyed, monkeypatch):
    fake(monkeypatch, with_banana(fat=None))
    found, err = usda.food(173944)
    assert err is None and (found["protein_g_per_100g"], found["carbs_g_per_100g"], found["fat_g_per_100g"]) == (
        1.09, 22.84, None)
    fake(monkeypatch, {"foods": [with_banana(2, shape="search")]})
    hits, err = usda.search("banana")
    assert err is None and (hits[0]["protein_g_per_100g"], hits[0]["fat_g_per_100g"]) == (1.09, 0.33)
    with db.new_session() as s:
        assert all(row.macros_read for row in s.scalars(select(UsdaFood)))


def test_a_usda_entry_s_macros_are_its_grams_a_100_g_times_the_grams(user, keyed, monkeypatch):
    fake(monkeypatch, with_banana())
    r = eat(user, {"usda_fdc_id": 173944, "grams": 150})
    # The cache keeps each figure a 100 g at 2 dp, as kcal is (1.09, 22.84, 0.33), and the entry is that times 1.5,
    # rounded to 1 dp only then.
    assert macros_of(r["entries"][0]) == (1.6, 34.3, 0.5, False)
    found = services.find_food(user.id, "banana")
    assert "usda" in found  # its hits carry the figures a 100 g, as the model publishes them


def test_a_small_usda_figure_a_100_g_is_never_rounded_to_a_zero_before_the_scaling(user, keyed, monkeypatch):
    fake(monkeypatch, with_banana(fdc_id=170000, kcal=30, protein=0.04, fat=0.04, carbs=7.04))
    r = eat(user, {"usda_fdc_id": 170000, "grams": 400})
    assert macros_of(r["entries"][0]) == (0.2, 28.2, 0.2, False)
    found, err = usda.food(170000)
    assert err is None and (found["protein_g_per_100g"], found["fat_g_per_100g"]) == (0.04, 0.04)


def test_a_cached_food_from_before_0015_is_stale_and_fetched_again(keyed, monkeypatch):
    with db.session_scope() as s:
        s.add(UsdaFood(fdc_id=173944, description="Bananas, raw", data_type="SR Legacy", kcal_per_100g=89,
                       fetched_at=db.utcnow(), macros_read=False))
    f = fake(monkeypatch, with_banana())
    found, err = usda.food(173944)
    assert err is None and found["protein_g_per_100g"] == 1.09 and len(f.requests) == 1
    usda.food(173944)
    assert len(f.requests) == 1  # read since 0015: the cache answers


def test_a_cached_search_over_a_food_from_before_0015_is_fetched_again(keyed, monkeypatch):
    f = fake(monkeypatch, {"foods": [with_banana(shape="search")]})
    usda.search("banana")
    with db.session_scope() as s:
        s.get(UsdaFood, 173944).macros_read = False
    usda.search("banana")
    assert len(f.requests) == 2
    usda.search("banana")
    assert len(f.requests) == 2


# ---------------------------------------------------------------- the day's numbers

def test_a_day_with_no_macro_answers_as_before(user):
    r = eat(user, {"description": "Dal", "kcal": 180})
    assert "macros" not in r["day"]
    assert "Protein" not in r["summary"] and r["summary"].endswith("Day total 180 kcal.")
    day = services.get_log(user.id)["days"][0]["food"]
    assert "macros" not in day


def test_the_day_s_macros_sum_what_entries_hold_and_say_so_far_while_one_has_none(user):
    eat(user, WRAP, {"description": "Eggs", "kcal": 156, "protein_g": 12.5, "estimated": True, "portion": "2"})
    r = eat(user, {"description": "Tea", "kcal": 40})
    macros = r["day"]["macros"]
    assert macros == {"protein": {"g": 44.5, "target_g": None, "left_g": None, "entries_without": 1},
                      "carbs": {"g": 48, "target_g": None, "left_g": None, "entries_without": 2},
                      "fat": {"g": 18, "target_g": None, "left_g": None, "entries_without": 2}}
    assert r["summary"].endswith(" Protein 44.5 g so far, 1 entry with none. Carbs 48 g so far, 2 entries with none. "
                                 "Fat 18 g so far, 2 entries with none.")
    assert tr.check(r["summary"], tr.SUMMARY) == []
    assert services.get_log(user.id)["days"][0]["food"]["macros"] == macros  # get_log reads the same helper


def test_so_far_is_left_out_when_every_entry_holds_the_macro(user):
    r = eat(user, WRAP)
    assert r["summary"].endswith(" Protein 32 g. Carbs 48 g. Fat 18 g.")


# ---------------------------------------------------------------- the daily goals on the day (P3, decision 2 A)

def goals(user, **targets):
    return services.update_settings(user.id, targets, "claude")


CHICKEN = {"description": "Chicken", "kcal": 300, "protein_g": 60, "macros_estimated": False}


def test_a_goal_gives_target_g_and_left_g_and_the_sentence_says_left_or_over_as_the_kcal_target_does(user):
    goals(user, protein_target_g=150, fat_target_g=15, calorie_target_kcal=2000)
    eat(user, WRAP, CHICKEN)
    r = eat(user, {"description": "Tea", "kcal": 40})
    macros = r["day"]["macros"]
    assert macros == {"protein": {"g": 92, "target_g": 150, "left_g": 58, "entries_without": 1},
                      "carbs": {"g": 48, "target_g": None, "left_g": None, "entries_without": 2},
                      "fat": {"g": 18, "target_g": 15, "left_g": -3, "entries_without": 2}}
    assert r["summary"].endswith(
        " 1,140 kcal left of the 2,000 kcal target. Protein 92 g so far, 1 entry with none, 58 g left of the 150 g goal."
        " Carbs 48 g so far, 2 entries with none. Fat 18 g so far, 2 entries with none, 3 g over the 15 g goal.")
    assert tr.check(r["summary"], tr.SUMMARY) == []
    assert services.get_log(user.id)["days"][0]["food"]["macros"] == macros  # get_log reads the same helper


def test_left_g_keeps_one_place_and_at_the_goal_it_is_0_g_left(user):
    goals(user, protein_target_g=150, carbs_target_g=48)
    r = eat(user, WRAP, {"description": "Eggs", "kcal": 156, "protein_g": 12.3, "estimated": True, "portion": "2"})
    assert (r["day"]["macros"]["protein"]["left_g"], r["day"]["macros"]["carbs"]["left_g"]) == (105.7, 0)
    assert "Protein 44.3 g, 105.7 g left of the 150 g goal." in r["summary"]
    assert "Carbs 48 g so far, 1 entry with none, 0 g left of the 48 g goal." in r["summary"]


def test_a_goal_with_no_macro_logged_still_gives_the_block_counting_g_as_0_and_says_no_sentence(user):
    goals(user, carbs_target_g=250)
    r = eat(user, {"description": "Dal", "kcal": 180}, {"description": "Tea", "kcal": 40})
    assert r["day"]["macros"] == {"protein": {"g": None, "target_g": None, "left_g": None, "entries_without": 2},
                                  "carbs": {"g": None, "target_g": 250, "left_g": 250, "entries_without": 2},
                                  "fat": {"g": None, "target_g": None, "left_g": None, "entries_without": 2}}
    assert r["summary"].endswith("Day total 220 kcal.")  # no "0 g": nothing is logged of it, so nothing is said
    empty = services.get_log(user.id, days=2)["days"][1]["food"]  # yesterday: no food at all
    assert empty["macros"]["carbs"] == {"g": None, "target_g": 250, "left_g": 250, "entries_without": 0}


def test_with_every_goal_cleared_a_day_with_no_macro_answers_as_an_account_that_never_had_one(make_user):
    before, after = make_user(), make_user()
    goals(after, protein_target_g=150, carbs_target_g=200, fat_target_g=60)
    assert "macros" in services.get_log(after.id)["days"][0]["food"]
    goals(after, protein_target_g=None, carbs_target_g=None, fat_target_g=None)
    plain = [eat(u, {"description": "Dal", "kcal": 180}) for u in (before, after)]
    assert all("macros" not in r["day"] for r in plain) and plain[0]["summary"] == plain[1]["summary"]
    assert plain[0]["day"] == plain[1]["day"]
    assert all("macros" not in services.get_log(u.id)["days"][0]["food"] for u in (before, after))


def test_through_mcp_the_day_with_a_goal_fits_the_published_shape(http):
    token = sign_for("user_01GOALS")
    ok(http, token, "update_settings", {"protein_target_g": 150})
    r = ok(http, token, "log_food", {"entries": [WRAP]})
    assert r["day"]["macros"]["protein"] == {"g": 32, "target_g": 150, "left_g": 118, "entries_without": 0}
    day = ok(http, token, "get_log", {})["days"][0]["food"]
    assert day["macros"] == r["day"]["macros"]
    tools = registered_tools(http)
    left = tools["log_food"]["outputSchema"]["$defs"]["MacroDay"]["properties"]["left_g"]
    assert left["description"] == "The goal minus g, with g as 0 while null; below 0 when over. Null without a goal."
    assert tools["get_log"]["outputSchema"]["$defs"]["MacroDay"] == tools["log_food"]["outputSchema"]["$defs"][
        "MacroDay"]


# ---------------------------------------------------------------- marks, edits and undo

def test_a_macro_edit_replaces_and_the_mark_turns_false_only_when_every_macro_held_is_sent(user):
    """One mark covers the three (the owner's pick of 2026-11-23, "Stays an estimate"): an edit of fewer than the
    entry holds leaves the others an estimate, and so leaves the mark as it is."""
    guess = eat(user, {"description": "Dal", "kcal": 180, "estimated": True, "portion": "a bowl", "protein_g": 9,
                       "fat_g": 4})["ids"][0]
    r = services.edit_entry(user.id, "food_entry", guess, {"protein_g": 11}, "claude")
    assert r["changed"] == ["protein_g"] and macros_of(r["after"]) == (11, None, 4, True) and "note" not in r
    assert r["after"]["source"] == "claude_estimate" and r["summary"] == "Food entry changed: protein_g from 9 to 11."
    cleared = services.edit_entry(user.id, "food_entry", guess, {"fat_g": None}, "claude")
    assert cleared["changed"] == ["fat_g"] and macros_of(cleared["after"]) == (11, None, None, True)
    assert cleared["summary"] == "Food entry changed: fat_g 4 cleared." and "note" not in cleared
    stated = services.edit_entry(user.id, "food_entry", guess, {"protein_g": 11}, "claude")  # every one held, sent
    assert stated["changed"] == ["protein_g"] and macros_of(stated["after"]) == (11, None, None, False)
    assert stated["note"] == "The macros are now the person's own grams."
    gone = services.edit_entry(user.id, "food_entry", guess, {"protein_g": None}, "claude")
    assert macros_of(gone["after"]) == (None, None, None, None) and "note" not in gone  # no macro: no mark


def test_an_edit_sending_every_macro_held_marks_them_the_person_s_and_a_restate_of_fewer_changes_nothing(user):
    guess = eat(user, {"description": "Paneer curry", "kcal": 400, "estimated": True, "portion": "a bowl",
                       "protein_g": 20, "carbs_g": 30, "fat_g": 25})["ids"][0]
    same = services.edit_entry(user.id, "food_entry", guess, {"protein_g": 20}, "claude")
    assert same["changed"] == [] and same["after"]["macros_estimated"] is True  # a restate of one is no change
    one_cleared = services.edit_entry(user.id, "food_entry", guess,
                                      {"protein_g": 22, "carbs_g": 30, "fat_g": None}, "claude")
    assert one_cleared["changed"] == ["protein_g", "carbs_g", "fat_g"]
    assert macros_of(one_cleared["after"]) == (22, 30, None, False)
    assert one_cleared["summary"] == ("Food entry changed: protein_g from 20 to 22. carbs_g 30 confirmed by the "
                                      "person. fat_g 25 cleared.")
    assert one_cleared["note"] == "The macros are now the person's own grams."
    added = eat(user, {"description": "Rice", "kcal": 200})["ids"][0]  # no macro, no mark
    r = services.edit_entry(user.id, "food_entry", added, {"carbs_g": 44}, "claude")
    assert macros_of(r["after"]) == (None, 44, None, False)  # the only one held, sent


def test_a_macro_restated_on_an_estimate_is_the_person_s_and_a_kcal_edit_leaves_the_macros(user):
    guess = eat(user, {"description": "Dal", "kcal": 180, "estimated": True, "portion": "a bowl",
                       "protein_g": 9})["ids"][0]
    same = services.edit_entry(user.id, "food_entry", guess, {"protein_g": 9}, "web")
    assert same["changed"] == ["protein_g"] and same["after"]["macros_estimated"] is False
    assert "protein_g 9 confirmed by the person" in same["summary"]
    assert services.edit_entry(user.id, "food_entry", guess, {"protein_g": 9}, "web")["changed"] == []
    other = eat(user, {"description": "Rice", "kcal": 200, "estimated": True, "portion": "a plate",
                       "carbs_g": 44})["ids"][0]
    kcal = services.edit_entry(user.id, "food_entry", other, {"kcal": 210}, "claude")
    assert kcal["after"]["source"] == "you_said" and macros_of(kcal["after"]) == (None, 44, None, True)


def test_a_portion_edit_and_a_macro_edit_pass_the_checks_a_log_does(user):
    e = eat(user, {"description": "Dal", "kcal": 180})["ids"][0]
    r = services.edit_entry(user.id, "food_entry", e, {"portion": "2 bowls"}, "claude")
    assert r["after"]["portion"] == "2 bowls" and r["summary"] == "Food entry changed: portion set to '2 bowls'."
    r = services.edit_entry(user.id, "food_entry", e, {"portion": "1 bowl"}, "claude")
    assert r["summary"] == "Food entry changed: portion from '2 bowls' to '1 bowl'."
    r = services.edit_entry(user.id, "food_entry", e, {"portion": None}, "claude")
    assert r["after"]["portion"] is None and r["summary"] == "Food entry changed: portion '1 bowl' cleared."
    assert services.edit_entry(user.id, "food_entry", e, {"fat_g": 556}, "claude")["error"] == "invalid_field"
    assert services.edit_entry(user.id, "food_entry", e, {"portion": "x" * 65}, "claude")["error"] == "invalid_field"
    assert services.edit_entry(user.id, "food_entry", e, {"macros_estimated": True}, "claude")["error"] == \
        "invalid_field"  # the mark is Spotter's: an edit sends grams


def test_edit_entry_passes_a_null_macro_on_through_mcp(http):
    token = sign_for("user_01EDITMACRO")
    logged = ok(http, token, "log_food", {"entries": [WRAP]})
    entry = logged["ids"][0]
    r = ok(http, token, "edit_entry", {"kind": "food", "entry_id": entry, "carbs_g": None, "portion": "1 wrap"})
    assert r["changed"] == ["carbs_g", "portion"] and r["after"]["carbs_g"] is None
    assert r["after"]["portion"] == "1 wrap" and r["after"]["macros_estimated"] is False


def test_an_undo_of_a_version_from_before_0015_puts_the_new_columns_back_as_null(user):
    e = eat(user, {"description": "Dal", "kcal": 180})["ids"][0]
    with db.session_scope() as s:
        row = s.get(FoodEntry, e)
        before = common.version_of(row)
        for column in common.ADDED_IN_0015["food_entry"]:
            del before[column]  # as a version kept before 0015 holds it
        s.add(EntryVersion(user_id=user.id, kind="food_entry", entry_id=e, by="claude", why="edit",
                           before=json.dumps(before)))
        row.protein_g, row.macros_estimated, row.portion = 20, False, "a bowl"
    r = services.restore_entry(user.id, "food_entry", e, "claude", undo_edit=True)
    assert r["undone"] is True and macros_of(r["entry"]) == (None, None, None, None) and r["entry"]["portion"] is None


def test_an_undo_of_a_macro_edit_brings_the_estimate_back(user):
    guess = eat(user, {"description": "Dal", "kcal": 180, "estimated": True, "portion": "a bowl",
                       "protein_g": 9})["ids"][0]
    services.edit_entry(user.id, "food_entry", guess, {"protein_g": 12}, "claude")
    r = services.restore_entry(user.id, "food_entry", guess, "claude", undo_edit=True)
    assert macros_of(r["entry"]) == (9, None, None, True) and r["entry"]["portion"] == "a bowl"


# ---------------------------------------------------------------- the saved meal

def lunch(user, **extra):
    roti = saved(user, protein_g_per_serving=3, carbs_g_per_serving=18, fat_g_per_serving=0.5)["id"]
    dal = saved(user, name="Dal", kcal=180, protein_g_per_serving=9, carbs_g_per_serving=20, fat_g_per_serving=6)["id"]
    meal = services.save_food(user.id, {"name": "Usual lunch", "items": [
        {"saved_food_id": roti, "servings": 2}, {"saved_food_id": dal}], **extra}, "claude")
    return roti, dal, meal


def test_a_meal_is_saved_with_its_items_and_spotter_s_sums(user):
    roti, dal, meal = lunch(user)
    assert "error" not in meal, meal
    f = meal["food"]
    assert (f["serving_label"], f["kcal_per_serving"], f["protein_g_per_serving"], f["carbs_g_per_serving"],
            f["fat_g_per_serving"]) == ("1 meal", 420, 15, 56, 7)
    assert f["items"] == [
        {"description": "Roti", "kcal": 240, "protein_g": 6, "carbs_g": 36, "fat_g": 1, "source": "saved_food",
         "saved_food_id": roti, "servings": 2, "usda_fdc_id": None, "grams": None},
        {"description": "Dal", "kcal": 180, "protein_g": 9, "carbs_g": 20, "fat_g": 6, "source": "saved_food",
         "saved_food_id": dal, "servings": 1, "usda_fdc_id": None, "grams": None}]
    assert meal["summary"] == ("Saved Usual lunch: 1 meal is 420 kcal, from 2 foods. Protein 15 g, carbs 56 g and fat "
                               "7 g a serving.")
    assert tr.check(meal["summary"], tr.SUMMARY) == []
    SavedFoodModel.model_validate(f)


def test_a_meal_is_found_with_its_items_and_logged_by_its_id_as_one_entry(user):
    _, _, meal = lunch(user)
    hit = next(h for h in services.find_food(user.id, "lunch")["saved"] if h["id"] == meal["id"])
    assert len(hit["items"]) == 2 and hit["kcal_per_serving"] == 420
    r = eat(user, {"saved_food_id": meal["id"]})
    [entry] = r["entries"]
    assert (entry["source"], entry["kcal"], entry["servings"], entry["description"]) == (
        "saved_food", 420, 1, "Usual lunch")
    assert macros_of(entry) == (15, 56, 7, False)
    assert len(rows(user.id)) == 1


def test_a_meal_s_macro_is_null_when_an_item_lacks_it_and_own_numbers_are_taken(user, keyed, monkeypatch):
    fake(monkeypatch, with_banana())
    meal = services.save_food(user.id, {"name": "Snack", "serving_label": "1 plate", "items": [
        {"description": "Biscuits", "kcal": 140, "protein_g": 2}, {"usda_fdc_id": 173944, "grams": 100}]}, "claude")
    f = meal["food"]
    assert (f["serving_label"], f["kcal_per_serving"], f["protein_g_per_serving"], f["carbs_g_per_serving"]) == (
        "1 plate", 229, 3.1, None)
    assert [i["source"] for i in f["items"]] == ["you_said", "usda"] and f["items"][1]["usda_fdc_id"] == 173944


def test_a_meal_refuses_an_estimate_a_meal_inside_it_its_own_sums_and_a_count_outside_2_to_30(user):
    roti, _, meal = lunch(user)
    one = {"saved_food_id": roti}
    cases = [
        ([one, {"description": "Rice", "kcal": 200, "estimated": True}], "invalid_field", "Item 2: estimated was sent.",
         "If the kcal is the person's own, send the item without estimated; else ask them for it. Nothing was saved."),
        ([one, {"saved_food_id": meal["id"]}], "invalid_field", "Item 2 is a saved meal itself.",
         "Send that meal's foods as items instead."),
        ([one], "invalid_field", "items holds 1; a meal needs 2 to 30.",
         "Send items as a list: one object per food of the meal, 2 to 30."),
        ([one] * 31, "invalid_field", "items holds 31; a meal needs 2 to 30.",
         "Send items as a list: one object per food of the meal, 2 to 30."),
        ([one, {"description": "Rice"}], "missing_field", "Item 2: kcal is missing.",
         "Give kcal as a number from 0 to 5,000 on item 2: it is kept as a whole number."),
        ([one, {"saved_food_id": roti, "kcal": 5}], "invalid_field", "Item 2: kcal was sent with saved_food_id.",
         "Leave kcal out: the app supplies the number for a saved or USDA food."),
        ([one, {"description": "Rice", "kcal": 200, "portion": "a bowl"}], "invalid_field",
         "Item 2: 'portion' is not a field of a meal's item.", f"Use: {', '.join(food.ITEM_FIELDS)}."),
    ]
    for items, code, message, fix in cases:
        refused = services.save_food(user.id, {"name": "Other", "items": items}, "claude")
        assert (refused["error"], refused["message"], refused["fix"]) == (code, message, fix), items
        for text in (message, fix):
            assert tr.check(text, tr.MESSAGE) == [], text
    sums = services.save_food(user.id, {"name": "Other", "items": [one, one], "kcal_per_serving": 300}, "claude")
    assert sums["message"] == "kcal_per_serving was sent with items."
    assert sums["fix"] == "Leave kcal_per_serving out: Spotter adds up a meal's numbers from its items."
    over = services.save_food(user.id, {"name": "Feast", "items": [{"description": "a", "kcal": 3000},
                                                                   {"description": "b", "kcal": 3000}]}, "claude")
    assert over["message"] == "The meal's kcal_per_serving works out at 6,000 kcal, outside 0 to 5,000."
    with db.new_session() as s:
        assert {f.name for f in s.scalars(select(SavedFood))} == {"Roti", "Dal", "Usual lunch"}


def test_a_meal_saved_again_keeps_a_version_and_an_undo_brings_its_items_back(user):
    roti, dal, meal = lunch(user)
    again = services.save_food(user.id, {"name": "usual lunch", "items": [
        {"saved_food_id": roti, "servings": 3}, {"saved_food_id": dal}]}, "claude")
    assert again["created"] is False and again["food"]["kcal_per_serving"] == 540
    undone = services.restore_entry(user.id, "saved_food", meal["id"], "claude", undo_edit=True)
    assert undone["undone"] is True and undone["entry"]["kcal_per_serving"] == 420
    assert undone["entry"]["items"][0]["servings"] == 2
    versions = versions_of(meal["id"])
    plain = services.save_food(user.id, {"name": "Usual lunch", "serving_label": "1 plate", "kcal_per_serving": 400},
                               "claude")
    assert plain == MEAL_TAKEN  # a plain save of a meal's name is refused, so the meal keeps its foods
    with db.new_session() as s:
        kept = s.get(SavedFood, meal["id"])
        assert kept.kcal_per_serving == 420 and len(json.loads(kept.items)) == 2
    assert versions_of(meal["id"]) == versions  # nothing written


def versions_of(food_id: int) -> int:
    with db.new_session() as s:
        return s.scalar(select(func.count()).select_from(EntryVersion).where(
            EntryVersion.kind == "saved_food", EntryVersion.entry_id == food_id))


MEAL_TAKEN = {"error": "invalid_field", "message": "name 'Usual lunch' is a saved meal.",
              "fix": "Send the meal's foods as items to change it, or give another name. Nothing was saved."}


def test_a_save_over_a_saved_food_keeps_a_macro_left_out_and_null_clears_it(user):
    roti = saved(user, protein_g_per_serving=3, carbs_g_per_serving=18, fat_g_per_serving=0.4)["id"]
    again = saved(user, kcal=125)
    assert (again["food"]["kcal_per_serving"], again["food"]["protein_g_per_serving"],
            again["food"]["carbs_g_per_serving"], again["food"]["fat_g_per_serving"]) == (125, 3, 18, 0.4)
    assert again["summary"] == "Updated Roti: 1 roti is 125 kcal. Protein 3 g, carbs 18 g and fat 0.4 g a serving."
    cleared = saved(user, kcal=125, carbs_g_per_serving=None, fat_g_per_serving=1)
    assert (cleared["food"]["protein_g_per_serving"], cleared["food"]["carbs_g_per_serving"],
            cleared["food"]["fat_g_per_serving"]) == (3, None, 1)
    fresh = saved(user, name="Dal", kcal=180, protein_g_per_serving=9)
    assert (fresh["food"]["carbs_g_per_serving"], fresh["food"]["fat_g_per_serving"]) == (None, None)
    logged = eat(user, {"saved_food_id": roti, "servings": 2})["entries"][0]
    assert macros_of(logged) == (6, None, 2, False)


def test_a_deleted_meal_brought_back_by_a_plain_save_takes_only_what_is_sent(user):
    roti, dal, meal = lunch(user)
    preview = services.delete_entry(user.id, "saved_food", meal["id"], "claude")
    services.delete_entry(user.id, "saved_food", meal["id"], "claude", confirm=preview["confirm"])
    back = services.save_food(user.id, {"name": "Usual lunch", "serving_label": "1 plate", "kcal_per_serving": 400,
                                        "protein_g_per_serving": 20}, "claude")
    assert back["summary"].startswith("Brought back and updated Usual lunch")
    assert back["food"]["items"] is None and (back["food"]["protein_g_per_serving"],
                                              back["food"]["carbs_g_per_serving"],
                                              back["food"]["fat_g_per_serving"]) == (20, None, None)


def test_an_old_chat_s_three_field_save_keeps_a_food_s_macros_and_never_turns_a_meal_plain(http):
    """A chat opened before C3 holds save_food with name, serving_label and kcal_per_serving only."""
    token = sign_for("user_01OLDSAVE")
    roti = ok(http, token, "save_food", {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120,
                                         "protein_g_per_serving": 3, "carbs_g_per_serving": 18,
                                         "fat_g_per_serving": 0.4})["id"]
    dal = ok(http, token, "save_food", {"name": "Dal", "serving_label": "1 bowl", "kcal_per_serving": 180})["id"]
    meal = ok(http, token, "save_food", {"name": "Usual lunch", "items": [{"saved_food_id": roti, "servings": 2},
                                                                          {"saved_food_id": dal}]})
    again = ok(http, token, "save_food", {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 125})
    assert (again["food"]["protein_g_per_serving"], again["food"]["carbs_g_per_serving"],
            again["food"]["fat_g_per_serving"]) == (3, 18, 0.4)
    logged = ok(http, token, "log_food", {"entries": [{"saved_food_id": roti, "servings": 2}]})["entries"][0]
    assert (logged["kcal"], *macros_of(logged)) == (250, 6, 36, 0.8, False)
    refused = failed(http, token, "save_food", {"name": "Usual lunch", "serving_label": "1 meal",
                                                "kcal_per_serving": 600})
    assert {k: refused[k] for k in MEAL_TAKEN} == MEAL_TAKEN
    found = ok(http, token, "find_food", {"query": "usual lunch"})["saved"][0]
    assert found["id"] == meal["id"] and found["kcal_per_serving"] == 420 and len(found["items"]) == 2
    cleared = ok(http, token, "save_food", {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 125,
                                            "fat_g_per_serving": None})
    assert (cleared["food"]["protein_g_per_serving"], cleared["food"]["fat_g_per_serving"]) == (3, None)


def test_a_plain_saved_food_takes_its_macros_a_serving_and_their_ranges(user):
    r = saved(user, protein_g_per_serving=3.04, fat_g_per_serving=0.4)
    assert (r["food"]["protein_g_per_serving"], r["food"]["carbs_g_per_serving"], r["food"]["fat_g_per_serving"]) == (
        3, None, 0.4)
    assert r["summary"] == "Saved Roti: 1 roti is 120 kcal. Protein 3 g and fat 0.4 g a serving."
    assert saved(user, name="Fat", fat_g_per_serving=556)["error"] == "invalid_field"
    plain = services.save_food(user.id, {"name": "Tea", "kcal_per_serving": 40}, "claude")
    assert plain["error"] == "missing_field"  # a plain food still needs its serving


def test_save_food_takes_a_meal_through_mcp_and_its_item_s_unknown_key_is_named(http):
    token = sign_for("user_01MEAL")
    roti = ok(http, token, "save_food", {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120,
                                         "protein_g_per_serving": 3})["id"]
    meal = ok(http, token, "save_food", {"name": "Usual lunch", "items": [{"saved_food_id": roti, "servings": 2},
                                                                          {"description": "Dal", "kcal": 180}]})
    assert meal["food"]["kcal_per_serving"] == 420 and meal["food"]["protein_g_per_serving"] is None
    refused = failed(http, token, "save_food", {"name": "X", "items": [{"saved_food_id": roti},
                                                                      {"description": "Rice", "kcal": 200,
                                                                       "estimated": True}]})
    assert refused["message"] == "Item 2: 'estimated' is not a field of a meal's item."
    assert refused["fix"].endswith(" If the kcal is the person's own, send the item without estimated; else ask "
                                   "them for it. Nothing was saved.")
    logged = ok(http, token, "log", {"kind": "food", "details": {"entries": [{"saved_food_id": meal["id"]}]}})
    assert logged["entries"][0]["kcal"] == 420 and logged["entries"][0]["source"] == "saved_food"


# ---------------------------------------------------------------- photos (decision 15 A)

BINARY = ("binary", "base64", "base64url", "byte")  # how pydantic and JSON Schema publish bytes


def schema_nodes(node):
    """Every object in a JSON schema, nested ones and $defs included."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from schema_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from schema_nodes(value)


def test_no_function_takes_an_image_no_input_schema_has_a_bytes_field_or_a_content_encoding(http):
    tools = registered_tools(http)  # every registered function, the hidden ones too
    assert "log_food" in tools and "save_food" in tools
    for name, tool in tools.items():
        for node in schema_nodes(tool["inputSchema"]):
            assert not {"contentEncoding", "contentMediaType"} & set(node), name
            assert node.get("format") not in BINARY, name


# ---------------------------------------------------------------- the call log

def test_atwater_notes_each_entry_with_all_three_macros_and_no_value(http):
    token = sign_for("user_01ATWATER")
    ok(http, token, "log_food", {"entries": [WRAP, {"description": "Tea", "kcal": 40, "protein_g": 1},
                                             {"description": "Water", "kcal": 0, "protein_g": 0, "carbs_g": 0,
                                              "fat_g": 0}]})
    # 4 x 32 + 4 x 48 + 9 x 18 = 482 over 520: 0.93. Tea lacks two; water has no kcal above 0.
    assert notes()[-1] == "atwater=0.93"
    ok(http, token, "log", {"kind": "meal", "details": {"entries": [{**WRAP, "kcal": 482}]}})
    assert notes()[-1] == "kind=food word=meal atwater=1"  # after kind= and word=, from the food service
    ok(http, token, "log_food", {"entries": [{"description": "Tea", "kcal": 40}]})
    assert notes()[-1] is None
    refused = failed(http, token, "log_food", {"entries": [WRAP, {"description": "x", "kcal": 1, "estimated": True}]})
    assert refused["error"] == "missing_field" and notes()[-1] is None  # a refused call notes none


def test_atwater_is_cut_at_a_whole_item_to_the_room_left(user):
    entries = [{"description": f"Food {n}", "kcal": 100 + n, "protein_g": 10, "carbs_g": 10, "fat_g": 1}
               for n in range(30)]
    held = calllog.new_notes()
    calllog.note("kind", "food")
    calllog.note("word", "x" * 30)
    services.log_food(user.id, {"entries": entries}, "claude")
    word = held["atwater"]
    items = word.split(",")
    assert len(" ".join(f"{k}={v}" for k, v in held.items())) <= calllog.NOTES_MAX
    assert all(len(item) <= 4 for item in items) and 0 < len(items) < 30
    every = food.atwater_items([{"source": "you_said", "kcal": e["kcal"], "protein_g": 10, "carbs_g": 10, "fat_g": 1}
                                for e in entries])
    assert items == every[:len(items)] and len(",".join(every[:len(items) + 1])) > calllog.room("atwater")


def test_the_call_log_knows_every_new_name_and_writes_names_only(user):
    for name in ("portion", "protein_g", "carbs_g", "fat_g", "macros_estimated", "items", "protein_g_per_serving",
                 "carbs_g_per_serving", "fat_g_per_serving"):
        assert name in calllog.KNOWN_NAMES, name
    tried = calllog.tried("save_food", {"name": "Lunch", "items": [{"saved_food_id": 1, "servings": 2},
                                                                   {"description": "Dal", "kcal": 180}]})
    assert tried == "items[2]{description,kcal,saved_food_id,servings} name"
    assert calllog.tried("log_food", {"entries": [{**WRAP, "portion": "a big bowl"}]}) == (
        "entries[1]{carbs_g,description,fat_g,kcal,macros_estimated,portion,protein_g}")


# ---------------------------------------------------------------- the model-read text

def test_the_food_sentence_is_in_log_food_s_description_after_the_food_rule_and_its_words_keep_the_rules(http):
    from app import mcp_server
    assert LOG_FOOD_MACROS == ("Send protein_g, carbs_g and fat_g when the person gave them or you can work them "
                               "out; an estimate needs its portion.")
    assert f"{mcp_server.FOOD_RULE} {LOG_FOOD_MACROS} " in LOG_FOOD_DESCRIPTION
    assert LOG_FOOD_MACROS not in mcp_server.INSTRUCTIONS and len(mcp_server.INSTRUCTIONS) == 4084
    tools = registered_tools(http)
    entry = tools["log_food"]["inputSchema"]["$defs"]["FoodEntryIn"]["properties"]
    assert entry["portion"]["description"] == FoodEntryIn.model_fields["portion"].description
    assert entry["protein_g"]["description"] == ("Protein in grams for what was eaten: the person's number, or yours. "
                                                 "Never with saved_food_id or usda_fdc_id.")
    assert entry["macros_estimated"]["description"] == ("false only when the person stated these grams, such as from "
                                                        "a label. Left out, they count as your estimate.")
    assert entry["portion"]["description"] == (
        "How much was eaten, in the person's words, such as a big bowl or 2. Needed with estimated true: if the "
        "person did not say, ask once, and send nothing until they answer.")
    save = tools["save_food"]
    assert save["description"].endswith(" With items, it saves a meal of several foods: Spotter adds up the meal's "
                                        "numbers, and it is logged by its id as one food.")
    saved_out = save["outputSchema"]["$defs"]["SavedFood"]["properties"]["id"]["description"]
    assert saved_out == "The saved food's id. Log it as saved_food_id, with servings: a meal too."
    def words(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    yield value
                else:
                    yield from words(value)
        elif isinstance(node, list):
            for value in node:
                yield from words(value)

    texts = [text for name in ("log_food", "save_food", "edit_entry", "find_food", "get_log")
             for text in words(tools[name])]
    assert len(texts) > 100
    for text in texts:
        assert tr.check(text, tr.CLAUDE) == [], text
