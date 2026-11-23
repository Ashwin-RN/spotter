"""The pure parts of the evals: cases, placeholders, the CLI output parser, the judging and the token mask.

Nothing here starts a server, opens a database or calls the model, so all of it is unit tested.

A case (evals/cases.json):

  {"id": "...", "say": "what the person types", "expect": {...}, "then": "..."?, "switch": "..."?, "seed": [...]?,
   "db": [...]?}

then, a second line the person sends in the same conversation after the first reply (contract exercise-list.md,
C4g). The case is judged on the calls of both turns and on the final reply. One line, as say is. A write that
succeeded in the first turn fails it (judge_turns): the second line must be the answer the write waited for.
evals/run.py runs it on the Claude CLI; on Codex and the Gemini runner it fails unrun, with its reason.

switch, what the person lets Claude do (log or log_delete), set through services.set_claude_can
before the run, as the Settings page sets it. Left out: the default, log_delete. Any other value, read among
them, is a problem when the cases are loaded.

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
  reply_has_pattern regular expressions the final reply must match, every one (re.search, ignoring case). Each
                    must compile, as reply_lacks_pattern's must. For a figure that must stand alone, such as
                    3.3 and never the 3.3 inside 93.3.
  reply_lacks       phrases the final reply must not contain (ignoring case).
  reply_lacks_pattern
                    regular expressions the final reply must not match anywhere (re.search, ignoring case).
                    Each must compile: a pattern that does not is a problem when the cases are loaded, and an
                    error when judged, never a pass.
  any_of            a list of expects of this same shape; at least one must hold.
  read_any          true: at least one data read (DATA_READS) was answered without error.
  quotes_spotter    n: the reply holds at least n distinct figures that appear in this run's data read answers
                    (contract function-list.md, G1). A data read is one of DATA_READS, never whoami, list_exercises,
                    find_food or get_routines; only an answer without error counts, texts inside it included but its
                    fixed help texts (FIXED_TEXT_KEYS: how, meaning, a field's words), which are the same on every
                    account. Each figure is matched whole, with the boundary reply_has_pattern uses. Not counted:
                    dates, ids, links, a span such as "28 days" or "4 weeks", a figure the case's say holds, and a
                    whole number under 10, with a unit or without (a decimal under 10, such as 3.3, counts). A sign
                    is ignored: "down 0.24 kg" quotes -0.24. 1,980 and 1980 are one figure. A negative case states 0,
                    with a reply_lacks_pattern for a made-up figure of its kind.
  quotes_one_of     figures, such as ["116.7", "100"]: the reply quotes at least one of them, and a data read of this
                    run gave it, by quotes_spotter's rules. The figure a direct case asks about, tied to the run.

  The four that read a call's own answer (contract exercise-list.md, C4g). Each is {function: what}, and needs no
  function key. A refusal's codes are its own code and every code in its errors list, so a code that is not the
  first of a combined refusal still counts (a pick_lift behind a date error).
  error_code        {function: code}: at least one call of it was refused with this code, such as pick_lift.
  no_error_code     {function: code}: no call of it was refused with this code.
  refusals          {function: code}: every call of it was refused with this code. No call at all passes: for
                    "any log_lifts call is refused as unknown_exercise", with log_lifts in the case's may.
  answer_has        {function: key} or {function: {...}}: at least one call of it was answered without error, and
                    its answer holds this key with a value that is not null, such as hand_check; or, for an object,
                    the answer matches it as inputs_include matches arguments.
  first_reply_lacks_pattern
                    a case with then only: the first turn's reply must match none of these regular expressions, as
                    reply_lacks_pattern reads the final reply (a claim of done before the person answered).

calls, the case's tags, on every case (contract function-list.md, "The golden set"):

  {"must": [...], "may": [...], "must_not": [...]}
  must      any of these, at least one called and answered (a refusal is an answer; a call cut off before its
            answer is not). Judged as function and any_of judge it. An empty must asks for no call.
  may       called or not, either is right. Judges nothing: it records intent.
  must_not  none of these called, refused or not. Judged as no_call judges it.
  When a case has both, its expect's top-level function is in must and its no_call is in must_not, so a reason
  the two give alike is said once.

kind, on a golden case only: direct, incidental or look_alike. Incidental and look_alike runs are kept out of the
pass count, the threshold and the exit code, and reported apart: the pick-up rate (incidental runs that made a must
call) and the over-call rate (look_alike runs that made a data read).

core, true or false: whether the case is in the core, the set each gate runs (python -m evals.run --core). Every
golden package case carries it, and G1's golden cases and the cases G1 added for a function carry it true; a case
without it is not in the core.

pair, on a look_alike case only: the id of the incidental case it is paired with, a case of the file.

seed, rows created for the person before the run, through Spotter's own services, in order:

  {"as": "roti", "service": "save_food" | "log_bodyweight" | "log_measurement" | "log_lifts" | "log_cardio" |
   "set_goal" | "import_entries" | "save_routine" | "update_settings" | "log_food" | "save_program", "data": {...}}
                                    import_entries returns no one id: no "as".
                                    update_settings (contract units.md R20) sets the person's own settings, such
                                    as weight_unit lb; it returns no id either: no "as".
                                    log_food (contract macros-and-asking.md) logs a day's food, macros and all, as
                                    the web logs it; it returns ids, not one id: no "as". An estimate seeded needs
                                    its portion, as any estimate does.
                                    save_program (contract routines-and-programs.md, R15) saves the person's
                                    program; each day names a routine seeded before it, and its weekdays are
                                    {{weekday:N}} placeholders, so a case never depends on the real weekday.
  {"service": "four_weeks"}         a seed that takes no data: four weeks of everything, three goals
                                    (evals/four_weeks.py). No "as": it returns no one id.
  {"service": "sixteen_weeks"}      the same kind: a year of weigh-ins, sixteen weeks of lifts, food, cardio
                                    and a waist, two goals (evals/sixteen_weeks.py, contract
                                    data-on-request.md). No "as" either.
  {"service": "edit_entry", "data": {"kind": "bodyweight", "entry_id": "{{seed.<as>}}", "changes": {...}}}
                                    an edit of a row seeded before it, so the person has an edit to undo.
                                    Its data is kind, entry_id and changes, all three.

db, checks on the person's rows after the run:

  {"table": "food_entry", "state": "live", "where": {...}, "count": 1, "all": {...}, "rows": [{...}, ...]}
  state: live (the default: rows not deleted) or deleted (soft-deleted rows only);
  where: only the rows that match it are checked, such as one date or one lift;
  count: exactly this many such rows, or a list of the counts allowed, such as [0, 3]; all: every one matches; rows:
  each matcher matches some row.
  A lift_session row carries set_count: how many sets it holds (evals/run.py's person_rows counts them). A
  routine row carries lift_count: how many lifts its lifts JSON holds. A saved_food row carries item_count: how many
  foods its items JSON holds, 0 for a plain food (contract macros-and-asking.md: a saved meal is a saved food with
  items). A program row carries week_count and day_count: the weeks and the training days of its blocks JSON, each
  added over every block, beside its start_on (contract routines-and-programs.md, R15). A food_entry row holds protein_g, carbs_g, fat_g, macros_estimated and portion, each null when not logged. A lift_set row is one set of the person's
  (contract exercise-list.md, C4g): exercise (the lift's name), weight_kg, reps, time_s, distance_m, set_number,
  session_id, and its session's date and deleted_at, so a set of a deleted session reads as deleted.

A log call, routed (contract log-and-guide.md, L5): every check that reads a call by name reads routed(calls), the
step at the top of judge_calls, judge_tags, judge_turns and made_must_call. A log call whose kind resolves answers to
both names: as log, with its arguments as sent; as its kind's function (log_lifts for kind lifts, and so on), with
the arguments app/log_shape.py's route gives that function, so a key path reads as the five's own (details.new_session
as new_session, details.entries as entries, a cardio part's fields at the top, a time or a distance in the kind's own
column). One whose kind resolves but whose shape route refuses counts under the kind's name as refused, so must_not,
no_call, refusals and error_code still catch it. One whose kind resolves to none counts as log alone. A reason names a
routed call as log (lifts). edit_entry, delete_entry and restore_entry take log's kind words too (decision 52): their
kind is read as its table word (log_shape.table_word), so lifts and lift_session both pass. The recorder keeps every
call as sent: routing is the judge's alone.

Placeholders, as a whole string: "{{today}}" and "{{yesterday}}" (the person's own dates, YYYY-MM-DD),
"{{days_ago:N}}" (N from 0 to DAYS_AGO, 60: the day N days before today), "{{next_month_end:N}}" (N from 1 to
12: the last day of month N, the next one strictly after today in the person's zone), and "{{weekday:N}}" (N from 0
to WEEKDAY_N, 6: the weekday N days after today, in lower case, such as tuesday: contract routines-and-programs.md,
R15), anywhere in seed, expect and db; "{{seed.<as>}}" (the id of the seeded row) in
expect and db, and in the data of a seed that comes after the one it names. A seed that names an "as" no
earlier seed gave is a problem when the cases are loaded, and so is any other name, or an N out of its range.
resolve() replaces them before seeding and before judging.
"""

import json
import re
import statistics
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CASES_FILE = Path(__file__).resolve().parent / "cases.json"
HIDDEN = "[token hidden]"

EXPECT_KEYS = {"function", "inputs_include", "inputs_exclude", "inputs_forbid", "ok_calls", "no_call",
               "refused", "reply_asks", "reply_has", "reply_has_pattern", "reply_lacks",
               "reply_lacks_pattern", "any_of", "read_any", "quotes_spotter", "quotes_one_of",
               "error_code", "no_error_code", "refusals", "answer_has", "first_reply_lacks_pattern"}
CODE_KEYS = ("error_code", "no_error_code", "refusals")  # {function: code}, read from the call's answer
CASE_KEYS = {"id", "say", "expect", "then", "switch", "seed", "db", "calls", "kind", "core", "pair"}
TAG_KEYS = ("must", "may", "must_not")
KINDS = ("direct", "incidental", "look_alike")  # a golden case's kind
APART = ("incidental", "look_alike")  # kept out of the pass count, reported apart
# The data reads, the only answers quotes_spotter and read_any read (contract function-list.md, G1). whoami,
# list_exercises, find_food and get_routines are never among them, and neither is guide (log-and-guide.md): its
# numbers ("1 to 60") are words on how to log, never Spotter's figures for the person.
DATA_READS = ("what_is_logged", "get_data", "get_log", "get_progress", "get_trends", "get_goals")
# Spotter's writes, as app/services/common.py's WRITE_FUNCTIONS names them (a test holds the two equal). A case
# whose must holds one is a logging case: the release bar of the contract's exit check 6.
WRITE_FUNCTIONS = ("log", "log_lifts", "log_bodyweight", "log_measurement", "log_food", "save_food", "save_routine",
                   "save_program", "log_cardio", "set_goal", "update_settings", "edit_entry", "delete_entry",
                   "restore_entry", "import_entries", "send_feedback")
DAYS_AGO = 60  # {{days_ago:N}} for N from 0 to this (evals/run.py's dates_for gives them)
WEEKDAY_N = 6  # {{weekday:N}} for N from 0 to this: the weekday N days after today (routines-and-programs.md, R15)
DB_KEYS = {"table", "state", "where", "count", "all", "rows"}
DB_STATES = ("live", "deleted")
SWITCHES = ("log", "log_delete")
# update_settings: units.md R20. log_food: macros-and-asking.md, a day's food with its macros, seeded as the web logs it.
# save_program: routines-and-programs.md R15, the person's program, its days naming routines seeded before it.
SEED_SERVICES = {"save_food", "log_bodyweight", "log_measurement", "log_lifts", "log_cardio", "set_goal",
                 "import_entries", "edit_entry", "save_routine", "update_settings", "log_food", "save_program"}
EDIT_SEED_KEYS = {"kind", "entry_id", "changes"}  # an edit_entry seed's data: see seed_service
NO_DATA_SERVICES = {"four_weeks", "sixteen_weeks"}  # called as seed_service(name)(user_id): see seed_service
DB_TABLES = {"food_entry", "saved_food", "bodyweight", "measurement", "cardio_entry", "lift_session", "lift_set",
             "goal", "feedback", "routine", "program"}
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
    if "then" in case and (not isinstance(case["then"], str) or not case["then"].strip() or "\n" in case["then"]):
        problems.append("then must be a non-empty line: the person's second message")
    if "expect" in case:
        problems += _expect_problems(case["expect"], "expect")
    if "switch" in case and case["switch"] not in SWITCHES:
        problems.append(f"switch must be one of {', '.join(SWITCHES)}")
    if "kind" in case and case["kind"] not in KINDS:
        problems.append(f"kind must be one of {', '.join(KINDS)}")
    if "core" in case and not isinstance(case["core"], bool):
        problems.append("core must be true or false")
    if "pair" in case:
        if not isinstance(case["pair"], str) or not case["pair"].strip():
            problems.append("pair must be the id of a case")
        elif case.get("kind") != "look_alike":
            problems.append("only a look_alike case names a pair: the incidental case it is paired with")
    if "calls" in case:
        problems += _tag_problems(case["calls"], case.get("expect"), case.get("kind"))
    named: set = set()  # the "as" of the seeds so far: what a later seed's {{seed.<as>}} may name
    for i, seed in enumerate(case.get("seed") or []):
        if isinstance(seed, dict) and seed.get("service") in NO_DATA_SERVICES:
            if set(seed) - {"service"}:
                problems.append(f"seed[{i}]: {seed['service']} takes no data and no as")
        elif not isinstance(seed, dict) or seed.get("service") not in SEED_SERVICES or not isinstance(
                seed.get("data"), dict):
            problems.append(f"seed[{i}] needs a service ({', '.join(sorted(SEED_SERVICES))}) and a data object, "
                            f"or a service that takes none ({', '.join(sorted(NO_DATA_SERVICES))})")
        else:
            if seed["service"] == "edit_entry" and set(seed["data"]) != EDIT_SEED_KEYS:
                problems.append(f"seed[{i}]: edit_entry's data is kind, entry_id and changes, all three")
            problems += [f"seed[{i}] names {{{{seed.{name}}}}}, which no earlier seed gave"
                         for name in sorted(_seed_names(seed["data"]) - named)]
            if isinstance(seed.get("as"), str):
                named.add(seed["as"])
    problems += [f"{{{{{name}}}}} is not a placeholder: {why}" for name, why in _placeholder_problems(
        [case.get("seed"), case.get("expect"), case.get("db")])]
    for i, check in enumerate(case.get("db") or []):
        if not isinstance(check, dict) or check.get("table") not in DB_TABLES:
            problems.append(f"db[{i}] needs a table ({', '.join(sorted(DB_TABLES))})")
        elif set(check) - DB_KEYS:
            problems.append(f"db[{i}] has unknown keys {sorted(set(check) - DB_KEYS)}")
        elif check.get("state", "live") not in DB_STATES:
            problems.append(f"db[{i}] state must be live or deleted")
        elif "where" in check and (not isinstance(check["where"], dict) or not check["where"]):
            problems.append(f"db[{i}] where must be a non-empty object")
        elif "count" in check and not _count_ok(check["count"]):
            problems.append(f"db[{i}] count must be a whole number, or a non-empty list of whole numbers")
    if isinstance(case.get("expect"), dict) and not case.get("then") and _has_key(case["expect"],
                                                                                  "first_reply_lacks_pattern"):
        problems.append("first_reply_lacks_pattern needs then: a case of one turn has no first reply")
    return problems


def _count_ok(count: Any) -> bool:
    """A db check's count: a whole number 0 or more, or a non-empty list of them (never a bool)."""
    whole = lambda n: isinstance(n, int) and not isinstance(n, bool) and n >= 0  # noqa: E731
    return whole(count) or (isinstance(count, list) and bool(count) and all(whole(n) for n in count))


def _has_key(expect: dict, key: str) -> bool:
    """The key in an expect or in any of its any_of options."""
    return key in expect or any(isinstance(sub, dict) and _has_key(sub, key) for sub in expect.get("any_of") or [])


def _placeholders(value: Any) -> set[str]:
    """The name of every whole-string placeholder in value."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return {name for item in value for name in _placeholders(item)}
    match = _PLACEHOLDER.match(value) if isinstance(value, str) else None
    return {match.group(1)} if match else set()


def _placeholder_problems(value: Any) -> list[tuple[str, str]]:
    """(name, why) for each placeholder no run can fill: an unknown name, or an N out of its range. {{seed.<as>}}
    is checked against the seeds apart."""
    out = []
    for name in sorted(_placeholders(value)):
        if name in ("today", "yesterday") or name.startswith("seed."):
            continue
        base, _, n = name.partition(":")
        if base == "days_ago" and n and 0 <= int(n) <= DAYS_AGO:
            continue
        if base == "next_month_end" and n and 1 <= int(n) <= 12:
            continue
        if base == "weekday" and n and 0 <= int(n) <= WEEKDAY_N:
            continue
        out.append((name, {"days_ago": f"N runs from 0 to {DAYS_AGO}", "next_month_end": "N runs from 1 to 12",
                           "weekday": f"N runs from 0 to {WEEKDAY_N}"}.get(base, "no such name")))
    return out


def _seed_names(value: Any) -> set[str]:
    """The <as> of every {{seed.<as>}} placeholder in value."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return {name for item in value for name in _seed_names(item)}
    match = _PLACEHOLDER.match(value) if isinstance(value, str) else None
    return {match.group(1)[5:]} if match and match.group(1).startswith("seed.") else set()


def _expect_problems(expect: Any, where: str) -> list[str]:
    if not isinstance(expect, dict) or not expect:
        return [f"{where} must be a non-empty object"]
    problems = [f"{where} has unknown key {k}" for k in sorted(set(expect) - EXPECT_KEYS)]
    needs_function = {"inputs_include", "inputs_exclude", "inputs_forbid", "refused"} & set(expect)
    if needs_function and "function" not in expect:
        problems.append(f"{where}: {', '.join(sorted(needs_function))} needs function")
    for key in ("reply_has_pattern", "reply_lacks_pattern", "first_reply_lacks_pattern"):
        if key in expect:
            problems += _pattern_problems(expect[key], f"{where}.{key}")
    if "read_any" in expect and expect["read_any"] is not True:
        problems.append(f"{where}.read_any must be true")
    if "quotes_spotter" in expect and (isinstance(expect["quotes_spotter"], bool)
                                       or not isinstance(expect["quotes_spotter"], int)
                                       or expect["quotes_spotter"] < 0):
        problems.append(f"{where}.quotes_spotter must be a whole number, 0 or more")
    if "quotes_one_of" in expect:
        problems += _one_of_problems(expect["quotes_one_of"], f"{where}.quotes_one_of")
    for key in CODE_KEYS + ("answer_has",):
        if key in expect:
            value = expect[key]
            if not isinstance(value, dict) or not value or not all(isinstance(f, str) and f for f in value):
                problems.append(f"{where}.{key} must be an object of function names")
            elif key in CODE_KEYS and not all(isinstance(v, str) and v for v in value.values()):
                problems.append(f"{where}.{key} gives each function an error code")
            elif key == "answer_has" and not all((isinstance(v, str) and v) or (isinstance(v, dict) and v)
                                                 for v in value.values()):
                problems.append(f"{where}.answer_has gives each function a key or an object")
    for i, sub in enumerate(expect.get("any_of") or []):
        problems += _expect_problems(sub, f"{where}.any_of[{i}]")
    return problems


def _one_of_problems(figures: Any, where: str) -> list[str]:
    """A non-empty list of figures as text, each one quotes_spotter can count: never a whole number under 10."""
    if not isinstance(figures, list) or not figures:
        return [f"{where} must be a non-empty list of figures"]
    problems = []
    for i, figure in enumerate(figures):
        value = _decimal(figure.replace(",", "")) if isinstance(figure, str) else None
        if value is None:
            problems.append(f"{where}[{i}] must be a figure written as text, such as \"116.7\"")
        elif _small_whole(value):
            problems.append(f"{where}[{i}] {figure} is a whole number under 10, which never counts")
    return problems


def _tag_problems(tags: Any, expect: Any, kind: Any) -> list[str]:
    """What is wrong with a case's calls tags, and where they disagree with its expect."""
    if not isinstance(tags, dict) or set(tags) != set(TAG_KEYS):
        return [f"calls must be an object of exactly {', '.join(TAG_KEYS)}"]
    problems = []
    for key in TAG_KEYS:
        names = tags[key]
        if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
            problems.append(f"calls.{key} must be a list of function names")
        elif len(set(names)) != len(names):
            problems.append(f"calls.{key} names a function twice")
    if problems:
        return problems
    for a, b in (("must", "may"), ("must", "must_not"), ("may", "must_not")):
        both = sorted(set(tags[a]) & set(tags[b]))
        if both:
            problems.append(f"calls.{a} and calls.{b} both name {', '.join(both)}")
    if isinstance(expect, dict):
        function = expect.get("function")
        if isinstance(function, str) and function not in tags["must"]:
            problems.append(f"calls.must leaves out {function}, the expect's function")
        missing = sorted(set(expect.get("no_call") or []) - set(tags["must_not"]))
        if missing:
            problems.append(f"calls.must_not leaves out {', '.join(missing)}, which the expect's no_call names")
    if kind == "incidental" and not tags["must"]:
        problems.append("an incidental case names the data read its step needs in calls.must")
    if kind == "look_alike" and not set(DATA_READS) <= set(tags["must_not"]):
        problems.append("a look_alike case names every data read in calls.must_not")
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
    kinds = {c.get("id"): c.get("kind") for c in cases if isinstance(c, dict)}
    for case in cases:
        if isinstance(case, dict) and isinstance(case.get("pair"), str) and case["pair"].strip() \
                and kinds.get(case["pair"]) != "incidental":
            problems.append(f"{case.get('id')}: pair {case['pair']} is not an incidental case in the file")
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


def seed_service(name: str):
    """The function a seed's service names. A name in NO_DATA_SERVICES is the function of that name in the evals
    module of that name (evals/four_weeks.py's four_weeks, evals/sixteen_weeks.py's sixteen_weeks), called as
    service(user_id); every other is Spotter's own service, called as service(user_id, data, "web"), but
    edit_entry, which is called as service(user_id, kind, entry_id, changes, "web") from its data
    (EDIT_SEED_KEYS). Imported only when asked for, so the rest of this module still opens no database."""
    if name in NO_DATA_SERVICES:
        import importlib
        return getattr(importlib.import_module(f"evals.{name}"), name)
    if name not in SEED_SERVICES:
        raise KeyError(f"no seed service {name}")
    from app import services
    return getattr(services, name)


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


# ---------------------------------------------------------------- log calls, routed (log-and-guide.md, L5)

KIND_WORD_FUNCTIONS = ("edit_entry", "delete_entry", "restore_entry")  # they take log's kind words (decision 52)


def routed(calls: list[dict]) -> list[dict]:
    """The calls as the checks read them (the module notes). A log call whose kind resolves becomes one record under
    its kind's function, with route's arguments, the arguments as sent kept as sent and its kind as log_kind; refused
    when route refuses it, whatever the answer was. Every other call is as recorded, but the kind of edit_entry,
    delete_entry and restore_entry read as its table word. New dicts throughout: the recorder's own are never
    changed, and calls already routed go on as they are, so the step may run twice. app.log_shape is imported here,
    as every other app import of this module is lazy."""
    from app import log_shape
    out = []
    for c in calls:
        if c.get("routed"):
            out.append(c)
            continue
        arguments = c.get("arguments") if isinstance(c.get("arguments"), dict) else {}
        if c.get("name") == "log":
            found = log_shape.route(arguments)
            if found.kind is None:  # no kind resolved: log alone
                out.append({**c, "routed": True})
                continue
            refused = found.refusal is not None
            out.append({**c, "name": found.function, "via": "log", "log_kind": found.kind, "sent": arguments,
                        "arguments": _refused_arguments(log_shape, arguments, found.kind) if refused else found.arguments,
                        "is_error": bool(c.get("is_error")) or refused, "routed": True})
        elif c.get("name") in KIND_WORD_FUNCTIONS and isinstance(arguments.get("kind"), str):
            out.append({**c, "arguments": {**arguments, "kind": log_shape.table_word(arguments["kind"])}, "routed": True})
        else:
            out.append({**c, "routed": True})
    return out


def _refused_arguments(log_shape, arguments: dict, kind: str) -> dict:
    """A log call route refused, as its kind's function would have been sent it: route again on the call with every
    key route does not take there left out; when that is refused too (a part count, a conversion), the shape moved by
    hand, every key kept and nothing converted. So inputs_* read a refused log call as they read a refused call of the
    five."""
    parts = arguments.get("parts") if isinstance(arguments.get("parts"), list) else []
    details = arguments.get("details") if isinstance(arguments.get("details"), dict) else {}
    takes = log_shape.PART_TAKES.get(kind, ())
    clean = {k: v for k, v in arguments.items() if k in log_shape.LOG_FIELDS and k not in ("parts", "details")}
    if kind not in log_shape.NOTES_KINDS:
        clean.pop("notes", None)
    if takes:
        clean["parts"] = [{k: v for k, v in p.items() if k in takes} for p in parts if isinstance(p, dict)]
    clean["details"] = {k: v for k, v in details.items() if k in log_shape.DETAIL_FIELDS[kind]}
    again = log_shape.route(clean)
    if again.refusal is None:
        return again.arguments
    top = {k: v for k, v in arguments.items() if k not in ("kind", "parts", "details")}
    names = log_shape.PART_MAP.get(kind, {})
    rows = [{names.get(k, k): v for k, v in p.items()} for p in parts if isinstance(p, dict)]
    if kind == "lifts":
        return {"sets": rows, **details, **top}
    if kind == "cardio":
        return {**(rows[0] if rows else {}), **details, **top}
    return {**details, **top}


def _is(call: dict, name: str) -> bool:
    """A routed call answers to its own name, and a routed log call to log too."""
    return call["name"] == name or (name == "log" and call.get("via") == "log")


def _arguments(call: dict, name: str) -> dict:
    """A routed call's arguments as a check by this name reads them: a log call's as sent when the check names log."""
    return call["sent"] if name == "log" and call.get("via") == "log" else call["arguments"]


def label(call: dict) -> str:
    """A call as a reason names it: a routed log call as log (lifts)."""
    return f"log ({call['log_kind']})" if call.get("via") == "log" else str(call["name"])


def _named(name: str, mine: list[dict]) -> str:
    """The name a reason gives the calls a check found by name: the check's own, unless every one was a log call
    routed to it, then those calls as log (kind)."""
    if mine and name != "log" and all(c.get("via") == "log" for c in mine):
        return ", ".join(dict.fromkeys(label(c) for c in mine))
    return name


def _called(calls: list[dict]) -> str:
    return ", ".join(label(c) for c in calls) or "none"


# ---------------------------------------------------------------- judging

def error_codes(call: dict) -> set[str]:
    """Every error code a refused call's answer carries: its own code and each code in its errors list (a combined
    refusal, rules.combine). Read from the answer the recorder keeps as result, and from its error. A call answered
    without error has none."""
    if not call.get("is_error"):
        return set()
    found = set()
    for source in (call.get("result"), {"error": call.get("error")}):
        if isinstance(source, dict):
            if isinstance(source.get("error"), str):
                found.add(source["error"])
            found |= {e["error"] for e in source.get("errors") or [] if isinstance(e, dict)
                      and isinstance(e.get("error"), str)}
    return found


def _holds(answer: Any, want: Any) -> bool:
    """answer_has: a key present with a value that is not null, or an object matched as a nested subset."""
    if isinstance(want, str):
        return isinstance(answer, dict) and answer.get(want) is not None
    return matches(answer, want)


def _code_reasons(expect: dict, calls: list[dict]) -> list[str]:
    """Why the run fails error_code, no_error_code, refusals and answer_has, or []."""
    reasons = []
    for name, code in (expect.get("error_code") or {}).items():
        mine = [c for c in calls if _is(c, name)]
        if not any(code in error_codes(c) for c in mine):
            got = sorted({x for c in mine for x in error_codes(c)})
            reasons.append(f"no {name} call was refused with {code} (calls of it: {len(mine)}; codes: "
                           f"{', '.join(got) or 'none'})")
    for name, code in (expect.get("no_error_code") or {}).items():
        mine = [c for c in calls if _is(c, name) and code in error_codes(c)]
        if mine:
            reasons.append(f"a {_named(name, mine)} call was refused with {code}")
    for name, code in (expect.get("refusals") or {}).items():
        bad = [c for c in calls if _is(c, name) and code not in error_codes(c)]
        if bad:
            reasons.append(f"{_named(name, bad)} was called and not refused with {code} "
                           f"(codes: {'; '.join(', '.join(sorted(error_codes(c))) or 'none' for c in bad)})")
    for name, want in (expect.get("answer_has") or {}).items():
        if not any(_is(c, name) and not c["is_error"] and _holds(c.get("result"), want) for c in calls):
            reasons.append(f"no {name} answer without error holds {json.dumps(want)}")
    return reasons


def judge_calls(expect: dict, calls: list[dict], reply: str, say: str = "") -> list[str]:
    """Why this run fails the expect, or [] when it passes. calls: [{name, arguments, is_error, result}, with answer
    for a data read (evals/recorder.py)]. say: the case's own words, whose figures quotes_spotter leaves out."""
    reasons: list[str] = []
    calls = routed(calls)
    function = expect.get("function")
    mine = [c for c in calls if _is(c, function)] if function else []

    def sent(c: dict) -> dict:
        return _arguments(c, function)

    if function and not mine:
        reasons.append(f"{function} was not called (calls: {_called(calls)})")
    if function and mine:
        if "inputs_include" in expect and not any(matches(sent(c), expect["inputs_include"]) for c in mine):
            reasons.append(f"no {function} call sent {json.dumps(expect['inputs_include'])} "
                           f"(sent: {'; '.join(json.dumps(sent(c)) for c in mine)})")
        for path in expect.get("inputs_exclude") or []:
            bad = [c for c in mine if any(v is not None for v in values_at(sent(c), path))]
            if bad:
                reasons.append(f"{_named(function, bad)} sent {path}, which should be left out "
                               f"(sent: {'; '.join(json.dumps(sent(c)) for c in bad)})")
        for path, value in (expect.get("inputs_forbid") or {}).items():
            bad = [c for c in mine if any(matches(v, value) for v in values_at(sent(c), path))]
            if bad:
                reasons.append(f"{_named(function, bad)} sent {path} = {json.dumps(value)}")
        if expect.get("refused") and not all(c["is_error"] for c in mine):
            reasons.append(f"{_named(function, [c for c in mine if not c['is_error']])} was called and not refused")
    for name, n in (expect.get("ok_calls") or {}).items():
        ok = sum(1 for c in calls if _is(c, name) and not c["is_error"])
        if ok != n:
            refused = sum(1 for c in calls if _is(c, name) and c["is_error"])
            reasons.append(f"{name} succeeded {ok} times, expected {n}" + (f" ({refused} refused)" if refused else ""))
    for name in expect.get("no_call") or []:
        found = [c for c in calls if _is(c, name)]
        if found:
            reasons.append(f"{_named(name, found)} was called and should not have been")
    reasons += _code_reasons(expect, calls)
    text = reply or ""
    if expect.get("reply_asks") and "?" not in text:
        reasons.append("the reply asks nothing")
    for phrase in expect.get("reply_has") or []:
        if phrase.casefold() not in text.casefold():
            reasons.append(f"the reply does not say {phrase!r}")
    if "reply_has_pattern" in expect:
        problems = _pattern_problems(expect["reply_has_pattern"], "reply_has_pattern")
        if problems:
            raise ValueError("; ".join(problems))
        for pattern in expect["reply_has_pattern"]:
            if not re.search(pattern, text, re.IGNORECASE):
                reasons.append(f"the reply does not match {pattern!r}")
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
    if expect.get("read_any") and not any(c["name"] in DATA_READS and not c["is_error"] for c in calls):
        reasons.append(f"no data read was answered without error (calls: {_called(calls)})")
    if "quotes_one_of" in expect:
        problems = _one_of_problems(expect["quotes_one_of"], "quotes_one_of")
        if problems:
            raise ValueError("; ".join(problems))
        asked = {_decimal(f.replace(",", "")) for f in expect["quotes_one_of"]}
        quoted = _quoted(text, calls, say)
        if not asked & {value for value, _ in quoted}:
            reasons.append(f"the reply quotes none of {', '.join(expect['quotes_one_of'])} from this run's data reads"
                           + (f" (quoted: {', '.join(shown for _, shown in quoted)})" if quoted else ""))
    if "quotes_spotter" in expect:
        quoted = quoted_figures(text, calls, say)
        if len(quoted) < expect["quotes_spotter"]:
            reasons.append(f"the reply quotes {len(quoted)} of this run's figures from Spotter's data reads, "
                           f"expected at least {expect['quotes_spotter']}"
                           + (f" (quoted: {', '.join(quoted)})" if quoted else ""))
    if expect.get("any_of"):
        options = [judge_calls(sub, calls, reply, say) for sub in expect["any_of"]]
        if all(options):
            reasons.append("none of: " + " | ".join("; ".join(o) for o in options))
    return reasons


def judge_tags(tags: dict | None, calls: list[dict]) -> list[str]:
    """Why this run fails a case's calls tags, or []. must: at least one of them called and answered; must_not: none
    of them called. may judges nothing. One must name gives function's own words, so a case whose expect names the
    same function says it once (judge drops a reason said twice)."""
    if not tags:
        return []
    reasons = []
    calls = routed(calls)
    must = tags.get("must") or []
    if must:
        made = [c for c in calls if any(_is(c, name) for name in must)]
        if not any(c.get("answered", True) for c in made):
            if made:
                reasons.append(f"{', '.join(sorted({label(c) for c in made}))} was called and never answered")
            elif len(must) == 1:
                reasons.append(f"{must[0]} was not called (calls: {_called(calls)})")
            else:
                reasons.append(f"none of {', '.join(must)} was called (calls: {_called(calls)})")
    for name in tags.get("must_not") or []:
        found = [c for c in calls if _is(c, name)]
        if found:
            reasons.append(f"{_named(name, found)} was called and should not have been")
    return reasons


# ---------------------------------------------------------------- Spotter's figures in a reply (quotes_spotter)

_LINK = re.compile(r"\]\([^)\s]*\)|<?https?://[^\s)>\]]+>?|\bwww\.[^\s)\]]+", re.IGNORECASE)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?")
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?"
          r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_MONTH_FIRST = _MONTH.replace("|may|", "|")  # "may 12" is more often the verb than the date
_PROSE_DATE = re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b\.?(?:,?\s+\d{{4}})?"
                         rf"|\b{_MONTH_FIRST}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}})?", re.IGNORECASE)
_NUMERIC_DATE = re.compile(r"(?<![\d.])\d{1,2}[/.]\d{1,2}[/.]\d{2,4}(?![\d.])")
_SPAN = re.compile(r"(?<![\w.,])\d+[\s-]+(?:days?|weeks?|months?|years?)\b", re.IGNORECASE)
_DURATION = re.compile(r"(?<![\w.,:])(\d{1,3}):(\d{2})(?::(\d{2}))?(?![\d:])")
_FIGURE = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?!\d|[.,]\d)")
_ID_KEYS = {"id", "ids", "more", "cursor", "links", "link", "url", "confirm", "key", "request_id"}
# The keys whose texts are fixed help, the same on every account (what_is_logged's how and its areas' meaning and
# field words; every read's how): their numbers ("0.5 for a secondary", "reps over 30") are no figure of the person's.
# A number under them still counts: a field's filled count is the last item of its list.
FIXED_TEXT_KEYS = {"how", "meaning", "fields"}


def _id_like(key: Any) -> bool:
    key = str(key)
    return key in _ID_KEYS or key.endswith(("_id", "_ids", "_url"))


def _decimal(text: str) -> Decimal | None:
    try:
        return abs(Decimal(text)).normalize()
    except InvalidOperation:
        return None


def _text_figures(text: str) -> list[tuple[Any, str]]:
    """(value, as written) for each figure in a text, in order: links, dates and spans such as "28 days" taken out
    first. A duration such as 29:30 is one figure."""
    for pattern in (_LINK, _ISO_DATE, _PROSE_DATE, _NUMERIC_DATE, _SPAN):
        text = pattern.sub(" ", text)
    found: list[tuple[Any, str]] = []

    def duration(m: re.Match) -> str:
        found.append((f"{int(m[1])}:{m[2]}" + (f":{m[3]}" if m[3] else ""), m[0]))
        return " "

    text = _DURATION.sub(duration, text)
    for m in _FIGURE.finditer(text):
        value = _decimal(m[1].replace(",", "") + (f".{m[2]}" if m[2] else ""))
        if value is not None:
            found.append((value, m[0]))
    return found


def _answer_figures(value: Any) -> set:
    """Every figure in one answer: its numbers and the figures in its texts, never an id, a cursor or a link, nor a
    fixed help text's (FIXED_TEXT_KEYS). A table's id columns (get_data's columns and rows) are left out too."""
    found: set = set()

    def walk(item: Any, fixed: bool = False) -> None:
        if isinstance(item, bool) or item is None:
            return
        if isinstance(item, (int, float)):
            number = _decimal(repr(item))
            if number is not None:
                found.add(number)
        elif isinstance(item, str):
            if not fixed:
                found.update(v for v, _ in _text_figures(item))
        elif isinstance(item, list):
            for x in item:
                walk(x, fixed)
        elif isinstance(item, dict):
            columns = item.get("columns") if isinstance(item.get("columns"), list) else []
            skip = {i for i, c in enumerate(columns) if _id_like(c)}
            for key, x in item.items():
                if _id_like(key):
                    continue
                inner = fixed or key in FIXED_TEXT_KEYS
                if skip and key in ("rows", "values") and isinstance(x, list):
                    for row in (x if key == "rows" else [x]):
                        if isinstance(row, list):
                            for i, cell in enumerate(row):
                                if i not in skip:
                                    walk(cell, inner)
                        else:
                            walk(row, inner)
                else:
                    walk(x, inner)

    walk(value)
    return found


def spotter_figures(calls: list[dict]) -> set:
    """The figures of this run's data read answers: answered without error, with an answer kept."""
    found: set = set()
    for c in calls:
        if c["name"] in DATA_READS and not c["is_error"] and c.get("answer") is not None:
            found |= _answer_figures(c["answer"])
    return found


def _small_whole(value: Any) -> bool:
    return isinstance(value, Decimal) and value == value.to_integral_value() and value < 10


def _quoted(reply: str, calls: list[dict], say: str = "") -> list[tuple[Any, str]]:
    """(value, as written) for each distinct figure of the reply that this run's data reads gave: not one the say
    holds, and not a whole number under 10, with a unit or without."""
    spotter = spotter_figures(calls)
    said = {v for v, _ in _text_figures(say or "")}
    out, seen = [], set()
    for value, shown in _text_figures(reply or ""):
        if value in seen or value in said or _small_whole(value) or value not in spotter:
            continue
        seen.add(value)
        out.append((value, shown))
    return out


def quoted_figures(reply: str, calls: list[dict], say: str = "") -> list[str]:
    """The distinct figures of the reply, as written, that this run's data reads gave (_quoted)."""
    return [shown for _, shown in _quoted(reply, calls, say)]


def judge_db(checks: list[dict], rows: dict[str, list[dict]]) -> list[str]:
    """Why the person's rows fail the checks, or []. rows: {table: [every row, deleted ones too, as dicts]}."""
    reasons = []
    for check in checks or []:
        deleted = check.get("state", "live") == "deleted"
        table = check["table"] + (" (deleted)" if deleted else "")
        have = [r for r in rows.get(check["table"], []) if (r.get("deleted_at") is not None) == deleted
                and ("where" not in check or matches(r, check["where"]))]
        if "where" in check:
            table += f" where {json.dumps(check['where'])}"
        if "count" in check:
            allowed = check["count"] if isinstance(check["count"], list) else [check["count"]]
            if len(have) not in allowed:
                reasons.append(f"{table}: {len(have)} rows, expected {' or '.join(str(n) for n in allowed)}")
        if "all" in check:
            bad = [r for r in have if not matches(r, check["all"])]
            if bad:
                reasons.append(f"{table}: rows not matching {json.dumps(check['all'])}: {_rows_text(bad)}")
        for want in check.get("rows") or []:
            if not any(matches(r, want) for r in have):
                reasons.append(f"{table}: no row matching {json.dumps(want)} (rows: {_rows_text(have) or 'none'})")
    return reasons


def _rows_text(rows: list[dict]) -> str:
    # A food entry's macros and portion, and a saved food's serving numbers and items (macros-and-asking.md).
    keep = ("date", "kind", "value", "kg", "meal", "description", "kcal", "source", "estimated", "saved_food_id",
            "servings", "protein_g", "carbs_g", "fat_g", "macros_estimated", "portion", "name", "serving_label",
            "kcal_per_serving", "protein_g_per_serving", "carbs_g_per_serving", "fat_g_per_serving", "item_count",
            "items", "minutes", "exercise_id", "estimated_kcal", "id", "status", "target_value", "target_date",
            "set_count", "lift_count", "exercise", "weight_kg", "reps", "time_s", "distance_m", "session_id",
            "start_on", "week_count", "day_count")  # a program's (routines-and-programs.md, R15)
    return "; ".join(json.dumps({k: r[k] for k in keep if k in r}) for r in rows)


def judge(case: dict, calls: list[dict], reply: str, rows: dict[str, list[dict]],
          first_reply: str | None = None) -> list[str]:
    """The whole verdict for one run of an already-resolved case: [] is a pass. The expect, the calls tags and the
    rows; a reason the expect and the tags give alike is said once."""
    reasons = (judge_calls(case["expect"], calls, reply, case.get("say", ""))
               + judge_tags(case.get("calls"), calls) + judge_db(case.get("db") or [], rows)
               + judge_turns(case, calls, first_reply))
    return list(dict.fromkeys(reasons))


def judge_turns(case: dict, calls: list[dict], first_reply: str | None = None) -> list[str]:
    """A case with a second turn: no write may succeed before the second line was sent, or the assistant acted on a
    guess and the second line was never its answer; and each code of its expect's error_code is a refusal of the first
    turn and of no call in the second; the expect's inputs_include is sent by a call of its function that succeeded in
    the second turn, never only by a refused one; and the first turn's reply (first_reply, evals/run.py keeps each
    turn's) matches none of first_reply_lacks_pattern. A call's turn is its turn key (run_once sets 1 or 2); a call
    without one counts as the first turn's."""
    if not case.get("then"):
        return []
    calls = routed(calls)
    early = [label(c) for c in calls if c.get("turn", 1) == 1 and c["name"] in WRITE_FUNCTIONS and not c["is_error"]]
    reasons = [f"a write succeeded before the second turn was sent: {', '.join(early)}"] if early else []
    # The question a two-turn case is built on (its expect's error_code, such as pick_lift) is Spotter's in the first
    # turn, and the second turn answers it: never asked first by the assistant alone, never asked again.
    for name, code in (case.get("expect", {}).get("error_code") or {}).items():
        mine = [c for c in calls if _is(c, name) and code in error_codes(c)]
        if not any(c.get("turn", 1) == 1 for c in mine):
            reasons.append(f"no {name} call in the first turn was refused with {code}")
        again = [c for c in mine if c.get("turn", 1) == 2]
        if again:
            reasons.append(f"a {_named(name, again)} call in the second turn was refused with {code} again")
    expect = case.get("expect", {})
    function = expect.get("function")
    if function and "inputs_include" in expect and not any(
            _is(c, function) and c.get("turn", 1) == 2 and not c["is_error"]
            and matches(_arguments(c, function), expect["inputs_include"]) for c in calls):
        reasons.append(f"no {function} call that succeeded in the second turn sent "
                       f"{json.dumps(expect['inputs_include'])}")
    if "first_reply_lacks_pattern" in expect:
        if first_reply is None:
            reasons.append("the first turn's reply was not kept, so it could not be judged")
        else:
            for pattern in expect["first_reply_lacks_pattern"]:
                found = re.search(pattern, first_reply, re.IGNORECASE)
                if found:
                    reasons.append(f"the first turn's reply matches {pattern!r}: it says {found.group(0)!r}")
    return reasons


# ---------------------------------------------------------------- the CLI's output

class CliOutputError(ValueError):
    """The CLI's output was not the JSON result document."""


def parse_cli_output(stdout: str) -> dict:
    """The parts of `claude -p --output-format json` the report uses.

    The document is one JSON object of type "result" (or, with --verbose, a list of messages whose last
    "result" message is the one). Returns {reply, subtype, is_error, num_turns, cost_usd, usage, tokens,
    input_tokens, cached_tokens, output_tokens, models, permission_denials}. tokens is input + cache creation +
    cache read + output; split as token_split says.
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
        **token_split("claude", usage),
        "models": models,
        "permission_denials": document.get("permission_denials") or [],
    }


def token_split(client: str, usage: dict) -> dict:
    """A run's tokens as input (not read from a cache), cached (read from a cache) and output, one rule a client,
    so the three add up to the run's tokens.

    claude  input = input_tokens + cache_creation_input_tokens (a cache write is input not yet cached);
            cached = cache_read_input_tokens; output = output_tokens.
    codex   input = input_tokens - cached_input_tokens (OpenAI counts the cached part inside input_tokens);
            cached = cached_input_tokens; output = output_tokens. reasoning_output_tokens is taken as part of
            output_tokens, OpenAI's convention, so never added again (not checked against a Codex run).
    gemini  input = promptTokenCount - cachedContentTokenCount (the cached part is inside the prompt count);
            cached = cachedContentTokenCount; output = candidatesTokenCount + thoughtsTokenCount (Gemini counts
            thoughts apart from the answer)."""
    def get(key: str) -> int:
        value = (usage or {}).get(key)
        return int(value) if _number(value) else 0

    if client == "claude":
        split = (get("input_tokens") + get("cache_creation_input_tokens"), get("cache_read_input_tokens"),
                 get("output_tokens"))
    elif client == "codex":
        split = (max(get("input_tokens") - get("cached_input_tokens"), 0), get("cached_input_tokens"),
                 get("output_tokens"))
    elif client == "gemini":
        split = (max(get("promptTokenCount") - get("cachedContentTokenCount"), 0), get("cachedContentTokenCount"),
                 get("candidatesTokenCount") + get("thoughtsTokenCount"))
    else:
        raise ValueError(f"no token rule for the client {client!r}")
    return dict(zip(("input_tokens", "cached_tokens", "output_tokens"), split))


# ---------------------------------------------------------------- the table

def made_must_call(run: dict, tags: dict | None) -> bool:
    """The run called one of the case's must functions and got an answer."""
    must = (tags or {}).get("must") or []
    return any(any(_is(c, name) for name in must) and c.get("answered", True) for c in routed(run.get("calls") or []))


def made_data_read(run: dict) -> bool:
    """The run called a data read, answered or not."""
    return any(c["name"] in DATA_READS for c in run.get("calls") or [])


def golden_rates(results: list[dict]) -> dict:
    """The pick-up and over-call rates of one run of the evals, from its results (a kept results.json holds them):
    pick_up (incidental runs that made a must call, incidental runs) and over_call (look_alike runs that made a data
    read, look_alike runs)."""
    incidental = [(r, res.get("tags")) for res in results if res.get("kind") == "incidental" for r in res["runs"]]
    look_alike = [r for res in results if res.get("kind") == "look_alike" for r in res["runs"]]
    return {"pick_up": (sum(1 for r, tags in incidental if made_must_call(r, tags)), len(incidental)),
            "over_call": (sum(1 for r in look_alike if made_data_read(r)), len(look_alike))}


def logging_case(result: dict) -> bool:
    """A case whose must holds a write: the release bar's cases (contract function-list.md, exit check 6)."""
    return bool(set((result.get("tags") or {}).get("must") or []) & set(WRITE_FUNCTIONS))


def _mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: list) -> float:
    return statistics.median(values) if values else 0.0


def _run_tokens(run: dict) -> tuple[int, int, int]:
    return (int(run.get("input_tokens") or 0), int(run.get("cached_tokens") or 0), int(run.get("output_tokens") or 0))


def _cost(runs: list[dict]) -> float | None:
    """The runs' cost, or None when no run's client reported one (Codex and Gemini give none)."""
    costs = [r["cost_usd"] for r in runs if _number(r.get("cost_usd"))]
    return sum(costs) if costs else None


def _money(cost: float | None) -> str:
    return "none given" if cost is None else f"${cost:.4f}"


def _rate(made: int, of: int) -> str:
    return f"{made} of {of}" + (f" ({made / of * 100:.0f}%)" if of else "")


def summarise(results: list[dict], n: int, threshold: float) -> tuple[str, bool]:
    """The printed table and whether every counted case met the threshold.

    results: one per case: {id, kind?, tags?, runs: [{passed, tokens, input_tokens?, cached_tokens?,
    output_tokens?, cost_usd, calls?, num_turns?, seconds?}]}. Each case's row gives its passes and, a run on
    average, its calls, turns, seconds and tokens (input, cached, output), and its runs' cost. Incidental and
    look_alike cases are marked apart: kept out of the counted passes and the threshold, with the pick-up and
    over-call rates under the table. Then the medians and totals over every run, and the logging cases' bar.
    """
    head = (f"{'case':<26} {'passes':>7} {'calls':>6} {'turns':>6} {'seconds':>8} {'input':>9} {'cached':>9} "
            f"{'output':>7} {'cost':>10}")
    lines = [head, "-" * len(head)]
    counted_pass = counted_runs = apart_pass = apart_runs = 0
    every: list[dict] = []
    ok = True
    for r in results:
        runs = r["runs"]
        every += runs
        passes = sum(1 for x in runs if x["passed"])
        apart = r.get("kind") in APART
        if apart:
            apart_pass += passes
            apart_runs += len(runs)
        else:
            counted_pass += passes
            counted_runs += len(runs)
        rate = passes / len(runs) if runs else 0.0
        below = not apart and rate < threshold
        ok = ok and not below
        tokens = [_run_tokens(x) for x in runs]
        turns = [x["num_turns"] for x in runs if _number(x.get("num_turns"))]
        lines.append(
            f"{r['id']:<26} {f'{passes}/{len(runs)}':>7} {_mean([len(x.get('calls') or []) for x in runs]):>6.1f} "
            f"{(f'{_mean(turns):.1f}' if turns else '-'):>6} {_mean([x.get('seconds') or 0 for x in runs]):>8.1f} "
            f"{_mean([t[0] for t in tokens]):>9,.0f} {_mean([t[1] for t in tokens]):>9,.0f} "
            f"{_mean([t[2] for t in tokens]):>7,.0f} {_money(_cost(runs)):>10}"
            + ("  below threshold" if below else "") + (f"  apart: {r['kind']}" if apart else ""))
    lines.append("-" * len(head))
    overall = (counted_pass / counted_runs * 100) if counted_runs else 0.0
    lines.append(f"counted: {counted_pass} of {counted_runs} runs passed, pass rate {overall:.0f}%, threshold "
                 f"{threshold:g} per case, n = {n}")
    if apart_runs:
        rates = golden_rates(results)
        lines.append(f"apart, incidental and look-alike: {apart_pass} of {apart_runs} runs passed, not counted")
        lines.append(f"pick-up rate: {_rate(*rates['pick_up'])} incidental runs made a must call; over-call rate: "
                     f"{_rate(*rates['over_call'])} look-alike runs made a data read")
    tokens = [_run_tokens(x) for x in every]
    lines.append(f"a run, median: {_median([len(x.get('calls') or []) for x in every]):g} calls, "
                 f"{_median([x['num_turns'] for x in every if _number(x.get('num_turns'))]):g} turns, "
                 f"{_median([x.get('seconds') or 0 for x in every]):g} seconds, "
                 f"{_median([sum(t) for t in tokens]):,.0f} tokens")
    lines.append(f"in total: {sum(len(x.get('calls') or []) for x in every)} calls, "
                 f"{sum(x.get('seconds') or 0 for x in every):,.1f} seconds, tokens {sum(t[0] for t in tokens):,} "
                 f"input, {sum(t[1] for t in tokens):,} cached, {sum(t[2] for t in tokens):,} output, cost "
                 f"{_money(_cost(every))}")
    logging = [r for r in results if logging_case(r) and r.get("kind") not in APART]
    if logging:
        runs = [x for r in logging for x in r["runs"]]
        low = [r["id"] for r in logging if r["runs"] and sum(x["passed"] for x in r["runs"]) / len(r["runs"]) < 2 / 3]
        lines.append(f"logging cases (a write in must): {_rate(sum(1 for x in runs if x['passed']), len(runs))} runs "
                     f"passed; under 2 of 3: {', '.join(low) or 'none'}")
    return "\n".join(lines), ok
