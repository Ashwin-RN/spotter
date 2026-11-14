# Hi there contract: the page an assistant shares with a new person

**Goal: a new person's assistant hands them one link in its first reply. The page behind it says what to say, what Spotter keeps and how to bring history over. Nothing on it is typed in by the person, and nothing on it can fall behind the code.**

The owner's words that bind it. 2026-11-12: "There should be an easy way to onboard. Let it be AI native only through the AI itself, but maybe share a link that they can read through what are the capabilities." The same day, on a guide: "how to port information from outside, how to set it up properly, what are the things that you can really use". 2026-11-13: "We also need to make a hi there page that the AI itself shares with the new user". The brief for this build (2026-11-14) puts it last: "Its content (goal types, inputs, import steps) comes from the input catalogue ... If that is not approved yet, build the page frame and the link only."

The input catalogue is not written and not approved (looked for on 2026-11-14). So this contract is the fallback: the frame and the link. Every fact on the page is one the code or another page already states.

It is built on the edit-history work, at commit `d890103`. **The feedback work is built beside it, on a tree of its own, and the two are joined after.** They were joined on 2026-11-14: on the joined tree there are 25 eval cases and 20 functions for everyone, and the page draws its feedback line. So the tree this is built on holds no feedback function, and every count in this contract is the count on that tree. See "Where it meets the feedback work".

What exists before this work, read in the code at `d890103`:

| Piece | State |
|---|---|
| A page for a new person | None. 15 templates, none a guide or a help page |
| The landing, `/` | Open signed in and out (`web.home`, `home.html`). It says how to connect: three steps and a list of assistants. Its "Things to say first" sentences are typed in the template. A test holds the whole page word for word |
| The privacy page | Written as `privacy.html`. No route draws it and no page links to it. This work leaves it so |
| Who may open a page | `/` and `/sign-in` are open. Every page in `web.PAGES` sends a signed-out visitor to sign-in and sits behind the page limit |
| Link names | `links._PATHS` holds 9. A test pins the set, a test holds that each has a page, and a test asks the README's page list for each top-level name but `home` and `sign_in` |
| The frame | `web.FRAMES` gives each template its page class, width class and tab. The stylesheet has a one-column width class of 720 px, `privacy-page`, that no routed page uses |
| What `whoami` carries | `links` with `settings` and `home`. `time_zone_note`, present only while no time zone is set. Nothing says the account is new |
| A new account | `services.account_empty`: no live entry in the five entry tables, no live saved food, no open goal. Today's "Nothing logged yet" card reads it. `whoami` does not |
| A result's shape | Each result's schema forbids a top-level field it does not name. A nested object such as `links` does not |
| The instructions | 3,678 characters, pinned at that number by two tests. They say "Put links last." |
| Text that sends a person to a page | A test allows one: the fix of `not_allowed`. It reads "page" or "form" beside a word such as set, open, go, send or ask |
| `docs/can-and-cant.md` | Served to an assistant as an MCP resource. Not a page |
| Evals | 21 cases. Each run starts a fresh account with a time zone set, so a case with no seed runs on an empty one: 9 of the 21. `reply_has` and `reply_lacks` judge the final reply by phrase. No case's expectation reads a link |
| Undo | One step, once. `restore_entry` has no `allow_repeat` and there is no Redo. Nothing here leans on either |

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **One page at `/hi`.** Link name `hi_there`, template `hi.html`, h1 "Hi there", title "Hi there · Spotter" | His name for it. Short enough to read out and to type |
| 2 | **Open signed in and out.** Routed beside `/` in both route tables, with web sign-in set up or not. Not in `web.PAGES`: no sign-in first, no page limit. Signed in it carries the tab bar with no tab current | The link is opened where the chat is, often a browser that is not signed in. A page to read must not ask for a sign-in |
| 3 | **It shows nothing of the person's.** The same words for everyone. Only one link differs: signed in or out | An open page. It also means the link can be passed on |
| 4 | **What it says:** the sections below, in that order, in those words. Each section is a card with its own h2, as the landing's are | The fallback: the frame, with what is already true |
| 5 | **No count and no list is typed in the template.** Each comes from the code, as the table below names. The say-list moves into two constants that the landing draws from too. The landing's words do not change, and its word-for-word test passes unedited | One place for each fact, so the page cannot fall behind |
| 6 | **What it must not do.** No form, no field, no button and no script in `main`. No step that is done on the page: each "how to" is a thing to say to the assistant, or a link to a page that exists. No function name, no assistant named, no account data, no link-preview tags. Nothing on it speaks of the switch that sets what the assistant may do | His rule: never send a person to a page to type what the assistant or the browser can supply |
| 7 | **The frame is today's.** `base.html`, the classes the stylesheet already has, the `privacy-page` width class, the site footer as on the landing. No change to `style.css`, no picture, no script | The look is being redrawn elsewhere. A page built from today's parts takes the new look when it lands |
| 8 | **The link rides in `whoami`: `links.hi_there`, always.** Its description: "A short read for the person: what to say, what Spotter keeps and how to bring history over from another app. Give it when new_account_note is present, or when the person asks what Spotter can do." | `whoami` is the first call of every conversation. A person who asks for help later still gets the link |
| 9 | **A new account is told to share it: `new_account_note`,** a field of `whoami` present only while the account is empty, by the rule Today's card uses. Its words: "Nothing is logged on this account yet. Give the person links.hi_there once, last in your reply: it is a short read on what to say, what Spotter keeps and how to bring history over from another app. It has nothing to fill in." The field's own description: "Present only while nothing is logged on the account: no entry, no saved food, no open goal. Give the person links.hi_there once, last in your reply." | The assistant does the sharing, as he asked. The pattern is `time_zone_note`'s. `whoami` stays a read: no column, no migration |
| 10 | **When it stops:** at the first live entry, saved food or open goal. Deleted rows and closed goals do not count, so an account emptied again is new again. Spotter does not store whether the link was shared: "once" is the assistant's to keep | One rule for "new", the one Today already uses. A read that writes a mark would not be a read |
| 11 | **`whoami`'s description gains one sentence:** "If it carries new_account_note, do what it says." The instructions do not change: 3,678 characters | Not every client shows a model a result's field descriptions. The description is read up front. The instructions are near their cap |
| 12 | **None of the new text sends a person to a page to do something.** No sentence of it holds "page" or "form". The test on such text reads the new note too, and its one exception stays one | The link is a read. The rule that guards the time zone and the switch stays whole |
| 13 | **`docs/can-and-cant.md` gains a short section, "A new person",** in the words under "The section in can-and-cant" below | An assistant that reads the resource should find the same rule there |
| 14 | **The feedback line is drawn only while the feedback function is offered to everyone,** read from the server's own table of functions at each render: `send_feedback` in `mcp_server.NEEDS`. On the tree this is built on the function is not there, so the line is not drawn. A test makes the table say one or the other and holds both states | The page is right before the feedback work is joined and after it |
| 15 | **`hi_there` is not one of the README's account pages.** The docs test leaves it out as it leaves out `home` and `sign_in` | The README's list is the pages of a person's account. This one is open, like the landing |
| 16 | **Nothing is stored and nothing is logged.** No table, no migration, no cookie. A page load is not in the call log, as today. The trace and the privacy page's words do not change | It is a page of words and one link in a result |
| 17 | **Two eval cases,** which make 23 on this tree. `hi_there_first_log`: an empty account, "I weighed 80.4 kg this morning": one weigh-in logged, and the reply holds "/hi". `hi_there_not_again`: the same sentence with a weigh-in seeded the day before: the weigh-in is logged and the reply does not hold "/hi" | The link must be given when the account is new and must not follow the person round after that |

### The page, section by section

| # | Section | Words | Where each fact comes from |
|---|---|---|---|
| 1 | Page head | h1 "Hi there". Under it, the landing's one line and its second line | `web.LANDING_LINES` |
| 2 | Say it to your assistant | The landing's three sentences, each with what it does. The label "Everyday logging" and its three lines | Two constants in `web.py`, `SAY_FIRST` and `SAY_EVERYDAY`, holding the landing's words as they are. `home.html` draws from the same two |
| 3 | What Spotter keeps | "Lifts. {n} lifts by name, each session with its sets." "Cardio. {n} activities." "Body. Weigh-ins, and {n} measurements: {names}." "Food. What you ate, by meal: {meals}." "Goals. {n} kinds: {kinds}." | `exercises.LIFTS`, `exercises.CARDIO`, `MEASUREMENT_KINDS` with `measurements.kind_word`, `web.MEALS`, `web.GOAL_KINDS`. Each count through `show`, as the landing's are |
| 4 | Bring your history over | "Your assistant can bring your history over from another app." "Give it the app's export file and ask." "It shows a preview first, and the preview writes nothing." "Your assistant is told to write only when you say yes." "It takes weigh-ins, measurements, lift sessions, food and cardio." | Typed. A test fails if `mcp_server.IMPORT_RULE` stops saying that the first call is a preview and that writing waits for a yes, or if the import's kinds (`imports.ORDER`) stop being those five |
| 5 | More | "Not connected yet? The steps are on the home page." with "the home page" a link to `/`. "Each assistant's own steps are in the README's table." with "the README's table" its link. Signed in: "Your pages start at Today." and the link "Open Today". Signed out, with web sign-in set up: "Sign in to see what you logged." and the link "Sign in". Signed out with web sign-in off: neither. While the feedback function is offered: "Something wrong or confusing? Tell your assistant to send feedback. It reaches the person who runs Spotter." | `web.README_TABLE`. The frame's own `signed_in` and `web_ready`. `mcp_server.NEEDS` for the feedback line |
| 6 | Footer | The site footer, as on the landing | `_footer.html` |

- Signed out, the page links to no signed-in page, as the landing does not.
- The page states no function count and no eval count. Those stay on the landing.
- Where one of these words fails a text rule, the builder changes the fewest words and names the change in its report.
- The fourth sentence of "Bring your history over" was first drawn as "It writes only when you say yes." The review asked whether the page can promise that. It cannot: the server writes when the assistant sends the write, and only the assistant's instructions make it wait for a yes. So the page says what the README's own line on importing says: the assistant is told to.

### The section in can-and-cant

It sits between `## What "today" means` and `## What Claude may do`.

```
## A new person

- While nothing is logged, `whoami` carries `new_account_note`: no live entry, no saved food and no open goal.
- Claude then gives the person `links.hi_there` once, last in the reply.
- The link is a short read: what to say, what Spotter keeps and how to bring history over. It has nothing to fill in.
- The note goes after the first entry, saved food or open goal. It comes back if the account is emptied again.
- `links.hi_there` is always there. Give it when the person asks what Spotter can do.
```

## Ruled where the decisions leave the how open

| # | Ruling | Fills |
|---|---|---|
| R1 | **The base.** Built at `d890103`, which holds edit history and not feedback. 21 eval cases become 23. Every count written is the count on this tree | The start |
| R2 | **The view.** One view, `hi_there`, routed as `("/hi", hi_there, ["GET"])` straight after `/` in both route tables. It is never wrapped by the page limit and is in none of `PAGES`, `PLACEHOLDERS` and `CARD_ADDRESSES`. It looks up the signed-in person as the landing does and reads nothing else. It builds no address with `links.page`, so it draws with no site address set. A visitor with no session reads no database. A session that names a person who is gone costs one read and is cleared, as on the landing | 2, 3, 16 |
| R3 | **The frame row.** `"hi.html": (None, "privacy-page", None)`: no page class, the 720 px width, no tab | 2, 7 |
| R4 | **The say-list.** `SAY_FIRST`: three pairs, a sentence and what it does. The first sentence is `LANDING_EXCHANGE["question"]` itself, not a copy. `SAY_EVERYDAY`: the three lines, with a no-break space where the template has one today. The label "Everyday logging" and each page's own h2 stay typed in the two templates | 5 |
| R5 | **A list in a sentence** is drawn "a, b and c", by one helper, with each word lowered where its source gives it a capital. A count goes through `show("count")` | 5, section 3 |
| R6 | **Each fact is tied to what the assistant can do, not only to a constant.** A test holds: the lift and cardio counts equal what `list_exercises` gives on a fresh database. The goal kinds equal `set_goal`'s own (`models.GOAL_KINDS` less `goals.SETTINGS_KINDS`). The meals equal `models.MEALS`. The measurements are every one of `MEASUREMENT_KINDS` | 5 |
| R7 | **The two links of "More"** that differ by state are `a.plain-link`, never a `button` element | 6 |
| R8 | **The feedback line** reads `"send_feedback" in mcp_server.NEEDS` at each render, never once at start | 14 |
| R9 | **`whoami`.** One private helper in `services/account.py` answers "is this account empty" inside a session. `account_empty` and `whoami` both call it. `whoami` stays a read: no lock, no write limit, no write. The note's words are one constant, `NEW_ACCOUNT_NOTE`, beside `TIME_ZONE_NOTE`. `new_account_note` comes after `time_zone_note` in the result | 9, 10 |
| R10 | **The old schema.** A test holds the known limit as stated: a result for an account that is not empty passes `whoami`'s schema as it was published before this work, and an empty account's fails it on `new_account_note` alone | Known limits |
| R11 | **Five new texts an assistant reads:** the note, the link's description, the sentence in `whoami`'s description, the description of `new_account_note`, and the section in can-and-cant. A test reads all five: none holds the word "page" or "form" | 12 |
| R12 | **The tests.** `tests/test_hi_there.py` holds every line of the exit check. The page is read by the rule functions of `tests/test_page_rules.py` and `tests/test_pages_text.py`, which gain three table rows: `FRAME["/hi"]`, `"/hi"` in `BUILT`, `NAMED["/hi"]`. The two page sweeps read `/hi` signed out and signed in on each of their accounts | Exit check |
| R13 | **The same page for everyone, proved.** `main` of `/hi` is byte for byte the same for two signed-in people, one with nothing logged and one with four weeks of entries. Signed out it differs from that by the one line and link of "More" alone | 3 |
| R14 | **The page limit.** More loads of `/hi` than the page limit allows, by one signed-in person, all answer 200, and they use up none of that person's page loads. The route tables differ from before by `/hi` alone: a test holds both tables row by row, each path with its methods and whether the page limit wraps it | 2 |
| R15 | **The eval cases in full.** `hi_there_first_log`: no seed, expects `log_bodyweight` with `kg` 80.4 and `reply_has` "/hi", and one live weigh-in of 80.4 kg today. `hi_there_not_again`: a seeded weigh-in of 80.9 kg on `{{yesterday}}`, expects `log_bodyweight` with `kg` 80.4 and `reply_lacks` "/hi", and two live weigh-ins. A test judges each case by hand both ways: with the link in the reply and without | 17 |
| R16 | **Notes kept true.** The module notes of `web.py`, `links.py` and `services/account.py`, and the comment in `_footer.html`, say what is now so. Nothing `_footer.html` draws changes | 7 |

## Errors added

None.

## Known limits

- **A client that holds an older function list.** A conversation begun before the deploy holds `whoami`'s old schema, which forbids a field it does not name. If it calls `whoami` on an empty account after the deploy, its client may refuse the result. Only an empty account gets the new field, and the link inside `links` is safe: R10's test holds both. Merged while the owner's is the only account in use, nobody meets it. No real client was tried.
- **Other clients.** The evals run through Claude Code. Whether another client shows a result's note to its model the same way is not proved by them.
- **"Once" is not enforced.** A new person who logs nothing gets the link again in each new conversation, until the first entry.
- **The frame's time-zone form.** A signed-in person with no time zone set gets the frame's hidden form and its script after `main`, as on every page they open. It saves the browser's zone once and comes back to `/hi`. That is the frame's, not the page's.
- **`whoami` reads more.** It now asks up to seven small questions of the database to tell whether the account is empty, on every call. If one of them fails, the call answers that the database is unavailable, as any failed read does: it does not answer without the note.
- **The counts are the code's.** The page counts the lifts and the cardio activities in the code's list, and reads no database to do it. The live exercise table only gains rows. No lift has been renamed or removed in the code so far. If one ever is, `list_exercises` gives more than the page says until the table is cleaned.
- **An assistant that may only read.** An empty account whose switch is on read still gets the note, and the page's sentences are ones that assistant cannot log. The page says nothing about the switch.
- **The preview is the assistant's to show.** The server writes nothing in a preview, but it cannot make an assistant ask for one first. The page says what the assistant is told.

## Work package: one builder

| Package | Files | Exit check |
|---|---|---|
| HT1 | `app/templates/hi.html` (new), `app/templates/home.html` (the say-list from the two constants), `app/templates/_footer.html` (its comment alone), `app/web.py` (the constants, the view, the `FRAMES` row, the route in both tables, the module notes), `app/links.py`, `app/services/account.py`, `app/mcp_schemas.py`, `app/mcp_server.py` (the one sentence), `docs/can-and-cant.md`, `evals/cases.json`, `docs/evals.md` (the two rows of its case table), `README.md` (the case count alone), `tests/test_hi_there.py` (new). Existing test files only where an assertion now fails because of a decision, or for a table row R12 names, each change named in the report. Expected: `test_links.py`, `test_docs.py`, `test_web.py`, `test_pages_text.py`, `test_page_rules.py`, `test_progress.py`, `test_mcp.py`, `test_result_text.py`, `test_evals.py` | Every decision and every ruling has a test. `/hi` answers 200 signed out, signed in, with web sign-in off and with no server address, and its words are the table's in each state. Signed out it links to no signed-in page, and a visitor with no session reads no database. One h1. No form, field, button or script in `main`. Its head is the plain head: no description, no link-preview tags. Each count and list equals its source, and no digit is typed in the template. The say-list equals the landing's item for item, and the landing's own tests pass unedited. The import sentences' test and the feedback line's two states. The page sweeps reach `/hi` signed in and out: the frame rules, the page rules, the text rules, the title. R13 and R14. `whoami` on an empty account carries the note and the link. After one entry, one saved food or one open goal the note is gone and the link stays. After that entry is deleted the note is back, and a closed goal does not count. `whoami` and Today agree on "new" in each of those states. Both results pass `whoami`'s schema, and every new field is described. R10. The note passes the message rules, and the page-sending test reads it and keeps its one exception. R11. The instructions are still 3,678 characters. No function but `whoami` changed in the published list. The link name builds and has a page, signed in and out. The eval cases load, and R15's test. The tests for the files touched pass, plain and with `-W error` |

Not in this work: the catalogue's content (goal shapes, each input with its unit and range, import steps app by app); a help function; a list of functions on the page; a door to `/hi` from the landing, from Today's new-account card or from the footer; link-preview tags; a route for the privacy page, or a link to it; any look, stylesheet rule or picture; a stored mark that the link was shared; setup prompts offered by the server; a word on the switch that sets what the assistant may do; the docs (`README.md`'s line on the page, `BUILD_LOG.md`), done after the review.

**An eval run follows the build:** `whoami`'s description, two result fields, the note and the resource are text a model reads, and `whoami` opens every conversation. Every case with no seed now starts with the note in hand, so the whole set runs: 23 cases on this tree, 25 once the feedback work's two are joined, 3 runs each, on `claude-opus-5-5`. The cost is told to the owner before it runs. The last whole run on record, 20 cases, was $8.6592 at list price (`BUILD_LOG.md`, 2026-11-14).

## Where it meets the feedback work

The feedback work is built on a tree of its own from the same edit-history code. Neither tree holds the other. The join is made after both are reviewed.

| Point | The feedback work | This work | At the join |
|---|---|---|---|
| The function count | It adds a function for everyone: 19 becomes 20 wherever it is stated | The page states no function count | Nothing of this work changes |
| The feedback line on the page | It adds `send_feedback` | One line of "More", drawn only while `send_feedback` is in `mcp_server.NEEDS`. It promises only this: the person asks, the assistant sends, the person who runs Spotter reads. It states no cap and no length | The line appears by itself. If the function's name differs as built, the name in `web.py` follows it. The test of the two states stays as it is |
| `app/web.py` | The count (`FUNCTION_COUNT`) | New constants, one view, one `FRAMES` row, one route in each table, the module notes | Different lines of one file |
| `app/mcp_server.py`, `app/mcp_schemas.py` | Two new functions and their results | `whoami` only: one sentence, two result fields | Different functions |
| `docs/can-and-cant.md` | A section and a function row | A new section, "A new person" | Each adds its own lines |
| Eval cases and every stated count | Two cases: 21 becomes 23 | Two cases: 21 becomes 23 | 25: `evals/cases.json` holds all four, and `tests/test_evals.py`, the README's count and `docs/evals.md` follow |
| Cases that start on an empty account | Not checked: its two cases were not read here | Each such case's first `whoami` carries the note, and its reply may gain the link. No case's expectation reads a link on this tree | The whole-set run measures it |
| The eval run | A run follows its build | A run follows this one | One run of 25 covers both |
| The privacy page | A card | Untouched: nothing new is stored | Nothing of this work changes |
| Shared test files | `test_mcp.py`, `test_web.py`, `test_docs.py`, `test_evals.py` and others | The same files, other tests | Read each file both changed |
| Migrations | `0008` | None | Nothing of this work changes |
