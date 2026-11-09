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
    Base, Bodyweight, CardioEntry, Exercise, FoodEntry, Goal, LiftSession, LiftSet, Measurement, entry_models,
    user_tables,
)

MISSING = 10 ** 9

# kind -> (model, the operations A tries with B's id).
# read/delete/restore go through get_entry, delete_entry and restore_entry. A goal is read with
# get_entry and changed only through set_goal: edit moves its target, delete marks it dropped,
# restore reopens it. No entry has an edit-by-id function in this stage; when one arrives, its
# operation joins these rows.
CASES = {
    "lift_session": (LiftSession, ("read", "delete", "restore")),
    "bodyweight": (Bodyweight, ("read", "delete", "restore")),
    "goal": (Goal, ("read", "edit", "delete", "restore")),
    # No function logs these yet; the by-id functions are generic, so they are covered now.
    "measurement": (Measurement, ("read", "delete", "restore")),
    "food_entry": (FoodEntry, ("read", "delete", "restore")),
    "cardio_entry": (CardioEntry, ("read", "delete", "restore")),
}

# Tables with a user_id that no by-id function reaches. (The user table is the account itself: it
# has no user_id, so it never appears in user_tables().)
EXEMPT = {
    "call_log": "Written by the MCP wrapper, never read back by id, and holds no content.",
    "saved_food": "No service reads or writes it in this stage; its functions add a row to CASES.",
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
    ids["goal"] = services.set_goal(b.id, {"kind": "lift", "exercise": "Deadlift", "target_value": 200,
                                           "target_date": (today + timedelta(days=60)).isoformat()}, "claude")["id"]
    with db.session_scope() as s:
        lift = s.scalar(select(Exercise.id).where(Exercise.name == "Deadlift"))  # any exercise row will do
        rows = {
            "measurement": Measurement(user_id=b.id, date=today, kind="waist", value=90, unit="cm",
                                       logged_by="web", dedupe_key="1" * 64),
            "food_entry": FoodEntry(user_id=b.id, date=today, meal="lunch", description="B's lunch", kcal=650,
                                    source="you_said", estimated=False, logged_by="claude", dedupe_key="2" * 64),
            "cardio_entry": CardioEntry(user_id=b.id, date=today, exercise_id=lift, minutes=30, logged_by="web",
                                        dedupe_key="3" * 64),
        }
        s.add_all(rows.values())
        s.flush()
        ids.update({kind: row.id for kind, row in rows.items()})
    return a, b, ids


def attempt(op, kind, user_id, row_id):
    if kind == "goal":
        data = {"read": None, "edit": {"target_value": 150}, "delete": {"status": "dropped"},
                "restore": {"status": "open"}}[op]
        if data is None:
            return services.get_entry(user_id, "goal", row_id)
        return services.set_goal(user_id, {"goal_id": row_id, **data}, "claude")
    call = {"read": services.get_entry, "delete": services.delete_entry, "restore": services.restore_entry}[op]
    return call(user_id, kind, row_id)


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
