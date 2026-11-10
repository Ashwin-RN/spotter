# Stage 4 contract: food and cardio

**Goal: a person says what they ate and what they did, in one message, and every number that lands carries an honest tag saying who supplied it: the person, the app, the USDA table, or Claude's guess.**

## The first-use path, walked first

A new person, nothing saved, nothing set.

| Step | From Claude | What the server does | On the web |
|---|---|---|---|
| "Lunch: two rotis and dal" | Nothing is saved yet, so Claude estimates and sends each entry with `estimated: true` | Tags both `claude_estimate`; the meal slot comes from the person's clock, nobody is asked | Today shows Lunch: 2 rotis 240 kcal (estimate), dal 180 kcal (estimate); the day's total and its estimated share |
| "Dal was 150" | `edit_entry` with `kcal: 150` | The tag becomes `you_said` | Today shows dal 150, no longer an estimate |
| "Save roti: one roti is 120 kcal" | `save_food` | A saved food with its serving | The Foods page lists it |
| "Two rotis" (next day) | `log_food` with the food's id and `servings: 2`, no number | The app works out 240, tag `saved_food` | Today shows it with its tag |
| "A banana" | `find_food("banana")`; saved and recent first, then USDA when the key is set | USDA answers are cached | |
| "30 minutes on the treadmill" | `log_cardio` | Calories from the Compendium MET and the person's current body weight; no weigh-in, no estimate and a note | Today's Cardio shows minutes, pace when there is a distance, the estimate and the machine's number side by side |
| "Set my target to 2,200" | `update_settings(calorie_target_kcal=2200)` | Until set, totals show with no comparison | Settings shows the targets |

What a new person does by hand: nothing. Saved foods are a convenience built by talking.

## What exists at the end

- Sixteen MCP functions and two resources.
- Food and cardio on Today, a Foods page, targets on Settings.
- The first eval cases, run with `claude -p` against a local copy with a throwaway person.
- No new migration: the tables exist since stage 2. Cardio rows join the shared exercise list.

## Decisions for this stage

| # | Decision | Why |
|---|---|---|
| 1 | An estimate stays tagged `claude_estimate` until the person states a number; then Claude edits the entry and the tag becomes `you_said`. "Yes, that's right" is the same number, stated | One mechanism, and the tag always says who supplied the number |
| 2 | `log_food` takes one call with many entries, each with its own meal and tag; all-or-nothing; the duplicate guard compares the whole set, as `log_lifts` does | A meal is described in one breath |
| 3 | A meal slot not given is picked by the server from the person's local time: 4 to 11 breakfast, 11 to 16 lunch, 16 to 22 dinner, else snack (so 22:00 to 04:00 is a snack: half past midnight is not breakfast). Claude passes a slot only when the person named one | Nobody is asked a question the clock answers |
| 4 | The server derives the tag; the caller never names it. A saved food or a USDA food is logged by id and amount, never with a number; a number with an id is refused | The tag is enforced by code where code can enforce it (plan 4c). Only `estimated` is Claude's honesty, and the eval measures it |
| 5 | USDA is built behind `USDA_API_KEY`. Without the key, `find_food` searches saved and recent foods and says USDA is off; a USDA id cannot be logged. With it, USDA results come after the person's own, and are cached | Cut order 4: the stage ships either way |
| 6 | The USDA client uses the standard library (`urllib`), a 5-second timeout, and the two cache tables. No new package | The whole lookup is one GET |
| 7 | Cardio calories: MET × 3.5 × kg ÷ 200 × minutes, rounded to a whole number, MET from the 2024 Adult Compendium of Physical Activities for the activities offered, kg = body weight now (`metrics.bodyweight_now`). No weigh-in: no estimate, a note. The machine's number is stored and shown beside it, labelled | The plan's rule. The Compendium licence (CC BY-NC-ND 4.0) allows this with attribution, and only the offered activities are stored |
| 8 | Cardio best efforts and cardio goals are stage 5. This stage shows cardio on Today and in `get_log`, with pace, and `get_progress` for an activity lists sessions and the last 7 days' minutes against the weekly target (a rolling week, so Monday never shows zero) | Best efforts need the goal kinds stage 5 defines |
| 9 | `update_settings` gains `calorie_target_kcal`, `target_sessions_per_week`, `target_cardio_minutes_per_week`, `add_back_exercise_kcal`. Still never the permission switch | Cheap, and the day total needs the first |
| 10 | Net calories show only when `add_back_exercise_kcal` is on (default off): the day's food minus its cardio estimates (the machine number when there is no estimate) | A setting, not a default |
| 11 | Editing a food entry's `kcal` makes it `you_said` and clears its food id, servings, USDA id and grams; editing its description or meal keeps the tag. Editing a cardio entry re-estimates its calories with the same weigh-in rule | An edited number is the person's number |
| 12 | No Indian food table values are shipped (IFCT 2017 and INDB are not openly licensed). Saved foods and the person's own numbers cover it; the doc says so | Research fact that binds the design |
| 13 | Evals: plain sentences, each with the call that should follow. They run with `claude -p` on the person's subscription against a local server with a throwaway person that resets each run, never on live data. The runner reads the tool calls from Claude's own JSON output, and the tags from the throwaway database. It reports a pass rate over repeated runs and tokens per case | Plan 4g and D6. The call log holds no arguments by design, so the transcript is the source |

## Rules added to every function

13. **The tag is the server's.** `source` is never an input. It is `saved_food` when a food id is given, `usda` when a USDA id is given, `claude_estimate` when `estimated` is true, else `you_said`.
14. **A number and an id never travel together.** `kcal` with `saved_food_id` or `usda_fdc_id` is `invalid_field`, with the fix "the app supplies the number for a saved or USDA food".
15. **Attribution travels with the estimate.** Wherever a cardio estimate is shown or returned, the Compendium is named: in the result's `estimate.source`, in `can-and-cant`, and in one line on the web page that shows it.

## Service signatures, fixed before any package starts

```
log_food(user_id, data, by)
    data: {entries: [{description, meal?, kcal?, estimated?, saved_food_id?, servings?, usda_fdc_id?, grams?}],
           date?, allow_repeat?}
save_food(user_id, data, by)          data: {name, serving_label, kcal_per_serving, grams_per_serving?}
                                      the same name (casefolded) updates the saved food; deleted ones come back live
find_food(user_id, query, limit=8)    read; saved and recent first, then USDA when the key is set
log_cardio(user_id, data, by)
    data: {exercise, minutes, date?, distance_km?, incline_pct?, speed_kmh?, avg_hr?, machine_kcal?, notes?, allow_repeat?}
update_settings(user_id, data, by)    + calorie_target_kcal, target_sessions_per_week, target_cardio_minutes_per_week,
                                        add_back_exercise_kcal
edit_entry(user_id, kind, entry_id, changes, by)
    + food_entry   {date?, meal?, description?, kcal?}
    + cardio_entry {date?, exercise?, minutes?, distance_km?, incline_pct?, speed_kmh?, avg_hr?, machine_kcal?, notes?}
get_log(user_id, date=None, days=1)   each day gains food and cardio (shapes below)
get_progress(user_id, exercise=None)  a cardio activity: its sessions and the last 7 days' minutes against the target
list_exercises(kind, category)        cardio rows now exist: kind "cardio", categories below
```

### Result shapes

```
log_food   -> {kind: "food_entry", ids: [...], duplicate, entries: [{id, date, meal, description, kcal, source,
               estimated, saved_food_id, servings, usda_fdc_id, grams}], day: {total_kcal, estimated_kcal,
               estimated_share_pct, target_kcal, remaining_kcal, net_kcal}, summary, links: {day, foods}[, note]}
               note when any entry is an estimate: "N entries are Claude's estimates: the person can confirm or
               correct them, and the day total says how much of it is estimated."
save_food  -> {kind: "saved_food", id, created: bool, food: {id, name, serving_label, kcal_per_serving,
               grams_per_serving}, summary, links: {foods}}
find_food  -> {query, saved: [{id, name, serving_label, kcal_per_serving, grams_per_serving, times_logged, last_logged}],
               recent: [{description, kcal, source, last_logged, times}]  (the person's own you_said and estimate
               entries, most frequent first, up to limit), usda: [{fdc_id, description, data_type, kcal_per_100g}] | null,
               usda_note: str | null  ("USDA lookup is off: no key is set." or "USDA did not answer: use a saved food
               or the person's own number."), summary}
log_cardio -> {kind: "cardio_entry", id, duplicate, entry: {...columns...}, pace: {min_per_km: "5:36", speed_kmh} | null,
               estimate: {kcal, weight_kg, met, source: "2024 Adult Compendium of Physical Activities, code 12050"} | null,
               machine_kcal, week: {minutes, target_minutes}, summary, links: {day, progress}[, note]}
get_log day += food: {meals: {breakfast: [entries], lunch: [...], dinner: [...], snack: [...]}, total_kcal,
                      estimated_kcal, estimated_share_pct, target_kcal, remaining_kcal, net_kcal}
               cardio: [{...entry, exercise, pace, estimate, machine_kcal}]
               totals += {food_entries, cardio_entries}
```

`net_kcal` is null unless `add_back_exercise_kcal` is on. `remaining_kcal` is null without a target.

### Ranges and kinds

| Field | Rule |
|---|---|
| `description` | 1 to 200 characters, `rules.text` |
| `kcal` | 0 to 5,000 per entry, whole number stored |
| `servings` | 0.1 to 50, two decimals |
| `grams` | 1 to 5,000 |
| `meal` | `breakfast`, `lunch`, `dinner`, `snack`; matched like a kind (case and spaces forgiven) |
| `kcal_per_serving` | 0 to 5,000; `serving_label` 1 to 64 characters; `name` 1 to 128 |
| `minutes` | 1 to 600, one decimal |
| `distance_km` | 0.01 to 300 |
| `incline_pct` | 0 to 40 |
| `speed_kmh` | 0.1 to 60 |
| `avg_hr` | 30 to 240, whole |
| `machine_kcal` | 1 to 5,000, whole |
| `calorie_target_kcal` | 800 to 10,000, whole; `target_sessions_per_week` 0 to 14; `target_cardio_minutes_per_week` 0 to 3,000 |

Pace is minutes per km as `m:ss` when a distance is given; `speed_kmh` is stored as given or worked out from distance and minutes. A given speed that differs from distance ÷ minutes by more than 20% is refused, with both numbers in the message: an entry never contradicts itself. A worked-out kcal (saved food × servings, USDA × grams) is held to the same 0 to 5,000 as a stated one. A back-dated session's estimate uses body weight now, and its summary says so.

### Cardio activities

About fifteen rows in `app/exercises.py`, `(name, category, met, compendium_code)`, kind `cardio`, categories `walk`, `run`, `cycle`, `row`, `swim`, `machine`, `other`. Names are what a person says: Treadmill Walk, Treadmill Run, Outdoor Walk, Outdoor Run, Stationary Bike, Outdoor Cycling, Rowing Machine, Elliptical, Stair Climber, Jump Rope, Swimming, Hiking, Circuit Training, Badminton, Football. Each MET value is taken from the 2024 Adult Compendium with its code, at a moderate, general intensity; a comment names the Compendium row. The reviewer checks the values against the Compendium.

## The MCP surface

| Function | Kind | Needs | Arguments |
|---|---|---|---|
| `find_food` | Read | `read` | `query`, `limit?` |
| `log_food` | Write | `log` | `entries` (list of `{description, meal?, kcal?, estimated?, saved_food_id?, servings?, usda_fdc_id?, grams?}`), `date?`, `allow_repeat?` |
| `save_food` | Write | `log` | `name`, `serving_label`, `kcal_per_serving`, `grams_per_serving?` |
| `log_cardio` | Write | `log` | `exercise`, `minutes`, `date?`, `distance_km?`, `incline_pct?`, `speed_kmh?`, `avg_hr?`, `machine_kcal?`, `notes?`, `allow_repeat?` |
| `update_settings` | Write | `log` | + the four fields |
| `edit_entry` | Write | `log` | + `meal?`, `description?`, `kcal?`, `exercise?`, `minutes?`, `distance_km?`, `incline_pct?`, `speed_kmh?`, `avg_hr?`, `machine_kcal?` |

- Instructions and `can-and-cant` say the tag rule in Claude's terms: a number the person stated goes as `kcal` with `estimated` false; a number Claude worked out goes with `estimated: true`, always; a saved or USDA food goes by id and amount with no number; when `find_food` has no match and the person gave no number, Claude may estimate and must say so in its reply.
- `can-and-cant` gains: the four tags and who supplies each; the meal-slot rule; the cardio estimate's formula, its weigh-in rule and the Compendium attribution; what USDA covers and that no Indian table values are shipped; that `net_kcal` appears only with add-back on.

## The web

| Page | Adds |
|---|---|
| `/day/{date}` | Food by meal with each entry's tag ("estimate" marked), the day's total, target and remaining, the estimated share, net when add-back is on; a one-line food form (description, kcal, meal, which logs as `you_said`); Cardio with minutes, distance, pace, the estimate and the machine number side by side; a cardio form (activity, minutes, distance); Edit and Delete on each, as stage 3 |
| `/entries/food_entry/{id}/edit`, `/entries/cardio_entry/{id}/edit` | The fields decision 11 allows |
| `/foods` | Saved foods: name, serving, kcal, times logged; add, edit, delete (soft) |
| `/settings` | Calorie target, weekly sessions, weekly cardio minutes, add-back, through `update_settings` with `by="web"` |
| Footer line on any page that shows a cardio estimate | "Calorie estimates use MET values from the 2024 Adult Compendium of Physical Activities (CC BY-NC-ND 4.0)." |

Every form behind CSRF; every write through `logged_write`; no new script; CSP unchanged.

## Evals

- `evals/cases.json`: each case is `{id, say, expect: {function, inputs_include?, inputs_exclude?, no_call?}, db?: {...rows expected after}}`.
- `evals/run.py`: starts the app on a free port with a temporary SQLite file and a throwaway person, mints a token the way the tests do, writes an MCP config for `claude -p` with that server and an `Authorization` header, runs each case `n` times (default 3) as a fresh conversation with `--output-format json`, records every `tools/call` the server received (an ASGI wrapper around the app: the ground truth is what reached Spotter, not the model's account of it, and the CLI's JSON output carries no tool calls anyway), reads the database for `db` checks, and prints a table: case, passes out of n, tokens per run. Exit code 1 when any case is under the threshold given (default: every run passes).
- First cases, at least: a stated number goes as `you_said`; a guessed number carries `estimated: true`; a saved food goes by id and servings with no number; an unsaid meal slot is not asked about and not passed; "yesterday" gets yesterday's date; "30 minutes on the treadmill" is one `log_cardio`; a repeated "Waist 84" in a fresh conversation is sent (a call is made); "delete that" asks or is refused, never silently done; an unknown exercise leads to `list_exercises` or a question, not an invented name.
- `docs/evals.md` says how to run them and what they do not prove. The runner's parsing has unit tests; nothing in the suite calls the model.

## Work packages

**Four packages. A, B and C start together on disjoint files; E starts when A and C report. Nobody edits `tests/conftest.py`.**

| | A: services | B: web | C: MCP surface | E: evals |
|---|---|---|---|---|
| **Files** | `app/services/food.py`, `app/services/cardio.py`, `app/services/usda.py` (new); `app/services/log.py`, `entries.py`, `account.py`, `common.py`, `__init__.py`, `progress.py`; `app/exercises.py`, `app/rules.py`, `app/metrics.py`, `app/config.py`; tests: `test_food.py`, `test_cardio.py`, `test_usda.py` (new), `test_log.py`, `test_entries.py`, `test_update_settings.py`, `test_isolation.py`, `test_rules.py`, `test_metrics.py`, `test_progress.py`, `test_config.py`, `test_schema.py` | `app/web.py`, `app/web_auth.py`, `app/templates/`, `app/static/`, `tests/test_web.py`, `tests/test_csrf.py` | `app/mcp_server.py`, `app/mcp_schemas.py`, `app/calllog.py`, `docs/can-and-cant.md`, `tests/test_mcp.py`, `tests/test_isolation_mcp.py`, `tests/test_calllog.py`, `tests/test_same_account.py` | `evals/` (new), `docs/evals.md` (new), `tests/test_evals.py` (new) |
| **Waits for** | Nothing | Nothing (builds against the signatures) | Nothing (same) | A and C |
| **Exit check** | Tag derivation and rule 14 tested for every combination; USDA with a fake transport (hit, miss, timeout, bad key, cache); the estimate with and without a weigh-in; pace; meal from the clock at the four boundaries; `edit_entry` for both kinds incl. re-tag and re-estimate; isolation over food, saved food and cardio; the exercise sync adds cardio rows idempotently; every write limited | Pages with none, one of each, many; both forms; both edit pages; Foods page add/edit/delete; targets on Settings; the attribution line present wherever an estimate shows; CSRF on every POST; call-log rows | Sixteen functions; NEEDS complete; schemas validate for real results incl. nulls; marker test over new string arguments; text tests; the tag rule in the instructions; isolation through MCP for food and cardio | The runner's parsing tested; a dry run against the local server with `n=1` on at least three cases, the pass rate reported honestly |

## Exit test

1. The full suite is green, plain and with warnings as errors, on a clean export.
2. Live, with Today open: save roti (120 kcal a roti) by talking; then "Two rotis and dal, then 30 minutes on the treadmill" in one message → one `log_food` with two entries (roti by id, servings 2, tag saved; dal with a number, tag estimate) and one `log_cardio` → Today shows the meal with both tags and the cardio with its estimate (or the no-weigh-in note) → the day's total equals 240 plus the dal estimate.
3. "Dal was 150" → the tag flips to you said; the total follows.
4. The evals: every first case passes at least 2 of 3 runs, the rate reported as it is.
