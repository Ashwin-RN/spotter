# Evals

**The evals check that Spotter's instructions and schemas steer Claude to the right call, measured
from what the server received, over repeated runs.**

## When evals run

Evals cost money, so they run once per release, and only when `python -m evals.need main` says yes.

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
- A change to `evals/cases.json`, `evals/judge.py` or `evals/run.py` counts too.
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
  was measured, nineteen since `import_entries`, twenty since `send_feedback`, twenty-two since `save_routine`
  and `get_routines`.
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

- `switch` sets what the person lets Claude do (`log` or `log_delete`) through the Settings
  page's own service before the run. Left out, it is the default, `log_delete`. A case that sets
  anything else, `read` among them, stops the load.
- `seed` rows are made through Spotter's services, in order: `save_food`, `log_bodyweight`,
  `log_measurement`, `log_lifts`, `set_goal`, `import_entries`, `save_routine`, `update_settings`, each
  with `data`, and `four_weeks`, which takes no data.
  - `update_settings` sets the person's settings, such as `weight_unit` lb, called as the web calls it. It
    returns no id, so it has no `as`.
  - `four_weeks` (`evals/four_weeks.py`) is four weeks of everything and three goals in one call, on an empty account. It has no `as`.
  - `import_entries` returns no one id either, so it has no `as`. Its rows carry fixed dates.
  - `edit_entry` edits a row seeded before it, so the person has an edit to undo. Its `data` is
    `{kind, entry_id, changes}`, all three, and `entry_id` is that row's `{{seed.<as>}}`.
- `db` checks the person's live rows, or with `"state": "deleted"` the soft-deleted ones.
  - The tables: `food_entry`, `saved_food`, `bodyweight`, `measurement`, `cardio_entry`, `lift_session`, `goal`,
    `feedback`.
  - A `lift_session` row carries `set_count`: how many sets it holds.
  - A note is never kept as deleted: a deleted note's row is gone. So every feedback row is live.
  - A goal has no delete, so every goal row is live. Check `status` instead.

### Placeholders

A placeholder is a whole string. It works in `seed`, `expect` and `db`.

| Placeholder | Becomes |
|---|---|
| `{{today}}` | The person's today, `YYYY-MM-DD`, in their zone. |
| `{{yesterday}}` | The day before. |
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
| `delete_asks_first` | Delete my last weigh-in (one weigh-in; Claude may delete) | `delete_entry` with kind `bodyweight` and that weigh-in's id, one success, never with `confirm`; the reply asks; the weigh-in still live, none deleted |
| `unknown_exercise` | Log 3 sets of 8 on the zorbinator at 20 kg | `list_exercises` called or the reply asks; `log_lifts` never called; no lift session |
| `goal_date` | I want to be 72 kg by the end of March (two weigh-ins, about 80 kg) | `set_goal` with kind bodyweight, target 72, `target_date` `{{next_month_end:3}}`, once; one goal row, down, open |
| `how_am_i_doing` | How am I doing? (the `four_weeks` seed) | `get_trends` called; no write function called |
| `on_track` | Am I on track? (the `four_weeks` seed: three goals) | `get_goals` called; no write function called |
| `export_is_a_click` | Export my data | No write function called; the reply says "Settings" |
| `delete_is_a_click` | Delete my account (one weigh-in; Claude may delete) | `delete_entry` never called; the reply says "Settings"; the weigh-in still live |
| `drop_goal` | Drop the squat goal (a Back Squat goal and a Bench Press goal) | `set_goal` with the squat goal's id and status dropped, one success; squat dropped, bench still open |
| `decimals_and_dashes` | How am I doing? (79.2 kg yesterday, 79.3 kg today, no goal) | `get_trends` called; no write function called; no em dash or en dash in the reply; no body weight at two decimals |
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

"No write function" is every write, by name, in `no_call`: the names in `WRITE_FUNCTIONS` in
`app/services/common.py`. A write added later must be added to those lists.

### What the six stage 5 cases prove, and what they do not

| Case | Proves | Does not prove |
|---|---|---|
| `goal_date` | The model turns "the end of March" into the next 31 March and sends it as the date. The server stores the goal pointing down. | Any other month or phrasing. A date said on the last day of a month. |
| `how_am_i_doing` | The question goes to `get_trends`, and nothing is written. | That the reply quotes the numbers right. Other calls beside it, such as `get_goals`, are allowed. |
| `on_track` | The question goes to `get_goals`, and nothing is written. | That the reply quotes the numbers right, or reads the statuses right. |
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
  - **The false fail of `reply_asks`.** A reply that puts a choice to the person and waits, with no
    question mark, fails. Seen once: run 2 of 2026-11-12, `import_unknown_lift`, its second run. Of the 9
    runs of that case judged by the question mark, the first, second and fifth runs' 3 each, it is the 1
    that failed.
  - **The false pass of the same kind.** A reply that guesses and then asks "Is that right?" passes the
    reply check. Only the server's record and the rows show what the model did.
  - **What was tried.** On 2026-11-12 a rule that also read fixed waiting phrases was built, then narrowed.
    A reviewer found wrong replies that each version passed, and it was withdrawn. Those replies are now
    fixtures that must fail.
- **Not the person's own data.** Every run is a throwaway person on a local copy. Nothing here touches
  the live site.
