# Gym logging contract: a day's sets go into that day's session

**Goal: a person can say each set at the gym as they do it. Every `log_lifts` call for a date adds its sets to that date's session, so a workout said set by set is one session, and a set said again is never questioned.**

The owner's words that bind it (2026-11-14): "a smooth UX", "Most of the days are going to be quick inputs, right? Log and push, not more than that", "we have to be a one-for-one replacement for them", "Let's not overcomplicate things out of the bag." On undo, the same day: "This undo, redo, edit thing seems overly complicated to me", "undo doesn't make much sense for me unless it's like an immediate undo. That somebody said something and they want to undo it and re-share it." So this work adds no way back for a way back. A confirm card before writes is his own later item, and is not in this work.

He read the decisions on 2026-11-15 and took both of the picks put to him: a second workout in a day is a second session only when the person says so (decision 2), and there is no guard against a retry that carries no key (decision 3).

The gap was found on 2026-11-14, in a read of what is left for a first version: each `log_lifts` call makes a new session, equal sets within 10 minutes come back as duplicates, and no eval case logs a lift.

## What exists today

Read on branch `switch` at `698ee18`, equal to `main`, on 2026-11-15. The builder reads these rows again on the commit it starts from.

| Piece | State | Where |
|---|---|---|
| A session per call | Every `log_lifts` call that passes its checks inserts a new `LiftSession` with its sets. Nothing looks for a session already logged that day | `app/services/lifts.py:255-258` |
| The 10-minute guard | The same date, notes and sets as a live session created in the last 10 minutes returns that session with `duplicate` true and writes nothing. Skipped with `allow_repeat` | `app/services/lifts.py:225-232`, `app/services/common.py:83`, `:438-450` |
| What the guard compares | A SHA-256 of the date, the notes and the whole sorted set list | `app/services/lifts.py:129-132` |
| So, at the gym | "Bench 80 for 5" after each of three equal sets: the first makes a session, the second and third come back duplicate and the assistant is told to ask. Said with small changes, each call is one more session that day | The two rows above |
| What the assistant is told | Instructions: "duplicate: true means nothing was written: say so, and send allow_repeat: true only when the person confirms it was a separate entry." `log_lifts`: "Records one lift session: its sets, and optional date and notes. ..." | `app/mcp_server.py:193-194`, `:596-618` |
| A set's shape | `exercise`, `weight_kg` (0 to 500), `reps` (1 to 30). kg only | `app/mcp_schemas.py:26-31` |
| The result | `kind`, `id`, `duplicate`, `session` (every set, in the order logged), `new_bests`, `summary`, `links`, `note` (on a duplicate) | `app/mcp_schemas.py:316-324`, `:340-356` |
| Sets in one session | 1 to 60 a call, `sets_count_out_of_range` past it | `app/rules.py:35`, `:330-333` |
| Set numbers | Per exercise, from 1, in the order given | `app/services/lifts.py:118-126` |
| New bests | Worked out per session: a session's best per lift against every set of the sessions before it, in the one order (date, then id). A lift's first session sets the mark, never a best | `app/services/lifts.py:168-191`, `app/metrics.py:164-182` |
| Bests at log time | The same rule against every earlier session's sets, then a summary with the bests, the marks or "No new bests." | `app/services/lifts.py:237-270` |
| `get_trends` | Counts live sessions: in the last 7 days, and over 28 days divided by 4 | `app/services/trends.py:162-171` |
| The 28-day grid | `lift_sessions` per day and per week, `lift_sessions_28d`, `lift_days_28d` | `app/services/trends.py:224-248` |
| `get_progress` | A lift's `sessions` is the number of sessions that hold it; one estimated max point per session | `app/services/progress.py:59`, `app/services/lifts.py:182-187` |
| `get_log` and the day page | Each day's sessions, each with its sets and its new bests. The day page draws one block per session: "Lift session", its actions, its sets table, its bests, its notes | `app/services/log.py:113-114`, `app/templates/day.html:213-228` |
| Lifts on the web | No form logs a lift. "Lifts are logged through your assistant." `log_lifts` is called from MCP only. The web edit page changes a session's date and notes | `app/templates/day.html:229`, `app/mcp_server.py:618`, `app/web.py:192` |
| `edit_entry` on a session | Its date, notes or whole set list (1 to 60, it replaces every set). Keeps a version first. Works the key out again, except an imported session keeps its key | `app/mcp_server.py:794-811`, `app/services/entries.py:481-511` |
| Undo | `restore_entry` with `undo_edit` puts the newest kept version back. One step, once: when the newest version is an undo's, nothing changes. A version holds a session's sets | `app/services/entries.py:260-279`, `app/services/common.py:481-491` |
| Import | Never adds to a day that holds a live session: "day taken". Matches a session by its sets | `app/services/imports.py:44-45`, `:586-588` |
| A write's key | Any write takes `key`. The same key and call again writes nothing and returns the first answer. With no key, "the 10-minute rule above is the only guard" | `app/services/common.py:43-54`, `docs/contracts/idempotency.md` decision 13, `docs/can-and-cant.md:151-155` |
| Whether assistants send a key | Not checked |  |
| `docs/can-and-cant.md` on lifts | "Lift sessions: the date, the sets ... and optional notes." Duplicates: "The same session ... logged again within 10 minutes comes back with `duplicate` true." The function table: "Records one lift session." | `docs/can-and-cant.md:8`, `:146-149`, `:381` |
| Evals | 26 cases. `log_lifts` appears in 5 lines, every one a `no_call` list | `evals/cases.json:111`, `:135`, `:146`, `:155`, `:197` |
| What a case can seed | `save_food`, `log_bodyweight`, `log_measurement`, `set_goal`, `import_entries`, `edit_entry`, `four_weeks`. Not `log_lifts` | `evals/judge.py:73-75` |
| What a case can check in the rows | One check per table, several checks per case. Tables: `food_entry`, `saved_food`, `bodyweight`, `measurement`, `cardio_entry`, `lift_session`, `goal`, `feedback`. Not `lift_set`: it has no `user_id` of its own, and the runner reads rows by `user_id` | `evals/judge.py:76-77`, `evals/run.py:34`, `:248-262`, `app/models.py:165-179` |
| How it compares | Numbers as numbers (80 matches 80.0). A list in `inputs_include` matches when each expected item matches some sent item: it cannot count sets | `evals/judge.py:227-241` |
| A run | One message from the person. No second turn | `docs/evals.md:296-297` |
| Tests that call `log_lifts` | 118 calls in 23 test files. How many log two sessions on one date and expect two: not counted | `tests/` |

## The three designs weighed

| Design | What it does | Verdict |
|---|---|---|
| (a) A date's calls add to that date's session, with `new_session` to start another | One rule in one function. The assistant sends each set as it is said, with nothing to remember | **Chosen.** It fixes the count, the pages and the question at once, and needs nothing held across turns |
| (b) An optional argument that names the session to add to | The assistant must hold the session's id across turns, or read it with `get_log` first | Not chosen. A call without it still makes a new session, so a forgetful assistant gives today's result. No eval can test it: a run is one message |
| (c) Sessions as today, and no duplicate question for equal sets said in a row | Stops the question only | Not chosen. Three sets said one by one are still three sessions: `get_trends` and the pages count them |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **A date's sets go into that date's session.** A `log_lifts` call for a date (left out: the person's today) that has a live session adds its sets to it. With none, it starts one, as today. With two or more, it adds to the latest, by id | The rule the person already lives by: today's workout. One rule, no clock |
| 2 | **`new_session` true starts a new session** for that date even when one is logged. Optional, false when left out. The assistant sends it only when the person says this is a second workout that day | A plain way out for the rare double day. A client that holds today's list never needs it |
| 3 | **No duplicate guard on `log_lifts`.** Equal sets said again are added. `duplicate` is always false. `allow_repeat` is still taken, and ignored | Equal sets are what a gym is. A retry with a key already writes once. The guard cannot tell a retry with no key from the next set, so it would ask at the gym every time |
| 4 | **A set said twice by mistake** is taken back with `restore_entry` and `undo_edit`, which already exists: each add keeps the session as it was, so the undo takes back the last add. One step, once, as today | His "immediate undo". No new function and no way back for the way back |
| 5 | **An add is an edit of the session.** It keeps a version first (`why` "edit"), sets `updated_at`, and works the key out again over the whole session. An imported session keeps the key it was imported under, as an edit does | So undo, the stale web form, a delete's confirm code and the import all see the add as they see an edit, with no new code in them |
| 6 | **Set numbers carry on.** An added Bench Press set after two is set 3 | The table reads as one workout |
| 7 | **Notes on an add are joined** to the session's notes, on a new line. Notes that would make the session's notes over 2,000 characters are refused, and nothing is added | Nothing the person said is dropped. The column's limit stays |
| 8 | **At most 60 sets in a session, in all.** An add that would pass 60 is refused with `sets_count_out_of_range`, and its fix names `new_session` | `edit_entry` takes a whole set list of at most 60, so a longer session could not be edited |
| 9 | **Bests stay worked out per session,** against every earlier session, as `get_log`, the day page and `get_progress` show them. An add reports the bests its own sets raised: those the whole session holds now that it did not hold before the add. A lift with no earlier session gets the mark line again, from the whole session | The reply and the pages say the same best with the same "up from". A set that beats an earlier set of the same day is no new best: the session's best is what counts, as today |
| 10 | **The summary says what this call did.** A new session: as today. An add: "Added {sets} to the session on {day}. It now has {n} sets." then the bests, marks or "No new bests." | The person hears the count, so a set taken twice shows at once |
| 11 | **What the reads show.** `get_trends`, the grid, `get_progress` and the day page are not changed. They now count and draw one session for a workout said set by set | The fix is in the data, so every read is right with no change |
| 12 | **The import is not changed.** A day that holds a live session is still "day taken". A `log_lifts` call can add to an imported session (decision 5 keeps its key) | The import's rules stand. One rule for `log_lifts`, whoever made the session |
| 13 | **No migration.** Past days that hold several sessions stay as they are. An add goes to the latest | No column changes, and old rows are true records of what was sent |
| 14 | **The instructions do not change.** Their duplicate sentence still holds for every other kind. The new words are in `log_lifts`' own description and arguments | They sit near their 4,096 cap (`docs/contracts/switch.md` decision 18: 3,927) |
| 15 | **The trace.** An add's row reads `written`. `new_session` joins the known names in `app/calllog.py` | The owner sees a call as a call |
| 16 | **A write's key does not change.** The same key and call again returns the first answer and adds nothing | Idempotency contract, as it stands |
| 17 | **Evals: three cases are added,** 29 in all. The runner gives each `lift_session` row a `set_count`, and `log_lifts` becomes a seed service | No case logs a lift today. The set count is the thing this work changes, and no check can read it now |

## Texts a model or a person reads

Every text below must pass the repo's text rules as built. Where one fails, the builder changes the fewest words and names the change in its report.

| Where | Old, in full | New, in full |
|---|---|---|
| `log_lifts` description | "Records one lift session: its sets, and optional date and notes. Returns the saved session, any new bests, and a one-line summary. If the same session was logged in the last 10 minutes, nothing is written and duplicate is true." | "Records sets of lifting. Sets for a date go into that date's session: the first call starts it, and each later call adds its sets to it. So send only the sets not yet logged. Returns the whole session, any new bests, and a one-line summary. restore_entry with undo_edit takes back the last sets added." |
| `log_lifts` title | "Log a lift session" | Unchanged |
| `sets` | "Every set done, in order: 1 to 60 sets." | "The sets to log, in order: 1 to 60. Only sets not yet logged: earlier sets of that date are already in its session. A session holds at most 60 sets." |
| `date` | "The day it happened, YYYY-MM-DD, at most 2 years back. Leave out for today." | Unchanged |
| `notes` | "How it went, in the person's words. Up to 2,000 characters." | "How it went, in the person's words. Up to 2,000 characters. On a later call for the same date, they are added to the session's notes." |
| `allow_repeat` | "true logs it even when it matches a session from the last 10 minutes. Send it only when the person confirms it was a separate session." | "Not needed: every call adds its sets. Ignored." |
| `new_session`, new | None | "true starts a new session for that date, even when one is logged. Send it only when the person says this is a separate workout." |
| Result `duplicate` | "True when the same session was already logged in the last 10 minutes. Then nothing was written and this is the earlier session. Say so; send allow_repeat: true only when the person confirms it was a separate session." | "Always false: a set said again is added, never refused." |
| Result `session` | "The session as saved." | "The session as saved, with every set in it." |
| Result `new_bests` | "New bests this session set, against every earlier set of the lift. A lift's first session never sets one. Empty on a duplicate." | "New bests this call's sets set, for the session as a whole, against every earlier session of the lift. A lift's first session never sets one." |
| Result `summary` | "The session in one line, ready to quote: its new bests, or for a lift's first session the mark it sets (a first session is never a new best)." | "What this call did, in one line, ready to quote: the sets logged or added, then new bests, or for a lift's first session the mark it sets (a first session is never a new best)." |
| Result `note` | "Present on a duplicate: what happened and what to do." | Removed: no answer carries it |
| Summary of an add | None | "Added Bench Press 1 × 5 at 80 kg to the session on Sun 15 Nov. It now has 3 sets." Then, as today, " New best: ...", the mark line, or " No new bests." |
| `docs/can-and-cant.md:8` | "**Lift sessions:** the date, the sets (exercise, weight in kg, reps) and optional notes." | "**Lift sessions:** the date, the sets (exercise, weight in kg, reps) and optional notes. Sets logged for a date go into that date's session, call by call. A second workout that day is a second session when the person says so." |
| `docs/can-and-cant.md:146-149` | "**Duplicates.** The same session, the same weight on the same day, the same measurement on the same day, the same set of foods or the same cardio session, logged again within 10 minutes comes back with `duplicate` true. Nothing new is written. Send `allow_repeat` true only when the person confirms it happened twice." | "**Duplicates.** The same weight on the same day, the same measurement on the same day, the same set of foods or the same cardio session, logged again within 10 minutes comes back with `duplicate` true. Nothing new is written. Send `allow_repeat` true only when the person confirms it happened twice. Lifts are the exception: every `log_lifts` call adds its sets to that day's session, so a set said twice is logged twice. `restore_entry` with `undo_edit` takes back the last sets added." The goal sentence after it is unchanged |
| `docs/can-and-cant.md:155` | "With no key, the 10-minute rule above is the only guard." | "With no key, the 10-minute rule above is the only guard, and lifts have none." |
| `docs/can-and-cant.md:381` | "Records one lift session." | "Records sets of lifting, into that day's session." |
| `docs/evals.md`, `evals/judge.py` notes | Seeds list six services and `four_weeks`. No word on sets | `log_lifts` joins the seed services. "A `lift_session` row carries `set_count`: how many sets it holds." Three rows in the case table, and a short "proves, does not prove" table |

## Errors

No new code.

| Code | When | Message | Fix |
|---|---|---|---|
| `sets_count_out_of_range` | An add would take the session past 60 sets | "The session on {day} has {n} sets; {k} more would make {n + k}, and a session holds at most 60." With one set held: "has 1 set" | "Send new_session true to start another session that day, or send fewer sets." |
| `invalid_field` | An add's notes would take the session's notes past 2,000 characters | "The session's notes would be {n} characters; the most is 2,000." | "Send shorter notes, or change the session's notes with edit_entry." |

## The eval cases

| Case | Seed | Says | Passes when |
|---|---|---|---|
| `lift_one_sentence` | None | "Back squat 3 sets of 5 at 100 kg, then bench press 80 kg for 8, 8 and 6" | One successful `log_lifts`, its sets holding Back Squat 100 × 5, Bench Press 80 × 8 and Bench Press 80 × 6. One live session, `set_count` 6 |
| `lift_set_by_set` | `log_lifts` as `bench`: Bench Press 80 × 5, today | "Bench press 80 kg for 5" | One successful `log_lifts` with Bench Press 80 × 5, never `new_session` true. One live session, the seeded one, `set_count` 2 |
| `lift_added_later` | `log_lifts` as `squat`: Back Squat 100 × 5, three sets, today | "Also did barbell rows, 3 sets of 10 at 50 kg" | One successful `log_lifts` with Barbell Row 50 × 10, never a Back Squat set, never `new_session` true. One live session, the seeded one, `set_count` 6 |

```json
{"id": "lift_one_sentence",
 "say": "Back squat 3 sets of 5 at 100 kg, then bench press 80 kg for 8, 8 and 6",
 "expect": {"function": "log_lifts", "ok_calls": {"log_lifts": 1},
            "inputs_include": {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5},
                                        {"exercise": "Bench Press", "weight_kg": 80, "reps": 8},
                                        {"exercise": "Bench Press", "weight_kg": 80, "reps": 6}]}},
 "db": [{"table": "lift_session", "count": 1, "all": {"set_count": 6, "date": "{{today}}"}}]}

{"id": "lift_set_by_set",
 "say": "Bench press 80 kg for 5",
 "seed": [{"as": "bench", "service": "log_lifts",
           "data": {"sets": [{"exercise": "Bench Press", "weight_kg": 80, "reps": 5}]}}],
 "expect": {"function": "log_lifts", "ok_calls": {"log_lifts": 1},
            "inputs_include": {"sets": [{"exercise": "Bench Press", "weight_kg": 80, "reps": 5}]},
            "inputs_forbid": {"new_session": true}},
 "db": [{"table": "lift_session", "count": 1, "all": {"id": "{{seed.bench}}", "set_count": 2}}]}

{"id": "lift_added_later",
 "say": "Also did barbell rows, 3 sets of 10 at 50 kg",
 "seed": [{"as": "squat", "service": "log_lifts",
           "data": {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5},
                             {"exercise": "Back Squat", "weight_kg": 100, "reps": 5},
                             {"exercise": "Back Squat", "weight_kg": 100, "reps": 5}]}}],
 "expect": {"function": "log_lifts", "ok_calls": {"log_lifts": 1},
            "inputs_include": {"sets": [{"exercise": "Barbell Row", "weight_kg": 50, "reps": 10}]},
            "inputs_forbid": {"sets.*.exercise": "Back Squat", "new_session": true}},
 "db": [{"table": "lift_session", "count": 1, "all": {"id": "{{seed.squat}}", "set_count": 6}}]}
```

| Case | Proves | Does not prove |
|---|---|---|
| `lift_one_sentence` | A workout said in one sentence becomes one call, one session, six sets, with the person's numbers | That three equal sets are sent as three items: `inputs_include` cannot count, so the set count is read from the row |
| `lift_set_by_set` | A set equal to one already logged that day is added to the same session, with no question and no second session. Before this work it failed: the call came back duplicate | That the assistant keeps doing it over five sets in one conversation: a run is one message. The seed stands in for the earlier sets, made before the run, so the assistant did not see them logged |
| `lift_added_later` | Told of one more lift, the assistant sends only the new sets, and they land in the day's session | That it never resends earlier sets it logged itself in the same conversation: no second turn. Only one phrasing |

On what a case can check: `lift_set` cannot be a `db` table (no `user_id`, not in `DB_TABLES`), so the set count comes from `set_count` on the session row. Two tables in one case is fine: each check names its own. `"weight_kg": 80` matches a stored 80.0. The pounds case is left out: units are another item.

Each new case goes into `evals/cases.json`, the case table of `docs/evals.md`, the README's count, and the counts pinned in `tests/test_evals.py:45` and `tests/test_feedback.py:976`.

## Work package: one builder

| Package | Files | Exit check |
|---|---|---|
| G1 | `app/services/lifts.py` (the add, its bests, its summary, its two refusals, the guard gone, its notes), `app/mcp_server.py` (`log_lifts`' description and arguments), `app/mcp_schemas.py` (`LogLiftsResult`), `app/calllog.py` (the known name), `docs/can-and-cant.md`, `evals/cases.json`, `evals/judge.py` (the seed service, its notes), `evals/run.py` (`set_count`), `docs/evals.md`, `README.md` (the count), `tests/test_gym_logging.py` (new). Existing test files only where an assertion now fails because of a decision, each change named in the report. Expected: `test_lifts.py` (its duplicate guard tests go or turn round), `test_mcp.py`, `test_feedback.py` (the published hash of `log_lifts`, the count), `test_evals.py`, and every test that logs two sessions on one date and means two, which sends `new_session` true | Every decision has a test. Three equal sets in three calls: one session, three sets, numbered 1 to 3, no duplicate. Two lifts in two calls: one session. `new_session` true: a second session that day, and the next call adds to it. A back-dated call adds to that date's session. A deleted session is not added to. Two live sessions on a date: the add goes to the later. The add keeps a version, and `restore_entry` with `undo_edit` takes back exactly the last add's sets and notes. A web edit form drawn before an add is refused. A delete's confirm code from before an add deletes nothing. An imported session added to keeps its key, and the same import sent again writes nothing. 60 sets then one more: refused, nothing added. Notes past 2,000 in all: refused, nothing added. A best set by an add is reported once, with the same value and "up from" that `get_log` then shows. A set that beats only an earlier set of the same session is no new best. The first session of a lift gives its mark, and no best, on every add. `allow_repeat` sent is taken and changes nothing. The same key and call twice: one add, the first answer back. Two adds at once: both land, one after the other. `get_trends` counts one session for a day said set by set. The eval cases load, and `set_count` reads right. Of the published list, only `log_lifts` differs. The tests for the files touched pass, plain and with `-W error` |

## Known limits

| Limit | Why it stands |
|---|---|
| Two workouts in one day are one session unless the person says it is a second one | The assistant cannot know. Nothing is lost: every set is there, and the day page shows them |
| A retry with no key adds the sets twice | The guard could not tell it from the next set. The summary's count shows it at once, and undo takes it back. With a key, nothing is added twice |
| Undo takes back the last add only, once. An earlier add is changed with `edit_entry` and the whole set list | Undo as he wanted it: immediate, one step |
| An assistant that resends sets it already logged in the same conversation logs them twice | The description says to send only new sets. `lift_added_later` tests one form of it. A conversation begun before the deploy holds the old description |
| A session dated after today, after a change of time zone, is not added to: a call for today starts a new one | The rule matches the date as stored. Rare |
| No way to split a session in two, or join two | Not asked for. `edit_entry` can move a whole session to another date |
| Days that already hold several sessions keep them | No migration (decision 13) |
| Existing tests that meant two sessions on one date change | Each change is named in the report. How many: not counted |

Not in this work: pounds and miles on a log (another item); the confirm card and "same as last time" (item 5, his re-look); timed holds, carries and sets over 30 reps; a web form that logs lifts; the exercise list; the guard of any other kind; telling assistants to always send a key; a second turn in the eval runner; a change to the instructions.

**An eval run follows the build.** `log_lifts`' description and arguments change, and every conversation holds the tool list. So the whole set runs: 29 cases, 3 runs each, on `claude-opus-5-5`. The last whole run of 26 cases cost $12.78 at list price, about $0.49 a case. 29 cases are about $14.25. A run of only the three new cases and the two that already touch lifts (`unknown_exercise`, `import_unknown_lift`) would be about $2.45.

**As built, 2026-11-15.** Two comment lines outside the file list, in `app/services/food.py` and `app/services/entries.py`, were made true: they named the lifts' guard. The first call of a day starts a session and has nothing for `undo_edit` to take back: it answers `nothing_to_undo`, and `delete_entry` takes it back. The same notes sent twice are joined twice.
