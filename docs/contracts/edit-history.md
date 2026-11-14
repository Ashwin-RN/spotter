# Edit history contract: an edit keeps the old value

**Goal: an edit never loses what was there. The values from before each edit are kept, and one call, or one click, puts the last ones back.**

The owner asked for this to be scoped and built once the idempotency work had passed ("scope and build c", 2026-11-14). It follows that work: it takes the next migration number, and an undo is a write like any other, key and all.

Built on 2026-11-14, and this text is brought up to what was built. The first build also had a way back after an undo, with a second argument and a third kind of version. The owner read the card and asked for less: "undo doesn't make much sense for me unless it's like an immediate undo", and "If they want to edit it again, let them edit it again." A trim round took the way back out the same day. Decisions 4, 5, 7, 9 and 17 are where this text differs from the one first approved.

What existed before this work, read in the code on the idempotency branch (`f1bceeb`):

| Piece | State |
|---|---|
| An edit | `edit_entry` overwrote the row in place and kept its id (`app/services/entries.py`). The old values were in the reply's `before` and nowhere else |
| A lift session's sets | Editing the list replaced the `lift_set` rows. The old ones were deleted for good |
| A food entry's kcal | Editing it also dropped the link to its saved or USDA food, and its source became "you said" |
| A cardio entry | Any real change worked the calorie estimate out again from that day's body weight. The old estimate was lost |
| A saved food | `save_food` with the same name overwrote it in place, and brought a deleted one back |
| A delete | Soft: the row stays with `deleted_at` set. `restore_entry` clears it. For a weigh-in or a measurement, restoring swapped it with the live row of that day |
| A history, a version, an old value | None. No table, no column |
| The web edit forms | The page read the entry, the form posted later, in another transaction. A form drawn before a newer change overwrote that change |
| Sent twice | An edit sets absolute values, so the same edit again changes nothing. Every write takes a key (contract `idempotency.md`) |
| What the assistant was told | `docs/can-and-cant.md`: "An edit keeps no old values: it cannot be undone, only edited again." A test pinned that sentence |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **One table, `entry_version`,** by migration `0007`: the person, the kind, the entry's id, when, who (web or claude), why (`edit` or `undo`), the request's id, and `before`: the row as it stood, as JSON, a lift session's sets with it. Deleted with the account | One shape for every kind |
| 2 | **Which writes keep one:** every `edit_entry` that changes something, on the five entry kinds, and every `save_food` that changes a food that already exists. Kept in the same transaction as the edit, after every check has passed and before the row changes. A call that changes nothing keeps none, and `save_food` with the same values writes nothing | These are the six kinds `restore_entry` already takes |
| 3 | **Undo: `restore_entry` gains one optional argument, `undo_edit`.** With it, on a live entry, the entry goes back to its newest kept version: its sets, its link to a saved or USDA food, its cardio estimate as it was. Without it, `restore_entry` does what it did. With it, the kind may also be `saved_food`. It never deletes and never restores: on a deleted entry it answers as an edit does | One function already means "bring it back" |
| 4 | **One step, once.** An undo puts back the values from before the last edit, and keeps what it replaces, so nothing is lost. A second undo changes nothing, at any age: the entry comes back as it stands, with `duplicate: true`. To get an edit back, the entry is edited again. There is no way back and no second argument | The owner's word: an immediate undo, and nothing more |
| 5 | **An undo sent twice is run once.** By decision 4, the second changes nothing. With a key, the key decides, as for any write. An edit after an undo keeps a version, so its own undo runs | A retry must not turn an entry round |
| 6 | **No version kept:** `nothing_to_undo`. "This entry has no earlier values kept." Fix: "Edit it with edit_entry.", or `save_food` for a saved food | A clear answer for an entry never edited, or edited over 90 days ago |
| 7 | **An undo never replaces another entry.** One that would move a weigh-in or a measurement back onto a day that holds another live one is refused with `day_taken`, as an edit is, before anything is kept | First written as a swap. The review found that a page swapped without a word, and that no page brings the other entry back |
| 8 | **How much is kept:** the newest 10 versions of an entry, each for 90 days. The eleventh drops the oldest, in the edit's own transaction. Older ones are deleted by the call log's own sweep, in a transaction of their own, and are never put back, swept or not | The table cannot grow without end |
| 9 | **On the web:** after an edit lands, the mark on the page offers Undo, as it does after a delete. One click posts the undo. After the undo the page says so in a plain line, with no button | The pattern and its macro exist. A double click is already one write, by the form's key |
| 10 | **A stale form is refused.** Both edit forms carry the entry's `updated_at` as the page drew it. The service checks it under the person's lock: if the entry changed since, nothing is saved and the answer is `entry_changed`. The page says nothing was saved and where to go. A stale form whose fields all equal the entry as it stands is not refused: it lands unchanged, as the second click of a double click must. Through MCP the argument is not offered | Before this the form silently overwrote the newer change |
| 11 | **The export gains one file,** `entry_versions.csv`: kind, entry id, when, who, why, and the values as JSON, with a set's exercise and a cardio activity by name, and the person's own letters as written | The export is every row of the person's, and gives names for what the shared lists hold |
| 12 | **No result gains a required field.** An undo's reply is `restore_entry`'s own shape, with four optional fields: `undone`, `duplicate`, `before` and `summary` | A client holding the old function list keeps working |
| 13 | **What the assistant is told.** `docs/can-and-cant.md` says an edit keeps the values from before it, that `restore_entry` with `undo_edit` brings them back once, that a second undo changes nothing, that to get an edit back the entry is edited again, and that an undo onto a taken day is refused. `restore_entry`'s description says the same in two sentences. The instructions do not change | The sentence it read before would be false |
| 14 | **The trace.** `undo_edit` joins the known names, and `entry_changed` has its step. A version's row holds the request's id, so an edit and its trace row can be joined | The owner can see which call changed an entry, never to what |
| 15 | **The privacy page's text says so,** in a card of its own: the values from before each edit are kept, the newest 10 an entry, for 90 days, and they go with the account and into the export | The page lists what is stored |
| 16 | **One eval case more,** `undo_edit`: a weigh-in edited by mistake, the person asks to undo it, and the server must receive `restore_entry` with `undo_edit` and hold the first value again. A seed of a case may now be `edit_entry` on a row seeded before it. 21 cases | The new argument is text a model reads and must pick by itself |
| 17 | **On a page, bringing a deleted entry back never replaces another one either.** The page's Undo after a delete, onto a day that holds another live weigh-in or measurement, is refused, and both stay as they were. Through MCP the swap is unchanged: its answer names what was set aside | An older fault, found by the review and fixed on the owner's word the same day |

## Errors added

| Code | When | Fix text |
|---|---|---|
| `nothing_to_undo` | `undo_edit` on an entry with no version kept | "Edit it with edit_entry." |
| `entry_changed` | A web edit form older than the entry's last change | "Open it again and make the change." |

`day_taken` is not new: an undo and a page's restore now answer it too.

## Known limits, accepted

| Limit | Why it stands |
|---|---|
| Undo reaches one edit back. Older versions are kept and exported, and no function reaches them | The owner's word: an immediate undo. A list of versions is not in this work |
| The Undo on a page undoes the entry's newest edit. If an assistant edited the entry after the page was drawn, that is the edit undone | Nothing is lost: the undo keeps what it replaces |
| The mark that draws Undo is read from the address, so a made-up address can draw it for any entry of the person's own. A click undoes that entry's last edit | It needs the person's own signed-in click, and the delete's mark has had the same shape since before this work |
| Two changes of one entry inside one clock step get the same `updated_at`, so a form drawn between them is not seen as stale | The host's clock steps in microseconds |
| A call that still sends the removed second argument runs as if it were not sent | The `mcp` library drops an argument the schema does not name |

## Work package: one builder

| Package | Files | Exit check |
|---|---|---|
| H1 | `app/services/entries.py`, `app/services/food.py` (`save_food`), `app/services/export.py`, `app/services/common.py`, `app/calllog.py` (the known name, the sweep), `app/models.py`, `app/mcp_server.py`, `app/mcp_schemas.py`, `app/web.py`, `app/templates/edit.html`, `app/templates/food_edit.html`, `app/templates/privacy.html`, the templates that draw the mark after an edit, `docs/can-and-cant.md`, `evals/cases.json`, `evals/judge.py`, `evals/run.py`, `docs/evals.md`, `migrations/versions/0007_*.py` (new), `tests/test_edit_history.py` (new). Existing test files only where an assertion now fails because of a decision, each change named in the report | For each of the six kinds: edit, then undo, and every column reads as before the edit, the sets, the food link and the cardio estimate among them. A second undo changes nothing, a second apart and 11 minutes apart, with and without a key. An edit that changes nothing keeps no version. The eleventh version drops the oldest. A version 91 days old is swept. `nothing_to_undo`. An undo onto a taken day is refused and nothing is kept. The web's Undo after an edit, the plain line after it, and the stale form refused with nothing saved, on both edit forms. A page's restore onto a taken day is refused, and the same restore through MCP swaps. The export's new file. One person can never undo, read or export another's version. The migration on SQLite: up, the models match, down. The new eval case loads and its seed runs. The tests for the files touched pass, plain and with `-W error` |

Not in this work: a way back after an undo; a goal's old target (a goal changes through `set_goal`, which is given the new values); a list of an entry's versions for the assistant or on a page; the stale check through MCP, where a key covers a late retry; a confirmation before an edit is written, which the owner has named as the better guard and which is its own later work.

**An eval run follows the build:** `restore_entry`'s schema and the can-and-cant resource are text a model reads. 21 cases, 3 runs each, on `claude-opus-5-5`. The cost is told to the owner before it runs.
