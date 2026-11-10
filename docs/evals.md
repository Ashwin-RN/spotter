# Evals

**The evals check that Spotter's instructions and schemas steer Claude to the right call, measured
from what the server received, over repeated runs.**

## Run them

```
python -m evals.run                      every case, 3 runs each, every run must pass
python -m evals.run --n 1                one run each: a quick check
python -m evals.run --threshold 0.66     the stage 4 exit test: every case passes at least 2 of 3
python -m evals.run --case yesterday --case saved_food --keep
```

| Flag | What it does |
|---|---|
| `--n N` | Runs per case, each a fresh conversation. Default 3. |
| `--threshold R` | The pass rate every case must reach, 0 to 1. Default 1.0. Exit code 1 when any case is under it. |
| `--case ID` | Only this case. Repeatable. |
| `--model M` | Passed through to the CLI as `--model`. Default: the CLI's own default. |
| `--keep` | Keeps the temporary directory: the database, each run's raw CLI output, `results.json`. |
| `--time-zone Z` | The person's IANA time zone. Default: the machine's. |

## What it needs

- The Claude CLI (`claude`) on the PATH, signed in. Nothing else: no key, no network beyond the CLI's own.
- The repo's venv. Nothing is installed.

## What one run costs

Measured on 2026-11-10, CLI 2.1.322, default model claude-opus-5-5:

- 12 to 35 seconds and 90,000 to 165,000 tokens per run. Most are cached reads of the CLI's own system
  prompt and the sixteen tool schemas.
- The CLI reports $0.13 to $0.17 a run at list price.
- A full run, ten cases × 3, is about 10 minutes and about $4.50 at list price.
- On a subscription that is usage against the plan's limits, not a bill.
- The table prints tokens per run and the cost per case.

## How a run works

1. One local server for the whole run: the app built in-process with `create_app(settings, fetch=...)`,
   served by uvicorn on a free port on 127.0.0.1, with a temporary SQLite file built by the migrations
   and the exercise list synced.
2. Sign-in is real. `evals/issuer.py` makes a key pair and a fake provider (its key set and metadata),
   copied from `tests/conftest.py`. The app is given the fake provider's fetch: the same hook the tests
   use. No check in the app is bypassed or added.
3. Per run, a fresh throwaway person: a new account with the machine's time zone, the case's seed rows
   made through Spotter's own services, and a token signed for that person alone.
4. The CLI runs once per conversation:

   ```
   claude -p "<say>" --output-format json --mcp-config <file> --strict-mcp-config
          --allowedTools "mcp__spotter__*" --no-session-persistence [--model M]
   ```

   - The MCP config names one server, `http://127.0.0.1:PORT/mcp`, with the token in an
     `Authorization` header. It sits in the temporary directory and is deleted when the run ends.
   - The working directory is an empty temporary folder: no project `CLAUDE.md`, memory or settings.
   - `--no-session-persistence` keeps the runs out of the CLI's saved sessions.
   - Each run is cut off at 180 seconds.
5. The token is never printed. Every printed or kept line is masked; the key pair dies with the process.

### The environment the CLI gets

The CLI runs fine when started from inside a Claude Code session. The runner still leaves out the
variables that session sets (`CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_EFFORT`, `AI_AGENT` and every
`CLAUDE_CODE_*`), because some point a child at the parent's IDE and messaging sockets. Each run then
is what it would be from a plain terminal. It prints the names it left out. `CLAUDE_CONFIG_DIR` and
everything else pass through.

User-level CLI settings and skills still load, as they would for the person.

## Where the ground truth comes from

**The server's record, never the model's account of what it did.**

- `evals/recorder.py` wraps the app. It reads every JSON-RPC body sent to `/mcp` and every answer:
  each `tools/call` with its function name and arguments as sent, and whether the answer was `isError`.
- Records are keyed by a hash of each run's token, so runs never mix.
- The `db` checks read the throwaway person's rows after the run. The food tag (`source`) is the
  server's, so the row is the proof of what the call meant.
- The CLI's JSON gives only the final reply, the token usage, the cost, the turns and the model.

The record is the server's because `--output-format json` carries no tool calls, and the server's
record cannot be talked round.

## The cases

`evals/cases.json`. Each case is `{id, say, expect, switch?, seed?, db?}`.

- `switch` sets what the person lets Claude do (`read`, `log`, `log_delete`) through the Settings
  page's own service before the run. Left out, it is the default, `log`.
- `seed` rows are made through Spotter's services; their data may say `{{today}}` or `{{yesterday}}`.
- `db` checks the person's live rows, or with `"state": "deleted"` the soft-deleted ones.

The vocabulary of `expect` is in `evals/judge.py`'s notes. In short:

| Key | Holds when |
|---|---|
| `function` | It was called. A refusal still counts as called. |
| `inputs_include` | One call of it sent arguments containing this (nested subset). |
| `inputs_exclude` | These paths are absent in every call of it. |
| `inputs_forbid` | No call of it sent this value at this path. |
| `ok_calls` | Exactly this many calls of a function succeeded. |
| `no_call` | These functions were never called. |
| `refused` | Every call of it was refused. |
| `reply_asks` | The final reply contains a question mark. |
| `reply_lacks` | The final reply does not contain these phrases. |
| `any_of` | At least one of these expects holds. |

| Case | Says | Passes when |
|---|---|---|
| `stated_number` | Lunch: dal, 180 kcal | `log_food` with kcal 180, never `estimated: true`; the row is `you_said` |
| `guessed_number` | Lunch: two rotis and dal | `log_food` with `estimated: true` on every entry; every row `claude_estimate` |
| `saved_food` | Two rotis (roti saved: 120 kcal) | `log_food` by the saved food's id, servings 2, no kcal; one row, `saved_food`, 240 |
| `meal_not_asked` | Had a banana, 90 kcal | `log_food` with no meal; the reply does not ask which meal |
| `yesterday` | Weighed 70 kg yesterday | `log_bodyweight` dated yesterday in the person's zone |
| `treadmill_once` | 30 minutes on the treadmill | Exactly one successful `log_cardio`, 30 minutes, a treadmill activity |
| `repeat_sent` | Waist 84 (84 already logged today) | `log_measurement` is called; the server may answer duplicate |
| `delete_ambiguous` | Delete my weigh-in (two weigh-ins on two days; Claude may delete) | `delete_entry` never called; both weigh-ins still live |
| `delete_explicit` | Delete my last weigh-in (one weigh-in; Claude may delete) | `delete_entry` with kind `bodyweight` and that weigh-in's id, once; the row is soft-deleted |
| `unknown_exercise` | Log 3 sets of 8 on the zorbinator at 20 kg | `list_exercises` called or the reply asks; `log_lifts` never called; no lift session |

## What the evals prove

- That the server's instructions, function descriptions and schemas steer this model, under the
  Claude Code harness, to the calls the contract wants, at the rate printed.
- That the server's tags land as designed when the model sends what it should.
- Whether the handshake carried the server's instructions (the runner prints it). CLI 2.1.322 opens
  with `server/discover` (MCP 2026-07-28), not `initialize`; the recorder reads both. It cannot see
  whether the CLI then put them in front of the model; the cases measure the effect.

## What they do not prove

- **Other clients.** claude.ai, the desktop app and other MCP clients build their own prompt and may
  not show the server's instructions at all.
- **Other models.** A pass on the default model says nothing about a smaller one. Use `--model`.
- **One run is not a rate.** The model is not deterministic. That is why each case runs n times and
  the rate is printed as it is, not rounded up.
- **The heuristics are heuristics.** `reply_asks` is a question mark; `reply_lacks` is a phrase list. A
  reply can ask without one, or say the phrase innocently.
- **Not the person's own data.** Every run is a throwaway person on a local copy. Nothing here touches
  the live site.
