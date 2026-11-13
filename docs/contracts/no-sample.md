# No sample data: the contract

**Goal: Spotter has no sample data. No button adds it, no button removes it, no page or reply mentions it, and no table carries a mark for it. The four weeks of data stay only as a seed the tests and evals use, outside `app/`.**

What exists before this work, read in the code on `cd2590e`:

| Piece | State |
|---|---|
| The generator | `app/services/sample.py`: `sample_state`, `add_sample_data`, `remove_sample_data`. Four weeks of everything and three goals, from `SEED` |
| Pages | Today offers "Add sample data" on an empty account (`day.html`). Every signed-in page shows `sample_banner.html` while sample rows exist. Settings' Your data card has "Remove sample data". Each row carries a `sample` tag (`_parts.html`, `day.html`, `goals.html`, `foods.html`). The landing says to press Add sample data, and its picture carries a sample tag and the caption "Sample data." |
| Routes | `POST /settings/sample`, `POST /settings/sample/remove` (`app/web.py`) |
| MCP replies | `is_sample` on rows, `includes_sample` and a sample note on reads (`app/mcp_schemas.py`, `SAMPLE_NOTE` in `app/services/common.py`, stage 5 rule 16) |
| Import | Refused while the account holds sample rows (`app/services/imports.py`) |
| Export | An `is_sample` column where the table has one (`app/services/export.py`) |
| Tables | `is_sample` on every entry table (`EntryColumns`), on `saved_food` and on `goal` (`app/models.py`) |
| Evals | Two cases seed with `{"service": "add_sample_data"}` (`evals/cases.json`, `evals/judge.py`) |
| Tests | `tests/test_sample.py`, and the fixed-day sample account many page and text tests read |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **Pages.** Gone: the Add offer on Today, `sample_banner.html` and every include of it, Settings' banner, Remove button and its line, every `sample` tag, `.tag-sample`, `.sample-banner` and `--sample` in `style.css`, the sample strips in `DAY_STRIPS`, `SAMPLE_SUFFIX` and what uses it, `sample_rows_of`, and every `sample_state` call. An empty account's Today card says, in one or two short lines, that nothing is logged yet and that the person tells their assistant what they ate, lifted or did, and it shows here. The landing loses its "press Add sample data" line; its picture loses the sample tag, and its caption "Sample data." becomes "An example." | He will not show it to anyone who adds sample data |
| 2 | **Routes.** `/settings/sample` and `/settings/sample/remove` are removed. A request to either gets what any unknown path gets | Nothing links to them |
| 3 | **MCP replies.** No result carries `is_sample`, `includes_sample` or the sample sentence. `SAMPLE_NOTE`, `INCLUDES_SAMPLE_TEXT` and `SAMPLE_NOTE_TEXT` go. A `note` that carried only the sample sentence goes; a `note` that also carries other text keeps that text and its description says only what is left. Every function and its other fields are unchanged | The assistant should never hear of sample data |
| 4 | **Import and export.** The import's refusal over sample rows goes. The export has no `is_sample` column | There are no sample rows |
| 5 | **The database.** Migration `0004`: hard-delete every row with `is_sample` true (a session's sets with it; a real food entry that points at a sample saved food loses the link, as removal did), then drop `is_sample` from every table that has it. Downgrade adds the columns back, false. The models lose the column | Sample rows left behind would show as the person's own |
| 6 | **The seed.** The generator moves to `evals/four_weeks.py`: one function that writes the same four weeks and three goals into an empty account, unmarked, with the same `SEED`, draw order and numbers, so every fixed-day figure the tests hold stays. No state function, no remove, no `(sample)` name. The eval seed becomes `{"service": "four_weeks"}`, and `evals/judge.py` resolves it there. `app/` never imports from `evals/` | The two eval cases and the page tests need a full account |
| 7 | **The write list.** `add_sample_data` and `remove_sample_data` leave the write function names in `app/services/common.py` and `app/services/__init__.py`. Old call-log rows that name them stay | History is history |

## Work package: one builder

| Package | Files | Exit check |
|---|---|---|
| N1 | `app/web.py`, `app/templates/*.html` (`sample_banner.html` deleted), `app/static/style.css`, `app/mcp_schemas.py`, `app/models.py`, `app/services/*.py` (`sample.py` deleted), `migrations/versions/0004_*.py` (new), `evals/four_weeks.py` (new), `evals/judge.py`, `evals/cases.json`, `tests/*` (`test_sample.py` replaced by tests of the seed, the migration and the gone routes) | `grep -rni sample app/` finds nothing. Every decision has a test: the empty Today card's lines; both routes gone; no MCP result schema or result holds `sample`; the migration on SQLite deletes a marked row of each table, drops the columns, and downgrades; the seed writes the same numbers as before (the fixed-day figures the tests already hold). The full suite passes, plain and with `-W error` |

Not in this work: the docs (`README.md`, `docs/can-and-cant.md`, `docs/evals.md`, `BUILD_LOG.md`), done by the lead after the review; earlier contracts, which are history; how the buttons look, which is the design work; any eval run.
