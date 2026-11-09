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

### What this stage has taught so far

- **The host did not read the repo's deploy file.** The first deploy of package A succeeded and the database stayed empty: the migration step had never run. The host has retired config files for new services, so the start command, the pre-deploy step and the health check now live in its dashboard. The check that caught it was looking at the database itself, not at the green deploy.
- **"Deployment successful" does not mean the step you added ran.** Verify the effect, not the status.
- **A wide `except` turns every bug into the wrong message.** The web pages caught every exception from a database call and showed "cannot reach the database". A typo would have looked like an outage.
- **A test can pass because its setup never reached the code.** The tampered-cookie test set its cookie with no domain, so the cookie was never sent. The page redirected for a different reason, and the test was green.
- **SQLite accepts what Postgres refuses.** A NUL character in text, an id past 32 bits: the tests ran green and the host would have crashed. A fuzz run and reading the SQL as Postgres would see it found both; the test suite could not.
- **A passing isolation test does not prove every query is scoped.** With the owner filter removed from two helper queries, the suite stayed green. Undoing each filter and watching for a red test is what found them.
- **Matching a shell command with one pattern does not work.** The command arrives inside a JSON string, so quotes are escaped, and no single pattern told `bash -c "git commit"` from `--grep="git commit"`. Splitting into shell words did.
