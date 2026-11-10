"""Run the evals: python -m evals.run [--n 3] [--threshold 1.0] [--case ID ...] [--model M] [--keep]

One local server for the whole run, with a temporary SQLite file. For each case, n fresh conversations
with the Claude CLI, each with a new throwaway person. Each run is judged by what the server received
(evals/recorder.py) and by that person's rows afterwards (evals/judge.py). See docs/evals.md.

The sign-in token is never printed, never kept: every line that is printed or saved goes through
judge.masked, and each run's MCP config file (the one place the token is written) is deleted after it.
"""

import argparse
import datetime as dt
import itertools
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zoneinfo
from pathlib import Path

from evals import judge
from evals.issuer import DOMAIN, RESOURCE, FakeProvider, Key, claims, sign
from evals.recorder import CallRecorder

RUN_TIMEOUT = 180  # seconds per CLI run
TABLES = ("food_entry", "saved_food", "bodyweight", "measurement", "cardio_entry", "lift_session")

# Set by the Claude Code session this may be started from. They make a child CLI think it is part of that
# session (or point it at its IDE and messaging sockets), so they are dropped: each run then is what it
# would be from a plain terminal. CLAUDE_CONFIG_DIR and everything else is passed through.
SESSION_VARIABLES = ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "AI_AGENT")
SESSION_PREFIX = "CLAUDE_CODE_"

# A zone for each common offset, when the machine's own IANA name cannot be read (Windows).
PREFERRED_ZONES = {330: "Asia/Kolkata", 0: "Europe/London", 60: "Europe/Paris", 120: "Europe/Athens",
                   180: "Europe/Moscow", 240: "Asia/Dubai", 300: "Asia/Karachi", 345: "Asia/Kathmandu",
                   360: "Asia/Dhaka", 420: "Asia/Bangkok", 480: "Asia/Singapore", 540: "Asia/Tokyo",
                   600: "Australia/Brisbane", -300: "America/New_York", -360: "America/Chicago",
                   -420: "America/Denver", -480: "America/Los_Angeles", -240: "America/New_York",
                   -180: "America/Sao_Paulo"}


# ---------------------------------------------------------------- small pure helpers

def child_env(environ: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The environment for the CLI, and the names dropped from it."""
    dropped = sorted(k for k in environ if k in SESSION_VARIABLES or k.startswith(SESSION_PREFIX))
    return {k: v for k, v in environ.items() if k not in dropped}, dropped


def cli_command(claude: str, say: str, config_path: str, model: str | None) -> list[str]:
    command = [claude, "-p", say, "--output-format", "json", "--mcp-config", config_path, "--strict-mcp-config",
               "--allowedTools", "mcp__spotter__*", "--no-session-persistence"]
    if model:
        command += ["--model", model]
    return command


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


def dates_for(zone: str) -> dict[str, str]:
    today = dt.datetime.now(zoneinfo.ZoneInfo(zone)).date()
    return {"today": today.isoformat(), "yesterday": (today - dt.timedelta(days=1)).isoformat()}


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
        """A fresh person with this time zone, this switch (None: the default) and these rows, whose
        data may hold date placeholders. Returns (user id, token, seeded ids)."""
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
            result = getattr(services, seed["service"])(user_id, judge.resolve(dict(seed["data"]), dates), "web")
            if "error" in result:
                raise RuntimeError(f"seed {seed.get('as')} failed: {result['error']}: {result.get('message')}")
            if seed.get("as"):
                seeded[seed["as"]] = result["id"]
        token = sign(self.key, claims(subject, email=f"{subject}@example.com", name="Eval Person"))
        return user_id, token, seeded



def person_rows(user_id: int) -> dict[str, list[dict]]:
    """The person's rows, deleted ones too (judge_db tells them apart by deleted_at), in every table a
    case can check, from the running settings' database. Dates and times come back as ISO strings."""
    from sqlalchemy import select

    from app import db
    from app.models import Base

    found = {}
    with db.get_engine().connect() as connection:
        for name in TABLES:
            table = Base.metadata.tables[name]
            query = select(table).where(table.c.user_id == user_id)
            found[name] =[{k: _plain(v) for k, v in row._mapping.items()} for row in connection.execute(query)]
    return found


# ---------------------------------------------------------------- one run

def run_once(server: LocalServer, case: dict, zone: str, claude: str, model: str | None, env: dict[str, str],
             workdir: Path, label: str, keep: bool) -> dict:
    user_id, token, seeded = server.new_person(zone, case.get("seed") or [], case.get("switch"))
    variables = {**dates_for(zone), **{f"seed.{k}": v for k, v in seeded.items()}}
    resolved = judge.resolve(case, variables)
    config_path = workdir / f"mcp-{label}.json"
    cwd = workdir / f"cwd-{label}"
    cwd.mkdir()
    config_path.write_text(json.dumps(mcp_config(server.port, token)), encoding="utf-8")
    outcome = {"passed": False, "tokens": 0, "cost_usd": 0.0, "reply": "", "reasons": [], "models": [],
               "num_turns": None}
    started = time.monotonic()
    try:
        done = subprocess.run(cli_command(claude, case["say"], str(config_path), model), cwd=cwd, env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
                              timeout=RUN_TIMEOUT, shell=False)
        stdout, stderr = done.stdout or "", done.stderr or ""
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
        stderr = f"timed out after {RUN_TIMEOUT} s"
    finally:
        config_path.unlink(missing_ok=True)  # the only file that held the token
    outcome["seconds"] = round(time.monotonic() - started, 1)
    if keep:
        (workdir / f"out-{label}.json").write_text(judge.masked(stdout, token), encoding="utf-8")
        if stderr:
            (workdir / f"err-{label}.txt").write_text(judge.masked(stderr, token), encoding="utf-8")
    shutil.rmtree(cwd, ignore_errors=True)

    calls = server.recorder.calls(token)
    outcome["calls"] = calls
    outcome["methods"] = server.recorder.methods(token)
    outcome["instructions"] = server.recorder.got_instructions(token)
    try:
        parsed = judge.parse_cli_output(stdout)
    except judge.CliOutputError as e:
        outcome["reasons"] = [f"CLI: {e}; stderr: {judge.masked(stderr.strip()[-500:], token) or 'none'}"]
        return outcome
    outcome.update({k: parsed[k] for k in ("tokens", "cost_usd", "reply", "models", "num_turns")})
    outcome["permission_denials"] = parsed["permission_denials"]
    reasons = []
    if parsed["is_error"] or parsed["subtype"] != "success":
        reasons.append(f"CLI ended with {parsed['subtype']}")
    reasons += judge.judge(resolved, calls, parsed["reply"], person_rows(user_id))
    outcome["reasons"] = [judge.masked(r, token) for r in reasons]
    outcome["reply"] = judge.masked(outcome["reply"], token)
    outcome["passed"] = not outcome["reasons"]
    return outcome


# ---------------------------------------------------------------- main

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description="Run Spotter's evals with the Claude CLI.")
    parser.add_argument("--n", type=int, default=3, help="runs per case (default 3)")
    parser.add_argument("--threshold", type=float, default=1.0,
                        help="the pass rate every case must reach, 0 to 1 (default 1.0: every run)")
    parser.add_argument("--case", action="append", default=[], metavar="ID", help="run only this case (repeatable)")
    parser.add_argument("--model", help="passed through to the CLI as --model")
    parser.add_argument("--keep", action="store_true",
                        help="keep the temporary directory with the database and each run's raw output")
    parser.add_argument("--time-zone", help="the person's IANA time zone (default: the machine's)")
    args = parser.parse_args(argv)
    if args.n < 1:
        parser.error("--n must be at least 1")
    if not 0 <= args.threshold <= 1:
        parser.error("--threshold is a rate from 0 to 1")
    return args


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
    claude = shutil.which("claude")
    if claude is None:
        print("The Claude CLI (claude) is not on PATH. Install it and sign in first.")
        return 2
    zone = local_time_zone(args.time_zone)
    env, dropped = child_env(dict(os.environ))

    workdir = Path(tempfile.mkdtemp(prefix="spotter-evals-"))
    server = LocalServer(workdir)
    results = []
    seen_models: set[str] = set()
    try:
        server.start()
        print(f"Spotter on 127.0.0.1:{server.port}, person's zone {zone}, {len(cases)} cases x {args.n} runs")
        if dropped:
            print(f"Left out of the CLI's environment: {', '.join(dropped)}")
        for case in cases:
            runs = []
            for i in range(1, args.n + 1):
                label = f"{case['id']}-{i}"
                outcome = run_once(server, case, zone, claude, args.model, env, workdir, label, args.keep)
                seen_models.update(outcome.get("models") or [])
                runs.append(outcome)
                verdict = "pass" if outcome["passed"] else "FAIL"
                names = ", ".join(c["name"] + (" (refused)" if c["is_error"] else "") for c in outcome["calls"])
                print(f"  {label}: {verdict} in {outcome['seconds']} s; calls: {names or 'none'}", flush=True)
            results.append({"id": case["id"], "runs": runs})
    finally:
        server.stop()

    if args.keep:
        (workdir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    table, ok = judge.summarise(results, args.n, args.threshold)
    print(table)
    print(f"model: {', '.join(sorted(seen_models)) or 'not reported'}")
    handshakes = [r.get("instructions") for res in results for r in res["runs"]]
    print(f"handshakes that carried the server's instructions: {sum(1 for h in handshakes if h)} of "
          f"{len(handshakes)} runs (whether the CLI shows them to the model is what the cases measure)")
    failures = [(res["id"], i, r) for res in results for i, r in enumerate(res["runs"], 1) if not r["passed"]]
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
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
