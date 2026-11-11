"""The pure parts of the evals: cases, placeholders, the CLI output parser, the judging and the token mask.

Nothing here starts a server, opens a database or calls the model, so all of it is unit tested.

A case (evals/cases.json):

  {"id": "...", "say": "what the person types", "expect": {...}, "switch": "..."?, "seed": [...]?, "db": [...]?}

switch, what the person lets Claude do (read, log or log_delete), set through services.set_claude_can
before the run, as the Settings page sets it. Left out: the default, log.

expect, every key optional, all of them must hold:

  function          this function was called at least once. A refusal (isError) still counts as called.
  inputs_include    at least one call of `function` sent arguments containing this (nested subset: a dict
                    matches when every key matches; a list when every expected item matches some sent item;
                    numbers compare as numbers, so 180 matches 180.0; strings ignore case and outer spaces;
                    a leaf {"$in": [a, b]} matches any one of them).
  inputs_exclude    paths that are absent (or null) in every call of `function`. "entries.*.kcal" means the
                    kcal of every item of entries.
  inputs_forbid     {path: value}: no call of `function` sent that value at that path.
  ok_calls          {function: n}: exactly n calls of that function succeeded (not isError).
  no_call           functions that were never called, refused or not.
  refused           true: every call of `function` was answered isError.
  reply_asks        true: the final reply asks the person something (it contains a question mark).
  reply_has         phrases the final reply must contain, every one (ignoring case).
  reply_lacks       phrases the final reply must not contain (ignoring case).
  reply_lacks_pattern
                    regular expressions the final reply must not match anywhere (re.search, ignoring case).
                    Each must compile: a pattern that does not is a problem when the cases are loaded, and an
                    error when judged, never a pass.
  any_of            a list of expects of this same shape; at least one must hold.

seed, rows created for the person before the run, through Spotter's own services, in order:

  {"as": "roti", "service": "save_food" | "log_bodyweight" | "log_measurement" | "set_goal", "data": {...}}
  {"service": "add_sample_data"}    a service that takes no data: four weeks of everything, three goals.
                                    No "as": it returns no one id.

db, checks on the person's rows after the run:

  {"table": "food_entry", "state": "live", "count": 1, "all": {...}, "rows": [{...}, ...]}
  state: live (the default: rows not deleted) or deleted (soft-deleted rows only);
  count: exactly this many such rows; all: every one matches; rows: each matcher matches some row.

Placeholders, as a whole string: "{{today}}" and "{{yesterday}}" (the person's own dates, YYYY-MM-DD),
and "{{next_month_end:N}}" (N from 1 to 12: the last day of month N, the next one strictly after today
in the person's zone), anywhere in seed, expect and db; "{{seed.<as>}}" (the id of the seeded row) in
expect and db. resolve() replaces them before seeding and before judging.
"""

import json
import re
from pathlib import Path
from typing import Any

CASES_FILE = Path(__file__).resolve().parent / "cases.json"
HIDDEN = "[token hidden]"

EXPECT_KEYS = {"function", "inputs_include", "inputs_exclude", "inputs_forbid", "ok_calls", "no_call",
               "refused", "reply_asks", "reply_has", "reply_lacks", "reply_lacks_pattern", "any_of"}
CASE_KEYS = {"id", "say", "expect", "switch", "seed", "db"}
DB_KEYS = {"table", "state", "count", "all", "rows"}
DB_STATES = ("live", "deleted")
SWITCHES = ("read", "log", "log_delete")
SEED_SERVICES = {"save_food", "log_bodyweight", "log_measurement", "set_goal"}
NO_DATA_SERVICES = {"add_sample_data"}  # called as service(user_id, by)
DB_TABLES = {"food_entry", "saved_food", "bodyweight", "measurement", "cardio_entry", "lift_session", "goal"}
_PLACEHOLDER = re.compile(r"^\{\{\s*([a-z_]+(?:\.[A-Za-z0-9_]+|:[0-9]+)?)\s*\}\}$")


# ---------------------------------------------------------------- the token mask

def masked(text: str, token: str | None) -> str:
    """The text with every copy of the token replaced. Everything printed or kept goes through this."""
    if not isinstance(text, str):
        text = str(text)
    return text.replace(token, HIDDEN) if token else text


# ---------------------------------------------------------------- cases

def case_problems(case: Any) -> list[str]:
    """What is wrong with one case, or [] when it is usable."""
    if not isinstance(case, dict):
        return ["a case must be an object"]
    problems = [f"missing {k}" for k in ("id", "say", "expect") if k not in case]
    problems += [f"unknown key {k}" for k in sorted(set(case) - CASE_KEYS)]
    if "id" in case and (not isinstance(case["id"], str) or not case["id"].strip()):
        problems.append("id must be a non-empty string")
    if "say" in case and (not isinstance(case["say"], str) or not case["say"].strip()):
        problems.append("say must be a non-empty string")
    if "expect" in case:
        problems += _expect_problems(case["expect"], "expect")
    if "switch" in case and case["switch"] not in SWITCHES:
        problems.append(f"switch must be one of {', '.join(SWITCHES)}")
    for i, seed in enumerate(case.get("seed") or []):
        if isinstance(seed, dict) and seed.get("service") in NO_DATA_SERVICES:
            if set(seed) - {"service"}:
                problems.append(f"seed[{i}]: {seed['service']} takes no data and no as")
        elif not isinstance(seed, dict) or seed.get("service") not in SEED_SERVICES or not isinstance(
                seed.get("data"), dict):
            problems.append(f"seed[{i}] needs a service ({', '.join(sorted(SEED_SERVICES))}) and a data object, "
                            f"or a service that takes none ({', '.join(sorted(NO_DATA_SERVICES))})")
    for i, check in enumerate(case.get("db") or []):
        if not isinstance(check, dict) or check.get("table") not in DB_TABLES:
            problems.append(f"db[{i}] needs a table ({', '.join(sorted(DB_TABLES))})")
        elif set(check) - DB_KEYS:
            problems.append(f"db[{i}] has unknown keys {sorted(set(check) - DB_KEYS)}")
        elif check.get("state", "live") not in DB_STATES:
            problems.append(f"db[{i}] state must be live or deleted")
    return problems


def _expect_problems(expect: Any, where: str) -> list[str]:
    if not isinstance(expect, dict) or not expect:
        return [f"{where} must be a non-empty object"]
    problems = [f"{where} has unknown key {k}" for k in sorted(set(expect) - EXPECT_KEYS)]
    needs_function = {"inputs_include", "inputs_exclude", "inputs_forbid", "refused"} & set(expect)
    if needs_function and "function" not in expect:
        problems.append(f"{where}: {', '.join(sorted(needs_function))} needs function")
    if "reply_lacks_pattern" in expect:
        problems += _pattern_problems(expect["reply_lacks_pattern"], f"{where}.reply_lacks_pattern")
    for i, sub in enumerate(expect.get("any_of") or []):
        problems += _expect_problems(sub, f"{where}.any_of[{i}]")
    return problems


def _pattern_problems(patterns: Any, where: str) -> list[str]:
    """A list of non-empty strings, each a regular expression that compiles. A bare string is refused:
    it would be read one character at a time."""
    if not isinstance(patterns, list) or not patterns:
        return [f"{where} must be a non-empty list of regular expressions"]
    problems = []
    for i, pattern in enumerate(patterns):
        if not isinstance(pattern, str) or not pattern:
            problems.append(f"{where}[{i}] must be a non-empty string")
            continue
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            problems.append(f"{where}[{i}] {pattern!r} is not a valid regular expression: {e}")
    return problems


def load_cases(path: Path = CASES_FILE) -> list[dict]:
    """The cases, checked. Raises ValueError naming every problem."""
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases.json must be a non-empty list of cases")
    problems = []
    seen = set()
    for i, case in enumerate(cases):
        label = case.get("id", f"#{i}") if isinstance(case, dict) else f"#{i}"
        problems += [f"{label}: {p}" for p in case_problems(case)]
        if label in seen:
            problems.append(f"{label}: duplicate id")
        seen.add(label)
    if problems:
        raise ValueError("; ".join(problems))
    return cases


def resolve(value: Any, variables: dict[str, Any]) -> Any:
    """value with every whole-string placeholder replaced from variables ("today", "seed.roti", ...)."""
    if isinstance(value, dict):
        return {k: resolve(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, variables) for v in value]
    if isinstance(value, str):
        match = _PLACEHOLDER.match(value)
        if match:
            name = match.group(1)
            if name not in variables:
                raise KeyError(f"no value for the placeholder {value}")
            return variables[name]
    return value


# ---------------------------------------------------------------- matching

def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def matches(actual: Any, expected: Any) -> bool:
    """Nested subset match. See the module notes."""
    if isinstance(expected, dict) and set(expected) == {"$in"}:
        return any(matches(actual, option) for option in expected["$in"])
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and matches(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and all(any(matches(a, e) for a in actual) for e in expected)
    if _number(expected):
        return _number(actual) and float(actual) == float(expected)
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    if isinstance(expected, str):
        return isinstance(actual, str) and actual.strip().casefold() == expected.strip().casefold()
    return actual == expected


def values_at(data: Any, path: str) -> list[Any]:
    """Every value at a dotted path; "*" steps into each item of a list. Missing steps give nothing."""
    found = [data]
    for step in path.split("."):
        nxt = []
        for item in found:
            if step == "*":
                if isinstance(item, list):
                    nxt.extend(item)
            elif isinstance(item, dict) and step in item:
                nxt.append(item[step])
        found = nxt
    return found


# ---------------------------------------------------------------- judging

def judge_calls(expect: dict, calls: list[dict], reply: str) -> list[str]:
    """Why this run fails the expect, or [] when it passes. calls: [{name, arguments, is_error}]."""
    reasons: list[str] = []
    called = [c["name"] for c in calls]
    function = expect.get("function")
    mine = [c for c in calls if c["name"] == function] if function else []

    if function and not mine:
        reasons.append(f"{function} was not called (calls: {', '.join(called) or 'none'})")
    if function and mine:
        if "inputs_include" in expect and not any(matches(c["arguments"], expect["inputs_include"]) for c in mine):
            reasons.append(f"no {function} call sent {json.dumps(expect['inputs_include'])} "
                           f"(sent: {'; '.join(json.dumps(c['arguments']) for c in mine)})")
        for path in expect.get("inputs_exclude") or []:
            bad = [c for c in mine if any(v is not None for v in values_at(c["arguments"], path))]
            if bad:
                reasons.append(f"{function} sent {path}, which should be left out "
                               f"(sent: {'; '.join(json.dumps(c['arguments']) for c in bad)})")
        for path, value in (expect.get("inputs_forbid") or {}).items():
            bad = [c for c in mine if any(matches(v, value) for v in values_at(c["arguments"], path))]
            if bad:
                reasons.append(f"{function} sent {path} = {json.dumps(value)}")
        if expect.get("refused") and not all(c["is_error"] for c in mine):
            reasons.append(f"{function} was called and not refused")
    for name, n in (expect.get("ok_calls") or {}).items():
        ok = sum(1 for c in calls if c["name"] == name and not c["is_error"])
        if ok != n:
            refused = sum(1 for c in calls if c["name"] == name and c["is_error"])
            reasons.append(f"{name} succeeded {ok} times, expected {n}" + (f" ({refused} refused)" if refused else ""))
    for name in expect.get("no_call") or []:
        if name in called:
            reasons.append(f"{name} was called and should not have been")
    text = reply or ""
    if expect.get("reply_asks") and "?" not in text:
        reasons.append("the reply asks nothing")
    for phrase in expect.get("reply_has") or []:
        if phrase.casefold() not in text.casefold():
            reasons.append(f"the reply does not say {phrase!r}")
    for phrase in expect.get("reply_lacks") or []:
        if phrase.casefold() in text.casefold():
            reasons.append(f"the reply says {phrase!r}")
    if "reply_lacks_pattern" in expect:
        problems = _pattern_problems(expect["reply_lacks_pattern"], "reply_lacks_pattern")
        if problems:
            raise ValueError("; ".join(problems))
        for pattern in expect["reply_lacks_pattern"]:
            found = re.search(pattern, text, re.IGNORECASE)
            if found:
                reasons.append(f"the reply matches {pattern!r}: it says {found.group(0)!r}")
    if expect.get("any_of"):
        options = [judge_calls(sub, calls, reply) for sub in expect["any_of"]]
        if all(options):
            reasons.append("none of: " + " | ".join("; ".join(o) for o in options))
    return reasons


def judge_db(checks: list[dict], rows: dict[str, list[dict]]) -> list[str]:
    """Why the person's rows fail the checks, or []. rows: {table: [every row, deleted ones too, as dicts]}."""
    reasons = []
    for check in checks or []:
        deleted = check.get("state", "live") == "deleted"
        table = check["table"] + (" (deleted)" if deleted else "")
        have = [r for r in rows.get(check["table"], []) if (r.get("deleted_at") is not None) == deleted]
        if "count" in check and len(have) != check["count"]:
            reasons.append(f"{table}: {len(have)} rows, expected {check['count']}")
        if "all" in check:
            bad = [r for r in have if not matches(r, check["all"])]
            if bad:
                reasons.append(f"{table}: rows not matching {json.dumps(check['all'])}: {_rows_text(bad)}")
        for want in check.get("rows") or []:
            if not any(matches(r, want) for r in have):
                reasons.append(f"{table}: no row matching {json.dumps(want)} (rows: {_rows_text(have) or 'none'})")
    return reasons


def _rows_text(rows: list[dict]) -> str:
    keep = ("date", "kind", "value", "kg", "meal", "description", "kcal", "source", "estimated", "saved_food_id",
            "servings", "minutes", "exercise_id", "estimated_kcal", "id", "status", "target_value", "target_date")
    return "; ".join(json.dumps({k: r[k] for k in keep if k in r}) for r in rows)


def judge(case: dict, calls: list[dict], reply: str, rows: dict[str, list[dict]]) -> list[str]:
    """The whole verdict for one run of an already-resolved case: [] is a pass."""
    return judge_calls(case["expect"], calls, reply) + judge_db(case.get("db") or [], rows)


# ---------------------------------------------------------------- the CLI's output

class CliOutputError(ValueError):
    """The CLI's output was not the JSON result document."""


def parse_cli_output(stdout: str) -> dict:
    """The parts of `claude -p --output-format json` the report uses.

    The document is one JSON object of type "result" (or, with --verbose, a list of messages whose last
    "result" message is the one). Returns {reply, subtype, is_error, num_turns, cost_usd, usage, tokens,
    models, permission_denials}. tokens is input + cache creation + cache read + output.
    Raises CliOutputError when there is no such document.
    """
    text = (stdout or "").strip()
    if not text:
        raise CliOutputError("the CLI printed nothing")
    document = None
    for candidate in (text, text.splitlines()[-1]):
        try:
            document = json.loads(candidate)
            break
        except ValueError:
            continue
    if isinstance(document, list):
        results = [m for m in document if isinstance(m, dict) and m.get("type") == "result"]
        document = results[-1] if results else None
    if not isinstance(document, dict) or document.get("type") != "result":
        raise CliOutputError("the CLI output is not a result document")
    usage = document.get("usage") if isinstance(document.get("usage"), dict) else {}
    tokens = sum(int(usage.get(k) or 0) for k in
                 ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
    model_usage = document.get("modelUsage") if isinstance(document.get("modelUsage"), dict) else {}
    # The main model first: the one that cost the most. Small helper models can appear beside it.
    models = sorted(model_usage, key=lambda m: -float((model_usage[m] or {}).get("costUSD") or 0))
    if not models and isinstance(document.get("model"), str):
        models = [document["model"]]
    cost = document.get("total_cost_usd")
    reply = document.get("result")
    return {
        "reply": reply if isinstance(reply, str) else "",
        "subtype": document.get("subtype"),
        "is_error": bool(document.get("is_error")),
        "num_turns": document.get("num_turns"),
        "cost_usd": float(cost) if _number(cost) else 0.0,
        "usage": usage,
        "tokens": tokens,
        "models": models,
        "permission_denials": document.get("permission_denials") or [],
    }


# ---------------------------------------------------------------- the table

def summarise(results: list[dict], n: int, threshold: float) -> tuple[str, bool]:
    """The printed table and whether every case met the threshold.

    results: one per case: {id, runs: [{passed, tokens, cost_usd}]}.
    """
    lines = [f"{'case':<26} {'passes':>7} {'tokens/run':>11} {'cost':>9}"]
    lines.append("-" * len(lines[0]))
    total_pass = total_runs = 0
    total_cost = 0.0
    ok = True
    for r in results:
        runs = r["runs"]
        passes = sum(1 for x in runs if x["passed"])
        tokens = sum(x.get("tokens") or 0 for x in runs) / len(runs) if runs else 0
        cost = sum(x.get("cost_usd") or 0.0 for x in runs)
        total_pass += passes
        total_runs += len(runs)
        total_cost += cost
        rate = passes / len(runs) if runs else 0.0
        below = rate < threshold
        ok = ok and not below
        lines.append(f"{r['id']:<26} {f'{passes}/{len(runs)}':>7} {tokens:>11,.0f} {f'${cost:.4f}':>9}"
                     + ("  below threshold" if below else ""))
    lines.append("-" * len(lines[0]))
    overall = (total_pass / total_runs * 100) if total_runs else 0.0
    lines.append(f"{'overall':<26} {f'{total_pass}/{total_runs}':>7} {'':>11} {f'${total_cost:.4f}':>9}")
    lines.append(f"pass rate {overall:.0f}%, threshold {threshold:g} per case, n = {n}")
    return "\n".join(lines), ok
