# Hardening contract: a small request does a small amount of work, and the database keeps its own rules

**Goal: what one call or one page costs stays small as a history grows, a delete stops when deleting is turned off, the export cannot be run in a loop, retention cleanup leaves the calls alone, and the database itself refuses a row no service would write.**

From a database review of 2026-11-17, read finding by finding against `main` at `a575a02` on 2026-11-17. The owner's order, the same day: "hardening first, then F, then G2". Two packages: H1 with no migration, H2 with migration `0012`.

## For the owner

**What changes for a person.** Nothing they read in chat: the same functions, the same words, the same figures.

| What | Today | After |
|---|---|---|
| A long lifting history | Every read that works out bests checks each session against every earlier set: 4.86 s of work at 2,000 sessions, 0.11 s of it the database | One pass: the work grows with the history, not its square |
| An old day | `get_log` loads every row from that day on: 601 food entries to show one day 600 days back | Only that day's rows |
| Pages | Today's page, Progress and a lift's page each work the lifting history out twice, and Goals does too while a lift goal is open | Once |
| Deleting turned off on Settings | A delete already under way still goes through | It is refused |
| The export | No limit: it can be run in a loop | One at a time, 5 an hour; a page says when to try again |
| Retention cleanup | Runs inside a person's call before it answers | Runs beside the calls, in small batches |
| A saved food named with letters that fold longer (65 of `ß`) | Taken on SQLite, refused by Postgres on the host | Taken on both |
| A row no service would write (a negative weight, a duplicate set number, a food entry on another person's saved food) | The database stores it | The database refuses it, from any writer |
| Is the server ready? | `/health` says OK without looking at the database | `/ready` also checks the database and its migration |

**The packages.** H1: the fixes with no migration (decisions 1 to 9). H2: migration `0012`, its rules, `/ready` (decisions 10 to 18). Both build on the commit the eval rebuild (G1) lands at. Their files do not overlap, so they can build at once in two worktrees. H2's unique set number needs H1's decision 9, so H1 is committed first, H2 goes on top of it, and H1 reaches `main` in a deploy of its own before H2.

**No eval run is expected.** No text an assistant reads changes. Decision 18 says how the code keeps `python -m evals.need` at no, run against G1's commit.

**His picks** are at the end: the closed lists the database checks, reading the live data before the deploy, Railway's healthcheck, Docker for the Postgres run, what to do if `evals.need` says yes anyway, and two deploys in place of one.

## The findings, read against the code

Each finding was read on `main` at `a575a02`. "Seen" means run on 2026-11-17 against a throwaway SQLite file in the lead's scratch folder, built by the migrations. No probe ran on Postgres: there was no Docker for this read.

| # | Found | Read in the code | Seen | Here |
|---|---|---|---|---|
| 1 | Bests cost the square of the history | `lifts.history` passes every earlier set of a lift to `metrics.prs` for each session (`app/services/lifts.py:187`, `:211`, `:214`), and `prs` scans them again (`app/metrics.py:165-183`). Callers: `log.py:110`, `progress.py:155`, `goals.py:294`, `routines.py:217`, `web.py:2485`, `:2577`. `goal_now_series` rebuilds each date's list of points (`metrics.py:460-474`) | 250 sessions of 5 sets 0.1 s, 500 0.33 s, 1,000 1.28 s, 2,000 4.86 s, load included; the load alone at 2,000: 0.11 s. `goal_now_series`: 0.14 s for 730 weigh-ins, 0.38 s for 1,460 lift points | H1: D4 to D7 |
| 2 | Paging bounds the answer, not the work | `get_data` (`app/services/data.py:819`) cuts its page from every row of the area (`:871-872`). The build log names both as known limits (`BUILD_LOG.md:1094-1095`) | Not run | Later |
| 3 | Retention cleanup runs inside a call | `calllog.finish` calls `sweep()` (`app/calllog.py:133`); the wrapper awaits `finish` (`app/mcp_server.py:712`) before it returns the answer (`:715`); the web's writes too (`app/web.py:1031`). "Last swept" moves before the deletes run (`calllog.py:146`), and a failed delete is swallowed (`:155-161`). Each delete is one statement over every old row. No index on `write_key.created_at` or `entry_version.created_at`; feedback's starts with `user_id` (`app/models.py:268`, `:286`, `:306`) | With every delete made to fail, "last swept" was set | H1: D8. H2: D11 |
| 4 | The export has no limit | A POST: the page limit counts GET and HEAD only (`web.py:872`, `:915-928`). `export_page` checks nothing (`web.py:2942-2953`) and builds the zip in memory (`:2933-2939`). `export_data` reads each table in its own statement with no isolation level set (`app/services/export.py:168-194`) | Not run | H1: D2 |
| 5 | An old day loads every later row | Food, weigh-ins, measurements and cardio take `date >= start` only (`app/services/log.py:118-131`); the end is applied in Python (`:107-108`). The lifting history is read whole (`:110`) | One day 600 days back: 1 food entry shown, 601 loaded | H1: D3 |
| 6 | A delete passes a switch changed under it | The wrapper reads the person's row (`mcp_server.py:655`) and checks the switch on it (`:663`) before the service runs. `delete_entry` locks and reloads the row (`app/services/entries.py:215`) and never reads `claude_can`. `set_claude_can` changes it under the same lock (`app/services/account.py:319`) | `claude_can` set to log, then `delete_entry` with its code: deleted | H1: D1 |
| 7 | A saved food's key can outgrow its column | Names up to 128 characters (`app/rules.py:96`) are casefolded into `name_key` (`app/services/food.py:376`), a `String(128)` (`models.py:346`). Routines keep 255 for a 64-character name (`models.py:366-367`, `rules.py:57`). The USDA key is cut to 128 (`app/services/usda.py:82`) | 65 of `ß`: saved, a 130-character key. Python 3.12's casefold, over every code point: at most 3 characters from one (16 such), so 128 can become 384. SQLite only; Postgres not run | H2: D10 |
| 8 | The schema checks nothing | No CHECK in any migration. "The services check them; the schema does not, so a later stage can add a value without a migration" (`models.py:12-13`). A food entry's saved food is keyed by its id alone (`models.py:392-393`) | A weigh-in of -5 kg with `logged_by` "robot" stored; a food entry on another person's saved food stored; 0 CHECK clauses. SQLite only | H2: D12 |
| 9 | The write lock also holds back reads' log rows | `lock_user` is `FOR UPDATE` on Postgres (`app/services/common.py:205-211`) | Not run | Later |
| 10 | `call_log.id` is 32 bits | `Integer` (`models.py:244`); `0001` builds it as `SERIAL` (offline Postgres SQL, seen) | Not run | H2: D13 |
| 11 | Account delete depends on a retry | The sign-in goes first, then the rows (`web.py:2963-2990`) | Not run | Later |
| 12 | Postgres checks are by hand | `tests/conftest.py` builds SQLite; earlier Postgres runs pointed a copy of the tree at a container (`BUILD_LOG.md:915`) | Not run | Later |
| 13 | `/health` never looks at the database | `app/main.py:189-190` | Not run | H2: D14 |

Two more things seen while probing, both of which shape H2:

- **A unique set number fails today's edits.** With a unique index on `lift_set (session_id, exercise_id, set_number)`, `edit_entry` of a session's set list raised `IntegrityError`: the new sets are written before the old ones are deleted (`entries.py:597`, and the same at `common.py:542`). An undo of an edit made before the index raised it too (re-checked on a scratch copy). An add to a session passed. Hence D9.
- **A table rebuild on SQLite loses rows.** A batch rebuild of `saved_food` and `lift_session` on a connection with foreign keys on, as `migrations/env.py` runs today (`app/db.py:68-74`), took `lift_set` from 18,754 rows to 0 and dropped the food link. With foreign keys off, every row and link stayed, and `PRAGMA foreign_key_check` found nothing. Migration `0004` saw the same trap and avoided rebuilds (`migrations/versions/0004_no_sample_data.py:15-18`). Hence D16.

## Decisions

### H1: no migration

| # | Decision | Why |
|---|---|---|
| 1 | **The delete switch is checked again under the lock.** `delete_entry` takes `may_delete`, a check its caller hands it; the MCP wrapper passes `functools.partial(not_allowed, "delete_entry")`. With `by` "claude", right after `lock_user` and before the write limit, for the preview and the delete alike, the locked row's `claude_can` goes through it, and a refusal is returned as it is: the wrapper's own `not_allowed`, word for word, links included. Its row reads refused, step permission (`calllog.py:82`). The wrapper keeps its early check. **It fails closed:** with `by` "claude" and no `may_delete`, the service lets the call on only when the locked row says log_delete, and otherwise raises, with no text, before anything changes, so the call is `internal_error` and nothing is deleted. The web passes no `by` (`web.py:2131`, `:2334`, `:2402`): the person's own click, never checked | The words stay in `mcp_server.py`, so no text moves between files (D18). Only `delete_entry` needs more than log (`mcp_server.py:249-261`), so it is the only one a switch change can catch mid-call |
| 2 | **The export: a gate in memory, and one moment read.** One export at a time per person; at most 5 per person in 60 minutes, counted at the start; at most 2 at once in the process. Counted per process, as the page limit is (`docs/contracts/safety.md`, decision 5), with its own clock as a seam. A refusal is a 429 message page with `Retry-After`, in the words below. On Postgres, `export_data` reads in one REPEATABLE READ, READ ONLY transaction, set through the session's execution options before its first read, never as typed SQL (D18); SQLite as today | The export writes no call-log row (`export_page` calls `_db`, not `write`: `web.py:2947`), so the count lives in memory. One moment read: a session's sets are never read after an edit its row was read before. SQLite is the tests and a laptop |
| 3 | **`get_log` bounds the end in SQL.** With a date sent, the four reads take `date <= end` beside `date >= start`, and `lifts.history` takes a last day: when the end is before today, only sessions dated on or before it are read. Undated, no upper bound: a row dated after today (a change of time zone) keeps its own block (`log.py:18-19`) | A best depends only on the sessions before it in the one order (`metrics.py:90-105`). With an end of today, a session dated later counts as today's and can come first, so the history's bound holds only for an end before today |
| 4 | **The history in one ordered pass.** Per lift, the best estimate so far, the heaviest weight so far and whether an earlier set exists; a session's new bests are judged against those, with the same strict "above" and the same rule that a first session sets no best (`metrics.py:165-183`). `metrics.prs` stays for `log_lifts` and an add, which compare one session against one query (`lifts.py:223-239`). Every field of `history` equal, value for value | Max is exact in any order, so a running best gives the same floats |
| 5 | **`get_routines` gets its weights from one narrow statement.** For the lifts its live routines name: each lift's latest session in the one order (effective date, then id), its real date and its heaviest set of the lift. At most one row a lift, built with SQLAlchemy's own constructs (D18); no history | It used `history` only for `last_top` (`routines.py:217`, `lifts.py:168-169`, `:207`) |
| 6 | **A page works the history out once.** A day page for today or later reads `get_log` once, undated (`web.py:1557`, `:1562`). Progress: `get_progress` takes a web-only keyword, as `exact` is (`progress.py:121-126`), and gives `best_sets` and `reps_only` from its own history; `_trend` stops reading one (`web.py:2485`). A lift's page: `_reps_only` goes, read from the same (`web.py:2577`). Goals: `goal_charts` (web only) also gives the closed goals, and the page stops calling `get_goals` (`web.py:2837-2848`, `goals.py:958`, `:984`) | No MCP result changes: no MCP function passes the keywords |
| 7 | **`goal_now_series` in one pass.** The points sorted once in the one order; each date's window found by two bounds that only move forward; a 7-day average summed fresh over its window in the order `goal_now` sums it; a 28-day best or lowest over its window; "latest" the last point on or before the date. Equal to `goal_now` on each date (`metrics.py:445-474`) | A running add and subtract can move a 2 dp average by a float's last bit; a fresh sum over at most 7 weigh-ins cannot |
| 8 | **Retention cleanup off the call.** `finish` writes the row and returns. When a sweep is due and none runs in the process, it starts on a thread of its own, through a seam (`calllog.start_sweep`), which `tests/conftest.py` makes inline for every test, so every test sees today's order. Each delete goes in batches of `SWEEP_BATCH` (1,000) rows a statement, built with the module's own `delete` (which `tests/test_trace.py:437-446` patches), each its own transaction, at most `SWEEP_BATCHES` (50) a table a run. A table's batches stop at its first failure, with today's one line for it, word for word (`calllog.py:161`); no new line. A group (the daily three, the hourly keys) is marked swept only when none of its deletes failed and each ended with a short batch; otherwise its last swept time stays as it was and it gets a retry time `SWEEP_RETRY_MINUTES` (10) on. A group is due when it has never swept in this process or its hours have passed, and no retry time of its own lies ahead. `tests/conftest.py` also clears the retry times before every test, so a failing sweep in one test never holds back the next: the existing sweep tests set only `_swept` and `_swept_keys` (`tests/test_trace.py:422-446`, `tests/test_edit_history.py:571-611`, `tests/test_feedback.py:859-892`, `tests/test_idempotency.py:642-680`) | A person's answer never waits on a delete. A failed sweep is tried again in minutes, not a day |
| 9 | **A session's set list is replaced old first.** An edit of the set list (`entries.py:597`) and an undo that puts sets back (`common.py:542`) delete the old sets and flush before the new ones go in | So no moment holds two sets with one session, lift and number (D12). What is stored is the same: today the new sets are new rows too |

Words of D2, on the page surface:

| When | Title | Line | Fix | `Retry-After` |
|---|---|---|---|---|
| One under way | "Export under way" | "An export of this account is being put together now." | "Wait for it to download, then try again if you need another." | 60 |
| 5 in the hour | "Too many exports" | "This account has been exported 5 times in the last hour." | "Try again in {n} minutes.", or "Try again in 1 minute.", as `too_many_pages` words it (`web.py:909`) | {n} x 60, {n} the minutes until the oldest counted export leaves the hour, at least 1 |
| 2 at once in the process | "Exports are busy" | "Spotter is putting other exports together right now." | "Try again in a minute." | 60 |

### H2: migration 0012

| # | Decision | Why |
|---|---|---|
| 10 | **`saved_food.name_key` becomes `String(384)`**: 128 characters at 3 each. No service change, no new refusal. A test walks every code point and holds both keys to 3 times their name's limit: `saved_food` 384 for 128, `routine` 255 for 64 | Casefold gives at most 3 characters from one (seen) |
| 11 | **Three indexes for the sweep**: `ix_write_key_created_at`, `ix_entry_version_created_at`, `ix_feedback_created_at`, declared with `index=True`, so the naming convention names them (`models.py:75-81`). `call_log` keeps `ix_call_log_started_at` (`models.py:238`) | A batch of D8 finds its rows by the time alone |
| 12 | **The rules the database keeps**, in the table below: (a) a sign rule and a wide ceiling on each number a person's row holds, looser than every range the services hold, so a range can change with no migration; the ceiling also keeps out Postgres's NaN and Infinity, which "above 0" lets through. (b) The closed lists, if he picks them. (c) Three goal shape rules, each binding only its own kind, so a new kind is free. (d) One set number per session and lift. (e) A food entry's saved food has the entry's owner: unique `(user_id, id)` on `saved_food`, and a key `food_entry (user_id, saved_food_id)` to it with no action of its own, DEFERRABLE INITIALLY DEFERRED, so it is checked at commit, after every action of the statement. The key on `saved_food_id` alone keeps its ON DELETE SET NULL, which nulls `saved_food_id` and leaves `user_id`; checked at commit, the new key is then met whatever order Postgres fires the two keys' triggers in. Seen on SQLite with the deferred key: a cross-owner link refused at commit, a hard delete of a linked saved food nulls only `saved_food_id`, an account with a linked entry deletes whole. The two uniques and the key are declared in `app/models.py` from column names, named by the convention (`uq_saved_food_user_id_id`, `fk_food_entry_user_id_saved_food`; no literal reaches 20 characters), or `compare_metadata` reports them. Declared, `FoodEntry.saved_food` has two paths to `saved_food` and stops mapping (`AmbiguousForeignKeysError`), so it names `foreign_keys=[saved_food_id]` and maps on `saved_food.id` alone, under `-W error` (seen on a scratch copy of the models). The service's own owner check stays (`food.py:183`) | Every rule was read against what the code writes today. A maintenance script, a later writer or a migration mistake is refused, never stored |
| 13 | **`call_log.id` becomes BIGINT on Postgres, with its sequence.** `ALTER TABLE ... TYPE BIGINT` and `ALTER SEQUENCE call_log_id_seq AS BIGINT`: a SERIAL's sequence is integer and does not follow its column. The model: `BigInteger` with an `Integer` variant on SQLite, whose INTEGER primary key is already a 64-bit rowid, so SQLite changes nothing and keeps numbering itself. No id check moves: no call-log id is taken from or given to a caller (`app/services/owner.py:41-64`; `OwnerCall` has no id, `app/mcp_schemas.py:1306-1321`), and `common.entry_id`'s ceiling of 2,147,483,647 (`common.py:170-187`) stays right for the entry tables, which stay INTEGER | The table that grows with every call, while it is small |
| 14 | **`/ready`, beside `/health`.** `/health` stays byte for byte (`docs/contracts/stage-1.md:45`; pinned by `tests/test_schema.py:288-291`, `tests/test_mcp.py:189`, `tools/probe.py:54`; polled by `evals/run.py:244`, `tools/load.py:288`). `/ready` reads the database's revision with alembic's own migration context, in a worker thread, never as typed SQL (D18): 200 "OK" when it answers with the code's head revision (read with alembic's script directory on the first `/ready`, then kept, so a missing `migrations/` answers 503 and never stops the app at start); else 503 "Not ready: database" or "Not ready: migration", never an error's text. Those words are built from parts, as D18 needs: "Not ready: migration" is 20 characters, `evals.need`'s `MIN_STRING` (`evals/need.py:42`). On both apps (`main.py:223-228`, `:244-254`), outside the page limit, with no call-log row. `tools/probe.py` checks it. Railway's healthcheck is set in its dashboard (`BUILD_LOG.md:199`); its path is not in the repo | A deploy can wait for a build that sees its database at its own revision, and the live check can read `/ready` in place of the deploy log's migration line |
| 15 | **The migration proves the rows first.** Before the rules, a count of the rows that break each one, and of the duplicate set numbers and cross-owner links. Any count above 0 stops the upgrade with one line a rule, its name and its count, never a value: "0012 stopped: ck_bodyweight_kg 2 rows. Nothing was changed." Postgres checks each rule again as it is added, in the run's one transaction (`migrations/env.py:55-60`), so a stop leaves the database at `0011`. Offline (`--sql`) there are no counts. The downgrade stops the same way and leaves the database at `0012`, in the same shape of line ("0012 down stopped: name_key 1 rows. Nothing was changed."; for the sequence, its name and "value past 2147483647" in place of a count): on both databases when a `name_key` is over 128 characters; on Postgres also when a call-log id or `call_log_id_seq`'s value is over 2,147,483,647, since the sequence can be past it with those rows swept. SQLite's `call_log` is not changed either way. Each stop has its own test | The live database holds one account, the owner's, written by every version of the code since stage 2. Its shape is not known from here |
| 16 | **On SQLite, migrations run with foreign keys off.** `env.py` turns them off on the migration's connection and then commits, before `context.configure`. The pragma does nothing inside a transaction, and SQLAlchemy begins one on the pragma itself: without the commit, alembic never commits the run, and it is lost with no error (seen on a scratch copy: 14 tables and no `alembic_version` row, against 18 tables and `0011` with the commit). After the migrations `PRAGMA foreign_key_check` must find no row, or the run fails. `0012` rebuilds ten tables on SQLite to add their rules, with pick 1's yes (`lift_session` and `entry_version` are rebuilt only for the closed lists): `user`, `lift_session`, `lift_set`, `bodyweight`, `measurement`, `goal`, `saved_food`, `food_entry`, `cardio_entry`, `entry_version`; one batch a table. `write_key` and `feedback` take only an index. On Postgres nothing is rebuilt: `ALTER TABLE` adds each rule, the BIGINT and the width | The probe above. A second probe, foreign keys off, added a CHECK by batch to `bodyweight`, `measurement`, `user` and `entry_version` over written rows, then took it off: every row stayed, every index's SQL word for word (the partial unique ones with their `WHERE deleted_at IS NULL`), `user`'s defaults and `entry_version`'s quoted `by` too, `compare_metadata` against the models found nothing, and a negative weigh-in was refused. The rebuild's copy also refused a bad row already there, as D15 wants. The same rules on both databases, so the whole SQLite suite writes through them: the best check of what the code can write today |
| 17 | **No column or result field goes, so the two-deploy rule does not apply.** One window is left: between `0012` committing and the new build serving, a build without D9 edits a set list in its old order and fails on the unique rule. A failed deploy after `0012` would leave that build serving for longer than seconds. So H1 reaches `main` in a deploy of its own before H2 (Open, 6): the build serving when `0012` runs already has D9 | `0004` accepted a window of seconds (`BUILD_LOG.md:673`), and its note says the next such change ships in two steps, code first |
| 18 | **No text an assistant reads changes, and no eval case.** `evals.need` part (a), the published surface and `whoami`'s answer, does not move. Part (b) compares, per file under `app/` but the pages, every string literal of 20 or more characters (`evals/need.py:42-43`, `:74-87`, `:291`): a CHECK's text, a typed constraint or index name, a new log line or the `not_allowed` words moved would make it say yes though no assistant reads them. So: only the CHECK rules' text lives in the migration, never in `app/models.py`, which declares the two uniques, the owner key, the indexes and the types from column names (D12); no typed SQL in `app/` (D2, D5, D14 use SQLAlchemy's and alembic's own constructs); every new name comes from the naming convention; `/ready`'s words are built from parts under 20 characters; no new log line; D1 leaves the words in `mcp_server.py`, and its fallback raises with no text. Expected: "evals needed: no", run as exit check 5 says | A yes owes a whole eval run at the release (`CLAUDE.md`, Release) |

### The rules of D12

Names follow the convention `ck_<table>_<rule>` (`models.py:80`). "Null or" means the column is nullable and a null passes.

| Table | Rule | Holds | The services today |
|---|---|---|---|
| `bodyweight` | `kg` | above 0, at most 1,000 | 20 to 400 (`rules.py:51`) |
| `lift_set` | `weight_kg` | 0 to 1,000 | 0 to 500 (`rules.py:48`) |
| `lift_set` | `reps` | 1 to 1,000 | 1 to 30 (`rules.py:47`) |
| `lift_set` | `set_number` | 1 to 1,000 | from 1 per lift, at most 60 a session (`lifts.py:135-145`, `rules.py:50`) |
| `measurement` | `value` | 0 to 1,000,000 | each kind's range, at most 200,000 steps (`rules.py:60-70`) |
| `food_entry` | `kcal` | 0 to 100,000 | 0 to 5,000, a worked-out number too (`rules.py:81`, `food.py:221-229`) |
| `food_entry` | `servings` | null or above 0, at most 1,000 | 0.1 to 50 (`rules.py:82`) |
| `food_entry` | `grams` | null or above 0, at most 100,000 | 1 to 5,000 (`rules.py:83`) |
| `saved_food` | `kcal_per_serving` | 0 to 100,000 | 0 to 5,000 (`rules.py:84`) |
| `saved_food` | `grams_per_serving` | null or above 0, at most 100,000 | 1 to 5,000 (`rules.py:85`) |
| `cardio_entry` | `minutes` | above 0, at most 10,000 | 1 to 600 (`rules.py:86`) |
| `cardio_entry` | `distance_km` | null or above 0, at most 10,000 | 0.01 to 300 (`rules.py:87`) |
| `cardio_entry` | `speed_kmh` | null or above 0, at most 1,000 | 0.1 to 60, a worked-out speed too (`rules.py:89`) |
| `cardio_entry` | `incline_pct` | null or 0 to 100 | 0 to 40 (`rules.py:88`) |
| `cardio_entry` | `avg_hr` | null or 1 to 1,000 | 30 to 240 (`rules.py:90`) |
| `cardio_entry` | `machine_kcal` | null or 0 to 100,000 | 1 to 5,000 (`rules.py:91`) |
| `cardio_entry` | `estimated_kcal` | null or 0 to 1,000,000 | MET x 3.5 x kg / 200 x minutes (`metrics.py:188-191`): at most 49,560 today, 11.8 MET (Jump Rope) at 400 kg for 600 minutes |
| `cardio_entry` | `estimate_weight_kg` | null or above 0, at most 1,000 | a weigh-in, 20 to 400 (`models.py:420`) |
| `goal` | `start_value` | 0 to 1,000,000 | each kind's range (`goals.py:115-117`) |
| `goal` | `target_value` | null or 0 to 1,000,000 | a lift above 0 to 666.67, body weight 20 to 400, 1 to 600 minutes, a measurement's range |
| `goal` | `distance_km` | null or above 0, at most 10,000 | 0.1 to 300 (`goals.py:117`) |
| `goal` | `exercise` | a lift or cardio_best goal has an exercise | `goals.py:120`, `:848` |
| `goal` | `distance` | a cardio_best goal has a distance | `goals.py:849` |
| `goal` | `measurement` | a measurement goal has a measurement kind | `goals.py:849` |
| `user` | `calorie_target_kcal` | null or above 0, at most 100,000 | 800 to 10,000 (`rules.py:92`) |
| `user` | `target_sessions_per_week` | null or 0 to 1,000 | 0 to 14 (`rules.py:93`) |
| `user` | `target_cardio_minutes_per_week` | null or 0 to 100,000 | 0 to 3,000 (`rules.py:94`) |
| `lift_set` | unique | `(session_id, exercise_id, set_number)` | `lifts.number_sets`; D9 |
| `saved_food` | unique | `(user_id, id)` | the target of the next row |
| `food_entry` | key | `(user_id, saved_food_id)` to `saved_food (user_id, id)`, checked at commit | `food.py:183` |

**The closed lists, his pick.** Each would be a rule as above; a new value later then needs a one-line migration.

| Table | Rule | Holds |
|---|---|---|
| `lift_session`, `bodyweight`, `measurement`, `food_entry`, `cardio_entry` | `logged_by` | web, claude or import (`models.py:32-33`) |
| `user` | `claude_can` | log_delete or log (`models.py:35`) |
| `user` | `weight_unit`, `distance_unit` | null, kg or lb; null, km or mi (`rules.py:122-123`) |
| `goal` | `direction`, `status` | up or down; open, hit or dropped (`models.py:38-39`) |
| `entry_version` | `by`, `why` | web or claude; edit or undo (`models.py:293-294`) |

Never checked, since they are meant to grow: `goal.kind`, `measurement.kind` and `unit`, `food_entry.meal` and `source`, `feedback.topic`, `exercise.kind` and `category`. No rule on `call_log`: a refused row is a lost row (`calllog.py:112-114`). `models.py:12-13` changes to say which lists the database checks.

## What changes for a model

Nothing. The instructions, `tools/list` for everyone and for the owner, the resources and every result keep their bytes; `tests/test_feedback.py`'s `PUBLISHED` passes unedited. One refusal can now come later in a call (D1), in the words it already has.

## The packages

The eval rebuild (G1) changes `evals/`, `docs/evals.md` and six test files: `tests/test_evals.py`, `tests/test_sixteen_weeks.py`, `tests/test_hi_there.py`, `tests/test_inputs.py`, `tests/test_routines.py`, `tests/test_units.py` (seen uncommitted in the working tree on 2026-11-17). G1 is committed first, and both packages build on that commit. Neither touches `evals/`. Of G1's test files, only `tests/test_routines.py` meets hardening: it counts `history`'s statements (H1, D5). None posts to `/settings/export`; `tests/test_hi_there.py:778` only lists its route. A file each package does not list is not touched.

| Package | Files | Exit check |
|---|---|---|
| **H1** | `app/services/entries.py` (D1, D9), `app/mcp_server.py` (D1), `app/web.py` (D2, D6), `app/services/export.py` (D2), `app/services/log.py` (D3), `app/services/lifts.py` (D3, D4), `app/metrics.py` (D4, D7), `app/services/routines.py` (D5), `app/services/progress.py` (D6), `app/services/goals.py` (D6), `app/calllog.py` (D8), `app/services/common.py` (D9), `tests/conftest.py` (the inline sweep, the retry times cleared), `tests/test_hardening.py` (new). Existing test files only where an assertion fails because of a decision, each named in the hand-over. Expected: `tests/test_routines.py`, whose `GET_STATEMENTS = 5` (`:1165` at `main`, asserted at `:1179`) counts the user, the routines and `history`'s three; D5 makes it 3, the user, the routines and the one weights statement, with its comment saying so (seen on a scratch copy: the review's one-statement stand-in gives 3), and its no-routine case stays 2 (`:1182`). No shape keeps 5 without two needless statements. And `tests/test_web_goals.py`, which hands the Goals page its own `get_goals` (`:81`, `:185-186`, `:1854-1868`). Not expected to move: `tests/test_charts.py` reads `_trend(...)["bodyweight"]` (`:961`, `:1053`, `:1070`), which stays; `tests/test_data.py:1146-1200` counts `history`'s statements, and D4 keeps its one load (`lifts.py:177-180`) | Every decision has a test of its own. The existing files pass unchanged but those named. The tests for the files touched pass, plain and with `-W error` |
| **H2** | `migrations/versions/0012_hardening.py` (new), `migrations/env.py` (D16), `app/models.py` (D10 to D13 and the note), `app/main.py` (D14), `tools/probe.py` (D14), `tests/test_schema.py` (`SQLITE_TYPES` gains `BigInteger`, `:382`; the offline SQL checks), `tests/test_hardening_schema.py` (new) | As H1, and the Postgres run below |

**Order.** G1's commit first. H1 and H2 build on it, and may build at once in two worktrees: their files do not overlap. H2's rules make H1's D9 needed by the whole suite, so H1 is committed first, H2 is put on H1, and H2's commit runs the whole suite through the hook.

## The tests

### H1, `tests/test_hardening.py`

- **D1.** The review's sequence through `/mcp`: a `delete_entry` whose service sees `claude_can` turned to log after the wrapper's check (the test wraps `services.delete_entry` so `set_claude_can` commits log right before the real one runs). The answer is `not_allowed`, word for word the wrapper's, links included; nothing is deleted; the row reads refused, step permission. The same for a preview. Called as the web calls it, with no `by`, it still deletes. With log_delete, a delete as today. The fallback: `by` "claude" with no `may_delete` on a log account raises, nothing is deleted, and through `/mcp` it would be `internal_error`; on a log_delete account it deletes, so the direct calls in `tests/test_switch.py`, `test_idempotency.py`, `test_inputs.py`, `test_routines.py` and `test_units.py` pass unedited.
- **D2.** The sixth export in an hour, a second while one runs (a seam holds the first) and a third at once in the process each get their 429 page with `Retry-After`; nothing is built. The clock is a seam: no real time. Otherwise the zip is as today.
- **D3.** A load counter on each model, as the probe had: one day 600 days back loads only that day's rows. Every field of `get_log` equal to today's, for every day of the 16-week seed (`evals/sixteen_weeks.py`, read only) and a 2-year generated history, undated and dated; an undated read still gives a later-dated row its block.
- **D4 to D7, the oracle.** Frozen copies of today's `lifts.history`, `metrics.prs`, `metrics.goal_now_series` and `get_routines`' weights live in the test file. Each new result equals the oracle's, with `==`, on the 16-week seed, the 4-week seed (`evals/four_weeks.py`, read only) and generated histories that hold sessions dated after today, 0 kg sets, sets over 10 reps, equal bests, two sessions a day and a lift never above 0 kg, each at several todays; `goal_now_series` for lift, body weight, measurement and cardio_best, with gaps and dense days.
- **Bounded work, by count, not by clock.** `metrics.epley` is called at most twice a set by `history` (today, once per earlier set per session). `goal_now_series` sorts its points a fixed number of times whatever the number of dates. `get_routines` sends one statement for its weights and never calls `history` (patched to fail). Each page of D6 builds the history once (a counter), and its existing page tests pass unchanged.
- **D8.** The default starter: a call returns while its sweep, held by an event, still runs, and a second due sweep does not start beside it. 2,500 old rows go in 3 statements of at most 1,000. At the cap, the rest stays, "last swept" stays as it was and the group is due again after 10 minutes (`db.utcnow` patched). A failing delete leaves "last swept" as it was, sets the retry time and logs today's one line for its table; `tests/test_trace.py:437-446`'s patch of `calllog.delete` still reaches every batch. A retry time left by one test never holds back the next. Every existing sweep test passes unedited (read for this contract): `tests/test_trace.py:422-446`, `tests/test_edit_history.py:571-611`, `tests/test_feedback.py:859-892`, `tests/test_idempotency.py:642-680`.
- **D9.** With a unique index on `(session_id, exercise_id, set_number)`, made by the test when the schema has none: an edit of a set list with the same numbering, its undo and a second edit all pass, and the sets stored are today's.

### H2, `tests/test_hardening_schema.py` and `tests/test_schema.py`

- Every rule of D12 is in the built database, read from it (`sqlite_master`, or `pg_constraint` on Postgres): the same list as the tables above, so a later migration that loses one fails here.
- Each number rule refuses a direct insert one step outside it and takes one at its edge. Each list rule refuses a value off its list. The goal shape rules refuse a lift goal with no exercise. A duplicate set number is refused. A link to another person's saved food is refused. A cross-owner link is refused at commit, not at the statement. A hard delete of a linked saved food nulls only `saved_food_id`. An account with a saved food and a linked entry deletes whole. `FoodEntry.saved_food` maps under `-W error` and joins on `saved_food.id` alone.
- `0012` up, down and up on SQLite over rows of every kind written at `0011` by the services: every row, set and link kept, `foreign_key_check` clean, the models and the database agree (`tests/test_schema.py:51-54`, `:462-477`). A planted bad row at `0011` stops the upgrade with D15's line, counts only, and the database stays at `0011`. A 129-character `name_key` planted at `0012` stops the downgrade with D15's line, and the database stays at `0012`; once it is gone, the downgrade runs.
- Inside a migration on SQLite, `PRAGMA foreign_keys` reads 0, and after the run `alembic_version` is at head, every table is there and the app's engine still has foreign keys on.
- A 128-character name of `ΐ` (U+0390) saves, with a 384-character key. The code point walk of D10.
- On SQLite a new call-log row still gets its id.
- `/ready`: 200 "OK" at head; 503 "Not ready: migration" with `alembic_version` changed by the test; 503 "Not ready: database" with no database (as `tests/test_schema.py:288` builds one); on the not-configured app too; never counted by the page limit; no call-log row. `/health` as before.
- The offline Postgres SQL (`tests/test_schema.py:69-89`) holds every rule, the two BIGINT statements, the width and the three indexes.

### The Postgres run

After the join, on a copy of the joined tree pointed at a throwaway Postgres 17 container, as before (`BUILD_LOG.md:915`, `:1061`). Docker by his okay; the container goes with its volume after.

1. Migrate an empty database to `0011`. With `main`'s own code at `0011`, write rows of every kind through the services, for two accounts: lifts with an edit and an undo, weigh-ins, measurements, food with a saved food and a USDA row, cardio, a goal of each kind, a routine, feedback, a keyed write, and 100 call-log rows.
2. Upgrade to `0012`. Every rule is in `pg_constraint`, the owner key deferrable and initially deferred; `call_log.id` and `call_log_id_seq` are bigint (`pg_sequences`); `name_key` is `varchar(384)`; the three indexes exist. Every row of step 1 is there.
3. Refusals and deletes, none of which leaves a row past what `0011` holds: by direct insert, each rule refuses a value outside it, the unique a duplicate set number, and the owner key a cross-owner link at commit. On the second account: a hard delete of a linked saved food nulls only the entry's `saved_food_id`; then the account is deleted, saved food and linked entries with it.
4. Down to `0011` and up again: rows as step 3 left them, rules gone and back.
5. D15's downgrade stops, each alone, each leaving the database at `0012` with D15's line: a saved food named with 65 of `ß` (a 130-character key), deleted straight after its stop; then a call-log row past 2,147,483,647 after `setval`; and with that row deleted, the sequence's value alone. Then `setval` goes back to the highest id, and the downgrade to `0011` runs.
6. At `0011`, a bad row planted stops the upgrade with D15's line, and the database stays at `0011`. The row goes, and the upgrade to `0012` runs.
7. D1 with two connections: a delete waits on the lock while `set_claude_can` commits log, then is refused. D2: an export that reads `lift_sessions`, then a set committed by another connection, then `lift_sets`, holds the state before the set.
8. The test files there: `tests/test_hardening.py`, `tests/test_hardening_schema.py`, `tests/test_entries.py`, `tests/test_edit_history.py`, `tests/test_lifts.py`, `tests/test_gym_logging.py`, `tests/test_food.py`, `tests/test_import.py`, `tests/test_bodyweight.py`, `tests/test_cardio.py`, `tests/test_measurements.py`, `tests/test_goals.py`, `tests/test_routines.py`, `tests/test_log.py`, `tests/test_progress.py`, `tests/test_export.py`, `tests/test_calllog.py`, `tests/test_trace.py`, `tests/test_idempotency.py`, `tests/test_feedback.py`, `tests/test_users.py`, `tests/test_update_settings.py`, `tests/test_web_account.py`. Every failure is read and named in the build log.
9. Timed there and written in the build log: `history` at 2,000 sessions, `get_log` of a day 600 days back, a sweep over 100,000 old call-log rows.

## Eval cases

None. No text an assistant reads changes and no route a model takes moves. The tests above hold every figure to today's.

## The exit check

1. Every row of each package's exit check holds.
2. On the joined tree: `history` at 2,000 sessions of 5 sets on SQLite is under 0.5 s, load included (4.86 s before), measured and written in the build log.
3. The Postgres run passes, steps 1 to 9.
4. The reviewer, on a frozen copy, repeats each probe of the findings table and the two below it: each is refused, bounded or equal as the decisions say.
5. `python -m evals.need <G1's commit>`, run in the hardening branch's worktree, says no (it needs a git repository: in a plain export it says "could not tell" and exits 2). Against its base, the commit G1 lands at, it reads only what hardening changes; never run it in a tree that still holds G1's uncommitted files.
6. The tests for the files touched pass, plain and with `-W error`. The hook runs the rest.
7. Before H2's deploy: his export read against the rules (Open 2), with nothing found.
8. After each deploy: `/health` 200, `/mcp` 401, `/` 200; after H2's, `/ready` 200 too.

## Docs at the release

The lead writes them in the release's docs commit, after both packages, as `CLAUDE.md` asks of a built record.

| File | What |
|---|---|
| `BUILD_LOG.md` | One entry: the two packages, the reviews, the Postgres run, the timings, the known limits |
| `docs/contracts/trace.md`, `idempotency.md`, `edit-history.md`, `feedback.md` | A dated note each: their retention runs beside the calls, in batches (D8); the periods are unchanged |
| `CLAUDE.md` | The live check reads `/ready` 200 in place of the deploy log's migration line, once his pick 3 is in |
| `docs/can-and-cant.md`, `README.md` | Nothing: neither names the sweep's timing, an export limit or `/health` (read for this contract) |

## Known limits

| Limit | Why it stands |
|---|---|
| The export gate and the page limit count per process | One process today (`docs/contracts/safety.md`, decision 5) |
| The export still builds its zip in memory | Small accounts. Streaming or a background job: later |
| A Postgres export is one transaction: a gap of 60 s between two of its reads would end it (`app/db.py:35`) | Not near it at today's sizes |
| Each process sweeps on its own: two would delete the same rows twice | Harmless. A Postgres advisory lock: later |
| A sweep that keeps failing tries every 10 minutes and logs each time | Today's line, no new one (D18) |
| `0012` runs under the app's 15 s statement and 10 s lock limits (`app/db.py:33-37`) | One account's rows: well inside. A large table later needs its own limits and an index built concurrently |
| A build without D9, serving on `0012`, fails a set-list edit | D17: H1 deploys first |
| The number rules are wide: they catch a sign, a NaN or a slip by a factor, not a value a little past a service's range | So a range can change without a migration |
| A later SQLite rebuild of a table must carry its rules | `tests/test_hardening_schema.py`'s list catches a lost one |
| Progress' best sets and most reps now come from `get_progress`'s own date, where `_trend` used the glance's: across the person's midnight, one render can credit a best to the other of two same-evening sessions | The order's accepted edge (`metrics.py:101-103`) |
| `/ready` answers anyone, as `/health` does: one small read a hit, saying only ready or not and why | The page limit is for signed-in pages |
| Whether Railway keeps the old build serving when a pre-deploy step fails is not checked | D15 stops before any change either way |
| `0012` holds ACCESS EXCLUSIVE locks on the tables it changes until its one commit, each wait at most 10 s. An open export (D2 holds its reads' locks for its transaction) or a write holding the person's row can make that wait run out: the deploy then fails before any change, and is run again. Calls stall behind the migration's locks meanwhile, and a call-log start that waits past its own 10 s is a lost row, so the write limit counts one fewer | One account, short transactions. Not run on Postgres |
| D15 counts rows, not the values kept as JSON in `entry_version.before`: an undo that puts back a value a rule refuses would fail as `internal_error` | Every rule is looser than every range the services hold today; older ranges were not read. Open 2's check reads the kept versions too |

## Later, not in hardening

| Item | Note |
|---|---|
| `get_data` and `what_is_logged` in SQL, paging by key | Finding 2; data on request's known limits (`BUILD_LOG.md:1094-1095`) |
| `FOR NO KEY UPDATE` for ordinary writes | Finding 9, tried on Postgres with two sessions |
| Account delete as a recorded request a worker retries | Finding 11 |
| Call-log partitioning, a budget for log writes | Finding 10's rest |
| USDA cache eviction | It never drops a row |
| A version number on edits | In place of `updated_at` as the token |
| An index on `food_entry (saved_food_id)` | For the SET NULL key's search on a saved food's hard delete: only an account delete does one today |
| A Postgres test lane each release, migrations with their own time limits | Finding 12 |
| Backups and a restore tried for real | The owner's host decision |
| Age, gender and a first name | Account foundations, F |

## Open for the owner

| # | Pick | The lead's pick |
|---|---|---|
| 1 | The closed lists of D12 as database rules: a new value later (a new way rows come in, a new unit) then needs a one-line migration | Yes, for those eight. Never for the lists meant to grow |
| 2 | Read the live data before the deploy: he downloads his export from Settings, the lead checks its CSVs against the rules on this PC, and the file is deleted after. The check reads every row the rules touch, and the number rules on `entry_versions.csv`'s kept values too. It also leaves him a copy of his data before a migration; whether the host keeps backups is not in the repo | Yes, beside D15's own count |
| 3 | Railway's healthcheck to `/ready`, his click in its dashboard (`BUILD_LOG.md:199`), after the deploy that adds it. Its path today is not in the repo | Yes |
| 4 | Docker for the Postgres run | His okay, as every migration's |
| 5 | If `evals.need` says yes only for a line no assistant reads: run the evals or not. Last full run: $35.2311 (`BUILD_LOG.md:1101`), Claude's side; nothing on Railway | Not, with the line named in the build log |
| 6 | Two merges, two deploys: H1 first, then H2 with `0012` (D17), each on his word | Yes |

His answer, 2026-11-17: "yes to all six". The lead's picks above are his.

## Review fixes, 2026-11-17

One review on a frozen copy: 2 must fixes, 6 should fixes, 10 nits. Each claim changed here was re-checked on a scratch copy first.

| Finding | What changed |
|---|---|
| Must 1: D5 breaks `tests/test_routines.py:1165` | H1 lists the file as an expected change, 5 to 3 statements with its comment, the no-routine case still 2; Order: G1's commit first, H1 on it. 3 re-checked; no shape keeps 5 without needless statements |
| Must 2: the Postgres run's step 3 blocks step 4's downgrade | Step 3 leaves no row past `0011`'s; the wide rows moved to a new step 5, the downgrade stops, then a cleanup and a downgrade that runs; the upgrade stop is step 6 |
| Should 1: the owner key in the models breaks `FoodEntry.saved_food` | D12 and D18: the uniques and the key declared from column names; the relationship names `foreign_keys=[saved_food_id]`. Re-checked: `AmbiguousForeignKeysError` without it, maps under `-W error` with it |
| Should 2: the PRAGMA's silent half migration | D16: commit after the pragma, before `context.configure`; its test reads the pragma inside a migration and `alembic_version` at head. Re-checked: 14 tables and no version without the commit |
| Should 3: the owner key's trigger order on Postgres, and a cited test that does not hold it | The key is DEFERRABLE INITIALLY DEFERRED (re-checked on SQLite: refused at commit, both deletes pass); Postgres step 3 checks both deletes; the `tests/test_schema.py:141` cite is gone, with a test of its own |
| Should 4: D1 fails open | D1's fallback: no `may_delete` lets a delete on only at log_delete, else raises with no text; one test. The direct test calls run at log_delete, so they pass unedited |
| Should 5: exit check 5 in the main tree | Exit check 5 runs against G1's commit, in the hardening worktree |
| Should 6: D15's downgrade stop untested | A SQLite test with a 129-character `name_key`, and Postgres step 5 |
| Nit 1: "1 minutes", `Retry-After` | D2's table words 1 minute as `too_many_pages` does, and gives each refusal its `Retry-After` |
| Nit 2: D9's undo not seen | Re-checked: the undo raises `IntegrityError` under the index; the findings note says so |
| Nit 3: the web passes no `by` | D1 and its test say so, with the three lines |
| Nit 4: the patched `calllog.delete` | D8 and its test: the batches use the module's `delete` |
| Nit 5: `/ready`'s words and the head read | D14: the split is required at 20 characters; the head is read on the first `/ready` |
| Nit 6: the sequence past 2,147,483,647 | D15 counts `call_log_id_seq`'s value too; Postgres step 5 tests it alone |
| Nit 7: "0.10 s" | 0.1 s |
| Nit 8: D2's cite | `web.py:2947`, `_db` not `write` |
| Nit 9: two deploys | Exit check 7 is Open 2's read before H2's deploy; 8 runs after each deploy |
| Nit 10: "ten tables" | D16 says it holds with pick 1's yes, which he gave |
| The known limit on 0012's locks | Added, marked not run |

A re-check on a fresh frozen copy found all eight must and should fixes done, and three nits, fixed here: exit check 5 runs in a worktree only, the Postgres run's step 5 deletes the long-named food straight after its own stop, and D15 gives the downgrade's line. Left as a known gap: D16 does not cover `env.py`'s passed-in connection path, which nothing in the repo uses today.

## Built, H1, 2026-11-17

H1 was built as decided, reviewed on a frozen copy (no must fix, 1 should fix, 2 nits) and fixed in one round. `history` at 2,000 sessions of 5 sets, load included, on SQLite: 0.188 s by the builder, 0.197 s by the reviewer, against 4.56 s and 5.12 s for `fd83753` on the same rows. `python -m evals.need fd83753`: no.

| Departure | Why |
|---|---|
| `tests/test_web.py`, 11 tests | The `get_progress` stand-ins take D6's web-only keyword; one test reads `reps_only` from `get_progress` in place of `_trend`; one counts one history read, not two; `_trend` with no account gives `{"bodyweight": None}` |
| `tests/test_edit_history.py`, one test | With D9's old-first replace, SQLite gives the new sets the old row ids, so the test counts the inserts into `lift_set` in place of comparing ids. What is stored is the same |
| `tests/test_mcp.py` | D1's `may_delete` reaches the service; `mcp_server.DELETE_CHECK` is the one `functools.partial(not_allowed, "delete_entry")`, built once |
| `tests/test_goals.py`, `tests/test_four_weeks.py` | Both pinned `goal_charts`' keys; D6 adds `closed` |
| `tests/test_page_rules.py` | Its Goals states hand `goal_charts` the closed goals, so the Closed goals card is drawn and checked again (the review's should fix) |
| `app/web.py`, the day page | When the person's midnight passes between the page's date and `get_log`'s, today's page reads that day once more by its date, where it would 404 (the review's nit; `fd83753` drew it). A test crosses midnight through the clock seam |

Not run here, left for the Postgres run: D2's one-moment read, D1 with two connections, D5's weights statement, D8's batched deletes.

## Built, H2, 2026-11-17

H2 was built as decided and reviewed on a frozen copy joined with H1: no must fix in H2's files, 4 nits. Two of them were fixed at the join. `python -m evals.need fd83753`: no.

The join changed 8 test files outside H2's list. The contract did not name them:

| File | Why |
|---|---|
| `tests/test_edit_history.py`, `tests/test_feedback.py`, `tests/test_routines.py`, `tests/test_switch.py`, `tests/test_units.py` | Each pinned the head at 0011; now 0012 |
| `tests/test_trace.py`, `tests/test_idempotency.py`, `tests/test_edit_history.py`, `tests/test_feedback.py`, `tests/test_switch.py` | Each held `compare_metadata`'s exact list at 0005 to 0009. Those lists now also hold 0012's indexes, uniques and key, and `name_key`'s width as a nested entry. Each call goes through `before_0012` (`tests/test_hardening_schema.py`), and the expected lists are unchanged |
| `tests/test_switch.py`, the four unknown stored values | `ck_user_claude_can` refuses them. The test asserts the refusal and checks `level_of` and `not_allowed` directly |
| `tests/test_feedback.py`, the owner's switch | "read" cannot be stored. The test is now at log, the lowest value the database takes, and is renamed |
| `tests/test_data.py`, another person's saved food | The owner key refuses the row at commit. The test asserts that, and that the person's food read is empty |

| Left open by the contract | Built |
|---|---|
| The stop | `sys.exit` with the line, exit 1. Several broken rules give one full line each |
| The downgrade's call-log stops on Postgres | `0012 down stopped: call_log.id N rows. Nothing was changed.` and `0012 down stopped: call_log_id_seq value past 2147483647. Nothing was changed.` |
| The owner key's count | It also counts a link whose saved food is missing, which the key refuses too |
| A failed key check after the run (SQLite) | The line says the migrations ran and the revision the database is at: SQLite has committed each migration by then |

Found while building and fixed:
- the migration had left out `ck_cardio_entry_logged_by`;
- the key check after the run committed the connection, which hid a missing commit after the pragma. It now only reads.

Still a known gap, with the review's probe: on `env.py`'s passed-in connection path a SQLite rebuild runs with foreign keys on and takes every lift set. The check after cannot see it, since the cascade leaves no orphan. Nothing in the repo uses that path.

Not run here, left for the Postgres run:
- D15's counts on Postgres;
- the NaN refusal;
- the call-log id and sequence stops;
- `pg_constraint` and the deferred key's account and hard deletes;
- the shared-database tests of `tests/test_hardening_schema.py`;
- whether a reader that holds a later table before `"user"` deadlocks with 0012 (the review's question 1).

## The Postgres run, 2026-11-17

On `bc5bb71` (H1 and H2 joined), a throwaway Postgres 17 container, removed with its volume after. Steps 1 to 7 and 9 passed; step 8 gave 1,719 of 1,720.

| Step | Seen |
|---|---|
| 1 to 6 | Rows of every kind written by `fd83753`'s code at `0011`, kept through the upgrade, the down and up, and each stop. Every rule in `pg_constraint`; NaN, Infinity and -Infinity each refused; a cross-owner link taken, then refused at commit; `call_log.id` and its sequence bigint; the downgrade's three stops each gave their line and left the database at `0012` |
| 7 | D1: the delete waited on the row lock while the switch went to log, then got `not_allowed` word for word; nothing was deleted. D2: the export saw 9 sets, the state before a set added mid-export (11 with the one-moment read turned off) |
| 8 | The one failure was the test's own: `test_the_goal_shape_rules_bind_only_their_own_kind` used a kind of 18 characters in a `String(16)` column, which SQLite never checks. Now `added_later`. `tests/test_routines.py` 96 of 96: `get_routines` sends 3 statements, one for its weights |
| 9 | `history` at 2,000 sessions: 0.212 s (4.576 s on `fd83753`). `get_log` of a day 600 days back: 0.136 s (4.648 s). A sweep of 100,000 old call-log rows: three runs, 0.57 s, 0.34 s and 0.01 s of work, 50,000 rows at most a run, the 10-minute retry between them, the 100 recent rows kept |

A correction to the owner's table at the top: for an old day, `get_log` now loads only that day's food, weigh-ins, measurements and cardio, but still every lift session up to that day, since its bests need the earlier sessions (D3, as decided). Not run: the deadlock question the H2 note leaves open.
