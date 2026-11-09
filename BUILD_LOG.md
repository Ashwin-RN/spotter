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
| Is `user_id` the same from the web and from Claude Code? | Yes. After both sign-ins the provider lists one user | The provider's user list |
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
