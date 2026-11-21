"""One matcher, package C4c (contract exercise-list.md, "Matching order", decisions 1 to 4, 28 and 31, R3 to R5, R19):
the key on every name, plural and "Name (Equipment)" form; a plain word in log_lifts picked from the person's history,
or asked about as pick_lift; exact true; every other function taking a name as written, a plain word refused with its
candidates or, in a read, every candidate the person logged; close names that show a lift's name, never an alias; and
a retired row matching nothing. Every new message and fix keeps the text rules.

Every date is a fixed today through db.utcnow, never the real date.
"""

import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import db, exercises, mcp_server, rules, services
from app.mcp_schemas import LogSetIn, SetIn
from app.models import Exercise, LiftSession

from . import text_rules as tr
from .test_exercise_list import _said_equipment, norm
from .test_idempotency import writer  # noqa: F401 - a fixture
from .test_mcp import failed, ok, registered_tools, tool_list

NOW = datetime(2026, 11, 20, 8, 30, tzinfo=timezone.utc)
TODAY = NOW.date()
NAMES = [name for name, _ in exercises.LIFTS]
CARDIO = [name for name, *_ in exercises.CARDIO]
LIFT_ROWS = [m for m in exercises.MOVEMENTS if m.kind == "lift"]


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


@pytest.fixture
def uid(make_user) -> int:
    return make_user(time_zone="UTC").id


def day(n: int) -> str:
    """n days before the fixed today."""
    return date_of(n).isoformat()


def date_of(n: int):
    from datetime import timedelta
    return TODAY - timedelta(days=n)


def seed(uid: int, name: str, ago: int, weight: float = 60, reps: int = 5) -> None:
    """A set of a lift by its exact name, n days ago: the person's history."""
    r = services.log_lifts(uid, {"date": day(ago), "new_session": True, "sets": [
        {"exercise": name, "weight_kg": weight, "reps": reps, "exact": True}]}, "claude")
    assert "error" not in r, r


def log(uid: int, *sets: dict) -> dict:
    """The sets as a new session today, so its answer holds only them."""
    return services.log_lifts(uid, {"sets": list(sets), "new_session": True}, "claude")


def logged_names(r: dict) -> list[str]:
    assert "error" not in r, r
    return [x["exercise"] for x in r["session"]["sets"]]


def sets_of(uid: int) -> int:
    with db.session_scope() as s:
        return sum(len(t.sets) for t in s.scalars(select(LiftSession).where(LiftSession.user_id == uid)))


# ---------------------------------------------------------------- the key

def test_name_key_is_the_key_the_list_was_checked_under_on_every_name_alias_and_word():
    texts = [t for m in exercises.MOVEMENTS for t in (m.name, *m.aliases)] + list(exercises.WORDS)
    assert [t for t in texts if rules.name_key(t) != norm(t)] == []
    assert rules.name_key("Farmer’s Carry") == rules.name_key("farmers carry") == "farmercarry"
    assert rules.name_key("Farmer's Carries") == rules.name_key("farmers carry")
    assert rules.name_key("carries") == "carry" and rules.name_key("Flies") == "fly"  # "-ies" as "y" (fix round)
    assert rules.name_key("ties") == "tie"  # 4 letters: only the final s
    assert rules.name_key("Rock & Roll") == "rockandroll"
    assert rules.name_key("T-Bar Rows") == rules.name_key("t bar row") == rules.name_key("T/Bar Row")
    assert rules.name_key("Bench Presses") == "benchpress" and rules.name_key("Press") == "press"
    assert rules.name_key("Flyes") == "fly" and rules.name_key("abs") == "ab" and rules.name_key("as") == "as"


@pytest.mark.parametrize("logging", [False, True])
def test_every_name_in_any_case_spacing_or_plural_reaches_its_own_row(logging):
    for m in exercises.MOVEMENTS:
        names = NAMES if m.kind == "lift" else CARDIO
        last = m.name.split()[-1].lower()
        plural = m.name if last.endswith("s") else m.name + ("es" if last.endswith(("x", "ch", "sh")) else "s")
        # A final consonant and y also takes "-ies": Farmer's Carries, Dumbbell Flies (fix round).
        ies = m.name[:-1] + "ies" if len(last) > 2 and last.endswith("y") and last[-2] not in "aeiou" else plural
        for sent in (m.name, f"  {m.name.upper()}  ", m.name.lower().replace(" ", "  "), plural, ies):
            got = rules.find_exercise(sent, names, logging=logging)
            if logging and got[1] is not None:  # a default's own name takes the word step in log_lifts (decision 2)
                assert exercises.WORDS[got[1]]["default"] == m.name and rules.name_key(got[1]) == rules.name_key(m.name)
                continue
            assert got == (m.name, None, ()), sent


def _bracketed(name: str) -> str:
    """A lift's name as an export writes it: the equipment word out of the name, in brackets after it."""
    said = _said_equipment(name)[0]
    at = name.lower().find(said)
    return f"{' '.join((name[:at] + name[at + len(said):]).split())} ({name[at:at + len(said)]})"


def test_the_ies_forms_said_most_reach_their_rows():
    for sent, row in (("Farmer's Carries", "Farmer's Carry"), ("farmers carries", "Farmer's Carry"),
                      ("Dumbbell Flies", "Dumbbell Fly"), ("Incline Dumbbell Flies", "Incline Dumbbell Fly")):
        assert rules.match_exercise(sent, NAMES) == (row, []), sent
    assert rules.find_exercise("flies", NAMES) == (None, "fly", exercises.WORDS["fly"]["candidates"])


def test_every_lift_whose_name_holds_an_equipment_word_reaches_its_own_row_as_name_equipment():
    shown = [m.name for m in LIFT_ROWS if _said_equipment(m.name)]
    assert len(shown) == 140
    for name in shown:
        form = _bracketed(name)
        assert form != name and "(" in form
        for logging in (False, True):
            assert rules.find_exercise(form, NAMES, logging=logging) == (name, None, ()), form
    assert _bracketed("Dumbbell Bench Press") == "Bench Press (Dumbbell)"
    assert _bracketed("Incline Dumbbell Press") == "Incline Press (Dumbbell)"


def test_squat_barbell_as_strong_writes_it_is_back_squat_and_the_first_place_that_reaches_a_row_wins():
    for sent in ("Squat (Barbell)", "squats  (barbell)", " Squat(Barbell) "):
        assert rules.match_exercise(sent, NAMES) == ("Back Squat", []), sent
        assert rules.find_exercise(sent, NAMES, logging=True) == ("Back Squat", None, ()), sent
    assert rules.match_exercise("Bench Press (Smith Machine)", NAMES) == ("Smith Bench Press", [])
    assert rules.match_exercise("Bench (Barbell)", NAMES) == ("Bench Press", [])  # "barbell bench", an alias
    assert rules.match_exercise("Squat (Cambered Bar)", NAMES)[0] is None
    assert rules.match_exercise("(Barbell)", NAMES)[0] is None and rules.match_exercise("Squat ()", NAMES)[0] is None


SAID = [("box jumps", "Box Jump"), ("box jump", "Box Jump"), ("burpees", "Burpee"), ("burpee", "Burpee"),
        ("burpee jump", "Burpee Broad Jump"), ("hyrox burpees", "Burpee Broad Jump"),
        ("burpee broad jumps", "Burpee Broad Jump")]


@pytest.mark.parametrize("said, row", SAID)
def test_the_said_words_of_decision_31_reach_their_one_row_in_log_lifts_too(uid, said, row):
    assert rules.find_exercise(said, NAMES, logging=True) == (row, None, ())
    sent = {"exercise": said, "weight_kg": 0, "reps": 10} if row != "Burpee Broad Jump" else \
        {"exercise": said, "weight_kg": 0, "distance_m": 80, "time_s": 240}
    assert logged_names(log(uid, sent)) == [row]


def test_a_burpee_word_never_reaches_cardio():
    assert rules.find_exercise("burpees", CARDIO) == (None, None, ())
    assert rules.match_exercise("tabata", CARDIO) == ("HIIT", [])  # a cardio alias still reaches its activity


# ---------------------------------------------------------------- the history step in log_lifts (decision 1)

def test_squat_with_nothing_logged_is_back_squat(uid):
    r = log(uid, {"exercise": "squat", "weight_kg": 80, "reps": 5})
    assert logged_names(r) == ["Back Squat"] and r["summary"].startswith("Back Squat 1 × 5 at 80 kg")


def test_squat_with_only_back_squat_logged_is_back_squat(uid):
    seed(uid, "Back Squat", 5)
    assert logged_names(log(uid, {"exercise": "Squats", "weight_kg": 80, "reps": 5})) == ["Back Squat"]


def test_squat_with_only_smith_squat_logged_is_smith_squat_named_in_the_summary(uid):
    seed(uid, "Smith Squat", 3)
    r = log(uid, *[{"exercise": "squat", "weight_kg": 60, "reps": 5}] * 3)
    assert logged_names(r) == ["Smith Squat"] * 3 and r["summary"].startswith("Smith Squat 3 × 5 at 60 kg")


def test_squat_with_both_logged_asks_with_each_date_and_writes_nothing(uid):
    seed(uid, "Back Squat", 10)
    seed(uid, "Back Squat", 5)
    seed(uid, "Smith Squat", 2)
    before = sets_of(uid)
    r = log(uid, {"exercise": "Bench Press", "weight_kg": 60, "reps": 5, "exact": True},
            *[{"exercise": "squat", "weight_kg": 80, "reps": 5}] * 3)
    assert r["error"] == "pick_lift"
    assert r["message"] == f"Set 2: 'squat' could be Back Squat (last {day(5)}) or Smith Squat (last {day(2)})."
    assert r["fix"] == "Ask the person which, then send that name with exact true."
    assert r["candidates"] == [{"name": "Back Squat", "last": day(5)}, {"name": "Smith Squat", "last": day(2)}]
    assert len(r["errors"]) == 1  # one refusal a word, however many of its sets
    assert sets_of(uid) == before
    for text in (r["message"], r["fix"]):
        assert tr.check(text, tr.MESSAGE) == [], text


def test_two_words_each_with_two_lifts_logged_give_one_refusal_each_and_other_problems_still_show(uid):
    for name in ("Back Squat", "Smith Squat", "Deadlift", "Trap Bar Deadlift"):
        seed(uid, name, 4)
    r = log(uid, {"exercise": "squat", "weight_kg": 80, "reps": 5}, {"exercise": "deadlifts", "weight_kg": 100, "reps": 5},
            {"exercise": "squat", "weight_kg": 80, "reps": 5}, {"exercise": "Leg Extension", "weight_kg": 40, "reps": 200})
    assert [e["error"] for e in r["errors"]] == ["pick_lift", "pick_lift", "reps_out_of_range"]
    assert r["errors"][1]["message"].startswith("Set 2: 'deadlifts' could be Deadlift (last ")


def test_a_soft_deleted_session_is_not_history(uid):
    seed(uid, "Back Squat", 5)
    seed(uid, "Smith Squat", 2)
    with db.session_scope() as s:
        smith = s.scalar(select(LiftSession).where(LiftSession.user_id == uid).order_by(LiftSession.id.desc()))
        smith.deleted_at = NOW
    assert logged_names(log(uid, {"exercise": "squat", "weight_kg": 80, "reps": 5})) == ["Back Squat"]


def test_another_account_s_history_is_never_read(uid, make_user):
    other = make_user(time_zone="UTC").id
    seed(other, "Smith Squat", 2)
    seed(other, "Back Squat", 2)
    assert logged_names(log(uid, {"exercise": "squat", "weight_kg": 80, "reps": 5})) == ["Back Squat"]


def test_bench_press_a_default_s_own_name_takes_the_history_step_in_log_lifts(uid):  # decision 2
    seed(uid, "Smith Bench Press", 7)
    assert logged_names(log(uid, {"exercise": "Bench Press", "weight_kg": 60, "reps": 5})) == ["Smith Bench Press"]
    assert logged_names(log(uid, {"exercise": "bench", "weight_kg": 60, "reps": 5})) == ["Smith Bench Press"]


def test_named_equipment_is_never_a_word(uid):  # word_named_equipment
    seed(uid, "Smith Bench Press", 7)
    assert logged_names(log(uid, {"exercise": "barbell bench press", "weight_kg": 60, "reps": 5})) == ["Bench Press"]
    assert logged_names(log(uid, {"exercise": "dumbbell bench", "weight_kg": 25, "reps": 8})) == [
        "Dumbbell Bench Press"]


def test_bench_with_only_dumbbell_bench_press_logged_takes_bench_press_as_designed(uid):  # word_dumbbell_history
    seed(uid, "Dumbbell Bench Press", 7, weight=30, reps=8)
    assert logged_names(log(uid, {"exercise": "bench", "weight_kg": 30, "reps": 8})) == ["Bench Press"]


def test_exact_true_skips_the_word_step(uid):  # R3
    for name in ("Bench Press", "Smith Bench Press"):
        seed(uid, name, 6)
    asked = log(uid, {"exercise": "Bench Press", "weight_kg": 60, "reps": 5})
    assert asked["error"] == "pick_lift"
    picked = log(uid, {"exercise": "Bench Press", "weight_kg": 60, "reps": 5, "exact": True})
    assert logged_names(picked) == ["Bench Press"]
    assert logged_names(log(uid, {"exercise": "Smith Bench Press", "weight_kg": 60, "reps": 5})) == [
        "Smith Bench Press"]


def test_exact_true_on_a_plain_word_that_is_no_name_is_refused_with_its_candidates(uid):
    r = log(uid, {"exercise": "squat", "weight_kg": 80, "reps": 5, "exact": True})
    assert (r["error"], r["message"], r["fix"], r["closest"]) == (
        "unknown_exercise", "Set 1: 'squat' names more than one lift.", "Ask the person which: Back Squat or Smith Squat.",
        ["Back Squat", "Smith Squat"])
    assert sets_of(uid) == 0


def test_an_unknown_lift_in_log_lifts_asks_the_person_or_a_search(uid):  # unknown_lift_close
    r = log(uid, {"exercise": "cambered bar skull crushers", "weight_kg": 30, "reps": 10})
    assert r["error"] == "unknown_exercise"
    assert r["message"] == "Set 1: 'cambered bar skull crushers' is not on the exercise list."
    assert r["fix"].endswith(" Ask the person which lift it is, or search list_exercises.")
    assert r["fix"].startswith("Closest names: ") and "EZ Bar Skull Crusher" in r["closest"]
    for text in (r["message"], r["fix"]):
        assert tr.check(text, tr.MESSAGE) == [], text


def test_an_add_to_the_day_s_session_takes_the_word_step_too(uid):
    seed(uid, "Smith Squat", 3)
    first = services.log_lifts(uid, {"sets": [{"exercise": "Leg Press", "weight_kg": 100, "reps": 10,
                                               "exact": True}]}, "claude")
    added = services.log_lifts(uid, {"sets": [{"exercise": "squat", "weight_kg": 60, "reps": 5}]}, "claude")
    assert added["id"] == first["id"] and logged_names(added) == ["Leg Press", "Smith Squat"]


def stored(r: dict) -> list[tuple]:
    assert "error" not in r, r
    return [(x["exercise"], x["weight_kg"], x["reps"]) for x in r["session"]["sets"]]


# The pick is one filter over the word's candidates (contract note (i)): a candidate fits when it records what was sent,
# is no band lift, and takes help if and only if the weight sent is below 0. The history table runs over the logged
# ones that fit; with none, the first that fits; with none fitting, the first candidate and its own refusal.

PICKS = [  # (what is logged, as (lift, weight)), the set sent, what is stored
    ((), {"exercise": "pull-ups", "weight_kg": -20, "reps": 8}, ("Assisted Pull-Up", -20, 8)),
    ((), {"exercise": "pull-ups", "weight_lb": -44.0925, "reps": 8}, ("Assisted Pull-Up", -20, 8)),
    ((("Pull-Up", 0),), {"exercise": "pull-ups", "weight_kg": -20, "reps": 8}, ("Assisted Pull-Up", -20, 8)),
    ((("Pull-Up", 0), ("Assisted Pull-Up", -30)), {"exercise": "pull-ups", "weight_kg": -10, "reps": 8},
     ("Assisted Pull-Up", -10, 8)),
    ((("Pull-Up", 0), ("Assisted Pull-Up", -30)), {"exercise": "pull-ups", "weight_kg": 10, "reps": 8},
     ("Pull-Up", 10, 8)),
    ((("Assisted Pull-Up", -30),), {"exercise": "pull-ups", "weight_kg": 10, "reps": 8}, ("Pull-Up", 10, 8)),
    ((("Assisted Pull-Up", -30),), {"exercise": "pull-ups", "weight_kg": 0, "reps": 8}, ("Pull-Up", 0, 8)),  # his A
    ((("Assisted Pull-Up", -30),), {"exercise": "pull-ups", "weight_lb": 0, "reps": 8}, ("Pull-Up", 0, 8)),
    ((("Assisted Pull-Up", -30),), {"exercise": "pull-ups", "weight_kg": -10, "reps": 8},
     ("Assisted Pull-Up", -10, 8)),
    ((("Band-Assisted Pull-Up", 0),), {"exercise": "pull-ups", "weight_kg": 10, "reps": 8}, ("Pull-Up", 10, 8)),
    ((("Band-Assisted Pull-Up", 0),), {"exercise": "pull-up", "weight_kg": 0, "reps": 8}, ("Pull-Up", 0, 8)),
    ((), {"exercise": "dips", "weight_kg": -15, "reps": 10}, ("Assisted Dip", -15, 10)),
    ((("Assisted Dip", -20),), {"exercise": "Dip", "weight_kg": 0, "reps": 10}, ("Dip", 0, 10)),
    ((("Assisted Dip", -20),), {"exercise": "dips", "weight_kg": -15, "reps": 10}, ("Assisted Dip", -15, 10)),
]


@pytest.mark.parametrize("logged, sent, kept", PICKS,
                         ids=[f"{'+'.join(n for n, _ in lo) or 'none'}-{s['exercise']}-"
                              f"{s.get('weight_kg', s.get('weight_lb'))}" for lo, s, _ in PICKS])
def test_the_pick_fits_the_set_sent_and_a_minus_weight_takes_the_assisted_lift_without_a_question(
        make_user, logged, sent, kept):
    uid = make_user(time_zone="UTC").id  # a fresh account for each case
    for n, (name, weight) in enumerate(logged):
        seed(uid, name, 3 + n, weight=weight, reps=8)
    r = log(uid, sent)
    assert stored(r) == [kept]
    if kept[1] < 0:
        assert f"with {-kept[1]} kg help" in r["summary"]


def test_pull_ups_with_no_weight_after_only_assisted_pull_ups_are_pull_up_s_and_ask_for_its_weight(uid):  # his A
    seed(uid, "Assisted Pull-Up", 3, weight=-30, reps=8)
    r = log(uid, {"exercise": "pull-ups", "reps": 8})
    assert (r["error"], r["message"]) == ("missing_field", "Set 1: weight_kg or weight_lb is missing.")
    assert all("help" not in e["message"] for e in r["errors"])


def test_a_minus_weight_on_a_word_no_lift_of_which_takes_help_gets_r6_s_refusal(uid):
    r = log(uid, {"exercise": "squat", "weight_kg": -20, "reps": 5})
    assert (r["error"], r["message"]) == ("weight_out_of_range",
                                          "Set 1: a minus weight is help, on Assisted Pull-Up or Assisted Dip only.")


@pytest.mark.parametrize("weight", [{"weight_kg": "heavy"}, {"weight_kg": ""}, {"weight_kg": True},
                                    {"weight_kg": -10, "weight_lb": -22}, {"weight_lb": "x"}, {"weight_kg": None},
                                    {"weight_kg": [1]}])
def test_a_weight_that_is_no_number_never_breaks_the_pick_and_gets_its_usual_refusal(uid, weight):
    seed(uid, "Assisted Pull-Up", 3, weight=-30, reps=8)
    sent = {"exercise": "pull-ups", "reps": 8, **weight}
    r = log(uid, sent)
    assert "error" in r and r["error"] != "pick_lift", r
    own = log(uid, {**sent, "exercise": "Pull-Up", "exact": True})  # the same set on the lift picked
    assert (r["error"], r["message"], r["fix"]) == (own["error"], own["message"], own["fix"])


def test_walking_lunges_of_distance_and_time_with_nothing_logged_are_the_first_lunge_that_records_them(uid):
    r = log(uid, {"exercise": "walking lunges", "weight_kg": 20, "distance_m": 40, "time_s": 60})
    assert [(x["exercise"], x["weight_kg"], x["distance_m"], x["time_s"]) for x in r["session"]["sets"]] == [
        ("Sandbag Lunge", 20, 40, 60)]


def test_with_only_sandbag_lunge_logged_walking_lunges_of_reps_are_walking_lunge(uid):
    r = services.log_lifts(uid, {"date": day(4), "new_session": True, "sets": [
        {"exercise": "Sandbag Lunge", "weight_kg": 20, "distance_m": 100, "time_s": 90, "exact": True}]}, "claude")
    assert "error" not in r, r
    for sent in ("Walking Lunge", "walking lunges"):
        assert stored(log(uid, {"exercise": sent, "weight_kg": 0, "reps": 20})) == [("Walking Lunge", 0, 20)], sent
    # A set of distance and time still moves to it: its numbers mean the same.
    moved = log(uid, {"exercise": "walking lunge", "weight_kg": 20, "distance_m": 100, "time_s": 80})
    assert [x["exercise"] for x in moved["session"]["sets"]] == ["Sandbag Lunge"]


def test_a_lift_of_another_measure_never_counts_toward_the_question(uid):
    seed(uid, "Walking Lunge", 5)
    r = services.log_lifts(uid, {"date": day(4), "new_session": True, "sets": [
        {"exercise": "Sandbag Lunge", "weight_kg": 20, "distance_m": 100, "time_s": 90, "exact": True}]}, "claude")
    assert "error" not in r, r
    # Walking Lunge and Sandbag Lunge are both logged, but only Walking Lunge records reps: no question.
    assert stored(log(uid, {"exercise": "walking lunges", "weight_kg": 20, "reps": 10})) == [("Walking Lunge", 20, 10)]


# ---------------------------------------------------------------- outside log_lifts: a name as written (decision 28)

def test_an_import_of_bench_press_with_smith_history_lands_on_bench_press(uid):  # import_exact_smith
    seed(uid, "Smith Bench Press", 7)
    r = services.import_entries(uid, {"rows": [{"kind": "lift_session", "date": day(30), "sets": [
        {"exercise": "Bench Press", "weight": 60, "reps": 5, "times": 3}]}], "weight_unit": "kg"}, "claude")
    assert "error" not in r and not r["failed"] and not r["unknown_names"], r
    written = services.import_entries(uid, {"rows": [{"kind": "lift_session", "date": day(30), "sets": [
        {"exercise": "Bench Press", "weight": 60, "reps": 5, "times": 3}]}], "weight_unit": "kg", "dry_run": False},
        "claude")
    assert "error" not in written
    with db.session_scope() as s:
        names = [x.exercise.name for t in s.scalars(select(LiftSession).where(
            LiftSession.user_id == uid, LiftSession.date == date_of(30))) for x in t.sets]
    assert names == ["Bench Press"] * 3


def test_an_import_of_a_plain_word_fails_its_row_with_the_candidates(uid):
    r = services.import_entries(uid, {"rows": [{"kind": "lift_session", "date": day(30), "sets": [
        {"exercise": "Squat", "weight": 100, "reps": 5}]}], "weight_unit": "kg"}, "claude")
    assert r["failed"][0]["error"] == "unknown_exercise"
    assert r["failed"][0]["message"] == "Row 1, set 1: 'Squat' names more than one lift."
    assert r["unknown_names"] == [{"name": "Squat", "kind": "lift", "rows": 1, "closest": ["Back Squat", "Smith Squat"]}]


def test_set_goal_squat_is_refused_with_both_logged_and_bench_press_is_exact(uid):  # note (bp)
    for name, ago in (("Back Squat", 9), ("Smith Squat", 3), ("Bench Press", 8), ("Smith Bench Press", 7)):
        seed(uid, name, ago)
    r = services.set_goal(uid, {"kind": "lift", "exercise": "squat", "target_value": 120,
                                "target_date": day(-60), "start_value": 90}, "claude")
    assert (r["error"], r["message"], r["fix"], r["field"]) == (
        "pick_lift", f"'squat' could be Back Squat (last {day(9)}) or Smith Squat (last {day(3)}).",
        "Ask the person which, then send that name.", "exercise")
    assert r["candidates"] == [{"name": "Back Squat", "last": day(9)}, {"name": "Smith Squat", "last": day(3)}]
    goal = services.set_goal(uid, {"kind": "lift", "exercise": "bench press", "target_value": 100,
                                   "target_date": day(-60), "start_value": 70}, "claude")
    assert "error" not in goal, goal
    assert goal["goal"]["exercise"] == "Bench Press"


# ---------------------------------------------------------------- set_goal's plain word (note (bp))
#
# A plain word takes the lift the person's live sets point to among the candidates a lift goal takes, as log_lifts
# does, by the owner's standing ruling (a plain "squat" is Back Squat unless a Smith squat was logged before). Each
# person here is on lb, as the release eval's lb_bench_goal is.

def lb_uid(make_user) -> int:
    return make_user(time_zone="UTC", weight_unit="lb").id


def lift_goal(uid: int, exercise: str, **more) -> dict:
    return services.set_goal(uid, {"kind": "lift", "exercise": exercise, "target_value": 225,
                                   "target_date": "2027-06-30", **more}, "claude")


def goals_of(uid: int) -> list[str]:
    return [g["exercise"] for g in services.get_goals(uid)["goals"]]


def test_set_goal_bench_with_bench_press_logged_on_lb_is_bench_press(make_user):  # lb_bench_goal
    uid = lb_uid(make_user)
    seed(uid, "Bench Press", 4, weight=80, reps=5)
    r = lift_goal(uid, "bench")
    assert "error" not in r, r
    assert r["goal"]["exercise"] == "Bench Press" and r["progress"]["label"] == "Bench Press 225 lb"
    assert goals_of(uid) == ["Bench Press"]


@pytest.mark.parametrize(("logged", "word", "taken"), [
    ((), "bench", "Bench Press"),  # none logged: the word's default
    (("Dumbbell Bench Press",), "bench", "Bench Press"),  # not a candidate of "bench": the default still
    (("Smith Bench Press",), "bench", "Smith Bench Press"),  # only one logged: that one
    (("Smith Squat",), "squat", "Smith Squat"),
    (("Smith Squat",), "squats", "Smith Squat"),  # a word's plural is the word
    (("Assisted Pull-Up",), "pull-up", "Pull-Up"),  # Pull-Up's own name: exact (the filter: test_goal_lift_...)
    (("Smith Squat",), "Squat (Barbell)", "Back Squat"),  # "Name (Equipment)" is exact
    (("Smith Bench Press",), "Bench Press", "Bench Press"),  # a default's own name is exact
])
def test_set_goal_takes_a_plain_word_by_the_person_s_history(make_user, logged, word, taken):
    uid = lb_uid(make_user)
    for n, name in enumerate(logged):
        seed(uid, name, 5 + n, weight=-20 if rules.takes_help(name) else 60)
    r = lift_goal(uid, word, start_value=135)
    assert "error" not in r, r
    assert r["goal"]["exercise"] == taken and r["progress"]["label"] == f"{taken} 225 lb"
    assert goals_of(uid) == [taken]


def test_set_goal_bench_with_both_bench_lifts_logged_asks_and_writes_nothing(make_user):
    uid = lb_uid(make_user)
    seed(uid, "Bench Press", 10)
    seed(uid, "Smith Bench Press", 2)
    seed(uid, "Dumbbell Bench Press", 1)  # not a candidate of "bench"
    r = lift_goal(uid, "bench", start_value=135)
    assert (r["error"], r["message"], r["fix"], r["field"]) == (
        "pick_lift", f"'bench' could be Bench Press (last {day(10)}) or Smith Bench Press (last {day(2)}).",
        "Ask the person which, then send that name.", "exercise")
    assert r["candidates"] == [{"name": "Bench Press", "last": day(10)}, {"name": "Smith Bench Press", "last": day(2)}]
    assert tr.check(r["message"], tr.CLAUDE) == [] and tr.check(r["fix"], tr.CLAUDE) == []
    assert goals_of(uid) == []
    answered = lift_goal(uid, "Smith Bench Press", start_value=135)  # the fix's answer: the name, exact
    assert "error" not in answered and answered["goal"]["exercise"] == "Smith Bench Press"


def test_set_goal_pull_ups_with_pull_up_and_assisted_logged_is_pull_up_without_a_question(make_user):
    # "pull-ups" is Pull-Up's own name by the plural rule, so it is exact; the filter is test_goal_lift_'s.
    uid = lb_uid(make_user)
    seed(uid, "Pull-Up", 6, weight=10, reps=8)
    seed(uid, "Assisted Pull-Up", 2, weight=-20, reps=8)
    r = lift_goal(uid, "pull-ups", target_value=60)
    assert "error" not in r, r
    assert r["goal"]["exercise"] == "Pull-Up"


def test_set_goal_a_word_with_no_lift_that_takes_a_goal_gets_r10_s_refusal(make_user):
    r = lift_goal(lb_uid(make_user), "sled", start_value=135)
    refusal = next(e for e in r.get("errors", [r]) if e.get("field") == "exercise")
    assert (refusal["error"], refusal["message"]) == (
        "invalid_field", "Sled Push records distance and time: a lift goal needs a lift of weight and reps.")


def test_goal_lift_runs_over_the_candidates_that_take_a_goal():
    pull_ups = exercises.WORDS["pull-up"]["candidates"]
    assert pull_ups == ("Pull-Up", "Assisted Pull-Up", "Band-Assisted Pull-Up")
    last = {"Assisted Pull-Up": date_of(3), "Band-Assisted Pull-Up": date_of(2)}
    assert rules.goal_lift("pull-up", pull_ups, last) == ("Pull-Up", None)  # neither takes a goal: none logged
    assert rules.goal_lift("pull-up", pull_ups, {**last, "Pull-Up": date_of(9)}) == ("Pull-Up", None)
    assert rules.goal_lift("pull-up", ("Assisted Pull-Up", "Pull-Up"), {}) == ("Pull-Up", None)  # the first that takes
    sleds = exercises.WORDS["sled"]["candidates"]
    assert rules.goal_lift("sled", sleds, {"Sled Pull": date_of(1)}) == ("Sled Push", None)  # none takes one
    bench = exercises.WORDS["bench"]["candidates"]
    assert rules.goal_lift("bench", bench, {"Smith Bench Press": date_of(1)}) == ("Smith Bench Press", None)
    name, asked = rules.goal_lift("bench", bench, {"Bench Press": date_of(4), "Smith Bench Press": date_of(1)})
    assert name is None and asked["error"] == "pick_lift" and asked["field"] == "exercise"
    assert [c["name"] for c in asked["candidates"]] == ["Bench Press", "Smith Bench Press"]


def test_through_mcp_set_goal_takes_a_word_and_its_pick_lift_reaches_the_client(writer):
    http, token, account = writer
    ok(http, token, "log_lifts", {"date": day(6), "sets": [{"exercise": "Smith Squat", "weight_kg": 60, "reps": 5,
                                                            "exact": True}]})
    taken = ok(http, token, "set_goal", {"kind": "lift", "exercise": "squat", "target_value": 120,
                                         "target_date": day(-60)})
    assert taken["goal"]["exercise"] == "Smith Squat"
    ok(http, token, "log_lifts", {"date": day(2), "sets": [{"exercise": "Back Squat", "weight_kg": 80, "reps": 5,
                                                            "exact": True}]})
    asked = failed(http, token, "set_goal", {"kind": "lift", "exercise": "squats", "target_value": 140,
                                             "target_date": day(-60)})
    assert (asked["error"], asked["field"], asked["fix"]) == (
        "pick_lift", "exercise", "Ask the person which, then send that name.")
    assert asked["candidates"] == [{"name": "Back Squat", "last": day(2)}, {"name": "Smith Squat", "last": day(6)}]


def test_save_routine_refuses_a_plain_word_and_takes_a_default_s_own_name_as_written(uid):
    seed(uid, "Smith Bench Press", 7)
    r = services.save_routine(uid, {"name": "Legs", "lifts": [{"exercise": "squats", "sets": 3, "reps": 5}]}, "claude")
    assert (r["error"], r["message"], r["fix"]) == (
        "unknown_exercise", "Lift 1: 'squats' names more than one lift.", "Ask the person which: Back Squat or Smith Squat.")
    saved = services.save_routine(uid, {"name": "Push", "lifts": [{"exercise": "bench press", "sets": 3, "reps": 5}]},
                                  "claude")
    assert "error" not in saved, saved
    assert services.get_routines(uid)["routines"][0]["lifts"][0]["exercise"] == "Bench Press"


def test_edit_entry_refuses_a_plain_word_and_takes_an_alias(uid):
    seed(uid, "Back Squat", 2)
    with db.session_scope() as s:
        sid = s.scalar(select(LiftSession.id).where(LiftSession.user_id == uid))
    r = services.edit_entry(uid, "lift_session", sid, {"sets": [{"exercise": "squat", "weight_kg": 80, "reps": 5}]},
                            "claude")
    refusal = r["errors"][0] if "errors" in r else r
    assert (refusal["error"], refusal["message"]) == ("unknown_exercise", "Set 1: 'squat' names more than one lift.")
    edited = services.edit_entry(uid, "lift_session", sid, {"sets": [
        {"exercise": "high bar squat", "weight_kg": 80, "reps": 5}]}, "claude")
    assert "error" not in edited and edited["after"]["sets"][0]["exercise"] == "Back Squat"


def test_log_cardio_takes_an_alias_and_refuses_a_lift_word(uid):
    hiit = services.log_cardio(uid, {"exercise": "tabata", "minutes": 20}, "claude")
    assert "error" not in hiit, hiit
    squat = services.log_cardio(uid, {"exercise": "squat", "minutes": 20}, "claude")
    assert squat["error"] == "unknown_exercise" and "names more than one lift" not in squat["message"]
    assert "Cardio activities: " in squat["fix"]


# ---------------------------------------------------------------- a read takes every candidate logged (decision 4)

def test_get_progress_with_squat_and_one_candidate_logged_is_that_lift_in_full(uid):
    seed(uid, "Smith Squat", 9)
    seed(uid, "Smith Squat", 2, weight=70)
    r = services.get_progress(uid, "squat")
    assert r["lift"]["exercise"] == "Smith Squat" and r["lift"]["sessions"] == 2


def test_get_progress_with_squat_and_none_logged_is_back_squat_never_logged(uid):
    r = services.get_progress(uid, "squats")
    assert r["lift"]["exercise"] == "Back Squat" and r["note"] == "No sets of Back Squat logged yet."


def test_get_progress_with_squat_and_both_logged_gives_both_lifts_only_never_asks(uid):  # read_plain_word, R19
    for name, ago in (("Back Squat", 9), ("Back Squat", 4), ("Smith Squat", 8), ("Smith Squat", 3),
                      ("Bench Press", 2)):
        seed(uid, name, ago)
    goal = services.set_goal(uid, {"kind": "lift", "exercise": "Bench Press", "target_value": 100,
                                   "target_date": day(-60)}, "claude")
    assert "error" not in goal, goal
    squat_goal = services.set_goal(uid, {"kind": "lift", "exercise": "Smith Squat", "target_value": 100,
                                         "target_date": day(-60)}, "claude")
    assert "error" not in squat_goal, squat_goal
    r = services.get_progress(uid, "squat")
    assert "error" not in r and "lift" not in r
    assert [x["exercise"] for x in r["lifts"]] == ["Back Squat", "Smith Squat"]
    assert [g["exercise"] for g in r["goals"]] == ["Smith Squat"]
    assert r["summary"].splitlines()[0].startswith("Back Squat: ") and len(r["summary"].splitlines()) == 2
    assert services.get_progress(uid, "Bench Press")["lift"]["exercise"] == "Bench Press"  # a name is exact here


def test_get_data_s_exercise_filter_with_a_plain_word_reads_every_candidate_the_person_logged(uid):
    for name in ("Back Squat", "Smith Squat", "Leg Press"):
        seed(uid, name, 3)
    r = services.get_data(uid, {"area": "lift_sets", "fields": ["exercise"], "filters": {"exercise": "squat"}})
    assert "error" not in r, r
    assert sorted(row[0] for row in r["rows"]) == ["Back Squat", "Smith Squat"]
    both = services.get_data(uid, {"area": "lift_sessions", "fields": ["exercises"],
                                   "filters": {"exercises": ["squats", "Leg Press"]}})
    assert "error" not in both and len(both["rows"]) == 3
    one = services.get_data(uid, {"area": "lift_sets", "fields": ["exercise"], "filters": {"exercise": "low bar squat"}})
    assert [row[0] for row in one["rows"]] == ["Back Squat"]


# ---------------------------------------------------------------- close names and retired rows

def test_close_names_over_aliases_show_the_lift_s_name_each_once():
    for sent in ("barbel bench pres", "db benchh", "smith machine benchh", "farmers wak", "hammer curlz", "tabatta"):
        _, close = rules.match_exercise(sent, NAMES + CARDIO)
        assert close and set(close) <= set(NAMES + CARDIO) and len(close) == len(set(close)) <= 3, (sent, close)
    assert rules.match_exercise("db benchh", NAMES)[1][0] == "Dumbbell Bench Press"  # "db bench", an alias
    assert rules.match_exercise("tabatta", CARDIO)[1] == ["HIIT"]
    err = rules.unknown_exercise("smith machine benchh", NAMES)
    assert "smith machine bench" not in err["fix"].casefold().replace("smith machine benchh", "")


def test_a_retired_row_matches_nothing_by_name_alias_word_or_close_name(uid):
    with db.session_scope() as s:
        s.scalar(select(Exercise).where(Exercise.name == "Smith Squat")).retired = True
    seed(uid, "Back Squat", 5)
    for sent in ("Smith Squat", "smith machine squat"):
        r = log(uid, {"exercise": sent, "weight_kg": 60, "reps": 5})
        assert r["error"] == "unknown_exercise" and "Smith Squat" not in r["closest"], (sent, r)
    assert logged_names(log(uid, {"exercise": "squat", "weight_kg": 80, "reps": 5})) == ["Back Squat"]
    left = [n for n in NAMES if n != "Smith Squat"]
    assert rules.find_exercise("Smith Squat", left) == (None, None, ())
    assert rules.find_exercise("squat", left) == (None, "squat", ("Back Squat",))


def test_text_that_is_no_name_reaches_nothing_and_a_long_name_skips_every_search():
    for sent in (None, "", "   ", 5, {"not": "text"}, ["squat"]):
        assert rules.find_exercise(sent, NAMES) == (None, None, ())
    rows = [{"exercise": "squat (" + "x" * 100_000 + ")", "weight_kg": 60, "reps": 5}] * 60
    started = time.perf_counter()
    _, _, err = rules.check_session(None, rows, NAMES, TODAY, logging=True, last_logged=lambda names: {})
    assert time.perf_counter() - started < 0.5
    assert err["errors"][0]["error"] == "unknown_exercise" and err["errors"][0]["closest"] == []


def test_the_history_is_read_once_a_call_for_every_candidate_its_words_name():
    calls = []

    def last_logged(names):
        calls.append(list(names))
        return {}
    rows = [{"exercise": w, "weight_kg": 60, "reps": 5} for w in ("squat", "bench", "squats", "Leg Extension")]
    _, sets, err = rules.check_session(None, rows, NAMES, TODAY, logging=True, last_logged=last_logged)
    assert err is None and [x["exercise"] for x in sets] == ["Back Squat", "Bench Press", "Back Squat", "Leg Extension"]
    assert calls == [["Back Squat", "Bench Press", "Smith Bench Press", "Smith Squat"]]
    calls.clear()
    rules.check_session(None, rows[3:], NAMES, TODAY, logging=True, last_logged=last_logged)
    assert calls == []
    # Without logging, an edit's or an import's: no history, and a plain word is refused.
    _, _, err = rules.check_session(None, rows[:1], NAMES, TODAY)
    assert err["error"] == "unknown_exercise" and err["message"] == "Set 1: 'squat' names more than one lift."


# ---------------------------------------------------------------- the model-read text, and through /mcp

def test_the_instructions_say_the_new_sentence_and_are_4082_characters():
    text = mcp_server.INSTRUCTIONS
    assert ("Log a lift in the person's own words, such as squat or bench: Spotter picks the lift. Use other names as "
            "list_exercises gives them. ") in text
    assert "Use exercise names exactly as list_exercises gives them." not in text
    # 2026-11-20: the sleep routing sentence, the shorter Goals sentence, the meal sentence
    assert len(text) == 4082 < 4096
    assert text.endswith("and do not also quote latest, should_be_today or ahead_by.")


def test_log_set_in_takes_the_person_s_words_and_exact_while_set_in_keeps_an_exact_name():
    assert list(LogSetIn.model_fields) == ["exercise", "weight_kg", "weight_lb", "reps", "time_s", "distance_m",
                                           "exact"]
    assert list(SetIn.model_fields) == ["exercise", "weight_kg", "weight_lb", "reps", "time_s", "distance_m"]
    assert LogSetIn.model_fields["exercise"].description == (
        "The lift in the person's own words, such as squat, or a name from list_exercises.")
    assert LogSetIn.model_fields["exact"].description == (
        "true only for a name the person picked from Spotter's question, or a name from get_routines: it is taken "
        "as written.")
    assert SetIn.model_fields["exercise"].description == "The exercise name, exactly as list_exercises gives it."
    for text in (LogSetIn.model_fields["exercise"].description, LogSetIn.model_fields["exact"].description):
        assert tr.check(text, tr.CLAUDE) == [], text


def test_through_mcp_a_word_is_logged_pick_lift_reaches_the_client_and_exact_answers_it(writer):
    http, token, account = writer
    tools = registered_tools(http)  # log_lifts, though no list holds it now (log-and-guide.md)
    log_lifts, routines = tools["log_lifts"], tools["get_routines"]
    assert log_lifts["description"].endswith(
        "Send the person's own word for a lift; Spotter picks it and the summary names it. pick_lift means two lifts "
        "fit: ask which.")
    assert "send its sets with log_lifts, one item a set with exact true, with each change" in routines["description"]
    props = log_lifts["inputSchema"]["$defs"]["LogSetIn"]["properties"]
    assert set(props) == {"exercise", "weight_kg", "weight_lb", "reps", "time_s", "distance_m", "exact"}
    assert tools["edit_entry"]["inputSchema"]["$defs"]["SetIn"]["properties"]["exercise"]["description"] == (
        "The exercise name, exactly as list_exercises gives it.")
    for text in (log_lifts["description"], routines["description"]):
        assert tr.check(text, tr.CLAUDE) == [], text

    first = ok(http, token, "log_lifts", {"date": day(5), "sets": [{"exercise": "squat", "weight_kg": 80, "reps": 5}]})
    assert first["session"]["sets"][0]["exercise"] == "Back Squat"
    ok(http, token, "log_lifts", {"date": day(2), "sets": [{"exercise": "Smith Squat", "weight_kg": 60, "reps": 5}]})
    asked = failed(http, token, "log_lifts", {"sets": [{"exercise": "squat", "weight_kg": 80, "reps": 5}]})
    assert asked["error"] == "pick_lift" and asked["candidates"] == [
        {"name": "Back Squat", "last": day(5)}, {"name": "Smith Squat", "last": day(2)}]
    picked = ok(http, token, "log_lifts", {"sets": [{"exercise": "Back Squat", "weight_kg": 80, "reps": 5,
                                                     "exact": True}]})
    assert picked["session"]["sets"][0]["exercise"] == "Back Squat"
    wrong = failed(http, token, "log_lifts", {"sets": [{"exercise": "Plank", "weight_kg": 0, "seconds": 60}]})
    assert wrong["fix"].startswith("Use: exercise, weight_kg, weight_lb, reps, time_s, distance_m, exact.")
    not_bool = failed(http, token, "log_lifts", {"sets": [{"exercise": "Back Squat", "weight_kg": 80, "reps": 5,
                                                           "exact": "yes"}]})
    assert "exact" in not_bool["message"]
    # A read never asks (R19): both logged, the every-lift form with only those two, through its published model.
    read = ok(http, token, "get_progress", {"exercise": "squat"})
    assert "lift" not in read and [x["exercise"] for x in read["lifts"]] == ["Back Squat", "Smith Squat"]


# ---------------------------------------------------------------- a band lift in a routine (C4c's fix round)

def test_a_routine_takes_no_weight_but_0_on_a_band_lift_and_its_words_name_none(uid):
    band = {"exercise": "Band Pull-Apart", "sets": 3, "reps": 15}
    for weight in ({"weight_kg": 5}, {"weight_lb": 5}, {"weight_kg": -5}):
        r = services.save_routine(uid, {"name": "Warm-up", "lifts": [{**band, **weight}]}, "claude")
        assert (r["error"], r["message"], r["fix"]) == (
            "invalid_field", "Lift 1: Band Pull-Apart records reps, not weight.",
            "Leave the weight out on lift 1: a band lift stores 0 kg."), weight
        for text in (r["message"], r["fix"]):
            assert tr.check(text, tr.MESSAGE) == [], text
    for weight in ({}, {"weight_kg": 0}, {"weight_lb": 0}):
        saved = services.save_routine(uid, {"name": "Warm-up", "lifts": [{**band, **weight}]}, "claude")
        assert "error" not in saved, saved
        assert saved["routine"]["lifts"][0]["weight_kg"] == 0
        assert " kg" not in saved["summary"] and saved["summary"] in (
            "Saved Warm-up: Band Pull-Apart 3 \u00d7 15.", "Warm-up is already saved as given: nothing changed.")
    read = services.get_routines(uid)["routines"][0]
    assert read["lifts"][0]["weight_kg"] == 0 and read["words"] == "Band Pull-Apart 3 \u00d7 15"
    logged = log(uid, {"exercise": "Band Pull-Apart", "reps": 15})
    assert "error" not in logged
    read = services.get_routines(uid)["routines"][0]
    assert (read["lifts"][0]["weight_kg"], read["lifts"][0]["weight_from"]) == (0, "last_session")
    assert read["words"] == "Band Pull-Apart 3 \u00d7 15"
