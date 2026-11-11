"""log_food, save_food, find_food: the tag is the server's, a number and an id never travel together,
the meal comes from the clock, and the day's numbers come from one helper."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import config, db, services, users
from app.models import CallLog, FoodEntry, SavedFood, User
from app.services import food, usda
from app.services.common import WRITE_LIMIT
from tests.conftest import settings as make_settings

NOON = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def user(make_user):
    return make_user()


def eat(user, *entries, by="claude", **data):
    return services.log_food(user.id, {"entries": list(entries), **data}, by)


def roti(user, kcal=120):
    return services.save_food(user.id, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": kcal}, "claude")


def stored(user_id):
    with db.new_session() as s:
        return list(s.scalars(select(FoodEntry).where(FoodEntry.user_id == user_id).order_by(FoodEntry.id)))


# ---------------------------------------------------------------- the tag is the server's

def test_a_stated_number_is_you_said_and_a_guess_is_claude_estimate(user):
    r = eat(user, {"description": "dal", "kcal": 180}, {"description": "rice", "kcal": 200, "estimated": True})
    assert [e["source"] for e in r["entries"]] == ["you_said", "claude_estimate"]
    assert [e["estimated"] for e in r["entries"]] == [False, True]
    assert r["kind"] == "food_entry" and r["ids"] == [e["id"] for e in r["entries"]] and r["duplicate"] is False
    assert set(r) == {"kind", "ids", "duplicate", "entries", "day", "summary", "links", "note"}
    assert set(r["entries"][0]) == {"id", "date", "meal", "description", "kcal", "source", "estimated",
                                    "saved_food_id", "servings", "usda_fdc_id", "grams", "is_sample"}
    assert r["entries"][0]["is_sample"] is False
    assert r["note"].startswith("1 entry is Claude's estimate:")
    two = eat(user, {"description": "a", "kcal": 1, "estimated": True}, {"description": "b", "kcal": 2, "estimated": True})
    assert two["note"].startswith("2 entries are Claude's estimates: the person can confirm or correct them")


def test_a_saved_food_goes_by_id_and_servings_and_the_app_works_out_kcal(user):
    saved = roti(user)
    r = eat(user, {"saved_food_id": saved["id"], "servings": 2})
    e = r["entries"][0]
    assert (e["source"], e["kcal"], e["servings"], e["saved_food_id"], e["description"]) == (
        "saved_food", 240, 2.0, saved["id"], "Roti")
    assert eat(user, {"saved_food_id": saved["id"], "description": "two rotis"})["entries"][0]["kcal"] == 120
    assert "note" not in r


def test_the_source_is_never_an_input(user):
    r = eat(user, {"description": "dal", "kcal": 180, "source": "saved_food"})
    assert r["error"] == "invalid_field" and "decided by the server" in r["fix"] and stored(user.id) == []


@pytest.mark.parametrize("entry", [
    {"saved_food_id": "SAVED", "kcal": 100},
    {"usda_fdc_id": 123, "grams": 100, "kcal": 100},
    {"saved_food_id": "SAVED", "kcal": 100, "estimated": True},
    {"usda_fdc_id": 123, "grams": 100, "kcal": 0},
])
def test_a_number_and_an_id_never_travel_together(user, entry):
    saved = roti(user)
    entry = {k: (saved["id"] if v == "SAVED" else v) for k, v in entry.items()}
    r = eat(user, {"description": "x", **entry})
    assert r["error"] == "invalid_field" and "the app supplies the number for a saved or USDA food" in r["fix"]
    assert stored(user.id) == []


def test_every_combination_of_number_and_id(user):
    """kcal, estimated, saved_food_id and usda_fdc_id in every combination: the tag, or the refusal."""
    saved = roti(user)
    for kcal in (None, 150):
        for estimated in (None, False, True):
            for sid in (None, saved["id"]):
                for fdc in (None, 999):
                    entry = {"description": "x"}
                    for key, value in (("kcal", kcal), ("estimated", estimated), ("saved_food_id", sid),
                                       ("usda_fdc_id", fdc)):
                        if value is not None:
                            entry[key] = value
                    if fdc:
                        entry["grams"] = 100
                    r = eat(user, entry, allow_repeat=True)
                    ids = [k for k in ("saved_food_id", "usda_fdc_id") if k in entry]
                    if ids and (kcal is not None or estimated or len(ids) == 2):
                        assert r["error"] == "invalid_field", (entry, r)
                    elif "usda_fdc_id" in entry:
                        assert r["error"] == "usda_off", (entry, r)      # no key in the tests' settings
                    elif "saved_food_id" in entry:
                        assert r["entries"][0]["source"] == "saved_food", entry
                    elif kcal is None:
                        assert r["error"] == "missing_field", entry
                    else:
                        want = "claude_estimate" if estimated else "you_said"
                        assert r["entries"][0]["source"] == want, entry


def test_wrong_amounts_for_the_kind_of_food(user):
    saved = roti(user)
    assert eat(user, {"saved_food_id": saved["id"], "grams": 50})["error"] == "invalid_field"
    assert eat(user, {"saved_food_id": saved["id"], "servings": 51})["error"] == "invalid_field"
    assert eat(user, {"saved_food_id": saved["id"], "servings": 0.05})["error"] == "invalid_field"
    assert eat(user, {"saved_food_id": 10 ** 9})["error"] == "not_found"


# ---------------------------------------------------------------- checks, all or nothing

def test_every_problem_comes_back_at_once_and_nothing_is_saved(user):
    r = eat(user, {"description": "dal", "kcal": 5001}, {"description": "", "kcal": 10}, {"kcal": 5},
            {"description": "x" * 201, "kcal": 1}, {"description": "rice", "kcal": 100, "meal": "brunch"},
            "not an entry", date="2999-01-01")
    kinds = [e["error"] for e in r["errors"]]
    assert kinds.count("invalid_field") >= 4 and "date_in_future" in kinds and "missing_field" in kinds
    assert "Entry 1:" in r["errors"][1]["message"] and stored(user.id) == []
    assert eat(user)["error"] == "invalid_field"
    assert eat(user, *[{"description": "x", "kcal": 1}] * 31)["error"] == "invalid_field"
    assert services.log_food(user.id, {"entries": "dal"}, "claude")["error"] == "invalid_field"
    assert services.log_food(user.id, {"entries": [{"description": "x", "kcal": 1}]}, "robot")["error"] == "invalid_by"
    assert stored(user.id) == []


def test_kcal_is_stored_whole_and_meals_are_forgiven(user):
    r = eat(user, {"description": "toast", "kcal": "180.6", "meal": " Breakfast "})
    assert r["entries"][0]["kcal"] == 181 and r["entries"][0]["meal"] == "breakfast"
    assert stored(user.id)[0].kcal == 181


# ---------------------------------------------------------------- the meal comes from the clock

@pytest.mark.parametrize("hour,minute,meal", [
    (0, 0, "snack"), (0, 30, "snack"), (3, 59, "snack"), (4, 0, "breakfast"), (10, 59, "breakfast"),
    (11, 0, "lunch"), (15, 59, "lunch"), (16, 0, "dinner"), (21, 59, "dinner"), (22, 0, "snack"), (23, 59, "snack"),
])
def test_an_unsaid_meal_comes_from_the_persons_clock(make_user, monkeypatch, hour, minute, meal):
    person = make_user(time_zone="Asia/Kolkata")                    # UTC+5:30
    local = datetime(2026, 11, 10, hour, minute, tzinfo=users.zone_for("Asia/Kolkata"))
    monkeypatch.setattr(db, "utcnow", lambda: local.astimezone(timezone.utc))
    r = eat(person, {"description": "x", "kcal": 1})
    assert r["entries"][0]["meal"] == meal and r["entries"][0]["date"] == "2026-11-10"
    assert food.meal_for(local) == meal


def test_a_meal_the_person_named_wins_over_the_clock(user, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOON)
    assert eat(user, {"description": "x", "kcal": 1, "meal": "snack"})["entries"][0]["meal"] == "snack"


# ---------------------------------------------------------------- the duplicate guard over the whole set

def test_the_same_set_within_ten_minutes_is_a_duplicate(user):
    first = eat(user, {"description": "dal", "kcal": 180}, {"description": "rice", "kcal": 200})
    again = eat(user, {"description": "Rice", "kcal": 200}, {"description": "dal", "kcal": 180}, by="web")
    assert again["duplicate"] is True and again["ids"] == first["ids"] and "allow_repeat" in again["note"]
    assert again["summary"].startswith("Already logged:") and len(stored(user.id)) == 2
    assert eat(user, {"description": "dal", "kcal": 180})["duplicate"] is False         # a part is not the set
    assert eat(user, {"description": "dal", "kcal": 180}, {"description": "rice", "kcal": 200},
               allow_repeat=True)["duplicate"] is False
    assert len(stored(user.id)) == 5


def test_a_set_older_than_ten_minutes_is_logged_again(user):
    eat(user, {"description": "dal", "kcal": 180})
    with db.session_scope() as s:
        for e in s.scalars(select(FoodEntry)):
            e.created_at = e.created_at - timedelta(minutes=11)
    assert eat(user, {"description": "dal", "kcal": 180})["duplicate"] is False


# ---------------------------------------------------------------- the day's numbers

def test_the_day_total_target_remaining_and_estimated_share(make_user):
    person = make_user(calorie_target_kcal=2000)
    r = eat(person, {"description": "dal", "kcal": 300}, {"description": "rice", "kcal": 100, "estimated": True})
    assert r["day"] == {"total_kcal": 400, "estimated_kcal": 100, "estimated_share_pct": 25, "target_kcal": 2000,
                        "remaining_kcal": 1600, "net_kcal": None}
    assert "1,600 kcal left of the 2,000 kcal target" in r["summary"]
    plain = make_user()
    assert eat(plain, {"description": "x", "kcal": 10})["day"]["remaining_kcal"] is None


def test_net_appears_only_with_add_back_on(make_user):
    person = make_user(add_back_exercise_kcal=True)
    services.log_bodyweight(person.id, {"kg": 80}, "web")
    walk = services.log_cardio(person.id, {"exercise": "Outdoor Walk", "minutes": 60}, "web")
    services.log_cardio(person.id, {"exercise": "Stationary Bike", "minutes": 10, "machine_kcal": 70}, "web")
    with db.session_scope() as s:  # the bike entry with no estimate: its machine number counts instead
        from app.models import CardioEntry
        bike = s.scalar(select(CardioEntry).where(CardioEntry.machine_kcal == 70))
        bike.estimated_kcal = None
    r = eat(person, {"description": "dal", "kcal": 900})
    assert r["day"]["net_kcal"] == 900 - walk["estimate"]["kcal"] - 70
    off = services.update_settings(person.id, {"add_back_exercise_kcal": False}, "web")
    assert off["changed"] == ["add_back_exercise_kcal"]
    assert eat(person, {"description": "x", "kcal": 1})["day"]["net_kcal"] is None


def test_log_food_and_get_log_use_the_same_numbers(make_user):
    person = make_user(calorie_target_kcal=1800)
    r = eat(person, {"description": "dal", "kcal": 300, "estimated": True}, {"description": "rice", "kcal": 100})
    day = services.get_log(person.id)["days"][0]["food"]
    assert {k: day[k] for k in r["day"]} == r["day"]
    assert [e["id"] for e in day["meals"][r["entries"][0]["meal"]]] == r["ids"]


# ---------------------------------------------------------------- save_food

def test_save_food_creates_then_updates_by_casefolded_name(user):
    first = roti(user)
    assert first["created"] is True and first["food"]["kcal_per_serving"] == 120 and first["kind"] == "saved_food"
    assert set(first) == {"kind", "id", "created", "food", "summary", "links"}
    again = services.save_food(user.id, {"name": "  ROTI ", "serving_label": "1 roti (40 g)",
                                         "kcal_per_serving": 110, "grams_per_serving": 40}, "web")
    assert again["created"] is False and again["id"] == first["id"]
    assert again["food"]["name"] == "ROTI" and again["food"]["grams_per_serving"] == 40
    assert "Updated" in again["summary"]


def test_a_deleted_saved_food_comes_back_live(user):
    first = roti(user)
    assert services.delete_entry(user.id, "saved_food", first["id"])["deleted"] is True
    assert services.find_food(user.id, "roti")["saved"] == []
    back = roti(user, kcal=125)
    assert back["id"] == first["id"] and back["created"] is False and "Brought back" in back["summary"]
    assert services.find_food(user.id, "roti")["saved"][0]["kcal_per_serving"] == 125


def test_save_food_checks(user):
    bad = services.save_food(user.id, {"name": "", "serving_label": "x" * 65, "kcal_per_serving": 5001,
                                       "grams_per_serving": 0}, "claude")
    assert [e["error"] for e in bad["errors"]] == ["missing_field", "invalid_field", "invalid_field", "invalid_field"]
    assert services.save_food(user.id, {"name": "a", "serving_label": "b", "kcal_per_serving": 1, "x": 1},
                              "claude")["error"] == "invalid_field"
    with db.new_session() as s:
        assert list(s.scalars(select(SavedFood))) == []


# ---------------------------------------------------------------- find_food

def test_find_food_saved_by_times_logged_then_name_and_recent_by_frequency(user):
    a, b = (services.save_food(user.id, {"name": n, "serving_label": "1", "kcal_per_serving": 100}, "web")
            for n in ("Rice bowl", "Brown rice"))
    services.save_food(user.id, {"name": "Rice cake", "serving_label": "1", "kcal_per_serving": 35}, "web")
    eat(user, {"saved_food_id": a["id"]})                      # Rice bowl twice, Brown rice once:
    eat(user, {"saved_food_id": a["id"], "servings": 2})       # by times logged, not by name
    eat(user, {"saved_food_id": b["id"]})
    for kcal in (200, 210):
        eat(user, {"description": "Rice with dal", "kcal": kcal}, allow_repeat=True)
    eat(user, {"description": "fried rice", "kcal": 400, "estimated": True})
    r = services.find_food(user.id, "RICE")
    assert [f["name"] for f in r["saved"]] == ["Rice bowl", "Brown rice", "Rice cake"]
    assert r["saved"][0]["times_logged"] == 2 and r["saved"][2]["times_logged"] == 0
    assert r["saved"][2]["last_logged"] is None
    assert [(g["description"], g["times"]) for g in r["recent"]] == [("Rice with dal", 2), ("fried rice", 1)]
    assert r["recent"][0]["kcal"] == 210 and r["recent"][1]["source"] == "claude_estimate"
    assert r["usda"] is None and r["usda_note"] == "USDA lookup is off: no key is set."
    assert set(r) == {"query", "saved", "recent", "usda", "usda_note", "summary"}
    assert len(services.find_food(user.id, "rice", limit=1)["saved"]) == 1


def test_find_food_checks_its_arguments(user):
    assert services.find_food(user.id, "")["error"] == "missing_field"
    assert services.find_food(user.id, "a\x00b")["error"] == "invalid_field"
    assert services.find_food(user.id, ["x"])["error"] == "invalid_field"
    for bad in (0, 26, 2.5, "many", True):
        assert services.find_food(user.id, "x", limit=bad)["error"] == "invalid_field"
    assert services.find_food(987654, "x")["error"] == "no_account"


def test_find_food_with_a_key_puts_usda_last_and_says_when_it_did_not_answer(user, monkeypatch):
    config.use_settings(make_settings(usda_api_key="test-key"))
    monkeypatch.setattr(usda, "search", lambda q: ([{"fdc_id": 1, "description": "Bananas, raw",
                                                     "data_type": "SR Legacy", "kcal_per_100g": 89.0}], None))
    r = services.find_food(user.id, "banana")
    assert r["usda"] == [{"fdc_id": 1, "description": "Bananas, raw", "data_type": "SR Legacy", "kcal_per_100g": 89.0}]
    assert r["usda_note"] is None and "1 from USDA" in r["summary"]
    monkeypatch.setattr(usda, "search", lambda q: (None, usda.unavailable()))
    down = services.find_food(user.id, "banana")
    assert down["usda"] is None and down["usda_note"] == "USDA did not answer: use a saved food or the person's own number."


def test_a_usda_food_is_logged_by_id_and_grams(user, monkeypatch):
    config.use_settings(make_settings(usda_api_key="test-key"))
    monkeypatch.setattr(usda, "food", lambda fdc: ({"fdc_id": fdc, "description": "Bananas, raw",
                                                    "data_type": "SR Legacy", "kcal_per_100g": 89.0}, None))
    r = eat(user, {"usda_fdc_id": 173944, "grams": 120})
    e = r["entries"][0]
    assert (e["source"], e["kcal"], e["usda_fdc_id"], e["grams"], e["description"]) == (
        "usda", 107, 173944, 120.0, "Bananas, raw")
    assert eat(user, {"usda_fdc_id": 173944})["error"] == "missing_field"
    monkeypatch.setattr(usda, "food", lambda fdc: (None, usda.unavailable()))
    assert eat(user, {"usda_fdc_id": 173944, "grams": 50})["error"] == "usda_unavailable"


def test_a_usda_id_without_a_key_is_usda_off(user):
    r = eat(user, {"usda_fdc_id": 173944, "grams": 120})
    assert r["error"] == "usda_off" and stored(user.id) == []


# ---------------------------------------------------------------- the lock and the limit

def _calls(user_id, n):
    with db.session_scope() as s:
        s.add_all(CallLog(user_id=user_id, function="log_food", started_at=db.utcnow(), ms=1, ok=True,
                          result_bytes=1) for _ in range(n))


@pytest.mark.parametrize("call", [
    lambda u: eat(u, {"description": "dal", "kcal": 180}),
    lambda u: roti(u),
])
def test_food_writes_are_limited(user, call):
    _calls(user.id, WRITE_LIMIT + 1)
    assert call(user)["error"] == "too_many_writes"
    assert stored(user.id) == []
    with db.new_session() as s:
        assert list(s.scalars(select(SavedFood))) == []


@pytest.mark.parametrize("call", [
    lambda u: eat(u, {"description": "dal", "kcal": 180}),
    lambda u: roti(u),
])
def test_food_writes_lock_the_user_row_first(user, call):
    from sqlalchemy import event
    from sqlalchemy.dialects import sqlite
    from app.services.common import lock_statement
    statements = []
    engine = db.get_engine()
    record = lambda *a: statements.append(" ".join(a[2].split()))  # noqa: E731
    event.listen(engine, "before_cursor_execute", record)
    try:
        call(user)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert statements[0] == " ".join(str(lock_statement("sqlite", user.id).compile(dialect=sqlite.dialect())).split())


# ---------------------------------------------------------------- stage 5: the description default (decision 14)

def test_a_saved_food_needs_no_description_and_defaults_to_its_name(user):
    saved = roti(user)
    r = eat(user, {"saved_food_id": saved["id"], "servings": 2})
    assert r["entries"][0]["description"] == "Roti" and r["entries"][0]["servings"] == 2
    named = eat(user, {"saved_food_id": saved["id"], "servings": 1, "description": "  roti at lunch "}, allow_repeat=True)
    assert named["entries"][0]["description"] == "roti at lunch"   # given, it is kept
    blank = eat(user, {"saved_food_id": saved["id"], "description": ""}, allow_repeat=True)
    assert blank["entries"][0]["description"] == "Roti" and blank["entries"][0]["servings"] == 1
    no_id = eat(user, {"kcal": 100})
    assert no_id["error"] == "missing_field" and "description" in no_id["message"]


def test_get_log_shows_a_saved_food_by_name_servings_and_serving(user):
    saved = roti(user)
    eat(user, {"saved_food_id": saved["id"], "servings": 2, "meal": "lunch"},
        {"description": "dal", "kcal": 180, "meal": "lunch"})
    lunch = services.get_log(user.id)["days"][0]["food"]["meals"]["lunch"]
    assert [(e["description"], e["servings"], e["saved_food_name"], e["serving_label"]) for e in lunch] == [
        ("Roti", 2.0, "Roti", "1 roti"), ("dal", None, None, None)]


# ---------------------------------------------------------------- stage 5: list_saved_foods (decision 15)

def test_list_saved_foods_agrees_with_find_food(user):
    r = roti(user)
    dal = services.save_food(user.id, {"name": "dal bowl", "serving_label": "1 bowl", "kcal_per_serving": 180,
                                       "grams_per_serving": 250}, "web")["id"]
    oats = services.save_food(user.id, {"name": "Oats", "serving_label": "1 bowl", "kcal_per_serving": 300}, "web")["id"]
    eat(user, {"saved_food_id": r["id"], "servings": 2}, date=(users.today_for(user) - timedelta(days=3)).isoformat())
    eat(user, {"saved_food_id": r["id"], "servings": 1})
    gone = eat(user, {"saved_food_id": dal, "servings": 1})["ids"][0]
    services.delete_entry(user.id, "food_entry", gone)          # a deleted entry is not counted
    services.delete_entry(user.id, "saved_food", oats)
    listed = services.list_saved_foods(user.id)
    assert [f["name"] for f in listed["foods"]] == ["dal bowl", "Roti", "Oats"]   # live by name, then deleted
    by_name = {f["name"]: f for f in listed["foods"]}
    assert by_name["Roti"] == {"id": r["id"], "name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120,
                               "grams_per_serving": None, "times_logged": 2,
                               "last_logged": users.today_for(user).isoformat(), "is_sample": False,
                               "deleted_at": None}
    assert by_name["dal bowl"]["times_logged"] == 0 and by_name["dal bowl"]["last_logged"] is None
    assert by_name["Oats"]["deleted_at"] is not None
    found = {f["name"]: f for f in services.find_food(user.id, "o")["saved"]}
    for name in ("Roti", "dal bowl"):
        assert {k: found[name][k] for k in ("times_logged", "last_logged")} == \
            {k: by_name[name][k] for k in ("times_logged", "last_logged")}
    assert listed["summary"] == "2 saved foods, 1 deleted."
    assert services.list_saved_foods(987654)["error"] == "no_account"


def test_list_saved_foods_marks_sample_foods(user):
    services.add_sample_data(user.id, "web")
    foods = services.list_saved_foods(user.id)["foods"]
    assert len(foods) == 4 and all(f["is_sample"] for f in foods) and foods[0]["times_logged"] > 0


# ---------------------------------------------------------------- stage 5: the read throttle (decision 13)

def _lookups(user_id, n, function="find_food", minutes_ago=0.0):
    with db.session_scope() as s:
        s.add_all(CallLog(user_id=user_id, function=function, started_at=db.utcnow() - timedelta(minutes=minutes_ago),
                          ms=1, ok=True, result_bytes=1) for _ in range(n))


def test_more_than_120_lookups_in_10_minutes_is_too_many_reads(user, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOON)
    asked = []
    monkeypatch.setattr(usda, "search", lambda q: asked.append(q) or (None, usda.unavailable()))
    _lookups(user.id, services.READ_LIMIT + 1, minutes_ago=4)
    r = services.find_food(user.id, "banana")
    assert r["error"] == "too_many_reads" and r["wait_minutes"] == 6 and "Wait 6 minutes" in r["fix"]
    assert "121 find_food calls" in r["message"] and asked == []   # refused before any lookup


def test_the_limit_itself_and_rows_that_do_not_count_are_allowed(user, make_user, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOON)
    _lookups(user.id, services.READ_LIMIT, minutes_ago=1)               # a direct call has no row of its own
    assert "error" not in services.find_food(user.id, "dal")
    _lookups(user.id, 50, minutes_ago=10.5)                             # outside the window
    _lookups(user.id, 300, function="log_food")                         # writes are the write limit's
    _lookups(make_user().id, 300)                                       # another person's
    assert "error" not in services.find_food(user.id, "dal")
    _lookups(user.id, 1, minutes_ago=0)
    assert services.find_food(user.id, "dal")["error"] == "too_many_reads"
    # The throttle is about find_food alone: other reads go on.
    assert "error" not in services.get_log(user.id) and "error" not in services.list_saved_foods(user.id)


def test_no_account(user):
    assert services.log_food(987654, {"entries": [{"description": "x", "kcal": 1}]}, "web")["error"] == "no_account"
    assert services.save_food(987654, {"name": "a", "serving_label": "b", "kcal_per_serving": 1}, "web")["error"] \
        == "no_account"
    with db.new_session() as s:
        assert s.get(User, user.id) is not None



# ---------------------------------------------------------------- review fixes

def test_a_usda_food_is_fetched_before_the_lock_and_logs_on_a_cache_miss(user, monkeypatch):
    """No network call and no cache write under the user lock: on SQLite the cache write would wait on
    the lock until it timed out."""
    import io
    import json as js
    from app.services import common
    config.use_settings(make_settings(usda_api_key="test-key"))
    order = []
    real_lock = common.lock_user

    def lock(s, uid):
        order.append("lock")
        return real_lock(s, uid)

    def fetch(request, timeout=None):
        order.append("fetch")
        return io.BytesIO(js.dumps({"fdcId": 1001, "dataType": "SR Legacy", "description": "Bananas, raw",
                                    "foodNutrients": [{"nutrient": {"id": 1008, "number": "208", "unitName": "kcal"},
                                                       "amount": 89}]}).encode())

    monkeypatch.setattr(food, "lock_user", lock)
    monkeypatch.setattr(usda, "urlopen", fetch)
    r = eat(user, {"usda_fdc_id": 1001, "grams": 120})
    assert "error" not in r, r
    assert r["entries"][0]["kcal"] == 107 and order == ["fetch", "lock"]
    from app.models import UsdaFood
    with db.new_session() as s:
        assert s.get(UsdaFood, 1001).kcal_per_100g == 89          # the cache was written, outside the lock


def test_a_worked_out_kcal_is_held_to_the_same_range(user, monkeypatch):
    big = services.save_food(user.id, {"name": "Feast", "serving_label": "1 tray", "kcal_per_serving": 5000}, "web")
    r = eat(user, {"saved_food_id": big["id"], "servings": 50})
    assert r["error"] == "invalid_field" and "250,000 kcal" in r["message"] and "fewer servings" in r["fix"]
    assert eat(user, {"saved_food_id": big["id"], "servings": 1})["entries"][0]["kcal"] == 5000
    config.use_settings(make_settings(usda_api_key="test-key"))
    monkeypatch.setattr(usda, "food", lambda fdc: ({"fdc_id": fdc, "description": "Oil", "data_type": "SR Legacy",
                                                    "kcal_per_100g": 884.0}, None))
    oil = eat(user, {"usda_fdc_id": 1, "grams": 1000})
    assert oil["error"] == "invalid_field" and "8,840 kcal" in oil["message"] and "fewer grams" in oil["fix"]
    assert len(stored(user.id)) == 1


def test_the_same_foods_at_another_meal_are_not_a_duplicate(user):
    first = eat(user, {"description": "dal", "kcal": 180, "meal": "lunch"})
    later = eat(user, {"description": "dal", "kcal": 180, "meal": "dinner"})
    assert later["duplicate"] is False and later["ids"] != first["ids"]
    assert eat(user, {"description": "dal", "kcal": 180, "meal": "lunch"})["ids"] == first["ids"]



def test_at_the_write_limit_a_usda_log_makes_no_usda_call(user, monkeypatch):
    config.use_settings(make_settings(usda_api_key="test-key"))
    calls = []
    monkeypatch.setattr(usda, "urlopen", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(OSError()))
    _calls(user.id, WRITE_LIMIT + 1)
    r = eat(user, {"usda_fdc_id": 2002, "grams": 100})
    assert r["error"] == "too_many_writes" and calls == []
    from app.models import UsdaFood
    with db.new_session() as s:
        assert s.get(UsdaFood, 2002) is None
