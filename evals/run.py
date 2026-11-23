"""Run the evals: python -m evals.run [--client claude|codex|gemini] [--n 3] [--threshold 1.0] [--case ID ...]
[--golden] [--core] [--model M] [--effort E] [--keep] [--tool-search on|off] [--transcripts]

One local server for the whole run, with a temporary SQLite file. For each case, n fresh conversations with the
client asked for, each with a new throwaway person: the Claude CLI (here), Codex (evals/codex_client.py) or a
Gemini model with this process as the MCP host (evals/gemini_client.py). Each run is judged by what the server
received (evals/recorder.py) and by that person's rows afterwards (evals/judge.py). See docs/evals.md.

The sign-in token is never printed, never kept: every line that is printed or saved goes through
judge.masked. For Claude, each run's MCP config file (the one place the token is written) is deleted after it; for
Codex the token is only in that one child's environment; for Gemini it is only in this process's memory.
"""

import argparse
import calendar
import datetime as dt
import itertools
import json
import logging
import re
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import zoneinfo
from pathlib import Path

from evals import judge
from evals.issuer import DOMAIN, RESOURCE, FakeProvider, Key, claims, sign
from evals.recorder import CallRecorder

RUN_TIMEOUT = 300  # seconds per CLI run: 180 until 2026-11-17, when two data runs were cut off mid-answer
TABLES = ("food_entry", "saved_food", "bodyweight", "measurement", "cardio_entry", "lift_session", "goal",
          "feedback", "routine", "program")

# Set by the Claude Code session this may be started from. They make a child CLI think it is part of that
# session (or point it at its IDE and messaging sockets), so they are dropped: each run then is what it
# would be from a plain terminal. CLAUDE_CONFIG_DIR and everything else is passed through.
SESSION_VARIABLES = ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "AI_AGENT")
SESSION_PREFIX = "CLAUDE_CODE_"

# The advisor is off in every run, since 2026-11-17, on the owner's word: a run measures the model asked for, alone.
# The advisor is the CLI's tool that hands the whole conversation to a second, stronger model; the user's own CLI
# settings turn it on. In the release run of that day 11 of 153 runs called it: claude-fable-5-2 beside the pinned
# model in modelUsage. Two layers. The documented switch is this variable, which also makes the CLI ignore any
# advisorModel (https://code.claude.com/docs/en/advisor.md, "Turn the advisor off";
# https://code.claude.com/docs/en/env-vars.md). The second is NO_ADVISOR_SETTINGS in cli_command, an empty
# advisorModel: it worked on CLI 2.1.326, but no doc names it as a switch. --disallowedTools advisor did not.
CHILD_SETS = {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}
NO_ADVISOR_SETTINGS = json.dumps({"advisorModel": ""})

# A zone for each common offset, when the machine's own IANA name cannot be read (Windows).
PREFERRED_ZONES = {330: "Asia/Kolkata", 0: "Europe/London", 60: "Europe/Paris", 120: "Europe/Athens",
                   180: "Europe/Moscow", 240: "Asia/Dubai", 300: "Asia/Karachi", 345: "Asia/Kathmandu",
                   360: "Asia/Dhaka", 420: "Asia/Bangkok", 480: "Asia/Singapore", 540: "Asia/Tokyo",
                   600: "Australia/Brisbane", -300: "America/New_York", -360: "America/Chicago",
                   -420: "America/Denver", -480: "America/Los_Angeles", -240: "America/New_York",
                   -180: "America/Sao_Paulo"}


# ---------------------------------------------------------------- small pure helpers

def child_env(environ: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The environment for the CLI, and the names dropped from it. CHILD_SETS is set on top, whatever the
    parent held for those names: it is replaced, not counted as dropped."""
    dropped = sorted(k for k in environ if (k in SESSION_VARIABLES or k.startswith(SESSION_PREFIX))
                     and k not in CHILD_SETS)
    env = {k: v for k, v in environ.items() if k not in dropped}
    env.update(CHILD_SETS)
    return env, dropped


# Tool search (contract log-and-guide.md, "Up-front loading"). Claude Code defers MCP functions and looks them up before
# first use, per its docs; ENABLE_TOOL_SEARCH=false loads every function up front, as the comparison rig ran. --tool-search
# on (the default) takes any value the parent held out, so a run is the CLI's own default whatever the shell holds; off
# sets the variable. Set after child_env, on the Claude CLI's environment only: Codex and Gemini load their own way.
TOOL_SEARCH_VARIABLE = "ENABLE_TOOL_SEARCH"
TOOL_SEARCH_MODES = ("on", "off")


def tool_search_env(env: dict[str, str], mode: str) -> dict[str, str]:
    """The Claude CLI's environment with tool search on (the variable taken out) or off (it set to false)."""
    if mode not in TOOL_SEARCH_MODES:
        raise ValueError(f"tool search is on or off, not {mode!r}")
    out = {k: v for k, v in env.items() if k != TOOL_SEARCH_VARIABLE}
    if mode == "off":
        out[TOOL_SEARCH_VARIABLE] = "false"
    return out


def tool_search_line(client: str, mode: str) -> str:
    """The run's line on tool search."""
    if client != "claude":
        return f"tool search: not applied: {client} loads its functions its own way (--tool-search is the Claude CLI's)"
    if mode == "off":
        return f"tool search: off, every function loaded up front ({TOOL_SEARCH_VARIABLE}=false)"
    return f"tool search: on, the CLI's default ({TOOL_SEARCH_VARIABLE} left unset)"


# The model every run uses unless --model says otherwise. Pinned on 2026-11-10 after the CLI's own default
# changed within a day and one full run cost three times the estimate.
DEFAULT_MODEL = "claude-opus-5-5"
CLIENTS = ("claude", "codex", "gemini")
# Each client's model when --model is left out: each is its release tier (contract function-list.md, D11). The gate
# tier is passed by name: --model claude-sonnet-5-5, --model gpt-6-luna (SMALL_GPT), as docs/evals.md says.
DEFAULT_MODELS = {"claude": DEFAULT_MODEL, "codex": "gpt-6.1-sol", "gemini": "gemini-4-hydrogen"}
# The small GPT, by its exact name in Codex's own model list (~/.codex/models_cache.json, fetched by codex-cli
# 0.169.0 on 2026-11-17 08:48 UTC): "Fast and affordable model for easier tasks", shown in the picker.
SMALL_GPT = "gpt-6-luna"
# The reasoning effort every run sends, a model at a time, unless --effort says otherwise (--effort "" sends none).
# Each is the model's own default, the level a person gets when nothing is set, pinned by the client's flag so a
# change in a default or in the owner's own settings never changes a run (his word, 2026-11-17: "yes, pin the
# defaults"). Claude: --effort; Codex: -c model_reasoning_effort; Gemini: generationConfig.thinkingConfig.
# thinkingLevel. The sources, read on 2026-11-17, no paid call:
#   claude-opus-5-5, claude-sonnet-5-5  medium: https://code.claude.com/docs/en/model-config.md, "Resolution order",
#                                       step 5: "Opus 5.5, Sonnet 5.5, and Haiku 5.5 default to medium"
#   gpt-6.1-sol low, gpt-6-luna medium  each model's default_reasoning_level in Codex's own model list
#                                       (~/.codex/models_cache.json, codex-cli 0.169.0)
#   gemini-4-hydrogen high              Google's thinking page (aistudio.google.com/docs/thinking): "On (high)"
# A model not here sends no effort: its client's own default, unpinned, and the run says so.
DEFAULT_EFFORTS = {"claude-opus-5-5": "medium", "claude-sonnet-5-5": "medium", "gpt-6.1-sol": "low",
                   "gpt-6-luna": "medium", "gemini-4-hydrogen": "high"}
# The levels each client takes: claude --help (2.1.326); Codex's model list (gpt-6.1-sol takes ultra too,
# gpt-6-luna stops at max); Google's thinking page (minimal is not taken by Gemini 4 or gemini-3.8-flash).
EFFORTS = {"claude": ("low", "medium", "high", "xhigh", "max"),
           "codex": ("low", "medium", "high", "xhigh", "max", "ultra"),
           "gemini": ("minimal", "low", "medium", "high", "xhigh")}
DAYS_AGO = judge.DAYS_AGO  # {{days_ago:N}} for N from 0 to this
WEEKDAY_N = judge.WEEKDAY_N  # {{weekday:N}} for N from 0 to this
# The reason a case with a second turn (then) fails on Codex or Gemini, unrun: their runners send one message a
# conversation, and a first turn alone is no run of the case.
TWO_TURNS_UNSENT = "{client} sends one message a conversation, so this case's second turn cannot be sent: failed, not run"


def cli_command(claude: str, say: str, config_path: str, model: str | None, effort: str | None = None,
                session: str | None = None, resume: bool = False, verbose: bool = False) -> list[str]:
    """One turn's command. A one-turn case keeps the run out of the CLI's saved sessions. A case with a second turn
    (then) names its session: the first turn starts it with --session-id and is saved, so the second can go on with
    --resume (contract exercise-list.md, C4g; the two flags were not checked against a live CLI). verbose (--transcripts)
    adds --verbose, so the JSON output is every message of the turn, the result last (judge.parse_cli_output reads
    either form; not checked against a live CLI)."""
    if session is None:
        kept = ["--no-session-persistence"]
    else:
        kept = ["--resume", session] if resume else ["--session-id", session]
    command = [claude, "-p", say, "--output-format", "json", *(["--verbose"] if verbose else []),
               "--mcp-config", config_path, "--strict-mcp-config", "--allowedTools", "mcp__spotter__*", *kept,
               "--settings", NO_ADVISOR_SETTINGS]  # the advisor's second off switch: see CHILD_SETS
    if effort:
        command += ["--effort", effort]
    if model:
        command += ["--model", model]
    return command


def _full_id(model: str | None) -> bool:
    return bool(model) and model.startswith("claude-")


def other_models(models: list[str], model: str | None) -> list[str]:
    """The models a run's modelUsage names beyond the one it should have, [] when it had only that one.

    A full model id asked for (it starts with "claude-") is the one: every other model is extra, a small helper
    model too, and all of them when that model took no part. An alias such as "sonnet", or none (--model "": the
    CLI's default), cannot be matched by name, because modelUsage names full ids: then the run must have exactly
    one model, and with more, every one is named. Cost cannot tell the main model: in the two runs kept on
    2026-11-17 the advisor cost more than the pinned model in 16 of the 19 that had both. No model reported
    gives []: model_reasons says it."""
    if _full_id(model):
        return [m for m in models if m != model]
    return sorted(models) if len(models) > 1 else []


def model_reasons(models: list[str], model: str | None) -> list[str]:
    """Why a run fails the one-model rule, [] when it passes. See other_models."""
    extra = other_models(models, model)
    if extra and _full_id(model):
        return [f"another model took part: {', '.join(extra)}"]
    if extra:
        return [f"more than one model took part: {', '.join(extra)}"]
    if not models:
        return ["the CLI reported no model, so no check that one model ran alone"]
    return []


def model_line(results: list[dict]) -> str:
    """The summary's line counting the runs another model took part in."""
    runs = [r for res in results for r in res["runs"]]
    joined = sorted({m for r in runs for m in r.get("other_models") or []})
    count = sum(1 for r in runs if r.get("other_models"))
    return f"runs where another model took part: {count} of {len(runs)}" + (f" ({', '.join(joined)})" if joined else "")


def mcp_config(port: int, token: str) -> dict:
    return {"mcpServers": {"spotter": {"type": "http", "url": f"http://127.0.0.1:{port}/mcp",
                                       "headers": {"Authorization": f"Bearer {token}"}}}}


def local_time_zone(override: str | None = None) -> str:
    """The machine's time zone as an IANA name: the override, TZ, /etc/localtime, else a zone with the
    machine's current UTC offset (any zone with that offset gives the same today and yesterday)."""
    for name in (override, os.environ.get("TZ")):
        if name:
            zoneinfo.ZoneInfo(name)  # raises on an unknown name
            return name
    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        target = str(localtime.resolve())
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    now = dt.datetime.now(dt.timezone.utc)
    offset = now.astimezone().utcoffset() or dt.timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    preferred = PREFERRED_ZONES.get(minutes)
    if preferred and now.astimezone(zoneinfo.ZoneInfo(preferred)).utcoffset() == offset:
        return preferred
    for name in sorted(zoneinfo.available_timezones()):
        if "/" in name and not name.startswith(("Etc/", "SystemV/")) and \
                now.astimezone(zoneinfo.ZoneInfo(name)).utcoffset() == offset:
            return name
    return "UTC"


def next_month_end(today: dt.date, month: int) -> dt.date:
    """The last day of this month (1 to 12), the next one strictly after today: "by the end of March"."""
    if not 1 <= month <= 12:
        raise ValueError("month is 1 to 12")
    for year in (today.year, today.year + 1):
        end = dt.date(year, month, calendar.monthrange(year, month)[1])
        if end > today:
            return end
    raise AssertionError("unreachable")


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")  # date.weekday()'s order


def dates_on(today: dt.date) -> dict[str, str]:
    """The placeholders' dates on this today: today, yesterday, days_ago:0 to :60, next_month_end:1 to :12, and
    weekday:0 to :6, the weekday N days after today in lower case, as save_program takes it (contract
    routines-and-programs.md, R15), so a schedule case never depends on the real weekday."""
    dates = {"today": today.isoformat(), "yesterday": (today - dt.timedelta(days=1)).isoformat()}
    dates.update({f"days_ago:{n}": (today - dt.timedelta(days=n)).isoformat() for n in range(DAYS_AGO + 1)})
    dates.update({f"next_month_end:{m}": next_month_end(today, m).isoformat() for m in range(1, 13)})
    dates.update({f"weekday:{n}": WEEKDAYS[(today + dt.timedelta(days=n)).weekday()] for n in range(WEEKDAY_N + 1)})
    return dates


def dates_for(zone: str) -> dict[str, str]:
    """The placeholders' dates for a person in this zone: dates_on the person's today."""
    return dates_on(dt.datetime.now(zoneinfo.ZoneInfo(zone)).date())


# A bearer token's shape (a JWT: three base64url parts, the first from '{"'), masked in a text a run raised even when
# it is no token this server signed.
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")


def kept_text(text: str, tokens: list[str]) -> str:
    """A text a run raised, as it may be kept and printed: every token this server signed masked, as judge.masked
    masks a run's own, and anything shaped like one too."""
    for token in tokens:
        text = judge.masked(text, token)
    return _JWT.sub(judge.HIDDEN, text)


def blank_outcome(client: str, model: str | None, effort: str | None) -> dict:
    """The keys every client's outcome carries before its run, so a run cut off or refused still reads in the
    table. cost_usd is None until a client reports one: Codex and Gemini give none."""
    return {"passed": False, "client": client, "model": model or None, "effort": effort or None, "tokens": 0,
            "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "cost_usd": None, "reply": "", "reasons": [],
            "models": [], "other_models": [], "num_turns": None, "calls": [], "seconds": 0.0}


def _plain(value):
    return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else value


# ---------------------------------------------------------------- the local server

class _QuietStartup(logging.Filter):
    """Drops the two expected start-up warnings: this server has no web sign-in, and the module-level
    app built from the environment on import has no MCP settings. Every other warning shows."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith(("Web sign-in disabled", "MCP endpoint disabled"))


class LocalServer:
    """The app, in-process, on 127.0.0.1, with its own SQLite file, behind the call recorder."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.key = Key("eval-key")
        self.provider = FakeProvider(self.key)
        self.port = 0
        self.recorder: CallRecorder | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._subjects = itertools.count(1)
        self.issued: list[str] = []  # every token signed for a person, so a kept text can be masked for each
        self._stamp = int(time.time())

    def start(self) -> None:
        spotter_log = logging.getLogger("spotter")
        if not spotter_log.handlers:  # app.main adds its own only when none is there
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
            handler.addFilter(_QuietStartup())
            spotter_log.addHandler(handler)
            spotter_log.setLevel(logging.WARNING)
            spotter_log.propagate = False

        import uvicorn
        from alembic import command
        from alembic.config import Config

        from app import config, db, exercises
        from app.config import ROOT, Settings
        from app.main import create_app

        url = f"sqlite:///{(self.workdir / 'evals.db').as_posix()}"
        self.settings = Settings(authkit_domain=DOMAIN, mcp_resource_url=RESOURCE, auth_debug=False,
                                 database_url=url, signups_per_day=100_000)
        config.use_settings(self.settings)
        alembic = Config(str(ROOT / "alembic.ini"))
        alembic.attributes["database_url"] = url
        alembic.attributes["skip_logging"] = True
        command.upgrade(alembic, "head")
        app = create_app(self.settings, fetch=self.provider.fetch)
        with db.session_scope() as session:
            exercises.sync(session)

        self.recorder = CallRecorder(app)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        self._server = uvicorn.Server(uvicorn.Config(self.recorder, log_level="warning", access_log=False,
                                                     lifespan="on"))
        self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [sock]}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:  # noqa: S310
                    if r.status == 200:
                        return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("the local server did not start")

    def stop(self) -> None:
        from app import db
        if self._server is not None:
            self._server.should_exit = True
            if self._thread is not None:
                self._thread.join(timeout=15)
        db.dispose()  # Windows keeps the file locked while a connection is open

    # ------------------------------------------------------------ people

    def new_person(self, zone: str, seeds: list[dict], switch: str | None = None) -> tuple[int, str, dict[str, int]]:
        """A fresh person with this time zone, this switch (None: the model's default, log_delete) and these
        rows, whose data may hold date placeholders and the ids of the rows seeded before them. Returns (user
        id, token, seeded ids)."""
        from app import db, services
        from app.models import User

        subject = f"user_eval_{self._stamp}_{next(self._subjects):04d}"
        now = db.utcnow()
        with db.session_scope() as session:
            user = User(provider=self.settings.auth_provider, subject=subject, email=f"{subject}@example.com",
                        name="Eval Person", time_zone=zone, created_at=now, updated_at=now, last_seen_at=now)
            session.add(user)
            session.flush()
            user_id = user.id
        if switch is not None:
            done = services.set_claude_can(user_id, switch)  # as the Settings page sets it
            if "error" in done:
                raise RuntimeError(f"switch {switch} failed: {done['error']}: {done.get('message')}")
        seeded = {}
        dates = dates_for(zone)
        for seed in seeds:
            service = judge.seed_service(seed["service"])
            if seed["service"] in judge.NO_DATA_SERVICES:
                result = service(user_id)
            else:
                data = judge.resolve(dict(seed["data"]), {**dates, **{f"seed.{k}": v for k, v in seeded.items()}})
                if seed["service"] == "edit_entry":  # an edit of a row seeded before it
                    result = service(user_id, data["kind"], data["entry_id"], data["changes"], "web")
                else:
                    result = service(user_id, data, "web")
            if "error" in result:
                raise RuntimeError(f"seed {seed.get('as')} failed: {result['error']}: {result.get('message')}")
            if seed.get("as"):
                seeded[seed["as"]] = result["id"]
        token = sign(self.key, claims(subject, email=f"{subject}@example.com", name="Eval Person"))
        self.issued.append(token)
        return user_id, token, seeded



def person_rows(user_id: int) -> dict[str, list[dict]]:
    """The person's rows, deleted ones too (judge_db tells them apart by deleted_at), in every table a
    case can check, from the running settings' database. Dates and times come back as ISO strings. A
    lift_session row also carries set_count: how many lift_set rows it holds (0 for none), from one counted
    query over all of the person's sessions. A routine row carries lift_count: how many lifts its lifts JSON holds,
    a saved_food row item_count: how many foods its items JSON holds, 0 for a plain food (macros-and-asking.md),
    and a program row week_count and day_count: the weeks and the training days of its blocks JSON, each added over
    every block (routines-and-programs.md, R15).
    lift_set, which has no user_id of its own, is every set of the person's sessions, one row a set (contract
    exercise-list.md, C4g): its id, session_id, set_number, exercise (the lift's name), weight_kg, reps, time_s and
    distance_m, with its session's date and deleted_at, so a set of a deleted session reads as deleted. A goal row
    carries exercise: its lift's name, or None for a goal of no lift (exercise-list.md, C4g: a goal set from a plain
    word is judged by the lift it was saved on)."""
    from sqlalchemy import func, select

    from app import db
    from app.models import Base

    found = {}
    with db.get_engine().connect() as connection:
        for name in TABLES:
            table = Base.metadata.tables[name]
            query = select(table).where(table.c.user_id == user_id)
            found[name] =[{k: _plain(v) for k, v in row._mapping.items()} for row in connection.execute(query)]
        sets, sessions = Base.metadata.tables["lift_set"], Base.metadata.tables["lift_session"]
        lifts = Base.metadata.tables["exercise"]
        counted = dict(connection.execute(
            select(sets.c.session_id, func.count()).join(sessions, sets.c.session_id == sessions.c.id)
            .where(sessions.c.user_id == user_id).group_by(sets.c.session_id)).all())
        query = (select(sets.c.id, sets.c.session_id, sets.c.set_number, lifts.c.name.label("exercise"),
                        sets.c.weight_kg, sets.c.reps, sets.c.time_s, sets.c.distance_m, sessions.c.date,
                        sessions.c.deleted_at)
                 .join(sessions, sets.c.session_id == sessions.c.id).join(lifts, sets.c.exercise_id == lifts.c.id)
                 .where(sessions.c.user_id == user_id).order_by(sets.c.id))
        found["lift_set"] = [{k: _plain(v) for k, v in row._mapping.items()} for row in connection.execute(query)]
        names = dict(connection.execute(select(lifts.c.id, lifts.c.name)).all())
    for row in found["goal"]:
        row["exercise"] = names.get(row.get("exercise_id"))
    for row in found["lift_session"]:
        row["set_count"] = counted.get(row["id"], 0)
    for row in found["routine"]:
        row["lift_count"] = len(json.loads(row["lifts"]))
    for row in found["saved_food"]:
        row["item_count"] = len(json.loads(row["items"])) if row.get("items") else 0
    for row in found["program"]:
        blocks = json.loads(row["blocks"])
        row["week_count"] = sum(block["weeks"] for block in blocks)
        row["day_count"] = sum(len(block["days"]) for block in blocks)
    return found


# ---------------------------------------------------------------- one run

def forget_session(session: str, env: dict[str, str]) -> list[str]:
    """Deletes what a two-turn run left in the Claude CLI's own store, and nothing else (contract exercise-list.md,
    C4g). The store is <config dir>/projects, the config dir the CLI's CLAUDE_CONFIG_DIR when set, else ~/.claude; a
    session is <project folder>/<session>.jsonl, and the CLI may keep a folder <project folder>/<session>/ beside it
    (tool-results). How the CLI names a project folder is not relied on: every match of projects/*/<session>.jsonl and
    projects/*/<session> goes, then each match's project folder if it is then empty. session must be a UUID as written,
    so the patterns name that session alone. A match whose resolved path is outside projects (a junction or a link out
    of it) is left, and named. Returns the paths it could not delete or left."""
    if str(uuid.UUID(session)) != session:  # raises on anything that is no UUID: the patterns must never widen
        raise ValueError(f"not a session id as the runner writes one: {session!r}")
    projects = Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    if not projects.is_dir():
        return []
    root = projects.resolve()
    failed = []
    for path in list(projects.glob(f"*/{session}.jsonl")) + list(projects.glob(f"*/{session}")):
        try:
            if not path.resolve().is_relative_to(root) or path.is_symlink() or path.is_junction():
                failed.append(f"{path} (a link out of the store: left)")
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            folder = path.parent
            if folder.resolve().parent == root and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            failed.append(str(path))
    return failed


def _joined(turns: list[dict]) -> dict:
    """The parsed CLI output of a run's turns as one: the last turn's reply, the tokens, cost and model steps of all
    of them added, every model any turn reported, every permission denial."""
    if len(turns) == 1:
        return turns[0]
    out = dict(turns[-1])
    for key in ("tokens", "input_tokens", "cached_tokens", "output_tokens", "cost_usd"):
        out[key] = sum(t[key] for t in turns)
    steps = [t["num_turns"] for t in turns if judge._number(t.get("num_turns"))]
    out["num_turns"] = sum(steps) if steps else None
    out["models"] = list(dict.fromkeys(m for t in turns for m in t["models"]))
    out["permission_denials"] = [d for t in turns for d in t["permission_denials"]]
    return out


def run_once(server: LocalServer, case: dict, zone: str, claude: str, model: str | None, env: dict[str, str],
             workdir: Path, label: str, keep: bool, effort: str | None = None, transcripts: bool = False) -> dict:
    """One Claude CLI run of a case. keep writes each turn's output to out-<label>.json (out-<label>-turn2.json for a
    second turn), and its error output beside it; transcripts makes that output the turn's whole transcript (the CLI's
    --verbose) and is kept with it. Every kept text is masked: the run's token, and anything shaped like a token."""
    user_id, token, seeded = server.new_person(zone, case.get("seed") or [], case.get("switch"))
    variables = {**dates_for(zone), **{f"seed.{k}": v for k, v in seeded.items()}}
    resolved = judge.resolve(case, variables)
    config_path = workdir / f"mcp-{label}.json"
    cwd = workdir / f"cwd-{label}"
    cwd.mkdir()
    config_path.write_text(json.dumps(mcp_config(server.port, token)), encoding="utf-8")
    outcome = {**blank_outcome("claude", model, effort), "cost_usd": 0.0}
    # A case with then is two turns of one conversation: the first names a session, the second resumes it, in the
    # same working folder with the same config file (contract exercise-list.md, C4g). Each turn has its own timeout.
    says = [case["say"]] + ([case["then"]] if case.get("then") else [])
    session = str(uuid.uuid4()) if len(says) > 1 else None
    turns: list[dict] = []
    failed = None
    first_turn = None  # how many calls the recorder held when the first turn ended
    started = time.monotonic()
    try:
        for i, say in enumerate(says):
            try:
                done = subprocess.run(cli_command(claude, say, str(config_path), model, effort, session, i > 0,
                                                  transcripts),
                                      cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                      encoding="utf-8", errors="replace", timeout=RUN_TIMEOUT, shell=False)
                stdout, stderr = done.stdout or "", done.stderr or ""
            except subprocess.TimeoutExpired as e:
                stdout = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
                stderr = f"timed out after {RUN_TIMEOUT} s"
            if i == 0:
                first_turn = len(server.recorder.calls(token))
            name = label if i == 0 else f"{label}-turn{i + 1}"
            if keep or transcripts:
                (workdir / f"out-{name}.json").write_text(kept_text(stdout, [token]), encoding="utf-8")
                if stderr:
                    (workdir / f"err-{name}.txt").write_text(kept_text(stderr, [token]), encoding="utf-8")
            try:
                turns.append(judge.parse_cli_output(stdout))
            except judge.CliOutputError as e:
                failed = (f"{f'turn {i + 1}: ' if i else ''}CLI: {e}; stderr: "
                          f"{judge.masked(stderr.strip()[-500:], token) or 'none'}")
                break
    finally:
        config_path.unlink(missing_ok=True)  # the only file that held the token
    outcome["seconds"] = round(time.monotonic() - started, 1)
    shutil.rmtree(cwd, ignore_errors=True)
    left = forget_session(session, env) if session is not None else []  # the CLI's saved conversation

    calls = server.recorder.calls(token)
    if session is not None:  # each call's turn, so judge_turns can tell a write made before the second line
        for n, c in enumerate(calls):
            c["turn"] = 1 if n < first_turn else 2
        if left:  # a delete failed: the summary names the session, so it can be removed by hand
            outcome["session_id"] = session
            outcome["session_left"] = left
    outcome["calls"] = calls
    outcome["methods"] = server.recorder.methods(token)
    outcome["instructions"] = server.recorder.got_instructions(token)
    if failed:
        outcome["reasons"] = [failed]
        return outcome
    parsed = _joined(turns)
    outcome.update({k: parsed[k] for k in ("tokens", "input_tokens", "cached_tokens", "output_tokens", "cost_usd",
                                           "reply", "models", "num_turns")})
    outcome["permission_denials"] = parsed["permission_denials"]
    outcome["replies"] = [judge.masked(t["reply"], token) for t in turns]  # each turn's, the last one the reply judged
    reasons = []
    for i, turn in enumerate(turns):
        if turn["is_error"] or turn["subtype"] != "success":
            reasons.append(f"{f'turn {i + 1}: ' if len(turns) > 1 else ''}CLI ended with {turn['subtype']}")
    outcome["other_models"] = other_models(parsed["models"], model)  # the one-model rule: see CHILD_SETS
    reasons += model_reasons(parsed["models"], model)
    reasons += judge.judge(resolved, calls, parsed["reply"], person_rows(user_id),
                           turns[0]["reply"] if len(turns) > 1 else None)
    outcome["reasons"] = [judge.masked(r, token) for r in reasons]
    outcome["reply"] = judge.masked(outcome["reply"], token)
    outcome["passed"] = not outcome["reasons"]
    return outcome


# ---------------------------------------------------------------- main

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.run",
                                     description="Run Spotter's evals with the Claude CLI, Codex or a Gemini model.")
    parser.add_argument("--client", choices=CLIENTS, default="claude",
                        help="who answers: claude (the Claude CLI, the default), codex (codex exec) or gemini (a "
                             "Gemini model, this process the MCP host)")
    parser.add_argument("--n", type=int, default=3, help="runs per case (default 3)")
    parser.add_argument("--threshold", type=float, default=1.0,
                        help="the pass rate every counted case must reach, 0 to 1 (default 1.0: every run)")
    parser.add_argument("--case", action="append", default=[], metavar="ID", help="run only this case (repeatable)")
    parser.add_argument("--golden", action="store_true", help="run only the golden set: the cases with a kind")
    parser.add_argument("--core", action="store_true",
                        help="run only the core: the cases marked core, the set each gate runs (function-list.md)")
    parser.add_argument("--model", default=None,
                        help="the model, pinned by its exact name (default: claude "
                             f"{DEFAULT_MODELS['claude']}, codex {DEFAULT_MODELS['codex']}, gemini "
                             f"{DEFAULT_MODELS['gemini']}; an empty value uses the client's own default)")
    parser.add_argument("--effort", default=None,
                        help="the reasoning effort every run sends (default: the model's own default, pinned: "
                             + ", ".join(f"{m} {e}" for m, e in DEFAULT_EFFORTS.items())
                             + "; another model sends none; an empty value sends none)")
    parser.add_argument("--gemini-key", type=int, default=1, metavar="N",
                        help="gemini only: the key GOOGLE_API_KEY_<N> in .env.evals, the one key of every run "
                             "(default 1)")
    parser.add_argument("--gemini-per-minute", type=int, default=5, metavar="N",
                        help="gemini only: requests a minute at most, each spaced to keep under it (default 5, the "
                             "free tier's; 0: not paced)")
    parser.add_argument("--keep", action="store_true",
                        help="keep the temporary directory with the database and each run's raw output")
    parser.add_argument("--tool-search", choices=TOOL_SEARCH_MODES, default="on",
                        help="claude only: on (the default) leaves the CLI's tool search as it is, with any "
                             f"{TOOL_SEARCH_VARIABLE} of the parent taken out; off sets {TOOL_SEARCH_VARIABLE}=false, so "
                             "every function loads up front")
    parser.add_argument("--transcripts", action="store_true",
                        help="keep each run's whole transcript, masked, with everything --keep keeps: claude adds "
                             "--verbose; codex keeps its JSONL stream; gemini's is in results.json")
    parser.add_argument("--time-zone", help="the person's IANA time zone (default: the machine's)")
    args = parser.parse_args(argv)
    if args.transcripts:  # a transcript is a kept file: it lives in the temporary directory --keep keeps
        args.keep = True
    if args.n < 1:
        parser.error("--n must be at least 1")
    if not 0 <= args.threshold <= 1:
        parser.error("--threshold is a rate from 0 to 1")
    if args.model is None:
        args.model = DEFAULT_MODELS[args.client]
    if args.effort is None:
        args.effort = DEFAULT_EFFORTS.get(args.model or "", "")
    if args.effort and args.effort not in EFFORTS[args.client]:
        parser.error(f"--effort for {args.client} is one of {', '.join(EFFORTS[args.client])}, or empty")
    if args.gemini_key < 1:
        parser.error("--gemini-key is 1 or more")
    if args.gemini_per_minute < 0:
        parser.error("--gemini-per-minute is 0 or more")
    if args.client == "gemini" and not args.model:
        parser.error("--client gemini needs a model by name")
    return args


def model_lines(results: list[dict], client: str, model: str | None, effort: str | None) -> list[str]:
    """The lines under the table that say who answered: the client, the model asked for and the models the runs
    reported, the effort sent, and for Claude the one-model rule's count."""
    seen = sorted({m for res in results for r in res["runs"] for m in r.get("models") or []})
    asked = model or "the client's own default"
    if client == "codex":
        lines = [f"client: codex, model asked for: {asked}; Codex names no model in its events, so the one ran "
                 "is not reported"]
    else:
        lines = [f"client: {client}, model asked for: {asked}; reported: {', '.join(seen) or 'not reported'}"]
    lines.append(f"effort sent: {effort or 'none (the client default)'}")
    if client == "claude":
        lines.append(model_line(results))
    return lines


def transcripts_line(client: str, workdir: Path) -> str:
    """Where --transcripts keeps each run's transcript, by client."""
    where = {"claude": "out-<case>-<n>.json a turn (out-<case>-<n>-turn2.json for a second), the CLI's --verbose "
                       "messages",
             "codex": "out-<case>-<n>.jsonl a run, Codex's own JSONL stream",
             "gemini": "each run's transcript in results.json"}[client]
    return f"transcripts: kept in {workdir}: {where}, masked"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):  # a reply's character the console cannot show is replaced, not fatal
        sys.stdout.reconfigure(errors="replace")
    try:
        cases = judge.load_cases()
    except ValueError as e:
        print(f"cases.json: {e}")
        return 2
    if args.case:
        unknown = sorted(set(args.case) - {c["id"] for c in cases})
        if unknown:
            print(f"unknown case: {', '.join(unknown)}")
            return 2
        cases = [c for c in cases if c["id"] in args.case]
    if args.golden:
        cases = [c for c in cases if c.get("kind")]
    if args.core:
        cases = [c for c in cases if c.get("core") is True]
    if not cases:
        print("no case to run")
        return 2
    zone = local_time_zone(args.time_zone)
    env, dropped = child_env(dict(os.environ))
    if args.client == "claude":
        env = tool_search_env(env, args.tool_search)

    stops: tuple = ()  # what ends one model's runs early: a Gemini quota or a request its API refuses
    redact = judge.masked  # (text, None): nothing to hide but on Gemini, whose key it takes out
    if args.client == "claude":
        claude = shutil.which("claude")
        if claude is None:
            print("The Claude CLI (claude) is not on PATH. Install it and sign in first.")
            return 2
    elif args.client == "codex":
        from evals import codex_client
        codex = codex_client.find_codex()
        if codex is None:
            print("Codex (codex) is not installed. Install it and sign in first.")
            return 2
    else:
        from evals import gemini_client
        try:
            api = gemini_client.GeminiAPI(gemini_client.load_key(args.gemini_key), per_minute=args.gemini_per_minute)
        except KeyError as e:
            print(f"Gemini: {e.args[0]}")
            return 2
        host = gemini_client.GeminiHost(api, args.model, args.effort)
        stops = (gemini_client.QuotaStop, gemini_client.ConfigStop)
        redact = lambda text, _token: api.redact(text)  # noqa: E731

    workdir = Path(tempfile.mkdtemp(prefix="spotter-evals-"))
    server = LocalServer(workdir)

    def run_one(case: dict, label: str) -> dict:
        if args.client == "claude":
            return run_once(server, case, zone, claude, args.model, env, workdir, label, args.keep,
                            effort=args.effort, transcripts=args.transcripts)
        if case.get("then"):  # contract exercise-list.md, C4g: no way to go on with a conversation is built here
            return {**blank_outcome(args.client, args.model, args.effort),
                    "reasons": [TWO_TURNS_UNSENT.format(client=args.client)]}
        if args.client == "codex":
            return codex_client.run_once_codex(server, case, zone, codex, args.model, args.effort, env, workdir,
                                               label, args.keep)
        return gemini_client.run_once_gemini(server, case, zone, host)

    results = []
    stopped = None
    try:
        server.start()
        print(f"Spotter on 127.0.0.1:{server.port}, person's zone {zone}, {len(cases)} cases x {args.n} runs, "
              f"client {args.client}, model {args.model or 'the client default'}, effort {args.effort or 'none'}")
        print(tool_search_line(args.client, args.tool_search))
        if args.transcripts:
            print(transcripts_line(args.client, workdir))
        if dropped:
            print(f"Left out of the client's environment: {', '.join(dropped)}")
        for case in cases:
            runs = []
            for i in range(1, args.n + 1):
                label = f"{case['id']}-{i}"
                try:
                    outcome = run_one(case, label)
                except stops as e:  # this model's runs end; the run that hit it is kept, failed, with the stop
                    stopped = f"{label}: {type(e).__name__}: {redact(kept_text(str(e), server.issued), None)[:1500]}"
                    outcome = {**blank_outcome(args.client, args.model, args.effort),
                               "reasons": [f"stopped: {stopped}"]}
                except Exception as e:  # noqa: BLE001 - one run's fault never loses the others: it is that run's reason
                    outcome = {**blank_outcome(args.client, args.model, args.effort),
                               "reasons": [f"the run raised {type(e).__name__}: "
                                           f"{redact(kept_text(str(e), server.issued), None)[:500]}"]}
                runs.append(outcome)
                verdict = "pass" if outcome["passed"] else "FAIL"
                names = ", ".join(c["name"] + (" (refused)" if c["is_error"] else "") for c in outcome["calls"])
                print(f"  {label}: {verdict} in {outcome['seconds']} s; calls: {names or 'none'}", flush=True)
                if stopped:
                    break
            if runs:
                results.append({"id": case["id"], "kind": case.get("kind"), "tags": case.get("calls"), "runs": runs})
            if stopped:
                print(f"Stopped, this model's runs end here: {stopped}")
                break
    finally:
        server.stop()
        if args.keep:  # whatever ended the loop, the runs so far are kept
            (workdir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str),
                                                  encoding="utf-8")
    print()
    table, ok = judge.summarise(results, args.n, args.threshold)
    print(table)
    for line in model_lines(results, args.client, args.model, args.effort):
        print(line)
    print(tool_search_line(args.client, args.tool_search))
    handshakes = [r.get("instructions") for res in results for r in res["runs"]]
    print(f"handshakes that carried the server's instructions: {sum(1 for h in handshakes if h)} of "
          f"{len(handshakes)} runs (whether the client shows them to the model is what the cases measure)")
    kept = [r["session_id"] for res in results for r in res["runs"] if r.get("session_id")]
    if kept:  # a two-turn run's saved conversation that forget_session could not delete (docs/evals.md)
        print(f"sessions of two-turn runs the Claude CLI still keeps, a delete failed: {', '.join(kept)}")
    failures =[(res["id"], i, r) for res in results for i, r in enumerate(res["runs"], 1) if not r["passed"]]
    if failures:
        print("\nFailed runs:")
        for case_id, i, r in failures:
            print(f"- {case_id} run {i}:")
            for reason in r["reasons"]:
                print(f"    {reason}")
            if r.get("permission_denials"):
                print(f"    permission denials: {json.dumps(r['permission_denials'])[:400]}")
            print("    reply: " + (r["reply"].strip().replace("\n", "\n           ") or "(none)"))
    if args.keep:
        print(f"\nKept: {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0 if ok and not stopped else 1


if __name__ == "__main__":
    sys.exit(main())
