# Evals

**The evals check that Spotter's instructions and schemas steer an assistant to the right call, measured
from what the server received, over repeated runs. Three clients run them: the Claude CLI, Codex and a Gemini model.**

## When evals run

Evals cost money, so the whole set runs once per release, and only when `python -m evals.need main` says yes.
Inside a build with gates (B6), the core runs at each gate: see the golden package below.

```
python -m evals.need          against main
python -m evals.need HEAD~1   against any commit or branch
```

- It reads what an assistant reads, from this folder and from the other commit. That is the surface:
  - the server's instructions;
  - the function list as everyone sees it and as the owner sees it;
  - each resource with its text;
  - whoami's answer for a new account and for the owner: setup, the new account note, the time zone note;
  - every string of 20 characters or more in `app/`, file by file, docstrings left out. This catches
    the notes, fix lines and plan lines that answers carry.
- Pages are not surface: `app/web.py`, `app/templates/` and `app/static/` are left out.
- The string check is strict on purpose. A changed log message says yes too: one run too many costs
  less than a changed note that goes out untested.
- A change to `evals/cases.json`, `evals/judge.py`, `evals/run.py`, `evals/recorder.py`, `evals/codex_client.py`
  or `evals/gemini_client.py` counts too.
- The first line is `evals needed: yes` or `evals needed: no`. Each line after it names one thing that
  differs, such as `tool whoami: description`, `resource spotter://can-and-cant`, `result whoami`,
  `owner result whoami` or `result text app/services/goals.py`.
- Exit code 0 for no, 1 for yes, 2 when it could not tell, with the reason.
- It costs nothing: no assistant, no network, no open port. It takes a few seconds.
- A change to code alone, with the same words, schemas and cases, says no. The tests cover that change.

## Run them

```
python -m evals.run                      every case, 3 runs each, every run must pass
python -m evals.run --n 1                one run each: a quick check
python -m evals.run --threshold 0.66     the exit test: every case passes at least 2 of 3
python -m evals.run --case yesterday --case saved_food --keep
python -m evals.run --golden --model claude-sonnet-5-5
python -m evals.run --core --model claude-sonnet-5-5
python -m evals.run --client codex --model gpt-6-luna
python -m evals.run --client gemini --golden --n 1
```

| Flag | What it does |
|---|---|
| `--client C` | Who answers: `claude` (the Claude CLI, the default), `codex` (`codex exec`) or `gemini` (a Gemini model, with the runner as the MCP host). |
| `--n N` | Runs per case, each a fresh conversation. Default 3. |
| `--threshold R` | The pass rate every counted case must reach, 0 to 1. Default 1.0. Exit code 1 when any counted case is under it. |
| `--case ID` | Only this case. Repeatable. |
| `--golden` | Only the golden set: the cases with a `kind`. |
| `--core` | Only the core: the cases with `core` true, the set each gate runs. With `--case`, only the core cases named. See "The golden set". |
| `--model M` | The model, pinned by its exact name. Defaults: `claude-opus-5-5`, `gpt-6.1-sol`, `gemini-4-hydrogen`, each its client's release tier. `--model ""` uses the client's own default (not for Gemini). Each Claude run must show this model alone: see "The advisor is off, and one model a run". |
| `--effort E` | The reasoning effort every run sends: see "Clients, models and effort". `--effort ""` sends none. |
| `--gemini-key N` | Gemini only: which key of `.env.evals`, `GOOGLE_API_KEY_<N>`. Default 1. One key a run, never switched. |
| `--gemini-per-minute N` | Gemini only: requests a minute at most, each spaced to keep under it. Default 5, the free tier's. 0 is not paced. |
| `--keep` | Keeps the temporary directory: the database, each run's raw output, `results.json`. |
| `--time-zone Z` | The person's IANA time zone. Default: the machine's. |

## What it needs

- Claude: the Claude CLI (`claude`) on the PATH, signed in. No key, no network beyond the CLI's own.
- Codex: codex-cli, signed in with ChatGPT. The runner starts the vendor `codex.exe` under `%APPDATA%\npm`, else
  `codex` on the PATH. A run is plan usage, not a bill.
- Gemini: `.env.evals` at the repo root, git-ignored, holding `GOOGLE_API_KEY_1` and on. The runner reads one key
  in its own process at the start, sends it only in the `x-goog-api-key` header, and takes it out of every text it
  prints or keeps. No test reads the file.
- The repo's venv. Nothing is installed.

## Clients, models and effort

**Every tier is pinned by its exact model name and its reasoning effort** (contract `function-list.md`, G1).

| Tier | Claude | Codex | Gemini |
|---|---|---|---|
| Every gate | `claude-sonnet-5-5` | `gpt-6-luna` | |
| Each release | `claude-opus-5-5` | `gpt-6.1-sol` | |
| Now and then, not a gate | | | `gemini-4-hydrogen` |

- `gpt-6-luna` is the small GPT, by its exact name in Codex's own model list (`~/.codex/models_cache.json`, fetched
  by codex-cli 0.169.0 on 2026-11-17): "Fast and affordable model for easier tasks", shown in the picker.

| Client | The effort flag | Levels it takes |
|---|---|---|
| Claude | `--effort <level>` on `claude -p` (`claude --help`, CLI 2.1.326) | low, medium, high, xhigh, max |
| Codex | `-c model_reasoning_effort="<level>"`, the config key Codex 0.169.0 reads | low, medium, high, xhigh, max; ultra on `gpt-6.1-sol` (its model list) |
| Gemini | `generationConfig.thinkingConfig.thinkingLevel` on `generateContent` | minimal, low, medium, high, xhigh; Gemini 4 and `gemini-3.8-flash` take no minimal |

**Each pinned model runs at its own default effort, the level a person gets when nothing is set, sent by its flag**
(the owner, 2026-11-17: "yes, pin the defaults"). So a change in a default, or in the owner's own settings, never
changes a run. Read on 2026-11-17, with no paid call:

| Model | Effort sent | Source |
|---|---|---|
| `claude-opus-5-5` | medium | https://code.claude.com/docs/en/model-config.md, "Resolution order", step 5: "Opus 5.5, Sonnet 5.5, and Haiku 5.5 default to `medium`" |
| `claude-sonnet-5-5` | medium | The same page and step |
| `gpt-6.1-sol` | low | Its `default_reasoning_level` in Codex's own model list, `~/.codex/models_cache.json` (codex-cli 0.169.0) |
| `gpt-6-luna` | medium | The same list |
| `gemini-4-hydrogen` | high | Google's thinking page, aistudio.google.com/docs/thinking: "On (high)" |

- Any other model sends no effort: its client's own default, unpinned. The run's lines say "effort sent: none".
  `--effort` sets another level by hand.
- Claude Code takes `--effort` over the user's settings (the same page, "Resolution order": the launch flag is
  step 3, the settings step 4). Only `CLAUDE_CODE_EFFORT_LEVEL` comes before it, and the runner leaves every
  `CLAUDE_CODE_*` variable out of the CLI's environment.
- Not checked: whether `thinkingConfig.thinkingLevel` is the field `generateContent` takes for Gemini 4. A
  first-turn refusal that names the thinking level stops that model's runs, so a wrong field shows at once.

| | Claude | Codex | Gemini |
|---|---|---|---|
| Turns | the CLI's `num_turns`: model steps | `turn.completed` events: 1 for one prompt | `generateContent` calls |
| Tokens: input, cached, output | input and cache writes; cache reads; output | input less cached; cached; output, reasoning inside it | prompt less cached; cached; answer and thoughts |
| Cost | the CLI's list-price figure | none given: plan usage | none given: the free tier |
| Model | `modelUsage`, checked by the one-model rule | the model asked for: no event names one | each answer's `modelVersion` |

## What one run costs

**It depends on the model.** The runner pins `claude-opus-5-5` since 2026-11-10, after the CLI's own default
changed within a day and one full run cost three times the estimate. Both below are CLI 2.1.322, measured on
2026-11-10, before the pin.

| Default model | Seconds a run | Tokens a run | Cost a run (CLI, list price) | Full run, 16 × 3 |
|---|---|---|---|---|
| claude-opus-5-5 (earlier that day) | 8 to 35 | 67,000 to 165,000 | $0.10 to $0.18 | not measured |
| claude-fable-5-2 (later that day) | 8 to 50 | 91,000 to 179,000 | $0.26 to $0.79 | measured: about 17 minutes of runs, $22.41 |

- Most tokens are cached reads of the CLI's own system prompt and the tool schemas: eighteen when this
  was measured, nineteen since `import_entries`, twenty since `send_feedback`, twenty-two since `save_routine`
  and `get_routines`, twenty-four since `what_is_logged` and `get_data`.
- The model line under the table says which model ran. Pin one with `--model` to compare runs.
- The cost is the CLI's list-price figure. On a subscription that is usage against the plan's limits, not
  a bill.
- The table prints tokens per run and the cost per case, for its runs.

**The run of 2026-11-12, measured.** The branch at `3eff0c3`, model `claude-opus-5-5`, 20 cases at 3 runs:
60 of 60 runs passed. It started at 21:23:48 and ended at 21:42:57. `reply_asks` read a question mark.

| | Smallest | Largest |
|---|---|---|
| Tokens a run, by case | 83,802 (`export_is_a_click`) | 179,850 (`treadmill_once`) |
| Cost by case, for its 3 runs | $0.3352 (`delete_is_a_click`) | $0.5211 (`import_again`) |

- The whole run: $8.5603.
- The runner printed: "handshakes that carried the server's instructions: 60 of 60 runs (whether the CLI
  shows them to the model is what the cases measure)".
- Three runs a case is a small sample: see "One run is not a rate" below.

## How a run works

1. One local server for the whole run: the app built in-process with `create_app(settings, fetch=provider.fetch)`,
   served by uvicorn on a free port on 127.0.0.1, with a temporary SQLite file built by the migrations
   and the exercise list synced.
2. Sign-in is real. `evals/issuer.py` makes a key pair and a fake provider (its key set and metadata),
   copied from `tests/conftest.py`. The app is given the fake provider's fetch: the same hook the tests
   use. No check in the app is bypassed or added.
3. Per run, a fresh throwaway person: a new account with the machine's time zone, the case's seed rows
   made through Spotter's own services, and a token signed for that person alone.
4. For Claude, the CLI runs once per conversation:

   ```
   claude -p "<say>" --output-format json --mcp-config <file> --strict-mcp-config
          --allowedTools "mcp__spotter__*" --no-session-persistence
          --settings '{"advisorModel": ""}' [--effort E] [--model M]
   ```

   - The MCP config names one server, `http://127.0.0.1:PORT/mcp`, with the token in an
     `Authorization` header. It sits in the temporary directory and is deleted when the run ends.
   - The working directory is an empty temporary folder: no project `CLAUDE.md`, memory or settings.
   - `--no-session-persistence` keeps the runs out of the CLI's saved sessions.
   - `--settings` with an empty `advisorModel` is the advisor's second off switch: see "The advisor is off, and
     one model a run".
   - Each run is cut off at 300 seconds. It was 180 until 17 November 2026, when two data runs were cut off
     after 10 to 15 right calls, made one a turn.
5. For Codex, `codex exec --json --ephemeral --skip-git-repo-check --ignore-user-config -s read-only`, the
   features that are not Spotter's off, web search off, `-m` and `model_reasoning_effort` pinned, and the server by
   `-c mcp_servers.spotter.url`, `.bearer_token_env_var` and `.default_tools_approval_mode="approve"`. The token is
   only in that one process's environment. A run that is cut off is stopped with its whole process tree.
6. For Gemini, the runner is the host: mcp's own client on the local server, the server's instructions as
   `systemInstruction`, each tool's input schema as `parametersJsonSchema`, calls one after another, each model part
   sent back as received. Requests are paced to 5 a minute, the free tier's limit (`--gemini-per-minute`): each
   waits until 12 seconds have passed since the one before. A 429 or 503 still backs off once; a 429 that persists
   stops that model. A run cut off keeps its usage, finish reasons and back-off log.
7. A run that raises is that run's failure, its reason the error, and the runs go on. A stop (a Gemini quota)
   keeps the run it stopped as a failure with the stop as its reason. With `--keep`, `results.json` is written
   whatever ended the runs.
8. The token is never printed. Every printed or kept line is masked; the key pair dies with the process.

### The environment the CLI gets

The CLI runs fine when started from inside a Claude Code session. The runner still leaves out the
variables that session sets (`CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_EFFORT`, `AI_AGENT` and every
`CLAUDE_CODE_*`), because some point a child at the parent's IDE and messaging sockets. Each run then
is what it would be from a plain terminal. It prints the names it left out. `CLAUDE_CONFIG_DIR` and
everything else pass through. It sets one variable of its own: `CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1`, whatever
the parent held for it.

User-level CLI settings and skills still load, as they would for the person, except the advisor.

### The advisor is off, and one model a run

**Since 17 November 2026 every run is the model asked for, alone.** The owner wants reliable results more than a
lower price, and a run that took a stronger model's help does not show what the model asked for does by itself.

- The advisor is a CLI tool. It hands the whole conversation to a second, stronger model, whose advice the
  model then takes. The user's own CLI settings turn it on, with `advisorModel`.
- In the release run of 17 November 2026, 11 of 153 runs called it: `claude-fable-5-2` beside
  `claude-opus-5-5` in the CLI's `modelUsage`. Such a run measures two models, not the one it names.
- Two switches turn it off in every run:
  - `CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1` in the CLI's environment. This is the documented switch: the CLI
    then ignores any `advisorModel`. See https://code.claude.com/docs/en/advisor.md, "Turn the advisor
    off", and https://code.claude.com/docs/en/env-vars.md.
  - `--settings '{"advisorModel": ""}'` on the command line. It turned the advisor off on CLI 2.1.326, but
    no doc names it as a switch. It stays as a second layer.
  - `--disallowedTools advisor` did not turn it off on CLI 2.1.326.
- After each run a guard reads the models in the CLI's `modelUsage`:
  - With a full model id, such as the pinned `claude-opus-5-5`, any other model fails the run. A small
    helper model fails it too. So does a run where the pinned model took no part.
  - The reason names the extra models: "another model took part: claude-fable-5-2". The reply is still
    judged, and its own reasons print beside it.
  - With `--model ""`, or an alias such as `sonnet`, the run must show exactly one model. `modelUsage`
    names full ids, never an alias. With more, the reason names them all, such as "more than one model took
    part: claude-fable-5-2, claude-sonnet-5-5". Cost cannot tell the main model: in the two runs kept on 17
    November 2026, the advisor cost more than `claude-opus-5-5` in 16 of the 19 that had both.
  - A run whose output names no model fails too: "the CLI reported no model".
- The summary prints a line under the model line, such as "runs where another model took part: 0 of 153".
  Each run it counts is a failed run in the table. With `--keep`, `results.json` holds each run's
  `other_models`.

## Where the ground truth comes from

**The server's record, never the model's account of what it did.**

- `evals/recorder.py` wraps the app. It reads every JSON-RPC body sent to `/mcp` and every answer:
  each `tools/call` with its function name and arguments as sent, whether the answer was `isError`, and whether
  any answer came. For a data read it keeps the answer itself, so `quotes_spotter` can read its figures.
- Records are keyed by a hash of each run's token, so runs never mix.
- The `db` checks read the throwaway person's rows after the run. The food tag (`source`) is the
  server's, so the row is the proof of what the call meant.
- The client's output gives only the final reply, the token usage, the cost where there is one, the turns and
  the model.

The record is the server's because `--output-format json` carries no tool calls, and the server's
record cannot be talked round.

## The cases

`evals/cases.json`. Each case is `{id, say, calls, expect, kind?, core?, pair?, switch?, seed?, db?}`.

- `calls` is on every case: `{must, may, must_not}`, each a list of function names.
  - `must`: at least one of these called and answered. A refusal is an answer. Empty asks for no call.
  - `may`: called or not, either is right. It judges nothing: it says what the case expects.
  - `must_not`: none of these called, refused or not.
  - They agree with `expect` when the cases load: its `function` is in `must`, its `no_call` in `must_not`. A
    reason both give is said once.
- `kind` marks the golden set: `direct`, `incidental` or `look_alike`. See "The golden set".
- `core`, true or false: whether `--core` runs it. Every case of the golden package carries it, and so do G1's golden
  cases and the seven cases G1 added (`catch_up`, `import_other_app`, `save_food_asked`, `edit_fixes_minutes`,
  `food_lookup`, `routine_read`, `exercise_list`), all true. A case without it is not in the core. A value that is
  not true or false stops the load.
- `pair`, on a look-alike of the golden package: the id of the incidental case it is paired with. It must name an
  incidental case of the file, and only a look-alike carries one, or the load stops.

- `switch` sets what the person lets Claude do (`log` or `log_delete`) through the Settings
  page's own service before the run. Left out, it is the default, `log_delete`. A case that sets
  anything else, `read` among them, stops the load.
- `seed` rows are made through Spotter's services, in order: `save_food`, `log_bodyweight`,
  `log_measurement`, `log_lifts`, `log_cardio`, `set_goal`, `import_entries`, `save_routine`, `update_settings`,
  each with `data`, and `four_weeks` and `sixteen_weeks`, which take no data.
  - `update_settings` sets the person's settings, such as `weight_unit` lb, called as the web calls it. It
    returns no id, so it has no `as`.
  - `four_weeks` (`evals/four_weeks.py`) is four weeks of everything and three goals in one call, on an empty account. It has no `as`.
  - `sixteen_weeks` (`evals/sixteen_weeks.py`) is a year of weigh-ins, sixteen weeks of lifts, food, cardio and a
    waist, and two goals, in one call, on an empty account. It has no `as` either.
  - `import_entries` returns no one id either, so it has no `as`. Its rows carry fixed dates.
  - `edit_entry` edits a row seeded before it, so the person has an edit to undo. Its `data` is
    `{kind, entry_id, changes}`, all three, and `entry_id` is that row's `{{seed.<as>}}`.
- `db` checks the person's live rows, or with `"state": "deleted"` the soft-deleted ones.
  - The tables: `food_entry`, `saved_food`, `bodyweight`, `measurement`, `cardio_entry`, `lift_session`, `goal`,
    `feedback`, `routine`.
  - A `lift_session` row carries `set_count`: how many sets it holds.
  - A note is never kept as deleted: a deleted note's row is gone. So every feedback row is live.
  - A goal has no delete, so every goal row is live. Check `status` instead.

### Placeholders

A placeholder is a whole string. It works in `seed`, `expect` and `db`.

| Placeholder | Becomes |
|---|---|
| `{{today}}` | The person's today, `YYYY-MM-DD`, in their zone. |
| `{{yesterday}}` | The day before. |
| `{{days_ago:N}}` | N from 0 to 60. The day N days before today. |
| `{{next_month_end:N}}` | N from 1 to 12. The last day of month N, the next one strictly after today. |
| `{{seed.<as>}}` | The id of the seeded row named `as`. In `seed`, only in the data of a later seed: one that names an `as` no earlier seed gave stops the load. |

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
| `reply_has_pattern` | The final reply matches every one of these regular expressions, ignoring case: for a figure that must stand alone, such as 3.3 and never the 3.3 inside 93.3. A pattern that does not compile stops the load. |
| `reply_lacks_pattern` | The final reply matches none of these regular expressions, ignoring case. A pattern that does not compile stops the load. |
| `any_of` | At least one of these expects holds. |
| `read_any` | At least one data read was answered without error. |
| `quotes_spotter` | The reply holds at least this many of the figures this run's data reads gave. |
| `quotes_one_of` | The reply quotes at least one of these figures, and a data read of this run gave it. |

**A data read** is `what_is_logged`, `get_data`, `get_log`, `get_progress`, `get_trends` or `get_goals`. Never
`whoami`, `list_exercises`, `find_food` or `get_routines`.

**How `quotes_spotter` counts.**
- A figure counts when it appears in an answer of this run's data reads, refused ones left out, texts included,
  but not the fixed help texts: each read's `how`, and `what_is_logged`'s area meanings and field words. Those are
  the same on every account ("0.5 for a secondary", "reps over 30"), so they are no figure of the person's.
- Each is matched whole: never the 3.3 inside 93.3. 1,980 and 1980 are one figure; 100.0 is 100; a sign is
  ignored, so "down 0.24 kg" quotes -0.24. A duration such as 29:30 is one figure.
- Never counted: dates, ids, links, a span such as "28 days" or "4 weeks", and a figure the case's `say` holds.
- A whole number under 10 never counts, with a unit or without; a decimal counts. So "5 km" and "3 sessions"
  never count, and 3.3 does.
- Each distinct figure counts once.
- A negative case states 0 and a `reply_lacks_pattern` for a made-up figure of its kind.
- Its limit: an advice figure that happens to equal one of the run's figures counts. So a direct case that asks
  for a figure also carries it as `quotes_one_of`: the asked figure must be one a read of that run gave, whichever
  read. `how_am_i_doing` asks for none, and a case for something never logged carries a `reply_lacks_pattern` for
  a made-up figure and a `reply_has_pattern` that the reply says there is none.
- A figure is judged, not the sentence round it: "No weigh-in this morning. Yesterday you were 81.1 kg." passes
  `gold_weigh_morning`. A known limit.

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
| `delete_asks_first` | Delete my last weigh-in (one weigh-in; Claude may delete) | `delete_entry` with kind `bodyweight` and that weigh-in's id, one success, never with `confirm`; the reply asks; the weigh-in still live, none deleted |
| `unknown_exercise` | Log 3 sets of 8 on the zorbinator at 20 kg | `list_exercises` called or the reply asks; `log_lifts` never called; no lift session |
| `goal_date` | I want to be 72 kg by the end of March (two weigh-ins, about 80 kg) | `set_goal` with kind bodyweight, target 72, `target_date` `{{next_month_end:3}}`, once; one goal row, down, open |
| `how_am_i_doing` | How am I doing? (the `four_weeks` seed) | Any data read answered; two of the run's figures quoted; no write function called |
| `on_track` | Am I on track? (the `four_weeks` seed: three goals) | Any data read answered; no write function called |
| `export_is_a_click` | Export my data | No write function called; the reply says "Settings" |
| `delete_is_a_click` | Delete my account (one weigh-in; Claude may delete) | `delete_entry` never called; the reply says "Settings"; the weigh-in still live |
| `drop_goal` | Drop the squat goal (a Back Squat goal and a Bench Press goal) | `set_goal` with the squat goal's id and status dropped, one success; squat dropped, bench still open |
| `decimals_and_dashes` | How am I doing? (79.2 kg yesterday, 79.3 kg today, no goal) | Any data read answered; no write function called; no em dash or en dash in the reply; no body weight at two decimals |
| `import_preview_first` | Bring my weigh-ins over from my old app, with a five-day export, `Date,Weight (lb)` | `import_entries` called with `weight_unit` lb, never with `dry_run` false; the reply asks; no weigh-in, live or deleted |
| `import_again` | I checked the preview, write them, with the same export (the five rows already imported) | `import_entries` called with `weight_unit` lb; the reply says "already"; 5 live weigh-ins, all `import`, none deleted |
| `import_unknown_lift` | Import my lifts from a Strong export of three Zorbinator Press sets at 20 kg | `list_exercises` or `import_entries` called; the reply asks and names Zorbinator; no lift session |
| `undo_edit` | I changed today's weigh-in by mistake. Undo that change. (a weigh-in of 80 kg, then edited to 85) | `restore_entry` with kind `bodyweight`, that weigh-in's id and `undo_edit` true, one success; the weigh-in holds 80 again |
| `feedback_sent` | Send feedback to Spotter's maker about a confusing progress chart, with what they were doing, what they expected and what happened | `send_feedback` called, one success; one feedback row |
| `feedback_offered` | The progress chart in Spotter is confusing (a complaint, nothing asked for) | `send_feedback` never called; no feedback row; the reply says "feedback", "maker" or "runs Spotter" |
| `hi_there_first_log` | I weighed 80.4 kg this morning (nothing logged yet) | `log_bodyweight` with kg 80.4. The reply says "/hi". One live weigh-in, 80.4, dated today |
| `hi_there_not_again` | I weighed 80.4 kg this morning (80.9 kg logged yesterday) | `log_bodyweight` with kg 80.4. The reply does not say "/hi". Two live weigh-ins, yesterday's 80.9 and today's 80.4 |
| `setup_asks_first` | Hi Spotter, help me set up (nothing logged yet) | `whoami` called; `log_bodyweight`, `log_lifts`, `set_goal`, `update_settings`, `save_food`, `log_food`, `log_cardio`, `log_measurement`, `import_entries` and `save_routine` never called; the reply asks and says "weigh"; no weigh-in, no goal |
| `setup_picks_up` | Help me finish setting up Spotter (80.9 kg logged yesterday, a body weight goal of 77 kg by the end of March) | `whoami` called; `log_bodyweight`, `set_goal`, `log_lifts`, `update_settings`, `save_food`, `import_entries` and `save_routine` never called; the reply asks and says "lift"; one weigh-in, one goal |
| `delete_regretted` | You deleted a weigh-in I wanted to keep. I never asked for that, and I do not want it to happen again. (one live weigh-in) | `delete_entry` never called; the reply says "Settings"; the weigh-in still live |
| `lift_one_sentence` | Back squat 3 sets of 5 at 100 kg, then bench press 80 kg for 8, 8 and 6 | One successful `log_lifts`, its sets holding Back Squat 100 × 5, Bench Press 80 × 8 and Bench Press 80 × 6. One live session, `set_count` 6 |
| `lift_set_by_set` | Bench press 80 kg for 5 (Bench Press 80 × 5 logged today) | One successful `log_lifts` with Bench Press 80 × 5, never `new_session` true. One live session, the seeded one, `set_count` 2 |
| `lift_added_later` | Also did barbell rows, 3 sets of 10 at 50 kg (Back Squat 100 × 5, three sets, logged today) | One successful `log_lifts` with Barbell Row 50 × 10, never a Back Squat set, never `new_session` true. One live session, the seeded one, `set_count` 6 |
| `setup_stays_quiet` | I weighed 80.4 kg this morning (80.9 kg logged yesterday, so `whoami` carries `setup` and no note) | `log_bodyweight` with kg 80.4; `log_lifts`, `set_goal`, `update_settings`, `save_food`, `import_entries` and `save_routine` never called; the reply asks nothing (no "?") and does not say "set up" or "setup"; two live weigh-ins, yesterday's 80.9 and today's 80.4 |
| `routine_saved` | Save my push day: bench press 3 sets of 8, overhead press 3 sets of 10, dips 3 sets of 10 | One successful `save_routine`, name "push day" (any case), lifts Bench Press 3 × 8, Overhead Press 3 × 10, Dip 3 × 10, and no `weight_kg` in any lift. No `log_lifts`. One live routine with 3 lifts |
| `routine_logged` | Log my push day (Push day saved: Bench Press 3 × 8 at 60, Overhead Press 3 × 10 at 40) | One successful `log_lifts`, its sets holding Bench Press 60 × 8 and Overhead Press 40 × 10, never `new_session` true. One live session today, `set_count` 6 |
| `routine_changed` | Log my push day, but bench was 62.5 today (the same routine saved) | One successful `log_lifts`, its sets holding Bench Press 62.5 × 8 and Overhead Press 40 × 10, and no set at 60. One live session, `set_count` 6 |
| `lb_weigh_in` | Weighed 185 this morning (weights set to lb) | One successful `log_bodyweight` with `lb` 185 and no `kg`. The reply has "185" and no "kg" as a word. One live weigh-in today, kg 83.91 |
| `lb_lifts` | Bench 225 for 5, then 3 sets of 8 at 185 (weights set to lb) | One successful `log_lifts` with Bench Press `weight_lb` 225 × 5 and `weight_lb` 185 × 8, no `weight_kg`. The reply has no "kg" as a word. One live session today, `set_count` 4 |
| `miles_run` | Ran 3 miles outside in 27 minutes (distances set to mi) | One successful `log_cardio`, Outdoor Run, `distance_mi` 3, minutes 27, no `distance_km`. The reply has "9:00" and no "km" as a word. One live cardio entry, distance_km 4.83 |
| `units_asked` | I weigh 185 lb (84 kg logged yesterday, units never chosen) | One successful `log_bodyweight` with `lb` 185. No `update_settings`. The reply asks. Two live weigh-ins, today's kg 83.91 |
| `units_switch` | Show my weights in pounds and my runs in miles from now on | One successful `update_settings` with weight_unit lb and distance_unit mi. No log call |
| `lb_routine_changed` | Log my push day, but bench was 230 today (weights set to lb; Push day saved: Bench Press 3 × 8 at weight_kg 102.06, Overhead Press 3 × 10 at weight_kg 61.23) | One successful `log_lifts`: Bench Press `weight_lb` 230 × 8, and Overhead Press either `weight_kg` 61.23 or `weight_lb` 135 × 10. No set sent `weight_lb` 102.06 or `weight_kg` 230. One live session today, `set_count` 6 |
| `waist_in_inches` | My waist is 32 inches | Either one successful `log_measurement`, kind waist, value 81.28 or 81.3, and the reply says "cm"; or no successful `log_measurement` and the reply asks. Every live waist row holds 81.28 or 81.3 |
| `machine_calories` | 30 minutes on the treadmill, it said 250 calories | One successful `log_cardio` with minutes 30 and `machine_kcal` 250. One live cardio entry, `machine_kcal` 250 |
| `data_basketball` | How do I get better at basketball? (the `sixteen_weeks` seed) | `what_is_logged` called, or `get_data` on any area; no write function called; the reply quotes one figure Spotter gives from the seed, as a figure of its own: 12 or 48 lift sessions, 108 or 432 sets, 2 or 26 cardio entries or 8 in the 28 days before, 33% or 67% beside upper or lower, the squat's 116.7 or 100 kg x 5, or a plan line's 3.3 or 1.6 kg behind, 120 kg on the plan line, 79.3 or 80.9 |
| `data_lifting` | How do I get better at lifting? (the `sixteen_weeks` seed) | Any data read answered; two of the run's figures quoted; no write function called; the reply says "squat", and quotes 116.7 or 100 from the run |
| `data_calories` | How do I cut down on my calories? Where do I remove it from day to day? (the `sixteen_weeks` seed) | `get_data` with area `food`; no write function called; the reply says "biryani", and "640", "798" or "42%" |
| `data_squat_goal` | How are my squats going? (the `sixteen_weeks` seed) | Any data read answered; one of the run's figures quoted; no write function called; the reply quotes the plan line's 3.3 or 120 from the run (never the 3.3 inside 93.3) |
| `data_back_on_track` | I have slipped lately. How do I get back on track? (the `sixteen_weeks` seed) | Any data read answered; two of the run's figures quoted; no write function called; the reply quotes 1.6 (never inside 81.6 or 81.61) or 79.3 from the run |
| `data_lb` | What is the heaviest I have benched in the last 4 weeks? (weights set to lb, then the `sixteen_weeks` seed) | Any data read answered; one of the run's figures quoted; no write function called; the reply quotes 214.95 from the run and has no "kg" as a word |
| `data_empty` | How has my sleep been lately? (the `sixteen_weeks` seed: no sleep logged) | Any data read answered; no write function called; the reply gives no figure in hours, hrs or h for the person: a range's end, a sentence of advice ("most adults need 7 to 9 hours") or an example of what to say is allowed, any other figure after "slept" is not; 16 live measurements, all waist |
| `data_year_more` | Was my weight ever above 90 kg in the past year? Which days, and what did I weigh? (the `sixteen_weeks` seed) | `get_data` with area `weigh_ins`; no write function called; the reply says "91.2", "92.4" and "90.6" |
| `gold_squat_month` | What is my squat this month? (`sixteen_weeks`) | Any data read answered; 116.7 or 100 quoted from the run, kg or kilos; no write |
| `gold_weigh_morning` | What did I weigh this morning? (`sixteen_weeks`) | Any data read answered; 81.1 quoted from the run; no write |
| `gold_kcal_yesterday` | How many calories did I eat yesterday? (`sixteen_weeks`) | Any data read answered; 1,980 or 1980 quoted from the run; no write |
| `gold_squat_goal` | Am I on track for my squat goal? (`sixteen_weeks`) | Any data read answered; 3.3, 120 or 116.7 quoted from the run, in any words; no write |
| `gold_eat_more` | Should I eat more? (`sixteen_weeks`) | Any data read answered; two of the run's figures, one of them 1,980, 1,905, 79.3, 80.9 or 1.6; no write |
| `gold_best_deadlift` | What is my best deadlift? (back squats only, two sessions) | Any data read answered; the reply says no deadlift is logged, and no weight in kg or lb beside "deadlift" in its sentence; no write |
| `gold_run_week` | How was my run this week? (`sixteen_weeks`: no cardio in 21 days) | Any data read answered; the reply says there was no run, and no distance or minutes said for this week, 0 aside; no write |
| `inc_10k_plan`, `inc_trip_meals`, `inc_deload` | A 10k plan; meals on a trip; whether to deload this week (`sixteen_weeks`) | Incidental: any data read called; no write. Reported apart |
| `look_10k_friend`, `look_trip_hours`, `look_deload` | A 10k plan for a friend; a beach shack's hours; what a deload is (`sixteen_weeks`) | Look-alike: no data read, no write. Reported apart |
| `save_food_asked` | Save my usual protein shake: one scoop is 150 kcal; not had one today | One successful `save_food` with 150 kcal a serving, no `log_food`; one saved food, no food entry |
| `edit_fixes_minutes` | My run yesterday was 32 minutes, not 30 (yesterday's run seeded by `log_cardio`) | One successful `edit_entry` of that run with minutes 32, no `delete_entry` or `log_cardio`; the same run, 32 minutes, 5 km |
| `food_lookup` | How many calories is one serving of my oats bowl? (Oats bowl saved, 300 kcal) | `find_food` or `get_data` called; no write; the reply says 300 |
| `routine_read` | What is in my push day? (Push day saved) | `get_routines` called; no write; the reply says bench, overhead, 60 and 40 |
| `exercise_list` | Which squat exercises can I log in Spotter? | `list_exercises` called; no write; the reply names Goblet Squat and Bulgarian Split Squat |
| `catch_up` | Three days off: back squat 3 x 5 at 100 kg three days ago, a 640 kcal biryani two days ago, 80.2 kg yesterday | One lift session three days ago with 3 sets, one food entry two days ago of 640 kcal, one weigh-in yesterday of 80.2 kg, none on another date; no `delete_entry`, no `import_entries` |
| `import_other_app` | Bring my weigh-ins over from The Hacker's Diet Online, with its CSV export | `import_entries` called with `weight_unit` kg, never with `dry_run` false; the reply asks; no weigh-in, live or deleted |

"No write function" is every write, by name, in `no_call`: the names in `WRITE_FUNCTIONS` in
`app/services/common.py`. A write added later must be added to those lists.

### What the six stage 5 cases prove, and what they do not

| Case | Proves | Does not prove |
|---|---|---|
| `goal_date` | The model turns "the end of March" into the next 31 March and sends it as the date. The server stores the goal pointing down. | Any other month or phrasing. A date said on the last day of a month. |
| `how_am_i_doing` | Since G1: a data read was answered, two of its figures are quoted, and nothing is written. Which read is not judged. | That each figure is read right. That the quoted figures are the ones that matter most. |
| `on_track` | Since G1: a data read was answered, and nothing is written. Which read is not judged. | That the reply quotes the numbers right, or reads the statuses right. |
| `export_is_a_click` | The model writes nothing and points to Settings. | That it never offers to export. "Settings" can appear in a reply that also offers something else. |
| `delete_is_a_click` | With deleting allowed and a row to delete, the model does not delete entries one by one, and points to Settings. | That it would not delete with a firmer request. The reply check is one word. |
| `drop_goal` | The model finds the squat goal's id and drops that one only. The bench goal stays open. | A goal named less plainly, or two goals for the same lift. |

### The stage 6 case: how Claude says numbers

**`decimals_and_dashes` holds the reply paragraph in the server instructions. It ran on 2026-11-12: 3 of 3
passed.**

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
instructions and the import's own description. They ran on 2026-11-12, on `3eff0c3`: each 3 of 3 passed.
That was before the description gained its sentence on mixed units.**

**The second run, 2026-11-12, after that sentence:** the three import cases at 3 runs, `claude-opus-5-5`, 8 of
9 passed, from 22:02:38 to 22:05:43. `import_unknown_lift` run 2 failed: "the reply asks nothing". Its reply
wrote nothing, named the lift, would not guess and put the choice to the person, with no question mark,
the judge's only sign of asking, then and now. That reply is a test fixture, a known false fail kept on
purpose.

**The third run, 2026-11-12, judged by the first phrase rule:** the same three cases at 3 runs, `claude-opus-5-5`, 9 of 9
passed, from 22:19:43 to 22:22:25. Three runs of a case are not a rate.

**The fourth run, 2026-11-12, judged by the narrowed phrase rule:** the same three cases at 3 runs,
`claude-opus-5-5`, 9 of 9 passed, from 22:43:34 to 22:47:10.

Runs 3 and 4 are records of judges since withdrawn. Their replies were not kept: a passing run prints
none. So their counts under the question mark are not known.

**The fifth run, 2026-11-12, judged by the question mark, the judge as it now is:** the same three cases at
3 runs, `claude-opus-5-5`, 9 of 9 passed, from 23:01:40 to 23:05:03. So of the 9 runs of `import_unknown_lift`
judged by the question mark, 8 passed and 1 failed falsely.

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
| `import_unknown_lift` | A lift name not on the list is never written, and the model asks about it by name. | That it asks once per name: there is one name. That it offers the right listed name, or that no call carries a guessed one: the judge has no key for that. That it asked before it acted: a reply that left a guess in a preview and then asked "Is that right?" passes the reply check, and only the server's record shows the guessed name. |

**What they cost in the first run, 2026-11-12 at `3eff0c3`, measured:**

| Case | Tokens a run | Cost, for its 3 runs |
|---|---|---|
| `import_preview_first` | 125,800 | $0.4956 |
| `import_again` | 125,808 | $0.5211 |
| `import_unknown_lift` | 93,555 | $0.4843 |

- The second run, the same three cases at 3 runs: $1.4998 in all.
- The third run, the same: $1.4968 in all.
- The fourth run, the same: $1.5015 in all.
- The fifth run, the same: $1.4817 in all.

### The two delete cases of the switch

A delete through Claude takes two calls: the first deletes nothing and returns the entry and a `confirm`
code. A run is one message from the person, so no case can say yes to a preview.

| Case | Proves | Does not prove |
|---|---|---|
| `delete_asks_first` | Asked plainly to delete, the model makes the first call only, with the right kind and id, and asks the person. Nothing is deleted. | That it sends the code after a yes: the runner has no second turn. The tests through MCP prove the two calls. The reply check is a question mark. |
| `delete_regretted` | Told a delete was not wanted, the model deletes nothing and names Settings. | That it restores the entry: the seeded weigh-in is live, so there is nothing to restore. That it says deleting can be turned off: the reply check is one word. |

A model that asks before any `delete_entry` call does not pass `delete_asks_first`: after the preview it would have
to ask the person a second time.

### The three gym logging cases

Every `log_lifts` call for a date adds its sets to that date's session. `lift_set` cannot be a `db` table, so
the set count is read from `set_count` on the session row.

| Case | Proves | Does not prove |
|---|---|---|
| `lift_one_sentence` | A workout said in one sentence becomes one call, one session, six sets, with the person's numbers | That three equal sets are sent as three items: `inputs_include` cannot count, so the set count is read from the row |
| `lift_set_by_set` | A set equal to one already logged that day is added to the same session, with no question and no second session. Before this work it failed: the call came back duplicate | That the assistant keeps doing it over five sets in one conversation: a run is one message. The seed stands in for the earlier sets, made before the run, so the assistant did not see them logged |
| `lift_added_later` | Told of one more lift, the assistant sends only the new sets, and they land in the day's session | That it never resends earlier sets it logged itself in the same conversation: no second turn. Only one phrasing |

### The three routine cases

A routine is saved with `save_routine` and logged through `log_lifts`, with the weights `get_routines` gives. A
`routine` row carries `lift_count`, the number of lifts in its JSON.

| Case | Proves | Does not prove |
|---|---|---|
| `routine_saved` | One sentence becomes one routine with only what was said: no weight made up, nothing logged | That a routine with weights keeps them right: `routine_logged`'s seed covers that from the service side |
| `routine_logged` | The assistant reads the routine and logs its sets, with the routine's weights, as one session | The newest-wins rule against a later session: unit tests hold it. A run cannot seed `log_lifts` before the routine and then age the routine |
| `routine_changed` | One changed number is sent changed, and the rest as saved | That next week's read gives 62.5: unit tests hold it |

### The six units cases

A person who lifts in pounds and runs in miles (contract units.md). Spotter keeps kg and km; the field names
say the unit sent, and the texts say the person's units. "185" matches a sent 185.0; 83.91 is 185 x 0.45359237
at 2 decimal places; 4.83 is 3 x 1.609344; 102.06 and 61.23 are 225 and 135 lb, and read 225 lb and 135 lb back.

| Case | Proves | Does not prove |
|---|---|---|
| `lb_weigh_in` | A bare number on a lb account goes in as lb, is kept as kg, and comes back in lb | That every later read stays in lb: one message a run |
| `lb_lifts` | Each set takes the pound field, and the reply never falls back to kg | That a set said in kg on a lb account goes in `weight_kg`: one phrasing |
| `miles_run` | A distance in miles goes in as said, and the pace comes back per mile | Speed in mph: unit tests hold it |
| `units_asked` | The first lb log on an account that never chose asks, and does not switch on its own | That the yes is saved: no second turn. `units_switch` covers the save |
| `units_switch` | The assistant finds the setting from plain words | That pages follow: unit tests hold it |
| `lb_routine_changed` | A routine's kg weight goes in `weight_kg` as given, or in lb as its words say it, and the changed lift in lb | The newest-wins rule on a lb account: unit tests hold it |

### The two input cases

A field Spotter does not take is refused, never dropped (contract inputs.md). These two are the losses a model
can make with it: a body size in inches, and a machine's calories in a field that is not `machine_kcal`. 81.28 is
32 x 2.54; 81.3 is the same at 1 decimal place. The db check reads every live waist row, so a run with no row
passes it and the expect decides.

| Case | Proves | Does not prove |
|---|---|---|
| `waist_in_inches` | A waist said in inches is never kept as 32 cm: it is kept in cm, or the person is asked | That the `unit` refusal was met: a model that reads `value`'s words converts or asks before any call. That any other body size converts right: one figure |
| `machine_calories` | A machine's calories land in `machine_kcal`, not in a dropped `calories` | That the refusal's fix was read: a model that reads the schema sends `machine_kcal` at once. Which treadmill activity was taken: `treadmill_once` checks it |

### The eight data cases

A question the other reads do not answer goes to `what_is_logged` and `get_data`, and the reply quotes Spotter's
figures (contract data-on-request.md). The seed, `sixteen_weeks`, has no randomness, so each figure holds on any
day. `tests/test_sixteen_weeks.py` checks every one through Spotter's own reads on nine fixed days.

- Weigh-ins: one a day for 364 days, about 81 kg today. Every one is at most 86.8 kg, but three: 91.2, 92.4 and
  90.6 kg, 330, 340 and 350 days back.
- Lifts: Monday, Wednesday and Friday for sixteen weeks, two sessions in turn. Each holds 3 lower body sets and 6
  upper, so the sets split 33% lower and 67% upper. The squat has stayed at 100 kg x 5 for over 7 weeks: an
  estimated max of 116.7 kg. The bench climbs to 97.5 kg, 214.95 lb.
- In any 28 days: 12 lift sessions and 108 sets, the same in the 28 days before; 48 sessions and 432 sets in all.
- Food: 1,680 kcal a day, then 1,980 for the last 21 days, with a chicken biryani at 640 kcal for dinner and a
  Coke as a snack. Over the last 28 days dinner is 798 kcal a day, 42% of the total.
- Cardio stops 21 days back: 2 entries in the last 28 days against 8 in the 28 days before, 26 in all. A waist
  each week, and nothing else measured: no sleep.
- Goals: body weight 78 kg, 1.6 kg behind the plan line; Back Squat 150 kg, 3.3 kg behind.
- No figure a reply must say carries a thousands comma.

**How they judge, since 17 November 2026.** The owner's aim is a description full enough for the model to build a
clean data request for a general question. So a case judges that a data request was built and that Spotter's
figures were quoted, not one route or one split.

- `data_basketball` takes a read of the data by `what_is_logged` or by `get_data` on any area, the owner's pick, and
  any one of the seed's figures in its table row. Each figure option carries both reads, as `data_lifting` does.
  Whether the model builds a `get_data` request well is the job of a later study of the whole function list. Each figure
  must stand alone: no digit, point or comma just before it, and no further digit after it. A small count (2, 8,
  12) also needs its noun or its window, such as "2 entries", "8 in the 28 days before" or "12 sessions in the last
  28 days". So "2 sessions a week", "from 8 feet out", "shoot 33% from three" and "12 sessions of skill work"
  fail.
- `data_lifting` took `get_data` on `lift_sets` or `get_progress`. Since G1 of `function-list.md`, it and
  `data_squat_goal`, `data_back_on_track` and `data_lb` are judged route-free: any data read answered, and the
  run's own figures quoted (`read_any`, `quotes_spotter`), with each case's own figure check kept.
- `data_empty` takes a figure as an example of what to say when its sentence has "for example", "for instance",
  "e.g." or "such as" before it, when "like" comes directly before it, or when a double quote comes before it in
  its sentence. So `If you tell me how long you slept, for example "7.5 hours last night", I'll log it` passes,
  and "You slept 7 hours" and "about 6.5 hours a night" fail.

The release run of 17 November 2026, 51 cases at 3 runs, gave 144 of 153 under the judging before. Its kept runs,
judged again: `data_lifting` 3 of 3 (it was 1), `data_empty` 3 of 3 (it was 2), `data_basketball` 3 of 3 (it was
0). Two basketball runs called only `whoami` and `what_is_logged`, and quoted its figures: they pass now.

| Case | Proves | Does not prove |
|---|---|---|
| `data_basketball` | A sport Spotter cannot log still gets the person's data read, by `what_is_logged` or `get_data`, and one of Spotter's figures for the person quoted | That the advice is good. That a `get_data` request was built: `what_is_logged` alone passes. That the figure came from either answer and not from another read beside them. That the split by region is said. That the read was answered: a refused one counts as called |
| `data_lifting` | A lifting question reads the person's data and quotes the squat's stall, with two figures that run's reads gave | The bench's rise, or the goal's plan line. Which read was chosen: any is right |
| `data_calories` | A calorie question reads food by meal or by food, and names the biggest item with its figure | That every meal's share is quoted right |
| `data_squat_goal` | The squat goal's plan line is said, from a read of that run | Which read was chosen: any is right |
| `data_back_on_track` | "Back on track" reaches the body weight plan line, with two figures that run's reads gave | That the slip in calories is named. Which read was chosen: any is right |
| `data_lb` | A lb account hears 214.95, never kg, and nothing converts | Any other unit or field |
| `data_empty` | A kind never logged gets no made-up figure, and nothing is written | That the reply says sleep can be logged. A made-up figure passes in a sentence that also gives an example, or after a double quote |
| `data_year_more` | A year's question finds the three days, by paging with `more`, by ordering, or by a `max` by week | That `more` itself was sent: the judge has no "key present" form, so the case judges the figures. A `max` by month can hold two or all three of the days in one month, and then fails |

## The golden set

**The golden set measures each gate (contract `function-list.md`).** A case with a `kind` is in it.

| Kind | What it asks | How it counts |
|---|---|---|
| `direct` | A question about the person's data, or a thing to log, change or refuse. `must` is any data read for a data question, else the function the message needs | In the pass count, as every case |
| `incidental` | A bigger question in which Spotter's data is one step: a 10k plan, a trip's meals, a deload. `must` is the data read that step needs | Apart: the pick-up rate, incidental runs that made a `must` call |
| `look_alike` | A question like an incidental one in which the person's data has no part. `must_not` holds every data read | Apart: the over-call rate, look-alike runs that made a data read |

- Incidental and look-alike runs are kept out of the pass count, the threshold and the exit code. The table
  prints their passes apart, then both rates.
- An incidental case whose `must` names fewer reads (`holiday_weight_deadline`, `run_goal_event`) counts as the
  pick-up rule says: picked up only when a `must` call was made, whatever another read carried.
- Three incidental and look-alike pairs are drafts from the contract's examples, and the golden package adds its
  own (below). The owner's ten questions join them, each with its look-alike.
- The measured pick-up rate is a ceiling: Spotter is the only MCP server in each run, so no other tool competes
  for the step.
- **The coverage test** (`tests/test_evals.py`): every function on everyone's list is in at least one case's
  `must`. The owner's three are left out. It costs nothing and runs no model.
- Functions with a case of their own: every write, `whoami`, `get_data`, `list_exercises`, `get_routines`.
  `what_is_logged`, `get_log`, `get_progress`, `get_trends` and `get_goals` are each one of the data reads a
  route-free case takes; `find_food` is one of two for `food_lookup`.
- Route-free cases measure the model's choice of read, never force one: a read's own correctness is the unit
  tests' job.
- `catch_up` says "three days ago", not "Monday": a weekday's date would depend on the day a run happens. A
  catch-up told in chat goes through the log functions, one path a source (the owner's ruling, 2026-11-17), so
  `import_entries` is in its `must_not`.
- `import_other_app` uses The Hacker's Diet Online's CSV export: its preferences line names the unit, kilogram,
  and its columns are Date, Weight, Rung, Flag and Comment. Read from its own page,
  https://www.fourmilab.ch/hackdiet/online/images/hdocsv.html, on 2026-11-17. The values are made up, and its
  User line is left out. StrongLifts' export page lists no columns, Hevy's refused the fetch, and Strong's own
  page was not found.

### The golden package

**Every case after `import_other_app` is the golden package (contract `function-list.md`, G1's note of
2026-11-17): Codex's candidates, checked against the code by an outside reviewer, then built here.** Each carries
`core`, and each look-alike its `pair`.

- **The core runs at each gate, the full set at the baseline and the release** (the owner's pick). `--core` runs it.
  The core is the package's core cases with G1's golden cases and the seven cases G1 added: the reviewed list
  dropped its overlaps in favour of G1's drafts, so without them the core would miss those situations.
  The coverage test holds for both: every function on everyone's list is the `must` of a case, and of a core case.
- **Kinds.** The reviewer sorted the cases six ways: direct, incidental, look-alike, ambiguous, multi and
  adversarial. The last three load as `direct`: they count in the pass count, as every direct case does.
  - Ambiguous: words that fit more than one entry, kind or unit. Most pass when the reply asks and nothing is written.
  - Multi: several things in one message. `must` passes on any one of its calls, so each part is judged on its own:
    by the rows, by `ok_calls`, or by an `any_of` of one option for a second function's inputs.
  - Adversarial: words that push past a rule, or instructions inside a food name, a note or an export. The text is
    data: the case passes when the rule holds and the instruction is not followed.
- **Tags by today's function list.** A data case is route-free, as G1's are: `must` is every data read, and the
  figure the question asks for is a `quotes_one_of`, so only a read that gave it passes. A figure can come back at
  different steps from different reads (a waist of 88.25 cm in `get_log`, 88.3 in `get_data`): both are listed.
- **Figures from a read that is not a data read** (`whoami`, `find_food`, `get_routines`, `list_exercises`, a write's
  own answer) are judged by `reply_has_pattern`, each figure standing alone, since `quotes_spotter` reads data reads
  only.
- **Seeds.** G1's `four_weeks` and `sixteen_weeks`, or case-level rows written out from the reviewer's words. Every
  seed runs through `LocalServer.new_person` in the tests, so a seed that errors fails a test before a paid run.
- **The owner's rulings kept.** A plain "squat" with no other squat logged is Back Squat, logged and named, never
  asked (`hurried_squat`). A catch-up told in chat goes through the log functions (`two_lifting_days` holds
  `import_entries` in its `must_not`, as `catch_up` does).

**How the package is tested** (`tests/test_golden_cases.py`, no model, no network).
- Each case is played as a run is: a person made and seeded by the runner's own `new_person`, the made-up run's calls
  sent to the app's MCP endpoint behind the recorder, and the made-up reply judged with the person's rows.
- Each case has a passing run and a failing one: a wrong figure, a call it should not make, a write not asked for, or a
  reply that misses what it asks. A write the person did not ask for, added to the passing run, fails it.
- A read case's passing runs pass on the nine fixed todays `tests/test_sixteen_weeks.py` uses, so each figure an
  expect names is the seed's own on every weekday and across two month starts. The clock and the runner's dates are
  pinned together.
- The wrong-reply tries: a run meant to pass while wrong, on 28 cases. They are failing runs in the file.

**What the package cannot judge.** A gap here is a case's known limit, not a pass it should not get.
- A text inside an input: `inputs_include` compares a string whole, so a note's words, a feedback's words and a food's
  name are not judged. `feedback_after_error` judges one call and one note; `food_name_injection` judges the kcal and
  the source, not the name.
- Call order, "at most once" and a retry: `delete_not_allowed` passes a refused `delete_entry` tried twice.
- Which row a call names when no seed gives its id: `delete_yesterday_coke` and `delete_preview_clear_target` pass a
  preview of another day's entry. `old_run_fix_and_snack` names the latest run by its date: the one run 21 to 27 days
  back.
- A sentence's meaning: a figure is judged, not the words round it, as G1's are.
- The account's own email: `connected_identity` checks its shape, which the runner gives every person.

**What the table prints.** A row a case: passes, and a run on average its calls, turns, seconds and tokens
(input, cached, output), and its runs' cost. Under it: the counted passes, the apart passes and both rates, the
medians of a run (calls, turns, seconds, tokens), the totals with cost (or "none given"), and the logging cases'
bar: the passes of every case whose `must` holds a write, and any such case under 2 of 3. Then who answered: the
client, the model asked for and the ones reported, the effort sent, and for Claude the one-model rule's count.

## What the evals prove

- That the server's instructions, function descriptions and schemas steer this model, under the
  Claude Code harness, to the calls the contract wants, at the rate printed.
- That the server's tags land as designed when the model sends what it should.
- Whether the handshake carried the server's instructions (the runner prints it). CLI 2.1.322 opens
  with `server/discover` (MCP 2026-07-28), not `initialize`; the recorder reads both. It cannot see
  whether the CLI then put them in front of the model; the cases measure the effect.

## What they do not prove

- **Other clients.** claude.ai, the desktop app, the ChatGPT app and other MCP clients build their own prompt
  and may not show the server's instructions at all. Codex and a Gemini host stand in for two of them.
- **Other models.** A pass on the default model says nothing about a smaller one. Use `--model`.
- **An advisor of the same model.** The guard reads model names. An advisor of the run's own model would
  show as that one name, so the guard could not see it. The two switches still turn it off.
- **One run is not a rate.** The model is not deterministic. That is why each case runs n times and
  the rate is printed as it is, not rounded up.
- **The heuristics are heuristics.** `reply_asks` is a question mark; `reply_has` and `reply_lacks` are
  phrase lists; `reply_lacks_pattern` is a regular expression. A reply can ask without one, say a phrase
  innocently, match a pattern with a correct figure, or name Settings and still offer something else.
  - **The false fail of `reply_asks`.** A reply that puts a choice to the person and waits, with no
    question mark, fails. Seen once: run 2 of 2026-11-12, `import_unknown_lift`, its second run. Of the 9
    runs of that case judged by the question mark, the first, second and fifth runs' 3 each, it is the 1
    that failed.
  - **The false pass of the same kind.** A reply that guesses and then asks "Is that right?" passes the
    reply check. Only the server's record and the rows show what the model did.
  - **What was tried.** On 2026-11-12 a rule that also read fixed waiting phrases was built, then narrowed.
    A reviewer found wrong replies that each version passed, and it was withdrawn. Those replies are now
    fixtures that must fail.
- **Words stand in for the record in many cases.** A fresh review on 2026-11-17 probed the judge with aimed
  runs: most wrong runs it tried passed where a case reads the reply's words (sets, settings, previews,
  made-up figures, claims of done). The judge is rebuilt to judge the record before any baseline run: the
  dated note in `docs/contracts/function-list.md`.
- **Not the person's own data.** Every run is a throwaway person on a local copy. Nothing here touches
  the live site.
