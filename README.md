# Spotter

A personal fitness record you keep by talking to Claude.

You talk to Claude. Claude logs through Spotter over MCP. Spotter stores it and works out every number in code: bests, trends, calorie estimates, the pace a goal needs. Claude quotes the numbers; it never invents them.

**Status: stages 1 to 4 are live; stage 5 (goals of every kind, trends, export, delete account, sample data) is being built.**

Live: https://spotter-production-0c0f.up.railway.app. The landing page carries the setup steps: sign in, connect Claude, start talking.

## What it does today

| You say | What happens |
|---|---|
| "Back squat, 3 sets of 5 at 80 kg" | One `log_lifts` call; new bests worked out against every earlier set; the same message again within 10 minutes is a duplicate, nothing written |
| "Weighed 79.4 this morning" | One weigh-in a day; the 7-day average and the trend |
| "Lunch: two rotis and dal" | One `log_food` call for the whole meal; every number tagged with who supplied it: you, a saved food, the USDA table, or Claude's estimate; the meal slot from your own clock, never asked |
| "30 minutes on the treadmill" | A calorie estimate from the 2024 Compendium MET and your body weight, shown beside the machine's number |
| "Squat 100 by June" | A goal with a start, a target, the pace it needs and the pace so far |

Sixteen MCP functions, a web app on the same account (Today, Progress, Foods, Settings), and a switch on Settings that says what Claude may do: read, log, or log and delete. Nothing is ever erased: every delete can be restored.

## How it is built

- Python 3.12, Starlette, SQLAlchemy, Alembic, the `mcp` library (streamable HTTP, stateless); Postgres; sign-in through an outside provider (WorkOS AuthKit) for the web and for Claude, so both reach one account.
- Every write locks the person's row first, counts against a write limit, and passes the same checks whether it came from Claude or a form.
- 1,287 tests, run plain and with warnings as errors on a clean export before every push.
- Evals: `claude -p` against a local copy with a throwaway person; the server records every call it received, and the eval judges that, not the model's own account of what it did. See `docs/evals.md`.

## Where to read more

| File | What it is |
|---|---|
| `BUILD_LOG.md` | How each stage was built: the first-use path, the packages, the reviews and what they found, the live exit test, what the stage taught |
| `docs/contracts/` | What each stage had to do, fixed before the builders started |
| `docs/can-and-cant.md` | What Spotter records, what its numbers mean, and what it cannot know. Served to Claude as a resource |
| `docs/evals.md` | How the evals run and what they do not prove |

Tests: `python -m pytest tests -q`.
