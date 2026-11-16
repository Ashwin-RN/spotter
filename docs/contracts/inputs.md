# Input checks contract: every bad input is refused with a fix, and nothing is written

**Goal: whatever an assistant or a form sends, Spotter either takes it as meant or refuses it with a code and a fix. No field is dropped without a word, no number is bent into range by rounding, and no input crashes a call.**

Approved by the owner on 2026-11-16, with every pick of its card. The card came from a read-only audit of every input on 2026-11-16, run on a copy of the build: every function's fields through `/mcp`, every form, and the import, 3,786 calls through `/mcp` alone. Built on `194e7f0`, after units U2 and before the release's one eval run. The two picks changed here, D8 (`get_log`'s floor) and D11 (`missing_field` for a missing argument), and the pick of each open question below were approved by the owner on 2026-11-16.

Read on the main tree on 2026-11-16, at `c6ddf59` with units U2 not yet committed. U2 then landed as `09653cb`, and `app/` there is what was read. U2 changed `app/web.py`, `app/services/goals.py`, `app/services/progress.py`, `app/services/trends.py`, `app/metrics.py`, `app/charts.py` and the templates. A line number in those files is marked (U2): each was checked again at `09653cb`, and the builder reads it again on `194e7f0`.

## What exists today

| Piece | State | Where |
|---|---|---|
| The library's argument model | `mcp` 2.3.0 builds one model per function on `ArgModelBase`, whose config sets no `extra`: pydantic's default ignores an unknown top-level key | `.venv/Lib/site-packages/mcp/server/mcpserver/utilities/func_metadata.py:96-112` |
| A schema failure | The library raises `ToolError("Error executing tool <name>: <pydantic's text>")` before Spotter's function runs. The server answers `is_error` with that text as the only content: no `structuredContent`, no code, no fix | `mcp/server/mcpserver/tools/base.py:151-156`, `mcp/server/mcpserver/server.py:437-455` |
| Spotter's own refusal | `{error, message, fix}`, sometimes `errors` or `closest`, as `structuredContent` and as compact JSON text, `is_error` true. Built in `Wrapper.call`, which also writes the call-log row | `app/mcp_server.py:425-499` |
| A schema failure and the call log | It never reaches `Wrapper.call`, so it writes no row. Two tests hold this | `tests/test_mcp.py:2308-2320`, `:2393-2406` |
| The arguments as sent | `SpotterServer.call_tool` holds them in `_SENT` for the wrapper, then calls the library | `app/mcp_server.py:393-399` |
| Nested input models | `SetIn`, `RoutineLiftIn`, `FoodEntryIn` set no `extra`: an unknown key is dropped. `TOP` (`extra="forbid"`) is the result models' config, not an input's | `app/mcp_schemas.py:21`, `:32`, `:55`, `:71` |
| The import's models | `ImportSetIn`, `ImportFoodIn`, `ImportRowIn` set `extra="allow"` on purpose: the service refuses an unknown row field as a row problem in the preview | `app/mcp_schemas.py:99-101`, `:105-169`, `app/services/imports.py:294-297` |
| The services' own unknown-key refusals | "'x' is not a field of a routine." / "Use: name, lifts."; "Lift 2: 'x' is not a field of a lift."; "Entry 1: 'x' is not a field of a food entry."; "'x' is not a field of a saved food."; "'x' is not a field of a cardio entry."; "'x' is not a field of an import." Reached only from the web or a direct service call: through MCP the key is dropped first | `app/services/routines.py:102`, `:142`, `app/services/food.py:143`, `:365`, `app/services/cardio.py:308`, `app/services/imports.py:611-614`, `docs/contracts/routines.md:96` |
| Numbers through MCP | Plain `float` and `int` in every signature and nested model. Lax pydantic reads `true` as 1, `"80"` as 80, `"1_0"` as 10, and `80.0` as 80 on an int field. `dry_run` alone is `StrictBool` | `app/mcp_server.py:613-1264`, `:1091` |
| Numbers in the services | `rules.to_float` refuses bools and takes text through `float()`, which reads `"1_0"`, `"٨٠"` and `"８０"` as numbers. The web forms and the import's text reach it | `app/rules.py:245-254` |
| Ids in the services | `common.entry_id` refuses bools, takes an int or up to 10 ASCII digits, 1 to 2**31 - 1 | `app/services/common.py:174-187` |
| Rounding | `round()`, half to even, before the range check: `rules.number` (2 dp), `rules.bounded` (`kept` then the check), `rules.check_session`'s weight check on the rounded weight. After the check, or with no range: `rules.measurement_value`, `common.in_range`, `goals._value` | `app/rules.py:331`, `:367`, `:386-387`, `:471-472`, `app/services/common.py:616`, `app/services/goals.py:235` (U2) |
| A bounded fix | `rules.bounded` says "a whole number" for a "round" field too: "Give kcal as a whole number from 0 to 5,000." while 2.5 is taken | `app/rules.py:375-378` |
| Dates | `rules.to_date` is `date.fromisoformat`, which takes `20261116` and `2026-W01-1`. `entry_date`, `goals._future_date` and `get_log` read it. The import parses for itself, with the same two calls | `app/rules.py:257-266`, `:414-429`, `app/services/goals.py:485-494` (U2), `app/services/log.py:82`, `app/services/imports.py:259-289` |
| A page address date | `parse_day`: `ISO_DAY.fullmatch`, then `fromisoformat`, then after `date.min`. `ISO_DAY` is `\d{4}-\d{2}-\d{2}`, and `\d` matches any script's digits; `fromisoformat` then refuses them | `app/web.py:518`, `:1061-1069` (U2) |
| `get_log`'s window | `start = end - timedelta(days=count - 1)`. With `0001-01-01` and `days` above 1 this overflows: `internal_error`. The audit's call sent 7 days | `app/services/log.py:89` |
| The owner's ids and text | `account_id` goes straight into a query: 2**63 overflows, `internal_error`. `email` and `request_id` are not checked for control characters. Whether a NUL crashes on Postgres was not checked | `app/services/owner.py:41`, `:45-53`, `app/services/feedback.py:139-142` |
| A lift goal's target | `TARGETS["lift"]` is `(0, LIFT_MAX)`, low end left out. `in_range` checks `lo < n` but words it "0.0 is outside 0 to 666.67" | `app/services/goals.py:113` (U2), `app/services/common.py:606-616` |
| A goal's date | After today, nothing else: 9999-12-31 is taken | `app/services/goals.py:485-494` (U2) |
| A cardio time goal | Minutes 1 to 600 and distance 0.1 to 300, each alone: 5 km in 1 minute is taken. `log_cardio` refuses a worked-out speed outside 0.1 to 60 km/h | `app/services/goals.py:113-114` (U2), `app/services/cardio.py:219-234` |
| The Goals page's words | By (code, field): `("date_not_in_future", "target_date")` "Choose a day after today."; cardio_best `target_value` and `start_value` "from 1 to 600" | `app/web.py:407`, `:434`, `:443-445`, `:455` (U2) |
| Page help that disagrees with its check | Food kcal "give a whole number from 0 to 5,000" (2.5 is taken); cardio notes "under 2,000 characters" and lift notes "keep them under 2,000 characters" (2,000 is taken) | `app/web.py:282`, `:291`, `:1827` (U2; the audit read them one line lower) |
| The published list | `tests/test_feedback.py`'s `PUBLISHED` pins each function's published entry by hash | `tests/test_feedback.py:67-90` |
| The case file's order | Three tests pin ids by their place from the end | `tests/test_hi_there.py:1330-1331`, `tests/test_routines.py:1273-1275`, `tests/test_units.py:839` |

## What the audit found, and what changes

Each finding below was read against the code. Where the code said more than the audit, the row says so.

| # | Found | Read in the code | Changes, by decision |
|---|---|---|---|
| 1 | An unknown key is dropped: `log_measurement` with `"unit": "in"` stores cm, a food entry with `serving` stores 1 serving, a routine lift's `weight_lbs` and `log_cardio`'s `calories` vanish | As found, at every level. A top-level unknown key on `import_entries` is dropped too: the service's own check at `imports.py:611` is never reached | D1, D2, D3 |
| 2 | `true` and `false` become 1 and 0 on every number and id field | As found. Text such as `"80"` is taken too. No contract records that as a choice for MCP; the services take text on purpose, for the forms (`tests/test_import.py:1217-1218`) | D4 |
| 3 | Rounding before the range check: kcal -0.4 kept as 0, -0.01 lb taken, 0.06 mph kept as 0.1 km/h, 2.5 and 5000.5 round to even | As found. 881.849 lb is 399.99998 kg, inside the stored range, so taking it is right | D5 |
| 4 | `get_log` with `0001-01-01`: `internal_error` | Read from `log.py:89`, not run: the overflow needs `days` above 1, and the audit's call sent 7 | D8 |
| 5 | `owner_calls` and `owner_feedback` with `account_id` 2**63: `internal_error` | As found | D9 |
| 6 | A NUL in the owner's text fields is not checked and would likely crash on Postgres | Not checked on Postgres. Not checked at all is right | D10 |
| 7 | 1,031 wrong-type calls come back as the library's raw text | As found: no code, no fix, no `structuredContent`, no call-log row | D11, D12 |
| 8 | A lift goal at 0: "0.0 is outside 0 to 666.67" | As found | D13 |
| 9 | A refusal quotes 1.0 when `true` was sent | D4 ends it for bools. A whole number sent is still quoted with ".0" ("kg -100.0 is outside", "0.0 is outside"): open question 2 | D4 |
| 10 | Page help: kcal "whole number", notes "under 2,000" | As found, and the same in a text a model reads: `rules.bounded`'s fix for kcal | D14 |
| 11 | A goal's date takes 9999-12-31 | As found | D15 |
| 12 | A cardio time goal takes 5 km in 1 minute | As found. The slow end too: 0.1 km in 600 minutes | D16 |
| 13 | Chat dates take `20261116` and `2026-W01-1` | As found, in `rules.to_date` and separately in the import's parser | D7 |
| 14 | `1_0` reads as 10; the page takes `٨٠` and `８０` while chat refuses them | As found | D4 |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **Every unknown key is refused, at every level of every function, with `invalid_field`.** The top level: Spotter checks the sent keys against the function's published properties before the library validates. Nested: `SetIn`, `RoutineLiftIn` and `FoodEntryIn` take `extra="forbid"`. The import's three row models keep `extra="allow"`: its service already refuses an unknown row field as a row problem, so a preview still shows every row's fate. Published: `additionalProperties: false` on every function's top level and on the three nested models | A dropped field is the worst answer: the person's number is lost and nobody is told. Publishing it tells a new conversation's model before it sends |
| 2 | **The words of an unknown key.** At the top: "'{key}' is not a field of {function}." / "{function} takes: {fields}." In an item, the services' own words: "Set 1: '{key}' is not a field of a set." / "Use: exercise, weight_kg, weight_lb, reps."; "Entry 1: '{key}' is not a field of a food entry." / food.py's own fix; "Lift 1: '{key}' is not a field of a lift." / routines.py's own fix. `{fields}` is the published properties in their order. Every unknown key is named, through `rules.combine` | One wording a model already reads, wherever it meets it |
| 3 | **A near miss names the field meant,** as a sentence after the fix. The table below, then, for a key not on it, `difflib` at cutoff 0.75 over the fields taken: "The nearest field is {name}." No sentence when nothing is near | `calories` and `unit` are near no field by spelling |
| 4 | **Numbers: bools are refused on every number and id field, and plain text stays.** One grammar, `rules.NUMBER_TEXT`, ASCII only: outer spaces, at most one sign, digits, at most one decimal point, at least one digit. So `" 80 "`, `"+80"`, `"80."` and `".5"` are numbers; `"1_0"`, `"8e1"`, `"1,000"`, `"٨٠"`, `"８０"`, `"nan"` and `"inf"` are not. `rules.to_float` uses it, so the pages, the services and the import read alike. Through MCP one before-validator on every number field (`Number`) and every whole-number and id field (`Whole`), in `app/mcp_schemas.py`, refuses a bool and text outside the grammar, then pydantic parses as today: a JSON number is untouched, `8e1` as a JSON number is 80, and a whole JSON number such as 5.0 is still taken by an int field. The published `number` and `integer` types do not change. The import's row fields take `Number` and `Whole` too: a bad type there refuses the whole call, as a wrong-typed row does today (`tests/test_mcp.py:2393-2406`), and a bad value of the right type stays a row problem in the preview | Pick 14 says the same on both sides, which only holds if chat keeps text numbers. Refusing `"80"` costs a retry for nothing, and the library notes that one client sends lists and objects as JSON text (`func_metadata.py:228-238`): a number as text is the same habit, though no client was seen doing it. The check sits ahead of pydantic's parse, which reads `"1_0"` as 10 before any service sees it |
| 5 | **Every range is checked on the number as sent, unrounded, then kept at its step, halves away from zero.** In the unit sent: a lb or mi number is converted unrounded and checked against the stored range, which is the same range exactly. The keep is `Decimal(repr(x)).quantize(step, ROUND_HALF_UP)`. The sites: `rules.number`, `rules.measurement_value`, `rules.bounded`, `rules.check_session`'s weight check, `common.in_range`, `goals._value`. The import's `round()` calls at `imports.py:355`, `:462` and `:540-550` stay: each rounds a number already kept at its step, so it changes nothing. The boundary table is below | A number outside the range is refused, whatever step it is kept at. Half to even keeps 2.5 kcal as 2, which no person means |
| 6 | **Where the services' checks already refuse, through MCP they are reached.** A routine lift's unknown key now meets `routines.md` R5's promise, in its words | It was promised and not met |
| 7 | **A date is `YYYY-MM-DD` only, ASCII digits.** `rules.ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")`; `rules.to_date` takes a date, a datetime, or text that fully matches it and is a real day, its outer spaces forgiven as today. `web.ISO_DAY` reads `rules.ISO_DAY`. The import's date: the date alone fully matches it, and a timestamp must start with it, then goes on to `datetime.fromisoformat` as today. A date in another form is `invalid_date` with the fix each caller has now | `20261116` and `2026-W01-1` are not what the descriptions promise. The import's own parser held the same two forms |
| 8 | **`get_log` refuses a date before the oldest day the account can hold:** the account's `created_at` date in UTC, less `OLDEST_DAYS`, less one day for the time zone. `date_too_old`, "date 0001-01-01 is before 2024-11-14, the oldest day this account can hold." / "Give a date from 2024-11-14 to today, or leave it out for today." The dates are an example. A window of up to 14 days may start before the floor: from a floor in this century it never overflows | Changed from the pick, which refused dates more than 2 years back. Entries age: an entry imported 2 years back today would fall outside that window tomorrow, and `get_log` could no longer show it. No entry can be dated before this floor, so nothing the person has is hidden. The day page already shows any date (`/day/2024-09-07` serves 200). Open question 4 |
| 9 | **The owner's `account_id` passes through `common.entry_id`,** on `owner_calls` and `owner_feedback`. Anything it refuses: `invalid_field`, "account_id 9223372036854775808 is not an account id." / "Give an account's id as a line gives it: a whole number from 1." | The pick. A row id has one check |
| 10 | **The owner's `email` and `request_id` pass through `rules.text`,** with the column's length as the limit: 320 for `email`, 16 for `request_id` (`app/models.py:120`, `:253`). Its own words | The pick |
| 11 | **Every schema failure is Spotter's own refusal.** The shape: `{error, message, fix}`, with `errors` when there are several (`rules.combine`), as `structuredContent` and compact JSON text, `is_error` true, the shape every other refusal has. The code: `missing_field` for a missing required argument, `invalid_field` for every other. The place names a field as the services do: "Set 1: reps", "Entry 2: kcal", "Lift 1: sets", "Row 3, set 2: weight", and the top-level name alone. An unknown key (D1) and a schema failure in the same call come back as one refusal. The words are in the table below. An unknown function name is not mapped: it stays the library's answer, with no row, so a caller's chosen name is never logged (`tests/test_mcp.py:1015-1025`) | The assistant reads one shape and retries from its fix. `missing_field` is the code every service already gives for the same mistake: changed from the pick's `invalid_field` (open question 6) |
| 12 | **A schema refusal runs through `Wrapper.call`,** as a work that returns the refusal, so it meets the same order as every call: the account, the call-log row, the switch, the owner check, the read limit, the key's shape, then the refusal. A non-owner's bad `owner_calls` gets `not_allowed`. The row says `ok` false, the code, outcome `refused`, step `check`, and the names sent. It counts against the write limit as a service's refusal does today: `common._counted` counts every row of a write function, whatever its outcome (`app/services/common.py:369-397`, 120 in 10 minutes) | The owner sees these calls in `owner_calls`, which shows nothing of them today. `tests/test_mcp.py:2308-2320` and `:2393-2406` assert no row: they change |
| 13 | **A range with its low end left out says so:** "target_value 0 must be more than 0 and at most 666.67." `common.in_range` words it; `rules.call_unit` rewords it in lb as it rewords "is outside": "target_value 0 lb must be more than 0 and at most 1,469.75 lb." `goals._value`'s fix: "Give target_value in kg, more than 0 and at most 666.67, for a lift goal." | The page already says "more than 0 and at most" (`app/web.py:433`, U2) |
| 14 | **Help text matches its check.** The page lines and the bounded fix in the table "Words" below | Pick 10, and the same mismatch in a fix a model reads |
| 15 | **A goal's target date is at most 5 years after today,** chat and page: the same day 5 years on, and 28 February for 29 February (`rules.goal_latest(today)`). Later: `date_too_far`, a new code. An existing goal dated later stays as stored, and an edit that sends no `target_date` is not refused | Five calendar years, so the date the refusal names and the words "5 years" always agree |
| 16 | **A cardio time goal takes the speeds `log_cardio` takes.** For the goal's distance `d` km, `target_value` and `start_value` run from `max(1, d)` to `min(600, 600 × d)` minutes, the low bound rounded up and the high one down to the minutes' step: 0.1 to 60 km/h. A new goal and an edit alike, from the distance sent or the goal's own. When the distance sent is itself refused, the values get the 1 to 600 check alone, and the distance's refusal says what to fix | The pick. Both values, since a start of 5 km in 1 minute is as false as a target |
| 17 | **Three descriptions change,** in the words below: `set_goal`'s `target_date` and `target_value`, and `log_measurement`'s `value` | The model should know a limit before it meets the refusal |
| 18 | **`docs/can-and-cant.md` says the input rules,** in the words below | It is the resource a model reads for what Spotter takes |
| 19 | **Two eval cases,** 43 in all: `waist_in_inches` and `machine_calories`. No other | The two real losses of finding 1 a model can make. The rest is held by tests |

### The near misses (D3)

| Where | Key sent | The sentence after the fix |
|---|---|---|
| `log_measurement`, `edit_entry` | `unit`, `units` | "value is in the kind's own unit: cm for waist, hips, chest, arm and thigh. Spotter takes no inches for a body size." |
| A food entry | `serving`, `serving_count`, `qty`, `quantity`, `amount` | "For a saved food, send servings. For a USDA food, send grams." |
| A food entry | `calories`, `cal`, `kcals` | "For the calories, send kcal." |
| `log_cardio` | `calories`, `cal`, `kcal` | "For the calories the machine or watch showed, send machine_kcal." |
| `edit_entry` | `calories`, `cal` | "For a food entry's calories, send kcal. For a cardio entry's machine number, send machine_kcal." |
| `log_cardio`, `edit_entry` | `distance` | "Send distance_km or distance_mi, in the unit the person said." |
| `log_cardio`, `edit_entry` | `speed` | "Send speed_kmh or speed_mph, in the unit the person said." |
| `log_cardio`, `edit_entry` | `duration`, `time` | "Send the time as minutes." |
| `log_cardio`, `edit_entry` | `heart_rate`, `hr` | "Send the average heart rate as avg_hr." |
| `log_cardio`, `edit_entry` | `incline` | "Send the incline as incline_pct." |
| A set, a routine lift | `weight`, `weight_lbs`, `lbs`, `lb`, `kg`, `weight_kgs` | "Send the weight in weight_kg or weight_lb, in the unit the person said." |
| `log_bodyweight`, `edit_entry` | `weight`, `weight_kg`, `weight_lb`, `lbs` | "Send the body weight in kg or lb, in the unit the person said." |
| `update_settings` | `length_unit` | "Spotter keeps body sizes in cm: there is no length unit to set." |

### The words of a schema refusal (D11)

`{at}` is the place and a colon ("Set 1: "), empty at the top. `{on}` is " on set 1", empty at the top. `{sent}` is the value as JSON writes it, cut by `rules.quote`, in single quotes when it is text: `true`, `[1]`, `{}`, `'12abc'`. `{range}` is ", from {lo} to {hi}" only where the published schema has a minimum and a maximum (`get_log`'s `days`, the owner's `hours` and `limit`).

| What failed | Code | Message | Fix |
|---|---|---|---|
| A required argument left out | `missing_field` | "{at}{field} is missing." | "Give {field}{on}." |
| A number field given something else, a bool or text outside the grammar among them | `invalid_field` | "{at}{field} must be a number, not {sent}." | "Give {field}{on} as a number{range}." |
| A whole-number or id field given something else, a fraction among them | `invalid_field` | "{at}{field} must be a whole number, not {sent}." | "Give {field}{on} as a whole number{range}." |
| A true-or-false field given something else | `invalid_field` | "{at}{field} must be true or false, not {sent}." | "Send {field} as true or false, or leave it out." |
| A text field given something else | `invalid_field` | "{at}{field} must be text, not {sent}." | "Give {field}{on} as text." |
| A list field given something else | `invalid_field` | "{at}{field} must be a list, not {sent}." | "Send {field} as a list." |
| An item that is not an object | `invalid_field` | "{at}it must be an object of named fields." | "Send each item with its named fields: {fields}." |
| A number outside a published minimum or maximum | `invalid_field` | "{at}{field} {sent} is outside {lo} to {hi}." | "Give {field}{on} from {lo} to {hi}." |
| A list longer or shorter than published | `invalid_field` | "{field} holds {n} items: it takes {lo} to {hi}." | "Send {lo} to {hi} items in {field}." |
| An unknown key | `invalid_field` | D2 | D2 and D3 |
| Anything else pydantic refuses | `invalid_field` | "{at}{field} {sent} is not what {field} takes." | "Give {field}{on} as {its type in the words above}." |

### Rounding at the bounds (D5)

Worked on 2026-11-16 with Python 3.12: today is `round()`, after is the range check unrounded, then half away from zero.

| Sent | Today | After |
|---|---|---|
| kcal -0.4 | kept 0 | refused |
| kcal -0.01 | kept 0 | refused |
| kcal 2.5 | kept 2 | kept 3 |
| kcal 5000.4 | kept 5000 | refused |
| kcal 5000.5 | kept 5000 | refused |
| weight_lb -0.01 (-0.0045 kg) | kept 0 kg | refused |
| speed_mph 0.06 (0.0966 km/h) | kept 0.1 km/h | refused |
| distance_km 0.005 | kept 0.01 | refused |
| minutes 600.04 | kept 600 | refused |
| minutes 0.95 | refused (rounds to 0.9) | refused |
| servings 0.125 | kept 0.12 | kept 0.13 |
| lb 881.849 (399.99998 kg) | kept 400 kg | kept 400 kg |

### Words: page lines, fixes and descriptions

| Where | Old, in full | New, in full |
|---|---|---|
| `app/web.py:282` (U2) | "That kcal was not saved: give a whole number from 0 to 5,000." | "That kcal was not saved: give a number from 0 to 5,000. It is kept as a whole number." |
| `app/web.py:291` (U2) | "Keep the notes under 2,000 characters." | "Keep the notes to 2,000 characters or fewer." |
| `app/web.py:1827` (U2) | "Those notes were not saved: keep them under 2,000 characters." | "Those notes were not saved: keep them to 2,000 characters or fewer." |
| `rules.bounded`'s fix, a "round" field | "Give kcal as a whole number from 0 to 5,000." | "Give kcal as a number from 0 to 5,000: it is kept as a whole number." A "whole" field keeps its words |
| `app/web.py`, beside `:443-445` (U2), new | None | `("date_too_far", "target_date")`: "Choose a day at most 5 years from today." |
| `app/web.py:434`, `:455` (U2), cardio_best | "... in minutes, from 1 to 600." | The same line, with the range D16 gives for the distance on the form. With no distance yet, 1 to 600 as now |
| `date_too_far` | None | "target_date 9999-12-31 is more than 5 years after today, 2026-11-16." / "Give a target date after today, at most 5 years on: 2031-11-16 at the latest." |
| A cardio time goal out of D16's range | "target_value 1 is outside 1 to 600." | "target_value 1 is outside 5 to 600 minutes for 5 km." / "Give target_value as the minutes for the goal's distance, 5 to 600 for 5 km: a speed from 0.1 to 60 km/h, as log_cardio takes." The distance and the speed in the person's units |
| `set_goal` `target_date` | "The date to reach it by, YYYY-MM-DD, after today. " + `MONTH_END_RULE` | "The date to reach it by, YYYY-MM-DD, after today and at most 5 years on. " + `MONTH_END_RULE` |
| `set_goal` `target_value`, its last sentence | "A cardio_best: minutes for the distance, 1 to 600." | "A cardio_best: minutes for the distance, 1 to 600, at a speed from 0.1 to 60 km/h." |
| `log_measurement` `value` | "The value in the kind's unit: cm for the body sizes, steps, hours of sleep, bpm, ml." | "The value in the kind's unit: cm for the body sizes, steps, hours of sleep, bpm, ml. Spotter takes no inches." |
| `docs/can-and-cant.md:380` | "- No entry can be dated after today, or more than 2 years back." | "- A date is sent as YYYY-MM-DD. No entry can be dated after today, or more than 2 years back. A goal's target date is after today, and at most 5 years on." |
| `docs/can-and-cant.md`, the import's Dates table, a last row | None | "Any other form of a date, such as 20261116 or 2026-W01-1 \| Refused" |
| `docs/can-and-cant.md`, a new section "What a call can send", after "What today means" | None | "- **Only the fields a function lists.** Any other field is refused with `invalid_field`, and nothing is written. Its fix lists the fields the function takes, and names the nearest one when there is one." "- **Numbers as numbers.** true and false are never a number. Text that holds a plain number, such as \"80\", is read as that number. Other text, such as 1_0, 8e1 or digits of another script, is refused." "- **A refused call writes nothing.** Its fix says what to send instead." |

## What changes for a model

| What | Changes |
|---|---|
| `tools/list`, input schemas | Every function's top level gains `additionalProperties: false`: the 22 everyone sees and the owner's 3. `SetIn` (in `log_lifts` and `edit_entry`), `FoodEntryIn` (in `log_food`) and `RoutineLiftIn` (in `save_routine`) gain it too. The three descriptions of D17. Every `number` and `integer` stays as it is. So every published entry differs, and `PUBLISHED` moves for each one it pins |
| `tools/list`, output schemas | None. No result field is added, removed or made required |
| Results that succeed | The same fields. A number kept at a half now rounds away from zero (D5): 2.5 kcal is kept as 3 |
| Refusals | A schema failure is Spotter's JSON refusal in place of the library's lines (D11). New refusals: an unknown key (D1); a bool or odd text as a number (D4); a number that rounded into range (D5); a date in another form (D7); `get_log` before the account's floor (D8); a bad owner id or text (D9, D10); a goal date past 5 years (`date_too_far`, D15); a cardio time goal too fast or too slow (D16). New words: the low end left out (D13), the kcal fix (D14) |
| The instructions | Unchanged: 3,927 characters |
| `docs/can-and-cant.md` | D18 |

**The two-deploy rule.** It covers removing a required result field or a column. This work removes neither, and adds no result field. A stricter input schema is not an output change: a client checks Spotter's answers against the output schema, never against the input one. One deploy.

**A conversation begun before the deploy** holds the old function list: no `additionalProperties: false`, the old words of `set_goal` and `log_measurement`. Its model may send an unknown key or a far date as before. It now gets a refusal with a fix in place of a silent drop, and retries once. A schema refusal's text changes from the library's lines to Spotter's JSON: a model reads both as text. No output schema changes, so nothing it holds refuses an answer. A new conversation sees the new list.

## The package: one builder

| Files | Exit check |
|---|---|
| `app/mcp_server.py` (`SpotterServer.call_tool`: the unknown-key check and the schema refusal through `Wrapper.call`; `Number` and `Whole` on every number and id argument; `additionalProperties: false` on every published top level; the three descriptions; the module notes), `app/mcp_schemas.py` (`Number`, `Whole`, `extra="forbid"` on the three models, the near-miss table, the module notes), `app/rules.py` (`NUMBER_TEXT`, `to_float`, `ISO_DAY`, `to_date`, the range before the keep and half away from zero, `goal_latest`, `bounded`'s fix, `call_unit` for the low-open words), `app/services/common.py` (`in_range`), `app/services/goals.py` (D13, D15, D16), `app/services/log.py` (D8), `app/services/owner.py` and `app/services/feedback.py` (D9, D10), `app/services/imports.py` (the date's pattern, D7), `app/web.py` (`ISO_DAY` from rules, the page lines), `docs/can-and-cant.md`, `evals/cases.json`, `docs/evals.md`, `tests/test_inputs.py` (new). Existing test files only where an assertion fails because of a decision, each named in the hand-over. Expected: `tests/test_mcp.py` (`:613-625`, `:2308-2320` and `:2393-2406`, the library's text and the missing row; its 2099 dates), `tests/test_feedback.py` (`PUBLISHED`), `tests/test_evals.py` (`CONTRACT_CASES`), `tests/test_hi_there.py` (`:1330-1331`), `tests/test_routines.py` (`:1273-1275`), `tests/test_units.py` (`:839`), and the eleven other files whose goals use a 2099 date, which D15 refuses: `tests/test_calllog.py`, `test_entries.py`, `test_idempotency.py`, `test_isolation_mcp.py`, `test_lifts.py`, `test_page_rules.py`, `test_progress.py`, `test_trace.py`, `test_web.py`, `test_web_account.py`, `test_web_goals.py`. Each such date becomes one year after the test's own today, worked from the helper its file uses for today, never typed. Likely too: `tests/test_rules.py`, `tests/test_import.py`, `tests/test_log.py`, `tests/test_goals.py`, `tests/test_food.py`, `tests/test_safety.py` (`:489`, kcal ending in .5). The far dates in `tests/test_bodyweight.py`, `test_cardio.py`, `test_food.py`, `test_import.py`, `test_measurements.py` and `test_result_text.py` are entry dates, refused already, so D15 does not touch them. A test that fails for any other reason stops the build and goes to the lead | The sweep below passes. Every decision has a test of its own, each refusal's message and fix word for word. A valid call of every function answers as before: the existing files pass. Of the published list, every entry differs from today's only by `additionalProperties: false` at the places D1 names and the three descriptions of D17: a test diffs the two. Every `number` and `integer` is as published today. Every output schema is byte for byte as before. The instructions are 3,927 characters. A schema refusal writes one call-log row and no other row, and a non-owner's gets `not_allowed`. Every new message, fix, page line and can-and-cant line passes `tests/text_rules.py` on its surface. The two cases load, and judge a passing and a failing run each. `python -m evals.need main` says yes. The tests for the files touched pass, plain and with `-W error` |

No migration: no table or column changes, so no Postgres run is owed by this item.

## The tests

`tests/test_inputs.py`, new. One sweep, through `/mcp` and through every form, on one throwaway account built by `tests/conftest.py`.

- **The fields come from the code,** never a hand list: every property of every function in the owner's `tools/list`, nested through `$defs`, and every form the templates hold, read as `tests/test_idempotency.py:1068` reads them. A field added later joins the sweep by itself.
- **A base call per function and per form,** valid, held in the file. Each bad value replaces one field of it.
- **Dates are worked from the person's today** (`users.today_for`), never typed. `0001-01-01` and `9999-12-31` are the only fixed dates.
- **The values, by the field's published type:**

| Type | Values |
|---|---|
| `number` | `true`, `false`, `"12abc"`, `""`, `"   "`, `"1_0"`, `"8e1"`, `"٨٠"`, `"８０"`, `"nan"`, `"inf"`, `[1]`, `{}`, `-0.01` |
| `integer` | Every `number` value, and `2.5`, `2**31`, `2**63`, `10**30` |
| `string` | `"a\u0000b"`, `true`, `12`, `[1]`, `{}`, and one character over the field's limit where it has one |
| A date field | Yesterday as `YYYYMMDD`, as an ISO week date, and as `YYYY/MM/DD`; 30 February of this year; `0001-01-01`; `9999-12-31`; for an entry, tomorrow and 2 years and 2 days back; for `target_date`, today and the day after `rules.goal_latest(today)` |
| `boolean` | `[1]`, `{}`, `"perhaps"` |
| `array` | `"not a list"`, `{}`, and `[]` where the least is 1 |
| Every object, at every level | `"zz_unknown"`, and each near miss of D3 that applies there |
| A form's text field | `"a\u0000b"`; a number field `"12abc"`, `"1_0"`, `"8e1"`, `"٨٠"`, `"８０"`, `"-0.01"`; a date field the date forms above |

- **What each must give, everywhere:** no row in any of the account's tables, the call log and the key claims aside, counted before and after; never `internal_error` or `database_unavailable`; and no "validation error", "pydantic" or "Input should" in any text.
- **Through `/mcp`, beside that:** `isError` true, with `structuredContent` holding a code and a non-empty fix. Two answers are not refusals, by design, and pass when they name the bad value: an `import_entries` row field with a bad value of the right type comes back as a preview, or a write that wrote nothing, with that row counted refused and its problem listed (`imports.py:294-297` and the row checks); and an `owner_delete_feedback` id that is no note is named under `not_found` (its description says so).
- **Through a form:** no 500, the page's refusal, nothing saved.
- **Taken on purpose,** in one table in the file, each with its reason: a blank or spaces on an optional field (left out, as the forms send it); a confirm code that does not match, which returns the preview again (`delete_entry`'s design); a `send_feedback` part of 2,001 to 10,000 characters (`app/services/feedback.py:51`: every part takes 10,000); and any value a decision above takes. "One over the field's limit" reads each limit from the service's own constant. Nothing else may be taken.
- **Its time.** Under 60 seconds on this PC, measured and written in the build log. The audit's four runs took about 220 seconds; the sweep sends one value of each kind a type needs, not the audit's every value. If it runs over, values are cut by type, never fields.

Beside the sweep, one test per decision, each word for word: D2 and D3 for each row of the near-miss table; D4's grammar, both sides; the boundary table of D5; D7 through `log_bodyweight`, `get_log`, `set_goal` and the import; D8 at the floor and a day before it; D9 and D10; D11's table, a row each, and two problems as one refusal; D12's row and order; D13 in kg and lb; D15 on 28 February and 29 February; D16 at 0.1 km and at 300 km.

## The eval cases

Two. The other findings are held by tests: a model never sends `1_0` or a bool on purpose, and the refusals' words are pinned.

| Case | Seed | Says | Passes when |
|---|---|---|---|
| `waist_in_inches` | None | "My waist is 32 inches" | Either one successful `log_measurement`, kind waist, value 81.28 or 81.3, and the reply says "cm"; or no successful `log_measurement` and the reply asks. Every live waist row holds 81.28 or 81.3: never 32 |
| `machine_calories` | None | "30 minutes on the treadmill, it said 250 calories" | One successful `log_cardio` with minutes 30 and `machine_kcal` 250. One live cardio entry, `machine_kcal` 250 |

```json
{"id": "waist_in_inches",
 "say": "My waist is 32 inches",
 "expect": {"any_of": [
     {"function": "log_measurement", "ok_calls": {"log_measurement": 1},
      "inputs_include": {"kind": "waist", "value": {"$in": [81.28, 81.3]}}, "reply_has": ["cm"]},
     {"ok_calls": {"log_measurement": 0}, "reply_asks": true}]},
 "db": [{"table": "measurement", "all": {"kind": "waist", "value": {"$in": [81.28, 81.3]}}}]}

{"id": "machine_calories",
 "say": "30 minutes on the treadmill, it said 250 calories",
 "expect": {"function": "log_cardio", "ok_calls": {"log_cardio": 1},
            "inputs_include": {"minutes": 30, "machine_kcal": 250}},
 "db": [{"table": "cardio_entry", "count": 1, "all": {"machine_kcal": 250, "minutes": 30}}]}
```

| Case | Proves | Does not prove |
|---|---|---|
| `waist_in_inches` | A waist said in inches is never kept as 32 cm: it is kept in cm, or the person is asked | That the `unit` refusal was met: a model that reads `value`'s words converts or asks before any call. That any other body size or figure converts right: one figure |
| `machine_calories` | A machine's calories land in `machine_kcal`, not in a dropped `calories` | That the refusal's fix was read: a model that reads the schema sends `machine_kcal` at once. Which treadmill activity was taken: the cardio rule decides that, and `treadmill_once` checks it |

81.28 is 32 × 2.54; 81.3 is the same at 1 dp. The db check reads every live waist row, so a run with no row passes it and the expect decides. Both cases go last in `evals/cases.json`, in this order, and into `docs/evals.md`'s case table with a short section like the units cases'. The three order pins move by two, as `docs/contracts/tidy.md` T4 plans for a case appended later.

**The run.** Not per item: the release's one run, 43 cases at 3 runs on `claude-opus-5-5`, after this item. At the last measured rate, about $0.48 a case at 3 runs, these two add about $0.96 and the whole run is about $21, on the Claude side. Nothing on Railway. The cost is told to the owner before it runs.

## The exit check

1. Every row of the package's exit check holds.
2. The sweep passes, under 60 seconds.
3. The reviewer, on a frozen copy, repeats the audit's kinds of call by probe: each finding of the table above, sent as the audit sent it, is refused or taken as the decisions say, and writes nothing it should not.
4. The tests for the files touched pass, plain and with `-W error`. The hook runs the rest.

## Known limits

| Limit | Why it stands |
|---|---|
| A conversation begun before the deploy holds the old list: its model is not told of `additionalProperties: false` or the 5 years, and meets the refusal instead | As every function change. One retry, from the fix |
| An import sent again after the deploy, holding a number exactly halfway at its step (80.125 kg, 2.5 kcal), may not match its first copy: a weigh-in falls on a taken day, a food entry is written again | The first copy was kept half to even. Rare: a binary half at the step |
| Text that holds a plain number is still taken through MCP, though the schema says `number` | D4: some clients send numbers as text |
| Whether a NUL in the owner's text crashed on Postgres was never tried | The refusal is in place either way. No Postgres run is owed by this item |
| A lone surrogate in a call's JSON is answered HTTP 400 by the library, before Spotter sees it: the audit saw it on 27 calls. No Spotter refusal and no row, and nothing written | Not Spotter's code. Why it is 400 was not checked |
| A model that repeats one bad write 120 times in 10 minutes reaches `too_many_writes`, now for a schema refusal too | A service's refusal already counts so today (D12). One fix a retry ends it long before |
| A key that is neither on the near-miss table nor close by spelling gets no nearest name | A list, not a guess. The fix still lists every field taken |
| A goal already dated more than 5 years on stays, and so does a cardio time goal already outside D16's range | No row is rewritten. A new value sent for it is checked |
| `waist_in_inches` can pass with no refusal met | See its row above |

## Not in this work

| Left as it is | Why |
|---|---|
| 0 kcal, 0 steps, a 0 kg set, 0 weekly targets | Deliberate: `docs/contracts/stage-4.md:104` (kcal 0 to 5,000), `:115` (sessions 0 to 14, cardio minutes 0 to 3,000), `docs/contracts/stage-3.md:87` (steps 0 to 200,000), `docs/contracts/gym-logging.md:22` and `app/rules.py:437` (0 for bodyweight work) |
| A saved food sent with no servings is kept as 1 serving | `app/services/food.py:179`. No contract line was found for it: open question 7 |
| An edit with no `updated_at` runs as an edit | Deliberate: `docs/contracts/edit-history.md:37` (decision 10: "Through MCP the argument is not offered"), and `app/services/common.py:548-550` ("Missing or malformed: None, and the write runs as it would without it") |
| A goal field of another kind is dropped | With a warning in the result, not silently: `app/services/goals.py:581-592` (U2) |
| The import's row models keep `extra="allow"` | Its service refuses an unknown row field in the preview (D1) |
| Loose true and false (`"yes"`, `1`) on eight fields | Not on the card: open question 1 |
| A whole number quoted with ".0" in a refusal | Not on the card: open question 2 |
| A name made only of invisible characters | Not on the card: open question 3 |
| Inches for a body size | Later, by `docs/contracts/units.md`'s approval |
| A new function, a migration, a change to the instructions | None is needed |

## The owner's questions, decided on 2026-11-16: each pick below, as written

| # | Question | The pick, if he says yes |
|---|---|---|
| 1 | Loose true and false are taken: `"yes"` and `1` read as true on `allow_repeat`, `new_session`, `estimated`, `add_back_exercise_kcal`, `include_closed`, `undo_edit`, `no_account` and `failed_only`. The audit saw 15 such calls taken on those eight fields, `restore_entry`'s undo among them | `StrictBool` on each, as `dry_run` has: true, false or left out |
| 2 | A refusal quotes a whole number with ".0": "kg -100.0 is outside 20 to 400.", "target_value 0.0 is outside ...". Pydantic turns the sent 0 into 0.0 before Spotter sees it | `rules.as_sent` shows a whole number with no ".0" |
| 3 | A name of only a zero-width space is saved: a saved food's name and serving, a routine's name, a feedback note's text, as the audit saw | `rules.required_text` counts text of only invisible characters as missing |
| 4 | D8 changes his pick for `get_log`. His pick, refusing more than 2 years back, would hide entries as they age past 2 years, imported ones within days of their import | D8 as written. His own wording is the one-line alternative: refuse a date before today less `OLDEST_DAYS` |
| 5 | Text numbers such as `"80"` stay taken through MCP (D4). No record says that was chosen | D4 as written. The alternative: refuse text on every MCP number field |
| 6 | A missing required argument is `missing_field`, not the pick's `invalid_field` (D11). The same reason, the code a service already gives, would make a wrong-typed weight or reps `not_a_number` or `not_a_whole_number` (`app/rules.py:324`, `:328`); D11 keeps `invalid_field` for those, the code `rules.bounded` and most services give | D11 as written |
| 7 | A saved food sent with no servings is logged as 1 serving. `FoodEntryIn` says "Send servings with it" | Keep it. The alternative: `missing_field` for servings |
| 8 | `waist_in_inches` passes a conversion by the assistant. `docs/contracts/units.md:51` calls converting in the assistant a break of "never recalculate" | The case as written, which passes a conversion or a question |
