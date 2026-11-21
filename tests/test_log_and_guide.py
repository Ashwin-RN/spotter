"""Log and guide (contract log-and-guide.md).

Package L0: the five log functions' words as named constants in app/mcp_schemas.py, an argument model for each,
misfit's form for an argument model, and the step each function takes from its arguments to its service. Nothing
published moves: tests/test_feedback.py's PUBLISHED hashes and INSTRUCTIONS_AS_BUILT hold as they are.

Package L1a: app/log_shape.py (the kinds, their words, route, the conversions of decision 51, said_as_sent, the kind
map of decision 52, log's words) and app/guide.py (each topic's answer), with LogPartIn and GuideResult in
app/mcp_schemas.py. Neither function is registered here, so nothing published moves either."""

import ast
import hashlib
import json
import os
import re
import subprocess
import sys

import pytest

from app import guide, log_shape, mcp_schemas, mcp_server, models, rules, services
from app.auth import JwksTokenVerifier, UserinfoClient
from app.config import ROOT
from app.log_shape import KINDS, route, said_as_sent
from app.mcp_schemas import LOG_ARGUMENTS
from app.mcp_server import LOG_STEPS, misfit, misfit_model, read_arguments, schema_refusal

from . import text_rules as tr
from .conftest import FakeProvider, claims, settings, sign, whoami
from .test_feedback import INSTRUCTIONS_AS_BUILT, PUBLISHED, compact
from .test_mcp import failed, one_user, registered_tools, spy_on_services, tool_list

FIVE = ("log_lifts", "log_bodyweight", "log_measurement", "log_food", "log_cardio")
OWNER = "user_01OWNER"
DAY = "2026-01-02"  # a fixed date: the spy never reads it, and no test here reads the real date


@pytest.fixture(scope="module")
def server():
    """A server built as the app builds one, for its registered functions. Nothing is called on it."""
    provider = FakeProvider()
    built = settings()
    return mcp_server.build_server(built, JwksTokenVerifier(built, provider.fetch), UserinfoClient(built, provider.fetch))


# ---------------------------------------------------------------- the words, written once

S = mcp_schemas
WORDS = {
    "log_lifts": (S.LOG_LIFTS_DESCRIPTION, {
        "sets": S.LIFTS_SETS_DESCRIPTION, "date": S.LIFTS_DATE_DESCRIPTION, "notes": S.LIFTS_NOTES_DESCRIPTION,
        "allow_repeat": S.LIFTS_ALLOW_REPEAT_DESCRIPTION, "new_session": S.LIFTS_NEW_SESSION_DESCRIPTION,
        "key": S.KEY_DESCRIPTION}),
    "log_bodyweight": (S.LOG_BODYWEIGHT_DESCRIPTION, {
        "kg": S.BODYWEIGHT_KG_DESCRIPTION, "lb": S.BODYWEIGHT_LB_DESCRIPTION, "date": S.BODYWEIGHT_DATE_DESCRIPTION,
        "allow_repeat": S.BODYWEIGHT_ALLOW_REPEAT_DESCRIPTION, "key": S.KEY_DESCRIPTION}),
    "log_measurement": (S.LOG_MEASUREMENT_DESCRIPTION, {
        "kind": S.MEASUREMENT_KIND_DESCRIPTION, "value": S.MEASUREMENT_VALUE_DESCRIPTION,
        "date": S.MEASUREMENT_DATE_DESCRIPTION, "allow_repeat": S.MEASUREMENT_ALLOW_REPEAT_DESCRIPTION,
        "key": S.KEY_DESCRIPTION}),
    "log_food": (S.LOG_FOOD_DESCRIPTION, {
        "entries": S.FOOD_ENTRIES_DESCRIPTION, "date": S.FOOD_DATE_DESCRIPTION,
        "allow_repeat": S.FOOD_ALLOW_REPEAT_DESCRIPTION, "key": S.KEY_DESCRIPTION}),
    "log_cardio": (S.LOG_CARDIO_DESCRIPTION, {
        "exercise": S.CARDIO_EXERCISE_DESCRIPTION, "minutes": S.CARDIO_MINUTES_DESCRIPTION,
        "date": S.CARDIO_DATE_DESCRIPTION, "distance_km": S.CARDIO_DISTANCE_KM_DESCRIPTION,
        "distance_mi": S.CARDIO_DISTANCE_MI_DESCRIPTION, "incline_pct": S.CARDIO_INCLINE_PCT_DESCRIPTION,
        "speed_kmh": S.CARDIO_SPEED_KMH_DESCRIPTION, "speed_mph": S.CARDIO_SPEED_MPH_DESCRIPTION,
        "avg_hr": S.CARDIO_AVG_HR_DESCRIPTION, "machine_kcal": S.CARDIO_MACHINE_KCAL_DESCRIPTION,
        "notes": S.CARDIO_NOTES_DESCRIPTION, "allow_repeat": S.CARDIO_ALLOW_REPEAT_DESCRIPTION,
        "key": S.KEY_DESCRIPTION}),
}


def test_the_published_hashes_and_the_instructions_are_unmoved(http):
    tools = registered_tools(http)
    hashes = {name: hashlib.sha256(compact(tool).encode()).hexdigest() for name, tool in tools.items()}
    assert {name: hashes[name] for name in PUBLISHED} == PUBLISHED
    assert hashlib.sha256(mcp_server.INSTRUCTIONS.encode()).hexdigest() == INSTRUCTIONS_AS_BUILT


def test_registered_tools_is_the_owner_s_list_entry_for_entry(make_client):
    client = make_client(owner_user_id=OWNER)
    listed = tool_list(client, sign(claims(sub=OWNER)))
    assert list(registered_tools(client).values()) == listed
    assert set(registered_tools(client)) == set(mcp_server.NEEDS)  # every registered function


def test_each_of_the_five_publishes_its_named_constants(http):
    tools = registered_tools(http)
    for name, (description, fields) in WORDS.items():
        assert tools[name]["description"] == description, name
        published = {field: node["description"] for field, node in tools[name]["inputSchema"]["properties"].items()}
        assert published == fields, name


def test_the_moved_texts_live_in_mcp_schemas_and_read_back_under_their_names():
    for name in ("CARDIO_RULE", "FOOD_RULE", "KEY_DESCRIPTION", "Key", "FOOD_NAME_RULE"):
        assert getattr(mcp_server, name) is getattr(mcp_schemas, name), name
    source = (ROOT / "app" / "mcp_server.py").read_text(encoding="utf-8")
    for name in ("CARDIO_RULE", "FOOD_RULE", "KEY_DESCRIPTION", "Key"):
        assert f"\n{name} = " not in source, name  # defined once, in mcp_schemas
    assert S.CARDIO_RULE in S.CARDIO_EXERCISE_DESCRIPTION
    assert f" {S.FOOD_RULE} {S.FOOD_NAME_RULE} " in S.LOG_FOOD_DESCRIPTION


def test_mcp_schemas_imports_only_app_rules_from_the_app():
    tree = ast.parse((ROOT / "app" / "mcp_schemas.py").read_text(encoding="utf-8"))
    from_app = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "app":
            from_app.append((node.module, [alias.name for alias in node.names]))
        if isinstance(node, ast.Import):
            from_app += [(alias.name, []) for alias in node.names if alias.name.split(".")[0] == "app"]
    assert from_app == [("app", ["rules"])]


# ---------------------------------------------------------------- the argument models

def test_each_argument_model_s_schema_is_its_registered_function_s_input_schema(http, server):
    tools = registered_tools(http)
    assert list(LOG_ARGUMENTS) == list(FIVE) == list(LOG_STEPS)
    for name, model in LOG_ARGUMENTS.items():
        schema = model.model_json_schema()
        assert schema == tools[name]["inputSchema"], name  # as published, through the wire
        assert schema == server._tool_manager.get_tool(name).parameters, name  # as registered
        assert compact(schema) == compact(tools[name]["inputSchema"]), name


# Arguments for each of the five, each sent to misfit with the registered function and to misfit_model with the
# argument model: an unknown key at the top and in an item, a wrong type, a missing field, a bool for a number, lists
# and objects sent as JSON text, and calls that fit.
SQUAT = {"exercise": "squat", "weight_kg": 100, "reps": 5}
MISFITS = {
    "log_lifts": [
        {"sets": [SQUAT]},
        {"sets": json.dumps([SQUAT])},
        {"sets": [{**SQUAT, "weight": 100}]},
        {"sets": json.dumps([{"exercise": "squat", "reps": "five"}]), "kg": 3},
        {"sets": [{"exercise": "squat", "reps": True}]},
        {"sets": [SQUAT], "new_session": "yes", "allow_repeat": 1},
        {"sets": {"exercise": "squat"}},
        {"sets": "not json"},
        {},
    ],
    "log_bodyweight": [
        {"kg": 80}, {"kg": "80"}, {"kg": True}, {"kg": "80kg"}, {"weight": 80}, {"lb": [180]}, {"date": 5},
    ],
    "log_measurement": [
        {"kind": "waist", "value": 81}, {}, {"kind": "waist", "value": "abc"}, {"kind": ["waist"], "value": 81},
        {"kind": "waist", "value": 81, "unit": "cm"}, {"kind": "steps", "value": "nan"},
    ],
    "log_food": [
        {"entries": [{"description": "dal", "kcal": 300}]},
        {"entries": json.dumps([{"description": "dal", "kcal": 300}])},
        {"entries": [{"description": "dal", "calories": 300}]},
        {"entries": [{"description": "dal", "kcal": 300}], "meal": "lunch"},
        {"entries": {"description": "dal"}},
        {"entries": json.dumps({"description": "dal"})},
        {"entries": [{"saved_food_id": True, "servings": "two"}], "allow_repeat": "true"},
    ],
    "log_cardio": [
        {"exercise": "Outdoor Run", "minutes": 30},
        {"exercise": "Outdoor Run", "minutes": 30, "distance": 5},
        {"exercise": "Outdoor Run"},
        {"exercise": "Outdoor Run", "minutes": 30, "avg_hr": 140.5, "machine_kcal": "lots"},
        {"exercise": "Outdoor Run", "minutes": False, "allow_repeat": "true"},
        {"exercise": 7, "minutes": 30, "incline_pct": "5", "speed": 10},
    ],
}
CASES = [(name, arguments) for name, every in MISFITS.items() for arguments in every]


def _errors(found):
    return None if found is None or found[1] is None else found[1].errors(include_url=False)


@pytest.mark.parametrize(("name", "arguments"), CASES, ids=[f"{n}-{i}" for i, (n, _) in enumerate(CASES)])
def test_misfit_model_answers_as_misfit_does_for_the_registered_function(server, name, arguments):
    tool = server._tool_manager.get_tool(name)
    model = LOG_ARGUMENTS[name]
    by_tool, by_model = misfit(tool, arguments), misfit_model(model, arguments)
    assert (by_tool is None) == (by_model is None)
    if by_tool is None:
        return
    assert by_tool[0] == by_model[0]  # the unknown top-level keys
    assert _errors(by_tool) == _errors(by_model)
    assert schema_refusal(name, tool.parameters, arguments, *by_tool) == \
        schema_refusal(name, model.model_json_schema(), arguments, *by_model)


def test_the_cases_cover_fits_and_misfits_of_each_kind(server):
    for name, every in MISFITS.items():
        found = [misfit_model(LOG_ARGUMENTS[name], arguments) for arguments in every]
        assert None in found, name
        assert any(f is not None and f[0] for f in found), name  # an unknown top-level key
        assert any(f is not None and f[1] is not None for f in found), name  # a schema failure


def test_read_arguments_reads_a_list_sent_as_json_text_as_the_library_does():
    sent = read_arguments(LOG_ARGUMENTS["log_lifts"], {"sets": json.dumps([SQUAT]), "kg": 3})
    assert [s.model_dump(exclude_none=True) for s in sent.sets] == [SQUAT]
    assert "kg" not in dict(sent)  # a key the model does not take is left to misfit_model's unknown list
    food = read_arguments(LOG_ARGUMENTS["log_food"], {"entries": json.dumps([{"description": "dal"}])})
    assert food.entries[0].description == "dal"


# ---------------------------------------------------------------- the steps

# (arguments sent, the service's data), the data written out from the five bodies as they were before L0: a field left
# out or sent as null is not passed, a set's or an entry's nulls are dropped, allow_repeat and new_session go only when
# true, and log_lifts never passes allow_repeat.
STEPS = {
    "log_lifts": [
        ({"sets": [{**SQUAT, "time_s": None}, {"exercise": "Plank", "time_s": 60, "exact": True}], "date": DAY,
          "notes": "Felt strong.", "allow_repeat": True, "new_session": True},
         {"sets": [SQUAT, {"exercise": "Plank", "time_s": 60, "exact": True}], "date": DAY, "notes": "Felt strong.",
          "new_session": True}),
        ({"sets": [{"exercise": "Back Squat", "weight_lb": 225, "reps": 5, "exact": None}], "date": None,
          "notes": None, "allow_repeat": False, "new_session": False},
         {"sets": [{"exercise": "Back Squat", "weight_lb": 225, "reps": 5}]}),
        ({"sets": json.dumps([{"exercise": "Farmer's Carry", "weight_kg": 30, "distance_m": 40, "time_s": 45}])},
         {"sets": [{"exercise": "Farmer's Carry", "weight_kg": 30, "distance_m": 40, "time_s": 45}]}),
    ],
    "log_bodyweight": [
        ({"lb": 180.5, "date": DAY, "allow_repeat": True}, {"lb": 180.5, "date": DAY, "allow_repeat": True}),
        ({"kg": 80, "lb": None, "date": None, "allow_repeat": False}, {"kg": 80}),
    ],
    "log_measurement": [
        ({"kind": "waist", "value": 81.5, "date": DAY, "allow_repeat": True},
         {"kind": "waist", "value": 81.5, "date": DAY, "allow_repeat": True}),
        ({"kind": "sleep_hours", "value": "7.5", "allow_repeat": False}, {"kind": "sleep_hours", "value": 7.5}),
    ],
    "log_food": [
        ({"entries": [{"description": "dal", "kcal": 300, "estimated": True, "meal": "lunch"},
                      {"saved_food_id": 4, "servings": 2, "description": None}], "date": DAY, "allow_repeat": True},
         {"entries": [{"description": "dal", "meal": "lunch", "kcal": 300, "estimated": True},
                      {"saved_food_id": 4, "servings": 2}], "date": DAY, "allow_repeat": True}),
        ({"entries": json.dumps([{"description": "banana", "kcal": 100}]), "allow_repeat": False},
         {"entries": [{"description": "banana", "kcal": 100}]}),
    ],
    "log_cardio": [
        ({"exercise": "Treadmill Walk", "minutes": 30, "date": DAY, "distance_mi": 2, "incline_pct": 5,
          "speed_mph": 4, "avg_hr": 130, "machine_kcal": 250, "notes": "Easy.", "allow_repeat": True},
         {"exercise": "Treadmill Walk", "minutes": 30, "date": DAY, "distance_mi": 2, "incline_pct": 5,
          "speed_mph": 4, "avg_hr": 130, "machine_kcal": 250, "notes": "Easy.", "allow_repeat": True}),
        ({"exercise": "Outdoor Run", "minutes": 25.5, "distance_km": 5, "speed_kmh": 12, "notes": None,
          "avg_hr": None, "allow_repeat": False},
         {"exercise": "Outdoor Run", "minutes": 25.5, "distance_km": 5, "speed_kmh": 12}),
    ],
}
STEP_CASES = [(name, sent, data) for name, every in STEPS.items() for sent, data in every]


@pytest.mark.parametrize(("name", "sent", "data"), STEP_CASES,
                         ids=[f"{n}-{i}" for i, (n, _, _) in enumerate(STEP_CASES)])
def test_each_step_gives_the_service_the_data_the_body_did(http, monkeypatch, name, sent, data):
    token = sign()
    whoami(http, token)
    account = one_user().id
    received = spy_on_services(monkeypatch)
    failed(http, token, name, sent)  # the decorated function, through /mcp
    assert received.pop(name) == ((account, data, "claude"), {})
    arguments = dict(read_arguments(LOG_ARGUMENTS[name], sent))
    LOG_STEPS[name](account, **arguments)  # the step alone, from the argument model
    assert received.pop(name) == ((account, data, "claude"), {})
    LOG_STEPS[name](account, **{**arguments, "key": "a-key-of-mine-01"})  # a key reaches no service
    assert received.pop(name) == ((account, data, "claude"), {})


def test_each_decorated_function_runs_its_step_with_every_field(http, monkeypatch):
    seen = {}
    for name in FIVE:
        def step(account, _name=name, **arguments):
            seen[_name] = (account, arguments)
            return {"error": "spy", "message": "Recorded.", "fix": "None."}

        monkeypatch.setattr(mcp_server, f"{name}_step", step)
    token = sign()
    whoami(http, token)
    account = one_user().id
    for name in FIVE:
        sent = STEPS[name][0][0]
        keyed = {**sent, "key": f"key-{name}-01"}  # a key of each function's own: one key on two is key_reused
        failed(http, token, name, keyed)
        model = LOG_ARGUMENTS[name]
        assert seen[name] == (account, dict(read_arguments(model, keyed))), name
        assert set(seen[name][1]) == set(model.model_fields), name


# ================================================================ L1a: log's shape and guide, as plain code

SET = {"movement": "Back Squat", "weight_kg": 100, "reps": 5}
WALK = {"movement": "Outdoor Walk", "minutes": 30}
LIFT_PART = "movement, weight_kg, weight_lb, reps, time_s, distance_m, exact, minutes, distance_km, distance_mi"
CARDIO_PART = "movement, time_s, distance_m, minutes, distance_km, distance_mi"
# One entry of each kind that route takes, without its kind word.
ENTRY = {"lifts": {"parts": [SET]}, "cardio": {"parts": [WALK]}, "bodyweight": {"details": {"kg": 80}},
         "food": {"details": {"entries": [{"description": "Dal", "kcal": 180}]}},
         "measurement": {"details": {"kind": "waist", "value": 86}}}
# A name of the five's shape left in a refusal said as sent: an item word, a moved field named bare, or a function.
LEFT_OVER = re.compile(
    r"\b[Ss]et \d|\b[Ee]ntry \d|takes: sets|\bsets\[|\brows?\b|(?<![\w.\]'])(exercise|new_session|incline_pct|"
    r"speed_kmh|speed_mph|avg_hr|machine_kcal|entries|value|minutes|distance_km|distance_mi) (is missing|must be|\d)|"
    r"Give (an )?(exercise|minutes|kind|value|kg|lb|entries)\b|(?<![\w.])(kg|lb) (is missing|must be|or lb|and lb|\d)")
FIVE_NAMES = re.compile(r"\b(log_lifts|log_bodyweight|log_measurement|log_food|log_cardio)\b")


def texts_of(answer: dict) -> list[str]:
    """Every message and fix of a refusal, its errors' too."""
    return [e[k] for e in [answer, *answer.get("errors", [])] for k in ("message", "fix")]


def refused(arguments: dict) -> dict:
    routed = route(arguments)
    assert routed.arguments is None and routed.refusal is not None, routed
    return routed.refusal


# ---------------------------------------------------------------- the kinds and their words

def every_word():
    """(word, kind, the measurement it names): the five, every open word, every measurement word, the table words."""
    yield from ((kind, kind, None) for kind in KINDS)
    yield from ((word, kind, None) for word, kind in log_shape.OPEN_WORDS.items())
    yield from ((word, "measurement", kind) for word, kind in log_shape.MEASURE_WORDS.items())
    yield from (("lift_session", "lifts", None), ("food_entry", "food", None), ("cardio_entry", "cardio", None))


WORDS_CASES = list(every_word())


@pytest.mark.parametrize(("word", "kind", "measure"), WORDS_CASES, ids=[w for w, _, _ in WORDS_CASES])
def test_route_takes_every_kind_word_measurement_word_and_table_word(word, kind, measure):
    assert log_shape.resolve_kind(word) == (kind, measure)
    entry = {"details": {"value": 7}} if measure else ENTRY[kind]
    routed = route({"kind": word, **entry})
    assert (routed.kind, routed.function, routed.refusal) == (kind, log_shape.KIND_FUNCTIONS[kind], None)
    assert routed.model is LOG_ARGUMENTS[routed.function] and routed.result is mcp_schemas.RESULTS[routed.function]
    if measure:
        assert routed.arguments == {"kind": measure, "value": 7}  # the word fills in details.kind
    assert misfit_model(routed.model, routed.arguments) is None
    assert routed.word == log_shape.fold(word).replace(" ", "_")


@pytest.mark.parametrize("word", ["Body Weight", "WEIGH-IN", "weight_training", "  Resting   Heart_Rate ", "Sleep"])
def test_a_kind_word_is_read_folded(word):
    assert log_shape.resolve_kind(word) is not None and route({"kind": word}).kind is not None


def test_the_measurement_words_are_the_kinds_rules_keeps():
    assert tuple(rules.MEASUREMENTS) == models.MEASUREMENT_KINDS
    assert set(log_shape.MEASURE_WORDS.values()) == set(models.MEASUREMENT_KINDS)


@pytest.mark.parametrize(("word", "closest"), [("lifs", "lifts"), ("bodywieght", "bodyweight"), ("cardoi", "cardio"),
                                               ("sleeep", "sleep"), ("Wiast", "waist")])
def test_an_unknown_word_is_refused_with_the_closest(word, closest):
    routed = route({"kind": word, "details": {"kg": 80}})
    assert (routed.kind, routed.function, routed.arguments, routed.word) == (None, None, None, "other")
    answer = routed.refusal
    assert answer["error"] == "invalid_field" and answer["field"] == "kind"
    assert answer["message"] == f"'{word}' is not a kind Spotter logs."
    assert answer["fix"] == ("Use lifts, bodyweight, measurement, food or cardio. guide with topic log lists every "
                             f"kind. The closest: {closest}.")


@pytest.mark.parametrize("word", ["yoga", 5, None, [1], {"kind": 2}, True, ""])
def test_a_word_with_no_close_kind_and_a_kind_that_is_not_text_are_refused(word):
    answer = refused({"kind": word})
    assert answer["field"] == "kind" and "The closest" not in answer["fix"]
    assert answer["message"].endswith("is not a kind Spotter logs.")


def test_a_call_with_no_kind_is_refused_and_a_field_log_does_not_take_too():
    answer = refused({"details": {"kg": 80}})
    assert (answer["error"], answer["field"], answer["message"]) == ("missing_field", "kind",
                                                                    "log needs kind: what is logged.")
    both = route({"kind": "bodyweight", "kg": 80, "sets": []})
    assert both.kind == "bodyweight" and both.function == "log_bodyweight"  # the kind is carried with the refusal
    assert [e["message"] for e in both.refusal["errors"]] == ["'kg' is not a field of log.",
                                                              "'sets' is not a field of log."]
    assert both.refusal["errors"][0]["fix"] == ("log takes: kind, parts, details, date, notes, allow_repeat, key. A "
                                                "kind's own fields go in details, as guide gives them.")
    assert both.refusal["errors"][1]["fix"].endswith("A part's numbers go in parts, a list of parts.")


def test_note_word_keeps_only_spotter_s_own_words():
    assert [log_shape.note_word(w) for w in ("gym", "Resting heart rate", "lift_session", "weigh-in", "Waist")] == [
        "gym", "resting_heart_rate", "lift_session", "weigh_in", "waist"]
    assert [log_shape.note_word(w) for w in ("my morning run", "yoga", 5, None, ["gym"])] == ["other"] * 5
    longest = max(len(log_shape.note_word(w)) for w in log_shape.KIND_WORDS)
    assert longest <= 30  # word= has at most 30 characters of the notes column


# ---------------------------------------------------------------- the kind map of decision 52

def test_the_kind_map_gives_the_table_word_for_log_s_five_words_alone():
    assert {k: log_shape.table_word(k) for k in KINDS} == {
        "lifts": "lift_session", "bodyweight": "bodyweight", "measurement": "measurement", "food": "food_entry",
        "cardio": "cardio_entry"}
    for word in ("lift_session", "food_entry", "cardio_entry", "saved_food", "routine", "gym", "Lifts", "waist", " lifts",
                 5, None, ["lifts"], {"kind": "lifts"}):
        assert log_shape.table_word(word) == word, word  # every other word, as sent: the kind check judges it
    assert all(w in mcp_server.KIND_DESCRIPTION for w in log_shape.TABLE_WORDS.values())  # edit_entry's kind words


# ---------------------------------------------------------------- route: one log call, in its kind's own shape

# (a log call, the same entry sent in its kind's function's own shape).
ROUTES = [
    ({"kind": "lifts", "parts": [SET, {"movement": "Bench Press", "weight_lb": 155, "reps": 6}],
      "details": {"new_session": True}, "date": DAY, "notes": "easy", "key": "abcdefgh01"},
     {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5},
               {"exercise": "Bench Press", "weight_lb": 155, "reps": 6}],
      "new_session": True, "date": DAY, "notes": "easy", "key": "abcdefgh01"}),
    ({"kind": "gym", "parts": [{"movement": "squat", "weight_kg": 60, "reps": 8, "exact": True}], "allow_repeat": True},
     {"sets": [{"exercise": "squat", "weight_kg": 60, "reps": 8, "exact": True}], "allow_repeat": True}),
    ({"kind": "lift_session", "parts": json.dumps([SET]), "details": '{"new_session": false}'},
     {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}], "new_session": False}),
    ({"kind": "lifts", "parts": [{"movement": "Plank", "minutes": 2}]}, {"sets": [{"exercise": "Plank", "time_s": 120}]}),
    ({"kind": "lifts", "parts": [{"movement": "Plank", "minutes": 1.1, "weight_kg": 0}]},
     {"sets": [{"exercise": "Plank", "time_s": 66, "weight_kg": 0}]}),
    ({"kind": "lifts", "parts": [{"movement": "Farmer's Carry", "weight_kg": 30, "distance_km": 0.04, "time_s": 45}]},
     {"sets": [{"exercise": "Farmer's Carry", "weight_kg": 30, "distance_m": 40, "time_s": 45}]}),
    ({"kind": "lifts", "parts": [{"movement": "Sled Push", "weight_kg": 40, "distance_mi": 0.025}]},
     {"sets": [{"exercise": "Sled Push", "weight_kg": 40, "distance_m": 40.2336}]}),
    ({"kind": "lifts", "parts": [{"movement": "Plank", "time_s": None, "minutes": 1}]},
     {"sets": [{"exercise": "Plank", "time_s": 60}]}),  # a null is no form
    ({"kind": "cardio", "parts": [{"movement": "Outdoor Run", "minutes": 25, "distance_km": 5}],
      "details": {"avg_hr": 150, "machine_kcal": 300}, "notes": "easy", "date": DAY},
     {"exercise": "Outdoor Run", "minutes": 25, "distance_km": 5, "avg_hr": 150, "machine_kcal": 300, "notes": "easy",
      "date": DAY}),
    ({"kind": "run", "parts": [{"movement": "Outdoor Run", "minutes": 25, "distance_m": 5000}]},
     {"exercise": "Outdoor Run", "minutes": 25, "distance_km": 5}),
    ({"kind": "cardio", "parts": [{"movement": "Outdoor Run", "minutes": 30, "distance_mi": 3}]},
     {"exercise": "Outdoor Run", "minutes": 30, "distance_mi": 3}),
    ({"kind": "cardio_entry", "parts": [{"movement": "Treadmill Walk", "time_s": 1530}],
      "details": {"speed_mph": 3.2, "incline_pct": 4}, "allow_repeat": True},
     {"exercise": "Treadmill Walk", "minutes": 25.5, "speed_mph": 3.2, "incline_pct": 4, "allow_repeat": True}),
    ({"kind": "cardio", "parts": [{"movement": "Outdoor Walk", "minutes": 30, "distance_km": 3, "distance_mi": 2}]},
     {"exercise": "Outdoor Walk", "minutes": 30, "distance_km": 3, "distance_mi": 2}),  # the service's own refusal
    ({"kind": "bodyweight", "details": {"lb": 185}, "date": DAY, "notes": None},
     {"lb": 185, "date": DAY}),  # a null notes on a kind that takes none is dropped
    ({"kind": "weigh-in", "details": {"kg": 80}, "parts": [], "key": None}, {"kg": 80, "key": None}),
    ({"kind": "food", "details": {"entries": [{"description": "Dal", "kcal": 180, "meal": "lunch"},
                                              {"saved_food_id": 4, "servings": 2}]}, "allow_repeat": True},
     {"entries": [{"description": "Dal", "kcal": 180, "meal": "lunch"}, {"saved_food_id": 4, "servings": 2}],
      "allow_repeat": True}),
    ({"kind": "food_entry", "details": json.dumps({"entries": [{"description": "banana", "kcal": 100,
                                                                "estimated": True}]})},
     {"entries": [{"description": "banana", "kcal": 100, "estimated": True}]}),
    ({"kind": "measurement", "details": {"kind": "waist", "value": 86}}, {"kind": "waist", "value": 86}),
    ({"kind": "waist", "details": {"kind": "waist", "value": 86}}, {"kind": "waist", "value": 86}),
    ({"kind": "sleep", "details": {"value": 7.5}, "date": DAY}, {"kind": "sleep_hours", "value": 7.5, "date": DAY}),
    ({"kind": "resting heart rate", "details": {"value": 58}}, {"kind": "resting_hr", "value": 58}),
    ({"kind": "steps", "details": {"value": 9000}, "allow_repeat": True},
     {"kind": "steps", "value": 9000, "allow_repeat": True}),
    ({"kind": "water", "details": {"value": 2000}}, {"kind": "water_ml", "value": 2000}),
    # details.kind sent as the same word, its own name or another case of it agrees with the kind word
    ({"kind": "water", "details": {"kind": "water", "value": 2000}}, {"kind": "water_ml", "value": 2000}),
    ({"kind": "sleep", "details": {"kind": "sleep_hours", "value": 7}}, {"kind": "sleep_hours", "value": 7}),
    ({"kind": "Waist", "details": {"kind": "WAIST", "value": 86}}, {"kind": "waist", "value": 86}),
    ({"kind": "pulse", "details": {"kind": "resting heart rate", "value": 58}}, {"kind": "resting_hr", "value": 58}),
]


@pytest.mark.parametrize(("sent", "own"), ROUTES, ids=[f"{i}-{s['kind']}" for i, (s, _) in enumerate(ROUTES)])
def test_route_gives_the_kind_s_own_arguments_and_its_step_the_same_data(monkeypatch, sent, own):
    routed = route(sent)
    assert routed.refusal is None and routed.arguments == own
    assert set(routed.arguments) <= set(routed.model.model_fields)  # nothing a model would drop unseen
    assert misfit_model(routed.model, routed.arguments) is None
    received = spy_on_services(monkeypatch)
    LOG_STEPS[routed.function](7, **dict(read_arguments(routed.model, routed.arguments)))
    by_log = received.pop(routed.function)
    LOG_STEPS[routed.function](7, **dict(read_arguments(routed.model, own)))
    assert by_log == received.pop(routed.function)
    assert by_log[0][0] == 7 and by_log[0][2] == "claude"


def test_the_fields_of_the_shape_are_each_kind_s_own_fields_once():
    for kind, function in log_shape.KIND_FUNCTIONS.items():
        taken = set(LOG_ARGUMENTS[function].model_fields)
        top = {f for f in log_shape.TOP_FIELDS if f in taken}
        own = (set(log_shape.PART_MAP["cardio"].values()) if kind == "cardio" else
               {"sets"} if kind == "lifts" else set())
        assert own | set(log_shape.DETAIL_FIELDS[kind]) | top == taken, kind
    assert set(log_shape.PART_MAP["lifts"].values()) == set(mcp_schemas.LogSetIn.model_fields)
    assert log_shape.PART_FIELDS == tuple(mcp_schemas.LogPartIn.model_fields)
    assert set(log_shape.PART_TAKES["lifts"]) == set(log_shape.PART_FIELDS)
    assert log_shape.PART_TAKES["cardio"] == ("movement", "time_s", "distance_m", "minutes", "distance_km",
                                              "distance_mi")
    assert set(log_shape.NOTES_KINDS) == {k for k, f in log_shape.KIND_FUNCTIONS.items()
                                          if "notes" in LOG_ARGUMENTS[f].model_fields}


# ---------------------------------------------------------------- the conversions of decision 51

@pytest.mark.parametrize(("part", "kept"), [
    ({"movement": "Plank", "minutes": 2}, {"time_s": 120}),
    ({"movement": "Plank", "minutes": "1.5"}, {"time_s": 90}),  # number text, as Number takes it
    ({"movement": "Plank", "minutes": 0.1}, {"time_s": 6}),
    ({"movement": "Farmer's Carry", "distance_km": 0.04, "time_s": 45}, {"distance_m": 40, "time_s": 45}),
    ({"movement": "Farmer's Carry", "distance_km": 1.1}, {"distance_m": 1100}),
    ({"movement": "Farmer's Carry", "distance_mi": 0.5}, {"distance_m": 804.672}),
    ({"movement": "Farmer's Carry", "distance_m": 40, "time_s": 45}, {"distance_m": 40, "time_s": 45}),
])
def test_a_lift_part_s_time_and_distance_go_to_its_seconds_and_metres(part, kept):
    (one,) = route({"kind": "lifts", "parts": [part]}).arguments["sets"]
    assert {k: v for k, v in one.items() if k != "exercise"} == kept
    assert all(type(v) is int for k, v in one.items() if k == "time_s")


@pytest.mark.parametrize(("part", "kept"), [
    ({"movement": "Outdoor Run", "minutes": 25, "distance_m": 5000}, {"minutes": 25, "distance_km": 5}),
    ({"movement": "Outdoor Run", "time_s": 1500, "distance_km": 5}, {"minutes": 25, "distance_km": 5}),
    ({"movement": "Outdoor Run", "time_s": 90, "distance_m": 1234}, {"minutes": 1.5, "distance_km": 1.234}),
    ({"movement": "Outdoor Run", "minutes": 30, "distance_mi": 3}, {"minutes": 30, "distance_mi": 3}),  # as today
])
def test_a_cardio_part_s_time_and_distance_go_to_its_minutes_and_km(part, kept):
    routed = route({"kind": "cardio", "parts": [part]})
    assert {k: v for k, v in routed.arguments.items() if k != "exercise"} == kept


def test_a_lift_time_short_of_a_whole_second_is_refused_with_a_fix_naming_time_s():
    answer = refused({"kind": "lifts", "parts": [SET, {"movement": "Plank", "minutes": 0.013}]})
    assert answer["message"] == "parts[1].minutes 0.013 is not a whole number of seconds."
    assert answer["fix"] == "Send the time as parts[1].time_s, in whole seconds."
    assert answer["field"] == "parts[1].minutes"
    run = route({"kind": "cardio", "parts": [{"movement": "Outdoor Run", "time_s": 61}]})
    assert run.arguments["minutes"] == 61 / 60  # a cardio session's minutes need not be whole


@pytest.mark.parametrize(("kind", "part", "message", "fix"), [
    ("lifts", {"movement": "Plank", "time_s": 60, "minutes": 1}, "parts[0] has time_s and minutes: send one.",
     "Send the time once, in the field of the unit the person said: time_s or minutes."),
    ("lifts", {"movement": "Farmer's Carry", "distance_km": 0.04, "distance_mi": 0.025},
     "parts[0] has distance_km and distance_mi: send one.",
     "Send the distance once, in the field of the unit the person said: distance_m, distance_km or distance_mi."),
    ("lifts", {"movement": "Farmer's Carry", "distance_m": 40, "distance_km": 0.04, "distance_mi": 0.025},
     "parts[0] has distance_m, distance_km and distance_mi: send one.",
     "Send the distance once, in the field of the unit the person said: distance_m, distance_km or distance_mi."),
    ("cardio", {"movement": "Outdoor Run", "minutes": 25, "time_s": 1500}, "parts[0] has time_s and minutes: send one.",
     "Send the time once, in the field of the unit the person said: time_s or minutes."),
    ("cardio", {"movement": "Outdoor Run", "minutes": 25, "distance_m": 5000, "distance_mi": 3},
     "parts[0] has distance_m and distance_mi: send one.",
     "Send the distance once, in the field of the unit the person said: distance_m, distance_km or distance_mi."),
])
def test_two_forms_of_one_number_in_one_part_are_refused(kind, part, message, fix):
    answer = refused({"kind": kind, "parts": [part]})
    assert (answer["message"], answer["fix"], answer["field"]) == (message, fix, "parts[0]")


@pytest.mark.parametrize("value", [True, "two", [2], {"m": 2}])
def test_a_form_that_is_no_number_is_refused_not_converted(value):
    answer = refused({"kind": "lifts", "parts": [{"movement": "Plank", "minutes": value}]})
    assert answer["message"].startswith("parts[0].minutes must be a number, not ")
    assert answer["fix"] == "Give parts[0].minutes as a number."


# ---------------------------------------------------------------- what the shape itself makes wrong

# (the call, the field named, the message, words of the fix).
SHAPE_REFUSALS = [
    ({"kind": "bodyweight", "parts": [{"weight_kg": 80}]}, "parts", "bodyweight takes no parts.",
     "Leave parts out and send the fields of bodyweight in details: kg, lb. guide with topic bodyweight gives them."),
    ({"kind": "waist", "parts": [{"minutes": 3}], "details": {"value": 86}}, "parts", "measurement takes no parts.",
     "Leave parts out"),
    ({"kind": "food", "parts": [{"movement": "Dal"}]}, "parts", "food takes no parts.", "in details: entries"),
    ({"kind": "lifts"}, "parts", "lifts needs parts: one part a set.", f"Send each set as a part, in order: {LIFT_PART}."),
    ({"kind": "lifts", "parts": []}, "parts", "lifts needs parts: one part a set.", LIFT_PART),
    ({"kind": "cardio", "details": {"avg_hr": 120}}, "parts", "cardio needs parts: one part, the session.",
     f"Send one part: {CARDIO_PART}."),
    ({"kind": "cardio", "parts": [WALK, WALK]}, "parts", "cardio takes exactly 1 part, the session: 2 were sent.",
     f"Send one part: {CARDIO_PART}. Another session takes its own log call."),
    ({"kind": "cardio", "parts": [{**WALK, "reps": 5}]}, "parts[0].reps", "parts[0].reps: a cardio part takes no reps.",
     f"A cardio part takes: {CARDIO_PART}. reps is a lifts part's field. To log lifts, send kind lifts."),
    ({"kind": "cardio", "parts": [{**WALK, "weight_kg": 5}]}, "parts[0].weight_kg",
     "parts[0].weight_kg: a cardio part takes no weight_kg.", "weight_kg is a lifts part's field."),
    ({"kind": "cardio", "parts": [{**WALK, "exact": True}]}, "parts[0].exact",
     "parts[0].exact: a cardio part takes no exact.", "exact is a lifts part's field."),
    ({"kind": "lifts", "parts": [SET], "details": {"exact": True}}, "details.exact",
     "details.exact: exact goes in a part.", f"Send it in parts, one part a set: {LIFT_PART}."),
    ({"kind": "lifts", "parts": [SET], "details": {"minutes": 2}}, "details.minutes",
     "details.minutes: minutes goes in a part.", "Send it in parts, one part a set"),
    ({"kind": "cardio", "parts": [{"movement": "Outdoor Walk"}], "details": {"minutes": 30}}, "details.minutes",
     "details.minutes: minutes goes in a part.", "Send it as parts[0].minutes."),
    ({"kind": "lifts", "parts": [SET], "details": {"sets": [SET]}}, "details.sets", "details.sets: sets go in a part.",
     "one part a set"),
    ({"kind": "cardio", "parts": [{"exercise": "Outdoor Walk", "minutes": 30}]}, "parts[0].exercise",
     "parts[0].exercise: a part names its lift or activity in movement.", "Send it as parts[0].movement."),
    ({"kind": "cardio", "parts": [{"minutes": 30}], "details": {"exercise": "Outdoor Walk"}}, "details.exercise",
     "details.exercise: exercise goes in a part.", "Send it as parts[0].movement."),
    ({"kind": "cardio", "parts": [{**WALK, "speed_mph": 3}]}, "parts[0].speed_mph",
     "parts[0].speed_mph: speed_mph goes in details.", "Send it as details.speed_mph."),
    ({"kind": "lifts", "parts": [{**SET, "new_session": True}]}, "parts[0].new_session",
     "parts[0].new_session: new_session goes in details.", "Send it as details.new_session."),
    ({"kind": "cardio", "parts": [{**WALK, "date": DAY}]}, "parts[0].date",
     "parts[0].date: date goes at the top of log, beside kind.", "Send date at the top of log, not in a part."),
    ({"kind": "lifts", "parts": [{**SET, "notes": "easy"}]}, "parts[0].notes",
     "parts[0].notes: notes goes at the top of log, beside kind.", "Send notes at the top of log, not in a part."),
    ({"kind": "bodyweight", "details": {"kg": 80, "date": DAY}}, "details.date",
     "details.date: date goes at the top of log, beside kind.", "Send date at the top of log, not in details."),
    ({"kind": "cardio", "parts": [WALK], "details": {"notes": "easy"}}, "details.notes",
     "details.notes: notes goes at the top of log, beside kind.", "Send notes at the top of log, not in details."),
    ({"kind": "lifts", "parts": [SET], "details": {"warmup": True}}, "details.warmup",
     "'details.warmup' is not a field lifts takes in details.",
     "In details, lifts takes: new_session. guide with topic lifts gives each."),
    ({"kind": "cardio", "parts": [WALK], "details": {"zone": 2}}, "details.zone",
     "'details.zone' is not a field cardio takes in details.",
     "In details, cardio takes: incline_pct, speed_kmh, speed_mph, avg_hr, machine_kcal. guide with topic cardio "
     "gives each."),
    ({"kind": "cardio", "parts": [WALK], "details": {"calories": 300}}, "details.calories",
     "'details.calories' is not a field cardio takes in details.",
     "For the calories the machine or watch showed, send details.machine_kcal."),
    ({"kind": "cardio", "parts": [WALK], "details": {"hr": 120}}, "details.hr",
     "'details.hr' is not a field cardio takes in details.", "Send the average heart rate as details.avg_hr."),
    ({"kind": "bodyweight", "details": {"weight": 80}}, "details.weight",
     "'details.weight' is not a field bodyweight takes in details.",
     "Send the body weight in details.kg or details.lb, in the unit the person said."),
    ({"kind": "bodyweight", "details": {"kgs": 80}}, "details.kgs",
     "'details.kgs' is not a field bodyweight takes in details.", "The nearest field is details.kg."),
    ({"kind": "waist", "details": {"value": 34, "unit": "in"}}, "details.unit",
     "'details.unit' is not a field measurement takes in details.",
     "details.value is in the kind's own unit: cm for waist, hips, chest, arm and thigh. Spotter takes no inches "
     "for a body size."),
    ({"kind": "food", "details": {"entries": [{"description": "Dal", "kcal": 180}], "meal": "lunch"}},
     "details.meal", "'details.meal' is not a field food takes in details.",
     "In details, food takes: entries. guide with topic food gives each."),
    ({"kind": "bodyweight", "details": {"kg": 80, "notes": "after breakfast"}}, "details.notes",
     "'details.notes' is not a field bodyweight takes in details.", "In details, bodyweight takes: kg, lb."),
    ({"kind": "lifts", "parts": [{**SET, "tempo": 3}]}, "parts[0].tempo",
     "parts[0]: 'tempo' is not a field of a lifts part.", f"A lifts part takes: {LIFT_PART}."),
    ({"kind": "lifts", "parts": [{"movement": "Back Squat", "kg": 100, "reps": 5}]}, "parts[0].kg",
     "parts[0]: 'kg' is not a field of a lifts part.",
     "Send the weight in weight_kg or weight_lb, in the unit the person said."),
    ({"kind": "cardio", "parts": [{**WALK, "distance": 3}]}, "parts[0].distance",
     "parts[0]: 'distance' is not a field of a cardio part.",
     "Send parts[0].distance_km or parts[0].distance_mi, in the unit the person said."),
    ({"kind": "cardio", "parts": [{**WALK, "minute": 1}]}, "parts[0].minute",
     "parts[0]: 'minute' is not a field of a cardio part.", "The nearest field is parts[0].minutes."),
    ({"kind": "bodyweight", "details": [80]}, "details", "details must be an object of the kind's own fields.",
     "Send the fields of bodyweight in details, as an object: kg, lb. guide with topic bodyweight gives them."),
    ({"kind": "bodyweight", "details": "80 kg"}, "details", "details must be an object of the kind's own fields.",
     "as an object: kg, lb"),
    ({"kind": "lifts", "parts": SET}, "parts", "parts must be a list of parts.",
     f"Send parts as a list, one object a part: {LIFT_PART}."),
    ({"kind": "lifts", "parts": '{"movement": "Back Squat"}'}, "parts", "parts must be a list of parts.", LIFT_PART),
    ({"kind": "food", "parts": "dal"}, "parts", "parts must be a list of parts.", "food takes no parts: leave parts out."),
    ({"kind": "lifts", "parts": ["Back Squat 100 x 5"]}, "parts[0]", "parts[0] must be an object of named fields.",
     f"Send each part with its named fields: {LIFT_PART}."),
    ({"kind": "waist", "details": {"kind": "hips", "value": 98}}, "details.kind",
     "kind waist and details.kind hips differ.",
     "Send one measurement's kind: as the kind, or in details.kind with kind measurement."),
    ({"kind": "sleep", "details": {"kind": 7, "value": 7}}, "details.kind", "kind sleep and details.kind 7 differ.",
     "Send one measurement's kind"),
    ({"kind": "bodyweight", "details": {"kg": 80}, "notes": "after breakfast"}, "notes", "bodyweight takes no notes.",
     "Leave notes out: only lifts and cardio take notes."),
    ({"kind": "food", "details": ENTRY["food"]["details"], "notes": "at home"}, "notes", "food takes no notes.",
     "Leave notes out"),
    ({"kind": "sleep", "details": {"value": 7}, "notes": 5}, "notes", "measurement takes no notes.", "Leave notes out"),
]


@pytest.mark.parametrize(("bad", "field", "message", "fix"), SHAPE_REFUSALS,
                         ids=[f"{i}-{row[1]}" for i, row in enumerate(SHAPE_REFUSALS)])
def test_route_refuses_what_the_shape_makes_wrong_naming_log_and_the_field_as_sent(bad, field, message, fix):
    routed = route(bad)
    answer = routed.refusal
    assert routed.arguments is None and answer is not None
    assert routed.kind == log_shape.resolve_kind(bad["kind"])[0]  # the kind rides with its refusal
    assert answer["error"] in ("invalid_field", "missing_field") and answer["field"] == field, answer
    assert answer["message"] == message and fix in answer["fix"], answer
    for text in texts_of(answer):
        assert not FIVE_NAMES.search(text), text
        assert tr.check(text, tr.MESSAGE) == [], text


def test_every_problem_of_the_shape_is_refused_at_once():
    answer = refused({"kind": "cardio", "parts": [{"exercise": "Outdoor Walk", "speed_mph": 3, "reps": 2}],
                      "details": {"minutes": 30}, "kg": 1})
    assert [e["field"] for e in answer["errors"]] == ["kg", "parts[0].exercise", "parts[0].speed_mph", "parts[0].reps",
                                                      "details.minutes"]
    assert answer["message"].startswith("5 problems. ")


def test_notes_on_a_kind_that_takes_none_a_null_is_dropped_and_text_refused():
    for kind in ("bodyweight", "food", "measurement"):
        routed = route({"kind": kind, **ENTRY[kind], "notes": None})
        assert routed.refusal is None and "notes" not in routed.arguments, kind
        assert refused({"kind": kind, **ENTRY[kind], "notes": "felt good"})["field"] == "notes"
    for kind in ("lifts", "cardio"):  # a kind that takes notes takes a null too, as its function does
        assert route({"kind": kind, **ENTRY[kind], "notes": None}).arguments["notes"] is None


JUNK = [True, False, None, "", "abc", "2", -1, 0, 1e308, 10 ** 400, [], [1], {}, {"a": 1}, "\x00\n", "[1", "{"]


def test_route_never_raises_on_any_value_in_any_place():
    seen = 0
    for kind in KINDS:
        base = {"kind": kind, **ENTRY[kind]}
        places = [("kind",), ("parts",), ("details",), *((f,) for f in log_shape.TOP_FIELDS)]
        places += [("details", f) for f in log_shape.DETAIL_FIELDS[kind]] + [("details", "zz")]
        if kind in log_shape.PART_MAP:
            places += [("parts", 0, f) for f in log_shape.PART_FIELDS] + [("parts", 0), ("parts", 0, "zz")]
        for place in places:
            for value in JUNK:
                sent = json.loads(json.dumps(base))
                where = sent
                for step in place[:-1]:
                    where = where.setdefault(step, {}) if isinstance(step, str) else where[step]
                where[place[-1]] = value
                routed = route(sent)
                assert (routed.arguments is None) != (routed.refusal is None), (place, value)
                if routed.arguments is not None:
                    misfit_model(routed.model, routed.arguments)  # the kind's own check takes it from here
                for text in texts_of(routed.refusal or {"message": "", "fix": ""}):
                    assert isinstance(text, str)
                said_as_sent({"error": "x", "message": repr(value), "fix": ""}, kind)
                seen += 1
    assert route("not an object").refusal["field"] == "kind" and route(None).refusal["field"] == "kind"
    assert seen > 1000


# ---------------------------------------------------------------- said_as_sent: a kind's own refusal, as log was sent

# Refusals by the kinds' own services: (the log call, the message, the fix). Each call is routed, read by its kind's
# argument model and run through its step and its real service; the refusal comes back said as sent.
SERVICE_REFUSALS = [
    ({"kind": "lifts", "parts": [SET, {"movement": " ", "weight_kg": 5, "reps": 5}]}, "parts[1]: movement is missing.",
     "Give movement on parts[1]."),
    ({"kind": "lifts", "parts": [SET, SET, {**SET, "reps": 101}]}, "parts[2]: reps 101 is outside 1 to 100.",
     "Give reps on parts[2] from 1 to 100."),
    ({"kind": "lifts", "parts": [{**SET, "weight_lb": 220}]}, "parts[0] has both weight_kg and weight_lb: send one.",
     "Send the weight in the unit the person said: weight_kg or weight_lb."),
    ({"kind": "lifts", "parts": [{"movement": "Back Squat", "reps": 5}]},
     "parts[0]: weight_kg or weight_lb is missing.", "Give weight_kg or weight_lb on parts[0]."),
    ({"kind": "lifts", "parts": [SET, {"movement": "Plank", "weight_kg": 0, "reps": 5}]},
     "parts[1]: Plank records time, not reps.", "Send time_s on parts[1]."),
    ({"kind": "lifts", "parts": [{"movement": "Plank", "weight_kg": 0, "minutes": 120}]},
     "parts[0]: time_s 7200 is outside 1 to 3,600.", "Give time_s on parts[0] from 1 to 3,600, in seconds."),
    ({"kind": "lifts", "parts": [{"movement": "Farmer's Carry", "weight_kg": 30, "time_s": 40}]},
     "parts[0]: distance_m is missing.", "Give distance_m on parts[0]."),
    ({"kind": "lifts", "parts": [SET] * 61}, "The session has 61 sets; it needs 1 to 60.",
     "Send one part per set, 1 to 60 parts: movement, weight_kg or weight_lb, reps."),
    ({"kind": "cardio", "parts": [{**WALK, "minutes": 700}]}, "parts[0].minutes 700 is outside 1 to 600.",
     "Give parts[0].minutes as a number from 1 to 600."),
    ({"kind": "cardio", "parts": [{**WALK, "distance_km": 3, "distance_mi": 2}]},
     "Both parts[0].distance_km and parts[0].distance_mi were sent: send one.",
     "Send the distance in the unit the person said: parts[0].distance_km or parts[0].distance_mi."),
    ({"kind": "cardio", "parts": [{**WALK, "distance_mi": 900}]},
     "parts[0].distance_mi 900 mi is outside 0.01 to 186.41 mi.", "Give parts[0].distance_mi from 0.01 to 186.41 mi."),
    ({"kind": "cardio", "parts": [{**WALK, "distance_km": 5}], "details": {"speed_kmh": 20}},
     "details.speed_kmh 20 does not match 5 km in 30 minutes, which is 10 km/h.",
     "Check the distance, the minutes and the speed, or leave details.speed_kmh out and the app works it out."),
    ({"kind": "cardio", "parts": [WALK], "details": {"speed_mph": 200}},
     "details.speed_mph 200 mph is outside 0.1 to 37.2 mph.", "Give details.speed_mph from 0.1 to 37.2 mph."),
    ({"kind": "cardio", "parts": [{"movement": "Jogging", "minutes": 20}]}, "'Jogging' is not on the exercise list.",
     "Use a name exactly as list_exercises gives it."),
    ({"kind": "bodyweight"}, "details.kg or details.lb is missing.",
     "Give the body weight in details.kg, 20 to 400, or in details.lb, 44.1 to 881.84."),
    ({"kind": "bodyweight", "details": {"kg": 80, "lb": 176}}, "Both details.kg and details.lb were sent: send one.",
     "Send the weight in the unit the person said: details.kg or details.lb."),
    ({"kind": "bodyweight", "details": {"kg": 900}}, "details.kg 900 is outside 20 to 400.",
     "Give the body weight in details.kg, 20 to 400."),
    ({"kind": "bodyweight", "details": {"lb": 9000}}, "details.lb 9000 is outside 44.1 to 881.84 lb.",
     "Give details.lb from 44.1 to 881.84 lb."),
    ({"kind": "measurement", "details": {"kind": "inch", "value": 3}}, "'inch' is not a kind of measurement.",
     "Use one of: waist, hips, chest, arm, thigh, steps, sleep_hours, resting_hr, water_ml."),
    ({"kind": "waist", "details": {"value": 900}}, "waist 900 cm is outside 30 to 300.", "Give waist in cm, 30 to 300."),
    ({"kind": "food", "details": {"entries": [{"description": "Dal", "kcal": 180}, {"description": "Rice"}]}},
     "details.entries[1]: kcal is missing.",
     "Give kcal as a number from 0 to 5,000 on details.entries[1]: it is kept as a whole number."),
]


def check_said(answer: dict, message: str, fix: str) -> None:
    assert answer["message"] == message, answer
    assert answer["fix"] == fix or (fix in answer["fix"] and "list" in answer["message"]), answer
    for text in texts_of(answer):
        assert not LEFT_OVER.search(text), text
        assert not FIVE_NAMES.search(text), text
        assert tr.check(text, tr.MESSAGE) == [], text


@pytest.mark.parametrize(("sent", "message", "fix"), SERVICE_REFUSALS,
                         ids=[f"{i}-{s['kind']}" for i, (s, _, _) in enumerate(SERVICE_REFUSALS)])
def test_a_service_s_own_refusal_is_said_as_log_was_sent(make_user, sent, message, fix):
    user = make_user()
    routed = route(sent)
    assert routed.refusal is None and misfit_model(routed.model, routed.arguments) is None
    answer = LOG_STEPS[routed.function](user.id, **dict(read_arguments(routed.model, routed.arguments)))
    assert "error" in answer, answer  # the service refused: nothing written
    said = said_as_sent(answer, routed.kind)
    check_said(said, message, fix)
    rest = ("message", "fix", "errors", "field")
    assert {k: v for k, v in said.items() if k not in rest} == {k: v for k, v in answer.items() if k not in rest}
    assert ("field" in said) == ("field" in answer)  # none is added


# A refusal of the kind's own argument model, as schema_refusal gives it for the kind's function.
SCHEMA_REFUSALS = [
    ({"kind": "lifts", "parts": [SET, {"weight_kg": 5, "reps": 5}]}, "parts[1]: movement is missing.",
     "Give movement on parts[1]."),
    ({"kind": "lifts", "parts": [{**SET, "movement": 5}]}, "parts[0]: movement must be text, not 5.",
     "Give movement on parts[0] as text."),
    ({"kind": "lifts", "parts": [{**SET, "exact": "yes"}]}, "parts[0]: exact must be true or false, not 'yes'.",
     "Send exact as true or false, or leave it out."),
    ({"kind": "lifts", "parts": [{**SET, "reps": "five"}]}, "parts[0]: reps must be a whole number, not 'five'.",
     "Give reps on parts[0] as a whole number."),
    ({"kind": "lifts", "parts": [SET], "details": {"new_session": "yes"}},
     "details.new_session must be true or false, not 'yes'.",
     "Send details.new_session as true or false, or leave it out."),
    ({"kind": "cardio", "parts": [{"movement": "Outdoor Walk"}]}, "parts[0].minutes is missing.",
     "Give parts[0].minutes."),
    ({"kind": "cardio", "parts": [{}]}, "2 problems. parts[0].movement is missing. parts[0].minutes is missing.",
     "Give parts[0].movement. Give parts[0].minutes."),
    ({"kind": "cardio", "parts": [WALK], "details": {"avg_hr": 100.5}},
     "details.avg_hr must be a whole number, not 100.5.", "Give details.avg_hr as a whole number."),
    ({"kind": "bodyweight", "details": {"kg": "eighty"}}, "details.kg must be a number, not 'eighty'.",
     "Give details.kg as a number."),
    ({"kind": "measurement", "details": {"value": 86}}, "details.kind is missing.", "Give details.kind."),
    ({"kind": "waist"}, "details.value is missing.", "Give details.value."),
    ({"kind": "waist", "details": {"value": "big"}}, "details.value must be a number, not 'big'.",
     "Give details.value as a number."),
    ({"kind": "food"}, "details.entries is missing.", "Give details.entries."),
    ({"kind": "food", "details": {"entries": "dal"}}, "details.entries must be a list, not 'dal'.",
     "Send details.entries as a list."),
    ({"kind": "food", "details": {"entries": [{"description": "Dal", "calories": 180}]}},
     "details.entries[0]: 'calories' is not a field of a food entry.",
     "Use: description, meal, kcal, estimated, saved_food_id, servings, usda_fdc_id, grams. source is decided by the "
     "server. " + mcp_schemas.NEAR_MISSES["entry"]["calories"]),
    ({"kind": "lifts", "parts": [{**SET, "weight_kg": True}]}, "parts[0]: weight_kg must be a number, not true.",
     "Give weight_kg on parts[0] as a number."),
]


@pytest.mark.parametrize(("sent", "message", "fix"), SCHEMA_REFUSALS,
                         ids=[f"{i}-{s['kind']}" for i, (s, _, _) in enumerate(SCHEMA_REFUSALS)])
def test_a_schema_refusal_of_the_kind_s_model_is_said_as_log_was_sent(sent, message, fix):
    routed = route(sent)
    assert routed.refusal is None
    found = misfit_model(routed.model, routed.arguments)
    assert found is not None and found[0] == []  # route lets no unknown key through to the kind's model
    answer = schema_refusal(routed.function, routed.model.model_json_schema(), routed.arguments, *found)
    check_said(said_as_sent(answer, routed.kind), message, fix)


def test_a_refusal_that_names_no_field_of_the_kind_is_not_reworded():
    for answer in ({"error": "too_many_writes", "message": "31 write calls in the last 10 minutes, this one included; "
                    "the most is 30. Nothing was saved.", "fix": "Wait 3 minutes, then send it again.",
                    "wait_minutes": 3},
                   mcp_server.database_unavailable(), mcp_server.internal_error("log"),
                   {"error": "not_allowed", "message": mcp_server.NOT_ALLOWED_MESSAGES["log"],
                    "fix": mcp_server.NOT_ALLOWED_FIX}, services.common.invalid_key()):
        for kind in KINDS:
            assert said_as_sent(answer, kind) == answer, (kind, answer)
    assert said_as_sent({"message": "x"}, None) == {"message": "x"} and said_as_sent("text", "lifts") == "text"


def test_a_function_s_name_in_a_refusal_is_said_as_log():
    answer = mcp_server.unknown_key("log_cardio", "zone", ["exercise", "minutes", "avg_hr"])
    said = said_as_sent(answer, "cardio")
    assert said["message"] == "'zone' is not a field of log."
    assert said["fix"] == "log takes: parts[0].movement, parts[0].minutes, details.avg_hr."
    assert said_as_sent(mcp_server.internal_error("log_food"), "food")["message"] == "log failed inside Spotter."


def test_key_shown_is_mcp_server_s_own():
    for key in ("zone", "a\nb", "tab\there", "​zero", "x" * 200, "\ud83d", "café", 5):
        assert log_shape.key_shown(key) == mcp_server.key_shown(key), key


# ---------------------------------------------------------------- log's words

def test_log_s_words_are_the_contract_s():
    assert log_shape.LOG_KIND_DESCRIPTION == (
        "What is logged, in a word: lifts, bodyweight, measurement, food or cardio, or a measurement's own word, such "
        "as waist, sleep, steps, resting heart rate or water. guide with topic log names every kind.")
    assert log_shape.LOG_PARTS_DESCRIPTION == (
        "Lifts: one part a set, in order, 1 to 60, each with movement and what the lift records: a weight in "
        "weight_kg or weight_lb and reps, a hold's time_s, or a carry's distance_m and time_s. Only sets not yet "
        "logged: earlier sets of that date are already in its session. Cardio: exactly one part, the session, with "
        "movement and minutes, and distance_km or distance_mi when the person gave a distance. Bodyweight, measurement "
        "and food take no parts: leave it out.")
    assert log_shape.MOVEMENT_DESCRIPTION == (
        "The lift in the person's own words, such as squat, or a name from list_exercises. Cardio: the activity "
        "exactly as list_exercises gives it, such as Outdoor Walk or Treadmill Walk. " + mcp_schemas.CARDIO_RULE)
    line = " Spotter converts it to what the kind keeps."
    assert log_shape.SHARED_PART_DESCRIPTIONS == {
        "time_s": "A time in seconds, as the person said it: a hold, a carry or a cardio session." + line,
        "minutes": "A time in minutes, as the person said it." + line,
        "distance_m": "A distance in metres, as the person said it." + line,
        "distance_km": "A distance in km, as the person said it, or with no unit and units.distance is km." + line,
        "distance_mi": "A distance in miles, as the person said it, or with no unit and units.distance is mi." + line}
    assert log_shape.LOG_ROUTING == "Record an entry with log. Call guide with its kind before any details. "
    assert log_shape.LOG_KEY_DESCRIPTION is mcp_schemas.KEY_DESCRIPTION
    assert log_shape.LOG_DESCRIPTION.startswith("Records one entry of a kind: lifts, bodyweight, measurement, food or ")


def test_log_part_carries_each_field_s_words_and_type():
    assert log_shape.PART_DESCRIPTIONS == {
        **log_shape.SHARED_PART_DESCRIPTIONS, "movement": log_shape.MOVEMENT_DESCRIPTION,
        **{f: "Lifts: " + mcp_schemas.LogSetIn.model_fields[f].description
           for f in ("weight_kg", "weight_lb", "reps", "exact")}}
    schema = mcp_schemas.LogPartIn.model_json_schema()
    types = {name: [o["type"] for o in node["anyOf"] if o["type"] != "null"]
             for name, node in schema["properties"].items()}
    assert types == {"movement": ["string"], "weight_kg": ["number"], "weight_lb": ["number"], "reps": ["integer"],
                     "time_s": ["integer"], "distance_m": ["number"], "exact": ["boolean"], "minutes": ["number"],
                     "distance_km": ["number"], "distance_mi": ["number"]}
    assert schema["additionalProperties"] is False and "required" not in schema and "description" not in schema
    with pytest.raises(ValueError):
        mcp_schemas.LogPartIn.model_validate({"movement": "Plank", "seconds": 60})


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def every_model_read_text():
    """(the text, its surface): log's words, guide's, and route's and guide's own refusals."""
    for name in ("LOG_DESCRIPTION", "LOG_KIND_DESCRIPTION", "LOG_PARTS_DESCRIPTION", "LOG_DETAILS_DESCRIPTION",
                 "LOG_DATE_DESCRIPTION", "LOG_NOTES_DESCRIPTION", "LOG_REPEAT_DESCRIPTION", "LOG_ROUTING"):
        yield getattr(log_shape, name), tr.CLAUDE
    yield from ((text, tr.CLAUDE) for text in log_shape.PART_DESCRIPTIONS.values())
    yield from ((field.description, tr.CLAUDE) for field in mcp_schemas.GuideResult.model_fields.values())
    yield from ((text, tr.CLAUDE) for text in (guide.GUIDE_DESCRIPTION, guide.GUIDE_TOPIC_DESCRIPTION,
                                               guide.MEASURE_NOTE, *guide.PARTS_TAKEN.values()))
    for answer in guide.ANSWERS.values():
        yield from ((text, tr.CLAUDE) for text in _strings(answer))
    others = [{"kind": "yoga"}, {"kind": "lifs"}, {}, {"kind": "lifts", "x": 1},
              {"kind": "lifts", "parts": [{"movement": "Plank", "minutes": 0.01}]},
              {"kind": "lifts", "parts": [{"movement": "Plank", "minutes": "x"}]},
              {"kind": "cardio", "parts": [{**WALK, "time_s": 60}]}]
    for bad in [row[0] for row in SHAPE_REFUSALS] + others:
        yield from ((text, tr.MESSAGE) for text in texts_of(route(bad).refusal))
    for bad in ({}, {"topic": "food", "kind": "cardio"}, {"topic": "yoga"}):
        yield from ((text, tr.MESSAGE) for text in texts_of(guide.answer(bad)))


def test_every_new_model_read_text_keeps_the_text_rules():
    texts = list(every_model_read_text())
    assert len(texts) > 100
    for text, surface in texts:
        assert tr.check(text, surface) == [], text


# ---------------------------------------------------------------- guide

GUIDE_SIZES = {"log": 1894, "lifts": 2884, "bodyweight": 1311, "measurement": 1404, "food": 3639, "cardio": 2864}


def test_guide_has_an_answer_for_each_topic_and_each_is_a_guide_result():
    assert tuple(guide.ANSWERS) == guide.TOPICS == ("log", "lifts", "bodyweight", "measurement", "food", "cardio")
    for topic, answer in guide.ANSWERS.items():
        assert answer["topic"] == topic
        mcp_schemas.GuideResult.model_validate(answer)
    used = {key for answer in guide.ANSWERS.values() for key in answer}
    assert used == set(mcp_schemas.GuideResult.model_fields)  # every field of the result is used by some answer
    with pytest.raises(ValueError):
        mcp_schemas.GuideResult.model_validate({**guide.ANSWERS["food"], "surprise": 1})


def test_each_guide_answer_s_size_is_pinned(capsys):
    sizes = {topic: len(compact(answer)) for topic, answer in guide.ANSWERS.items()}
    with capsys.disabled():
        print("\nguide answer sizes, compact JSON characters:", sizes)
    assert sizes == GUIDE_SIZES


S_ = mcp_schemas
KIND_WORDS_OF = {  # each kind's description and top-field words, as the constants hold them
    "lifts": (S_.LOG_LIFTS_DESCRIPTION, {"date": S_.LIFTS_DATE_DESCRIPTION, "notes": S_.LIFTS_NOTES_DESCRIPTION,
                                         "allow_repeat": S_.LIFTS_ALLOW_REPEAT_DESCRIPTION, "key": S_.KEY_DESCRIPTION}),
    "bodyweight": (S_.LOG_BODYWEIGHT_DESCRIPTION, {"date": S_.BODYWEIGHT_DATE_DESCRIPTION,
                                                   "allow_repeat": S_.BODYWEIGHT_ALLOW_REPEAT_DESCRIPTION,
                                                   "key": S_.KEY_DESCRIPTION}),
    "measurement": (S_.LOG_MEASUREMENT_DESCRIPTION, {"date": S_.MEASUREMENT_DATE_DESCRIPTION,
                                                     "allow_repeat": S_.MEASUREMENT_ALLOW_REPEAT_DESCRIPTION,
                                                     "key": S_.KEY_DESCRIPTION}),
    "food": (S_.LOG_FOOD_DESCRIPTION, {"date": S_.FOOD_DATE_DESCRIPTION,
                                       "allow_repeat": S_.FOOD_ALLOW_REPEAT_DESCRIPTION, "key": S_.KEY_DESCRIPTION}),
    "cardio": (S_.LOG_CARDIO_DESCRIPTION, {"date": S_.CARDIO_DATE_DESCRIPTION, "notes": S_.CARDIO_NOTES_DESCRIPTION,
                                           "allow_repeat": S_.CARDIO_ALLOW_REPEAT_DESCRIPTION,
                                           "key": S_.KEY_DESCRIPTION}),
}
DETAIL_WORDS = {"lifts": {"new_session": S_.LIFTS_NEW_SESSION_DESCRIPTION},
                "bodyweight": {"kg": S_.BODYWEIGHT_KG_DESCRIPTION, "lb": S_.BODYWEIGHT_LB_DESCRIPTION},
                "measurement": {"kind": S_.MEASUREMENT_KIND_DESCRIPTION, "value": S_.MEASUREMENT_VALUE_DESCRIPTION},
                "food": {"entries": S_.FOOD_ENTRIES_DESCRIPTION},
                "cardio": {"incline_pct": S_.CARDIO_INCLINE_PCT_DESCRIPTION,
                           "speed_kmh": S_.CARDIO_SPEED_KMH_DESCRIPTION, "speed_mph": S_.CARDIO_SPEED_MPH_DESCRIPTION,
                           "avg_hr": S_.CARDIO_AVG_HR_DESCRIPTION, "machine_kcal": S_.CARDIO_MACHINE_KCAL_DESCRIPTION}}
SET_WORDS = {f: x.description for f, x in S_.LogSetIn.model_fields.items()}
PART_WORDS = {"lifts": {"movement": SET_WORDS["exercise"],
                        **{f: SET_WORDS[f] for f in ("weight_kg", "weight_lb", "reps", "time_s", "distance_m", "exact")}},
              "cardio": {"movement": S_.CARDIO_EXERCISE_DESCRIPTION, "minutes": S_.CARDIO_MINUTES_DESCRIPTION,
                         "distance_km": S_.CARDIO_DISTANCE_KM_DESCRIPTION,
                         "distance_mi": S_.CARDIO_DISTANCE_MI_DESCRIPTION}}


@pytest.mark.parametrize("kind", KINDS)
def test_each_guide_answer_is_its_kind_s_own_description_and_field_words(kind):
    answer = guide.ANSWERS[kind]
    description, top = KIND_WORDS_OF[kind]
    assert answer["how"] == description and answer["top"] == top
    details = answer["details"]
    assert {f: node["description"] for f, node in details["properties"].items()} == DETAIL_WORDS[kind]
    schema = LOG_ARGUMENTS[log_shape.KIND_FUNCTIONS[kind]].model_json_schema()
    assert details["properties"] == {f: schema["properties"][f] for f in DETAIL_WORDS[kind]}  # ranges, types, refs
    assert details["required"] == [f for f in schema.get("required", []) if f in DETAIL_WORDS[kind]]
    assert details["additionalProperties"] is False and details["type"] == "object"
    parts = answer["parts"]
    assert parts["takes"] == guide.PARTS_TAKEN[kind] + (" " + S_.LIFTS_SETS_DESCRIPTION if kind == "lifts" else "")
    assert parts["fields"] == PART_WORDS.get(kind, {})
    assert parts.get("converted") == (S_.CONVERTS_LINE if kind in ("lifts", "cardio") else None)
    assert parts.get("each") == (S_.LogSetIn.__doc__ if kind == "lifts" else None)
    assert answer.get("note") == (guide.MEASURE_NOTE if kind == "measurement" else None)
    if kind == "food":  # the food rule and each entry field's own words reach guide food unchanged
        assert S_.FOOD_RULE in answer["how"] and S_.FOOD_NAME_RULE in answer["how"]
        entry = details["$defs"]["FoodEntryIn"]
        assert entry == schema["$defs"]["FoodEntryIn"]
        assert {f: n["description"] for f, n in entry["properties"].items()} == {
            f: x.description for f, x in S_.FoodEntryIn.model_fields.items()}
    else:
        assert "$defs" not in details
    if kind == "cardio":
        assert S_.CARDIO_RULE in parts["fields"]["movement"]
    if kind == "measurement":
        for word in ("sleep_hours", "steps", "resting_hr", "water_ml"):
            assert word in answer["how"]


def test_each_guide_answer_matches_its_registered_function(http):
    tools = registered_tools(http)
    for kind, function in log_shape.KIND_FUNCTIONS.items():
        tool, answer = tools[function], guide.ANSWERS[kind]
        assert answer["how"] == tool["description"]
        for field, node in answer["details"]["properties"].items():
            assert node == tool["inputSchema"]["properties"][field], (kind, field)
        for field, words in answer["top"].items():
            assert words == tool["inputSchema"]["properties"][field]["description"], (kind, field)


def test_guide_log_names_every_kind_with_its_words():
    answer = guide.ANSWERS["log"]
    assert answer["how"] == log_shape.LOG_DESCRIPTION
    assert [k["kind"] for k in answer["kinds"]] == list(KINDS)
    for row in answer["kinds"]:
        kind = row["kind"]
        assert row["records"] == guide.ANSWERS[kind]["how"].split(". ")[0] + "."
        assert row["parts"] == guide.PARTS_TAKEN[kind] and row["details"] == list(log_shape.DETAIL_FIELDS[kind])
        assert all(log_shape.resolve_kind(word)[0] == kind for word in row["words"]), kind
    every = [w for row in answer["kinds"] for w in row["words"]]
    assert sorted(every) == sorted(log_shape.KIND_WORDS)
    measurement = next(row for row in answer["kinds"] if row["kind"] == "measurement")
    for word in ("sleep", "steps", "resting heart rate", "water"):
        assert word in measurement["words"]


def test_guide_names_none_of_the_five_but_where_food_entry_s_docstring_does():
    """FoodEntryIn's docstring names log_food (the contract's row 6 rewords it in package L2): the only such name."""
    hits = {topic: FIVE_NAMES.findall(compact(answer)) for topic, answer in guide.ANSWERS.items()}
    doc = FIVE_NAMES.findall(S_.FoodEntryIn.__doc__)
    assert hits == {"log": [], "lifts": [], "bodyweight": [], "measurement": [], "food": doc, "cardio": []}


@pytest.mark.parametrize("topic", ["food", "Food", "food_entry", "meal", "log food", "lifts", "lift_session", "gym",
                                   "Body weight", "sleep", "waist", "cardio_entry", "run", "log", "Log", "kinds",
                                   "logging"])
def test_guide_reads_a_topic_as_log_reads_a_kind_and_kind_as_topic(topic):
    by_topic = guide.answer({"topic": topic})
    folded = log_shape.fold(topic)
    expected = "log" if folded in guide.LOG_TOPIC_WORDS else log_shape.resolve_kind(folded.removeprefix("log "))[0]
    assert "error" not in by_topic and by_topic == guide.ANSWERS[expected]
    assert guide.answer({"kind": topic}) == by_topic
    assert guide.answer({"topic": topic, "kind": topic}) == by_topic
    assert guide.answer({"topic": topic, "kind": folded}) == by_topic  # the same word, folded


def test_guide_refuses_two_topics_an_unknown_topic_and_any_other_field():
    differ = guide.answer({"topic": "food", "kind": "cardio"})
    one = {"error": "invalid_field", "message": "guide takes one topic: topic 'food' and kind 'cardio' are not the same.",
           "fix": "Send topic alone, such as lifts or food, or log for every kind.", "field": "topic"}
    assert differ == {**one, "errors": [one]}
    assert guide.answer({"topic": "food"})["topic"] == "food"  # the fix goes through
    unknown = guide.answer({"kind": "yoga"})
    assert unknown["message"] == "'yoga' is not a topic of guide." and unknown["field"] == "topic"
    assert unknown["fix"] == "Send a kind: lifts, bodyweight, measurement, food or cardio. Or log, for every kind."
    for bad in ({}, {"kind": 3}, {"topic": "food", "kind": None}, {"kind": "food", "sets": 1},
                {"topic": "food", "kinds": "food"}, None, "food"):
        assert guide.answer(bad)["message"] == "guide takes one field, topic: a kind, or log.", bad


def test_a_guide_answer_is_a_copy():
    first = guide.answer({"topic": "food"})
    first["details"]["properties"].clear()
    assert guide.answer({"topic": "food"})["details"]["properties"]


# ---------------------------------------------------------------- what the two modules import

def _app_imports(path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "app":
            found.append((node.module, sorted(alias.name for alias in node.names)))
        if isinstance(node, ast.Import):
            found += [(alias.name, []) for alias in node.names if alias.name.split(".")[0] == "app"]
    return found


def test_log_shape_and_guide_import_only_what_the_contract_allows():
    shape = _app_imports(ROOT / "app" / "log_shape.py")
    assert {module for module, _ in shape} == {"app", "app.mcp_schemas"}
    assert [names for module, names in shape if module == "app"] == [["rules"]]
    guided = _app_imports(ROOT / "app" / "guide.py")
    assert {module for module, _ in guided} == {"app", "app.log_shape", "app.mcp_schemas"}
    assert [names for module, names in guided if module == "app"] == [["rules"]]


@pytest.mark.parametrize("module", ["app.log_shape", "app.guide"])
def test_the_module_imported_alone_loads_no_server_no_app_and_no_call_log(tmp_path, module):
    code = ("import json, sys; import " + module + "; "
            "print(json.dumps([m for m in ('app.main', 'app.mcp_server', 'app.calllog') if m in sys.modules]))")
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env.update({"PYTHONPATH": str(ROOT), "DATABASE_URL": f"sqlite:///{(tmp_path / 'alone.db').as_posix()}"})
    done = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == []
    assert not (ROOT / "spotter.db").exists() and not (tmp_path / "spotter.db").exists()
