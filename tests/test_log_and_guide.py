"""Log and guide (contract log-and-guide.md), package L0: the five log functions' words as named constants in
app/mcp_schemas.py, an argument model for each, misfit's form for an argument model, and the step each function takes
from its arguments to its service. Nothing published moves: tests/test_feedback.py's PUBLISHED hashes and
INSTRUCTIONS_AS_BUILT hold as they are."""

import ast
import hashlib
import json

import pytest

from app import mcp_schemas, mcp_server
from app.auth import JwksTokenVerifier, UserinfoClient
from app.config import ROOT
from app.mcp_schemas import LOG_ARGUMENTS
from app.mcp_server import LOG_STEPS, misfit, misfit_model, read_arguments, schema_refusal

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
