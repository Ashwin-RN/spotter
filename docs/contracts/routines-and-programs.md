# Routines and programs contract: cardio and timed items, last time's numbers, and a program over weeks

**Goal: a routine holds what a person does at the gym, a row to warm up, squats, a plank, an incline walk to finish, and reads at the gym as the plan first, then last time's numbers with their date. A program lays the routines over weeks, so "what's today?" has an answer. Every number stays Spotter's, and every item a routine holds is one a log function takes as given.**

Scoped 2026-11-21 against `main` at `3b2441e`. His picks taken the same day (the foot); not built. C3 (food macros) is scoped at the same time and merges first: its migration is `0015`, this one's is `0016`. The builder reads each "What exists" row again on the commit it starts from, and after C3's merge.

## His words

- 2026-11-21, on a cardio item's heart rate, machine calories and watts: "If they mention it, let's have it ... store it as maybe an optional input rather than a required one ... If they're taking pictures of their screens and posting it onto Claude ... we should be able to track it."
- 2026-11-21, the brief: "a routine holds lifts only. People want cardio and timed items in a routine, last time's numbers with their date when they read it at the gym, and programs that run over several weeks."
- 2026-11-16, on last time's numbers on the routines page: "quite important". The page row decided then: "Read at the gym: the planned sets first, then last time's numbers with their date and unit. Saving one is a chat job; delete stays as a fix."
- 2026-11-19, on setup: "we should encourage them to set up their routine early on. what are the days they have in the gym (upper lower) or ppl or whatever and save the usual exercises rght? then we can do a quickadd ?"
- 2026-11-15, on routines: "a routine is logged straight away, not shown first." The standing ones: "Let's not overcomplicate things out of the bag." "Most of the days are going to be quick inputs, right? Log and push, not more than that."
- Programs have no words of his beyond the brief. His picks of 2026-11-21 (the foot) decide them.

## What exists today

Read at `3b2441e` on 2026-11-21.

| Piece | State | Where |
|---|---|---|
| A routine row | `routine`: `name`, `name_key`, `lifts` (JSON text), `saved_on`, the three times. Not an entry table | `app/models.py:378-395` |
| What a routine item holds | `{exercise, sets, reps, weight_kg}`, stored in that key order. A lift that does not record reps is refused: "Lift 2: Plank records time: a routine holds lifts of sets and reps." / "Log it with log_lifts." Cardio is refused as an unknown lift | `app/services/routines.py:121-185`, the refusal at `:138-140` |
| A save that changes nothing | Skipped when `json.loads(row.lifts) == checked`: `updated_at` and `saved_on` stay | `app/services/routines.py:240` |
| The weight to log | The heaviest set of the lift's latest live session (effective date, then id), unless the routine holds a weight and that session is dated before `saved_on`. One statement, a window over each lift's sessions | `app/services/routines.py:258-304` |
| Last time's numbers | **None.** `get_routines` gives `weight_kg`, `weight_from` and `last_date` a lift: one weight and one date, never the sets | `app/mcp_schemas.py:1494-1504` |
| A lift saved before it recorded time | A Plank, Side Plank, Farmer's Carry or Suitcase Carry saved with reps before `0013` is left out of what to log, with "Plank is left out: it records time now. Ask the person how long, and log it with time_s." | `app/services/routines.py:65-74`, `:293-295` |
| **A cardio item met by today's read** | **Raises.** `rules.records("Rowing Machine")` is "reps" by default, so `left_out_line` meets `ASK_FOR["reps"]`: `KeyError`. `lift_words` reads `x['sets']`: `KeyError`. A hold saved as `{exercise, sets, time_s, weight_kg}` only reads as left out. Probed 2026-11-21 from the scratchpad, no database written | `app/services/routines.py:66-74`, `:93-106` |
| The result models | `SavedRoutineLift.sets` and `.reps`, `LiftToLog.sets` and `.reps`: required `int`. The top-level result models forbid unknown fields (`TOP`); the nested ones, `LiftToLog` among them, set none. The exercise list contract kept them required because a routine took only lifts of reps, and named timed items as this item | `app/mcp_schemas.py:39`, `:1465-1504`; `docs/contracts/exercise-list.md` R7, R10 |
| The input model | `RoutineLiftIn`: `exercise`, `sets`, `reps` required; `weight_kg`, `weight_lb` | `app/mcp_schemas.py:176-195` |
| A set's time and distance | `log_lifts` takes `time_s` (1 to 3,600) for a hold, `distance_m` (1 to 5,000) and `time_s` for a carry or a sled, by the lift's measures | `app/rules.py:58-64`, `:710-748` |
| Cardio's numbers | `log_cardio`: `minutes` (1 to 600), `distance_km` or `distance_mi`, `incline_pct` (0 to 40), `speed_kmh` or `speed_mph`, `avg_hr`, `machine_kcal`. Checked by `cardio.check_pairs`, `check_numbers`, `with_speed` | `app/rules.py:107-110`, `app/services/cardio.py:71-74`, `:125-235` |
| What a lift records | 242 lifts and 25 cardio rows. 11 lifts record time, or distance and time: Wall Sit, Dead Hang, Plank, Side Plank (time); Farmer's Carry, Suitcase Carry, Sled Push, Sled Pull, Sled Drag, Sandbag Lunge, Burpee Broad Jump (distance and time) | `app/exercises.py`, read 2026-11-21 |
| Set words | `lifts.sets_text`: "Plank 2 × 60 s", "Farmer's Carry 2 × 40 m at 32 kg each in 45 s", mixed sets "60 kg × 5, 60 kg × 4" | `app/services/lifts.py:278-330` |
| The page | `/routines`: one table, Routine and Lifts (the lifts in words with the weight to log) and Delete. Settings tab, Foods' frame | `app/web.py:2464-2526`, `:696`, `app/templates/routines.html` |
| A schedule or a program | **None.** No table, column or function holds a weekday, a week or a routine's place in a plan. No session records which routine it came from | `app/models.py` (searched) |
| Counts | 24 functions for everyone, 27 with the owner's three, 14 writes. Instructions 4,082 of 4,096, pinned by length in 9 test files and by hash in `tests/test_feedback.py:96` | `app/mcp_server.py:266`, `app/services/common.py:120`, `tests/test_switch.py:573` |
| Migration head | `0014`, pinned in `tests/test_edit_history.py:1355`, `test_feedback.py:215`, `test_hardening_schema.py:38`, `test_routines.py:1252`, `test_schema.py:598`, `test_switch.py:511`, `test_units.py:820` | As listed |
| A person's tables | `fields.OTHER_TABLES` must name every table no data area reads; `tests/test_schema.py:164` lists `user_tables()` | `app/services/fields.py:867-876` |
| Evals | 258 cases, 101 core. 13 cases use the routine functions (listed under "The eval run"). 13 cases delete or restore. Placeholders: `{{today}}`, `{{yesterday}}`, `{{days_ago:N}}`, `{{next_month_end:N}}`. A database check cannot name a cardio activity: a `lift_set` row carries its lift's name and `time_s`, a `cardio_entry` row only `exercise_id` | `evals/run.py:212-217`, `:370-400`, `evals/judge.py:119`, `:158-162` |
| The log rework | `docs/contracts/log-and-guide.md`, on branch `log-and-guide` only, waits for his go. Its row 4 rewrites `get_routines`' sentence that names `log_lifts`; its row 16 rewrites the hold refusal at `routines.py:140`, which C5 removes | That branch |

## The designs weighed

| Question | Way | Verdict |
|---|---|---|
| Where cardio items go | (a) In `lifts`, in the order done, told apart by the exercise's kind | **Pick.** No migration, no renamed argument, order kept. Its cost: a field named `lifts` holds a row. The words say so |
| | (b) A new `cardio` list beside `lifts` | Not picked: the order between a warm-up, the lifts and a finisher is lost, and two lists need explaining |
| | (c) Rename to `items` | Not picked: a renamed argument breaks a chat holding the old list at once, a renamed result field takes two deploys, a renamed column a migration |
| How a program places a day | (A) A weekday schedule in blocks of weeks: Spotter works out the week and today's routine from the date | **Pick.** `log_lifts`, `log_cardio` and `lift_session` do not change. Its limit: a rotation not tied to weekdays (push, pull, legs, rest, repeat) is not followed, and a missed day does not move the plan |
| | (B) A rotation that moves on when a routine day is logged | Not picked: each session must record its routine, so `lift_session` and `cardio_entry` gain a column and `log_lifts` and `log_cardio` an argument: model-read text on the two most used functions, the files C3 and the log rework also change |
| | (C) Count workout days since the start | Not picked: an extra cardio day or a missed one moves it wrongly, and the rule is hard to say |
| What "last time" is | Each item's own latest session or entry, wherever it was done | **Pick.** No link needed; the same session the weight to log already comes from |
| | The last time this routine was done, one date for the routine (the 2026-11-18 drawing) | Not picked: needs the session link of (B) |
| Logging a mixed routine | `get_routines`, then `log_lifts` for the lifts, holds and carries, and `log_cardio` once a cardio item | **Pick.** One way to log each kind; the log rework later makes these `log` calls |
| | One call that logs a routine by name | Not picked: a new write, a rule for which sets a change replaces, and the assistant cannot say the numbers first. `routines.md` weighed it the same way |

## Decisions

The numbers in brackets name his pick (the foot) behind the row. Rows with none follow from the rule named.

| # | Decision | Why |
|---|---|---|
| 1 | **Three packages, two deploys.** C5a (deploy 1): last time's numbers, the page, and the read made safe for any item. C5b (deploy 2): cardio and timed items saved and read. C5c (deploy 2): the program, migration `0016`. Nothing in C5a or C5b depends on C5c: dropped, C5 has no migration [1, 13] | `CLAUDE.md`: a required result field made optional takes two deploys |
| 2 | **Deploy 1 makes `sets` and `reps` optional** in `SavedRoutineLift` and `LiftToLog`, still filled for every item, and adds `last` (default null). No new item kind can be saved yet [13] | So no chat holding the old list ever meets a missing `reps` |
| 3 | **Deploy 1's read never raises on an item it cannot place.** A cardio row, a lift of time or distance, an unknown name or a missing key is left out of what to log and of the words, with a line. So a rollback from deploy 2 to deploy 1 is safe | The probe above: today's code raises on a cardio item |
| 4 | **A routine item is one of four kinds,** by its exercise's list row: a lift of reps (`("weight", "reps")` or a band's `("reps",)`), a hold (`("weight", "time")`), a carry or sled (`("weight", "distance", "time")`), or cardio (kind cardio) [6, 7, 8] | What the list already says a set records |
| 5 | **Each kind holds what its log function takes, checked by that function's own rules,** with no new range: a lift `sets` and `reps`; a hold `sets` and `time_s`; a carry or sled `sets`, `distance_m` and `time_s` when given; each with `weight_kg` or `weight_lb` as today. Cardio `minutes`, and when given `distance_km` or `distance_mi`, `incline_pct`, `speed_kmh` or `speed_mph`, `avg_hr` and `machine_kcal`: every `log_cardio` number, each optional but `minutes`, so a heart rate or a machine's calories the person names, from a screen photo too, are kept [7, 8] | Whatever a routine holds, a log function takes as given |
| 6 | **Stored as today for a lift of reps:** exactly `{exercise, sets, reps, weight_kg}` in that order. A hold `{exercise, sets, time_s, weight_kg}`; a carry `{exercise, sets, distance_m, time_s, weight_kg}`; cardio `{exercise, minutes, distance_km, incline_pct, speed_kmh, avg_hr, machine_kcal}`, nulls kept, miles stored as km and mph as km/h at 2 dp | The skip at `routines.py:240`: an extra key on an old lift would make an unchanged save write, move `saved_on` and flip the newest weight |
| 7 | **The weight to log follows today's rule for every lift kind:** the plan keeps the count (reps, time or distance), the weight comes from the newest. A cardio item is logged as planned, its heart rate and machine calories only when the person gives them that day: a plan's figure is never logged as a result | The rule the person already has |
| 8 | **A hold or carry with no weight reads null, and the assistant asks,** as a lift today. His open question on a routine lift saved with no weight then decides lifts and holds together [9] | One rule for every lift, decided once |
| 9 | **A hold saved with reps before `0013` is never read as seconds.** It stays left out with today's line | A rep count is not a time |
| 10 | **Cardio items count toward the 20 items, not the 60 sets.** The limits keep their numbers: 20 routines, 20 items, 10 sets an item, 60 sets in all [5] | A cardio item is its own entry, not a set of the session |
| 11 | **`last` on each item:** the lift's latest live session in the one order, every set of that lift in it with its weight, reps, time or distance, its date, and its words in the person's units. For cardio, the activity's latest live entry: date, minutes, distance, incline, speed, heart rate, the machine's calories and words. Null when never logged [10] | "Last time's numbers with their date and unit". The same session the weight to log reads |
| 12 | **"Same as last time" logs last time's sets as they were:** one sentence in `get_routines`' description [11] | `last` carries them. No new rule |
| 13 | **Logging a routine is the log functions, unchanged:** `log_lifts` for the lifts, holds and carries, one item a set with `exact` true; `log_cardio` once a cardio item. Each item names its function in `log_with`. Logged straight away, as planned, with each change the person names [12] | His 2026-11-15 pick. A field where the model looks, not more words |
| 14 | **The page: one card a routine, a table of Item, Plan and Last time,** the program card above them. No form. Built from the stylesheet as it is; a new rule, if any, is named in the hand-over [16] | His page row. The read at the gym is the page |
| 15 | **A program is a weekday schedule in blocks of weeks.** A block: 1 or more weeks that repeat the same days, each day a weekday and a saved routine's name. The week is counted from `start_on`; today's routine is the block's routine for today's weekday [2] | Design (A) |
| 16 | **One program a person.** `save_program` makes it, or replaces it whatever its name. The old schedule is not kept; the routines and sessions are [3] | "What's today" needs one answer |
| 17 | **`save_program`, a new write, and the program in `get_routines`' answer.** 25 functions for everyone, 28 with the owner's, 15 writes; C3 adds no function [4] | One job a function. One read for the gym, routine and program together |
| 18 | **A program's limits:** 1 to 52 weeks in all, **a cap**: a year, so a reply's week count stays short; revisited from use. Each weekday at most once a block, 1 to 7 days. `start_on` no more than two years back (the entries' rule, `rules.OLDEST_DAYS`) and no more than 52 weeks ahead, the same cap: an entry is never after today, so a forward bound is new, and a start a year out is a slip of the year. Every routine a day names must be live at the save [5] | Never a guessed number: the one new number is named as a cap, and serves both bounds |
| 19 | **Delete and restore take kind `program`,** through `_deletable`, the two calls and the switch, as a routine. The page's Delete deletes at once with Undo | The switch's rule for every delete by an assistant |
| 20 | **Export `programs.csv`, account delete counts it, the trace, isolation, `fields.OTHER_TABLES`:** as `routines.md` decisions 13 to 17 for the routine | A person's data, all of it, and own data only |
| 21 | **The instructions do not change: 4,082 characters,** or C3's figure | 14 characters are free |
| 22 | **One eval run for the release, at deploy 2,** on the gate tier (his pick 15) [14, 15] | His rule: once, at the end of a whole build |

## Rulings on the how

| # | Ruling | Why |
|---|---|---|
| R1 | **The base.** C5a and C5b build in a worktree from `main` beside C3. C5c builds after C3 merges: it needs `0015` and the most files C3 changes. Before C5's first merge, the branch merges `main` and the builder rereads every overlap row (below) | `CLAUDE.md`: items on the same files build one after another. Here the join is planned instead |
| R2 | **Deploy 1's tolerance (decision 3).** `get_routines` places an item only when its exercise is a lift of reps and the item holds `sets` and `reps`; keys it does not know on an item are ignored, so a field a later build adds never breaks this read; anything else is left out with a line: a cardio row "Rowing Machine is left out: log it with log_cardio.", an unknown name or shape "{name} is left out." (or "An item is left out." with no name). `left_out_line` never indexes `ASK_FOR` with a key it lacks. `lift_words` reads only placed items | Decision 3 |
| R3 | **`last`, one more statement.** `latest_tops` also returns each lift's session id; one statement then reads every live set of those (session, lift) pairs in set order. Cardio (C5b): one statement, a window over each activity's live entries by effective date then id. The read runs a fixed number of statements however many routines, as hardening D5 holds | No walk over every session |
| R4 | **`last`'s shape.** A lift: `{date, sets: [{weight_kg, reps, time_s, distance_m}], words}`, `words` as `lifts.sets_text` says that lift's sets, without the name ("62.5 kg × 8, 62.5 kg × 8, 57.5 kg × 7", "3 × 60 s"). Cardio: `{date, minutes, distance_km, incline_pct, speed_kmh, avg_hr, machine_kcal, words}`, the minutes by `formatting`'s `minutes` quantity ("30 min"), the distance and pace as `cardio.summary` gives them ("30 min, 5 km, pace 6:00 per km"); `cardio.summary` itself says "for 30:00", so its opening is not reused. Dates YYYY-MM-DD; the page says the day as `formatting.day` does | One wording for sets, already tested |
| R5 | **`save_routine`'s checks (C5b).** By the item's kind (decision 4). A lift of reps: today's checks, word for word. A hold or carry: `sets` 1 to 10, then `rules._set_numbers` with the lift's name, so its refusals are `log_lifts`' codes with "Lift n"; the weight as today. Cardio: `cardio.check_pairs` and `check_numbers` over `minutes`, `distance_km`, `incline_pct`, `speed_kmh`, `avg_hr` and `machine_kcal`, `log_cardio`'s own ranges, then `speed_disagrees`; a mile number refused in miles as `log_cardio` refuses it; `sets`, `reps` or a weight on a cardio item: `invalid_field` "Lift 2: Rowing Machine is cardio: it takes minutes, not sets." / "Send minutes on lift 2, with distance, incline or speed when the person gave them." Every failure at once, nothing written | Decision 5. "Lift n" stays: the argument is `lifts` |
| R6 | **The words of an item** (summaries and the page): a lift as today; a hold "Plank 3 × 60 s", with a weight "at 10 kg"; a carry "Farmer's Carry 3 × 40 m at 32 kg each", with a time "in 45 s"; cardio "Rowing Machine 10 min", "Incline Treadmill Walk 20 min at 12% incline", "Outdoor Run 30 min, 5 km". Every number through `app.formatting`, in the person's units | `lifts.sets_text`'s words for sets |
| R7 | **`get_routines`' items (C5b)** gain `time_s`, `distance_m`, `minutes`, `distance_km`, `incline_pct`, `speed_kmh`, `avg_hr`, `machine_kcal` (null where the kind has none) and `log_with` ("log_lifts" or "log_cardio"). A cardio item's `weight_kg`, `weight_from`, `last_date` are null; `sets` and `reps` null | Decision 13 |
| R8 | **The program row (C5c).** `Program(Base)`, not an entry table: `id`, `user_id` (`_user_id()`, unique), `name` `String(64)`, `start_on` `Date`, `blocks` `Text` (JSON), `saved_on` `Date`, `created_at`, `updated_at`, `deleted_at`. `UniqueConstraint("user_id")`, named `uq_program_user_id` | One a person (decision 16). A table, so the export, delete and isolation patterns hold |
| R9 | **`save_program`'s save.** Lock, write limit, checks, then the person's row, deleted or not: none makes one (`created` true); one is replaced, its name too, and brought back if deleted. Blocks stored as `[{"weeks": n, "days": [{"weekday": "monday", "routine": "Push day"}]}]`, the routine's own stored name, days in weekday order. The same name, start and blocks again write nothing | `save_routine`'s pattern |
| R10 | **The program in `get_routines`.** `program` null with none. Else `{id, name, start_on, weeks, week, state, today, next, this_week, blocks, words}`. `week` = days since `start_on` // 7 + 1 while running; `state` "not_started", "running" or "ended"; `today` the routine for today's weekday in this week's block, or null for a rest day; `next` the first later date in the program with a routine, `{date, routine}`; `this_week` the 7 days of the current program week, `{date, weekday, routine}`. Today is `users.today_for(user)`. A day whose routine is no longer live gives its name and the words say "Pull day is not saved now." | Decision 15 |
| R11 | **The program's functions.** `save_program` after `get_routines`, before `what_is_logged`. `NEEDS` `log`, annotation `SETTING`, takes `key`. `common`: `WRITE_FUNCTIONS`, `KIND_LABELS["program"]` ("program", "the id that save_program returned, or the one in get_routines"), `READ_BACK["save_program"]` "get_routines", `ENTRY_OR_FOOD` gains "or get_routines for a routine or the program". Input models `ProgramBlockIn` (`weeks`, `days`), `ProgramDayIn` (`weekday`, a lower-case weekday name as an enum, `routine`) | As `routines.md` R10 and R11 |
| R12 | **The rest of the table's checklist (C5c)**, as `routines.md` R12 to R16 for the routine: `entries._deletable`, `entry_dict`, `_links`; `restore_entry`'s `not_deleted` fix "Nothing to do: it is live. get_routines shows it."; `undo_edit` `nothing_to_undo` "A program has no earlier values kept." / "Save it again with save_program, as it was."; `edit_entry` `unknown_kind` "A program is not edited here." / "Use save_program with the whole program."; `calllog.TABLES` and `KNOWN_NAMES` (`blocks`, `start_on` and the two input models' fields); `export.TABLES` and `ORDER` after `routines.csv`; `account.OWNED`; `web.DELETE_COUNTS` `(Program, "Programs")`; `fields.OTHER_TABLES["program"]` "get_routines reads it."; the Settings lines that list what an assistant may delete | So nothing a person keeps is missed |
| R13 | **Counts and pins.** Every count of functions, writes and cases that is drawn, pinned or written moves, each by file and line in the hand-over, from C3's figure plus C5's | Two items move the same pins |
| R14 | **Size.** `get_routines` returns every routine with `last` on each item. The builder prints the answer's size at 6 routines of 8 items with 4 sets each, and at the limits (20 routines, 20 items, 60 sets a session), and pins both. Not measured here | His rule: a number from the data |
| R15 | **The eval runner (C5c)** gains `{{weekday:N}}`, the person's weekday N days after today (0 to 6), lower case, so a schedule case never depends on the real weekday. `save_program` joins `judge.SEED_SERVICES`; `program` joins `DB_TABLES` and `run.TABLES`, a row with `week_count` (the blocks' weeks added). A cardio item is judged on `log_cardio`'s inputs: a database check cannot name the activity | `CLAUDE.md`: no test depends on the real date or weekday |
| R16 | **Tests use the clock's seam** for today and the weekday, never the real date | `CLAUDE.md` |

## The two deploys

| Deploy | Packages | What it changes |
|---|---|---|
| 1 | C5a | `sets`, `reps` optional in two result models; `last` on lifts; "same as last time" in `get_routines`' description; the read made safe (R2); the page. No item kind is saved that was not before. No migration |
| 2 | C5b, C5c | Holds, carries, sleds and cardio saved and read; `log_with`; the program, `0016`, `save_program`. The release eval run |

Deploy 2 goes no sooner than a day after deploy 1; he says when. A rollback from deploy 2 to deploy 1 is safe (R2). A rollback past deploy 1 after deploy 2 has saved a cardio item is not: today's code raises on it. A chat begun before a deploy may refuse an answer with a field it does not know, as at every function change: told to him at each merge.

## Said, call by call

| Said | Calls | What comes back |
|---|---|---|
| "Save my legs day: 10 minutes on the rower, back squat 4 sets of 5, plank 3 times 60 seconds, 20 minutes incline walk at 12%" | `save_routine` with four items in order | "Saved Legs day: Rowing Machine 10 min, Back Squat 4 × 5, Plank 3 × 60 s and Incline Treadmill Walk 20 min at 12% incline." |
| "Log my legs day" | `get_routines`; `log_lifts` with 4 squat sets at the weight to log and 3 plank sets of 60 s; `log_cardio` twice | The session summary and the two cardio summaries |
| "Log my legs day, but I rowed 15 minutes" | The same, the row at 15 | The same |
| "Log my push day, same as last time" | `get_routines`; `log_lifts` with `last`'s sets | The session summary |
| "What did I do on push day last time?" | `get_routines` | Each item's `last` words and date |
| "I'm starting an 8 week PPL on Monday: push Monday, pull Wednesday, legs Friday" (said on Sat 21 Nov) | `save_program` with `start_on` 2026-11-23 (the three routines saved before) | "Saved PPL: 8 weeks from Mon 23 Nov. Push day on Monday, Pull day on Wednesday and Legs day on Friday." |
| "What's my workout today?" | `get_routines` | On a Wednesday: "PPL, week 3 of 8. Today: Pull day." with the routine's items and last time. On Sat 21 Nov, a PPL from Mon 2 Nov: "PPL, week 3 of 8. Today is a rest day. Next: Push day on Mon 23 Nov." |
| "Make me an 8 week program, 4 days a week" | The assistant writes it with the person, then `save_routine` once a routine and `save_program` | As above |

## Every text a model or a person reads

Each passes `tests/text_rules.py`. Where one fails as built, the builder changes the fewest words and names the change.

### `save_routine` (C5b)

| Part | Text |
|---|---|
| Description | "Saves a workout the person does often under its name, such as Push day, in the order done: each lift with its sets and its reps, a hold's time or a carry's distance, and each cardio activity with its minutes. A weight, distance, incline, speed, heart rate or machine calories only when the person gave one. Send only what the person said: never add an item, a set or a number of your own. Saving the same name again replaces the routine. To save a session already logged, read it with get_log and send each lift with its number of sets, its reps, time or distance, and its heaviest weight." |
| `lifts` | "Each item, in the order done: lifts, holds, carries and cardio. 1 to 20 items, and at most 60 sets in all." |
| `lifts[].exercise` | "The lift or the cardio activity, exactly as list_exercises gives it." |
| `lifts[].sets` | "How many sets of a lift, hold or carry, 1 to 10. Leave out for cardio." |
| `lifts[].reps` | "Reps in each set, a whole number from 1 to 100. Leave out for a hold, a carry or cardio." |
| `lifts[].time_s` | "A hold's time in each set, or a carry's when the person gave one, in seconds, 1 to 3,600." |
| `lifts[].distance_m` | "A carry's or a sled's distance in each set, in metres, 1 to 5,000." |
| `lifts[].minutes` | "A cardio activity's minutes, 1 to 600." |
| `lifts[].distance_km`, `distance_mi`, `incline_pct`, `speed_kmh`, `speed_mph`, `avg_hr`, `machine_kcal` | `log_cardio`'s own words for each, with "only when the person gave one" |
| `weight_kg`, `weight_lb` | Today's words, with "a hold or a carry" beside "a lift" |

### `get_routines` (C5a, then C5b, then C5c)

| Part | Text |
|---|---|
| Description, deploy 1 | Today's, with one sentence after "with each change the person names": "When the person says same as last time, send last's sets as given." |
| Description, deploy 2 | "The person's saved routines and their program. Each item has its plan, the weight to log for a lift, and last time's numbers with their date. Call it when the person names a routine, such as log my push day, or asks what today's workout is. To log a routine, send each item with the function its log_with names: lifts, holds and carries with log_lifts, one item a set with exact true; each cardio item with log_cardio. Send each change the person names, and leave the date out for today. Send each weight_kg and distance_km as given, whatever the person's units. Where weight_kg is null, ask the person, and send the answer in the field of its unit. When the person says same as last time, send last's sets as given. Say in your reply what was logged." |
| `lifts[].last` | "What was done last time, with its date: every set of this lift in its latest session, or this activity's latest session. Null when never logged." |
| `lifts[].log_with` | "The function that logs this item: log_lifts or log_cardio." |
| `program` | "The person's program, or null when none is saved: this week's number, today's routine, the next one and this week's days." |
| `program.today` | "Today's routine by name, or null on a rest day." |
| `program.words` | "Where the program stands, in one line, ready to quote." |

### `save_program` (C5c)

| Part | Text |
|---|---|
| Title | "Save a program" |
| Description | "Saves the person's training program: the routine for each day of the week, in blocks of weeks that repeat the same days, such as 8 weeks of push on Monday, pull on Wednesday and legs on Friday. Each day names a routine already saved: save the routines first with save_routine. Saving a program replaces the one there. Send only what the person said or agreed to." |
| `name` | "The program's name, as the person says it, such as PPL. 1 to 64 characters." |
| `start_on` | "The day week 1 starts, YYYY-MM-DD. Leave out for today." |
| `blocks` | "The weeks in order, 1 to 52 weeks in all. A block is some weeks that repeat the same days." |
| `blocks[].weeks` | "How many weeks this block runs." |
| `blocks[].days` | "The training days of each week in this block, each weekday at most once." |
| `days[].weekday` | "monday to sunday." |
| `days[].routine` | "A saved routine's name, as get_routines gives it." |
| Result `id` | "The program's id, for delete_entry and restore_entry with kind program." |

### Texts that change (C5c)

| Where | New |
|---|---|
| `delete_entry`'s `kind` | "... Or saved_food, routine or program." |
| `restore_entry`'s `kind` | "... Or routine or program, to bring back a deleted one." |
| The id of both | "... a routine's from save_routine or get_routines, the program's from save_program or get_routines." |
| `delete_entry`'s first sentence | "Deletes one of the person's entries, saved foods, routines or their program, in two calls." |
| Settings' two switch lines | "routines" becomes "routines and programs" where they list what an assistant saves and deletes |
| `docs/can-and-cant.md` | "What it records": routines with cardio and timed items, and the program. The "Routines" section: the four kinds, last time, logging with two functions, the program and its limits. The function table: `save_program`, and the count |
| The export README | `routines.csv` names the four item shapes; `programs.csv`: "your program: blocks holds its weeks as JSON, each with weeks and days, a day a weekday and a routine's name. start_on is the day week 1 starts" |
| `README.md` | The count, and a line on programs beside routines |

### Summaries and errors

| When | Text |
|---|---|
| `save_program`, new | "Saved PPL: 8 weeks from Mon 23 Nov. Push day on Monday, Pull day on Wednesday and Legs day on Friday." Several blocks: "Weeks 1 to 4: ... Weeks 5 to 8: ..." |
| A replace | "Replaced your program with PPL: ..." A deleted one: "Brought back and replaced PPL: ..." The same again: "PPL is already saved as given: nothing changed." |
| `program.words` | "PPL, week 3 of 8. Today: Pull day." / "PPL, week 3 of 8. Today is a rest day. Next: Push day on Mon 23 Nov." / "PPL starts on Mon 23 Nov. First: Push day." / "PPL ended on Sun 17 Jan, after 8 weeks." The `get_routines` summary leads with it |
| `unknown_routine`, new | "Block 1, Wednesday: 'pul day' is not a saved routine. Saved: Push day, Pull day and Legs day." / "Send a name as get_routines gives it, or save the routine first with save_routine." |
| `invalid_field` | "Block 2: wednesday is there twice." / "Give each weekday once in a block." "A program runs 1 to 52 weeks, not 60." / "Send fewer weeks." "Block 1, day 2: 'weds' is not a weekday." / "Use monday to sunday." |
| The date | The existing date rule's codes and words |

## The page, `/routines`

| Piece | Words |
|---|---|
| The program card (C5c) | h2 the program's name; "Week 3 of 8, from Mon 2 Nov."; a table of this week's days, Day and Routine, today's row marked "Today"; "Next: Push day on Mon 23 Nov."; Delete, with the Undo strip after |
| A routine card | h2 the routine's name, Delete beside it. A table with columns Item, Plan, Last time. A row: "Back Squat", "4 × 5 at 92.5 kg", "Wed 11 Nov: 92.5 kg × 5, 92.5 kg × 5, 87.5 kg × 6"; "Plank", "3 × 60 s", "Wed 11 Nov: 60 s, 60 s, 45 s"; "Rowing Machine", "10 min", "Wed 11 Nov: 10 min, 2.2 km" |
| Never logged | "Not logged yet." |
| Left out | The line `get_routines` gives |
| Under the cards | "Each weight is the one your assistant would log today: your latest session's, or the routine's own when it was saved on a later day." |
| None yet | Today's empty card |

A phone shows each row stacked by `data-label`, as Foods' table does. The design rules the builder's brief names are read before the page is built.

## The migration (C5c)

`migrations/versions/0016_program.py`, `revision "0016"`, `down_revision "0015"` (C3's; the builder reads its file after C3 merges). Up creates `program` with the columns of R8, the foreign key `fk_program_user_id_user` (`ON DELETE CASCADE`) and `uq_program_user_id`. No number column, so no rule of `0012`'s kind. Down drops it. No other table changes. Up and down on SQLite by the builder; up, down, up over a written row on a local Postgres, by his okay (`CLAUDE.md`).

## The eval run

### Cases

| Case | Package | Seed | Says | Passes when |
|---|---|---|---|---|
| `routine_items_saved` | C5b | None | "Save my legs day: 10 minutes on the rowing machine to warm up, back squat 4 sets of 5, plank 3 times 60 seconds, then 20 minutes incline treadmill walk at 12 percent" | One `save_routine`: Rowing Machine minutes 10; Back Squat 4 × 5; Plank 3 sets, `time_s` 60; Incline Treadmill Walk minutes 20, `incline_pct` 12; no weight sent. No log call. One routine, `lift_count` 4 |
| `routine_items_logged` | C5b | Legs day: Rowing Machine 10 min, Back Squat 4 × 5 at 100, Plank 3 × 60 s | "Log my legs day" | One `log_lifts` and one `log_cardio`, both ok; `log_cardio` sent Rowing Machine, minutes 10. Today's session `set_count` 7: 4 Back Squat 100 × 5, 3 Plank `time_s` 60. One cardio entry today, minutes 10 |
| `routine_cardio_changed` | C5b | The same | "Log my legs day, but I rowed for 15 minutes today, the rower said 180 calories and my heart rate was 145" | As above, `log_cardio` minutes 15, never 10, `machine_kcal` 180, `avg_hr` 145; the cardio entry minutes 15, `machine_kcal` 180, `avg_hr` 145 |
| `routine_same_as_last_time` | C5a | Push day: Bench Press 3 × 8 at 60, Overhead Press 3 × 10 at 40; a session `{{days_ago:3}}`: Bench 62.5 × 8, 62.5 × 8, 60 × 6, OHP 40 × 10 three times | "Log my push day, same as last time" | One `log_lifts`, never `new_session`. Today's session `set_count` 6. Bench Press sets of 60 × 6: 2 in all (the seed's and today's). The planned path would give 60 × 8 three times: the routine, saved today with a weight, is newer than the session, so its own 60 wins; no 60 × 6 is added |
| `routine_last_time_read` | C5a | Push day; a session `{{days_ago:3}}` with Bench 62.5 × 8, 62.5 × 8, 57.5 × 7 | "What did I do on my push day last time?" | No write. The reply holds 57.5 (a figure only last time has). Reply-judged: weak until the judge rebuild |
| `program_saved` | C5c | Push day, Pull day, Legs day | "I'm starting an 8 week program today: push day on Mondays, pull day on Wednesdays, legs day on Fridays" | One `save_program`: one block, weeks 8, the three days. No `save_routine`, no log. One program, `start_on` `{{today}}`, `week_count` 8. **Core** (below) |
| `program_today_logged` | C5c | Pull day: Barbell Row 3 × 8 at 60; Push day: Bench Press 3 × 8 at 60; a program from `{{days_ago:14}}`, Pull day on `{{weekday:0}}`, Push day on `{{weekday:2}}` | "Log today's workout" | One `log_lifts`. Today's Barbell Row sets: 3, 60 × 8. No Bench Press set |
| `program_rest_day` | C5c | The same routines; Pull day on `{{weekday:1}}`, Push day on `{{weekday:3}}` | "What's my workout today?" | `get_routines` called, no log. The reply names Pull day (tomorrow's). Reply-judged |

The 13 cases that use the routine functions stay as they are and rerun, as their functions' words change: `routine_saved`, `routine_logged`, `routine_changed`, `lb_routine_changed`, `routine_read`, `save_hinge_routine`, `delete_unused_routine`, `hotel_routine`, `old_routine_unclear`, `logged_session_to_routine`, `replace_routine_entirely`, `routine_null_weight_asks`, `lb_routine_save`. With C5c, the 12 other delete and restore cases rerun too: the kind list changes. New cases go last in `evals/cases.json`, after C3's.

**The core.** `tests/test_evals.py:1303-1312` holds that every function on everyone's list is some core case's `must`, and that the core is 101 (`:1312`, `:1685-1686`). `save_program` is new, so `program_saved` joins the core and the core becomes 102: each gate runs one case more. His yes on 101 is still open (the judge rebuild), so this is his pick 19: yes. No C5a or C5b case joins the core: they add no function.

### What it costs

His rule puts every model run at the gate tier: `claude-sonnet-5-5`, and `gpt-6-luna` on Codex. Sonnet's runs in `BUILD_LOG.md` cost $0.08 to $0.13 a run (the rows at lines 1227, 1261, 1302 and 1303), a long message $0.12 to $0.21 (1259, 1298). The new routine cases make three calls, so expect the upper half. 3 runs a case.

| Scope | Cases | Runs | Sonnet | Opus, at $0.69 a case |
|---|---|---|---|---|
| New and changed, without programs | 18 | 54 | about $4 to $7 | about $12 |
| New and changed, with programs | 33 | 99 | about $8 to $13 | about $23 |
| The whole set, without programs | 263 | 789 | about $64 to $104 | about $181 |
| The whole set, with programs | 266 | 798 | about $65 to $105 | about $184 |

These are the CLI's list-price figures, against his Claude plan: no API credits, nothing on Railway. Luna on the routine cases is his Codex plan, and no figure is given. The real figure is said after the run. C3's contract sizes its own run at about 50 cases and about $14 on Sonnet, the same rate; if C3 and C5 release together, one run covers both.

## Work packages

| Package | Deploy | Files | Exit check |
|---|---|---|---|
| **C5a** last time, the page, the read made safe | 1 | `app/services/routines.py`, `app/mcp_schemas.py` (`SavedRoutineLift`, `LiftToLog` optional `sets` and `reps`, `LastTime`), `app/mcp_server.py` (`get_routines`' sentence), `app/web.py` (`routines_page`'s rows), `app/templates/routines.html`, `tests/test_routines.py`, `tests/test_feedback.py` (`PUBLISHED`), `tests/test_web.py` where the page's assertions move, `evals/cases.json` (2 cases), `docs/evals.md` (2 rows) | `last` for a lift never logged, logged once, twice on a day, in a deleted session, a session dated after today, the newest-wins branches all agreeing with `weight_from`'s session. The words in kg and lb. A cardio row, a hold with time, a hold with old reps, an unknown name, a missing key: each left out with its line; a lift of reps with a key it does not know is placed, the key ignored; nothing raises, the page draws, and `delete_entry`'s preview, the delete and `restore_entry` answer with the routine as stored (their `entry` is an open dict, `app/mcp_schemas.py:1016`, `:1033`). An unchanged save of an old routine writes nothing. The statement count pinned. The size of R14 printed and pinned. The page signed in, behind the limit, its text and page rules. Of the published list only `get_routines` and `save_routine` differ. The instructions 4,082. Every touched test file plain and with `-W error` |
| **C5b** cardio and timed items | 2 | `app/services/routines.py`, `app/rules.py` (no new number; a helper if needed), `app/mcp_schemas.py` (`RoutineLiftIn`, the result fields), `app/mcp_server.py` (both descriptions), `app/services/export.py` (the README line), `app/web.py` and `app/templates/routines.html` (the new kinds' rows), `docs/can-and-cant.md`, `README.md`, `evals/cases.json` (3 cases), `docs/evals.md`, `tests/test_routines.py`, `tests/test_routine_items.py` (new), `tests/test_export.py` (the README line), `tests/test_web.py` (the page's rows), `tests/test_feedback.py`, `tests/test_units.py` and `tests/test_mcp.py` (the published entries), `tests/test_sets.py` (the old hold refusal), and the 11 that read `docs/can-and-cant.md` (`test_calllog`, `test_docs`, `test_edit_history`, `test_eval_need`, `test_feedback`, `test_hi_there`, `test_inputs`, `test_mcp`, `test_switch`, `test_text_rules`, `test_units`) | Each kind saved, read, logged by the assistant's calls and read back as `last`; each refused shape with `log_lifts`' or `log_cardio`'s code and words; a cardio item's `avg_hr` and `machine_kcal` kept, out of range refused as `log_cardio` refuses them; miles stored as km; a hold in lb; a cardio item's sets refused; 20 items with cardio taken, 21 refused; 60 sets with cardio beside them taken. An unchanged save writes nothing, for every kind. The rollback: deploy 1's read over deploy 2's rows. `log_with` right for every kind. Every touched test file plain and with `-W error` |
| **C5c** the program | 2 | `migrations/versions/0016_program.py` (new), `app/models.py` (the model, the header), `app/rules.py` (the 52-week cap), `app/services/programs.py` (new), `app/services/routines.py` (the `program` field), `app/services/__init__.py`, `app/services/common.py`, `app/services/entries.py`, `app/services/account.py`, `app/services/export.py`, `app/services/fields.py`, `app/mcp_server.py`, `app/mcp_schemas.py`, `app/calllog.py`, `app/web.py` (the card, its delete and restore, `DELETE_COUNTS`, the Settings lines), `app/templates/routines.html`, `app/links.py` if a link is new, `docs/can-and-cant.md`, `docs/evals.md`, `README.md`, `evals/cases.json` (3 cases), `evals/judge.py`, `evals/run.py`, `tests/test_programs.py` (new), `tests/test_data.py` (`OTHER_TABLES`), `tests/test_export.py`, `tests/test_isolation.py`, `tests/test_schema.py` (`:164`, the head), `tests/test_trace.py`, `tests/test_web.py`, `tests/test_evals.py` (the core and the counts), the 11 readers of `docs/can-and-cant.md`, and every count and head pin (R13) | Each week and state on fixed todays and every weekday through the clock's seam; a rest day; the next across a block's end; a deleted routine named; a program before its start and after its end. The 52-week cap; a weekday twice; an unknown routine with the live names. One a person: a replace, a deleted one brought back, the same again writing nothing, two saves at once. Delete through MCP in two calls, restore, the switch at `log` refusing; the page's Delete and Undo. Export, account delete and its count, the trace, isolation, `OTHER_TABLES`. `{{weekday:N}}` on every weekday. Migration up and down on SQLite. 25 and 28 functions and 15 writes (plus C3's) wherever stated. Every touched test file plain and with `-W error` |

Order: C5a; its deploy; then C5b, then C5c, one commit each; then deploy 2 and the eval run. C5b before C5c: both change `routines.py`, `mcp_server.py` and `mcp_schemas.py`.

## Files C3 also touches

Read from C3's contract, `docs/contracts/macros-and-asking.md` (untracked, 2026-11-21), its "Packages" and "C5: the shared files". C3 adds no function and no table (its saved meal lives in `saved_food`), leaves the setup text alone unless a case misses, and changes `log_lifts`' words only on a measured miss, in its P6, after C5's join. Each row is a join by hand, not a known conflict.

| File or pin | C5's change | C3's change | The join |
|---|---|---|---|
| `app/mcp_server.py` | The two routine descriptions; `save_program`; the kind and id texts; `NEEDS`; the notes' counts | `log_food`, `save_food`, `find_food`, `edit_entry`, `update_settings` | Different functions |
| `app/mcp_schemas.py` | `RoutineLiftIn`, the routine results, `LastTime`, the program models | `FoodEntryIn`, the food and settings results | Different models |
| `app/models.py` | `Program`; the header's notes | Food, saved food, USDA and user columns; the header's notes | Different classes; the header by hand |
| `migrations/versions/` | `0016` on `0015` | `0015` on `0014` | C5c builds after C3's merge |
| `app/rules.py` | The 52-week cap | `NUMBERS`, the portion and item checks; P6's words on a miss | Different blocks |
| `app/services/common.py` | `WRITE_FUNCTIONS`, `KIND_LABELS`, `READ_BACK`, `ENTRY_OR_FOOD` | `put_back`'s null rule | Different lines |
| `app/services/entries.py` | `_deletable` and the program's fixes | Food edits and undo | Different branches |
| `app/services/export.py` | `programs.csv`, the README lines | The food notes, the account columns | The README by hand |
| `app/services/account.py` | `OWNED` | The three amounts (its 2A) | Different lines |
| `app/services/fields.py` | `OTHER_TABLES["program"]` | The food area's three fields | Different lines |
| `app/calllog.py` | `TABLES`, `KNOWN_NAMES`, the input models | `KNOWN_NAMES` | One set, by hand |
| `app/web.py` | The Routines page, `DELETE_COUNTS`, the Settings switch lines | Food pages, Today, the Settings amounts | Different routes and blocks |
| `app/static/style.css` | None planned (decision 14); a rule only if the builder names one | The food pages | By hand, if any |
| `app/templates/` | `routines.html` only | `day.html`, `edit.html`, `foods.html`, `settings.html`, `_parts.html` | Disjoint |
| `app/services/__init__.py`, `app/links.py`, `app/services/routines.py`, `app/services/programs.py` | The program service; a link if new | None | C5 only |
| `docs/can-and-cant.md`, `README.md` | The routine and program parts, the counts | The food sections, the photo line | Different sections; the 11 readers of can-and-cant rerun after the join |
| `docs/evals.md`, `evals/cases.json`, `evals/judge.py`, `evals/run.py` | Cases after C3's; the program table; `{{weekday:N}}` | New cases; the judge's food columns; `item_count` for `saved_food` | The counts by hand |
| `docs/contracts/data-on-request.md`, `log-and-guide.md` | A dated note each, if owed | A dated note each (C3's decision 1, its food area) | Notes at the foot, one each |
| `BUILD_LOG.md` | One entry | One entry | One each |
| The head pins | `0015` to `0016`: `tests/test_schema.py:598`, `test_edit_history.py:1355`, `test_feedback.py:215`, `test_hardening_schema.py:38`, `test_routines.py:1252`, `test_switch.py:511`, `test_units.py:820` | `0014` to `0015`, the same lines | The join takes the higher |
| The published entries | `get_routines`, `save_routine`, `delete_entry`, `restore_entry`, `save_program` in `tests/test_feedback.py:69` `PUBLISHED`, `tests/test_units.py`, `tests/test_mcp.py` | The food functions' entries in the same places | Different keys, recomputed at the join |
| Pins C5 alone moves | `tests/test_mcp.py:253` `FUNCTIONS`, `test_progress.py:453`, `test_trace.py:234`, `test_routines.py:858`, `test_schema.py:164`, `test_sixteen_weeks.py:299`; the core 101 to 102 (`tests/test_evals.py:1312`, `:1685-1686`) | None: no function, no table | C5 only |
| The case counts and order | `tests/test_evals.py:26`, `:66`; the case-order pins in `tests/test_inputs.py`, `test_hi_there.py`, `test_routines.py`, `test_units.py` | The same | C5's cases after C3's |
| Other test files both edit | `tests/test_web.py`, `test_export.py`, `test_data.py`, `test_calllog.py`, `test_docs.py`, `test_hi_there.py`, `test_inputs.py`, `test_feedback.py`, `test_units.py`, `test_mcp.py`, `test_evals.py` | The same, and more for the portion on every estimate | Line edits in different tests |
| The setup text | None: 4,082 | None by default; a reword at the same length on a miss | Its hash pin moves only if C3 rewords |

## Known limits

| Limit | Why it stands |
|---|---|
| A rotation not tied to weekdays is not followed; a missed day does not move the plan | Design (A). (B) is the way, at the cost written there |
| One program a person; a replaced schedule is not kept | Open decision 3 |
| Weeks that differ in sets or reps take a routine each: a 4-week wave of 4 days is 16 of the 20 routines | The cap stays 20 until use says otherwise |
| One value an item: 8, 8 and 6 reps, or 60, 45 and 30 s, is the item twice or one value | As routines today |
| No intervals (run 1 min, walk 90 s, 8 times), no rest times, no supersets | Not asked for; an interval needs a new cardio shape |
| "Last time" is each item's own latest session, so two routines holding bench show the same last time | Design weighed above |
| A hold with no weight asks, a plank included, until his null-weight pick | Decision 8 |
| A rollback past deploy 1 after a cardio item is saved breaks `get_routines` and the page | The two deploys section |
| `gpt-6-luna` sometimes asks for the exercises instead of reading the saved routine (`routine_logged`, 8 of 12 right after the exercise list's second deploy). Not fixed here; the release run measures it again | One round per AI fix, by a field where the model looks. `log_with` and the program are such fields, not a fix of it |
| A chat begun before a deploy keeps the old list | As every function change |

## Not in this work

Progression rules (next weight and reps worked out by Spotter). A session marked with its routine. A routine's own history. A form that saves or logs a routine or a program. The program on Today or in `whoami`. Watts and level, logged or in a cardio item: C6, his item with its own migration; once C6 stores watts, a routine's cardio item takes them as it takes `avg_hr`. Routines or programs in the import. Pounds per item beyond today's. The Routines page's tab, which is C2's.

## Dated notes owed

Written at the release, at each contract's foot.

| Contract | Note |
|---|---|
| `routines.md` | C5: a routine holds holds, carries and cardio; `last`; "Cardio in a routine. Supersets, rest times, timed holds" of "Not in this work" partly done; a schedule now by a program |
| `exercise-list.md` | R10 reversed for routines: holds, carries and sleds are saved; the left-out line stays only for a hold saved with reps before `0013`; `SavedRoutineLift.reps` and `LiftToLog.reps` optional from C5's deploy 1 |
| `log-and-guide.md` | Its rows 4 and 16 meet C5's texts: whichever lands second rewrites `get_routines`' sentence on the log functions; row 16's refusal no longer exists |
| `data-on-request.md` | `OTHER_TABLES` gains `program`, if `tests/test_data.py`'s read of it needs a line |

## His picks

Taken 2026-11-21: every pick as written below, but 7, which he changed ("If they mention it, let's have it ... store it as an optional input rather than a required one").

| # | Decision | Pick |
|---|---|---|
| 1 | Programs in C5 (package C5c, deploy 2), or a later item | In C5. Nothing else depends on it; dropped, C5 needs no migration |
| 2 | A program's shape: (A) a weekday schedule in blocks of weeks, or (B) a rotation that moves on when a routine day is logged | (A). (B) changes `log_lifts`, `log_cardio` and the sessions |
| 3 | One program a person, replaced by a new one, or several by name with one live | One |
| 4 | `save_program` as a new function, the program read in `get_routines` | Yes |
| 5 | The program's limits: 1 to 52 weeks (a cap), each weekday once a block, a start no more than two years back and no more than 52 weeks ahead (the same cap) | As written |
| 6 | Cardio and timed items in the `lifts` list, in order | Yes |
| 7 | A cardio item: minutes, and distance, incline, speed, heart rate and the machine's calories when given | His change: heart rate and machine calories kept when given. Watts stays in C6, which stores it first |
| 8 | A timed item: a hold's sets and time, a carry's sets and distance and time when given, the weight by the newest-wins rule | As written |
| 9 | A hold or carry with no weight: null and ask, as a lift, until his null-weight pick | That |
| 10 | "Last time": each item's own latest session, not the routine's last date | Each item's own |
| 11 | "Same as last time" logs last time's sets | Yes |
| 12 | A mixed routine: logged straight away as planned with each change named, by `log_lifts` and `log_cardio`; no one-call routine log | Yes |
| 13 | Two deploys: the read side first, the write side a day or more later, his word for when | Yes |
| 14 | No eval run at deploy 1; the one run at deploy 2 reads both | Yes |
| 15 | The run: new and changed cases on Sonnet, 3 runs, plus the routine cases on Luna; or the whole set | New and changed, plus Luna |
| 16 | The page: a card a routine with Item, Plan and Last time, the program card above, no new form | Yes |
| 17 | The log rework: C5 does not wait for it; whichever lands second rewrites the shared sentence | Yes |
| 18 | C5c builds after C3 merges; C5a and C5b build beside C3 | Yes |
| 19 | `program_saved` joins the core, so the core is 102 (the test needs every function in a core case) | Yes, if programs stay in |

## Dated notes

**2026-11-21, the order after log-and-guide.** His word the same day: log-and-guide (`docs/contracts/log-and-guide.md`, on its own branch) is built before the rest of C5. Pick 17 changes for C5b and C5c: they wait for log-and-guide's deploy 1, where `log` comes in; its deploy 2, which drops the five, is not waited for. C5a is built and parked on branch `c5-routines`, also behind log-and-guide's merge: it touches the log functions only in one sentence of `get_routines`' description and its two cases' calls. Log-and-guide adds no migration, so C5's `0016` still follows C3's `0015`.

Read again at the pickup, against the code as log-and-guide leaves it; no new text is written here, as part of `log`'s shape is untested:

| Row | Why |
|---|---|
| Decision 13, R7 `log_with` | A result is read by chats on both lists, so it names a function from neither: `log_with` gives the kind word, `lifts` or `cardio` |
| R5's cardio field words | Taken from the shared constants log-and-guide adds, never a copy |
| The published hashes | Recomputed from the built server after the rebase, never merged by hand |
| His open question | Whether `save_routine` takes `minutes`, `distance_km` and `distance_mi` for a hold or carry, as `log` will, or only `time_s` and `distance_m`. The pick: the forms `log` takes, converted to the kind's one stored form before the store, so the unchanged-save check holds; checked at the pickup, as log-and-guide calls that cross-form part untested. 2026-11-22: decided, the forms `log` takes (the note of that date) |
| R5 | Its refusals are `log_lifts`' and `log_cardio`'s codes; they become those `log` gives, for a lift part and a cardio part |
| `save_routine`'s and `get_routines`' descriptions | Fixed texts: from deploy 1 they name `log`, never the five (log-and-guide row 4) |
| The C5a sentence in `get_routines`' description | Joined by hand with log-and-guide's row 4 |
| The eval cases' pass lines | Every `log_lifts` and `log_cardio` call becomes `log` with its kind. 2026-11-22: overruled by his call, the cases keep the five's names (the note of that date) |
| R13, decision 17's counts, the core | Log-and-guide moves the function count, the writes and the core's must lists |
| Decision 21, R13, the counts | The log-and-guide chat gives, after its merge: 21 functions listed, 24 for the owner, 15 writes, the core 103, the instructions 4,084. Not checked here: read off the code at the pickup, never written by hand |
| "Files C3 also touches" | Gains a log-and-guide column, from its packages L1 to L3 |

**2026-11-21, C5a as built.** The rulings the main chat gave the builder, which overrule the rows above for C5a:

| Row | As built |
|---|---|
| R2, an off-list name | An item whose name is not on the list, a renamed lift's old name included, is placed as today's code places it (read as reps). Only a listed cardio row or a listed lift that does not record reps is left out |
| R2, the cardio line | "Rowing Machine is left out: log it as cardio.": no new text names `log_lifts` or `log_cardio`. The two new cases' calls name `log_lifts` until the rebase |
| R3 | The weight to log and last time come from one statement, not two, so they are always the same session; `tests/test_hardening.py` keeps its count |
| `get_routines`' description | The C5a sentence is its own source line, after the last, so log-and-guide's rewrite of the sentence on the log functions merges by line |
| C5a's files | Add `app/services/entries.py` (a routine with nothing placed previews as its name alone), `tests/test_inputs.py` (the hashes), `tests/test_evals.py`, `tests/test_golden_cases.py` (the case counts and `RUNS`), `tests/test_sets.py` and `tests/test_units_pages.py` (the page's Item and Plan cells), and `app/static/style.css` for two named rules (decision 14): `.card-head h2 { overflow-wrap: anywhere; }`, so a long name with no space wraps, and `.saved-foods table.rows td.wide-cell { grid-column: 1 / -1; }` on a phone, so the Plan cell spans its row |
| The review's eval fix | `routine_same_as_last_time` also counts 4 Bench Press sets of 62.5 × 8, so a run that logs the plan's weight with last time's reps, or one set short, fails |
| R14 | Printed and pinned by the builder against today's figure; no cap. A Postgres run of the new statement is owed before deploy 1, by his okay |

**2026-11-22, the rebase onto `9950f13`.** C5a, built on `3b2441e`, is rebased onto `9950f13`, where log-and-guide is merged. Git stopped in 7 test and eval files, 10 blocks; every app file merged clean. His rulings of that day, which overrule the rows above where they differ:

| Row | As decided |
|---|---|
| The eval cases' pass lines | His call: C5a's two cases keep the five's names, `log_lifts` in `must` and `ok_calls`, as log-and-guide's own cases do. The judge routes a `log` call to its kind's function. `routine_last_time_read`, which asks for no write, lists `log` in `must_not` and `no_call`. This overrules the row above that says the calls become `log` |
| The C5a sentence in `get_routines`' description | Its own line after log-and-guide's sentence, no other line changed: "When the person says same as last time, send last's sets as given, each with the lift as movement." `last.sets` carries no `movement`, so the line names it |
| The counts after the rebase | 21 functions listed, 24 for the owner, `NEEDS` 29, 15 writes, the instructions 4,084. 262 cases, 103 core, the package 191, direct 143, core direct 63 |
| His open question | Decided: `save_routine` takes a hold's or carry's time as `time_s` or `minutes`, and a distance as `distance_m`, `distance_km` or `distance_mi`, the forms `log` takes. Each is converted, before the store, to the kind's one stored form: `time_s` and `distance_m`. So saving an unchanged routine still writes nothing. For C5b |
| C5b, `log_with` | Gives the kind word, `lifts` or `cardio`. Its fixture value in `tests/test_routines.py`, "log_lifts", becomes "lifts" |
| C5b, deploy 2's description | Written in `log`'s words: one `log` of kind lifts for the lifts, holds and carries, one `log` of kind cardio for each cardio item |
| C5b, the cardio field words | Imported from the `CARDIO_*_DESCRIPTION` constants in `app/mcp_schemas.py`, never a copy |
| C5c, the counts | With `save_program`: 22 listed, 25 for the owner, `NEEDS` 30, 16 writes. The `== 21` in `tests/test_routines.py` becomes 22 |
| C5c, likely hand merges | `WRITE_FUNCTIONS` in `app/services/common.py`, `KNOWN_NAMES` in `app/calllog.py`, and `KIND_LABELS` |

**2026-11-22, C5b redrawn after C5a went live (`fcba0e8`).** Read against `main` at `fcba0e8`, where `log` and `guide` stand. His go the same day. These rows overrule the C5b rows above wherever they differ; a row not named here holds as written.

| Row | C5b as it is built |
|---|---|
| Kinds and the stored shape (decisions 4 to 6) | As decision 6, with cardio `{exercise, minutes, distance_km, incline_pct, speed_kmh, avg_hr, machine_kcal}`, nulls kept, miles stored as km and mph as km/h as `log_cardio` stores them. A lift of reps stays exactly `{exercise, sets, reps, weight_kg}` |
| The forms a save takes (his pick 2026-11-22, and the main chat's) | A hold's or carry's time as `time_s` or `minutes`, a distance as `distance_m`, `distance_km` or `distance_mi`, stored as `time_s` and `distance_m`. A cardio item's time as `minutes` or `time_s`, a distance as `distance_km`, `distance_mi` or `distance_m`, stored as `minutes` and `distance_km`: the forms `log` takes for a lift part and a cardio part. Converted by one public entry in `app/log_shape.py`, the place passed in, so the whole-second rule, the two-forms refusal and the as-sent wording of a converted number's range refusal are `log`'s own; `log`'s own texts stay byte for byte |
| `RoutineLiftIn`'s fields and words | `exercise`, `sets`, `reps`, `weight_kg`, `weight_lb` as today; `time_s`, `minutes`, `distance_m`, `distance_km`, `distance_mi` with the `PART_*_DESCRIPTION` words, as a hold and a cardio item both take them; `incline_pct`, `speed_kmh`, `speed_mph`, `avg_hr`, `machine_kcal` with the `CARDIO_*_DESCRIPTION` words. Each imported, never copied. `exercise`, `sets`, `reps` take the words of the `save_routine` table above. Only `exercise` is required |
| R5, the checks | Each kind through its log function's own checks, the signatures of `rules._set_numbers`, `_set_weight` and the cardio checks unchanged. A refusal reads "Lift n: ..." and its fix "on lift n", worded after the check as `log` words its parts. A cardio name is matched on the cardio list, a lift on the lifts; a name on neither is refused with both lists' near names. `sets`, `reps` or a weight on a cardio item: R5's own `invalid_field` lines. A hold sent with reps is refused as `log` refuses it ("records time, not reps"), so today's "a routine holds lifts of sets and reps" refusal goes |
| R7, the items read | `get_routines`' items gain `time_s`, `distance_m`, `minutes`, `distance_km`, `incline_pct`, `speed_kmh`, null where the kind has none, and never `avg_hr` or `machine_kcal`: a plan's heart rate and machine calories are said in the item's words and on the page only, so no field offers them as numbers to log, and `log_with`: the kind word `lifts` or `cardio`, never a function name. A cardio item's `weight_kg`, `weight_from`, `last_date`, `sets` and `reps` are null. `SavedRoutineLift`, `LiftToLog` and `LastTime` gain these as optional fields: no required field is added or removed |
| R3 and R4, cardio's last | A fourth statement, run only when a routine places a cardio item: each activity's latest live entry by effective date then id. `last` for cardio `{date, minutes, distance_km, incline_pct, speed_kmh, words}`; the session's heart rate and machine calories are in its words only, by R4, so "same as last time" never copies them as today's. A hold's and a carry's `last` come from the statement there is |
| Decision 7, logging cardio | As planned: minutes and distance as the part, incline and speed in `details`. A heart rate or the machine's calories go in only when the person gives them that day, never the plan's or last time's |
| "Same as last time" for cardio | `last`'s minutes, distance, incline and speed, as given; no heart rate or machine calories unless the person says them. The deploy-2 description's "same as last time" line covers both: lifts send last's sets, cardio last's numbers |
| Name matching | A name is matched on the lifts first, exactly as today; only a name that reaches no lift and is not a plain word of the lifts (which is refused as today) is matched on the cardio list. A miss on both is refused with near names from both. Every name `save_routine` takes today resolves to the same lift |
| `get_routines`' description, deploy 2 | The C5a text with the fewest words changed: the lifts' sentence names holds and carries, one sentence says each cardio item goes with `log`, kind cardio, as its `log_with` says, and the first sentence names last time. The "same as last time" line stays its own line. Names `log` and `guide` only, never the five. Passes the text rules; its characters are printed in the hand-over |
| `save_routine`'s description | The table's text above, which names no log function |
| The page | R6's words for each kind; cardio's last time as R4 |
| The eval cases | The three C5b cases as above, with two changes: the Plank in the seed of `routine_items_logged` and `routine_cardio_changed` holds `weight_kg` 0 (decision 8: no weight asks), so its three rows are logged at 0; and the seed's Rowing Machine plan holds `avg_hr` 140 and `machine_kcal` 120, which `log_cardio`'s inputs must not carry in `routine_items_logged` (`inputs_forbid`). Each case has a failing `RUNS` entry for the wrong run it exists to catch: the plan's heart rate logged, minutes 10 when 15 was said, a hold logged with reps, one log call where two are needed. Each case has `ok_calls` naming what must succeed (`{"log_lifts": 1, "log_cardio": 1}` for the two logging cases) and `log_cardio` out of `must_not` and `no_call`. `expect.function` `log_cardio`, its `inputs_include` reading `minutes`, `avg_hr` and `machine_kcal`; the lifts judged by their `lift_set` rows. `must` keeps the old names (his ruling of 2026-11-22). They go after C5a's two |
| The files | The C5b row's, less `tests/test_export.py` (the README line is pinned in `tests/test_routines.py`), plus `app/log_shape.py`, `tests/test_log_and_guide.py`, `tests/test_inputs.py`, `tests/test_evals.py`, `tests/test_golden_cases.py`, `tests/test_hardening.py` (the statement count), `tests/test_matcher.py` (a description substring). `app/log_shape.py` joins the C3 overlap table: C3 does not list it |
| Counts | No function, write or core case is added. The case counts move by three, derived where the files already derive them. The setup text stays 4,084 |
| Before deploy 2 | A Postgres run of the cardio statement, by his okay, as C5a's. The release eval at deploy 2 (his pick 15): C5b's three cases and the 13 routine cases that rerun, on Sonnet, then the routine cases on `gpt-6-luna` |

**2026-11-22, C5b as built.** The builder's departures, each ruled by the main chat to stand:

| Row | As built |
|---|---|
| Near names on a miss | By the item's fields: lift fields give the lifts' near names, cardio fields cardio's first, a bare name both. A name that reaches no row is checked as cardio or a lift by its fields |
| `get_routines`' description | Also carries decision 7's clause, "with a heart rate or the machine's calories only when the person gives them today", and says "what was logged" for "which sets were logged" |
| A worked-out speed | `cardio.with_speed` refuses a routine's cardio numbers that `log` would refuse (decision 5) |
| Texts the rows did not give | "Lift n: X is a lift: it takes sets and reps, not avg_hr." with "Leave avg_hr out on lift n."; the plan's words ", heart rate 140 bpm, the machine said 120 kcal"; "at 0 kg" on a hold, as on a lift of reps; the both-units fix as `rules.both_sent` words it |
| A conversion refusal | Comes back alone for its item, as `route` does |
| Stored forms | A cardio time in seconds is kept as minutes at 1 dp, as `log_cardio` keeps it (61 s is 1.0) |
| The input schema | `RoutineLiftIn` requires only `exercise`; the time and distance fields carry the `PART_*` words |
| The page | `page_read` also returns each item's plan words |
| C3's overlap | `app/log_shape.py` joins "Files C3 also touches" |
| `routine_resave_keeps_plan` | A fourth case measures whether a resave keeps a plan's heart rate and machine calories, which `get_routines`' fields do not carry; a miss goes to the owner's gaps list, no text fix |

**2026-11-23, C5c redrawn after C3 went live (`10d37e0`).** Read against `main` at `10d37e0`, where log-and-guide, C5a, C5b and C3 (`0015`) stand. His go the same day. C5c is its own deploy, after C5b's. These rows overrule the C5c rows above wherever they differ; a row not named here holds as written.

| Row | C5c as it is built |
|---|---|
| Counts | With `save_program`: `NEEDS` 30, 22 listed for everyone, 25 for the owner, 16 writes (`common.WRITE_FUNCTIONS` and the judge's copy). Cases 284, the package 213, the core 104 (pick 19's 102 counted before log-and-guide's and C3's core cases). `get_routines` runs one statement more, always: 4, or 5 with a cardio item. The setup text stays 4,084. `README.md` and `docs/can-and-cant.md` state no function count: only the function table's row and a README line beside the routines line change |
| `get_routines`' description | C5b's text with the fewest words changed: "and their program" in the first sentence, "or asks what today's workout is" where it says when to call it. Names `log` and `guide` only. `log_with` stays as C5b built it |
| `program` in `get_routines` (R10) | An optional field, present only when a program is saved, left out otherwise, so every answer for a person with no program is today's. Its words: "The person's program, present only when one is saved: this week's number, today's routine, the next one and this week's days." One deploy: no required field is added |
| The states (R10) | Running: `week`, `today` (null on a rest day), `next`, `this_week` the 7 days of the program week. Not started: `week`, `today` and `this_week` null, `next` the first day with a routine. Ended: `week`, `today`, `this_week` and `next` null. Each state has its line in `words` |
| The function's place (R11) | `save_program` right after `save_routine` in the list, the two writes together; `names[-3:]` in `tests/test_data.py` holds |
| `days[].weekday` (R11) | A string, not an enum: the service checks it, case and spaces forgiven, so "'weds' is not a weekday." is said. No input of the server uses an enum |
| A day's routine | Matched by the routine's name key as `save_routine` folds it, stored as the routine's own stored name; at the read, a day whose routine is not live gives its stored name and the words say it is not saved now |
| `start_on` (decision 18) | Blank is today. Not a date: `invalid_date` naming `start_on`. More than 2 years back: `date_too_old` as `rules.entry_date` words it, naming `start_on`. More than 52 weeks on: `date_too_far` with `field` `start_on` (goals' code), "start_on 2028-01-03 is more than 52 weeks after today, 2026-11-23." / "Give a start at most 52 weeks on: 2027-11-22 at the latest." `rules.entry_date` refuses every future date, so the "existing date rule" row cannot hold |
| The cap | `rules.PROGRAM_WEEKS = (1, 52)`, one constant for the weeks in all and the forward bound, its comment naming it a cap, revisited from use |
| The row (R8) | No `saved_on`: nothing reads it. The rest as R8 |
| Delete preview | `entries.entry_words` gives a program its line, as a routine's, so the person sees words before a yes |
| `save_program`'s result | `{kind: "program", id, created, program {id, name, start_on, blocks, weeks, updated_at}, summary, links {routines}}`, TOP, as `SaveRoutineResult` |
| Kept apart | `save_routine`, `delete_entry` and `restore_entry` of a routine, and `edit_entry` never read the program table: `tests/test_macros_schema.py` writes through them at `0015`. A test helper `before_0016` strips the new table beside `before_0015` for the older migration tests |
| `{{weekday:N}}` | In `evals/run.py`'s dates and the judge's placeholder check; its test passes a fixed today, all seven weekdays, never the real clock |
| The seeded cases | `program_today_logged` and `program_rest_day` seed a program of 4 weeks from `{{days_ago:14}}`, so today is in week 3 |
| Packages | C5c1 the program and its checklist, C5c3 the evals and docs, C5c2 the page, built one after another in the one worktree; one commit for C5c, one review of the whole |
| Before the deploy | A Postgres run, by his okay: `0016` up, down, up over a written row, and a `get_routines` read with a program |

**2026-11-23, C5c as built.** Three packages in order (the program and its checklist, the evals and docs, the page), one review on a frozen copy, one fix round. These rows record what the build, the review and the owner's picks changed; a row not named here holds as the note above says.

| Row | As built |
|---|---|
| `start_on` left out (the owner's pick, the review's S1) | Keeps the saved program's start while it has not ended, so a change keeps its week; today when there is no program, a deleted one or an ended one. Its words: "The day week 1 starts, YYYY-MM-DD. Left out: the saved program's start if it has not ended, else today." This overrules "blank is today" for a change. A new program saved over a running one with the start left out takes the old start, until the person gives the date |
| `program.today`'s words | "Today's routine by name; null on a rest day and when the program is not running." |
| Texts the tables did not give | The builders' own, ruled to stand: a first routine day after the start ("PPL starts on Tue 24 Nov. First: Pull day on Wed 25 Nov."), no routine day left, the block, day and count refusals, the unknown routine with none saved, the delete preview, the Settings lines, the page's "Rest day", "No routine day is left after today.", "Program deleted.", "Program restored." and its stale Undo lines |
| The shared refusal path | An item's words are picked by the innermost list a key was sent in, so "Block 1, day 1: ..." is said; every older nested refusal is byte for byte the same (the review's probe) |
| `get_routines` with no program | The answer is the same field for field; one statement more |
| The old cases | Not left alone, as the scope said: 28 cases that pin `must_not` and `no_call` to every write gained `save_program` there, and nothing else |
| The cases | `program_saved` says "an 8 week PPL program", as `name` is required; it checks `day_count` 3. The two seeded cases' program is "Four weeks", so no reply passes on its name; `program_rest_day` needs "pull day" and refuses a reply that calls today Pull day. A fourth case, `program_day_moved`, measures a mid-way change keeping the start. 285 cases, the package 214, the core 104 |
| The eval runner | `{{weekday:N}}`, `program` with `week_count` and `day_count` |
| The page | The card above the routines, outside the routines' block; the page reads which routines are live by the routines' name key from the same read, and names every routine of this week and the next that is not saved now, more than `get_routines`' words name. No CSS rule: seen at 320 and 390 px |
| Left as they are | A routine resaved under another case shows in the program by its stored spelling; a kept start with fewer weeks can read as ended at once; the `invalid_date` fix still says "or leave it out for today" |
