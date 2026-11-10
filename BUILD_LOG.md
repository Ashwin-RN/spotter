# Build log

**How Spotter is built.** One control session runs the build. For each stage it writes a contract (`docs/contracts/`), hands a builder agent a fixed list of files, runs the full suite itself, sends a separate reviewer agent over the diff, routes the findings back to the builder, then pushes and checks the live server. This file records what each stage did and what it found out.

## Stage 1: the sign-in test

**Date:** 2026-11-09. **Contract:** `docs/contracts/stage-1.md`.

**Result: passed. Claude on the web and Claude Code both sign in, and `whoami` names the signed-in person.**

### What was built

- One Starlette app: `/health`, and the MCP endpoint at `/mcp` with one function, `whoami`.
- A token verifier: RS256 only, the provider's key set, exact issuer, exact audience, strict expiry.
- A probe, `tools/probe.py`, that checks a running server from outside without a token.
- No database, no web pages, no secrets.

### What the stage found out

| Question | Answer | How we know |
|---|---|---|
| Does the provider put this server's address in `aud`? | Yes, exactly, once the address is registered with the provider as a resource | Server log of a verified call |
| Which claims name the person? | None. The token carries `aud`, `auth_time`, `client_id`, `exp`, `iat`, `iss`, `jti`, `scope`, `sid` and `sub` | Server log (claim names only) |
| Where do email and name come from, then? | The provider's userinfo endpoint, called with the same token | `whoami` live, after the fix below |
| What is in `scope`? | `email offline_access openid profile` | Server log |
| Does Claude on the web register through its Client ID Metadata Document? | Yes. `client_id` is `https://claude.ai/oauth/mcp-oauth-client-metadata`. Claude's connector form detected both the sign-in and this method with no manual setup | Server log; the connector form |
| Does Claude Code do the same? | Yes, with its own identity: `https://claude.ai/oauth/claude-code-client-metadata` | Server log |
| Is `user_id` the same from the web and from Claude Code? | Yes | `whoami` returned the same id from both |
| Does userinfo work without scopes? | No. From Claude Code, whose token had no scopes, `whoami` returned null for email and name. From the web, with scopes, it returned both | `whoami` live from both clients |
| Do both clients ask for the same scopes? | No. Claude on the web asked for four. Claude Code asked for none, and its token has no `scope` claim at all | Server log |

### Exit test

| Check | Status |
|---|---|
| Suite green | Done: 101 tests |
| `tools/probe.py` against the deployed server | Done: every check passes |
| Claude on the web connects, signs in, and `whoami` names the person | Done |
| Claude Code does the same, with the same `user_id` | Done |
| A token meant for another server is refused | Proven in the suite. No live token with a wrong audience was available to try |

### The review

A separate agent read the first version (72 tests). Verdict: no blockers, ship after fixes. It confirmed its findings by running probes, not only by reading.

| Finding | Fix |
|---|---|
| The userinfo call followed redirects, and the redirect carried the `Authorization` header to the new host | Every outbound fetch now refuses redirects |
| Userinfo was read from the wrong discovery document. The provider lists it only in its OpenID document, so email and name would always be null | Read the OpenID document first |
| The 30-second clock allowance also applied to expiry. The endpoint refused an expired token only because the MCP library checked again | The verifier now refuses `exp <= now` itself |
| A provider host written in mixed case passed the settings check, then every real token failed the issuer check | The host is lowercased and checked as a host name |
| Only five packages were pinned | Every runtime package is pinned |
| A slow key-set refresh made every request wait, even for a cached key | A cached key is returned without the lock |
| The userinfo lookup had no overall time limit | One limit for the whole lookup, and a size cap on responses |

Smaller fixes: an empty `sub` is refused; a userinfo answer is used only when its `sub` matches the token's; a failed key fetch is retried after 5 seconds, not 60; logged claim values are cut at 200 characters.

After the fixes: 101 tests. Each fix was checked by reverting it and watching its test fail.

The reviewer then verified the fixes against the committed code, undoing each one in a copy to confirm a test turns red. Verdict: fixes verified. Four small points stay open and are scheduled with the scope follow-up: abandoned lookup threads are not counted against a limit; a failed discovery fetch can hide email and name for an hour; after the hourly key expiry a hung provider slows requests again; an `exp` sent as a string raises instead of refusing.

### What this stage taught

- **A test fake can agree with the code and disagree with the world.** The first fake provider put the userinfo address in the document the code read. The tests passed. The real provider puts it somewhere else. The fake now mirrors the real provider's two documents.
- **A second check can hide a missing first one.** The expiry test passed because the library refused the token, not because the verifier did. The verifier now has its own test.
- **Python's `urllib` forwards `Authorization` across a redirect to another host.** Anything that sends a token must refuse redirects.
- **The provider's custom domain is not needed.** Its default address works as the issuer.
- **A client asks for the scopes the server advertises, and this server advertised none.** Claude Code therefore got a token with no scopes, which means no refresh. The follow-up is to advertise `openid profile email offline_access` in the metadata.

## Stage 2: one person, one account

**Date:** 2026-11-09. **Contract:** `docs/contracts/stage-2.md`. **Status: in progress.**

The stage is split into four packages with separate files: A (schema and identity), B (services), D (web), C (the MCP surface). A went first, B and D run side by side, C goes last.

### Package A: schema and identity

- One migration creates all 13 tables for stages 2 to 5. It ran on the live Postgres on 2026-11-09.
- A user is keyed on (provider, subject). The same person from Claude and from the web lands on one row.
- Tests build their database with the migration, and one test checks the migration and the models agree.

**The review.** Verdict: run the migration, no blockers. The reviewer rendered the migration for Postgres offline and read the SQL, then undid 26 behaviours one at a time to see which tests noticed. Four did not, and those now have tests.

| Finding | Fix |
|---|---|
| With no database address set, the host would quietly use a throwaway SQLite file, and every redeploy would wipe it | A public https site now refuses to start its database without `DATABASE_URL`; the deploy step fails with one clear line |
| Without time-zone data on the host, every time zone would be refused and "today" would stay UTC | `tzdata` is installed everywhere, not only on Windows |
| A time with no zone was read as the machine's local time | Refused |

### Package D: web sign-in

- Sign-in through the provider's own library. The session is Spotter's signed cookie; no provider token is kept.
- Five pages, a CSRF token on every form, and security headers on every page.
- The cookie is attached to the web routes only. The MCP endpoint never reads or sets it.
- A `.claude/` folder: builder and reviewer agents, a stage-check command, and a hook that runs the suite before a commit.

**The review.** Verdict: sound, no blockers. The reviewer ran the real provider library against a fake provider server, read real access-log lines, and undid 28 behaviours one at a time; 24 turned a test red.

| Finding | Fix |
|---|---|
| Any bug in a page showed the "cannot reach the database" message, with only the error's type in the log | Only a real database outage shows that page. A bug is a 500 with a traceback |
| A logged database error could carry bound values, such as an email | The engine hides parameters in every error |
| The commit hook missed `git -C <dir> commit`, `git --no-pager commit` and two more forms, and fired on `git commit-tree` | The hook now splits the command into shell words and looks for git's `commit` subcommand |
| A copied cookie stayed valid after sign-out, and its 14 days slid forward on every visit | A session ends 14 days after sign-in, whatever the cookie says |
| A one-character `SESSION_SECRET` was accepted | Under 32 characters, web sign-in stays off |
| Four tests would not notice their behaviour being removed | Each now starts from a state where the behaviour matters |

Left out on purpose: PKCE (the exchange authenticates with the server's secret, and `state` is checked) and `form-action` in the content security policy (it can block the sign-out redirect; it goes in after a live sign-out confirms the redirect chain).

**A decision made on the way: no link may 404.** The services already return links to the day, progress and goals pages, which arrive in stage 3. Each page named in `app/links.py` that is not built yet gets a signed-in placeholder, and a test walks every named page. A new link name with no route fails the suite.

### Package B: the services

- Nine functions the web and the MCP surface both call: log lifts, log a weigh-in, set a goal, read progress, read the account, list exercises, and read, delete and restore an entry by id.
- One helper does every by-id lookup. Someone else's id, a deleted row and a missing id give the same answer, word for word.
- Every write locks the person's own row first, so two retries at once run one after the other.
- The numbers (estimated max, bests, trend, goal projection, the 7-day bodyweight average) come from the older app and were checked against it.

**The review.** Verdict: commit after fixes. A second reviewer, separate from the web package's, read every query for its owner filter, fuzzed the nine functions with 1,825 junk calls, recomputed the numbers by hand, and undid 60 behaviours one at a time; 44 turned a test red.

| Finding | Fix |
|---|---|
| A NUL character in notes crashed on Postgres and passed on SQLite, so no test could see it | Free text keeps newlines and tabs; any other control character is refused |
| Four more kinds of bad input raised: a list where a name belongs, a 400-digit number, an id like `²`, a datetime where a date belongs | Each returns `{error, message, fix}`. The fuzz now runs 1,825 calls with none raised |
| An error repeated the caller's input word for word: 100,000 characters in, 600 kB out | A quoted value is cut to 80 characters; notes are capped at 2,000 |
| A bodyweight goal started from one weigh-in, so a person already at target read as "behind" | It starts from the 7-day average |
| A lift goal started from the latest session, so one light day lowered the start | "Current" now means the best session in the last 4 weeks, in the goal and in progress alike |
| One error's fix asked for a target the rules refuse | The target range is derived from the weight and rep limits, so the two cannot disagree |
| Two isolation checks and three rules had no test that would notice them gone | Each has one now |

Two mutants stay alive on purpose: the duplicate guard's date filter (the date is already inside the key it compares) and its ordering.

**The fixes were reviewed too.** The same reviewer re-ran its probes on the fixed commit: all 23 fixes held, and the fuzz went to 3,185 calls with none raised. It also found two problems in the logic the fixes had added. A goal could show a current value past its target and still read "behind", because the value used the new 4-week best and the status used the last session. And an error that quoted half an emoji back could not be sent over MCP at all. Both are fixed: one measure now feeds a goal's value, status and pace, and every result is tested by encoding it the way the wire does.

### Package C: the MCP surface

- Six functions: `whoami`, `list_exercises`, `log_lifts`, `log_bodyweight`, `set_goal`, `get_progress`. Two resources: what Spotter can and cannot tell you, and the exercise list.
- Each function has a title, permission hints, named typed arguments with descriptions, and a published result schema.
- One wrapper runs every function: it reads the verified token, finds the person's account, runs the service in a worker thread, and writes one call-log row that holds no argument and no result.
- A failed call is flagged as an error and carries `{error, message, fix}`. A duplicate is a success that says nothing was written.

**The review.** Verdict: sound, no blocker. The reviewer ran a real server: 20 simultaneous calls from two people with no result for the wrong person; 50 three-second calls at once with no deadlock, while `/health` answered in 16 ms; every success checked against the schema the server publishes; a marker sent in every argument and found in no call-log row and no log line. It undid 40 behaviours; 34 turned a test red.

| Finding | Fix |
|---|---|
| Four behaviours no test noticed: writes marked as coming from Claude, one optional argument reaching the service, a not-a-number result refused, the command-line caller refusing redirects | Each has a test, and one table-driven test now checks every optional argument of every function reaches its service |
| The command-line caller printed whatever the server sent back, which could include the token | The token is masked in everything it prints |
| The MCP library logged a line on every call, and logged a caller-chosen tool name | The library's logging is turned down to warnings |
| A database pool timeout read as an internal error | It reads as "database unavailable" |

Accepted as it is: a call the MCP library refuses before Spotter's code runs (a wrong-typed argument, an unknown function) leaves no call-log row.

### A design change after the first live use: nobody types a time zone

The first live sign-in showed the mistake at once. The Settings page asked the person to type a zone name such as `Asia/Kolkata` and save it, and `whoami` told Claude to send the person to that page. The plan had always had Claude setting it, through `update_settings`; the stage contract had left that function for a later stage and kept only the web form. Staging the build had put a chore on the first thing a new person meets.

| Where | Before | After |
|---|---|---|
| From Claude | `whoami` said: send the person to Settings | Claude sets it with `update_settings`: the person's zone if it knows it, otherwise one question, "which city are you in?" |
| On the web | A text box for a zone name | The browser's own zone is saved at the first sign-in, with no click |
| Settings | Type and save | The zone already set, and a list to correct it |

- `update_settings` is the seventh function. In this stage it sets the time zone only. It can never change what Claude may do: that field is refused by the service and absent from the function's arguments.
- One function owns zone names. Chrome reports India as `Asia/Calcutta`, an old alias; the web path and Claude's path both store `Asia/Kolkata`.
- The page now runs one 17-line script, so the content security policy allows scripts from Spotter's own address and nowhere else.

**The review.** Verdict: sound, no blocker. The reviewer could not make the script loop: not with a refused zone, no browser storage, the back button or two tabs. It sent 20,000 junk zone names: none crashed, and every accepted one was a real zone. It could not change what Claude may do through the new function, by any spelling or shape. A page on another site could not set someone's zone.

| Finding | Fix |
|---|---|
| Changing zone hid entries. An entry dated "tomorrow" in the new zone vanished from progress until its date arrived, because every read kept only dates up to today | A read never leaves an entry out for its date. In any number worked out over a window ending today, such an entry counts as today's. Writes still refuse a date after today |
| The website and Claude stored the zone through two code paths | One path for both, `update_settings`, which locks the person's row first |
| The website's error for a bad zone still said "use an IANA name" | It says to choose from the list |
| Old zone names that browsers still report resolved to a neighbouring country's city: nine of the 100 old names, checked by hand | A table of the nine, and a test that fails when the zone data gains an old name nobody has checked |
| Eight behaviours no test noticed, four of them the script's loop guards | A test each. Every new test was broken on purpose and went red |

**The fixes were reviewed too, and one was half a fix.** The reviewer undid every fix and saw a test go red each time. But the first one had only fixed what is shown. Anything that depends on order still used the stored date, so after a zone change moved today back a day:

| What went wrong | Example |
|---|---|
| A false new best | 120 kg already logged and dated tomorrow. Then 110 kg logged today was announced as a new best, while progress showed 120 |
| A stale "latest" weigh-in | 80 kg dated tomorrow, then 81 kg today. The trend called 80 the latest, while the average used 81 |

The fix is one ordering rule, written once and used by every reader: entries are ordered by effective date (the entry's own date, or today when it is later), and within a date by the order they were logged. "Latest", "previous" and "newest first" all mean that order. Stored dates are still what is shown.

### Exit test

| Check | Status |
|---|---|
| The full suite is green | Done: 739 tests, plain and with warnings as errors, on a clean export |
| The cross-user test passes, through the services and through MCP | Done: in the suite, and the reviewer ran 20 simultaneous calls from two people against a real server |
| Live: the sign-in id on Settings equals `whoami.user_id`, and the account id is the same | Web side seen: Settings shows the stage 1 sign-in id and account 1. Claude logged into account 1 as session 1 |
| Live: `log_lifts` from Claude, then the same message again, reports a duplicate | Done: the second call wrote nothing and named the first session |

The live run found one wording problem. A lift's first session has nothing to beat, so it sets no best, and the summary said "No new bests." Claude read that as a bug and told the person so. The summary now says "First Back Squat session: estimated max 70 kg is the mark to beat."

### What this stage taught

- **The host did not read the repo's deploy file.** The first deploy of package A succeeded and the database stayed empty: the migration step had never run. The host has retired config files for new services, so the start command, the pre-deploy step and the health check now live in its dashboard. The check that caught it was looking at the database itself, not at the green deploy.
- **"Deployment successful" does not mean the step you added ran.** Verify the effect, not the status.
- **A wide `except` turns every bug into the wrong message.** The web pages caught every exception from a database call and showed "cannot reach the database". A typo would have looked like an outage.
- **A test can pass because its setup never reached the code.** The tampered-cookie test set its cookie with no domain, so the cookie was never sent. The page redirected for a different reason, and the test was green.
- **SQLite accepts what Postgres refuses.** A NUL character in text, an id past 32 bits: the tests ran green and the host would have crashed. A fuzz run and reading the SQL as Postgres would see it found both; the test suite could not.
- **A passing isolation test does not prove every query is scoped.** With the owner filter removed from two helper queries, the suite stayed green. Undoing each filter and watching for a red test is what found them.
- **Walk the first-use path before fixing a stage's function list.** Every stage 2 test passed, every review came back sound, and the first thing a real person saw was a form asking for an IANA zone name. No test or reviewer was asked "what does a new person have to do by hand, and could the software do it?" That question now opens every stage contract.
- **A safe filter stops being safe when the thing it compares against can move.** Every read kept only entries dated up to today. That hid nothing while today only went forward. Letting the time zone change let today go back a day, and yesterday's filter hid this morning's weigh-in. Removing the filter was half the fix: the same assumption sat in every sort, and it took a second review to find it there.
- **A true result can still be misread by the model that relays it.** "No new bests" was correct for a first session, and Claude called it a bug. Every result text is read by a model before a person sees it, so a result that is right for a non-obvious reason has to say the reason.
- **A fix is new code, and it needs its own review.** The first review of the services found the crashes. The review of the fixes found a contradiction the fixes themselves had introduced.
- **A library can log on your behalf.** The MCP library set up logging at INFO when the server was built, and one of its lines repeated a name chosen by the caller. Spotter's own "no content in the logs" rule did not cover lines it never wrote, until a reviewer read the real log.
- **Matching a shell command with one pattern does not work.** The command arrives inside a JSON string, so quotes are escaped, and no single pattern told `bash -c "git commit"` from `--grep="git commit"`. Splitting into shell words did.

## Stage 3: lifts, weight and measurements you can see

**Goal: everything a person logs by talking shows on a web page the same minute, can be fixed or removed from either side, and Claude does only what the person has allowed.** Contract: `docs/contracts/stage-3.md`.

The contract opened by walking the first-use path, as the stage 2 lesson demanded. A new person talks and signs in, nothing else: sign-in lands on Today; the zone saves silently; "log back squat 3×5 at 60" shows on Today; "it was 65" fixes it; "delete that" is refused until the person turns deleting on in Settings, which is their consent and the one page-send Spotter makes.

### What was built

Three packages on disjoint files, built at the same time in one working tree: services and schema, web, MCP.

| Package | What |
|---|---|
| Services and schema | `log_measurement` (nine kinds, unit and range per kind, one live value per kind per day with replace and restore), `edit_entry` (in place, re-keyed, a moved weigh-in never silently replaces), `get_log` (one to fourteen days, every day in the range, bests per session from the one history), `set_claude_can` (web only), the write limit (120 in 10 minutes, counted from the call log, checked after the lock in all eight writes), migration `0002` (a partial unique index on live measurements, built after keeping one value per kind and day) |
| Web | Sign-in lands on Today. Day page from `get_log` with weigh-in and measurement forms, Edit and Delete on every entry, Delete with Undo. Edit pages per kind. Progress with charts, one lift's page, the permission switch on Settings. Every web write leaves a call-log row under the MCP name, through one helper. The zone form carries `next`, followed only for a local path |
| MCP | Twelve functions. `NEEDS` maps each to `read`, `log` or `log_delete`; the wrapper checks the person's switch before the service runs, and the server refuses to start if a function has no level. `delete_entry` carries the destructive hint. `not_allowed` is the one text in Spotter that sends a person to a page, and a test names it as the only exception |

Each builder broke its own behaviours on purpose before reporting: 37, 10 and 8 breaks, each turning a test red. One wait-rounding case survived until a test was added for it. Suite: 945 tests, plain and with warnings as errors.

### The review

Two reviewers at once, one over the services, migration and MCP surface, one over the web. Both: sound, no blocker.

**Services, migration and MCP.** The reviewer ran a real server with real tokens through all twelve functions at each of the three switch values: at `read` every write was refused and nothing changed; at `log` everything worked but delete; bad switch values counted as `read`; a `claude_can` argument smuggled into `update_settings` was dropped by the library and refused by the service. Every edit a log would refuse was refused by the edit too (fourteen cases, each with the stored row unchanged). Migration 0002 on a database with three live waists on one day kept the last-logged and soft-deleted the others, with every row still there. It undid 28 behaviours; 26 turned a test red.

| Finding | Fix |
|---|---|
| The write limit could be overshot by calls arriving together: a call's call-log row was written after its transaction committed, so writers queued behind the user lock all passed the count. Six simultaneous calls with two writes left saved four and five | A call writes its call-log row before the service runs and completes it afterwards. The count includes the call's own row |
| Refused writes count against the limit, so a loop of refused deletes could use up the web's budget | Accepted and documented: a loop of refusals should be throttled |
| "Nothing is ever erased" sat next to edits, which overwrite values | The doc says an edit keeps no old values |
| Two doc facts (the limit's number, the delete sentence) had no test | Asserted in the text test |
| The person's own switch is a write with no limit | Exempt on purpose, now said in the contract: it is never Claude's call |

**Web.** The reviewer tried fifteen `next` values, none left the site; every id and kind in a path gave a 404 that echoed nothing; HTML in a kind or value was refused; the switch stored only its three values against seven bad ones; forty-one lifts on Progress rendered in 0.11 s. It ran 33 mutants; 24 died outright, one crashed the server at start-up, and the survivors are the table below.

| Finding | Fix |
|---|---|
| `/day/0001-01-01` was a 500: the previous day overflowed | A 404 page |
| For an hour after a 25-hour zone change, a later day's "previous" link could 404 | Previous is the latest of today and the later days |
| No test put HTML in notes: marking notes safe in two templates passed every test | A test with a script tag and an image handler |
| No test read the CSRF token from the Undo form | The test posts the rendered form's own token |
| The Today-in-your-zone test passed with the machine's date, since UTC+14 agrees most of the day | The clock is frozen at a moment where they differ |
| Two `next` checks had no test | Added |

### Exit test

Run live by an agent driving the person's own browser: a new Claude chat with the Spotter connector in one tab, the Today page reloaded in another after every step.

| Check | Status |
|---|---|
| The full suite is green, plain and with warnings as errors, on a clean export | Done: 980 tests |
| "Log back squat, 3×5 at 60" shows on Today after one reload | Done: a second session beside the morning's |
| "It was 65, not 60" | Done: Today shows 65, with two new-best lines |
| "Delete that session" with the switch at log | Done: Claude called delete, the server refused it, Claude explained and linked Settings. The block was the server's, which is the point |
| Switch to "log and delete" on Settings, "Delete it" | Done: the client asked to allow the destructive tool once, then the session was gone |
| "Restore it" | Done: back, with its bests |
| "Waist 84", then "Waist 84" again | First shown. Second: Claude answered from the conversation without calling Spotter, so the server's duplicate check for measurements was not observed live; it is covered by the suite and by the reviewer's run through MCP |
| Switch set back to log | Done |

Two things the run showed about the model rather than the server. After the edit to 65 kg, Claude did not mention the new bests the page shows: the edit result says bests come from history rather than listing them. And on the repeated "Waist 84", Claude judged the repeat itself instead of sending it. Both go to the eval set and the reply-shape stage.

### What this stage taught

- **A limit counted after the fact is not a limit under load.** The write limit counted call-log rows that were written when a call finished. Six calls arriving together all passed the count before any row existed. Writing the row before the call runs, under the same lock the writes queue on, closed it, at the price of one more row update per call.
- **A reviewer's time is best spent where the contract is newest.** Both reviewers were pointed at the new rules first (the switch, the limit, the edit rules, `next`), and that is where the findings were. The parts carried from stage 2 produced none.
- **The model can answer from the conversation instead of calling the server.** A repeated "Waist 84" never reached Spotter; Claude said it was already logged, which happened to be true. The duplicate guard protects against retries the model does send, not against the model deciding for itself. An eval case has to check the call was made.
