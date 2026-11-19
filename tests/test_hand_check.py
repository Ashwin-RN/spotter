"""The per-hand check, package C4f (contract exercise-list.md, decision 32, R12, R13): a pair total above the person's
heaviest set on the bar lift it is compared with asks once, in hand_check; the number is stored as sent; the ratio
goes to the call log's notes as `pair`, never a weight. In the family (Dumbbell Bench Press against Bench Press),
through each of the 4 compare_with links, against the barbell of two bar lifts, against the EZ bar alone; no check
with the bar lift never logged, or at or below it; asked once, then asked again once the earlier set is deleted.

Also the extra item of 2026-11-19, C4f (the owner's pending pick A): list_exercises says "a side" for a lift done one
side at a time, and the set fields say a side's reps or time.

Every date is a fixed today through db.utcnow, never the real date.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import calllog, db, exercises, rules, services
from app.mcp_schemas import LogLiftsResult, LogSetIn, SetIn
from app.models import CallLog, Exercise
from app.services import lifts

from . import text_rules as tr
from .conftest import sign
from .test_exercise_list import LINKS, PER_HAND_NO_LINK, _compared_with
from .test_mcp import call, ok, one_user, tool_list

NOW = datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


@pytest.fixture
def uid(make_user) -> int:
    return make_user(time_zone="UTC").id


def day(n: int) -> str:
    """The nth day of November 2026, on or before the fixed today (10 Nov)."""
    return f"2026-11-{n:02d}"


def one(exercise: str, weight, reps: int = 8) -> dict:
    return {"exercise": exercise, "exact": True, "weight_kg": weight, "reps": reps}


def log(uid: int, on: str, *sets: dict, new: bool = True) -> dict:
    out = services.log_lifts(uid, {"date": on, "sets": list(sets), "new_session": new or None}, "claude")
    assert "error" not in out, out
    LogLiftsResult.model_validate(out)
    return out


def line(lift: str, each: str, both: str, bar: str, best: str, hand: str, half: str) -> str:
    return (f"{lift}: {each} each is {both} in both hands, above the heaviest {bar} set logged, {best}. Ask the "
            f"person once: {hand} a hand, or {half} a hand? If {half}, change it with edit_entry.")


def clean(text: str) -> None:
    for surface in (tr.SUMMARY, tr.CLAUDE):
        assert tr.check(text, surface) == [], text


# ---------------------------------------------------------------- the lift it is compared with

def test_the_compared_lift_is_the_list_s_own_for_every_per_hand_lift_and_none_for_the_rest():
    for m in exercises.MOVEMENTS:
        got = lifts.compared_lift(m.name)
        if m.kind != "lift" or m.load_type != "per_hand":
            assert got is None, m.name  # Machine Row carries compare_with, but is no pair: no check
            continue
        target = m.family if any(x.load_type == "bar_total" for x in exercises.FAMILIES[m.family]) else m.compare_with
        if target is None:
            assert m.family in PER_HAND_NO_LINK and got is None, m.name
        else:
            assert [got] == _compared_with(target), m.name
    assert lifts.compared_lift("Machine Row") is None and lifts.compared_lift("Not A Lift") is None
    assert lifts.compared_lift(None) is None


def test_the_four_links_the_barbell_of_two_and_the_ez_bar_alone():
    assert {f: lifts.compared_lift(exercises.FAMILIES[f][0].name) for f in LINKS} == {
        "chest_supported_dumbbell_row": "Barbell Row", "dumbbell_arnold_press": "Overhead Press",
        "dumbbell_hammer_curl": "Barbell Curl", "incline_dumbbell_curl": "Barbell Curl"}
    assert lifts.compared_lift("Dumbbell Deadlift") == "Deadlift"  # beside Trap Bar Deadlift
    assert lifts.compared_lift("Dumbbell Curl") == "Barbell Curl"  # beside EZ Bar Curl
    assert lifts.compared_lift("Dumbbell Shrug") == "Barbell Shrug"  # beside Trap Bar Shrug
    assert lifts.compared_lift("Dumbbell Preacher Curl") == "EZ Bar Preacher Curl"  # its only bar lift
    assert lifts.compared_lift("Dumbbell Skull Crusher") == "EZ Bar Skull Crusher"
    assert lifts.compared_lift("Dumbbell Bench Press") == "Bench Press"
    assert lifts.compared_lift("Farmer's Carry") is None and lifts.compared_lift("Lateral Raise") is None


# ---------------------------------------------------------------- the check through log_lifts

def test_in_the_family_a_pair_total_above_the_bar_best_asks_once_and_the_number_is_stored_as_sent(uid):
    log(uid, day(3), one("Bench Press", 80, 5))
    out = log(uid, day(10), one("Dumbbell Bench Press", 50), one("Dumbbell Bench Press", 45))
    assert out["hand_check"] == line("Dumbbell Bench Press", "50 kg", "100 kg", "Bench Press", "80 kg", "50", "25")
    clean(out["hand_check"])
    assert [x["weight_kg"] for x in out["session"]["sets"]] == [50, 45]  # never changed
    stored = services.get_log(uid, day(10))["days"][0]["sessions"][0]["sets"]
    assert [x["weight_kg"] for x in stored] == [50, 45]
    assert "25 kg" not in out["summary"]


@pytest.mark.parametrize("lift, bar", [("Chest-Supported Dumbbell Row", "Barbell Row"),
                                       ("Dumbbell Arnold Press", "Overhead Press"),
                                       ("Dumbbell Hammer Curl", "Barbell Curl"),
                                       ("Incline Dumbbell Curl", "Barbell Curl")])
def test_through_each_of_the_four_links(uid, lift, bar):
    log(uid, day(2), one(bar, 30))
    out = log(uid, day(9), one(lift, 20))
    assert out["hand_check"] == line(lift, "20 kg", "40 kg", bar, "30 kg", "20", "10")


def test_against_the_barbell_of_two_bar_lifts_and_never_the_other(uid):
    log(uid, day(1), one("Trap Bar Shrug", 200))  # the heavier bar lift is not the one compared with
    assert "hand_check" not in log(uid, day(2), one("Dumbbell Shrug", 60))  # Barbell Shrug never logged: no check
    log(uid, day(1), one("Trap Bar Deadlift", 200, 5), one("Deadlift", 100, 5))
    out = log(uid, day(6), one("Dumbbell Deadlift", 60))
    assert out["hand_check"] == line("Dumbbell Deadlift", "60 kg", "120 kg", "Deadlift", "100 kg", "60", "30")
    log(uid, day(7), one("EZ Bar Curl", 60))
    log(uid, day(7), one("Barbell Curl", 30))
    assert log(uid, day(8), one("Dumbbell Curl", 20))["hand_check"].startswith(
        "Dumbbell Curl: 20 kg each is 40 kg in both hands, above the heaviest Barbell Curl set logged, 30 kg.")


def test_against_the_ez_bar_alone(uid):
    log(uid, day(1), one("EZ Bar Preacher Curl", 30))
    out = log(uid, day(2), one("Dumbbell Preacher Curl", 17.5))
    assert out["hand_check"] == line("Dumbbell Preacher Curl", "17.5 kg", "35 kg", "EZ Bar Preacher Curl", "30 kg",
                                     "17.5", "8.75")
    clean(out["hand_check"])


def test_no_check_with_the_bar_lift_never_logged_or_at_or_below_it(uid, make_user):
    assert "hand_check" not in log(uid, day(1), one("Dumbbell Bench Press", 50))  # no Bench Press yet
    assert "hand_check" not in log(uid, day(2), one("Lateral Raise", 50))  # no bar lift to compare with
    for each in (40, 30, 39.99):  # 80 kg in both hands: at it; then below it. Each on a fresh account, so no
        fresh = make_user(time_zone="UTC").id  # earlier set stops the line
        log(fresh, day(2), one("Bench Press", 80, 5))
        assert "hand_check" not in log(fresh, day(3), one("Dumbbell Bench Press", each)), each
    fresh = make_user(time_zone="UTC").id
    log(fresh, day(2), one("Bench Press", 80, 5))
    assert log(fresh, day(3), one("Dumbbell Bench Press", 40.01))["hand_check"]  # just above it


@pytest.mark.parametrize("bench, each", [(185, 92.5), (135, 67.5), (95, 47.5), (45, 22.5), (245, 122.5)])
def test_in_lb_a_pair_at_the_bar_best_as_the_person_sees_it_asks_nothing(make_user, bench, each):
    uid = make_user(time_zone="UTC", weight_unit="lb").id  # stored at 0.01 kg, each figure reads the same in lb
    log(uid, day(1), {"exercise": "Bench Press", "exact": True, "weight_lb": bench, "reps": 5})
    assert "hand_check" not in log(uid, day(2), {"exercise": "Dumbbell Bench Press", "exact": True,
                                                 "weight_lb": each, "reps": 8})


def test_in_lb_an_earlier_pair_at_the_bar_best_as_the_person_sees_it_never_stops_the_ask(make_user):
    uid = make_user(time_zone="UTC", weight_unit="lb").id
    log(uid, day(1), {"exercise": "Bench Press", "exact": True, "weight_lb": 185, "reps": 5})
    log(uid, day(2), {"exercise": "Dumbbell Bench Press", "exact": True, "weight_lb": 92.5, "reps": 8})  # at it
    out = log(uid, day(3), {"exercise": "Dumbbell Bench Press", "exact": True, "weight_lb": 95, "reps": 8})
    assert out["hand_check"].startswith("Dumbbell Bench Press: 95 lb each is 190 lb in both hands")


def test_in_lb_a_pair_above_the_bar_best_asks(make_user):
    uid = make_user(time_zone="UTC", weight_unit="lb").id
    log(uid, day(1), {"exercise": "Bench Press", "exact": True, "weight_lb": 185, "reps": 5})
    out = log(uid, day(2), {"exercise": "Dumbbell Bench Press", "exact": True, "weight_lb": 95, "reps": 8})
    assert out["hand_check"] == line("Dumbbell Bench Press", "95 lb", "190 lb", "Bench Press", "185 lb", "95", "47.5")


def test_a_bar_lift_at_0_kg_is_never_compared_with(uid):
    log(uid, day(1), one("Bench Press", 0, 10))
    assert "hand_check" not in log(uid, day(2), one("Dumbbell Bench Press", 20))


def test_asked_once_then_not_at_a_heavier_weight_and_asked_again_once_the_earlier_set_is_deleted(uid):
    log(uid, day(1), one("Bench Press", 80, 5))
    first = log(uid, day(3), one("Dumbbell Bench Press", 50))
    assert first["hand_check"]
    second = log(uid, day(5), one("Dumbbell Bench Press", 55))
    assert "hand_check" not in second  # the person was asked at 50 kg a hand, and kept the weight
    assert services.delete_entry(uid, "lift_session", first["id"])["deleted"] is True
    assert "hand_check" not in log(uid, day(6), one("Dumbbell Bench Press", 55))  # the 55 kg set is still there
    for out in (second, services.get_log(uid, day(6))):
        sid = out["id"] if "id" in out else out["days"][0]["sessions"][0]["id"]
        assert services.delete_entry(uid, "lift_session", sid)["deleted"] is True
    again = log(uid, day(7), one("Dumbbell Bench Press", 55))
    assert again["hand_check"] == line("Dumbbell Bench Press", "55 kg", "110 kg", "Bench Press", "80 kg", "55",
                                       "27.5")


def test_an_earlier_set_counts_only_against_the_bar_best_as_it_stands_now(uid):
    log(uid, day(1), one("Bench Press", 80, 5))
    assert log(uid, day(2), one("Dumbbell Bench Press", 50))["hand_check"]  # 100 kg above 80 kg
    log(uid, day(3), one("Bench Press", 120, 3))
    out = log(uid, day(4), one("Dumbbell Bench Press", 65))  # the 50 kg set is no longer above the bar best
    assert out["hand_check"] == line("Dumbbell Bench Press", "65 kg", "130 kg", "Bench Press", "120 kg", "65",
                                     "32.5")


def test_only_the_person_s_own_sets_are_read(uid, make_user):
    other = make_user(time_zone="UTC").id
    log(other, day(1), one("Bench Press", 200, 1), one("Dumbbell Bench Press", 120))
    assert "hand_check" not in log(uid, day(2), one("Dumbbell Bench Press", 50))  # this person: no Bench Press
    log(uid, day(3), one("Bench Press", 80, 5))
    log(other, day(4), one("Dumbbell Bench Press", 10))
    log(uid, day(4), one("Dumbbell Bench Press", 50))  # asked: 100 kg against this person's own 80 kg
    out = log(uid, day(5), one("Dumbbell Bench Press", 45))
    assert "hand_check" not in out  # 90 kg against 80 kg, but this person's own 50 kg set was above it already
    fresh = make_user(time_zone="UTC").id
    log(fresh, day(1), one("Bench Press", 80, 5))
    assert log(fresh, day(2), one("Dumbbell Bench Press", 50))["hand_check"] == line(
        "Dumbbell Bench Press", "50 kg", "100 kg", "Bench Press", "80 kg", "50", "25")  # never the other's 200 kg


# ---------------------------------------------------------------- asked once, in the order sets were logged
# An earlier set of the lift stops the line only when it was logged after the bar lift's first live set above 0 kg,
# by set id, whatever the dates (exercise-list.md, note of 2026-11-19, C4f (ae)). Each case on a fresh account.

BENCH, DB = "Bench Press", "Dumbbell Bench Press"
ASKED_50 = line(DB, "50 kg", "100 kg", BENCH, "80 kg", "50", "25")


@pytest.fixture
def fresh(make_user):
    return lambda: make_user(time_zone="UTC").id


def test_a_dumbbell_set_before_the_bench_never_stops_the_ask(fresh):  # (a)
    uid = fresh()
    log(uid, day(1), one(DB, 50))  # no Bench Press yet: nothing asked
    log(uid, day(2), one(BENCH, 80, 5))
    assert log(uid, day(3), one(DB, 50))["hand_check"] == ASKED_50


def test_asked_then_a_heavier_set_is_not_asked(fresh):  # (b)
    uid = fresh()
    log(uid, day(1), one(BENCH, 80, 5))
    assert log(uid, day(2), one(DB, 50))["hand_check"] == ASKED_50
    assert "hand_check" not in log(uid, day(3), one(DB, 55))


def test_a_back_dated_dumbbell_set_after_the_bench_was_asked(fresh):  # (c)
    uid = fresh()
    log(uid, day(5), one(BENCH, 80, 5))
    assert log(uid, day(2), one(DB, 50))["hand_check"] == ASKED_50  # dated before the bench, logged after it
    assert "hand_check" not in log(uid, day(6), one(DB, 50))


def test_a_back_dated_bench_never_makes_an_earlier_dumbbell_set_count(fresh):  # (d)
    uid = fresh()
    log(uid, day(3), one(DB, 50))
    log(uid, day(1), one(BENCH, 80, 5))  # dated before the dumbbell set, logged after it
    assert log(uid, day(5), one(DB, 50))["hand_check"] == ASKED_50


@pytest.mark.parametrize("new", [False, True])  # an add to the day's session, and a session of its own
def test_a_dumbbell_set_the_same_day_before_the_bench_never_stops_the_ask(fresh, new):  # (e)
    uid = fresh()
    log(uid, day(2), one(DB, 50))
    log(uid, day(2), one(BENCH, 80, 5), new=new)
    assert log(uid, day(3), one(DB, 50))["hand_check"] == ASKED_50


def test_a_bench_of_0_kg_never_starts_the_order(fresh):  # (f)
    uid = fresh()
    log(uid, day(1), one(BENCH, 0, 10))
    assert "hand_check" not in log(uid, day(2), one(DB, 50))  # nothing to compare with
    log(uid, day(3), one(BENCH, 80, 5))
    assert log(uid, day(4), one(DB, 50))["hand_check"] == ASKED_50


def test_the_call_holding_the_first_bench_set_asks_and_a_later_set_does_not(fresh):  # (g)
    uid = fresh()
    log(uid, day(1), one(DB, 60))
    out = log(uid, day(2), one(BENCH, 80, 5), one(DB, 50))  # no stored dumbbell set counts
    assert out["hand_check"] == ASKED_50
    assert "hand_check" not in log(uid, day(3), one(DB, 50))


def test_the_call_with_the_dumbbell_set_sent_before_the_first_bench_set_asks_again_later(fresh):
    uid = fresh()  # a known limit: within the call the dumbbell set is logged first, so it never counts
    assert log(uid, day(2), one(DB, 50), one(BENCH, 80, 5))["hand_check"] == ASKED_50
    assert log(uid, day(3), one(DB, 50))["hand_check"] == ASKED_50  # asked twice: the safe side


def test_a_back_dated_call_with_a_bench_and_a_dumbbell_set(fresh):  # (h), the note-3 case
    uid = fresh()
    log(uid, day(3), one(DB, 60))
    log(uid, day(5), one(BENCH, 80, 5))  # the bench's first set above 0 kg
    out = log(uid, day(1), one(BENCH, 70, 5), one(DB, 50))
    # The day 3 set was logged before the first bench set, so it counts for nothing; the bar best is 80 kg, and
    # 100 kg is above it: asked.
    assert out["hand_check"] == ASKED_50


def test_the_call_s_own_bar_sets_count_as_logged(uid):
    out = log(uid, day(2), one("Bench Press", 60, 5), one("Dumbbell Bench Press", 40))
    assert out["hand_check"] == line("Dumbbell Bench Press", "40 kg", "80 kg", "Bench Press", "60 kg", "40", "20")
    assert "hand_check" not in log(uid, day(3), one("Bench Press", 100, 5), one("Dumbbell Bench Press", 45))


def test_an_add_to_the_day_s_session_checks_and_its_own_earlier_sets_count(uid):
    log(uid, day(10), one("Bench Press", 80, 5))
    added = log(uid, day(10), one("Dumbbell Bench Press", 50), new=False)
    assert added["summary"].startswith("Added ") and added["hand_check"]
    again = log(uid, day(10), one("Dumbbell Bench Press", 52.5), new=False)
    assert "hand_check" not in again  # the session's own 50 kg set was asked about


def test_one_line_a_lift_in_the_order_sent(uid):
    log(uid, day(1), one("Bench Press", 80, 5), one("Barbell Row", 60))
    out = log(uid, day(2), one("Chest-Supported Dumbbell Row", 35), one("Dumbbell Bench Press", 45),
              one("Dumbbell Bench Press", 42.5))
    assert out["hand_check"] == " ".join([
        line("Chest-Supported Dumbbell Row", "35 kg", "70 kg", "Barbell Row", "60 kg", "35", "17.5"),
        line("Dumbbell Bench Press", "45 kg", "90 kg", "Bench Press", "80 kg", "45", "22.5")])


def test_in_lb_the_weights_are_the_person_s(make_user):
    uid = make_user(time_zone="UTC", weight_unit="lb").id
    services.log_lifts(uid, {"date": day(1), "sets": [{"exercise": "Bench Press", "weight_lb": 185, "reps": 5}]},
                       "claude")
    out = services.log_lifts(uid, {"date": day(2), "sets": [{"exercise": "Dumbbell Bench Press", "exact": True,
                                                              "weight_lb": 100, "reps": 8}]}, "claude")
    assert out["hand_check"] == line("Dumbbell Bench Press", "100 lb", "200 lb", "Bench Press", "185 lb", "100", "50")
    assert out["session"]["sets"][0]["weight_kg"] == 45.36  # stored as sent, in kg


def test_a_refused_call_carries_no_check_and_writes_nothing(uid):
    log(uid, day(10), one("Bench Press", 80, 5))
    full = [one("Bench Press", 60, 5)] * 59
    log(uid, day(10), *full, new=False)  # 60 sets in the session
    out = services.log_lifts(uid, {"date": day(10), "sets": [one("Dumbbell Bench Press", 50)]}, "claude")
    assert out["error"] == "sets_count_out_of_range" and "hand_check" not in out


# ---------------------------------------------------------------- the call log's pair note (R12)

def rows() -> list[CallLog]:
    with db.session_scope() as s:
        return list(s.scalars(select(CallLog).order_by(CallLog.id)))


def ids(*names: str) -> list[int]:
    with db.session_scope() as s:
        by = {e.name: e.id for e in s.scalars(select(Exercise).where(Exercise.name.in_(names)))}
    return [by[n] for n in names]


def test_the_ratio_is_noted_whenever_the_bar_lift_is_logged_and_never_a_weight(http):
    token = sign()
    ok(http, token, "log_lifts", {"sets": [one("Bench Press", 81.15, 5)], "date": day(1)})
    ok(http, token, "log_lifts", {"sets": [one("Dumbbell Bench Press", 37.35)], "date": day(2), "new_session": True})
    asked = ok(http, token, "log_lifts", {"sets": [one("Dumbbell Bench Press", 43.65)], "date": day(3),
                                         "new_session": True})
    assert asked["hand_check"]
    [dumbbell] = ids("Dumbbell Bench Press")
    # 74.7 over 81.15 is 0.92, at or below it: noted, not asked. 87.3 over 81.15 is 1.08: noted and asked.
    assert [r.notes for r in rows()] == [None, f"pair={dumbbell}:0.92", f"pair={dumbbell}:1.08"]
    for row in rows():
        for column in CallLog.__table__.columns:
            text = str(getattr(row, column.key))
            for weight in ("81.15", "37.35", "43.65", "74.7", "87.3", "40.58", "18.68", "21.83"):
                assert weight not in text, (column.name, weight)


def test_no_note_without_the_bar_lift_and_none_on_a_refusal(http):
    token = sign()
    ok(http, token, "log_lifts", {"sets": [one("Dumbbell Bench Press", 40)], "date": day(1)})  # no Bench Press
    result = call(http, token, "log_lifts", {"sets": [one("Dumbbell Bench Press", 40)], "date": "soon"})
    assert result["isError"] is True
    assert [r.notes for r in rows()] == [None, None]


def test_the_pair_note_is_cut_at_a_whole_item_to_fit_the_column(http):
    pairs = {}
    for m in exercises.MOVEMENTS:
        bar = lifts.compared_lift(m.name)
        if bar is not None and m.measures == ("weight", "reps"):
            pairs[m.name] = bar
    assert len(pairs) >= 20
    token = sign()
    bars = list(dict.fromkeys(pairs.values()))
    ok(http, token, "log_lifts", {"sets": [one(b, 61.35, 5) for b in bars], "date": day(1)})
    out = ok(http, token, "log_lifts", {"sets": [one(n, 23.45) for n in pairs], "date": day(2), "new_session": True})
    assert "hand_check" not in out  # 46.9 kg in both hands, below 61.35 kg
    noted = rows()[-1].notes
    assert noted.startswith("pair=") and len(noted) <= calllog.NOTES_MAX
    items = noted.removeprefix("pair=").split(",")
    every = [f"{i}:0.76" for i in ids(*pairs)]
    assert items == every[:len(items)] and len(items) < len(every)  # whole items, in the order sent, cut
    assert len(",".join(every[:len(items) + 1])) > calllog.NOTES_MAX - len("pair=")  # the next would not fit
    assert "61.35" not in noted and "23.45" not in noted and "46.9" not in noted


def test_pair_note_cuts_at_a_whole_item():
    items = [f"{n}:1.25" for n in range(100, 140)]
    word = lifts.pair_note(items)
    assert len("pair=" + word) <= calllog.NOTES_MAX and word.split(",") == items[:len(word.split(","))]
    assert len("pair=" + word + "," + items[len(word.split(","))]) > calllog.NOTES_MAX
    assert lifts.pair_note(["7:1"]) == "7:1" and lifts.pair_note([]) == ""
    assert lifts._ratio(100, 80) == "1.25" and lifts._ratio(80, 100) == "0.8" and lifts._ratio(160, 80) == "2"


# ---------------------------------------------------------------- the published field

def test_hand_check_is_published_and_described(http):
    tool = next(t for t in tool_list(http, sign()) if t["name"] == "log_lifts")
    spec = tool["outputSchema"]["properties"]["hand_check"]
    assert spec["description"] == ("Present when a dumbbell, kettlebell or two-cable weight may be both hands' "
                                   "total: ask the person once, as it says. The weight was logged as sent.")
    assert "hand_check" not in tool["outputSchema"].get("required", [])
    clean(spec["description"])


def test_hand_check_reaches_the_assistant_through_mcp(http):
    token = sign()
    ok(http, token, "log_lifts", {"sets": [one("Barbell Curl", 30)], "date": day(1)})
    out = ok(http, token, "log_lifts", {"sets": [{"exercise": "hammer curls", "weight_kg": 20, "reps": 10}] * 3})
    assert out["hand_check"] == line("Dumbbell Hammer Curl", "20 kg", "40 kg", "Barbell Curl", "30 kg", "20", "10")
    assert [x["weight_kg"] for x in out["session"]["sets"]] == [20, 20, 20]
    assert one_user().id


# ---------------------------------------------------------------- a side (the owner's pending pick A)

def records(uid: int, search: str) -> dict:
    return {e["name"]: e["records"] for e in services.list_exercises(uid, search=search)["exercises"]}


def test_list_exercises_says_a_side_for_a_lift_done_one_side_at_a_time(uid):
    assert records(uid, "Bulgarian Split Squat")["Bulgarian Split Squat"] == "reps a side"
    row = services.list_exercises(uid, search="Single-Arm Dumbbell Row")["exercises"][0]  # an alias of Dumbbell Row
    assert (row["name"], row["records"]) == ("Dumbbell Row", "reps a side")
    assert records(uid, "Back Squat")["Back Squat"] == "reps"
    assert records(uid, "Side Plank")["Side Plank"] == "time a side"
    assert records(uid, "Suitcase Carry")["Suitcase Carry"] == "distance and time a side"
    assert records(uid, "Plank")["Plank"] == "time"
    every = {e["name"]: e["records"] for e in services.all_exercises()["exercises"]}
    sides = [m.name for m in exercises.MOVEMENTS if m.sides == "each"]
    assert len(sides) == 42 and all(every[n].endswith(" a side") for n in sides)
    assert not any(v and v.endswith(" a side") for n, v in every.items() if n not in sides)


def test_the_refusals_keep_their_words_without_a_side(uid):
    assert rules.records("Side Plank") == "time" and rules.records("Bulgarian Split Squat") == "reps"
    out = services.log_lifts(uid, {"sets": [{"exercise": "Side Plank", "exact": True, "weight_kg": 0, "reps": 5}]},
                             "claude")
    assert out["errors"][0]["message"] == "Set 1: Side Plank records time, not reps."


def test_the_set_fields_say_one_side_s_reps_and_time(http):
    for model in (SetIn, LogSetIn):
        assert model.model_fields["reps"].description == (
            "Reps in this set, a whole number from 1 to 100. Leave out for a hold or a carry. On a lift done one "
            "side at a time, one side's reps.")
        assert model.model_fields["time_s"].description == (
            "A hold's or a carry's time in seconds, 1 to 3,600. On a lift done one side at a time, one side's time.")
        assert model.model_fields["distance_m"].description == (
            "A carry's or a sled's distance in metres, 1 to 5,000. On a lift done one side at a time, one side's "
            "distance.")
        for field in ("reps", "time_s", "distance_m"):
            clean(model.model_fields[field].description)
    tools = {t["name"]: t for t in tool_list(http, sign())}
    for name, model in (("log_lifts", "LogSetIn"), ("edit_entry", "SetIn")):
        props = tools[name]["inputSchema"]["$defs"][model]["properties"]
        assert "one side's reps" in props["reps"]["description"] and "one side's time" in props["time_s"]["description"]
        assert "one side's distance" in props["distance_m"]["description"]
