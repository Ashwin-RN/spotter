# Units contract: a person logs and sees weights in lb and distances in miles

**Goal: a person who lifts in pounds and runs in miles says "bench 225 for 5" and "ran 3 miles in 27 minutes", and every reply, every page and every chart says 225 lb and 3 mi back. Spotter keeps kg and km underneath, as it always has.**

Approved by the owner on 2026-11-15, with every pick of its card: ask once, after a person's first lb or mile log; two settings, weights (kg or lb) and distances (km or miles); inches for a body size later, not here; built after the push of gym logging, first day and routines, which has happened. It is built on `76df357`, after the tidy-ups package.

## His words

The standing ones: "a smooth UX", "Let's not overcomplicate things out of the bag", "we have to be a one-for-one replacement for them". On units, gym logging's contract left it out by name: "Not in this work: pounds and miles on a log (another item)". On the card, 2026-11-15: "save your picks as the decisions".

## What exists today

Read at `76df357` on 2026-11-15: gym logging, first day and routines all merged, migrations up to `0010`. The builder reads these rows again on the commit it starts from, which is `76df357` plus tidy-ups.

| Piece | State | Where |
|---|---|---|
| A set's weight | `SetIn.weight_kg`, a required float, "The weight lifted in kg, 0 to 500. Use 0 for bodyweight work." `rules.check_session` checks it: "Set 1: weight 600 kg is outside 0 to 500 kg." / "Give weight_kg on set 1 from 0 to 500 (0 for bodyweight)." Its count refusal names the fields: "exercise, weight_kg, reps" | `app/mcp_schemas.py:27-32`, `app/rules.py:31-34`, `:326-373` |
| A weigh-in | `kg`, a required float, 20 to 400. `body.check_kg`: "kg is missing." / "Give the body weight in kg, 20 to 400." | `app/mcp_server.py:641`, `app/rules.py:36`, `app/services/body.py:51-57` |
| Cardio | `distance_km` (0.01 to 300), `speed_kmh` (0.1 to 60). The speed checks quote km: `speed_disagrees`, `ran` ("5 km in 30.5 minutes"), `with_speed` | `app/mcp_server.py:986-988`, `app/rules.py:72-74`, `app/services/cardio.py:130-180` |
| An edit | `edit_entry` takes `kg`, `distance_km`, `speed_kmh` and a whole set list of `SetIn`. Its description says "A weigh-in: its kg or date." The summary names each field: "Weigh-in changed: kg from 74.8 to 78.4." A cardio speed that no longer fits quotes km/h | `app/mcp_server.py:800-850`, `app/services/entries.py:304-327`, `:472-498`, `:540-559` |
| Edit history | `entry_version.before` holds the row as JSON before each edit or undo, a session's sets with it, in kg and km as stored. `keep_version` writes it in the write's own transaction. `entry_versions.csv` exports it | `app/models.py:274-291`, `app/services/common.py:501`, `app/services/export.py:7-12` |
| A goal | `set_goal`: `distance_km` (0.1 to 300); `target_value` and `start_value` "in the kind's unit": kg for a lift (above 0 to 666.67) or body weight (20 to 400). `goals.UNITS` fixes "kg" for both. `label` writes "Back Squat 100 kg"; `plan_line` and `_start`'s warnings write kg through `goal_quantity` | `app/mcp_server.py:653-700`, `app/services/goals.py:103-108`, `:139-146`, `:181-202`, `:318-354`, `:573-590` |
| What is stored | Float columns: `lift_set.weight_kg`, `bodyweight.kg`, `goal.distance_km`, `goal.unit` ("kg" for a lift and a body weight goal), `cardio_entry.distance_km`, `speed_kmh` and `estimate_weight_kg`. A sent number is kept at 2 decimals (`round(parsed, 2)`) | `app/models.py:178`, `:197`, `:209-212`, `:409-415`, `app/rules.py:225` |
| The only conversion | `import_entries`: `weight_unit` kg or lb, `distance_unit` km or mi, `length_unit` cm or in, named per call, never assumed. `UNITS`, `_converted`, `UNIT_OF`, `_inward` (a stored range in the call's unit, each bound moved inward) and `_call_unit` (a refusal said in the call's unit). `_speed_in_call_unit` words a speed refusal in miles ("mi an hour") | `app/services/imports.py:106-108`, `:151-212`, `docs/can-and-cant.md:199-209` |
| The user row | `time_zone`, three targets, `claude_can`, `add_back_exercise_kcal`. **No unit** | `app/models.py:108-131` |
| `update_settings` | Sets `time_zone`, the three targets and `add_back_exercise_kcal`, each through a check in `SETTABLE`, each change said in `SAID`. Its refusals list `SETTABLE`'s names | `app/services/account.py:216-283`, `app/mcp_server.py:717-752` |
| `whoami` | Account, time zone, today, targets, `claude_can`, links, `time_zone_note`, `new_account_note`, `setup`. **No unit**. Its description ends " If it carries setup, walk setup.missing as setup.how says." | `app/services/account.py:94-134`, `app/mcp_schemas.py:286-312`, `app/mcp_server.py:566-580` |
| The one place a number is rounded for display | `formatting._TABLE`: `weigh_in` and `weight_kg` 0.01 kg, `bodyweight_derived` and `estimated_max` 0.1 kg, `weight_change` 0.01 kg, `km` 0.01 km, `speed` 0.1 km/h, `pace` m:ss. `rate` and `goal_target` carry no unit: the caller writes it. The widened step is a tenth of the step, and keeps its zeros. Trailing zeros go otherwise | `app/formatting.py:39-73`, `:113-138`, `:193-212`, `:215-224` |
| Texts a model quotes, each in kg or km | `lifts._widened`, `best_text`, `first_text`, `sets_text`, the new and the added session summaries; `cardio.summary` ("5 km, pace 6:00 per km", "from body weight now, 80.2 kg"); the trends summary ("kg a week"); a goal's `label`, `plan_line`, warnings and refusals; edit summaries; the weigh-in replace note (`common.pair`); `routines.lift_words` and the save summary ("at 60 kg") | `app/services/lifts.py:51-113`, `:296-297`, `:346-347`, `app/services/cardio.py:218-231`, `app/services/trends.py:106-114`, `app/services/goals.py:318-354`, `app/services/entries.py:472-498`, `app/services/body.py:120-126`, `app/services/common.py:557-565`, `app/services/routines.py:68-76`, `:185` |
| Gym logging's session adds | Each `log_lifts` call for a date adds its sets to that day's session, kept first as an edit (`keep_version`), so the one-step undo takes the last add back. "Added ... to the session on ... It now has 4 sets." | `app/services/lifts.py:249-300` |
| Routines | `RoutineLiftIn.weight_kg` (0 to 500 or null); the stored JSON holds `weight_kg`; `LIFT_FIELDS` and the refusal "Use: exercise, sets, reps, weight_kg." `get_routines` gives each lift `weight_kg` to log (the latest session's heaviest set, unless the routine was saved on a later day), and a summary of **names only**: "2 routines: Push day and Pull day." Its description: "Where weight_kg is null, ask the person for it." The Routines page builds its words with `lift_words` from `get_routines`; `routines.html` itself holds no unit word | `app/mcp_schemas.py:41-50`, `:1261`, `:1286-1314`, `app/mcp_server.py:1108-1143`, `app/services/routines.py:45`, `:68-121`, `:185`, `:190-220`, `app/web.py:2194-2203` |
| Results with no text for their weights | `log_bodyweight` (no `summary`), `get_progress` (no `summary`), `get_log` (its `summary` counts entries only; a day has none), `delete_entry` (the entry as a dict) | `app/mcp_schemas.py:419-435`, `:716-729`, `:864-871`, `app/services/log.py:122-125`, `app/mcp_schemas.py:808-820` |
| `get_trends` and `get_progress` | Number fields in kg (`kg`, `smoothed`, `rate_per_week`, `estimated_max_kg`, `slope_per_week`). `get_trends`' summary writes "kg a week". `get_progress` gives a cardio activity's sessions with `pace.min_per_km` | `app/services/trends.py:106-139`, `app/services/progress.py:52-141` |
| The instructions | 3,927 characters of 4,096. "Quote each number as Spotter gives it, with its unit: never add decimals, never round further, and never recalculate one yourself. Where a summary gives a figure, say the summary's figure." The cardio rule says 7 km/h | `app/mcp_server.py:183-207`, `tests/test_switch.py:556-558` |
| The published list | `tests/test_feedback.py`'s `PUBLISHED` pins each function's published entry, its result schema included, by hash | `tests/test_feedback.py:67-90`, `:528-529` |
| Pages | kg and km written into the templates: "Weight (kg)", "kg trend", "kg a week", "Distance (km)", "Pace (per km)", "Speed (km/h)", "Target weight (kg)", "Where you are now (kg)", tiles and sheets on Home and Progress, the sets table | `app/templates/_parts.html:102-141`, `:194-202`, `day.html:56`, `:119`, `:223`, `:248-249`, `:278`, `:292-307`, `edit.html:23-36`, `goals.html:109-120`, `progress.html:52-93`, `:129-163`, `progress_lift.html:23-67` |
| How a page shows a number | The `show` filter is `formatting.show` with a no-break space; `page()` builds every render's values from `request.state.user` | `app/web.py:129-138`, `:635-659` |
| Page forms and their refusals | The weigh-in form posts `kg`, the cardio form `distance_km`, the edit form `kg`, `distance_km`, `speed_kmh`; `edit_values` fills them at the stored decimals, and `changed_fields` sends a number field only when the typed number differs from the stored one; the goal form's labels and refusals say kg and km | `app/web.py:195-253`, `:321-382`, `:1666`, `:1709-1775`, `:1865-1901` |
| Settings | Cards for the time zone, the targets and the switch (`SETTINGS_CARDS`, `SETTINGS_SAVED`). The switch's words: "It can change your time zone and targets, ..." | `app/web.py:264-268`, `:277-286`, `:1269-1360`, `app/templates/settings.html` |
| Charts | Ticks and labels in kg (`KG_STEPS`, "whole kg"), `_goal_kg` ("Goal 100 kg"), "-0.25 kg a week", "Pace per km" | `app/charts.py:51-62`, `:222-224`, `:263-285`, `:304-344`, `:408-417`, `:557`, `:593`, `:690-697` |
| The export | Columns named with their unit (`weight_kg`). README "each weigh-in, in kg". `account.csv` has no unit | `app/services/export.py:48-49`, `:68-101` |
| The hi page | "It logs weights in kg and distances in km. An import from another app converts pounds and miles.", pinned by `tests/test_hi_there.py:178`. Its sample sentences say kg ("Back squat, 3 sets of 5 at 60 kg") | `app/templates/hi.html:77`, `app/web.py:1054-1072`, `:1157-1185` |
| The text rules | `_UNITS`, which tells a figure from an entry's id, holds kg, km and km/h, **not lb, mi or mph**: "Weigh-in 185 lb" is flagged as an id on the summary surface today | `tests/text_rules.py:351-355` |
| The trace | `KNOWN_NAMES` holds `weight_unit` and `distance_unit` (the import's), not `lb`, `distance_mi` or `speed_mph`. Input models' fields join by themselves | `app/calllog.py:63-70` |
| Evals | 35 cases. Seeds: `save_food`, `log_bodyweight`, `log_measurement`, `log_lifts`, `set_goal`, `import_entries`, `edit_entry`, `save_routine`, and `four_weeks`. Not `update_settings`. A seed's `as` is optional. A case can check `reply_has`, `reply_lacks`, `reply_asks`, and `{"$in": [...]}` at any leaf. No case logs lb outside the import | `evals/judge.py:13-58`, `:75-80`, `:100-125`, `evals/cases.json` |

## The three designs weighed

| Design | What it does | Verdict |
|---|---|---|
| (a) A unit setting on the account that the pages and replies follow; storage stays kg and km | Shows lb, but a log still takes `weight_kg`. Either the field quietly means lb on a lb account, or the assistant converts | Not alone. A field named `weight_kg` that holds pounds writes 225 kg the day an assistant reads the name and not the setting. Converting in the assistant breaks "never recalculate" |
| (b) Unit fields on each log function, like the import's, no setting | The number goes in as said | Not alone. Every reply, page and chart still says 102.06 kg for the 225 the person said |
| (c) Both: a field for each unit on every log, and two settings that every page and text follows | Said in lb, stored in kg, shown in lb | **Chosen.** (a) needs (b)'s fields to be safe, and (b) needs (a)'s setting to show anything |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **Stored units never change: kg and km, at 2 decimals, for every account and every row.** A setting changes what is shown, never what is kept. No row is rewritten, by a switch or by a migration. `entry_version.before` stays in kg and km, so an undo puts back the exact stored number | Bests, Epley, the cardio formula, 7,700 kcal a kg, the import's matching, the edit history, the confirm codes and the export all read one unit. A lb said is held to 0.011 lb at 2 dp of kg, under half of the 0.05 lb step (decision 9), so it always comes back as said |
| 2 | **Two settings on the account: `weight_unit` (kg or lb) and `distance_unit` (km or mi).** Two nullable columns on `user`. Null means not chosen, and shows kg and km | A Canadian lifts in lb and runs in km; a Briton runs in miles and lifts in kg. Null tells "never asked" from "chose kg" (decision 12) |
| 3 | **Set with `update_settings`** (`weight_unit`, `distance_unit`, the import's names, case and outer spaces forgiven), and on a new Settings card, "Units" | One place for every setting, the import's words for the same thing |
| 4 | **A number goes in the field of its unit.** A set: `weight_kg` or `weight_lb`, in `log_lifts`, `edit_entry` and a routine's lifts. A weigh-in: `kg` or `lb`. Cardio: `distance_km` or `distance_mi`, `speed_kmh` or `speed_mph`. Exactly one of each pair: both is `invalid_field`, neither is `missing_field` where one is needed. A null is not sent. `kg` on `log_bodyweight` becomes optional | The name says the unit, so a stored number never depends on the assistant having read a setting. Each set carries its own, so lb dumbbells beside a kg bar log as said. A kg client sends what it sends today |
| 5 | **A goal's target takes `weight_unit` on `set_goal`** (kg or lb) for a lift or a body weight goal, and `distance_mi` beside `distance_km`. Left out, `weight_unit` is the person's own setting | `target_value` and `start_value` have no unit in their name, and serve cm, bpm and minutes too |
| 6 | **The account's units travel by hand.** `users.units_for(user)` gives `Units(weight, distance)`, kg and km for null. Each service that writes a text passes it; `formatting.show` takes `units`; every page render gets `units` and the `show` filter reads it from there; the charts take it | `formatting` stays pure: no hidden input. Every service already holds the user row |
| 7 | **A kg and km account reads every text exactly as today,** with four named exceptions that hold for every account: the refusals that list a set's or a lift's fields name `weight_lb` too; a missing weigh-in weight is "kg or lb is missing." with a fix naming both; `update_settings`' refusals list its two new settings; and the new summaries and `words` of decision 11 are new for everyone | No existing text, test or eval case moves for the majority |
| 8 | **Every text follows the units:** summaries, `new_bests[].text`, the mark line, a goal's `label`, `plan_line` and warnings, the replace note, edit summaries, `save_routine`'s summary, `get_routines`' new `words`, every refusal of a number sent in lb or mi (in that unit, the range moved inward, as the import's are), every page, every chart. **Number fields stay in kg and km,** their names unchanged | The instructions already say to quote a summary's figure. Renaming or converting number fields breaks every client and test that reads them |
| 9 | **Shown steps in lb and mi.** A set, a weigh-in and a goal's target: 0.05 lb. Body weight now, the smoothed trend and an estimated max: 0.1 lb. A rate and a change: 0.01 lb. Miles 0.01, mph 0.1, a pace m:ss per mi. **The widened step in lb is 0.01 lb for the 0.05 and 0.1 lb steps, 0.001 lb for 0.01.** kg and km steps unchanged. As in kg, trailing zeros go, except in a widened figure | 0.05 lb is the finest step the 2 dp kg store always gives back exactly: 225, 47.5 and 46.25 read as said. Two stored numbers 0.01 kg apart are 0.022 lb apart, so 0.01 lb always tells them apart: a tenth of 0.05 (0.005) would print a third decimal for nothing |
| 10 | **Close calls are judged in the shown unit.** When a best and the one it beat, a goal's now and plan, or an edit's before and after read the same in lb, they widen together (`lifts._widened`, `common.pair`, `formatting.widen` take `units`) | "225 lb, up from 225 lb" happens when a kg-said set beats a lb-said one by 0.01 kg |
| 11 | **Results with no text for their weights get one,** in the person's units, built from the texts that exist: `log_bodyweight` `summary`; `get_progress` `summary`; each day of `get_log` a `summary`; `delete_entry` `summary` for a session, a weigh-in, a cardio entry and a routine (null for the other kinds); each routine of `get_routines` `words` | A lb lifter's weigh-in, "what did I bench Monday", "how is my squat", "delete that" and "what is in my push day" all need a figure in lb, and the assistant may not convert one |
| 12 | **Asked once.** While a unit column is null, a write that sent `weight_lb`, `lb` or `weight_unit` lb (or `distance_mi`, `speed_mph`) carries `units_note`: ask once whether to show it everywhere, and save the answer either way. Saving kg or km ends it as surely as lb or mi. The import never carries it | The American's first "I weigh 185 lb" is the moment to ask, and a kg person never hears the question. No setup step |
| 13 | **`whoami` carries `units`** `{weight, distance}`, the effective ones. Its description gains one sentence | `whoami` is called first. The model learns there that a bare 225 is lb |
| 14 | **The instructions do not change: 3,927 characters.** The words are in `whoami`'s description and `units`, the new fields' descriptions and `units_note` | No room |
| 15 | **One table of factors,** the import's `UNITS`, `UNIT_OF`, `_converted`, `_inward` and `_call_unit`, moved to `app/rules.py` (as `UNITS`, `UNIT_OF`, `converted`, `inward`, `call_unit`); the import reads them from there and is otherwise not changed, `_speed_in_call_unit` included | Logging and the import convert and refuse with the same words |
| 16 | **A page form shown in lb or mi that comes back unchanged changes nothing.** The edit page fills a weigh-in, a distance and a speed at the shown step; a field whose typed text equals what was filled keeps the stored number | 80 kg fills as 176.35 lb, which is 79.99 kg sent back: an untouched form must not edit |
| 17 | **The export stays in kg and km.** `account.csv` gains `weight_unit` and `distance_unit` (blank: not chosen); the README says every file is in kg and km whatever the pages show. `routines.csv` and `entry_versions.csv` hold kg as stored | Columns named with their unit read the same for every account |
| 18 | **Migration `0011`**, after routines' `0010`: two nullable `String(2)` columns on `user`. No data change, no default | Null is "not chosen". A nullable column needs no table rebuild on SQLite |
| 19 | **The trace:** `lb`, `distance_mi`, `speed_mph` join `KNOWN_NAMES`; `weight_lb` joins through `SetIn` and `RoutineLiftIn` | The owner sees the names a call sent |
| 20 | **Evals: six cases,** 41 in all. The draft's five, and `lb_routine_changed`, added at `76df357` because routines is now in the tree. `update_settings` becomes a seed service (no `as`: it returns no id) | No case logs lb outside the import, none checks a reply's unit, and none checks that a routine's kg weights go in `weight_kg` on a lb account |

## Rulings on the how

Made on `76df357`, where the decisions leave a how open or the code moved under them. None changes a decision.

| # | Ruling | Why |
|---|---|---|
| R1 | **The base.** `76df357` plus tidy-ups. Routines is in the tree, so decisions 4, 8 and 11 cover `save_routine` and `get_routines` (R10). No function is added: 22 for everyone, 14 writes, and every count of functions stays. Only the count of eval cases moves, 35 to 41, in the one place tidy-ups keeps it | What moved since the draft |
| R2 | **The seam, `app/formatting.py`.** `Units = NamedTuple("Units", weight=str, distance=str)`, `KG_KM = Units("kg", "km")`. `show`, `field`, `widen`, `is_zero` and a new `convert(value, quantity, units) -> float` take a keyword `units=None`; None or `KG_KM` is today's behaviour, byte for byte. With `units.weight == "lb"`, a quantity in `WEIGHT_QUANTITIES` is divided by `LB` (0.45359237) and shown at its lb row; with `units.distance == "mi"`, `km` and `speed` are divided by `MI` (1.609344) and `pace` multiplied by it. `unit_word(kind, units)` gives "kg" or "lb" (`weight`), "km" or "mi" (`distance`), "km/h" or "mph" (`speed`). `rounded` takes no `units`: it rounds number fields, which stay kg | Every other package reads only this. `convert` is for the charts, which place points in the shown unit |
| R3 | **The lb and mi rows**, beside `_TABLE`: `weigh_in`, `weight_kg` and `weight_target` 0.05 lb (`weight_target` with no unit); `bodyweight_derived` and `estimated_max` 0.1 lb; `weight_change` 0.01 lb; `weight_rate` 0.01 with no unit, signed. `km` 0.01 "mi", `speed` 0.1 "mph", `pace` m:ss. Each row carries its widened step (decision 9) and its form decimals: a lb or mi form field is filled at the shown step | Decision 9 |
| R4 | **Two new kg quantities, `weight_rate` and `weight_target`,** the same as `rate` and `goal_target` in kg, so a kg text is unchanged. Every caller that shows a rate of a weight (`trends._summary`, the tiles and sheets, `charts`' "a week" line) or the target of a lift or body weight goal (`goals.label`, `said`, the goal page and chart) moves to them. `rate` and `goal_target` stay for cm, bpm and every other caller. `WEIGHT_QUANTITIES` is the seven weight rows of R3 | A unitless quantity cannot know it is a weight; a measurement goal's cm must never be divided by 0.45 |
| R5 | **`users.units_for(user) -> Units`**: the two columns, kg and km for null. `units_set(user) -> (bool, bool)`: whether each column is set, for `units_note` | Decision 6 |
| R6 | **The checks of a pair, in `rules`.** `rules.one_of(row, kg_field, other_field, where, needed)`: both sent (neither None nor "") is `invalid_field` "Set 2 has both weight_kg and weight_lb: send one." / "Send the weight in the unit the person said: weight_kg or weight_lb."; neither, when `needed`, is `missing_field` "Set 2: weight_kg or weight_lb is missing." / "Give weight_kg or weight_lb on set 2." Without a `where`, the same words with no "Set 2" ("Both kg and lb were sent: send one."). An `other_field` value is parsed by `rules.to_float`, passed through `rules.converted` to the stored unit unrounded, and checked by the kg or km check that exists; a refusal of it is reworded by `rules.call_unit` with the inward range in its unit (0 to 1,102.31 lb; 44.1 to 881.84 lb; 0.01 to 186.41 mi; 0.1 to 37.2 mph). The stored number is that check's own, at 2 dp | The import's order, so logging and the import refuse alike. Every bound here is worked with `inward` on 2026-11-15 |
| R7 | **`units_note`, in `app/services/common.py`.** `units_note(user, sent_weight_lb: bool, sent_miles: bool) -> str | None`: the weight sentence when `sent_weight_lb` and `weight_unit` is null, the distance sentence when `sent_miles` and `distance_unit` is null, both joined by a space when both hold. Set on the result of `log_lifts`, `log_bodyweight`, `log_cardio`, `edit_entry`, `set_goal` and `save_routine`, after a write that succeeded, a duplicate included. A refusal carries none | Decision 12 |
| R8 | **Lift texts.** `best_text`, `first_text`, `sets_text`, `_widened` and the two session summaries take `units`. `sets_text` groups sets by their shown weight and reps, so two sets that read 225 lb × 5 read "2 × 5 at 225 lb" whatever kg they hold; in kg shown equals stored, so no kg text moves. `log_lifts` passes `units_for(user)`; `get_log` does too, so `new_bests[].text` and the day page follow. `rounded_best` (number fields) is unchanged | Decisions 8 and 10 |
| R9 | **`log_lifts` through MCP.** `SetIn`: `weight_kg: float | None = None`, `weight_lb: float | None = None`. The sets go to the service as `s.model_dump(exclude_none=True)`, in `log_lifts` and `edit_entry`, so the trace and the service see only what was sent. `check_session` runs `one_of` per set (needed), converts `weight_lb`, and returns sets in kg as now. The count refusal's fix: "Send one row per set, 1 to 60 rows: exercise, weight_kg or weight_lb, reps." | Decision 4 |
| R10 | **Routines.** `RoutineLiftIn` gains `weight_lb: float | None = None`. `LIFT_FIELDS` becomes `("exercise", "sets", "reps", "weight_kg", "weight_lb")`, and its refusal "Use: exercise, sets, reps, weight_kg or weight_lb." `_lift` runs `one_of` (not needed: a lift may carry no weight) and converts `weight_lb`. The stored JSON keeps `{"exercise", "sets", "reps", "weight_kg"}` in that order, kg, so "the same lifts again write nothing" compares kg and `routines.csv` is unchanged. `lift_words(items, page, units)`. The save summary in the person's units. `get_routines`: each routine gains `words`, `lift_words` of its lifts with the weight to log, in the person's units; `weight_kg` stays kg; `summary` stays names only (a kg text does not move) | Decisions 4, 8 and 11. The assistant sends a routine's weights as `weight_kg`, as given: its field says kg and holds kg |
| R11 | **Cardio.** `log_cardio` and `edit_entry` take `distance_mi` and `speed_mph` through `one_of` (not needed). `cardio.summary(entry, today, units)`: "3 mi, pace 9:00 per mi", the pace `metrics.pace(distance_km / MI, minutes)`, and "from body weight now, 185.2 lb". On a call that sent miles, `speed_disagrees`' and `with_speed`'s refusals and `entries._cardio_speed`'s are worded in miles by `cardio.in_miles(err, raw, values)`, built after the import's `_speed_in_call_unit`, with "mph" for the speed. The import's own words ("mi an hour") are not changed | Decision 8. The import is not changed (decision 15) |
| R12 | **Goals.** `set_goal` takes `weight_unit` (kg or lb, case forgiven, else `invalid_unit` in the import's words) and `distance_mi`. For a lift or a body weight goal, `target_value` and `start_value` are read in `weight_unit`, else the person's own; a lb value is converted, and its refusal said in lb with the inward range (above 0 to 1,469.74 lb; 44.1 to 881.84 lb; a goal distance 0.07 to 186.41 mi). `goal.unit` stays "kg". `label`, `plan_line`, `said` and `_start`'s warnings take `units`; `goal_quantity` is unchanged and `show` converts. `get_goals`' summary follows through `label` | Decisions 5 and 8 |
| R13 | **Edits and deletes.** `edit_entry` takes `lb`, `distance_mi`, `speed_mph` and `weight_lb` in sets. Per setting: on a lb account a changed weight is named in words with its unit ("Weigh-in changed: weight from 185 lb to 184 lb."), on a mi account a changed distance or speed ("distance from 3 mi to 3.4 mi", "speed from 6 mph to 6.5 mph"); sets go through `sets_text`. A kg weight, a km distance and a km/h speed keep the field's name, as today, so a lb and km account's cardio edit still says `distance_km`. `delete_entry` gains `summary` (R14) on the preview and on the delete. `restore_entry` is unchanged | Decisions 8 and 11 |
| R14 | **The new summaries, words in full.** `log_bodyweight`: "Weigh-in 185 lb on Sun 15 Nov.", then on a replace " It replaces 186 lb, kept, not erased.", then with a 7-day average " 7-day average 184.6 lb."; a duplicate: "Already logged: 185 lb on Sun 15 Nov. Nothing new was written." `get_progress`, one lift: "Bench Press: estimated max now 262.5 lb. Best set 225 lb × 5 on Sun 15 Nov. 12 sessions.", then with a trend " Trend +1.2 lb a week over 6 weeks."; every lift: each lift's first sentence and its session count, one line a lift; a lift never done above 0: "Pull-up: most reps 12, 8 sessions."; a cardio activity: its session count, then its latest session as `cardio.summary` words it. `get_log` day: the sets of each session as `sets_text` words them, then "Weigh-in 185 lb.", then each cardio summary; empty when the day has none. `delete_entry`: a session "Lift session on Sun 15 Nov: Bench Press 3 × 5 at 225 lb.", a weigh-in "Weigh-in 185 lb on Sun 15 Nov.", a cardio entry and a routine their summaries' words; null for a measurement, a food entry or a saved food | Decision 11. Each passes `check(text, SUMMARY)` once R18 is in |
| R15 | **The pages.** `page()` puts `units` (`users.units_for(user)`, `KG_KM` signed out) in every render. The `show` and `field` filters become `pass_context` and read it. `unit_word` is a global, so "Weight ({{ unit_word('weight', units) }})" and "kg a week" become the unit in use. The weigh-in form posts `lb` on a lb account, the cardio form `distance_mi`, the edit form `lb`, `distance_mi`, `speed_mph`, each filled at the shown step (`formatting.field(..., units=)`). `changed_fields` today compares the typed number with the stored one, which a lb fill never equals (80 kg fills as 176.35); for a field filled in lb or mi it compares the typed text with the filled text instead, so an untouched field is never sent (decision 16). A kg and km field keeps today's comparison. `FIELD_HELP` and the page refusals say the unit in use with the inward range. The goal form posts `weight_unit` as a hidden field with the person's unit, and its labels and refusals follow | Decisions 6 and 16 |
| R16 | **The Settings card.** `SETTINGS_CARDS["units"] = "units"`, `SETTINGS_SAVED` "Units saved.". The card posts `weight_unit` and `distance_unit` through `update_settings`, by `write()` with the form's CSRF token and key. The switch's words: "It can change your time zone, units and targets, ..." | Decision 3 |
| R17 | **The charts** take `units`: points, ticks, the goal line, the projection and every label in the shown unit through `formatting.convert` and `show`. The tick lists are unchanged (`KG_STEPS` serves lb); `_goal_kg` becomes `_goal_weight(value, units)`; "Pace per km" and "6:00 per km" follow `unit_word('distance', units)`; a goal chart's desc still says `plan_line` word for word | Decision 8 |
| R18 | **`tests/text_rules.py`:** `_UNITS` gains `lb`, `mi` and `mph`, so "Weigh-in 185 lb" reads as a figure, not an id. No other rule changes | Without it, R14's own texts fail the summary surface |
| R19 | **The export.** `ACCOUNT_COLUMNS` gains `weight_unit` and `distance_unit` after `time_zone`. The README's new line after "Times are UTC ...", wrapped as its neighbours are | Decision 17 |
| R20 | **The evals.** `update_settings` joins `judge.SEED_SERVICES`, called as `service(user_id, data, "web")`, which is its shape (`app/services/account.py:237`), with no `as`. The judge's notes and `docs/evals.md` say so. The six cases go last in `evals/cases.json`, in their order. Not run by the builder: the eval run is the whole build's, once, at the end | Decision 20 |
| R21 | **Every new result field is optional:** `units`, `units_note`, each new `summary` and `words` default to null in their result models, so no result schema gains a required field. Added on the owner's two-deploy rule, 2026-11-16 | A client holding the old function list must still validate an answer from the new server where it can |

## "Bench 225 for 5" and "ran 3 miles", call by call

| Said | Calls | What comes back |
|---|---|---|
| "I weigh 185 lb" (never chose units, kg logged before) | `log_bodyweight` lb 185 | "Weigh-in 83.91 kg on Sun 15 Nov." and `units_note`. The assistant asks once whether to show lb everywhere |
| "Yes, pounds" | `update_settings` weight_unit lb | "Weights now show in lb. Today is Sun 15 Nov." |
| "Bench 225 for 5" (lb account) | `log_lifts`, Bench Press `weight_lb` 225 × 5 | "Bench Press 1 × 5 at 225 lb on Sun 15 Nov. New best: Bench Press estimated max 262.5 lb, up from ..." |
| "Ran 3 miles outside in 27 minutes" (mi account) | `log_cardio` Outdoor Run, `distance_mi` 3, minutes 27 | "Outdoor Run on Sun 15 Nov for 27:00, 3 mi, pace 9:00 per mi, about ... kcal estimated." |
| "Log my push day, but bench was 230 today" (lb account) | `get_routines`; `log_lifts` with the bench sets as `weight_lb` 230 and the rest as `weight_kg` as given | The session summary, every weight in lb |
| "Show my weights in pounds and my runs in miles" | `update_settings` weight_unit lb, distance_unit mi | "Weights now show in lb. Distances now show in miles. Today is Sun 15 Nov." |

## Every text a model or a person reads

Each text below passes `tests/text_rules.py` on its surface, once R18 is in. Where one fails as built, the builder changes the fewest words and names the change. Every figure here was worked on 2026-11-15 with half-up Decimal from the 2 dp store: 225 lb is kept as 102.06 kg and reads 225 lb; 185 lb is 83.91 kg; 135 lb is 61.23 kg; 230 lb is 104.33 kg; 3 mi is 4.83 km and reads 3 mi; 27 minutes over 4.83 km is 335.4 s a km (5:35) and 539.78 s a mile (9:00); Epley on 102.06 kg × 5 is 119.07 kg, 262.5 lb. A kg-said 20 kg reads 44.1 lb, 100 kg 220.45 lb. Every other figure in these tables is an example of the shape, not a worked result.

### Function texts that change

| Where | Old, in full | New, in full |
|---|---|---|
| `whoami` description, after its last sentence | " If it carries setup, walk setup.missing as setup.how says." | Kept, then: " It carries units: send each number in the field of the unit the person said, and say weights and distances as Spotter's summaries give them." |
| `whoami` result `units`, new | None | "The person's units: weight kg or lb, distance km or mi, set with update_settings, kg and km until then. Every summary, text, label, plan_line and note gives weights, distances, paces and speeds in them. Every number field stays in kg or km, as its name says: say the figures the texts give, never a number field you converted. A number the person says with no unit is in these units." |
| `SetIn.weight_kg` | "The weight lifted in kg, 0 to 500. Use 0 for bodyweight work." | "The weight lifted in kg, 0 to 500, when the person said kg, or gave no unit and units.weight is kg, or it came from get_routines. Use 0 for bodyweight work. Send weight_kg or weight_lb, never both." |
| `SetIn.weight_lb`, new | None | "The weight lifted in lb, 0 to 1,102.31, when the person said pounds, or gave no unit and units.weight is lb. Send it as said: Spotter converts it. Use 0 for bodyweight work. Send weight_kg or weight_lb, never both." |
| `log_bodyweight` `kg` | "Body weight in kg, 20 to 400." | "Body weight in kg, 20 to 400, when the person said kg, or gave no unit and units.weight is kg. Send kg or lb, never both." |
| `log_bodyweight` `lb`, new | None | "Body weight in lb, 44.1 to 881.84, when the person said pounds, or gave no unit and units.weight is lb. Send it as said: Spotter converts it. Send kg or lb, never both." |
| `log_cardio` `distance_km` | "The distance in km, if the person gave it." | "The distance in km, if the person gave it in km, or with no unit and units.distance is km. Send distance_km or distance_mi, never both." |
| `log_cardio` `distance_mi`, new | None | "The distance in miles, if the person gave it in miles, or with no unit and units.distance is mi. Send it as said: Spotter converts it. Send distance_km or distance_mi, never both." |
| `log_cardio` `speed_kmh` | "The speed in km/h, if the person gave it." | "The speed in km/h, if the person gave it so. Send speed_kmh or speed_mph, never both." |
| `log_cardio` `speed_mph`, new | None | "The speed in miles an hour, if the person gave it so. Send it as said: Spotter converts it. Send speed_kmh or speed_mph, never both." |
| `edit_entry` description | "... A weigh-in: its kg or date. ..." | "... A weigh-in: its weight or date. ..." The rest unchanged |
| `edit_entry` `kg` | "A weigh-in's new weight in kg, 20 to 400." | "A weigh-in's new weight in kg, 20 to 400. Send kg or lb, never both." |
| `edit_entry` `lb`, new | None | "A weigh-in's new weight in lb, 44.1 to 881.84, as said. Send kg or lb, never both." |
| `edit_entry` `distance_mi`, new | None | "A cardio entry's distance in miles, as said. null clears it. Send distance_km or distance_mi, never both." |
| `edit_entry` `speed_mph`, new | None | "A cardio entry's speed in miles an hour, as said. null works it out from distance and minutes. Send speed_kmh or speed_mph, never both." |
| `set_goal` `distance_km` | "A cardio_best goal: the distance in km, 0.1 to 300. A session at or over it counts." | "A cardio_best goal: the distance in km, 0.1 to 300, when the person said km. A session at or over it counts. Send distance_km or distance_mi, never both." |
| `set_goal` `distance_mi`, new | None | "A cardio_best goal: the distance in miles, 0.07 to 186.41, when the person said miles. Spotter converts it. Send distance_km or distance_mi, never both." |
| `set_goal` `target_value` | "The target, in the kind's unit. A lift: kg, an estimated one-rep max, above 0 to 666.67. Body weight: kg, 20 to 400. A measurement: its own unit, cm for a body size, bpm for resting_hr. A cardio_best: minutes for the distance, 1 to 600." | "The target, in the kind's unit. A lift: an estimated one-rep max in weight_unit, above 0 to 666.67 kg or 1,469.74 lb. Body weight: in weight_unit, 20 to 400 kg or 44.1 to 881.84 lb. A measurement: its own unit, cm for a body size, bpm for resting_hr. A cardio_best: minutes for the distance, 1 to 600." |
| `set_goal` `start_value` | "Where the person is now, in the kind's unit. Used only when nothing is logged yet to start from." | "Where the person is now, in the kind's unit, weight_unit for a lift or body weight. Used only when nothing is logged yet to start from." |
| `set_goal` `weight_unit`, new | None | "kg or lb: the unit of target_value and start_value for a lift or body weight goal, as the person said it. Leave it out for the person's units.weight. Never convert a number yourself." |
| A goal's `label` (`GoalNumbers`, in `set_goal`, `get_goals` and `get_progress`) | "The goal in a few words, such as 'Back Squat 100 kg', 'Body weight 75 kg', 'Waist 80 cm' or 'Outdoor Run 5 km in 28:00'." | "The goal in a few words, in the person's units, such as 'Back Squat 100 kg', 'Body weight 75 kg', 'Waist 80 cm' or 'Outdoor Run 5 km in 28:00'." |
| `update_settings` description, its first sentence | "Sets the person's time zone and targets." | "Sets the person's time zone, units and targets." The rest unchanged |
| `update_settings` `weight_unit`, new | None | "kg or lb: how every page and every summary shows weights. Set it when the person asks, or when they answer a units_note. No stored number changes." |
| `update_settings` `distance_unit`, new | None | "km or mi: how every page and every summary shows distances, paces and speeds. Set it when the person asks, or when they answer a units_note. No stored number changes." |
| `save_routine` `lifts[].weight_kg` | "The weight in kg, 0 to 500, only when the person gave one. Leave it out otherwise: get_routines then gives the weight of the lift's latest session." | The same, then " Send weight_kg or weight_lb, never both." |
| `save_routine` `lifts[].weight_lb`, new | None | "The weight in lb, 0 to 1,102.31, only when the person gave one in pounds, or with no unit and units.weight is lb. Leave it out otherwise." |
| `get_routines` description | "... Where weight_kg is null, ask the person for it. Say in your reply which sets were logged." | "... Send each weight_kg as given, in weight_kg, whatever the person's units. Where weight_kg is null, ask the person, and send the answer in the field of its unit. Say in your reply which sets were logged." The rest unchanged |
| `get_routines` `lifts[].weight_kg` | "The weight to log, in kg: ... Null when there is neither: ask the person." | "The weight to log, in kg: ... Send it in weight_kg as it is, never converted. Null when there is neither: ask the person." |
| `get_routines` `routines[].words`, new | None | "The routine's lifts in words, each with the weight to log, in the person's units, ready to quote." |
| Result `units_note`, new, on `log_lifts`, `log_bodyweight`, `log_cardio`, `edit_entry`, `set_goal`, `save_routine` | None | Description: "Present only when this call sent a weight in lb or a distance or speed in miles, and the person has not chosen their units. Ask once, as it says." Text, weight: "This weight was given in lb, and Spotter shows this person's weights in kg. Ask once whether to show lb everywhere, and save the answer with update_settings: weight_unit lb, or kg to keep kg. Either answer ends this note." Distance: "This distance was given in miles, and Spotter shows this person's distances in km. Ask once whether to show miles everywhere, and save the answer with update_settings: distance_unit mi, or km to keep km. Either answer ends this note." |
| `log_bodyweight` result `summary`, new | None | "The weigh-in in one line, ready to quote, in the person's units." Text: R14 |
| `get_log` day `summary`, new | None | "The day's lift sessions, weigh-in and cardio, ready to quote, in the person's units. Empty when the day has none. Food and measurements are in their own fields." Text: R14 |
| `get_progress` result `summary`, new | None | "Progress in a few lines, ready to quote, in the person's units." Text: R14 |
| `delete_entry` result `summary`, new | None | "The entry in one line, in the person's units, to show the person before they say yes. Null for a measurement, a food entry or a saved food." Text: R14 |

### Summaries and pages on each account

The kg column is today's words where the text exists, and the new texts' words in kg where it does not.

| Text | kg and km account | lb and mi account |
|---|---|---|
| `log_lifts` summary | "Bench Press 1 × 5 at 100 kg on Sun 15 Nov. New best: Bench Press estimated max 116.7 kg, up from 113.8 kg." | "Bench Press 1 × 5 at 225 lb on Sun 15 Nov. New best: Bench Press estimated max 262.5 lb, up from 250.8 lb." |
| A session add | "Added Bench Press 2 × 5 at 100 kg to the session on Sun 15 Nov. It now has 4 sets." | "Added Bench Press 2 × 5 at 225 lb to the session on Sun 15 Nov. It now has 4 sets." |
| The mark line | "First Bench Press session: estimated max 116.7 kg is the mark to beat." | "First Bench Press session: estimated max 262.5 lb is the mark to beat." |
| A heaviest-weight best | "Pull-up heaviest weight 20 kg, up from 15 kg" | "Pull-up heaviest weight 45 lb, up from 35 lb" |
| `log_cardio` summary | "Outdoor Run on Sun 15 Nov for 27:00, 4.83 km, pace 5:35 per km, about 300 kcal estimated." | "Outdoor Run on Sun 15 Nov for 27:00, 3 mi, pace 9:00 per mi, about 300 kcal estimated." Back-dated: "from body weight now, 185.2 lb" |
| `get_trends` summary | "Body weight 84 kg now, smoothed 84.2 kg, -0.25 kg a week ..." | "Body weight 185.2 lb now, smoothed 185.6 lb, -0.55 lb a week ..." |
| A goal's `label` | "Back Squat 140 kg", "Body weight 78 kg", "Outdoor Run 5 km in 28:00" | "Back Squat 315 lb", "Body weight 175 lb", "Outdoor Run 3.1 mi in 28:00" |
| A goal's `plan_line` | "84.3 kg now against 83.9 kg on the plan line today: 0.4 kg behind." | "185.8 lb now against 185 lb on the plan line today: 0.8 lb behind." |
| An edit summary, a weigh-in | "Weigh-in changed: kg from 84 to 83.5." | "Weigh-in changed: weight from 185 lb to 184 lb." |
| An edit summary, cardio | "distance_km from 5 to 5.5" | "distance from 3 mi to 3.4 mi" |
| A refusal, a set | "Set 1: weight 600 kg is outside 0 to 500 kg." | "Set 1: weight_lb 1200 lb is outside 0 to 1,102.31 lb." Fix: "Give weight_lb on set 1 from 0 to 1,102.31 lb." |
| A refusal, a weigh-in | "Give the body weight in kg, 20 to 400." | "Give lb from 44.1 to 881.84 lb." |
| `save_routine` summary | "Saved Push day: Bench Press 3 × 8 at 60 kg, Overhead Press 3 × 10 and Dip 3 × 10." | "Saved Push day: Bench Press 3 × 8 at 135 lb, Overhead Press 3 × 10 and Dip 3 × 10." |
| `get_routines` `words` | "Bench Press 3 × 8 at 60 kg and Overhead Press 3 × 10 at 40 kg" | "Bench Press 3 × 8 at 225 lb and Overhead Press 3 × 10 at 135 lb" |
| `update_settings` summary, new lines in `SAID` | None | "Weights now show in lb", "Weights now show in kg", "Distances now show in miles", "Distances now show in km" |
| Pages: headers and labels | "Weight (kg)", "Distance (km)", "Pace (per km)", "Speed (km/h)", "Fastest pace (per km)", "Estimated max (kg)", "Target weight (kg)", "Target estimated max (kg)", "Where you are now (kg)", "kg trend", "kg a week", "kg, estimated max now", "kg × 5, best set" | "Weight (lb)", "Distance (mi)", "Pace (per mi)", "Speed (mph)", "Fastest pace (per mi)", "Estimated max (lb)", "Target weight (lb)", "Target estimated max (lb)", "Where you are now (lb)", "lb trend", "lb a week", "lb, estimated max now", "lb × 5, best set" |
| Pages: forms | The weigh-in form posts `kg`, cardio `distance_km`, edit `kg`, `distance_km`, `speed_kmh` | They post `lb`, `distance_mi`, `speed_mph`, filled at the shown step |
| Page refusal, a weigh-in | "That weight was not saved: give it in kg, from 20 to 400." | "That weight was not saved: give it in lb, from 44.1 to 881.84." |
| The Routines page | "Bench Press 3 × 8 at 62.5 kg ..." | "Bench Press 3 × 8 at 137.8 lb ...", from `get_routines` as now |
| Charts | Ticks in kg, "Goal 100 kg", "Pace per km: Outdoor Run", "6:00 per km" | Ticks in lb from the same step list, "Goal 225 lb", "Pace per mi: Outdoor Run", "9:00 per mi" |
| Settings, a new card | None | Heading "Units". "Weights" with kg and lb, "Distances" with km and miles, button "Save units", saved line "Units saved." Under it: "Your pages and your assistant show weights and distances in these units. Switching changes no entry." |
| Settings, "Read, log and delete" | "... It can change your time zone and targets, and delete entries, saved foods and routines. ..." | "... It can change your time zone, units and targets, and delete entries, saved foods and routines. ..." on every account |
| `hi.html:77` | "It logs weights in kg and distances in km. An import from another app converts pounds and miles." | "It takes no stones, and no inches for a body size. Weights are kg or lb and distances km or miles: say which you use." |
| Export README, a new line | None | "Weights are in kg and distances in km in every file, whatever units the account shows. account.csv names the units chosen: weight_unit and distance_unit, blank when none was chosen." |
| `docs/can-and-cant.md:8` | "... the sets (exercise, weight in kg, reps) ..." | "... the sets (exercise, weight, reps) ..." |
| `docs/can-and-cant.md:11` | "**Body weight:** one weigh-in a day, in kg." | "**Body weight:** one weigh-in a day." |
| `docs/can-and-cant.md:166` | "... a weigh-in's kg or date ..." | "... a weigh-in's weight or date ..." |
| `docs/can-and-cant.md`, a new section "Units", after "What its numbers are" | None | "- **Kept in kg and km.** Every weight is kept in kg and every distance in km, at 2 decimal places. A result's number fields are in kg and km, as their names say." "- **The person's units** are kg or lb and km or mi, set with `update_settings` (`weight_unit`, `distance_unit`) or on Settings: kg and km until then. Switching changes no entry." "- **Shown in them:** every page, and every summary, text, label, plan_line and note. Claude says those figures and never converts a number field." "- **Sent as said.** A weight in pounds goes in `weight_lb` (a set) or `lb` (a weigh-in), a distance in miles in `distance_mi`, a speed in `speed_mph`, a goal's target with `weight_unit`. Spotter converts: lb x 0.45359237, mi x 1.609344, kept at 2 decimal places. A number said with no unit is in the person's units. A routine's weights from `get_routines` go in `weight_kg` as given." "- **Shown steps:** a set, a weigh-in and a goal's target to 0.05 lb; body weight now, the trend and an estimated max to 0.1 lb; a rate to 0.01 lb a week; miles to 0.01, mph to 0.1, a pace per mile as m:ss." "- **Asked once.** A weight in lb or a distance in miles, before the person has chosen, comes back with `units_note`: Claude asks once, and saves either answer." "- **Not offered:** stones, inches for a body size, a best mile (best times stay at 1, 5 and 10 km)." |
| `docs/can-and-cant.md`, the function table | `update_settings`: "Sets the person's time zone and targets." | "Sets the person's time zone, units and targets." |

The hi page's sample sentences ("Back squat, 3 sets of 5 at 60 kg") and the README's examples stay in kg: they are fixed examples, not a person's figures.

## Errors

| Code | When | Message | Fix |
|---|---|---|---|
| `invalid_field` | Both of a pair: `weight_kg` and `weight_lb` on one set or lift, `kg` and `lb`, `distance_km` and `distance_mi`, `speed_kmh` and `speed_mph` | "Set 2 has both weight_kg and weight_lb: send one." (and its like) | "Send the weight in the unit the person said: weight_kg or weight_lb." |
| `missing_field` | Neither of a pair where one is needed: a set's weight, a weigh-in's | "Set 2: weight_kg or weight_lb is missing." / "kg or lb is missing." | "Give weight_kg or weight_lb on set 2." / "Give the body weight in kg, 20 to 400, or in lb, 44.1 to 881.84." |
| `invalid_unit` | `update_settings` or `set_goal` given another unit | The import's: "weight_unit 'pounds' is not a unit Spotter takes." | "Use kg or lb for weight_unit, km or mi for distance_unit." |
| The range refusals | A number in lb or mi out of range | In the unit sent, the range moved inward, by `rules.call_unit` | As the import's |

## The migration

`migrations/versions/0011_units.py`, `revision "0011"`, `down_revision "0010"`, notes in the shape of `0010`'s. Up adds `user.weight_unit` and `user.distance_unit`, each `String(2)`, nullable, no server default. Down drops both. No data changes. Tested up and down on SQLite by the builder, and run on Postgres after, as `0010` was.

## The eval cases

| Case | Seed | Says | Passes when |
|---|---|---|---|
| `lb_weigh_in` | `update_settings` weight_unit lb | "Weighed 185 this morning" | One successful `log_bodyweight` with `lb` 185 and no `kg`. The reply has "185" and no "kg" as a word. One live weigh-in today, kg 83.91 |
| `lb_lifts` | `update_settings` weight_unit lb | "Bench 225 for 5, then 3 sets of 8 at 185" | One successful `log_lifts` with Bench Press `weight_lb` 225 × 5 and `weight_lb` 185 × 8, no `weight_kg`. The reply has no "kg" as a word. One live session today, `set_count` 4 |
| `miles_run` | `update_settings` distance_unit mi | "Ran 3 miles outside in 27 minutes" | One successful `log_cardio`, Outdoor Run, `distance_mi` 3, minutes 27, no `distance_km`. The reply has "9:00" and no "km" as a word. One live cardio entry, distance_km 4.83 |
| `units_asked` | `log_bodyweight` kg 84 yesterday, so the account is not new and no `new_account_note` asks first | "I weigh 185 lb" | One successful `log_bodyweight` with `lb` 185. No `update_settings`. The reply asks. Two live weigh-ins, today's kg 83.91 |
| `units_switch` | None | "Show my weights in pounds and my runs in miles from now on" | One successful `update_settings` with weight_unit lb and distance_unit mi. No log call |
| `lb_routine_changed` | `update_settings` weight_unit lb; `save_routine` Push day, Bench Press 3 × 8 at weight_kg 102.06, Overhead Press 3 × 10 at weight_kg 61.23 | "Log my push day, but bench was 230 today" | One successful `log_lifts`: Bench Press `weight_lb` 230 × 8, and Overhead Press either `weight_kg` 61.23 or `weight_lb` 135 × 10. No set sent `weight_lb` 102.06 or `weight_kg` 230. One live session today, `set_count` 6 |

```json
{"id": "lb_weigh_in",
 "say": "Weighed 185 this morning",
 "seed": [{"service": "update_settings", "data": {"weight_unit": "lb"}}],
 "expect": {"function": "log_bodyweight", "ok_calls": {"log_bodyweight": 1},
            "inputs_include": {"lb": 185}, "inputs_exclude": ["kg"],
            "reply_has": ["185"], "reply_lacks_pattern": ["\bkg\b"]},
 "db": [{"table": "bodyweight", "count": 1, "all": {"kg": 83.91, "date": "{{today}}"}}]}

{"id": "lb_lifts",
 "say": "Bench 225 for 5, then 3 sets of 8 at 185",
 "seed": [{"service": "update_settings", "data": {"weight_unit": "lb"}}],
 "expect": {"function": "log_lifts", "ok_calls": {"log_lifts": 1},
            "inputs_include": {"sets": [{"exercise": "Bench Press", "weight_lb": 225, "reps": 5},
                                        {"exercise": "Bench Press", "weight_lb": 185, "reps": 8}]},
            "inputs_exclude": ["sets.*.weight_kg"], "reply_lacks_pattern": ["\bkg\b"]},
 "db": [{"table": "lift_session", "count": 1, "all": {"set_count": 4, "date": "{{today}}"}}]}

{"id": "miles_run",
 "say": "Ran 3 miles outside in 27 minutes",
 "seed": [{"service": "update_settings", "data": {"distance_unit": "mi"}}],
 "expect": {"function": "log_cardio", "ok_calls": {"log_cardio": 1},
            "inputs_include": {"exercise": "Outdoor Run", "distance_mi": 3, "minutes": 27},
            "inputs_exclude": ["distance_km"], "reply_has": ["9:00"], "reply_lacks_pattern": ["\bkm\b"]},
 "db": [{"table": "cardio_entry", "count": 1, "all": {"distance_km": 4.83}}]}

{"id": "units_asked",
 "say": "I weigh 185 lb",
 "seed": [{"as": "earlier", "service": "log_bodyweight", "data": {"kg": 84, "date": "{{yesterday}}"}}],
 "expect": {"function": "log_bodyweight", "ok_calls": {"log_bodyweight": 1},
            "inputs_include": {"lb": 185}, "no_call": ["update_settings"], "reply_asks": true},
 "db": [{"table": "bodyweight", "count": 2,
         "rows": [{"id": "{{seed.earlier}}", "kg": 84}, {"kg": 83.91, "date": "{{today}}"}]}]}

{"id": "units_switch",
 "say": "Show my weights in pounds and my runs in miles from now on",
 "expect": {"function": "update_settings", "ok_calls": {"update_settings": 1},
            "inputs_include": {"weight_unit": "lb", "distance_unit": "mi"},
            "no_call": ["log_bodyweight", "log_lifts", "log_cardio"]}}

{"id": "lb_routine_changed",
 "say": "Log my push day, but bench was 230 today",
 "seed": [{"service": "update_settings", "data": {"weight_unit": "lb"}},
          {"as": "push", "service": "save_routine",
           "data": {"name": "Push day", "lifts": [{"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 102.06},
                                                 {"exercise": "Overhead Press", "sets": 3, "reps": 10, "weight_kg": 61.23}]}}],
 "expect": {"function": "log_lifts", "ok_calls": {"log_lifts": 1},
            "inputs_include": {"sets": [{"exercise": "Bench Press", "weight_lb": 230, "reps": 8},
                                        {"$in": [{"exercise": "Overhead Press", "weight_kg": 61.23, "reps": 10},
                                                 {"exercise": "Overhead Press", "weight_lb": 135, "reps": 10}]}]},
            "inputs_forbid": {"sets.*.weight_lb": 102.06, "sets.*.weight_kg": 230}},
 "db": [{"table": "lift_session", "count": 1, "all": {"set_count": 6, "date": "{{today}}"}}]}
```

| Case | Proves | Does not prove |
|---|---|---|
| `lb_weigh_in` | A bare number on a lb account goes in as lb, is kept as kg, and comes back in lb | That every later read stays in lb: one message a run |
| `lb_lifts` | Each set takes the pound field, and the reply never falls back to kg | That a set said in kg on a lb account goes in `weight_kg`: one phrasing |
| `miles_run` | A distance in miles goes in as said, and the pace comes back per mile | Speed in mph: unit tests hold it |
| `units_asked` | The first lb log on an account that never chose asks, and does not switch on its own | That the yes is saved: no second turn. `units_switch` covers the save |
| `units_switch` | The assistant finds the setting from plain words | That pages follow: unit tests hold it |
| `lb_routine_changed` | A routine's kg weight goes in `weight_kg` as given, or in lb as its words say it, and the changed lift in lb | The newest-wins rule on a lb account: unit tests hold it |

`"lb": 185` matches a sent 185.0; 83.91 is 185 x 0.45359237 at 2 dp; 4.83 is 3 x 1.609344 at 2 dp; 102.06 and 61.23 are 225 and 135 lb at 2 dp of kg, and read 225 lb and 135 lb back. The `$in` leaf at a set is the judge's own (`evals/judge.py:232-233`). Each case goes into `evals/cases.json`, the case table of `docs/evals.md`, and the case count in the one place tidy-ups keeps it.

**The run.** Not per item: the whole set, 41 cases, 3 runs each, on `claude-opus-5-5`, once, at the end of the whole build. The cost is told to the owner before it runs.

## Work packages: two builders, U1 then U2, landing in one push

Two packages, built one after the other in the same tree and pushed together. U1 is the store, the services and every text a model reads; U2 is every page and chart. The seam is only `app/formatting.py` (R2, R3, R4) and `users.units_for` (R5), both U1's. After U1 alone a lb account hears lb in chat and sees kg on its pages, so U1 is never pushed without U2.

Why two: the build is about 52 files and 2,100 lines, near routines' size (its commit, `52b0e76`: 49 files, +2,647), and the pages and charts are a surface of their own with their own reviews. One builder would hold every service and every template at once.

### Files both this work and tidy-ups touch

Tidy-ups builds first and moves the hard-coded counts (eval cases, function counts) into one place. Units changes no function count. Where a file below is touched by both, units builds on tidy-ups' version and changes only what this table says.

| File | Tidy-ups | Units (package) |
|---|---|---|
| `evals/cases.json` | Its count is read from one place | U1: six cases appended |
| `docs/evals.md` | Its counts | U1: six rows in the case table, `update_settings` among the seed services, and the count where tidy-ups left it |
| `README.md` | Its counts and other edits | U1: the case count only, where tidy-ups left it; nothing if tidy-ups draws it |
| `tests/test_evals.py` | The pinned case count | U1: the count, only if tidy-ups left one to move |
| `tests/test_feedback.py` | The pinned counts | U1: the `PUBLISHED` hashes of the thirteen changed functions, and the case count if tidy-ups left one |
| The one place tidy-ups keeps the counts | Made there | U1: the case count, 35 to 41 |
| `app/mcp_server.py`, `app/mcp_schemas.py`, `docs/can-and-cant.md`, `app/web.py` | Possibly the function counts in their notes and text | U1 and U2 as below. No count of functions moves here |

### U1: the store, the services, the texts a model reads

| Files | Exit check |
|---|---|
| `migrations/versions/0011_units.py` (new), `app/models.py` (two columns, the module notes), `app/users.py` (`units_for`, `units_set`), `app/formatting.py` (R2 to R4), `app/rules.py` (the moved table and helpers, `one_of`, `check_session`), `app/services/imports.py` (reads the moved helpers; no change of behaviour), `app/mcp_schemas.py`, `app/mcp_server.py`, `app/calllog.py`, `app/services/account.py` (`SETTABLE`, `SAID`, `whoami`'s `units`), `common.py` (`units_note`, `pair`'s `units`), `lifts.py`, `body.py`, `cardio.py`, `goals.py`, `trends.py`, `progress.py`, `log.py`, `entries.py`, `routines.py`, `export.py`, `docs/can-and-cant.md`, `docs/evals.md`, `evals/cases.json`, `evals/judge.py` (`SEED_SERVICES` and the notes), `README.md` (the count, as above), `tests/text_rules.py` (`_UNITS`), `tests/test_units.py` (new). Existing test files only where an assertion now fails because of a decision, each named in the hand-over; expected: `tests/test_feedback.py` (hashes, count), `tests/test_evals.py` (count), `tests/test_routines.py` and `tests/test_gym_logging.py` (published words), `tests/test_mcp.py` (`kg` and `weight_kg` no longer required), `tests/test_update_settings.py` (the refusal listing `SETTABLE`), `tests/test_bodyweight.py` (`kg` missing), `tests/test_text_rules.py` (`_UNITS`) | Every decision and ruling a service or a model text carries has a test. On a lb account: 225 lb is kept as 102.06 kg and reads "225 lb" in `log_lifts`' summary, a session add, `get_log`'s day summary and `new_bests[].text`, and `get_progress`' summary; 46.25 and 47.5 read as said; a kg-said 20 kg reads 44.1 lb. A best that reads the same as the one it beat in lb widens to 0.01 lb. A set or a weigh-in with both fields is refused and writes nothing; one with neither is `missing_field`. A lb refusal quotes lb and the inward range. On a mi account: 3 mi is 4.83 km and reads 3 mi, pace 9:00 per mi, speed in mph; a speed refusal on a miles call says mph. Switching kg to lb to kg rewrites no row: every stored number and every `entry_version.before` the same, and an undo after a lb edit puts back the exact kg. A kg and km account's every existing text test passes unchanged but the four of decision 7. `units_note` appears on a lb or mi write while its column is null, a duplicate included, never on a refusal, never once either answer is saved, never on a kg write or an import. `whoami` gives `units`, kg and km for null. `update_settings` sets both, forgives case, refuses "pounds", and says each change. `set_goal` with `weight_unit` lb and without it on a lb account both store kg, `goal.unit` stays "kg", and the label reads lb; a measurement goal's cm is never converted. A routine saved with `weight_lb` is kept in kg; saving it again in kg changes nothing; `get_routines`' `words` read lb and its `weight_kg` stays kg. `delete_entry`'s summary on a preview and on a delete. The export's numbers are the same on both accounts; `account.csv` names the units. The import gives the same answers as before, word for word. The trace writes `weight_lb`, `lb`, `distance_mi`, `speed_mph`. The migration runs up and down on SQLite. The instructions are still 3,927 characters. Of the published list, exactly the thirteen functions named below differ. Every new text passes `check` on its surface. The eval cases load and judge both ways. The tests for the files touched pass, plain and with `-W error` |

### U2: the pages and the charts

| Files | Exit check |
|---|---|
| `app/web.py` (R15, R16, the weigh-in, cardio, edit and goal forms, `FIELD_HELP`, the page refusals, the switch's words, `routines_page` passing `units`), `app/charts.py` (R17), `app/templates/_parts.html`, `day.html`, `edit.html`, `goals.html`, `progress.html`, `progress_lift.html`, `settings.html`, `hi.html`, `tests/test_units_pages.py` (new). Existing test files only where a decision makes an assertion fail, each named in the hand-over; expected: `tests/test_hi_there.py` (the hi line at `:178`, the schema check near `:674`), `tests/test_web.py` and `tests/test_web_account.py` (the switch's words), `tests/test_charts.py` | On a lb and mi account: the day page, Home's tiles and sheets, Progress, a lift's page, Goals, Routines and every chart show lb, mi, mph and per mi, with no "kg" or "km" in their visible text but the best-time names (1, 5 and 10 km). A kg and km account's pages are byte for byte as before, but three texts that change on every account: the hi page's line, the switch's words on Settings, and the new Units card. The weigh-in form on a lb account saves 185 as 83.91 kg; the cardio form saves 3 mi as 4.83 km. The edit page on a lb account sent back untouched changes nothing and keeps no version; a changed weight is saved. The goal form on a lb account sets a lb target. The Units card saves both settings, says "Units saved.", and its next render shows lb. Every page keeps the page and text rules, and the 25-word sentences. The tests for the files touched pass, plain and with `-W error` |

**Size, an estimate, not counted:** about 52 files and +2,100 lines, about half of it tests. U1: about 37 files, +1,400 (+700 code, +700 tests). U2: about 15 files, +700 (+330 code, +370 tests). For scale, routines' commit `52b0e76` was 49 files and +2,647; gym logging's `3fe468b` was 32 files and +1,150.

**The published list.** Thirteen of the twenty-two functions' published entries change, so `PUBLISHED` moves for each: `whoami`, `log_lifts`, `log_bodyweight`, `log_cardio`, `edit_entry`, `set_goal`, `update_settings`, `get_progress`, `get_goals`, `get_log`, `delete_entry`, `save_routine`, `get_routines`. Not by one byte: `list_exercises`, `log_measurement`, `restore_entry`, `find_food`, `log_food`, `save_food`, `get_trends`, `import_entries`, `send_feedback`, and the owner's three.

## Known limits

| Limit | Why it stands |
|---|---|
| A set said in kg on a lb account shows in lb: 20 kg reads 44.1 lb, 100 kg 220.45 lb | No row keeps the unit it was said in (decision 1) |
| An assistant that puts a pound number in `weight_kg` logs kg. The summary says it back in the account's lb ("496 lb"), so it shows at once, and `edit_entry` fixes it | The field's name is the guard. A kg number that is also a sane lb number cannot be told apart |
| Number fields stay kg and km. An assistant that reads them over the texts says kg | Renaming them breaks every client. `whoami.units` says to use the texts |
| A pace per mile comes from the stored km at 2 dp: it can read a second away from the pace worked from the miles said | One stored precision, the import's |
| Best times stay at 1, 5 and 10 km, named in km on every account. No best mile | Race distances are named in km. A best mile is a new best, not a unit |
| The import's preview gives its ranges in kg and km after conversion, its speed refusals say "mi an hour", and an import in lb carries no `units_note` | The import is not changed (decision 15) |
| `restore_entry` without `undo_edit` gives the entry as numbers | Rare. Its summary is only for an undo today |
| The cardio rule in the instructions still says 7 km/h | The instructions do not change (decision 14) |
| The maintenance note says 7,700 kcal a kg | It names the formula, not a figure of the person's |
| The hi page's and the README's sample sentences say kg | Fixed examples, the same for every reader |
| A person who never answers `units_note` is asked again in another conversation | Nothing is stored for a question not answered. Any answer ends it |
| A conversation begun before the deploy holds the old tool list: no `weight_lb`, and it may refuse an answer of the thirteen changed functions whose result model forbids fields it does not know, as `whoami` was refused after first day. A new conversation clears it; the owner is told at the merge | As every function change |
| A weight finer than 0.05 lb shows at 0.05 lb | The finest step the 2 dp kg store gives back exactly |

## Not in this work

Inches for a body size, stones, a best-mile time, the unit an entry was said in, an export in lb or miles, the import's preview in lb, a units step in setup, kJ, a change to the instructions, a new function.

## Note, 2026-11-16: from the U1 review

- The lift bound in lb is 1,469.75, not 1,469.74: 666.67 / 0.45359237 = 1,469.7558, rounded down.
- A bare goal value from the web is read as kg until the goal form sends `weight_unit` (U2).
- A lb refusal reads "lb 900 is outside 44.1 to 881.84 lb."
