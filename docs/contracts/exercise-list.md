# Exercise list contract: every lift by its own name, a plain word taken as the person means it

**Goal: Spotter knows every lift a gym-goer does, Hyrox's movements among them, by a stable key. A plain word such as "squat" or "bench" is logged as the lift the person means, from their own history. A set can be a hold or a carry, up to 100 reps, or a lift with help. Volume counts both hands and both sides. A dumbbell total sent as one hand's weight is caught by one question, never by a changed number.**

The design was reviewed with the owner on 2026-11-19, point by point, and every point is decided (below). This contract waits for his yes before the build. Read against `main` at `2aec4ab` on 2026-11-19. The builder reads each "What exists" row again on the commit it starts from.

## His words

On the list: "cover literally every possible lift ... related movements".

On a plain word: "If there is no mention specifically, just assume that backsquat or like free bar backsquat or whatever version is being used. But if somebody has used Smith in the past, then verify."

On the sets logged so far: "that's already logged, basically need to go." He ruled that every one goes, for every account, and that no sample is recreated. And: "people need to see only their data".

On a dumbbell total that may be a pair: "can we use their normal bench numbers and figure it out by some sort of normalization?" His pick was the link and the one question in decision 32.

On brand names: "Let's see it as it comes up."

## Sources

| Source | What was read | When |
|---|---|---|
| free-exercise-db (github.com/yuhonas/free-exercise-db, The Unlicense) | `dist/exercises.json` at commit `f00c92c`: 876 entries, 17 muscle names. The 40 live lifts with an entry hold exactly the entry and muscles `app/exercises.py` holds today. Suitcase Carry has none | Fetched 2026-11-18 |
| 2024 Adult Compendium of Physical Activities (pacompendium.com, CC BY-NC-ND 4.0) | The rows behind the 10 new cardio activities. 10 of the 15 live rows were read again and matched `app/exercises.py` in MET and code | 2026-11-19 |
| Hyrox (hyrox.com/the-fitness-race) | 8 runs of 1 km, each followed by a station: Ski Erg 1,000 m, Sled Push 50 m, Sled Pull 50 m, Burpee Broad Jumps 80 m, Rowing 1,000 m, Farmers Carry 200 m, Sandbag Lunges 100 m, Wall Balls 100 reps. The rulebook was not read | 2026-11-19 |

No other exercise database was used. The JSON is not put in the repo (R17).

## Decisions

The owner's, 2026-11-19. Not open to change here.

| # | Decision |
|---|---|
| 1 | Only Smith Squat logged: "squat" logs Smith Squat, named in the reply. Ask only when two or more of a word's lifts are logged |
| 2 | When logging, the 18 names that are a word's default ("bench press") also take the history step |
| 3 | No kept choice per person now. Only if asking nags |
| 4 | A read with a plain word takes every candidate the person logged, and never asks |
| 5 | Volume of sets already logged: moot, they are deleted (decision 35). The volume rule still changes |
| 6 | The live plain names mean what their contract entries say |
| 7 | Barbell is left out of 19 names, as the list has them |
| 8 | Machines named as themselves keep their names: 22 |
| 9 | Assisted Pull-Up and Assisted Dip are their own lifts, help as a minus weight |
| 10 | Landmine counts the plates only. Trap bar and EZ bar count bar and plates. Two cables or two kettlebells are a pair. "40 a side" and "80" are one load. A band set stores 0 kg |
| 11 | Near entries stay: 43 lifts take their muscles from another entry, Burpee's (Mountain Climbers) among them |
| 12 | Dead Hang: forearms primary at 1, lats secondary at 0.5, source `ours` (no entry fits). The standard rule, no weights of its own (the owner's change of 2026-11-19, after the review) |
| 13 | Cardio on a nearest Compendium row: as marked |
| 14 | Sleds are lifts: weight and metres |
| 15 | Category: accessory is single-joint work; 9 lifts keep their movement's pattern |
| 16 | `list_exercises` with no search: the person's own logged names, plus the 46 default lifts behind the plain words, the count, and a line to search |
| 17 | The exercises resource stays whole |
| 18 | `get_data` takes a lift's family as a filter and a group |
| 19 | The reps limit is 100, for Hyrox's wall balls |
| 20 | Holds and carries logged as reps: moot, deleted with the rest |
| 21 | The per-hand check's reach is replaced by `compare_with` (decision 32) |
| 22 | Brand names stay in aliases. Spin Class and Ski Erg stay as named |
| 23 | Ski Erg stays at 10.5 MET (its row 02082 reads "slow to moderate speed"). Battle Ropes, Curved Treadmill Walk and Incline Treadmill Walk stay as marked |
| 24 | Box Jump and Burpee are added |
| 25 | Cable Hip Adduction takes the adductor machine's entry |
| 26 | Region by the main muscle; every carry and sled full body |
| 27 | Bests: an assisted lift takes the least help (-20 beats -30), then the most reps; a hold, the longest time at a weight; a carry, the heaviest weight over its distance; a band lift, the most reps |
| 28 | A plain word outside logging: a read takes every candidate logged; a goal, a routine, an edit or an import asks which; a default's own name is exact there |
| 29 | Box Jump: a bodyweight lift in reps, its own family in squat, from entry `Front_Box_Jump`. Aliases: front box jump, plyo box jump, plyometric box jump |
| 30 | Burpee: a bodyweight lift in reps, its own family in squat, muscles from `Mountain_Climbers` (quadriceps 1; chest, hamstrings, shoulders 0.5). Aliases: full burpee, chest to floor burpee |
| 31 | "box jumps" and "burpees" reach their rows by the plural rule. "burpee jump" and "hyrox burpees" stay on Burpee Broad Jump. No HIIT word holds burpee |
| 32 | The per-hand check: when a pair total is above the person's best on the bar lift it is compared with, the reply asks once "50 a hand, or 25 a hand?". The number is never changed. Every pair ratio is logged, so a tighter rule can come from real numbers. No fixed ratio |
| 33 | `compare_with` links a family with a per-hand lift and no bar lift to its barbell counterpart's family: 4 links |
| 34 | No level between family and category |
| 35 | Every lift set on live, for every account, is deleted by a one-off step in this item's deploy. Food, weight and cardio stay. No sample is recreated |
| 36 | Gym cardio is in; yoga and Pilates are later; no cardio on the body map |
| 37 | A dumbbell weight is one hand's; a dumbbell lift's volume counts both hands |

## What exists today

| Piece | State | Where |
|---|---|---|
| The list | 41 lifts `(name, category)` and 15 cardio `(name, category, MET, code)`, as tuples. Muscles and region for the 41 in `_SOURCE`, `MUSCLES`, `REGIONS`; the two carries full body by name | `app/exercises.py:20-62`, `:64-129`, `:136-154` |
| Sync | By name: adds what is missing, brings a cardio row's MET and code up to the list, never renames. `Exercise(**row)` from `rows()` | `app/exercises.py:158-183` |
| `Exercise` | `id`, `name` (String 64, unique), `kind`, `category`, `met`, `compendium_code` | `app/models.py:148-158` |
| `LiftSet` | `weight_kg` and `reps`, both NOT NULL; unique `(session_id, exercise_id, set_number)` | `app/models.py:179-197` |
| The database's rules on a set | `ck_lift_set_weight_kg` 0 to 1,000; `ck_lift_set_reps` 1 to 1,000; `ck_lift_set_set_number` 1 to 1,000. The rules' text lives only in the migration | `migrations/versions/0012_hardening.py:55-57` |
| What links to `lift_set` and `lift_session` | `lift_set.session_id` (ON DELETE CASCADE), `lift_set.exercise_id`; `entry_version` rows of kind `lift_session` hold the session and its sets as JSON, `[exercise id, set number, weight, reps]`; `write_key` keeps a write's first answer for 24 hours. A deleted session is soft: `restore_entry` clears `deleted_at`. `goal.exercise_id` and the routines' JSON name lifts, never a set. `call_log` holds no set | `app/models.py:189-190`, `app/services/common.py:487-497`, `:534-549`, `app/services/entries.py:269-306`, `app/calllog.py:59` |
| A set's limits | `HARD_RULES` reps 1 to 30, weight 0 to 500 kg; 1 to 60 sets a session | `app/rules.py:46-50` |
| `check_session` | Shared by `log_lifts`, `edit_entry`'s sets and the import | `app/rules.py:492-549`; callers `app/services/lifts.py:356`, `app/services/entries.py:421`, `app/services/imports.py:333` |
| Matching | Casefold and spaces, exact; else up to 3 close names by `difflib`. `unknown_exercise`'s fix: "Use a name exactly as list_exercises gives it." | `app/rules.py:331-357` |
| Other matcher callers | cardio, goals, progress, routines, the import's closest names, `get_data`'s filters | `app/services/cardio.py:126-135`, `goals.py:577`, `:600`, `progress.py:150-155`, `routines.py:112`, `imports.py:690`, `fields.py:212-215` |
| The duplicate key | The set list as `(exercise id, set number, weight, reps)` | `app/services/lifts.py:135-151` |
| The summary | "Back Squat 3 × 5 at 80 kg": weight and reps only | `app/services/lifts.py:105-124` |
| Bests | Epley none at 0 kg or below, or outside 1 to 10 reps; the heaviest weight counts only weights above 0; a lift never above 0 kg gets "most reps" on the pages only (`reps_only`) | `app/metrics.py:122-132`, `:159-178`, `app/services/lifts.py:159-233` |
| Volume | `weight x reps` per set, per session and per day; a minus weight would count minus | `app/services/fields.py:409-420`, `:533-534`, `:630-631` |
| Muscles in `get_data` | "Null for Suitcase Carry" in the field's meaning | `app/services/fields.py:596-600` |
| `list_exercises` | Every row, or by kind or category. Takes no account. Also feeds the exercises resource and the pages' lift slugs | `app/services/account.py:362-377`, `app/mcp_server.py:808-824`, `:1561-1568`, `app/web.py:2661-2663` |
| `common.exercises()` | Every row of a kind, by id: what every matcher reads | `app/services/common.py:459-463` |
| The instructions | 4,011 of 4,096 characters (printed on this tree). The sentence "Use exercise names exactly as list_exercises gives them." is 57 characters with its space | `app/mcp_server.py:205-230`, `:211` |
| Pins on the instructions | `== 4011` in 8 files, and the hash `INSTRUCTIONS_AS_BUILT` | `tests/test_data.py:1095`, `test_edit_history.py:681`, `test_hi_there.py:1232`, `test_idempotency.py:736`, `test_inputs.py:132`, `test_routines.py:855`, `test_switch.py:571`, `test_units.py:705`, `tests/test_feedback.py:92` |
| Published results with `reps` required | `SetOut.reps` (in `log_lifts`' session and `get_log`'s sessions); `BestSet.reps`; `SavedRoutineLift.reps`, `LiftToLog.reps` | `app/mcp_schemas.py:467-471`, `:482`, `:989`, `:780-784`, `:1405`, `:1433` |
| The reps limit in text | `SetIn.reps`, `ROUTINE_REPS`, `ImportSetIn.reps`: "1 to 30" | `app/mcp_schemas.py:136`, `:142`, `:207` |
| The call log's notes | Fixed words, `key=word`, at most 128 characters; one word a key, a later note on the same key replaces it; rows kept 90 days | `app/calllog.py:12`, `:57-58`, `:242-246` |
| The /hi page | "41 lifts by name"; "A name not on the list is refused, never guessed." | `app/templates/hi.html:64`, `:79`, `app/web.py:1349` |
| `docs/can-and-cant.md` | The sets are "exercise, weight, reps"; names "exactly as `list_exercises` gives them"; the "Squat (Barbell)" example, whose closest name today is Barbell Row; muscles and region | `docs/can-and-cant.md:8`, `:24-25`, `:232-234`, `:376-379` |
| Eval cases | 235 cases, 101 core. 13 cases check `sets[].exercise` on a `log_lifts` call by a list name. The judge's rows give a session's `set_count`, never its lifts. `pushups_over_30` expects a refusal at 30 | `evals/cases.json`, `evals/judge.py:85-92`, `evals/run.py:358-382` |

## What changes

| For a person | Today | After |
|---|---|---|
| The list | 41 lifts, 15 cardio | 242 lifts in 153 families, 25 cardio, 61 plain words, 769 aliases (the sheet on 2026-11-19, after a sweep of equipment words before each plain word; `tests/test_exercise_list.py` holds the count). The 56 live names stay |
| "squat", "bench", "curl" | Refused unless the name is exact | Logged as the default, or as the one variant the person has logged; asked when two or more are logged |
| A plank, a dead hang, a farmer's carry, a sled push | Not loggable, or logged as reps | A set of time, or of distance and time |
| 100 wall balls | Refused past 30 | Up to 100 reps a set |
| An assisted pull-up | Not loggable | Help as a minus weight |
| Dumbbells | The weight's meaning was not said | One hand's weight; volume counts both hands; the reply says "25 kg each" |
| A pair total sent as one hand | Taken | Taken as sent, and the reply asks once when it is above the person's best on the bar lift |
| Volume | weight x reps | x 2 for a pair, x 2 for one side at a time; none for a set of time or distance |
| The logged lift sets | Kept | Deleted, for every account, once |

## The design

Stated here in full. The sheet's own files are not in the repo.

### A row of the list

| Attribute | Values | What reads it |
|---|---|---|
| `key` | lower snake, at most 64 characters, unique. The rule: the name lowercased, apostrophes dropped, "&" as "and", each of `- / ( ) , .` and spaces as one underscore. Every key on the sheet follows it | sync, the database, every later table that points at a lift |
| `name` | at most 64 plain characters | every answer and page |
| `kind` | `lift`, `cardio` | listings |
| `category` | lifts: squat, hinge, push, pull, carry, core, accessory; cardio: walk, run, cycle, row, swim, machine, other. Unchanged in name and values | `list_exercises` and `get_data` filters |
| `family` | the key of the family's default lift | the word step, the per-hand check, `get_data`'s family |
| `compare_with` | a family key, or none | the per-hand check |
| `equipment` | barbell, dumbbell, kettlebell, cable, machine, plate_loaded, smith, trap_bar, ez_bar, landmine, band, bodyweight, plate, sled, medicine_ball, sandbag, other | the search, the per-hand check |
| `load_type` | `bar_total`, `per_hand`, `single`, `bodyweight`, `band` | volume, the reply's words, the per-hand check |
| `sides` | `both`, `each` | volume, the reply's words |
| `measures` | from one list for every kind: weight, reps, time, distance, speed, pace, incline, watts, level, floors, rounds. Lifts on the sheet: 228 `weight reps`, 7 `weight distance time`, 4 `weight time`, 3 `reps` | what a set takes and must have |
| muscles | primary and secondary, from the 17 names; a source `fedb` (its own entry), `fedb_near` (another entry, with a note) or `ours` (Dead Hang alone); the entry id | `get_data`'s muscle, region |
| aliases | each true of its lift, equal to no name, no plain word and no other alias | the matcher |
| `met`, `compendium_code`, the row's text | cardio only | the cardio estimate |

### Load types and sides

| Load type | The weight logged | Volume | Equipment it fits |
|---|---|---|---|
| `bar_total` | the bar and plates | weight x reps | barbell, EZ bar, trap bar |
| `per_hand` | one of two equal weights, one a hand | weight x 2 x reps | dumbbell, kettlebell, cable |
| `single` | one weight as said: one dumbbell or kettlebell, a stack's pin, plates on a machine, Smith, landmine or sled, a plate, a ball, a sandbag | weight x reps | any but barbell, bodyweight, band |
| `bodyweight` | the added weight; 0 for none; help as minus on the two assisted lifts | added x reps; 0 at 0 kg or below | bodyweight, and the two assisted machines |
| `band` | none, stored as 0 kg | none | band |

`sides` `each`: reps, time or distance a side, and volume x 2. The two stack: a Bulgarian split squat with 20 kg dumbbells for 10 a leg is 20 x 2 x 10 x 2 = 800 kg. Sandbag Lunge's metres cover both legs, so it is `both`. A set with no reps has no volume. Records and the estimated max stay on the weight as logged: one hand's weight for a pair.

### Families and plain words

A family is one movement at one angle, position and grip, on any equipment. It holds one lift per equipment, load type and sides, and one barbell lift. Its key is its default lift's key. 110 of the 153 are a single lift.

A plain word names several lifts: it has a default and its candidates, the lifts a person means by the word without naming equipment. For a barbell word those are the bar versions, barbell and Smith; for curls, flys, raises, rows and machine presses, every version. 18 of the 61 words are a default's own name, such as "bench press" and "pull-up".

When logging, the person's live sets among a word's candidates decide:

| History | Pick |
|---|---|
| None logged | The default, named in the reply |
| Only the default | The default |
| Only one other, such as Smith Squat | That one, named in the reply |
| Two or more | Ask before writing, each with its last date. Nothing is written |

### Matching order

1. **The key** (`rules.name_key`): casefold; no apostrophes or dots; "&" as "and"; hyphens and slashes as spaces; a plural "es" after ch, sh, ss, x or y off a word of more than 4 letters, else a final s (not ss) off a word of 3 letters or more; no spaces. "Name (Equipment)", as export files write it, tries the bracketed word at each place in the name, and takes the first place that reaches a name, an alias or a word: "Squat (Barbell)" reaches Back Squat.
2. **In `log_lifts` only:** a plain word (a default's own name too), then the history step above. A set sent with `exact` true skips this step (R3).
3. **The exact name.** Everywhere but `log_lifts` this comes first: an import, a goal, a routine, an edit and a read take a name as written, so an import of "Bench Press" rows never lands on Smith Bench Press.
4. **An alias.**
5. **Outside `log_lifts`, a plain word that is not a name.** A read takes every candidate the person logged, and never asks; with none logged, the default. A write is refused as `unknown_exercise`, the word's candidates as the closest names.
6. **`unknown_exercise`**, with up to 3 close names over names and aliases; an alias shows its lift's name.

Retired rows are never matched (R2).

### The per-hand check

On a `per_hand` set in `log_lifts`, Spotter takes the pair total (both hands) and the person's heaviest live set on one bar lift:

- in the lift's own family, its `bar_total` lift; where a family holds two, the barbell one (Deadlift beside Trap Bar Deadlift, Barbell Curl beside EZ Bar Curl, Barbell Shrug beside Trap Bar Shrug); where its only one is an EZ bar, that one;
- for a family with a per-hand lift and no `bar_total` lift, the family its `compare_with` names, by the same rule.

| Family (key) | `compare_with` | Compared with |
|---|---|---|
| `chest_supported_dumbbell_row` | `barbell_row` | Barbell Row |
| `dumbbell_arnold_press` | `overhead_press` | Overhead Press |
| `dumbbell_hammer_curl` | `dumbbell_curl` | Barbell Curl |
| `incline_dumbbell_curl` | `dumbbell_curl` | Barbell Curl |

Left with none, each with its reason in the list's test: Farmer's Carry, Dumbbell Fly, Incline Dumbbell Fly, Low Cable Crossover, Lateral Raise, Dumbbell Front Raise, Dumbbell Rear Delt Fly, Dumbbell Triceps Kickback, and Calf Raise's family: no bar version on the list.

When the pair total is above that heaviest set, the result carries `hand_check` and the reply asks once. With the bar lift never logged, no check. The ratio is logged in every case where the bar lift is logged (R12).

### Bests (decision 27)

| Lift | Kinds | A new best when |
|---|---|---|
| Weight and reps, any set above 0 kg | `estimated_max`, `weight` | as today |
| Assisted Pull-Up, Assisted Dip | `least_help`: value the weight (-20 beats -30); `reps`: value the reps | a weight above every earlier set's; else, at the best weight held so far, more reps than any earlier set at it |
| A band lift | `reps` | more reps than any earlier set |
| A hold (`weight time`) | `time`: value the seconds | a time above every earlier set's at the same or a heavier weight |
| A carry or sled (`weight distance time`) | `carry`: value the kg | a weight above every earlier set's at the same or a longer distance |

A first session sets no best, as today. A bodyweight lift at 0 kg keeps today's rule (`reps_only` on the pages, no best): so `tests/test_hardening.py`'s oracles, which hold Dip at 0 kg, stay equal.

### Muscles and region

Every lift gets its muscles in code, primary at 1 and secondary at 0.5 (R13 of `docs/contracts/data-on-request.md`, unchanged). Suitcase Carry gains muscles from the farmer's walk entry, as a near entry. Dead Hang has no entry and takes forearms 1, lats 0.5, source `ours`. Region by the primary muscle, as today; every lift in the carry category (the carries and the sleds) is full body. So Wall Ball and the thrusters read upper (their entries' primary is shoulders) and the jerks lower.

### Names

Angle or position, then grip, then equipment, then movement. Barbell is left out for Bench Press, Deadlift, Back Squat, Overhead Press, Hip Thrust, names built on them, the live names and 19 more. Flat presses say Bench Press with a bar or dumbbells and Chest Press on a machine; angled presses say Press after their equipment. One-side lifts say Single-Arm or Single-Leg.

## Rulings on the how

Picks made against the code, each with its reason. Those the owner may want to see again are also under "Open questions".

| # | Ruling | Why |
|---|---|---|
| R1 | **Where the data goes.** `app/exercise_rows.py`, new, data only: one row a movement (every column of the table above but the cardio row's text, which stays a comment, as today), and `WORDS`, each plain word with its default and candidates, by name. `app/exercises.py` keeps the closed lists, a frozen `Movement` dataclass, the views every reader uses (`LIFTS` as `(name, category)`, `CARDIO` as `(name, category, met, code)`, `MUSCLES`, `REGIONS`, `FREE_EXERCISE_DB`, `CATEGORIES`, `CARDIO_CATEGORIES`, `MUSCLE_NAMES`, `REGION_NAMES`, `REGION_OF`) and new ones (`BY_KEY`, `BY_NAME`, `FAMILIES`, `ALIASES`), built from the rows at import, and `sync`. `rows()` gives the five columns plus `key` | `fields.py`, `web.py` and five test files read today's views (`app/services/fields.py:450-451`, `:575-576`, `app/web.py:1349`); keeping their shape keeps them working. One home for the data, read in Python, as muscles are today |
| R2 | **Sync by key.** Adds a listed row whose key the table lacks; brings a held row's name, category, MET and code up to the list; sets `retired` on a row whose key is not listed, and clears it on one that is. A rename to a name another row holds stops the sync with one line, the two keys, before any change. Rows are never deleted. `common.exercises()`, every matcher, `list_exercises` and the resource leave retired rows out; a session's sets, `history` and the pages still read them, so the pages' lift slugs read every row, retired too (`all_exercises(retired=True)`, R8) | The database must know a row without its name. A retired lift keeps its history |
| R3 | **`exact` on a set, and a set model of `log_lifts`' own.** `SetIn` is also `edit_entry`'s `sets` (`app/mcp_server.py:1068`), where a plain word is refused (decision 28), and `:400` builds a refusal's "Use: ..." line from its fields. So `log_lifts` takes a new `LogSetIn`: `SetIn`'s fields with `exercise` in the person's words and `exact` (true or left out: the name goes as written, with no word step). `SetIn` keeps an exact name and gains `time_s`, `distance_m` and an optional `reps`, so an edit can fix a hold. `:400`'s table gets a row for `LogSetIn`; `calllog.py:88`'s tuple of input models gains it. The ask (R4) and `get_routines` both tell the assistant to send `exact`. Without it, a person with Bench Press and Smith Bench Press logged who answers "the barbell one" is asked again, since "Bench Press" is a word's default name (decision 2) | A loop in decision 2, found against the code: the answer to the question is itself a plain word. One model a meaning |
| R4 | **The ask.** A set whose word has two or more candidates logged refuses the whole call, nothing written, as `pick_lift`: message "Set 2: 'squat' could be Back Squat (last 2026-11-10) or Smith Squat (last 2026-11-17).", fix "Ask the person which, then send that name with exact true." It carries `candidates`, each `{name, last}`. With several such sets, one refusal a word. Its step in the call log is `check`, as every service code's | One question, before any write, as decision 1 says |
| R5 | **`check_session` takes a mode.** `logging=True` from `log_lifts` runs the word step with the person's history; `edit_entry` and the import call it as today and get step 3 first. The history read is one statement: the latest live session date of each candidate the call's words name | The three callers share it today |
| R6 | **What a set takes,** by its lift's `measures`: `weight reps` needs reps; `weight time` needs `time_s` and takes no reps; `weight distance time` needs `distance_m` and takes `time_s`; `reps` (a band) needs reps and takes no weight above 0 (none sent stores 0). A field the lift does not record is refused, `invalid_field`, "Set 1: Plank records time, not reps." / "Send time_s on set 1." Ranges: reps 1 to 100; `time_s` 1 to 3,600, whole; `distance_m` 1 to 5,000, 2 dp; weight 0 to 500 kg, and on Assisted Pull-Up and Assisted Dip -200 to 0 kg (help); a minus weight on any other lift is refused, "Set 1: a minus weight is help, on Assisted Pull-Up or Assisted Dip only." `weight_lb` follows by its factor | One rule a lift, read from the list. A set without what it records is not a set |
| R7 | **The two deploys** (below). `SetOut.reps` is the one required result field that can now be missing. `BestSet.reps` stays required: `best_set` is the Epley set, which always has reps. `SavedRoutineLift.reps` and `LiftToLog.reps` stay required: a routine takes only lifts that record reps (R10) | Read in the schemas: only `SetOut` carries a set that can lack reps |
| R8 | **`list_exercises` takes the account.** `services.list_exercises(user_id, kind, category, search)` for the function; `services.all_exercises(kind=None, retired=False)` for the resource (every row not retired, whatever the person logged) and `web.lift_names()` (with `retired=True`). With no search and no filter: the person's own logged lifts and activities, then the 46 lift defaults not already named, then the 25 cardio activities not already named, then `count` (every row listed, 267 on the sheet) and `note`. So a new account sees about 71 rows, cardio among them, as it sees every cardio activity today. The answer's size is not measured: today's 56 rows are 5,193 characters (measured with the design on 2026-11-19), and each row gains three fields; the builder measures the new one on an empty account and on a full one, for the hand-over and the build log. A kind or category with no search: every row of it, as today. A search: up to 10 rows whose key holds the search's key, over names, aliases and words, in this order: a name, an alias, a word's candidates; ties in list order. `count` is then how many matched | Decision 16 for the bare call. Without the cardio rows a new account would see no activity to log, where today it sees all 15. The filters keep their meaning, so "which squat lifts" still lists them all |
| R9 | **The one-off delete** is its own data-only migration, `0014`, in deploy 2 (below): every `lift_set` and every `lift_session` row, live and soft-deleted; every `entry_version` row of kind `lift_session`; every `write_key` row of function `log_lifts`. Lift goals, routines, call-log rows, food, weigh-ins, measurements and cardio stay | A session with no sets still counts as a session. A soft-deleted one would come back by `restore_entry`, and a kept version would put old sets back by `undo_edit`. A kept `log_lifts` answer would claim sets that are gone. On SQLite migrations run with foreign keys off (`docs/contracts/hardening.md` D16), so the sets are deleted by their own statement, not by the cascade. In deploy 2, not 1: sets logged between the deploys under the old rules (a plank as reps, a dumbbell weight of unknown meaning) go with the rest, so no set on live predates the new rules |
| R10 | **Routines and goals take only lifts that record reps.** `save_routine` refuses a hold, a carry or a sled: "Lift 2: Plank records time: a routine holds lifts of sets and reps." / "Log it with log_lifts." A lift goal refuses the same: "Plank records time: a lift goal needs a lift of weight and reps." / "Use a lift from list_exercises with kind lift." Routine reps go to 1 to 100 with `HARD_RULES`. Four live lifts stop recording reps: Plank and Side Plank (time), Farmer's Carry and Suitcase Carry (distance and time). A routine saved before with one of them keeps it; `get_routines` leaves it out of what to log, and its summary says so: "Plank is left out: it records time now. Ask the person how long, and log it with time_s." An open lift goal on one of them stays, and reads no figure | Timed items in a routine are item C5. An estimated max needs reps. `get_routines` would otherwise hand out reps that R6 refuses |
| R11 | **The import takes only lifts that record reps,** as today's fields. A row of a hold, a carry or a sled fails alone: "Row 3: Plank records time: an import takes sets of reps." / "Leave it out and tell the person." A minus weight follows R6 | Time and distance fields in the import are a later change, read against real exports first |
| R12 | **The pair ratio goes to the call log.** `calllog.note("pair", word)`, the word the call's ratios as `<exercise id>:<ratio>` joined by commas, one a per-hand lift in the call (its heaviest set's pair total over the compared lift's heaviest set, 2 dp), cut at a whole item to fit 128 characters. No new column. `owner_calls` already gives `notes` | The note is a figure worked from two of the person's weights, never a weight or an argument; the trace contract gets a dated note. Kept 90 days, as every row |
| R13 | **`hand_check`** is a new result field of `log_lifts`, one line a lift, present only when a check fires: "Dumbbell Bench Press: 50 kg each is 100 kg in both hands, above the heaviest Bench Press set logged, 80 kg. Ask the person once: 50 a hand, or 25 a hand? If 25, change it with edit_entry." No note once any earlier live set of the same lift had a pair total above the compared lift's heaviest set as it stands now: the person was asked then, and kept the weight | "Asks once": one question a lift, not one at every heavier weight |
| R14 | **The reply's words for a set** (`lifts.sets_text`, also `get_log`'s): a pair "3 × 10 at 25 kg each"; one side at a time "3 × 10 a side at 20 kg each"; help "3 × 8 with 20 kg help"; a hold "Plank 2 × 60 s", "Plank 60 s at 10 kg"; a carry "Farmer's Carry 2 × 40 m at 32 kg each in 45 s"; a band "Band Pull-Apart 3 × 15". Numbers through `formatting.show`, new quantities `seconds` and `metres` | The text rules: numbers as numbers, no needless decimals |
| R15 | **A version holds six items a set,** `[exercise id, set number, weight, reps, time_s, distance_m]`; `put_back` also reads the old four. The duplicate key covers time and distance. The export's `lift_sets.csv` gains `time_s` and `distance_m` after `reps` | No hold may read as another with the same weight and reps |
| R16 | **`distance_m` is metres for every account,** and `time_s` seconds. No feet or yards on a mi account; the column name says the unit, as `get_data` names each column | The units contract has no short-distance unit. A carry is said in metres in every source read |
| R16a | **The data is generated, not typed.** The C4a.2 builder writes `app/exercise_rows.py` with a script from the owner's reviewed sheet of 2026-11-19 (two tables: the rows and the plain words), handed to the builder. The script and the sheet stay outside the repo. After the build, a dump of the module back to the sheet's two tables must equal them, row for row and cell for cell, and the sheet's own check must pass on the dump. Both outputs go in the hand-over. This blocks C4a.2's sign-off. From then on the module is the list's one home, edited as code | 267 rows and 769 aliases typed by hand would drift from what the owner reviewed |
| R17 | **The list's test, not the sheet's script.** `tests/test_exercise_list.py` holds every rule the sheet's 20 checks held that needs no outside file: unique keys and names under the key, the key rule, plurals reaching their rows, aliases, words with their defaults first, equipment words in names, closed lists, load fitting equipment, measures fitting kind and load, families keyed by their default with one lift per equipment, load and sides, `compare_with` links and blanks with reasons, one bar lift to compare with, muscles from the 17 names with a source, Dead Hang's (forearms 1, lats 0.5), no cardio muscles, every MET with a five-digit code, names at most 64 plain characters with no dash but the hyphen, the Hyrox movements present, the said words of decision 31, and the 56 live names with their categories, METs and codes. The check of each lift's muscles against free-exercise-db's JSON is run once by the C4a.2 builder, outside the tests, and its count goes in the hand-over | Tests never touch the network, and the JSON is not ours to keep in the repo |
| R18 | **What `get_data` gains** (C4e): on `lift_sets`, `time_s` (unit s), `distance_m` (unit m) and `family` (filter and group; a cell is the family's default lift's name; a filter takes any lift of the family, or a word); `volume_kg` by the load rule. `exercise` and `family` filters take a plain word: every candidate the person logged. `exercise.key` and `exercise.retired` go into `INTERNAL_COLUMNS` | The tie test fails until each new column is a field or internal. Decision 18 |
| R19 | **`get_progress` with a plain word** (decision 4): one candidate logged, that lift in full; two or more, the every-lift form (`lifts`) with only those lifts; none, the default as a lift never logged | One result model, no question |

## Migrations `0013` and `0014`

The design put the delete in `0013`. It is split out here as `0014`, in deploy 2, for the reason in R9 (open question 1). Both run on SQLite and Postgres and are never edited once they have run on the host.

### `0013`: the columns and the rules (deploy 1)

`migrations/versions/0013_exercise_list.py`, follows `0012`.

| Step | What |
|---|---|
| 1 | `exercise.key`, String(64): added nullable, filled for every row by the key rule (held in the migration as a function, with the 56 live names written out), then NOT NULL and unique (`uq_exercise_key` by the convention). A row not among the 56 takes the key its name gives, and the migration prints its key, never more. Two rows with one key stop the upgrade, "0013 stopped: exercise key {key} 2 rows. Nothing was changed." |
| 2 | `exercise.retired`, Boolean, NOT NULL, server default false |
| 3 | `lift_set.time_s`, Integer, nullable; `lift_set.distance_m`, Float, nullable; `reps` nullable |
| 4 | Rules, each `ck_lift_set_<rule>`: `weight_kg` becomes -1,000 to 1,000 (dropped and added again); `time_s` 1 to 100,000; `distance_m` above 0, at most 100,000; `measure`: reps, `time_s` or `distance_m` is not null. `reps` keeps 1 to 1,000 (a null passes). The text lives in this file only (`docs/contracts/hardening.md` D18) |

On SQLite, `lift_set` and `exercise` are rebuilt by batch, foreign keys off (`migrations/env.py`), carrying every rule of `0012` on `lift_set`, its unique and its two indexes. On Postgres, `ALTER TABLE` adds each change.

**Down** stops, leaving the database at `0013`, with a count line in `0012`'s shape, when a set has no reps ("0013 down stopped: lift_set reps 3 rows. Nothing was changed.") or a minus weight. Otherwise it drops `time_s`, `distance_m`, `key`, `retired` and the new rules, makes `reps` NOT NULL and puts back `ck_lift_set_weight_kg` at 0 to 1,000. It does not remove the exercise rows sync added: older code lists every row of the table, 267 of them after deploy 2.

### `0014`: the one-off delete (deploy 2)

`migrations/versions/0014_lift_sets_cleared.py`, follows `0013`, data only. It deletes what R9 names, in this order: `lift_set`, `lift_session`, the `entry_version` rows of kind `lift_session`, the `write_key` rows of function `log_lifts`. It prints one line of counts, numbers only: "0014 deleted: 412 lift sets, 61 lift sessions, 18 versions, 2 keys." No schema change.

**Down** does nothing and restores nothing: the delete is one-way, by decision 35. **A down past `0014` and an up again runs the delete again,** and every lift set logged since goes too. So a down past `0014` on the host is the owner's call alone, said first.

### The two deploys

 Making `reps` optional in a published result binds `CLAUDE.md`'s rule: a client holding the old function list must never meet a missing `reps`.

| Deploy | Packages | What it changes |
|---|---|---|
| 1 | C4a.1 | `0013`; the models; the 56 keyed and synced by key; `SetOut.reps` optional and `time_s`, `distance_m` in `SetOut`. No set without reps can be written yet, and no set is deleted |
| 2 | C4a.2 to C4g | `0014`, the delete; everything else. A set of time or distance can now be written |

Deploy 2 goes no sooner than a day after deploy 1, so that fewer conversations begun before deploy 1 are still open. How long a client keeps a conversation's function list is not checked. The owner says when.

No other required result field or column is removed. `NewBest.kind` and `Best.kind` gain values, and `value` gains units, by description only.

## Model-read text

Each changes `python -m evals.need main` to yes. The builder prints each figure on the built server and puts it in the hand-over.

| Where | Today | After |
|---|---|---|
| The instructions, `app/mcp_server.py:211` | "Use exercise names exactly as list_exercises gives them." | "Log a lift in the person's own words, such as squat or bench: Spotter picks the lift. Use other names as list_exercises gives them." 132 characters with its space: the instructions become 4,086 (printed on this tree by replacing the sentence) |
| `IMPORT_RULE` | "Use names only from list_exercises." | Unchanged: the instructions have 10 characters left, and an import still takes exact names (decision 28) |
| `IMPORT_DESCRIPTION`, its names part (`app/mcp_server.py:142`) | "Names. Use exercise and activity names only from list_exercises, exactly as it gives them. When a name in the export is not on the list, ask the person once per name which listed name it is, or whether to leave it out." | "Names. Search list_exercises for each lift and activity the export names, and send the name it gives, exactly. When a name reaches nothing, ask the person once per name which listed name it is, or whether to leave it out." The rest of the part stays. The bare list is about 71 rows, so a lift outside the defaults is found by search, never guessed. It also gains: "A hold, a carry or a sled is refused: an import takes sets of reps." |
| `list_exercises`' description | "The exercise names Spotter knows. Every other function takes a name exactly as written here. Filter by kind or category, or leave both out for the whole list." | "Exercise names, with each lift's equipment and how its weight is logged. Left bare: the person's own, the usual lifts and every cardio activity, and how many Spotter knows. search finds the rest. log_lifts also takes the person's own words; every other function takes a name as written here." |
| `list_exercises`' `search` | none | "A word or two, such as row or hip thrust: up to 10 lifts and activities whose name, other names or plain words hold it." |
| `ExerciseItem` | name, kind, category, met, code | gains `equipment` ("A lift's equipment, such as dumbbell or machine; null for cardio."), `weight` ("How a lift's weight is logged: bar and plates, one hand's weight, one weight, added weight (help as minus), or none; null for cardio."), `records` ("What a set records: reps, time, or distance and time; for cardio, null.") |
| `ExercisesResult` | exercises | gains `count` ("How many exercises match, or with nothing sent, how many Spotter knows.") and `note` |
| `ExercisesResult.note`, bare | none | "The person's logged exercises, the usual lifts and every cardio activity, of {count}. Send search with a word, such as row, for the other lifts." |
| The exercises resource's description | "the same as list_exercises with no filter" | "Every exercise Spotter knows, as JSON, with its equipment and how its weight is logged." |
| `LogSetIn.exercise` (`log_lifts`' sets, R3) | `SetIn`'s: "The exercise name, exactly as list_exercises gives it." | "The lift in the person's own words, such as squat, or a name from list_exercises." |
| `LogSetIn.exact` | none | "true only for a name the person picked from Spotter's question, or a name from get_routines: it is taken as written." |
| `SetIn.exercise` (`edit_entry`'s sets) | as today | Unchanged: an edit takes a name as written |
| `SetIn` and `LogSetIn`, `weight_kg`, `weight_lb` | "Use 0 for bodyweight work." | adds: "For dumbbells, kettlebells or two cables, one hand's weight. Help on Assisted Pull-Up or Assisted Dip as a minus weight." |
| `SetIn` and `LogSetIn`, `reps` | "Reps done in this set, a whole number from 1 to 30." | "Reps in this set, a whole number from 1 to 100. Leave out for a hold or a carry." |
| `SetIn` and `LogSetIn`, `time_s`, `distance_m` | none | "A hold's or a carry's time in seconds, 1 to 3,600." / "A carry's or a sled's distance in metres, 1 to 5,000." |
| The near-miss line, `app/mcp_server.py:400` | "Use: exercise, weight_kg, weight_lb, reps." from `SetIn` | each model's own fields |
| `ROUTINE_REPS`, `ImportSetIn.reps` | "1 to 30" | "1 to 100" |
| `SetOut` | `reps` required | `reps` "Reps done, or null for a hold or a carry."; `time_s`, `distance_m` "or null"; `weight_kg` "Weight in kg: one hand's for a pair, minus for help, 0 for bodyweight work." |
| `NewBest.kind`, `Best.kind`, `value`, `previous` | "estimated_max or weight", "in kg" | kinds: estimated_max, weight, least_help, reps, time, carry; "kg, or reps for reps, or seconds for time" |
| `LogLiftsResult.hand_check` | none | "Present when a dumbbell or kettlebell weight may be both hands' total: ask the person once, as it says. The weight was logged as sent." |
| `log_lifts`' description | as today | adds: "Send the person's own word for a lift; Spotter picks it and the summary names it. pick_lift means two lifts fit: ask which." |
| `get_routines`' description | "To log one, send its sets with log_lifts, one item a set" | adds "with exact true" after "one item a set" |
| `pick_lift` | none | R4's words |
| `unknown_exercise`'s fix | "Use a name exactly as list_exercises gives it." | In `log_lifts`: "Ask the person which lift it is, or search list_exercises." Elsewhere, with a plain word: message "'squat' names more than one lift." and fix "Ask the person which: Back Squat or Smith Squat." |
| The refusals of R6, R10, R11 | none | their words as written there |
| `what_is_logged`'s meanings, `app/services/fields.py` | `muscle`: "Null for Suitcase Carry."; `volume_kg`: "weight x reps. A 0 kg set counts 0." | `muscle`: the sentence goes; `volume_kg`: "weight x reps, x 2 for a pair of weights, x 2 for one side at a time. None for a set of time or distance. 0 at 0 kg or below."; new `time_s`, `distance_m`, `family`; the session and day volume the same |
| `docs/can-and-cant.md:8` | "the sets (exercise, weight, reps)" | "the sets: exercise, weight, and reps, or a time, or a distance and time. A dumbbell weight is one hand's." |
| `docs/can-and-cant.md:24-25` | "Use them exactly as `list_exercises` gives them." | "A lift can be logged in the person's own words: Spotter takes the usual lift, or the one the person has logged, and asks when they have logged two. Every other function takes names as `list_exercises` gives them." |
| `docs/can-and-cant.md:232-234` | "for 'Squat (Barbell)' the closest is Barbell Row" | "'Squat (Barbell)' is read as Back Squat." |
| `docs/can-and-cant.md:376-379` | region and muscles | region: "full body for every carry and sled"; muscles: adds "Dead Hang's are Spotter's own: forearms and lats." Volume: one line with the load rule |
| `docs/can-and-cant.md`, the function table | `list_exercises`: "The exercise names to use." | "The exercise names, and a search." |
| `app/templates/hi.html:79` (a page) | "A name not on the list is refused, never guessed." | "A plain word such as squat takes the usual lift, or the one you have logged. A name Spotter does not know is refused, never guessed." |

The instructions stay under 4,096 and end with the plan_line sentence. The published entries that move, by `tests/test_feedback.py`'s `PUBLISHED`: deploy 1 `log_lifts`, `get_log`; deploy 2 also `list_exercises`, `edit_entry` (`SetIn`), `get_progress` (`Best`), `save_routine`, `get_routines`, `import_entries`. Every other entry byte for byte as before.

## Packages

Every pair of packages shares a file or a pin test (`app/mcp_schemas.py`, `app/services/lifts.py`, `app/rules.py`, `tests/test_feedback.py`'s `PUBLISHED`), so they build one after another, in this order, in one tree. None builds in parallel. Each builder runs its own test files, plain and with `-W error`. Existing test files change only where an assertion fails because of a decision here, each named in the hand-over.

| Package | Deploy | Effort | Files | Its tests |
|---|---|---|---|---|
| **C4a.1** The key and the migration | 1 | high | `migrations/versions/0013_exercise_list.py` (new), `app/models.py` (key, retired, time_s, distance_m, reps nullable, the module note), `app/exercises.py` (key in `rows()`, sync by key, retired), `app/services/common.py` (`exercises()` leaves retired out), `app/services/lifts.py` (`session_dict` gives `time_s` and `distance_m`), `app/mcp_schemas.py` (`SetOut`), `app/services/fields.py` (`INTERNAL_COLUMNS`: the four new columns) | `tests/test_exercise_migration.py` (new); `tests/test_schema.py`, `tests/test_hardening_schema.py` (the rule list gains the new rules), `tests/test_data.py:268`, `tests/test_feedback.py` (`log_lifts`, `get_log` hashes) |
| **C4a.2** The full list | 2 | high | `app/exercise_rows.py` (new, generated by a script from the owner's reviewed sheet of 2026-11-19, handed to the builder, with Dead Hang at lats 0.5; never typed by hand), `app/exercises.py` (R1, region by category carry), `app/templates/hi.html` (`:79`) | `tests/test_exercise_list.py` (new, R17); `tests/test_data.py` (`:249` the 41, `:272-293` the contract table: the 40 rows that still hold, Suitcase Carry by the dated note), `tests/test_schema.py` (`:186`, `:214`), `tests/test_progress.py:433`, `tests/test_cardio.py` (`:34`, `:60`, `:242`, `:425`, `:428`), `tests/test_hi_there.py` (`:95`, `:96`, `:182`) |
| **C4b** Sets of time and distance, and the delete | 2 | high | `migrations/versions/0014_lift_sets_cleared.py` (new), `app/rules.py` (R6, `HARD_RULES`), `app/mcp_schemas.py` (`SetIn`, `LogSetIn` new with an exact name for now, `ROUTINE_REPS`, `ImportSetIn`), `app/calllog.py` (`LogSetIn` joins the input models at `:88`), `app/services/lifts.py` (number, key, summary R14, history and `_prior` skipping sets without reps for Epley), `app/formatting.py` (`seconds`, `metres`), `app/services/common.py` (R15), `app/services/entries.py`, `app/services/export.py`, `app/services/imports.py` (R11), `app/services/routines.py` (R10, `get_routines`' left-out lift), `app/services/goals.py` (R10), `app/services/log.py`, `app/templates/_parts.html` (the sets table gains Time and Distance when any set holds them), `app/mcp_server.py` (`log_lifts` takes `LogSetIn`, the `:400` table, `IMPORT_DESCRIPTION`), `docs/can-and-cant.md:8` | `tests/test_sets.py` (new); `tests/test_exercise_migration.py` (`0014`'s part); `tests/test_schema.py` (`:493-504`) and `tests/test_hardening_schema.py` (their up, down, up runs write lift sets: read each against `0014` at head); `tests/test_data.py` (`:751`: a Suitcase Carry set of reps); `tests/test_rules.py` (`:171`), `tests/test_lifts.py`, `tests/test_gym_logging.py`, `tests/test_routines.py` (`:78`, `:503`), `tests/test_import.py`, `tests/test_edit_history.py`, `tests/test_export.py`, `tests/test_web.py`, `tests/test_formatting.py`, `tests/test_feedback.py` |
| **C4c** One matcher | 2 | high | `app/rules.py` (`name_key`, the steps, R3 to R5, `unknown_exercise`), `app/services/lifts.py` (the history read, `exact`), `app/mcp_schemas.py` (`LogSetIn.exercise`, `exact`), `app/mcp_server.py` (the instructions, `log_lifts`' and `get_routines`' descriptions), `app/services/cardio.py`, `app/services/goals.py`, `app/services/progress.py` (R19), `app/services/routines.py`, `app/services/imports.py`, `app/services/fields.py` (the filters' word read), `docs/can-and-cant.md` (`:24-25`, `:232-234`) | `tests/test_matcher.py` (new); `tests/test_rules.py` (`:19`, `:127`), `tests/test_cardio.py`, `tests/test_goals.py`, `tests/test_progress.py`, `tests/test_import.py`, `tests/test_routines.py`; the 4,011 pins in the 8 files and `INSTRUCTIONS_AS_BUILT`, all to 4,086 |
| **C4d** `list_exercises` | 2 | medium | `app/services/account.py` (R8), `app/services/__init__.py` (`all_exercises`), `app/mcp_server.py` (the function, its argument, the resource), `app/mcp_schemas.py` (`ExerciseItem`, `ExercisesResult`), `app/calllog.py` (`KNOWN_NAMES` gains `search` by hand; the set fields join by themselves through `:88`), `app/web.py` (`lift_names`), `docs/can-and-cant.md` (the function table) | `tests/test_list_exercises.py` (new); `tests/test_mcp.py`, `tests/test_trace.py`, `tests/test_feedback.py`, `tests/test_progress.py:433`, `tests/test_cardio.py:34` |
| **C4e** Volume, bests, muscles | 2 | high | `app/services/fields.py` (R18, the volume rule, the meanings), `app/metrics.py` (bests by kind), `app/services/lifts.py` (`history`, `prs` callers, best texts), `app/services/progress.py` (summary lines for the new kinds), `app/mcp_schemas.py` (`NewBest`, `Best`), `app/templates/progress.html`, `app/templates/progress_lift.html`, `app/web.py` (the progress pages), `docs/can-and-cant.md` (`:376-379`) | `tests/test_bests.py` (new); `tests/test_data.py`, `tests/test_progress.py`, `tests/test_web.py`; `tests/test_hardening.py` passes unchanged (the oracles hold no new kind) |
| **C4f** The per-hand check | 2 | medium | `app/services/lifts.py` (the check, R12, R13), `app/mcp_schemas.py` (`hand_check`), `app/mcp_server.py` (`log_lifts`' description) | `tests/test_hand_check.py` (new); `tests/test_feedback.py`, `tests/test_mcp.py` (`every_result` gives `hand_check`), `tests/test_calllog.py` (the marker test still finds no value) |
| **C4g** The evals | 2 | medium | `evals/cases.json`, `evals/judge.py` (a `lift_set` table in the rows, its notes), `evals/run.py` (`person_rows`), `docs/evals.md` | `tests/test_evals.py` (`CONTRACT_CASES`), `tests/test_golden_cases.py` (`:695` and each changed case's passing and failing run), `tests/test_web.py:474` (the case count) |

**Pins outside the packages.** Grepped on this tree for "41", "30 reps", "1 to 30" and "exactly as" in `README.md`, `docs/` (contracts aside) and `app/templates/`:

| Hit | Owner |
|---|---|
| `docs/can-and-cant.md:24`, "Use them exactly as `list_exercises`" | C4c (the model-read table above) |
| `app/templates/hi.html:64`, "{n} lifts by name": the count renders from `exercises.LIFTS`, so the words stay true; its pin `tests/test_hi_there.py:95` | C4a.2 |
| `app/templates/hi.html:79`, "its own list of {n} names. A name not on the list is refused, never guessed." | C4a.2 |
| `README.md`, `docs/evals.md`, `docs/domain-move.md`, `app/templates/home.html` (the landing page) | No hit. `docs/evals.md` still gains the judge's four additions and the new cases' rows in C4g |

**Order.** C4a.1, its deploy, then C4a.2, C4b, C4c, C4d, C4e, C4f, C4g, one commit each, then deploy 2. C4b before C4c: the matcher's word step returns the sets C4b's checks read. C4e after C4b: bests read time and distance. C4f after C4e: the check reads the heaviest set.

## Tests

Every date from a fixed today through `db.utcnow`, never the real date. No test touches the network.

| File | What |
|---|---|
| `tests/test_exercise_migration.py` (C4a.1, then C4b) | `0013` up, down, up on SQLite over rows of every kind written at `0012` through the services, for two accounts: every row and set kept; `foreign_key_check` clean; the models and the database agree. Each key by the rule; an unknown row's key printed; two rows of one key stop the upgrade with its line and the database stays at `0012`. Every rule of `0012` still on `lift_set` after the rebuild, with its unique and indexes. A set at -20 kg taken; -1,001 refused; a set with no reps, time or distance refused; `time_s` 0 refused. Down stops on a set with no reps and on a minus weight, each with its line, the database left at `0013`; with both gone, down runs and `reps` is NOT NULL again. The offline Postgres SQL holds the columns and the rules. Sync by key: a renamed row keeps its id and sets; an unlisted key is retired and never matched, its history still read; a rename onto a held name stops with its line. C4b adds `0014`: after it, no lift set, session (a soft-deleted one too), `lift_session` version or `log_lifts` key is left, for either account; every food entry, saved food, weigh-in, measurement, cardio entry, goal, routine, feedback row, call-log row and other write key is; its line's counts; its down changes nothing |
| `tests/test_exercise_list.py` (C4a.2) | R17's rules, and the counts: 242 lifts, 25 cardio, 153 families, 61 words, 46 defaults, 18 words a default's own name, 4 `compare_with` links |
| `tests/test_sets.py` (C4b) | Each row of R6, the message and fix word for word; reps 100 taken and 101 refused; a hold, a carry, a band set and a help set logged, read by `get_log`, edited, undone and exported with their time and distance; two holds that differ only in time are two sets in the key; R10 and R11's refusals; a four-item version from before `0013` put back |
| `tests/test_matcher.py` (C4c) | `name_key` on every name, its plural, its "Name (Equipment)" forms (every lift whose name holds an equipment word reaches its own row and no other), and the said words of decision 31. The history table's four rows for "squat", with Smith Squat; decision 2's "bench press" with Smith Bench Press; R4's refusal with its dates; `exact` true skips the step; outside `log_lifts`, an import of "Bench Press" with Smith history lands on Bench Press, `set_goal` "squat" is refused with both, `set_goal` "bench press" is exact; a read with "squat" takes every candidate logged (R19, `get_data`); close names over aliases show the lift's name; a retired row matches nothing |
| `tests/test_list_exercises.py` (C4d) | The bare call: the person's logged names first, then the 46 lift defaults, then the 25 cardio activities, each once, `count` 267, the note; another account's names never in it. A kind, a category, a search ("row", "hip thrust", "smith", an alias), at most 10 rows, `count` beyond 10. The resource is every row. The pages' slugs reach a lift never logged and a retired one. Each answer's size printed for the hand-over |
| `tests/test_bests.py` (C4e) | Each row of the bests table, the first session setting none; volume by every load type and both sides, the 800 kg example, none for a hold, 0 at help; `get_data`'s `family`, `time_s`, `distance_m`; Suitcase Carry's muscles in a muscle group; a carry's region full body |
| `tests/test_hand_check.py` (C4f) | In the family (Dumbbell Bench Press against Bench Press), through each of the 4 links, against the barbell of two bar lifts, against the EZ bar alone; no check with the bar lift never logged, or at or below it; asked at 50 kg a hand, then no ask at 55 kg a hand later, since an earlier set's pair total was already above the bar best; asked again once that earlier set is deleted; the number stored as sent; the call log's `pair` note, its cut at 128 characters, and no weight in any column |

Through `/mcp`: the published list for everyone, with only the entries this contract names differing; every input and result field described; every new message, fix, note and summary through `tests/text_rules.py`.

## Eval cases

New cases, in `evals/cases.json` after the last, each with a passing and a failing run in `tests/test_golden_cases.py`. A seed of history is a `log_lifts` seed dated days ago, as today's seeds are.

**The judge first reads the rows and the calls, then the reply.** Its known hole is reading the reply's words, so every case below passes first on the database or on a call's own result, and only second on the reply. C4g gives the judge four things:

| Addition | What |
|---|---|
| A `lift_set` table in `db` | One row a set: `exercise` (the lift's name), `weight_kg`, `reps`, `time_s`, `distance_m`, `session_id` (`evals/run.py`'s `person_rows`) |
| `error_code` on a call | The call's answer was a refusal with this code, such as `pick_lift` |
| `answer_has` on a call | The call answered without error and its result holds this key, such as `hand_check`, or a key and a value, such as an import preview's lift name |
| A second turn, `then` | A second line sent in the same conversation after the first reply, judged on the calls of both turns. Claude Code continues a session by its id; whether the Codex and Gemini clients can is not checked. A client that cannot runs the case's first turn only, and the run says so |

| Id | Say | Seed | First: rows or calls | Then: the reply |
|---|---|---|---|---|
| `word_no_history` | "bench 3 sets of 8 at 60" | none | three Bench Press sets of 60 kg x 8 | names Bench Press |
| `word_smith_history` | "squat 3x5 at 60" | Smith Squat 3 days ago | three Smith Squat sets; no Back Squat set | names Smith Squat |
| `word_both_logged` | "squat 3x5 at 80" | Back Squat 5 days ago, Smith Squat 2 days ago | a `log_lifts` call with `error_code` `pick_lift`; no new set | asks, naming both |
| `pick_lift_answer` | "squat 3x5 at 80", then "the barbell one" | as `word_both_logged` | turn 1: `error_code` `pick_lift`; turn 2: a `log_lifts` call with `exact` true and no `pick_lift`; three Back Squat sets of 80 kg x 5 | turn 2 asks nothing |
| `word_named_equipment` | "barbell bench press 3x5 at 60" | Smith Bench Press a week ago | three Bench Press sets of 60 kg x 5; no new Smith Bench Press set | names Bench Press |
| `word_dumbbell_history` | "bench 3x8 at 30" | only Dumbbell Bench Press, a week ago | three Bench Press sets of 30 kg x 8: the design's result, since "bench"'s candidates are the bar lifts (Bench Press, Smith Bench Press) and none is logged. **The owner is told this result before the run** | names Bench Press |
| `dumbbell_each` | "dumbbell bench press 3x10 with the 25s" | none | three Dumbbell Bench Press sets at 25 kg | has "25 kg each" or "each" |
| `hand_check_family` | "dumbbell bench press 50 kg for 8" | Bench Press 80 kg x 5, a week ago | one Dumbbell Bench Press set at 50 kg, unchanged; the `log_lifts` call `answer_has` `hand_check` | asks once whether 50 was a hand or both |
| `hand_check_link` | "hammer curls 3x10 at 20" | Barbell Curl 30 kg x 8, a week ago | three Dumbbell Hammer Curl sets at 20 kg, unchanged; `answer_has` `hand_check` | asks once |
| `hold_plank` | "held a plank for 90 seconds, twice" | none | two Plank sets, `time_s` 90, reps null | |
| `dead_hang` | "dead hang 45 seconds" | none | one Dead Hang set, `time_s` 45 | |
| `carry_farmers` | "farmer's carry, 32 kg each hand, 2 lengths of 40 m" | none | two Farmer's Carry sets, 32 kg, `distance_m` 40 | |
| `hyrox_wall_balls` | "100 wall balls with a 6 kg ball" | none | one Wall Ball set, 6 kg x 100 | |
| `box_jumps` | "4 sets of 5 box jumps" | none | four Box Jump sets of 5 | |
| `burpees` | "did 3 sets of 15 burpees" | none | three Burpee sets of 15; no `log_cardio` call; no Burpee Broad Jump set | |
| `assisted_pull_up` | "assisted pull-ups 3x8 with 20 kg of help" | none | three Assisted Pull-Up sets at -20 kg x 8 | |
| `unknown_lift_close` | "3x10 cambered bar skull crushers at 30" | none | no new set; any `log_lifts` call has `error_code` `unknown_exercise` | asks which lift |
| `read_plain_word` | "how is my squat going?" | Back Squat and Smith Squat, each twice | a data read answered without error (`read_any`); no write; no `pick_lift` | names both; asks nothing |
| `import_exact_smith` | "import this: 2026-10-20 Bench Press 3x5 60kg" | Smith Bench Press a week ago | an `import_entries` preview whose answer holds Bench Press; no lift set written | names Bench Press |
| `import_outside_defaults` | "import this: 2026-10-20 Hack Squat 3x8 100kg" | none | a `list_exercises` call with `search`, or an `import_entries` preview whose answer holds Hack Squat (not among the 46 defaults); no lift set written | names Hack Squat; asks before writing |
| `pushups_over_100` | "did 120 push-ups in one set this morning, log it" | none | no lift set; any `log_lifts` call has `error_code` `reps_out_of_range` | asks, and says 100 |

Changed cases:

| Case | Change |
|---|---|
| `lift_one_sentence`, `lift_set_by_set`, `lift_added_later`, `routine_logged`, `routine_changed`, `lb_lifts`, `lb_routine_changed`, `mixed_unit_session`, `bodyweight_pullups`, `two_separate_workouts`, `two_lifting_days`, `hurried_squat`, `rambling_day_log` | Each checks `sets[].exercise` on a `log_lifts` call by a list name. The person's own word is now right, so the lift's name moves from `inputs_include` to the `lift_set` rows, with the same weights and reps. `hurried_squat`'s forbidden names and its "back squat" in the reply stay |
| `pushups_over_30` | 50 push-ups now log: it passes on one Push-Up set of 50 and no question. `tests/test_golden_cases.py:695` changes with it |
| `exercise_list` | Unchanged: "Which squat exercises" sends category squat, which lists every squat lift (R8). If its runs search instead, the 10 rows may miss one of its two names: the run says |

**The run.** One for the release, at deploy 2, when `python -m evals.need main` says yes. Its scope and cost are told to the owner first, on the Claude side; nothing on Railway. At the last release run's $0.69 a case at 3 runs (`docs/evals.md:122`: $35.2311 for 51 cases), the 21 new and 14 changed cases are about $24; the whole set of 256 about $177. `pick_lift_answer`'s two turns may cost more than a case's average: not measured. Each run passes `--model`.

## Dated notes owed

Written at the release, at each contract's foot, never in its body.

| Contract | Note |
|---|---|
| `docs/contracts/data-on-request.md` | The volume rule by load type and sides (`volume_kg` on `lift_sets`, `lift_sessions` and `days`): x 2 for a pair, x 2 for one side at a time, none for a set without reps, 0 at 0 kg or below. The 41-lift table stays the record of 2026-11-16; the list in code is now the home of every lift's muscles, at R13's 1 and 0.5, and R13 (h) no longer binds: Suitcase Carry has muscles. Region: every carry and sled full body. `lift_sets` gains `time_s`, `distance_m` and `family`. The note goes after "Not in this work", so `tests/test_data.py`'s table parser still finds the table first |
| `docs/contracts/gym-logging.md` | A set is weight and reps, or a time, or a distance and time; reps 1 to 100; a plain word in `log_lifts`; the "Not in this work" line's holds, carries and sets over 30 are done here |
| `docs/contracts/routines.md` | Reps 1 to 100; a routine takes only lifts of sets and reps (R10); a plain word is refused there with its candidates, a default's own name is exact; `get_routines` says to send `exact` true |
| `docs/contracts/trace.md` | `notes` also holds `pair`, the per-hand ratios: a figure worked from two weights, never a weight (R12) |
| `docs/contracts/hardening.md` | `0013` widens `ck_lift_set_weight_kg` to -1,000 and makes `reps` nullable, with three new rules on `lift_set`; `0014` deletes every lift set once |
| `docs/contracts/units.md` | `distance_m` and `time_s` are metres and seconds on every account (R16) |

`BUILD_LOG.md` gets one entry for the item: both deploys, the reviews, the Postgres run, the sizes, the eval run.

## The exit check

1. Every package's tests pass, plain and with `-W error`.
2. C4a.2 only: the dump of `app/exercise_rows.py` equals the owner's reviewed sheet, and the sheet's own check passes on the dump (R16a). Without both, C4a.2 is not signed off.
3. The whole suite once on a clean export of each deploy's exact commit, as `CLAUDE.md` says.
4. One local Postgres run, on the owner's okay to start Docker: at `0012`, rows of every kind for two accounts through the services, lifts with an edit, an undo and a soft delete among them; up to `0013` (every row kept, the rules in `pg_constraint`, the keys filled); down to `0012` and up again over those rows; up to `0014` (every lift row, version and `log_lifts` key gone, every other row there, its line); a hold and a help set written; `0013`'s down stops on them; with them gone, down to `0012` and up to `0014` again over a written food row and weigh-in. Docker stopped after, the container gone with its volume.
5. The reviewer, on a frozen copy, for each package: each ruling by probe through `/mcp`.
6. `python -m evals.need main` says yes; the release's one run, its cost said first.
7. After each deploy: `/health` 200, `/mcp` 401 with no token, `/` 200, `/ready` 200, and a fresh assistant session. After deploy 1: the live database at `0013`, `list_exercises` with 56 rows. After deploy 2: `list_exercises` bare, its note and count; a fresh session sees `search` and the new sentence.
8. Before deploy 2: the owner is told that every lift set on live goes, for every account, those logged since deploy 1 too, and that no down migration brings them back.

## Known limits

| Limit | Why it stands |
|---|---|
| Sets logged on the old build between `0014` committing and the new build serving stay, under the old rules | Seconds. They are the person's own |
| Sets logged between the two deploys go at deploy 2 | So no set predates the new rules (R9). The owner is told first (exit check 8) |
| A routine or a goal made before C4 keeps its numbers. A dumbbell weight in one may be a pair total | The routines and goals hold no set. The person edits them |
| A conversation begun before a deploy keeps the old list and the old instructions | As every function change. A new conversation sees them |
| A client holding the old list may refuse a new top-level result field (`count`, `note`, `hand_check`) | Every new result field defaults to null, as `docs/contracts/units.md` R21 has it |
| The pair ratios live 90 days | The call log's retention |
| A rename that meets a name another row holds stops the sync | Rare; a release changes one of the two |
| The search matches by the key's substring: "row" also finds "Arrow" names if any were added | None on the list today |

## Open questions

Each with a pick. Only real ones found in the code. The owner took every pick on 2026-11-19, with three more: the
eval run at the release covers the new and changed cases only; "bench" from a person with only Dumbbell Bench Press
logged takes Bench Press, as designed; the gap between the deploys is his to say at the time.

| # | Question | Pick |
|---|---|---|
| 1 | The delete in `0013` at deploy 1, as first designed, or in its own `0014` at deploy 2. In deploy 1, sets logged between the deploys keep the old rules: a plank as reps, a dumbbell weight whose meaning was never said, a volume that changes at deploy 2 | `0014` at deploy 2 (R9) |
| 2 | Deploy 1's only model-read change is `SetOut`'s optional reps. An eval run then, or the one run at deploy 2 for both | The one run at deploy 2: it reads the same surface |
| 3 | `exact` and `LogSetIn` (R3): a new input and a new model, there to break the loop decision 2 makes | Yes, as R3 |
| 4 | The pair ratio's home (R12): the call log's `notes`, 90 days, no new column; or a small table of its own, kept for good with no person on it | `notes` |
| 5 | Minus weights only on the two assisted lifts (R6), or on every bodyweight lift | The two |
| 6 | The ranges of R6: `time_s` 1 to 3,600, `distance_m` 1 to 5,000, help down to -200 kg. **These are numbers picked for this contract, not his or the data's:** he sets them, or logged sets do | As written until then; the database's rules are wider, so a change needs no migration |
| 7 | Imports of holds, carries and sleds (R11) | Refused for now, the row named |
| 8 | `distance_m` on a mi account (R16) | Metres for everyone |
| 9 | The gap between the two deploys | At least a day; the owner says when |
| 10 | "dumbbell bench" reached no name, alias or word on the sheet of 2026-11-19 | Done: the sheet's alias sweep (pairs of equipment and a plain word) adds the true ones before C4a.2 |
| 11 | A routine holding Plank, Side Plank or a carry (R10): left out of what to log, with a line, or the routine refused until edited | Left out, with the line |

## Not in this work

The `exercise_alias` table (for when something writes aliases at runtime). Packs (Hyrox as a training kind, running, yoga). A session with splits and a total time. Watts, level, floors and rounds. Timed items in a routine (C5). Time and distance in the import. A kept per-person choice for a plain word. The body map page. A fixed per-hand ratio. Yoga and Pilates.

## Notes from the build

2026-11-19, C4a.2:

- (a) C4a.2 exempts five alias lines of `app/exercise_rows.py` from T1's AI-vocabulary rule. Each holds the gym name "leverage", a plate-loaded machine, on a line of its own. The five are in `tests/text_rules.py` `EXEMPT_LINES`, and `tests/test_text_rules.py:685` pins the file among the exempt ones. This holds only while no result shows an alias, so C4c and C4d keep aliases unshown.
- (b) On live, row ids mix lifts and cardio: the 56, then the 211 new. Any listing that needs an order (C4d's bare list, the resource) sorts by the list's own order, not by id.
- (c) Test files edited outside C4a.2's row, each for one reason:
  - `tests/test_exercise_migration.py`: its pins of 56 and 55 rows follow the full list, and a database at `0012` holds only the 56 live names.
  - `tests/test_data.py` beyond `:249` and `:272-293`: Suitcase Carry now has muscles, so its muscle filter, muscle groups, muscle names and filled count change. Its set of reps stays for C4b.
  - `tests/test_cardio.py:51-53`: the sync adds 25 cardio rows, not 15.
  - `tests/test_mcp.py:2442-2443`: "Squat (Barbell)" gets three closest names over 267 rows until C4c reads it as Back Squat.
  - `tests/test_text_rules.py:685` and `tests/text_rules.py`: the exemptions in (a).

2026-11-19, C4b:

- (d) `pushups_over_30` moved from C4g to C4b, so each commit is green: 50 push-ups log as one Push-Up set of 50,
  with no question (`evals/cases.json`, `tests/test_golden_cases.py`).
- (e) A routine takes R6's weight range for each lift, so a help weight round-trips: `save_routine` takes -200 to
  0 kg on Assisted Pull-Up and Assisted Dip, and what `get_routines` hands out from the last session can be saved
  back. Decided at C4b's review.
- (f) A rollback to deploy 1 after deploy 2 has written a hold, a carry or an edit is not safe: the old code's
  `get_log` and `delete_entry` raise on a set with no reps, and undo and export on a six-item version. Deploy 2 is not
  rolled back by code. A fix goes forward.
- (g) `0014` deletes only `log_lifts` write keys. A kept `import_entries`, `edit_entry`, `delete_entry` or
  `restore_entry` answer that names a lift session stays up to 24 hours: the contract's choice.
- (h) For C4e: `what_is_logged` also raises over a hold, as `get_data` does (`app/services/fields.py:631`, the
  session volume with reps None).
