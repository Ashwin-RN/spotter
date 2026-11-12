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


def test_bs_goals_of_every_kind_and_trends_never_reach_a(world):
    a, b, _ = world
    goal_date = (users.today_for(b) + timedelta(days=60)).isoformat()
    for data in ({"kind": "measurement", "measurement_kind": "waist", "target_value": 85},
                 {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_value": 27},
                 {"kind": "bodyweight", "target_value": 85}):
        made = services.set_goal(b.id, {**data, "target_date": goal_date}, "claude")
        assert "error" not in made, made
        assert services.set_goal(a.id, {"goal_id": made["id"], "status": "dropped"}, "claude")["error"] == "not_found"
    assert len(services.get_goals(b.id)["goals"]) == 4
    mine = services.get_goals(a.id, include_closed=True)
    assert mine["goals"] == [] and mine["closed"] == []
    assert len(services.goal_charts(b.id)["goals"]) == 4 and services.goal_charts(a.id)["goals"] == []
    # A's own goals start from A's own numbers: nothing of B's is logged for A.
    none_yet = services.set_goal(a.id, {"kind": "measurement", "measurement_kind": "waist", "target_value": 85,
                                        "target_date": goal_date}, "claude")
    assert none_yet["error"] == "missing_field"
    trends = services.get_trends(a.id)
    assert trends["bodyweight"] is None and trends["calories"] is None and trends["cardio"]["minutes_this_week"] == 0
    assert services.get_trends(b.id)["bodyweight"]["now"] == 90
    assert services.list_saved_foods(a.id)["foods"] == []
    assert [f["id"] for f in services.list_saved_foods(b.id)["foods"]] == [world[2]["saved_food"]]


def test_sample_data_is_per_person(world):
    a, b, _ = world
    assert services.sample_state(a.id) == {"empty": True, "sample_rows": 0}
    assert services.add_sample_data(b.id, "web")["error"] == "not_empty"   # B's own rows
    assert "error" not in services.add_sample_data(a.id, "web")
    before = {kind: snapshot(model, world[2][kind]) for kind, (model, _) in CASES.items()}
    services.remove_sample_data(a.id, "web")
    assert {kind: snapshot(model, world[2][kind]) for kind, (model, _) in CASES.items()} == before


# ---------------------------------------------------------------- deleting an account

def test_delete_account_empties_every_table_for_the_person_and_leaves_the_other(world):
    from sqlalchemy import func

    from app import calllog
    from app.models import CallLog, User
    a, b, ids = world
    services.log_bodyweight(a.id, {"kg": 70}, "web")
    services.delete_entry(b.id, "bodyweight", ids["bodyweight"])   # a deleted row goes too
    started = db.utcnow()
    row_id = calllog.start("delete_account", started, b.id)       # as the web writes it, first
    calllog.start("get_log", started, b.id)
    r = services.delete_account(b.id)
    assert r == {"deleted": True, "rows": {"user": 1, "lift_session": 1, "bodyweight": 1, "measurement": 1, "goal": 1,
                                           "saved_food": 1, "food_entry": 1, "cardio_entry": 1, "lift_set": 2}}
    calllog.finish(row_id, 12, True, None, 40)                     # its own row is still there to finish
    with db.new_session() as s:
        assert s.get(User, b.id) is None and s.get(User, a.id) is not None
        for table in user_tables():
            model = next(m.class_ for m in Base.registry.mappers if m.class_.__tablename__ == table)
            assert s.scalar(select(func.count()).select_from(model).where(model.user_id == b.id)) == 0, table
        assert s.scalar(select(func.count()).select_from(LiftSet)) == 0
        kept = list(s.scalars(select(CallLog).where(CallLog.function.in_(("delete_account", "get_log")))))
        assert len(kept) == 2 and all(c.user_id is None for c in kept)
        finished = s.get(CallLog, row_id)
        assert finished.ok is True and finished.ms == 12 and finished.error_code is None
    assert services.get_log(a.id)["totals"]["weigh_ins"] == 1
    assert services.delete_account(b.id)["error"] == "no_account"   # a retry finds nothing left to do


def test_delete_account_goes_through_at_the_write_limit_and_touches_only_its_person(world):
    from app.models import CallLog, User
    a, b, ids = world
    with db.session_scope() as s:
        s.add_all(CallLog(user_id=a.id, function="log_lifts", started_at=db.utcnow(), ms=1, ok=True, result_bytes=1)
                  for _ in range(services.WRITE_LIMIT + 1))
    before = {kind: snapshot(model, ids[kind]) for kind, (model, _) in CASES.items()}
    assert services.delete_account(a.id)["deleted"] is True   # the person can always leave
    with db.new_session() as s:
        assert s.get(User, a.id) is None and s.get(User, b.id) is not None
    assert {kind: snapshot(model, ids[kind]) for kind, (model, _) in CASES.items()} == before


def test_bs_food_and_cardio_never_reach_as_day(world):
    a, _, _ = world
    day = services.get_log(a.id)["days"][0]
    assert day["food"]["total_kcal"] == 0 and day["cardio"] == []
    assert services.get_progress(a.id, "Outdoor Run")["cardio"]["sessions"] == []


def test_the_web_only_reads_never_show_bs_rows(world):
    """get_days, get_cardio and lifts.history's reps_only read only the person's own rows (stage 6)."""
    from app.services import lifts
    a, b, _ = world
    services.log_lifts(b.id, {"sets": [{"exercise": "Pull-Up", "weight_kg": 0, "reps": 9}]}, "web")
    mine = services.get_days(a.id)
    counts = (mine["lift_sessions_28d"], mine["lift_days_28d"], mine["cardio_days_28d"])
    assert mine["weight"] is None and counts == (0, 0, 0)
    assert all(d["kcal"] is None and not d["weighed"] and not d["food_entries"] for d in mine["days"])
    theirs = services.get_days(b.id)
    assert theirs["weight"]["latest"]["kg"] == 90 and theirs["lift_sessions_28d"] == 2
    assert services.get_cardio(a.id) == {"today": mine["today"], "activities": [], "includes_sample": False}
    run = services.get_cardio(a.id, "Outdoor Run")
    assert run["sessions"] == [] and run["bests"] == {"fastest_pace": None, "longest": None, "times": []}
    assert run["week"]["minutes"] == 0
    assert len(services.get_cardio(b.id, "Outdoor Run")["sessions"]) == 1
    with db.new_session() as s:
        assert lifts.history(s, a.id, users.today_for(a))["reps_only"] == {}
        assert lifts.history(s, b.id, users.today_for(b))["reps_only"] == {"Pull-Up": {"reps": 9, "date": mine["today"]}}


# ---------------------------------------------------------------- importing

def _zoned(user):
    """An import needs the person's time zone: UTC, as B's today is read until one is set."""
    from app.models import User
    with db.session_scope() as s:
        s.get(User, user.id).time_zone = "UTC"


def test_as_import_never_matches_blocks_or_touches_bs_rows_on_the_same_days(world):
    """A sends, on B's own day, exactly what B holds. Nothing of B's is already there or takes the day for A,
    and every column of B's rows is as it was."""
    a, b, ids = world
    _zoned(a)
    before = {kind: snapshot(model, ids[kind]) for kind, (model, _) in CASES.items()}
    on = before["bodyweight"]["date"].isoformat()
    meal = before["food_entry"]
    rows = [{"kind": "lift_session", "date": on, "notes": "B only",
             "sets": [{"exercise": "Deadlift", "weight": 140, "reps": 5, "times": 2}]},
            {"kind": "bodyweight", "date": on, "weight": 90},
            {"kind": "measurement", "date": on, "measurement_kind": "waist", "value": 90},
            {"kind": "food_entry", "date": on, "entries": [
                {"description": meal["description"], "kcal": meal["kcal"], "meal": meal["meal"]}]},
            {"kind": "cardio_entry", "date": on, "exercise": "Outdoor Run", "minutes": 30, "distance": 5}]
    for dry in (True, False):
        r = services.import_entries(a.id, {"rows": rows, "dry_run": dry}, "claude")
        assert r["row_outcomes"] == {"written": 5, "already_there": 0, "day_taken": 0, "failed": 0}, r
        assert r["day_taken"] == []
    assert {kind: snapshot(model, ids[kind]) for kind, (model, _) in CASES.items()} == before
    assert services.get_log(b.id)["totals"] == {"sessions": 1, "weigh_ins": 1, "measurements": 1,
                                                "food_entries": 1, "cardio_entries": 1}
    # B's own import of the same rows: B's held entries are already there or take the day, A's never count.
    _zoned(b)
    mine = services.import_entries(b.id, {"rows": rows, "dry_run": True}, "web")["row_outcomes"]
    assert mine["written"] == 0 and mine["already_there"] + mine["day_taken"] == 5


def test_as_import_limit_counts_only_as_own_imported_rows(world):
    from sqlalchemy import insert

    from app.models import IMPORTED
    a, b, _ = world
    _zoned(a)
    at = db.utcnow()
    with db.session_scope() as s:
        s.execute(insert(FoodEntry), [
            {"user_id": b.id, "date": users.today_for(b), "meal": "snack", "description": "x", "kcal": 1,
             "source": "you_said", "estimated": False, "logged_by": IMPORTED, "dedupe_key": "k", "created_at": at,
             "updated_at": at, "is_sample": False} for _ in range(services.imports.IMPORT_LIMIT)])
    r = services.import_entries(a.id, {"rows": [{"kind": "bodyweight", "date": users.today_for(a).isoformat(),
                                                 "weight": 70}], "dry_run": False}, "claude")
    assert r["imported_last_24h"] == 0 and r["within_import_limit"] is True and r["row_outcomes"]["written"] == 1
