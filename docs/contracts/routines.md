# Routines contract: a workout saved by name, logged in one sentence

**Goal: a person saves a workout they do often, such as "push day", once, with its lifts, sets and reps. After that, "log my push day" logs it into today's session, with the newest weight Spotter knows for each lift, and "bench was 62.5 today" changes one number on the way.**

Approved by the owner on 2026-11-15, with every pick of its card. It is built after gym logging, on `3fe468b`.

## His words

2026-11-15: "encourage them to save their fixed routines if needed. goals, and aso on. whatever would improve the exp." Earlier: "do we store what the chest day requires or mark it down anywhere?" The standing ones: "a smooth UX", "Most of the days are going to be quick inputs, right? Log and push, not more than that", "Let's not overcomplicate things out of the bag." On undo, 2026-11-14: "undo doesn't make much sense for me unless it's like an immediate undo."

On the card's four questions and its three choices, 2026-11-15: "yeah... lets go with your picks. choices are also cool". So: built next; a logged routine uses the newest weight (the latest session's, unless the routine was saved after it); a Routines page with Delete and Undo and no form, this round; a routine is logged straight away, not shown first; 20 routines, 1 to 20 lifts each, at most 60 sets; no undo of a routine saved again.

## What exists today

Read at `3fe468b` on 2026-11-15: what is live, plus gym logging. The builder reads these rows again on the commit it starts from.

| Piece | State | Where |
|---|---|---|
| A routine | **None.** No table, column or function holds a workout's name or its usual sets | `app/models.py:107-430` |
| A saved food, the model to follow | One table with `name`, `name_key` (casefolded, spaces folded, unique per person, deleted rows included), `deleted_at`. Not an entry table (`EntryColumns`), so no entry kind. `save_food`: lock, write limit, checks, then the same name updates it and a deleted one comes back live. A change keeps a version; a save that changes nothing writes nothing | `app/models.py:334-347`, `app/services/food.py:340-394` |
| How many saved foods a person may hold | No cap | `app/services/food.py` (searched) |
| Delete and restore by id | `entries._deletable()`: the five entry tables and `saved_food`. A delete through an assistant takes two calls: a preview with a code worked out from the account, the kind, the id and `updated_at`, then the delete. Restore clears `deleted_at` | `app/services/entries.py:107-109`, `:175-249`, `docs/contracts/switch.md` decisions 7 to 10 |
| Undo of a change | `restore_entry` with `undo_edit` puts back the newest kept version. With none: `nothing_to_undo`, its fix names `edit_entry` or `save_food` | `app/services/entries.py:252-280` |
| An edit of a saved food | `edit_entry` answers `unknown_kind`: "A saved food is not edited here." | `app/services/entries.py:374-376` |
| Kind labels and read-backs | `common.KIND_LABELS` names each kind and where its ids come from, for `not_found`. `READ_BACK` names the read that shows a write, for `already_done` | `app/services/common.py:89-110` |
| The kind and id arguments | `delete_entry`'s kind: "lift_session, bodyweight, measurement, food_entry or cardio_entry. Or saved_food." `restore_entry`'s: "... With undo_edit, saved_food too." The id of both: "... A saved food's id comes from save_food or find_food." | `app/mcp_server.py:261-267` |
| A set | `exercise`, `weight_kg` (0 to 500), `reps` (1 to 30). kg only. A session holds 1 to 60 sets. `rules.check_session` checks a set: `weight_out_of_range`, `reps_out_of_range`, `unknown_exercise` with the closest names | `app/mcp_schemas.py:26-31`, `app/rules.py:31-35`, `:319-366` |
| Range checks | `rules.bounded` takes only the food, cardio and settings numbers of `rules.NUMBERS`. `rules.required_text` takes its limit from `TEXT_LIMITS` (`name`: 128) | `app/rules.py:57-75`, `:258-288` |
| A lift's last session | `lifts.history` walks every live session in the one order (`metrics.order_key`) and keeps per lift `last` (the real date of the latest session holding it), one estimated max point per session, `best_set`, `bests`. **It keeps no lift's last weight** | `app/services/lifts.py:148-205` |
| Lift logging | Gym logging, built: a date's sets go into that date's session, call by call. `new_session` true starts another. No duplicate guard. 60 sets a session in all, past it `sets_count_out_of_range` with a fix that names `new_session`. Undo takes back the last add | `app/services/lifts.py:245-346`, `docs/contracts/gym-logging.md` |
| A write | Locks the person's row, then the write limit (120 in 10 minutes, the functions of `common.WRITE_FUNCTIONS`), before any other read. Takes the caller's `key`: thirteen write functions | `app/services/common.py:115-117`, `:208-222`, `:363-376`, `app/mcp_server.py:250`, `tests/test_idempotency.py:119` |
| A read | The read limit counts every function at `read` in `NEEDS` | `app/mcp_server.py:227-242` |
| The call log | `tried` writes only `KNOWN_NAMES`, the argument names and the fields of five input models. `TABLES` names seven tables for a kind | `app/calllog.py:58-71` |
| The switch | `log_delete` (the default) or `log`. At `log`, `delete_entry` answers `not_allowed` | `app/mcp_server.py:224-245` |
| The export | One CSV a table, in `ORDER`, and a README.txt naming each file. `name_key` is left out | `app/services/export.py:48-100` |
| Account delete | `account.OWNED` lists the tables counted; the foreign key's cascade takes the rows. The delete page says what goes, by count (`web.DELETE_COUNTS`) | `app/services/account.py:259-282`, `app/web.py:411-415` |
| The import | Five kinds: weigh-ins, measurements, lift sessions, food, cardio | `docs/can-and-cant.md:187-190` |
| "Empty" | `_empty`: no live row in the entry tables, no live saved food, no open goal. The setup work (its own contract, first-day) is built apart from this work and joined after | `app/services/account.py:106-111` |
| The instructions | 3,927 characters | `tests/test_switch.py:545-547` |
| Counts of functions | 20 for everyone. Drawn on the landing (`web.FUNCTION_COUNT`). Pinned in `tests/test_feedback.py:524`, `tests/test_trace.py:231` (23 with the owner's three) and `:468`, `tests/test_web.py:44`, `tests/test_idempotency.py:119` (13 writes). Listed in `tests/test_mcp.py:251`, `tests/test_progress.py:446`. "Twenty functions" in `docs/can-and-cant.md:380`, read by `tests/test_mcp.py:1482`, `:2080` and `tests/test_feedback.py:944`. "twenty" in the notes of `app/mcp_server.py:1`, `:1101` and `app/mcp_schemas.py:1`; "thirteen" at `app/mcp_server.py:250`. "twenty since `send_feedback`" in `docs/evals.md:41`. The README's "20 MCP functions", read by `tests/test_docs.py` | As listed |
| The web pages | Foods: a list with Delete in each row and an Undo strip after ("Saved food deleted."), plus a form that saves a food. Routed `/foods`, `/foods/{id}/delete`, `/foods/{id}/restore`. Settings' "More" card links to it | `app/templates/foods.html`, `app/web.py:1896-2093`, `:2633-2636`, `app/templates/settings.html` |
| Evals | 29 cases. Seeds: `save_food`, `log_bodyweight`, `log_measurement`, `log_lifts`, `set_goal`, `import_entries`, `edit_entry`, `four_weeks`. A seed is called as `service(user_id, data, "web")`. Tables a case can read: eight; a `lift_session` row carries `set_count`. One message a run | `evals/judge.py:74-79`, `:209-220`, `evals/run.py:34`, `:248-270` |

## The designs weighed

| Question | Way | Verdict |
|---|---|---|
| How a routine is logged | (a) `get_routines` reads it, and the assistant sends its sets with `log_lifts`. "Log my push day": 2 calls. "Bench was 62.5 today": the same 2 calls, the bench sets sent at 62.5 | **Chosen.** `log_lifts` does not change, every number is in the call the person's assistant made, and a change is no new rule |
| | (b) `log_lifts` takes a routine's name. 1 call. A change needs a rule for which sets a sent set replaces, and the assistant cannot say the numbers before it logs | Not chosen: one call fewer, a merge rule more, and gym logging's description changes again |
| What a routine holds | (1) Weights, fixed until saved again. Three weeks on, "log my push day" logs three-week-old weights unless the person says each change | Not chosen: wrong numbers, quietly |
| | (2) No weights: each lift's weight from its last session. The first log of a lift never done asks for it, though the person said it when saving | Not chosen: asks again for what was said |
| | (3) A weight only when the person gave one, and the newest wins: the lift's latest session, unless the routine was saved after it | **Chosen by the owner.** Right on the first day and three weeks on. "Bench was 62.5" carries to next week by itself |
| "Save today's workout as push day" | A new argument, or one sentence in `save_routine`'s description: read the session with `get_log` and send it | **The sentence, in this round.** No code |
| Where routines show | (a) Their own page, `/routines`, with Delete and Undo as Foods has; (b) none, ask the assistant | **(a), chosen by the owner** |
| Undo of a replace | A kept version and `undo_edit`, as a saved food has; or none | **None.** His view of undo. Saving it again as it was puts it back |
| Where weights are worked out | In the assistant, from `get_log`; or in Spotter | **Spotter**, in `get_routines`, as every number is |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **One table, `routine`,** migration `0010`: `id`, `user_id` (foreign key to `user`, cascade on delete), `name` (1 to 64 characters), `name_key` (the name casefolded, spaces folded, unique with `user_id`, deleted rows included), `lifts` (JSON text), `saved_on` (the person's date of the last save), `created_at`, `updated_at`, `deleted_at` | One row a routine, as a saved food. `saved_on` is what the newest-wins rule compares, with no time zone sum |
| 2 | **`lifts` holds 1 to 20 items, in order, each `exercise` (a name exactly as on the list), `sets` (1 to 10), `reps` (1 to 30) and `weight_kg` (0 to 500, or null).** At most 60 sets in all. The same lift may appear twice, for a heavy and a back-off set | A logged routine fits one session (`rules.SESSION_SETS`). One reps value a lift is what "3 sets of 8" says |
| 3 | **Names as the list gives them,** checked at save with the rule `log_lifts` uses: an unknown name is `unknown_exercise` with the closest names | Exercise names only from the list. The list only gains rows |
| 4 | **Up to 20 live routines a person.** The 21st is `too_many_routines` | A cap keeps a read short. 20 is more than a weekly split needs |
| 5 | **`save_routine`,** a write at `log`: lock, write limit, checks, then by `name_key`: a new name makes a row; a name held replaces its lifts and `saved_on`; a deleted one comes back live and replaced. The same lifts again write nothing and keep `updated_at`. Takes `key`. Annotated as a setting, as `save_food` is | `save_food`'s pattern, line for line |
| 6 | **No version and no undo of a replace.** `restore_entry` with `undo_edit` and kind `routine` answers `nothing_to_undo` with its own fix | His view of undo. A routine is the person's own words: said again, it is back |
| 7 | **`get_routines`,** a read at `read`: every live routine by name, each lift with the weight to log, `weight_from` and `last_date` | The one read the assistant needs before it logs |
| 8 | **The weight to log, worked out in Spotter:** the heaviest set of the lift's latest live session (the one order, `metrics.order_key`), unless the routine holds a weight for it and that session is dated before `saved_on`, or there is none: then the routine's weight. With neither, null, and the assistant asks | The newest number Spotter was told. A session on the day of the save wins, so "save today's workout" agrees with itself |
| 9 | **`lifts.history` gains one field, `last_top`: per lift, the date and heaviest weight of its latest session.** Worked out in the loop it already runs | No new walk over the sessions. `get_progress` and the pages do not copy it |
| 10 | **Logging a routine is `log_lifts`, unchanged.** Its sets go into today's session; `new_session` only when the person says it is a second workout. No session records which routine it came from | One way to log lifts. Nothing new to keep true |
| 11 | **Delete and restore: `delete_entry` and `restore_entry` take kind `routine`,** through `_deletable`, the two calls and the switch included. The web's Delete deletes at once with Undo, as Foods does | The switch's rule for every delete by an assistant |
| 12 | **A routine does not end "new account".** `_empty` is unchanged. The setup step `routine` reads a live routine | "Nothing is logged" stays true. The hi there tests keep their states |
| 13 | **A page, `/routines`,** behind sign-in and the page limit, the Settings tab, the frame class of Foods. A table: the routine, its lifts with the weights `get_routines` gives, Delete. No form saves or logs one. Settings' "More" links to it | Every other thing a person keeps can be seen. No new stylesheet rule |
| 14 | **Export: `routines.csv`,** after `saved_foods.csv`: `id`, `name`, `lifts` (the JSON as stored), `saved_on`, `created_at`, `updated_at`, `deleted_at`. The README names it. Account delete counts it in `OWNED` and the cascade takes it | The person's data, all of it |
| 15 | **The import does not touch routines.** Its five kinds stay | Not asked for. An export file rarely holds a routine |
| 16 | **The trace.** `routine` joins `calllog.TABLES`. `lifts` and the fields of the new input model join `KNOWN_NAMES` | The owner sees each call as a call |
| 17 | **Every statement on `routine` names the person's id:** by `owned()` for an id, by `user_id` and `name_key` for a save, by `user_id` for the read, the cap, the page, the export and the delete count | Own data only |
| 18 | **The instructions do not change: 3,927 characters.** The words are in the two functions' own text | No room |
| 19 | **22 functions for everyone, 14 writes.** Every count above follows: the drawn one by itself, each pinned one and each written one by hand | The landing's count must stay true |
| 20 | **Setup and `/hi` need nothing more** than the table and the two functions: the `routine` step, the page's step 5 and the keeps line appear by themselves. The joined tree's tests hold that they now do | Setup decision 8 |
| 21 | **An eval run of the whole set follows,** with three new cases | Two functions join every conversation's tool list, and `delete_entry`'s and `restore_entry`'s texts change |

2026-11-15, the join with first day: decision 20 is wired. `whoami` marks the `routine` step done while the person holds a live routine, and `/hi` draws step 5 and the Routines line; the joined tree's tests hold it.

## Rulings on the how

Made by the lead on `3fe468b`, where the decisions leave a how open or the code moved under them. None changes a decision.

| # | Ruling | Why |
|---|---|---|
| R1 | **The base.** Gym logging is built, so decision 10 holds as written. `lifts.history` sits at `app/services/lifts.py:148-205`. The setup work is not in this tree: `whoami`, the setup parts of `app/services/account.py`, `app/templates/hi.html` and `tests/test_hi_there.py` are not touched here, and decision 20 is wired and tested when the two join. 29 eval cases here, so 32 with this work's three | What moved since the decisions were written |
| R2 | **The model.** `Routine(Base)`, not built from `EntryColumns`: it is no entry kind, so `get_log`, the import, the trends, `entry_kinds()` and the duplicate guard never see it. Columns in this order: `id`, `user_id` (`_user_id()`), `name` `String(64)`, `name_key` `String(255)`, `lifts` `Text`, `saved_on` `Date`, `created_at`, `updated_at` (default and onupdate `_now`), `deleted_at` (nullable). `UniqueConstraint("user_id", "name_key")`, named by the naming rule `uq_routine_user_id_name_key`. No other index: the unique one serves every read by `user_id` | Casefolding can make a name longer (one character can fold to three), so the key gets room. The column order is the export's |
| R3 | **The migration.** `migrations/versions/0010_routine.py`, `revision "0010"`, `down_revision "0009"`, notes in the shape of `0009`'s. Up creates the table with the columns, the foreign key `fk_routine_user_id_user` (`ON DELETE CASCADE`) and the unique constraint, named as the model names them. Down drops the table. No other table changes | As every migration here |
| R4 | **The limits live in `rules.py`:** `ROUTINES_MAX = 20`, `ROUTINE_LIFTS = (1, 20)`, `ROUTINE_LIFT_SETS = (1, 10)`, `ROUTINE_NAME_MAX = 64`. The total is `SESSION_SETS[1]`. `rules.required_text` gains an optional `limit` (left out: `TEXT_LIMITS[field]`, as now); no other caller changes | Every limit is read from `rules`, never typed. A routine's name is 64, a saved food's 128 |
| R5 | **The checks of `save_routine`,** every failure at once through `rules.combine`, nothing written. `name`: `required_text(value, "name", limit=64)`, its words. A key of `data` other than `name` and `lifts`: `invalid_field` "'x' is not a field of a routine." / "Use: name, lifts." `lifts` not a list counts as 0 lifts. Outside 1 to 20: `invalid_field` "A routine holds 1 to 20 lifts, not 23." / "Send 1 to 20 lifts.", then the first 20 are checked. Each lift, as "Lift n": a key other than the four, `invalid_field` "Lift 2: 'x' is not a field of a lift." / "Use: exercise, sets, reps, weight_kg."; `exercise` missing, `missing_field` "Lift 2: exercise is missing." / "Give an exercise name on lift 2."; not on the lift list, `rules.unknown_exercise(raw, names, "Lift 2: ")`, and the list's own name is stored (`rules.match_exercise`: case and spaces forgiven); `sets` by `rules.number(value, int, "sets", "Lift 2")`, then outside 1 to 10, `invalid_field` "Lift 2: sets 11 is outside 1 to 10." / "Give sets on lift 2 from 1 to 10."; `reps` by `rules.number(value, int, "reps", "Lift 2")`, then outside 1 to 30, `reps_out_of_range` "Lift 2: reps 31 is outside 1 to 30." / "Give reps on lift 2 from 1 to 30."; `weight_kg` null or blank is null, else `rules.number(value, float, "weight_kg", "Lift 2")` (2 dp), then outside 0 to 500, `weight_out_of_range` "Lift 2: weight 501 kg is outside 0 to 500 kg." / "Give weight_kg on lift 2 from 0 to 500, or leave it out." When every lift's sets passed and they add up past 60: `sets_count_out_of_range` with the words of the "Errors" table, the name as checked, or "This routine" when the name was refused | `rules.bounded`, which the table below once named for these, takes only food, cardio and settings numbers. `log_lifts`' own set checks are the codes and words a model already knows, with "lift" for "set" |
| R6 | **The save,** after the checks. `name_key` is `" ".join(name.split()).casefold()`, as `save_food`'s. The row is found by `user_id` and `name_key`, deleted rows included. **The cap:** a save that would make a routine live that is not (a new name, or a deleted one brought back) while 20 are live is `too_many_routines`, nothing written. A replace of a live one is never refused by the cap. A live routine whose name as sent and lifts as checked equal what is stored writes nothing: `updated_at` and `saved_on` stay. Otherwise the name as sent, the lifts, `saved_on` (the person's today), `deleted_at` none and `updated_at` (`db.utcnow()`) are set, then flush. `lifts` is stored as `json.dumps` of the checked list, each item `{"exercise", "sets", "reps", "weight_kg"}` in that order, with `separators=(",", ":")` | Decision 4 counts live routines, so every path that makes one live counts. The same name in another case is a change of the name, as for a saved food |
| R7 | **The words of a lift,** in a summary and on the page: "{exercise} {sets} × {reps}", and " at {weight}" when the weight is not null, 0 kg included ("Dip 3 × 10 at 0 kg"), every number through `app.formatting` (`count`, `reps`, `weight_kg`), joined by `lifts._joined` ("a, b and c"). The summaries of a save show the routine's own weights | Null means "ask", 0 means bodyweight: the two must read apart |
| R8 | **`get_routines`,** a read: no lock and no write limit, `no_account` for no account. `today` is `users.today_for(user)`. The live routines of the person, by `name_key` then `id`. `lifts.history(s, user.id, today)["last_top"]`, once a call, only when a routine is there. Each lift: with a `last_top` and (no routine weight, or `last_top`'s date on or after `saved_on`): that weight, `weight_from` "last_session". Else with a routine weight: it, `weight_from` "routine". Else null and null. `last_date`: `last_top`'s date as YYYY-MM-DD, or null. The summary: "1 routine: Push day.", "2 routines: Push day and Pull day.", the count through `app.formatting`. None: "No routines saved yet." and the note. `how` always | Decision 8, read as the dates compare: a session dated before the save loses to the routine's own weight, one on the day or after wins |
| R9 | **`last_top`.** In `history`'s per-lift loop, beside `last`: `last_top[lift] = {"date": t.date, "weight_kg": max of that session's weights of the lift}`. The real date, as `last` keeps it. No new query. `get_progress` and the pages copy no field of it | Decision 9 |
| R10 | **Through MCP.** Two functions, registered after `send_feedback` and before the owner's three: `save_routine` (annotation `SETTING`), then `get_routines` (`READ_ONLY`). `NEEDS`: `save_routine` `log`, `get_routines` `read`. The input model `RoutineLiftIn` in `app/mcp_schemas.py`: `exercise: str`, `sets: int`, `reps: int`, `weight_kg: float | None = None`, with the words below. `save_routine(name, lifts: list[RoutineLiftIn], key)` sends `name` and `lifts` (each `model_dump(exclude_none=True)`) through `_sent`. The result models carry the words below; a field they leave unnamed takes the input's words (a lift's `exercise`, `sets`, `reps`) or the words of the result's own `id`. The notes' counts: "twenty" to "twenty-two" at `app/mcp_server.py:1` and `:1101` and `app/mcp_schemas.py:1`, "thirteen" to "fourteen" at `app/mcp_server.py:250` | So everyone's list is the twenty in their order, then the two, then the owner's |
| R11 | **`app/services/common.py`.** `save_routine` joins `WRITE_FUNCTIONS`, after `save_food`. `KIND_LABELS["routine"]`: ("routine", "the id that save_routine returned, or one from get_routines"). `READ_BACK["save_routine"]`: "get_routines". `ENTRY_OR_FOOD` becomes "get_log, find_food for a saved food, or get_routines for a routine" | The write limit counts a save. `not_found` and `already_done` name the read that shows a routine |
| R12 | **`app/services/entries.py`, the kind and its fixes.** `_deletable` gains `"routine": Routine`, after `saved_food`. `entry_dict` and `_links` (`{"routines": ...}`) know a routine. `restore_entry` of a deleted routine while 20 are live: `too_many_routines`, as R6. Its `not_deleted` fix for a routine: "Nothing to do: it is live. get_routines shows it." `undo_edit` on a routine, after the lookup: `nothing_to_undo` with the words of the "Errors" table. `edit_entry` with kind `routine`: `unknown_kind` "A routine is not edited here." / "Use save_routine with the same name and the whole routine." The two calls of a delete and the switch are the code that is there | Decisions 4, 6 and 11. The fixes say what is true of a routine, as a saved food's do |
| R13 | **The trace.** `routine` joins `calllog.TABLES`, after `saved_food`, and its comment says eight. `"lifts"` joins `KNOWN_NAMES`, and `RoutineLiftIn` the tuple of input models | Decision 16 |
| R14 | **The export and account delete.** `"routines.csv": (Routine, {})` after `saved_foods.csv` in `export.TABLES` and in `ORDER`; the module notes list it; the README's line, in its words below, wrapped to the README's columns as its neighbours are. `account.OWNED` gains `Routine`. The delete page's `DELETE_COUNTS` gains `(Routine, "Routines")` after "Saved foods": the page says what goes | Decision 14 |
| R15 | **The page.** Routes `/routines` (GET), `/routines/{routine_id}/delete` and `/routines/{routine_id}/restore` (POST), after the Foods routes. `FRAMES["routines.html"]`: `("foods-page", None, "settings")`. The view reads `services.get_routines`, so the page's weights are the assistant's. Delete and Undo are Foods' code with kind `routine`: sign-in, the form's CSRF token and key, `write()`, `too_many` and the not-found page. The strip: "Routine deleted." with Undo, then "Routine restored.", from a table as Foods' are. A refused Undo: `not_deleted`, the message page "Already back" / "That routine is not deleted, so there was nothing to undo." / "Go back to Routines to see it." (409); `too_many_routines`, "Too many routines" / the service's message / "Delete one on Routines first, then undo again." (409). `links.py` gains `"routines": "/routines"`. Settings' "More" gains "Saved routines" after "Saved foods" | Foods names no food in its strip: the words come from a table, never from the address, and naming a deleted routine would need a read no service gives. The two refusals are what a stale strip can meet |
| R16 | **The evals,** as the section below. `save_routine` joins `judge.SEED_SERVICES`; `routine` joins `judge.DB_TABLES` and `run.TABLES`; `person_rows` gives each `routine` row `lift_count`, the length of its `lifts` JSON; `judge._rows_text` keeps `lift_count`; the module notes say both. The three cases go last in `evals/cases.json`, in their order. The counts move to 32 here | Decision 21. Not run by the builder |
| R17 | **Counts.** Every count of functions, writes and cases that is drawn, pinned or written moves, each listed by file and line in the hand-over, and `docs/evals.md:41` gains ", twenty-two since `save_routine` and `get_routines`" | Decision 19 |
| R18 | **The words of the weight rule say days, as the rule compares them.** "saved after that session" became "saved on a later day than that session," in `weight_kg`'s and `how`'s words and in `docs/can-and-cant.md`, and the page's "when it is newer" became "when it was saved on a later day". Ruled after the review | Decision 8 compares the session's date with `saved_on`: a routine saved later on the same day as a session loses to it, and a time zone change can put either first. The old words promised a comparison of times |
| R19 | **A routine's name is stored with its spaces folded,** `" ".join(name.split())`, after its check, so a newline or a tab never reaches a one-line summary. Ruled after the review | `name_key` already folds them; the name shown now matches |

## "Save my push day" and "log my push day", call by call

| Said | Calls | What comes back |
|---|---|---|
| "Save my push day: bench press 3 sets of 8 at 60, overhead press 3 by 10, dips 3 by 10" | `save_routine` name "Push day", lifts Bench Press 3 × 8 at 60, Overhead Press 3 × 10, Dip 3 × 10. (`list_exercises` first only if the assistant is unsure of a name) | "Saved Push day: Bench Press 3 × 8 at 60 kg, Overhead Press 3 × 10 and Dip 3 × 10." |
| "Log my push day" (no lift logged before) | `get_routines`: bench 60 from the routine; overhead press and dip null. The assistant asks for those two | One question, once |
| "Log my push day", next week | `get_routines`: every weight from last week's session. `log_lifts` with 9 sets | The session summary, from gym logging: the sets and any new bests |
| "Log my push day, but bench was 62.5 today" | `get_routines`, then `log_lifts` with the bench sets at 62.5 | The summary. Next week `get_routines` gives 62.5 by itself |
| "Save today's workout as push day" | `get_log` for today, then `save_routine` with each lift, its sets, reps and heaviest weight | "Saved Push day: ..." |
| "Delete my push day" | `delete_entry` kind routine: a preview; the person says yes; the second call | As any delete. `restore_entry` brings it back |

## Every text a model or a person reads

Each text below passes `tests/text_rules.py` on its surface. No model text holds "page" or "form" beside an action word. Where one fails as built, the builder changes the fewest words and names the change.

### `save_routine`

| Part | Text |
|---|---|
| Title | "Save a routine" |
| Description | "Saves a workout the person does often under its name, such as Push day: each lift with its number of sets and its reps, and a weight only when the person gave one. Send only what the person said: never add a lift, a set or a weight of your own. Saving the same name again replaces the routine. To save a session already logged, read it with get_log and send each lift with its number of sets, its reps and its heaviest weight." |
| `name` | "The routine's name, as the person says it, such as Push day. 1 to 64 characters." |
| `lifts` | "Each lift, in the order done: 1 to 20 lifts, and at most 60 sets in all." |
| `lifts[].exercise` | "The lift, exactly as list_exercises gives it." |
| `lifts[].sets` | "How many sets of it, 1 to 10." |
| `lifts[].reps` | "Reps in each set, a whole number from 1 to 30." |
| `lifts[].weight_kg` | "The weight in kg, 0 to 500, only when the person gave one. Leave it out otherwise: get_routines then gives the weight of the lift's latest session." |
| `key` | The shared `Key` argument, word for word |
| Result `kind` | "routine." |
| Result `id` | "The routine's id, for delete_entry and restore_entry with kind routine." |
| Result `created` | "True when the name was new. False when a routine of that name was replaced or brought back." |
| Result `routine` | "The routine as saved." With `id`, `name`, `lifts` (each `weight_kg`: "The weight the person gave, in kg, or null when none was given."), `saved_on` ("The person's date when the routine was last saved."), `updated_at` |
| Result `summary` | "What this call did, in one line, ready to quote." |
| Result `links.routines` | "The Routines page: the person's routines." |

### `get_routines`

| Part | Text |
|---|---|
| Title | "Get routines" |
| Description | "The person's saved routines, each lift with its sets, its reps and the weight to log. Call it when the person names a routine, such as log my push day. To log one, send its sets with log_lifts, one item a set, with each change the person names, and leave the date out for today. Where weight_kg is null, ask the person for it. Say in your reply which sets were logged." |
| Arguments | None |
| Result `routines` | "Every live routine, by name." Each with `id`, `name`, `saved_on`, `lifts` |
| `lifts[].weight_kg` | "The weight to log, in kg: the heaviest set of this lift's latest session, unless the routine was saved on a later day than that session, with a weight of its own. Null when there is neither: ask the person." |
| `lifts[].weight_from` | "Where weight_kg came from: last_session, routine, or null." |
| `lifts[].last_date` | "The date of this lift's latest session, or null when it was never logged." |
| Result `how` | "weight_kg is the heaviest set of the lift's latest session, unless the routine was saved on a later day than that session, with a weight of its own. Quote these numbers; do not work out your own." |
| Result `summary` | "The routines by name, in one line, ready to quote." |
| Result `note` | "Present when no routine is saved." |
| Result `links.routines` | "The Routines page: the person's routines." |

### Texts that change

| Where | Old | New |
|---|---|---|
| `delete_entry`'s `kind` | "lift_session, bodyweight, measurement, food_entry or cardio_entry. Or saved_food." | "lift_session, bodyweight, measurement, food_entry or cardio_entry. Or saved_food or routine." |
| `restore_entry`'s `kind` | "... With undo_edit, saved_food too." | "... With undo_edit, saved_food too. Or routine, to bring back a deleted one." |
| The id of both | "... A saved food's id comes from save_food or find_food." | "... A saved food's id comes from save_food or find_food, a routine's from save_routine or get_routines." |
| `delete_entry`'s description, its first sentence | "Deletes one of the person's entries or saved foods, in two calls." | "Deletes one of the person's entries, saved foods or routines, in two calls." The rest unchanged |
| Settings, "Read, log and delete" | "Your assistant can log and import entries, edit and restore them, save foods and set goals. It can change your time zone and targets, and delete entries and saved foods. ..." | "Your assistant can log and import entries, edit and restore them, save foods and routines, and set goals. It can change your time zone and targets, and delete entries, saved foods and routines. Before a delete it is shown the entry and told to ask you. Nothing is erased: a deleted one can be restored." |
| Settings, "Read and log" | "Everything above except deleting: your assistant cannot delete an entry or a saved food." | "Everything above except deleting: your assistant cannot delete an entry, a saved food or a routine." |
| Settings, "More" | "Saved foods" | "Saved foods", then a new link "Saved routines" |
| `docs/can-and-cant.md`, "What it records today" | None | "- **Routines:** workouts the person does often, saved by name: each lift with its sets and reps, and a weight when the person gave one." |
| `docs/can-and-cant.md`, a new section "Routines", after "Importing" | None | "- A routine is saved by name with `save_routine`: each lift with its number of sets and its reps, and a weight only when the person gave one. Up to 20 routines, each of 1 to 20 lifts and at most 60 sets, so a logged routine fits one session." "- `get_routines` gives each lift's weight to log: the heaviest set of its latest session, unless the routine was saved on a later day than that session, with a weight of its own. With neither, Claude asks." "- To log one, Claude sends its sets with `log_lifts`, with each change the person names. They go into that day's session, as any sets do." "- Saving the same name again replaces the routine. There is no undo of a replace: save it again as it was. `delete_entry` takes kind `routine`, in its two calls, and `restore_entry` brings a deleted routine back." "- The import does not bring routines over, and the export holds them in `routines.csv`." |
| `docs/can-and-cant.md`, the function table | "Twenty functions, nothing else:" | "Twenty-two functions, nothing else:", and two rows after `send_feedback`: `save_routine` "Saves a workout by name, with its lifts, sets and reps." `get_routines` "The saved routines, each lift with the weight to log." |
| The export README | None | "routines.csv        each routine: lifts holds its lifts as JSON, each with exercise, sets, reps and weight_kg (null when none was given). saved_on is the date it was last saved" |
| `README.md` | The function count | 22, and a line on routines beside saved foods |
| `docs/evals.md:41` | "... twenty since `send_feedback`." | "... twenty since `send_feedback`, twenty-two since `save_routine` and `get_routines`." |

### Summaries (`summary`, quoted by the assistant)

| When | Text |
|---|---|
| A new name | "Saved Push day: Bench Press 3 × 8 at 60 kg, Overhead Press 3 × 10 and Dip 3 × 10." |
| A name held | "Replaced Push day: Bench Press 3 × 8 and Overhead Press 3 × 10." |
| A deleted name | "Brought back and replaced Push day: Bench Press 3 × 8." |
| The same lifts again | "Push day is already saved as given: nothing changed." |
| `get_routines` | "2 routines: Push day and Pull day." / "1 routine: Push day." / "No routines saved yet." |
| `get_routines`' note, with none | "No routines saved yet. save_routine saves one." |

Each lift is worded by R7.

### Errors

| Code | When | Message | Fix |
|---|---|---|---|
| `too_many_routines`, new | A save or a restore that would make a 21st routine live | "This account holds 20 routines, the most it keeps." | "Delete one with delete_entry, or save under a name already used to replace that routine." |
| `sets_count_out_of_range`, as today's code | Over 60 sets in all | "Push day would hold 64 sets. A routine holds at most 60, so that it fits in one session." | "Send fewer sets, or split it into two routines." |
| `invalid_field` | Not 1 to 20 lifts | "A routine holds 1 to 20 lifts, not 23." | "Send 1 to 20 lifts." |
| `invalid_field`, `missing_field`, `not_a_number`, `not_a_whole_number`, `reps_out_of_range`, `weight_out_of_range`, `unknown_exercise` | A name, a sets, reps or weight out of range or of the wrong shape; a lift not on the list; a field a routine does not have | As R5 words them | As R5 |
| `nothing_to_undo`, its routine fix | `undo_edit` on a routine | "A routine has no earlier values kept." | "Save it again with save_routine, as it was." |
| `unknown_kind` | `edit_entry` with kind `routine` | "A routine is not edited here." | "Use save_routine with the same name and the whole routine." |

### The page, `/routines`

| Piece | Words |
|---|---|
| Title, h1 | "Routines · Spotter", "Routines" |
| Lede | "Workouts you do often, saved by name. Say "log my push day" and your assistant logs its sets." "Routines are saved by talking to your assistant." |
| Table | Columns "Routine", "Lifts" and a Delete cell. A row: "Push day", "Bench Press 3 × 8 at 62.5 kg, Overhead Press 3 × 10 at 40 kg and Dip 3 × 10", "Delete" |
| Under it | "Each weight is the one your assistant would log today: your latest session's, or the routine's own when it was saved on a later day." |
| After a delete | "Routine deleted." and "Undo", the Foods strip. After the Undo: "Routine restored." |
| A refused Undo | The two message pages of R15 |
| None yet | "No routines yet. Tell your assistant a workout you do often, such as your push day, and ask it to save it." |

## The migration

`migrations/versions/0010_routine.py`: up creates `routine` with the columns of decision 1, `UNIQUE (user_id, name_key)`, and the foreign key `user_id` to `user.id` `ON DELETE CASCADE`, named as R2 and R3 name them. Down drops it. No other table changes. Tested up and down on SQLite by the builder, and run on Postgres after, as the switch's `0009` was.

## The eval cases

| Case | Seed | Says | Passes when |
|---|---|---|---|
| `routine_saved` | None | "Save my push day: bench press 3 sets of 8, overhead press 3 sets of 10, dips 3 sets of 10" | One successful `save_routine`, name "push day" (any case), lifts Bench Press 3 × 8, Overhead Press 3 × 10, Dip 3 × 10, and no `weight_kg` in any lift. No `log_lifts`. One live routine with 3 lifts |
| `routine_logged` | `save_routine` as `push`: Bench Press 3 × 8 at 60, Overhead Press 3 × 10 at 40 | "Log my push day" | One successful `log_lifts`, its sets holding Bench Press 60 × 8 and Overhead Press 40 × 10, never `new_session` true. One live session today, `set_count` 6 |
| `routine_changed` | The same | "Log my push day, but bench was 62.5 today" | One successful `log_lifts`, its sets holding Bench Press 62.5 × 8 and Overhead Press 40 × 10, and no set at 60. One live session, `set_count` 6 |

```json
{"id": "routine_saved",
 "say": "Save my push day: bench press 3 sets of 8, overhead press 3 sets of 10, dips 3 sets of 10",
 "expect": {"function": "save_routine", "ok_calls": {"save_routine": 1},
            "inputs_include": {"name": "push day",
                               "lifts": [{"exercise": "Bench Press", "sets": 3, "reps": 8},
                                         {"exercise": "Overhead Press", "sets": 3, "reps": 10},
                                         {"exercise": "Dip", "sets": 3, "reps": 10}]},
            "inputs_exclude": ["lifts.*.weight_kg"], "no_call": ["log_lifts"]},
 "db": [{"table": "routine", "count": 1, "all": {"lift_count": 3}}]}

{"id": "routine_logged",
 "say": "Log my push day",
 "seed": [{"as": "push", "service": "save_routine",
           "data": {"name": "Push day", "lifts": [{"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 60},
                                                 {"exercise": "Overhead Press", "sets": 3, "reps": 10, "weight_kg": 40}]}}],
 "expect": {"function": "log_lifts", "ok_calls": {"log_lifts": 1},
            "inputs_include": {"sets": [{"exercise": "Bench Press", "weight_kg": 60, "reps": 8},
                                        {"exercise": "Overhead Press", "weight_kg": 40, "reps": 10}]},
            "inputs_forbid": {"new_session": true}},
 "db": [{"table": "lift_session", "count": 1, "all": {"set_count": 6, "date": "{{today}}"}}]}

{"id": "routine_changed",
 "say": "Log my push day, but bench was 62.5 today",
 "seed": [{"as": "push", "service": "save_routine",
           "data": {"name": "Push day", "lifts": [{"exercise": "Bench Press", "sets": 3, "reps": 8, "weight_kg": 60},
                                                 {"exercise": "Overhead Press", "sets": 3, "reps": 10, "weight_kg": 40}]}}],
 "expect": {"function": "log_lifts", "ok_calls": {"log_lifts": 1},
            "inputs_include": {"sets": [{"exercise": "Bench Press", "weight_kg": 62.5, "reps": 8},
                                        {"exercise": "Overhead Press", "weight_kg": 40, "reps": 10}]},
            "inputs_forbid": {"sets.*.weight_kg": 60}},
 "db": [{"table": "lift_session", "count": 1, "all": {"set_count": 6}}]}
```

| Case | Proves | Does not prove |
|---|---|---|
| `routine_saved` | One sentence becomes one routine with only what was said: no weight made up, nothing logged | That a routine with weights keeps them right: `routine_logged`'s seed covers that from the service side |
| `routine_logged` | The assistant reads the routine and logs its sets, with the routine's weights, as one session | The newest-wins rule against a later session: unit tests hold it. A run cannot seed `log_lifts` before the routine and then age the routine |
| `routine_changed` | One changed number is sent changed, and the rest as saved | That next week's read gives 62.5: unit tests hold it |

**What the judge and the runner need, read at `3fe468b`:** `save_routine` joins `judge.SEED_SERVICES`; it is called as `service(user_id, data, "web")`, which is `save_routine`'s shape (`evals/judge.py:209-220`). `routine` joins `judge.DB_TABLES` and `run.TABLES` (`evals/run.py:34`); it has `user_id`, so `person_rows` reads it as it is (`:248-270`). The runner gives each `routine` row a `lift_count`, read from its JSON, as it gives `lift_session` its `set_count`. `inputs_exclude` with `lifts.*.weight_kg` and `inputs_forbid` with `sets.*.weight_kg` are paths of the kind the judge's notes name (`evals/judge.py:20-22`). "push day" matches "Push day": the judge ignores case in strings. The case table in `docs/evals.md`, the README's count, and the counts in `tests/test_evals.py` and `tests/test_feedback.py` follow: 32 in this tree.

**The run that follows:** the whole set, 3 runs each, on `claude-opus-5-5`, once this work and the setup work are joined. The cost is told to the owner before it runs.

## Work package: one builder, R1

| Files | Exit check |
|---|---|
| `migrations/versions/0010_routine.py` (new), `app/models.py`, `app/rules.py` (the limits, `required_text`'s `limit`), `app/services/routines.py` (new), `app/services/__init__.py`, `app/services/common.py` (R11), `app/services/lifts.py` (`last_top` alone), `app/services/entries.py` (R12), `app/services/account.py` (`OWNED` alone), `app/services/export.py`, `app/mcp_server.py` (two functions, the kind and id texts, `delete_entry`'s first sentence, `NEEDS`, the notes' counts), `app/mcp_schemas.py`, `app/calllog.py`, `app/web.py` (the page, its two posts, `FRAMES`, `PAGES`, the routes, the switch lines, `DELETE_COUNTS`, the Settings link), `app/links.py`, `app/templates/routines.html` (new), `app/templates/settings.html` (the link), `docs/can-and-cant.md`, `docs/evals.md`, `README.md`, `evals/cases.json`, `evals/judge.py`, `evals/run.py`, `tests/test_routines.py` (new). Existing test files only where a decision makes an assertion fail, each named in the hand-over | Every decision and ruling has a test. A save with only lifts, sets and reps; one with weights; the same name in any case or with other spaces replaces; a deleted name comes back; the same lifts again write nothing and keep `updated_at`. 20 live, the 21st refused, a deleted one brought back as the 21st refused, a restore as the 21st refused, a replace still allowed; two saves at once. 61 sets, 21 lifts, a name not on the list, a weight of 501, 65 characters of name: each refused, nothing written. Each newest-wins branch: no session, a session before the save, a session on the save's day, a session after it, a deleted session, two sessions on a date, a session dated after today, a lift with no weight and no session. `get_routines` reads no other person's rows, and no path reads, replaces, deletes or restores another person's routine. Delete through MCP in two calls, restore, the switch at `log` refusing the delete; the web's Delete and Undo and their two refusals. `undo_edit` gives `nothing_to_undo` with its fix; `edit_entry` gives `unknown_kind`. The export holds `routines.csv` and the README names it. Account delete takes the rows and counts them, and its page says so. The import refuses a routine kind as today. The trace writes `lifts` and `kind=routine`, and nothing else new. The same key twice saves once. The write limit counts a save. The page answers signed in, behind the limit, and keeps the page and text rules. 22 functions everywhere a count is stated. Of the published list, only the two new ones, `delete_entry` and `restore_entry` differ; `log_lifts` not by one byte. The instructions are 3,927 characters. Migration up and down on SQLite. The eval cases load and judge both ways. Every test file passes, plain and with `-W error` |

## Known limits

| Limit | Why it stands |
|---|---|
| One reps value a lift. 8, 8 and 6 is saved as the lift twice, or as 3 × 8 | "3 sets of 8" is how a routine is said |
| A lift listed twice, heavy and back-off, gets the same newest weight for both once it is logged | The rule reads the heaviest set of a session. The person says the back-off weight |
| A light day sets the next weight lower | The newest number wins. The person says the change |
| A routine logged into a day that already holds many sets can pass 60, and is refused with gym logging's fix | One session holds 60 |
| A conversation begun before the deploy does not see the two functions | A running chat holds its tool list. A new one sees them |
| A routine holds the names as saved. If the list ever renames a lift, logging that routine is refused with the closest names | The list has only gained rows so far |
| "Once" for a weight question is the assistant's | Nothing is stored about it |

## Not in this work

A form that saves or logs a routine. Marking a session with the routine it came from. A routine's own history or progress. A schedule ("push on Mondays"). Cardio in a routine. Supersets, rest times, timed holds. Undo of a replace. `edit_entry` on a routine. Routines in the import. Volume by muscle. Pounds. A change to `log_lifts` or to the instructions. The setup step and the `/hi` lines, which come with the setup work.

## Note, 2026-11-20: the exercise list (`docs/contracts/exercise-list.md`)

Reps 1 to 100. A routine takes only lifts of sets and reps (exercise-list.md R10): a hold, a carry or a sled is refused, and `get_routines` leaves such a lift out with a line. A band lift takes no weight; a minus weight is help on the two assisted lifts.

A plain word is refused in `save_routine` with its lifts; a lift's own name is taken as written. `get_routines` says to send each lift with `exact` true.

## Note, 2026-11-22: log and guide (`docs/contracts/log-and-guide.md`)

`get_routines`' description names `log`: "To log one, send its sets with log, kind lifts, one part a set, the lift as movement, with exact true, with each change the person names, and leave the date out for today." `save_routine`'s refusal of a lift that records no reps, such as a hold or a carry, has the fix "Log its sets instead.", where it named `log_lifts`.

## Note, 2026-11-25: the logic audit (`docs/contracts/logic-audit.md`)

- Decision 9 (line 70), R8 (line 99) and R9 (line 100): `last_top` is gone from `lifts.history`, by the logic audit's ruling R1. `get_routines` reads `routines.latest_sessions`, the one read of each lift's latest session. The test oracle in `tests/test_hardening.py` is unchanged: it builds the figure itself and still checks that read.
- Settings, "Read and log" (line 172), as built: "Everything above except deleting. A second weigh-in or measurement on a day still replaces the first, which is kept."
