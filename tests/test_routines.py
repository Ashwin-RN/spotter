"""Routines (docs/contracts/routines.md): a workout saved by name, read back with the weight to log for each lift,
logged through log_lifts, deleted and restored as any entry, and shown on the Routines page with Delete and Undo.

The exit check line by line, then what the lead's points P2 to P8 name. Calls go through the services, and through the
real HTTP path (TestClient) where the wrapper, the call log, the switch or a write's key is the point, as in
tests/test_switch.py and tests/test_gym_logging.py. The clock is the test's own (db.utcnow replaced, as
tests/test_edit_history.py does), so every date, saved_on and updated_at is set here, never read from the real clock.
The eval cases are judged against a person seeded through LocalServer.new_person, as tests/test_evals.py does, on
the real clock its placeholders read.
"""

import csv
import hashlib
import json
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import event, inspect, select, update

from app import db, links, mcp_server, rules, services, web
from app.calllog import KNOWN_NAMES, TABLES
from app.db import make_engine
from app.mcp_schemas import GetRoutinesResult, RoutineLiftIn, SaveRoutineResult
from app.models import CallLog, LiftSession, Routine, User, user_tables
from app.services import common, entries, routines
from evals import judge
from evals.run import TABLES as RUN_TABLES, person_rows

from . import text_rules as tr
from .conftest import TEST_DIR, alembic_config, lock_sql, sign
from .test_calllog import rows
from .test_evals import _lift_case, _lift_server, call as recorded
from .test_feedback import compact
from .test_inputs import without_inputs
from .test_idempotency import K, keys, writer  # noqa: F401 - writer is a fixture
from .test_mcp import call, failed, ok, set_switch, tool_list
from .test_page_rules import Seen, built_findings, follow, frame_findings, page_rule_findings, titled_card_findings
from .test_pages_text import entered_notes, entered_text, names_of, page_findings, step_4_findings
from .test_trace import OWNER, sign_for
from .test_web import client, location, only_user, post, seam, session_of, sign_in, web_client  # noqa: F401

START = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)  # a Tuesday; the person has no zone, so today is its date
X = "×"
PUSH = [{"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 60},
        {"exercise": "Overhead Press", "sets": 3, "reps": 10},
        {"exercise": "Dip", "sets": 3, "reps": 10}]
NEW_SUMMARY = f"Saved Push day: Bench Press 3 {X} 8 at 60 kg, Overhead Press 3 {X} 10 and Dip 3 {X} 10."
NOT_FOUND = {"error": "not_found", "message": "No routine with that id.",
             "fix": "Check the id and the kind: use the id that save_routine returned, or one from get_routines."}
TOO_MANY = {"error": "too_many_routines", "message": "This account holds 20 routines, the most it keeps.",
            "fix": "Delete one with delete_entry, or save under a name already used to replace that routine."}
HOW = ("weight_kg is the heaviest set of the lift's latest session, unless the routine was saved on a later day than "
       "that session, with a weight of its own. Quote these numbers; do not work out your own.")  # R18
PREVIEW_NOTE = ("Nothing was deleted. Show the person this entry and ask. Send the same kind and id with this confirm "
                "only when they say yes.")
MISMATCH_NOTE = ("That confirm does not match this entry as it stands, so nothing was deleted. Show the person the "
                 "entry and ask again.")
SAVE_DESCRIPTION = (
    "Saves a workout the person does often under its name, such as Push day: each lift with its number of sets and "
    "its reps, and a weight only when the person gave one. Send only what the person said: never add a lift, a set or "
    "a weight of your own. Saving the same name again replaces the routine. To save a session already logged, read "
    "it with get_log and send each lift with its number of sets, its reps and its heaviest weight.")
GET_DESCRIPTION = (
    "The person's saved routines, each lift with its sets, its reps and the weight to log. Call it when the person "
    "names a routine, such as log my push day. To log one, send its sets with log_lifts, one item a set, with each "
    "change the person names, and leave the date out for today. Send each weight_kg as given, in weight_kg, whatever "
    "the person's units. Where weight_kg is null, ask the person, and send the answer in the field of its unit. Say "
    "in your reply which sets were logged.")  # units.md
KEY_WORDS = mcp_server.KEY_DESCRIPTION
LIFT_IN = {"exercise": "The lift, exactly as list_exercises gives it.", "sets": "How many sets of it, 1 to 10.",
           "reps": "Reps in each set, a whole number from 1 to 30.",
           "weight_kg": ("The weight in kg, 0 to 500, only when the person gave one. Leave it out otherwise: "
                         "get_routines then gives the weight of the lift's latest session. Send weight_kg or "
                         "weight_lb, never both."),
           "weight_lb": ("The weight in lb, 0 to 1,102.31, only when the person gave one in pounds, or with no unit "
                         "and units.weight is lb. Leave it out otherwise.")}  # units.md R10
NAME_IN = "The routine's name, as the person says it, such as Push day. 1 to 64 characters."
LIFTS_IN = "Each lift, in the order done: 1 to 20 lifts, and at most 60 sets in all."
ID_WORDS = "The routine's id, for delete_entry and restore_entry with kind routine."
LINKS_WORDS = "The Routines page: the person's routines."
# Every function as published at ec48cdb, the join's base, before this work: the SHA-256 of the tool as compact JSON
# with sorted keys, read from the published list for the owner (it holds everyone's twenty and the owner's three).
# Built at 3fe468b, the same but for whoami, which first day moved (setup).
BEFORE = {
    "whoami": "2afb942c1c4d04691599987f4df754cfc34e7ff424b1df1f43bbd6a813cb4ca9",
    "list_exercises": "5c8162608e9e239073cc020cb7278ceace0ae3481a94944e28f8a736216c2e27",
    "log_lifts": "9e95e0445afc3a57390021fcc57a0c9774146be42ff37d4447c51b1ac995fe18",
    "log_bodyweight": "f6d2e5b9ad195b9256152099a072142fdd926c7e176e35fc561e036ff66654b7",
    "set_goal": "266f24d8d1467cc4080c620386262b4f5108ce61c002c9975f830028eabc759f",
    "get_progress": "e01a7b022248ac56d33d1d9a812e1c3b582187af14ee63bb7a6789bd0cd359e0",
    "update_settings": "8c4f498c508107ef3610e2e735d5e839647aedd30dabefcaed87471c313355c1",
    "get_log": "0f13ed202bfb439905e0c81120ab93ecf50031da8a720dac9e790916dd104169",
    "log_measurement": "7456c5853a541df971d7b8ab8f382d835e82a6b1ca35c8ece0c60c985db12647",
    "edit_entry": "3fbe15511d1b775980d1d89c5e24414a52b5cf4ccf1def94133f0b7d29673e7e",
    "restore_entry": "387046ab52a01f54010b144d3e326d87ac8b36f75617c8fda446f9fb9e332fe4",
    "delete_entry": "9f44f254de2d734c647c15921809df43fd2310a3ee9a914e307ad545c47a0143",
    "find_food": "cf6cce7ada8d743d741108f84359140a7c0571d85ef43b0964b70cfbdc101189",
    "log_food": "6ffc9850d580f531c6cf3bd6d961b186936aa00b5bf3b9af19198a3a8e7acd56",
    "save_food": "0a8e5b4526af8bc103af661c711ad6719ab8588579dc64ceb4b6b4ed6cb218cc",
    "log_cardio": "22bfeaeb1a13afa6438c365ba0d9b699a9eb1b4adf09cc38ae92ea5f4914f764",
    "get_trends": "ffb7403c8b2029888d1c57e431059922e77c4884e21934b0632d0e68c063aa3e",
    "get_goals": "ac0e1ea4076d1f304ef604f676d024b743ff07ffdd01615c84a1aa5702a70cbe",
    "import_entries": "8e1bce9821e8547d62c9768c5b05b9a7c686c7437e2f34e80d8275a4a82d5ffd",
    "send_feedback": "63bf4722f94c7863dcafce688f8864224c46cbf30868d971e5347475a0686184",
    "owner_calls": "79ec889297b107ba7f25781a2ace0a058b712960510d994d36788485da57d787",
    "owner_feedback": "242b0d08f0ca2b4bb696ec818f327ede1d2b2b71b6959dba0e687cc9b628a5f7",
    "owner_delete_feedback": "e6d44e98f7ac2ce9945b277c0c592c7bde7ba6060b6628990f3a1bc16764909b",
}


# ---------------------------------------------------------------- the clock and the helpers

class Clock:
    """The test's own db.utcnow: START until a test moves it."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def at(self, days: int = 0, **step) -> None:
        self.now = START + timedelta(days=days, **step)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    held = Clock()
    monkeypatch.setattr(db, "utcnow", held)
    return held


@pytest.fixture
def user(make_user, clock):
    return make_user()


def day(offset: int = 0) -> str:
    return (START.date() + timedelta(days=offset)).isoformat()


def save(person_id: int, name: str = "Push day", lifts=None, by: str = "claude") -> dict:
    return services.save_routine(person_id, {"name": name, "lifts": PUSH if lifts is None else lifts}, by)


def lift(exercise: str = "Bench Press", sets: int = 3, reps: int = 8, weight=None) -> dict:
    return {"exercise": exercise, "sets": sets, "reps": reps, **({} if weight is None else {"weight_kg": weight})}


def log(person_id: int, *sets_, **data) -> dict:
    done = services.log_lifts(person_id, {"sets": [{"exercise": e, "weight_kg": w, "reps": r} for e, w, r in sets_],
                                          **data}, "claude")
    assert "error" not in done, done
    return done


def table() -> list[dict]:
    """Every routine row, every column, in id order."""
    with db.new_session() as s:
        return [{c.name: getattr(r, c.key) for c in Routine.__table__.columns}
                for r in s.scalars(select(Routine).order_by(Routine.id))]


def read(person_id: int) -> dict:
    found = services.get_routines(person_id)
    assert "error" not in found, found
    GetRoutinesResult.model_validate(found)
    return found


def weights(person_id: int, n: int = 0) -> list[tuple]:
    """(exercise, weight_kg, weight_from, last_date) of each lift of the person's nth routine, as get_routines gives."""
    return [(x["exercise"], x["weight_kg"], x["weight_from"], x["last_date"])
            for x in read(person_id)["routines"][n]["lifts"]]


def many(person_id: int, n: int, start: int = 1) -> list[int]:
    return [save(person_id, f"Day {i}", [lift()])["id"] for i in range(start, start + n)]


def delete(person_id: int, routine_id: int) -> dict:
    return services.delete_entry(person_id, "routine", routine_id)


def statements_of(fn) -> tuple[object, list[tuple[str, tuple]]]:
    seen: list[tuple[str, tuple]] = []

    def keep(conn, cursor, statement, parameters, context, executemany):
        values = parameters.values() if isinstance(parameters, dict) else parameters or ()  # whatever the paramstyle
        seen.append((" ".join(statement.split()), tuple(values)))

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", keep)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", keep)
    return result, seen


PARAM = r"(?:\?|%\(\w+\)s(?:::\w+)?)"  # a placeholder: ? on SQLite, %(name)s on psycopg, a cast after it
ON_ROUTINE = re.compile(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+routine\b")


# ---------------------------------------------------------------- the weight to log (decision 8, R8, R9)

def test_with_no_session_the_routine_s_weight_counts_and_a_null_weight_gives_null_and_null(user):
    save(user.id)
    assert weights(user.id) == [("Bench Press", 60.0, "routine", None), ("Overhead Press", None, None, None),
                                ("Dip", None, None, None)]


def test_a_session_dated_before_saved_on_loses_to_the_routine_s_weight_and_counts_where_the_routine_has_none(user):
    log(user.id, ("Bench Press", 70, 8), ("Overhead Press", 40, 10), date=day(-1))
    save(user.id)
    assert weights(user.id)[:2] == [("Bench Press", 60.0, "routine", day(-1)),
                                    ("Overhead Press", 40.0, "last_session", day(-1))]


def test_a_session_on_the_day_of_the_save_wins_logged_before_the_save_or_after_it(user):
    log(user.id, ("Bench Press", 65, 8))
    save(user.id)  # "save today's workout" agrees with itself
    assert weights(user.id)[0] == ("Bench Press", 65.0, "last_session", day())
    log(user.id, ("Bench Press", 67.5, 8))
    assert weights(user.id)[0] == ("Bench Press", 67.5, "last_session", day())


def test_a_routine_saved_with_a_weight_later_the_same_day_as_a_session_loses_to_that_session(user, clock):
    """R18: the rule compares days. A session at 07:00 and the routine saved at 17:00 with a weight of its own: the
    session's weight, from last_session, as the published words now say."""
    clock.at(0, hours=-5)
    log(user.id, ("Bench Press", 50, 8))
    clock.at(0, hours=5)
    save(user.id, lifts=[lift(weight=60)])
    assert weights(user.id) == [("Bench Press", 50.0, "last_session", day())]
    assert "saved on a later day than that session" in read(user.id)["how"]


def test_a_session_after_the_save_wins_and_next_week_s_read_gives_it_by_itself(user, clock):
    clock.at(-7)
    save(user.id)  # Bench Press at 60
    clock.at(0)
    log(user.id, ("Bench Press", 62.5, 8), ("Bench Press", 62.5, 8))
    assert weights(user.id)[0] == ("Bench Press", 62.5, "last_session", day())
    clock.at(7)  # the next week, nothing said
    assert weights(user.id)[0] == ("Bench Press", 62.5, "last_session", day())


def test_the_latest_session_deleted_the_one_before_it_counts_or_none(user):
    first = log(user.id, ("Bench Press", 50, 8), date=day(-3))
    last = log(user.id, ("Bench Press", 55, 8), date=day(-1))
    save(user.id, lifts=[lift()])
    assert weights(user.id) == [("Bench Press", 55.0, "last_session", day(-1))]
    services.delete_entry(user.id, "lift_session", last["id"])
    assert weights(user.id) == [("Bench Press", 50.0, "last_session", day(-3))]
    services.delete_entry(user.id, "lift_session", first["id"])
    assert weights(user.id) == [("Bench Press", None, None, None)]


def test_with_two_live_sessions_on_one_date_the_later_by_id_counts_not_the_heavier(user):
    log(user.id, ("Bench Press", 70, 8))
    log(user.id, ("Bench Press", 60, 8), new_session=True)
    save(user.id, lifts=[lift()])
    assert weights(user.id) == [("Bench Press", 60.0, "last_session", day())]


def test_a_session_dated_after_today_is_the_latest_in_the_one_order(user, clock):
    log(user.id, ("Bench Press", 80, 8), date=day(-1))
    clock.at(2)
    ahead = log(user.id, ("Bench Press", 90, 8))  # dated two days on, as after a change of time zone
    clock.at(0)
    assert ahead["session"]["date"] == day(2)
    save(user.id)  # Bench Press at 60, saved today: the session's real date is after it
    assert weights(user.id)[0] == ("Bench Press", 90.0, "last_session", day(2))
    log(user.id, ("Bench Press", 85, 8))  # today's, a higher id on the same effective date: now the latest
    assert weights(user.id)[0] == ("Bench Press", 85.0, "last_session", day())


def test_a_lift_logged_at_0_kg_gives_0_from_its_session(user):
    save(user.id, "Core", [lift("Dip", 3, 10)])
    log(user.id, ("Dip", 0, 10))
    assert weights(user.id) == [("Dip", 0.0, "last_session", day())]


def test_the_heaviest_of_a_session_s_sets_counts(user):
    log(user.id, ("Bench Press", 60, 8), ("Bench Press", 65, 6), ("Bench Press", 62.5, 8), ("Dip", 10, 10))
    save(user.id, lifts=[lift()])
    assert weights(user.id) == [("Bench Press", 65.0, "last_session", day())]


def test_another_person_s_sessions_never_count(make_user, clock):
    mine, theirs = make_user(), make_user()
    log(theirs.id, ("Bench Press", 100, 8), ("Dip", 20, 10))
    save(mine.id)
    assert weights(mine.id) == [("Bench Press", 60.0, "routine", None), ("Overhead Press", None, None, None),
                                ("Dip", None, None, None)]
    assert read(theirs.id)["routines"] == []


def test_last_top_is_the_latest_session_s_date_and_heaviest_set_and_get_progress_does_not_copy_it(user):
    log(user.id, ("Back Squat", 100, 5), ("Back Squat", 110, 3), date=day(-2))
    log(user.id, ("Back Squat", 90, 5))
    with db.new_session() as s:
        history = services.lifts.history(s, user.id, START.date())
    assert history["last_top"] == {"Back Squat": {"date": START.date(), "weight_kg": 90.0}}
    assert "last_top" not in json.dumps(services.get_progress(user.id, "Back Squat"))


def test_get_routines_answers_in_name_order_with_its_words_and_with_none_says_so(user):
    empty = read(user.id)
    assert empty == {"routines": [], "how": HOW, "summary": "No routines saved yet.",
                     "note": "No routines saved yet. save_routine saves one.",
                     "links": {"routines": links.page("routines")}}
    save(user.id, "Push day")
    assert read(user.id)["summary"] == "1 routine: Push day." and "note" not in read(user.id)
    pull = save(user.id, "pull day", [lift("Barbell Row")])
    found = read(user.id)
    assert found["summary"] == "2 routines: pull day and Push day."  # by the name's key, then id
    assert [r["name"] for r in found["routines"]] == ["pull day", "Push day"]
    assert list(found["routines"][0]) == ["id", "name", "saved_on", "lifts", "words"]  # units.md decision 11
    assert found["routines"][0]["id"] == pull["id"] and found["routines"][0]["saved_on"] == day()
    assert services.get_routines(10 ** 9) == common.no_account()


# ---------------------------------------------------------------- the save (decisions 2, 3, 5, R6, R7)

def test_each_summary_of_a_save_is_the_contract_s_and_every_answer_passes_its_model(user):
    new = save(user.id)
    assert (new["kind"], new["created"], new["summary"]) == ("routine", True, NEW_SUMMARY)
    assert new["links"] == {"routines": links.page("routines")}
    held = save(user.id, lifts=[lift(), lift("Overhead Press", 3, 10)])
    assert (held["created"], held["summary"]) == (
        False, f"Replaced Push day: Bench Press 3 {X} 8 and Overhead Press 3 {X} 10.")
    assert delete(user.id, new["id"])["deleted"] is True
    back = save(user.id, lifts=[lift()])
    assert (back["created"], back["summary"]) == (False, f"Brought back and replaced Push day: Bench Press 3 {X} 8.")
    same = save(user.id, lifts=[lift()])
    assert (same["created"], same["summary"]) == (False, "Push day is already saved as given: nothing changed.")
    for answer in (new, held, back, same):
        assert answer["id"] == new["id"] and list(answer) == ["kind", "id", "created", "routine", "summary", "links"]
        SaveRoutineResult.model_validate(answer)
    assert new["routine"] == {"id": new["id"], "name": "Push day", "saved_on": day(), "updated_at": START.isoformat(),
                              "lifts": [{**lift(weight=60.0)}, {**lift("Overhead Press", 3, 10), "weight_kg": None},
                                        {**lift("Dip", 3, 10), "weight_kg": None}]}


def test_the_same_name_in_another_case_or_with_other_spaces_replaces_the_one_row(user):
    first = save(user.id)
    again = save(user.id, "  PUSH   Day ", [lift()])
    assert again["id"] == first["id"] and again["summary"] == f"Replaced PUSH Day: Bench Press 3 {X} 8."  # R19
    [row] = table()
    assert (row["name"], row["name_key"]) == ("PUSH Day", "push day")


def test_a_name_s_newlines_tabs_and_runs_of_spaces_are_folded_in_what_is_stored_and_said(user):
    """R19: the name is stored and summarised with its spaces folded, so a summary stays one line. One row."""
    first = save(user.id, "Push\nday", [lift()])
    assert first["summary"] == f"Saved Push day: Bench Press 3 {X} 8." and first["routine"]["name"] == "Push day"
    again = save(user.id, "Push \t  day", [lift()])
    assert again["id"] == first["id"] and again["summary"] == "Push day is already saved as given: nothing changed."
    [row] = table()
    assert (row["name"], row["name_key"]) == ("Push day", "push day")
    assert [r["name"] for r in read(user.id)["routines"]] == ["Push day"]
    # The length is checked on the name as sent, stripped: 64 characters with a run of spaces in it pass, 65 do not.
    assert save(user.id, "a" * 30 + " " * 4 + "b" * 30, [lift()])["routine"]["name"] == "a" * 30 + " " + "b" * 30
    assert save(user.id, "a" * 30 + " " * 5 + "b" * 30, [lift()])["error"] == "invalid_field"


def test_a_name_held_saved_again_in_another_case_with_the_same_lifts_writes_the_new_name(user, clock):
    """R6: the no-change check compares the name as sent too, so a change of case alone is a replace."""
    first = save(user.id, "Push day", [lift("Dip", 3, 10)])
    clock.at(0, minutes=5)
    again = save(user.id, "PUSH DAY", [lift("Dip", 3, 10)])
    assert (again["id"], again["created"], again["summary"]) == (first["id"], False, f"Replaced PUSH DAY: Dip 3 {X} 10.")
    [row] = table()
    assert (row["name"], row["updated_at"]) == ("PUSH DAY", START + timedelta(minutes=5))


def test_a_deleted_routine_saved_again_exactly_as_it_was_comes_back_live(user, clock):
    """R6: a deleted one saved again with its stored name and lifts is brought back, never "already saved"."""
    first = save(user.id)
    delete(user.id, first["id"])
    clock.at(0, minutes=5)
    back = save(user.id)
    assert (back["id"], back["created"]) == (first["id"], False)
    assert back["summary"] == (f"Brought back and replaced Push day: Bench Press 3 {X} 8 at 60 kg, Overhead Press 3 {X} "
                               f"10 and Dip 3 {X} 10.")
    [row] = table()
    assert row["deleted_at"] is None and len(read(user.id)["routines"]) == 1


def test_routines_come_back_by_name_key_then_id_whatever_order_they_were_made_in(user):
    """R8's order: by name_key, then id. Made in an order that neither id nor newest id gives, and "arms" comes
    before "Back" by its key, where ordering by the name itself would put "Back" first."""
    for name in ("Chest", "arms", "Back", "Day 1"):
        save(user.id, name, [lift()])
    names = [r["name"] for r in read(user.id)["routines"]]
    assert names == ["arms", "Back", "Chest", "Day 1"]
    assert read(user.id)["summary"] == "4 routines: arms, Back, Chest and Day 1."


def test_a_deleted_name_comes_back_live_and_replaced_with_its_id(user, clock):
    first = save(user.id)
    delete(user.id, first["id"])
    clock.at(1)
    back = save(user.id, "push day", [lift("Dip", 2, 12)])
    [row] = table()
    assert back["id"] == row["id"] == first["id"] and row["deleted_at"] is None and row["saved_on"] == date(2026, 11, 11)
    assert json.loads(row["lifts"]) == [{"exercise": "Dip", "sets": 2, "reps": 12, "weight_kg": None}]


def test_the_same_lifts_again_write_nothing_updated_at_and_saved_on_too(user, clock):
    first = save(user.id)
    before = table()
    clock.at(3, hours=2)  # a write now would move both
    same, seen = statements_of(lambda: save(user.id, lifts=[{**x} for x in PUSH]))
    assert same["summary"] == "Push day is already saved as given: nothing changed." and table() == before
    assert not [sql for sql, _ in seen if ON_ROUTINE.search(sql) and not sql.startswith("SELECT")]
    assert same["routine"]["updated_at"] == first["routine"]["updated_at"] == START.isoformat()
    lighter = save(user.id, lifts=[lift(weight=60.0), lift("Overhead Press", 3, 10), lift("Dip", 3, 10)])
    assert lighter["summary"].startswith("Push day is already saved") and table() == before  # 60 and 60.0 are one


def test_a_replace_moves_saved_on_and_updated_at(user, clock):
    clock.at(-3)
    save(user.id)
    clock.at(0)
    save(user.id, lifts=[lift()])
    [row] = table()
    assert (row["saved_on"], row["updated_at"], row["created_at"]) == (START.date(), START, START - timedelta(days=3))


def test_the_stored_lifts_are_compact_json_in_order_each_with_its_four_fields(user):
    save(user.id, lifts=[lift("bench   PRESS", 3, 8, 60), lift("Dip", 3, 10, "0"), lift("Overhead Press", 3, 10)])
    [row] = table()
    assert row["lifts"] == ('[{"exercise":"Bench Press","sets":3,"reps":8,"weight_kg":60.0},'
                            '{"exercise":"Dip","sets":3,"reps":10,"weight_kg":0.0},'
                            '{"exercise":"Overhead Press","sets":3,"reps":10,"weight_kg":null}]')


def test_a_save_with_only_lifts_sets_and_reps_and_one_with_weights_and_0_kg_reads_at_0_kg(user):
    bare = save(user.id, "Pull day", [lift("Barbell Row", 4, 10), lift("Pull-Up", 3, 8)])
    assert bare["summary"] == f"Saved Pull day: Barbell Row 4 {X} 10 and Pull-Up 3 {X} 8."
    weighed = save(user.id, "Core", [lift("Dip", 3, 10, 0), lift("Bench Press", 3, 8, 62.5)])
    assert weighed["summary"] == f"Saved Core: Dip 3 {X} 10 at 0 kg and Bench Press 3 {X} 8 at 62.5 kg."
    assert [x["weight_kg"] for x in weighed["routine"]["lifts"]] == [0.0, 62.5]


def test_a_name_sent_in_another_case_stores_the_list_s_name_and_the_same_lift_may_come_twice(user):
    done = save(user.id, "Heavy", [lift(" bench press ", 1, 3, 100), lift("BENCH PRESS", 3, 8, 80)])
    assert [x["exercise"] for x in done["routine"]["lifts"]] == ["Bench Press", "Bench Press"]


# ---------------------------------------------------------------- the refusals (R5)

def one(message: str, fix: str, code: str = "invalid_field") -> tuple:
    return code, message, fix


GOOD = lift()
REFUSALS = {
    "no name": ({"lifts": [GOOD]}, one("name is missing.", "Give name, 1 to 64 characters.", "missing_field")),
    "a blank name": ({"name": "  ", "lifts": [GOOD]},
                     one("name is missing.", "Give name, 1 to 64 characters.", "missing_field")),
    "65 characters of name": ({"name": "x" * 65, "lifts": [GOOD]},
                              one("name is 65 characters; the most is 64.", "Shorten name to 64 characters or fewer.")),
    "a name that is no text": ({"name": 7, "lifts": [GOOD]}, one("name must be text, not 7.", "Give name as text.")),
    "another field": ({"name": "Push day", "lifts": [GOOD], "colour": "red"},
                      one("'colour' is not a field of a routine.", "Use: name, lifts.")),
    "no lifts": ({"name": "Push day"}, one("A routine holds 1 to 20 lifts, not 0.", "Send 1 to 20 lifts.")),
    "lifts not a list": ({"name": "Push day", "lifts": "bench"},
                         one("A routine holds 1 to 20 lifts, not 0.", "Send 1 to 20 lifts.")),
    "23 lifts": ({"name": "Push day", "lifts": [lift(sets=1)] * 23},
                 one("A routine holds 1 to 20 lifts, not 23.", "Send 1 to 20 lifts.")),
    "a field a lift does not have": ({"name": "Push day", "lifts": [GOOD, {**GOOD, "x": 1}]},
                                     one("Lift 2: 'x' is not a field of a lift.",
                                         "Use: exercise, sets, reps, weight_kg or weight_lb.")),  # units.md R10
    "no exercise": ({"name": "Push day", "lifts": [GOOD, {"sets": 3, "reps": 8}]},
                    one("Lift 2: exercise is missing.", "Give an exercise name on lift 2.", "missing_field")),
    "a name not on the list": ({"name": "Push day", "lifts": [GOOD, lift("Zorbinator")]},
                               one("Lift 2: 'Zorbinator' is not on the exercise list.",
                                   "No close name found. Use a name exactly as list_exercises gives it.",
                                   "unknown_exercise")),
    "sets 11": ({"name": "Push day", "lifts": [GOOD, lift(sets=11)]},
                one("Lift 2: sets 11 is outside 1 to 10.", "Give sets on lift 2 from 1 to 10.")),
    "sets 0": ({"name": "Push day", "lifts": [GOOD, lift(sets=0)]},
               one("Lift 2: sets 0 is outside 1 to 10.", "Give sets on lift 2 from 1 to 10.")),
    "sets missing": ({"name": "Push day", "lifts": [GOOD, {"exercise": "Dip", "reps": 8}]},
                     one("Lift 2: sets is missing.", "Give sets on lift 2.", "missing_field")),
    "sets of words": ({"name": "Push day", "lifts": [GOOD, lift(sets="three")]},
                      one("Lift 2: sets 'three' is not a number.", "Give sets on lift 2 as a number.", "not_a_number")),
    "sets of 2.5": ({"name": "Push day", "lifts": [GOOD, lift(sets=2.5)]},
                    one("Lift 2: sets must be a whole number.", "Give sets on lift 2 as a whole number.",
                        "not_a_whole_number")),
    "reps 31": ({"name": "Push day", "lifts": [GOOD, lift(reps=31)]},
                one("Lift 2: reps 31 is outside 1 to 30.", "Give reps on lift 2 from 1 to 30.", "reps_out_of_range")),
    "reps missing": ({"name": "Push day", "lifts": [GOOD, {"exercise": "Dip", "sets": 3}]},
                     one("Lift 2: reps is missing.", "Give reps on lift 2.", "missing_field")),
    "a weight of 501": ({"name": "Push day", "lifts": [GOOD, lift(weight=501)]},
                        one("Lift 2: weight 501 kg is outside 0 to 500 kg.",
                            "Give weight_kg on lift 2 from 0 to 500, or leave it out.", "weight_out_of_range")),
    "a weight below 0": ({"name": "Push day", "lifts": [GOOD, lift(weight=-2.5)]},
                         one("Lift 2: weight -2.5 kg is outside 0 to 500 kg.",
                             "Give weight_kg on lift 2 from 0 to 500, or leave it out.", "weight_out_of_range")),
    "a weight of words": ({"name": "Push day", "lifts": [GOOD, lift(weight="heavy")]},
                          one("Lift 2: weight_kg 'heavy' is not a number.", "Give weight_kg on lift 2 as a number.",
                              "not_a_number")),
    "64 sets": ({"name": "Push day", "lifts": [lift(sets=10)] * 6 + [lift(sets=4)]},
                one("Push day would hold 64 sets. A routine holds at most 60, so that it fits in one session.",
                    "Send fewer sets, or split it into two routines.", "sets_count_out_of_range")),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_each_check_alone_refuses_in_its_words_and_writes_nothing(user, case):
    data, (code, message, fix) = REFUSALS[case]
    save(user.id, "Pull day", [lift("Barbell Row")])
    before = table()
    refused = services.save_routine(user.id, data, "claude")
    assert (refused["error"], refused["message"], refused["fix"]) == (code, message, fix)
    assert len(refused["errors"]) == 1 and table() == before


def test_several_problems_come_back_at_once_in_order_and_the_total_names_this_routine_when_the_name_was_refused(user):
    data = {"lifts": [lift(sets=10)] * 6 + [lift(reps=31, weight=501), lift("Zorbinator", sets=4)], "x": 1}
    refused = services.save_routine(user.id, data, "claude")
    assert [e["error"] for e in refused["errors"]] == [
        "missing_field", "invalid_field", "reps_out_of_range", "weight_out_of_range", "unknown_exercise",
        "sets_count_out_of_range"]
    assert refused["errors"][-1]["message"] == ("This routine would hold 67 sets. A routine holds at most 60, so that "
                                                "it fits in one session.")
    assert refused["message"].startswith("6 problems. name is missing. 'x' is not a field of a routine. Lift 7: reps 31")
    assert table() == []
    some = services.save_routine(user.id, {"name": "Push day", "lifts": [lift(sets=11)] + [lift(sets=10)] * 6}, "claude")
    assert [e["error"] for e in some["errors"]] == ["invalid_field"]  # a lift's sets refused: no total is said


@pytest.mark.parametrize("passes, refused", [
    ([lift(sets=10)] * 6, [lift(sets=10)] * 6 + [lift(sets=1)]),         # 60 sets, then 61
    ([lift(sets=1)] * 20, [lift(sets=1)] * 21),                          # 20 lifts, then 21
    ([lift(weight=500)], [lift(weight=501)]),                            # 500 kg, then 501
], ids=["60 and 61 sets", "20 and 21 lifts", "500 and 501 kg"])
def test_each_limit_takes_its_highest_and_refuses_one_past_it(user, passes, refused):
    assert "error" in save(user.id, "Past it", refused) and table() == []
    assert save(user.id, "At it", passes)["created"] is True


def test_64_characters_of_name_pass_and_65_do_not(user):
    assert save(user.id, "x" * 65)["error"] == "invalid_field" and table() == []
    assert save(user.id, "x" * 64)["created"] is True and table()[0]["name"] == "x" * 64


def test_no_data_and_a_wrong_caller_are_refused_as_every_write_s_are(user):
    assert services.save_routine(user.id, "bench", "claude") == common.check_data("bench")[1]
    assert services.save_routine(user.id, {"name": "Push day", "lifts": PUSH}, "import") == common.check_by("import")
    assert services.save_routine(10 ** 9, {"name": "Push day", "lifts": PUSH}, "claude") == common.no_account()


# ---------------------------------------------------------------- the cap (decision 4, R6, R12)

def test_at_20_live_a_21st_name_is_refused_and_a_replace_of_a_live_one_is_not(user):
    ids = many(user.id, 20)
    before = table()
    assert save(user.id, "Day 21", [lift()]) == {**TOO_MANY, "error": "too_many_routines"} and table() == before
    replaced = save(user.id, "day 7", [lift("Dip", 3, 10)])
    assert replaced["id"] == ids[6] and replaced["summary"].startswith("Replaced day 7")


def test_a_deleted_one_brought_back_as_the_21st_is_refused_and_a_deleted_one_does_not_count(user):
    ids = many(user.id, 20)
    delete(user.id, ids[0])
    assert save(user.id, "Day 21", [lift()])["created"] is True  # 19 live: the deleted one does not count
    before = table()
    assert save(user.id, "Day 1", [lift("Dip")]) == TOO_MANY and table() == before


def test_a_restore_as_the_21st_is_refused_through_the_service_and_mcp_and_nothing_changes(writer):
    http, token, account = writer
    ids = many(account, 20)
    delete(account, ids[0])
    many(account, 1, start=21)
    before = table()
    assert services.restore_entry(account, "routine", ids[0]) == TOO_MANY
    assert failed(http, token, "restore_entry", {"kind": "routine", "entry_id": ids[0]}) == TOO_MANY
    assert table() == before
    delete(account, ids[1])
    assert ok(http, token, "restore_entry", {"kind": "routine", "entry_id": ids[0]})["restored"] is True


def test_two_saves_of_new_names_at_once_at_19_live_one_lands_and_one_is_refused(user, monkeypatch):
    many(user.id, 19)
    counted = routines.live_count

    def slow(*args):  # after the count: without the lock both would read 19
        found = counted(*args)
        time.sleep(0.2)
        return found

    monkeypatch.setattr(routines, "live_count", slow)
    barrier = threading.Barrier(2)
    results, failures = [], []

    def send(name):
        try:
            barrier.wait(timeout=5)
            results.append(save(user.id, name, [lift()]))
        except Exception as exc:  # reported below, so a crash in a thread fails the test
            failures.append(exc)

    threads = [threading.Thread(target=send, args=(name,)) for name in ("Day A", "Day B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not failures, failures
    assert sorted("error" in r for r in results) == [False, True]
    assert [r for r in results if "error" in r] == [TOO_MANY]
    assert sum(r["deleted_at"] is None for r in table()) == 20


# ---------------------------------------------------------------- delete, restore, undo, edit (decisions 6, 11, R12)

def test_a_delete_through_mcp_takes_two_calls_and_a_restore_brings_it_back(writer):
    http, token, account = writer
    made = save(account)
    before = table()
    first = ok(http, token, "delete_entry", {"kind": "routine", "entry_id": made["id"]})
    assert table() == before
    assert (first["kind"], first["id"], first["deleted"], first["dry_run"], first["note"]) == (
        "routine", made["id"], False, True, PREVIEW_NOTE)
    assert first["entry"] == {**made["routine"], "deleted_at": None}
    second = ok(http, token, "delete_entry", {"kind": "routine", "entry_id": made["id"], "confirm": first["confirm"]})
    assert (second["deleted"], second["dry_run"]) == (True, False) and table()[0]["deleted_at"] is not None
    assert read(account)["routines"] == []
    back = ok(http, token, "restore_entry", {"kind": "routine", "entry_id": made["id"]})
    assert back["restored"] is True and back["entry"]["deleted_at"] is None and len(read(account)["routines"]) == 1
    assert [(r.function, r.outcome, r.tried) for r in rows()[-3:]] == [
        ("delete_entry", "preview", "entry_id kind=routine"), ("delete_entry", "written", "confirm entry_id kind=routine"),
        ("restore_entry", "written", "entry_id kind=routine")]


def test_a_confirm_from_before_a_replace_deletes_nothing_and_answers_the_preview_again(writer, clock):
    http, token, account = writer
    made = save(account)
    code = ok(http, token, "delete_entry", {"kind": "routine", "entry_id": made["id"]})["confirm"]
    clock.at(0, seconds=5)
    save(account, lifts=[lift()])  # updated_at moves, so the code does
    late = ok(http, token, "delete_entry", {"kind": "routine", "entry_id": made["id"], "confirm": code})
    assert (late["deleted"], late["dry_run"], late["note"]) == (False, True, MISMATCH_NOTE) and late["confirm"] != code
    assert table()[0]["deleted_at"] is None


def test_with_deleting_off_a_routine_s_delete_is_not_allowed_and_nothing_is_deleted(writer):
    http, token, account = writer
    made = save(account)
    set_switch(account, "log")
    refused = failed(http, token, "delete_entry", {"kind": "routine", "entry_id": made["id"]})
    assert refused["error"] == "not_allowed" and refused["message"] == "This account lets Claude read and log, not delete."
    assert table()[0]["deleted_at"] is None
    assert ok(http, token, "save_routine", {"name": "Pull day", "lifts": [lift("Barbell Row")]})["created"] is True


def test_another_person_s_a_deleted_and_a_missing_id_are_the_one_not_found_on_every_path(writer, make_user):
    http, token, account = writer
    theirs = save(make_user().id)["id"]
    gone = save(account, "Gone", [lift()])["id"]
    delete(account, gone)
    before = table()
    for entry_id in (theirs, gone, 10 ** 9):
        assert failed(http, token, "delete_entry", {"kind": "routine", "entry_id": entry_id}) == NOT_FOUND
        assert services.delete_entry(account, "routine", entry_id, "claude") == NOT_FOUND
        assert services.get_entry(account, "routine", entry_id) == NOT_FOUND
        assert services.restore_entry(account, "routine", entry_id, "claude", undo_edit=True) == NOT_FOUND
    for entry_id in (theirs, 10 ** 9):
        assert failed(http, token, "restore_entry", {"kind": "routine", "entry_id": entry_id}) == NOT_FOUND
    assert table() == before
    assert save(account, "Push day", [lift()])["created"] is True  # their name is not this person's name
    assert [r["user_id"] for r in table()].count(account) == 2


def test_undo_edit_is_nothing_to_undo_edit_entry_is_unknown_kind_and_a_live_one_is_not_deleted(user):
    made = save(user.id)
    assert services.restore_entry(user.id, "routine", made["id"], "claude", undo_edit=True) == {
        "error": "nothing_to_undo", "message": "A routine has no earlier values kept.",
        "fix": "Save it again with save_routine, as it was."}
    assert services.edit_entry(user.id, "routine", made["id"], {"name": "Pull day"}, "claude") == {
        "error": "unknown_kind", "message": "A routine is not edited here.",
        "fix": "Use save_routine with the same name and the whole routine."}
    assert services.restore_entry(user.id, "routine", made["id"], "claude") == {
        "error": "not_deleted", "message": "This entry is not deleted, so there is nothing to restore.",
        "fix": "Nothing to do: it is live. get_routines shows it."}
    found = services.get_entry(user.id, "routine", made["id"])
    assert found["entry"] == {**made["routine"], "deleted_at": None} and found["links"] == {
        "routines": links.page("routines")}


def test_a_routine_is_no_entry_kind_so_the_log_the_import_and_the_trends_never_see_it(user):
    save(user.id)
    assert "routine" not in services.entry_kinds() and "routine" in entries._deletable()
    assert services.get_log(user.id)["days"][0]["sessions"] == []
    sent = {"rows": [{"kind": "routine", "date": day(), "name": "Push day"},
                     {"kind": "zorbinator", "date": day()}], "dry_run": False}
    services.update_settings(user.id, {"time_zone": "UTC"}, "web")
    result = services.import_entries(user.id, sent, "claude")
    errors = [f["errors"][0] if "errors" in f else f for f in result["failed"]]
    assert [(e["error"], e["message"]) for e in errors] == [
        ("unknown_kind", "Row 1: 'routine' is not a kind of row."),
        ("unknown_kind", "Row 2: 'zorbinator' is not a kind of row.")]
    assert errors[0]["fix"] == errors[1]["fix"] and "routine" not in errors[0]["fix"]
    assert len(table()) == 1


def test_a_routine_does_not_end_a_new_account(user):
    """Decision 12: _empty is unchanged, so an account with only a routine is still empty and whoami still hands the
    person the hi there page."""
    save(user.id)
    assert services.account_empty(user.id) == {"empty": True}
    assert "new_account_note" in services.whoami(user.id)


# ---------------------------------------------------------------- the key and the write limit (decision 5, R11)

def test_the_same_key_and_call_twice_is_one_save_and_the_first_answer_and_its_second_row_reads_repeat(writer, clock):
    http, token, _ = writer
    sent = {"name": "Push day", "lifts": PUSH, "key": K}
    first = call(http, token, "save_routine", sent)
    clock.at(0, seconds=30)
    again = call(http, token, "save_routine", sent)
    assert again == first and first["structuredContent"]["summary"] == NEW_SUMMARY and len(table()) == 1
    assert table()[0]["updated_at"] == START
    assert [r.outcome for r in rows()[-2:]] == ["written", "repeat"]
    assert rows()[-1].tried == "key lifts[3]{exercise,reps,sets,weight_kg} name"


def test_a_key_on_a_refused_save_is_free(writer):
    http, token, _ = writer
    refused = failed(http, token, "save_routine", {"name": "Push day", "lifts": [lift(reps=31)], "key": K})
    assert refused["error"] == "reps_out_of_range" and keys() == []
    assert ok(http, token, "save_routine", {"name": "Push day", "lifts": [lift()], "key": K})["created"] is True


def test_the_write_limit_counts_a_save_and_a_save_is_refused_past_it(writer, monkeypatch):
    http, token, _ = writer
    monkeypatch.setattr(common, "WRITE_LIMIT", 1)
    assert ok(http, token, "save_routine", {"name": "Push day", "lifts": [lift()]})["created"] is True
    assert failed(http, token, "log_bodyweight", {"kg": 80})["error"] == "too_many_writes"  # the save counted
    assert failed(http, token, "save_routine", {"name": "Pull day", "lifts": [lift()]})["error"] == "too_many_writes"
    assert len(table()) == 1 and "save_routine" in common.WRITE_FUNCTIONS
    assert common.already_done("save_routine")["fix"] == ("Read it back with get_routines. Send a new key for a new "
                                                          "write.")


# ---------------------------------------------------------------- through MCP (decisions 7, 16, 18, 19, R10, R13)

def test_the_published_words_arguments_results_and_annotations_are_the_contract_s(http):
    tools = {t["name"]: t for t in tool_list(http, sign())}
    saving, getting = tools["save_routine"], tools["get_routines"]
    assert (saving["title"], saving["description"]) == ("Save a routine", SAVE_DESCRIPTION)
    assert (getting["title"], getting["description"]) == ("Get routines", GET_DESCRIPTION)
    schema = saving["inputSchema"]
    assert list(schema["properties"]) == ["name", "lifts", "key"] and schema["required"] == ["name", "lifts"]
    assert schema["properties"]["name"]["description"] == NAME_IN
    assert schema["properties"]["lifts"]["description"] == LIFTS_IN
    assert schema["properties"]["key"]["description"] == KEY_WORDS
    item = schema["$defs"]["RoutineLiftIn"]
    assert {k: v["description"] for k, v in item["properties"].items()} == LIFT_IN
    assert item["required"] == ["exercise", "sets", "reps"]
    assert getting["inputSchema"]["properties"] == {}
    assert saving["annotations"] == {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
                                     "openWorldHint": False}  # SETTING, as save_food
    assert getting["annotations"] == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                                      "openWorldHint": False}
    assert (mcp_server.NEEDS["save_routine"], mcp_server.NEEDS["get_routines"]) == ("log", "read")
    assert "get_routines" in mcp_server.READ_FUNCTIONS and "save_routine" not in mcp_server.READ_FUNCTIONS
    out = saving["outputSchema"]
    assert {k: v["description"] for k, v in out["properties"].items()} == {
        "kind": "routine.", "id": ID_WORDS,
        "created": "True when the name was new. False when a routine of that name was replaced or brought back.",
        "routine": "The routine as saved.", "summary": "What this call did, in one line, ready to quote.",
        "links": "Pages to offer the person. Put links last in a reply.",
        "units_note": ("Present only when this call sent a weight in lb or a distance or speed in miles, and the "
                       "person has not chosen their units. Ask once, as it says.")}  # units.md R7
    saved = out["$defs"]["SavedRoutine"]["properties"]
    assert {k: v["description"] for k, v in saved.items()} == {
        "id": ID_WORDS, "name": NAME_IN, "lifts": LIFTS_IN,
        "saved_on": "The person's date when the routine was last saved.", "updated_at": "When it last changed, UTC."}
    lift_out = out["$defs"]["SavedRoutineLift"]["properties"]
    assert {k: v["description"] for k, v in lift_out.items()} == {
        **{k: LIFT_IN[k] for k in ("exercise", "sets", "reps")},
        "weight_kg": "The weight the person gave, in kg, or null when none was given."}
    assert out["$defs"]["RoutinesLinks"]["properties"]["routines"]["description"] == LINKS_WORDS
    got = getting["outputSchema"]
    assert {k: v["description"] for k, v in got["properties"].items()} == {
        "routines": "Every live routine, by name.", "how": HOW,
        "summary": "The routines by name, in one line, ready to quote.", "note": "Present when no routine is saved.",
        "links": "Pages to offer the person. Put links last in a reply."}
    assert {k: v["description"] for k, v in got["$defs"]["RoutineToLog"]["properties"].items()} == {
        "id": ID_WORDS, "name": NAME_IN, "saved_on": "The person's date when the routine was last saved.",
        "lifts": LIFTS_IN, "words": ("The routine's lifts in words, each with the weight to log, in the person's units, "
                                     "ready to quote.")}  # units.md decision 11
    assert {k: v["description"] for k, v in got["$defs"]["LiftToLog"]["properties"].items()} == {
        **{k: LIFT_IN[k] for k in ("exercise", "sets", "reps")},
        "weight_kg": ("The weight to log, in kg: the heaviest set of this lift's latest session, unless the routine "
                      "was saved on a later day than that session, with a weight of its own. Send it in weight_kg "
                      "as it is, never converted. Null when there is neither: ask the person."),  # units.md
        "weight_from": "Where weight_kg came from: last_session, routine, or null.",
        "last_date": "The date of this lift's latest session, or null when it was never logged."}


def test_the_kind_and_id_words_and_delete_entry_s_first_sentence_are_the_contract_s(http):
    tools = {t["name"]: t for t in tool_list(http, sign())}
    entry_id = ("The entry's id, from log_lifts, log_bodyweight, log_measurement, log_food, log_cardio or get_log, "
                "with the kind it came with. A saved food's id comes from save_food or find_food, a routine's from "
                "save_routine or get_routines.")
    deleting, restoring = tools["delete_entry"]["inputSchema"], tools["restore_entry"]["inputSchema"]
    assert deleting["properties"]["kind"]["description"] == (
        "lift_session, bodyweight, measurement, food_entry or cardio_entry. Or saved_food or routine.")
    assert restoring["properties"]["kind"]["description"] == (
        "lift_session, bodyweight, measurement, food_entry or cardio_entry. With undo_edit, saved_food too. Or "
        "routine, to bring back a deleted one.")
    assert deleting["properties"]["entry_id"]["description"] == restoring["properties"]["entry_id"]["description"] == \
        entry_id
    assert tools["delete_entry"]["description"].startswith(
        "Deletes one of the person's entries, saved foods or routines, in two calls. With no confirm, nothing is "
        "deleted:")
    edit = tools["edit_entry"]["inputSchema"]["properties"]
    assert edit["entry_id"]["description"] == mcp_server.ENTRY_ID_DESCRIPTION and "routine" not in json.dumps(edit)


def test_only_the_two_new_ones_delete_entry_and_restore_entry_differ_in_the_published_list(make_client):
    client = make_client(owner_user_id=OWNER)
    tools = tool_list(client, sign_for(OWNER))
    names = [t["name"] for t in tools]
    assert names == [*list(BEFORE)[:20], "save_routine", "get_routines", "what_is_logged", "get_data",
                     *list(BEFORE)[20:]]  # data-on-request.md: its two after get_routines
    # inputs.md then moved every one: its changes are taken out here (tests/test_inputs.py diffs the two).
    hashes = {t["name"]: hashlib.sha256(compact(without_inputs(t)).encode()).hexdigest() for t in tools}
    # Routines moved delete_entry, restore_entry and whoami; units then moved its thirteen (units.md), of these.
    units = {"whoami", "log_lifts", "log_bodyweight", "log_cardio", "edit_entry", "set_goal", "update_settings",
             "get_progress", "get_goals", "get_log", "delete_entry"}
    assert {n for n in BEFORE if hashes[n] != BEFORE[n]} == {"delete_entry", "restore_entry", "whoami"} | units
    assert hashes["restore_entry"] != BEFORE["restore_entry"] and "restore_entry" not in units
    everyone = [t["name"] for t in tool_list(make_client(), sign())]
    assert everyone == [n for n in names if n not in mcp_server.OWNER_FUNCTIONS] and len(everyone) == web.FUNCTION_COUNT


def test_the_instructions_do_not_change_4011_characters():  # data-on-request.md: DATA_RULE joined after
    assert len(mcp_server.INSTRUCTIONS) == 4011
    assert "routine" not in mcp_server.INSTRUCTIONS


def test_a_save_and_a_read_through_mcp_pass_their_models_and_the_trace_writes_lifts_and_nothing_else_new(writer):
    http, token, account = writer
    none = ok(http, token, "get_routines")
    assert none["routines"] == [] and none["note"].startswith("No routines saved yet.")
    sent = {"name": "Push day", "lifts": [lift(weight=60), lift("Dip", 3, 10)]}
    saved = ok(http, token, "save_routine", sent)
    assert saved["summary"] == f"Saved Push day: Bench Press 3 {X} 8 at 60 kg and Dip 3 {X} 10."
    some = ok(http, token, "get_routines")
    GetRoutinesResult.model_validate(some)
    assert [x["weight_kg"] for x in some["routines"][0]["lifts"]] == [60.0, None]
    made = rows()[-3:]
    assert [(r.function, r.outcome, r.tried) for r in made] == [
        ("get_routines", "read", ""), ("save_routine", "written", "lifts[2]{exercise,reps,sets,weight_kg} name"),
        ("get_routines", "read", "")]
    assert "lifts" in KNOWN_NAMES and set(RoutineLiftIn.model_fields) <= KNOWN_NAMES
    assert TABLES == ("lift_session", "bodyweight", "measurement", "food_entry", "cardio_entry", "saved_food",
                      "routine", "goal")
    marked = failed(http, token, "save_routine", {"name": "Pull day", "lifts": [{**lift(), "colour": "red"}]})
    # A field the model does not name is refused before the service, in R5's words (inputs.md D1 and D6: was
    # dropped), and counted, never named, in tried.
    assert marked["message"] == "Lift 1: 'colour' is not a field of a lift."
    assert rows()[-1].tried == "lifts[1]{exercise,reps,sets} name other[1]" and rows()[-1].outcome == "refused"


def test_get_routines_is_counted_by_the_read_limit(writer):
    http, token, account = writer
    with db.session_scope() as s:
        s.add_all(CallLog(user_id=account, function="get_routines", started_at=db.utcnow(), ms=1, ok=True,
                          result_bytes=1) for _ in range(common.READS_LIMIT))
    assert failed(http, token, "get_routines")["error"] == "too_many_reads"


# ---------------------------------------------------------------- export and account delete (decision 14, R14)

def test_the_export_holds_routines_csv_after_saved_foods_csv_with_the_json_as_stored_and_deleted_rows(user, clock):
    kept = save(user.id)
    gone = save(user.id, "Gone", [lift("Dip", 2, 12)])
    delete(user.id, gone["id"])
    out = services.export_data(user.id)
    names = list(out["files"])
    assert names.index("routines.csv") == names.index("saved_foods.csv") + 1 and out["rows"]["routines.csv"] == 2
    lines = out["files"]["routines.csv"].lstrip("﻿").splitlines()
    assert lines[0] == "id,name,lifts,saved_on,created_at,updated_at,deleted_at" and "name_key" not in lines[0]
    stored = {r["id"]: r for r in table()}
    parsed = list(csv.DictReader(lines))
    assert [int(r["id"]) for r in parsed] == [kept["id"], gone["id"]]
    assert [r["lifts"] for r in parsed] == [stored[kept["id"]]["lifts"], stored[gone["id"]]["lifts"]]
    assert parsed[0]["deleted_at"] == "" and parsed[1]["deleted_at"] == START.isoformat()
    readme = out["files"]["README.txt"]
    assert ("  routines.csv        each routine: lifts holds its lifts as JSON, each with exercise, sets, reps\n"
            "                      and weight_kg (null when none was given). saved_on is the date it was last\n"
            "                      saved\n") in readme
    assert readme.index("saved_foods.csv") < readme.index("routines.csv") < readme.index("food_entries.csv")


def test_account_delete_counts_routines_and_takes_them_and_its_page_says_so(client, make_user):
    sign_in(client)
    me = only_user().id
    other = make_user().id
    save(me)
    delete(me, save(me, "Gone", [lift()])["id"])
    save(other)
    page = client.get("/settings/delete").text
    assert re.search(r'<li class="entry-line"><span>Saved foods</span><strong>0</strong></li>\s*'
                     r'<li class="entry-line"><span>Routines</span><strong>2</strong></li>', page)
    with db.session_scope() as s:
        s.expunge(s.get(User, me))
    result = services.delete_account(me)
    assert result["rows"]["routine"] == 2 and [r["user_id"] for r in table()] == [other]


# ---------------------------------------------------------------- the page (decision 13, R15, P6)

def routine_rows(markup: str) -> list[tuple[str, str]]:
    return [(name, words) for name, words in re.findall(
        r'<tr><td class="lead">([^<]*)</td><td data-label="Lifts">([^<]*)</td>', markup)]


NB = " "


def test_signed_out_the_page_and_its_posts_go_to_sign_in(client):
    assert location(client.get("/routines")).endswith("/sign-in")
    for path in ("/routines/1/delete", "/routines/1/restore"):
        assert client.post(path).status_code == 403  # no session, so no token: as every post


def test_signed_in_the_page_shows_each_routine_with_the_weights_get_routines_gives(client):
    sign_in(client)
    me = only_user().id
    empty = client.get("/routines")
    assert empty.status_code == 200 and ("No routines yet. Tell your assistant a workout you do often, such as your "
                                         "push day, and ask it to save it.") in empty.text
    assert "<table" not in empty.text
    save(me, lifts=[lift(weight=60), lift("Overhead Press", 3, 10, 40), lift("Dip", 3, 10)])
    log(me, ("Bench Press", 62.5, 8))
    page = client.get("/routines").text
    assert routine_rows(page) == [("Push day", f"Bench Press 3 {X} 8 at 62.5{NB}kg, Overhead Press 3 {X} 10 at "
                                               f"40{NB}kg and Dip 3 {X} 10")]
    assert [x["weight_kg"] for x in read(me)["routines"][0]["lifts"]] == [62.5, 40.0, None]
    assert ("Each weight is the one your assistant would log today: your latest session's, or the routine's own when "
            "it was saved on a later day.") in page
    assert ('Workouts you do often, saved by name. Say "log my push day" and your assistant logs its sets. Routines '
            'are saved by talking to your assistant.') in page.replace("&#34;", '"')
    assert "<title>Routines · Spotter</title>" in page and "<h1>Routines</h1>" in page
    # No form saves or logs one: the only form in a row is its Delete, and every field on the page is hidden.
    assert 'action="/routines"' not in page and re.findall(r'<form [^>]*action="([^"]+)"', page) == [
        "/sign-out", f"/routines/{read(me)['routines'][0]['id']}/delete", "/settings"]  # the frame's two, and Delete
    assert page.count("<input") == page.count('<input type="hidden"')


def test_the_page_is_behind_the_page_limit(client, monkeypatch):
    monkeypatch.setattr(web, "PAGE_LIMIT", 3)
    sign_in(client)
    assert [client.get("/routines").status_code for _ in range(4)] == [200, 200, 200, 429]


def test_delete_deletes_at_once_and_lands_on_the_strip_whose_undo_restores(client):
    sign_in(client)
    me = only_user().id
    made = save(me)["id"]
    landed = location(post(client, f"/routines/{made}/delete"))
    assert landed.endswith(f"/routines?deleted=routine:{made}") and table()[0]["deleted_at"] is not None
    page = client.get(landed).text
    assert "Routine deleted." in page and routine_rows(page) == []
    token = re.search(rf'action="/routines/{made}/restore" class="inline">\s*'
                      r'<input type="hidden" name="csrf_token" value="([^"]+)">', page).group(1)
    back = client.post(f"/routines/{made}/restore", data={"csrf_token": token})
    assert location(back).endswith("/routines?restored=routine") and table()[0]["deleted_at"] is None
    restored = client.get(location(back)).text
    assert '<p class="ok">Routine restored.</p>' in restored and [n for n, _ in routine_rows(restored)] == ["Push day"]
    deleted_rows = [(r.function, r.caller, r.tried) for r in rows()[-2:]]
    assert deleted_rows == [("delete_entry", "web", "entry_id kind=routine"),
                            ("restore_entry", "web", "entry_id kind=routine")]


def test_the_two_refused_undos_say_so_and_change_nothing(client):
    sign_in(client)
    me = only_user().id
    live = save(me)["id"]
    r = post(client, f"/routines/{live}/restore")
    assert r.status_code == 409 and "Already back" in r.text
    assert "That routine is not deleted, so there was nothing to undo." in r.text
    assert "Go back to Routines to see it." in r.text
    ids = many(me, 19)
    delete(me, ids[0])
    many(me, 1, start=30)
    before = table()
    r = post(client, f"/routines/{ids[0]}/restore")
    assert r.status_code == 409 and "Too many routines" in r.text
    assert "This account holds 20 routines, the most it keeps." in r.text
    assert "Delete one on Routines first, then undo again." in r.text and table() == before


def test_a_post_with_no_token_or_another_person_s_id_changes_nothing_and_a_get_of_a_post_address_is_refused(
        client, make_user):
    sign_in(client)
    me = only_user().id
    mine = save(me)["id"]
    theirs = save(make_user().id, "Secret day", [lift("Dip", 3, 10, 123.45)])["id"]  # a "." is in no page token
    before = table()
    for path in (f"/routines/{mine}/delete", f"/routines/{mine}/restore"):
        assert client.post(path).status_code == 403
        assert client.post(path, data={"csrf_token": "wrong"}).status_code == 403
    for path in (f"/routines/{theirs}/delete", f"/routines/{theirs}/restore", "/routines/abc/delete"):
        r = post(client, path)
        assert r.status_code == 404 and "Secret day" not in r.text and "123.45" not in r.text
    assert table() == before and "Secret day" not in client.get("/routines").text
    for path in (f"/routines/{mine}/delete", f"/routines/{mine}/restore"):  # a GET of a post address: as Foods'
        refused, foods = client.get(path), client.get(path.replace("/routines/", "/foods/"))
        assert (refused.status_code, refused.text) == (foods.status_code, foods.text) and refused.status_code == 404
    assert table() == before


def test_settings_links_to_the_page_and_says_what_each_setting_lets_the_assistant_do_with_routines(client):
    sign_in(client)
    page = client.get("/settings").text
    assert ('<p><a class="action" href="/foods">Saved foods</a></p>\n'
            '  <p><a class="action" href="/routines">Saved routines</a></p>') in page
    assert ("Your assistant can log and import entries, edit and restore them, save foods and routines, and set goals. "
            "It can change your time zone, units and targets, and delete entries, saved foods and routines. Before a "
            "delete it is shown the entry and told to ask you. Nothing is erased: a deleted one can be "
            "restored.") in page  # units.md R16: units joins what it can change
    assert "Everything above except deleting: your assistant cannot delete an entry, a saved food or a routine." in page
    assert links.page("routines").endswith("/routines") and ("/routines", web.routines_page, ["GET"]) in web.PAGES


def routines_renders(client, uid: int) -> list[Seen]:
    """Every Routines render: each mark it has words for, one it ignores, then a delete and its Undo and both refused
    Undos, each followed to where it lands."""
    zone, today = not only_user().time_zone, START.date().isoformat()
    out = []
    ids = [r["id"] for r in read(uid)["routines"]]
    marks = ["", "?restored=routine", "?restored=1", "?saved=1"] + [f"?deleted=routine:{i}" for i in ids[:1]]
    for mark in marks:
        r = client.get("/routines" + mark)
        assert r.status_code == 200, mark
        out.append(Seen("/routines" + mark, r.text, True, zone_missing=zone, today=today))
    if ids:
        landed = [follow(client, post(client, f"/routines/{ids[0]}/delete")),
                  follow(client, post(client, f"/routines/{ids[0]}/restore"))]
        out += [Seen(address, markup, True, zone_missing=zone, today=today) for address, markup in landed]
        refused = post(client, f"/routines/{ids[0]}/restore")
        assert refused.status_code == 409
        out.append(Seen(f"/routines/{ids[0]}/restore", refused.text, True, zone_missing=zone, message=True,
                        calm=False, today=today))
    return out


def _some(uid: int) -> None:
    save(uid, lifts=[lift(weight=60), lift("Overhead Press", 3, 10, 40), lift("Dip", 3, 10, 0)])
    save(uid, "Pull day", [lift("Barbell Row", 4, 10), lift("Pull-Up", 3, 8)])
    log(uid, ("Bench Press", 62.5, 8))


@pytest.mark.parametrize("state", ["none", "some"])
def test_the_page_and_its_landings_keep_every_rule_foods_keeps(client, clock, state):
    sign_in(client)
    uid = only_user().id
    if state == "some":
        _some(uid)
    seen = routines_renders(client, uid)
    entered, names, notes = entered_text(uid), names_of(uid), entered_notes(uid)
    assert state == "none" or {"Push day", "Pull day"} <= set(entered)  # a routine's name is the person's own text
    findings = []
    for s in seen:
        findings += frame_findings(s) + page_rule_findings(s) + titled_card_findings(s)
        if not s.message:
            findings += built_findings(s, uid)
        findings += page_findings(s.address, s.markup, entered, uid, route=None)
        findings += step_4_findings(s.address, s.markup, names, notes, message=s.message, entered=entered)
    assert not findings, "\n".join(findings)
    assert len(seen) == (5 + 3 if state == "some" else 4)


def test_too_many_routines_page_keeps_the_rules_too(client, clock):
    sign_in(client)
    uid = only_user().id
    ids = many(uid, 20)
    delete(uid, ids[0])
    many(uid, 1, start=30)
    r = post(client, f"/routines/{ids[0]}/restore")
    s = Seen(f"/routines/{ids[0]}/restore", r.text, True, zone_missing=True, message=True, calm=False,
             today=START.date().isoformat())
    findings = frame_findings(s) + page_rule_findings(s) + titled_card_findings(s)
    findings += page_findings(s.address, s.markup, [], uid) + step_4_findings(s.address, s.markup, None, [],
                                                                            message=True)
    assert r.status_code == 409 and not findings, "\n".join(findings)


def test_every_new_text_keeps_the_text_rules():
    for text in (SAVE_DESCRIPTION, GET_DESCRIPTION, NAME_IN, LIFTS_IN, ID_WORDS, LINKS_WORDS, HOW, *LIFT_IN.values()):
        assert tr.check(text, tr.CLAUDE) == [], text
    for text in (NEW_SUMMARY, "No routines saved yet. save_routine saves one.", TOO_MANY["message"], TOO_MANY["fix"]):
        assert tr.check(text, tr.SUMMARY) == [], text
    for text in (*web.CLAUDE_CAN_LINES.values(), *web.ROUTINE_STRIPS.values(), "Saved routines", "Routines"):
        assert tr.check(text, tr.PAGE) == [], text


# ---------------------------------------------------------------- every statement names the person (P2, P3)

def test_a_save_locks_first_and_once(user):
    for lifts_ in (PUSH, [lift()], [lift()]):  # new, replaced, unchanged
        result, seen = statements_of(lambda: save(user.id, lifts=lifts_))
        lock = lock_sql(user.id)
        assert "error" not in result and seen[0][0] == lock and sum(sql == lock for sql, _ in seen) == 1


def test_every_statement_on_routine_names_the_person_s_id(client, make_user):
    sign_in(client)
    me = only_user()
    other = make_user()
    theirs = save(other.id)["id"]
    log(other.id, ("Bench Press", 99, 8))
    mine = {}
    actions = {
        "a new save": lambda: mine.setdefault("id", save(me.id)["id"]),
        "a replace": lambda: save(me.id, lifts=[lift()]),
        "the same again": lambda: save(me.id, lifts=[lift()]),
        "a read": lambda: services.get_routines(me.id),
        "a read by id": lambda: services.get_entry(me.id, "routine", mine["id"]),
        "a preview": lambda: services.delete_entry(me.id, "routine", mine["id"], "claude"),
        "a delete": lambda: services.delete_entry(me.id, "routine", mine["id"]),
        "a restore": lambda: services.restore_entry(me.id, "routine", mine["id"], "claude"),
        "the page's delete": lambda: post(client, f"/routines/{mine['id']}/delete"),
        "a save brought back": lambda: save(me.id, lifts=[lift("Dip")]),
        "the page": lambda: client.get("/routines"),
        "the export": lambda: services.export_data(me.id),
        "the delete page": lambda: client.get("/settings/delete"),
        "another's id": lambda: services.delete_entry(me.id, "routine", theirs),
    }
    for what, action in actions.items():
        _, seen = statements_of(action)
        on = [(sql, params) for sql, params in seen if ON_ROUTINE.search(sql)]
        assert on, what
        for sql, params in on:
            if sql.startswith("UPDATE routine"):  # the row written back, found first by the person's id
                assert re.search(rf"WHERE routine\.id = {PARAM}$", sql) and mine["id"] in params, (what, sql)
            elif sql.startswith("INSERT INTO routine"):
                assert "user_id" in sql and me.id in params, (what, sql)
            else:
                assert re.search(rf"routine\.user_id = {PARAM}", sql) and me.id in params, (what, sql, params)
    assert [r["user_id"] for r in table() if r["id"] == theirs] == [other.id]
    assert read(other.id)["routines"][0]["lifts"][0]["weight_kg"] == 99.0


GET_STATEMENTS = 5  # as built: the user, the routines, and lifts.history's sessions, their sets and their exercises


def test_get_routines_runs_a_fixed_number_of_statements_however_many_routines_and_sessions(make_user, clock):
    small, large = make_user(), make_user()
    save(small.id)
    log(small.id, ("Bench Press", 60, 8))
    many(large.id, 20)
    for n in range(30):
        log(large.id, ("Bench Press", 60 + n, 8), ("Dip", 0, 10), date=day(-n))
    counts = {}
    for person in (small, large):
        _, seen = statements_of(lambda: services.get_routines(person.id))
        counts[person.id] = len(seen)
    assert counts[small.id] == counts[large.id] == GET_STATEMENTS
    nobody = make_user()
    _, seen = statements_of(lambda: services.get_routines(nobody.id))
    assert len(seen) == 2  # with no routine, history is not walked


# ---------------------------------------------------------------- the migration (decision 1, R2, R3, P8)

def _columns(engine, table_name: str) -> list[tuple]:
    with engine.connect() as c:
        return [(col["name"], str(col["type"]), col["nullable"]) for col in inspect(c).get_columns(table_name)]


def _schema(engine) -> dict:
    """Every table's columns, indexes, keys and unique constraints, as JSON text: a column's type and an index's
    WHERE are objects, which compare by identity, so each is written as its SQL text."""
    with engine.connect() as c:
        found = inspect(c)
        return {t: json.dumps([found.get_columns(t), found.get_indexes(t), found.get_foreign_keys(t),
                               found.get_unique_constraints(t)], default=str, sort_keys=True)
                for t in found.get_table_names()}


def test_0010_builds_the_table_on_sqlite_and_comes_down_leaving_no_other_change():
    address = f"sqlite:///{(Path(tempfile.mkdtemp(dir=TEST_DIR)) / 'routine.db').as_posix()}"
    cfg = alembic_config(address)
    command.upgrade(cfg, "0009")
    engine = make_engine(address)
    try:
        before = _schema(engine)
        assert "routine" not in before
        command.upgrade(cfg, "0010")
        assert _columns(engine, "routine") == [
            ("id", "INTEGER", False), ("user_id", "INTEGER", False), ("name", "VARCHAR(64)", False),
            ("name_key", "VARCHAR(255)", False), ("lifts", "TEXT", False), ("saved_on", "DATE", False),
            ("created_at", "DATETIME", False), ("updated_at", "DATETIME", False), ("deleted_at", "DATETIME", True)]
        with engine.connect() as c:
            found = inspect(c)
            assert found.get_unique_constraints("routine") == [
                {"name": "uq_routine_user_id_name_key", "column_names": ["user_id", "name_key"]}]
            [key] = found.get_foreign_keys("routine")
            assert (key["name"], key["referred_table"], key["constrained_columns"], key["options"]) == (
                "fk_routine_user_id_user", "user", ["user_id"], {"ondelete": "CASCADE"})
            assert found.get_indexes("routine") == []
        now = "2026-11-10 08:00:00.000000"
        with engine.begin() as c:
            for user in (1, 2):
                c.exec_driver_sql("INSERT INTO user (id, provider, subject, claude_can, add_back_exercise_kcal, "
                                  "created_at, updated_at, last_seen_at) VALUES (?, 'workos', ?, 'log_delete', 0, ?, ?, "
                                  "?)", (user, f"s{user}", now, now, now))
                c.exec_driver_sql("INSERT INTO routine (user_id, name, name_key, lifts, saved_on, created_at, "
                                  "updated_at) VALUES (?, 'Push day', 'push day', '[]', '2026-11-10', ?, ?)",
                                  (user, now, now))
        with engine.begin() as c:
            c.exec_driver_sql("DELETE FROM user WHERE id = 1")
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT user_id FROM routine").scalars().all() == [2]  # the cascade
        command.downgrade(cfg, "0009")
        after = _schema(engine)
        assert "routine" not in after and after == before
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0009"
    finally:
        engine.dispose()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("0010").down_revision == "0009" and script.get_current_head() == "0011"  # units


def test_the_model_is_no_entry_table_and_its_columns_are_in_the_contract_s_order():
    assert [c.name for c in Routine.__table__.columns] == ["id", "user_id", "name", "name_key", "lifts", "saved_on",
                                                           "created_at", "updated_at", "deleted_at"]
    assert "logged_by" not in Routine.__table__.c and "routine" in user_tables()
    assert [c.name for c in Routine.__table__.constraints if c.name and c.name.startswith("uq_")] == [
        "uq_routine_user_id_name_key"]
    assert (rules.ROUTINES_MAX, rules.ROUTINE_LIFTS, rules.ROUTINE_LIFT_SETS, rules.ROUTINE_NAME_MAX) == (
        20, (1, 20), (1, 10), 64)
    assert rules.required_text("x" * 128, "name") == ("x" * 128, None)  # a saved food's name keeps its 128


# ---------------------------------------------------------------- the eval cases (decision 21, R16, P7)

def _saved(user_id: int, sent: dict) -> dict:
    """One save_routine call through the service, as the recorder would hold it."""
    return recorded("save_routine", sent, is_error="error" in services.save_routine(user_id, sent, "claude"))


def _logged(user_id: int, rows_: list[tuple], **extra) -> dict:
    sent = {"sets": [{"exercise": e, "weight_kg": w, "reps": r} for e, w, r in rows_], **extra}
    return recorded("log_lifts", sent, is_error="error" in services.log_lifts(user_id, sent, "claude"))


SAID = {"name": "push day", "lifts": [{"exercise": "Bench Press", "sets": 3, "reps": 8},
                                     {"exercise": "Overhead Press", "sets": 3, "reps": 10},
                                     {"exercise": "Dip", "sets": 3, "reps": 10}]}


def test_the_three_cases_load_last_and_the_tables_and_seed_are_known():
    """Setup's quiet case, then these three, close the file."""
    cases = judge.load_cases()
    # units' six next, then inputs.md's two (its decision 19), then data-on-request.md's eight (its decision 20)
    assert [c["id"] for c in cases[-19:-16]] == ["routine_saved", "routine_logged", "routine_changed"]
    assert cases[-20]["id"] == "setup_stays_quiet"
    assert all(judge.case_problems(c) == [] for c in cases[-19:-16])
    assert "save_routine" in judge.SEED_SERVICES and "routine" in judge.DB_TABLES and "routine" in RUN_TABLES
    assert judge.seed_service("save_routine") is services.save_routine
    assert "lift_count" in judge._rows_text([{"lift_count": 3}])


def test_the_routine_saved_case_judges_as_designed(app_settings):
    server = _lift_server(app_settings)
    case, person = _lift_case(server, "routine_saved")
    calls = [_saved(person, SAID)]
    assert person_rows(person)["routine"][0]["lift_count"] == 3
    assert judge.judge(case, calls, "Saved.", person_rows(person)) == []
    _, weighed = _lift_case(server, "routine_saved")  # a weight sent that the person never said
    sent = {**SAID, "lifts": [{**SAID["lifts"][0], "weight_kg": 60}, *SAID["lifts"][1:]]}
    [why] = judge.judge_calls(case["expect"], [_saved(weighed, sent)], "Saved.")
    assert why.startswith("save_routine sent lifts.*.weight_kg, which should be left out")
    _, logged = _lift_case(server, "routine_saved")  # logged as well as saved
    both = [_saved(logged, SAID), _logged(logged, [("Bench Press", 60, 8)])]
    assert judge.judge_calls(case["expect"], both, "Saved and logged.") == [
        "log_lifts was called and should not have been"]
    _, twice = _lift_case(server, "routine_saved")  # a second routine row
    two = [_saved(twice, SAID), _saved(twice, {**SAID, "name": "Push day 2"})]
    assert judge.judge_db(case["db"], person_rows(twice)) == ["routine: 2 rows, expected 1"]
    assert judge.judge(case, two, "Saved.", person_rows(twice))


ROUTINE_SETS = [("Bench Press", 60, 8)] * 3 + [("Overhead Press", 40, 10)] * 3


def test_the_routine_logged_case_judges_as_designed(app_settings):
    server = _lift_server(app_settings)
    case, person = _lift_case(server, "routine_logged")
    [seeded] = person_rows(person)["routine"]
    assert (seeded["name"], seeded["lift_count"]) == ("Push day", 2)
    assert judge.judge(case, [_logged(person, ROUTINE_SETS)], "Logged.", person_rows(person)) == []
    _, apart = _lift_case(server, "routine_logged")  # new_session true
    assert judge.judge_calls(case["expect"], [_logged(apart, ROUTINE_SETS, new_session=True)], "Logged.") == [
        "log_lifts sent new_session = true"]
    _, split = _lift_case(server, "routine_logged")  # a second session that day
    two = [_logged(split, ROUTINE_SETS[:3]), _logged(split, ROUTINE_SETS[3:], new_session=True)]
    assert judge.judge_db(case["db"], person_rows(split)) and judge.judge(case, two, "Logged.", person_rows(split))
    _, short = _lift_case(server, "routine_logged")  # one set short: set_count 5
    five = [_logged(short, ROUTINE_SETS[1:])]
    assert judge.judge_calls(case["expect"], five, "Logged.") == []
    [why] = judge.judge_db(case["db"], person_rows(short))
    assert why.startswith("lift_session: rows not matching") and '"set_count": 5' in why


def test_the_routine_changed_case_judges_as_designed(app_settings):
    server = _lift_server(app_settings)
    case, person = _lift_case(server, "routine_changed")
    changed = [("Bench Press", 62.5, 8)] * 3 + [("Overhead Press", 40, 10)] * 3
    assert judge.judge(case, [_logged(person, changed)], "Logged.", person_rows(person)) == []
    _, kept = _lift_case(server, "routine_changed")  # a set at 60: the change half made
    half = [_logged(kept, [("Bench Press", 60, 8)] + changed[1:])]
    assert judge.judge_calls(case["expect"], half, "Logged.") == ["log_lifts sent sets.*.weight_kg = 60"]
    _, five = _lift_case(server, "routine_changed")  # one set short: set_count 5
    short = [_logged(five, changed[1:])]
    assert judge.judge_calls(case["expect"], short, "Logged.") == [] and judge.judge_db(case["db"], person_rows(five))
    _, split = _lift_case(server, "routine_changed")  # two sessions
    two = [_logged(split, changed[:3]), _logged(split, changed[3:], new_session=True)]
    assert judge.judge_db(case["db"], person_rows(split))[0] == "lift_session: 2 rows, expected 1"
    assert judge.judge(case, two, "Logged.", person_rows(split))


def test_person_rows_gives_each_routine_its_lift_count_and_never_another_person_s(make_user):
    mine, theirs = make_user(), make_user()
    services.save_routine(mine.id, {"name": "Push day", "lifts": PUSH}, "claude")
    gone = services.save_routine(mine.id, {"name": "Gone", "lifts": [lift()]}, "claude")["id"]
    services.delete_entry(mine.id, "routine", gone)
    services.save_routine(theirs.id, {"name": "Theirs", "lifts": [lift()] * 5}, "claude")
    counts = {r["name"]: (r["lift_count"], r["deleted_at"] is None) for r in person_rows(mine.id)["routine"]}
    assert counts == {"Push day": (3, True), "Gone": (1, False)}
    assert [r["lift_count"] for r in person_rows(theirs.id)["routine"]] == [5]
