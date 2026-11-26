# Macros and asking first contract: protein, carbs and fat with a daily goal each, a portion asked before an estimate, a saved meal, photos never kept

**Goal: a food entry carries protein, carbs and fat in grams beside its kcal, each marked as the person's number or the assistant's estimate, and each day shows them against a daily goal. An estimate is never written without the portion the person ate: Spotter refuses it and the assistant asks. A meal of several foods is saved once and logged again by name. A food photo is read by the assistant and never reaches Spotter. A lift's missing weight or reps is asked, not guessed.**

Scoped 2026-11-21, not built. Read against `main` at `3b2441e`. Nothing here is approved to build until the owner says so. The builder reads each "What exists" row again on the commit it starts from. Routines (C5) is scoped at the same time for a parallel build in its own worktree, in `docs/contracts/routines-and-programs.md`: C3 merges first, C5's migration is `0016` on `0015`, and its cases go after C3's. The files both may touch are in "C5: the shared files".

## His words

On macros: "protein or so many carbs... we can mark it in the food itself".

On asking: "we should probably have the AI itself get the details from the end user for whatever was missed before we actually take the input."

On daily amounts, from B5: "Daily amount can't be a setting because typically we need to adjust calorie count as we move forward, right?" B5's agreed shape: an ongoing goal is a row, never a migration.

On where the numbers come from: "most important is having the calories and macros identified by the ai and grounded in the web search or whatever right? not just guessed by the ai itself."

Photos, decided 2026-11-16: physique and meal photos are not kept.

## Decisions

His to make. The lead's pick is in the last column.

His picks, 2026-11-21: all 20 as the lead's pick, 1 and 2 both A.

| # | Decision | Options | Pick |
|---|---|---|---|
| 1 | Which functions carry it | (A) today's: `log_food`, `save_food`, `find_food`, `edit_entry`, `update_settings`. (B) wait for `log` and `guide` | A. `log`'s food part takes `FoodEntryIn` as it stands, so the new fields carry over. `log-and-guide.md` owes a dated note: the sizes it pins for `log_food` and the food guide move |
| 2 | Where the three daily goals live. His B5 yes says a row, never a migration | (A) three settings beside `calorie_target_kcal`, through `update_settings`; B5 later moves all six amounts to rows: three more columns to move, over two deploys. (B) B5's rows first: the goal parts wait for B5's contract | A. The goal parts are marked "(2A)" in every package: under B they leave the build, and the rest stands |
| 3 | The numbers | Grams, three nullable columns. Null is "not logged", never 0 g. Ranges come from the kcal caps: an entry 0 to 1,250 g of protein or carbs and 0 to 555 g of fat (5,000 kcal over 4 and 9 kcal a gram, rounded down); a daily goal 1 to 2,500 g of protein or carbs and 1 to 1,111 g of fat (the 10,000 kcal target cap, the same way). Caps named as caps, revisited from the log | As written |
| 4 | Who supplied the macros | (A) their own mark, `macros_estimated`; left out, it counts as an estimate, so a slip shows as an estimate the person can confirm, never as their own number. (B) the same, but left out is refused. (C) one mark, `estimated`, for kcal and macros | A. A label's kcal with the assistant's protein is common, and C cannot say it |
| 5 | A check of the macros against the kcal | (A) none now; the call log keeps the ratio of 4p + 4c + 9f to kcal, no value, and a check is set from those numbers later. (B) a check now | A, by the numbers rule |
| 6 | The portion asked first | An entry with `estimated` true needs `portion`, the amount in the person's words ("a big bowl", "2"). Without it the call writes nothing and the fix says to ask once. Never asked: a stated number, a saved food (no servings is 1), a USDA food (grams are needed already) | As written. `guessed_number` changes: "Lunch: two rotis and dal" now asks about the dal |
| 7 | The portion kept | (A) stored, and shown on the day's receipt. (B) checked, then dropped | A |
| 8 | A saved meal's shape | (A) a saved food with items: `save_food` takes `items`, `find_food` lists it with them, `log_food` logs it by `saved_food_id` and `servings` as one entry. (B) its own record and functions, as routines have. (C) A, but logged as one entry a food | A. No new function, no new logging words. C misreads `saved_food_id` in six places |
| 9 | A meal's numbers | (A) worked out by Spotter when it is saved; saving it again updates them. (B) worked out at each log from the foods as they stand | A, as an entry copies its kcal today |
| 10 | What a meal holds | (A) saved foods by id, USDA foods by id and grams, or the person's own numbers; no estimate, as `save_food` takes the person's number. (B) estimates too | A. A saved meal is logged as a saved food, so an estimate inside it would lose its mark |
| 11 | A meal whose item has no macro | (A) that macro of the meal is null. (B) a partial sum | A, as 3: never a partial figure passed off as whole |
| 12 | USDA | Protein, fat and carbs per 100 g, FoodData Central nutrient ids 1003, 1004 and 1005. A cached food from before reads as stale and is fetched again: no clear in the migration, which would race the old build for the 40 s of a deploy | As written |
| 13 | The setup text | (A) unchanged. Two sentences read against C3: "never estimate your own, except a food's kcal" and the import's "logging food still never asks.". The cases show whether either stops a macro estimate or a portion question. On a miss, the fix round rewords both: "except for food," (5 fewer) and "logging food never asks which meal." (5 more), so the length stays 4,082. (B) reworded now | A, as AI5 went: the case first, the words after a measured miss |
| 14 | A lift's missing weight or reps | (A) the cases first, on today's refusals ("Give reps on set 1."); the fix words become "ask once, never guess" only if they miss. Built last, after C5's join. (B) reworded now | A |
| 15 | Photos | (A) no code and no function text: Spotter takes no image, so it keeps none. README and can-and-cant say so; the portion rule covers a photo's estimate; one Chrome check at UI-TEST. Image input in the eval runner stays in ROUTE. (B) build image input in the runner first | A |
| 16 | The pages | Today and the day's receipt (macros, portion, "not logged"), Settings (three amounts), the food edit form (macros, portion), Foods (macros a serving, a meal's foods). The look as it is; no chart | As written |
| 17 | Macros over time | (A) `get_data`'s food area gains the three fields, so a week's protein is Spotter's sum; `get_trends` and a chart wait for C9. (B) `get_trends` now | A |
| 18 | Out of this work | Macros in `import_entries` (IMP); L-FOODSRC's line (no room in the setup text: a number found on the web stays the assistant's estimate under today's marks); B7's meal rule (his decision 10); CONFIRM | As written |
| 19 | The migration | One, `0015`. C3 joins `main` first; C5's, if it has one, is `0016` on `0015` | As written |
| 20 | The eval run | Sonnet and `gpt-6-luna`, 3 runs a case. About 50 cases, about $14 Claude side; about $37 if 13 or 14 rewords (the core set joins) | As written |

## What exists today

| Piece | State | Where |
|---|---|---|
| A food entry | `date`, `meal`, `description` (200), `kcal` (whole, 0 to 5,000), `source` (one of four), `estimated`, `saved_food_id` with `servings`, `usda_fdc_id` with `grams`. No protein, carbs, fat or portion | `app/models.py:398-428`, `FOOD_SOURCES` `:53` |
| A saved food | `name`, `serving_label` (64), `kcal_per_serving`, `grams_per_serving`. No items | `app/models.py:358-375` |
| The USDA cache | `usda_food`: `fdc_id`, `description`, `data_type`, `kcal_per_100g`, `fetched_at`; 30 days. `usda_search` maps a query to ids | `app/models.py:456-475`; `app/services/usda.py:54`, `:141-167`, `:182-199` |
| USDA nutrients | Energy only: id 1008, number 208, unit kcal. The test fixture already carries protein as `{"nutrientId": 1003, "nutrientNumber": "203", "unitName": "G"}` | `app/services/usda.py:56`, `:141-155`; `tests/test_usda.py:25` |
| The daily target | `user.calorie_target_kcal`, 800 to 10,000, set by `update_settings`, cleared by null. Goals take no daily amount: `set_goal` refuses `calories` and points at `update_settings` | `app/models.py:134`; `app/services/account.py:248-252`; `app/rules.py:113`; `app/services/goals.py:116`, `:555-557` |
| `log_food`'s checks | Per entry: unknown fields refused; a number and an id never together; servings with a saved food, grams with a USDA food; the tag decided by Spotter. All or nothing | `app/services/food.py:137-218`, `:260-333` |
| The day's numbers | `day_numbers`: total, estimated, share, target, remaining, net. `log_food` and `get_log` both use it | `app/services/food.py:114-126`; `app/services/log.py:146-156` |
| The duplicate guard | The date and the whole set of entries within 10 minutes. `entry_tuple`: meal, description, kcal, source, the ids and amounts | `app/services/food.py:102-111`, `:336-344` |
| `save_food` | `name`, `serving_label`, `kcal_per_serving` (all three required in the schema), `grams_per_serving`. The same name updates; a change keeps a version | `app/services/food.py:347-403`; `app/mcp_server.py:1222-1243` |
| `find_food` | `saved` (by use), `recent` (the person's own you_said and estimate entries), `usda` | `app/services/food.py:406-458`; `app/mcp_schemas.py:756-786` |
| What reads `saved_food_id` as "servings of this food" | `saved_parts`, `LogFoodEntry.saved_food_name`, `_usage`, `fields.load_food`, the export, the kcal edit | `app/services/food.py:95-99`, `:461-467`; `app/mcp_schemas.py:693-699`; `app/services/fields.py:355-371`; `app/services/export.py:143-152`; `app/services/entries.py:620-622` |
| Food edits | `date`, `meal`, `description`, `kcal`. A kcal edit makes the entry `you_said` and clears its link | `app/services/entries.py:96`, `:535-539`, `:620-636` |
| Undo | `put_back` sets only the columns a version holds. A version kept before `0015` holds no macro or portion key | `app/services/common.py:544-558` |
| The input model | `FoodEntryIn`: `description`, `meal`, `kcal`, `estimated`, `saved_food_id`, `servings`, `usda_fdc_id`, `grams`; `extra="forbid"` | `app/mcp_schemas.py:195-224` |
| Result models | `FoodEntryOut`, `FoodDay`, `LogFoodResult`, `SavedFood`, `FindFoodResult`. The top level forbids extra keys. A new result field defaults to null, as `units.md` R21 and `exercise-list.md` had it | `app/mcp_schemas.py:39`, `:677-786` |
| The food rule | `FOOD_RULE`, in the setup text and in `log_food`'s description | `app/mcp_server.py:170-175`, `:1197-1205` |
| The setup text | 4,082 of 4,096, measured on `3b2441e`. It says "never estimate your own, except a food's kcal", "Never ask which meal", for cardio "Never ask first", and in the import rule "logging food still never asks." (pinned word for word in `tests/test_mcp.py`). Its length is pinned in 9 files, its hash in `tests/test_feedback.py` (`INSTRUCTIONS_AS_BUILT`) | `app/mcp_server.py:106`, `:112-156`, `:211-238`; `tests/test_data.py:1123`, `test_edit_history.py:683`, `test_hi_there.py:1235`, `test_idempotency.py:737`, `test_inputs.py:140`, `test_matcher.py:654-660`, `test_routines.py:861-863`, `test_switch.py:569-573`, `test_units.py:699-712`; `tests/test_feedback.py:96` |
| "Never ask" elsewhere | `docs/can-and-cant.md:142` and `:261` say the meal is never asked: true after C3, kept | grep, 2026-11-21 |
| The published list's pins | `tests/test_feedback.py` holds a hash of each function's published entry (`PUBLISHED`): every changed food function moves its hash, and C5's `get_routines` moves its own in the same dict | `tests/test_feedback.py:69-92` |
| The database's checks | Ranges on `food_entry` and `saved_food` numbers. Meals, food sources and goal kinds are lists meant to grow, unchecked by the database | `migrations/versions/0012_hardening.py:70-75`; `app/models.py:15-19` |
| The migration head | `0014`: 6 assertions and the `HEAD` constant, in 7 files | `tests/test_schema.py:598`, `test_edit_history.py:1355`, `test_feedback.py:215`, `test_hardening_schema.py:38`, `test_routines.py:1252`, `test_switch.py:511`, `test_units.py:820` |
| A lift set's missing numbers | Refused: "Give weight_kg or weight_lb on set 1." and "Give reps on set 1.". Weight 0 passes on every lift ("0 for bodyweight"). A missing set count cannot be seen | `app/rules.py:214-230`, `:498-506`, `:674-707` |
| The call row's names | `tried` writes only `KNOWN_NAMES` | `app/calllog.py:73` |
| A web add form | The day page still logs a stated kcal: `food_from_form` sends no `estimated`, so the entry is `you_said` and needs no portion | `app/templates/day.html:192-197`; `app/web.py:2025-2032` |
| The seeded weeks | Write food rows directly, estimates among them, with no service call: no portion needed, every figure unchanged | `evals/four_weeks.py:80-124`; `evals/sixteen_weeks.py:68-110` |
| Eval seeds with an estimate | None: `estimated` true appears only in the expected inputs of `guessed_number` and `food_label_bundle`, and nowhere in `evals/without_cases.json` | a script over both files, 2026-11-21 |
| Two-turn eval cases | `then` runs on Claude only; Codex fails such a case unrun | `docs/evals.md:152-168`, `:195-196` |
| Estimates in the tests | 50 lines in 21 test files send `estimated` true, by grep; each that reaches `log_food` gains a `portion` | grep of `tests/`, 2026-11-21 |

## The design

### The columns

| Table | New columns |
|---|---|
| `food_entry` | `protein_g`, `carbs_g`, `fat_g` (Float, null), `macros_estimated` (Boolean, null when no macro is held), `portion` (String 64, null) |
| `saved_food` | `protein_g_per_serving`, `carbs_g_per_serving`, `fat_g_per_serving` (Float, null), `items` (Text, null: a plain food; JSON for a meal) |
| `usda_food` | `protein_g_per_100g`, `carbs_g_per_100g`, `fat_g_per_100g` (Float, null), `macros_read` (Boolean, default false: a row from before `0015` reads as stale) |
| `user` | `protein_target_g`, `carbs_target_g`, `fat_target_g` (Integer, null). (2A) |

Every number is stored at 1 dp and shown through `app.formatting` with no needless decimals.

**Nothing changes for an account with no macro.** With no macro on an entry and no macro goal set, every answer, summary and page is what it is today, but for the new result fields at null. So the existing tests stand, but where a decision touches them (the portion, the pins).

Caps, each named with its reason, revisited from the call log: `portion` 1 to 64 characters, `serving_label`'s own limit for the same kind of words (`app/models.py:370`); a meal's `items` 2 to 30, 2 because a meal is several foods and 30 as `log_food`'s own entries a call (`app/services/food.py:64`).

### A food entry's new fields

| Field | Takes | Words (model-read) |
|---|---|---|
| `protein_g`, `carbs_g`, `fat_g` | 0 to 1,250 g, fat to 555 g. Never with `saved_food_id` or `usda_fdc_id`: Spotter works them out | "Protein in grams for what was eaten: the person's number, or yours. Never with saved_food_id or usda_fdc_id." (carbs and fat the same) |
| `macros_estimated` | true or false. Left out with any macro sent: true | "false only when the person stated these grams, such as from a label. Left out, they count as your estimate." |
| `portion` | 1 to 64 characters, the person's words. Needed when `estimated` is true | "How much was eaten, in the person's words, such as a big bowl or 2. Needed with estimated true: if the person did not say, ask once, and send nothing until they answer." |

The refusal when `portion` is missing on an estimate: `missing_field`, "Entry 2: portion is missing for an estimate." Fix: "Ask the person once how much they had, then send it as portion in entry 2, in their words. Nothing was logged."

A saved food's entry takes its macros from the food times the servings; a USDA entry from the 100 g figures times the grams; `macros_estimated` false for both. A saved food or USDA food with no figure for a macro logs that macro as null.

### Marks, edits and the confirm tap

| Act | kcal and `source` | Macros and `macros_estimated` |
|---|---|---|
| Logged with a stated kcal | `you_said` | as sent; left-out mark true |
| Logged as an estimate | `claude_estimate` | as sent; left-out mark true |
| `edit_entry` sends `kcal` | `you_said`, link cleared, as today | unchanged, with their own mark |
| `edit_entry` sends any macro | unchanged | the sent ones replace; the mark becomes false; a macro sent null clears it |
| The page's confirm tap | `you_said`, as today | the mark becomes false: the person confirmed the line they saw |
| Undo of a version kept before `0015` | as today | a C3 column missing from the version is put back as null: the entry had none then |

### The day's numbers

`FoodDay` gains one field, `macros` (default null), present when the person has a macro on the day or a macro goal set: `{protein, carbs, fat}`, each `{g, target_g, left_g, entries_without}`. `target_g` and `left_g` are (2A). `g` is the sum over entries that hold it, null when none does; `entries_without` counts the day's entries with that macro null. The summary adds one sentence, such as "Protein 92 g so far, 1 entry with none, 58 g left of the 150 g goal." "so far" appears only when `entries_without` is above 0. `get_log` carries the same block, from the same helper.

### The saved meal

| Point | Rule |
|---|---|
| Saving | `save_food` with `name`, `serving_label` (optional for a meal, default "1 meal"), and `items`: 2 to 30, each a saved food (`saved_food_id`, `servings`), a USDA food (`usda_fdc_id`, `grams`) or the person's own (`description`, `kcal`, the macros). `kcal_per_serving` and the macros are not sent with `items`: Spotter sums them. An item that is a meal, or sent with `estimated` true, is refused with a fix |
| Stored | `items` as JSON, each item as resolved: its description, kcal, the three macros, where it came from. The meal's `kcal_per_serving` and macros are the sums; a macro is null when any item lacks it |
| Found | `find_food`'s `saved` items gain `items` (null for a plain food) and the three macros a serving |
| Logged | By `saved_food_id` and `servings`, exactly as a saved food: one entry, `source` `saved_food`, kcal and macros times the servings. Every reader of `saved_food_id` stays true |
| Changed for one day | "My usual lunch without the dal" is logged food by food. Known limit |
| Edited, deleted, undone | As a saved food: `save_food` with the same name, `delete_entry` kind `saved_food`, `restore_entry` with `undo_edit`. The version holds `items` |

`SavedFood`'s `id` words gain "a meal too". `save_food`'s description gains one sentence: "With items, it saves a meal of several foods: Spotter adds up the meal's numbers, and it is logged by its id as one food." It names no function, so it carries into `log` and `guide` unchanged.

### USDA

`_parsed` reads the three nutrients by id or number (1003 or 203 protein, 1004 or 204 fat, 1005 or 205 carbohydrate by difference), unit g, beside energy. A food with energy but no macro keeps null for that macro: never a fallback to another nutrient. A cached row with `macros_read` false is stale and fetched again, as one older than 30 days is. `UsdaFood` in `find_food` gains the three figures a 100 g.

### Daily goals (P3, all of it 2A)

`update_settings` gains `protein_target_g`, `carbs_target_g` and `fat_target_g`, each "null clears it", on the shape of `calorie_target_kcal`. `whoami`'s settings, `get_data`'s settings area, the Settings page form and the export's account columns carry them. `set_goal` refuses `protein`, `carbs` and `fat` as kinds with the fix naming `update_settings`, as it does `calories`.

### Asking about a lift (P6, decision 14 A)

P6 adds the cases and runs them on today's words. Only on a miss does the fix change: "Ask the person once for set 1's reps, then send them. Never guess." and the same for a weight. `rules.py` is then the only app file touched.

### Photos (decision 15 A)

No function takes an image. A test pins that no input schema has a field of type bytes or a `contentEncoding`. `docs/can-and-cant.md` and the README gain one line: "Spotter never receives a photo. The assistant reads a photo of a meal and sends its foods as words and numbers, as estimates with their portions." The privacy page (D1) needs a line: the photo goes to the assistant's maker, never to Spotter.

### The call log

`KNOWN_NAMES` gains every new field name. A `log_food` call with all three macros and kcal above 0 on an entry notes `atwater=` with each such entry's ratio at 2 dp, sized to the notes column as the per-hand check's `pair=` is. No value, no food.

## Migration 0015

`migrations/versions/0015_macros.py`, `revision "0015"`, `down_revision "0014"`, notes in the shape of `0012`'s. Up: the columns of "The columns", null, through batch on SQLite; range checks in `0012`'s shape, `ck_food_entry_protein_g` and the rest, each from 0 to 100,000 as the other number checks are. `usda_food.macros_read` server default false. Down drops them. No data moves: every existing entry reads "not logged". Run on a local Postgres by his okay: up, down, up, over a written row with each column set.

## Model-read text

Every string a model reads that changes. Each passes `tests/text_rules.py` on its surface.

| # | Where | Change |
|---|---|---|
| 1 | `FoodEntryIn`, `app/mcp_schemas.py:195-224` | The five fields above |
| 2 | `log_food`'s description | One sentence after `FOOD_RULE`: "Send protein_g, carbs_g and fat_g when the person gave them or you can work them out; an estimate needs its portion." |
| 3 | `save_food`'s description and its schema | `items`; `serving_label` and `kcal_per_serving` optional (a relaxation only); the three macros a serving |
| 4 | Result models | `FoodEntryOut`, `LogFoodEntry`, `FoodDay.macros`, `SavedFood` and `SavedFoodHit`, `UsdaFood`: new fields, each default null |
| 5 | `edit_entry` | `protein_g`, `carbs_g`, `fat_g`, `portion`: "A food entry's ... as the person stated them. They become their own numbers." |
| 6 | `update_settings` (P3) | The three targets |
| 7 | `set_goal`'s refusal (P3) | `protein`, `carbs`, `fat` named beside `calories` |
| 8 | The refusals | The portion refusal; the meal refusals; P6's two only on a miss |
| 9 | `docs/can-and-cant.md` | Food: the macros and their marks, the portion, the saved meal, the photo line, the three daily amounts |
| 10 | The setup text | Unchanged (decision 13). On a measured miss only: "except a food's kcal" becomes "except for food", 4,077 |

The published size of each changed function is printed on the built server and pinned where the tests pin sizes.

## A chat opened before the deploy

| What it holds | What happens |
|---|---|
| The old `FoodEntryIn`, with no `portion` | An estimate is refused; the fix names `portion`. Whether its client passes a field its list lacks: Claude's, as `inputs.md` found, likely; Codex's, not checked. If not, that chat cannot log an estimate until a new one starts. Checked at the live check from a chat opened before |
| The old result schemas | Every new result field defaults to null, as before |
| The old `save_food`, three fields required | It saves plain foods as today |

## Packages

One builder each, in order: P1, P2, P3. Then P4 and P5, which touch no file of each other's, side by side in their own worktrees or one after the other. P6 after C5's join. Each list is built by grep on `3b2441e`; a test file not named that fails because of a decision is named in the hand-over, and any other failure stops the build.

| Package | Files | Exit check |
|---|---|---|
| P1 Storage | `migrations/versions/0015_macros.py`, `app/models.py` (the `user` columns are 2A), `tests/test_macros_schema.py` (new). The head pins: `tests/test_schema.py`, `test_edit_history.py`, `test_feedback.py`, `test_hardening_schema.py`, `test_routines.py`, `test_switch.py`, `test_units.py` | Up, down, up on SQLite with a written row; the checks refuse a negative gram; head `0015` |
| P2 Food | `app/services/food.py`, `usda.py`, `entries.py` (food edits, undo), `common.py` (`put_back`'s null rule), `log.py`, `fields.py`, `data.py` (the food area), `export.py` (the notes), `app/rules.py` (`NUMBERS`, the portion and item checks), `app/calllog.py`, `app/mcp_schemas.py` (food models), `app/mcp_server.py` (`log_food`, `save_food`, `find_food`, `edit_entry`). Tests: `tests/test_macros.py` (new), `test_food.py`, `test_usda.py`, `test_entries.py`, `test_calllog.py` (`KNOWN_NAMES`); the published pins `test_feedback.py` (`PUBLISHED`), `test_units.py`, `test_mcp.py`; the estimate sites that reach `log_food`, in `test_data.py`, `test_edit_history.py`, `test_evals.py`, `test_export.py`, `test_four_weeks.py`, `test_golden_cases.py`, `test_hardening_schema.py`, `test_idempotency.py`, `test_inputs.py`, `test_log.py`, `test_page_rules.py`, `test_pages_text.py`, `test_progress.py`, `test_result_text.py`, `test_sixteen_weeks.py`, `test_switch.py`, `test_trends.py`, `test_web.py` | Every row of the design tested; every estimate site passes with its portion; an account with no macro answers as today |
| P3 Daily goals, all 2A | `app/services/account.py`, `goals.py` (the refusal only), `data.py` (settings), `export.py` (account columns), `app/mcp_server.py` (`update_settings`), `app/mcp_schemas.py` (the settings results), `tests/test_update_settings.py`, `test_feedback.py` (`update_settings`'s hash), `test_export.py`, `test_hi_there.py` (`whoami`'s settings) | The three set, cleared, refused out of range; the day's `left_g` |
| P4 Pages | `app/web.py`, `app/templates/day.html`, `edit.html`, `foods.html`, `settings.html` (2A), `_parts.html`, `app/static/style.css`, `tests/test_web.py`, `test_pages_text.py`, `test_page_rules.py`, `test_web_account.py` | "not logged", never "0 g"; the portion on its line; a meal's foods; 320 and 375 px with the worst-case sample |
| P5 Docs and evals | `docs/can-and-cant.md`, `README.md`, `docs/evals.md`, `evals/cases.json` (`macro_goal_set` and `protein_left_today` are 2A), `evals/judge.py` (`_rows_text` keeps the new columns; `item_count` for `saved_food`), `evals/run.py`. Tests: `test_evals.py`, and the files that read can-and-cant or the README: `test_calllog.py`, `test_docs.py`, `test_eval_need.py`, `test_hi_there.py`, `test_inputs.py`, `test_mcp.py`, `test_text_rules.py` | The new cases load and judge a passing and a failing run each; the can-and-cant and README pins pass |
| P6 Lifts | `evals/cases.json` (four cases), then on a miss `app/rules.py` and `tests/test_rules.py` | The cases' run; a reword only after a miss |

## C5: the shared files

Every file C3 touches that a routines build may also touch, with how the clash shrinks.

| File | C3 changes | Shrink |
|---|---|---|
| `app/mcp_schemas.py` | `FoodEntryIn`, food result models | Different models; a line merge |
| `app/mcp_server.py` | food functions, `edit_entry`, `update_settings` | Different functions; the setup text unchanged by default |
| `app/models.py` | food, saved food, USDA, user columns | Different classes |
| `app/rules.py` | `NUMBERS`, the food checks; P6 only on a miss | P6 after the join |
| `app/web.py`, `app/templates/settings.html`, `app/static/style.css` | food pages; three amounts on Settings (2A) | Different routes and blocks; C5's Settings change is the "Saved routines" line at most |
| `app/services/entries.py`, `common.py`, `export.py`, `account.py`, `fields.py` | food edits, undo, columns, settings | Food rows only |
| `app/calllog.py` | `KNOWN_NAMES` | Both add names to one set: a merge by hand |
| `migrations/versions/` | `0015` | C5's, if any, is `0016` on `0015`, rebased at the join |
| The head pins: `tests/test_schema.py`, `test_edit_history.py`, `test_feedback.py`, `test_hardening_schema.py`, `test_routines.py`, `test_switch.py`, `test_units.py` | `0014` to `0015` | One line each: the join takes the higher |
| The setup text's pins: 9 files on its length (`tests/test_routines.py` and `test_matcher.py` among them) and its hash in `test_feedback.py` | none by default | Only if decision 13 rewords: then the hash and the pinned sentences move, the length stays |
| `tests/test_feedback.py` `PUBLISHED`, `tests/test_units.py`, `tests/test_mcp.py` | the food functions' published entries | C5 moves `get_routines`' and `save_routine`'s in the same places: different keys, recomputed at the join |
| `docs/can-and-cant.md`, `README.md` | food sections, the photo line | Different sections |
| `evals/cases.json`, `evals/judge.py`, `evals/run.py`, `docs/evals.md`, `tests/test_evals.py` (`CONTRACT_CASES` and the count, `:26`, `:66`), `tests/test_eval_need.py`, and the case-order pins in `tests/test_inputs.py`, `test_hi_there.py`, `test_routines.py`, `test_units.py` | new cases, judge columns, counts | C5's contract puts its cases after C3's. Both move the counts: joined by hand; one eval run for both if they release together |
| Pins C5 moves that C3 does not | `tests/test_mcp.py:253` `FUNCTIONS`, `test_progress.py:453`, `test_trace.py:234`, `test_schema.py:164`, `test_sixteen_weeks.py:299` | C3 adds no function and no table: the saved meal lives in `saved_food` |
| Other test files both may edit: `tests/test_calllog.py`, `test_data.py`, `test_docs.py`, `test_export.py`, `test_golden_cases.py`, `test_hi_there.py`, `test_idempotency.py`, `test_inputs.py`, `test_page_rules.py`, `test_pages_text.py`, `test_result_text.py`, `test_sixteen_weeks.py`, `test_web.py`, `test_web_account.py` | a portion on an estimate, a name, a page line | Line edits in different tests; joined by hand |
| `BUILD_LOG.md` | one entry | One entry each |

## Eval cases

Judged on the record. An ask is judged on no successful write (`ok_calls` 0, no row) and a question, never on a reply's words alone. A two-turn case has a one-turn twin, so `gpt-6-luna` counts.

| Case | The person says | Passes when |
|---|---|---|
| `macros_stated` | lunch was a chicken wrap, label says 520 kcal, 32 g protein, 48 g carbs, 18 g fat | One entry: kcal 520 `you_said`, protein 32, carbs 48, fat 18, `macros_estimated` false |
| `macros_estimated` | had 2 boiled eggs for breakfast | One entry, `claude_estimate`, a portion, the three macros held, `macros_estimated` true; the reply says estimate |
| `macros_saved_food` | Two rotis (a saved Roti with 3 g protein a roti) | One entry, servings 2, protein 6, no macro sent |
| `macro_goal_set` | set my protein goal to 150 g a day | `update_settings` with 150; no `set_goal` |
| `protein_left_today` | how much protein do I have left today? (seeded day, a 150 g goal) | A read; Spotter's `left_g` quoted; no write |
| `protein_not_logged` | how much protein have I had today? (one entry with none) | Spotter's figure with "so far"; never 0 g for the entry |
| `portion_asked` | had some rice for lunch | No successful write; the reply asks how much |
| `portion_asked_then` | the same, then: a big bowl | One entry, estimated, portion holds "big bowl"; nothing before the answer |
| `portion_given` | had a big bowl of dal for lunch | One entry, estimated, portion set, no question |
| `save_meal` | save my usual lunch: 2 rotis and a bowl of dal (both saved) | One saved food with 2 items, kcal the sum; no food entry |
| `log_saved_meal` | had my usual lunch (the meal saved) | One entry, `saved_food_id` the meal, servings 1 |
| `guessed_number`, changed | Lunch: two rotis and dal | No successful write; the reply asks about the dal |
| `lift_weight_asked` (P6) | did 3 sets of 8 on bench | No successful write; the reply asks the weight |
| `lift_weight_asked_then` (P6) | the same, then: 60 kg | One session, 3 sets of 60 × 8 |
| `lift_reps_asked` (P6) | squatted 100 kg for 3 sets | No successful write; the reply asks the reps |
| `pullups_no_ask` (P6) | 3 sets of 10 pull-ups | 3 sets at 0 kg, no question |

`meal_not_asked`, `stated_number`, `food_label_bundle`, `saved_food` and `rambling_day_log` stay as they are: they must still log in one turn. No photo case: the runner sends no image.

### The run and its cost

One run at the end of the build, when `python -m evals.need main` says yes. Sonnet (`claude-sonnet-5-5`) and `gpt-6-luna`, 3 runs a case. The cost per Sonnet run, from BUILD_LOG: $4.6104 for 52 runs (`:1227`) and $2.0433 for 24 (`:1302`): about $0.09 a run.

| Run | Cases | Claude side, at list price |
|---|---|---|
| Default (13 and 14 unchanged) | The 16 above, `guessed_number`, the 23 cases with a food function in `must`, about 10 reads that quote food figures: about 50, 2 of them two-turn | About $14. Luna: plan usage, the two-turn cases fail unrun there |
| If 13 or 14 rewords | The core 101 join: about 135 | About $37 |
| A trial before a reword | One case, 12 runs | About $1 |

The real figure is reported after.

## The exit check

1. The package checks above, each package's tests plain and with `-W error`.
2. The whole suite once on a clean export of the exact commit, its output read in full.
3. `0015` on a local Postgres, up, down, up, over a written row, by his okay.
4. The eval run, its real cost.
5. After the merge, on his word: `/health` 200, `/mcp` 401 with no token, `/` 200, `/ready` 200 at `0015`. A fresh chat quotes `FoodEntryIn`'s `portion`; a chat opened before logs one estimate.

## Known limits

- A missing set count cannot be seen: "bench 60 for 8" is one set.
- `same_again_no_context` stays a known miss unless the lift cases move it.
- A meal changed for one day is logged food by food.
- An estimate inside a saved meal is refused, so a meal needs each food's number first.
- A food photo is untested by the runner: one check by hand in Chrome.
- A chat opened before the deploy may not be able to send `portion` (above).
- Daily goals as settings keep no history of their changes until B5 (decision 2).

## Not in this work

Macros through `import_entries`. L-FOODSRC. B7. CONFIRM. Macros in `get_trends` and a chart over time (C9). Image input in the eval runner (ROUTE). The look of the pages (C12).

## Note, 2026-11-21: log-and-guide merges first

The owner's word, after the build began: `docs/contracts/log-and-guide.md` merges to `main` before C3. P1 stands as built, since log-and-guide adds no migration, so `0015` follows `0014`. P2 to P5 are built only after log-and-guide is on `main` and this branch is rebased onto it. Each builder reads that contract's "What exists" against the commit it starts from. Where this note and the sections above disagree, this note wins.

| # | Point | What changes here |
|---|---|---|
| 1 | `log_food`'s description is a named constant after log-and-guide, and `guide` food is built from it and `FoodEntryIn` | P2 adds model-read text 2's sentence to that constant, so `log_food` and `guide` food both carry it |
| 2 | The `atwater=` note | Written by the food service, not keyed on the name `log_food`, so a call through `log` with kind food notes it too. Sized by the room left that log-and-guide's `calllog` gives, as `pair_note` is, after `kind=` and `word=` |
| 3 | `SavedFood.id`'s words become "Log it as saved_food_id, with servings." | The meal words go onto that sentence: "Log it as saved_food_id, with servings: a meal too." |
| 4 | Decision 13 named one place wrongly. "logging food still never asks" is in `import_entries`' description (`IMPORT_DESCRIPTION`, `app/mcp_server.py:152` on `3b2441e`), not in the instructions. The instructions are 4,084 after log-and-guide | On a measured miss, "except a food's kcal," becomes "except for food," in the instructions: 4,084 to 4,079. "logging food still never asks." becomes "logging food never asks which meal." in `import_entries`' description: 5 more there. The instructions' pins move by 5; `import_entries`' published size and hash move too. Model-read text row 10's "4,077" reads 4,079 |
| 5 | A chat opened before C3 holds `log_food` with no `portion` | P2 adds a test that sends an estimate with no `portion` to `log_food`, refused with the fix, then the same call with `portion`, logged. "A chat opened before the deploy" stands: whether the client passes a field its list lacks is checked at the live check |
| 6 | Through `log`, a food refusal names its entry as sent: `details.entries[1]`, not "Entry 2" | The portion refusal and the meal refusals take the entry's place by the same `where` the other food checks take, so `said_as_sent` rewrites them. P2 tests both: "Entry 2" through `log_food` and `details.entries[1]` through `log` |
| 7 | The published hashes and sizes | Recomputed after the rebase, on log-and-guide's numbers. The core is 103 before C3's cases. The case counts in "Eval cases" and "The run and its cost" are read again from `evals/cases.json` after the rebase |

## Note, 2026-11-22: rebased onto log-and-guide as built

`c3-macros` is rebased onto `main` at `9950f13`, where log-and-guide is live. P1 was set aside as a patch, the contract commit rebased, and P1 put back with no conflict. On that tree P1's 11 test files pass but for `tests/test_edit_history.py`'s export of versions, which is P2's. Read on `9950f13`: 21 functions listed, 24 for the owner, 260 cases, 103 core, the last case `log_plank_minutes`, the instructions 4,084 characters pinned in 10 test files (`tests/test_mcp.py` is the tenth). This note wins over the sections above and over the note of 2026-11-21 where they differ.

| # | Point | What C3 does |
|---|---|---|
| 1 | `tests/test_log_and_guide.py:1419` ("food estimated, no meal") is a row that runs through both `log` and `log_food` with no error | P2 gives it a `portion`, and adds the portion refusal to that file's "said as sent" lists (`:907`, `:1485`). Through `log` it reads "details.entries[1]: portion is missing for an estimate.", its fix "... send it as portion in details.entries[1] ..." |
| 2 | Cases | P5 appends after `log_plank_minutes`: 271 cases, the package 200. Each new case gets its `RUNS` in `tests/test_golden_cases.py`, which joins P5's files. Each case that asks for no write, `guessed_number` among them, lists `log` in `must_not` and `no_call`. Cases keep the old function names: the judge routes a `log` call to its kind's function. P6's four come after C5's join |
| 3 | Published hashes of `log_food`, `save_food`, `find_food`, `edit_entry` (P2) and `update_settings` (P3) | Recomputed on the built server, never merged by hand. A pin of a hidden function goes through `registered_tools` |
| 4 | The food sentence (model-read text row 2) | Goes in `LOG_FOOD_DESCRIPTION`, `app/mcp_schemas.py:313`. `guide` food reads it, so `GUIDE_SIZES["food"]` (3,626, `tests/test_log_and_guide.py:1136`) moves. Topic `log` reads only the description's first sentence, so its size should not move: printed on the built server |
| 5 | How arguments reach `services.log_food` | Any change goes in `log_food_step`, `app/mcp_server.py:847`, never in the decorated `log_food`, or `log` misses it |
| 6 | `atwater=` | Sized by `calllog.room("atwater")` (`app/calllog.py:265`), after `kind=` and `word=`, as `pair_note` is |
| 7 | Hand merges likely | `KNOWN_NAMES` in `app/calllog.py`; the `save_food` row in `docs/can-and-cant.md`; `app/services/fields.py` near `:813` |
| 8 | Stale figures above | "The run and its cost": the core is 103, so a reword run is about 137 cases, not about 135; its cost scales the same way, read again before the run. "What exists": the length is pinned in 10 files, not 9. The estimate sites are 52 lines, the 6 new ones in `tests/test_log_and_guide.py` |
| 9 | Notes and the log | C3's notes on `docs/contracts/data-on-request.md` and `docs/contracts/log-and-guide.md` go after log-and-guide's own notes there; C3's `BUILD_LOG.md` entry at the tail |

No merge, deploy or eval run without the owner's word.

## Note, 2026-11-22: rebased onto C5a

`main` moved to `fcba0e8`, where C5a (routines-and-programs.md, deploy 1) is live on top of log-and-guide. `c3-macros` is rebased onto it the same way: P1 to P4 set aside as a patch, the contract commit rebased, the patch put back with no conflict. C5a's diffs to the files both touch were read first: its changes in `app/mcp_schemas.py`, `app/mcp_server.py`, `app/services/entries.py` and `app/web.py` are routine lines apart from C3's food lines. On the merged tree the published hashes in `tests/test_feedback.py` and `tests/test_inputs.py` pass as computed by the server. Read on `fcba0e8`: 262 cases, 103 core, the last three `log_plank_minutes`, `routine_same_as_last_time` and `routine_last_time_read`; `tests/test_golden_cases.py` pins the package as 189 + `len(C5A_RUNS)` and the cases as 260 + `len(C5A_RUNS)`. This note wins over the notes above where they differ.

| # | Point | What C3 does |
|---|---|---|
| 1 | Cases (replaces point 2 of the note of 2026-11-22 on the counts) | P5's 11 go after `routine_last_time_read`: 273 cases, 103 core, the package 202. Their `RUNS` sit in a block of their own beside `C5A_RUNS`, which stays as it is. The rest of that point stands: each case that asks for no write lists `log` in `must_not` and `no_call`, and cases keep the old function names |
| 2 | The instructions | 4,084 characters, pinned in 10 test files. On a measured miss, decision 13's reword takes them to 4,079 |
| 3 | `GUIDE_SIZES["food"]` | 3,626 on `fcba0e8`, 4,885 with P2's food sentence; `log` 1,894 is unchanged |
| 4 | The page line on estimates | `app/templates/hi.html` said "The one number it may estimate is a food's calories". It now names the macros too, and `tests/test_hi_there.py` pins the new line |
| 5 | The log | C3's `BUILD_LOG.md` entry goes after C5a's, now at the tail |

Still to come on `main`: C5b, then C5c after C3's merge and its migration `0015`; the five old log functions are removed last. One more rebase is expected if C5b merges first.

No merge, deploy or eval run without the owner's word.

## Note, 2026-11-23: rebased onto C5b, and two picks

`main` moved to `101306a`, where C5b (routines-and-programs.md, deploy 2) is live. P1 to P5 were committed as one commit on the owner's pick of 2026-11-23: they were built one after another in one tree, and P2 and P3 share the function file, the schemas, the rules and every hash pin, so a commit a package would hold states no build ran. The branch was then rebased onto `101306a`. Eight files conflicted and were merged by hand with both sides kept: `app/mcp_server.py` (the imports), `app/services/export.py` (the notes), `docs/can-and-cant.md` (C5b's Routines line beside C3's Food and Saved foods), `docs/evals.md`, `evals/cases.json`, `tests/test_evals.py`, `tests/test_golden_cases.py` and `tests/test_routines.py`. `tests/test_routine_items.py` read C5b's four cases as the last of the file; it now finds them by id, as `tests/test_routines.py` finds C5a's two. The published hashes pass as the merged server computes them: C5b moved `save_routine` and `get_routines` only. This note wins over the notes above where they differ.

| # | Point | What C3 does |
|---|---|---|
| 1 | Cases | 277, 103 core: C5a's 2, then C5b's 4, then C3's 11, each direct and out of the core. The package in `tests/test_golden_cases.py` is 206: 189 + C5a's 2 + C5b's 4 + C3's 11. P6's four follow C3's |
| 2 | The two ask cases, the owner's pick of 2026-11-23 | `guessed_number` and `portion_asked` pass a food log that Spotter refuses for its portion, followed by the question: `log` and `log_food` sit in `may` with `ok_calls` 0 each and no food row. Every other write stays in `must_not` and `no_call`. This is the eval section's rule above ("no successful write"); the note of 2026-11-22's "lists `log` in `must_not` and `no_call`" holds for the read cases |
| 3 | P6 | Built right after P5 and before the review, on the owner's pick of 2026-11-23, rather than after C5's join |
| 4 | The rotis line | The landing's and the hi page's "Lunch was two rotis and dal." now does "Asks how much dal, then logs the meal.", as `guessed_number` now asks |
| 5 | The log | C3's `BUILD_LOG.md` entry goes after C5b's, now at the tail |

Still to come on `main`: C5c, after C3's merge and its migration `0015`; the five old log functions are removed last.

No merge, deploy or eval run without the owner's word.

## Note, 2026-11-23: the review, the owner's two picks and the fix round

C3 as built (`04c0668`, P1 to P6) was reviewed on a frozen copy: one must fix, two should fix, two minor. The fix round is `a80f42e`, re-checked on a frozen copy because the review had a must fix: clean, no must fix. Its one should fix, the meal item refusal's words, was reworded after it (row 4). This note wins over the sections above and the notes before it where they differ.

| # | Point | As built now |
|---|---|---|
| 1 | The review's must fix: a `save_food` with only the old three fields cleared a saved food's macros, and a meal's name saved that way dropped its foods | A save over a saved food keeps a macro a serving left out, and a null clears one (`not_sent()`, as `update_settings`'s targets). A save of a live meal's name with no `items` is refused: "Send the meal's foods as items to change it, or give another name. Nothing was saved." A deleted food brought back plain takes only the macros sent. This replaces the row "The old `save_food`, three fields required" and the saved meal's "Edited" row. `grams_per_serving` still clears when left out, as on `main`: tracked, not changed |
| 2 | "Marks, edits and the confirm tap", the row "`edit_entry` sends any macro"; model-read text row 5 | The owner's pick of 2026-11-23, "Stays an estimate": one mark covers the three macros, so it becomes false only when the edit sends every macro the entry holds after it, as the confirm tap does. An edit of fewer replaces what it sends and leaves the mark as it was. The edit page sends every held macro once one changes, so a page edit marks them the person's own. `edit_entry`'s words: "and macros become their own grams when every one held is sent." |
| 3 | The goal line: "An estimate is never written without the portion the person ate" | The owner's pick of 2026-11-23, "Keep as built": the portion is needed when the kcal is an estimate (decision 6 as written). A stated kcal with estimated macros, such as a label's kcal with the assistant's protein, needs none: the label is the serving |
| 4 | An estimated meal item's refusal | Its fix no longer says to log the food as an entry of its own, which would write food nobody ate: "If the kcal is the person's own, send the item without estimated; else ask them for it. Nothing was saved." It asked for a kcal already given when an item carried estimated false, and its "save it with save_food first" let an estimate be stored with no mark (the re-check; reworded on the owner's word of 2026-11-23) |
| 5 | "Every number is stored at 1 dp" | A USDA food's protein, carbs and fat a 100 g are kept at 2 dp, as its kcal is; an entry's grams are rounded to 1 dp after the scaling. A 150 g banana gives 1.6 g protein, not 1.7 |
| 6 | Photos: "A test pins that no input schema has a field of type bytes or a `contentEncoding`" | The test was missing from P2; the fix round adds it (`tests/test_macros.py`) |
| 7 | Migration 0015's rules, "each from 0 to 100,000" | The food and saved food grams are `>= 0 AND <= 100000`; the three daily goals are `> 0 AND <= 100000`, as `calorie_target_kcal`'s rule is |
| 8 | The `save_meal` case | It says "save my usual lunch as a meal: 2 rotis and a bowl of dal" |
| 9 | Hashes moved in the fix round | `save_food` and `edit_entry`, printed from the built server and pinned in `tests/test_feedback.py` and `tests/test_inputs.py`. The setup text is still 4,084 characters |
| 10 | The Postgres check | On the owner's word of 2026-11-23, `0015` runs once more on a local Postgres at the final commit, up, down, up over a written row; the run of 2026-11-21 was on the same file by the session's record, without a byte copy at the minute of the run |

No merge, deploy or eval run without the owner's word.

## Note, 2026-11-25: the logic audit (`docs/contracts/logic-audit.md`)

- Grams on a plain save (row 1 of the note above, line 354, said they still clear when left out). By the logic audit's ruling R7 the key's presence decides. Left out, a saved food keeps its grams and a new one has none. Sent as null or `""`, they are cleared. A save with items clears grams left out, and so does a deleted food brought back by a plain save. The Foods page sends `""` for an empty grams field.
- Its limit: through MCP a null is dropped before the service, so an assistant cannot clear a saved food's grams on purpose. `save_food`'s description is unchanged.
- A portion of only invisible characters (the logic audit's ruling R6): no portion on a log, as before. On an edit it clears the portion, as an empty one does, where it was refused. `rules.portion` reads it for both. An estimate's portion cleared on an edit is still not refused.
