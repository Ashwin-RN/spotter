"""Volume, bests and muscles, package C4e (contract exercise-list.md): each row of the bests table, the first session
setting none, through metrics, log_lifts, get_progress, get_log and the pages; volume by every load type and both
sides (the 800 kg example), none for a hold or a carry, 0 at help; get_data's family, time_s and distance_m
(R18); Suitcase Carry's muscles in a muscle group and every carry's region full body; what_is_logged and get_data
over a hold, a carry, a sled set and a help set (note (h)); a retired lift's page (note (s)). Every new text keeps
the text rules.

Every date is a fixed today through db.utcnow, never the real date.
"""

import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update

from app import db, exercises, formatting, metrics, services
from app.formatting import Units
from app.mcp_schemas import Best, NewBest
from app.models import Exercise
from app.services import fields, lifts

from . import text_rules as tr
from .test_data import area_of, field_row, table
from .test_idempotency import writer  # noqa: F401 - a fixture
from .test_mcp import ok
from .test_web import client, seam, sign_in, web_client  # noqa: F401 - fixtures

NOW = datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)
TODAY = NOW.date()
NB = " "


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


@pytest.fixture
def uid(make_user) -> int:
    return make_user(time_zone="UTC").id


def day(n: int) -> str:
    """The nth day of November 2026, before the fixed today (10 Nov)."""
    return f"2026-11-{n:02d}"


def one(exercise: str, weight=None, reps=None, time_s=None, distance_m=None) -> dict:
    """A set sent by its exact name, with only the fields given."""
    sent = {"exercise": exercise, "exact": True, "weight_kg": weight, "reps": reps, "time_s": time_s,
            "distance_m": distance_m}
    return {k: v for k, v in sent.items() if v is not None}


def log(uid: int, on: str, *sets: dict) -> dict:
    out = services.log_lifts(uid, {"date": on, "sets": list(sets), "new_session": True}, "claude")
    assert "error" not in out, out
    return out


def data(uid: int, **query) -> dict:
    return services.get_data(uid, query)


def clean(*texts: str, surface: str) -> None:
    for text in texts:
        assert tr.check(text, surface) == [], text


# ---------------------------------------------------------------- the bests table, in metrics

def full(*sets) -> list[tuple]:
    return [tuple(x) for x in sets]


def hit(kind, value, previous, **where) -> dict:
    """One of metrics.prs' bests, with where it stands (a hold's weight, a carry's distance, help) when it has one."""
    return {"kind": kind, "value": value, "previous": previous, **where}


def held(value, previous, at, was=None) -> dict:
    return hit("time", value, previous, weight_kg=at, previous_weight_kg=at if was is None else was)


def carried(value, previous, over, was=None) -> dict:
    return hit("carry", value, previous, distance_m=over, previous_distance_m=over if was is None else was)


def test_help_takes_the_least_help_then_the_most_reps_at_the_best_weight():
    prior = full((-30, 8, None, None))
    assert metrics.prs(prior, full((-20, 6, None, None)), "help") == [hit("least_help", -20, -30)]  # -20 beats -30
    prior = full((-30, 8, None, None), (-20, 6, None, None))
    assert metrics.prs(prior, full((-20, 8, None, None)), "help") == [hit("reps", 8, 6, weight_kg=-20)]
    assert metrics.prs(prior, full((-20, 6, None, None)), "help") == []  # no more reps at -20
    assert metrics.prs(prior, full((-30, 12, None, None)), "help") == []  # more reps, at more help
    assert metrics.prs(prior, full((0, 3, None, None), (-20, 9, None, None)), "help") == [
        hit("least_help", 0, -20)]  # one best a session: least help first
    # The reps counted are the best weight's alone: 12 reps at more help does not count.
    assert metrics.prs(full((-20, 5, None, None)), full((-20, 6, None, None), (-30, 12, None, None)), "help") == [
        hit("reps", 6, 5, weight_kg=-20)]


def test_a_band_takes_the_most_reps():
    assert metrics.prs(full((0, 15, None, None)), full((0, 20, None, None)), "band") == [hit("reps", 20, 15)]
    assert metrics.prs(full((0, 15, None, None)), full((0, 15, None, None)), "band") == []


def test_a_hold_takes_a_longer_time_at_the_same_or_a_heavier_weight_else_above_every_earlier_time():
    prior = full((0, None, 60, None))
    assert metrics.prs(prior, full((0, None, 90, None)), "hold") == [held(90, 60, 0)]
    assert metrics.prs(prior, full((0, None, 60, None)), "hold") == []  # as long is no best
    # No earlier set at 10 kg or more: every earlier time is the mark (C4e fix, item 3).
    assert metrics.prs(prior, full((10, None, 90, None)), "hold") == [held(90, 60, 10, was=0)]
    assert metrics.prs(prior, full((10, None, 30, None)), "hold") == []
    prior = full((0, None, 60, None), (10, None, 30, None))
    assert metrics.prs(prior, full((10, None, 45, None)), "hold") == [held(45, 30, 10)]
    assert metrics.prs(prior, full((0, None, 50, None)), "hold") == []  # 60 s was held at 0 kg
    assert metrics.prs(prior, full((5, None, 40, None)), "hold") == [held(40, 30, 5, was=10)]
    # On a tie the earlier set at the same weight is the one named, so the words name no other weight.
    assert metrics.prs(full((0, None, 60, None), (10, None, 60, None)), full((0, None, 70, None)), "hold") == [
        held(70, 60, 0)]
    # One best for each weight held, in order of weight (C4e fix, item 2).
    assert metrics.prs(prior, full((10, None, 40, None), (0, None, 70, None), (10, None, 35, None)), "hold") == [
        held(70, 60, 0), held(40, 30, 10)]


def test_a_carry_or_a_sled_takes_a_heavier_weight_at_the_same_or_a_longer_distance_else_above_every_weight():
    prior = full((32, None, 45, 40))
    assert metrics.prs(prior, full((36, None, 50, 40)), "carry") == [carried(36, 32, 40)]
    assert metrics.prs(prior, full((40, None, 20, 20)), "carry") == [carried(40, 32, 20, was=40)]
    assert metrics.prs(prior, full((34, None, 90, 80)), "carry") == [carried(34, 32, 80, was=40)]  # item 3
    assert metrics.prs(prior, full((30, None, 70, 80)), "carry") == []
    assert metrics.prs(prior, full((30, None, 30, 20)), "carry") == []
    assert metrics.prs(prior, full((36, None, 30, 20), (34, None, 60, 40)), "carry") == [
        carried(36, 32, 20, was=40), carried(34, 32, 40)]  # one best for each distance


def test_the_first_session_sets_no_best_and_the_standard_kinds_stay_as_they_were():
    for kind in metrics.BEST_KINDS:
        assert metrics.prs([], full((10, 5, 60, 40)), kind) == []
    two, four = [(100, 5), (90, 8)], [(100, 5, None, None), (90, 8, None, None)]
    assert metrics.prs([(80, 5)], two) == metrics.prs([(80, 5, None, None)], four) == [
        {"kind": "estimated_max", "value": 116.67, "previous": 93.33}, {"kind": "weight", "value": 100,
                                                                        "previous": 80}]


def test_each_lift_s_kind_comes_from_the_list():
    kinds = {name: lifts.best_kind(name) for name in ("Assisted Pull-Up", "Assisted Dip", "Band Pull-Apart",
                                                      "Plank", "Dead Hang", "Farmer's Carry", "Sled Push",
                                                      "Sandbag Lunge", "Back Squat", "Pull-Up", "Not a lift")}
    assert kinds == {"Assisted Pull-Up": "help", "Assisted Dip": "help", "Band Pull-Apart": "band", "Plank": "hold",
                     "Dead Hang": "hold", "Farmer's Carry": "carry", "Sled Push": "carry", "Sandbag Lunge": "carry",
                     "Back Squat": "standard", "Pull-Up": "standard", "Not a lift": "standard"}


def test_each_best_names_where_it_stands_in_the_person_s_units():
    def text(**p):
        return lifts.best_text({"exercise": "X", **p})
    assert text(**held(45, 30, 10)) == "X longest hold 45 s at 10 kg, up from 30 s"
    assert text(**held(90, 60, 10, was=0)) == "X longest hold 90 s at 10 kg, up from 60 s at 0 kg"
    assert text(**held(90, 60, 0)) == "X longest hold 90 s, up from 60 s"
    assert text(**held(70, 60, 0, was=10)) == "X longest hold 70 s, up from 60 s at 10 kg"
    assert text(**carried(36, 32, 40)) == "X heaviest load 36 kg over 40 m, up from 32 kg"
    assert text(**carried(34, 32, 80, was=40)) == "X heaviest load 34 kg over 80 m, up from 32 kg over 40 m"
    assert text(**hit("reps", 8, 6, weight_kg=-20)) == "X most reps 8 with 20 kg help, up from 6"
    assert text(**hit("reps", 8, 6, weight_kg=0)) == "X most reps 8 with no help, up from 6"
    assert text(**hit("reps", 20, 15)) == "X most reps 20, up from 15"
    assert text(**hit("least_help", -20, -30)) == "X least help 20 kg, down from 30 kg"
    assert text(**hit("least_help", 0, -10)) == "X no help, down from 10 kg"
    lb = Units(weight="lb", distance="mi")
    assert lifts.best_text({"exercise": "X", **held(45, 30, 10)}, lb) == "X longest hold 45 s at 22.05 lb, up from 30 s"
    assert lifts.best_text({"exercise": "X", **carried(36, 32, 40)}, lb) == (
        "X heaviest load 79.35 lb over 40 m, up from 70.55 lb")  # a distance stays in metres on miles
    assert lifts.record_figure("help", (0, 8, None, None)) == "no help"
    assert lifts.record_figure("hold", (10, None, 60, None)) == "longest hold 60 s at 10 kg"
    assert lifts.record_figure("carry", (32, None, 45, 40)) == "heaviest load 32 kg over 40 m"


# ---------------------------------------------------------------- the bests table, logged and read back

# (lift, the sessions in order, each one's new bests as (kind, value, previous), the first session's mark, the texts)
SEQUENCES = {
    "help": ("Assisted Pull-Up", [[(-30, 8)], [(-20, 6)], [(-20, 8)], [(-30, 12)]],
             [[], [("least_help", -20, -30)], [("reps", 8, 6)], []],
             "First Assisted Pull-Up session: least help 30 kg is the mark to beat.",
             ["Assisted Pull-Up least help 20 kg, down from 30 kg",
              "Assisted Pull-Up most reps 8 with 20 kg help, up from 6"]),
    "band": ("Band Pull-Apart", [[(None, 15)], [(None, 20)], [(None, 18)]],
             [[], [("reps", 20, 15)], []],
             "First Band Pull-Apart session: most reps 15 is the mark to beat.",
             ["Band Pull-Apart most reps 20, up from 15"]),
    "hold": ("Plank", [[(0, None, 60)], [(0, None, 90)], [(10, None, 30)], [(10, None, 45)]],
             [[], [("time", 90, 60)], [], [("time", 45, 30)]],
             "First Plank session: longest hold 60 s is the mark to beat.",
             ["Plank longest hold 90 s, up from 60 s", "Plank longest hold 45 s at 10 kg, up from 30 s"]),
    "carry": ("Farmer's Carry", [[(32, None, 45, 40)], [(36, None, 50, 40)], [(30, None, 70, 60)],
                                 [(40, None, 20, 20)]],
              [[], [("carry", 36, 32)], [], [("carry", 40, 36)]],
              "First Farmer's Carry session: heaviest load 32 kg over 40 m is the mark to beat.",
              ["Farmer's Carry heaviest load 36 kg over 40 m, up from 32 kg",
               "Farmer's Carry heaviest load 40 kg over 20 m, up from 36 kg over 40 m"]),
    "sled": ("Sled Push", [[(100, None, 30, 20)], [(120, None, 35, 20)]],
             [[], [("carry", 120, 100)]],
             "First Sled Push session: heaviest load 100 kg over 20 m is the mark to beat.",
             ["Sled Push heaviest load 120 kg over 20 m, up from 100 kg"]),
}


@pytest.mark.parametrize("case", list(SEQUENCES))
def test_each_row_of_the_bests_table_through_log_lifts_get_progress_and_get_log(uid, case):
    lift, sessions, expected, first, texts = SEQUENCES[case]
    seen, said = [], []
    for n, sets in enumerate(sessions):
        logged = log(uid, day(n + 1), *(one(lift, *x) for x in sets))
        got = [(b["kind"], b["value"], b["previous"]) for b in logged["new_bests"]]
        assert got == expected[n], (n, logged["summary"])
        seen += [(day(n + 1), *hit) for hit in got]
        said += [b["text"] for b in logged["new_bests"]]
        if n == 0:
            assert logged["summary"].endswith(first), logged["summary"]
        for b in logged["new_bests"]:
            assert f" New best: {b['text']}." in logged["summary"]
            NewBest.model_validate(b)
            assert set(b) == set(NewBest.model_fields)  # where it stands is in the text alone
    assert said == texts
    read = services.get_progress(uid, lift)
    bests = read["lift"]["bests"]
    assert [(b["date"], b["kind"], b["value"], b["previous"]) for b in bests] == seen
    assert [b["text"] for b in bests] == texts  # get_progress says each best as log_lifts did (note of 2026-11-20)
    for b in bests:
        Best.model_validate(b)
    in_log = [(day(n + 1), b["kind"], b["value"], b["previous"]) for n, _ in enumerate(sessions)
              for s in services.get_log(uid, day(n + 1))["days"][0]["sessions"] for b in s["new_bests"]]
    assert in_log == seen
    in_words = [b["text"] for n, _ in enumerate(sessions)
                for s in services.get_log(uid, day(n + 1))["days"][0]["sessions"] for b in s["new_bests"]]
    assert in_words == texts  # get_log, and so the day page, says each best as log_lifts did
    clean(first, *texts, read["summary"], surface=tr.SUMMARY)


def test_get_progress_s_best_text_names_a_hold_s_weight_and_a_carry_s_distance_and_a_weight_best_s_is_as_before(
        uid, make_user):
    # An outside review (exercise-list.md, note of 2026-11-20): Best kept date, kind, value and previous, so a hold's
    # weight and a carry's distance, which make the best true, were in log_lifts' words alone.
    def words(person, lift, on):
        """Each best's text as log_lifts, get_log and get_progress give it, for the session on that day."""
        progress = [b["text"] for b in services.get_progress(person, lift)["lift"]["bests"] if b["date"] == on]
        in_log = [b["text"] for s in services.get_log(person, on)["days"][0]["sessions"] for b in s["new_bests"]]
        return progress, in_log

    log(uid, day(1), one("Plank", 0, time_s=90))
    assert log(uid, day(2), one("Plank", 10, time_s=30))["new_bests"] == []  # shorter than every earlier hold
    logged = log(uid, day(3), one("Plank", 10, time_s=45))
    said = [b["text"] for b in logged["new_bests"]]
    assert said == ["Plank longest hold 45 s at 10 kg, up from 30 s"]
    assert words(uid, "Plank", day(3)) == (said, said)
    [best] = services.get_progress(uid, "Plank")["lift"]["bests"]
    assert best == {"date": day(3), "kind": "time", "value": 45, "previous": 30, "text": said[0]}
    Best.model_validate(best)

    log(uid, day(4), one("Farmer's Carry", 32, time_s=45, distance_m=40))
    carried = [b["text"] for b in log(uid, day(5), one("Farmer's Carry", 36, time_s=50, distance_m=40))["new_bests"]]
    assert carried == ["Farmer's Carry heaviest load 36 kg over 40 m, up from 32 kg"]
    assert words(uid, "Farmer's Carry", day(5)) == (carried, carried)

    # A weight best: its numbers as before, and its text the words log_lifts says, in the person's units.
    for person, expected in ((uid, ["Bench Press estimated max 116.7 kg, up from 93.3 kg",
                                    "Bench Press heaviest weight 100 kg, up from 80 kg"]),
                             (make_user(time_zone="UTC", weight_unit="lb").id,
                              ["Bench Press estimated max 257.2 lb, up from 205.8 lb",
                               "Bench Press heaviest weight 220.45 lb, up from 176.35 lb"])):
        log(person, day(6), one("Bench Press", 80, 5))
        heavier = log(person, day(7), one("Bench Press", 100, 5), one("Bench Press", 90, 8))
        assert [b["text"] for b in heavier["new_bests"]] == expected
        assert words(person, "Bench Press", day(7)) == (expected, expected)
        read = services.get_progress(person, "Bench Press")["lift"]["bests"]
        assert [(b["kind"], b["value"], b["previous"]) for b in read] == [("estimated_max", 116.7, 93.3),
                                                                         ("weight", 100, 80)]
        unrounded = services.get_progress(person, "Bench Press", exact=True)["lift"]["bests"]  # the pages' read
        assert [b["text"] for b in unrounded] == expected
    clean(said[0], *carried, *expected, surface=tr.SUMMARY)


def test_an_add_to_the_session_reports_only_a_best_it_did_not_hold_before(uid):
    log(uid, day(1), one("Plank", 0, time_s=60))
    first = services.log_lifts(uid, {"date": day(2), "sets": [one("Plank", 0, time_s=90)]}, "claude")
    assert [b["kind"] for b in first["new_bests"]] == ["time"]
    added = services.log_lifts(uid, {"date": day(2), "sets": [one("Plank", 0, time_s=80)]}, "claude")
    assert added["summary"].startswith("Added") and added["new_bests"] == []  # 90 s was already the best
    longer = services.log_lifts(uid, {"date": day(2), "sets": [one("Plank", 0, time_s=100)]}, "claude")
    assert [(b["kind"], b["value"], b["previous"]) for b in longer["new_bests"]] == [("time", 100, 60)]


@pytest.mark.parametrize("order", [(10, 0), (0, 10)])
def test_a_best_log_lifts_announces_never_disappears_from_get_progress_or_get_log(uid, order):
    log(uid, day(1), one("Plank", 0, time_s=60))
    log(uid, day(2), one("Plank", 10, time_s=30))
    sets = {10: one("Plank", 10, time_s=45), 0: one("Plank", 0, time_s=70)}
    words = {10: "Plank longest hold 45 s at 10 kg, up from 30 s", 0: "Plank longest hold 70 s, up from 60 s"}
    said = []
    for weight in order:  # the first starts the day's session, the second is added to it
        out = services.log_lifts(uid, {"date": day(3), "sets": [sets[weight]]}, "claude")
        assert [b["text"] for b in out["new_bests"]] == [words[weight]], out["summary"]
        said.append((out["new_bests"][0]["value"], out["new_bests"][0]["previous"]))
    kept = [(b["value"], b["previous"]) for b in services.get_progress(uid, "Plank")["lift"]["bests"]
            if b["date"] == day(3)]
    assert sorted(kept) == sorted(said) == [(45, 30), (70, 60)]
    in_log = services.get_log(uid, day(3))["days"][0]["sessions"][0]["new_bests"]
    assert sorted((b["value"], b["previous"]) for b in in_log) == sorted(said)


def test_a_bodyweight_lift_at_0_kg_keeps_today_s_rule(uid):
    log(uid, day(1), one("Pull-Up", 0, 8))
    again = log(uid, day(2), one("Pull-Up", 0, 12))
    assert again["new_bests"] == []
    page = services.get_progress(uid, for_page=True)
    assert page["reps_only"] == {"Pull-Up": {"reps": 12, "date": day(2)}} and "records" not in page


# ---------------------------------------------------------------- the records: the summary and the pages

def _record_lifts(uid: int) -> None:
    for case in ("help", "band", "hold", "carry"):
        lift, sessions, *_ = SEQUENCES[case]
        for n, sets in enumerate(sessions):
            log(uid, day(n + 1), *(one(lift, *x) for x in sets))


def test_get_progress_says_each_record_in_its_summary_and_the_page_read_carries_the_records(uid):
    _record_lifts(uid)
    every = services.get_progress(uid)
    assert every["summary"].splitlines() == [
        "Assisted Pull-Up: least help 20 kg, 4 sessions.", "Band Pull-Apart: most reps 20, 3 sessions.",
        "Farmer's Carry: heaviest load 40 kg over 20 m, 4 sessions.", "Plank: longest hold 90 s, 4 sessions."]
    assert "records" not in every and "records" not in services.get_progress(uid, "Plank")  # no MCP result changes
    plank = services.get_progress(uid, "Plank")
    assert plank["summary"] == f"Plank: longest hold 90 s. Best set 90 s on {formatting.day(day(2), TODAY)}. " \
                               "4 sessions."
    carry = services.get_progress(uid, "Farmer's Carry")["summary"]
    assert carry == f"Farmer's Carry: heaviest load 40 kg over 20 m. Best set 20 m at 40 kg each in 20 s on " \
                    f"{formatting.day(day(4), TODAY)}. 4 sessions."
    helped = services.get_progress(uid, "Assisted Pull-Up")["summary"]
    assert helped.startswith("Assisted Pull-Up: least help 20 kg. Best set 8 reps with 20 kg help on ")
    page = services.get_progress(uid, for_page=True)
    assert page["records"] == {
        "Assisted Pull-Up": {"kind": "help", "weight_kg": -20, "reps": 8, "time_s": None, "distance_m": None,
                             "date": day(3)},
        "Band Pull-Apart": {"kind": "band", "weight_kg": 0, "reps": 20, "time_s": None, "distance_m": None,
                            "date": day(2)},
        "Plank": {"kind": "hold", "weight_kg": 0, "reps": None, "time_s": 90, "distance_m": None, "date": day(2)},
        "Farmer's Carry": {"kind": "carry", "weight_kg": 40, "reps": None, "time_s": 20, "distance_m": 20,
                           "date": day(4)}}
    assert page["reps_only"] == {}  # a band and help have their own record, never "needs added weight"
    clean(*every["summary"].splitlines(), plank["summary"], carry, helped, surface=tr.SUMMARY)


def test_the_record_words_follow_the_person_s_units_and_say_a_side():
    rec = {"weight_kg": 20, "reps": None, "time_s": 45, "distance_m": 30}
    assert lifts.record_words("Suitcase Carry", rec) == "30 m a side at 20 kg in 45 s"
    assert lifts.record_words("Side Plank", {**rec, "distance_m": None, "weight_kg": 0}) == "45 s a side"
    lb = Units(weight="lb", distance="km")
    assert lifts.record_words("Farmer's Carry", rec, lb) == "30 m at 44.1 lb each in 45 s"
    assert lifts.record_words("Farmer's Carry", rec, page=True) == f"30{NB}m at 20{NB}kg each in 45{NB}s"
    assert lifts.record_figure("help", (-20, 8, None, None), lb) == "least help 44.1 lb"


def _pages(client) -> int:
    sign_in(client)
    with db.new_session() as s:
        from app.models import User
        return s.scalars(select(User.id).order_by(User.id.desc())).first()


def test_the_progress_page_and_each_lift_page_say_the_record(client):
    person = _pages(client)
    assert "error" not in services.update_settings(person, {"time_zone": "UTC"}, "web")
    _record_lifts(person)
    log(person, day(5), one("Back Squat", 100, 5))
    progress = client.get("/progress")
    assert progress.status_code == 200
    rows = {re.sub(r"<[^>]+>", "", m).strip() for m in re.findall(r"<tr><td class=\"lead\">(.*?)</tr>", progress.text,
                                                                  re.S)}
    plank = next(r for r in rows if r.startswith("Plank"))
    assert "Not worked out for a hold" in plank and f"90{NB}s" in plank
    helped = next(r for r in rows if r.startswith("Assisted Pull-Up"))
    assert "Not worked out with help" in helped and f"8{NB}reps with 20{NB}kg help" in helped
    carry = next(r for r in rows if r.startswith("Farmer"))
    assert "Not worked out for distance" in carry and f"20{NB}m at 40{NB}kg each in 20{NB}s" in carry
    band = next(r for r in rows if r.startswith("Band"))
    assert "Not worked out for a band" in band and f"20{NB}reps" in band
    squat = next(r for r in rows if r.startswith("Back Squat"))
    assert f"100{NB}kg × 5" in squat  # a lift of reps reads as before
    page = client.get("/progress/plank")
    assert page.status_code == 200
    assert "Longest hold" in page.text and "s, longest hold" in page.text and "Estimated max" not in page.text
    assert f"longest hold 90{NB}s, up from 60{NB}s" in page.text
    assert f"longest hold 45{NB}s at 10{NB}kg, up from 30{NB}s" in page.text
    helped = client.get("/progress/assisted-pull-up")
    assert f"least help 20{NB}kg, down from 30{NB}kg" in helped.text and "Least help" in helped.text
    assert f"most reps 8 with 20{NB}kg help, up from 6" in helped.text
    squat = client.get("/progress/back-squat")
    assert "Estimated max" in squat.text and "Longest hold" not in squat.text
    lifts_card = re.search(r'<section class="card lifts-card".*?</section>', progress.text, re.S).group(0)
    for markup in (lifts_card, page.text, helped.text):  # the Lifts card: the Trends card is test_pages_text's
        for piece in tr.html_text(markup):
            assert tr.check(piece.text, tr.TITLE if piece.kind == "title" else tr.PAGE) == [], piece


def test_the_record_tiles_say_reps_each_and_no_help(client):
    person = _pages(client)
    assert "error" not in services.update_settings(person, {"time_zone": "UTC"}, "web")
    log(person, day(1), one("Band Pull-Apart", reps=20), one("Farmer's Carry", 32, time_s=45, distance_m=40),
        one("Assisted Dip", 0, 10))
    tile = lambda path: re.sub(r"<[^>]+>", " ", re.search(  # noqa: E731
        r'<div class="tile tile-max">(.*?)</div>', client.get(path).text, re.S).group(1)).split()
    assert tile("/progress/band-pull-apart") == ["Most", "reps:", "20", "reps,", "most", "reps"]
    assert tile("/progress/farmers-carry") == ["Heaviest", "load:", "32", "kg", "each,", "heaviest", "load"]
    assert tile("/progress/assisted-dip") == ["Least", "help:", "No", "help"]
    assert services.get_progress(person, "Assisted Dip")["summary"].startswith("Assisted Dip: no help. Best set 10 reps")


# ---------------------------------------------------------------- volume by load type and sides

VOLUMES = [  # (the set, its load type and sides, its volume)
    (one("Back Squat", 100, 5), ("bar_total", "both"), 500),
    (one("Dumbbell Bench Press", 25, 10), ("per_hand", "both"), 500),  # one hand's 25 kg, both hands counted
    (one("Smith Squat", 60, 5), ("single", "both"), 300),
    (one("Pull-Up", 0, 10), ("bodyweight", "both"), 0),
    (one("Pull-Up", 10, 5), ("bodyweight", "both"), 50),
    (one("Band Pull-Apart", reps=15), ("band", "both"), 0),
    (one("Bulgarian Split Squat", 20, 10), ("per_hand", "each"), 800),  # 20 x 2 x 10 x 2
    (one("Barbell Bulgarian Split Squat", 60, 8), ("bar_total", "each"), 960),
    (one("Assisted Pull-Up", -20, 8), ("bodyweight", "both"), 0),  # help counts 0
    (one("Plank", 0, time_s=60), ("bodyweight", "both"), None),  # a hold has none
    (one("Farmer's Carry", 32, time_s=45, distance_m=40), ("per_hand", "both"), None),
    (one("Sled Push", 100, time_s=30, distance_m=20), ("single", "both"), None),
    (one("Suitcase Carry", 24, time_s=40, distance_m=30), ("single", "each"), None),
]


def test_set_volume_stacks_a_pair_and_one_side_at_a_time():
    assert metrics.set_volume(20, 10, pair=True, one_side=True) == 800
    assert metrics.set_volume(20, 10, pair=True) == metrics.set_volume(20, 10, one_side=True) == 400
    assert metrics.set_volume(-20, 8) == 0 and metrics.set_volume(0, 8, pair=True) == 0
    assert metrics.set_volume(10, None) is None and metrics.set_volume(None, 5) is None
    assert fields.volume("Not a lift", 50, 5) == 250  # a name not on the list, a retired one: weight x reps


def test_volume_by_every_load_type_and_both_sides_in_sets_sessions_and_days(uid):
    for sent, (load, sides), _ in VOLUMES:
        row = exercises.BY_NAME[sent["exercise"]]
        assert (row.load_type, row.sides) == (load, sides), sent
    session = log(uid, day(3), *(x for x, _, _ in VOLUMES))
    rows = table(data(uid, area="lift_sets", fields=["exercise", "weight_kg", "reps", "volume_kg"]))
    assert [r["volume_kg"] for r in reversed(rows)] == [v for _, _, v in VOLUMES]
    whole = sum(v for _, _, v in VOLUMES if v)
    assert whole == 3110
    assert table(data(uid, area="lift_sessions", fields=["id", "volume_kg"])) == [
        {"id": session["id"], "volume_kg": whole}]
    assert table(data(uid, area="days", fields=["date", "volume_kg"], from_date=day(3), to_date=day(3))) == [
        {"date": day(3), "volume_kg": whole}]
    totals = data(uid, area="lift_sets", totals={"volume_kg": ["sum", "count"]})["totals"]
    assert totals["values"] == [whole, 9]  # the four sets of time or distance hold no volume


def test_what_is_logged_and_get_data_read_a_hold_a_carry_a_sled_and_help(uid):
    log(uid, day(1), one("Plank", 0, time_s=60), one("Farmer's Carry", 32, time_s=45, distance_m=40),
        one("Sled Pull", 80, time_s=30, distance_m=25), one("Assisted Dip", -15, 10))
    logged = services.what_is_logged(uid)
    assert "error" not in logged, logged
    sets = area_of(logged, "lift_sets")
    assert field_row(sets, "time_s")[-1] == 3 and field_row(sets, "distance_m")[-1] == 2
    assert field_row(sets, "volume_kg")[-1] == 1 and field_row(sets, "reps")[-1] == 1
    for area in ("lift_sets", "lift_sessions", "days"):
        assert "error" not in data(uid, area=area), area
    assert table(data(uid, area="lift_sessions", fields=["sets", "volume_kg"])) == [{"sets": 4, "volume_kg": 0}]
    held = table(data(uid, area="lift_sets", fields=["exercise", "weight_kg", "reps", "time_s", "distance_m"]))
    assert held == [
        {"exercise": "Assisted Dip", "weight_kg": -15, "reps": 10, "time_s": None, "distance_m": None},
        {"exercise": "Sled Pull", "weight_kg": 80, "reps": None, "time_s": 30, "distance_m": 25},
        {"exercise": "Farmer's Carry", "weight_kg": 32, "reps": None, "time_s": 45, "distance_m": 40},
        {"exercise": "Plank", "weight_kg": 0, "reps": None, "time_s": 60, "distance_m": None}]
    totals = data(uid, area="lift_sets", totals={"time_s": ["sum", "max"], "distance_m": ["sum", "avg"]})["totals"]
    assert totals == {"columns": ["time_s_sum", "time_s_max", "distance_m_sum", "distance_m_avg"],
                      "values": [135, 60, 65, 32.5]}


# ---------------------------------------------------------------- get_data's family (R18)

def test_family_is_a_cell_a_filter_by_any_lift_or_a_word_and_a_group(uid):
    log(uid, day(1), one("Back Squat", 100, 5), one("Smith Squat", 80, 5), one("Bench Press", 60, 5))
    log(uid, day(2), one("Dumbbell Squat", 30, 10), one("Dumbbell Bench Press", 25, 8))
    cells = table(data(uid, area="lift_sets", fields=["exercise", "family"]))
    assert {r["exercise"]: r["family"] for r in cells} == {
        "Back Squat": "Back Squat", "Smith Squat": "Back Squat", "Dumbbell Squat": "Back Squat",
        "Bench Press": "Bench Press", "Dumbbell Bench Press": "Bench Press"}
    names = lambda answer: sorted(r["exercise"] for r in table(answer))  # noqa: E731
    squats = ["Back Squat", "Dumbbell Squat", "Smith Squat"]
    assert names(data(uid, area="lift_sets", filters={"family": "Dumbbell Squat"})) == squats  # any lift of it
    assert names(data(uid, area="lift_sets", filters={"family": "squat"})) == squats  # a plain word
    assert names(data(uid, area="lift_sets", filters={"family": ["bench", "Smith Squat"]})) == sorted(
        squats + ["Bench Press", "Dumbbell Bench Press"])
    log(uid, day(3), one("Sled Push", 100, time_s=30, distance_m=20), one("Sled Drag", 80, time_s=40, distance_m=25))
    sleds = data(uid, area="lift_sets", filters={"family": "sled"}, fields=["exercise", "family"])
    assert sorted((r["exercise"], r["family"]) for r in table(sleds)) == [
        ("Sled Drag", "Sled Drag"), ("Sled Push", "Sled Push")]  # a word whose lifts are of several families
    grouped = table(data(uid, area="lift_sets", filters={"family": ["squat", "bench"]}, group_by=["family"],
                         totals={"sets": ["count"]}))
    assert grouped == [{"family": "Back Squat", "sets_count": 3}, {"family": "Bench Press", "sets_count": 2}]
    refused = data(uid, area="lift_sets", filters={"family": "Zercher Lunge Thing"})
    assert refused["error"] == "unknown_exercise" and refused["message"].startswith("filters.family: ")
    assert "family" not in data(uid, area="lift_sets")["columns"]  # not a default column, as muscle
    names_logged = area_of(services.what_is_logged(uid), "lift_sets")["names"]["family"]
    assert names_logged == ["Back Squat", "Bench Press", "Sled Drag", "Sled Push"]  # most sets first, then by name


def test_a_plain_word_on_family_takes_the_families_of_the_candidates_the_person_logged(uid, make_user):
    other = make_user(time_zone="UTC").id
    log(other, day(1), one("Dumbbell Shoulder Press", 20, 8))  # another account's lift never widens it
    log(uid, day(1), one("Dumbbell Bench Press", 30, 8), one("Overhead Press", 50, 5), one("Bench Press", 80, 5))
    names = lambda word: sorted(r["exercise"] for r in table(data(uid, area="lift_sets", filters={"family": word})))  # noqa: E731
    # "dumbbell press" is Dumbbell Bench Press or Dumbbell Shoulder Press; this person logged only the first.
    assert names("dumbbell press") == ["Bench Press", "Dumbbell Bench Press"]
    assert names("Dumbbell Shoulder Press") == ["Overhead Press"]  # a lift named: its family, logged or not
    log(uid, day(2), one("Dumbbell Shoulder Press", 22, 8))
    assert names("dumbbell press") == ["Bench Press", "Dumbbell Bench Press", "Dumbbell Shoulder Press",
                                       "Overhead Press"]
    assert table(data(other, area="lift_sets", filters={"family": "bench"})) == []  # none of its candidates logged


def test_a_lift_goal_rides_with_a_family_filter_only_when_its_lift_is_of_it(uid):
    log(uid, day(1), one("Back Squat", 100, 5), one("Bench Press", 60, 5))
    assert "error" not in services.set_goal(uid, {"kind": "lift", "exercise": "Back Squat", "target_value": 140,
                                                  "target_date": "2027-03-31"}, "claude")
    riding = data(uid, area="lift_sets", filters={"family": "Smith Squat"})["goals"]
    assert len(riding) == 1 and "Back Squat" in riding[0]["label"]
    assert data(uid, area="lift_sets", filters={"family": "Bench Press"})["goals"] == []


# ---------------------------------------------------------------- muscles and region

def test_suitcase_carry_s_muscles_count_in_a_muscle_group_and_every_carry_is_full_body(uid):
    log(uid, day(1), one("Suitcase Carry", 24, time_s=40, distance_m=30))
    grouped = table(data(uid, area="lift_sets", group_by=["muscle"], totals={"sets": ["sum"]}))
    assert grouped[0] == {"muscle": "forearms", "sets_sum": 1}
    assert {r["muscle"]: r["sets_sum"] for r in grouped[1:]} == dict.fromkeys(
        ("abdominals", "glutes", "hamstrings", "lower back", "quadriceps", "traps"), 0.5)
    assert table(data(uid, area="lift_sets", fields=["region"])) == [{"region": "full_body"}]
    for name in ("Farmer's Carry", "Suitcase Carry", "Sled Push", "Sled Pull", "Sled Drag"):
        assert exercises.REGIONS[name] == "full_body", name


def test_the_meanings_say_the_volume_rule_and_no_lift_is_without_muscles():
    by_area = {area.name: {f.name: f.meaning for f in area.fields} for area in fields.AREAS.values()}
    sets = by_area["lift_sets"]
    assert sets["volume_kg"] == ("weight x reps, x 2 for a pair of weights, x 2 for one side at a time. None for a "
                                 "set of time or distance. 0 at 0 kg or below.")
    assert "Suitcase" not in sets["muscle"]
    assert sets["time_s"] == "A hold's or a carry's time in seconds, or null."
    assert sets["distance_m"] == "A carry's or a sled's distance in metres, or null."
    added_up = ("weight x reps, x 2 for a pair of weights, x 2 for one side at a time, 0 at 0 kg or below. A set of "
                "time or distance adds nothing. A session of holds or carries alone counts 0.")
    assert by_area["lift_sessions"]["volume_kg"] == "Its sets' volume added up: " + added_up
    assert by_area["days"]["volume_kg"] == "That day's sets' volume added up: " + added_up
    assert sets["weight_kg"] == "The weight lifted: one hand's for a pair, a minus weight for help, 0 for bodyweight work."
    assert "time_s" not in fields.INTERNAL_COLUMNS["lift_set"]
    clean(*(m for area in by_area.values() for m in area.values()), surface=tr.CLAUDE)
    for model in (NewBest, Best):
        clean(*(f.description for f in model.model_fields.values()), surface=tr.CLAUDE)
    clean(*lifts.NO_ESTIMATE.values(), surface=tr.PAGE)


# ---------------------------------------------------------------- a retired lift's page (note (s))

def test_a_retired_lift_s_page_reads_its_history_and_get_progress_still_refuses_it(client):
    person = _pages(client)
    assert "error" not in services.update_settings(person, {"time_zone": "UTC"}, "web")
    log(person, day(1), one("Pendulum Squat", 80, 8))
    log(person, day(2), one("Pendulum Squat", 90, 8))
    with db.session_scope() as s:
        s.execute(update(Exercise).where(Exercise.name == "Pendulum Squat").values(retired=True))
    page = client.get("/progress/pendulum-squat")
    assert page.status_code == 200, page.text
    assert "Pendulum Squat" in page.text and f"heaviest weight 90{NB}kg, up from 80{NB}kg" in page.text
    plain = services.get_progress(person, "Pendulum Squat")
    assert plain["error"] == "unknown_exercise"  # every matcher leaves a retired row out (R2)
    read = services.get_progress(person, "Pendulum Squat", for_page=True)
    assert read["lift"]["sessions"] == 2
    assert "Pendulum Squat" in [r["exercise"] for r in services.get_progress(person)["lifts"]]


def test_a_lift_that_left_the_list_keeps_its_page_its_bests_and_its_data(client):
    """A row whose key the list no longer holds (R2): retired by sync, and in none of the code's views."""
    from app.models import LiftSession, LiftSet
    person = _pages(client)
    assert "error" not in services.update_settings(person, {"time_zone": "UTC"}, "web")
    with db.session_scope() as s:
        old = Exercise(key="old_press", name="Old Press", kind="lift", category="push", retired=True)
        s.add(old)
        s.flush()
        for n, kg in ((1, 40), (2, 45)):
            session = LiftSession(user_id=person, date=datetime(2026, 11, n).date(), notes="", dedupe_key=f"k{n}",
                                  logged_by="web")
            s.add(session)
            s.flush()
            s.add(LiftSet(session_id=session.id, exercise_id=old.id, set_number=1, weight_kg=kg, reps=5))
    assert "Old Press" not in exercises.BY_NAME
    page = client.get("/progress/old-press")
    assert page.status_code == 200, page.text
    assert f"heaviest weight 45{NB}kg, up from 40{NB}kg" in page.text
    assert "Old Press" in client.get("/progress").text
    assert services.get_progress(person, "Old Press")["error"] == "unknown_exercise"
    rows = table(data(person, area="lift_sets", fields=["exercise", "family", "region", "volume_kg"]))
    assert rows[0] == {"exercise": "Old Press", "family": None, "region": None, "volume_kg": 225}


# ---------------------------------------------------------------- through /mcp

def test_get_progress_and_get_log_over_every_kind_pass_their_published_models_through_mcp(writer):
    http, token, account = writer
    _record_lifts(account)
    for name in ("Assisted Pull-Up", "Band Pull-Apart", "Plank", "Farmer's Carry"):
        read = ok(http, token, "get_progress", {"exercise": name})
        assert read["lift"]["bests"], name
    assert ok(http, token, "get_log", {"date": day(2)})["days"][0]["sessions"][0]["new_bests"]
    logged = ok(http, token, "log", {"kind": "lifts", "date": day(9), "parts": [
        {"movement": "Plank", "exact": True, "weight_kg": 0, "time_s": 120}]})
    assert logged["new_bests"][0]["text"] == "Plank longest hold 120 s, up from 90 s"
