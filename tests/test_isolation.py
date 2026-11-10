"""Own data only, through the services: A gets "not found" for B's ids, and B's rows do not change.

Users A and B. B logs one entry of every kind and a goal. For each (kind, operation) pair below, A
calls with B's id. The result must be not_found, the very same dict A gets for an id that does not
exist, and every column of B's row (and of a session's sets) must be as it was.

The guard test keeps the table honest: every table with a user_id is either in CASES or on the
short EXEMPT list, so a new table without isolation coverage fails here.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import db, services, users
from app.models import (
    Base, Bodyweight, CardioEntry, FoodEntry, Goal, LiftSession, LiftSet, Measurement, SavedFood,
    entry_models, user_tables,
)

MISSING = 10 ** 9

# kind -> (model, the operations A tries with B's id).
# read/edit/delete/restore go through get_entry, edit_entry, delete_entry and restore_entry. A goal
# is read with get_entry and changed only through set_goal: edit moves its target, delete marks it
# dropped, restore reopens it.
CASES = {
    "lift_session": (LiftSession, ("read", "edit", "delete", "restore")),
    "bodyweight": (Bodyweight, ("read", "edit", "delete", "restore")),
    "measurement": (Measurement, ("read", "edit", "delete", "restore")),
    "goal": (Goal, ("read", "edit", "delete", "restore")),
    "food_entry": (FoodEntry, ("read", "edit", "delete", "restore")),
    "cardio_entry": (CardioEntry, ("read", "edit", "delete", "restore")),
    # A saved food is changed only through save_food, by name: A's save_food never reaches B's (tested below).
    "saved_food": (SavedFood, ("read", "delete", "restore")),
}

# Tables with a user_id that no by-id function reaches. (The user table is the account itself: it
# has no user_id, so it never appears in user_tables().)
EXEMPT = {
    "call_log": "Written by the MCP wrapper, never read back by id, and holds no content.",
}

# Tables with no user_id of their own, owned through a parent that has one.
OWNED_THROUGH = {"lift_set": "lift_session"}


def snapshot(model, row_id) -> dict:
    """Every column of the row, read fresh from the database; for a session, its sets too."""
    with db.new_session() as s:
        row = s.get(model, row_id)
        out = {c.name: getattr(row, c.key) for c in model.__table__.columns}
        if model is LiftSession:
            out["sets"] = [{c.name: getattr(x, c.key) for c in LiftSet.__table__.columns}
                           for x in s.scalars(select(LiftSet).where(LiftSet.session_id == row_id).order_by(LiftSet.id))]
        return out


@pytest.fixture
def world(make_user):
    """A and B, and one of everything for B. Returns (A, B, {kind: B's id})."""
    a, b = make_user(subject="user_A"), make_user(subject="user_B")
    today = users.today_for(b)
    ids = {}
    ids["lift_session"] = services.log_lifts(b.id, {"notes": "B only", "sets": [
        {"exercise": "Deadlift", "weight_kg": 140, "reps": 5},
        {"exercise": "Deadlift", "weight_kg": 140, "reps": 5}]}, "claude")["id"]
    ids["bodyweight"] = services.log_bodyweight(b.id, {"kg": 90}, "web")["id"]
    ids["measurement"] = services.log_measurement(b.id, {"kind": "waist", "value": 90}, "web")["id"]
    ids["goal"] = services.set_goal(b.id, {"kind": "lift", "exercise": "Deadlift", "target_value": 200,
                                           "target_date": (today + timedelta(days=60)).isoformat()}, "claude")["id"]
    ids["saved_food"] = services.save_food(b.id, {"name": "B's roti", "serving_label": "1 roti",
                                                  "kcal_per_serving": 120}, "web")["id"]
    ids["food_entry"] = services.log_food(b.id, {"entries": [
        {"saved_food_id": ids["saved_food"], "servings": 2}]}, "claude")["ids"][0]
    ids["cardio_entry"] = services.log_cardio(b.id, {"exercise": "Outdoor Run", "minutes": 30, "distance_km": 5},
                                              "web")["id"]
    return a, b, ids


def attempt(op, kind, user_id, row_id):
    if kind == "goal":
        data = {"read": None, "edit": {"target_value": 150}, "delete": {"status": "dropped"},
                "restore": {"status": "open"}}[op]
        if data is None:
            return services.get_entry(user_id, "goal", row_id)
        return services.set_goal(user_id, {"goal_id": row_id, **data}, "claude")
    if op == "edit":
        return services.edit_entry(user_id, kind, row_id, EDITS[kind], "claude")
    call = {"read": services.get_entry, "delete": services.delete_entry, "restore": services.restore_entry}[op]
    return call(user_id, kind, row_id)


# One change per kind, each one a log would accept, so only ownership can refuse it.
EDITS = {
    "lift_session": {"notes": "A was here", "sets": [{"exercise": "Deadlift", "weight_kg": 10, "reps": 1}]},
    "bodyweight": {"kg": 60, "date": "2026-01-01"},
    "measurement": {"value": 70},
    "food_entry": {"kcal": 10, "description": "A was here", "meal": "snack"},
    "cardio_entry": {"exercise": "Hiking", "minutes": 5, "distance_km": 1, "machine_kcal": 1},
}


def prepare(op, kind, b, row_id):
    """For a restore to mean anything, B's row must be in its deleted (or closed) state first."""
    if op != "restore":
        return
    if kind == "goal":
        assert services.set_goal(b.id, {"goal_id": row_id, "status": "dropped"}, "web")["goal"]["status"] == "dropped"
    else:
        assert services.delete_entry(b.id, kind, row_id)["deleted"] is True


PAIRS = [(kind, op) for kind, (_, ops) in CASES.items() for op in ops]


@pytest.mark.parametrize("kind,op", PAIRS, ids=[f"{k}-{o}" for k, o in PAIRS])
def test_a_gets_not_found_for_bs_id_and_bs_row_is_unchanged(world, kind, op):
    a, b, ids = world
    model = CASES[kind][0]
    prepare(op, kind, b, ids[kind])
    before = snapshot(model, ids[kind])

    result = attempt(op, kind, a.id, ids[kind])
    missing = attempt(op, kind, a.id, MISSING)
    assert result["error"] == "not_found", result
    assert result == missing  # word for word: nothing tells A the id exists
    assert snapshot(model, ids[kind]) == before


@pytest.mark.parametrize("kind", [k for k, (_, ops) in CASES.items() if "restore" in ops and k != "goal"])
def test_a_cannot_restore_bs_live_entry_either(world, kind):
    a, _, ids = world
    before = snapshot(CASES[kind][0], ids[kind])
    assert services.restore_entry(a.id, kind, ids[kind]) == services.restore_entry(a.id, kind, MISSING)
    assert snapshot(CASES[kind][0], ids[kind]) == before


def test_a_weigh_in_on_the_same_date_leaves_bs_untouched(world):
    a, b, ids = world
    before = snapshot(Bodyweight, ids["bodyweight"])
    r = services.log_bodyweight(a.id, {"kg": 70}, "claude")
    assert r["duplicate"] is False and r["replaced"] is None and r["recent"] == [
        {"date": users.today_for(a).isoformat(), "kg": 70}]
    assert snapshot(Bodyweight, ids["bodyweight"]) == before
    assert services.log_bodyweight(b.id, {"kg": 90}, "web")["id"] == ids["bodyweight"]  # B's own duplicate still


def test_a_restoring_a_weigh_in_never_swaps_out_bs(world):
    a, _, ids = world
    first = services.log_bodyweight(a.id, {"kg": 70}, "claude")
    services.delete_entry(a.id, "bodyweight", first["id"])  # A has no live weigh-in today; B has one
    before = snapshot(Bodyweight, ids["bodyweight"])
    back = services.restore_entry(a.id, "bodyweight", first["id"])
    assert back["restored"] is True and back["replaced"] == []
    assert snapshot(Bodyweight, ids["bodyweight"]) == before


def test_bs_open_goals_never_appear_in_as_warnings(world):
    a, _, _ = world
    services.log_lifts(a.id, {"sets": [{"exercise": "Deadlift", "weight_kg": 100, "reps": 5}]}, "claude")
    mine = services.set_goal(a.id, {"kind": "lift", "exercise": "Deadlift", "target_value": 150,
                                    "target_date": (users.today_for(a) + timedelta(days=30)).isoformat()}, "claude")
    assert "error" not in mine and mine["warnings"] == []  # B's open Deadlift goal is not A's


def test_the_same_session_as_b_is_not_a_duplicate_for_a(world):
    a, _, ids = world
    same = services.log_lifts(a.id, {"notes": "B only", "sets": [
        {"exercise": "Deadlift", "weight_kg": 140, "reps": 5},
        {"exercise": "Deadlift", "weight_kg": 140, "reps": 5}]}, "claude")
    assert same["duplicate"] is False and same["id"] != ids["lift_session"] and same["new_bests"] == []


def test_a_cannot_reach_bs_sets(world):
    a, b, ids = world
    with db.new_session() as s:
        set_ids = list(s.scalars(select(LiftSet.id).where(LiftSet.session_id == ids["lift_session"])))
    assert set_ids
    assert services.get_entry(a.id, "lift_session", ids["lift_session"])["error"] == "not_found"
    for set_id in set_ids:  # a set is not a kind of its own: it is reached only through its session
        assert services.get_entry(a.id, "lift_set", set_id)["error"] == "unknown_kind"
    progress = services.get_progress(a.id, "Deadlift")["lift"]
    assert progress["sessions"] == 0 and progress["goals"] == [] and progress["best_set"] is None
    assert services.get_progress(b.id, "Deadlift")["lift"]["sessions"] == 1


def test_a_sees_none_of_bs_data_in_reads(world):
    a, b, _ = world
    summary = services.get_progress(a.id)
    assert summary["lifts"] == [] and summary["goals"] == []
    me = services.whoami(a.id)
    assert me["account_id"] == a.id and me["user_id"] == "user_A"
    assert services.whoami(b.id)["user_id"] == "user_B"


# ---------------------------------------------------------------- the guard

def test_every_table_with_a_user_id_is_covered_or_exempt():
    tables = set(user_tables())
    uncovered = tables - set(CASES) - set(EXEMPT)
    assert not uncovered, f"Add these tables to CASES (or, with a reason, to EXEMPT): {sorted(uncovered)}"
    assert set(EXEMPT) <= tables, "EXEMPT names a table that has no user_id: remove it"
    assert set(CASES) <= tables, "CASES names a table that has no user_id"
    for model in entry_models():
        assert model.__tablename__ in CASES, f"{model.__tablename__} is an entry kind with no isolation row"


def test_every_table_owned_through_a_parent_is_named():
    owned = set(user_tables())
    for table in Base.metadata.sorted_tables:
        if table.name in owned:
            continue
        parents = {fk.column.table.name for fk in table.foreign_keys} & owned
        if parents:
            assert OWNED_THROUGH.get(table.name) in parents, (
                f"{table.name} hangs off {sorted(parents)} with no user_id of its own: add it to OWNED_THROUGH "
                "and a check that A cannot reach B's rows through it")



def test_a_edits_of_bs_entries_change_nothing_even_with_every_field(world):
    a, b, ids = world
    for kind in EDITS:
        before = snapshot(CASES[kind][0], ids[kind])
        r = services.edit_entry(a.id, kind, ids[kind], EDITS[kind], "web")
        assert r == services.edit_entry(a.id, kind, MISSING, EDITS[kind], "web") and r["error"] == "not_found"
        assert snapshot(CASES[kind][0], ids[kind]) == before
        mine = services.edit_entry(b.id, kind, ids[kind], EDITS[kind], "web")  # the owner can
        assert mine["changed"], mine


def test_a_measurement_on_bs_day_leaves_bs_untouched(world):
    a, b, ids = world
    before = snapshot(Measurement, ids["measurement"])
    r = services.log_measurement(a.id, {"kind": "waist", "value": 70}, "claude")
    assert r["replaced"] is None and r["recent"] == [{"date": users.today_for(a).isoformat(), "value": 70}]
    assert snapshot(Measurement, ids["measurement"]) == before
    assert services.get_log(a.id)["totals"] == {"sessions": 0, "weigh_ins": 0, "measurements": 1,
                                                "food_entries": 0, "cardio_entries": 0}
    assert services.get_log(b.id)["totals"]["measurements"] == 1


def test_the_guard_sees_real_measurement_rows(world):
    _, b, ids = world
    with db.new_session() as s:
        row = s.get(Measurement, ids["measurement"])
        assert row.user_id == b.id and row.deleted_at is None
    assert "measurement" in user_tables() and "edit" in CASES["measurement"][1]



def test_find_food_never_shows_bs_foods_and_a_cannot_log_bs_saved_food(world):
    a, b, ids = world
    r = services.find_food(a.id, "roti")
    assert r["saved"] == [] and r["recent"] == []
    assert services.find_food(b.id, "roti")["saved"][0]["id"] == ids["saved_food"]
    used = services.log_food(a.id, {"entries": [{"saved_food_id": ids["saved_food"], "servings": 1}]}, "claude")
    assert used["error"] == "not_found" and services.get_log(a.id)["totals"]["food_entries"] == 0


def test_as_save_food_with_bs_name_makes_as_own_and_leaves_bs_alone(world):
    a, b, ids = world
    before = snapshot(SavedFood, ids["saved_food"])
    mine = services.save_food(a.id, {"name": "B's roti", "serving_label": "1", "kcal_per_serving": 1}, "web")
    assert mine["created"] is True and mine["id"] != ids["saved_food"]
    assert snapshot(SavedFood, ids["saved_food"]) == before


def test_bs_food_and_cardio_never_reach_as_day(world):
    a, _, _ = world
    day = services.get_log(a.id)["days"][0]
    assert day["food"]["total_kcal"] == 0 and day["cardio"] == []
    assert services.get_progress(a.id, "Outdoor Run")["cardio"]["sessions"] == []
