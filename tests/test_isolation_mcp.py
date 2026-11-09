"""The cross-user test through MCP: two tokens, two people. A sees none of B's data, and an id of B's
gets A the same "not found" as an id that does not exist, with B's row unchanged.

The guard: every function with an id-shaped argument (`id`, or a name ending `_id`) must have a row in
ID_ARGUMENTS, so a new function that takes an id cannot skip this test.
"""

from datetime import date, timedelta

import pytest

from app import db
from app.models import Goal
from app.services.common import row_dict

from .conftest import claims, sign
from .test_mcp import call, failed, ok, squat, tool_list

A, B = "user_01ALICE", "user_01BOB"
MISSING_ID = 2_000_000_000

# function -> (the id argument, the other arguments a real call would send)
ID_ARGUMENTS = {
    "set_goal": ("goal_id", {"status": "dropped", "target_value": 300, "text": "A's edit"}),
}


def token(subject: str) -> str:
    """A token for this person. It names them, so no userinfo lookup is needed."""
    return sign(claims(sub=subject, email=f"{subject}@example.com", name=subject))


@pytest.fixture
def bob(http) -> dict:
    """B logs a lift session, a weigh-in and a lift goal. Returns the ids, by the argument that takes them."""
    t = token(B)
    today = date.fromisoformat(ok(http, t, "whoami")["today"])
    ok(http, t, "log_lifts", {"sets": squat(120), "notes": "Bob's squat", "date": (today - timedelta(days=2)).isoformat()})
    ok(http, t, "log_bodyweight", {"kg": 91.5})
    goal = ok(http, t, "set_goal", {"kind": "lift", "exercise": "Back Squat", "target_value": 180,
                                    "target_date": (today + timedelta(days=90)).isoformat(), "text": "Bob's goal"})
    return {"goal_id": goal["id"]}


def goal_row(goal_id: int) -> dict:
    with db.session_scope() as session:
        return row_dict(session.get(Goal, goal_id))


def test_every_function_that_takes_an_id_is_in_the_table(http):
    taking_ids = {t["name"]: [p for p in t["inputSchema"]["properties"] if p == "id" or p.endswith("_id")]
                  for t in tool_list(http, sign())}
    taking_ids = {name: props for name, props in taking_ids.items() if props}
    assert taking_ids == {name: [argument] for name, (argument, _) in ID_ARGUMENTS.items()}


def test_a_sees_none_of_bs_data(http, bob):
    t = token(A)
    who = ok(http, t, "whoami")
    assert who["user_id"] == A and B not in str(who)
    summary = ok(http, t, "get_progress")
    assert summary["lifts"] == [] and summary["goals"] == [] and summary["note"]
    lift = ok(http, t, "get_progress", {"exercise": "Back Squat"})["lift"]
    assert lift["sessions"] == 0 and lift["estimated_max_over_time"] == [] and lift["goals"] == []
    assert lift["best_set"] is None and lift["current_estimated_max_kg"] is None
    for text in (str(who), str(summary), str(lift)):
        assert "Bob" not in text and "180" not in text and "91.5" not in text
    # And B still sees all of it.
    mine = ok(http, token(B), "get_progress")
    assert [row["exercise"] for row in mine["lifts"]] == ["Back Squat"] and len(mine["goals"]) == 1


@pytest.mark.parametrize("function", sorted(ID_ARGUMENTS))
def test_bs_id_gets_a_the_same_not_found_as_a_missing_id(http, bob, function):
    argument, others = ID_ARGUMENTS[function]
    t = token(A)
    before = goal_row(bob[argument])
    theirs = failed(http, t, function, {argument: bob[argument], **others})
    missing = failed(http, t, function, {argument: MISSING_ID, **others})
    assert theirs["error"] == "not_found"
    assert theirs == missing  # word for word: nothing says the id exists
    assert str(bob[argument]) not in theirs["message"] + theirs["fix"]
    assert goal_row(bob[argument]) == before


def test_a_new_goal_for_a_starts_from_as_numbers_not_bs(http, bob):
    t = token(A)
    result = call(http, t, "set_goal", {"kind": "lift", "exercise": "Back Squat", "target_value": 150,
                                        "target_date": "2099-01-01"})
    assert result["isError"] is True and result["structuredContent"]["error"] == "missing_field"
    assert "start_value" in result["structuredContent"]["fix"]  # A has no squat to start from; B's is not used
