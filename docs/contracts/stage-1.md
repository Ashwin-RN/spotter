# Stage 1 contract: the sign-in test

**Goal: prove the sign-in chain end to end on a bare server, before any feature is built on it.**

Spotter is an OAuth resource server. It never issues tokens and never stores passwords.
An outside provider (WorkOS AuthKit) is the authorization server.
Claude finds the provider through Spotter's metadata, signs the person in there, and calls Spotter with a token.

## What exists at the end

- One Starlette app with `/health` and the MCP endpoint at `/mcp`.
- One MCP function, `whoami`.
- A token verifier, with tests.
- A deploy file for Railway.

No database. No web pages. No secrets: every setting in this stage is a public value.

## Files (the fixed set)

| File | Holds |
|---|---|
| `app/__init__.py` | Empty |
| `app/config.py` | Reads the environment. Nothing else reads `os.environ` |
| `app/auth.py` | The token verifier |
| `app/mcp_server.py` | The MCP server and `whoami` |
| `app/main.py` | The Starlette app: `/health`, the MCP mount, lifespan |
| `tests/__init__.py`, `tests/conftest.py`, `tests/test_auth.py`, `tests/test_mcp.py` | Tests |
| `tools/probe.py` | Checks a running server from outside, without a token |
| `requirements.txt`, `requirements-dev.txt`, `.python-version`, `railway.json` | Run and deploy |

## Settings

| Variable | Example | Meaning |
|---|---|---|
| `AUTHKIT_DOMAIN` | `example-name-12.authkit.app` | The provider's host. The issuer is `https://<AUTHKIT_DOMAIN>` |
| `MCP_RESOURCE_URL` | `https://spotter.example.app/mcp` | This server's own MCP address. The only audience it accepts |
| `AUTH_DEBUG` | `1` | Optional. Logs claim names and a few public claims. Off by default |

## Behaviour

Each line is a test.

**Health**

1. `GET /health` returns 200 and the text `OK`. It needs no sign-in and touches nothing else.

**Finding the sign-in**

2. A call to `/mcp` with no token returns 401. The `WWW-Authenticate` header carries `resource_metadata`, pointing at this server's protected-resource metadata.
3. The protected-resource metadata lists `resource` equal to `MCP_RESOURCE_URL`, character for character, and `authorization_servers` equal to `["https://<AUTHKIT_DOMAIN>"]`.
4. The resource address comes from the setting only. A forged `Host` or `X-Forwarded-Host` header does not change it.

**Checking a token**

5. A token is accepted only when all of these hold:
   - signed by a key in the provider's key set (`https://<AUTHKIT_DOMAIN>/oauth2/jwks`), with RS256
   - `iss` equals `https://<AUTHKIT_DOMAIN>` exactly
   - `aud` contains `MCP_RESOURCE_URL` exactly
   - not expired
6. Each of these is refused with 401: a token for another audience; a token from another issuer; an expired token; a bad signature; a token with `alg` of `none` or `HS256`; a token that is not a JWT.
7. A key id the server has not seen triggers one refresh of the key set, at most once a minute. After that, the token is refused.
8. The key set is cached in memory. Fetching it never blocks the event loop.

**`whoami`**

9. With a valid token, `whoami` returns `user_id` (the token's `sub`), `email`, `name`, `scopes`, `client_id`, `issuer` and `expires_at`.
10. If the token carries no email or name, the server looks up `userinfo_endpoint` in the provider's metadata and asks it. On any failure, `email` and `name` are null. The token is never sent to any other host.
11. `whoami` is marked read-only, has a title, and has a result schema.

**Failing closed**

12. With `AUTHKIT_DOMAIN` or `MCP_RESOURCE_URL` missing, the app still starts and `/health` still answers. `/mcp` returns 503 with `{error, message, fix}`. No token is accepted.

**Logs**

13. No token and no `Authorization` header is ever logged, in any mode.
14. With `AUTH_DEBUG=1`, a verified call logs the claim names plus `iss`, `aud`, `scope` and `client_id`. Nothing else.

**Transport**

15. The MCP endpoint is stateless and answers in JSON: every call stands alone.

## Rules for the build

- Use what the MCP library already provides for resource servers: its auth settings, its token-verifier interface, its 401 and its metadata route. Write only the verifier.
- No new packages. PyJWT and `cryptography` already come with the MCP library. If something else seems needed, stop and say so.
- No scopes are required in this stage. What the provider puts in `scope` is one of the things this stage finds out.
- Tests make their own RSA key, serve their own key set, and sign their own tokens. No test touches the network.

## Exit test

1. The suite is green.
2. `tools/probe.py <address>` passes against the deployed server: health, the 401 and its header, and the metadata.
3. Claude on the web and Claude Code each connect, sign in, and call `whoami`. It names the signed-in person in both.
4. A token meant for another server is refused. The suite proves this; the live check uses a token with the wrong audience where one can be had.

## What this stage finds out

Written into `BUILD_LOG.md` when known.

- Does the provider put this server's address in `aud`?
- Which claims name the person, and what is in `scope`?
- Does Claude register through its Client ID Metadata Document, on the web and in Claude Code?
- Does a sign-in from the web and one from Claude Code give the same `user_id`?
