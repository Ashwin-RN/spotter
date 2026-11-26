# Logic audit contract: one home for each rule, and the write paths agree

**Goal: code nothing reads is gone. Log, edit and import key and check an entry alike where they differ today, and a test keeps it so. Two saves that lose a value stop losing it. The pages ask the services for every figure. The pages, the README and the older contracts say what is built.**

From a review of the logic on 2026-11-24, read row by row against `main` at `4e9c4fb`. Approved by the owner on 2026-11-24 and 2026-11-25, with the picks under "Decisions". Line numbers are from `4e9c4fb`: each builder reads each line again on the tree it starts from.

## For the owner

**What changes for a person.**

| What | Today | After |
|---|---|---|
| A saved food saved again with its grams left out | The grams are cleared | The grams are kept. The Foods page clears them with an empty field |
| A kcal sent again at the number an entry already holds, on an entry logged from a saved or USDA food | The entry loses its link, its servings and its grams | Nothing changes. An estimate still becomes the person's number, as before |
| Two open body weight goals | The Progress chart draws whichever the database gives first | The one set first (the lower id) |
| After a change of time zone, a weigh-in dated tomorrow and one logged today | `get_data` gives the first for today, `get_trends` the second | Both give the second |
| A cardio session with a distance, edited, then sent again as it now stands, within 10 minutes | Written a second time | Answered as a duplicate |
| A portion edited to an invisible character | Refused | Cleared, as a log reads it |
| Settings, "Read and log" | Says the assistant cannot delete an entry | Also says a second weigh-in or measurement on a day still replaces the first |
| The Foods page, the first-day page, the landing | Three stale lines | Each says what the page does |

Nothing an assistant reads changes: no description, no schema, no instruction, no line of `docs/can-and-cant.md`. One answer changes by an existing line: the same-number kcal above now answers "nothing changed".

**No eval run is expected.** "What a model reads" says how the code keeps `python -m evals.need main` at no.

**No migration, no new package, one deploy.**

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **`log`'s inner shapes stay as they are.** Each kind's shape becomes `log`'s own when the first new kind is designed. Gates G2 to G5 of `docs/contracts/function-list.md` are held by the function rework, the work that made `log` and `guide`: a dated note there, nothing built | Doing it now moves no byte an assistant reads, yet costs the eval judge, many test files and an eval run, and may be built twice |
| 2 | **The two saves that lose a value are fixed now, with no description touched.** (a) A left-out `grams_per_serving` is kept on a plain save over a saved food. (b) A kcal sent at the value already held restates only an estimate | Lost data comes first. An undo reaches only the newest version, so one more edit puts the lost value beyond any function |
| 3 | **The Settings line for a "Read and log" account is made true by its words.** No refusal is added | A refusal would close one door of two (a plain log still replaces), and needs new words an assistant reads |
| 4 | **"On the plan line" stays judged in kg.** A dated note on `docs/contracts/units.md` | No stored or queried number is wrong, and the other way moves words an assistant reads |
| 5 | **`get_data`'s day takes its weigh-in by the one order** (effective date, then id), as `get_trends` and the 28-day grid do | Two reads gave two weights for one day |
| 6 | **`owner_calls` with an email keeps every matched account**, with `failed_only` too. Each line's `failed_calls` says it. The docstring says so and a test holds it | The mode exists to list every account an email is |
| 7 | **Kept on purpose:** `charts.pace_svg` and its CSS, `web.PLACEHOLDERS`, the `field` filter, `guide.TOPICS`, the four description names and `LOG_ROUTING` in `app/log_shape.py`, `fields.INTERNAL_COLUMNS` and `OTHER_TABLES`, the six stored copies (`food_entry.estimated`, `measurement.unit`, `lift_session.dedupe_key`, `user.issuer`, `user.updated_at`, `entry_version.request_id`), `data.py`'s own 28 (its published keys say `last_28d`) | Each has a reader, a planned page, or waits with decision 1 |
| 8 | **Edit stays a field by field update, and the import keeps its own wrapper.** Only the real differences close | A whole-row check on edit would judge rows stored under older rules. The import rewrites log's refusals by their words |

## Rulings on the how

| # | Ruling |
|---|---|
| R1 | **`last_top` goes; the test's own oracle stays.** `latest_sessions`' SQL is checked against `old_history` and `old_weights` in `tests/test_hardening.py:64-119`, which build the figure themselves. `lifts.history`'s `last_top` is a second copy with no reader in `app/`. So the key leaves `lifts.history`, `old_history` and `old_weights` stay whole, and `histories()` (`tests/test_hardening.py:264-266`) drops the key from the oracle's side only |
| R2 | **A goal's quantity has one home, `goals.goal_quantity(kind, unit)`.** `set_goal` can store five pairs: lift in kg, body weight in kg, a cardio best in min, a measurement in cm (waist, hips, chest, arm, thigh) or in bpm (resting heart rate). The three copies agree on all five. They differ only on a row no path can write: a measurement goal in steps, h or ml (`set_goal` refuses those kinds), where the service says steps, hours or ml, the page said cm and the charts said body weight. After this work the page and the charts say what the service says. On a unit the service does not know it raises, as `get_goals` itself does; the wrappers keep no fall-back. Only open goals reach a wrapper (`app/templates/goals.html:30`, `web.now_widened`), and each carries its `unit` (`app/services/goals.py:510`) |
| R3 | **The activity row's new key is `fastest_min_per_km`** (18 characters): the fastest pace as a number, minutes a km, or null. `get_cardio` is web only, so no published schema moves |
| R4 | **The chart's reads live in `progress.bodyweight_view(user_id, today=None)`.** It opens the session, reads the person, the weigh-ins and the open goals by id, takes the last `trends.WINDOW_DAYS` days, and calls `bodyweight_chart` by its module name with every argument by position, `exact` too (true for a person in lb). It answers `{"bodyweight": chart or None}`, and the same with no account. `web._trend(user_id, today=None)` keeps its name, its arguments and its answer, and becomes one call |
| R5 | **The cardio key on an edit.** After an edit the key takes the row's speed as none when it equals `metrics.speed(distance_km, minutes)` of the row as it now stands, the test `_cardio_speed` already uses (`app/services/entries.py:716`). No line of `app/services/cardio.py` moves. An imported row keeps its key, as today |
| R6 | **No portion.** One helper in `app/rules.py`, beside `required_text`: nothing, a blank, or only invisible characters is no portion; anything else is `required_text`'s answer. Log and edit both call it. It adds no words. On an edit, no portion clears it, as an empty one does today. An estimate's portion cleared on an edit is not refused: that needs new words and is not in this work |
| R7 | **Grams on a save.** The key's presence in the data decides. Absent: a live saved food keeps its grams, a new one has none. Present as null or `""`: cleared. A save with items clears grams left out, as today: a meal's numbers come from its items. A deleted food brought back by a plain save clears grams left out, as it takes only the macros sent. The Foods page sends `""` for an empty grams field. `save_food`'s description and `app/mcp_server.py` are not touched, so an assistant still cannot clear grams on purpose |
| R8 | **The same-number kcal.** `restated` holds only when the entry's source is `claude_estimate`. On a `saved_food` or `usda` entry a kcal at the value held is no change: the link, the servings and the grams stay, and with nothing else sent the answer is the existing "nothing changed" line. A changed kcal still makes it the person's number and clears the link (stage 4, decision 11) |
| R9 | **Dated notes.** Every note on an older contract is written in package D2, last. No builder edits this contract. `BUILD_LOG.md` is written after the review, by no builder |

## What a model reads, and the eval run

`python -m evals.need main` says yes when a description, an input or result schema, the instructions, a resource or `whoami`'s answer moves, and also when a string of 20 or more characters is added, changed, deleted or moved in any `app/**/*.py` but `app/web.py` and `app/charts.py`. Docstrings, comments and templates are free.

So, for every package:

- No description, schema, instruction, result field, `docs/can-and-cant.md`, `app/log_shape.py` or `app/guide.py` is touched.
- No string of 20 or more characters is added, changed, deleted or moved in a service file, `app/rules.py`, `app/metrics.py`, `app/users.py` or `app/mcp_server.py`. New code there uses short keys and existing words.
- A builder that cannot build an item inside these two lines leaves the item unbuilt and reports it.

Expected at the end, on the joined tree: no. A yes names the string or schema that moved and goes to the owner.

## Package A: unused service code

### What exists

| # | Name | Where | Readers |
|---|---|---|---|
| A1 | `RECORD_BEST` | `app/services/lifts.py:82-83` | none |
| A2 | The `UNIT_OF` name in the import | `app/services/imports.py:153-154` | none in `imports`; `rules.UNIT_OF` stays |
| A3 | `days_since_last`, `sessions_vs_target`, `weekly_sessions`, `_ages`, `TARGET_MAX_WEEKS`, `WEEKLY_CHART_WEEKS` | `app/metrics.py:80,83,297-345`, the module notes `:29-32` | seven tests in `tests/test_metrics.py:103-143` |
| A4 | `last_top` in `lifts.history` | `app/services/lifts.py:405-406,425,452,476` | `tests/test_routines.py:356`; the whole-dict compare in `tests/test_hardening.py:264-266` |
| A5 | `latest_tops` | `app/services/routines.py:634-637` | `tests/test_hardening.py:682,701` |
| A6 | `user_exists` | `app/users.py:123-125` | `tests/test_users.py:140`, its import `:16` |
| A7 | `clock` | `app/services/goals.py:168-170` | none |
| A8 | `_MISSING` | `app/mcp_server.py:434` | none |
| A9 | `owner_calls`' notes on the email mode | `app/services/owner.py:7-16` | The notes promise two things that clash: every matched account is listed, and `failed_only` keeps only the lines with a failed call. The code keeps every matched account (`:116-119`) |
| A10 | The module notes of `app/mcp_server.py` | `:62` | They say the write limit comes first in the wrapper. The wrapper runs none: each service runs its own, so a `log` call that the routing or the kind's model refuses never meets it |

### What changes

- A1 to A8: deleted, with the comments and docstring lines that name them (`app/services/routines.py:47,605`, the module notes of `app/metrics.py`).
- A4 and A5 by ruling R1. The two tests at `tests/test_hardening.py:682,701` call `latest_sessions` and take each lift's date and weight; they still compare with `old_weights`.
- A9: the notes say that with an email every matched account keeps its line, with `failed_only` too. No code changes.
- A10: the notes say where the write limit runs. Docstring only.

### Tests

- Deleted with their code: the seven in `tests/test_metrics.py` and its import; `tests/test_routines.py:356`; `tests/test_users.py:140` and its import.
- Changed: `tests/test_hardening.py:266` (R1), `:694-713` (A5).
- Added, in `tests/test_trace.py` near `:543-551`: an email of two accounts sent with `failed_only`, where one account has no failed call. Both keep their line.

## Package B: the write paths agree

### What exists

| # | What | Where |
|---|---|---|
| B1 | A cardio edit keys every stored number, a worked-out speed too. Log and import key only what was given | `app/services/entries.py:690`; `app/services/cardio.py:327-328` |
| B2 | A portion of only invisible characters is no portion on log and a refusal on edit | `app/services/food.py:265-273`; `app/services/entries.py:462-465` |
| B3 | `PER_100G`; a `del` that cannot run (a cardio entry has no editable kcal) | `app/services/food.py:105`; `app/services/entries.py:563-564` |
| B4 | `restated` is true for any source but `you_said`, so a same-number kcal clears a saved or USDA link | `app/services/entries.py:569,664-667` |
| B5 | The days area sorts a day's weigh-ins by real date, then id | `app/services/fields.py:444-446` |
| B6 | No test sends one candidate down all three paths | |

### What changes

- B1 by R5. B2 by R6. B3: both deleted. B4 by R8.
- B5: the sort key is the one order's, `metrics.order_key`, and the comment says so.
- B6: a new file, `tests/test_paths_agree.py`.

### Tests

`tests/test_paths_agree.py`:

- For a weigh-in, a measurement, a lift session, a cardio session with a distance, and a food entry logged alone: the same values logged, reached by an edit of another row, and imported give the same stored values and the same key (an imported row's key by the import's own formula, which is the log's).
- Each check written more than once refuses the same bad value at the same limit in each copy: both of kg and lb, a set's weight for its lift, reps outside 1 to 100, more than 60 sets.
- An invisible portion: no portion on log, cleared on edit, nothing refused.
- Known limits, pinned as they are: an edit of one food entry of a set of two keys that row alone; a stated speed equal to distance over minutes keys with the speed on log and without it after an edit.
- A difference this contract does not list is a finding: the builder reports it, changes no app code for it, and does not weaken the test to pass.

Elsewhere:

- `tests/test_entries.py`: the cardio line missing from the re-key test at `:234`; a same-number kcal on a saved-food entry and on a USDA entry keeps the link and answers "nothing changed"; the same with a meal also sent changes the meal and keeps the link.
- `tests/test_data.py`: a weigh-in dated after today with the lower id, one today with the higher: `get_data`'s day gives today's, as `get_trends` does. No test reads the real date.

## Package C: the pages ask the services

### What exists

| # | What | Where |
|---|---|---|
| C1 | `weight_now_label` types its own 6 days | `app/web.py:2889-2898`; the rule's home is `goals.now_basis` (`app/services/goals.py:334-348`) |
| C2 | Three copies of a goal's quantity | `app/services/goals.py:158-165`, `app/web.py:2984-2991`, `app/charts.py:59-60,564-566` |
| C3 | `fastest_in_miles` reads `get_cardio` once more for each paced activity | `app/web.py:2872-2886`; the number is already in `_activities` (`app/services/cardio.py:436-455`) |
| C4 | `_trend` opens the database itself, and its goal read has no order | `app/web.py:2811-2829`; `bodyweight_chart` takes the first open body weight goal it is given (`app/services/progress.py:312`) |
| C5 | The 28-day window is written in `trends.py:45`, `cardio.py:362`, `web.py:2808` | |
| C6 | `lift_chart`'s `choices` key and `bodyweight_chart`'s `average` key have no reader | `app/services/progress.py:280,316-318` |
| C7 | Four page lines are stale | below |
| C8 | `web.clock` (`:2978-2980`), `web.day_path` (`:1187-1188`), the `kind_names=` argument (`:1712`), the `weekday` filter (`:162`), the `too_many_writes` branch (`:1620-1621`) have no reader or cannot run | `app/web.py` |

### What changes

- C1: `weight_now_label` asks `goals.now_basis("bodyweight", points, today)`, with each point's kg (a point with no value is dropped there). `average_7d` is "7-day average"; `latest_weigh_in` and none are "latest weigh-in"; the `except` to "now" stays.
- C2 by R2: `web.goal_quantity(goal)` and `charts.goal_quantity(goal)` keep their names and call the service. `web.GOAL_QUANTITIES`, `charts.GOAL_QUANTITY` and `charts.UNIT_QUANTITY` go; `app/charts.py:571`, which asks whether a goal is in kg, reads `goals.ROUNDED`. No service imports `app.charts`: `tests/test_eval_need.py` holds that, and the builder runs it.
- C3 by R3: the row gains the key; `fastest_in_miles` reads it and makes no read of its own.
- C4 by R4.
- C5: `cardio.py` and the new `progress` function read `trends.WINDOW_DAYS`; `web.BODY_WEIGHT_DAYS` goes. If an import would make a cycle, the builder stops that item and reports it.
- C6: the two keys go, with the work behind `average` when nothing else reads it. The local `choices` stays.
- C7, the exact lines:

| Where | Now | After |
|---|---|---|
| `app/templates/foods.html:66` | "Saving a name you already have updates it." | "Saving a name you already have updates it. A saved meal is changed through your assistant." |
| `app/templates/hi.html:71` | "Your fixed workouts by name, each with its lifts and usual sets." | "Your fixed workouts by name, each with what you usually do in it." |
| `app/templates/home.html:120-126` | Today, Progress, Goals, Foods, Settings | Routines after Foods: `/routines`, "Routines", "Your saved routines." |
| `app/web.py:351-352`, "Read and log" | "Everything above except deleting: your assistant cannot delete an entry, a saved food, a routine or a program." | "Everything above except deleting. A second weigh-in or measurement on a day still replaces the first, which is kept." |

  Each line stands as written unless a text rule or a test forces a change; then the builder makes the smallest change and reports the final words. The landing's Routines row is always drawn, as Foods' is: `/routines` is always a page and Settings always links it. The first-day line keeps its own condition.
- C8: deleted.

### Tests

- Changed for the words: `tests/test_web_account.py:371,1490`, `tests/test_hi_there.py:175`, `tests/test_web.py:286-289`, `tests/test_programs.py:699`, `tests/test_routines.py:1148`.
- Changed for the keys: `tests/test_cardio.py:486-492` (whole rows), `tests/test_charts.py:165,180-183`, `tests/test_progress.py:202,227`, `tests/test_update_settings.py:262`.
- Rewritten: `tests/test_units_pages.py:801`. Its premise, a per-activity read that fails, is gone: a row with no fastest pace leaves its cell empty.
- Green as they are, by R4: `tests/test_charts.py:956,1044,1061`, `tests/test_web.py:6524,6566,7500,7576`, `tests/test_web_goals.py:1395`.
- Added: two open body weight goals, the chart draws the lower id's; the per-mile pace is drawn with no read for each activity; the label reads "latest weigh-in" on an account whose weigh-ins are all older than 28 days.

## Package B-grams: a saved food keeps its grams

Built after B and C: it is the one item in both `app/services/food.py` and `app/web.py`.

### What exists

`app/services/food.py:579-582` sets `grams_per_serving` to none before it reads the data, so a save that leaves it out clears it. The macros a serving keep a left-out value (`:573-577`). Through MCP a null is dropped before the service, so an assistant that saves a food again to change its kcal clears its grams. The Foods page leans on the clear: `food_form` (`app/web.py:2395-2399`) drops an empty grams field.

### What changes

By R7: `app/services/food.py:579-582` and `:601-602`, the module notes at `:62-63`, and `web.food_form`. The builder reads every caller of `save_food` in `app/`, `evals/` and `tools/`, and checks that an empty grams field on a food with no grams is still no change on the edit page.

### Tests

- Changed: `tests/test_edit_history.py:87-88` sends `grams_per_serving` as null, so the case still clears the grams and `:152` holds.
- Added, in `tests/test_food.py`: left out keeps; null clears; `""` clears; a save with items clears grams left out; a deleted food brought back plain clears grams left out; a new food with grams left out has none.
- Green as they are: `tests/test_web_account.py:605` ("Emptying the grams clears them"), `tests/test_web.py:2802`, `tests/test_edit_history.py:1178`.

## Package D1: the README and the eval notes

| Where | Now | After |
|---|---|---|
| `README.md:22` | "The first sign-in creates your account and saves your time zone." | "The first sign-in creates your account. Your time zone is saved when you first open a Spotter page, or your assistant sets it." |
| `README.md:150` | "Every write locks the person's row first, counts against a write limit, and passes the same checks whether it came from the assistant or a form." | "Every write locks the person's row first and passes the same checks whether it came from the assistant or a form. Every write but the Settings switch and deleting the account counts against a write limit." |
| `README.md:154` | "a read limit and a page limit per person" | "a read limit and a page limit per person (300 each in 10 minutes)", as `app/services/common.py:128-130` and `app/web.py:895-896` hold them |
| `README.md:155` | "one row per call" | "one row per function call" |
| `docs/evals.md:896,907,923,960` | "the core stays at 103" | The cases "add none to the core". The number 103 leaves the four places: the file and its test hold 104 |

## Package D2: the dated notes

One note at the foot of each file, under the heading `## Note, 2026-11-25: the logic audit (docs/contracts/logic-audit.md)` with the file name in backticks. A contract is a record: nothing above the note is rewritten. The builder reads the code behind each line before it writes it, and writes only what it read.

| Contract | The note says |
|---|---|
| `stage-1.md` | `:87` "No scopes are required": the server asks for the `openid` scope |
| `stage-2.md` | `:85` the exercise list is synced by the deploy's own step, not at start-up. `:89` one by-id read of a saved food goes by the session after `owned` has checked the id |
| `safety.md` | `:30` the page limit counts GET and HEAD |
| `stage-6.md` | `:1517` the Progress card carries the Today chart's words. `:1827` the landing follows the README's table of assistants. `:480-482` and `:2060`: the charts and the page now ask `goals.goal_quantity` and `goals.now_basis` (C1, C2). `:1730` and `:1838`: the two lines as C7 leaves them. The two chart keys of C6 are gone |
| `function-list.md` | Gates G2 to G5 are not built and are held by the function rework (decision 1), with the list of what that leaves as it is today: output schemas published, `get_data` taking one area, `what_is_logged` first, `spotter://exercises` registered, no `app/mcp_inputs.py`, no `docs/adding-a-feature.md` |
| `log-and-guide.md` | `:221` a call row's `word=` keeps the open words and the measure words. `:191` the wrapper runs no write limit (A10) |
| `edit-history.md` | `:40` `restore_entry`'s description names neither `day_taken` nor editing again; the refusal is built. `:31` a second undo past 90 days answers `nothing_to_undo`, as decision 6 says |
| `units.md` | Decision 10 (`:68`): the zero test of "on the plan line" stays in kg (decision 4 here). On a lb account a gap of 0.0227 to 0.05 kg reads on the line |
| `trace.md` | `:42` `owner_calls` with an email of none or several accounts gives a line for each matched account, and `failed_only` drops none of them (decision 6) |
| `routines-and-programs.md` | `:93` `latest_tops` is gone; `latest_sessions` is the one read. `:101` the label says "or one from get_routines". `:207` the page's left-out line is the shorter one |
| `tidy.md` | `:235-245` the landing's notes follow the README's cells, nine assistants |
| `routines.md` | `:70,100` `last_top` is gone from `lifts.history` (R1). `:172` the Settings line as C7 leaves it |
| `switch.md` | `:103` the Settings line as C7 leaves it, and why (decision 3) |
| `first-day.md` | `:168` the Routines line as C7 leaves it |
| `stage-4.md` | Decision 1 and decision 11: R8. The cardio key on an edit: R5 and its known limit |
| `macros-and-asking.md` | `:354` grams on a plain save: R7. A portion of invisible characters on an edit: R6 |
| `data-on-request.md` | `:295` the day's weigh-in goes by the one order (decision 5) |

## Work packages

Built one after another in one tree, in this order. No two builders at once.

| | A | B | C | B-grams | D1 | D2 |
|---|---|---|---|---|---|---|
| Waits for | nothing | A | B | C | B-grams | D1 |
| Kind | code | code | code | code | docs only | docs only |
| App files | `app/metrics.py`, `app/users.py`, `app/mcp_server.py` (`_MISSING` and the module notes only), `app/services/lifts.py`, `routines.py`, `goals.py`, `imports.py`, `owner.py` | `app/rules.py`, `app/services/entries.py`, `food.py`, `fields.py` | `app/web.py`, `app/charts.py`, `app/services/progress.py`, `cardio.py` (the read half, from `:360`), `app/templates/foods.html`, `hi.html`, `home.html`, `progress.html` (a comment) | `app/services/food.py`, `app/web.py` (`food_form`) | `README.md`, `docs/evals.md` | The 17 files of its table |
| Test files | `test_metrics`, `test_routines`, `test_hardening`, `test_users`, `test_trace` | `test_paths_agree` (new), `test_entries`, `test_data` | `test_web`, `test_web_goals`, `test_web_account`, `test_hi_there`, `test_programs`, `test_routines`, `test_cardio`, `test_charts`, `test_progress`, `test_update_settings`, `test_units_pages`; run, not edited: `test_eval_need` | `test_food`, `test_edit_history` | none edited | none edited |
| Exit check | Each definition of A1 to A8 is gone and nothing in `app/` reads it. These hits stay and are right: `last_top` in the oracle of R1, `rules.UNIT_OF`, `formatting.clock` and (until C) `web.clock`, and `sessions_vs_target` as an eval case's id in `evals/cases.json` and `tests/test_golden_cases.py`. No package touches `evals/`. The new trace test fails when `failed_only` is made to drop a line | Each new behaviour fails its test when broken once. No string of 20 or more characters moved | `web._trend` keeps its name, arguments and answer. The key of R3 is under 20 characters. The builder lists every goal kind and unit `set_goal` stores and each copy's answer, and says what the Goals page shows with two open body weight goals. The Settings words reported as built | The three ways of R7 each have a test. `app/mcp_server.py` unchanged | The docs tests green | No line above a note changed (`git diff` shows additions at each foot only). `test_data` green |

Each builder runs, once, after its last edit, plain and then with `-W error`: every test file that names a symbol it changed or deleted (by grep over `tests/`), and its package's test files. Never the whole suite. A test that goes red and is not named here is reported with its output, not rewritten to pass.

## Tests

Named in each package. The docs packages run `tests/test_docs.py`, `test_web.py`, `test_switch.py`, `test_text_rules.py`, `test_evals.py`, and D2 also `test_data.py`. No test touches the network or reads the real date, weekday or hour.

## Eval cases

None are added, changed or removed, and no package touches `evals/`. Read in `evals/cases.json`: the one case whose `edit_entry` sends a food kcal (`food_correction_and_water`) sends a new number to an entry the person stated; the four cases that name `grams_per_serving` each save a new food once on an empty account, so none saves again a food that holds grams. No case judges a page.

## The exit check

1. Every package's exit check holds.
2. One review of the joined tree on a frozen copy, then one fix round.
3. `python -m evals.need main` on the joined tree says no. A yes is reported with its reasons, not fixed by a guess.
4. The whole suite, once, on a clean export of the commit, before the push.
5. `git grep` finds no em dash in a file this work added or a line it changed.

## Known limits

| Limit | Why it stands |
|---|---|
| A stated speed that equals distance over minutes keys with the speed on log and without it after an edit | No column says whether a speed was given. The key is read only by the 10-minute guard, so the cost is one duplicate missed |
| An edit of one food entry of a set keys that row alone, so the same message sent again within 10 minutes writes the set again | The set rule on an edit has no clear right answer. Pinned in `tests/test_paths_agree.py` |
| An import row whose entries partly matched stores its new rows under a key over those alone | Pinned already in `tests/test_import.py`. A fix needs a match by entry, not a key change |
| An assistant cannot clear a saved food's grams on purpose | A null there is a change to `save_food`'s description |
| An edit can clear an estimate's portion | A refusal needs new words an assistant reads |
| `edit_entry`'s description and `docs/can-and-cant.md` say a kcal makes an entry the person's number, with no word on the same number | Their words are wider than R8 by that one case. Wording is taken together later |
| On a lb account, "on the plan line" is judged at the kg step | Decision 4 |
| The chart tests of the 7-day average at 1 decimal go with the `average` key | No page drew it. `metrics.bodyweight_average` keeps its own tests |
| A measurement goal in steps, h or ml, which no path can write, now shows in its own quantity on the page | R2 |

## Not in this work

`log`'s layers and gates G2 to G5 (decision 1). The zero test in the shown unit (decision 4). One home for the meal names and the name fold. The kind labels and the word for a cardio entry. A split of `app/web.py`. The privacy page. The three wording rows where the code does what was decided and a sentence says more: `allow_repeat` on a weigh-in, a later-dated entry "in every number", an estimate's portion on an edit. Any change to a description, the instructions or `docs/can-and-cant.md`.

## Notes from the build, 2026-11-25

Built as six packages in one tree, one after another, then one review on a frozen copy and one fix round. Where the build parts from the lines above:

| Line above | As built |
|---|---|
| C8, the `too_many_writes` branch of `claude_can_page` | Not built: the branch stays. `tests/test_web.py::test_a_limit_refusal_is_a_429_with_the_wait[switch]` stubs the save to answer that code, and went red without the branch. The branch cannot run today (`set_claude_can` is outside the write limit), so it goes only together with that test row. The other four lines of C8 are gone |
| Package C's test files | `tests/test_four_weeks.py` also changed: its expected activity rows gained `fastest_min_per_km` (R3), a whole-row compare like `tests/test_cardio.py`'s |
| Package C's app files, `cardio.py` "the read half" | Two lines above it changed too: `trends` joined the import line (C5), and one line of the module notes names the new number |
| Package C's tests | Two tests were renamed, as their titles named the `average` key: `test_the_chart_rounds_its_smoothed_points_and_keeps_each_weigh_in` (`tests/test_progress.py`) and `test_the_chart_shows_the_later_dated_weigh_in_and_body_weight_now_is_it` (`tests/test_update_settings.py`) |
| Package B-grams' test files | `tests/test_mcp.py` gained one test: a food saved again through `/mcp` with its grams left out keeps them |
| R4, goals by id | No test on SQLite can see the order: SQLite gives the rows by id with no order asked. The `order_by` is what holds it on Postgres |
| D1, `README.md:150` | After the review the sentence reads "Every write a person makes but the Settings switch and deleting the account counts against a write limit.": the owner's own `owner_delete_feedback` runs none |
| D2's table | After the review, the note on `stage-6.md` also names the activity row's new key, and the note on `trace.md` also says `word=` keeps the measure words |

One more known limit, found by `tests/test_paths_agree.py` and pinned there as it is: a log takes a null beside a value in a pair (`kg` 81 with `lb` null) and an edit refuses the pair as `invalid_field` (`app/services/entries.py`, `_stored_units`). It was so before this work. On an edit a null can mean "clear", so the edit's reading stands until the pair rule is looked at as a whole.

The helper of R6 is `rules.portion`. The read of R4 is `progress.bodyweight_view`.

Two more reads of the whole change on 2026-11-26, before the commit, found no fault in the code and these in the words above:

| Line above | As it is |
|---|---|
| "For the owner", the cardio row: "within 10 minutes" | The 10 minutes count from when the session was logged, not from the edit, as `docs/contracts/stage-4.md`'s note says |
| "What a model reads": a string "moved" | Moved to another file. `evals.need` sorts each file's strings, so a move inside one file is no change |
| "For the owner": "Nothing an assistant reads changes" | No field, schema or sentence changes. Five answers change by lines that exist already: `save_food`'s food and summary keep the grams; the same-number kcal; an invisible portion on an edit is a clear; the cardio session sent again after an edit is a duplicate; `get_data`'s day gives the other weigh-in |
| R6: "only invisible characters is no portion" | Up to the portion's length limit. A longer run of invisible characters is refused as too long, on a log and on an edit alike, as before this work |
| Decision 6: "keeps every matched account" | Up to `limit` lines (50 when left out), as `owner_calls`' notes say of every mode |
| Package B's tests: "in each copy" | The weight and the reps limits are sent through log, edit and a routine save. The import runs the session through the same `rules.check_session` as a log and is in the 60 sets test only |
| D2, `units.md` | The gap that reads on the line is from 0.0227 kg to under 0.05 kg: at 0.05 kg it rounds to a step and is off the line |
| R2: "it raises" | Only a measurement goal reaches the unit table. A lift, body weight or cardio best goal takes its quantity from its kind, whatever its unit |
