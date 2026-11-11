"""T2: every summary, note, message and fix the services return, held to the text rules.

Three accounts on a fixed day (2026-11-10): an empty one, one with sample data, and an edge-case one
(a zero weight rate, a day over its calorie target and over 1,000 kcal, a cardio time goal, every
measurement kind, a new best that widens with the one it beat). On each, every read and write runs,
and a battery of refused calls gives the messages and fixes.

Surfaces, from the contract's T2 row:
  summary   summary and note, and the other service-built words Claude reads beside them: a new
            best's text, a goal's label and plan_line, maintenance_note and usda_note; and the pace
            strings a page shows (a session's pace, get_cardio's fastest_pace), m:ss from the formatter
  message   message and fix (an error's errors[] too), and the text for Claude beside them: warnings
            and time_zone_note
Text the person entered (descriptions, notes, names, the goal's own text, a refused value echoed
back) is masked with text_rules.mask_entered first. A widened figure is allowed its trailing zero
only in the one result that carries a widened pair, and in a plan_line of close-call rule 2: that
sentence alone, never the other texts of its result.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import db, formatting, services
from app.models import Bodyweight, CardioEntry, FoodEntry, LiftSession, Measurement, SavedFood
from app.services import goals as goal_service
from app.services import lifts, sample

from . import text_rules as tr

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)   # a Tuesday
TODAY = NOW.date()
SUMMARY_KEYS = ("summary", "note", "text", "label", "plan_line", "maintenance_note", "usda_note",
                "pace", "fastest_pace", "min_per_km")
MESSAGE_KEYS = ("message", "fix", "warnings", "time_zone_note")
KINDS = ("waist", "hips", "chest", "arm", "thigh", "steps", "sleep_hours", "resting_hr", "water_ml")
CODE_VALUES = re.compile(r"\b(?:None|True|False|null)\b")
VALUES = {"waist": 84.5, "hips": 98, "chest": 101.25, "arm": 35.5, "thigh": 58, "steps": 12345,
          "sleep_hours": 7.5, "resting_hr": 58, "water_ml": 2500}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def texts(result, where: str = "result"):
    """(surface, where, text) for every text key in a result, at any depth."""
    if isinstance(result, dict):
        for key, value in result.items():
            here = f"{where}.{key}"
            if key in SUMMARY_KEYS and isinstance(value, str):
                yield tr.SUMMARY, here, value
            elif key in MESSAGE_KEYS and isinstance(value, str):
                yield tr.MESSAGE, here, value
            elif key in MESSAGE_KEYS and isinstance(value, list) and all(isinstance(v, str) for v in value):
                for n, v in enumerate(value):
                    yield tr.MESSAGE, f"{here}[{n}]", v
            else:
                yield from texts(value, here)
    elif isinstance(result, list):
        for n, value in enumerate(result):
            yield from texts(value, f"{where}[{n}]")


class Seen:
    """Every text met, by surface, each with the values the person entered in the call that returned it.
    Only that call's values are masked in its texts, so a refusal's inputs never hide a figure elsewhere."""

    def __init__(self):
        self.items: list[tuple[str, str, str, bool, frozenset]] = []

    def add(self, name: str, result, *entered, widened: bool | frozenset = False):
        """widened: True for every text of the result, or a set of the exact texts that carry a widened
        figure (a rule 2 plan_line), so the other texts beside them are still held to the rule."""
        mine = frozenset(str(e) for e in entered if e is not None)
        wide = (lambda t: t in widened) if isinstance(widened, (set, frozenset)) else (lambda t: widened)
        self.items += [(surface, f"{name}: {where}", text, wide(text), mine) for surface, where, text in texts(result)]
        return result

    def keys(self) -> set[str]:
        return {where.rsplit(".", 1)[-1].split("[")[0] for _, where, _, _, _ in self.items}

    def texts(self) -> set[str]:
        return {text for _, _, text, _, _ in self.items}

    def findings(self) -> list[str]:
        out = []
        for surface, where, text, widened, entered in self.items:
            masked = tr.mask_entered(text, entered)
            found = tr.check(masked, surface, widened=widened)
            if surface == tr.SUMMARY:  # a Python or JSON value word standing for a figure or a word
                found += [tr.Finding("code_value", m.group(0), masked) for m in CODE_VALUES.finditer(masked)]
            for f in found:
                out.append(f"{where} [{surface}] {f}\n    {text!r}")
        return out

    def assert_clean(self):
        found = self.findings()
        assert not found, f"{len(found)} findings:\n" + "\n".join(found)


def refusals(seen: Seen, user_id: int):
    """A battery of refused calls, one or more for every write and read that can refuse."""
    bad = [
        ("log_lifts", lambda: services.log_lifts(user_id, {"date": "2999-01-01", "sets": [
            {"exercise": "Bak Squat", "weight_kg": 501, "reps": 31}, {"exercise": "", "weight_kg": "x", "reps": 2.5},
            {"exercise": "Back Squat", "weight_kg": 500.555, "reps": 0}]}, "claude"), ("Bak Squat", "x")),
        ("log_lifts date", lambda: services.log_lifts(user_id, {"date": "soon", "sets": []}, "claude"), ("soon",)),
        ("log_lifts old", lambda: services.log_lifts(user_id, {"date": "2020-01-01", "sets": [
            {"exercise": "Deadlift", "weight_kg": 100, "reps": 5}]}, "claude"), ()),
        ("log_bodyweight", lambda: services.log_bodyweight(user_id, {"kg": 1234.5}, "claude"), (1234.5,)),
        ("log_bodyweight text", lambda: services.log_bodyweight(user_id, {"kg": "heavy"}, "claude"), ("heavy",)),
        ("log_measurement steps", lambda: services.log_measurement(
            user_id, {"kind": "steps", "value": 8000.5}, "claude"), (8000.5,)),
        ("log_measurement water", lambda: services.log_measurement(
            user_id, {"kind": "water_ml", "value": 25000}, "claude"), (25000,)),
        ("log_measurement waist", lambda: services.log_measurement(
            user_id, {"kind": "waist", "value": 12.25}, "claude"), (12.25,)),
        ("log_measurement kind", lambda: services.log_measurement(
            user_id, {"kind": "wasit", "value": 80}, "claude"), ("wasit",)),
        ("log_food", lambda: services.log_food(user_id, {"entries": [
            {"description": "feast", "kcal": 5000.6}, {"description": "", "kcal": "lots"},
            {"description": "x", "meal": "brunch", "kcal": 1}, {"saved_food_id": 999999},
            {"usda_fdc_id": "abc", "grams": 10}, {"description": "y", "kcal": 1, "saved_food_id": 1},
            {"description": "z", "kcal": 1, "colour": "red"}]}, "claude"),
         ("feast", "lots", "brunch", "abc", "colour", "x", "y", "z", 5000.6)),
        ("log_food none", lambda: services.log_food(user_id, {"entries": []}, "claude"), ()),
        ("save_food", lambda: services.save_food(user_id, {"name": "", "serving_label": "x" * 65,
                                                           "kcal_per_serving": 5000.5, "grams_per_serving": 0.5,
                                                           "shape": 1}, "claude"), ("shape", 5000.5, 0.5)),
        ("log_cardio speed", lambda: services.log_cardio(user_id, {"exercise": "Outdoor Run", "minutes": 30,
                                                                   "distance_km": 5, "speed_kmh": 30}, "claude"), ()),
        ("log_cardio worked", lambda: services.log_cardio(user_id, {"exercise": "Outdoor Run", "minutes": 1,
                                                                    "distance_km": 300}, "claude"), ()),
        ("log_cardio ranges", lambda: services.log_cardio(user_id, {"exercise": "Back Squat", "minutes": 601,
                                                                    "avg_hr": 120.5, "machine_kcal": 10_000,
                                                                    "incline_pct": 41, "notes": "a\x00b"},
                                                          "claude"), ("Back Squat", 601, 120.5, 10_000, 41)),
        ("set_goal kind", lambda: services.set_goal(user_id, {"kind": "calories", "target_value": 2000}, "claude"),
         ()),
        ("set_goal nothing", lambda: services.set_goal(user_id, {"kind": "bodyweight"}, "claude"), ()),
        ("set_goal ranges", lambda: services.set_goal(user_id, {"kind": "bodyweight", "target_value": 1000.5,
                                                                "target_date": "2020-01-01", "status": "won"},
                                                      "claude"), (1000.5, "won")),
        ("set_goal lift", lambda: services.set_goal(user_id, {"kind": "lift", "exercise": "Treadmill Run",
                                                              "target_value": 900, "target_date": "2027-01-01"},
                                                    "claude"), (900,)),
        ("set_goal cardio", lambda: services.set_goal(user_id, {"kind": "cardio_best", "exercise": "Back Squat",
                                                                "distance_km": 301, "target_value": 0.5,
                                                                "target_date": "2027-01-01"}, "claude"), (301, 0.5)),
        ("set_goal measurement", lambda: services.set_goal(user_id, {"kind": "measurement", "measurement_kind": "steps",
                                                                     "target_value": 10000,
                                                                     "target_date": "2027-01-01"}, "claude"), ()),
        ("set_goal resting", lambda: services.set_goal(user_id, {"kind": "measurement",
                                                                 "measurement_kind": "resting_hr",
                                                                 "target_value": 55.5, "target_date": "2027-01-01"},
                                                       "claude"), (55.5,)),
        ("set_goal missing", lambda: services.set_goal(user_id, {"goal_id": 999999, "status": "hit"}, "claude"), ()),
        ("update_settings", lambda: services.update_settings(user_id, {"time_zone": "EST", "calorie_target_kcal": 799,
                                                                       "target_sessions_per_week": 2.5,
                                                                       "claude_can": "log", "colour": 1}, "claude"),
         ("EST", 799, 2.5, "colour")),
        ("update_settings none", lambda: services.update_settings(user_id, {}, "claude"), ()),
        ("edit_entry kind", lambda: services.edit_entry(user_id, "goal", 1, {"x": 1}, "claude"), ()),
        ("edit_entry fields", lambda: services.edit_entry(user_id, "bodyweight", 1, {"notes": "x"}, "claude"),
         ("notes",)),
        ("edit_entry missing", lambda: services.edit_entry(user_id, "bodyweight", 999999, {"kg": 80}, "claude"), ()),
        ("delete_entry", lambda: services.delete_entry(user_id, "goal", 1), ()),
        ("restore_entry", lambda: services.restore_entry(user_id, "food_entry", 999999), ()),
        ("get_log", lambda: services.get_log(user_id, date="2999-01-01", days=15), ()),
        ("get_log days", lambda: services.get_log(user_id, days=15), (15,)),
        ("get_progress", lambda: services.get_progress(user_id, "Bak Squat"), ("Bak Squat",)),
        ("get_cardio lift", lambda: services.get_cardio(user_id, "Back Squat"), ("Back Squat",)),
        ("get_cardio unknown", lambda: services.get_cardio(user_id, "treadmil rnu"), ("treadmil rnu",)),
        ("find_food", lambda: services.find_food(user_id, "", limit=30), ()),
        ("find_food limit", lambda: services.find_food(user_id, "dal", limit=30), (30,)),
        ("list_exercises", lambda: services.list_exercises("dance"), ("dance",)),
        ("by", lambda: services.log_bodyweight(user_id, {"kg": 80}, "robot"), ()),
    ]
    for name, call, entered in bad:
        r = seen.add(name, call(), *entered)
        assert "error" in r, (name, r)


def widened_lines(user_id: int) -> frozenset:
    """The plan_line of every open goal in close-call rule 2 (formatting.widen on the unrounded now and
    plan figures, from goal_charts): the sentences that carry one more decimal."""
    return frozenset(n["plan_line"] for n in services.goal_charts(user_id)["goals"]
                     if n["plan_line"] and n["vs_plan"] != "on the line"
                     and formatting.widen([n["latest"], n["should_be_today"]],
                                          goal_service.goal_quantity(n["kind"], n["unit"])))


PLAN_FIGURES = re.compile(r"^(?P<now>.+?) now against (?:the target of )?(?P<plan>.+?)(?: on the plan line today)?: ")


def assert_plan_lines_part_their_figures(seen: Seen) -> int:
    """No plan_line shows two equal figures beside a gap: where a sentence has a now figure and a plan
    figure, the two read differently (close-call rule 2 widens them apart). Returns how many it read."""
    lines = {text for _, where, text, _, _ in seen.items if where.endswith(".plan_line")}
    pairs = [m for m in (PLAN_FIGURES.match(line) for line in lines) if m]
    same = [m.string for m in pairs if m["now"] == m["plan"]]
    assert not same, same
    return len(pairs)


def every_read(seen: Seen, user_id: int, widened: bool = False):
    """Every read. widened: the log carries a widened pair (a new best's text). A rule 2 plan_line is
    widened on its own, wherever a goal carries it."""
    lines = widened_lines(user_id)
    seen.add("whoami", services.whoami(user_id))
    seen.add("get_trends", services.get_trends(user_id))
    seen.add("get_goals", services.get_goals(user_id, include_closed=True), widened=lines)
    seen.add("get_progress", services.get_progress(user_id), widened=lines)
    for name in ("Back Squat", "Deadlift", "Outdoor Run", "Treadmill Walk"):
        seen.add(f"get_progress {name}", services.get_progress(user_id, name), widened=lines)
    seen.add("get_log", services.get_log(user_id, days=14), widened=widened)
    seen.add("get_days", services.get_days(user_id))
    seen.add("get_cardio", services.get_cardio(user_id))
    for name in ("Outdoor Run", "Treadmill Walk", "Hiking"):
        seen.add(f"get_cardio {name}", services.get_cardio(user_id, name))
    seen.add("list_saved_foods", services.list_saved_foods(user_id))
    seen.add("find_food", services.find_food(user_id, "roti"), "roti")
    seen.add("export_data", services.export_data(user_id))
    seen.add("sample_state", services.sample_state(user_id))


# ---------------------------------------------------------------- the three accounts

def test_an_empty_account(make_user):
    user = make_user()
    seen = Seen()
    every_read(seen, user.id)
    refusals(seen, user.id)
    seen.add("remove_sample_data", services.remove_sample_data(user.id, "web"))
    assert {"summary", "note", "message", "fix", "time_zone_note"} <= seen.keys()
    seen.assert_clean()


def sample_entered() -> set[str]:
    names = {name for name, _, _ in sample.SAVED_FOODS} | {label for _, label, _ in sample.SAVED_FOODS}
    names |= {item[0] for _, items in sample.MEALS for item in items} | {sample.SNACK}
    return names


def test_a_sample_account(make_user):
    user = make_user(time_zone="Europe/London")
    seen = Seen()
    names = sample_entered()   # the sample's own foods and descriptions: masked only where a call shows them
    seen.add("add_sample_data", services.add_sample_data(user.id, "web"))
    every_read(seen, user.id)
    with db.new_session() as s:
        roti = s.scalar(select(SavedFood.id).where(SavedFood.user_id == user.id, SavedFood.name == "Roti"))
        walk = s.scalar(select(CardioEntry.id).where(CardioEntry.user_id == user.id).order_by(CardioEntry.id))
        session = s.scalar(select(LiftSession.id).where(LiftSession.user_id == user.id).order_by(LiftSession.id))
        weigh_in = s.scalar(select(Bodyweight.id).where(Bodyweight.user_id == user.id).order_by(Bodyweight.id))
        waist = s.scalar(select(Measurement.id).where(Measurement.user_id == user.id, Measurement.kind == "waist")
                         .order_by(Measurement.id))
        food = s.scalar(select(FoodEntry.id).where(FoodEntry.user_id == user.id).order_by(FoodEntry.id))
    seen.add("log_lifts", services.log_lifts(user.id, {"notes": "felt strong", "sets": [
        {"exercise": "Back Squat", "weight_kg": 120, "reps": 5}] * 3 + [
        {"exercise": "Bench Press", "weight_kg": 90, "reps": 5}, {"exercise": "Bench Press", "weight_kg": 85, "reps": 6}]},
        "claude"), "felt strong")
    seen.add("log_bodyweight", services.log_bodyweight(user.id, {"kg": 78.6}, "claude"))
    seen.add("log_measurement", services.log_measurement(user.id, {"kind": "waist", "value": 86.25}, "claude"))
    seen.add("log_food", services.log_food(user.id, {"entries": [
        {"saved_food_id": roti, "servings": 3}, {"description": "Paneer tikka", "kcal": 420, "estimated": True}]},
        "claude"), "Paneer tikka", *names)
    seen.add("log_cardio", services.log_cardio(user.id, {"exercise": "Treadmill Walk", "minutes": 42.5,
                                                         "date": ago(1)}, "claude"))
    seen.add("edit cardio", services.edit_entry(user.id, "cardio_entry", walk, {"minutes": 35.5,
                                                                                "notes": "hilly"}, "web"), "hilly")
    # An edit summary quotes the set lists (rules.quote, cut at 80 characters): text T2 treats as entered.
    before = services.get_entry(user.id, "lift_session", session)["entry"]["sets"]
    quoted = lifts.sets_text([(x["exercise"], x["weight_kg"], x["reps"]) for x in before])
    seen.add("edit session", services.edit_entry(user.id, "lift_session", session, {"sets": [
        {"exercise": "Back Squat", "weight_kg": 82.5, "reps": 5}]}, "web"), quoted)
    seen.add("edit weigh-in", services.edit_entry(user.id, "bodyweight", weigh_in, {"kg": 80.25}, "web"))
    seen.add("edit measurement", services.edit_entry(user.id, "measurement", waist, {"value": 88.25}, "web"))
    seen.add("edit food", services.edit_entry(user.id, "food_entry", food, {"kcal": 310, "meal": "lunch"}, "web"))
    seen.add("delete", services.delete_entry(user.id, "food_entry", food))
    seen.add("restore", services.restore_entry(user.id, "food_entry", food))
    seen.add("set_goal", services.set_goal(user.id, {"kind": "lift", "exercise": "Deadlift", "target_value": 160,
                                                     "target_date": "2027-03-31", "text": "by spring"}, "claude"),
             "by spring")
    for g in services.get_goals(user.id)["goals"]:
        seen.add("close goal", services.set_goal(user.id, {"goal_id": g["goal_id"], "status": "hit"}, "web"))
    every_read(seen, user.id)
    seen.add("remove_sample_data", services.remove_sample_data(user.id, "web"))
    assert any(t.endswith(" 1 real entry was logged against a sample food. It keeps its numbers and loses the link.")
               for t in seen.texts())   # a real entry lost its link to a sample food
    refusals(seen, user.id)
    assert {"summary", "note", "text", "label", "message", "fix"} <= seen.keys()
    # The web-only reads are walked too: their notes, and every pace string (stage 6, E2 and E3).
    assert {"pace", "fastest_pace", "min_per_km"} <= seen.keys()
    assert {"5:54", "No Hiking logged yet."} <= seen.texts()
    assert any(where.startswith("get_days: ") and where.endswith(".note") for _, where, *_ in seen.items)
    assert assert_plan_lines_part_their_figures(seen) >= 3   # the three sample goals, the body weight one rule 2
    seen.assert_clean()


def test_an_edge_case_account(make_user):
    user = make_user(time_zone="UTC", calorie_target_kcal=2000, target_sessions_per_week=3,
                     target_cardio_minutes_per_week=150, add_back_exercise_kcal=True)
    seen = Seen()
    # A zero rate: the same weight at the rate's two ends, so the smoothed line does not move.
    for days in (30, 20, 0):
        seen.add("log_bodyweight", services.log_bodyweight(user.id, {"kg": 80, "date": ago(days)}, "claude"))
    trends = seen.add("get_trends", services.get_trends(user.id))
    assert trends["bodyweight"]["rate_per_week"] == 0 and ", 0 kg a week" in trends["summary"]
    assert "+0" not in trends["summary"]
    # A day over its target and over 1,000 kcal.
    day = seen.add("log_food", services.log_food(user.id, {"entries": [
        {"description": "Biryani", "kcal": 1450}, {"description": "Kulfi", "kcal": 980, "estimated": True}]},
        "claude"), "Biryani", "Kulfi")
    assert day["day"]["remaining_kcal"] == -430 and "430 kcal over" in day["summary"] and "2,430" in day["summary"]
    # Every measurement kind, each logged twice on one day so the second replaces the first.
    for kind in KINDS:
        seen.add(f"log_measurement {kind}", services.log_measurement(user.id, {"kind": kind, "value": VALUES[kind]},
                                                                     "claude"))
        seen.add(f"log_measurement {kind} again", services.log_measurement(
            user.id, {"kind": kind, "value": VALUES[kind] + 1}, "claude"))
    # The kind's word from measurements.KIND_WORDS: it never repeats the unit beside the figure.
    assert {"Water 2,501 ml on Tue 10 Nov. Replaced 2,500 ml.", "Resting heart rate 59 bpm on Tue 10 Nov. Replaced "
            "58 bpm.", "Sleep 8.5 h on Tue 10 Nov. Replaced 7.5 h.", "Steps 12,346 on Tue 10 Nov. Replaced 12,345."} \
        <= seen.texts()
    assert not [t for t in seen.texts() if re.search(r"Water ml|Resting hr|Sleep hours|water ml|resting hr", t)]
    # A cardio time goal, and goals in cm and bpm.
    for minutes, days in ((31.25, 20), (30.5, 13), (29.75, 6), (29.5, 1)):
        seen.add("log_cardio", services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": minutes,
                                                             "distance_km": 5, "date": ago(days)}, "claude"))
    seen.add("set_goal run", services.set_goal(user.id, {"kind": "cardio_best", "exercise": "Outdoor Run",
                                                         "distance_km": 5, "target_value": 27.5,
                                                         "target_date": "2027-01-31", "start_value": 32},
                                               "claude"))
    seen.add("set_goal waist", services.set_goal(user.id, {"kind": "measurement", "measurement_kind": "waist",
                                                           "target_value": 80.5, "target_date": "2027-01-31"}, "claude"))
    seen.add("set_goal resting", services.set_goal(user.id, {"kind": "measurement", "measurement_kind": "resting_hr",
                                                             "target_value": 52, "target_date": "2027-01-31"}, "claude"))
    seen.add("set_goal weight", services.set_goal(user.id, {"kind": "bodyweight", "target_value": 75.25,
                                                            "target_date": "2027-01-31"}, "claude"))
    seen.add("set_goal again", services.set_goal(user.id, {"kind": "bodyweight", "target_value": 76,
                                                           "target_date": "2027-02-28"}, "claude"))
    # A new best that prints the same as the one it beat at 1 dp: the pair widens together.
    seen.add("log_lifts first", services.log_lifts(user.id, {"date": ago(2), "sets": [
        {"exercise": "Back Squat", "weight_kg": 100, "reps": 5}]}, "claude"))
    widened = seen.add("log_lifts widened", services.log_lifts(user.id, {"sets": [
        {"exercise": "Back Squat", "weight_kg": 100.03, "reps": 5}]}, "claude"), widened=True)
    best = next(b for b in widened["new_bests"] if b["kind"] == "estimated_max")
    assert (best["value"], best["previous"]) == (116.7, 116.67)   # 116.7016 and 116.6667, at 2 dp
    assert "116.70 kg, up from 116.67 kg" in best["text"]
    seen.add("log_cardio long", services.log_cardio(user.id, {"exercise": "Treadmill Walk", "minutes": 75.5,
                                                              "machine_kcal": 1234}, "claude"))
    seen.add("update_settings", services.update_settings(user.id, {"calorie_target_kcal": 2500,
                                                                   "target_cardio_minutes_per_week": 1500,
                                                                   "add_back_exercise_kcal": False}, "claude"))
    every_read(seen, user.id, widened=True)
    refusals(seen, user.id)
    assert {"summary", "note", "text", "label", "message", "fix", "warnings"} <= seen.keys()
    assert_plan_lines_part_their_figures(seen)
    seen.assert_clean()


def test_the_other_paths(make_user):
    """The branches the three accounts do not reach: replacing, duplicates, no-op edits, every kind of edit
    part, a saved food, no weigh-in, first sessions, the rate gaps, a settings no-op, a restore swap."""
    user = make_user()
    seen = Seen()
    say = lambda name, result, *entered, **kw: seen.add(name, result, *entered, **kw)  # noqa: E731
    # No weigh-in: the cardio notes give METs; the rate-gap notes; first sessions; no new bests.
    say("cardio no weigh-in", services.log_cardio(user.id, {"exercise": "Treadmill Walk", "minutes": 30}, "claude"))
    say("cardio no weigh-in, several", services.log_cardio(user.id, {"exercise": "Outdoor Walk", "minutes": 30,
                                                                       "distance_km": 3}, "claude"))
    say("cardio duplicate", services.log_cardio(user.id, {"exercise": "Outdoor Walk", "minutes": 30,
                                                          "distance_km": 3}, "claude"))
    say("trends, one end", services.get_trends(user.id))
    say("weigh-in", services.log_bodyweight(user.id, {"kg": 80.5, "date": ago(2)}, "claude"))
    say("weigh-in replaced", services.log_bodyweight(user.id, {"kg": 81.25, "date": ago(2)}, "claude"))
    say("weigh-in duplicate", services.log_bodyweight(user.id, {"kg": 81.25, "date": ago(2)}, "claude"))
    say("trends, no latest", services.get_trends(user.id))
    first = say("first sessions", services.log_lifts(user.id, {"date": ago(1), "sets": [
        {"exercise": "Back Squat", "weight_kg": 60, "reps": 12}, {"exercise": "Pull-Up", "weight_kg": 0, "reps": 12}]},
        "claude"))
    assert "First Back Squat session: 60 kg is the mark to beat. First Pull-Up session." in first["summary"]
    same = say("no new bests", services.log_lifts(user.id, {"date": ago(1), "sets": [
        {"exercise": "Back Squat", "weight_kg": 60, "reps": 12}], "allow_repeat": True}, "claude"))
    assert same["summary"].endswith(" No new bests.")
    say("lifts duplicate", services.log_lifts(user.id, {"date": ago(1), "sets": [
        {"exercise": "Back Squat", "weight_kg": 60, "reps": 12}]}, "claude"))
    # Food: a saved food, its duplicate, a USDA-off search, saved foods with one deleted.
    roti = say("save_food", services.save_food(user.id, {"name": "Roti", "serving_label": "1 roti (big)",
                                                         "kcal_per_serving": 120.5, "grams_per_serving": 40},
                                               "web"), "Roti", "1 roti (big)")
    say("save_food again", services.save_food(user.id, {"name": "roti", "serving_label": "1 roti (big)",
                                                        "kcal_per_serving": 1250}, "web"), "roti", "1 roti (big)")
    dal = say("save_food dal", services.save_food(user.id, {"name": "Dal; tadka", "serving_label": "1 bowl",
                                                            "kcal_per_serving": 180}, "web"), "Dal; tadka", "1 bowl")
    say("delete saved food", services.delete_entry(user.id, "saved_food", dal["id"]))
    say("list_saved_foods", services.list_saved_foods(user.id))
    meal = {"entries": [{"saved_food_id": roti["id"], "servings": 1.5}, {"description": "dal (home)", "kcal": 180}]}
    eaten = say("log_food", services.log_food(user.id, meal, "claude"), "Roti", "dal (home)")
    say("log_food duplicate", services.log_food(user.id, meal, "claude"), "Roti", "dal (home)")
    # Every kind of edit part: a date, a cleared value, a worked-out speed, kcal confirmed, a description, an
    # activity, a no-op.
    weigh_in = services.get_log(user.id, date=ago(2))["days"][0]["weigh_in"]["id"]
    say("edit date", services.edit_entry(user.id, "bodyweight", weigh_in, {"date": ago(3)}, "web"))
    say("edit no-op", services.edit_entry(user.id, "bodyweight", weigh_in, {"kg": 81.25}, "web"))
    walk = services.get_log(user.id)["days"][0]["cardio"][-1]["id"]
    say("edit worked out", services.edit_entry(user.id, "cardio_entry", walk, {"minutes": 45.5}, "web"))
    say("edit cleared", services.edit_entry(user.id, "cardio_entry", walk, {"distance_km": None}, "web"))
    say("edit filled", services.edit_entry(user.id, "cardio_entry", walk, {"distance_km": 4, "avg_hr": 118}, "web"))
    waist = services.log_measurement(user.id, {"kind": "waist", "value": 84.25}, "claude")["id"]
    say("edit same print", services.edit_entry(user.id, "measurement", waist, {"value": 84.34}, "web"))
    say("replace same print", services.log_measurement(user.id, {"kind": "waist", "value": 84.26}, "claude"))
    say("edit activity", services.edit_entry(user.id, "cardio_entry", walk, {"exercise": "Hiking",
                                                                             "machine_kcal": 1500}, "web"))
    guess = services.log_food(user.id, {"entries": [{"description": "poha", "kcal": 250, "estimated": True}]},
                              "claude")["ids"][0]
    say("edit confirmed", services.edit_entry(user.id, "food_entry", guess, {"kcal": 250}, "web"))
    say("edit description", services.edit_entry(user.id, "food_entry", guess, {"description": "poha (2 cups)"},
                                                 "web"), "poha", "poha (2 cups)")
    # A restore that swaps two weigh-ins of one day; a settings no-op.
    say("weigh-in today", services.log_bodyweight(user.id, {"kg": 80}, "claude"))
    replaced = say("weigh-in today again", services.log_bodyweight(user.id, {"kg": 79.75}, "claude"))
    say("restore swap", services.restore_entry(user.id, "bodyweight", replaced["replaced"]["id"]))
    say("settings no-op", services.update_settings(user.id, {"time_zone": "UTC"}, "claude"))
    say("settings no-op again", services.update_settings(user.id, {"time_zone": "UTC"}, "claude"))
    every_read(seen, user.id)
    texts_seen = seen.texts()
    assert any(t.startswith("Replaced the ") and "weigh-in of 80.5 kg" in t for t in texts_seen)
    assert any(t.startswith("Already logged: Back Squat 1 × 12 at 60 kg on ") for t in texts_seen)
    assert "Weigh-in: nothing changed. Every value given is already stored." in texts_seen
    assert "Roti" in str(roti["summary"]) and ", 40 g a serving." in roti["summary"]
    assert "1 saved food, 1 deleted." in texts_seen
    assert ("Cardio entry changed: distance_km set to 4. avg_hr set to 118. speed_kmh set to 5.3, worked out from "
            "distance and minutes.") in texts_seen   # filled where it was empty: never Python's None
    assert "Measurement changed: value from 84.25 cm to 84.34 cm." in texts_seen
    assert any(t.startswith("Waist 84.26 cm on ") and t.endswith(" Replaced 84.34 cm.") for t in texts_seen)
    assert {"summary", "note", "maintenance_note"} <= seen.keys()
    seen.assert_clean()


def test_the_walk_finds_nested_texts():
    result = {"summary": "a", "goals": [{"note": "b", "label": "c", "plan_line": "h"}],
              "errors": [{"message": "d", "fix": "e"}], "warnings": ["f", "g"], "how": "not read"}
    assert sorted((surface, text) for surface, _, text in texts(result)) == [
        (tr.MESSAGE, "d"), (tr.MESSAGE, "e"), (tr.MESSAGE, "f"), (tr.MESSAGE, "g"),
        (tr.SUMMARY, "a"), (tr.SUMMARY, "b"), (tr.SUMMARY, "c"), (tr.SUMMARY, "h")]


def test_a_widened_set_widens_only_its_own_texts():
    seen = Seen()
    seen.add("goals", {"summary": "Down 0.10 kg.", "goals": [{"plan_line": "80.10 kg now."}]},
             widened=frozenset({"80.10 kg now."}))
    assert [line.split(" [")[0] for line in seen.findings()] == ["goals: result.summary"]


def test_every_plan_line_form(make_user, monkeypatch):
    """Each plan_line template of section E1, from real goals on the fixed day: ahead or behind, on the line,
    close-call rule 2 (widened, that sentence alone), after the target date (off the line and at the target), and a
    cardio time in both forms.
    Each goal is set on its start day, so its plan line today is its start; a second entry then moves now."""
    user = make_user(time_zone="UTC")
    seen = Seen()
    say = lambda name, result, *entered, **kw: seen.add(name, result, *entered, **kw)  # noqa: E731
    early = NOW - timedelta(days=10)
    monkeypatch.setattr(db, "utcnow", lambda: early)       # a waist goal whose date passes before the fixed day
    say("waist", services.log_measurement(user.id, {"kind": "waist", "value": 90}, "claude"))
    say("waist goal", services.set_goal(user.id, {"kind": "measurement", "measurement_kind": "waist",
                                                  "target_value": 85, "target_date": ago(5)}, "claude"))
    say("hips", services.log_measurement(user.id, {"kind": "hips", "value": 95}, "claude"))
    say("hips goal", services.set_goal(user.id, {"kind": "measurement", "measurement_kind": "hips",
                                                 "target_value": 90, "target_date": ago(5)}, "claude"))
    monkeypatch.setattr(db, "utcnow", lambda: NOW)
    say("waist now", services.log_measurement(user.id, {"kind": "waist", "value": 88}, "claude"))
    say("hips now", services.log_measurement(user.id, {"kind": "hips", "value": 90.02}, "claude"))  # at the target
    # Body weight, rule 2: 80.14 now against a start of 80.08, both 80.1 kg at 1 dp, the gap 0.06 kg.
    say("weigh-in", services.log_bodyweight(user.id, {"kg": 80.08}, "claude"))
    say("weight goal", services.set_goal(user.id, {"kind": "bodyweight", "target_value": 75,
                                                   "target_date": "2027-01-31"}, "claude"))
    say("weigh-in again", services.log_bodyweight(user.id, {"kg": 80.14}, "claude"))
    # A lift on the line: its start day, nothing since.
    say("squat", services.log_lifts(user.id, {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}]},
                                    "claude"))
    say("squat goal", services.set_goal(user.id, {"kind": "lift", "exercise": "Back Squat", "target_value": 130,
                                                  "target_date": "2027-01-31"}, "claude"))
    # Resting heart rate, behind: 66 now against a start of 64.
    say("resting", services.log_measurement(user.id, {"kind": "resting_hr", "value": 64}, "claude"))
    say("resting goal", services.set_goal(user.id, {"kind": "measurement", "measurement_kind": "resting_hr",
                                                    "target_value": 58, "target_date": "2027-01-31"}, "claude"))
    say("resting again", services.log_measurement(user.id, {"kind": "resting_hr", "value": 66}, "claude"))
    # Two cardio times. Treadmill Run: 30:00 at the start, 29:42 now. Outdoor Run, rule 2: 10.01 km in 60.1 min
    # is 30.02 min for 5 km (30:01.2), then 15.01 km in 90.1 min is 30.01 (30:00.6): both 30:01 at whole seconds.
    say("treadmill", services.log_cardio(user.id, {"exercise": "Treadmill Run", "minutes": 30, "distance_km": 5},
                                         "claude"))
    say("treadmill goal", services.set_goal(user.id, {"kind": "cardio_best", "exercise": "Treadmill Run",
                                                      "distance_km": 5, "target_value": 27,
                                                      "target_date": "2027-01-31"}, "claude"))
    say("treadmill again", services.log_cardio(user.id, {"exercise": "Treadmill Run", "minutes": 29.7,
                                                         "distance_km": 5}, "claude"))
    say("run", services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 60.1, "distance_km": 10.01},
                                   "claude"))
    say("run goal", services.set_goal(user.id, {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5,
                                                "target_value": 27, "target_date": "2027-01-31"}, "claude"))
    say("run again", services.log_cardio(user.id, {"exercise": "Outdoor Run", "minutes": 90.1, "distance_km": 15.01},
                                         "claude"))
    every_read(seen, user.id)
    lines = [g["plan_line"] for g in services.get_goals(user.id)["goals"]]
    assert lines == [
        "88 cm now against the target of 85 cm: 3 cm behind.",
        "90 cm now, at the target.",
        "80.14 kg now against 80.08 kg on the plan line today: 0.06 kg behind.",
        "On the plan line today, at 116.7 kg.",
        "66 bpm now against 64 bpm on the plan line today: 2 bpm behind.",
        "29:42 now against 30:00 on the plan line today: 18 seconds ahead.",
        "30:00.6 now against 30:01.2 on the plan line today: 0.6 seconds ahead."]
    assert widened_lines(user.id) == {lines[2], lines[6]}   # only the two rule 2 sentences are widened
    assert set(lines) <= seen.texts()
    assert assert_plan_lines_part_their_figures(seen) == 5
    seen.assert_clean()


def test_a_bpm_rule_2_sentence_is_widened_alone(make_user, monkeypatch):
    """Resting heart rate 64 against a plan line of 63.5: both print 64 bpm, the gap is real, so the sentence says
    "64.0 bpm". Its trailing zero passes only because that sentence alone is widened."""
    user = make_user(time_zone="UTC")
    seen = Seen()
    monkeypatch.setattr(db, "utcnow", lambda: NOW - timedelta(days=10))
    seen.add("resting", services.log_measurement(user.id, {"kind": "resting_hr", "value": 64}, "claude"))
    seen.add("resting goal", services.set_goal(user.id, {"kind": "measurement", "measurement_kind": "resting_hr",
                                                         "target_value": 62, "target_date": ago(-30)}, "claude"))
    monkeypatch.setattr(db, "utcnow", lambda: NOW)
    every_read(seen, user.id)
    line = "64.0 bpm now against 63.5 bpm on the plan line today: 0.5 bpm behind."
    assert widened_lines(user.id) == {line} and line in seen.texts()
    assert [f.rule for f in tr.check(line, tr.SUMMARY)] == [tr.TRAILING_ZERO]   # unwidened, "64.0" is caught
    seen.assert_clean()


def test_a_dirty_text_is_caught():
    """The checks are live: a summary of today's kind fails them."""
    seen = Seen()
    seen.add("old", {"summary": "2026-11-10: Back Squat 3 × 5 @ 80 kg; New bests: 81.67 kg (was 70 kg).",
                     "note": "Replaced the weigh-in (id 12)."})
    rules = {line.split(" [")[1].split("] ")[1].split(":")[0] for line in seen.findings()}
    assert {"iso_date", "at_or_ampersand", "semicolon", "brackets", "id"} <= rules
