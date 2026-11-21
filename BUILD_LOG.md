# Build log

**How Spotter is built:** `CLAUDE.md` holds the process. This file records what each stage or item did and what it found out. An entry's process lines say how it was built at the time.

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

The stage lands in four steps. Step 1 was the design, agreed on a static build before any template changed. Step 2 is the number and text rules on today's pages, pushed on its own. Step 3 is the stylesheet, the charts and the new reads, committed locally and pushed with step 4. Step 4 is every page rebuilt on that design.

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

## Before posting online: the safety basics

What one person, or many at once, can do to the server before strangers use it. The decisions are in `docs/contracts/safety.md`.

| Part | What it does |
|---|---|
| Connection pool | 5 connections and 5 more under load, a 10 s wait for one, each recycled after 30 min |
| Postgres limits | Every connection starts with a 15 s statement limit, a 10 s lock wait and a 1 min limit on an idle transaction |
| Busy database | Every time-out, a full pool and an idle transaction cut off all give the same "database busy" answer, on the web and through MCP |
| Read limit | 300 reads per person in 10 min through MCP, beside the write limit of 120 |
| Page limit | 300 page loads per person in 10 min on the web, then a 429 |
| Request size | Every request body is capped at 1 MiB, then a 413 |
| Load test | `tools/load.py`, run only against a local database. It refuses a remote address or a setting that could send it to one |

### The load test

Local Postgres 17, every simulated person calling back to back with no pause.

| Run | Calls a second | p50 | p95 | Errors |
|---|---|---|---|---|
| 10 people | 87 | 111 ms | 168 ms | none |
| 50 people | 94 | 514 ms | 748 ms | none |

A bigger pool (15 and 5) gave 101 calls a second: the limit is the one Python process, not the database. The three Postgres limits were read back from a real connection, and they held after a rollback.

### The review

| Finding | Fix |
|---|---|
| An idle transaction cut off by Postgres gave a 500 on the web and an internal error through MCP | It gives the busy answer like every other time-out |
| A libpq setting in the environment, or `?service=` in the URL, could send the load test to a remote database | The tool refuses both |
| A body over 1 MiB that was already sent in full gets a dropped connection, not the 413 line | Left as it is: the request is refused either way |

## No sample data

The owner's call: Spotter will not be shown to anyone who would press "Add sample data", so it went. The decisions are in `docs/contracts/no-sample.md`.

| Part | What changed |
|---|---|
| Pages | No Add offer, no Remove button, no banner, no sample tags. An empty account's Today says "Nothing logged yet" and "Tell your assistant what you ate, lifted or did, and it shows here." The landing's picture is captioned "An example." |
| Routes | `/settings/sample` and `/settings/sample/remove` are gone and answer as any unknown path does |
| Assistant's results | No `is_sample`, no `includes_sample`, no sample note. Notes that also said something else keep that |
| Import and export | An import is no longer refused over sample rows. The export has no `is_sample` column |
| Database | Migration 0004 deletes every row marked sample (a session's sets with it; a real food entry linked to a sample food keeps its numbers and loses the link), then drops `is_sample` from the seven tables that had it |
| The four weeks | Moved to `evals/four_weeks.py`, a seed for the tests and two eval cases. Nothing in `app/` imports it |

### The review

A reviewer ran the old generator and the new seed on fresh databases for seven fixed days and compared every column of eight tables: identical, 2,821 rows each day. The migration's Postgres SQL was read offline: one transaction, deletes before the column drops. One finding, a choice rather than a fault: between the migration and the new code going live, the old code still serving fails on the dropped column for a few seconds. Accepted: the owner is the only user today. Later migrations that drop a column ship in two steps, code first.

The full suite: 4,835 tests, plain and with warnings as errors.

## Know what happened: the call trace

What the owner can see when a call goes wrong for anyone, without seeing what the person said. The decisions are in `docs/contracts/trace.md`.

| Part | What it does |
|---|---|
| Request id | Every request gets 16 hex characters of Spotter's own making, never taken from the caller. It is on the call's row, on every Spotter log line, and on the response as `X-Request-Id` |
| What was sent | Field names and counts only, such as `date entries[2]{description,kcal,meal}`. A name Spotter does not know is counted, never written. One value is written: which table an edit, a delete or a restore was for |
| Whether it did it | read, written, duplicate, preview, refused or failed. A write that landed before its reply failed reads written |
| Where it stopped | The step, from the error code, and for a crash the file, line and function. Never the message |
| USDA | A failed lookup says why: the key refused, over the limit, a bad request, no answer, a bad reply. The assistant hears a message of its own for the first two |
| Retention | Rows older than 90 days are deleted by the app itself, at most once a day |
| The owner's read | `owner_calls`, one more function, listed only for the account named in `OWNER_USER_ID`. Everyone else's list is the same as before, byte for byte. Unset, nobody is the owner |
| Database | Migration 0005 adds seven nullable columns and one index to the call log |

### The review

| Finding | Fix |
|---|---|
| With "failed only", a call still running was listed as failed, the owner's own reading call among them | A failed call is one that is not ok and has finished |
| A web write that crashed had its row but no Spotter log line with its id | It logs one line, the type and the frames only |
| A 500 from outside the page layer went out with no id | The id layer wraps the whole app |
| Calls with no account, such as a refused sign-in, could be found only by request id | The summary has a line for them, and `no_account` lists them |

A second reviewer read the fix round as new code: it holds. A non-owner's function list, the instructions and the resources were compared with the build before, as sent on the wire: identical. Not run on a real Postgres: the migration and the owner's queries were read and compiled for it, not executed there.

The full suite: 4,878 tests, plain, at the commit. The files touched also passed with warnings as errors.

## A USDA lookup that answers

On the live site, `find_food` said "USDA did not answer" for more than half of first lookups, with a working key. The decisions are in `docs/contracts/usda-steady.md`.

### What was measured, 2026-11-14

| Where | Request | Result |
|---|---|---|
| The live site, through `find_food` | 25 words nobody had looked up before | About 15 said "USDA did not answer". A failed word answered on a second try. One word sent four times at once: three answered, one did not |
| Another network, USDA's public demo key | The live request: a GET with three `dataType` values in the address, 8 words | 3 got "400 Bad Request", an nginx page and not USDA's JSON. Unencoded brackets changed nothing |
| The same | That GET with no `dataType`, 20 words, 6 of them two words long | 20 of 20 answered |
| The build machine, the owner's key, the two forms in turn in one run | The live GET, 20 words | 11 answered. 9 got the same 400 page, each in about 1.1 seconds |
| The same run | The new POST, the filter in a JSON body, 20 words | 20 of 20 answered, each naming the three data types as the filter it applied. Median 2.3 seconds, slowest 3.1 |

So the key is right and the request is well formed. USDA's own front end rejects the search at random when the filter rides in the address, and did not once when it rode in the body. The fix before this one, spaces as `%20` in place of `+`, had met this same random 400.

The contract asked for the last run with the public demo key. That key allows too few requests: one POST with it answered, and the next was over its limit. The run used the owner's key, read from a file by the measuring script, sent in the header only and never printed.

### What changed

| Part | What changed |
|---|---|
| The search | A POST to `/foods/search`: the query, the three data types as a list and the page size in a JSON body. The address is the path alone, so the person's query is no longer in it. The key stays in the `X-Api-Key` header |
| Tries | A try that fails with a 400, a status from 500 to 599 or no answer is tried again at once: three tries at most, and none begun once 8 seconds have gone. A refused key, a spent limit, a redirect and a broken reply are never tried again |
| Food by id | Still a GET, with the same rule |
| A food log | `log_food` stops looking up at the first food that fails. The call is refused on that food anyway |
| The call's row | `usda_tries=N` when a lookup took more than one try: in a call of several lookups, the most any one took. One warning line for each failed try, with its number |
| The assistant | Nothing it reads changed: the same messages and the same fixes |

### The review

| Finding | Fix |
|---|---|
| One `log_food` call can name 30 USDA foods. With three tries each it could make 90 requests, every one after the first failure spent for nothing | It stops at the first lookup that fails |
| A row could pair one lookup's tries with another lookup's reason | The row keeps the most tries of any lookup in the call |
| The food-by-id address ended in a bare `?` | The `?` is written only with something after it |
| A tries note that was not a number would have crashed a lookup after USDA had answered. Nothing writes such a note today | It is read with a guard |

A second reviewer read the fix round as new code: it holds. Over every path tried, the result, the call's row and the log lines held no key, no query and no address. The longest one lookup can take is just under 13 seconds: two tries inside the 8 seconds, then a third of up to 5. Accepted.

Not known until this is live: whether the live site's failures were this 400. The pattern matches, and the call's row will say: `usda=bad_request` for a lookup that failed, `usda_tries` for one that needed more than one try.

The full suite: 4,898 tests, plain, at the commit. The files touched also passed with warnings as errors.

## Safe to send twice: the key

A write that is sent twice is done once. The decisions, and the limits that were accepted, are in `docs/contracts/idempotency.md`.

| Part | What it does |
|---|---|
| The key | Each of the 12 write functions takes an optional `key` of the caller's own: 8 to 64 letters, digits, `-` or `_`. With no key a call runs as before: no statement of the write names the new table |
| The same call again | The same key and the same call writes nothing and gets the first answer back, the same bytes. Its row in the call log reads `repeat` |
| A key used wrongly | Another call under a kept key is `key_reused`. A key of another shape is `invalid_key`. Nothing is written for either |
| Where the key is claimed | Under the person's lock, in the write's own transaction, in the new table `write_key`. So the key exists only if the write committed, and two sends at once cannot both pass. No service was edited for it |
| What keeps no key | A refused call, an import preview and a crash. The same key can be sent again for each |
| A retry during the first send | It waits for the first answer, a look every 100 ms for up to 2 seconds, and gets it |
| A lost answer | A kept key with no answer is `already_done`, with the function that reads the thing back. After the first minute it answers at once |
| How long | 24 hours. An older key is never honoured, and the app deletes old keys at most once an hour |
| The web | 18 of the 21 forms carry a hidden key, new each time the page is drawn. The other three write nothing. A double click saves once. The same form sent again with a field changed is a new write |
| A new goal | The same new goal set again within 10 minutes is not set twice: the first comes back with a warning, and the Goals page says so |
| The trace | `outcome` gains `repeat` and `step` gains `key`. The key's value is in no column and on no log line |
| The privacy page | A card of its own says an answer is kept for a day, and that these answers go with the account and are not in the export |
| Database | Migration 0006 adds the table `write_key`: one row per person and key, deleted with the account |

Three calls were made where the contract was silent, put to the owner and approved the same day: on the web a form sent again with a field changed is a new write, a retry during the first send waits for its answer, and old keys are swept hourly.

### The review

A separate reviewer found no second write, no claim that leaks into another call and no reach into another account. It broke the code 18 times to test the tests: 14 breaks failed a test, 3 changed no behaviour, and 1 showed a missing test.

| Finding | Fix |
|---|---|
| A key whose answer was lost made every later send wait 2 seconds, and the write limit never stopped them | A kept key with no answer and older than 60 seconds is `already_done` at once, before any work. Measured after: 0.015 seconds and one look, against 2.05 seconds and 22 |
| No test held the second run of the work, when the first send is refused while a second waits | A test holds it |
| A failed sweep of old keys cost the call log its own 90-day delete for a day | Each delete has its own transaction |
| The goal guard compared six fields, so the same goal with another text or another start came back as the first | It also compares the text and the start. Only a true re-send is stopped |
| A goal the guard stopped read `written` in the call log | It reads `duplicate` |
| `already_done` named `get_log` for every function | It names the read that shows the thing: `get_goals`, `whoami`, `find_food`, or `get_log` |

A second reviewer read the fix round as new code: clean to commit. Six sends at once through the assistant's path, and six clicks at once on a form, wrote once or not at all. Of 19 more breaks, 16 failed a test, and tests were added for the other 3.

Not run on a real Postgres: two sends at once ran on SQLite only. On Postgres the unique rule on person and key means the worst case is an error, never two writes. The migration's SQL for Postgres was read, not run there.

### The eval run

Every write function's schema gained an argument, which is text a model reads. All 20 cases, 3 runs each, on `claude-opus-5-5`, at `f1bceeb`, from 13:20:22 to 13:34:18 on 2026-11-14: 59 of 60 runs passed, $8.6592 at list price. Every case passed at least 2 of its 3 runs.

The one failure is `import_unknown_lift`, its first run, "the reply asks nothing". The reply wrote nothing, named the lift, would not guess and put the choice to the person, with no question mark. It is the false fail of the question-mark check that the import's own runs recorded, in a case this work does not touch.

The full suite: 5,059 tests, plain, at the commit. Every test file also passed with warnings as errors, in separate runs.

## An edit keeps what it replaces

An edit used to overwrite the row and keep nothing. Now the values from before each edit are kept, and one call or one click puts the last ones back. The decisions, and the limits that were accepted, are in `docs/contracts/edit-history.md`.

| Part | What it does |
|---|---|
| What is kept | Every `edit_entry` that changes something, on the five entry kinds, and every `save_food` that changes a food that exists, keeps the row as it stood: a lift session's sets, a food entry's link to its saved or USDA food, a cardio estimate. It is kept in the edit's own transaction, after every check has passed. An edit that changes nothing keeps nothing |
| Undo | `restore_entry` takes one new optional argument, `undo_edit`. It puts back the values from before the last edit, and keeps what it replaces, so nothing is lost |
| Once | A second undo changes nothing, at any age, and comes back with `duplicate` true. To get an edit back, the entry is edited again |
| A taken day | An undo that would move a weigh-in or a measurement onto a day that holds another one is refused with `day_taken`, as an edit is. Nothing is replaced |
| How much | The newest 10 versions of an entry, each for 90 days. The call log's sweep deletes older ones |
| The web | After an edit lands, the page offers Undo, as it does after a delete. After the undo it says so in a plain line with no button |
| A stale form | Both edit forms carry the entry's `updated_at` as the page drew it. A form older than the entry's last change is refused with `entry_changed`, and nothing is saved. Before this, such a form overwrote the newer change without a word |
| The export | One file more, `entry_versions.csv`: each kept version, with names for what the shared lists hold |
| The trace | `undo_edit` is a known field name and `entry_changed` has its step. A version's row holds the request's id, so an edit and its trace row can be joined |
| The privacy page | A card of its own says what an edit keeps, how many and for how long |
| Database | Migration 0007 adds the table `entry_version`, deleted with the account |

### Built, then cut back

The first build did more. After an undo it offered a way back: a second argument, a third kind of version, a Redo button and a 10-minute rule to tell a retry from a wish. One of the first review's two main findings, and the re-check's first, were about that way back. The owner read the card and asked for less: an immediate undo, and to get an edit back the entry is edited again. A trim round took the way back out the same day, and the second and third rows of the table below went with it.

### The review

Three separate reviewers, each on a frozen copy: the build, the fix round, and the trim. None found a reach into another account or a refusal that writes.

| Finding | Fix |
|---|---|
| On a page, the Undo of an edit could set another weigh-in aside, and the page never said so. No page brings the other one back | An undo never replaces another entry: it is refused with `day_taken`, through the assistant too |
| An undo sent with the way-back argument, twice, undid and then brought the edit back | Gone with the trim: there is no way back, and a second undo changes nothing |
| A late plain undo sent twice turned the entry round | The same |
| The food edit page read its values and its stamp in two reads, so a save between them was overwritten | The stamp is read first. The worst case is a refusal the person did not need |
| Two decisions were pinned by no test | A test for each |
| The export wrote a person's own letters as escapes | They are written as typed |
| An older fault, not of this work: on a page, bringing a deleted weigh-in back onto a day that has another one set that one aside without a word | On a page it is refused, and both stay as they were. Through the assistant the swap is unchanged: its answer names what was set aside |

The re-check of the fix round broke the code 17 times, and each break failed a test. The re-check of the trim found nothing of the way back alive in code or in any text a person or a model reads, and 15 more breaks each failed a test. A last small round made one landing true, Undo pressed again from an old page, and removed leftovers of the way back.

Not run on a real Postgres at this commit: migration 0007 ran up and down on SQLite only.

### The eval run

`restore_entry`'s schema and the can-and-cant resource are text a model reads, and one case was added: a weigh-in edited by mistake, which the person asks to undo. All 21 cases, 3 runs each, on `claude-opus-5-5`, twice on 2026-11-14. At `fde0704`, the build with its fix round: 63 of 63, $9.0927 at list price. After the trim, at `d890103`, from 17:36:49 to 17:51:43: 63 of 63, $9.3467.

The full suite: 5,172 tests, plain, at `d890103` and again at `a04b909`, the last round. The files of the last round also passed with warnings as errors, in separate runs.

## No cap on new accounts

New accounts were capped at 50 a day unless `SIGNUPS_PER_DAY` said otherwise. The owner asked for the cap to go. The change to decision 7 is written into `docs/contracts/safety.md`.

| Part | What it does |
|---|---|
| No cap unless one is set | `SIGNUPS_PER_DAY` unset or blank: every first sign-in gets an account, and today's accounts are not counted |
| The dial stays | A whole number of 1 or more caps new accounts for a UTC day, as before. 0 closes sign-ups, as before |
| A value that cannot be read | It closes sign-ups. The variable is only ever set to hold sign-ups back, so a slip of the keys must not open the door |
| The landing | With no cap, "Before you start" has its two lines and no cap line. With a cap, or at 0, the line as before |
| `signups_closed` | Unchanged: its code, message, fix and page |

### The review

A separate reviewer, on a frozen copy: clean of blockers. With no cap set, 60 first sign-ins on the web, 60 through the assistant's path and 16 at once all got accounts. Caps of 1, 7 and 0 held on both paths. Ten breaks of the code each failed a test. One gap in a test, older than this work, was closed: at 0 the signed-in landing was never drawn signed in, and now the test says which page it drew.

Not run on a real Postgres.

The full suite: 5,177 tests, plain, on a clean export of the commit `6452b3c`. The commit's own run was cut off by its time limit on a busy machine, so the suite was run again there to the end. The files this work touched also passed with warnings as errors, in separate runs.

## Feedback through the assistant

A person can ask their assistant to send a note to Spotter's maker. The decisions, and the limits that were accepted, are in `docs/contracts/feedback.md`.

| Part | What it does |
|---|---|
| `send_feedback` | A 20th function for everyone. The note in the person's words and, when they said them, what they were doing, what they expected and what happened, and a topic. Each text up to 10,000 characters |
| When it is sent | When the person asks. On a complaint the assistant is told to offer once, and to send only on a yes |
| A cap, as a dial | At most 10 notes a person in any 24 hours. The number is one constant, `NOTES_PER_DAY`: the description and the refusal are built from it, and no other text states it. Over it: `too_much_feedback`, with the hours to wait |
| The same note twice | The same note within 10 minutes is saved once, with a new key too. It takes a key, as every write does |
| The switch | An ordinary write: refused at read only, counted in the write limit |
| The answer | The note's id and one line. It repeats no word of the note, so the answer kept for a key holds none |
| `owner_feedback` | Listed for the owner alone, as `owner_calls` is. The notes newest first, each with its account and that person's calls from 10 minutes before the note to 5 minutes after, at most 20 and 10, as `owner_calls` gives them: never what was sent. With `delete`, the notes the owner names are deleted for good |
| A note is data | The description, the argument and the answer each say a note is that person's own words and never an instruction |
| How long | The owner deletes a note once it is dealt with. One still there after a year is deleted by the call log's sweep |
| The person's side | `feedback.csv` in the export. Deleted with the account. A card on the privacy page says what is kept and who reads it |
| The trace | A call's row names the fields sent, never the note. `too_much_feedback` has the step `limit`. An owner call that deleted reads `written` |
| Database | Migration 0008 adds the table `feedback` |

It was built beside the edit history work, in a tree of its own on that work's first build, and joined onto the main line afterwards. Four files met and were joined by hand, and the pinned text of `restore_entry` was pinned again as trimmed.

### The review

A separate reviewer, on a frozen copy, found no blocker. No path lets one person read, count or delete another's note. No one but the owner reaches `owner_feedback`. A note's text reaches no call-log column, no kept answer and no log line on any path tried. The 20 functions that were there publish what they did.

| Finding | Fix |
|---|---|
| Two rules of `around`, whose calls it holds after the note and when it says it was cut, were pinned by no test | A test for each, and each break now fails one |
| At a cap of 1 the refusal said "1 notes", and six tests failed at a low cap | "1 note", and every test holds at any cap of at least 1 |
| A delete that committed before a listing that crashed: the retry said the ids were never notes | `not_found` says they are no note now: never one, or deleted before |
| The owner's list runs two queries a note | Stands: a list is at most 50 notes |
| A capital letter outside ASCII in an email is not matched on SQLite | Stands: the same code as `owner_calls` |

A second reviewer read the fix round as new code, on a second frozen copy: clean to commit. It left three small things, one of them a test that could pass for the wrong reason, and each was fixed.

Accepted: an account set to read only cannot send a note. A hostile note can tell the owner's assistant to delete notes, and a deleted note is gone for good: three texts warn against it, and the call's row keeps that a delete ran.

Not run on a real Postgres: two sends at once ran on SQLite only. The evals were not run for this work alone: its two cases, `feedback_sent` and `feedback_offered`, make 23, and they run with the next work that changes what a model reads.

The full suite: 5,247 tests, plain, at the commit `3e135b4`. Before the join, every test file also passed with warnings as errors, in separate runs.

## On a real Postgres

The key, the edit history and feedback had run on SQLite only. On 2026-11-14 the tree at `a7ba6d3` ran against Postgres 17.12, in a throwaway container on the build machine.

| Check | Result |
|---|---|
| Migrations from empty to head | 0001 to 0008 ran, and the models match the database |
| Down to the live revision and up again | 0008, 0007 and 0006 each came down and went up again, one at a time, over a person's row written at 0005. The row was still there, and the models matched again |
| The key's tests | `tests/test_idempotency.py`: 164 passed, two sends at once and a retry during the first send among them |
| The edit history's and feedback's tests | `tests/test_edit_history.py` and `tests/test_feedback.py`: 169 passed |
| The write path around them | Nine more files, the ones for users, the write limit, entries, isolation, the trace, the call log, the export and MCP: 624 passed |

How: a copy of the tree, with the tests' database address pointed at that Postgres and each test starting from truncated tables with their id counters set back. Nothing in the repository changed for it. 957 tests in all, with no failure.

It is one machine's Postgres, not the host's. It shows that the SQL, the locks and the migrations hold there. It does not show how the host behaves under load.

## A page for a new person

A new person's assistant hands them one link in its first reply. The decisions, and the limits that were accepted, are in `docs/contracts/hi-there.md`.

| Part | What it does |
|---|---|
| The page | `/hi`, "Hi there", open with no sign-in. Four parts: what to say to the assistant, what Spotter keeps, how to bring history over from another app, and where to go next. The person fills in nothing on it |
| Nothing typed twice | Each fact is drawn from the code's own lists: 41 lifts, 15 cardio activities, 9 measurements, the meals and the 4 kinds of goal. The example sentences are the landing's own |
| A visitor costs nothing | A visitor with no session reads no database, and a load of the page uses none of a person's page loads |
| The note | While nothing is logged on an account, `whoami` carries `new_account_note` and `links.hi_there`. The assistant is told to give the link once, last in its reply. The note goes after the first entry, saved food or open goal |
| One rule for "new" | Today's empty state and the note ask the same helper |
| The feedback line | The page says how to send feedback while `send_feedback` is offered, read at each render |
| What an assistant reads | One sentence more in `whoami`'s description, two result fields and a section of the can-and-cant resource. The instructions did not change |

It was built beside the feedback work, in a tree of its own, and joined onto the main line after it. Five files met and were joined by hand, and two pins in the feedback tests were moved: the published text of `whoami` and the count of eval cases.

### The review

A separate reviewer, on a frozen copy: sound. No defect in sign-in, sessions, the form token, one person's data, the page limit or the note. In 31 states of an account the note agreed with Today's empty state. It made 23 breaks of the code to test the tests.

| Finding | Fix |
|---|---|
| The landing sent nine extra lines of spaces | It sends what it sent before, but for the count of eval cases |
| The test of the route tables could not fail | Both tables are written out row by row |
| The page said "It writes only when you say yes.", which the server cannot hold an assistant to | "Your assistant is told to write only when you say yes." |

A second reviewer read the fix round as new code: clean to join and commit. Three small things, two of them tests that pinned less than they claimed, were fixed in one more round.

Accepted: "once" is not enforced, so a person who logs nothing gets the link in each new conversation. A conversation begun before the deploy, on an empty account, may have `whoami` refused by a client that holds the old schema. The page counts the code's list, not the live table.

Not checked: a real browser, so the look of the page has not been seen. Not run on a real Postgres.

### The eval run, for feedback and this page

Both pieces of work change what a model reads, and `whoami` opens every conversation, so the whole set ran once for the two. All 25 cases, 3 runs each, on `claude-opus-5-5`, at `d108df5`, from 21:16:03 to 21:36:55 on 2026-11-14: 75 of 75 runs passed, $11.6883 at list price. Every case passed all 3 of its runs, the four new ones among them: `feedback_sent`, `feedback_offered`, `hi_there_first_log` and `hi_there_not_again`.

The full suite: 5,308 tests, plain, at the commit `d108df5`. Before the join, every test file also passed with warnings as errors, in separate runs.

## A read that only reads

The owner's `owner_feedback` both listed the notes and, with `delete`, deleted them. So it was marked as a delete, and an assistant app may ask its person to confirm a call so marked: the owner would have been asked before a plain read. He would not have it: "If they want to make a deletion of something, then only should we ask permissions, right?" The change is written into `docs/contracts/feedback.md`.

| Part | What it does |
|---|---|
| `owner_feedback` | Only reads. `delete` is gone from it, and it is marked read only, as `owner_calls` is |
| `owner_delete_feedback` | New, for the owner alone. It deletes for good the notes whose ids it is given, 1 to 100, and is marked as a delete. It answers what was deleted, which ids were no note, and one line to quote |
| Everyone else | Nothing changes. The list sent to a person who is not the owner is the same bytes as before |
| The trace | A delete that deleted reads `written`. `ids` is a known name, and `delete` no longer is |

It was found before the feedback work went live, so no one ever held the function in its first shape.

### The review

A separate reviewer, on a frozen copy: clean of blockers. No one but the owner reaches either function, at any setting of the switch or with no owner set. The read deletes nothing whatever is sent to it, an old client's `delete` among it, and is published as read only. The list a person who is not the owner is sent is the parent's 142,762 bytes. A deleted note is in no list, no export and no count of its sender's cap. Sixteen breaks of the code each failed a test.

One finding, older than this work: two deletes of the same note at the same moment both answered that they had deleted it. The delete is now one statement that reads back what it removed, so one call names the note as deleted and the other as no note, and a test holds two calls at a barrier to show it.

Accepted, and the same before this work: a badly shaped call from a person who is not the owner gets the library's own error before the owner check, which shows that the function exists and nothing more.

The full suite: 5,314 tests, plain, on the tree of the commit `e982ba3`. The files this work touched also passed with warnings as errors, in separate runs. Not run on a real Postgres. No eval run: no text that anyone but the owner is sent has changed.

## Two settings, and a delete that asks first

The switch for what an assistant may do had three settings and started at "Read and log". The owner called read only useless and asked for deleting to be on from the start, with one more confirmation from the person. The decisions, and the limits that were accepted, are in `docs/contracts/switch.md`.

| Part | What it does |
|---|---|
| Two settings | "Read, log and delete", which every account starts with, and "Read and log", which turns deleting off. Read only is gone from the code, the pages and every text |
| A delete takes two calls | `delete_entry` with no `confirm` deletes nothing: it answers with the entry as it stands and a `confirm` code. The same kind and id with that code deletes. One call can no longer delete |
| The code | The first 12 hex characters of a SHA-256 over the account, the kind, the id and the entry's last change. It is worked out each time and never stored. Any change of the entry makes an older code stale: it deletes nothing and answers with the preview again |
| What the assistant is told | To show the person the entry and ask, and to delete only on a yes. When a person did not want something deleted, to restore it and say deleting can be turned off on Settings |
| The pages | Their own deletes are unchanged: a click on a page was already the person's own act |
| The accounts that exist | Migration 0009 moves every one to "Read, log and delete", on the owner's word, in one statement that touches no other column. Its downgrade changes no row: nothing records what a row held |
| The trace and the key | A delete's first call reads `preview` and keeps no key. The second reads `written` and keeps its key, so the same delete sent twice runs once |
| Feedback | With read only gone, every account can send a note. The privacy page and the can-and-cant resource say so |

### The review

A separate reviewer, on a frozen copy: clean of blockers. No path deletes in one call. No preview writes anything. No code outlives a change of its entry. No person reaches another's entry.

| Finding | Fix |
|---|---|
| A function missing from the table of levels is refused only at "Read and log", and two comments said it was always refused | The comments say what is true, and a test holds it. The server already refuses to start when a function has no level |
| A row still holding a word outside the two settings is gated as "Read and log", while Settings checks no button for it | Stands, as a known limit. The migration leaves no such row |
| One assertion checked a marker that was never sent | Removed |

A second reviewer read the fix round as new code: it holds. One sentence in the evals doc was made exact.

Accepted: Spotter cannot see the person, so an assistant that makes both calls with no person between them still deletes. What Spotter holds is that one call never deletes and that the assistant was shown the entry and told to ask. The code is not a secret and has no end date. A conversation begun before the deploy holds the old shape of `delete_entry`, and its first delete now deletes nothing.

On a real Postgres, 17.12 in a throwaway container on the build machine: migration 0009 moved three accounts, written at the live revision with read only, read and log, and delete on, to delete on. It moved no `updated_at`, and going down and up again changed no row. A new account starts with deleting on. Of 400 tests of this work and its neighbours run there, 391 passed. The 9 that failed compare a statement with SQLite's own lock on the person's row, which Postgres takes with another statement. Six of them are this work's test that a preview writes nothing but the lock: on Postgres the preview sends no write at all, and the checks before that one passed.

### The eval run

The instructions changed, and every conversation reads them, so the whole set ran. `delete_explicit` became `delete_asks_first`: asked to delete a weigh-in, the model must make the first call, send no `confirm`, and ask. `delete_regretted` is new: told that a delete was not wanted, the model deletes nothing and names Settings.

All 26 cases, 3 runs each, on `claude-opus-5-5`, at `97e634c`, from 00:48:24 to 01:19:45 on 2026-11-15: 77 of 78 runs passed, $12.7761 at list price. The one failure is `delete_regretted`, its third run: the CLI hit the runner's limit of 180 seconds and printed no reply. It had called `whoami` and `get_log`, and never `delete_entry`. That case and `delete_asks_first` were then run again, 3 runs each: 6 of 6, $2.5368. `delete_regretted` is slow, 40 to 152 seconds in the runs that passed: a new conversation looks for the deleted entry, and no read lists deleted ones.

The full suite: 5,377 tests, plain, at the commit `97e634c`. Before the join, every test file also passed with warnings as errors, in separate runs.

## A day's sets in one session, a setup page, and routines

Three items, built and reviewed one after another on 2026-11-15, and released together. Their decisions and limits are in `docs/contracts/gym-logging.md`, `docs/contracts/first-day.md` and `docs/contracts/routines.md`.

| Item | What it does |
|---|---|
| Gym logging | Every `log_lifts` call for a date adds its sets to that date's session, so a workout said set by set is one session. `new_session` starts a second workout that day. Lifts have no duplicate question. An add is kept first as an edit, so the one-step undo takes the last add back |
| First day | `/hi` is the setup page: say "Hi Spotter, help me set up", your first day in order, what you can say, what Spotter works out and how, what it keeps, your say. `whoami` carries `setup`: the steps an account still lacks, in order, walked one question at a time, only when asked or once on a new account |
| Routines | `save_routine` keeps a workout by name, `get_routines` gives each lift the weight to log: the latest session's, unless the routine was saved on a later day. Logging one is `log_lifts`. A Routines page with Delete and Undo. Migration 0010 |

### The reviews

Each item had one builder and one separate reviewer on frozen copies, then one fix round. Gym logging's review was clean of blockers, with two test gaps closed. First day's review had nothing that must be fixed: one eval case was added (setup stays quiet when the person only logs) and four lines of wording were made exact. Routines' review was clean of blockers: the weight rule's words were made to say "on a later day", as the code compares days, and a name is stored with its spaces folded. Each fix round was re-checked by a fresh reviewer and held. First day's fix round still left one test red, and it was made right by hand at the join.

The three were joined in order. At the last join the setup step for routines was wired: it is done when the person holds a live routine, and the setup field names it.

### Checks

On a real Postgres, 17 in a throwaway container: migration 0010 went up, down and up again; the constraint names, the cascade on account delete and the unique name per person held. Of 181 tests of routines, the export and the switch run there, 172 passed; the 9 that failed compare a statement with SQLite's own text, and the statements Postgres ran lock the row first and name the person's id.

The whole suite on the joined tree: 5,599 tests, in groups, and again by the commit hook.

### The eval run

All 35 cases, 3 runs each, on `claude-opus-5-5`, at `52b0e76`, from 22:07 to 22:49 on 2026-11-15: 105 of 105 runs passed, $16.8061 at list price. Among them the six new cases: a workout in one sentence, a set added to a started session, a lift added later, setup asked for, setup picked up, and setup staying quiet when the person only logs; and routines' three: one saved from a sentence, one logged, one logged with a changed weight.

## Units, input checks and the tidy-ups

Built and reviewed one after another on 2026-11-15 and 2026-11-16 on one branch, and released together. Their decisions and limits are in `docs/contracts/tidy.md`, `docs/contracts/units.md` and `docs/contracts/inputs.md`. Before them, the build system: `python -m evals.need` says whether a change owes an eval run, and `CLAUDE.md` says how the repo is built.

| Item | What it does |
|---|---|
| Tidy-ups | The tests that compared SQLite's statement text hold on Postgres too. Each count of functions and cases is drawn from the code. The landing says what each assistant needs. A private contact address, once set, is the Contact link |
| Units | A person can keep weights in lb and distances in miles, set through the assistant or on Settings. Every answer, page and chart is in their units; storage stays kg and km. Migration 0011 |
| Input checks | Every input is checked as sent. An unknown field is refused with the field meant. True and false are never numbers. A range is checked before rounding. A number sent as text has one form, a date one form (YYYY-MM-DD). A goal's date is at most 5 years on. Two crashes are gone. A refusal from the function list's own check is Spotter's, with its fix |

### The reviews

Each item had one builder and one separate reviewer on frozen copies, then one fix round. The tidy-ups' review found two things, fixed in one commit: a test's exemption for the landing's steps covered more than its one sentence, and the privacy page's Contact card lost the issues link once an address was set. Units U1 had nothing that must be fixed: two should-fixes and three lows were fixed. Units U2 had two must-fixes, each re-checked by a fresh reviewer: a page figure one step off the assistant's summary (262.6 against 262.5 lb), and a lb lift goal's "so far"; pages now take the services' unrounded figures. Input checks had nothing that must be fixed. Its fix round closed four gaps the builder found outside its files (a routine's weight rounded before its range check, a food's servings or grams dropped when no food is named, a feedback note of only invisible characters, a second "Nothing to change" line beside a refused goal edit) and five lows.

### Checks

On a real Postgres, 17 in a throwaway container, at `c6ddf59`: migration 0011 went up, down and up again over a written row, and the models match the database. Of 1,329 tests in 17 files, 1,328 passed; the one, a count of statements, was by design: Postgres sends a session's new sets as one insert. Its test now counts per engine, and passed 42 of 42 there.

The whole suite ran in the commit hook on each code commit. On `194e7f0` the hook was cut off at its time limit; the suite run on a clean export of that commit passed 5,794 tests, with 15 errors in `test_eval_need`, which needs a `.git` an export does not have.

### Known limits

- The contact address check lets some odd shapes through; it only decides whether a link is shown.
- A mile account's refusal of a minutes-only cardio edit still names its speed bound in km/h.
- No page shows a best time's name yet.

### The eval run

All 43 cases, 3 runs each, on `claude-opus-5-5`, at `d4e9400`, from 16:38 to 17:24 on 2026-11-16: 129 of 129 runs passed, $21.0332 at list price. Among them the new cases: units' six (a weigh-in in lb, lifts in lb, a run in miles, the units question after a first lb log, a switch of units, a routine changed in lb) and input checks' two (a waist given in inches, and a machine's calories).

## Data on request

Built on 2026-11-16 and 2026-11-17 on one branch, in two packages, and released together. Its decisions and limits are in `docs/contracts/data-on-request.md`, with the build's own notes at its end.

| Package | What it does |
|---|---|
| D1 | Two reads. `what_is_logged` says what the person has logged: each area's fields and what they mean, dates, counts, the names used, and the open goals. `get_data` answers one question over one area: fields, filters, a date range, groups, and totals (sum, average, highest, lowest, count, per day, share) worked out by Spotter, in the person's units. Eight areas, one field list tied to the models by a test. Body region and weighted muscles for the 41 lifts, from free-exercise-db. The open goals and today's amounts ride beside the data. A cursor with no state on the server, and a 30,000 character ceiling an answer. No migration |
| D2 | A 16-week seed and 8 eval cases for open questions: the owner's three (basketball, lifting, calories), squats with their goal, getting back on track, a lb account, a kind never logged, and a year of weigh-ins |

### The reviews

The contract was read against the code, reconciled after the owner's later changes and reviewed twice before the build. Each package had one builder and one separate reviewer on a frozen copy, then one fix round. D1's review had one must fix, re-checked by a fresh reviewer: a range total of a measurement's value over several kinds mixed units, and is now empty. Its other fixes: at most 10 goals ride along and a page always keeps a row, a cursor longer than 200 characters is refused, a measurement's share needs one kind, a test bounds the statements an open lift goal adds, and the saved food join also checks the owner. D2's review found cases that passed or failed for the wrong reason: a figure matching inside a longer one, and sleep patterns that caught general advice. Both were fixed.

### Checks

The whole suite ran in the commit hook on D1 and on the fix round. On D2 the hook ran only the docs tests, because the files were staged in the same command as the commit; the whole suite on a clean export of that commit passed 6,098 tests, and `test_eval_need` passed 19 in the tree. On a real Postgres, 17.12 in a throwaway container, at the D2 commit: `tests/test_data.py` passed 86 of 86 and `tests/test_sixteen_weeks.py` 39 of 39. Timed on SQLite over a year of data in every area: `get_data` 99 ms, `what_is_logged` 111 ms.

### Known limits

- Paging is by offset: an entry logged or deleted between two pages can move a row across the page edge.
- Totals are worked out in Python over the area's rows, not in the database. Fast at a year of data; to revisit as a person's history grows.
- The data cases judge a reply by the figures they list. A right reply that quotes another of Spotter's figures fails.
- Some runs' model line names `claude-fable-5-2` beside the pinned `claude-opus-5-5`. Why is not checked. (2026-11-17: it was the advisor, 11 of 153 runs; off in every run since `2718899`, as `docs/evals.md` says.)

### The eval run

All 51 cases, 3 runs each, on `claude-opus-5-5`, at `e6174f7`, from 08:01 to 09:18 on 2026-11-17: 144 of 153 runs passed, $35.2311 at list price. In every answered run the reply quoted Spotter's figures right. The misses: two data cases asked for one route where another read gave the right figures, two runs were cut off at 180 s after 10 to 15 right calls, one checker read an example of what to say as a made-up sleep figure, and one older case missed once. The fix round (`6529133`) judges the data cases on Spotter's figures and a read of Spotter's data, and gives each run 300 s. The six cases again, 3 runs each, from 11:29 to 11:53: 17 of 18, $11.8049. The miss was a right reply quoting figures from `get_trends`, outside the case's list. Released on the owner's word with that one miss.

## Hardening

Built on 2026-11-17 on one branch, in two packages, and released in two deploys. It came from an outside review of the database, whose verdict was to keep Postgres and the relational design and make a small request do a small amount of work. Its decisions and limits are in `docs/contracts/hardening.md`, with the build's notes and the Postgres run at its end.

| Package | What it does |
|---|---|
| H1 | No migration. A delete's switch is checked again under the row lock. The export runs one at a time per person, at most 5 an hour and 2 at once, and reads one moment on Postgres. `get_log` reads only the days asked, and every lift session up to them for its bests. The lift history, routines' weights and the goal series work in one pass, and each page works the history out once. The retention sweep runs beside the calls, in batches. A set list is replaced old first |
| H2 | Migration `0012`. A sign and a ceiling on every number a person's row holds, the closed value lists, the goal shape rules, one set number per session and lift, a food entry's saved food tied to the entry's owner (checked at commit), a saved food key wide enough for any casefolded name, three indexes for the sweep, a 64-bit call-log id on Postgres, and `/ready` beside `/health`. The migration counts rule-breaking rows first and stops with counts only. SQLite migrations run with foreign keys off and check them after |

### The reviews

The contract was reviewed, fixed and re-checked before the build, with two must fixes: a statement count pinned by the routines tests, and the order of the Postgres steps. The owner said yes to its six picks. H1's review had no must fix, one should fix (a page test had lost its closed goals check) and two nits (a dead stand-in, and today's page giving 404 for one render at the person's midnight), all fixed in one round. H2 was reviewed on a copy joined with H1: no must fix in its files and four nits, two fixed at the join. The join changed 13 tests in 8 files: head pins, `compare_metadata` lists, and rows the new rules refuse. H2's builder found and fixed two faults of its own: a missing cardio `logged_by` rule, and a key check that committed.

### Checks

The whole suite ran in the commit hook on H1 (`4ddee93`), H2 (`bc5bb71`) and the Postgres run's fix (`9bd6518`). On a real Postgres, 17 in a throwaway container, at `bc5bb71`: up, down and up over rows written by the old code; every rule in place; NaN and Infinity refused; another person's saved food refused at commit; the downgrade's three stops; a delete waited on the row lock, then got `not_allowed`; an export saw the state from before a set added during it. The service, schema and page test files run there passed 1,719 of 1,720: the one was a test's own goal kind, longer than its column, fixed in `9bd6518`. Timed on Postgres: `history` at 2,000 sessions 0.212 s (4.576 s before), `get_log` of a day 600 days back 0.136 s (4.648 s), and a sweep of 100,000 old call-log rows done in three runs. `python -m evals.need main` said no for each deploy, so no eval run.

### Release

H1 was live at 20:27 and H2 at 20:32 on 2026-11-17, each on the owner's word. The export check before H2 was skipped on his word: his is the only account, and it has nothing logged. After each deploy: `/health` 200, `/mcp` 401, `/` 200, the probe with no failures, and a fresh assistant session. After H2, `/ready` 200: the database is at `0012`. Railway's healthcheck then moved to `/ready` on the owner's click and deploy at 20:46, and `/health` and `/ready` answered 200 after.

### Known limits

- `get_log` of an old day still reads every lift session up to that day, for its bests.
- On `env.py`'s passed-in connection path, a SQLite rebuild runs with foreign keys on and takes every lift set, and the check after cannot see it. Nothing in the repo uses that path.
- Not run: whether a reader holding a later table before `"user"` would deadlock with a migration like `0012`.
- Left for later, as the review placed them: `get_data` and `what_is_logged` in SQL with paging by key, the write lock holding back the call log's insert, and the order of an account delete's two steps.

## The evals rebuilt, the advisor off, and the golden cases

On 2026-11-17. Eval code and cases. No migration and no model-read text. Written on 2026-11-18, when the entry was found missing; read from the commits.

| Commit | What it did |
|---|---|
| `2718899` | The advisor is off in every run (`CLAUDE_CODE_DISABLE_ADVISOR_TOOL`, and an empty `advisorModel` as a second layer). A run in which another model took part fails, so a run measures the model asked for, alone |
| `fd83753` | B6 G1. The judge reads Spotter's own answers: a figure counts only if a data read of that run gave it. Every case carries must, may and must_not call tags, with a coverage test. `--client codex` and `--client gemini` (Gemini paced to the free tier). Calls, tokens and cost per case. Each model's default reasoning effort pinned. One failing run no longer ends the run |
| `08ebc11` | The golden package: 164 reviewed golden cases (direct, incidental, look-alike, catch-up and import; every function at its floor), a core of 101 run with `--core`, and `tests/test_golden_cases.py` giving each case a passing and a failing run. Committed as built: a fresh review found the judge passes many wrong runs where it reads the reply's words, so by the owner's ruling it is rebuilt to judge the record before any baseline run (`docs/contracts/function-list.md`, the note of 2026-11-17) |

## The domain move and the README

On 2026-11-18. Docs, and the landing's list of assistants. No migration and no model-read text.

| Part | What it does |
|---|---|
| The domain move | The site, its DNS and sign-in are live at `spottermcp.com`. Checked from outside that day: `/health`, `/ready` and `/` 200, `/mcp` 401 with its sign-in details on the new address. The old Railway address still answers, and its 401 points to the same details. What was not checked is in the dated note at the end of `docs/domain-move.md` |
| The README | The new address, the setup in three steps (add, sign in, the starter prompt), a row for each app whose own docs say it takes a remote MCP server with sign-in, the commands and files for the tools set up that way, a starter prompt that opens with the line `/hi` gives, and a shorter What to say. Each row was read against its vendor's page that day. The landing's list of assistants follows the table |
| The tests | The commit hook is off for now. Before a push, the tests of the touched files run plain and with `-W error`, then the whole suite once on a clean export of the commit. `CLAUDE.md` says so |

## The exercise list

On 2026-11-19 and 2026-11-20. Contract `docs/contracts/exercise-list.md`. Two migrations, model-read text, and eval code and cases. Built in two deploys.

| Package | Commit | What it did |
|---|---|---|
| C4a.1 | `16ec2bc`, `b6a42e8` | Deploy 1, migration `0013`: each exercise gets a stable key and a retired mark, synced by key; a lift set gains `time_s` and `distance_m`, `reps` nullable, and the weight rule widens for help. `SetOut.reps` optional ahead of deploy 2 |
| C4a.2 | `e96d310` | The list: 267 rows, 242 lifts in 153 families, 25 cardio, 61 plain words, 769 aliases, muscles at 1 and 0.5 |
| C4b | `9181498` | Sets of reps, a hold's time, or a carry's distance and time, by what each lift records; reps 1 to 100; help -200 to 0 kg; routines, goals and imports refuse lifts of time or distance; migration `0014` deletes every lift set, session, their versions and `log_lifts` keys once |
| C4c | `69cb6cd` | One matcher: a lift in the person's own words; a plain word takes the usual lift, or the one the person logged that fits the set, and asks when two fit |
| C4d | `8468451` | `list_exercises` takes the account and a search; equipment in plain words |
| C4e | `7546c94` | Volume by load type and sides; bests by kind (least help, reps, longest hold at a weight, heaviest load over a distance); `get_data` gains `time_s`, `distance_m`, `family`; a retired lift's page reads |
| C4f | `12ad791` | The per-hand check: one question when a pair's total is above the bar best; reps, time and distance a side on one-side lifts |
| C4g | `deb7fe5` | 23 new and 16 changed eval cases; the judge reads written rows and two-turn cases |
| Notes | `b47edef` | The dated notes owed on six contracts |
| From an outside review | `1f07a35`, `f1e01b3` | A lift goal refused on band and assisted lifts; `get_progress` bests carry their words; the per-hand words name a pair; the Goals page offers only lifts a goal takes |
| From the release run | `bbfab68` | `set_goal` takes a plain word by history |
| Eval fixes | `33a36d5`, `1238c9d`, `0c1d760`, `7ecf58c` | Judge fixes from the reviews and the runs; the claim patterns kept strict |

### The reviews

Each package had a reviewer on a frozen copy and one fix round. Must fixes: C4c (the history step moved to a lift whose numbers meant something else; the owner ruled A on 2026-11-19), C4f (an lb pair at the bar best asked), C4g (a two-turn case passed a guess). Each had a fresh re-check, C4c's by a Fable agent over 5,460 cases, C4f's over 730. An outside review and a Fable second opinion on 2026-11-20 found six more points, all fixed; `0014`'s down stays a no-op as the contract decides.

### Checks

The whole suite on a clean export: 6,604 at `b6a42e8`, 6,956 at `b47edef`, 6,966 at `f1e01b3`, and 6,987 at `7ecf58c`. Postgres 17 in a throwaway container: `0013` on 2026-11-19 (up, down, up over rows by the old code; the down stops); `0014` on 2026-11-20 at `b47edef` ("0014 deleted: 12 lift sets, 6 lift sessions, 2 versions, 4 keys", every other table identical row for row, down and up again a no-op, the new reads on Postgres, the old code over the emptied tables). Docker stopped after each.

### Eval runs

On `claude-opus-5-5`, 3 runs a case. The 38 new and changed cases: 104 of 114, $18.4874; every failure read by hand was a judge fault. 26 cases after the fixes: 62 of 78, $13.1818: `lb_bench_goal` 0 of 3 was a real regression (fixed in `bbfab68`), `same_again_no_context` 0 of 3 fails on the live code too, the rest judge faults or known strict fails. Two cases on the live code: $1.0399. The last three cases (`lb_bench_goal`, `pick_lift_answer`, `hand_check_family`): 9 of 9, $1.8739. In all, $34.583.

### Release

Deploy 1 (`b6a42e8`) live at 06:45 UTC on 2026-11-19 on the owner's word: `/ready` 503 for about 17 s while `0013` ran, then 200; `/health`, `/` 200, `/mcp` 401. Deploy 2: see the commit that merges this entry.

### Known limits

- The claim patterns are strict: a run failing only on a negated claim ("I haven't logged it yet") is read by hand.
- `same_again_no_context` fails on the live code and on this branch: the model logs another set rather than asking.
- An import or an edit written after the first bar set stops the per-hand ask, though it runs no check.
- The closest-name search takes about 16 s for 1,000 unknown names in an import.
- A downgrade past `0014` then an upgrade would delete every lift logged after deploy 2.

## Sleep, steps, resting heart rate and water in the instructions

On 2026-11-20. No contract: a one-sentence fix of a gap on the live list, found by a comparison run on `gpt-6-luna`. Model-read text: the instructions. A dated note on `docs/contracts/stage-5.md`.

| Package | Commit | What it did |
|---|---|---|
| The fix | `3e955d1` | The instructions gain "Log sleep, steps, resting heart rate and water with log_measurement." after the sentence on lift names, and their Goals sentence takes a short form that keeps "the next such day after today": 4,090 characters. `MONTH_END_RULE` stays word for word in `set_goal`'s description and `target_date` |

### Why

On `gpt-6-luna`, "slept 6.25 hours last night. record that for today" was logged 5 times in 12 on the live code. Every miss called only `whoami` and said Spotter has no sleep log.

### What was tried

On `gpt-6-luna`, on scratch copies of the live code, before the build.

| Try | Sleep, of 12 | What else moved |
|---|---|---|
| A sentence in `log_measurement`'s description | 3 | Not shipped: the model decides before it reads the description |
| The sentence in the instructions before the lift sentence, the month-end sentence dropped | 12 | Goals: 3 of 6 runs of `goal_date` and `lb_bench_goal` asked which year, where 6 of 6 set the goal live |
| The same, with the short Goals sentence | 12, measured after the build | Goals 24 of 24 against 22 of 24 live; "deadlifts ... then bench ..." went in two calls in 4 of 9 runs against 0 of 9 live, the record right each time |
| The sentence after the lift sentence, with the short Goals sentence: shipped | 12 | Pulse 6 of 6, the two-lift case 5 of 6, `word_named_equipment` 6 of 6 |

### Checks

The 15 test files the change touches, plain and with `-W error`. The whole suite on a clean export of `3e955d1`: 6,988 passed.

### Eval runs

On the built commit.

| Model | Cases | Result | Cost |
|---|---|---|---|
| `claude-sonnet-5-5` | the 28 cases it touches, 2 runs | 48 of 52; the two misses miss the same way on the live code | $4.6104, and $0.3066 for the live check |
| `gpt-6-luna` | the same 28, 3 runs | sleep 3 of 3, pulse 3 of 3, 57 of 78 in all | plan usage |
| `gpt-6-luna` | six cases with a miss, 6 runs, beside the live code | `goal_date` 6 and 6, `waist_in_inches` 6 and 5, `lb_bench_goal` 5 and 5, `unknown_lift_close` 3 and 3; `undo_waist_then_water` 4 and 6, `read_plain_word` 3 and 6 | plan usage |
| `gpt-6-luna` | those last two, 12 runs, on four trees at once | live 11 and 9, this commit 10 and 10, the sentence before the lift sentence 10 and 11, the short Goals sentence alone 12 and 7: within 2 of live on each, so the dips were noise | plan usage |

### Known limits

- `lb_bench_goal` on the short Goals sentence: 2 of 15 runs asked which year a June goal meant, against 0 of 15 live.
- `capabilities_plain` fails on both trees: the model answers from the instructions without the calls the case wants. On the live code one Sonnet reply said it saw nothing for sleep; on this commit both named sleep.
- `undo_moved_scale_date` fails on both trees: an edit where an undo is asked.
- On `gpt-6-luna`, `health_export_preview` and `daily_sleep_goal` fail on both trees by the judge's words: "Say yes and I'll import them" is not read as a question, and its "can't" pattern takes a straight apostrophe where the model writes a curly one.

## Both walks named in log_cardio

On 2026-11-20. No contract: a one-phrase fix of a gap on the live list since the exercise list's second deploy, found by a comparison run on Sonnet. Model-read text.

| Package | Commit | What it did |
|---|---|---|
| The fix | `5d1ec03` | `log_cardio`'s `exercise` words name both walks: "such as Outdoor Walk or Treadmill Walk", where they named Treadmill Walk alone |

### Why

Since the instructions take a lift in the person's own words, Sonnet with every function loaded up front often logs a message of several kinds without looking names up in `list_exercises`. It then took "walked home after, about 25 minutes" as Treadmill Walk, the one walk the field named, and said Spotter has no outdoor walk. `rambling_day_log` judges the walk through its `expect`; it had not run since that deploy.

### Checks

The 11 test files the change touches, plain and with `-W error`; two published hashes moved. The whole suite on a clean export of `5d1ec03`: 6,988 passed.

### Eval runs

| Model | Cases | Live code | This commit | Cost |
|---|---|---|---|---|
| `claude-sonnet-5-5`, every function loaded up front | `rambling_day_log`, 12 runs | 4 passed; the walk as Treadmill Walk 8 times | 11 passed; the walk right 12 of 12, with no `list_exercises` call | $2.4803 |
| `claude-sonnet-5-5`, tool search on | `rambling_day_log`, 12 runs | 8 passed; the walk right 12 of 12 | 10 passed; the walk right 12 of 12 | $2.8631 |
| `claude-sonnet-5-5`, both ways | 9 cardio cases, 2 runs | `cardio_duplicate_said` 0 of 2 | 16 of 18 with tool search on, both misses `cardio_duplicate_said`; 18 of 18 up front | $2.9202 |
| `gpt-6-luna` | 10 cardio cases, 3 runs | | 24 of 30; `rambling_day_log` 3 of 3 | plan usage |
| `gpt-6-luna` | `distance_implies_run` and `tennis_not_listed`, 12 runs | 12 and 6 | 10 and 5 | plan usage |

### Known limits

- The bug shows only with every function loaded up front: with Claude Code's default tool search, Sonnet looked the names up and was right before the fix too.
- `cardio_duplicate_said` fails on both trees by the judge's words: the reply offers to log it again without a question mark.
- `tennis_not_listed` fails on `gpt-6-luna` on both trees about half the time: it sends "Tennis" to `log_cardio` before `list_exercises`, is refused, and nothing is written.
- `distance_implies_run`: 2 of 12 runs on `gpt-6-luna` changed a setting unasked; the run itself was logged right.
- In `rambling_day_log`, a meal the person named is often left out, and Spotter files it by its clock.

## A food's meal: the one the person named is sent

On 2026-11-20. No contract: a reworded rule and three eval checks. Model-read text.

| Package | Commit | What it did |
|---|---|---|
| The eval checks | `4d28319` | `rambling_day_log`, `stated_number` and `catch_up` judge the meal in the `log_food` call as well as the stored row. A test plays each with no meal at 12:30 and with lunch at 17:30 |
| The wording | `0efb1f9` | The instructions say "Send the meal the person named for a food; with none named, leave it out. Never ask which meal." in place of "Leave the meal out unless the person named it: Spotter picks it from their clock. Never ask which meal." (4,082 characters, from 4,090). `log_food`'s meal field and `can-and-cant` lead the same way; with none named the clock still picks it |

### Why

"lunch was a chicken wrap ... the label said 520 kcal" in a long message: Sonnet and Haiku often sent no meal, so Spotter filed the wrap by the clock, as dinner in the evening. Their replies said lunch, so the model knew it; most runs then moved it with `edit_entry`, some left it wrong. The rule led with leaving the meal out, and every drop went that way. The drops grew after the exercise list's second deploy, when the model began logging without looking names up first; why is not shown.

The eval had hidden it: with no meal sent, a run between 11:00 and 16:00 in the account's zone was filed as lunch and passed. The checks now read the call, so the verdict is the same at any hour.

### Checks

The eval checks: `tests/test_evals.py` and `tests/test_golden_cases.py`, 504 passed plain and with `-W error`. The wording: the 11 test files that pin the instructions, their length and the published list, with `test_mcp`, `test_docs`, `test_text_rules`, `test_calllog` and `test_eval_need`, 1,978 passed plain and with `-W error`; `log_food`'s published hash and the instructions' hash moved. The whole suite on a clean export of this entry's commit: see the release.

### Eval runs

Both trees carry the new checks: `4d28319` (the rule as it was) against `0efb1f9` (the new wording), run at about 21:00 in Asia/Kolkata, when a missing meal is filed as dinner. "Sent" is the named meal in the first `log_food` call that went through.

| Model | Cases | Before | After | Cost before, after |
|---|---|---|---|---|
| `claude-sonnet-5-5`, tool search on | `rambling_day_log`, 24 runs | 15 passed; lunch sent 15, 8 of the 9 others moved by `edit_entry` | 24 passed; lunch sent 24 | $2.885, $2.7044 |
| `claude-sonnet-5-5`, every function loaded up front | `rambling_day_log`, 12 runs | 4 passed; lunch sent 4, 5 of the 8 others moved | 12 passed; lunch sent 12 | $1.0467, $1.2151 |
| `claude-haiku-5-5`, every function loaded up front | `rambling_day_log`, 12 runs | 10 passed; lunch sent 10 | 11 passed; lunch sent 12 | $0.0807, $0.0815 |
| `gpt-6-luna` | `rambling_day_log`, 12 runs | 9 passed; lunch sent 11 | 12 passed; lunch sent 12 | plan usage |
| All three | 8 cases that name a meal or none, 3 runs | Sonnet 21 of 24, Haiku 22 of 24, Luna 23 of 24 | Sonnet 21 of 24, Haiku 21 of 24, Luna 24 of 24 | Sonnet $2.0433, $2.0059; Haiku $0.1231, $0.1259 |
| All three | `separate_food_repeat`, `meal_not_asked`, `old_run_fix_and_snack`, 3 runs | 27 of 27 | Sonnet 9 of 9, Haiku 6 of 9, Luna 7 of 9 | Sonnet $1.1842, $1.1265; Haiku $0.0581, $0.0568 |
| `gpt-6-luna` | `separate_food_repeat`, 12 runs | 11 passed; no meal sent in any run | 9 passed; a meal sent 3 | plan usage |

In all: $7.4211 before, $7.3161 after.

### Known limits

- "had another Roti just now, yes an extra one after lunch" (`separate_food_repeat`) names no meal, but with the new wording a meal was sent: on `claude-haiku-5-5` 2 of 3 ("snack"), on `gpt-6-luna` 4 of 15 ("lunch" 3, "snack" 1); on Sonnet 0 of 3. Kept: it moves an entry between meals, never a number, and the new wording sends 21 named meals that were left out before, against these 6.
- `recent_biryani_again` fails on both trees: on Sonnet 0 of 3 each, on Haiku 1 of 3 and 0 of 3. The model reads `get_log`, then neither logs the food nor asks.
- On `gpt-6-luna` one `stated_number` run before the change sent `meal` beside `entries`; it was refused and the retry left the meal out.

## Without Spotter: the same model with Spotter and without it

On 2026-11-21. Contract: `docs/contracts/without-spotter.md`. Eval code and a doc: no app file and no model-read text changed, so `python -m evals.need main` says no.

| Package | Commit | What it did |
|---|---|---|
| The runner, the cases, the tests, the doc | `d3d6383` | `python -m evals.without`: 14 cases on the seeded weeks, each run with Spotter (W) and without it: the whole history told in one prompt (N-history), a short fixed memory (N-memory), or nothing (N-new). Judged on the reply's numbers, with a band that fails a made-up figure beside the right one; W also on the record |
| From the pilot | `0199e10` | A Codex run whose only tool items are its own resource listings on its built-in server is judged, as they find nothing with no server given; C2 takes an 8-week plan written as 8-week, 8 week or eight-week |
| From a diagnosis | `709dc59` | A Claude run with no Spotter is told it is a plain chat with no tools, no files and no working directory |
| From the runs | `e976626` | U1's "estimate" check reads only the sentence that names the biryani or 640 |

### Why

To measure what Spotter adds over the same assistant alone, on the questions where it should show, and where it still slips.

### The hang

A Claude run with no Spotter and no data hung to the 300 s time-out, 4 runs in 5 on one cell, alone too. The CLI's debug log showed the stream starting, then nothing after the model's thinking. Claude Code's own prompt tells the model it has a working folder, so with no data and no tools it reached for a file tool that is not there; a run that did answer said it would "check the working directory for a food-tracking file". With the plain-chat line, 6 runs in 6 answered in 5 to 7 s. Claude's N runs made before the line are not counted.

### Checks

The files the build touched, with `test_evals`, `test_switch`, `test_docs`, `test_text_rules` and `test_eval_need`, plain and with `-W error` after each package. The whole suite on a clean export of this entry's commit: see the release.

### Eval runs

3 runs a cell; `claude-sonnet-5-5` and `claude-haiku-5-5` through the Claude CLI with tool search at its default, `gpt-6-luna` through Codex; the seeded sixteen weeks; run 2026-11-21.

| Model | 9 reads: W | N-history | N-memory | N-new says no record | 3 unclear: W, N-history | 2 controls: W, N-new |
|---|---|---|---|---|---|---|
| `claude-sonnet-5-5` | 26 of 27 | 22 of 27 | 4 of 27 | 9 of 9 | 4, 4 of 9 | 6, 6 of 6 |
| `claude-haiku-5-5` | 27 of 27 | 22 of 27 | 3 of 27 | 9 of 9 | 2, 5 of 9 | 6, 6 of 6 |
| `gpt-6-luna` | 26 of 27 | 16 of 27 | 3 of 27 | 8 of 9 | 5, 4 of 9 | 6, 6 of 6 |

A W read counts only where the record backs it. Every reply is judged with the rules of `e976626`. The past problem writes on `evals.run` (`rambling_day_log`, `routine_logged`, `tennis_not_listed`): Sonnet 9 of 9, Haiku 8 of 9, Luna 8 of 9. A read on Sonnet, median: W $0.087, N-history $0.118. Spend: Sonnet $13.54, Haiku $0.76, Luna plan usage.

### Known limits

- "85 today" (U3): with Spotter every model logged 85 kg as today's weigh-in, replacing the 78.6 there, 9 of 9 runs; told the history, the models mostly asked.
- U1 on Sonnet with Spotter: the model saw the biryani already logged tonight and added nothing; the case wants a log or a question, so it fails.
- N-memory is a stand-in written once, not any app's real memory. Codex is not the ChatGPT app.
- Gemini: the pilot only. Five of nine keys answered 429 on the first request, and the W runs on the rest timed out or were reset; the full run is not done.
