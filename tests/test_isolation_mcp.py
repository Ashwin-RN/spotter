"""The cross-user test through MCP: two tokens, two people. A sees none of B's data, and an id of B's
gets A the same "not found" as an id that does not exist, with B's row unchanged.

The guard: every function with an id-shaped argument (`id`, or a name ending `_id`) must have a row in
ID_CALLS, so a new function that takes an id cannot skip this test. A is allowed everything
(log_delete), so a refusal is never the switch's: it can only be the owner check.
"""

from datetime import date, timedelta

import pytest

from app import db, services
from app.models import Goal, User
from app.services.common import row_dict

from .conftest import claims, sign
from .test_mcp import call, failed, ok, squat, tool_list

A, B = "user_01ALICE", "user_01BOB"
MISSING_ID = 2_000_000_000
ENTRY_KINDS = ("lift_session", "bodyweight", "measurement")
EDITS = {"lift_session": {"notes": "A's edit", "sets": squat(10)}, "bodyweight": {"kg": 70},
         "measurement": {"value": 70}}

# (function, its id argument, the kind of B's row it is pointed at, the other arguments a real call sends)
ID_CALLS = [
    ("set_goal", "goal_id", "goal", {"status": "dropped", "target_value": 300, "text": "A's edit"}),
    *[("edit_entry", "entry_id", kind, {"kind": kind, **EDITS[kind]}) for kind in ENTRY_KINDS],
    *[("delete_entry", "entry_id", kind, {"kind": kind}) for kind in ENTRY_KINDS],
    *[("restore_entry", "entry_id", kind, {"kind": kind}) for kind in ENTRY_KINDS],
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
    return {"account": account, "goal": goal["id"], "lift_session": session["id"], "bodyweight": weigh_in["id"],
            "measurement": measurement["id"], "deleted_measurement": gone["id"]}


def snapshot(account: int, kind: str, entry_id: int) -> dict:
    """B's row as it is, deleted or not, through B's own account."""
    if kind == "goal":
        with db.session_scope() as session:
            return row_dict(session.get(Goal, entry_id))
    found = services.get_entry(account, kind, entry_id)
    if found.get("error") == "not_found":  # a deleted row: read it directly
        model = services.entry_kinds()[kind]
        with db.session_scope() as session:
            return row_dict(session.get(model, entry_id))
    return found["entry"]


def test_every_function_that_takes_an_id_is_in_the_table(http):
    taking_ids = {t["name"]: [p for p in t["inputSchema"]["properties"] if p == "id" or p.endswith("_id")]
                  for t in tool_list(http, sign())}
    taking_ids = {name: props for name, props in taking_ids.items() if props}
    assert taking_ids == {function: [argument] for function, argument, _, _ in ID_CALLS}


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
    assert log["totals"] == {"sessions": 0, "weigh_ins": 0, "measurements": 0}
    assert all(not d["sessions"] and d["weigh_in"] is None and not d["measurements"] for d in log["days"])
    for text in (str(who), str(summary), str(lift), str(log)):
        assert "Bob" not in text and "180" not in text and "91.5" not in text and "97.25" not in text
    # And B still sees all of it.
    mine = ok(http, token(B), "get_log", {"days": 14})
    assert mine["totals"] == {"sessions": 1, "weigh_ins": 1, "measurements": 1}


@pytest.mark.parametrize("function, argument, kind, others", ID_CALLS,
                         ids=[f"{f}-{k}" for f, _, k, _ in ID_CALLS])
def test_bs_id_gets_a_the_same_not_found_as_a_missing_id(http, bob, function, argument, kind, others):
    allow_everything(http, A)
    t = token(A)
    ids = [bob[kind]] + ([bob["deleted_measurement"]] if function == "restore_entry" and kind == "measurement" else [])
    for theirs_id in ids:
        before = snapshot(bob["account"], kind, theirs_id)
        theirs = failed(http, t, function, {argument: theirs_id, **others})
        missing = failed(http, t, function, {argument: MISSING_ID, **others})
        assert theirs["error"] == "not_found", (function, kind, theirs)
        assert theirs == missing  # word for word: nothing says the id exists
        assert str(theirs_id) not in theirs["message"] + theirs["fix"]
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
