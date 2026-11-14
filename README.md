# Spotter

A personal fitness record you keep by talking to your AI assistant.

You say what you lifted, ate or weighed. Your assistant logs it through Spotter over MCP. Spotter stores it and does the arithmetic in code: bests, trends, calorie estimates, the pace a goal needs. The assistant is told to quote Spotter's numbers.

**Status: stages 1 to 6 are live. Every web page is on the new design.**

Live: https://spotter-production-0c0f.up.railway.app

## Why use it

- **Log by talking.** Say it in a sentence. No forms.
- **Numbers worked out in code.** Bests, trends and goal pace come from your own log. Every food number is tagged with who supplied it: you, a saved food, the USDA table, or the assistant's estimate.
- **Your record, not the assistant's.** It lives in Spotter under your sign-in, so the same data is there from any assistant you connect. Use more than one, or switch, with nothing to export or import.
- **You set the limit.** One switch on Settings says what the assistant may do: read, log, or log and delete. It starts at log. A deleted entry can be restored.
- **See it and take it with you.** A web app on the same account (Today, Progress with a page per lift, Goals, Foods, Settings). From Settings you can export everything as CSV files or delete the account.

## Set up

1. **Sign in** at the live address above. The first sign-in creates your account and saves your time zone from the browser.
2. **Add Spotter to your assistant** as a remote MCP server. Some call it a custom connector. The address is `https://spotter-production-0c0f.up.railway.app/mcp`. There is no key to paste: the assistant opens the sign-in page. Use the same account.
3. **Say what you did**, such as "Back squat, 3 sets of 5 at 80 kg".

Signing in from the assistant first also creates the account.

The client must support remote MCP over streamable HTTP with OAuth sign-in.

## Your assistant

| Assistant | How to add Spotter | Status |
|---|---|---|
| [Claude apps](https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp): web and desktop | Customize, then Connectors, then "+ Add", then "Add custom connector". Enter a name and the address, then follow the form to "Add". In a chat, press "+", then Connectors, and switch Spotter on. | Tried on the web. Desktop: from its docs. Free plans get one custom connector. On Team and Enterprise an owner adds it. |
| [Claude Code](https://code.claude.com/docs/en/mcp) | Run the command below, then type `/mcp`, pick spotter and sign in. | Tried |
| [ChatGPT](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) | On the web: Plugins, then the plus button, then "Add custom MCP server". Enter the address, set up OAuth, then "Create as a plugin". Install it, then type @ in a chat and pick it. | From its docs. Full access needs Business, Enterprise or Edu. Pro can read only. |
| [Cursor](https://cursor.com/docs/context/mcp) | In `~/.cursor/mcp.json`, add a server under `mcpServers` with its `url` set to the address, then sign in. | From its docs |
| [VS Code with Copilot](https://code.visualstudio.com/docs/copilot/customization/mcp-servers) | Run "MCP: Add Server", choose HTTP, and enter the address. | From its docs |
| [Gemini app](https://support.google.com/gemini/answer/17209137) | On the web: Settings, then "Connected Apps". Under "Custom apps", enter the address, then "Next". | From its docs. US only, 18 or over, personal account, Keep Activity on. |
| [Mistral](https://docs.mistral.ai/le-chat/knowledge-integrations/connectors/mcp-connectors) | In the sidebar: Context, then Connectors, then "New connector", then "Add custom connector". Enter a name and the address, leave Authentication on "Auto-detect", then "Add connector". Sign in and approve. | Tried |
| [goose](https://goose-docs.ai/docs/getting-started/using-extensions) | Run `goose configure`, then "Add Extension", then "Remote Extension (Streamable HTTP)", and give it the address. | From its docs |

"From its docs" means the steps follow that client's own documentation and have not been tried with Spotter yet.

Any other MCP client that supports remote servers with OAuth sign-in is added the same way, with the address. A client that only speaks stdio can go through the [mcp-remote](https://github.com/geelen/mcp-remote) bridge, by its README: `npx mcp-remote` followed by the address.

Claude Code, run once:

```
claude mcp add --transport http --scope user spotter https://spotter-production-0c0f.up.railway.app/mcp
```

## What to say

| You say | What happens |
|---|---|
| "Back squat, 3 sets of 5 at 80 kg" | The session is logged, and new bests are worked out against every earlier set |
| "Weighed 79.4 this morning" | One weigh-in a day, with the 7-day average and the trend |
| "Lunch: two rotis and dal" | The whole meal is logged at once, each number tagged with who supplied it |
| "30 minutes on the treadmill" | A calorie estimate from your body weight, kept beside the machine's number when you give one |
| "Squat 100 by June" | A goal with a start, a target and the pace it needs |
| "How am I doing?" | Your weight trend, average calories, sessions and cardio minutes |
| "Am I on track?" | Each open goal's needed pace, its pace so far and its projected date |
| "Bring my weigh-ins over from my old app", with its export | A preview first, which writes nothing: what would go in, what is already there, what was refused. The assistant is told to write only when you say yes |

## Under the hood

- 19 MCP functions and two resources, over streamable HTTP.
- Python 3.12, Starlette, SQLAlchemy, Alembic, the `mcp` library and Postgres. Sign-in goes through an outside provider (WorkOS AuthKit) for the web and the assistant, so both reach one account.
- Every write locks the person's row first, counts against a write limit, and passes the same checks whether it came from the assistant or a form.
- Every write is safe to send twice. A call may carry a key of its own: the same key and call again writes nothing and gets the first answer back. Every form that writes carries a hidden key, so a double click saves once. Answers are kept for a day.
- An edit keeps what it replaces. The values from before each edit are kept, the newest 10 an entry for 90 days, and one call or one click undoes the last edit. An edit form older than the entry's last change is refused.
- Safety limits: a sized connection pool, a time limit on every query, lock wait and idle transaction, a read limit and a page limit per person, and a 1 MiB cap on every request. A database that is too busy answers that it is busy, not with a crash.
- A call log with a trace: one row per call, with a request id that is also on the log lines and the response, the names of the fields sent and never a value, whether it wrote, and where a failed call stopped. Rows are deleted after 90 days.
- A docs-only change runs the docs tests, and a small code change runs the tests for the code it touches. The whole suite runs with warnings as errors on a clean export before a big push, and about once in every four or five pushes otherwise.
- Evals: `claude -p` against a local copy with a throwaway person; the server records every call it received, and the eval judges that, not the model's own account of what it did. 21 cases, 3 runs each. At stage 5, before stage 6 added four, 48 of 48 runs passed, on `claude-fable-5-2`. On 2026-11-12 the set of that day ran on `claude-opus-5-5`: 60 of 60 runs passed. On 2026-11-14 the 21 cases ran: 63 of 63. The runner pins `claude-opus-5-5`. The result is recorded in `BUILD_LOG.md`; how the evals run is in `docs/evals.md`.

## Read more

| File | What it is |
|---|---|
| `docs/can-and-cant.md` | What Spotter records, what its numbers mean, and what it cannot know. Served to the assistant as an MCP resource |
| `docs/evals.md` | How the evals run and what they do not prove |
| `BUILD_LOG.md` | How each stage was built, reviewed and checked live |
| `docs/contracts/` | What each stage had to do, fixed before the builders started |

Tests: Python 3.12, `pip install -r requirements.txt -r requirements-dev.txt`, then `python -m pytest tests -q`.

## Licence

MIT for the code: see `LICENSE`. The cardio MET values, codes and activity descriptions come from the 2024 Adult Compendium of Physical Activities and keep its own terms: see `NOTICE`.
