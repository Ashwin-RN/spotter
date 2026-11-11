# Spotter

A personal fitness record you keep by talking to Claude.

You talk to Claude. Claude logs through Spotter over MCP. Spotter stores it and works out every number in code: bests, trends, calorie estimates, the pace a goal needs. Claude quotes the numbers; it never invents them.

**Status: stages 1 to 5 are live.**

Live: https://spotter-production-0c0f.up.railway.app. The landing page carries the setup steps: sign in, connect Claude, start talking.

## What it does today

| You say | What happens |
|---|---|
| "Back squat, 3 sets of 5 at 80 kg" | One `log_lifts` call; new bests worked out against every earlier set; the same message again within 10 minutes is a duplicate, nothing written |
| "Weighed 79.4 this morning" | One weigh-in a day; the 7-day average and the trend |
| "Lunch: two rotis and dal" | One `log_food` call for the whole meal; every number tagged with who supplied it: you, a saved food, the USDA table, or Claude's estimate; the meal slot from your own clock, never asked |
| "30 minutes on the treadmill" | A calorie estimate from the 2024 Compendium MET and your body weight, shown beside the machine's number |
| "Squat 100 by June" | A goal with a start, a target, the pace it needs and the pace so far |
| "How am I doing?" | A `get_trends` call: the smoothed body weight and its rate a week, average calories a day, sessions and cardio minutes, and a maintenance estimate once there is enough logged |
| "Am I on track?" | A `get_goals` call: each open goal's needed pace, its pace so far and its projected date |

18 MCP functions, a web app on the same account (Today, Progress with a page per lift, Goals, Foods, Settings), and a switch on Settings that says what Claude may do: read, log, or log and delete. One button on Today fills an empty account with four weeks of sample data, and one on Settings removes it. Settings also exports the data as a zip of CSV files and deletes the account. A deleted entry can be restored. An edit keeps no old values, and deleting the account cannot be undone.

## How it is built

- Python 3.12, Starlette, SQLAlchemy, Alembic, the `mcp` library (streamable HTTP, stateless); Postgres; sign-in through an outside provider (WorkOS AuthKit) for the web and for Claude, so both reach one account.
- Every write locks the person's row first, counts against a write limit, and passes the same checks whether it came from Claude or a form.
- The test suite runs plain and with warnings as errors on a clean export before every push.
- Evals: `claude -p` against a local copy with a throwaway person; the server records every call it received, and the eval judges that, not the model's own account of what it did. 17 cases, 3 runs each. At stage 5, with one case fewer, 48 of 48 runs passed, on `claude-fable-5-2`. The newest case, from stage 6, has not been run yet. The runner now pins `claude-opus-5-5`. See `docs/evals.md`.

## Where to read more

| File | What it is |
|---|---|
| `BUILD_LOG.md` | How each stage was built: the first-use path, the packages, the reviews and what they found, the live exit test, what the stage taught |
| `docs/contracts/` | What each stage had to do, fixed before the builders started |
| `docs/can-and-cant.md` | What Spotter records, what its numbers mean, and what it cannot know. Served to Claude as a resource |
| `docs/evals.md` | How the evals run and what they do not prove |

Tests: Python 3.12, `pip install -r requirements.txt -r requirements-dev.txt`, then `python -m pytest tests -q`.

## Licence

MIT for the code: see `LICENSE`. The cardio MET values, codes and activity descriptions come from the 2024 Adult Compendium of Physical Activities and keep its own terms: see `NOTICE`.
