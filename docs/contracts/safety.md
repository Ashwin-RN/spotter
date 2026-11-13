# Safety contract: before strangers arrive

**Goal: when people other than the owner start using Spotter, one busy person, one runaway script or one slow query cannot take the site down for everyone, and we know roughly how many people at once it holds.**

What exists before this work, read in the code on `d72602f`:

| Piece | State |
|---|---|
| One write at a time per person | `lock_user` takes the person's row first in every write (`app/services/common.py`) |
| Duplicate guard | The same entry within 10 minutes is not written twice |
| Write limit | 120 write calls in 10 minutes per person, web and assistant together |
| `find_food` throttle | 120 calls in 10 minutes per person |
| New accounts | At most `SIGNUPS_PER_DAY` a day, 50 when unset (`app/users.py`, `app/config.py`) |
| Database work off the event loop | MCP and the web run it in worker threads |
| Connection pool | SQLAlchemy's default: 5 kept, 10 more on demand, 30 s wait. `pool_pre_ping` on |
| Query time | No limit |
| Reads through MCP | Only `find_food` is limited |
| Page loads | No limit |
| Request size | No limit |
| Capacity | Never measured |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **The pool.** On Postgres only: `pool_size` 5, `max_overflow` 5, `pool_timeout` 10 s, `pool_recycle` 1,800 s, `pool_pre_ping` kept. `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` override the first two; a value that is not a whole number from 1 to 50 (pool) or 0 to 50 (overflow) falls back to the default. Only `app/config.py` reads them. SQLite is unchanged | Ten connections a process, well under a stock Postgres's 100. A spike waits 10 s for a connection, then gets a clear error, not a hang |
| 2 | **Query limits.** On Postgres only, every connection starts with `statement_timeout` 15 s, `lock_timeout` 10 s and `idle_in_transaction_session_timeout` 60 s, set through the connection's startup `options`, so a rollback cannot undo them. `connect_timeout` 5 s. Fixed constants in `app/db.py`, not settings | One slow query or a stuck transaction frees its connection. The export, the heaviest read, is far under 15 s on a full account (the builder measures it on SQLite and records the number) |
| 3 | **What a timeout looks like.** A query cancelled by a limit, a lock wait that runs out, or no free connection in 10 s is "the database is busy": MCP answers `database_unavailable` as now, and the web shows its existing database page. The web's `DATABASE_DOWN` gains SQLAlchemy's pool `TimeoutError`, which MCP already has | Today a pool time-out on a page is a 500. The person gets the same "try again" either way |
| 4 | **A read limit through MCP.** Every function at the `read` level of `NEEDS` counts together: more than 300 such calls by one person in the last 10 minutes is `too_many_reads`, with `wait_minutes`, counted from the call-log rows the way the write limit counts. `find_food` keeps its own 120 as well. Checked before the work runs; nothing is locked | A conversation makes a handful of reads. 300 in 10 minutes is only a loop |
| 5 | **A page limit on the web.** More than 300 page loads (GET requests to the signed-in pages) by one person in 10 minutes shows a "Too many page loads" message page with how long to wait, status 429. Counted in memory, per process, keyed by the person's id; signed-out pages and `/health` are not counted. Writes keep their write limit | Page reads write no call-log row, so the count lives in memory. One server process today, so one count |
| 6 | **A size limit on every request.** A body over 1 MiB is refused with 413 before the app reads it: by its `Content-Length`, or, with no length, when the streamed body passes 1 MiB. One pure ASGI wrapper around the whole app in `app/main.py` | The largest real body is an `import_entries` call of 200 rows. The builder measures one, records its size, and it must be under a quarter of the limit |
| 7 | **New accounts unchanged.** 50 a day unless `SIGNUPS_PER_DAY` says otherwise | It exists and works. The owner sets it on the host |
| 8 | **A load test, local only.** `tools/load.py` starts the app in-process on 127.0.0.1 with the eval's fake provider (`evals/issuer.py`), makes N throwaway people with signed tokens, and has them all call MCP at once: `whoami`, `log_food`, `log_lifts`, `get_log`, `get_trends`, `get_goals`, in a loop for a set time. It prints, for each function and in all: calls, errors by code, p50, p95 and the slowest, in ms. It takes `--people` (default 10), `--seconds` (default 30) and uses `DATABASE_URL`. It refuses to run against anything but a local address and refuses a database URL whose host is not local. No new package | We learn where it bends, on a copy, never on the live site |
| 9 | **What the load test is not.** It is a tool, not a test in the suite, and its numbers are not asserted. One run on SQLite proves it works; the run that counts is on a local Postgres, recorded in `BUILD_LOG.md` with the machine it ran on | A laptop is not the host. The numbers show the shape, not the host's ceiling |

## Errors added

| Code | When | Fix text, in the spirit of the existing ones |
|---|---|---|
| `too_many_reads` (now from any read) | More than 300 reads in 10 minutes | "Wait N minutes, then ask again." |
| Page: "Too many page loads" | More than 300 page loads in 10 minutes | "Wait N minutes, then reload." |
| 413 | A request body over 1 MiB | A plain line: "That request is larger than Spotter takes (1 MiB)." |

## Work package: one builder

| Package | Files | Exit check |
|---|---|---|
| S1 | `app/config.py`, `app/db.py`, `app/main.py`, `app/mcp_server.py`, `app/services/common.py`, `app/web.py`, the template for the new message page if the message pages need one, `tools/load.py`, `tests/test_safety.py` (new). Existing test files only where an existing assertion now fails because of a decision above, each such change named in the report | Every decision above has a test, except 2's Postgres settings (a test checks the engine is built with the options string and the pool numbers on a Postgres URL, without connecting) and 8 (one SQLite run of `tools/load.py --people 5 --seconds 10`, its printed table in the report). The 200-row import's body size and the export's time on the sample account, in the report. The tests for the files touched pass, plain and with `-W error` |

Not in this work: the docs (`README.md`, `docs/can-and-cant.md`, `BUILD_LOG.md`), done after the review; the Postgres load run, done by the lead; anything on the host (its plan, its backups, its start command), which is the owner's.
