"""Every write safe to send twice (docs/contracts/idempotency.md): a caller's key on any write, the first answer
returned again, a hidden key on every form, a guard for a new goal.

Calls go through the real HTTP path (TestClient), so the wrapper, the worker threads and the context are the ones
a real call has. USDA is replaced at usda.urlopen, as in tests/test_usda.py. A row's age is made by writing its
created_at, never by waiting.
"""

import contextvars
import hashlib
import json
import re
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import event, inspect, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app import calllog, db, mcp_server, rules, services, web, web_auth
from app.db import make_engine
from app.mcp_schemas import OwnerCall, SetGoalResult
from app.models import Base, Bodyweight, CallLog, FoodEntry, Goal, LiftSession, UsdaFood, User, WriteKey
from app.services import common, goals, lifts, usda

from . import text_rules as tr
from .conftest import TEST_DIR, alembic_config, claims, sign
from .test_calllog import MARKER, rows
from .test_mcp import (
    OPTIONAL_ARGUMENTS, REQUIRED, call, every_table, failed, ok, service_input, set_switch, spy_on_services, squat,
    tool_list,
)
from .test_pages_text import Rendered, error_page_findings
from .test_trace import today
from .test_usda import KEY as USDA_KEY, Fake, banana
from .test_web import client, only_user, plant_session, post, seam, session_of, sign_in, web_client  # noqa: F401

WRITES = common.WRITE_FUNCTIONS
K = "retry-0001"
FAR = "2099-01-31"


def keys() -> list[WriteKey]:
    with db.session_scope() as s:
        return list(s.scalars(select(WriteKey).order_by(WriteKey.id)))


def count(model) -> int:
    with db.session_scope() as s:
        return len(s.scalars(select(model)).all())


def day(offset: int = 0) -> str:
    return (db.utcnow().date() + timedelta(days=offset)).isoformat()


def age(model, row_id: int, **back) -> None:
    """Make a row older by writing its created_at."""
    with db.session_scope() as s:
        s.execute(update(model).where(model.id == row_id).values(created_at=db.utcnow() - timedelta(**back)))


def a_weigh_in(account: int) -> int:
    return services.log_bodyweight(account, {"kg": 80}, "claude")["id"]


def a_deleted_weigh_in(account: int) -> int:
    entry = a_weigh_in(account)
    assert services.delete_entry(account, "bodyweight", entry)["deleted"] is True
    return entry


# One call of each write function that writes, as arguments. allow_repeat on the logs: the 10-minute guard is
# off, so only the key stands between a second send and a second entry.
CALLS = {
    "log_lifts": lambda account: {"sets": squat(100), "allow_repeat": True},
    "log_bodyweight": lambda account: {"kg": 80.5, "allow_repeat": True},
    "log_measurement": lambda account: {"kind": "waist", "value": 84.5, "allow_repeat": True},
    "log_food": lambda account: {"entries": [{"description": "Dal", "kcal": 180}], "allow_repeat": True},
    "save_food": lambda account: {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120},
    "log_cardio": lambda account: {"exercise": "Treadmill Walk", "minutes": 30, "allow_repeat": True},
    "set_goal": lambda account: {"kind": "bodyweight", "target_value": 75, "start_value": 80, "target_date": FAR},
    "update_settings": lambda account: {"time_zone": "Asia/Kolkata"},
    "edit_entry": lambda account: {"kind": "bodyweight", "entry_id": a_weigh_in(account), "kg": 81},
    "delete_entry": lambda account: {"kind": "bodyweight", "entry_id": a_weigh_in(account)},
    "restore_entry": lambda account: {"kind": "bodyweight", "entry_id": a_deleted_weigh_in(account)},
    "import_entries": lambda account: {"rows": [{"kind": "bodyweight", "date": day(-3), "weight": 80}],
                                       "weight_unit": "kg", "dry_run": False},
}


@pytest.fixture
def writer(http):
    """(client, token, account id) of a person with a time zone, whose assistant may delete."""
    token = sign()
    account = ok(http, token, "whoami")["account_id"]
    assert "error" not in services.update_settings(account, {"time_zone": "UTC"}, "web")
    set_switch(account, "log_delete")
    return http, token, account


def test_the_table_of_calls_covers_the_twelve_write_functions():
    assert set(CALLS) == set(WRITES) and len(WRITES) == 12


# ---------------------------------------------------------------- decisions 4, 5 and 14: the same call again

@pytest.mark.parametrize("name", WRITES)
def test_a_write_sent_twice_with_one_key_writes_once_answers_the_same_and_its_second_row_reads_repeat(writer, name):
    http, token, account = writer
    arguments = {**CALLS[name](account), "key": K}
    before = every_table()
    first = call(http, token, name, arguments)
    after = every_table()
    second = call(http, token, name, arguments)
    assert first["isError"] is False and after != before  # the first send wrote
    assert second == first  # the text word for word, its parse as the structured content, and not an error
    assert every_table() == after  # nothing was written twice: the key's own row was complete before the first reply
    a, b = rows()[-2:]
    assert (a.function, a.outcome, a.ok) == (name, "written", True)
    assert (b.function, b.outcome, b.ok, b.error_code, b.step) == (name, "repeat", True, None, None)
    assert b.result_bytes == a.result_bytes and "key" in a.tried.split() and "key" in b.tried.split()
    [kept] = keys()
    assert (kept.user_id, kept.key, kept.function) == (account, K, name)
    assert kept.answer == first["content"][0]["text"] and re.fullmatch(r"[0-9a-f]{64}", kept.fingerprint)


def test_the_same_arguments_in_another_order_are_the_same_call(writer):
    http, token, _ = writer
    first = call(http, token, "log_measurement", {"kind": "waist", "value": 84, "key": K})
    again = call(http, token, "log_measurement", {"key": K, "value": 84, "kind": "waist"})
    assert again == first and rows()[-1].outcome == "repeat"
    assert common.fingerprint("f", {"a": 1, "b": [2], "key": "x"}) == common.fingerprint("f", {"b": [2], "a": 1})
    assert common.fingerprint("f", {"a": 1}) != common.fingerprint("g", {"a": 1})


def test_the_key_row_holds_a_fingerprint_and_never_an_argument(writer):
    http, token, _ = writer
    call(http, token, "log_lifts", {"sets": squat(100), "notes": MARKER, "key": K})
    [kept] = keys()
    assert all(MARKER not in str(getattr(kept, c.key)) for c in WriteKey.__table__.columns if c.name != "answer")
    assert MARKER in kept.answer  # the first answer is the person's own data, kept 24 hours


# ---------------------------------------------------------------- two sends at once

def test_two_sends_at_once_with_one_key_write_once_and_both_get_the_first_answer(writer):
    http, token, _ = writer
    arguments = {"sets": squat(100), "allow_repeat": True, "key": K}
    barrier = threading.Barrier(4)
    answers: list[dict] = []

    def send() -> None:
        barrier.wait()
        answers.append(call(http, token, "log_lifts", arguments))

    threads = [threading.Thread(target=send) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(answers) == 4 and count(LiftSession) == 1
    assert all(a == answers[0] and a["isError"] is False for a in answers)  # never already_done
    assert sorted(r.outcome for r in rows() if r.function == "log_lifts") == ["repeat"] * 3 + ["written"]
    assert len(keys()) == 1


def test_a_retry_that_arrives_while_the_first_send_is_still_writing_gets_the_first_answer(writer, monkeypatch):
    http, token, _ = writer
    arguments = {"sets": squat(100), "allow_repeat": True, "key": K}
    inside, go = threading.Event(), threading.Event()
    real = lifts.check_write_limit
    answers: dict[str, dict] = {}

    def held(s, user_id):  # right after lock_user: the first send holds the lock and has claimed the key
        inside.set()
        go.wait(timeout=3)
        return real(s, user_id)

    def send(name: str) -> None:
        answers[name] = call(http, token, "log_lifts", arguments)

    monkeypatch.setattr(lifts, "check_write_limit", held)
    first, retry = (threading.Thread(target=send, args=(name,)) for name in ("first", "retry"))
    first.start()
    assert inside.wait(timeout=10)
    retry.start()
    retry.join(timeout=0.5)
    assert retry.is_alive() and "retry" not in answers  # it waits for the first send: it cannot write beside it
    go.set()
    for t in (first, retry):
        t.join(timeout=60)
    assert answers["retry"] == answers["first"] and answers["first"]["isError"] is False  # never already_done
    assert count(LiftSession) == 1 and len(keys()) == 1
    assert sorted(r.outcome for r in rows() if r.function == "log_lifts") == ["repeat", "written"]


def test_a_retry_that_arrives_before_the_first_answer_is_kept_waits_for_it_and_gets_it(writer):
    """The first send has committed and not yet kept its answer: its row is there, its answer empty. The retry
    is stopped under the lock, reads the row again every 100 ms, and answers with the first answer once it is in."""
    http, token, account = writer
    arguments = {"sets": squat(100), "allow_repeat": True, "key": K}
    first = call(http, token, "log_lifts", arguments)
    text = first["content"][0]["text"]
    with db.session_scope() as s:
        s.execute(update(WriteKey).values(answer=None))

    def keep() -> None:
        common.settle(account, {"row": keys()[0].id}, json.loads(text), text)

    timer = threading.Timer(0.25, keep)
    timer.start()
    retry = call(http, token, "log_lifts", arguments)
    timer.join()
    assert retry == first and count(LiftSession) == 1 and rows()[-1].outcome == "repeat"


def test_a_send_stopped_by_the_key_of_a_first_send_that_is_then_refused_runs_the_work_itself_and_writes_once(
        writer, monkeypatch):
    """The first send claims the key and is refused under the lock: its row is there from its commit until it is
    freed. A second send of the same key is stopped under the lock by that row. Once the row has gone it runs the
    work once more, so it gets its own answer, never the first one's refusal and never already_done."""
    http, token, _ = writer
    arguments = {"sets": squat(100), "allow_repeat": True, "key": K}
    inside, go, stopped = threading.Event(), threading.Event(), threading.Event()
    real_limit, real_waited, real_settle = lifts.check_write_limit, common._waited, common.settle
    limits: list[int] = []
    answers: dict[str, dict] = {}

    def refused_once(s, user_id):  # the first send: it holds the lock with the key claimed, then is refused
        limits.append(user_id)
        if len(limits) > 1:
            return real_limit(s, user_id)
        inside.set()
        go.wait(timeout=10)
        return {"error": "too_many_writes", "message": "m", "fix": "f", "wait_minutes": 1}

    def waited(user_id, claim, kept):  # the second send, stopped under the lock by the first one's row
        stopped.set()
        return real_waited(user_id, claim, kept)

    def settle(user_id, claim, result, text):  # the refusal's row goes only once the second send has met it
        if "error" in result:
            stopped.wait(timeout=10)
        return real_settle(user_id, claim, result, text)

    def send(name: str) -> None:
        answers[name] = call(http, token, "log_lifts", arguments)

    monkeypatch.setattr(lifts, "check_write_limit", refused_once)
    monkeypatch.setattr(common, "_waited", waited)
    monkeypatch.setattr(common, "settle", settle)
    first, second = (threading.Thread(target=send, args=(name,)) for name in ("first", "second"))
    first.start()
    assert inside.wait(timeout=10)
    second.start()
    go.set()
    for t in (first, second):
        t.join(timeout=60)
    assert answers["first"]["structuredContent"]["error"] == "too_many_writes"
    assert stopped.is_set() and answers["second"]["isError"] is False  # its own answer, from its second run
    assert answers["second"]["structuredContent"]["duplicate"] is False and count(LiftSession) == 1
    assert [k.answer for k in keys()] == [answers["second"]["content"][0]["text"]]
    assert sorted(r.outcome for r in rows() if r.function == "log_lifts") == ["refused", "written"]


# ---------------------------------------------------------------- decision 7: what does not keep its key

def test_a_refused_call_leaves_its_key_free(writer):
    http, token, _ = writer
    refused = failed(http, token, "log_lifts", {"sets": squat(100), "date": "soon", "key": K})
    assert refused["error"] == "invalid_date" and keys() == []  # refused under the lock: its row is deleted
    assert ok(http, token, "log_lifts", {"sets": squat(100), "key": K})["duplicate"] is False
    assert [r.outcome for r in rows()[-2:]] == ["refused", "written"] and count(LiftSession) == 1


def test_a_preview_leaves_its_key_free(writer):
    http, token, _ = writer
    sent = {"rows": [{"kind": "bodyweight", "date": day(-3), "weight": 80}], "weight_unit": "kg", "key": K}
    assert ok(http, token, "import_entries", sent)["dry_run"] is True and keys() == []
    assert ok(http, token, "import_entries", sent)["dry_run"] is True  # the same preview again: it runs again
    written = ok(http, token, "import_entries", {**sent, "dry_run": False})  # and the key is free for the write
    assert written["row_outcomes"]["written"] == 1 and count(Bodyweight) == 1
    assert [r.outcome for r in rows()[-3:]] == ["preview", "preview", "written"]


def test_a_crash_leaves_its_key_free(writer, monkeypatch):
    http, token, _ = writer

    def broken(*args, **kwargs):
        raise ValueError(MARKER)

    with monkeypatch.context() as patched:
        patched.setattr(rules, "entry_date", broken)  # inside log_bodyweight's transaction, after the lock
        crashed = failed(http, token, "log_bodyweight", {"kg": 80, "key": K})
    assert crashed["error"] == "internal_error" and keys() == []  # the row rolled back with the write
    assert ok(http, token, "log_bodyweight", {"kg": 80, "key": K})["duplicate"] is False
    assert [r.outcome for r in rows()[-2:]] == ["failed", "written"] and count(Bodyweight) == 1


def test_a_duplicate_inside_the_10_minutes_keeps_its_key_and_its_answer(writer):
    http, token, _ = writer
    ok(http, token, "log_bodyweight", {"kg": 80})
    duplicate = call(http, token, "log_bodyweight", {"kg": 80, "key": K})
    assert duplicate["structuredContent"]["duplicate"] is True
    assert call(http, token, "log_bodyweight", {"kg": 80, "key": K}) == duplicate
    assert [r.outcome for r in rows()[-3:]] == ["written", "duplicate", "repeat"]


# ---------------------------------------------------------------- decision 6: a key used for another call

def test_a_key_sent_with_another_call_is_refused_and_nothing_is_written(writer):
    http, token, _ = writer
    first = call(http, token, "log_bodyweight", {"kg": 80, "key": K})
    before = every_table()
    for name, arguments in (("log_bodyweight", {"kg": 81, "key": K}), ("log_lifts", {"sets": squat(100), "key": K})):
        refused = failed(http, token, name, arguments)
        assert refused == {"error": "key_reused", "message": "This key was used for another call.",
                           "fix": "Send a new key for a new write."}
        row = rows()[-1]
        assert (row.ok, row.error_code, row.outcome, row.step) == (False, "key_reused", "refused", "key")
    assert every_table() == before
    assert call(http, token, "log_bodyweight", {"kg": 80, "key": K}) == first  # the first call's key still answers it


def test_under_the_lock_a_key_kept_for_another_call_is_refused_too(make_user):
    """The look with no lock answers a kept key first. This goes straight to the work, as a send does that passed
    the first look while the other send was still writing."""
    user = make_user()
    mine = common.new_claim("log_bodyweight", K, common.fingerprint("log_bodyweight", {"kg": 80}))
    assert common.keyed(user.id, mine, lambda: services.log_bodyweight(user.id, {"kg": 80}, "claude"))[1] is None

    def under_the_lock(function: str, sent: dict):
        claim = common.new_claim(function, K, common.fingerprint(function, sent))
        held = common._CLAIM.set(claim)
        try:
            with pytest.raises(common.KeyKept) as signal:
                services.log_bodyweight(user.id, {"kg": 82}, "claude")
        finally:
            common._CLAIM.reset(held)
        return common._waited(user.id, claim, signal.value.kept)

    assert under_the_lock("log_lifts", {})[0]["error"] == "key_reused"
    assert under_the_lock("log_bodyweight", {"kg": 81})[0]["error"] == "key_reused"
    assert count(Bodyweight) == 1 and len(keys()) == 1  # the stopped sends wrote nothing


def test_one_person_s_key_never_reaches_another_person_s_row(http):
    asha, ben = sign(claims(sub="user_01ASHA")), sign(claims(sub="user_01BEN"))
    hers = call(http, asha, "log_bodyweight", {"kg": 60, "key": K})
    his = call(http, ben, "log_lifts", {"sets": squat(100), "key": K})  # the same key, another person, another call
    assert hers["isError"] is False and his["isError"] is False
    assert call(http, ben, "log_lifts", {"sets": squat(100), "key": K}) == his
    assert call(http, asha, "log_bodyweight", {"kg": 60, "key": K}) == hers
    assert sorted((k.function, k.key) for k in keys()) == [("log_bodyweight", K), ("log_lifts", K)]
    assert len({k.user_id for k in keys()}) == 2 and count(Bodyweight) == 1 and count(LiftSession) == 1


# ---------------------------------------------------------------- decision 8: the first answer was lost

def test_a_kept_key_whose_answer_was_not_kept_is_already_done_and_writes_nothing(writer, monkeypatch):
    http, token, _ = writer
    arguments = {"sets": squat(100), "allow_repeat": True, "key": K}
    ok(http, token, "log_lifts", arguments)
    with db.session_scope() as s:  # as when the process stopped between the write and keeping its answer
        s.execute(update(WriteKey).values(answer=None))
    monkeypatch.setattr(common, "ANSWER_WAIT", 0.2)
    lost = failed(http, token, "log_lifts", arguments)
    assert lost == {"error": "already_done", "message": "This call already ran. Its answer was not kept.",
                    "fix": "Read it back with get_log. Send a new key for a new write."}
    row = rows()[-1]
    assert (row.ok, row.error_code, row.outcome, row.step) == (False, "already_done", "refused", "key")
    assert count(LiftSession) == 1 and common.ANSWER_EVERY == 0.1 and common.ANSWER_MAX == 65_536


def lose_the_answer(**back) -> None:
    """Every kept key loses its answer, as when the process stopped before keeping it, and is this old."""
    with db.session_scope() as s:
        s.execute(update(WriteKey).values(answer=None, created_at=db.utcnow() - timedelta(**back)))


def no_sleep(monkeypatch) -> list[float]:
    """The key's waits, recorded and never slept: common's own clock, so nothing else is touched."""
    slept: list[float] = []
    monkeypatch.setattr(common, "time", SimpleNamespace(monotonic=time.monotonic, sleep=slept.append))
    return slept


def looks(statements: list[str]) -> list[str]:
    return [s for s in statements if "write_key" in s and s.lstrip().upper().startswith("SELECT")]


# The read already_done names for each write: get_log for any other. A delete or a restore takes a saved food too,
# and get_log shows no saved food.
READS = {"set_goal": "get_goals", "update_settings": "whoami", "save_food": "find_food",
         "delete_entry": "get_log, or find_food for a saved food",
         "restore_entry": "get_log, or find_food for a saved food"}


@pytest.mark.parametrize("name", WRITES)
def test_already_done_names_the_read_that_shows_what_the_write_did(writer, name):
    http, token, account = writer
    arguments = {**CALLS[name](account), "key": K}
    assert call(http, token, name, arguments)["isError"] is False
    lose_the_answer(seconds=61)
    read = READS.get(name, "get_log")
    assert failed(http, token, name, arguments) == {
        "error": "already_done", "message": "This call already ran. Its answer was not kept.",
        "fix": f"Read it back with {read}. Send a new key for a new write."}
    named = re.findall(r"whoami|[a-z]+_[a-z]+", read)  # each a function there is, which a read-only assistant may call
    assert named and all(mcp_server.NEEDS[function] == "read" for function in named)
    assert sum(common.READ_BACK.get(write, "get_log") == "get_log" for write in WRITES) == 7


@pytest.mark.parametrize("name", sorted(READS))
def test_a_wait_that_ends_with_no_answer_names_the_function_s_own_read_too(writer, monkeypatch, name):
    """A young row with no answer: the send is stopped under the lock and waits. Here the wait is over at once."""
    http, token, account = writer
    arguments = {**CALLS[name](account), "key": K}
    assert call(http, token, name, arguments)["isError"] is False
    lose_the_answer(seconds=5)
    monkeypatch.setattr(common, "ANSWER_WAIT", 0)
    lost = failed(http, token, name, arguments)
    assert (lost["error"], lost["fix"]) == ("already_done",
                                            f"Read it back with {READS[name]}. Send a new key for a new write.")


def test_a_send_stopped_twice_by_a_row_that_goes_each_time_is_already_done_and_names_its_own_read(
        make_user, monkeypatch):
    """The end of keyed(): the work was stopped under the lock, the row went while the send waited, and the work's
    second run was stopped the same way. It is not run a third time."""
    user = make_user()
    slept = no_sleep(monkeypatch)
    claim = common.new_claim("save_food", K, "f" * 64)
    runs: list[int] = []

    def stopped() -> dict:  # by a young row with no answer, which is gone at the next look
        runs.append(len(runs))
        raise common.KeyKept("save_food", "f" * 64, None, db.utcnow())

    lost, text = common.keyed(user.id, claim, stopped)
    assert (lost["error"], text, runs) == ("already_done", None, [0, 1]) and slept == [common.ANSWER_EVERY] * 2
    assert lost["fix"] == "Read it back with find_food. Send a new key for a new write." and keys() == []


# ---------------------------------------------------------------- a lost answer is not waited for

def test_a_lost_answer_over_60_seconds_old_is_already_done_in_one_look_with_no_wait_and_no_work(
        writer, statements, monkeypatch):
    http, token, _ = writer
    arguments = {"sets": squat(100), "allow_repeat": True, "key": K}
    ok(http, token, "log_lifts", arguments)
    lose_the_answer(seconds=61)
    slept, received = no_sleep(monkeypatch), spy_on_services(monkeypatch)
    statements.clear()
    assert failed(http, token, "log_lifts", arguments)["error"] == "already_done"
    assert len(looks(statements)) == 1 and slept == [] and received == {}  # before the work, so before the lock
    row = rows()[-1]
    assert (row.error_code, row.outcome, row.step) == ("already_done", "refused", "key") and common.ANSWER_LOST == 60


def test_the_60_seconds_are_counted_from_the_row_s_start_and_a_row_exactly_that_old_still_waits(monkeypatch):
    now = db.utcnow()
    monkeypatch.setattr(db, "utcnow", lambda: now)
    claim = common.new_claim("save_food", K, "f" * 64)

    def answered(**back):
        return common._answer(claim, ("save_food", "f" * 64, None, now - timedelta(**back)))

    assert answered(seconds=59) is None and answered(seconds=60) is None
    lost, text = answered(seconds=60, microseconds=1)
    assert (lost["error"], text) == ("already_done", None) and "find_food" in lost["fix"]
    assert common._answer(claim, ("save_food", "f" * 64, "{}", now - timedelta(hours=23))) == (None, "{}")


def test_under_the_lock_a_lost_answer_over_60_seconds_old_is_already_done_with_no_wait(make_user, monkeypatch):
    """A send that passed the first look while the row was not there yet, or straight to the work as here."""
    user = make_user()
    row = plant(user.id, K, answer=None, seconds=61)
    slept = no_sleep(monkeypatch)
    claim = common.new_claim("log_lifts", K, "f" * 64)
    held = common._CLAIM.set(claim)
    try:
        with pytest.raises(common.KeyKept) as signal:
            services.log_lifts(user.id, {"sets": squat(100)}, "claude")
    finally:
        common._CLAIM.reset(held)
    lost, text = common._waited(user.id, claim, signal.value.kept)
    assert (lost["error"], text) == ("already_done", None) and slept == []
    assert count(LiftSession) == 0 and [k.id for k in keys()] == [row]


def test_a_lost_answer_over_60_seconds_old_asks_usda_nothing(make_client, monkeypatch):
    http = make_client(usda_api_key=USDA_KEY)
    token = sign()
    fake = Fake(banana(shape="detail"))
    monkeypatch.setattr(usda, "urlopen", fake)
    arguments = {"entries": [{"usda_fdc_id": 173944, "grams": 100}], "allow_repeat": True, "key": K}
    assert call(http, token, "log_food", arguments)["isError"] is False and len(fake.requests) == 1
    lose_the_answer(seconds=61)
    with db.session_scope() as s:  # the cache forgets the food: a lookup now would ask USDA again
        s.query(UsdaFood).delete()
    for _ in range(3):
        assert failed(http, token, "log_food", arguments)["error"] == "already_done"
    assert len(fake.requests) == 1 and count(FoodEntry) == 1 and count(UsdaFood) == 0


def test_a_row_59_seconds_old_with_no_answer_is_still_waited_for_and_gets_the_answer_when_it_arrives(
        writer, monkeypatch):
    http, token, account = writer
    arguments = {"sets": squat(100), "allow_repeat": True, "key": K}
    first = call(http, token, "log_lifts", arguments)
    text = first["content"][0]["text"]
    now = db.utcnow()
    monkeypatch.setattr(db, "utcnow", lambda: now)  # the row stays 59 seconds old however long this test takes
    lose_the_answer(seconds=59)
    slept: list[float] = []

    def sleep(seconds: float) -> None:  # while the retry waits, the first send keeps its answer
        slept.append(seconds)
        common.settle(account, {"row": keys()[0].id}, json.loads(text), text)

    monkeypatch.setattr(common, "time", SimpleNamespace(monotonic=time.monotonic, sleep=sleep))
    retry = call(http, token, "log_lifts", arguments)
    assert retry == first and slept == [common.ANSWER_EVERY] and count(LiftSession) == 1
    assert rows()[-1].outcome == "repeat"


def test_130_sends_of_a_key_whose_answer_was_lost_hold_no_thread_and_reach_no_lock(writer, statements, monkeypatch):
    """The review's probe A1. Before, each such send ran the work up to the lock and then looked every 100 ms for
    2 seconds. The write limit still never refuses one, and each one's row still counts toward it."""
    http, token, _ = writer
    monkeypatch.setattr(calllog, "_swept_keys", db.utcnow())  # the sweep is not a send: none in this test
    arguments = {"sets": squat(100), "allow_repeat": True, "key": K}
    ok(http, token, "log_lifts", arguments)
    lose_the_answer(seconds=61)
    slept = no_sleep(monkeypatch)
    statements.clear()
    codes = {call(http, token, "log_lifts", arguments)["structuredContent"].get("error") for _ in range(130)}
    assert codes == {"already_done"} and slept == [] and len(looks(statements)) == 130
    assert not [s for s in statements if "write_key" in s and not s.lstrip().upper().startswith("SELECT")]
    assert failed(http, token, "log_bodyweight", {"kg": 80})["error"] == "too_many_writes"  # 132 rows in the window
    assert count(LiftSession) == 1 and count(Bodyweight) == 0


def test_an_answer_of_exactly_64_kb_is_kept_and_one_byte_more_is_not(make_user):
    user = make_user()
    most = "é" * (common.ANSWER_MAX // 2)  # 65,536 bytes of UTF-8 in 32,768 characters: bytes are counted
    for key, text in (("size-exact", most), ("size-over1", most + "a")):
        common.settle(user.id, {"row": plant(user.id, key, answer=None, minutes=1)}, {"kind": "goal"}, text)
    assert [(k.key, k.answer) for k in keys()] == [("size-exact", most), ("size-over1", None)]
    assert len(most.encode("utf-8")) == 65_536 == common.ANSWER_MAX


def test_an_answer_too_large_to_keep_is_already_done_on_the_next_send(writer, monkeypatch):
    http, token, _ = writer
    monkeypatch.setattr(common, "ANSWER_MAX", 10)
    monkeypatch.setattr(common, "ANSWER_WAIT", 0)
    arguments = {"sets": squat(100), "allow_repeat": True, "key": K}
    assert ok(http, token, "log_lifts", arguments)["duplicate"] is False
    assert [k.answer for k in keys()] == [None]
    assert failed(http, token, "log_lifts", arguments)["error"] == "already_done" and count(LiftSession) == 1


def test_a_write_whose_reply_failed_is_already_done_on_the_next_send(writer, monkeypatch):
    http, token, _ = writer
    real = services.log_bodyweight
    monkeypatch.setattr(common, "ANSWER_WAIT", 0)
    with monkeypatch.context() as patched:  # a lone surrogate: the write lands and the reply cannot be sent
        patched.setattr(services, "log_bodyweight", lambda *args: {**real(*args), "summary": chr(0xD83D)})
        assert failed(http, token, "log_bodyweight", {"kg": 80, "key": K})["error"] == "internal_error"
    assert [k.answer for k in keys()] == [None] and rows()[-1].outcome == "written"
    assert failed(http, token, "log_bodyweight", {"kg": 80, "key": K})["error"] == "already_done"
    assert count(Bodyweight) == 1


def test_a_key_that_cannot_be_settled_is_logged_by_type_and_swallowed(make_user, monkeypatch, logs):
    user = make_user()
    row = plant(user.id, K, answer=None, minutes=1)

    def down():
        raise OperationalError("UPDATE", {}, Exception(MARKER))

    with monkeypatch.context() as patched:
        patched.setattr(db, "session_scope", down)
        assert common.settle(user.id, {"row": row}, {"kind": "goal"}, "{}") is None  # keeping the answer
        assert common.settle(user.id, {"row": row}, {"error": "invalid_date"}, None) is None  # freeing the key
    assert logs.text.count("A write's key could not be settled: OperationalError") == 2 and MARKER not in logs.text
    assert [k.answer for k in keys()] == [None]


# ---------------------------------------------------------------- decision 9: 24 hours

def test_an_expired_key_is_never_honoured_swept_or_not(writer, monkeypatch):
    http, token, _ = writer
    monkeypatch.setattr(calllog, "_swept_keys", db.utcnow())  # no sweep in this test
    arguments = {"sets": squat(100), "allow_repeat": True, "key": K}
    first = ok(http, token, "log_lifts", arguments)
    age(WriteKey, keys()[0].id, hours=23, minutes=59)
    assert ok(http, token, "log_lifts", arguments) == first and count(LiftSession) == 1  # still kept
    age(WriteKey, keys()[0].id, hours=25)
    assert len(keys()) == 1  # not swept
    again = ok(http, token, "log_lifts", arguments)
    assert again["id"] != first["id"] and count(LiftSession) == 2 and rows()[-1].outcome == "written"
    [kept] = keys()  # the old row went under the lock, and the new write holds the key
    assert db.utcnow() - kept.created_at < timedelta(minutes=1) and json.loads(kept.answer) == again
    assert calllog.KEY_HOURS == 24 and common.KEY_HOURS == 24


def plant(user_id: int, key: str, answer: str | None = "{}", **back) -> int:
    with db.session_scope() as s:
        row = WriteKey(user_id=user_id, key=key, function="log_lifts", fingerprint="f" * 64, answer=answer,
                       created_at=db.utcnow() - timedelta(**back))
        s.add(row)
        s.flush()
        return row.id


def test_the_sweep_deletes_a_key_25_hours_old_keeps_one_23_hours_old_and_runs_once_an_hour(writer, monkeypatch):
    http, token, account = writer
    monkeypatch.setattr(calllog, "_swept_keys", None)  # as on the first call after the process starts
    monkeypatch.setattr(calllog, "_swept", db.utcnow())  # the call rows were swept a moment ago: not again
    old, kept = plant(account, "old-key-01", hours=25), plant(account, "new-key-01", hours=23)
    old_call = CallLog(function="whoami", started_at=db.utcnow() - timedelta(days=91), ms=1, ok=True, result_bytes=1)
    with db.session_scope() as s:
        s.add(old_call)
    ok(http, token, "whoami")
    assert [k.id for k in keys()] == [kept] and old_call.id in {r.id for r in rows()}  # the keys only
    assert ok(http, token, "log_lifts", {"sets": squat(100), "key": "old-key-01"})["duplicate"] is False  # free
    again = plant(account, "old-key-02", hours=25)
    ok(http, token, "whoami")
    assert again in {k.id for k in keys()}  # within the hour: no sweep
    later = db.utcnow() + timedelta(minutes=61)
    monkeypatch.setattr(db, "utcnow", lambda: later)
    ok(http, token, "whoami")
    assert again not in {k.id for k in keys()} and old_call.id in {r.id for r in rows()}
    assert (calllog.KEY_SWEEP_HOURS, calllog.SWEEP_HOURS) == (1, 24)


@pytest.mark.parametrize("failing, what", [("write_key", "write keys"), ("call_log", "call log")])
def test_one_delete_of_the_sweep_that_fails_never_costs_the_other(make_user, monkeypatch, logs, failing, what):
    user = make_user()
    old_key = plant(user.id, "old-key-01", hours=25)
    old_call = CallLog(function="whoami", started_at=db.utcnow() - timedelta(days=91), ms=1, ok=True, result_bytes=1)
    with db.session_scope() as s:
        s.add(old_call)
    monkeypatch.setattr(calllog, "_swept", None)
    monkeypatch.setattr(calllog, "_swept_keys", None)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(f"DELETE FROM {failing}"):
            raise OperationalError("DELETE", {}, Exception(MARKER))

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", fail)
    try:
        calllog.sweep()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert [k.id for k in keys()] == ([old_key] if failing == "write_key" else [])  # the other delete went through
    assert [r.id for r in rows()] == ([old_call.id] if failing == "call_log" else [])
    assert [r.getMessage() for r in logs.records if "swept" in r.getMessage()] == [
        f"The {what} could not be swept: OperationalError"]
    assert MARKER not in logs.text


# ---------------------------------------------------------------- decisions 1 and 10: the argument

@pytest.mark.parametrize("key", ["short-7", "x" * 65, "", "has a space", "has.a.dot", "has/a/slash", "abcdefgh\n",
                                 "١٢٣٤٥٦٧٨", "café-au-lait"])
def test_a_key_of_another_shape_is_invalid_key_and_nothing_runs(writer, monkeypatch, key):
    http, token, _ = writer
    received = spy_on_services(monkeypatch)
    refused = failed(http, token, "log_bodyweight", {"kg": 80, "key": key})
    assert refused == {"error": "invalid_key", "message": "key must be 8 to 64 letters, digits, - or _.",
                       "fix": "Send 8 to 64 letters, digits, - or _, or leave the key out."}
    row = rows()[-1]
    assert (row.error_code, row.outcome, row.step) == ("invalid_key", "refused", "key")
    assert received == {} and keys() == []


@pytest.mark.parametrize("key", ["abcd1234", "x" * 64, "A-b_C-d_", "0123456789",
                                 "f47ac10b-58cc-4372-a567-0e02b2c3d479"])
def test_a_key_of_8_to_64_letters_digits_hyphens_and_underscores_is_taken(writer, key):
    http, token, _ = writer
    assert ok(http, token, "log_bodyweight", {"kg": 80, "key": key})["duplicate"] is False
    assert [k.key for k in keys()] == [key]


def test_a_key_sent_as_null_or_left_out_runs_as_before(writer):
    http, token, _ = writer
    plain = ok(http, token, "log_bodyweight", {"kg": 80})
    null = ok(http, token, "log_bodyweight", {"kg": 80, "key": None})
    assert null["duplicate"] is True and null["id"] == plain["id"]  # the 10-minute guard, as with no key
    assert keys() == [] and [r.outcome for r in rows()[-2:]] == ["written", "duplicate"]


def test_each_write_function_publishes_key_with_the_contract_s_words_and_no_other_function_does(http):
    words = ("Optional. A key of your own for this one write: 8 to 64 letters, digits, - or _. Send the same key "
             "when you send the same call again: nothing is written twice and the first answer comes back. A new "
             "write takes a new key.")
    for tool in tool_list(http, sign()):
        properties = tool["inputSchema"]["properties"]
        if tool["name"] not in WRITES:
            assert "key" not in properties, tool["name"]
            continue
        assert list(properties)[-1] == "key" and "key" not in tool["inputSchema"].get("required", [])
        # No pattern and no length: Spotter's own check answers, so the caller gets Spotter's fix.
        assert properties["key"] == {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None,
                                     "description": words, "title": "Key"}, tool["name"]
    assert mcp_server.KEY_DESCRIPTION == words and tr.check(words, tr.CLAUDE) == []
    assert len(mcp_server.INSTRUCTIONS) == 3678 and " key" not in mcp_server.INSTRUCTIONS  # they do not change


@pytest.mark.parametrize("name", WRITES)
def test_the_key_never_reaches_a_service(http, monkeypatch, name):
    token = sign()
    account = ok(http, token, "whoami")["account_id"]
    set_switch(account, "log_delete")
    received = spy_on_services(monkeypatch)
    every = {**REQUIRED.get(name, {}), **OPTIONAL_ARGUMENTS[name]}
    assert failed(http, token, name, {**every, "key": K})["error"] == "spy"
    assert received[name] == service_input(name, account, every) and keys() == []


def test_the_new_messages_keep_the_text_rules():
    for refusal in (common.invalid_key(), common.key_reused(), *(common.already_done(name) for name in WRITES)):
        assert set(refusal) == {"error", "message", "fix"} and calllog.step(refusal["error"]) == "key"
        assert tr.check(refusal["message"], tr.MESSAGE) == [] and tr.check(refusal["fix"], tr.MESSAGE) == []


# ---------------------------------------------------------------- decision 14: never the key's value

def test_the_key_s_value_reaches_no_call_log_column_and_no_log_line(make_client, logs):
    """The marker as the key of every write function: tests/test_calllog.py's marker table leaves the key to this."""
    client = make_client(auth_debug=True)
    token = sign()
    account = ok(client, token, "whoami")["account_id"]
    services.update_settings(account, {"time_zone": "UTC"}, "web")
    set_switch(account, "log_delete")
    for name in WRITES:
        arguments = {**CALLS[name](account), "key": MARKER}
        for _ in range(2):  # written or refused by key_reused, then the same again
            call(client, token, name, arguments)
    call(client, token, "log_bodyweight", {"kg": 80, "date": "soon", "key": MARKER})  # refused by the service
    logged = rows()
    assert {"written", "repeat", "refused"} <= {r.outcome for r in logged}
    assert sum("key" in (r.tried or "").split() for r in logged) == 2 * len(WRITES) + 1
    for row in logged:
        for column in CallLog.__table__.columns:
            assert MARKER not in str(getattr(row, column.key)), column.name
    assert "token verified" in logs.text and MARKER not in logs.text
    assert all(MARKER not in str(r.args) and MARKER not in (r.exc_text or "") for r in logs.records)


# ---------------------------------------------------------------- decision 15: a fast look first

def test_a_repeat_of_log_food_makes_no_usda_call(make_client, monkeypatch):
    http = make_client(usda_api_key=USDA_KEY)
    token = sign()
    fake = Fake(banana(shape="detail"))
    monkeypatch.setattr(usda, "urlopen", fake)
    arguments = {"entries": [{"usda_fdc_id": 173944, "grams": 100}], "allow_repeat": True, "key": K}
    first = call(http, token, "log_food", arguments)
    assert first["isError"] is False and len(fake.requests) == 1
    with db.session_scope() as s:  # the cache forgets the food: a lookup now would ask USDA again
        s.query(UsdaFood).delete()
    assert call(http, token, "log_food", arguments) == first
    assert len(fake.requests) == 1 and count(FoodEntry) == 1 and count(UsdaFood) == 0


def test_a_kept_answer_is_returned_only_after_the_permission_check_and_the_key_s_shape_comes_after_it(writer):
    http, token, account = writer
    kept = call(http, token, "log_bodyweight", {"kg": 80, "key": K})
    set_switch(account, "read")
    assert failed(http, token, "log_bodyweight", {"kg": 80, "key": K})["error"] == "not_allowed"
    assert failed(http, token, "log_bodyweight", {"kg": 80, "key": "bad"})["error"] == "not_allowed"
    set_switch(account, "log")
    assert call(http, token, "log_bodyweight", {"kg": 80, "key": K}) == kept
    assert [(r.step, r.outcome) for r in rows()[-3:]] == [("permission", "refused"), ("permission", "refused"),
                                                         (None, "repeat")]


def test_a_key_sent_to_a_read_is_not_looked_at(writer):
    http, token, _ = writer
    assert call(http, token, "get_log", {"key": "bad"})["isError"] is False and keys() == []


# ---------------------------------------------------------------- decision 13: with no key, nothing changes

@pytest.fixture
def statements():
    """Every SQL statement the test database runs, as text."""
    seen: list[str] = []

    def keep(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", keep)
    yield seen
    event.remove(engine, "before_cursor_execute", keep)


@pytest.mark.parametrize("name", WRITES)
def test_with_no_key_a_write_never_touches_the_key_table(writer, statements, monkeypatch, name):
    http, token, account = writer
    monkeypatch.setattr(calllog, "_swept_keys", db.utcnow())  # the sweep is not the write: none in this test
    arguments = CALLS[name](account)
    statements.clear()
    assert call(http, token, name, arguments)["isError"] is False
    assert statements and not [s for s in statements if "write_key" in s]
    assert keys() == [] and "key" not in rows()[-1].tried.split()
    call(http, token, name, {**arguments, "key": K})  # and with a key it does, so the check above can fail
    assert [s for s in statements if "write_key" in s]


def test_with_no_claim_in_the_context_lock_user_claims_nothing(make_user):
    user = make_user()
    assert common._CLAIM.get() is None
    assert "error" not in services.log_bodyweight(user.id, {"kg": 80}, "claude") and keys() == []


def test_a_call_that_reaches_the_lock_twice_does_not_trip_over_its_own_claim(make_user):
    user = make_user()

    def two_writes() -> dict:
        services.log_bodyweight(user.id, {"kg": 80}, "claude")
        return services.log_lifts(user.id, {"sets": squat(100)}, "claude")

    claim = common.new_claim("two_writes", K, common.fingerprint("two_writes", {}))
    result, kept = common.keyed(user.id, claim, two_writes)
    assert kept is None and "error" not in result and count(Bodyweight) == 1 and count(LiftSession) == 1
    assert [k.id for k in keys()] == [claim["row"]] and common._CLAIM.get() is None


# ---------------------------------------------------------------- decision 2: the table

def test_0006_adds_the_key_table_the_models_match_and_it_comes_down_again():
    address = f"sqlite:///{(Path(tempfile.mkdtemp(dir=TEST_DIR)) / 'keys.db').as_posix()}"
    cfg = alembic_config(address)
    command.upgrade(cfg, "0005")
    engine = make_engine(address)
    now = "2026-11-10 08:00:00.000000"
    row = "INSERT INTO write_key (user_id, key, function, fingerprint, created_at) VALUES (?, ?, 'log_lifts', 'f', ?)"
    try:
        with engine.begin() as c:
            for user in (1, 2):
                c.exec_driver_sql("INSERT INTO user (id, provider, subject, claude_can, add_back_exercise_kcal, "
                                  "created_at, updated_at, last_seen_at) VALUES (?, 'workos', ?, 'log', 0, ?, ?, ?)",
                                  (user, f"s{user}", now, now, now))
            c.exec_driver_sql("INSERT INTO call_log (function, started_at, ms, ok, result_bytes) "
                              "VALUES ('whoami', ?, 1, 1, 10)", (now,))
            assert "write_key" not in inspect(c).get_table_names()
        command.upgrade(cfg, "0006")
        with engine.connect() as c:
            found = {col["name"]: col for col in inspect(c).get_columns("write_key")}
            assert list(found) == ["id", "user_id", "key", "function", "fingerprint", "answer", "created_at"]
            assert [name for name, col in found.items() if col["nullable"]] == ["answer"]
            assert (found["key"]["type"].length, found["fingerprint"]["type"].length) == (64, 64)
            assert [u["column_names"] for u in inspect(c).get_unique_constraints("write_key")] == [["user_id", "key"]]
            [owner] = inspect(c).get_foreign_keys("write_key")
            assert (owner["referred_table"], owner["options"]) == ("user", {"ondelete": "CASCADE"})
            # The models match, but for the table and index 0007 adds (tests/test_edit_history.py holds them there).
            assert sorted((d[0], d[1].name) for d in compare_metadata(MigrationContext.configure(c), Base.metadata)) \
                == [("add_index", "ix_entry_version_user_id_kind_entry_id_id"), ("add_table", "entry_version")]
        with engine.begin() as c:
            for user, key in ((1, "one-key-01"), (1, "two-key-02"), (2, "one-key-01")):  # one row per person and key
                c.exec_driver_sql(row, (user, key, now))
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.exec_driver_sql(row, (1, "one-key-01", now))
        with engine.begin() as c:
            c.exec_driver_sql("DELETE FROM user WHERE id = 1")  # the rows go with the account
            left = c.exec_driver_sql("SELECT user_id, key FROM write_key").all()
            assert [tuple(row) for row in left] == [(2, "one-key-01")]
        command.downgrade(cfg, "0005")
        with engine.connect() as c:
            assert "write_key" not in inspect(c).get_table_names()
            assert c.exec_driver_sql("SELECT count(*) FROM call_log").scalar() == 1
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0005"
    finally:
        engine.dispose()


def test_deleting_the_account_deletes_its_keys_and_no_one_else_s(http):
    asha, ben = sign(claims(sub="user_01ASHA")), sign(claims(sub="user_01BEN"))
    ok(http, asha, "log_bodyweight", {"kg": 60, "key": K})
    ok(http, ben, "log_bodyweight", {"kg": 80, "key": K})
    hers = ok(http, asha, "whoami")["account_id"]
    assert services.delete_account(hers)["deleted"] is True
    assert [k.user_id for k in keys()] == [ok(http, ben, "whoami")["account_id"]]


# ---------------------------------------------------------------- decision 12: the guard for a new goal

GOALS = {
    "bodyweight": {"kind": "bodyweight", "target_value": 75, "start_value": 80, "target_date": FAR},
    "lift": {"kind": "lift", "exercise": "Back Squat", "target_value": 140, "start_value": 100, "target_date": FAR},
    "measurement": {"kind": "measurement", "measurement_kind": "waist", "target_value": 80, "start_value": 90,
                    "target_date": FAR},
    "cardio_best": {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5, "target_value": 27,
                    "start_value": 30, "target_date": FAR},
}
NOT_TWICE = "This goal was already set in the last 10 minutes, so it was not set twice: this is the first one."


@pytest.mark.parametrize("kind", sorted(GOALS))
def test_the_same_new_goal_inside_10_minutes_is_not_set_twice_and_the_first_comes_back(make_user, kind):
    user = make_user()
    first = services.set_goal(user.id, GOALS[kind], "claude")
    again = services.set_goal(user.id, dict(GOALS[kind]), "web")
    assert "error" not in first and count(Goal) == 1
    assert again == {**first, "warnings": [*first["warnings"], NOT_TWICE]}  # the first goal, in the same shape
    SetGoalResult.model_validate(again)
    assert tr.check(NOT_TWICE, tr.MESSAGE) == [] and common.DUPLICATE_MINUTES == 10


# What makes a new goal another goal: its target, its date, or the thing it is for.
OTHERS = [("bodyweight", {"target_value": 74}), ("bodyweight", {"target_date": "2099-02-28"}),
          ("lift", {"exercise": "Deadlift"}), ("measurement", {"measurement_kind": "hips"}),
          ("cardio_best", {"distance_km": 10, "target_value": 55, "start_value": 60}),
          ("cardio_best", {"exercise": "Treadmill Run"})]


@pytest.mark.parametrize("kind, change", OTHERS, ids=[next(iter(change)) + str(n) for n, (_, change) in
                                                      enumerate(OTHERS)])
def test_a_new_goal_that_differs_is_still_set(make_user, kind, change):
    user = make_user()
    first = services.set_goal(user.id, GOALS[kind], "claude")
    other = services.set_goal(user.id, {**GOALS[kind], **change}, "claude")
    assert "error" not in other and other["id"] != first["id"] and count(Goal) == 2
    assert NOT_TWICE not in other["warnings"]


def test_the_same_new_goal_after_10_minutes_or_after_the_first_is_closed_is_set_again(make_user):
    user = make_user()
    first = services.set_goal(user.id, GOALS["bodyweight"], "claude")
    age(Goal, first["id"], minutes=11)
    second = services.set_goal(user.id, GOALS["bodyweight"], "claude")
    assert second["id"] != first["id"] and "Both are kept" in second["warnings"][0] and count(Goal) == 2
    for goal in (first, second):
        services.set_goal(user.id, {"goal_id": goal["id"], "status": "dropped"}, "claude")
    third = services.set_goal(user.id, GOALS["bodyweight"], "claude")
    assert third["id"] not in (first["id"], second["id"]) and third["warnings"] == [] and count(Goal) == 3


def test_among_several_goals_that_match_the_guard_gives_back_the_first_by_id(make_user):
    user = make_user()
    first = services.set_goal(user.id, GOALS["bodyweight"], "claude")
    age(Goal, first["id"], minutes=11)
    second = services.set_goal(user.id, GOALS["bodyweight"], "claude")  # the first was too old to stop it
    age(Goal, first["id"], minutes=1)  # now both are inside the 10 minutes, and each is the same goal
    again = services.set_goal(user.id, GOALS["bodyweight"], "claude")
    assert second["id"] > first["id"] and count(Goal) == 2
    assert again["id"] == again["goal"]["id"] == first["id"] and again["warnings"] == [NOT_TWICE]


def test_a_goal_not_set_twice_reads_duplicate_on_its_call_s_row_and_is_noted_there(writer):
    http, token, _ = writer
    first = ok(http, token, "set_goal", GOALS["bodyweight"])
    again = ok(http, token, "set_goal", GOALS["bodyweight"])  # no key: the guard alone
    assert again["id"] == first["id"] and again["warnings"] == [NOT_TWICE] and count(Goal) == 1
    assert [(r.outcome, r.ok, r.notes) for r in rows()[-2:]] == [("written", True, None),
                                                                 ("duplicate", True, "goal=duplicate")]


def test_a_crash_after_the_guard_noted_the_goal_still_reads_failed_on_its_call_s_row(writer, monkeypatch):
    http, token, _ = writer
    ok(http, token, "set_goal", GOALS["bodyweight"])

    def broken(*args, **kwargs):
        raise ValueError(MARKER)

    monkeypatch.setattr(goals, "goal_dict", broken)  # set_goal builds its result with it, after the guard
    assert failed(http, token, "set_goal", GOALS["bodyweight"])["error"] == "internal_error" and count(Goal) == 1
    row = rows()[-1]  # the note was set, and the crash still decides what the row reads
    assert (row.outcome, row.ok, row.error_code, row.step, row.notes) == ("failed", False, "internal_error", "crash",
                                                                         "goal=duplicate")


def test_on_the_web_a_goal_not_set_twice_reads_duplicate_too_and_the_page_says_so(client):
    sign_in(client)
    assert "error" not in services.update_settings(only_user().id, {"time_zone": "UTC"}, "web")
    fields = {k: str(v) for k, v in GOALS["bodyweight"].items()}
    first = post(client, "/goals", key="a-form-key-0001", **fields)
    again = post(client, "/goals", key="a-form-key-0002", **fields)  # the form drawn again: a new key, the same goal
    assert first.headers["location"].endswith("/goals?saved=goal#top")
    assert again.headers["location"].endswith("/goals?saved=goal&notes=twice#top") and count(Goal) == 1
    assert [(r.function, r.caller, r.outcome, r.notes) for r in rows()[-2:]] == [
        ("set_goal", "web", "written", None), ("set_goal", "web", "duplicate", "goal=duplicate")]
    said = "This goal was already set, so it was not set twice."
    page = client.get("/goals?saved=goal&notes=twice").text
    assert web.GOAL_NOTE_TEXT["twice"] == said and said in page and said not in client.get("/goals?saved=goal").text
    assert web.goal_note_codes([NOT_TWICE]) == ["twice"] and tr.check(said, tr.PAGE) == []


# What the guard also holds, beside the thing, the target and the date: the text, and the start the first has.
@pytest.mark.parametrize("change", [{"text": "For the summer"}, {"start_value": 79}], ids=["text", "start"])
def test_a_new_goal_with_another_text_or_another_start_is_set_with_the_warning_it_had(make_user, change):
    user = make_user()
    first = services.set_goal(user.id, GOALS["bodyweight"], "claude")
    other = services.set_goal(user.id, {**GOALS["bodyweight"], **change}, "claude")
    assert "error" not in other and other["id"] != first["id"] and count(Goal) == 2
    [warning] = other["warnings"]
    assert warning.startswith("There is already an open goal") and "Both are kept" in warning
    for field, value in change.items():  # the start given took effect: nothing is logged to start from
        assert other["goal"][field] == value != first["goal"][field]
    again = services.set_goal(user.id, {**GOALS["bodyweight"], **change}, "claude")  # and that one again is the guard's
    assert again["id"] == other["id"] and again["warnings"][-1] == NOT_TWICE and count(Goal) == 2


def test_a_start_value_that_is_ignored_leaves_it_the_same_goal(make_user):
    user = make_user()
    assert "error" not in services.log_bodyweight(user.id, {"kg": 80}, "claude")  # the start is this, whatever is sent
    first = services.set_goal(user.id, GOALS["bodyweight"], "claude")
    again = services.set_goal(user.id, {**GOALS["bodyweight"], "start_value": 79}, "claude")
    assert again["id"] == first["id"] and count(Goal) == 1 and again["goal"]["start_value"] == 80
    [ignored] = first["warnings"]
    assert ignored.startswith("start_value was ignored") and again["warnings"] == [ignored, NOT_TWICE]


def test_a_weigh_in_between_the_two_sends_moves_the_start_so_the_second_is_another_goal(make_user):
    user = make_user()
    services.log_bodyweight(user.id, {"kg": 80}, "claude")
    first = services.set_goal(user.id, {**GOALS["bodyweight"], "start_value": None}, "claude")
    services.log_bodyweight(user.id, {"kg": 82, "date": day(-1)}, "claude")  # the 7-day average is now 81
    other = services.set_goal(user.id, {**GOALS["bodyweight"], "start_value": None}, "claude")
    assert (first["goal"]["start_value"], other["goal"]["start_value"]) == (80, 81) and count(Goal) == 2
    assert other["id"] != first["id"] and "Both are kept" in other["warnings"][0]


# ---------------------------------------------------------------- decision 11: the forms

TEMPLATES = Path(web.HERE) / "templates"
FORM = re.compile(r"<form\b[^>]*>.*?</form>", re.S)
UNKEYED = ('action="/sign-out"', 'action="/settings/export"')  # they write nothing through a service


def test_every_form_that_writes_carries_one_hidden_key_as_its_last_field_and_the_three_others_carry_none():
    forms = [form for path in sorted(TEMPLATES.glob("*.html")) for form in FORM.findall(path.read_text("utf-8"))]
    keyed = [form for form in forms if not any(action in form for action in UNKEYED)]
    assert (len(forms), len(keyed)) == (21, 18)
    field = '<input type="hidden" name="key" value="{{ form_key() }}">'
    for form in forms:
        assert form.count(field) == (form in keyed) and form.count('name="key"') == form.count(field), form[:80]
        assert form.count('name="csrf_token"') == 1  # the CSRF token stays as it was
        assert form not in keyed or re.search(re.escape(field) + r"\s*</form>$", form), form[:80]


def drawn_key(page: str, action: str, holding: str = "") -> str:
    """The key of the one form drawn with this action (and holding this text, where two share an action)."""
    [form] = [f for f in FORM.findall(page) if f'action="{action}"' in f and holding in f]
    [key] = re.findall(r'<input type="hidden" name="key" value="([^"]*)">', form)
    assert common.is_key(key) and len(key) == 22
    return key


def an_estimate(user: int) -> int:
    entry = {"description": "Dal", "kcal": 300, "estimated": True}
    return services.log_food(user, {"entries": [entry]}, "claude")["ids"][0]


def a_saved_food(user: int) -> int:
    return services.save_food(user, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120}, "web")["id"]


def a_deleted_food(user: int) -> int:
    food = a_saved_food(user)
    assert services.delete_entry(user, "saved_food", food)["deleted"] is True
    return food


def a_goal(user: int) -> int:
    return services.set_goal(user, GOALS["bodyweight"], "web")["id"]


# Every keyed form but Delete my account: label -> (set up, giving an id; the page that draws it; its action; text
# only that form holds, where two share the action; its fields; whether its route writes without reading first).
# A route that reads first and finds nothing to change writes nothing, so a later second send never reaches the
# write: it lands on the same page, marked unchanged, as before this work.
FORMS = {
    "the automatic time zone": (None, "/progress", "/settings", "", {"time_zone": "Asia/Kolkata",
                                                                     "next": "/progress"}, True),
    "the time zone card": (None, "/settings", "/settings#zone", "", {"time_zone": "Asia/Kolkata", "card": "zone"},
                           True),
    "the targets": (None, "/settings", "/settings/targets#targets", "", {"calorie_target_kcal": "2200"}, False),
    "what the assistant may do": (None, "/settings", "/settings/claude-can#assistant", "", {"claude_can": "read"},
                                  True),
    "a food entry": (None, "/day/{day}", "/day/{day}/food#add-food", "", {"description": "Dal", "kcal": "300"},
                     True),
    "a cardio session": (None, "/day/{day}", "/day/{day}/cardio#add-cardio", "",
                         {"exercise": "Treadmill Walk", "minutes": "30"}, True),
    "a weigh-in": (None, "/day/{day}", "/day/{day}/weigh-in#add-weigh-in", "", {"kg": "80.5"}, True),
    "a measurement": (None, "/day/{day}", "/day/{day}/measurement#add-measurement", "",
                      {"kind": "waist", "value": "84"}, True),
    "an entry's edit": (a_weigh_in, "/entries/bodyweight/{id}/edit", "/entries/bodyweight/{id}/edit", "",
                        {"kg": "82", "date": "{day}"}, False),
    "an entry's delete": (a_weigh_in, "/day/{day}", "/entries/bodyweight/{id}/delete", "", {}, True),
    "an entry's undo": (a_deleted_weigh_in, "/day/{day}?deleted=bodyweight:{id}", "/entries/bodyweight/{id}/restore",
                        "", {}, True),
    "an estimate's confirm": (an_estimate, "/day/{day}", "/entries/food_entry/{id}/confirm", "", {}, False),
    "a new goal": (None, "/goals", "/goals#set-goal", "", {k: str(v) for k, v in GOALS["bodyweight"].items()}, True),
    "a goal's status": (a_goal, "/goals", "/goals/{id}/status", 'name="status" value="hit"', {"status": "hit"},
                        False),
    "a saved food": (None, "/foods", "/foods#save-food", "",
                     {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": "120"}, False),
    "a saved food's edit": (a_saved_food, "/foods/{id}/edit", "/foods/{id}/edit", "",
                            {"serving_label": "1 roti", "kcal_per_serving": "130"}, False),
    "a saved food's delete": (a_saved_food, "/foods", "/foods/{id}/delete", "", {}, True),
    "a saved food's undo": (a_deleted_food, "/foods?deleted=saved_food:{id}", "/foods/{id}/restore", "", {}, True),
}


@pytest.fixture
def form(client, request):
    """(the client, signed in; the form's key as its page drew it; the address it posts to; its fields; whether its
    route writes without reading first)."""
    set_up, page, action, holding, fields, writes_first = FORMS[request.param]
    sign_in(client)
    user = only_user().id
    if request.param != "the automatic time zone":  # that form is drawn only while the person has no zone
        assert "error" not in services.update_settings(user, {"time_zone": "UTC"}, "web")
    fill = {"day": today(), "id": set_up(user) if set_up else ""}
    drawn = client.get(page.format(**fill))
    assert drawn.status_code == 200, drawn.text[:300]
    key = drawn_key(drawn.text, action.format(**fill), holding)
    assert key != drawn_key(client.get(page.format(**fill)).text, action.format(**fill), holding)  # new each draw
    return (client, key, action.format(**fill).partition("#")[0], {k: v.format(**fill) for k, v in fields.items()},
            writes_first)


@pytest.mark.parametrize("form", sorted(FORMS), indirect=True)
def test_a_form_sent_twice_writes_once_and_lands_on_the_same_page(form):
    client, key, address, fields, writes_first = form
    logged, before = len(rows()), every_table()
    first = post(client, address, key=key, **fields)
    after = every_table()
    second = post(client, address, key=key, **fields)
    assert first.status_code == 303 and after != before  # the first send wrote
    assert second.status_code == 303 and every_table() == after  # the second wrote nothing
    made = rows()[logged:]
    where = [r.headers["location"].partition("?")[0].partition("#")[0] for r in (first, second)]
    assert where[0] == where[1]
    if writes_first:  # the same key and the same write again: the first result, so the very same address
        assert second.headers["location"] == first.headers["location"]
        assert [(r.outcome, r.ok, r.caller) for r in made] == [("written", True, "web"), ("repeat", True, "web")]
        assert made[0].function == made[1].function and "key" in made[1].tried.split()
        assert made[1].result_bytes == made[0].result_bytes
    else:  # its route read first and found nothing left to change: no write, as before
        assert [r.outcome for r in made] == ["written"] and "saved=unchanged" in second.headers["location"]
    assert len(keys()) == 1


def test_the_table_of_forms_covers_every_keyed_form_but_delete_my_account():
    """One template form can be drawn for two routes (the Undo strip), so the table is held to the routes: every
    POST route but sign-out and export is posted to by one of its rows, and Delete my account has its own test."""
    posted = {re.sub(r"\{\w+\}", "{}", action.partition("#")[0]) for _, _, action, _, _, _ in FORMS.values()}
    routes = {re.sub(r"\{\w+\}", "{}", path.replace("/entries/{kind}/", "/entries/bodyweight/"))
              for path, _, methods in web.PAGES if "POST" in methods}
    assert routes - posted == {"/settings/export", "/settings/delete"}
    assert posted - routes == set()


def test_delete_my_account_sent_twice_deletes_once_and_its_keys_go_with_it(client, monkeypatch):
    async def deleted(subject):
        return None

    monkeypatch.setattr(web_auth, "delete_identity", deleted)
    sign_in(client)
    session = session_of(client)
    post(client, f"/day/{today()}/weigh-in", kg="80", key="a-form-key-0001")
    key = drawn_key(client.get("/settings/delete").text, "/settings/delete")
    assert len(keys()) == 1
    first = client.post("/settings/delete", data={"csrf_token": session["csrf"], "key": key})
    assert first.status_code == 200 and "Your account is deleted" in first.text
    assert count(User) == 0 and keys() == []  # the account's keys went with it: no repeat can be told from them
    plant_session(client, session)  # the second click was sent with the same cookie
    second = client.post("/settings/delete", data={"csrf_token": session["csrf"], "key": key})
    assert second.status_code == 303 and second.headers["location"].endswith("/sign-in")  # as before this work
    assert [(r.function, r.outcome) for r in rows()] == [("log_bodyweight", "written"), ("delete_account", "written")]


def test_a_form_s_write_key_is_worked_out_from_the_form_s_key_and_what_the_write_sends(client):
    sign_in(client)
    r = post(client, f"/day/{today()}/weigh-in", kg="80.5", key="a-form-key-0001")
    assert r.status_code == 303 and rows()[-1].tried == "date key kg"
    mark = common.fingerprint("log_bodyweight", {"kg": "80.5", "date": today()})
    [kept] = keys()
    assert kept.key == hashlib.sha256(f"a-form-key-0001:{mark}".encode()).hexdigest()[:40]
    assert (kept.function, kept.fingerprint) == ("log_bodyweight", mark) and "a-form-key-0001" not in str(
        [getattr(row, c.key) for row in rows() for c in CallLog.__table__.columns])


def test_a_form_sent_again_with_a_field_changed_is_a_new_write(client):
    sign_in(client)
    for kg in ("80.5", "81"):  # back, fix the number, send: the same form key
        assert post(client, f"/day/{today()}/weigh-in", kg=kg, key="a-form-key-0001").status_code == 303
    assert [r.outcome for r in rows()] == ["written", "written"] and len(keys()) == 2  # never key_reused
    with db.session_scope() as s:
        assert [b.kg for b in s.scalars(select(Bodyweight).where(Bodyweight.deleted_at.is_(None)))] == [81]


@pytest.mark.parametrize("key", [None, "", "short", "has a space", "x" * 65])
def test_a_missing_or_malformed_form_key_runs_the_write_with_no_key_as_before(client, key):
    sign_in(client)
    sent = {} if key is None else {"key": key}
    first = post(client, f"/day/{today()}/weigh-in", kg="80.5", **sent)
    second = post(client, f"/day/{today()}/weigh-in", kg="80.5", **sent)
    assert (first.status_code, second.status_code) == (303, 303) and keys() == []
    assert "saved=bodyweight" in first.headers["location"] and "saved=unchanged" in second.headers["location"]
    assert [(r.outcome, r.tried) for r in rows()] == [("written", "date kg"), ("duplicate", "date kg")]


def test_a_form_sent_again_after_the_10_minutes_still_writes_once(client):
    sign_in(client)
    food = {"description": "Dal", "kcal": "300", "key": "a-form-key-0001"}
    first = post(client, f"/day/{today()}/food", **food)
    with db.session_scope() as s:
        s.execute(update(FoodEntry).values(created_at=db.utcnow() - timedelta(minutes=11)))
    second = post(client, f"/day/{today()}/food", **food)
    assert second.headers["location"] == first.headers["location"] and count(FoodEntry) == 1
    assert post(client, f"/day/{today()}/food", description="Dal", kcal="300").status_code == 303
    assert count(FoodEntry) == 2  # with no key, the same food after the 10 minutes is a second entry, as before


def test_a_refused_form_leaves_its_key_free(client):
    sign_in(client)
    for _ in range(2):
        assert post(client, f"/day/{today()}/weigh-in", kg="heavy", key="a-form-key-0001").status_code == 400
    assert keys() == [] and [r.outcome for r in rows()] == ["refused", "refused"]


def test_two_writes_of_one_form_get_two_keys(make_user):
    user = make_user()

    def both() -> list[dict]:
        web._FORM_KEY.set("a-form-key-0001")
        return [web.logged_write(user.id, "log_bodyweight", services.log_bodyweight, user.id, {"kg": "80"}, "web"),
                web.logged_write(user.id, "log_lifts", services.log_lifts, user.id, {"sets": squat(100)}, "web")]

    first = contextvars.copy_context().run(both)
    assert contextvars.copy_context().run(both) == first  # the same form again: both first results
    assert len({k.key for k in keys()}) == 2 and count(Bodyweight) == 1 and count(LiftSession) == 1
    assert [r.outcome for r in rows()] == ["written", "written", "repeat", "repeat"]


def test_a_form_whose_first_result_was_not_kept_says_already_done_on_its_own_page(web_client, monkeypatch):
    client = web_client()
    sign_in(client)
    services.update_settings(only_user().id, {"time_zone": "UTC"}, "web")
    address = f"/day/{today()}/weigh-in"
    assert post(client, address, kg="80.5", key="a-form-key-0001").status_code == 303
    with db.session_scope() as s:
        s.execute(update(WriteKey).values(answer=None))
    monkeypatch.setattr(common, "ANSWER_WAIT", 0)
    again = post(client, address, kg="80.5", key="a-form-key-0001")
    assert again.status_code == 409 and count(Bodyweight) == 1
    assert "<h1 class=\"title\">Already done</h1>" in again.text
    assert "This form was already sent, and what it asked for was done then." in again.text
    assert "Nothing was done a second time. Go back and reload the page to see it." in again.text
    assert (rows()[-1].error_code, rows()[-1].outcome, rows()[-1].step) == ("already_done", "refused", "key")
    findings = error_page_findings(Rendered(address, again, True, user_id=only_user().id))
    assert not findings, "\n".join(findings)
    assert not re.search(r"\b(call|key)\b", again.text.partition("<main")[2])


# ---------------------------------------------------------------- what the owner and the person are told

def test_the_owner_s_function_names_repeat_among_the_outcomes():
    assert OwnerCall.model_fields["outcome"].description == (
        "read, written, duplicate, preview, repeat, refused or failed. Null while it runs, or when the process "
        "stopped under it.")


def test_the_privacy_page_says_an_answer_is_kept_for_a_day_in_a_card_of_its_own():
    cards = re.findall(r'<section class="card".*?</section>', (TEMPLATES / "privacy.html").read_text("utf-8"), re.S)
    [at] = [n for n, card in enumerate(cards) if ">A log of calls</h2>" in card]
    calls, twice = cards[at], cards[at + 1]  # beside the log of calls, not in it: that card says what it never holds
    assert ">A change sent twice</h2>" in twice and "24 hours" not in twice
    for said in ("Spotter keeps its answer to each change for a day, so that a change sent twice is saved once.",
                 "After a day the answer is not used, and it is deleted at the next clean-up."):
        assert f"<p>{said}</p>" in twice
    assert "<p>It never holds what you said and never what came back.</p>" in calls and "a day" not in calls
