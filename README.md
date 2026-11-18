# Spotter

A personal fitness record you keep by talking to your AI assistant.

You say what you lifted, ate or weighed. Your assistant logs it through Spotter over MCP. Spotter stores it and does the arithmetic in code: bests, trends, calorie estimates, the pace a goal needs. The assistant is told to quote Spotter's numbers.

**Status: live. Each stage, and each item built since, is recorded in `BUILD_LOG.md`.**

Live: https://spottermcp.com

## Why use it

- **Log by talking.** Say it in a sentence. No forms.
- **Numbers worked out in code.** Bests, trends and goal pace come from your own log. Every food number is tagged with who supplied it: you, a saved food, the USDA table, or the assistant's estimate.
- **Your record, not the assistant's.** It lives in Spotter under your sign-in, so the same data is there from any assistant you connect. Use more than one, or switch, with nothing to export or import.
- **You set the limit.** The assistant can read, log and delete. One switch on Settings turns deleting off. A delete takes two steps: the assistant is first shown the entry and told to ask you. A deleted entry can be restored.
- **See it and take it with you.** A web app on the same account (Today, Progress with a page per lift, Goals, Foods, Routines, Settings). From Settings you can export everything as CSV files or delete the account.

## Set up

1. **Add Spotter in your assistant** as a remote MCP server. Some call it a custom connector. The address is `https://spottermcp.com/mcp`. The steps for each app are under Your assistant below.
2. **Sign in through the assistant.** There is no key to paste: the assistant opens Spotter's sign-in page, and you sign in with Google. The first sign-in creates your account and saves your time zone.
3. **Paste the starter prompt** below into a new chat.

Sign-in is open only to invited Google accounts for now.

You can also sign in on the web at https://spottermcp.com, with the same Google account, to see your pages.

While nothing is logged yet, your assistant hands you one link, `/hi`: a short page on what to say, what Spotter keeps and how to bring your history over from another app.

The app must support remote MCP over streamable HTTP with OAuth sign-in.

## Your assistant

| Assistant | How to add Spotter | Status |
|---|---|---|
| [Claude apps](https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp) | On the web or desktop: Customize, then Connectors, then "+ Add", then "Add custom connector". Enter a name and the address, then "Continue", and follow the form to "Add". In a chat, press "+", then Connectors, and switch Spotter on. | Tried on the web. Desktop: from its docs. The phone apps use it once it is added on the web or desktop. Free plans get one custom connector. On Team and Enterprise an owner adds it. |
| [Claude Code](https://code.claude.com/docs/en/mcp) | Run its command under Commands, then type `/mcp`, pick spotter and sign in. | Tried with Spotter. |
| [ChatGPT](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) | On the web: Plugins, then the plus button, then "Add custom MCP server". Enter a name and the address, set up OAuth, tick the risk box, then "Create as a plugin". Install it, then type @ in a chat and pick it. | From its docs. Added on the web only. Full access needs Business, Enterprise or Edu. Pro can only read. |
| [Gemini app](https://support.google.com/gemini/answer/17209137) | On gemini.google.com: Settings, then "Connected Apps". Under "Custom apps", add a custom app, enter the address, then "Next" and follow the steps. | From its docs. Added on the web only, then it works in the phone app too. US only, 18 or over, personal account, English, History on. |
| [Grok](https://docs.x.ai/grok/connectors) | At grok.com/connectors: "New Connector", then "Custom". Enter the address and sign in. | From its docs. |
| [Mistral](https://docs.mistral.ai/vibe/work/web/connectors/mcp-connectors) | In Work: Connectors, then "+ Add Connector", then the "Custom MCP Connector" tab. Enter a name with no spaces, such as spotter, and the address, then "Connect". Sign in and approve. | Tried with Spotter. On Free, Pro and Student plans the account owner adds it. |
| [Perplexity](https://www.perplexity.ai/help-center/en/articles/13915507-adding-custom-remote-connectors) | Plugins in the sidebar, then "Add custom", then "Add MCP connector", then "Remote". Enter a name and the address. Under Advanced, set Authentication to OAuth and Transport to Streamable HTTP. Tick the box, then "Add", then press the card to sign in. | From its docs. In an organization, an admin must let members add connectors. |
| [GitHub Copilot in VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers) | Run "MCP: Add Server", choose HTTP, and enter the address. Or add the JSON under Commands. | From its docs. |
| [Cursor](https://cursor.com/docs/mcp) | Add the JSON under Commands to `~/.cursor/mcp.json`, then sign in. | From its docs. |

"From its docs" means the steps follow that app's own docs and have not been tried with Spotter yet.

What cannot add Spotter today: Microsoft Copilot, the consumer app, as no Microsoft page found says it can. ChatGPT's phone app, as adding is on the web only. And the ChatGPT plans its page does not name.

Any other MCP client that supports remote servers with OAuth sign-in is added the same way, with the address. A client that only speaks stdio can go through the [mcp-remote](https://github.com/geelen/mcp-remote) bridge, by its README: `npx mcp-remote` followed by the address.

## Commands

For the tools set up by a command or a file. Each is from that tool's own docs.

[Claude Code](https://code.claude.com/docs/en/mcp), run once, then type `/mcp` and sign in:

```
claude mcp add --transport http --scope user spotter https://spottermcp.com/mcp
```

[Codex CLI](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), in `~/.codex/config.toml`:

```
[mcp_servers.spotter]
url = "https://spottermcp.com/mcp"
```

Then sign in with `codex mcp login spotter`.

[Gemini CLI](https://geminicli.com/docs/tools/mcp-server/), run once, then type `/mcp auth spotter` and sign in. Without `--scope user` it is added to the current project only:

```
gemini mcp add --transport http --scope user spotter https://spottermcp.com/mcp
```

[Cursor](https://cursor.com/docs/mcp), in `~/.cursor/mcp.json`:

```
{
  "mcpServers": {
    "spotter": {
      "url": "https://spottermcp.com/mcp"
    }
  }
}
```

[VS Code](https://code.visualstudio.com/docs/agents/reference/mcp-configuration), in `.vscode/mcp.json` or your user `mcp.json`:

```
{
  "servers": {
    "spotter": {
      "type": "http",
      "url": "https://spottermcp.com/mcp"
    }
  }
}
```

[Zed](https://zed.dev/docs/ai/mcp), in its settings file. With no Authorization header, Zed asks you to sign in:

```
{
  "context_servers": {
    "spotter": {
      "url": "https://spottermcp.com/mcp"
    }
  }
}
```

## Starter prompt

Paste this into a new chat once Spotter is added:

```
Hi Spotter, help me set up.
Call whoami first and do what it says.
Ask me one thing at a time, and save each answer as I give it.
Use Spotter's numbers, not your own. If a number is an estimate, say so.
```

## What to say

| You say | What happens |
|---|---|
| "Back squat, 3 sets of 5 at 80 kg" | The session is logged, with any new bests |
| "Lunch: two rotis and dal" | The whole meal is logged at once, each number tagged with who supplied it |
| "Weighed 79.4 this morning" | One weigh-in a day, with the latest weigh-ins and the trend |
| "30 minutes on the treadmill" | A calorie estimate from your body weight, kept beside the machine's number when you give one |
| "How am I doing?" | Your weight trend, calories a day, lift sessions and cardio minutes against your targets, and your maintenance calories once enough is logged |
| "Am I on track?" | Each open goal's needed pace, its pace so far and the date it is reached at that pace |
| "Save my push day: bench 3 sets of 8 at 60, dips 3 by 10" | The routine is kept by name. "Log my push day" then logs its sets |
| "Bring my weigh-ins over from my old app", with its export | A preview first, which writes nothing: what would go in, what is already there, what was refused. The assistant is told to write only when you say yes |
| "Tell Spotter's maker the progress chart is confusing" | The note is kept for the person who runs Spotter. No reply comes back through Spotter |

## Under the hood

- The MCP functions listed in `docs/can-and-cant.md`, and two resources, over streamable HTTP.
- Python 3.12, Starlette, SQLAlchemy, Alembic, the `mcp` library and Postgres. Sign-in goes through an outside provider (WorkOS AuthKit) for the web and the assistant, so both reach one account.
- Every write locks the person's row first, counts against a write limit, and passes the same checks whether it came from the assistant or a form.
- Every write is safe to send twice. A call may carry a key of its own: the same key and call again writes nothing and gets the first answer back. Every form that writes carries a hidden key, so a double click saves once. Answers are kept for a day.
- An edit keeps what it replaces. The values from before each edit are kept, the newest 10 an entry for 90 days, and one call or one click undoes the last edit. An edit form older than the entry's last change is refused.
- Feedback goes through the assistant. A note is sent only when the person asks or says yes to an offer, with a cap a person and the same note saved once. The owner alone reads the notes, each with the names of the functions that account called around then, never what was logged. A note is in the person's export and goes with the account.
- Safety limits: a sized connection pool, a time limit on every query, lock wait and idle transaction, a read limit and a page limit per person, a 1 MiB cap on every request, and an export limit of one at a time and 5 an hour per person. The database checks its own rules too: a sign and a ceiling on every number a person's row holds, and the closed value lists. A database that is too busy answers that it is busy, not with a crash.
- A call log with a trace: one row per call, with a request id that is also on the log lines and the response, the names of the fields sent and never a value, whether it wrote, and where a failed call stopped. Rows are deleted after 90 days.
- The tests run by hand before each push: the tests of the files touched, then the whole suite once.
- Evals: Claude, Codex or a Gemini host against a local copy with a throwaway person; the server records every call it received, and the eval judges that, not the model's own account of what it did. The cases are in `evals/cases.json`, 3 runs each. The runner pins each model and its reasoning effort. Each run's result is recorded in `BUILD_LOG.md`, and how the evals run is in `docs/evals.md`.

## Read more

| File | What it is |
|---|---|
| `docs/can-and-cant.md` | What Spotter records, what its numbers mean, and what it cannot know. Served to the assistant as an MCP resource |
| `docs/evals.md` | How the evals run and what they do not prove |
| `docs/domain-move.md` | The steps that moved the site to spottermcp.com, and the checks after |
| `BUILD_LOG.md` | How each stage was built, reviewed and checked live |
| `docs/contracts/` | Each stage's and each item's contract: what it had to do, fixed before its builders started |
| `tools/probe.py` | Checks a running server from outside, without a token |
| `tools/mcp_call.py` | Calls one function on a running server the way an assistant would, with a token read from the environment |
| `tools/load.py` | A load test, run only against a local database |

Tests: Python 3.12, `pip install -r requirements.txt -r requirements-dev.txt`, then `python -m pytest tests -q`.

## Contact

To reach the person who runs Spotter, use Contact at the foot of the live site. Problems with the code can go on the issues page.

## Licence

MIT for the code: see `LICENSE`. The cardio MET values, codes and activity descriptions come from the 2024 Adult Compendium of Physical Activities and keep its own terms: see `NOTICE`.
