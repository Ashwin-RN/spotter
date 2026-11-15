# Tidy contract: four tidy-ups

**Goal: four small repairs and the join review's fixes, built as one package. The test suite passes on Postgres as on SQLite. Each count of functions and cases lives in one place, and the README states none. The landing says, under each assistant's name, what it needs. The site's Contact gives a private address once the owner sets one.**

Approved by the owner on 2026-11-15, with every pick of its card: "save your picks as the decisions and lets clean up the repo and get our things in order." Read at `76df357` on 2026-11-15: gym logging, first day and routines, all merged and live. The builder reads each "What exists" row again on the commit it starts from.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Which address takes Contact | A new address used only for Spotter, made by the owner and set on the host as `CONTACT_EMAIL`. No file in the repository holds it |
| 2 | Until it is set | Contact stays the GitHub issues link, as today |
| 3 | Footer and privacy card | The footer says "Contact". The privacy card shows the address |
| 4 | The landing's assistant notes | Each assistant's whole Status cell from the README, word for word |
| 5 | README counts | The README states no count of functions or cases |
| 6 | The can-and-cant sentence | Changed. Its eval run is folded into the one run at the end of the whole build, this package and the units work after it |

### Ruled in this contract, where the decisions leave the how open

| # | Ruling | Why |
|---|---|---|
| T1 | **Build order 3, 4, 1, 2,** one commit an item | 3 is tests only and touches `tests/test_switch.py` before 4 does. 4 holds the one text a model reads. 1 and 2 both touch `app/web.py` and `tests/test_web.py`, beside each other |
| T2 | **ChatGPT's Status cell reads "Pro can only read."**, not "Pro can read only." | `tests/test_switch.py:592` holds that the landing never says "read only", the label the switch removed. The cell keeps its meaning, and the landing's note stays word for word the README's |
| T3 | **The privacy page keeps its date.** | The page has no route at `76df357`, so no reader holds a version of it. The work that gives it a route dates it. Two tests pin the date: `tests/test_edit_history.py:1367`, `tests/test_feedback.py:939` |
| T4 | **Pins that name an order stay:** the case file's last four ids, the published list's first nineteen, the forms count `(22, 19)` | They hold an order or a template count, not a total. A case appended later moves them by hand, as a planned change |
| T5 | **The instructions' length stays pinned** as it is, at 3,927 | Approved as unchanged. It is in five tests, listed under item 4 for the record |
| T6 | **Evals run once,** at the end of the whole build | Decision 6. Items 1, 2 and 3 change nothing a model reads |

## Item 3. Tests that fail on Postgres only because they compare SQLite's statement text

### What exists

| Piece | State | Where |
|---|---|---|
| The lock | `lock_statement(dialect, user_id)`: on SQLite an `UPDATE user SET claude_can=user.claude_can` that changes nothing, elsewhere `SELECT user.id ... FOR UPDATE`. `lock_user` picks by `s.get_bind().dialect.name`. Right on both | `app/services/common.py:201-207`, `:210-222` |
| The test database | A SQLite file. The suite meets Postgres only when pointed there by hand | `tests/conftest.py:39`, `BUILD_LOG.md:903-917` |
| The Postgres runs | 2026-11-14: 957 tests, no failure. With the switch: 400 run, 9 failed, all on SQLite's lock text, 6 of them the preview test below. With routines: 181 run, 9 failed on SQLite's text. The log does not name them. The 7 kinds of the preview test, `tests/test_routines.py:1105` and `:1111` make 9 | `BUILD_LOG.md:915`, `:1009`, `:1037` |

Every test that compares a recorded statement with SQLite's text:

| Test | What binds it to SQLite | Run on Postgres |
|---|---|---|
| `tests/test_switch.py:159` `test_a_preview_sets_nothing_and_writes_nothing_but_the_lock` (7 kinds since routines added `routine`) | `:178`: every write must start `UPDATE user SET claude_can=user.claude_can`. `:179`: exactly one write. On Postgres the preview sends no write at all | Yes: 6 failed in the switch's run, when it had 6 kinds |
| `tests/test_update_settings.py:137` `test_the_lock_is_the_first_statement_whether_or_not_anything_changes` (2 cases) | `:151`: `lock_statement("sqlite", ...)` compiled with `sqlite.dialect()`. Import at `:8`, `:14` | Yes: 2 failed |
| `tests/test_update_settings.py:328` `test_the_switch_locks_the_user_row_first` | `:340`, the same | Yes: failed |
| `tests/test_cardio.py:172` `test_cardio_is_limited_and_locks_first` | `:184`, the same. Imports at `:174-175` | Not run there |
| `tests/test_food.py:334` `test_food_writes_lock_the_user_row_first` (2 cases) | `:346`, the same. Imports at `:336-337` | Not run there |
| `tests/test_lifts.py:423` `test_every_write_locks_the_user_row_before_anything_else` | `:426-427` compare with `LOCK_SQL`, a module constant at `:434` | Not run there |
| `tests/test_gym_logging.py:602` `test_an_add_locks_first_and_once_and_runs_as_many_statements_...` | `LOCK_SQL` at `:596`, compared at `:611`. Imports at `:16`, `:22`. Its counts, `ADD_STATEMENTS` at `:597` and `:613-614`, say "On SQLite the ORM inserts each new set on its own": Postgres may batch the inserts | Not run there |
| `tests/test_routines.py:1105` `test_a_save_locks_first_and_once` | `LOCK_SQL` at `:204`, compared at `:1108`. Imports at `:26`, `:34` | In the 181, by its file |
| `tests/test_routines.py:1111` `test_every_statement_on_routine_names_the_person_s_id` | `:1140` `endswith("WHERE routine.id = ?")`, `:1144` `"routine.user_id = ?"`. `statements_of` at `:189-200` keeps `tuple(parameters)`: on psycopg the parameters are a dict, so the tuple holds its keys, and `me.id in params` at `:1142` and `:1144` fails | In the 181, by its file |

True on both already, so left alone: `tests/test_lifts.py:437` (compiles each dialect on purpose and runs nothing), `tests/test_switch.py:512` (compiles migration 0009's statement for Postgres on purpose), `tests/test_hi_there.py:1129` (every statement a `SELECT` with no `FOR UPDATE`), `tests/test_idempotency.py:407` and `:824` and `tests/test_feedback.py:672` (match by table name and `SELECT`), the sweep failures at `tests/test_edit_history.py:610`, `tests/test_feedback.py:883` and `tests/test_idempotency.py:678` (match `DELETE FROM` a table), the upper bounds on a statement count at `tests/test_bodyweight.py:190`, `tests/test_goals.py:402`, `:688` and `tests/test_progress.py:157`, and every migration test that builds its own `sqlite:///` file.

### What changes

Ask the running engine. One helper in `tests/conftest.py`:

```python
def lock_sql(user_id: int = 1) -> str:
    """The lock on the person's row as the running engine sends it, whitespace squashed."""
    engine = db.get_engine()
    return " ".join(str(lock_statement(engine.dialect.name, user_id).compile(dialect=engine.dialect)).split())
```

It is called inside a test, never at import: the three module constants `LOCK_SQL` are built before the session fixture points the settings at the test database, so each becomes a call. `compile(dialect=engine.dialect)` gives the text the cursor receives, psycopg's own rendering included, by SQLAlchemy's design. Not run yet on Postgres.

| Test | Change |
|---|---|
| `test_update_settings`, `test_cardio`, `test_food` | `statements[0] == lock_sql(person.id)`. The `sqlite` imports go where nothing else uses them |
| `test_lifts:423`, `test_gym_logging:602`, `test_routines:1105` | `LOCK_SQL` becomes `lock_sql()` inside the test. `test_lifts.py` keeps its `sqlite` import for `:437` |
| `test_switch:159` | `lock = lock_sql(account)`, then `assert statements.count(lock) == 1 and [s for s in writes if s != lock] == []`. On SQLite the lock is the one write. On Postgres there is none. The docstring: "the only write is the lock, which on SQLite is an UPDATE that changes nothing" |
| `test_routines:1111` | In `statements_of`, keep the values whatever the paramstyle, and match a placeholder by its shape. The lines are in the block below the table |
| `test_gym_logging`'s counts | Left until a Postgres run says. If they differ, the count of inserts for k new sets is the dialect's, read from the engine, and the comment says which |

`tests/test_routines.py`, for `:1111`:

```python
# statements_of (:193): the values, whatever the paramstyle
seen.append((" ".join(statement.split()),
             tuple(parameters.values()) if isinstance(parameters, dict) else tuple(parameters or ())))

PARAM = r"(?:\?|%\(\w+\)s(?:::\w+)?)"  # a placeholder: ? on SQLite, %(name)s on psycopg, a cast after it

# :1140
assert re.search(rf"WHERE routine\.id = {PARAM}$", sql) and mine["id"] in params, (what, sql)
# :1144
assert re.search(rf"routine\.user_id = {PARAM}", sql) and me.id in params, (what, sql, params)
```

### Files

`tests/conftest.py`, `tests/test_switch.py`, `tests/test_update_settings.py`, `tests/test_cardio.py`, `tests/test_food.py`, `tests/test_lifts.py`, `tests/test_gym_logging.py`, `tests/test_routines.py`. No app file.

### Exit check

Those files pass on SQLite as today. Then the same files, plus the 400 of the switch's run and the 181 of routines' run, against a throwaway Postgres, the way `BUILD_LOG.md:915` describes: a copy of the tree, the tests' database address pointed there. No failure. That run needs Docker and a Postgres container on the build machine, which costs nothing.

**Changes what a model reads: no.** Tests only.

## Item 4. Each count in one place

### What exists

**Already drawn, no change:** the landing's two counts. `eval_case_count()` reads `evals/cases.json`, and `FUNCTION_COUNT = len(mcp_server.NEEDS) - len(mcp_server.OWNER_FUNCTIONS)` (`app/web.py:1106-1121`). `tests/test_web.py:44`, `:420-429` hold them to their sources.

**The eval case count, 35**

| Place | Text |
|---|---|
| `README.md:80` | "35 cases, 3 runs each." Held by T9 (`tests/test_docs.py:106-109`) |
| `tests/test_evals.py:48-50` | `assert len(cases) == 35` with a history comment over three lines |
| `tests/test_evals.py:24-33` | `CONTRACT_CASES`, every case id: the planned list, a set |
| `tests/test_feedback.py:982` | `... and len(cases) == 35  # gym logging, first day and routines: was 26` |
| `tests/test_routines.py:1260`, `:1262` | The docstring "At the join, 35", and `len(cases) == 35` beside the ids of the last three |
| `tests/test_switch.py:629` | `assert "35 cases, 3 runs each." in readme` |
| `docs/evals.md:48` | "20 cases at 3 runs": a dated run, history, left |

**The function count, 22, and the write count, 14**

| Place | Text |
|---|---|
| `README.md:71` | "22 MCP functions and two resources" (held by T9, `tests/test_docs.py:91-96`, which also asks that a count be there) |
| `app/mcp_server.py:1` | Docstring "twenty-two functions and two resources" |
| `app/mcp_server.py:35`, `:251` | "each of the fourteen write functions" |
| `app/mcp_server.py:1071` | Comment "After the nineteen" |
| `app/mcp_server.py:1107` | Comment "everyone's list is the twenty, then these" |
| `app/mcp_server.py:1145` | Comment "everyone else's list is the twenty-two above" |
| `app/mcp_schemas.py:1` | Docstring "the twenty-two MCP functions' ... and of the owner's two". Stale already: the owner has three. `tests/test_hi_there.py:831` asks the docstring to hold `setup_steps` |
| `docs/can-and-cant.md:403` | "Twenty-two functions, nothing else:", then the table under "## What Claude may do", one row a function in `FUNCTIONS`' order. **Served to the assistant as the `spotter://can-and-cant` resource** (`app/mcp_server.py:273-274`, `:1224-1231`) |
| `docs/evals.md:40-42` | "eighteen when this was measured, nineteen since ..., twenty-two since `save_routine`": a dated record, left |
| `docs/evals.md:203-206` | "the fourteen writes by name in `no_call`", then the fourteen names |
| `tests/test_feedback.py:530` | `web.FUNCTION_COUNT == len(FUNCTIONS) == 22` |
| `tests/test_feedback.py:950` | `"Twenty-two functions"` must be in can-and-cant |
| `tests/test_mcp.py:1496`, `:2094` | The same string, twice more |
| `tests/test_trace.py:231` | `len(tools) == 25` (the owner's list) |
| `tests/test_trace.py:456`, `:468` | A test named "the nineteen as before", and `web.FUNCTION_COUNT == len(FUNCTIONS) == 22` |
| `tests/test_routines.py:839` | `everyone == names[:22] and len(everyone) == 22 == web.FUNCTION_COUNT` |
| `tests/test_idempotency.py:120-121` | A test named "the fourteen write functions", and `len(WRITES) == 14` (`WRITES = common.WRITE_FUNCTIONS`, `:45`) |
| `tests/test_progress.py:444-449` | `OFFERED`, the twenty-two names typed again, under "The twenty-two functions Claude is offered" |
| `tests/test_evals.py:35-37` | `WRITES`, the fourteen names typed again (`app/services/common.py:116` holds them) |
| `tests/test_mcp.py:251-257` | `FUNCTIONS` and `READS`: the planned names, in order, imported by `tests/test_feedback.py:39`, `tests/test_trace.py:35`, `tests/test_calllog.py:18` |

**Pins that stay (rulings T4, T5)**

| Place | Text |
|---|---|
| `tests/test_feedback.py:67-90` | `PUBLISHED`: each function's SHA-256 as published |
| `tests/test_feedback.py:91`, `:973` | `INSTRUCTIONS_AS_BUILT` |
| `tests/test_switch.py:63`, `:576` | `OWNER_FEEDBACK_BEFORE` |
| `tests/test_switch.py:558`, `tests/test_edit_history.py:662`, `tests/test_hi_there.py:1223`, `tests/test_idempotency.py:735`, `tests/test_routines.py:843` | The instructions' length, 3,927 |
| `tests/test_feedback.py:256-261` | `send_feedback` published after the first nineteen of `PUBLISHED`: an order, and history |
| `tests/test_hi_there.py:1321-1322`, `tests/test_routines.py:1262-1263` | The ids of the case file's last four |
| `tests/test_routines.py:833` | `list(BEFORE)[:20]`: the list before routines, a snapshot |
| `tests/test_idempotency.py:1069` | `(len(forms), len(keyed)) == (22, 19)`: forms in the templates, not functions |

### What changes

The sources: `evals/cases.json` for the cases, `mcp_server.NEEDS` less `OWNER_FUNCTIONS` for the functions, `common.WRITE_FUNCTIONS` for the writes. The planned pins, which change by hand when a case or a function is added on purpose and so catch one added by accident: `tests/test_evals.CONTRACT_CASES` (ids), `tests/test_mcp.FUNCTIONS` and `CONTRACT_NEEDS` (names, order, level), `tests/test_idempotency.CALLS` (one call a write), `tests/test_feedback.PUBLISHED` and `INSTRUCTIONS_AS_BUILT` (the bytes a model reads). Every other place drops its number, or draws it.

**Tests**

| Place | Change |
|---|---|
| `tests/test_evals.py:48-50` | `assert len(cases) == len(CONTRACT_CASES)`, which still catches a repeated id. The history comment goes |
| `tests/test_feedback.py:982` | Drop `and len(cases) == 35` and its comment |
| `tests/test_routines.py:1260`, `:1262` | The docstring loses "At the join, 35". Drop `len(cases) == 35 and`. The ids stay |
| `tests/test_switch.py:629` | The line goes. T9 holds the README |
| `tests/test_feedback.py:530`, `tests/test_trace.py:468` | `assert web.FUNCTION_COUNT == len(FUNCTIONS)` |
| `tests/test_trace.py:231` | `len(tools) == len(FUNCTIONS) + len(mcp_server.OWNER_FUNCTIONS)` |
| `tests/test_trace.py:456` | The test name loses "the nineteen as before": `..._and_everyone_else_s_is_the_same_list` |
| `tests/test_routines.py:839` | `everyone == [n for n in names if n not in mcp_server.OWNER_FUNCTIONS] and len(everyone) == web.FUNCTION_COUNT` |
| `tests/test_idempotency.py:120-121` | `assert set(CALLS) == set(WRITES)`. The name loses "fourteen", and the comment goes |
| `tests/test_progress.py:444-449` | `OFFERED = tuple(FUNCTIONS)`, imported from `tests/test_mcp.py`. The comment names no number |
| `tests/test_evals.py:35-37` | `WRITES = set(common.WRITE_FUNCTIONS)` |
| `tests/test_feedback.py:950`, `tests/test_mcp.py:1496`, `:2094` | Drop `"Twenty-two functions"` |
| `tests/test_mcp.py`, a new test | The first table under "## What Claude may do" in can-and-cant has one row a function, `FUNCTIONS` in order, and the sentence above it holds no number. It stops at the first table: the `claude_can` table after it has rows of the same shape |
| `tests/test_docs.py:91-96`, `:106-109` | T9 asks that the README states no function count and no case count: `function_counts(readme) == []` and `case_counts(readme) == []`. The module docstring (`:1-9`) says so. `cases_in`, `CASES` and the `json` import go if nothing else uses them. The helper tests at `:118-140` stay |

**Texts, exact**

| Place | Text |
|---|---|
| `docs/can-and-cant.md:403` | "Twenty-two functions, nothing else:" becomes "Claude may call these functions, and nothing else:" |
| `docs/evals.md:203-206` | "\"No write function\" is every write, by name, in `no_call`: the names in `WRITE_FUNCTIONS` in `app/services/common.py`. A write added later must be added to those lists." |
| `app/mcp_server.py:1` | "Spotter's MCP server: the functions in NEEDS and two resources, and owner_calls, owner_feedback and owner_delete_feedback for Spotter's owner alone." |
| `app/mcp_server.py:35`, `:251` | "each write function (common.WRITE_FUNCTIONS) takes key" |
| `app/mcp_server.py:1071` | "After the others, so everyone's list is those in their order, then this one" |
| `app/mcp_server.py:1107` | "after send_feedback, so everyone's list is the ones above, then these" |
| `app/mcp_server.py:1145` | "so everyone else's list is the functions above, in their order" |
| `app/mcp_schemas.py:1` | "The shapes of every MCP function's arguments and results, the owner's too." The rest of the docstring, `setup_steps` with it, stays |

The README's lines for this item are under "The README" below.

### Files

`README.md`, `docs/can-and-cant.md`, `docs/evals.md`, `app/mcp_server.py` (comments and the module docstring only), `app/mcp_schemas.py` (its first line only), `tests/test_docs.py`, `tests/test_evals.py`, `tests/test_feedback.py`, `tests/test_switch.py`, `tests/test_trace.py`, `tests/test_routines.py`, `tests/test_idempotency.py`, `tests/test_progress.py`, `tests/test_mcp.py`.

### Exit check

Those test files pass. Then a dry run of the rule, reverted after: add one case to `evals/cases.json` and only `CONTRACT_CASES` and the last-four id pins fail. Add one name to `NEEDS` and only the planned pins fail: `FUNCTIONS`, `CONTRACT_NEEDS`, `PUBLISHED`, `CALLS` for a write, the new can-and-cant table test (the table lacks its row) and the order pin at `tests/test_routines.py:833`. No count test fails, and no README test. `git grep -n -i -E "\b(twenty|twenty-two|fourteen|thirteen|nineteen)\b" -- app README.md docs/can-and-cant.md tests` finds only: `tests/test_docs.py`'s word list and its helper test at `:120`, `tests/test_feedback.py:368` (a pattern for notes), `tests/test_mcp.py:254` (history), `tests/test_routines.py:86` (the snapshot) and `tests/test_metrics.py:457-459` (days of food). Test names joined by underscores, such as `tests/test_feedback.py:256`, do not match the pattern. `docs/evals.md:41` keeps its dated record.

The instructions' hash and every tool's hash in `PUBLISHED` hold unedited. If the eval check of the next section is in the tree by then, it is run against `76df357`, as that section says.

**Changes what a model reads: yes, one sentence.** `docs/can-and-cant.md:403`, the `spotter://can-and-cant` resource. The tool list, the descriptions, `whoami`'s notes and the instructions do not change, and the hashes prove it. The comments and docstrings in `app/` are not sent to a model.

## Item 1. The landing says which assistants and plans work

### What exists

| Place | What it says |
|---|---|
| `README.md:31-44`, "## Your assistant" | A row per assistant. The last column, Status, holds the limits. Claude apps: "Free plans get one custom connector. On Team and Enterprise an owner adds it." ChatGPT: "Full access needs Business, Enterprise or Edu. Pro can read only." Gemini app: "US only, 18 or over, personal account, Keep Activity on." Five rows end "Tried" or "From its docs" with no full stop: 36, 38, 39, 41, 42. Line 44 says what "From its docs" means |
| `app/templates/home.html:47-56`, Before you start | "You need an AI assistant that can add a remote MCP server with sign-in." (`:48`) and "Each assistant's plan limits are in the README's table." (`:49`, a link), then the new-account cap |
| `app/templates/home.html:87-97`, `ul.assistants` | Each name, a link to the README's table. Only Claude Code carries lines under it, the command |
| `app/web.py:1036-1041` | `README_TABLE`, and `ASSISTANTS`, the names in the README's order |
| `app/web.py:1124-1132` | `home()` renders the landing |
| `tests/test_web.py:520-533` | Holds `web.ASSISTANTS` to the README rows' names, and each item's link and the command |
| `tests/test_web.py:231-276` (`LANDING_WORDS`), `:380-386` | The landing's words in order, and the Before you start lines, exactly |
| `tests/test_web.py:350-361` | Every GitHub link on the landing, in order: one Before you start link, one a list item |
| `tests/test_web.py:510-517`, `tests/test_pages_text.py:766-800` | The landing may say Claude only inside `ul.assistants`, and never "whoami" or "privacy" |
| `tests/test_switch.py:592-596` | `/`, `/settings`, `/privacy` and `/hi` never say "Read only" or "read only" |
| `app/static/style.css:1123-1125` | `ul.assistants li > p` is styled already, so `tests/test_css.py` needs no change |

The Status facts are the README's, written from each app's docs. Not checked again (no network).

### What changes

Each assistant's line on the landing is the README's Status cell, word for word, under its name in `ul.assistants`, the one place the landing may name a product. One dict in `web.py` holds them. A test holds the dict to the README's last column, so the README stays the one source a person edits.

`app/web.py`, beside `ASSISTANTS`:

```python
# Each assistant's line on the landing: the README table's Status cell, word for word (tests/test_web.py holds them).
ASSISTANT_NOTES = {
    "Claude apps": "Tried on the web. Desktop: from its docs. Free plans get one custom connector. On Team and "
                   "Enterprise an owner adds it.",
    "Claude Code": "Tried with Spotter.",
    "ChatGPT": "From its docs. Full access needs Business, Enterprise or Edu. Pro can only read.",
    "Cursor": "From its docs.",
    "VS Code with Copilot": "From its docs.",
    "Gemini app": "From its docs. US only, 18 or over, personal account, Keep Activity on.",
    "Mistral": "Tried with Spotter.",
    "goose": "From its docs.",
}
```

`home()` passes `assistant_notes=ASSISTANT_NOTES`.

`app/templates/home.html`:

- Line 49 becomes: `<li>Some assistants need a paid plan or have other limits. Each one's limits are under its name below, from <a href="{{ readme_table }}">the README's table</a>.</li>`
- In each `ul.assistants` item, after the link and before Claude Code's command: `<p class="muted">{{ assistant_notes[name] }}</p>`.
- After the list, before "Any other MCP client...": `<p class="muted">"From its docs" means the steps follow that assistant's own docs and have not been tried with Spotter yet.</p>`

`README.md`, the Status cells: Claude Code and Mistral "Tried" become "Tried with Spotter."; Cursor, VS Code with Copilot and goose "From its docs" become "From its docs."; ChatGPT's "Pro can read only." becomes "Pro can only read." (ruling T2). Line 44 is unchanged.

Each new line passed `tr.check` on the page surface, and the template lines passed the T1 template sweep, on 2026-11-15. "Claude" appears only inside `ul.assistants`. The landing's GitHub links keep their count and order: the Before you start link stays, and the new lines carry none.

### Tests

- New, in `tests/test_web.py` beside `test_the_list_of_assistants_is_the_readmes_table_and_sits_after_the_steps`:

```python
def test_each_assistant_s_line_is_its_readme_status_cell():
    readme = (web.HERE.parent / "README.md").read_text(encoding="utf-8")
    table = readme[readme.index("\n## Your assistant\n"):]
    table = table[:table.index("\n## ", 1)]
    rows = [line.strip().strip("|").split("|") for line in table.splitlines() if line.startswith("| [")]
    cells = {re.match(r"\s*\[([^\]]+)\]", row[0]).group(1): row[-1].strip() for row in rows}
    assert cells == web.ASSISTANT_NOTES and tuple(web.ASSISTANT_NOTES) == web.ASSISTANTS
```

- The landing render test checks each item's `<p class="muted">` equals its note.
- `LANDING_WORDS` (`tests/test_web.py:244`, `:253-256`) gains each note after its name, the new Before you start line, and the "From its docs" line. `:385` takes the new Before you start line.

### Files

`app/web.py`, `app/templates/home.html`, `README.md`, `tests/test_web.py`.

### Exit check

`tests/test_web.py`, `tests/test_pages_text.py`, `tests/test_page_rules.py`, `tests/test_switch.py` (the read only check on `/`), `tests/test_hi_there.py` (the hi page names no assistant, unchanged), `tests/test_text_rules.py`, `tests/test_css.py` and `tests/test_docs.py` pass. A one-character change to a README Status cell fails the new test.

**Changes what a model reads: no.** The landing and the README are not served over MCP.

## Item 2. A private contact address on the site

### What exists

| Place | What it says or does |
|---|---|
| `app/templates/_footer.html:1-8` | "Source code", "Contact", "Spotter is an open project, run by one person." Contact links `site_foot.contact`. Its comment names `web.SITE_FOOT` |
| `app/web.py:1083-1084` | `SITE_FOOT = {"source": links.REPOSITORY, "contact": links.REPOSITORY + "/issues"}`: Contact is the public GitHub issues page |
| `app/web.py:1132`, `:1216` | The footer is drawn on the landing (`/`) and the hi page (`/hi`) only |
| `app/templates/privacy.html:108-116`, Contact card | "Questions and problems go on the project's issues page." with a link, or a line naming GitHub when there is no repository. **No route serves this page at `76df357`**: the route tables (`app/web.py:2770-2823`) have no `/privacy`, and `links._PATHS` (`app/links.py:20-32`) has no privacy name. Its date card, `:118-121`, says 14 Nov 2026 |
| `README.md` | No contact line |
| `app/config.py:56-70`, `:184-205` | `Settings` and `load_settings`: no contact setting. The pattern: a field on `Settings`, read in `load_settings` with `_text()` or a small parser, and a malformed value never stops the app |
| `app/` | No e-mail address typed anywhere |
| In the product | `send_feedback`, through the assistant, reaches the owner. No reply goes back (`README.md:66`) |

### What changes

One setting, `CONTACT_EMAIL`, read from the environment in `app/config.py` like the others, set by the owner on the host. No template, no Python file and no doc types the address.

- **Set and well formed:** the footer's Contact link is `mailto:` that address, on `/` and `/hi`. The privacy card gives it as text with the same link, and keeps the issues page for problems with the app.
- **Unset, blank or malformed:** exactly today. Contact goes to the issues page, and the privacy card says today's words. A typo never shows a broken address.
- The footer's words stay "Source code", "Contact", "Spotter is an open project, run by one person.", so `tests/test_web.py:275` and `tests/test_hi_there.py:198` hold.

`app/config.py`:

- The module docstring gains a paragraph: "CONTACT_EMAIL is the address a person can write to, given on the site's footer and the privacy page. Unset or not an address, Contact goes to the project's issues page."
- `Settings`: `contact_email: str | None = None  # the address the footer's Contact gives; unset: the issues page`
- A parser, `_email(value)`: stripped, kept only if it matches `^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$`, else `None`.
- `load_settings`: `contact_email=_email(env.get("CONTACT_EMAIL"))`.

`app/web.py`: `SITE_FOOT` becomes a function read at each render, as the new-account cap is:

```python
def site_foot() -> dict[str, str]:
    """The site footer's links: the source, and Contact, the address from CONTACT_EMAIL or else the issues page."""
    address = get_settings().contact_email
    return {"source": links.REPOSITORY,
            "contact": f"mailto:{address}" if address else links.REPOSITORY + "/issues"}
```

`home()` and `hi_there()` pass `site_foot=site_foot()`. `_footer.html`'s comment names `web.site_foot()`. Its markup is unchanged.

`app/templates/privacy.html`, the Contact card, ready for when the page gets its route:

```
{% if contact_email %}
<p>Questions about your data go to <a class="plain-link" href="mailto:{{ contact_email }}">{{ contact_email }}</a>.</p>
<p>Problems with the app can also go on the project's issues page.</p>
{% elif repository %}  ... today's two lines ...
{% else %}  ... today's line ...
{% endif %}
```

Its date stays (ruling T3).

`README.md`, a short section before "## Licence":

```
## Contact

To reach the person who runs Spotter, use Contact at the foot of the live site. Problems with the code can go on the
issues page.
```

The parser's pattern, the `mailto:` line and the privacy lines passed the T1 sweeps on 2026-11-15: the `@` in a pattern is not read as a word.

### Tests

- `tests/test_config.py`: `CONTACT_EMAIL` unset, blank, and malformed (`"me"`, `"a@b"`, `"a b@c.d"`, `"<x>@y.z"`) give `None`. A good one is kept, trimmed of spaces round it.
- `tests/test_web.py:350`, `test_every_repository_link_is_built_from_the_one_constant`: run unset (the links as today) and set (the last link is `mailto:` the address, so the GitHub list loses `/issues`).
- New: the footer on `/` and `/hi` gives `mailto:` the address when set, and the issues page when unset.
- New: no file in `app/` holds an e-mail address shape, so the address lives only in the environment. The shape it looks for must not match `_email`'s own pattern: the builder runs it against `app/config.py`.
- `tests/test_feedback.py:918-939`, `tests/test_edit_history.py:1360-1367` and `tests/test_idempotency.py:1317` read other cards of `privacy.html` by its source. They hold unchanged.

### Files

`app/config.py`, `app/web.py`, `app/templates/_footer.html` (its comment), `app/templates/privacy.html`, `README.md`, `tests/test_config.py`, `tests/test_web.py`. `tests/test_hi_there.py` only if its footer test is where the `/hi` case goes.

### Exit check

The tests above pass with the setting unset and set, and `tests/test_text_rules.py`, `tests/test_page_rules.py` and `tests/test_hi_there.py` pass. On the live site, after the owner sets `CONTACT_EMAIL` on the host, the footer of `/` links `mailto:` it. A variable change redeploys the service. Whether it costs anything on the host: not checked.

**Changes what a model reads: no.** No MCP text changes.

## Item 5. What the review of the two joins found

A review on 2026-11-15 read the two hand joins of `ec48cdb` (first day onto gym logging) and `52b0e76` (routines onto first day) against the trees each item was reviewed on. Nothing was lost or doubled. One should fix and three low findings, all built here, with item 4's commit, since both touch `docs/can-and-cant.md`.

| # | Finding | Where at `76df357` | Change |
|---|---|---|---|
| J1 | The "Setting up" section a model reads lists what marks each setup step done, and leaves out a live routine. `whoami` has put `routine` among the missing steps since the join (`app/services/account.py:127-128`) | `docs/can-and-cant.md:398`, pinned at `tests/test_hi_there.py:72` | Add "a live routine," after "a live saved food," in both. The first day contract's row `docs/contracts/first-day.md:251` gains the same words |
| J2 | The first day contract still says the tree before the join in four places | `docs/contracts/first-day.md:246`, `:280`, `:290`, `:297`, `:324`, `:330` | One sentence added to the join note at `:389`: the `setup` field names `routine`, `whoami` reads at most 14 statements, the three setup cases forbid `save_routine`, and routines' three cases now close the file after `setup_stays_quiet`. The rows themselves stay as records |
| J3 | The page-words test for the four states of `/hi` lost its check with routines and feedback off at the join | `tests/test_hi_there.py:254-260` | Parametrize it over both off and both on, keeping the as-built assert for both on |
| J4 | The build log says each fix round held, but first day's fix round left one test red, made right by hand at the join | `BUILD_LOG.md`, the entry for gym logging, first day and routines | One sentence under its reviews saying so. No code change |

### Files

`docs/can-and-cant.md`, `tests/test_hi_there.py`, `docs/contracts/first-day.md`, `BUILD_LOG.md`.

### Exit check

`tests/test_hi_there.py`, `tests/test_docs.py` and `tests/test_text_rules.py` pass plain and with `-W error`. A read of the section from "## Setting up" to the next heading names a routine.

**Changes what a model reads: yes,** `docs/can-and-cant.md`, with item 4's line.

## The README

Every change to `README.md` in this package, read at `76df357`. Items 1 and 2 land their own lines. The rest land with item 4.

| Line | Today | Becomes | Item |
|---|---|---|---|
| 7 | "**Status: stages 1 to 6 are live. Every web page is on the new design.**" | "**Status: live. Each stage, and each item built since, is recorded in `BUILD_LOG.md`.**" The design clause goes: it records a change long done | 4 |
| 36, 37, 38, 39, 41, 42 | The Status cells | As item 1 says | 1 |
| 71 | "- 22 MCP functions and two resources, over streamable HTTP." | "- The MCP functions listed in `docs/can-and-cant.md`, and two resources, over streamable HTTP." | 4 |
| 80 | The evals line: "... 35 cases, 3 runs each. At stage 5, ... 48 of 48 runs passed ... Later that day, with three more items: 105 of 105. The runner pins `claude-opus-5-5`. ..." | "- Evals: `claude -p` against a local copy with a throwaway person; the server records every call it received, and the eval judges that, not the model's own account of what it did. The cases are in `evals/cases.json`, 3 runs each. The runner pins `claude-opus-5-5`. Each run's result is recorded in `BUILD_LOG.md`, and how the evals run is in `docs/evals.md`." The run history goes: `BUILD_LOG.md` already records each run (`:419`, `:572`, `:953`, `:1015`, `:1043`), so it gains nothing | 4 |
| 89 | The `docs/contracts/` row: "What each stage had to do, fixed before the builders started" | The row in the block below the table | 4 |
| After 89, in "## Read more" | No developer tools | Three rows, in the block below the table | 4 |
| Before "## Licence" | No contact | The "## Contact" section of item 2 | 2 |

The rows of "## Read more", exact:

```
| `docs/contracts/` | Each stage's and each item's contract: what it had to do, fixed before its builders started |
| `tools/probe.py` | Checks a running server from outside, without a token |
| `tools/mcp_call.py` | Calls one function on a running server the way an assistant would, with a token read from the environment |
| `tools/load.py` | A load test, run only against a local database |
```

Every new line passed the DOC rules (`doc_findings`) on 2026-11-15. T9 finds no function count, no case count and no test count in them, and line 7's text holds no "web app", so the page list T9 reads after line 17 is still the first. The heading "## Your assistant" stays, so the landing's `#your-assistant` links hold.

### Tests that read the README

| Test | What it reads | Change |
|---|---|---|
| `tests/test_docs.py:89-113` (T9) | The function count (now required), the case count, the page list after "web app", no test count | `:91-96` and `:106-109` ask for no count. Item 4 |
| `tests/test_switch.py:626-629` | The "You set the limit." sentence, and "35 cases, 3 runs each." | `:629` goes. Item 4 |
| `tests/test_switch.py:602-611` | No "three settings", "Read only" and the like | None: no new line says them |
| `tests/test_web.py:520-524` | The table's names under "## Your assistant" | None |
| `tests/test_web.py`, the new test of item 1 | Each row's Status cell | New. Item 1 |
| `tests/test_text_rules.py:789`, `:939` (T1, `test_t1_docs`) | Every block, by the DOC rules | None: no exemption names a README line |

## What a model reads, and the eval run

| Text | Changes |
|---|---|
| The server's instructions | No. `INSTRUCTIONS_AS_BUILT` and the 3,927 hold |
| Any tool's title, description or schema | No. Every hash in `PUBLISHED` holds |
| `whoami`'s notes and `setup` | No |
| `docs/can-and-cant.md`, served as `spotter://can-and-cant` | **Yes, line 403** (item 4) **and line 398** (item 5, J1) |
| `docs/evals.md`, `evals/` | `docs/evals.md:203-206` only, which no model reads. `evals/cases.json`, `judge.py` and `run.py` do not change |

So the repo's rule, that a change to a text a model reads gets an eval run, holds for this package on the resource alone. At `76df357` no tool in the tree decides it. If `python -m evals.need` lands before this build, the builder runs it against `76df357` and names each line it prints in the hand-over. Expected: `resource spotter://can-and-cant`. If that tool reads every long string in `app/`, it may also name `app/config.py` for the address pattern of item 2, a string no model is sent. Any other line is a finding. The evals do not run per item or for this package alone. They run once, at the end of the whole build, this package and the units work after it, on `claude-opus-5-5`, and the result goes in `BUILD_LOG.md`. The last whole run, 35 cases at 3 runs, was $16.8061 at list price.

## Work package: one builder, TD1

| Files | Exit check |
|---|---|
| Item 3: `tests/conftest.py`, `tests/test_switch.py`, `tests/test_update_settings.py`, `tests/test_cardio.py`, `tests/test_food.py`, `tests/test_lifts.py`, `tests/test_gym_logging.py`, `tests/test_routines.py`. Item 4: `README.md`, `docs/can-and-cant.md`, `docs/evals.md`, `app/mcp_server.py` (comments and its docstring), `app/mcp_schemas.py` (its first line), `tests/test_docs.py`, `tests/test_evals.py`, `tests/test_feedback.py`, `tests/test_switch.py`, `tests/test_trace.py`, `tests/test_routines.py`, `tests/test_idempotency.py`, `tests/test_progress.py`, `tests/test_mcp.py`. Item 1: `app/web.py`, `app/templates/home.html`, `README.md`, `tests/test_web.py`. Item 2: `app/config.py`, `app/web.py`, `app/templates/_footer.html`, `app/templates/privacy.html`, `README.md`, `tests/test_config.py`, `tests/test_web.py`, `tests/test_hi_there.py` if its footer test takes the `/hi` case. Item 5: `docs/can-and-cant.md`, `tests/test_hi_there.py`, `docs/contracts/first-day.md`, `BUILD_LOG.md`, built with item 4. Built one item at a time, a fresh builder an item, and committed after each: four commits, in the order 3, 4 with 5, 1, 2. One review after the four, of the whole range, then one fix round as its own commit. Any other test file only where an assertion fails because of this contract, each named in the hand-over | Each item's exit check above. The files touched pass plain and with `-W error`. The Postgres run of item 3 passes. The dry run of item 4 fails only the planned pins. The eval check, if in the tree, names what the section above expects. The whole suite runs once at the end, by the commit hook. No eval in this package |

## Known limits

| Limit | Why it stands |
|---|---|
| A landing note is only as true as the README's cell. The cells were not checked against each app's docs again | No network in this work. The README stays the one place to fix them |
| The address shows in the page's source to anyone | The owner's choice of a Spotter-only address |
| The privacy page's Contact card is not seen by anyone until the page gets its route | That route is its own work |
| `tests/test_gym_logging.py`'s statement counts may differ on Postgres | Left until a Postgres run says, as item 3 rules |
| The last-four id pins move by hand when a case is appended | Ruling T4: an order, kept on purpose |

## Not in this work

A route for the privacy page. A contact form. A reply to feedback. A check of each assistant's limits against its docs. A change to the instructions, a tool or `whoami`. The units work, built after this one. The earlier contracts, which are records of their day: `docs/contracts/routines.md:80` says 22 functions, and stays.
