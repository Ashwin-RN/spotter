# Stage 2 contract: one person, one account

**Goal: every signed-in person gets exactly one account, sees only their own data, and reaches the same account from Claude and from the web.**

Spotter started from a trainer's notebook. This stage moves in the parts that fit one person (lifts, bodyweight, goals, the numbers worked out from them) and leaves the trainer-shaped parts out.

## What exists at the end

- A database holding the whole v1 schema, created by one migration.
- A `user` row per person, created on the first valid sign-in from either side.
- Six MCP functions and two resources.
- Five web pages with sign-in through the same provider.
- A call log that never holds content.
- A `.claude/` folder: builder and reviewer agents, a stage-check command, a hook that runs the tests before a commit.

## Decisions for this stage

| # | Decision | Why |
|---|---|---|
| 1 | Services live in a package, `app/services/`, one module per area | Later stages each own a module, so their work does not collide |
| 2 | Read, delete and restore by id exist at service level now, with no MCP function yet | The cross-user test needs them |
| 3 | A user is keyed on (`provider`, `subject`). `provider` comes from the settings, never from a token | Keying on the issuer could split one person into two accounts |
| 4 | Bodyweight: the same weight again within 10 minutes is a duplicate. A different weight on the same day replaces the old one, which is soft-deleted and can be restored | One live weigh-in per day keeps the trend honest |
| 5 | Web sign-in uses the provider's Python library (`workos`). The session is Spotter's own signed cookie, holding four things: the user id, a CSRF token, the provider's session id (an id, not a token; sign-out needs it) and the sign-in time. A session ends 14 days after sign-in, however often it is used | No provider token ever sits in a cookie, and a copied cookie cannot be kept alive for ever |
| 6 | Until a time zone is set, "today" is UTC, and `whoami` tells Claude to ask. `/settings` can set it | Logging must work from the first message |
| 7 | The permission switch defaults to `log` | Logging works at once; delete stays off until the person turns it on |
| 8 | CSRF is hand-written: a random token per session, checked on every non-GET web route | About 30 lines, no new package |
| 9 | All tables for stages 2 to 5 go in the first migration | Later stages add functions and rows, never schema. Anything unforeseen gets a new migration; the first is never edited once deployed |
| 10 | A failed MCP call is flagged as an error (`isError`) and carries `{error, message, fix}` | The MCP spec asks for it, and Claude reads the fix and retries. A duplicate is a success, not an error |
| 11 | Each MCP function takes named, typed arguments, not one blob. The service stays the judge of ranges and rules | The caller sees exactly what to send. A value of the wrong type is refused by the MCP library, naming the field |
| 12 | No link Spotter hands out may 404. A page that is named in `app/links.py` and not built yet gets a signed-in placeholder, and a test walks every named page | Results already link to the day, progress and goals pages, which arrive in stage 3 |
| 13 | The provider's userinfo is asked only while a user row lacks an email or a name, at most once per 10 minutes per person | The provider's tokens name nobody, and a network call on every function would be waste |

## Settings

| Variable | Secret? | Meaning |
|---|---|---|
| `AUTHKIT_DOMAIN`, `MCP_RESOURCE_URL`, `AUTH_DEBUG` | No | As in stage 1 |
| `DATABASE_URL` | Yes | Postgres on the host. Unset on a local machine: a local SQLite file. Unset on a public https site: the database refuses to start |
| `WORKOS_CLIENT_ID` | No | The provider's public id for this app |
| `WORKOS_API_KEY` | Yes | Lets the server finish a web sign-in |
| `SESSION_SECRET` | Yes | Optional. Signs the session cookie. At least 32 characters. When unset, the key is derived from `WORKOS_API_KEY` with HKDF and a fixed label |
| `AUTH_PROVIDER` | No | Default `workos`. Stored on each user row |
| `SIGNUPS_PER_DAY` | No | Default 50. New accounts allowed per UTC day |

- Only `app/config.py` reads the environment.
- Web sign-in fails closed: without `WORKOS_API_KEY` and `WORKOS_CLIENT_ID`, the web pages say sign-in is not set up, and MCP still works.
- The site's own address is the origin of `MCP_RESOURCE_URL`. It never comes from a request header.

## The schema

**Conventions.** Every table has an integer `id`. Times are UTC. Dates are the person's local date.

Every entry table carries these five columns:

| Column | Type | Why |
|---|---|---|
| `logged_by` | `web` or `claude` | Who wrote it |
| `created_at` | time, required | The duplicate guard's window; ordering |
| `updated_at` | time, required | Edits |
| `deleted_at` | time, null | Soft delete. Restore clears it |
| `is_sample` | boolean, default false | "Remove sample data" deletes only these rows |

Every entry table is indexed on (`user_id`, `date`) and (`user_id`, `created_at`). Every query filters `deleted_at IS NULL`, except restore.

| Table | Columns beyond `id` | Keys and indexes | First used |
|---|---|---|---|
| `user` | `provider`, `subject`, `issuer` (last seen), `email` null, `name` null, `time_zone` null (IANA name), `calorie_target_kcal` null, `target_sessions_per_week` null, `target_cardio_minutes_per_week` null, `claude_can` (`read`, `log`, `log_delete`; default `log`), `add_back_exercise_kcal` default false, `created_at`, `updated_at`, `last_seen_at` | UNIQUE(`provider`, `subject`); index `created_at` | 2 |
| `exercise` (shared list) | `name`, `kind` (`lift`, `cardio`), `category`, `met` null, `compendium_code` null | UNIQUE(`name`) | 2 (cardio rows in 4) |
| `lift_session` | `user_id`, `date`, `notes`, entry columns, `dedupe_key` | entry indexes; (`user_id`, `dedupe_key`, `created_at`) | 2 |
| `lift_set` | `session_id`, `exercise_id`, `set_number`, `weight_kg`, `reps` | index `session_id`; index `exercise_id` | 2 |
| `bodyweight` | `user_id`, `date`, `kg`, entry columns, `dedupe_key` | entry indexes; one live row per (`user_id`, `date`) | 2 |
| `goal` | `user_id`, `kind` (`bodyweight`, `lift`, `cardio_best`, `cardio_weekly`, `calories`, `sessions`), `exercise_id` null, `distance_km` null, `unit`, `start_value`, `start_date`, `direction` (`up`, `down`), `target_value` null, `target_date` null, `status` (`open`, `hit`, `dropped`), `text`, `created_at`, `updated_at`, `closed_at` null, `is_sample` | (`user_id`, `status`) | 2 (lift, bodyweight); 5 (the rest) |
| `call_log` | `user_id` null, `function`, `started_at`, `ms`, `ok`, `error_code` null, `result_bytes` | (`started_at`); (`user_id`, `started_at`); (`function`, `started_at`) | 2 |
| `measurement` | `user_id`, `date`, `kind` (`waist`, `hips`, `chest`, `arm`, `thigh`, `steps`, `sleep_hours`, `resting_hr`, `water_ml`), `value`, `unit`, entry columns, `dedupe_key` | entry indexes; (`user_id`, `kind`, `date`) | 3 |
| `saved_food` | `user_id`, `name`, `name_key` (casefolded), `serving_label`, `kcal_per_serving`, `grams_per_serving` null, `created_at`, `updated_at`, `deleted_at`, `is_sample` | UNIQUE(`user_id`, `name_key`) | 4 |
| `food_entry` | `user_id`, `date`, `meal` (`breakfast`, `lunch`, `dinner`, `snack`), `description`, `kcal`, `source` (`you_said`, `saved_food`, `usda`, `claude_estimate`), `estimated`, `saved_food_id` null, `servings` null, `usda_fdc_id` null, `grams` null, entry columns, `dedupe_key` | entry indexes; (`user_id`, `saved_food_id`) | 4 |
| `cardio_entry` | `user_id`, `date`, `exercise_id`, `minutes`, `distance_km` null, `incline_pct` null, `speed_kmh` null, `avg_hr` null, `machine_kcal` null, `estimated_kcal` null, `estimate_weight_kg` null, `notes`, entry columns, `dedupe_key` | entry indexes; (`user_id`, `exercise_id`, `date`) | 4 |
| `usda_food` (shared cache) | `fdc_id` (primary key), `description`, `data_type`, `kcal_per_100g`, `fetched_at` | primary key | 4 |
| `usda_search` (shared cache) | `query_key` (primary key), `fdc_ids`, `fetched_at` | primary key. Holds no user id | 4 |

- `kcal` on a food entry is copied in at log time. Editing a saved food never rewrites history.
- The call log holds a function name, a duration, ok or an error code, and a result size. **Never an argument or a result.**
- Tests build their database with the migration, not from the models. One test asserts the migration and the models agree.
- The exercise list is synced by name at start-up. Running it twice changes nothing.

## Rules built into every function

1. **Own data only.** Every query is scoped to the signed-in user. One helper does every by-id lookup. An id that belongs to someone else, or to a deleted row, gives the same "not found" as an id that does not exist.
2. **Errors carry a fix.** `{error, message, fix}`. Claude reads the fix and retries.
3. **Duplicate guard.** Same user, same date, same content, created within 10 minutes, not deleted: the first entry comes back with `duplicate: true` and nothing is written. `allow_repeat: true` logs it anyway. It applies to the web and to Claude alike.
4. **What "the same entry" means.** A lift session: its date, notes and the full set list. A weigh-in: its date and weight. The guard compares a `dedupe_key`, a SHA-256 of those values.
5. **Two retries at once** run one after the other: every write takes a lock on the user row first.
6. **Soft delete.** Delete sets `deleted_at`. Restore clears it.
7. **Today** comes from one function that knows the user's time zone. Nothing else reads a clock for a date.
8. **Links** come from one helper. Every link points at a page behind sign-in. No link carries a secret.

## Identity

```
verified token (stage 1)                web sign-in callback
  subject, issuer, email, name            subject, email, name
            \                              /
             users.user_for_identity(...)        <- the one lookup
                        |
                    user row
                        |
        every service call takes user_id first
```

- Found: update the email and name if they changed.
- Not found: check the daily signup cap, then insert. Cap reached: error `signups_closed`, with a fix.
- Two first sign-ins at once give one row.
- A token's `sub` and the web sign-in's user id are the same value for the same person. That is what makes it one account.

## The MCP surface

Every function has a title, a permission hint and a result schema.

| Function | Kind | What it does |
|---|---|---|
| `whoami` | Read | The account: `user_id` (the token's `sub`), `account_id`, email, name, time zone or a note to ask for it, targets, what Claude may do, and a `sign_in` block (client, scopes, issuer, expiry) from the token |
| `list_exercises` | Read | The lift list, filtered by kind or category |
| `log_lifts` | Write | One session: date, notes, sets. Returns the saved session, any new bests, and `duplicate` |
| `log_bodyweight` | Write | One weigh-in |
| `set_goal` | Write | A lift or bodyweight goal. Stores the start value, start date and direction when created |
| `get_progress` | Read | One lift over time: bests, estimated max, projection |

- Resources: `spotter://can-and-cant` and `spotter://exercises`.
- One wrapper runs every function: it resolves the user, times the call, writes the call-log row, and turns a crash into `{error, message, fix}`.
- A result is the service's own dict, as structured content and as JSON text. An error result is flagged `isError`.
- The services are synchronous. Every database call runs in a worker thread, never on the event loop.
- A crash is logged by type and stack frames only, never by its message: a database error's message can carry a person's data.
- The permission switch is stored in this stage and enforced from stage 3.

## The web

| Page | Signed out | Signed in |
|---|---|---|
| `/` | What Spotter is, the one MCP address, how to connect | The same, plus a link to Settings |
| `/sign-in` | Sends the person to the provider | Redirects to `/settings` |
| `/auth/callback` | Finishes sign-in, checks `state`, creates or finds the user | |
| `/sign-out` (POST) | | Ends the session |
| `/settings` | Redirects to `/sign-in` | Name, email, sign-in id, account id; a form to set the time zone |

- Every form carries a CSRF token. A POST without it gets 403.
- The cookie is signed, `HttpOnly`, `SameSite=Lax`, and `Secure` on the host.

## Work packages

**Four packages with separate files. A goes first. B and D run side by side. C goes last.**

| | A: schema and identity | B: services | D: web and repo setup | C: MCP surface |
|---|---|---|---|---|
| **Files** | `app/config.py`, `app/db.py`, `app/models.py`, `app/exercises.py`, `app/users.py`, `app/links.py`, `alembic.ini`, `migrations/`, `requirements*.txt`, `tests/conftest.py`, `tests/test_schema.py`, `tests/test_users.py`, `tests/test_links.py`, `tests/test_config.py` | `app/services/`, `app/metrics.py`, `app/rules.py`, `app/charts.py`, `tests/test_metrics.py`, `tests/test_rules.py`, `tests/test_lifts.py`, `tests/test_bodyweight.py`, `tests/test_goals.py`, `tests/test_progress.py`, `tests/test_charts.py`, `tests/test_entries.py`, `tests/test_isolation.py` | `app/web.py`, `app/web_auth.py`, `app/csrf.py`, `app/main.py`, `app/templates/`, `app/static/`, `tests/test_web.py`, `tests/test_csrf.py`, `.claude/` | `app/mcp_server.py`, `app/mcp_schemas.py`, `app/calllog.py`, `docs/can-and-cant.md`, `tools/mcp_call.py`, `tests/test_mcp.py`, `tests/test_isolation_mcp.py`, `tests/test_calllog.py`, `tests/test_same_account.py` |
| **Waits for** | Stage 1 | A | A | B and D |
| **Exit check** | The migration builds every table on an empty database; migration and models agree; the same identity twice gives one row; the exercise sync is idempotent; stage 1's tests still pass | Its tests pass; the isolation test passes; no trainer-shaped name is left in its files | Signed-out pages redirect; a POST without the CSRF token is 403; sign-in creates then finds the user; sign-out clears the session | The function list is exactly the six, each with title, hint and schema; a marker string passed as an argument never appears in the call log; isolation and same-account tests pass; **the full suite passes** |

`app/auth.py` is not edited in this stage.

### Service signatures, fixed before B and C start

```
whoami(user_id)
list_exercises(kind=None, category=None)
log_lifts(user_id, data, by)
log_bodyweight(user_id, data, by)
set_goal(user_id, data, by)
get_progress(user_id, exercise=None)
get_entry(user_id, kind, entry_id)
delete_entry(user_id, kind, entry_id)
restore_entry(user_id, kind, entry_id)
```

`by` is `web` or `claude`. No function takes a base address.

## The cross-user test

**User A gets "not found" for user B's id, and B's row is unchanged.**

- Through the services: B logs one of every entry kind. For each kind and each of read, edit, delete and restore, A calls with B's id. The result is `not_found`, with the same text as for an id that does not exist, and B's row is unchanged.
- Through MCP: two tokens, two people. B logs. A calls every function that takes an id with B's id, and sees none of B's data in `whoami` or `get_progress`.
- A guard test lists every table with a `user_id`. A new entry table with no row in the isolation test fails it.

## Exit test

1. The full suite is green.
2. The cross-user test passes, through the services and through MCP.
3. Live: sign in on the web, then call `whoami` from Claude. The sign-in id on `/settings` equals `whoami.user_id`, and the account id is the same.
4. Live: `log_lifts` from Claude, then the same message again, reports a duplicate.
