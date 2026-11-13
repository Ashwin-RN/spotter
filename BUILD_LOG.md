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
- Local assistant tooling for the build: builder and reviewer agents, a stage-check command, and a hook that runs the suite before a commit. It was in the repo until 2026-11-11, and is now kept on the builder's machine only (stage 6).

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

## Stage 4: food and cardio

**Goal: a person logs what they ate and what they did by talking, every calorie number carries a tag saying who supplied it, and cardio gets a calorie estimate the person did not have to work out.** Contract: `docs/contracts/stage-4.md`.

The first-use path, walked before anything was built: a new person with nothing saved says "Lunch: two rotis and dal". Claude estimates, says so, and the entries land tagged as estimates. "Dal was 150" edits the entry and the tag becomes *you said*. "Save roti: 1 roti, 120 kcal" is the first saved food; from then on "two rotis" goes by id and servings and the app supplies the number. Nobody is asked which meal: the server picks it from the person's own clock. The USDA lookup sits behind a key and the stage ships without it. Nothing is set up by hand.

Three rules were added. A food tag is the server's (`you_said`, `saved_food`, `usda`, `claude_estimate`): Claude sends a number, or an id and an amount, or a number marked as its estimate, and the server decides the tag. A number and an id never travel together: the app supplies the number for a saved or USDA food, and a `kcal` sent with an id is refused. An estimate stays an estimate until the person states a number.

### What was built

Four packages: services, web and MCP on disjoint files at the same time, then the evals once the services and MCP had landed.

| Package | What |
|---|---|
| Services | `log_food` (one to thirty entries in one call, all or nothing, the tag decided per entry, the meal from the clock, the duplicate guard over the whole set), `save_food` (the same name updates, a deleted food comes back), `find_food` (saved foods most-logged first, then the person's own recent entries, then USDA when the key is set), the USDA client (standard library only, the key in a header and never in an address, a five-second timeout, both cache tables, off without the key), `log_cardio` (kcal = MET × 3.5 × kg ÷ 200 × minutes from body weight now, a note instead of a number when there is no weigh-in, the machine's number kept beside it, pace and speed, the last seven days' minutes against the target), four targets in `update_settings`, `edit_entry` for both kinds (a kcal edit re-tags to *you said*, the same number included; a cardio edit re-estimates), food and cardio in `get_log`, a cardio activity in `get_progress`, fifteen cardio activities with their 2024 Compendium codes in the exercise list, the three new writes locked and limited |
| Web | The day page shows food by meal with each entry's tag in words and estimates marked, the total, the estimated share, the target and what remains, and net only when adding exercise back is on; cardio with estimate and machine number side by side. A one-line food form (always *you said*; a meal only when chosen) and a cardio form. Edit pages for both kinds, a Foods page for saved foods with delete and undo, the targets on Settings. The Compendium attribution line is a footer on exactly the pages that show an estimate |
| MCP | Sixteen functions. `find_food`, `log_food`, `save_food`, `log_cardio` with typed arguments and result models; the tag rule written once in Claude's terms and used by both the instructions and the `log_food` description; a `source` sent by a caller is dropped before the service; `can-and-cant` gains the tags, the meal hours, the cardio formula, the Compendium sentence and licence, what USDA covers, and that no Indian table values are shipped (IFCT 2017 and INDB are not openly licensed) |
| Evals | The first eval cases, run with the Claude Code CLI against a local server with a fake sign-in provider. The server records what it received; the eval judges that, not the model's own account. See `docs/evals.md` |

Each builder broke its own behaviours before reporting: 34, 12 and 9 breaks, each turning a test red. One weak test was found that way: sorting saved foods by name happened to give the same order as by times logged. Suite before review: 1181 tests, plain and with warnings as errors. The models and the migrations were compared column by column on fresh databases: the stage 1 schema already held every table and column this stage needed, so there is no stage 4 migration.

### The review

Two reviewers at once, one over the services and MCP surface, one over the web, plus a third agent sent to pacompendium.com with only the MET table: all fifteen codes and values matched the 2024 Compendium rows, none flagged as estimated.

**Services and MCP.** The reviewer ran the tag table through MCP (a stated number, an estimate, each id with and without a number, both ids, a smuggled `source`, 31 entries, 5,001 kcal), every edit rule, isolation across two people for food, saved foods and cardio, the meal slot at six boundaries in the person's own zone, USDA with a fake transport, the cardio formula by hand, and the targets' ranges. It undid 25 behaviours; 24 turned a test red. Four should-fixes and six smaller findings; all fixed, and the reviewer re-checked each on a copy.

| Finding | Fix |
|---|---|
| Logging a USDA food by id with an empty cache failed on SQLite: the lookup ran inside the write transaction, holding the user lock while a second session tried to store the cache row, and the network call ran under the lock too | Every USDA id is resolved, and the cache filled, before the lock; no network call under a lock |
| A number worked out from a saved or USDA food skipped the 5,000 kcal cap: fifty servings of a 5,000 kcal food stored 250,000 kcal | The worked-out number goes through the same range, with a fix naming servings or grams |
| The USDA client followed redirects, carrying the key to the new host, the hole stage 1 closed for sign-in | The USDA client uses the sign-in module's redirect-refusing opener |
| Two failures escaped as `internal_error`: an incomplete read from USDA, and two first searches for one query inserting the same cache row | Both are "USDA did not answer"; the cache store is an upsert and a failed store still returns the answer |
| A target could be cleared by the service but not through MCP: the wrapper dropped a null | The four target fields tell "not sent" from "sent as null"; null clears, and the descriptions say so |
| A stated speed could contradict the distance and minutes (30 km/h beside a 6:00/km pace) | A stated speed more than 20% from distance ÷ minutes is refused, with both numbers; on edit too |
| A back-dated session's estimate uses body weight now, by decision, but the summary did not say so | The summary says "from body weight now, 80 kg" when the date is not today |
| Half past midnight was breakfast | 22:00 to 04:00 is a snack; the contract and the doc amended |
| The exercise sync never updated an existing row, so a corrected MET would not reach a live table | The sync updates a cardio row's MET and code when they differ |
| Dropping the meal from the duplicate key left every test green | A test: the same foods at another meal are not a duplicate |

The speed check took three passes to close, and the re-checks are why. The first fix held when logging but not when editing: a stated speed was kept unchecked when the distance or minutes changed. The second fix held in the service but not through MCP: its error told Claude to send the speed as null to have it worked out again, and the MCP wrapper dropped nulls, a dead end. The third let an explicit null through `edit_entry` for cardio's optional fields (null clears; for speed, null means work it out), and made the edit summary show the stored speed rather than the null that asked for it. A USDA lookup also now runs after a cheap limit check, so a throttled loop cannot burn the USDA quota while being refused.

**Web.** The reviewer posted crafted forms (a saved-food id, `estimated`, a tag, a USDA id, servings, grams in the food form; a machine number and notes in the cardio form) and found every one dropped; tried another person's entries and foods on every route with nine kinds of id and got a 404 that echoed nothing; checked every figure on the day page against `get_log`; found the attribution line on exactly the two pages that show an estimate; and rendered a day with sixty-five entries in 0.03 s. It ran 36 mutants: 28 died, and the survivors were five test gaps and one product gap. No behaviour bugs.

| Finding | Fix |
|---|---|
| None of the eight new write routes had a test for the write limit's 429 | All eight in the limit test |
| An unchanged saved-food edit, the "times logged" count, a crafted food name on the edit page, and ids in the food form had no test | Four tests, each killing its mutant |
| The web had no way to confirm a Claude estimate without retyping its number: an untouched kcal sends nothing | A Confirm button beside each estimate on the day page, posting the entry's own number so the service re-tags it *you said* |
| The Confirm form's rendered token was not read by any test | A test posts the token read from the rendered form |

**The first evals, and what they found.** Ten cases, three runs each, the Claude Code CLI against a local server with a fake sign-in provider (the same hook the tests use; nothing in the app bypassed). On the first run, eight of nine cases passed 3 of 3 and one passed 0 of 3: "30 minutes on the treadmill" made the model ask "walk or run?" every time, because the list has two treadmill rows and nothing said what to do when a word fits more than one activity. A second case passed for the wrong reason: "Delete my last weigh-in" was deleted at once without a question, and the switch at `log` refused it. Both were text gaps. The instructions gained an activity rule (log the one the words point to, more than 7 km/h is a run, with nothing to go on the walk, say which, never ask first) and a delete rule (delete only the entry named; when the words fit more than one, ask which; never delete on a guess), the `log_cardio` result gained a note naming the sibling activity with its own estimate, and the delete case was split into an ambiguous one (two weigh-ins, Claude allowed to delete: it must ask) and an explicit one (it must delete the right row). After the change the three cases passed 9 of 9 runs.

### Exit test

| Check | Status |
|---|---|
| The full suite is green, plain and with warnings as errors, on a clean export | Done: 1287 tests, both ways, on the export of `cdb10c8` |
| The connector offers the four new functions | Done: Claude listed sixteen, the four among them, with no reconnect |
| "Save roti as a food: 1 roti is 120 kcal" | Done: one `save_food`; Foods shows Roti, 1 roti, 120 kcal |
| "Two rotis and dal, then 30 minutes on the treadmill" in one message → one `log_food` with both tags, one `log_cardio`, Today shows the meal, the cardio and the right total | Done: `find_food("dal")`, `list_exercises(cardio)`, then one `log_food` with two entries (Roti by id, 2 servings, no number; dal 150 marked as an estimate; no meal sent) and one `log_cardio` (Treadmill Walk, 30 min). Claude asked nothing, said the dal was its estimate, said it chose a walk because no speed was given and offered the run. Today: both under Lunch, "saved food" and "estimate" tags, a Confirm button on the dal, total 390 of which 150 estimated (38%); the cardio with the no-weigh-in note and no footer line, since the account had no weigh-in that day |
| "Dal was 150" → the tag flips to *you said*; the total follows | Done, by the confirmation path: Claude's estimate was already 150, so the edit sent the same number, the tag became *you said*, the Confirm button went, and the total stayed 390 |
| "Dal was 180" → the number changes | Done: one `edit_entry`, kcal 180; Today shows dal 180 *you said*, total 420 |
| Settings shows the targets; "Set my calorie target to 2200"; "Remove my calorie target" | Done: the form is there; `update_settings(2200)` gave Target 2200 and Remaining 1810 on Today; `update_settings(null)` cleared it and the lines went |
| The evals: every case passes at least 2 of 3 runs, the rate reported as it is | Done: ten cases, 30 of 30 runs, model `claude-opus-5-5`, $4.46 at list price. Run after the last service fix; one later change (explicit nulls through `edit_entry`, and a summary wording for cleared values) landed after it, and no case edits an entry |

Run live by the same agent in the person's browser as stage 3's, every tool call allowed by hand. Two things the run showed. The cardio estimate and the Compendium footer were not seen live, because the account had no weigh-in that day; the suite and the reviewer's MCP run cover them, and nobody invented a weigh-in for a real account. And the day page read "2 rotis × 2: 240 kcal": Claude wrote the count into the description and into servings, so a saved-food line can look like twice its amount. The number is right; the display and the description rule go to stage 5.

Two edits in the stage commit were not re-checked by a reviewer: the summary now writes a cleared value as "none, cleared" instead of Python's `None`, for any field on any kind, and a cleared distance says "cleared with the distance" for the speed it takes with it. The suite covers both.

### What this stage taught

- **The first evals found text, not code.** Every service rule passed its tests and its review; two of ten eval cases still went wrong, and both were sentences that were missing from the instructions (what to do when a word fits two activities; what to do when "delete" fits two entries). The cases were cheap to run and caught this before a person did. The ground truth is the server's record of what it received, never the model's account of what it did.
- **A rule that spans the service and its wrapper is only checked through the wrapper.** The speed fix was whole in the service and useless through MCP, because the service's own advice (send null) was a value the wrapper could not carry. The reviewer's re-check of each fix, through MCP, is what found it, twice.
- **No network call under a lock.** The USDA lookup inside the write transaction worked on Postgres and failed on SQLite, which the tests and the evals run on. Resolving every outside lookup before the lock is cheaper than finding out which database is running.

## Stage 5: goals and trends

**Goal: a person asks "how am I doing?" and "am I on track?" and gets numbers worked out from their own logs; the two clicks only they can make, export and delete, sit on Settings; and an empty account can fill itself with sample data to see all of it before logging anything.** Contract: `docs/contracts/stage-5.md`.

The first-use path, walked before anything was built: "I want to be 75 kg by the end of March" sets a goal from the 7-day average with the pace it needs. "How am I doing?" is one call, `get_trends`: the smoothed weight trend and its rate, calories a day over the last 7 and 28 logged days, sessions and cardio minutes against the targets, and a maintenance estimate once 14 of the last 21 days have food logged, else how many more it needs. "Am I on track?" is `get_goals`: each goal's needed pace against its actual pace, the date it is reached at that pace, and one of reached, on track, behind, not enough data. Export and delete are the person's own clicks on Settings, and Claude is told to say so rather than offer. A new account's Today has one button that fills four weeks of everything, marked sample, removed by one click.

Two rules were added. A read that includes sample rows says so (`includes_sample`, with a note naming the button that removes them). Export and delete account are never offered through Claude: no function does either.

### What was built

Four packages again: services, web and MCP on disjoint files at the same time, the evals once the services and MCP had landed. This stage needed the first migration since stage 3: one nullable column, `goal.measurement_kind`, because the stage 1 schema had nowhere to say which measurement a goal was about.

| Package | What |
|---|---|
| Services | Four goal kinds through `set_goal` (a lift's estimated max, body weight, a measurement of six kinds, a time for a distance in one cardio activity), one shape of numbers for every kind, with the projected date from the same fitted line as the projection; `get_trends` (an exponentially weighted moving average of the weigh-ins, α 0.1; the rate per week between a weigh-in in the last 7 days and the last one 14 to 42 days back; calorie averages over logged days; sessions and cardio against the targets; the maintenance estimate from the 21-day window at 7,700 kcal a kg, never setting the target); `get_goals`; sample data (307 rows over 28 days, deterministic from the day and a fixed seed, added only to an empty account as one write, every row marked, removed completely by one call); export as CSV texts; `delete_account` (the person's call-log rows kept with no owner, then the user row, and the schema's cascade); a read throttle on `find_food`; `list_saved_foods`; a saved food's description defaulting to its name; `includes_sample` on every read |
| Web | A Goals page with every kind's numbers, Hit, Drop and Reopen, and one form for any kind; Progress with a Trends card from `get_trends` and the chart's line now the smoothed trend; Settings with Export (a zip of CSVs, built in memory), Remove sample data, and Delete my account behind a confirm page that deletes the sign-in at the provider first and the rows here second, so a half-done delete can be finished; "Add sample data" on an empty account's Today, a banner and a tag while sample rows exist; a saved food shown as "Roti × 2 (2 rotis)"; the landing page rewritten as a six-step walkthrough from sign-in to the first sentence said to Claude, with the Claude Code command carrying the live address |
| MCP | Eighteen functions. `get_trends` and `get_goals`; `set_goal` with four kinds, `measurement_kind` and `distance_km`; five new instruction sentences kept as named constants shared with the descriptions (how am I doing, am I on track, the end of a month, export and delete are the person's clicks, the count goes in servings); result models for every new shape, and a new check that walks every real result at every depth and fails on any key no model has, which found a stage 4 gap (`met` and `compendium_code` on the exercise list); `can-and-cant` with the goal kinds, the trend method, the maintenance rule, sample rows, and the one accepted hole: a sign-in already given to Claude stays valid until it expires, an hour at most, and a call in that hour recreates an empty account |
| Evals | Six cases added, sixteen in all; the runner gains a next-month-end date and a reply-must-say check. See `docs/evals.md` |

Each builder broke its own behaviours before reporting: 57, 40 and 42 breaks, each turning a test red (one survivor on the services side was a test gap, closed). Suite before review: 1,543 tests, plain and with warnings as errors, from 1,287. The sample set's goal and trend numbers for a fixed day were worked out a second way, by a plain script from the seed with no app code, and matched to the decimal.

### The review

Two reviewers at once, one over the services and MCP surface, one over the web, each with the contract's numbers in hand.

**Services and MCP.** The reviewer hand-worked every number the contract fixes and compared it with the code's: the EWMA carried unrounded (80, 80.04, 80.04 gives 80.0, 80.0, 80.01; a rounded carry would give 80.0), the rate's two ends at every boundary (a weigh-in at T−6 counts and T−7 does not; a reference at T−14 and T−42 counts, T−13 and T−43 do not), the maintenance estimate (14 days at 2,000 kcal with 80.0 → 79.3 kg gives 2,257; 13 days gives "needs 1 more"; the sample day's 2,513 = 2,198 + 0.86 × 7,700 ÷ 21), the projected date and its three nulls (a line falling 0.5 kg a week from 80.5 to 78 lands on T+35; exactly 730 days out is kept), needed against actual for each of the four kinds, a 5 km time from a 10 km run's pace with shorter, older and slower runs left out. It drove `set_goal` of every kind through MCP, checked every new refusal's fix could be followed with the arguments the wrapper carries (the stage 4 lesson), ran the migration up and down with rows present and read its Postgres SQL, added sample data for two people and checked determinism, every count and tag, every duplicate key, the three refusals and that removal touched only one person's sample rows, read the export for another person's rows (none) and deleted rows (present), deleted an account and found every table empty for that person and the call-log rows kept with no owner, and found the read throttle counts only `find_food` and never blocks a write. It ran 55 mutants on the services and 9 on the MCP side: 59 died, one was equivalent, and four survived. No code bugs; the four survivors were missing tests.

| Finding | Fix |
|---|---|
| Removing the `deleted_at` filters from a goal's "now" (a deleted waist value, a deleted faster run) left the whole suite green | Tests: a deleted 84 leaves the waist at 86; a deleted faster run leaves the time at 30; restoring brings them back |
| Only a dropped goal was tested as "closed"; a hit goal would have blocked sample data unnoticed | A hit goal in the test |
| The sample flags were only ever tested on the full sample set, where every kind sets the flag; a flag broken for one kind survived | Tests per kind: with only sample meals left, `get_log` and `get_trends` still say so; sample weigh-ins under a real goal flag `get_goals` |
| Removing sample data left a real entry logged against a sample food with its number and tag but no link, so its day line lost "× 3 (3 rotis)" | The removal summary counts and names those entries |
| An edit that tried to change a goal's identity got `cannot_change_kind` and a contradicting `nothing_to_change` | Only the first |
| Whole-number kcal written as `100.0` in the CSV | Written as `100` |
| A lift goal naming a cardio activity answered "not on the exercise list", which was false | "Outdoor Run is a cardio activity; a lift goal needs a lift", and the reverse, each with a fix naming the right kind |

**Web.** The reviewer posted every kind of goal and thirty-odd bad values (wrong-kind exercises both ways, steps and sleep, a target the wrong way or equal to the start, past and malformed dates, oversized text, an empty form) and found every refusal beside the right field with nothing written, tried another person's goal ids on the status route with nine kinds of id and got the same 404 echoing nothing, opened the export zip and found all ten files with deleted rows in and nothing of another person, ran the delete with the seam succeeding, raising, answering "no such user" and failing locally after the provider had succeeded, checked the Trends card figure by figure against `get_trends` for the same person, counted sample tags against live rows on all 29 day pages, and read the landing page against the current claude.ai and Claude Code documentation. It ran 42 mutants: 36 died, two were equivalent, four were test gaps. Four should-fixes, none a data bug.

| Finding | Fix |
|---|---|
| Sample goals showed no "sample" tag: the numbers carried the flag, the template never read it | The tag beside the label, on closed goals too (the closed shape gained the flag) |
| Every `set_goal` warning was thrown away after a save: a second identical goal was created with "Goal set." and no word that the first was kept | Warnings shown after a save and after Hit, Drop and Reopen, carried across the redirect as short codes; nothing from the address is echoed |
| At the write limit, the delete deleted the sign-in at the provider and then answered 429, leaving only the session cookie able to finish | Deleting the account is exempt from the write limit, as the Claude switch is: the person must always be able to leave. The local-failure page adds "Do not sign out until this finishes." |
| The Claude Code command on the landing page added Spotter only for the folder it was run in | `--scope user` |
| The claude.ai steps left out the connector's name field and the per-chat switch | Both named, as the help page has them |
| The serving pluraliser produced "2 mediums apple", "2 ozes", "2 tomatos" | Plurals only for "1 word" and "1 small/medium/large word" where a plain -s is right; units and awkward endings fall back to "2 × label" |
| "7-day average" shown when the figure was the latest weigh-in; the rate line used fixed text instead of the service's note saying which end is missing | "latest weigh-in" when the 7 days are empty; the service's note shown |
| The cardio "nothing to start from" refusal landed under Distance | Placed under "Where you are now" |
| The export zip was built on the event loop; dead stand-in services and skip markers could hide a broken export as a quiet skip | The threadpool; the scaffolding removed |

Both reviewers re-checked the fixed tree once the builders had stopped. The services reviewer drove each of its nine items through MCP again on the quiet tree (a deleted waist of 86 leaves the goal's start at 90; a hit goal no longer blocks sample data; the four flags per kind; the removal sentence; the single error on an identity edit; `100` in the CSV; the delete at 130 write rows with exactly one lock call; both wrong-kind refusals followed to a working call) and ran 15 more mutants, all dead: holds. The web reviewer re-ran every finding with the test client, crafted the warning codes (`bogus`, a script tag, `__class__`, an empty value, a 64-character value: none shown, nothing echoed), and found one leftover skip marker in a chart test, removed. On the sample flag the two builders' readings differed and the reviewer settled it: a read says `includes_sample` when sample rows are in its own numbers, so with only sample meals left `get_log` and `get_trends` say so and `get_goals` does not.

Two edits after the re-checks were mine and not re-checked by a reviewer: the leftover skip marker removed, and one serving of any label shown as the label itself ("1 roti (40 g)", not "1 × 1 roti (40 g)"). The suite covers both.

### Exit test

| Check | Status |
|---|---|
| The full suite is green, plain and with warnings as errors, on a clean export | Done: 1,598 tests, both ways, on the export of `9092aab` |
| Live, on an empty account: "Add sample data" fills Today, Progress and Goals; the three goals show the numbers the suite computed for that day | Done: one press; Today showed four meals with all three tags, a walk with its estimate, a weigh-in and two measurements, every row tagged sample; Goals showed 79.9 → 79.01 needing −0.25 against −0.29 reached by 2026-12-26, 93.33 → 107.92 needing 1.01 against 4.41 by 2026-11-26, 31:00 → 29:30 needing −0.31 against −0.5 by 2026-12-12, all on track; Progress showed smoothed 79.22, −0.24 kg a week from 2026-10-27, 2,216 and 2,196 kcal a day, 41% estimates, 3 sessions, 84.5 minutes, maintenance about 2,513: every figure the suite asserts for that day |
| "How am I doing?" from Claude → `get_trends` with the same numbers; "Am I on track?" → `get_goals` | Done: for the first question Claude called `whoami`, `get_progress`, `get_log` (14 days) and `get_trends`, and its reply quoted the trend, calorie, maintenance, session and cardio figures as the page had them, and said first that everything was sample data; for the second, one `get_goals`, three goals on track with the page's numbers |
| "Drop the squat goal" → dropped; "Remove sample data" → Today is empty again | Done: one `set_goal` with the squat goal's id and status dropped, no lookup first; Goals showed it under closed goals, tagged sample, with Reopen. Remove sample data: Today empty, no banner, the button back; no goals, no foods |
| "Export my data" from Settings → a zip with every file | Done: `spotter-export-2026-11-10.zip`, 7,122 bytes, downloaded |
| Delete account: the person's own click; removes the account here and at the provider | The person's, when they choose |
| The evals: every case passes at least 2 of 3 runs, the rate reported as it is | Done: sixteen cases, 48 of 48 runs. The CLI's default model had changed during the day to `claude-fable-5-2`, so the run cost $22.41 at list price against about $7 on the model the earlier runs used; the runner now pins `claude-opus-5-5` |

Run live by a browser agent in the person's own signed-in browser, every tool call allowed by hand, with the day still 2026-11-10 for the account so the suite's fixed-day numbers applied. Things the run showed, for the next stages: "How am I doing?" cost four read calls where one would do, and the reply claimed "every lift set a new best in its last session", which no page shows; Pull-Up, logged at 0 kg in the sample set, shows no estimated max and no best set beside a last-trained date; the maintenance line names its days but not the intake and weight change it used; the account's deleted Roti made the sample roti "Roti (sample)", as designed; the first press of "Add sample data" showed an empty page for a moment before the full one.

### What this stage taught

- **A deterministic sample set turns "matches a hand-worked example" into a test.** The plan's exit test asked for goal numbers checked by hand. With the sample rows fixed by the day and a seed, the suite asserts every goal and trend figure to the decimal for a fixed day, a plain script from the seed with no app code gives the same figures, and the live check is reading the same numbers off a page.
- **A builder's mutants test what the builder thought of.** All three builders ran their own mutation passes and reported every mutant dead. The reviewers' passes, from a different angle, still found five that lived: a deleted row leaking into a goal's "now", a hit goal blocking sample data, a sample flag broken for one kind. None was a code bug, every one was a test that did not exist, and each would have become a bug the first time the code was touched again.
- **The person must always be able to leave.** The write limit is right for every write except the one that ends the account: at the limit the first design deleted the sign-in at the provider and then refused the local delete. The exemption the Claude switch already had is the exemption delete needed.
- **Two reviewers see what one does not.** The web reviewer found the write-limit hole in a service; the services reviewer found a schema field the web had asked for and nobody had modelled. Findings crossed package lines both ways, and routing them is the control centre's job.

## Stage 6: the look, the numbers and the words

**Goal: every page and every number Spotter shows reads as finished work. One formatter and one set of text rules sit behind every page and every summary, and tests hold them. Then every page is rebuilt on one design.** Contract: `docs/contracts/stage-6.md`.

The stage lands in four steps. Step 1 was the design, agreed on a static build before any template changed. Step 2 is the number and text rules on today's pages, pushed on its own. Step 3 is the stylesheet, the charts and the new reads, committed locally and pushed with step 4. Step 4 (every page rebuilt) is added below when it lands.

The contract was reviewed against the code before any builder started: 36 findings on the first draft, 14 of them sentences two builders could have built two ways, then 20 more after the look was merged in. None changed the design. Most were about who hands what to whom, and how a half rounds.

### Step 2: what was built

No page changes shape in this step. Numbers and dates read the same on every page and in every summary, and the words lose their arrows, semicolons and filler.

| Package | What |
|---|---|
| Numbers and services | `app/formatting.py`: the one place a number or a date is rounded and written (`show`, `rounded`, `is_zero`, `widen`, `clock`, `field`, `day`, `day_heading`, `days`, `day_utc`). Halves round away from zero through `Decimal(repr(value))`, so 80.25 shows 80.3 where Python's `round` gives 80.2. Every summary, note, message and fix goes through it. The smoothed weight, the 7-day average and the estimated max come back at 1 decimal, rounded once on a copy when the result is built: nothing computes from a rounded field. A best and the one it beat carry a second decimal when they would otherwise read the same. Summaries carry dates as "Tue 10 Nov", a comma from 1,000, and no ids: each id is still in a field. The export gains a byte-order mark, a guard on text cells that start like a formula, and a unit on every number column (`avg_hr_bpm`) |
| Web | Jinja filters over the formatter, and every number and date on a page through them; "Previous day" and "Next day" in place of arrows; words where a page said "none"; a deleted entry named ("Food entry deleted.") |
| MCP | A reply paragraph in the server instructions (`REPLY_RULE`): quote each number as Spotter gives it, say "about" as a word, no dashes between clauses |
| Text rules and docs | `tests/text_rules.py`, one set of checks shared by three tests. T1 sweeps every template, every string constant in `app/`, the README and `docs/can-and-cant.md`, and fails a numeric format spec anywhere in `app/` outside the formatter. T2 holds every summary, note, message and fix the services return, on four accounts. T3 holds the visible text of every page on three. `tests/test_docs.py` holds the README's function count, page list and eval case count against the code, so the README no longer states a test count by hand |
| Evals | A judge key, `reply_lacks_pattern`, and one case, `decimals_and_dashes`: seventeen cases. The new case has not been run |

The order mattered. The formatter was built alone and reviewed before anything imported it, then the shared text checks, then services, web, the sweep and the evals side by side, then the old `rules.kg` deleted once nothing called it. Each builder broke its own behaviours before handing over, about 260 breaks across the packages. Those that stayed green were test gaps and were closed, apart from two in the formatter that changed no behaviour.

### Step 2: the review

Four reviewers, one on the formatter before the fan-out and three on the finished diffs.

| Finding | Fix |
|---|---|
| The formatter's rounding depended on the caller's Decimal context: with a precision of 3 set elsewhere, 1234.56 came back as 1230 | One private context around every operation; a test runs it under five hostile contexts |
| `rounded` on a run time with no stated step returned a fraction of 1/60, which would have gone into a result as 29.516666 | Refused with an error that says to pass the step |
| An edit that fills an empty field said "distance_km from None to 3" in the summary Claude reads. No test looked for the word | "distance_km set to 3"; T2 fails "None", "True", "False" and "null" in any summary |
| A before and after that print the same read as no change: a waist edited from 84.25 to 84.34 said "from 84.3 cm to 84.3 cm" | Both carry one more decimal when they would read the same, the rule a best and the one it beat already had |
| A goal's warning quoted the start at 2 decimals beside a field at 1 | The warning prints what the field prints |
| A refused weight of 1e300 was echoed as about 300 digits, twice a set | Echoed as sent, cut at 80 characters |
| T2 masked every value typed on an account in every text of that account, so an uncommaed number could hide behind a refused input | Each call's typed values are masked in that call's texts only |
| The cardio estimate cell's new words, "No weigh-in", were false once a weigh-in was logged later the same day: the estimate is fixed when the session is logged | "No estimate", and a footnote that is true in both states |
| An entry kind was spelled two ways: "Food entry deleted." and "Edit food" | One table for both |
| The text checks had gained a rule the contract does not have (no negative contractions on a page) | Out of the page rules |
| The README did not say the new eval case had not been run; the rounding line in `docs/can-and-cant.md` could be read as leaving a single weigh-in's decimals on body weight now | Both say so |

Held for step 3, with nothing hiding behind them today: seven ways the format-spec and template checks can be tightened (a nested spec, number formatting inside a template, a template in a subfolder among them).

### Step 2: exit

| Check | Status |
|---|---|
| The full suite is green, plain and with warnings as errors, on a clean export | 2,960 tests, from 1,598 |
| No page changed shape; the security header, every route and the stylesheet are as they were | Held by the reviewer's read of every template diff and by the existing header tests |
| The new eval case | Written and unit-tested. Not run: a run is the last item of the stage's exit |
| Live after the push | Deploy succeeded; `/health` 200, `/mcp` 401 without a token, the landing page 200, the security header unchanged |
| Live, with data on screen | Done on an empty account: sample data added, every page read, then removed. Dates, headings, day links, commas and run times read as the rules say on every page; no console error and no policy violation. Body weight now, the smoothed weight, the rate, three goals and two day totals matched between the pages and `get_trends`, `get_goals` and `get_log`. The three summaries carried no raw date, semicolon, arrow or id |

The live read also showed what step 2 left for the later steps. The old stylesheet breaks a word in the middle on the Trends card (`word-break: break-all`), a rate's unit words are joined to the number by a plain space, adding and removing sample data show no confirmation line, and a cardio result carries the stored `estimate_weight_kg` at 2 decimals beside the rounded `estimate.weight_kg`.

### What step 2 taught

- **Review the foundation alone.** Five packages lean on the formatter. Its review found a rounding that depended on global state and a silent wrong value, with no caller yet to rework.
- **A check that nobody asked for is still a cost.** The text checks arrived with one rule beyond the contract. It was right by a style guide and wrong for the stage, because every page would have been reworded for a rule nobody had agreed.
- **A summary is an interface.** The word "None" in a sentence passed every type check and every schema. Only a test that reads the sentence as a person would could catch it.

### Beside the steps, 2026-11-11

- **The README became a product page and setup guide for any MCP client.** Why use it, three setup steps that name no assistant, and a table of eight assistants, each "Tried" or "From its docs". The same day, dynamic client registration was switched on at the sign-in provider. Its public metadata now carries a `registration_endpoint`, so a client that registers itself can sign in, as well as one that uses a client ID metadata document.
- **The local assistant tooling left the repo.** The builder and reviewer agents, the stage-check command and the commit hook are kept on the builder's machine, ignored by git. They remain in the repo's history.

### Step 3: what was built

**The stylesheet, the charts and the new reads. No page changed shape: the one template change is the stylesheet link in `base.html`, which now carries a version. So step 3 is not pushed alone: the new stylesheet goes live with step 4's markup.**

| Lane | What |
|---|---|
| Text rules | Went first. The checks tightened on nine points, among them a nested format spec, a bare zero pad, number formatting inside a template, a template held in a constant or a subfolder, literals in `with` blocks and macro defaults, docs read a paragraph at a time, a stale exemption failing a test. The MCP surface is now checked under the rules for text the model reads |
| MCP | Five plan-line fields on every goal, each optional and described. `REPLY_RULE` gained its last sentence: say `plan_line` as written, and do not also quote the figures it carries. The estimate sentence now allows a food's kcal. `docs/can-and-cant.md` names the plan line and every rounded field |
| Numbers and services, pass 1 | The plan line: `metrics.goal_path`, one pure function, and five fields on every goal (`should_be_today`, `ahead_by`, `days_ahead`, `vs_plan`, `plan_line`). `goals.goal_charts`, the Goals page's read, adds the path, `now_basis` and `line_ended`. One table of measurement kind words, in `app/rules.py`, so "Water ml 2,501 ml" reads "Water 2,501 ml". A cardio result's `estimate_weight_kg` rounded with `estimate.weight_kg` |
| Numbers and services, pass 2 | `trends.get_days`, the 28-day grid with four weeks and the window's latest, highest and lowest weigh-in. `cardio.get_cardio`, an activity's sessions and bests, or a row per activity. `cardio.sessions_of` and `cardio.cardio_times`, shared with goals and Progress. `lifts.history` `reps_only`, for a lift never done above 0 kg. `bodyweight_chart`'s `since`. All web only: no MCP result changed |
| Web | `app/static/style.css`, written whole from the agreed design: 19 colour tokens, dark only, the system font. Eleven icons. `/static` cached for an hour, and the stylesheet link carries a version from the file's content. Eight chart functions in `app/charts.py`, two of them the kept charts restyled, first against hand-made data, then drawn from the real reads on the sample account. `tests/test_css.py` holds tokens, contrast pairs, tap sizes, motion and chart text sizes |

- The sample body weight goal is the close-call case the design expected. Its tag says On track, and its plan line says "79.01 kg now against 78.96 kg on the plan line today: 0.05 kg behind." Both are true: one compares the projection with the target, the other where the person is with where the line is today.
- The order: text rules first; then MCP and web side by side; then numbers and services in two passes in a row, since its two halves share `goals.py`, `progress.py` and `test_sample.py`. One builder was too much for the whole pass. Then a review of each lane, and a fix round for each list of findings: two for numbers and services, one per pass.

**Each builder broke its own behaviours before handing over. Every break turned a test red.**

| Lane and round | Breaks |
|---|---|
| Text rules | 37 of 37 |
| MCP; its fix round | 9 of 9; 7 of 7 |
| Web; its fix round | 57 of 57; 29 of 29 |
| Numbers and services, pass 1 | 31 of 31. 28 went red at first; the 3 that stayed green were test gaps, closed before the hand-over |
| Numbers and services, pass 2 | 38 of 38 |
| Fix round for pass 1 | 36 of 36, 23 of them the first round's breaks run again on the new code |
| Fix round for pass 2 | 47 of 47: 9 new, and the 38 of pass 2 run again |

### Step 3: the review

Three reviewers: the web lane's stylesheet and charts; the plan line end to end, across the services and the MCP descriptions; the new reads. The lead read every chart drawn from the real reads.

| Finding | Fix |
|---|---|
| Chart labels ran off the canvas: y-axis labels such as "102.5 kg" and "65 bpm" past the left edge, the goal's target label past the right | Every margin worked out from the widest label it holds, at the phone text size, with the unit on the top tick |
| Chart text showed at about 9.6 px on a 360 px phone, against the design's 11 to 14 px | 15 chart units on a phone, 30 on the compact pair, and a size and a width cap per chart on a laptop. The sizes first ruled for a laptop fell under 11 px at 960 px, so the caps replaced them. A test works each size out from the stylesheet at every width from 360 to 1,600 px |
| Pace dates were thinned by a fixed gap: the latest ran off the right edge from 9 sessions, and a date with a year never fitted | Labels placed by their estimated width. Those that would meet give way, and the latest keeps its own |
| A lift with one session wrote its date twice, one copy off the left edge | One date when the two ends meet |
| Smaller chart faults: a point dated after today drawn past the right edge; 31 grid lines on a wide range; a desc naming a projection that was not drawn; a day of 0 kcal drawn as no food; "1 sessions" | A point after today drawn at today; at most 5 grid lines; the desc follows the drawing; 0 kcal is food; counts through the formatter |
| `body { overflow-x: hidden }` would have hidden the sideways overflow the 360 px walk is there to find | Removed |
| A missing stylesheet would have stopped the app at import, `/mcp` included | The version falls back: the pages lose their look, and nothing else stops |
| The goal chart's desc made its own comparison, "Now 79 kg. The plan line is at 79 kg today. Now is behind it.": two equal figures beside a gap, which the close-call rules forbid | The desc says the goal's `plan_line` word for word |
| On the line after the target date, `plan_line` read "On the plan line today, at 78 kg." though the line had ended | "78 kg now, at the target." `goal_path` works out `ended` once, and `goal_charts` carries it as `line_ended` |
| `ended` was worked out in two places, and a line with no length before its target date had no test: two mutants lived, one printing a wrong sentence | One place, and two tests |
| The chart's series was built on every read, MCP reads included, though only the Goals chart uses it: 0.3 s a goal on four years of daily weigh-ins | Built only for `goal_charts` |
| Field descriptions a model reads were false in edge cases: `should_be_today` on a line with no length, `days_ahead` 0 off the line, `vs_plan` for a cardio time's half second, `plan_line` after the target date. `can-and-cant` still said every other figure keeps its precision | Each made true in every edge case, and the rounding sentence added |
| A test pinned a rule the code does not have (`days_ahead` 0 exactly on the line), and in kg only | Dropped; half-step pairs in cm, bpm and a cardio time added |
| The fastest-pace tie compared raw floats: 20.1 min over 4.02 km and 25 min over 5 km, both 5:00, went to the later session, while the 1 km best named the earlier | Paces compared as exact fractions of what was entered |
| Seven mutants lived in the new reads, among them a day counted twice for a session dated after today, a sample flag missed for an old run, a machine's kcal dropped | A test for each, and one for a restored row |
| The new kind words broke the Goals page's error placement, which matched the message's words | Held for step 4: the page places an error by its code or field |

Accepted as they are, and written into the contract: a nearly flat plan line gives a very large `days_ahead`; the fastest pace and the 1 km best can name different sessions when two paces differ by less than a time's 2 decimals of a minute; a sheet's chart draws in again each time the sheet opens.

Found by a reviewer, older than stage 6: `get_progress` for lifts takes 7 to 8 s on three years of daily lifting. It goes to stage 7, to be fixed only with results proven identical.

### Step 3: the re-check

A fourth reviewer read every fix round as new code, on a frozen copy, and broke it 42 ways. 39 breaks turned a test red. The suite passed on that copy both ways, 3,286 tests, and plain on a clean export of the commit (`b0ac9b6`).

Every finding on a fix list is fixed. The re-check found seven new things. None is live, because step 3 is not pushed. All seven are carried into step 4, which rebuilds the page the first one is on.

| Finding | Carried as |
|---|---|
| Must fix. The body weight card on Progress kept its old words after its chart took the last 28 days. Someone with weigh-ins, but fewer than two in the last 28 days, reads "The chart appears from the second weigh-in." and loses the 7-day average line | The card takes the contract's words, keyed on the 28 days, shows the average whenever there is a weigh-in, and gains a test with old weigh-ins |
| No test holds the growth of the tick steps: the three breaks that stayed green | Three pinned outputs |
| The width estimate is narrow for M, W, m and w: a "Wed" date or a bpm tick can pass the canvas edge by about 2 units | A width of their own, and two more wide labels in the test |
| Four notes: a chart outside the named contexts has no laptop cap; the weight sheet labels a date after today its own way; a leftover skip guard in a chart test; the sleep and water goal errors land on the form | Fixed with the pages |

Step 3 has no exit of its own. It goes live with step 4, and step 4's exit covers both.

**A change of method, 2026-11-11, the owner's call.** The whole suite no longer runs on every push. A docs-only push runs the docs tests. A small change runs the tests for the files it touches. The whole suite runs on a big push, such as a stage step, or about once in every four or five pushes.

### Beside the steps, 2026-11-12: the import's evals

Five runs of the evals, each on `claude-opus-5-5` through the Claude Code CLI. The cost is the CLI's list-price figure. On a subscription it is usage against the plan's limits, not a bill.

| Run | What ran | Passed | From, to | Cost at list price |
|---|---|---|---|---|
| 1, at `3eff0c3` | all 20 cases, 3 runs each | 60 of 60 | 21:23:48 to 21:42:57 | $8.5603 |
| 2, after the description gained its sentence on mixed units | the three import cases, 3 runs each | 8 of 9 | 22:02:38 to 22:05:43 | $1.4998 |
| 3, after the judge read a waiting phrase as asking | the same three cases | 9 of 9 | 22:19:43 to 22:22:25 | $1.4968 |
| 4, after that rule was narrowed | the same three cases | 9 of 9 | 22:43:34 to 22:47:10 | $1.5015 |
| 5, after the judge went back to the question mark | the same three cases | 9 of 9 | 23:01:40 to 23:05:03 | $1.4817 |

- **The one failure:** run 2, `import_unknown_lift`, its second run, "the reply asks nothing". The reply wrote nothing, named the lift, would not guess and put the choice to the person, with no question mark, which was then the judge's only sign of asking. The server's record was right: `whoami` and `list_exercises`, no import call. It is a false fail of the question-mark check, kept on purpose as a test fixture.
- **The judge of each run.** Runs 1 and 2: the question mark. Run 3: a rule that also read fixed waiting phrases. Run 4: that rule, narrowed. A reviewer found wrong replies that each version passed, so the phrase rule was withdrawn and the judge is the question mark again. Runs 3 and 4 kept no replies, so their counts under it are not known. Run 5: the question mark.
- Every run's handshakes carried the server's instructions: 60 of 60, then 9 of 9 in each of the four later runs.

### Step 4: what was built

**Every page on the new design, 2026-11-11 to 2026-11-13.**

| Group | What |
|---|---|
| Frame | The page frame, titles and tabs, the favicon set. Designed 404 and 500 pages and one message template |
| Today | The page head and Today card, the Food card with its Add food fold-out, the Lifts, Cardio, Weigh-in and Measurements cards, the edit pages for all five kinds, three tiles and their sheets, the last 28 days and the chart pair |
| Progress | Four tiles and their sheets, the Body weight card, the Trends table, the Cardio and Lifts cards. A page for each lift |
| Goals | A card for each open goal with its path chart, closed goals, and the Set a goal form, one kind at a time |
| Foods, Settings | Foods' table and its edit page. Settings in six cards, and Delete my account |
| Landing | The first screen, a sample exchange above a picture of the Goals card, Before you start, the three steps, the list of assistants, Things to say first, Your data, How it is built, a footer |

- Left out on purpose: the page for a cardio activity, the privacy page, the Add to Claude button, a link-preview picture. The contract marks each.
- Beside the pages, `import_entries`, the server's 19th function, was merged in with its three eval cases. The entry above records their runs.

### Step 4: the review

Each pass was read by a fresh reviewer on a frozen copy, who broke the code on purpose and counted the breaks a test caught. Each page group was then looked at in headless Chrome at 360 and 1,280 px, with measurements. Fixes were batched by page group and re-checked.

| Finding | Fix |
|---|---|
| A form low on a phone page: a refused post landed at the top, its error out of sight. Measured in Chrome: a form whose `action` ends in a fragment lands there on a refusal, and a redirect with no fragment inherits it | Each such form posts to its own anchor, and each redirect that answers it names `#top` |
| Settings: the line for "Read and log" left out that the assistant may also save foods, import, and change the time zone and targets | The line says all the setting allows. A test holds each line beside what the server allows at that setting |
| The landing's sample reply broke the server's own reply rules: five facts on a line, no plan line, a pace converted | Rewritten to them |
| The landing's picture held a chat nobody could read on a phone: its text drew under 7 px | It became the Goals card alone |
| Foods' table scrolled a phone sideways by 136 px | A row table that stacks |
| The landing said a deleted entry comes back "when you ask your assistant to restore it". An assistant can restore only an entry whose id it holds | Reworded |
| Two calorie error lines carried an example figure the page's own number rule refused | They say it in words |

### Step 4: exit

| Check | Status |
|---|---|
| The full suite is green, plain and with warnings as errors, on a clean export | 4,806 tests, from 3,286 |
| A browser look at every page, after the cleanup pass (`2e49d9c`) | Headless Chrome at 360 and 1,280 px, 40 page loads on three accounts: sample data, edge cases, nothing logged. Every load answered 200, none scrolled sideways, none wrote a console message. Nothing sat under the phone's bottom bar or the laptop's top bar. A refused post on the day page landed on its own form with its error in view, and a save landed at the top with its line in view. A second look, on the tree of the last two passes (`ccab5df`), opened the seven tile sheets on Today and Progress, the edit pages of the other four kinds of entry, and the landing's new reply and picture, at both widths: no load scrolled sideways |

### What step 4 taught

- **Measure what the browser does.** Where a refused post lands was settled in Chrome, not by reading. A redirect with no fragment of its own inherits the form's, and no reading of the templates showed that.
- **A sample is a promise.** The landing's reply was what a stranger would expect from their assistant, and it broke the rules the server gives the assistant. A test now rebuilds it from `get_goals`.
- **A line about a permission is held against the permission.** The switch's line and the Undo line each said less, or more, than the server allows.
