# Moving Spotter to spottermcp.com

The steps to move the live site and MCP server from the Railway address to `spottermcp.com`, and sign-in from
WorkOS's staging environment to production, in one move. Written 2026-11-17, before the first outside sign-up.
Nothing here is secret: keys and passwords are typed by the owner on each provider's own page, never here.

## Why one move, and why now

| Fact | Source |
|---|---|
| Every address the app uses comes from one variable, `MCP_RESOURCE_URL`: the site, the MCP address, and the sign-in return `/auth/callback` | `app/web_auth.py:49`, `app/config.py` |
| A new address means every connector is added again | MCP tokens are checked against `MCP_RESOURCE_URL` |
| WorkOS staging and production are separate: no user moves between them; each person signs in again and gets a new production account. Production needs billing added; its keys start `sk_live_`; its redirect must be https | https://workos.com/docs/authkit/environments |
| Railway's Trial plan allows one custom domain a service (the bare domain and www count as two). A bare domain needs CNAME flattening or an ALIAS record at the DNS host | https://docs.railway.com/networking/domains/working-with-domains |
| GoDaddy's DNS cannot point a bare domain at Railway, so the DNS moves to Cloudflare's free plan. The domain stays registered at GoDaddy | https://station.railway.com/questions/domain-verification-help-4f6fbaf2 |

Both changes ask everyone to reconnect, so they go together, while the only account is the owner's.

## The steps

"Owner" steps are the owner's own clicks. Step 3 can take minutes to a few hours to spread; steps 6 and 7 can be done meanwhile.

| # | Who | Step |
|---|---|---|
| 1 | Owner | **GoDaddy:** two-factor sign-in on the account, the domain's transfer lock, auto-renew |
| 2 | Owner | **Cloudflare:** a free account, "Add a site", `spottermcp.com`, the Free plan. Cloudflare shows two nameservers |
| 3 | Owner | **GoDaddy:** the domain's nameservers replaced with Cloudflare's two. Cloudflare emails when the site is active |
| 4 | Owner | **Railway:** the service, Settings, Networking, Custom Domain, `spottermcp.com`. Railway shows a target value, and maybe a TXT record to prove ownership. Neither is secret |
| 5 | Owner | **Cloudflare DNS:** a CNAME named `@` pointing to Railway's target, set to **DNS only** (grey cloud); the TXT record if Railway gave one; and two records that say the domain sends no email: TXT `@` = `v=spf1 -all`, TXT `_dmarc` = `v=DMARC1; p=reject`. Wait until Railway shows the certificate issued |
| 6 | Owner | **WorkOS:** billing added to unlock production (check the price shown before confirming). In the production environment: every AuthKit setting copied from staging (the sign-in methods, the setting that lets assistants register as MCP clients, the branding), and the redirect `https://spottermcp.com/auth/callback` |
| 7 | Owner | **Railway variables:** `AUTHKIT_DOMAIN`, `WORKOS_CLIENT_ID` and `WORKOS_API_KEY` from WorkOS production; `MCP_RESOURCE_URL` = `https://spottermcp.com/mcp`. Then Deploy |
| 8 | Builder | `README.md`'s three addresses changed to the new one, a docs commit, pushed |
| 9 | Owner | Sign in at `https://spottermcp.com`. Remove the old Spotter connector in claude.ai and ChatGPT and add it again with `https://spottermcp.com/mcp`; in Claude Code, `claude mcp remove spotter` then `claude mcp add --transport http --scope user spotter https://spottermcp.com/mcp` |
| 10 | Builder, then owner | A fresh assistant session calls `whoami` and reads the owner's new account id. The owner sets `OWNER_USER_ID` to it on Railway and deploys. The owner functions are checked |

## The checks

| Check | Expected |
|---|---|
| `https://spottermcp.com/health` | 200 |
| `https://spottermcp.com/mcp` with no token | 401, with `resource_metadata` on the new address |
| `https://spottermcp.com/` | 200 |
| `python tools/probe.py https://spottermcp.com` | Every check PASS |
| Web sign-in | Lands on Today, as a new account |
| A fresh `claude -p` session | `whoami` answers, and `what_is_logged` |
| The owner functions | Answer for the new owner id |

## After the move

- The old staging account is left empty and can no longer sign in. It is removed later.
- The Railway address keeps serving, but a connector on it fails sign-in, since tokens are checked against the new address.
- Later, by the owner's word: WHOIS privacy (whether the owner's details show in the domain's public record), and email forwarding through Cloudflare for a contact address.
