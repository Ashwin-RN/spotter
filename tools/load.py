"""Load test, local only: python tools/load.py [--people 10] [--seconds 30]

Starts Spotter in-process on 127.0.0.1 with the evals' fake sign-in provider (evals/issuer.py), makes
--people throwaway people with signed tokens, and has them all call MCP at once, each in its own thread,
for --seconds: whoami, log_food, log_lifts, get_log, get_trends, get_goals, round and round. Then it prints,
for each function and in all: calls, errors by code, and the 50th and 95th percentile and the slowest time,
in ms, as the caller saw them (the request's whole round trip).

The database is DATABASE_URL, read through app.config like every setting: a local copy, never the live
site. It must be set (unset would be the repo's own spotter.db), and its host must be local: a Postgres
address names localhost, 127.0.0.1 or ::1 itself, since libpq would otherwise take PGHOST from the
environment. The migrations are run on it first, as the evals do. The throwaway people, and every row they
wrote, are deleted at the end.

The app's own limits apply as they would to anyone: a person past 120 writes or 300 reads in ten minutes is
refused, and those refusals show in the errors column (too_many_writes, too_many_reads). A
database_unavailable there means the pool or a query limit gave way.

It is a tool, not a test: its numbers are not asserted, and a laptop is not the host. Nothing is sent
anywhere but 127.0.0.1. The tokens are signed by a key that lives only in this process.
"""

import argparse
import itertools
import logging
import math
import os
import socket
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # run as a script from anywhere: the app and the evals import from the repo root
    sys.path.insert(0, str(ROOT))

from sqlalchemy import make_url  # noqa: E402

from app.config import DEFAULT_DATABASE_URL, LOCAL_HOSTS  # noqa: E402

FUNCTIONS = ("whoami", "log_food", "log_lifts", "get_log", "get_trends", "get_goals")
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
HOST = "127.0.0.1"
REQUEST_TIMEOUT = 60  # seconds: longer than every database limit, so a slow answer is measured, not cut off
START_TIMEOUT = 30
REDIRECTING_ENV = ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")


# ---------------------------------------------------------------- refusals

def address_problem(base_url: str) -> str | None:
    """Why the tool will not call this address, or None: only a local http address."""
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or (parsed.hostname or "") not in LOCAL_HOSTS:
        return "The load test only calls a server on this machine (127.0.0.1, localhost or ::1)."
    return None


def database_problem(database_url: str) -> str | None:
    """Why the tool will not use this database, or None: set, and on this machine."""
    if not database_url or database_url == DEFAULT_DATABASE_URL:
        return ("DATABASE_URL is not set. Point it at a local copy: a SQLite file in a temporary folder, or a "
                "Postgres on localhost.")
    try:
        url = make_url(database_url)
    except Exception:  # noqa: BLE001 - a malformed address is refused, never shown (it may hold a password)
        return "DATABASE_URL is not a database address."
    backend = url.get_backend_name()
    if backend == "sqlite":
        return None  # a file on this machine
    if backend != "postgresql":
        return f"The load test runs on SQLite or Postgres, not {backend}."
    # libpq lets these steer a connection away from the address's own host, so none may be in play.
    steering = [name for name in REDIRECTING_ENV if os.environ.get(name)]
    if steering or url.query.get("service") is not None:
        return ("A libpq setting could send the connection elsewhere (" + ", ".join(steering or ["service"])
                + "). Unset it and name the local host in DATABASE_URL.")
    hosts =[url.host, *_query_values(url.query.get("host")), *_query_values(url.query.get("hostaddr"))]
    named = [h for h in hosts if h]
    if not named:
        return "DATABASE_URL names no host. Name localhost, 127.0.0.1 or ::1 in it."
    if any(h.strip("[]").lower() not in LOCAL_HOSTS for h in named):
        return "DATABASE_URL is not on this machine. The load test never runs against a remote database."
    return None


def _query_values(value) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, tuple) else (value,)
    return [part for v in values for part in str(v).split(",")]


# ---------------------------------------------------------------- the numbers

def percentile(sorted_ms: list[float], share: float) -> float:
    """The nearest-rank percentile of an ascending list: share 0.5 is the median, 0.95 the 95th."""
    if not sorted_ms:
        return 0.0
    rank = max(1, math.ceil(share * len(sorted_ms)))
    return sorted_ms[rank - 1]


def table(results: dict[str, list[tuple[float, str | None]]], seconds: float) -> str:
    """The printed table: one line a function in FUNCTIONS order, then all of them together."""
    rows = [("function", "calls", "errors", "p50 ms", "p95 ms", "slowest ms")]
    everything: list[tuple[float, str | None]] = []
    for name in FUNCTIONS:
        calls = results.get(name, [])
        everything += calls
        rows.append(_row(name, calls))
    rows.append(_row("all", everything))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(cell.ljust(widths[i]) if i in (0, 2) else cell.rjust(widths[i]) for i, cell in enumerate(r))
             for r in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    rate = len(everything) / seconds if seconds > 0 else 0.0
    lines.append(f"{len(everything)} calls in {seconds:.1f} s: {rate:.1f} calls a second")
    return "\n".join(lines)


def _row(name: str, calls: list[tuple[float, str | None]]) -> tuple[str, ...]:
    times = sorted(ms for ms, _ in calls)
    errors = Counter(code for _, code in calls if code is not None)
    shown = ", ".join(f"{code} {n}" for code, n in sorted(errors.items())) or "none"
    return (name, str(len(calls)), shown, f"{percentile(times, 0.5):.0f}", f"{percentile(times, 0.95):.0f}",
            f"{times[-1] if times else 0:.0f}")


# ---------------------------------------------------------------- one person's loop

def arguments(name: str, turn: int) -> dict:
    """Each function's arguments. The numbers move with the turn, so the duplicate guard never answers
    instead of a real write."""
    if name == "log_food":
        return {"entries": [{"description": "Banana", "kcal": 80 + turn % 900}]}
    if name == "log_lifts":
        return {"sets": [{"exercise": "Back Squat", "weight_kg": 60 + (turn % 400) / 4, "reps": 5},
                         {"exercise": "Back Squat", "weight_kg": 60 + (turn % 400) / 4, "reps": 5}]}
    if name == "get_log":
        return {"days": 7}
    return {}


def outcome(response) -> str | None:
    """None for a call that worked; else its error code, or http_<status> or the exception's type."""
    if response.status_code != 200:
        return f"http_{response.status_code}"
    try:
        body = response.json()
    except ValueError:
        return "not_json"
    if "error" in body:
        return f"rpc_{body['error'].get('code')}"
    result = body.get("result") or {}
    if result.get("isError"):
        return str((result.get("structuredContent") or {}).get("error") or "is_error")
    return None


def person_loop(base_url: str, token: str, deadline: float, record) -> None:
    import httpx2

    headers = {**HEADERS, "Authorization": f"Bearer {token}"}
    ids = itertools.count(1)
    with httpx2.Client(base_url=base_url, headers=headers, timeout=REQUEST_TIMEOUT, trust_env=False) as client:
        for turn in itertools.count():
            for name in FUNCTIONS:
                if time.monotonic() >= deadline:
                    return
                body = {"jsonrpc": "2.0", "id": next(ids), "method": "tools/call",
                        "params": {"name": name, "arguments": arguments(name, turn)}}
                started = time.perf_counter()
                try:
                    code = outcome(client.post("/mcp", json=body))
                except Exception as exc:  # noqa: BLE001 - a failed request is a result, counted by its type
                    code = type(exc).__name__
                record(name, (time.perf_counter() - started) * 1000, code)


# ---------------------------------------------------------------- the server

class _Quiet(logging.Filter):
    """Drops the two expected start-up warnings: no web sign-in on this server, and the module-level app
    built from the environment has no MCP settings."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith(("Web sign-in disabled", "MCP endpoint disabled"))


def run(people: int, seconds: float, database_url: str) -> str:
    """Start the server, run the people, stop, clean up. Returns the table."""
    spotter_log = logging.getLogger("spotter")
    if not spotter_log.handlers:  # app.main adds its own only when none is there
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        handler.addFilter(_Quiet())
        spotter_log.addHandler(handler)
        spotter_log.setLevel(logging.ERROR)  # a refusal per call would bury the table; the table counts them
        spotter_log.propagate = False

    import uvicorn
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import delete

    from app import config, db, exercises
    from app.config import Settings
    from app.main import create_app
    from app.models import User
    from evals.issuer import DOMAIN, RESOURCE, FakeProvider, Key, claims, sign

    running = config.get_settings()
    settings = Settings(authkit_domain=DOMAIN, mcp_resource_url=RESOURCE, database_url=database_url,
                        signups_per_day=100_000, db_pool_size=running.db_pool_size,
                        db_max_overflow=running.db_max_overflow)
    config.use_settings(settings)
    alembic = Config(str(ROOT / "alembic.ini"))
    alembic.attributes["database_url"] = database_url
    alembic.attributes["skip_logging"] = True
    command.upgrade(alembic, "head")
    key = Key("load-key")
    provider = FakeProvider(key)
    app = create_app(settings, fetch=provider.fetch)
    with db.session_scope() as session:
        exercises.sync(session)

    stamp = int(time.time())
    user_ids, tokens = [], []
    now = db.utcnow()
    with db.session_scope() as session:
        for i in range(people):
            subject = f"user_load_{stamp}_{i:04d}"
            user = User(provider=settings.auth_provider, subject=subject, email=f"{subject}@example.com",
                        name="Load Person", time_zone="UTC", created_at=now, updated_at=now, last_seen_at=now)
            session.add(user)
            session.flush()
            user_ids.append(user.id)
            tokens.append(sign(key, claims(subject, email=f"{subject}@example.com", name="Load Person")))

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    base_url = f"http://{HOST}:{sock.getsockname()[1]}"
    problem = address_problem(base_url)
    if problem:  # never true: kept so the address the tool calls is checked, not assumed
        raise SystemExit(problem)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False, lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        _wait_for(base_url)
        results: dict[str, list[tuple[float, str | None]]] = defaultdict(list)
        lock = threading.Lock()

        def record(name: str, ms: float, code: str | None) -> None:
            with lock:
                results[name].append((ms, code))

        started = time.monotonic()
        deadline = started + seconds
        workers = [threading.Thread(target=person_loop, args=(base_url, t, deadline, record), daemon=True)
                   for t in tokens]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        took = time.monotonic() - started
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        try:
            with db.session_scope() as session:  # every row of theirs goes with them (ON DELETE CASCADE)
                session.execute(delete(User).where(User.id.in_(user_ids)))
        finally:
            db.dispose()
    return table(results, took)


def _wait_for(base_url: str) -> None:
    import httpx2

    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if httpx2.get(f"{base_url}/health", timeout=2, trust_env=False).status_code == 200:
                return
        except httpx2.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError("the local server did not start")


# ---------------------------------------------------------------- main

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python tools/load.py",
                                     description="Load Spotter on this machine with throwaway people.")
    parser.add_argument("--people", type=int, default=10, help="people calling at once (default 10)")
    parser.add_argument("--seconds", type=float, default=30, help="how long they keep calling (default 30)")
    args = parser.parse_args(argv)
    if not 1 <= args.people <= 500:
        parser.error("--people is 1 to 500")
    if not 0 < args.seconds <= 3600:
        parser.error("--seconds is more than 0 and at most 3600")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    from app.config import load_settings

    database_url = load_settings().database_url  # DATABASE_URL, read where every setting is read
    problem = database_problem(database_url)
    if problem:
        print(problem)
        return 2
    backend = make_url(database_url).get_backend_name()
    print(f"{args.people} people for {args.seconds:g} s on {backend}, server on {HOST}", flush=True)
    print(run(args.people, args.seconds, database_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
