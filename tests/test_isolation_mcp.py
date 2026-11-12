"""The cross-user test through MCP: two tokens, two people. A sees none of B's data, and an id of B's
gets A the same "not found" as an id that does not exist, with B's row unchanged.

The guard: every function with an id-shaped argument (`id`, or a name ending `_id`) must have a row in
ID_CALLS, so a new function that takes an id cannot skip this test. A is allowed everything
(log_delete), so a refusal is never the switch's: it can only be the owner check.
"""

import re
from datetime import date, timedelta

import pytest

from app import db, services
from app.models import Goal, SavedFood, User
from app.services.common import row_dict

from .conftest import claims, sign
from .test_mcp import call, failed, ok, squat, tool_list

A, B = "user_01ALICE", "user_01BOB"
MISSING_ID = 2_000_000_000
ENTRY_KINDS = ("lift_session", "bodyweight", "measurement", "food_entry", "cardio_entry")
EDITS = {"lift_session": {"notes": "A's edit", "sets": squat(10)}, "bodyweight": {"kg": 70},
         "measurement": {"value": 70}, "food_entry": {"kcal": 999, "description": "A's edit"},
         "cardio_entry": {"minutes": 99, "notes": "A's edit"}}
# Id-shaped arguments that point at no person's rows: the USDA table is shared and public.
SHARED_IDS = {"usda_fdc_id"}

# (function, its id argument, the kind of B's row it is pointed at, the other arguments a real call sends)
ID_CALLS = [
    ("set_goal", "goal_id", "goal", {"status": "dropped", "target_value": 300, "text": "A's edit"}),
    *[("edit_entry", "entry_id", kind, {"kind": kind, **EDITS[kind]}) for kind in ENTRY_KINDS],
    *[("delete_entry", "entry_id", kind, {"kind": kind}) for kind in ENTRY_KINDS],
    *[("restore_entry", "entry_id", kind, {"kind": kind}) for kind in ENTRY_KINDS],
    ("log_food", "saved_food_id", "saved_food", {}),  # inside an entry: see food_call below
]


def token(subject: str) -> str:
    """A token for this person. It names them, so no userinfo lookup is needed."""
    return sign(claims(sub=subject, email=f"{subject}@example.com", name=subject))


def allow_everything(http, subject: str) -> int:
    account = ok(http, token(subject), "whoami")["account_id"]
    with db.session_scope() as session:
        session.get(User, account).claude_can = "log_delete"
    return account


@pytest.fixture
def bob(http) -> dict:
    """B logs a lift session, a weigh-in, a measurement and a lift goal, and deletes a second measurement.
    Returns B's account and the ids, by kind."""
    account = allow_everything(http, B)
    t = token(B)
    today = date.fromisoformat(ok(http, t, "whoami")["today"])
    session = ok(http, t, "log_lifts", {"sets": squat(120), "notes": "Bob's squat",
                                        "date": (today - timedelta(days=2)).isoformat()})
    weigh_in = ok(http, t, "log_bodyweight", {"kg": 91.5})
    measurement = ok(http, t, "log_measurement", {"kind": "waist", "value": 97.25})
    gone = ok(http, t, "log_measurement", {"kind": "hips", "value": 103.75})
    ok(http, t, "delete_entry", {"kind": "measurement", "entry_id": gone["id"]})
    goal = ok(http, t, "set_goal", {"kind": "lift", "exercise": "Back Squat", "target_value": 180,
                                    "target_date": (today + timedelta(days=90)).isoformat(), "text": "Bob's goal"})
    saved = ok(http, t, "save_food", {"name": "Bob's roti", "serving_label": "1 roti", "kcal_per_serving": 123})
    food = ok(http, t, "log_food", {"entries": [{"description": "Bob's dal", "kcal": 321}]})
    cardio = ok(http, t, "log_cardio", {"exercise": "Treadmill Walk", "minutes": 47, "notes": "Bob's walk"})
    return {"account": account, "goal": goal["id"], "lift_session": session["id"], "bodyweight": weigh_in["id"],
            "measurement": measurement["id"], "deleted_measurement": gone["id"], "saved_food": saved["id"],
            "food_entry": food["ids"][0], "cardio_entry": cardio["id"]}


def snapshot(account: int, kind: str, entry_id: int) -> dict:
    """B's row as it is, deleted or not, through B's own account."""
    if kind in ("goal", "saved_food"):
        with db.session_scope() as session:
            return row_dict(session.get(Goal if kind == "goal" else SavedFood, entry_id))
    found = services.get_entry(account, kind, entry_id)
    if found.get("error") == "not_found":  # a deleted row: read it directly
        model = services.entry_kinds()[kind]
        with db.session_scope() as session:
            return row_dict(session.get(model, entry_id))
    return found["entry"]


def id_arguments(schema: dict) -> set[str]:
    """Every id-shaped argument, at the top or inside a nested item (a food entry's saved_food_id)."""
    names = set(schema.get("properties", {}))
    for nested in schema.get("$defs", {}).values():
        names |= set(nested.get("properties", {}))
    return {n for n in names if (n == "id" or n.endswith("_id")) and n not in SHARED_IDS}


def test_every_function_that_takes_an_id_is_in_the_table(http):
    taking_ids = {t["name"]: id_arguments(t["inputSchema"]) for t in tool_list(http, sign())}
    taking_ids = {name: props for name, props in taking_ids.items() if props}
    expected: dict[str, set[str]] = {}
    for function, argument, _, _ in ID_CALLS:
        expected.setdefault(function, set()).add(argument)
    assert taking_ids == expected


def food_call(argument: str, theirs_id: int) -> dict:
    """log_food carries the id inside an entry."""
    return {"entries": [{"description": "A's roti", argument: theirs_id, "servings": 1}]}


def test_a_sees_none_of_bs_data(http, bob):
    t = token(A)
    who = ok(http, t, "whoami")
    assert who["user_id"] == A and B not in str(who)
    summary = ok(http, t, "get_progress")
    assert summary["lifts"] == [] and summary["goals"] == [] and summary["note"]
    lift = ok(http, t, "get_progress", {"exercise": "Back Squat"})["lift"]
    assert lift["sessions"] == 0 and lift["estimated_max_over_time"] == [] and lift["goals"] == []
    assert lift["best_set"] is None and lift["current_estimated_max_kg"] is None
    log = ok(http, t, "get_log", {"days": 14})
    assert log["totals"] == {"sessions": 0, "weigh_ins": 0, "measurements": 0, "food_entries": 0,
                             "cardio_entries": 0}
    assert all(not d["sessions"] and d["weigh_in"] is None and not d["measurements"] and not d["cardio"]
               for d in log["days"])
    found = ok(http, t, "find_food", {"query": "roti"})  # B saved a roti; A must not see it
    dal = ok(http, t, "find_food", {"query": "dal"})
    assert found["saved"] == [] and found["recent"] == [] and dal["saved"] == [] and dal["recent"] == []
    for text in (str(who), str(summary), str(lift), str(log), str(found), str(dal)):
        assert "Bob" not in text and "180" not in text and "91.5" not in text and "97.25" not in text
        assert "321" not in text and "123" not in text
    # And B still sees all of it.
    mine = ok(http, token(B), "get_log", {"days": 14})
    assert {k: mine["totals"][k] for k in ("sessions", "weigh_ins", "measurements", "food_entries",
                                           "cardio_entries")} == {"sessions": 1, "weigh_ins": 1, "measurements": 1,
                                                                  "food_entries": 1, "cardio_entries": 1}
    assert [f["name"] for f in ok(http, token(B), "find_food", {"query": "roti"})["saved"]] == ["Bob's roti"]


@pytest.mark.parametrize("function, argument, kind, others", ID_CALLS,
                         ids=[f"{f}-{k}" for f, _, k, _ in ID_CALLS])
def test_bs_id_gets_a_the_same_not_found_as_a_missing_id(http, bob, function, argument, kind, others):
    allow_everything(http, A)
    t = token(A)
    ids = [bob[kind]] + ([bob["deleted_measurement"]] if function == "restore_entry" and kind == "measurement" else [])
    for theirs_id in ids:
        before = snapshot(bob["account"], kind, theirs_id)
        if function == "log_food":
            theirs = failed(http, t, function, food_call(argument, theirs_id))
            missing = failed(http, t, function, food_call(argument, MISSING_ID))
        else:
            theirs = failed(http, t, function, {argument: theirs_id, **others})
            missing = failed(http, t, function, {argument: MISSING_ID, **others})
        assert theirs["error"] == "not_found", (function, kind, theirs)
        assert theirs == missing  # word for word: nothing says the id exists
        told = re.sub(r"Entry \d+: ", "", theirs["message"] + theirs["fix"])  # an entry's place, not an id
        assert not re.search(r"\b" + str(theirs_id) + r"\b", told)
        assert snapshot(bob["account"], kind, theirs_id) == before


@pytest.mark.parametrize("kind", ENTRY_KINDS)
def test_bs_entries_are_not_found_by_as_service_reads_either(http, bob, kind):
    # get_entry is not an MCP function; through MCP, get_log is the read, checked above. This is its service.
    a = allow_everything(http, A)
    assert services.get_entry(a, kind, bob[kind]) == services.get_entry(a, kind, MISSING_ID)
    assert services.get_entry(a, kind, bob[kind])["error"] == "not_found"


def test_a_new_goal_for_a_starts_from_as_numbers_not_bs(http, bob):
    t = token(A)
    result = call(http, t, "set_goal", {"kind": "lift", "exercise": "Back Squat", "target_value": 150,
                                        "target_date": "2099-01-01"})
    assert result["isError"] is True and result["structuredContent"]["error"] == "missing_field"
    assert "start_value" in result["structuredContent"]["fix"]  # A has no squat to start from; B's is not used


# ---------------------------------------------------------------- stage 5: goals of every kind, and the trends

@pytest.fixture
def bobs_goals(http, bob) -> dict:
    """B adds a goal of each other kind to his lift goal, and drops a second lift goal. Returns the ids by kind."""
    t = token(B)
    today = date.fromisoformat(ok(http, t, "whoami")["today"])
    by = (today + timedelta(days=90)).isoformat()
    ok(http, t, "log_cardio", {"exercise": "Outdoor Run", "minutes": 31.5, "distance_km": 5})
    ids = {"lift": bob["goal"]}
    for kind, arguments in (("bodyweight", {"target_value": 85.5}),
                            ("measurement", {"measurement_kind": "waist", "target_value": 90.5}),
                            ("cardio_best", {"exercise": "Outdoor Run", "distance_km": 5, "target_value": 27.5})):
        ids[kind] = ok(http, t, "set_goal", {"kind": kind, "target_date": by, "text": f"Bob's {kind}",
                                             **arguments})["id"]
    dropped = ok(http, t, "set_goal", {"kind": "lift", "exercise": "Bench Press", "target_value": 140,
                                       "target_date": by, "start_value": 110})["id"]
    ok(http, t, "set_goal", {"goal_id": dropped, "status": "dropped"})
    return {**ids, "dropped": dropped}


def test_a_sees_none_of_bs_goals_or_trends(http, bob, bobs_goals):
    t = token(A)
    goals = ok(http, t, "get_goals", {"include_closed": True})
    assert goals["goals"] == [] and goals["closed"] == []
    trends = ok(http, t, "get_trends")
    assert trends["bodyweight"] is None and trends["calories"] is None and trends["maintenance"] is None
    assert trends["sessions"]["this_week"] == 0 and trends["cardio"]["minutes_this_week"] == 0
    assert ok(http, t, "get_progress")["goals"] == []
    for text in (str(goals), str(trends)):
        assert "Bob" not in text and "91.5" not in text and "97.25" not in text and "85.5" not in text
        assert "90.5" not in text and "27.5" not in text and "31.5" not in text
    # And B still sees all of his.
    mine = ok(http, token(B), "get_goals", {"include_closed": True})
    assert sorted(g["kind"] for g in mine["goals"]) == ["bodyweight", "cardio_best", "lift", "measurement"]
    assert [g["goal_id"] for g in mine["closed"]] == [bobs_goals["dropped"]]
    assert ok(http, token(B), "get_trends")["bodyweight"]["now"] == 91.5


@pytest.mark.parametrize("kind", ["bodyweight", "measurement", "cardio_best", "dropped"])
def test_bs_goal_of_any_kind_gets_a_the_same_not_found(http, bob, bobs_goals, kind):
    allow_everything(http, A)
    t = token(A)
    before = snapshot(bob["account"], "goal", bobs_goals[kind])
    changes = {"status": "open"} if kind == "dropped" else {"status": "dropped", "target_value": 80, "text": "A's edit"}
    theirs = failed(http, t, "set_goal", {"goal_id": bobs_goals[kind], **changes})
    missing = failed(http, t, "set_goal", {"goal_id": MISSING_ID, **changes})
    assert theirs["error"] == "not_found" and theirs == missing
    assert snapshot(bob["account"], "goal", bobs_goals[kind]) == before

# ---------------------------------------------------------------- import pass B: an import through MCP

def test_as_import_of_bs_own_entries_on_bs_days_writes_as_and_leaves_bs_alone(http, bob):
    """A sends, through MCP, the very entries B logged, on B's days. None of B's rows is already there for A or
    takes A's day: every row is A's to write. B's rows, his deleted measurement too, are unchanged."""
    t = token(A)
    ok(http, t, "update_settings", {"time_zone": "UTC"})  # B has none set: his today is the UTC date
    today = date.fromisoformat(ok(http, t, "whoami")["today"])
    on, two_back = today.isoformat(), (today - timedelta(days=2)).isoformat()
    rows = [{"kind": "bodyweight", "date": on, "weight": 91.5},
            {"kind": "measurement", "date": on, "measurement_kind": "waist", "value": 97.25},
            {"kind": "measurement", "date": on, "measurement_kind": "hips", "value": 103.75},  # B deleted his
            {"kind": "lift_session", "date": two_back, "notes": "Bob's squat",
             "sets": [{"exercise": "Back Squat", "weight": 120, "reps": 5, "times": 3}]},
            {"kind": "food_entry", "date": on, "entries": [{"description": "Bob's dal", "kcal": 321, "meal": "lunch"}]},
            {"kind": "cardio_entry", "date": on, "exercise": "Treadmill Walk", "minutes": 47, "notes": "Bob's walk"}]
    kinds = (*ENTRY_KINDS, "deleted_measurement")
    before = {k: snapshot(bob["account"], "measurement" if k == "deleted_measurement" else k, bob[k]) for k in kinds}
    totals = ok(http, token(B), "get_log", {"days": 14})["totals"]
    for dry in (True, False):
        r = ok(http, t, "import_entries", {"rows": rows, "dry_run": dry, "weight_unit": "kg", "length_unit": "cm"})
        assert r["row_outcomes"] == {"written": 6, "already_there": 0, "day_taken": 0, "failed": 0}, dry
        assert r["day_taken"] == [] and r["imported_last_24h"] == 0
    assert {k: snapshot(bob["account"], "measurement" if k == "deleted_measurement" else k, bob[k])
            for k in kinds} == before
    assert ok(http, token(B), "get_log", {"days": 14})["totals"] == totals
    mine = ok(http, t, "get_log", {"days": 14})["totals"]
    assert mine == {"sessions": 1, "weigh_ins": 1, "measurements": 2, "food_entries": 1, "cardio_entries": 1}
