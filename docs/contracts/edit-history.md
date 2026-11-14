# Edit history contract: an edit keeps the old value

**Goal: an edit never loses what was there. The values from before each edit are kept, and one call, or one click, puts them back.**

The owner asked for this to be scoped and built once the idempotency work had passed ("scope and build c", 2026-11-14). It follows that work: it takes the next migration number, and an undo is a write like any other, key and all.

What exists before this work, read in the code on the idempotency branch (`f1bceeb`):

| Piece | State |
|---|---|
| An edit | `edit_entry` overwrites the row in place and keeps its id (`app/services/entries.py`). The old values are in the reply's `before` and nowhere else |
| A lift session's sets | Editing the list replaces the `lift_set` rows. The old ones are deleted for good |
| A food entry's kcal | Editing it also drops the link to its saved or USDA food, and its source becomes "you said" |
| A cardio entry | Any real change works the calorie estimate out again from today's body weight. The old estimate is lost |
| A saved food | `save_food` with the same name overwrites it in place, and brings a deleted one back. Entries already logged keep their own kcal |
| A goal | `set_goal` with `goal_id` overwrites its target, date, text or status |
| A delete | Soft: the row stays with `deleted_at` set. `restore_entry` clears it. For a weigh-in or a measurement, restoring swaps it with the live row of that day |
| A history, a version, an old value | None. No table, no column |
| The web edit forms | The page reads the entry, the form posts later, in another transaction. A form drawn before a newer change overwrites that change |
| Sent twice | An edit sets absolute values, so the same edit again changes nothing. Every write takes a key (contract `idempotency.md`) |
| What the assistant is told | `docs/can-and-cant.md`: "An edit keeps no old values: it cannot be undone, only edited again." A test pins that sentence |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **One table, `entry_version`,** by migration `0007`: the person, the kind, the entry's id, when, who (web or claude), why (`edit` or `undo`), the request's id, and `before`: the row as it stood, as JSON, a lift session's sets with it. Deleted with the account | One shape for every kind |
| 2 | **Which writes keep one:** every `edit_entry` that changes something, on the five entry kinds, and every `save_food` that changes a food that already exists. Kept in the same transaction as the edit, before the row changes. A call that changes nothing keeps none | These are the six kinds `restore_entry` already takes |
| 3 | **Undo: `restore_entry` gains one optional argument, `undo_edit`.** With it, on a live entry, the entry goes back to its newest kept version: its sets, its link to a saved or USDA food, its cardio estimate as it was. Without it, `restore_entry` does what it does today. It never deletes and never restores: on a deleted entry it answers as an edit does | One function already means "bring it back" |
| 4 | **An undo is itself an edit.** It keeps the values it replaces, so an undo run again brings the edit back, and no undo can lose anything | No second function for redo |
| 5 | **An undo sent twice is run once.** An undo of the same entry within 10 minutes of the last one changes nothing: the entry comes back as it stands, with `duplicate: true`. `restore_entry` gains `allow_repeat`, as the logs have: with it the undo runs again, which brings the edit back. With a key, the key decides, as for any write | Without this, undo would be the one write that a retry turns round. The 10-minute rule and its argument are the ones the assistant already knows |
| 6 | **No version kept:** `nothing_to_undo`. "This entry has no earlier values kept." Fix: "Edit it with edit_entry." | A clear answer for an entry never edited, or edited over 90 days ago |
| 7 | **A weigh-in or measurement whose undo moves it back onto a day that holds another live one** follows the restore rule: the other row is soft-deleted and named under `replaced` | The one-a-day rule holds, and nothing is erased |
| 8 | **How much is kept:** the newest 10 versions of an entry, each for 90 days. The eleventh drops the oldest, in the edit's own transaction. Older ones are deleted by the call log's own sweep, in a transaction of their own | The last few edits are what a person wants back. The table cannot grow without end |
| 9 | **On the web:** after an edit lands, the mark on the page offers Undo, as it does after a delete. One click posts the undo. After an undo the page says so and offers to bring the edit back, which posts the undo with `allow_repeat` | The pattern and its macro exist. A double click is already one write, by the form's key |
| 10 | **A stale form is refused.** Both edit forms carry the entry's `updated_at` as the page drew it. The service checks it under the person's lock: if the entry changed since, nothing is saved and the answer is `entry_changed`. The page says: "This entry changed since this page was drawn. Reload and try again." Through MCP the argument is not offered | Today the form silently overwrites the newer change |
| 11 | **The export gains one file,** `entry_versions.csv`: kind, entry id, when, who, why, and the values as JSON | The export is every row of the person's |
| 12 | **No result gains a required field.** The edit's reply already carries `before`. An undo's reply is `restore_entry`'s own shape, with a summary that says the edit was undone | A client holding the old function list keeps working |
| 13 | **What the assistant is told.** `docs/can-and-cant.md`'s sentence becomes: "An edit keeps the values from before it: `restore_entry` with `undo_edit` brings them back. The newest 10 are kept per entry, for 90 days." `restore_entry`'s description says the same, and what `allow_repeat` does, in two sentences. The instructions do not change | The sentence it reads today would be false |
| 14 | **The trace.** `undo_edit` joins the known names. A version's row holds the request's id, so an edit and its trace row can be joined | The owner can see which call changed an entry, never to what |
| 15 | **The privacy page says so:** the values from before each edit are kept, the newest 10 an entry, for 90 days, and they go with the account and into the export. Its date moves | The page lists what is stored |
| 16 | **One eval case more,** `undo_edit`: a weigh-in edited by mistake, the person asks to undo it, and the server must receive `restore_entry` with `undo_edit` and hold the first value again. 21 cases, and every place that states the count follows | The new argument is text a model reads and must pick by itself |

## Errors added

| Code | When | Fix text |
|---|---|---|
| `nothing_to_undo` | `undo_edit` on an entry with no version kept | "Edit it with edit_entry." |
| `entry_changed` | A web edit form older than the entry's last change | "Reload and try again." |

## Work package: one builder

| Package | Files | Exit check |
|---|---|---|
| H1 | `app/services/entries.py`, `app/services/food.py` (`save_food`), `app/services/export.py`, `app/services/common.py`, `app/calllog.py` (the known name, the sweep), `app/models.py`, `app/mcp_server.py`, `app/mcp_schemas.py`, `app/web.py`, `app/templates/edit.html`, `app/templates/food_edit.html`, `app/templates/privacy.html`, the templates that draw the mark after an edit, `docs/can-and-cant.md` (the one sentence), `evals/cases.json`, `migrations/versions/0007_*.py` (new), `tests/test_edit_history.py` (new). Existing test files only where an assertion now fails because of a decision, each change named in the report | For each of the six kinds: edit, then undo, and every column reads as before the edit, the sets, the food link and the cardio estimate among them; undo again with `allow_repeat` and the edit is back. An undo sent twice within 10 minutes runs once, with and without a key. An edit that changes nothing keeps no version. The eleventh version drops the oldest. A version 91 days old is swept. `nothing_to_undo`. The one-a-day swap on an undo. The web's Undo after an edit, the way back after an undo, and the stale form refused with nothing saved, on both edit forms. The export's new file. One person can never undo, read or export another's version. The migration on SQLite: up, the models match, down. The new eval case loads and its seed runs. The tests for the files touched pass, plain and with `-W error` |

Not in this work: a goal's old target (a goal changes through `set_goal`, which is given the new values); a link from a replaced weigh-in to the row that replaced it; a list of an entry's versions for the assistant or on a page; the stale check through MCP, where a key covers a late retry; the docs (`README.md`, `BUILD_LOG.md`), done after the review.

**An eval run follows the build:** `restore_entry`'s schema and the can-and-cant resource are text a model reads. 21 cases, 3 runs each, on `claude-opus-5-5`. The cost is told to the owner before it runs.
