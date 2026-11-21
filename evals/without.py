"""The same model with Spotter and without it: python -m evals.without [--client claude|codex|gemini] [--model M]
[--effort E] [--n 3] [--case ID ...] [--arm W|N-history|N-memory|N-new ...] [--gemini-key N] [--gemini-per-minute N]
[--keep] [--time-zone Z]

Each case of evals/without_cases.json is one message, sent in a fresh conversation to one model in up to four arms
(contract docs/contracts/without-spotter.md):

  W          Spotter on the local eval server, the seeded person: evals/run.py's run_once, codex_client's
             run_once_codex or gemini_client's run_once_gemini, as they are.
  N-history  no tools; the person's whole history, rendered from the same seeded rows, one dated line a day, then
             the message.
  N-memory   no tools; a fixed summary of facts and habits (the case's memory text), then the message.
  N-new      no tools; the message alone.

Every N prompt starts with today's date, as a chat app states it. The prompt goes by stdin to the Claude CLI and to
Codex (a history is longer than the Windows command line), and as the one user message to Gemini.

Every arm is judged on the reply by the case's reply rule (new_reply for N-new when the case has one) and its band.
W is also judged on the server's record (the case's w), so it has two columns: the reply right, and the reply right
with the record. A run that did not run (a CLI error, a time-out, another model in the run, a setup fault) is not
counted: it is named apart.

The subprocesses of the N arms and of a Codex W run start through one popen seam, and the Gemini REST call through
post: the tests replace both. A Claude W run starts inside run.run_once. A Gemini key's value is taken out of every
line this prints or keeps.
"""

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from evals import codex_client, gemini_client, judge, run

CASES_FILE = Path(__file__).resolve().parent / "without_cases.json"
ARMS = ("W", "N-history", "N-memory", "N-new")
N_ARMS = ARMS[1:]
CASE_KEYS = {"id", "source", "say", "seed", "switch", "arms", "reply", "new_reply", "w", "band", "memory", "spotter",
             "apart"}
APART_KEYS = {"label", "w", "reply"}
REPLY_KEYS = {"reply_has", "reply_has_pattern", "reply_lacks", "reply_lacks_pattern", "reply_asks", "any_of"}
W_KEYS = {"expect", "calls", "db"}
BAND_KEYS = {"unit", "low", "high", "allowed"}
DEFAULT_MODELS = {"claude": "claude-sonnet-5-5", "codex": run.SMALL_GPT, "gemini": "gemini-4-hydrogen"}
EMPTY_MCP = json.dumps({"mcpServers": {}})
# Codex items that are no tool: an N run with any other item used a tool it should not have had.
CODEX_PLAIN_ITEMS = ("agent_message", "reasoning")
# Codex's own resource listings, on its built-in server "codex": with no MCP server given they find nothing, so an N
# run whose only tool items are these still ran with no tools. Any other tool, or any other server, is a tool used.
CODEX_LISTINGS = ("list_mcp_resources", "list_mcp_resource_templates")
# A W outcome's reasons that mean the run did not run as a run, not that the model was wrong.
FAULTS = ("CLI: ", "CLI ended with", "turn 1: CLI", "turn 2: CLI", "another model took part",
          "more than one model took part", "the CLI reported no model", "codex: ", "codex did not complete",
          "timed out after", "setup faults on every attempt")
GAP = r"[^\d\n.!?;:]{0,12}?"  # what may stand between a band's figure and its unit
MEALS = ("breakfast", "lunch", "dinner", "snack")
HISTORY_HEAD = "Everything I have told you about my training, food and body weight so far, one line a day, oldest first:"
MEMORY_HEAD = "What you remember about me:"


# ---------------------------------------------------------------- the cases

def w_case(case: dict) -> dict:
    """The case dict a W run gets: the keys evals/run.py's runners read, and nothing else."""
    w = case.get("w") or {}
    out = {"id": case["id"], "say": case["say"], "expect": w.get("expect") or {}}
    for key in ("seed", "switch"):
        if case.get(key) is not None:
            out[key] = case[key]
    for key in ("calls", "db"):
        if w.get(key) is not None:
            out[key] = w[key]
    return out


def _reply_key_problems(rule: dict, where: str) -> list[str]:
    problems = [f"{where} has {k}, which is not a reply key" for k in sorted(set(rule) - REPLY_KEYS)]
    for i, sub in enumerate(rule.get("any_of") or []):
        if isinstance(sub, dict):
            problems += _reply_key_problems(sub, f"{where}.any_of[{i}]")
    return problems


def _figure(text) -> Decimal | None:
    try:
        return abs(Decimal(str(text).replace(",", ""))).normalize()
    except InvalidOperation:
        return None


def band_problems(band) -> list[str]:
    if not isinstance(band, dict) or set(band) != BAND_KEYS:
        return [f"band must hold exactly {', '.join(sorted(BAND_KEYS))}"]
    problems = []
    try:
        re.compile(band["unit"], re.IGNORECASE)
    except (re.error, TypeError) as e:
        problems.append(f"band.unit is not a regular expression: {e}")
    if not all(judge._number(band[k]) for k in ("low", "high")) or band["low"] >= band["high"]:
        problems.append("band.low and band.high are numbers, low under high")
    elif not isinstance(band["allowed"], list) or not band["allowed"] or not all(
            judge._number(a) for a in band["allowed"]):
        problems.append("band.allowed is a non-empty list of numbers")
    return problems


def case_problems(case, memories: dict) -> list[str]:
    """What is wrong with one case of the file, or []."""
    if not isinstance(case, dict):
        return ["a case must be an object"]
    problems = [f"missing {k}" for k in ("id", "say", "arms", "reply") if k not in case]
    problems += [f"unknown key {k}" for k in sorted(set(case) - CASE_KEYS)]
    if problems:
        return problems
    arms = case["arms"]
    if not isinstance(arms, list) or not arms or len(set(arms)) != len(arms) or set(arms) - set(ARMS):
        return [f"arms must be a list of {', '.join(ARMS)}, each once"]
    for key in ("reply", "new_reply"):
        if key in case:
            if not isinstance(case[key], dict) or not case[key]:
                problems.append(f"{key} must be a non-empty object")
                continue
            problems += _reply_key_problems(case[key], key)
            # The judge's own check of the patterns and of the seed: a case of one turn, with this rule as its expect.
            plain = {"id": case["id"], "say": case["say"], "expect": case[key]}
            if case.get("seed") is not None:
                plain["seed"] = case["seed"]
            problems += [f"{key}: {p}" for p in judge.case_problems(plain)]
    if "new_reply" in case and "N-new" not in arms:
        problems.append("new_reply needs N-new in arms")
    if ("W" in arms) != ("w" in case):
        problems.append("w is there exactly when W is in arms")
    elif "w" in case:
        if not isinstance(case["w"], dict) or set(case["w"]) - W_KEYS or "expect" not in case["w"]:
            problems.append(f"w holds expect, and calls and db at most")
        else:
            problems += [f"w: {p}" for p in judge.case_problems(w_case(case))]
    if ("N-memory" in arms) != ("memory" in case):
        problems.append("memory is there exactly when N-memory is in arms")
    elif "memory" in case and case["memory"] not in memories:
        problems.append(f"memory {case['memory']!r} is not in memories")
    if "band" in case:
        problems += band_problems(case["band"])
    if "spotter" in case and (not isinstance(case["spotter"], list) or not case["spotter"]
                              or not all(isinstance(f, str) and _figure(f) is not None for f in case["spotter"])):
        problems.append("spotter is a list of figures written as text")
    if "apart" in case:
        problems += apart_problems(case)
    return problems


def apart_problems(case: dict) -> list[str]:
    """apart: a second outcome counted on its own, beside right and wrong: its label, the record rule that shows it
    in W and the reply rule that shows it in an N arm."""
    apart = case["apart"]
    if not isinstance(apart, dict) or set(apart) != APART_KEYS or not isinstance(apart["label"], str):
        return [f"apart holds exactly {', '.join(sorted(APART_KEYS))}"]
    problems = []
    for key in ("w", "reply"):
        if not isinstance(apart[key], dict) or not apart[key]:
            problems.append(f"apart.{key} must be a non-empty object")
            continue
        if key == "reply":
            problems += _reply_key_problems(apart["reply"], "apart.reply")
        plain = {"id": case["id"], "say": case["say"], "expect": apart[key]}
        problems += [f"apart.{key}: {p}" for p in judge.case_problems(plain)]
    return problems


def load_cases(path: Path = CASES_FILE) -> tuple[list[dict], dict[str, str]]:
    """(the cases, the memory texts by name), checked. Raises ValueError naming every problem."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list) or not data["cases"]:
        raise ValueError("the file is an object with a non-empty cases list")
    memories = data.get("memories") or {}
    if not isinstance(memories, dict) or not all(isinstance(v, str) and v for v in memories.values()):
        raise ValueError("memories maps a name to its text")
    problems, seen = [], set()
    for i, case in enumerate(data["cases"]):
        label = case.get("id", f"#{i}") if isinstance(case, dict) else f"#{i}"
        problems += [f"{label}: {p}" for p in case_problems(case, memories)]
        if label in seen:
            problems.append(f"{label}: duplicate id")
        seen.add(label)
    if problems:
        raise ValueError("; ".join(problems))
    return data["cases"], memories


# ---------------------------------------------------------------- the judge for this run

def band_reasons(band: dict | None, reply: str, say: str = "", calls: list[dict] | None = None) -> list[str]:
    """Every figure just before the band's unit and inside the band must be allowed: one of the band's own, one the
    message holds, or, in W, one an answered data read of this run gave. Dates and links are taken out first, as
    judge.py's figure reader takes them out. [] when it holds."""
    if not band:
        return []
    text = reply or ""
    for pattern in (judge._LINK, judge._ISO_DATE, judge._PROSE_DATE, judge._NUMERIC_DATE):
        text = pattern.sub(" ", text)
    allowed = {_figure(a) for a in band["allowed"]} | {v for v, _ in judge._text_figures(say or "")}
    allowed |= judge.spotter_figures(calls or [])
    near = re.compile(judge._FIGURE.pattern + GAP + rf"(?:{band['unit']})(?![a-z])", re.IGNORECASE)
    reasons = []
    for m in near.finditer(text):
        value = _figure(m[1] + (f".{m[2]}" if m[2] else ""))
        if value is not None and band["low"] <= value <= band["high"] and value not in allowed:
            reasons.append(f"the reply says {m[0]!r}: {format(value, 'f')} is inside {band['low']} to {band['high']} "
                           "and is no figure of the record")
    return reasons


def reply_reasons(case: dict, arm: str, reply: str, calls: list[dict] | None = None) -> list[str]:
    """Why the reply fails the arm's reply rule and the case's band, or []. Calls only in W."""
    rule = case.get("new_reply") if arm == "N-new" and case.get("new_reply") else case["reply"]
    reasons = judge.judge_calls(rule, [], reply or "", case["say"])
    if arm != "N-new" or not case.get("new_reply"):
        reasons += band_reasons(case.get("band"), reply, case["say"], calls if arm == "W" else None)
    return list(dict.fromkeys(reasons))


def counted_apart(case: dict, arm: str, reply: str, calls: list[dict]) -> bool:
    """The case's second outcome: in W by the server's record (apart.w), in an N arm by the reply (apart.reply)."""
    if arm == "W":
        return judge.judge_calls(case["apart"]["w"], calls, reply or "", case["say"]) == []
    return judge.judge_calls(case["apart"]["reply"], [], reply or "", case["say"]) == []


def quotes_spotter(case: dict, reply: str) -> bool:
    """The reply holds one of Spotter's own figures for this case (its spotter list)."""
    wanted = {_figure(f) for f in case.get("spotter") or []}
    return bool(wanted & {v for v, _ in judge._text_figures(reply or "")})


# ---------------------------------------------------------------- what an N arm is told

def num(value) -> str:
    """A number as a person writes it: no needless decimals."""
    return format(Decimal(str(value)).normalize(), "f")


def today_line(today: dt.date) -> str:
    return f"Today is {today:%A}, {today.day} {today:%B %Y}."


def person_today(user_id: int) -> dt.date:
    """The person's own today, from Spotter's one clock, as the seeds read it."""
    from app import db, users
    from app.models import User

    with db.session_scope() as s:
        return users.today_for(s.get(User, user_id))


def activity_names() -> dict[int, str]:
    """Every exercise's name by its id: person_rows gives a cardio row's exercise_id only."""
    from sqlalchemy import select

    from app import db
    from app.models import Base

    table = Base.metadata.tables["exercise"]
    with db.get_engine().connect() as connection:
        return dict(connection.execute(select(table.c.id, table.c.name)).all())


def _live(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("deleted_at") is None]


def _food_item(row: dict) -> str:
    name = (row.get("description") or "food").lower()
    if row.get("grams"):
        name += f" {num(row['grams'])} g"
    elif row.get("servings") and float(row["servings"]) != 1:
        name += f" x{num(row['servings'])}"
    if row.get("estimated"):
        return f"{name} (you estimated {num(row['kcal'])})"
    return f"{name} ({num(row['kcal'])} kcal)"


def _sets_text(sets: list[dict]) -> str:
    """One session's sets, the same set in a row told once with its count."""
    groups: list[list] = []
    for s in sorted(sets, key=lambda s: s["id"]):  # as logged: set_number counts within each lift
        key = (s["exercise"], s.get("weight_kg"), s.get("reps"), s.get("time_s"), s.get("distance_m"))
        if groups and groups[-1][0] == key:
            groups[-1][1] += 1
        else:
            groups.append([key, 1])
    parts = []
    for (name, kg, reps, seconds, metres), count in groups:
        text = f"{name.lower()} {count} {'set' if count == 1 else 'sets'}"
        if reps:
            text += f" of {num(reps)}"
        if seconds:
            text += f" for {num(seconds)} seconds"
        if metres:
            text += f" for {num(metres)} m"
        text += f" at {num(kg)} kg" if kg else ", bodyweight"
        parts.append(text)
    return ", ".join(parts)


def _cardio_text(row: dict, names: dict[int, str]) -> str:
    text = f"{names.get(row['exercise_id'], 'cardio').lower()} {num(row['minutes'])} minutes"
    if row.get("distance_km"):
        text += f", {num(row['distance_km'])} km"
    if row.get("incline_pct"):
        text += f", incline {num(row['incline_pct'])}%"
    if row.get("avg_hr"):
        text += f", average heart rate {num(row['avg_hr'])}"
    if row.get("machine_kcal"):
        text += f", the machine said {num(row['machine_kcal'])} kcal"
    return text


def _goal_text(goal: dict, names: dict[int, str]) -> str:
    by = goal["target_date"]
    if goal["kind"] == "bodyweight":
        return f"set a goal: body weight {num(goal['target_value'])} kg by {by}, I'm {num(goal['start_value'])} now"
    lift = (goal.get("exercise") or names.get(goal.get("exercise_id"), "")).lower()
    if goal["kind"] == "lift":
        return f"{lift} goal {num(goal['target_value'])} kg by {by}, my {lift} is {num(goal['start_value'])} now"
    if goal["kind"] == "cardio_best":
        return (f"{lift} goal: {num(goal['distance_km'])} km in {num(goal['target_value'])} minutes by {by}, "
                f"my best is {num(goal['start_value'])} minutes now")
    return f"goal: {num(goal['target_value'])} {goal.get('unit') or ''} by {by}".strip()


def history_lines(rows: dict[str, list[dict]], names: dict[int, str]) -> list[str]:
    """The person's history as they would have told it, one line a day, oldest first, from their live rows. The
    figures are the person's own (what they weighed, ate, lifted and did), never one Spotter worked out, such as a
    cardio calorie estimate."""
    days: dict[str, dict[str, list[str]]] = {}

    def add(date: str, part: str, text: str) -> None:
        days.setdefault(str(date)[:10], {}).setdefault(part, []).append(text)

    for row in _live(rows.get("bodyweight", [])):
        add(row["date"], "weight", f"weighed {num(row['kg'])} kg")
    for row in _live(rows.get("measurement", [])):
        add(row["date"], "measured", f"{row['kind'].replace('_', ' ')} {num(row['value'])} {row['unit']}")
    eaten: dict[tuple, list[dict]] = {}
    for row in sorted(_live(rows.get("food_entry", [])), key=lambda r: r["id"]):
        eaten.setdefault((str(row["date"])[:10], row.get("meal") or "food"), []).append(row)
    for (date, meal), items in sorted(eaten.items(), key=lambda kv: (
            kv[0][0], MEALS.index(kv[0][1]) if kv[0][1] in MEALS else len(MEALS))):
        add(date, "food", f"{meal}: " + ", ".join(_food_item(r) for r in items))
    sessions: dict[int, list[dict]] = {}
    for row in _live(rows.get("lift_set", [])):
        sessions.setdefault(row["session_id"], []).append(row)
    for session_id in sorted(sessions):
        add(sessions[session_id][0]["date"], "lifted", "lifted: " + _sets_text(sessions[session_id]))
    for row in sorted(_live(rows.get("cardio_entry", [])), key=lambda r: r["id"]):
        add(row["date"], "cardio", _cardio_text(row, names))
    for row in _live(rows.get("goal", [])):
        add(row["start_date"], "goal", _goal_text(row, names))
    order = ("weight", "measured", "food", "lifted", "cardio", "goal")
    lines = []
    for date in sorted(days):
        day = dt.date.fromisoformat(date)
        parts = [t for part in order for t in days[date].get(part, [])]
        lines.append(f"{date} {day:%a}: " + ". ".join(parts) + ".")
    return lines


def memory_text(template: str, rows: dict[str, list[dict]]) -> str:
    """The case's memory text with its goal dates filled from the person's goals: {bodyweight_by} and {lift_by},
    written as "16 January"."""
    filled = template
    for goal in _live(rows.get("goal", [])):
        target = dt.date.fromisoformat(str(goal["target_date"])[:10])
        filled = filled.replace("{" + goal["kind"] + "_by}", f"{target.day} {target:%B}")
    if "{" in filled:
        raise ValueError(f"the memory text keeps a slot no goal filled: {filled}")
    return filled


def prompt_for(arm: str, case: dict, rows: dict[str, list[dict]], names: dict[int, str], today: dt.date,
               memories: dict[str, str]) -> str:
    """What an N arm sends, in one message: today's date, then the history, the memory or nothing, then the case's
    message."""
    head = today_line(today)
    if arm == "N-history":
        lines = history_lines(rows, names)
        body = HISTORY_HEAD + "\n\n" + ("\n".join(lines) if lines else "(nothing yet)")
        return f"{head}\n\n{body}\n\n{case['say']}"
    if arm == "N-memory":
        return f"{head}\n\n{MEMORY_HEAD}\n{memory_text(memories[case['memory']], rows)}\n\n{case['say']}"
    if arm == "N-new":
        return f"{head}\n\n{case['say']}"
    raise ValueError(f"no prompt for the arm {arm}")


# ---------------------------------------------------------------- the commands and the request of an N arm

def claude_command(claude: str, model: str | None, effort: str | None) -> list[str]:
    """A Claude CLI run with no tools: no MCP server (an empty config, strictly), no built-in tool, the advisor off
    as run.py turns it off. The prompt goes by stdin: no prompt in the command."""
    command = [claude, "-p", "--output-format", "json", "--mcp-config", EMPTY_MCP, "--strict-mcp-config",
               "--tools", "", "--no-session-persistence", "--settings", run.NO_ADVISOR_SETTINGS]
    if effort:
        command += ["--effort", effort]
    if model:
        command += ["--model", model]
    return command


def codex_command(codex: str, cwd: Path, model: str | None, effort: str | None) -> list[str]:
    """codex_client's own command with its three Spotter server lines taken out and "-" as the prompt: Codex then
    reads the prompt from stdin. Everything else, web search off among it, is as a W run has it."""
    full = codex_client.codex_command(codex, "-", 0, cwd, model, effort)
    out, i = [], 0
    while i < len(full):
        if full[i] == "-c" and i + 1 < len(full) and full[i + 1].startswith("mcp_servers."):
            i += 2
            continue
        out.append(full[i])
        i += 1
    if len(full) - len(out) != 6 or any("mcp_servers" in part for part in out) or out[-1] != "-":
        raise RuntimeError("codex_client's command changed: the server lines could not be taken out")
    return out


def gemini_body(prompt: str, effort: str | None) -> dict:
    """One user message, no tools and no system instruction; the pinned thinking level, as a W run sends it."""
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    if effort:
        body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": effort.upper()}}
    return body


def stop_tree(process) -> None:
    """Stops a run that is cut off, with every process it started."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, shell=False)
    else:
        process.kill()


# ---------------------------------------------------------------- one run

def blank(case: dict, arm: str, client: str, model: str | None, effort: str | None, i: int) -> dict:
    return {**run.blank_outcome(client, model, effort), "case": case["id"], "arm": arm, "run": i, "status": "ok",
            "fault": None, "reply_passed": False, "record_passed": None, "reply_reasons": [], "record_reasons": [],
            "quoted_spotter": False, "prompt_chars": None, "apart": None, "codex_listings": []}


class Runner:
    """One invocation's client, model, server and seams."""

    def __init__(self, client: str, exe: str | None, model: str | None, effort: str | None, env: dict[str, str],
                 server, zone: str, workdir: Path, keep: bool, memories: dict[str, str], popen=subprocess.Popen,
                 api=None) -> None:
        self.client, self.exe, self.model, self.effort, self.env = client, exe, model, effort, env
        self.server, self.zone, self.workdir, self.keep, self.memories = server, zone, workdir, keep, memories
        self.popen, self.api = popen, api
        self.host = gemini_client.GeminiHost(api, model, effort) if api is not None else None

    def redact(self, text) -> str:
        text = run.kept_text(text if isinstance(text, str) else str(text), self.server.issued)
        return self.api.redact(text) if self.api is not None else text

    def one(self, case: dict, arm: str, i: int) -> dict:
        label = f"{case['id']}-{arm}-{i}"
        if arm == "W":
            outcome = self.w(case, label, i)
        else:
            outcome = self.n(case, arm, label, i)
        if outcome["status"] == "ok":
            outcome["quoted_spotter"] = quotes_spotter(case, outcome["reply"])
            if case.get("apart"):
                outcome["apart"] = counted_apart(case, arm, outcome["reply"], outcome.get("calls") or [])
        outcome["passed"] = (outcome["status"] == "ok" and outcome["reply_passed"]
                             and outcome["record_passed"] is not False)
        outcome["reasons"] = [self.redact(r) for r in
                              ([outcome["fault"]] if outcome["fault"] else []) + outcome["reply_reasons"]
                              + outcome["record_reasons"]]
        outcome["reply"] = self.redact(outcome["reply"])
        return outcome

    # ------------------------------------------------------------ W

    def w(self, case: dict, label: str, i: int) -> dict:
        wcase = w_case(case)
        if self.client == "claude":
            got = run.run_once(self.server, wcase, self.zone, self.exe, self.model, self.env, self.workdir, label,
                               self.keep, effort=self.effort)
        elif self.client == "codex":
            got = codex_client.run_once_codex(self.server, wcase, self.zone, self.exe, self.model, self.effort,
                                              self.env, self.workdir, label, self.keep, popen=self.popen)
        else:
            got = gemini_client.run_once_gemini(self.server, wcase, self.zone, self.host)
        outcome = {**blank(case, "W", self.client, self.model, self.effort, i), **got,
                   "case": case["id"], "arm": "W", "run": i}
        reasons = got.get("reasons") or []
        faults = [r for r in reasons if r.startswith(FAULTS)]
        if faults:
            outcome.update(status="fault", fault="; ".join(faults))
            return outcome
        outcome["record_reasons"] = list(reasons)
        outcome["reply_reasons"] = reply_reasons(case, "W", got.get("reply") or "", got.get("calls") or [])
        outcome["reply_passed"] = not outcome["reply_reasons"]
        outcome["record_passed"] = not outcome["record_reasons"]
        return outcome

    # ------------------------------------------------------------ N

    def n(self, case: dict, arm: str, label: str, i: int) -> dict:
        from evals.run import person_rows

        user_id, _token, _seeded = self.server.new_person(self.zone, case.get("seed") or [], case.get("switch"))
        prompt = prompt_for(arm, case, person_rows(user_id), activity_names(), person_today(user_id), self.memories)
        outcome = blank(case, arm, self.client, self.model, self.effort, i)
        outcome["prompt_chars"] = len(prompt)
        started = time.monotonic()
        if self.client == "gemini":
            fault = self._gemini(prompt, outcome)
        else:
            fault = self._process(prompt, outcome, label)
        outcome["seconds"] = round(time.monotonic() - started, 1)
        if fault:
            outcome.update(status="fault", fault=fault)
            return outcome
        outcome["reply_reasons"] = reply_reasons(case, arm, outcome["reply"])
        outcome["reply_passed"] = not outcome["reply_reasons"]
        return outcome

    def _process(self, prompt: str, outcome: dict, label: str) -> str | None:
        """A Claude CLI or Codex run with the prompt by stdin, in an empty folder. Returns the fault, or None."""
        cwd = self.workdir / f"cwd-{label}"
        cwd.mkdir()
        if self.client == "claude":
            command = claude_command(self.exe, self.model, self.effort)
        else:
            command = codex_command(self.exe, cwd, self.model, self.effort)
        process = self.popen(command, cwd=cwd, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, encoding="utf-8", errors="replace", shell=False)
        timed_out = False
        try:
            stdout, stderr = process.communicate(input=prompt, timeout=run.RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_tree(process)
            stdout, stderr = process.communicate()
        shutil.rmtree(cwd, ignore_errors=True)
        stdout, stderr = stdout or "", stderr or ""
        if self.keep:
            (self.workdir / f"out-{label}.txt").write_text(stdout, encoding="utf-8")
            if stderr:
                (self.workdir / f"err-{label}.txt").write_text(stderr, encoding="utf-8")
        tail = stderr.strip()[-500:] or "none"
        if timed_out:
            return f"timed out after {run.RUN_TIMEOUT} s"
        if self.client == "claude":
            try:
                parsed = judge.parse_cli_output(stdout)
            except judge.CliOutputError as e:
                return f"CLI: {e}; stderr: {tail}"
            outcome.update({k: parsed[k] for k in ("reply", "cost_usd", "tokens", "input_tokens", "cached_tokens",
                                                   "output_tokens", "models", "num_turns")})
            outcome["other_models"] = run.other_models(parsed["models"], self.model)
            if parsed["is_error"] or parsed["subtype"] != "success":
                return f"CLI ended with {parsed['subtype']}"
            wrong = run.model_reasons(parsed["models"], self.model)
            return "; ".join(wrong) if wrong else None
        try:
            parsed = codex_client.parse_codex_jsonl(stdout)
        except judge.CliOutputError as e:
            return f"codex: {e}; stderr: {tail}"
        outcome.update({k: parsed[k] for k in ("reply", "num_turns", "tokens", "input_tokens", "cached_tokens",
                                               "output_tokens")})
        outcome["item_types"] = parsed["item_types"]
        if not parsed["completed"] or parsed["errors"]:
            return f"codex did not complete the turn: {json.dumps(parsed['errors'])[:300]}"
        listings = [i for i in parsed["tool_items"] if i["server"] == "codex" and i["tool"] in CODEX_LISTINGS]
        outcome["codex_listings"] = [i["tool"] for i in listings]
        others = sorted({f"{i['server']}.{i['tool']}" for i in parsed["tool_items"] if i not in listings})
        tools = sorted(set(parsed["item_types"]) - set(CODEX_PLAIN_ITEMS) - {"mcp_tool_call"}) + others
        return f"codex used a tool with no tools given: {', '.join(tools)}" if tools else None

    def _gemini(self, prompt: str, outcome: dict) -> str | None:
        """One generateContent with no tools. A quota that persists or a refused thinking level stops this model, as
        in a W run; a transport error or a 5xx is tried again, at most MAX_SETUP_RETRIES times."""
        body = gemini_body(prompt, self.effort)
        backoffs: list = []
        for _ in range(gemini_client.MAX_SETUP_RETRIES + 1):
            status, parsed, _headers, attempts = self.api.generate(self.model, body, backoffs)
            if status == 429 and attempts == 2:
                raise gemini_client.QuotaStop(self.api.redact(json.dumps(parsed)[:3000]))
            if status == 400 and re.search(r"thinking", gemini_client._refusal_words(parsed), re.IGNORECASE):
                raise gemini_client.ConfigStop(self.api.redact(gemini_client._refusal_words(parsed))[:2000])
            if status == 200:
                break
            if status not in (0, 500, 502, 503, 504):
                return f"HTTP {status}: {self.api.redact(json.dumps(parsed))[:1500]}"
        else:
            return f"HTTP {status} on every try: {self.api.redact(json.dumps(parsed))[:1500]}"
        usage = parsed.get("usageMetadata") or {}
        split = judge.token_split("gemini", usage)
        outcome.update(split, tokens=sum(split.values()), num_turns=1,
                       models=[parsed["modelVersion"]] if parsed.get("modelVersion") else [])
        candidates = parsed.get("candidates") or []
        if not candidates:
            return f"no candidates: {self.api.redact(json.dumps(parsed.get('promptFeedback')))[:500]}"
        parts = (candidates[0].get("content") or {}).get("parts") or []
        outcome["finish_reasons"] = [candidates[0].get("finishReason")]
        if any("functionCall" in p for p in parts):
            return "gemini asked for a function with no tools given"
        outcome["reply"] = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
        return None if outcome["reply"].strip() else f"empty reply ({candidates[0].get('finishReason')})"


# ---------------------------------------------------------------- the table

def _count(runs: list[dict], key: str) -> str:
    ok = [r for r in runs if r["status"] == "ok"]
    good = sum(1 for r in ok if r.get(key) is True)
    return f"{good}/{len(ok)}"


def table(results: list[dict]) -> str:
    """One row a case and arm: the reply right, the reply right with the record (W), the runs that quoted
    Spotter's own figure, the runs that did not run, and a run's seconds, tokens and the cell's cost."""
    head = (f"{'case':<5} {'arm':<10} {'reply':>6} {'record':>7} {'spotter':>8} {'not run':>8} {'seconds':>8} "
            f"{'input':>8} {'cached':>8} {'output':>7} {'cost':>10}")
    lines = [head, "-" * len(head)]
    cells: dict[tuple, list[dict]] = {}
    for r in results:
        cells.setdefault((r["case"], r["arm"]), []).append(r)
    for (case, arm), runs in cells.items():
        tokens = [judge._run_tokens(r) for r in runs]
        record = _count(runs, "passed") if arm == "W" else "-"
        lines.append(f"{case:<5} {arm:<10} {_count(runs, 'reply_passed'):>6} {record:>7} "
                     f"{_count(runs, 'quoted_spotter'):>8} {sum(1 for r in runs if r['status'] != 'ok'):>8} "
                     f"{judge._mean([r.get('seconds') or 0 for r in runs]):>8.1f} "
                     f"{judge._mean([t[0] for t in tokens]):>8,.0f} {judge._mean([t[1] for t in tokens]):>8,.0f} "
                     f"{judge._mean([t[2] for t in tokens]):>7,.0f} {judge._money(judge._cost(runs)):>10}")
    return "\n".join(lines)


GROUPS = (("the reads", "R"), ("the unclear ones", "U"), ("the controls", "C"))


def summary(results: list[dict], cases: list[dict] | None = None) -> list[str]:
    """Per group of cases and arm: the counted runs, those right, and for W those right with the record. Then each
    case's second outcome counted apart, and the cost."""
    lines = []
    for name, prefix in GROUPS:
        for arm in ARMS:
            runs = [r for r in results if r["arm"] == arm and r["case"].startswith(prefix)]
            if not runs:
                continue
            ok = [r for r in runs if r["status"] == "ok"]
            line = f"{arm} on {name}: reply right {sum(r['reply_passed'] for r in ok)} of {len(ok)}"
            if arm == "W":
                line += f", with the record {sum(r['passed'] for r in ok)} of {len(ok)}"
            if len(ok) < len(runs):
                line += f"; not run: {len(runs) - len(ok)}"
            lines.append(line)
    for case in cases or []:
        if not case.get("apart"):
            continue
        counts = []
        for arm in ARMS:
            ok = [r for r in results if r["case"] == case["id"] and r["arm"] == arm and r["status"] == "ok"]
            if ok:
                counts.append(f"{arm} {sum(1 for r in ok if r.get('apart'))} of {len(ok)}")
        if counts:
            lines.append(f"{case['id']} counted apart, {case['apart']['label']}: {', '.join(counts)}")
    for arm in ARMS:
        runs = [r for r in results if r["arm"] == arm]
        if runs:
            lines.append(f"cost of {arm}: {judge._money(judge._cost(runs))}")
    lines.append(f"cost in all: {judge._money(judge._cost(results))}")
    return lines


def by_hand(results: list[dict]) -> list[str]:
    """The cells to read by hand: two arms of a case whose counts differ by 2 or more, every run of R7 and R8, every
    N-new run."""
    found = []
    counts: dict[str, dict[str, int]] = {}
    for r in results:
        if r["status"] == "ok":
            counts.setdefault(r["case"], {}).setdefault(r["arm"], 0)
            counts[r["case"]][r["arm"]] += 1 if r["passed"] else 0
    for case, arms in counts.items():
        names = list(arms)
        for a in range(len(names)):
            for b in range(a + 1, len(names)):
                if abs(arms[names[a]] - arms[names[b]]) >= 2:
                    found.append(f"{case} {names[a]} against {names[b]} ({arms[names[a]]} against {arms[names[b]]})")
    if any(r["case"] in ("R7", "R8") for r in results):
        found.append("every run of R7 and R8")
    if any(r["arm"] == "N-new" for r in results):
        found.append("every N-new run")
    return found


# ---------------------------------------------------------------- main

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.without",
                                     description="The same model with Spotter (W) and without it (N).")
    parser.add_argument("--client", choices=run.CLIENTS, default="claude")
    parser.add_argument("--model", default=None, help="default: " + ", ".join(
        f"{c} {m}" for c, m in DEFAULT_MODELS.items()))
    parser.add_argument("--effort", default=None, help="default: the model's own, pinned as evals.run pins it")
    parser.add_argument("--n", type=int, default=3, help="runs a cell (default 3); run 1 of every cell goes first")
    parser.add_argument("--case", action="append", default=[], metavar="ID", help="only this case (repeatable)")
    parser.add_argument("--arm", action="append", default=[], choices=ARMS, help="only this arm (repeatable)")
    parser.add_argument("--gemini-key", type=int, default=1, metavar="N",
                        help="gemini only: GOOGLE_API_KEY_<N> in .env.evals, the one key of the invocation")
    parser.add_argument("--gemini-per-minute", type=int, default=gemini_client.PER_MINUTE, metavar="N",
                        help="gemini only: requests a minute at most (0: not paced)")
    parser.add_argument("--keep", action="store_true", help="keep the folder: results.json and each run's output")
    parser.add_argument("--time-zone", help="the person's IANA time zone (default: the machine's)")
    args = parser.parse_args(argv)
    if args.n < 1:
        parser.error("--n must be at least 1")
    if args.model is None:
        args.model = DEFAULT_MODELS[args.client]
    if args.effort is None:
        args.effort = run.DEFAULT_EFFORTS.get(args.model or "", "")
    if args.effort and args.effort not in run.EFFORTS[args.client]:
        parser.error(f"--effort for {args.client} is one of {', '.join(run.EFFORTS[args.client])}, or empty")
    if args.gemini_key < 1 or args.gemini_per_minute < 0:
        parser.error("--gemini-key is 1 or more, --gemini-per-minute 0 or more")
    if args.client == "gemini" and not args.model:
        parser.error("--client gemini needs a model by name")
    return args


def main(argv: list[str] | None = None, popen=subprocess.Popen, post=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        cases, memories = load_cases()
    except ValueError as e:
        print(f"without_cases.json: {e}")
        return 2
    unknown = sorted(set(args.case) - {c["id"] for c in cases})
    if unknown:
        print(f"unknown case: {', '.join(unknown)}")
        return 2
    cells = [(c, arm) for c in cases if not args.case or c["id"] in args.case
             for arm in c["arms"] if not args.arm or arm in args.arm]
    if not cells:
        print("no case to run")
        return 2
    zone = run.local_time_zone(args.time_zone)
    env, dropped = run.child_env(dict(os.environ))
    exe, api = None, None
    if args.client == "claude":
        exe = shutil.which("claude")
        if exe is None:
            print("The Claude CLI (claude) is not on PATH. Install it and sign in first.")
            return 2
    elif args.client == "codex":
        exe = codex_client.find_codex()
        if exe is None:
            print("Codex (codex) is not installed. Install it and sign in first.")
            return 2
    else:
        try:
            key = gemini_client.load_key(args.gemini_key)
        except KeyError as e:
            print(f"Gemini: {e.args[0]}")
            return 2
        extra = {"post": post} if post is not None else {}
        api = gemini_client.GeminiAPI(key, per_minute=args.gemini_per_minute, **extra)
        del key

    workdir = Path(tempfile.mkdtemp(prefix="spotter-without-"))
    server = run.LocalServer(workdir)
    runner = Runner(args.client, exe, args.model, args.effort, env, server, zone, workdir, args.keep, memories,
                    popen=popen, api=api)
    stops = (gemini_client.QuotaStop, gemini_client.ConfigStop)
    results: list[dict] = []
    stopped = None
    try:
        server.start()
        model = args.model or "the client default"
        print(runner.redact(f"{len(cells)} cells x {args.n} runs, client {args.client}, model {model}, effort "
                            f"{args.effort or 'none'}, person's zone {zone}"))
        if dropped:
            print(f"Left out of the client's environment: {', '.join(dropped)}")
        for i in range(1, args.n + 1):  # run 1 of every cell first: the pilot
            for case, arm in cells:
                label = f"{case['id']} {arm} {i}"
                try:
                    outcome = runner.one(case, arm, i)
                except stops as e:
                    stopped = f"{label}: {type(e).__name__}: {runner.redact(str(e))[:1500]}"
                    outcome = {**blank(case, arm, args.client, args.model, args.effort, i), "status": "fault",
                               "fault": f"stopped: {stopped}", "reasons": [f"stopped: {stopped}"]}
                except Exception as e:  # noqa: BLE001 - one run's fault never loses the others
                    fault = f"the run raised {type(e).__name__}: {runner.redact(str(e))[:500]}"
                    outcome = {**blank(case, arm, args.client, args.model, args.effort, i), "status": "fault",
                               "fault": fault, "reasons": [fault]}
                results.append(outcome)
                verdict = ("not run" if outcome["status"] != "ok" else "right" if outcome["passed"]
                           else "reply right, record wrong" if outcome["reply_passed"] else "wrong")
                print(f"  {label}: {verdict} in {outcome['seconds']} s", flush=True)
                if stopped:
                    break
            if stopped:
                print(f"Stopped, this model's runs end here: {stopped}")
                break
    finally:
        server.stop()
        if args.keep:
            text = json.dumps(results, indent=2, ensure_ascii=False, default=str)
            (workdir / "results.json").write_text(runner.redact(text), encoding="utf-8")
    print()
    print(runner.redact(table(results)))
    for line in summary(results, cases) + run.model_lines([{"runs": results}], args.client, args.model, args.effort):
        print(runner.redact(line))
    hand = by_hand(results)
    if hand:
        print("read by hand: " + "; ".join(hand))
    odd = [r for r in results if r["status"] != "ok" or not r["passed"]]
    if odd:
        print("\nRuns not right:")
        for r in odd:
            print(runner.redact(f"- {r['case']} {r['arm']} run {r['run']}:"))
            for reason in r["reasons"]:
                print(runner.redact(f"    {reason}"))
            print(runner.redact("    reply: " + ((r.get("reply") or "").strip()[:1500].replace("\n", "\n           ")
                                                 or "(none)")))
    if args.keep:
        print(f"\nKept: {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
