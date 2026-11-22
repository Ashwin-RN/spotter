"""Input checks (docs/contracts/inputs.md): whatever an assistant or a form sends, Spotter takes it as meant or refuses
it with a code and a fix, and a refused call writes nothing.

Two parts. The sweep sends every field of every function, read from the owner's tools/list (nested through $defs),
and every field of every form a page draws, a bad value at a time over a valid base call held here, and holds what
each must give. Then one test per decision, each refusal's words word for word.

Dates are worked from the person's today (users.today_for), never typed: 0001-01-01 and 9999-12-31 are the only fixed
dates, and the boundary tests of decision 15 set the person's today at a seam.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, text as sql, update

from app import db, formatting, mcp_server, rules, services, users, web
from app.models import Base, CallLog, Feedback, Goal, User
from app.services import common, feedback, goals, owner, usda
from evals import judge

from . import text_rules as tr
from .conftest import claims, rpc, sign
from .test_feedback import compact
from .test_idempotency import FORM, TEMPLATES
from .test_mcp import registered_tools, set_switch
from .test_trace import OWNER, sign_for
from .test_web import client, only_user, post, seam, sign_in, today_of, web_client  # noqa: F401 - fixtures

# ---------------------------------------------------------------- the published list (decisions 1 and 17)

# Each function as published at 194e7f0, before this work: the SHA-256 of the tool as compact JSON with sorted keys.
# log_lifts and get_log: as published after exercise-list.md's deploy 1, where SetOut gained time_s and distance_m. Then
# its package C4b moved log_lifts (LogSetIn), get_log (SetOut), edit_entry (SetIn), import_entries, save_routine and
# get_routines (reps 1 to 100, the import's names part), C4d list_exercises (search, count, note), and C4e log_lifts,
# get_log and get_progress (NewBest's and Best's kinds and units). On 2026-11-20 an outside review moved log_lifts and
# edit_entry (a pair's weight) and get_progress (Best.text).
BEFORE_INPUTS = {
    "whoami": "f9df11008be2cd1b403484a17383cdd7786f09fc04a0b78047b1aaf9bf7e894b",
    "list_exercises": "5c4dace8b6b714d2523bc3e7a5f6ebb34b65753d7d4da2abb11f5c2bf12bbfa3",  # exercise list C4d: search; C4f: a side; log and guide: log takes the person's words
    "log_lifts": "25bad23e53f48945c75888f6f706afe840f0253c68dcf1db3d7e3efdaf6f7c11",  # exercise list: LogSetIn; C4e: NewBest; C4f: hand_check, a side; 2026-11-20: a pair's weight
    "log_bodyweight": "62a520d93a8c65d8e66bdc12cff2c1fb1b3e981730e8b06e97b3b5a183bb790a",
    "set_goal": "001cc988cf2a05368940fca6fc6a440cd0988ba2e50ecb9441336ebef98abec2",
    "get_progress": "81b1ca6ab08ee7a300aa32893713e73457fc836f649b845bb4260dfdfb17edd5",  # exercise list C4e: Best; 2026-11-20: Best.text
    "update_settings": "1053550decae70f255f0edaf894b730c1da9315e2d989ae1e5e2536eee85c879",
    "get_log": "dcfab8951e9a8aa501e136054150cb05c07477ad2eda6ca87b6dfc5114e059e5",  # exercise list: SetOut; C4e: NewBest
    "log_measurement": "7456c5853a541df971d7b8ab8f382d835e82a6b1ca35c8ece0c60c985db12647",
    "edit_entry": "86f202eb01e3ff4c39a8e9236234a350f4a02ed20c1cdc1c5b559c5e2ebe2bc5",  # exercise list: SetIn; C4f: a side; 2026-11-20: a pair's weight; log and guide: kind words, id words
    "restore_entry": "7ef4931069bb011e48b55cb21e2c574ea21c2716112a85957b0ad0499220cb67",  # log and guide: kind words, id words
    "delete_entry": "02798afe3eee1addad25aac4c73de15475ec648830d12f67134cd910b308e7b0",  # log and guide: kind words, id words
    "find_food": "db1fa82718a2a9e97d81806d21988b8340d2f20ec1c1a30d01eccd0b6e30a586",  # log and guide: ids log takes
    "log_food": "91bdd6db7aa8421b0dc1fc806d2afea719e7c6c23c08ae259bc6da7ecbccabad",  # 2026-11-20: meal leads with sending; log and guide: FoodEntryIn's words
    "save_food": "599ec355d36b392504083437501412a35693828717381813341ff1b7683ad5fd",  # log and guide: ids log takes
    "log_cardio": "5a4106f698eae37d7b11fe096d19f60e2d405050ada8b23aa7e33b65df8d8794",  # 2026-11-20: exercise's words name both walks
    "get_trends": "ffb7403c8b2029888d1c57e431059922e77c4884e21934b0632d0e68c063aa3e",
    "get_goals": "deea8694d330fa819c06766d8d24217c74a69c41314448f93946f97803995c94",
    "import_entries": "5bfa3ce4279e3aca8619fdcc0dbc987081ca9b27f79fa9a74dd836377bc865c5",  # exercise list
    "send_feedback": "63bf4722f94c7863dcafce688f8864224c46cbf30868d971e5347475a0686184",
    "save_routine": "4d81200fa0c288b0ee8a5a1c8bffcb38ec8016359e8ad31043541d5ef7b981a6",  # exercise list; C4c: help, band; C5a: sets and reps optional; C5b: holds, carries and cardio
    "get_routines": "d7dd893333de70dc78d22a1c943887f06954659fb338f38c529ce40c8a9017a3",  # exercise list; C4c: exact true, help; log and guide: sets sent with log; C5a: last, same as last time; C5b: log_with, the kinds' plans, cardio's last, last's numbers as they are
    "owner_calls": "c9ea367aa190315d016fa4d014e783b8947dc436b81984600bebc785211d1cbb",  # log and guide L4: function, caller, all_accounts, a call's account_id
    "owner_feedback": "242b0d08f0ca2b4bb696ec818f327ede1d2b2b71b6959dba0e687cc9b628a5f7",
    "owner_delete_feedback": "e6d44e98f7ac2ce9945b277c0c592c7bde7ba6060b6628990f3a1bc16764909b",
}
# Decision 17: the three descriptions, (function, argument) -> (before, now).
MONTH_END = mcp_server.MONTH_END_RULE
DESCRIPTIONS = {
    ("set_goal", "target_date"): (
        "The date to reach it by, YYYY-MM-DD, after today. " + MONTH_END,
        "The date to reach it by, YYYY-MM-DD, after today and at most 5 years on. " + MONTH_END),
    ("set_goal", "target_value"): (
        "The target, in the kind's unit. A lift: an estimated one-rep max in weight_unit, above 0 to 666.67 kg or "
        "1,469.75 lb. Body weight: in weight_unit, 20 to 400 kg or 44.1 to 881.84 lb. A measurement: its own unit, cm "
        "for a body size, bpm for resting_hr. A cardio_best: minutes for the distance, 1 to 600.",
        "The target, in the kind's unit. A lift: an estimated one-rep max in weight_unit, above 0 to 666.67 kg or "
        "1,469.75 lb. Body weight: in weight_unit, 20 to 400 kg or 44.1 to 881.84 lb. A measurement: its own unit, cm "
        "for a body size, bpm for resting_hr. A cardio_best: minutes for the distance, 1 to 600, at a speed from 0.1 to "
        "60 km/h."),
    ("log_measurement", "value"): (
        "The value in the kind's unit: cm for the body sizes, steps, hours of sleep, bpm, ml.",
        "The value in the kind's unit: cm for the body sizes, steps, hours of sleep, bpm, ml. Spotter takes no inches."),
}
FORBIDDING = ("SetIn", "FoodEntryIn", "RoutineLiftIn",  # the three argument objects of decision 1
              "LogSetIn")  # and log_lifts' own set model since exercise-list.md R3


def sha(tool: dict) -> str:
    return hashlib.sha256(compact(tool).encode()).hexdigest()


def without_inputs(tool: dict) -> dict:
    """A published tool with this work's changes taken out: additionalProperties at its top level and on the three
    argument objects, and the three old descriptions back. What it published before, when nothing else moved."""
    out = copy.deepcopy(tool)
    schema = out["inputSchema"]
    schema.pop("additionalProperties", None)
    for name in FORBIDDING:
        schema.get("$defs", {}).get(name, {}).pop("additionalProperties", None)
    for (function, argument), (before, now) in DESCRIPTIONS.items():
        spec = schema["properties"].get(argument) if out["name"] == function else None
        if spec is not None and spec.get("description") == now:
            spec["description"] = before
    return out


def owner_tools(make_client) -> list[dict]:
    """The owner's list and the five log functions, which no list holds now and a chat opened before still calls
    (log-and-guide.md), in the order registered."""
    return list(registered_tools(make_client(owner_user_id=OWNER)).values())


def test_the_published_list_differs_only_by_additional_properties_and_the_three_descriptions(make_client):
    # data-on-request.md added what_is_logged and get_data after get_routines: the old names are compared as before.
    tools = [t for t in owner_tools(make_client) if t["name"] in BEFORE_INPUTS]
    assert [t["name"] for t in tools] == list(BEFORE_INPUTS)
    for tool in tools:  # every number and integer, every output schema and every other word as published before
        assert sha(without_inputs(tool)) == BEFORE_INPUTS[tool["name"]], tool["name"]
        schema = tool["inputSchema"]
        assert schema["additionalProperties"] is False, tool["name"]
        for name, spec in schema.get("$defs", {}).items():  # the import's three allow, as they did, for its preview
            expected = False if name in FORBIDDING else True if name.startswith("Import") else None
            assert spec.get("additionalProperties") is expected, (tool["name"], name)
    by_name = {t["name"]: t for t in tools}
    for (function, argument), (_, now) in DESCRIPTIONS.items():
        assert by_name[function]["inputSchema"]["properties"][argument]["description"] == now
        assert tr.check(now, tr.CLAUDE) == [], now
    defs = {name for t in tools for name in t["inputSchema"].get("$defs", {})}
    assert set(FORBIDDING) <= defs and {"ImportRowIn", "ImportSetIn", "ImportFoodIn"} <= defs  # those three allow


def test_the_instructions_are_unchanged():
    # data-on-request.md: DATA_RULE joined after; exercise-list.md C4c
    # 2026-11-20: the sleep routing sentence, the shorter Goals sentence, the meal sentence
    # log-and-guide.md L2: log's routing sentence in place of the sleep sentence
    assert len(mcp_server.INSTRUCTIONS) == 4084


# ---------------------------------------------------------------- the person and the calls

GATES = ("too_many_writes", "too_many_reads", "too_much_feedback", "too_many_imported", "time_zone_not_set",
         "not_allowed", "no_account")  # a refusal of the call's gate, never of its value: the sweep sees none
CRASHES = ("internal_error", "database_unavailable")
LIBRARY_WORDS = ("validation error", "pydantic", "Input should")
COUNTED = [t for t in Base.metadata.sorted_tables if t.name not in ("call_log", "write_key")]


def rows_held() -> int:
    """Every row of every table but the call log and the key claims: one statement."""
    total = " + ".join(f"(SELECT count(*) FROM {t.name})" for t in COUNTED)
    with db.session_scope() as s:
        return s.execute(sql(f"SELECT {total}")).scalar()


def clear_call_log() -> None:
    """The call log, emptied between sweep calls: no write, read or feedback limit is reached by the sweep itself."""
    with db.session_scope() as s:
        s.execute(CallLog.__table__.delete())


def tool_call(client, token: str, name: str, arguments: dict) -> dict:
    r = rpc(client, "tools/call", {"name": name, "arguments": arguments}, token=token)
    assert r.status_code == 200, r.text
    return r.json()["result"]


@pytest.fixture
def owner_person(make_client, make_user):
    """(client, token, user) of Spotter's owner, with a time zone, deleting allowed, and one of each thing a base
    call needs: a weigh-in to edit, a deleted one to restore, a session, a saved food, a goal, a routine and a note."""
    client = make_client(owner_user_id=OWNER)
    token = sign_for(OWNER)
    account = tool_call(client, token, "whoami", {})["structuredContent"]["account_id"]
    assert "error" not in services.update_settings(account, {"time_zone": "UTC"}, "web")
    set_switch(account, "log_delete")
    with db.session_scope() as s:
        user = s.get(User, account)
        s.expunge(user)
    return client, token, user


def seeded(user: User) -> dict:
    """The ids the base calls name, written through the services."""
    today = users.today_for(user)
    day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    uid = user.id
    weigh_in = services.log_bodyweight(uid, {"kg": 80, "date": day(3)}, "claude")["id"]
    gone = services.log_bodyweight(uid, {"kg": 81, "date": day(4)}, "claude")["id"]
    assert "error" not in services.delete_entry(uid, "bodyweight", gone, "claude",
                                                confirm=services.delete_entry(uid, "bodyweight", gone, "claude")
                                                ["confirm"])
    saved = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 100}, "claude")
    goal = services.set_goal(uid, {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                                   "target_date": (today + timedelta(days=200)).isoformat()}, "claude")["id"]
    note = services.send_feedback(uid, {"text": "The chart is confusing."})["id"]
    return {"weigh_in": weigh_in, "gone": gone, "saved_food": saved["id"], "goal": goal, "note": note}


def bases(user: User, ids: dict) -> dict:
    """A valid call of every function, by name: each sweep value replaces one field of it."""
    today = users.today_for(user)
    day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    later = (today + timedelta(days=365)).isoformat()
    return {
        "whoami": {},
        "list_exercises": {"kind": "lift", "category": "squat"},
        # log-and-guide.md: log's lift part, its details, and guide's topic, through misfit and then route.
        "log": {"kind": "lifts", "parts": [{"movement": "Back Squat", "weight_kg": 100, "reps": 5}], "date": day(1),
                "notes": "Easy", "allow_repeat": False},
        "guide": {"topic": "food"},
        "log_lifts": {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}], "date": day(1),
                      "notes": "Easy", "allow_repeat": False, "new_session": False},
        "log_bodyweight": {"kg": 80, "date": day(1), "allow_repeat": False},
        "set_goal": {"kind": "cardio_best", "exercise": "Outdoor Walk", "distance_km": 5, "target_value": 28,
                     "target_date": later, "text": "Sub 28", "start_value": 31},
        "get_progress": {"exercise": "Back Squat"},
        "update_settings": {"time_zone": "UTC", "calorie_target_kcal": 2200, "target_sessions_per_week": 4,
                            "target_cardio_minutes_per_week": 150, "add_back_exercise_kcal": True, "weight_unit": "kg",
                            "distance_unit": "km"},
        "get_log": {"date": day(1), "days": 3},
        "log_measurement": {"kind": "waist", "value": 84, "date": day(1), "allow_repeat": False},
        "edit_entry": {"kind": "bodyweight", "entry_id": ids["weigh_in"], "kg": 79},
        "restore_entry": {"kind": "bodyweight", "entry_id": ids["gone"], "undo_edit": False},
        "delete_entry": {"kind": "bodyweight", "entry_id": ids["weigh_in"]},
        "find_food": {"query": "roti", "limit": 5},
        "log_food": {"entries": [{"description": "Dal", "kcal": 180, "estimated": False}], "date": day(1),
                     "allow_repeat": False},
        "save_food": {"name": "Idli", "serving_label": "1 idli", "kcal_per_serving": 40, "grams_per_serving": 50},
        "log_cardio": {"exercise": "Outdoor Run", "minutes": 30, "date": day(1), "distance_km": 5, "incline_pct": 1,
                       "speed_kmh": 10, "avg_hr": 150, "machine_kcal": 300, "notes": "Easy", "allow_repeat": False},
        "get_trends": {},
        "get_goals": {"include_closed": False},
        "import_entries": {"rows": [
            {"kind": "lift_session", "date": day(20), "sets": [{"exercise": "Deadlift", "weight": 100, "reps": 5,
                                                                 "times": 2}], "notes": "Old"},
            {"kind": "bodyweight", "date": day(21), "weight": 81},
            {"kind": "measurement", "date": day(21), "measurement_kind": "waist", "value": 85},
            {"kind": "food_entry", "date": day(22), "entries": [{"description": "Oats", "kcal": 350,
                                                                  "meal": "breakfast"}]},
            {"kind": "cardio_entry", "date": day(23), "exercise": "Outdoor Run", "minutes": 30, "distance": 5,
             "incline_pct": 1, "speed": 10, "avg_hr": 150, "machine_kcal": 300}],
            "dry_run": False, "weight_unit": "kg", "distance_unit": "km", "length_unit": "cm"},
        "send_feedback": {"text": "It is slow.", "doing": "Logging", "expected": "Fast", "happened": "Slow",
                          "topic": "logging"},
        "save_routine": {"name": "Push day", "lifts": [{"exercise": "Bench Press", "sets": 3, "reps": 8,
                                                        "weight_kg": 60}]},
        "get_routines": {},
        "what_is_logged": {},  # data-on-request.md: its two, so the sweep's values reach get_data's fields
        "get_data": {"area": "lift_sets", "fields": ["reps"], "filters": {"exercise": "Back Squat"},
                     "from_date": day(10), "to_date": day(1), "order": "-reps", "limit": 5},
        "owner_calls": {"account_id": user.id, "hours": 24, "failed_only": False, "limit": 50},
        "owner_feedback": {"email": "owner@example.com", "limit": 10},
        "owner_delete_feedback": {"ids": [ids["note"]]},
    }


def test_every_base_call_succeeds_and_names_every_function(owner_person, monkeypatch):
    client, token, user = owner_person
    calls = bases(user, seeded(user))
    before = list(BEFORE_INPUTS)  # log-and-guide.md: log and guide after list_exercises, as registered
    assert list(calls) == [*before[:2], "log", "guide", *before[2:22], "what_is_logged", "get_data", *before[22:]]
    for name, arguments in calls.items():
        result = tool_call(client, token, name, arguments)
        assert result["isError"] is False, (name, result)
        clear_call_log()


# ---------------------------------------------------------------- the sweep's values (the contract's table)

def _resolve(schema: dict, node):
    return mcp_server._node(schema, node)


def fields_of(schema: dict):
    """(path, spec) of every field of a published input schema, nested through $defs: a list of objects is walked
    at its first item, (name, 0, field); a list of plain values names its first item, (name, 0)."""
    def walk(node, path):
        for name, spec in node.get("properties", {}).items():
            spec = _resolve(schema, spec)
            yield (*path, name), spec
            if spec.get("type") == "array":
                items = _resolve(schema, spec.get("items")) or {}
                if items.get("type") == "object":
                    yield from walk(items, (*path, name, 0))
                else:
                    yield (*path, name, 0), items
    yield from walk(_resolve(schema, schema), ())


def objects_of(schema: dict):
    """(path, the fields it takes) of every object in a published input schema: the top level, and each list's item."""
    yield (), list(schema.get("properties", {}))
    for path, spec in fields_of(schema):
        if spec.get("type") == "array":
            items = _resolve(schema, spec.get("items")) or {}
            if items.get("type") == "object":
                yield (*path, 0), list(items.get("properties", {}))


NUMBER_VALUES = [True, False, "12abc", "", "   ", "1_0", "8e1", "٨٠", "８０", "nan", "inf", [1], {},
                 -0.01]
WHOLE_VALUES = [*NUMBER_VALUES, 2.5, 2 ** 31, 2 ** 63, 10 ** 30]
STRING_VALUES = ["a\u0000b", True, 12, [1], {}]
BOOLEAN_VALUES = [[1], {}, "perhaps"]
ARRAY_VALUES = ["not a list", {}, []]
FORM_TEXT = ["a\u0000b"]
FORM_NUMBER = ["12abc", "1_0", "8e1", "٨٠", "８０", "-0.01"]
# One character over the field's limit, read from the service's own constant.
LIMITS = {
    "notes": rules.TEXT_MAX, "text": rules.TEXT_MAX, "description": rules.TEXT_LIMITS["description"],
    "name": rules.TEXT_LIMITS["name"], "serving_label": rules.TEXT_LIMITS["serving_label"],
    "email": owner.TEXT_FILTERS["email"], "request_id": owner.TEXT_FILTERS["request_id"],
}
LIMITS_BY = {("save_routine", "name"): rules.ROUTINE_NAME_MAX,
             **{("send_feedback", f): feedback.TEXT_MAX for f in ("text", "doing", "expected", "happened")}}
ENTRY_DATES = {"log_lifts", "log_bodyweight", "log_measurement", "log_food", "log_cardio", "edit_entry",
               "import_entries"}


def date_values(function: str, field: str, today: date) -> list[str]:
    """The date forms of the contract's table, from the person's today."""
    yesterday = today - timedelta(days=1)
    week = yesterday.isocalendar()
    out = [yesterday.strftime("%Y%m%d"), f"{week.year}-W{week.week:02d}-{week.weekday}", yesterday.strftime("%Y/%m/%d"),
           f"{today.year}-02-30", "0001-01-01", "9999-12-31"]
    if function in ENTRY_DATES:
        out += [(today + timedelta(days=1)).isoformat(), (today - timedelta(days=rules.OLDEST_DAYS + 1)).isoformat()]
    if field == "target_date":
        out += [today.isoformat(), (rules.goal_latest(today) + timedelta(days=1)).isoformat()]
    return out


def values_for(function: str, path: tuple, spec: dict, today: date) -> list:
    field = next(p for p in reversed(path) if isinstance(p, str))
    kind = spec.get("type")
    if kind == "string" and field in ("date", "target_date"):
        return date_values(function, field, today) + [True, 12, [1], {}]
    if kind == "string":
        limit = LIMITS_BY.get((function, field), LIMITS.get(field))
        return STRING_VALUES + (["x" * (limit + 1)] if limit else [])
    return {"number": NUMBER_VALUES, "integer": WHOLE_VALUES, "boolean": BOOLEAN_VALUES,
            "array": ARRAY_VALUES}.get(kind, [])


NEAR_KEYS = {  # the near misses of decision 3, by where they apply: a function's top level, or an item's list
    "log_measurement": ("unit", "units"), "edit_entry": ("unit", "units", "calories", "cal", "distance", "speed",
                                                         "duration", "time", "heart_rate", "hr", "incline", "weight",
                                                         "weight_kg", "weight_lb", "lbs"),
    "log_cardio": ("calories", "cal", "kcal", "distance", "speed", "duration", "time", "heart_rate", "hr", "incline"),
    "log_bodyweight": ("weight", "weight_kg", "weight_lb", "lbs"), "update_settings": ("length_unit",),
    "sets": ("weight", "weight_lbs", "lbs", "lb", "kg", "weight_kgs"),
    "lifts": ("weight", "weight_lbs", "lbs", "lb", "kg", "weight_kgs"),
    "entries": ("serving", "serving_count", "qty", "quantity", "amount", "calories", "cal", "kcals"),
}


# A number's twin in the other unit (units.md decision 4): sent beside it, the pair is refused whatever the value.
TWINS = {"kg": "lb", "weight_kg": "weight_lb", "distance_km": "distance_mi", "speed_kmh": "speed_mph"}
TWINS |= {b: a for a, b in TWINS.items()}


def put(arguments: dict, path: tuple, value, items: dict) -> dict:
    """The base call with one field set at path: a list it passes through that the base lacks gets one item. The
    field's twin in the other unit is taken out, so the value is judged on its own."""
    out = copy.deepcopy(arguments)
    node = out
    for part in path[:-1]:
        if isinstance(part, int):
            node = node[part]
            continue
        if part not in node or not isinstance(node[part], list):
            node[part] = [copy.deepcopy(items.get(part, {}))]
        node = node[part]
    if isinstance(node, dict):
        node.pop(TWINS.get(path[-1]), None)
    node[path[-1]] = value
    return out


ITEMS = {"sets": {"exercise": "Back Squat", "weight_kg": 100, "reps": 5}, "entries": {"description": "Dal", "kcal": 180},
         "lifts": {"exercise": "Bench Press", "sets": 3, "reps": 8}}


def row_path(arguments: dict, path: tuple) -> tuple:
    """An import row field's path, moved to the base row of the kind that holds the field (rows 0 is a session)."""
    if path[:2] != ("rows", 0) or len(path) < 3:
        return path
    rows = arguments["rows"]
    holder = next((i for i, row in enumerate(rows) if path[2] in row), 0)
    return ("rows", holder, *path[2:])


# What is taken on purpose: (function, field, a test of the value) -> the reason. Nothing else may be taken.
TAKEN = [
    (lambda f, field, v: f == "delete_entry" and field == "confirm",
     "A confirm code that does not match returns the preview again: delete_entry's design."),
    (lambda f, field, v: f == "set_goal" and field == "measurement_kind" and isinstance(v, str),
     "A goal field of another kind is left out with a warning in the result (goals.py), not silently."),
]


def _no_network(*args, **kwargs):
    raise AssertionError("a test reached for the network")


def taken_on_purpose(function: str, field: str, value) -> str | None:
    return next((why for test, why in TAKEN if test(function, field, value)), None)


def refusal_findings(result: dict) -> list[str]:
    """What a refusal through /mcp must hold: is_error, a code and a fix in structuredContent, no crash, no gate."""
    found = []
    text = (result.get("content") or [{}])[0].get("text", "")
    found += [f"library words: {w}" for w in LIBRARY_WORDS if w in text]
    out = result.get("structuredContent") or {}
    if result.get("isError") is not True:
        found.append("not refused")
    if not out.get("error") or not str(out.get("fix") or "").strip():
        found.append(f"no code or fix: {text[:120]}")
    if out.get("error") in CRASHES or out.get("error") in GATES:
        found.append(f"code {out.get('error')}")
    return found


def test_the_sweep_through_mcp(owner_person, monkeypatch):
    client, token, user = owner_person
    monkeypatch.setattr(usda, "urlopen", _no_network)
    ids = seeded(user)
    calls = bases(user, ids)
    # A food entry's servings and grams are swept twice: beside their food's id, and on the base entry, which has
    # none, so even a good amount (2 servings, 100 grams) is refused there, never dropped.
    entry_for = {"servings": {"saved_food_id": ids["saved_food"], "servings": 2},
                 "grams": {"usda_fdc_id": 1001, "grams": 100}}
    no_id = {("entries", 0, "servings"): 2, ("entries", 0, "grams"): 100}
    today = users.today_for(user)
    # Every registered function, from the code: log and guide, and the five that no list holds now.
    schemas = {name: t["inputSchema"] for name, t in registered_tools(client).items()}
    assert set(schemas) == set(calls)
    # The import's rows are written once first, so a call with one bad row writes nothing: its other rows are there.
    assert tool_call(client, token, "import_entries", calls["import_entries"])["isError"] is False
    clear_call_log()
    findings, sent, started, used = [], 0, time.monotonic(), set()
    for function, schema in schemas.items():
        base = calls[function]
        cases = [(path, value) for path, spec in fields_of(schema) for value in values_for(function, path, spec, today)]
        for path, taken in objects_of(schema):
            keys = ["zz_unknown", *NEAR_KEYS.get(function if not path else path[-2], ())]
            cases += [((*path, key), 1) for key in keys if key not in taken]
        cases = [(path, value, False) for path, value in cases]
        if function == "log_food":
            cases += [(path, value, True) for path, good in no_id.items() for value in (good, *NUMBER_VALUES)]
        for path, value, plain in cases:
            path = row_path(base, path)
            if function == "log_food" and path[-1] in entry_for and path[0] == "entries" and not plain:
                arguments = put({**base, "entries": [entry_for[path[-1]]]}, path, value, ITEMS)
            else:
                arguments = put(base, path, value, ITEMS)
            before = rows_held()
            result = tool_call(client, token, function, arguments)
            after = rows_held()
            clear_call_log()
            sent += 1
            field = next(p for p in reversed(path) if isinstance(p, str))
            where = f"{function} {'.'.join(map(str, path))}={json.dumps(value)[:40]}"
            out = result.get("structuredContent") or {}
            text = (result.get("content") or [{}])[0].get("text", "")
            if any(w in text for w in LIBRARY_WORDS) or out.get("error") in CRASHES:
                findings.append(f"{where}: {text[:160]}")
                continue
            if result.get("isError"):
                problems = refusal_findings(result) + ([f"wrote {after - before} rows"] if after != before else [])
                findings += [f"{where}: {p}" for p in problems]
                continue
            # Not refused, by design: an import row's value of the right type fails its row in the result, and an
            # id that is no note is named under not_found.
            if function == "import_entries" and path[0] == "rows" and len(path) > 2:
                row = path[1] + 1
                if not any(p["row"] == row for p in out.get("failed", [])) or out["row_outcomes"]["failed"] < 1:
                    findings.append(f"{where}: the row was not refused")
                elif after != before:
                    findings.append(f"{where}: wrote {after - before} rows")
                continue
            if function == "owner_delete_feedback" and path[:1] == ("ids",) and value in out.get("not_found", []):
                continue
            why = taken_on_purpose(function, field, value)
            if why is None:
                findings.append(f"{where}: taken ({text[:120]})")
            used.add(why)
    took = time.monotonic() - started
    assert not findings, "\n".join(findings)
    assert used - {None} == {why for _, why in TAKEN}  # every row of the table is reached: none stands for nothing
    assert sent > 1000, sent
    print(f"\nthe sweep through /mcp: {sent} calls in {took:.0f} s")


# ---------------------------------------------------------------- the sweep through every form

FIELD = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.S)
ATTR = re.compile(r'([a-z_-]+)="([^"]*)"')
OPTION = re.compile(r"<option\b([^>]*)>", re.S)
# Hidden fields that are not the person's data, each with why a bad one is not refused:
NOT_DATA = {
    "csrf_token": "the CSRF check's own, swept by tests/test_csrf.py",
    "key": "a malformed form key makes an unkeyed write, by design (web.with_form_key, idempotency.md)",
    "updated_at": "missing or malformed, the write runs as it would without it (edit-history.md decision 10)",
    "next": "where the page goes after: only a path on this site is followed (web.local_path)",
    "card": "which Settings card posted (web.ZONE_CARD_FIELD), for where the answer lands: another is the zone's",
}
# Taken on purpose through a form: (address, field, a test of the value) -> the reason. Nothing else may be taken.
FORM_TAKEN = [
    (lambda address, field, v: field == "add_back_exercise_kcal",
     "A checkbox sends its value when ticked and nothing when not: any other value reads as not ticked."),
]
UNPOSTED = {"/sign-out", "/settings/export"}  # they write nothing through a service, as test_idempotency names them


def drawn_forms(page: str) -> list[tuple[str, list[dict]]]:
    """(action, fields) of each form on a rendered page: each field's name, type, drawn value, and inputmode."""
    out = []
    for form in FORM.findall(page):
        action = re.search(r'action="([^"]*)"', form).group(1).replace("&amp;", "&")
        fields = []
        for match in FIELD.finditer(form):
            tag, attrs = match.groups()
            a = dict(ATTR.findall(attrs))
            kind = a.get("type", tag)
            if "name" not in a or kind in ("submit", "button"):
                continue
            value = a.get("value", "")
            if tag == "select":
                body = form[match.end():form.index("</select>", match.end())]
                options = [(dict(ATTR.findall(o)), "selected" in o) for o in OPTION.findall(body)]
                value = next((o.get("value", "") for o, chosen in options if chosen),
                             next((o.get("value", "") for o, _ in options if o.get("value")), ""))
            if kind in ("radio", "checkbox") and "checked" not in attrs:
                if any(f["name"] == a["name"] for f in fields):
                    continue
                value = value if kind == "radio" else None
            fields.append({"name": a["name"], "type": kind, "value": value, "inputmode": a.get("inputmode", "")})
        out.append((action, fields))
    return out


def form_kind(field: dict) -> str:
    """How the sweep treats a drawn field: date, number or text."""
    if field["type"] == "date" or field["name"] in ("date", "target_date"):
        return "date"
    if field["type"] == "number" or field["inputmode"] in ("decimal", "numeric"):
        return "number"
    return "text"


def form_values(kind: str, name: str, today: date) -> list[str]:
    if kind == "date":  # a goal's date takes a goal's date values; an entry's, an entry's
        return [*date_values("set_goal" if name == "target_date" else "log_bodyweight", name, today), *FORM_TEXT]
    return [*FORM_NUMBER, *FORM_TEXT] if kind == "number" else FORM_TEXT


def form_id(action: str, fields: list[dict]) -> tuple:
    """A drawn form, once: its address with each date and id as a hole, and its fields' names. A day's forms drawn on
    two days are one form."""
    address = re.sub(r"/\d+(?=/|$)", "/{}", re.sub(r"/\d{4}-\d{2}-\d{2}(?=/|$)", "/{}", action.split("#")[0]))
    return address, tuple(sorted(f["name"] for f in fields))


def pattern_of(action: str) -> re.Pattern:
    """A template form's action as a pattern of the addresses it draws: each Jinja hole is one path part. The undo
    strip's action is all one hole: a restore, after a delete or an edit."""
    if re.fullmatch(r"\{\{.*?\}\}", action):
        return re.compile(r"/.+/restore")
    parts = re.split(r"\{\{.*?\}\}", action.split("#")[0])
    return re.compile("[^/?#]+".join(re.escape(p) for p in parts) + r"(?:#.*)?")


def template_actions() -> list[str]:
    return [re.search(r'action="([^"]*)"', f).group(1) for path in sorted(TEMPLATES.glob("*.html"))
            for f in FORM.findall(path.read_text("utf-8"))]


def form_seeds(uid: int, today: date) -> dict:
    """One of each thing a form edits, deletes or changes, through the services."""
    on = (today - timedelta(days=1)).isoformat()
    entries = {
        "bodyweight": services.log_bodyweight(uid, {"kg": 80, "date": on}, "claude")["id"],
        "measurement": services.log_measurement(uid, {"kind": "waist", "value": 84, "date": on}, "claude")["id"],
        "food_entry": services.log_food(uid, {"entries": [{"description": "Dal", "kcal": 180, "estimated": True}],
                                              "date": on}, "claude")["ids"][0],
        "cardio_entry": services.log_cardio(uid, {"exercise": "Treadmill Walk", "minutes": 60, "distance_km": 5,
                                                  "date": on}, "claude")["id"],
        "lift_session": services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5}],
                                                 "date": on}, "claude")["id"],
    }
    spare = services.log_bodyweight(uid, {"kg": 82, "date": (today - timedelta(days=2)).isoformat()}, "claude")["id"]
    saved = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 100}, "claude")["id"]
    services.set_goal(uid, {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                            "target_date": (today + timedelta(days=200)).isoformat()}, "claude")
    services.save_routine(uid, {"name": "Push day", "lifts": [{"exercise": "Bench Press", "sets": 3, "reps": 8}]},
                          "claude")
    return {"entries": entries, "spare": spare, "saved_food": saved, "on": on}


def form_base(address: str, fields: list[dict], today: date) -> dict:
    """A valid post of a drawn form: its drawn values, and what the page leaves for the person to type."""
    # The drawn key and updated_at are left out: a page drawn before an earlier post would answer entry_changed.
    data = {f["name"]: f["value"] for f in fields
            if f["value"] is not None and f["name"] not in ("csrf_token", "key", "updated_at")}
    typed = {"/weigh-in": {"kg": "80"}, "/measurement": {"kind": "waist", "value": "84"},
             "/food": {"description": "Poha", "kcal": "250"}, "/cardio": {"exercise": "Outdoor Walk", "minutes": "30"},
             "/foods": {"name": "Upma", "serving_label": "1 bowl", "kcal_per_serving": "200"},
             "/goals": {"target_value": "70", "start_value": "80", "distance_km": "5",
                        "target_date": (today + timedelta(days=365)).isoformat()}}
    # Each kind starts from what form_seeds logged: a lift aims up, a cardio time down.
    goal = {"lift": ("100", "Back Squat"), "cardio_best": ("25", "Treadmill Walk"), "measurement": ("80", None)}
    if data.get("kind") in goal:
        target, exercise = goal[data["kind"]]
        typed["/goals"].update(target_value=target, **({"exercise": exercise} if exercise else {}))
    for end, values in typed.items():
        if address.split("#")[0].endswith(end):
            data.update({k: v for k, v in values.items() if any(f["name"] == k for f in fields)})
    return data


def test_the_sweep_through_every_form(client, monkeypatch):
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.update_settings(uid, {"time_zone": "UTC"}, "web")
    today = today_of(only_user())
    ids = form_seeds(uid, today)
    pages = [f"/day/{today.isoformat()}", *[f"/entries/{k}/{i}/edit" for k, i in ids["entries"].items()], "/foods",
             f"/foods/{ids['saved_food']}/edit", "/routines", "/settings", "/settings/delete",
             *[f"/goals?kind={k}" for k in ("bodyweight", "lift", "cardio_best", "measurement")]]
    forms: dict[tuple, tuple[str, list[dict]]] = {}
    for page in pages:
        r = client.get(page)
        assert r.status_code == 200, page
        for action, fields in drawn_forms(r.text):
            forms.setdefault(form_id(action, fields), (action, fields))
    # The undo strips: drawn after a delete and after an edit.
    for path, data in ((f"/entries/bodyweight/{ids['spare']}/delete", {}),
                       (f"/entries/bodyweight/{ids['entries']['bodyweight']}/edit", {"kg": "79", "date": ids["on"]})):
        r = post(client, path, **data)
        assert r.status_code == 303, (path, r.text[:200])
        for action, fields in drawn_forms(client.get(r.headers["location"]).text):
            forms.setdefault(form_id(action, fields), (action, fields))
    drawn = [action for action, _ in forms.values()]
    for action in template_actions():  # every form a template holds was drawn and is swept: a new one joins
        if action not in UNPOSTED:
            assert any(pattern_of(action).fullmatch(a) for a in drawn), action
    clear_call_log()
    findings, posts, started, used = [], 0, time.monotonic(), set()
    for action, fields in forms.values():
        address = action.split("#")[0]
        if address in UNPOSTED:
            continue
        base = form_base(action, fields, today)
        for field in fields:
            if field["name"] in NOT_DATA:
                continue
            for value in form_values(form_kind(field), field["name"], today):
                before = rows_held()
                r = post(client, address, **{**base, field["name"]: value})
                after = rows_held()
                clear_call_log()
                posts += 1
                where = f"{address} {field['name']}={value!r}"
                if r.status_code >= 500:
                    findings.append(f"{where}: {r.status_code}")
                elif r.status_code in (400, 404, 409):
                    if after != before:
                        findings.append(f"{where}: refused, but wrote {after - before} rows")
                else:
                    why = next((why for test, why in FORM_TAKEN if test(address, field["name"], value)), None)
                    if why is None:
                        findings.append(f"{where}: taken ({r.status_code} {r.headers.get('location', '')})")
                    used.add(why)
    took = time.monotonic() - started
    assert not findings, "\n".join(findings)
    assert used - {None} == {why for _, why in FORM_TAKEN}  # every row of the table is reached
    # Each base post is valid: the writes that change a thing in place first, then the undos, the deletes last, and
    # Delete my account never (its own tests post it).
    ordered = sorted(forms.values(), key=lambda f: ("/delete" in f[0], "/restore" in f[0], f[0]))
    for action, fields in ordered:
        address = action.split("#")[0]
        if address in UNPOSTED or address == "/settings/delete":
            continue
        data = form_base(action, fields, today)
        r = post(client, address, **data)
        assert r.status_code == 303, (address, data, r.status_code, [t.text for t in tr.html_text(r.text)][-12:])
        clear_call_log()
    assert posts > 100, posts
    print(f"\nthe sweep through every form: {posts} posts in {took:.0f} s")


# ---------------------------------------------------------------- one test per decision, word for word

@pytest.fixture
def person(make_client):
    """(client, token, user) of the owner: the owner's three functions are checked here too."""
    client = make_client(owner_user_id=OWNER)
    token = sign_for(OWNER)
    account = tool_call(client, token, "whoami", {})["structuredContent"]["account_id"]
    assert "error" not in services.update_settings(account, {"time_zone": "UTC"}, "web")
    set_switch(account, "log_delete")
    with db.session_scope() as s:
        user = s.get(User, account)
        s.expunge(user)
    return client, token, user


def refused(person, function: str, arguments: dict) -> dict:
    client, token, _ = person
    result = tool_call(client, token, function, arguments)
    assert result["isError"] is True, result
    return result["structuredContent"]


def taken(person, function: str, arguments: dict) -> dict:
    client, token, _ = person
    result = tool_call(client, token, function, arguments)
    assert result["isError"] is False, result
    return result["structuredContent"]


def fields_taken(person, function: str) -> str:
    client, token, _ = person
    tool = registered_tools(client)[function]  # the five too, though no list holds them now
    return ", ".join(tool["inputSchema"]["properties"])


BODY_SIZE = ("value is in the kind's own unit: cm for waist, hips, chest, arm and thigh. Spotter takes no inches for a "
             "body size.")
SERVINGS = "For a saved food, send servings. For a USDA food, send grams."
FOOD_KCAL = "For the calories, send kcal."
MACHINE = "For the calories the machine or watch showed, send machine_kcal."
EDIT_CAL = "For a food entry's calories, send kcal. For a cardio entry's machine number, send machine_kcal."
DISTANCE = "Send distance_km or distance_mi, in the unit the person said."
SPEED = "Send speed_kmh or speed_mph, in the unit the person said."
MINUTES = "Send the time as minutes."
HEART = "Send the average heart rate as avg_hr."
INCLINE = "Send the incline as incline_pct."
SET_WEIGHT = "Send the weight in weight_kg or weight_lb, in the unit the person said."
BODY_WEIGHT = "Send the body weight in kg or lb, in the unit the person said."
LENGTH = "Spotter keeps body sizes in cm: there is no length unit to set."
# The contract's near-miss table, a row a key: (where, keys, the sentence).
NEAR_TABLE = [
    (("log_measurement", "edit_entry"), ("unit", "units"), BODY_SIZE),
    (("entry",), ("serving", "serving_count", "qty", "quantity", "amount"), SERVINGS),
    (("entry",), ("calories", "cal", "kcals"), FOOD_KCAL),
    (("log_cardio",), ("calories", "cal", "kcal"), MACHINE),
    (("edit_entry",), ("calories", "cal"), EDIT_CAL),
    (("log_cardio", "edit_entry"), ("distance",), DISTANCE),
    (("log_cardio", "edit_entry"), ("speed",), SPEED),
    (("log_cardio", "edit_entry"), ("duration", "time"), MINUTES),
    (("log_cardio", "edit_entry"), ("heart_rate", "hr"), HEART),
    (("log_cardio", "edit_entry"), ("incline",), INCLINE),
    (("set", "lift"), ("weight", "weight_lbs", "lbs", "lb", "kg", "weight_kgs"), SET_WEIGHT),
    (("log_bodyweight", "edit_entry"), ("weight", "weight_kg", "weight_lb", "lbs"), BODY_WEIGHT),
    (("update_settings",), ("length_unit",), LENGTH),
]
NEAR_CASES = [(where, key, sentence) for places, keys, sentence in NEAR_TABLE for where in places for key in keys]
TOP_BASE = {"log_measurement": {"kind": "waist", "value": 84}, "edit_entry": {"kind": "bodyweight", "entry_id": 1},
            "log_cardio": {"exercise": "Outdoor Run", "minutes": 30}, "log_bodyweight": {"kg": 80},
            "update_settings": {"time_zone": "UTC"}}
ITEM_CALLS = {  # an item's list: the call around it, the item, its place's words, the service's own fix
    "set": ("log_lifts", "sets", {"exercise": "Back Squat", "weight_kg": 100, "reps": 5}, "Set 1", "a set",
            "Use: exercise, weight_kg, weight_lb, reps, time_s, distance_m, exact."),  # exercise-list.md R6, R3
    "lift": ("save_routine", "lifts", {"exercise": "Bench Press", "sets": 3, "reps": 8}, "Lift 1", "a lift",
             "Use: exercise, sets, reps, weight_kg or weight_lb, time_s or minutes, distance_m, distance_km or "
             "distance_mi, incline_pct, speed_kmh or speed_mph, avg_hr, machine_kcal."),  # routines-and-programs.md C5b
    "entry": ("log_food", "entries", {"description": "Dal", "kcal": 180}, "Entry 1", "a food entry",
              "Use: description, meal, kcal, estimated, saved_food_id, servings, usda_fdc_id, grams. source is decided "
              "by the server."),
}


@pytest.mark.parametrize("where, key, sentence", NEAR_CASES, ids=[f"{w}-{k}" for w, k, _ in NEAR_CASES])
def test_each_near_miss_names_the_field_meant(person, where, key, sentence):
    """Decisions 2 and 3: the key named in the services' words, and the near miss's sentence after the fix."""
    if where in ITEM_CALLS:
        function, items, item, place, what, fix = ITEM_CALLS[where]
        extra = {"name": "Push day"} if function == "save_routine" else {}
        out = refused(person, function, {items: [{**item, key: 1}], **extra})
        assert (out["error"], out["message"], out["fix"]) == (
            "invalid_field", f"{place}: '{key}' is not a field of {what}.", f"{fix} {sentence}")
    else:
        out = refused(person, where, {**TOP_BASE[where], key: 1})
        assert (out["error"], out["message"], out["fix"]) == (
            "invalid_field", f"'{key}' is not a field of {where}.",
            f"{where} takes: {fields_taken(person, where)}. {sentence}")
    assert tr.check(out["message"], tr.MESSAGE) == [] and tr.check(out["fix"], tr.MESSAGE) == []


def test_an_item_s_unknown_key_reads_as_the_service_s_own_refusal(person):
    """The routine and food services refuse an unknown key in their own words (routines.md R5): through MCP the
    refusal is the same, with the near miss's sentence after the service's fix."""
    _, _, user = person
    out = refused(person, "save_routine", {"name": "Push day", "lifts": [{**ITEM_CALLS["lift"][2], "colour": "red"}]})
    own = services.save_routine(user.id, {"name": "Push day", "lifts": [{**ITEM_CALLS["lift"][2], "colour": "red"}]},
                                "claude")
    assert (out["message"], out["fix"]) == (own["message"], own["fix"])
    out = refused(person, "log_food", {"entries": [{**ITEM_CALLS["entry"][2], "serving": 2}]})
    own = services.log_food(user.id, {"entries": [{**ITEM_CALLS["entry"][2], "serving": 2}]}, "claude")
    assert out["message"] == own["message"] and out["fix"] == f"{own['fix']} {SERVINGS}"


def test_a_key_near_no_field_by_the_table_gets_the_nearest_by_spelling_or_none(person):
    near = refused(person, "log_bodyweight", {"kgs": 80})
    assert near["fix"] == f"log_bodyweight takes: {fields_taken(person, 'log_bodyweight')}. The nearest field is kg."
    far = refused(person, "log_bodyweight", {"kg": 80, "zz_unknown": 1})
    assert far["fix"] == f"log_bodyweight takes: {fields_taken(person, 'log_bodyweight')}."
    none = refused(person, "whoami", {"zz_unknown": 1})
    assert none["fix"] == "whoami takes no fields: send none."
    two = refused(person, "log_bodyweight", {"kg": 80, "aa": 1, "bb": 2})  # every unknown key is named
    assert [e["message"] for e in two["errors"]] == ["'aa' is not a field of log_bodyweight.",
                                                    "'bb' is not a field of log_bodyweight."]
    assert two["message"] == "2 problems. 'aa' is not a field of log_bodyweight. 'bb' is not a field of log_bodyweight."


# Decision 4: one grammar of a number as text.
NUMBER_TEXT_TAKEN = {" 80 ": 80, "+80": 80, "80.": 80, ".5": 0.5, "-3": -3, "0": 0}
NUMBER_TEXT_REFUSED = ["1_0", "8e1", "1,000", "٨٠", "８０", "nan", "inf", "", "   ", "+-1", "1.2.3",
                       ".", "+", "12abc", "0x10", "²"]


def test_the_number_grammar_on_both_sides():
    for text, number in NUMBER_TEXT_TAKEN.items():
        assert rules.to_float(text) == number, text
    for text in NUMBER_TEXT_REFUSED:
        assert rules.to_float(text) is None, text
    assert rules.to_float(True) is None and rules.to_float(False) is None and rules.to_float(80) == 80


@pytest.mark.parametrize("text, number", [(" 8 ", 8), ("+8", 8), ("8.", 8), (".5", 0.5)])
def test_text_in_the_grammar_is_its_number_through_mcp(person, text, number):
    out = taken(person, "log_measurement", {"kind": "sleep_hours", "value": text})
    assert out["measurement"]["value"] == number
    if number == int(number):  # a whole-number field takes it too
        taken(person, "update_settings", {"target_sessions_per_week": text})
        with db.session_scope() as s:
            assert s.get(User, person[2].id).target_sessions_per_week == number
    else:
        out = refused(person, "update_settings", {"target_sessions_per_week": text})
        assert out["message"] == f"target_sessions_per_week must be a whole number, not '{text}'."


@pytest.mark.parametrize("value", [True, False, *NUMBER_TEXT_REFUSED[:7]])
def test_a_bool_or_text_outside_the_grammar_is_refused_through_mcp(person, value):
    shown = json.dumps(value) if not isinstance(value, str) else f"'{value}'"
    out = refused(person, "log_measurement", {"kind": "sleep_hours", "value": value})
    assert (out["error"], out["message"], out["fix"]) == ("invalid_field", f"value must be a number, not {shown}.",
                                                          "Give value as a number.")
    whole = refused(person, "get_log", {"days": value})
    assert whole["message"] == f"days must be a whole number, not {shown}."
    assert whole["fix"] == "Give days as a whole number, from 1 to 14."


def test_the_same_grammar_on_a_page(client):
    sign_in(client)
    today = today_of(only_user()).isoformat()
    before = rows_held()
    for typed in ("٨٠", "８０", "1_0", "8e1"):
        assert post(client, f"/day/{today}/weigh-in", kg=typed).status_code == 400, typed
    assert rows_held() == before
    assert post(client, f"/day/{today}/weigh-in", kg=" 80 ").status_code == 303 and rows_held() == before + 1


# Decision 5: the boundary table, sent through the services the calls reach.
def test_the_boundary_table(make_user):
    uid = make_user(time_zone="UTC").id
    entry = lambda kcal: services.log_food(uid, {"entries": [{"description": "Dal", "kcal": kcal}]}, "claude")  # noqa
    for kcal in (-0.4, -0.01, 5000.4, 5000.5):
        assert entry(kcal)["error"] == "invalid_field", kcal
    assert entry(-0.4)["message"] == "Entry 1: kcal -0.4 is outside 0 to 5,000."
    assert entry(2.5)["entries"][0]["kcal"] == 3
    lb = services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_lb": -0.01, "reps": 5}]}, "claude")
    assert lb["error"] == "weight_out_of_range", lb
    cardio = lambda **sent: services.log_cardio(uid, {"exercise": "Outdoor Run", "minutes": 30, **sent}, "claude")  # noqa
    assert cardio(speed_mph=0.06)["error"] == "invalid_field" and cardio(distance_km=0.005)["error"] == "invalid_field"
    assert cardio(minutes=600.04)["error"] == "invalid_field" and cardio(minutes=0.95)["error"] == "invalid_field"
    saved = services.save_food(uid, {"name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 100}, "claude")
    servings = services.log_food(uid, {"entries": [{"saved_food_id": saved["id"], "servings": 0.125}]}, "claude")
    assert servings["entries"][0]["servings"] == 0.13
    assert services.log_bodyweight(uid, {"lb": 881.849}, "claude")["weigh_in"]["kg"] == 400
    assert (rules.keep(2.5, 0), rules.keep(-2.5, 0), rules.keep(0.125, 2), rules.keep(80.125, 2)) == (3, -3, 0.13, 80.13)
    assert rules.keep(1e30, 2) == 1e30 and rules.keep(2 ** 53 + 0.0, 2) == 2 ** 53  # too large to hold a fraction


def test_the_bounded_fix_says_a_round_field_takes_any_number(make_user):
    assert rules.bounded("kcal", -1)[1]["fix"] == "Give kcal as a number from 0 to 5,000: it is kept as a whole number."
    assert rules.bounded("avg_hr", 1.5)[1]["fix"] == "Give avg_hr as a whole number from 30 to 240."  # whole keeps
    for line in (rules.bounded("kcal", -1)[1]["fix"], web.FIELD_HELP[("food_entry", "kcal")]):
        assert tr.check(line, tr.MESSAGE) == [], line


# Decision 7: a date is YYYY-MM-DD only.
def test_a_date_in_another_form_is_refused_in_chat_in_the_import_and_on_the_pages(person):
    _, _, user = person
    yesterday = users.today_for(user) - timedelta(days=1)
    week = yesterday.isocalendar()
    for sent in (yesterday.strftime("%Y%m%d"), f"{week.year}-W{week.week:02d}-{week.weekday}"):
        out = refused(person, "log_bodyweight", {"kg": 80, "date": sent})
        assert (out["error"], out["message"], out["fix"]) == (
            "invalid_date", f"date '{sent}' is not a date.", "Give the date as YYYY-MM-DD, or leave it out for today.")
        out = refused(person, "get_log", {"date": sent})
        assert (out["error"], out["message"]) == ("invalid_date", f"date '{sent}' is not a date.")
        out = refused(person, "set_goal", {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                                           "target_date": sent})
        assert (out["error"], out["message"], out["fix"]) == (
            "invalid_date", f"target_date '{sent}' is not a date.", "Give the date as YYYY-MM-DD.")
        preview = taken(person, "import_entries", {"rows": [{"kind": "bodyweight", "date": sent, "weight": 80}],
                                                   "weight_unit": "kg"})
        assert [(f["row"], f["error"]) for f in preview["failed"]] == [(1, "invalid_date")]
    assert rules.to_date(f" {yesterday.isoformat()} ") == yesterday  # outer spaces forgiven, as before
    stamp = f"{yesterday.isoformat()}T07:12:00+05:30"  # a timestamp starts with the date, and goes on as before
    preview = taken(person, "import_entries", {"rows": [{"kind": "bodyweight", "date": stamp, "weight": 80}],
                                               "weight_unit": "kg"})
    assert preview["row_outcomes"]["failed"] == 0
    assert web.ISO_DAY is rules.ISO_DAY and web.parse_day("٢٠٢٦-١١-١٥") is None


# Decision 8: get_log's floor.
def test_get_log_refuses_a_date_before_the_oldest_day_the_account_can_hold(person):
    client, token, user = person
    with db.session_scope() as s:  # a fixed account age: the floor is the contract's example
        s.get(User, user.id).created_at = db.utcnow().replace(year=2026, month=11, day=16, hour=3)
    assert taken(person, "get_log", {"date": "2024-11-14", "days": 14})["to"] == "2024-11-14"  # a window starts before
    out = refused(person, "get_log", {"date": "2024-11-13"})
    assert (out["error"], out["message"], out["fix"]) == (
        "date_too_old", "date 2024-11-13 is before 2024-11-14, the oldest day this account can hold.",
        "Give a date from 2024-11-14 to today, or leave it out for today.")
    out = refused(person, "get_log", {"date": "0001-01-01", "days": 7})
    assert out["message"] == "date 0001-01-01 is before 2024-11-14, the oldest day this account can hold."
    assert tr.check(out["message"], tr.MESSAGE) == [] and tr.check(out["fix"], tr.MESSAGE) == []


# Decisions 9 and 10: the owner's ids and text.
@pytest.mark.parametrize("function", ["owner_calls", "owner_feedback"])
def test_the_owner_s_account_id_email_and_request_id_are_checked(person, function):
    for sent in (2 ** 63, 0, 2 ** 31):
        out = refused(person, function, {"account_id": sent})
        assert (out["error"], out["message"], out["fix"]) == (
            "invalid_field", f"account_id {sent} is not an account id.",
            "Give an account's id as a line gives it: a whole number from 1.")
    out = refused(person, function, {"email": "a\u0000b@example.com"})
    assert (out["message"], out["fix"]) == ("email holds a control character.",
                                            "Remove control characters from email; newlines and tabs are fine.")
    out = refused(person, function, {"email": "x" * 321})
    assert (out["message"], out["fix"]) == ("email is 321 characters; the most is 320.",
                                            "Shorten email to 320 characters or fewer.")
    assert taken(person, function, {"email": "x" * 320})  # the column's length is taken
    if function == "owner_calls":
        out = refused(person, function, {"request_id": "r" * 17})
        assert out["message"] == "request_id is 17 characters; the most is 16."
        assert refused(person, function, {"request_id": "a\u0000b"})["message"] == "request_id holds a control character."
    assert owner.filters({"account_id": 7}, ("account_id",)) == ({"account_id": 7}, None)


# Decision 11: the words of a schema refusal, a row each.
SCHEMA_ROWS = [
    ("log_lifts", {}, "missing_field", "sets is missing.", "Give sets."),
    ("log_lifts", {"sets": [{"exercise": "Back Squat", "weight_kg": 100}]}, "missing_field", "Set 1: reps is missing.",
     "Give reps on set 1."),
    ("log_bodyweight", {"kg": "12abc"}, "invalid_field", "kg must be a number, not '12abc'.", "Give kg as a number."),
    ("import_entries", {"rows": [{"kind": "lift_session", "date": "2026-01-01", "sets": [
        {"exercise": "Deadlift", "weight": 100, "reps": 5}, {"exercise": "Deadlift", "weight": True, "reps": 5}]}]},
     "invalid_field", "Row 1, set 2: weight must be a number, not true.", "Give weight on row 1, set 2 as a number."),
    ("get_log", {"days": 2.5}, "invalid_field", "days must be a whole number, not 2.5.",
     "Give days as a whole number, from 1 to 14."),
    ("log_food", {"entries": [{"description": "Dal", "saved_food_id": [1]}]}, "invalid_field",
     "Entry 1: saved_food_id must be a whole number, not [1].", "Give saved_food_id on entry 1 as a whole number."),
    ("log_bodyweight", {"kg": 80, "allow_repeat": "perhaps"}, "invalid_field",
     "allow_repeat must be true or false, not 'perhaps'.", "Send allow_repeat as true or false, or leave it out."),
    ("log_lifts", {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}], "notes": 12}, "invalid_field",
     "notes must be text, not 12.", "Give notes as text."),
    ("log_lifts", {"sets": "not a list"}, "invalid_field", "sets must be a list, not 'not a list'.",
     "Send sets as a list."),
    ("save_routine", {"name": "Push day", "lifts": ["x"]}, "invalid_field", "Lift 1: it must be an object of named fields.",
     "Send each item with its named fields: exercise, sets, reps, weight_kg, weight_lb, time_s, minutes, distance_m, "
     "distance_km, distance_mi, incline_pct, speed_kmh, speed_mph, avg_hr, machine_kcal."),  # C5b: every kind's
    ("owner_calls", {"hours": 0}, "invalid_field", "hours 0 is outside 1 to 2,160.", "Give hours from 1 to 2,160."),
    ("owner_delete_feedback", {"ids": []}, "invalid_field", "ids holds 0 items: it takes 1 to 100.",
     "Send 1 to 100 items in ids."),
    ("owner_delete_feedback", {"ids": ["abc"]}, "invalid_field", "ids item 1 must be a whole number, not 'abc'.",
     "Give ids item 1 as a whole number."),
    ("whoami", {"zz": 1}, "invalid_field", "'zz' is not a field of whoami.", "whoami takes no fields: send none."),
]


@pytest.mark.parametrize("function, arguments, code, message, fix", SCHEMA_ROWS,
                         ids=[f"{r[0]}-{r[3][:24]}" for r in SCHEMA_ROWS])
def test_each_schema_failure_is_spotter_s_own_refusal(person, function, arguments, code, message, fix):
    client, token, _ = person
    result = tool_call(client, token, function, arguments)
    out = result["structuredContent"]
    assert result["isError"] is True and (out["error"], out["message"], out["fix"]) == (code, message, fix)
    assert json.loads(result["content"][0]["text"]) == out  # the same as compact JSON text, as every refusal
    assert tr.check(message, tr.MESSAGE) == [] and tr.check(fix, tr.MESSAGE) == []


def test_anything_else_pydantic_refuses_is_named_by_its_type():
    schema = {"properties": {"odd": {"description": "No type."}, "n": {"type": "number"}}}
    out = mcp_server._problem("f", schema, {"odd": 1}, {"type": "weird", "loc": ("odd",), "input": 1})
    assert (out["message"], out["fix"]) == ("odd 1 is not what odd takes.", "Give odd as what its description says.")
    out = mcp_server._problem("f", schema, {"n": "x"}, {"type": "weird", "loc": ("n",), "input": "x"})
    assert out["message"] == "n must be a number, not 'x'."  # a known type has its own words, whatever pydantic says


def test_two_problems_and_an_unknown_key_come_back_as_one_refusal(person):
    out = refused(person, "log_bodyweight", {"kg": True, "zz": 1})
    assert out["message"] == "2 problems. 'zz' is not a field of log_bodyweight. kg must be a number, not true."
    assert [e["error"] for e in out["errors"]] == ["invalid_field", "invalid_field"]
    missing = refused(person, "log_measurement", {"zz": 1})
    assert missing["error"] == "invalid_field" and [e["error"] for e in missing["errors"]] == [
        "invalid_field", "missing_field", "missing_field"]


# Decision 12: the row and the order.
def test_a_schema_refusal_writes_one_call_log_row_and_no_other(person):
    client, token, user = person
    clear_call_log()
    before = rows_held()
    out = refused(person, "log_lifts", {"sets": "x", "zz": 1})
    with db.session_scope() as s:
        [row] = list(s.scalars(select(CallLog)))
        assert (row.function, row.ok, row.error_code, row.outcome, row.step, row.tried, row.caller) == (
            "log_lifts", False, out["error"], "refused", "check", "other[1] sets", "claude")
        assert common._counted(s, user.id, common.WRITE_FUNCTIONS)[0] == 1  # it counts against the write limit
    assert rows_held() == before


def test_a_schema_refusal_meets_the_order_every_call_meets(person, make_client):
    client, token, user = person
    other = sign(claims(sub="user_01OTHER"))
    assert refused((client, other, None), "owner_calls", {"hours": "x"})["error"] == "not_allowed"  # the owner check
    set_switch(user.id, "log")
    assert refused(person, "delete_entry", {"kind": 5, "entry_id": 1})["error"] == "not_allowed"  # the switch
    set_switch(user.id, "log_delete")
    assert refused(person, "log_bodyweight", {"kg": True, "key": "bad"})["error"] == "invalid_key"  # the key's shape
    with db.session_scope() as s:
        s.add_all(CallLog(user_id=user.id, function="get_log", started_at=db.utcnow(), ms=1, ok=True, result_bytes=1)
                  for _ in range(common.READS_LIMIT))
    assert refused(person, "get_log", {"days": "x"})["error"] == "too_many_reads"  # the read limit
    assert tool_call(client, token, "zz_no_function", {})["isError"] is True  # an unknown function: the library's
    with db.session_scope() as s:
        assert s.scalar(select(func.count()).select_from(CallLog).where(CallLog.function == "zz_no_function")) == 0


# Decision 13: a range with its low end left out.
def test_a_lift_goal_at_0_says_more_than_0_in_kg_and_in_lb(person):
    _, _, user = person
    later = (users.today_for(user) + timedelta(days=60)).isoformat()
    lift = {"kind": "lift", "exercise": "Back Squat", "target_value": 0, "start_value": 100, "target_date": later}
    kg = refused(person, "set_goal", lift)
    assert (kg["message"], kg["fix"]) == ("target_value 0 must be more than 0 and at most 666.67.",
                                          "Give target_value in kg, more than 0 and at most 666.67, for a lift goal.")
    lb = refused(person, "set_goal", {**lift, "weight_unit": "lb"})
    assert (lb["message"], lb["fix"]) == ("target_value 0 lb must be more than 0 and at most 1,469.75 lb.",
                                          "Give target_value in lb, more than 0 and at most 1,469.75, for a lift goal.")
    for text in (kg["message"], kg["fix"], lb["message"], lb["fix"]):
        assert tr.check(text, tr.MESSAGE) == [], text


# Decision 14: help text matches its check.
def test_the_page_lines_match_their_checks():
    assert web.FIELD_HELP[("food_entry", "kcal")] == ("That kcal was not saved: give a number from 0 to 5,000. It is kept "
                                                       "as a whole number.")
    assert web.FIELD_HELP[("cardio_entry", "notes")] == "Keep the notes to 2,000 characters or fewer."
    assert web.GOAL_ERROR_WORDS[("date_too_far", "target_date")] == "Choose a day at most 5 years from today."
    words = web.goal_error_words({"error": "invalid_field", "field": "target_value"}, "cardio_best", distance_km=5)
    assert words == "Give the time you aim for in minutes, from 5 to 600."
    assert web.goal_error_words({"error": "invalid_field", "field": "start_value"}, "cardio_best") == \
        "Give your time now in minutes, from 1 to 600."  # no distance yet
    assert web.typed_distance({"distance_mi": "3.10686"}) == pytest.approx(5, abs=0.001)
    assert web.typed_distance({"distance_km": "400"}) is None  # a distance set_goal refuses: 1 to 600
    lines = [web.FIELD_HELP[("food_entry", "kcal")], web.FIELD_HELP[("cardio_entry", "notes")],
             web.GOAL_ERROR_WORDS[("date_too_far", "target_date")], words,
             "Those notes were not saved: keep them to 2,000 characters or fewer."]
    for line in lines:
        assert tr.check(line, tr.PAGE) == [], line


def test_the_lift_session_notes_line_on_the_edit_page(client):
    sign_in(client)
    uid = only_user().id
    session = services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]}, "claude")
    r = post(client, f"/entries/lift_session/{session['id']}/edit", notes="x" * (rules.TEXT_MAX + 1))
    assert r.status_code == 400 and "Those notes were not saved: keep them to 2,000 characters or fewer." in r.text


# Decision 15: a goal's date at most 5 years on.
@pytest.mark.parametrize("today, latest", [(date(2028, 2, 28), date(2033, 2, 28)), (date(2028, 2, 29), date(2033, 2, 28)),
                                           (date(2026, 11, 16), date(2031, 11, 16))])
def test_a_goal_s_latest_date_is_5_calendar_years_on(person, monkeypatch, today, latest):
    assert rules.goal_latest(today) == latest
    monkeypatch.setattr(users, "today_for", lambda user: today)
    goal = {"kind": "bodyweight", "target_value": 70, "start_value": 80}
    assert taken(person, "set_goal", {**goal, "target_date": latest.isoformat()})["goal"]["target_date"] == \
        latest.isoformat()
    after = latest + timedelta(days=1)
    out = refused(person, "set_goal", {**goal, "target_value": 69, "target_date": after.isoformat()})
    assert (out["error"], out["message"], out["fix"], out["field"]) == (
        "date_too_far", f"target_date {after.isoformat()} is more than 5 years after today, {today.isoformat()}.",
        f"Give a target date after today, at most 5 years on: {latest.isoformat()} at the latest.", "target_date")
    assert tr.check(out["message"], tr.MESSAGE) == [] and tr.check(out["fix"], tr.MESSAGE) == []


def test_a_goal_dated_later_already_stays_and_an_edit_that_sends_no_date_is_taken(person):
    _, _, user = person
    today = users.today_for(user)
    made = taken(person, "set_goal", {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                                      "target_date": (today + timedelta(days=30)).isoformat()})
    far = today + timedelta(days=3650)
    with db.session_scope() as s:
        s.execute(update(Goal).where(Goal.id == made["id"]).values(target_date=far))
    edited = taken(person, "set_goal", {"goal_id": made["id"], "text": "Ten years on"})
    assert edited["goal"]["target_date"] == far.isoformat()


def test_the_goals_page_says_the_5_years(client):
    sign_in(client)
    today = today_of(only_user())
    far = (rules.goal_latest(today) + timedelta(days=1)).isoformat()
    r = post(client, "/goals", kind="bodyweight", target_value="70", start_value="80", target_date=far)
    assert r.status_code == 400 and "Choose a day at most 5 years from today." in r.text


# Decision 16: a cardio time goal takes the speeds log_cardio takes.
def test_a_cardio_time_goal_takes_the_speeds_log_cardio_takes(person):
    _, _, user = person
    later = (users.today_for(user) + timedelta(days=60)).isoformat()
    goal = {"kind": "cardio_best", "exercise": "Outdoor Run", "target_date": later}
    assert goals.time_limits(0.1) == (1, 60) and goals.time_limits(300) == (300, 600) and goals.time_limits(5) == (5, 600)
    assert goals.time_limits(0.123) == (1, 73.8) and goals.time_limits(None) == (1, 600)
    assert taken(person, "set_goal", {**goal, "distance_km": 0.1, "target_value": 59, "start_value": 60})
    out = refused(person, "set_goal", {**goal, "distance_km": 0.1, "target_value": 60.1, "start_value": 59})
    assert out["message"] == "target_value 60.1 is outside 1 to 60 minutes for 0.1 km."
    assert refused(person, "set_goal", {**goal, "distance_km": 0.1, "target_value": 0.9, "start_value": 59})[
        "message"] == "target_value 0.9 is outside 1 to 60 minutes for 0.1 km."
    out = refused(person, "set_goal", {**goal, "distance_km": 0.2, "target_value": 59, "start_value": 121})
    assert out["message"] == "start_value 121 is outside 1 to 120 minutes for 0.2 km."  # the start too
    assert taken(person, "set_goal", {**goal, "distance_km": 300, "target_value": 300, "start_value": 400})
    out = refused(person, "set_goal", {**goal, "distance_km": 300, "target_value": 299.9, "start_value": 400})
    assert (out["message"], out["fix"]) == (
        "target_value 299.9 is outside 300 to 600 minutes for 300 km.",
        "Give target_value as the minutes for the goal's distance, 300 to 600 for 300 km: a speed from 0.1 to 60 km/h, "
        "as a cardio entry takes.")
    out = refused(person, "set_goal", {**goal, "distance_km": 5, "target_value": 1, "start_value": 30})
    assert (out["message"], out["fix"]) == (
        "target_value 1 is outside 5 to 600 minutes for 5 km.",
        "Give target_value as the minutes for the goal's distance, 5 to 600 for 5 km: a speed from 0.1 to 60 km/h, as "
        "a cardio entry takes.")
    refused_distance = refused(person, "set_goal", {**goal, "distance_km": 500, "target_value": 1, "start_value": 30})
    assert [e["field"] for e in refused_distance["errors"]] == ["distance_km"]  # the values get 1 to 600 alone
    edit = taken(person, "set_goal", {**goal, "distance_km": 5, "target_value": 25, "start_value": 30})
    out = refused(person, "set_goal", {"goal_id": edit["id"], "target_value": 4})  # an edit: from the goal's own
    assert out["errors"][0]["message"] == "target_value 4 is outside 5 to 600 minutes for 5 km."
    for text in (out["message"], out["fix"]):
        assert tr.check(text, tr.MESSAGE) == [], text


def test_a_cardio_time_goal_s_words_are_in_the_person_s_units(person):
    _, _, user = person
    assert "error" not in services.update_settings(user.id, {"distance_unit": "mi"}, "web")
    later = (users.today_for(user) + timedelta(days=60)).isoformat()
    out = refused(person, "set_goal", {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 5,
                                       "target_value": 1, "start_value": 30, "target_date": later})
    with db.session_scope() as s:
        distance = formatting.show(5, "km", units=users.units_for(s.get(User, user.id)))
    assert out["message"] == f"target_value 1 is outside 5 to 600 minutes for {distance}." and distance.endswith(" mi")
    # The speeds moved inward at mph's shown step, as log_cardio's own mph refusal says them (cardio.py).
    assert out["fix"].endswith(f"for {distance}: a speed from 0.1 to 37.2 mph, as a cardio entry takes.")


# Decisions 17 and 18: the descriptions (above) and the can-and-cant lines.
CAN_AND_CANT_LINES = [
    "- A date is sent as YYYY-MM-DD. No entry can be dated after today, or more than 2 years back. A goal's\n"
    "  target date is after today, and at most 5 years on.",
    "| Any other form of a date, such as 20261116 or 2026-W01-1 | Refused |",
    "## What a call can send",
    "- **Only the fields a function lists.** Any other field is refused with `invalid_field`, and nothing is\n"
    "  written. Its fix lists the fields the function takes, and names the nearest one when there is one.",
    "- **Numbers as numbers.** true and false are never a number. Text that holds a plain number, such as\n"
    "  \"80\", is read as that number. Other text, such as 1_0, 8e1 or digits of another script, is refused.",
    "- **A refused call writes nothing.** Its fix says what to send instead.",
]


def test_can_and_cant_says_the_input_rules():
    text = mcp_server.CAN_AND_CANT.read_text(encoding="utf-8").replace("\r\n", "\n")
    for line in CAN_AND_CANT_LINES:
        assert line in text, line
        assert tr.check(" ".join(line.split()), tr.DOC) == [], line
    assert text.index("## What \"today\" means") < text.index("## What a call can send") < text.index("## Feedback")


# The owner's questions 1 to 3, as decided.
STRICT = [("log_lifts", "allow_repeat"), ("log_lifts", "new_session"), ("update_settings", "add_back_exercise_kcal"),
          ("get_goals", "include_closed"), ("restore_entry", "undo_edit"), ("owner_calls", "no_account"),
          ("owner_calls", "failed_only"), ("owner_calls", "all_accounts"), ("log_bodyweight", "allow_repeat"), ("log_measurement", "allow_repeat"),
          ("log_food", "allow_repeat"), ("log_cardio", "allow_repeat")]


@pytest.mark.parametrize("function, field", STRICT)
@pytest.mark.parametrize("loose", ["yes", 1])
def test_true_or_false_is_strict_on_every_field(person, function, field, loose):
    out = refused(person, function, {field: loose})
    shown = "'yes'" if loose == "yes" else "1"
    assert f"{field} must be true or false, not {shown}." in [e["message"] for e in out["errors"]]


def test_estimated_is_strict_too(person):
    out = refused(person, "log_food", {"entries": [{"description": "Dal", "kcal": 180, "estimated": "yes"}]})
    assert out["message"] == "Entry 1: estimated must be true or false, not 'yes'."


def test_a_whole_number_is_quoted_with_no_point_zero(person):
    assert (rules.as_sent(0.0), rules.as_sent(-100.0), rules.as_sent(2.5), rules.as_sent(1e20)) == ("0", "-100", "2.5",
                                                                                                    "1e+20")
    assert refused(person, "log_bodyweight", {"kg": -100})["message"] == "kg -100 is outside 20 to 400."


def test_a_name_of_only_invisible_characters_is_missing(person):
    assert rules.required_text("​⁠ ", "name")[1]["error"] == "missing_field"
    assert rules.required_text("​Roti", "name")[0] == "​Roti"
    out = refused(person, "save_food", {"name": "​", "serving_label": "1 roti", "kcal_per_serving": 100})
    assert out["error"] == "missing_field"
    out = refused(person, "save_routine", {"name": "﻿", "lifts": [ITEM_CALLS["lift"][2]]})
    assert out["error"] == "missing_field"


# ---------------------------------------------------------------- decision 19: the two eval cases

def test_the_two_eval_cases_load_last_and_judge_both_ways():
    cases = judge.load_cases()
    # data-on-request.md's eight follow (its decision 20), then function-list.md's G1 cases: counted from the eight
    at = [c["id"] for c in cases].index("data_basketball")
    assert [c["id"] for c in cases[at - 2:at]] == ["waist_in_inches", "machine_calories"]
    waist, machine = (judge.resolve(c, {"today": "2026-11-16"}) for c in cases[at - 2:at])
    assert judge.case_problems(waist) == [] and judge.case_problems(machine) == []
    logged = [{"name": "log_measurement", "arguments": {"kind": "waist", "value": 81.28}, "is_error": False}]
    row = {"measurement": [{"id": 1, "kind": "waist", "value": 81.28, "deleted_at": None}]}
    assert judge.judge(waist, logged, "Logged your waist as 81.28 cm.", row) == []
    assert judge.judge(waist, [], "Is that 32 inches? Shall I log it as 81.28 cm?", {"measurement": []}) == []
    as_sent = [{"name": "log_measurement", "arguments": {"kind": "waist", "value": 32}, "is_error": False}]
    assert judge.judge(waist, as_sent, "Logged your waist as 32 cm.",
                       {"measurement": [{"id": 1, "kind": "waist", "value": 32, "deleted_at": None}]})
    good = [{"name": "log_cardio", "arguments": {"exercise": "Treadmill Walk", "minutes": 30, "machine_kcal": 250},
             "is_error": False}]
    rows = {"cardio_entry": [{"id": 1, "machine_kcal": 250, "minutes": 30, "deleted_at": None}]}
    assert judge.judge(machine, good, "Logged 30 minutes on the treadmill, with the machine's 250 kcal.", rows) == []
    dropped = [{"name": "log_cardio", "arguments": {"exercise": "Treadmill Walk", "minutes": 30}, "is_error": False}]
    assert judge.judge(machine, dropped, "Logged 30 minutes.",
                       {"cardio_entry": [{"id": 1, "machine_kcal": None, "minutes": 30, "deleted_at": None}]})


def test_an_edit_that_sends_the_goal_s_own_distance_again_is_not_a_change(person):
    """Decision 5: a distance is kept halves away from zero, and the edit's same-distance test keeps it the same way,
    so 2.675 sent again is the distance the goal was set with (round() would make it 2.67, and another distance)."""
    _, _, user = person
    later = (users.today_for(user) + timedelta(days=60)).isoformat()
    made = taken(person, "set_goal", {"kind": "cardio_best", "exercise": "Outdoor Run", "distance_km": 2.675,
                                      "target_value": 15, "start_value": 20, "target_date": later})
    assert made["goal"]["distance_km"] == 2.68
    assert taken(person, "set_goal", {"goal_id": made["id"], "distance_km": 2.675, "text": "Same"})["goal"]["text"] == \
        "Same"


def test_a_schema_refusal_with_a_key_keeps_no_key_and_answers_the_same_again(person):
    """Decision 12: a keyed write refused at the check frees its key, as a service's refusal does: sent again, the
    same refusal comes back, and no key row is left."""
    from app.models import WriteKey
    sent = {"kg": True, "key": "retry-input-0001"}
    first, again = refused(person, "log_bodyweight", sent), refused(person, "log_bodyweight", sent)
    assert first == again and first["message"] == "kg must be a number, not true."
    with db.session_scope() as s:
        assert s.scalar(select(func.count()).select_from(WriteKey)) == 0


# ---------------------------------------------------------------- the fix round, each red without its fix

# A minus weight below 0 says R6's words since exercise-list.md: help is on the two assisted lifts only.
MINUS = "Lift 1: a minus weight is help, on Assisted Pull-Up or Assisted Dip only."


@pytest.mark.parametrize("weight, message", [
    ({"weight_kg": -0.001}, MINUS),
    ({"weight_kg": 500.004}, "Lift 1: weight 500.004 kg is outside 0 to 500 kg."),
    ({"weight_lb": -0.01}, MINUS)])
def test_a_routine_lift_s_weight_is_checked_as_sent(person, weight, message):
    """Decision 5 in routines.py: the range on the weight unrounded, then the keep (was: kept at 2 dp, then 0 kg)."""
    _, _, user = person
    lift = {"exercise": "Bench Press", "sets": 3, "reps": 8, **weight}
    out = services.save_routine(user.id, {"name": "Push day", "lifts": [lift]}, "claude")
    assert (out["error"], out["message"]) == ("weight_out_of_range", message)
    assert refused(person, "save_routine", {"name": "Push day", "lifts": [lift]})["message"] == message


def test_servings_and_grams_with_no_food_id_are_refused_never_dropped(person):
    _, _, user = person
    sent = {"entries": [{"description": "Dal", "kcal": 100, "servings": -5, "grams": 99999}]}
    before = rows_held()
    for out in (services.log_food(user.id, sent, "claude"), refused(person, "log_food", sent)):
        assert [(e["error"], e["message"], e["fix"]) for e in out["errors"]] == [
            ("invalid_field", "Entry 1: servings goes only with saved_food_id.",
             "Send servings with a saved food's id, or leave it out and send kcal for the amount eaten."),
            ("invalid_field", "Entry 1: grams goes only with usda_fdc_id.",
             "Send grams with a USDA food's id, or leave it out and send kcal for the amount eaten.")]
        for e in out["errors"]:
            assert tr.check(e["message"], tr.MESSAGE) == [] and tr.check(e["fix"], tr.MESSAGE) == []
    assert rows_held() == before
    good = {"entries": [{"description": "Dal", "kcal": 100, "servings": 2}]}  # even a good amount, with no food
    assert refused(person, "log_food", good)["message"] == "Entry 1: servings goes only with saved_food_id."


def test_a_note_of_only_invisible_characters_is_missing(person):
    _, _, user = person
    out = services.send_feedback(user.id, {"text": "\u200b\u3164"})
    assert (out["error"], out["message"]) == ("missing_field", "text is missing.")
    with db.session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Feedback)) == 0


def test_a_refused_target_on_an_edit_is_one_problem(person):
    _, _, user = person
    later = (users.today_for(user) + timedelta(days=60)).isoformat()
    goal = taken(person, "set_goal", {"kind": "bodyweight", "target_value": 70, "start_value": 80, "target_date": later})
    out = refused(person, "set_goal", {"goal_id": goal["id"], "target_value": 5})
    assert out["message"] == "target_value 5 is outside 20 to 400." and len(out["errors"]) == 1
    nothing = refused(person, "set_goal", {"goal_id": goal["id"]})
    assert nothing["error"] == "nothing_to_change"  # with nothing sent, still nothing to change


@pytest.mark.parametrize("blank", ["\u3164", "\u2800", "\u115f\u1160", "\uffa0", "\u0301", "\u20dd", "\u200b "])
def test_blank_letters_and_bare_marks_are_invisible(person, blank):
    assert rules.visible(blank) is False and rules.visible("e\u0301") is True and rules.visible("\u0915") is True
    out = refused(person, "save_food", {"name": blank, "serving_label": "\u2800", "kcal_per_serving": 100})
    assert [e["message"] for e in out["errors"]] == ["name is missing.", "serving_label is missing."]


def test_a_number_too_large_for_a_float_is_not_a_number(person):
    out = refused(person, "log_bodyweight", {"kg": "1" * 400})
    assert out["message"] == f"kg must be a number, not '{'1' * 79}\u2026'." and "inf" not in out["message"]
    assert out["fix"] == "Give kg as a number."


@pytest.mark.parametrize("days", [20, 0, "20"])
def test_days_out_of_range_says_the_range(person, days):
    out = refused(person, "get_log", {"days": days})
    shown = int(days)
    assert (out["error"], out["message"], out["fix"]) == ("invalid_field", f"days {shown} is outside 1 to 14.",
                                                          "Give days as a whole number from 1 to 14.")
    assert tr.check(out["message"], tr.MESSAGE) == []


def test_an_unknown_key_s_control_characters_are_shown_escaped(person):
    out = refused(person, "log_bodyweight", {"kg": 80, "a\nb\u200bc": 1})
    assert out["message"] == "'a\\nb\\u200bc' is not a field of log_bodyweight."
    nested = refused(person, "log_lifts", {"sets": [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5, "x\ty": 1}]})
    assert nested["message"] == "Set 1: 'x\\ty' is not a field of a set."
