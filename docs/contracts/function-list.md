# Function list contract (B6): one read for data, a lean list, evals on every vendor

## For the owner (one page)

**What changes for a person using Spotter.** Nothing they can see on the pages. In a chat, the assistant gets
Spotter's numbers in fewer calls and fewer seconds: one data read takes several questions at once, and the
function list an assistant reads shrinks from 173,365 characters to at most 50,000. Every number stays Spotter's own.

**The gates, in order.** Each gate is built, reviewed and judged against the gate before it on the same eval
cases, so a regression is traced to one gate.

| Gate | What it does | Changes behaviour |
|---|---|---|
| G1 | The evals: judged on Spotter's figures and the data read, not one route; calls, turns, seconds, tokens and cost a case; a golden set; Codex and Gemini runners; the second model taken out of Claude runs; a baseline on today's list | No: evals only |
| G2 | The list diet: no output schemas published, no auto titles, plain short wording, size budgets. Every answer unchanged | Only by the few rules moved from output schemas into descriptions: judged against G1's baseline |
| G3 | Routing: `get_data` takes several queries a call, closed lists named in its schema, 2 or 3 example calls, the instructions rewritten, the most used functions loaded up front. At least a day after G2 is live | Yes |
| G4 | Worked-out areas in `get_data` (lift bests, estimated max a session and its trend, the weight trend, every goal number), from the same code the pages and the old reads use, tested equal | Yes |
| G5 | Retiring `get_progress`, `get_trends` and `get_goals`, only if a test arm with them hidden is as accurate and not slower, over two deploys | Yes, or nothing |

**Yours to decide** (every open pick; the reasons are at the end):
1. Retire the three reads, or let G5's test arm decide. Pick: the test decides.
2. "As accurate and not slower", in that test and at every gate. Pick: G1 runs its baseline twice on each model;
   the spread between the two runs is the allowed band for passes, seconds and tokens, recorded here before any gate
   result is seen; a gate passes inside the band or better. G5's arm test runs 6 a case, not 3. **Changed after your
   yes:** the first rule, total passes not fewer, fails on noise (at a 95% pass rate, 180 runs move by about 3
   passes either way with nothing changed).
3. Output schemas: unpublish now, delete the result models only after G5. Pick: yes.
4. An eval comparison at every gate, not one a release. Pick: yes, small runs on the second tier; the full run on
   the top tier before each merge for which `python -m evals.need main` says yes (expected: G2, G3 with G4, and
   G5's first deploy, or its only one if the three stay).
5. Tiers and who pays. Pick: Sonnet 5.5 and a small GPT every gate (Claude side and your ChatGPT plan); Opus 5.5
   and gpt-6.1-sol at each release; Hydrogen now and then on your free key. G1's baseline, run twice: about 720
   second-tier runs plus 66 on Opus (about $12 at the last run's Opus rate); Sonnet's cost is measured on its first
   runs and said before the rest. The small GPT is pinned by name, and every tier's reasoning effort is pinned, before
   the baseline; a first 10 Codex runs show what your plan allows.
6. Loaded up front in Claude Code. Pick: `whoami`, `get_data`, `log_food`, `log_lifts`.
7. `get_data` takes 1 to 5 queries a call. Pick: yes.
8. The new areas' names. Pick: `lift_bests`, `lift_progress`, `goals`, totals `trend` and `per_week`.
9. Your goal and target words. Pick: no rename in B6, all of them in B5's own two deploys. This differs from the
   card, which put them in B6's deploys: B5 moves the daily and weekly amounts anyway, so renaming now would rename
   twice.
10. The writes out of B6. Pick: the one body write, several dates a call, one confirm step and a field list for
    writes become their own item after B6, as do age, gender and a first name, and account plans. The card had the
    body write and the confirm step in B6.
11. The order after G1. G1 touches only `evals/` and tests, so it may start now. G2 opens `app/mcp_server.py`, which
    the hardening items (H1, H2) and the account foundations also touch (the wrapper). Your pick on their order is
    needed before G2 starts.
12. The golden set: about ten real questions of yours where Spotter is one step of a bigger question, and your yes
    on a one-page table of the cases (question, kind, must call, must not call) before the baseline runs.

---

Status: for the owner's yes. Built on `2718899` (the evals' advisor fix, on top of `f8385d6`, data on request live);
`main` is now `52d2e2b`, which adds only `docs/domain-move.md`. `app/` and `tests/` cites are read on `f8385d6`, equal
in `app/` to `2718899`; `evals/run.py`, `docs/evals.md` and `tests/test_evals.py` cites are read on `2718899`. Read on
2026-11-17. The builder reads the rows of "What exists today" again on the commit it starts from.

## His words

- On the list (2026-11-17): "lets not have 2 calls for the same data then. lets streamline functions and retire ones
  that add no purpose. if the model needs to build the call let it. keep dedicated functions only if its used very
  often and if it would save time overall.. otherwise lets get rid of the repeat tools/functions in the list"
- On the target: "what we need to do is have a comprehensive desc that allows the model to build a data req cleanly to
  answer generic enough questions. that was the target here."
- On the study: "don't we think we should probably reval exactly which functions are needed and for what? If you can
  build it with get data, how to convey that to the model cleanly ... Maybe even restructure the entire functions list
  with the instructions based on how to maximize utility here, plus grow smoothly from here onwards."
- The principle: "We don't want to have multiple pathways for the same information because it just complicates logging
  and tracking and stuff like that. We don't know where things are going wrong, and we are not in control of their AI
  either. So we want to fine scope and optimize from our end as much as possible."
- The list diet, his yes ("cool, add that to the B6 contract"): "We should be more concerned about the input schema
  that the AI needs to structure its sends through, right? Rather than the output." On wording: "Reduce on wordage,
  retain the meaning. That way token consumption reduces and also speed increases."
- On the result models: "please retire the MCP schema spy file in itself then. What we need to know is just the rules
  of what is required when we include a new functionality and how to include it within our existing insturctions."
- On vendors: "If it works on both of these, it works everywhere." On tiers: "From now onwards, I think we can actually
  reliably hope to use Sonnet and like tier 2 models effectively across the board for our testing, right?" On Gemini:
  "gemini-4-hydrogen"; "carbon and argon arent free. dont bother trying now. I said hydrogen because I am pretty sure
  they will drop 3.8 soon"; then "If it works on Flash, it'll probably work on Hydrogen if they use it through the app.
  So I'm least bothered on that front ... If it's working on the demo models on Claude and Codex, it should work on
  Google too, like 90%. And no one really uses the Gemini app like that anyway."

## What exists today

| Piece | State | Where |
|---|---|---|
| The list | 24 functions for everyone, 27 for the owner; the owner's three hidden from others by a filter on `tools/list` | `app/mcp_server.py:249-264`, `:595-598` |
| A function and `NEEDS` | `build_server` refuses to start when the registered functions differ from `NEEDS` | `app/mcp_server.py:1566` |
| Output schemas | Every function returns `Annotated[CallToolResult, Model]`; the library publishes `Model` as `outputSchema` and validates against it. A plain `CallToolResult` return publishes none and passes the result through | `app/mcp_server.py:799` and each function; `.venv/Lib/site-packages/mcp/server/mcpserver/utilities/func_metadata.py:203-207`, `:416-434` |
| The result itself | `Wrapper.call` builds both copies, `content` text and `structured_content`, from the service's dict, and checks a success with `model.model_validate` | `app/mcp_server.py:688`, `:715` |
| `_meta` on a tool | `server.tool` takes `meta` | `.venv/Lib/site-packages/mcp/server/mcpserver/server.py:676` |
| Unknown keys | Every function publishes `additionalProperties: false`; `call_tool` refuses a key not published, before the library | `app/mcp_server.py:602-617`, `:1569` |
| `app/mcp_schemas.py` | 1,572 lines, 114 classes: the input half (`SetIn`, `RoutineLiftIn`, `FoodEntryIn`, `ImportSetIn`, `ImportFoodIn`, `ImportRowIn`, `Number`, `Whole`, `whole_in`, `NEAR_CUTOFF`, `FOOD_NAME_RULE`) then the result half (`Projection` on), and `RESULTS` | `app/mcp_schemas.py:1544` |
| Who imports it | 13 files by `git grep -l mcp_schemas -- app tests evals tools`: `app/calllog.py`, `app/mcp_server.py`, `app/services/data.py` and 10 test files; 10 test files assert on `outputSchema`. The card's "28 files" is not what this grep finds | command run 2026-11-17 |
| The instructions | 4,011 characters, pinned in 8 test files; the first read routing line starts at character 827 and `DATA_RULE` at 1,003 (the study's static count, not recounted) | `app/mcp_server.py:205-230`, `tests/test_switch.py:567`, `tests/test_data.py:1094` |
| Routing to the old reads | `TRENDS_RULE`, `GOALS_RULE`, and `DATA_RULE`'s "For any other question", which frames `get_data` as the fallback | `app/mcp_server.py:177-181` |
| `get_data` | One query a call; `filters` and `totals` are open objects; no enums | `app/mcp_server.py:1425-1466` |
| `what_is_logged`'s answer | 10,154 characters on an empty account (the study's probe); 11,514 on the `sixteen_weeks` seed (the contract re-check's run), of it 9,886 the area catalogue: field meanings 3,060, the rest units, totals, marks and counts. The same catalogue as a one-line-an-area index of fields, marks and totals, without meanings: 1,453 | measured 2026-11-17 |
| The old reads' exact paths | `get_progress(..., exact=True)`, `get_trends(..., exact=True)`; goal numbers unrounded in `goal_numbers`, shown by `rounded_numbers` | `app/services/progress.py:121`, `app/services/trends.py:172`, `app/services/goals.py:386`, `:472` |
| Formulas the old reads own | Epley per session, the 6-week least-squares slope (4 points, 2 dates), the smoothed weight (EWMA, 0.1) and its rate, maintenance over 21 days | `app/metrics.py:292-355`, `:531-570`, `:602-620` |
| Texts naming a read to retire | `KIND_LABELS`, `READ_BACK`, "get_progress shows them", "get_goals has them all", `set_goal`'s `goal_id` | `app/services/common.py:96`, `:106`, `app/services/entries.py:104`, `app/services/data.py:852` |
| The web | Reads `NEEDS` for the landing's count; the hi page's "How is my bench press going?" line describes `get_progress`. The pages call the services, never the MCP functions | `app/web.py:1234`, `:1293` |
| The exercise resource | `spotter://exercises`, "the same as list_exercises with no filter" | `app/mcp_server.py:1554-1562` |
| Evals (on `2718899`) | 51 cases; the runner runs `claude -p` pinned to `claude-opus-5-5`, 300 s a run; the judge's keys. Each outcome already records one token figure, `cost_usd`, `num_turns`, `seconds` and `models` | `evals/run.py:33`, `:76`, `:79-85`, `:339`, `:352`, `:368`, `evals/judge.py:77-80` |
| The advisor in eval runs (`2718899`) | Off in every Claude run: `CLAUDE_CODE_DISABLE_ADVISOR_TOOL` in the child's environment and an empty `advisorModel` by `--settings`; a run in which any model but the one asked for took part fails, a small helper model too (`other_models`, `model_reasons`). Its notes record that `--disallowedTools advisor` did not turn it off | `evals/run.py:43-51`, `:82`, `:92-116`, `:374` |
| What the evals see of an answer | The recorder keeps a call's name, arguments and whether it was an error, the error code read from `structuredContent`; not the answer | `evals/recorder.py:137-151` |
| A client's output schema check | The MCP Python SDK raises when a tool whose cached list carries an output schema answers with no structured content, and checks a structured answer against that schema. Claude Code, claude.ai and Codex: not checked | `.venv/Lib/site-packages/mcp/client/session.py:1135-1141` |
| Cases bound to a read to retire | `how_am_i_doing`, `decimals_and_dashes` (`get_trends`), `on_track` (`get_goals`); `data_lifting`, `data_squat_goal`, `data_lb`, `data_back_on_track` accept one | `evals/cases.json` |
| Live call log | One account's testing, read by the lead: its counts decide no keep or retire here | |

## Measured

Everyone's 24 entries of `tools/list`, compact JSON as `tests/test_feedback.py:144` measures, on a tree equal in
`app/` to `f8385d6` (no `app/` change since `e6174f7`, `git diff --stat`).

| Cut | Characters |
|---|---|
| Today | 173,365, of it 117,224 output schemas |
| No output schemas | 55,757 |
| Also no auto titles inside the input schemas | 51,838 |
| Also less `get_progress`, `get_trends`, `get_goals` (608, 637, 708) | 49,885 |
| The card's 51,266 | Also dropped the 24 tool titles (308 characters plus 11 of key each, 572). `tests/test_mcp.py:478` requires a title: titles stay |

| Input schema, no titles | Bytes | Codex compacts past 5,000 |
|---|---|---|
| `import_entries` | 5,728 | over |
| `edit_entry` | 4,283 | under |
| `get_data` today | 2,003 | under |
| `get_data` batched, a pydantic prototype: 1 to 5 queries, area, group and total names published as enums but not enforced (R5), `filters` an open object as today | 2,179 | under |

These figures are sums of the 24 entries; the `tools/list` JSON itself is 25 characters more (173,390). The budget
test measures entries.

**The second model in Claude runs.** The release run's kept output (51 cases at 3 runs, $35.23; all 153 run outputs
kept): 11 of 153 runs list
`claude-fable-5-2` in `modelUsage` beside `claude-opus-5-5`: `data_basketball` 1 and 3, `data_calories` 1 and 2,
`data_lifting` 1 to 3, `data_back_on_track` 3, `delete_regretted` 1 to 3. In those 11 it cost $6.55 against Opus's
$4.75, so those runs cost about 2.4 times what Opus alone did. `data_lifting` run 1: Opus 313,235 cached input tokens,
the second model 69,002 uncached input and 5,067 output, $0.75 against $0.58. Its `usage.iterations` holds an item of
type `advisor_message` on `claude-fable-5-2`; 5 of the 11 runs show one. So an advisor step of the CLI ran on a second
model inside those runs. A tier's figures are that tier's only once it is off.

**2026-11-17, `2718899`:** the advisor is off in every Claude run, and a run in which another model took part fails
(`evals/run.py:43-51`, `:92-116`). `--disallowedTools advisor` did not work. G1 keeps both and builds nothing more for
them; G1's one-model exit line is that rule.

## Decisions

Decided by him: the list diet (D1 to D4), the result models retired (D5), the vendor tiers (D11). The rest are the
lead's picks, for his yes.

| # | Decision |
|---|---|
| D1 | No output schema is published. Every answer stays self-describing JSON |
| D2 | Input schemas carry no auto `title` keys inside them; tool titles stay. Plain field words; closed lists named in the schema (R5) |
| D3 | Plain short wording: common words, one rule a line, no rule said twice, no filler. Not caveman: "never", "only", "unless" stay; no abbreviations |
| D4 | A size budget test per function and for the whole list, and smaller answers (`what_is_logged` first) |
| D5 | The result models are retired. Pick on timing: unpublished in G2, deleted after G5 (Open 3). The input models stay, in `app/mcp_inputs.py`, and `app/mcp_schemas.py` goes with the result half |
| D6 | `get_data` is the one data read: several queries a call, its closed lists named in its schema, 2 or 3 example calls, the areas and fields named in its own entry, no longer the fallback |
| D7 | `what_is_logged` is the menu of what this person holds: counts, dates, names, open goals, the daily and weekly amounts, maintenance and the estimated share. Not needed before `get_data` |
| D8 | Worked-out areas for what only the old reads give, from the old reads' own code |
| D9 | Retiring the three reads is tested, not fixed: G5's arm decides. Live counts decide nothing |
| D10 | `whoami`, `get_log`, `get_routines`, `list_exercises`, `find_food` stay. `spotter://exercises` goes in G5's first deploy, whatever the arm test gives |
| D11 | Evals: Sonnet 5.5 and a small GPT at every gate; Opus 5.5 and gpt-6.1-sol at the release; Gemini Hydrogen now and then, not a gate |
| D12 | No word that may flip: B6 adds no published name in either sense of goal or target (as `docs/contracts/data-on-request.md:15` ruled). The goal numbers area is named `goals`, the word `set_goal` and the rider use today. B5 renames them all in its own two deploys |
| D13 | A short repo doc, `docs/adding-a-feature.md`: where a new field, area, input and rule go, the budgets, the eval cases on each tier |
| D14 | **A later paywall gates inside the call, never by the list:** a running chat keeps the list it began with. A function, area or total that may one day be paid answers a refusal (such as `not_in_plan`, with a fix and a link) from the one place `not_allowed` is checked today (`Wrapper.call`, `app/mcp_server.py:663-666`). B6 adds no second check point: a later gate on a `get_data` area reads the sent queries in that same step, never in `fields.py` or a loader. Plans themselves are not built here |

## Rulings

| # | Ruling | Why |
|---|---|---|
| R1 | **Unpublish by the return type.** Each function returns `CallToolResult`, not `Annotated[CallToolResult, Model]`. Until the models are deleted, `Wrapper.call` still checks a success against its model (`app/mcp_server.py:688`) | The library publishes nothing for a plain return (`func_metadata.py:416-434`); the shape check stays while shapes change most |
| R2 | **Titles stripped at build,** where `additionalProperties` is set (`app/mcp_server.py:1569`): every `title` key in `tool.parameters` and its `$defs`, never a property that is named `title` | One place, no per-model config |
| R3 | **G2 always sends both copies,** `content` text and `structuredContent`, as now. G2 also runs the marker probe of 2026-11-17 again, with the output schema off. Outcome A: Codex shows one copy: nothing more. Outcome B: Codex shows both: a content-only answer is its own later item, deployed at least a day after G2 is live, and only if the same probe shows Claude Code then reads the text (on 2026-11-17 it showed only `structuredContent` with both present). Its files: `evals/recorder.py:150-151`, `evals/need.py:112`, `tools/load.py:160`, `tools/mcp_call.py:118` and the test helpers (96 reads of `structuredContent` in 14 test files) | A client holding a list from before G2 caches the output schemas, and the MCP Python SDK raises on a structured-less answer to such a tool (`session.py:1135-1140`); MCP 2025-11-25 says a structured result SHOULD also come as text |
| R4 | **`get_data`'s inputs:** `queries`, 1 to 5, each with `area`, `fields`, `filters`, `from_date`, `to_date`, `group_by`, `totals`, `order`, `limit`, `cursor` as today. Null is left out (data-on-request R2) | One call answers a question that needs several reads, on every client |
| R5 | **Closed lists named, not enforced.** `area`, each `group_by` item and each total carry an `enum` published by `json_schema_extra`, built at build from `fields.AREAS`, from `TIME_GROUPS` plus every field of every area that groups, and from `TOTALS`. Published, not enforced (a pydantic check on 2026-11-17: the prototype took `area: "lift_set"`), so a wrong value still reaches the service and gets today's refusal word for word, with its nearest name: `_problem` has no branch for a `literal_error` (`app/mcp_server.py:497-549`), so an enforced enum would get the schema refusal's words (the contract review's probe: "area must be text, not 'lift_set'."). An item that groups in one area but not the one asked keeps the service's per-area fix. `filters` stays an open object, its values matched by the service as today (case and outer spaces forgiven, `app/services/fields.py:219-229`). Field names stay strings | A new area or field stays one entry; the published entry follows by itself. **A departure** from data-on-request's scaling rule ("Neither function, nor its published entry ... names one"): the entry now names them, built from the list, so a new field changes model-read text and owes an eval run |
| R6 | **The answer:** `{results: [one answer a query, in order], targets}`: each result is today's answer less `targets`, which comes once at the top; the open goals ride on each query's result as today. **One refused query refuses the call:** every problem of every query comes back at once through `rules.combine` (`app/rules.py:318-328`), each message led by its query's place, such as "queries[2]: area 'lift_set' is not an area Spotter reads.", the rest of each message and every fix word for word as today, and nothing is read. So `results` holds successes only and its model has no error item; the call is an error by the top-level `error` as today (`app/mcp_server.py:683`); the call-log code is the first problem's, as `combine` gives it; and `read_any` counts the call as no read. The old shape (R7) has no prefix. The 30,000 character ceiling holds for the whole answer: each query's page is cut at 30,000 divided by the number of queries, with its own `more` | Every query keeps its own cursor; one rule, no state |
| R7 | **The old shape, one more deploy.** A `get_data` call with a top-level `area` and no `queries` is taken, before the unknown-key check, as one query, and answers today's single shape. Not published. Refused from G5's last deploy, whatever the arm test gives, and at least a day after G3's deploy | A chat begun before G3 holds today's schema (`app/mcp_server.py:602-617` refuses unpublished keys) |
| R8 | **`what_is_logged` slimmed:** areas with no entries are named only; an area with entries gives its counts, dates, names logged, its fields' filled counts, and a note for each field whose rule its name does not say (`muscle`'s weights, `value`'s one kind, a pace's avg, a share), with `how` once. Field names, filter and group marks and each field's totals live in `get_data`'s description as the index (1,453 characters for today's 8 areas, measured on a kept answer; the index depends on the field list only, not on the seed); every other meaning is dropped, the name saying it. Answer budget: 1,500 characters on an empty account, 6,000 on `sixteen_weeks` | 10,154 characters today on an empty account, and every answer stays in the conversation |
| R9 | **Equal means:** a worked-out figure equals the old read's from the same exact path (R10's sources), then each is shown at its own step: the old read's kg fields at theirs, `get_data`'s in the person's units at its quantity's step. The test compares the exact values, and the shown ones on a kg account where both steps are the same | The study saw 80.7 from `get_trends` and 80.87 from `get_data` on one average: different steps, not different sums |
| R10 | **No second formula.** Each worked-out area's loader calls the code the old read and the pages call: `lifts.history` (`app/services/lifts.py:159`), `metrics.slope_per_week`, `metrics.ewma` and `trend_rate`, `metrics.maintenance`, `goals.goal_numbers` | One figure, one place |
| R11 | **A retired read in G5's first deploy** is registered, hidden from every list by the `list_tools` filter, and answers `{error: "retired", message, fix}`, the fix naming the `get_data` call that gives its figures. Its `NEEDS` entry stays until the second deploy | `build_server` needs `NEEDS` equal to the registered functions; a client holding the old list gets a fix, not a protocol error |
| R12 | **Loaded up front:** `meta={"anthropic/alwaysLoad": True}` on `whoami`, `get_data`, `log_food`, `log_lifts`. Each of their descriptions stays within 4,096 characters | Claude Code defers MCP tools and searches before first use; its cap for an up-front description is 4,096 |
| R13 | **The instructions' first 512 characters** carry the core: whoami first, data questions to `get_data` with every query in one call, writes to the log functions, independent calls together, Spotter's numbers quoted. **G3's core keeps the routing to the old reads,** so G5's arm A is G3's text as shipped. Draft, 503 characters: "Spotter is the person's own fitness record. Call whoami first: it gives their date today and their units. How the person is doing: call get_trends. Whether they are on track: call get_goals. Any other question about their data: call get_data, with every query it needs in one call. To record something, use the log functions, and leave the date out for today. Make calls that do not depend on each other together, in one turn. Quote Spotter's numbers as given, with their units: never work out your own." **Arm B's core,** shipped only if B holds, 486 characters: "Spotter is the person's own fitness record. Call whoami first: it gives their date today and their units. For any question about their data, call get_data, with every query the question needs in one call. To record something, use the log functions, and leave the date out for today. Make calls that do not depend on each other together, in one turn. Quote Spotter's numbers as it gives them, with their units: never work out your own. If a call returns an error, read its fix and retry." The builder may tune either. The whole stays under 4,096 | Codex and ChatGPT weigh the first 512; Claude Code cuts at 4,096 |
| R14 | **A rule goes** in the function's own description when it is about that call; in a refusal's fix when Spotter can see the case; in the instructions only when it must hold before any call | The instructions are at 4,011 of 4,096 |
| R15 | **Two deploys for a result change.** G2's deploy unpublishes the output schemas with every result unchanged: it is the "optional first" deploy of CLAUDE.md's rule. No result field is removed or changed (R6, R8 on) in the same deploy as G2: G3 ships at least a day after G2 is live | A client holding a list from before G2 still holds its output schemas, and the SDK checks a structured answer against them (`session.py:1141`) |

## The rules now only in output descriptions

Everyone's output schemas hold 692 field descriptions, 45,690 characters (698 and 46,223 with the 6 model
docstrings), 473 distinct texts. A word search for rules (say, quote, send, use, ask, never, do not, show, tell, call,
only, instead, must, should, read, offer, walk, put, leave, retry), case-blind and by whole word, flags 88 of them; a search misses a rule worded
otherwise, so the G2 builder reads all 473. Each group below was searched for a second home in model-read text.

| Group | Count | Second home | Fate |
|---|---|---|---|
| "Put links last in a reply" on every `links` | 17 | the instructions, "Put links last." (`app/mcp_server.py:228`) | Dropped |
| "ready to quote" on a summary or text | about 25 | `REPLY_RULE`: "Where a summary gives a figure, say the summary's figure." | Dropped |
| `duplicate` "Say so." | 6 | the instructions' duplicate sentence | Dropped |
| `units_note` "Ask once, as it says." | 6 | the note's own text, "Ask once" (`app/services/common.py:575`, `:578`) | Dropped |
| Delete preview and `confirm` | 2 | `DELETE_RULE`, `confirm`'s input description | Dropped |
| Goal numbers (`ahead_by`, `should_be_today`, `plan_line`, `OpenGoal.plan_line`) | 4 | `REPLY_RULE`'s plan_line sentence | Dropped; the meanings go to the `goals` area's field meanings (G4) |
| `whoami`: `time_zone_note`, `today`, `claude_can`, `setup` | 5 | the instructions and `whoami`'s description | Dropped |
| `Goal.direction` "to aim the other way, drop it and set a new one" | 1 | refusal fixes (`app/services/goals.py:743`, `:803`) | Dropped |
| **Only here:** `whoami.units` "A number the person says with no unit is in these units" | 1 | partly in four input fields (`app/mcp_server.py:863`, `:866`, `:1239`, `:1242`) | Moved to `whoami`'s description |
| **Only here:** `links.hi_there` "Give it when ... the person asks what Spotter can do" | 1 | `docs/can-and-cant.md:440`, a resource never read in 153 runs | Moved to `whoami`'s description |
| **Only here:** `saved_food_name` "Show the entry as this name, the servings and the serving" | 1 | none | Moved to `get_log`'s description |
| **Only here:** `CardioEstimate.source` "Name it when you quote the estimate" | 1 | none | Moved to `log_cardio`'s description |
| **Only here:** import `ranges` "Show them in a preview: a wrong unit shows here" and `within_import_limit` "False in a preview means the real run would be refused" | 2 | none | Moved to `import_entries`' description |
| **Only here:** `SetGoalResult.warnings` "Things to tell the person" | 1 | none | Moved to `set_goal`'s description |
| **Only here:** `get_data.range_note` "say which range this is" | 1 | none | Moved to `get_data`'s description |
| **Only here:** `log_food` `note` "Present when an entry is your estimate, or on a duplicate: what to tell the person" (`app/mcp_schemas.py:669`) | 1 | partly `FOOD_RULE` and the duplicate sentence | Moved to `log_food`'s description: say the note |
| **Only here:** `Maintenance.kcal` "An estimate: it never sets the target" | 1 | none | G2: into `get_trends`' description; G4: with maintenance into `what_is_logged`'s answer |
| `get_trends` `note` "what to tell the person" | 1 | none | Retires with `get_trends`, or stays if G5 keeps it |

## The gates

Items touching the same files go one after another. G1 touches `evals/`, `docs/evals.md` and test files only, no
`app/` file; G2 to G5 all touch `app/mcp_server.py`, so they build in order, each after the one before is committed. Each
gate: one builder, a reviewer on a frozen copy, one fix round, its own eval comparison, a commit.

**Every gate's comparison:** its case list, 3 runs a case, on Sonnet 5.5 and the small GPT, against the gate before
on the same cases. G2: every case, since it rewords every description. G3 and G4: the golden set and every case whose
G1 baseline runs call a data read. G5: its arm set (below). Reported a case and in total: passes, calls, turns,
seconds, tokens, and cost where the client gives one.

**The band, one rule at every gate and in G5's arm test.** G1 runs its baseline twice on each model, the same cases
at the same runs. For each model, the spread between the two runs, in total passes, median seconds and median tokens
over all runs of the set, is the allowed band. It is written into this contract's record as a dated note before any
gate's result is seen, and never moved after. A gate passes on a model when, against the gate before, its passes are
fewer by at most the band and its medians higher by at most the band, and never below the lower of G1's two runs
(so small slips cannot add up across gates). Better than the band always passes. A gate that falls outside it stops,
and the owner is told. Why: at a 95% pass rate, 180 runs move by about 3 passes either way with nothing changed
(the square root of 180 × 0.95 × 0.05 is 2.9), so a rule of "not fewer" fails on noise.

**2026-11-17, after the owner's yes on the 10 picks:** pick 2 changed from "total passes not fewer, medians at most
10% above" to the band above, for that reason.

### G1: the evals (medium)

| Piece | What |
|---|---|
| Judging | `evals/recorder.py` reads each answer but keeps only whether it was an error (`:137-151`): it now keeps each data read's answer too. Two new expect keys, both over the data reads only: `what_is_logged`, `get_data`, `get_log`, `get_progress`, `get_trends`, `get_goals`; never `whoami`, `list_exercises`, `find_food` or `get_routines`. `read_any`: at least one data read answered without error. `quotes_spotter: n`: the reply holds at least n figures that appear in this run's data read answers, texts included, each matched whole with the boundary `reply_has_pattern` uses; dates, ids, links, a figure in the case's `say` and a bare number under 10 do not count. Each case states its n; a negative case states 0 and a `reply_lacks_pattern` for a made-up figure of its kind, as `data_empty` does. Data cases judge "Spotter's data was read and its figures quoted" by these two, so no typed list of figures can miss a right reply; `reply_has_pattern` stays for the figure a case asks about: every direct golden case carries one, in an `any_of` across the routes that reach it, so a reply that quotes other counts ("364 weigh-ins and 48 lift sessions") does not pass "What is my squat this month?" (the re-check's probe); 214.95 on a lb account is another. A route is judged only where the route is the point (a delete's two calls, an import's preview). The release miss it fixes: `data_basketball` run 1 read `what_is_logged`, `get_trends` and `get_data` on cardio and quoted the squat plan line as written, and failed on "no get_data call on lift_sets" and no 33% or 67% |
| Route-free re-judge | `how_am_i_doing`, `decimals_and_dashes`, `on_track`, `data_lifting`, `data_squat_goal`, `data_lb`, `data_back_on_track`: the figures and no wrong write, any read. `drop_goal`: the goal's id may come from `get_goals`, `what_is_logged`'s goals or (after G4) the `goals` area |
| Metrics | Each outcome already records one token figure, cost, turns, seconds and models (`evals/run.py:339`, `:352`, `:368`, on `2718899`). New: the calls count, tokens split into input, cached and output, and `summarise` printing them a case and in total, a model line per client |
| The second model | Done in `2718899` (the dated note under "Measured"): kept as built, nothing more to build. Before the first Sonnet run, note that the rule also fails a run in which a small helper model took part (`evals/run.py:95-96`): that run's reason says so, and the baseline reports it |
| `--client codex` | From the trial's `codex_client.py` (`codex_command`, `parse_codex_jsonl`, `run_once_codex`): the vendor `codex.exe`, `exec --json --ephemeral --skip-git-repo-check --ignore-user-config -s read-only`, the features off, `mcp_servers.spotter.url`, `bearer_token_env_var`, `default_tools_approval_mode="approve"`, `-m` pinned; the token only in the child's environment |
| `--client gemini` | From the trial's `gemini_run.py`: the server's instructions as `systemInstruction`, each tool's input schema as `parametersJsonSchema`, calls one after another, every model part sent back as received. One key a run from `.env.evals` (git-ignored, `.gitignore:10`), never printed; a 429 that persists after one back-off stops that model; 300 s a run; **a cut-off run keeps its usage, finish reasons and back-off log** (the trial lost them) |
| Model names and effort | `claude-sonnet-5-5`, `claude-opus-5-5`, `gpt-6.1-sol`, `gemini-4-hydrogen`, and the small GPT, pinned by its exact name from Codex's own model list before the baseline (its name: not checked). Every tier's reasoning effort is pinned too, by each client's own flag or setting (which one: not checked; the Codex trial reported 0 reasoning tokens a run), and written in `docs/evals.md` |
| Codex first | Before the baseline, a first 10 Codex runs (cases from the golden set) to see what his ChatGPT plan allows in a five-hour window: their count, seconds and any limit hit go in the build log, and the baseline is paced to fit |
| New cases | The golden set (below, as G1 rules it), a catch-up case and an import case, as today's list answers them: the write item after B6 is measured against them |
| Baseline | On `f8385d6`'s app, **run twice on each model**, so the band can be read: every case at 3 runs on Sonnet 5.5 and the small GPT; the golden set on Opus 5.5 and gpt-6.1-sol; the golden set once on Hydrogen when the free tier answers, outside the band. The band goes into this contract as a dated note before G2's comparison is run |
| Files | `evals/run.py`, `evals/judge.py`, `evals/recorder.py`, `evals/cases.json`, `evals/codex_client.py` (new), `evals/gemini_client.py` (new), `docs/evals.md`, `tests/test_evals.py`, `tests/test_eval_clients.py` (new), and the case order pins, which count from the end: `tests/test_inputs.py:1225`, `tests/test_hi_there.py:1330-1332`, `tests/test_routines.py:1279-1281`, `tests/test_units.py:845`, `tests/test_sixteen_weeks.py:295` (51 cases, the last 8 in order). `tests/test_sixteen_weeks.py`'s kept runs are judged both ways and hold no read answers: each gains the answers it needs, or keeps keys that need none. No `app/` file |
| Tests | Each client's parser on a kept transcript, its command never holding the token, a cut-off Gemini run keeping usage; `read_any` and `quotes_spotter` on kept runs, a figure from `say` not counted; every new case loads and judges a passing and a failing run; the re-judged cases pass their kept passing runs; every case carries the three call tags; **the coverage test** (below); the pick-up and over-call rates worked from a kept run |
| Exit | The owner's yes on the golden set's table, before any baseline run. The baseline report in the build log, both runs, with each model's cost by side and the band. Every Claude run passes `2718899`'s one-model rule |

**2026-11-17, G1's fix round (its review, and the owner's and the lead's rulings):**
- Files: `evals/need.py` (its eval files gain `evals/recorder.py`, `evals/codex_client.py` and
  `evals/gemini_client.py`), `evals/__init__.py` and `tests/test_eval_need.py` join G1's list.
- The figure a direct case asks about is checked by a new key, `quotes_one_of`: a figure a data read of that run
  gave, whichever read. It replaces the `reply_has_pattern` in an `any_of` of routes above: route-free, the same aim.
- `quotes_spotter` never counts the fixed help texts (each read's `how`, `what_is_logged`'s meanings and field
  words), and never a whole number under 10, with a unit or without; a decimal counts.
- Each tier's reasoning effort is the model's own default, pinned by its flag (his word: "yes, pin the defaults"):
  medium for `claude-opus-5-5` and `claude-sonnet-5-5`, low for `gpt-6.1-sol`, medium for `gpt-6-luna`, high for
  `gemini-4-hydrogen`. Each source is in `docs/evals.md`.
- A catch-up told in chat goes through the log functions, one path a source: `import_entries` is a `must_not` on
  `catch_up`.
- Gemini runs are paced to 5 requests a minute, the free tier's limit; the persistent 429 stop stays.
- The route-free cases give `get_goals`, `get_log`, `get_progress` and `get_trends` no case of their own. The evals
  measure the model's choice; a read's correctness is the unit tests' job.
- The free tier is the measure (his words: "The free tier is the most important tier for us when we are testing"):
  Sonnet 5.5, `gpt-6-luna` and `gemini-4-hydrogen`, with tokens and cost per model and per case in every report.
  Opus 5.5 and `gpt-6.1-sol` run at the baseline and the release.
- The golden set grows by a package of its own after G1: 170 reviewed cases (Codex's 164 candidates checked against
  the code, 27 dropped as overlaps, 33 added). His pick: a core of 81 at each gate, the full set at the baseline and
  the release. As built: 164 of the 170 went in (5 repeats of G1's cases, `account_clock` unjudgeable). The core is
  101, not 81: the 77 new core cases and 24 of G1's cases (17 of them golden cases), which the reviewed list kept in
  place of its repeats, 606 runs a gate on the two free-tier models. The owner agreed to 81; 101 is open for him.

**2026-11-17, the judge, a fresh review after the golden package was built.** The same kind of hole had come back
after three fix rounds, so a fresh reviewer probed the judge with aimed runs, no model run behind them: 125 of 136
wrong runs written by hand passed, 55 of 59 made by rule passed, and 44 of 76 right runs in other words or more
calls failed. Where the judge reads the record (rows, call arguments, a figure tied to the run's answers) it held.
Where it reads the reply's words, each case carries its own patterns: a fix to one never reaches the others, and no
list of phrasings is complete in either direction. The main holes:

- A lift's sets and the person's settings are never compared: wrong sets with the right count, and a write nobody
  asked for, pass.
- A write's answer is not kept, so a preview of the wrong rows or entry passes.
- A made-up figure beside the right one passes every figure case.
- A claim of done in other words passes, and right replies that say nothing was done fail.
- `reply_asks` is a question mark.
- A right run made in more calls fails where `inputs_include` wants every entry in one call.

Ruled by the owner: no fix round case by case; the golden package is committed as built, with a judge known to pass
wrong runs; the judge is rebuilt to judge the record under a contract of its own: what the run changed, compared with
a right run's change on a twin person; every answer kept and a preview judged by what it showed; one rule for
figures; one shared rule for a claim of done, counted apart from the gate; a test that makes wrong runs by rule over
every case. No baseline run, and no yes from him on the case table, until it is built.

**2026-11-17, at the waypoint (a review of the repo's state):**

- The baseline as the list now stands: 235 cases, 181 golden, 101 core. As written, 2,820 second-tier runs (235
  cases, 3 runs, 2 models, twice) and 1,086 Opus runs (181 golden cases, 3 runs, twice): about $206 at $0.19 a
  run, about 16 times the 66 Opus runs and $12 above. The cost is said to the owner by side before the baseline
  runs; the rebuild's contract may size it again.
- G1 (`fd83753`) and the evals' advisor fix (`2718899`) were committed on `main` itself, not on a branch. Both
  change eval files only. G1's baseline run is still owed.
- The status line at the top says "for the owner's yes": his yes is given (the picks). His order since:
  "hardening first, then F, then G2". Hardening is live.

### G2: the list diet (high)

| Piece | What |
|---|---|
| Output schemas | Unpublished (R1). The result models stay, unpublished, until after G5 |
| Titles | Stripped (R2) |
| Wording | Every description and input field rewritten by D3; the rules of the table above moved or dropped. Behaviour changes only by the "Only here" rows, now in descriptions every client shows, where before only Codex saw them (as output schema types) |
| Budgets | The tests below |
| Codex compaction | Read what Codex does to an input schema past 5,000 bytes (its source, `tool_input_schema_max_bytes`) and see it with one Codex probe on `import_entries`; then bring `import_entries`' input under 5,000 bytes by wording, the main path for people coming from other apps |
| What a client is sent | Both copies, unchanged (R3). The marker probe with the schema off, on Claude Code and Codex; its outcome, A or B, in the build log |
| The doc | `docs/adding-a-feature.md` (D13), under 4,000 characters |
| Files | `app/mcp_server.py`, `app/mcp_schemas.py` (descriptions only), `docs/adding-a-feature.md` (new), `docs/can-and-cant.md` where it repeats a moved rule; the pins: `tests/test_feedback.py` (`PUBLISHED`, `INSTRUCTIONS_AS_BUILT`), `tests/test_mcp.py`, `tests/test_units.py`, `tests/test_switch.py`, `tests/test_data.py`, `tests/test_gym_logging.py`, `tests/test_edit_history.py`, `tests/test_hi_there.py`, `tests/test_routines.py`, `tests/test_eval_need.py`, `tests/test_inputs.py`, `tests/test_idempotency.py`; a new `tests/test_list_size.py`. `evals/need.py` reads `outputSchema` as a surface (`:40`): changed only if it fails on a list with none |
| Tests | No entry has `outputSchema`; no `title` inside any input schema; each entry at most its figure in "Budgets"; everyone's list at most 45,000; each input schema at most 5,000 bytes; every result still passes its model; the instructions under 4,096 |
| Exit | The comparison against G1's baseline is not worse. Sizes before and after in the build log. If the wording cannot reach 45,000 without losing a rule, the builder stops and says so: the owner decides |

### G3: routing and get_data (high)

| Piece | What |
|---|---|
| `get_data` | R4 to R7: `queries`, the named lists, the answer, the old shape. Its description: when to use it (any question about the person's data), the areas with their fields in one compact index built from `fields.AREAS`, 2 or 3 example calls, the batch rule, the range rule |
| `what_is_logged` | R8; its description no longer says to call it before `get_data` |
| Instructions | R13's G3 core: `get_trends` and `get_goals` keep their routing sentences and stay listed; `DATA_RULE` is rewritten so `get_data` is not the fallback for what is left but the read for every other question, with every query in one call |
| Loading | R12 |
| Files | `app/mcp_server.py`, `app/mcp_schemas.py` (the `get_data` result model as `{results, targets}`, today's single-shape model kept for R7, the `what_is_logged` model as R8; `RESULTS` follows), `app/services/data.py`, `app/services/fields.py`, `app/calllog.py` (`KNOWN_NAMES` gains `queries`; `filters` stays an open object, so no filter key is published), `docs/can-and-cant.md`, `tests/test_data.py`, `tests/test_mcp.py` (`every_result`), `tests/test_trace.py:225-233` (known names equal every published property, `$defs` included), `tests/test_calllog.py:185-190` (a marker for each string field of a query, through its `$ref`), the pins of G2's list |
| Tests | 5 queries in one call equal 5 single calls sent at the same ceiling of 6,000 characters each, row for row, each less `targets`; 6 refused; each query's refusals word for word as today, a wrong `area`, group or total included, with its index; the ceiling across queries; the old shape answers today's single shape byte for byte; the published enums equal `fields.AREAS`, the group fields with `TIME_GROUPS`, and `TOTALS` (a new area joins by itself); the index in the description names every area and field; R8's budgets; the first 512 characters name `whoami`, `get_data`, the log functions and "together"; the four `alwaysLoad` entries; every success still passes its model; `get_data`'s entry at most 7,500 characters (the prototype input is 2,179 bytes), its description at most 3,500 (the index is about 2,000 with G4's areas); everyone's list at most 50,000 |
| Exit | Not worse than G2, and calls and seconds a data case lower on at least one model; if not lower on either, the owner is told before G4. Deployed at least a day after G2 is live (R15) |

### G4: the worked-out areas (high)

| Area or total | Rows or figure | From | Equal to |
|---|---|---|---|
| `lift_bests` | Each new best: date, session_id, exercise, category, region, kind (estimated_max or weight), best and previous in the person's units | `lifts.history`'s bests | `get_progress` `bests` |
| `lift_progress` | One lift in one session: date, session_id, exercise, category, region, its best estimated max (null when no set is 1 to 10 reps above 0 kg: the row stays), its heaviest weight | a walk of `lifts.history`'s `sessions`: its `points` hold only (date, best) a lift (`app/services/lifts.py:165`, `:210`) | `get_progress` `estimated_max_over_time` on the rows with a best; the max over 28 days equals `current_estimated_max_kg` where the 28 days hold a session |
| `trend` (a total) | Change a week, over **its own window ending at `to_date`, whatever `from_date` says**; the result's `summary` names the window (no new key: the result model forbids extras, `app/mcp_schemas.py:1517`). The loader drops points after `to_date` before it calls the metric, since a point after the today it is given counts as that today (`metrics.on_or_before`, `app/metrics.py:90`). On `lift_progress.estimated_max_kg`: the least-squares slope over the 43 days from `to_date` less 42 (`_fit` keeps only those, `app/metrics.py:336`), 4 points on 2 dates. On `weigh_ins.smoothed_kg`: `trend_rate`'s fixed windows, the latest weigh-in in the 7 days ending `to_date` against the last one 14 to 42 days before (`app/metrics.py:545-569`) | `metrics.slope_per_week`; `metrics.trend_rate` | `get_progress` `trend.slope_per_week`; `get_trends` `rate_per_week`, both with `to_date` today; tested with `from_date` = `to_date` less 42 and with a year's range, the same figure |
| `smoothed_kg` (a field on `weigh_ins`) | The smoothed weight at each weigh-in, over every weigh-in to that day | `metrics.ewma` | `get_trends` `series[].smoothed` |
| `per_week` (a total) | A sum or count over the range's days divided by 7 | | `get_trends` `per_week_4w` and `minutes_per_week_4w` over 28 days |
| `goals` | One row a goal. An open goal: every `GoalNumbers` field but `projection`, with a value's `unit`. `projection` is an object and a row cell is text, a number, true or false, or null (`Cell`, `app/mcp_schemas.py:1472`), so it is left out: its `latest`, `status`, `needed_per_week` and `projected_date` are `GoalNumbers` fields at the top already; its slope, points used and weeks left are not carried. `note` ("why there is no projection") is carried as a field. A closed goal: `_closed`'s fields (goal_id, label, kind, status, closed_at, target_value, target_date), the rest null. An open goal `goal_numbers` skips (no target or no date, `_counts`, `app/services/goals.py:451-458`) gives no row, as `get_goals` leaves it out. Filters kind, exercise, status, open | `goals.goal_numbers`, `rounded_numbers`, `goals._closed` (`:922-925`) | `get_goals` with `include_closed`: its `goals`, every field but `projection`, and its `closed`, field for field |
| In `what_is_logged` | `maintenance` (with "an estimate: it never sets the target") and the estimated share of the last 28 days | `metrics.maintenance`; `trends._calories` | `get_trends` `maintenance`, `estimated_share_pct_28d` |

**Figures of the old reads not carried as one figure,** each reached by queries that R11's fix names:

| Figure | Old read | Reached by |
|---|---|---|
| Body weight now: the 7-day average, else the latest | `get_trends` `bodyweight.now` | `weigh_ins` avg over the 7 days; with none there, `weigh_ins` ordered `-date`, limit 1 |
| Current estimated max: best of 28 days, else the latest session | `get_progress` `current_estimated_max_kg` | `lift_progress` max over 28 days; with none there, ordered `-date`, limit 1 |
| Best set | `get_progress` `best_set` | `lift_sets` filtered by the lift, ordered `-estimated_max_kg`, limit 1 |
| A line a lift (sessions, last trained, current and best, slope, goal status) | `get_progress` with no exercise | `lift_progress` grouped by exercise with count, max and `trend`; last trained ordered `-date` a lift; goal status from `goals` |
| A cardio activity's sessions and week | `get_progress` with an activity | `cardio` filtered by activity; the week's minutes by a 7-day range sum |

| Piece | What |
|---|---|
| Files | `app/services/fields.py`, `app/services/data.py`, `app/mcp_schemas.py` (the `what_is_logged` model gains `maintenance` and the estimated share; `RESULTS` follows), `docs/can-and-cant.md`, `tests/test_data.py`, `tests/test_mcp.py` (`every_result`), `tests/test_worked_out.py` (new). `lifts.py`, `metrics.py`, `goals.py`, `trends.py` only to expose an existing step as a function, its result unchanged. The words `goals`, `goal_id`, `label`, `open`, `plan_line`, `status`, `target_date` and `note` that `data.py` holds today (`OPEN_GOAL` at `:64`, the rider key at `:145`, `:880`, the answer's own `note` key at `:148`, `:883`) become the `goals` area's names, so they move to `fields.py` as constants, as `ROW_KEYS` did, and the source test's exempt set stays `{"entries", "values"}` (`tests/test_data.py:295-299`) |
| Tests | Every row of the table equal by R9 on the `sixteen_weeks` seed at its nine todays and on a lb account; the tie test and the scaling test still pass; the `goals` area's loader reads the `goal` table only through `goals.py` |
| Exit | Not worse than G3, every equality test green. Only then G5 |

### G5: the retirement gate, then two deploys (medium)

**The arm test,** on a scratch copy of the G4 tree, never the live tree. Arm A: the list as G4 left it, the three
reads listed and G3's core routing to them. Arm B: the three hidden from `tools/list` (as `list_tools` hides the
owner's), R13's arm B core, and every other sentence that names them re-pointed to `get_data`. Nothing else differs.
The arm set: the golden set, the seven re-judged cases, and every case whose G4 runs call one of the three (in the
release run, `drop_goal`, `feedback_offered` and `data_calories` 3 of 3 and `data_basketball` 1 of 3 did). Sonnet 5.5
and the small GPT, **6 runs a case in each arm**, not 3: it is the hardest decision to undo (a removed function is gone
from every client's list, and bringing it back is another two-deploy round while running chats lack it), and twice the
runs halve the noise in the share of passes. Retirement goes ahead only if arm B is inside the band or better against
arm A, the band scaled to 6 runs a case (G1's spread doubled for passes, its medians' spread as it is). The
incidental cases' pick-up rate and the look-alikes' over-call rate are reported beside it. Calls a case too.

| Outcome | Then |
|---|---|
| B holds | Deploy 1: R11 for the three; arm B's instructions; `spotter://exercises` dropped; the texts that name them re-pointed to `get_data` (`KIND_LABELS`, `READ_BACK`, `entries.py:104`, `data.py:852`, `set_goal`'s `goal_id`, the hi page line `web.py:1293`); `docs/can-and-cant.md`'s table. Deploy 2, at least a day later: the three unregistered, their `NEEDS` entries gone, R7's old shape refused; then the result models deleted, the input half moved to `app/mcp_inputs.py`, the 13 importing files and every test that read a result model rewritten to check by value |
| B fails | The three stay, lean (about 1,950 characters together after G2), with G3's instructions as tested in arm A; G4's areas stay. One deploy, at least a day after G3's: `spotter://exercises` dropped, R7's refusal, the result models deleted. The result goes to the owner |

| Piece | What |
|---|---|
| Files | `app/mcp_server.py` (a `RETIRED` set beside `OWNER_FUNCTIONS`), `app/services/common.py`, `app/services/entries.py`, `app/services/data.py`, `app/web.py` (`:1234`, `FUNCTION_COUNT` less the retired too; `:1293`), `docs/can-and-cant.md`, `docs/evals.md` (its rows naming the three: `:245-250`, `:278-282`, `:294-295`, `:479-496`), `evals/cases.json` (the `no_call` lists and routes naming the three), deploy 2's `app/mcp_inputs.py` (new), `app/mcp_schemas.py` (deleted), `app/calllog.py` (`KNOWN_NAMES` drops `include_closed`, which only `get_goals` publishes, `app/mcp_server.py:1290`), `tests/text_rules.py` (`CLAUDE_SOURCES`), the test files that import a result model, and four that import none: `tests/test_feedback.py` (`PUBLISHED` at `:68-90` holds the three's hashes; `:532` reads the owner's list, so a hidden one raises `KeyError`), `tests/test_calllog.py` (`get_trends` and `get_goals` in `MARKED_CALLS`, `:142-143`, answer `retired` in deploy 1, so `errors == REFUSED` at `:197-200` moves), `tests/test_inputs.py` (`get_goals` in the strict sweep, `:1187`, and a base call, `:224`: unknown after deploy 2), `tests/test_trace.py` (the known names at `:225-233`; calls of `get_progress` and `get_trends` through `/mcp`, `:117-121`, `:248-249`, among others) |
| Tests | A retired read answers `retired` with its fix and writes one call-log row; it is in nobody's list; the owner's list keeps the owner's three; the landing's count equals everyone's list (`NEEDS` less the owner's three and the retired), 21 in deploy 1; after deploy 2, no file imports `app.mcp_schemas` |
| Exit | The arm report in the build log, its outcome, and the comparison of the tree after deploy 1 against G4 |

## Budgets (G2's test, `tests/test_list_size.py`)

Each entry at most its figure here: today's, with no output schema and no titles inside the input schema, so nothing
grows. Everyone's whole list at most 45,000 at G2's exit, so that G3 can lift `get_data` to 7,500 and G4 add its areas
within 50,000. Each input schema at most 5,000 bytes. The owner's three, listed for him alone: `owner_calls` 1,833,
`owner_feedback` 1,186, `owner_delete_feedback` 700.

| Entry | Most | Entry | Most | Entry | Most |
|---|---|---|---|---|---|
| `whoami` | 852 | `log_measurement` | 1,451 | `log_cardio` | 3,270 |
| `list_exercises` | 753 | `edit_entry` | 5,059 | `get_trends` | 637 |
| `log_lifts` | 2,719 | `restore_entry` | 1,647 | `get_goals` | 708 |
| `log_bodyweight` | 1,554 | `delete_entry` | 2,159 | `import_entries` | 9,092 |
| `set_goal` | 4,077 | `find_food` | 849 | `send_feedback` | 1,999 |
| `get_progress` | 608 | `log_food` | 3,653 | `save_routine` | 2,277 |
| `update_settings` | 2,408 | `save_food` | 1,199 | `get_routines` | 724 |
| `get_log` | 873 | `what_is_logged` | 603 | `get_data` | 2,667 |

R12's `_meta` (`"_meta":{"anthropic/alwaysLoad":true}`, 37 characters with its comma) may lift `whoami`, `log_food`
and `log_lifts` past their figure by that much. If G2 lands at exactly 45,000, G3's `get_data` at 7,500 and the
`_meta` leave about 50 characters under 50,000: so G4's areas must fit inside `get_data`'s 7,500, and no other
entry grows.

## The eval cases

### The golden set, as G1 rules it

His words: "we have to ensure that the sample questions that we're using, essentially the golden data set that we
have right today, covers all our functions, and like functions that would essentially cover our asses if it is like a
quasi requirement. Like if you need your data from our system today, but only for like a mini step of the process. Say
they need to answer some complicated question that vaguely would benefit from having spotters information included.
Are we seeing the AIs actually pick up on that? ... we should tag to which tool calls we are trying to see the AI
actually pick up and use in that scenario."

| Rule | What |
|---|---|
| Tags | One uniform field on every case, `calls`: `must` (any of these, at least one called and answered), `may` (called or not, either is right) and `must_not` (none called). Built from the judge's parts that exist: `function` and `any_of` for `must`, `no_call` for `must_not`, `ok_calls` (`evals/judge.py:23`, `:299`) where a count matters. `may` is new and judges nothing: it records intent, so a reader sees what each case expects |
| Coverage | A unit test, no cost, no run: every function on everyone's list is the `must` of at least one case. The owner's three are left out: they are listed for him alone. Today two have no case that calls them: in the release run `save_food` and `edit_entry` were called 0 times in 153 runs (the function map of 2026-11-17); G1 adds a case for each |
| Three kinds | **Direct:** the question is about the person's data (the table below). **Incidental:** Spotter is one step of a bigger question, such as a 10k plan, a trip's meals, or whether to deload this week; `must` names the data read that step needs. **Look-alike:** paired with each incidental, a similar question in which Spotter's data has no part (a 10k plan for a friend, a restaurant's opening hours); `must_not` names every data read |
| Scoring | Direct cases count in a gate's passes, as every case does. Incidental and look-alike cases are kept out of a gate's pass count and the band, and reported apart: the pick-up rate per model (incidental runs that made the `must` call, of all incidental runs) beside the over-call rate (look-alike runs that made a data read). Both are tracked gate to gate. G3's instructions rewrite is the lever for pick-up, so both rates are in G1's baseline |
| Wording limit | Descriptions and instructions say when Spotter's data is relevant ("Spotter holds the person's own training, food and body records"), and never tell the model to call a function the person did not ask for. The Anthropic directory rejects a description that tells Claude to call tools the user did not ask for (https://claude.com/docs/connectors/building/review-criteria.md, as the tool design study read it on 2026-11-17). R13's sentences route a question about the person's data to `get_data`; they add no call for a question that is not |
| Known limit | Spotter is the only MCP server in the harness (`--strict-mcp-config`, `evals/run.py:80`), so no other tool competes for the step: the measured pick-up rate is a ceiling |
| His part | About ten real incidental questions from him, and a one-page table of every golden case (question, kind, must call, must not call) for his yes before the baseline runs. The case list itself is the G1 builder's work, from his questions and the rules above |

**The direct cases to start from,** judged by `read_any` and `quotes_spotter`, with the no-write list of the data
cases. Four reuse a case that exists, re-judged so: "How am I doing?" is `how_am_i_doing` (on `four_weeks`, as now), "How do I get better
at lifting?" `data_lifting`, "I have slipped lately ..." `data_back_on_track`, "How has my sleep been?" `data_empty`.
The other seven are new.

| Kind | Cases | Seed | n |
|---|---|---|---|
| Direct | "What is my squat this month?"; "What did I weigh this morning?"; "How many calories did I eat yesterday?"; "Am I on track for my squat goal?" | `sixteen_weeks` | 1, and a `reply_has_pattern` for the figure asked (the squat's estimated max or top set, the day's weigh-in, the day's kcal, the plan line's gap or plan figure), each held on the nine todays |
| Direct | "How am I doing?" | `four_weeks` | 2, and its own figure checks of today, re-judged route-free |
| Direct, open | "How do I get better at lifting?"; "Should I eat more?"; "I have slipped lately. How do I get back on track?" | `sixteen_weeks` | 2 |
| Direct, nothing logged | "How has my sleep been?" (no sleep logged) | `sixteen_weeks` | 0, and no made-up sleep figure |
| Direct, nothing logged | "What is my best deadlift?" | a case-level `log_lifts` seed of squats only (both seeds hold a deadlift: `evals/sixteen_weeks.py:56`) | 0, and no made-up deadlift figure |
| Direct, nothing logged | "How was my run this week?" | `sixteen_weeks` at a today whose week holds no cardio | 0, and no made-up run figure |

The incidental and look-alike pairs come from his questions; none is written here.

**A catch-up case:** one message telling three days away (lifts Monday, food Tuesday, a weigh-in Wednesday), judged
on the rows written on each date and no row on a wrong date. **An import case:** rows in the column layout of one app
people come from, read from that app's own help page and named in `docs/evals.md`, with made-up values; judged on a
preview first and nothing written before a yes.

## Deploy plan

| Deploy | Holds | Gap | The full run on Opus 5.5 and gpt-6.1-sol before the merge, when `python -m evals.need main` says yes |
|---|---|---|---|
| G1 | No app change: nothing for a person changes | | None |
| G2 | The diet, both copies sent, every result unchanged | | Expected |
| G3 and G4 | Together or apart, each after its comparison. R7 keeps the old `get_data` shape working | At least a day after G2 | Expected, once for the two if they merge together |
| G5 deploy 1 | B holds: the three hidden and answering `retired`, arm B's instructions, the resource gone | | Expected |
| G5 deploy 2 | The three removed (if B held), R7 refused, the result models deleted. If B fails, the only G5 deploy, with the resource dropped too | At least a day after deploy 1, or after G3's | Expected when it is the only G5 deploy (R7's refusal text is model-read); after deploy 1, only if it says yes |

A fresh assistant session at each live check (a running one keeps its list). Each deploy: `/health` 200, `/mcp` 401
with no token, `/` 200.

**Order.** G1 touches only `evals/` and tests, so it may start now. G2 opens `app/mcp_server.py`, which the hardening
items (H1, H2) and the account foundations (plans and status, age, gender and a first name) also touch, at the same
wrapper (`Wrapper.call`) and `whoami`. Before G2 starts: his pick on the order of those items against G2 to G5 (Open 11).

## The exit check

1. Every gate's exit row holds, each with its comparison in the build log.
2. Everyone's list at most 45,000 characters after G2 and 50,000 after G4, measured on the built server; each input
   schema at most 5,000 bytes; the instructions under 4,096 with R13's core in the first 512.
3. The reviewer, on a frozen copy, calls `get_data` with 5 queries, the old shape, and each worked-out area through
   `/mcp`; and, after G5 deploy 1, a retired read.
4. The tests for the files touched pass, plain and with `-W error`. The hook runs the rest.
5. `python -m evals.need main` says yes at G2 to G5: the full run on Opus 5.5 and gpt-6.1-sol is owed before each merge
   the deploy plan marks, its cost said first.
6. **An absolute bar, beside the relative ones.** At each release, the logging cases (a case whose `must` is a write)
   pass at least 95% of their runs on Sonnet 5.5 and at least 95% on the small GPT, and no logging case passes fewer
   than 2 of its 3 runs. His words: "If Sonnet is not able to use it right, then that's a problem." Why 95% and 2 of 3:
   logging is what a person does every day and a wrong or missing write is the costliest miss; in the release run of
   2026-11-17 every logging case passed 3 of 3 on Opus (the function map); and 2 of 3 is the bar this repo's stage
   evals already set ("every case passes at least 2 of 3 runs", `BUILD_LOG.md:419`). A baseline under the bar fails
   the release even when every gate is inside its band, so a weak start cannot pass every gate.

## Known limits

| Limit | Why it stands |
|---|---|
| "Claude Code never shows output schemas" rests on one run and the model's own account; claude.ai was not probed | G2's comparison is the check that matters |
| A chat begun before G2 and still open past the one-day gap holds output schemas, and its client may check a G3 or later answer against them, as the Python SDK does | Clients refresh their list only in a new session; how long a claude.ai chat keeps its list is not checked |
| The arm test is 6 runs a case on two models: still a small sample | The cost of more; the release run is a second look |
| The incidental pick-up rate is measured with Spotter the only MCP server in the harness: a ceiling, not what a person with other tools sees | `--strict-mcp-config` (`evals/run.py:80`) keeps every run to Spotter alone |
| `lift_bests` and `lift_progress` read `lifts.history`, whose work grows with the square of the history: each session's bests are checked against every earlier set of the lift (`app/services/lifts.py:211-214`) | The database hardening item after B6 |
| The enums are published, not enforced, and field names are not enums at all: a wrong value gets Spotter's refusal with its nearest name; a client that checks inputs against the schema first refuses it in its own words | Today's refusals stay word for word (R5); fields differ by area |
| Gemini is not a gate, and its free tier may refuse a run | His word |

## After B6, each its own item and contract

| Item | Scope and the lead's picks, for his yes |
|---|---|
| **Writes, one shape** | One body write for weigh-ins and measurements (body weight as a kind); each log item takes its own date, so a catch-up after days away is one call a log (his words: "Do we use the export function there and ask the AI to structure it as an export, or do we still feed it through the normal functions itself?"); another app's export stays with `import_entries`, one path per source; one confirm step for every write that needs a yes: the first call writes nothing and returns what would change with a code, the same call with the code writes (delete today, import and B7's add or replace next). G1's catch-up and import cases measure it |
| **A field list for writes** | After the item above, with C3's macros as its first user. Weighed on `f8385d6`: one stored number today sits in 10 to 12 app files (`git grep -l -w`: `incline_pct` 11 files, 26 lines; `avg_hr` 12, 27; `grams_per_serving` 10, 32), across the log inputs, `edit_entry`, `import_entries`, the rules, the pages and the export. Rebuilding those inputs from one list is larger than all of B6's read side |
| **Age, gender and a first name** | His words: "I think that we should be tracking people's age and their gender, male, female, whatever." and "Other than that, first name is enough. And yeah, like I said before, just the number is okay. We don't need necessarily the date of birth in its full context." Picks: optional, through `update_settings` and a Settings card, shown in `whoami`; age kept with the date given, shown aged by whole years since; gender female, male, another or prefer not to say; a first name of the person's own in its own column, since `name` is refreshed from the sign-in on every call (`app/users.py:57-61`). One migration: its own `0012`, or one file with H2 if H2 goes first, one Postgres run of up, down, up for both. The privacy page names the three and why. These picks are that item's starting point, for its own contract |
| Database hardening | Small fixes with no migration (H1) and a migration (H2), from a database review of 2026-11-17 kept outside the repo: its own items |
| Account plans and status | A plan or paid-until mark on the account, shown in `whoami`, and D14's refusal. It touches the same wrapper in `app/mcp_server.py` and `whoami`, so it builds after B6 |
| Content-only answers | Only if G2's probe gives R3's Outcome B: an answer sent as `content` text alone, at least a day after G2 is live, with R3's files |
| B5 | Goals as rows, and every goal and target rename, over its own two deploys (D12) |
| B7 | Food reuse and add or replace |
| B6b | The list's token cost and splitting the server, its own chat |

## Open for the owner

| # | Pick | The lead's pick | Why |
|---|---|---|---|
| 1 | Retire `get_progress`, `get_trends`, `get_goals` | Let G5's arm test decide | Without output schemas they cost about 1,950 characters together: only routing argues for removal, and that is measurable |
| 2 | "As accurate and not slower", in the arm test and at every gate | The band: G1 runs its baseline twice on each model, and the spread between the two (passes, median seconds, median tokens) is the allowed band, recorded as a dated note here before any gate result is seen. A gate passes inside the band or better, and never below the lower of G1's two runs. G5's arm test runs 6 a case. **Changed after his yes on the 10 picks** (it was: total passes not fewer, medians at most 10% above) | At a 95% pass rate, 180 runs move by about 3 passes either way with nothing changed, so "not fewer" fails on noise; the band is measured, not guessed. 6 runs in the arm test because retiring is the hardest decision to undo |
| 3 | The result models | Unpublish in G2, delete after G5 | Deleting saves no tokens and drops the shape check just as result shapes change most |
| 4 | An eval comparison at every gate | Yes: each gate's case list on the second tier; the full set on the top tier before each merge for which `python -m evals.need main` says yes | CLAUDE.md asks one run a release; a regression is found only gate by gate |
| 5 | Tiers and cost | Sonnet 5.5 and a small GPT every gate; Opus 5.5 and gpt-6.1-sol at each release; Hydrogen now and then. Claude side: the last Opus release run was $35.23, $6.55 of it the advisor step, so Opus alone about $0.19 a run. G1's baseline, run twice: about 720 second-tier runs (60 cases, 3 runs, 2 models, twice; more once his incidental pairs join) and the golden set's 66 on Opus, about $12; a first 10 Codex runs show what his plan allows; the small GPT and every tier's reasoning effort pinned before the baseline; Sonnet's cost is measured on its first runs and said before the rest. Codex on his ChatGPT plan (no bill reported in the trial). Gemini on his free key | His tier word; the gate is Claude and Codex |
| 6 | Loaded up front in Claude Code | `whoami`, `get_data`, `log_food`, `log_lifts` | The most used; each loaded schema costs every session's start |
| 7 | `get_data` takes 1 to 5 queries a call | Yes | One call for a question that needs several reads, on every client |
| 8 | The worked-out areas' names | `lift_bests`, `lift_progress`, `goals`, totals `trend` and `per_week`, field `smoothed_kg` | Plain words; `goals` flips only with B5's rename of every goal word |
| 9 | His goal and target words | No rename in B6; every one in B5's own two deploys (D12). The card put them in B6's deploys | B5 moves the daily and weekly amounts out of the settings anyway: renaming now renames twice |
| 10 | The writes out of B6 | The one body write, several dates a call, one confirm step and a field list for writes are their own item after B6, as are age, gender and a first name, and account plans. The card had the body write and the confirm step in B6 | Each gate then changes the read side alone, so a regression is traced to one change; G1's catch-up and import cases give the write item its baseline |
| 11 | The order after G1 | G1 starts now: it touches only `evals/` and tests. Before G2, his pick on the order of the hardening items (H1, H2) and the account foundations against G2 to G5 | They touch the same wrapper in `app/mcp_server.py` and `whoami` that G2 to G5 rewrite: built in one order, never side by side |
| 12 | The golden set | About ten real incidental questions from him; his yes on a one-page table of every golden case (question, kind, must call, must not call) before the baseline runs | The cases decide every gate; his questions are what real use looks like |

Scope is frozen as written. A new ask goes to the backlog, not into this contract.
