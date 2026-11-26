"""Programs (docs/contracts/routines-and-programs.md, decisions 15 to 20, R8 to R12, and the note of 2026-11-23): the
person's training program, a weekday schedule in blocks of weeks, saved with save_program and read in get_routines'
answer; deleted and restored as a routine is; exported, counted and taken with the account; one a person.

The clock is the test's own (db.utcnow replaced, as tests/test_routines.py does), so every today, every weekday and
every updated_at is set here, never read from the real clock. The person has no zone, so today is the clock's date.
Calls go through the services, and through the real HTTP path where the wrapper, the switch or the schema is the
point.
"""

import csv
import json
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, select
from sqlalchemy import text as sql_text

from app import db, links, mcp_schemas, mcp_server, rules, services, web
from app.calllog import KNOWN_NAMES
from app.db import make_engine
from app.mcp_schemas import GetRoutinesResult, SaveProgramResult
from app.models import Program, User
from app.services import common, entries, fields, programs
from app.services.entries import PREVIEW_NOTE

from . import text_rules as tr
from .conftest import TEST_DIR, alembic_config
from .test_calllog import rows
from .test_hardening_schema import _master
from .test_idempotency import writer  # noqa: F401 - writer is a fixture
from .test_mcp import failed, ok, registered_tools, set_switch
from .test_routines import Clock, statements_of
from .test_web import client, only_user, seam, sign_in, web_client  # noqa: F401 - fixtures

MONDAY = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)  # Mon 23 Nov 2026: the contract's PPL starts on it
PUSH = [{"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 60}]
PULL = [{"exercise": "Barbell Row", "sets": 3, "reps": 8, "weight_kg": 60}]
LEGS = [{"exercise": "Back Squat", "sets": 4, "reps": 5}]
PPL_DAYS = [{"weekday": "monday", "routine": "Push day"}, {"weekday": "wednesday", "routine": "Pull day"},
            {"weekday": "friday", "routine": "Legs day"}]
PPL = {"name": "PPL", "start_on": "2026-11-23", "blocks": [{"weeks": 8, "days": PPL_DAYS}]}
SAID = ("PPL: 8 weeks from Mon 23 Nov. Push day on Monday, Pull day on Wednesday and Legs day on Friday.")
NOT_FOUND = {"error": "not_found", "message": "No program with that id.",
             "fix": "Check the id and the kind: use the id that save_program returned, or one from get_routines."}


@pytest.fixture
def clock(monkeypatch) -> Clock:
    held = Clock()
    held.now = MONDAY
    monkeypatch.setattr(db, "utcnow", held)
    return held


def at(clock: Clock, day: date, hour: int = 12) -> None:
    clock.now = datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc)


@pytest.fixture
def user(make_user, clock):
    person = make_user()
    for name, items in (("Push day", PUSH), ("Pull day", PULL), ("Legs day", LEGS)):
        assert "error" not in services.save_routine(person.id, {"name": name, "lifts": items}, "web")
    return person


def save(person_id: int, data: dict | None = None, by: str = "claude") -> dict:
    return services.save_program(person_id, PPL if data is None else data, by)


def saved(person_id: int, data: dict | None = None) -> dict:
    out = save(person_id, data)
    assert "error" not in out, out
    SaveProgramResult.model_validate(out)
    return out


def read(person_id: int) -> dict:
    found = services.get_routines(person_id)
    assert "error" not in found, found
    GetRoutinesResult.model_validate(found)
    return found


def program(person_id: int) -> dict:
    return read(person_id)["program"]


def table() -> list[dict]:
    with db.new_session() as s:
        return [{c.name: getattr(r, c.key) for c in Program.__table__.columns}
                for r in s.scalars(select(Program).order_by(Program.id))]


def refused(person_id: int, data: dict) -> dict:
    out = save(person_id, data)
    assert "error" in out, out
    return out


def messages(out: dict) -> list[tuple[str, str, str]]:
    return [(e["error"], e["message"], e["fix"]) for e in out["errors"]]


def with_start(start: str | None, blocks=None, **more) -> dict:
    data = {"name": "PPL", "blocks": blocks or [{"weeks": 8, "days": PPL_DAYS}], **more}
    return data if start is None else {**data, "start_on": start}


# ---------------------------------------------------------------- a save (decisions 15, 16, R9)

def test_a_new_program_is_saved_in_the_contract_s_words_with_its_row_and_answer(user, clock):
    at(clock, date(2026, 11, 21))  # a Saturday: "starting on Monday"
    out = saved(user.id)
    assert out["summary"] == f"Saved {SAID}"
    assert (out["kind"], out["created"], out["links"]) == ("program", True, {"routines": links.page("routines")})
    assert out["program"] == {"id": out["id"], "name": "PPL", "start_on": "2026-11-23",
                              "blocks": [{"weeks": 8, "days": PPL_DAYS}], "weeks": 8,
                              "updated_at": clock.now.isoformat()}
    [row] = table()
    assert (row["user_id"], row["name"], row["start_on"], row["deleted_at"]) == (user.id, "PPL", date(2026, 11, 23),
                                                                                  None)
    assert row["blocks"] == json.dumps([{"weeks": 8, "days": PPL_DAYS}], separators=(",", ":"))


def test_days_are_stored_in_weekday_order_with_each_routine_s_stored_name_case_and_spaces_forgiven(user):
    days = [{"weekday": " FRIDAY ", "routine": "legs   DAY"}, {"weekday": "Monday", "routine": " push day "},
            {"weekday": "wednesday", "routine": "PULL DAY"}]
    out = saved(user.id, {"name": "  P  P L ", "blocks": [{"weeks": 8, "days": days}]})
    assert out["program"]["blocks"] == [{"weeks": 8, "days": PPL_DAYS}] and out["program"]["name"] == "P P L"


def test_left_out_or_blank_start_on_is_today(user, clock):
    at(clock, date(2026, 12, 2))
    assert saved(user.id, with_start(None))["program"]["start_on"] == "2026-12-02"
    assert saved(user.id, with_start("  "))["program"]["start_on"] == "2026-12-02"


def test_several_blocks_say_their_weeks_and_a_block_of_one_week_says_week(user):
    blocks = [{"weeks": 4, "days": PPL_DAYS[:2]}, {"weeks": 1, "days": PPL_DAYS[2:]},
              {"weeks": 3, "days": PPL_DAYS}]
    out = saved(user.id, with_start("2026-11-23", blocks))
    assert out["summary"] == ("Saved PPL: 8 weeks from Mon 23 Nov. Weeks 1 to 4: Push day on Monday and Pull day on "
                              "Wednesday. Week 5: Legs day on Friday. Weeks 6 to 8: Push day on Monday, Pull day on "
                              "Wednesday and Legs day on Friday.")
    assert out["program"]["weeks"] == 8
    one = saved(user.id, with_start("2026-11-23", [{"weeks": 1, "days": PPL_DAYS[:1]}]))
    assert one["summary"] == "Replaced your program with PPL: 1 week from Mon 23 Nov. Push day on Monday."


def test_a_start_in_another_year_carries_its_year(user):
    assert saved(user.id, with_start("2027-01-04"))["summary"].startswith("Saved PPL: 8 weeks from Mon 4 Jan 2027.")


# ---------------------------------------------------------------- one a person (decision 16, R9)

def test_a_save_replaces_the_one_there_whatever_its_name_and_keeps_its_id(user, clock):
    first = saved(user.id)
    clock.now += timedelta(seconds=5)
    again = saved(user.id, {"name": "Upper lower", "start_on": "2026-11-30",
                            "blocks": [{"weeks": 6, "days": PPL_DAYS[:2]}]})
    assert (again["id"], again["created"]) == (first["id"], False)
    assert again["summary"] == ("Replaced your program with Upper lower: 6 weeks from Mon 30 Nov. Push day on Monday "
                                "and Pull day on Wednesday.")
    [row] = table()
    assert (row["name"], row["start_on"], row["updated_at"]) == ("Upper lower", date(2026, 11, 30), clock.now)


def test_the_same_again_writes_nothing_and_says_so(user, clock):
    first = saved(user.id)
    before = table()
    clock.now += timedelta(seconds=5)
    same, seen = statements_of(lambda: saved(user.id, {**PPL, "blocks": [{"weeks": 8, "days": PPL_DAYS[::-1]}]}))
    assert same["summary"] == "PPL is already saved as given: nothing changed." and same["created"] is False
    assert table() == before and same["program"]["updated_at"] == first["program"]["updated_at"]
    assert not [sql for sql, _ in seen if sql.startswith(("UPDATE program", "INSERT INTO program"))]


# ---------------------------------------------------------------- a blank start keeps the program's (the owner's pick
# of 2026-11-23, the C5c review's S1): PPL from Mon 23 Nov, 8 weeks, so it ends after Sun 17 Jan 2027

MOVED = [{"weeks": 8, "days": [*PPL_DAYS[:2], {"weekday": "saturday", "routine": "Legs day"}]}]  # Friday to Saturday


@pytest.mark.parametrize("start", [None, "", "  "])
def test_a_running_program_changed_with_start_on_left_out_keeps_its_start_and_its_week(user, clock, start):
    saved(user.id)
    at(clock, date(2026, 12, 9))  # Wed 9 Dec: week 3
    assert (program(user.id)["week"], program(user.id)["today"]) == (3, "Pull day")
    out = saved(user.id, with_start(start, MOVED))
    assert out["summary"] == ("Replaced your program with PPL: 8 weeks from Mon 23 Nov. Push day on Monday, Pull day "
                              "on Wednesday and Legs day on Saturday.")
    assert out["program"]["start_on"] == "2026-11-23" and table()[0]["start_on"] == date(2026, 11, 23)
    assert (program(user.id)["week"], program(user.id)["today"]) == (3, "Pull day")


def test_a_program_on_its_last_day_keeps_its_start_and_one_ended_starts_today(user, clock):
    saved(user.id)
    at(clock, date(2027, 1, 17))  # its last day: still running, week 8
    assert saved(user.id, with_start(None, MOVED))["program"]["start_on"] == "2026-11-23"
    at(clock, date(2027, 1, 18))  # the day after: ended
    assert program(user.id)["state"] == "ended"
    assert saved(user.id, with_start(None))["program"]["start_on"] == "2027-01-18"


def test_a_program_not_started_yet_keeps_its_start(user, clock):
    at(clock, date(2026, 11, 21))
    saved(user.id, with_start("2026-11-30"))
    at(clock, date(2026, 11, 25))
    out = saved(user.id, with_start(None, MOVED))
    assert out["program"]["start_on"] == "2026-11-30" and program(user.id)["state"] == "not_started"


def test_a_deleted_program_and_none_at_all_start_today(user, clock, make_user):
    first = saved(user.id)
    services.delete_entry(user.id, "program", first["id"])
    at(clock, date(2026, 12, 9))  # its start and weeks would still run, but it is deleted
    back = saved(user.id, with_start(None))
    assert back["summary"].startswith("Brought back and replaced PPL: 8 weeks from Wed 9 Dec.")
    assert back["program"]["start_on"] == "2026-12-09"
    other = make_user()
    for name, items in (("Push day", PUSH), ("Pull day", PULL), ("Legs day", LEGS)):
        services.save_routine(other.id, {"name": name, "lifts": items}, "web")
    assert saved(other.id, with_start(None))["program"]["start_on"] == "2026-12-09"  # none at all


def test_a_start_on_sent_replaces_the_stored_one_and_is_checked_as_before(user, clock):
    saved(user.id)
    at(clock, date(2026, 12, 9))
    assert saved(user.id, with_start("2026-12-07", MOVED))["program"]["start_on"] == "2026-12-07"
    assert program(user.id)["week"] == 1
    assert refused(user.id, with_start("2026-13-01", MOVED))["error"] == "invalid_date"
    assert table()[0]["start_on"] == date(2026, 12, 7)


def test_the_same_days_again_with_start_on_left_out_on_a_later_day_write_nothing(user, clock):
    first = saved(user.id)
    before = table()
    at(clock, date(2026, 12, 9))
    same, seen = statements_of(lambda: saved(user.id, with_start(None)))
    assert same["summary"] == "PPL is already saved as given: nothing changed."
    assert table() == before and same["program"]["updated_at"] == first["program"]["updated_at"]
    assert not [sql for sql, _ in seen if sql.startswith(("UPDATE program", "INSERT INTO program"))]


def test_a_deleted_program_is_brought_back_and_replaced_with_its_id(user, clock):
    first = saved(user.id)
    services.delete_entry(user.id, "program", first["id"])
    assert "program" not in read(user.id)
    clock.now += timedelta(seconds=5)
    back = saved(user.id)  # the same as it was: a deleted one is brought back, never the same again
    assert (back["id"], back["created"]) == (first["id"], False)
    assert back["summary"] == f"Brought back and replaced {SAID}"
    assert table()[0]["deleted_at"] is None and program(user.id)["id"] == first["id"]


def test_two_saves_at_once_make_one_row(user, monkeypatch):
    checked = programs._checked

    def slow(*args):  # after the checks: without the lock both would find no row and make one each
        found = checked(*args)
        time.sleep(0.2)
        return found

    monkeypatch.setattr(programs, "_checked", slow)
    barrier = threading.Barrier(2)
    results, failures = [], []

    def send(name):
        try:
            barrier.wait(timeout=5)
            results.append(save(user.id, {**PPL, "name": name}))
        except Exception as exc:  # reported below, so a crash in a thread fails the test
            failures.append(exc)

    threads = [threading.Thread(target=send, args=(name,)) for name in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not failures, failures
    assert sorted(r["created"] for r in results) == [False, True]
    assert len(table()) == 1 and len({r["id"] for r in results}) == 1


# ---------------------------------------------------------------- the checks (decision 18, the note's words)

DAYS = PPL_DAYS


@pytest.mark.parametrize(("data", "expected"), [
    ({"name": "PPL", "blocks": [{"weeks": 60, "days": DAYS}]},
     [("invalid_field", "A program runs 1 to 52 weeks, not 60.", "Send fewer weeks.")]),
    ({"name": "PPL", "blocks": [{"weeks": 40, "days": DAYS}, {"weeks": 13, "days": DAYS}]},
     [("invalid_field", "A program runs 1 to 52 weeks, not 53.", "Send fewer weeks.")]),
    ({"name": "PPL", "blocks": [{"weeks": 4, "days": DAYS}, {"weeks": 4, "days": [
        {"weekday": "wednesday", "routine": "Push day"}, {"weekday": "Wednesday", "routine": "Pull day"}]}]},
     [("invalid_field", "Block 2: wednesday is there twice.", "Give each weekday once in a block.")]),
    ({"name": "PPL", "blocks": [{"weeks": 8, "days": [DAYS[0], {"weekday": "weds", "routine": "Pull day"}]}]},
     [("invalid_field", "Block 1, day 2: 'weds' is not a weekday.", "Use monday to sunday.")]),
    ({"name": "PPL", "blocks": [{"weeks": 8, "days": [DAYS[0], {"weekday": "wednesday", "routine": "pul day"}]}]},
     [("unknown_routine", "Block 1, Wednesday: 'pul day' is not a saved routine. Saved: Legs day, Pull day and Push "
                          "day.",
       "Send a name as get_routines gives it, or save the routine first with save_routine.")]),
    ({"name": "PPL", "blocks": [{"weeks": 0, "days": DAYS}]},
     [("invalid_field", "Block 1: weeks 0 is less than 1.", "Give weeks on block 1 as 1 or more.")]),
    ({"name": "PPL", "blocks": [{"weeks": 8, "days": []}]},
     [("invalid_field", "Block 1 holds 1 to 7 days, not 0.", "Send 1 to 7 days in block 1, each weekday once.")]),
    ({"name": "PPL", "blocks": []},
     [("invalid_field", "A program holds 1 to 52 blocks, not 0.", "Send 1 to 52 blocks, each with its weeks and "
                                                                  "days.")]),
    ({"name": "PPL"}, [("missing_field", "blocks is missing.", "Give blocks, each with its weeks and days.")]),
    ({"name": "PPL", "blocks": [{"weeks": 8, "days": DAYS, "rest": 1}]},
     [("invalid_field", "Block 1: 'rest' is not a field of a block.", "Use: weeks, days.")]),
    ({"name": "PPL", "blocks": [{"weeks": 8, "days": [{**DAYS[0], "time": "6am"}]}]},
     [("invalid_field", "Block 1, day 1: 'time' is not a field of a day.", "Use: weekday, routine.")]),
    ({"name": "PPL", "blocks": [{"weeks": 8, "days": [{"routine": "Push day"}]}]},
     [("missing_field", "Block 1, day 1: weekday is missing.", "Use monday to sunday.")]),
    ({"name": "PPL", "blocks": [{"weeks": 8, "days": [{"weekday": "monday"}]}]},
     [("missing_field", "Block 1, Monday: routine is missing.", "Give a saved routine's name, as get_routines gives "
                                                                "it.")]),
    ({"name": "PPL", "blocks": [{"days": DAYS}]},
     [("missing_field", "Block 1: weeks is missing.", "Give weeks on block 1.")]),
    ({"name": "", "blocks": [{"weeks": 8, "days": DAYS}]},
     [("missing_field", "name is missing.", "Give name, 1 to 64 characters.")]),
    ({**PPL, "length": 8},
     [("invalid_field", "'length' is not a field of a program.", "Use: name, start_on, blocks.")]),
])
def test_each_check_alone_refuses_in_its_words_and_writes_nothing(user, data, expected):
    out = refused(user.id, data)
    assert messages(out) == expected and table() == []


def test_52_weeks_are_taken_and_53_refused(user):
    assert saved(user.id, with_start("2026-11-23", [{"weeks": 52, "days": DAYS}]))["program"]["weeks"] == 52
    assert refused(user.id, with_start("2026-11-23", [{"weeks": 52, "days": DAYS}, {"weeks": 1, "days": DAYS}]))


def test_seven_days_a_block_are_taken_and_8_refused(user):
    week = [{"weekday": d, "routine": "Push day"} for d in programs.WEEKDAYS]
    assert len(saved(user.id, with_start("2026-11-23", [{"weeks": 1, "days": week}]))["program"]["blocks"][0][
        "days"]) == 7
    out = refused(user.id, with_start("2026-11-23", [{"weeks": 1, "days": [*week, week[0]]}]))
    assert messages(out)[0] == ("invalid_field", "Block 1 holds 1 to 7 days, not 8.",
                                "Send 1 to 7 days in block 1, each weekday once.")


def test_an_unknown_routine_with_none_saved_says_so(make_user, clock):
    out = refused(make_user().id, PPL)
    assert messages(out)[0] == ("unknown_routine", "Block 1, Monday: 'Push day' is not a saved routine. No routine is "
                                                   "saved yet.", "Save the routine first with save_routine.")


def test_a_deleted_routine_is_not_a_saved_one(user):
    [pull] = [r for r in read(user.id)["routines"] if r["name"] == "Pull day"]
    services.delete_entry(user.id, "routine", pull["id"])
    out = refused(user.id, PPL)
    assert messages(out) == [("unknown_routine", "Block 1, Wednesday: 'Pull day' is not a saved routine. Saved: Legs "
                                                 "day and Push day.", "Send a name as get_routines gives it, or save "
                                                                      "the routine first with save_routine.")]


def test_every_failure_comes_back_at_once_in_order(user):
    out = refused(user.id, {"name": "", "start_on": "soon", "blocks": [
        {"weeks": 0, "days": [{"weekday": "weds", "routine": "pul day"}]}, {"weeks": 60, "days": DAYS}]})
    assert [m for _, m, _ in messages(out)] == [
        "name is missing.", "start_on 'soon' is not a date.", "Block 1: weeks 0 is less than 1.",
        "Block 1, day 1: 'weds' is not a weekday.",
        "Block 1, day 1: 'pul day' is not a saved routine. Saved: Legs day, Pull day and Push day.",
        "A program runs 1 to 52 weeks, not 60."]


@pytest.mark.parametrize(("start", "expected"), [
    ("23/11/2026", ("invalid_date", "start_on '23/11/2026' is not a date.",
                    "Give the date as YYYY-MM-DD, or leave it out for today.")),
    ("2024-11-21", ("date_too_old", "start_on 2024-11-21 is more than 2 years ago.",
                    "Give a start from the last 2 years only; check the year.")),
    ("2027-11-23", ("date_too_far", "start_on 2027-11-23 is more than 52 weeks after today, 2026-11-23.",
                    "Give a start at most 52 weeks on: 2027-11-22 at the latest.")),
    ("2028-01-03", ("date_too_far", "start_on 2028-01-03 is more than 52 weeks after today, 2026-11-23.",
                    "Give a start at most 52 weeks on: 2027-11-22 at the latest.")),
])
def test_start_on_s_three_refusals_name_start_on(user, start, expected):
    out = refused(user.id, with_start(start))
    assert messages(out) == [expected] and out["field"] == "start_on" and table() == []


def test_start_on_s_edges_are_taken(user):
    assert saved(user.id, with_start("2027-11-22"))["program"]["start_on"] == "2027-11-22"  # 52 weeks on
    oldest = (date(2026, 11, 23) - timedelta(days=rules.OLDEST_DAYS)).isoformat()
    assert saved(user.id, with_start(oldest))["program"]["start_on"] == oldest
    assert refused(user.id, with_start((date(2026, 11, 23) - timedelta(days=rules.OLDEST_DAYS + 1)).isoformat()))


def test_the_cap_is_one_constant_for_the_weeks_and_the_forward_bound():
    assert rules.PROGRAM_WEEKS == (1, 52)


def test_no_data_and_a_wrong_caller_are_refused_as_every_write_s_are(user):
    assert services.save_program(user.id, "PPL", "claude")["error"] == "invalid_field"
    assert services.save_program(user.id, PPL, "robot")["error"] == "invalid_by"
    assert services.save_program(10 ** 6, PPL, "claude")["error"] == "no_account"
    assert table() == []


# ---------------------------------------------------------------- the read (R10, the note's states)

@pytest.mark.parametrize("offset", range(7))
def test_every_weekday_of_week_3_gives_its_routine_or_a_rest_day_and_the_week_s_days(user, clock, offset):
    saved(user.id, {**PPL, "start_on": "2026-11-02"})  # week 3 runs Mon 16 Nov to Sun 22 Nov
    today = date(2026, 11, 16) + timedelta(days=offset)
    at(clock, today)
    found = program(user.id)
    routine = {0: "Push day", 2: "Pull day", 4: "Legs day"}.get(offset)
    assert (found["state"], found["week"], found["weeks"], found["today"]) == ("running", 3, 8, routine)
    assert found["this_week"] == [{"date": (date(2026, 11, 16) + timedelta(days=i)).isoformat(),
                                   "weekday": programs.WEEKDAYS[i],
                                   "routine": {0: "Push day", 2: "Pull day", 4: "Legs day"}.get(i)} for i in range(7)]
    following = {0: ("2026-11-18", "Pull day"), 1: ("2026-11-18", "Pull day"), 2: ("2026-11-20", "Legs day"),
                 3: ("2026-11-20", "Legs day")}.get(offset, ("2026-11-23", "Push day"))
    assert found["next"] == {"date": following[0], "routine": following[1]}
    if routine:
        assert found["words"] == f"PPL, week 3 of 8. Today: {routine}."
    else:
        shown = {"2026-11-18": "Wed 18 Nov", "2026-11-20": "Fri 20 Nov", "2026-11-23": "Mon 23 Nov"}[following[0]]
        assert found["words"] == f"PPL, week 3 of 8. Today is a rest day. Next: {following[1]} on {shown}."


def test_the_contract_s_two_lines_on_a_wednesday_and_on_sat_21_nov(user, clock):
    saved(user.id, {**PPL, "start_on": "2026-11-02"})
    at(clock, date(2026, 11, 18))
    assert program(user.id)["words"] == "PPL, week 3 of 8. Today: Pull day."
    at(clock, date(2026, 11, 21))
    assert program(user.id)["words"] == "PPL, week 3 of 8. Today is a rest day. Next: Push day on Mon 23 Nov."


def test_a_week_starts_on_start_on_s_weekday_not_on_monday(user, clock):
    saved(user.id, {**PPL, "start_on": "2026-11-25"})  # a Wednesday
    at(clock, date(2026, 12, 1))  # Tuesday: the 7th day of week 1
    found = program(user.id)
    assert (found["week"], found["today"]) == (1, None)
    assert [d["date"] for d in found["this_week"]] == [(date(2026, 11, 25) + timedelta(days=i)).isoformat()
                                                       for i in range(7)]
    assert found["next"] == {"date": "2026-12-02", "routine": "Pull day"}
    at(clock, date(2026, 12, 2))
    assert (program(user.id)["week"], program(user.id)["today"]) == (2, "Pull day")


def test_next_runs_across_a_block_s_end_into_the_next_block_s_days(user, clock):
    blocks = [{"weeks": 1, "days": [{"weekday": "monday", "routine": "Push day"}]},
              {"weeks": 1, "days": [{"weekday": "thursday", "routine": "Legs day"}]}]
    saved(user.id, with_start("2026-11-23", blocks))
    at(clock, date(2026, 11, 24))  # Tuesday of week 1: its block holds no more days
    found = program(user.id)
    assert found["next"] == {"date": "2026-12-03", "routine": "Legs day"}
    assert found["words"] == "PPL, week 1 of 2. Today is a rest day. Next: Legs day on Thu 3 Dec."
    at(clock, date(2026, 11, 30))  # Monday of week 2: block 2's days, so no Push day
    assert (program(user.id)["week"], program(user.id)["today"]) == (2, None)


def test_a_rest_day_with_no_routine_left_says_so(user, clock):
    saved(user.id, with_start("2026-11-23", [{"weeks": 1, "days": PPL_DAYS[:1]}]))
    at(clock, date(2026, 11, 28))
    found = program(user.id)
    assert (found["state"], found["today"], found["next"]) == ("running", None, None)
    assert found["words"] == "PPL, week 1 of 1. Today is a rest day, and no routine day is left."


def test_before_its_start_the_program_says_when_it_starts_and_its_first_routine(user, clock):
    at(clock, date(2026, 11, 21))
    saved(user.id)
    found = program(user.id)
    assert (found["state"], found["week"], found["today"], found["this_week"]) == ("not_started", None, None, None)
    assert found["next"] == {"date": "2026-11-23", "routine": "Push day"}
    assert found["words"] == "PPL starts on Mon 23 Nov. First: Push day."
    saved(user.id, {**PPL, "start_on": "2026-11-24"})  # a Tuesday: the first routine is that Wednesday's
    assert program(user.id)["words"] == "PPL starts on Tue 24 Nov. First: Pull day on Wed 25 Nov."


def test_after_its_end_the_program_says_when_it_ended(user, clock):
    saved(user.id)
    at(clock, date(2027, 1, 18))  # 8 weeks on: the day after Sun 17 Jan
    found = program(user.id)
    assert (found["state"], found["week"], found["today"], found["next"], found["this_week"]) == (
        "ended", None, None, None, None)
    assert found["words"] == "PPL ended on Sun 17 Jan, after 8 weeks."  # today's year: no year said
    at(clock, date(2027, 1, 17))
    assert (program(user.id)["state"], program(user.id)["week"], program(user.id)["today"]) == ("running", 8, None)
    at(clock, date(2027, 1, 18))
    saved(user.id, {**PPL, "start_on": "2026-11-23", "blocks": [{"weeks": 1, "days": DAYS}]})
    assert program(user.id)["words"] == "PPL ended on Sun 29 Nov 2026, after 1 week."


def test_a_day_whose_routine_is_not_live_keeps_its_name_and_the_words_say_so(user, clock):
    saved(user.id, {**PPL, "start_on": "2026-11-02"})
    [pull] = [r for r in read(user.id)["routines"] if r["name"] == "Pull day"]
    services.delete_entry(user.id, "routine", pull["id"])
    at(clock, date(2026, 11, 18))
    found = program(user.id)
    assert found["today"] == "Pull day"
    assert found["words"] == "PPL, week 3 of 8. Today: Pull day. Pull day is not saved now."
    at(clock, date(2026, 11, 17))
    assert program(user.id)["words"] == ("PPL, week 3 of 8. Today is a rest day. Next: Pull day on Wed 18 Nov. Pull "
                                         "day is not saved now.")
    at(clock, date(2026, 11, 16))
    assert program(user.id)["words"] == "PPL, week 3 of 8. Today: Push day."  # only a routine the words name


def test_get_routines_leaves_program_out_with_none_and_leads_its_summary_with_the_words(user, clock):
    before = read(user.id)
    assert "program" not in before and before["summary"] == "3 routines: Legs day, Pull day and Push day."
    saved(user.id, {**PPL, "start_on": "2026-11-02"})
    at(clock, date(2026, 11, 18))
    after = read(user.id)
    assert list(after) == ["routines", "how", "summary", "program", "links"]
    assert after["summary"] == "PPL, week 3 of 8. Today: Pull day. 3 routines: Legs day, Pull day and Push day."
    assert after["program"]["blocks"] == [{"weeks": 8, "days": PPL_DAYS}]
    assert after["program"]["start_on"] == "2026-11-02"


def test_a_deleted_program_leaves_get_routines_as_before(user):
    made = saved(user.id)
    services.delete_entry(user.id, "program", made["id"])
    assert "program" not in read(user.id)


def test_with_no_routine_live_the_program_still_reads(user, clock):
    saved(user.id)
    for r in read(user.id)["routines"]:
        services.delete_entry(user.id, "routine", r["id"])
    found = read(user.id)
    assert found["summary"] == "PPL, week 1 of 8. Today: Push day. Push day is not saved now. No routines saved yet."
    assert found["note"] == "No routines saved yet. save_routine saves one."


def test_another_person_s_program_never_reads_and_their_id_is_not_found(user, make_user, clock):
    other = make_user()
    services.save_routine(other.id, {"name": "Push day", "lifts": PUSH}, "web")
    theirs = saved(other.id, {"name": "Theirs", "blocks": [{"weeks": 2, "days": DAYS[:1]}]})
    assert "program" not in read(user.id)
    for call in (lambda: services.delete_entry(user.id, "program", theirs["id"], "claude"),
                 lambda: services.delete_entry(user.id, "program", theirs["id"]),
                 lambda: services.restore_entry(user.id, "program", theirs["id"], "claude"),
                 lambda: services.get_entry(user.id, "program", theirs["id"])):
        assert call() == NOT_FOUND
    mine = saved(user.id)
    assert mine["created"] is True and mine["id"] != theirs["id"] and program(other.id)["name"] == "Theirs"


# ---------------------------------------------------------------- delete, restore, undo, edit (decision 19, R12)

def test_a_delete_through_mcp_takes_two_calls_with_words_and_a_restore_brings_it_back(writer):
    http, token, account = writer
    for name, items in (("Push day", PUSH), ("Pull day", PULL), ("Legs day", LEGS)):
        services.save_routine(account, {"name": name, "lifts": items}, "web")
    made = ok(http, token, "save_program", PPL)
    before = table()
    first = ok(http, token, "delete_entry", {"kind": "program", "entry_id": made["id"]})
    assert table() == before
    assert (first["kind"], first["id"], first["deleted"], first["dry_run"], first["note"]) == (
        "program", made["id"], False, True, PREVIEW_NOTE)
    assert first["entry"] == {**made["program"], "deleted_at": None} and first["summary"] == SAID
    second = ok(http, token, "delete_entry", {"kind": "program", "entry_id": made["id"], "confirm": first["confirm"]})
    assert (second["deleted"], second["dry_run"], second["summary"]) == (True, False, SAID)
    assert table()[0]["deleted_at"] is not None and "program" not in read(account)
    back = ok(http, token, "restore_entry", {"kind": "program", "entry_id": made["id"]})
    assert back["restored"] is True and back["entry"]["deleted_at"] is None and "program" in read(account)
    assert [(r.function, r.outcome, r.tried) for r in rows()[-4:]] == [
        ("save_program", "written", "blocks[1]{days,weeks} name start_on"),
        ("delete_entry", "preview", "entry_id kind=program"),
        ("delete_entry", "written", "confirm entry_id kind=program"),
        ("restore_entry", "written", "entry_id kind=program")]


def test_with_deleting_off_a_program_s_delete_is_not_allowed_and_nothing_is_deleted(writer):
    http, token, account = writer
    for name, items in (("Push day", PUSH), ("Pull day", PULL), ("Legs day", LEGS)):
        services.save_routine(account, {"name": name, "lifts": items}, "web")
    made = ok(http, token, "save_program", PPL)
    set_switch(account, "log")
    out = failed(http, token, "delete_entry", {"kind": "program", "entry_id": made["id"]})
    assert out["error"] == "not_allowed" and table()[0]["deleted_at"] is None
    assert ok(http, token, "save_program", {**PPL, "name": "PPL 2"})["created"] is False  # saving stays at log


def test_undo_edit_is_nothing_to_undo_edit_entry_is_unknown_kind_and_a_live_one_is_not_deleted(user):
    made = saved(user.id)
    assert services.restore_entry(user.id, "program", made["id"], "claude", undo_edit=True) == {
        "error": "nothing_to_undo", "message": "A program has no earlier values kept.",
        "fix": "Save it again with save_program, as it was."}
    assert services.edit_entry(user.id, "program", made["id"], {"name": "Other"}, "claude") == {
        "error": "unknown_kind", "message": "A program is not edited here.",
        "fix": "Use save_program with the whole program."}
    assert services.restore_entry(user.id, "program", made["id"], "claude") == {
        "error": "not_deleted", "message": "This entry is not deleted, so there is nothing to restore.",
        "fix": "Nothing to do: it is live. get_routines shows it."}
    found = services.get_entry(user.id, "program", made["id"])
    assert found["entry"] == {**made["program"], "deleted_at": None}
    assert found["links"] == {"routines": links.page("routines")}


def test_a_program_is_no_entry_kind_and_delete_takes_it(user):
    assert "program" not in services.entry_kinds() and entries._deletable()["program"] is Program


def test_a_routine_s_save_delete_restore_and_an_edit_never_read_the_program_table(user):
    saved(user.id)
    rid = read(user.id)["routines"][0]["id"]
    session = services.log_lifts(user.id, {"sets": [{"exercise": "Bench Press", "weight_kg": 60, "reps": 8}]},
                                 "claude")["id"]
    actions = (lambda: services.save_routine(user.id, {"name": "Arms", "lifts": PUSH}, "claude"),
               lambda: services.delete_entry(user.id, "routine", rid, "claude"),
               lambda: services.delete_entry(user.id, "routine", rid),
               lambda: services.restore_entry(user.id, "routine", rid, "claude"),
               lambda: services.edit_entry(user.id, "lift_session", session, {"notes": "heavy"}, "claude"))
    for action in actions:
        out, seen = statements_of(action)
        assert "error" not in out, out
        assert not [sql for sql, _ in seen if re.search(r"\bprogram\b", sql)], seen


def test_every_statement_on_program_names_the_person(user, make_user):
    other = make_user()
    services.save_routine(other.id, {"name": "Push day", "lifts": PUSH}, "web")
    saved(other.id, {"name": "Theirs", "blocks": [{"weeks": 2, "days": DAYS[:1]}]})
    made = {}
    actions = (lambda: made.setdefault("id", saved(user.id)["id"]), lambda: saved(user.id),
               lambda: services.get_routines(user.id), lambda: services.delete_entry(user.id, "program", made["id"]),
               lambda: services.restore_entry(user.id, "program", made["id"], "claude"),
               lambda: services.export_data(user.id))
    for action in actions:
        _, seen = statements_of(action)
        on = [(sql, p) for sql, p in seen if re.search(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+program\b", sql)]
        assert on
        for sql, params in on:
            if sql.startswith("UPDATE program"):
                assert re.search(r"WHERE program\.id = \?$", sql) and made["id"] in params, sql
            else:
                assert ("user_id" in sql) and user.id in params, sql


# ---------------------------------------------------------------- export, account delete, data, the switch lines

def test_the_export_holds_programs_csv_after_routines_csv_as_stored_and_deleted_too(user, clock):
    made = saved(user.id)
    services.delete_entry(user.id, "program", made["id"])
    out = services.export_data(user.id)
    names = list(out["files"])
    assert names.index("programs.csv") == names.index("routines.csv") + 1 and out["rows"]["programs.csv"] == 1
    lines = out["files"]["programs.csv"].lstrip("﻿").splitlines()
    assert lines[0] == "id,name,start_on,blocks,created_at,updated_at,deleted_at"
    [row] = list(csv.DictReader(lines))
    assert (row["name"], row["start_on"], row["blocks"]) == ("PPL", "2026-11-23", table()[0]["blocks"])
    assert row["deleted_at"] == MONDAY.isoformat()
    readme = out["files"]["README.txt"]
    assert ("  programs.csv        your program: blocks holds its weeks as JSON, each with weeks and days, a day\n"
            "                      a weekday and a routine's name. start_on is the day week 1 starts\n") in readme
    assert readme.index("routines.csv") < readme.index("programs.csv") < readme.index("food_entries.csv")


def test_another_person_s_program_is_never_exported(user, make_user):
    other = make_user()
    services.save_routine(other.id, {"name": "Push day", "lifts": PUSH}, "web")
    saved(other.id, {"name": "Theirs", "blocks": [{"weeks": 2, "days": DAYS[:1]}]})
    assert services.export_data(user.id)["rows"]["programs.csv"] == 0


def test_account_delete_takes_the_program_and_counts_it(client, make_user, clock):
    sign_in(client)
    me = only_user().id
    other = make_user().id
    for person in (me, other):
        services.save_routine(person, {"name": "Push day", "lifts": PUSH}, "web")
        saved(person, {"name": "Mine", "blocks": [{"weeks": 2, "days": DAYS[:1]}]})
    page = client.get("/settings/delete").text
    assert re.search(r'<li class="entry-line"><span>Routines</span><strong>1</strong></li>\s*'
                     r'<li class="entry-line"><span>Programs</span><strong>1</strong></li>', page)
    with db.session_scope() as s:
        s.expunge(s.get(User, me))
    result = services.delete_account(me)
    assert result["rows"]["program"] == 1 and [r["user_id"] for r in table()] == [other]


def test_other_tables_names_the_program_for_get_routines():
    assert fields.OTHER_TABLES["program"] == "get_routines reads it."


def test_the_settings_lines_name_programs_where_they_list_what_an_assistant_saves_and_deletes():
    lines = web.CLAUDE_CAN_LINES
    assert "save foods, routines and programs" in lines["log_delete"]
    assert "delete entries, saved foods, routines and programs." in lines["log_delete"]
    assert lines["log"] == ("Everything above except deleting. A second weigh-in or measurement on a day still "
                            "replaces the first, which is kept.")
    assert (Program, "Programs") in web.DELETE_COUNTS


# ---------------------------------------------------------------- the function, as published (R11)

SAVE_DESCRIPTION = (
    "Saves the person's training program: the routine for each day of the week, in blocks of weeks that repeat the "
    "same days, such as 8 weeks of push on Monday, pull on Wednesday and legs on Friday. Each day names a routine "
    "already saved: save the routines first with save_routine. Saving a program replaces the one there. Send only "
    "what the person said or agreed to.")
FIELD_WORDS = {
    "name": "The program's name, as the person says it, such as PPL. 1 to 64 characters.",
    "start_on": ("The day week 1 starts, YYYY-MM-DD. Left out: the saved program's start if it has not ended, "
                 "else today."),
    "blocks": "The weeks in order, 1 to 52 weeks in all. A block is some weeks that repeat the same days.",
    "weeks": "How many weeks this block runs.",
    "days": "The training days of each week in this block, each weekday at most once.",
    "weekday": "monday to sunday.",
    "routine": "A saved routine's name, as get_routines gives it.",
}
ID_WORDS = "The program's id, for delete_entry and restore_entry with kind program."
PROGRAM_WORDS = ("The person's program, present only when one is saved: this week's number, today's routine, the next "
                 "one and this week's days.")


def test_save_program_is_published_in_the_contract_s_words_after_save_routine(http):
    tools = registered_tools(http)
    names = list(tools)
    assert names.index("save_program") == names.index("save_routine") + 1
    entry = tools["save_program"]
    assert (entry["title"], entry["description"]) == ("Save a program", SAVE_DESCRIPTION)
    assert entry["annotations"] == tools["save_routine"]["annotations"]
    assert mcp_server.NEEDS["save_program"] == "log" and "save_program" in common.WRITE_FUNCTIONS
    schema = entry["inputSchema"]
    assert list(schema["properties"]) == ["name", "blocks", "start_on", "key"] and schema["required"] == ["name",
                                                                                                          "blocks"]
    props = schema["properties"]
    assert {k: props[k]["description"] for k in ("name", "start_on", "blocks")} == {
        k: FIELD_WORDS[k] for k in ("name", "start_on", "blocks")}
    block, day = schema["$defs"]["ProgramBlockIn"], schema["$defs"]["ProgramDayIn"]
    assert {k: v["description"] for k, v in block["properties"].items()} == {
        "weeks": FIELD_WORDS["weeks"], "days": FIELD_WORDS["days"]}
    assert {k: v["description"] for k, v in day["properties"].items()} == {
        "weekday": FIELD_WORDS["weekday"], "routine": FIELD_WORDS["routine"]}
    assert day["properties"]["weekday"]["type"] == "string" and "enum" not in day["properties"]["weekday"]
    assert block["additionalProperties"] is False and day["additionalProperties"] is False
    out = entry["outputSchema"]
    assert out["properties"]["id"]["description"] == ID_WORDS and out["additionalProperties"] is False
    routines_out = tools["get_routines"]["outputSchema"]
    assert routines_out["properties"]["program"]["description"] == PROGRAM_WORDS
    assert "program" not in routines_out.get("required", [])
    today = routines_out["$defs"]["ProgramToday"]["properties"]
    assert today["today"]["description"] == ("Today's routine by name; null on a rest day and when the program is not "
                                             "running.")
    assert today["words"]["description"] == "Where the program stands, in one line, ready to quote."


def test_get_routines_description_names_the_program_and_today_s_workout(http):
    words = registered_tools(http)["get_routines"]["description"]
    assert words.startswith("The person's saved routines and their program, each item with its plan, the weight to log "
                            "for a lift and last time. Call it when the person names a routine, such as log my push "
                            "day, or asks what today's workout is. To log one, ")


def test_a_save_through_mcp_passes_its_model_and_its_nested_refusals_name_the_block_and_the_day(writer):
    http, token, account = writer
    services.save_routine(account, {"name": "Push day", "lifts": PUSH}, "web")
    out = ok(http, token, "save_program", {"name": "PPL", "blocks": [{"weeks": 4, "days": [DAYS[0]]}]})
    assert out["created"] is True and out["program"]["weeks"] == 4
    bad = failed(http, token, "save_program", {"name": "PPL", "blocks": [{"weeks": 4, "days": [{**DAYS[0],
                                                                                                "time": "6am"}]}]})
    assert (bad["error"], bad["message"], bad["fix"]) == (
        "invalid_field", "Block 1, day 1: 'time' is not a field of a day.", "Use: weekday, routine.")
    bad = failed(http, token, "save_program", {"name": "PPL", "blocks": [{"weeks": 4, "days": [DAYS[0]], "x": 1}]})
    assert (bad["message"], bad["fix"]) == ("Block 1: 'x' is not a field of a block.", "Use: weeks, days.")
    bad = failed(http, token, "save_program", {"name": "PPL", "blocks": [{"weeks": 4, "days": [
        {"weekday": "weds", "routine": "Push day"}]}]})
    assert bad["message"] == "Block 1, day 1: 'weds' is not a weekday."


def test_the_trace_knows_every_new_name():
    assert {"start_on", "blocks", "weeks", "days", "weekday", "routine"} <= KNOWN_NAMES


def test_every_new_text_keeps_the_text_rules():
    for text in (SAVE_DESCRIPTION, *FIELD_WORDS.values(), ID_WORDS, PROGRAM_WORDS,
                 "Today's routine by name; null on a rest day and when the program is not running.",
                 "Where the program stands, in one line, ready to quote.",
                 common.KIND_LABELS["program"][1], common.ENTRY_OR_FOOD,
                 mcp_server.DELETE_KIND_DESCRIPTION, mcp_server.RESTORE_KIND_DESCRIPTION,
                 mcp_server.RESTORE_ID_DESCRIPTION):
        assert tr.check(text, tr.CLAUDE) == [], text
    for text in (f"Saved {SAID}", f"Replaced your program with {SAID}", f"Brought back and replaced {SAID}",
                 "PPL is already saved as given: nothing changed.", "PPL, week 3 of 8. Today: Pull day.",
                 "PPL, week 3 of 8. Today is a rest day. Next: Push day on Mon 23 Nov.",
                 "PPL starts on Mon 23 Nov. First: Push day.", "PPL ended on Sun 17 Jan, after 8 weeks.",
                 "PPL, week 1 of 1. Today is a rest day, and no routine day is left.", "Pull day is not saved now."):
        assert tr.check(text, tr.SUMMARY) == [], text
    for text in ("A program has no earlier values kept.", "Save it again with save_program, as it was.",
                 "A program is not edited here.", "Use save_program with the whole program.",
                 "Block 1, Wednesday: 'pul day' is not a saved routine. Saved: Push day, Pull day and Legs day.",
                 "Send a name as get_routines gives it, or save the routine first with save_routine.",
                 "Block 2: wednesday is there twice.", "Give each weekday once in a block.",
                 "A program runs 1 to 52 weeks, not 60.", "Send fewer weeks.",
                 "Block 1, day 2: 'weds' is not a weekday.", "Use monday to sunday.",
                 "start_on 2028-01-03 is more than 52 weeks after today, 2026-11-23.",
                 "Give a start at most 52 weeks on: 2027-11-22 at the latest.",
                 "Give a start from the last 2 years only; check the year.",
                 "Block 1 holds 1 to 7 days, not 0.", "Send 1 to 7 days in block 1, each weekday once.",
                 "A program holds 1 to 52 blocks, not 0.", "Send 1 to 52 blocks, each with its weeks and days.",
                 "Block 1: weeks 0 is less than 1.", "Give weeks on block 1 as 1 or more.",
                 "'pul day' is not a saved routine. No routine is saved yet.",
                 "Save the routine first with save_routine.",
                 "start_on 'soon' is not a date.", "Give the date as YYYY-MM-DD, or leave it out for today.",
                 "start_on 2024-11-21 is more than 2 years ago.", "Block 1, day 1: 'time' is not a field of a day.",
                 "Use: weekday, routine.", "Block 1: 'rest' is not a field of a block.", "Use: weeks, days.",
                 "'length' is not a field of a program.", "Use: name, start_on, blocks.",
                 "Block 1, Monday: routine is missing.", "Give a saved routine's name, as get_routines gives it.",
                 "Block 1, day 1: weekday is missing.", "Block 1: days is missing.",
                 "Give the days of block 1, each a weekday and a routine.", "blocks is missing.",
                 "Give blocks, each with its weeks and days.", "Block 1: it must hold weeks and days.",
                 "Send block 1 as weeks and days.", "Block 1, day 1: it must hold a weekday and a routine.",
                 "Send each day as weekday and routine."):
        assert tr.check(text, tr.MESSAGE) == [], text  # an error's message and fix
    for text in (*web.CLAUDE_CAN_LINES.values(), "Programs"):
        assert tr.check(text, tr.PAGE) == [], text


# ---------------------------------------------------------------- the migration (R8, the note's row)

def _schema(engine) -> dict:
    with engine.connect() as c:
        found = inspect(c)
        return {t: json.dumps([found.get_columns(t), found.get_indexes(t), found.get_foreign_keys(t),
                               found.get_unique_constraints(t)], default=str, sort_keys=True)
                for t in found.get_table_names()}


def test_0016_builds_the_table_on_sqlite_and_comes_down_leaving_no_other_change():
    """Up from 0015, down and up again, over written rows: a user at 0015 and that user's program at 0016. Down keeps
    the user and leaves 0015's schema; up again holds no program."""
    address = f"sqlite:///{(Path(tempfile.mkdtemp(dir=TEST_DIR)) / 'program.db').as_posix()}"
    cfg = alembic_config(address)
    command.upgrade(cfg, "0015")
    engine = make_engine(address)
    stamp = "2026-11-23 09:00:00.000000"
    try:
        with engine.begin() as c:
            c.execute(sql_text('INSERT INTO "user" (id, provider, subject, created_at, updated_at, last_seen_at) '
                               "VALUES (7, 'test', 'kept', :t, :t, :t)"), {"t": stamp})
        before, master = _schema(engine), _master(engine)
        command.upgrade(cfg, "0016")
        with engine.begin() as c:
            c.execute(sql_text("INSERT INTO program (user_id, name, start_on, blocks, created_at, updated_at) "
                               "VALUES (7, 'PPL', '2026-11-23', :b, :t, :t)"),
                      {"b": json.dumps([{"weeks": 8, "days": [{"weekday": "monday", "routine": "Push day"}]}]),
                       "t": stamp})
            assert c.execute(sql_text("SELECT count(*) FROM program")).scalar() == 1
        with engine.connect() as c:
            found = inspect(c)
            assert [(col["name"], str(col["type"]), col["nullable"]) for col in found.get_columns("program")] == [
                ("id", "INTEGER", False), ("user_id", "INTEGER", False), ("name", "VARCHAR(64)", False),
                ("start_on", "DATE", False), ("blocks", "TEXT", False), ("created_at", "DATETIME", False),
                ("updated_at", "DATETIME", False), ("deleted_at", "DATETIME", True)]
            assert found.get_unique_constraints("program") == [{"name": "uq_program_user_id",
                                                                "column_names": ["user_id"]}]
            [key] = found.get_foreign_keys("program")
            assert (key["name"], key["referred_table"], key["constrained_columns"], key["options"]) == (
                "fk_program_user_id_user", "user", ["user_id"], {"ondelete": "CASCADE"})
            assert found.get_indexes("program") == []
        after = _schema(engine)
        assert {t: v for t, v in after.items() if t != "program"} == before
        engine.dispose()
        command.downgrade(cfg, "0015")
        assert _schema(engine) == before and _master(engine) == master
        with engine.connect() as c:
            assert c.execute(sql_text('SELECT id, subject FROM "user"')).all() == [(7, "kept")]
        engine.dispose()
        command.upgrade(cfg, "0016")
        with engine.connect() as c:
            assert c.execute(sql_text("SELECT count(*) FROM program")).scalar() == 0
            assert c.execute(sql_text("SELECT version_num FROM alembic_version")).scalar() == "0016"
    finally:
        engine.dispose()


def test_the_model_is_no_entry_table_and_its_columns_are_r8_s_less_saved_on():
    assert [c.name for c in Program.__table__.columns] == ["id", "user_id", "name", "start_on", "blocks", "created_at",
                                                           "updated_at", "deleted_at"]
    assert not hasattr(Program, "logged_by") and "program" not in services.entry_kinds()
