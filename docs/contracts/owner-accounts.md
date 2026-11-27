# Owner accounts contract

**Goal:** the owner can see every account on one page, and can suspend an account and lift the suspension,
with a reason and a record of each action.

Fixed 2026-11-27, read against the code at `06cf974`. This is the first part of the account foundations.
Left for later, on purpose: plans, grants, payments, plan limits, consent records, how a person arrived,
invite codes, and any rule about a person who deletes an account and comes back.

## For the owner

| | Before | After |
|---|---|---|
| Who uses Spotter | `owner_calls` lists accounts that made a call in a window | `/owner/accounts` lists every account, with what each has logged |
| A bad account | Nothing can stop it short of the database | One form suspends it, one lifts it |
| A suspended person | Does not exist | Every function and page answers "suspended". Export and delete still work |
| The record | None | One row per suspend or lift: who, which account, the reason, when |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | The view is a web page, `/owner/accounts`, not a function | A function changes the owner's function list and about 10 pinned test files. The page is reachable from any browser |
| 2 | Suspend and lift are forms on that page | The same reason. One place for the owner's account work |
| 3 | The page and its forms are for the owner alone: the signed-in row's `subject` equals `OWNER_USER_ID`. Anyone else, signed in or not, gets `not_found_page`, the site's one 404 | The same rule as `is_owner`. A 404 does not say the page exists |
| 4 | The page is not in `links._PATHS`, not in any result's links, not named in the README, and not in `web.PAGES`: its two routes are added in `routes()` beside the table, behind `page_limited`, and are off when sign-in is not set up, as every signed-in page is | It is not a page a person or an assistant is sent to. `PAGES` is every person's page, rendered by the page tests on plain accounts |
| 5 | The numbers come from the services: `services.owner_accounts()` and `services.set_account_status()` in `app/services/owner.py` | Pages ask the services |
| 6 | Entry counts are live rows only (`deleted_at` is null), one `GROUP BY user_id` query a table | A deleted entry is not something the person has |
| 7 | Calls are counted over the last 90 days and feedback over the last 365, and the page says so | That is how long those rows are kept (`CALL_LOG_DAYS`, `FEEDBACK_DAYS` in `app/calllog.py`). The windows are read from those constants, never typed again |
| 8 | Status is `active` or `suspended` on the user row, with `status_reason` and `status_at` | The smallest shape that answers "is it suspended, why, since when" |
| 9 | A reason is required to suspend: 1 to 200 characters after trimming, checked with `rules.text`. Lifting takes an optional reason for the record | An action with no reason cannot be explained later. 200 is a pick for a one-line note, not a number from data |
| 10 | On lift, `status` is `active`, `status_reason` is null and `status_at` is the time of the lift. The record keeps the history | The row says what is true now |
| 11 | The owner's own account cannot be suspended: the service refuses when the target's `subject` equals `OWNER_USER_ID` | The lift form sits behind the same gate |
| 12 | Suspending a suspended account, or lifting an active one, changes nothing, writes no record row, and says so | No noise in the record |
| 13 | MCP: the check sits in `Wrapper.call`, on the `User`, after the account is found and before `not_allowed`. Every function passes it, `whoami` too | One gate. The call-log row keeps its account |
| 14 | The MCP refusal is `{"error": "account_suspended", "message": ..., "fix": ...}`, logged as refused under step `account` in `calllog.STEPS` | Its own code, so the owner can find these calls. Nothing is added to the server's instructions: the fix line says there is nothing to retry |
| 15 | Web: a suspended person can still sign in. The gate covers the rows of `web.PAGES` and the placeholders, and nothing else: for a suspended person each answers with one "suspended" page, status 403, and no handler runs | The person must be able to take their data and leave. `/`, `/hi`, `/sign-in`, `/auth/callback` and `/sign-out` are outside `PAGES` and are never gated; a card address only redirects to `/settings`, which is gated |
| 16 | Two rows of `PAGES` stay open to a suspended person: `/settings/export` (POST) and `/settings/delete` (GET and POST). The suspended page carries the Export button, a link to the delete page, and sign-out | The same two things Settings promises everyone |
| 17 | The web check runs on every request, from the row `current_user` loads, in one wrapper applied where `routes()` wraps each `PAGES` row | A suspension takes hold at once, with no sign-out needed |
| 18 | The reason is the owner's note. The suspended page does not show it, the export does not carry it (`export.ACCOUNT_COLUMNS` is unchanged: no status column in the file), and `whoami` is unchanged | It is not text written for the person |
| 19 | The record is a new table `owner_action`: `id`, `by_account_id`, `action` (`suspend` or `lift`), `account_id`, `reason`, `created_at`. The two account columns are plain integers with no foreign key | The record outlives a deleted account. A column named `user_id` would make it the person's own table: exported, and wiped on delete |
| 20 | The record holds no email and no name | Nothing of a deleted person stays but an id |
| 21 | The page shows the last 20 record rows | Enough to see what was done lately. Not a number worked out from data: a page size |
| 22 | The owner's form does not go through `write()`: no call-log row, no write key, no write limit. It is CSRF-checked as every form is, and its one trace is the `owner_action` row | `write()` is for a person's own entries under a function's name. This is not a function |
| 23 | "Seen in the last 7 days" on the page is a pick, a week, named as such | Not a number from data |

## What a model reads, and the eval run

- New model-read text: the `account_suspended` message and fix line, two sentences. Only a suspended
  account ever receives them.
- No function is added or changed, the server's instructions are not touched, and `whoami`'s answer for an
  active account is the same.
- `python -m evals.need main` is expected to say yes, for the two sentences and for new text in
  `app/services/owner.py`. The run at the release is the owner's call. No eval case reaches a suspended
  account, and none is added: the refusal is covered by tests.

The two sentences, as written:

- message: `This account is suspended.`
- fix: `Nothing to retry. Tell the person their account is suspended, and that they can still export or delete their data on Spotter's Settings page.`

## Package P1: the view

### What exists

- `app/services/owner.py`: `owner_calls`, with `filters()`.
- `app/web.py`: `current_user` loads the signed-in row. No owner-only page.
- Entry rows are soft-deleted only. The five entry models are `models.entry_models()`.

### What changes

- `app/services/owner.py`: `owner_accounts()` returns one line per account, newest first by `created_at`:
  `account_id`, `email`, `name`, `time_zone`, `joined`, `last_seen`, the count of live rows for each of
  lift sessions, body weights, measurements, food entries and cardio entries, `calls` (90 days),
  `feedback` (365 days). After P2: `status`, `status_reason`, `status_at`. Also the totals: accounts, and
  accounts seen in the last 7 days.
- `app/services/__init__.py`: the export.
- `app/web.py`: `GET /owner/accounts`, by decision 3. Dates are shown in the owner's own time zone.
- `app/templates/owner_accounts.html`: one table, in the site's look. An account with no entries shows 0.
- No migration.

### Tests

- New `tests/test_owner_accounts.py`: the owner sees every account; counts leave out deleted rows; a call
  older than the window is not counted; a signed-out visitor and a signed-in non-owner both get the 404,
  with no account's email in the body; `OWNER_USER_ID` unset means nobody sees it; an account with nothing
  logged shows zeros.
- Pins that gain a row: the route table in `tests/test_hi_there.py`.
- No test depends on the real date: times are set through the seams the tests already use.

## Package P2: account status

### What exists

- `User` has no status. `Wrapper.call` checks, in order: the account, `not_allowed`, the owner, the read
  limit. `routes()` wraps each page in `page_limited`.
- The migrations' head is `0016`.

### What changes

- `migrations/versions/0017_account_status.py`, in the house style of `0015` and `0016`:
  - `user.status VARCHAR(16) NOT NULL`, server default `'active'`; `status_reason VARCHAR(200) NULL`;
    `status_at` NULL, time zone aware.
  - `ck_user_status`: `status IN ('active', 'suspended')`.
  - Table `owner_action` by decision 19, with `pk_owner_action`, `ck_owner_action_action`, and an index on
    `account_id`.
  - Downgrade: drop the table, then the rule and the three columns. The suspensions and the record are lost.
- `app/models.py`: the three columns, a `STATUSES` tuple, `OwnerAction`.
- `app/services/owner.py`: `set_account_status(by_user, data)` by decisions 9 to 12, the user row locked
  as the other writes lock it, the row change and the record row in one transaction.
- `app/mcp_server.py`: the gate, by decisions 13 and 14. `app/calllog.py`: the `STEPS` row.
- `app/web.py`: the gate, by decisions 15 to 17; `POST /owner/accounts/status` (CSRF as every form, owner
  only by decision 3, then back to the page with a one-line outcome); the suspended page.
- `app/templates/suspended.html`; the forms and the record in `owner_accounts.html`.

### Tests

- New `tests/test_account_status.py`:
  - The service: suspend, lift, the reason's limits, the owner refused, the two no-change cases, an
    unknown account id, the record rows.
  - MCP: a suspended account's call of every registered function answers `account_suspended` and writes
    nothing; `whoami` too; the call-log row is refused, step `account`, with its account id; lifting
    restores every call.
  - Web: a suspended person gets the suspended page on every page in `web.PAGES` but the four of decision
    16; every other POST writes nothing; export returns the file; delete deletes; a session opened before
    the suspension is caught on its next request.
  - The owner form: a non-owner POST is the 404 and changes nothing; a POST without the CSRF token is
    refused as every form is.
  - The record survives the suspended account's delete, and holds no email.
  - `delete_account`'s `rows` answer and the export are unchanged by the new table.
- Pins that move: any test that lists the user row's columns against the export; the head `0016` to `0017` (seven files and `HEAD` in `tests/test_hardening_schema.py`);
  `CHECKS_AT_HEAD` gains two; the older-revision chain gains `before_0017`; the POST route set in
  `tests/test_web.py`; the route table; the form sweep in `tests/test_inputs.py`; `tests/test_schema.py`'s
  column and default checks.
- A migration test in the pattern of `tests/test_programs.py`: `0017` up, down, up on SQLite.

## Work packages

One builder builds P1, runs its tests, then builds P2 on top: the two share `app/web.py`, the template and
`app/services/owner.py`.

| Package | Files |
|---|---|
| P1 | `app/services/owner.py`, `app/services/__init__.py`, `app/web.py`, `app/templates/owner_accounts.html`, `tests/test_owner_accounts.py`, `tests/test_hi_there.py` |
| P2 | `migrations/versions/0017_account_status.py`, `app/models.py`, `app/services/owner.py`, `app/services/__init__.py`, `app/mcp_server.py`, `app/calllog.py`, `app/web.py`, `app/templates/owner_accounts.html`, `app/templates/suspended.html`, `tests/test_account_status.py`, and the pinned test files named above |

A pinned test outside these lists that turns red is reported with its line, and changed only when the
change is a new row or a moved head.

## Eval cases

None added. See "What a model reads".

## The exit check

1. The touched test files green, plain and with `-W error`.
2. A reviewer on a frozen copy, with probes; one fix round.
3. `python -m evals.need main`, its answer written down.
4. One run on a local Postgres: `0017` up, down, up, over a written row and a suspended account.
5. The whole suite on a clean export of the commit.
6. The page seen in a browser from a local render, at phone width and desktop width.
7. After the deploy: the owner opens `/owner/accounts` on the live site. The page is the only surface, and
   it rests on the web sign-in giving the same subject as `OWNER_USER_ID`.

## Known limits

- A suspended person who deletes the account can sign up again. Nothing stops a return.
- `last_seen` moves on an MCP call or a web sign-in, never on a page load, so a person who only reads
  pages on an old session looks idle.
- The page lists every account with no paging. It is revisited when the list is long enough to be slow.
- The view is not reachable from a chat. An owner function can be added later on the same service.

## Notes from the build, 2026-11-27

One builder built P1, then P2 on top. Nothing is committed.

- **The status column has the database's default alone.** The model gives `user.status` its server default and no
  default of its own. With one, every insert names the column, and two older tests that insert into a database at
  0011 and 0012 fail on a column that is not there yet. An insert reads the default back (RETURNING), so a row just
  made carries it: a new account's first call passes the gate (`tests/test_account_status.py`).
- **One pinned test stays red.** In `tests/test_macros_schema.py`, this one:

  `test_0015_up_down_and_up_over_rows_of_every_kind_with_each_new_column_set`

  It writes rows through the head's models into a database at 0015. At 0017 the user row has three columns 0015
  lacks, so the insert fails, with or without a default on the model. The change it needs is not a new row or a
  moved head, so it is left for the lead. Tried once and put back: upgrade to the head, write the rows, downgrade to
  0015, the rest unchanged: the file goes green.
- **No time zone form for a suspended account.** `base.html` draws a form that posts the browser's zone to
  `/settings` for a person with none. For a suspended person `/settings` is the suspended page, so the form would take
  them off the open Delete page. `page()` leaves the form out for a suspended account only.
- **CSRF runs before the owner check.** A form with no token or a wrong one is "Form expired" (403) for anyone, as
  every form is, before decision 3's 404. `tests/test_web.py` wants 403 from every POST route with no token.
- **Decision 16 names two rows.** The test list says "the four of decision 16". Built by decision 16: `/settings/export`
  (POST) and `/settings/delete` (GET and POST) stay open. The export row was never behind the new gate: it keeps its
  own export gate, as before.
- **Pins outside the named list, each a new row or a count:** `tests/test_idempotency.py` (the owner's form action in
  `UNKEYED`, the form count from 24 to 28: Suspend, Lift, and the suspended page's Export and Sign out),
  `tests/test_inputs.py` (the owner's form action in `UNPOSTED`; its bad inputs are covered in
  `tests/test_account_status.py`), `tests/test_hardening_schema.py` (the tables holding a rule gain `owner_action`;
  the tables at 0012 leave out 0017's table too).
- **The owner's form.** Every outcome, a refusal too, goes back to the page with one mark, and the page draws that
  mark's words from a fixed table, never the address's text. A refused reason is not kept in the form. A status
  change does not move the user row's `updated_at`. The record shows dates, in the owner's zone, without a time.
- **Codes.** The service refuses the owner's own account as `owner_account`, an id no account holds as `not_found`,
  and a bad id, action or reason as `invalid_field` (`missing_field` for a suspend with no reason). Only the page
  reads them.
- **The review and the fix round.** The review found no must fix. S1 (a test that the suspension answers before
  `not_allowed`), M1 (the refused reason line, now true for a lift too) and M4 (a test that markup in an account's
  fields and its reason is escaped) were fixed in the fix round, and so was the accounts table on a laptop: it is
  `table.rows.many-cols`, whose short cells keep to one line from 720 px, so the table scrolls sideways in its own box.
- **Decision 3 and the form's token.** The token check runs before the owner check, so a non-owner POST with no valid
  token gets the 403 "Form expired" page, not the 404. Nothing is revealed or changed.
- **The 0015 test, later.** `tests/test_macros_schema.py`'s 0015 up, down, up test now writes its rows at the head and
  comes down to 0015, because the head's models name `user.status`. The review confirmed the schema reached that way
  equals a fresh 0015.
- **Left as built.** A reason may hold a line break (M2). The suspended page keeps the four tabs, each of which answers
  the same suspended page (M3).
- **A new account's status.** A new account's first MCP call reads `status` back from the insert (the database's
  default, RETURNING). Checked on SQLite by a test, and on Postgres in the exit check's run: an account made
  there read `active` straight after it was made, twice.
- **The Postgres run, 2026-11-27.** On `postgres:17-alpine`: rows written at 0016 by the code before this item, then
  0017 up (both accounts read `active`; the rule and index names as written above; a status outside the list is
  refused by the rule), a suspend through the service with its record row, the owner's own account refused, down
  to 0016 (the three columns and the table gone, every account and entry kept), and up again (every account
  `active`, the record empty). The container and its volume were removed after.
- **Seen in a browser, from a local render on a seeded test database.** The accounts page at 390 px (stacked) and
  at 1,100 px (the table scrolls sideways in its box), and the suspended page at 390 px. One fault found there and
  fixed after the fix round: a hidden label inside the wide table made the whole page scroll sideways, so
  `div.table-wrap` is now `position: relative` and holds it. No reviewer read that one line.
- **The whole suite's one red.** The first run of the whole suite failed one test, `tests/test_data.py`'s tie test:
  every table must be read by an area or named in `fields.OTHER_TABLES`. `owner_action` is now named there as
  internal. No builder or reviewer run had that file in its list.
- **Not seen.** The owner's page on the live site, and the forms posted from a browser.
