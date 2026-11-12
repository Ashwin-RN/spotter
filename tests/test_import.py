"""import_entries: the service alone, with plain dicts. A preview writes nothing; a real run never replaces,
never adds to a held day, never brings back a deleted import row, and writes nothing the second time.

Every test runs on a fixed clock, NOW (Tuesday 10 November 2026, 17:30 in Kolkata), so no assertion reads
the real date. The date-rule tests move the clock themselves, either side of the person's midnight.
"""

import json
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


def imp(user, *rows, dry=False, by="claude", **data):
    """An import of these rows. dry=None leaves dry_run out."""
    sent = {"rows": list(rows), **data}
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


@pytest.mark.parametrize("dry_run", [None, True, "true", "yes", 1, ""])
def test_dry_run_left_out_or_true_is_a_preview(user, dry_run):
    before = everything()
    r = imp(user, weigh(ago(1), 80), dry=None) if dry_run is None else imp(user, weigh(ago(1), 80), dry=dry_run)
    assert r["dry_run"] is True and r["counts"]["bodyweight"]["written"] == 1
    assert everything() == before


@pytest.mark.parametrize("dry_run", [False, "false", "False", "no", 0, "0"])
def test_only_a_plain_false_writes(user, dry_run):
    r = imp(user, weigh(ago(1), 80), dry=dry_run)
    assert r["dry_run"] is False and total(Bodyweight, user.id) == 1


@pytest.mark.parametrize("dry_run", ["maybe", 2, [], {}, 0.5])
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
    assert r["row_outcomes"]["day_taken"] == 1 and r["day_taken"][0]["entry_id"] == first


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
def test_an_edited_then_deleted_import_row_comes_back_at_its_old_value_as_built(user, kind):
    """As built, a ruling wanted: the edit changed the deleted row's value, so the row as exported no longer
    matches it and the day holds nothing live. The re-run writes the exported value again. Food: the edited
    entry comes back, the untouched deleted one stays deleted."""
    imp(user, KINDS[kind]["row"](ago(1)))
    first = rows_of(MODELS[kind], user.id)[0].id
    services.edit_entry(user.id, kind, first, KINDS[kind]["edit"], "web")
    for row in rows_of(MODELS[kind], user.id):
        services.delete_entry(user.id, kind, row.id)
    r = imp(user, KINDS[kind]["row"](ago(1)))
    assert r["row_outcomes"]["written"] == 1 and r["counts"][kind]["written"] == 1


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
            meal(ago(1), ("Feast", 5000.6, "dinner")), run(ago(1), "Outdoor Run", 30, 5, speed_kmh=30),
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
    assert got[4]["message"] == "Row 5: " + own[4]["message"] and got[4]["fix"] == own[4]["fix"]
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
    ({"rows": [lift(ago(n), *[("Deadlift", 100, 5)] * 50) for n in range(1, 21)] + [meal(ago(1), ("Tea", 40, "snack"))]},
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
    assert set(preview) == {"kind", "dry_run", "rows", "from", "to", "units", "counts", "row_outcomes", "ranges",
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
        "dry_run": "false", "weight_unit": "lb"}, "web")
    assert r["row_outcomes"]["written"] == 2 and rows_of(Bodyweight, user.id)[0].kg == 80.06


def test_users_with_no_time_zone_are_refused_before_any_row_is_read(make_user):
    user = make_user()
    with db.session_scope() as s:
        s.get(User, user.id).time_zone = None
    assert imp(user, "not a row")["error"] == "time_zone_not_set"
