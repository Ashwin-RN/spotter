# The icon's addresses: the contract

**Goal: favicon crawlers and assistant apps can find Spots, the icon in `app/static/` since `4e9c4fb`. The site
answers `/favicon.ico` at its root, and the MCP server names its icon and website.**

What exists before this work, read in the code on `4e9c4fb`:

| Piece | State |
|---|---|
| The icon files | `app/static/favicon.svg`, `favicon.ico` (32 by 32), `apple-touch-icon.png` (180 by 180) |
| Where they answer | Under `/static/` only, through the `/static` mount at the end of `web.routes`, with `headers_for(settings, static=True)`: the page headers with an hour's cache line |
| `/favicon.ico` at the root | No route: `web.fallback` answers it with the 404 page |
| The server's identity | `build_server` in `app/mcp_server.py` passes a name and a version. `serverInfo` carries no icons and no website |
| What the library takes | `MCPServer(website_url=..., icons=[Icon(...)])` in mcp 2.3.0. `Icon` has `src`, `mime_type`, `sizes`, `theme` |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **`/favicon.ico` answers 200** with the bytes of `app/static/favicon.ico`, GET and HEAD, as a `FileResponse`, the class `StaticFiles` sends. Its content type is the one `/static/favicon.ico` gets | The root answered 404. The bytes are served, never a redirect |
| 2 | **The route sits beside the `/static` mount,** after both arms of `web.routes`, with the same static headers. No session, no database, no page limit, no CSRF form key | It must answer on the off site too, and signed out |
| 3 | **The server declares its website and two icons,** from `settings.site_origin`, never a request's host: `{origin}/static/apple-touch-icon.png`, `image/png`, `180x180`, then `{origin}/static/favicon.svg`, `image/svg+xml`, `any`. `website_url` is the origin | The server declared no icon |
| 4 | **No origin, no icons:** `site_identity(None)` passes neither field. The app never builds the server then (the MCP settings are missing), and `AuthSettings` refuses a missing address, so the helper is what the test reads | An address without a host would be a broken link |
| 5 | **No model-read text changes:** no description, no instructions, no `docs/can-and-cant.md`, no result text | The icon is for the apps, not the assistant |

## Work package: one builder

| Package | Files | Exit check |
|---|---|---|
| I1 | `app/web.py`, `app/mcp_server.py`, `tests/test_page_rules.py`, `tests/test_mcp.py`, `tests/test_hi_there.py` (the route table pin gains the `/favicon.ico` row before `/static`), this contract, `BUILD_LOG.md` | `/favicon.ico` signed out: 200, the file's bytes, the content type, cache line and fixed headers of `/static/favicon.ico`, nosniff, no cookie, no database statement; HEAD 200 with no body. Not behind the page limit. 200 on both off sites (no web sign-in, no server address). `initialize` at `2025-06-18` and `2025-11-25` carries `websiteUrl` and the two icons in order, with the configured origin even under a forged host; with no origin, neither. The touched test files pass plain and with `-W error`. `python -m evals.need main` is run and its answer recorded |

Not in this work: a new icon file, a page link, any eval run, the whole suite and the live check (the lead's).
