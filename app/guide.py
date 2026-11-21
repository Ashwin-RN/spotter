"""guide (contract docs/contracts/log-and-guide.md): how to log each kind log takes, by topic.

Each answer is built once, at import, from the five log functions' own words: each one's description constant and its
argument model's schema (mcp_schemas.LOG_ARGUMENTS), which equals the function's published input schema. So while the
five are listed, guide and the five cannot disagree, and when the five go the words stay. For a kind: its own
description; what its parts take, with each part field's own words and the line on the other forms Spotter converts;
its details as a schema, with the five's own words, units and ranges; and the words of the fields at log's top. For
topic log: every kind, its words, what it records, its parts and its details.

kind, the field log takes the word by, is read as topic (decision 44); both sent with two words is refused.

It imports app.log_shape and app.mcp_schemas from the app, never app.main, app.mcp_server or app.calllog.
"""

import copy
import json
from typing import Any

from app import rules
from app.log_shape import (
    DETAIL_FIELDS, KIND_FUNCTIONS, KINDS, LOG_DESCRIPTION, MEASURE_WORDS, OPEN_WORDS, PART_MAP, TOP_FIELDS, fold,
    resolve_kind,
)
from app.mcp_schemas import (
    CONVERTS_LINE, LOG_ARGUMENTS, LOG_BODYWEIGHT_DESCRIPTION, LOG_CARDIO_DESCRIPTION, LOG_FOOD_DESCRIPTION,
    LOG_LIFTS_DESCRIPTION, LOG_MEASUREMENT_DESCRIPTION,
)

# guide's own words (model-read).
GUIDE_DESCRIPTION = (
    "How to log one kind of entry: what its parts take, and the fields log takes in details for that kind, with "
    "their units and rules. With topic log, every kind Spotter logs."
)
GUIDE_TOPIC_DESCRIPTION = "A kind, as log takes it, such as lifts or food. Or log, for every kind."

TOPICS = ("log", *KINDS)
# Each kind's function's own description, by the kind.
DESCRIPTIONS = {"lifts": LOG_LIFTS_DESCRIPTION, "bodyweight": LOG_BODYWEIGHT_DESCRIPTION,
                "measurement": LOG_MEASUREMENT_DESCRIPTION, "food": LOG_FOOD_DESCRIPTION,
                "cardio": LOG_CARDIO_DESCRIPTION}
PARTS_TAKEN = {"lifts": "One part a set.", "cardio": "Exactly 1 part: the session.",
               "bodyweight": "None: leave parts out.", "food": "None: leave parts out.",
               "measurement": "None: leave parts out."}
MEASURE_NOTE = ("details.kind is required with log's kind measurement. A measurement's own word as log's kind, such "
                "as waist or sleep_hours, fills in details.kind: it may then be left out, or sent as the same word.")
LOG_TOPIC_WORDS = ("log", "kinds", "logging")  # what reads as topic log, after fold


def _set_schema(schema: dict) -> dict:
    """The schema of one item of log_lifts' sets, through the list's own $ref: mcp_schemas.LogSetIn."""
    ref = schema["properties"]["sets"]["items"]["$ref"]
    return schema["$defs"][ref.rsplit("/", 1)[-1]]


def _part_words(schema: dict, kind: str) -> dict:
    """Each part field the kind keeps, with its function's own words for it."""
    if kind not in PART_MAP:
        return {}
    properties = (_set_schema(schema) if kind == "lifts" else schema)["properties"]
    return {sent: properties[own]["description"] for sent, own in PART_MAP[kind].items()}


def kind_answer(kind: str) -> dict:
    """guide's answer for one kind, from its function's description and argument model."""
    schema = LOG_ARGUMENTS[KIND_FUNCTIONS[kind]].model_json_schema()
    properties = schema["properties"]
    own = {k: copy.deepcopy(properties[k]) for k in DETAIL_FIELDS[kind]}
    details = {"type": "object", "properties": own, "additionalProperties": False,
               "required": [k for k in schema.get("required", []) if k in own]}
    used = {name: node for name, node in schema.get("$defs", {}).items() if f"#/$defs/{name}" in json.dumps(own)}
    if used:
        details["$defs"] = copy.deepcopy(used)
    taken = PARTS_TAKEN[kind] + (" " + properties["sets"]["description"] if kind == "lifts" else "")
    parts = {"takes": taken, "fields": _part_words(schema, kind)}
    if kind == "lifts":  # the words the function's list has for one set
        parts["each"] = _set_schema(schema)["description"]
    if kind in PART_MAP:  # a time or a distance in another form (decision 51)
        parts["converted"] = CONVERTS_LINE
    answer = {"topic": kind, "how": DESCRIPTIONS[kind], "parts": parts, "details": details,
              "top": {k: properties[k]["description"] for k in TOP_FIELDS if k in properties}}
    if kind == "measurement":
        answer["note"] = MEASURE_NOTE
    return answer


def log_answer(kinds: dict) -> dict:
    """guide's answer for topic log: every kind with its words, the first sentence of its description, its parts and
    its details."""
    words: dict[str, list] = {}
    for word, kind in OPEN_WORDS.items():
        words.setdefault(kind, []).append(word)
    words["measurement"] += sorted(MEASURE_WORDS)
    return {"topic": "log", "how": LOG_DESCRIPTION,
            "kinds": [{"kind": k, "words": words[k], "records": kinds[k]["how"].split(". ")[0] + ".",
                       "parts": PARTS_TAKEN[k], "details": list(DETAIL_FIELDS[k])} for k in KINDS]}


def build() -> dict:
    """Every topic's answer, by topic."""
    kinds = {kind: kind_answer(kind) for kind in KINDS}
    return {"log": log_answer(kinds), **kinds}


ANSWERS = build()


def _refusal(message: str, fix: str) -> dict:
    return rules.combine([rules.error("invalid_field", message, fix, field="topic")])


def answer(arguments: Any) -> dict:
    """guide's answer for a call's arguments as sent: a topic's answer, or a refusal. kind is read as topic; both sent
    must fold to the same word. A copy, so a caller may change it."""
    arguments = arguments if isinstance(arguments, dict) else {}
    unknown = [k for k in arguments if k not in ("topic", "kind")]
    sent = [arguments[k] for k in ("topic", "kind") if k in arguments]
    if unknown or not sent or not all(isinstance(word, str) for word in sent):
        return _refusal("guide takes one field, topic: a kind, or log.",
                        "Send topic, such as lifts or food, or log for every kind.")
    topic = sent[0]
    if fold(topic) != fold(sent[-1]):
        return _refusal(f"guide takes one topic: topic '{rules.quote(topic)}' and kind '{rules.quote(sent[-1])}' are "
                        "not the same.", "Send topic alone, such as lifts or food, or log for every kind.")
    folded = fold(topic)
    if folded.startswith("log ") and folded != "log":
        folded = folded[4:]
    if folded in LOG_TOPIC_WORDS:
        return copy.deepcopy(ANSWERS["log"])
    found = resolve_kind(folded)
    if found is None:
        return _refusal(f"'{rules.quote(topic)}' is not a topic of guide.",
                        "Send a kind: lifts, bodyweight, measurement, food or cardio. Or log, for every kind.")
    return copy.deepcopy(ANSWERS[found[0]])
