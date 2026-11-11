# Spotter

A personal fitness record you keep by talking to your AI assistant.

You say what you lifted, ate or weighed. Your assistant logs it through Spotter over MCP. Spotter stores it and does the arithmetic in code: bests, trends, calorie estimates, the pace a goal needs. The assistant is told to quote Spotter's numbers.

**Status: stages 1 to 5 are live.**

Live: https://spotter-production-0c0f.up.railway.app

## Why use it

- **Log by talking.** Say it in a sentence. No forms.
- **Numbers worked out in code.** Bests, trends and goal pace come from your own log. Every food number is tagged with who supplied it: you, a saved food, the USDA table, or the assistant's estimate.
- **Your record, not the assistant's.** It lives in Spotter under your sign-in, so the same data is there from any assistant you connect. Use more than one, or switch, with nothing to export or import.
- **You set the limit.** One switch on Settings says what the assistant may do: read, log, or log and delete. It starts at log. A deleted entry can be restored.
- **See it and take it with you.** A web app on the same account (Today, Progress with a page per lift, Goals, Foods, Settings, Privacy). From Settings you can export everything as CSV files or delete the account.

## Set up

1. **Sign in** at the live address above. The first sign-in creates your account and saves your time zone from the browser.
2. **Add Spotter to your assistant** as a remote MCP server. Some call it a custom connector. The address is `https://spotter-production-0c0f.up.railway.app/mcp`. There is no key to paste: the assistant opens the sign-in page. Use the same account.
3. **Say what you did**, such as "Back squat, 3 sets of 5 at 80 kg".

Signing in from the assistant first also creates the account. To see it full before you log anything, press "Add sample data" on Today. One click on Settings removes it.

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

## Under the hood

- 18 MCP functions and two resources, over streamable HTTP.
- Python 3.12, Starlette, SQLAlchemy, Alembic, the `mcp` library and Postgres. Sign-in goes through an outside provider (WorkOS AuthKit) for the web and the assistant, so both reach one account.
- Every write locks the person's row first, counts against a write limit, and passes the same checks whether it came from the assistant or a form.
- A docs-only change runs the docs tests, and a small code change runs the tests for the code it touches. The whole suite runs with warnings as errors on a clean export before a big push, and about once in every four or five pushes otherwise.
- Evals: `claude -p` against a local copy with a throwaway person; the server records every call it received, and the eval judges that, not the model's own account of what it did. 17 cases, 3 runs each. At stage 5, with one case fewer, 48 of 48 runs passed, on `claude-fable-5-2`. The newest case, from stage 6, has not been run yet. The runner now pins `claude-opus-5-5`. The result is recorded in `BUILD_LOG.md`; how the evals run is in `docs/evals.md`.

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
