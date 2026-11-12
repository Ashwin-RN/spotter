# Evals

**The evals check that Spotter's instructions and schemas steer Claude to the right call, measured
from what the server received, over repeated runs.**

## Run them

```
python -m evals.run                      every case, 3 runs each, every run must pass
python -m evals.run --n 1                one run each: a quick check
python -m evals.run --threshold 0.66     the exit test: every case passes at least 2 of 3
python -m evals.run --case yesterday --case saved_food --keep
```

| Flag | What it does |
|---|---|
| `--n N` | Runs per case, each a fresh conversation. Default 3. |
| `--threshold R` | The pass rate every case must reach, 0 to 1. Default 1.0. Exit code 1 when any case is under it. |
| `--case ID` | Only this case. Repeatable. |
| `--model M` | Passed through to the CLI as `--model`. Default: `claude-opus-5-5`, pinned, so a change in the CLI's own default never changes what a run costs. `--model ""` uses the CLI's default. |
| `--keep` | Keeps the temporary directory: the database, each run's raw CLI output, `results.json`. |
| `--time-zone Z` | The person's IANA time zone. Default: the machine's. |

## What it needs

- The Claude CLI (`claude`) on the PATH, signed in. Nothing else: no key, no network beyond the CLI's own.
- The repo's venv. Nothing is installed.

## What one run costs

**It depends on the model.** The runner pins `claude-opus-5-5` since 2026-11-10, after the CLI's own default
changed within a day and one full run cost three times the estimate. Both below are CLI 2.1.322, measured on
2026-11-10, before the pin.

| Default model | Seconds a run | Tokens a run | Cost a run (CLI, list price) | Full run, 16 × 3 |
|---|---|---|---|---|
| claude-opus-5-5 (earlier that day) | 8 to 35 | 67,000 to 165,000 | $0.10 to $0.18 | not measured |
| claude-fable-5-2 (later that day) | 8 to 50 | 91,000 to 179,000 | $0.26 to $0.79 | measured: about 17 minutes of runs, $22.41 |

- Most tokens are cached reads of the CLI's own system prompt and the tool schemas: eighteen when this
  was measured, nineteen since `import_entries`.
- The model line under the table says which model ran. Pin one with `--model` to compare runs.
- On a subscription that is usage against the plan's limits, not a bill.
- The table prints tokens per run and the cost per case.

## How a run works

1. One local server for the whole run: the app built in-process with `create_app(settings, fetch=provider.fetch)`,
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
- `seed` rows are made through Spotter's services, in order: `save_food`, `log_bodyweight`,
  `log_measurement`, `set_goal`, `import_entries`, each with `data`, and `add_sample_data`, which takes no data.
  - `add_sample_data` is four weeks of everything and three goals in one call. It has no `as`.
  - `import_entries` returns no one id either, so it has no `as`. Its rows carry fixed dates.
- `db` checks the person's live rows, or with `"state": "deleted"` the soft-deleted ones.
  - The tables: `food_entry`, `saved_food`, `bodyweight`, `measurement`, `cardio_entry`, `lift_session`, `goal`.
  - A goal has no delete, so every goal row is live. Check `status` instead.

### Placeholders

A placeholder is a whole string. It works in `seed`, `expect` and `db`.

| Placeholder | Becomes |
|---|---|
| `{{today}}` | The person's today, `YYYY-MM-DD`, in their zone. |
| `{{yesterday}}` | The day before. |
| `{{next_month_end:N}}` | N from 1 to 12. The last day of month N, the next one strictly after today. |
| `{{seed.<as>}}` | The id of the seeded row named `as`. Not in `seed`. |

`{{next_month_end:N}}` in examples, today 10 November 2026:

- `:3` is 2027-03-31. "By the end of March" said in November means next March.
- `:12` is 2026-12-31.
- `:11` is 2026-11-30. On 30 November itself it is 2027-11-30.
- It follows the server's own rule: "the next such day after today".

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
| `reply_has` | The final reply contains every one of these phrases, ignoring case. |
| `reply_lacks` | The final reply does not contain these phrases, ignoring case. |
| `reply_lacks_pattern` | The final reply matches none of these regular expressions, ignoring case. A pattern that does not compile stops the load. |
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
| `goal_date` | I want to be 72 kg by the end of March (two weigh-ins, about 80 kg) | `set_goal` with kind bodyweight, target 72, `target_date` `{{next_month_end:3}}`, once; one goal row, down, open |
| `how_am_i_doing` | How am I doing? (sample data: four weeks) | `get_trends` called; no write function called |
| `on_track` | Am I on track? (sample data: three goals) | `get_goals` called; no write function called |
| `export_is_a_click` | Export my data | No write function called; the reply says "Settings" |
| `delete_is_a_click` | Delete my account (one weigh-in; Claude may delete) | `delete_entry` never called; the reply says "Settings"; the weigh-in still live |
| `drop_goal` | Drop the squat goal (a Back Squat goal and a Bench Press goal) | `set_goal` with the squat goal's id and status dropped, one success; squat dropped, bench still open |
| `decimals_and_dashes` | How am I doing? (79.2 kg yesterday, 79.3 kg today, no goal) | `get_trends` called; no write function called; no em dash or en dash in the reply; no body weight at two decimals |
| `import_preview_first` | Bring my weigh-ins over from my old app, with a five-day export, `Date,Weight (lb)` | `import_entries` called with `weight_unit` lb, never with `dry_run` false; the reply asks; no weigh-in, live or deleted |
| `import_again` | I checked the preview, write them, with the same export (the five rows already imported) | `import_entries` called with `weight_unit` lb; the reply says "already"; 5 live weigh-ins, all `import`, none deleted |
| `import_unknown_lift` | Import my lifts from a Strong export of three Zorbinator Press sets at 20 kg | `list_exercises` or `import_entries` called; the reply asks and names Zorbinator; no lift session |

"No write function" is the twelve writes by name in `no_call`: `log_lifts`, `log_bodyweight`,
`log_measurement`, `log_food`, `save_food`, `log_cardio`, `set_goal`, `update_settings`, `edit_entry`,
`restore_entry`, `delete_entry`, `import_entries`. A write added later must be added to those lists.

### What the six stage 5 cases prove, and what they do not

| Case | Proves | Does not prove |
|---|---|---|
| `goal_date` | The model turns "the end of March" into the next 31 March and sends it as the date. The server stores the goal pointing down. | Any other month or phrasing. A date said on the last day of a month. |
| `how_am_i_doing` | The question goes to `get_trends`, and nothing is written. | That the reply quotes the numbers right. Other calls beside it, such as `get_goals`, are allowed. |
| `on_track` | The question goes to `get_goals`, and nothing is written. | That the reply quotes the numbers right, or reads the statuses right. |
| `export_is_a_click` | The model writes nothing and points to Settings. | That it never offers to export. "Settings" can appear in a reply that also offers something else. |
| `delete_is_a_click` | With deleting allowed and a row to delete, the model does not delete entries one by one, and points to Settings. | That it would not delete with a firmer request. The reply check is one word. |
| `drop_goal` | The model finds the squat goal's id and drops that one only. The bench goal stays open. | A goal named less plainly, or two goals for the same lift. |

The sample-data seeds carry `includes_sample` and a note in every read. The model may say so in its
reply. That is right, and nothing checks it.

### The stage 6 case: how Claude says numbers

**`decimals_and_dashes` holds the reply paragraph in the server instructions. It has not been run yet.**

- The two weigh-ins are at 1 dp. Their 7-day average works out at 79.25 kg, two decimals, before Spotter
  rounds it to 1 dp. A reply that recalculates, or adds decimals, says a figure Spotter never gave.
- No goal is seeded, so no goal figure is in the reply either.
- The dash check is `reply_lacks` with U+2014 and U+2013. A hyphen inside a word passes.
- The pattern, a two- or three-digit figure followed by exactly two decimal places:

  ```
  (?<![\d.,:+\-\u2212])\d{2,3}\.\d{2}(?!\d|\.\d)
  ```

  - It names no figure, so the case holds on any day.
  - It needs no unit, so a bare "79.25" at the end of a sentence or in a table cell fails too.
  - It passes "79.3 kg", "2,216 kcal", "29:30", "29:30.0", "2026-11-11", "11.11.2026" and a signed
    rate such as "-0.35 kg a week".

| Proves | Does not prove |
|---|---|
| The model keeps Spotter's body weight figures at their shown precision, and joins clauses without a dash. | A weigh-in the person entered at two decimals: Spotter gives it as entered, and the pattern would fail it. The seed avoids it. |
| | Three decimals, or a one-digit figure: the pattern looks for exactly two decimals on a two- or three-digit figure. |
| | A date written as day and month with a dot and no year, such as "11.11": the pattern fails it. |
| | Any other number rule in the paragraph: rounding further, "about" as a symbol, naming an estimate. |

### The stage 6 import cases

**`import_preview_first`, `import_again` and `import_unknown_lift` hold the import rule in the server
instructions and the import's own description. They have not been run yet.**

- The server instructions carry only the import's guards. The rest is in `import_entries`' description,
  which the model reads when it calls the function. Claude Code cuts server instructions, and the
  description of a tool that loads upfront, past 4,096 characters, and the evals run through it. A test
  holds both under that cap.

- A placeholder does not work in `say`, so the pasted exports carry fixed dates: 5 to 9 October 2026 for
  the weigh-ins, 20 October 2026 for the lift. They leave the 2-year window in October 2028.
- `say` is one line, so each pasted CSV is one line too: the header, then each row after a space.
- `import_again`'s seed is the same five rows through `import_entries` with `dry_run` false and
  `weight_unit` lb, as the person's earlier, approved call would have sent them.
- No unit is assumed. Every case's export names its unit in its header, and a call that leaves the unit
  out is refused with `missing_unit`. Each case passes or fails on what it checks either way: the seed
  sends `weight_unit` lb itself.
- `inputs_forbid` reads `dry_run` at the top of the call. A call that leaves `dry_run` out passes, which is
  right: left out is a preview.

| Case | Proves | Does not prove |
|---|---|---|
| `import_preview_first` | The model previews before it writes, in the export's own unit, and asks the person. Nothing is written. | That the reply shows the preview's numbers right, or names the unit. A preview of a longer export. |
| `import_again` | Told to write, the model sends the export's own unit, and says the rows are already there. Nothing is written twice. | That it previews first when told to write. It may send `dry_run` false at once, and passes. |
| `import_unknown_lift` | A lift name not on the list is never written, and the model asks about it by name. | That it asks once per name: there is one name. That it offers the right listed name, or that no call carries a guessed one: the judge has no key for that. |

**What a run of them costs, by the import spec's estimate, not measured:** $0.15 to $0.40 a run on
`claude-opus-5-5`, so about $1.50 to $3.50 for the 3 cases at 3 runs. The instructions changed, so the
whole suite should run again: 20 cases at 3 runs is 60 runs, about $9 to $15, estimated.

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
- **The heuristics are heuristics.** `reply_asks` is a question mark; `reply_has` and `reply_lacks` are
  phrase lists; `reply_lacks_pattern` is a regular expression. A reply can ask without one, say a phrase
  innocently, match a pattern with a correct figure, or name Settings and still offer something else.
- **Not the person's own data.** Every run is a throwaway person on a local copy. Nothing here touches
  the live site.
