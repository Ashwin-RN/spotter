"""import_entries: the service alone, with plain dicts. A preview writes nothing; a real run never replaces,
never adds to a held day, never brings back a deleted import row, and writes nothing the second time.

Every test runs on a fixed clock, NOW (Tuesday 10 November 2026, 17:30 in Kolkata), so no assertion reads
the real date. The date-rule tests move the clock themselves, either side of the person's midnight.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, insert, select

from app import db, metrics, services
from app.models import (
    IMPORTED, Base, Bodyweight, CallLog, CardioEntry, Exercise, FoodEntry, Goal, LiftSession, LiftSet, Measurement,
    User,
)
from app.services import body, cardio, food, imports, lifts, measurements
from app.services.common import WRITE_LIMIT
from app.services.entries import SESSION_NOTE

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)   # a Tuesday: 17:30 in Kolkata
TODAY = NOW.date()
ZONE = "Asia/Kolkata"
MODELS = {"bodyweight": Bodyweight, "measurement": Measurement, "lift_session": LiftSession,
          "food_entry": FoodEntry, "cardio_entry": CardioEntry}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


@pytest.fixture
def user(make_user):
    return make_user(time_zone=ZONE)


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------- rows as an export gives them

def weigh(on, weight):
    return {"kind": "bodyweight", "date": on, "weight": weight}


def measure(on, kind, value):
    return {"kind": "measurement", "date": on, "measurement_kind": kind, "value": value}


def lift(on, *sets, **extra):
    """sets: (exercise, weight, reps) or (exercise, weight, reps, times)."""
    return {"kind": "lift_session", "date": on, "sets": [
        {"exercise": s[0], "weight": s[1], "reps": s[2], **({"times": s[3]} if len(s) > 3 else {})} for s in sets],
        **extra}


def meal(on, *entries):
    """entries: (description, kcal, meal)."""
    return {"kind": "food_entry", "date": on,
            "entries": [{"description": d, "kcal": k, "meal": m} for d, k, m in entries]}


def run(on, exercise, minutes, distance=None, **extra):
    row = {"kind": "cardio_entry", "date": on, "exercise": exercise, "minutes": minutes, **extra}
    if distance is not None:
        row["distance"] = distance
    return row


# The units a call names unless a test says otherwise: the defaults the service had before the fix round on
# 7e95ee9, which made every unit a row needs required. A unit given as None is left out of the call.
UNITS = {"weight_unit": "kg", "distance_unit": "km", "length_unit": "cm"}


def imp(user, *rows, dry=False, by="claude", **data):
    """An import of these rows, in kg, km and cm unless data names a unit. dry=None leaves dry_run out."""
    sent = {"rows": list(rows), **{k: v for k, v in {**UNITS, **data}.items() if v is not None}}
    if dry is not None:
        sent["dry_run"] = dry
    return services.import_entries(user.id, sent, by)


def everything() -> dict:
    """Every row of every table, every column: the database, to compare before and after."""
    out = {}
    with db.get_engine().connect() as c:
        for table in Base.metadata.sorted_tables:
            out[table.name] = [tuple(r) for r in c.execute(select(table).order_by(*table.primary_key.columns))]
    return out


def rows_of(model, user_id, **where):
    with db.new_session() as s:
        query = select(model).where(model.user_id == user_id)
        for k, v in where.items():
            query = query.where(getattr(model, k) == v)
        return list(s.scalars(query.order_by(model.id)))


def total(model, user_id) -> int:
    with db.new_session() as s:
        return s.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))


# Per kind: an import row for a date, another row of that kind for the same date, a hand log of something
# different that day (returns its id), a hand log of the same thing, and an edit of the imported row.
KINDS = {
    "bodyweight": {
        "row": lambda d: weigh(d, 80.5), "other": lambda d: weigh(d, 81.25),
        "hand": lambda u, d: services.log_bodyweight(u.id, {"kg": 79, "date": d}, "web")["id"],
        "same": lambda u, d: services.log_bodyweight(u.id, {"kg": 80.5, "date": d}, "web")["id"],
        "edit": {"kg": 82}},
    "measurement": {
        "row": lambda d: measure(d, "waist", 84.5), "other": lambda d: measure(d, "waist", 85),
        "hand": lambda u, d: services.log_measurement(u.id, {"kind": "waist", "value": 86, "date": d}, "web")["id"],
        "same": lambda u, d: services.log_measurement(u.id, {"kind": "waist", "value": 84.5, "date": d}, "web")["id"],
        "edit": {"value": 87}},
    "lift_session": {
        "row": lambda d: lift(d, ("Back Squat", 100, 5, 3), ("Bench Press", 70, 8)),
        "other": lambda d: lift(d, ("Deadlift", 140, 5)),
        "hand": lambda u, d: services.log_lifts(u.id, {"date": d, "sets": [
            {"exercise": "Overhead Press", "weight_kg": 40, "reps": 5}]}, "web")["id"],
        "same": lambda u, d: services.log_lifts(u.id, {"date": d, "notes": "by hand", "sets": [
            {"exercise": "Back Squat", "weight_kg": 100, "reps": 5}] * 3 + [
            {"exercise": "Bench Press", "weight_kg": 70, "reps": 8}]}, "web")["id"],
        "edit": {"sets": [{"exercise": "Back Squat", "weight_kg": 105, "reps": 5}]}},
    "food_entry": {
        "row": lambda d: meal(d, ("Oats", 350, "breakfast"), ("Dal rice", 600, "lunch")),
        "other": lambda d: meal(d, ("Toast", 200, "breakfast")),
        "hand": lambda u, d: services.log_food(u.id, {"date": d, "entries": [
            {"description": "Poha", "kcal": 250, "meal": "breakfast"}]}, "web")["ids"][0],
        "same": lambda u, d: services.log_food(u.id, {"date": d, "entries": [
            {"description": "oats", "kcal": 350, "meal": "breakfast"},
            {"description": "Dal rice", "kcal": 600, "meal": "lunch"}]}, "web")["ids"][0],
        "edit": {"kcal": 400}},
    "cardio_entry": {
        "row": lambda d: run(d, "Outdoor Run", 30, 5), "other": lambda d: run(d, "Outdoor Run", 40, 6),
        "hand": lambda u, d: services.log_cardio(u.id, {"exercise": "Outdoor Run", "minutes": 25, "date": d},
                                                 "web")["id"],
        "same": lambda u, d: services.log_cardio(u.id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5,
                                                        "date": d}, "web")["id"],
        "edit": {"minutes": 33}},
}


def counted(result, kind) -> dict:
    return result["counts"][kind]


def assert_adds_up(result):
    """Rows: each has one outcome, and the outcomes add up to the rows sent. Kinds: each count is the
    rows of that kind (food: its entries)."""
    assert sum(result["row_outcomes"].values()) == result["rows"], result["row_outcomes"]
    assert result["row_outcomes"]["failed"] == len(result["failed"]) + result["more_failed"]
    assert result["row_outcomes"]["day_taken"] == len(result["day_taken"]) + result["more_day_taken"]


# ---------------------------------------------------------------- 1, 2: the preview

def mixed_rows():
    return [weigh(ago(3), 80.4), weigh(ago(2), 80.1), measure(ago(2), "waist", 84), measure(ago(2), "steps", 9000),
            lift(ago(2), ("Back Squat", 100, 5, 3)), meal(ago(2), ("Oats", 350, "breakfast"), ("Dal", 500, "lunch")),
            run(ago(1), "Outdoor Run", 30, 5), weigh(ago(1), 79.9),
            weigh(ago(5), 81),                      # day taken: a hand weigh-in that day
            lift(ago(4), ("Squat (Barbell)", 100, 5)),  # refused: not on the list
            measure(ago(6), "waist", 85)]           # already there: logged by hand, the same value


def test_a_dry_run_writes_nothing_and_the_real_run_writes_exactly_what_it_counted(user):
    services.log_bodyweight(user.id, {"kg": 82, "date": ago(5)}, "web")
    services.log_measurement(user.id, {"kind": "waist", "value": 85, "date": ago(6)}, "web")
    before = everything()
    preview = imp(user, *mixed_rows(), dry=True)
    assert everything() == before   # every row of every table, every column
    assert preview["dry_run"] is True and preview["summary"].startswith("Preview, nothing written.")
    assert_adds_up(preview)
    assert preview["row_outcomes"] == {"written": 8, "already_there": 1, "day_taken": 1, "failed": 1}
    real = imp(user, *mixed_rows(), dry=False)
    assert real["dry_run"] is False
    for key in ("counts", "row_outcomes", "failed", "unknown_names", "ranges", "from", "to"):
        assert real[key] == preview[key], key
    assert [{k: v for k, v in t.items()} for t in real["day_taken"]] == preview["day_taken"]
    after = everything()
    for kind, model in MODELS.items():
        added = len(after[model.__tablename__]) - len(before[model.__tablename__])
        assert added == real["counts"][kind]["written"], kind
    assert len(after["lift_set"]) - len(before["lift_set"]) == 3


# Was: [None, True, "true", "yes", 1, ""] all previewed. Now only left out and true do (the lead's ruling).
@pytest.mark.parametrize("dry_run", [None, True])
def test_dry_run_left_out_or_true_is_a_preview(user, dry_run):
    before = everything()
    r = imp(user, weigh(ago(1), 80), dry=None) if dry_run is None else imp(user, weigh(ago(1), 80), dry=dry_run)
    assert r["dry_run"] is True and r["counts"]["bodyweight"]["written"] == 1
    assert everything() == before


# Was: [False, "false", "False", "no", 0, "0"] all wrote. Now only False writes.
@pytest.mark.parametrize("dry_run", [False])
def test_only_a_plain_false_writes(user, dry_run):
    r = imp(user, weigh(ago(1), 80), dry=dry_run)
    assert r["dry_run"] is False and total(Bodyweight, user.id) == 1


# Was: ["maybe", 2, [], {}, 0.5]. Now the text forms and 0 and 1 are refused too.
@pytest.mark.parametrize("dry_run", ["maybe", 2, [], {}, 0.5, "true", "false", "False", "yes", "no", "off", "on", "",
                                     "0", "1", 0, 1])
def test_a_dry_run_that_is_neither_true_nor_false_is_refused_and_writes_nothing(user, dry_run):
    before = everything()
    r = imp(user, weigh(ago(1), 80), dry=dry_run)
    assert r["error"] == "invalid_field" and r["message"].startswith("dry_run ")
    assert everything() == before


# ---------------------------------------------------------------- 3, 4: the mark, and running it twice

def test_each_kind_is_written_with_the_import_mark_and_its_own_kinds_key(user):
    r = imp(user, *[KINDS[k]["row"](ago(1)) for k in KINDS], by="web")
    assert "error" not in r and r["row_outcomes"]["written"] == 5
    for kind, model in MODELS.items():
        for row in rows_of(model, user.id):
            assert (row.logged_by, row.is_sample, row.deleted_at) == (IMPORTED, False, None), kind
    [w] = rows_of(Bodyweight, user.id)
    assert w.dedupe_key == body.weigh_in_key(w.date, w.kg)
    [m] = rows_of(Measurement, user.id)
    assert m.dedupe_key == measurements.measurement_key(m.date, m.kind, m.value)
    [c] = rows_of(CardioEntry, user.id)
    assert c.dedupe_key == cardio.cardio_key(c.date, c.exercise_id, {"minutes": 30.0, "distance_km": 5.0})
    entries = rows_of(FoodEntry, user.id)
    assert {e.dedupe_key for e in entries} == {food.entry_key(entries[0].date, [food.entry_tuple(e) for e in entries])}
    with db.new_session() as s:
        session = s.scalars(select(LiftSession).where(LiftSession.user_id == user.id)).one()
        keyed = [(x.exercise_id, x.set_number, x.weight_kg, x.reps) for x in session.sets]
        assert session.dedupe_key == lifts.session_key(session.date, session.notes, keyed)


def test_a_hand_log_of_the_same_thing_soon_after_is_a_duplicate_as_now(user):
    imp(user, weigh(TODAY.isoformat(), 80.5), lift(TODAY.isoformat(), ("Deadlift", 100, 5)))
    assert services.log_bodyweight(user.id, {"kg": 80.5}, "claude")["duplicate"] is True
    assert services.log_lifts(user.id, {"sets": [{"exercise": "Deadlift", "weight_kg": 100, "reps": 5}]},
                              "claude")["duplicate"] is True


@pytest.mark.parametrize("kind", list(KINDS))
def test_the_same_import_twice_writes_nothing_the_second_time(user, kind):
    rows = [KINDS[kind]["row"](ago(n)) for n in (1, 2, 3)]
    first = imp(user, *rows)
    assert first["row_outcomes"]["written"] == 3
    before = everything()
    again = imp(user, *rows)
    assert everything() == before   # no new row, no new soft-deleted row
    assert again["row_outcomes"] == {"written": 0, "already_there": 3, "day_taken": 0, "failed": 0}
    assert counted(again, kind)["written"] == 0
    assert again["summary"].startswith("Nothing new: all 3 rows from ")


def test_a_mixed_import_twice_writes_nothing_the_second_time(user):
    imp(user, *mixed_rows())
    before = everything()
    again = imp(user, *mixed_rows())
    assert everything() == before
    assert again["row_outcomes"]["written"] == 0 and again["row_outcomes"]["failed"] == 1


# ---------------------------------------------------------------- 5: units

def test_pounds_miles_and_inches_are_converted_and_kept_at_two_places(user):
    r = imp(user, weigh(ago(1), 225), lift(ago(1), ("Back Squat", 135, 5), ("Pull-Up", 0, 8)),
            run(ago(1), "Outdoor Run", 30, 3.1), measure(ago(1), "waist", 32), measure(ago(1), "steps", 10000),
            weight_unit="lb", distance_unit="mi", length_unit="in")
    assert r["units"] == {"weight": "lb", "distance": "mi", "length": "in"}
    assert [w.kg for w in rows_of(Bodyweight, user.id)] == [102.06]        # 225 x 0.45359237 = 102.058...
    with db.new_session() as s:
        assert [x.weight_kg for x in s.scalars(select(LiftSet).order_by(LiftSet.id))] == [61.23, 0.0]  # 61.234...
    assert [c.distance_km for c in rows_of(CardioEntry, user.id)] == [4.99]  # 3.1 x 1.609344 = 4.988...
    assert {m.kind: m.value for m in rows_of(Measurement, user.id)} == {"waist": 81.28, "steps": 10000}  # 32 x 2.54
    assert r["ranges"] == {"bodyweight_kg": {"lowest": 102.06, "highest": 102.06},
                           "body_size_cm": {"lowest": 81.28, "highest": 81.28},
                           "heaviest_set_kg": 61.23, "longest_km": 4.99}


def test_miles_are_converted_at_the_exact_factor(user):
    imp(user, run(ago(1), "Outdoor Run", 50, 5.9), distance_unit="mi")
    assert rows_of(CardioEntry, user.id)[0].distance_km == 9.5   # 5.9 x 1.609344 = 9.4951..., not 9.49


def test_the_same_file_in_pounds_twice_matches(user):
    rows = [weigh(ago(n), 180.4 + n) for n in range(1, 6)] + [lift(ago(1), ("Deadlift", 315, 5, 3))]
    imp(user, *rows, weight_unit="lb")
    again = imp(user, *rows, weight_unit="LB ")
    assert again["row_outcomes"] == {"written": 0, "already_there": 6, "day_taken": 0, "failed": 0}


def test_kg_km_and_cm_are_stored_as_sent(user):
    imp(user, weigh(ago(1), 80.25), run(ago(1), "Outdoor Run", 30, 5.25), measure(ago(1), "waist", 84.75))
    assert rows_of(Bodyweight, user.id)[0].kg == 80.25 and rows_of(CardioEntry, user.id)[0].distance_km == 5.25
    assert rows_of(Measurement, user.id)[0].value == 84.75


def test_a_wrong_unit_reads_in_the_preview_ranges(user):
    """180 lb sent as kg is inside 20 to 400 kg: the ranges and the summary show it before anything is written."""
    r = imp(user, weigh(ago(2), 180), weigh(ago(1), 182), dry=True)
    assert r["ranges"]["bodyweight_kg"] == {"lowest": 180.0, "highest": 182.0}
    assert "Weigh-ins run from 180 kg to 182 kg." in r["summary"]


# ---------------------------------------------------------------- 6 to 11: held days and matches, per kind

@pytest.mark.parametrize("kind", list(KINDS))
def test_a_held_day_is_never_added_to_or_replaced(user, kind):
    held = KINDS[kind]["hand"](user, ago(1))
    before = everything()
    preview = imp(user, KINDS[kind]["row"](ago(1)), dry=True)
    r = imp(user, KINDS[kind]["row"](ago(1)))
    assert everything() == before   # the held entry is neither replaced nor soft-deleted, and nothing is added
    for result in (preview, r):
        assert result["day_taken"] == [{"row": 1, "kind": kind, "date": ago(1), "entry_id": held}]
        assert result["row_outcomes"]["day_taken"] == 1
    assert "1 was left out: its day already holds an entry. Nothing was replaced." in r["summary"]


@pytest.mark.parametrize("kind", list(KINDS))
def test_an_identical_held_entry_is_already_there(user, kind):
    KINDS[kind]["same"](user, ago(1))
    before = everything()
    r = imp(user, KINDS[kind]["row"](ago(1)))
    assert everything() == before and r["row_outcomes"]["already_there"] == 1


@pytest.mark.parametrize("kind", list(KINDS))
def test_an_edited_import_row_is_left_as_edited(user, kind):
    imp(user, KINDS[kind]["row"](ago(1)))
    first = rows_of(MODELS[kind], user.id)[0].id
    assert services.edit_entry(user.id, kind, first, KINDS[kind]["edit"], "web")["changed"]
    before = everything()
    r = imp(user, KINDS[kind]["row"](ago(1)))
    assert everything() == before   # the edit stands, and nothing comes back beside it
    # Was: day taken, naming the edited entry:
    #     assert r["row_outcomes"]["day_taken"] == 1 and r["day_taken"][0]["entry_id"] == first
    # Now the edited row keeps the key it was imported under, so the row is already there.
    assert r["row_outcomes"] == {"written": 0, "already_there": 1, "day_taken": 0, "failed": 0}


@pytest.mark.parametrize("kind", list(KINDS))
def test_a_deleted_import_row_stays_deleted(user, kind):
    imp(user, KINDS[kind]["row"](ago(1)))
    for row in rows_of(MODELS[kind], user.id):
        assert services.delete_entry(user.id, kind, row.id)["deleted"] is True
    before = everything()
    r = imp(user, KINDS[kind]["row"](ago(1)))
    assert everything() == before
    assert r["row_outcomes"]["already_there"] == 1 and r["row_outcomes"]["written"] == 0


@pytest.mark.parametrize("kind", list(KINDS))
def test_a_deleted_hand_logged_entry_does_not_block(user, kind):
    """Only an imported row counts once deleted: the person's own deleted entry is not a record of the other app."""
    KINDS[kind]["same"](user, ago(1))
    for row in rows_of(MODELS[kind], user.id):
        services.delete_entry(user.id, kind, row.id)
    r = imp(user, KINDS[kind]["row"](ago(1)))
    assert r["row_outcomes"]["written"] == 1


@pytest.mark.parametrize("kind", list(KINDS))
def test_an_edited_then_deleted_import_row_stays_deleted(user, kind):
    """The edit kept the key the row was imported under, so the deleted row still holds it."""
    imp(user, KINDS[kind]["row"](ago(1)))
    first = rows_of(MODELS[kind], user.id)[0].id
    services.edit_entry(user.id, kind, first, KINDS[kind]["edit"], "web")
    for row in rows_of(MODELS[kind], user.id):
        services.delete_entry(user.id, kind, row.id)
    before = everything()
    r = imp(user, KINDS[kind]["row"](ago(1)))
    # Was (as built, ruling wanted): the exported value came back.
    #     assert r["row_outcomes"]["written"] == 1 and r["counts"][kind]["written"] == 1
    assert r["row_outcomes"]["written"] == 0 and r["counts"][kind]["written"] == 0
    assert r["row_outcomes"]["already_there"] == 1 and everything() == before


# A one-entry food day, as the reviewer named it: no second entry stays behind on the old day.
ONE = {**{k: v["row"] for k, v in KINDS.items()}, "food_entry": lambda d: meal(d, ("Oats", 350, "breakfast"))}


@pytest.mark.parametrize("kind", list(KINDS))
def test_a_moved_import_row_is_never_written_again(user, kind):
    """M3: the person moves an imported row to another day; the same import then writes nothing."""
    imp(user, ONE[kind](ago(3)))
    for row in rows_of(MODELS[kind], user.id):
        assert services.edit_entry(user.id, kind, row.id, {"date": ago(2)}, "web")["changed"], kind
    before = everything()
    for dry in (True, False):
        r = imp(user, ONE[kind](ago(3)), dry=dry)
        assert r["row_outcomes"] == {"written": 0, "already_there": 1, "day_taken": 0, "failed": 0}, (kind, dry)
    assert everything() == before


@pytest.mark.parametrize("kind", list(KINDS))
def test_a_moved_and_changed_then_deleted_import_row_is_never_written_again(user, kind):
    imp(user, ONE[kind](ago(3)))
    for row in rows_of(MODELS[kind], user.id):
        services.edit_entry(user.id, kind, row.id, {"date": ago(2)}, "web")
        services.edit_entry(user.id, kind, row.id, KINDS[kind]["edit"], "web")
        services.delete_entry(user.id, kind, row.id)
    before = everything()
    assert imp(user, ONE[kind](ago(3)))["row_outcomes"]["already_there"] == 1 and everything() == before


def test_a_moved_food_entry_of_a_two_entry_day_is_never_written_again(user):
    imp(user, KINDS["food_entry"]["row"](ago(3)))
    oats = rows_of(FoodEntry, user.id)[0]
    services.edit_entry(user.id, "food_entry", oats.id, {"date": ago(2), "kcal": 360}, "web")
    r = imp(user, KINDS["food_entry"]["row"](ago(3)))
    assert r["counts"]["food_entry"] == {"written": 0, "already_there": 2, "day_taken": 0, "failed": 0}
    assert total(FoodEntry, user.id) == 2


@pytest.mark.parametrize("kind", ["bodyweight", "measurement", "cardio_entry"])
def test_a_deleted_wrong_unit_import_can_be_imported_again_with_the_right_unit(user, kind):
    """No undo in version 1: delete the wrong rows, send the file with its right unit. Different numbers, different
    keys: the corrected rows go in."""
    row = {"bodyweight": weigh(ago(1), 180), "measurement": measure(ago(1), "waist", 33),
           "cardio_entry": run(ago(1), "Outdoor Run", 30, 3.1)}[kind]
    unit = {"bodyweight": {"weight_unit": "lb"}, "measurement": {"length_unit": "in"},
            "cardio_entry": {"distance_unit": "mi"}}[kind]
    imp(user, row)
    for r in rows_of(MODELS[kind], user.id):
        services.delete_entry(user.id, kind, r.id)
    again = imp(user, row, **unit)
    assert again["row_outcomes"]["written"] == 1


def test_a_hand_log_of_an_edited_import_row_soon_after_reads_the_import_time_key(user):
    """What the 10-minute guard says once an imported row keeps its key: the weigh-in as imported is a
    duplicate of the edited row, and the edited value is not."""
    imp(user, weigh(TODAY.isoformat(), 80.5))
    [w] = rows_of(Bodyweight, user.id)
    services.edit_entry(user.id, "bodyweight", w.id, {"kg": 81}, "web")
    same_as_imported = services.log_bodyweight(user.id, {"kg": 80.5}, "claude")
    assert same_as_imported["duplicate"] is True and same_as_imported["id"] == w.id
    edited = services.log_bodyweight(user.id, {"kg": 81}, "claude")
    assert edited["duplicate"] is False and edited["replaced"]["id"] == w.id


def test_an_edit_of_a_hand_logged_row_still_works_its_key_out_again(user):
    w = services.log_bodyweight(user.id, {"kg": 80.5, "date": ago(1)}, "web")["id"]
    services.edit_entry(user.id, "bodyweight", w, {"kg": 81, "date": ago(2)}, "web")
    [row] = rows_of(Bodyweight, user.id)
    assert row.dedupe_key == body.weigh_in_key(row.date, 81.0)


def test_an_edit_of_an_imported_row_keeps_its_key(user):
    imp(user, weigh(ago(1), 80.5))
    [row] = rows_of(Bodyweight, user.id)
    key = row.dedupe_key
    services.edit_entry(user.id, "bodyweight", row.id, {"kg": 81, "date": ago(2)}, "web")
    assert rows_of(Bodyweight, user.id)[0].dedupe_key == key == body.weigh_in_key(TODAY - timedelta(days=1), 80.5)


def test_bodyweight_a_second_row_for_the_same_day_in_one_call(user):
    preview = imp(user, weigh(ago(1), 80), weigh(ago(1), 80), weigh(ago(1), 81), dry=True)
    assert preview["row_outcomes"] == {"written": 1, "already_there": 1, "day_taken": 1, "failed": 0}
    assert preview["day_taken"] == [{"row": 3, "kind": "bodyweight", "date": ago(1), "entry_id": None, "by_row": 1}]
    real = imp(user, weigh(ago(1), 80), weigh(ago(1), 80), weigh(ago(1), 81))
    [kept] = rows_of(Bodyweight, user.id)
    assert kept.kg == 80 and real["day_taken"][0]["entry_id"] == kept.id and real["day_taken"][0]["by_row"] == 1


def test_measurement_another_kind_on_a_held_day_is_written(user):
    held = services.log_measurement(user.id, {"kind": "waist", "value": 86, "date": ago(1)}, "web")["id"]
    r = imp(user, measure(ago(1), "waist", 84), measure(ago(1), "hips", 99), measure(ago(1), "sleep hours", 7.5))
    assert r["counts"]["measurement"] == {"written": 2, "already_there": 0, "day_taken": 1, "failed": 0}
    assert r["day_taken"][0]["entry_id"] == held
    assert {m.kind for m in rows_of(Measurement, user.id, deleted_at=None)} == {"waist", "hips", "sleep_hours"}


def test_lift_any_other_session_takes_the_day_and_notes_do_not_matter(user):
    KINDS["lift_session"]["same"](user, ago(1))
    other = KINDS["lift_session"]["hand"](user, ago(2))
    r = imp(user, KINDS["lift_session"]["row"](ago(1)) | {"notes": "from the old app"},
            KINDS["lift_session"]["row"](ago(2)))
    assert r["counts"]["lift_session"] == {"written": 0, "already_there": 1, "day_taken": 1, "failed": 0}
    assert r["day_taken"][0]["entry_id"] == other


def test_lift_two_identical_sessions_in_one_call_both_write_and_both_match_again(user):
    same = KINDS["lift_session"]["row"](ago(1))
    r = imp(user, same, same)
    assert r["counts"]["lift_session"]["written"] == 2 and total(LiftSession, user.id) == 2
    again = imp(user, same, same)
    assert again["counts"]["lift_session"] == {"written": 0, "already_there": 2, "day_taken": 0, "failed": 0}
    third = imp(user, same, same, same)   # each held session matches one row only: the third is day taken
    assert third["counts"]["lift_session"] == {"written": 0, "already_there": 2, "day_taken": 1, "failed": 0}


def test_lift_sets_match_per_lift_in_any_order_across_lifts(user):
    imp(user, lift(ago(1), ("Back Squat", 100, 5), ("Bench Press", 70, 8), ("Back Squat", 102.5, 3)))
    again = imp(user, lift(ago(1), ("Bench Press", 70, 8), ("Back Squat", 100, 5), ("Back Squat", 102.5, 3)))
    assert again["row_outcomes"]["already_there"] == 1
    swapped = imp(user, lift(ago(1), ("Back Squat", 102.5, 3), ("Back Squat", 100, 5), ("Bench Press", 70, 8)))
    assert swapped["row_outcomes"]["day_taken"] == 1   # within a lift the numbering matters


def test_food_a_held_day_is_taken_whole_unless_every_entry_matches(user):
    services.log_food(user.id, {"date": ago(1), "entries": [{"description": "Oats", "kcal": 350, "meal": "breakfast"}]},
                      "web")
    r = imp(user, meal(ago(1), ("oats ", 350, "Breakfast")), meal(ago(1), ("Oats", 350, "breakfast"), ("Tea", 40, "snack")))
    assert r["row_outcomes"] == {"written": 0, "already_there": 1, "day_taken": 1, "failed": 0}
    assert r["counts"]["food_entry"] == {"written": 0, "already_there": 1, "day_taken": 2, "failed": 0}
    assert total(FoodEntry, user.id) == 1


def test_food_entries_match_one_for_one_so_two_bananas_stay_two(user):
    bananas = meal(ago(1), ("Banana", 105, "snack"), ("Banana", 105, "snack"))
    assert imp(user, bananas)["counts"]["food_entry"]["written"] == 2
    assert imp(user, bananas)["counts"]["food_entry"] == {"written": 0, "already_there": 2, "day_taken": 0, "failed": 0}
    three = imp(user, meal(ago(1), ("Banana", 105, "snack"), ("Banana", 105, "snack"), ("Banana", 105, "snack")))
    assert three["row_outcomes"]["day_taken"] == 1 and total(FoodEntry, user.id) == 2


def test_food_a_deleted_entry_never_comes_back_beside_a_new_one(user):
    """The day's imported entries are all deleted; a newer export adds one to that day: the deleted ones
    match and stay deleted, and only the new entry is written."""
    imp(user, meal(ago(1), ("Oats", 350, "breakfast"), ("Dal rice", 600, "lunch")))
    for e in rows_of(FoodEntry, user.id):
        services.delete_entry(user.id, "food_entry", e.id)
    r = imp(user, meal(ago(1), ("Oats", 350, "breakfast"), ("Dal rice", 600, "lunch"), ("Tea", 40, "snack")))
    assert r["counts"]["food_entry"] == {"written": 1, "already_there": 2, "day_taken": 0, "failed": 0}
    assert [e.description for e in rows_of(FoodEntry, user.id, deleted_at=None)] == ["Tea"]


def test_cardio_the_same_activity_minutes_and_distance_is_already_there_another_activity_is_written(user):
    services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5, "date": ago(1)}, "web")
    r = imp(user, run(ago(1), "outdoor run", 30.04, 5.001), run(ago(1), "Treadmill Walk", 20),
            run(ago(1), "Outdoor Run", 35, 6))
    assert r["counts"]["cardio_entry"] == {"written": 1, "already_there": 1, "day_taken": 1, "failed": 0}
    with db.new_session() as s:
        names = [s.get(Exercise, c.exercise_id).name for c in rows_of(CardioEntry, user.id)]
    assert names == ["Outdoor Run", "Treadmill Walk"]


def test_cardio_without_a_distance_matches_one_without(user):
    imp(user, run(ago(1), "Rowing Machine", 20))
    assert imp(user, run(ago(1), "Rowing Machine", 20))["row_outcomes"]["already_there"] == 1
    assert imp(user, run(ago(1), "Rowing Machine", 20, 4))["row_outcomes"]["day_taken"] == 1


def test_rows_of_one_call_do_not_take_each_others_day_for_sessions_food_and_cardio(user):
    r = imp(user, lift(ago(1), ("Deadlift", 140, 5)), lift(ago(1), ("Bench Press", 70, 5)),
            meal(ago(1), ("Oats", 350, "breakfast")), meal(ago(1), ("Dal", 500, "lunch")),
            run(ago(1), "Outdoor Run", 30, 5), run(ago(1), "Outdoor Run", 20, 3))
    assert r["row_outcomes"]["written"] == 6


def test_a_day_split_across_two_calls_comes_back_day_taken(user):
    """Section 5's risk: the second call's rows for a day the first call filled are day taken, and say so."""
    imp(user, lift(ago(1), ("Back Squat", 100, 5)), meal(ago(1), ("Oats", 350, "breakfast")))
    first = rows_of(LiftSession, user.id)[0].id
    r = imp(user, lift(ago(1), ("Bench Press", 70, 5)), meal(ago(1), ("Dal", 500, "lunch")))
    assert r["row_outcomes"]["day_taken"] == 2 and r["day_taken"][0]["entry_id"] == first
    assert total(LiftSession, user.id) == 1 and total(FoodEntry, user.id) == 1


def test_another_day_of_a_newer_export_is_written_and_the_old_days_are_already_there(user):
    imp(user, *[weigh(ago(n), 80 + n / 10) for n in range(3, 8)])
    r = imp(user, *[weigh(ago(n), 80 + n / 10) for n in range(1, 8)])
    assert r["row_outcomes"] == {"written": 2, "already_there": 5, "day_taken": 0, "failed": 0}


# ---------------------------------------------------------------- 14 to 16, 18, 19: refused rows

def test_a_bad_row_fails_alone_and_the_good_rows_are_written(user):
    r = imp(user, weigh(ago(3), 80), weigh(ago(2), 1234.5), {"kind": "weight", "date": ago(1)}, "not a row",
            meal(ago(1), ("Oats", 350, "brunch")), run(ago(1), "Outdoor Run", 30, 5),
            {"kind": "bodyweight", "weight": 80})
    assert r["row_outcomes"] == {"written": 2, "already_there": 0, "day_taken": 0, "failed": 5}
    assert [(f["row"], f["error"]) for f in r["failed"]] == [
        (2, "invalid_field"), (3, "unknown_kind"), (4, "invalid_field"), (5, "invalid_field"), (7, "missing_field")]
    assert r["failed"][1]["message"] == "Row 3: 'weight' is not a kind of row."
    assert r["failed"][1]["fix"] == "Use one of: bodyweight, measurement, lift_session, food_entry, cardio_entry."
    assert r["failed"][4]["message"] == "Row 7: date is missing." and r["failed"][4]["kind"] == "bodyweight"
    assert r["counts"]["bodyweight"] == {"written": 1, "already_there": 0, "day_taken": 0, "failed": 2}
    assert r["counts"]["food_entry"]["failed"] == 1 and r["counts"]["cardio_entry"]["written"] == 1
    assert_adds_up(r)
    assert total(Bodyweight, user.id) == 1 and total(CardioEntry, user.id) == 1


def test_a_row_refusal_is_the_refusal_its_own_log_function_gives(user):
    """The same rule, the same words: only the row's number and the field's import name are added."""
    def without_set(message):
        return message.split(": ", 1)[1]
    r = imp(user, weigh(ago(1), 1234.5), lift(ago(1), ("Back Squat", 100, 31)), measure(ago(1), "steps", 8000.5),
            meal(ago(1), ("Feast", 5000.6, "dinner")), run(ago(1), "Outdoor Run", 30, 5, speed=30),
            run(ago(2), "Outdoor Run", 601))
    own = [services.log_bodyweight(user.id, {"kg": 1234.5}, "web"),
           services.log_lifts(user.id, {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 31}]}, "web"),
           services.log_measurement(user.id, {"kind": "steps", "value": 8000.5}, "web"),
           services.log_food(user.id, {"entries": [{"description": "Feast", "kcal": 5000.6, "meal": "dinner"}]}, "web"),
           services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5, "speed_kmh": 30},
                               "web"),
           services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 601}, "web")]
    got = r["failed"]
    assert [f["error"] for f in got] == [o["error"] for o in own]
    assert got[0]["message"] == "Row 1: " + own[0]["message"].replace("kg ", "weight ", 1) and got[0]["fix"] == own[0]["fix"]
    assert got[1]["message"] == "Row 2, set 1: " + without_set(own[1]["message"]) and got[1]["fix"] == own[1]["fix"]
    assert got[2]["message"] == "Row 3: " + own[2]["message"] and got[2]["fix"] == own[2]["fix"]
    assert got[3]["message"] == "Row 4, entry 1: " + without_set(own[3]["message"]) and got[3]["fix"] == own[3]["fix"]
    # Was: "Row 5: " + own[4]["message"]. The import's field is speed now (S2).
    # Then: "Row 5: " + own[4]["message"].replace("speed_kmh", "speed"). Now the speed sent carries its unit, as on
    # a mi call (fix round 2): "speed 30 km/h does not match 5 km in 30 minutes, which is 10 km/h."
    assert got[4]["message"] == "Row 5: " + own[4]["message"].replace("speed_kmh 30 ", "speed 30 km/h ")
    assert got[4]["fix"] == own[4]["fix"].replace("speed_kmh", "speed")
    assert got[5]["message"] == "Row 6: " + own[5]["message"] and got[5]["fix"] == own[5]["fix"]


def test_a_session_with_one_unknown_set_fails_whole_and_unknown_names_groups_it(user):
    r = imp(user, lift(ago(3), ("Back Squat", 100, 5), ("Squat (Barbell)", 100, 5)),
            lift(ago(2), ("squat  (barbell)", 105, 5), ("Squat (Barbell)", 100, 3)),
            lift(ago(1), ("Deadlift", 140, 5)), run(ago(1), "Zorbinator Run", 30))
    assert total(LiftSession, user.id) == 1   # Spotter never drops a set itself
    assert r["counts"]["lift_session"] == {"written": 1, "already_there": 0, "day_taken": 0, "failed": 2}
    first = r["failed"][0]
    assert first["message"] == "Row 1, set 2: 'Squat (Barbell)' is not on the exercise list."
    own = services.log_lifts(user.id, {"sets": [{"exercise": "Squat (Barbell)", "weight_kg": 100, "reps": 5}]}, "web")
    assert first["error"] == "unknown_exercise" and first["closest"] == own["closest"] and own["closest"]
    assert r["unknown_names"][0] == {"name": "Squat (Barbell)", "kind": "lift", "rows": 2,
                                     "closest": first["closest"]}
    assert r["unknown_names"][1]["name"] == "Zorbinator Run" and r["unknown_names"][1]["kind"] == "cardio"
    assert len(r["unknown_names"]) == 2


def test_times_expands_into_numbered_sets(user):
    imp(user, lift(ago(1), ("Back Squat", 100, 5, 3), ("Bench Press", 70, 8, 2), ("Back Squat", 90, 8)))
    with db.new_session() as s:
        got = [(s.get(Exercise, x.exercise_id).name, x.set_number, x.weight_kg, x.reps)
               for x in s.scalars(select(LiftSet).order_by(LiftSet.id))]
    assert got == [("Back Squat", 1, 100, 5), ("Back Squat", 2, 100, 5), ("Back Squat", 3, 100, 5),
                   ("Bench Press", 1, 70, 8), ("Bench Press", 2, 70, 8), ("Back Squat", 4, 90, 8)]


def test_times_and_one_item_a_set_are_the_same_session(user):
    imp(user, lift(ago(1), ("Back Squat", 100, 5, 3)))
    again = imp(user, lift(ago(1), ("Back Squat", 100, 5), ("Back Squat", 100, 5), ("Back Squat", 100, 5)))
    assert again["row_outcomes"]["already_there"] == 1


def test_more_than_sixty_sets_once_times_is_counted_is_refused(user):
    r = imp(user, lift(ago(1), ("Back Squat", 100, 5, 20), ("Bench Press", 70, 8, 20), ("Deadlift", 140, 5, 20),
                       ("Barbell Row", 60, 8)))
    assert r["failed"][0]["error"] == "sets_count_out_of_range"
    assert r["failed"][0]["message"] == "Row 1: The session has 61 sets once times is counted. It needs 1 to 60."
    ok = imp(user, lift(ago(1), ("Back Squat", 100, 5, 20), ("Bench Press", 70, 8, 20), ("Deadlift", 140, 5, 20)))
    assert ok["row_outcomes"]["written"] == 1


@pytest.mark.parametrize("times,code", [(0, "invalid_field"), (21, "invalid_field"), (2.5, "not_a_whole_number"),
                                        ("x", "not_a_number")])
def test_times_out_of_range_fails_the_row(user, times, code):
    r = imp(user, lift(ago(1), ("Back Squat", 100, 5, times)))
    assert r["failed"][0]["error"] == code and r["failed"][0]["message"].startswith("Row 1, set 1: ")


def test_a_set_with_a_field_it_does_not_take_fails_the_row(user):
    r = imp(user, {"kind": "lift_session", "date": ago(1), "sets": [{"exercise": "Deadlift", "weight_kg": 140, "reps": 5}]})
    assert r["failed"][0]["message"].startswith("Row 1: 2 problems. Set 1: 'weight_kg' is not a field of a set.")
    assert "Set 1: weight is missing." in r["failed"][0]["message"] and total(LiftSession, user.id) == 0


def test_a_row_with_many_problems_names_the_first_three_and_counts_the_rest(user):
    r = imp(user, lift(ago(1), *[("Nope", 900, 50)] * 3))
    message = r["failed"][0]["message"]
    assert message.startswith("Row 1: 9 problems. Set 1: 'Nope' is not on the exercise list.")
    assert message.endswith(" And 6 more.")


@pytest.mark.parametrize("kind", list(KINDS))
def test_a_field_of_another_kind_fails_the_row_with_the_kinds_own_fields(user, kind):
    row = KINDS[kind]["row"](ago(1)) | {"kg": 80}
    r = imp(user, row)
    [f] = r["failed"]
    assert f["error"] == "invalid_field" and f["message"] == f"Row 1: 'kg' is not a field of a {kind} row."
    assert f["fix"] == f"A {kind} row takes: date, " + ", ".join(imports.ROW_FIELDS[kind]) + "."


@pytest.mark.parametrize("field", ["saved_food_id", "usda_fdc_id", "estimated", "grams"])
def test_an_imported_food_entry_takes_no_ids_and_no_estimate_flag(user, field):
    r = imp(user, {"kind": "food_entry", "date": ago(1),
                   "entries": [{"description": "Oats", "kcal": 350, "meal": "breakfast", field: 1}]})
    assert r["failed"][0]["message"] == f"Row 1, entry 1: '{field}' is not a field of an imported food entry."


def test_an_imported_food_entry_needs_its_meal(user):
    r = imp(user, {"kind": "food_entry", "date": ago(1), "entries": [{"description": "Oats", "kcal": 350}]},
            meal(ago(2), ("Oats", 350, "brunch")))
    assert r["failed"][0]["message"] == "Row 1, entry 1: meal is missing."
    assert r["failed"][1]["message"] == "Row 2, entry 1: meal 'brunch' is not a meal."
    assert r["failed"][1]["fix"] == imports.MEAL_FIX and "clock" not in r["failed"][1]["fix"]


@pytest.mark.parametrize("on,code", [(1, "date_in_future"), (-731, None), (-732, "date_too_old"),
                                     ("soon", "invalid_date"), (None, "missing_field"), ("", "missing_field")])
def test_the_date_must_be_there_and_in_the_two_years_up_to_today(user, on, code):
    sent = (TODAY + timedelta(days=on)).isoformat() if isinstance(on, int) else on
    r = imp(user, weigh(sent, 80))
    if code is None:
        assert r["row_outcomes"]["written"] == 1
        return
    assert r["failed"][0]["error"] == code and r["failed"][0]["message"].startswith("Row 1: ")
    if code == "invalid_date":
        assert r["failed"][0]["fix"] == imports.DATE_FIX   # no "leave it out for today": a row needs its date


@pytest.mark.parametrize("sent", [20261109, 2026.11, True, ["2026-11-09"], {"d": 1}])
def test_a_date_that_is_not_text_fails_its_row(user, sent):
    r = imp(user, weigh(sent, 80), weigh(ago(1), 80))
    assert r["failed"][0]["error"] == "invalid_date" and r["failed"][0]["fix"] == imports.DATE_FIX
    assert r["row_outcomes"]["written"] == 1


def test_a_refused_date_is_not_in_the_range_or_the_link(user):
    r = imp(user, weigh("2999-01-01", 80), weigh("2020-01-01", 80), weigh(ago(3), 80), weigh(ago(1), 80), dry=True)
    assert (r["from"], r["to"]) == (ago(3), ago(1)) and r["links"]["day"].endswith("/day/" + ago(1))
    assert "4 rows from 7 to 9 Nov." in r["summary"]


@pytest.mark.parametrize("name", [123, 1.5, True, ["Deadlift"]])
def test_an_exercise_that_is_not_text_fails_its_row(user, name):
    r = imp(user, lift(ago(1), (name, 100, 5)), run(ago(1), name, 30), run(ago(2), "Outdoor Run", 30))
    assert [f["error"] for f in r["failed"]] == ["unknown_exercise", "unknown_exercise"]
    assert r["row_outcomes"]["written"] == 1 and {u["kind"] for u in r["unknown_names"]} == {"lift", "cardio"}


def test_each_cardio_name_gets_its_own_refusal(user):
    r = imp(user, run(ago(1), "", 30), run(ago(2), None, 30), run(ago(3), "Zorbinator", 30), run(ago(4), "zorbinator", 30))
    assert [f["message"] for f in r["failed"]] == [
        "Row 1: exercise is missing.", "Row 2: exercise is missing.", "Row 3: 'Zorbinator' is not on the exercise list.",
        "Row 4: 'zorbinator' is not on the exercise list."]
    assert r["unknown_names"][0]["rows"] == 2


def test_a_missing_date_is_never_today(user):
    imp(user, lift(None, ("Deadlift", 100, 5)), {"kind": "bodyweight", "weight": 80})
    assert total(LiftSession, user.id) == 0 and total(Bodyweight, user.id) == 0


# ---------------------------------------------------------------- 17: the date rule

def stored_date(user, sent) -> str:
    r = imp(user, weigh(sent, 80))
    assert r["row_outcomes"]["written"] == 1, r
    return rows_of(Bodyweight, user.id)[-1].date.isoformat()


def test_a_timestamp_with_an_offset_keeps_its_own_date(make_user):
    user = make_user(time_zone="America/New_York")   # where they live now; the session was in India
    assert stored_date(user, "2026-03-01T23:30:00+05:30") == "2026-03-01"
    assert stored_date(user, "2026-03-02T00:30:00-08:00") == "2026-03-02"
    assert stored_date(user, "2025-03-03 07:12:00 +0530") == "2025-03-03"   # Apple Health's own form


def test_a_utc_timestamp_moves_to_the_persons_date_either_side_of_midnight(user):
    assert stored_date(user, "2026-03-01T18:29:00Z") == "2026-03-01"        # 23:59 in Kolkata
    assert stored_date(user, "2026-03-03T18:31:00+00:00") == "2026-03-04"   # 00:01 in Kolkata


def test_a_timestamp_with_no_offset_is_refused(user):
    r = imp(user, weigh("2026-03-01T07:12", 80))
    assert r["failed"][0] == {"row": 1, "kind": "bodyweight", "date": "2026-03-01T07:12",
                              "error": "timestamp_without_offset", "message": "Row 1: '2026-03-01T07:12' has no offset.",
                              "fix": "Send the timestamp with its offset, such as +05:30, or the date alone when it is "
                                     "the person's local date."}


def test_today_is_the_persons_own_date_on_either_side_of_their_midnight(user, monkeypatch):
    """00:01 in Kolkata on 11 November is 18:31 UTC on 10 November: 11 November is today there, not after it."""
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 10, 18, 31, tzinfo=timezone.utc))
    assert imp(user, weigh("2026-11-11", 80))["row_outcomes"]["written"] == 1
    assert imp(user, weigh("2026-11-12", 80))["failed"][0]["error"] == "date_in_future"
    monkeypatch.setattr(db, "utcnow", lambda: datetime(2026, 11, 10, 18, 29, tzinfo=timezone.utc))
    assert imp(user, weigh("2026-11-11", 81))["failed"][0]["error"] == "date_in_future"   # 23:59 on the 10th there


# ---------------------------------------------------------------- 20, 21: cardio estimates

def test_weigh_ins_go_first_so_imported_cardio_gets_an_estimate_from_body_weight_now(user):
    r = imp(user, run(ago(10), "Outdoor Run", 30, 5), weigh(ago(2), 80), weigh(ago(1), 82))
    [entry] = rows_of(CardioEntry, user.id)
    kg = metrics.bodyweight_now([(TODAY - timedelta(days=2), 80.0), (TODAY - timedelta(days=1), 82.0)], TODAY)
    assert entry.estimate_weight_kg == kg == 81.0
    assert entry.estimated_kcal == metrics.cardio_kcal(9.3, kg, 30)
    assert imports.CARDIO_ESTIMATE_NOTE in r["note"]
    preview = imp(user, run(ago(11), "Outdoor Run", 30, 5), dry=True)
    assert imports.CARDIO_ESTIMATE_NOTE in preview["note"]


def test_with_no_weigh_in_imported_cardio_has_no_estimate_and_the_note_says_so(user):
    preview = imp(user, run(ago(1), "Outdoor Run", 30, 5), dry=True)
    assert preview["note"] == imports.NO_WEIGH_IN_NOTE
    r = imp(user, run(ago(1), "Outdoor Run", 30, 5, machine_kcal=320))
    [entry] = rows_of(CardioEntry, user.id)
    assert (entry.estimated_kcal, entry.estimate_weight_kg, entry.machine_kcal) == (None, None, 320)
    assert r["note"] == imports.NO_WEIGH_IN_NOTE


def test_a_preview_with_weigh_ins_in_the_same_call_says_cardio_gets_its_estimate(user):
    preview = imp(user, run(ago(1), "Outdoor Run", 30, 5), weigh(ago(1), 80), dry=True)
    assert imports.CARDIO_ESTIMATE_NOTE in preview["note"]


# ---------------------------------------------------------------- 22: imported food

def test_imported_food_is_you_said_not_estimated_and_stays_out_of_find_foods_recents(user):
    imp(user, meal(ago(2), ("Breakfast from the old app", 640, "breakfast")), meal(ago(1), ("Dal rice", 600, "lunch")))
    assert {(e.source, e.estimated) for e in rows_of(FoodEntry, user.id)} == {("you_said", False)}
    assert services.find_food(user.id, "old app")["recent"] == []
    assert services.find_food(user.id, "dal")["recent"] == []
    services.log_food(user.id, {"entries": [{"description": "Dal rice", "kcal": 600}]}, "web")
    assert [g["times"] for g in services.find_food(user.id, "dal")["recent"]] == [1]   # the hand-logged one only


# ---------------------------------------------------------------- 23, 25: refusals of the whole call

WHOLE = [
    ({"rows": [weigh(ago(1), 80)] * 201}, "rows_out_of_range", "rows holds 201. It needs 1 to 200.",
     "Send at most 200 rows a call, oldest first, and the rest in the next call."),
    ({"rows": []}, "rows_out_of_range", "rows holds 0. It needs 1 to 200.", None),
    ({"rows": "all of it"}, "rows_out_of_range", "rows holds 0. It needs 1 to 200.", None),
    ({}, "rows_out_of_range", "rows holds 0. It needs 1 to 200.", None),
    ({"rows": [lift(ago(n), *[("Deadlift", 100, 5)] * 50) for n in range(1, 21)] + [meal(ago(1), ("Tea", 40, "snack"))],
      "weight_unit": "kg"},   # the unit the sets need since the fix round on 7e95ee9
     "items_out_of_range", "These rows hold 1,001 sets and food entries. The most in one call is 1,000.",
     "Send fewer rows in this call, and the rest in the next."),
    ({"rows": [weigh(ago(1), 80)], "weight_unit": "stone"}, "invalid_unit",
     "weight_unit 'stone' is not a unit Spotter takes.",
     "Use kg or lb for weight_unit, km or mi for distance_unit, cm or in for length_unit."),
    ({"rows": [weigh(ago(1), 80)], "distance_unit": "miles"}, "invalid_unit",
     "distance_unit 'miles' is not a unit Spotter takes.", None),
    ({"rows": [weigh(ago(1), 80)], "length_unit": 2.54}, "invalid_unit", "length_unit '2.54' is not a unit Spotter takes.",
     None),
    ({"rows": [weigh(ago(1), 80)], "weight_units": "lb"}, "invalid_field", "'weight_units' is not a field of an import.",
     "Use: rows, dry_run, weight_unit, distance_unit, length_unit."),
]


@pytest.mark.parametrize("data,code,message,fix", WHOLE, ids=[w[1] + str(n) for n, w in enumerate(WHOLE)])
def test_a_whole_call_refusal_writes_nothing(user, data, code, message, fix):
    before = everything()
    r = services.import_entries(user.id, {**data, "dry_run": False}, "claude")
    assert r["error"] == code and r["message"] == message, r
    if fix:
        assert r["fix"] == fix
    assert everything() == before


# ---------------------------------------------------------------- the fix round on 7e95ee9: a unit is never assumed

NO_UNIT = {"weight_unit": None, "distance_unit": None, "length_unit": None}   # every unit left out of the call
HOLDS = {"weight_unit": "a weight", "distance_unit": "a distance or a speed", "length_unit": "a body size"}


@pytest.mark.parametrize("row, unit", [
    (weigh(ago(1), 176), "weight_unit"), (lift(ago(1), ("Deadlift", 225, 5)), "weight_unit"),
    (lift(ago(1), ("Pull-Up", 0, 8)), "weight_unit"),   # 0 for bodyweight work is still a weight
    (run(ago(1), "Outdoor Run", 30, 3.1), "distance_unit"), (run(ago(1), "Outdoor Run", 30, speed=6), "distance_unit"),
    (measure(ago(1), "Waist", 33.5), "length_unit"), (measure(ago(1), "thigh", 22), "length_unit")],
    ids=["weigh-in", "set", "bodyweight set", "distance", "speed", "waist", "thigh"])
@pytest.mark.parametrize("dry", [None, True, False], ids=["left out", "preview", "real"])
def test_a_quantity_without_its_unit_is_refused_whole_preview_or_not(user, row, unit, dry):
    before = everything()
    r = imp(user, row, meal(ago(2), ("Oats", 350, "breakfast")), dry=dry, **NO_UNIT)
    assert r == {"error": "missing_unit", "message": f"{unit} is missing, and 1 row holds {HOLDS[unit]}.",
                 # Was: "... {kg or lb}. Spotter never assumes a unit." Fix round 3: the fix says to ask, never guess.
                 "fix": f"Send {unit} as the export writes it: {' or '.join(imports.UNITS[unit])}. If the export "
                        "does not say which unit, ask the person. Spotter never assumes a unit.",
                 "missing_units": [unit]}
    assert everything() == before
    stored = next(iter(imports.UNITS[unit]))   # kg, km or cm, named: the same rows are then taken
    named = imp(user, row, meal(ago(2), ("Oats", 350, "breakfast")), dry=True, **{**NO_UNIT, unit: stored})
    assert "error" not in named and named["units"] == {unit.removesuffix("_unit"): stored}


def test_two_or_three_units_missing_are_named_in_one_refusal(user):
    rows = [weigh(ago(1), 176), lift(ago(2), ("Deadlift", 225, 5)), run(ago(1), "Outdoor Run", 30, 3.1),
            measure(ago(1), "waist", 33.5)]
    before = everything()
    two = imp(user, *rows, weight_unit=None, distance_unit=None, length_unit="in")
    assert two == {"error": "missing_unit",
                   "message": "weight_unit is missing, and 2 rows hold a weight. distance_unit is missing, and 1 row "
                              "holds a distance or a speed.",
                   "fix": "Send weight_unit and distance_unit as the export writes them: weight_unit kg or lb, "
                          "distance_unit km or mi. If the export does not say which unit, ask the person. Spotter "
                          "never assumes a unit.",
                   "missing_units": ["weight_unit", "distance_unit"]}
    three = imp(user, *rows, dry=True, **NO_UNIT)
    assert three["missing_units"] == ["weight_unit", "distance_unit", "length_unit"]
    assert three["message"].endswith(" length_unit is missing, and 1 row holds a body size.")
    assert three["fix"] == ("Send weight_unit, distance_unit and length_unit as the export writes them: weight_unit kg "
                            "or lb, distance_unit km or mi, length_unit cm or in. If the export does not say which "
                            "unit, ask the person. Spotter never assumes a unit.")
    assert everything() == before


def test_a_unit_sent_that_no_row_needs_is_taken(user):
    r = imp(user, meal(ago(1), ("Oats", 350, "breakfast")), measure(ago(1), "steps", 9000), **{**NO_UNIT,
            "weight_unit": "lb", "distance_unit": "mi"})
    assert r["row_outcomes"]["written"] == 2 and r["units"] == {"weight": "lb", "distance": "mi"}


def test_food_rows_alone_need_no_unit(user):
    r = imp(user, meal(ago(1), ("Oats", 350, "breakfast")), meal(ago(2), ("Dal", 500, "lunch")), **NO_UNIT)
    assert r["row_outcomes"]["written"] == 2 and r["units"] == {}
    assert total(FoodEntry, user.id) == 2


def test_resting_heart_rate_alone_needs_no_length_unit(user):
    """A measurement that is not a length needs no length_unit: resting_hr, and steps, sleep_hours and water_ml."""
    rows = [measure(ago(1), "resting_hr", 58), measure(ago(1), "steps", 9000), measure(ago(1), "sleep_hours", 7.5),
            measure(ago(1), "water_ml", 2000)]
    r = imp(user, rows[0], **NO_UNIT)
    assert r["row_outcomes"]["written"] == 1 and r["units"] == {}
    assert imp(user, *rows[1:], **NO_UNIT)["row_outcomes"]["written"] == 3


def test_a_mixed_call_needs_the_units_its_rows_hold_and_only_those(user):
    rows = [weigh(ago(1), 176.4), lift(ago(1), ("Deadlift", 225, 5, 3)), meal(ago(1), ("Oats", 350, "breakfast")),
            measure(ago(1), "resting_hr", 58), run(ago(2), "Outdoor Run", 30, 3.1)]
    before = everything()
    refused = imp(user, *rows, **{**NO_UNIT, "weight_unit": "lb"})   # the run's miles are not named
    assert refused["error"] == "missing_unit" and refused["missing_units"] == ["distance_unit"]
    assert refused["message"] == "distance_unit is missing, and 1 row holds a distance or a speed."
    assert everything() == before
    r = imp(user, *rows, **{**NO_UNIT, "weight_unit": "lb", "distance_unit": "mi"})   # no body size: no length_unit
    assert r["row_outcomes"] == {"written": 5, "already_there": 0, "day_taken": 0, "failed": 0}
    assert r["units"] == {"weight": "lb", "distance": "mi"}
    assert rows_of(Bodyweight, user.id)[0].kg == 80.01 and rows_of(CardioEntry, user.id)[0].distance_km == 4.99


def test_a_row_that_is_not_what_its_kind_takes_needs_no_unit_and_fails_as_before(user):
    """Which units a call needs is read from the rows as sent, before the row checks, and a malformed row needs
    none: it is refused by its own checks, as without the unit rule."""
    r = imp(user, {"kind": "bodyweight", "date": ago(1)}, measure(ago(1), "wasit", 80),
            {"kind": "weight", "date": ago(1), "weight": 80}, "not a row",
            {"kind": "lift_session", "date": ago(1), "sets": "heavy"}, meal(ago(1), ("Oats", 350, "breakfast")),
            **NO_UNIT)
    assert [f["error"] for f in r["failed"]] == ["missing_field", "invalid_kind", "unknown_kind", "invalid_field",
                                                 "sets_count_out_of_range"]
    assert r["row_outcomes"]["written"] == 1


def test_a_missing_unit_is_refused_before_too_many_items(user):
    """The ruled order: rows_out_of_range, then missing_unit, then items_out_of_range (fix round 3)."""
    rows = [lift(ago(n), *[("Deadlift", 100, 5)] * 50) for n in range(1, 21)] + [meal(ago(1), ("Tea", 40, "snack"))]
    r = imp(user, *rows, **NO_UNIT)
    assert r["error"] == "missing_unit" and r["missing_units"] == ["weight_unit"]
    assert imp(user, *rows, **{**NO_UNIT, "weight_unit": "kg"})["error"] == "items_out_of_range"
    assert imp(user, *rows * 10, **NO_UNIT)["error"] == "rows_out_of_range"


def test_known_limit_utc_rows_sent_again_after_a_time_zone_change_are_written_again(user):
    """Not built in version 1, documented (the review's M1). A timestamp in UTC takes the person's date in the zone
    set when it is imported, and every key holds the date. After a change of zone the same rows can land on another
    date, and are written there again. A timestamp with its own offset, and a plain date, do not move."""
    utc = "2026-11-07T20:00:00Z"   # 8 Nov in Kolkata (01:30), 7 Nov in New York (15:00)
    rows = [weigh(utc, 80), run(utc, "Outdoor Run", 30, 5), lift("2026-11-07T20:00:00+00:00", ("Deadlift", 100, 5))]
    kept = [weigh("2026-11-05T20:00:00+05:30", 81), measure("2026-11-04", "waist", 84)]
    first = imp(user, *rows, *kept)
    assert first["row_outcomes"]["written"] == 5 and first["from"] == "2026-11-04" and first["to"] == "2026-11-08"
    with db.session_scope() as s:
        s.get(User, user.id).time_zone = "America/New_York"
    again = imp(user, *rows, *kept)
    assert again["row_outcomes"] == {"written": 3, "already_there": 2, "day_taken": 0, "failed": 0}
    weigh_ins = sorted((b.date.isoformat(), b.kg) for b in rows_of(Bodyweight, user.id, deleted_at=None))
    assert weigh_ins == [("2026-11-05", 81.0), ("2026-11-07", 80.0), ("2026-11-08", 80.0)]   # one row, twice
    assert total(CardioEntry, user.id) == 2 and total(LiftSession, user.id) == 2 and total(Measurement, user.id) == 1


def test_an_unknown_unit_is_refused_before_a_missing_one(user):
    r = imp(user, weigh(ago(1), 80), run(ago(1), "Outdoor Run", 30, 3.1), **{**NO_UNIT, "weight_unit": "stone"})
    assert r["error"] == "invalid_unit" and r["message"] == "weight_unit 'stone' is not a unit Spotter takes."


def test_a_thousand_items_is_allowed(user):
    rows = [lift(ago(n), *[("Deadlift", 100, 5)] * 50) for n in range(1, 21)]
    assert imp(user, *rows, dry=True)["row_outcomes"]["written"] == 20


def test_no_time_zone_is_refused_whole(make_user):
    user = make_user()
    before = everything()
    r = imp(user, weigh("2026-11-09", 80))
    assert r["error"] == "time_zone_not_set" and everything() == before
    assert r["fix"].startswith("Set it with update_settings first")


def test_an_account_with_sample_data_is_refused_whole(user):
    assert "error" not in services.add_sample_data(user.id, "web")
    before = everything()
    for dry in (True, False):
        r = imp(user, weigh(ago(40), 80), dry=dry)
        assert r["error"] == "sample_data_present" and everything() == before
    assert "Remove sample data button on Settings" in r["fix"]


def test_by_is_web_or_claude_and_the_mark_is_import_either_way(user):
    assert imp(user, weigh(ago(1), 80), by="import")["error"] == "invalid_by"
    assert services.log_bodyweight(user.id, {"kg": 80}, "import")["error"] == "invalid_by"   # never a caller
    assert imp(user, weigh(ago(1), 80), by="web")["row_outcomes"]["written"] == 1
    assert rows_of(Bodyweight, user.id)[0].logged_by == IMPORTED


def test_no_account(user):
    assert services.import_entries(10 ** 9, {"rows": [weigh(ago(1), 80)]}, "claude")["error"] == "no_account"


def test_data_must_be_an_object(user):
    assert services.import_entries(user.id, ["rows"], "claude")["error"] == "invalid_field"


# ---------------------------------------------------------------- 24: the 24-hour cap

def imported_rows(user_id, n, hours_ago=1.0, deleted=False):
    """n imported food entries, created hours_ago before NOW (food has no one-a-day index)."""
    at = NOW - timedelta(hours=hours_ago)
    with db.session_scope() as s:
        s.execute(insert(FoodEntry), [
            {"user_id": user_id, "date": TODAY - timedelta(days=30), "meal": "snack", "description": "x", "kcal": 1,
             "source": "you_said", "estimated": False, "logged_by": IMPORTED, "dedupe_key": "k", "created_at": at,
             "updated_at": at, "deleted_at": at if deleted else None, "is_sample": False} for _ in range(n)])


def test_the_cap_is_ten_thousand_rows_in_24_hours():
    assert imports.IMPORT_LIMIT == 10_000 and imports.IMPORT_HOURS == 24


def test_at_ten_thousand_the_next_real_call_is_refused_and_a_dry_run_says_it_would_be(user):
    imported_rows(user.id, 9_000, hours_ago=20.5)
    imported_rows(user.id, 1_000, hours_ago=2, deleted=True)   # deleted ones count: they were imported
    before = everything()
    r = imp(user, weigh(ago(1), 80))
    assert r == {"error": "too_many_imported",
                 "message": "10,000 rows were imported in the last 24 hours. This call would take it past 10,000. "
                            "Nothing was saved.",
                 "fix": "Wait 4 hours, then send it again.", "wait_hours": 4}   # the 20.5-hour-old rows leave in 3.5
    assert everything() == before
    preview = imp(user, weigh(ago(1), 80), dry=True)
    assert preview["within_import_limit"] is False and preview["imported_last_24h"] == 10_000
    assert preview["import_limit"] == 10_000
    assert ("Sent to be written, this call would be refused: 10,000 rows were imported in the last 24 hours, "
            "and the most is 10,000.") in preview["note"]
    assert everything() == before


def test_a_call_that_writes_nothing_new_passes_at_the_cap(user):
    imp(user, weigh(ago(1), 80))
    imported_rows(user.id, 9_999)
    r = imp(user, weigh(ago(1), 80))
    assert "error" not in r and r["row_outcomes"]["already_there"] == 1


def test_the_cap_counts_what_this_call_would_add(user):
    imported_rows(user.id, 9_998)
    assert imp(user, weigh(ago(2), 80), weigh(ago(1), 80))["row_outcomes"]["written"] == 2
    r = imp(user, weigh(ago(4), 80), weigh(ago(3), 80))
    assert r["error"] == "too_many_imported" and r["wait_hours"] == 23


def test_the_wait_is_until_enough_rows_leave_for_this_call(user):
    """One row 20 hours old and 9,999 two hours old: a call of two needs two to leave, so the wait is for the
    second oldest, 22 hours, not the oldest's 4."""
    imported_rows(user.id, 1, hours_ago=20)
    imported_rows(user.id, 9_999, hours_ago=2)
    r = imp(user, weigh(ago(2), 80), weigh(ago(1), 80))
    assert r["error"] == "too_many_imported" and r["wait_hours"] == 22
    assert imp(user, weigh(ago(1), 80))["wait_hours"] == 4


def test_rows_older_than_24_hours_and_hand_logged_rows_do_not_count(user):
    imported_rows(user.id, 10_000, hours_ago=24.5)
    services.log_bodyweight(user.id, {"kg": 79, "date": ago(9)}, "web")
    r = imp(user, weigh(ago(1), 80))
    assert r["row_outcomes"]["written"] == 1 and r["imported_last_24h"] == 0


# ---------------------------------------------------------------- 26: the write limit

def call_rows(user_id, n, function="log_lifts", in_progress=False):
    with db.session_scope() as s:
        s.add_all(CallLog(user_id=user_id, function=function, started_at=NOW - timedelta(minutes=3),
                          ms=0 if in_progress else 1, ok=not in_progress,
                          error_code="in_progress" if in_progress else None, result_bytes=0) for _ in range(n))


@pytest.mark.parametrize("dry", [True, False])
def test_at_the_write_limit_the_call_is_refused_and_nothing_is_written(user, dry):
    call_rows(user.id, WRITE_LIMIT)
    call_rows(user.id, 1, function="import_entries", in_progress=True)   # this call's own row, as MCP writes it
    before = everything()
    r = imp(user, *[weigh(ago(n), 80) for n in range(1, 201)], dry=dry)
    assert r["error"] == "too_many_writes" and everything() == before


def test_one_call_is_one_write_however_many_rows_it_carries(user):
    call_rows(user.id, WRITE_LIMIT - 1)
    call_rows(user.id, 1, function="import_entries", in_progress=True)
    r = imp(user, *[weigh(ago(n), 80) for n in range(1, 201)])
    assert r["row_outcomes"]["written"] == 200
    with db.new_session() as s:   # the service adds no call-log row of its own: the wrapper writes the one
        assert s.scalar(select(func.count()).select_from(CallLog).where(CallLog.user_id == user.id)) == WRITE_LIMIT
    call_rows(user.id, 1, function="log_bodyweight", in_progress=True)   # the next write's own row: one too many
    assert services.log_bodyweight(user.id, {"kg": 80}, "web")["error"] == "too_many_writes"


# ---------------------------------------------------------------- 27, 28, trends: what reads see after

def history_rows():
    return [lift(ago(30), ("Back Squat", 100, 5, 3)), lift(ago(20), ("Back Squat", 110, 5, 3)),
            lift(ago(10), ("Back Squat", 105, 5, 3), ("Bench Press", 80, 5))]


def without_ids(value):
    if isinstance(value, dict):
        return {k: without_ids(v) for k, v in value.items() if k not in ("session_id", "id", "links")}
    if isinstance(value, list):
        return [without_ids(v) for v in value]
    return value


def test_bests_after_an_earlier_dated_import_equal_those_of_the_same_history_logged_by_hand(user, make_user):
    by_hand = make_user(time_zone=ZONE)
    services.log_lifts(user.id, {"sets": [{"exercise": "Back Squat", "weight_kg": 107.5, "reps": 5}]}, "web")
    services.log_lifts(by_hand.id, {"sets": [{"exercise": "Back Squat", "weight_kg": 107.5, "reps": 5}]}, "web")
    imp(user, *history_rows())
    for row in history_rows():
        services.log_lifts(by_hand.id, {"date": row["date"], "sets": [
            {"exercise": x["exercise"], "weight_kg": x["weight"], "reps": x["reps"]}
            for x in row["sets"] for _ in range(x.get("times", 1))]}, "web")
    for name in ("Back Squat", "Bench Press"):
        mine, theirs = services.get_progress(user.id, name)["lift"], services.get_progress(by_hand.id, name)["lift"]
        assert without_ids(mine) == without_ids(theirs), name
    bests = services.get_progress(user.id, "Back Squat")["lift"]["bests"]
    assert {b["date"] for b in bests} == {ago(20)}   # the older, heavier import: today's 107.5 kg is no new best


def test_an_open_goals_start_value_and_date_do_not_change(user):
    services.log_bodyweight(user.id, {"kg": 84}, "web")
    goal = services.set_goal(user.id, {"kind": "bodyweight", "target_value": 78, "target_date": "2027-03-31"}, "web")
    imp(user, *[weigh(ago(n), 90 - n / 10) for n in range(1, 60)])
    with db.new_session() as s:
        g = s.get(Goal, goal["id"])
        assert (g.start_value, g.start_date) == (84, TODAY)


def test_trends_read_imported_weigh_ins_as_they_read_logged_ones(user, make_user):
    by_hand = make_user(time_zone=ZONE)
    rows = [weigh(ago(n), 82 - n / 20) for n in range(0, 40, 2)]
    imp(user, *rows)
    for row in rows:
        services.log_bodyweight(by_hand.id, {"kg": row["weight"], "date": row["date"]}, "web")
    mine, theirs = services.get_trends(user.id), services.get_trends(by_hand.id)
    assert mine["bodyweight"] == theirs["bodyweight"] and mine["summary"] == theirs["summary"]


# ---------------------------------------------------------------- 29: the result's size, and its shape

def test_with_200_failing_rows_the_result_lists_50_counts_the_rest_and_stays_small(user):
    r = imp(user, *[lift(ago(n % 700 + 1), ("Squat (Barbell)", 100, 5), ("Bench (Barbell)", 60, 5))
                    for n in range(200)])
    assert len(r["failed"]) == 50 and r["more_failed"] == 150 and r["row_outcomes"]["failed"] == 200
    assert "The first 50 refused rows are listed, and 150 more were refused." in r["note"]
    assert [u["rows"] for u in r["unknown_names"]] == [200, 200]
    assert len(json.dumps(r)) < 50_000


def test_more_than_50_held_days_are_counted_beyond_the_list(user):
    for n in range(1, 61):
        services.log_bodyweight(user.id, {"kg": 79, "date": ago(n)}, "web")
    r = imp(user, *[weigh(ago(n), 80) for n in range(1, 61)], dry=True)
    assert len(r["day_taken"]) == 50 and r["more_day_taken"] == 10
    assert "The first 50 rows left out for a held day are listed, and 10 more were left out." in r["note"]


def test_the_result_shape_and_its_summaries(user):
    services.log_bodyweight(user.id, {"kg": 79, "date": ago(5)}, "web")
    rows = [weigh(ago(400), 80.2), weigh(ago(5), 80), weigh(ago(1), 81.4), lift(ago(1), ("Deadlift", 140, 5, 3)),
            lift(ago(2), ("Zercher Squat", 80, 5))]
    preview = imp(user, *rows, dry=True)
    assert set(preview) == {"kind", "dry_run", "rows", "from", "to", "units", "counts", "row_outcomes",
                            "repeated_rows", "ranges",
                            "day_taken", "failed", "unknown_names", "more_failed", "more_day_taken",
                            "imported_last_24h", "import_limit", "within_import_limit", "summary", "note", "links"}
    assert (preview["kind"], preview["from"], preview["to"]) == ("import", ago(400), ago(1))
    assert preview["summary"] == ("Preview, nothing written. 5 rows from 6 Oct 2025 to 9 Nov 2026. Would write 2 "
                                  "weigh-ins and 1 lift session. 1 row falls on days Spotter already holds. 1 row was "
                                  "refused. Weigh-ins run from 80 kg to 81.4 kg. The heaviest set is 140 kg.")
    assert preview["note"] == SESSION_NOTE
    assert preview["links"]["day"].endswith("/day/" + ago(1)) and preview["links"]["progress"].endswith("/progress")
    real = imp(user, *rows)
    assert real["summary"] == ("Imported 3 of 5 rows from 6 Oct 2025 to 9 Nov 2026: 2 weigh-ins and 1 lift session. "
                               "1 was left out: its day already holds an entry. 1 was refused. Nothing was replaced.")
    again = imp(user, *rows[:2] + rows[2:4])
    assert again["summary"] == ("Imported 0 of 4 rows from 6 Oct 2025 to 9 Nov 2026. 3 were already there. 1 was left "
                                "out: its day already holds an entry. Nothing was replaced.")
    same = imp(user, rows[2], rows[3])
    assert same["summary"] == "Nothing new: all 2 rows on Mon 9 Nov are already there."
    one = imp(user, rows[2])
    assert one["summary"] == "Nothing new: the row on Mon 9 Nov is already there."


def test_the_service_needs_no_mcp_object_and_takes_plain_text_numbers(user):
    """A later upload page sends form text: numbers as strings reach the same checks."""
    r = services.import_entries(user.id, {"rows": [
        {"kind": "bodyweight", "date": ago(1), "weight": "176.5"},
        {"kind": "lift_session", "date": ago(1), "sets": [{"exercise": "Deadlift", "weight": "225", "reps": "5",
                                                           "times": "3"}]}],
        "dry_run": False, "weight_unit": "lb"}, "web")   # was "dry_run": "false", refused now
    assert r["row_outcomes"]["written"] == 2 and rows_of(Bodyweight, user.id)[0].kg == 80.06


def test_users_with_no_time_zone_are_refused_before_any_row_is_read(make_user):
    user = make_user()
    with db.session_scope() as s:
        s.get(User, user.id).time_zone = None
    assert imp(user, "not a row")["error"] == "time_zone_not_set"


# ---------------------------------------------------------------- the fix round

def test_more_than_60_set_items_fail_the_row_and_nothing_is_dropped(user):
    """M1: a 61st item was cut off before it was counted."""
    items = [("Bench Press", 60, 5)] * 60 + [("Deadlift", 200, 1)]
    r = imp(user, lift(ago(1), *items))
    assert r["failed"][0]["error"] == "sets_count_out_of_range" and total(LiftSession, user.id) == 0
    assert r["failed"][0]["message"] == "Row 1: The session has 61 set items. It needs 1 to 60."
    sixty = imp(user, lift(ago(1), *items[:60]))
    assert sixty["row_outcomes"]["written"] == 1


def test_every_one_of_60_items_is_checked(user):
    r = imp(user, lift(ago(1), *[("Bench Press", 60, 5)] * 59, ("Zorbinator", 9999, 99)))
    assert r["row_outcomes"]["failed"] == 1 and r["unknown_names"][0]["name"] == "Zorbinator"
    assert r["failed"][0]["message"].startswith("Row 1: 3 problems. Set 60: ")


def test_a_food_row_of_31_entries_fails_and_30_go_in(user):
    entries = [(f"Food {n}", 100, "snack") for n in range(31)]
    r = imp(user, meal(ago(1), *entries))
    assert r["failed"][0]["message"] == "Row 1: entries holds 31. It needs 1 to 30." and total(FoodEntry, user.id) == 0
    assert r["counts"]["food_entry"]["failed"] == 31
    assert imp(user, meal(ago(1), *entries[:30]))["counts"]["food_entry"]["written"] == 30


@pytest.mark.parametrize("zone,sent,code", [
    ("America/New_York", "0001-01-01T00:00:00Z", "date_too_old"),
    ("Asia/Kolkata", "9999-12-31T23:59:59Z", "date_in_future"),
    ("Pacific/Kiritimati", "9999-12-31T23:59:59+00:00", "date_in_future"),
    ("Pacific/Kiritimati", "0001-01-01T00:00:00Z", "date_too_old"),
    ("Asia/Kolkata", "0001-01-01T00:00:00-05:00", "date_too_old"),
    ("Asia/Kolkata", "9999-12-31T23:59:59+14:00", "date_in_future")])
def test_a_timestamp_near_year_1_or_9999_fails_its_row_and_the_call_goes_on(make_user, zone, sent, code):
    """M2: the conversion overflowed and ended the call."""
    user = make_user(time_zone=zone)
    r = imp(user, weigh(sent, 80), weigh(ago(1), 80))
    assert r["failed"][0]["error"] == code and r["row_outcomes"]["written"] == 1


def test_a_utc_timestamp_in_a_daylight_saving_zone_lands_on_the_local_date(make_user):
    user = make_user(time_zone="America/New_York")
    assert stored_date(user, "2026-03-08T04:30:00Z") == "2026-03-07"   # 23:30 EST, before the change
    assert stored_date(user, "2026-03-08T07:30:00Z") == "2026-03-08"   # 03:30 EDT, after it
    assert stored_date(user, "2026-11-01T03:30:00Z") == "2026-10-31"   # 23:30 EDT
    assert stored_date(user, "2026-11-01T06:30:00Z") == "2026-11-01"   # 01:30 EST


def test_refusals_quote_the_number_as_sent_in_the_calls_unit(user):
    """S1: the messages quoted the converted number with the stored range."""
    r = imp(user, weigh(ago(1), 1000), lift(ago(1), ("Back Squat", 1200, 5)), run(ago(1), "Outdoor Run", 600, 400),
            measure(ago(1), "waist", 200), run(ago(2), "Outdoor Run", 30, speed=40),
            weight_unit="lb", distance_unit="mi", length_unit="in")
    got = [(f["message"], f["fix"]) for f in r["failed"]]
    # Was, the bounds rounded to the nearest step, so an edge value read as inside the range it was refused by:
    #     ("Row 1: weight 1000 lb is outside 44.09 to 881.85 lb.", "Give weight from 44.09 to 881.85 lb."),
    #     ("Row 4: waist 200 in is outside 11.8 to 118.1 in.", "Give waist from 11.8 to 118.1 in."),
    #     ("Row 5: speed 40 mi an hour is outside 0.1 to 37.3 mi an hour.", "Give speed from 0.1 to 37.3 mi an hour.")
    # Now the lower bound is rounded up and the upper one down (fix round 2). Rows 2 and 3 read the same.
    assert got == [
        ("Row 1: weight 1000 lb is outside 44.1 to 881.84 lb.", "Give weight from 44.1 to 881.84 lb."),
        ("Row 2, set 1: weight 1200 lb is outside 0 to 1,102.31 lb.",
         "Give weight on set 1 from 0 to 1,102.31 lb, 0 for bodyweight."),
        ("Row 3: distance 400 mi is outside 0.01 to 186.41 mi.", "Give distance from 0.01 to 186.41 mi."),
        ("Row 4: waist 200 in is outside 11.9 to 118.1 in.", "Give waist from 11.9 to 118.1 in."),
        ("Row 5: speed 40 mi an hour is outside 0.1 to 37.2 mi an hour.",
         "Give speed from 0.1 to 37.2 mi an hour.")]


def test_refusals_in_kg_km_and_cm_keep_the_log_functions_words(user):
    r = imp(user, weigh(ago(1), 1000), measure(ago(1), "waist", 400))
    assert r["failed"][0]["message"] == "Row 1: weight 1000 is outside 20 to 400."
    assert r["failed"][1]["message"].startswith("Row 2: waist 400 cm is outside 30 to 300")


def test_a_speed_on_a_mile_call_is_converted(user):
    """S2: speed_kmh was stored as sent on a mi call: 6 mph read as 6 km/h."""
    imp(user, run(ago(1), "Treadmill Run", 30, speed=6), distance_unit="mi")
    assert rows_of(CardioEntry, user.id)[0].speed_kmh == 9.66   # 6 x 1.609344 = 9.656...


def test_speed_kmh_is_not_a_field_of_an_imported_row(user):
    r = imp(user, run(ago(1), "Treadmill Run", 30, speed_kmh=6))
    assert r["failed"][0]["message"] == "Row 1: 'speed_kmh' is not a field of a cardio_entry row."
    assert "speed" in r["failed"][0]["fix"].split(": ")[1].split(", ")


def test_a_speed_that_disagrees_on_a_mile_call_is_said_in_miles(user):
    r = imp(user, run(ago(1), "Outdoor Run", 30, 3, speed=12), distance_unit="mi")
    assert r["failed"][0]["message"] == ("Row 1: speed 12 mi an hour does not match 3 mi in 30 minutes, which is 6 mi "
                                         "an hour.")


def test_rows_that_repeat_an_earlier_row_of_the_call_are_counted_and_said(user):
    """S3: they still go in, and the preview and the real run say how many."""
    rows = [lift(ago(1), ("Deadlift", 140, 5)), lift(ago(1), ("Deadlift", 140, 5)), run(ago(1), "Outdoor Run", 30, 5),
            run(ago(1), "Outdoor Run", 30, 5), weigh(ago(1), 80), weigh(ago(1), 80)]
    preview = imp(user, *rows, dry=True)
    assert preview["repeated_rows"] == 3 and "3 rows repeat an earlier row of this call." in preview["summary"]
    real = imp(user, *rows)
    assert real["repeated_rows"] == 3 and "3 rows repeated an earlier row of this call." in real["summary"]
    assert total(LiftSession, user.id) == 2 and total(CardioEntry, user.id) == 2 and total(Bodyweight, user.id) == 1
    again = imp(user, *rows)
    assert again["summary"].endswith(" are already there. 3 rows repeated an earlier row of this call.")
    assert imp(user, rows[0], dry=True)["repeated_rows"] == 0


def test_a_partly_matched_food_row_says_how_many_entries_were_there(user):
    """S4: it read as all new."""
    imp(user, meal(ago(1), ("Oats", 350, "breakfast")))
    for e in rows_of(FoodEntry, user.id):
        services.delete_entry(user.id, "food_entry", e.id)
    rows = [meal(ago(1), ("Oats", 350, "breakfast"), ("Dal", 500, "lunch"), ("Tea", 40, "snack"))]
    preview = imp(user, *rows, dry=True)
    assert "Would write 2 food entries. 1 food entry in those rows is already there." in preview["summary"]
    real = imp(user, *rows)
    assert real["summary"] == ("Imported 1 of 1 row on Mon 9 Nov: 2 food entries. 1 food entry in those rows was "
                               "already there. Nothing was replaced.")


def test_session_notes_are_stored(user):
    imp(user, lift(ago(1), ("Deadlift", 140, 5), notes="Felt heavy, from the old app"))
    assert rows_of(LiftSession, user.id)[0].notes == "Felt heavy, from the old app"


def test_the_daily_cap_counts_food_entries_not_rows(user):
    imported_rows(user.id, 9_998)
    r = imp(user, meal(ago(1), ("Oats", 350, "breakfast"), ("Dal", 500, "lunch"), ("Tea", 40, "snack")))
    assert r["error"] == "too_many_imported"   # 3 entries in 1 row: 10,001
    assert "error" not in imp(user, meal(ago(1), ("Oats", 350, "breakfast"), ("Dal", 500, "lunch")))


def test_the_food_key_covers_the_entries_written_not_the_whole_row(user):
    imp(user, meal(ago(1), ("Oats", 350, "breakfast")))
    imp(user, meal(ago(2), ("Oats", 350, "breakfast")))
    for e in rows_of(FoodEntry, user.id, date=TODAY - timedelta(days=1)):
        services.delete_entry(user.id, "food_entry", e.id)
    imp(user, meal(ago(1), ("Oats", 350, "breakfast"), ("Tea", 40, "snack")))
    [tea] = rows_of(FoodEntry, user.id, description="Tea")
    assert tea.dedupe_key == food.entry_key(tea.date, [food.entry_tuple(tea)])


def test_two_identical_weigh_ins_on_a_held_day_are_both_already_there(user):
    services.log_bodyweight(user.id, {"kg": 80, "date": ago(1)}, "web")
    r = imp(user, weigh(ago(1), 80), weigh(ago(1), 80))
    assert r["row_outcomes"] == {"written": 0, "already_there": 2, "day_taken": 0, "failed": 0}
    m = services.log_measurement(user.id, {"kind": "waist", "value": 84, "date": ago(1)}, "web")
    assert m["id"] and imp(user, measure(ago(1), "waist", 84), measure(ago(1), "waist", 84))["row_outcomes"][
        "already_there"] == 2


def test_a_failed_food_row_counts_its_entries(user):
    r = imp(user, meal(ago(1), ("Oats", 350, "breakfast"), ("Dal", "lots", "lunch"), ("Tea", 40, "snack")))
    assert r["counts"]["food_entry"]["failed"] == 3 and r["row_outcomes"]["failed"] == 1


# ---------------------------------------------------------------- fix round 2

# The same content on every day: the shape where a value match on one day could take another day's row.
SAME = {"cardio_entry": lambda d: run(d, "Treadmill Walk", 30), "lift_session": lambda d: lift(d, ("Back Squat", 100, 5, 3)),
        "food_entry": lambda d: meal(d, ("Oats", 350, "breakfast"))}


def move(user, kind, frm: str, to: str):
    """Every live row of this kind on frm, moved to to, as the person does it on the edit page."""
    for row in rows_of(MODELS[kind], user.id, date=datetime.fromisoformat(frm).date(), deleted_at=None):
        assert services.edit_entry(user.id, kind, row.id, {"date": to}, "web")["changed"] == ["date"]


@pytest.mark.parametrize("kind", list(SAME))
def test_two_identical_rows_each_moved_back_a_day_are_never_written_again(user, kind):
    """F1: 7 Nov's row moved to 6 Nov, then 8 Nov's to 7 Nov. 7 Nov's row took 8 Nov's moved row by its value,
    so 8 Nov's row found nothing and was written again."""
    rows = [SAME[kind](ago(3)), SAME[kind](ago(2))]
    assert imp(user, *rows)["row_outcomes"]["written"] == 2
    move(user, kind, ago(3), ago(4))
    move(user, kind, ago(2), ago(3))
    before = everything()
    for dry in (True, False):
        r = imp(user, *rows, dry=dry)
        assert r["row_outcomes"] == {"written": 0, "already_there": 2, "day_taken": 0, "failed": 0}, (kind, dry)
    assert everything() == before


@pytest.mark.parametrize("kind", list(SAME))
def test_two_identical_rows_each_moved_forward_a_day_are_never_written_again(user, kind):
    rows = [SAME[kind](ago(3)), SAME[kind](ago(2))]
    imp(user, *rows)
    move(user, kind, ago(2), ago(1))
    move(user, kind, ago(3), ago(2))
    before = everything()
    assert imp(user, *rows)["row_outcomes"]["already_there"] == 2 and everything() == before


def test_a_week_of_walks_each_moved_back_a_day_is_never_written_again(user):
    """The reviewer's shape: an export that dates evening walks a day late, each moved back by the person."""
    rows = [run(ago(n), "Treadmill Walk", 30) for n in range(8, 1, -1)]
    imp(user, *rows)
    for n in range(8, 1, -1):   # the oldest first, so no walk lands on a day another still holds
        move(user, "cardio_entry", ago(n), ago(n + 1))
    before = everything()
    r = imp(user, *rows)
    assert r["row_outcomes"] == {"written": 0, "already_there": 7, "day_taken": 0, "failed": 0}
    assert everything() == before


def test_two_food_days_that_share_a_breakfast_each_moved_back_are_never_written_again(user):
    """The reviewer's shape: Rice was written again on 8 Nov, 500 kcal added to a day."""
    rows = [meal(ago(3), ("Oats", 350, "breakfast"), ("Dal", 600, "lunch")),
            meal(ago(2), ("Oats", 350, "breakfast"), ("Rice", 500, "lunch"))]
    imp(user, *rows)
    move(user, "food_entry", ago(3), ago(4))
    move(user, "food_entry", ago(2), ago(3))
    before = everything()
    r = imp(user, *rows)
    assert r["counts"]["food_entry"] == {"written": 0, "already_there": 4, "day_taken": 0, "failed": 0}
    assert everything() == before


def test_a_key_match_takes_only_the_rows_own_import(user):
    """The walk imported for 8 Nov, moved to 7 Nov and deleted, is 8 Nov's row's own: 8 Nov's row takes it by
    its key, it stays deleted, and a 7 Nov walk new to this export is written. By value first, 7 Nov's walk
    took it and 8 Nov's walk came back."""
    imp(user, SAME["cardio_entry"](ago(2)))
    move(user, "cardio_entry", ago(2), ago(3))
    [moved] = rows_of(CardioEntry, user.id)
    services.delete_entry(user.id, "cardio_entry", moved.id)
    r = imp(user, SAME["cardio_entry"](ago(3)), SAME["cardio_entry"](ago(2)))
    assert r["row_outcomes"] == {"written": 1, "already_there": 1, "day_taken": 0, "failed": 0}
    assert [str(c.date) for c in rows_of(CardioEntry, user.id, deleted_at=None)] == [ago(3)]
    assert rows_of(CardioEntry, user.id, id=moved.id)[0].deleted_at is not None


@pytest.mark.parametrize("kind", list(SAME))
def test_three_identical_rows_after_two_were_imported_and_moved_write_one(user, kind):
    """Each key holder matches one row only: two stored, three sent, the third is written."""
    imp(user, SAME[kind](ago(2)), SAME[kind](ago(2)))
    move(user, kind, ago(2), ago(3))
    r = imp(user, *[SAME[kind](ago(2))] * 3)
    assert r["row_outcomes"] == {"written": 1, "already_there": 2, "day_taken": 0, "failed": 0}
    assert total(MODELS[kind], user.id) == 3


def test_a_session_held_on_its_date_and_by_its_key_is_held_once(user):
    """The stored session is found by both queries: held twice, it matched both rows of the re-run."""
    imp(user, SAME["lift_session"](ago(1)))
    r = imp(user, SAME["lift_session"](ago(1)), SAME["lift_session"](ago(1)))
    assert r["counts"]["lift_session"] == {"written": 0, "already_there": 1, "day_taken": 1, "failed": 0}


def test_two_different_sessions_on_one_day_repeat_nothing(user):
    before = everything()
    r = imp(user, lift(ago(1), ("Deadlift", 140, 5)), lift(ago(1), ("Bench Press", 70, 5)), dry=True)
    assert r["repeated_rows"] == 0 and "repeat" not in r["summary"]
    assert everything() == before


def test_a_moved_session_with_notes_is_never_written_again(user):
    """The key covers the notes: a key worked out without them matches no stored session."""
    row = lift(ago(3), ("Deadlift", 140, 5), notes="From the old app")
    imp(user, row)
    move(user, "lift_session", ago(3), ago(2))
    before = everything()
    assert imp(user, row)["row_outcomes"]["already_there"] == 1 and everything() == before


@pytest.mark.parametrize("kind", ["bodyweight", "measurement"])
def test_two_identical_one_a_day_rows_after_a_move_are_both_already_there(user, kind):
    """A weigh-in's or measurement's key holder is not used up: a second row that day matches it too."""
    row = weigh(ago(2), 80) if kind == "bodyweight" else measure(ago(2), "waist", 84)
    imp(user, row)
    move(user, kind, ago(2), ago(3))
    before = everything()
    r = imp(user, row, row)
    assert r["row_outcomes"] == {"written": 0, "already_there": 2, "day_taken": 0, "failed": 0}
    assert everything() == before


@pytest.mark.parametrize("row,units,message", [
    (weigh(ago(1), 44.09), {"weight_unit": "lb"}, "Row 1: weight 44.09 lb is outside 44.1 to 881.84 lb."),
    (weigh(ago(1), 881.85), {"weight_unit": "lb"}, "Row 1: weight 881.85 lb is outside 44.1 to 881.84 lb."),
    (measure(ago(1), "waist", 11.8), {"length_unit": "in"}, "Row 1: waist 11.8 in is outside 11.9 to 118.1 in."),
    (measure(ago(1), "waist", 11.81), {"length_unit": "in"}, "Row 1: waist 11.81 in is outside 11.9 to 118.1 in."),
    (run(ago(1), "Outdoor Run", 30, speed=37.29), {"distance_unit": "mi"},
     "Row 1: speed 37.29 mi an hour is outside 0.1 to 37.2 mi an hour."),
    (run(ago(1), "Outdoor Run", 30, speed=37.3), {"distance_unit": "mi"},
     "Row 1: speed 37.3 mi an hour is outside 0.1 to 37.2 mi an hour.")])
def test_a_refused_edge_value_is_outside_the_range_its_message_quotes(user, row, units, message):
    """S1: the bounds were rounded to the nearest step: "weight 44.09 lb is outside 44.09 to 881.85 lb". Each
    bound the message quotes is itself taken."""
    before = everything()
    r = imp(user, row, dry=True, **units)
    assert r["failed"][0]["message"] == message
    field = {"bodyweight": "weight", "measurement": "value", "cardio_entry": "speed"}[row["kind"]]
    for bound in re.search(r"outside ([\d.,]+) to ([\d.,]+) ", message).groups():
        sent = {**row, field: float(bound.replace(",", ""))}
        assert imp(user, sent, dry=True, **units)["row_outcomes"]["written"] == 1, bound
    assert everything() == before


@pytest.mark.parametrize("row,units,message,fix", [
    (weigh(ago(1), "heavy"), {"weight_unit": "lb"}, "Row 1: weight 'heavy' is not a number.",
     "Give weight from 44.1 to 881.84 lb."),
    (measure(ago(1), "waist", "big"), {"length_unit": "in"}, "Row 1: value 'big' is not a number.",
     "Give waist from 11.9 to 118.1 in."),
    (measure(ago(1), "waist", None), {"length_unit": "in"}, "Row 1: value is missing.", "Give waist from 11.9 to 118.1 in."),
    (run(ago(1), "Outdoor Run", 30, "far"), {"distance_unit": "mi"}, "Row 1: distance 'far' is not a number.",
     "Give distance from 0.01 to 186.41 mi."),
    (run(ago(1), "Outdoor Run", 30, speed="fast"), {"distance_unit": "mi"}, "Row 1: speed 'fast' is not a number.",
     "Give speed from 0.1 to 37.2 mi an hour.")])
def test_a_fix_on_a_pound_mile_or_inch_call_gives_the_range_in_the_calls_unit(user, row, units, message, fix):
    """S1: the fix gave the stored unit's range ("Give the body weight in kg, 20 to 400.") on a lb call."""
    r = imp(user, row, dry=True, **units)
    assert (r["failed"][0]["message"], r["failed"][0]["fix"]) == (message, fix)


def test_a_fix_on_a_kg_km_or_cm_call_keeps_the_log_functions_words(user):
    r = imp(user, weigh(ago(1), "heavy"), measure(ago(1), "waist", "big"), run(ago(1), "Outdoor Run", 30, "far"),
            run(ago(2), "Outdoor Run", 30, speed="fast"), dry=True)
    own = [services.log_bodyweight(user.id, {"kg": "heavy"}, "web"),
           services.log_measurement(user.id, {"kind": "waist", "value": "big"}, "web"),
           services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": "far"}, "web"),
           services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 30, "speed_kmh": "fast"}, "web")]
    assert [f["fix"] for f in r["failed"]] == [o["fix"].replace("distance_km", "distance").replace("speed_kmh", "speed")
                                               for o in own]
    assert [f["fix"] for f in r["failed"]] == ["Give the body weight in kg, 20 to 400.", "Give waist in cm, 30 to 300.",
                                               "Give distance as a number from 0.01 to 300.",
                                               "Give speed as a number from 0.1 to 60."]


def test_a_speed_that_disagrees_on_a_km_call_carries_its_unit(user):
    """S2: "speed 30 does not match 5 km in 30 minutes" lost its unit with the rename."""
    r = imp(user, run(ago(1), "Outdoor Run", 30, 5, speed=30), dry=True)
    assert r["failed"][0]["message"] == "Row 1: speed 30 km/h does not match 5 km in 30 minutes, which is 10 km/h."


def test_a_speed_out_of_range_on_a_km_call_carries_its_unit(user):
    """Import pass B: "Row 1: speed 70 is outside 0.1 to 60." lost its unit with the rename to speed. The fix keeps
    log_cardio's own words, as test_a_fix_on_a_kg_km_or_cm_call_keeps_the_log_functions_words holds."""
    before = everything()
    r = imp(user, run(ago(1), "Outdoor Run", 30, speed=70), run(ago(2), "Outdoor Run", 30, speed=0.05), dry=True)
    assert everything() == before
    assert [f["message"] for f in r["failed"]] == ["Row 1: speed 70 km/h is outside 0.1 to 60 km/h.",
                                                   "Row 2: speed 0.05 km/h is outside 0.1 to 60 km/h."]
    own = services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 30, "speed_kmh": 70}, "web")
    assert own["message"] == "speed_kmh 70 is outside 0.1 to 60."   # the log function's own words, unchanged
    assert r["failed"][0]["fix"] == own["fix"].replace("speed_kmh", "speed") == "Give speed as a number from 0.1 to 60."
    for bound in (0.1, 60):   # each bound the message quotes is itself taken
        assert imp(user, run(ago(3), "Outdoor Run", 30, speed=bound), dry=True)["row_outcomes"]["written"] == 1, bound


def test_beside_the_food_entries_sentence_the_rows_refused_are_named_rows(user):
    """Import pass B: "1 food entry in those rows was already there. 1 was refused." The second counts rows."""
    imp(user, meal(ago(1), ("Oats", 350, "breakfast")))
    for e in rows_of(FoodEntry, user.id):
        services.delete_entry(user.id, "food_entry", e.id)
    rows = [meal(ago(1), ("Oats", 350, "breakfast"), ("Dal", 500, "lunch")), weigh(ago(2), 1000),
            weigh(ago(3), 2000)]
    preview = imp(user, *rows, dry=True)
    assert preview["summary"] == ("Preview, nothing written. 3 rows from 7 to 9 Nov. Would write 1 food entry. 1 food "
                                  "entry in those rows is already there. 2 rows were refused.")
    real = imp(user, *rows)
    assert real["summary"] == ("Imported 1 of 3 rows from 7 to 9 Nov: 1 food entry. 1 food entry in those rows was "
                               "already there. 2 rows were refused. Nothing was replaced.")
    bare = imp(user, weigh(ago(5), 80), weigh(ago(6), 1000))   # no food entries' sentence: the bare count stays
    assert bare["summary"] == "Imported 1 of 2 rows from 4 to 5 Nov: 1 weigh-in. 1 was refused. Nothing was replaced."


def test_no_in_those_rows_sentence_for_a_food_row_already_there_whole(user):
    """The re-check's R3: the sentence counts matched entries of rows still written or left out, never a row that
    is already there whole."""
    imp(user, meal(ago(1), ("Oats", 350, "breakfast"), ("Dal", 500, "lunch")))
    rows = [meal(ago(1), ("Oats", 350, "breakfast"), ("Dal", 500, "lunch")), weigh(ago(2), 80)]
    preview, real = imp(user, *rows, dry=True), imp(user, *rows)
    assert preview["summary"] == ("Preview, nothing written. 2 rows from 8 to 9 Nov. Would write 1 weigh-in. 1 row is "
                                  "already there. Every weigh-in is 80 kg.")
    assert real["summary"] == ("Imported 1 of 2 rows from 8 to 9 Nov: 1 weigh-in. 1 was already there. Nothing was "
                               "replaced.")
    assert real["counts"]["food_entry"] == {"written": 0, "already_there": 2, "day_taken": 0, "failed": 0}
    for r in (preview, real):
        assert "in those rows" not in r["summary"]


def test_beside_the_food_entries_sentence_two_rows_already_there_are_plural(user):
    """The re-check's R4: "2 rows were already there.", never "2 rows was"."""
    imp(user, meal(ago(1), ("Oats", 350, "breakfast")), weigh(ago(2), 80), weigh(ago(3), 81))
    for e in rows_of(FoodEntry, user.id):
        services.delete_entry(user.id, "food_entry", e.id)
    rows = [weigh(ago(2), 80), weigh(ago(3), 81), meal(ago(1), ("Oats", 350, "breakfast"), ("Dal", 500, "lunch"))]
    preview = imp(user, *rows, dry=True)
    assert preview["summary"] == ("Preview, nothing written. 3 rows from 7 to 9 Nov. Would write 1 food entry. 1 food "
                                  "entry in those rows is already there. 2 rows are already there. Weigh-ins run from "
                                  "80 kg to 81 kg.")
    real = imp(user, *rows)
    assert real["summary"] == ("Imported 1 of 3 rows from 7 to 9 Nov: 1 food entry. 1 food entry in those rows was "
                               "already there. 2 rows were already there. Nothing was replaced.")


def test_beside_the_food_entries_sentence_the_rows_already_there_are_named_rows(user):
    """Was: "1 food entry in those rows was already there. 1 was already there." The second counts rows."""
    imp(user, meal(ago(1), ("Oats", 350, "breakfast")), weigh(ago(2), 80))
    for e in rows_of(FoodEntry, user.id):
        services.delete_entry(user.id, "food_entry", e.id)
    rows = [weigh(ago(2), 80), meal(ago(1), ("Oats", 350, "breakfast"), ("Dal", 500, "lunch"))]
    before = everything()
    preview = imp(user, *rows, dry=True)
    assert everything() == before
    assert preview["summary"] == ("Preview, nothing written. 2 rows from 8 to 9 Nov. Would write 1 food entry. 1 food "
                                  "entry in those rows is already there. 1 row is already there. Every weigh-in is "
                                  "80 kg.")
    real = imp(user, *rows)
    assert real["summary"] == ("Imported 1 of 2 rows from 8 to 9 Nov: 1 food entry. 1 food entry in those rows was "
                               "already there. 1 row was already there. Nothing was replaced.")


def test_a_partly_matched_food_row_on_a_held_day_says_its_matched_entries(user):
    """S4 on a day-taken row: counts had the matched entry as already there, and the summary said nothing of it."""
    services.log_food(user.id, {"date": ago(5), "entries": [{"description": "Oats", "kcal": 350, "meal": "breakfast"}]},
                      "web")
    row = meal(ago(5), ("Oats", 350, "breakfast"), ("Tea", 40, "snack"))
    before = everything()
    preview = imp(user, row, dry=True)
    real = imp(user, row)
    assert everything() == before
    for r in (preview, real):
        assert r["counts"]["food_entry"] == {"written": 0, "already_there": 1, "day_taken": 1, "failed": 0}
    assert preview["summary"] == ("Preview, nothing written. 1 row on Thu 5 Nov. Would write nothing. 1 food entry in "
                                  "those rows is already there. 1 row falls on days Spotter already holds.")
    # Was: "... 1 food entry in those rows was already there. 1 was left out: ...". Beside the food entries'
    # sentence the count names rows, as "already there" and "refused" do (fix round on 7e95ee9).
    assert real["summary"] == ("Imported 0 of 1 row on Thu 5 Nov. 1 food entry in those rows was already there. 1 row "
                               "was left out: its day already holds an entry. Nothing was replaced.")


def test_known_limit_a_moved_food_day_sent_later_with_fewer_entries_writes_them_again(user):
    """Not built in version 1, documented (the re-check's observation). Oats and Tea imported for one day and both
    moved to the next; a later export holds Oats alone for the first day. Its key is over Oats alone, the stored
    key over both, and nothing on the first day is live: Oats is written again. A food entry on that day takes it."""
    for n, held in ((3, False), (6, True)):
        imp(user, meal(ago(n), ("Oats", 350, "breakfast"), ("Tea", 40, "snack")))
        for e in rows_of(FoodEntry, user.id, date=TODAY - timedelta(days=n)):
            services.edit_entry(user.id, "food_entry", e.id, {"date": ago(n - 1)}, "web")
        if held:
            services.log_food(user.id, {"date": ago(n), "entries": [{"description": "Toast", "kcal": 200}]}, "web")
        again = imp(user, meal(ago(n), ("Oats", 350, "breakfast")))
        want = {"written": 0, "already_there": 0, "day_taken": 1, "failed": 0} if held else {
            "written": 1, "already_there": 0, "day_taken": 0, "failed": 0}
        assert again["counts"]["food_entry"] == want, n
        oats = rows_of(FoodEntry, user.id, description="Oats", deleted_at=None)
        assert sorted(e.date for e in oats) == sorted([TODAY - timedelta(days=n - 1)] + ([] if held else [
            TODAY - timedelta(days=n)])), n
        for e in oats:   # clear the way for the next pass
            services.delete_entry(user.id, "food_entry", e.id)


def test_known_limit_a_food_entry_imported_beside_deleted_entries_comes_back_after_a_move_or_an_edit_and_delete(user):
    """Not built in version 1, documented. An entry written beside entries that were already there stores a key
    over the entries written, not over its whole row, so a re-run of the row finds it neither by key nor, once
    it is moved or edited, by value. Pinned as it is: Tea comes back on its first day."""
    for n, change in ((3, "move"), (6, "edit then delete")):
        imp(user, meal(ago(n), ("Oats", 350, "breakfast")))
        for e in rows_of(FoodEntry, user.id, date=TODAY - timedelta(days=n)):
            services.delete_entry(user.id, "food_entry", e.id)
        row = meal(ago(n), ("Oats", 350, "breakfast"), ("Tea", 40, "snack"))
        assert imp(user, row)["counts"]["food_entry"]["written"] == 1   # Tea, its key over Tea alone
        [tea] = rows_of(FoodEntry, user.id, date=TODAY - timedelta(days=n), description="Tea")
        if change == "move":
            services.edit_entry(user.id, "food_entry", tea.id, {"date": ago(n - 1)}, "web")
        else:
            services.edit_entry(user.id, "food_entry", tea.id, {"kcal": 60}, "web")
            services.delete_entry(user.id, "food_entry", tea.id)
        again = imp(user, row)
        assert again["counts"]["food_entry"] == {"written": 1, "already_there": 1, "day_taken": 0, "failed": 0}, change
        assert len(rows_of(FoodEntry, user.id, date=TODAY - timedelta(days=n), description="Tea", deleted_at=None)) == 1
