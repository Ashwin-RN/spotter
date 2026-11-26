"""The write paths agree (contract logic-audit.md, package B).

- The same values logged, reached by an edit of another row, and imported give the same stored values and the same
  key, for a weigh-in, a measurement, a lift session, a cardio session with a distance and a food entry logged alone.
- Each check written in more than one place refuses the same bad value at the same limit in each copy: both of a
  pair, a set's weight for its lift, reps outside 1 to 100, more than 60 sets. The words may differ on purpose, so
  each case compares the error code and the bound, not the sentence.
- An invisible portion: no portion on a log, cleared on an edit, nothing refused.
- The known limits, pinned as they are.

Every test runs on a fixed clock, NOW, so no assertion reads the real date. Each path of a kind writes on its own
account, so the one-a-day rule and the import's match by value never meet another path's row.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, select

from app import db, services
from app.models import Bodyweight, CardioEntry, Exercise, FoodEntry, LiftSession, Measurement
from app.services import cardio, food

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
DAY = (TODAY - timedelta(days=3)).isoformat()      # the day every path writes
EARLIER = (TODAY - timedelta(days=5)).isoformat()  # where the row an edit starts from sits
UNITS = {"weight_unit": "kg", "distance_unit": "km", "length_unit": "cm"}
# What a path may set apart from the values: the row's own id, owner, times and who wrote it.
NOT_VALUES = {"id", "user_id", "created_at", "updated_at", "deleted_at", "logged_by"}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


@pytest.fixture
def three(make_user):
    """One account a path: logged, edited, imported."""
    return [make_user(time_zone="UTC").id for _ in range(3)]


@pytest.fixture
def uid(make_user):
    return make_user(time_zone="UTC").id


def edit(user_id, kind, entry_id, **changes):
    return services.edit_entry(user_id, kind, entry_id, changes, "claude")


def imp(user_id, *rows, dry=False):
    return services.import_entries(user_id, {"rows": list(rows), **UNITS, "dry_run": dry}, "claude")


def stored(model, user_id) -> list[dict]:
    """Every live row of this kind of this account, every column but NOT_VALUES, the key included; a session's sets
    as (lift, number, weight, reps, time, distance) in the order written."""
    with db.new_session() as s:
        rows = s.scalars(select(model).where(model.user_id == user_id, model.deleted_at.is_(None)).order_by(model.id))
        out = []
        for row in rows:
            values = {a.key: getattr(row, a.key) for a in inspect(model).column_attrs if a.key not in NOT_VALUES}
            if isinstance(row, LiftSession):
                values["sets"] = [(x.exercise_id, x.set_number, x.weight_kg, x.reps, x.time_s, x.distance_m)
                                  for x in sorted(row.sets, key=lambda x: x.id)]
            out.append(values)
        return out


def codes(answer: dict) -> list[str]:
    assert "error" in answer, answer
    return [e["error"] for e in answer.get("errors", [answer])]


def exercise_id(name: str) -> int:
    with db.new_session() as s:
        return s.scalar(select(Exercise.id).where(Exercise.name == name))


# ---------------------------------------------------------------- one candidate down the three paths

SETS = [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}] * 3 + [
    {"exercise": "Bench Press", "weight_kg": 70, "reps": 8}]
PATHS = {
    "bodyweight": {
        "model": Bodyweight,
        "log": lambda u: services.log_bodyweight(u, {"kg": 80.5, "date": DAY}, "claude"),
        "start": lambda u: services.log_bodyweight(u, {"kg": 75, "date": EARLIER}, "claude")["id"],
        "edit": {"date": DAY, "kg": 80.5},
        "row": {"kind": "bodyweight", "date": DAY, "weight": 80.5}},
    "measurement": {
        "model": Measurement,
        "log": lambda u: services.log_measurement(u, {"kind": "waist", "value": 84.5, "date": DAY}, "claude"),
        "start": lambda u: services.log_measurement(u, {"kind": "waist", "value": 90, "date": EARLIER}, "claude")["id"],
        "edit": {"date": DAY, "value": 84.5},
        "row": {"kind": "measurement", "date": DAY, "measurement_kind": "waist", "value": 84.5}},
    "lift_session": {
        "model": LiftSession,
        "log": lambda u: services.log_lifts(u, {"date": DAY, "notes": "heavy", "sets": SETS}, "claude"),
        "start": lambda u: services.log_lifts(u, {"date": EARLIER, "sets": [
            {"exercise": "Deadlift", "weight_kg": 140, "reps": 5}]}, "claude")["id"],
        "edit": {"date": DAY, "notes": "heavy", "sets": SETS},
        "row": {"kind": "lift_session", "date": DAY, "notes": "heavy", "sets": [
            {"exercise": "Back Squat", "weight": 100, "reps": 5, "times": 3},
            {"exercise": "Bench Press", "weight": 70, "reps": 8}]}},
    "cardio_entry": {
        # A distance and no speed: the speed is worked out, 10 km/h, and stays out of the key on every path (R5).
        "model": CardioEntry,
        "log": lambda u: services.log_cardio(u, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5,
                                                 "avg_hr": 150, "date": DAY}, "claude"),
        "start": lambda u: services.log_cardio(u, {"exercise": "Outdoor Walk", "minutes": 20, "distance_km": 2,
                                                   "date": EARLIER}, "claude")["id"],
        "edit": {"date": DAY, "exercise": "Outdoor Run", "minutes": 30, "distance_km": 5, "avg_hr": 150},
        "row": {"kind": "cardio_entry", "date": DAY, "exercise": "Outdoor Run", "minutes": 30, "distance": 5,
                "avg_hr": 150}},
    "food_entry": {
        # Logged alone. The edit starts from a stated entry, so nothing an estimate keeps (its portion) is left over.
        "model": FoodEntry,
        "log": lambda u: services.log_food(u, {"date": DAY, "entries": [
            {"description": "Dal rice", "kcal": 600, "meal": "lunch"}]}, "claude"),
        "start": lambda u: services.log_food(u, {"date": EARLIER, "entries": [
            {"description": "Toast", "kcal": 200, "meal": "breakfast"}]}, "claude")["ids"][0],
        "edit": {"date": DAY, "description": "Dal rice", "kcal": 600, "meal": "lunch"},
        "row": {"kind": "food_entry", "date": DAY, "entries": [
            {"description": "Dal rice", "kcal": 600, "meal": "lunch"}]}},
}


@pytest.mark.parametrize("kind", list(PATHS))
def test_the_same_values_logged_edited_into_another_row_and_imported_are_stored_and_keyed_alike(three, kind):
    path = PATHS[kind]
    logged, edited, imported = three
    assert "error" not in path["log"](logged)
    changed = edit(edited, kind, path["start"](edited), **path["edit"])
    assert "error" not in changed and changed["changed"], changed
    assert imp(imported, path["row"])["row_outcomes"]["written"] == 1
    model = path["model"]
    rows = stored(model, logged)
    assert len(rows) == 1
    assert stored(model, edited) == rows, kind
    assert stored(model, imported) == rows, kind  # the import's key by its own formula, which is the log's


def test_a_cardio_key_after_an_edit_is_the_log_s_so_the_same_session_sent_again_is_a_duplicate(uid):
    """R5: the worked-out speed stays out of the key after an edit, as on a log."""
    walk = services.log_cardio(uid, {"exercise": "Outdoor Walk", "minutes": 30, "distance_km": 3}, "claude")
    longer = edit(uid, "cardio_entry", walk["id"], minutes=45)
    assert longer["after"]["speed_kmh"] == 4.0
    again = services.log_cardio(uid, {"exercise": "Outdoor Walk", "minutes": 45, "distance_km": 3}, "claude")
    assert again["duplicate"] is True and again["id"] == walk["id"]


def test_an_imported_cardio_row_keeps_its_key_through_an_edit(uid):
    imp(uid, PATHS["cardio_entry"]["row"])
    [row] = stored(CardioEntry, uid)
    with db.new_session() as s:
        entry = s.scalar(select(CardioEntry.id).where(CardioEntry.user_id == uid))
    assert "error" not in edit(uid, "cardio_entry", entry, minutes=35)
    [after] = stored(CardioEntry, uid)
    assert after["minutes"] == 35 and after["dedupe_key"] == row["dedupe_key"]


# ---------------------------------------------------------------- a check written in more than one place

def test_both_of_a_pair_is_refused_alike_by_log_and_by_edit(uid):
    weigh_in = services.log_bodyweight(uid, {"kg": 80, "date": EARLIER}, "claude")["id"]
    run = services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5}, "claude")["id"]
    pairs = [({"kg": 80, "lb": 176}, "bodyweight", weigh_in),
             ({"distance_km": 5, "distance_mi": 3.1}, "cardio_entry", run),
             ({"speed_kmh": 10, "speed_mph": 6.2}, "cardio_entry", run)]
    for both, kind, entry in pairs:
        if kind == "bodyweight":
            logged = services.log_bodyweight(uid, both, "claude")
        else:
            logged = services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 30, **both}, "claude")
        edited = edit(uid, kind, entry, **both)
        assert codes(logged) == codes(edited) == ["invalid_field"], both
        for answer in (logged, edited):
            assert all(f in answer["message"] for f in both), (both, answer)
    sets = [{"exercise": "Back Squat", "weight_kg": 100, "weight_lb": 220, "reps": 5}]
    session = services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}]}, "claude")
    routine = services.save_routine(uid, {"name": "Legs", "lifts": [
        {"exercise": "Back Squat", "sets": 3, "reps": 5, "weight_kg": 100, "weight_lb": 220}]}, "claude")
    for answer in (services.log_lifts(uid, {"sets": sets}, "claude"),
                   edit(uid, "lift_session", session["id"], sets=sets), routine):
        assert codes(answer) == ["invalid_field"] and "weight_kg" in answer["message"], answer
        assert "weight_lb" in answer["message"], answer


def test_known_difference_a_null_beside_a_value_is_taken_by_log_and_refused_by_edit(uid):
    """Pinned as it is: log reads a null in the other unit as not sent; edit reads the pair as both sent, as a null
    there clears a distance or a speed. The contract's known limits do not list it."""
    weigh_in = services.log_bodyweight(uid, {"kg": 80, "date": EARLIER}, "claude")["id"]
    run = services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5,
                                    "date": EARLIER}, "claude")["id"]
    assert "error" not in services.log_bodyweight(uid, {"kg": 81, "lb": None}, "claude")
    assert codes(edit(uid, "bodyweight", weigh_in, kg=82, lb=None)) == ["invalid_field"]
    assert "error" not in services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 6,
                                                    "distance_mi": None}, "claude")
    assert codes(edit(uid, "cardio_entry", run, distance_km=6, distance_mi=None)) == ["invalid_field"]


# (lift, weight in kg, the error code or None). The bounds of rules.weight_range: 0 to 500, -200 to 0 on the two
# assisted lifts, and a band lift's 0.
WEIGHTS = [("Back Squat", 500, None), ("Back Squat", 501, "weight_out_of_range"), ("Back Squat", 0, None),
           ("Back Squat", -1, "weight_out_of_range"), ("Assisted Pull-Up", -200, None),
           ("Assisted Pull-Up", -201, "weight_out_of_range"), ("Assisted Pull-Up", 1, "weight_out_of_range"),
           ("Band Pull-Apart", 0, None), ("Band Pull-Apart", 5, "invalid_field")]


@pytest.mark.parametrize("name,kg,code", WEIGHTS)
def test_a_set_s_weight_for_its_lift_is_judged_alike_by_log_edit_and_a_routine(uid, name, kg, code):
    session = services.log_lifts(uid, {"date": EARLIER, "sets": [{"exercise": "Deadlift", "weight_kg": 140,
                                                                  "reps": 5}]}, "claude")["id"]
    sets = [{"exercise": name, "weight_kg": kg, "reps": 5}]
    answers = {"log": services.log_lifts(uid, {"sets": sets}, "claude"),
               "edit": edit(uid, "lift_session", session, sets=sets),
               "routine": services.save_routine(uid, {"name": "One", "lifts": [
                   {"exercise": name, "sets": 3, "reps": 5, "weight_kg": kg}]}, "claude")}
    for path, answer in answers.items():
        if code is None:
            assert "error" not in answer, (path, answer)
        else:
            assert codes(answer) == [code], (path, answer)


@pytest.mark.parametrize("reps,code", [(1, None), (100, None), (0, "reps_out_of_range"), (101, "reps_out_of_range")])
def test_reps_1_to_100_are_judged_alike_by_log_edit_and_a_routine(uid, reps, code):
    session = services.log_lifts(uid, {"date": EARLIER, "sets": [{"exercise": "Deadlift", "weight_kg": 140,
                                                                  "reps": 5}]}, "claude")["id"]
    sets = [{"exercise": "Back Squat", "weight_kg": 100, "reps": reps}]
    answers = {"log": services.log_lifts(uid, {"sets": sets}, "claude"),
               "edit": edit(uid, "lift_session", session, sets=sets),
               "routine": services.save_routine(uid, {"name": "One", "lifts": [
                   {"exercise": "Back Squat", "sets": 3, "reps": reps, "weight_kg": 100}]}, "claude")}
    for path, answer in answers.items():
        if code is None:
            assert "error" not in answer, (path, answer)
        else:
            assert codes(answer) == [code], (path, answer)
            assert "1 to 100" in answer["message"], (path, answer)


ROUTINE_LIFTS = ["Back Squat", "Bench Press", "Deadlift", "Barbell Row", "Overhead Press", "Front Squat", "Leg Press"]


@pytest.mark.parametrize("count,code", [(60, None), (61, "sets_count_out_of_range")])
def test_1_to_60_sets_a_session_are_judged_alike_by_log_edit_the_import_and_a_routine(uid, count, code):
    session = services.log_lifts(uid, {"date": EARLIER, "sets": [{"exercise": "Deadlift", "weight_kg": 140,
                                                                  "reps": 5}]}, "claude")["id"]
    sets = [{"exercise": "Bench Press", "weight_kg": 60, "reps": 5}] * count
    items = [{"exercise": "Bench Press", "weight": 60, "reps": 5}] * count  # one item a set
    by_times = [{"exercise": "Bench Press", "weight": 60, "reps": 5, "times": 20}] * 3 + (
        [{"exercise": "Deadlift", "weight": 140, "reps": 5}] if count > 60 else [])  # 60 or 61 once times counts
    lifts = [{"exercise": n, "sets": 10, "reps": 5} for n in ROUTINE_LIFTS[:6]] + (
        [{"exercise": ROUTINE_LIFTS[6], "sets": 1, "reps": 5}] if count > 60 else [])  # 60 or 61 sets in all
    answers = {"log": services.log_lifts(uid, {"sets": sets}, "claude"),
               "edit": edit(uid, "lift_session", session, sets=sets),
               "routine": services.save_routine(uid, {"name": "Long", "lifts": lifts}, "claude")}
    previews = {"import items": imp(uid, {"kind": "lift_session", "date": DAY, "sets": items}, dry=True),
                "import times": imp(uid, {"kind": "lift_session", "date": DAY, "sets": by_times}, dry=True)}
    for path, answer in answers.items():
        if code is None:
            assert "error" not in answer, (path, answer)
        else:
            assert codes(answer) == [code], (path, answer)
            assert "60" in answer["message"], (path, answer)
    for path, preview in previews.items():
        assert "error" not in preview, (path, preview)
        if code is None:
            assert preview["row_outcomes"]["written"] == 1 and preview["failed"] == [], (path, preview)
        else:
            [failed] = preview["failed"]
            assert failed["error"] == code and "It needs 1 to 60." in failed["message"], (path, failed)
    empty = {"log": services.log_lifts(uid, {"sets": []}, "claude"),
             "edit": edit(uid, "lift_session", session, sets=[])}
    for path, answer in empty.items():
        assert codes(answer) == ["sets_count_out_of_range"], (path, answer)
    none = imp(uid, {"kind": "lift_session", "date": DAY, "sets": []}, dry=True)
    assert [f["error"] for f in none["failed"]] == ["sets_count_out_of_range"]


# ---------------------------------------------------------------- an invisible portion (R6)

INVISIBLE = ["​", " ⁠​ ", "ㅤ", "​⠀"]


def portion_of(user_id, entry_id):
    return services.get_entry(user_id, "food_entry", entry_id)["entry"]["portion"]


@pytest.mark.parametrize("blank", INVISIBLE)
def test_an_invisible_portion_is_no_portion_on_a_log_and_clears_it_on_an_edit(uid, blank):
    stated = services.log_food(uid, {"entries": [{"description": "dal", "kcal": 300, "portion": blank}]}, "claude")
    assert "error" not in stated and portion_of(uid, stated["ids"][0]) is None
    # An estimate still needs a portion, as before: the invisible one is none.
    guess = services.log_food(uid, {"entries": [{"description": "rice", "kcal": 200, "estimated": True,
                                                 "portion": blank}]}, "claude")
    assert codes(guess) == ["missing_field"] and "portion is missing for an estimate" in guess["message"]
    for entry in (
            {"description": "dal", "kcal": 310, "portion": "a bowl"},
            {"description": "rice", "kcal": 210, "estimated": True, "portion": "a plate"}):  # an estimate too
        held = services.log_food(uid, {"entries": [entry]}, "claude")["ids"][0]
        cleared = edit(uid, "food_entry", held, portion=blank)
        assert "error" not in cleared and cleared["changed"] == ["portion"], cleared
        assert portion_of(uid, held) is None
        assert edit(uid, "food_entry", held, portion=blank)["changed"] == []  # no portion again: nothing changed


def test_a_portion_too_long_is_refused_alike_on_a_log_and_an_edit(uid):
    long = "a" * 65
    logged = services.log_food(uid, {"entries": [{"description": "dal", "kcal": 300, "portion": long}]}, "claude")
    held = services.log_food(uid, {"entries": [{"description": "dal", "kcal": 320, "portion": "a bowl"}]},
                             "claude")["ids"][0]
    edited = edit(uid, "food_entry", held, portion=long)
    assert codes(logged) == codes(edited) == ["invalid_field"]
    assert all("the most is 64" in a["message"] for a in (logged, edited))
    # An estimate with a portion refused is not told its portion is missing as well.
    guess = services.log_food(uid, {"entries": [{"description": "rice", "kcal": 200, "estimated": True,
                                                 "portion": long}]}, "claude")
    assert codes(guess) == ["invalid_field"]


# ---------------------------------------------------------------- known limits, pinned as they are

def test_known_limit_an_edit_of_one_food_entry_of_a_set_keys_that_row_alone(uid):
    """The same message sent again within 10 minutes writes the set again: the edited row now sits under its own
    key, so the set's key finds 1 entry of 2."""
    entries = [{"description": "Oats", "kcal": 350, "meal": "breakfast"},
               {"description": "Dal rice", "kcal": 600, "meal": "lunch"}]
    first = services.log_food(uid, {"date": DAY, "entries": entries}, "claude")
    assert edit(uid, "food_entry", first["ids"][1], description="Dal Rice")["changed"] == ["description"]
    rows = stored(FoodEntry, uid)
    assert rows[0]["dedupe_key"] != rows[1]["dedupe_key"]
    assert rows[1]["dedupe_key"] == food.entry_key(TODAY - timedelta(days=3), [
        ("lunch", "dal rice", 600, "you_said", None, None, None, None)])
    again = services.log_food(uid, {"date": DAY, "entries": entries}, "claude")
    assert again["duplicate"] is False and len(stored(FoodEntry, uid)) == 4


def test_known_limit_a_stated_speed_equal_to_distance_over_minutes_keys_with_it_on_a_log_and_without_after_an_edit(
        uid):
    """No column says whether a speed was given; the key is read only by the 10-minute guard."""
    sent = {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5, "speed_kmh": 10, "date": DAY}
    run = services.log_cardio(uid, sent, "claude")
    on, run_id = TODAY - timedelta(days=3), exercise_id("Outdoor Run")
    numbers = {"minutes": 30.0, "distance_km": 5.0, "speed_kmh": 10.0}  # as stored: the key keeps a float at 2 dp
    [row] = stored(CardioEntry, uid)
    assert row["dedupe_key"] == cardio.cardio_key(on, run_id, numbers)
    assert edit(uid, "cardio_entry", run["id"], notes="windy")["changed"] == ["notes"]
    [row] = stored(CardioEntry, uid)
    assert row["speed_kmh"] == 10 and row["dedupe_key"] == cardio.cardio_key(on, run_id, {**numbers, "speed_kmh": None})
    assert services.log_cardio(uid, sent, "claude")["duplicate"] is False  # one duplicate missed
