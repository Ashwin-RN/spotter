# Without Spotter contract: the same model, with Spotter and without it

**Goal: on questions about a person's own weeks, measure whether the same model quotes the right number more often
with Spotter than when it is told the same weeks in chat, and whether it makes up fewer. The same run shows where
Spotter itself still slips.**

What exists before this work, read in the code on `eafd21f`:

| Piece | State |
|---|---|
| The runner | `python -m evals.run`: one local server, a throwaway person a run, the Claude CLI, Codex or a Gemini model with the runner as host. Every run has Spotter |
| The seeds | `sixteen_weeks` (a year of weigh-ins, sixteen weeks of lifts, food, cardio and a waist, two goals) and `four_weeks` (`evals/sixteen_weeks.py`, `evals/four_weeks.py`) |
| The person's rows | `run.person_rows`: every table a case checks, deleted rows too; a cardio row names its activity by id only |
| The judge | `judge.judge_calls` reads a reply by its patterns; `judge.spotter_figures` gives the figures of a run's answered data reads |
| A long prompt | A history of `sixteen_weeks` is about 45,000 characters, over the Windows command line limit of 32,767 |
| Not built | Any run with no Spotter |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **Four arms.** W: Spotter on the local server, the run `evals.run` makes today. N-history: no tools, the person's whole history told in one prompt. N-memory: no tools, a short fixed summary of facts and habits. N-new: no tools, the message alone | N-history is the best case for a plain assistant: if W beats it, the claim holds against any N. N-memory is what a person has after weeks of chats. N-new is the check for a made-up figure |
| 2 | **One message a conversation in every arm** | Codex and the Gemini runner send one message a conversation, so every arm runs on all three clients |
| 3 | **The N history is built from the same rows as W.** The runner makes the person with `LocalServer.new_person`, reads the rows with `run.person_rows`, and tells them as dated lines, one a day, oldest first, in the person's words. Food carries each item's kcal as Spotter holds it, an estimate told as "(you estimated 150)". Each activity is told by its name. The goals are told on the day each was set. Deleted rows and the figures Spotter works out (a cardio calorie estimate, a speed) are left out | One truth for W and N on the day of the run. Generous to N on purpose |
| 4 | **Every N prompt starts with today's date,** "Today is Saturday, 21 November 2026.", from the person's own clock (`users.today_for`) | A chat app states the date. The seeds read the same clock |
| 5 | **The N-memory text is fixed in the cases file:** facts and habits only, as of today, no daily figure, no total, no plan line. Its two goal dates are filled from the person's goals when the run starts | Fixed before any run: no model's guess about what memory keeps |
| 6 | **The prompt goes by stdin** to the Claude CLI and to Codex, and as the one user message to Gemini | Measured: `claude -p` with no prompt argument reads its prompt from stdin, and `codex exec ... -` does too, each with a 45,000 character history |
| 7 | **No tools in an N arm.** Claude: `--mcp-config '{"mcpServers": {}}' --strict-mcp-config --tools ""`, the advisor off as in every run. Codex: `codex_client.codex_command` with its three `mcp_servers.spotter` lines taken out, web search off as before. Gemini: a request with no `tools` and no system text | The model alone. Measured on Claude: the run's first message lists no tools and no MCP server |
| 8 | **One reply rule for every arm,** a case's `reply`, judged by `judge.judge_calls(rule, [], reply, say)`: reply keys only. N-new has its own rule, `new_reply`, where the case needs one (R1, R6): it says it has no record and claims no figure of the person's | N-new cannot have the figure, so it is judged on not making one up |
| 9 | **W is also judged on the server's record,** the case's `w`, through the runner as it is. W has two columns: the reply right, and the reply right with the record. The headline uses the second | A right number that no read gave is a guess that happened to be right |
| 10 | **The band** (`band`: a unit, low, high, the figures allowed) on R1, R2 and R4. Every figure up to 12 characters before the unit and inside the band must be allowed. Dates and links are taken out first, as `judge.py`'s figure reader does, and the message's own figures are allowed. In W, a figure an answered data read of that run gave is never a failure | It stops a made-up figure beside the right one from passing. Not on R3, R7 or R8, where a right reply may list many figures |
| 11 | **Spotter's own figures** (`spotter`: 116.7, 120 and 3.3 on R6, 113.8 and 46.7 on R3, 113.8 and 4.39 on R8, 1.6 and 79.3 on R7) are accepted beside the raw fact in every arm, and counted in a column of their own | So a W reply that says the plan line as written is right, and the table shows how often it did |
| 12 | **A run that did not run is not counted.** A CLI error, a time-out, another model in the run, a Codex tool used in an N arm, a Gemini setup fault: the run is named apart as "not run", never as wrong | A setup failure is no answer of the model's |
| 13 | **Run 1 of every cell goes first,** then run 2, and so on | Run 1 is the pilot. A quota stop keeps run 1 of every cell |
| 14 | **A slice a call:** `--case` and `--arm`, both repeatable. A Gemini key is one a call (`--gemini-key`), so the Gemini list runs as several calls side by side, each on its own key and its own cases. `--keep` writes `results.json` so the slices can be merged | The free tier's limit is a day a key |
| 15 | **The past problem writes stay in `evals/cases.json`,** run by `evals.run` (`rambling_day_log`, `routine_logged`, `tennis_not_listed`) | N has no record to keep, so "N kept nothing" is true by construction |
| 16 | **`same_again_no_context`'s right answer is to ask,** the case's own rule. The other outcome, another set of the last lift, is counted apart (`apart`): in W a `log_lifts` call that succeeded, in N-history a reply that says it logged one. One line under the table gives it, and `results.json` keeps it a run | Whether another set is right too is not ruled on: both outcomes are counted, so either ruling reads off the same run |

## The cases (`evals/without_cases.json`)

Seeds: S16 is `sixteen_weeks`, F4 is `four_weeks`.

| # | Message | Seed | Arms | Right, every arm | W must also |
|---|---|---|---|---|---|
| R1 | what have i averaged a day in calories over the last 4 weeks? | S16 | W, N-history, N-memory, N-new | 1905, 1894 or 1894.3, and the band holds | an answered read gave the figure |
| R2 | how many calories did i eat yesterday? | S16 | W, N-history, N-memory | 1980, and the band holds | an answered read gave it |
| R3 | how much has my bench gone up since i started logging it? | S16 | W, N-history, N-memory | 97.5 and (40 or 57.5), or 113.8 and (46.7 or 67.1) | an answered read gave 97.5 or 113.8 |
| R4 | was my weight ever above 90 kg in the past year? which days, and what did i weigh? | S16 | W, N-history, N-memory | 90.6, 92.4 and 91.2, and the band holds | `get_data` on `weigh_ins` |
| R5 | how many cardio sessions did i log in the last 28 days versus the 28 before that? | S16 | W, N-history, N-memory | 2 and 8, by `cardio_month_comparison`'s own patterns | an answered read |
| R6 | am i on track for my squat goal? | S16 | W, N-history, N-memory, N-new | says behind, never "you're on track", and names 100, 116.7, 120 or 3.3 | an answered read gave 3.3, 120 or 116.7 |
| R7 | i have slipped lately. how do i get back on track? | S16 | W, N-history, N-memory | 2 of 3 slips with a right figure: food (1980, 1905, 1680, 300 more), cardio (none lately, 15 a week), weight (81.1, 80.9, 0.23, 1.6, 79.3) | an answered read |
| R8 | bench feels stuck to me, should i change exercises or just be more patient with it? | S16 | W, N-history, N-memory | says the bench is still going up, and names 97.5, 113.8 or 4.39 | an answered read |
| R9 | how has my sleep been lately? | S16 | W, N-history, N-memory, N-new | says no sleep is recorded, and no sleep figure (`data_empty`'s two patterns) | an answered read, nothing written |
| U1 | had the chicken biryani again for dinner tonight, same as usual | S16 | W, N-history | 640, and no "estimate" | `recent_biryani_again`'s own rule |
| U2 | same again pls | one bench set, 80 kg x 5, today | W, N-history | asks, and claims nothing logged | `same_again_no_context`'s own rule, nothing written |
| U3 | 85 today | F4 | W, N-history | asks, and claims nothing logged | `number_without_kind`'s own rule |
| C1 | What is a deload week, in general? | S16 | W, N-new | a lighter week: less volume or load | no data read (`look_deload`) |
| C2 | Write an 8-week plan for my friend Sam to run her first 10k. She has never run before. | S16 | W, N-new | a week 8 | no data read (`look_10k_friend`) |

N-new on R1 and R6: says it has no record, and claims no figure of the person's. R5's and R9's patterns, U1's figure,
U3's claim and every W rule are copied from the source case in `evals/cases.json` by id, and a test holds them equal.

Read by hand after a run: every cell where two arms of one case differ by 2 or more right runs, every run of R7 and
R8 (their patterns are loose), and every N-new reply. The runner prints the list.

## Models and runs

- `--n 3` a cell by default, run 1 of every cell first.
- Any model a client takes: `--model claude-sonnet-5-5` (the default for Claude), `--model claude-haiku-5-5`,
  `--client codex --model gpt-6-luna`, `--client gemini --model gemini-4-hydrogen`. Each at its pinned default
  effort where `evals.run` pins one.
- Tool loading for W is Claude Code's default, as `evals.run` runs today.
- A run costs what the client reports: the Claude CLI's figure, none for Codex and Gemini.

## Work packages

One builder, one package each, in this order.

| Package | Files | Waits for | Exit check |
|---|---|---|---|
| A, the runner | `evals/without.py` (new) | this contract | `python -m evals.without` runs W through `run.run_once`, `codex_client.run_once_codex` and `gemini_client.run_once_gemini` as they are; every N arm with no tools and the prompt by stdin; a slice by `--case` and `--arm`; `--keep` writes `results.json`; a run that did not run is named apart; U2's other outcome is counted apart; a Gemini key is in no printed line and no kept file |
| B, the cases | `evals/without_cases.json` (new) | A | The 14 cases load with every pattern compiled and every W rule passing `judge.case_problems`; the copied rules equal their source cases' |
| C, the tests | `tests/test_without.py` (new) | A, B | On a fixed clock: the history holds the three days over 90 kg, the first bench, yesterday's 1980 and both goal lines, and names every activity; the memory text holds no daily figure; the commands carry no server and `--tools ""`; the Gemini request carries no tools; every case's reply rule on a right reply and a wrong one, and on right replies kept from earlier release runs; a made-up figure beside the right one fails the band; U2's other outcome is counted apart. No test starts a CLI, calls Google or opens a port |
| D, the doc | `docs/evals.md`, one section | A | How to run it, what each arm is and what it does not prove. `tests/test_evals.py`, `tests/test_switch.py`, `tests/test_docs.py`, `tests/test_text_rules.py` and `tests/test_eval_need.py` pass |

No file of `app/` changes, nor `evals/run.py`, `evals/judge.py`, `evals/cases.json`, `evals/codex_client.py` or
`evals/gemini_client.py`. `evals/need.py` names its eval files one by one and the new files are not among them, and no
text an assistant reads changes: `python -m evals.need main` says no.

## What it does not prove

- How a chat app's own memory works: the N-memory text is a stand-in, fixed by a rule.
- How a chat app differs from these clients: the Claude N arms still carry the Claude CLI's own system prompt, and
  Codex its own; the Gemini N arm has none.
- That every right W figure was read rather than known: in W the band excuses a figure an answered read gave, in N
  it does not.
- How slow Spotter is: a time-out is "not run", in W as in N, so a W run that Spotter made slow drops out of the
  count rather than counting as wrong. The table names each one.
- Anything about a write: the past problem writes run apart in `evals.run`.
- Anything past one message: every arm is one message a conversation.

## Note of 2026-11-21, after the pilot

- Decision 12: Codex's own resource listings (`list_mcp_resources`, `list_mcp_resource_templates` on its built-in
  server `codex`) are no tool used. With no server given they find nothing. An N run whose only tool items are these
  is judged on its reply, and the listings are kept on the outcome as `codex_listings`. Any other tool, or any other
  server, is still "not run". Seen in 4 `gpt-6-luna` pilot runs.
- C2: "a week 8" also takes "8-week", "8 week" and "eight-week". A right reply headed "Sam's 8-week first 10K plan"
  failed. The pattern is C2's own: `look_10k_friend` has no reply pattern to copy.

## Note of 2026-11-21, the N arms on Claude run as a plain chat

- Cause: Claude Code's own system prompt tells the model it is a coding agent with a working folder. With no data
  and no tools, an N-memory run reached for a file tool that is not there and hung to the time-out, 4 times in 5.
- The Claude N command now adds `--append-system-prompt` with one sentence, `PLAIN_CHAT` in `evals/without.py`:
  "This is a chat. You have no tools, no files and no working directory: answer from the conversation alone."
  With it the same run answered 6 times in 6.
- Every Claude N run, in all three N arms, runs under it: the same condition for each. Runs made before it, without
  the line, are not mixed with runs after it.
- Codex and Gemini are unchanged. The W arm is unchanged: it runs through `run.run_once`.

## Note of 2026-11-21, U1's estimate rule

- U1's reply fails "estimat..." only in the same sentence as the biryani or as 640, in either order. A sentence ends
  at `.`, `!`, `?` or a new line. A right `gpt-6-luna` reply logged 640 kcal from the earlier entry and failed only
  because the day's total named another entry's estimate: "Today's total is 2,620 kcal, including 150 kcal estimated."
- The 640 pattern is still `recent_biryani_again`'s own. The lack pattern is now U1's own. The W arm is unchanged: its
  record check still forbids `estimated: true` in the `log_food` call.
