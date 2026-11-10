# Stage 3 contract: lifts, weight and measurements you can see

**Goal: everything a person logs by talking shows on a web page the same minute, can be fixed or removed from either side, and Claude does only what the person has allowed.**

## The first-use path, walked first

A new person talks and signs in. Nothing else is done by hand.

| Step | From Claude | On the web |
|---|---|---|
| Arrive | `whoami`; Claude sets the time zone (stage 2) | Sign in lands on **Today**. The browser's zone is saved silently; the person stays on Today |
| Log | "Back squat 3×5 at 60" → `log_lifts`; the result links to the day page, which now exists | Today shows the session, its sets and any bests |
| Weigh | "78.4 this morning" → `log_bodyweight` | A one-line weigh-in form on the day page |
| Measure | "Waist 84" → `log_measurement` | A one-line measurement form |
| Fix | "It was 65, not 60" → `edit_entry` | Edit on every entry; sets are edited through Claude only in this stage |
| Remove | "Delete that" → `delete_entry`, refused while the switch is at `log`. The fix says the person turns deleting on in Settings: that is their consent, and the switch lives on the web only | Delete on every entry, with Undo |
| Look back | "What did I do this week" → `get_log`, 1 to 14 days | Prev and next day links; Progress pages with charts |

## What exists at the end

- Twelve MCP functions and two resources.
- Web pages: Today, a day, Progress, one lift's progress, Settings with the permission switch. Goals and Foods stay placeholders.
- The permission switch enforced for Claude. A write rate limit for everyone.
- One new migration, `0002`: one live measurement per kind per day.

## Decisions for this stage

| # | Decision | Why |
|---|---|---|
| 1 | Web sign-in lands on Today. The automatic zone save returns the person to the page they were on | Settings is not where a person starts |
| 2 | An edit changes the entry in place: a weigh-in's kg or date, a measurement's value or date, a session's date, notes or whole set list. `updated_at` moves; old values are not kept. The result shows before and after | A delete plus a new entry would change the id Claude holds and re-announce bests |
| 3 | The switch, `claude_can`: `read` lets Claude read only; `log` (default) adds every write but `delete_entry`; `log_delete` adds that. `restore_entry` is a write, allowed at `log`. The switch is set on the Settings page only; `update_settings` refuses it | The plan's switch, enforced. Restoring undoes a mistake; it never destroys |
| 4 | Write rate limit: 120 write calls per account in any 10 minutes, web and Claude together, counted from the call log. Web writes get call-log rows too. A refused write is `too_many_writes` and counts as well | Generous for a person logging history in a batch, tight for a runaway loop |
| 5 | Measurements: nine kinds, unit fixed per kind. One live value per kind per day; a different value on the same day replaces the old one, which is soft-deleted and can be restored. The same value within 10 minutes is a duplicate | The weigh-in rules, nothing new to learn |
| 6 | `get_log` covers one day by default and up to 14, ending on the date given or today. With no date given, an entry dated after today (after a zone change) still appears, under its own date | The plan's read function; a read never hides an entry for its date |
| 7 | Web logging: weigh-in and measurement forms on the day page; lifts are logged through Claude only in this stage | A set-list form is a stage of its own |
| 8 | History import stays one session per `log_lifts` call | The rate limit allows it, and the function stays simple |
| 9 | The permission check runs in the MCP wrapper, before the service. The rate limit runs in the services, after the user lock | The switch is about Claude; the limit is about the account |
| 10 | `delete_entry` carries the destructive hint, so the client asks the person first. Every other write keeps the hints from stage 2 | The plan says delete always prompts |
| 11 | No page needs a script beyond `time-zone.js`. Every form is plain HTML with a CSRF token | The content security policy stays as it is |

## Rules built into every function (added to stage 2's)

9. **A write announces itself before it runs.** Every call, web or Claude, writes its call-log row first (`in_progress`) and completes it afterwards (time taken, ok or the error code, result size). The web uses the same names as MCP (`log_bodyweight`, `edit_entry`, …). So a call's row exists before its own service runs, and a second writer queued behind the user lock sees it: the limit cannot be overshot by calls arriving together.
10. **The limit.** After the lock, a write counts this person's write rows started in the last 10 minutes, its own among them. More than 120: `too_many_writes`, with the minutes to wait. Nothing is saved. A refused write counts too, so a loop of refusals stays throttled. The person's own switch (`set_claude_can`, web only) leaves a row but is not limited: it is never Claude's call.
11. **One live row per day, per kind.** Bodyweight (per date) and measurement (per kind and date) have a partial unique index on live rows. Logging a new value replaces; restoring swaps back. Editing a date onto a taken day is refused (`day_taken`), never silently replaced.
12. **Edits re-key.** An edit recomputes `dedupe_key`, so a repeat of the new content is a duplicate.

## Service signatures, fixed before any package starts

```
log_measurement(user_id, data, by)              data: {kind, value, date?, allow_repeat?}
edit_entry(user_id, kind, entry_id, changes, by) changes: lift_session {date?, notes?, sets?}
                                                          bodyweight   {date?, kg?}
                                                          measurement  {date?, value?}
get_log(user_id, date=None, days=1)             days 1..14; date default today
set_claude_can(user_id, value)                  web only: read | log | log_delete
delete_entry(user_id, kind, entry_id)           as stage 2, now rate-limited
restore_entry(user_id, kind, entry_id)          as stage 2, now rate-limited
```

Existing write functions (`log_lifts`, `log_bodyweight`, `set_goal`, `update_settings`) gain the limit and nothing else.

### Result shapes

```
log_measurement -> {kind: "measurement", id, duplicate, measurement: {id, date, kind, value, unit, ...},
                    replaced: {…} | null, recent: [{date, value}] (this kind, newest first, up to 10),
                    summary, links: {day}[, note]}
edit_entry      -> {kind, id, changed: [field, ...], before: {entry}, after: {entry}, summary, links: {day}[, note]}
                   note for a session: "Bests are worked out from history: get_progress shows them."
get_log         -> {from, to, days: [{date, sessions: [{…session, new_bests: [...]}], weigh_in: {…} | null,
                    measurements: [{…}]}], (newest day first; every day in the range, empty ones included)
                    totals: {sessions, weigh_ins, measurements}, summary, links: {day: <to>, progress}}
set_claude_can  -> {claude_can, changed: bool} or {error, message, fix}
```

Errors added: `too_many_writes` (with `wait_minutes`), `day_taken` (with `entry_id` of the live row), `invalid_kind` (a measurement kind), `nothing_to_change`, `invalid_field` (an edit field the kind has no business with), `not_allowed` (the switch, from the MCP wrapper).

### Measurement kinds

| Kind | Unit | Range | Whole number? |
|---|---|---|---|
| `waist`, `hips`, `chest` | cm | 30 to 300 | no |
| `arm` | cm | 10 to 100 | no |
| `thigh` | cm | 20 to 150 | no |
| `steps` | steps | 0 to 200,000 | yes |
| `sleep_hours` | h | 0 to 24 | no |
| `resting_hr` | bpm | 20 to 250 | yes |
| `water_ml` | ml | 0 to 20,000 | yes |

Values keep two decimals at most. The kind is matched like an exercise name: case and spaces forgiven, meaning not (`Waist`, `resting hr` fine; `wrist` is `invalid_kind` with the list).

## The MCP surface

| Function | Kind | Needs | What it does |
|---|---|---|---|
| `whoami`, `list_exercises`, `get_progress` | Read | `read` | As stage 2 |
| `get_log` | Read | `read` | `date?`, `days?` (1 to 14). The days' sessions with sets and bests, weigh-in, measurements |
| `log_lifts`, `log_bodyweight`, `set_goal`, `update_settings` | Write | `log` | As stage 2 |
| `log_measurement` | Write | `log` | `kind`, `value`, `date?`, `allow_repeat?` |
| `edit_entry` | Write | `log` | `kind`, `entry_id`, then the fields: `date?`, `notes?`, `sets?`, `kg?`, `value?`. The wrapper passes only the fields sent |
| `restore_entry` | Write | `log` | `kind`, `entry_id` |
| `delete_entry` | Write, destructive hint | `log_delete` | `kind`, `entry_id` |

- `NEEDS` in `app/mcp_server.py` maps every function to `read`, `log` or `log_delete`. A test asserts every registered function is in it, and that the three levels gate every function as the table says, through MCP, for all twelve.
- `not_allowed`: "This account lets Claude read only" or "…read and log, not delete", with the fix "The person can change what Claude may do on the Settings page" and `links.settings`. Nothing else in Spotter sends a person to a page.
- `can-and-cant` gains: the twelve functions, what the switch means, the limit, edits, measurements. The "Deletes" line changes: Claude can delete when the person allows it, and nothing is ever erased.

## The web

| Page | Signed out | Signed in |
|---|---|---|
| `/today` | Redirect to sign-in | Redirect to `/day/<today>` |
| `/day/{date}` | Redirect | The day from `services.get_log(user.id, date, 1)`: sessions with sets and bests, weigh-in, measurements. Prev and next day. A weigh-in form and a measurement form. Edit and Delete on every entry; after a delete, "Deleted. Undo" (a POST form that restores) |
| `/entries/{kind}/{id}/edit` | Redirect | A form for the fields the kind allows on the web: weigh-in kg and date; measurement value and date; session date and notes |
| `/entries/{kind}/{id}/delete`, `/restore` | | POST only, CSRF, redirect back to the day |
| `/progress` | Redirect | One line per lift (current estimated max, best set, last trained, link); the body weight trend with its chart |
| `/progress/{lift}` | Redirect | The lift's chart (`charts.chart_svg`), bests, sessions. An unknown slug is a 404 page |
| `/settings` | Redirect | As stage 2, plus the switch: three radio buttons with one line each on what they allow |
| `/goals`, `/foods` | | Placeholders, as stage 2 |

- Sign-in finishes on `/today`. The hidden zone form carries `next`, the current path; `/settings` returns there after saving when `next` is a local path (starts with one `/`), else to Settings.
- Every web write calls the service, then `calllog.write(<function name>, …)` with the same name MCP uses.
- A write refused by the limit shows the message with status 429. A page the switch would block never arises: the switch is about Claude.
- Dates and weights are formatted in one place each (`rules.kg`, ISO dates).

## Work packages

**Three packages with separate files. A and B start together; C starts when A reports. Nobody edits `tests/conftest.py`: a fixture a package needs goes in its own test file.**

| | A: services and schema | B: web | C: MCP surface |
|---|---|---|---|
| **Files** | `app/services/common.py`, `app/services/entries.py`, `app/services/measurements.py` (new), `app/services/log.py` (new), `app/services/account.py`, `app/services/__init__.py`, `app/services/lifts.py`, `app/services/body.py`, `app/services/goals.py`, `app/rules.py`, `app/models.py`, `migrations/versions/0002_*.py` (new), `tests/test_entries.py`, `tests/test_measurements.py` (new), `tests/test_log.py` (new), `tests/test_write_limit.py` (new), `tests/test_isolation.py`, `tests/test_schema.py`, `tests/test_rules.py`, `tests/test_lifts.py`, `tests/test_bodyweight.py`, `tests/test_goals.py`, `tests/test_update_settings.py` | `app/web.py`, `app/web_auth.py`, `app/templates/`, `app/static/`, `tests/test_web.py`, `tests/test_csrf.py` | `app/mcp_server.py`, `app/mcp_schemas.py`, `app/calllog.py`, `docs/can-and-cant.md`, `tests/test_mcp.py`, `tests/test_isolation_mcp.py`, `tests/test_calllog.py`, `tests/test_same_account.py` |
| **Waits for** | Nothing | Nothing (its pages call A's functions by the signatures above; its tests run once A's code is in the tree) | A |
| **Exit check** | Migration 0002 applies on an empty database and on one holding rows; migration and models agree; every write refuses the 121st call in 10 minutes and accepts after; edit, measurement and get_log tests pass; the isolation test covers measurement and edit; no write happens without a call-log row in the tests that check it | Signed-in pages render for a person with no entries and with one of each; every POST without CSRF is 403; sign-in lands on Today; the zone save returns to `next`; the switch changes `claude_can` and nothing else; a web write leaves a call-log row; no link 404s (the stage 2 guard test still passes) | Exactly twelve functions with title, hint and schema; the permission matrix (12 × 3) through MCP; `delete_entry` carries the destructive hint; a limit refusal reaches Claude as `too_many_writes`; isolation through MCP for edit and measurement; `can-and-cant` names the twelve and no text sends a person to a page except `not_allowed` |

## The cross-user test, extended

User A gets `not_found` for user B's measurement and for an edit of any of B's entries, and B's rows are unchanged. The guard test that lists every table with a `user_id` must now see measurement rows.

## Exit test

1. The full suite is green, plain and with warnings as errors, on a clean export.
2. Live, in one sitting, with the Today page open in the browser:
   - "Log back squat 3×5 at 60" from Claude → one reload shows it on Today.
   - "It was 65" → Today shows 65.
   - "Delete it" → refused; Claude says the switch is at log.
   - Switch to "log and delete" on Settings → "Delete it" → gone from Today → "Restore it" → back.
   - "Waist 84" → shown; the same again → duplicate.
3. Live: the same `log_lifts` message twice → duplicate (stage 2's test, re-run).
