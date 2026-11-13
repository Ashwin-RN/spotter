"""The safety work (docs/contracts/safety.md): the Postgres pool and its query limits, what a time-out looks
like, the read limit through MCP, the page limit on the web, the size limit on every request, the signup
cap left as it was, and the load tool's refusals.

Nothing here connects to Postgres: the engine is built on a Postgres address and its first connection is
stopped at SQLAlchemy's do_connect hook, before the driver opens a socket.
"""

import asyncio
import importlib.util
import json
from datetime import timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import InternalError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app import config, db, mcp_server, services, web, web_auth
from app.config import (
    DEFAULT_DB_MAX_OVERFLOW, DEFAULT_DB_POOL_SIZE, DEFAULT_SIGNUPS_PER_DAY, ROOT, load_settings,
)
from app.main import MAX_BODY_BYTES, TOO_LARGE, BodyLimit
from app.models import CallLog
from app.services.common import READ_LIMIT, READS_LIMIT, check_reads

from .conftest import HEADERS, RESOURCE, rpc, settings
from .test_pages_text import Rendered, error_page_findings, parse
from .test_web import client, csrf_of, only_user, seam, sign_in, web_client  # noqa: F401 - fixtures

POSTGRES = "postgresql+psycopg://spotter:secret@db.example.invalid:5432/spotter"
BASE = {"AUTHKIT_DOMAIN": "example.authkit.app", "MCP_RESOURCE_URL": RESOURCE}
SUBJECT = "user_01TEST"


class Stopped(Exception):
    """Raised at do_connect, so the engine never reaches the driver."""


def connect_params(engine) -> dict:
    """The keyword arguments the engine would hand psycopg.connect, without connecting."""
    seen: dict = {}

    @event.listens_for(engine, "do_connect")
    def _stop(dialect, record, cargs, cparams):
        seen.update(cparams)
        raise Stopped

    with pytest.raises(Stopped):
        engine.connect()
    return seen


# ---------------------------------------------------------------- 1. the pool

def test_the_pool_settings_default_to_5_and_5():
    s = load_settings(BASE)
    assert (s.db_pool_size, s.db_max_overflow) == (5, 5) == (DEFAULT_DB_POOL_SIZE, DEFAULT_DB_MAX_OVERFLOW)


def test_the_pool_settings_are_read_within_their_bounds():
    for size, overflow in (("1", "0"), ("50", "50"), (" 8 ", "2")):
        s = load_settings({**BASE, "DB_POOL_SIZE": size, "DB_MAX_OVERFLOW": overflow})
        assert (s.db_pool_size, s.db_max_overflow) == (int(size), int(overflow))


@pytest.mark.parametrize("value", ["", "0", "51", "-1", "+5", "2.5", "five", "１０", "1e2"])
def test_a_pool_size_that_is_not_a_whole_number_from_1_to_50_falls_back(value):
    assert load_settings({**BASE, "DB_POOL_SIZE": value}).db_pool_size == DEFAULT_DB_POOL_SIZE


@pytest.mark.parametrize("value", ["", "51", "-1", "2.5", "many"])
def test_an_overflow_that_is_not_a_whole_number_from_0_to_50_falls_back(value):
    assert load_settings({**BASE, "DB_MAX_OVERFLOW": value}).db_max_overflow == DEFAULT_DB_MAX_OVERFLOW


def test_a_postgres_engine_has_the_pool_numbers():
    engine = db.make_engine(POSTGRES)
    try:
        pool = engine.pool
        assert (pool.size(), pool._max_overflow, pool._timeout, pool._recycle) == (5, 5, 10, 1800)
        assert pool._pre_ping is True
    finally:
        engine.dispose()


def test_the_running_engine_takes_the_pool_numbers_from_the_settings(app_settings):
    config.use_settings(settings(database_url=POSTGRES, db_pool_size=8, db_max_overflow=0))
    try:
        pool = db.get_engine().pool
        assert (pool.size(), pool._max_overflow) == (8, 0)
    finally:
        db.dispose()
        config.use_settings(app_settings)


def test_sqlite_keeps_sqlalchemys_own_pool_and_no_limits(tmp_path):
    engine = db.make_engine(f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    try:
        pool = engine.pool
        assert (pool._max_overflow, pool._timeout, pool._recycle) == (10, 30, -1)  # SQLAlchemy's defaults
        params = connect_params(engine)
        assert "options" not in params and "connect_timeout" not in params
    finally:
        engine.dispose()


# ---------------------------------------------------------------- 2. the query limits

def test_every_postgres_connection_starts_with_the_three_limits_and_a_connect_timeout():
    engine = db.make_engine(POSTGRES)
    try:
        params = connect_params(engine)
    finally:
        engine.dispose()
    assert params["connect_timeout"] == 5
    assert params["options"] == ("-c statement_timeout=15000 -c lock_timeout=10000 "
                                 "-c idle_in_transaction_session_timeout=60000")
    assert params["host"] == "db.example.invalid"  # the address's own values are untouched


def test_the_startup_options_are_a_conninfo_psycopg_accepts():
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    built = conninfo_to_dict(make_conninfo("host=localhost", options=db.STARTUP_OPTIONS, connect_timeout=5))
    assert built["options"] == db.STARTUP_OPTIONS and built["connect_timeout"] == "5"


# ---------------------------------------------------------------- 3. a time-out is "the database is busy"

TIMEOUTS = [
    PoolTimeout("QueuePool limit of size 5 overflow 5 reached, connection timed out, timeout 10.00"),
    OperationalError("SELECT 1", {}, Exception("canceling statement due to statement timeout")),
    OperationalError("SELECT 1", {}, Exception("canceling statement due to lock timeout")),
    InternalError("SELECT 1", {}, Exception("terminating connection due to idle-in-transaction timeout")),
]
TIMEOUT_IDS = ["no free connection", "statement timeout", "lock timeout", "idle in transaction"]


@pytest.mark.parametrize("error", TIMEOUTS, ids=TIMEOUT_IDS)
def test_a_time_out_through_mcp_is_database_unavailable(http, make_user, token_for, monkeypatch, error):
    make_user(subject=SUBJECT, email="asha@example.com", name="Asha", time_zone="UTC")

    def busy(*args, **kwargs):
        raise error

    monkeypatch.setattr(services, "get_log", busy)
    r = rpc(http, "tools/call", {"name": "get_log", "arguments": {}}, token=token_for(SUBJECT))
    result = r.json()["result"]
    assert result["isError"] is True and result["structuredContent"] == mcp_server.database_unavailable()


@pytest.mark.parametrize("error", TIMEOUTS, ids=TIMEOUT_IDS)
def test_a_time_out_on_a_page_is_the_database_page_not_a_500(client, monkeypatch, error):
    sign_in(client)

    def busy(*args, **kwargs):
        raise error

    monkeypatch.setattr(web, "_load_user", busy)
    r = client.get("/settings")
    assert r.status_code == 503 and "cannot reach its database" in r.text
    assert "QueuePool" not in r.text and "canceling" not in r.text


def test_the_web_and_mcp_treat_the_same_errors_as_the_database_being_down():
    assert set(web.DATABASE_DOWN) == set(mcp_server.DATABASE_DOWN)
    assert PoolTimeout in web.DATABASE_DOWN


# ---------------------------------------------------------------- 4. the read limit through MCP

def plant(user_id: int, functions, count: int, minutes_ago: float = 4) -> None:
    """count call-log rows of these functions (taken in turn), started minutes_ago."""
    started = db.utcnow() - timedelta(minutes=minutes_ago)
    with db.session_scope() as s:
        s.add_all(CallLog(function=functions[i % len(functions)], started_at=started, ms=1, ok=True,
                          error_code=None, result_bytes=1, user_id=user_id) for i in range(count))


@pytest.fixture
def person(make_user):
    return make_user(subject=SUBJECT, email="asha@example.com", name="Asha", time_zone="UTC")


def call(http, token: str, name: str, arguments: dict | None = None) -> dict:
    r = rpc(http, "tools/call", {"name": name, "arguments": arguments or {}}, token=token)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_the_read_functions_are_every_read_level_function():
    assert set(mcp_server.READ_FUNCTIONS) == {n for n, level in mcp_server.NEEDS.items() if level == "read"}
    assert {"whoami", "get_log", "get_trends", "get_goals", "find_food", "list_exercises",
            "get_progress", "owner_calls"} == set(mcp_server.READ_FUNCTIONS)  # owner_calls: trace.md decision 16
    assert READS_LIMIT == 300 and READ_LIMIT == 120


def test_the_301st_read_in_10_minutes_is_too_many_reads_and_its_work_never_runs(http, person, token_for, monkeypatch):
    plant(person.id, mcp_server.READ_FUNCTIONS, READS_LIMIT, minutes_ago=4)

    def never(*args, **kwargs):
        raise AssertionError("the read ran past the limit")

    monkeypatch.setattr(services, "get_log", never)
    result = call(http, token_for(SUBJECT), "get_log")
    out = result["structuredContent"]
    assert result["isError"] is True and out["error"] == "too_many_reads"
    assert out["wait_minutes"] == 6 and out["fix"] == "Wait 6 minutes, then ask again."
    assert "301 read calls in the last 10 minutes" in out["message"]
    with db.session_scope() as s:
        last = s.scalars(select(CallLog).order_by(CallLog.id.desc())).first()
    assert (last.function, last.ok, last.error_code) == ("get_log", False, "too_many_reads")


def test_the_300th_read_is_allowed(http, person, token_for):
    plant(person.id, mcp_server.READ_FUNCTIONS, READS_LIMIT - 1)
    result = call(http, token_for(SUBJECT), "get_trends")
    assert result["isError"] is False, result


def test_every_read_counts_together_and_the_limit_holds_for_each(http, person, token_for):
    plant(person.id, ("whoami",), 150)
    plant(person.id, ("get_goals",), 150)
    token = token_for(SUBJECT)
    for name in ("whoami", "get_progress", "list_exercises", "get_goals"):
        assert call(http, token, name)["structuredContent"]["error"] == "too_many_reads", name


def test_writes_do_not_count_as_reads_and_are_not_held_by_the_read_limit(http, person, token_for):
    token = token_for(SUBJECT)
    plant(person.id, ("log_food", "log_bodyweight"), 100)
    assert call(http, token, "get_log")["isError"] is False
    plant(person.id, ("get_log",), READS_LIMIT + 5)
    result = call(http, token, "log_bodyweight", {"kg": 80})
    assert result["isError"] is False, result


def test_old_reads_and_other_peoples_reads_do_not_count(http, person, make_user, token_for):
    other = make_user(subject="user_02OTHER", email="b@example.com", name="B", time_zone="UTC")
    plant(person.id, ("get_log",), READS_LIMIT + 10, minutes_ago=11)
    plant(other.id, ("get_log",), READS_LIMIT + 10)
    assert call(http, token_for(SUBJECT), "get_log")["isError"] is False


def test_find_food_keeps_its_own_120_beside_the_300(http, person, token_for):
    plant(person.id, ("find_food",), READ_LIMIT)  # 120 + this call: past find_food's own, far under the 300
    out = call(http, token_for(SUBJECT), "find_food", {"query": "dal"})["structuredContent"]
    assert out["error"] == "too_many_reads" and "find_food calls" in out["message"]


def test_check_reads_counts_only_these_functions_and_says_the_wait(person):
    plant(person.id, ("get_log",), READS_LIMIT + 1, minutes_ago=9.5)
    out = check_reads(person.id, ("get_log",))
    assert out["error"] == "too_many_reads" and out["wait_minutes"] == 1
    assert out["fix"] == "Wait 1 minute, then ask again."
    assert check_reads(person.id, ("whoami",)) is None


# ---------------------------------------------------------------- 5. the page limit on the web

class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(web, "page_clock", fake)
    return fake


@pytest.fixture
def small_limit(monkeypatch):
    monkeypatch.setattr(web, "PAGE_LIMIT", 3)


def test_the_page_limit_is_300_in_10_minutes():
    assert (web.PAGE_LIMIT, web.PAGE_WINDOW_MINUTES) == (300, 10)


def test_past_the_limit_a_signed_in_page_load_is_the_too_many_page_loads_page(client, clock, small_limit):
    sign_in(client)  # /sign-in and /auth/callback: signed-out pages, not counted
    for _ in range(3):
        clock.now += 60
        assert client.get("/settings").status_code == 200
    clock.now += 60  # 180 s after the first counted load at 1060: it leaves the window in 420 s
    r = client.get("/progress")
    assert r.status_code == 429 and r.headers["retry-after"] == "420"
    assert [n.text() for n in parse(r.text).find_all("h1")] == ["Too many page loads"]
    assert "Wait 7 minutes, then reload." in r.text
    findings = error_page_findings(Rendered("/progress", r, True, user_id=only_user().id))
    assert not findings, "\n".join(findings)


def test_the_load_after_the_oldest_leaves_the_window_is_served(client, clock, small_limit):
    sign_in(client)
    for _ in range(3):
        assert client.get("/settings").status_code == 200
    assert client.get("/settings").status_code == 429
    clock.now += web.PAGE_WINDOW_MINUTES * 60
    assert client.get("/settings").status_code == 200


def test_a_refused_load_is_not_counted(client, clock, small_limit):
    sign_in(client)
    for _ in range(3):
        assert client.get("/settings").status_code == 200
        clock.now += 100
    for _ in range(5):
        assert client.get("/settings").status_code == 429
    clock.now = 1000 + 600 + 1  # the first load has left; the refused ones never counted
    assert client.get("/settings").status_code == 200


def test_signed_out_pages_health_and_posts_are_not_counted(client, clock, small_limit):
    for _ in range(5):
        assert client.get("/settings").status_code == 303  # signed out: on to sign-in, not counted
    sign_in(client)
    token = csrf_of(client)  # one counted load
    for _ in range(5):
        assert client.get("/").status_code == 200
        assert client.get("/health").text == "OK"
        assert client.get("/sign-in").status_code == 303
    for _ in range(5):
        r = client.post("/settings", data={"time_zone": "UTC", "csrf_token": token})
        assert r.status_code in (200, 303), r.status_code
    assert client.get("/settings").status_code == 200
    assert client.get("/settings").status_code == 200
    assert client.get("/settings").status_code == 429


def test_a_head_request_is_a_page_load(client, clock, small_limit):
    sign_in(client)
    for _ in range(3):
        assert client.head("/settings").status_code == 200
    assert client.get("/settings").status_code == 429


def test_the_count_is_per_person(client, clock, small_limit, seam):
    sign_in(client)
    for _ in range(3):
        client.get("/settings")
    assert client.get("/settings").status_code == 429
    client.cookies.clear()
    seam.identity = web_auth.Identity("user_02WEB", "ben@example.com", "Ben Web", "session_02XYZ")
    sign_in(client)
    assert client.get("/settings").status_code == 200


def test_each_app_keeps_its_own_count(web_client, clock, small_limit):
    first = web_client()
    sign_in(first)
    for _ in range(3):
        first.get("/settings")
    assert first.get("/settings").status_code == 429
    second = web_client()
    sign_in(second)
    assert second.get("/settings").status_code == 200


# ---------------------------------------------------------------- 6. the size limit

def test_the_limit_is_one_mib():
    assert MAX_BODY_BYTES == 1024 * 1024
    assert TOO_LARGE == "That request is larger than Spotter takes (1 MiB)."


def test_a_body_over_the_limit_by_its_length_is_413_and_never_reaches_mcp(http, person, token_for):
    body = b"x" * (MAX_BODY_BYTES + 1)
    r = http.post("/mcp", content=body, headers={**HEADERS, "Authorization": f"Bearer {token_for(SUBJECT)}"})
    assert r.status_code == 413 and r.text == TOO_LARGE
    assert r.headers["content-type"].startswith("text/plain")
    with db.session_scope() as s:
        assert s.scalars(select(CallLog)).first() is None


def test_exactly_the_limit_passes_and_one_byte_more_does_not(http):
    assert http.post("/health", content=b"x" * MAX_BODY_BYTES).status_code == 404  # reached the app: no such route
    assert http.post("/health", content=b"x" * (MAX_BODY_BYTES + 1)).status_code == 413


def test_a_streamed_body_with_no_length_is_cut_off_when_it_passes_the_limit(http, person, token_for):
    def chunks():
        for _ in range(40):  # 40 chunks of 64 KiB: 2.5 MiB, sent chunked with no length
            yield b"y" * 65536

    r = http.post("/mcp", content=chunks(),
                  headers={**HEADERS, "Authorization": f"Bearer {token_for(SUBJECT)}"})
    assert "content-length" not in r.request.headers and r.request.headers["transfer-encoding"] == "chunked"
    assert r.status_code == 413 and r.text == TOO_LARGE
    with db.session_scope() as s:
        assert s.scalars(select(CallLog)).first() is None


def test_a_streamed_body_under_the_limit_reaches_the_app(http):
    def chunks():
        yield b"x" * 1000
        yield b"x" * 1000

    assert http.post("/health", content=chunks()).status_code == 404  # reached the app


def run_body_limit(app, chunks: list[bytes]) -> list[dict]:
    """BodyLimit around `app`, fed these body chunks with no Content-Length; the messages it sends."""
    queue = list(chunks)
    sent: list[dict] = []

    async def receive():
        if not queue:
            return {"type": "http.disconnect"}
        body = queue.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(queue)}

    async def send(message):
        sent.append(message)

    asyncio.run(BodyLimit(app)({"type": "http", "method": "POST", "path": "/", "headers": []}, receive, send))
    return sent


def test_when_the_body_passes_the_limit_part_way_the_413_is_the_only_answer():
    async def app(scope, receive, send):  # reads to the end, swallows the refusal, then answers anyway
        try:
            while (await receive()).get("more_body"):
                pass
        except Exception:  # noqa: BLE001
            pass
        await send({"type": "http.response.start", "status": 400, "headers": []})
        await send({"type": "http.response.body", "body": b"bad"})

    sent = run_body_limit(app, [b"a" * 600_000, b"b" * 600_000, b"c"])
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]
    assert [m["body"] for m in sent if m["type"] == "http.response.body"] == [TOO_LARGE.encode()]


def test_an_app_that_fails_on_the_cut_body_raises_nothing_more():
    async def app(scope, receive, send):
        while (await receive()).get("more_body"):
            pass
        raise AssertionError("never reached: the receive raised first")

    sent = run_body_limit(app, [b"a" * 600_000, b"b" * 600_000])
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]


def test_a_body_under_the_limit_in_chunks_is_passed_through_whole():
    got = []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            got.append(message["body"])
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent = run_body_limit(app, [b"a" * 500_000, b"b" * 500_000, b"c" * (MAX_BODY_BYTES - 1_000_000)])
    assert sum(len(b) for b in got) == MAX_BODY_BYTES
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [200]


def test_a_page_form_over_the_limit_is_413(client):
    sign_in(client)
    token = csrf_of(client)
    r = client.post("/settings", data={"time_zone": "UTC", "csrf_token": token, "pad": "z" * MAX_BODY_BYTES})
    assert r.status_code == 413 and r.text == TOO_LARGE


def test_the_limit_holds_when_mcp_is_not_configured(make_client):
    off = make_client(authkit_domain=None)
    assert off.post("/mcp", content=b"x" * (MAX_BODY_BYTES + 1)).status_code == 413


def import_body(rows: int = 200) -> bytes:
    """The largest real body: an import_entries call of 200 food rows of 5 foods each, the 1,000 foods a
    call may hold, named as a food app's export names them."""
    foods = ("Chicken breast, grilled, skinless", "Basmati rice, cooked", "Greek yoghurt, plain, 2% fat",
             "Banana, medium", "Almonds, raw, unsalted")
    meals = ("breakfast", "lunch", "dinner", "snack", "snack")
    data = [{"kind": "food_entry", "date": f"2026-{1 + i // 28 % 10:02d}-{1 + i % 28:02d}",
             "entries": [{"description": foods[j], "kcal": 120.5 + 37 * j, "meal": meals[j]} for j in range(5)]}
            for i in range(rows)]
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "import_entries", "arguments": {"rows": data, "dry_run": True}}}
    return json.dumps(payload).encode("utf-8")


def test_a_200_row_import_is_under_a_quarter_of_the_limit_and_goes_through(http, person, token_for):
    body = import_body()
    assert len(body) < MAX_BODY_BYTES // 4, len(body)
    r = http.post("/mcp", content=body, headers={**HEADERS, "Authorization": f"Bearer {token_for(SUBJECT)}"})
    assert r.status_code == 200, r.text[:300]
    result = r.json()["result"]
    assert result["isError"] is False, result["structuredContent"]


# ---------------------------------------------------------------- 7. new accounts unchanged

def test_new_accounts_are_50_a_day_unless_signups_per_day_says_otherwise():
    assert load_settings(BASE).signups_per_day == DEFAULT_SIGNUPS_PER_DAY == 50
    assert load_settings({**BASE, "SIGNUPS_PER_DAY": "12"}).signups_per_day == 12


# ---------------------------------------------------------------- 8. the load tool's refusals

def load_tool():
    spec = importlib.util.spec_from_file_location("load_tool", ROOT / "tools" / "load.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("url", [
    "sqlite:///C:/temp/load.db",
    "postgresql+psycopg://u:p@localhost:5432/spotter",
    "postgresql+psycopg://u:p@127.0.0.1/spotter",
    "postgresql+psycopg://u:p@[::1]:5432/spotter",
])
def test_the_load_tool_takes_a_local_database(url):
    assert load_tool().database_problem(url) is None


@pytest.mark.parametrize("url", [
    POSTGRES,
    "postgresql+psycopg://u:p@db.railway.internal:5432/railway",
    "postgresql+psycopg://u:p@/spotter",  # no host: libpq would read PGHOST
    "postgresql+psycopg://u:p@localhost/spotter?host=db.example.invalid",
    "mysql://u:p@localhost/spotter",
    config.DEFAULT_DATABASE_URL,  # unset: the repo's own file
    "",
])
def test_the_load_tool_refuses_a_database_that_is_not_local_or_not_set(url):
    problem = load_tool().database_problem(url)
    assert problem and "secret" not in problem and "u:p" not in problem


@pytest.mark.parametrize("name", ["PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_the_load_tool_refuses_when_libpq_could_be_steered_elsewhere(monkeypatch, name):
    monkeypatch.setenv(name, "db.example.invalid")
    problem = load_tool().database_problem("postgresql+psycopg://u:p@localhost:5432/spotter")
    assert problem and name in problem and "u:p" not in problem


def test_the_load_tool_refuses_a_service_in_the_address(monkeypatch):
    for name in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    assert load_tool().database_problem("postgresql+psycopg://u:p@localhost/spotter?service=prod")


def test_the_load_tool_only_calls_this_machine():
    tool = load_tool()
    assert tool.address_problem("http://127.0.0.1:8123") is None
    assert tool.address_problem("http://localhost:8123") is None
    assert tool.address_problem("https://spotter.example.app") is not None
    assert tool.address_problem("http://10.0.0.5:8000") is not None


def test_the_load_tool_prints_each_function_and_all():
    tool = load_tool()
    results = {"whoami": [(10.0, None), (30.0, None)], "log_food": [(5.0, None), (50.0, "too_many_writes")]}
    printed = tool.table(results, 2.0)
    lines = printed.splitlines()
    assert lines[0].split() == ["function", "calls", "errors", "p50", "ms", "p95", "ms", "slowest", "ms"]
    assert [line.split()[0] for line in lines[2:9]] == [*tool.FUNCTIONS, "all"]
    assert "too_many_writes 1" in printed and lines[-1] == "4 calls in 2.0 s: 2.0 calls a second"
    assert tool.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.5) == 5
    assert tool.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 10
