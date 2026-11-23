"""Data on request (docs/contracts/data-on-request.md): what_is_logged and get_data, from the field list.

Rows are written straight through the models, so each test holds exactly the rows it names. Every date is worked from
a fixed today set through db.utcnow (Monday 16 November 2026, 09:00 UTC), never the real date.
"""

import ast
import base64
import math
import dataclasses
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, event, select
from sqlalchemy.exc import IntegrityError

from app import calllog, db, exercises, mcp_schemas, mcp_server, rules, services
from app.mcp_schemas import RESULTS
from app.models import (
    Base, Bodyweight, CallLog, CardioEntry, Exercise, FoodEntry, LiftSession, LiftSet, Measurement, SavedFood,
    UsdaFood,
)
from app.services import common, data as data_service, fields
from app.services.fields import AREAS, Area, Field

from . import text_rules as tr
from .conftest import claims, rpc, sign
from .test_feedback import compact
from .test_mcp import call, failed, ok, tool_list
from .test_trace import OWNER, sign_for

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
TODAY = NOW.date()  # a Monday
SIZE_BAR = 7500
FLOOR = "2024-11-14"  # log.oldest_day of an account made today: 2 years and 2 days back
LIFT_NAMES = [name for name, _ in exercises.LIFTS]
CARDIO_NAMES = [name for name, _, _, _ in exercises.CARDIO]
GET_DATA_DESCRIPTION = (
    "One query over the person's own entries, for a question the other reads do not answer. Name an area, fields, "
    "filters, group_by and totals by the names what_is_logged gives. Spotter works out every total: quote them, never "
    "work out your own. Left out, the range is the last 28 days. Rows are lists under columns. Unlike the other "
    "functions, its numbers are in the person's units, and each column's name says which. When more is given, send "
    "it as cursor with the same query for the next rows.")
WHAT_IS_LOGGED_DESCRIPTION = (
    "What Spotter can hold and what this person has logged: each area get_data reads, with its fields, what each "
    "means, its unit, and the totals, filters and groups it takes, how many entries it holds and when, the names "
    "logged, and every open goal with its plan line. Call it before get_data, for a question the other reads do not "
    "answer. Quote these numbers.")
INPUTS = {
    "area": "An area, as what_is_logged names it, such as lift_sets or food.",
    "fields": "The fields to show, as what_is_logged names them. Leave out for every field. Not with group_by.",
    "filters": "Only rows that match, by a filter field of what_is_logged: a name, a list of names, or true or false, "
               "such as {\"exercise\": \"Back Squat\"}.",
    "from_date": "The first day, YYYY-MM-DD. Leave out with to_date for the last 28 days.",
    "to_date": "The last day, YYYY-MM-DD. Leave out for today.",
    "group_by": "One or two groups: day, week, month, or a field what_is_logged marks as a group. Each row is then one "
                "group.",
    "totals": "By field, as what_is_logged allows: sum, avg, max, min, count, per_day or share, such as "
              "{\"kcal\": [\"sum\", \"per_day\"]}.",
    "order": "A column of the answer to sort by, smallest first, or with a minus sign first for largest first, such as "
             "-kcal_sum. Leave out for newest first, or largest first for groups by a field.",
    "limit": "The most rows: 1 to 300. Leave out for 100.",
    "cursor": "more from the last answer, as it came, with the same query.",
}
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
LIFT_SETS_FIELDS = ("date, session_id, exercise, family, category, region, muscle, set_number, weight_kg, reps, "
                    "time_s, distance_m, estimated_max_kg, volume_kg, sets")


def ago(n: int) -> date:
    return TODAY - timedelta(days=n)


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


@pytest.fixture
def person(make_user):
    return make_user(time_zone="UTC").id


# ---------------------------------------------------------------- writing rows

def _exercise_ids() -> dict[str, int]:
    with db.session_scope() as s:
        return {e.name: e.id for e in s.scalars(select(Exercise))}


def lift(uid: int, day: date, sets: list[tuple], notes: str = "", deleted: bool = False) -> int:
    """A session written straight to the tables. A set is (lift, kg, reps), or (lift, kg, reps, time_s,
    distance_m) for a hold or a carry."""
    ids = _exercise_ids()
    with db.session_scope() as s:
        session = LiftSession(user_id=uid, date=day, notes=notes, dedupe_key="k", logged_by="web",
                              deleted_at=NOW if deleted else None)
        s.add(session)
        s.flush()
        numbers: dict[str, int] = {}
        for name, kg, reps, *more in sets:
            time_s, distance_m = (more + [None, None])[:2]
            numbers[name] = numbers.get(name, 0) + 1
            s.add(LiftSet(session_id=session.id, exercise_id=ids[name], set_number=numbers[name], weight_kg=kg,
                          reps=reps, time_s=time_s, distance_m=distance_m))
        return session.id


def weigh(uid: int, day: date, kg: float, deleted: bool = False) -> int:
    with db.session_scope() as s:
        row = Bodyweight(user_id=uid, date=day, kg=kg, dedupe_key="k", logged_by="web",
                         deleted_at=NOW if deleted else None)
        s.add(row)
        s.flush()
        return row.id


def measure(uid: int, day: date, kind: str, value: float, deleted: bool = False) -> int:
    with db.session_scope() as s:
        row = Measurement(user_id=uid, date=day, kind=kind, value=value, unit=rules.MEASUREMENTS[kind][0],
                          dedupe_key="k", logged_by="web", deleted_at=NOW if deleted else None)
        s.add(row)
        s.flush()
        return row.id


def eat(uid: int, day: date, description: str, kcal: float, meal: str = "lunch", source: str = "you_said",
        saved: int | None = None, servings: float | None = None, usda: int | None = None, grams: float | None = None,
        deleted: bool = False) -> int:
    with db.session_scope() as s:
        row = FoodEntry(user_id=uid, date=day, meal=meal, description=description, kcal=kcal, source=source,
                        estimated=source == "claude_estimate", saved_food_id=saved, servings=servings,
                        usda_fdc_id=usda, grams=grams, dedupe_key="k", logged_by="web",
                        deleted_at=NOW if deleted else None)
        s.add(row)
        s.flush()
        return row.id


def saved_food(uid: int, name: str, kcal: float, label: str = "1 bowl", deleted: bool = False) -> int:
    with db.session_scope() as s:
        row = SavedFood(user_id=uid, name=name, name_key=name.casefold(), serving_label=label, kcal_per_serving=kcal,
                        deleted_at=NOW if deleted else None)
        s.add(row)
        s.flush()
        return row.id


def usda_food(fdc: int, description: str) -> None:
    with db.session_scope() as s:
        s.add(UsdaFood(fdc_id=fdc, description=description, data_type="Foundation", kcal_per_100g=100))


def run(uid: int, day: date, activity: str, minutes: float, km: float | None = None, deleted: bool = False,
        **extra) -> int:
    ids = _exercise_ids()
    with db.session_scope() as s:
        row = CardioEntry(user_id=uid, date=day, exercise_id=ids[activity], minutes=minutes, distance_km=km,
                          dedupe_key="k", logged_by="web", deleted_at=NOW if deleted else None, **extra)
        s.add(row)
        s.flush()
        return row.id


def goal(uid: int, **sent) -> int:
    out = services.set_goal(uid, {"target_date": (TODAY + timedelta(days=60)).isoformat(), **sent}, "web")
    assert "error" not in out, out
    return out["id"]


def data(uid: int, **query) -> dict:
    return services.get_data(uid, query)


def table(answer: dict) -> list[dict]:
    assert "error" not in answer, answer
    return [dict(zip(answer["columns"], row)) for row in answer["rows"]]


def refused(answer: dict) -> list[tuple[str, str, str]]:
    assert "error" in answer, answer
    return [(e["error"], e["message"], e["fix"]) for e in answer["errors"]]


def one_refusal(answer: dict) -> tuple[str, str, str]:
    found = refused(answer)
    assert len(found) == 1, found
    return found[0]


def area_of(logged: dict, name: str) -> dict:
    return next(a for a in logged["areas"] if a["area"] == name)


def field_row(area: dict, name: str) -> list:
    return next(f for f in area["fields"] if f[0] == name)


# ---------------------------------------------------------------- the field list

def untied(metadata: MetaData) -> list[str]:
    """Every way the field list and the tables disagree: a table no area and no OTHER_TABLES row names, a column of
    an area's table no field reads and INTERNAL_COLUMNS does not name, and a named column that does not exist."""
    read = {t for a in AREAS.values() for t in a.tables}
    used = {c for a in AREAS.values() for f in a.fields for c in f.columns}
    problems = []
    for table_ in metadata.sorted_tables:
        if (table_.name in read) == (table_.name in fields.OTHER_TABLES):
            problems.append(f"table {table_.name}")
        if table_.name in read:
            problems += [f"column {table_.name}.{c.name}" for c in table_.c
                         if f"{table_.name}.{c.name}" not in used
                         and c.name not in fields.INTERNAL_COLUMNS.get(table_.name, ())]
    named = [*used, *(f"{t}.{c}" for t, cs in fields.INTERNAL_COLUMNS.items() for c in cs)]
    for name in named:
        t, c = name.split(".")
        if t not in metadata.tables or c not in metadata.tables[t].c:
            problems.append(f"no column {name}")
    problems += [f"no table {t}" for t in fields.OTHER_TABLES if t not in metadata.tables]
    return problems


def test_the_tie_test_every_table_and_every_column_is_read_or_named():
    assert untied(Base.metadata) == []
    # A column added to a person's table fails until it is listed.
    copy = MetaData()
    for t in Base.metadata.sorted_tables:
        t.to_metadata(copy)
    Table("food_entry", copy, Column("zz_new_column", Integer), extend_existing=True)  # protein_g is read since 0015
    assert untied(copy) == ["column food_entry.zz_new_column"]
    assert "lift_set" not in {"lift_set"} - {t for a in AREAS.values() for t in a.tables}  # read, though
    # models.user_tables() misses it: it has no user_id


def test_every_area_has_its_count_field_and_every_field_its_totals_among_the_seven():
    assert list(AREAS) == ["lift_sets", "lift_sessions", "cardio", "weigh_ins", "measurements", "food", "saved_foods",
                           "days"]
    for area in AREAS.values():
        names = [f.name for f in area.fields]
        assert area.count_field in names and len(set(names)) == len(names), area.name
        for f in area.fields:
            assert set(f.totals) <= set(fields.T) and set(f.totals) <= set(fields.TOTALS) - {"count"}, f.name
            assert f.filter == (f.match is not None), f.name
            assert set(f.total_quantity) <= set(fields.TOTALS), f.name
            if f.ratio:
                assert all(fields.field_named(area, n) for n in f.ratio)


def test_the_regions_and_muscles_cover_every_lift_by_the_rule():
    # Since the exercise list (contract exercise-list.md, "Muscles and region"): every lift has muscles, Suitcase
    # Carry too, and every lift in the carry category, the carries and the sleds, is full_body.
    assert set(exercises.REGIONS) == set(exercises.MUSCLES) == set(LIFT_NAMES) and len(LIFT_NAMES) == 242
    assert set(exercises.REGIONS.values()) <= {"upper", "lower", "core", "full_body"}
    regions = {r: sum(1 for v in exercises.REGIONS.values() if v == r) for r in ("lower", "upper", "core", "full_body")}
    assert regions == {"lower": 86, "upper": 129, "core": 22, "full_body": 5}
    carries = {name for name, category in exercises.LIFTS if category == "carry"}
    assert carries == {"Farmer's Carry", "Suitcase Carry", "Sled Push", "Sled Pull", "Sled Drag"}
    for name, worked in exercises.MUSCLES.items():
        assert set(worked) <= set(exercises.MUSCLE_NAMES) and set(worked.values()) <= {1.0, 0.5}, name
        primary = [m for m, w in worked.items() if w == 1]
        assert len(primary) == 1, name
        expected = "full_body" if name in carries else exercises.REGION_OF[primary[0]]
        assert exercises.REGIONS[name] == expected, name
    assert len(exercises.MUSCLE_NAMES) == 17
    assert exercises.MUSCLES["Bench Press"] == {"chest": 1, "shoulders": 0.5, "triceps": 0.5}
    assert exercises.MUSCLES["Deadlift"] == {"lower back": 1, **dict.fromkeys(
        ("calves", "forearms", "glutes", "hamstrings", "lats", "middle back", "quadriceps", "traps"), 0.5)}
    assert exercises.REGIONS["Deadlift"] == "lower" and exercises.REGIONS["Pallof Press"] == "core"
    # Neither is a column, nor in list_exercises.
    assert set(exercises.rows()[0]) == {"key", "name", "kind", "category", "met", "compendium_code"}


def contract_table() -> dict[str, tuple]:
    """The contract's table of the 41 lifts: lift to (entry, primary muscles, secondary muscles, region)."""
    text = (Path(__file__).resolve().parent.parent / "docs" / "contracts" / "data-on-request.md").read_text(
        encoding="utf-8")
    start = text.index("| Lift | free-exercise-db entry |")
    out = {}
    for line in text[start:].splitlines()[2:]:
        if not line.startswith("|"):
            break
        lift_, entry, _, primary, secondary, region = [c.strip() for c in line.strip("|").split("|")]
        muscles = lambda cell: () if cell == "none" else tuple(m.strip() for m in cell.split(","))  # noqa: E731
        out[lift_] = (None if entry == "none" else entry.strip("`"), muscles(primary), muscles(secondary), region)
    return out


def test_the_muscles_regions_and_entries_are_the_contract_s_table_exactly():
    # The table is the record of 2026-11-16: its 40 rows with an entry still hold. Suitcase Carry, none there, takes
    # the farmer's walk entry as a near entry since the exercise list (exercise-list.md, "Muscles and region").
    rows = contract_table()
    assert len(rows) == 41 and set(rows) <= set(LIFT_NAMES)
    assert rows.pop("Suitcase Carry") == (None, (), (), "full_body")
    assert exercises.FREE_EXERCISE_DB["Suitcase Carry"] == "Farmers_Walk"
    assert exercises.BY_NAME["Suitcase Carry"].muscle_source == "fedb_near"
    assert exercises.MUSCLES["Suitcase Carry"] == exercises.MUSCLES["Farmer's Carry"]
    assert exercises.REGIONS["Suitcase Carry"] == "full_body"
    for name, (entry, primary, secondary, region) in rows.items():
        assert exercises.FREE_EXERCISE_DB[name] == entry, name
        assert exercises.MUSCLES[name] == {**dict.fromkeys(primary, 1.0), **dict.fromkeys(secondary, 0.5)}, name
        assert not set(primary) & set(secondary), name
        assert exercises.REGIONS[name] == region, name


def test_data_py_names_no_area_and_no_field_as_a_whole_string_save_the_exempt_two():
    tree = ast.parse(Path(data_service.__file__).read_text(encoding="utf-8"))
    found = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    banned = set(AREAS) | {name for a in AREAS.values() for f in a.fields for name in fields.names_of(f)}
    assert (found & banned) - {"entries", "values"} == set()
    assert "weight_lb" in banned and "volume_lb" in banned and "pace_per_mi" in banned  # either unit's name
    # The words data.py shares with a field live in fields.py.
    assert fields.ROW_KEYS == ("date", "id") and fields.PERIOD_CELLS == ("days", "days_logged")
    assert fields.TIME_GROUPS == ("day", "week", "month")
    assert fields.SUMMARY_AREAS == ("lift_sessions", "cardio", "weigh_ins", "measurements", "food")
    assert fields.FIELD_COLUMNS == ("field", "meaning", "unit", "from", "totals", "filter", "group", "filled")


# ---------------------------------------------------------------- the scaling rule

PARTS = [{"date": ago(1), "id": 1, "part": "run", "seconds": 300, "metres": 1000},
         {"date": ago(1), "id": 2, "part": "row", "seconds": 240, "metres": 1000},
         {"date": ago(2), "id": 3, "part": "run", "seconds": 330, "metres": 1500}]
ZONES = {1: [("legs", 1.0), ("core", 0.5)], 2: [("back", 1.0), ("legs", 0.5)], 3: [("legs", 1.0), ("core", 0.5)]}


def _parts_guard(total: str, query) -> dict | None:
    if total == "sum" and len(query.filters.get("part", ())) != 1:
        return rules.error("invalid_field", "guarded's sum needs one part.", "Filter part to one name.")
    return None


TEST_PARTS = Area(
    "test_parts", "One part of a test format.", ("part", "parts"), "parts", (),
    lambda s, user, start, end: [dict(r) for r in PARTS], (
        Field("date", "The day.", None, None, True, (), False, False, (), lambda r: r["date"]),
        Field("id", "The part's id.", None, None, True, (), False, False, (), lambda r: r["id"]),
        Field("part", "Which part.", None, None, True, (), True, True, (), lambda r: r["part"],
              match=fields.one_of("part", "part", ["run", "row", "ski"])),
        Field("seconds", "Its time in seconds.", "count", None, True, fields.T, False, False, (),
              lambda r: r["seconds"], weighted=True),
        Field("metres", "Its distance.", "count", None, True, ("sum",), False, False, (), lambda r: r["metres"]),
        Field("split", "Seconds a metre.", "rate", None, False, ("avg",), False, False, (),
              lambda r: r["seconds"] / r["metres"], ratio=("seconds", "metres")),
        Field("guarded", "A total with a rule.", "count", None, False, ("sum", "max"), False, False, (),
              lambda r: r["seconds"], guard=_parts_guard),
        Field("zone", "A zone it works.", None, None, False, (), False, True, (),
              lambda r: ZONES[r["id"]][0][0], fan=lambda r: ZONES[r["id"]], default=False),
        Field("parts", "One for each part.", "count", None, False, ("share",), False, False, (), lambda r: 1),
    ))


def test_a_new_area_and_a_new_field_are_entries_only(make_client, person, monkeypatch):
    before = tool_list(make_client(owner_user_id=OWNER), sign_for(OWNER))
    schemas = {name: json.dumps(model.model_json_schema(), sort_keys=True) for name, model in RESULTS.items()}
    food = AREAS["food"]
    tenth = Field("kcal_tenth", "A tenth of the calories.", "count_avg", None, False, ("sum",), False, False, (),
                  lambda r: r["kcal"] / 10)
    monkeypatch.setattr(fields, "AREAS", {**AREAS, "food": dataclasses.replace(food, fields=(*food.fields, tenth)),
                                          "test_parts": TEST_PARTS})
    monkeypatch.setattr(data_service, "AREAS", fields.AREAS)
    eat(person, ago(1), "Dal", 185)
    logged = services.what_is_logged(person)
    parts = area_of(logged, "test_parts")
    assert parts["meaning"] == "One part of a test format." and parts["entries"] == 3
    assert field_row(parts, "split")[:2] == ["split", "Seconds a metre."]
    assert field_row(area_of(logged, "food"), "kcal_tenth")[:2] == ["kcal_tenth", "A tenth of the calories."]
    answer = data(person, area="test_parts", filters={"part": "run"}, group_by=["part"],
                  totals={"seconds": ["sum", "avg", "max", "min", "count", "per_day", "share"], "split": ["avg"],
                          "guarded": ["sum"]})
    assert table(answer) == [{"part": "run", "seconds_sum": 630, "seconds_avg": 315, "seconds_max": 330,
                              "seconds_min": 300, "seconds_count": 2, "seconds_per_day": 315, "seconds_share": 100,
                              "split_avg": 0.25, "guarded_sum": 630}]  # 630 / 2500, not (0.3 + 0.22) / 2
    assert answer["days_logged"] == 2
    assert one_refusal(data(person, area="test_parts", totals={"guarded": ["sum"]})) == (
        "invalid_field", "guarded's sum needs one part.", "Filter part to one name.")
    assert "error" not in data(person, area="test_parts", totals={"guarded": ["max"]})
    zones = data(person, area="test_parts", group_by=["zone"], totals={"seconds": ["sum", "share", "count"]})
    # legs: 300 + 240 x 0.5 + 330 = 750; core: 150 + 165 = 315; back: 240. Of 1305: 57.47, 24.14, 18.39, so the
    # one left goes to legs, the largest remainder.
    assert table(zones) == [{"zone": "legs", "seconds_sum": 750, "seconds_share": 58, "seconds_count": 3},
                            {"zone": "core", "seconds_sum": 315, "seconds_share": 24, "seconds_count": 2},
                            {"zone": "back", "seconds_sum": 240, "seconds_share": 18, "seconds_count": 1}]
    assert zones["totals"] == {"columns": ["seconds_sum", "seconds_count"], "values": [870, 3]}  # each row once
    assert table(data(person, area="food", fields=["kcal"], totals={"kcal_tenth": ["sum"]})) == [{"kcal": 185}]
    assert data(person, area="food", totals={"kcal_tenth": ["sum"]})["totals"]["values"] == [18.5]
    assert tool_list(make_client(owner_user_id=OWNER), sign_for(OWNER)) == before
    assert {name: json.dumps(model.model_json_schema(), sort_keys=True) for name, model in RESULTS.items()} == schemas


# ---------------------------------------------------------------- each area's rows

def full(uid: int) -> dict:
    """One known row of every area, every derived field among them."""
    ids = {"session": lift(uid, ago(2), [("Back Squat", 100, 5), ("Back Squat", 100, 12), ("Pull-Up", 0, 6),
                                         ("Bench Press", 60, 8), ("Suitcase Carry", 30, None, 60, 40)],
                         notes="Good")}
    ids["run"] = run(uid, ago(3), "Outdoor Run", 30, 5, speed_kmh=10, avg_hr=150, machine_kcal=300,
                     estimated_kcal=280, estimate_weight_kg=80.25, notes="Easy")
    ids["bike"] = run(uid, ago(3), "Stationary Bike", 30)
    ids["weigh"] = weigh(uid, ago(1), 80.4)
    ids["waist"] = measure(uid, ago(1), "waist", 84.25)
    ids["roti"] = saved_food(uid, "Roti", 120, "1 roti")
    usda_food(1001, "Rice, white, cooked")
    ids["saved_entry"] = eat(uid, ago(1), "2 rotis", 240, saved=ids["roti"], servings=2, source="saved_food")
    ids["usda_entry"] = eat(uid, ago(1), "rice", 195, meal="dinner", usda=1001, grams=150, source="usda")
    ids["plain"] = eat(uid, ago(1), "Chicken  Biryani", 640, meal="dinner")
    ids["plain_again"] = eat(uid, ago(0), "chicken biryani", 600.4, meal="dinner", source="claude_estimate")
    return ids


def test_each_area_s_rows_carry_their_stored_and_derived_cells(person):
    ids = full(person)
    sets = table(data(person, area="lift_sets"))
    assert sets[-1] == {"date": ago(2).isoformat(), "session_id": ids["session"], "exercise": "Back Squat",
                        "category": "squat", "region": "lower", "set_number": 1, "weight_kg": 100, "reps": 5,
                        "time_s": None, "distance_m": None, "estimated_max_kg": 116.7, "volume_kg": 500}
    by_reps = {(r["exercise"], r["reps"]): r for r in sets}
    assert by_reps[("Back Squat", 12)]["estimated_max_kg"] is None  # outside 1 to 10 reps
    assert by_reps[("Back Squat", 12)]["set_number"] == 2
    assert by_reps[("Pull-Up", 6)]["estimated_max_kg"] is None and by_reps[("Pull-Up", 6)]["volume_kg"] == 0
    carry = by_reps[("Suitcase Carry", None)]  # a set of distance and time: no reps, no volume
    assert carry["region"] == "full_body" and (carry["time_s"], carry["distance_m"]) == (60, 40)
    assert carry["volume_kg"] is None and carry["estimated_max_kg"] is None
    assert table(data(person, area="lift_sessions")) == [{
        "date": ago(2).isoformat(), "id": ids["session"],
        "exercises": "Back Squat, Pull-Up, Bench Press, Suitcase Carry",
        "sets": 5, "volume_kg": 2180, "notes": "Good"}]  # 500 + 1,200 + 0 + 480; the carry has none
    cardio = {r["activity"]: r for r in table(data(person, area="cardio"))}
    assert cardio["Outdoor Run"] == {"date": ago(3).isoformat(), "id": ids["run"], "activity": "Outdoor Run",
                                     "category": "run", "minutes": 30, "distance_km": 5, "pace_per_km": "6:00",
                                     "speed_kmh": 10, "incline_pct": None, "avg_hr": 150, "machine_kcal": 300,
                                     "estimated_kcal": 280, "estimate_weight_kg": 80.3, "notes": "Easy"}
    assert cardio["Stationary Bike"]["pace_per_km"] is None and cardio["Stationary Bike"]["distance_km"] is None
    assert table(data(person, area="weigh_ins")) == [{"date": ago(1).isoformat(), "id": ids["weigh"],
                                                      "weight_kg": 80.4}]
    assert table(data(person, area="measurements")) == [{"date": ago(1).isoformat(), "id": ids["waist"],
                                                         "kind": "waist", "value": 84.3, "unit": "cm"}]
    food = {r["id"]: r for r in table(data(person, area="food"))}
    assert food[ids["saved_entry"]] == {"date": ago(1).isoformat(), "id": ids["saved_entry"], "meal": "lunch",
                                        "description": "2 rotis", "food": "Roti", "kcal": 240, "source": "saved_food",
                                        "estimated": False, "saved_food_id": ids["roti"], "servings": 2,
                                        "grams": None, "usda_fdc_id": None, "protein_g": None, "carbs_g": None,
                                        "fat_g": None, "macros_estimated": None, "portion": None}
    assert food[ids["usda_entry"]]["food"] == "Rice, white, cooked" and food[ids["usda_entry"]]["grams"] == 150
    assert food[ids["plain"]]["food"] == "Chicken  Biryani" and food[ids["plain_again"]]["kcal"] == 600
    assert food[ids["plain_again"]]["estimated"] is True
    by_food = table(data(person, area="food", group_by=["food"], totals={"kcal": ["sum"]}))
    # The two spellings are one group, shown in the latest entry's words.
    assert by_food[0] == {"food": "chicken biryani", "kcal_sum": 1240}
    assert [r["food"] for r in by_food] == ["chicken biryani", "Roti", "Rice, white, cooked"]
    assert table(data(person, area="saved_foods")) == [{"id": ids["roti"], "name": "Roti", "serving_label": "1 roti",
                                                        "kcal_per_serving": 120, "grams_per_serving": None,
                                                        "protein_g_per_serving": None, "carbs_g_per_serving": None,
                                                        "fat_g_per_serving": None,
                                                        "times_logged": 1, "last_logged": ago(1).isoformat()}]
    days = table(data(person, area="days", from_date=ago(3).isoformat()))
    assert days == [
        {"date": ago(0).isoformat(), "lift_sessions": 0, "lift_sets": 0, "volume_kg": 0, "cardio_entries": 0,
         "cardio_minutes": 0, "food_entries": 1, "kcal": 600, "weight_kg": None, "measurements": 0},
        {"date": ago(1).isoformat(), "lift_sessions": 0, "lift_sets": 0, "volume_kg": 0, "cardio_entries": 0,
         "cardio_minutes": 0, "food_entries": 3, "kcal": 1075, "weight_kg": 80.4, "measurements": 1},
        {"date": ago(2).isoformat(), "lift_sessions": 1, "lift_sets": 5, "volume_kg": 2180, "cardio_entries": 0,
         "cardio_minutes": 0, "food_entries": 0, "kcal": None, "weight_kg": None, "measurements": 0},
        {"date": ago(3).isoformat(), "lift_sessions": 0, "lift_sets": 0, "volume_kg": 0, "cardio_entries": 2,
         "cardio_minutes": 60, "food_entries": 0, "kcal": None, "weight_kg": None, "measurements": 0}]


def test_an_entry_dated_after_today_counts_as_today_s(person):
    eat(person, TODAY + timedelta(days=1), "Late dal", 200)
    answer = data(person, area="food", group_by=["day"], from_date=ago(1).isoformat())
    assert table(answer) == [{"day": TODAY.isoformat(), "entries_count": 1}, {"day": ago(1).isoformat(),
                                                                                 "entries_count": 0}]
    assert data(person, area="food")["rows"][0][0] == (TODAY + timedelta(days=1)).isoformat()  # its own date shown


# ---------------------------------------------------------------- deleted entries and never logged

def test_deleted_entries_are_left_out_of_every_row_total_count_filled_and_entries(person):
    lift(person, ago(2), [("Back Squat", 100, 5)], deleted=True)
    live = lift(person, ago(1), [("Bench Press", 60, 5)])
    weigh(person, ago(1), 90, deleted=True)  # replaced
    weigh(person, ago(1), 80)
    eat(person, ago(1), "Gone", 999, deleted=True)
    eat(person, ago(1), "Dal", 180)
    saved_food(person, "Old", 50, deleted=True)
    saved_food(person, "New", 60)
    assert [r["session_id"] for r in table(data(person, area="lift_sets"))] == [live]
    assert data(person, area="lift_sets", totals={"sets": ["count"], "weight_kg": ["max"]})["totals"]["values"] == [1,
                                                                                                                 60]
    assert table(data(person, area="weigh_ins", totals={"weight_kg": ["max"]})) == [
        {"date": ago(1).isoformat(), "id": table(data(person, area="weigh_ins"))[0]["id"], "weight_kg": 80}]
    assert data(person, area="food", totals={"kcal": ["sum", "count"]})["totals"]["values"] == [180, 1]
    assert [r["name"] for r in table(data(person, area="saved_foods"))] == ["New"]
    logged = services.what_is_logged(person)
    counts = {a["area"]: a["entries"] for a in logged["areas"]}
    assert counts == {"lift_sets": 1, "lift_sessions": 1, "cardio": 0, "weigh_ins": 1, "measurements": 0, "food": 1,
                      "saved_foods": 1, "days": 1}
    assert field_row(area_of(logged, "food"), "kcal")[-1] == 1
    assert area_of(logged, "lift_sets")["names"] == {"exercise": ["Bench Press"], "family": ["Bench Press"],
                                                     "category": ["push"], "region": ["upper"],
                                                     "muscle": ["chest", "shoulders", "triceps"]}


def test_never_logged_gives_empty_rows_and_the_note(person):
    answer = data(person, area="measurements")
    assert answer["rows"] == [] and answer["note"] == "Nothing is logged in measurements yet."
    assert answer["summary"] == "No measurements from 20 Oct to 16 Nov."
    lift(person, ago(100), [("Back Squat", 100, 5)])
    earlier = data(person, area="lift_sets")
    assert earlier["rows"] == [] and earlier["note"] == "Nothing in lift_sets from 20 Oct to 16 Nov. The last one is " \
                                                        "on Sat 8 Aug."
    measure(person, ago(1), "waist", 84)
    sleep = data(person, area="measurements", filters={"kind": "sleep_hours"})
    assert sleep["rows"] == [] and sleep["note"] == "Nothing in measurements matches these filters yet."
    # A field never filled: null in every row, its totals null and its count 0, filled 0.
    run(person, ago(1), "Outdoor Walk", 30)
    walk = data(person, area="cardio", fields=["incline_pct"], totals={"incline_pct": ["avg", "max", "count"]})
    assert walk["rows"] == [[None]] and walk["totals"]["values"] == [None, None, 0]
    assert field_row(area_of(services.what_is_logged(person), "cardio"), "incline_pct")[-1] == 0


# ---------------------------------------------------------------- filters

def test_each_kind_of_filter_and_a_list_of_names(person):
    full(person)
    lift(person, ago(4), [("Romanian Deadlift", 80, 8)])
    names = lambda answer: sorted({r["exercise"] for r in table(answer)})  # noqa: E731
    assert names(data(person, area="lift_sets", filters={"exercise": " back squat "})) == ["Back Squat"]
    assert names(data(person, area="lift_sets", filters={"exercise": ["Bench Press", "Pull-Up"]})) == [
        "Bench Press", "Pull-Up"]
    assert names(data(person, area="lift_sets", filters={"category": "Hinge"})) == ["Romanian Deadlift"]
    assert names(data(person, area="lift_sets", filters={"region": "upper"})) == ["Bench Press", "Pull-Up"]
    assert names(data(person, area="lift_sets", filters={"muscle": "Triceps"})) == ["Bench Press"]  # a secondary
    assert names(data(person, area="lift_sets", filters={"muscle": "lower back"})) == [
        "Back Squat", "Romanian Deadlift", "Suitcase Carry"]  # Suitcase Carry's farmer's walk muscles
    assert [r["activity"] for r in table(data(person, area="cardio", filters={"activity": "outdoor run"}))] == [
        "Outdoor Run"]
    assert [r["activity"] for r in table(data(person, area="cardio", filters={"category": "cycle"}))] == [
        "Stationary Bike"]
    assert [r["kind"] for r in table(data(person, area="measurements", filters={"kind": "Waist"}))] == ["waist"]
    assert {r["meal"] for r in table(data(person, area="food", filters={"meal": " Dinner "}))} == {"dinner"}
    assert [r["source"] for r in table(data(person, area="food", filters={"source": "usda"}))] == ["usda"]
    assert [r["kcal"] for r in table(data(person, area="food", filters={"estimated": True}))] == [600]
    assert len(table(data(person, area="food", filters={"estimated": False}))) == 3
    assert len(table(data(person, area="food", filters={"meal": None, "source": "  "}))) == 4  # left out


def test_the_exercises_filter_keeps_whole_sessions(person):
    sid = lift(person, ago(1), [("Back Squat", 100, 5), ("Bench Press", 60, 5)])
    lift(person, ago(2), [("Deadlift", 140, 5)])
    assert table(data(person, area="lift_sessions", filters={"exercises": "Bench Press"})) == [
        {"date": ago(1).isoformat(), "id": sid, "exercises": "Back Squat, Bench Press", "sets": 2, "volume_kg": 800,
         "notes": ""}]


ERRORS = [
    ({"area": "lifts"}, ("invalid_field", "area 'lifts' is not an area Spotter reads.",
                         "Use one of: lift_sets, lift_sessions, cardio, weigh_ins, measurements, food, saved_foods, "
                         "days. The nearest is lift_sets.")),
    ({"area": "   "}, ("missing_field", "area is missing.", "Give area, as what_is_logged names it.")),
    ({"area": "lift_sets", "fields": []}, ("invalid_field", "fields holds no fields.",
                                           "Name at least one field, or leave fields out for every field.")),
    ({"area": "lift_sets", "fields": ["weight"]}, ("invalid_field", "'weight' is not a field of lift_sets.",
                                                   f"lift_sets takes: {LIFT_SETS_FIELDS}. The nearest field is "
                                                   "weight_kg.")),
    ({"area": "lift_sets", "totals": {"weight": ["max"]}}, ("invalid_field", "'weight' is not a field of lift_sets.",
                                                            f"lift_sets takes: {LIFT_SETS_FIELDS}. The nearest field "
                                                            "is weight_kg.")),
    ({"area": "lift_sets", "fields": ["reps"], "group_by": ["week"]}, (
        "invalid_field", "fields and group_by do not go together: with group_by each row is a group.",
        "Leave fields out with group_by, and name in totals what to work out for each group.")),
    ({"area": "lift_sets", "filters": {"colour": "red"}}, (
        "invalid_field", "'colour' is not a filter of lift_sets.", "lift_sets filters on: exercise, family, category, "
                                                                   "region, "
                                                                   "muscle.")),
    ({"area": "weigh_ins", "filters": {"kind": "waist"}}, ("invalid_field", "weigh_ins takes no filters.",
                                                           "Leave filters out for weigh_ins.")),
    ({"area": "lift_sets", "filters": {"exercise": []}}, (
        "invalid_field", "filters.exercise holds no names.",
        "Send one name or a list of names, or leave the filter out.")),
    ({"area": "lift_sets", "filters": {"exercise": 5}}, (
        "invalid_field", "filters.exercise takes a name or a list of names, not 5.",
        "Send one name or a list of names, or leave the filter out.")),
    ({"area": "food", "filters": {"estimated": "yes"}}, ("invalid_field", "filters.estimated takes true or false, not "
                                                                          "'yes'.",
                                                         "Send estimated as true or false, or leave it out.")),
    ({"area": "food", "totals": {"kcal": "sum"}}, ("invalid_field", "totals.kcal takes a list of totals, not 'sum'.",
                                                   "Send totals.kcal as a list, such as [\"sum\"].")),
    ({"area": "food", "filters": {"meal": "brunch"}}, ("invalid_field", "filters: meal 'brunch' is not a meal.",
                                                       "Use one of: breakfast, lunch, dinner, snack.")),
    ({"area": "lift_sets", "filters": {"region": "legs"}}, ("invalid_field", "region 'legs' is not a region.",
                                                            "Use one of: upper, lower, core, full_body.")),
    ({"area": "lift_sets", "filters": {"muscle": "pecs"}}, (
        "invalid_field", "muscle 'pecs' is not a muscle.",
        "Use one of: abdominals, abductors, adductors, biceps, calves, chest, forearms, glutes, hamstrings, lats, "
        "lower back, middle back, neck, quadriceps, shoulders, traps, triceps.")),
    ({"area": "lift_sets", "filters": {"category": "legs"}}, (
        "invalid_field", "category 'legs' is not a category.",
        "Use one of: squat, hinge, push, pull, carry, core, accessory.")),
    ({"area": "food", "filters": {"source": "me"}}, ("invalid_field", "source 'me' is not a source.",
                                                     "Use one of: you_said, saved_food, usda, claude_estimate.")),
    ({"area": "lift_sets", "group_by": ["colour"]}, (
        "invalid_field", "'colour' is not a group of lift_sets.",
        "lift_sets groups by: day, week, month, exercise, family, category, region, muscle.")),
    ({"area": "lift_sets", "group_by": ["week", "month"]}, (
        "invalid_field", "group_by holds week and month: one of day, week and month at most.",
        "Keep one of day, week and month in group_by.")),
    ({"area": "lift_sets", "group_by": ["exercise", "exercise"]}, ("invalid_field", "group_by names exercise twice.",
                                                                   "Name each group once.")),
    ({"area": "saved_foods", "group_by": ["week"]}, ("invalid_field", "saved_foods takes no group_by.",
                                                     "Leave group_by out for saved_foods.")),
    ({"area": "food", "totals": {"kcal": ["mean"]}}, ("invalid_field", "'mean' is not a total.",
                                                      "Use: sum, avg, max, min, count, per_day, share.")),
    ({"area": "lift_sets", "totals": {"weight_kg": ["sum"]}}, ("invalid_field", "weight_kg takes no sum.",
                                                               "weight_kg takes: count, avg, max, min.")),
    ({"area": "food", "totals": {"kcal": ["share"]}}, (
        "invalid_field", "share needs group_by: it is a group's part of the range.",
        "Send group_by with share, or leave share out.")),
    ({"area": "food", "totals": {"kcal": []}}, ("invalid_field", "totals.kcal holds no totals.",
                                                "Name at least one total for kcal, or leave it out.")),
    ({"area": "measurements", "totals": {"value": ["avg"]}}, (
        "invalid_field", "value holds more than one kind, so its totals mix units.",
        "Filter on one kind, such as {\"kind\": \"waist\"}, or send group_by kind.")),
    ({"area": "measurements", "filters": {"kind": "waist"}, "totals": {"value": ["sum"]}}, (
        "invalid_field", "waist takes no sum: it is not a day's amount.",
        "Use avg, max or min for waist. sum and per_day are for steps, sleep_hours and water_ml.")),
    ({"area": "measurements", "group_by": ["kind"], "totals": {"value": ["sum"]}}, (
        "invalid_field", "value's sum needs a kind filter of steps, sleep_hours or water_ml: grouped by kind, it could "
                         "take in a kind that is not a day's amount.",
        "Filter kind to steps, sleep_hours or water_ml, or use avg, max or min.")),
    ({"area": "measurements", "group_by": ["kind"], "filters": {"kind": ["steps", "waist"]},
      "totals": {"value": ["per_day"]}}, (
        "invalid_field", "value's per_day needs a kind filter of steps, sleep_hours or water_ml: grouped by kind, it "
                         "could take in a kind that is not a day's amount.",
        "Filter kind to steps, sleep_hours or water_ml, or use avg, max or min.")),
    ({"area": "food", "group_by": ["meal"], "order": "kcal"}, (
        "invalid_field", "order 'kcal' is not a column of this answer.",
        "Order by one of: meal, entries_count, or with a minus sign first, such as -entries_count.")),
    ({"area": "food", "from_date": "20261116"}, ("invalid_date", "from_date '20261116' is not a date.",
                                                 "Give from_date as YYYY-MM-DD, or leave it out.")),
    ({"area": "food", "to_date": "2026-12-01"}, ("date_in_future", "to_date 2026-12-01 is after today (2026-11-16).",
                                                 "Give to_date as today or earlier, or leave it out for today.")),
    ({"area": "food", "from_date": "2026-11-10", "to_date": "2026-11-01"}, (
        "invalid_field", "from_date 2026-11-10 is after to_date 2026-11-01.", "Give from_date on or before to_date.")),
    ({"area": "food", "from_date": "2020-01-01"}, (
        "date_too_old", f"from_date 2020-01-01 is before {FLOOR}, the oldest day this account can hold.",
        f"Give a date from {FLOOR} to today, or leave from_date out.")),
    ({"area": "food", "cursor": "nope"}, (
        "invalid_field", "cursor is not one Spotter gave.",
        "Send more from the last get_data answer as it came, or leave cursor out to start from the first rows.")),
]


@pytest.mark.parametrize("sent, expected", ERRORS, ids=[str(i) for i in range(len(ERRORS))])
def test_every_refusal_of_the_table_word_for_word(person, sent, expected):
    assert one_refusal(services.get_data(person, sent)) == expected


def test_the_name_refusals_are_their_own_functions_words(person):
    err = services.get_data(person, {"area": "cardio", "filters": {"activity": "Basketball"}})
    assert err["errors"] == [rules.unknown_exercise("Basketball", CARDIO_NAMES, "filters.activity: ")]
    assert err["message"] == "filters.activity: 'Basketball' is not on the exercise list."
    err = services.get_data(person, {"area": "lift_sessions", "filters": {"exercises": ["Bench Press", "Benchh"]}})
    assert err["errors"] == [rules.unknown_exercise("Benchh", LIFT_NAMES, "filters.exercises: ")]
    err = services.get_data(person, {"area": "measurements", "filters": {"kind": "wasit"}})
    assert err["errors"] == [rules.match_kind("wasit")[1]] and err["error"] == "invalid_kind"


def test_every_problem_comes_back_at_once(person):
    answer = services.get_data(person, {"area": "lift_sets", "fields": ["weight"], "filters": {"region": "legs"},
                                        "group_by": ["colour"], "from_date": "soon"})
    assert answer["message"].startswith("5 problems. ") and [e["error"] for e in answer["errors"]] == [
        "invalid_date", "invalid_field", "invalid_field", "invalid_field", "invalid_field"]
    assert answer["errors"] == rules.combine(answer["errors"])["errors"]


def test_a_refused_call_reads_no_row(person):
    full(person)
    statements = []
    engine = db.get_engine()

    def count(*args):
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", count)
    try:
        services.get_data(person, {"area": "food", "group_by": ["colour"]})
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert all("food_entry" not in sql for sql in statements) and len(statements) == 1  # the user row alone


# ---------------------------------------------------------------- totals

def test_per_day_keeps_its_divisor_under_a_meal_filter(person):
    eat(person, ago(1), "Dal", 300, meal="lunch")
    eat(person, ago(1), "Rice", 500, meal="dinner")
    eat(person, ago(2), "Roti", 600, meal="dinner")
    eat(person, ago(3), "Oats", 200, meal="breakfast")
    answer = data(person, area="food", filters={"meal": "lunch"}, totals={"kcal": ["sum", "per_day"]})
    assert answer["days_logged"] == 3 and answer["totals"]["values"] == [300, 100]


def test_shares_are_whole_by_largest_remainder_with_the_tie_rule(person):
    for meal in ("breakfast", "lunch", "dinner"):
        eat(person, ago(1), "Same", 100, meal=meal)
    three = data(person, area="food", group_by=["meal"], totals={"kcal": ["share"]})
    assert table(three) == [{"meal": "breakfast", "kcal_share": 34}, {"meal": "dinner", "kcal_share": 33},
                            {"meal": "lunch", "kcal_share": 33}]  # 33 each alone is 99: the earlier group takes it
    assert "totals" not in three  # share alone: no range totals
    eat(person, ago(1), "More", 200, meal="lunch")  # lunch 300, breakfast 100, dinner 100: 60, 20, 20
    eat(person, ago(1), "Snack", 300, meal="snack")  # 37.5, 37.5, 12.5, 12.5: 98 whole, two left
    uneven = table(data(person, area="food", group_by=["meal"], totals={"kcal": ["sum", "share"]}))
    assert [(r["meal"], r["kcal_sum"], r["kcal_share"]) for r in uneven] == [
        ("lunch", 300, 38), ("snack", 300, 38), ("breakfast", 100, 12), ("dinner", 100, 12)]  # the larger values
    assert sum(r["kcal_share"] for r in uneven) == 100
    assert fields_shares([1, 4, 3]) == [12, 50, 38]  # a tie in remainder goes to the larger value
    page = data(person, area="food", group_by=["meal"], totals={"kcal": ["share"]}, limit=2)
    assert [r["kcal_share"] for r in table(page)] == [38, 38] and "more" in page  # a page need not sum to 100


def fields_shares(values: list[int]) -> list[int]:
    from fractions import Fraction  # noqa: PLC0415
    return data_service._shares([Fraction(v) for v in values])


def test_a_pace_s_avg_is_total_minutes_over_total_distance_and_sorts_by_its_minutes(person):
    run(person, ago(1), "Outdoor Run", 30, 5)    # 6:00
    run(person, ago(2), "Outdoor Run", 40, 10)   # 4:00
    run(person, ago(3), "Outdoor Run", 100, 10)  # 10:00
    answer = data(person, area="cardio", totals={"pace_per_km": ["avg", "max", "min"]})
    assert answer["totals"]["values"] == ["6:48", "10:00", "4:00"]  # 170 / 25 = 6.8 minutes, not the paces' 6:40
    ordered = data(person, area="cardio", fields=["pace_per_km"], order="pace_per_km")
    assert ordered["rows"] == [["4:00"], ["6:00"], ["10:00"]]  # by minutes, never as text
    assert data(person, area="cardio", fields=["pace_per_km"], order="-pace_per_km")["rows"][0] == ["10:00"]


def test_value_totals_take_one_kind(person):
    for n, value in enumerate((84, 83.5, 83)):
        measure(person, ago(n + 1), "waist", value)
    measure(person, ago(1), "steps", 9000)
    waist = data(person, area="measurements", filters={"kind": "waist"}, totals={"value": ["avg", "max", "count"]})
    assert waist["totals"]["values"] == [83.5, 84, 3]
    by_kind = data(person, area="measurements", group_by=["kind"], totals={"value": ["avg", "count"]})
    assert table(by_kind) == [{"kind": "steps", "value_avg": 9000, "value_count": 1},
                              {"kind": "waist", "value_avg": 83.5, "value_count": 3}]  # largest first
    assert "totals" in by_kind and by_kind["totals"]["columns"] == ["value_avg", "value_count"]
    counted = data(person, area="measurements", totals={"value": ["count"]})  # a count is never guarded
    assert counted["totals"]["values"] == [4]


def test_per_day_of_a_measurement_divides_by_the_days_with_that_kind(person):
    for n in range(20):
        if n % 2:
            measure(person, ago(n), "steps", 8000 + 100 * n)
        else:
            measure(person, ago(n), "waist", 84)
    answer = data(person, area="measurements", filters={"kind": "steps"}, totals={"value": ["sum", "per_day"]})
    assert answer["days_logged"] == 20
    total = sum(8000 + 100 * n for n in range(20) if n % 2)
    assert answer["totals"]["values"] == [total, total // 10]  # 10 days with steps, not 20


# ---------------------------------------------------------------- groups

def test_grouped_by_muscle_a_set_counts_in_each_muscle_at_its_weight(person):
    # Suitcase Carry has muscles since the exercise list (contract exercise-list.md, "Muscles and region"): the
    # farmer's walk entry's, forearms 1 and six secondaries at 0.5. Its set is of distance and time (note (j)), so it
    # counts in sets and has no volume.
    lift(person, ago(1), [("Bench Press", 60, 8), ("Suitcase Carry", 40, None, 50, 30)])
    answer = data(person, area="lift_sets", group_by=["muscle"],
                  totals={"sets": ["sum", "count", "share"], "volume_kg": ["sum"]})
    carry = lambda muscle, share: {  # noqa: E731
        "muscle": muscle, "sets_sum": 0.5, "sets_count": 1, "sets_share": share, "volume_kg_sum": None}
    assert table(answer) == [
        {"muscle": "chest", "sets_sum": 1, "sets_count": 1, "sets_share": 17, "volume_kg_sum": 480},
        {"muscle": "forearms", "sets_sum": 1, "sets_count": 1, "sets_share": 17, "volume_kg_sum": None},
        carry("abdominals", 9), carry("glutes", 9), carry("hamstrings", 8), carry("lower back", 8),
        carry("quadriceps", 8),
        {"muscle": "shoulders", "sets_sum": 0.5, "sets_count": 1, "sets_share": 8, "volume_kg_sum": 240},
        carry("traps", 8),
        {"muscle": "triceps", "sets_sum": 0.5, "sets_count": 1, "sets_share": 8, "volume_kg_sum": 240}]
    assert answer["totals"] == {"columns": ["sets_sum", "sets_count", "volume_kg_sum"], "values": [2, 2, 480]}
    only = data(person, area="lift_sets", filters={"muscle": "triceps"}, group_by=["muscle"],
                totals={"sets": ["sum"]})
    assert table(only) == [{"muscle": "triceps", "sets_sum": 0.5}]
    assert table(data(person, area="lift_sets", fields=["exercise", "muscle"])) == [
        {"exercise": "Suitcase Carry", "muscle": "forearms"}, {"exercise": "Bench Press", "muscle": "chest"}]
    assert "muscle" not in data(person, area="lift_sets")["columns"]
    lift(person, ago(2), [("Dip", 0, 10), ("Dip", 0, 10)])
    names = area_of(services.what_is_logged(person), "lift_sets")["names"]["muscle"]
    assert names == ["triceps", "chest", "shoulders", "forearms", "abdominals", "glutes", "hamstrings", "lower back",
                     "quadriceps", "traps"]  # 2.5, 2, 2, 1, then 0.5 each, ties by name


def test_grouped_by_category_push_against_pull(person):
    lift(person, ago(1), [("Bench Press", 60, 8), ("Bench Press", 60, 8), ("Pull-Up", 0, 6)])
    assert table(data(person, area="lift_sets", group_by=["category"], totals={"sets": ["count", "share"]})) == [
        {"category": "push", "sets_count": 2, "sets_share": 67}, {"category": "pull", "sets_count": 1,
                                                                   "sets_share": 33}]


def test_a_week_group_over_a_range_from_a_wednesday_and_its_empty_weeks(person):
    eat(person, date(2026, 11, 5), "Dal", 200)
    answer = data(person, area="food", group_by=["week"], from_date="2026-11-04", totals={"kcal": ["sum"]})
    assert answer["columns"] == ["week", "days", "days_logged", "kcal_sum"]
    assert table(answer) == [{"week": "2026-11-16", "days": 1, "days_logged": 0, "kcal_sum": None},
                             {"week": "2026-11-09", "days": 7, "days_logged": 0, "kcal_sum": None},
                             {"week": "2026-11-02", "days": 5, "days_logged": 1, "kcal_sum": 200}]
    counted = table(data(person, area="food", group_by=["week"], from_date="2026-11-04"))
    assert [r["entries_count"] for r in counted] == [0, 0, 1]
    months = table(data(person, area="food", group_by=["month"], from_date="2026-10-30", to_date="2026-11-05"))
    assert months == [{"month": "2026-11", "days": 5, "days_logged": 1, "entries_count": 1},
                      {"month": "2026-10", "days": 2, "days_logged": 0, "entries_count": 0}]
    days = data(person, area="food", group_by=["day"], from_date="2026-11-04", to_date="2026-11-05")
    assert days["columns"] == ["day", "entries_count"] and days["rows"] == [["2026-11-05", 1], ["2026-11-04", 0]]


def test_two_groups_their_default_order_and_a_sent_order(person):
    eat(person, ago(0), "Oats", 300, meal="breakfast")
    eat(person, ago(1), "Dal", 200, meal="lunch")
    eat(person, ago(1), "Rice", 300, meal="lunch")
    eat(person, ago(1), "Roti", 100, meal="dinner")
    answer = data(person, area="food", group_by=["day", "meal"], from_date=ago(2).isoformat(),
                  totals={"kcal": ["sum"]})
    assert answer["rows"] == [[ago(0).isoformat(), "breakfast", 300], [ago(1).isoformat(), "lunch", 500],
                              [ago(1).isoformat(), "dinner", 100], [ago(2).isoformat(), None, None]]
    meal_first = data(person, area="food", group_by=["meal", "day"], from_date=ago(1).isoformat(),
                      totals={"kcal": ["sum"]})
    assert meal_first["rows"] == [["lunch", ago(0).isoformat(), None], ["lunch", ago(1).isoformat(), 500],
                                  ["breakfast", ago(0).isoformat(), 300], ["breakfast", ago(1).isoformat(), None],
                                  ["dinner", ago(0).isoformat(), None], ["dinner", ago(1).isoformat(), 100]]
    ordered = data(person, area="food", group_by=["day", "meal"], from_date=ago(2).isoformat(),
                   totals={"kcal": ["sum"]}, order="kcal_sum")
    assert [row[2] for row in ordered["rows"]] == [100, 300, 500, None]  # smallest first, nulls last


def test_the_column_names_of_a_time_key_a_field_key_and_the_period_cells(person):
    eat(person, ago(1), "Dal", 200)
    assert data(person, area="food", group_by=["month", "meal"])["columns"] == ["month", "meal", "days", "days_logged",
                                                                             "entries_count"]
    assert data(person, area="lift_sets", group_by=["exercise"])["columns"] == ["exercise", "sets_count"]


def test_saved_foods_most_used_first_then_by_name(person):
    a = saved_food(person, "Banana", 100)
    b = saved_food(person, "Apple", 80)
    c = saved_food(person, "Curd", 90)
    eat(person, ago(1), "Curd", 90, saved=c, servings=1, source="saved_food")
    rows = table(data(person, area="saved_foods", fields=["name", "times_logged"]))
    assert rows == [{"name": "Curd", "times_logged": 1}, {"name": "Apple", "times_logged": 0},
                    {"name": "Banana", "times_logged": 0}]
    assert a and b


# ---------------------------------------------------------------- units

def test_a_lb_and_mi_account_reads_its_own_units(make_user):
    uid = make_user(time_zone="UTC", weight_unit="lb", distance_unit="mi").id
    lift(uid, ago(1), [("Bench Press", 97.5, 5), ("Bench Press", 100, 5)])
    run(uid, ago(1), "Outdoor Run", 30, 5, speed_kmh=10, estimate_weight_kg=80)
    weigh(uid, ago(1), 80)
    sets = data(uid, area="lift_sets")
    assert sets["columns"] == ["date", "session_id", "exercise", "category", "region", "set_number", "weight_lb",
                               "reps", "time_s", "distance_m", "estimated_max_lb", "volume_lb"]
    assert table(sets)[-1]["weight_lb"] == 214.95 and table(sets)[0]["volume_lb"] == 1102.3
    cardio = table(data(uid, area="cardio", fields=["distance_mi", "pace_per_km", "speed_kmh", "estimate_weight_kg"]))
    assert cardio == [{"distance_mi": 3.11, "pace_per_mi": "9:39", "speed_mph": 6.2, "estimate_weight_lb": 176.4}]
    by_both = data(uid, area="lift_sets", fields=["weight_kg", "weight_lb"], totals={"weight_kg": ["max"],
                                                                                    "weight_lb": ["min"]},
                   order="-weight_kg")
    assert by_both["columns"] == ["weight_lb", "weight_lb"] and by_both["rows"][0] == [220.45, 220.45]
    assert by_both["totals"] == {"columns": ["weight_lb_max", "weight_lb_min"], "values": [220.45, 214.95]}
    assert data(uid, area="weigh_ins")["rows"][0][-1] == 176.35
    logged = services.what_is_logged(uid)
    assert field_row(area_of(logged, "cardio"), "pace_per_mi")[2] == "m:ss per mi"
    assert field_row(area_of(logged, "lift_sets"), "weight_lb")[2] == "lb"


def test_a_cell_is_never_a_whole_float(person):
    full(person)
    for area in AREAS:
        answer = data(person, area=area)
        cells = [c for row in answer["rows"] for c in row]
        assert not [c for c in cells if isinstance(c, float) and c.is_integer()], area


# ---------------------------------------------------------------- the cursor and the ceiling

def test_the_cursor_pages_to_the_end_with_no_row_twice_or_missed(person, monkeypatch):
    with db.session_scope() as s:
        s.add_all(FoodEntry(user_id=person, date=ago(n % 28), meal="lunch", description=f"Food {n}", kcal=n,
                            source="you_said", estimated=False, dedupe_key="k", logged_by="web") for n in range(250))
    seen, cursor, pages = [], None, 0
    while True:
        answer = data(person, area="food", fields=["id"], **({"cursor": cursor} if cursor else {}))
        seen += [row[0] for row in answer["rows"]]
        pages += 1
        cursor = answer.get("more")
        if not cursor:
            break
    assert pages == 3 and len(seen) == len(set(seen)) == 250
    assert answer["summary"] == "250 entries from 20 Oct to 16 Nov, rows 201 to 250 here."
    first = data(person, area="food", fields=["id"])
    assert first["summary"] == "250 entries from 20 Oct to 16 Nov, rows 1 to 100 here. Send more for the rest."
    assert first["more"].startswith("c1.") and len(first["more"]) < 120
    assert one_refusal(data(person, area="food", fields=["id"], filters={"meal": "lunch"}, cursor=first["more"])) == (
        "cursor_changed", "This cursor is for another query.",
        "Send the cursor with the same area, fields, filters, from_date, to_date, group_by, totals and order as the "
        "call that gave it, or leave cursor out to start again.")
    assert one_refusal(data(person, area="food", fields=["id"], cursor=first["more"][:-3]))[0] == "invalid_field"
    assert one_refusal(data(person, area="food", fields=["id"], cursor="c1." + base64.urlsafe_b64encode(
        b'{"q":"x","f":"2026-01-02","t":"2026-01-01","o":0}').decode()))[0] == "invalid_field"
    # The hash is no secret: a cursor re-made with a range Spotter never gives is refused, never read.
    held = json.loads(base64.urlsafe_b64decode(first["more"][3:] + "=" * (-len(first["more"][3:]) % 4)))
    for f, t in (("0001-01-01", first["to"]), (first["from"], "9999-12-31")):
        forged = "c1." + base64.urlsafe_b64encode(json.dumps({**held, "f": f, "t": t}).encode()).decode().rstrip("=")
        assert one_refusal(data(person, area="food", fields=["id"], cursor=forged))[1] == \
            "cursor is not one Spotter gave."
    # The answer's own from and to may be sent back; another date is another query.
    assert "error" not in data(person, area="food", fields=["id"], cursor=first["more"], from_date=first["from"],
                               to_date=first["to"])
    assert one_refusal(data(person, area="food", fields=["id"], cursor=first["more"],
                            from_date=ago(5).isoformat()))[0] == "cursor_changed"
    # A page after midnight reads the first page's range.
    monkeypatch.setattr(db, "utcnow", lambda: NOW + timedelta(days=1))
    later = data(person, area="food", fields=["id"], cursor=first["more"])
    assert (later["from"], later["to"]) == (first["from"], first["to"]) and len(later["rows"]) == 100
    past = data(person, area="food", fields=["id"], limit=5, cursor=data_service._cursor(
        data_service._plan({"area": "food", "fields": ["id"]}, TODAY, date(2024, 11, 14), None).digest,
        ago(27), TODAY, 400))
    assert past["rows"] == [] and "more" not in past


def test_the_ceiling_cuts_the_page_and_paging_gives_every_row_once(person):
    with db.session_scope() as s:
        s.add_all(FoodEntry(user_id=person, date=ago(n % 28), meal="dinner", description=f"{n:03d} " + "x" * 196,
                            kcal=500, source="you_said", estimated=False, dedupe_key="k", logged_by="web")
                  for n in range(300))
    seen, cursor = [], None
    while True:
        answer = data(person, area="food", limit=300, **({"cursor": cursor} if cursor else {}))
        assert len(mcp_server._json_text(answer)[0]) <= data_service.CEILING
        seen += [row[1] for row in answer["rows"]]
        cursor = answer.get("more")
        if not cursor:
            break
        assert answer["note"] == "The rows stop at the size limit: send more for the rest."
        assert len(answer["rows"]) < 300
    assert len(seen) == len(set(seen)) == 300


# ---------------------------------------------------------------- goals and targets

def test_the_goals_that_ride_with_the_data(person):
    lift(person, ago(10), [("Back Squat", 100, 5), ("Bench Press", 60, 5)])
    weigh(person, ago(5), 82)
    squat = goal(person, kind="lift", exercise="Back Squat", target_value=130)
    bench = goal(person, kind="lift", exercise="Bench Press", target_value=90)
    goal(person, kind="bodyweight", target_value=75)
    closed = goal(person, kind="lift", exercise="Deadlift", target_value=200, start_value=150)
    services.set_goal(person, {"goal_id": closed, "status": "dropped"}, "web")
    rode = lambda **q: [g["goal_id"] for g in data(person, **q)["goals"]]  # noqa: E731
    assert rode(area="lift_sets", filters={"exercise": "Back Squat"}) == [squat]
    assert rode(area="lift_sets") == [squat, bench]
    assert rode(area="lift_sets", filters={"muscle": "chest"}) == [bench]
    assert rode(area="lift_sessions", filters={"exercises": ["Bench Press"]}) == [bench]
    assert rode(area="lift_sets", filters={"region": "lower"}) == [squat]
    assert rode(area="food") == [] and rode(area="saved_foods") == [] and rode(area="cardio") == []
    assert len(rode(area="weigh_ins")) == 1 and len(rode(area="days")) == 3
    numbers = {g["goal_id"]: g for g in services.get_goals(person)["goals"]}
    for g in data(person, area="days")["goals"]:
        assert g == {k: numbers[g["goal_id"]][k] for k in ("goal_id", "label", "status", "target_date", "plan_line")}
    assert [g["goal_id"] for g in services.what_is_logged(person)["goals"]] == list(numbers)


def walk_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(walk_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(set(), *(walk_keys(v) for v in value))
    return set()


@pytest.mark.parametrize("targets", [{}, {"calorie_target_kcal": 2100, "target_sessions_per_week": 4,
                                         "target_cardio_minutes_per_week": 150}])
def test_both_answers_carry_whoami_s_targets_and_no_other_setting(person, targets):
    assert "error" not in services.update_settings(person, {**targets, "weight_unit": "kg", "time_zone": "UTC"}, "web")
    eat(person, ago(1), "Dal", 180)
    who = services.whoami(person)["targets"]
    settings = {"time_zone", "claude_can", "weight_unit", "distance_unit", "add_back_exercise_kcal", "email", "units"}
    logged = services.what_is_logged(person)
    assert logged["targets"] == who and not walk_keys(logged) & settings
    for area in AREAS:
        answer = data(person, area=area)
        assert answer["targets"] == who and not walk_keys(answer) & settings, area
    assert who == ({"calorie_target_kcal": None, "protein_target_g": None, "carbs_target_g": None,
                    "fat_target_g": None,  # macros-and-asking.md P3: the daily goals in grams
                    "target_sessions_per_week": None, "target_cardio_minutes_per_week": None} | targets)


# ---------------------------------------------------------------- one person never sees another's rows

def test_one_person_never_sees_another_s_rows(make_user):
    a, b = make_user(time_zone="UTC").id, make_user(time_zone="UTC").id
    full(a)
    lift(b, ago(1), [("Deadlift", 180, 3)])
    run(b, ago(1), "Rowing Machine", 20, 4)
    weigh(b, ago(2), 95)
    measure(b, ago(2), "steps", 12345)
    banana = saved_food(b, "Banana", 105)
    eat(b, ago(2), "Banana", 105, saved=banana, servings=1, source="saved_food")
    goal(b, kind="bodyweight", target_value=90)
    goal(a, kind="lift", exercise="Back Squat", target_value=140)
    a_text = json.dumps([data(a, area=x, **q) for x in AREAS for q in ({}, {"group_by": ["week"]})]
                        + [services.what_is_logged(a)])
    b_text = json.dumps([data(b, area=x, **q) for x in AREAS for q in ({}, {"group_by": ["week"]})]
                        + [services.what_is_logged(b)])
    for word in ("Deadlift", "Rowing Machine", "12345", "Banana", "Body weight 90"):
        assert word not in a_text
    for word in ("Back Squat", "Outdoor Run", "Roti", "Chicken", "Back Squat 140"):
        assert word not in b_text
    groups = {"lift_sets": "exercise", "cardio": "activity", "measurements": "kind", "food": "meal"}
    for uid, count in ((a, {"lift_sets": 5, "cardio": 2, "weigh_ins": 1, "measurements": 1, "food": 4}),
                       (b, {"lift_sets": 1, "cardio": 1, "weigh_ins": 1, "measurements": 1, "food": 1})):
        for area, n in count.items():
            assert len(data(uid, area=area)["rows"]) == n
            grouped = data(uid, area=area, group_by=[groups.get(area, "week")])
            assert sum(r[-1] for r in grouped["rows"]) == n and grouped["totals"]["values"] == [n]
        assert {x["area"]: x["entries"] for x in services.what_is_logged(uid)["areas"]
                if x["area"] in count} == count


# ---------------------------------------------------------------- the review's rulings

def test_totals_left_empty_and_the_default_range_near_the_floor(person, make_user):
    eat(person, ago(1), "Dal", 180)
    assert data(person, area="food", totals={}) == data(person, area="food")
    old = make_user(time_zone="UTC", created_at=NOW).id
    near = (date(2024, 11, 14) + timedelta(days=10)).isoformat()
    answer = data(old, area="food", to_date=near)
    assert (answer["from"], answer["to"]) == (FLOOR, near) and answer["days_in_range"] == 11
    assert answer["range_note"] == "No from_date was given: this is the 11 days ending 24 Nov 2024."


def test_three_groups_get_the_schema_s_words_and_nothing_is_read(http, monkeypatch):
    token = sign()
    ok(http, token, "whoami")
    monkeypatch.setattr(services, "get_data", lambda *a: pytest.fail("the service ran"))
    err = failed(http, token, "get_data", {"area": "food", "group_by": ["day", "meal", "food"]})
    assert (err["error"], err["message"], err["fix"]) == ("invalid_field", "group_by holds 3 items: it takes 1 to 2.",
                                                          "Send 1 to 2 items in group_by.")


# ---------------------------------------------------------------- what_is_logged

def test_what_is_logged_on_an_empty_account(person):
    logged = services.what_is_logged(person)
    assert logged["note"] == "Nothing is logged yet: every area is empty."
    assert logged["summary"] == "No entries yet. No open goals."
    assert logged["field_columns"] == ["field", "meaning", "unit", "from", "totals", "filter", "group", "filled"]
    for area in logged["areas"]:
        assert (area["entries"], area["first"], area["last"], area["last_28d"], area["prior_28d"]) == (
            0, None, None, 0, 0), area["area"]
        assert "names" not in area and all(f[-1] == 0 for f in area["fields"])
    assert logged["how"] == data_service.HOW and logged["goals"] == []


def test_what_is_logged_on_a_full_account(person):
    full(person)
    lift(person, ago(40), [("Back Squat", 90, 5)])
    eat(person, ago(40), "Dal", 180)
    goal(person, kind="lift", exercise="Back Squat", target_value=140)
    logged = services.what_is_logged(person)
    assert "note" not in logged
    sets = area_of(logged, "lift_sets")
    assert (sets["entries"], sets["first"], sets["last"], sets["last_28d"], sets["prior_28d"]) == (
        6, ago(40).isoformat(), ago(2).isoformat(), 5, 1)
    assert sets["names"]["exercise"] == ["Back Squat", "Bench Press", "Pull-Up", "Suitcase Carry"]
    assert field_row(sets, "weight_kg") == ["weight_kg", "The weight lifted: one hand's for a pair, a minus weight for "
                                            "help, 0 for bodyweight work.", "kg", "stored",
                                            "count, avg, max, min", False, False, 6]
    assert field_row(sets, "muscle")[-1] == 6 and field_row(sets, "estimated_max_kg")[-1] == 3  # every lift has muscles
    assert field_row(sets, "time_s")[-1] == 1 and field_row(sets, "distance_m")[-1] == 1  # the carry's
    food = area_of(logged, "food")
    assert food["names"]["food"][0] == "chicken biryani" and food["names"]["meal"] == ["dinner", "lunch"]
    assert area_of(logged, "days")["entries"] == 5  # days with any entry
    assert area_of(logged, "saved_foods")["entries"] == 1 and area_of(logged, "saved_foods")["first"] == \
        TODAY.isoformat()
    assert logged["summary"] == "11 entries in 5 areas, from Wed 7 Oct to Mon 16 Nov. 1 open goal."
    assert logged["goals"] == [{k: services.get_goals(person)["goals"][0][k]
                                for k in ("goal_id", "label", "status", "target_date", "plan_line")}]


# ---------------------------------------------------------------- through /mcp

def test_both_are_published_with_their_words_arguments_and_the_read_only_hints(http):
    tools = {t["name"]: t for t in tool_list(http, sign())}
    names = list(tools)
    assert names[-3:] == ["get_routines", "what_is_logged", "get_data"]
    for name, title, words in (("what_is_logged", "What is logged", WHAT_IS_LOGGED_DESCRIPTION),
                               ("get_data", "Get data", GET_DATA_DESCRIPTION)):
        tool = tools[name]
        assert (tool["title"], tool["description"], tool["annotations"]) == (title, words, READ_ONLY)
        assert tr.check(words, tr.CLAUDE) == [], name
    assert (len(WHAT_IS_LOGGED_DESCRIPTION), len(GET_DATA_DESCRIPTION)) == (355, 490)
    assert tools["what_is_logged"]["inputSchema"]["properties"] == {}
    schema = tools["get_data"]["inputSchema"]
    assert list(schema["properties"]) == list(INPUTS) and schema["required"] == ["area"]
    assert {n: p["description"] for n, p in schema["properties"].items()} == INPUTS
    group_by = mcp_server._node(schema, schema["properties"]["group_by"])
    assert (group_by["minItems"], group_by["maxItems"]) == (1, 2)
    limit = mcp_server._node(schema, schema["properties"]["limit"])
    assert (limit["minimum"], limit["maximum"]) == (1, 300)
    for name in ("what_is_logged", "get_data"):
        out = tools[name]["outputSchema"]
        for obj in [out, *out.get("$defs", {}).values()]:
            for prop, spec in obj.get("properties", {}).items():
                assert spec.get("description"), (name, prop)
                assert tr.check(spec["description"], tr.CLAUDE) == [], (name, prop)
    assert mcp_server.INSTRUCTIONS.count(mcp_server.DATA_RULE) == 1
    assert mcp_server.GOALS_RULE + " " + mcp_server.DATA_RULE + " " in mcp_server.INSTRUCTIONS
    # exercise-list.md C4c: the sentence on a person's own words
    # 2026-11-20: the sleep routing sentence, the shorter Goals sentence, the meal sentence
    # log-and-guide.md L2: log's routing sentence in place of the sleep sentence
    assert len(mcp_server.INSTRUCTIONS) == 4084 < 4096
    assert mcp_server.INSTRUCTIONS.endswith("do not also quote latest, should_be_today or ahead_by.")


def test_each_new_entry_is_at_most_7500_characters(make_client):
    tools = {t["name"]: t for t in tool_list(make_client(owner_user_id=OWNER), sign_for(OWNER))}
    sizes = {name: len(compact(tools[name])) for name in ("what_is_logged", "get_data")}
    print("SIZES", sizes)
    assert all(size <= SIZE_BAR for size in sizes.values()), sizes


def test_a_null_input_is_taken_as_left_out(http):
    token = sign()
    ok(http, token, "whoami")
    nulls = {name: None for name in INPUTS if name != "area"}
    assert ok(http, token, "get_data", {"area": "food", **nulls}) == ok(http, token, "get_data", {"area": "food"})
    assert ok(http, token, "get_data", {"area": "food", "fields": None, "from_date": "  "})["range_note"]


def test_both_count_toward_the_read_limit_and_tried_names_no_value(http):
    token = sign()
    account = ok(http, token, "whoami")["account_id"]
    assert {"what_is_logged", "get_data"} <= set(mcp_server.READ_FUNCTIONS)
    ok(http, token, "get_data", {"area": "food", "filters": {"meal": "lunch"}, "group_by": ["meal"],
                                 "totals": {"kcal": ["sum"]}})
    with db.session_scope() as s:
        row = s.scalars(select(CallLog).order_by(CallLog.id.desc())).first()
        assert (row.function, row.outcome, row.tried) == (
            "get_data", "read", "area filters{meal} group_by[1] totals{kcal}")
        s.add_all(CallLog(user_id=account, function="get_data", started_at=db.utcnow(), ms=1, ok=True, result_bytes=1)
                  for _ in range(common.READS_LIMIT))
    assert failed(http, token, "what_is_logged")["error"] == "too_many_reads"
    assert failed(http, token, "get_data", {"area": "food"})["error"] == "too_many_reads"


def test_the_answers_through_mcp_are_the_services_own(http):
    token = sign()
    account = ok(http, token, "whoami")["account_id"]
    full(account)
    assert ok(http, token, "what_is_logged") == services.what_is_logged(account)
    for area in AREAS:
        result = call(http, token, "get_data", {"area": area})
        assert result["structuredContent"] == services.get_data(account, {"area": area})
        assert result["content"][0]["text"] == json.dumps(result["structuredContent"], separators=(",", ":"),
                                                           ensure_ascii=False)
        RESULTS["get_data"].model_validate(result["structuredContent"])


# ---------------------------------------------------------------- statements

def many(uid: int, n: int) -> None:
    """n rows of every area (weigh-ins one a day, so at most 700), an open body weight goal, and an open lift goal,
    whose numbers read lifts.history: its selectinload reads 500 sessions a statement on each of its two levels."""
    ids = _exercise_ids()
    with db.session_scope() as s:
        roti = SavedFood(user_id=uid, name="Roti", name_key="roti", serving_label="1 roti", kcal_per_serving=120)
        s.add(roti)
        s.flush()
        for i in range(n):
            day = ago(i % 700)
            session = LiftSession(user_id=uid, date=day, notes="", dedupe_key="k", logged_by="web")
            session.sets = [LiftSet(exercise_id=ids["Back Squat"], set_number=1, weight_kg=100, reps=5)]
            s.add(session)
            s.add(CardioEntry(user_id=uid, date=day, exercise_id=ids["Outdoor Run"], minutes=30, distance_km=5,
                              dedupe_key="k", logged_by="web"))
            s.add(FoodEntry(user_id=uid, date=day, meal="lunch", description="Roti", kcal=120, source="saved_food",
                            estimated=False, saved_food_id=roti.id, servings=1, dedupe_key="k", logged_by="web"))
            s.add(Measurement(user_id=uid, date=ago(i // 9), kind=list(rules.MEASUREMENTS)[i % 9], value=1,
                              unit=list(rules.MEASUREMENTS.values())[i % 9][0], dedupe_key="k", logged_by="web"))
            if i < 700:
                s.add(Bodyweight(user_id=uid, date=day, kg=80, dedupe_key="k", logged_by="web"))
    goal(uid, kind="bodyweight", target_value=75)
    goal(uid, kind="lift", exercise="Back Squat", target_value=150)


def statements_of(work) -> int:
    seen = []
    engine = db.get_engine()

    def count(*args):
        seen.append(1)

    event.listen(engine, "before_cursor_execute", count)
    try:
        assert "error" not in work()
    finally:
        event.remove(engine, "before_cursor_execute", count)
    return len(seen)


def test_the_statements_do_not_grow_with_the_rows(make_user):
    counts = {}
    for n in (10, 1000):
        uid = make_user(time_zone="UTC").id
        many(uid, n)
        whole = {"from_date": FLOOR}
        counts[n] = {area: statements_of(lambda: data(uid, area=area, **whole)) for area in AREAS}
        counts[n] |= {f"{area} by week": statements_of(lambda: data(uid, area=area, group_by=["week"], **whole))
                      for area in AREAS if area != "saved_foods"}
        counts[n]["what_is_logged"] = statements_of(lambda: services.what_is_logged(uid))
    # Only the reads that carry the lift goal grow, and by at most one statement a 500 sessions on each of
    # lifts.history's two selectinload levels: never one a row.
    lifting = {"lift_sets", "lift_sessions", "days", "lift_sets by week", "lift_sessions by week", "days by week",
               "what_is_logged"}
    bound = 2 * math.ceil(1000 / 500)
    for read, few in counts[10].items():
        grown = counts[1000][read] - few
        assert (0 <= grown <= bound) if read in lifting else grown == 0, (read, few, counts[1000][read])


# ---------------------------------------------------------------- text

def test_every_text_passes_its_surface(person, make_user):
    for sent, (_, message, fix) in ERRORS:
        assert tr.check(message, tr.MESSAGE) == [] and tr.check(fix, tr.MESSAGE) == [], message
    for area in AREAS.values():
        assert tr.check(area.meaning, tr.SUMMARY) == [], area.name
        for f in area.fields:
            assert tr.check(f.meaning, tr.SUMMARY) == [], f.name
    assert tr.check(data_service.HOW, tr.SUMMARY) == []
    full(person)
    texts = [services.what_is_logged(person)["summary"], services.what_is_logged(make_user().id)["summary"],
             data_service.SIZE_NOTE, data_service.EMPTY_NOTE]
    for area in AREAS:
        for q in ({}, {"group_by": ["week"]} if area != "saved_foods" else {}, {"limit": 1},
                  {"from_date": ago(200).isoformat(), "to_date": ago(150).isoformat()}):
            answer = data(person, area=area, **q)
            texts += [answer["summary"], answer.get("note") or "", answer.get("range_note") or ""]
    texts += [data(person, area="lift_sets", to_date=ago(3).isoformat())["range_note"]]
    for text in texts:
        assert tr.check(text, tr.SUMMARY) == [], text


# ---------------------------------------------------------------- the review's fix round

def test_a_range_total_of_value_never_mixes_kinds(person):
    measure(person, ago(1), "waist", 84)
    measure(person, ago(1), "steps", 10000)
    measure(person, ago(1), "water_ml", 2000)
    by_kind = data(person, area="measurements", group_by=["kind"], totals={"value": ["avg", "max"]})
    assert table(by_kind) == [{"kind": "steps", "value_avg": 10000, "value_max": 10000},
                              {"kind": "water_ml", "value_avg": 2000, "value_max": 2000},
                              {"kind": "waist", "value_avg": 84, "value_max": 84}]
    assert by_kind["totals"] == {"columns": ["value_avg", "value_max"], "values": [None, None]}  # not 4028 of cm,
    # steps and ml
    daily = data(person, area="measurements", group_by=["kind"], filters={"kind": ["steps", "water_ml"]},
                 totals={"value": ["sum", "per_day", "count"]})
    assert daily["totals"] == {"columns": ["value_sum", "value_per_day", "value_count"], "values": [None, None, 2]}
    one = data(person, area="measurements", group_by=["kind"], filters={"kind": "steps"}, totals={"value": ["sum"]})
    assert one["totals"]["values"] == [10000]  # one kind: a figure


def test_value_s_share_needs_one_kind_even_grouped_by_kind(person):
    for n, steps in enumerate((8000, 12000)):
        measure(person, ago(n), "steps", steps)
    measure(person, ago(0), "water_ml", 2000)
    weeks = data(person, area="measurements", group_by=["week"], filters={"kind": "steps"},
                 totals={"value": ["sum", "share"]}, from_date=ago(1).isoformat())
    assert table(weeks) == [{"week": ago(0).isoformat(), "days": 1, "days_logged": 1, "value_sum": 8000,
                             "value_share": 40},
                            {"week": "2026-11-09", "days": 1, "days_logged": 1,
                             "value_sum": 12000, "value_share": 60}]
    assert one_refusal(data(person, area="measurements", group_by=["kind"], filters={"kind": ["steps", "water_ml"]},
                            totals={"value": ["share"]})) == (
        "invalid_field", "value's share needs one kind: it is a part of one kind's total.",
        "Filter kind to one kind, such as {\"kind\": \"steps\"}, or leave share out.")
    for text in ("value's share needs one kind: it is a part of one kind's total.",
                 "Filter kind to one kind, such as {\"kind\": \"steps\"}, or leave share out."):
        assert tr.check(text, tr.MESSAGE) == [], text
    assert one_refusal(data(person, area="measurements", group_by=["kind"], filters={"kind": ["steps", "waist"]},
                            totals={"value": ["share"]}))[1].startswith("value's share needs a kind filter of")
    assert "error" not in data(person, area="measurements", group_by=["kind"], filters={"kind": ["steps", "water_ml"]},
                               totals={"value": ["sum"]})  # sum and per_day keep the row's rule


def test_at_most_ten_goals_ride_and_a_page_always_moves_on(person):
    lift(person, ago(1), [("Back Squat", 100, 5), ("Back Squat", 100, 5)])
    squat = _exercise_ids()["Back Squat"]
    from app.models import Goal  # noqa: PLC0415
    with db.session_scope() as s:
        s.add_all(Goal(user_id=person, kind="lift", exercise_id=squat, unit="kg", start_value=100, start_date=TODAY,
                       direction="up", target_value=150 + i, target_date=TODAY + timedelta(days=60 + i),
                       status="open", text="") for i in range(200))
    first = data(person, area="lift_sets", limit=1)
    assert len(mcp_server._json_text(first)[0]) < data_service.CEILING and len(first["rows"]) == 1
    assert len(first["goals"]) == 10
    assert [g["goal_id"] for g in first["goals"]] == [g["goal_id"] for g in services.get_goals(person)["goals"][:10]]
    assert first["note"] == "190 more open goals: get_goals has them all."
    second = data(person, area="lift_sets", limit=1, cursor=first["more"])
    assert len(second["rows"]) == 1 and second["rows"] != first["rows"] and "more" not in second
    assert second["summary"] == "2 sets from 20 Oct to 16 Nov, rows 2 to 2 here."
    # However big the part that is not rows, a page keeps one row, so more moves on.
    page = data_service._paged(data_service._plan({"area": "food"}, TODAY, date(2024, 11, 14), None),
                               {"pad": "x" * data_service.CEILING}, {}, [], {}, [[1], [2]], None)
    assert page["rows"] == [[1]] and "more" in page


def test_a_long_or_deep_cursor_is_refused_never_a_crash(http):
    token = sign()
    ok(http, token, "whoami")
    deep = "c1." + base64.urlsafe_b64encode(b"[" * 3000).decode().rstrip("=")
    assert len(deep) == 4003
    err = failed(http, token, "get_data", {"area": "food", "cursor": deep})
    assert (err["error"], err["message"], err["fix"]) == (
        "invalid_field", "cursor is not one Spotter gave.",
        "Send more from the last get_data answer as it came, or leave cursor out to start from the first rows.")
    short_deep = "c1." + base64.urlsafe_b64encode(b"[" * 100).decode()
    assert len(short_deep) <= data_service.CURSOR_MOST and data_service._cursor_of(short_deep) is None
    assert data_service._cursor_of("c1." + "A" * 198) is None
    # A cursor Spotter could have made, but padded past 200 characters, is refused for its length alone.
    real = data_service._cursor(data_service._plan({"area": "food"}, TODAY, date(2024, 11, 14), None).digest,
                                ago(27), TODAY, 0)
    body = json.loads(base64.urlsafe_b64decode(real[3:] + "=" * (-len(real[3:]) % 4)))
    padded = "c1." + base64.urlsafe_b64encode(json.dumps({**body, "x": "a" * 200}).encode()).decode().rstrip("=")
    assert data_service._cursor_of(real) is not None and data_service._cursor_of(padded) is None


def test_another_account_s_saved_food_never_names_a_food_entry(make_user):
    a, b = make_user(time_zone="UTC").id, make_user(time_zone="UTC").id
    private = saved_food(b, "BEE private shake", 300)
    with pytest.raises(IntegrityError):  # fk_food_entry_user_id_saved_food refuses the row at commit (hardening D12)
        eat(a, ago(1), "Shake", 300, saved=private, servings=1, source="saved_food")
    assert table(data(a, area="food")) == []
    assert "BEE" not in json.dumps(services.what_is_logged(a))


def test_the_days_area_takes_every_goal_kind_get_goals_counts():
    from app.services import goals as goal_reads  # noqa: PLC0415
    assert AREAS["days"].goal_kinds == goal_reads.KINDS_NOW
