# Idempotency contract: every write safe to send twice

**Goal: a write that is sent twice is done once. A caller can send a key with any write, and the same key again gets the first answer and writes nothing. A web form cannot be submitted twice. The key has the shape the confirm card's draft id will have, so the card can use the same field later.**

Approved by the owner on 2026-11-14. Where this text was silent the build made three calls, put to the owner the same day and approved: on the web a form sent again with a field changed is a new write (decision 11), a send that arrives while the first is still running waits for its answer (decision 5), and expired keys are swept hourly (decision 9). The review's rulings are in decisions 8, 12 and 14 and under "Known limits".

What exists before this work, read in the code on `e061bd8`. The trace work and the USDA fix since changed none of it; the counts were checked again on `ad85304`:

| Piece | State |
|---|---|
| One write at a time per person | `lock_user` is the first statement of every write, in the write's own transaction (`app/services/common.py`). Two sends of one call run one after the other |
| The 10-minute guard | The five `log_` functions refuse the same content logged again within 10 minutes, with `duplicate: true` (`find_duplicate`, a SHA-256 of the entry's own values). After 10 minutes the same call writes again: a second session, meal or cardio entry; for a weigh-in or a measurement, a new identical row that replaces the first |
| What the guard cannot see | A retry whose date or meal Spotter filled in differently (across midnight, across a meal boundary), a reworded note or description, any call with `allow_repeat`, an entry edited or deleted since |
| A new goal | Never guarded. A second identical goal is written, with a warning (`app/services/goals.py`) |
| Edits, settings, a goal's change | They set absolute values, so the same call again changes nothing. A late retry of an older edit undoes a newer one |
| Delete and restore | A second delete answers `not_found`, word for word as a wrong id does. A second restore answers `not_deleted` |
| Import | Matched by content with no time window. Sent again, the same rows write nothing, except in the two documented cases |
| A reply that fails after the write | A write can commit and still answer `internal_error`, whose fix says "Retry once" (`Wrapper.call`). Since the trace work the call's row reads `written` for it; the caller is told nothing of that |
| Web forms | 21 forms in 9 templates. Each carries the session's one CSRF token. Nothing stops a second click: every double submit runs twice |
| A key from the caller | None. No function, form or table takes or keeps one. No draft or pending write exists |
| Transactions | Every write is one transaction. Outside it: the call log, and `log_food`'s USDA lookup and cache write, which run before the lock |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **The key.** Any write may carry `key`: 8 to 64 characters, letters, digits, `-` and `_`. Anything else is `invalid_key` and nothing is written. Left out, the call runs as it does today | Short enough to say, wide enough for a random id. The confirm card's draft id will be issued in this shape and sent in this field |
| 2 | **One table, `write_key`,** by migration `0006`: the person, the key, the function, a fingerprint, the first answer, when. One row per person and key. Deleted with the account | One place, small, and the later draft is a row here with its answer still empty |
| 3 | **The fingerprint.** A SHA-256 of the function's name and the arguments as sent, without the key. Never the arguments themselves | To tell the same call sent again from a key used for something else |
| 4 | **The key is claimed inside the write's own transaction, under the person's lock.** `lock_user` reads the call's key from the context and writes the row in the same transaction as the entry. So the key exists if and only if the write committed, and two sends at once cannot both pass. No service is edited | The only place every write already goes through first. A key written before or after the write could say done when it was not |
| 5 | **The same key and the same call again:** nothing runs. The first answer comes back as it was, word for word, and the call's row reads `repeat`. A send that arrives while the first is still running waits for that answer: a look every 100 ms, for up to 2 seconds | To the caller a retry looks exactly like the answer it missed |
| 6 | **The same key with a different call:** `key_reused`, nothing written. "This key was used for another call." Fix: "Send a new key for a new write." | A caller that reuses one key for everything must not have its second write swallowed |
| 7 | **What does not keep its key:** a refused call (a typed error), an import preview, and a crash, which rolls back. The same key can be sent again for each | A refusal such as "wait 5 minutes" must not be the answer for the next day |
| 8 | **When the first answer was lost** (the process stopped between the write and keeping its answer, or the answer was over 64 KB): `already_done`, nothing written. "This call already ran. Its answer was not kept." Fix: "Read it back with get_log. Send a new key for a new write.", naming the function that reads it: `get_goals` for `set_goal`, `whoami` for `update_settings`, `find_food` for `save_food`, `get_log` for the other nine. `delete_entry` and `restore_entry` also take a saved food, so theirs reads "get_log, or find_food for a saved food". A kept key with no answer is waited for only while its row is under 60 seconds old. Older, it is `already_done` at once, before any work | Rare, and it must still never write twice. A lost answer must not cost every later send a 2-second wait |
| 9 | **Kept 24 hours.** `KEY_HOURS = 24`. Older rows are deleted by the call log's own sweep, at most once an hour, and are never honoured, swept or not. After that the key is free again | A retry comes in seconds, a confirmed draft in minutes or hours. The stored answer is the person's data, so it is not kept long |
| 10 | **Through MCP:** the 12 write functions each gain one optional argument, `key`, with one description: "Optional. A key of your own for this one write: 8 to 64 letters, digits, - or _. Send the same key when you send the same call again: nothing is written twice and the first answer comes back. A new write takes a new key." No result changes. The instructions do not change | The schema says it where a model reads it. The instructions had 3,678 of their 4,096 characters used when read on 2026-11-14 |
| 11 | **On the web:** every form that writes carries a hidden key, made new each time the page is drawn. A second submit of the same form writes nothing and lands where the first did. A form that makes two writes gives each its own key. The write's own key is worked out from the form's key and what the write sends, so the same form sent again with a field changed (back, fix a typo, send) is a new write, and the web never answers `key_reused`. A missing or malformed form key runs the write with no key. A lost answer shows one page: 409, "Already done" | Closes every double click and every back-and-resubmit, for 24 hours |
| 12 | **A new goal gets the guard the logs have.** The same new goal as an open one set in the last 10 minutes (kind, exercise, measurement kind, distance, target, date, text, and the start it would be stored with) is not written again: the first one comes back with a warning that says so, and its call's row reads `duplicate`. A goal that differs in any of these is written as before, with its warning | The one write with no guard at all today |
| 13 | **Calls with no key keep the 10-minute guard as it is.** `allow_repeat`, the replace rule for a weigh-in, and the second delete's `not_found` do not change | A client that sends no key sees no change |
| 14 | **The trace.** `outcome` gains `repeat`. `step` gains `key`, for the three new errors. `key` joins the known names, so the row says a key was sent. The key's value is never in the call log | The owner sees a retry as a retry |
| 15 | **A fast look first.** Before the work, the wrapper and the web look the key up with no lock. A known key with its answer returns at once, before any USDA lookup. The look under the lock is the one that decides | A repeated `log_food` should not call USDA again |

## Errors added

| Code | When | Fix text |
|---|---|---|
| `invalid_key` | A key that is not 8 to 64 of the allowed characters | "Send 8 to 64 letters, digits, - or _, or leave the key out." |
| `key_reused` | A kept key sent with a different call | "Send a new key for a new write." |
| `already_done` | A kept key whose first answer was not kept | "Read it back with get_log. Send a new key for a new write.", with the function that reads it (decision 8) |

## Work package: one builder

| Package | Files | Exit check |
|---|---|---|
| I1 | `app/services/common.py`, `app/calllog.py`, `app/models.py`, `app/mcp_server.py`, `app/web.py`, `app/services/goals.py`, the 9 templates that hold a form, `migrations/versions/0006_*.py` (new), `tests/test_idempotency.py` (new). Existing test files only where an assertion now fails because of a decision, each change named in the report | For each of the 12 write functions through MCP, and each form on the web: sent twice with one key, one write, the same answer, and the second row reads `repeat`. Two sends at once with one key: one write. A refused call, a preview and a crash each leave the key free. A key reused for another call is refused. A lost answer is `already_done`. A row 25 hours old is swept and its key is free. A double submit of every form writes once and lands on the same page. The new goal guard. A call with no key behaves, result for result, as before. Migration `0006` on SQLite: up, the models match, down. The tests for the files touched pass, plain and with `-W error` |

## Known limits, accepted

| Limit | Why it stands |
|---|---|
| The first answer comes back for 24 hours whatever happened since. Delete with a key, restore, then the same delete with the same key again answers "deleted" while the entry is live | Decisions 5 and 11 as written: a new write takes a new key, and the schema says so. On the web it needs the browser to bring back the exact old page, and the pages are `no-store` |
| Six forms land a later second send on the same page marked unchanged, not as a repeat: edit an entry, confirm an estimate, a goal's status, save a food, edit a saved food, the targets | Their routes read first and write nothing when nothing differs. Two sends that overlap do get the repeat |
| A refused call's key row commits with the refusal and is deleted a moment later. A process that stops in between leaves `already_done` on that key for 24 hours, though nothing was written | It cannot be closed without editing every service. The fix text sends the caller to read back and use a new key |
| The fingerprint is of the arguments as sent: `80` and `80.0` with one key are two calls, and the second is `key_reused` | Nothing is written, and the fix says what to do |
| A key sent as a number is refused by the MCP library's own type check, in its words, not as `invalid_key` | As for any wrongly typed argument |
| A repeat is never refused by the write limit. Its row counts toward the limit | A repeat runs no service |
| Two sends at once were run on SQLite only | On Postgres the unique rule on person and key means the worst case is an error, never two writes |

Not in this work: the confirm card and its drafts; telling assistants in the instructions to always send a key; the stale edit form that overwrites a newer change (item C's ground); cardio's guard leaving notes out; a retry across midnight or a meal boundary with no key; the docs (`README.md`, `docs/can-and-cant.md`, `BUILD_LOG.md`), done after the review.

**An eval run follows the build:** every write function's schema gains an argument, which is text a model reads. 20 cases, 3 runs each, on `claude-opus-5-5`. The cost is told to the owner before it runs.
