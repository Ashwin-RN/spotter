# Spotter: how this repo is built

Spotter is a personal fitness record kept by talking to an AI assistant over MCP, with a small web app on
the same account. Python 3.12, Starlette, SQLAlchemy, Alembic, the `mcp` library, Postgres live and SQLite
in tests. The README says what it does; this file says how changes are made.

## Map

| Path | What |
|---|---|
| `app/mcp_server.py`, `app/mcp_schemas.py` | The MCP functions, their descriptions and result shapes: what an assistant reads |
| `app/services/` | The work behind each function and page. Numbers are worked out here, never by the assistant |
| `app/web.py`, `app/templates/`, `app/static/` | The web pages |
| `app/config.py` | The only module that reads the environment |
| `migrations/versions/` | Alembic migrations, numbered `0001` up |
| `tests/` | pytest. `tests/conftest.py` builds the app on a throwaway SQLite file |
| `evals/` | Claude, Codex or Gemini against a local server (`--client`). `python -m evals.run`, and `python -m evals.need` says whether a run is needed |
| `docs/can-and-cant.md` | Served to the assistant as a resource: model-read text |
| `docs/evals.md` | How the evals run |
| `docs/domain-move.md` | The steps to move the site to its own domain |
| `docs/contracts/` | One contract per stage or item, fixed before the build. A record once built: a later change adds a dated note rather than rewriting it |
| `BUILD_LOG.md` | One entry per stage or item: the build, the reviews, the checks, the eval run |
| `tools/` | `probe.py` checks a live server from outside, `mcp_call.py` calls one function, `load.py` is a local load test |

## Four kinds of change and their checks

| Kind | Example | Check |
|---|---|---|
| Docs only | README, a contract, BUILD_LOG | The docs tests |
| Code | A service, a page | The test files for the code touched, plain and with `-W error`. The whole suite runs by hand before the push |
| Model-read text | A function's description, the server's instructions, `docs/can-and-cant.md`, text inside a result such as a note or a fix line | As code, and `python -m evals.need main` says yes: an eval run is owed at the release |
| Migration | A new table or column | As code, and one run on a local Postgres: up, down, up, over a written row |

Tests: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider <files>`. Never run app code from the repo
root without a database set: it leaves a `spotter.db` that fails the schema test.

## How an item is built

1. **Contract.** `docs/contracts/<name>.md`, read against the code as it is now: decisions, what exists,
   what changes, the files of each package, the tests, the eval cases, the exit check.
2. **Build.** One `builder` agent per package. It touches only its package's files, runs its own test
   files, and never commits.
3. **Review.** A separate `reviewer` agent on a frozen copy (`git archive` of a tree), never the live tree.
   It changes nothing and confirms each finding with a probe.
4. **Fix round.** One. A fresh re-check only when the review had a must fix; otherwise the fix diff is read.
5. **Commit** on a quiet tree, one item a commit, files added by name. Plain messages: no co-author or
   tool lines. The tests before a push are below.

Items that touch the same files are built one after another. Independent items may build in parallel,
each in its own git worktree, and are joined after.

## Release

1. The items are committed on a branch.
2. One eval run for the whole release, when `python -m evals.need main` says yes. Its cost is said first.
3. The tests before a push, below, on the exact commit. Merge to `main` as a fast-forward and push.
   Railway deploys `main`: migrations run before the new build serves.
4. Live check: `/health` 200, `/mcp` 401 with no token, `/` 200, `/ready` 200 (the database is at the
   code's migration head), and a fresh assistant session (a running one keeps the function list it
   started with).

Removing a required result field or a column takes two deploys: optional first, removed later. A client
holding the old function list breaks otherwise.

## Tests before a push

There is no commit hook for now. Before a push, the test files of the files touched run plain and with
`-W error`, then the whole suite once on a clean export of the exact commit (`git archive` of it). A red
run is never pushed. The hook is rebuilt once the code is stable.

## Text rules

For every page, every result an assistant relays, the README and the docs:

- No em dashes and no AI-isms. Plain words, short lines.
- Numbers as numbers. No needless decimals. Comments go in their own column.
- Spotter's numbers are quoted, never estimated, except a food's kcal marked as an estimate.
- The server's instructions stay under 4,096 characters.

## Code rules

- No new package without the owner's okay. Outside calls use `urllib`.
- Tests never touch the network: replace the outside world at a seam the code provides.
- Never print, log or render a secret.
- No test depends on the real date, weekday or hour.
