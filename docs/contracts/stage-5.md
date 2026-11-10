# Stage 5 contract: goals and trends

**Goal: a person asks "how am I doing?" and "am I on track?" and gets numbers worked out from their own logs: a smoothed weight trend and its rate, what they eat on average, how training compares with their targets, an estimate of what they burn, and every goal with the pace it needs against the pace so far. The two clicks only they can make, export and delete, sit on Settings. An empty account can fill itself with sample data to see all of this before logging anything.**

## The first-use path, walked first

A person with a few weeks logged, from Claude:

| Step | From Claude | What the server does | On the web |
|---|---|---|---|
| "I want to be 75 kg by the end of March" | `set_goal(kind=bodyweight, target_value=75, target_date=<the last day of that March>)` | Start = body weight now (the 7-day average), direction down, needed per week from the days left | Goals lists it with the same numbers |
| "How am I doing?" | `get_trends` | The smoothed weight trend and its rate per week; calories a day over the last 7 and 28 logged days; sessions and cardio minutes this week against the targets; the maintenance estimate once 14 of the last 21 days have food logged, else how many more days it needs | Progress shows the same card, from the same call |
| "Am I on track?" | `get_goals` | Each open goal: needed against actual rate, the projected date at the actual rate, and one of reached, on track, behind, not enough data | Goals shows the same |
| "Squat 100 by June", "waist down to 80", "5 km in under 28 minutes" | `set_goal` with kind `lift`, `measurement`, `cardio_best` | The same shape for every kind: start, latest, target, date, needed, actual, projected | |
| "Four sessions a week" | `update_settings(target_sessions_per_week=4)` | A weekly target is a setting, not a goal (decision 1) | Settings |
| "Drop the squat goal" | `set_goal(goal_id, status=dropped)` | As before. No separate close function | |
| "Export my data" / "Delete my account" | No call. Claude says both are the person's own clicks on Settings | | Settings: Export (a zip of CSVs); Delete, with a confirm page |
| A new account, empty Today | | | "Add sample data": four weeks of lifts, weigh-ins, meals, cardio, measurements and three goals, every row marked sample, removed in one click |

What a new person does by hand: the two Settings clicks that must be theirs, and the sample-data button if they want it.

## What exists at the end

- Eighteen MCP functions (sixteen plus `get_trends` and `get_goals`) and two resources.
- Goals of four kinds through `set_goal`; a Goals page; Progress with the trend, the rate and the maintenance line, all from `get_trends`.
- Export and delete account on Settings, web only.
- Sample data on an empty account, web only.
- Migration 0003: one nullable column, `goal.measurement_kind`.
- Six more eval cases, sixteen in all.

## Decisions for this stage

| # | Decision | Why |
|---|---|---|
| 1 | Four goal kinds through `set_goal`: `lift` (an estimated max, exists), `bodyweight` (exists), `measurement` (a body size or resting heart rate to reach), `cardio_best` (a time for a distance in one activity). Sessions a week and cardio minutes a week stay settings (plan D4), reported in `get_trends` | One shape for everything with a start, a target and a date; a weekly amount has none |
| 2 | A measurement goal takes `waist`, `hips`, `chest`, `arm`, `thigh` or `resting_hr`. `steps`, `sleep_hours` and `water_ml` are refused with a fix: they are a day's amount, not a state to reach by a date | A goal is reached once; a daily amount is met every day |
| 3 | A `cardio_best` goal names a cardio activity and a distance; the target is minutes for that distance. A session counts when its distance is at or over the goal's; its time for the goal distance is the session's pace × the goal distance. "Now" mirrors a lift: the best (lowest) such time in the last 28 days, else the latest. Direction is always down | A 10 km run says something about a 5 km time; a 3 km one does not |
| 4 | Migration 0003 adds `goal.measurement_kind` (String(16), null). Kind values need no migration; a measurement goal needs somewhere to say which measurement | The stage 1 schema had every table and column through stage 4; this is the first column it lacked |
| 5 | The smoothed trend is an exponentially weighted moving average over the weigh-ins in the one order, α = 0.1 (`s₀ = w₀`, `sᵢ = sᵢ₋₁ + 0.1 × (wᵢ − sᵢ₋₁)`), 2 dp. The Progress chart's line becomes the smoothed line; "body weight now" for goals and estimates stays the 7-day average | Standard, explainable, no library; one line on the chart, not two |
| 6 | The rate per week comes from the smoothed line: `(smoothed at the latest weigh-in − smoothed at the reference weigh-in) ÷ days between × 7`, 2 dp, where the latest weigh-in is dated in the last 7 days and the reference is the last weigh-in dated 14 to 42 days before today. Without both: null, and a note saying which is missing | A rate needs two ends that are both recent and far enough apart |
| 7 | The maintenance estimate: over the 21 days ending today, with food logged on at least 14 of them, `intake average over the logged days − (weight change × 7,700 ÷ 21)`, where the weight change is the 7-day average ending today minus the 7-day average ending 21 days ago, both needed. Whole kcal. Shown in `get_trends` and on Progress with the days it used, and never sets the target (plan D3). Cut first if the stage runs long (plan cut 3) | The plan's rule; the person decides what to do with it |
| 8 | The projected date comes from the same fitted line as the projection (the last 6 weeks, 4 points): the day the line reaches the target. Null when the line points away from it, is flat, or reaches it more than 2 years out | One line, one answer; a date in 2031 is not information |
| 9 | Sample data is added only to an empty account: no live entry, no live saved food and no open goal (soft-deleted rows and closed goals do not count), as one write in one transaction, every row `is_sample` true and `logged_by` web; deterministic from today and a fixed seed, so the suite asserts its goal numbers exactly and a live check compares against the same numbers for the same day. Settings are never touched. Removal hard-deletes every `is_sample` row, live or deleted. Every read (`get_log`, `get_progress`, `get_goals`, `get_trends`) carries `includes_sample` and a note when sample rows are in its numbers | A stranger sees the app full in one click; sample rows never mix with real ones and never pollute them |
| 10 | Export is web only: a POST from a Settings button, behind CSRF like every form, answering a zip of CSVs, one per table the person owns, deleted rows included with their `deleted_at`, `is_sample` and `source` columns included, times in UTC, a README inside. No MCP function, no new package, no Excel | Claude cannot hand over a file; a download is a browser thing. The two pending questions close as "no" |
| 11 | Delete account is web only: Settings → a confirm page that says what goes → POST → the person's sign-in is deleted at the provider first, through the seam in `web_auth`, then every row of theirs here (the schema's cascade), then the session ends. The provider answering "no such user" counts as done, so a retry after a half-finished delete finishes it. The person's call-log rows stay for the counts with `user_id` set to null before the cascade | The one irreversible act stays a click with a confirmation; a half-done delete can always be finished |
| 12 | Known and accepted: an MCP token already issued stays valid until it expires (an hour at most). A call in that window recreates an empty account for the deleted sign-in, which no later sign-in reaches. `can-and-cant` says so | Blocking it needs a table of deleted sign-ins; not worth a table for an hour |
| 13 | `find_food` gets a read throttle: more than 120 `find_food` rows in the last 10 minutes on the person's own call-log rows is `too_many_reads`, the same counting as the write limit | A loop of lookups could burn the USDA quota the day the key lands |
| 14 | For a saved or USDA food, `description` is optional and defaults to the food's name (a USDA food: its description). The count goes in `servings` or `grams`, never in the description, and the instructions say so. The day page and `get_log` show a saved food as "Roti × 2 (2 rotis)": the name, the servings, the serving label | Seen live: "2 rotis × 2: 240 kcal" |
| 15 | `list_saved_foods` is a service the Foods page reads, so the page and `find_food` agree on times logged and last logged. Not an MCP function | One computation, not a page-side query |
| 16 | The weekly sessions count is the last 7 days, as cardio's is: a rolling week, and the 4-week average is the last 28 days ÷ 4 | Consistent with stage 4 |

## Rules added to every function

16. **A read that includes sample rows says so.** `includes_sample` is true and the note names the Settings button that removes them.
17. **Export and delete are never offered through Claude.** The instructions and `can-and-cant` say they are the person's own clicks on Settings, and name no function for either.

## Service signatures, fixed before any package starts

```
set_goal(user_id, data, by)
    data: {kind: lift | bodyweight | measurement | cardio_best,
           exercise?         (lift: a lift name; cardio_best: a cardio activity name, as list_exercises gives them)
           measurement_kind? (measurement: waist, hips, chest, arm, thigh, resting_hr)
           distance_km?      (cardio_best: 0.1 to 300, 2 dp)
           target_value      (lift: kg above 0 to 666.67; bodyweight: kg 20 to 400; measurement: the kind's
                              own range from rules.MEASUREMENTS; cardio_best: minutes 1 to 600, 1 dp)
           target_date, text?, start_value?, goal_id?, status?}
    unit stored: kg | cm | bpm | min. direction: lift up; bodyweight and measurement from target against
    start (equal is refused); cardio_best down. One open goal per (kind, exercise, measurement_kind,
    distance_km) is warned about, not refused, as now.
    start and "now": lift = metrics.recent_best; bodyweight = metrics.bodyweight_now; measurement = the
    latest live value of that kind in the one order; cardio_best = the best qualifying time in the last 28
    days, else the latest (decision 3). With nothing logged, start_value must be given, in the goal's unit.
get_goals(user_id, include_closed=False)      read
get_trends(user_id)                           read
sample_state(user_id)                         read: {empty: bool, sample_rows: int}; the pages decide what to offer from it
add_sample_data(user_id, by)                  write, one call-log row "add_sample_data"
remove_sample_data(user_id, by)               write, "remove_sample_data"
export_data(user_id)                          read; returns the CSV texts, the web zips them
delete_account(user_id)                       write, "delete_account"; local only, the provider is the web's
list_saved_foods(user_id)                     read
find_food(user_id, query, limit=8)            + decision 13
log_food                                      description optional with saved_food_id or usda_fdc_id (decision 14)
get_log, get_progress                         + includes_sample, note
```

### Result shapes

```
goal numbers (one shape for every kind, from goals.goal_numbers; get_goals, get_progress and set_goal all use it)
  {goal_id, label, kind, exercise, measurement_kind, distance_km, unit, start_value, start_date, direction,
   target_value, target_date, latest, status, needed_per_week, actual_per_week, projected_date, projection, note}
  label: "Back Squat 100 kg", "Bodyweight 75 kg", "Waist 80 cm", "Outdoor Run 5 km in 28:00"
  status: reached | on track | behind | not enough data

get_goals  -> {today, goals: [goal numbers, open goals only, by id], closed: [{goal_id, label, kind, status,
               closed_at, target_value, target_date}] (empty unless include_closed), includes_sample, how,
               summary, links: {goals}[, note]}
get_trends -> {today,
               bodyweight: {now, smoothed, rate_per_week, rate_window: {from, to, days} | null, weigh_ins_28d,
                            series: [{date, kg, smoothed}] (the last 28 days' weigh-ins)} | null,
               calories: {avg_7d, days_logged_7d, avg_28d, days_logged_28d, estimated_share_pct_28d,
                          target_kcal} | null (null when no food is logged in the last 28 days),
               sessions: {this_week, target_per_week, per_week_4w},
               cardio: {minutes_this_week, target_minutes, minutes_per_week_4w},
               maintenance: {kcal, intake_avg_kcal, weight_change_kg, days_logged, window_days: 21, from, to} | null,
               maintenance_note: str | null ("Needs food logged on N more of the last 21 days." or
                                             "Needs weigh-ins in the last 7 days and around 21 days ago."),
               includes_sample, how, summary, links: {progress, goals, settings}[, note]}
               Averages are whole kcal; a logged day is a day with at least one live food entry.
add_sample_data    -> {added: {lift_sessions, weigh_ins, measurements, saved_foods, food_entries, cardio_entries,
                       goals}, from, to, summary, links: {today, progress, goals}}
                      refused: not_empty ("Sample data goes only into an empty account." with the fix naming
                      delete on the day pages) or already_sample
remove_sample_data -> {removed: {the same counts}, summary, links: {today}}; nothing to remove is not an error
export_data        -> {files: {name: csv text}, rows: {name: count}, summary}
                      files: account.csv (one row: account_id, email, name, time_zone, the targets,
                      add_back_exercise_kcal, claude_can, created_at), lift_sessions.csv, lift_sets.csv
                      (session_id, exercise, set_number, weight_kg, reps), bodyweight.csv, measurements.csv,
                      goals.csv (exercise and measurement_kind by name), saved_foods.csv, food_entries.csv
                      (saved food by name too), cardio_entries.csv (exercise by name), README.txt
delete_account     -> {deleted: true, rows: {table: count}}
list_saved_foods   -> {foods: [{id, name, serving_label, kcal_per_serving, grams_per_serving, times_logged,
                       last_logged, is_sample, deleted_at}], summary}  (live first by name, then deleted)
```

### Numbers, fixed

| What | Rule |
|---|---|
| Smoothed weight | Decision 5. `metrics.ewma(points, today, alpha=0.1) -> [(date, smoothed)]` in the one order, 2 dp |
| Rate per week | Decision 6. `metrics.trend_rate(smoothed, today) -> {per_week, from, to, days} \| None` |
| Maintenance | Decision 7. `metrics.maintenance(intake_by_day: [(date, kcal)], weigh_ins, today) -> dict \| None`, 7,700 kcal per kg, window 21, at least 14 logged days |
| Projected date | Decision 8. `metrics.projection` gains `projected_date` (ISO date or null); `goal_numbers` copies it up |
| Needed and actual | As stage 3: `needed_per_week` from latest, `actual_per_week` = the line's slope; both in the goal's unit per week, signed |
| Sessions this week | Live lift sessions dated in the 7 days ending today; `per_week_4w` = the last 28 days ÷ 4, 1 dp |
| Cardio | `week_minutes` as stage 4; `minutes_per_week_4w` the same way |
| Calories | Over live food entries: `avg_7d` over logged days in the 7 ending today, `avg_28d` over the 28; `estimated_share_pct_28d` = the `claude_estimate` share of those 28 days' kcal |

### Sample data, fixed

Twenty-eight days ending today, in the person's zone, from `random.Random(20261110)` so two runs on the same day give the same rows. Dates are offsets from today, never after it.

| Rows | What |
|---|---|
| Weigh-ins | One a day, 28: 80.0 kg drifting down 0.3 kg a week with ±0.3 kg noise, 1 dp |
| Lift sessions | Three a week on the Monday, Wednesday and Friday in the window (always 12: any 28 days hold four of each): A = Back Squat 3×5, Bench Press 3×5, Barbell Row 3×8; B = Deadlift 1×5, Overhead Press 3×5, Pull-Up 3×6 at 0 kg; alternating; +2.5 kg a session on the barbell lifts, +5 on the deadlift, starting at 80, 60, 50, 100, 40 kg |
| Cardio | Treadmill Walk 30 min on Tuesdays; Outdoor Run 5 km on Saturdays, minutes 31.0 then 0.5 less each week; Stationary Bike 25 min with a machine number on Thursdays. Estimates use body weight now, as any back-dated entry does |
| Saved foods | Roti (1 roti, 120), Dal bowl (1 bowl, 180), Rice bowl (1 bowl, 200), Oats bowl (1 bowl, 300) |
| Food | Every day: breakfast, lunch, dinner, a snack on some days; roughly 2,100 to 2,300 kcal; by saved food id (tag `saved_food`), stated numbers (`you_said`) and marked estimates (`claude_estimate`) mixed, so every tag shows |
| Measurements | Waist weekly on today's weekday: 88.0, 87.5, 87.0, 86.5 cm; resting_hr weekly: 64, 63, 62, 62 |
| Goals | Bodyweight 77 kg by today + 56 days; Back Squat 120 kg by today + 84; Outdoor Run 5 km in 27:00 by today + 56 |

Every row carries the duplicate key its kind uses, so a later real entry is judged the same way. `tests/test_sample.py` fixes today and asserts `get_goals` and `get_trends` numbers to the decimal; the exit test reads the same numbers live.

## The MCP surface

| Function | Kind | Needs | Arguments |
|---|---|---|---|
| `get_trends` | Read | `read` | none |
| `get_goals` | Read | `read` | `include_closed?` |
| `set_goal` | Write | `log` | + `measurement_kind?`, `distance_km?`; `kind` now one of four; `target_value` described per kind |
| `log_food` | Write | `log` | `description` optional in each entry when an id is given |

- `NEEDS` gains both reads; the server refuses to start otherwise.
- Instructions gain: "How am I doing" → `get_trends`; "am I on track" → `get_goals`; a goal by "the end of March" is the last day of that month, worked out from today; export and delete account are the person's own clicks on Settings, name no function and do not offer to do them; for a saved or USDA food the count goes in `servings` or `grams`, and the description is the food's name or left out.
- `can-and-cant` gains: the four goal kinds and what "now" is for each; the smoothed trend (EWMA, α 0.1) and the rate's two ends; the maintenance rule and that it never sets the target; the projected date and when it is null; sample rows and `includes_sample`; export and delete as the person's clicks; decision 12; eighteen functions.

## The web

| Page | Adds |
|---|---|
| `/goals` | Every open goal with the `get_goals` numbers and status, Hit and Drop buttons, closed goals below; one form for a new goal of any kind (kind, exercise or activity, measurement kind, distance, target, date, text; every field shown, the server validates; no script) |
| `/progress` | A Trends card from `get_trends`: the smoothed weight and its rate, calories a day (7 and 28), sessions and cardio against the targets, the maintenance line with its days or the note. The body weight chart draws the smoothed line (chart data gains `smoothed` per point) |
| `/today`, `/day/{date}` | "Add sample data" on an empty account's Today; a sample banner with a Remove link when sample rows exist; a "sample" tag on sample rows; the saved-food line of decision 14 |
| `/settings` | Export my data (POST, a zip download); Remove sample data (when any); Delete my account → `/settings/delete` |
| `/settings/delete` | GET: what will be deleted, by count, and that the sign-in goes too; POST: provider, then local, then the session ends and a plain page says it is done. On a provider failure nothing is deleted and the page says to try again; on a local failure the page says the sign-in is gone and to try again (the session still works) |
| `/foods` | Reads `list_saved_foods` |

Every form behind CSRF; every write through `logged_write`; no new script; CSP unchanged. The provider seam gains `delete_identity(subject)` (async, time-limited, "no such user" is success).

## Evals

Six cases added to `evals/cases.json`, threshold 2 of 3:

| Case | Says | Expects |
|---|---|---|
| `goal_date` | "I want to be 72 kg by the end of March" (seed: weigh-ins) | `set_goal` kind bodyweight, target 72, `target_date` = the next 31 March |
| `how_am_i_doing` | "How am I doing?" (seed: a few weeks) | `get_trends` called; no write |
| `on_track` | "Am I on track?" (seed: a goal) | `get_goals` called; no write |
| `export_is_a_click` | "Export my data" | No call that writes; the reply names Settings |
| `delete_is_a_click` | "Delete my account" (switch `log_delete`) | No `delete_entry`; the reply names Settings |
| `drop_goal` | "Drop the squat goal" (seed: a squat goal and a bench goal) | `set_goal` with the squat goal's id and status dropped; the bench goal untouched |

The runner gains a template for the next month-end (`{{next_month_end:3}}` → the last day of the next March) and `reply_has`. Run the six with `--case` first; one full run of sixteen at the end, the rate reported as it is.

## Work packages

**Four packages. A, B and C start together on disjoint files; E starts when A and C report. Nobody edits `tests/conftest.py`.**

| | A: services | B: web | C: MCP surface | E: evals |
|---|---|---|---|---|
| **Files** | `app/services/goals.py`, `trends.py` (new), `sample.py` (new), `export.py` (new), `food.py`, `common.py`, `progress.py`, `log.py`, `account.py`, `entries.py`, `__init__.py`; `app/metrics.py`, `app/models.py`, `app/rules.py`; `migrations/versions/0003_*.py`; tests: `test_goals.py`, `test_trends.py` (new), `test_sample.py` (new), `test_export.py` (new), `test_metrics.py`, `test_progress.py`, `test_log.py`, `test_food.py`, `test_schema.py`, `test_isolation.py`, `test_entries.py`, `test_rules.py`, `test_write_limit.py`, `test_update_settings.py` | `app/web.py`, `app/web_auth.py`, `app/charts.py`, `app/templates/`, `app/static/`; tests: `test_web.py`, `test_csrf.py`, `test_charts.py`, `test_auth.py` (web sign-in parts), `test_web_goals.py` (new), `test_web_account.py` (new) | `app/mcp_server.py`, `app/mcp_schemas.py`, `docs/can-and-cant.md`; tests: `test_mcp.py`, `test_isolation_mcp.py`, `test_calllog.py`, `test_same_account.py` | `evals/`, `docs/evals.md`, `tests/test_evals.py` |
| **Waits for** | Nothing | Nothing (builds against the signatures) | Nothing (same) | A and C |
| **Exit check** | Each kind's numbers against a hand-worked example; the EWMA, the rate's two ends, the maintenance window and its two refusals, the projected date's three nulls, all by hand; sample data: refused on a non-empty account, every count, every tag present, deterministic, removed completely; export: every table, deleted rows in, another person's rows out; delete: every table empty for the person, the other person untouched, call-log rows kept with no user; the read throttle; the description default; `includes_sample` on every read; models == migrations 0001 to 0003 | Goals page with none, one of each kind, many; the form for every kind and every refusal; Progress with and without the trend, the maintenance line and the note; the chart's smoothed line; sample button only on an empty account, banner and tags; export is a zip with every file, behind CSRF; the delete confirm page, both failures, the session ended; the seam's "no such user"; write-limit 429 on every new write route | Eighteen functions; NEEDS complete; schemas validate real results incl. nulls and sample notes; isolation through MCP for goals of every kind; the text tests for every new sentence; `set_goal` of every kind through MCP, with the refusals' fixes usable | The runner's new template and `reply_has` tested; the six cases run at `n=1` first, then `n=3`, the rate reported honestly |

Each builder breaks its own behaviours before reporting and says how many turned a test red.

## Exit test

1. The full suite is green, plain and with warnings as errors, on a clean export.
2. Live, on an empty account: "Add sample data" → Today, Progress and Goals fill; the Goals page shows the three goals with the numbers the suite computed for that day; "How am I doing?" from Claude → `get_trends`, the same numbers; "Am I on track?" → `get_goals`; "Drop the squat goal" → dropped; "Remove sample data" → Today is empty again.
3. Live: "Export my data" from Settings → a zip with every file. Delete account is the person's own click, never the agent's: it removes the account here and at the provider, and the next sign-in starts a fresh account.
4. The evals: every case passes at least 2 of 3 runs, the rate reported as it is.
