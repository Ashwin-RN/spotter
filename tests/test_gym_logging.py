"""Gym logging (docs/contracts/gym-logging.md): every log_lifts call for a date adds its sets to that date's session.

The exit check line by line, then what the lead's rulings R1 to R8 name. Calls go through the services, and through
the real HTTP path (TestClient) where the wrapper, the call log or a write's key is the point, as in
tests/test_idempotency.py and tests/test_switch.py. A row's last change is moved back by hand, never by waiting:
this machine's clock steps 1 to 2 ms.
"""

import json
import threading
import time
from datetime import date, timedelta

import pytest
from sqlalchemy import event, func, select, update

from app import db, formatting, services, users
from app.mcp_schemas import LogLiftsResult
from app.models import EntryVersion, LiftSession, LiftSet
from app.services import lifts

from . import text_rules as tr
from .conftest import lock_sql, sign
from .test_calllog import rows
from .test_idempotency import K, keys, writer  # noqa: F401 - writer is a fixture
from .test_mcp import call, failed, ok, tool_list

BENCH = ("Bench Press", 80, 5)
SETS_FIX = "Send new_session true to start another session that day, or send fewer sets."
NOTES_FIX = "Send shorter notes, or change the session's notes with edit_entry."
DESCRIPTION = ("Records sets of lifting. Sets for a date go into that date's session: the first call starts it, and "
               "each later call adds its sets to it. So send only the sets not yet logged. Returns the whole session, "
               "any new bests, and a one-line summary. restore_entry with undo_edit takes back the last sets added.")
ARGUMENTS = {
    "sets": ("The sets to log, in order: 1 to 60. Only sets not yet logged: earlier sets of that date are already in "
             "its session. A session holds at most 60 sets."),
    "date": "The day it happened, YYYY-MM-DD, at most 2 years back. Leave out for today.",
    "notes": ("How it went, in the person's words. Up to 2,000 characters. On a later call for the same date, they "
              "are added to the session's notes."),
    "allow_repeat": "Not needed: every call adds its sets. Ignored.",
    "new_session": ("true starts a new session for that date, even when one is logged. Send it only when the person "
                    "says this is a separate workout."),
}
RESULT = {
    "duplicate": "Always false: a set said again is added, never refused.",
    "session": "The session as saved, with every set in it.",
    "new_bests": ("New bests this call's sets set, for the session as a whole, against every earlier session of the "
                  "lift. A lift's first session never sets one."),
    "summary": ("What this call did, in one line, ready to quote: the sets logged or added, then new bests, or for a "
                "lift's first session the mark it sets (a first session is never a new best)."),
}


@pytest.fixture
def user(make_user):
    return make_user()


def sets(*rows_):
    return [{"exercise": e, "weight_kg": w, "reps": r} for e, w, r in rows_]


def log(person, *rows_, by="claude", **data):
    return services.log_lifts(person.id, {"sets": sets(*rows_), **data}, by)


def day(person, offset: int = 0) -> str:
    return (users.today_for(person) + timedelta(days=offset)).isoformat()


def shown(person, offset: int = 0) -> str:
    """A date as a summary says it: 'Sun 15 Nov'."""
    today = users.today_for(person)
    return formatting.day(today + timedelta(days=offset), today)


def numbers(result: dict) -> list[tuple]:
    return [(x["exercise"], x["set_number"], x["weight_kg"], x["reps"]) for x in result["session"]["sets"]]


def live_sessions(user_id: int) -> int:
    with db.new_session() as s:
        return s.scalar(select(func.count(LiftSession.id)).where(LiftSession.user_id == user_id,
                                                                 LiftSession.deleted_at.is_(None)))


def versions(session_id: int) -> list[EntryVersion]:
    with db.new_session() as s:
        return list(s.scalars(select(EntryVersion).where(EntryVersion.kind == "lift_session",
                                                         EntryVersion.entry_id == session_id)
                              .order_by(EntryVersion.id)))


def stored(session_id: int) -> dict:
    """Every stored column of the session, its sets with their ids, and every column of its versions."""
    with db.new_session() as s:
        row = s.get(LiftSession, session_id)
        out = {c.name: getattr(row, c.key) for c in LiftSession.__table__.columns}
        out["sets"] = [(x.id, x.exercise_id, x.set_number, x.weight_kg, x.reps)
                       for x in s.scalars(select(LiftSet).where(LiftSet.session_id == session_id).order_by(LiftSet.id))]
    columns = EntryVersion.__table__.columns
    out["versions"] = [{c.name: getattr(v, c.key) for c in columns} for v in versions(session_id)]
    return out


def held(session_id: int) -> tuple:
    """What an undo must put back: the notes, the key, and the sets with their numbers, in the order logged."""
    row = stored(session_id)
    return row["notes"], row["dedupe_key"], [x[1:] for x in row["sets"]]


def older(session_id: int, **back) -> None:
    """Move the session's last change back by hand, so the next change gives it another updated_at."""
    with db.session_scope() as s:
        s.execute(update(LiftSession).where(LiftSession.id == session_id).values(
            updated_at=db.utcnow() - timedelta(**(back or {"hours": 1}))))


def undo(person, session_id: int) -> dict:
    return services.restore_entry(person.id, "lift_session", session_id, "claude", undo_edit=True)


def reported(result: dict) -> list[tuple]:
    return [(b["kind"], b["value"], b["previous"], b["text"]) for b in result["new_bests"]]


def logged_bests(person, session_id: int, on: str) -> list[tuple]:
    """The bests get_log gives for that session."""
    [found] = [t for d in services.get_log(person.id, date=on)["days"] for t in d["sessions"] if t["id"] == session_id]
    return reported(found)


# ---------------------------------------------------------------- the exit check: which session (decisions 1, 2, 13)

def test_three_equal_sets_in_three_calls_are_one_session_of_three_sets_numbered_1_to_3_and_never_a_duplicate(user):
    first, second, third = (log(user, BENCH) for _ in range(3))
    assert {r["id"] for r in (first, second, third)} == {first["id"]} and live_sessions(user.id) == 1
    assert [r["duplicate"] for r in (first, second, third)] == [False] * 3
    assert numbers(third) == [("Bench Press", n, 80, 5) for n in (1, 2, 3)]
    mark = "First Bench Press session: estimated max 93.3 kg is the mark to beat."  # epley(80, 5), 93.33
    assert first["summary"] == f"Bench Press 1 × 5 at 80 kg on {shown(user)}. {mark}"
    assert second["summary"] == (f"Added Bench Press 1 × 5 at 80 kg to the session on {shown(user)}. It now has 2 "
                                 f"sets. {mark}")
    assert third["summary"] == (f"Added Bench Press 1 × 5 at 80 kg to the session on {shown(user)}. It now has 3 "
                                f"sets. {mark}")
    assert [r["new_bests"] for r in (first, second, third)] == [[], [], []]


def test_two_lifts_in_two_calls_are_one_session_and_its_links_name_both_in_the_order_first_logged(user):
    squat = log(user, ("Back Squat", 100, 5), ("Back Squat", 100, 5))
    bench = log(user, ("Bench Press", 80, 8))
    assert bench["id"] == squat["id"] and live_sessions(user.id) == 1
    assert numbers(bench) == [("Back Squat", 1, 100, 5), ("Back Squat", 2, 100, 5), ("Bench Press", 1, 80, 8)]
    assert list(bench["links"]["progress"]) == ["Back Squat", "Bench Press"]
    assert bench["links"]["day"] == squat["links"]["day"]
    assert list(bench) == ["kind", "id", "duplicate", "session", "new_bests", "summary", "links"]


def test_new_session_true_starts_a_second_session_that_day_and_the_next_call_adds_to_it(user):
    morning = log(user, ("Back Squat", 100, 5))
    evening = log(user, BENCH, new_session=True)
    later = log(user, BENCH)
    assert evening["id"] != morning["id"] and later["id"] == evening["id"] and live_sessions(user.id) == 2
    assert evening["summary"].startswith(f"Bench Press 1 × 5 at 80 kg on {shown(user)}. ")  # a new session's summary
    assert numbers(later) == [("Bench Press", 1, 80, 5), ("Bench Press", 2, 80, 5)]
    assert len(stored(morning["id"])["sets"]) == 1
    assert log(user, BENCH, new_session=False)["id"] == evening["id"]  # false is left out


def test_a_back_dated_call_adds_to_that_date_s_session_and_leaves_today_s_alone(user):
    old = log(user, ("Deadlift", 120, 5), date=day(user, -3))
    today = log(user, ("Deadlift", 100, 5))
    before = stored(today["id"])
    back = log(user, ("Deadlift", 125, 3), date=day(user, -3))
    assert back["id"] == old["id"] and stored(today["id"]) == before
    assert back["summary"].startswith(f"Added Deadlift 1 × 3 at 125 kg to the session on {shown(user, -3)}. It now "
                                      "has 2 sets.")


def test_a_deleted_session_is_not_added_to_and_a_restored_one_is(user):
    gone = log(user, BENCH)
    services.delete_entry(user.id, "lift_session", gone["id"])
    fresh = log(user, BENCH)
    assert fresh["id"] != gone["id"] and len(fresh["session"]["sets"]) == 1 and len(stored(gone["id"])["sets"]) == 1
    services.delete_entry(user.id, "lift_session", fresh["id"])
    assert services.restore_entry(user.id, "lift_session", gone["id"])["restored"] is True
    back = log(user, BENCH)
    assert back["id"] == gone["id"] and len(back["session"]["sets"]) == 2


def test_with_two_live_sessions_on_a_date_the_add_goes_to_the_later_by_id(user):
    first = log(user, BENCH)
    second = log(user, BENCH, new_session=True)
    # The earlier one is changed after the later one: the rule is the id, not the last change.
    assert "error" not in services.edit_entry(user.id, "lift_session", first["id"], {"notes": "Edited"}, "claude")
    added = log(user, BENCH)
    assert added["id"] == second["id"] and len(stored(first["id"])["sets"]) == 1


def test_a_call_of_one_person_never_adds_to_another_person_s_session_of_the_same_date(make_user):
    asha, ben = make_user(), make_user()
    hers = log(asha, BENCH)
    before = stored(hers["id"])
    his = log(ben, BENCH)
    assert his["id"] != hers["id"] and len(his["session"]["sets"]) == 1 and stored(hers["id"]) == before


def test_a_session_dated_after_today_is_not_added_to_by_a_call_for_today(user, monkeypatch):
    later = db.utcnow() + timedelta(days=2)
    monkeypatch.setattr(db, "utcnow", lambda: later)
    ahead = log(user, BENCH)  # dated two days after the real today, as after a change of time zone
    monkeypatch.undo()
    assert ahead["session"]["date"] == day(user, 2)
    now = log(user, BENCH)
    assert now["id"] != ahead["id"] and now["session"]["date"] == day(user) and len(stored(ahead["id"])["sets"]) == 1


# ---------------------------------------------------------------- an add is an edit (decisions 4, 5)

def test_an_add_keeps_one_version_a_call_why_edit_by_its_caller_before_the_change_and_a_first_call_keeps_none(user):
    first = log(user, BENCH, notes="Easy", by="web")
    assert versions(first["id"]) == []
    log(user, ("Bench Press", 82.5, 5), by="claude")
    third = log(user, ("Bench Press", 85, 3), notes="Hard", by="web")
    kept = versions(first["id"])
    assert [(v.user_id, v.by, v.why) for v in kept] == [(user.id, "claude", "edit"), (user.id, "web", "edit")]
    one, two = (json.loads(v.before) for v in kept)
    assert ([x[1:] for x in one["sets"]], one["notes"]) == ([[1, 80.0, 5]], "Easy")
    assert ([x[1:] for x in two["sets"]], two["notes"]) == ([[1, 80.0, 5], [2, 82.5, 5]], "Easy")
    assert third["session"]["notes"] == "Easy\nHard" and third["session"]["logged_by"] == "web"


def test_undo_after_one_add_takes_back_exactly_that_add(user):
    first = log(user, BENCH, BENCH, notes="Easy")
    before = held(first["id"])
    log(user, BENCH, ("Back Squat", 100, 5), notes="Then squats")
    assert undo(user, first["id"])["undone"] is True
    assert held(first["id"]) == before


def test_undo_after_two_adds_takes_back_the_second_only_and_a_second_undo_changes_nothing(user):
    first = log(user, BENCH, notes="One")
    log(user, BENCH, notes="Two")
    after_one_add = held(first["id"])
    log(user, ("Bench Press", 82.5, 5), notes="Three")
    assert undo(user, first["id"])["undone"] is True and held(first["id"]) == after_one_add
    again = undo(user, first["id"])
    assert (again["undone"], again["duplicate"]) == (False, True) and held(first["id"]) == after_one_add


def test_undo_after_an_add_then_an_edit_takes_back_the_edit(user):
    first = log(user, BENCH)
    log(user, BENCH, notes="Added")
    after_add = held(first["id"])
    assert "error" not in services.edit_entry(user.id, "lift_session", first["id"], {"notes": "Edited"}, "claude")
    assert undo(user, first["id"])["undone"] is True and held(first["id"]) == after_add


def test_an_add_after_an_undo_is_what_the_next_undo_takes_back(user):
    first = log(user, BENCH)
    log(user, BENCH)
    undo(user, first["id"])
    after_undo = held(first["id"])
    log(user, ("Back Squat", 100, 5), notes="Squats")
    assert undo(user, first["id"])["undone"] is True and held(first["id"]) == after_undo


def started(user_id: int, how: str) -> tuple[int, dict]:
    """A session to add to, and the date to send: logged today, or imported yesterday with no notes, so that an add
    with no notes changes no column of its row but updated_at (an imported session keeps its key)."""
    if how == "logged":
        return services.log_lifts(user_id, {"sets": sets(BENCH)}, "claude")["id"], {}
    today = services.update_settings(user_id, {"time_zone": "UTC"}, "web")["today"]  # an import needs one
    on = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    done = services.import_entries(user_id, {"rows": [{"kind": "lift_session", "date": on, "sets": [
        {"exercise": "Bench Press", "weight": 80, "reps": 5}]}], "weight_unit": "kg", "dry_run": False}, "claude")
    assert done["row_outcomes"]["written"] == 1, done
    with db.new_session() as s:
        return s.scalars(select(LiftSession.id).where(LiftSession.user_id == user_id)).one(), {"date": on}


@pytest.mark.parametrize("how", ["logged", "imported"])
def test_a_web_edit_form_drawn_before_an_add_is_refused(user, how):
    first, on = started(user.id, how)
    older(first)
    drawn = services.get_entry(user.id, "lift_session", first)["updated_at"]
    assert log(user, BENCH, **on)["id"] == first
    stale = services.edit_entry(user.id, "lift_session", first, {"notes": "From the form"}, "web", drawn_at=drawn)
    assert stale["error"] == "entry_changed" and stored(first)["notes"] == ""


@pytest.mark.parametrize("how", ["logged", "imported"])
def test_a_delete_s_confirm_code_from_before_an_add_deletes_nothing_through_mcp(writer, how):
    http, token, account = writer
    first, on = started(account, how)
    older(first)
    code = ok(http, token, "delete_entry", {"kind": "lift_session", "entry_id": first})["confirm"]
    assert ok(http, token, "log_lifts", {"sets": sets(BENCH), **on})["id"] == first
    late = ok(http, token, "delete_entry", {"kind": "lift_session", "entry_id": first, "confirm": code})
    assert (late["deleted"], late["dry_run"]) == (False, True) and late["confirm"] != code
    assert stored(first)["deleted_at"] is None and len(stored(first)["sets"]) == 2


def test_an_imported_session_added_to_keeps_its_key_and_the_same_import_again_writes_nothing(user):
    assert "error" not in services.update_settings(user.id, {"time_zone": "UTC"}, "web")  # an import needs one
    sent = {"rows": [{"kind": "lift_session", "date": day(user, -1),
                      "sets": [{"exercise": "Deadlift", "weight": 140, "reps": 3}]}],
            "weight_unit": "kg", "dry_run": False}
    assert services.import_entries(user.id, sent, "claude")["row_outcomes"]["written"] == 1
    with db.new_session() as s:
        imported = s.scalars(select(LiftSession.id).where(LiftSession.user_id == user.id)).one()
    key = stored(imported)["dedupe_key"]
    added = log(user, ("Deadlift", 150, 2), date=day(user, -1))
    assert added["id"] == imported and added["session"]["logged_by"] == "import"
    assert stored(imported)["dedupe_key"] == key
    before = stored(imported)
    again = services.import_entries(user.id, sent, "claude")
    assert again["row_outcomes"] == {"written": 0, "already_there": 1, "day_taken": 0, "failed": 0}
    assert stored(imported) == before and live_sessions(user.id) == 1


def test_an_add_onto_a_session_an_edit_changed_numbers_on_from_its_sets_and_keys_the_whole_session(user):
    first = log(user, BENCH, BENCH, BENCH)
    assert "error" not in services.edit_entry(user.id, "lift_session", first["id"], {
        "sets": sets(("Bench Press", 85, 5), ("Back Squat", 100, 5))}, "claude")
    added = log(user, ("Bench Press", 85, 4), ("Back Squat", 100, 4))
    assert numbers(added) == [("Bench Press", 1, 85, 5), ("Back Squat", 1, 100, 5), ("Bench Press", 2, 85, 4),
                              ("Back Squat", 2, 100, 4)]
    row = stored(first["id"])
    assert row["dedupe_key"] == lifts.session_key(row["date"], row["notes"], [x[1:] for x in row["sets"]])


def test_an_add_onto_a_session_the_web_started_leaves_it_the_web_s(user):
    first = log(user, BENCH, by="web")
    added = log(user, BENCH, by="claude")
    assert added["id"] == first["id"] and added["session"]["logged_by"] == "web"
    assert added["session"]["created_at"] == first["session"]["created_at"]
    assert [v.by for v in versions(first["id"])] == ["claude"]


# ---------------------------------------------------------------- the two refusals (decisions 7, 8)

def sets_message(person, n: int, k: int) -> str:
    return (f"The session on {shown(person)} has {formatting.show(n, 'count')} sets; {k} more would make "
            f"{formatting.show(n + k, 'count')}, and a session holds at most 60.")


def test_59_sets_and_1_more_are_taken_and_60_and_1_more_are_refused_whole_and_nothing_is_written(user):
    first = log(user, *[BENCH] * 59)
    taken = log(user, BENCH)
    assert len(taken["session"]["sets"]) == 60 and taken["session"]["sets"][-1]["set_number"] == 60
    assert "It now has 60 sets." in taken["summary"]
    before = stored(first["id"])
    refused = log(user, BENCH)
    one = {"error": "sets_count_out_of_range", "message": sets_message(user, 60, 1), "fix": SETS_FIX}
    assert one["message"] == (f"The session on {shown(user)} has 60 sets; 1 more would make 61, and a session holds at "
                              "most 60.")
    assert refused == {**one, "errors": [one]}
    assert stored(first["id"]) == before  # no set, no version, no column moved, updated_at least of all
    assert log(user, BENCH, new_session=True)["id"] != first["id"]  # the way out the fix names


def test_a_session_of_one_set_reads_has_1_set_when_60_more_are_refused(user):
    first = log(user, BENCH)
    before = stored(first["id"])
    one = {"error": "sets_count_out_of_range", "fix": SETS_FIX,
           "message": f"The session on {shown(user)} has 1 set; 60 more would make 61, and a session holds at most 60."}
    assert log(user, *[BENCH] * 60) == {**one, "errors": [one]} and stored(first["id"]) == before


def test_notes_of_2000_in_all_are_taken_and_2001_are_refused_whole_and_nothing_is_written(user):
    first = log(user, BENCH, notes="a" * 1000)
    before = stored(first["id"])
    refused = log(user, BENCH, notes="b" * 1000)  # 1,000 + the new line + 1,000
    one = {"error": "invalid_field", "message": "The session's notes would be 2,001 characters; the most is 2,000.",
           "fix": NOTES_FIX}
    assert refused == {**one, "errors": [one]} and stored(first["id"]) == before
    taken = log(user, BENCH, notes="b" * 999)
    assert taken["session"]["notes"] == "a" * 1000 + "\n" + "b" * 999 and len(taken["session"]["notes"]) == 2000
    quiet = log(user, BENCH)  # no notes: the session's stay
    assert quiet["session"]["notes"] == taken["session"]["notes"] and len(quiet["session"]["sets"]) == 3


def test_both_refusals_at_once_come_back_together_sets_first_and_nothing_is_written(user):
    first = log(user, *[BENCH] * 60, notes="n" * 2000)
    before = stored(first["id"])
    both = log(user, BENCH, BENCH, notes="more")
    sets_error = {"error": "sets_count_out_of_range", "message": sets_message(user, 60, 2), "fix": SETS_FIX}
    notes_error = {"error": "invalid_field", "message": "The session's notes would be 2,005 characters; the most is "
                                                      "2,000.", "fix": NOTES_FIX}
    assert both == {**sets_error, "message": f"2 problems. {sets_error['message']} {notes_error['message']}",
                    "fix": f"{SETS_FIX} {NOTES_FIX}", "errors": [sets_error, notes_error]}
    assert stored(first["id"]) == before
    # The call's own checks come first, as before: a bad set is its own error, and the add is not judged.
    own = log(user, ("Bench Press", 80, 31))
    assert [e["error"] for e in own["errors"]] == ["reps_out_of_range"] and stored(first["id"]) == before


def test_an_add_s_words_keep_the_text_rules():
    for text in (DESCRIPTION, *ARGUMENTS.values(), *RESULT.values()):
        assert tr.check(text, tr.CLAUDE) == [], text
    for text in (SETS_FIX, NOTES_FIX, "The session on Sun 15 Nov has 58 sets; 3 more would make 61, and a session "
                 "holds at most 60.", "The session's notes would be 2,001 characters; the most is 2,000."):
        assert tr.check(text, tr.MESSAGE) == [], text
    assert tr.check("Added Bench Press 1 × 5 at 80 kg to the session on Sun 15 Nov. It now has 3 sets. No new bests.",
                    tr.SUMMARY) == []


# ---------------------------------------------------------------- bests (decision 9)

def test_an_add_that_raises_the_session_s_estimated_max_reports_it_as_get_log_does_and_again_reports_nothing(user):
    log(user, ("Back Squat", 100, 5), date=day(user, -7))           # 116.67
    today = log(user, ("Back Squat", 100, 5))                        # the same: no best
    assert today["new_bests"] == [] and today["summary"].endswith(" No new bests.")
    added = log(user, ("Back Squat", 100, 8))                        # 126.67, at the same weight
    best = [("estimated_max", 126.7, 116.7, "Back Squat estimated max 126.7 kg, up from 116.7 kg")]
    assert reported(added) == best == logged_bests(user, today["id"], day(user))
    assert added["summary"].endswith(" New best: Back Squat estimated max 126.7 kg, up from 116.7 kg.")
    again = log(user, ("Back Squat", 100, 8))                        # the same add sent again: the session holds it
    assert again["new_bests"] == [] and again["summary"].endswith(" No new bests.")
    assert logged_bests(user, today["id"], day(user)) == best


def test_an_add_that_raises_only_the_heaviest_weight_reports_only_that(user):
    log(user, ("Back Squat", 100, 8), date=day(user, -7))           # 126.67, 100 kg
    today = log(user, ("Back Squat", 90, 5))
    added = log(user, ("Back Squat", 102.5, 1))                      # 105.92: under 126.67; 102.5 kg over 100
    best = [("weight", 102.5, 100, "Back Squat heaviest weight 102.5 kg, up from 100 kg")]
    assert reported(added) == best == logged_bests(user, today["id"], day(user))


def test_with_two_sessions_on_the_date_the_earlier_one_holds_the_lift_and_counts_before_the_later(user):
    log(user, ("Back Squat", 100, 5))                                # the morning: the mark
    evening = log(user, ("Back Squat", 90, 5), new_session=True)     # under the morning: no best
    assert evening["new_bests"] == []
    added = log(user, ("Back Squat", 105, 5))                        # to the evening: 122.5 against 116.67
    best = [("estimated_max", 122.5, 116.7, "Back Squat estimated max 122.5 kg, up from 116.7 kg"),
            ("weight", 105, 100, "Back Squat heaviest weight 105 kg, up from 100 kg")]
    assert reported(added) == best == logged_bests(user, evening["id"], day(user))


def test_a_back_dated_add_is_not_measured_against_a_heavier_later_dated_session(user):
    log(user, ("Back Squat", 80, 5), date=day(user, -10))           # 93.33
    week = log(user, ("Back Squat", 90, 5), date=day(user, -5))      # 105 against 93.33
    log(user, ("Back Squat", 120, 5))                                # today: heavier than all, and later
    added = log(user, ("Back Squat", 95, 5), date=day(user, -5))     # 110.83 against 93.33, never against 140
    best = [("estimated_max", 110.8, 93.3, "Back Squat estimated max 110.8 kg, up from 93.3 kg"),
            ("weight", 95, 80, "Back Squat heaviest weight 95 kg, up from 80 kg")]
    assert reported(added) == best == logged_bests(user, week["id"], day(user, -5))


def test_a_session_dated_after_today_is_prior_for_an_add_to_today_s_session(user, monkeypatch):
    later = db.utcnow() + timedelta(days=2)
    monkeypatch.setattr(db, "utcnow", lambda: later)
    log(user, ("Back Squat", 100, 5))                                # dated after today: it counts as today's
    monkeypatch.undo()
    today = log(user, ("Back Squat", 90, 5))                         # today's, a higher id: no best
    assert today["new_bests"] == [] and today["session"]["date"] == day(user)
    added = log(user, ("Back Squat", 105, 5))                        # 122.5 against the later-dated 116.67
    best = [("estimated_max", 122.5, 116.7, "Back Squat estimated max 122.5 kg, up from 116.7 kg"),
            ("weight", 105, 100, "Back Squat heaviest weight 105 kg, up from 100 kg")]
    assert added["id"] == today["id"] and reported(added) == best == logged_bests(user, today["id"], day(user))


def test_today_s_session_is_not_prior_for_an_add_to_a_back_dated_session_logged_after_it(user):
    log(user, ("Back Squat", 140, 5))                                # today, logged first: the lowest id
    log(user, ("Back Squat", 80, 5), date=day(user, -10))           # 93.33: before the back-dated session
    week = log(user, ("Back Squat", 90, 5), date=day(user, -5))      # a higher id than today's, an earlier date
    added = log(user, ("Back Squat", 95, 5), date=day(user, -5))     # 110.83 against 93.33, never against 163.33
    best = [("estimated_max", 110.8, 93.3, "Back Squat estimated max 110.8 kg, up from 93.3 kg"),
            ("weight", 95, 80, "Back Squat heaviest weight 95 kg, up from 80 kg")]
    assert added["id"] == week["id"] and reported(added) == best == logged_bests(user, week["id"], day(user, -5))


def test_a_set_that_beats_only_an_earlier_set_of_its_own_session_is_no_new_best(user):
    log(user, ("Back Squat", 110, 5), date=day(user, -7))
    today = log(user, ("Back Squat", 100, 5))
    added = log(user, ("Back Squat", 105, 5))                        # over the 100 of this session, under the 110
    assert added["new_bests"] == [] and added["summary"].endswith(" No new bests.")
    assert logged_bests(user, today["id"], day(user)) == []


def test_a_lift_s_first_session_gives_its_mark_on_every_add_from_the_whole_session_and_never_a_best(user):
    log(user, ("Back Squat", 100, 5), date=day(user, -2))
    log(user, ("Back Squat", 100, 5))
    one = log(user, ("Bench Press", 60, 5))                          # a lift new to this add: no earlier session
    two = log(user, ("Bench Press", 80, 5))                          # heavier, still the lift's first session
    assert one["summary"].endswith(" First Bench Press session: estimated max 70 kg is the mark to beat.")
    assert two["summary"].endswith(" First Bench Press session: estimated max 93.3 kg is the mark to beat.")
    assert one["new_bests"] == two["new_bests"] == []


# ---------------------------------------------------------------- allow_repeat (decision 3)

def test_allow_repeat_sent_directly_is_taken_and_the_answer_and_the_rows_are_what_they_are_without_it(make_user):
    with_it, without = make_user(), make_user()
    answers, kept = [], []
    for person, extra in ((with_it, {"allow_repeat": True}), (without, {})):
        log(person, BENCH, notes="Easy")
        answers.append(log(person, BENCH, notes="Easy", **extra))
        kept.append(held(answers[-1]["id"]))

    def plain(r):
        return {**{k: v for k, v in r.items() if k != "id"},
                "session": {k: v for k, v in r["session"].items() if k not in ("id", "created_at", "updated_at")}}

    assert plain(answers[0]) == plain(answers[1]) and kept[0] == kept[1]
    assert len(answers[0]["session"]["sets"]) == 2 and answers[0]["duplicate"] is False


def test_allow_repeat_sent_through_mcp_is_taken_and_adds_as_any_call_does(writer):
    http, token, _ = writer
    first = ok(http, token, "log_lifts", {"sets": sets(BENCH)})
    added = ok(http, token, "log_lifts", {"sets": sets(BENCH), "allow_repeat": True})
    assert added["id"] == first["id"] and added["duplicate"] is False and len(added["session"]["sets"]) == 2
    assert rows()[-1].outcome == "written" and "allow_repeat" in rows()[-1].tried.split()


# ---------------------------------------------------------------- a write's key (decision 16)

def test_the_same_key_and_call_twice_is_one_add_and_the_first_answer_and_its_second_row_reads_repeat(writer):
    http, token, _ = writer
    ok(http, token, "log_lifts", {"sets": sets(BENCH)})
    sent = {"sets": sets(BENCH), "key": K}
    first = call(http, token, "log_lifts", sent)
    again = call(http, token, "log_lifts", sent)
    assert again == first and len(first["structuredContent"]["session"]["sets"]) == 2
    assert len(stored(first["structuredContent"]["id"])["sets"]) == 2
    assert [r.outcome for r in rows()[-2:]] == ["written", "repeat"]


def test_a_key_on_an_add_that_was_refused_is_free(writer):
    http, token, _ = writer
    full = ok(http, token, "log_lifts", {"sets": sets(*[BENCH] * 60)})
    refused = failed(http, token, "log_lifts", {"sets": sets(BENCH), "key": K})
    assert refused["error"] == "sets_count_out_of_range" and keys() == []
    taken = ok(http, token, "log_lifts", {"sets": sets(BENCH), "new_session": True, "key": K})
    assert taken["id"] != full["id"] and [r.outcome for r in rows()[-2:]] == ["refused", "written"]


# ---------------------------------------------------------------- two at once, the lock, the statements

def test_two_adds_at_once_both_land_one_after_the_other(user, monkeypatch):
    first = log(user, BENCH)
    numbered = lifts.number_sets

    def slow(*args, **kwargs):  # after the session is found: without the lock both would read the same sets
        found = numbered(*args, **kwargs)
        time.sleep(0.2)
        return found

    monkeypatch.setattr(lifts, "number_sets", slow)
    barrier = threading.Barrier(2)
    results, failures = [], []

    def add(rows_):
        try:
            barrier.wait(timeout=5)
            results.append(services.log_lifts(user.id, {"sets": sets(*rows_)}, "claude"))
        except Exception as exc:  # reported below, so a crash in a thread fails the test
            failures.append(exc)

    threads = [threading.Thread(target=add, args=([BENCH] * 2,)),
               threading.Thread(target=add, args=([("Bench Press", 82.5, 5)] * 3,))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not failures, failures
    assert [r["id"] for r in results] == [first["id"]] * 2 and not any(r["duplicate"] for r in results)
    assert sorted(len(r["session"]["sets"]) for r in results)[-1] == 6
    assert [x[2] for x in stored(first["id"])["sets"]] == [1, 2, 3, 4, 5, 6] and len(versions(first["id"])) == 2


def count_queries(fn):
    statements = []

    def record(_conn, _cursor, statement, *_):
        statements.append(statement)

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return result, statements


def flat(statement: str) -> str:
    return " ".join(statement.split())


ADD_STATEMENTS = 11  # as built, for one new set: the lock, the user, the write limit, the exercises, the session,
# its sets, the earlier sets, the version and the look for versions past ten, the session's update, the new set.
# On SQLite the ORM inserts each new set on its own, as a new session's sets always were: k new sets are 10 + k.


def test_an_add_locks_first_and_once_and_runs_as_many_statements_on_a_session_of_30_sets_as_on_one_of_3(make_user):
    small, large = make_user(), make_user()
    log(small, *[BENCH] * 3)
    log(large, *[BENCH] * 30)
    counts = {}
    for held_sets, person in ((3, small), (30, large)):
        for more in (1, 29):
            result, statements = count_queries(lambda: log(person, *[BENCH] * more))
            assert "error" not in result and result["summary"].startswith("Added "), result
            lock = lock_sql()
            assert flat(statements[0]) == lock and sum(flat(s) == lock for s in statements) == 1
            counts[held_sets, more] = len(statements)
    assert counts[3, 1] == counts[30, 1] == ADD_STATEMENTS
    assert counts[3, 29] == counts[30, 29] == ADD_STATEMENTS - 1 + 29
    result, statements = count_queries(lambda: log(make_user(), BENCH, BENCH))  # a new session: the bound of 12
    assert result["summary"].startswith("Bench Press 2 × 5") and len(statements) <= 12, statements


# ---------------------------------------------------------------- the reads (decision 11)

def test_the_reads_count_one_session_for_a_day_said_set_by_set(user):
    for row in (("Back Squat", 100, 5),) * 3 + ((BENCH),):
        log(user, row)
    assert services.get_trends(user.id)["sessions"]["this_week"] == 1
    grid = services.get_days(user.id)
    today = next(d for d in grid["days"] if d["date"] == day(user))
    assert (today["lift_sessions"], grid["lift_sessions_28d"], grid["lift_days_28d"]) == (1, 1, 1)
    squat = services.get_progress(user.id, "Back Squat")["lift"]
    assert squat["sessions"] == 1 and len(squat["estimated_max_over_time"]) == 1
    [session] = services.get_log(user.id)["days"][0]["sessions"]
    assert len(session["sets"]) == 4


# ---------------------------------------------------------------- through MCP (decisions 2, 3, 14, 15)

def test_the_published_description_arguments_and_result_fields_are_the_contract_s_words(http):
    [tool] = [t for t in tool_list(http, sign()) if t["name"] == "log_lifts"]
    assert (tool["title"], tool["description"]) == ("Log a lift session", DESCRIPTION)
    schema = tool["inputSchema"]
    assert list(schema["properties"]) == ["sets", "date", "notes", "allow_repeat", "new_session", "key"]
    assert {name: schema["properties"][name]["description"] for name in ARGUMENTS} == ARGUMENTS
    assert schema["required"] == ["sets"]  # new_session and allow_repeat are optional, false when left out
    for name in ("allow_repeat", "new_session"):
        assert (schema["properties"][name]["type"], schema["properties"][name]["default"]) == ("boolean", False)
    out = tool["outputSchema"]["properties"]
    assert {name: out[name]["description"] for name in RESULT} == RESULT and "note" not in out
    assert "note" not in LogLiftsResult.model_fields
    assert tool["annotations"] == {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
                                   "openWorldHint": False}  # WRITE, as before


def test_an_add_s_answer_passes_its_model_carries_no_note_and_its_row_reads_written_naming_new_session(writer):
    http, token, _ = writer
    first = call(http, token, "log_lifts", {"sets": sets(BENCH)})
    added = call(http, token, "log_lifts", {"sets": sets(BENCH), "notes": "Felt fine"})
    second = call(http, token, "log_lifts", {"sets": sets(BENCH), "new_session": True})
    for result in (first, added, second):
        assert result["isError"] is False and "note" not in result["structuredContent"]
        LogLiftsResult.model_validate(result["structuredContent"])
    answers = [r["structuredContent"] for r in (first, added, second)]
    assert answers[1]["id"] == answers[0]["id"] != answers[2]["id"]
    assert answers[1]["summary"].startswith("Added Bench Press 1 × 5 at 80 kg to the session on ")
    made = rows()[-3:]
    assert [(r.function, r.outcome, r.ok) for r in made] == [("log_lifts", "written", True)] * 3
    assert "new_session" not in made[1].tried.split() and "new_session" in made[2].tried.split()
