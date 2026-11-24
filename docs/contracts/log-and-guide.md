# Log and guide contract: one `log` for every entry, a `guide` for how, and the five logs kept for open chats

**Goal: a new chat logs every entry through one function, `log`, and learns each kind's fields from `guide`. The five log functions leave every new chat's list but keep answering a chat opened before, by name, until the call log shows nobody calls them. Reads do not change. Every number, check and answer stays the five's own.**

Fixed 2026-11-20 after the owner's answers, and fixed in place on 2026-11-21 after a reading of the app, the evals and the clashes with C3 and C5; not built. Read against `main` at `3b2441e` on 2026-11-21: every line number below is that commit's. The tested shape is the comparison rig's arm D at commit `5783ad7` (`evals/arms.py`, `evals/compare.py`, and `docs/evals.md`'s "The function list comparison" on that branch), whose app code is `982e8da`'s. The builder reads each "What exists" row again on the commit it starts from. Nothing here is approved to build until the owner says so.

## His words

On the five (2026-11-20): "Yes, for q1": the five log functions become one log and a guide, as tested.

On hiding them: "If you can make it selectively visible, then yes."

On the open points (2026-11-20): "Q2 C, Q3 A, Q4 A, Q5 A".

On the reading of 2026-11-21: "Yes to Q1 to Q6", then "your picks for questions work", and on the split of L1 and a fresh branch from `main`: "go with your picks". On the build, the review, the commits and the release eval: "Yes, for all the questions."

## What the rig measured

Write cases, right on the record, 12 runs of 24 cases, 288 runs a side (2026-11-20):

| Model | Today's list | Arm D (`log` and `guide`) | Calls a run, D against today |
|---|---|---|---|
| Sonnet | 288 | 288 | about 1 more |
| `gpt-6-luna` | 272 | 281 | about 1 more |
| Haiku | 288 | 288 | under 1 more |

Every rig run loaded every function up front (`ENABLE_TOOL_SEARCH=false` for Claude). No rig number was measured with tool search on. The rig's lift part had no `time_s`, `distance_m` or `exact`: they came with the exercise list's second deploy, after the runs.

After that deploy, arm D's lift part took `LogSetIn`'s fields and the 9 lift cases ran again on Sonnet, 12 runs a side (2026-11-20): lifts 108 of 108 on both arms; every write in those cases today's list 101, arm D 108 of 108. Today's list's 7 misses were one walk home logged as Treadmill Walk, a gap of today's list since that deploy. On `gpt-6-luna`, the same 9 cases at 12 runs a side: today's list 104, arm D 107 of 108, every miss `routine_logged`, where the model asks for the exercises instead of reading the saved routine. A Haiku pilot at 2 runs: arm D 18 of 18, today's list 16 of 18. None of the 9 cases sends a hold, a carry or Spotter's which-lift question.

## Decisions

The owner's, 2026-11-19 and 2026-11-20, in plain words. Not open to change here.

| # | Decision |
|---|---|
| 3 | Quick functions stay only where a comparison shows them quicker or better. One general log takes a kind word |
| 4 | The assistant learns how to use a kind from a guide it calls, by topic |
| 9 | Words that grow (kinds, exercise names) stay open text that Spotter checks and the guide lists. Lists that never grow (meals, kg and lb) may stay fixed |
| 14 | The call log keeps words: which functions and kinds are used, what was refused and what fixed it. Never numbers or food |
| 20 | An open chat learns what is new only from Spotter's answers and the guide. A new chat learns at its first call |
| 36 | The general log has named fields for the numbers its parts share, and a small open part for each kind's own words. A kind never has two ways in |
| 39 | The trial log covers all five of today's kinds: lifts and cardio by named numbers in parts; bodyweight, food and measurements by the open part |
| 40 | Goals keep their own function; their kind words become the log's words later, today's goal words working until nobody sends them. Out of this release: `set_goal` does not change here |
| 37 | One kind word everywhere: the word a thing is logged with is the word it is changed, deleted or restored with. Change and delete keep their shape for now |
| 38, as amended by 50 | A retired name goes once the call log shows nobody calls it |
| 44 | `guide` takes `kind` as the same field as `topic`. The words Spotter's own log answers give (`lift_session`, `food_entry`, `cardio_entry`) read as their kinds |
| 46 | No warm runs: the guide is paid once a message, as built |
| 48 | The live sleep gap is fixed on today's list first, in its own item: live since `fd13e1f` (2026-11-20), a routing sentence in the instructions with a short Goals sentence. A sentence in `log_measurement`'s description had not moved it |
| 49 | `log_lifts`, `log_bodyweight`, `log_measurement`, `log_food` and `log_cardio` become `log` and `guide`, as tested. Reads stay as they are. Change, delete, saves and finds are out of the first release |
| 50 | The five leave every new chat's list and keep answering calls by name from chats opened before, until the call log shows nobody calls them. Then they go. No pointer: a pointer to `log` is a dead end in a list with no `log` |
| 51 | Every part takes a time as `time_s` or `minutes` and a distance as `distance_m`, `distance_km` or `distance_mi`, as the person said it, and Spotter converts to the kind's own columns. No field is renamed, and a new kind needs no new field |
| 52 | `edit_entry`, `delete_entry` and `restore_entry` also take `log`'s five kind words from the first release. Nothing else about them changes |
| 53 | `guide` and `what_is_logged` stay apart: `guide` says how to log, `what_is_logged` keeps its menu and the person's facts |
| 54 | The five go in a second deploy, once the call log shows no assistant calling them over a window the owner sets from the numbers after the first. `owner_calls` gains a filter by function to read it |

## Decided 2026-11-21

The owner's, on the reading of 2026-11-21, labelled N to keep them apart from the numbered register above. Not open to change here.

| # | Decision |
|---|---|
| N1 | Deploy 1 is the target before December. Deploy 2 goes on his window, read from the call log |
| N2 | Decisions 49 to 54 stand as written |
| N3 | This contract is fixed in place: one document, no dated notes, until it is built |
| N4 | It is built in its own git worktree |
| N5 | The C3 and C5 builds are told of this contract by a note the owner passes on |
| N6 | `log` publishes no output schema, as tested. The lead's pick, told to the owner, standing unless he says otherwise |
| N7 | The homepage count is what a new chat sees: 21 |
| N8 | The plain words "log" and "guide" on a page are not code words. The lead's pick, told to the owner, standing unless he says otherwise |
| N9 | Two new eval cases, `guide_how_food` and `log_plank_minutes`, both core: 260 cases, 103 core. `log_word_both`, `log_plank`, `log_farmers_carry` and `log_sleep_word` are dropped |
| N10 | No tool search pilot. The release run goes logging cases first, and they are read before the rest. A dip stops the merge. The named fallback is `alwaysLoad` on `log` and `guide` |
| N11 | The release eval runs at the gate tier, `claude-sonnet-5-5`, with the logging cases also on `gpt-6-luna` |
| N12 | C3's new food fields reach new chats through `guide` only |
| N13 | C5's `log_with` gives the kind word (`lifts`, `cardio`), never a function name |
| N14 | This item merges first. C3, then C5, rebase onto it |
| N15 | If a sleep sentence is needed after the release eval, the cut that makes room comes to the owner with numbers |

## What exists today

| Piece | State | Where |
|---|---|---|
| The five | Each its own function, description, input schema and result model. `log_lifts` takes `sets` of `LogSetIn` (`exercise`, `weight_kg`, `weight_lb`, `reps`, `time_s`, `distance_m`, `exact`), `notes`, `new_session`, `allow_repeat` (ignored), `key`. `log_bodyweight` `kg` or `lb`. `log_measurement` `kind`, `value`. `log_food` `entries` of `FoodEntryIn`. `log_cardio` `exercise`, `minutes`, distance, speed, incline, heart rate, machine kcal, `notes`. The descriptions are inline. The field words of `log_bodyweight`, `log_measurement` and `log_cardio` live only in their signatures. Each body turns its arguments into the service's `data` inline (`model_dump(exclude_none=True)`, `allow_repeat or None`, `new_session or None`) | `app/mcp_server.py:842-872`, `:874-899`, `:1039-1064`, `:1197-1220`, `:1244-1288`; the bodies `:869-872`, `:897-899`, `:1062-1064`, `:1217-1220`, `:1284-1288`; `app/mcp_schemas.py:131-167`, `:195-223` |
| Their published size | Name, description and input schema as compact JSON: `log_lifts` 4,000 characters, `log_bodyweight` 1,534, `log_measurement` 1,435, `log_food` 3,870, `log_cardio` 3,440; 14,279 together. With output schemas: 33,546 | Printed on `3b2441e` by a scratch script, 2026-11-21 |
| The permission switch | Two settings, `log_delete` (the default) and `log`. No read-only setting: "read only is gone". Every log is at `log` in `NEEDS`; `not_allowed` has words for `log` only | `app/models.py:46`; `app/mcp_server.py:254-275`, `:353-360` |
| Hiding by account | `list_tools` drops `OWNER_FUNCTIONS` for anyone but the owner; a call of one by anyone else is `not_allowed` in `Wrapper.call` | `app/mcp_server.py:612-615`, `:680-682`; `tests/test_feedback.py:521-537` |
| A call of a function | `SpotterServer.call_tool` finds the function in the tool manager, not in the list: a registered function answers whether listed or not. Before the library's call it runs `misfit` on the function's own published schema and signature; a misfit is `schema_refusal` through the wrapper, before the function's body runs | `app/mcp_server.py:578-586`, `:619-633` |
| The start guard | The registered functions must equal `NEEDS`, or the server does not start. Every function's top level is set to take only its listed fields | `app/mcp_server.py:1589-1594` |
| The call row | One a call, named by the function called, with `caller` (`web` or `claude`), `tried` (names and counts, never values; a kind's value only for edit, delete and restore, and only when it is a table word), `outcome`, `step`, `notes` (fixed words, `key=word`, 128 characters). `tried` is built from the arguments as sent, before the work runs. The notes dict is made inside `Wrapper.call`, joined in the order noted and cut at 128. Kept 90 days | `app/mcp_server.py:667-669`; `app/calllog.py:57`, `:69-71`, `:73-90`, `:139`, `:236-252`, `:255-294`; `app/models.py:249-278` |
| The web's rows | The pages write under four of the five's names, caller `web`: `log_bodyweight`, `log_measurement`, `log_food`, `log_cardio`. No page writes a `log_lifts` row | `app/web.py:1988-1998` |
| Write limit and keys | Both go by `WRITE_FUNCTIONS`, which holds the five. A key's claim is the function's name and a fingerprint of its arguments as sent; the same key on another call is `key_reused`, nothing written | `app/services/common.py:120-123`, `:261-270`, `:375`; `app/mcp_server.py:685-696` |
| The read limit | Every function at `read` in `NEEDS` counts together: `READ_FUNCTIONS` is built from `NEEDS` | `app/mcp_server.py:274`, `:683-684`; `app/services/common.py:413` |
| Kind words of edit, delete, restore | `lift_session`, `bodyweight`, `measurement`, `food_entry`, `cardio_entry`; each log's result gives its own as `kind`. `confirm_code` hashes the kind as sent | `app/mcp_server.py:293-302`; `app/mcp_schemas.py:558`, `:612`, `:720`, `:817`, `:972`; `app/services/entries.py:130-148`, `:202-206`, `:236`, `:468-479` |
| The instructions | 4,082 of 4,096 characters. They name one of the five once, in "Log sleep, steps, resting heart rate and water with log_measurement." Pinned at 4,082 in 9 test files and by `INSTRUCTIONS_AS_BUILT`; the sleep sentence and its one `log_measurement` by its own test | `app/mcp_server.py:211-238`, `:219`; `tests/test_data.py:1123`, `test_edit_history.py:683`, `test_hi_there.py:1235`, `test_idempotency.py:737`, `test_inputs.py:140`, `test_matcher.py:654-660`, `test_routines.py:861-863`, `test_switch.py:569-573`, `test_units.py:699-712`; `test_feedback.py:96`; `test_mcp.py:2076-2079` |
| The month-end rule | Word for word in `set_goal`'s description and its `target_date`. In the instructions in a short form: "Goals: a goal by the end of a month takes that month's last day as target_date, the next such day after today." (`GOALS_MONTH_END`) | `app/mcp_server.py:184-187`, `:191-193`, `:227`, `:910`, `:937`; `tests/test_mcp.py:2057-2073` |
| Output schemas | Every listed function publishes one and has a `RESULTS` row; every real result is held to its row. The wrapper sends the result as text and as structured content itself | `tests/test_mcp.py:488-490`, `:493-498`, `:534-541`, `every_result` `:318-330`; `app/mcp_schemas.py:1608-1636`; `app/mcp_server.py:705`, `:732` |
| The homepage count | `FUNCTION_COUNT = len(NEEDS) - len(OWNER_FUNCTIONS)`, 24, shown as "functions your assistant can call". The other readers of `NEEDS` in `app/` (whoami's setup steps, the page's feedback group) read only `save_routine` and `send_feedback` | `app/web.py:1325`, `app/templates/home.html:145`; pins `tests/test_feedback.py:536`, `tests/test_trace.py:471`, `tests/test_routines.py:858`, `tests/test_web.py:44`, `:473`; `app/services/account.py:98-110`, `app/web.py:1395-1420` |
| Code words on pages | Every `NEEDS` name with no underscore is a code word on a page, case blind. Today only `whoami` | `tests/text_rules.py:396-412`, `:262` |
| Up-front loading | No function carries `anthropic/alwaysLoad` (grep of `app/`, `tests/`, `evals/`) | `docs/contracts/function-list.md` R12, not built |
| The sleep gap | Fixed on `main` since `fd13e1f`: the instructions' sleep sentence after the sentence on lift names. On `gpt-6-luna` a stated sleep went from 5 in 12 to 12 in 12 | `BUILD_LOG.md`, 2026-11-20 |
| Eval set | 258 cases, 101 core. 67 have one of the five in `must`, every one with `db` checks; 222 have one in `must_not`; 62 have one as top-level `expect.function`; 29 have one in `ok_calls` (25 at the top level, the rest inside `any_of`); 43 seeds name one (services, by name); 1 case has a second turn (`pick_lift_answer`). 118 cases list every write in `no_call` and in `must_not`. 125 cases have a write in `must` (the logging cases) | `evals/cases.json`, counted on `3b2441e`, 2026-11-21 |
| The judge | Reads each call by its name and its arguments as sent: `must`, `must_not`, `no_call`, `function`, `inputs_*`, `refused`, `ok_calls`, the four answer checks, and the two-turn checks. Its app imports are lazy. Spotter's figures come from data reads only, and `guide` is not one | `evals/judge.py:433`, `:500-626`, `:793-827`, `:916-919`, `:148`, `:151` |
| Coverage test | Every function in `NEEDS` but the owner's is some case's `must`, in the full set and in the 101 core; no tag names a function outside that set | `tests/test_evals.py:1303-1317` |
| Tool loading in `evals.run` | `--allowedTools mcp__spotter__*`, no `ENABLE_TOOL_SEARCH` set, no `--tools`; `child_env` passes a parent's variables through but the session markers. It keeps the CLI's JSON result, no transcript. `BUILD_LOG.md:1267` reports that Claude Code's default tool search looked names up; that the runner's plain run defers tools is not checked by a transcript | `evals/run.py:69-76`, `:113-130` |
| The rig on `main` | `evals/arms.py`, `evals/compare.py` and `evals/compare_cases.json` are not on `main`. Its `instructions()` raises on `main` at `GOALS_CUT`, the long month-end sentence it cuts, which `main` replaced with `GOALS_MONTH_END` | `5783ad7:evals/arms.py:198`, `:337-338`; `app/mcp_server.py:191-193` |
| The notes column | 128 characters. The per-hand check's `pair=` note sizes itself to the whole column | `app/calllog.py:57`; `app/services/lifts.py:598-607`, `:666-667` |
| Other callers of the five's names | `tools/load.py` calls `log_food` and `log_lifts` through `/mcp`; `evals/four_weeks.py` and `evals/sixteen_weeks.py` name them in comments only. `evals/need.py` reads the built surface, and also says yes when an eval file differs | `tools/load.py:5`, `:44`, `:138`, `:140`; `evals/need.py:1-20` |

## What changes

| For a chat | Today | After deploy 1 | After deploy 2 |
|---|---|---|---|
| New chat's list | the five | `log` and `guide` in their place | the same |
| Chat opened before deploy 1 | the five | still the five: they answer as today | a call of one is the library's unknown function |
| What gets written | each kind's own service | the same service, through `log` | the same |
| A call row | the five's names | `log` with `kind=lifts`; `guide` with `topic=food`; the five's names from old chats | `log` and `guide` |
| The homepage count | 24 | 21: the functions a new chat lists | 21 |

## The design

### `log`'s shape

Arm D's, with the lift part taking `LogSetIn`'s fields as they are.

| Field | Takes | Its words come from |
|---|---|---|
| `kind` | open text: `lifts`, `bodyweight`, `measurement`, `food`, `cardio`, the plain words of the rig's `OPEN_WORDS`, the table words `lift_session`, `food_entry`, `cardio_entry` (44), or a measurement's own word (`waist`, `sleep_hours`, `sleep`, `steps`, `resting heart rate`, `pulse`, `water` and the rest, the rig's `MEASURE_WORDS` over `MEASUREMENT_KINDS`). Required | Arm D's `KIND_OPEN`, plus "sleep, steps, resting heart rate or water" among its examples: the words the sleep gap showed a model must see |
| `parts` | lifts: one part a set, 1 to 60. Cardio: exactly one part. Other kinds: none. Each part a `LogPartIn` (new, `app/mcp_schemas.py`) with the ten part fields | `PARTS_DESCRIPTION`, below |
| a lift part | `movement` (the lift in the person's own words), `weight_kg` or `weight_lb`, `reps`, `time_s`, `distance_m`, `exact`; or `minutes`, `distance_km`, `distance_mi`, converted (51) | Each field's own `LogSetIn` words (`app/mcp_schemas.py:131-167`), after "Lifts: " |
| a cardio part | `movement` (the activity as `list_exercises` gives it, with `CARDIO_RULE`), `minutes`, `distance_km` or `distance_mi`; or `time_s`, `distance_m`, converted (51) | `log_cardio`'s own field words, after "Cardio: " |
| `details` | lifts `new_session`; cardio `incline_pct`, `speed_kmh`, `speed_mph`, `avg_hr`, `machine_kcal`; bodyweight `kg`, `lb`; food `entries`; measurement `kind`, `value`. An open object | Arm D's `DETAILS_DESCRIPTION`; each field's own words through `guide` |
| `date`, `notes`, `allow_repeat`, `key` | at the top, as each kind takes them | Arm D's `DATE_DESCRIPTION`, `NOTES_DESCRIPTION`, `REPEAT_DESCRIPTION`; `KEY_DESCRIPTION` |

Three texts change from arm D at `5783ad7`. The rest of its texts (`LOG_DESCRIPTION`, `DETAILS_DESCRIPTION`, `DATE_DESCRIPTION`, `NOTES_DESCRIPTION`, `REPEAT_DESCRIPTION`, `GUIDE_DESCRIPTION`, `TOPIC_DESCRIPTION`, `LOG_ROUTING`) carry word for word.

| Text | Arm D at `5783ad7` | After |
|---|---|---|
| `movement` | Built from the two functions' own words: "Lifts: The lift in the person's own words, such as squat, or a name from list_exercises." then "Cardio: " and `log_cardio`'s `exercise` words of `982e8da` ("... such as Treadmill Walk. " + `CARDIO_RULE`) | "The lift in the person's own words, such as squat, or a name from list_exercises. Cardio: the activity exactly as list_exercises gives it, such as Outdoor Walk or Treadmill Walk. " + `CARDIO_RULE`, as `log_cardio`'s `exercise` words on `main` name both walks (`app/mcp_server.py:1258`) |
| `parts` | "The numbers of lifts or cardio, as an ordered list of parts. Lifts: one part a set, in order, 1 to 60, each with movement, weight_kg or weight_lb, and reps, or time_s or distance_m as each of those says. ..." | "Lifts: one part a set, in order, 1 to 60, each with movement and what the lift records: a weight in weight_kg or weight_lb and reps, a hold's time_s, or a carry's distance_m and time_s. Only sets not yet logged: earlier sets of that date are already in its session. Cardio: exactly one part, the session, with movement and minutes, and distance_km or distance_mi when the person gave a distance. Bodyweight, measurement and food take no parts: leave it out." |
| `kind` | "What is logged, in a word: lifts, bodyweight, measurement, food or cardio, or a body measurement's own word, such as waist or sleep_hours. guide with topic log names every kind." | "What is logged, in a word: lifts, bodyweight, measurement, food or cardio, or a measurement's own word, such as waist, sleep, steps, resting heart rate or water. guide with topic log names every kind." |

Each is printed on the built server and through the text rules.

Every part takes a time as `time_s` or `minutes` and a distance as `distance_m`, `distance_km` or `distance_mi`, as the person said it (51). `route` converts to the kind's own columns, so the judge, which reads `route`'s output, sees them too: a lift's `time_s` and `distance_m`, a cardio session's `minutes` and `distance_km`. A cardio `distance_mi` goes on as `log_cardio` takes it, and its service converts it as today. A lift's time must come to a whole second, as `time_s` is whole: otherwise it is refused, with a fix naming `time_s`. Two forms of one number in one part (`time_s` and `minutes`) are refused: send one. Arm D refused another kind's field (`_part_problem`), so this part of the shape is untested: `log_plank_minutes` carries it in the release eval.

The five fields' words in `log`'s schema are written once, in `app/log_shape.py`, each true for every kind, not taken from one kind's model (51):

| Field | Its words |
|---|---|
| `time_s` | "A time in seconds, as the person said it: a hold, a carry or a cardio session." |
| `minutes` | "A time in minutes, as the person said it." |
| `distance_m` | "A distance in metres, as the person said it." |
| `distance_km` | "A distance in km, as the person said it, or with no unit and units.distance is km." |
| `distance_mi` | "A distance in miles, as the person said it, or with no unit and units.distance is mi." |

After each, the line "Spotter converts it to what the kind keeps." `guide` keeps each kind's own words and ranges, the five's, and adds that one line under its parts. These are model-read text: the text rules, and the release eval with `log_plank_minutes`.

### Every behaviour of the five, carried

`log` routes to the kind's own service with the kind's own arguments (arm D's `route`), so each behaviour below is the service's, unchanged.

| Behaviour | Where it lives | Through `log` |
|---|---|---|
| Duplicates and `allow_repeat` | `find_duplicate` in each service; lifts never refuse a repeat | `allow_repeat` at the top; lifts ignore it, as today |
| Keys | `Wrapper.call`, `common.keyed` | `key` at the top; see "Keys" |
| kg and lb | the five's fields | a set's `weight_kg` or `weight_lb`; a weigh-in's `details.kg` or `details.lb`; `units_note` as today |
| km and miles | `log_cardio`'s fields | the cardio part's `distance_km` or `distance_mi`; `details.speed_kmh` or `details.speed_mph` |
| The food estimate rule | `FOOD_RULE`, `FoodEntryIn.kcal`, `estimated` | `details.entries`, the rule in the instructions and in `guide` food |
| `find_food` ids | `saved_food_id` with `servings`, `usda_fdc_id` with `grams`; `FOOD_NAME_RULE` | the same fields in `details.entries` |
| Meals | `FoodEntryIn.meal`, its words as on `main` (`app/mcp_schemas.py:205-209`); Spotter picks it from the clock when none is named | the same, in `details.entries[].meal` |
| Cardio's activity choice and estimate | `CARDIO_RULE`, the MET estimate from body weight now | `movement`'s words carry `CARDIO_RULE`; the estimate is the service's |
| Routines | `get_routines` gives each lift with `exact` | its sets sent as lift parts, `exact` true |
| `new_session` | `log_lifts` | `details.new_session` |
| Notes | lifts and cardio | `notes` at the top; on another kind, a null is dropped and text is refused once, as arm D does |
| The lift matcher's question | `pick_lift`, `PICK_FIX` ("send that name with exact true", `app/rules.py:753`) | the same refusal, said as sent (below) |
| The per-hand check | `hand_check` on the result, its text asks once | the same result |

`log` publishes no output schema (N6). Each answer is validated against its kind's own result model (`LogLiftsResult` and the rest) by `Wrapper.call` (`app/mcp_server.py:705`). The five's result-field words that tell the assistant to act ("ask the person once") are also in the values: `hand_check`'s text says "Ask the person once" (`app/services/lifts.py:654-656`), and `units_note` is the question itself.

What the `mcp` library sends as structured content for a function that publishes no output schema: not checked. The wrapper builds a `CallToolResult` with the text and the structured content itself (`app/mcp_server.py:732`). The L1 builder probes it first, through `/mcp`, and writes what it finds in its report. L5's replay test then proves that `error_code`, `refusals` and `answer_has` can read a `log` answer.

A refusal from a kind's own checks names fields as they were sent (`said_as_sent`, as tested): `sets[2].exercise` reads `parts[2].movement`, `kg` reads `details.kg`, "set 3" reads `parts[2]`, "entry 2" reads `details.entries[1]`. Its code, numbers and other words stay. `ITEM_WORDS` (`app/mcp_server.py:404`) gains a word for `parts`, so a schema refusal from `misfit` (`_place`, `:480-487`) names a part's place too. That leaves two wordings for a part, "Part 3" from `misfit` and `parts[2]` from `said_as_sent` and `route`: open point 13.

### Where it runs

`log` is one function in `NEEDS` at `log` and one entry in `WRITE_FUNCTIONS`. One call writes one row, named `log`. Arm D instead called the hidden function through `call_tool`, so its rows and key claims read `log_lifts`: the real app must not.

In order, as `main`'s code runs a call:
1. `SpotterServer.call_tool` (`app/mcp_server.py:619-633`) runs `misfit` on `log`'s own published schema and signature. A top-level key `log` does not take, a part field `LogPartIn` does not take, or a wrong type is `schema_refusal("log", ...)` through the wrapper, in `_problem`'s words, before `log`'s body runs. Arm D never took this path: its `call_tool` caught `log` first.
2. `log`'s body runs `route` (no database). It gives the kind, the kind's function, its routed arguments and its result model, or a refusal with the kind when one resolved. `route` also checks what the published schema lets through: a key in `details` the kind does not take, another kind's part field, the conversions of 51, the part counts.
3. `Wrapper.call("log", model, work)`. `model` is the kind's own result model from step 2; `BaseModel` for a refusal, as a schema refusal does today. So the account, the row, the switch, the write limit and the key come before any refusal of step 2 is returned or anything is written, as for every call. `Wrapper.call` does not change.
4. Inside `work`: first `kind=` and `word=` are noted (`calllog.note`), since the notes dict is made inside `Wrapper.call` (`app/mcp_server.py:669`) and a note made in step 2 would be lost. Then step 2's refusal is returned, or the kind's own argument model checks the routed arguments (L0's model form of `misfit`), and the kind's own step (L0) turns them into the service's call.

Every refusal of a `log` call names `log`, never one of the five. `unknown_key` and `schema_refusal` take a function's name (`app/mcp_server.py:506-510`, `:568-575`): in this path they get `log`, and `said_as_sent` names each field as sent. `_near` looks up `NEAR_MISSES` by the function's name (`:490-496`), so `log` gets its own rows (model-read row 9). Arm D cleaned the same words with `swap_result` over every answer; the real app builds them right instead. A test sends every refusal path through `log` and finds none of the five's names in any answer.

New module `app/log_shape.py` holds the kinds, the open words, `route`, `said_as_sent`, the conversions of 51, the kind map of 52, and the part and detail field lists, moved from arm D and read against `main`. It reads L0's argument models and constants, never a registered tool, so it holds after deploy 2. It imports only `app.mcp_schemas` and `app.rules`, never `app.main`, `app.mcp_server` or `app.calllog`: the judge and `app/calllog.py` import it. `app/mcp_schemas.py` imports only `app.rules` today (`app/mcp_schemas.py:37`). A test imports `app.log_shape` alone and finds none of the three loaded.

### `guide`

| Point | Pick |
|---|---|
| Topics | `log` (every kind, its words, what its parts and details take), `lifts`, `bodyweight`, `measurement`, `food`, `cardio`. `kind` is read as `topic` (44); both sent and different is refused |
| Its answer | For a kind: the kind's description, what its parts take with each field's words, its `details` as a schema, and the top fields' words (arm D's `guides`) |
| Its result model | A small `GuideResult` in `app/mcp_schemas.py`, with a `RESULTS` row and an output schema, as every other read has. The output-schema tests and `Wrapper.call`'s check then hold for it with no exemption; only `log` is exempt (the lead's pick) |
| Where its text lives | `app/guide.py`, built at start from L0's named constants and argument models (`FOOD_RULE`, `CARDIO_RULE`, `LogSetIn`, `FoodEntryIn`, each description and field word), not from the registered tools. While both live, `guide` and the five cannot disagree; when the five go at deploy 2, the constants stay |
| Sleep, steps, resting heart rate and water | Named in `guide` measurement's answer, which carries `log_measurement`'s description as it stands at the build, and as examples in `log`'s `kind` words |
| Size | Every figure is to be printed by the builder on the built server: each topic's answer, `log`'s and `guide`'s published entries. The tests pin them. Arm D's figures of 2026-11-20 were made before `log_food` and `log_cardio` grew and with no `GuideResult`: they are not current |
| Its row and the read limit | A row (14), `notes` `topic=food`; counted with every read, as `READ_FUNCTIONS` is built from `NEEDS`. Arm D's guide wrote no row and was outside the limit |
| Its overlap with `what_is_logged` | Decided (53): apart. `guide` says how to log; `what_is_logged` keeps its menu and the person's facts, as reads stay (49) |

### Hiding the five

`RETIRING = ("log_lifts", "log_bodyweight", "log_measurement", "log_food", "log_cardio")` in `app/mcp_server.py`. `list_tools` (`:612-615`) drops them for every account, the owner's too, after the owner's own rule. Nothing else about them changes: they stay registered, in `NEEDS`, in `WRITE_FUNCTIONS`, with their descriptions and checks, so a chat holding them gets the same answer as today. A call of one writes its row under its own name, caller `claude`.

`log` and `guide` are registered right after `list_exercises` (`:820-840`), so the list puts them where `log_lifts` stood (arm D's order).

### What a call row records

| Call | `function` | `tried` | `notes` |
|---|---|---|---|
| `log` | `log` | `kind parts[3]{exact,movement,reps,weight_kg} details{new_session}`: names and counts. `parts`, `details` and `movement` join `KNOWN_NAMES`, and `LogPartIn` joins its model tuple (`app/calllog.py:73-90`). The kind's value is never written: `log` is not in `TABLE_KINDS` | `kind=lifts` (the resolved kind, one of five fixed words); `word=gym` when the word sent is one of Spotter's own open words, folded with `_`, else `word=other`; a refusal of the kind `kind=none`. The free text sent is never written |
| `guide` | `guide` | `topic` | `topic=food`, or `topic=none` |
| One of the five, by name | its own name | as today | as today |

The notes column holds 128 characters. `log`'s notes go first: `kind=` (at most 16 characters) and `word=` (at most 30). The per-hand check's `pair=` takes the room left, cut at a whole item as today; `word=` is dropped before `pair=` is cut below 3 items. `calllog` gives the room left, and `pair_note` (`app/services/lifts.py:598-607`) reads it in place of the whole column.

So, from the call log alone:
- "Nobody calls the five": no row of the five with caller `claude` in the window. Rows with caller `web` are the pages, which keep four of the five's names for good.
- "Which kinds are logged": `log` rows by `kind=`, beside the five's rows by name.

### `owner_calls` (L4)

Today `owner_calls` has no function filter and no caller filter, and with no person named it answers one line per account, never calls (`app/services/owner.py:56-80`, `_lines` `:83-100`; `app/mcp_server.py:1495-1523`). Calls come only for one account, one request or no account. The web writes four of the five's names (`app/web.py:1988-1998`), so "no assistant calls the five" needs the caller.

It gains, for the owner alone:

| Field | Takes |
|---|---|
| `function` | a function's name, matched exactly |
| `caller` | `claude` or `web` |
| `all_accounts` | true: the calls of every account in the window, newest first, with `limit` as today. The name and shape are the lead's pick |

`function` and `caller` narrow every mode. A test reads the five's rows with caller `claude` across two accounts and finds none of the web's. The new fields are model-read text for the owner's list only.

### The permission map

| Setting | `log` | `guide` | The five, by name |
|---|---|---|---|
| `log_delete` (the default) | allowed | allowed | allowed |
| `log` (read and log, no delete) | allowed | allowed | allowed |
| Read only | No such setting (`app/models.py:46`). Should one return, `log` is `not_allowed` before any routing, and `NOT_ALLOWED_MESSAGES` needs its row | allowed | `not_allowed` |

### Keys

A key's claim is the function's name and a fingerprint of the arguments as sent (`app/services/common.py:261-270`). A key sent to `log_lifts` and then to `log` is `key_reused`: nothing is written by the second, and its fix asks for a new key. One chat holds one list, so the same key reaches both only across two chats. Pick: `log` claims under its own name, as every function does, and the test names the crossed case.

### Kind words

`log` reads its five words and the table words. `edit_entry`, `delete_entry` and `restore_entry` also take `log`'s five words from deploy 1 (52): `lifts` as `lift_session`, `food` as `food_entry`, `cardio` as `cardio_entry`; `bodyweight` and `measurement` are the same words.

One map, in `app/log_shape.py`, used in two places:
- At the door of `edit_entry`, `delete_entry` and `restore_entry` in `app/services/entries.py`, before the kind check (`:130-148`, `:468-479`) and before `confirm_code` hashes the kind (`:202-206`, `:236`). So a preview with `lifts` and a confirm with `lift_session` match, and every answer that echoes `kind` echoes the table word.
- In `calllog.tried` (`app/calllog.py:282`), since `tried` is built from the arguments as sent before any service runs (`app/mcp_server.py:667-668`). So the call row keeps the table word, and counts stay comparable.

Their kind description names both word sets for new chats; a chat opened before keeps its own, and the table words still work. Nothing else about the three changes (49).

### Model-read text: every string that names the five or their fields

Two kinds of text, by when a chat reads it:
- **Fixed for a chat:** the instructions, descriptions and schemas. A chat keeps the ones it started with. After deploy 1 only new chats read the new ones, so these may name `log` and `guide` and must not name the five.
- **Read by both lists:** answers, refusals, fix lines and the resources, read at call time. Each names the function that was called, or names none.

| # | Where | Today | Read | Rule | After |
|---|---|---|---|---|---|
| 1 | Instructions, `app/mcp_server.py:211-238` | name `log_measurement` once, in 48's sentence (`:219`) | fixed | may name `log` | the routing sentence in place of 48's sentence; names none of the five (below) |
| 2 | `list_exercises`' description, `app/mcp_server.py:826` | "log_lifts also takes the person's own words" | fixed | name `log` | "log also takes the person's own words for a lift" |
| 3 | `ENTRY_ID_DESCRIPTION`, `app/mcp_server.py:298-300` (edit, restore, delete) | "from log_lifts, log_bodyweight, log_measurement, log_food, log_cardio or get_log" | fixed | name `log` | "from log or get_log" |
| 4 | `get_routines`' description, `app/mcp_server.py:1423-1424` | "send its sets with log_lifts, one item a set with exact true" | fixed | name `log` | "send its sets with log, kind lifts, one part a set, the lift as movement, with exact true" |
| 5 | The five's descriptions and field words, `app/mcp_server.py:842-1288`, `app/mcp_schemas.py:131-223` | as today, inline | fixed in old chats | unchanged | moved to named constants in L0, byte for byte; also `guide`'s source |
| 6 | `FoodEntryIn`'s docstring, `app/mcp_schemas.py:196`, published in `$defs` | "One food in a log_food call." | fixed, also in `guide` food | none | "One food entry." |
| 7 | `SavedFood.id`, `app/mcp_schemas.py:734` (find_food's and save_food's results) | "log_food takes it as saved_food_id" | fixed | none | "Log it as saved_food_id, with servings." |
| 8 | `UsdaFood.fdc_id`, `app/mcp_schemas.py:770` | "log_food takes it as usda_fdc_id, with grams" | fixed | none | "Log it as usda_fdc_id, with grams." |
| 9 | `ITEM_FIELDS`, `ITEM_WORDS`, `NEAR_MISSES`, `app/mcp_server.py:404`, `:409-414`, `app/mcp_schemas.py:96-109` | rows keyed by `log_lifts`, `log_measurement`, `log_cardio`, `log_bodyweight`; no word for `parts` | both | the function called | the five's rows stay; `log` gets its own for a part and for `details`; `ITEM_WORDS` gains `parts` |
| 10 | `unknown_key`, `app/mcp_server.py:506-510` | "'x' is not a field of log_lifts" | both | the function called | unchanged; `log`'s own from `misfit` and `route` |
| 11 | `not_found`'s fix, `KIND_LABELS`, `app/services/common.py:106-115`, `:134-139` | "the id that log_lifts returned, or one from get_log" | both | none | "the id that logging it returned, or one from get_log", for each of the five kinds |
| 12 | `already_done`, `app/services/common.py:251-254` | "Read it back with get_log" for any log | both | none of the five | unchanged; `log` takes the default |
| 13 | The duplicate note, `app/services/common.py:456` | "send it again with allow_repeat: true" | both | a field both shapes take | unchanged |
| 14 | `set_goal`'s start refusals, `app/services/goals.py:698-713` | "(log_lifts)", "(log_bodyweight)", "(log_measurement)", "(log_cardio with distance_km)" | both | none | "Log a session with X first", "Log a weigh-in first", "Log one first", "Log one first, with its distance" |
| 15 | `set_goal`'s time fix, `app/services/goals.py:269` | "as log_cardio takes" | both | none | "as a cardio entry takes" |
| 16 | `save_routine`'s refusal, `app/services/routines.py:140` | "Log it with log_lifts." | both | none | "Log its sets instead." |
| 17 | `what_is_logged`'s saved food id, `app/services/fields.py:813` | "The saved food's id, for log_food." | both | none | "The saved food's id, to log it by." |
| 18 | `unknown_exercise`'s and `pick_lift`'s fixes, `app/rules.py:489-490`, `:753` | name `list_exercises` and `exact` | both | fields both shapes take | unchanged; through `log`, said as sent |
| 19 | `hand_check`, `app/services/lifts.py:654-656` | names `edit_entry` | both | unchanged | unchanged |
| 20 | `whoami`'s notes and setup, `app/services/account.py:88-102` | name `update_settings` only; "save each answer with its function" | both | none of the five | unchanged |
| 21 | `get_log`'s answers, `app/services/log.py` | name none (its module docstring names `log_food`, not read by a model) | both | none | unchanged |
| 22 | `docs/can-and-cant.md:39` | "`log_lifts` says so in its summary" | both | none | "the summary says so" |
| 23 | `docs/can-and-cant.md:176` | "every `log_lifts` call adds its sets" | both | none | "every call that logs sets adds them" |
| 24 | `docs/can-and-cant.md:360` | "Claude sends its sets with `log_lifts`" | both | none | "Claude logs its sets" |
| 25 | `docs/can-and-cant.md:460-484`, the function table (the five at `:462`, `:463`, `:468`, `:473`, `:475`) | the five, one row each | both | both lists | rows for `log` and `guide` where `log_lifts` stood; one line under the table: "A chat opened before the change holds log_lifts, log_bodyweight, log_measurement, log_food and log_cardio in their place: they log the same entries." The line goes at deploy 2 |
| 26 | `README.md`, `app/templates/` | name none of the five (grep) | | | unchanged |
| 27 | The homepage count, `app/web.py:1325`, `app/templates/home.html:145` | 24, as `len(NEEDS)` less the owner's three | person-read | what a new chat lists | 21: `NEEDS` less the owner's three and `RETIRING` (N7) |

`docs/evals.md` names the five on 41 lines (grep): the judge's notes and the case tables, not model-read, read by `tests/test_evals.py` and `tests/test_switch.py`. Most stay under routing; L5 adds the routing note, the new cases' rows and the flags.

### The instructions

Measured on `3b2441e` by a scratch script, 2026-11-21:

| Text | Characters |
|---|---|
| Today: 48's sentence in, the short Goals sentence, the meal sentence of `eafd21f` | 4,082 |
| 48's sentence, "Log sleep, steps, resting heart rate and water with log_measurement. " | 69 |
| The rig's routing sentence, "Record an entry with log. Call guide with its kind before any details. " | 71 |
| Deploy 1: 48's sentence out, as new chats have no `log_measurement`; the rig's sentence in | 4,084 |
| The month-end sentence dropped whole, as first tried for the room | goals asked which year in 3 of 6 runs on `gpt-6-luna`: not used |

Where `log`'s sentence sits (open point 11): where 48's sentence sits, right after the sentence on lift names. Measured on `3b2441e` with 48's sentence swapped for it: it starts at 544 and ends at 614 with its trailing space, past the first 512 (function-list R13), and "Spotter picks the lift" keeps its place, ending at 497. The rig tested it before the sentence on lift names, ending at 483, which pushes "Spotter picks the lift" to end at 569 (measured on `fd13e1f`; nothing before those places changed since): that is the fallback if the release eval's lift cases dip on `gpt-6-luna`. `MONTH_END_RULE` itself does not change: `set_goal`'s description and `target_date` carry it word for word. A description is not enough on its own: `gpt-6-luna` asks the year before it reads `set_goal`, as it missed sleep before it read `log_measurement`. The short Goals sentence keeps the clause that answers the year, "the next such day after today": on `gpt-6-luna`, 6 runs a case on both trees, `goal_date`, `running_goal_from_start`, `postpone_lift_deadline` and `lb_bench_goal` 24 of 24 against 22 of 24 on `main`; over every run of `lb_bench_goal` on it that day, 2 of 15 asked the year, against 0 of 15 on `main`. The rig's full runs held the whole month-end sentence and its Sonnet lift rerun dropped it (no lift case sets a goal): the short one is not tested on arm D.

The instructions after keep ending with the plan_line sentence and name none of the five.

### Up-front loading

Claude Code defers MCP functions and searches before first use, per its docs. Every rig number was measured with every function loaded up front. The runner's default on `main` sets nothing, and `BUILD_LOG.md:1267` reports that a run with the default searched; no transcript shows it. So the release run is the first measure of `log` and `guide` under tool search.

Pick (N10): no pilot and no `alwaysLoad` now. The release run's Claude passes run with `--tool-search on --transcripts` (L5), logging cases first. Their transcripts are read for a search step before the first `log` and for any call of a hidden name. A dip stops the merge. The fallback is `alwaysLoad` on `log` and `guide` (function-list R12's `meta`), in its own item, with its own run of the logging cases.

## The path

| Deploy | Holds | Gap |
|---|---|---|
| Before | The sleep gap's fix on today's list (48): live since `fd13e1f`, so the instructions are 4,082 when this build starts | |
| 1 | L0 to L5: the five's words as constants; `log` and `guide`; the five hidden and answering; the homepage count; the instructions; every string of the table; the call row's notes; `owner_calls`' filters; the evals | The target is before December (N1) |
| 2 | L6: the five's MCP registrations removed, and their names out of `NEEDS`. Their services, their names in `WRITE_FUNCTIONS` (the web's rows), their argument models and the constants `guide` reads stay. Row 25's line goes | Once no row of the five with caller `claude` is in the window the owner sets, read with `owner_calls` |

No required result field and no column is removed in either deploy. A function hidden first and removed later is the two-deploy rule's shape for a function.

## Packages

They build one after another in this order, in one tree: L0, L1a, L1b, L2, L3, L4, L5. L0 to L3 share `app/mcp_server.py` and `app/mcp_schemas.py`; L4 and L5 read L1's names. Each builder runs its own test files, plain and with `-W error`, and ends green on them. A test file is in the package whose change first breaks it, so no package hands on a red tree. The builder greps for every other reader of what it changes and adds it to its own list. One commit a package, seven for deploy 1, each on a green tree (the owner's pick, 2026-11-21). The lead reads L1's diff and test output before L2 starts.

L1 builds in two parts, each its own commit (the owner's pick, 2026-11-21):

| Part | Holds | Ends with |
|---|---|---|
| L1a | `app/log_shape.py` and `app/guide.py` as plain code, with `LogPartIn` and `GuideResult` in `app/mcp_schemas.py`: the kinds, the open words, `route`, `said_as_sent`, the conversions of 51, the kind map of 52, each topic's answer. Neither function is registered. Nothing published moves | `tests/test_log_and_guide.py`'s unit tests of `route`, `said_as_sent` and each `guide` answer; the import test; `PUBLISHED` unmoved |
| L1b | Everything else in L1's row: the two functions registered, `NEEDS`, `RETIRING`, `list_tools`, the notes, the write tuple, the homepage count, row 25, the eval files, and every test keyed on the list | L1's whole test list green | One fix round after the review.

What first breaks a test, by trigger:

| Trigger | Package |
|---|---|
| The five's words move to constants, the bodies call a shared step | L0: nothing published moves |
| `log` and `guide` join `NEEDS`, the five leave the list, `log` joins `WRITE_FUNCTIONS` | L1: every test keyed on `NEEDS`, the list, the write tuple or the function table |
| The instructions and the fixed text change, the kind words of 52 | L2 |
| The text both lists read changes | L3 |
| `owner_calls` gains its fields | L4 |
| The judge routes, the runner gains its flags | L5 |

| Package | Deploy | Effort | Files | Its tests |
|---|---|---|---|---|
| **L0** The five's words as constants | 1 | high | `app/mcp_schemas.py`, the one home of every text `log_shape` and `guide` read: each of the five's description as a named constant; the bodyweight, measurement and cardio field words as named constants; `CARDIO_RULE` (`app/mcp_server.py:102`) and `FOOD_RULE` (`:169`) moved here (`FOOD_NAME_RULE` is here already, `:112`), and `KEY_DESCRIPTION` (`:285`) if `guide` reads it; a standalone argument model for each of the five, its fields carrying the same constants, `LogSetIn` and `FoodEntryIn` as they are. `app/mcp_server.py`: imports each moved text back under its own name; the five's signatures read the constants; each body's argument-to-`data` step pulled out into one function a kind, which the decorated function calls; `misfit` gains a form that takes an argument model, with the library's reading of a list or object sent as JSON text | `tests/test_mcp.py` gains one helper beside `tool_list` (`tests/test_units.py:34` imports `tool_list` from there): `registered_tools(client)`, every registered function's entry by name as `tools/list` would publish it, read from the server's tool manager. L1's tests read the five through it. `tests/test_log_and_guide.py` (new): each argument model's schema equal to its registered function's input schema; each pulled-out step gives the same `data` as the body did. `tests/test_feedback.py` (`PUBLISHED`: no hash moves), `tests/test_mcp.py`, `tests/test_units.py`, `tests/test_inputs.py`, and the test files of the five: `tests/test_gym_logging.py`, `tests/test_sets.py`, `tests/test_hand_check.py`, `tests/test_matcher.py`, `tests/test_hi_there.py`, `tests/test_idempotency.py` |
| **L1** `log`, `guide` and the list | 1 | high | `app/log_shape.py` (new), `app/guide.py` (new), `app/mcp_server.py` (the two functions after `list_exercises`, `NEEDS`, `RETIRING`, `list_tools`, `ITEM_FIELDS`, `ITEM_WORDS`, the notes inside `work`), `app/mcp_schemas.py` (`LogPartIn`, `GuideResult`, its `RESULTS` row, `NEAR_MISSES` rows), `app/services/common.py` (`WRITE_FUNCTIONS`), `app/calllog.py` (`KNOWN_NAMES`, the room left), `app/services/lifts.py` (`pair_note` reads the room left), `app/web.py` (`FUNCTION_COUNT`), `docs/can-and-cant.md` (row 25 only), `evals/judge.py` (`WRITE_FUNCTIONS` only, `log` at the place L1 puts it in `common`), `evals/cases.json` (`log` added by script to the `no_call` and `must_not` of the 118 no-write cases; the two new cases), `evals/without_cases.json` (`log` in each write list) | `tests/test_log_and_guide.py`; `tests/test_mcp.py` (`FUNCTIONS` `:253`, `READS` gains `guide`; the output-schema tests `:488-490`, `:493-498`, `:534-541` and `every_result` `:318-330` exempt `log`, whose results are held to their kind's model; the can-and-cant table test `:2194-2207` reads the listed functions, `NEEDS` less the owner's and `RETIRING`; the five's schemas `:503-528`, `:1654`, `:2097`, `:2163`, `:2367` read through `registered_tools`); `tests/test_feedback.py` (the lists `:521-537`; the five's `PUBLISHED` hashes read through `registered_tools`, so they stay pinned as unchanged; `log` and `guide` added; `FUNCTION_COUNT` `:536`); `tests/test_trace.py` (`:226-234`: the published names plus the five's own registered schemas equal `KNOWN_NAMES`; `:471`); `tests/test_hi_there.py` (`:345` matches whole words; `:566`, `:573`, `:684` read the five through `registered_tools`; `hi.html` holds no whole-word "log" or "guide" today, by grep); `tests/text_rules.py` (`log` and `guide` exempt as plain words, N8) and the page tests through it; `tests/test_web_account.py` (`SWITCH_ALLOWS` `:1478-1487` gains both); `tests/test_web.py` (`OFFERED` `:44`, `:473`); `tests/test_routines.py:858`; `tests/test_sixteen_weeks.py:299` (the write tuple); `tests/test_progress.py:453` (`OFFERED` from `FUNCTIONS`); `tests/test_safety.py` (reads one of the shared lists, by grep: which, not checked); `tests/test_gym_logging.py:642`, `tests/test_hand_check.py:413`, `tests/test_matcher.py:681`, `tests/test_sets.py:518`, `tests/test_units.py:699-709` (each reads one of the five from the list: through `registered_tools`); `tests/test_calllog.py` (every published field has a marker, `key` for a write), `tests/test_idempotency.py`, `tests/test_write_limit.py`, `tests/test_inputs.py`, `tests/test_hand_check.py` (the `pair_note` pins `:380-406` take the room left); `tests/test_evals.py` (the coverage test `:1303-1317` keeps reading `NEEDS`, core 103 at `:1312`; `:1320-1324`; `:390-393` and `:535` read `WRITES` with `log`; the `--core` run `:1681-1687` at 103; the case count `:66`); the case-order pins in `tests/test_inputs.py`, `tests/test_hi_there.py`, `tests/test_routines.py`, `tests/test_units.py`, as the clash reading names them (not checked here: the builder greps); `tests/test_golden_cases.py` (the two cases' pass and fail runs; the counts `:1683`, `:1692-1693`; a tail test as C4g's at `:1821-1826`); `tests/test_without.py`; the 11 files that read `docs/can-and-cant.md` (`test_calllog`, `test_docs`, `test_edit_history`, `test_eval_need`, `test_feedback`, `test_hi_there`, `test_inputs`, `test_mcp`, `test_switch`, `test_text_rules`, `test_units`) |
| **L2** The fixed text and the kind words | 1 | high | `app/mcp_server.py` (the instructions, rows 2 to 4, `KIND_DESCRIPTION` `:293` for 52), `app/mcp_schemas.py` (rows 6 to 8), `app/services/entries.py` (the kind map at the door of the three), `app/calllog.py` (`tried` maps the kind, `:282`) | the 4,082 pins in the 9 files, to 4,084; `tests/test_feedback.py` (`INSTRUCTIONS_AS_BUILT` `:96`; the `PUBLISHED` rows that move: `list_exercises`, `edit_entry`, `delete_entry`, `restore_entry`, `get_routines`, `find_food`, `save_food`, and `log_food` for row 6), `tests/test_mcp.py` (`:2076-2079` becomes the routing sentence's test, with no `log_measurement`; `:2057-2073`; `:2124-2132`; `:2572-2583`), `tests/test_units.py:699-712` (the entries that differ), `tests/test_routines.py` (`:72`, `:823`, `:828-830`), `tests/test_matcher.py:685`, `tests/test_inputs.py`, `tests/test_trace.py`; for 52 `tests/test_entries.py`, `tests/test_edit_history.py` (`:664` pins `KIND_DESCRIPTION`), `tests/test_calllog.py` |
| **L3** The text both lists read | 1 | medium | `app/services/common.py` (row 11), `app/services/goals.py` (rows 14, 15), `app/services/routines.py` (row 16), `app/services/fields.py` (row 17), `docs/can-and-cant.md` (rows 22 to 24) | `tests/test_inputs.py` (`:1143`, `:1168`, row 15), `tests/test_sets.py` (`:327-331`, row 16); the other files that assert on these strings: the builder greps each; the 11 files that read `docs/can-and-cant.md` |
| **L4** `owner_calls`' filters | 1 | medium | `app/services/owner.py`, `app/mcp_server.py` (`owner_calls`) | `tests/test_feedback.py` (`PUBLISHED` `owner_calls`), `tests/test_trace.py` |
| **L5** The evals | 1 | high | `evals/judge.py` (routing, below), `evals/run.py` (`--tool-search`, `--transcripts`), `evals/recorder.py` (read; a change only if the probe of L1 shows `log`'s answer needs one), `evals/without.py` (read: covered by routing in `judge_calls`), `docs/evals.md` | `tests/test_evals.py` (the routing, the flags; `child_env`'s pin `:1227-1233` holds, as the variable is set after it), `tests/test_golden_cases.py` (the replay test), `tests/test_without.py` (a routed `log` lifts call counts for U2's `ok_calls`), `tests/test_switch.py` (`:619-627`: no read-only words in `docs/evals.md` or `evals/judge.py`), `tests/test_web.py:474` |
| **L6** The five removed | 2 | medium | `app/mcp_server.py` (the five's registrations and their names in `NEEDS`, so the start guard at `:1589-1592` holds; `RETIRING` goes), `docs/can-and-cant.md` (row 25's line), `tools/load.py`, `tests/test_evals.py` (the coverage test's allowed names gain the five as the kinds' routed names, so `:1313-1314` holds for the cases that name them), the tests that read the five through `registered_tools`, and the five's `PUBLISHED` rows, go with the registrations; every test that calls the five through `/mcp` (42 test files hold their names as quoted strings, 774 times, by grep on `3b2441e`): to `log`, or to the services | the whole suite |

C3 and C5 edit L0's constants after their rebase, not a copy of their own ("Clashes with C3 and C5").

### L5's routing, in full

- One step, `routed(calls)`, at the top of `judge_calls`, `judge_tags`, `judge_turns` and `made_must_call` (`evals/judge.py:523`, `:605`, `:793`, `:916`). So `evals/without.py`, which calls `judge_calls` directly (`:224`, `:233`), is covered with no edit.
- A routed `log` call answers to both names: `log` and the kind's function. A check naming `log` sees every `log` call. A check naming one of the five sees the five's own calls and the routed `log` calls.
- A `log` call whose kind resolves but whose shape `route` refuses counts under the kind's name as a refused call, so `must_not`, `no_call`, `refusals` and `error_code` still catch it. A `log` call whose kind resolves to none counts as `log` alone.
- Every check that reads a call by name reads the routed calls: `function`, `inputs_include`, `inputs_exclude`, `inputs_forbid`, `refused`, `ok_calls`, `no_call`, `error_code`, `no_error_code`, `refusals`, `answer_has`, the tags' `must` and `must_not`, and in `judge_turns` the early write, `error_code` by turn and `inputs_include` in turn 2 (`:802-818`). Key paths are routed in full: `details.new_session` is read as `new_session`, `details.entries[].meal` as `entries[].meal`, a cardio part's fields at the top.
- The judge imports `app.log_shape` inside the routing step, as its other app imports are lazy (`:433`).
- A reason names a routed call as `log (lifts)`, so a failed run reads right.
- The coverage test keeps reading `NEEDS` (`tests/test_evals.py:1307`).
- `guide` stays out of `DATA_READS` (`:148`): a test pins it, so `guide`'s numbers never count as Spotter's figures.
- `--tool-search on|off`, Claude only, default `on`. `off` sets `ENABLE_TOOL_SEARCH=false` in the child's environment, as the rig did; `on` removes any value from the parent, so it is the CLI's default whatever the shell holds. Set after `child_env`. The mode is printed in the run's lines. Codex and Gemini print it as not applied.
- `--transcripts`, Claude only: `--verbose` on the CLI, each turn's output kept, masked. For Codex it is `--keep`'s stream; Gemini keeps one already.
- `docs/evals.md`: both flags in the flags table (`:48-61`); the routing note; the new cases' rows; `:453`'s rule on writes names `log`; `:656-658` and `:682` say the coverage test reads `NEEDS`, which at deploy 1 is wider than the list; `:767`'s "the core stays at 101" reads 103.
- The replay test, in `tests/test_golden_cases.py`: existing pass and fail runs sent again as `log` calls, one a kind (`stated_number`, `lb_weigh_in`, `waist_in_inches`, `hold_plank`, `miles_run`) plus `pick_lift_answer` (two turns) and `hand_check_family` (`answer_has`). Each judges as it does through the five. This proves the `must_not` cases, the `ok_calls` cases and the two-turn case with no model, and that the answer checks can read a `log` answer.

## Tests

Every date from a fixed today, never the real date. No test touches the network.

| File | What |
|---|---|
| `tests/test_log_and_guide.py` (L0) | Each of the five's argument models equal to its registered input schema; each pulled-out step equal to the body it replaced; the `PUBLISHED` hashes unmoved |
| `tests/test_log_and_guide.py` (L1) | For each kind, one write through `log` and the same write through the five give the same rows and the same answer, but for `said_as_sent`. Each row of "Every behaviour, carried": a duplicate and `allow_repeat`; kg and lb; km and miles; a stated, an estimated, a saved and a USDA food; a meal named and a meal left out; a treadmill with no speed taking the walk; a routine's sets with `exact`; `new_session`; notes on a kind that takes none; `pick_lift` with both dates; `hand_check`; a hold (`time_s`), a carry (`distance_m`, `time_s`). Every kind word and measurement word; an unknown word refused with the closest. The order of checks: a field `log` does not take refused by `misfit` as `log`, its row named `log`. One refusal of a part from `misfit` and one from `said_as_sent`, each printed (open point 13). `guide` on every topic, `kind` as `topic`, both differing refused; each answer equal to its kind's description and field words; each size printed and pinned. One row a call, named `log` or `guide`, its notes in order, no value in any column (the marker test). The write limit counts `log`; the read limit counts `guide`. A key: the same `log` call twice gives the first answer; a key sent to `log_lifts` then `log` is `key_reused`, nothing written. Both settings allow `log`. Each time and distance form on each kind (51): a plank sent in minutes stored as seconds, a carry sent in km stored as metres, a run sent in metres stored as km, a run in miles as today; a lift time short of a whole second refused with its fix; two forms of one number in one part refused. `app/log_shape.py` imported alone loads none of `app.main`, `app.mcp_server`, `app.calllog` |
| `tests/test_feedback.py`, `tests/test_mcp.py` (L1) | The list for everyone and for the owner: `log` and `guide` where `log_lifts` stood, none of the five. Each of the five called by name answers as before, its row under its own name. Only the entries this contract names differ, byte for byte. `log` has no output schema; `guide` has one. The homepage count is 21 |
| `tests/test_entries.py`, `tests/test_edit_history.py` (L2) | Edit, delete and restore by each of `log`'s five words and by the table words: the same entry and the same answer; a delete previewed with `lifts` and confirmed with `lift_session`; an unknown word refused as today; the call row keeps the table word (52) |
| `tests/test_mcp.py` (L2) | The instructions at 4,084, ending with the plan_line sentence, naming none of the five, with the routing sentence after the sentence on lift names; `MONTH_END_RULE` in `set_goal` and `target_date` word for word; the shorter Goals sentence in the instructions |
| `tests/test_feedback.py`, `tests/test_trace.py` (L4) | `owner_calls` by function, by caller, and across accounts: the five's rows with caller `claude` read apart from the web's |
| `tests/test_evals.py`, `tests/test_golden_cases.py`, `tests/test_without.py` (L5) | The routing rules above, one test each; the replay test; the two flags |
| `tests/test_text_rules.py` and its helper (L1 to L4) | Every new and changed string through the text rules |

## Eval cases

**The judge reads a `log` call as the kind's function too** ("L5's routing, in full"). The recorder keeps the call as sent. So every existing case keeps its words, and a wrong write through `log` still fails the 222 cases that forbid one. The `db` checks read the rows, and stay. `log` joins the judge's write tuple, and the `no_call` and `must_not` of the 118 no-write cases, by script in L1: so a refused `log` call whose kind resolves to none also fails a no-write case.

`guide` is no data read: its numbers ("1 to 60") never count as Spotter's figures.

New cases, both core (N9), each with a passing and a failing run in `tests/test_golden_cases.py`, added in L1:

| Id | Say | Seed | Calls | First: rows or calls | Then: the reply |
|---|---|---|---|---|---|
| `guide_how_food` | "how do I log food with you?" | none | `must` `guide`; every write in `no_call` and `must_not` | a `guide` call, nothing written | no kcal figure of its own: `reply_lacks_pattern` on a number next to kcal or calories. A range quoted from `guide`'s words would fail it too; the pattern is the lead's pick, and the builder shows it against the pass and fail runs |
| `log_plank_minutes` | "held a plank for 2 minutes" | none | `must` `log`; `log_lifts` in `may`, so it passes before L5 routes and after | one Plank set of 120 s, no reps | names Plank |

Dropped, as the ground is covered through routing: `log_word_both` (the same say and seed as `word_both_logged`, which fails the duplicate test at `tests/test_golden_cases.py:1703-1706`), `log_plank` (`hold_plank`), `log_farmers_carry` (`carry_farmers`), `log_sleep_word` (`sleep_record`).

So 260 cases, 103 core. The pins that move: `tests/test_evals.py:1312` and `:1681-1687` (103, "103 cases x 1 runs"); `tests/test_golden_cases.py:1683` (new cases equal `RUNS`, plus 2), `:1692-1693` (direct and core direct, plus 2 each), a tail test for the two after C4g's (`:1821-1826`); `tests/test_web.py:474` follows the file.

### The release eval

`python -m evals.need main` says yes. One run for the release, at the gate tier (N11):

| Pass | Model | Cases | Runs | Mode |
|---|---|---|---|---|
| 1 | `claude-sonnet-5-5` | the logging cases: 125 with a write in `must`, plus `log_plank_minutes`: 126 | 378 at 3 | `--tool-search on --transcripts` |
| 2 | `claude-sonnet-5-5` | the other 134 | 402 at 3 | `--tool-search on --transcripts` |
| 3 | `gpt-6-luna` | the 126 logging cases | 378 at 3 | as the client runs |

Pass 1 is read before passes 2 and 3 run: the transcripts for a search step before the first `log` and for a call of a hidden name, the misses by case. A dip stops the merge (N10). A dip is read by comparison, not by a count: each case that misses in pass 1 is run again twice, on `main`'s tree in the same mode, and on this branch with `--tool-search off`. Only a case that holds on `main` and misses on this branch counts. The second run says whether the search or the shape caused it, which says whether `alwaysLoad` is the right fallback. No price is written here: the cost line for passes 2 and 3 is said from pass 1's own figures before they run. `gpt-6-luna` is plan usage (`docs/evals.md:116`). The real figures go in `BUILD_LOG.md`.

Open points 11 and 12 have bars at 9 and 12 runs, so pass 3 adds runs for them: the plain-word lift cases and `mixed_unit_session` to 9 runs each, `sleep_record` to 12. Which cases are the plain-word lift cases: the lead names them from `evals/cases.json` before the run, with their cost from pass 1's figures.

## The exit check

1. Every package's tests pass, plain and with `-W error`.
2. The whole suite once on a clean export of each deploy's exact commit.
3. One reviewer, on a frozen copy, after L5, for L0 to L5: L0's published entries unmoved; each behaviour row by probe through `/mcp`; a write through each of the five by name; the call rows.
4. The instructions printed at 4,084; `log`, `guide` and each `guide` answer printed and matching the pins.
5. The release eval, as above, its transcripts read.
6. After deploy 1: `/health` 200, `/mcp` 401 with no token, `/` 200, `/ready` 200. A fresh assistant session lists `log` and `guide` and none of the five. The homepage says 21. The owner, in a chat opened before the deploy, logs one entry: it goes through the old function, its row reads that name.
7. Before deploy 2: the owner reads the five's rows with caller `claude` in his window with `owner_calls`, and says go.

## Clashes with C3 and C5

Read on `3b2441e`, 2026-11-21. What C3 and C5 do is as their contracts and builds said on that day, not checked on `main` here. This item merges first; C3 then C5 rebase onto it (N14).

### The counts after each item lands

| Count | `main` | After this item | After C3 | After C5 |
|---|---|---|---|---|
| `NEEDS` entries | 27 | 29 | 29 | 30 |
| Listed for everyone | 24 | 21 | 21 | 22 |
| Listed for the owner | 27 | 24 | 24 | 25 |
| `WRITE_FUNCTIONS` | 14 | 15 | 15 | 16 |
| The homepage count | 24 | 21 | 21 | 22 |
| Cases | 258 | 260 | 275 | 283 |
| Core | 101 | 103 | 103 | 104 |
| Instructions | 4,082 | 4,084 | 4,084 (4,079 on C3's miss reword) | the same |
| Migration head | `0014` | `0014` | `0015` | `0016` |

C3's 15 new cases and C5's 8, its one new core case (`program_saved`) and `save_program` are as their contracts say. Spare characters after all three: 12.

### The clash table, by place

Kind: SL same lines, SC same constant or dict, CT a count both move, SQ a string one rewords and another quotes or pins, DS a design point.

**The instructions**

| Where (`main`) | This item | C3 or C5 | Kind | On the rebase |
|---|---|---|---|---|
| `app/mcp_server.py:211-238`; `INSTRUCTIONS_AS_BUILT`; the 9 length pins | 48's sentence at `:219` becomes the routing sentence: 4,084 | C3: none by default; on a miss "except a food's kcal" (`:221`) becomes "except for food", 5 fewer. C5: none | SL, CT | Every 4,082 pin reads 4,084. C3 on its miss: 4,079 and a new hash. C3's "stays 4,082" (its decision 13) is wrong: the import line it rewords is `IMPORT_DESCRIPTION`, not the instructions |
| The budget | +2 | C3 0 or -5; C5 0 | CT | 12 spare. A sleep sentence of about 69 characters needs a cut (open point 12) |

**Food (C3)**

| Where (`main`) | This item | C3 | Kind | On the rebase |
|---|---|---|---|---|
| `log_food`'s description, `app/mcp_server.py:1200-1205` | L0 makes it a named constant, which `guide` food reads | adds one sentence after `FOOD_RULE` | SL, SC | C3 adds it to the constant; `guide` food's pinned size moves, in `tests/test_log_and_guide.py` |
| `FoodEntryIn`, `app/mcp_schemas.py:195-223` | docstring `:196` becomes "One food entry." | five new fields | SL | A line merge. The fields reach `guide` food and the hidden `log_food` by themselves, as `guide` reads the models |
| `log`'s schema | `details` is an open object | its fields appear in `guide` food only (N12) | DS | Nothing to add to `log`'s schema |
| Chats that hold `log_food` | after deploy 1 only chats opened before it, with the pre-C3 schema | C3's `PUBLISHED["log_food"]` becomes a pin on a hidden function | DS, open | C3 settles whether its portion refusal locks such a chat out of an estimate (C3's own question) |
| C3's portion refusal through `log` | `said_as_sent` says "entry 2" as `details.entries[1]` | "Entry 2: portion is missing ..." / "... send it as portion in entry 2 ..." | SQ | Reads "details.entries[1]: portion is missing ..." through `log`. C3 checks the words pass the text rules; this item's refusal test sends it once C3 lands |
| `SavedFood.id`, `app/mcp_schemas.py:734` | "Log it as saved_food_id, with servings." | "a meal too" | SL | One string with both, such as "Log it as saved_food_id, with servings: a meal too." |
| `UsdaFood`, `app/mcp_schemas.py:769-770` | `fdc_id`'s words | three fields per 100 g | SL | A line merge |
| `ITEM_FIELDS` `"entries"`, from `food.ENTRY_FIELDS` | `log`'s rows for parts and details | extends `ENTRY_FIELDS` | SC | Whether `log`'s details fix lists food fields from `ENTRY_FIELDS`: the L1 builder says |
| The notes column | `kind=`, `word=` first; `pair=` takes the room left | a new `atwater=` note, sized to the whole column | DS, SC, open | `atwater=` uses the room left. Whether it is noted in the food service (so `log` kind food gets it too) is C3's to settle |
| `app/services/fields.py:813` | "to log it by" | the food area's fields in the same file | maybe SL | Read at the rebase |

**Routines (C5)**

| Where (`main`) | This item | C5 | Kind | On the rebase |
|---|---|---|---|---|
| `get_routines`' description, `app/mcp_server.py:1421-1427` | row 4 names `log`, kind lifts | C5a adds "When the person says same as last time, send last's sets as given."; C5b rewrites it, naming `log_lifts`, `log_cardio` and `log_with` | SL, DS | C5 writes its text in `log` words: one `log` call of kind lifts for the lifts, holds and carries, one `log` of kind cardio for each cardio item, `exercise` as `movement`, `minutes` and `distance_km` in the part, the rest in `details` |
| `log_with` (C5b) | 50: no pointer to a function a list lacks | values `log_lifts`, `log_cardio` | DS, SQ | Gives the kind word, `lifts` or `cardio` (N13) |
| C5a's left-out line in the `get_routines` summary | text both lists read names no function | "Rowing Machine is left out: log it with log_cardio." | SQ | Reworded to name no function, such as "log it as cardio", with its pins |
| `save_routine`'s refusal, `app/services/routines.py:140` | row 16: "Log its sets instead." | C5b removes it (holds are saved) | SL | C5b deletes this item's line |
| Time and distance forms | 51: every part takes each form | a routine hold takes only `time_s`, a carry only `distance_m`, cardio only `minutes` and km or miles | DS, open | C5 settles whether `save_routine` takes the other forms or the assistant converts |
| `log_cardio`'s field words | L0's named constants | C5b's routine cardio fields take `log_cardio`'s words | SC | C5b imports L0's constants |
| `docs/can-and-cant.md:360` | row 24 | C5b rewrites the section | SL, SQ | Names no log function |

**Kind words and entries (C5)**

| Where (`main`) | This item | C5 | Kind | On the rebase |
|---|---|---|---|---|
| `KIND_DESCRIPTION`, `RESTORE_KIND_DESCRIPTION`, `DELETE_KIND_DESCRIPTION`, `ENTRY_ID_DESCRIPTION`, `RESTORE_ID_DESCRIPTION`, `app/mcp_server.py:293-302` | 52's words; row 3 | C5c adds `program` and its ids | SL, SC | C5c appends to this item's constants |
| The kind check, `app/services/entries.py:130-148`, `:468-479` | the kind map at the door | C5c adds `program` | SL | `program` after the map; its "Use one of" list then shows the map's words too |
| `calllog.TABLES`, `app/calllog.py:69` | the row keeps the table word | C5c adds `program` | SC | `program` is its own table word |
| `KIND_LABELS`, `ENTRY_OR_FOOD`, `app/services/common.py:95`, `:106-115` | row 11 | C5c adds `program` | SL | A line merge |

**Shared lists and the call log (C3 and C5)**

| Where (`main`) | This item | C3 or C5 | Kind | On the rebase |
|---|---|---|---|---|
| `WRITE_FUNCTIONS`, `app/services/common.py:120-123` | adds `log` | C5c adds `save_program` | SC, CT | 16; pin at `tests/test_sixteen_weeks.py:299` |
| `NEEDS`, `app/mcp_server.py:257-269` | adds `log`, `guide` | C5c adds `save_program` | SC, CT | Both lines in the dict |
| `KNOWN_NAMES`, `app/calllog.py:73-90` | `parts`, `details`, `movement`, `LogPartIn` | C3 and C5 add names and models | SL, SC | One hand merge of the set and the model tuple |
| `tests/test_trace.py:226-234` | published names plus the five's registered schemas equal `KNOWN_NAMES` | C3's food fields are published only in the hidden `log_food` | SC | C3 rebases onto this form; its fields are in `log_food`'s registered schema |
| `ITEM_FIELDS`, `NEAR_MISSES` | `log`'s rows | C5b rewords `routines.LIFT_FIELDS_TEXT` | SC | A line merge |
| The homepage count, `app/web.py:1325` | counts `NEEDS` less the owner's and `RETIRING` | C5c +1 | CT | 22 |
| `FUNCTIONS`, `tests/test_mcp.py:253`; `tests/test_progress.py:453` | `log` and `guide` where `log_lifts` stood | C5c `save_program` after `get_routines` | SL | A line merge |

**Published pins and tests (C3 and C5)**

| Where (`main`) | This item | C3 or C5 | Kind | On the rebase |
|---|---|---|---|---|
| `PUBLISHED`, `tests/test_feedback.py:69-92`; `tests/test_units.py`; `tests/test_mcp.py` | moves the rows of L2 and adds `log`, `guide`; the five read from the registered tools | C3: `log_food`, `save_food`, `find_food`, `edit_entry`, `update_settings`. C5: `get_routines`, `save_routine`, `delete_entry`, `restore_entry`, `save_program` | SC, CT | Recompute after the rebase, never merge by hand |
| "Only these entries differ": `tests/test_units.py:699`, `tests/test_routines.py:850-858`, `tests/test_inputs.py` | this item's set | each item's set | SC | Each rebase draws the union of the items landed |
| `docs/can-and-cant.md:460-484` | the five's rows become `log` and `guide`, with the old-chat line | C3: `save_food`'s row. C5c: `save_program`, routine rows | SL | A line merge; the 11 readers run after each join |
| Tests that call the five through `/mcp` | L6 moves them | C3 and C5 add more | CT | L6's list grows by theirs |

**Evals (C3 and C5)**

| Where (`main`) | This item | C3 or C5 | Kind | On the rebase |
|---|---|---|---|---|
| The judge, `evals/judge.py:500-626`, `:793-827`, `:916-919` | routes `log` calls for every check by name, `ok_calls` included | C5's `routine_same_as_last_time` uses `ok_calls` on `log_lifts`; C3's ask cases `ok_calls` 0 | DS | Their cases keep the five's names; the routing covers them. `inputs_include` on `log_cardio`'s `machine_kcal` and `avg_hr` and `inputs_forbid` on `new_session` read the routed paths |
| `evals/judge.py:158-162` seeds and tables | unchanged | C5c: `save_program`, `program`. C3: `_rows_text`, `item_count` | none | Different lines |
| `evals/run.py` | the two flags | C5c: `{{weekday:N}}`, `run.TABLES` | same file | Different functions |
| `evals/cases.json` tail and the case-order pins | 2 new cases; `log` in the 118 no-write lists | C3: 15 after these; C5: 8 after C3's | SL, CT | Order: this item, C3, C5. Each new no-write case lists `log` too |
| The coverage test, `tests/test_evals.py:1303-1317` | reads `NEEDS`; core 103 | C5c's `program_saved` in the core | SC, CT | 104 |
| `docs/evals.md` | "the core stays at 101" reads 103 | C5a's section says 101 | SQ | Each item's sections take the count after the rebase |

**Order and deploys**

| Point | This item | C3 or C5 | On the rebase |
|---|---|---|---|
| Migrations | none | C3 `0015`, C5c `0016` | No schema clash |
| Contract notes | this contract reaches `main` with this item | C3 and C5 each owe a note on it | Their notes wait for this item's merge |
| `tests/test_schema.py:164` and the migration head pins | none | C3 `0015` in 7 files; C5c `0016` and `program` | This item edits `test_feedback`, `test_switch`, `test_units`, `test_routines`, `test_edit_history` on other lines: a line merge |
| `tools/load.py:44`, `:138` | L6 moves its `log_food` and `log_lifts` calls | C3: whether its `log_food` body sends an estimate, which would then need `portion`, is not checked | Read at C3's build |
| Watts (C6) | `log_shape.py`'s cardio `details` fields | C6 adds watts | C6 adds it to the cardio `details` fields as well as to `log_cardio` |

### What each item does on its rebase

| Item | Steps |
|---|---|
| C3 | Edit `log_food`'s description where L0 put it, the named constant. Write the meal words onto row 7's string. Note `atwater=` through the room left. Read 4,084 for the instructions, 4,079 on its miss. Add its names to `KNOWN_NAMES` in this item's form of `tests/test_trace.py:226-234`. Send its estimate cases through `log` too, and expect its portion refusal through `log` as written. Recompute every `PUBLISHED` row. Its 15 cases go after this item's 2; core stays 103 |
| C5 | Write no text that names `log_lifts` or `log_cardio`. Reword C5a's left-out line and its pins. Keep C5a's `get_routines` sentence a separate sentence, so row 4 and it merge by line. Write `log_with` as the kind word and C5b's `get_routines` text in `log` words. Leave `app/services/routines.py:140` to C5b's own removal. Plan from this item's counts: 22 listed, 25 with the owner, 16 writes, core 104, its cases after C3's. Take `log_cardio`'s field words from L0's constants. Append C5c's kind text onto this item's constants. Recompute every `PUBLISHED` row |

## Known limits

| Limit | Why it stands |
|---|---|
| A chat opened before deploy 1 keeps the five and the old instructions | As every function change. How long each app keeps a chat's list: not checked |
| A client that lists again mid-chat would hold the new list with the old instructions | The old instructions name `log_measurement` (48's sentence), which that new list lacks; `log` is in it. Whether any app lists again: not checked |
| A crossed key is `key_reused`, and a new key could then write twice | It needs one key across two chats |
| A new chat pays one `guide` call before a kind's first details | Measured: about one call a run |
| The web's rows keep four of the five's names | The pages call the services, not `log` |

## Open points

His answers settle 2, 4, 7, 9 and 10 (decisions 51 to 54, N9) and 8 (N10). The rest are the lead's picks, told to him, standing unless he says otherwise; 11 and 12 are settled by numbers in the release eval.

| # | Question | Pick |
|---|---|---|
| 1 | The path: two deploys, hidden then removed; or a first deploy of the neutral words alone | Two |
| 2 | `guide` and `what_is_logged`'s menu | Apart: decided (53) |
| 3 | The 61 characters | The shorter Goals sentence, tested on four goal cases (24 of 24 on `gpt-6-luna`; over 15 runs of `lb_bench_goal`, 2 asked the year, against 0 of 15 on `main`); dropping the whole month-end sentence costs goals |
| 4 | Kind words in edit, delete and restore now, or with the change release | Now: decided (52) |
| 5 | The eval set: the judge routes `log` calls, or the cases move to `log` | The judge routes, a routed call answering to both names |
| 6 | `guide`'s row and the read limit | Both |
| 7 | The window for deploy 2, and `owner_calls`' filter | His window, set from the numbers; the filter: decided (54), with a caller filter and a view across accounts |
| 8 | `alwaysLoad` on `log` and `guide` | Decided (N10): only as the fallback, if the release run dips |
| 9 | A part's time and distance | Decided (51), converted in `route` |
| 10 | The core | Decided (N9): 103 |
| 11 | Where `log`'s routing sentence sits | Settled by numbers in the release eval. Before the lift sentence, as the rig tested, pushes "Spotter picks the lift" past the first 512 characters; with 48's sentence there, `gpt-6-luna` split "deadlifts ... then bench ..." into two calls in 4 of 9 runs (0 of 9 on `main`), the record right each time. 48 went live after it instead (the split 1 of 6). Start after; if the plain-word lift cases or `mixed_unit_session` dip on `gpt-6-luna`, before |
| 12 | Whether deploy 1 keeps a sleep route once 48's sentence goes | Measured in the release eval: `sleep_record` on `gpt-6-luna`. Arm D, with `log`'s kind words and no sleep sentence, had it right 11 of 12 before C4. Under 10 of 12, a sleep sentence goes back. Only 12 characters are spare after all three items, so it needs a cut, which the lead brings to the owner with numbers (N15) |
| 13 | How a part is named in a refusal | Two wordings stand: "Part 2" from `misfit` (`ITEM_WORDS`), `parts[1]` from `said_as_sent` and `route`, as tested. The L1 builder prints one of each in its report; one wording is a later pick, with the release eval's misses read first |
| 14 | `guide`'s result | A small `GuideResult` with a `RESULTS` row; only `log` is exempt from the output-schema tests |
| 15 | Where the coverage test and the two new cases land | In L1, with `log_plank_minutes`'s `must` as `log` alone, so the tree is green before L5 routes |
| 16 | `guide_how_food`'s "no number of its own" | A `reply_lacks_pattern` on a kcal figure, shown against the pass and fail runs |

## Dated notes owed

Each a dated note on its contract, not a rewrite.

| Contract | Note |
|---|---|
| `function-list.md` | R12: `log_food` and `log_lifts` leave the list, `alwaysLoad` is the fallback after the release run. R13: the routing sentence names `log`. G5's "hidden and answering `retired`": the five answer as before, with no pointer |
| `idempotency.md` | `log` takes `key` under its own name; a key crossed with one of the five is `key_reused` |
| `inputs.md` | `log`'s refusals name `log` and each field as sent; `ITEM_WORDS` has a word for a part |
| `trace.md` | `log`'s and `guide`'s notes; `parts`, `details`, `movement` in `KNOWN_NAMES`; `owner_calls`' new fields |
| `routines.md` | `get_routines`' sentence names `log` |
| `exercise-list.md` | `log_lifts`' fields reach a new chat as a lift part |
| `data-on-request.md` | the saved food id's meaning. `tests/test_data.py:277` reads this contract: whether it pins that meaning, not checked |
| `switch.md` | `log` and `guide` in the table of what each setting allows |

## Not in this work

Change, delete, restore, saves and finds through the log's shape. A race's ordered parts. Rounds. New kinds. The comparison rig's arms after the build.

## Notes from the build, 2026-11-22

Built as L0 to L5 and one fix round after the review. Where the build departed from the text above or added to it, the build is the record:

| Point | The contract | As built |
|---|---|---|
| The hi page | L1's tests: "`hi.html` holds no whole-word "log" or "guide" today, by grep" | The template holds none, but the drawn page does, from `app/web.py`'s strings: "A fitness log", "log my push day", "Read and log", "Read, log and delete". `tests/test_hi_there.py` matches whole words and takes `log` and `guide` as plain words (N8) |
| `tried` | `kind parts[3]{exact,movement,reps,weight_kg} details{new_session}` | Kept in its sorted order: `details{new_session} kind notes parts[3]{exact,movement,reps,weight_kg}` |
| The without-Spotter cases | `log` in each write list of `evals/without_cases.json` | U1's write lists leave `log_food` out on purpose, so `log` was not added to them |
| L4's files | `app/services/owner.py`, `app/mcp_server.py` | Also `app/calllog.py` (`function`, `caller` and `all_accounts` in `KNOWN_NAMES`) and `app/mcp_schemas.py` (`OwnerAccountCall`, with an optional `account_id`), and the hash pins in `tests/test_inputs.py`, `tests/test_routines.py` and `tests/test_units.py` |
| Row 11 | "the id that logging it returned, or one from get_log" | A food entry's fix reads "an id that logging it returned", as a food call returns several ids |
| Rows 6 to 8 | Part of each string | The full strings: "One food entry. There is no tag field: Spotter works out who supplied the number."; "The saved food's id. Log it as saved_food_id, with servings."; "The USDA id. Log it as usda_fdc_id, with grams." |
| 52's kind words | Both word sets named | `KIND_DESCRIPTION` reads "lift_session or lifts, bodyweight, measurement, food_entry or food, cardio_entry or cardio." |
| "Spotter picks the lift" | Ends at 497 | Ends at 497 counting its full stop |
| `distance_mi`'s words | "... or with no unit and units.distance is mi. Spotter converts it." | L1a dropped the doubled "Spotter converts it." from this contract's table (`6602077`), as the line after the table adds it to each field |
| The logging cases | "What exists": 125 with a write in `must` | 126 with `log_plank_minutes`, as pass 1 of the release eval counts them |
| The judge | Routes `log` calls | Also reads the kind of `edit_entry`, `delete_entry` and `restore_entry` through `log_shape.table_word`, so either word passes the 8 cases that pin a table word |
| `--transcripts` | `--verbose` on the CLI, each turn's output kept, masked | Also turns on `--keep`, and kept Claude output is masked for anything shaped like a token |
| The fix round | Not in the text above | A null on a field the kind does not take is read as left out; `measurement` takes a measurement's own word inside `details`; a refused time or distance that `log` converted names the field and the value as sent. These go beyond what the rig tested: the release eval is their first test on a model |
| Open point 13 | Two wordings for a part | A schema refusal says "Part 2: reps must be a whole number, not 'five'."; a service refusal says "parts[1]: reps 500 is outside 1 to 100." Both stand |

## Note, 2026-11-22: C3 (macros-and-asking.md)

| Point | As built |
|---|---|
| `log`'s food kind | `details.entries` is `FoodEntryIn` as it stands, so `portion`, `protein_g`, `carbs_g`, `fat_g` and `macros_estimated` reach `services.log_food` through `log_food_step`, as from `log_food` |
| The portion refusal through `log` | Said as sent: "details.entries[1]: portion is missing for an estimate.", its fix "Ask the person once how much they had, then send it as portion in details.entries[1], in their words. Nothing was logged." Through `log_food` it reads "Entry 2". `tests/test_log_and_guide.py` holds both in its "said as sent" lists |
| `guide`'s food topic | `LOG_FOOD_DESCRIPTION` gains "Send protein_g, carbs_g and fat_g when the person gave them or you can work them out; an estimate needs its portion.", and the topic carries the new fields' words: `GUIDE_SIZES["food"]` from 3,626 to 4,885. Topic `log` reads only the description's first sentence and stays 1,894 |
| The cases | C3's eleven keep the five's names, and the judge routes a `log` call to its kind's function. Each that asks for no write through `log` lists `log` in `must_not` and `no_call`, but for the four ask cases. Those (`guessed_number`, `portion_asked`, `lift_weight_asked`, `lift_reps_asked`) keep `log` in `may` with `ok_calls` 0, on the owner's pick of 2026-11-23: a `log` call Spotter refuses before the question passes, a written one fails |

## Note, 2026-11-24: L6 as built (deploy 2)

Built on `81da59f`, after C3 and C5c. The owner's go: 2026-11-23. The call log read that day with `owner_calls`: no call of the five with caller `claude` in 72 hours, and no assistant call of any function since 2026-11-21 but the reads themselves, so the window shows no one who breaks; a chat opened before deploy 1 is the owner's own.

| Point | The text above | As built |
|---|---|---|
| The five | Registrations and `NEEDS` names removed, `RETIRING` gone | So. A call of one by name answers "Unknown tool: log_lifts", `isError` true, no `structuredContent`, no call row, no key, no entry |
| What a new chat reads | The same | The function list for the owner and for everyone else, the instructions (4,084 characters), each `guide` topic and `whoami` are the same bytes as at `81da59f`. `spotter://can-and-cant` loses row 25's line and nothing else |
| The homepage count | "What changes" says 21 | 22 since C5c's `save_program`, before and after: 30 less the owner's 3 less the 5, now 25 less the owner's 3 |
| Files | L6's row | Also `app/web.py`: `FUNCTION_COUNT` read `RETIRING`. `app/mcp_server.py` no longer imports the five's 33 description words and 5 result models; they stay in `app/mcp_schemas.py`, where `guide` and `log` read them. Comments in `app/mcp_schemas.py` and the docstring of `app/guide.py` made true |
| `ITEM_FIELDS[("log_lifts", "sets")]` | Not named | Kept. `log` checks a part against `LogPartIn` before the set model, so no `/mcp` call was found that reaches the row |
| `owner_calls`' `function` words | Not in L6's row | Kept as "such as log_lifts": the rows of chats before deploy 1 and the web's rows carry the five's names |
| `logged_by`'s words on a lift session | Not named | In no published schema: `log` publishes no output schema (N6), and `Session` was published only by `log_lifts`. True for a new chat since deploy 1 |
| `tools/load.py` | Its `log_food` and `log_lifts` calls move | Two lines of its table, `log:food` and `log:lifts`, both sent as `log` |
| The tests | Every test that calls the five through `/mcp`: to `log`, or to the services | 26 test files. Each call moved to `log` in `log`'s own shape, with no helper that maps the five's arguments onto it; the asserts on rows, numbers and error codes stand. A refusal's words follow `log`'s, as decided: `details.kg`, `details.entries[0]`, "Part 1", `parts[0]`. A call row is `log`'s, its notes starting `kind=lifts word=lifts`. A key is kept under `log` |
| The five's own argument checks | Not named | `misfit_model` on each kind's model pinned for 35 cases to the answers the registered five gave at `81da59f`, the same bytes. Where `log` refuses a case first in its own words, the kind's model is tested directly and `log`'s answer is pinned beside it |
| "Through `log` and through its kind's function" | Not named | The second side runs the kind's argument model and its step in process. For a refusal by the kind's own model the two sides share that code, so those comparisons check the routing and the wording as sent |
| The golden cases' made-up runs | Not named | Each sends `log`. `evals/cases.json` is unchanged and keeps the five's names: the judge reads a `log` call as its kind's function. Every run's judge reasons compared before and after: no failing run passes, no passing run fails; 17 failing runs gain the reason that `log` was called. Seven runs removed: six that became copies of another, and `log_plank_minutes`' right row not sent through `log`, which can no longer happen |
| The coverage test | Its allowed names gain the five | So, as `log_shape.LOG_FUNCTIONS` |
| New pins | Not named | A call of each of the five by name is the unknown function and writes nothing. Every string field of each kind carries a marker in the log-leak test. The every-field calls cover each kind's optional fields. A hash of each `guide` topic's answer beside its size. The `seconds` key in a part pinned as `log` answers it |
| Test ids | Not named | 7,945 before, 7,938 after: `tests/test_inputs.py` holds 8 fewer (the five's schema rows and true-or-false rows became `log`'s), the old-chat portion test went with its two asserts moved into the `log` one, and two came in (the marker table, the `guide` hashes). The rest are renames |

Found and left, not changed here: an empty `parts` list is taken on kinds bodyweight, measurement and food (`app/log_shape.py`, since deploy 1); a measurement kind holding a control character comes back with it in the refusal; `tests/test_macros.py` leaves call-log notes that fail one test of `tests/test_hand_check.py` when the two run in the other order.

The eval: `python -m evals.need main` says yes for `spotter://can-and-cant` alone. A run cannot read a resource, so no case measures the line that went. Six logging cases, one or two a kind, ran 3 times each on both gate models as a check of the runner and the judge on a tree without the five: `BUILD_LOG.md`.
